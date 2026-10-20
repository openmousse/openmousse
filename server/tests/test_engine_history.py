"""第 2 批：旧会话、接着聊、分叉、切枝（engine/history.py，命令 /resume 在 engine/commands.py）。模型是假的（SizedFake、tests/fake_claude.py），
不连真的模型；数据都是编的。"""
from __future__ import annotations

import json

import pytest

from engine import commands, history, owner
from engine.session import DbSession, day_key, text_of
from test_engine_claude_code import serving
from test_engine_context import MODEL, run, say, session_of


@pytest.fixture
def machine(request):
    return request.getfixturevalue("home")


@pytest.fixture
def model(request):
    return request.getfixturevalue("fake")


@pytest.fixture
def cli(request):
    return request.getfixturevalue("runner")


@pytest.fixture
def cmds(machine):
    machine.write({"engine": {"model": MODEL, "commands": True}})
    return machine


def today() -> str:
    import chat
    return chat.day_of(chat.now_iso())


def test_sessions_are_listed_newest_first(cmds, model, cli):
    model([{"text": "A route along the coast."}, {"text": "Hi there."}])
    say("t-hist-list", "2026-10-15", "plan the fictional trip to the coast", ref=301)
    say("t-hist-list", today(), "hello again", ref=302)
    got = history.sessions_of("t-hist-list", today())
    assert [s["day"] for s in got] == [today(), "2026-10-15"]
    assert got[0]["current"] and not got[1]["current"] and got[1]["turns"] == 1
    assert got[1]["first"] == got[1]["title"] == "plan the fictional trip to the coast" and got[1]["last"] == "A route along the coast."
    assert history.sessions_of("t-hist-list:other", today()) == [] and history.day_of_key("mousse:t-hist-list:x:2026-10-15", "t-hist-list") is None


def test_resume_starts_a_new_session_carrying_the_old_one(cmds, model, cli):
    model([{"text": "A route along the coast."}, {"text": "Fine."}, {"text": "Back to the coast trip then."}])
    say("t-hist-res", "2026-10-15", "plan the fictional trip to the coast", ref=311)
    say("t-hist-res", today(), "something else", ref=312)
    listed = run(commands.handle("t-hist-res", "/resume")).text
    assert "1. 2026-10-15" in listed and "plan the fictional trip to the coast" in listed and "something else" not in listed
    before = session_of("t-hist-res", today())
    out = run(commands.handle("t-hist-res", "/resume 1"))
    assert out.ok and "2026-10-15" in out.text
    s = session_of("t-hist-res", today())
    assert s.id != before.id and s.key == before.key  # 今天的键、新的一条
    root = json.loads(s.path()[0]["data"])
    old = DbSession.latest(day_key("t-hist-res", "2026-10-15"))
    assert root["fresh"] == {"from": old.id, "reason": "resume"} and "plan the fictional trip" in root["carry"]["text"]
    assert "Where an earlier session of this chat left off" in s.system
    assert history.sessions_of("t-hist-res", today())[0]["how"] == {"reason": "resume", "from": old.id}
    assert not run(commands.handle("t-hist-res", "/resume 9")).ok
    assert not run(commands.handle("t-hist-res", "/resume 1", is_busy=True)).ok  # 换要等这一轮停


def test_fork_copies_up_to_a_message(cmds, model, cli):
    f = model([{"text": "Coast."}, {"text": "Mountains."}, {"text": "Lakes, then."}])
    d = today()
    say("t-hist-fork", d, "where should the fictional trip go", ref=321)
    say("t-hist-fork", d, "and in winter?", ref=322)
    src = session_of("t-hist-fork", d)
    out = history.fork_endpoint("t-hist-fork", history.ForkIn(message="db322"))  # 分在用户那一句上：那一句和后面的都不要，原话还回来
    assert out["from"] == src.id and out["text"] == "and in winter?"
    new = DbSession.get(out["session"])
    assert session_of("t-hist-fork", d).id == new.id and [text_of(m) for m in new.messages()] == [
        text_of(m) for m in src.messages()][:2]
    assert len(src.messages()) == 4  # 原来那条一个字不动
    say("t-hist-fork", d, "what about lakes", ref=323)
    assert [text_of(m) for m in f.requests[-1][1].messages if m["role"] == "user"][-1].endswith("what about lakes")
    assert not any("winter" in text_of(m) for m in f.requests[-1][1].messages)  # 分出来的这条没有那一句
    with pytest.raises(Exception):
        history.fork_endpoint("t-hist-fork", history.ForkIn(entry="e_nope"))


def test_branches_list_and_switch(cmds, model, cli):
    model([{"text": "Coast."}, {"text": "Mountains."}, {"text": "Lakes."}])
    d = today()
    say("t-hist-br", d, "pick a fictional destination", ref=331)
    say("t-hist-br", d, "somewhere colder", ref=332)
    assert owner.rewind("t-hist-br", d, 332)  # 撤回第二句：旧枝还在
    say("t-hist-br", d, "somewhere with lakes", ref=333)
    got = history.branches_endpoint("t-hist-br")["branches"]
    assert len(got) == 2 and sum(b["current"] for b in got) == 1
    cur = next(b for b in got if b["current"])
    other = next(b for b in got if not b["current"])
    assert cur["last"] == "Lakes." and other["last"] == "Mountains." and other["from"]
    assert history.branch_endpoint("t-hist-br", history.BranchIn(leaf=other["leaf"])) == {"ok": True}
    assert [text_of(m) for m in session_of("t-hist-br", d).messages()][-1] == "Mountains."
    with pytest.raises(Exception):
        history.branch_endpoint("t-hist-br", history.BranchIn(leaf="e_nope"))


def test_a_fork_starts_a_fresh_claude_code_session(server_json, request):
    claude = request.getfixturevalue("cc")
    server_json({"claw": {"kind": "builtin"}, "engine": {"model": MODEL, "models": [MODEL, "claude-code/opus"]}})
    claude.scenario_([[{"say": "Coast."}], [{"say": "Mountains."}], [{"say": "Lakes."}]])
    d = "2026-10-18"

    async def go():
        async with serving(claude):
            await owner.respond("t-hist-cc", d, "pick a fictional destination", "claude-code/opus", lambda t: None, ref=341)
            await owner.respond("t-hist-cc", d, "somewhere colder", "claude-code/opus", lambda t: None, ref=342)
            src = DbSession.latest(day_key("t-hist-cc", d))
            history.fork("t-hist-cc", d, src, history.entry_of_message(src, 342))
            await owner.respond("t-hist-cc", d, "somewhere with lakes", "claude-code/opus", lambda t: None, ref=343)
    run(go())
    starts = claude.records("start")
    assert "--resume" in starts[1]["argv"] and "--resume" not in starts[2]["argv"] and "--session-id" in starts[2]["argv"]
    assert not any('"cc"' in json.dumps(json.loads(r["data"])) for r in DbSession.latest(day_key("t-hist-cc", d)).path()[:2])


from test_engine_claude_code import cc  # noqa: E402,F401 — 假的 claude（上面经 request 拿）
from test_engine_context import fake, home, runner  # noqa: E402,F401 — fixture，上面经 request.getfixturevalue 拿
