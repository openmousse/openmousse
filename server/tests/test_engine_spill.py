"""第 2 批：工具输出太长存成文件、让模型翻页（engine/spill.py，engine.loop.spill）。模型、工具都是假的。"""
from __future__ import annotations

import asyncio
import os
import re
import time

import pytest

import engine
from engine import spill
from engine.session import MemorySession, text_of, user
from test_engine import FakeModel

ON = {"engine": {"model": "deepseek/deepseek-flash", "loop": {"spill": True}}}
BIG = "\n".join(f"line {i:03d} " + "x" * 490 for i in range(1, 121))  # 120 行、约 6 万字


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def fake(monkeypatch):
    from engine import providers

    def make(script):
        f = FakeModel(script)
        monkeypatch.setattr(providers, "stream", f.stream)
        return f
    return make


def dump() -> engine.Tool:
    async def go(args, ctx):
        return BIG
    return engine.Tool(name="dump", description="prints a lot", schema={"type": "object", "properties": {}}, run=go, classify=lambda a: ("read", "t"))


def spec(conv: str) -> engine.Spec:
    return engine.Spec(kind="owner", model="deepseek/x", tools=[dump()], conversation=conv)


def test_long_output_is_saved_and_can_be_paged(server_json, fake):
    server_json(ON)
    f = fake([{"calls": [("c1", "dump", {})]}, {"text": "seen"}])
    res = run(engine.run_turn(spec("t-spill"), MemorySession(), user("go")))
    shown = next(text_of(m) for m in res.messages if m["role"] == "tool")
    m = re.search(r'read_output\(id="([^"]+)", offset=(\d+)\)', shown)
    assert m and shown.startswith("line 001 ") and shown.rstrip().endswith("x" * 20) and "of 120) are not shown here" in shown
    assert len(shown) < 31_000  # 还是那么长（以前掐中间也是这么长）
    assert "read_output" in [t["name"] for t in f.requests[0][1].tools]  # 开着就给翻页的工具
    sid, first = m.group(1), int(m.group(2))
    saved = spill.folder("t-spill") / f"{sid}.txt"
    assert saved.read_text(encoding="utf8") == BIG and oct(saved.stat().st_mode & 0o777) == "0o600"
    f2 = fake([{"calls": [("c2", "read_output", {"id": sid, "offset": first, "limit": 3})]}, {"text": "ok"}])
    res2 = run(engine.run_turn(spec("t-spill"), MemorySession(), user("more")))
    page = next(text_of(m) for m in res2.messages if m["role"] == "tool")
    assert page.startswith(f"line {first:03d} ") and f"[Showing lines {first}-{first + 2} of 120. Use offset={first + 3} to continue.]" in page
    assert f2.requests


def test_other_conversations_cannot_read_it(server_json, fake):
    server_json(ON)
    fake([{"calls": [("c1", "dump", {})]}, {"text": "seen"}])
    res = run(engine.run_turn(spec("t-spill-a"), MemorySession(), user("go")))
    sid = re.search(r'id="([^"]+)"', next(text_of(m) for m in res.messages if m["role"] == "tool")).group(1)
    assert spill.read("t-spill-a", sid, 1, 1).startswith("line 001")
    with pytest.raises(engine.ToolFailure):
        spill.read("t-spill-b", sid)
    with pytest.raises(engine.ToolFailure):
        spill.read("t-spill-a", "../t-spill-b/x")


def test_off_by_default_clips_as_before(server_json, fake):
    server_json({"engine": {"model": "deepseek/deepseek-flash"}})
    f = fake([{"calls": [("c1", "dump", {})]}, {"text": "seen"}])
    res = run(engine.run_turn(spec("t-spill-off"), MemorySession(), user("go")))
    shown = next(text_of(m) for m in res.messages if m["role"] == "tool")
    assert "characters cut" in shown and "read_output" not in shown
    assert "read_output" not in [t["name"] for t in f.requests[0][1].tools]


def test_old_files_are_cleaned_up(server_json):
    server_json(ON)
    old = spill.save("t-spill-prune", "t_old00001", "c1", "old output")
    new = spill.save("t-spill-prune", "t_new00001", "c2", "new output")
    p_old = spill.folder("t-spill-prune") / f"{old}.txt"
    past = time.time() - 30 * 86400
    os.utime(p_old, (past, past))
    assert spill.prune() >= 1
    assert not p_old.exists() and (spill.folder("t-spill-prune") / f"{new}.txt").exists()
