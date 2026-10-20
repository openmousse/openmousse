"""第 2 批：手动压缩 /compact [#消息号] [关注点]（owner.compact_now → compact.manual）、POST /api/engine/compact（从这条往前总结）、
压完以后在对话里说一句（engine.compaction.notify）、排一次写回（engine.compaction.writeback）、裁旧的工具结果（engine.compaction.prune）。
模型是假的（SizedFake：用量按请求的大小报；Claude Code 的是 tests/fake_claude.py），不连真的模型。"""
from __future__ import annotations

import asyncio
import json

import pytest

import bridges
from engine import commands, compact, owner, writeback
from engine.session import PRUNED, SUMMARY_PREFIX, DbSession, day_key, text_of
from test_engine_context import MODEL, long, run, say, session_of, summary_reply

DAY = "2026-10-14"


@pytest.fixture
def machine(request):
    """test_engine_context 的 home：主工作区、两个 Agent、档案；claw 是 builtin。"""
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
def wb(monkeypatch):
    got: list[tuple[str, str]] = []

    async def fake_run(sid, reason):
        got.append((sid, reason))
        return {}
    monkeypatch.setattr(writeback, "run_session", fake_run)
    return got


def setup(machine, **compaction) -> None:
    machine.write({"engine": {"model": MODEL, "commands": True, **({"compaction": compaction} if compaction else {})}})


async def handle(thread: str, text: str):
    out = await commands.handle(thread, text)
    await asyncio.sleep(0)  # 压完以后后台排的写回：让它先跑起来
    return out


def test_compact_with_a_focus(machine, model, cli, wb):
    import chat
    setup(machine)
    f = model([{"text": "Day one: museums."}, {"text": "Day two: the coast."}, summary_reply("S: museums, then the coast. Open request: none"),
               {"text": "Happy to help."}])
    day = chat.day_of(chat.now_iso())  # 命令按今天的会话办
    say("t-cm-focus", day, "plan day one of the Lisbon trip")
    say("t-cm-focus", day, "and day two")
    out = run(handle("t-cm-focus", "/compact the hotel budget"))
    assert out.ok and out.text.startswith("Compacted: the 1 earlier turns are now a summary")
    ask = next(r for _, r in f.requests if r.system == compact.SUMMARIZER)
    prompt = text_of(ask.messages[0])
    assert "<focus>\nthe hotel budget\n</focus>" in prompt and "plan day one" in prompt and "and day two" not in prompt  # 最近一轮留着
    comp = json.loads(next(r for r in session_of("t-cm-focus", day).path() if r["kind"] == "compaction")["data"])
    assert comp["reason"] == "manual" and comp["fresh"] is True and comp["focus"] == "the hotel budget"
    say("t-cm-focus", day, "thanks")
    sent = f.requests[-1][1].messages
    assert text_of(sent[0]).startswith(SUMMARY_PREFIX) and "S: museums" in text_of(sent[0]) and text_of(sent[-1]).endswith("thanks")
    assert wb == []  # 没开 engine.compaction.writeback：不排写回


def test_nothing_to_compact_and_no_session(machine, model, cli):
    setup(machine)
    model([{"text": "Hi."}])
    assert "no session in this chat today" in run(handle("t-cm-none", "/compact")).text
    import chat
    say("t-cm-short", chat.day_of(chat.now_iso()), "hello")
    out = run(handle("t-cm-short", "/compact"))
    assert not out.ok and "at least two exchanges" in out.text


def test_compact_everything_before_a_message(machine, model, cli, wb):
    import chat
    setup(machine, writeback=True)
    f = model([{"text": "A."}, {"text": "B."}, {"text": "C."}, summary_reply("S: the first two"), {"text": "D."}])
    day = chat.day_of(chat.now_iso())
    for i, q in enumerate(("first", "second", "third"), 301):
        say("t-cm-before", day, q, ref=i)
    out = run(commands.compact_endpoint(commands.CompactIn(thread="t-cm-before", before="db303")))
    assert out["ok"] and out["turns"] == 2 and out["text"].startswith("Compacted: the 2 earlier turns")
    prompt = text_of(next(r for _, r in f.requests if r.system == compact.SUMMARIZER).messages[0])
    assert "first" in prompt and "second" in prompt and "third" not in prompt
    s = session_of("t-cm-before", day)
    assert [m.get("ref") for m in s.messages() if m["role"] == "user"][1:] == [303]  # 摘要后面是 #303 那一轮
    assert wb == [(s.id, "compact")]  # 开了 engine.compaction.writeback、写回也开着：后台排一次
    missing = run(commands.compact_endpoint(commands.CompactIn(thread="t-cm-before", before="db999")))
    assert not missing["ok"] and missing["why"] == "not_found"


def test_no_writeback_when_writeback_itself_is_off(machine, model, cli, wb):
    machine.write({"engine": {"model": MODEL, "commands": True, "compaction": {"writeback": True}, "writeback": False}})
    model([{"text": "A."}, {"text": "B."}, summary_reply("S"), {"text": "C."}])
    say("t-cm-nowb", DAY, "first")
    say("t-cm-nowb", DAY, "second")
    assert run(owner.compact_now("t-cm-nowb", DAY, model=MODEL))["ok"] and wb == []


def test_an_automatic_compaction_leaves_a_line_in_the_chat_when_asked(request, machine, model, cli, wb):
    import chat
    request.getfixturevalue("lean")  # 开头小一点：只有 schedule 一个工具、没有技能目录
    f = model([{"text": long("alpha")}, {"text": long("bravo")}, summary_reply("S1"), {"text": "ok"}])
    day = chat.day_of(chat.now_iso())
    say("t-cm-note", day, "q1 " + long("one", 500))
    base = compact.tokens(f.requests[0][1].system)
    machine.write({"engine": {"model": MODEL, "compaction": {"threshold": base + 700, "keep": 300, "notify": True, "writeback": True}}})
    say("t-cm-note", day, "q2 " + long("two", 500))
    say("t-cm-note", day, "q3 " + long("three", 500))  # 这一轮开头压一次（每轮约 360 token）
    s = session_of("t-cm-note", day)
    assert any(r["kind"] == "compaction" for r in s.path())
    with chat._lock, chat.db() as conn:
        notes = [dict(r) for r in conn.execute("SELECT role, text, status, origin FROM messages WHERE thread=?", ("t-cm-note",))]
    assert notes == [{"role": "auto", "text": "The 1 earlier turns were compacted into a summary; the conversation carries on as usual.",
                      "status": "notice", "origin": "notice"}]
    assert wb == [(s.id, "compact")]
    assert owner.seed("t-cm-note", day, 10**9) == []  # 那一行灰字不抄进新会话


def test_old_tool_results_are_trimmed_in_one_batch_and_the_record_keeps_them(machine, model, monkeypatch):
    setup(machine, prune={"at": 1, "keep_turns": 1, "min_chars": 1000, "head": 100, "tail": 100, "min_reclaim": 50})
    big = "START " + "r" * 5000 + " END"

    async def fake_run(name, args, stdin, agent, *, kill_on_cancel=False):
        return big
    monkeypatch.setattr(bridges, "run", fake_run)
    f = model([{"text": "Looking.", "calls": [("c1", "schedule", {"args": ["day"]})]}, {"text": "Done."},
               {"text": "Two."}, {"text": "Three."}, {"text": "Four."}])
    for q in ("what's on", "and then", "and after that", "one more"):
        say("t-cm-prune", DAY, q)
    def tool_text(i: int) -> str:
        return next(text_of(m) for m in f.requests[i][1].messages if m["role"] == "tool")
    assert tool_text(2) == big  # 第二轮开头：那个结果还在最近一轮里，不裁
    third = tool_text(3)
    assert third.startswith("START ") and third.endswith(" END") and PRUNED.format(head=100, tail=100, n=len(big)) in third and len(third) < 400
    assert tool_text(4) == third  # 下一轮一字不差（前缀稳定，提示缓存接得上）
    s = session_of("t-cm-prune", DAY)
    assert len([r for r in s.path() if r["kind"] == "prune"]) == 1  # 一批一次
    raw = next(json.loads(r["data"]) for r in s.path() if r["kind"] == "message" and r["role"] == "tool")
    assert text_of(raw) == big  # 库里原文不动


def test_without_prune_nothing_is_trimmed(machine, model, monkeypatch):
    setup(machine)
    big = "x" * 6000

    async def fake_run(name, args, stdin, agent, *, kill_on_cancel=False):
        return big
    monkeypatch.setattr(bridges, "run", fake_run)
    f = model([{"text": "Looking.", "calls": [("c1", "schedule", {"args": ["day"]})]}, {"text": "Done."}, {"text": "Two."}, {"text": "Three."}])
    for q in ("what's on", "and then", "and after that"):
        say("t-cm-noprune", DAY, q)
    assert next(text_of(m) for m in f.requests[-1][1].messages if m["role"] == "tool") == big


def test_claude_code_opens_a_new_session_with_the_summary_after_compact(request):
    import chat
    fake_cc = request.getfixturevalue("cc")
    from test_engine_claude_code import serving
    fake_cc.scenario_([[{"say": "Booked the museum."}], [{"say": "Added the coast."}], [{"say": "<summary>S-trip: museum, coast</summary>"}],
                       [{"say": "Fresh and ready."}]])
    day = chat.day_of(chat.now_iso())

    async def go():
        async with serving(fake_cc):
            await owner.respond("t-cc-compact", day, "book the museum", "claude-code/opus", lambda t: None)
            await owner.respond("t-cc-compact", day, "add the coast", "claude-code/opus", lambda t: None)
            out = await owner.compact_now("t-cc-compact", day, model="claude-code/opus")
            res = await owner.respond("t-cc-compact", day, "what's left?", "claude-code/opus", lambda t: None)
            return out, res
    out, res = run(go())
    assert out["ok"] and res.status == "done" and res.text == "Fresh and ready."
    starts = fake_cc.records("start")
    first = starts[0]["argv"][starts[0]["argv"].index("--session-id") + 1]
    last = starts[-1]["argv"]
    assert "--resume" not in last and last[last.index("--session-id") + 1] != first  # 压完以后开新的 Claude Code 会话
    said = json.dumps(fake_cc.records("stdin")[-1], ensure_ascii=False)
    assert "S-trip: museum, coast" in said and "what's left?" in said  # 开头带压好的那份
    assert DbSession.latest(day_key("t-cc-compact", day)).view().summary == "S-trip: museum, coast"


from test_engine_claude_code import cc  # noqa: E402,F401 — fixture（假的 claude + /mcp）
from test_engine_context import fake, home, lean, runner  # noqa: E402,F401 — fixture，上面经 request.getfixturevalue 拿
