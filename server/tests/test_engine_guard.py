"""第 2 批：死循环检测（engine/guard.py，engine.loop.guard）：提醒、没人在看的硬停、有人在看只提醒、有进展清零、/mcp 那一层（Claude Code 的回合）。
模型是假的（FakeModel），工具是假的。"""
from __future__ import annotations

import asyncio

import pytest

import engine
from engine import guard, mcp_engine
from engine.session import MemorySession, text_of, user
from test_engine import FakeModel

ON = {"engine": {"model": "deepseek/deepseek-flash", "loop": {"guard": True}}}


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def fake(monkeypatch):
    from engine import providers

    def make(script):
        f = FakeModel(script)
        monkeypatch.setattr(providers, "stream", f.stream)
        return f
    return make


def failing(name: str = "fetch", level: str = "read", log: list | None = None) -> engine.Tool:
    async def go(args, ctx):
        if log is not None:
            log.append(dict(args))
        raise engine.ToolFailure("connection refused")
    return engine.Tool(name=name, description="always fails", schema={"type": "object", "properties": {"q": {"type": "string"}}}, run=go,
                       classify=lambda a: (level, "test"))


def steady(name: str = "lookup") -> engine.Tool:
    async def go(args, ctx):
        return "the same answer"
    return engine.Tool(name=name, description="always the same", schema={"type": "object", "properties": {"q": {"type": "string"}}}, run=go,
                       classify=lambda a: ("read", "test"))


def writer() -> engine.Tool:
    async def go(args, ctx):
        return "saved"
    return engine.Tool(name="save", description="writes", schema={"type": "object", "properties": {}}, run=go, classify=lambda a: ("undo", "test"))


def turn(tools, script_calls, origin="user", final="ok"):
    return [{"calls": [c]} for c in script_calls] + [{"text": final}], engine.Spec(kind="owner", model="deepseek/x", tools=tools,
                                                                                  conversation="t-guard", origin=origin)


def results(res) -> list[str]:
    return [text_of(m) for m in res.messages if m["role"] == "tool"]


def test_the_same_failing_call_twice_gets_a_warning(server_json, fake):
    server_json(ON)
    script, spec = turn([failing()], [("c1", "fetch", {"q": "a"}), ("c2", "fetch", {"q": "a"})])
    fake(script)
    out = results(run(engine.run_turn(spec, MemorySession(), user("go"))))
    assert "Tool loop" not in out[0]
    assert out[1].startswith("connection refused") and "[Tool loop warning: exact_warn; count=2;" in out[1]
    assert "failed 2 times with identical arguments" in out[1]


def test_same_tool_failing_with_different_args_warns_at_three(server_json, fake):
    server_json(ON)
    script, spec = turn([failing()], [("c1", "fetch", {"q": "a"}), ("c2", "fetch", {"q": "b"}), ("c3", "fetch", {"q": "c"})])
    fake(script)
    out = results(run(engine.run_turn(spec, MemorySession(), user("go"))))
    assert "Tool loop" not in out[0] + out[1] and "same_tool_warn; count=3" in out[2] and "diagnose before retrying" in out[2]


def test_a_read_only_call_with_no_progress_warns(server_json, fake):
    server_json(ON)
    script, spec = turn([steady()], [("c1", "lookup", {"q": "x"}), ("c2", "lookup", {"q": "x"})])
    fake(script)
    out = results(run(engine.run_turn(spec, MemorySession(), user("go"))))
    assert out[0] == "the same answer" and "no_progress_warn; count=2" in out[1]


def test_unattended_turns_stop_hard(server_json, fake):
    server_json(ON)
    log: list = []
    script, spec = turn([failing(log=log)], [(f"c{i}", "fetch", {"q": "a"}) for i in range(1, 8)], origin="auto")
    fake(script)
    res = run(engine.run_turn(spec, MemorySession(), user("go")))
    assert res.status == "halted" and len(log) == 5  # 第 6 次拦下、不跑
    out = results(res)
    assert len(out) == 6 and out[-1].startswith("Not run: Blocked fetch: the same tool call failed 5 times")
    assert res.text == guard.stop_reply(guard.Decision("block", "exact_block", "fetch", 5)) and "I stopped retrying" in res.text
    assert res.messages[-1]["role"] == "assistant"


def test_attended_turns_only_warn(server_json, fake):
    server_json(ON)
    log: list = []
    script, spec = turn([failing(log=log)], [(f"c{i}", "fetch", {"q": "a"}) for i in range(1, 8)], origin="user", final="giving up")
    fake(script)
    res = run(engine.run_turn(spec, MemorySession(), user("go")))
    assert res.status == "done" and res.text == "giving up" and len(log) == 7  # 有人在看：一次都不拦


def test_same_tool_halts_unattended_turns_at_eight(server_json, fake):
    server_json(ON)
    script, spec = turn([failing()], [(f"c{i}", "fetch", {"q": str(i)}) for i in range(1, 10)], origin="task")
    fake(script)
    res = run(engine.run_turn(spec, MemorySession(), user("go")))
    out = results(res)
    assert res.status == "halted" and len(out) == 8 and "Tool loop hard stop: same_tool_halt; count=8" in out[-1]


def test_progress_resets_the_count(server_json, fake):
    server_json(ON)
    script, spec = turn([failing(), writer()], [("c1", "fetch", {"q": "a"}), ("c2", "fetch", {"q": "a"}), ("c3", "save", {}),
                                                ("c4", "fetch", {"q": "a"})])
    fake(script)
    out = results(run(engine.run_turn(spec, MemorySession(), user("go"))))
    assert "exact_warn" in out[1] and out[2] == "saved" and "Tool loop" not in out[3]  # 改了东西：下一次算新的尝试


def test_off_by_default(server_json, fake):
    server_json({"engine": {"model": "deepseek/deepseek-flash"}})
    script, spec = turn([failing()], [(f"c{i}", "fetch", {"q": "a"}) for i in range(1, 7)], origin="auto")
    fake(script)
    res = run(engine.run_turn(spec, MemorySession(), user("go")))
    assert res.status == "done" and not any("Tool loop" in t for t in results(res))


def test_claude_code_turns_are_checked_at_the_mcp_layer(server_json):
    server_json(ON)
    log: list = []

    async def gate(tool, args, ctx):
        return None
    tok = mcp_engine.grant(agent="main", tools=[failing(log=log)], gate=gate, conversation="t-guard-mcp", origin="auto")
    try:
        g = mcp_engine.lookup(tok)

        async def go():
            return [await mcp_engine.call(g, "fetch", {"q": "a"}) for _ in range(6)]
        out = run(go())
    finally:
        mcp_engine.revoke(tok)
    assert "exact_warn; count=2" in out[1][0] and all(err for _, err in out)
    assert out[5][0].startswith("Not run: Blocked fetch") and len(log) == 5  # 没人在看：第 6 次不再替它跑
