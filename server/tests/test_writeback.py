"""对话结束时的写回（13b，engine/writeback.py）：什么时候算结束（停了一阵 / 跨过 04:00 / 攒够了轮数）、游标只在成功后往前挪、
服务器自己挑（三项都过、原话真在对话里、内容检查、最多 3 条）、叶子经 tree 工具同一条路写（出处是这个对话、这条消息）、
改做法进收件箱、没选的留一笔、别人的话（名片 Agent 收到的）只进待定、OpenClaw 那条路不跑；
没写成的退避、连着失败到上限就停、有新的话再从头来，缺东西不调模型，一天的上限算上失败的，状态接口看得出来。模型是假的，数据都是编的。"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

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
        conn.execute("DELETE FROM engine_turns WHERE origin='writeback' OR conversation GLOB 'writeback:*'")  # 今天写回的次数从零数
    monkeypatch.setattr(writeback, "_needs", {})  # 写回要的包每个测试重新查
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


def failing_model(monkeypatch):
    """每次都出错的模型（一次写回调一次：loop 那一层不重试）。"""
    monkeypatch.setattr(loop, "MODEL_RETRIES", ())
    return fake_model(monkeypatch, *[RuntimeError("boom")] * 20)


def only(monkeypatch, sessions=(), refs=()) -> None:
    """sweep 只看这几段：库是几个测试共用的，前面留下的会话、名片 Agent 的对话别掺进来（判断到没到点还是 due / card_refs 自己的）。"""
    due, card_refs, ids = writeback.due, writeback.card_refs, {s.id for s in sessions}
    monkeypatch.setattr(writeback, "due", lambda now=None: [x for x in due(now) if x[0] in ids])
    monkeypatch.setattr(writeback, "card_refs", lambda idle, now: [x for x in card_refs(idle, now) if x[0] in refs])


def expire(key: str) -> None:
    """退避的时间到了：retry_at 改成刚过去。"""
    with store.tx() as conn:
        conn.execute("UPDATE engine_writeback SET retry_at=? WHERE session=?",
                     ((datetime.now(config.settings.tz) - timedelta(seconds=1)).isoformat(timespec="seconds"), key))


def say(s: DbSession, text: str, ref: int | None = None, minutes_ago: float = 45) -> None:
    """会话里又说了一句（加上回的那一句），最后一句在 minutes_ago 分钟以前（默认已经停了一阵）。"""
    s.append({"role": "user", "content": [{"type": "text", "text": text}], **({"ref": ref} if ref is not None else {})})
    s.append({"role": "assistant", "content": [{"type": "text", "text": "好。"}], "api": "openai", "provider": "deepseek",
              "model": "deepseek-flash", "stop": "end", "usage": {}})
    touch(s, minutes_ago)


# —— 什么时候算结束 ————————————————————————————————————————————————————————————————

def test_idle_rollover_and_turns_make_a_session_due(wb, monkeypatch):
    now = datetime.now(config.settings.tz)  # 钟只读这一次：会话算哪天、到没到点都照它，几点跑都一样（跑到 04:00 那一下也不会前后对不上）
    day = chat.day_of(now.isoformat(timespec="seconds"))
    yesterday = (date.fromisoformat(day) - timedelta(days=1)).isoformat()  # 逻辑日的前一天：04:00 以前，日历上的昨天还算今天
    quiet = session("main", day, [("user", "我不吃香菜，以后点菜帮我避开", 1), ("assistant", "好的。", None)])
    touch(quiet, 45)
    busy = session("g-x", day, [("user", "刚说的一句", 2)])
    touch(busy, 1)
    old = session("g-y", yesterday, [("user", "昨天说的", 3)])
    touch(old, 1)
    chatty = session("g-z", day, [("user", f"第 {i} 句", i) for i in range(writeback.TURNS)])
    touch(chatty, 1)
    api = session("api:main:x", day, [("user", "外面的程序说的", 9)])
    touch(api, 90)
    due = dict(writeback.due(now))
    assert due.get(quiet.id) == "idle" and due.get(old.id) == "rollover" and due.get(chatty.id) == "turns"
    assert busy.id not in due and api.id not in due  # 还在聊的、对外接口的会话不写回
    lock = loop._locks.setdefault(quiet.id, asyncio.Lock())

    async def held():
        async with lock:
            return dict(writeback.due(now))
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


def test_a_failure_keeps_the_cursor_backs_off_and_stops_at_retries(wb, monkeypatch):
    """没写成：游标不动，退避（一次比一次等得久），退避的时候这一轮不调模型；同一段连着失败 RETRIES 次就停（failed），跨过 04:00 也不再跑。"""
    s = session("main", today(), [("user", "我决定下学期改选市场营销，不选计量了", 401)])
    touch(s, 45)
    f = failing_model(monkeypatch)
    monkeypatch.setattr(writeback, "has_tree", lambda agent: True)
    only(monkeypatch, [s])
    waits = []
    for n in range(1, writeback.RETRIES + 1):
        run(writeback.sweep())
        row = writeback._wb(s.id)
        assert len(f.requests) == n and row["tries"] == n and row["upto"] is None and row["tried"] == s.leaf and row["reason"] == "idle"
        if n < writeback.RETRIES:
            assert row["status"] == "error" and "boom" in row["error"]
            waits.append(datetime.fromisoformat(row["retry_at"]) - datetime.fromisoformat(row["updated_at"]))
        assert run(writeback.sweep()) == [] and len(f.requests) == n  # 还在退避 / 停了：这一轮不调
        expire(s.id)
    assert [round(w.total_seconds() / 60) for w in waits] == [writeback.BACKOFF_MIN, writeback.BACKOFF_MIN * 2]
    assert row["status"] == "failed" and row["retry_at"] is None
    tomorrow = datetime.now(config.settings.tz) + timedelta(days=1)
    assert s.id not in dict(writeback.due()) and s.id not in dict(writeback.due(tomorrow))  # 停了就是停了：跨过 04:00 也不重跑
    run(writeback.sweep())
    assert len(f.requests) == writeback.RETRIES


def test_model_routing_spends_a_plan_on_the_users_own_background(wb, server_json, monkeypatch):
    """写回是用户自己的后台（来路 auto）。ChatGPT 没同意过后台：仍用按量的。同意过、或主模型是 Claude Code：用那份套餐。
    名片、Doorman、管家这些外人来路照旧不能花。来路名字 writeback 本身不在能花的名单里。"""
    from engine import chatgpt_plan
    server_json({"claw": {"kind": "builtin"}, "engine": {"model": MODEL, "use": {"background": "chatgpt/gpt-6.1-sol"}}})
    assert writeback.model_for() == MODEL  # 后台是 ChatGPT、还没同意：这一轮不花，用按量的默认模型
    monkeypatch.setattr(chatgpt_plan, "background_allowed", lambda: True)
    assert writeback.model_for() == "chatgpt/gpt-6.1-sol"
    monkeypatch.setattr(chatgpt_plan, "background_allowed", lambda: False)
    server_json({"claw": {"kind": "builtin"}, "engine": {"model": "claude-code/opus", "models": [MODEL]}})
    assert writeback.model_for() == "claude-code/opus"  # Claude Code 的后台不用另一次同意
    server_json({"claw": {"kind": "builtin"}, "engine": {"model": MODEL, "writeback": {"model": "moonshot/kimi-k3"}}})
    assert writeback.model_for() == "moonshot/kimi-k3"
    server_json({"claw": {"kind": "builtin"}, "engine": {"model": MODEL, "use": {"background": "moonshot/kimi-k3"}}})
    assert writeback.model_for() == "moonshot/kimi-k3"
    assert models.may_spend("chatgpt/gpt-6.1-sol", kind="outsider", origin="writeback") is False
    assert models.may_spend("claude-code/opus", kind="cyber", origin="card") is False
    assert models.may_spend("claude-code/opus", kind="cyber", origin="doorman") is False
    assert models.may_spend("claude-code/opus", kind="cyber", origin="butler") is False
    assert models.may_spend("claude-code/opus", kind="owner", origin="auto") is True


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
        n0 = conn.execute("SELECT COUNT(*) FROM card_log WHERE ref=?", (ref,)).fetchone()[0]  # 同一段接着说：id 接着编
        for i, (d, text) in enumerate(lines):
            ts = (at + timedelta(seconds=i)).isoformat(timespec="seconds")
            conn.execute("INSERT INTO card_log(id, ts, day, peer, peer_name, tier, channel, ref, dir, text, status) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                         (f"l-{ref}-{n0 + i}", ts, ts[:10], "fr-sam", "Sam", "friend", "a2a", ref, d, text, "received" if d == "in" else "sent"))


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
    only(monkeypatch, refs={"a2a:ctx-1"})  # 只看名片 Agent 的这一段（库是几个测试共用的）
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


# —— 没写成、缺东西：不空转（10/19 线上：一小时对同样几段调了 30 多次模型）——————————————————————————————————

def test_a_missing_tree_package_means_no_model_call(wb, monkeypatch):
    """那台的 Python 没装开源世界树包：原来先调模型、挑的时候才导入，每一段每一轮都白花一次。现在调模型之前就查：缺了一次都不调、不算失败，
    记成 blocked、写明缺什么怎么装；同一个缺口不再记；查过的记着，过一天（或服务重启）再查，装好了接着写。"""
    real = {k: sys.modules[k] for k in ("openmousse_tree", "openmousse_tree.provenance")}
    for k in real:
        monkeypatch.setitem(sys.modules, k, None)  # 和那台一样：导不进 openmousse_tree
    s = session("main", today(), [("user", "我决定下学期改选市场营销，不选计量了", 801)])
    touch(s, 45)
    card_talk("a2a:ctx-9", [("in", "Heads up, I'm moving to Berlin at the end of November!")])
    f = fake_model(monkeypatch)  # 剧本是空的：调一次就记下来了
    monkeypatch.setattr(writeback, "has_tree", lambda agent: True)
    only(monkeypatch, [s], {"a2a:ctx-9"})
    done = {r["session"]: r for r in run(writeback.sweep())}
    assert not f.requests and writeback.today_count() == 0 and set(done) == {s.id, "card:a2a:ctx-9"}
    for r in done.values():
        assert r["status"] == "blocked" and r["upto"] is None and r["tries"] == 0 and r["error"].startswith("missing openmousse_tree (")
    text = writeback.parsed(dict(done[s.id]))["statusText"]
    assert f"{sys.executable} -m pip install {Path(writeback.__file__).resolve().parents[2] / 'tree'}" in text and "README" in text
    run(writeback.sweep())  # 下一轮：还是不调，同一个缺口不再记（那几行不动）
    assert not f.requests and {k: writeback._wb(k)["updated_at"] for k in done} == {k: r["updated_at"] for k, r in done.items()}
    for k, m in real.items():  # 装上了包：没重启、没过一天，查过的还记着，这一轮照样不调
        monkeypatch.setitem(sys.modules, k, m)
    run(writeback.sweep())
    assert not f.requests
    writeback._needs["at"] -= writeback.NEEDS_TTL  # 过了一天再查（服务重启也一样：进程新起，查过的没了）
    f = fake_model(monkeypatch, {"candidates": [], "procedures": []}, {"candidates": []})
    done = run(writeback.sweep())
    assert len(f.requests) == 2 and [r["status"] for r in done] == ["done", "done"] and all(r["error"] is None for r in done)


def test_card_conversations_back_off_and_stop_too(wb, monkeypatch):
    """名片 Agent 那条路（outside）一样封顶：线上它没写成就每一轮重来，RETRIES 管不住。现在退避、连着 RETRIES 次就停，对方又说了话再从头数。"""
    card_talk("a2a:ctx-2", [("in", "Heads up, I'm moving to Berlin at the end of November!"), ("out", "Thanks for letting Mira know!")])
    f = failing_model(monkeypatch)
    only(monkeypatch, refs={"a2a:ctx-2"})
    key = "card:a2a:ctx-2"
    for n in range(1, writeback.RETRIES + 1):
        run(writeback.sweep())
        row = writeback._wb(key)
        assert len(f.requests) == n and row["tries"] == n and row["upto"] is None and "boom" in row["error"]
        assert run(writeback.sweep()) == [] and len(f.requests) == n  # 还在退避 / 停了：这一轮不调
        expire(key)
    assert row["status"] == "failed" and row["retry_at"] is None
    run(writeback.sweep())
    assert len(f.requests) == writeback.RETRIES
    card_talk("a2a:ctx-2", [("in", "We're having a farewell dinner on the 28th, you should come")], minutes_ago=40)
    run(writeback.sweep())
    row = writeback._wb(key)
    assert len(f.requests) == writeback.RETRIES + 1 and row["status"] == "error" and row["tries"] == 1  # 对方说了新的话：从头数


def test_an_unchanged_session_is_not_run_again(wb, monkeypatch):
    """会话没变就不重跑：写成了的游标挪过去，没新的话不再写；定时发进来的【自动触发】不算新的话（写成了的那一段话太少、不调模型，
    停了的还是停着）。"""
    ok = session("main", today(), [("user", "我决定明年春天去读一个夜校的设计课", 901)])
    bad = session("g-x", today(), [("user", "我决定下学期改选市场营销，不选计量了", 902)])
    for s in (ok, bad):
        touch(s, 45)
    monkeypatch.setattr(writeback, "has_tree", lambda agent: True)
    only(monkeypatch, [ok, bad])
    fake_model(monkeypatch, {"candidates": [], "procedures": []})
    assert run(writeback.run_session(ok.id, "idle"))["status"] == "done"
    f = failing_model(monkeypatch)
    for _ in range(writeback.RETRIES):
        run(writeback.run_session(bad.id, "idle"))
    assert writeback._wb(bad.id)["status"] == "failed" and writeback.due() == []
    for s in (ok, bad):
        say(s, "【自动触发】日结：今天的训练和饮食")
    assert [sid for sid, _ in writeback.due()] == [ok.id]  # 写成了的那条叶子挪了：再看一眼；停了的没有用户的新话：不看
    run(writeback.sweep())
    assert len(f.requests) == writeback.RETRIES and writeback._wb(ok.id)["reason"] == "too-little"  # 只是定时发进来的：不调模型
    assert writeback.due() == []


def test_new_words_start_over(wb, monkeypatch):
    """没写成以后用户又说了话：不用等退避，停下来就写，从 1 数起；停了的也一样重新开始。游标一直没动：没写成的那几句还在这一段里。"""
    s = session("main", today(), [("user", "我决定下学期改选市场营销，不选计量了", 1001)])
    touch(s, 45)
    monkeypatch.setattr(writeback, "has_tree", lambda agent: True)
    only(monkeypatch, [s])
    f = failing_model(monkeypatch)
    run(writeback.sweep())
    assert writeback._wb(s.id)["tries"] == 1 and writeback.due() == []  # 还在退避
    say(s, "还有，下学期我想多选一门设计史", 1002)
    assert dict(writeback.due()).get(s.id) == "idle"  # 有新的话：不等退避
    run(writeback.sweep())
    assert len(f.requests) == 2 and writeback._wb(s.id)["tries"] == 1  # 从 1 数起
    for _ in range(writeback.RETRIES - 1):
        expire(s.id)
        run(writeback.sweep())
    assert writeback._wb(s.id)["status"] == "failed" and len(f.requests) == writeback.RETRIES + 1
    say(s, "对了，设计史那门课我想周三上", 1003)
    f = fake_model(monkeypatch, {"candidates": [], "procedures": []})
    row = run(writeback.sweep())[0]
    assert len(f.requests) == 1 and row["status"] == "done" and row["upto"] == s.leaf and row["tries"] == 0 and row["error"] is None
    sent = f.requests[0][1].messages[-1]["content"][0]["text"]
    assert "[User #1001]: 我决定下学期改选市场营销" in sent and "[User #1003]: 对了，设计史那门课我想周三上" in sent


def test_a_busy_session_that_failed_is_not_retried_on_every_new_line(wb, monkeypatch):
    """还在聊（攒够 TURNS 句那种）的没写成：接着说的每一句都是新的话，但不是每一轮都重来，要从没写成的那一条起再攒够 TURNS 句（或者停下来）。"""
    s = session("g-chat", today(), [("user", f"第 {i} 件事：我在想下学期选哪门课", 1300 + i) for i in range(writeback.TURNS)])
    touch(s, 1)
    monkeypatch.setattr(writeback, "has_tree", lambda agent: True)
    only(monkeypatch, [s])
    f = failing_model(monkeypatch)
    assert dict(writeback.due()).get(s.id) == "turns"
    run(writeback.sweep())
    for i in range(writeback.TURNS - 1):
        say(s, f"再补一句，第 {i} 句", minutes_ago=1)
        assert s.id not in dict(writeback.due())
    run(writeback.sweep())
    assert len(f.requests) == 1
    say(s, "最后一句", minutes_ago=1)
    assert dict(writeback.due()).get(s.id) == "turns"  # 从没写成的那一条起又攒够了：再试，从 1 数起
    run(writeback.sweep())
    assert len(f.requests) == 2 and writeback._wb(s.id)["tries"] == 1


def test_per_day_counts_the_failed_calls_too(wb, monkeypatch, server_json):
    """一天的上限（per_day）数的是调了几次模型，没写成的也算（原来只数写成的，失败了白花的不进账）。"""
    server_json({"claw": {"kind": "builtin"}, "engine": {"model": MODEL, "writeback": {"per_day": 2}}})
    ss = [session(f"g-day{i}", today(), [("user", f"我决定第 {i} 个学期改选市场营销，不选计量了", 1100 + i)]) for i in range(3)]
    for s in ss:
        touch(s, 45)
    monkeypatch.setattr(writeback, "has_tree", lambda agent: True)
    only(monkeypatch, ss)
    f = failing_model(monkeypatch)
    run(writeback.sweep())
    assert len(f.requests) == 2 and writeback.today_count() == 2  # 两次都没写成，照样算进今天的
    assert sum(writeback._wb(s.id) is None for s in ss) == 1  # 第三段今天不跑了
    for s in ss:
        expire(s.id)
    run(writeback.sweep())
    assert len(f.requests) == 2  # 退避到点了也不跑：今天的次数用完了


def test_the_status_shows_what_backs_off_what_stopped_and_why(wb, monkeypatch):
    """GET /api/engine/writeback：哪几段在退避（第几次、什么时候再试）、哪几段停了、为什么；缺东西的写明缺什么、怎么补；今天调了几次模型。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(writeback.router)
    c = TestClient(app)
    a = session("main", today(), [("user", "我决定下学期改选市场营销，不选计量了", 1201)])
    b = session("g-y", today(), [("user", "我决定明年春天去读一个夜校的设计课", 1202)])
    monkeypatch.setattr(writeback, "has_tree", lambda agent: True)
    f = failing_model(monkeypatch)
    run(writeback.run_session(a.id, "idle"))
    for _ in range(writeback.RETRIES):
        run(writeback.run_session(b.id, "turns"))
    got = c.get("/api/engine/writeback").json()
    assert got["ok"] and got["blocked"] is None and got["today"] == {"calls": 1 + writeback.RETRIES, "limit": writeback.PER_DAY}
    (back,), (stop,) = got["backoff"], got["stopped"]
    assert back["session"] == a.id and back["tries"] == 1 and back["retry_at"] and "boom" in back["error"] and back["statusText"]
    assert stop["session"] == b.id and stop["tries"] == writeback.RETRIES and stop["retry_at"] is None and "boom" in stop["statusText"]
    items = {x["session"]: x for x in got["items"]}
    assert items[a.id]["status"] == "error" and items[b.id]["status"] == "failed" and items[b.id]["statusText"] == stop["statusText"]
    assert c.get("/api/engine/writeback", params={"conversation": "g-y"}).json()["backoff"] == []  # 按对话看
    # 缺东西（包没装、没有写回能用的模型）：一次都不调，写明缺什么、怎么补
    for k in ("openmousse_tree", "openmousse_tree.provenance"):
        monkeypatch.setitem(sys.modules, k, None)
    writeback._needs.clear()
    monkeypatch.setattr(writeback, "model_for", lambda: None)
    assert run(writeback.run_session(a.id, "manual"))["status"] == "blocked" and len(f.requests) == 1 + writeback.RETRIES
    blocked = c.get("/api/engine/writeback").json()["blocked"]
    assert "missing openmousse_tree" in blocked["error"] and "missing model" in blocked["error"]
    assert "pip install" in blocked["text"] and "engine.writeback.model" in blocked["text"]


def test_an_old_writeback_table_gets_the_new_columns(tmp_path, monkeypatch):
    """10/19 那台的库是加这几栏以前建的：打开的时候补上 tries、tried、retry_at，原来的行照样在。"""
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
    CREATE TABLE engine_writeback (
      session TEXT PRIMARY KEY, conversation TEXT NOT NULL, agent TEXT NOT NULL, upto TEXT, status TEXT NOT NULL, reason TEXT,
      candidates TEXT, picked TEXT, skipped TEXT, turn TEXT, error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
    INSERT INTO engine_writeback(session, conversation, agent, status, reason, error, created_at, updated_at)
      VALUES('s_old', 'main', 'main', 'error', 'idle#2', 'ModuleNotFoundError: No module named ''openmousse_tree''', 'x', 'x');
    """)
    conn.close()
    monkeypatch.setattr(config.settings, "db", path)
    with store.read() as conn:
        assert {"tries", "tried", "retry_at"} <= {r[1] for r in conn.execute("PRAGMA table_info(engine_writeback)")}
        row = dict(conn.execute("SELECT * FROM engine_writeback").fetchone())
    assert row["status"] == "error" and row["reason"] == "idle#2" and (row["tries"], row["tried"], row["retry_at"]) == (0, None, None)
    text = writeback.status_text(row)
    assert "ModuleNotFoundError" in text and "Attempt 0" not in text and "第 0 次" not in text  # 没有 retry_at 的老行：不说「第 0 次」
