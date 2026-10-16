"""内核的四张表（都在 mousse.db 里，和 app 的对话记录同一个库）：

  engine_sessions  会话：一条会话树。key = mousse:<对话>:<逻辑日> 这类（沿用 claw.py 的写法，每天 04:00 换新的）；
                   system = 开头定死的规矩（之后的变化往后补成 role=system 的条目，不回头改开头：省缓存，也是 Claude 的硬规矩）；
                   leaf = 现在的叶子（撤回 = 换叶子，旧的枝还在）
  engine_entries   会话里的一条：parent 指上一条；kind = message（中立格式的消息：user / assistant / tool / system）
                   或者以后的 compaction（压缩成的摘要）、model_change、label 这些不是消息的条目（借 pi 的会话格式，第 4 项用）
  engine_turns     一轮：从一句话进来到模型说完（中间可能调好多次模型和工具）
  engine_steps     每一步：一次模型调用、一次工具调用、一次插话
对话 → 一轮 → 每一步三层就是 harness 方案里的「一条事件流」：以后 app 的进度、任务卡、后台回报都从这里出（第 3 步）。
"""
from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime

from config import settings

_lock = threading.Lock()
_ready: set[str] = set()

SCHEMA = """
CREATE TABLE IF NOT EXISTS engine_sessions (
  id TEXT PRIMARY KEY, key TEXT NOT NULL, kind TEXT NOT NULL, agent TEXT, model TEXT,
  system TEXT NOT NULL DEFAULT '', leaf TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS engine_sessions_key ON engine_sessions(key, created_at);
CREATE TABLE IF NOT EXISTS engine_entries (
  id TEXT PRIMARY KEY, session TEXT NOT NULL, parent TEXT, kind TEXT NOT NULL DEFAULT 'message', role TEXT NOT NULL, data TEXT NOT NULL,
  turn TEXT, ts TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS engine_entries_session ON engine_entries(session, parent);
CREATE TABLE IF NOT EXISTS engine_turns (
  id TEXT PRIMARY KEY, conversation TEXT NOT NULL, session TEXT, kind TEXT NOT NULL, agent TEXT, model TEXT NOT NULL,
  origin TEXT, status TEXT NOT NULL, error TEXT, steps INTEGER NOT NULL DEFAULT 0, usage TEXT,
  started_at TEXT NOT NULL, ended_at TEXT, ms INTEGER);
CREATE INDEX IF NOT EXISTS engine_turns_conv ON engine_turns(conversation, started_at);
CREATE TABLE IF NOT EXISTS engine_steps (
  id INTEGER PRIMARY KEY AUTOINCREMENT, turn TEXT NOT NULL, seq INTEGER NOT NULL, type TEXT NOT NULL, name TEXT, detail TEXT,
  status TEXT NOT NULL, data TEXT, started_at TEXT NOT NULL, ended_at TEXT, ms INTEGER);
CREATE INDEX IF NOT EXISTS engine_steps_turn ON engine_steps(turn, seq);
"""


def connect() -> sqlite3.Connection:
    path = settings.db
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    if str(path) not in _ready:
        conn.executescript(SCHEMA)
        if "kind" not in {r[1] for r in conn.execute("PRAGMA table_info(engine_entries)")}:  # 10/16 早期测试库：补上这一栏
            conn.execute("ALTER TABLE engine_entries ADD COLUMN kind TEXT NOT NULL DEFAULT 'message'")
            conn.commit()
        _ready.add(str(path))
    return conn


@contextmanager
def tx() -> Iterator[sqlite3.Connection]:
    """一次写：拿锁、开连接、成功就提交、出错回滚，最后关掉连接。"""
    with _lock:
        conn = connect()
        try:
            with conn:
                yield conn
        finally:
            conn.close()


@contextmanager
def read() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


def now_iso() -> str:
    return datetime.now(settings.tz).isoformat(timespec="milliseconds")
