"""未读：每个对话线程读到哪了、「今天」页的新卡片、收件箱待你点头的条数，以及 app 图标上的角标（推送时 push.py 也用它算 badge）。

- read_marks(thread, last_read, updated_at)：last_read = 读到的最后一条 messages.id。表第一次建的时候把已有的每个线程标成读到最新，
  老消息不会一下子全变未读；之后新开的线程没有这一行 = 从头算。
- 只算这些线程：main、每个 Agent（groups）、没归档的独立空间。学习台（study-*）、任务（task:*）之类不算。
- n = 读到的位置之后助手回了几条（role=grava）；mine = 其中回的是你自己发的话（messages.origin=user），定时器、收件箱触发的不算。
- 角标 badge = 收件箱待你点头（含 OpenClaw 执行审批）+ 各线程 mine 之和。
- 新卡片 feedNew：feed_items.seen_at 为空、没划掉的卡（app 用 /api/feed/seen 标成看过，见 data.py）。
- 执行审批的条数要起一次 openclaw CLI（一个 node 进程），这里走单独的 60 秒缓存，app 频繁轮询也不会每次都起。
"""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import inbox
from chat import _lock, now_iso
from data import ddb
from i18n import L
from push import preview

router = APIRouter()


def udb() -> sqlite3.Connection:
    """应用数据库（含 groups / side_chats / feed_items）+ read_marks。read_marks 第一次建的时候按现有记录把每个线程标成已读。"""
    conn = ddb()
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='read_marks'").fetchone():
        conn.execute("CREATE TABLE IF NOT EXISTS read_marks (thread TEXT PRIMARY KEY, last_read INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL)")
        conn.execute("INSERT OR IGNORE INTO read_marks(thread, last_read, updated_at) SELECT thread, MAX(id), ? FROM messages GROUP BY thread", (now_iso(),))
    return conn


def tracked(conn: sqlite3.Connection) -> list[str]:
    """算未读的线程：main、每个 Agent、没归档的独立空间。"""
    return (["main"] + [r["id"] for r in conn.execute("SELECT id FROM groups ORDER BY position, created_at")]
            + [r["id"] for r in conn.execute("SELECT id FROM side_chats WHERE archived=0 ORDER BY created_at")])


def counts(conn: sqlite3.Connection) -> dict[str, dict]:
    """有未读的线程：{thread: {n, mine, lastId}}。"""
    marks = {r["thread"]: r["last_read"] for r in conn.execute("SELECT thread, last_read FROM read_marks")}
    out = {}
    for th in tracked(conn):
        r = conn.execute("""SELECT COUNT(*) n, IFNULL(SUM(origin='user'), 0) mine, MAX(id) last FROM messages
            WHERE thread=? AND role='grava' AND id>?""", (th, marks.get(th) or 0)).fetchone()
        if r["n"]:
            out[th] = {"n": r["n"], "mine": r["mine"], "lastId": r["last"]}
    return out


async def summary() -> dict:
    with _lock, udb() as conn:
        threads = {}
        for th, c in counts(conn).items():
            last = conn.execute("SELECT id, text, ts, origin FROM messages WHERE id=?", (c["lastId"],)).fetchone()
            threads[th] = {"n": c["n"], "mine": c["mine"],
                           "last": {"id": f"db{last['id']}", "text": preview(last["text"]), "ts": last["ts"], "origin": last["origin"]} if last else None}
        feed_new = [r["id"] for r in conn.execute("SELECT id FROM feed_items WHERE seen_at IS NULL AND dismissed=0 ORDER BY created_at DESC LIMIT 50")]
    pending = await inbox.pending_count()
    return {"ok": True, "threads": threads, "feedNew": feed_new, "inbox": pending, "badge": pending + sum(t["mine"] for t in threads.values())}


async def badge() -> int:
    """app 图标上的数：收件箱待你点头 + 给你的未读回复。"""
    with _lock, udb() as conn:
        mine = sum(c["mine"] for c in counts(conn).values())
    return mine + await inbox.pending_count()


@router.get("/api/unread")
async def unread():
    """{threads: {<线程>: {n, mine, last: {id, text, ts, origin}}}（只列有未读的）, feedNew: [卡片 id], inbox: 待你点头, badge}。"""
    return await summary()


class ReadBody(BaseModel):
    thread: str
    upto: int | str | None = None  # 读到哪一条（db123 或 123）；不给 = 这个线程最新的一条


def msg_id(value: int | str | None) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, int):
        return value
    s = str(value).strip()
    s = s[2:] if s.startswith("db") else s
    if not s.isdigit():
        raise HTTPException(400, L("upto 是消息 id（db123 或 123）", "upto must be a message id (db123 or 123)"))
    return int(s)


@router.post("/api/unread/read")
async def mark_read(body: ReadBody):
    """标成已读到 upto（不给就是到最新）。只会往后挪，不会倒回去。返回和 GET /api/unread 一样的摘要。"""
    thread = body.thread.strip()
    if not thread:
        raise HTTPException(400, L("要给 thread", "thread is required"))
    upto = msg_id(body.upto)
    with _lock, udb() as conn:
        top = conn.execute("SELECT IFNULL(MAX(id), 0) FROM messages WHERE thread=?", (thread,)).fetchone()[0]
        mark = top if upto is None else min(upto, top)
        conn.execute("""INSERT INTO read_marks(thread, last_read, updated_at) VALUES(?,?,?)
            ON CONFLICT(thread) DO UPDATE SET last_read=MAX(last_read, excluded.last_read), updated_at=excluded.updated_at""",
                     (thread, mark, now_iso()))
    return await summary()
