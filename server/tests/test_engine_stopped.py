"""10/19 彩排补的第 6a 件：用户点了「停」的那一轮，会话里补一句「用户停了」（engine/loop.note_stopped，chat.engine_stream 调），
下一轮、压缩摘要（compact.serialize / RULES）、第二天的「上次聊到哪」（context.tail_part）都知道那件事是用户叫停的，不是没做完的待办。
关服务停的照旧只补「重启打断了」；定时任务的看门狗停的什么都不补。模型是假的（FakeModel / tests/fake_claude.py），不连真的模型。"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

import bridges
from engine import compact, context, loop, owner, providers
from engine.session import DbSession, day_key, system_note, text_of, user
from stored_values import ASSISTANT_ROLE
from test_engine import FakeModel

BUILTIN = {"claw": {"kind": "builtin"}, "engine": {"model": "deepseek/deepseek-flash"}}


def run(coro):
    return asyncio.run(coro)


async def until(cond, timeout: float = 5.0) -> None:
    t0 = time.monotonic()
    while not cond():
        if time.monotonic() - t0 > timeout:
            raise AssertionError("timed out waiting")
        await asyncio.sleep(0.01)


@pytest.fixture
def fake(monkeypatch):
    def make(script):
        f = FakeModel(script)
        monkeypatch.setattr(providers, "stream", f.stream)
        return f
    return make


@pytest.fixture
def slow_tools(monkeypatch):
    """假的命令行：每个调用等 delay 秒（点停时被取消）。"""
    b = SimpleNamespace(delay=5.0)

    async def fake_run(name, args, stdin, agent, *, kill_on_cancel=False):
        await asyncio.sleep(b.delay)
        return f"ok: {name}"
    monkeypatch.setattr(bridges, "run", fake_run)
    return b


def session_of(thread: str, day: str) -> DbSession:
    s = DbSession.latest(day_key(thread, day))
    assert s is not None
    return s


def notes(s: DbSession) -> list[dict]:
    return [m for m in s.messages() if m["role"] == "system"]


def test_a_stopped_turn_is_marked_and_the_next_turn_sees_it(server_json, fake, slow_tools):
    import chat
    server_json(BUILTIN)
    f = fake([{"text": "Booking the dentist.", "calls": [("c1", "schedule", {"args": ["day"]})]}, {"text": "Hi again."}])

    async def go():
        r = chat.start_run("t-stopped", "book the dentist for Friday", None)
        await until(lambda: r.progress and any(s.get("status") == "running" for s in r.progress.get("steps") or []))
        await chat.stop(chat.StopBody(thread="t-stopped"))
        await until(lambda: r.done)
        r2 = chat.start_run("t-stopped", "what's the weather like", None)
        await until(lambda: r2.done)
        return r, r2
    r, r2 = run(go())
    assert r.status == "stopped" and r2.status == "ok" and r2.text == "Hi again."
    s = session_of("t-stopped", chat.day_of(r.started))
    (note,) = notes(s)
    assert note.get("stopped") and text_of(note) == loop.STOPPED_NOTE
    sent = f.requests[1][1].messages  # 下一轮发出去的：停了的那一轮、「用户停了」、新的一句
    assert [m["role"] for m in sent[-3:]] == ["tool", "system", "user"] and text_of(sent[-2]) == loop.STOPPED_NOTE
    assert "dropped, not as unfinished work" in loop.STOPPED_NOTE


def test_only_the_users_stop_adds_the_note(server_json, fake, slow_tools, monkeypatch):
    """关服务停的只补「重启打断了」；定时任务（chat 模式）的看门狗停的什么都不补：那都不是用户叫停的。"""
    import chat
    from engine import unattended
    server_json(BUILTIN)
    monkeypatch.setattr(chat, "SHUTTING_DOWN", False)
    fake([{"text": "Looking.", "calls": [("c1", "schedule", {"args": ["day"]})]},
          {"text": "Checking.", "calls": [("c2", "schedule", {"args": ["day"]})]}])

    async def go():
        r = chat.start_run("t-down-note", "what's on", None)
        await until(lambda: r.progress and any(s.get("status") == "running" for s in r.progress.get("steps") or []))
        chat.stop_engine_runs()
        await chat.engine_shutdown()
        monkeypatch.setattr(chat, "SHUTTING_DOWN", False)
        job = await unattended.in_chat("t-dog", "Check", "check the calendar", level="none", idle=0.5)
        return r, job
    r, job = run(go())
    assert r.status == "stopped" and r.shutdown and job.status == "timeout"
    down = notes(session_of("t-down-note", chat.day_of(r.started)))
    assert len(down) == 1 and "restarted" in text_of(down[0]) and not down[0].get("stopped")
    dog = DbSession.latest(day_key("t-dog", chat.day_of(chat.now_iso())))
    assert dog is not None and notes(dog) == []


def test_claude_code_sees_the_stop_in_front_of_the_next_message(request):
    """Claude Code 跑的主人回合：它自己的会话不知道我们补了什么，下一句前面的「这期间发生了什么」里带着这句。"""
    import chat
    fake_cc = request.getfixturevalue("cc")
    from test_engine_claude_code import finish, serving, text_of_cc
    fake_cc.scenario_([[{"say": "Drafting the plan."}, {"sleep": 8}, {"say": "never shown"}], [{"say": "Sure."}]])

    async def go():
        async with serving(fake_cc):
            r = chat.start_run("t-cc-stopped", "draft a week of meals", "claude-code/opus")
            await until(lambda: "Drafting the plan." in r.text, 8)
            await chat.stop(chat.StopBody(thread="t-cc-stopped"))
            await finish(chat, "t-cc-stopped", 8)
            return await owner.respond("t-cc-stopped", chat.day_of(r.started), "something else", "claude-code/opus", lambda t: None)
    res = run(go())
    assert res.status == "done" and res.text == "Sure."
    second = text_of_cc(fake_cc.records("stdin")[-1])
    assert "since your last reply" in second and loop.STOPPED_NOTE in second and second.rstrip().endswith("something else")


def test_the_compaction_summary_does_not_count_a_stopped_request_as_open():
    msgs = [user("book the dentist for Friday"), {"role": "assistant", "content": [{"type": "text", "text": "Booking"}], "stop": "aborted"},
            {**system_note(loop.STOPPED_NOTE), "stopped": True}, system_note("(an ordinary note)"), user("log my oats")]
    lines = compact.serialize(msgs)
    assert lines == ["[User]: book the dentist for Friday", "[Assistant]: Booking (cut off: the user stopped it)", compact.STOPPED_MARK,
                     "[System note]: (an ordinary note)", "[User]: log my oats"]
    prompt = compact.prompt_for(lines, None, False, "2026-10-19")
    assert f"ends with {compact.STOPPED_MARK} was called off by the user" in prompt and "never count it as open" in prompt


@pytest.mark.parametrize("tail, said", [
    ("(Stopped)", "The user stopped your last reply there"),
    ("（停了）", "The user stopped your last reply there"),
    ("(The server restarted; this turn stopped here.)", "Your last reply didn't finish."),
])
def test_the_next_day_knows_the_last_reply_was_stopped_by_the_user(server_json, tail, said):
    import chat
    server_json(BUILTIN)
    thread = f"t-tail-{len(tail)}"
    with chat._lock, chat.db() as conn:
        for role, text, status in (("user", "plan a trip to the coast", "ok"), (ASSISTANT_ROLE, f"Looking at trains.\n\n{tail}", "stopped")):
            conn.execute("INSERT INTO messages(thread, role, text, ts, status) VALUES(?,?,?,?,?)", (thread, role, text, "2026-10-18T20:00:00+00:00",
                                                                                                status))
    lines = context.tail_part(thread, "2026-10-19").text.splitlines()
    assert lines[0] == "User: plan a trip to the coast" and said in lines[-1]  # 最后一行：用户叫停的不是没做完的；重启打断的照旧


from test_engine_claude_code import cc  # noqa: E402,F401 — fixture（假的 claude + /mcp），上面经 request.getfixturevalue 拿
