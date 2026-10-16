"""事件流：一轮（engine_turns）和每一步（engine_steps）的记录，外加进程里的实时广播（以后 app 的进度条从这里接，第 3 步）。

记什么：谁（回合类型、Agent、模型）、什么时候、多久、状态、用量和花费、工具名和一句标题。
不记什么：对方说的话、工具的参数和输出（外人回合的原话已经在名片记录里，工具输出在会话树里，这里只给界面和账本用）。
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid

from . import store

_t0: dict[str, float] = {}      # 一轮 / 一步开始时的 monotonic 时间，算 ms 用
_seq: dict[str, int] = {}       # 每一轮下一步的序号
_subs: dict[str, set[asyncio.Queue]] = {}


def turn_open(*, conversation: str, kind: str, model: str, session: str | None = None, agent: str | None = None,
              origin: str | None = None) -> str:
    tid = "t_" + uuid.uuid4().hex[:16]
    with store.tx() as conn:
        conn.execute("INSERT INTO engine_turns(id, conversation, session, kind, agent, model, origin, status, started_at) VALUES(?,?,?,?,?,?,?,?,?)",
                     (tid, conversation, session, kind, agent, model, origin, "running", store.now_iso()))
    _t0[tid] = time.monotonic()
    _seq[tid] = 0
    publish(conversation, {"type": "turn", "turn": tid, "status": "running", "kind": kind, "model": model})
    return tid


def turn_close(tid: str, *, conversation: str, status: str, usage: dict | None = None, error: str | None = None) -> None:
    ms = int((time.monotonic() - _t0.pop(tid, time.monotonic())) * 1000)
    steps = _seq.pop(tid, 0)
    with store.tx() as conn:
        conn.execute("UPDATE engine_turns SET status=?, error=?, steps=?, usage=?, ended_at=?, ms=? WHERE id=?",
                     (status, (error or None) and str(error)[:300], steps, json.dumps(usage or {}), store.now_iso(), ms, tid))
    publish(conversation, {"type": "turn", "turn": tid, "status": status, "ms": ms})


def step_open(tid: str, *, type_: str, name: str | None = None, detail: str | None = None, conversation: str | None = None) -> int:
    _seq[tid] = _seq.get(tid, 0) + 1
    with store.tx() as conn:
        cur = conn.execute("INSERT INTO engine_steps(turn, seq, type, name, detail, status, started_at) VALUES(?,?,?,?,?,?,?)",
                           (tid, _seq[tid], type_, name, (detail or None) and str(detail)[:160], "running", store.now_iso()))
        sid = int(cur.lastrowid or 0)
    _t0[f"s{sid}"] = time.monotonic()
    if conversation:
        publish(conversation, {"type": "step", "turn": tid, "step": sid, "kind": type_, "name": name, "detail": detail, "status": "running"})
    return sid


def step_close(sid: int, *, status: str, data: dict | None = None, conversation: str | None = None, turn: str | None = None) -> None:
    ms = int((time.monotonic() - _t0.pop(f"s{sid}", time.monotonic())) * 1000)
    with store.tx() as conn:
        conn.execute("UPDATE engine_steps SET status=?, data=?, ended_at=?, ms=? WHERE id=?",
                     (status, json.dumps(data or {}, ensure_ascii=False), store.now_iso(), ms, sid))
    if conversation:
        publish(conversation, {"type": "step", "turn": turn, "step": sid, "status": status, "ms": ms})


def turns(conversation: str | None = None, limit: int = 20) -> list[dict]:
    q = "SELECT * FROM engine_turns" + (" WHERE conversation=?" if conversation else "") + " ORDER BY started_at DESC LIMIT ?"
    with store.read() as conn:
        rows = conn.execute(q, ((conversation, limit) if conversation else (limit,))).fetchall()
    return [{**dict(r), "usage": json.loads(r["usage"] or "{}")} for r in rows]


def steps(tid: str) -> list[dict]:
    with store.read() as conn:
        rows = conn.execute("SELECT * FROM engine_steps WHERE turn=? ORDER BY seq", (tid,)).fetchall()
    return [{**dict(r), "data": json.loads(r["data"] or "{}")} for r in rows]


# —— 实时广播（进程内）：subscribe(对话) 拿一个队列，publish 往这个对话的所有队列里放 ————————————————

def subscribe(conversation: str) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=500)
    _subs.setdefault(conversation, set()).add(q)
    return q


def unsubscribe(conversation: str, q: asyncio.Queue) -> None:
    s = _subs.get(conversation)
    if s:
        s.discard(q)
        if not s:
            _subs.pop(conversation, None)


def publish(conversation: str, event: dict) -> None:
    for q in list(_subs.get(conversation, ())):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:  # 没人读的队列别撑爆内存：丢掉这一条
            pass
