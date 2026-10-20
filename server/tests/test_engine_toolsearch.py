"""第 3 批第 10 项：工具多了按需加载（engine/toolsearch.py）。不写 engine.tools.search：MCP 工具照旧直接给；开了、多过门槛：收进
tool_search / tool_describe / tool_call（目录按名字排、写在 tool_search 的说明里，同一份工具每次一模一样），别的工具照旧直接给；
找、看参数、调：分类照被调的那个（只读的直接做；要点头的交它自己的卡，同意了照它自己的路做一次）。MCP 服务是 test_engine_mcp 的那个小 stdio 服务。"""
from __future__ import annotations

import asyncio
import json

import pytest

import engine
from engine import mcp_client, tools, toolsearch
from test_engine_mcp import env, script  # noqa: F401 — 那边的小 MCP 服务

pytest.importorskip("mcp")


def run(coro):
    return asyncio.run(coro)


def ctx() -> engine.ToolCtx:
    return engine.ToolCtx(turn="t", kind="owner", agent="main", conversation="main", call_id="1", control=engine.Control())


async def listed(agent: str = "main") -> dict[str, engine.Tool]:
    await mcp_client.ready(agent)
    return {t.name: t for t in tools.for_agent(agent)}


def test_off_by_default_and_below_the_threshold(env):  # noqa: F811 — env 是导入的 fixture
    async def go():
        plain = await listed()
        env.cfg(extra={"engine": {"tools": {"search": {"threshold": 100}}}})
        few = await listed()
        await mcp_client.shutdown()
        return plain, few
    plain, few = run(go())
    assert "demo__echo" in plain and "tool_search" not in plain and toolsearch.cfg() is not None
    assert "demo__echo" in few and "tool_call" not in few  # 没多过门槛：照旧


def test_many_tools_go_behind_search_describe_and_call(env):  # noqa: F811
    env.cfg(extra={"engine": {"tools": {"search": {"threshold": 3}}}})

    async def go():
        have = await listed()
        again = await listed()
        found = await have["tool_search"].run({"query": "weather"}, ctx())
        shown = await have["tool_describe"].run({"name": "demo__get_weather"}, ctx())
        level = have["tool_call"].level({"name": "demo__echo", "arguments": {"text": "hi"}})
        echoed = await have["tool_call"].run({"name": "demo__echo", "arguments": {"text": "hello there, fictional friend"}}, ctx())
        with pytest.raises(engine.ToolFailure, match="no tool"):
            await have["tool_call"].run({"name": "demo__nope"}, ctx())
        with pytest.raises(engine.ToolFailure, match="missing 'city'"):  # 照被调的那个的 schema 查，和直接调它一样
            await have["tool_call"].run({"name": "demo__get_weather", "arguments": {}}, ctx())
        sunny = await have["tool_call"].run({"name": "demo__get_weather", "arguments": '{"city": "Fictionville"}'}, ctx())
        await mcp_client.shutdown()
        return have, again, found, shown, level, echoed, sunny
    have, again, found, shown, level, echoed, sunny = run(go())
    assert "sunny in Fictionville" in sunny  # arguments 写成 JSON 字符串也认
    fixed = have["tool_call"].prepare({"name": "demo__get_weather", "arguments": '{"city": "Fictionville"}'})
    assert engine.loop.check_args(fixed, have["tool_call"].schema) == (fixed, None) and fixed["arguments"] == {"city": "Fictionville"}  # loop 那一关也过
    assert not any(n.startswith("demo__") for n in have) and {"tool_search", "tool_describe", "tool_call"} <= set(have)
    assert "memory_search" in have or "read_file" in have or len(have) > 3  # 别的工具照旧直接给
    desc = have["tool_search"].description
    assert "- demo__echo:" in desc and desc.index("demo__echo") < desc.index("demo__wipe")  # 目录按名字排
    assert again["tool_search"].description == desc and again["tool_call"].schema == have["tool_call"].schema  # 同一份工具：一模一样（缓存不破）
    assert "demo__get_weather" in found and "tool_describe" in found
    assert "Parameters (JSON Schema)" in shown and '"city"' in shown
    assert level[0] == "read" and "hello there, fictional friend" in echoed


def test_a_call_that_needs_the_ok_sends_that_tools_own_card(env, server_json, monkeypatch):  # noqa: F811
    import chat
    import inbox
    from engine import providers
    from test_engine import FakeModel
    env.cfg(extra={"claw": {"kind": "builtin"}, "engine": {"model": "deepseek/deepseek-flash", "tools": {"search": {"threshold": 3}}}})
    tools.install_hooks()
    monkeypatch.setattr(providers, "stream", FakeModel([{"text": "Done."}]).stream)

    async def go():
        have = await listed()
        call = have["tool_call"]
        args = {"name": "demo__wipe", "arguments": {"target": "old-notes"}}
        assert call.level(args)[0] == "ask"
        out = await tools.gate(call, args, ctx())
        assert isinstance(out, dict) and out["status"] == "approval-pending" and not (env.marks / "wiped").exists()
        with chat._lock, inbox.idb() as conn:
            card = dict(conn.execute("SELECT * FROM inbox WHERE id=?", (out["inbox"],)).fetchone())
        res = await inbox.act(out["inbox"], inbox.ActIn(action="approve"))
        for _ in range(100):
            r = chat.RUNS.get("main")
            if r and r.done:
                break
            await asyncio.sleep(0.05)
        row = tools.call_of(out["inbox"])
        await mcp_client.shutdown()
        return card, res, row
    card, res, row = run(go())
    assert card["kind"] == "tool" and card["title"] == "demo: wipe" and "old-notes" in card["detail"]  # 就是直接调 demo__wipe 时那张卡
    assert res["item"]["status"] == "done" and (env.marks / "wiped").read_text() == "old-notes"
    assert row["tool"] == "mcp:demo__wipe" and json.loads(row["args"])["arguments"] == {"target": "old-notes"}


def test_hooks_still_see_a_tool_called_through_tool_call(env, tmp_path):  # noqa: F811
    """收进 tool_call 的工具照样过 tool:before 的钩子，钩子看到的是它的真名字（tools.for_agent：钩子包在收起来之前，第 5 批）。"""
    import sys
    from engine import user_hooks
    guard = tmp_path / "guard.py"
    guard.write_text('import json, sys\np = json.load(sys.stdin)\nprint(json.dumps({"decision": "block", "reason": "no echo: " + p["tool_name"]}))\n',
                     encoding="utf8")
    env.cfg(extra={"engine": {"tools": {"search": {"threshold": 3}},
                              "hooks": {"tool:before": [{"command": [sys.executable, str(guard)], "matcher": "demo__echo"}]}}})
    for h in user_hooks.hooks():
        user_hooks.approve(h)

    async def go():
        have = await listed()
        with pytest.raises(engine.ToolFailure, match="no echo: demo__echo"):
            await have["tool_call"].run({"name": "demo__echo", "arguments": {"text": "hi"}}, ctx())
        sunny = await have["tool_call"].run({"name": "demo__get_weather", "arguments": {"city": "Fictionville"}}, ctx())
        await mcp_client.shutdown()
        return have, sunny
    have, sunny = run(go())
    assert "tool_call" in have and "demo__echo" not in have and "sunny in Fictionville" in sunny  # 钩子只管它点名的那个
