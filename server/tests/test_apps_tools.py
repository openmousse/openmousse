"""连接器工具瘦身（apps.py）：apps.<id>.disabled_tools / enabled_tools 关掉用不上的；
给模型看的说明超过 apps.desc_max（默认 600 字）就截断。关掉的调用明确报错，不去连对方。
数据都是编的，不连真的远端。
"""
from __future__ import annotations

import asyncio

import pytest
from mcp.server.fastmcp.exceptions import ToolError

import apps
from config import settings

SCHEMA = {"type": "object", "properties": {}}


def run(coro):
    return asyncio.run(coro)


def seed(tmp_path, monkeypatch, *, policy=None, overrides=None) -> dict:
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    app = {"id": "notes", "name": "Notes", "url": "http://127.0.0.1:9/mcp", "auth": "none", "status": "connected",
           "agents": ["main"], "policy": policy or {"read": "auto", "write": "auto"}, "overrides": overrides or {},
           "tools": [{"name": "search", "title": "Search", "description": "find notes", "kind": "read", "inputSchema": SCHEMA},
                     {"name": "write", "title": "Write", "description": "w" * 900, "kind": "write", "inputSchema": SCHEMA}]}
    apps.put(app)
    return app


def test_lists_hide_tools_and_a_call_says_why_without_connecting(tmp_path, monkeypatch, server_json):
    seed(tmp_path, monkeypatch)
    server_json({"apps": {"notes": {"disabled_tools": ["write"]}}})
    assert set(apps.exposed()) == {"notes__search"}
    called: list[int] = []

    async def boom(*a, **k):
        called.append(1)
        raise AssertionError("disabled tool must not open a session")

    monkeypatch.setattr(apps, "session_run", boom)
    res = run(apps.mcp_call("notes__write", {}, {"agent": "main"}))
    assert res is not None and res.isError
    text = res.content[0].text
    assert "write" in text and "disabled_tools" in text and "apps.notes" in text
    with pytest.raises(ToolError, match="disabled_tools"):
        run(apps.run_tool(apps.get("notes"), [t for t in apps.get("notes")["tools"] if t["name"] == "write"][0], {}, {"agent": "main"}))
    assert called == []

    server_json({"apps": {"notes": {"enabled_tools": ["search"]}}})
    assert set(apps.exposed()) == {"notes__search"}
    res = run(apps.mcp_call("notes__write", {}, {"agent": "main"}))
    assert res.isError and "enabled_tools" in res.content[0].text

    server_json({"apps": {"notes": {"enabled_tools": []}}})
    assert apps.exposed() == {}

    server_json({"apps": {"notes": {"enabled_tools": ["search", "write"], "disabled_tools": ["write"]}}})
    assert set(apps.exposed()) == {"notes__search"}

    server_json({"apps": {"notes": {"disabled_tools": "write", "enabled_tools": "search"}}})  # 不是列表：当没写
    assert set(apps.exposed()) == {"notes__search", "notes__write"}

    server_json({})
    seed(tmp_path, monkeypatch, overrides={"write": "off"})
    assert set(apps.exposed()) == {"notes__search"}
    res = run(apps.mcp_call("notes__write", {}, {"agent": "main"}))
    assert res.isError and "Settings → Connectors" in res.content[0].text and "write" in res.content[0].text
    assert called == []
    assert run(apps.mcp_call("search", {}, {"agent": "main"})) is None
    missing = run(apps.mcp_call("notes__missing", {}, {"agent": "main"}))
    assert missing.isError and "isn't available" in missing.content[0].text


def test_descriptions_are_capped_before_the_prefix_and_the_ask_note(tmp_path, monkeypatch, server_json):
    app = seed(tmp_path, monkeypatch, overrides={"write": "ask"})
    write = [t for t in app["tools"] if t["name"] == "write"][0]
    server_json({})  # 接的是 OpenClaw（claw 没写）：/mcp 给它的说明照旧 8000 字，不掐
    desc = apps.tool_def("notes__write", app, write).description
    assert desc.startswith("[Notes] " + ("w" * 900)) and "…" not in desc  # 900 字一字不少
    server_json({"claw": {"kind": "builtin"}})
    assert apps.desc_max() == 600
    desc = apps.tool_def("notes__write", app, write).description
    assert desc.startswith("[Notes] " + ("w" * 600) + "…")
    assert "w" * 601 not in desc
    assert "Each call needs the user's OK" in desc  # 先问我那句在 600 字之外

    server_json({"claw": {"kind": "builtin"}, "apps": {"desc_max": 40}})
    desc = apps.tool_def("notes__write", app, write).description
    assert desc.startswith("[Notes] " + ("w" * 40) + "…") and "w" * 41 not in desc

    for bad in (0, -3, "no", None):
        server_json({"apps": {"desc_max": bad}})
        assert apps.desc_max() == 600


def test_the_builtin_connector_tool_uses_the_same_cap_and_error(tmp_path, monkeypatch, server_json):
    from engine.loop import ToolCtx, ToolFailure
    from engine.tools import connector_tool

    app = seed(tmp_path, monkeypatch, overrides={"write": "ask"})
    server_json({"claw": {"kind": "builtin"}, "apps": {"desc_max": 40, "notes": {"disabled_tools": ["write"]}}})
    write = [t for t in app["tools"] if t["name"] == "write"][0]
    tool = connector_tool(apps, "notes__write", app, write, "main")
    assert tool.description.startswith("[Notes] " + ("w" * 40) + "…")
    assert "Each call needs the user's OK" in tool.description

    async def boom(*a, **k):
        raise AssertionError("disabled tool must not open a session")

    monkeypatch.setattr(apps, "session_run", boom)
    ctx = ToolCtx(turn="t", kind="owner", agent="main", conversation="c", call_id="1", control=None)
    with pytest.raises(ToolFailure, match="disabled_tools"):
        run(tool.run({}, ctx))
