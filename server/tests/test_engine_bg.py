"""第 4 批：后台跑这句（engine/bg.py，POST /api/engine/bg）。派成后台任务（和 tasks 工具一样跑、有台账），做完结果回到这个对话；
你自己要的不问额度；急停着不派；只认主人的设备。模型是假的（父对话、子 Agent 各一份剧本，见 test_engine_tasks.py）。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import config
from engine import bg, estop, tasks
from engine.loop import ToolFailure
import test_engine_tasks as tt
from test_engine_jobs import agent
from test_engine_tasks import BUILTIN, delivered, messages, run, task_of, until

models, bridge, clean = tt.models, tt.bridge, tt.clean


@pytest.fixture(autouse=True)
def stop_file(tmp_path, monkeypatch):
    monkeypatch.setattr(estop, "path", lambda: tmp_path / "ESTOP")
    yield


def test_a_message_runs_in_the_background_and_comes_back(server_json, models, bridge):
    import chat
    server_json(BUILTIN)
    aid = agent("g-bg1", "Notes")
    models(parent=[{"text": "Here is the summary of today's notes."}], child=[{"text": "# Notes\n- Call the vet\n- Buy seeds"}])

    async def go():
        out = await bg.start(aid, "Summarise today's notes into a short list.", label="Notes summary")
        await until(lambda: delivered(aid))
        await until(lambda: not chat.busy(aid))
        return out
    out = run(go())
    assert out["status"] == "accepted" and out["label"] == "Notes summary"
    row = task_of(aid)
    assert row["status"] == "done" and row["agent"] == aid and row["task"] == "Summarise today's notes into a short list."
    ms = messages(aid)
    assert ms[-2]["role"] == "auto" and "Notes summary" in ms[-2]["text"] and ms[-1]["text"] == "Here is the summary of today's notes."


def test_your_own_requests_skip_the_quota_card(server_json, models, bridge, monkeypatch):
    server_json({**BUILTIN, "tasks": {"daily_limit": 1}})
    aid = agent("g-bg2", "Notes")
    started: list[str] = []
    monkeypatch.setattr(tasks, "start", started.append)

    async def go():
        a = await bg.start(aid, "First thing.")
        b = await bg.start(aid, "Second thing, over the daily allowance.")
        return a, b
    a, b = run(go())
    assert a["status"] == b["status"] == "accepted" and len(started) == 2  # 超了额度也不出卡：你自己要的


def test_paused_or_unknown_chats_are_refused(server_json, monkeypatch):
    server_json(BUILTIN)
    monkeypatch.setattr(tasks, "start", lambda tid: None)
    with pytest.raises(ToolFailure):
        run(bg.start("no-such-chat", "Do something."))
    with pytest.raises(ToolFailure):
        run(bg.start("cron:job-1", "Do something."))
    estop.pause("testing")
    with pytest.raises(ToolFailure):
        run(bg.start("main", "Do something."))
    estop.resume()


def test_the_bg_command_sends_it_off(server_json, monkeypatch):
    from engine import commands
    server_json({**BUILTIN, "engine": {**BUILTIN["engine"], "commands": True}})
    started: list[str] = []
    monkeypatch.setattr(tasks, "start", started.append)
    out = run(commands.handle("main", "/bg"))
    assert not out.ok and "/bg" in out.text and not started
    out = run(commands.handle("main", "/bg Look up the bus times for Saturday."))
    assert out.ok and out.text.startswith("Running in the background: Look up the bus times for Saturday.") and len(started) == 1
    assert task_of("main")["task"] == "Look up the bus times for Saturday."
    estop.pause("testing")
    out = run(commands.handle("main", "/bg Book the table for Sunday."))
    assert not out.ok and "emergency stop" in out.text.lower() and len(started) == 1
    estop.resume()
    assert run(commands.handle("main", "/bg Book the table for Sunday.", who="friend")) is None  # 别人的 /bg 只是文字
    assert len(started) == 1


def test_the_api_only_answers_your_own_devices(server_json, monkeypatch):
    server_json({**BUILTIN, "auth": {"tokens": {"phone": "tok-phone", "mcp": "tok-mcp"}}})
    config._cache = None
    started: list[str] = []
    monkeypatch.setattr(tasks, "start", started.append)
    c = TestClient(__import__("main").app)
    body = {"thread": "main", "text": "Look up the bus times for Saturday."}
    assert c.post("/api/engine/bg", json=body, headers={"Authorization": "Bearer tok-mcp"}).status_code == 403
    assert c.post("/api/engine/bg", json=body, headers={"Authorization": "Bearer tok-phone", "X-Mousse-Via": "mcp"}).status_code == 403
    r = c.post("/api/engine/bg", json=body, headers={"Authorization": "Bearer tok-phone"})
    assert r.status_code == 200 and r.json()["task"]["status"] == "accepted" and len(started) == 1
    assert c.post("/api/engine/bg", json={"thread": "main", "text": ""}, headers={"Authorization": "Bearer tok-phone"}).status_code == 409
