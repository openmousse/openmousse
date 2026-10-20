"""第 7 批：mousse config validate / get / set / unset（server/ops/confcheck.py），doctor 的 config.valid，setup_ctl.py 写完以后的提醒。"""
from __future__ import annotations

import json

import pytest
from ops_helpers import SERVER, make_home, ns, run_mousse

from ops import confcheck as CC
from ops import doctor

KEY = "sk-test-0123456789abcdWXYZ"


@pytest.fixture
def h(tmp_path, monkeypatch):
    return make_home(tmp_path, monkeypatch)


def base(**extra) -> dict:
    return {"claw": {"kind": "builtin"}, "language": "en", "timezone": "UTC", "bind": {"host": "127.0.0.1", "port": 8080},
            "auth": {"tokens": {"phone": "tok-phone-0123456789", "local": "tok-local-9876543210"}},
            "engine": {"model": "deepseek/deepseek-flash", "fallbacks": ["moonshot/kimi-k3"], "use": {"card": True, "background": "deepseek/deepseek-flash"},
                       "providers": {"box": {"api": "openai", "url": "http://127.0.0.1:11434/v1", "key_env": "", "headers": {"X": "y"}}},
                       "api": {"agents": ["main"], "approve": True}, "tools": {"levels": {"journal": "off", "schedule delete": "ask"}},
                       "host_exec": {"agents": {"main": {"allow": ["git status"], "ask": "on-miss", "security": "allowlist", "timeout": 120}}}},
            **extra}


def run(capsys, **kw) -> tuple[int, str, str]:
    code = CC.main(ns(**kw))
    got = capsys.readouterr()
    return code, got.out, got.err


def test_validate_accepts_real_configs_and_only_warns_on_unknown_keys(h, capsys):
    for cfg in (json.loads((SERVER / "server.example.json").read_text(encoding="utf8")), base()):
        assert CC.errors(CC.validate(cfg)) == [], CC.validate(cfg)
    h.write(base(future_feature={"on": True}, engine={**base()["engine"], "brand_new": 1}))
    code, out, _ = run(capsys, action="validate", json=True, file=None)
    rep = json.loads(out)
    assert code == 0 and rep["ok"] and rep["errors"] == 0 and rep["warnings"] == 2  # 新版本、别的批加的键：只警告
    assert {f["path"] for f in rep["findings"]} == {"future_feature", "engine.brand_new"} and {f["level"] for f in rep["findings"]} == {"warn"}


def test_validate_flags_wrong_types_and_ranges(h, capsys, tmp_path):
    bad = base(bind={"host": "127.0.0.1", "port": 99999}, timezone="Mars/Base", language="fr", update={"channel": "beta"})
    bad["engine"].update(model="not-a-ref", effort="ultra", jobs={"max_parallel": 0}, api={"agents": "main"}, tools={"levels": {"journal": "maybe"}},
                         fallbacks="moonshot/kimi-k3")
    bad["engine"]["host_exec"]["agents"]["main"]["security"] = "yolo"
    f = tmp_path / "other.json"
    f.write_text(json.dumps(bad), encoding="utf8")
    code, out, _ = run(capsys, action="validate", json=False, file=str(f))
    assert code == 1
    for path in ("bind.port", "timezone", "language", "update.channel", "engine.model", "engine.effort", "engine.jobs.max_parallel",
                 "engine.api.agents", "engine.tools.levels.journal", "engine.fallbacks", "engine.host_exec.agents.main.security"):
        assert f"✗ {path}:" in out, path
    assert "99999 won't do: it must be 1–65535" in out
    f.write_text("{oops", encoding="utf8")
    assert run(capsys, action="validate", json=False, file=str(f))[0] == 1
    assert run(capsys, action="validate", json=False, file=str(tmp_path / "missing.json"))[0] == 1


def test_get_masks_secrets(h, capsys):
    cfg = base(claw={"kind": "openai", "url": "http://127.0.0.1:9/v1", "token": KEY, "token_env": "CLAW_TOKEN"})
    cfg["engine"]["providers"]["box"]["key"] = KEY
    h.write(cfg)
    code, out, _ = run(capsys, action="get", path="engine.model")
    assert code == 0 and json.loads(out) == "deepseek/deepseek-flash"
    code, out, _ = run(capsys, action="get", path="auth")
    assert code == 0 and json.loads(out)["tokens"] == {"phone": "***", "local": "***"} and "tok-phone" not in out
    code, out, _ = run(capsys, action="get", path="claw")
    assert KEY not in out and json.loads(out)["token"] == "***" and json.loads(out)["token_env"] == "CLAW_TOKEN"  # 变量名照给
    code, out, _ = run(capsys, action="get", path="engine.providers")
    assert KEY not in out and json.loads(out)["box"]["key"] == "***"
    code, out, err = run(capsys, action="get", path="engine.nothing")
    assert code == 1 and "isn't set" in err
    assert run(capsys, action="get", path="engine..model")[0] == 2


def test_set_validates_backs_up_and_writes_atomically(h, capsys):
    h.write(base())
    code, out, _ = run(capsys, action="set", path="engine.fallbacks", value='["moonshot/kimi-k3", "deepseek/deepseek-v4-pro"]')
    assert code == 0 and h.read()["engine"]["fallbacks"] == ["moonshot/kimi-k3", "deepseek/deepseek-v4-pro"] and "backed up" in out
    baks = list(h.mousse.glob("server.json.bak-*"))
    assert len(baks) == 1 and json.loads(baks[0].read_text())["engine"]["fallbacks"] == ["moonshot/kimi-k3"]
    assert (h.config.stat().st_mode & 0o777) == 0o600
    assert run(capsys, action="set", path="engine.model", value="moonshot/kimi-k3")[0] == 0  # 不是 JSON：当字符串
    assert h.read()["engine"]["model"] == "moonshot/kimi-k3"
    assert run(capsys, action="set", path="update.channel", value="main")[0] == 0 and h.read()["update"] == {"channel": "main"}  # 没有的段建上
    before = h.config.read_bytes()
    n = len(list(h.mousse.glob("server.json.bak-*")))
    code, out, _ = run(capsys, action="set", path="bind.port", value='"eighty"')
    assert code == 1 and "nothing was written" in out and h.config.read_bytes() == before  # 整份校验不过：不落盘、不备份
    assert len(list(h.mousse.glob("server.json.bak-*"))) == n
    code, _, err = run(capsys, action="set", path="engine.providers.box.key", value=KEY)
    assert code == 2 and "secrets.env" in err and KEY not in h.config.read_text()  # 密钥不往 server.json 里写
    assert run(capsys, action="set", path="auth.tokens.new", value="abc")[0] == 2
    assert run(capsys, action="unset", path="update.channel")[0] == 0 and "channel" not in h.read()["update"]
    assert run(capsys, action="unset", path="no.such.thing")[1].strip() == "No change"
    h.write('{"broken": ')
    code, _, err = run(capsys, action="set", path="engine.model", value="moonshot/kimi-k3")
    assert code == 1 and "can't be read" in err and h.config.read_text() == '{"broken": '  # 读不了的不拿空的写回去


def test_secrets_never_get_in_through_a_parent_or_a_header(h, capsys):
    """守门不只看最后一段：写进去的整个对象里带密钥、header 里的 Authorization / *-Key、auth 整段、auth.tokens / pairing 都不给改；
    get 把 header 里的也打码。"""
    cfg = base(claw={"kind": "openai", "url": "http://127.0.0.1:9/v1", "headers": {"Authorization": f"Bearer {KEY}"}})
    cfg["engine"]["providers"]["box"]["headers"] = {"Authorization": f"Bearer {KEY}", "X": "y"}
    h.write(cfg)
    for path in ("claw", "engine.providers", "engine.providers.box.headers", "engine.providers.box.headers.Authorization"):
        code, out, _ = run(capsys, action="get", path=path)
        assert code == 0 and KEY not in out, path
    assert json.loads(run(capsys, action="get", path="engine.providers.box.headers")[1])["X"] == "y"
    before = h.config.read_bytes()
    for action, path, value in (("set", "engine.providers.q", json.dumps({"api": "openai", "url": "http://127.0.0.1:9/v1", "key": KEY})),
                                ("set", "engine.providers.box.headers", json.dumps({"Authorization": f"Bearer {KEY}"})),
                                ("set", "engine.providers.box.headers.X-Api-Key", KEY),
                                ("set", "engine.providers", json.dumps({"q": {"api": "openai", "url": "http://127.0.0.1:9/v1", "keys": [KEY]}})),
                                ("set", "auth", json.dumps({"tokens": {"phone": "weak"}})), ("set", "auth.pairing", "[]"),
                                ("unset", "auth", None), ("unset", "auth.tokens", None), ("unset", "auth.tokens.phone", None)):
        code, _, err = run(capsys, action=action, path=path, value=value)
        assert code == 2 and "secrets.env" in err, (action, path)
    assert h.config.read_bytes() == before
    ok = {"api": "openai", "url": "http://127.0.0.1:9/v1", "key_env": "Q_API_KEY", "max_tokens": 4096, "headers": {"X": "z", "Authorization": "${BOX_AUTH}"}}
    assert run(capsys, action="set", path="engine.providers.q", value=json.dumps(ok))[0] == 0  # 变量名、${变量}、max_tokens 照写
    assert h.read()["engine"]["providers"]["q"] == ok
    assert run(capsys, action="set", path="auth.trust_loopback", value="true")[0] == 0 and h.read()["auth"]["trust_loopback"] is True


def test_model_aliases_validate(h, capsys):
    """engine.aliases 里起的别名（engine/models.py）能写在要模型的地方；没起过的、别名指别名算错。"""
    cfg = base()
    cfg["engine"].update(aliases={"smart": "moonshot/kimi-k3", "fast": "deepseek/deepseek-flash"}, model="smart", fallbacks=["fast"])
    cfg["engine"]["use"]["json"] = "fast"
    h.write(cfg)
    found = CC.validate(cfg)
    assert CC.errors(found) == [] and not [f for f in found if "alias" in f.message], found
    assert run(capsys, action="set", path="engine.effort", value="high")[0] == 0  # 别的项照样改得了
    undefined = CC.validate({**cfg, "engine": {**cfg["engine"], "model": "nosuch"}})
    assert [f.path for f in CC.errors(undefined)] == ["engine.model"] and "engine.aliases" in CC.errors(undefined)[0].message
    bad = CC.validate({**cfg, "engine": {**cfg["engine"], "aliases": {"a": "smart"}}})
    assert [f for f in CC.errors(bad) if f.path == "engine.aliases.a"]
    assert CC._ALIASES.get() == frozenset()  # noqa: SLF001 — 查完就清掉，不留给下一次


def test_doctor_and_setup_ctl_use_the_validator(h, capsys):
    cfg = base()
    cfg["engine"]["effort"] = "ultra"
    h.write(cfg)
    c = {x.id: x for x in doctor.check_config_valid(doctor.ctx())}["config.valid"]
    assert c.status == "error" and "engine.effort" in c.detail and c.fix == "mousse config validate"
    cfg["engine"]["effort"] = "low"
    cfg["shiny"] = 1
    h.write(cfg)
    c = {x.id: x for x in doctor.check_config_valid(doctor.ctx())}["config.valid"]
    assert c.status == "info" and "shiny" in c.detail  # 不认识的键不算毛病
    import setup_ctl
    cfg["engine"]["effort"] = "ultra"
    h.write(cfg)
    assert setup_ctl.main(["use", "writing", "off"]) == 0  # 照旧写成；写完提醒一句整份里不对的地方
    out = capsys.readouterr().out
    assert "engine.effort" in out and "mousse config validate" in out


def test_agents_cant_run_the_commands_that_change_things(h, monkeypatch):
    """Agent 自己跑的命令里（MOUSSE_AGENT）：会改东西的运维命令不跑（退出码 2），只查的照跑。"""
    from ops import cli
    h.write(base())
    before = h.config.read_bytes()
    r = run_mousse(h, "config", "set", "engine.effort", "high", MOUSSE_AGENT="main")
    assert r.returncode == 2 and "MOUSSE_AGENT" in r.stderr and h.config.read_bytes() == before
    r = run_mousse(h, "config", "get", "engine.model", MOUSSE_AGENT="main")
    assert r.returncode == 0 and json.loads(r.stdout) == "deepseek/deepseek-flash"
    refused = (("update", "main", ns(check=False, plan=False)), ("backup", "main_restore", ns(dry_run=False)), ("uninstall", "main", ns(dry_run=False)),
               ("sessions", "main", ns(action="cleanup", dry_run=False)), ("confcheck", "main", ns(action="unset")), ("doctor", "main", ns(fix=True)),
               ("audit", "main", ns(action="audit", fix=True)))
    allowed = (("update", "main", ns(check=True, plan=False)), ("update", "main", ns(check=False, plan=True)), ("uninstall", "main", ns(dry_run=True)),
               ("backup", "main_restore", ns(dry_run=True)), ("backup", "main_backup", ns(action="create")), ("confcheck", "main", ns(action="get")),
               ("doctor", "main", ns(fix=False)), ("status", "main", ns()), ("diagnostics", "main", ns()))
    assert all(cli.writes(*x) for x in refused) and not any(cli.writes(*x) for x in allowed)
    monkeypatch.setenv("MOUSSE_AGENT", "main")
    assert cli._run("confcheck")(ns(action="unset", path="engine.effort")) == 2 and h.config.read_bytes() == before  # noqa: SLF001
    monkeypatch.delenv("MOUSSE_AGENT")
    assert cli._run("confcheck")(ns(action="set", path="engine.effort", value="high")) == 0  # noqa: SLF001 — 自己在终端里：照跑


def test_config_from_the_command_line(h):
    h.write(base())
    r = run_mousse(h, "config", "validate")
    assert r.returncode == 0 and "0 errors" in r.stdout
    r = run_mousse(h, "config", "set", "engine.effort", "medium")
    assert r.returncode == 0 and json.loads(h.config.read_text())["engine"]["effort"] == "medium"
    r = run_mousse(h, "config", "get", "auth.tokens")
    assert r.returncode == 0 and "tok-phone" not in r.stdout
