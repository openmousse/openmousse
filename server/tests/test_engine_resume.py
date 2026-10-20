"""第 2 批：重启后被打断的那一轮接着跑一次（engine/resume.py，engine.loop.resume，接在 ../chat.py 的 resume_engine）：被杀的、
正常关服务停下的都接，只接一次、只接用户自己的、太久的不接，Claude Code 的 --resume 它的会话；不写照旧只收尾。模型、claude 都是假的。"""
from __future__ import annotations

import pytest

from engine import events, loop, resume, store
from engine.session import DbSession, day_key, text_of, user
from test_engine_claude_code import serving
from test_engine_events import BUILTIN, replies, run, until

MODEL = "deepseek/deepseek-flash"
ON = {**BUILTIN, "engine": {**BUILTIN["engine"], "loop": {"resume": True}}}


@pytest.fixture
def model(request):
    return request.getfixturevalue("fake")


@pytest.fixture
def cli(request):
    return request.getfixturevalue("bridge")


def rows(chat, thread: str) -> list[tuple]:
    with chat._lock, chat.db() as conn:
        return [(r["role"], r["status"], r["text"]) for r in conn.execute("SELECT * FROM messages WHERE thread=? ORDER BY id", (thread,))]


def interrupted(chat, thread: str, *, origin: str = "user", model: str = MODEL) -> tuple[DbSession, str]:
    """app 里问了一句、内核那一轮跑到一半（调了一个工具还没结果）进程就没了：事件表里还是 running。"""
    ts = chat.now_iso()
    with chat._lock, chat.db() as conn:
        asked = conn.execute("INSERT INTO messages(thread, role, text, ts, status) VALUES(?,?,?,?,?)",
                             (thread, "user" if origin == "user" else "auto", "book the gym at six", ts, "ok")).lastrowid
    s = DbSession.latest(day_key(thread, chat.day_of(ts))) or DbSession.create(day_key(thread, chat.day_of(ts)), kind="owner", agent="main",
                                                                               model=model, system="sys")
    tid = events.turn_open(conversation=thread, kind="owner", model=model, session=s.id, agent="main", origin=origin)
    s.append({**user("book the gym at six"), "ref": asked}, turn=tid)
    s.append({"role": "assistant", "content": [{"type": "text", "text": "Booking it."},
                                               {"type": "tool_call", "id": "c1", "name": "schedule", "args": {"args": ["add"]}}],
              "api": "openai", "provider": "deepseek", "model": "deepseek-flash", "stop": "tool", "usage": {}}, turn=tid)
    return s, tid


def settle_others(chat, server_json, monkeypatch, *threads: str) -> None:
    """别的测试留下的、还在 running 的回合先收掉（没开 resume：只收尾）；接着跑只认这里的对话（别的测试留下的「关服务停下的」回复不算）。"""
    server_json(BUILTIN)
    chat.resume_engine()
    real = chat.resume_turn
    monkeypatch.setattr(chat, "resume_turn", lambda t: real(t) if t.get("conversation") in threads else None)


def test_a_killed_turn_is_picked_up_once(server_json, model, cli, monkeypatch):
    import chat
    thread = "t-resume-kill"
    settle_others(chat, server_json, monkeypatch, thread)
    s, tid = interrupted(chat, thread)
    server_json(ON)
    f = model([{"text": "The booking went through: six o'clock."}])

    async def go():
        chat.resume_engine()
        r = chat.RUNS[thread]
        await until(lambda: r.done)
        return r
    r = run(go())
    assert r.origin == "auto" and r.text == "The booking went through: six o'clock." and len(f.requests) == 1
    msgs = f.requests[0][1].messages
    tool = next(m for m in msgs if m["role"] == "tool")
    assert tool["id"] == "c1" and text_of(tool) == loop.RESTART_NO_RESULT  # 它看得到哪个调用没结果
    assert loop.RESTART_NOTE in [text_of(m) for m in msgs if m["role"] == "system"]
    assert [text_of(m) for m in msgs if m["role"] == "user"][-1] == resume.PROMPT  # 交给模型的是「接着做」那一句，不是 app 里那行灰字
    assert rows(chat, thread) == [("user", "ok", "book the gym at six"),
                                  ("grava", "error", "Booking it.\n\n(The server restarted; this turn didn't finish.)"),
                                  ("auto", "ok", resume.shown()), ("grava", "ok", "The booking went through: six o'clock.")]
    # 接着跑的那一轮（origin auto）又被打断：不再接
    t2 = events.turn_open(conversation=thread, kind="owner", model=MODEL, session=s.id, agent="main", origin="auto")
    s.append(user(resume.PROMPT), turn=t2)
    chat.RUNS.pop(thread, None)

    async def again():
        chat.resume_engine()
        return chat.RUNS.get(thread)
    assert run(again()) is None and len(f.requests) == 1


def test_off_by_default_only_settles(server_json, model, cli, monkeypatch):
    import chat
    thread = "t-resume-off"
    settle_others(chat, server_json, monkeypatch, thread)
    interrupted(chat, thread)
    f = model([])

    async def go():
        chat.resume_engine()
        return chat.RUNS.get(thread)
    assert run(go()) is None and f.requests == [] and replies(chat, thread)[-1]["status"] == "error"


def test_a_command_answered_mid_turn_does_not_hide_the_cut_off_turn(server_json, model, cli, monkeypatch):
    """回复在跑的时候插了一句、又打了 /btw（命令的回答也是 role grava、是这个对话最后一条）：重启后照样认出那一轮没回完，
    补「没拿到回复」，插话改成排着的。"""
    import chat
    thread = "t-resume-cmd"
    settle_others(chat, server_json, monkeypatch, thread)
    interrupted(chat, thread)
    ts = chat.now_iso()
    with chat._lock, chat.db() as conn:
        late = conn.execute("INSERT INTO messages(thread, role, text, ts, status) VALUES(?,?,?,?,?)",
                            (thread, "user", "and the sauna", ts, "steered")).lastrowid
        conn.execute("INSERT INTO messages(thread, role, text, ts, status, origin) VALUES(?,?,?,?,?,?)",
                     (thread, "user", "/btw is the pool open", ts, "ok", "command"))
        conn.execute("INSERT INTO messages(thread, role, text, ts, status, origin) VALUES(?,?,?,?,?,?)",
                     (thread, "grava", "(Side question) Until nine.", ts, "ok", "btw"))
    model([])
    run(_settle(chat))
    assert replies(chat, thread)[-1]["status"] == "error" and "(The server restarted" in replies(chat, thread)[-1]["text"]
    assert _status(chat, late) == "queued"


async def _settle(chat) -> None:
    chat.resume_engine()


def _status(chat, mid: int) -> str:
    with chat._lock, chat.db() as conn:
        return conn.execute("SELECT status FROM messages WHERE id=?", (mid,)).fetchone()[0]


def test_scheduled_and_old_turns_are_not_picked_up(server_json, model, cli, monkeypatch):
    import chat
    settle_others(chat, server_json, monkeypatch, "t-resume-auto", "t-resume-old")
    interrupted(chat, "t-resume-auto", origin="auto")  # 定时任务、心跳的回合归它们自己
    _s, tid = interrupted(chat, "t-resume-old")
    with store.tx() as conn:
        conn.execute("UPDATE engine_turns SET started_at='2026-01-02T03:04:05+00:00' WHERE id=?", (tid,))
    server_json(ON)
    f = model([])

    async def go():
        chat.resume_engine()
        return chat.RUNS.get("t-resume-auto"), chat.RUNS.get("t-resume-old")
    assert run(go()) == (None, None) and f.requests == []
    assert replies(chat, "t-resume-old")[-1]["status"] == "error"  # 照旧补了「没拿到回复」


def test_a_turn_stopped_by_a_normal_shutdown_is_picked_up(server_json, model, cli, monkeypatch):
    import chat
    thread = "t-resume-shutdown"
    settle_others(chat, server_json, monkeypatch, thread)
    cli.delays["schedule"] = 5
    model([{"text": "Booking it.", "calls": [("c1", "schedule", {"args": ["add"]})]}])

    async def stop_for_restart():
        r = chat.start_run(thread, "book the gym at six", None)
        await until(lambda: cli.calls)
        r.stopping = r.shutdown = True  # 关服务时 stop_engine_runs 对每一轮做的：像点了停那样停下（这里只停这一轮）
        r.control.abort()
        await until(lambda: r.done)
        return r
    r = run(stop_for_restart())
    assert r.status == "stopped" and r.text.endswith("(The server restarted; this turn stopped here.)")
    chat.RUNS.pop(thread, None)
    server_json(ON)
    f = model([{"text": "Booked for six."}])

    async def go():
        chat.resume_engine()
        nxt = chat.RUNS[thread]
        await until(lambda: nxt.done)
        chat.resume_engine()  # 再起一次：最后一条已经不是那条了，不再接
        return nxt, chat.RUNS[thread]
    nxt, after = run(go())
    assert nxt.origin == "auto" and nxt.text == "Booked for six." and after is nxt and len(f.requests) == 1
    msgs = f.requests[0][1].messages
    assert loop.RESTART_NOTE in [text_of(m) for m in msgs if m["role"] == "system"]
    assert [text_of(m) for m in msgs if m["role"] == "user"][-1] == resume.PROMPT


def test_claude_code_resumes_its_own_session(server_json, request, cli, monkeypatch):
    import chat
    claude = request.getfixturevalue("cc")
    settle_others(chat, server_json, monkeypatch, "t-resume-cc")
    server_json({**ON, "engine": {**ON["engine"], "models": [MODEL, "claude-code/opus"]}})
    thread = "t-resume-cc"
    chat.set_model(chat.ModelBody(thread=thread, model="claude-code/opus"))
    claude.scenario_([[{"say": "Noted."}], [{"say": "Picked it back up: booked for six."}]])

    async def go():
        async with serving(claude):
            first = chat.start_run(thread, "remember the gym", None)
            await until(lambda: first.done, 20)
            chat.RUNS.pop(thread, None)  # 重启以后内存里没有它了
            interrupted(chat, thread, model="claude-code/opus")  # 第二轮跑到一半进程没了
            chat.resume_engine()
            r = chat.RUNS[thread]
            await until(lambda: r.done, 20)
            return first, r
    first, r = run(go())
    assert first.text == "Noted." and r.origin == "auto" and r.text == "Picked it back up: booked for six."
    starts = claude.records("start")
    sid = starts[0]["argv"][starts[0]["argv"].index("--session-id") + 1]
    assert starts[-1]["argv"][starts[-1]["argv"].index("--resume") + 1] == sid  # 接着它自己的会话


from test_engine_claude_code import cc  # noqa: E402,F401 — 假的 claude（上面经 request 拿）
from test_engine_events import bridge, fake  # noqa: E402,F401 — 假模型、假命令行（上面经 request 拿）
