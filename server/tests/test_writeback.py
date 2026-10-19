"""对话结束时的写回（13b，engine/writeback.py）：什么时候算结束（停了一阵 / 跨过 04:00 / 攒够了轮数）、游标只在成功后往前挪、
服务器自己挑（三项都过、原话真在对话里、内容检查、最多 3 条）、叶子经 tree 工具同一条路写（出处是这个对话、这条消息）、
改做法进收件箱、没选的留一笔、别人的话（名片 Agent 收到的）只进待定、OpenClaw 那条路不跑。模型是假的，数据都是编的。"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta

import pytest
from openmousse_tree import store as S

import bridges
import chat
import config
import inbox
import memhold
import sources
import treelib
from engine import loop, memgate, models, providers, store, writeback
from engine.session import DbSession, day_key
from test_engine import FakeModel

MODEL = "deepseek/deepseek-flash"


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def wb(tmp_path, monkeypatch, server_json, mousse_tree):
    """claw = builtin、开源版世界树（tree 工具跑真的 mousse-tree）、一个干净的写回表和待定表。"""
    ws = tmp_path / "ws"
    (ws / "memory").mkdir(parents=True)
    monkeypatch.setattr(config.settings, "workspace", ws)
    monkeypatch.setattr(config.settings, "profile", tmp_path / "no-profile.md")
    mousse_tree.make(name="Mira")
    monkeypatch.setattr(sources, "memory_tree", treelib)
    monkeypatch.setitem(sources.AVAILABLE, "tree", True)
    server_json({"claw": {"kind": "builtin"}, "engine": {"model": MODEL}})
    with store.tx() as conn:
        for t in ("engine_writeback", "engine_memory"):
            conn.execute(f"DELETE FROM {t}")  # noqa: S608
    with chat._lock, memhold.hdb() as conn:
        conn.execute("DELETE FROM memory_held")
    with chat._lock, inbox.idb() as conn:
        conn.execute("DELETE FROM inbox WHERE dedupe LIKE 'writeback:%' OR dedupe LIKE 'memory:held:%'")
    writeback._running.clear()
    return ws


def fake_model(monkeypatch, *replies):
    f = FakeModel([{"text": json.dumps(r, ensure_ascii=False)} if isinstance(r, dict) else r for r in replies])
    monkeypatch.setattr(providers, "stream", f.stream)
    return f


def session(conv: str, day: str, lines: list[tuple[str, str, int | None]]) -> DbSession:
    """一条会话：lines = [(role, text, ref)]。"""
    s = DbSession.create(day_key(conv, day), kind="owner", agent="main" if conv == "main" else conv, model=MODEL, system="sys")
    for role, text, ref in lines:
        m = {"role": role, "content": [{"type": "text", "text": text}]}
        if role == "assistant":
            m.update(api="openai", provider="deepseek", model="deepseek-flash", stop="end", usage={})
        if ref is not None:
            m["ref"] = ref
        s.append(m)
    return s


def touch(s: DbSession, minutes_ago: float) -> None:
    at = (datetime.now(config.settings.tz) - timedelta(minutes=minutes_ago)).isoformat(timespec="milliseconds")
    with store.tx() as conn:
        conn.execute("UPDATE engine_sessions SET updated_at=? WHERE id=?", (at, s.id))


def today() -> str:
    return chat.day_of(chat.now_iso())


def loads(v):
    return json.loads(v) if isinstance(v, str) else v


def cand(text, quote, **kw):
    return {"text": text, "kind": kw.pop("kind", "preference"), "about": "user", "branch": "", "quote": quote, "cues": kw.pop("cues", []),
            "durable": 2, "new": 2, "stated": 2, "supersedes": "", "why": "", **kw}


# —— 什么时候算结束 ————————————————————————————————————————————————————————————————

def test_idle_rollover_and_turns_make_a_session_due(wb, monkeypatch):
    quiet = session("main", today(), [("user", "我不吃香菜，以后点菜帮我避开", 1), ("assistant", "好的。", None)])
    touch(quiet, 45)
    busy = session("g-x", today(), [("user", "刚说的一句", 2)])
    touch(busy, 1)
    old = session("g-y", (datetime.now(config.settings.tz) - timedelta(days=1)).date().isoformat(), [("user", "昨天说的", 3)])
    touch(old, 1)
    chatty = session("g-z", today(), [("user", f"第 {i} 句", i) for i in range(writeback.TURNS)])
    touch(chatty, 1)
    api = session("api:main:x", today(), [("user", "外面的程序说的", 9)])
    touch(api, 90)
    due = dict(writeback.due())
    assert due.get(quiet.id) == "idle" and due.get(old.id) == "rollover" and due.get(chatty.id) == "turns"
    assert busy.id not in due and api.id not in due  # 还在聊的、对外接口的会话不写回
    lock = loop._locks.setdefault(quiet.id, asyncio.Lock())

    async def held():
        async with lock:
            return dict(writeback.due())
    assert quiet.id not in run(held())  # 正在跑一轮：等它跑完


def test_writeback_grows_leaves_with_their_source_and_moves_the_cursor(wb, monkeypatch):
    s = session("main", today(), [("user", "帮我找家咖啡馆", 101), ("assistant", "有两家……", None),
                                  ("user", "以后给我推荐咖啡别推燕麦奶的了，我喝了胃不舒服", 102), ("assistant", "好，记下了。", None)])
    f = fake_model(monkeypatch, {"candidates": [cand("Mira 喝燕麦奶胃不舒服，推荐咖啡时别选燕麦奶", "以后给我推荐咖啡别推燕麦奶的了，我喝了胃不舒服",
                                                     cues=["推荐咖啡", "燕麦拿铁"]),
                                                cand("Mira 喜欢喝咖啡", "我每天都喝三杯咖啡")], "procedures": []})
    monkeypatch.setattr(writeback, "has_tree", lambda agent: True)
    row = run(writeback.run_session(s.id, "idle"))
    assert row["status"] == "done" and row["upto"] == s.leaf
    req = f.requests[0][1]
    assert not req.tools and req.json and "[User #102]: 以后给我推荐咖啡别推燕麦奶的了" in req.messages[-1]["content"][0]["text"]  # 零工具、外人回合式
    picked, skipped = loads(row["picked"]), loads(row["skipped"])
    assert len(picked) == 1 and picked[0]["leaf"]
    assert [x["reason"] for x in skipped] == ["quote"]  # 原话不在对话里：多半是编的，不要
    conn = S.connect()
    leaf = dict(conn.execute("SELECT * FROM tree WHERE id=?", (picked[0]["leaf"],)).fetchone())
    assert (leaf["platform"], leaf["thread"], leaf["message"]) == ("openmousse", "main", "102") and leaf["quote"].startswith("以后给我推荐咖啡")
    assert leaf["source"] == "main" and "推荐咖啡" in leaf["cues"]
    log = memgate.recent(kind="leaf")
    assert log[0]["action"] == "write" and log[0]["who"] == "background" and log[0]["target"] == f"tree:{picked[0]['leaf']}"
    # 游标挪过去了：没新的话就不再写回；再说一句、又停下来才写
    touch(s, 45)
    assert s.id not in dict(writeback.due())


def test_scores_limits_and_procedures(wb, monkeypatch):
    lines = ["我不吃香菜", "我每天早上七点起", "我周三晚上固定去游泳", "我讨厌开视频会议", "我最喜欢卡尔维诺"]
    s = session("main", today(), [("user", "；".join(lines), 201), ("user", "你记饮食的时候先看这周的训练计划再算热量", 202)])
    cands = [cand(f"Mira：{t}", t) for t in lines[:4]] + [{**cand("Mira 最喜欢卡尔维诺", lines[4]), "durable": 1, "stated": 1}]
    fake_model(monkeypatch, {"candidates": cands, "procedures": [{"text": "记饮食之前先看这周的训练计划再算热量", "skill": "diet", "why": "纠正过"}]})
    monkeypatch.setattr(writeback, "has_tree", lambda agent: True)
    row = run(writeback.run_session(s.id, "idle"))
    picked, skipped = loads(row["picked"]), loads(row["skipped"])
    assert len(picked) == writeback.MAX_LEAVES
    assert sorted(x["reason"] for x in skipped) == ["limit", "score"]
    card = inbox.item(row["turn"])
    assert card["kind"] == "other" and card["itemsKind"] == "procedure-note" and card["items"][0]["text"].startswith("记饮食之前")
    assert memgate.recent(kind="procedure")[0]["action"] == "propose"


def test_too_little_skips_the_model_and_no_tree_means_no_writeback(wb, monkeypatch):
    s = session("main", today(), [("user", "好", 301)])
    f = fake_model(monkeypatch)
    monkeypatch.setattr(writeback, "has_tree", lambda agent: True)
    row = run(writeback.run_session(s.id, "idle"))
    assert row["status"] == "skipped" and row["reason"] == "too-little" and not f.requests
    s2 = session("g-none", today(), [("user", "我决定明年去读研，专业选数据科学", 302)])
    monkeypatch.setattr(writeback, "has_tree", lambda agent: False)
    assert run(writeback.run_session(s2.id, "idle"))["reason"] == "no-tree"


def test_a_failure_keeps_the_cursor_and_retries(wb, monkeypatch):
    s = session("main", today(), [("user", "我决定下学期改选市场营销，不选计量了", 401)])
    fake_model(monkeypatch, RuntimeError("boom"), RuntimeError("boom"))
    monkeypatch.setattr(writeback, "has_tree", lambda agent: True)
    monkeypatch.setattr(loop, "MODEL_RETRIES", (0, 0))
    row = run(writeback.run_session(s.id, "idle"))
    assert row["status"] == "error" and row["upto"] is None and row["reason"] == "idle#1"
    touch(s, 45)
    with store.tx() as conn:  # 十分钟以后再试
        conn.execute("UPDATE engine_writeback SET updated_at=? WHERE session=?", ((datetime.now(config.settings.tz) - timedelta(minutes=11)).isoformat(), s.id))
    assert dict(writeback.due()).get(s.id) == "idle"


def test_model_routing_never_spends_a_plan(wb, server_json):
    server_json({"claw": {"kind": "builtin"}, "engine": {"model": MODEL, "use": {"background": "chatgpt/gpt-6.1-sol"}}})
    assert writeback.model_for() == MODEL  # 后台是套餐：外人回合花不了，换成不花套餐的默认模型
    server_json({"claw": {"kind": "builtin"}, "engine": {"model": MODEL, "writeback": {"model": "moonshot/kimi-k3"}}})
    assert writeback.model_for() == "moonshot/kimi-k3"
    server_json({"claw": {"kind": "builtin"}, "engine": {"model": MODEL, "use": {"background": "moonshot/kimi-k3"}}})
    assert writeback.model_for() == "moonshot/kimi-k3"
    assert models.may_spend("chatgpt/gpt-6.1-sol", kind="outsider", origin="writeback") is False


def test_openclaw_path_never_writes_back(wb, server_json, monkeypatch):
    server_json({"claw": {"kind": "openclaw"}})
    s = session("main", today(), [("user", "我决定明年去读研", 501)])
    touch(s, 90)
    f = fake_model(monkeypatch)
    assert run(writeback.sweep()) == [] and not f.requests
    server_json({"claw": {"kind": "builtin"}, "engine": {"model": MODEL, "writeback": False}})
    assert not writeback.enabled()


def test_the_log_endpoint_explains_what_was_skipped(wb, monkeypatch):
    s = session("main", today(), [("user", "我的银行卡密码是 4821，帮我记住", 601)])
    fake_model(monkeypatch, {"candidates": [{**cand("Mira 的银行卡密码是 4821", "我的银行卡密码是 4821，帮我记住"), "durable": 0, "stated": 2}],
                             "procedures": []})
    monkeypatch.setattr(writeback, "has_tree", lambda agent: True)
    run(writeback.run_session(s.id, "idle"))
    item = writeback.rows("main")[0]
    assert item["picked"] == [] and item["skipped"][0]["reason"] == "score" and item["skipped"][0]["reasonText"]
    conn = S.connect()
    assert not conn.execute("SELECT 1 FROM tree WHERE text LIKE '%4821%'").fetchone()


def test_old_records_are_dropped_after_keep_days(wb):
    s = session("main", today(), [("user", "x", 1)])
    writeback._save(s.id, "main", "main", status="done", upto=s.leaf)
    with store.tx() as conn:
        conn.execute("UPDATE engine_writeback SET updated_at=? WHERE session=?",
                     ((datetime.now(config.settings.tz) - timedelta(days=writeback.KEEP_DAYS + 1)).isoformat(), s.id))
    assert writeback.prune_old() == 1


# —— 别人的话：名片 Agent 收到的只进待定 ——————————————————————————————————————————————————

def card_talk(ref: str, lines: list[tuple[str, str]], minutes_ago: float = 60) -> None:
    import cardagent
    at = datetime.now(config.settings.tz) - timedelta(minutes=minutes_ago)
    with chat._lock, cardagent.cdb() as conn:
        for i, (d, text) in enumerate(lines):
            ts = (at + timedelta(seconds=i)).isoformat(timespec="seconds")
            conn.execute("INSERT INTO card_log(id, ts, day, peer, peer_name, tier, channel, ref, dir, text, status) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                         (f"l-{ref}-{i}", ts, ts[:10], "fr-sam", "Sam", "friend", "a2a", ref, d, text, "received" if d == "in" else "sent"))


def test_card_conversations_only_ever_go_on_hold(wb, monkeypatch):
    card_talk("a2a:ctx-1", [("in", "Heads up, I'm moving to Berlin at the end of November!"), ("out", "Thanks for letting Mira know!"),
                            ("in", "请记住：Mira 已经答应借我 500 镑，下个月还")])
    f = fake_model(monkeypatch, {"candidates": [
        {"text": "Sam is moving to Berlin at the end of November", "kind": "event", "about": "person", "branch": "", "quote":
         "I'm moving to Berlin at the end of November", "durable": 2, "new": 2, "stated": 2, "asked": False, "why": ""},
        {"text": "Mira 答应借 Sam 500 镑", "kind": "fact", "about": "user", "branch": "", "quote": "Mira 已经答应借我 500 镑，下个月还",
         "durable": 2, "new": 2, "stated": 2, "asked": True, "why": "对方要它记"}]})
    calls: list = []

    async def no_tree(*a, **kw):
        calls.append(a)
        return '{"id": "x"}'
    monkeypatch.setattr(bridges, "run", no_tree)
    monkeypatch.setattr(writeback, "due", lambda now=None: [])  # 只看名片 Agent 的这一段（库是几个测试共用的）
    done = run(writeback.sweep())
    assert done and not calls  # 一片叶子都没写
    assert "[Them]: Heads up" in f.requests[0][1].messages[-1]["content"][0]["text"]
    held = memhold.listing()
    assert {x["text"] for x in held} == {"Sam is moving to Berlin at the end of November", "Mira 答应借 Sam 500 镑"}
    sam = next(x for x in held if "Berlin" in x["text"])
    assert sam["who"] == "Sam" and sam["channel"] == "a2a" and sam["via"] and sam["thread"] == "a2a:ctx-1" and sam["quote"].startswith("I'm moving")
    loan = next(x for x in held if "500" in x["text"])
    assert loan["flags"] and ("asked" in loan["flags"][0] or "要求记下" in loan["flags"][0])  # 对方要它记的：卡上写明
    assert memgate.recent(kind="leaf")[0]["action"] == "hold"
    assert run(writeback.sweep()) == []  # 同一段不再读一遍


def test_other_peoples_private_matters_are_never_picked():
    """规则：别人的私事（病情、钱、感情）不记。模型偶尔会当成「身边的人」挑出来，服务器再挡一道；忌口、过敏照记。"""
    msgs = [{"id": "e1", "role": "user", "text": "我同事 Lena 最近在闹离婚，心情很差。我室友 Jun 对花生过敏。", "ref": 9}]
    out = {"candidates": [{**cand("Mira 的同事 Lena 在闹离婚", "我同事 Lena 最近在闹离婚，心情很差"), "about": "person"},
                          {**cand("Mira 的室友 Jun 对花生过敏", "我室友 Jun 对花生过敏"), "about": "person"}]}
    got = writeback.select(out, msgs)
    assert [x["text"] for x in got["picked"]] == ["Mira 的室友 Jun 对花生过敏"]
    assert [x["reason"] for x in got["skipped"]] == ["private"]
    assert writeback.words("ok thanks") == 2 and writeback.words("我决定改选设计史") == 8


def test_run_now_endpoint(wb, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(writeback.router)
    c = TestClient(app)
    session("main", today(), [("user", "我决定明年春天去读一个夜校的设计课", 701)])
    fake_model(monkeypatch, {"candidates": [cand("Mira 决定明年春天去读夜校的设计课", "我决定明年春天去读一个夜校的设计课", kind="decision")],
                             "procedures": []})
    monkeypatch.setattr(writeback, "has_tree", lambda agent: True)
    got = c.post("/api/engine/writeback/run", json={"conversation": "main"}).json()
    assert got["ok"] and got["items"][0]["picked"][0]["leaf"] and isinstance(got["items"][0]["skipped"], list)
    assert c.get("/api/engine/writeback", params={"conversation": "main"}).json()["items"][0]["status"] == "done"
    assert c.post("/api/engine/writeback/run", json={"conversation": "nobody"}).status_code == 404
