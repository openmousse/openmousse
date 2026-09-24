"""推送通知（Expo Push → APNs）：回复在服务端生成完就推一条，锁屏也能看到，点开直接到那个对话。

- 手机端 app 启动并连上后把 Expo push token 交到 /api/push/register，存 grava.db 的 push_tokens。
- 回复完成（chat.py run_gateway 末尾）→ notify_reply()：标题是对话名，正文是回复的前 140 字，data.thread 给 app 跳转用。
  不管手机有没有在看：app 在前台且开着这个对话时，自己把通知压掉（见 src/api/push.ts）。
- 以后起床报告、ddl 提醒也走 send_push()。Expo 那边的 DeviceNotRegistered 会把 token 标为失效。
"""
from __future__ import annotations

import re
import sqlite3

import httpx
from fastapi import APIRouter
from pydantic import BaseModel

from chat import _lock, db, now_iso
from config import settings
from i18n import L

router = APIRouter()
EXPO_PUSH = "https://exp.host/--/api/v2/push/send"


def pdb() -> sqlite3.Connection:
    conn = db()
    conn.execute("""CREATE TABLE IF NOT EXISTS push_tokens (token TEXT PRIMARY KEY, platform TEXT, created_at TEXT NOT NULL,
        last_seen TEXT NOT NULL, disabled INTEGER NOT NULL DEFAULT 0, note TEXT)""")
    return conn


class Register(BaseModel):
    token: str
    platform: str = "ios"


@router.post("/api/push/register")
def register(body: Register):
    tok = body.token.strip()
    if not (tok.startswith("ExponentPushToken[") or tok.startswith("ExpoPushToken[")):
        return {"ok": False, "error": L("不是 Expo push token", "Not an Expo push token")}
    ts = now_iso()
    with _lock, pdb() as conn:
        conn.execute("""INSERT INTO push_tokens(token, platform, created_at, last_seen, disabled) VALUES(?,?,?,?,0)
            ON CONFLICT(token) DO UPDATE SET last_seen=excluded.last_seen, disabled=0, platform=excluded.platform""", (tok, body.platform, ts, ts))
    return {"ok": True}


@router.get("/api/push/status")
def status():
    with _lock, pdb() as conn:
        rows = conn.execute("SELECT platform, last_seen, disabled, note FROM push_tokens ORDER BY last_seen DESC").fetchall()
    return {"ok": True, "devices": [{"platform": r["platform"], "lastSeen": r["last_seen"], "disabled": bool(r["disabled"]), "note": r["note"]} for r in rows]}


class TestBody(BaseModel):
    title: str = ""
    body: str = ""  # 空 = 默认的测试文字（按请求语言）


@router.post("/api/push/test")
async def test(body: TestBody):
    text = body.body or L("测试推送：收到就说明通了。", "Test notification: if you can see this, push works.")
    return await send_push(body.title or settings.app_name, text, {"thread": "main"})


class SendBody(BaseModel):
    title: str
    body: str
    thread: str = "today"   # 点开去哪：某个线程 id，或 today（「今天」页）
    thread_id: str | None = None  # iOS 通知分组


@router.post("/api/push/send")
async def send(body: SendBody):
    """系统主动推一条（起床报告、ddl 提醒等，suggestion_watcher 用）。"""
    return await send_push(body.title, body.body, {"thread": body.thread}, thread_id=body.thread_id or body.thread)


def active_tokens() -> list[str]:
    with _lock, pdb() as conn:
        return [r["token"] for r in conn.execute("SELECT token FROM push_tokens WHERE disabled=0")]


async def send_push(title: str, body: str, data: dict | None = None, thread_id: str | None = None) -> dict:
    tokens = active_tokens()
    if not tokens:
        return {"ok": False, "sent": 0, "error": L("没有注册的设备", "No registered devices")}
    msgs = [{"to": t, "title": title, "body": body[:180], "data": data or {}, "sound": "default", "priority": "high",
             **({"threadId": thread_id} if thread_id else {})} for t in tokens]
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(EXPO_PUSH, json=msgs, headers={"Accept": "application/json", "Content-Type": "application/json"})
        res = r.json()
    except (httpx.HTTPError, ValueError) as exc:
        return {"ok": False, "sent": 0, "error": str(exc)[:200]}
    tickets = res.get("data") or []
    bad = []
    for tok, tk in zip(tokens, tickets):
        if tk.get("status") == "error":
            err = (tk.get("details") or {}).get("error") or tk.get("message")
            if err == "DeviceNotRegistered":
                bad.append((tok, err))
    if bad:
        with _lock, pdb() as conn:
            for tok, err in bad:
                conn.execute("UPDATE push_tokens SET disabled=1, note=? WHERE token=?", (err, tok))
    return {"ok": True, "sent": sum(1 for t in tickets if t.get("status") == "ok"), "errors": [t for t in tickets if t.get("status") == "error"][:3]}


def thread_title(thread: str) -> str:
    if thread == "main":
        return settings.app_name
    with _lock, db() as conn:
        try:
            r = conn.execute("SELECT name FROM groups WHERE id=?", (thread,)).fetchone()
            if r:
                return f"{settings.app_name} · {r['name']}"
            r = conn.execute("SELECT title FROM side_chats WHERE id=?", (thread,)).fetchone()
            if r:
                return f"{settings.app_name} · {r['title']}"
        except sqlite3.Error:
            pass
    return settings.app_name


def preview(text: str) -> str:
    t = re.sub(r"[*_`#>]+", "", text or "").strip()
    t = re.sub(r"\s+", " ", t)
    return t[:140] + ("…" if len(t) > 140 else "")


async def notify_reply(thread: str, text: str, status: str) -> None:
    body = preview(text) if status == "ok" else L("这条没回成，点开看看。", "This reply didn't go through. Tap to take a look.")
    try:
        await send_push(thread_title(thread), body or L("回复好了。", "Reply ready."), {"thread": thread}, thread_id=thread)
    except Exception:  # noqa: BLE001 — 推送失败不影响回复本身
        pass
