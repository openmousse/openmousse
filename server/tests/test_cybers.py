"""赛博（主线第三步，cybers.py）：存起来、建 / 改 / 删（删 = 记忆移进归档）、形象、开关；一对一走赛博回合、对话列表和未读。
不连真模型、不花钱：模型是假的（test_engine.FakeModel 按剧本回放），密钥是假的；名字、人和事都是编的。"""
from __future__ import annotations

import asyncio
import json
import time

import pytest
from fastapi.testclient import TestClient

import chat
import config
import cybers
from engine import events, providers
from stored_values import ASSISTANT_ROLE
from test_engine import FakeModel

H = {"Authorization": "Bearer tok-phone"}
BASE = {"claw": {"kind": "builtin"}, "auth": {"tokens": {"phone": "tok-phone"}}, "timezone": "UTC", "language": "en",
        "engine": {"model": "deepseek/deepseek-flash", "models": ["deepseek/deepseek-flash", "moonshot/kimi-k3"]}, "cybers": True}


def run(coro):
    return asyncio.run(coro)


async def until(cond, timeout: float = 5.0) -> None:
    t0 = time.monotonic()
    while not cond():
        if time.monotonic() - t0 > timeout:
            raise AssertionError("timed out waiting")
        await asyncio.sleep(0.01)


@pytest.fixture
def fake(monkeypatch):
    def make(script):
        f = FakeModel(script)
        monkeypatch.setattr(providers, "stream", f.stream)
        return f
    return make


@pytest.fixture
def env(server_json, tmp_path, monkeypatch):
    """claw builtin、开着赛博；赛博的表清空（库是几个测试共用的）；不推送。cfg(**改动) 在 BASE 上叠一份新的 server.json。"""
    import main
    import push
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-cybers-0000")
    monkeypatch.setenv("MOONSHOT_API_KEY", "sk-test-cybers-1111")
    monkeypatch.setattr(config.settings, "workspace", tmp_path / "ws")

    async def no_push(*a, **k):
        return {"ok": True, "sent": 0}
    monkeypatch.setattr(push, "send_push", no_push)

    def cfg(**extra):
        return server_json({**BASE, **extra})
    cfg()
    with chat._lock, cybers.cydb() as conn:
        conn.execute("DELETE FROM cybers")
        conn.execute("INSERT OR REPLACE INTO cyber_meta(key, value) VALUES('defaults', 'tests')")  # 默认的两位：专门的测试自己清掉这一条

    class E:
        pass
    e = E()
    e.cfg, e.client = cfg, TestClient(main.app)
    return e


def make(env, name: str, **extra) -> dict:
    r = env.client.post("/api/cybers", json={"name": name, **extra}, headers=H)
    assert r.status_code == 200, r.text
    return r.json()["cyber"]


def test_off_by_default_and_off_without_the_builtin_engine(env):
    env.cfg(cybers=False)
    assert env.client.get("/api/cybers", headers=H).status_code == 404
    env.cfg(cybers=None)
    assert env.client.post("/api/cybers", json={"name": "Victor"}, headers=H).status_code == 404
    env.cfg(claw={"kind": "openclaw"})  # 赛博回合在自己的内核里：OpenClaw 那条路没有
    assert env.client.get("/api/cybers", headers=H).status_code == 404
    env.cfg(cybers={"enabled": False})
    assert env.client.get("/api/cybers", headers=H).status_code == 404
    env.cfg()
    assert env.client.get("/api/cybers", headers=H).json() == {"ok": True, "cybers": []}


def test_create_read_update_with_numbers_that_never_come_back(env):
    v = make(env, "Victor", tag="Early investor", kind="pro", identity="An early-stage investor.", tone="Direct.", rules="Asks for numbers.")
    assert cybers.ID_RE.match(v["id"]) and v["thread"] == v["id"] and v["no"] == 1 and v["days"] == 1
    assert v["avatar"] == cybers.DEFAULT_AVATAR and v["model"] is None and v["modelInUse"] == "deepseek/deepseek-flash"
    assert v["modelName"] == "DeepSeek Flash" and v["loadout"] == [] and v["last"] is None
    k = make(env, "Kai", model="moonshot/kimi-k3")
    assert k["no"] == 2 and k["modelName"] == "Kimi K3"
    got = env.client.get(f"/api/cybers/{v['id']}", headers=H).json()["cyber"]
    assert got["identity"] == "An early-stage investor." and got["tag"] == "Early investor"
    r = env.client.patch(f"/api/cybers/{v['id']}", json={"tone": "Direct, never rude.", "identity": "An early-stage investor."}, headers=H).json()
    assert r["changed"] == ["tone"] and r["cyber"]["tone"] == "Direct, never rude."
    r = env.client.patch(f"/api/cybers/{k['id']}", json={"model": None}, headers=H).json()  # null = 回到默认
    assert r["changed"] == ["model"] and r["cyber"]["model"] is None
    assert env.client.delete(f"/api/cybers/{v['id']}", headers=H).json() == {"ok": True}
    assert env.client.get(f"/api/cybers/{v['id']}", headers=H).status_code == 404
    assert [c["name"] for c in env.client.get("/api/cybers", headers=H).json()["cybers"]] == ["Kai"]
    assert make(env, "Victor")["no"] == 3  # 删了的名字能再用，编号不回收
    with chat._lock, cybers.cydb() as conn:  # 删了的那一行还在（不真删）
        assert conn.execute("SELECT deleted_at FROM cybers WHERE id=?", (v["id"],)).fetchone()["deleted_at"]


def test_checks_names_models_and_avatars(env):
    make(env, "Rachel")
    assert env.client.post("/api/cybers", json={"name": "rachel"}, headers=H).status_code == 409  # 群里 @ 它不能认错
    assert env.client.post("/api/cybers", json={"name": "  "}, headers=H).status_code == 400
    r = env.client.post("/api/cybers", json={"name": "Sam", "model": "claude-code/opus"}, headers=H)
    assert r.status_code == 400 and "plan" in r.json()["detail"]  # 赛博暂时不能用套餐
    assert env.client.post("/api/cybers", json={"name": "Sam", "model": "nonsense"}, headers=H).status_code == 400
    assert env.client.post("/api/cybers", json={"name": "Sam", "avatar": {"hair": "mohawk"}}, headers=H).status_code == 400
    assert env.client.post("/api/cybers", json={"name": "Sam", "avatar": {"upload": "0123456789ab"}}, headers=H).status_code == 400
    m = make(env, "Quiz", kind="machine", avatar={"machine": "timer"})
    assert m["avatar"] == {"machine": "timer", "bg": "cyan"} and m["kind"] == "machine"
    s = make(env, "Sam", avatar={"hair": "curly", "acc": {"glasses": 1, "wings": True}})
    assert s["avatar"]["hair"] == "curly" and s["avatar"]["acc"] == {"glasses": True, "phones": False, "earring": False, "hat": False}
    assert s["avatar"]["skin"] == cybers.DEFAULT_AVATAR["skin"]  # 缺的照默认补
    up = env.client.post("/api/chat/upload", data={"thread": s["id"]}, files={"files": ("me.png", b"\x89PNG\r\n\x1a\nnot really", "image/png")},
                         headers=H).json()["attachments"][0]
    r = env.client.patch(f"/api/cybers/{s['id']}", json={"avatar": {"upload": up["id"]}}, headers=H).json()
    assert r["cyber"]["avatar"] == {"upload": up["id"]}


def test_random_avatars_keep_background_and_clothes_apart():
    import random
    rng = random.Random(7)
    seen = [cybers.random_avatar(rng) for _ in range(300)]
    assert all(cybers.FAMILY[a["clothes"]] != a["bg"] for a in seen)  # 20 像素时只剩轮廓和颜色：底色和衣服颜色要分得开
    beards = sum(a["beard"] == "none" for a in seen) / len(seen)
    hats = sum(a["acc"]["hat"] for a in seen) / len(seen)
    assert 0.5 < beards < 0.7 and 0.07 < hats < 0.25
    p = cybers.presets("Rachel")
    assert p == cybers.presets("Rachel") and len({a["bg"] for a in p}) == 3  # 同一个名字每次一样，三个底色不同


def test_pretty_model_names():
    assert cybers.pretty_model("anthropic/claude-sonnet-5-5") == "Claude Sonnet 5.5"
    assert cybers.pretty_model("moonshot/kimi-k2.6") == "Kimi K2.6"
    assert cybers.pretty_model("ollama/qwen3:8b") == "Qwen3:8b"
    assert cybers.pretty_model(None) == ""


# —— 一对一：赛博回合 ——————————————————————————————————————————————————————————————————————————

def talk(thread: str, text: str):
    """在 app 的这个对话里说一句（同 /api/chat/send 那条路），等它回完 → 那一轮的 Run。"""
    async def go():
        r = chat.start_run(thread, text, None)
        await until(lambda: r.done)
        return r
    return run(go())


def test_a_cyber_chat_runs_a_cyber_turn_with_its_own_model(env, fake):
    v = make(env, "Victor", tag="Early investor", identity="An early-stage investor.", tone="Direct.", rules="Asks for retention numbers.",
             model="moonshot/kimi-k3")
    f = fake([{"chunks": ["What's ", "your 4-week retention?"]}, {"text": "40% is a start."}])
    r = talk(v["id"], "I'm building a sync library.")
    assert r.status == "ok" and r.text == "What's your 4-week retention?" and r.model == "moonshot/kimi-k3"
    ref, req = f.requests[0]
    assert ref == "moonshot/kimi-k3" and "You are Victor" in req.system and "An early-stage investor." in req.system
    assert "Tone: Direct." in req.system and "Rules: Asks for retention numbers." in req.system
    assert [t["name"] for t in req.tools] == ["propose_change"]  # 没装载：只有提改法的那一个（kind 只有 cyber）
    assert req.messages[-1]["role"] == "user" and "I'm building a sync library." in str(req.messages[-1]["content"])
    turn = events.turns(f"cyber:{v['id']}")[0]
    assert turn["kind"] == "cyber" and turn["origin"] == "user"  # 赛博回合，不是主人回合
    with chat._lock, chat.db() as conn:
        rows = conn.execute("SELECT role, text, model, origin FROM messages WHERE thread=? ORDER BY id", (v["id"],)).fetchall()
    assert [(x["role"], x["text"], x["model"]) for x in rows] == [("user", "I'm building a sync library.", None),
                                                                   (ASSISTANT_ROLE, "What's your 4-week retention?", "moonshot/kimi-k3")]
    talk(v["id"], "About 40%.")
    assert [m["role"] for m in f.requests[1][1].messages] == ["user", "assistant", "user"]  # 接在它今天的会话后面


def test_cyber_turns_never_spend_the_owners_plan(env, fake):
    env.cfg(engine={**BASE["engine"], "use": {"cyber": "claude-code/opus"}})  # 赛博的默认是用户自己的 Claude Code：换成不花套餐的
    k = make(env, "Kai")
    assert k["modelInUse"] == "deepseek/deepseek-flash"
    f = fake([{"text": "Hi."}])
    talk(k["id"], "hello")
    assert f.requests[0][0] == "deepseek/deepseek-flash"


def test_chat_rows_and_unread_follow_the_conversation(env, fake):
    k = make(env, "Kai", tag="Code advisor")
    assert not any(r["id"] == k["id"] for r in env.client.get("/api/chats?with=cyber", headers=H).json()["rows"])  # 聊过才出现
    fake([{"text": "Three of them touch your sync module."}])
    talk(k["id"], "anything new on the radar?")
    rows = {r["id"]: r for r in env.client.get("/api/chats?with=cyber", headers=H).json()["rows"]}
    row = rows[k["id"]]
    assert row["kind"] == "cyber" and row["title"] == "Kai" and row["tag"] == "Code advisor" and row["model"] == "DeepSeek Flash"
    assert row["line"] == "Three of them touch your sync module." and row["unread"] == 1
    assert k["id"] not in {r["id"] for r in env.client.get("/api/chats", headers=H).json()["rows"]}  # 老 app 不说要就不给
    assert env.client.get("/api/unread", headers=H).json()["threads"][k["id"]]["n"] == 1
    env.client.post("/api/unread/read", json={"thread": k["id"]}, headers=H)
    assert k["id"] not in env.client.get("/api/unread", headers=H).json()["threads"]
    assert env.client.patch(f"/api/chats/{k['id']}", json={"pinned": True}, headers=H).json()["pinned"] is True
    assert env.client.delete(f"/api/chats/{k['id']}", headers=H).status_code == 400  # 删赛博走它自己的接口


def test_no_cyber_chat_when_off_or_deleted(env, fake):
    import fastapi
    k = make(env, "Kai")
    env.client.delete(f"/api/cybers/{k['id']}", headers=H)
    with pytest.raises(fastapi.HTTPException) as e:
        chat.start_run(k["id"], "hello", None)
    assert e.value.status_code == 404
    j = make(env, "Jun")
    env.cfg(cybers=False)
    with pytest.raises(fastapi.HTTPException) as e:
        chat.start_run(j["id"], "hello", None)
    assert e.value.status_code == 404


# —— 记忆、改法、回看 ——————————————————————————————————————————————————————————————————————————

def wait_idle(thread: str, timeout: float = 5.0) -> None:
    """收件箱点了同意以后，服务器在那个对话里补的一轮（TestClient 自己的事件循环里跑）：等它跑完。"""
    t0 = time.monotonic()
    while (r := chat.RUNS.get(thread)) is not None and not r.done:
        if time.monotonic() - t0 > timeout:
            raise AssertionError("timed out waiting")
        time.sleep(0.02)


def card_of_change(c: dict) -> dict:
    import inbox
    return inbox.item(c["inbox"])


def test_a_proposed_note_waits_for_the_owner_then_shows_in_the_next_turn(env, fake):
    k = make(env, "Kai")
    c = run(cybers.propose(k["id"], "note", "Prefers five options per recommendation.", via="review", why="They said: always give me five."))
    assert c["status"] == "pending" and c["inbox"]
    card = card_of_change(c)
    assert card["kind"] == "tool" and card["thread"] == k["id"] and card["sourceName"] == "Kai"  # 挂在这位赛博的对话上，写它的名字
    assert card["changes"] == ["Keep: Prefers five options per recommendation."] and card["approveLabel"] == "Keep"
    assert env.client.get(f"/api/cybers/{k['id']}/notes", headers=H).json()["count"] == 0  # 没点头之前不记
    f = fake([{"text": "Noted."}, {"text": "Here are five."}])
    r = env.client.post(f"/api/inbox/{c['inbox']}", json={"action": "approve"}, headers=H).json()
    assert r["item"]["status"] == "done" and "Kept: Prefers five options" in r["item"]["result"]
    wait_idle(k["id"])  # 「已同意…」发回它的对话，它简短回一句
    notes = env.client.get(f"/api/cybers/{k['id']}/notes", headers=H).json()
    assert [n["text"] for n in notes["notes"]] == ["Prefers five options per recommendation."] and notes["notes"][0]["source"] == "review"
    talk(k["id"], "recommend some sync libraries")
    sys = f.requests[-1][1].system
    assert "## What you've kept" in sys and "Prefers five options per recommendation." in sys
    got = env.client.get(f"/api/cybers/{k['id']}", headers=H).json()["cyber"]
    assert got["notes"] == 1 and got["recent"]["field"] == "note"  # 资料页：记下 1 条、「刚改 · 撤销」
    env.client.post(f"/api/cybers/{k['id']}/undo", json={}, headers=H)
    assert env.client.get(f"/api/cybers/{k['id']}/notes", headers=H).json()["count"] == 0  # 撤销 = 删掉那一条


def test_rejected_cards_drop_the_change_and_stale_ones_do_not_apply(env):
    k = make(env, "Kai", tone="Casual.")
    c = run(cybers.propose(k["id"], "tone", "Casual, and brief.", via="review"))
    env.client.post(f"/api/inbox/{c['inbox']}", json={"action": "reject", "note": "no"}, headers=H)
    ch = env.client.get(f"/api/cybers/{k['id']}/changes", headers=H).json()
    assert ch["pending"] == []  # 卡在收件箱里拒了：这边也不算数
    c2 = run(cybers.propose(k["id"], "tone", "Casual, and warmer.", via="profile"))
    assert "card" not in c2 and c2["inbox"] is None  # 资料页上是你自己点的：不出卡
    env.client.patch(f"/api/cybers/{k['id']}", json={"tone": "Formal."}, headers=H)  # 这期间又直接改过
    r = env.client.post(f"/api/cybers/{k['id']}/changes/{c2['id']}", json={"action": "apply"}, headers=H)
    assert r.status_code == 409 and env.client.get(f"/api/cybers/{k['id']}", headers=H).json()["cyber"]["tone"] == "Formal."
    c3 = run(cybers.propose(k["id"], "rules", "Ask one question at a time.", via="profile"))
    r = env.client.post(f"/api/cybers/{k['id']}/changes/{c3['id']}", json={"action": "apply"}, headers=H).json()
    assert r["version"]["field"] == "rules" and env.client.get(f"/api/cybers/{k['id']}", headers=H).json()["cyber"]["rules"] == "Ask one question at a time."
    assert env.client.post(f"/api/cybers/{k['id']}/changes/{c3['id']}", json={"action": "apply"}, headers=H).status_code == 409  # 只改一次
    vers = env.client.get(f"/api/cybers/{k['id']}/versions", headers=H).json()["versions"]
    assert [v["field"] for v in vers][:2] == ["rules", "tone"]
    r = env.client.post(f"/api/cybers/{k['id']}/undo", json={"version": vers[1]["id"]}, headers=H)
    assert r.status_code == 200 and r.json()["cyber"]["tone"] == "Casual."  # 回到上一版
    assert env.client.post(f"/api/cybers/{k['id']}/undo", json={"version": vers[1]["id"]}, headers=H).status_code == 404


def tool_results(req) -> list[str]:
    return [str(m.get("content")) for m in req.messages if m.get("role") == "tool"]


def test_saying_it_in_the_chat_proposes_a_change_on_a_card(env, fake):
    import inbox
    k = make(env, "Kai", rules="Recommends three options.")
    f = fake([{"calls": [("c1", "propose_change", {"field": "rules", "value": "Recommends five options each time.", "why": "From now on, five."}),
                         ("c2", "propose_change", {"field": "note", "value": "Sleeps badly before releases."})]},
              {"text": "I've put that in front of you."}, {"text": "Five from now on."}])
    r = talk(k["id"], "From now on recommend five each time. Also I sleep badly before releases.")
    assert r.text == "I've put that in front of you."
    first, second = f.requests[0][1], f.requests[1][1]
    assert [t["name"] for t in first.tools] == ["propose_change"] and "- propose_change:" in first.system
    done, refused = tool_results(second)
    assert "waiting for" in done and "rules: Recommends three options. -> Recommends five options each time." in done
    assert "Health, diet and sleep" in refused  # 健康、饮食、睡眠不记
    pend = env.client.get(f"/api/cybers/{k['id']}/changes", headers=H).json()["pending"]
    assert [(c["field"], c["via"], c["why"]) for c in pend] == [("rules", "chat", "From now on, five.")]
    card = inbox.item(pend[0]["inbox"])
    assert card["kind"] == "tool" and card["thread"] == k["id"] and card["approveLabel"] == "Change"
    assert card["changes"] == ["Before: Recommends three options.", "After: Recommends five options each time."]
    assert env.client.get(f"/api/cybers/{k['id']}", headers=H).json()["cyber"]["rules"] == "Recommends three options."  # 点头之前不改
    res = env.client.post(f"/api/inbox/{card['id']}", json={"action": "approve"}, headers=H).json()
    assert res["item"]["status"] == "done" and "Changed: Rules" in res["item"]["result"]
    wait_idle(k["id"])
    assert env.client.get(f"/api/cybers/{k['id']}", headers=H).json()["cyber"]["rules"] == "Recommends five options each time."
    v = env.client.get(f"/api/cybers/{k['id']}/changes", headers=H).json()["versions"][0]
    assert v["via"] == "chat" and v["inbox"] == card["id"]  # 对话里那张卡的回执上「撤销」认它
    undone = env.client.post(f"/api/cybers/{k['id']}/undo", json={"version": v["id"]}, headers=H).json()
    assert undone["cyber"]["rules"] == "Recommends three options."


def test_the_profile_row_shows_the_result_first_through_the_same_changes(env, fake):
    k = make(env, "Kai", tone="Casual.", rules="Recommends three options.")
    r = env.client.post(f"/api/cybers/{k['id']}/changes", json={"field": "tone", "value": "Casual and brief."}, headers=H).json()
    c = r["changes"][0]
    assert (c["old"], c["new"], c["via"], c["inbox"]) == ("Casual.", "Casual and brief.", "profile", None) and "card" not in c  # 你自己点的：不出卡
    assert env.client.get(f"/api/cybers/{k['id']}", headers=H).json()["cyber"]["tone"] == "Casual."
    env.client.post(f"/api/cybers/{k['id']}/changes/{c['id']}", json={"action": "apply"}, headers=H)
    f = fake([{"text": json.dumps({"value": "Recommends three options and says plainly which is weakest."})}])
    r = env.client.post(f"/api/cybers/{k['id']}/changes", json={"field": "rules", "tweak": "stricter"}, headers=H).json()
    c = r["changes"][0]
    assert c["new"] == "Recommends three options and says plainly which is weakest." and c["why"] == "Stricter"
    ref, req = f.requests[0]
    assert req.tools == [] and "Make it stricter" in json.dumps(req.messages) + req.system  # 零工具的改写
    env.client.post(f"/api/cybers/{k['id']}/changes/{c['id']}", json={"action": "drop"}, headers=H)
    fake([{"text": json.dumps({"changes": [{"field": "rules", "value": "Recommends five options."},
                                           {"field": "note", "value": "Works on an offline sync library."}, {"field": "model", "value": "x"}]})}])
    r = env.client.post(f"/api/cybers/{k['id']}/changes", json={"ask": "Always give me five; I work on an offline sync library."}, headers=H).json()
    assert [(c["field"], c["via"]) for c in r["changes"]] == [("rules", "profile"), ("note", "profile")]  # 模型挑的，只认能改的那几项
    for c in r["changes"]:
        env.client.post(f"/api/cybers/{k['id']}/changes/{c['id']}", json={"action": "apply"}, headers=H)
    got = env.client.get(f"/api/cybers/{k['id']}", headers=H).json()["cyber"]
    assert got["rules"] == "Recommends five options." and got["notes"] == 1 and got["tone"] == "Casual and brief."
    vers = env.client.get(f"/api/cybers/{k['id']}/versions", headers=H).json()["versions"]
    assert [(v["field"], v["via"]) for v in vers] == [("note", "profile"), ("rules", "profile"), ("tone", "profile")]
    for bad in ({"field": "name", "value": "Kay"}, {"field": "rules", "tweak": "louder"}, {}, {"field": "note", "tweak": "shorter"}):
        assert env.client.post(f"/api/cybers/{k['id']}/changes", json=bad, headers=H).status_code == 400
    fake([{"text": "Sure."}, {"text": "Sure again."}])
    assert env.client.post(f"/api/cybers/{k['id']}/changes", json={"field": "tone", "ask": "warmer"}, headers=H).status_code == 502


def test_all_three_ways_meet_in_one_list_of_changes(env, fake):
    k = make(env, "Kai", tone="Casual.")
    run(cybers.propose(k["id"], "note", "Ships on Fridays.", via="review", why="It came up twice."))  # 它自己提的（回看）
    env.client.post(f"/api/cybers/{k['id']}/changes", json={"field": "tone", "value": "Casual and brief."}, headers=H)  # 资料页点一行
    fake([{"calls": [("c1", "propose_change", {"field": "tag", "value": "Code advisor"})]}, {"text": "Proposed."}])
    talk(k["id"], "Call yourself my code advisor.")  # 对话里说
    pend = env.client.get(f"/api/cybers/{k['id']}/changes", headers=H).json()["pending"]
    assert sorted((c["field"], c["via"], bool(c["inbox"])) for c in pend) == [("note", "review", True), ("tag", "chat", True), ("tone", "profile", False)]


def test_health_is_never_kept_and_notes_can_be_deleted_or_cleared(env):
    import fastapi
    k = make(env, "Sam")
    with pytest.raises(fastapi.HTTPException) as e:
        run(cybers.propose(k["id"], "note", "Sleeps badly before deadlines.", via="profile"))
    assert e.value.status_code == 400  # 健康、饮食、睡眠不给赛博，也不记
    for text in ("Writes a thesis on offline sync.", "Likes examples first."):
        c = run(cybers.propose(k["id"], "note", text, via="profile"))
        env.client.post(f"/api/cybers/{k['id']}/changes/{c['id']}", json={"action": "apply"}, headers=H)
    notes = env.client.get(f"/api/cybers/{k['id']}/notes", headers=H).json()["notes"]
    assert env.client.delete(f"/api/cybers/{k['id']}/notes/{notes[0]['id']}", headers=H).json() == {"ok": True}
    assert env.client.delete(f"/api/cybers/{k['id']}/notes", headers=H).json()["deleted"] == 1
    assert env.client.get(f"/api/cybers/{k['id']}/memory", headers=H).json()["rule"] == "Health, diet and sleep are never given to a Cyber"


def test_knowledge_comes_with_its_source_and_relevant_bits_follow_the_message(env, fake):
    k = make(env, "Kai")
    for i in range(3):
        env.client.post(f"/api/cybers/{k['id']}/knowledge", json={"title": f"Report {i}", "text": f"Filler number {i}.", "source": "GitHub radar"},
                        headers=H)
    old = env.client.post(f"/api/cybers/{k['id']}/knowledge", json={"title": "Old notes", "text": "CRDT merge strategies for offline sync."},
                          headers=H).json()["item"]
    assert old["source"] == "From you" and old["origin"] == "given"
    for i in range(3):  # 再来三份新的：那份旧的不在开头的全文里了
        env.client.post(f"/api/cybers/{k['id']}/knowledge", json={"title": f"Daily {i}", "text": f"Nothing much {i}.", "source": "GitHub radar"},
                        headers=H)
    f = fake([{"text": "On it."}])
    talk(k["id"], "which merge strategies suit offline sync?")
    req = f.requests[0][1]
    assert "## Your knowledge base" in req.system and "Old notes (From you" in req.system and "<prompt-data>" in req.system
    assert "CRDT merge strategies" not in req.system  # 旧的那份只有标题在开头
    extra = [m for m in req.messages if m["role"] == "system"]
    assert extra and "CRDT merge strategies for offline sync." in str(extra[-1]["content"])  # 这一句相关的跟在后面，当资料
    assert env.client.post(f"/api/cybers/{k['id']}/knowledge", json={"text": " "}, headers=H).status_code == 400


def test_deleting_a_cyber_archives_its_memory_and_withdraws_open_cards(env):
    import inbox
    k = make(env, "Kai")
    c = run(cybers.propose(k["id"], "note", "Ships on Fridays.", via="review"))
    env.client.post(f"/api/cybers/{k['id']}/knowledge", json={"title": "x", "text": "y"}, headers=H)
    env.client.delete(f"/api/cybers/{k['id']}", headers=H)
    assert inbox.item(c["inbox"])["status"] == "withdrawn"
    with chat._lock, cybers.cydb() as conn:
        assert conn.execute("SELECT archived_at FROM cyber_knowledge WHERE cyber=?", (k["id"],)).fetchone()["archived_at"]


def test_review_is_off_by_default_and_proposes_when_on(env, fake):
    import json
    k = make(env, "Kai", tone="Chatty.")
    fake([{"text": "a"}, {"text": "b"}])
    talk(k["id"], "hi")
    talk(k["id"], "from now on keep it short")
    assert cybers._bg == set() and env.client.get(f"/api/cybers/{k['id']}/changes", headers=H).json()["pending"] == []  # 默认关：不花钱
    env.cfg(cybers={"review": {"every": 2}})
    out = {"notes": [{"text": "Wants short replies.", "why": "They said: keep it short"}, {"text": "Sleeps late.", "why": "x"}],
           "change": {"field": "tone", "new": "Brief and direct.", "why": "They said: keep it short"}}
    f = fake([{"text": "c"}, {"text": "d"}, {"text": json.dumps(out)}])

    async def go():
        for t in ("ok", "short please"):
            r = chat.start_run(k["id"], t, None)
            await until(lambda r=r: r.done)
            assert t != "ok" or cybers._bg == set()  # 关着时说的两轮不算：开了以后第一轮还不够 2 轮（不然回看会和下一轮抢着说）
        await until(lambda: not cybers._bg)
    run(go())
    review = f.requests[2]
    assert review[0] == "deepseek/deepseek-flash" and review[1].tools == [] and "looking back over your recent talk" in review[1].system
    pend = {c["field"]: c for c in env.client.get(f"/api/cybers/{k['id']}/changes", headers=H).json()["pending"]}
    assert set(pend) == {"note", "tone"} and pend["note"]["new"] == "Wants short replies." and pend["tone"]["new"] == "Brief and direct."
    assert all(c["inbox"] for c in pend.values())  # 都是先给样子的卡，点了才生效（「Sleeps late.」健康的不提）


# —— 装载：ask_agent ——————————————————————————————————————————————————————————————————————————

def agent(env, name: str, **extra) -> str:
    """建一个功能 Agent（POST /api/groups，builtin 下只写一行 + 工作区）。"""
    r = env.client.post("/api/groups", json={"name": name, "model": "deepseek/deepseek-flash", **extra}, headers=H)
    assert r.status_code == 200, r.text
    return r.json()["id"]


@pytest.fixture
def runner(monkeypatch):
    """假的命令行工具：记下谁跑了什么，回一句。"""
    import bridges
    calls: list = []

    async def fake_run(name, args, stdin, agent, *, kill_on_cancel=False):
        calls.append((name, list(args), agent))
        return f"ok: {name} {' '.join(args[:2])}"
    monkeypatch.setattr(bridges, "run", fake_run)
    return calls


@pytest.fixture
def groups_clean(monkeypatch, tmp_path):
    """功能 Agent 的表清空，工作区放 tmp（agents.provision_builtin 写的东西不留在真的家目录）。"""
    import data
    with chat._lock, data.ddb() as conn:
        conn.execute("DELETE FROM groups")
    monkeypatch.setattr(config.settings, "openclaw_home", tmp_path / "oc")
    monkeypatch.setattr(config.settings, "openclaw_json", tmp_path / "oc" / "openclaw.json")


def test_health_agents_can_not_be_loaded(env, groups_clean):
    k = make(env, "Kai")
    notes = agent(env, "Notes library", icon="book", purpose="Keeps reading notes")
    diet = agent(env, "Meals", icon="utensils")
    sleep = agent(env, "Bedtime", icon="moon", purpose="Interprets last night's sleep")
    lo = env.client.get(f"/api/cybers/{k['id']}/loadout", headers=H).json()
    flags = {a["id"]: a for a in lo["available"]}
    assert not flags[notes]["health"] and flags[diet]["health"] and flags[sleep]["health"]
    assert flags[diet]["why"] == "Health data isn't given to a Cyber"
    assert env.client.post(f"/api/cybers/{k['id']}/loadout", json={"agent": sleep}, headers=H).status_code == 400
    r = env.client.post(f"/api/cybers/{k['id']}/loadout", json={"agent": notes}, headers=H).json()
    assert [a["id"] for a in r["loaded"]] == [notes]
    env.cfg(cybers={"health_agents": [notes]})  # 自己补的名单：只能加
    lo = env.client.get(f"/api/cybers/{k['id']}/loadout", headers=H).json()
    assert lo["loaded"][0]["health"]
    with chat._lock, cybers.cydb() as conn:
        assert cybers.loadout_of(conn, cybers.get_row(conn, k["id"])) == []  # 后来变成健康类的：ask_agent 也不给它
    env.cfg()
    assert env.client.delete(f"/api/cybers/{k['id']}/loadout/{notes}", headers=H).json()["loaded"] == []


def test_a_cyber_asks_its_loaded_agent_and_gets_read_only_results(env, fake, runner, groups_clean):
    k = make(env, "Kai", model="moonshot/kimi-k3")
    notes = agent(env, "Notes library", icon="book", purpose="Keeps reading notes")
    other = agent(env, "Job hunt", icon="briefcase")
    env.client.post(f"/api/cybers/{k['id']}/loadout", json={"agent": notes}, headers=H)
    f = fake([
        {"text": "Let me check.", "calls": [("c1", "ask_agent", {"agent": notes, "question": "notes on offline sync?"})]},
        {"text": "", "calls": [("n1", "board", {"args": ["show"]})]},  # 笔记库那一轮：只读的直接跑
        {"text": "Two notes: CRDTs and op logs."},
        {"text": "", "calls": [("c2", "ask_agent", {"agent": other, "question": "open roles?"})]},  # 名单外的：不问
        {"text": "You have two notes on it; I'd start with the CRDT one."},
    ])
    r = talk(k["id"], "what do my notes say about offline sync?")
    assert r.status == "ok" and r.text.endswith("I'd start with the CRDT one.")
    cy = f.requests[0][1]
    assert [t["name"] for t in cy.tools] == ["ask_agent", "propose_change"] and "ask_agent: ask a functional Agent loaded on you (Notes library)" in cy.system
    sub_ref, sub = f.requests[1]
    assert sub_ref == "deepseek/deepseek-flash" and "Cyber \"Kai\"" in sub.system and "Moonshot" in sub.system  # 告诉它问的是用别家模型的赛博
    assert not {t["name"] for t in sub.tools} & {"handoff", "sessions_list", "sessions_history", "ask_agent"}  # 只有一层，看不到主人别的会话
    assert runner == [("board", ["show"], notes)]
    back = str(f.requests[3][1].messages[-1]["content"])
    assert "[Inter-session message] sourceAgent=" in back and "Two notes: CRDTs and op logs." in back and "<prompt-data>" in back
    refused = str(f.requests[4][1].messages[-1]["content"])
    assert "isn't loaded on you" in refused
    turns = events.turns(f"cyber:{k['id']}")
    assert turns[0]["kind"] == "cyber"
    with chat._lock, chat.db() as conn:  # 功能 Agent 那边也留了一笔：灰字 + 回答，不算未读
        rows = conn.execute("SELECT role, text, origin FROM messages WHERE thread=? ORDER BY id", (notes,)).fetchall()
    assert [(x["role"], x["origin"]) for x in rows] == [("auto", "relay"), (ASSISTANT_ROLE, "relay")]
    assert 'The Cyber "Kai" asked (read-only): notes on offline sync?' in rows[0]["text"] and rows[1]["text"] == "Two notes: CRDTs and op logs."
    assert env.client.get("/api/unread", headers=H).json()["threads"][notes]["mine"] == 0  # 同主对话转来的：不算进角标
    row = {r["id"]: r for r in env.client.get("/api/chats", headers=H).json()["agents"]}[notes]
    assert row["unread"] == 0 and row["at"] is None  # 不算未读、不改排序


def test_changes_from_a_cyber_become_cards_for_the_owner(env, fake, runner, groups_clean):
    import inbox
    k = make(env, "Kai")
    notes = agent(env, "Notes library", icon="book")
    env.client.post(f"/api/cybers/{k['id']}/loadout", json={"agent": notes}, headers=H)
    f = fake([
        {"text": "", "calls": [("c1", "ask_agent", {"agent": notes, "question": "add the offline sync library to the to-read list"})]},
        {"text": "", "calls": [("n1", "board", {"args": ["rows", "add", "reading", "--data", "-"], "input": '{"title": "offline sync lib"}'})]},
        {"text": "Asked; it's waiting for your OK."},
        {"text": "It's on the card under this message."},
        {"text": "Added."},  # 点了同意以后发回赛博对话的那一轮
    ])
    talk(k["id"], "save that library for later")
    assert runner == []  # 撤得回的写也不直接做：要改你的数据，先出卡
    card = [it for it in env.client.get(f"/api/inbox?thread={k['id']}", headers=H).json()["items"] if it["kind"] == "tool"][0]
    assert card["source"] == notes and card["sourceName"] == "Notes library" and 'The Cyber "Kai" asked Notes library' in card["why"]
    assert "approval-pending" in str(f.requests[2][1].messages[-1]["content"])  # 那一轮拿到的：交给主人点头了
    back = str(f.requests[3][1].messages[-1]["content"])  # 赛博拿到的：它出了卡、还没改
    assert "put 1 change(s) in front of" in back and "Nothing has changed yet" in back
    env.client.post(f"/api/inbox/{card['id']}", json={"action": "approve"}, headers=H)
    wait_idle(k["id"])
    assert runner == [("board", ["rows", "add", "reading", "--data", "-"], notes)]  # 同意后照原样跑一次
    assert inbox.item(card["id"])["status"] == "done"


def test_the_loaded_agent_never_spends_the_owners_plan(env, fake, runner, groups_clean):
    notes = agent(env, "Notes library", icon="book")
    with chat._lock, chat.db() as conn:
        conn.execute("UPDATE threads SET model='claude-code/opus' WHERE id=?", (notes,))  # 它自己的对话选的是主人的 Claude Code
    k = make(env, "Kai")
    env.client.post(f"/api/cybers/{k['id']}/loadout", json={"agent": notes}, headers=H)
    f = fake([{"text": "", "calls": [("c1", "ask_agent", {"agent": notes, "question": "anything new?"})]}, {"text": "Nothing new."}, {"text": "Quiet day."}])
    talk(k["id"], "anything new?")
    assert f.requests[1][0] == "deepseek/deepseek-flash"  # 来路是赛博：换成不花套餐的


def test_daily_read_is_off_by_default_and_reads_todays_output_when_on(env, fake, groups_clean):
    from datetime import datetime
    radar = agent(env, "GitHub radar", icon="code", purpose="Writes a daily report on new repos")
    k = make(env, "Kai")
    env.client.post(f"/api/cybers/{k['id']}/loadout", json={"agent": radar}, headers=H)
    noon = datetime.now(config.TZ).replace(hour=12, minute=0)  # 日报的时间也放在这一天的上午：凌晨 4 点前跑测试，「现在」还算前一天
    with chat._lock, chat.db() as conn:
        conn.execute("INSERT INTO messages(thread, role, text, model, ts, status, origin) VALUES(?,?,?,?,?,?,?)",
                     (radar, ASSISTANT_ROLE, "12 repos today; 3 touch offline sync.", None, noon.replace(hour=8).isoformat(), "ok", "auto"))
    assert run(cybers.daily_check(noon)) == []  # 默认关
    env.client.post(f"/api/cybers/{k['id']}/daily", json={"on": True}, headers=H)
    assert run(cybers.daily_check(noon.replace(hour=8))) == []  # 还没到钟点
    fake([{"text": "Three of today's repos touch your sync module."}])

    async def go():
        done = await cybers.daily_check(noon)
        await until(lambda: chat.RUNS.get(k["id"]) is not None and chat.RUNS[k["id"]].done)
        return done
    assert run(go()) == [k["id"]]
    know = env.client.get(f"/api/cybers/{k['id']}/knowledge", headers=H).json()["items"]
    assert know[0]["origin"] == "daily" and know[0]["title"].startswith("GitHub radar · ") and "3 touch offline sync" in know[0]["excerpt"]
    assert run(cybers.daily_check(noon)) == []  # 一天只读一次


def test_default_cybers_iris_and_zhe_are_made_once(env, monkeypatch):
    def fresh() -> None:
        with chat._lock, cybers.cydb() as conn:
            conn.execute("DELETE FROM cybers")
            conn.execute("DELETE FROM cyber_meta WHERE key='defaults'")
    fresh()
    got = env.client.get("/api/cybers", headers=H).json()["cybers"]  # 第一次来列赛博时建好
    assert {c["name"]: (c["builtin"], c["kind"], c["tag"]) for c in got} == {"Iris": ("iris", "pro", "Host"), "Zhe": ("zhe", "friend", "Layman")}
    iris = next(c for c in got if c["builtin"] == "iris")
    assert "one question at a time" in iris["rules"] and iris["avatar"]["acc"]["phones"] and iris["model"] is None
    env.client.delete(f"/api/cybers/{iris['id']}", headers=H)
    assert [c["name"] for c in env.client.get("/api/cybers", headers=H).json()["cybers"]] == ["Zhe"]  # 删了不再建
    assert cybers.by_builtin("iris") is None and cybers.by_builtin("zhe")["name"] == "Zhe"
    fresh()
    env.cfg(cybers=False)
    assert cybers.seed_defaults() == []  # 没开赛博：一切照旧
    env.cfg()
    make(env, "Iris")  # 你已经有一位同名的：不建那一位
    assert len(cybers.seed_defaults()) == 1 and {c["name"] for c in env.client.get("/api/cybers", headers=H).json()["cybers"]} == {"Iris", "Zhe"}
    fresh()
    monkeypatch.setattr(config.settings, "language", "zh")  # 存的是服务器的语言
    env.client.get("/api/contacts", headers=H)  # 通讯录也会先建好
    with chat._lock, cybers.cydb() as conn:
        assert {r["name"]: r["tag"] for r in cybers.active_rows(conn)} == {"Iris": "主持", "阿哲": "外行"}
