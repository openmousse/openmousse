"""会议（meetings.py）：一轮的规矩（每位最多说一次、@ 只转一层、说完写纪要）在 builtin 下照旧——这一轮的跑法（run_round）群聊也在用
（MeetingRoom / groupchat.GroupRoom）。不连真模型、不花钱：模型是假的（按内容回放）；名字、人和事都是编的。"""
from __future__ import annotations

import asyncio
import json
import time

import pytest

import chat
import meetings
from engine import providers
from test_cybers import H, agent, env, groups_clean  # noqa: F401 — 赛博那边的 fixture（builtin、假密钥）和建功能 Agent 的小函数
from test_groupchat import ByContent, said


def run(coro):
    return asyncio.run(coro)


async def settle(mid: str, timeout: float = 5.0) -> None:
    t0 = time.monotonic()
    await asyncio.sleep(0)
    while meetings.busy_now(mid):
        if time.monotonic() - t0 > timeout:
            raise AssertionError("timed out waiting for the meeting")
        await asyncio.sleep(0.01)


@pytest.fixture
def menv(env, groups_clean, monkeypatch):  # noqa: F811 — env、groups_clean 是导入的 fixture
    env.cfg(user_name="Alex")
    with chat._lock, meetings.mdb() as conn:
        conn.execute("DELETE FROM meetings")
        conn.execute("DELETE FROM meeting_seen")
    return env


@pytest.fixture
def scripted(monkeypatch):
    def make_(rules):
        f = ByContent(rules)
        monkeypatch.setattr(providers, "stream", f.stream)
        return f
    return make_


def minutes(**kw) -> dict:
    return {"text": json.dumps({"decisions": [], "todos": [], "next": [], "open": [], **kw})}


def test_a_round_goes_one_hop_and_ends_with_minutes(menv, scripted):
    runner = agent(menv, "Running", icon="target", purpose="Plans runs and races")
    notes = agent(menv, "Notes", icon="book", purpose="Keeps reading notes")
    f = scripted([
        ('You are "Running"', {"text": "Train three times a week.\n@Notes any notes on tapering?"}),
        ('You are "Notes"', {"text": "Two notes on tapering. @Running fine?"}),  # 回答 @ 的那一条里再 @：不算
        ("Write the minutes of round 1", minutes(decisions=["Train three times a week"], open=["Taper length"])),
    ])

    async def go():
        out = await meetings.create(meetings.MeetingIn(topic="Spring race plan", members=[runner, notes]))
        mid = out["meeting"]["id"]
        await settle(mid)
        return mid
    mid = run(go())
    got = meetings.get_meeting(mid)
    said = [m for m in got["messages"] if m["kind"] == "agent"]
    assert [m["name"] for m in said] == ["Running", "Notes"] and said[1]["replyTo"]["id"] == runner and said[0]["at"]["id"] == notes
    assert "at" not in said[1]  # 只转一层
    mins = [m for m in got["messages"] if m["kind"] == "minutes"]
    assert len(mins) == 1 and mins[0]["minutes"]["decisions"] == ["Train three times a week"]
    assert got["meeting"]["status"] == "pending" and got["meeting"]["round"] == 1 and not f.rules
    assert [m["text"] for m in got["messages"] if m["kind"] == "system"][:2] == ["Started by you, hosted by TestMousse",
                                                                                  "TestMousse asked Running, Notes to answer"]
    rows = {r["id"]: r for r in menv.client.get("/api/chats", headers=H).json()["rows"]}
    assert rows[mid]["kind"] == "meeting" and rows[mid]["state"] == "waiting"  # 纪要有决定、等你确认


def test_stopping_a_meeting_round_marks_the_speaker_and_skips_the_minutes(menv, scripted):
    runner = agent(menv, "Running", icon="target")
    notes = agent(menv, "Notes", icon="book")
    scripted([('You are "Running"', {"chunks": ["A ", "long ", "answer ", "here"], "delay": 0.05})])

    async def go():
        out = await meetings.create(meetings.MeetingIn(topic="Long one", members=[runner, notes]))
        mid = out["meeting"]["id"]
        for _ in range(100):
            lv = meetings.running(mid)
            if lv and lv.text:
                break
            await asyncio.sleep(0.01)
        await meetings.stop(mid)
        await settle(mid)
        return mid
    mid = run(go())
    got = meetings.get_meeting(mid)
    said = [m for m in got["messages"] if m["kind"] == "agent"]
    assert len(said) == 1 and said[0]["text"].endswith("(Stopped)")
    assert got["messages"][-1]["text"] == "This round was stopped" and got["meeting"]["status"] == "pending"
    assert not [m for m in got["messages"] if m["kind"] == "minutes"]


# —— 主线第三步：收不收看谁发起、定了告诉我、会议记录、会议里的赛博 ————————————————————————————————————————

async def until_status(mid: str, want: str, timeout: float = 5.0) -> None:
    t0 = time.monotonic()
    while True:
        with chat._lock, meetings.mdb() as conn:
            r = conn.execute("SELECT status FROM meetings WHERE id=?", (mid,)).fetchone()
        if r and r["status"] == want:
            return
        if time.monotonic() - t0 > timeout:
            raise AssertionError(f"meeting never became {want}")
        await asyncio.sleep(0.02)


def todo(agent_: str, text: str, **kw) -> dict:
    return {"agent": agent_, "text": text, "title": text, "dates": [time.strftime("%Y-%m-%d")], "start": "18:00", "end": None, **kw}


def test_without_the_switch_every_meeting_keeps_its_old_place(menv, scripted):
    runner = agent(menv, "Running", icon="target")
    notes = agent(menv, "Notes", icon="book")
    scripted([('You are "Running"', {"text": "a"}), ('You are "Notes"', {"text": "b"}), ("Write the minutes of round 1", minutes())] * 2)

    async def go():
        a = (await meetings.create(meetings.MeetingIn(topic="Mine", members=[runner, notes])))["meeting"]
        await settle(a["id"])
        b = (await meetings.create(meetings.MeetingIn(topic="From the main chat", members=[runner, notes], origin="main")))["meeting"]
        await settle(b["id"])
        return a, b
    a, b = run(go())
    assert not a["keep"] and a["listed"] and b["listed"] and not b["keep"]  # 照旧：keep 默认 false，都单列
    rows = {r["id"] for r in menv.client.get("/api/chats", headers=H).json()["rows"]}
    assert {a["id"], b["id"]} <= rows


def test_with_the_switch_the_assistants_meetings_leave_the_list(menv, scripted):
    menv.cfg(user_name="Alex", meetings={"by_origin": True})
    import inbox
    with chat._lock, inbox.idb() as conn:  # 前面的测试留下的待确认会让主对话那一行算「等你」（waiting 盖过 working）
        conn.execute("DELETE FROM inbox WHERE status='pending'")
    runner = agent(menv, "Running", icon="target")
    notes = agent(menv, "Notes", icon="book")
    scripted([('You are "Running"', {"chunks": ["slow ", "one"], "delay": 0.05}), ('You are "Notes"', {"text": "b"}),
              ("Write the minutes of round 1", minutes()),
              ('You are "Running"', {"text": "a"}), ('You are "Notes"', {"text": "b"}), ("Write the minutes of round 1", minutes())])
    seen: dict = {}

    async def go():
        b = (await meetings.create(meetings.MeetingIn(topic="From the main chat", members=[runner, notes], origin="main")))["meeting"]
        main = menv.client.get("/api/chats", headers=H).json()["rows"][0]  # 正在进行：主对话那一行写「会议中」
        seen["main"] = main
        await settle(b["id"])
        a = (await meetings.create(meetings.MeetingIn(topic="Mine", members=[runner, notes])))["meeting"]
        await settle(a["id"])
        return a, b
    a, b = run(go())
    assert seen["main"]["line"] == "In a meeting · From the main chat" and seen["main"]["state"] == "working" and seen["main"]["meeting"]["id"] == b["id"]
    assert a["keep"] and a["listed"] and not b["keep"] and not b["listed"]  # 你发起的留下（会后保留为群聊），助手发起的收走
    rows = {r["id"] for r in menv.client.get("/api/chats", headers=H).json()["rows"]}
    assert a["id"] in rows and b["id"] not in rows
    assert b["id"] not in menv.client.get("/api/unread", headers=H).json().get("threads", {})
    assert menv.client.get("/api/chats", headers=H).json()["rows"][0]["line"] != "In a meeting · From the main chat"  # 开完了：不再「会议中」
    recs = menv.client.get("/api/meetings/records", headers=H).json()
    assert [r["id"] for r in recs["records"]] == [a["id"], b["id"]] and recs["records"][1]["initiator"] == "Started by TestMousse"
    assert [r["id"] for r in menv.client.get("/api/meetings/records?filter=main", headers=H).json()["records"]] == [b["id"]]
    assert [r["id"] for r in menv.client.get("/api/meetings/records?filter=mine", headers=H).json()["records"]] == [a["id"]]


def test_tell_me_when_settled_is_only_for_your_own_meetings_with_the_switch_on(menv, scripted):
    """「定了告诉我」会自动确认纪要（待办不经你点头就进日程）：开关关着、助手发起的，收到 auto 也不理。资料页的「会议记录」那一行
    跟着开关（options 的 records）；关掉以后，开着时收走的会还在，照样画。"""
    runner = agent(menv, "Running", icon="target")
    notes = agent(menv, "Notes", icon="book")
    scripted([('You are "Running"', {"text": "a"}), ('You are "Notes"', {"text": "b"}), ("Write the minutes of round 1", minutes())] * 2)

    def opts():
        o = menv.client.get("/api/meetings/options", headers=H).json()
        return o["byOrigin"], o["records"]

    async def go(**kw):
        m = (await meetings.create(meetings.MeetingIn(topic="Tuesday session", members=[runner, notes], auto=True, **kw)))["meeting"]
        await settle(m["id"])
        return meetings.get_meeting(m["id"])["meeting"]
    assert opts() == (False, False)
    off = run(go())
    assert not off["auto"] and off["listed"] and off["round"] == 1 and not off["confirmed"]  # 开关关着：照旧开一轮、等你确认
    menv.cfg(user_name="Alex", meetings={"by_origin": True})
    assert opts() == (True, True)
    mine = run(go(origin="main"))
    assert not mine["auto"] and not mine["listed"] and mine["round"] == 1 and not mine["confirmed"]  # 助手发起的：开完收走，但不全自动
    menv.cfg(user_name="Alex")
    assert opts() == (False, True)  # 开关关掉以后：收走的那场还得找得到


def test_tell_me_when_settled_runs_to_the_end_by_itself(menv, scripted):
    menv.cfg(user_name="Alex", meetings={"by_origin": True})
    runner = agent(menv, "Running", icon="target")
    notes = agent(menv, "Notes", icon="book")
    scripted([('You are "Running"', {"text": "Intervals on Tuesday."}), ('You are "Notes"', {"text": "What pace?"}),
              ("Write the minutes of round 1", minutes(decisions=["Intervals"], open=["Pace"], next=[runner])),
              ('You are "Running"', {"text": "5:00 per km."}),  # 还有没定的、纪要说了该请谁：自己开第 2 轮
              ("Write the minutes of round 2", minutes(decisions=["Intervals at 5:00"], todos=[todo(runner, "Tue 18:00 intervals")]))])

    async def go():
        m = (await meetings.create(meetings.MeetingIn(topic="Tuesday session", members=[runner, notes], auto=True)))["meeting"]
        await until_status(m["id"], "ended")
        return m["id"]
    mid = run(go())
    got = meetings.get_meeting(mid)
    assert got["meeting"]["round"] == 2 and got["meeting"]["confirmed"]["todos"] == 1 and got["meeting"]["auto"] and not got["meeting"]["listed"]
    assert got["messages"][0]["text"] == "Started by you, hosted by TestMousse · you'll be told once it's settled"
    with chat._lock, chat.db() as conn:
        last = conn.execute("SELECT text, origin FROM messages WHERE thread='main' ORDER BY id DESC LIMIT 1").fetchone()
        sched = conn.execute("SELECT title, source FROM schedule_items WHERE key LIKE ?", (f"meeting:{mid}:%",)).fetchall()
    assert last["text"] == 'The meeting "Tuesday session" is done; 1 to-do went into the schedule and dashboards:'
    assert [(x["title"], x["source"]) for x in sched] == [("Tue 18:00 intervals", runner)]
    card = [c for c in menv.client.get("/api/chat/cards?thread=main", headers=H).json()["cards"] if c.get("meeting") == mid and c["kind"] == "minutes"]
    assert card and card[-1]["status"] == "confirmed"  # 主对话留一张纪要卡（已确认的回执）
    rec = menv.client.get("/api/meetings/records?filter=auto", headers=H).json()["records"]
    assert rec[0]["id"] == mid and rec[0]["line"] == "Started by you · told when settled · In the schedule"


def test_records_search_topics_minutes_and_speeches(menv, scripted):
    runner = agent(menv, "Running", icon="target")
    notes = agent(menv, "Notes", icon="book")
    scripted([('You are "Running"', {"text": "Hill repeats are the key."}), ('You are "Notes"', {"text": "Noted."}),
              ("Write the minutes of round 1", minutes(decisions=["Add a taper week"]))])

    async def go():
        m = (await meetings.create(meetings.MeetingIn(topic="Marathon block", members=[runner, notes])))["meeting"]
        await settle(m["id"])
        return m["id"]
    mid = run(go())
    c = menv.client
    for q, part in (("marathon", "Marathon block"), ("taper", "taper week"), ("hill repeats", "Hill repeats")):
        recs = c.get(f"/api/meetings/records?q={q}", headers=H).json()["records"]
        assert [r["id"] for r in recs] == [mid] and part.lower() in recs[0]["match"].lower()
    assert c.get("/api/meetings/records?q=nothing-like-this", headers=H).json()["records"] == []
    assert c.get("/api/meetings/records?filter=bogus", headers=H).status_code == 400


def test_cybers_can_join_a_meeting_but_take_no_to_dos(menv, scripted):
    from engine import events
    from test_cybers import make
    runner = agent(menv, "Running", icon="target")
    meals = agent(menv, "Meals", icon="utensils")
    v = make(menv, "Victor", tag="Early investor", identity="An early-stage investor.")["id"]
    c = menv.client
    assert c.post("/api/meetings", json={"topic": "x", "members": [v, meals]}, headers=H).status_code == 400  # 健康类的不和赛博同会
    assert c.post("/api/meetings", json={"topic": "x", "members": [v, make(menv, "Sam")["id"]]}, headers=H).status_code == 400  # 没有功能 Agent
    scripted([("You are Victor", {"text": "Race only if it pays off."}), ('You are "Running"', {"text": "Spring 10K."}),
              ("Write the minutes of round 1", minutes(todos=[todo(v, "Victor follows up"), todo(runner, "Book the 10K")]))])

    async def go():
        m = (await meetings.create(meetings.MeetingIn(topic="Spring race", members=[v, runner])))["meeting"]
        await settle(m["id"])
        return m["id"]
    mid = run(go())
    got = meetings.get_meeting(mid)
    said = [m for m in got["messages"] if m["kind"] == "agent"]
    assert [m["name"] for m in said] == ["Victor", "Running"]
    assert events.turns(f"meeting:{mid}:{v}")[-1]["kind"] == "cyber"  # 赛博回合，不花套餐
    assert got["meeting"]["members"][0]["kind"] == "cyber" and got["meeting"]["members"][0]["tag"] == "Early investor"
    mins = [m for m in got["messages"] if m["kind"] == "minutes"][0]["minutes"]
    assert [td["agent"] for td in mins["todos"]] == [runner]  # 赛博不领待办


def test_an_agent_found_to_be_about_health_later_sits_out_while_a_cyber_is_in_the_meeting(menv, scripted):
    """健康类不和赛博同会：开会时查过；后来才认出来的（这里是写进 cybers.health_agents），下一轮请它之前现查：记一行，这次不请。"""
    from test_cybers import make
    runner = agent(menv, "Running", icon="target", purpose="Plans runs and races")
    v = make(menv, "Victor", identity="An early-stage investor.")["id"]
    f = scripted([("You are Victor", {"text": "Race only if it pays off."}), ('You are "Running"', {"text": "Spring 10K."}),
                  ("Write the minutes of round 1", minutes()),
                  ("You are Victor", {"text": "Still only if it pays off."}), ("Write the minutes of round 2", minutes())])

    async def go():
        m = (await meetings.create(meetings.MeetingIn(topic="Spring race", members=[v, runner])))["meeting"]
        await settle(m["id"])
        menv.cfg(user_name="Alex", cybers={"health_agents": [runner]})
        await meetings.continue_(m["id"])
        await settle(m["id"])
        return m["id"]
    mid = run(go())
    msgs = meetings.get_meeting(mid)["messages"]
    assert [m["name"] for m in msgs if m["kind"] == "agent"] == ["Victor", "Running", "Victor"]  # 第二轮没请 Running
    assert "Running looks after health, diet or sleep and a Cyber is here, so it wasn't asked" in [m["text"] for m in msgs if m["kind"] == "system"]
    assert sum('You are "Running"' in req.system + said(req) for _, req in f.requests) == 1  # 它的回合只起过第一轮那一次


def test_options_list_agents_first_mark_health_and_follow_the_switch(menv):
    from test_cybers import make
    runner = agent(menv, "Running", icon="target")
    meals = agent(menv, "Meals", icon="utensils")
    v = make(menv, "Victor", tag="Early investor")["id"]
    out = menv.client.get("/api/meetings/options", headers=H).json()
    assert [(m["id"], m["kind"], m["health"]) for m in out["members"]] == [(runner, "agent", False), (meals, "agent", True), (v, "cyber", False)]
    assert out["members"][2]["tag"] == "Early investor" and out["keep"] is False and out["byOrigin"] is False
    menv.cfg(user_name="Alex", meetings={"by_origin": True})  # 开关开着：你发起的默认留成群聊
    assert menv.client.get("/api/meetings/options", headers=H).json()["keep"] is True
    assert "meetings.records" in menv.client.get("/api/health", headers=H).json()["server"]["features"]
