"""开源版世界树 0.4（tree/ 的 openmousse_tree）：叶子带出处（原话、来自哪次对话）、「用得上的时候」进全文索引、老库升级、
修剪时出处跟着走、逐条只做同意的几步、回原话核对（provenance.py）。全在临时目录里（conftest 的 mousse_tree），数据都是编的。"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import time

import pytest
from openmousse_tree import cli
from openmousse_tree import config as C
from openmousse_tree import notes as N
from openmousse_tree import prune as P
from openmousse_tree import provenance as V
from openmousse_tree import server
from openmousse_tree import store as S

BOTH = pytest.mark.parametrize("storage", ["sqlite", "markdown"])

# 0.3 的表：有 branch，没有出处和 cues；全文索引只有 text、tags
V03 = """
CREATE TABLE tree (id TEXT PRIMARY KEY, text TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'fact', tags TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL, observed_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active', supersedes TEXT, created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL, branch TEXT);
CREATE VIRTUAL TABLE tree_fts USING fts5(text, tags, content='tree', content_rowid='rowid', tokenize='trigram');
CREATE TRIGGER tree_ai AFTER INSERT ON tree BEGIN INSERT INTO tree_fts(rowid, text, tags) VALUES (new.rowid, new.text, new.tags); END;
CREATE TRIGGER tree_ad AFTER DELETE ON tree BEGIN INSERT INTO tree_fts(tree_fts, rowid, text, tags) VALUES ('delete', old.rowid, old.text, old.tags); END;
CREATE TRIGGER tree_au AFTER UPDATE ON tree BEGIN
  INSERT INTO tree_fts(tree_fts, rowid, text, tags) VALUES ('delete', old.rowid, old.text, old.tags);
  INSERT INTO tree_fts(rowid, text, tags) VALUES (new.rowid, new.text, new.tags); END;
CREATE TABLE activity (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, actor TEXT NOT NULL, text TEXT NOT NULL);
CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT);
"""


def leaf(conn, mid: str) -> dict:
    return dict(conn.execute("SELECT * FROM tree WHERE id=?", (mid,)).fetchone())


def add(conn, text: str, **kw) -> str:
    r = S.add(conn, text=text, source=kw.pop("source", "claude"), **kw)
    assert not r["duplicate"], r
    return r["id"]


# —— 写进去、找出来 ——————————————————————————————————————————————————————————————

@BOTH
def test_a_leaf_keeps_its_quote_conversation_and_cues(mousse_tree, storage):
    mousse_tree.make(storage)
    conn = S.connect()
    mid = add(conn, "Alex weighs pasta dry, before cooking", kind="preference", quote="  「I always weigh it dry, before it goes in」 ",
              thread="chat-42", message="m7", cues="log a bowl of pasta；how many grams is a bowl；pasta calories；pasta calories")
    r = leaf(conn, mid)
    assert r["quote"] == "I always weigh it dry, before it goes in"  # 外面的引号、空白去掉
    assert (r["platform"], r["thread"], r["message"]) == ("claude", "chat-42", "m7")  # 没写平台：就是写的那个平台
    assert r["cues"].split("\n") == ["log a bowl of pasta", "how many grams is a bowl", "pasta calories"]  # 重复的去掉
    # 正文、标签里都没有的说法，靠「用得上的时候」找回来：长词走全文索引，短词走 LIKE
    assert [x["id"] for x in S.recall(conn, "grams bowl")] == [mid]
    assert [x["id"] for x in S.recall(conn, "calories")] == [mid]
    text = S.fmt(S.recall(conn, "calories"))
    assert 'quote: "I always weigh it dry, before it goes in"' in text and "(from claude · chat-42)" in text
    if storage == "markdown":  # 笔记里是属性，原话加了引号
        note = (N.root() / r["path"]).read_text(encoding="utf8")
        assert 'quote: "I always weigh it dry, before it goes in"' in note and 'thread: "chat-42"' in note
        assert 'cues:\n  - "log a bowl of pasta"\n  - "how many grams is a bowl"\n  - "pasta calories"' in note
    export = S.export(conn).read_text(encoding="utf8")
    assert "(useful when: log a bowl of pasta; how many grams is a bowl; pasta calories)" in export


@BOTH
def test_no_source_stays_empty_and_superseding_keeps_the_cues(mousse_tree, storage):
    mousse_tree.make(storage)
    conn = S.connect()
    old = add(conn, "Alex runs on Tuesdays", cues=["running days", "when does Alex run"], platform="ignored-without-a-quote")
    assert {k: leaf(conn, old)[k] for k in S.PROV} == dict.fromkeys(S.PROV, "")  # 没有原话也没有对话：不存出处
    new = add(conn, "Alex runs on Tuesdays and Fridays now", supersedes=old)
    assert leaf(conn, new)["cues"].split("\n") == ["running days", "when does Alex run"]  # 自己没写：沿用旧的
    assert leaf(conn, old)["status"] == "superseded"
    longq = "x" * (S.QUOTE_MAX + 50)
    assert len(leaf(conn, add(conn, "Alex hums while cooking", quote=longq))["quote"]) == S.QUOTE_MAX  # 原话截到合理长度
    assert S.cue_list("a | b ; c；d\ne\nf") == ["a", "b", "c", "d", "e"]  # 最多五句


@BOTH
def test_forgetting_wipes_the_quote_and_cues_too(mousse_tree, storage):
    mousse_tree.make(storage)
    conn = S.connect()
    mid = add(conn, "Alex's sister Mia is learning Portuguese", quote="Mia started Portuguese lessons!", thread="chat-9",
              cues="Mia's hobbies")
    assert S.set_status(conn, mid, "retracted", "owner")
    r = leaf(conn, mid)
    assert r["status"] == "retracted" and all(not r[k] for k in ("text", "quote", "thread", "message", "cues"))
    assert S.recall(conn, "Portuguese") == []
    if storage == "markdown":
        shell = (N.root() / r["path"]).read_text(encoding="utf8")
        assert "Portuguese" not in shell and "quote" not in shell and "cues" not in shell


def test_note_edits_keep_the_source_and_users_own_properties(mousse_tree):
    mousse_tree.make("markdown")
    conn = S.connect()
    mid = add(conn, "Alex prefers aisle seats", quote="aisle, always", thread="chat-3", cues="booking a flight")
    path = N.root() / leaf(conn, mid)["path"]
    raw = path.read_text(encoding="utf8").replace("created_at:", "mood: calm\ncreated_at:")
    path.write_text(raw, encoding="utf8")
    S.set_branch(conn, mid, "People", "owner")  # 改一篇笔记（挪枝）：出处、cues、用户加的属性都留着
    after = (N.root() / leaf(conn, mid)["path"]).read_text(encoding="utf8")
    assert 'quote: "aisle, always"' in after and "mood: calm" in after and '  - "booking a flight"' in after
    r = leaf(conn, mid)
    assert (r["quote"], r["thread"], r["cues"]) == ("aisle, always", "chat-3", "booking a flight")


# —— 老库升级 ————————————————————————————————————————————————————————————————————

def test_upgrade_from_0_3_sqlite_adds_columns_and_rebuilds_the_search_index(mousse_tree):
    mousse_tree.make()
    with sqlite3.connect(C.DB) as old:
        old.executescript(V03)
        old.execute("INSERT INTO tree VALUES ('d1e2f3a4b5c6', 'Alex bakes sourdough on Sundays', 'fact', 'baking', 'claude', '2026-09-05', "
                    "'active', NULL, '2026-09-05T08:00:00+00:00', '2026-09-05T08:00:00+00:00', NULL)")
    conn = S.connect()
    assert set(S.EXTRA) <= {r[1] for r in conn.execute("PRAGMA table_info(tree)")}
    assert "cues" in {r[1] for r in conn.execute("PRAGMA table_info(tree_fts)")}
    assert [x["id"] for x in S.recall(conn, "sourdough")] == ["d1e2f3a4b5c6"]  # 老叶子照样找得到
    assert leaf(conn, "d1e2f3a4b5c6")["quote"] == ""
    mid = add(conn, "Alex keeps a starter called Bubbles", cues="what is the starter's name")
    assert [x["id"] for x in S.recall(conn, "starter's name")] == [mid]
    assert S.migrate(conn) is False  # 再连一次：不再动


def test_upgrade_from_0_3_markdown_reads_quotes_written_in_obsidian(mousse_tree):
    mousse_tree.make("markdown")
    note = ("---\nid: e1f2a3b4c5d6\nkind: fact\nsource: owner\nobserved_at: 2026-09-06\nstatus: active\nbranch: \"[[Profile]]\"\n"
            "quote: we adopted a greyhound!\ncues:\n  - our dog's name\ncreated_at: 2026-09-06T09:00:00+00:00\n"
            "updated_at: 2026-09-06T09:00:00+00:00\n---\nAlex has a greyhound called Dash\n")
    (mousse_tree.vault / "2026-09-06 Alex has a greyhound.md").write_text(note, encoding="utf8")
    with sqlite3.connect(C.INDEX) as old:  # 0.3 的索引
        old.executescript(V03.replace("updated_at TEXT NOT NULL, branch TEXT);", "updated_at TEXT NOT NULL, branch TEXT, path TEXT NOT NULL DEFAULT '');")
                          + "CREATE TABLE issue (path TEXT NOT NULL, problem TEXT NOT NULL);")
        old.execute("INSERT INTO meta(k, v) VALUES ('sig', 'old-signature')")
    t = time.time() - 30  # 装成是一会儿以前写的（1 秒内刚写的文件，索引会等下一轮再收）
    for p in mousse_tree.vault.rglob("*"):
        os.utime(p, (t, t))
    conn = S.connect()
    r = leaf(conn, "e1f2a3b4c5d6")
    assert r["quote"] == "we adopted a greyhound!" and r["cues"] == "our dog's name"
    assert [x["id"] for x in S.recall(conn, "dog's name")] == ["e1f2a3b4c5d6"]


# —— 命令行、MCP ——————————————————————————————————————————————————————————————————

def run_cli(monkeypatch, capsys, *argv: str, env: dict | None = None) -> str:
    for k in ("MOUSSE_AGENT", "MOUSSE_THREAD", "MOUSSE_MESSAGE"):
        monkeypatch.delenv(k, raising=False)
    for k, v in (env or {}).items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(sys, "argv", ["mousse-tree", *argv])
    cli.main()
    return capsys.readouterr().out


def test_cli_takes_the_source_and_openmousse_fills_in_the_conversation(mousse_tree, monkeypatch, capsys):
    mousse_tree.make()
    out = run_cli(monkeypatch, capsys, "add", "--text", "Alex wants window seats", "--quote", "window please", "--cue", "booking a train",
                  "--cue", "seat preference", env={"MOUSSE_AGENT": "main", "MOUSSE_THREAD": "main", "MOUSSE_MESSAGE": "321"})
    r = leaf(S.connect(), json.loads(out)["id"])
    assert (r["source"], r["platform"], r["thread"], r["message"], r["quote"]) == ("main", "openmousse", "main", "321", "window please")
    assert r["cues"] == "booking a train\nseat preference"
    out = run_cli(monkeypatch, capsys, "add", "--text", "Alex reads on the bus", "--thread", "https://chat.example.com/c/abc",
                  "--platform", "chatgpt", "--cues", "commute habits;reading time", env={"MOUSSE_THREAD": "main"})
    r = leaf(S.connect(), json.loads(out)["id"])  # 写了对话的用写的那个，环境变量不管
    assert (r["platform"], r["thread"], r["message"]) == ("chatgpt", "https://chat.example.com/c/abc", "")
    assert "quote: \"window please\"" in run_cli(monkeypatch, capsys, "recall", "--q", "window")


def test_mcp_remember_takes_quote_conversation_and_cues(mousse_tree):
    mousse_tree.make()
    srv = server.platform_server("gemini", C.load())
    tools = {t.name: t for t in asyncio.run(srv.list_tools())}
    assert {"quote", "conversation", "cues"} <= set(tools["remember"].inputSchema["properties"])
    assert "quote" in server.instructions(C.load())

    def call(name: str, **args) -> str:
        out = asyncio.run(srv.call_tool(name, args))
        blocks = out[0] if isinstance(out, tuple) else out
        return "".join(getattr(b, "text", "") for b in blocks)
    said = call("remember", text="Alex's colleague Priya runs the Friday quiz", quote="Priya does the quiz every Friday",
                conversation="conv-77", cues="who runs the quiz；Friday plans")
    mid = said.split("[")[1].split("]")[0]
    r = leaf(S.connect(), mid)
    assert (r["platform"], r["thread"], r["quote"]) == ("gemini", "conv-77", "Priya does the quiz every Friday")
    assert 'quote: "Priya does the quiz every Friday" (from gemini · conv-77)' in call("recall", query="quiz")
    assert call("remember", text="Alex likes rainy days").startswith("Remembered [")  # 老的调法照旧


# —— 修剪：出处跟着走、只做同意的几步、改过的一步再校验 ————————————————————————————————

@BOTH
def test_merging_carries_the_source_and_cues(mousse_tree, storage):
    mousse_tree.make(storage)
    conn = S.connect()
    a = add(conn, "Alex takes the 8:10 bus", cues="commute")
    b = add(conn, "Every weekday Alex catches the 8:10", quote="I'm on the 8:10 every day", thread="chat-5", cues="morning routine")
    P.plan(conn, {"steps": [{"op": "merge", "ids": [a, b], "text": "Alex takes the 8:10 bus every weekday"}]})
    assert P.apply(conn)["done"] == 1
    new = conn.execute("SELECT * FROM tree WHERE source='prune' AND status='active'").fetchone()
    assert new["quote"] == "I'm on the 8:10 every day" and new["thread"] == "chat-5" and new["cues"] == "commute\nmorning routine"


def test_apply_only_does_the_approved_steps(mousse_tree):
    mousse_tree.make()
    conn = S.connect()
    a, b, c = add(conn, "Alex owns a red bike"), add(conn, "Alex's old phone is cracked", kind="event"), add(conn, "Alex likes chess")
    P.plan(conn, {"steps": [{"op": "archive", "id": b}, {"op": "rewrite", "id": a, "text": "Alex owns a red road bike"},
                            {"op": "archive", "id": c}]})
    res = P.apply(conn, only={2})
    assert res["done"] == 1 and [n for n, _ in res["skipped"]] == [1, 3] and "not approved" in res["skipped"][0][1]
    assert leaf(conn, b)["status"] == "active" and leaf(conn, c)["status"] == "active" and leaf(conn, a)["status"] == "superseded"
    again = P.apply(conn)  # 再跑一次：没同意的以后也不做
    assert again["done"] == 1 and leaf(conn, b)["status"] == "active"


def test_a_revised_step_is_checked_against_the_rest_of_the_plan(mousse_tree):
    mousse_tree.make()
    conn = S.connect()
    a, b, c = add(conn, "Alex swims"), add(conn, "Alex plays tennis"), add(conn, "Alex climbs on weekends")
    P.plan(conn, {"steps": [{"op": "archive", "id": a}, {"op": "rewrite", "id": b, "text": "Alex plays tennis twice a week"},
                            {"op": "sprout", "name": "Sport", "about": "Exercise", "move": [c]}]})
    doc = P.load()
    st = P.recheck(conn, doc, 2, {"op": "rewrite", "id": b, "text": "Alex plays tennis on Mondays"})
    assert st["text"] == "Alex plays tennis on Mondays" and st["was"] == "Alex plays tennis"
    with pytest.raises(P.PlanError) as e:  # 别的那步已经动了 a
        P.recheck(conn, doc, 2, {"op": "merge", "ids": [a, b], "text": "Alex swims and plays tennis"})
    assert "Step 2:" in str(e.value) and "already in step 1" in str(e.value)
    st = P.recheck(conn, doc, 1, {"op": "branch", "id": a, "to": "Sport"})  # 第 3 步长出来的枝算数
    assert st["to"] == "Sport"
    assert P.raw_step(doc["steps"][1]) == {"op": "rewrite", "id": b, "text": "Alex plays tennis twice a week"}


# —— 回原话核对 ——————————————————————————————————————————————————————————————————

def test_quote_matching_tolerates_spacing_and_a_typo_but_not_a_paraphrase():
    said = "OK fine. 算了 word 版本吧，PDF 不用了"
    assert V.found_in("算了word版本吧", said)
    assert V.found_in("ok, FINE", said)
    assert V.found_in("I always weigh pasta dry befor it goes in", "honestly I always weigh pasta dry before it goes in the pot")
    assert not V.found_in("I prefer the Word version", said)
    assert not V.found_in("", said) and V.found_in("hi", "say hi to him") and not V.found_in("hey", "say hi to him")


def test_leaf_and_step_checks_and_their_defaults(mousse_tree):
    mousse_tree.make()
    conn = S.connect()
    ok = add(conn, "Alex trains three times a week", quote="three sessions a week for now", thread="t1")
    bad = add(conn, "Alex trains four times a week", quote="four times a week", thread="t1", message="9")
    gone = add(conn, "Alex skips leg day", quote="no legs today", thread="t-deleted")
    far = add(conn, "Alex loves jazz", quote="jazz is my thing")  # 在 claude 上说的：这里查不了
    old = add(conn, "Alex wakes at six")  # 以前的叶子：没有出处
    said = {"t1": [("8", "ok so three sessions a week for now"), ("9", "maybe more later")]}

    def find(row):  # 服务器那边的样子：t1 查得到，t-deleted 该有却没有，别的这里查不了
        if row["thread"] == "t-deleted":
            return {"record": False, "where": "a deleted chat"}
        if row["thread"] not in said:
            return None
        msgs = said[row["thread"]]
        if row["message"]:
            text = dict(msgs).get(row["message"], "")
            return {"record": True, "found": V.found_in(row["quote"], text), "said": text, "where": "t1"}
        return {"record": True, "found": any(V.found_in(row["quote"], t) for _, t in msgs), "where": "t1", "day": "2026-10-09"}
    states = {mid: V.leaf(leaf(conn, mid), find)["state"] for mid in (ok, bad, gone, far, old)}
    assert states == {ok: "ok", bad: "mismatch", gone: "missing", far: "unverifiable", old: "none"}
    far_check = V.leaf(leaf(conn, far), find)
    assert "claude" in far_check["text"]
    mm = V.leaf(leaf(conn, bad), find)
    assert 'that message says "maybe more later"' in mm["text"]
    assert V.leaf(leaf(conn, ok), find)["day"] == "2026-10-09"
    merge = V.step(conn, {"op": "merge", "ids": [ok, bad], "text": "x"}, find)
    assert merge["state"] == "mismatch" and "(1 of 2 match)" in merge["text"] and V.default(merge) == "reject"
    assert V.default(V.step(conn, {"op": "promote", "id": old, "section": "Habits", "text": "x"}, find)) == "approve"
    assert V.default(V.step(conn, {"op": "rewrite", "id": far, "text": "x"}, lambda row: None)) == "approve"  # 查不了：照以前
    assert V.default(V.step(conn, {"op": "rewrite", "id": gone, "text": "x"}, find)) == "reject"
    assert V.step(conn, {"op": "archive", "id": bad}, find) is None  # 归档不核对
