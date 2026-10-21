"""连接器「先问我」卡的过期（apps.ask）：默认 24 小时（apps.ask_ttl_hours 可改）。
过了收件箱标成 expired，再批会被拒；点头时再对一次时间，过了不去调对方。只动这一种卡。
推送是假的，数据都是编的。
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException

import apps
import inbox
from config import TZ, settings


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def quiet(monkeypatch):
    import push

    async def fake_send(*a, **kw):
        return {"ok": True}

    monkeypatch.setattr(push, "send_push", fake_send)
    monkeypatch.setattr(inbox, "kick", lambda thread, text: "started")


def seed(tmp_path, monkeypatch) -> dict:
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    app = {"id": "notes", "name": "Notes", "url": "http://127.0.0.1:9/mcp", "auth": "none", "status": "connected",
           "agents": ["main"], "policy": {"read": "auto", "write": "ask"}, "tools": [
               {"name": "write", "title": "Write", "description": "add a note", "kind": "write", "inputSchema": {"type": "object"}}]}
    apps.put(app)
    return app


def tool(app: dict) -> dict:
    return app["tools"][0]


def hours_left(iid: str) -> float:
    exp = inbox.parse_time(inbox.item(iid)["expiresAt"])
    assert exp is not None
    return (exp - datetime.now(TZ)).total_seconds() / 3600


def test_a_new_card_expires_in_a_day_unless_configured(quiet, tmp_path, monkeypatch, server_json):
    app = seed(tmp_path, monkeypatch)
    server_json({})  # 接的是 OpenClaw（claw 没写）：卡照旧不过期
    text = run(apps.ask(app, tool(app), {"text": "zero"}, "main"))
    iid = text.split("inbox card ")[1].split(")")[0]
    assert inbox.item(iid)["expiresAt"] is None and inbox.item(iid)["status"] == "pending"
    server_json({"claw": {"kind": "builtin"}})
    text = run(apps.ask(app, tool(app), {"text": "one"}, "main"))
    iid = text.split("inbox card ")[1].split(")")[0]
    assert 23.9 < hours_left(iid) < 24.1
    assert inbox.item(iid)["status"] == "pending" and inbox.item(iid)["kind"] == "app"

    server_json({"claw": {"kind": "builtin"}, "apps": {"ask_ttl_hours": 2}})
    text = run(apps.ask(app, tool(app), {"text": "two"}, "main"))
    iid = text.split("inbox card ")[1].split(")")[0]
    assert 1.9 < hours_left(iid) < 2.1

    for bad in (0, -1, "no"):
        server_json({"apps": {"ask_ttl_hours": bad}})
        assert apps.ask_ttl() == 24 * 3600


def test_an_expired_card_cannot_be_approved_and_the_hook_checks_again(quiet, tmp_path, monkeypatch, server_json):
    app = seed(tmp_path, monkeypatch)
    server_json({"claw": {"kind": "builtin"}})
    called: list[str] = []

    async def nope(*a, **k):
        called.append("call")
        return "should-not-run", False

    monkeypatch.setattr(apps, "call", nope)
    text = run(apps.ask(app, tool(app), {"text": "late"}, "main"))
    iid = text.split("inbox card ")[1].split(")")[0]
    past = (datetime.now(TZ) - timedelta(hours=1)).isoformat(timespec="seconds")
    with inbox.idb() as conn:
        conn.execute("UPDATE inbox SET expires_at=? WHERE id=?", (past, iid))

    with pytest.raises(HTTPException) as caught:
        run(inbox.act(iid, inbox.ActIn(action="approve")))
    assert caught.value.status_code == 409
    assert inbox.item(iid)["status"] == "expired"
    assert called == []

    fresh = run(apps.ask(app, tool(app), {"text": "still-good"}, "main"))
    iid2 = fresh.split("inbox card ")[1].split(")")[0]
    it = inbox.item(iid2)
    it["expiresAt"] = past  # 库里还没过期，点头时手里这张已经过了
    done = run(apps.on_decided(it, "approve"))
    assert done and done.get("failed") and "expired" in done["failed"].lower()
    assert called == []

    live = inbox.item(iid2)
    done = run(apps.on_decided(live, "approve"))
    assert done and "should-not-run" not in (done.get("failed") or "")
    assert called == ["call"] and "should-not-run" in done["result"]
