"""第 2 批：持续目标（engine/goal.py，命令 /goal 在 engine/commands.py，接在 ../chat.py 每一轮收尾）：判做到没有、没做到接着做、轮数上限、
做不了停下、点了停先停着、判不出来几次就停、用户的话先回。模型是假的（FakeModel：回复和判的那一次都是它），不花钱。数据都是编的。"""
from __future__ import annotations

import asyncio
import json

import pytest

from engine import commands, goal
from engine.errors import EngineError
from engine.session import text_of
from test_engine_commands import rows, say, until
from test_engine_events import running_step

MODEL = "deepseek/deepseek-flash"
ON = {"claw": {"kind": "builtin"}, "engine": {"model": MODEL, "commands": True, "goal": {"max_turns": 5}}}


def run(coro):
    return asyncio.run(coro)


def verdict(v: str, why: str = "") -> dict:
    return {"text": json.dumps({"verdict": v, "reason": why})}


async def settled(thread: str, timeout: float = 10) -> dict:
    """等这个对话的目标不在做了、也没有在跑的回复。"""
    import chat
    await until(lambda: (goal.get(thread) or {}).get("status") != "active" and not chat.busy(thread) and not goal._bg, timeout)
    return goal.get(thread) or {}


@pytest.fixture
def model(request):
    return request.getfixturevalue("fake")


@pytest.fixture
def cli(request):
    return request.getfixturevalue("tools")


def test_off_by_default(server_json):
    server_json({**ON, "engine": {"model": MODEL, "commands": True}})
    out = run(commands.handle("t-goal-off", "/goal finish the checklist"))
    assert not out.ok and "engine.goal" in out.text and goal.get("t-goal-off") is None


def test_it_keeps_going_until_the_judge_says_done(server_json, model, cli):
    server_json(ON)
    thread = "t-goal-done"
    f = model([{"text": "Step one is done."}, verdict("continue", "step two is left"), {"text": "Step two is done; all finished."},
               verdict("done", "both steps are finished")])

    async def go():
        await say(thread, "/goal finish the fictional two-step checklist")
        return await settled(thread)
    g = run(go())
    assert g["status"] == "done" and g["turns"] == 2 and g["reason"] == "both steps are finished"
    got = [(r["role"], r["origin"], r["text"]) for r in rows(thread)]
    assert got[0][:2] == ("user", "command") and got[1][:2] == ("grava", "command") and "Goal set" in got[1][2]
    assert [(r, t) for r, _o, t in got[2:]] == [("auto", goal.shown({"goal": "finish the fictional two-step checklist", "turns": 0})),
                                                ("grava", "Step one is done."),
                                                ("auto", goal.shown({"goal": "finish the fictional two-step checklist", "turns": 1})),
                                                ("grava", "Step two is done; all finished."), ("auto", "Goal done: both steps are finished")]
    first, judge = f.requests[0][1], f.requests[1][1]
    assert text_of([m for m in first.messages if m["role"] == "user"][-1]).startswith("[Continuing toward your standing goal]")
    assert judge.tools == [] and judge.json and "strict judge" in judge.system  # 判的那一次：零工具、一个 JSON
    assert "Step one is done." in text_of(judge.messages[-1]) and "two-step checklist" in text_of(judge.messages[-1])
    assert len(f.requests) == 4


def test_turn_limit_and_blocked(server_json, model, cli):
    server_json({**ON, "engine": {**ON["engine"], "goal": {"max_turns": 2}}})
    model([{"text": "Working."}, verdict("continue"), {"text": "Still working."}, {"text": "Need your password for the fictional portal."},
           verdict("blocked", "it needs the user's password")])

    async def go():
        await say("t-goal-cap", "/goal tidy the fictional archive")
        capped = await settled("t-goal-cap")
        await say("t-goal-block", "/goal log in to the fictional portal")
        return capped, await settled("t-goal-block")
    capped, blocked = run(go())
    assert capped["status"] == "limited" and capped["turns"] == 2  # 第二轮完了就到上限：不再判、不再接着做
    assert rows("t-goal-cap")[-1]["text"].startswith("The goal hit its limit (2/2 turns")
    assert blocked["status"] == "blocked" and "password" in blocked["reason"]
    assert "The goal is blocked: it needs the user's password" in rows("t-goal-block")[-1]["text"]
    assert run(commands.handle("t-goal-block", "/goal")).text.startswith("**Goal**: log in to the fictional portal\n- Status: blocked")


def test_stop_pauses_and_judge_failures_pause(server_json, model, cli):
    import chat
    server_json(ON)
    cli.delay = 5.0
    model([{"text": "Checking.", "calls": [("c1", "schedule", {"args": ["day"]})]}])

    async def stopped():
        await say("t-goal-stop", "/goal plan the fictional week")
        r = chat.RUNS["t-goal-stop"]
        await until(lambda: running_step(r))
        await say("t-goal-stop", "/stop")
        return await settled("t-goal-stop")
    g = run(stopped())
    assert g["status"] == "paused" and "stopped" in g["reason"]
    cli.delay = 0.0
    model([{"text": "One."}, EngineError("judge down"), {"text": "Two."}, EngineError("judge down"), {"text": "Three."}, EngineError("judge down")])

    async def failing():
        await say("t-goal-fail", "/goal sort the fictional notes")
        return await settled("t-goal-fail")
    g = run(failing())
    assert g["status"] == "paused" and g["turns"] == 3 and "failed 3 times" in g["reason"]  # 判不出来算 continue，连着 3 次就停


def test_the_emergency_stop_pauses_the_goal(server_json, model, cli, tmp_path, monkeypatch):
    from engine import estop
    server_json(ON)
    monkeypatch.setattr(estop, "path", lambda: tmp_path / "ESTOP")
    f = model([{"text": "Step one is done."}, verdict("continue", "step two is left")])

    async def go():
        estop.pause("fictional test", by="test")
        await say("t-goal-estop", "/goal finish the fictional two-step checklist")  # 你自己下的命令：头一轮照跑
        return await settled("t-goal-estop")
    g = run(go())
    assert g["status"] == "paused" and "emergency stop" in g["reason"] and g["turns"] == 1
    assert len(f.requests) == 1  # 不判、不接着做
    assert rows("t-goal-estop")[-1]["text"].startswith("The emergency stop is on, so the goal is paused.")


def test_the_users_own_turn_goes_first(server_json, model, cli):
    import chat
    server_json(ON)
    cli.delay = 0.5
    thread = "t-goal-user"
    model([{"text": "Looking.", "calls": [("c1", "schedule", {"args": ["day"]})]}, {"text": "Your day is clear."},
           verdict("continue", "the archive is not tidy yet"), {"text": "Archive tidied."}, verdict("done", "tidied")])

    async def go():
        r = chat.start_run(thread, "what's on today", None)
        await until(lambda: running_step(r))
        out = await say(thread, "/goal tidy the fictional archive")  # 正在回：目标先不开，等这一轮完了判
        started_now = chat.RUNS[thread] is not r
        return out, started_now, await settled(thread)
    out, started_now, g = run(go())
    assert "Goal set" in out[-1][1]["text"] and not started_now
    assert g["status"] == "done" and g["turns"] == 2  # 用户自己那一轮也算一轮（照 Hermes）
    assert [r["text"] for r in rows(thread) if r["role"] == "grava" and r["origin"] != "command"] == ["Looking.\n\nYour day is clear.", "Archive tidied."]


from test_engine_commands import fake, tools  # noqa: E402,F401 — 假模型、假命令行（上面经 request 拿）
