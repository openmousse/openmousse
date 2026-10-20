"""Docker（第 9 批 15.2，2026-10-19）：docker/entrypoint.py 第一次生成 server.json（自带内核、令牌随机、数据都在卷里），再起来一个字不动；
Dockerfile、compose.yaml、.dockerignore 守着几条规矩（不用 root、MOUSSE_INSTALL=docker、端口只绑宿主 127.0.0.1……）。不起容器、不连网。"""
from __future__ import annotations

import importlib.util
import json
import stat
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def ep():
    spec = importlib.util.spec_from_file_location("mousse_docker_entrypoint", REPO / "docker" / "entrypoint.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def env_for(tmp_path: Path, **extra) -> dict:
    return {"MOUSSE_DATA": str(tmp_path / "data"), "MOUSSE_SERVER_CONFIG": str(tmp_path / "data" / "server.json"),
            "MOUSSE_TREE_HOME": str(tmp_path / "data" / "tree"), **extra}


def test_first_start_writes_a_builtin_server_json_inside_the_volume(ep, tmp_path):
    env = env_for(tmp_path, MOUSSE_LANG="zh-CN", MOUSSE_TZ="Asia/Shanghai", MOUSSE_NAME="Pebble")
    fresh, cfg = ep.first_run(env, tmp_path / "data")
    assert fresh
    path = tmp_path / "data" / "server.json"
    assert json.loads(path.read_text()) == cfg and stat.S_IMODE(path.stat().st_mode) == 0o600
    assert cfg["claw"] == {"kind": "builtin"} and cfg["bind"] == {"host": "0.0.0.0", "port": 8080}
    assert (cfg["app_name"], cfg["language"], cfg["timezone"]) == ("Pebble", "zh", "Asia/Shanghai")
    data = str(tmp_path / "data")
    for key in ("data_dir", "workspace", "profile", "openclaw_home"):
        assert cfg[key].startswith(data + "/"), key                 # 会变的东西都在卷里
    toks = cfg["auth"]["tokens"]
    assert set(toks) == {"phone", "local", "mcp"} and len(set(toks.values())) == 3 and all(len(t) >= 30 for t in toks.values())
    assert cfg["auth"]["trust_loopback"] is False
    assert (tmp_path / "data" / "workspace" / "USER.md").read_text().startswith("# USER.md")
    for d in ("data", "workspace", "tree", "home"):
        assert (tmp_path / "data" / d).is_dir()
    other = ep.first_config(env_for(tmp_path / "x"), tmp_path / "x")
    assert other["auth"]["tokens"]["phone"] != toks["phone"]          # 每台随机


def test_a_second_start_changes_nothing(ep, tmp_path):
    env = env_for(tmp_path, MOUSSE_NAME="Pebble")
    ep.first_run(env, tmp_path / "data")
    path = tmp_path / "data" / "server.json"
    cfg = json.loads(path.read_text())
    cfg["engine"] = {"model": "deepseek/deepseek-flash"}             # 用的时候改过
    path.write_text(json.dumps(cfg))
    before = path.read_bytes()
    fresh, again = ep.first_run(env_for(tmp_path, MOUSSE_NAME="Other", MOUSSE_LANG="en"), tmp_path / "data")
    assert not fresh and again["app_name"] == "Pebble" and path.read_bytes() == before


def test_defaults_and_a_wrong_timezone(ep, tmp_path):
    cfg = ep.first_config({"MOUSSE_TZ": "Mars/Olympus_Mons"}, tmp_path)
    assert (cfg["app_name"], cfg["language"], cfg["timezone"]) == ("Mousse", "en", "UTC")
    assert ep.first_config({"LANG": "zh_CN.UTF-8", "TZ": "Europe/Lisbon"}, tmp_path)["language"] == "zh"
    assert ep.first_config({"TZ": "Europe/Lisbon"}, tmp_path)["timezone"] == "Europe/Lisbon"


def test_pairing_without_a_public_address_says_how(ep, tmp_path):
    _fresh, cfg = ep.first_run(env_for(tmp_path), tmp_path / "data")
    text = ep.pair(cfg, env_for(tmp_path))
    assert "MOUSSE_PUBLIC_URL" in text and "tokens.py pair" in text and "tailscale serve" in text


def test_pairing_with_a_public_address_makes_a_one_time_code(ep, tmp_path):
    env = env_for(tmp_path, MOUSSE_PUBLIC_URL="https://box.example-tailnet.ts.net/")
    _fresh, cfg = ep.first_run(env, tmp_path / "data")
    text = ep.pair(cfg, env)
    assert "openmousse://pair?s=https%3A%2F%2Fbox.example-tailnet.ts.net&c=" in text
    saved = json.loads((tmp_path / "data" / "server.json").read_text())
    assert len(saved["auth"]["pairing"]) == 1 and "hash" in saved["auth"]["pairing"][0]   # 只存哈希


def test_health_uses_a_token_that_may_call_the_api(ep, tmp_path, monkeypatch):
    env = env_for(tmp_path)
    _fresh, cfg = ep.first_run(env, tmp_path / "data")
    seen = {}

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"ok": true}'

    def fake(req, timeout):
        seen["url"], seen["auth"] = req.full_url, req.get_header("Authorization")
        return Resp()
    monkeypatch.setattr(ep.urllib.request, "urlopen", fake)
    assert ep.health(env) == 0
    assert seen == {"url": "http://127.0.0.1:8080/api/health", "auth": f"Bearer {cfg['auth']['tokens']['phone']}"}
    assert ep.health({**env, "MOUSSE_SERVER_CONFIG": str(tmp_path / "missing.json")}) == 1


def test_dockerfile_rules():
    text = (REPO / "docker" / "Dockerfile").read_text()
    lines = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    assert lines[0] == "FROM python:3.12-slim"
    assert "MOUSSE_INSTALL=docker" in text and "MOUSSE_SERVER_CONFIG=/data/server.json" in text and "MOUSSE_TREE_HOME=/data/tree" in text
    assert "server/requirements.txt" in text and "/opt/openmousse/tree" in text
    users = [ln for ln in lines if ln.startswith("USER ")]
    assert users == ["USER mousse"] and lines.index("USER mousse") > max(i for i, ln in enumerate(lines) if ln.startswith(("RUN", "COPY")))
    assert any(ln.startswith("HEALTHCHECK") and "entrypoint.py" in ln and "health" in ln for ln in lines)
    assert any(ln.startswith("VOLUME /data") for ln in lines)


def test_compose_binds_only_the_hosts_loopback_and_keeps_data_in_a_volume():
    c = yaml.safe_load((REPO / "docker" / "compose.yaml").read_text())
    svc = c["services"]["openmousse"]
    assert svc["restart"] == "unless-stopped"
    assert svc["ports"] and all(str(p).startswith("127.0.0.1:") for p in svc["ports"])
    assert any(str(v).endswith(":/data") for v in svc["volumes"]) and "openmousse-data" in c["volumes"]
    assert svc["build"] == {"context": "..", "dockerfile": "docker/Dockerfile"}


def test_dockerignore_keeps_heavy_and_private_things_out():
    rules = {ln.strip() for ln in (REPO / ".dockerignore").read_text().splitlines() if ln.strip() and not ln.startswith("#")}
    assert {".git", "**/node_modules", "app/*", "!app/dist", "**/*.db"} <= rules
    assert {"**/.env", "**/secrets.env", "**/server.json", "**/config.json", "**/*.pem"} <= rules   # 本地的设置和密钥不进镜像
