"""config.py：配置文件解析、默认值、令牌挑选、Gateway 地址。"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from zoneinfo import ZoneInfoNotFoundError

import pytest

import config

# —— pick_api_token ——————————————————————————————————————————————


def test_pick_api_token_skips_program_tokens():
    tokens = {"mcp": "m", "mcp-helper": "mh", "sentinel": "s", "phone": "p", "laptop": "l"}
    assert config.pick_api_token(tokens) == "p"


def test_pick_api_token_order_independent_of_program_tokens_position():
    assert config.pick_api_token({"phone": "p", "mcp": "m"}) == "p"
    assert config.pick_api_token({"mcp": "m", "phone": "p"}) == "p"


def test_pick_api_token_skips_empty_values():
    assert config.pick_api_token({"old": "", "none": None, "phone": "p"}) == "p"


def test_pick_api_token_none_when_only_program_tokens():
    assert config.pick_api_token({"mcp": "m", "mcp-a": "x", "sentinel": "s"}) is None
    assert config.pick_api_token({}) is None


def test_pick_api_token_stringifies():
    assert config.pick_api_token({1: 12345}) == "12345"


# —— gateway_default ——————————————————————————————————————————————


def test_gateway_default_reads_port(tmp_path):
    p = tmp_path / "openclaw.json"
    p.write_text(json.dumps({"gateway": {"port": 19001}}), encoding="utf8")
    assert config.gateway_default(p) == "http://127.0.0.1:19001"


def test_gateway_default_port_as_string(tmp_path):
    p = tmp_path / "openclaw.json"
    p.write_text(json.dumps({"gateway": {"port": "19002"}}), encoding="utf8")
    assert config.gateway_default(p) == "http://127.0.0.1:19002"


@pytest.mark.parametrize("content", [
    None,  # 文件不存在
    "not json",
    "[]",  # 顶层不是对象
    json.dumps({"gateway": "oops"}),
    json.dumps({"gateway": {"port": "abc"}}),
    json.dumps({"gateway": {"port": 0}}),
    json.dumps({"gateway": None}),
    json.dumps({}),
])
def test_gateway_default_falls_back_to_18789(tmp_path, content):
    p = tmp_path / "openclaw.json"
    if content is not None:
        p.write_text(content, encoding="utf8")
    assert config.gateway_default(p) == "http://127.0.0.1:18789"


# —— raw / save ———————————————————————————————————————————————————


def test_raw_missing_file_is_empty(server_json):
    assert config.raw() == {}
    assert config.raw(fresh=True) == {}


def test_raw_invalid_json_is_empty(server_json):
    server_json("{broken")
    assert config.raw() == {}


def test_raw_reads_and_caches_by_mtime(server_json):
    path = server_json({"app_name": "A"})
    assert config.raw()["app_name"] == "A"
    st = path.stat()
    path.write_text(json.dumps({"app_name": "B"}), encoding="utf8")
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))  # 内容变了但 mtime 没变：用缓存
    assert config.raw()["app_name"] == "A"
    assert config.raw(fresh=True)["app_name"] == "B"


def test_raw_rereads_when_mtime_changes(server_json):
    path = server_json({"app_name": "A"})
    assert config.raw()["app_name"] == "A"
    path.write_text(json.dumps({"app_name": "B"}), encoding="utf8")
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))
    assert config.raw()["app_name"] == "B"


@pytest.mark.xfail(reason="server.json 顶层不是对象（比如 []）时 raw() 原样返回，调用方的 raw().get(...) 会抛 AttributeError；"
                          "坏 JSON 却会被当成 {}，两种坏文件的处理不一致", strict=True)
def test_raw_non_object_json_is_treated_as_empty(server_json):
    server_json("[]")
    assert config.raw() == {}


def test_save_roundtrip_and_permissions(server_json, tmp_path, monkeypatch):
    path = tmp_path / "nested" / "server.json"
    monkeypatch.setattr(config, "CONFIG_PATH", path)
    config.save({"user_name": "Testuser", "auth": {"tokens": {"phone": "t"}}})
    assert json.loads(path.read_text(encoding="utf8"))["user_name"] == "Testuser"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not path.with_suffix(".tmp").exists()
    assert config.raw(fresh=True)["auth"]["tokens"] == {"phone": "t"}


def test_save_keeps_non_ascii(server_json, tmp_path):
    path = server_json({})
    config.save({"app_name": "小慕"})
    assert "小慕" in path.read_text(encoding="utf8")


# —— Settings ——————————————————————————————————————————————————————


def test_settings_defaults_on_empty_config(server_json):
    s = config.Settings()
    assert s.app_name == "OpenMousse"
    assert s.timezone == "UTC"
    assert s.language == "en"
    assert (s.host, s.port) == ("127.0.0.1", 8080)
    assert s.workspace == s.openclaw_home / "workspace"
    assert s.scripts == s.workspace / "scripts"
    assert s.db == s.data_dir / "mousse.db"
    assert s.openclaw_json == s.openclaw_home / "openclaw.json"
    assert s.gateway == "http://127.0.0.1:18789"
    assert s.agent_default_skills == []


def test_settings_reads_fields(server_json, tmp_path):
    oc = tmp_path / "oc"
    oc.mkdir()
    (oc / "openclaw.json").write_text(json.dumps({"gateway": {"port": 18999}}), encoding="utf8")
    server_json({
        "app_name": "Demo", "timezone": "Asia/Tokyo", "language": "zh-CN",
        "bind": {"host": "100.64.0.1", "port": "9000"},
        "openclaw_home": str(oc), "data_dir": str(tmp_path / "d"), "db": str(tmp_path / "x.db"),
        "agent_default_skills": ["a", 2],
    })
    s = config.Settings()
    assert s.app_name == "Demo"
    assert str(s.tz) == "Asia/Tokyo"
    assert s.language == "zh"
    assert (s.host, s.port) == ("100.64.0.1", 9000)
    assert s.workspace == oc / "workspace"
    assert s.uploads == tmp_path / "d" / "uploads"
    assert s.db == tmp_path / "x.db"
    assert s.gateway == "http://127.0.0.1:18999"
    assert s.agent_default_skills == ["a", "2"]


def test_settings_explicit_gateway_wins(server_json, tmp_path):
    server_json({"openclaw_home": str(tmp_path), "gateway": "http://10.0.0.5:1234"})
    assert config.Settings().gateway == "http://10.0.0.5:1234"


def test_settings_expands_user(server_json, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    server_json({"data_dir": "~/somewhere"})
    assert config.Settings().data_dir == tmp_path / "somewhere"


@pytest.mark.parametrize("lang,expected", [("ZH", "zh"), ("zh_TW", "zh"), ("en", "en"), ("fr", "en"), ("", "en"), (None, "en")])
def test_settings_language(server_json, lang, expected):
    server_json({"language": lang})
    assert config.Settings().language == expected


def test_settings_bad_timezone_raises(server_json):
    server_json({"timezone": "Mars/Olympus"})
    with pytest.raises(ZoneInfoNotFoundError):
        config.Settings()


def test_settings_bad_port_raises(server_json):
    server_json({"bind": {"port": "eighty"}})
    with pytest.raises(ValueError):
        config.Settings()


def test_settings_dynamic_auth(server_json):
    s = config.Settings()
    server_json({"auth": {"tokens": {"mcp": "m", "phone": "p"}, "tailscale_nodes": ["node-a"], "trust_loopback": 1}})
    assert s.tokens() == {"mcp": "m", "phone": "p"}
    assert s.api_token() == "p"
    assert s.tailscale_nodes() == {"node-a"}
    assert s.trust_loopback() is True


def test_settings_dynamic_auth_empty(server_json):
    s = config.Settings()
    assert s.tokens() == {}
    assert s.api_token() is None
    assert s.tailscale_nodes() == set()
    assert s.trust_loopback() is False
    assert s.nutrition_targets() is None


def test_set_agent_workspace_add_and_remove(server_json, tmp_path):
    server_json({"app_name": "Keep"})
    s = config.Settings()
    s.set_agent_workspace("helper", tmp_path / "ws-helper")
    assert s.agent_workspaces == {"helper": tmp_path / "ws-helper"}
    assert s.memory_files["helper"] == tmp_path / "ws-helper" / "MEMORY.md"
    assert s.memory_files["main"] == s.workspace / "MEMORY.md"
    assert s.group_agents == {"helper"}
    assert config.raw(fresh=True)["app_name"] == "Keep"
    s.set_agent_workspace("helper", None)
    s.set_agent_workspace("missing", None)  # 删一个不存在的不报错
    assert s.agent_workspaces == {}


# —— user_word —————————————————————————————————————————————————————


@pytest.mark.parametrize("name,expected", [(None, "用户"), ("", "用户"), ("   ", "用户"), ("Alex", " Alex "), ("小明", "小明")])
def test_user_word(server_json, name, expected):
    server_json({"user_name": name} if name is not None else {})
    assert config.user_word() == expected


def test_config_path_comes_from_env():
    assert Path(os.environ["MOUSSE_SERVER_CONFIG"]).name == "server.json"
    assert "mousse-test-" in os.environ["MOUSSE_SERVER_CONFIG"]
