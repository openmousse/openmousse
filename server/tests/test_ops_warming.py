"""第 7 批 15：提示缓存保温（engine/warming.py）。默认关；只记主人自己在 app 里、Claude、1 小时缓存的那一次；保温照原样再发（只改
max_tokens），记一轮 origin warming（用途 warming）；出错就不再保温；过了 hours 不保；急停、到了花费上限时跳过。模型是 evals/fakemodel.py 的假模型
服务器（真的走 HTTP），不连网、不花钱。"""
from __future__ import annotations

import asyncio
import json
import sys
import time
import types

import pytest

import engine
from engine import fallback, loop, store, usage, warming
from engine.session import MemorySession, user
from evals.fakemodel import Fail, FakeModelServer, Reply

CLAUDE = "anthropic/claude-opus-5-5"
CONV = "warm-test"


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def fm(server_json, monkeypatch, request):
    server = FakeModelServer([]).start()
    request.addfinalizer(server.stop)

    def cfg(**engine_) -> None:
        server_json({"claw": {"kind": "builtin"}, "engine": {"model": CLAUDE, "providers": {
            "anthropic": {"url": server.anthropic_url, "key": "test-key"}, "fake": {"api": "openai", "url": server.openai_url, "key_env": ""}},
            **engine_}})
    cfg(cache_warming=True)
    server.cfg = cfg
    monkeypatch.setattr(loop, "MODEL_RETRIES", (0, 0))
    fallback.reset()
    warming._slots.clear()  # noqa: SLF001
    yield server
    warming._slots.clear()  # noqa: SLF001
    fallback.reset()


def owner_turn(text: str = "good morning", **kw):
    spec = engine.Spec(**{"kind": "owner", "model": CLAUDE, "system": "You are Mousse, a fictional assistant.", "conversation": CONV,
                          "agent": "main", "origin": "user", "cache_ttl": "1h", **kw})
    return run(engine.run_turn(spec, MemorySession(), user(text)))


def test_off_by_default_and_only_the_users_own_claude_chats(fm):
    fm.cfg()  # 不写 engine.cache_warming = 关
    fm.push(Reply(text="hi"))
    owner_turn()
    assert warming._slots == {}  # noqa: SLF001
    fm.cfg(cache_warming=True)
    for kw in ({"kind": "outsider", "origin": "card"}, {"origin": "auto"}, {"origin": "api"}, {"cache_ttl": None}, {"json": True},
               {"model": "fake/m1"}):
        fm.push(Reply(text="{}" if kw.get("json") else "hi"))
        owner_turn(**kw)
        assert warming._slots == {}, kw  # noqa: SLF001
    fm.push(Reply(text="Good morning!"))
    owner_turn()
    assert list(warming._slots) == [CONV] and warming._slots[CONV].ref == CLAUDE  # noqa: SLF001
    assert [s["conversation"] for s in warming.status()] == [CONV]


def test_a_warm_replays_the_same_request_with_max_tokens_1(fm):
    fm.push(Reply(text="Good morning!"), Reply(text="x", usage={"in": 3, "out": 1, "cache_read": 4000, "cache_write": 0}))
    owner_turn()
    slot = warming._slots[CONV]  # noqa: SLF001
    assert run(warming.warm(CONV, slot)) is True
    first, again = (r["body"] for r in fm.of("anthropic"))
    assert again["max_tokens"] == 1 and first["max_tokens"] != 1
    assert {k: v for k, v in again.items() if k != "max_tokens"} == {k: v for k, v in first.items() if k != "max_tokens"}  # 前缀一个字不差
    assert json.dumps(first["system"]).count('"ttl": "1h"') == 1
    with store.read() as conn:
        row = conn.execute("SELECT * FROM engine_turns WHERE conversation=? ORDER BY rowid DESC LIMIT 1", (f"warm:{CONV}",)).fetchone()
    assert row["origin"] == "warming" and row["kind"] == "owner" and row["status"] == "done"
    u = json.loads(row["usage"])
    assert u["cache_read"] == 4000 and u["out"] == 1 and u["cost"] is not None
    assert usage.purpose_of("owner", "warming") == "warming" and "warming" in usage.PURPOSES
    assert any(p["purpose"] == "warming" for p in usage.summary(days=1)["byPurpose"])


def test_a_failed_warm_stops_warming_that_conversation(fm):
    fm.push(Reply(text="Good morning!"), Fail(400, "invalid request"))
    owner_turn()
    slot = warming._slots[CONV]  # noqa: SLF001
    slot.due = time.monotonic() - 1

    run(asyncio.wait_for(warming._run(), 5))  # noqa: SLF001 — 唯一的那个对话丢了，_run 自己就退出
    assert warming._slots == {}  # noqa: SLF001
    with store.read() as conn:
        row = conn.execute("SELECT status FROM engine_turns WHERE conversation=? ORDER BY rowid DESC LIMIT 1", (f"warm:{CONV}",)).fetchone()
    assert row["status"] == "error"


def test_stops_after_hours_and_skips_while_paused(fm, monkeypatch):
    fm.push(Reply(text="Good morning!"))
    owner_turn()
    slot = warming._slots[CONV]  # noqa: SLF001
    slot.due = time.monotonic() - 1
    monkeypatch.setitem(sys.modules, "engine.estop", types.SimpleNamespace(paused=lambda: True))  # 急停（第 4 批的）
    monkeypatch.setattr(engine, "estop", sys.modules["engine.estop"], raising=False)
    before = len(fm.of("anthropic"))

    async def go():
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(warming._run(), 0.5)  # noqa: SLF001
    run(go())
    assert len(fm.of("anthropic")) == before and CONV in warming._slots  # 停着：这一次跳过，对话留着  # noqa: SLF001
    slot.last = time.monotonic() - 3 * 3600  # 过了 hours（默认 2 小时）
    run(asyncio.wait_for(warming._run(), 3))  # noqa: SLF001
    assert warming._slots == {} and len(fm.of("anthropic")) == before  # noqa: SLF001


def test_skips_at_the_spend_cap_and_records_the_agent(fm):
    """花费上限（engine/spend.py）到了：这一次跳过、对话留着；没到时保温那一轮记着是哪个 Agent 的（按 Agent 的上限才算得到它）。"""
    fm.push(Reply(text="Good morning!"), Reply(text="x"))
    owner_turn()
    slot = warming._slots[CONV]  # noqa: SLF001
    assert slot.agent == "main"
    fm.cfg(cache_warming=True, spend={"purposes": {"warming": {"day": 0}}})
    slot.due = time.monotonic() - 1
    before = len(fm.of("anthropic"))

    async def go():
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(warming._run(), 0.5)  # noqa: SLF001
    run(go())
    assert len(fm.of("anthropic")) == before and CONV in warming._slots  # noqa: SLF001
    fm.cfg(cache_warming=True)
    assert run(warming.warm(CONV, slot)) is True
    with store.read() as conn:
        row = conn.execute("SELECT agent, origin FROM engine_turns WHERE conversation=? ORDER BY rowid DESC LIMIT 1", (f"warm:{CONV}",)).fetchone()
    assert row["agent"] == "main" and row["origin"] == "warming"


def test_the_warming_task_does_not_carry_the_turns_picker(fm, monkeypatch):
    seen = []

    async def fake_run():
        seen.append(fallback.current())
    monkeypatch.setattr(warming, "_run", fake_run)

    async def go():
        tok = fallback._current.set(fallback.Picker(engine.Spec(kind="owner", model=CLAUDE)))  # noqa: SLF001
        try:
            warming._start()  # noqa: SLF001
            await warming._task  # noqa: SLF001
        finally:
            fallback._current.reset(tok)  # noqa: SLF001
    run(go())
    assert seen == [None]
