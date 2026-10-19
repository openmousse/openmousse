"""Zen 想完写的叶子带出处（think.py，2026-10-19）：platform zen、thread = 主题 id、message = 原话出自的那条想法、quote = 那段原话；
每周修剪核对原话时回 think 的存储找（treeprune.find_said 的 Zen 分支）：对得上 ok、改了 mismatch、想法删了 missing；/api/tree 的出处
能跳回那个主题。数据都是编的。"""
from __future__ import annotations

import json

import pytest
from openmousse_tree import provenance as V
from openmousse_tree import store as S

import chat
import config
import memtree
import sources
import think
import treelib
import treeprune


@pytest.fixture
def zen(mousse_tree, monkeypatch, server_json, tmp_path):
    mousse_tree.make(name="Mira")
    monkeypatch.setattr(sources, "memory_tree", treelib)
    monkeypatch.setitem(sources.AVAILABLE, "tree", True)
    server_json({"claw": {"kind": "builtin"}, "think": {"vault": str(tmp_path / "vault")}})
    think._cache.clear()
    return tmp_path / "vault"


def thought(fid: str, text: str) -> dict:
    p = think.inbox_dir() / f"2026-10-18 2100 {fid}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    think.write_atomic(p, think.dump_note({"id": fid, "kind": "text", "created_at": "2026-10-18T21:00:00+01:00", "source": "app"}, text))
    return next(f for f in think.scan() if f["id"] == fid)


def topic(tid: str, title: str, frags: list[dict]) -> None:
    with chat._lock, think.tdb() as conn:
        conn.execute("INSERT OR REPLACE INTO think_topics(id, title, fragments, status, created_at, updated_at) VALUES(?,?,?,?,?,?)",
                     (tid, title, json.dumps([f["id"] for f in frags]), "open", chat.now_iso(), chat.now_iso()))


def test_saving_a_finished_topic_writes_a_leaf_with_its_source(zen):
    a = thought("th-0000000a", "今天在想记忆该归谁。我觉得记忆最重要的是能带走，不被任何一家平台锁住。")
    b = thought("th-0000000b", "顺便想到，备份要放在自己的硬盘上。")
    topic("tp-0000000a", "记忆该归谁", [a, b])
    out = think.save_sync("tp-0000000a", think.SaveIn(title="记忆该归谁", oneLine="能带走", tree="Mira 认为记忆最重要的是能带走，不被平台锁住",
                                                    keywords=["记忆"]))
    leaf = dict(S.connect().execute("SELECT * FROM tree WHERE id=?", (out["tree"]["id"],)).fetchone())
    assert (leaf["platform"], leaf["thread"], leaf["message"]) == ("zen", "tp-0000000a", "th-0000000a")
    assert "记忆最重要的是能带走" in leaf["quote"] and len(leaf["quote"]) <= think.QUOTE_MAX
    assert memtree.tier_of(leaf["source"], leaf["quote"], leaf["platform"]) == "quoted"  # 用户自己写的想法
    snap = memtree.snapshot()
    said = next(x for x in snap["leaves"] if x["id"] == leaf["id"])["said"]
    assert said["platformName"] == "Zen" and said["link"] == {"topic": "tp-0000000a", "thought": "th-0000000a"}


def test_the_quote_is_cut_from_the_closest_part_of_a_long_thought(zen):
    long = "开头讲了很多无关的事情。" * 30 + "其实我最在意的是每周日晚上能完整地休息一下，不安排任何工作。" + "后面又写了别的。" * 30
    a = thought("th-0000000c", long)
    said = think.zen_said("tp-0000000c", [a], "Mira 最在意每周日晚上能完整休息、不安排工作")
    assert said["message"] == "th-0000000c" and "每周日晚上" in said["quote"] and len(said["quote"]) <= think.QUOTE_MAX
    assert think.zen_said("tp-nothing0", [], "anything") == {"platform": "zen", "thread": "tp-nothing0"}  # 没有原话：只记是哪个主题


def test_pruning_checks_the_quote_against_the_thoughts(zen):
    a = thought("th-0000000d", "我决定以后每天只看两次邮件")
    topic("tp-0000000d", "邮件", [a])
    row = {"platform": "zen", "thread": "tp-0000000d", "message": "th-0000000d", "quote": "每天只看两次邮件"}
    assert V.leaf(row, treeprune.find_said)["state"] == "ok"
    assert V.leaf({**row, "quote": "每周只看一次邮件"}, treeprune.find_said)["state"] == "mismatch"
    (think.inbox_dir() / "2026-10-18 2100 th-0000000d.md").unlink()  # 想法在 Obsidian 里删了
    think._cache.clear()
    assert V.leaf(row, treeprune.find_said)["state"] == "missing"
    assert V.leaf({"platform": "zen", "thread": "tp-gone0000", "quote": "x"}, treeprune.find_said)["state"] == "missing"


def test_talk_lines_count_as_the_users_own_words_too(zen):
    topic("tp-0000000e", "跑步", [])
    with chat._lock, chat.db() as conn:
        mid = conn.execute("INSERT INTO messages(thread, role, text, ts) VALUES(?,?,?,?)",
                           ("tp-0000000e", "user", "我打算明年春天跑第一个半马", chat.now_iso())).lastrowid
    said = think.zen_said("tp-0000000e", [], "Mira 打算明年春天跑第一个半马")
    assert said["message"] == str(mid) and said["quote"] == "我打算明年春天跑第一个半马"
    assert V.leaf({**said}, treeprune.find_said)["state"] == "ok"
    assert config.settings.app_name  # 服务器配置还在
