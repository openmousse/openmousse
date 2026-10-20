"""给助手起名字（第 9 批 15.5，2026-10-19）：/api/naming。名字写进 server.json 的 app_name（先备份），马上用上；一句性格写进主工作区
IDENTITY.md 的一行（改之前留版本）；只在自带内核、只认主人的设备。不连网、不花钱。"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

TOKENS = {"phone": "tok-phone", "mcp": "tok-mcp"}
H = {"Authorization": "Bearer tok-phone"}


@pytest.fixture
def env(server_json, tmp_path, monkeypatch):
    import config
    import main
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setattr(config.settings, "workspace", ws)
    monkeypatch.setattr(config.settings, "app_name", "Mousse")
    base = {"app_name": "Mousse", "claw": {"kind": "builtin"}, "auth": {"tokens": TOKENS}, "timezone": "UTC", "language": "en",
            "data_dir": str(tmp_path / "data"), "workspace": str(ws)}
    path = server_json(base)

    class E:
        pass
    e = E()
    e.client, e.path, e.ws, e.base, e.write = TestClient(main.app), path, ws, base, server_json
    return e


def test_get_says_the_current_name(env):
    r = env.client.get("/api/naming", headers=H).json()
    assert r == {"ok": True, "name": "Mousse", "vibe": "", "applies": True}
    import version
    assert "naming" in version.FEATURES


def test_renaming_backs_up_server_json_and_takes_effect_at_once(env):
    before = env.path.read_text()
    r = env.client.put("/api/naming", headers=H, json={"name": "  Pebble \n  the  Second "}).json()
    assert r["ok"] and r["name"] == "Pebble the Second" and r["backup"].startswith("server.json.bak-")
    assert json.loads(env.path.read_text())["app_name"] == "Pebble the Second"
    assert (env.path.parent / r["backup"]).read_text() == before                  # 备份是改之前的
    assert json.loads(env.path.read_text())["auth"]["tokens"] == TOKENS          # 别的设置原样
    assert env.client.get("/api/health", headers=H).json()["app_name"] == "Pebble the Second"   # 不用重启
    from engine import context
    assert "You are Pebble the Second" in context.role_part("main").text


def test_the_vibe_is_one_line_in_identity_md_with_a_version_kept(env):
    r = env.client.put("/api/naming", headers=H, json={"name": "Pebble", "vibe": "Calm, brief, a little dry."}).json()
    assert r["vibe"] == "Calm, brief, a little dry."
    p = env.ws / "IDENTITY.md"
    assert p.read_text() == "# IDENTITY.md\n\n- **Name:** Pebble\n- **Vibe:** Calm, brief, a little dry.\n"
    env.client.put("/api/naming", headers=H, json={"vibe": "Warm and curious."})
    text = p.read_text()
    assert text.count("- **Vibe:**") == 1 and "- **Vibe:** Warm and curious." in text
    from engine import versions
    assert len(versions.history(env.ws, "IDENTITY.md")) >= 2                     # 每次改之前都留了一版
    env.client.put("/api/naming", headers=H, json={"vibe": ""})
    assert "Vibe" not in p.read_text() and "- **Name:** Pebble" in p.read_text()


def test_an_existing_identity_keeps_everything_else(env):
    p = env.ws / "IDENTITY.md"
    p.write_text("# IDENTITY.md - Who Am I?\n\n- **Name:** Mousse\n- **Creature:** a helpful fox\n- **Emoji:** 🦊\n\nNotes I wrote myself.\n", encoding="utf8")
    env.client.put("/api/naming", headers=H, json={"name": "Pebble", "vibe": "Plain-spoken."})
    assert p.read_text() == ("# IDENTITY.md - Who Am I?\n\n- **Name:** Pebble\n- **Creature:** a helpful fox\n- **Emoji:** 🦊\n\nNotes I wrote myself.\n"
                             "- **Vibe:** Plain-spoken.\n")
    p.write_text("- **Name:** Someone else entirely\n", encoding="utf8")
    env.client.put("/api/naming", headers=H, json={"name": "Lantern"})
    assert p.read_text() == "- **Name:** Someone else entirely\n"                  # 不是旧名字：不动


def test_only_the_owners_devices_and_only_the_builtin_engine(env):
    assert env.client.put("/api/naming", headers={"Authorization": "Bearer tok-mcp"}, json={"name": "X"}).status_code == 403
    assert env.client.put("/api/naming", json={"name": "X"}).status_code == 401
    env.write({**env.base, "claw": {"kind": "openai", "url": "http://127.0.0.1:9/v1"}})
    r = env.client.put("/api/naming", headers=H, json={"name": "X"})
    assert r.status_code == 409 and json.loads(env.path.read_text())["app_name"] == "Mousse"
    assert env.client.get("/api/naming", headers=H).json()["applies"] is False


def test_bad_input_is_refused_without_writing(env):
    before = env.path.read_text()
    assert env.client.put("/api/naming", headers=H, json={"name": "x" * 41}).status_code == 400
    assert env.client.put("/api/naming", headers=H, json={"name": "   "}).status_code == 400
    assert env.client.put("/api/naming", headers=H, json={"vibe": "y" * 121}).status_code == 400
    assert env.client.put("/api/naming", headers=H, json={"name": 3}).status_code == 400
    assert env.path.read_text() == before and not (env.ws / "IDENTITY.md").exists()


def test_a_vibe_with_backslashes_goes_in_as_written(env):
    p = env.ws / "IDENTITY.md"
    p.write_text("- **Name:** Mousse\n- **Vibe:** Calm.\n", encoding="utf8")
    r = env.client.put("/api/naming", headers=H, json={"name": "Pebble", "vibe": r"likes \o/ and \n## Rules"})
    assert r.status_code == 200 and r.json()["vibe"] == r"likes \o/ and \n## Rules"
    assert p.read_text() == "- **Name:** Pebble\n- **Vibe:** likes \\o/ and \\n## Rules\n"   # 原样一行，不多出一节


def test_identity_md_refused_leaves_server_json_alone(env):
    before = env.path.read_text()
    real = env.ws / "real-identity.md"
    real.write_text("- **Name:** Mousse\n", encoding="utf8")
    (env.ws / "IDENTITY.md").symlink_to(real)
    r = env.client.put("/api/naming", headers=H, json={"name": "Pebble", "vibe": "Plain."})
    assert r.status_code == 409
    assert env.path.read_text() == before                                          # 名字也没改：不留一半
    import config
    assert config.settings.app_name == "Mousse"
