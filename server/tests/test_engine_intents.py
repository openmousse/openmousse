"""第 5 批第 7 项：要记得的事（engine/intents.py）。remember_when 工具（只给主人回合、默认关、撤得回）；主人说的每一句不调模型地对一遍
触发词（整词；中日韩按字串），对上了在这一轮开头那条 system 后面带一行；最多几次、冷却、过期；记号进事件流，建、撤过记性的闸（经历）。
模型是假的，人和事都是编的。"""
from __future__ import annotations

import asyncio
import time
import uuid
from types import SimpleNamespace

import pytest

import config
import sources
from engine import intents, manuals, memgate, owner, store, used
from engine import tools as tools_mod
from engine.loop import Control, ToolCtx, ToolFailure
from engine.session import text_of
from test_engine import FakeModel

MODEL = "deepseek/deepseek-flash"
DAY = "2026-10-19"


def run(coro):
    return asyncio.run(coro)


def ctx(kind: str = "owner", agent: str = "main", turn: str = "t-int") -> ToolCtx:
    return ToolCtx(turn=turn, kind=kind, agent=agent, conversation=agent, call_id="c1", control=Control())


@pytest.fixture
def home(tmp_path, monkeypatch, server_json):
    ws = tmp_path / "ws-main"
    (ws / "memory").mkdir(parents=True)
    (ws / "skills").mkdir()
    monkeypatch.setattr(config.settings, "workspace", ws)
    monkeypatch.setattr(config.settings, "profile", tmp_path / "no-profile.md")
    monkeypatch.setattr(sources, "memory_tree", None)
    base = {"claw": {"kind": "builtin"}, "engine": {"model": MODEL, "manuals": False}}
    server_json({**base, "engine": {**base["engine"], "intents": {"enabled": True}}})
    intents.ready()
    used.ready()
    with store.tx() as conn:  # 库是几个测试共用的：清掉别的测试记的
        conn.execute("DELETE FROM engine_intents")
        conn.execute("DELETE FROM engine_used")
        conn.execute("DELETE FROM engine_memory")
    return SimpleNamespace(base=base, cfg=server_json, thread="t-" + uuid.uuid4().hex[:8])


def tool():
    return next(t for t in tools_mod.for_agent("main") if t.name == intents.TOOL)


def make(**args) -> dict:
    out = run(tool().run({"action": "create", **args}, ctx()))
    iid = out.split("Kept as ", 1)[1].split(".", 1)[0] if "Kept as " in out else out.split("记下了：", 1)[1].split("。", 1)[0]
    return intents.get(iid)


def test_off_by_default(home):
    home.cfg(home.base)
    assert intents.TOOL not in {t.name for t in tools_mod.for_agent("main")}
    assert intents.match("main", "my passport") == []


def test_create_is_undoable_owner_only_and_goes_through_the_memory_gate(home):
    t = tool()
    assert t.kinds == ("owner",)
    assert t.level({"action": "create"})[0] == tools_mod.UNDO and t.level({"action": "list"})[0] == tools_mod.READ
    it = make(triggers=["passport", "护照"], reminder="Renew the passport before the Lisbon trip.")
    assert it["status"] == "armed" and it["triggers"] == ["passport", "护照"] and it["max_fires"] == 3
    assert abs(it["expires"] - it["created"] - 90 * 86400) < 5 and it["cooldown"] == 24 * 3600
    with store.read() as conn:
        rows = [dict(r) for r in conn.execute("SELECT kind, op, action, who, target FROM engine_memory")]
    assert {"kind": "experience", "op": "add", "action": "write", "who": "owner", "target": f"intent:{it['id']}"} in rows
    listing = run(t.run({"action": "list"}, ctx()))
    assert it["id"] in listing and '"passport"' in listing
    assert it["id"] in run(t.run({"action": "cancel", "id": it["id"]}, ctx()))
    assert intents.get(it["id"])["status"] == "cancelled" and run(t.run({"action": "list"}, ctx())) in ("(nothing kept yet)", "（没有记着的）")
    with pytest.raises(ToolFailure):
        run(t.run({"action": "cancel", "id": it["id"]}, ctx()))  # 已经撤了
    with pytest.raises(ToolFailure):
        run(t.run({"action": "create", "triggers": ["x"], "reminder": "too short a trigger"}, ctx()))
    with pytest.raises(ToolFailure):
        run(t.run({"action": "create", "triggers": ["passport"], "reminder": "x", "expires": "2001-01-01"}, ctx()))
    with pytest.raises(ToolFailure):  # 别的回合过不了闸（工具本来也只标 owner）
        run(t.run({"action": "create", "triggers": ["passport"], "reminder": "x"}, ctx(kind="cyber")))


def test_matching_is_by_whole_words_and_by_substring_for_cjk(home):
    a = make(triggers=["passport"], reminder="A")
    b = make(triggers=["dentist appointment"], reminder="B")
    c = make(triggers=["日本"], reminder="C")
    assert intents.match("main", "nothing relevant here") == []
    assert intents.match("main", "I lost my passports") == []  # 整词才算
    assert [x["id"] for x in intents.match("main", "Where is my PASSPORT?")] == [a["id"]]
    assert intents.match("main", "the dentist called") == []  # 短语里的词都在才算
    assert [x["id"] for x in intents.match("main", "Booked an appointment with the dentist")] == [b["id"]]
    assert [x["id"] for x in intents.match("main", "下周去日本出差")] == [c["id"]]
    assert intents.match("g-other", "Where is my passport?") == []  # 只对这个 Agent 的


def test_fires_cooldown_max_and_expiry(home):
    now = time.time()
    it = make(triggers=["passport"], reminder="Renew it.", max_fires=2, cooldown_hours=1)
    assert len(intents.match("main", "passport", now=now)) == 1
    assert intents.match("main", "passport", now=now + 60) == []  # 冷却中
    assert len(intents.match("main", "passport", now=now + 3700)) == 1
    got = intents.get(it["id"])
    assert got["fires"] == 2 and got["status"] == "done"  # 次数用完
    assert intents.match("main", "passport", now=now + 9000) == []
    soon = make(triggers=["visa"], reminder="Check the visa.", expires=time.strftime("%Y-%m-%d", time.localtime(now + 2 * 86400)))
    assert intents.match("main", "visa", now=now + 5 * 86400) == []
    assert intents.get(soon["id"])["status"] == "expired"


def test_at_most_three_and_within_the_character_budget(home):
    ids = [make(triggers=["budget"], reminder=f"Reminder {i}: " + "x" * 300)["id"] for i in range(4)]
    fired = intents.match("main", "about the budget")
    assert 1 <= len(fired) <= intents.SHOW and len(intents.render(fired)) <= intents.SHOW_CHARS
    assert [x["id"] for x in fired] == ids[:len(fired)]  # 先建的先


def test_the_reminder_rides_in_the_turn_after_the_users_message(home, monkeypatch):
    from engine import providers
    f = FakeModel([{"text": "Noted."}, {"text": "Sure."}])
    monkeypatch.setattr(providers, "stream", f.stream)
    it = make(triggers=["passport"], reminder="Renew the passport before the Lisbon trip.")
    run(owner.turn(home.thread, DAY, "What should I pack? I have my passport ready.", None, lambda _x: None))
    msgs = f.requests[0][1].messages
    note = msgs[-1]
    assert note["role"] == "system" and note.get("intents") == [it["id"]]
    assert "Renew the passport before the Lisbon trip." in text_of(note) and "Reminder the user asked you to keep" in text_of(note)
    from engine import events
    turn = events.turns(home.thread)[0]["id"]
    assert ("intent", it["id"], "prep") in [(r["kind"], r["name"], r["via"]) for r in used.of_turn(turn)]
    assert manuals.describe("intent", it["id"])["about"] == "Renew the passport before the Lisbon trip."
    run(owner.turn(home.thread, DAY, "And my passport photo?", None, lambda _x: None))  # 冷却中：这一句不再带
    again = f.requests[1][1].messages
    assert again[-1]["role"] == "user" and sum(1 for m in again if m.get("intents")) == 1  # 上一轮那条还在会话里，这一句没再多一条


def test_system_messages_are_not_matched(home):
    make(triggers=["passport"], reminder="Renew it.")
    assert manuals.skip("auto") and manuals.skip(None, "task")  # 收件箱知会、定时脚本、后台任务交回来的：按句搜（连同这里）整个跳过
    note = run(intents.attach(None, "", turn="t-x", agent="main", conversation="main"))
    assert note is None


def test_api_lists_and_cancels(home):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(intents.router)
    it = make(triggers=["passport"], reminder="Renew it.")
    c = TestClient(app)
    got = c.get("/api/engine/intents?agent=main").json()
    assert got["enabled"] is True and [x["id"] for x in got["intents"]] == [it["id"]]
    assert c.delete(f"/api/engine/intents/{it['id']}").json()["intent"]["status"] == "cancelled"
    assert c.delete(f"/api/engine/intents/{it['id']}").status_code == 404
    assert c.get("/api/engine/intents?agent=main&all=1").json()["intents"][0]["status"] == "cancelled"
    assert memgate.decide(intents.KIND, "add").action == "write" and memgate.decide(intents.KIND, "add", turn="outsider").action == "deny"
