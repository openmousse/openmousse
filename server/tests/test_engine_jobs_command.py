"""10/19：只跑命令的定时任务（engine/jobs.py「只跑命令的」）：到点跑一个命令行工具，不调模型。只许 BRIDGES 里的工具（参数照工具的规矩查）
或者 server.json engine.jobs.scripts 白名单里的脚本（参数照 bridges.guard 查）；有时限、输出记进运行记录、没跑成记错不重试；
Agent 建的一样先停着等用户点头；每隔多久、每周几几点都行。

命令是 tmp_path 里的假脚本（经 server.json 的 mcp.scripts 换掉 board 背后的命令，或者列进白名单），不调模型（FakeModel 一次都不该被调）。
数据都是编的。"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from datetime import timedelta

import pytest

import engine
from engine import jobs, store, when
from test_engine_jobs import agent, ctx, fire, msgs, run, runs, wait_run

FAKE = '''import json, os, sys, time
args = sys.argv[1:]
if "--sleep" in args:
    time.sleep(float(args[args.index("--sleep") + 1]))
    open(os.environ["FAKE_TOOL_MARK"], "w").write("still ran")
print(json.dumps({"args": args, "agent": os.environ.get("MOUSSE_AGENT"), "via": os.environ.get("MOUSSE_VIA"), "stdin": sys.stdin.read()}))
if "--fail" in args:
    print("boom: the fictional feed is down", file=sys.stderr)
    sys.exit(3)
'''


@pytest.fixture(autouse=True)
def quiet_jobs():
    """库是所有测试共用的：别的测试留下的任务先停掉，免得被这里的 tick 跑起来（同 test_engine_jobs）。"""
    jobs.ready()
    with store.tx() as conn:
        conn.execute("UPDATE engine_jobs SET state='paused' WHERE state='on'")
    jobs._running.clear()
    jobs._down.update(since=None, started=None)
    yield


@pytest.fixture
def cmd(tmp_path, server_json, monkeypatch):
    """builtin + 一个假的命令行工具（board 背后换成它）+ 一个白名单里的脚本（能执行的）+ 一个不在白名单里的。
    set(**engine 段多写的) 重写 server.json；model 是按量的，但一次都不该调（FakeModel 空剧本：调了就报错）。"""
    from engine import providers

    from test_engine import FakeModel
    tool, script, other = tmp_path / "fake_board.py", tmp_path / "practice_check.py", tmp_path / "not_listed.py"
    tool.write_text(FAKE, encoding="utf8")
    for p in (script, other):
        p.write_text(f"#!{sys.executable}\n" + FAKE, encoding="utf8")
        p.chmod(0o755)
    monkeypatch.setenv("FAKE_TOOL_MARK", str(tmp_path / "mark"))
    model = FakeModel([])
    monkeypatch.setattr(providers, "stream", model.stream)

    def set_(**extra) -> None:
        jobs_cfg = {"scripts": [str(script)], **extra.pop("jobs", {})}
        server_json({"claw": {"kind": "builtin"}, "mcp": {"scripts": {"board": [sys.executable, str(tool)]}},
                     "engine": {"model": "deepseek/deepseek-flash", "jobs": jobs_cfg, **extra}})
    set_()
    from engine import tools
    tools.install_hooks()

    class Cmd:
        pass
    c = Cmd()
    c.tool, c.script, c.other, c.mark, c.model, c.set = tool, script, other, tmp_path / "mark", model, set_
    return c


def user_cmd_job(aid: str, command: dict, when_: str = "every 5m", **given) -> dict:
    out = run(jobs.api_create(jobs.JobIn(agent=aid, when=when_, command=command, **given)))
    return jobs.get(out["job"]["id"])


def due_fire(jid: str) -> None:
    fire(when.parse_iso(jobs.get(jid)["next_run"]) + timedelta(seconds=1))


# —— 用户建的：直接开，到点跑、输出记下来 ———————————————————————————————————————————————

def test_a_user_job_runs_a_tool_every_5_minutes_and_logs_its_output(cmd):
    aid = agent("g-cmd1", "Practice")
    j = user_cmd_job(aid, {"tool": "board", "args": ["rows", "query", "sessions"]})
    assert j["state"] == "on" and j["command"] == {"tool": "board", "args": ["rows", "query", "sessions"], "timeout": 120}
    assert j["prompt"] == "board rows query sessions" and j["session"] == "isolated" and j["model"] is None
    due = when.parse_iso(j["next_run"])
    assert abs((due - when.now()).total_seconds() - 300) < 10  # 每 5 分钟：开了以后 5 分钟第一次
    before = len(msgs(aid))
    due_fire(j["id"])
    r = runs(j["id"])[-1]
    got = json.loads(r["output"])
    assert r["status"] == "ok" and r["turn"] is None and r["preview"] is None
    assert got == {"args": ["rows", "query", "sessions"], "agent": aid, "via": "auto", "stdin": ""}  # 照这个 Agent 跑；脚本再开的回合算后台
    k = jobs.get(j["id"])
    assert k["last_status"] == "ok" and k["runs"] == 1 and when.parse_iso(k["next_run"]) == due + timedelta(minutes=5)
    assert len(msgs(aid)) == before and cmd.model.requests == []  # 不往对话里发，不调模型
    v = jobs.view(k)
    assert v["command"]["tool"] == "board" and "runs a command, no model" in jobs.line(k)


def test_an_allowlisted_script_on_weekdays_at_a_time(cmd):
    aid = agent("g-cmd2", "Practice")
    j = user_cmd_job(aid, {"script": str(cmd.script), "args": ["--window", "60"], "input": "hello"}, "weekdays 07:30")
    assert j["schedule"] == {"kind": "daily", "at": "07:30", "days": [0, 1, 2, 3, 4], "text": "weekdays 07:30"}
    nxt = when.parse_iso(j["next_run"])
    assert nxt.weekday() < 5 and (nxt.hour, nxt.minute) == (7, 30)

    async def by_hand():  # 手动跑一次（不改下一次）；同一个事件循环里等它跑完
        assert (await jobs.api_action(j["id"], "run"))["ok"]
        await jobs.settle()
    run(by_hand())
    got = json.loads(runs(j["id"])[-1]["output"])
    assert got["args"] == ["--window", "60"] and got["stdin"] == "hello" and jobs.get(j["id"])["next_run"] == j["next_run"]
    assert jobs.get(j["id"])["command"]["timeout"] == jobs.SCRIPT_TIMEOUT


# —— 只许跑什么、参数怎么查 ——————————————————————————————————————————————————————————

@pytest.mark.parametrize("command,why", [
    ({"script": "PLACEHOLDER_OTHER"}, "isn't listed"),                                   # 不在白名单里
    ({"script": "practice_check.py"}, "isn't listed"),                                    # 相对路径不认
    ({"tool": "rm", "args": ["-rf", "/"]}, "no command-line tool called rm"),            # 不是 BRIDGES 里的
    ({"tool": "board", "args": ["apply", "--file", "/etc/hosts"]}, "write --file -"),    # 读服务器上的文件（bridges.guard）
    ({"tool": "inbox", "args": ["add", "--source", "g-someone"]}, "leave out --source"),  # 冒充别人（tools.problem）
    ({"tool": "agents", "args": ["list"]}, "You don't have the agents tool"),             # 主对话专用的
    ({"script": "PLACEHOLDER_SCRIPT", "args": ["--file=/etc/hosts"]}, "write --file -"),  # 脚本的参数一样照 guard 查
    ({"tool": "board", "script": "PLACEHOLDER_SCRIPT"}, "either tool or script"),
    ({"tool": "board", "args": ["show"], "timeout": 9999}, "between 1 and 600"),
    ({"tool": "board", "args": [{"nested": 1}]}, "list of words"),
])
def test_only_registered_tools_and_allowlisted_scripts_with_checked_args(cmd, command, why):
    aid = agent("g-cmd3", "Practice")
    command = {k: str(cmd.other) if v == "PLACEHOLDER_OTHER" else str(cmd.script) if v == "PLACEHOLDER_SCRIPT" else v for k, v in command.items()}
    with pytest.raises(jobs.JobError) as e:
        run(jobs.create(aid, {"when": "every 5m", "command": command}, by_user=True))
    assert why in str(e.value)
    with pytest.raises(engine.ToolFailure) as e2:  # Agent 经 cron 工具建的：一样的规矩
        run(jobs.tool(aid).run({"action": "create", "when": "every 5m", "command": command}, ctx(aid)))
    assert why in str(e2.value)


def test_a_script_dropped_from_the_allowlist_no_longer_runs(cmd):
    aid = agent("g-cmd4", "Practice")
    j = user_cmd_job(aid, {"script": str(cmd.script)})
    cmd.set(jobs={"scripts": []})
    due_fire(j["id"])
    r = runs(j["id"])[-1]
    assert r["status"] == "error" and "isn't listed" in r["error"] and r["output"] is None  # 到点再查一遍：不跑


def test_a_tool_turned_off_in_the_settings_does_not_run(cmd):
    aid = agent("g-cmd5", "Practice")
    j = user_cmd_job(aid, {"tool": "board", "args": ["show"]})
    cmd.set(tools={"levels": {"board": "off"}})
    due_fire(j["id"])
    assert runs(j["id"])[-1]["status"] == "error" and "don't have the board tool" in runs(j["id"])[-1]["error"]


# —— 时限、失败 ———————————————————————————————————————————————————————————————————

def test_a_command_that_runs_too_long_is_stopped(cmd):
    aid = agent("g-cmd6", "Practice")
    j = user_cmd_job(aid, {"script": str(cmd.script), "args": ["--sleep", "3"], "timeout": 1})
    t0 = time.monotonic()
    due_fire(j["id"])
    assert time.monotonic() - t0 < 2.5
    r = runs(j["id"])[-1]
    assert r["status"] == "timeout" and "didn't finish within 1 seconds" in r["error"]
    time.sleep(2.5)
    assert not cmd.mark.exists()  # 连它一起停掉了：没有跑完那一步


def test_command_jobs_do_not_take_the_agents_tool_slots(cmd, monkeypatch):
    """几分钟长的命令不挤着 Agent 调工具：不占 /mcp 和内核共用的那 4 个（bridges.PARALLEL），定时任务自己有 max_parallel。"""
    import bridges
    monkeypatch.setattr(bridges, "PARALLEL", asyncio.Semaphore(0))  # 4 个全被占着
    aid = agent("g-cmd13", "Practice")
    j = user_cmd_job(aid, {"tool": "board", "args": ["show"], "timeout": 5})

    async def go():
        await jobs.tick(at=when.parse_iso(j["next_run"]) + timedelta(seconds=1))
        await asyncio.wait_for(jobs.settle(), 4)
    run(go())
    assert runs(j["id"])[-1]["status"] == "ok"


def test_failures_are_logged_said_once_never_retried_and_pause_after_five(cmd):
    aid = agent("g-cmd7", "Practice")
    j = user_cmd_job(aid, {"tool": "board", "args": ["show", "--fail"]})
    due = when.parse_iso(j["next_run"])
    due_fire(j["id"])
    r = runs(j["id"])[-1]
    assert r["status"] == "error" and r["error"] == "exit code 3: boom: the fictional feed is down"
    assert '"args": ["show", "--fail"]' in r["output"] and "stderr:\nboom" in r["output"]  # 标准输出、错误输出都记下
    assert when.parse_iso(jobs.get(j["id"])["next_run"]) == due + timedelta(minutes=5)  # 不马上重试：照常排到下一次
    assert run(jobs.tick(at=due + timedelta(seconds=30))) == []
    for _ in range(jobs.FAILS_TO_PAUSE - 1):
        due_fire(j["id"])
    k = jobs.get(j["id"])
    assert k["state"] == "paused" and k["fails"] == jobs.FAILS_TO_PAUSE and len(runs(j["id"])) == jobs.FAILS_TO_PAUSE
    notes = [m["text"] for m in msgs(aid) if m["role"] == "auto"]
    assert sum("didn't run this time" in n for n in notes) == 1 and sum("so it's paused" in n for n in notes) == 1  # 不刷屏


def test_command_jobs_do_not_use_up_the_daily_model_limit(cmd, monkeypatch):
    aid = agent("g-cmd8", "Practice")
    j = user_cmd_job(aid, {"tool": "board", "args": ["show"]})
    m = run(jobs.create(aid, {"when": "every 5m", "task": "Remind the user to stretch."}, by_user=True))["job"]
    cmd.set(jobs={"daily_limit": 3})
    real = jobs._today_runs
    monkeypatch.setattr(jobs, "_today_runs", lambda at: 3)
    at = max(when.parse_iso(j["next_run"]), when.parse_iso(m["nextRun"])) + timedelta(seconds=1)
    assert fire(at) == [j["id"]]  # 只跑命令的照跑；调模型的到了上限
    assert runs(j["id"])[-1]["status"] == "ok" and runs(m["id"])[-1]["status"] == "limit"
    start = when.iso(when.now().replace(hour=0, minute=0, second=0, microsecond=0))
    q = ("SELECT COUNT(*) FROM engine_job_runs r JOIN engine_jobs j ON j.id=r.job WHERE julianday(r.started_at)>=julianday(?) "
         "AND r.kind!='heartbeat' AND r.status NOT IN ('skipped','missed','limit','estop','unchanged','gated')")  # 第 4 批：急停、没变、pre 没叫醒的也不算
    with store.read() as conn:
        everything = conn.execute(q, (start,)).fetchone()[0]
        commands = conn.execute(q + " AND j.command IS NOT NULL", (start,)).fetchone()[0]
    assert commands >= 1 and real(when.now()) == everything - commands  # 数今天跑了几次时，只跑命令的那几次不算


# —— Agent 建的：先停着，用户点头才开 ———————————————————————————————————————————————————

def test_an_agent_command_job_waits_for_the_users_ok(cmd):
    import inbox
    aid = agent("g-cmd9", "Practice")
    t = jobs.tool(aid)
    res = run(t.run({"action": "create", "name": "Practice check", "when": "every 5m", "why": "So I can ask before practice.",
                     "command": {"tool": "board", "args": ["rows", "query", "sessions"], "timeout": 30}}, ctx(aid)))
    assert res["status"] == "approval-pending"
    j = jobs.get(res["job"])
    assert j["state"] == "draft" and not j["approved"] and j["created_by"] == f"agent:{aid}"
    it = inbox.item(res["inbox"])
    assert it["kind"] == "schedule" and it["approveLabel"] == "Turn on"
    assert any("Runs: board rows query sessions (just this command, no model)" in c for c in it["changes"])
    assert any("stopped after 30s" in c for c in it["changes"]) and any("one command each, no model call" in c for c in it["changes"])
    assert '["board", "rows", "query", "sessions"]' in it["detail"]  # 整条命令照原样写在卡上
    assert run(jobs.tick(at=when.now() + timedelta(days=1))) == []  # 没点头：到点也不跑
    cmd.model.script.append({"text": "Great, it's on."})

    async def approve():  # 同意了：服务器告诉它一声（这一轮调模型）；同一个事件循环里等这一轮回完
        await inbox.act(res["inbox"], inbox.ActIn(action="approve"))
        return await wait_run(aid)
    assert run(approve()).origin == "auto" and len(cmd.model.requests) == 1
    k = jobs.get(j["id"])
    assert k["state"] == "on" and k["approved"]
    due_fire(j["id"])
    assert runs(j["id"])[-1]["status"] == "ok"

    async def by_hand():  # 它自己手动跑一次：回的是「输出记在运行记录里」，不说发进对话
        said = await t.run({"action": "run", "id": j["id"]}, ctx(aid))
        await jobs.settle()
        return said
    assert "run log" in run(by_hand()) and any(r["kind"] == "manual" and r["status"] == "ok" for r in runs(j["id"]))
    assert len(cmd.model.requests) == 1  # 到点跑的、手动跑的都不调模型
    # 换命令是大改：先存着、交卡，原来那一版照常跑
    ch = run(t.run({"action": "update", "id": j["id"], "command": {"tool": "board", "args": ["rows", "query", "goals"]}}, ctx(aid)))
    assert ch["status"] == "approval-pending"
    k = jobs.get(j["id"])
    assert k["command"]["args"][-1] == "sessions" and k["pending"]["command"]["args"][-1] == "goals" and k["state"] == "on"
    assert any(c.startswith("Command: board rows query sessions (30s) → board rows query goals") for c in inbox.item(ch["inbox"])["changes"])
    with pytest.raises(engine.ToolFailure):  # 调模型的和只跑命令的不能互相改
        run(t.run({"action": "update", "id": j["id"], "task": "Ask the user how practice went."}, ctx(aid)))


def test_the_card_warns_when_the_command_normally_needs_an_ok(cmd):
    """平时要点头的命令（撤不回、对外：board alert delete）：卡上说清开了以后每次都直接跑；撤得回的（rows delete 是软删除）不用说。"""
    import inbox
    aid = agent("g-cmd10", "Practice")
    t = jobs.tool(aid)
    risky = run(t.run({"action": "create", "when": "every day 21:00", "command": {"tool": "board", "args": ["alert", "delete", "al-1"]}}, ctx(aid)))
    assert any("runs every time without asking" in c for c in inbox.item(risky["inbox"])["changes"])
    soft = run(t.run({"action": "create", "when": "every day 21:00", "command": {"tool": "board", "args": ["rows", "delete", "r-1"]}}, ctx(aid)))
    assert not any("without asking" in c for c in inbox.item(soft["inbox"])["changes"])


def test_old_tables_get_the_new_columns(tmp_path, monkeypatch):
    """10/19 以前建的库：engine_jobs 没有 command、engine_job_runs 没有 output，ready() 补上。"""
    import sqlite3

    import config
    db = tmp_path / "old.db"
    old = jobs.SCHEMA.replace(",\n  command TEXT);", ");").replace(", usage TEXT, output TEXT);", ", usage TEXT);")
    assert "command TEXT" not in old and "output TEXT" not in old
    conn = sqlite3.connect(db)
    conn.executescript(old)
    conn.close()
    monkeypatch.setattr(config.settings, "db", db)
    jobs.ready()
    conn = sqlite3.connect(db)
    assert "command" in {r[1] for r in conn.execute("PRAGMA table_info(engine_jobs)")}
    assert "output" in {r[1] for r in conn.execute("PRAGMA table_info(engine_job_runs)")}
    conn.close()


def test_the_output_kept_in_the_log_is_clipped():
    assert jobs.output_text("ok", "") == "ok" and jobs.output_text("", "err") == "err"
    assert jobs.output_text("a", "b") == "stdout:\na\n\nstderr:\nb"
    long = jobs.output_text("x" * 10_000, "")
    assert len(long) < jobs.OUTPUT_MAX + 100 and "omitted" in long


def test_settle_waits_for_command_runs(cmd):
    aid = agent("g-cmd11", "Practice")
    j = user_cmd_job(aid, {"tool": "board", "args": ["show"]})

    async def go():
        assert await jobs.tick(at=when.parse_iso(j["next_run"]) + timedelta(seconds=1)) == [j["id"]]
        assert jobs.get(j["id"])["running"]
        await jobs.settle()
        return jobs.get(j["id"])
    k = asyncio.run(go())
    assert not k["running"] and k["last_status"] == "ok"


# —— 活动页：到点跑成了的那次不列（engine/appdata.py engine_items）——————————————————————————————

def test_quiet_runs_stay_off_the_activity_page_but_stay_in_the_log(cmd):
    from engine import appdata
    aid = agent("g-cmd14", "Practice")
    bad = user_cmd_job(aid, {"tool": "board", "args": ["show", "--fail"]})
    due_fire(bad["id"])  # 没跑成的
    jobs.pause(bad["id"])  # 别跟着下面的 tick 再跑
    quiet = user_cmd_job(aid, {"tool": "board", "args": ["show"]})
    for _ in range(3):
        due_fire(quiet["id"])  # 到点跑成了，什么也没说

    async def by_hand():
        assert (await jobs.api_action(quiet["id"], "run"))["ok"]
        await jobs.settle()
    run(by_hand())
    assert sorted((r["kind"], r["status"]) for r in runs(quiet["id"])) == [("manual", "ok")] + [("on_time", "ok")] * 3  # 运行记录里一次不少
    shown = [x for _, x in appdata.engine_items(200, {}) if x.get("job") in (quiet["id"], bad["id"])]
    assert [(x["job"], x["run"], x["kind"]) for x in shown] == [(quiet["id"], "manual", "cron"), (bad["id"], "on_time", "failed")]
    assert "failed: exit code 3: boom" in shown[1]["text"]
    newest = [x["job"] for _, x in appdata.engine_items(2, {}) if x.get("source") == "job"]
    assert newest == [quiet["id"], bad["id"]]  # 不列的不占 limit：没跑成的那次后面又跑成了 3 次，它还在最新的两条里


def test_the_activity_page_reads_an_old_library_before_the_job_loop_does(tmp_path, monkeypatch):
    """10/19 以前建的库，服务刚起、jobs.loop 还没补上 command 那一栏，活动页先被问到：照样读得出来（engine_items 先 ready()）。"""
    import sqlite3

    import config
    from engine import appdata
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.executescript(jobs.SCHEMA.replace(",\n  command TEXT);", ");").replace(", usage TEXT, output TEXT);", ", usage TEXT);"))
    conn.execute("INSERT INTO engine_job_runs(id, job, agent, started_at, ended_at, kind, status, error) VALUES(?,?,?,?,?,?,?,?)",
                 ("jr-old", "job-old", "main", "2026-10-18T09:00:00+00:00", "2026-10-18T09:00:05+00:00", "on_time", "error", "exit code 1"))
    conn.commit()
    conn.close()
    monkeypatch.setattr(config.settings, "db", db)
    with store.tx():
        pass  # 内核别的表（engine_turns……）：用过内核的库都有
    assert [x["id"] for _, x in appdata.engine_items(10, {})] == ["jr-old"]
    conn = sqlite3.connect(db)
    assert "command" in {r[1] for r in conn.execute("PRAGMA table_info(engine_jobs)")}
    conn.close()


# —— README 里的例子：开练前一小时问一句 ——————————————————————————————————————————————————

def readme_example(readme: str, name: str) -> str:
    from pathlib import Path
    text = (Path(__file__).resolve().parent.parent / readme).read_text(encoding="utf8")
    return text.split(f"<!-- example: {name} -->\n```python\n", 1)[1].split("\n```", 1)[0] + "\n"


@pytest.fixture
def fake_server():
    """一个假的 OpenMousse 服务（只认 GET /api/schedule 和 POST /api/chat/trigger），起在 127.0.0.1 的随机端口。"""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    st = {"events": [], "triggers": [], "auth": []}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def reply(self, body: dict) -> None:
            raw = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            st["auth"].append(self.headers.get("Authorization"))
            self.reply({"ok": True, "events": st["events"]} if self.path.startswith("/api/schedule") else {"ok": False})

        def do_POST(self):
            st["auth"].append(self.headers.get("Authorization"))
            if self.path == "/api/chat/trigger":
                st["triggers"].append(json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0))))
            self.reply({"ok": True})

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    st["port"] = srv.server_address[1]
    yield st
    srv.shutdown()


def test_the_readme_example_asks_once_an_hour_before_practice(cmd, fake_server, tmp_path, monkeypatch):
    """README 的例子（中英文两份是同一段）当成只跑命令的任务每 5 分钟跑：一小时内有训练就经 /api/chat/trigger 问一句、只问一次；
    别的事、太远的训练都不管；没要问的什么都不发。"""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    code = readme_example("README.md", "practice_check.py")
    assert code == readme_example("README.zh-CN.md", "practice_check.py")
    script = tmp_path / "scripts" / "practice_check.py"
    script.parent.mkdir()
    script.write_text(code, encoding="utf8")
    script.chmod(0o755)
    conf = tmp_path / "script-server.json"  # 脚本读的 server.json：指到假的服务
    conf.write_text(json.dumps({"bind": {"host": "0.0.0.0", "port": fake_server["port"]}, "auth": {"tokens": {"local": "tok-local"}},
                                "timezone": "UTC", "language": "en"}), encoding="utf8")
    monkeypatch.setenv("MOUSSE_SERVER_CONFIG", str(conf))
    now = datetime.now(ZoneInfo("UTC"))

    def event(eid: str, title: str, minutes: int, kind: str = "event") -> dict:
        at = now + timedelta(minutes=minutes)
        return {"id": eid, "kind": kind, "title": title, "date": at.date().isoformat(), "start": at.strftime("%H:%M"), "done": False}
    fake_server["events"] = [event("item:7", "Rowing practice", 40), event("item:8", "Budget review", 30),
                             event("item:9", "Evening training", 180), event("item:10", "Practice report due", 20, kind="deadline")]
    cmd.set(jobs={"scripts": [str(script)]})
    aid = agent("g-cmd12", "Practice")
    j = user_cmd_job(aid, {"script": str(script), "timeout": 30})
    due_fire(j["id"])
    first = runs(j["id"])[-1]
    start = fake_server["events"][0]["start"]
    assert first["status"] == "ok" and first["output"] == f"asked about Rowing practice at {start}"
    assert fake_server["triggers"] == [{"thread": "main", "level": "quiet",
                                        "text": f"[An hour before practice] \"Rowing practice\" starts at {start}: ask the user how they feel "
                                                "today and whether to adjust it."}]
    assert set(fake_server["auth"]) == {"Bearer tok-local"}
    due_fire(j["id"])  # 5 分钟以后：问过了，不再问
    assert runs(j["id"])[-1]["status"] == "ok" and runs(j["id"])[-1]["output"] == "" and len(fake_server["triggers"]) == 1
    assert cmd.model.requests == []
