"""第一次打开的三步和设置中心（第 9 批，2026-10-19）在服务器这一边加的那几样：/api/health 报 setup 这一项能力、no_model；
服务商列表带服务器是不是 Mac（「仅 Mac」的本机跑的那几家）、每个地区自己拿密钥的网址。不连网、不花钱。"""
from __future__ import annotations

import sys

import pytest
from fastapi.testclient import TestClient

TOKENS = {"phone": "tok-phone", "mcp": "tok-mcp"}
H = {"Authorization": "Bearer tok-phone"}


@pytest.fixture
def client(server_json, tmp_path, monkeypatch):
    import main
    from engine import catalog, runners, setup_api
    monkeypatch.setattr(setup_api, "_mcache_path", lambda: tmp_path / "setup-models.json")
    setup_api.reset()
    for pid in catalog.LOCAL:  # 本机的一律当没在跑：不探这台机器上真的端口
        monkeypatch.setitem(catalog.LOCAL[pid], "url", "http://127.0.0.1:9/v1")
        monkeypatch.setitem(catalog.LOCAL[pid], "alt", [])
    monkeypatch.setattr(runners, "_warm", True)
    monkeypatch.setattr(runners, "_found", {})
    for name in {e["key_env"] for e in catalog.CLOUD.values()}:
        monkeypatch.delenv(name, raising=False)
    server_json({"claw": {"kind": "builtin"}, "auth": {"tokens": TOKENS}, "timezone": "UTC", "language": "en", "data_dir": str(tmp_path / "data"),
                 "openclaw_home": str(tmp_path / "openclaw")})
    return TestClient(main.app)


def test_health_reports_setup_and_no_model(client):
    h = client.get("/api/health", headers=H).json()
    assert "setup" in h["server"]["features"]
    assert h["no_model"] is True and h["claw"]["kind"] == "builtin"


def test_providers_say_whether_the_server_is_a_mac_and_each_region_has_its_key_page(client, monkeypatch):
    from engine import catalog
    r = client.get("/api/setup/models?probe=0", headers=H).json()
    assert r["host"] == {"mac": sys.platform == "darwin"}
    kimi = next(p for p in r["providers"] if p["id"] == "moonshot")
    want = {rid: v["key_url"] for rid, v in catalog.CLOUD["moonshot"]["regions"].items()}
    assert {g["id"]: g["keyUrl"] for g in kimi["regions"]} == want
    assert kimi["regions"][1]["keyUrl"] != kimi["regions"][0]["keyUrl"]  # 跟着地区变
    monkeypatch.setattr(sys, "platform", "darwin")
    assert client.get("/api/setup/models?probe=0", headers=H).json()["host"] == {"mac": True}


def test_setup_is_still_only_for_the_owners_devices(client):
    assert client.get("/api/setup/models?probe=0", headers={"Authorization": "Bearer tok-mcp"}).status_code == 403
    assert client.get("/api/setup/models?probe=0").status_code == 401


def test_overview_lists_each_agents_hands(client, server_json, tmp_path, monkeypatch):
    """「手」页：一个 Agent 一行（主对话在前）。文件只在自己的工作区；打开网页、搜索、主机命令照 server.json 现读。"""
    import config
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setattr(config.settings, "workspace", ws)  # 路径是启动时算好的
    base = {"claw": {"kind": "builtin"}, "auth": {"tokens": TOKENS}, "timezone": "UTC", "language": "en", "data_dir": str(tmp_path / "data"),
            "openclaw_home": str(tmp_path / "openclaw"), "workspace": str(ws)}
    server_json(base)
    main = next(a for a in client.get("/api/setup", headers=H).json()["layers"]["hands"]["agents"] if a["id"] == "main")
    assert main == {"id": "main", "name": "Main chat", "files": True, "webFetch": True, "webSearch": False, "sandbox": False, "browser": False,
                    "hostExec": False}
    server_json({**base, "engine": {"files": {"agents": {"main": False}}, "web": {"agents": {"main": {"fetch": False}}},
                                    "host_exec": {"agents": {"main": {"allow": ["/usr/bin/uptime"]}}}}})
    main = next(a for a in client.get("/api/setup", headers=H).json()["layers"]["hands"]["agents"] if a["id"] == "main")
    assert (main["files"], main["webFetch"], main["hostExec"]) == (False, False, True)
