"""第 2 批：/new 和会话换新（engine/owner.py 的 start_new / _fresh、engine/reset.py：engine.reset.idle_minutes、engine.reset.at）。
新会话和原来那条同一个键（mousse:<对话>:<逻辑日>）、新的一条：键的老拆法（写回、看别的会话、记忆检索、按天的记录、内核自己的 open_day /
DbSession.latest）都照样认，下面一处一处测。模型是假的（SizedFake / tests/fake_claude.py），不连真的模型。"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from engine import commands, owner, reset, sessions, writeback
from engine.session import DbSession, day_key, text_of
from test_engine_context import MODEL, run, say, session_of

DAY = "2026-10-12"


@pytest.fixture
def machine(request):
    """test_engine_context 的 home：主工作区、两个 Agent（g-fit 有工作区，g-solo 没有）、档案；claw 是 builtin。"""
    return request.getfixturevalue("home")


@pytest.fixture
def model(request):
    """test_engine_context 的 fake：假模型，用量按请求的大小报。"""
    return request.getfixturevalue("fake")


@pytest.fixture
def cli(request):
    """test_engine_context 的 runner：假的命令行工具。"""
    return request.getfixturevalue("runner")


@pytest.fixture
def cmds(machine):
    """machine 那台假机器，开了斜杠命令。"""
    machine.write({"engine": {"model": MODEL, "commands": True}})
    return machine


@pytest.fixture
def wb(monkeypatch):
    """写回换成记一笔（不调模型）。"""
    got: list[tuple[str, str]] = []

    async def fake_run(sid, reason):
        got.append((sid, reason))
        return {}
    monkeypatch.setattr(writeback, "run_session", fake_run)
    return got


def new(thread: str, notes: str = "", day: str = DAY):
    return run(owner.start_new(thread, day, agent=owner.agent_of(thread), model=MODEL, notes=notes))


def test_new_starts_a_session_under_the_same_key_carrying_where_it_left_off(cmds, model, cli, wb):
    f = model([{"text": "Squats on Monday, rows on Thursday."}, {"text": "Got it, starting fresh."}])
    say("g-fit", "2026-10-01", "plan my week: squats and rows")
    old = session_of("g-fit", "2026-10-01")
    s, was = new("g-fit", day="2026-10-01")
    assert was is not None and was.id == old.id and s.id != old.id and s.key == old.key == day_key("g-fit", "2026-10-01")
    assert DbSession.latest(day_key("g-fit", "2026-10-01")).id == s.id  # 以后这一天说的都进新的这条
    assert "Where the previous session ended (the user started a new session with /new)" in s.system
    assert "User: plan my week: squats and rows" in s.system and "You: Squats on Monday, rows on Thursday." in s.system
    root = json.loads(s.path()[0]["data"])
    assert root["fresh"] == {"from": old.id, "reason": "new"} and root["carry"]["text"]
    say("g-fit", "2026-10-01", "what's next?")
    sent = f.requests[1][1]
    assert [m["role"] for m in sent.messages] == ["user"] and text_of(sent.messages[0]).endswith("what's next?")  # 前面的不再整段发
    assert "rows on Thursday" in sent.system
    assert wb == [(old.id, "new")]  # 原来那条在后台写回一次


def test_new_with_notes_carries_only_the_notes(cmds, model, cli, wb):
    model([{"text": "Booked."}])
    say("g-fit", "2026-10-02", "book the dentist for Friday")
    s, _ = new("g-fit", "Keep: deadline is Friday; budget 200", day="2026-10-02")
    assert "What the user asked to carry over into this new session (/new)" in s.system
    assert "Keep: deadline is Friday; budget 200" in s.system and "book the dentist" not in s.system


def test_new_via_the_command(cmds, model, cli, wb):
    import chat
    model([{"text": "Hi."}])
    out = run(commands.handle("t-new-cmd", "/new"))
    assert "next message starts a new one" in out.text  # 今天还没有会话：什么都不换
    say("t-new-cmd", chat.day_of(chat.now_iso()), "hello")
    out = run(commands.handle("t-new-cmd", "/new"))
    assert out.ok and out.text.startswith("Started a new session.")
    assert len(sessions.rows(sessions.Scope(None), conversation="t-new-cmd", days=0)) == 2
    busy = run(commands.handle("t-new-cmd", "/new", is_busy=True))
    assert not busy.ok and "/stop" in busy.text  # 改会话的要等这一轮停


def test_the_new_key_is_read_by_every_old_parser(cmds, model, cli, wb):
    """新会话的键 = 原来的键：写回的 conversation_of、看别的会话的 parse_key / rows、owner.stream 的 KEY、记忆检索按会话算的「还在上下文里」、
    内核自己按键找今天的会话（plan、unattended、tasks 都是 DbSession.latest(day_key(…))）。"""
    import chat
    from engine import recall
    model([{"text": "One."}, {"text": "Two."}])
    day = chat.day_of(chat.now_iso())
    thread = "t-new-keys"
    say(thread, day, "first thing", ref=101)
    s, old = new(thread, day=day)
    out: list[str] = []
    res = run(owner.respond(thread, day, "second thing", None, out.append, ref=102))
    assert writeback.conversation_of(s.key) == (thread, day) == writeback.conversation_of(old.key)
    assert sessions.parse_key(s.key) == (thread, day)
    assert owner.KEY.match(s.key).group(1) == thread
    assert {r["id"] for r in sessions.rows(sessions.Scope(None), conversation=thread, days=0)} == {s.id, old.id}
    assert DbSession.latest(day_key(thread, day)).id == s.id
    assert sessions.current_session(res.turn) == s.id
    assert recall._in_context(SimpleNamespace(conversation=thread, turn=res.turn)) == (thread, 102)  # /new 之前那句又能搜到了
    later = datetime.now(timezone.utc) + timedelta(hours=2)
    assert {old.id, s.id} <= {sid for sid, _why in writeback.due(later)}  # 写回按会话一条一条认，两条都认得
    assert owner.rewind(thread, day, 102) and DbSession.latest(day_key(thread, day)).id == s.id


def test_the_apps_day_history_is_unchanged_by_new(cmds, model, cli, wb):
    """app 按天的记录（/api/chat/history、/api/chat/days）只看 app 自己的消息：/new 前后的话、命令那两行都在今天。"""
    import chat
    from test_engine_commands import say as send
    model([{"text": "Hello there."}, {"text": "Fresh start."}])
    run(send("t-new-app", "hello"))
    run(send("t-new-app", "/new"))
    run(send("t-new-app", "again"))
    texts = [m["text"] for m in chat.history("t-new-app")["messages"]]
    assert texts[0] == "hello" and texts[2] == "/new" and texts[3].startswith("Started a new session.") and texts[-1] == "Fresh start."
    assert [d["count"] for d in chat.days("t-new-app")["days"]] == [6]


def test_the_carry_survives_a_rewritten_opening(cmds, model, cli, wb):
    """压缩时开头按那时的背景重写（owner.respond 的 renderer）：换新时带的那一段从会话的第一条 context 条目里读回来，照样带上。"""
    from engine import context
    model([{"text": "Noted."}])
    say("t-new-carry", DAY, "remember the blue folder")
    s, _ = new("t-new-carry")
    carry = owner.carried(s)
    assert carry and "blue folder" in carry["text"] and owner.carried(DbSession.get(s.id)) == carry
    ctx = run(context.gather("main", "t-new-carry", DAY, set()))
    assert "blue folder" not in owner.system_for("main", [], ctx)
    assert "blue folder" in owner.system_for("main", [], owner.with_carry(ctx, carry))


def test_claude_code_starts_a_new_session_after_new(request):
    import chat
    fake_cc = request.getfixturevalue("cc")
    from test_engine_claude_code import serving
    fake_cc.scenario_([[{"say": "Noted."}], [{"say": "Again."}], [{"say": "Fresh."}]])
    day = chat.day_of(chat.now_iso())

    async def go():
        async with serving(fake_cc):
            await owner.respond("t-cc-new", day, "one", "claude-code/opus", lambda t: None)
            await owner.respond("t-cc-new", day, "two", "claude-code/opus", lambda t: None)
            await owner.start_new("t-cc-new", day, agent="main", model="claude-code/opus")
            return await owner.respond("t-cc-new", day, "three", "claude-code/opus", lambda t: None)
    res = run(go())
    assert res.status == "done" and res.text == "Fresh."
    starts = fake_cc.records("start")
    first = starts[0]["argv"][starts[0]["argv"].index("--session-id") + 1]
    assert starts[1]["argv"][starts[1]["argv"].index("--resume") + 1] == first  # 同一条会话：接着它的
    third = starts[2]["argv"]
    assert "--resume" not in third and third[third.index("--session-id") + 1] != first  # /new 以后：开新的
    assert "Where the previous session ended" in starts[2]["system"] and "User: two" in starts[2]["system"]  # 开头带上刚才聊到哪


# —— 闲置换新、换天的钟点（engine.reset）————————————————————————————————————————————————————

def test_idle_reset_starts_a_new_session_on_the_next_message(machine, model, cli, wb):
    from engine import store
    machine.write({"engine": {"model": MODEL, "reset": {"idle_minutes": 60}}})
    f = model([{"text": "Sure."}, {"text": "Welcome back."}])
    say("t-new-idle", DAY, "draft the newsletter")
    old = session_of("t-new-idle", DAY)
    with store.tx() as conn:  # 两个小时没动静
        conn.execute("UPDATE engine_sessions SET updated_at=? WHERE id=?",
                     ((datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(timespec="seconds"), old.id))
    say("t-new-idle", DAY, "where were we?")
    s = session_of("t-new-idle", DAY)
    assert s.id != old.id and json.loads(s.path()[0]["data"])["fresh"] == {"from": old.id, "reason": "idle"}
    assert "it had been idle for a while" in f.requests[1][1].system and "draft the newsletter" in f.requests[1][1].system
    assert wb == [(old.id, "idle")]


def test_without_engine_reset_nothing_changes(machine, model, cli, wb):
    from engine import store
    model([{"text": "Sure."}, {"text": "Again."}])
    say("t-new-still", DAY, "draft the newsletter")
    old = session_of("t-new-still", DAY)
    with store.tx() as conn:
        conn.execute("UPDATE engine_sessions SET updated_at=? WHERE id=?",
                     ((datetime.now(timezone.utc) - timedelta(days=3)).isoformat(timespec="seconds"), old.id))
    say("t-new-still", DAY, "where were we?")
    assert session_of("t-new-still", DAY).id == old.id and wb == []
    assert reset.idle_minutes() is None and reset.day_start() == timedelta(hours=4)


def test_the_day_boundary_follows_engine_reset_at(server_json):
    import chat
    from engine import cyber, recall, used
    server_json({"claw": {"kind": "builtin"}, "engine": {"model": MODEL}})
    assert chat.day_of("2026-10-19T05:00:00+00:00") == "2026-10-19" and chat.day_bounds("2026-10-19")[0].startswith("2026-10-19T04:00")
    server_json({"claw": {"kind": "builtin"}, "engine": {"model": MODEL, "reset": {"at": "06:30"}}})
    assert chat.day_of("2026-10-19T05:00:00+00:00") == "2026-10-18" and chat.day_of("2026-10-19T06:30:00+00:00") == "2026-10-19"
    assert chat.day_bounds("2026-10-19") == ("2026-10-19T06:30:00+00:00", "2026-10-20T06:30:00+00:00")
    at5 = datetime(2026, 10, 19, 5, 0, tzinfo=timezone.utc)
    assert recall.day_of(at5) == used.day_of(at5) == cyber.logical_day(at5) == "2026-10-18"
    server_json({"claw": {"kind": "openclaw"}, "engine": {"model": MODEL, "reset": {"at": "06:30"}}})
    assert chat.day_of("2026-10-19T05:00:00+00:00") == "2026-10-19"  # OpenClaw 那条路照旧 04:00
    server_json({"claw": {"kind": "builtin"}, "engine": {"model": MODEL, "reset": {"at": "25:00"}}})
    assert reset.at() is None and chat.day_of("2026-10-19T05:00:00+00:00") == "2026-10-19"  # 写错了当没写
    server_json({"claw": {"kind": "builtin"}, "engine": {"model": MODEL, "reset": {"at": "00:00"}}})
    assert reset.day_start() == timedelta(0) and chat.day_of("2026-10-19T01:00:00+00:00") == "2026-10-19"  # 零点换天不会变回 04:00


def test_latest_session_goes_by_real_time_not_the_offset_string(server_json):
    """同一个键的两条会话：新的那条写下时时区往西换了（偏移字符串反而小），latest 照样认新的。"""
    from engine import store
    server_json({"claw": {"kind": "builtin"}, "engine": {"model": MODEL}})
    key = "t-latest-tz:2026-10-25"
    old = DbSession.create(key, kind="owner", agent="main", model=MODEL, system="sys")
    new = DbSession.create(key, kind="owner", agent="main", model=MODEL, system="sys")
    with store.tx() as conn:
        conn.execute("UPDATE engine_sessions SET created_at=? WHERE id=?", ("2026-10-25T01:45:00.000+01:00", old.id))  # 00:45 UTC
        conn.execute("UPDATE engine_sessions SET created_at=? WHERE id=?", ("2026-10-25T01:15:00.000+00:00", new.id))  # 01:15 UTC，回拨以后
    assert DbSession.latest(key).id == new.id


from test_engine_claude_code import cc  # noqa: E402,F401 — fixture（假的 claude + /mcp）
from test_engine_context import fake, home, runner  # noqa: E402,F401 — fixture，上面经 request.getfixturevalue 拿
