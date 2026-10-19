"""内核第 3 项：事件流接 app（engine/loop.py 的事件、chat.py 的 engine_stream / note_engine / steer_engine / stop / resume_engine，
cards.py / settle.py 在 builtin 下不读 OpenClaw 的台账）。模型是假的（FakeModel 按剧本回放），命令行脚本换成假的跑法（能设延迟）。"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from types import SimpleNamespace

import pytest

import bridges
import engine
from engine import events, loop, store
from engine.session import DbSession, MemorySession, day_key, text_of, tool_calls, user
from test_engine import FakeModel

BUILTIN = {"claw": {"kind": "builtin"}, "engine": {"model": "deepseek/deepseek-flash"}}


@pytest.fixture
def fake(monkeypatch):
    from engine import providers

    def make(script):
        f = FakeModel(script)
        monkeypatch.setattr(providers, "stream", f.stream)
        return f
    return make


@pytest.fixture
def bridge(monkeypatch):
    """假的命令行：记下每次 (工具, args)；delays[工具] 秒以后才回（点停时记下谁被取消了）。"""
    b = SimpleNamespace(calls=[], replies={}, delays={}, cancelled=[])

    async def fake_run(name, args, stdin, agent, *, kill_on_cancel=False):
        b.calls.append((name, list(args)))
        try:
            if b.delays.get(name):
                await asyncio.sleep(b.delays[name])
        except asyncio.CancelledError:
            b.cancelled.append(name)
            raise
        return b.replies.get(name, f"ok: {name} {' '.join(args[:1])}")
    monkeypatch.setattr(bridges, "run", fake_run)
    return b


def run(coro):
    return asyncio.run(coro)


async def until(cond, timeout: float = 5.0) -> None:
    t0 = time.monotonic()
    while not cond():
        if time.monotonic() - t0 > timeout:
            raise AssertionError("timed out waiting")
        await asyncio.sleep(0.01)


async def collect(gen, until_event: str = "done", timeout: float = 10.0) -> list[tuple[str, dict]]:
    """读一条 SSE（chat.attach / send 的 body_iterator）到 until_event 为止 → [(event, data)]。"""
    out: list[tuple[str, dict]] = []

    async def go() -> None:
        async for chunk in gen:
            for block in (chunk.decode() if isinstance(chunk, bytes) else chunk).split("\n\n"):
                if not block.strip():
                    continue
                ev, data = "message", {}
                for line in block.split("\n"):
                    if line.startswith("event:"):
                        ev = line[6:].strip()
                    elif line.startswith("data:"):
                        data = json.loads(line[5:].strip())
                out.append((ev, data))
                if ev == until_event:
                    return
    await asyncio.wait_for(go(), timeout)
    return out


def running_step(run_) -> bool:
    return bool(run_.progress and any(s["status"] == "running" for s in run_.progress["steps"]))


def msg_row(chat, mid: int):
    with chat._lock, chat.db() as conn:
        return conn.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()


def replies(chat, thread: str) -> list:
    with chat._lock, chat.db() as conn:
        return conn.execute("SELECT * FROM messages WHERE thread=? AND role='grava' ORDER BY id", (thread,)).fetchall()


# —— 内核这一层：事件 ——————————————————————————————————————————————————————

def echo(delay: float = 0.0, fail: bool = False) -> engine.Tool:
    async def go(args, ctx):
        if delay:
            await asyncio.sleep(delay)
        if fail:
            raise engine.ToolFailure("nope")
        return "ok"
    return engine.Tool(name="echo", description="", schema={"type": "object", "properties": {"x": {"type": "string"}}}, run=go,
                       title=lambda a: f"echo {a.get('x')}", timeout=1)


def test_loop_emits_model_steps_steers_and_how_tools_ended(fake):
    seen: list = []
    control = engine.Control()

    async def first(args, ctx):
        if args["x"] == "1":
            assert ctx.control.steer({**user("one more"), "ref": 7})
        return "ok"
    t = engine.Tool(name="echo", description="", schema={"type": "object", "properties": {"x": {"type": "string"}}}, run=first,
                    title=lambda a: f"echo {a.get('x')}")
    control.follow_up({**user("and later"), "ref": 8})
    fake([{"thinking": "hmm", "calls": [("c1", "echo", {"x": "1"})]}, {"text": "noted"}, {"text": "later done"}])
    res = run(engine.run_turn(engine.Spec(kind="owner", model="deepseek/x", tools=[t]), MemorySession(), user("go"), on_event=seen.append,
                              control=control))
    kinds = [e["type"] for e in seen]
    assert kinds == ["model", "thinking", "tool_start", "tool_run", "tool_end", "steer", "model", "text", "follow_up", "model", "text"]
    assert seen[3] == {"type": "tool_run", "id": "c1", "name": "echo", "detail": "echo 1"}
    assert seen[4]["status"] == "ok" and seen[4]["ok"] is True
    assert seen[5]["ref"] == 7 and seen[8]["ref"] == 8 and [e["step"] for e in seen if e["type"] == "model"] == [1, 2, 3]
    assert res.status == "done" and res.pending == []
    assert [s["type"] for s in events.steps(res.turn)].count("follow_up") == 1  # 后续记成自己的一步，不再记成插话


def test_follow_up_waits_while_there_are_steers(fake):
    """说完了：有插话先带插话再问一次，后续等真要停下（pi 的顺序）。"""
    control = engine.Control()
    control.follow_up(user("later"))
    control.steer(user("now"))
    f = fake([{"text": "a"}, {"text": "b"}, {"text": "c"}])
    run(engine.run_turn(engine.Spec(kind="owner", model="deepseek/x"), MemorySession(), user("go"), control=control))
    assert text_of(f.requests[1][1].messages[-1]) == "now" and text_of(f.requests[2][1].messages[-1]) == "later"


def test_tool_end_says_why(fake):
    seen: list = []

    async def gate(tool, args, ctx):
        return {"status": "approval-pending", "inbox": "ib-1"} if args["x"] == "ask" else "Not run: no." if args["x"] == "no" else None
    fake([{"calls": [("c1", "echo", {"x": "ask"}), ("c2", "echo", {"x": "no"})]}, {"text": "ok"}])
    run(engine.run_turn(engine.Spec(kind="owner", model="deepseek/x", tools=[echo()], gate=gate), MemorySession(), user("go"), on_event=seen.append))
    ends = [e for e in seen if e["type"] == "tool_end"]
    assert [(e["status"], e["ok"]) for e in ends] == [("pending", True), ("denied", False)]
    seen.clear()
    fake([{"calls": [("c1", "echo", {"x": "a"})]}, {"text": "ok"}])
    run(engine.run_turn(engine.Spec(kind="owner", model="deepseek/x", tools=[echo(delay=3)]), MemorySession(), user("go"), on_event=seen.append))
    assert [e["status"] for e in seen if e["type"] == "tool_end"] == ["timeout"]
    seen.clear()
    fake([{"calls": [("c1", "echo", {"x": "a"})]}, {"text": "ok"}])
    run(engine.run_turn(engine.Spec(kind="owner", model="deepseek/x", tools=[echo(fail=True)]), MemorySession(), user("go"), on_event=seen.append))
    assert [e["status"] for e in seen if e["type"] == "tool_end"] == ["error"]


def test_a_broken_listener_does_not_break_the_turn(fake):
    fake([{"chunks": ["a", "b"]}])

    def boom(ev):
        raise RuntimeError("ui bug")
    res = run(engine.run_turn(engine.Spec(kind="owner", model="deepseek/x"), MemorySession(), user("hi"), on_event=boom))
    assert res.status == "done" and res.text == "ab"


def test_steer_cap_and_no_steer_after_stop():
    c = engine.Control()
    assert all(c.steer(user(str(i))) for i in range(loop.STEER_CAP)) and not c.steer(user("one too many"))
    c2 = engine.Control()
    c2.abort()
    assert not c2.can_steer() and not c2.steer(user("too late"))


def test_abort_interrupts_preparation(fake):
    """还在备料、压缩时点停：马上停，这句话照样进会话，不调模型。"""
    f = fake([{"text": "never"}])

    async def slow_prepare(tid, prompts):
        await asyncio.sleep(5)
        return prompts

    async def go():
        c = engine.Control()
        task = asyncio.create_task(engine.run_turn(engine.Spec(kind="owner", model="deepseek/x", prepare=slow_prepare, conversation="t-prep"),
                                                   MemorySession(), user("hi"), control=c))
        await asyncio.sleep(0.05)
        t0 = time.monotonic()
        c.abort()
        res = await task
        return res, time.monotonic() - t0
    res, took = run(go())
    assert res.status == "aborted" and took < 1 and [text_of(m) for m in res.messages] == ["hi"] and f.requests == []
    c = engine.Control()  # 还没开始就停了：备料不跑
    c.abort()
    res = run(engine.run_turn(engine.Spec(kind="owner", model="deepseek/x", prepare=slow_prepare), MemorySession(), user("hi"), control=c))
    assert res.status == "aborted" and [m["role"] for m in res.messages] == ["user"]


# —— app 这一层：进度 ——————————————————————————————————————————————————————

def test_progress_has_the_same_shape_as_openclaw(server_json, fake, bridge, monkeypatch):
    import chat
    server_json(BUILTIN)
    monkeypatch.setattr(chat, "PROGRESS_EVERY", 0)
    bridge.delays["schedule"] = 0.2
    fake([{"thinking": "Let me look at the schedule first.", "calls": [("c1", "schedule", {"args": ["day"]})]},
          {"thinking": "Now the journal.", "calls": [("c2", "journal", {"args": ["list"]})]},
          {"text": "Two things today."}])

    async def go():
        r = chat.start_run("t-prog", "what's on today?", None)
        first = asyncio.create_task(collect(chat.attach(r)))
        await until(lambda: running_step(r))
        later = await collect(chat.attach(r), until_event="progress")  # 断线重连：先补发现在的进度
        return r, await first, later
    r, evs, later = run(go())
    progress = [d for e, d in evs if e == "progress"]
    assert progress and all(set(p) == {"since", "phase", "steps", "thought"} for p in progress)
    assert all(set(s) == {"id", "tool", "detail", "status"} for p in progress for s in p["steps"])
    phases = [p["phase"] for p in progress]
    assert phases[0] == "preparing_context" and {"waiting_for_model", "thinking", "tool"} <= set(phases)
    last = progress[-1]
    assert [(s["id"], s["tool"], s["detail"], s["status"]) for s in last["steps"]] == [("c1", "schedule", "schedule day", "done"),
                                                                                      ("c2", "journal", "journal list", "done")]
    assert "Let me look at the schedule first." in [p["thought"] for p in progress]
    assert last["since"] == round(r.t0, 1)
    assert later[-1][0] == "progress" and later[-1][1]["steps"][0]["status"] == "running"  # 重连时补发的是正在跑的那一步
    assert [e for e, _ in later][:2] == ["start", "progress"]
    assert r.status == "ok" and r.text == "Two things today."
    # 和对话通道（note_progress）画出来的是同一个形状
    ref = chat.Run(thread="x", model="m", user_id=0, started=chat.now_iso())
    chat.note_progress(ref, {"stream": "item", "data": {"kind": "tool", "itemId": "i1", "name": "exec", "meta": "x", "phase": "start"}})
    assert set(ref.progress) == set(last) and set(ref.progress["steps"][0]) == set(last["steps"][0])


def test_thinking_progress_is_throttled_and_keeps_the_tail(server_json, monkeypatch):
    import chat
    from engine import providers
    server_json(BUILTIN)

    async def stream(ref, req):
        for i in range(60):
            await asyncio.sleep(0.01)
            yield {"type": "thinking", "text": f"step {i}. " + "x" * 20}
        yield {"type": "text", "text": "done"}
        yield {"type": "done", "message": {"role": "assistant", "content": [{"type": "text", "text": "done"}], "api": "openai",
                                           "provider": "deepseek", "model": "deepseek-flash", "stop": "end", "usage": {}}}
    monkeypatch.setattr(providers, "stream", stream)

    async def go():
        r = chat.start_run("t-think", "think hard", None)
        return await collect(chat.attach(r))
    evs = run(go())
    thoughts = [d["thought"] for e, d in evs if e == "progress" and d["thought"]]
    assert 1 <= len(thoughts) <= 8  # 60 段思考约 0.7 秒：最多 0.4 秒推一次
    assert all(len(t) <= chat.PROGRESS_THOUGHT for t in thoughts) and thoughts[-1].rstrip("x").endswith(". ")


def test_waiting_for_approval_shows_as_a_done_step(server_json, fake, bridge, monkeypatch):
    import chat
    server_json(BUILTIN)
    monkeypatch.setattr(chat, "PROGRESS_EVERY", 0)
    fake([{"calls": [("c1", "journal", {"args": ["delete", "j-evt-1"]})]}, {"text": "It's waiting for your OK."}])

    async def go():
        r = chat.start_run("t-wait", "delete that entry", None)
        return await collect(chat.attach(r))
    evs = run(go())
    step = [d for e, d in evs if e == "progress"][-1]["steps"][0]
    assert step["status"] == "done" and step["detail"] == "journal delete · waiting for your OK" and bridge.calls == []


# —— 插话 ————————————————————————————————————————————————————————————————

def test_steer_joins_the_running_turn(server_json, fake, bridge):
    import chat
    server_json(BUILTIN)
    bridge.delays["schedule"] = 0.4
    f = fake([{"text": "Checking.", "calls": [("c1", "schedule", {"args": ["day"]})]}, {"text": "Done, and noted the dentist."}])

    async def go():
        r = chat.start_run("t-steer", "check my day", None)
        first = asyncio.create_task(collect(chat.attach(r)))
        await until(lambda: running_step(r))
        resp = await chat.send(chat.SendBody(thread="t-steer", text="also the dentist at 3"))
        second = await collect(resp.body_iterator)
        return r, await first, second
    r, first, second = run(go())
    kind, data = second[0]
    assert kind == "queued" and data["steer"] is True
    sid = int(data["userId"][2:])
    done1, done2 = dict(first)["done"], dict(second)["done"]
    assert done1["id"] == done2["id"] and done1["text"] == "Checking.\n\nDone, and noted the dentist."  # 还是同一条回复
    msgs = f.requests[1][1].messages
    assert [m["role"] for m in msgs[-3:]] == ["assistant", "tool", "user"] and text_of(msgs[-1]) == "also the dentist at 3"
    assert msgs[-1]["ref"] == sid and msg_row(chat, sid)["status"] == "steered" and len(replies(chat, "t-steer")) == 1
    assert "steer" in [s["type"] for s in events.steps(events.turns("t-steer")[0]["id"])]


def test_several_steers_land_in_the_same_batch(server_json, fake, bridge):
    import chat
    server_json(BUILTIN)
    bridge.delays["schedule"] = 0.4
    f = fake([{"calls": [("c1", "schedule", {"args": ["day"]})]}, {"text": "Got all three."}])

    async def go():
        r = chat.start_run("t-steer3", "check my day", None)
        await until(lambda: running_step(r))
        streams = [(await chat.send(chat.SendBody(thread="t-steer3", text=t))).body_iterator for t in ("one", "two", "three")]
        return [await collect(s) for s in streams]
    outs = run(go())
    assert all(o[0][0] == "queued" and o[0][1]["steer"] for o in outs)
    assert len({dict(o)["done"]["id"] for o in outs}) == 1
    assert [text_of(m) for m in f.requests[1][1].messages[-3:]] == ["one", "two", "three"] and len(f.requests) == 2


def test_steer_that_misses_the_turn_becomes_the_next_turn(server_json, fake, bridge):
    """插话进去了但这一轮没用上就结束了（这里是点了停）：不丢，回完变成下一轮，那条的连接接到下一轮。"""
    import chat
    server_json(BUILTIN)
    bridge.delays["schedule"] = 3
    f = fake([{"calls": [("c1", "schedule", {"args": ["day"]})]}, {"text": "About the dentist: noted."}])

    async def go():
        r = chat.start_run("t-miss", "check my day", None)
        first = asyncio.create_task(collect(chat.attach(r)))
        await until(lambda: running_step(r))
        resp = await chat.send(chat.SendBody(thread="t-miss", text="dentist at 3"))
        second = asyncio.create_task(collect(resp.body_iterator))
        await asyncio.sleep(0.05)
        await chat.stop(chat.StopBody(thread="t-miss"))
        return r, await first, await second
    r, first, second = run(go())
    assert r.status == "stopped" and dict(first)["done"]["text"] == "(Stopped)" and bridge.cancelled == ["schedule"]
    sid = int(second[0][1]["userId"][2:])
    done2 = dict(second)["done"]
    assert done2["text"] == "About the dentist: noted." and done2["id"] != dict(first)["done"]["id"]
    nxt = chat.RUNS["t-miss"]
    assert nxt.user_id == sid and msg_row(chat, sid)["status"] == "ok"
    msgs = f.requests[1][1].messages
    assert text_of(msgs[-1]) == "dentist at 3" and msgs[-1]["ref"] == sid and msgs[-3]["role"] == "tool"
    assert msgs[-2]["role"] == "system" and text_of(msgs[-2]) == loop.STOPPED_NOTE  # 停了的那一轮后面补的「用户停了」


def test_steer_that_cannot_get_in_is_queued(server_json, fake, bridge, monkeypatch):
    """插话已经排满（或者这一轮刚结束）：照排队处理（app 上标「排队」，回完合成一轮）。"""
    import chat
    server_json(BUILTIN)
    monkeypatch.setattr(loop, "STEER_CAP", 1)
    bridge.delays["schedule"] = 0.4
    f = fake([{"calls": [("c1", "schedule", {"args": ["day"]})]}, {"text": "first done"}, {"text": "second turn"}])

    async def go():
        r = chat.start_run("t-cap", "check my day", None)
        await until(lambda: running_step(r))
        a = (await chat.send(chat.SendBody(thread="t-cap", text="steered"))).body_iterator
        b = (await chat.send(chat.SendBody(thread="t-cap", text="queued"))).body_iterator
        return await collect(a), await collect(b)
    a, b = run(go())
    assert a[0][1]["steer"] is True and "steer" not in b[0][1]
    assert dict(a)["done"]["text"] == "first done" and dict(b)["done"]["text"] == "second turn"
    assert text_of(f.requests[1][1].messages[-1]) == "steered" and "queued" in text_of(f.requests[2][1].messages[-1])


# —— 停 ————————————————————————————————————————————————————————————————————

def test_stop_keeps_the_text_completes_the_session_and_leaves_cards_waiting(server_json, fake, bridge):
    import chat
    import inbox
    server_json(BUILTIN)
    bridge.delays["schedule"] = 5
    fake([{"text": "Asking first.", "calls": [("c1", "journal", {"args": ["delete", "j-stop-1"]}), ("c2", "schedule", {"args": ["day"]})]}])

    async def go():
        r = chat.start_run("t-stop", "clean up", None)
        # 等 schedule 真的跑起来再停（只等「有一步在跑」的话，忙的时候可能停在 journal 那一步，schedule 还没开始，就没东西可取消）
        await until(lambda: running_step(r) and any(n == "schedule" for n, _ in bridge.calls))
        t0 = time.monotonic()
        out = await chat.stop(chat.StopBody(thread="t-stop"))
        await until(lambda: r.done)
        return r, out, time.monotonic() - t0
    r, out, took = run(go())
    assert out["stopped"] and took < 2 and r.status == "stopped" and r.text == "Asking first.\n\n(Stopped)"
    assert bridge.cancelled == ["schedule"] and replies(chat, "t-stop")[-1]["status"] == "stopped"
    s = DbSession.latest(day_key("t-stop", chat.day_of(r.started)))
    msgs = s.messages()
    results = {m["id"]: text_of(m) for m in msgs if m["role"] == "tool"}
    assert set(results) == {c["id"] for m in msgs for c in tool_calls(m)} == {"c1", "c2"}  # 会话完整：每个调用都有结果
    assert "approval-pending" in results["c1"] and results["c2"] == "Stopped by the user."
    assert events.turns("t-stop")[0]["status"] == "aborted"
    with store.read() as conn:
        call = conn.execute("SELECT * FROM engine_calls WHERE call='c1' AND turn=?", (events.turns("t-stop")[0]["id"],)).fetchone()
    with chat._lock, inbox.idb() as conn:
        card = conn.execute("SELECT status FROM inbox WHERE id=?", (call["inbox"],)).fetchone()
    assert call["status"] == "pending" and card["status"] == "pending"  # 等你点头的卡照样在等


def test_stop_while_preparing_still_records_the_question(server_json, fake, bridge, monkeypatch):
    import chat
    from engine import compact
    server_json(BUILTIN)
    f = fake([{"text": "never"}])

    async def slow(*a, **k):
        await asyncio.sleep(5)
    monkeypatch.setattr(compact, "maybe", slow)

    async def go():
        r = chat.start_run("t-stop-prep", "a long question", None)
        await asyncio.sleep(0.3)
        await chat.stop(chat.StopBody(thread="t-stop-prep"))
        await until(lambda: r.done, 3)
        return r
    r = run(go())
    s = DbSession.latest(day_key("t-stop-prep", chat.day_of(r.started)))
    assert r.status == "stopped" and f.requests == [] and [text_of(m) for m in s.messages()] == ["a long question", loop.STOPPED_NOTE]


# —— 重启 ——————————————————————————————————————————————————————————————————

def test_restart_marks_the_turn_aborted_and_completes_the_session(server_json):
    import chat
    server_json(BUILTIN)
    ts = chat.now_iso()
    day = chat.day_of(ts)
    with chat._lock, chat.db() as conn:
        asked = conn.execute("INSERT INTO messages(thread, role, text, ts, status) VALUES('t-reboot','user','plan my day',?, 'ok')", (ts,)).lastrowid
        late = conn.execute("INSERT INTO messages(thread, role, text, ts, status, gw_text) VALUES('t-reboot','user','and lunch',?, 'steered', "
                            "'and lunch')", (ts,)).lastrowid
        kept = conn.execute("INSERT INTO messages(thread, role, text, ts, status, gw_text) VALUES('t-reboot','user','and gym',?, 'steered', "
                            "'and gym')", (ts,)).lastrowid
    s = DbSession.create(day_key("t-reboot", day), kind="owner", agent="main", model="deepseek/deepseek-flash", system="sys")
    tid = events.turn_open(conversation="t-reboot", kind="owner", model="deepseek/deepseek-flash", session=s.id, agent="main", origin="user")
    s.append({**user("plan my day"), "ref": asked}, turn=tid)
    s.append({"role": "assistant", "content": [{"type": "tool_call", "id": "c8", "name": "goals", "args": {"args": ["list"]}}],
              "api": "openai", "provider": "deepseek", "model": "deepseek-flash", "stop": "tool", "usage": {}}, turn=tid)
    s.append({"role": "tool", "id": "c8", "name": "goals", "content": [{"type": "text", "text": "no goals"}], "error": False}, turn=tid)
    s.append({**user("and gym"), "ref": kept}, turn=tid)  # 这一批工具做完写进会话的插话（它读到了）
    s.append({"role": "assistant", "content": [{"type": "text", "text": "Checking your calendar."},
                                               {"type": "tool_call", "id": "c9", "name": "schedule", "args": {"args": ["day"]}}],
              "api": "openai", "provider": "deepseek", "model": "deepseek-flash", "stop": "tool", "usage": {}}, turn=tid)
    events.step_open(tid, type_="tool", name="schedule", detail="schedule day", conversation="t-reboot")
    chat.resume_engine()
    t = events.turns("t-reboot")[0]
    assert t["status"] == "aborted" and t["error"] == "server restarted" and all(x["status"] == "aborted" for x in events.steps(tid))
    msgs = DbSession.get(s.id).messages()
    assert msgs[-2]["role"] == "tool" and msgs[-2]["id"] == "c9" and msgs[-2]["error"] and "restarted" in text_of(msgs[-2])
    assert msgs[-1]["role"] == "system" and "restarted" in text_of(msgs[-1])
    reply = replies(chat, "t-reboot")[-1]
    assert reply["status"] == "error" and reply["text"] == "Checking your calendar.\n\n(The server restarted; this turn didn't finish.)"
    assert msg_row(chat, late)["status"] == "queued" and msg_row(chat, kept)["status"] == "steered"  # 没读到的交给 resume_queued 接着发
    n = len(DbSession.get(s.id).messages())
    chat.resume_engine()  # 再起一次：没有在跑的了，什么都不动
    assert len(DbSession.get(s.id).messages()) == n and len(replies(chat, "t-reboot")) == 1


def test_normal_restart_cancelled_turn_gets_its_reply_once(server_json, fake, bridge):
    """正常关服务：asyncio 取消还在跑的那一轮（不是点停）——内核当场补好会话、记成 cancelled；重启后 app 那句补「没拿到回复」，只补一次。"""
    import chat
    server_json(BUILTIN)
    bridge.delays["schedule"] = 5
    fake([{"text": "Looking.", "calls": [("c1", "schedule", {"args": ["day"]}), ("c2", "goals", {"args": ["list"]})]}])

    async def go():
        r = chat.start_run("t-shutdown", "what's on", None)
        await until(lambda: running_step(r) and bridge.calls)  # 同上：等命令真的开始跑
        r.stream_task.cancel()
        await asyncio.sleep(0.1)
        return r
    r = run(go())
    assert not r.done and not replies(chat, "t-shutdown") and bridge.cancelled == ["schedule"]
    t = events.turns("t-shutdown")[0]
    assert t["status"] == "aborted" and t["error"] == "cancelled"
    s = DbSession.latest(day_key("t-shutdown", chat.day_of(r.started)))
    results = {m["id"]: text_of(m) for m in s.messages() if m["role"] == "tool"}
    assert results["c1"] == loop.CANCELLED_MID_CALL and results["c2"].startswith("Not run")  # 跑到一半的不说「没做」
    chat.RUNS.pop("t-shutdown", None)  # 重启以后内存里没有它了
    chat.resume_engine()
    reply = replies(chat, "t-shutdown")[-1]
    assert reply["status"] == "error" and reply["text"] == "Looking.\n\n(The server restarted; this turn didn't finish.)"
    after = s.messages()
    assert after[-1]["role"] == "system" and "restarted" in text_of(after[-1])
    assert events.turns("t-shutdown")[0]["error"] == events.CHECKED  # 看过了：下次启动不再看
    with chat._lock, chat.db() as conn:  # 之后把那条回复删了（只删 app 的记录）：再重启也不会又补一条
        conn.execute("DELETE FROM messages WHERE id=?", (reply["id"],))
    chat.resume_engine()
    assert not replies(chat, "t-shutdown") and len(s.messages()) == len(after)


def test_restart_without_engine_tables_touches_nothing(tmp_path, monkeypatch):
    import config
    db = tmp_path / "fresh.db"
    sqlite3.connect(db).close()
    monkeypatch.setattr(config.settings, "db", db)
    assert events.recover() == [] and not store.exists()
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0] == 0  # 没建表


# —— 卡片、后台回报：builtin 不认 OpenClaw ——————————————————————————————————————————

def fake_ledger(path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript("""CREATE TABLE task_runs (task_id TEXT, runtime TEXT, owner_key TEXT, child_session_key TEXT, run_id TEXT, label TEXT,
            task TEXT, status TEXT, created_at INTEGER, started_at INTEGER, ended_at INTEGER, tool_use_count INTEGER, last_tool_name TEXT,
            error TEXT, progress_summary TEXT, terminal_summary TEXT);
            CREATE TABLE subagent_runs (run_id TEXT, payload_json TEXT);""")
        now = int(time.time() * 1000)
        for key in ("mousse:t-cards", "agent:main:main"):
            conn.execute("INSERT INTO task_runs VALUES(?, 'subagent', ?, NULL, ?, 'Research', 'look things up', 'succeeded', ?, ?, ?, 3, "
                         "'web_search', NULL, NULL, 'found it')", (f"task-{key[-4:]}", key, f"run-{key[-4:]}", now, now, now))
            conn.execute("INSERT INTO subagent_runs VALUES(?, ?)", (f"run-{key[-4:]}", json.dumps({"requesterOrigin": {"channel": "webchat"}})))


def test_cards_and_settle_do_not_read_openclaw_under_builtin(server_json):
    import cards
    import chat
    import settle
    from engine import store
    fake_ledger(cards.LEDGER)
    with store.tx() as conn:  # 内核自己的台账（第 5 项）是空的
        conn.execute("DELETE FROM engine_tasks")
    try:
        server_json(BUILTIN)
        out = run(cards.cards(thread="t-cards"))
        assert not [c for c in out["cards"] if c["kind"] == "task"] and out["tasksAvailable"] is True  # 读的是内核自己的台账
        assert cards.quota()["today"] == 0 and run(settle.catch_up()) == 0 and run(cards.check_done()) is None
        r = chat.Run(thread="t-cards", model="m", user_id=1, started=chat.now_iso(), reply_id=99)
        r.t0 -= 60
        cards.on_run_end(r)
        with chat._lock, cards.cdb() as conn:
            assert conn.execute("SELECT COUNT(*) FROM task_links WHERE message_id=99").fetchone()[0] == 0
        run(cards.after_run(chat.Run(thread="task:task-main", model="m", user_id=1, started=chat.now_iso())))  # 不报错
        server_json({})  # OpenClaw：照旧读它的台账
        out = run(cards.cards(thread="main"))
        assert [c["id"] for c in out["cards"] if c["kind"] == "task"] == ["task-main"] and out["tasksAvailable"] is True
        assert out["cards"][-1]["thread"] == "main" and cards.quota()["today"] == 2
    finally:
        cards.LEDGER.unlink(missing_ok=True)


def test_engine_task_events_become_live_cards(server_json, fake, monkeypatch):
    """第 5 项会在派活的那一轮发 {"type": "task", "task": 一行}：当场出任务卡，记进 run.cards（重连补发）。"""
    import chat
    from engine import owner
    server_json(BUILTIN)
    row = {"task_id": "et-1", "thread": "t-task", "label": "Find flights", "task": "Look for flights.\n要交：three options", "status": "running",
           "created_at": int(time.time() * 1000), "tool_use_count": 0, "payload": {}}
    real = owner.respond

    async def respond(*a, on_event=None, **k):
        on_event({"type": "task", "task": row})
        return await real(*a, on_event=on_event, **k)
    monkeypatch.setattr(owner, "respond", respond)
    fake([{"text": "Started a search."}])

    async def go():
        r = chat.start_run("t-task", "find flights", None)
        evs = await collect(chat.attach(r))
        return r, evs
    r, evs = run(go())
    card = dict(evs)["card"]
    assert card["kind"] == "task" and card["id"] == "et-1" and card["thread"] == "t-task" and card["deliverable"] == ["three options"]
    assert r.cards["et-1"]["status"] == "进行中"


# —— 谁在回复：builtin 的 Agent 不用有工作区 ——————————————————————————————————————————

def test_agent_of_follows_the_engine_under_builtin(server_json):
    import chat
    import data
    import schedule
    with chat._lock, data.ddb() as conn:
        conn.execute("INSERT OR REPLACE INTO groups(id, name, icon, purpose, created_at) VALUES('g-evt', 'Books', 'book', 'Reading log.', ?)",
                     (chat.now_iso(),))
    server_json({})
    assert chat.agent_of("g-evt") == "main"  # OpenClaw：没有工作区的还留在 main（照旧）
    server_json(BUILTIN)
    assert chat.agent_of("g-evt") == "g-evt" and chat.agent_of("main") == "main" and chat.agent_of("p-123") == "main"
    r = chat.Run(thread="g-evt", model="m", user_id=1, started=chat.now_iso())
    chat.RUNS["g-evt"] = r
    try:
        assert schedule.run_of("g-evt") is r  # 它改的日程能挂到它这一次回复下面
    finally:
        chat.RUNS.pop("g-evt", None)


# —— OpenClaw 的回合也记一份（engine.record_openclaw，默认关）——————————————————————————————

class FakeGatewayWS:
    """对话通道的假客户端：发出去就回一串 agent / delta / final 事件（gateway_ws.client() 的样子）。"""

    def __init__(self) -> None:
        self.sent: list = []

    async def send(self, key, text, queue_mode=None, model=None):
        self.sent.append((key, text))
        return f"run-{len(self.sent)}"

    async def subscribe(self, key):
        return None

    async def events(self, rid, idle=900):
        def tool(iid, name, **d):
            return {"state": "agent", "stream": "item", "data": {"kind": "tool", "itemId": iid, "name": name, **d}}
        yield tool("i1", "read", meta="from /home/x/notes/plan.md", phase="start")
        yield tool("i1", "read", phase="end", status="completed")
        yield tool("i2", "exec", meta="run the tests", phase="start")
        yield tool("i2", "exec", phase="end", isError=True)
        yield tool("i3", "web_search", meta="flights", phase="start")
        yield {"state": "delta", "deltaText": "Done."}
        yield {"state": "final", "message": {"role": "assistant", "content": [{"type": "text", "text": "Done."}]}}


def test_openclaw_turns_are_mirrored_only_when_asked(server_json, monkeypatch):
    import chat
    import gateway_ws
    fake_ws = FakeGatewayWS()
    monkeypatch.setattr(gateway_ws, "client", lambda: fake_ws)

    async def no_gateway(key):  # 回完会去 Gateway 问实际用的模型：测试里不问
        return None
    monkeypatch.setattr(chat, "actual_model", no_gateway)

    async def one(thread: str):
        r = chat.begin(thread, "anthropic/claude-opus-5-5", 1, chat.now_iso(), "hello", "")
        await until(lambda: r.done)
        return r
    server_json({"chat": {"transport": "ws"}})  # OpenClaw，默认不记
    assert not chat.ClawRecord.wanted()
    r = run(one("t-oc-off"))
    assert r.status == "ok" and r.text == "Done." and r.record is None and events.turns("t-oc-off") == []
    assert [s["tool"] for s in r.progress["steps"]] == ["read", "exec", "web_search"]  # 进度照旧
    server_json({"chat": {"transport": "ws"}, "engine": {"record_openclaw": True}})
    assert chat.ClawRecord.wanted()
    r = run(one("t-oc-on"))
    assert r.status == "ok" and r.text == "Done."
    t = events.turns("t-oc-on")[0]
    assert t["kind"] == "claw" and t["status"] == "done" and t["model"] == "anthropic/claude-opus-5-5" and t["origin"] == "user"
    assert [(s["name"], s["detail"], s["status"]) for s in events.steps(t["id"])] == [("read", "plan.md", "ok"), ("exec", "run the tests", "error"),
                                                                                     ("web_search", "flights", "aborted")]
    server_json(BUILTIN)
    assert not chat.ClawRecord.wanted()  # 只给 OpenClaw


def test_claw_record_never_breaks_the_turn(server_json, monkeypatch):
    import chat
    from engine import events as engine_events
    server_json({"engine": {"record_openclaw": True}})
    r = chat.Run(thread="t-oc-broken", model="m", user_id=1, started=chat.now_iso())

    def boom(*a, **k):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(engine_events, "turn_open", boom)
    rec = chat.ClawRecord(r)
    rec.note({"stream": "item", "data": {"kind": "tool", "itemId": "i1", "name": "read", "phase": "start"}})
    rec.close("done")  # 开不了就什么都不记，也不抛
    assert rec.tid is None


# —— 关服务：先停内核的回合 ——————————————————————————————————————————————————————

def test_shutdown_stops_engine_turns_and_keeps_the_queue(server_json, fake, bridge, monkeypatch):
    import chat
    server_json(BUILTIN)
    monkeypatch.setattr(chat, "SHUTTING_DOWN", False)
    bridge.delays["schedule"] = 5
    fake([{"text": "Looking.", "calls": [("c1", "schedule", {"args": ["day"]})]}])

    async def go():
        r = chat.start_run("t-down", "what's on", None)
        await until(lambda: running_step(r) and bridge.calls)  # 同上：等命令真的开始跑
        q = chat.enqueue("t-down", "and then this", [], None)  # 排着的：关服务时不发
        t0 = time.monotonic()
        assert chat.stop_engine_runs() == 1
        await chat.engine_shutdown()
        return r, q, time.monotonic() - t0
    r, q, took = run(go())
    assert r.done and took < 2 and r.status == "stopped" and r.shutdown
    assert r.text == "Looking.\n\n(The server restarted; this turn stopped here.)" and bridge.cancelled == ["schedule"]
    assert msg_row(chat, q.user_id)["status"] == "queued" and chat.QUEUED.get("t-down")  # 留给下次启动的 resume_queued
    msgs = DbSession.latest(day_key("t-down", chat.day_of(r.started))).messages()
    assert msgs[-2]["role"] == "tool" and msgs[-1]["role"] == "system" and "restarted" in text_of(msgs[-1])
    chat.QUEUED.pop("t-down", None)


def test_engine_shutdown_without_turns_returns_at_once(monkeypatch):
    import chat
    monkeypatch.setattr(chat, "SHUTTING_DOWN", False)
    t0 = time.monotonic()
    run(chat.engine_shutdown())
    assert time.monotonic() - t0 < 0.1 and chat.SHUTTING_DOWN is False


def test_watch_shutdown_chains_only_uvicorns_handler(monkeypatch):
    import signal

    import chat
    import uvicorn
    monkeypatch.setattr(chat, "SHUTTING_DOWN", False)
    original = signal.getsignal(signal.SIGTERM)
    server = uvicorn.Server(uvicorn.Config(lambda scope, receive, send: None))
    seen: list = []
    monkeypatch.setattr(chat, "stop_engine_runs", lambda: seen.append("stopped") or 0)
    try:
        signal.signal(signal.SIGTERM, signal.SIG_DFL)  # 认不出（不是 uvicorn 装的）：不接

        async def plain():
            return chat.watch_shutdown()
        assert run(plain()) is False and signal.getsignal(signal.SIGTERM) is signal.SIG_DFL
        signal.signal(signal.SIGTERM, server.handle_exit)

        async def hooked():
            assert chat.watch_shutdown() is True
            signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)  # 收到信号
            await asyncio.sleep(0.01)
        run(hooked())
        assert seen == ["stopped"] and server.should_exit  # 先停内核的回合，再交给 uvicorn 自己照常关
    finally:
        signal.signal(signal.SIGTERM, original)
