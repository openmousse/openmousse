"""播客的录前讨论群（主线第三步第二段，podprep.py）：开着群聊和赛博才有；主持按录法（Iris / 阿哲）、先开口、一位成员时你说一句它就接、
提纲钉顶（主持人排 / 改，群里一行「更新了提纲第 N 条」）、「放进」的卡（健康类的只给主持赛博、只这一次）、「制作中」那一行的字段、
约录制时间进日程、老的 pod_turns 照样读得到、删一期连群一起删。不连真模型：模型是假的（按内容回放）；名字、事都是编的。"""
from __future__ import annotations

import asyncio
import json

import pytest

import chat
import cybers
import friendgroup
import groupchat
import podcast
import podmaterials
import podprep
import schedule
from stored_values import ASSISTANT_ROLE
from test_butler import ENGINE
from test_cybers import H, agent, env, groups_clean  # noqa: F401 — 赛博那边的 fixture（builtin、假密钥）和建功能 Agent 的小函数
from test_friendgroup import ME_URL, Peer, deliver, fenv, post_group  # noqa: F401 — 好友群那边的 fixture（假的别人的服务器）
from test_groupchat import ByContent, said, settle


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def penv(env, groups_clean, monkeypatch):  # noqa: F811 — env、groups_clean 是导入的 fixture
    """开着赛博和群聊；默认的两位赛博（Iris、阿哲）建好；播客、群聊的表清空。"""
    env.cfg(groupchat=True, user_name="Alex")
    with chat._lock, cybers.cydb() as conn:
        conn.execute("DELETE FROM cyber_meta WHERE key='defaults'")
    cybers.seed_defaults()
    with chat._lock, podcast.pdb() as conn:
        for t in ("pod_episodes", "pod_turns"):
            conn.execute(f"DELETE FROM {t}")  # noqa: S608
    with chat._lock, groupchat.gdb() as conn:
        conn.execute("DELETE FROM groupchats")
    with chat._lock, podprep.odb() as conn:
        conn.execute("DELETE FROM pod_offers")
    return env


@pytest.fixture
def scripted(monkeypatch):
    from engine import providers

    def make_(rules):
        f = ByContent(rules)
        monkeypatch.setattr(providers, "stream", f.stream)
        return f
    return make_


def episode(title: str = "Why I switched to running", mode: str = "host") -> str:
    return run(podcast.create(podcast.EpisodeIn(title=title, mode=mode)))["episode"]["id"]


def start(eid: str, **kw) -> dict:
    async def go():
        out = await podprep.discuss(eid, podprep.DiscussIn(**kw))
        await settle(out["group"])
        return out
    return run(go())


def test_prep_groups_need_group_chats_and_cybers(penv):
    eid = episode()
    penv.cfg(groupchat=False, user_name="Alex")
    assert penv.client.post(f"/api/podcast/episodes/{eid}/discuss", json={}, headers=H).status_code == 404
    assert penv.client.get(f"/api/podcast/episodes/{eid}", headers=H).json()["episode"]["prepGroup"] is None  # 原来的录前聊天照旧


def test_the_host_opens_and_answers_each_reply(penv, scripted):
    iris = cybers.by_builtin("iris")["id"]
    eid = episode()
    f = scripted([("You are Iris", {"text": "First reaction: why running and not cycling?"})])
    out = start(eid)
    gid = out["group"]
    g = groupchat.get_group(gid)
    assert g["group"]["host"]["id"] == iris and [m["id"] for m in g["group"]["members"]] == [iris] and g["group"]["mode"] == "host"
    assert g["group"]["source"] == {"kind": "podcast", "id": eid, "title": "Why I switched to running"}
    assert g["messages"][1]["text"] == "Iris starts with a few questions" and g["messages"][-1]["speaker"] == iris
    first = said(f.requests[0][1])
    assert "Open by asking Alex for their first reaction" in first and "podcast prep chat" in f.requests[0][1].system
    ep = podcast.episode_json(eid)  # 主持人问过了以后再看那一行
    assert ep["prepGroup"] == gid and ep["participants"][0]["id"] == iris and ep["participants"][0]["host"]
    assert ep["steps"]["current"] == 1 and ep["latest"].startswith("Iris: First reaction") and ep["waiting"]
    assert start(eid)["group"] == gid  # 再点「开始讨论」：还是那一个群
    f = scripted([("You are Iris", {"text": "What changed after the first month?"})])  # 一位成员：你说一句它就接（不另外问主持人挑谁）

    async def reply():
        out = await groupchat.send(gid, groupchat.SendIn(text="Cheaper and I can do it anywhere."))
        assert out["how"] == "arrange"
        await settle(gid)
    run(reply())
    assert len(f.requests) == 1 and groupchat.get_group(gid)["messages"][-1]["text"] == "What changed after the first month?"


def test_feynman_gets_zhe_and_the_outline_is_pinned(penv, scripted):
    zhe = cybers.by_builtin("zhe")["id"]
    eid = episode("How vaccines train the body", mode="feynman")
    scripted([("You are Zhe", {"text": "Wait, what's an antigen?"})])
    gid = start(eid)["group"]
    assert groupchat.get_group(gid)["group"]["host"]["id"] == zhe
    scripted([("draft an outline", {"text": json.dumps({"outline": ["What it is", "Why it works", "An example"], "reply": "Here's a plan."})})])
    out = run(podprep.outline(eid, podprep.OutlineIn()))
    assert [o["text"] for o in out["episode"]["outline"]] == ["What it is", "Why it works", "An example"]
    assert out["message"]["text"] == "Zhe drafted the outline · 3 items"
    scripted([("draft an outline", {"text": json.dumps({"outline": ["What it is", "Why it lasts", "An example"]})})])
    out = run(podprep.outline(eid, podprep.OutlineIn(note="second one: why it lasts")))
    assert out["message"]["text"] == "Zhe updated outline item 2"
    f = scripted([("You are Zhe", {"text": "So the second point is about memory cells?"})])

    async def ask():
        await groupchat.send(gid, groupchat.SendIn(text="@Zhe does the order work?"))
        await settle(gid)
    run(ask())
    assert "Pinned outline:\n1. What it is\n2. Why it lasts\n3. An example" in said(f.requests[0][1])  # 钉在顶上：每次都读
    g = groupchat.get_group(gid)
    assert g["episode"]["outline"][1]["text"] == "Why it lasts" and g["episode"]["hostLine"] == "Zhe · the layman"
    assert podcast.brief(podcast.load(eid))["steps"]["current"] == 2  # 有提纲了：第二格


def test_a_health_report_goes_only_to_the_host_and_only_once(penv, scripted):
    iris = cybers.by_builtin("iris")["id"]
    sleep = agent(penv, "Bedtime", icon="moon", purpose="Interprets last night's sleep")
    notes = agent(penv, "Notes", icon="book", purpose="Keeps reading notes")
    with chat._lock, chat.db() as conn:
        conn.execute("INSERT INTO messages(thread, role, text, ts, status, origin) VALUES(?,?,?,?,?,?)",
                     (sleep, ASSISTANT_ROLE, "Seven nights: 6h40 on average, two short nights.", chat.now_iso(), "ok", "auto"))
        conn.execute("INSERT INTO messages(thread, role, text, ts, status, origin) VALUES(?,?,?,?,?,?)",
                     (notes, ASSISTANT_ROLE, "Three notes on habit change.", chat.now_iso(), "ok", "auto"))
    eid = episode("Sleeping better")
    scripted([("You are Iris", {"text": "What does a good night look like?"})])
    gid = start(eid)["group"]
    o = run(podprep.make_offer(eid, podprep.OfferIn(agent=sleep)))["offer"]
    assert o["health"] and o["cyber"] == iris and o["status"] == "offered" and "Only for Iris, only once" in o["line"]
    assert groupchat.get_group(gid)["offers"][0]["id"] == o["id"]  # 群里画在顶上，只有你看得到
    put = run(podprep.act_offer(o["id"], podprep.OfferAct(action="put")))["offer"]
    assert put["status"] == "put"
    n = run(podprep.make_offer(eid, podprep.OfferIn(agent=notes)))["offer"]
    assert not n["health"]
    run(podprep.act_offer(n["id"], podprep.OfferAct(action="put")))
    import podmaterials
    mats = [m["title"] for m in podmaterials.listing(eid)]
    assert mats == ["Notes's latest report"]  # 健康类的不进这一期的素材
    f = scripted([("You are Iris", {"text": "Two short nights; which ones?"}), ("You are Iris", {"text": "And the weekend?"})])

    async def talk(text):
        await groupchat.send(gid, groupchat.SendIn(text=text))
        await settle(gid)
    run(talk("I want to sleep more."))
    first = said(f.requests[0][1])
    assert "Seven nights: 6h40 on average" in first and "Three notes on habit change." in first
    run(talk("Mostly weekdays."))
    second = said(f.requests[1][1])
    assert "Seven nights" not in second and "Three notes on habit change." in second  # 只这一次
    assert podprep.offers_of(eid)[0]["status"] == "used"


def test_booking_a_recording_puts_it_in_the_schedule(penv, scripted):
    eid = episode("Weekend run")
    scripted([("You are Iris", {"text": "Why weekends?"})])
    start(eid)
    r = penv.client.patch(f"/api/podcast/episodes/{eid}", json={"bookedAt": "2026-11-07T15:00"}, headers=H).json()["episode"]
    assert r["booked"] == {"at": "2026-11-07T15:00", "label": "Recording 11/7 15:00"}
    with chat._lock, chat.db() as conn:
        item = conn.execute("SELECT title, date, start, deleted_at FROM schedule_items WHERE key=?", (f"podcast:{eid}:rec",)).fetchone()
    assert (item["title"], item["date"], item["start"], item["deleted_at"]) == ("Record: Weekend run", "2026-11-07", "15:00", None)
    r = penv.client.patch(f"/api/podcast/episodes/{eid}", json={"bookedAt": ""}, headers=H).json()["episode"]
    assert r["booked"] is None
    with chat._lock, chat.db() as conn:
        assert conn.execute("SELECT deleted_at FROM schedule_items WHERE key=?", (f"podcast:{eid}:rec",)).fetchone()["deleted_at"]


def friends_env(penv, fenv):  # noqa: F811 — fenv 是导入的 fixture
    """多人对谈拉好友：群聊、赛博、管家都开着，有对外地址；两位好友（新版本，名片有 group）。"""
    fenv.cfg(groupchat=True, butler=True, user_name="Alex", engine=ENGINE, share={"public_url": ME_URL})
    mia, sam = Peer("Mia", "https://mia.example.com"), Peer("Sam", "https://sam.example.com")
    return mia, sam, mia.befriend(), sam.befriend()


def test_with_friends_the_prep_is_a_friend_group_my_butler_hosts(penv, fenv, scripted):  # noqa: F811
    mia, sam, fm, fs = friends_env(penv, fenv)
    eid = episode("Moving abroad", mode="friends")
    out = penv.client.post(f"/api/podcast/episodes/{eid}/discuss", json={"friends": [fm["id"], fs["id"]]}, headers=H).json()
    gid = out["group"]
    assert gid.startswith("fg-") and out["episode"]["prepGroup"] == gid
    ep = out["episode"]
    assert [(p["kind"], p["host"]) for p in ep["participants"]] == [("butler", True), ("friend", False), ("friend", False)]
    assert ep["participants"][0]["name"] == "Alex's butler" and ep["latest"].startswith("Alex's butler: This episode is about")
    g = penv.client.get(f"/api/friendgroups/{gid}", headers=H).json()
    assert g["group"]["source"]["kind"] == "podcast" and g["group"]["episode"]["hostLine"] == "Your butler · also hosts"
    assert [(m["who"], m["text"].split(",")[0]) for m in g["messages"]] == [("butler", 'This episode is about "Moving abroad"')]
    deliver()
    sent = [s for s in fenv.sent if s["payload"]["type"] == "msg"]
    assert sorted(s["to"] for s in sent) == sorted([mia.kid, sam.kid]) and "podcast" not in json.dumps(sent[0]["payload"])  # 来源只在我这边
    said_ = penv.client.get("/api/butler/said", headers=H).json()["items"]
    assert said_[0]["where"] == 'In "Moving abroad"' and said_[0]["by"] == "fixed"  # 进「说过的话」
    assert penv.client.post(f"/api/podcast/episodes/{eid}/discuss", json={"friends": [fm["id"]]}, headers=H).json()["group"] == gid  # 还是那一个
    # 提纲：Iris 帮忙排，只给她我自己说的话；那一行写管家、只在我这边
    roster = json.loads(friendgroup.group_row(chat.db(), gid)["roster"])
    post_group(fenv, mia, {"v": 1, "type": "msg", "roster": roster,
                           "msg": {"id": "1" * 32, "who": "person", "text": "My visa took four months, ask me about that", "at": chat.now_iso()}})
    friendgroup.say(gid, "I want to start with the paperwork")
    chat_with = {"kind": "friend", "title": "Chat with Mia · Oct 3", "at": chat.now_iso(), "meta": {"friend": "Mia", "friendId": fm["id"]}}
    podmaterials.add(eid, {**chat_with, "ref": f"{fm['id']}:1", "who": "me", "text": "Told her the residence permit is the worst part"})
    podmaterials.add(eid, {**chat_with, "ref": f"{fm['id']}:2", "who": "assistant", "text": "Mia says her own permit took four months"})
    podmaterials.add(eid, {"kind": "chat", "ref": "c1", "title": "Chat · Oct 2", "who": "me", "at": chat.now_iso(), "text": "The bank account took three weeks"})
    f = scripted([("draft an outline", {"text": json.dumps({"outline": ["The paperwork", "The first month", "What I miss"]})})])
    res = run(podprep.outline(eid, podprep.OutlineIn()))
    iris_saw = said(f.requests[0][1])
    assert '(You are the Cyber "Iris"' in f.requests[0][1].system + iris_saw  # 帮忙排提纲的是 Iris
    assert "I want to start with the paperwork" in iris_saw and "visa" not in iris_saw and "Mia" not in iris_saw
    assert "bank account" in iris_saw and "permit" not in iris_saw  # 别的素材照给；和朋友的聊天（我说的、名片 Agent 复述的）整条不给
    assert res["message"]["system"] and res["message"]["text"] == "Alex's butler drafted the outline · 3 items"
    fenv.sent.clear()
    deliver()
    assert not [s for s in fenv.sent if "outline" in json.dumps(s["payload"])]  # 提纲那一行不发给好友
    sleep = agent(penv, "Bedtime", icon="moon", purpose="Interprets last night's sleep")
    assert penv.client.post(f"/api/podcast/episodes/{eid}/offers", json={"agent": sleep}, headers=H).status_code == 409  # 有好友在：健康类不放进来
    notes = agent(penv, "Notes", icon="book", purpose="Keeps reading notes")
    with chat._lock, chat.db() as conn:
        conn.execute("INSERT INTO messages(thread, role, text, ts, status, origin) VALUES(?,?,?,?,?,?)",
                     (notes, ASSISTANT_ROLE, "Three notes on moving abroad.", chat.now_iso(), "ok", "auto"))
    o = penv.client.post(f"/api/podcast/episodes/{eid}/offers", json={"agent": notes}, headers=H).json()["offer"]
    assert [x["id"] for x in penv.client.get(f"/api/friendgroups/{gid}", headers=H).json()["offers"]] == [o["id"]]  # 群页上画得出来


def new_items(before: set[str]) -> list[tuple]:
    """这一条测试里新进日程的（别的测试留下的不算）：[(key, title, date, start)]。"""
    with chat._lock, schedule.sdb() as conn:
        rows = conn.execute("SELECT id, key, title, date, start FROM schedule_items WHERE deleted_at IS NULL ORDER BY created_at").fetchall()
    return [tuple(r)[1:] for r in rows if r["id"] not in before]


def test_a_time_a_friend_proposes_is_a_card_first_then_the_episodes_booking(penv, fenv, scripted):  # noqa: F811
    mia, sam, fm, fs = friends_env(penv, fenv)
    with chat._lock, schedule.sdb() as conn:
        before = {r[0] for r in conn.execute("SELECT id FROM schedule_items").fetchall()}
    eid = episode("Moving abroad", mode="friends")
    gid = penv.client.post(f"/api/podcast/episodes/{eid}/discuss", json={"friends": [fm["id"], fs["id"]]}, headers=H).json()["group"]
    roster = json.loads(friendgroup.group_row(chat.db(), gid)["roster"])
    post_group(fenv, sam, {"v": 1, "type": "msg", "roster": roster, "msg": {"id": "2" * 32, "who": "person", "text": "Sounds fun", "at": chat.now_iso()}})
    assert fenv.later == []  # 没叫它、也没提时间：管家不接
    ask = {"kind": "decision", "summary": "Record on Saturday at 3pm",
           "proposal": {"what": "recording", "date": "2026-11-07", "start": "15:00", "end": "", "place": ""}}
    scripted([("Could we record on Saturday at 3pm?", {"text": json.dumps({"reply": "I'll check with Alex.", "used": [], "ask_owner": ask, "declined": []})})])
    post_group(fenv, mia, {"v": 1, "type": "msg", "roster": roster,
                           "msg": {"id": "3" * 32, "who": "person", "text": "Could we record on Saturday at 3pm?", "at": chat.now_iso()}})
    assert len(fenv.later) == 1  # 录前讨论里管家兼主持：有人提了时间就接
    run(fenv.later.pop())
    with chat._lock, chat.db() as conn:
        card = conn.execute("SELECT id, title FROM inbox WHERE kind='social' AND status='pending'").fetchone()
    assert new_items(before) == []  # 先出卡：还没进日程
    assert "Mia" in card["title"] and podcast.episode_json(eid)["booked"] is None and podcast.episode_json(eid)["waiting"]
    fenv.sent.clear()
    assert penv.client.post(f"/api/inbox/{card['id']}", json={"action": "approve"}, headers=H).status_code == 200
    ep = podcast.episode_json(eid)
    assert ep["booked"] == {"at": "2026-11-07T15:00", "label": "Recording 11/7 15:00"}
    assert new_items(before) == [(f"podcast:{eid}:rec", "Record: Moving abroad", "2026-11-07", "15:00")]  # 同一条，不另加「见面」
    msgs = penv.client.get(f"/api/friendgroups/{gid}", headers=H).json()["messages"]
    assert msgs[-1]["who"] == "butler" and "Alex is in" in msgs[-1]["text"]  # 回话由管家发回群里
    # 我自己改时间：直接进日程（同一条），管家在群里说一声
    fenv.sent.clear()
    penv.client.patch(f"/api/podcast/episodes/{eid}", json={"bookedAt": "2026-11-08T10:00"}, headers=H)
    msgs = penv.client.get(f"/api/friendgroups/{gid}", headers=H).json()["messages"]
    assert msgs[-1]["text"] == "Recording is set for 11/8 at 10:00."
    deliver()
    assert {s["to"] for s in fenv.sent} == {mia.kid, sam.kid}
    assert penv.client.delete(f"/api/podcast/episodes/{eid}", headers=H).json() == {"ok": True}
    assert penv.client.get(f"/api/friendgroups/{gid}", headers=H).status_code == 200  # 好友群是大家的：删一期只断开


def test_old_prep_turns_stay_readable_and_deleting_the_episode_takes_the_group(penv, scripted):
    eid = episode("Old one")
    with chat._lock, podcast.pdb() as conn:
        conn.execute("INSERT INTO pod_turns (episode, phase, role, text, created_at) VALUES (?,?,?,?,?)", (eid, "prep", "host", "First reaction?", chat.now_iso()))
        conn.execute("INSERT INTO pod_turns (episode, phase, role, text, created_at) VALUES (?,?,?,?,?)", (eid, "prep", "me", "It felt freeing.", chat.now_iso()))
    ep = penv.client.get(f"/api/podcast/episodes/{eid}", headers=H).json()["episode"]
    assert [t["text"] for t in ep["turns"]] == ["First reaction?", "It felt freeing."] and ep["prepGroup"] is None
    scripted([("You are Iris", {"text": "Freeing how?"})])
    gid = start(eid)["group"]
    ep = penv.client.get(f"/api/podcast/episodes/{eid}", headers=H).json()["episode"]
    assert len(ep["turns"]) == 2 and ep["prepGroup"] == gid  # 不搬不删
    assert penv.client.delete(f"/api/podcast/episodes/{eid}", headers=H).json() == {"ok": True}
    assert penv.client.get(f"/api/groupchats/{gid}", headers=H).status_code == 404
