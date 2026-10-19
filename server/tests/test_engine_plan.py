"""内核 ⑫b C：待办清单（engine/todo.py）和计划模式（engine/plan.py）。
清单：整张换、按 id 改、看；存在 engine_todos（一个对话一行），每次改了发 todo 事件（SSE 推给 app）；压缩以后、第二天还在会话开头；
子 Agent 管它自己的（task:<id>）。计划模式：开着时撤得回 / 撤不回的工具都被关卡挡下；propose_plan 出收件箱卡、计划也是回复里的一段；
同意 = 关掉计划模式、开一轮照做（这时能写了）；改一下把意见交回去；拒绝留在计划模式；卡被改过不认；进出的接口；server.json 给 Agent
默认开；模型自己进；当天开关了下一句话后面补一条。模型是假的（FakeModel 按剧本回放），命令行脚本换成假的跑法。"""
from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import HTTPException

import engine
from engine import owner, plan, store, tasks, todo, tools
from engine.errors import EngineError
from engine.session import DbSession, day_key, text_of
from test_engine import FakeModel
from test_engine_events import collect, until

BUILTIN = {"claw": {"kind": "builtin"}, "engine": {"model": "deepseek/deepseek-flash"}}
ITEMS = [{"id": "1", "content": "Book the venue", "status": "completed"},
         {"id": "2", "content": "Order the cake", "status": "in_progress"},
         {"id": "3", "content": "Send the invites", "status": "pending"}]
PLAN = "# Party prep\n\n1. Add a 15:00 block called Party prep\n2. Remind the user at 14:30\n\nRisks: none. Check: the block shows up in the day."


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
    import bridges
    calls: list = []

    async def fake_run(name, args, stdin, agent, *, kill_on_cancel=False):
        calls.append((name, list(args)))
        return f"ok: {name} {' '.join(args)}"
    monkeypatch.setattr(bridges, "run", fake_run)
    return calls


def run(coro):
    return asyncio.run(coro)


def ctx(conversation: str, seen: list | None = None) -> engine.ToolCtx:
    return engine.ToolCtx(turn="turn-x", kind="owner", agent="main", conversation=conversation, call_id="c1", control=engine.Control(),
                          emit=seen.append if seen is not None else None)


async def next_run(chat, thread: str, after, timeout: float = 10):
    """等这个对话里 after 之后开的那一轮跑完（收件箱卡点了以后 inbox.kick 开的）。"""
    await until(lambda: chat.RUNS.get(thread) is not None and chat.RUNS[thread] is not after and chat.RUNS[thread].done, timeout)
    return chat.RUNS[thread]


def card_of(iid: str) -> dict:
    import inbox
    return inbox.item(iid)


# —— 待办清单 ————————————————————————————————————————————————————————————————————

def test_todo_replaces_merges_and_reads_its_list(server_json):
    server_json(BUILTIN)
    t = todo.tool()
    seen: list = []
    first = run(t.run({"todos": [*ITEMS[1:], ITEMS[0]]}, ctx("t-todo", seen)))
    assert first["todos"] == [{**ITEMS[1]}, ITEMS[2], ITEMS[0]] and first["summary"] == {
        "total": 3, "pending": 1, "in_progress": 1, "completed": 1, "cancelled": 0}
    assert seen == [{"type": "todo", "items": first["todos"]}] and todo.load("t-todo") == first["todos"]
    got = run(t.run({"merge": True, "todos": [{"id": "2", "status": "completed"}, {"id": "3", "status": "in_progress"},
                                               {"id": "4", "content": "Buy  candles\n", "status": "pending"}]}, ctx("t-todo", seen)))
    assert [(x["id"], x["status"]) for x in got["todos"]] == [("2", "completed"), ("3", "in_progress"), ("1", "completed"), ("4", "pending")]
    assert got["todos"][-1]["content"] == "Buy candles" and len(seen) == 2
    assert run(t.run({}, ctx("t-todo", seen)))["todos"] == got["todos"] and len(seen) == 2  # 只看：不发事件
    run(t.run({"todos": got["todos"]}, ctx("t-todo", seen)))
    assert len(seen) == 2  # 一模一样：不算改
    with store.read() as conn:
        assert conn.execute("SELECT revision FROM engine_todos WHERE conversation='t-todo'").fetchone()[0] == 2
    for bad in ({"todos": "do stuff"}, {"todos": [{"id": "9", "status": "pending"}]}, {"merge": True, "todos": [{"content": "no id"}]}):
        with pytest.raises(engine.ToolFailure):
            run(t.run(bad, ctx("t-todo")))
    assert todo.load("t-todo") == got["todos"]  # 不像样的一次：原来的清单不动


def test_todo_normalizes_like_hermes():
    items = todo.write([], [{"id": "a", "content": "first", "status": "pending"}, {"id": "b", "content": "second", "status": "in_progress"},
                            {"id": "a", "content": "first, again", "status": "weird"}, {"content": "x" * 600}])
    assert [x["id"] for x in items] == ["b", "a", "1"]  # 在做的排到待做前面；同一个 id 留最后那次；没 id 的补一个没用过的数字
    assert items[1] == {"id": "a", "content": "first, again", "status": "pending"} and items[2]["content"].endswith(todo.CUT)
    assert len(items[2]["content"]) == todo.MAX_CHARS
    assert [x["id"] for x in todo.write([], [{"id": "1", "content": "a"}, {"content": "b"}, {"id": "2", "content": "c"}])] == ["1", "3", "2"]
    assert len(todo.write([], [{"id": str(i), "content": f"step {i}"} for i in range(150)])) == todo.MAX_ITEMS
    assert todo.lines(ITEMS) == ["- [x] 1. Book the venue (completed)", "- [>] 2. Order the cake (in_progress)", "- [ ] 3. Send the invites (pending)"]


def test_todo_updates_stream_to_the_app(server_json, fake):
    import chat
    server_json(BUILTIN)
    fake([{"calls": [("c1", "todo", {"todos": ITEMS})]}, {"calls": [("c2", "todo", {"merge": True, "todos": [{"id": "2", "status": "completed"}]})]},
          {"text": "The cake is ordered."}])

    async def go():
        r = chat.start_run("t-todo-sse", "plan the party", None)
        return r, await collect(chat.attach(r))
    r, evs = run(go())
    lists = [d["items"] for e, d in evs if e == "todo"]
    assert len(lists) == 2 and lists[0] == ITEMS and lists[1][1] == {**ITEMS[1], "status": "completed"} and r.todo == lists[1]


def test_the_list_is_in_the_background_after_compaction_and_on_the_next_day(server_json, fake):
    server_json(BUILTIN)
    thread, day = "t-todo-keep", "2026-10-20"
    f = fake([{"calls": [("c1", "todo", {"todos": ITEMS})]}, {"text": "Started on the cake."},
              EngineError("deepseek HTTP 400: prompt is too long: 1200000 tokens > 1000000 maximum", status=400),
              {"text": "<summary>\nS: planning a party for the fictional Wren family\n</summary>"},
              {"text": "Still on the cake."},
              {"text": "Morning! The cake is still open."}])
    out: list[str] = []
    run(owner.turn(thread, day, "plan the party", None, out.append))
    assert "to-do list from earlier" not in f.requests[0][1].system  # 建会话时还没有清单
    assert run(owner.turn(thread, day, "how's it going?", None, out.append)) == "Still on the cake."
    retry = f.requests[4][1].system  # 压缩时开头按那时的背景重写：带上还没做完的
    assert "Your to-do list from earlier in this conversation (still open; 1 item already done;" in retry
    assert "- [>] 2. Order the cake (in_progress)\n- [ ] 3. Send the invites (pending)" in retry and "Book the venue" not in retry
    run(owner.turn(thread, "2026-10-21", "good morning", None, out.append))  # 第二天：新的会话，开头还有
    assert "- [ ] 3. Send the invites (pending)" in f.requests[5][1].system
    run(todo.tool().run({"todos": [{**x, "status": "completed"} for x in ITEMS]}, ctx(thread)))
    assert todo.block(thread) == ""  # 都做完了：开头不再放


def test_child_agents_keep_their_own_list(server_json):
    server_json(BUILTIN)
    row = {"id": "tk_todo_1", "agent": "main", "thread": "t-todo-parent", "model": "deepseek/deepseek-flash", "depth": 1, "level": "undo",
           "tools": 0, "timeout_s": 60, "label": "x", "task": "y"}
    child = tasks.child_tools(row)
    t = next(x for x in child if x.name == "todo")
    run(t.run({"todos": [{"id": "1", "content": "Read the brief", "status": "in_progress"}]}, ctx("task:tk_todo_1")))
    assert todo.load("task:tk_todo_1")[0]["content"] == "Read the brief" and todo.load("t-todo-parent") == []
    system = tasks.child_system(row, child)
    assert "## To-do list\nYour to-do list from earlier" in system and "[>] 1. Read the brief" in system
    assert "## To-do list\nFor a task with several steps" in tasks.child_rules(row, child)


# —— 计划模式 ————————————————————————————————————————————————————————————————————

def test_plan_mode_blocks_changes_until_the_plan_is_approved(server_json, fake, bridge):
    import chat
    import inbox
    server_json(BUILTIN)
    tools.install_hooks()
    assert plan.post_mode(plan.ModeIn(conversation="t-plan", on=True)) == {"ok": True, "conversation": "t-plan", "on": True, "changed": True}
    st = plan.get_mode("t-plan")
    assert (st["on"], st["by"], st["default"]) == (True, "user", False)
    f = fake([{"text": "Let me look first.", "calls": [("c1", "schedule", {"args": ["day"]}), ("c2", "schedule", {"args": ["add", "Party prep 15:00"]}),
                                                       ("c3", "journal", {"args": ["delete", "j-plan-1"]})]},
              {"calls": [("c4", "propose_plan", {"plan": PLAN})]},
              {"text": "The plan is waiting for your OK."},
              {"calls": [("c5", "schedule", {"args": ["add", "Party prep 15:00"]})]},
              {"text": "Added the 15:00 block."}])

    async def go():
        r = chat.start_run("t-plan", "set up party prep this afternoon", None)
        evs = await collect(chat.attach(r))
        pend = json.loads(text_of(f.requests[2][1].messages[-1]))
        before = list(bridge)
        out = await inbox.act(pend["inbox"], inbox.ActIn(action="approve"))
        follow = await next_run(chat, "t-plan", r)
        return r, evs, pend, before, out, follow
    r, evs, pend, before, out, follow = run(go())
    assert plan.RULES_ON in f.requests[0][1].system  # 开着：会话开头的背景里写着规矩
    assert before == [("schedule", ["day"])]  # 只看的照跑，改的、撤不回的都没跑（撤不回的也没出卡）
    blocked = {m["id"]: text_of(m) for m in f.requests[1][1].messages if m["role"] == "tool"}
    assert blocked["c2"].startswith("Not run: plan mode is on") and blocked["c3"].startswith("Not run: plan mode is on")
    assert r.text == f"Let me look first.\n\n{PLAN}\n\nThe plan is waiting for your OK."  # 计划也是回复里的一段
    assert pend["status"] == "approval-pending" and "waiting for their OK" in pend["message"]
    card = card_of(pend["inbox"])
    assert card["kind"] == "tool" and card["title"] == "Plan: Party prep" and card["approveLabel"] == "Go ahead"
    assert card["changes"] == ["1. Add a 15:00 block called Party prep", "2. Remind the user at 14:30"] and PLAN in card["detail"]
    assert tools.call_of(pend["inbox"])["tool"] == "propose_plan"
    assert out["item"]["status"] == "done" and "run" not in out  # 钩子自己开了那一轮
    st = plan.state("t-plan")
    assert (st["on"], st["by"]) == (False, "approved") and tools.call_of(pend["inbox"])["status"] == "done"
    kick = f.requests[3][1].messages[-1]
    assert text_of(kick).startswith(chat_mark()) and "The user approved your plan" in text_of(kick) and PLAN in text_of(kick)
    assert follow.origin == "auto" and follow.text == "Added the 15:00 block."
    assert bridge[-1] == ("schedule", ["add", "Party prep 15:00"])  # 同意以后能写了
    assert not any("Plan mode (updated)" in text_of(m) for m in f.requests[3][1].messages)  # 点同意关掉的：同意那一句已经说了，不再补
    with pytest.raises(HTTPException) as e:
        run(inbox.act(pend["inbox"], inbox.ActIn(action="approve")))
    assert e.value.status_code == 409


def chat_mark() -> str:
    import inbox
    return inbox.MARK


def test_revise_sends_the_notes_back_and_decline_keeps_plan_mode(server_json, fake, bridge):
    import chat
    import inbox
    server_json(BUILTIN)
    tools.install_hooks()
    plan.set_mode("t-plan-r", True, "user")
    v2 = PLAN.replace("15:00", "16:00")
    f = fake([{"calls": [("c1", "propose_plan", {"plan": PLAN, "title": "Party prep v1"})]}, {"text": "Waiting for your OK."},
              {"calls": [("c2", "propose_plan", {"plan": v2, "title": "Party prep v2"})]}, {"text": "Revised; waiting again."}])

    async def go():
        r = chat.start_run("t-plan-r", "set up party prep", None)
        await until(lambda: r.done)
        a = json.loads(text_of(f.requests[1][1].messages[-1]))["inbox"]
        out = await inbox.act(a, inbox.ActIn(action="revise", note="start at 16:00 instead"))
        second = await next_run(chat, "t-plan-r", r)
        b = json.loads(text_of(f.requests[3][1].messages[-1]))["inbox"]
        declined = await inbox.act(b, inbox.ActIn(action="reject", note="not today"))
        await until(lambda: not plan._bg)
        return a, b, out, second, declined
    a, b, out, second, declined = run(go())
    assert out["item"]["status"] == "done" and tools.call_of(a)["status"] == "revised"
    kick = text_of(f.requests[2][1].messages[-1])
    assert "The user wants changes to your plan" in kick and "start at 16:00 instead" in kick and "Plan mode is still on" in kick
    assert second.text == f"{v2}\n\nRevised; waiting again." and plan.on("t-plan-r")
    assert declined["item"]["status"] == "rejected" and tools.call_of(b)["status"] == "rejected" and plan.on("t-plan-r")  # 拒绝：还在计划模式
    s = DbSession.latest(day_key("t-plan-r", chat.day_of(second.started)))
    last = s.messages()[-1]
    assert last["role"] == "system" and "declined your plan" in text_of(last) and "not today" in text_of(last)
    assert chat.RUNS["t-plan-r"] is second and bridge == []  # 拒绝不开新的一轮；一路都没改东西


def test_an_edited_plan_card_is_not_carried_out(server_json, bridge):
    import chat
    import inbox
    server_json(BUILTIN)
    tools.install_hooks()
    plan.set_mode("t-plan-e", True, "user")
    got = run(plan.propose_tool().run({"plan": PLAN}, ctx("t-plan-e")))
    with chat._lock, inbox.idb() as conn:
        conn.execute("UPDATE inbox SET detail='do something else entirely' WHERE id=?", (got["inbox"],))

    async def go():
        out = await inbox.act(got["inbox"], inbox.ActIn(action="approve"))
        await until(lambda: chat.RUNS.get("t-plan-e") is not None)
        await chat.stop(chat.StopBody(thread="t-plan-e"))  # 发回去说一声的那一轮：不用跑完
        return out
    out = run(go())
    assert out["item"]["status"] == "failed" and "edited" in out["item"]["result"] and plan.on("t-plan-e")
    assert run(plan.propose_tool().run({"plan": PLAN}, ctx("t-plan-e")))["inbox"] != got["inbox"]  # 卡已经不在等了：再交是新的一张


def test_the_model_enters_plan_mode_itself_and_is_not_told_twice(server_json, fake, bridge):
    import chat
    server_json(BUILTIN)
    f = fake([{"calls": [("c1", "enter_plan_mode", {"why": "the user asked for a plan first"})]},
              {"calls": [("c2", "schedule", {"args": ["add", "Party prep 15:00"]})]}, {"text": "Here's how I'd do it."},
              {"text": "Still planning."}])

    async def go():
        r = chat.start_run("t-plan-m", "give me a plan first", None)
        evs = await collect(chat.attach(r))
        r2 = chat.start_run("t-plan-m", "and the music?", None)
        await until(lambda: r2.done)
        return evs
    evs = run(go())
    assert [d for e, d in evs if e == "plan_mode"] == [{"on": True, "by": "model"}]  # SSE 推给 app
    entered = next(m for m in f.requests[1][1].messages if m["role"] == "tool")
    assert text_of(entered).startswith("Plan mode is on.") and plan.RULES_ON in text_of(entered)
    assert text_of(f.requests[2][1].messages[-1]).startswith("Not run: plan mode is on") and bridge == []
    st = plan.state("t-plan-m")
    assert (st["on"], st["by"]) == (True, "model")
    assert not any(m["role"] == "system" for m in f.requests[3][1].messages)  # 它自己进的：下一句话后面不再补
    assert run(plan.enter_tool().run({}, ctx("t-plan-m"))) == "Plan mode is already on."
    with pytest.raises(engine.ToolFailure):
        run(plan.enter_tool().run({}, ctx("cron:j-1")))


def test_turning_plan_mode_on_or_off_mid_day_is_told_to_the_model(server_json, fake):
    import chat
    server_json(BUILTIN)
    f = fake([{"text": "a"}, {"text": "b"}, {"text": "c"}])

    async def say(text: str):
        r = chat.start_run("t-plan-t", text, None)
        await until(lambda: r.done)

    async def go():
        await say("one")
        plan.post_mode(plan.ModeIn(conversation="t-plan-t", on=True))
        await say("two")
        plan.post_mode(plan.ModeIn(conversation="t-plan-t", on=False))
        await say("three")
    run(go())
    notes = [[text_of(m) for m in req.messages if m["role"] == "system"] for _, req in f.requests]
    assert plan.RULES_ON not in f.requests[0][1].system and notes[0] == []
    assert len(notes[1]) == 1 and notes[1][0].endswith(f"## Plan mode (updated)\n{plan.RULES_ON}")  # 用户开的：下一句话后面补一条
    assert len(notes[2]) == 2 and notes[2][1].endswith(f"## Plan mode (updated)\n{plan.OFF_NOTE}")
    assert f.requests[2][1].messages[-1]["role"] == "system" and text_of(f.requests[2][1].messages[-2]) == "three"


def test_server_json_turns_plan_mode_on_by_default_per_agent(server_json, monkeypatch):
    import chat
    import data
    with chat._lock, data.ddb() as conn:
        conn.execute("INSERT OR REPLACE INTO groups(id, name, icon, purpose, created_at) VALUES('g-plan', 'Builder', 'x', 'Big jobs.', ?)",
                     (chat.now_iso(),))
    server_json({**BUILTIN, "engine": {**BUILTIN["engine"], "plan": {"agents": {"g-plan": True}}}})
    assert plan.state("g-plan") == {"on": True, "by": "default", "at": None} and not plan.on("t-plan-other")
    assert plan.blocked("schedule", "g-plan") and plan.blocked("todo", "g-plan") is None and plan.blocked("ask_user", "g-plan") is None
    server_json({**BUILTIN, "engine": {**BUILTIN["engine"], "plan": {"agents": {"*": True, "g-plan": False}}}})
    assert plan.on("t-plan-other") and not plan.on("g-plan")
    for conv in ("cron:j-1", "heartbeat:main", "task:tk_1", "api:main:x", "-", ""):  # 只有 app 的对话有计划模式
        assert not plan.on(conv) and plan.blocked("schedule", conv) is None
    with pytest.raises(ValueError):
        plan.set_mode("cron:j-1", True, "user")
    for bad in (lambda: plan.post_mode(plan.ModeIn(conversation="task:tk_1", on=True)), lambda: plan.get_mode("cron:j-1")):
        with pytest.raises(HTTPException) as e:
            bad()
        assert e.value.status_code == 400
    plan.set_mode("t-plan-day", False, "user")  # 今天关掉了：今天一直关着
    assert not plan.on("t-plan-day") and plan.state("t-plan-day")["by"] == "user"
    monkeypatch.setattr(plan, "_today", lambda: "2099-01-02")  # 换天：又照默认
    assert plan.state("t-plan-day") == {"on": True, "by": "default", "at": None}
