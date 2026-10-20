"""第 4 批：急停（engine/estop.py）。停着时定时任务、心跳到点不跑（记一条 estop，不算失败）；新的后台任务不派；写回、回看、补向量不跑；
在跑的默认不杀，stop_running 才停；文件坏了也算停着；重启后还停着；主人在 app 里说话照常；接口只认主人的设备；命令行 mousse pause / resume。
模型是假的（FakeModel），时钟是假的（tick(at=…)）；急停的文件放在 tmp_path（不碰共用的数据目录）。"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from datetime import timedelta
from pathlib import Path

import pytest

import engine
from engine import estop, heartbeat, jobs, ledger, runners, semantic, skill_write, store, tasks, when, writeback
import test_engine_jobs as tj
from test_engine_jobs import fire, msgs, run, runs, user_job, wait_run

builtin, fake, pushes = tj.builtin, tj.fake, tj.pushes  # 定时任务那边的几个 fixture

SERVER = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def stop_file(tmp_path, monkeypatch):
    """急停的文件、内核的库都放在这个测试自己的目录里：别的测试永远看不到它们（记下的 estop 不会混进别的测试数的运行记录）。"""
    import config
    f = tmp_path / "estop" / "ESTOP"
    monkeypatch.setattr(estop, "path", lambda: f)
    monkeypatch.setattr(config.settings, "db", tmp_path / "estop.db")
    estop._logged.clear()
    monkeypatch.setattr(runners, "_found", {})
    jobs._running.clear()
    heartbeat._running.clear()
    yield f
    estop._logged.clear()


def ctx(aid: str = "main") -> engine.ToolCtx:
    return engine.ToolCtx(turn="t-x", kind="owner", agent=aid, conversation=aid, call_id="c", control=engine.Control())


# —— 停着：到点不跑 ——————————————————————————————————————————————————————————————————

def test_paused_jobs_and_heartbeats_skip_without_counting_as_failures(builtin, fake, pushes):
    aid = "main"  # 库是这个测试自己的：用 main（groups 表在共用的那个库里）
    j = user_job(aid)
    builtin({"heartbeat": {aid: {"every": "1m", "prompt": "Remind the user to stretch."}}})
    f = fake([])  # 调了模型就是错的（剧本是空的）
    assert not estop.paused()
    estop.pause("Away for the weekend")
    assert estop.paused() and estop.reason() == "Away for the weekend"
    due = when.parse_iso(j["next_run"])
    before = jobs._today_runs(due)
    assert fire(due + timedelta(seconds=1)) == []
    assert jobs._today_runs(due) == before  # estop 不算进每天的上限
    k = jobs.get(j["id"])
    r = runs(j["id"])[-1]
    assert r["status"] == "estop" and r["error"] == "Away for the weekend" and r["kind"] == "on_time"
    assert k["state"] == "on" and k["fails"] == 0 and k["runs"] == 0 and when.parse_iso(k["next_run"]) == due + timedelta(minutes=2)
    t0 = when.now()
    run(heartbeat.tick(at=t0))  # 第一次：排上
    assert fire(t0 + timedelta(seconds=61), beat=True) == []
    assert heartbeat.state(aid)["last_reason"] == "estop" and jobs.runs_of(f"heartbeat:{aid}")[0]["status"] == "estop"
    assert f.requests == [] and pushes == []
    # 恢复：下一次到点照常跑
    assert estop.resume() and not estop.paused() and not estop.resume()
    fake([{"text": "Water time."}])
    assert fire(due + timedelta(minutes=2, seconds=1)) == [j["id"]]
    assert runs(j["id"])[-1]["status"] == "ok" and msgs(aid)[-1]["text"] == "Water time."


def test_a_one_shot_due_while_paused_is_marked_missed(builtin, fake):
    aid = "main"  # 库是这个测试自己的：用 main（groups 表在共用的那个库里）
    j = user_job(aid, when="in 30m", task="Remind the user to call the bank.")
    fake([])
    estop.pause()
    assert fire(when.parse_iso(j["next_run"]) + timedelta(seconds=1)) == []
    k = jobs.get(j["id"])
    assert k["state"] == "missed" and k["last_status"] == "estop" and runs(j["id"])[-1]["status"] == "estop"


def test_an_agent_cannot_run_a_job_by_hand_while_paused_but_the_owner_can(builtin, fake, pushes):
    aid = "main"  # 库是这个测试自己的：用 main（groups 表在共用的那个库里）
    j = user_job(aid)
    estop.pause()
    with pytest.raises(engine.ToolFailure, match="paused"):
        run(jobs.tool(aid).run({"action": "run", "id": j["id"]}, ctx(aid)))
    fake([{"text": "Ran by hand."}])

    async def by_owner():
        out = await jobs.api_action(j["id"], "run")
        await jobs.settle()
        return out
    assert run(by_owner())["ok"] and runs(j["id"])[-1]["status"] == "ok"


def test_new_background_tasks_are_not_dispatched(server_json):
    server_json({"claw": {"kind": "builtin"}, "engine": {"model": "deepseek/deepseek-flash"}})
    with store.tx() as conn:
        conn.execute("DELETE FROM engine_tasks")
    estop.pause("Testing the brakes")
    spawn = {"action": "spawn", "label": "Formula sheet", "task": "Goal: a one-page formula sheet"}
    with pytest.raises(engine.ToolFailure, match="Testing the brakes"):
        run(tasks.tool("main").run(spawn, ctx("main")))
    with pytest.raises(engine.ToolFailure):
        tasks.create(thread="main", agent="main", params={"task": "x"})
    assert ledger.rows("1=1", (), 5) == []


def test_write_back_skill_review_and_embeddings_wait(builtin, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("should not run while paused")
    monkeypatch.setattr(writeback, "due", boom)
    monkeypatch.setattr(skill_write, "review_model", boom)
    estop.pause()
    assert run(writeback.sweep()) == [] and semantic._held()

    class Res:
        status = "done"
        messages: list = []
    skill_write.after_turn(Res(), agent="main", session=None, offered={skill_write.TOOL_NAME}, model="deepseek/deepseek-flash")
    estop.resume()
    assert not semantic._held()


# —— 在跑的：默认不杀，stop_running 才停 ————————————————————————————————————————————————

def test_running_work_finishes_unless_stop_running(builtin, fake, pushes):
    aid = "main"  # 库是这个测试自己的：用 main（groups 表在共用的那个库里）
    j = user_job(aid)
    fake([{"chunks": ["a", "b", "c"], "delay": 0.3}])
    due = when.parse_iso(j["next_run"])

    async def keep():
        assert await jobs.tick(at=due + timedelta(seconds=1)) == [j["id"]]
        estop.pause("no kill")
        assert await estop.enforce() == 0  # 没说 stop_running：在跑的跑完
        await jobs.settle()
    run(keep())
    assert runs(j["id"])[-1]["status"] == "ok" and msgs(aid)[-1]["text"] == "abc"
    estop.resume()
    fake([{"chunks": ["slow"] * 20, "delay": 0.3}])
    nxt = when.parse_iso(jobs.get(j["id"])["next_run"])

    async def kill():
        assert await jobs.tick(at=nxt + timedelta(seconds=1)) == [j["id"]]
        await asyncio.sleep(0.1)
        estop.pause("stop it", stop_running=True)
        assert await estop.enforce() >= 1
        await jobs.settle()
        assert await estop.enforce() == 0  # 照做过一次了：不再来
    run(kill())
    k = jobs.get(j["id"])
    assert runs(j["id"])[-1]["status"] == "interrupted" and k["fails"] == 0 and k["state"] == "on" and not k["running"]
    assert estop.state()["stopped_at"] and estop.view()["stopRunning"]


def test_stop_running_also_stops_background_tasks(server_json, fake, monkeypatch):
    server_json({"claw": {"kind": "builtin"}, "engine": {"model": "deepseek/deepseek-flash"}})
    monkeypatch.setattr(tasks, "GRACE", 0.5)
    with store.tx() as conn:
        conn.execute("DELETE FROM engine_tasks")
    fake([{"chunks": ["working"] * 40, "delay": 0.2}])

    async def go():
        row = tasks.create(thread="main", agent="main", params={"task": "Goal: read everything", "label": "Long read"})
        tasks.start(row["id"])
        t0 = time.monotonic()
        while (ledger.get(row["id"]) or {}).get("status") != "running" and time.monotonic() - t0 < 5:
            await asyncio.sleep(0.02)
        estop.pause(stop_running=True)
        assert await estop.enforce() >= 1
        while (ledger.get(row["id"]) or {}).get("status") in ("queued", "running") and time.monotonic() - t0 < 10:
            await asyncio.sleep(0.05)
        return ledger.get(row["id"])
    r = run(go())
    assert r["status"] == "cancelled" and (r.get("data") or {}).get("stopped_by") == "user"


def test_stop_running_stops_media_that_went_to_the_background(server_json, monkeypatch):
    from engine import host_process, mediagen
    server_json({"claw": {"kind": "builtin"}, "engine": {"model": "deepseek/deepseek-flash"}})
    said: list[tuple[str, str]] = []
    monkeypatch.setattr(host_process, "note", lambda thread, line, text: said.append((thread, line)))
    monkeypatch.setattr(mediagen, "_jobs", {})

    async def slow(*a, **k):
        await asyncio.sleep(30)
    monkeypatch.setattr(mediagen, "generate", slow)

    async def go():
        made = {}
        for bg in (True, False):
            j = mediagen.Job(id=f"vg-{int(bg)}", tool=mediagen.VIDEO, agent="main", conversation="main", ref="fal/fictional-video",
                             prompt="a fictional cat on a fictional beach", background=bg)
            j.task = asyncio.ensure_future(mediagen._work(j, None, {}, None, "clip"))
            mediagen._jobs[j.id] = j
            made[bg] = j
        await asyncio.sleep(0.05)
        estop.pause(stop_running=True)
        n = await estop.enforce()
        inline = made[False].status  # 还在一轮里等着的：跟着那一轮（你自己的那一轮不停）
        made[False].task.cancel()
        await asyncio.gather(made[False].task, return_exceptions=True)
        return n, made[True].status, inline
    n, background, inline = run(go())
    assert n >= 1 and background == "stopped" and inline == "running"
    assert said == [("main", "Video generation stopped by the emergency stop")]


# —— 文件：坏了也算停着、重启以后还在 ——————————————————————————————————————————————————————

def test_a_broken_or_empty_file_still_pauses(stop_file):
    stop_file.parent.mkdir(parents=True, exist_ok=True)
    stop_file.write_text("not json", encoding="utf8")
    assert estop.paused() and estop.reason() is None and estop.view()["paused"]
    stop_file.write_text("", encoding="utf8")
    assert estop.paused()
    assert estop.resume() and not estop.paused()


def test_the_command_line_pauses_and_resumes_across_processes(tmp_path, monkeypatch):
    """mousse pause 在另一个进程里写的文件，这边（服务器）马上看得到；重启（新的进程）也照样停着。"""
    data = tmp_path / "data"
    cfg = tmp_path / "server.json"
    cfg.write_text(json.dumps({"timezone": "UTC", "language": "en", "data_dir": str(data)}), encoding="utf8")
    monkeypatch.setattr(estop, "path", lambda: data / "ESTOP")
    env = {**os.environ, "MOUSSE_SERVER_CONFIG": str(cfg)}

    def mousse(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(SERVER / "mousse.py"), *args], env=env, capture_output=True, text=True, timeout=60)
    out = mousse("pause", "--reason", "Spend looked odd", "--stop-running")
    assert out.returncode == 0 and "Paused (Spend looked odd)" in out.stdout and "within 30 seconds" in out.stdout
    s = estop.state()
    assert s["reason"] == "Spend looked odd" and s["by"] == "cli" and s["stop_running"] and not s["stopped_at"]
    assert oct((data / "ESTOP").stat().st_mode & 0o777) == "0o600"
    probe = subprocess.run([sys.executable, "-c", "from engine import estop; print(estop.paused(), estop.reason())"], cwd=str(SERVER), env=env,
                           capture_output=True, text=True, timeout=60)
    assert probe.stdout.strip() == "True Spend looked odd"  # 一个新的进程（重启以后）：照样停着
    again = mousse("pause")
    assert again.returncode == 0 and again.stdout.startswith("Still paused") and estop.state()["since"] == s["since"]
    out = mousse("resume")
    assert out.returncode == 0 and "Resumed" in out.stdout and not estop.paused()
    assert "wasn't paused" in mousse("resume").stdout


# —— 主人自己说话照常 —————————————————————————————————————————————————————————————————————

def test_the_owner_can_still_chat_while_paused(builtin, fake, pushes):
    import chat
    fake([{"text": "Still here."}])
    estop.pause()

    async def go():
        chat.start_run("main", "hello?", None)
        return await wait_run("main")
    r = run(go())
    assert r.status == "ok" and r.text == "Still here."


# —— 接口：只认主人的设备 ——————————————————————————————————————————————————————————————————

def test_the_api_takes_only_the_owners_devices(server_json, monkeypatch):
    import main
    from fastapi.testclient import TestClient

    from engine import api
    server_json({"claw": {"kind": "builtin"}, "engine": {"model": "deepseek/deepseek-flash"},
                 "auth": {"tokens": {"phone": "tok-phone", "mcp": "tok-mcp", "api": "tok-api"}}})
    c = TestClient(main.app)
    h = {"Authorization": "Bearer tok-phone"}
    assert c.get("/api/engine/pause", headers=h).json() == {"ok": True, "paused": False}
    r = c.post("/api/engine/pause", headers=h, json={"reason": "Trip", "stop_running": False})
    assert r.status_code == 200 and r.json()["paused"] and r.json()["reason"] == "Trip" and r.json()["stopped"] == 0
    assert estop.paused() and api.status()["paused"] is True
    assert c.post("/api/engine/resume", headers={**h, "X-Mousse-Via": "auto"}).status_code == 403  # Agent 的脚本替你点的
    assert c.post("/api/engine/resume", headers={"Authorization": "Bearer tok-mcp"}).status_code == 403
    assert c.post("/api/engine/resume").status_code == 401
    monkeypatch.setattr(main, "principal_of", lambda req: "loopback")
    assert c.post("/api/engine/resume").status_code == 403 and estop.paused()
    monkeypatch.setattr(main, "principal_of", lambda req: "tailscale:my-phone")
    r = c.post("/api/engine/resume")
    assert r.status_code == 200 and r.json()["lifted"] and not estop.paused() and api.status()["paused"] is False
    assert c.post("/api/engine/pause", json={"reason": "x" * 1000}).status_code == 400
