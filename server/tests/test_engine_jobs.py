"""内核第 6 项：定时任务（engine/jobs.py、when.py、unattended.py）和心跳（engine/heartbeat.py）。
模型是假的（FakeModel 按剧本回放），时钟是假的（tick(at=…)），推送只记下来不发。"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

import config
import engine
from engine import heartbeat, jobs, store, tools, unattended, when
from engine.errors import EngineError
from engine.session import DbSession, MemorySession, day_key, text_of, user
from stored_values import ASSISTANT_ROLE
from test_engine import FakeModel

UTC = ZoneInfo("UTC")


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def quiet_jobs():
    """库是所有测试共用的：别的测试留下的任务先停掉，免得被这里的 tick 跑起来。"""
    jobs.ready()
    heartbeat.ready()
    with store.tx() as conn:
        conn.execute("UPDATE engine_jobs SET state='paused' WHERE state='on'")
        conn.execute("DELETE FROM engine_heartbeats")
    jobs._running.clear()
    jobs._trigger_refs.clear()
    jobs._down.update(since=None, started=None)
    heartbeat._running.clear()
    yield


@pytest.fixture
def fake(monkeypatch):
    from engine import providers

    def make(script):
        f = FakeModel(script)
        monkeypatch.setattr(providers, "stream", f.stream)
        return f
    return make


@pytest.fixture
def pushes(monkeypatch):
    import push
    sent: list[dict] = []

    async def fake_send(title, body, data=None, thread_id=None, **kw):
        sent.append({"title": title, "body": body, "data": data, **kw})
        return {"ok": True, "sent": 1, "level": kw.get("level")}
    monkeypatch.setattr(push, "send_push", fake_send)
    return sent


@pytest.fixture
def builtin(server_json):
    def make(extra: dict | None = None):
        server_json({"claw": {"kind": "builtin"}, "engine": {"model": "deepseek/deepseek-flash", **(extra or {})}})
    make()
    tools.install_hooks()
    return make


def agent(aid: str, name: str = "Water") -> str:
    import chat
    import data
    with chat._lock, data.ddb() as conn:
        conn.execute("INSERT OR REPLACE INTO groups(id, name, icon, purpose, created_at) VALUES(?,?,?,?,?)",
                     (aid, name, "drop", "Reminders and check-ins.", chat.now_iso()))
    return aid


def ctx(aid: str, conv: str | None = None, turn: str = "t-x") -> engine.ToolCtx:
    return engine.ToolCtx(turn=turn, kind="owner", agent=aid, conversation=conv or aid, call_id="c", control=engine.Control())


def msgs(thread: str) -> list[dict]:
    import chat
    with chat._lock, chat.db() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM messages WHERE thread=? ORDER BY id", (thread,))]


def wait_run(thread: str, timeout: float = 10):
    import chat

    async def go():
        t0 = time.time()
        while time.time() - t0 < timeout:
            r = chat.RUNS.get(thread)
            if r and r.done:
                return r
            await asyncio.sleep(0.02)
        raise AssertionError("run did not finish")
    return go()


def user_job(aid: str, **given) -> dict:
    """用户在 app 里建的（直接开）。"""
    out = run(jobs.create(aid, {"task": "Remind the user to drink a glass of water.", "when": "every 2m", **given}, by_user=True))
    return jobs.get(out["job"]["id"])


def runs(jid: str) -> list[dict]:
    return list(reversed(jobs.runs_of(jid)))


def fire(at, *, beat: bool = False) -> list[str]:
    """tick 再等它跑完：同一个事件循环里（asyncio.run 一结束，没跑完的后台任务就被取消了）。"""
    async def go():
        started = await (heartbeat.tick(at=at) if beat else jobs.tick(at=at))
        await (heartbeat.settle() if beat else jobs.settle())
        return started
    return run(go())


# —— 时间写法和下一次 ——————————————————————————————————————————————————————————

def test_when_reads_the_usual_forms():
    t0 = datetime(2026, 10, 16, 20, 5, tzinfo=UTC)

    def p(s):
        return when.parse(s, t0)
    assert p("every 2m") == {"kind": "every", "minutes": 2, "text": "every 2m"}
    assert p("每两分钟")["minutes"] == 2 and p("每 2 分钟")["minutes"] == 2 and p("每小时")["minutes"] == 60 and p("30m")["minutes"] == 30
    assert p("every hour")["minutes"] == 60 and p("hourly")["minutes"] == 60 and p("every day")["minutes"] == 1440
    assert p("every day 9am") == {"kind": "daily", "at": "09:00", "days": [], "text": "every day 9am"}
    assert p("daily 21:30")["at"] == "21:30" and p("每天晚上9点半")["at"] == "21:30" and p("每天早上9点")["at"] == "09:00"
    assert p("weekdays at 9:30pm") == {"kind": "daily", "at": "21:30", "days": [0, 1, 2, 3, 4], "text": "weekdays at 9:30pm"}
    assert p("每周一、三 18:00")["days"] == [0, 2] and p("工作日 9:00")["days"] == [0, 1, 2, 3, 4] and p("周末10点")["days"] == [5, 6]
    assert p("mon,wed 18:00")["days"] == [0, 2] and p("every monday 9am")["days"] == [0] and p("星期五晚上8点")["at"] == "20:00"
    assert p("in 30m") == {"kind": "once", "at": "2026-10-16T20:35:00+00:00", "text": "in 30m"}
    assert p("2分钟后")["at"] == "2026-10-16T20:07:00+00:00" and p("半小时后")["at"] == "2026-10-16T20:35:00+00:00"
    assert p("tomorrow 9am")["at"] == "2026-10-17T09:00:00+00:00" and p("明天早上8点")["at"] == "2026-10-17T08:00:00+00:00"
    assert p("2026-10-17 15:00")["at"] == "2026-10-17T15:00:00+00:00" and p("2026-10-17T15:00:00+01:00")["at"] == "2026-10-17T14:00:00+00:00"
    assert p("0 9 * * 1-5") == {"kind": "cron", "expr": "0 9 * * 1-5", "text": "0 9 * * 1-5"}
    for bad in ("", "remind me at nine pm", "every monday", "0 25 * * *", "every 0m", "每周", "*/0 * * * *"):
        with pytest.raises(when.WhenError):
            p(bad)


def test_when_describes_in_plain_words():
    assert when.describe(when.parse("every 2m")) == "every 2 minutes" and when.describe(when.parse("every 2h")) == "every 2 hours"
    assert when.describe(when.parse("weekdays 9am")) == "weekdays 09:00" and when.describe(when.parse("mon,wed 18:00")) == "Mon, Wed 18:00"
    assert when.describe(when.parse("2026-10-17 15:00")) == "once on 17 Oct at 15:00"
    assert when.runs_per_day(when.parse("every 2m")) == 720 and when.runs_per_day(when.parse("in 5m")) is None


def test_next_run_every_stays_on_its_lattice():
    t = datetime(2026, 10, 16, 10, 0, tzinfo=UTC)
    every = when.parse("every 2m")
    assert when.next_after(every, t) == t + timedelta(minutes=2)  # 第一次：开的时候 + 一个周期
    assert when.next_after(every, t + timedelta(minutes=3), anchor=t) == t + timedelta(minutes=4)  # 晚了：排到下一格，不越漂越远
    assert when.next_after(every, t, anchor=t + timedelta(minutes=2)) == t + timedelta(minutes=2)  # 还没到
    once = when.parse("in 5m", t)
    assert when.next_after(once, t) == t + timedelta(minutes=5) and when.next_after(once, t + timedelta(minutes=5)) is None


def test_next_run_cron_and_days():
    fri = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)
    assert when.next_after(when.parse("0 9 13 * 1"), fri) == datetime(2026, 10, 19, 9, 0, tzinfo=UTC)  # 13 号或周一，对上哪个都行
    assert when.next_after(when.parse("30 8 1 * *"), fri) == datetime(2026, 11, 1, 8, 30, tzinfo=UTC)
    assert when.next_after(when.parse("*/15 * * * *"), fri) == datetime(2026, 10, 16, 12, 15, tzinfo=UTC)
    assert when.next_after(when.parse("0 0 * * 7"), fri) == datetime(2026, 10, 18, 0, 0, tzinfo=UTC)  # 周日写成 7
    assert when.next_after(when.parse("0 9 * jan,feb mon"), fri) == datetime(2027, 1, 4, 9, 0, tzinfo=UTC)
    assert when.next_after(when.parse("weekdays 9am"), fri) == datetime(2026, 10, 19, 9, 0, tzinfo=UTC)
    assert when.next_after(when.parse("every day 9am"), datetime(2026, 10, 16, 9, 0, 30, tzinfo=UTC)) == datetime(2026, 10, 17, 9, 0, tzinfo=UTC)


def test_daily_keeps_the_wall_clock_across_dst(monkeypatch):
    lon = ZoneInfo("Europe/London")
    monkeypatch.setattr(config.settings, "tz", lon)
    daily = when.parse("every day 01:30")
    # 2026-10-25 02:00 夏令时结束：01:30 有两次，只跑第一次，下一次是第二天
    first = when.next_after(daily, datetime(2026, 10, 25, 0, 0, tzinfo=lon))
    assert first.utcoffset() == timedelta(hours=1) and (first.hour, first.minute) == (1, 30)
    second = when.next_after(daily, first)
    assert second.date() == date(2026, 10, 26) and (second.hour, second.minute) == (1, 30)
    # 2027-03-28 01:00 夏令时开始：01:30 不存在，落在跳完以后（01:30 GMT = 02:30 BST）
    gap = when.next_after(daily, datetime(2027, 3, 27, 12, 0, tzinfo=lon))
    assert gap.astimezone(UTC) == datetime(2027, 3, 28, 1, 30, tzinfo=UTC)
    # 每隔两小时按真实经过的时间算
    ev = when.parse("every 2h")
    a = datetime(2026, 10, 25, 0, 30, tzinfo=lon)
    b = when.next_after(ev, a)
    assert b.astimezone(UTC) == a.astimezone(UTC) + timedelta(hours=2) and (b.hour, b.fold) == (1, 1)  # 钟面上只过了一小时：01:30 的第二次


def test_grace_is_half_the_period_clamped():
    assert when.grace_seconds(when.parse("every 2m")) == 120
    assert when.grace_seconds(when.parse("every 1h")) == 1800
    assert when.grace_seconds(when.parse("every day")) == 7200 and when.grace_seconds(when.parse("every day 9am")) == 7200
    once = when.parse("in 5m")
    assert when.grace_seconds(once) == 120 and when.grace_seconds(once, outage=True) == 7200


def test_silence_markers():
    s = unattended.silent
    for yes in ("[SILENT]", "NO_REPLY", " no_reply. ", "[SILENT] nothing new", "Nothing new.\nNO_REPLY", "静默", "HEARTBEAT_OK",
                "**HEARTBEAT_OK**", "All good. HEARTBEAT_OK"):
        assert s(yes), yes
    for no in ("Time for a glass of water", "I'll answer NO_REPLY only when nothing changes", "", "HEARTBEAT_OK " + "x" * 400, "[SILENT"):
        assert not s(no), no


# —— Agent 建的先停着，用户点了才开 ——————————————————————————————————————————————

def test_agent_job_stays_off_until_the_user_turns_it_on(builtin, fake, pushes):
    import chat
    import inbox
    aid = agent("g-water1")
    f = fake([{"text": "Sure.", "calls": [("c1", "cron", {"action": "create", "name": "Water", "when": "every 2m",
                                                           "task": "Remind the user to drink a glass of water.", "why": "You asked for it."})]},
              {"text": "It's waiting for your OK."},
              {"text": "Great, it's on."},
              {"text": "💧 Time for a glass of water."}])

    async def go():
        chat.start_run(aid, "remind me to drink water every 2 minutes", None)
        first = await wait_run(aid)
        assert first.text.endswith("It's waiting for your OK.")
        res = json.loads(text_of(f.requests[1][1].messages[-1]))
        assert res["status"] == "approval-pending" and res["job"].startswith("job-") and res["inbox"].startswith("ib-")
        j = jobs.get(res["job"])
        assert j["state"] == "draft" and not j["approved"] and j["next_run"] is None and j["created_by"] == f"agent:{aid}"
        assert j["session"] == "isolated" and j["deliver"] == aid and j["level"] == "quiet"
        it = inbox.item(res["inbox"])
        assert it["kind"] == "schedule" and it["status"] == "pending" and it["thread"] == aid and it["messageId"] == first.reply_id
        assert it["approveLabel"] == "Turn on" and it["why"] == "You asked for it."
        assert any("first run 2 minutes after you turn it on; about 720 runs a day" in c for c in it["changes"]) and it["schedule"]["perDay"] == 720
        assert "Next runs" not in it["detail"]  # 每隔多久的：几点要看什么时候点「开」
        assert await jobs.tick(at=when.now() + timedelta(days=2)) == []  # 草稿到点也不跑
        out = await inbox.act(res["inbox"], inbox.ActIn(action="approve"))
        assert out["item"]["status"] == "done" and "it's on" in out["item"]["result"]
        kick = await wait_run(aid)  # 同意了：服务器告诉它一声（已经生效、不用再报 done）
        assert kick.origin == "auto" and kick.text == "Great, it's on."
        j = jobs.get(j["id"])
        assert j["state"] == "on" and j["approved"] and j["inbox"] is None
        due = when.parse_iso(j["next_run"])
        assert abs((due - when.now()).total_seconds() - 120) < 10
        assert await jobs.tick(at=due + timedelta(seconds=1)) == [j["id"]]
        await jobs.settle()
        return j["id"], due
    jid, due = run(go())
    j = jobs.get(jid)
    assert j["runs"] == 1 and j["last_status"] == "ok" and when.parse_iso(j["next_run"]) == due + timedelta(minutes=2)
    m = msgs(aid)
    assert m[-2]["role"] == "auto" and m[-2]["text"] == "【自动触发】Scheduled: Water" and "ran on its own" in m[-2]["gw_text"]
    assert m[-1]["role"] == ASSISTANT_ROLE and m[-1]["text"] == "💧 Time for a glass of water." and m[-1]["origin"] == "auto"
    assert pushes[-1]["body"] == "💧 Time for a glass of water." and pushes[-1]["level"] == "quiet" and pushes[-1]["kind"] == "reply"
    req = f.requests[3][1]  # 到点那一轮：新会话、没有 cron 工具、照常的开头
    assert [m["role"] for m in req.messages] == ["user"] and "Nobody is watching this run" in text_of(req.messages[0])
    assert "cron" not in {t["name"] for t in req.tools} and 'You are the Agent "Water"' in req.system and "<available_skills>" in req.system
    r = runs(jid)[-1]
    assert r["status"] == "ok" and r["kind"] == "on_time" and r["message"] == m[-1]["id"] and r["turn"]
    s = DbSession.latest(day_key(aid, __import__("chat").day_of(__import__("chat").now_iso())))  # 今天的会话里补了一条 system
    last = json.loads(s.path()[-1]["data"])
    assert last["role"] == "system" and last.get("mirror") == f"cron:{jid}" and "💧 Time for a glass of water." in text_of(last)


def test_declined_job_is_not_proposed_again(builtin):
    import inbox
    aid = agent("g-water2")
    t = jobs.tool(aid)
    args = {"action": "create", "when": "every day 9am", "task": "Send the user a short plan for the day."}
    res = run(t.run(args, ctx(aid)))
    run(inbox.act(res["inbox"], inbox.ActIn(action="reject", note="not now")))
    assert jobs.get(res["job"])["state"] == "rejected"
    with pytest.raises(engine.ToolFailure) as e:
        run(t.run(args, ctx(aid)))
    assert "declined this exact job" in str(e.value)
    again = run(t.run({**args, "when": "every day 10am"}, ctx(aid)))  # 不一样的可以再提
    assert again["status"] == "approval-pending" and again["inbox"] != res["inbox"]
    same = run(t.run({**args, "when": "every day 10am"}, ctx(aid)))  # 一模一样的还在等：还是那张卡
    assert same["inbox"] == again["inbox"] and "already waiting" in same["message"]


def test_edited_card_is_not_acted_on(builtin):
    import chat
    import inbox
    aid = agent("g-water3")
    res = run(jobs.tool(aid).run({"action": "create", "when": "every 1h", "task": "Check in with the user."}, ctx(aid)))
    with chat._lock, inbox.idb() as conn:
        conn.execute("UPDATE inbox SET title='Something harmless' WHERE id=?", (res["inbox"],))
    out = run(inbox.act(res["inbox"], inbox.ActIn(action="approve")))
    assert out["item"]["status"] == "failed" and "edited" in out["item"]["result"]
    assert jobs.get(res["job"])["state"] == "draft"


def test_big_changes_wait_small_ones_apply(builtin):
    import inbox
    aid = agent("g-upd")
    j = user_job(aid, when="every 1h", name="Check-in")
    t = jobs.tool(aid)
    out = run(t.run({"action": "update", "id": j["id"], "name": "Hydrate", "level": "none"}, ctx(aid)))
    assert out["message"].startswith("Done") and jobs.get(j["id"])["name"] == "Hydrate" and jobs.get(j["id"])["level"] == "none"
    out = run(t.run({"action": "update", "id": j["id"], "when": "every 30m", "why": "Twice as often."}, ctx(aid)))
    assert out["status"] == "approval-pending"
    k = jobs.get(j["id"])
    assert k["schedule"]["minutes"] == 60 and k["pending"]["schedule"]["minutes"] == 30 and k["state"] == "on"  # 原来那一版照常跑
    it = inbox.item(out["inbox"])
    assert it["title"] == 'Change the scheduled job "Hydrate"' and any("every hour → every 30 minutes" in c for c in it["changes"])
    run(inbox.act(out["inbox"], inbox.ActIn(action="approve")))
    k = jobs.get(j["id"])
    assert k["schedule"]["minutes"] == 30 and k["pending"] is None and k["state"] == "on"
    louder = run(t.run({"action": "update", "id": j["id"], "level": "ring"}, ctx(aid)))
    assert louder["status"] == "approval-pending" and jobs.get(j["id"])["level"] == "none"
    # 删掉开着的：先问；不同意 = 照常跑，同一件事 30 天内别再提
    d = run(t.run({"action": "delete", "id": j["id"]}, ctx(aid)))
    assert d["status"] == "approval-pending" and jobs.get(j["id"])["state"] == "on"
    run(inbox.act(d["inbox"], inbox.ActIn(action="reject")))
    assert jobs.get(j["id"])["pending"] is None and jobs.get(j["id"])["state"] == "on"
    with pytest.raises(engine.ToolFailure) as e:
        run(t.run({"action": "delete", "id": j["id"]}, ctx(aid)))
    assert "said no" in str(e.value)


def test_delete_and_pause_resume(builtin):
    import inbox
    aid = agent("g-del")
    t = jobs.tool(aid)
    draft = run(t.run({"action": "create", "when": "every 1h", "task": "Check in."}, ctx(aid)))
    assert run(t.run({"action": "delete", "id": draft["job"]}, ctx(aid)))["message"] == "Deleted."
    assert jobs.get(draft["job"])["state"] == "withdrawn" and inbox.item(draft["inbox"])["status"] == "withdrawn"  # 草稿直接撤
    j = user_job(aid)
    assert run(t.run({"action": "pause", "id": j["id"]}, ctx(aid))).startswith("Paused") and jobs.get(j["id"])["state"] == "paused"
    assert run(t.run({"action": "resume", "id": j["id"]}, ctx(aid))).startswith("Resumed") and jobs.get(j["id"])["state"] == "on"
    d = run(t.run({"action": "delete", "id": j["id"]}, ctx(aid)))
    run(inbox.act(d["inbox"], inbox.ActIn(action="approve")))
    assert jobs.get(j["id"])["state"] == "deleted" and jobs.get(j["id"])["next_run"] is None
    other = agent("g-other")
    mine = user_job(other)
    with pytest.raises(engine.ToolFailure):  # 别的 Agent 的碰不到
        run(t.run({"action": "pause", "id": mine["id"]}, ctx(aid)))
    with pytest.raises(engine.ToolFailure):  # 还没同意的草稿不能自己开
        d2 = run(t.run({"action": "create", "when": "every 3h", "task": "Stretch."}, ctx(aid)))
        run(t.run({"action": "resume", "id": d2["job"]}, ctx(aid)))


def test_revise_on_a_job_card_points_at_cron(builtin):
    import inbox
    aid = agent("g-rev")
    res = run(jobs.tool(aid).run({"action": "create", "when": "every 1h", "task": "Check in."}, ctx(aid)))
    it = inbox.item(res["inbox"])
    assert "cron tool's update" in inbox.revise_text(it, "make it every 2 hours") and res["job"] in inbox.revise_text(it, "x")
    got = inbox.reply_context(res["inbox"])
    assert got and "cron tool's update" in got[1]


# —— 到点：先推进再跑、补跑、一次的、重启、不说话 —————————————————————————————————————————

def test_next_run_moves_before_the_run_and_no_double_start(builtin, fake, pushes):
    aid = agent("g-adv")
    j = user_job(aid)
    fake([{"chunks": ["a", "b", "c"], "delay": 0.2}])
    due = when.parse_iso(j["next_run"])

    async def go():
        assert await jobs.tick(at=due + timedelta(seconds=1)) == [j["id"]]
        k = jobs.get(j["id"])
        assert k["running"] and when.parse_iso(k["next_run"]) == due + timedelta(minutes=2)  # 还没跑完就推进了
        assert await jobs.tick(at=due + timedelta(seconds=2)) == []  # 正在跑：不再起一个
        await jobs.settle()
    run(go())
    k = jobs.get(j["id"])
    assert not k["running"] and k["runs"] == 1 and msgs(aid)[-1]["text"] == "abc"


def test_late_runs_catch_up_once_or_skip(builtin, fake, pushes):
    aid = agent("g-late")
    j = user_job(aid)
    old = when.parse_iso(when.iso(when.now() - timedelta(hours=3)))  # 库里存到秒
    jobs._write(j["id"], next_run=when.iso(old))
    fake([{"text": "Water time (late)."}])
    at = when.now()
    assert fire(at) == [j["id"]]
    r = runs(j["id"])[-1]
    nxt = when.parse_iso(jobs.get(j["id"])["next_run"])
    assert r["kind"] == "catch_up" and r["status"] == "ok" and at < nxt <= at + timedelta(minutes=2)
    assert ((nxt - old).total_seconds() % 120) == 0  # 攒下的几十次合成这一次，下一次还在原来的格子上
    builtin({"jobs": {"catch_up_missed": False}})
    jobs._write(j["id"], next_run=when.iso(old))
    assert run(jobs.tick(at=at)) == []
    assert runs(j["id"])[-1]["status"] == "skipped" and when.parse_iso(jobs.get(j["id"])["next_run"]) > at
    # 晚了一点（补跑窗口以内）照跑
    builtin()
    fake([{"text": "On time."}])
    jobs._write(j["id"], next_run=when.iso(at - timedelta(seconds=60)))
    assert fire(at) == [j["id"]]
    assert runs(j["id"])[-1]["kind"] == "on_time"


def test_one_shot_misses_unless_the_server_was_down(builtin, fake, pushes):
    aid = agent("g-once")
    j = user_job(aid, when="in 30m", task="Remind the user to call the bank.")
    due = when.parse_iso(j["next_run"])
    assert run(jobs.tick(at=due + timedelta(minutes=5))) == []  # 服务器开着却晚了 5 分钟（不会发生，除非卡住了）：记成错过
    k = jobs.get(j["id"])
    assert k["state"] == "missed" and runs(j["id"])[-1]["status"] == "missed"
    j2 = user_job(aid, when="in 30m", task="Remind the user to call the bank.")
    due2 = when.parse_iso(j2["next_run"])
    jobs._down.update(since=due2 - timedelta(hours=1), started=due2 + timedelta(minutes=40))  # 那会儿服务器没开着
    fake([{"text": "Call the bank (40 minutes late)."}])
    assert fire(due2 + timedelta(minutes=40)) == [j2["id"]]
    k = jobs.get(j2["id"])
    assert k["state"] == "done" and k["next_run"] is None and runs(j2["id"])[-1]["kind"] == "late"


def test_restart_marks_interrupted_and_keeps_the_schedule(builtin):
    aid = agent("g-boot")
    j = user_job(aid)
    due = when.parse_iso(j["next_run"])
    assert jobs._claim(j, j["next_run"], due + timedelta(minutes=2), due)  # 上一个进程：推进了、开跑了，然后崩了
    rid = jobs._add_run(j, due, "on_time", "running", due)
    o = user_job(aid, when="in 10m")
    assert jobs._claim(o, o["next_run"], None, when.parse_iso(o["next_run"]))
    jobs._meta("tick", when.iso(due))
    assert jobs.recover(at=due + timedelta(minutes=5)) >= 2
    k = jobs.get(j["id"])
    assert not k["running"] and k["last_status"] == "interrupted" and when.parse_iso(k["next_run"]) == due + timedelta(minutes=2)
    assert jobs.get(o["id"])["state"] == "done"  # 一次的：算跑过了，不重跑
    assert [r for r in runs(j["id"]) if r["id"] == rid][0]["status"] == "interrupted"
    assert jobs._down["since"] == when.parse_iso(when.iso(due))


def test_silent_reply_sends_nothing(builtin, fake, pushes):
    aid = agent("g-quiet")
    j = user_job(aid, task="Tell the user only if a deadline is within 24 hours.")
    before = len(msgs(aid))
    fake([{"text": "[SILENT]"}])
    fire(when.parse_iso(j["next_run"]) + timedelta(seconds=1))
    assert len(msgs(aid)) == before and pushes == [] and runs(j["id"])[-1]["status"] == "silent"


def test_failures_are_said_once_then_pause(builtin, fake, pushes):
    aid = agent("g-fail")
    j = user_job(aid)
    fake([EngineError("boom", kind="http")] * 3 * jobs.FAILS_TO_PAUSE)
    t = when.parse_iso(j["next_run"])
    for _ in range(jobs.FAILS_TO_PAUSE):
        fire(t + timedelta(seconds=1))
        t = when.parse_iso(jobs.get(j["id"])["next_run"])
    k = jobs.get(j["id"])
    assert k["state"] == "paused" and k["fails"] == jobs.FAILS_TO_PAUSE and k["last_status"] == "error"
    notes = [m["text"] for m in msgs(aid) if m["role"] == "auto"]
    assert sum("didn't run this time" in n for n in notes) == 1 and any("so it's paused" in n for n in notes) and pushes == []


def test_daily_limit_skips(builtin, fake, monkeypatch):
    aid = agent("g-limit")
    j = user_job(aid)
    builtin({"jobs": {"daily_limit": 3}})
    monkeypatch.setattr(jobs, "_today_runs", lambda at: 3)
    assert run(jobs.tick(at=when.parse_iso(j["next_run"]) + timedelta(seconds=1))) == []
    assert runs(j["id"])[-1]["status"] == "limit" and jobs.get(j["id"])["runs"] == 0


def test_times_stops_after_n_runs(builtin, fake, pushes):
    aid = agent("g-times")
    j = user_job(aid, times=2)
    fake([{"text": "one"}, {"text": "two"}])
    for _ in range(2):
        fire(when.parse_iso(jobs.get(j["id"])["next_run"]) + timedelta(seconds=1))
    k = jobs.get(j["id"])
    assert k["state"] == "done" and k["runs"] == 2 and k["next_run"] is None


def test_not_builtin_runs_nothing(server_json):
    jobs.ready()
    server_json({})
    assert run(jobs.tick(at=when.now() + timedelta(days=30))) == [] and run(heartbeat.tick()) == []


# —— chat 模式：在对话里跑，这一轮里不许再建定时任务 ——————————————————————————————————————

def test_chat_mode_runs_in_the_conversation_and_cannot_make_jobs(builtin, fake, pushes):
    aid = agent("g-chatjob")
    j = user_job(aid, when="every 1h", task="Ask the user how their day is going.", session="chat")
    f = fake([{"text": "", "calls": [("c1", "cron", {"action": "create", "when": "every 1h", "task": "Ask again."})]},
              {"text": "How's your day going?"}])
    fire(when.parse_iso(j["next_run"]) + timedelta(seconds=1))
    m = msgs(aid)
    assert j["name"] == "Ask the user how the…"  # 没起名字：取 task 的前 20 个字
    assert m[-2]["role"] == "auto" and m[-2]["text"] == f"【自动触发】Scheduled: {j['name']}" and "[Scheduled job]" in m[-2]["gw_text"]
    assert m[-1]["role"] == ASSISTANT_ROLE and m[-1]["text"] == "How's your day going?"
    tool_res = f.requests[1][1].messages[-1]
    assert tool_res["role"] == "tool" and tool_res["error"] and "scheduled job running" in text_of(tool_res)
    assert [x for x in jobs.listing(aid) if x["id"] != j["id"]] == []  # 没建出新的
    assert pushes[-1]["level"] == "quiet" and pushes[-1]["body"] == "How's your day going?"
    s = DbSession.latest(day_key(aid, __import__("chat").day_of(__import__("chat").now_iso())))
    assert any(r["role"] == "user" and json.loads(r["data"]).get("ref") == m[-2]["id"] for r in s.path())  # 这一轮在当天的会话里


def test_chat_mode_silent_leaves_no_trace(builtin, fake, pushes):
    aid = agent("g-chatquiet")
    j = user_job(aid, when="every 1h", task="Say something only if it rains.", session="chat")
    fake([{"text": "first"}])
    import chat

    async def first():
        chat.start_run(aid, "hello", None)
        await wait_run(aid)
    run(first())
    before = msgs(aid)
    pushes.clear()
    fake([{"text": "[SILENT]"}])
    fire(when.parse_iso(j["next_run"]) + timedelta(seconds=1))
    assert msgs(aid) == before and pushes == [] and runs(j["id"])[-1]["status"] == "silent"
    s = DbSession.latest(day_key(aid, chat.day_of(chat.now_iso())))
    assert [text_of(json.loads(r["data"])) for r in s.path() if r["kind"] == "message"][-2:] == ["hello", "first"]  # 会话退回去了


def test_chat_mode_runs_record_the_turn_and_its_usage(builtin, fake, pushes):
    """10/19 彩排：chat 模式的运行记录没有这一轮的 id 和用量（新会话那种一直有）。回了话的、「没要说的」都记。"""
    from engine import events
    aid = agent("g-chatturn")
    j = user_job(aid, when="every 1h", task="Ask the user what they had for lunch.", session="chat")
    fake([{"text": "What did you have for lunch?"}])
    fire(when.parse_iso(j["next_run"]) + timedelta(seconds=1))
    fake([{"text": "[SILENT]"}])
    fire(when.parse_iso(jobs.get(j["id"])["next_run"]) + timedelta(seconds=1))
    said, quiet = runs(j["id"])[-2:]
    newest, older = events.turns(aid)[:2]
    assert (said["status"], said["turn"], quiet["status"], quiet["turn"]) == ("ok", older["id"], "silent", newest["id"])
    assert said["usage"]["in"] == 10 and said["usage"]["out"] == 5 and said["usage"] == older["usage"] and quiet["usage"]["in"] == 10


# —— 工具给谁 ———————————————————————————————————————————————————————————————

def test_cron_tool_only_in_owner_turns(builtin, fake, server_json):
    assert "cron" in {t.name for t in tools.for_agent("main")} and "cron" not in {t.name for t in unattended.tools_for("main")}
    cron = next(t for t in tools.for_agent("main") if t.name == "cron")
    assert cron.kinds == ("owner",) and cron.level({"action": "list"})[0] == "read" and cron.level({"action": "delete"})[0] == "undo"
    f = fake([{"text": "hi"}])
    run(engine.run_turn(engine.Spec(kind="cyber", model="deepseek/x", tools=tools.for_agent("main"), gate=tools.gate), MemorySession(), user("hi")))
    assert f.requests[0][1].tools == []
    with pytest.raises(EngineError):
        run(engine.run_turn(engine.Spec(kind="outsider", model="deepseek/x", tools=tools.for_agent("main")), MemorySession(), user("x")))
    assert "only run in the owner's" in run(tools.gate(cron, {"action": "list"}, engine.ToolCtx(
        turn="t", kind="cyber", agent="main", conversation="c", call_id="1", control=engine.Control())))
    assert "## Scheduled jobs" in __import__("engine.owner", fromlist=["rules"]).rules("main", tools.for_agent("main"))
    server_json({"claw": {"kind": "builtin"}, "engine": {"tools": {"levels": {"cron": "off"}}}})
    assert "cron" not in {t.name for t in tools.for_agent("main")}


def test_deliver_must_be_your_own_chat(builtin):
    aid = agent("g-own")
    agent("g-someone")
    with pytest.raises(engine.ToolFailure):
        run(jobs.tool(aid).run({"action": "create", "when": "every 1h", "task": "x", "deliver": "g-someone"}, ctx(aid)))
    with pytest.raises(engine.ToolFailure):
        run(jobs.tool(aid).run({"action": "create", "when": "every 1h", "task": "x", "deliver": "main"}, ctx(aid)))
    res = run(jobs.tool("main").run({"action": "create", "when": "every 1h", "task": "x"}, ctx("main", conv="g-own")))
    assert jobs.get(res["job"])["deliver"] == "main"  # 在别人的对话里：投到自己的


def test_user_api_creates_jobs_that_are_on(builtin):
    aid = agent("g-api")
    out = run(jobs.api_create(jobs.JobIn(agent=aid, when="weekdays 9am", task="Send the user the day's plan.", level="ring")))
    j = out["job"]
    assert j["state"] == "on" and j["approved"] and j["createdBy"] == "user" and j["nextRun"] and j["level"] == "ring"
    assert run(jobs.api_action(j["id"], "pause"))["job"]["state"] == "paused"
    assert run(jobs.api_action(j["id"], "resume"))["job"]["state"] == "on"
    p = run(jobs.api_update(j["id"], jobs.JobIn(when="every day 8am")))
    assert p["job"]["schedule"]["at"] == "08:00" and p["job"]["state"] == "on"
    assert jobs.api_get(j["id"])["job"]["id"] == j["id"]
    assert run(jobs.api_delete(j["id"]))["ok"] and jobs.get(j["id"])["state"] == "deleted"


# —— 心跳 ————————————————————————————————————————————————————————————————

def test_active_hours():
    t = datetime(2026, 10, 16, 23, 30, tzinfo=UTC)
    assert heartbeat.in_active_hours(None, t) and heartbeat.in_active_hours(["22:00", "06:00"], t)
    assert not heartbeat.in_active_hours(["08:00", "22:00"], t) and heartbeat.in_active_hours(["08:00", "24:00"], t)
    assert not heartbeat.in_active_hours(["09:00", "09:00"], t) and heartbeat.in_active_hours(["bad", "22:00"], t)


def test_empty_checklist():
    assert heartbeat.effectively_empty("# Heartbeat\n\n- [ ]\n<!-- add things here -->\n```\n```\n")
    assert not heartbeat.effectively_empty("# Heartbeat\n- If lunch isn't logged by 14:00, remind the user")


def test_heartbeat_skips_without_a_checklist_and_stays_quiet(builtin, fake, pushes, tmp_path):
    aid = agent("g-hb1")
    builtin({"heartbeat": {aid: {"every": "1m"}}})
    t0 = when.now()
    fake([])  # 没清单：一次模型都不该调（调了 FakeModel 就报错）
    assert run(heartbeat.tick(at=t0)) == []  # 第一次：排上
    assert run(heartbeat.tick(at=t0 + timedelta(seconds=61))) == []
    assert heartbeat.state(aid)["last_reason"] == "empty"
    builtin({"heartbeat": {aid: {"every": "1m", "prompt": "If the user hasn't logged lunch by 14:00, remind them."}}})
    f = fake([{"text": "NO_REPLY"}, {"text": "You haven't logged lunch yet."}])
    before = len(msgs(aid))
    assert fire(t0 + timedelta(seconds=125), beat=True) == [aid]
    assert len(msgs(aid)) == before and pushes == [] and heartbeat.state(aid)["last_status"] == "silent"
    req = f.requests[0][1]
    assert "cron" not in {t["name"] for t in req.tools} and unattended.LIGHT_NOTE in req.system and "<available_skills>" not in req.system
    assert "Do not infer or repeat old tasks" in text_of(req.messages[0]) and "remind them" in text_of(req.messages[0])
    assert fire(t0 + timedelta(seconds=190), beat=True) == [aid]
    m = msgs(aid)
    assert m[-1]["text"] == "You haven't logged lunch yet." and m[-2]["text"] == "【自动触发】Checked in on its own"
    assert pushes[-1]["level"] == "quiet" and heartbeat.state(aid)["last_status"] == "ok"
    hb = [r for r in jobs.runs_of(f"heartbeat:{aid}")]
    assert [r["status"] for r in reversed(hb)] == ["silent", "ok"] and all(r["kind"] == "heartbeat" for r in hb)


def test_heartbeat_reads_heartbeat_md_and_respects_hours_and_busy(builtin, fake, pushes, tmp_path, server_json):
    import chat
    ws = tmp_path / "ws-hb2"
    ws.mkdir()
    (ws / "HEARTBEAT.md").write_text("# Checks\n- Remind the user about the 15:00 call\n", encoding="utf8")
    aid = agent("g-hb2")
    server_json({"claw": {"kind": "builtin"}, "engine": {"model": "deepseek/deepseek-flash", "heartbeat": {aid: {"every": "1m", "active": ["08:00", "09:00"]}}},
                 "agent_workspaces": {aid: str(ws)}})
    assert heartbeat.checklist(aid, heartbeat.config()[aid])[1] == "HEARTBEAT.md"
    t0 = datetime.now(UTC).replace(hour=12, minute=0, second=0, microsecond=0)
    fake([])
    run(heartbeat.tick(at=t0))
    run(heartbeat.tick(at=t0 + timedelta(seconds=61)))
    assert heartbeat.state(aid)["last_reason"] == "quiet-hours"
    server_json({"claw": {"kind": "builtin"}, "engine": {"model": "deepseek/deepseek-flash", "heartbeat": {aid: {"every": "1m"}}},
                 "agent_workspaces": {aid: str(ws)}})
    chat.RUNS[aid] = chat.Run(thread=aid, model="x", user_id=0, started=chat.now_iso())  # 这个 Agent 正在回话
    try:
        run(heartbeat.tick(at=t0 + timedelta(seconds=122)))
        assert heartbeat.state(aid)["last_reason"] == "busy"
    finally:
        chat.RUNS.pop(aid, None)
    f = fake([{"text": "HEARTBEAT_OK"}])
    assert fire(t0 + timedelta(seconds=183), beat=True) == [aid]
    assert heartbeat.state(aid)["last_status"] == "silent" and "15:00 call" in text_of(f.requests[0][1].messages[0])
    assert heartbeat.status()[aid]["checklist"] == "HEARTBEAT.md"


def test_heartbeat_uses_a_spendable_chat_plan(builtin, fake, pushes, server_json, monkeypatch):
    """没写心跳模型和 engine.use.background 时，对话模型是这一轮能花的套餐就用它。写了那两处仍以它们为准。定时任务照旧跟对话。
    ChatGPT 没同意后台：换成非套餐的；只有套餐、又花不了时这一次不跑、记成出错，一次模型都不调。"""
    import chat
    from engine import runners
    monkeypatch.setattr(runners, "_found", {"claude-code": {"bin": "/bin/false", "auth": "claude.ai", "billing": "subscription"}})  # 登录了
    aid = agent("g-hb3")
    chat.set_model(chat.ModelBody(thread=aid, model="claude-code/haiku"))
    builtin({"heartbeat": {aid: {"every": "1m", "prompt": "Remind the user to stretch."}}})
    assert heartbeat.model_of(aid, heartbeat.config()[aid]) == "claude-code/haiku"
    assert jobs.model_for(aid, None) == "claude-code/haiku"  # 定时任务照旧跟对话的模型
    builtin({"heartbeat": {aid: {"every": "1m", "model": "claude-code/opus"}}})
    assert heartbeat.model_of(aid, heartbeat.config()[aid]) == "claude-code/opus"  # 心跳上写明的优先
    builtin({"use": {"background": "deepseek/deepseek-flash"}, "heartbeat": {aid: {"every": "1m", "prompt": "Remind the user to stretch."}}})
    assert heartbeat.model_of(aid, heartbeat.config()[aid]) == "deepseek/deepseek-flash"  # engine.use.background 优先
    server_json({"claw": {"kind": "builtin"}, "engine": {"model": "claude-code/opus",
                                                         "heartbeat": {aid: {"every": "1m", "prompt": "Remind the user to stretch."}}}})
    assert heartbeat.model_of(aid, heartbeat.config()[aid]) == "claude-code/haiku"  # 只有套餐、这一轮花得了：用对话的
    server_json({"claw": {"kind": "builtin"}, "engine": {"model": "chatgpt/gpt-test", "models": ["deepseek/deepseek-flash"],
                                                         "heartbeat": {aid: {"every": "1m", "prompt": "Remind the user to stretch."}}}})
    monkeypatch.setattr(runners, "_found", {})  # 对话上的 claude-code 不在可选列表里，心跳看到的是默认的 ChatGPT 套餐
    assert heartbeat.model_of(aid, heartbeat.config()[aid]) == "deepseek/deepseek-flash"  # 没同意后台：换成按量的
    server_json({"claw": {"kind": "builtin"}, "engine": {"model": "chatgpt/gpt-test",
                                                         "heartbeat": {aid: {"every": "1m", "prompt": "Remind the user to stretch."}}}})
    assert heartbeat.model_of(aid, heartbeat.config()[aid]) == ""
    fake([])  # 调了模型 FakeModel 就报错
    t0 = when.now()
    run(heartbeat.tick(at=t0))
    assert fire(t0 + timedelta(seconds=61), beat=True) == [aid]
    assert heartbeat.state(aid)["last_status"] == "error"
    assert "engine.heartbeat" in jobs.runs_of(f"heartbeat:{aid}")[0]["error"]


def test_hooks_leave_a_database_without_the_kernel_alone(monkeypatch, tmp_path):
    """claw 是 OpenClaw（比如一直跑 OpenClaw 的老实例）时，OpenClaw 的 Agent 交的 schedule 卡也会经过这里的钩子：没用过内核的库什么都不碰。"""
    import sqlite3
    db = tmp_path / "mousse.db"
    sqlite3.connect(db).execute("CREATE TABLE inbox (id TEXT)").connection.commit()
    monkeypatch.setattr(config.settings, "db", db)
    assert not jobs.exists() and jobs.by_inbox("ib-x") is None and jobs.extra("ib-x") is None
    assert run(jobs.on_decided({"id": "ib-x", "title": "t", "why": "", "changes": [], "detail": ""}, "approve")) is None
    tables = {r[0] for r in sqlite3.connect(db).execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert tables == {"inbox"}


def test_card_counts_runs_honestly(builtin):
    t = when.now()
    spec = {"schedule": when.parse("every 1m", t), "times": 3}
    assert "runs a day" not in jobs._often(spec, t)  # 跑三次就停：不写「一天约 1440 次」
    assert "about 1440 runs a day" in jobs._often({**spec, "times": None}, t)
    assert "first run 1 minute after you turn it on" in jobs._often(spec, t)
    assert "(next " in jobs._often({"schedule": when.parse("every day 9am", t), "times": None}, t)
