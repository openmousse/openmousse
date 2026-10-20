"""第 7 批：mousse security audit（server/ops/audit.py）。每一条至少一个例子；--fix 只做安全的；tailscale 是假的。"""
from __future__ import annotations

import json
import os
import stat
from datetime import datetime, timedelta, timezone

import pytest
from ops_helpers import make_home, ns

from ops import audit as A

KEY = "sk-test-0123456789abcdWXYZ"


@pytest.fixture
def h(tmp_path, monkeypatch):
    return make_home(tmp_path, monkeypatch)


def clean(h, **extra) -> dict:
    cfg = {"claw": {"kind": "builtin"}, "language": "en", "bind": {"host": "127.0.0.1", "port": 8080}, "data_dir": str(h.mousse / "data"),
           "auth": {"tokens": {"phone": "tok-phone-0123456789abcdefgh", "mcp": "tok-mcp-0123456789abcdefghij"}}, **extra}
    h.write(cfg)
    (h.mousse / "data").mkdir(mode=0o700, exist_ok=True)
    return cfg


def ids(found) -> dict[str, list]:
    out: dict[str, list] = {}
    for f in found:
        out.setdefault(f.id, []).append(f)
    return out


def audit(capsys, **kw) -> tuple[int, dict]:
    code = A.main(ns(json=True, fix=kw.get("fix", False)))
    return code, json.loads(capsys.readouterr().out)


def test_a_clean_install_has_nothing_critical(h, capsys):
    clean(h)
    code, rep = audit(capsys)
    assert code == 0 and rep["ok"] and rep["summary"]["critical"] == 0 and rep["summary"]["warn"] == 0


def test_permissions_and_fix(h, capsys):
    clean(h)
    os.chmod(h.config, 0o644)
    (h.mousse / "secrets.env").write_text(f"OPENAI_API_KEY={KEY}\n", encoding="utf8")
    os.chmod(h.mousse / "secrets.env", 0o666)  # 别人写得了 = ✗
    os.chmod(h.mousse / "data", 0o755)
    (h.mousse / "data" / "chatgpt").mkdir(mode=0o755)
    (h.mousse / "backups").mkdir()
    (h.mousse / "backups" / "openmousse-backup-20261019-000000.tar.gz").write_bytes(b"x")
    os.chmod(h.mousse / "backups" / "openmousse-backup-20261019-000000.tar.gz", 0o644)
    found = ids(A.run_audit()[0])
    assert found["fs.secrets"][0].severity == "critical" and found["fs.config"][0].severity == "critical"  # 谁都读得到的令牌
    assert found["fs.data_dir"][0].severity == "warn" and found["fs.chatgpt"][0].severity == "critical" and "fs.backups" in found
    code, rep = audit(capsys, fix=True)
    assert code == 0 and all(f["fixed"] for f in rep["findings"] if f["id"].startswith("fs."))
    assert stat.S_IMODE((h.mousse / "secrets.env").stat().st_mode) == 0o600 and stat.S_IMODE(h.config.stat().st_mode) == 0o600
    assert stat.S_IMODE((h.mousse / "data").stat().st_mode) == 0o700
    assert stat.S_IMODE((h.mousse / "backups" / "openmousse-backup-20261019-000000.tar.gz").stat().st_mode) == 0o600
    assert (h.mousse / "secrets.env").read_text() == f"OPENAI_API_KEY={KEY}\n"


def test_inline_secrets_and_tokens(h, capsys):
    clean(h, claw={"kind": "openai", "url": "http://127.0.0.1:9/v1", "token": KEY}, auth={"tokens": {"phone": "short", "api": "same-0123456789abcdef",
                                                                                                  "laptop": "same-0123456789abcdef"}, "trust_loopback": True},
          engine={"providers": {"x": {"api": "openai", "url": "http://127.0.0.1:9/v1", "key": KEY, "key_env": "X_API_KEY"}}},
          mcp_servers={"gh": {"command": "gh-mcp", "env": {"GITHUB_TOKEN": "ghp_fake0123456789abcdef", "OTHER_TOKEN": "${OTHER_TOKEN}"}}})
    found = ids(A.run_audit()[0])
    hits = found["config.inline_secrets"][0]
    assert "claw.token" in hits.why and "engine.providers.x.key" in hits.why and "mcp_servers.gh.env.GITHUB_TOKEN" in hits.why
    assert "OTHER_TOKEN" not in hits.why and "key_env" not in hits.why and KEY not in hits.why
    assert found["auth.short_tokens"][0].severity == "warn" and "phone" in found["auth.short_tokens"][0].title
    assert found["auth.reused_tokens"][0].severity == "critical"  # api（只给 /v1）和 laptop（人用的）一把：api 的持有人也能走 /api
    assert found["auth.trust_loopback"][0].severity == "warn"
    code, rep = audit(capsys)
    assert code == 1 and KEY not in json.dumps(rep) and "same-0123456789abcdef" not in json.dumps(rep)
    clean(h, auth={})
    assert "auth.none" in ids(A.run_audit()[0])


def test_bind_and_funnel(h):
    clean(h, bind={"host": "0.0.0.0", "port": 8080})
    assert ids(A.run_audit()[0])["net.bind_all"][0].severity == "critical"
    clean(h, bind={"host": "192.168.1.20", "port": 8080})
    assert ids(A.run_audit()[0])["net.bind_other"][0].severity == "warn"
    clean(h, bind={"host": "100.101.102.103", "port": 8080})
    assert "net.bind_other" not in ids(A.run_audit()[0])  # Tailscale 地址：照安装器
    status = {"Web": {"box.tail1234.ts.net:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8080"}, "/t": {"Proxy": "http://127.0.0.1:8787/t"},
                                                               "/grafana": {"Proxy": "http://127.0.0.1:3000"}}}},
              "AllowFunnel": {"box.tail1234.ts.net:443": True}}
    h.fake("tailscale", f"cat <<'EOF'\n{json.dumps(status)}\nEOF\n")
    found = ids(A.run_audit()[0])
    assert found["net.funnel_api"][0].severity == "critical" and "--set-path=/ off" in found["net.funnel_api"][0].fix
    assert [f.title for f in found["net.funnel_other"]] == ["Funnel exposes box.tail1234.ts.net:443/grafana"]  # /t 是安装器开的，不提


def test_engine_api_hands_plan_and_sandbox(h, monkeypatch):
    clean(h, auth={"tokens": {"phone": "tok-phone-0123456789abcdefgh"}},
          engine={"api": {"agents": ["main"], "responses": True},
                  "host_exec": {"agents": {"main": {"allow": ["git status"]}, "fitness": {"security": "full"}}},
                  "tools": {"levels": {"journal": "auto", "board show": "auto", "inbox add": "ask"}},
                  "sandbox": {"network": "host"}},
          mcp_servers={"fs": {"command": "x", "tools": {"write_file": "auto", "read_file": "auto", "list_dir": "ask"}}, "all": {"command": "y", "tools": {"*": "auto"}}})
    found = ids(A.run_audit()[0])
    assert found["api.no_tokens"][0].severity == "info" and found["api.approve"][0].severity == "warn" and "/v1/responses" in found["api.approve"][0].why
    assert [f.title for f in found["exec.security_full"]] == ["fitness runs server commands without an allowlist (security: full)"]
    loose = found["tools.auto"][0].title
    assert "journal" in loose and "board show" not in loose  # board show 本来就是只读的：auto 不算放松
    mcp = {f.title for f in found["mcp.write_auto"]}
    assert any("write_file" in t and "read_file" not in t for t in mcp) and any("*" in t for t in mcp)
    assert found["sandbox.network"][0].severity == "critical"
    from engine import chatgpt_plan
    monkeypatch.setattr(chatgpt_plan, "background_allowed", lambda: True)
    assert "plan.chatgpt_background" in ids(A.run_audit()[0])
    clean(h, engine={"api": {"approve": False}, "sandbox": {"network": "none"}})
    found = ids(A.run_audit()[0])
    assert "api.approve" not in found and "sandbox.network" not in found


def test_expired_pairing_codes_are_cleared_by_fix(h, capsys):
    now = datetime.now(timezone.utc)
    clean(h, auth={"tokens": {"phone": "tok-phone-0123456789abcdefgh"},
                   "pairing": [{"hash": "a", "expires": (now - timedelta(hours=1)).isoformat(timespec="seconds")},
                               {"hash": "b", "expires": (now + timedelta(minutes=5)).isoformat(timespec="seconds")}]})
    assert "auth.expired_pairing" in ids(A.run_audit()[0])
    code, rep = audit(capsys, fix=True)
    assert code == 0 and [f for f in rep["findings"] if f["id"] == "auth.expired_pairing"][0]["fixed"]
    assert [p["hash"] for p in h.read()["auth"]["pairing"]] == ["b"] and list(h.mousse.glob("server.json.bak-*"))


def test_broken_config_still_checks_permissions(h, capsys):
    h.write("{oops", mode=0o644)
    code, rep = audit(capsys)
    assert rep["config"] == "broken" and any(f["id"] == "fs.config" for f in rep["findings"])
