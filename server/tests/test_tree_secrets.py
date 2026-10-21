"""世界树写入前隐去密钥：写回、待定长成叶子、想完了。只在 builtin 改工作区那份脚本那条路；OpenClaw 原样。

样本是编的。开源包自己计数；这里不在交给包之前先换掉。
"""
from __future__ import annotations

import asyncio
import json

import pytest
from openmousse_tree import store as S

import bridges
import chat
import config
import memhold
import sources
import treelib
from engine import memgate, writeback

NOTION = "ntn_aB3dEf7hIj9kLm1nOp2qRs4tUv6wXy8z"


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def wb(tmp_path, monkeypatch, server_json, mousse_tree):
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setattr(config.settings, "workspace", ws)
    mousse_tree.make(name="Mira")
    monkeypatch.setattr(sources, "memory_tree", treelib)
    monkeypatch.setitem(sources.AVAILABLE, "tree", True)
    server_json({"claw": {"kind": "builtin"}, "engine": {"model": "deepseek/deepseek-flash"}})
    with chat._lock, memhold.hdb() as conn:
        conn.execute("DELETE FROM memory_held")
    return tmp_path


def test_package_path_is_not_scrubbed_before_add(wb):
    assert sources.tree_script() is None
    assert NOTION in memgate.scrub_script(f"see {NOTION}")


def test_write_leaf_stores_the_placeholder_and_the_count(wb):
    leaf, dup = run(writeback.write_leaf("main", "conv-1", {
        "text": f"Mira pasted {NOTION} into a note", "kind": "fact", "quote": f"the key was {NOTION}",
        "cues": [NOTION],
    }))
    assert leaf and not dup
    row = S.connect().execute("SELECT text, quote, cues, redacted FROM tree WHERE id=?", (leaf,)).fetchone()
    blob = f"{row['text']} {row['quote']} {row['cues']}"
    assert NOTION not in blob and row["redacted"] >= 3 and "‹redacted›" in row["text"]


def test_grow_copies_the_redacted_leaf_back_into_the_held_row(wb):
    hid = memhold.hold(f"Sam mentioned {NOTION}", who="Sam", channel="a2a", via="Sam's agent", who_id="fr-sam",
                       thread="a2a:ctx-9", message="l-1", quote=f"look {NOTION}", cues=[NOTION])["id"]
    assert NOTION in memhold.listing()[0]["text"]  # 还没同意：待定里先留着
    memhold.grow(hid)
    row = memhold.listing("approved")[0]
    assert NOTION not in row["text"] and NOTION not in row["quote"] and NOTION not in " ".join(row["cues"])
    leaf = S.connect().execute("SELECT text, quote, redacted FROM tree WHERE id=?", (row["leaf"],)).fetchone()
    assert NOTION not in leaf["text"] and NOTION not in leaf["quote"] and leaf["redacted"] >= 2


def test_grow_onto_an_existing_leaf_keeps_the_rows_own_words(wb):
    """第二次说同一件事：树里那片已经有了（隐去过），这一行留自己的原话和「用得上的时候」，只换掉自己的密钥。"""
    first = memhold.hold(f"Sam mentioned {NOTION}", who="Sam", channel="a2a", via="Sam's agent", who_id="fr-sam",
                         thread="a2a:ctx-9", quote=f"look {NOTION}", cues=["the first card"])["id"]
    memhold.grow(first)
    again = memhold.hold(f"Sam mentioned {NOTION}", who="Sam", channel="a2a", via="Sam's agent", who_id="fr-sam",
                         thread="a2a:ctx-10", quote=f"told you twice, {NOTION}", cues=["the second card"])["id"]
    assert memhold.grow(again)["duplicate"]
    row = next(x for x in memhold.listing("approved") if x["id"] == again)
    assert row["quote"].startswith("told you twice") and row["cues"] == ["the second card"]
    assert NOTION not in row["text"] and NOTION not in row["quote"]


def test_author_script_is_scrubbed_only_when_builtin(wb, monkeypatch, tmp_path):
    script = tmp_path / "memory_tree.py"
    script.write_text("# fictional author script\n", encoding="utf8")
    monkeypatch.setattr(sources, "tree_script", lambda: script)
    captured: dict = {}

    async def fake_run(name, args, stdin, agent, *, kill_on_cancel=False):
        captured["args"] = list(args)
        return '{"id": "leaf1", "duplicate": false}\n'

    monkeypatch.setattr(bridges, "run", fake_run)
    run(writeback.write_leaf("main", "conv-1", {"text": f"Mira saved {NOTION}", "kind": "fact", "quote": NOTION, "cues": [NOTION]}))
    blob = " ".join(captured["args"])
    assert NOTION not in blob and "‹redacted›" in blob


def test_openclaw_author_script_is_left_alone(wb, monkeypatch, server_json, tmp_path):
    server_json({"claw": {"kind": "openclaw"}})
    script = tmp_path / "memory_tree.py"
    script.write_text("# fictional author script\n", encoding="utf8")
    monkeypatch.setattr(sources, "tree_script", lambda: script)
    assert NOTION in memgate.scrub_script(f"see {NOTION}")
    captured: dict = {}

    async def fake_run(name, args, stdin, agent, *, kill_on_cancel=False):
        captured["args"] = list(args)
        return '{"id": "leaf1", "duplicate": false}\n'

    monkeypatch.setattr(bridges, "run", fake_run)
    run(writeback.write_leaf("main", "conv-1", {"text": f"Mira saved {NOTION}", "kind": "fact", "quote": NOTION}))
    assert NOTION in " ".join(captured["args"])


def test_think_package_path_records_the_count(wb):
    import think
    out = think.remember_tree(f"Mira noted {NOTION}", None, ["demo"],
                              said={"quote": f"see {NOTION}", "platform": "zen", "thread": "tp-1", "message": "th-1"})
    assert out and out.get("redacted", 0) >= 1
    row = S.connect().execute("SELECT text, quote, redacted FROM tree WHERE id=?", (out["id"],)).fetchone()
    assert NOTION not in row["text"] and NOTION not in row["quote"] and row["redacted"] >= 1


def test_think_scrubs_the_author_script_when_builtin(wb, monkeypatch, tmp_path):
    import think
    script = tmp_path / "memory_tree.py"
    script.write_text("# fictional author script\n", encoding="utf8")
    monkeypatch.setattr(sources, "tree_script", lambda: script)
    seen: dict = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd

        class P:
            stdout = '{"id": "leaf-z"}\n'
            returncode = 0
        return P()

    monkeypatch.setattr(think.subprocess, "run", fake_run)
    out = think.remember_tree(f"Mira noted {NOTION}", None, ["demo"], said={"quote": NOTION, "platform": "zen", "thread": "tp-1", "message": "th-1"})
    blob = " ".join(seen["cmd"])
    assert NOTION not in blob and "‹redacted›" in blob and out.get("id") == "leaf-z"


def test_writeback_log_drops_the_secret_only_for_builtin(wb, server_json):
    raw = json.dumps({"text": f"saw {NOTION}"}, ensure_ascii=False)
    assert NOTION not in writeback._scrubbed(raw) and "‹redacted›" in writeback._scrubbed(raw)
    server_json({"claw": {"kind": "openclaw"}})
    assert writeback._scrubbed(raw) == raw
