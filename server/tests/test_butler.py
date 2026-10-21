"""管家（butler.py，主线第三步第三段）：名片 Agent 升级来的对外身份。默认关；例句只试答（不发、不记、缓存）；「改这一句」先给样子、
点头才写进档位、能撤销；请教赛博只带要点（没有名字和原话，健康的不去问）；说过的话里 Doorman 扣下的放行 / 不放走原来那张卡。
不连真模型：模型是假的（按内容回放）；人和事都是编的。"""
from __future__ import annotations

import asyncio
import json

import pytest

import butler
import cardagent
import chat
import inbox
import social
from engine import providers
from test_cybers import H, env, groups_clean, make  # noqa: F401 — 赛博那边的 fixture（builtin、假密钥）和建赛博的小函数
from test_groupchat import ByContent, said

ENGINE = {"model": "deepseek/deepseek-flash", "models": ["deepseek/deepseek-flash", "moonshot/kimi-k3"], "use": {"card": "deepseek/deepseek-flash"}}


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def benv(env):  # noqa: F811 — env 是导入的 fixture
    env.cfg(butler=True, user_name="Alex", engine=ENGINE)
    social.set_setting("butler", None)
    social.set_setting("tiers", None)
    butler._cache.clear()
    with chat._lock, butler.bdb() as conn:
        conn.execute("DELETE FROM butler_changes")
        conn.execute("DELETE FROM butler_consults")
    with chat._lock, cardagent.cdb() as conn:
        conn.execute("DELETE FROM card_log")
        conn.execute("DELETE FROM card_asks")
    with chat._lock, inbox.idb() as conn:
        conn.execute("DELETE FROM inbox WHERE kind='social'")  # 别的测试留下的社交卡（好友群里约的时间之类）
    return env


@pytest.fixture
def scripted(monkeypatch):
    def make_(rules):
        f = ByContent(rules)
        monkeypatch.setattr(providers, "stream", f.stream)
        return f
    return make_


def reply(text: str, ask=None) -> dict:
    return {"text": json.dumps({"reply": text, "used": [], "ask_owner": ask, "declined": []})}


def card_lines() -> int:
    with chat._lock, cardagent.cdb() as conn:
        return conn.execute("SELECT COUNT(*) FROM card_log").fetchone()[0]


def test_off_by_default_and_only_on_the_builtin_engine(benv):
    benv.cfg(butler=None, user_name="Alex", engine=ENGINE)
    assert benv.client.get("/api/butler", headers=H).status_code == 404
    assert benv.client.get("/api/contacts", headers=H).json()["butler"]["open"] == "CardAgent"  # 没开：还是名片 Agent
    benv.cfg(butler=True, user_name="Alex", engine=ENGINE, claw={"kind": "openclaw"})
    assert benv.client.get("/api/butler", headers=H).status_code == 404
    benv.cfg(butler=True, user_name="Alex", engine=ENGINE)
    p = benv.client.get("/api/butler", headers=H).json()
    assert p["name"] == "Alex's butler" and p["avatar"] == {"orbit": True} and p["model"]["name"] == "DeepSeek Flash"
    assert [r["id"] for r in p["rules"]] == ["ask", "health"] and "Health, diet and sleep" in p["rules"][1]["text"]
    assert p["week"] == {"said": 0, "held": 0} and "butler" in benv.client.get("/api/health", headers=H).json()["server"]["features"]
    row = benv.client.get("/api/contacts", headers=H).json()["butler"]
    assert row["open"] == "Butler" and row["name"] == "Alex's butler"
    p = benv.client.patch("/api/butler", json={"name": "Jeeves"}, headers=H).json()
    assert p["name"] == "Jeeves" and p["customName"] is True
    assert benv.client.patch("/api/butler", json={"name": ""}, headers=H).json()["name"] == "Alex's butler"  # 空 = 回到默认


def test_samples_are_only_tried_cached_and_never_sent_or_logged(benv, scripted):
    f = scripted([("Are you busy this week?", reply("Busy on Thursday evening, free on Friday.")),
                  ("What have you been up to lately?", reply("Revising for finals.")),
                  ("Where do you live?", reply("That's not something I can share."))])
    out = benv.client.get("/api/butler/samples?tier=friend", headers=H).json()
    assert [(i["key"], i["category"], i["level"]) for i in out["items"]] == [("busy", "calendar", "busy"), ("doing", "status", "line"), ("where", "address", "none")]
    assert out["items"][0]["reply"] == "Busy on Thursday evening, free on Friday." and [o["level"] for o in out["items"][0]["options"]] == ["detail", "busy", "none"]
    assert out["items"][0]["options"][1]["current"] is True and out["items"][2]["options"][1]["example"] == "This information can't be shared."
    assert all("You are the card agent of Alex" in req.system for _ref, req in f.requests)  # 和真的回答同一套
    assert card_lines() == 0  # 不进「说过的话」
    with chat._lock, inbox.idb() as conn:
        assert conn.execute("SELECT COUNT(*) FROM inbox WHERE kind='social'").fetchone()[0] == 0  # 不出卡
    again = benv.client.get("/api/butler/samples?tier=friend", headers=H).json()  # 缓存：模型不再被问（ByContent 的条用完了，再问就报错）
    assert all(i["cached"] for i in again["items"]) and again["items"][1]["reply"] == "Revising for finals."


def test_changing_a_line_previews_first_applies_on_ok_and_undoes(benv, scripted):
    scripted([("Are you busy this week?", reply("Busy on Thursday evening.")), ("Are you busy this week?", reply("This question needs an answer from Alex."))])
    c = benv.client
    assert c.post("/api/butler/samples/friend/busy/preview", json={"level": "busy"}, headers=H).status_code == 409  # 现在就是这样
    assert c.post("/api/butler/samples/friend/busy/preview", json={"level": "loud"}, headers=H).status_code == 400
    ch = c.post("/api/butler/samples/friend/busy/preview", json={"level": "none"}, headers=H).json()["change"]
    assert (ch["old"], ch["new"], ch["status"]) == ("busy", "none", "pending") and ch["before"] == "Busy on Thursday evening."
    assert ch["after"] == "This question needs an answer from Alex." and ch["newLabel"] == "Don't say"
    assert social.tier_scopes("friend")["calendar"] == "busy"  # 先给样子，没写进去
    done = c.post(f"/api/butler/changes/{ch['id']}", json={"action": "apply"}, headers=H).json()["change"]
    assert done["status"] == "applied" and social.tier_scopes("friend")["calendar"] == "none"
    assert c.post(f"/api/butler/changes/{ch['id']}", json={"action": "apply"}, headers=H).status_code == 409
    back = c.post("/api/butler/undo", json={}, headers=H).json()["change"]
    assert back["status"] == "undone" and social.tier_scopes("friend")["calendar"] == "busy"
    assert c.post("/api/butler/undo", json={}, headers=H).status_code == 409


def test_consulting_a_cyber_takes_only_the_gist(benv, scripted):
    benv.cfg(butler=True, user_name="Alex", engine=ENGINE, cybers=True)
    v = make(benv, "Victor", tag="Early investor", identity="An early-stage investor.")["id"]
    s = make(benv, "Socrates")["id"]
    benv.client.patch("/api/butler", json={"cybers": [v]}, headers=H)
    q = "Mia here: should Alex open-source the sync library? Asking for the reading group on Saturday."
    f = scripted([("Turn it into the gist", {"text": json.dumps({"gist": "Whether Mia should open-source a sync library"})}),
                  ("You are Victor", {"text": "Open core is the safer bet."})])
    out = run(butler.consult(v, q, peer="f-1", peer_name="Mia", ref="grp:test"))
    assert out["view"] == "Victor's view: Open core is the safer bet." and out["gist"] == "Whether they should open-source a sync library"
    cyber_saw = said(f.requests[1][1])
    assert "open-source a sync library" in cyber_saw and "Mia" not in cyber_saw and "reading group" not in cyber_saw  # 没有名字和原话
    assert "Mia" not in f.requests[1][1].system
    items = benv.client.get("/api/butler/consults", headers=H).json()["items"]
    assert items[0]["name"] == "Victor" and items[0]["line"].startswith("Only you can see this: asked Victor")
    assert run(butler.consult(v, "Is Alex sleeping enough these days?", peer_name="Mia")) is None  # 健康的不去问（不调模型）
    assert run(butler.consult(s, q, peer_name="Mia")) is None  # 不在名单上
    scripted([("Turn it into the gist", {"text": json.dumps({"gist": q})})])  # 照抄原话：不去问
    assert run(butler.consult(v, q, peer_name="Someone")) is None


def test_said_lines_and_doorman_release_or_keep(benv, monkeypatch):
    got: list = []

    async def deliver(ask, text, data):
        got.append((ask["ref"], text, data["outcome"]))
        return True
    monkeypatch.setitem(cardagent.DELIVER, "test", deliver)
    peer = {"key": "f-test", "name": "Mia", "tier": "friend"}
    cardagent.write_log(peer, "test", "r1", "out", "Alex is free on Friday.", "sent", [], None, {"sentinel": {"verdict": "pass"}})

    async def held(ref: str, original: str) -> str:
        ask = await cardagent.ask_owner(peer, "test", ref, {"kind": "review", "summary": "had an address", "proposal": None, "original": original,
                                                           "used": [], "usedNames": [], "reasons": ["address"]}, "Where?", "en", {})
        return cardagent.write_log(peer, "test", ref, "out", cardagent.say("hold", "en"), "sent", [], ask["inbox_id"],
                                   {"sentinel": {"verdict": "hold", "reasons": ["address"]}})
    keep_id = run(held("r2", "Alex lives at 12 Elm St."))
    rel_id = run(held("r3", "Alex is around on Saturday."))
    out = benv.client.get("/api/butler/said", headers=H).json()
    assert out["week"] == {"said": 3, "held": 2} and len(out["items"]) == 3
    row = next(i for i in out["items"] if i["id"] == keep_id)
    assert row["held"] and row["inboxId"] and row["original"] == "Alex lives at 12 Elm St." and row["reasons"] == ["address"] and row["where"] == "To Mia"
    c = benv.client
    assert c.post(f"/api/butler/said/{keep_id}", json={"action": "keep"}, headers=H).json()["item"]["status"] == "rejected"
    assert c.post(f"/api/butler/said/{rel_id}", json={"action": "release"}, headers=H).status_code == 200
    assert got == [("r2", "This question needs an answer from Alex.", "withheld"), ("r3", "Alex is around on Saturday.", "released")]
    assert c.post(f"/api/butler/said/{keep_id}", json={"action": "keep"}, headers=H).status_code == 409  # 已经定过了
    assert c.post("/api/butler/said/cl-nothing", json={"action": "keep"}, headers=H).status_code == 404
