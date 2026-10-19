"""每周修剪的卡一张卡多条（treeprune.py + inbox.py 的 items，2026-10-17）：一步一条、合并 / 改写 / 并进档案之前回原话核对
（对话记录在这台服务器的 messages 表里）、对不上的默认不做、老 app 点「同意」= 每条照默认、逐条「修改」按一句话重出那一步再校验、
提交只做同意的；/api/tree 的叶子带出处；内核的 tree 工具给世界树补上是哪次对话、哪条消息。模型是假的，数据都是编的。"""
from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi import HTTPException
from openmousse_tree import prune as P
from openmousse_tree import store as S
from test_tree_server import run, submit

import bridges
import chat
import inbox
import memtree
import push
import sources
import treelib
import treeprune
from config import TZ


@pytest.fixture
def tree(mousse_tree, monkeypatch):
    """开源版世界树接上了；别的测试留下的修剪卡清掉（同一周只有一张）。"""
    mousse_tree.make()
    monkeypatch.setattr(sources, "memory_tree", treelib)
    monkeypatch.setitem(sources.AVAILABLE, "tree", True)
    with chat._lock, inbox.idb() as db:
        db.execute("DELETE FROM inbox WHERE dedupe LIKE 'tree:prune:%' OR kind='tree'")
    with chat._lock, treeprune.tdb() as db:
        db.execute("DELETE FROM tree_prune")
    return mousse_tree


@pytest.fixture
def kicks(monkeypatch):
    """不真推送；收件箱往 Agent 的对话里发的话记下来（修剪的卡一句都不该发）。"""
    said: list[tuple[str, str]] = []

    async def fake_send(*a, **kw):
        return {"ok": True}
    monkeypatch.setattr(push, "send_push", fake_send)
    monkeypatch.setattr(inbox, "kick", lambda thread, text: said.append((thread, text)) or "started")
    return said


def say(thread: str, text: str, hours_ago: float = 1) -> int:
    """对话里用户说过的一句（写进 messages 表）→ 消息 id。"""
    ts = (datetime.now(TZ) - timedelta(hours=hours_ago)).isoformat(timespec="seconds")
    with chat._lock, chat.db() as conn:
        return conn.execute("INSERT INTO messages(thread, role, text, ts) VALUES(?,?,?,?)", (thread, "user", text, ts)).lastrowid


def leaf(conn, text: str, **kw) -> str:
    r = S.add(conn, text=text, source=kw.pop("source", "main"), **kw)
    assert not r["duplicate"], r
    return r["id"]


def card_for(conn) -> tuple[str, dict]:
    """一份三步的计划交上去：1 并进档案（原话对得上）、2 合并（其中一片的原话对不上）、3 归档（不核对）。"""
    mid = say("main", "From now on I only drink oat milk in my coffee")
    ok = leaf(conn, "Alex only takes oat milk in coffee", kind="preference", quote="I only drink oat milk in my coffee", thread="main",
              platform="openmousse", message=str(mid))
    say("main", "my sister is visiting next month")
    a = leaf(conn, "Alex's sister visits next month", kind="event", quote="my sister is visiting next month", thread="main", platform="openmousse")
    b = leaf(conn, "Alex's sister is moving in with Alex", kind="event", quote="my sister is moving in with me", thread="main", platform="openmousse")
    old = leaf(conn, "Alex has a dentist appointment on Monday", kind="event", source="claude")
    plan = {"summary": "Three things", "steps": [{"op": "promote", "id": ok, "section": "Food", "text": "Takes oat milk in coffee"},
                                                {"op": "merge", "ids": [a, b], "text": "Alex's sister is coming to stay", "why": "same visit"},
                                                {"op": "archive", "id": old, "why": "it has passed"}]}
    msg = submit(conn, plan)
    iid = msg.split("inbox card ")[1].split(" ")[0]
    return iid, {"ok": ok, "a": a, "b": b, "old": old, "mid": mid, "msg": msg}


def status_of(conn, mid: str) -> str:
    return dict(conn.execute("SELECT status FROM tree WHERE id=?", (mid,)).fetchone())["status"]


def test_the_card_has_one_item_per_step_with_checks_against_what_was_said(tree, kicks):
    conn = treelib.connect()
    iid, ids = card_for(conn)
    it = inbox.item(iid)
    assert it["kind"] == "tree" and it["itemsKind"] == "tree-prune" and it["caps"] == ["items", "revise"]
    one, two, three = it["items"]
    assert [x["id"] for x in it["items"]] == ["1", "2", "3"] and one["data"] == {"op": "promote", "id": ids["ok"], "section": "Food",
                                                                                   "text": "Takes oat milk in coffee"}
    assert one["check"]["state"] == "ok" and one["check"]["message"] == str(ids["mid"]) and one["check"]["thread"] == "main"
    assert one["default"] == "approve" and "Matches the conversation" in one["check"]["text"] and "main chat" in one["check"]["text"]
    assert two["check"]["state"] == "mismatch" and two["default"] == "reject" and "(1 of 2 match)" in two["check"]["text"]
    assert three["check"] is None and three["default"] == "approve" and "- why: it has passed" in three["detail"]
    assert "not done by default" in it["changes"][1] and "one item each" in ids["msg"] and "1 step(s) didn't match" in ids["msg"]
    with chat._lock, treeprune.tdb() as db:
        assert db.execute("SELECT card FROM tree_prune WHERE inbox_id=?", (iid,)).fetchone()[0] == "tree-prune"


def test_the_old_app_approving_does_only_the_default_steps(tree, kicks):
    conn = treelib.connect()
    iid, ids = card_for(conn)
    done = run(inbox.act(iid, inbox.ActIn(action="approve")))["item"]
    assert done["status"] == "done" and "1 not approved, left as is" in done["result"] and "skipped" not in done["result"]
    assert status_of(conn, ids["ok"]) == "archived" and "Takes oat milk in coffee" in S.profile_text()  # 并进了档案
    assert status_of(conn, ids["a"]) == "active" and status_of(conn, ids["b"]) == "active"  # 原话对不上的那步没做
    assert status_of(conn, ids["old"]) == "archived" and kicks == []
    assert [x["decision"] for x in done["items"]] == ["approve", "reject", "approve"]


def test_revising_a_step_redoes_it_checks_it_and_submitting_applies_the_new_version(tree, kicks, monkeypatch):
    conn = treelib.connect()
    iid, ids = card_for(conn)
    asked: list[dict] = []

    async def fake_reviser(prompt, input_, required):
        asked.append(input_)
        return {"op": "promote", "id": ids["ok"], "section": "Drinks", "text": "Only oat milk in coffee"}
    monkeypatch.setattr(inbox, "ask_reviser", fake_reviser)
    it = run(inbox.act_item(iid, "1", inbox.ItemActIn(action="revise", note="put it under Drinks")))["item"]
    one = it["items"][0]
    assert one["data"]["section"] == "Drinks" and 'Into the profile ("Drinks")' in one["text"] and one["decision"] is None
    assert one["check"]["state"] == "ok" and one["was"][0]["note"] == "put it under Drinks"
    sent = asked[0]
    assert sent["note"] == "put it under Drinks" and sent["leaves"] == [{"id": ids["ok"], "text": "Alex only takes oat milk in coffee"}]
    assert "Profile" in sent["branches"] and sent["step"]["section"] == "Food"
    res = run(inbox.act(iid, inbox.ActIn(action="submit", decisions={"1": "approve", "2": "reject", "3": "reject"})))["item"]
    assert res["status"] == "done" and "2 not approved" in res["result"]
    assert "## Drinks\n- Only oat milk in coffee" in S.profile_text() and status_of(conn, ids["old"]) == "active"
    assert [s["op"] for s in P.load(P.week_of())["steps"]] == ["promote"]  # 同意的这几步重新交成这周的计划


def test_a_revision_that_breaks_the_plan_is_retried_once_then_refused(tree, kicks, monkeypatch):
    conn = treelib.connect()
    iid, ids = card_for(conn)
    tries: list[dict] = []

    async def bad_reviser(prompt, input_, required):
        tries.append(input_)
        return {"op": "archive", "id": ids["old"]}  # 第 3 步已经动了这片
    monkeypatch.setattr(inbox, "ask_reviser", bad_reviser)
    with pytest.raises(HTTPException) as e:
        run(inbox.act_item(iid, "1", inbox.ItemActIn(action="revise", note="archive the dentist one instead")))
    assert e.value.status_code == 422 and "already in step 3" in e.value.detail
    assert len(tries) == 2 and "already in step 3" in tries[1]["problems"][0]  # 第二次带着毛病再问
    assert inbox.item(iid)["items"][0]["data"]["op"] == "promote"  # 卡上那条没动


def test_api_tree_gives_each_leaf_its_source_and_a_way_back(tree):
    conn = treelib.connect()
    mid = say("main", "Remind me that I hate early meetings", hours_ago=30)
    a = leaf(conn, "Alex dislikes early meetings", kind="preference", quote="I hate early meetings", thread="main", platform="openmousse",
             message=str(mid), cues=["scheduling a meeting", "what time works"])
    b = leaf(conn, "Alex likes Thai food", source="claude", quote="Thai is my favourite", thread="https://claude.example/chat/1")
    c = leaf(conn, "Alex wakes at seven", source="claude")
    leaves = {x["id"]: x for x in memtree.snapshot()["leaves"]}
    said = leaves[a]["said"]
    assert said["platform"] == "openmousse" and said["threadName"] == "Main chat" and said["message"] == str(mid)
    assert said["link"] == {"thread": "main", "day": chat.day_of((datetime.now(TZ) - timedelta(hours=30)).isoformat()), "messageId": mid}
    assert leaves[a]["quote"] == "I hate early meetings" and leaves[a]["cues"] == ["scheduling a meeting", "what time works"]
    assert leaves[b]["said"]["platformName"] == "Claude" and leaves[b]["said"]["link"] == {"url": "https://claude.example/chat/1"}
    assert leaves[c]["said"] is None and leaves[c]["quote"] == "" and leaves[c]["cues"] == []


@pytest.mark.skipif(not (shutil.which("mousse-tree") or (Path(sys.executable).parent / "mousse-tree").exists()),
                    reason="mousse-tree isn't installed (pip install ./tree)")
def test_the_engine_tells_the_tree_which_conversation_and_message(tree):
    token = bridges.SAID.set(("main", "4242"))
    try:
        out = json.loads(run(bridges.run("tree", ["add", "--text", "Alex keeps a paper diary", "--quote", "I still write in a paper diary"], None,
                                         "main")))
    finally:
        bridges.SAID.reset(token)
    row = dict(treelib.connect().execute("SELECT * FROM tree WHERE id=?", (out["id"],)).fetchone())
    assert (row["platform"], row["thread"], row["message"], row["quote"]) == ("openmousse", "main", "4242", "I still write in a paper diary")
    out = json.loads(run(bridges.run("tree", ["add", "--text", "Alex collects stamps"], None, "main")))  # 不是内核调的：没有
    assert dict(treelib.connect().execute("SELECT thread FROM tree WHERE id=?", (out["id"],)).fetchone())["thread"] == ""


@pytest.mark.skipif(not (shutil.which("mousse-tree") or (Path(sys.executable).parent / "mousse-tree").exists()),
                    reason="mousse-tree isn't installed (pip install ./tree)")
def test_memory_search_finds_a_leaf_by_how_it_would_be_asked(tree):
    from engine import recall
    conn = treelib.connect()
    mid = leaf(conn, "Alex weighs pasta dry before cooking", source="claude", cues=["how much is a bowl of noodles", "noodle calories"])
    out = run(recall._tree("noodle calories", "main"))  # 内核 memory_search 附在后面的那几条：正文里没有这几个词
    assert out is not None and f"[{mid}] Alex weighs pasta dry" in out
