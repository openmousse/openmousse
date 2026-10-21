"""第 2 批：/btw 旁问（engine/side.py，命令在 engine/commands.py）：拿这个对话当背景、零工具、不进会话、不打断正在跑的那一轮；
Claude Code 跑的对话交给它一次性地跑。模型是假的（FakeModel、tests/fake_claude.py），不连真的模型、不花钱。数据都是编的。"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import bridges
from engine import commands, events, providers, side
from engine.session import DbSession, day_key, text_of, user
from stored_values import ASSISTANT_ROLE
from test_engine import FakeModel
from test_engine_commands import rows, say, until

MODEL = "deepseek/deepseek-flash"
ON = {"claw": {"kind": "builtin"}, "engine": {"model": MODEL, "commands": True}}


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def fake(monkeypatch):
    def make(script):
        f = FakeModel(script)
        monkeypatch.setattr(providers, "stream", f.stream)
        return f
    return make


def today() -> str:
    import chat
    return chat.day_of(chat.now_iso())


def seeded(thread: str) -> DbSession:
    """今天这个对话已经聊了一轮（用了一次工具）。"""
    s = DbSession.create(day_key(thread, today()), kind="owner", agent="main", model=MODEL, system="You are a helpful assistant.")
    s.append(user("Let's plan the fictional garden party for Saturday."))
    s.append({"role": "assistant", "content": [{"type": "tool_call", "id": "c1", "name": "schedule", "args": {"args": ["day"]}}]})
    s.append({"role": "tool", "id": "c1", "name": "schedule", "content": [{"type": "text", "text": "Saturday is free."}], "error": False})
    s.append({"role": "assistant", "content": [{"type": "text", "text": "Saturday is free; we picked the blue tent."}]})
    return s


def test_transcript_keeps_the_newest_and_names_tool_calls():
    msgs = [user(f"question {i} " + "x" * 300) for i in range(200)] + [
        {"role": "assistant", "content": [{"type": "tool_call", "id": "c1", "name": "web_fetch", "args": {}}]},
        {"role": "tool", "id": "c1", "name": "web_fetch", "content": [{"type": "text", "text": "y" * 5000}], "error": False},
        {"role": "system", "content": [{"type": "text", "text": "rules changed"}]}]
    out = side.transcript(msgs, summary="We talked about tents.")
    assert out.startswith("[...older conversation omitted...]") and len(out) <= side.BUDGET + 40
    assert "question 199" in out and "question 0 " not in out and "EARLIER (summary)" not in out  # 放不下的旧的（连摘要）先丢
    assert "ASSISTANT [called tools: web_fetch]" in out and "TOOL RESULT: " + "y" * side.PER_MESSAGE in out
    assert "y" * (side.PER_MESSAGE + 1) not in out  # 一条最多 PER_MESSAGE 字
    assert "rules changed" not in out  # 开头、补的规矩不带
    short = side.transcript([user("hi")], summary="We talked about tents.", carry="Yesterday: the fair.")
    assert short.splitlines() == ["EARLIER (carried over from a previous session): Yesterday: the fair.",
                                  "EARLIER (summary): We talked about tents.", "USER: hi"]
    assert side.transcript([]) == "(no prior conversation)"


def test_btw_answers_from_the_chat_with_no_tools_and_stays_out_of_the_session(server_json, fake):
    server_json(ON)
    thread = "t-btw"
    s = seeded(thread)
    before = [e["id"] for e in s.path()]
    f = fake([{"text": "The blue one."}])
    evs = run(say(thread, "/btw which tent did we pick?"))
    start, done = evs[0][1], evs[-1][1]
    assert start["command"] is True and done["text"] == "(Side question) The blue one."
    (ref, req), = f.requests
    assert ref == MODEL and req.tools == [] and req.max_tokens is None
    assert req.system.startswith(side.INSTRUCTIONS) and "You have no tools" in req.system
    asked = text_of(req.messages[-1])
    assert "USER: Let's plan the fictional garden party" in asked and "ASSISTANT [called tools: schedule]" in asked
    assert "TOOL RESULT: Saturday is free." in asked and asked.rstrip().endswith("Side question: which tent did we pick?")
    assert [e["id"] for e in DbSession.latest(day_key(thread, today())).path()] == before  # 会话一条都没多
    got = rows(thread)
    assert [(r["role"], r["origin"]) for r in got] == [("user", "command"), (ASSISTANT_ROLE, "btw")]
    assert got[1]["text"] == "(Side question) The blue one."  # app 里看得出是旁问
    t = next(t for t in events.turns(thread, 20) if t["model"] == MODEL)
    assert t["session"] is None and t["origin"] == "btw" and t["kind"] == "outsider"  # 不落会话；用量记在这个对话名下


def test_btw_does_not_interrupt_a_running_reply(server_json, fake, monkeypatch):
    import chat
    server_json(ON)
    thread = "t-btw-busy"
    gate = SimpleNamespace(ev=None)

    async def slow_tool(name, args, stdin, agent, *, kill_on_cancel=False):
        await gate.ev.wait()
        return "Saturday is free."
    monkeypatch.setattr(bridges, "run", slow_tool)
    f = fake([{"text": "Checking.", "calls": [("c1", "schedule", {"args": ["day"]})]}, {"text": "We're still checking the calendar."},
              {"text": "Saturday works."}])

    async def go():
        gate.ev = asyncio.Event()
        r = chat.start_run(thread, "is Saturday free?", None)
        await until(lambda: r.progress and any(st.get("status") == "running" for st in r.progress.get("steps") or []))
        side_q = await say(thread, "/btw what are you doing right now?")
        still = not r.done
        gate.ev.set()
        await until(lambda: r.done)
        return r, side_q, still
    r, side_q, still = run(go())
    assert still and side_q[-1][1]["text"] == "(Side question) We're still checking the calendar."
    assert r.status == "ok" and r.text.endswith("Saturday works.")
    assert f.requests[1][1].tools == [] and "is Saturday free?" in text_of(f.requests[1][1].messages[-1])  # 正在跑的那一轮也算背景
    last = f.requests[2][1]  # 这一轮接着问模型的那一次：没有旁问的问答
    assert not any("what are you doing" in text_of(m) or "still checking" in text_of(m) for m in last.messages)
    later = [m for m in DbSession.latest(day_key(thread, today())).view().messages]
    assert not any("/btw" in text_of(m) or "still checking" in text_of(m) for m in later)


def test_btw_needs_a_question_and_reports_failures(server_json, fake):
    server_json(ON)
    out = run(commands.handle("t-btw-empty", "/btw"))
    assert not out.ok and "/btw" in out.text and out.origin == "command"
    from engine.errors import EngineError
    fake([EngineError("upstream said no to key sk-abcdefghijklmnop", kind="http")])
    bad = run(commands.handle("t-btw-fail", "/side what now?"))  # 别名
    assert not bad.ok and bad.origin == "btw" and bad.text.startswith("(Side question) It didn't go through")
    assert "sk-abcdefghijklmnop" not in bad.text


def test_btw_on_claude_code_runs_it_once_with_no_tools(server_json, request):
    claude = request.getfixturevalue("cc")
    server_json({**ON, "engine": {**ON["engine"], "models": [MODEL]}})
    model = claude.fake_model([])  # 按量的一次都不该调
    claude.scenario_([[{"say": "You picked the blue tent."}]])
    thread = "t-btw-cc"
    s = seeded(thread)
    before = [e["id"] for e in s.path()]
    out = run(commands.handle(thread, "/btw which tent?", model="claude-code/sonnet"))
    assert out.ok and out.text == "(Side question) You picked the blue tent." and out.origin == "btw" and model.requests == []
    argv = claude.records("start")[-1]["argv"]
    assert argv[argv.index("--tools") + 1] == "" and "--resume" not in argv and "--no-session-persistence" in argv
    assert argv[argv.index("--model") + 1] == "sonnet"
    assert claude.records("start")[-1]["system"].startswith(side.INSTRUCTIONS)
    assert [e["id"] for e in DbSession.latest(day_key(thread, today())).path()] == before


from test_engine_claude_code import cc  # noqa: E402,F401 — 假的 claude（上面经 request 拿）
