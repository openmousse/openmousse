"""第 2 批：只读工具一起跑（engine/loop.py 的 _segments、_run_batch；engine.loop.parallel）。模型是假的（FakeModel），工具是假的（按时间记）。"""
from __future__ import annotations

import asyncio
import time

import pytest

import engine
from engine import events, loop
from engine.session import DbSession, MemorySession, text_of, user
from test_engine import FakeModel

ON = {"engine": {"model": "deepseek/deepseek-flash", "loop": {"parallel": True}}}


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


def tool(name: str, log: list, *, delay: float = 0.2, level: str = "read", fail: bool = False, sequential: bool = False) -> engine.Tool:
    async def go(args, ctx):
        log.append(("start", name, time.monotonic()))
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            log.append(("cancelled", name, time.monotonic()))
            raise
        log.append(("end", name, time.monotonic()))
        if fail:
            raise engine.ToolFailure(f"{name} broke")
        return f"{name} ok"
    return engine.Tool(name=name, description=f"{name} tool", schema={"type": "object", "properties": {}}, run=go,
                       classify=lambda a: (level, "test"), sequential=sequential, title=lambda a: name)


def spans(log: list) -> dict[str, tuple[float, float]]:
    start = {n: t for k, n, t in log if k == "start"}
    end = {n: t for k, n, t in log if k in ("end", "cancelled")}
    return {n: (start[n], end.get(n, float("inf"))) for n in start}


def overlap(a: tuple[float, float], b: tuple[float, float]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def calls(*names: str) -> list:
    return [(f"c{i}", n, {}) for i, n in enumerate(names, 1)]


def test_reads_run_together_and_results_keep_their_order(server_json, fake):
    server_json(ON)
    log: list = []
    tools = [tool(n, log, delay=0.3) for n in ("alpha", "bravo", "charlie")]
    fake([{"calls": calls("alpha", "bravo", "charlie")}, {"text": "done"}])
    seen: list = []
    t0 = time.monotonic()
    res = run(engine.run_turn(engine.Spec(kind="owner", model="deepseek/x", tools=tools, conversation="t-par-order"), MemorySession(), user("go"),
                              on_event=seen.append))
    assert time.monotonic() - t0 < 0.8  # 三个各 0.3 秒：一起跑
    s = spans(log)
    assert overlap(s["alpha"], s["bravo"]) and overlap(s["bravo"], s["charlie"])
    results = [m for m in res.messages if m["role"] == "tool"]
    assert [m["id"] for m in results] == ["c1", "c2", "c3"] and [text_of(m) for m in results] == ["alpha ok", "bravo ok", "charlie ok"]
    assert sorted(e["id"] for e in seen if e["type"] == "tool_run") == ["c1", "c2", "c3"]
    assert sorted(e["id"] for e in seen if e["type"] == "tool_end") == ["c1", "c2", "c3"]
    assert [(s_["type"], s_["status"]) for s_ in events.steps(res.turn)].count(("tool", "ok")) == 3


def test_writes_and_interactive_calls_are_barriers(server_json, fake):
    server_json(ON)
    log: list = []
    tools = [tool("look", log), tool("save", log, level="undo"), tool("peek", log), tool("scan", log), tool("ask_user", log), tool("glance", log),
             tool("check", log)]
    fake([{"calls": calls("look", "save", "peek", "scan", "ask_user", "glance", "check")}, {"text": "done"}])
    res = run(engine.run_turn(engine.Spec(kind="owner", model="deepseek/x", tools=tools, conversation="t-par-bar"), MemorySession(), user("go")))
    s = spans(log)
    assert s["look"][1] <= s["save"][0] and s["save"][1] <= s["peek"][0]  # 写的那个前后不跨着跑
    assert overlap(s["peek"], s["scan"])  # 挨着的两个只读：一起
    assert s["scan"][1] <= s["ask_user"][0] and s["ask_user"][1] <= s["glance"][0]  # 交互的当分隔
    assert overlap(s["glance"], s["check"])
    assert [m["id"] for m in res.messages if m["role"] == "tool"] == [f"c{i}" for i in range(1, 8)]


def test_stop_cancels_every_call_in_the_batch(server_json, fake):
    server_json(ON)
    log: list = []
    tools = [tool(n, log, delay=5) for n in ("one", "two", "three")]
    fake([{"calls": calls("one", "two", "three")}])
    control = engine.Control()

    async def go():
        task = asyncio.ensure_future(engine.run_turn(engine.Spec(kind="owner", model="deepseek/x", tools=tools, conversation="t-par-stop"),
                                                     MemorySession(), user("go"), control=control))
        while sum(1 for k, _, _ in log if k == "start") < 3:
            await asyncio.sleep(0.01)
        control.abort()
        return await asyncio.wait_for(task, 3)
    res = run(go())
    assert res.status == "aborted"
    assert [text_of(m) for m in res.messages if m["role"] == "tool"] == ["Stopped by the user."] * 3
    assert sorted(n for k, n, _ in log if k == "cancelled") == ["one", "three", "two"]


def test_one_failure_does_not_spoil_the_others(server_json, fake):
    server_json(ON)
    log: list = []
    tools = [tool("ok1", log), tool("bad", log, fail=True), tool("ok2", log)]
    fake([{"calls": calls("ok1", "bad", "ok2")}, {"text": "done"}])
    res = run(engine.run_turn(engine.Spec(kind="owner", model="deepseek/x", tools=tools, conversation="t-par-fail"), MemorySession(), user("go")))
    out = {m["id"]: (text_of(m), m["error"]) for m in res.messages if m["role"] == "tool"}
    assert out == {"c1": ("ok1 ok", False), "c2": ("bad broke", True), "c3": ("ok2 ok", False)} and res.status == "done"


def test_sequential_tools_and_the_worker_cap(server_json, fake):
    server_json({"engine": {"model": "deepseek/deepseek-flash", "loop": {"parallel": {"max": 2}}}})
    log: list = []
    tools = [tool("page", log, sequential=True), tool("a", log), tool("b", log), tool("c", log), tool("d", log)]
    fake([{"calls": calls("page", "a", "b", "c", "d")}, {"text": "done"}])
    run(engine.run_turn(engine.Spec(kind="owner", model="deepseek/x", tools=tools, conversation="t-par-cap"), MemorySession(), user("go")))
    s = spans(log)
    assert s["page"][1] <= min(s[n][0] for n in "abcd")  # 标了 sequential：自己跑
    times = sorted([(st, 1) for st, _ in (s[n] for n in "abcd")] + [(en, -1) for _, en in (s[n] for n in "abcd")])
    busy = peak = 0
    for _, d in times:
        busy += d
        peak = max(peak, busy)
    assert peak == 2  # 同时最多 2 个


def test_off_by_default(server_json, fake):
    server_json({"engine": {"model": "deepseek/deepseek-flash"}})
    log: list = []
    tools = [tool(n, log, delay=0.1) for n in ("x1", "x2", "x3")]
    fake([{"calls": calls("x1", "x2", "x3")}, {"text": "done"}])
    run(engine.run_turn(engine.Spec(kind="owner", model="deepseek/x", tools=tools, conversation="t-par-off"), MemorySession(), user("go")))
    s = spans(log)
    assert s["x1"][1] <= s["x2"][0] and s["x2"][1] <= s["x3"][0]  # 照旧一个一个跑


def test_an_outside_cancel_keeps_the_session_whole(server_json, fake):
    """外面取消（关服务）：跑完的照存，正在跑的记「可能做了、可能没做」，没轮到的补「没做」。"""
    server_json({"engine": {"model": "deepseek/deepseek-flash", "loop": {"parallel": {"max": 2}}}})
    log: list = []
    tools = [tool("quick", log, delay=0.05), tool("slow", log, delay=5), tool("later", log, delay=5)]
    fake([{"calls": calls("quick", "slow", "later")}])
    s = DbSession.create("test:par-cancel", kind="owner")

    async def go():
        task = asyncio.ensure_future(engine.run_turn(engine.Spec(kind="owner", model="deepseek/x", tools=tools, conversation="t-par-cancel"), s,
                                                     user("go")))
        while not any(k == "end" for k, _, _ in log) or sum(1 for k, _, _ in log if k == "start") < 3:
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    run(go())
    results = {m["id"]: text_of(m) for m in s.messages() if m["role"] == "tool"}
    assert results["c1"] == "quick ok" and results["c2"] == loop.CANCELLED_MID_CALL and results["c3"] == loop.CANCELLED_MID_CALL
