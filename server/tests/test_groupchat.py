"""群聊（主线第三步第二段，groupchat.py）：开关、建群（成员、健康类不和赛博同群、主持人、发言方式）、@ 一位、@所有 Agent = 一轮
（复用会议的一轮：@ 只转一层）、主持人安排（一次最多一位）、每次发言读到的（设定 + 最近 20 条 + @ 它的那条，进群以前的不给）、
赛博走赛博回合 / 功能 Agent 用自己的开头零工具、【要做】变成只有你看得到的卡、对话列表和未读、用量、服务重启、停、群里的内容进待定。
不连真模型、不花钱：模型是假的（按内容回放），密钥是假的；名字、人和事都是编的。"""
from __future__ import annotations

import asyncio
import json
import time

import pytest

import chat
import cybers
import groupchat
import inbox
import meetings
from engine import events, providers
from stored_values import ASSISTANT_ROLE
from test_cybers import BASE, H, agent, env, groups_clean, make  # noqa: F401 — 赛博那边的 fixture 和建人的小函数
from test_engine import FakeModel


def run(coro):
    return asyncio.run(coro)


async def settle(gid: str, timeout: float = 5.0) -> None:
    """等这个群这一次说完（后台的那一轮跑完）。"""
    t0 = time.monotonic()
    await asyncio.sleep(0)
    while groupchat.busy_now(gid):
        if time.monotonic() - t0 > timeout:
            raise AssertionError("timed out waiting for the group")
        await asyncio.sleep(0.01)


class ByContent:
    """按内容回：rules = [(要在 system 或消息里出现的字, 回的那一条)]，每条用一次，先中的先用；都不中就报错（多调了一次模型）。"""

    def __init__(self, rules: list) -> None:
        self.rules = list(rules)
        self.requests: list = []

    async def stream(self, ref, req):
        self.requests.append((ref, req))
        hay = req.system + "\n" + said(req)
        for i, (needle, step) in enumerate(self.rules):
            if needle in hay:
                self.rules.pop(i)
                break
        else:
            raise AssertionError(f"no scripted reply for: {hay[-400:]}")
        async for ev in FakeModel([step]).stream(ref, req):
            yield ev


@pytest.fixture
def scripted(monkeypatch):
    def make_(rules):
        f = ByContent(rules)
        monkeypatch.setattr(providers, "stream", f.stream)
        return f
    return make_


@pytest.fixture
def genv(env, groups_clean, monkeypatch, tmp_path):  # noqa: F811 — env、groups_clean 是导入的 fixture
    """开着赛博和群聊；群聊的表清空；收件箱「已同意」以后发进对话的那句只记下来（不真跑主对话）。"""
    env.cfg(groupchat=True, user_name="Alex")
    with chat._lock, groupchat.gdb() as conn:
        for t in ("groupchats", "group_cards", "group_usage"):
            conn.execute(f"DELETE FROM {t}")  # noqa: S608
    with chat._lock, inbox.idb() as conn:
        conn.execute("DELETE FROM inbox WHERE dedupe LIKE 'group:%'")
    kicked: list = []
    monkeypatch.setattr(inbox, "kick", lambda thread, text: kicked.append((thread, text)) or "started")
    env.kicked = kicked
    return env


def people(genv):
    """两位赛博、一个功能 Agent。"""
    v = make(genv, "Victor", tag="Early investor", identity="An early-stage investor.", model="moonshot/kimi-k3")
    s = make(genv, "Socrates", tag="Questioner", identity="Only asks questions.")
    run_ = agent(genv, "Running", icon="target", purpose="Plans runs and races")
    return v["id"], s["id"], run_


def new(members, **kw) -> str:
    return run(groupchat.create(groupchat.GroupIn(members=members, **kw)))["group"]["id"]


def said(req) -> str:
    """这一次发给模型的字（system 不算），给断言用。"""
    return "\n".join(b.get("text", "") for m in req.messages for b in (m.get("content") or []) if isinstance(b, dict))


def texts(gid: str) -> list[tuple]:
    out = groupchat.get_group(gid)
    return [(m["kind"], m["name"], m["text"]) for m in out["messages"]]


def test_off_by_default_and_only_on_the_builtin_engine(genv):
    genv.cfg(groupchat=None)
    assert genv.client.get("/api/groupchats", headers=H).status_code == 404
    genv.cfg(groupchat={"enabled": False})
    assert genv.client.post("/api/groupchats", json={"members": []}, headers=H).status_code == 404
    genv.cfg(groupchat=True, claw={"kind": "openclaw"})
    assert genv.client.get("/api/groupchats", headers=H).status_code == 404
    genv.cfg(groupchat=True)
    assert genv.client.get("/api/groupchats", headers=H).json() == {"ok": True, "groups": []}
    assert "groupchat" in genv.client.get("/api/health", headers=H).json()["server"]["features"]


def test_creating_checks_members_hosts_and_keeps_health_agents_away_from_cybers(genv):
    v, s, r = people(genv)
    meals = agent(genv, "Meals", icon="utensils")
    c = genv.client
    assert c.post("/api/groupchats", json={"members": ["cy-00000000"]}, headers=H).status_code == 400
    assert c.post("/api/groupchats", json={"members": []}, headers=H).status_code == 400
    bad = c.post("/api/groupchats", json={"members": [v, meals]}, headers=H)
    assert bad.status_code == 400 and "can't share a group with a Cyber" in bad.json()["detail"]  # 赛博读得到群里的每一句
    assert c.post("/api/groupchats", json={"members": [meals, r], "host": s}, headers=H).status_code == 400  # 赛博主持也一样
    assert c.post("/api/groupchats", json={"members": [meals, r]}, headers=H).status_code == 200  # 没有赛博：可以
    assert c.post("/api/groupchats", json={"members": [v], "host": r}, headers=H).status_code == 400  # 主持人不能是功能 Agent
    assert c.post("/api/groupchats", json={"members": [v], "host": "", "mode": "host"}, headers=H).status_code == 400
    g = c.post("/api/groupchats", json={"members": [v, s]}, headers=H).json()["group"]
    assert g["title"] == "Victor, Socrates" and g["host"] == {"id": "main", "kind": "main", "name": "TestMousse"} and g["mode"] == "mention"
    assert [m["name"] for m in g["members"]] == ["Victor", "Socrates"] and g["members"][0]["tag"] == "Early investor"
    assert g["members"][0]["model"] == "Kimi K3" and g["usage"]["speeches"] == 0
    assert texts(g["id"]) == [("system", "TestMousse", "Started by you, hosted by TestMousse")]
    assert c.post("/api/chat/send", json={"thread": g["id"], "text": "hi"}, headers=H).status_code == 400  # 群的话不走主对话那条路


def test_candidates_list_cybers_and_agents_and_mark_the_health_ones(genv):
    v, s, r = people(genv)
    meals = agent(genv, "Meals", icon="utensils")
    with chat._lock, chat.db() as conn:  # 和 Socrates 聊过：排在前面
        conn.execute("INSERT INTO messages(thread, role, text, ts, status, origin) VALUES(?,?,?,?,?,?)", (s, "user", "hi", chat.now_iso(), "ok", "user"))
    out = genv.client.get("/api/groupchats/candidates", headers=H).json()
    assert [c["id"] for c in out["cybers"]] == [s, v] and out["cybers"][1]["tag"] == "Early investor"
    assert {a["id"]: a["health"] for a in out["agents"]} == {r: False, meals: True}
    assert out["host"] == {"id": "main", "name": "TestMousse"} and (out["min"], out["max"]) == (1, 8)


def test_a_topic_starts_with_one_round(genv, scripted):
    v, s, r = people(genv)
    f = scripted([("You are Victor", {"text": "Open core."}), ("You are Socrates", {"text": "Open for whom?"}),
                  ("Write the summary of round 1", summ(line="Open core, maybe."))])

    async def go():
        g = await groupchat.create(groupchat.GroupIn(members=[v, s], topic="Should the sync library be open source?"))
        await settle(g["group"]["id"])
        return g["group"]["id"]
    gid = run(go())
    assert [m["name"] for m in groupchat.get_group(gid)["messages"] if m["kind"] == "member"] == ["Victor", "Socrates"]
    assert "Start with your view on the topic" in json.dumps(f.requests[0][1].messages, ensure_ascii=False)
    g = groupchat.get_group(gid)["group"]
    assert g["round"] == 1 and g["title"].startswith("Should the sync") and g["title"].endswith("…")  # 没给名字：议题的第一句截一截


def test_at_one_member_answers_in_its_own_cyber_turn(genv, scripted):
    v, s, r = people(genv)
    gid = new([v, s, r], title="Sync library", topic="")
    groupchat.patch(gid, groupchat.GroupPatch(topic="Should the sync library be open source?"))
    f = scripted([("@Victor", {"chunks": ["Open core. ", "Charge for hosting."]})])

    async def ask():
        out = await groupchat.send(gid, groupchat.SendIn(text="@Victor how would you make money?"))
        assert out["how"] == "mention"
        await settle(gid)
    run(ask())
    ref, req = f.requests[0]
    assert ref == "moonshot/kimi-k3" and "You are Victor" in req.system and "## This group chat" in req.system
    assert "Topic: Should the sync library be open source?" in req.system and "Socrates (Cyber · Questioner)" in req.system
    asked = json.dumps(req.messages, ensure_ascii=False)
    assert "【Alex】@Victor how would you make money?" in asked and "Alex asked for you with" in asked
    assert len(f.requests) == 1  # 只有被 @ 的那一位说
    msgs = groupchat.get_group(gid)["messages"]
    last = msgs[-1]
    assert last["kind"] == "member" and last["speaker"] == v and last["text"] == "Open core. Charge for hosting."
    assert last["who"]["kind"] == "cyber" and last["who"]["tag"] == "Early investor"
    assert any(m["kind"] == "system" and m["text"] == "You asked Victor to answer" for m in msgs)
    turn = events.turns(f"group:{gid}:{v}")[-1]
    assert turn["kind"] == "cyber" and turn["origin"] == "user"  # 它自己在这个群的会话，赛博回合，替用户说的能花套餐
    with chat._lock, chat.db() as conn:
        row = conn.execute("SELECT role, origin, model FROM messages WHERE thread=? ORDER BY id DESC LIMIT 1", (gid,)).fetchone()
    assert (row["role"], row["origin"], row["model"]) == (ASSISTANT_ROLE, "group", "moonshot/kimi-k3")


def test_all_agents_is_one_round_where_an_at_goes_one_hop(genv, scripted):
    v, s, r = people(genv)
    gid = new([v, s, r], title="Race plan")
    f = scripted([
        ("You are Victor", {"text": "Pick a race in spring.\n@Running which spring races fit?"}),
        ("You are the Agent \"Running\"", {"text": "The city 10K in April. @Socrates agree?"}),  # 回答 @ 的那一条里再 @：不算
        ("You are Socrates", {"text": "Why a race at all?"}),
        ("Write the summary of round 1", summ(line="A spring 10K.")),
    ])

    async def go():
        out = await groupchat.send(gid, groupchat.SendIn(text="@所有 Agent what should I train for?"))
        assert out["how"] == "round"
        await settle(gid)
    run(go())
    msgs = [m for m in groupchat.get_group(gid)["messages"] if m["kind"] == "member"]
    assert [m["name"] for m in msgs] == ["Victor", "Running", "Socrates"]  # 被 @ 的提到前面，只转一层
    assert msgs[1]["replyTo"] == {"id": v, "name": "Victor"} and msgs[0]["at"]["id"] == r
    assert "at" not in msgs[1] and all(m["round"] == 1 for m in msgs)
    system = [m["text"] for m in groupchat.get_group(gid)["messages"] if m["kind"] == "system"]
    assert "One round · 3 answers in turn · TestMousse writes the summary" in system
    agent_req = next(req for _, req in f.requests if "Running" in req.system and "You are the Agent" in req.system)
    assert agent_req.tools == [] and "Plans runs and races" in agent_req.system and "no tools in this group chat" in agent_req.system
    assert "\"Victor\" @-mentioned you" in said(agent_req)
    assert events.turns(f"group:{gid}:{r}")[-1]["kind"] == "owner"  # 功能 Agent：主人回合（你的 Agent），零工具
    assert groupchat.get_group(gid)["group"]["round"] == 1
    u = groupchat.get_group(gid)["group"]["usage"]
    assert u["speeches"] == 3 and u["calls"] == 4 and u["tokens"] > 45 and u["estimated"]  # 发言照实记（10 进 5 出），小结那一问按字数估


def test_each_turn_reads_the_recent_twenty_and_newcomers_only_what_came_after(genv, scripted):
    v, s, r = people(genv)
    gid = new([v, r], title="Long one")
    with chat._lock, groupchat.gdb() as conn:
        for i in range(25):
            meetings.add_message(conn, gid, "user", f"note {i:02d}", None, None)
    f = scripted([("You are Victor", {"text": "ok"}), ("You are Socrates", {"text": "why?"})])

    async def go():
        await groupchat.send(gid, groupchat.SendIn(text="@Victor sum up"))
        await settle(gid)
    run(go())
    seen = json.dumps(f.requests[0][1].messages, ensure_ascii=False)
    assert "note 24" in seen and "note 06" in seen and "note 05" not in seen  # 最近 20 条（含 @ 它的那句）
    groupchat.patch(gid, groupchat.GroupPatch(members=[v, r, s]))  # 新加进来的：以前的不给

    async def go2():
        await groupchat.send(gid, groupchat.SendIn(text="@Socrates your turn"))
        await settle(gid)
    run(go2())
    seen = json.dumps(f.requests[1][1].messages, ensure_ascii=False)
    assert "@Socrates your turn" in seen and "note 24" not in seen and "sum up" not in seen
    assert any(m["text"] == "Socrates joined" for m in groupchat.get_group(gid)["messages"])


def test_the_host_arranges_one_member_at_a_time_and_stops_at_the_cap(genv, scripted):
    v, s, r = people(genv)
    gid = new([v, s], title="Pricing", mode="host")
    f = scripted([
        ("Should one member speak next", {"text": json.dumps({"next": v, "why": "money"})}),
        ("You are Victor", {"text": "Charge per seat."}),
        ("Should one member speak next", {"text": json.dumps({"next": None})}),
    ])

    async def go():
        out = await groupchat.send(gid, groupchat.SendIn(text="How should I price it?"))
        assert out["how"] == "arrange"
        await settle(gid)
    run(go())
    msgs = groupchat.get_group(gid)["messages"]
    assert [m["name"] for m in msgs if m["kind"] == "member"] == ["Victor"]
    assert any(m["text"] == "TestMousse asked Victor to answer" for m in msgs)
    assert not f.rules  # 问了两次主持人：请了一位，再问说不请
    # 主持人老是要请人：一句话最多请 ARRANGE_MAX 位（每次只请一位，刚说过的不请）
    f.rules = [("Should one member speak next", {"text": json.dumps({"next": s})}), ("You are Socrates", {"text": "Why price now?"}),
               ("Should one member speak next", {"text": json.dumps({"next": v})}), ("You are Victor", {"text": "Because runway."}),
               ("Should one member speak next", {"text": json.dumps({"next": s})}), ("You are Socrates", {"text": "Whose runway?"}),
               ("Should one member speak next", {"text": json.dumps({"next": v})})]

    async def go2():
        await groupchat.send(gid, groupchat.SendIn(text="Any other view?"))
        await settle(gid)
    run(go2())
    said = [m["name"] for m in groupchat.get_group(gid)["messages"] if m["kind"] == "member"][1:]
    assert said == ["Socrates", "Victor"] and len(said) <= groupchat.ARRANGE_MAX  # 第三次挑的是刚说过的 Socrates：不请
    u = groupchat.get_group(gid)["group"]["usage"]
    assert u["estimated"] and u["calls"] > u["speeches"]  # 主持人那几问没报用量：按字数估


def test_a_cyber_host_uses_its_own_model_and_no_host_means_only_at(genv, scripted):
    v, s, r = people(genv)
    gid = new([s, r], title="Hosted by Victor", host=v, mode="host")
    f = scripted([("Should one member speak next", {"text": json.dumps({"next": None})})])

    async def go():
        await groupchat.send(gid, groupchat.SendIn(text="Thoughts?"))
        await settle(gid)
    run(go())
    ref, req = f.requests[0]
    assert ref == "moonshot/kimi-k3" and "You are the Cyber \"Victor\"" in said(req) + req.system
    g = groupchat.patch(gid, groupchat.GroupPatch(host=""))["group"]
    assert g["host"] is None and g["mode"] == "mention"  # 不设主持人：只能 @
    f.requests.clear()

    async def go2():
        out = await groupchat.send(gid, groupchat.SendIn(text="Anyone?"))
        assert out["how"] == "none"
        await settle(gid)
    run(go2())
    assert f.requests == []  # 没 @ 谁：谁都不说


def test_to_do_lines_become_cards_only_the_owner_sees(genv, scripted):
    v, s, r = people(genv)
    gid = new([v, r], title="Goals")
    scripted([("You are Victor", {"text": "Aim for 40% 4-week retention.\n【要做】把「4 周留存 40%」设为目标\n[To do] hand to Running: book a 10K"}),
              ("You are the Agent \"Running\"", {"text": "I'll plan it.\n【要做】周三加一次间歇跑"})])

    async def go():
        await groupchat.send(gid, groupchat.SendIn(text="@Victor @Running ideas?"))
        await settle(gid)
    run(go())
    cards = groupchat.get_group(gid)["cards"]
    got = {(c["title"], c["source"], c["thread"]) for c in cards}
    assert got == {("把「4 周留存 40%」设为目标", v, "main"), ("book a 10K", v, r), ("周三加一次间歇跑", r, r)}
    goal = next(c for c in cards if c["thread"] == "main")
    assert goal["kind"] == "other" and goal["status"] == "pending" and goal["why"] == 'From Victor (group chat "Goals") · for TestMousse'
    victor_msg = next(m for m in groupchat.get_group(gid)["messages"] if m.get("speaker") == v)
    assert goal["groupMessage"] == victor_msg["id"] and goal["id"] in victor_msg["cards"]
    assert groupchat.get_group(gid)["group"]["cardsPending"] == 3
    rows = {x["id"]: x for x in genv.client.get("/api/chats?with=group", headers=H).json()["rows"]}
    assert rows[gid]["state"] == "waiting"  # 有等你点头的卡：琥珀点
    r2 = genv.client.post(f"/api/inbox/{goal['id']}", json={"action": "approve"}, headers=H)
    assert r2.status_code == 200
    assert genv.kicked and genv.kicked[0][0] == "main" and "【收件箱】" in genv.kicked[0][1]  # 同意了：交给主对话照做
    assert groupchat.get_group(gid)["group"]["cardsPending"] == 2


def test_chat_list_rows_unread_pin_and_delete(genv, scripted):
    v, s, r = people(genv)
    gid = new([v, s], title="Book club")
    scripted([("You are Victor", {"text": "Read the founder memoir."})])

    async def go():
        await groupchat.send(gid, groupchat.SendIn(text="@Victor pick a book"))
        await settle(gid)
    run(go())
    c = genv.client
    assert gid not in {x["id"] for x in c.get("/api/chats", headers=H).json()["rows"]}  # 老 app 不认 kind group：不说就不给
    data = c.get("/api/chats?with=group", headers=H).json()
    row = {x["id"]: x for x in data["rows"]}[gid]
    assert row["kind"] == "group" and row["title"] == "Book club" and row["line"] == "Victor: Read the founder memoir."
    assert row["unread"] == 1 and [f["name"] for f in row["faces"]] == ["Victor", "Socrates"] and data["endedGroups"] == 0
    assert c.get("/api/unread", headers=H).json()["threads"][gid]["n"] == 1
    assert c.patch(f"/api/chats/{gid}", json={"pinned": True}, headers=H).json()["pinned"] is True
    run(groupchat.end(gid))
    assert gid not in {x["id"] for x in c.get("/api/chats?with=group", headers=H).json()["rows"]}
    assert gid in {x["id"] for x in c.get("/api/chats?archived=1&with=group", headers=H).json()["rows"]}
    assert texts(gid)[-1] == ("system", "TestMousse", "Group chat ended")
    assert c.post(f"/api/groupchats/{gid}/send", json={"text": "hello?"}, headers=H).status_code == 409
    assert c.delete(f"/api/chats/{gid}", headers=H).json() == {"ok": True}
    assert c.get(f"/api/groupchats/{gid}", headers=H).status_code == 404
    with chat._lock, chat.db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM messages WHERE thread=?", (gid,)).fetchone()[0] == 0


def test_a_restart_mid_round_leaves_a_line_and_stop_ends_the_speaker(genv, scripted):
    v, s, r = people(genv)
    gid = new([v, s], title="Restart")
    with chat._lock, groupchat.gdb() as conn:
        conn.execute("UPDATE groupchats SET running=1 WHERE id=?", (gid,))
    assert texts(gid)[-1] == ("system", "TestMousse", "The server restarted; this didn't finish")
    scripted([("You are Victor", {"chunks": ["Long ", "answer ", "that ", "goes on"], "delay": 0.05})])

    async def go():
        await groupchat.send(gid, groupchat.SendIn(text="@所有 Agent go"))
        for _ in range(100):
            lv = groupchat.running(gid)
            if lv and lv.text:
                break
            await asyncio.sleep(0.01)
        await groupchat.stop(gid)
        await settle(gid)
    run(go())
    msgs = groupchat.get_group(gid)["messages"]
    said = [m for m in msgs if m["kind"] == "member"]
    assert len(said) == 1 and said[0]["text"].endswith("(Stopped)") and msgs[-1]["text"] == "Stopped"  # 后面的不再请


def test_group_content_goes_on_hold_not_into_the_tree(genv, scripted, mousse_tree, monkeypatch):
    import memhold
    import sources
    import treelib
    from engine import writeback
    mousse_tree.make(name="Alex")
    monkeypatch.setattr(sources, "memory_tree", treelib)
    monkeypatch.setattr(writeback, "_needs", {})
    with chat._lock, memhold.hdb() as conn:
        conn.execute("DELETE FROM memory_held")
    v, s, r = people(genv)
    gid = new([v], title="Move")
    scripted([("You are Victor", {"text": "Berlin has a good scene for this."})])

    async def go():
        await groupchat.send(gid, groupchat.SendIn(text="@Victor I'm moving to Berlin next spring, does that change things?"))
        await settle(gid)
    run(go())
    scripted([("group chat between the user", {"text": json.dumps({"candidates": [
        {"text": "Alex is moving to Berlin next spring.", "kind": "event", "about": "user", "quote": "I'm moving to Berlin next spring",
         "durable": 2, "new": 2, "stated": 2, "why": "plans"},
        {"text": "Alex wants a good scene.", "kind": "preference", "about": "user", "quote": "Berlin has a good scene for this.",
         "durable": 2, "new": 2, "stated": 0, "why": "Victor's guess"}]})})])
    held = run(groupchat.hold_pass(gid))
    assert [h["text"] for h in held] == ["Alex is moving to Berlin next spring."]  # 成员的猜测不算（stated 0）
    rows = memhold.listing()
    assert len(rows) == 1 and rows[0]["channel"] == "group" and rows[0]["who"] == "Alex" and rows[0]["thread"] == gid
    assert rows[0]["quote"] == "I'm moving to Berlin next spring"
    with chat._lock, groupchat.gdb() as conn:
        assert conn.execute("SELECT held FROM groupchats WHERE id=?", (gid,)).fetchone()["held"] > 0
    assert run(groupchat.hold_pass(gid)) == []  # 游标挪过去了：同一段不再挑
    with chat._lock, memhold.hdb() as conn:  # 待定的会接到每周修剪的卡上：别留给后面的测试
        conn.execute("DELETE FROM memory_held")


def test_cybers_off_means_their_seats_are_skipped(genv, scripted):
    v, s, r = people(genv)
    gid = new([v, r], title="Off", host="")
    genv.cfg(groupchat=True, user_name="Alex", cybers=False)
    scripted([("You are the Agent \"Running\"", {"text": "Still here."})])

    async def go():
        await groupchat.send(gid, groupchat.SendIn(text="@所有 Agent anyone?"))
        await settle(gid)
    run(go())
    msgs = groupchat.get_group(gid)["messages"]
    assert [m["name"] for m in msgs if m["kind"] == "member"] == ["Running"]  # 赛博关着：它的位子没人说，别的照常
    assert groupchat.get_group(gid)["group"]["members"][0]["gone"]
    with chat._lock, cybers.cydb() as conn:
        assert cybers.row_of(conn, v) is not None  # 赛博本身还在，只是关着


def test_an_agent_found_to_be_about_health_later_sits_out_while_a_cyber_is_there(genv, scripted):
    """健康类不和赛博同群：建群时查过；后来才认出来的（这里是写进 cybers.health_agents），每请一位之前现查：记一行，这次不请。"""
    v, s, r = people(genv)
    gid = new([v, r], title="Training", host="")
    genv.cfg(groupchat=True, user_name="Alex", cybers={"health_agents": [r]})
    f = scripted([("You are Victor", {"text": "Rest days matter."})])

    async def go():
        await groupchat.send(gid, groupchat.SendIn(text="@所有 Agent how should I train?"))
        await settle(gid)
    run(go())
    msgs = groupchat.get_group(gid)["messages"]
    assert [m["name"] for m in msgs if m["kind"] == "member"] == ["Victor"]
    assert "Running looks after health, diet or sleep and a Cyber is here, so it wasn't asked" in [m["text"] for m in msgs if m["kind"] == "system"]
    assert not any("Plans runs and races" in req.system for _, req in f.requests)  # 没请它：它的回合一次都没起


def test_a_cyber_asking_its_loaded_agent_in_a_group_shows_the_card_there(genv, scripted, monkeypatch):
    import bridges
    ran: list = []

    async def fake_run(name, args, stdin, agent_, *, kill_on_cancel=False):
        ran.append((name, list(args), agent_))
        return "ok"
    monkeypatch.setattr(bridges, "run", fake_run)
    v, s, r = people(genv)
    notes = agent(genv, "Notes library", icon="book", purpose="Keeps reading notes")
    genv.client.post(f"/api/cybers/{v}/loadout", json={"agent": notes}, headers=H)
    gid = new([v, s], title="Reading")
    scripted([
        ("You are Victor", {"text": "", "calls": [("c1", "ask_agent", {"agent": notes, "question": "add the memoir to the to-read list"})]}),
        ("add the memoir to the to-read list", {"text": "", "calls": [("n1", "board", {"args": ["rows", "add", "reading", "--data", "-"],
                                                                                         "input": '{"title": "founder memoir"}'})]}),
        ("approval-pending", {"text": "Asked; it's waiting for the OK."}),
        ("Nothing has changed yet", {"text": "Notes library will add it once you approve."}),
    ])

    async def go():
        await groupchat.send(gid, groupchat.SendIn(text="@Victor save the memoir for me"))
        await settle(gid)
    run(go())
    assert ran == []  # 改你的数据：先出卡，不直接做
    cards = groupchat.get_group(gid)["cards"]
    assert len(cards) == 1 and cards[0]["kind"] == "tool" and cards[0]["thread"] == v and cards[0]["via"] == "loadout"
    assert cards[0]["source"] == notes  # 卡照旧挂在赛博一对一的对话上（同意后的结果回那边），群里也列出来
    said_ = [m for m in groupchat.get_group(gid)["messages"] if m["kind"] == "member"]
    assert said_[-1]["text"] == "Notes library will add it once you approve." and said_[-1]["cards"] == [cards[0]["id"]]


# —— 小结和群记忆（第 16 件）——————————————————————————————————————————————————————————————————

def summ(**kw) -> dict:
    return {"text": json.dumps({"line": "", "decided": [], "disputed": [], "settled": [], "notes": [], **kw}, ensure_ascii=False)}


def round_of(gid: str, text: str = "@所有 Agent go") -> None:
    async def go():
        await groupchat.send(gid, groupchat.SendIn(text=text))
        await settle(gid)
    run(go())


def test_a_hosted_round_ends_with_a_summary_in_the_groups_memory(genv, scripted):
    import inbox as inbox_mod
    v, s, r = people(genv)
    gid = new([v, s], title="Open source?", topic="")
    f = scripted([
        ("You are Victor", {"text": "Open core, charge for hosting."}),
        ("You are Socrates", {"text": "Open for whom?"}),
        ("Write the summary of round 1", summ(line="Open core looks right; who it's for is open.", decided=[{"text": "Open core", "agreed": False}],
                                             disputed=[{"point": "Who the open part is for", "sides": [{"who": v, "says": "Developers"},
                                                                                                      {"who": s, "says": "Unclear"}]}],
                                             notes=[{"who": v, "text": "Alex cares about hosting revenue"}, {"who": "nobody", "text": "x"}])),
    ])
    round_of(gid)
    assert not f.rules
    msgs = groupchat.get_group(gid)["messages"]
    assert "One round · 2 answers in turn · TestMousse writes the summary" in [m["text"] for m in msgs if m["kind"] == "system"]
    sm = msgs[-1]
    assert sm["kind"] == "summary" and sm["round"] == 1 and sm["host"] == {"id": "main", "name": "TestMousse", "kind": "main"}
    assert sm["summary"]["decided"] == [{"text": "Open core", "agreed": False}]
    assert sm["summary"]["disputed"][0]["sides"][1] == {"who": s, "says": "Unclear", "name": "Socrates"}
    assert [n["who"] for n in sm["summary"]["notes"]] == [v]  # 不是成员的 who 不收
    mem = groupchat.get_memory(gid)
    assert mem["counts"] == {"decided": 1, "disputed": 1, "note": 1} and mem["decided"][0]["tag"].startswith("round 1 · ")
    assert mem["intro"].startswith("Tomorrow's talk starts from here")
    g = groupchat.get_group(gid)["group"]
    assert g["memory"] == {"decided": 1, "disputed": 1, "note": 1} and g["pinned"]["line"] == "Open core looks right; who it's for is open."
    rows = {x["id"]: x for x in genv.client.get("/api/chats?with=group", headers=H).json()["rows"]}
    assert rows[gid]["line"] == "TestMousse: Summary saved to the group's memory"
    with chat._lock, cybers.cydb() as conn:  # 赛博记下的：照「记下的」的规矩先提给你（卡挂在它一对一的对话上）
        c = conn.execute("SELECT * FROM cyber_changes WHERE cyber=? AND field='note'", (v,)).fetchone()
    assert c["via"] == "group" and c["new"] == "Alex cares about hosting revenue" and c["status"] == "pending"
    assert inbox_mod.item(c["inbox"])["thread"] == v and "Open source?" in c["why"]


def test_the_next_round_starts_from_the_pinned_summary_and_settles_disputes(genv, scripted):
    v, s, r = people(genv)
    gid = new([v, s], title="Pricing")
    scripted([("You are Victor", {"text": "Per seat."}), ("You are Socrates", {"text": "Why per seat?"}),
              ("Write the summary of round 1", summ(line="Per seat, maybe.", decided=[{"text": "Charge money"}],
                                                   disputed=[{"point": "Per seat or flat", "sides": [{"who": v, "says": "Per seat"}]}]))])
    round_of(gid)
    f = scripted([("You are Victor", {"text": "Flat is simpler, fine."}), ("You are Socrates", {"text": "Agreed."}),
                  ("Write the summary of round 2", summ(line="Flat price.", decided=[{"text": "Flat price", "agreed": True},
                                                                                     {"text": "Charge money", "agreed": True}],
                                                       settled=["Per seat or flat"]))])
    round_of(gid, "@所有 Agent I agree with flat, final word?")
    seen = said(f.requests[0][1])
    assert "Pinned summary (the group's memory)" in seen and "Round 1: Per seat, maybe." in seen and "- Charge money (round 1" in seen
    assert "Per seat or flat: Victor: Per seat" in seen
    summary_in = said(f.requests[2][1])
    assert "Charge money" in summary_in  # 小结看得到到上一轮为止的群记忆
    mem = groupchat.get_memory(gid)
    assert [d["text"] for d in mem["decided"]] == ["Charge money", "Flat price"] and mem["disputed"] == []  # 定下来的争议收起来，同样的不重复
    assert all(d["agreed"] for d in mem["decided"]) and mem["decided"][0]["tag"].endswith("you agreed")
    mem = groupchat.act_memory(gid, mem["decided"][0]["id"], groupchat.MemoryAct(action="unagree"))
    assert not mem["decided"][0]["agreed"]
    mem = groupchat.act_memory(gid, mem["decided"][0]["id"], groupchat.MemoryAct(action="remove"))
    assert [d["text"] for d in mem["decided"]] == ["Flat price"]


def test_no_host_means_no_summary(genv, scripted):
    v, s, r = people(genv)
    gid = new([v, s], title="Quiet", host="")
    scripted([("You are Victor", {"text": "One."}), ("You are Socrates", {"text": "Two?"})])  # 没给小结的剧本：调了就报错
    round_of(gid)
    msgs = groupchat.get_group(gid)["messages"]
    assert [m["kind"] for m in msgs][-2:] == ["member", "member"] and "One round · 2 answers in turn" in [m["text"] for m in msgs]
    assert groupchat.get_memory(gid)["counts"] == {"decided": 0, "disputed": 0, "note": 0}


def test_a_summary_goes_back_into_the_idea_it_came_from(genv, scripted):
    import think
    v, s, r = people(genv)
    idea = think.create_fragment(kind="text", text="Should the sync library be open source?")
    gid = run(groupchat.create(groupchat.GroupIn(members=[v], title="From an idea", source=groupchat.SourceIn(kind="idea", id=idea["id"]))))["group"]["id"]
    scripted([("You are Victor", {"text": "Open core."}), ("Write the summary of round 1", summ(line="Open core.", decided=[{"text": "Open core"}]))])
    round_of(gid)
    f = think.frag_by_id(idea["id"])
    raw = think.frag_path(f).read_text(encoding="utf8")
    assert raw.count(f"%% mousse:group {gid} %%") == 1 and "- Open core" in raw and "group: " + gid in raw
    assert "%%" not in f["text"] and "Open core" in f["text"] and f["text"].startswith("Should the sync library")  # 记号不当正文
    sm = groupchat.get_group(gid)["messages"][-1]
    assert sm["actions"]["writeback"]["id"] == idea["id"] and "error" not in sm["actions"]["writeback"]
    scripted([("You are Victor", {"text": "Still open core."}), ("Write the summary of round 2", summ(line="Hosting too.", decided=[{"text": "Hosted plan"}]))])
    round_of(gid)
    raw = think.frag_path(think.frag_by_id(idea["id"])).read_text(encoding="utf8")
    assert raw.count(f"%% mousse:group {gid} %%") == 1 and "- Hosted plan" in raw and "round 2" in raw  # 换成最新的，不往后堆


def test_the_three_actions_under_a_summary(genv, scripted, monkeypatch):
    import podmaterials
    import think
    started: list = []
    monkeypatch.setattr(meetings, "begin_round", lambda mid, invited, rnd, minutes, event="invite": started.append((mid, invited)))
    v, s, r = people(genv)
    notes = agent(genv, "Notes", icon="book", purpose="Keeps reading notes")
    gid = new([r, notes], title="Reading plan")
    scripted([("You are the Agent \"Running\"", {"text": "Run before reading."}), ("You are the Agent \"Notes\"", {"text": "Read first."}),
              ("Write the summary of round 1", summ(line="Order is open.", disputed=[{"point": "Run or read first", "sides": [{"who": r, "says": "Run"}]}]))])
    round_of(gid)
    out = run(groupchat.summary_writeback(gid, 1))  # 没有来源：记成一条新想法，以后写回到它
    fid = out["writeback"]["id"]
    assert think.frag_by_id(fid)["title"] == "Reading plan" and groupchat.get_group(gid)["group"]["source"]["id"] == fid
    out = run(groupchat.summary_meeting(gid, 1))
    assert out["meeting"]["topic"].endswith("Run or read first") and started and set(started[0][1]) == {r, notes}
    assert out["message"]["actions"]["meeting"]["id"] == out["meeting"]["id"]
    out = run(groupchat.summary_podcast(gid, 1))
    mats = podmaterials.listing(out["episode"]["id"])
    assert len(mats) == 1 and mats[0]["kind"] == "chat" and "Order is open." in mats[0]["preview"]
    assert set(groupchat.get_group(gid)["messages"][-1]["actions"]) == {"writeback", "meeting", "podcast"}
    with pytest.raises(Exception):
        run(groupchat.summary_meeting(gid, 7))  # 没有这一轮的小结：404


def test_ending_tells_the_main_chat_once_with_a_summary_card(genv, scripted):
    v, s, r = people(genv)
    gid = new([v, s], title="Wrap up")
    scripted([("You are Victor", {"text": "Ship it."}), ("You are Socrates", {"text": "Ship what?"}),
              ("Write the summary of round 1", summ(line="Ship the beta.", decided=[{"text": "Ship the beta"}],
                                                   disputed=[{"point": "What's in it", "sides": []}]))])
    round_of(gid)
    with chat._lock, chat.db() as conn:
        before = conn.execute("SELECT COUNT(*) FROM messages WHERE thread='main'").fetchone()[0]
    run(groupchat.end(gid))
    with chat._lock, chat.db() as conn:
        rows = conn.execute("SELECT role, text, origin FROM messages WHERE thread='main' ORDER BY id DESC LIMIT 1").fetchall()
        after = conn.execute("SELECT COUNT(*) FROM messages WHERE thread='main'").fetchone()[0]
    assert after == before + 1 and rows[0]["text"] == 'The group chat "Wrap up" is over; the summary is in its memory:' and rows[0]["origin"] == "group"
    cards = [c for c in genv.client.get("/api/chat/cards?thread=main", headers=H).json()["cards"] if c.get("group")]
    assert len(cards) == 1 and cards[0]["kind"] == "minutes" and cards[0]["meeting"] == gid and cards[0]["decisions"] == ["Ship the beta"]
    assert cards[0]["open"] == ["What's in it"] and cards[0]["line"] == "Ship the beta." and cards[0]["status"] == "ended"
    run(groupchat.end(gid))  # 再点一次：不再说
    with chat._lock, chat.db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM messages WHERE thread='main'").fetchone()[0] == after
    run(groupchat.delete(gid))
    assert not [c for c in genv.client.get("/api/chat/cards?thread=main", headers=H).json()["cards"] if c.get("group")]
    with chat._lock, chat.db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM messages WHERE thread='main'").fetchone()[0] == before  # 那一行也删了
