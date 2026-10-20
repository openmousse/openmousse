"""第 4 批：心跳补齐（engine/heartbeat.py）。heartbeat_respond：notify false 什么都不发、那一句下次心跳带上；notify true 发 text；
只有心跳那一轮收、一轮只收一次、心跳开着的 Agent 才有；session chat 在对话里跑（没要说的照样不留）；pre 说不叫醒就不调模型、不然输出当资料。
模型是假的（FakeModel），时钟是假的（tick(at=…)）；急停的文件在 tmp_path。"""
from __future__ import annotations

import json
import stat
from datetime import timedelta

import pytest

import engine
from engine import estop, heartbeat, loop, tools, when
from engine.loop import ToolFailure
from engine.session import text_of
import test_engine_jobs as tj
from test_engine_jobs import agent, fire, msgs, run

builtin, fake, pushes, quiet_jobs = tj.builtin, tj.fake, tj.pushes, tj.quiet_jobs


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(estop, "path", lambda: tmp_path / "ESTOP")
    monkeypatch.setattr(loop, "MODEL_RETRIES", ())
    heartbeat._answers.clear()
    heartbeat._refs.clear()
    yield


def respond(**args) -> dict:
    return {"text": "", "calls": [("c1", heartbeat.RESPOND, args)]}


def ctx(aid: str, conversation: str, turn: str) -> engine.ToolCtx:
    return engine.ToolCtx(turn=turn, kind="owner", agent=aid, conversation=conversation, call_id="c", control=engine.Control())


# —— heartbeat_respond ————————————————————————————————————————————————————————————————

def test_notify_false_sends_nothing_and_the_note_comes_back_next_time(builtin, fake, pushes):
    aid = agent("g-hbr1")
    builtin({"heartbeat": {aid: {"every": "1m", "prompt": "Check whether anything new needs the user."}}})
    t0 = when.now()
    run(heartbeat.tick(at=t0))
    f = fake([respond(notify=False, summary="Nothing new since this morning."), {"text": "All checked."}, {"text": "NO_REPLY"}])
    before = len(msgs(aid))
    assert fire(t0 + timedelta(seconds=61), beat=True) == [aid]
    st = heartbeat.state(aid)
    assert st["last_status"] == "silent" and st["memo"] == "Nothing new since this morning." and len(msgs(aid)) == before and pushes == []
    assert heartbeat.RESPOND in {t["name"] for t in f.requests[0][1].tools}
    assert fire(t0 + timedelta(seconds=122), beat=True) == [aid]
    assert "Nothing new since this morning." in text_of(f.requests[2][1].messages[0])  # 下一次心跳带上上次自己记的


def test_notify_true_sends_the_text(builtin, fake, pushes):
    aid = agent("g-hbr2")
    builtin({"heartbeat": {aid: {"every": "1m", "prompt": "Remind the user about lunch if it isn't logged."}}})
    t0 = when.now()
    run(heartbeat.tick(at=t0))
    fake([respond(notify=True, summary="Lunch not logged.", text="You haven't logged lunch yet."), {"text": "Sent the reminder."}])
    assert fire(t0 + timedelta(seconds=61), beat=True) == [aid]
    assert msgs(aid)[-1]["text"] == "You haven't logged lunch yet." and heartbeat.state(aid)["last_status"] == "ok"
    assert pushes[-1]["body"] == "You haven't logged lunch yet."


def test_the_tool_only_answers_inside_a_heartbeat_and_only_once(builtin):
    aid = agent("g-hbr3")
    assert heartbeat.RESPOND not in {t.name for t in tools.for_agent(aid)}  # 心跳没开：没有这个工具
    builtin({"heartbeat": {aid: {"every": "1m", "prompt": "Look around."}}})
    assert heartbeat.RESPOND in {t.name for t in tools.for_agent(aid)}  # 开着：每一轮都有（工具表稳定）
    tool = heartbeat.respond_tool(aid)
    with pytest.raises(ToolFailure):
        run(tool.run({"notify": False, "summary": "x"}, ctx(aid, aid, "t-normal")))  # 平常的一轮不收
    hb = ctx(aid, f"heartbeat:{aid}", "t-beat")
    with pytest.raises(ToolFailure):
        run(tool.run({"notify": True, "summary": "x"}, hb))  # notify true 要有 text
    assert "nothing is sent" in run(tool.run({"notify": False, "summary": "All quiet."}, hb))
    with pytest.raises(ToolFailure):
        run(tool.run({"notify": False, "summary": "again"}, hb))  # 一轮只收一次
    assert heartbeat._answers["t-beat"]["summary"] == "All quiet."


# —— session chat ——————————————————————————————————————————————————————————————————

def test_a_chat_session_heartbeat_runs_in_the_conversation(builtin, fake, pushes):
    aid = agent("g-hbr4")
    builtin({"heartbeat": {aid: {"every": "1m", "prompt": "Ask the user how the day is going.", "session": "chat"}}})
    t0 = when.now()
    run(heartbeat.tick(at=t0))
    f = fake([{"text": "How's your day going?"}])
    assert fire(t0 + timedelta(seconds=61), beat=True) == [aid]
    m = msgs(aid)
    assert m[-1]["text"] == "How's your day going?" and m[-2]["role"] == "auto" and m[-2]["text"].endswith("Checked in on its own")
    assert "Ask the user how the day is going." in json.dumps(f.requests[0][1].messages, ensure_ascii=False)
    before = len(msgs(aid))
    fake([respond(notify=False, summary="Already asked today."), {"text": "Nothing more to add."}])
    assert fire(t0 + timedelta(seconds=122), beat=True) == [aid]
    assert len(msgs(aid)) == before and heartbeat.state(aid)["memo"] == "Already asked today."  # notify false：对话里不留
    assert heartbeat.state(aid)["last_status"] == "silent"


# —— pre ——————————————————————————————————————————————————————————————————————————

def test_a_pre_command_can_keep_the_heartbeat_asleep(builtin, fake, pushes, tmp_path):
    flag = tmp_path / "unread.txt"
    flag.write_text("", encoding="utf8")
    s = tmp_path / "unread.py"
    s.write_text("#!/usr/bin/env python3\nimport json\n"
                 f"n = open({str(flag)!r}).read().strip()\n"
                 "print(f'{n} unread messages' if n else json.dumps({'wake': False}))\n", encoding="utf8")
    s.chmod(s.stat().st_mode | stat.S_IXUSR)
    aid = agent("g-hbr5")
    builtin({"jobs": {"scripts": [str(s)]}, "heartbeat": {aid: {"every": "1m", "prompt": "Tell the user about unread messages.",
                                                                "pre": {"script": str(s)}}}})
    t0 = when.now()
    run(heartbeat.tick(at=t0))
    f = fake([{"text": "You have 2 unread messages."}])
    assert fire(t0 + timedelta(seconds=61), beat=True) == [aid]
    assert heartbeat.state(aid)["last_status"] == "gated" and f.requests == []  # pre 说不叫醒：不调模型
    flag.write_text("2", encoding="utf8")
    assert fire(t0 + timedelta(seconds=122), beat=True) == [aid]
    said = text_of(f.requests[0][1].messages[0])
    assert "2 unread messages" in said.split("untrusted_tool_result source=", 1)[1]  # pre 的输出包成资料
    assert msgs(aid)[-1]["text"] == "You have 2 unread messages."
