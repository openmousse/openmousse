"""事件流：一轮（engine_turns）和每一步（engine_steps）的记录，外加进程里的实时广播（subscribe / publish）。

记什么：谁（回合类型、Agent、模型）、什么时候、多久、状态、用量和花费、工具名和一句标题。
不记什么：对方说的话、工具的参数和输出（外人回合的原话已经在名片记录里，工具输出在会话树里，这里只给界面和账本用）。
app 的进度条（第 3 项）走的是一轮自己的 on_event（loop.py 开头那些事件，chat.note_engine 画成 run.progress），断线重连由 chat.attach 补发；
这张表管账、管重启后收尾（recover：上次停机时还在跑的标成 aborted），以后的任务台账（第 5 项，engine/ledger.py）也从这里出。
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime, timedelta

from config import settings

from . import store
from .errors import scrub

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


def turn_close(tid: str, *, conversation: str, status: str, usage: dict | None = None, error: str | None = None,
               models: list[str] | None = None) -> None:
    """models：这一轮真的调过的模型（第 13 步的备用链换过就不止一个；engine_turns.model 是一开始要的那个）。"""
    ms = int((time.monotonic() - _t0.pop(tid, time.monotonic())) * 1000)
    steps = _seq.pop(tid, 0)
    with store.tx() as conn:  # 报错原文里像密钥的串进库之前抹掉（errors.scrub；DeepSeek 的 401 带着密钥的末 4 位）
        conn.execute("UPDATE engine_turns SET status=?, error=?, steps=?, usage=?, ended_at=?, ms=?, models=? WHERE id=?",
                     (status, (error or None) and scrub(str(error))[:300], steps, json.dumps(usage or {}), store.now_iso(), ms,
                      json.dumps(models) if models else None, tid))
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
    if data and isinstance(data.get("error"), str):  # 模型那一步的报错原文：像密钥的串进库之前抹掉
        data = {**data, "error": scrub(data["error"])}
    with store.tx() as conn:
        conn.execute("UPDATE engine_steps SET status=?, data=?, ended_at=?, ms=? WHERE id=?",
                     (status, json.dumps(data or {}, ensure_ascii=False), store.now_iso(), ms, sid))
    if conversation:
        publish(conversation, {"type": "step", "turn": turn, "step": sid, "status": status, "ms": ms})


def recover() -> list[dict]:
    """服务启动时：上次停机时还在跑的回合和步骤（status running）都标成 aborted（error = server restarted）。
    → 这些回合 [{id, conversation, session, kind, agent, model, origin, started_at}]（loop.recover 接着把会话补完整）。
    从没用过内核（库里没有这几张表）就什么都不做，也不建表。"""
    if not store.exists():
        return []
    now = store.now_iso()
    with store.tx() as conn:
        rows = [dict(r) for r in conn.execute("SELECT id, conversation, session, kind, agent, model, origin, started_at FROM engine_turns "
                                              "WHERE status='running' ORDER BY julianday(started_at), rowid")]
        conn.execute("UPDATE engine_steps SET status='aborted', ended_at=? WHERE status='running'", (now,))
        for r in rows:
            n = conn.execute("SELECT IFNULL(MAX(seq), 0) FROM engine_steps WHERE turn=?", (r["id"],)).fetchone()[0]
            conn.execute("UPDATE engine_turns SET status='aborted', error='server restarted', steps=?, ended_at=? WHERE id=?",
                         (n, now, r["id"]))
    for r in rows:
        publish(r["conversation"], {"type": "turn", "turn": r["id"], "status": "aborted"})
    return rows


CANCELLED, CHECKED = "cancelled", "cancelled; checked after restart"


def cancelled(hours: float = 72) -> list[dict]:
    """被 asyncio 取消的回合（外面取消了这一轮：关服务时没来得及停、点了停又 STOP_GRACE 秒没收尾的硬停）：内核当场把会话补完整、
    记成 aborted（error = cancelled），所以重启后它们不是 running。→ 最近 hours 小时里这样结束、还没看过的回合（chat.resume_engine
    看 app 那边那一句拿到回复没有，看完 checked 记一笔，每个只看一次）。没用过内核 → []。"""
    if not store.exists():
        return []
    since = (datetime.now(settings.tz) - timedelta(hours=hours)).isoformat(timespec="milliseconds")
    with store.read() as conn:
        return [dict(r) for r in conn.execute("SELECT id, conversation, session, kind, agent, model, origin, started_at FROM engine_turns "
                                              "WHERE status='aborted' AND error=? AND julianday(ended_at)>=julianday(?) "
                                              "ORDER BY julianday(started_at), rowid", (CANCELLED, since))]


def checked(ids: list[str]) -> None:
    """这几轮 cancelled 的重启后看过了（resume_engine）：下次启动不再看（免得之后删了回复，又被补一条「没拿到回复」）。"""
    if not ids:
        return
    with store.tx() as conn:
        conn.executemany("UPDATE engine_turns SET error=? WHERE id=? AND error=?", [(CHECKED, i, CANCELLED) for i in ids])


def turns(conversation: str | None = None, limit: int = 20) -> list[dict]:
    """最近的几轮，新的在前。按真时间排（julianday：时区改过的话 started_at 的偏移不一样，按字符串排会乱，见 store.py 开头）；
    按对话取的走 (conversation, julianday(started_at)) 的索引，不用另外排序。"""
    q = "SELECT * FROM engine_turns" + (" WHERE conversation=?" if conversation else "") + " ORDER BY julianday(started_at) DESC, rowid DESC LIMIT ?"
    with store.read() as conn:
        rows = conn.execute(q, ((conversation, limit) if conversation else (limit,))).fetchall()
    return [{**dict(r), "usage": json.loads(r["usage"] or "{}"), "models": json.loads(r["models"] or "[]")} for r in rows]


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
