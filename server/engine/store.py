"""内核的几张表（都在 mousse.db 里，和 app 的对话记录同一个库）：

  engine_sessions  会话：一条会话树。key = mousse:<对话>:<逻辑日> 这类（沿用 claw.py 的写法，每天 04:00 换新的）；
                   system = 开头定死的规矩（之后的变化往后补成 role=system 的条目，不回头改开头：省缓存，也是 Claude 的硬规矩）；
                   leaf = 现在的叶子（撤回 = 换叶子，旧的枝还在）
  engine_entries   会话里的一条：parent 指上一条；kind = message（中立格式的消息：user / assistant / tool / system）
                   或者以后的 compaction（压缩成的摘要）、model_change、label 这些不是消息的条目（借 pi 的会话格式，第 4 项用）
  engine_turns     一轮：从一句话进来到模型说完（中间可能调好多次模型和工具）；model = 一开始要的模型，models = 真的调过的
                   （JSON 列表，第 13 步的备用链换过模型就不止一个）
  engine_steps     每一步：一次模型调用、一次工具调用、一次插话
  engine_calls     等用户点头的工具调用（第 2 项的关卡，engine/tools.py）：哪个工具、原样的参数、谁、在哪个对话、收件箱卡 id、
                   卡上内容的指纹；同意了照原样跑一次，结果记在这里（pending → running → done / failed，或 rejected / withdrawn / revised）
  engine_tasks     后台任务的台账（第 5 项，engine/ledger.py 读写、engine/tasks.py 跑）：谁派的、哪个对话、标题和说明、谁来跑（内核 /
                   Claude Code / Codex）、状态、开始结束、用量、结果、子会话、做完的结果交回去没有。先记一行再开跑（pi：先记意图再执行）
  engine_todos     待办清单（⑫b，engine/todo.py）：一个对话一行（app 的对话；后台任务的子 Agent 是 task:<id>），整张清单的 JSON
  engine_plan      计划模式（⑫b，engine/plan.py）：对话 + 逻辑日一行，开没开、谁开关的；没有这一行 = 照 server.json 的默认
  engine_memory    记性的闸（13b，engine/memgate.py）：每一次「记」的决定一行：哪种记忆、什么动作、怎么办（直接写 / 提议 / 待定 / 不做）、
                   动的是哪一个、谁写的、内容是不是别人的话、为什么、结果；不记记忆的内容
  engine_writeback 对话结束时的写回（13b，engine/writeback.py）：一条会话一行：写回到哪一条了、状态、列了哪些候选、选了哪几条长成叶子、
                   没选的和为什么（留 WRITEBACK_KEEP_DAYS 天）；没写成的：连着失败了几次（tries）、失败的那一段到哪一条（tried）、
                   退避到什么时候（retry_at）
对话 → 一轮 → 每一步三层就是 harness 方案里的「一条事件流」：以后 app 的进度、任务卡、后台回报都从这里出（第 3 步）。
时间（started_at、ended_at……）存的是带时区偏移的 ISO 字符串（now_iso）。server.json 的 timezone 改过，新旧两种偏移就混在一起：
按时间比、按时间排一律用 julianday(列)（SQLite 自己解开偏移再比），不按字符串；engine_turns 的索引也建在 julianday(started_at) 上。
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
  started_at TEXT NOT NULL, ended_at TEXT, ms INTEGER, models TEXT);
DROP INDEX IF EXISTS engine_turns_conv;
CREATE INDEX IF NOT EXISTS engine_turns_conv_at ON engine_turns(conversation, julianday(started_at));
CREATE TABLE IF NOT EXISTS engine_steps (
  id INTEGER PRIMARY KEY AUTOINCREMENT, turn TEXT NOT NULL, seq INTEGER NOT NULL, type TEXT NOT NULL, name TEXT, detail TEXT,
  status TEXT NOT NULL, data TEXT, started_at TEXT NOT NULL, ended_at TEXT, ms INTEGER);
CREATE INDEX IF NOT EXISTS engine_steps_turn ON engine_steps(turn, seq);
CREATE TABLE IF NOT EXISTS engine_calls (
  id TEXT PRIMARY KEY, inbox TEXT, dedupe TEXT NOT NULL, tool TEXT NOT NULL, args TEXT NOT NULL, input TEXT, agent TEXT NOT NULL,
  thread TEXT NOT NULL, turn TEXT, call TEXT, card TEXT NOT NULL, status TEXT NOT NULL, result TEXT, created_at TEXT NOT NULL,
  decided_at TEXT, kind TEXT, origin TEXT);
CREATE INDEX IF NOT EXISTS engine_calls_inbox ON engine_calls(inbox);
CREATE TABLE IF NOT EXISTS engine_tasks (
  id TEXT PRIMARY KEY, thread TEXT NOT NULL, agent TEXT NOT NULL, parent TEXT, depth INTEGER NOT NULL DEFAULT 1, turn TEXT, call TEXT,
  label TEXT NOT NULL DEFAULT '', task TEXT NOT NULL, runner TEXT NOT NULL DEFAULT 'engine', model TEXT, level TEXT NOT NULL DEFAULT 'undo',
  timeout_s INTEGER NOT NULL, status TEXT NOT NULL, created_at INTEGER NOT NULL, started_at INTEGER, ended_at INTEGER, session TEXT,
  child_turn TEXT, tools INTEGER NOT NULL DEFAULT 0, last_tool TEXT, last_title TEXT, usage TEXT, result TEXT, error TEXT,
  delivery TEXT NOT NULL DEFAULT 'none', delivered_at INTEGER, attempts INTEGER NOT NULL DEFAULT 0, retry_of TEXT, inbox TEXT, data TEXT);
CREATE INDEX IF NOT EXISTS engine_tasks_thread ON engine_tasks(thread, created_at);
CREATE INDEX IF NOT EXISTS engine_tasks_status ON engine_tasks(status, created_at);
CREATE INDEX IF NOT EXISTS engine_tasks_parent ON engine_tasks(parent);
CREATE TABLE IF NOT EXISTS engine_todos (
  conversation TEXT PRIMARY KEY, items TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0, turn TEXT, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS engine_plan (
  conversation TEXT NOT NULL, day TEXT NOT NULL, active INTEGER NOT NULL, by TEXT NOT NULL, at TEXT NOT NULL,
  PRIMARY KEY(conversation, day));
CREATE TABLE IF NOT EXISTS engine_memory (
  id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, turn TEXT, conversation TEXT, agent TEXT, kind TEXT NOT NULL, op TEXT NOT NULL,
  action TEXT NOT NULL, who TEXT NOT NULL, outside INTEGER NOT NULL DEFAULT 0, target TEXT NOT NULL DEFAULT '', author TEXT NOT NULL DEFAULT '',
  why TEXT NOT NULL DEFAULT '', result TEXT NOT NULL DEFAULT '', ok INTEGER NOT NULL DEFAULT 1);
CREATE INDEX IF NOT EXISTS engine_memory_conv ON engine_memory(conversation, id);
CREATE INDEX IF NOT EXISTS engine_memory_kind ON engine_memory(kind, id);
CREATE TABLE IF NOT EXISTS engine_writeback (
  session TEXT PRIMARY KEY, conversation TEXT NOT NULL, agent TEXT NOT NULL, upto TEXT, status TEXT NOT NULL, reason TEXT,
  candidates TEXT, picked TEXT, skipped TEXT, turn TEXT, error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  tries INTEGER NOT NULL DEFAULT 0, tried TEXT, retry_at TEXT);
CREATE INDEX IF NOT EXISTS engine_writeback_conv ON engine_writeback(conversation, updated_at);
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
        if "models" not in {r[1] for r in conn.execute("PRAGMA table_info(engine_turns)")}:  # 第 13 步以前建的库：补上这一栏
            conn.execute("ALTER TABLE engine_turns ADD COLUMN models TEXT")
            conn.commit()
        if "tries" not in {r[1] for r in conn.execute("PRAGMA table_info(engine_writeback)")}:  # 写回有退避以前（10/19）建的库：补上这几栏
            for col in ("tries INTEGER NOT NULL DEFAULT 0", "tried TEXT", "retry_at TEXT"):
                conn.execute(f"ALTER TABLE engine_writeback ADD COLUMN {col}")
            conn.commit()
        if "origin" not in {r[1] for r in conn.execute("PRAGMA table_info(engine_calls)")}:  # 10/21 以前建的库：补上交卡那一轮的回合类型和来路
            for col in ("kind TEXT", "origin TEXT"):
                conn.execute(f"ALTER TABLE engine_calls ADD COLUMN {col}")
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


def exists() -> bool:
    """库里已经有内核的表了吗（用过内核）。只读地看一眼，不建表：服务启动时的收尾（events.recover）没用过内核就什么都不碰。"""
    path = settings.db
    if str(path) in _ready:
        return True
    if not path.exists():
        return False
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    except sqlite3.Error:
        return False
    try:
        return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='engine_turns'").fetchone() is not None
    except sqlite3.Error:
        return False
    finally:
        conn.close()


def now_iso() -> str:
    return datetime.now(settings.tz).isoformat(timespec="milliseconds")
