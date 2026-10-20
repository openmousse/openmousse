"""第 4 批：轻量的多步工作流（engine/flows.py）。run → do → ask → do 一步一步走（命令的输出包成资料交给下一步、要点头的出卡、
点头时写的那句交给下一步）；拒绝就停；做到一半重启记 interrupted、resume 从那一步再来；急停着不往下走、--stop-running 停掉正在做的；
定时任务能开它（同一个已经在走就记 busy）；配错的开不了；接口只认主人的设备。模型是假的（FakeModel），时钟是假的（tick(at=…)）。"""
from __future__ import annotations

import asyncio
import json
import stat
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

import config
from engine import estop, flows, jobs, loop, store, when
import test_engine_jobs as tj
from test_engine_jobs import agent, fire, msgs, run, runs

builtin, fake, pushes, quiet_jobs = tj.builtin, tj.fake, tj.pushes, tj.quiet_jobs


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(estop, "path", lambda: tmp_path / "ESTOP")
    monkeypatch.setattr(loop, "MODEL_RETRIES", ())
    flows.ready()
    with store.tx() as conn:
        conn.execute("DELETE FROM engine_flow_runs")
    flows._live.clear()
    jobs.install()
    yield


def script(tmp_path, name: str, body: str):
    p = tmp_path / name
    p.write_text("#!/usr/bin/env python3\n" + body + "\n", encoding="utf8")
    p.chmod(p.stat().st_mode | stat.S_IXUSR)
    return p


def setup(builtin, tmp_path, aid: str, steps: list, name: str = "review", **more) -> None:
    s = script(tmp_path, "done_list.py", "print('done: water the plants; book the vet')")
    builtin({"jobs": {"scripts": [str(s)]}, "flows": {name: {"agent": aid, "steps": [
        {"run": {"script": str(s)}} if x == "RUN" else x for x in steps], **more}}})


def go(coro):
    """跑到它停下（做完、等点头、出错）：同一个事件循环里等后台的那一步走完。"""
    async def inner():
        out = await coro if asyncio.iscoroutine(coro) else coro
        await flows.settle()
        return out
    return run(inner())


# —— 一步一步走 ——————————————————————————————————————————————————————————————————

def test_a_flow_runs_its_steps_and_waits_for_the_owner(builtin, fake, pushes, tmp_path):
    import inbox
    aid = agent("g-flow1", "Home")
    setup(builtin, tmp_path, aid, ["RUN", {"do": "Summarise what got done."}, {"ask": "Send the summary to the family chat?"},
                                   {"do": "Send the summary to the family chat."}])
    f = fake([{"text": "Done this week: plants watered, vet booked."}, {"text": "Sent to the family chat."}])

    async def start():
        return flows.start("review")
    r = go(start())
    r = flows.get(r["id"])
    assert r["status"] == "waiting" and r["at"] == 2 and r["outputs"][0].startswith("done: water the plants")
    said = json.dumps(f.requests[0][1].messages[-1], ensure_ascii=False)
    inside = said.split("untrusted_tool_result source=", 1)
    assert "Summarise what got done." in inside[0] and "book the vet" in inside[1]  # 命令的输出包成资料
    it = inbox.item(r["inbox"])
    assert it["kind"] == "flow" and "Send the summary to the family chat?" in it["title"] and "plants watered" in it["detail"]
    assert len(f.requests) == 1  # 还没点头：不往下走

    async def approve():
        return await inbox.act(r["inbox"], inbox.ActIn(action="approve", note="Keep it to two lines."))
    out = go(approve())
    assert out["item"]["status"] == "done" and "run" not in out
    r = flows.get(r["id"])
    assert r["status"] == "done" and len(f.requests) == 2
    assert "Keep it to two lines." in json.dumps(f.requests[1][1].messages[-1], ensure_ascii=False)  # 点头时写的那句交给下一步
    assert msgs(aid)[-1]["text"] == "Sent to the family chat."


def test_saying_no_stops_the_flow(builtin, fake, pushes, tmp_path):
    import inbox
    aid = agent("g-flow2", "Home")
    setup(builtin, tmp_path, aid, [{"ask": "Start the weekly clean-up?"}, {"do": "Clean up the board."}], name="cleanup")
    fake([])

    async def start():
        return flows.start("cleanup")
    r = go(start())
    run(inbox.act(flows.get(r["id"])["inbox"], inbox.ActIn(action="reject")))
    assert flows.get(r["id"])["status"] == "stopped"


def test_a_failed_command_stops_and_says_so(builtin, fake, pushes, tmp_path):
    aid = agent("g-flow3", "Home")
    bad = script(tmp_path, "boom.py", "import sys\nsys.exit(4)")
    builtin({"jobs": {"scripts": [str(bad)]}, "flows": {"broken": {"agent": aid, "steps": [{"run": {"script": str(bad)}}, {"do": "Never reached."}]}}})
    fake([])

    async def start():
        return flows.start("broken")
    r = flows.get(go(start())["id"])
    assert r["status"] == "failed" and "exit code 4" in r["error"]
    assert "stopped at step 1" in msgs(aid)[-1]["text"]


# —— 重启、急停 ——————————————————————————————————————————————————————————————————

def test_a_step_cut_by_a_restart_is_not_redone_until_resumed(builtin, fake, pushes, tmp_path):
    aid = agent("g-flow4", "Home")
    setup(builtin, tmp_path, aid, [{"do": "Check the calendar."}, {"do": "Tell the user what's on."}], name="calendar")
    fake([])
    rid = "fr-restart01"
    now = store.now_iso()
    with store.tx() as conn:  # 上一个进程做到第 1 步的一半停了
        conn.execute("INSERT INTO engine_flow_runs(id, flow, agent, thread, steps, at, started, status, note, by, created_at, updated_at) "
                     "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (rid, "calendar", aid, aid, json.dumps(flows.flow("calendar")["steps"]), 0, 1, "running",
                                                         json.dumps({"level": "quiet"}), "user", now, now))
    assert flows.recover() == 1 and flows.get(rid)["status"] == "interrupted"
    assert run(flows.tick()) == []  # 被打断的不自己接着走
    f = fake([{"text": "Two meetings today."}, {"text": "You have two meetings today."}])

    async def resume():
        return flows.resume(rid)
    go(resume())
    assert flows.get(rid)["status"] == "done" and len(f.requests) == 2  # 从停下的那一步再来


def test_the_emergency_stop_holds_and_stops_flows(builtin, fake, pushes, tmp_path):
    aid = agent("g-flow5", "Home")
    setup(builtin, tmp_path, aid, [{"do": "Step one."}, {"do": "Step two."}], name="two")
    estop.pause("weekend")
    with pytest.raises(flows.FlowError):
        flows.start("two")  # 急停着不开新的
    estop.resume()
    fake([{"text": "One done.", "delay": 0.5, "chunks": ["One ", "done."]}, {"text": "Two done."}])

    async def scenario():
        r = flows.start("two")
        await asyncio.sleep(0.2)
        n = await flows.stop_running()  # 正在做第一步
        await flows.settle()
        return r, n
    r, n = run(scenario())
    assert n == 1 and flows.get(r["id"])["status"] == "interrupted"


# —— 定时任务开它 ——————————————————————————————————————————————————————————————————

def test_a_job_can_start_a_flow(builtin, fake, pushes, tmp_path):
    aid = agent("g-flow6", "Home")
    setup(builtin, tmp_path, aid, [{"ask": "Run the weekly review now?"}], name="weekly")
    j = run(jobs.create(aid, {"when": "every 1h", "flow": "weekly"}, by_user=True))["job"]
    assert j["task"].endswith("weekly") and jobs.get(j["id"])["flow"] == "weekly"
    fake([])
    fire(when.parse_iso(jobs.get(j["id"])["next_run"]) + timedelta(seconds=1))
    run(flows.settle())
    assert runs(j["id"])[-1]["status"] == "ok" and flows.runs(flow_name="weekly")[0]["status"] == "waiting"
    fire(when.parse_iso(jobs.get(j["id"])["next_run"]) + timedelta(seconds=1))
    assert runs(j["id"])[-1]["status"] == "busy"  # 上一次还在等点头：不再开一次
    with pytest.raises(jobs.JobError):
        jobs.clean(aid, {"when": "every 1h", "flow": "no-such-flow"})


def test_bad_flows_cannot_start(builtin, tmp_path):
    aid = agent("g-flow7", "Home")
    builtin({"flows": {"bad": {"agent": aid, "steps": [{"do": ""}, {"jump": "x"}, {"run": {"script": "/not/allowed.py"}}]}}})
    f = flows.flow("bad")
    assert f["problem"] and "Step 1" in f["problem"] and "Step 2" in f["problem"] and "Step 3" in f["problem"]
    with pytest.raises(flows.FlowError):
        flows.start("bad")
    with pytest.raises(flows.FlowError):
        flows.start("missing")


def test_the_api_starts_and_stops_from_your_devices(builtin, fake, pushes, tmp_path, monkeypatch):
    aid = agent("g-flow8", "Home")
    setup(builtin, tmp_path, aid, [{"ask": "Go ahead?"}], name="api-flow")
    data = json.loads(config.CONFIG_PATH.read_text(encoding="utf8"))
    data["auth"] = {"tokens": {"phone": "tok-phone", "mcp": "tok-mcp"}}
    config.CONFIG_PATH.write_text(json.dumps(data), encoding="utf8")
    config._cache = None
    c = TestClient(__import__("main").app)
    auth = {"Authorization": "Bearer tok-phone"}
    assert c.get("/api/engine/flows", headers={"Authorization": "Bearer tok-mcp"}).status_code == 403
    g = c.get("/api/engine/flows", headers=auth)
    assert g.status_code == 200 and g.json()["flows"][0]["name"] == "api-flow"
    started: list[str] = []
    monkeypatch.setattr(flows, "_go", started.append)  # 测试的客户端一个请求一个事件循环：往下走这件事在这里自己跑
    r = c.post("/api/engine/flows/api-flow/start", headers=auth)
    assert r.status_code == 200
    rid = r.json()["run"]["id"]
    assert started == [rid] and run(flows.advance(rid)) == "waiting"
    assert c.post("/api/engine/flows/api-flow/start", headers=auth).status_code == 409  # 已经有一次在走
    s = c.post(f"/api/engine/flows/runs/{rid}/stop", headers=auth)
    assert s.status_code == 200 and s.json()["run"]["status"] == "stopped"
