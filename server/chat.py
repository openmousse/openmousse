"""Grava API 第二档：真实对话。

- 转发到 Gateway 自带的 OpenAI 兼容接口（只在 loopback），每个线程一个显式 session key。
- 主对话用 `agent:main:main`，和 Telegram 共用同一个会话与记忆；Group 与独立空间用 `agent:main:grava:<id>`
  （Group 还没拆成独立 agent 之前，暂借 main 的记忆）。
- 模型切换用 `x-openclaw-model` 做单次覆盖，不改 agent 默认值。
- 显示用的对话记录存在 Grava 自己的 SQLite（L6 会话记录的 app 侧副本）；模型上下文由 Gateway 会话维护。
- 长按消息：删除只动 app 侧记录；撤回 / 重新编辑用 Gateway 的 `sessions.rewind` 把会话退回到那条消息之前，
  模型上下文里也一起去掉（工具已经做过的事、写过的文件不会回滚）。
- 执行和连接分开：一次回复在服务端后台跑到底并入库，手机切后台、断网都不会丢；客户端随时用 /api/chat/stream 重新接上。
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import shutil
import signal
import sqlite3
import sys
import threading
import time
from contextvars import ContextVar
from datetime import datetime, timedelta
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import claw  # noqa: E402
from config import TZ, raw, settings, user_word  # noqa: E402
from i18n import L, LS  # noqa: E402

OPENCLAW = settings.openclaw_json
DB = settings.db
GATEWAY = settings.gateway
DEFAULT_MODEL = settings.default_model
# 这个请求是经谁来的（main.guard 按 X-Mousse-Via 设；脚本经 bridges.run 跑时带上 MOUSSE_VIA）：外面的 MCP 客户端 = mcp，内核工具跑的脚本 =
# 那一轮的来路。begin() 记进 Run.via，自己的内核据此不让 /mcp 和后台引出来的回合花 ChatGPT 套餐（engine/models.may_spend）
REQUEST_VIA: ContextVar[str | None] = ContextVar("mousse_via", default=None)
router = APIRouter()
_lock = threading.Lock()
_at_index: set[str] = set()  # 建过 messages 时间索引的库（见 db）


@dataclass
class Run:
    """一次正在进行的回复。与 HTTP 连接无关，客户端断了它照样跑完。"""
    thread: str
    model: str
    user_id: int
    started: str
    key: str | None = None  # 显式的 session key（给子会话发修改意见时用），默认按 thread 推
    text: str = ""
    done: bool = False
    status: str = "ok"
    error: str | None = None
    reply_id: int | None = None
    finished: str | None = None
    requested: str | None = None
    t0: float = field(default_factory=time.time)
    queues: list[asyncio.Queue] = field(default_factory=list)
    notify: bool = True  # 旧开关：False = 回完不推（= level none；学习台、主对话转来的问题用它）
    origin: str = "user"  # user：用户发的；auto：定时器 / 收件箱之类系统触发；relay：主对话转来。存进回复那一行的 origin
    level: str | None = None  # 推送档位 ring / quiet / none；None = 按 origin 定（见 push.run_level）
    feed_mark: int = 0  # 开跑时 feed_items 的最大 rowid：回完看这之后这个线程有没有写新卡，有就推卡片
    digest: bool = False  # 这一轮的回复是日结（别的 claw）：回完存进 <data_dir>/digest/，明天第一句话前面带上
    inbox_mark: int = 0  # 开跑时 inbox 的最大 rowid：回完把这之后这个线程新交的收件箱条目挂到这条回复下（created_at 只到秒，不够准）
    handoff_mark: int = 0  # 开跑时 handoffs 的最大 rowid：回完把这之后从这个线程转出去的挂到这条回复下（见 cards.py）
    cards: dict = field(default_factory=dict)  # 这次回复里出的转交卡、任务卡（id → 最新的样子）：客户端重新接上时补发（见 cards.py）
    stopping: bool = False  # 用户点了「停」（/api/chat/stop）：断开到 Gateway 的连接，Gateway 就中止这一轮
    stream_task: asyncio.Task | None = None  # 流式读 Gateway 的那一段（停的时候取消它）
    gw_run: str | None = None  # 走 WebSocket 对话通道时 Gateway 给这一轮的 runId（停、插话、断线后接着收都靠它）
    progress: dict | None = None  # 准备阶段、工具步骤、最近的思考摘要（对话通道：note_progress；自己的内核：note_engine），客户端重新接上时补发
    control: Any = None  # 自己的内核（builtin）跑的这一轮：engine.Control（插话 steer、停 abort 都经它）
    steered: dict = field(default_factory=dict)  # builtin：插进这一轮、还没被它读到的话（app 消息 id → Queued）；没赶上的回完变成下一轮
    thought: str = ""  # builtin：这次调模型到现在的思考（progress 的 thought 取它的最后一段）
    progress_at: float = 0.0  # builtin：上次推 progress 的时间（思考一个字一个字来，最多 PROGRESS_EVERY 秒推一次）
    record: Any = None  # OpenClaw：这一轮也记进内核的事件表（ClawRecord；server.json 的 engine.record_openclaw，默认关 = None）
    shutdown: bool = False  # builtin：是关服务时停下的（stop_engine_runs），不是用户点的停
    watched: bool = False  # builtin：用户自己在 app 里说的这一轮（有人在看）：内核多给 ask_user（engine/ask.py）
    asking: dict | None = None  # builtin：ask_user 正在等的那个问题（ask_user 事件；重连时补发）
    todo: list | None = None  # builtin：这一轮最新的待办清单（todo 事件；重连时补发）
    via: str | None = None  # builtin：经谁的请求引出来的（REQUEST_VIA；后台任务做完开的那一轮 = task）：ChatGPT 套餐据此认 /mcp 和后台
    served: str | None = None  # builtin：备用链（engine/fallback.py）换过模型时真正回答的那个；回完当 model，原来要的记成 requested（→ fallbackFrom）
    watchdog: bool = False  # builtin：是定时任务的看门狗停的（engine/unattended.in_chat 一直没动静），不是用户点的停：会话里不补「用户停了」
    turn: str | None = None  # builtin：内核记的这一轮（engine_turns.id）和它的用量：定时任务 chat 模式的运行记录带上（engine/jobs.py）
    usage: dict | None = None

    def publish(self, item: tuple[str, dict]) -> None:
        for q in list(self.queues):
            q.put_nowait(item)


RUNS: dict[str, Run] = {}
RUN_KEEP_SECONDS = 15 * 60  # 回完的回复留一会儿：手机断线后重新接上还能拿到完整内容，不至于显示"没发出去"
RESTART_TAIL = ("（服务重启了，这一轮停在这里）", "(The server restarted; this turn stopped here.)")  # 关服务停的那条回复接在后面的


def stopped_by_user(status: str | None, text: str | None) -> bool:
    """库里这条回复是不是用户点了停的（stopped，后面接的不是 RESTART_TAIL）：engine/context.tail_part 据此说那件事别接着做。"""
    return status == "stopped" and not (text or "").rstrip().endswith(RESTART_TAIL)


@dataclass
class Queued:
    """回复进行中用户又发来的一条（2026-09-28）：先记进库（status queued，app 上标「排队」），
    这条回复结束后和同一批排着的合成一轮发给模型（drain）；future 拿到那一轮的 Run，等着的连接接上去看回复。"""
    user_id: int
    content: str | list  # 发给 Gateway 的这一条（前情、附件都拼好了）
    gw_text: str
    ts: str
    future: asyncio.Future


QUEUED: dict[str, list[Queued]] = {}
STEERS: dict[str, asyncio.Future] = {}  # 插话那一条的 runId → 结果：None = 并进了正在跑的那一轮；Run = 变成了单独一轮（接管了）
QUEUE_RESUME_MINUTES = 30  # 服务重启时库里还排着的：这么久以内的接着发，更早的标成没发出去


ENV_REF = re.compile(r"\$(\$?)\{([A-Z_][A-Z0-9_]*)\}")  # OpenClaw 配置里的 ${VAR}；$${VAR} = 字面的 ${VAR}


def gateway_token() -> str:
    """Gateway 的共享令牌 = openclaw.json 的 gateway.auth.token。可以照 OpenClaw 的写法引用环境变量（2026-09-30）：
    "${VAR}" 或 SecretRef {"source": "env", "id": "VAR"}；没写令牌就用 OPENCLAW_GATEWAY_TOKEN。
    变量先看服务进程的环境，再看 .env（claw.env_value；OpenClaw 自己也读 ~/.openclaw/.env）。缺了 → 503，不拿 "${VAR}" 原样去连。"""
    try:
        auth = (json.loads(OPENCLAW.read_text(encoding="utf8")).get("gateway") or {}).get("auth") or {}
        tok = auth.get("token")
    except (OSError, ValueError, AttributeError) as e:
        raise HTTPException(503, L(f"读不到 Gateway token：{e}", f"Can't read the Gateway token: {e}")) from e
    missing: list[str] = []

    def var(name: str) -> str:
        if not (v := claw.env_value(name)):
            missing.append(name or "?")
        return v

    if isinstance(tok, dict):  # SecretRef 只认 env 这一种；file / exec 要 OpenClaw 自己解
        if tok.get("source") != "env":
            raise HTTPException(503, L(f"gateway.auth.token 是 {tok.get('source')} 类的 SecretRef，这里只认 env 类和 ${{VAR}}",
                                       f"gateway.auth.token is a {tok.get('source')} SecretRef; only env ones and ${{VAR}} work here"))
        tok = var(str(tok.get("id") or ""))
    elif isinstance(tok, str):
        tok = ENV_REF.sub(lambda m: "${" + m[2] + "}" if m[1] else var(m[2]), tok)
    else:
        tok = var("OPENCLAW_GATEWAY_TOKEN")
    if missing or not tok:
        what = "、".join(missing) or "gateway.auth.token"
        raise HTTPException(503, L(f"读不到 Gateway token：{what} 没有值（服务的环境和 .env 里都没有）",
                                   f"Can't read the Gateway token: {what} has no value (not in the server's environment or .env)"))
    return tok


PREFIX_RELAY = "【主对话转来】"  # 每个 Group 一个独立 OpenClaw agent（2026-09-24，第 7 步）


def study_agent() -> str | None:
    """学习台（study-* 线程）归哪个 Agent：server.json 的 study.agent，没配或那个 Agent 不在就留在 main（2026-09-27）。"""
    a = (raw().get("study") or {}).get("agent")
    return a if a and a in settings.group_agents else None


def agent_of(thread: str) -> str:
    """线程归哪个 agent：main 和独立空间在 main，Group 各自一个，学习台归 study.agent。groups 表里有但配置里没建 agent 的先留在 main。
    builtin（自己的内核）：和内核一样分（engine/owner.agent_of：groups 表里的 Agent 不用有工作区）——内核按它定脚本认成谁（MOUSSE_AGENT），
    日程 / 页面 / 项目的改动卡再按它找「这会儿在回复的是哪一次」，两边要一致，不然卡挂不到那个 Agent 的回复下面。"""
    if claw.is_builtin():
        from engine import owner as engine_owner  # 延迟导入：内核只在 builtin 时用
        return engine_owner.agent_of(thread)
    if thread in settings.group_agents:
        return thread
    if thread.startswith("study-") and (a := study_agent()):
        return a
    return "main"


def session_key(thread: str) -> str:
    if not claw.is_openclaw():  # 别的 claw：会话键只是给它分会话用（claw.py 的 header / user 模式）
        return f"mousse:{thread}"
    if thread == "main":
        return "agent:main:main"
    if thread in settings.group_agents:
        return f"agent:{thread}:main"
    return f"agent:{agent_of(thread)}:grava:{thread}"


def thread_of(key: str | None) -> str | None:
    """session_key 反过来：agent:main:main → main，agent:main:grava:<id> → <id>，agent:<Agent>:main → 那个 Agent。
    别的会话（定时任务、Telegram 群、子会话）不是 app 的线程，返回 None。"""
    parts = (key or "").split(":")
    if key == "agent:main:main":
        return "main"
    if len(parts) >= 4 and parts[:3] == ["agent", "main", "grava"]:
        return ":".join(parts[3:])
    if len(parts) >= 4 and parts[0] == "agent" and parts[2] == "grava" and parts[1] in settings.group_agents:
        return ":".join(parts[3:])  # 归到某个 Agent 的学习台线程
    if len(parts) == 3 and parts[0] == "agent" and parts[2] == "main" and parts[1] in settings.group_agents:
        return parts[1]
    return None


def db() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT, thread TEXT NOT NULL, role TEXT NOT NULL, text TEXT NOT NULL,
        model TEXT, ts TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'ok')""")
    conn.execute("CREATE INDEX IF NOT EXISTS messages_thread ON messages(thread, id)")
    if str(DB) not in _at_index:  # 每个进程每个库只建（试）一次
        _at_index.add(str(DB))
        try:  # 按时间取（某一天、上次聊到哪）都比 julianday(ts)（见 day_bounds）：表达式索引让它照样走索引，老数据不用改写
            conn.execute("CREATE INDEX IF NOT EXISTS messages_thread_at ON messages(thread, julianday(ts))")
        except sqlite3.Error as e:  # 建不起来（哪一行的 ts 不像时间）只是按天取慢一点，别拦着整个对话
            print(f"[chat] messages 的时间索引没建上：{e}")
    conn.execute("""CREATE TABLE IF NOT EXISTS threads (
        id TEXT PRIMARY KEY, model TEXT, updated_at TEXT)""")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(messages)")}
    if "requested" not in cols:
        conn.execute("ALTER TABLE messages ADD COLUMN requested TEXT")  # 请求的模型；model 是实际回答的模型
    if "attachments" not in cols:
        conn.execute("ALTER TABLE messages ADD COLUMN attachments TEXT")  # 给 app 显示的附件摘要 JSON（files.py）
        conn.execute("ALTER TABLE messages ADD COLUMN gw_text TEXT")  # 实际发给 Gateway 的文字（含附件抽出的内容），撤回时用它找记录
    if "origin" not in cols:
        conn.execute("ALTER TABLE messages ADD COLUMN origin TEXT")  # 回复是被什么引出来的（user / auto / relay），未读里的「给你的」按它算
    if "reply_to" not in cols:
        conn.execute("ALTER TABLE messages ADD COLUMN reply_to INTEGER")  # 长按「引用」着发的：引的是哪条消息（app 在气泡上面显示原话）
    if "forward" not in cols:
        conn.execute("ALTER TABLE messages ADD COLUMN forward TEXT")  # 合并转发给这个 Agent 的：转发卡（forward.py 的 forward_card，JSON）
    conn.execute("""CREATE TABLE IF NOT EXISTS activity_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, actor TEXT NOT NULL, text TEXT NOT NULL, kind TEXT NOT NULL)""")
    return conn


def log_activity(text: str, kind: str, actor: str | None = None) -> None:
    """app 侧有后果的动作记一行（记忆规范 L4 activity_log）。不写被删 / 被忘的内容本身。"""
    with _lock, db() as conn:
        conn.execute("INSERT INTO activity_log(ts, actor, text, kind) VALUES(?,?,?,?)", (now_iso(), actor or L("你", "You"), text, kind))


def now_iso() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


DAY_START_HOUR = 4  # 逻辑日从 04:00 开始（蓝图 4.3）：凌晨 1 点还在聊的算前一天


def day_of(ts: str) -> str:
    """一条消息属于哪个逻辑日（YYYY-MM-DD，按 server.json 现在的时区，04:00 为界）。"""
    try:
        dt = datetime.fromisoformat(ts).astimezone(TZ)
    except ValueError:
        return ts[:10]
    return (dt - timedelta(hours=DAY_START_HOUR)).strftime("%Y-%m-%d")


def day_bounds(day: str) -> tuple[str, str]:
    """逻辑日 day 对应的 [起, 止) ISO 时间（含时区）。
    库里的 ts 带的是写下那一刻的时区偏移：server.json 的 timezone 改过（比如 +01:00 的里斯本改成 +08:00 的上海），新旧两种偏移就混在一起，
    按字符串比、按字符串排都会错（上海的「17 日 05:00」按字符串排在里斯本的「16 日 23:00」后面，其实早了一个小时）。
    所以 SQL 里一律比 julianday(ts) 和 julianday(?)：SQLite 自己解开偏移、换到同一条时间轴上再比，老数据不用改写；
    messages 有 (thread, julianday(ts)) 的表达式索引（见 db），按天取照样走索引。Python 里按 instant 比。"""
    d = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=TZ, hour=DAY_START_HOUR)
    return d.isoformat(), (d + timedelta(days=1)).isoformat()


def instant(ts: str | None) -> float:
    """ISO 时间 → Unix 秒，按真时间比、排序用（偏移不一定一样，见 day_bounds）。不带偏移的按现在的时区算；不像样的 → 0。"""
    try:
        dt = datetime.fromisoformat(str(ts))
    except ValueError:
        return 0.0
    return (dt if dt.tzinfo else dt.replace(tzinfo=TZ)).timestamp()


def hhmm(ts: str) -> str:
    """显示的几点几分：带偏移的换到现在的时区（时区改过以后，和它归到哪一天对得上）。"""
    try:
        dt = datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        dt = None
    return dt.astimezone(TZ).strftime("%H:%M") if dt is not None and dt.tzinfo else ts[11:16]


def row_to_msg(r: sqlite3.Row) -> dict:
    fallback = r["requested"] if r["requested"] and r["requested"] != r["model"] else None
    keys = r.keys()
    att = r["attachments"] if "attachments" in keys else None
    fwd = r["forward"] if "forward" in keys else None
    out = {"id": f"db{r['id']}", "role": "user" if r["role"] == "user" else "auto" if r["role"] == "auto" else "grava", "text": r["text"],
           "modelId": r["model"], "fallbackFrom": fallback, "time": hhmm(r["ts"]), "status": r["status"],
           "attachments": json.loads(att) if att else None}
    if fwd:
        try:
            out["forward"] = json.loads(fwd)
        except ValueError:
            pass
    return out


def with_quote(m: dict, ref: sqlite3.Row | None) -> dict:
    """长按「引用」着发的那条：带上引的是哪条、原话（app 在气泡上面显示一行）。"""
    if ref is not None:
        m["replyTo"] = {"id": f"db{ref['id']}", "role": "user" if ref["role"] == "user" else "grava", "text": " ".join(ref["text"].split())[:200]}
    return m


def thread_model(conn: sqlite3.Connection, thread: str) -> str:
    r = conn.execute("SELECT model FROM threads WHERE id=?", (thread,)).fetchone()
    m = r["model"] if r and r["model"] else None
    if not claw.is_openclaw():  # 别的 claw：线程上记的不在它的模型列表里（比如换 claw 之前选的）就用它的默认
        return m if m in claw.models() else claw.model()
    return m or DEFAULT_MODEL


@router.get("/api/chat/history")
def history(thread: str = "main", limit: int = 200, day: str | None = None, all: int = 0):
    """默认只给当前逻辑日（04:00 为界）的记录：会话每天 04:00 重置，模型也只记得今天的，之前的在历史页翻。
    day=YYYY-MM-DD 取某一天；all=1 取最近 limit 条（调试用）。"""
    if not day and not all and thread.startswith("tp-"):
        all = 1  # 思考主题跨好几天聊：整段都给（主题里的碎片每天第一句话会重新带给模型）
    if not day and not all:
        day = day_of(now_iso())
    with _lock, db() as conn:
        if day:
            try:
                lo, hi = day_bounds(day)
            except ValueError as exc:
                raise HTTPException(400, L("day 要写成 YYYY-MM-DD", "day must be YYYY-MM-DD")) from exc
            # 按真时间比（时区改过、夏令时切换，偏移都可能不一样，见 day_bounds）
            rows = conn.execute("SELECT * FROM messages WHERE thread=? AND julianday(ts)>=julianday(?) AND julianday(ts)<julianday(?) "
                                "ORDER BY id DESC LIMIT 1000", (thread, lo, hi)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM messages WHERE thread=? ORDER BY id DESC LIMIT ?", (thread, min(limit, 500))).fetchall()
        quoted = {r["reply_to"] for r in rows if r["reply_to"]}
        refs = {q["id"]: q for q in conn.execute(f"SELECT id, role, text FROM messages WHERE id IN ({','.join('?' * len(quoted))})",
                                                 list(quoted)).fetchall()} if quoted else {}
        run = RUNS.get(thread)
        return {"ok": True, "thread": thread, "sessionKey": session_key(thread), "modelId": thread_model(conn, thread),
                "messages": [with_quote(row_to_msg(r), refs.get(r["reply_to"])) for r in reversed(rows)],
                "inFlight": {"text": run.text, "modelId": run.model, "time": hhmm(run.started)} if run and not run.done else None}


@router.get("/api/chat/days")
def days(thread: str = "main"):
    """这个线程有记录的逻辑日，新的在前：日期、条数、第一句。历史页的目录。"""
    with _lock, db() as conn:
        rows = conn.execute("SELECT id, role, text, ts FROM messages WHERE thread=? ORDER BY id", (thread,)).fetchall()
    out: dict[str, dict] = {}
    for r in rows:
        d = day_of(r["ts"])
        e = out.setdefault(d, {"day": d, "count": 0, "first": "", "lastTs": r["ts"]})
        e["count"] += 1
        e["lastTs"] = r["ts"]
        if not e["first"] and r["role"] == "user":
            e["first"] = r["text"].strip().replace("\n", " ")[:80]
    return {"ok": True, "thread": thread, "days": sorted(out.values(), key=lambda x: x["day"], reverse=True)}


def snippet(text: str, words: list[str], width: int = 90) -> str:
    """命中词附近的一段。"""
    flat = text.replace("\n", " ")
    low = flat.lower()
    pos = min((low.find(w) for w in words if low.find(w) >= 0), default=0)
    start = max(0, pos - width // 3)
    seg = flat[start:start + width]
    return ("…" if start > 0 else "") + seg + ("…" if start + width < len(flat) else "")


@router.get("/api/search")
def search(q: str, thread: str | None = None, limit: int = 60):
    """关键词搜索：对话记录、建议卡、日志。多个词都要出现。LIKE 就够（表很小；将来慢了再上 FTS）。"""
    words = [w.lower() for w in q.split() if w.strip()]
    if not words:
        return {"ok": True, "hits": []}
    like = " AND ".join(["lower(text) LIKE ?"] * len(words))
    args = [f"%{w}%" for w in words]
    hits: list[dict] = []
    with _lock, db() as conn:
        tq = "SELECT id, thread, role, text, ts FROM messages WHERE " + like + (" AND thread=?" if thread else "") + " ORDER BY id DESC LIMIT ?"
        for r in conn.execute(tq, (*args, *([thread] if thread else []), limit)).fetchall():
            hits.append({"kind": "message", "id": f"db{r['id']}", "thread": r["thread"], "role": r["role"], "day": day_of(r["ts"]),
                         "ts": r["ts"], "time": hhmm(r["ts"]), "snippet": snippet(r["text"], words)})
        fq = "SELECT id, group_id, title, body, created_at FROM feed_items WHERE " + " AND ".join(["(lower(title) LIKE ? OR lower(body) LIKE ?)"] * len(words)) + (" AND group_id=?" if thread else "") + " ORDER BY created_at DESC LIMIT ?"
        for r in conn.execute(fq, (*[a for w in args for a in (w, w)], *([thread] if thread else []), limit)).fetchall():
            hits.append({"kind": "card", "id": r["id"], "thread": r["group_id"], "role": "grava", "day": day_of(r["created_at"]),
                         "ts": r["created_at"], "time": hhmm(r["created_at"]), "snippet": r["title"] + " · " + snippet(r["body"] or "", words, 60)})
        jq = "SELECT id, group_id, text, ts FROM journal WHERE status='active' AND " + like + (" AND group_id=?" if thread else "") + " ORDER BY ts DESC LIMIT ?"
        for r in conn.execute(jq, (*args, *([thread] if thread else []), limit)).fetchall():
            hits.append({"kind": "journal", "id": r["id"], "thread": r["group_id"], "role": "user", "day": day_of(r["ts"]),
                         "ts": r["ts"], "time": hhmm(r["ts"]), "snippet": snippet(r["text"], words)})
    if not thread:  # 全部对话：好友聊天也搜（2026-10-05）。kind friend，friend = {id, name}，id 是好友消息在本机的行 id
        try:
            import friends  # 延迟导入：friends 依赖本模块
            for h in friends.search_messages(words, limit):
                hits.append({"kind": "friend", "id": str(h["id"]), "thread": None, "friend": {"id": h["friend"], "name": h["name"]},
                             "role": "user" if h["dir"] == "out" else "friend", "day": day_of(h["ts"]), "ts": h["ts"], "time": hhmm(h["ts"]),
                             "snippet": h["snippet"]})
        except Exception:  # noqa: BLE001 — 好友表没建好不影响别的
            pass
    hits.sort(key=lambda h: instant(h["ts"]), reverse=True)
    return {"ok": True, "q": q, "hits": hits[:limit]}


class ModelBody(BaseModel):
    thread: str
    model: str


@router.post("/api/chat/model")
def set_model(body: ModelBody):
    with _lock, db() as conn:
        conn.execute("INSERT INTO threads(id, model, updated_at) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET model=excluded.model, updated_at=excluded.updated_at",
                     (body.thread, body.model, now_iso()))
    return {"ok": True, "thread": body.thread, "modelId": body.model}


class SendBody(BaseModel):
    thread: str = "main"
    text: str = ""
    model: str | None = None
    attachments: list[str] = []  # 先经 /api/chat/upload 拿到的附件 id
    origin: str = "user"         # user：用户发的；auto：定时器之类系统触发的，app 里显示成一行灰字
    notify: bool | None = None   # 旧开关（/api/chat/trigger）：false = 回完不推，等于 level none
    level: str | None = None     # 推送档位（/api/chat/trigger）：ring 响铃 / quiet 静默进通知中心 / none 不推；都不给 = quiet
    inboxId: str | None = None   # （/api/chat/send）引用收件箱里的一条回复：还没定下来的 = 「改一下」（改成 revising），定下来的 = 「跟进」；都给模型带上前情
    ref: str | None = None       # （/api/chat/send）说的是日程或「要记得的」里的哪一条（schedule.py 的 id）：模型另外看到是哪一条、怎么改
    save: str | None = None      # （/api/chat/send）问的是哪条收藏（saves.py 的 id）：模型另外看到它的来源、备注和正文
    replyTo: str | None = None   # （/api/chat/send）长按「引用」着发的：引的是这个对话里哪条消息（"db<id>"），模型另外看到原话
    study: str | None = None     # （/api/chat/send）在学习台里说的（「课|向导第几步」）：模型另外看到是哪门课、在加课的哪一步（courses.chat_context）
    digest: bool = False         # （/api/chat/trigger）别的 claw 的日结：这一轮的回复就是今天的日结，服务器存下来（见 save_digest）


def sse(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode()


async def gateway_stream(run: Run, text: str | list, token: str) -> None:
    """发给 Gateway、流式攒进 run.text。出错抛 httpx.HTTPError / RuntimeError / OSError；被「停」取消时连接一断，Gateway 就中止这一轮。"""
    async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=10)) as client:
        async with client.stream("POST", f"{GATEWAY}/v1/chat/completions",
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                                          "x-openclaw-model": run.model, "x-openclaw-session-key": run.key or session_key(run.thread),
                                          "x-openclaw-agent-id": agent_of(run.thread)},
                                 json={"model": f"openclaw/{agent_of(run.thread)}", "stream": True, "messages": [{"role": "user", "content": text}]}) as r:
            if r.status_code != 200:
                raw = (await r.aread()).decode("utf8", "replace")
                raise RuntimeError(f"Gateway HTTP {r.status_code}: {raw[:300]}")
            async for line in r.aiter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    j = json.loads(payload)
                except ValueError:
                    continue
                if "error" in j:
                    raise RuntimeError(j["error"].get("message") or "Gateway error")
                for ch in j.get("choices", []):
                    delta = (ch.get("delta") or {}).get("content")
                    if delta:
                        run.text += delta
                        run.publish(("delta", {"text": delta}))


def transport() -> str:
    """发消息走哪条路：server.json 的 chat.transport。"http"（默认）= OpenAI 兼容接口；"ws" = Gateway 的 WebSocket 对话通道（gateway_ws.py，能插话）。"""
    if not claw.is_openclaw():
        return "http"
    return str((raw().get("chat") or {}).get("transport") or "http").lower()


def ws_ok(content: str | list) -> bool:
    """这一条能不能走 WebSocket：带图片的（content 数组）还走 HTTP（图片按 OpenAI 格式直接给模型，WebSocket 的附件格式没对过）。"""
    return transport() == "ws" and isinstance(content, str)


async def claw_stream(run: Run, content: str | list) -> None:
    """别的 claw（claw.py，OpenAI 兼容接口）：history 模式把这个对话今天在这一条之前的记录一起发过去，claw 自己不用记会话。
    builtin（自己的内核）：见 engine_stream。"""
    if claw.is_builtin():
        await engine_stream(run, content)
        return
    mode, _, turns = claw.session_mode()
    messages: list[dict] = []
    if mode == "history":
        with _lock, db() as conn:
            messages = claw.history(conn, run.thread, run.user_id, day_bounds(day_of(run.started))[0], turns)
    messages.append({"role": "user", "content": content})

    def on_delta(text: str) -> None:
        run.text += text
        run.publish(("delta", {"text": text}))
    await claw.stream(messages, claw.day_key(run.key or session_key(run.thread), day_of(run.started)), run.model, on_delta)


async def engine_stream(run: Run, content: str | list) -> None:
    """builtin（自己的内核，engine/owner.py）：接在这个对话这一天的会话后面跑一轮主人回合，带这个 Agent 的工具和关卡（第 3 项起和
    OpenClaw 的对话通道一样有「在想 / 在做」、插话、停）：
    - 进度：内核的事件经 note_engine 画成 run.progress（和 note_progress 一样的形状），推 SSE progress；重连时 attach 补发。
    - 插话：回复进行中又说的一句经 run.control 进这一轮（steer_engine）。写进会话了（steer 事件）= 并进了这一轮；这一轮没来得及用上
      就结束了 = 排到队首，回完和排着的一起变成下一轮（drain 发，那几条的连接接到那一轮上）——不丢。
    - 停：/api/chat/stop → run.control.abort()：已经出来的字留着，正在跑的工具收到取消，会话补完整，这一轮记成 stopped；
      等你点头的收件箱卡不受影响（关卡交卡就返回了，卡照样在收件箱里等）。会话里补一句「用户停了」（engine/loop.note_stopped）：
      下一轮、压缩摘要都不把它当成没做完的接着做。
    - 后台任务的线程 task:<id>（任务卡上的「改一下」、派它的对话 send 的，第 5 项）：不是主对话，交给 engine/tasks.round_turn
      在那个任务的子会话里接着跑一轮（子 Agent 的工具和关卡）。
    - ⑫b：用户自己说的这一轮（run.watched）有 ask_user：问题是回复里的一段字，同时推 SSE 的 ask_user 事件（以后 app 画选项按钮），
      用户插的下一句就是回答（answer 事件：那条的连接接到这一轮上，和插话一样）；todo、plan_mode 事件也推成 SSE（以后 app 画）。
      app 现在不认这几个事件，直接跳过（同 card、progress）。
    模型出错 → RuntimeError（run_gateway 记成没拿到回复）。被取消（硬停、服务关了）照常往外抛，内核已经把会话补完整。"""
    from engine import Control  # 延迟导入：内核只在 builtin 时用
    from engine import owner as engine_owner

    if run.control is None:
        run.control = Control()

    def on_text(text: str) -> None:
        run.text += text
        run.publish(("delta", {"text": text}))

    def on_event(ev: dict) -> None:
        t = ev.get("type")
        if t in ("steer", "follow_up", "answer"):  # 插话写进会话了（answer：ask_user 等到的回答）：并进了这一轮，那条的连接接到这一轮上
            item = run.steered.pop(ev.get("ref"), None)
            if item is not None and not item.future.done():
                item.future.set_result(run)
            return
        if t in ("ask_user", "ask_user_done", "todo", "plan_mode"):  # ⑫b：推给 app（以后画选项按钮、清单、开关）；重连时补发
            data = {k: v for k, v in ev.items() if k != "type"}
            if t == "ask_user":
                run.asking = data
            elif t == "ask_user_done":
                run.asking = None
            elif t == "todo":
                run.todo = data.get("items")
            run.publish((t, data))
            return
        if t == "rewrite":  # 撤回了一段（Claude Code 流断了整条重来、前一段后面又整个说了一遍，engine/claude_brain）：整段换掉
            run.text = str(ev.get("text") or "")
            run.publish(("text", {"text": run.text}))
            return
        if ev.get("type") == "task" and isinstance(ev.get("task"), dict):  # 这一轮派的后台任务（第 5 项发）：当场出任务卡，重连时补发
            import cards as cards_mod  # 延迟导入：cards.py 依赖本模块
            cards_mod.publish(run, cards_mod.task_json(ev["task"]))
            return
        if not note_engine(run, ev):
            return
        now = time.monotonic()
        if ev.get("type") == "thinking" and now - run.progress_at < PROGRESS_EVERY:
            return  # 思考一个字一个字来：攒着，下一次（或者下一个事件）一起推
        run.progress_at = now
        pr = run.progress  # 推一份快照：note_engine 原地改 run.progress，连接慢的话排着的几条会都变成最新的样子
        run.publish(("progress", {**pr, "steps": [dict(s) for s in pr["steps"]]}))

    try:
        if run.thread.startswith("task:"):  # 后台任务的线程（改一下、接着说，第 5 项）：在那个任务的子会话里接着跑一轮
            from engine import tasks as engine_tasks
            res = await engine_tasks.round_turn(run.thread[5:], content, on_text, origin=run.origin, control=run.control, on_event=on_event)
        else:
            res = await engine_owner.respond(run.thread, day_of(run.started), content, run.model, on_text, ref=run.user_id, origin=run.origin,
                                             control=run.control, on_event=on_event, watched=run.watched, via=run.via)
    finally:
        run.control.close()  # 这一轮不再收插话：没赶上的排到队首，这一轮收完尾 drain 一起发出去
        left = [it for it in run.steered.values() if not it.future.done()]
        run.steered.clear()
        if left:  # 库里也改成排着的：万一还没 drain 服务就停了，下次启动 resume_queued 接着发
            QUEUED[run.thread] = [*left, *QUEUED.get(run.thread, [])]
            ids = [it.user_id for it in left]
            with _lock, db() as conn:
                conn.execute(f"UPDATE messages SET status='queued' WHERE id IN ({','.join('?' * len(ids))})", ids)
        close_steps(run)
    run.turn, run.usage = res.turn, dict(res.usage or {})
    if res.models and res.model and res.model != res.models[0]:  # 备用链换过模型（Result.models 按先后）：res.model 是出了回复的那个
        run.served = res.model
    if res.status == "aborted":
        run.status, run.level = "stopped", "none"
        from engine import loop as engine_loop
        if run.shutdown:  # 关服务时停的（不是用户点的）：会话里补一句，下一轮它知道用户没看到后面
            engine_loop.note_restart(res.turn)
        elif run.stopping and not run.watchdog:  # 用户点了停：补一句「用户停了」，压缩摘要、下一轮都别当成没做完的待办接着做
            engine_loop.note_stopped(res.turn)
    elif res.status == "error":
        raise RuntimeError(res.error or "model error")
    elif res.status == "refused" and not res.text:
        raise RuntimeError("the model declined to answer")


def steer_engine(thread: str, cur: Run, text: str, context: str | None) -> tuple[int, str, asyncio.Future] | None:
    """builtin 的插话：记下这一条（status steered，app 上标「插话」，和 OpenClaw 时一样），交给正在跑的这一轮（Control.steer：这一批工具
    做完带着它再问模型；模型已经说完了也带着再问一次）。返回 (消息 id, 时间, 结果)：结果是这条该接着看的那一轮——写进会话了 = 这一轮
    （cur）；没赶上 = 下一轮（engine_stream 收尾时排到队首，drain 开跑时给）。进不去（这一轮刚结束、停了、插话已经排满 STEER_CAP 条）
    → None，调用方当排队处理。
    连着插好几句：每句各占一条消息，这一批工具做完一起读到（OpenClaw 的 steer 也是逐条进记录）；没赶上的几句回完合成一轮（combine）。"""
    ctl = cur.control
    if ctl is None or not ctl.can_steer():
        return None
    from engine import owner as engine_owner
    content = f"{context}\n\n{text}" if context else text
    msg = engine_owner.user_message(content)
    if msg is None:
        return None
    ts = now_iso()
    with _lock, db() as conn:
        user_id = conn.execute("INSERT INTO messages(thread, role, text, model, ts, status, gw_text) VALUES(?,?,?,?,?,?,?)",
                               (thread, "user", text, None, ts, "steered", content)).lastrowid
    item = Queued(user_id=user_id, content=content, gw_text=content, ts=ts, future=asyncio.get_running_loop().create_future())
    cur.steered[user_id] = item
    # 上面查过进得去，中间没有 await；万一没进去，它还在 cur.steered 里，收尾时照样变成下一轮。said：ask_user 正在等的话，这句就是回答（⑫b）
    ctl.steer({**msg, "ref": user_id}, said=True)
    return user_id, ts, item.future


def close_steps(run: Run) -> None:
    """一轮结束：还转着的步骤标成没做完（Hermes 的 flush_open_tool_calls：被挡下、被打断的调用不会自己报结束，别一直转）。"""
    pr = run.progress
    if pr and any(s.get("status") == "running" for s in pr["steps"]):
        pr["steps"] = [{**s, "status": "failed"} if s.get("status") == "running" else s for s in pr["steps"]]


PROGRESS_STEPS = 6       # 进度里最多留最近几步
PROGRESS_THOUGHT = 600   # 思考摘要留最后多少字
PROGRESS_EVERY = 0.4     # builtin：思考的进度最多多久推一次（秒）


def note_engine(run: Run, ev: dict) -> bool:
    """自己的内核（builtin）的事件（engine/loop.py 开头列的）→ run.progress，和 note_progress 一样的形状，app 不用改：
      phase（建会话备料、压缩：preparing_context）→ phase；model（又要问模型了）→ waiting_for_model，思考从这次重新攒；
      thinking → thought（这次调模型的思考的最后 PROGRESS_THOUGHT 字，同对话通道）；
      tool_run → 一步 running，tool = 工具名，detail = Tool.title（一句标题，不放参数原文）；
      tool_end → done / failed（交给你点头的算 done，detail 后面写「等你点头」；被关卡挡下、出错、超时、点了停的算 failed）。
    返回 False = 这个事件不改变显示。"""
    t = ev.get("type")
    pr = run.progress or {"since": round(run.t0, 1), "phase": "", "steps": [], "thought": ""}
    if t == "phase":
        phase = str(ev.get("phase") or "")
        if not phase or pr["phase"] == phase:
            return False
        pr["phase"] = phase
    elif t == "model":
        run.thought = ""
        if pr["phase"] == "waiting_for_model":
            return False
        pr["phase"] = "waiting_for_model"
    elif t == "thinking":
        run.thought = (run.thought + str(ev.get("text") or ""))[-PROGRESS_THOUGHT * 4:]
        thought = run.thought.strip()[-PROGRESS_THOUGHT:]
        if not thought or (pr["thought"] == thought and pr["phase"] == "thinking"):
            return False
        pr["phase"], pr["thought"] = "thinking", thought
    elif t in ("tool_run", "tool_end"):
        sid, name = str(ev.get("id") or ""), str(ev.get("name") or "")
        if not sid:
            return False
        old = next((x for x in pr["steps"] if x["id"] == sid), None)
        detail = str(ev.get("detail") or "").strip()[:120] or (old or {}).get("detail", "")
        if t == "tool_run":
            status = "running"
        else:
            how = str(ev.get("status") or ("ok" if ev.get("ok") else "error"))
            status = "done" if how in ("ok", "pending") else "failed"
            if how == "pending":
                wait = L("等你点头", "waiting for your OK")
                detail = f"{detail} · {wait}" if detail else wait
        step = {"id": sid, "tool": name, "detail": detail, "status": status}
        if step == old:
            return False
        steps = [step if x["id"] == sid else x for x in pr["steps"]] if old else [*pr["steps"], step]
        pr["steps"] = steps[-PROGRESS_STEPS:]
        pr["phase"] = "tool" if any(x["status"] == "running" for x in pr["steps"]) else "thinking"
    else:
        return False
    run.progress = pr
    return True


def note_progress(run: Run, p: dict) -> bool:
    """对话通道的 agent 事件 → run.progress（给 app 画「在想 / 在做」）：
    {"since": 开始的 Unix 秒, "phase": 准备阶段或 thinking / tool, "steps": [{"id", "tool", "detail", "status"}], "thought": 最近一段思考摘要}。
    步骤只取 kind = tool 的条目（exec 另有一条 command，重复），detail 是模型自己写的标题（读文件只留文件名），不带命令参数和输出。
    返回 False = 这个事件不改变显示。"""
    stream, d = p.get("stream"), p.get("data") or {}
    pr = run.progress or {"since": round(run.t0, 1), "phase": "", "steps": [], "thought": ""}
    if stream == "run_status":
        phase = str(d.get("phase") or "")
        if not phase or pr["phase"] == phase:
            return False
        pr["phase"] = phase
    elif stream == "thinking":
        text = str(d.get("text") or "").strip()
        if not text:
            return False
        pr["phase"], pr["thought"] = "thinking", text[-PROGRESS_THOUGHT:]
    elif stream == "item":
        if d.get("kind") != "tool" or not d.get("itemId"):
            return False
        sid, name = str(d["itemId"]), str(d.get("name") or "")
        detail = item_detail(name, d.get("meta"))
        status = str(d.get("status") or "")
        status = "failed" if d.get("isError") or status in ("failed", "error") else "done" if d.get("phase") == "end" or status == "completed" else "running"
        old = next((x for x in pr["steps"] if x["id"] == sid), None)
        step = {"id": sid, "tool": name, "detail": detail[:120] or (old or {}).get("detail", ""), "status": status}
        if step == old:
            return False
        steps = [step if x["id"] == sid else x for x in pr["steps"]] if old else [*pr["steps"], step]
        pr["steps"] = steps[-PROGRESS_STEPS:]
        pr["phase"] = "tool" if any(x["status"] == "running" for x in pr["steps"]) else "thinking"
    else:
        return False
    run.progress = pr
    return True


def item_detail(name: str, meta) -> str:
    """对话通道里一个工具调用的标题（模型自己写的）：读写文件的只留文件名，不带命令参数和输出。"""
    detail = str(meta or "").strip()
    if name in ("read", "write", "edit", "apply_patch") and detail:
        detail = re.sub(r"^(from|to|in)\s+", "", detail)
        detail = detail.rstrip("/").rsplit("/", 1)[-1]
    return detail


class ClawRecord:
    """claw 是 OpenClaw 时，把它的一轮也记进内核的事件表（engine_turns 的 kind = claw，加 engine_steps）：harness 方案要的「两份记录
    合成一条事件流」，以后的账本、台账、桌面工作台看一张表就够。server.json 的 engine.record_openclaw = true 才记（默认关）。
    只多记一份：每一步都在 try 里，出错只是少一行记录，OpenClaw 这条路的行为一点不变。记：一轮（哪个对话、Agent、请求的模型、来源、
    多久、结果）+ 对话通道（chat.transport = ws）报的每个工具调用（工具名、模型写的标题，同 note_progress）；不记说的话、思考、
    工具的参数和输出（同 engine/events.py）。服务重启时它们和内核自己的回合一样被标成 aborted（Gateway 那边那一轮可能还在跑，
    重新接上的那一段另记一轮）。"""

    def __init__(self, run: Run) -> None:
        self.tid: str | None = None
        self.conv = run.thread
        self.steps: dict[str, int] = {}
        self.ended: set[str] = set()
        try:
            from engine import events as engine_events  # 延迟导入：只在开了记录时用
            self.tid = engine_events.turn_open(conversation=run.thread, kind="claw", model=run.model, agent=agent_of(run.thread),
                                               origin=run.origin)
        except Exception as e:  # noqa: BLE001
            print(f"[chat] OpenClaw 的这一轮没记进事件表：{type(e).__name__}")

    @staticmethod
    def wanted() -> bool:
        e = raw().get("engine")
        return claw.is_openclaw() and isinstance(e, dict) and e.get("record_openclaw") is True

    def note(self, p: dict) -> None:
        """对话通道的一个 agent 事件：工具调用开始 → 开一步，结束 → 收这一步。"""
        if self.tid is None:
            return
        try:
            d = p.get("data") or {}
            if p.get("stream") != "item" or d.get("kind") != "tool" or not d.get("itemId"):
                return
            from engine import events as engine_events
            iid, name = str(d["itemId"]), str(d.get("name") or "")
            if iid not in self.steps:
                self.steps[iid] = engine_events.step_open(self.tid, type_="tool", name=name, detail=item_detail(name, d.get("meta")),
                                                          conversation=self.conv)
            status = str(d.get("status") or "")
            failed = bool(d.get("isError")) or status in ("failed", "error")
            if (failed or d.get("phase") == "end" or status == "completed") and iid not in self.ended:
                self.ended.add(iid)
                engine_events.step_close(self.steps[iid], status="error" if failed else "ok", conversation=self.conv, turn=self.tid)
        except Exception:  # noqa: BLE001 — 少记一步而已
            pass

    def close(self, status: str, error: str | None = None) -> None:
        """这一轮结束：没收的步骤标 aborted，一轮记上结果（done / aborted / error）。只收一次。"""
        if self.tid is None:
            return
        tid, self.tid = self.tid, None
        try:
            from engine import events as engine_events
            for iid, sid in self.steps.items():
                if iid not in self.ended:
                    engine_events.step_close(sid, status="aborted", conversation=self.conv, turn=tid)
            engine_events.turn_close(tid, conversation=self.conv, status=status, error=error)
        except Exception:  # noqa: BLE001
            pass


async def gateway_ws_stream(run: Run, text: str | None) -> None:
    """经 Gateway 的对话通道：发出去（text 为 None = 接管一个已经在跑的 runId），按 runId 收 chat 事件攒进 run.text。
    断过线：重连后这一轮还在跑就接着收，已经结束了就去 chat.history 补回回复。"""
    import gateway_ws as gw_mod  # 延迟导入：只有 chat.transport = ws 时才用得到
    c = gw_mod.client()
    key = run.key or session_key(run.thread)
    if text is not None:
        run.gw_run = await c.send(key, text, queue_mode="followup", model=run.model)  # followup：别插进 Telegram 那边正在跑的一轮
    else:
        await c.subscribe(key)  # 接管已经在跑的一轮：先订阅这个会话，不然收不到它的事件
    while True:
        lost = False
        async for p in c.events(run.gw_run):
            st = p.get("state")
            if st == "agent":
                if run.record is not None:  # 默认没有（engine.record_openclaw）
                    run.record.note(p)
                if note_progress(run, p):
                    run.publish(("progress", run.progress))
                continue
            if st == "delta":
                d = p.get("deltaText") or ""
                if p.get("replace"):
                    run.text = d
                    run.publish(("text", {"text": d}))
                elif d:
                    run.text += d
                    run.publish(("delta", {"text": d}))
            elif st == "final":
                final = gw_mod.message_text(p.get("message"))
                if final and final != run.text:
                    run.text = final
                    run.publish(("text", {"text": final}))
                return
            elif st == "aborted":
                run.status, run.level = "stopped", "none"
                run.text = gw_mod.message_text(p.get("message")) or run.text
                return
            elif st == "error":
                raise RuntimeError(p.get("errorMessage") or "Gateway error")
            elif st == "lost":
                lost = True
        if not lost:
            return
        got = await recover_reply(key, run.gw_run, run.t0)
        if got is not None:
            run.text = got or run.text
            return
        # 还在跑：重连以后接着收


async def recover_reply(key: str, gw_run: str | None, since: float) -> str | None:
    """断线期间这一轮可能已经结束：chat.history 里它不在进行中了，就拿 since 之后最后一条助手回复的文字；还在跑返回 None。"""
    import gateway_ws as gw_mod
    try:
        h = await gw_mod.client().history(key, 30)
    except Exception:  # noqa: BLE001
        return None
    info = h.get("sessionInfo") or {}
    active = info.get("activeRunIds") or []
    if gw_run and gw_run in active:
        return None
    if not gw_run and info.get("hasActiveRun"):
        return None
    for m in reversed(h.get("messages") or []):
        if m.get("role") == "assistant" and (m.get("timestamp") or 0) / 1000 >= since - 5:
            t = gw_mod.message_text(m)
            if t.strip():
                return t
    return ""


async def run_gateway(run: Run, text: str | list | None, token: str) -> None:
    """后台把一条消息发给 Gateway，流式攒回复，结束后入库。不依赖任何客户端连接。text 可以是 OpenAI 的 content 数组（带图片）；
    None = 接管一个 Gateway 已经在跑的 runId（run.gw_run：插话变成了单独一轮、服务重启后接回来）。结束后把回复进行中排着的消息合成下一轮发出去（drain）。"""
    if not claw.is_openclaw():
        run.stream_task = asyncio.create_task(claw_stream(run, text if text is not None else ""))
    else:
        if ClawRecord.wanted():  # 默认关：开了只是多记一份进内核的事件表
            run.record = ClawRecord(run)
        use_ws = text is None or ws_ok(text)
        run.stream_task = asyncio.create_task(gateway_ws_stream(run, text) if use_ws else gateway_stream(run, text, token))
    try:
        await run.stream_task
    except asyncio.CancelledError:
        if not run.stopping:  # 不是用户点的「停」（服务关了之类）：照常往外抛
            if run.record is not None:
                run.record.close("aborted", "cancelled")
            raise
        run.status = "stopped"
        run.level = "none"  # 自己停的不用推
    except (httpx.HTTPError, RuntimeError, OSError) as e:  # noqa: BLE001
        run.status = "error"
        run.error = str(e)
    except Exception as e:  # noqa: BLE001 — WebSocket 那条路的错（连不上、Gateway 拒了）：这一条记成没拿到回复
        run.status = "error"
        run.error = str(e)
    if run.record is not None:
        run.record.close({"ok": "done", "stopped": "aborted"}.get(run.status, "error"), run.error)
    if run.status == "stopped":
        tail = L(*RESTART_TAIL) if run.shutdown else L("（停了）", "(Stopped)")
        run.text = (run.text.rstrip() + "\n\n" if run.text.strip() else "") + tail
    if run.status == "error" and not run.text:
        run.text = L(f"（没拿到回复：{run.error}）", f"(Didn't get a reply: {run.error})")
    run.requested = run.model
    if run.status == "ok":  # 回退链换了模型：OpenClaw 从会话记录里读，自己的内核在 run.served（engine_stream 记的）
        run.model = await actual_model(run.key or session_key(run.thread)) or run.served or run.model
    run.finished = now_iso()
    with _lock, db() as conn:
        cur = conn.execute("INSERT INTO messages(thread, role, text, model, requested, ts, status, origin) VALUES(?,?,?,?,?,?,?,?)",
                           (run.thread, "grava", run.text, run.model, run.requested, run.finished, run.status, run.origin))
        run.reply_id = cur.lastrowid
    if run.digest and run.status == "ok" and run.text.strip():
        save_digest(run.thread, day_of(run.started), run.text)
    try:  # 这次回复里交到收件箱的条目挂到这条回复下面（app 在对话里把卡片显示在它下面）；在「done」之前，app 一刷新就看得到
        import inbox as inbox_mod  # 延迟导入：inbox.py 依赖本模块
        inbox_mod.link_message(run.thread, run.inbox_mark, run.reply_id)
    except Exception:  # noqa: BLE001 — 挂不上只是对话里少一张卡，回复照常结束
        pass
    try:  # 这次回复里转出去的、派出去的也挂到它下面；这次是被转的 Agent 在答，就更新那张转交卡（见 cards.py）
        import cards as cards_mod  # 延迟导入：cards.py 依赖本模块
        cards_mod.on_run_end(run)
    except Exception:  # noqa: BLE001
        pass
    run.done = True
    run.publish(("done", done_payload(run)))
    drain(run.thread)  # 回复进行中排着的：合成下一轮发出去
    import push as push_mod  # 延迟导入：push.py 依赖本模块
    await push_mod.notify_run(run)  # 按档位推：这次写了卡就推卡，否则推回复（level none 不推；推送失败不影响回复）
    try:
        import cards as cards_mod
        await cards_mod.after_run(run)  # 「改一下」的那一轮做完了：静默推到派这个任务的对话
    except Exception:  # noqa: BLE001
        pass

    def _forget() -> None:
        if RUNS.get(run.thread) is run:
            RUNS.pop(run.thread, None)
    asyncio.get_running_loop().call_later(RUN_KEEP_SECONDS, _forget)


def done_payload(run: Run) -> dict:
    fallback = run.requested if run.requested and run.requested != run.model else None
    return {"id": f"db{run.reply_id}", "text": run.text, "modelId": run.model, "fallbackFrom": fallback, "time": hhmm(run.finished or now_iso()),
            "status": run.status, "error": run.error, "seconds": round(time.time() - run.t0, 1)}


PROVIDER_ALIAS = {"openai-codex": "openai"}


async def actual_model(key: str) -> str | None:
    """回退链可能换了模型：从会话记录里读最后一条回复实际用的是哪个。读不到就算了。"""
    if not claw.is_openclaw():
        return None
    try:
        hist = await gateway_call("chat.history", {"sessionKey": key, "limit": 4}, timeout=8)
    except (HTTPException, asyncio.TimeoutError, OSError, ValueError):
        return None
    for m in reversed(hist.get("messages", [])):
        if m.get("role") == "assistant" and m.get("model"):
            prov = m.get("provider") or ""
            return f"{PROVIDER_ALIAS.get(prov, prov)}/{m['model']}" if prov else m["model"]
    return None


async def attach(run: Run) -> AsyncIterator[bytes]:
    """把一个客户端接到正在进行的回复上：先补发已攒下的文字，再转发后续增量。"""
    q: asyncio.Queue = asyncio.Queue()
    run.queues.append(q)
    try:
        yield sse("start", {"userId": f"db{run.user_id}", "time": hhmm(run.started), "modelId": run.model, "sessionKey": run.key or session_key(run.thread)})
        for card in list(run.cards.values()):  # 这次回复里已经出的转交卡、任务卡（老版本 app 不认 card 事件，直接跳过）
            yield sse("card", card)
        if run.progress and not run.done:  # 想到哪、做到哪（老版本 app 不认 progress 事件，直接跳过）
            yield sse("progress", run.progress)
        if run.todo and not run.done:  # ⑫b：这一轮的待办清单、正在等回答的问题（app 现在不认，跳过）
            yield sse("todo", {"items": run.todo})
        if run.asking and not run.done:
            yield sse("ask_user", run.asking)
        if run.text:
            yield sse("delta", {"text": run.text})
        if run.done:
            yield sse("done", done_payload(run))
            return
        while True:
            event, data = await q.get()
            yield sse(event, data)
            if event == "done":
                return
    finally:
        if q in run.queues:
            run.queues.remove(q)


def with_context(context: str, content: str | list) -> str | list:
    """把前情放在这条消息前面：content 是字符串就直接拼，是 content 数组（带图片）就拼进第一段文字。"""
    if isinstance(content, str):
        return f"{context}\n\n{content}"
    first, *rest = content
    return [{**first, "text": f"{context}\n\n{first.get('text', '')}"}, *rest]


def max_rowid(table: str) -> int:
    """表现在的最大 rowid（表还没有 = 0）。回复结束时拿它判断这次回复里新写了哪些行（建议卡、收件箱条目）。"""
    try:
        with _lock, db() as conn:
            return conn.execute(f"SELECT IFNULL(MAX(rowid), 0) FROM {table}").fetchone()[0]
    except sqlite3.Error:
        return 0


def feed_mark() -> int:
    return max_rowid("feed_items")


def start_run(thread: str, text: str, model: str | None, key: str | None = None, attachment_ids: list[str] | None = None, origin: str = "user",
              context: str | None = None, level: str | None = None) -> Run:
    """记下用户这一条，在后台开跑。thread 决定记录存在哪；key 不给就按 thread 推 session key。
    有附件时：图片随消息给模型，文档 / 音频抽出的文字拼进消息，其它只给路径（见 files.py）。
    context：只给模型看的前情（学习台的课件全文之类），拼在消息前面；对话记录里只显示 text。
    level：回完推送的档位（ring / quiet / none），不给按 origin 定：user 响铃、relay 不推、auto 静默（见 push.run_level）。"""
    if (cur := RUNS.get(thread)) and not cur.done:
        raise HTTPException(409, L("上一条还没回完，等它结束或先接回去看。", "The last reply isn't finished yet. Wait for it, or reconnect to see it."))
    if thread.startswith("grp-"):  # 会议的线程：成员各有各的会话，走 /api/meetings（meetings.py），不在这里发给主对话的 agent
        raise HTTPException(400, L("会议消息请用 /api/meetings/{id}/send", "Send meeting messages with /api/meetings/{id}/send"))
    token = gateway_token() if claw.is_openclaw() else ""
    import files as files_mod  # 延迟导入：files.py 依赖本模块
    rows = files_mod.load_pending(thread, attachment_ids or [])
    role = "auto" if origin in ("auto", "relay") else "user"
    content, gw_text = files_mod.build_content(text, rows)
    if daily := daily_context(thread):
        context = f"{daily}\n\n{context}" if context else daily
    if context:
        content, gw_text = with_context(context, content), f"{context}\n\n{gw_text}"
    with _lock, db() as conn:
        model = model or thread_model(conn, thread)
        ts = now_iso()
        user_id = conn.execute("INSERT INTO messages(thread, role, text, model, ts, gw_text) VALUES(?,?,?,?,?,?)",
                               (thread, role, text, None, ts, gw_text if rows or context else None)).lastrowid
        conn.execute("INSERT INTO threads(id, model, updated_at) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at",
                     (thread, model, ts))
    if rows:
        shown = files_mod.bind(rows, user_id)
        with _lock, db() as conn:
            conn.execute("UPDATE messages SET attachments=? WHERE id=?", (json.dumps(shown, ensure_ascii=False), user_id))
        log_activity(L(f"发了 {len(rows)} 个附件给 {settings.app_name}（{'、'.join(r['name'] for r in rows)[:80]}）",
                       f"Sent {len(rows)} attachment{'' if len(rows) == 1 else 's'} to {settings.app_name} ({', '.join(r['name'] for r in rows)[:80]})"), "upload")
    return begin(thread, model, user_id, ts, content, token, key=key, origin=origin, level=level)


def daily_context(thread: str) -> str | None:
    """每天第一句话（和卡片变了以后）要带给模型的：思考主题的碎片和「陪你想」的规矩（think.py）、项目卡（projects.py）。
    OpenClaw 每天重置会话，靠它接上。两个 context_for 都会记下「今天带过了」，所以只在真要发出去的时候调。
    builtin（自己的内核）：Agent 的职责、上一次的日结、项目卡由内核自己备（engine/context.py：放在当天会话的开头，变了往后补），这里不重复。"""
    parts = []
    try:
        import think as think_mod  # 延迟导入：think.py 依赖本模块
        parts.append(think_mod.context_for(thread))
    except Exception:  # noqa: BLE001 — 带不上只是模型少看一眼碎片
        pass
    if not claw.is_builtin():
        try:
            import projects as projects_mod  # 延迟导入：projects.py 依赖本模块
            parts.append(projects_mod.context_for(thread))
        except Exception:  # noqa: BLE001 — 带不上只是模型少看一眼项目卡，消息照发
            pass
    try:
        import meetings as meetings_mod  # 延迟导入：meetings.py 依赖本模块
        parts.append(meetings_mod.context_for(thread))  # 它发起的会议有了纪要、确认了：没告诉过它的带上
    except Exception:  # noqa: BLE001
        pass
    if not claw.is_openclaw() and not claw.is_builtin() and first_today(thread):
        parts += [agent_role(thread), last_digest(thread)]
    return "\n\n".join(x for x in parts if x) or None


def first_today(thread: str) -> bool:
    """这个对话今天（逻辑日）还没说过话：开跑前调，这一句就是今天的第一句。"""
    since = day_bounds(day_of(now_iso()))[0]
    with _lock, db() as conn:
        return conn.execute("SELECT 1 FROM messages WHERE thread=? AND julianday(ts)>=julianday(?) LIMIT 1", (thread, since)).fetchone() is None


DIGEST_DAYS = 7  # 带上一次的日结：最多往回找这么多天


def digest_path(thread: str, day: str) -> Path:
    return settings.data_dir / "digest" / thread.replace("/", "_") / f"{day}.md"


def save_digest(thread: str, day: str, text: str) -> None:
    """别的 claw 的日结：回复原样存成 <data_dir>/digest/<线程>/<逻辑日>.md（临时文件 + 换名）。OpenClaw 的日结 agent 自己写进工作区，不走这里。"""
    p = digest_path(thread, day)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f".{p.name}.tmp")
        tmp.write_text(text.strip() + "\n", encoding="utf8")
        tmp.replace(p)
    except OSError as e:
        print(f"[chat] 日结没存上：{type(e).__name__}")


def last_digest(thread: str) -> str | None:
    """今天之前最近一次的日结（最多往回 DIGEST_DAYS 天）：别的 claw 不一定记得昨天，每天第一句话前面带上它。"""
    today = datetime.strptime(day_of(now_iso()), "%Y-%m-%d").date()
    for i in range(1, DIGEST_DAYS + 1):
        d = (today - timedelta(days=i)).isoformat()
        try:
            text = digest_path(thread, d).read_text(encoding="utf8").strip()
        except OSError:
            continue
        if text:
            return LS(f"（{settings.app_name} 给你的说明，{user_word()}看不到）这个对话上一次的日结（{d}）：\n{text}",
                      f"({settings.app_name}'s note to you; the user doesn't see it) This conversation's last daily digest ({d}):\n{text}")
    return None


def agent_role(thread: str) -> str | None:
    """别的 claw 没有 OpenClaw 那样各自独立的 Agent：一个 Agent = 同一个 claw 的一个单独会话。这个 Agent 的对话今天的第一句话前面
    带上它是谁、管什么（groups 表的名字和职责），history 模式下这一段跟着记录一直带着。不是 Agent 的线程 → None。"""
    with _lock, db() as conn:
        g = conn.execute("SELECT name, purpose FROM groups WHERE id=?", (thread,)).fetchone()
        if not g:
            return None
    who, what = g["name"], (g["purpose"] or "").strip()
    return LS(f"（{settings.app_name} 给你的说明，{user_word()}看不到）这个对话是 Agent「{who}」" + (f"：{what}" if what else "") +
              f"。你就是它：只管这一块，别的事请{user_word()}去主对话说。它的 id 是 {thread}：用 {settings.app_name} 的工具时 agent 填它。",
              f"({settings.app_name}'s note to you; the user doesn't see it) This conversation is the Agent \"{who}\"" + (f": {what}" if what else "") +
              f". You are that Agent: stick to this area and send anything else back to the main chat. Its id is {thread}: "
              f"put it in agent when you use {settings.app_name}'s tools.")


def begin(thread: str, model: str, user_id: int, ts: str, content: str | list, token: str, key: str | None = None, origin: str = "user",
          level: str | None = None, watched: bool | None = None, via: str | None = None) -> Run:
    """开跑：记下这一轮，后台发给 Gateway。watched：有人在看（builtin 的 ask_user）；不给 = 用户自己发的（origin user）就算。
    via：不给 = 这个请求的 REQUEST_VIA（见上）。"""
    run = Run(thread=thread, model=model, user_id=user_id, started=ts, key=key, origin=origin, level=level, feed_mark=feed_mark(),
              inbox_mark=max_rowid("inbox"), handoff_mark=max_rowid("handoffs"), watched=origin == "user" if watched is None else watched,
              via=via if via is not None else REQUEST_VIA.get())
    if claw.is_builtin():  # 自己的内核：插话、停都经它（engine_stream）；现在就建好，还在备料时插的话、点的停也接得住
        from engine import Control  # 延迟导入：内核只在 builtin 时用
        run.control = Control()
    RUNS[thread] = run
    asyncio.create_task(run_gateway(run, content, token))
    try:  # 回复进行中盯着 OpenClaw 的任务台账：这次新派的子任务当场出任务卡（见 cards.py）；别的 claw 没有这本台账
        import cards as cards_mod  # 延迟导入：cards.py 依赖本模块
        if claw.is_openclaw():
            cards_mod.watch(run)
    except Exception:  # noqa: BLE001
        pass
    return run


# —— 回复进行中又发来的：排队，回完合成一轮（2026-09-28）——————————————————————————

BATCH_HEAD = ("（你刚才回复的时候，用户又接着发了 {n} 条，按顺序在下面。都看完再回：分开回答的话，每段开头单独一行写 `> 「他的原话」`"
              "（长的截前 20 个字左右），app 会把它显示成引用、点了跳回那条；说的是一件事就不用引用。）",
              "(While you were replying, the user sent {n} more messages, in order below. Read them all, then reply: when you answer them "
              "separately, start each part with its own line `> 「their words」` (the first 20 or so characters of a long one); the app shows it "
              "as a quote that jumps back to that message. No quote needed if it's all one thing.)")


def busy(thread: str) -> bool:
    cur = RUNS.get(thread)
    return bool(cur and not cur.done)


def enqueue(thread: str, text: str, attachment_ids: list[str], context: str | None) -> Queued:
    """这个线程正在回复：记下这一条（status queued），附件先挂上（气泡里看得到），等这条回完再发。"""
    import files as files_mod  # 延迟导入：files.py 依赖本模块
    rows = files_mod.load_pending(thread, attachment_ids)
    content, gw_text = files_mod.build_content(text, rows)
    if context:
        content, gw_text = with_context(context, content), f"{context}\n\n{gw_text}"
    ts = now_iso()
    with _lock, db() as conn:
        user_id = conn.execute("INSERT INTO messages(thread, role, text, model, ts, status, gw_text) VALUES(?,?,?,?,?,?,?)",
                               (thread, "user", text, None, ts, "queued", gw_text)).lastrowid
        conn.execute("INSERT INTO threads(id, updated_at) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at", (thread, ts))
    if rows:
        shown = files_mod.bind(rows, user_id)
        with _lock, db() as conn:
            conn.execute("UPDATE messages SET attachments=? WHERE id=?", (json.dumps(shown, ensure_ascii=False), user_id))
    item = Queued(user_id=user_id, content=content, gw_text=gw_text, ts=ts, future=asyncio.get_running_loop().create_future())
    QUEUED.setdefault(thread, []).append(item)
    return item


def combine(items: list[Queued]) -> tuple[str | list, str]:
    """排着的几条拼成一轮：一条就是它自己；几条就编号，前面一句说明（让模型分开回答时引用原话）。图片放在最后。"""
    if len(items) == 1:
        return items[0].content, items[0].gw_text
    head = LS(*(h.format(n=len(items)) for h in BATCH_HEAD))
    texts: list[str] = []
    images: list = []
    for i, it in enumerate(items, 1):
        if isinstance(it.content, list):
            first, *rest = it.content
            texts.append(f"[{i}] {first.get('text', '')}")
            images += rest
        else:
            texts.append(f"[{i}] {it.content}")
    body = f"{head}\n\n" + "\n\n".join(texts)
    gw = f"{head}\n\n" + "\n\n".join(f"[{i}] {it.gw_text}" for i, it in enumerate(items, 1))
    return ([{"type": "text", "text": body}, *images] if images else body), gw


def drain(thread: str) -> None:
    """这个线程没在回复了：排着的全部合成一轮发出去，等着的连接都接到这一轮上。
    服务正在关（SHUTTING_DOWN，builtin 才会有）：先不发，库里还是排着的，下次启动 resume_queued 接着发。"""
    items = QUEUED.get(thread) or []
    if not items or busy(thread) or SHUTTING_DOWN:
        return
    QUEUED.pop(thread, None)
    ids = [it.user_id for it in items]
    try:
        token = gateway_token() if claw.is_openclaw() else ""  # 别的 claw 没有 Gateway 令牌（和 begin 一样）
        content, gw_text = combine(items)
        if daily := daily_context(thread):
            content, gw_text = with_context(daily, content), f"{daily}\n\n{gw_text}"
        with _lock, db() as conn:
            model = thread_model(conn, thread)
            # 发出去了：去掉「排队」；gw_text 都记成这一轮实际发的（撤回任何一条都退到这一轮之前）
            conn.execute(f"UPDATE messages SET status='ok', gw_text=? WHERE id IN ({','.join('?' * len(ids))})", (gw_text, *ids))
        run = begin(thread, model, ids[-1], items[0].ts, content, token)
    except Exception as e:  # noqa: BLE001 — 发不出去：记一条出错的回复，等着的连接都拿到它
        run = failed_run(thread, ids, str(getattr(e, "detail", e)))
    for it in items:
        if not it.future.done():
            it.future.set_result(run)


def failed_run(thread: str, ids: list[int], error: str) -> Run:
    ts = now_iso()
    run = Run(thread=thread, model=DEFAULT_MODEL, user_id=ids[-1], started=ts, status="error", error=error, finished=ts, done=True,
              text=L(f"（没发出去：{error}）", f"(Couldn't send: {error})"))
    with _lock, db() as conn:
        conn.execute(f"UPDATE messages SET status='ok' WHERE id IN ({','.join('?' * len(ids))})", ids)
        run.reply_id = conn.execute("INSERT INTO messages(thread, role, text, model, ts, status, origin) VALUES(?,?,?,?,?,?,?)",
                                    (thread, "grava", run.text, None, ts, "error", "user")).lastrowid
    return run


async def queued_stream(thread: str, item: Queued) -> AsyncIterator[bytes]:
    """排队那一条的 SSE：先说排上了；等合成的那一轮开跑，接上去转发它的回复。客户端断了也不影响那一轮。"""
    yield sse("queued", {"userId": f"db{item.user_id}", "time": hhmm(item.ts), "position": len(QUEUED.get(thread) or [])})
    run = await asyncio.shield(item.future)
    async for chunk in attach(run):
        yield chunk


async def steer(thread: str, cur: Run, text: str, context: str | None) -> tuple[int, str, asyncio.Future]:
    """插话：记下这一条（status steered，app 上标「插话」），经对话通道 chat.send queueMode steer。返回 (消息 id, 时间, 结果)。"""
    import gateway_ws as gw_mod
    content = f"{context}\n\n{text}" if context else text
    ts = now_iso()
    with _lock, db() as conn:
        user_id = conn.execute("INSERT INTO messages(thread, role, text, model, ts, status, gw_text) VALUES(?,?,?,?,?,?,?)",
                               (thread, "user", text, None, ts, "steered", content)).lastrowid
    c = gw_mod.client()
    rid = await c.send(cur.key or session_key(thread), content, queue_mode="steer")
    fut = asyncio.get_running_loop().create_future()
    STEERS[rid] = fut
    asyncio.create_task(watch_steer(thread, cur.key, rid, user_id, ts, fut))
    return user_id, ts, fut


async def watch_steer(thread: str, key: str | None, rid: str, user_id: int, ts: str, fut: asyncio.Future) -> None:
    """看插话那一条的 runId：只来一个空的 final = 并进了正在跑的那一轮；它自己开始出东西 = Gateway 没能插进去、排成了单独一轮，
    等我们这边上一轮收完尾，接管它（记成这条消息的回复）。"""
    import gateway_ws as gw_mod
    c = gw_mod.client()
    q = c.watch(rid)
    first = None
    try:
        while first is None:
            try:
                p = await asyncio.wait_for(q.get(), 15 * 60)
            except asyncio.TimeoutError:
                break
            st = p.get("state")
            if st == "agent":  # 进度事件说明不了插进去没有，只看 chat 事件
                continue
            if st == "disconnected":
                break
            if st == "final" and not gw_mod.message_text(p.get("message")):
                break  # 并进去了
            first = p
    finally:
        c.unwatch(rid)
        STEERS.pop(rid, None)
    if first is None:
        if not fut.done():
            fut.set_result(None)
        return
    c.requeue(rid, first)  # 这一轮自己的事件：交给接管它的 run
    while busy(thread):  # Gateway 是在我们上一轮结束后才开始这一轮的：等这边收完尾
        await asyncio.sleep(0.2)
    with _lock, db() as conn:
        model = thread_model(conn, thread)
    run = Run(thread=thread, model=model, user_id=user_id, started=ts, key=key, origin="user", feed_mark=feed_mark(),
              inbox_mark=max_rowid("inbox"), handoff_mark=max_rowid("handoffs"), gw_run=rid)
    RUNS[thread] = run
    asyncio.create_task(run_gateway(run, None, ""))
    if not fut.done():
        fut.set_result(run)


async def steered_stream(thread: str, user_id: int, ts: str, fut: asyncio.Future) -> AsyncIterator[bytes]:
    """插话那一条的 SSE：先说插进去了；等知道结果——并进了正在跑的那一轮就接到它上面，变成了单独一轮就接那一轮。"""
    yield sse("queued", {"userId": f"db{user_id}", "time": hhmm(ts), "steer": True})
    got = await asyncio.shield(fut)
    run = got if isinstance(got, Run) else RUNS.get(thread)
    if run is None:
        return
    async for chunk in attach(run):
        yield chunk


async def resume_ws() -> None:
    """服务启动时（走对话通道的）：最近 30 分钟里发出去还没拿到回复的，Gateway 那边的这一轮没被重启掐断——
    还在跑就接管它（app 能接上看），已经回完了就从 chat.history 把回复补进来。"""
    if transport() != "ws":
        return
    import gateway_ws as gw_mod
    cutoff = (datetime.now(TZ) - timedelta(minutes=QUEUE_RESUME_MINUTES)).isoformat(timespec="seconds")
    with _lock, db() as conn:
        rows = conn.execute("""SELECT m.* FROM messages m JOIN (SELECT thread, MAX(id) id FROM messages GROUP BY thread) last ON last.id = m.id
            WHERE m.role='user' AND m.status IN ('ok','steered') AND julianday(m.ts) >= julianday(?)""", (cutoff,)).fetchall()
    for r in rows:
        thread = r["thread"]
        if busy(thread):
            continue
        key = session_key(thread)
        try:
            h = await gw_mod.client().history(key, 30)
        except Exception:  # noqa: BLE001
            continue
        info = h.get("sessionInfo") or {}
        active = info.get("activeRunIds") or []
        since = datetime.fromisoformat(r["ts"]).timestamp()
        if not active and info.get("hasActiveRun"):  # 在跑、但 Gateway 没给 runId：隔几秒看一次，回完了补进来
            asyncio.create_task(wait_reply(thread, key, since))
            continue
        if active:
            with _lock, db() as conn:
                model = thread_model(conn, thread)
            run = Run(thread=thread, model=model, user_id=r["id"], started=r["ts"], origin="user", feed_mark=feed_mark(),
                      inbox_mark=max_rowid("inbox"), handoff_mark=max_rowid("handoffs"), gw_run=str(active[0]))
            RUNS[thread] = run
            asyncio.create_task(run_gateway(run, None, ""))
            continue
        text = await recover_reply(key, None, since)
        if text:
            save_recovered(thread, text)


async def wait_reply(thread: str, key: str, since: float, minutes: int = 15) -> None:
    """重启后接不上的那一轮：每 5 秒看一次 chat.history，它回完了就把回复补进来。"""
    end = time.time() + minutes * 60
    while time.time() < end:
        await asyncio.sleep(5)
        text = await recover_reply(key, None, since)
        if text is None:
            continue
        if text:
            save_recovered(thread, text)
        return


def save_recovered(thread: str, text: str) -> None:
    with _lock, db() as conn:
        conn.execute("INSERT INTO messages(thread, role, text, model, ts, status, origin) VALUES(?,?,?,?,?,?,?)",
                     (thread, "grava", text, None, now_iso(), "ok", "user"))
    log_activity(L("服务重启时补回了一条回复", "Recovered a reply after a server restart"), "edit")


def resume_engine() -> None:
    """服务启动时（main.py，在 resume_queued 之前）：上次停机时自己的内核还在跑的回合。两种：
    - 进程被杀（OOM、断电）：事件表里还是 running——engine/loop.recover 标成 aborted、会话补完整（没结果的工具调用各补一条
      「可能做了、可能没做」，再补一句「上一轮被重启打断了」）；
    - 正常关服务（systemd stop、重启）：asyncio 当时取消了它，内核已经补好会话、记成 aborted / cancelled（events.cancelled 找回来，
      app 那边没拿到回复的再补那句「被重启打断了」）。
    app 这边和 OpenClaw 重启后对齐：这一轮的那句没有回复的，补一条没拿到回复（status error），已经说出来、存进会话的几段字带上
    （正在流的半截在内存里，没了）；插进去还没被它读到的话（status steered、会话里没有）当成排着的，交给 resume_queued 接着发
    （QUEUE_RESUME_MINUTES 以内；更早的它标成没发出去）。这一轮本身不重跑：工具可能已经做了一半，重跑会做两遍。
    没用过内核（库里没有内核的表）什么都不做；跑两遍也只补一次（看的是 app 里最后一条回复之后还有没有没回的话）。"""
    try:
        from engine import events as engine_events  # 延迟导入：内核只在用到时加载
        from engine import loop as engine_loop
        from engine import owner as engine_owner
        killed = engine_loop.recover()
        stopped = engine_events.cancelled()
    except Exception as e:  # noqa: BLE001 — 收不了尾不拦着服务启动
        print(f"[chat] 内核的回合重启后没收好尾：{type(e).__name__}: {str(e)[:160]}")
        return
    for t in [*killed, *({**x, "cancelled": True} for x in stopped)]:
        if t.get("kind") != "owner" or not t.get("session"):
            continue  # 赛博、外人回合、一问一答（不存会话）：不在 app 的对话里
        try:
            if settle_restarted(t, engine_owner.said(t["id"]), engine_owner.refs(t["session"])) and t.get("cancelled"):
                engine_loop.note_restart(t["id"], t["session"])
        except Exception as e:  # noqa: BLE001
            print(f"[chat] {t.get('conversation')}：重启打断的那一轮没补上回复：{type(e).__name__}: {str(e)[:160]}")
    try:
        engine_events.checked([t["id"] for t in stopped])  # 每个只看一次
    except Exception:  # noqa: BLE001
        pass


def settle_restarted(t: dict, said: str, refs: set[int]) -> bool:
    """重启打断的一轮（resume_engine）：app 里这个对话最后一条回复之后还有没回的话，就补一条没拿到回复，没被读到的插话改成排着的。
    → 补了没有（没有没回的话 = 那一轮的回复已经入库了，比如点停以后被取消的那种）。"""
    thread = t["conversation"]
    with _lock, db() as conn:
        last = conn.execute("SELECT IFNULL(MAX(id), 0) FROM messages WHERE thread=? AND role='grava'", (thread,)).fetchone()[0]
        asked = conn.execute("SELECT id, status FROM messages WHERE thread=? AND id>? AND role IN ('user','auto') AND status IN ('ok','steered') "
                             "ORDER BY id", (thread, last)).fetchall()
        if not asked:
            return False
        late = [r["id"] for r in asked if r["status"] == "steered" and r["id"] not in refs]
        if late:
            conn.execute(f"UPDATE messages SET status='queued' WHERE id IN ({','.join('?' * len(late))})", late)
        why = L("服务重启了", "the server restarted")
        text = (f"{said}\n\n" + L("（服务重启了，这一轮没做完）", "(The server restarted; this turn didn't finish.)") if said
                else L(f"（没拿到回复：{why}）", f"(Didn't get a reply: {why})"))
        conn.execute("INSERT INTO messages(thread, role, text, model, requested, ts, status, origin) VALUES(?,?,?,?,?,?,?,?)",
                     (thread, "grava", text, t.get("model"), t.get("model"), now_iso(), "error", t.get("origin") or "user"))
    log_activity(L("服务重启，打断了一轮正在进行的回复", "A server restart cut off a reply in progress"), "failed")
    return True


def resume_queued() -> None:
    """服务启动时：库里还排着的（上次重启前没来得及发的）。QUEUE_RESUME_MINUTES 以内的按线程合成一轮接着发（附件只剩文字），更早的标成没发出去。"""
    cutoff = time.time() - QUEUE_RESUME_MINUTES * 60  # 按真时间比：重启前后时区可能改过（见 day_bounds）
    with _lock, db() as conn:
        rows = conn.execute("SELECT id, thread, text, gw_text, ts FROM messages WHERE status='queued' ORDER BY id").fetchall()
        old = [r["id"] for r in rows if instant(r["ts"]) < cutoff]
        if old:
            conn.execute(f"UPDATE messages SET status='error' WHERE id IN ({','.join('?' * len(old))})", old)
    loop = asyncio.get_running_loop()
    for r in rows:
        if instant(r["ts"]) >= cutoff:
            QUEUED.setdefault(r["thread"], []).append(Queued(user_id=r["id"], content=r["gw_text"] or r["text"], gw_text=r["gw_text"] or r["text"],
                                                             ts=r["ts"], future=loop.create_future()))
    for thread in list(QUEUED):
        drain(thread)


@router.post("/api/chat/send")
async def send(body: SendBody):
    text = body.text.strip()
    if not text and not body.attachments:
        raise HTTPException(400, L("空消息", "Empty message"))
    if not text:
        text = "（见附件）"  # 不翻译：app 按这串原文隐藏占位（ChatView PLACEHOLDER_TEXT）；给模型的那份在 files.build_content 按语言换
    reply_to = follow = None
    if body.inboxId:  # 引用收件箱的卡回复：对话记录里只有用户的话，模型另外看到「这是在回复哪一条、改好 / 做完怎么交」
        import inbox as inbox_mod  # 延迟导入：inbox.py 依赖本模块
        reply_to = inbox_mod.reply_context(body.inboxId)  # 还没定下来的：改一下
        follow = None if reply_to else inbox_mod.follow_context(body.inboxId)  # 定下来的：跟进（「已处理」里点的）
    quoted = reply_to or follow
    context = quoted[1] if quoted else None
    if body.ref and not quoted:  # 「要记得的」里点「不对？跟它说」带过来的
        import schedule as schedule_mod  # 延迟导入：schedule.py 依赖本模块
        context = await asyncio.to_thread(schedule_mod.ref_context, body.ref)
    if body.study and not quoted:  # 学习台的加课向导、学习屏旁边的对话：哪门课、第几步
        import courses as courses_mod  # 延迟导入：courses.py 依赖本模块
        context = await asyncio.to_thread(courses_mod.chat_context, body.study)
    if body.save and not quoted:  # 收藏里点「问问」「翻译」带过来的
        import saves as saves_mod  # 延迟导入：saves.py 依赖本模块
        context = await asyncio.to_thread(saves_mod.save_context, body.save)
    quote, quote_id = quote_context(body.thread, body.replyTo)  # 长按「引用」着发的：原话给模型
    if quote:
        context = f"{quote}\n\n{context}" if context else quote
    cur = RUNS.get(body.thread)
    steered = None
    if body.origin == "user" and busy(body.thread) and cur and cur.control is not None and body.attachments and cur.control.asking():
        cur.control.answer({"cancel": True})  # ⑫b：ask_user 在等，用户发来带附件的一条：不算回答，问题收起来，这一条照常排成下一轮
    if body.origin == "user" and busy(body.thread) and cur and cur.control is not None and not body.attachments and not QUEUED.get(body.thread):
        # 自己的内核、它正在回我们的上一条：插话——这一批工具做完带着这句再问模型，还是同一条回复（进不去就走下面的排队）
        steered = steer_engine(body.thread, cur, text, context)
    if steered:
        user_id, ts, fut = steered
        stream = steered_stream(body.thread, user_id, ts, fut)
    elif body.origin == "user" and busy(body.thread) and cur and cur.gw_run and not body.attachments and not QUEUED.get(body.thread) and transport() == "ws":
        # 走对话通道、它正在回我们的上一条：插话——Gateway 在这一轮的下一步把这句交给模型，还是同一条回复
        user_id, ts, fut = await steer(body.thread, cur, text, context)
        stream = steered_stream(body.thread, user_id, ts, fut)
    elif body.origin == "user" and (busy(body.thread) or QUEUED.get(body.thread)):
        # 这个线程正在回复：不再 409，先记下、标「排队」，这条回完和排着的合成一轮发；连接等着接那一轮的回复
        item = enqueue(body.thread, text, body.attachments, context)
        user_id, stream = item.user_id, queued_stream(body.thread, item)
        drain(body.thread)  # 万一刚好回完了（只剩排着的）：现在就发
    else:
        run = start_run(body.thread, text, body.model, attachment_ids=body.attachments, origin=body.origin, context=context)
        user_id, stream = run.user_id, attach(run)
    if quote_id:
        with _lock, db() as conn:
            conn.execute("UPDATE messages SET reply_to=? WHERE id=?", (quote_id, user_id))
    if reply_to:
        inbox_mod.mark_revising(reply_to[0], text)
    elif follow:
        inbox_mod.mark_followed(follow[0], text)
    return StreamingResponse(stream, media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


def quote_context(thread: str, ref: str | None) -> tuple[str | None, int | None]:
    """长按「引用」着发的：引的是这个对话里哪条（"db<id>"）。返回 (只给模型看的前情, 消息 id)；找不到就 (None, None)。"""
    if not ref or not ref.startswith("db") or not ref[2:].isdigit():
        return None, None
    with _lock, db() as conn:
        r = conn.execute("SELECT id, role, text FROM messages WHERE id=? AND thread=?", (int(ref[2:]), thread)).fetchone()
    if not r:
        return None, None
    snippet = " ".join(r["text"].split())
    snippet = snippet[:120] + ("…" if len(snippet) > 120 else "")
    mine = r["role"] == "user"
    return LS(f"（这条是接着对话里{'他' if mine else '你'}之前的这句说的：「{snippet}」）",
              f'(This message refers back to {"their" if mine else "your"} earlier line in this chat: "{snippet}")'), r["id"]


class StopBody(BaseModel):
    thread: str


@router.post("/api/chat/stop")
async def stop(body: StopBody):
    """停掉这个线程正在进行的回复：断开到 Gateway 的连接（Gateway 就中止这一轮），已经说了的留着、末尾标「停了」。排着的消息接着发。
    自己的内核（builtin）：Control.abort()——正在流的字留下、正在跑的工具收到取消（子进程一起杀掉）、会话补完整；这一轮照常收尾、入库。
    等你点头的收件箱卡不受影响。abort 打不断的地方（等会话锁、换天时等前一天那一轮）STOP_GRACE 秒后直接取消。"""
    run = RUNS.get(body.thread)
    if not run or run.done:
        return {"ok": True, "stopped": False}
    run.stopping = True
    if run.control is not None:
        run.control.abort()
        asyncio.get_running_loop().call_later(STOP_GRACE, hard_stop, run)
    elif run.gw_run:  # WebSocket 那条路：让 Gateway 中止这一轮（收到 aborted 事件这一轮就结束）；它不认再断连接
        import gateway_ws as gw_mod
        try:
            await gw_mod.client().abort(run.key or session_key(run.thread), run.gw_run)
        except Exception:  # noqa: BLE001
            if run.stream_task and not run.stream_task.done():
                run.stream_task.cancel()
    elif run.stream_task and not run.stream_task.done():
        run.stream_task.cancel()
    log_activity(L("停掉了一条正在进行的回复", "Stopped a reply in progress"), "edit")
    return {"ok": True, "stopped": True}


STOP_GRACE = 5.0  # builtin：点了停这么多秒还没收尾就直接取消


def hard_stop(run: Run) -> None:
    if not run.done and run.stream_task and not run.stream_task.done():
        run.stream_task.cancel()


# —— 关服务（builtin）：先把自己内核正在跑的回合停下 ——————————————————————————————————
# uvicorn 收到 SIGTERM：先关端口，再等连接关完（app 正接着的回复要等它跑完），最后把 SIGTERM 原样再发给自己、进程当场退出
# （asyncio 来不及取消剩下的任务）。不管的话：这期间那一轮还在跑，调自己接口的工具全都连不上，模型对着一串「连不上」接着说，
# 然后进程直接没了（下次启动按 resume_engine 收尾）。所以收到信号就先像点了停那样停下（已经出来的字留着、会话补完整、回复入库），
# 连接跟着结束，关服务不用等；lifespan 关的时候再等它们收完尾。OpenClaw 的回复不在这台机器上跑，不用管。

SHUTTING_DOWN = False
SHUTDOWN_WAIT = 8.0  # 关服务时最多等停下的回合收尾几秒


def stop_engine_runs() -> int:
    """把自己内核正在跑的回合都停下（关服务）：像点了停，回复末尾写「服务重启了」。排着的先不发（drain 看 SHUTTING_DOWN）。
    对外接口（engine/openai_api.py，开了才导入）正在跑的回合也停，它们的连接跟着结束。→ 停了几个。"""
    global SHUTTING_DOWN
    SHUTTING_DOWN = True
    n = 0
    for run in list(RUNS.values()):
        if not run.done and run.control is not None and not run.stopping:
            run.stopping = run.shutdown = True
            run.control.abort()
            n += 1
    try:  # 后台任务的子 Agent 也停下（第 5 项）：结果下次启动再交回对话
        if engine_tasks := loaded_tasks():
            n += engine_tasks.shutdown()
    except Exception:  # noqa: BLE001
        pass
    api = sys.modules.get("engine.openai_api")  # 对外接口（第 14 项）的回合也停下
    if api is not None:
        n += api.stop_all()
    return n


def loaded_tasks():
    """内核的后台任务模块（engine/tasks.py，第 5 项），只在已经加载过时给：没加载过 = 一个任务都没派过（builtin 启动时
    main.py 就会加载它）。关服务的路上不现导入它（第一次导入要上百毫秒，机器忙时更久）。"""
    return sys.modules.get("engine.tasks")


def watch_shutdown() -> bool:
    """服务启动时（main.py，claw 是 builtin 才调）：接在 uvicorn 自己的 SIGTERM / SIGINT 处理前面，收到就先 stop_engine_runs。
    只接 uvicorn.Server 用 signal.signal 装的那个（0.29 起都是）；认不出就不接，照旧（lifespan 关的时候 engine_shutdown 还会停一次）。"""
    loop = asyncio.get_running_loop()
    hooked = False
    for sig in (signal.SIGTERM, signal.SIGINT):
        old = signal.getsignal(sig)
        if not callable(old) or type(getattr(old, "__self__", None)).__module__.split(".")[0] != "uvicorn":
            continue

        def handler(signum, frame, _old=old) -> None:
            try:
                loop.call_soon_threadsafe(stop_engine_runs)
            except RuntimeError:  # 事件循环已经关了
                pass
            _old(signum, frame)
        signal.signal(sig, handler)
        hooked = True
    return hooked


async def engine_shutdown() -> None:
    """lifespan 关的时候（main.py）：还在跑的内核回合（和后台任务的子 Agent、对外接口的回合）停下，等它们收完尾、回复入库
    （最多 SHUTDOWN_WAIT 秒）。没有就马上返回。"""
    engine_tasks = loaded_tasks()
    api = sys.modules.get("engine.openai_api")

    def busy_tasks() -> bool:
        try:
            return bool(engine_tasks and engine_tasks.busy())
        except Exception:  # noqa: BLE001
            return False

    def running() -> bool:
        return (any(not r.done and r.control is not None for r in RUNS.values()) or busy_tasks()
                or bool(api is not None and api.busy()))
    if not running():
        return
    stop_engine_runs()
    end = time.monotonic() + SHUTDOWN_WAIT
    while time.monotonic() < end and running():
        await asyncio.sleep(0.05)


@router.get("/api/chat/busy")
def busy_threads():
    """正在回复的线程和排着的消息数。safe_restart.py 等它们都没了再重启服务（重启会掐断进行中的回复）。"""
    running = [t for t, r in RUNS.items() if not r.done]
    if claw.is_builtin():  # 自己内核的后台任务（第 5 项）：重启会打断它们（下次启动交回去说被打断了），safe_restart 等它们做完
        try:
            from engine import tasks as engine_tasks
            running += [f"task:{tid}" for tid in engine_tasks.running_ids() if f"task:{tid}" not in running]
        except Exception:  # noqa: BLE001
            pass
    try:  # 正在进行的会议（成员在发言、在写纪要）
        import meetings as meetings_mod  # 延迟导入：meetings.py 依赖本模块
        running += meetings_mod.busy()
    except Exception:  # noqa: BLE001
        pass
    queued = {t: len(v) for t, v in QUEUED.items() if v}
    for t, n in RELAY_WAITING.items():  # 排队等着转进去的转交（重启会把它们丢掉）
        if n:
            queued[t] = queued.get(t, 0) + n
    return {"ok": True, "running": running, "queued": queued, "idle": not running and not queued}


LEVELS = ("ring", "quiet", "none")  # 推送档位：响铃 / 静默（进通知中心不出声）/ 不推


@router.post("/api/chat/trigger")
async def trigger(body: SendBody):
    """系统触发（suggestion_watcher 等）：记一条 auto 消息、后台开跑，立刻返回，不流式。
    回完的推送按 level：ring / quiet / none；只给了 notify=false 等于 none；都没给 = quiet（半夜的日结不再响铃）。"""
    text = body.text.strip()
    if not text:
        raise HTTPException(400, L("空消息", "Empty message"))
    if body.level is not None and body.level not in LEVELS:
        raise HTTPException(400, L("level 只能是 ring / quiet / none", "level must be ring, quiet or none"))
    level = body.level or ("none" if body.notify is False else "quiet")
    run = start_run(body.thread, text, body.model, origin="auto", level=level)
    run.digest = body.digest and not claw.is_openclaw()  # 回复要等后台跑完：现在标上来得及
    return {"ok": True, "thread": body.thread, "userId": f"db{run.user_id}", "modelId": run.model, "level": level}


class RelayBody(BaseModel):
    thread: str
    text: str
    timeout: int = 150


RELAY_WAIT_S = 15 * 60          # 转交排队最多等多久（和收件箱 start_when_free 一样）
RELAY_WAITING: dict[str, int] = {}  # 线程 → 排队等着转进去的问题数（busy_threads 报给 safe_restart，重启前等它们转完）


def relay_slot_free(thread: str) -> bool:
    """这个线程能接转交了：没在回复，也没有用户排着的消息（用户的先发）。"""
    return not busy(thread) and not QUEUED.get(thread)


async def relay_when_free(thread: str, text: str, deadline: float) -> Run | None:
    """排队：每秒看一次那个 Agent 空下来没有，空了就转进去。到 deadline 还没轮到回 None。"""
    while time.time() < deadline:
        if relay_slot_free(thread):
            try:
                return start_run(thread, PREFIX_RELAY + text, None, origin="relay", level="none")
            except HTTPException as e:
                if e.status_code != 409:  # 409 = 被别的抢先了，接着等
                    raise
        await asyncio.sleep(1)
    return None


_relay_tasks: set[asyncio.Task] = set()


async def relay_later(thread: str, text: str, hid: str | None, until: float) -> None:
    """relay 等到 timeout 还没轮到：后台接着排，最多到 until；转进去以后回答照样进它的对话，卡跟着更新（on_run_end）。"""
    import cards as cards_mod
    try:
        run = await relay_when_free(thread, text, until)
        if run:
            cards_mod.handoff_begin(hid, run)
        else:
            cards_mod.handoff_dropped(hid, L("排队超过 15 分钟，未转交", "Waited over 15 minutes; not handed over"))
            log_activity(L(f"转给「{thread}」的问题排队超过 15 分钟，未转交", f'A question for "{thread}" waited over 15 minutes and was not handed over'), "failed")
    except Exception as e:  # noqa: BLE001
        cards_mod.handoff_dropped(hid, str(e)[:200])
    finally:
        RELAY_WAITING[thread] = max(0, RELAY_WAITING.get(thread, 1) - 1)


@router.post("/api/chat/relay")
async def relay(body: RelayBody):
    """主对话把问题转给某个 Agent：记进那个 Agent 的线程（role=auto，app 里显示成「主对话转来」），等它答完，把答案带回去。
    不推送（用户在主对话里等着）。超时 → 200 但 status=timeout，回复仍会在后台完成并入库。
    那个 Agent 正在回复（2026-10-05 起不再 409）：排进它的队列，空下来就转进去，整个过程都算在 timeout 里；到时还没轮到 →
    200、status=queued，后台接着排（最多 15 分钟），回答进它的对话。
    同时记一张转交卡（cards.py）：发起转交的那次回复当场出「正在问 …」（排队时是「排队中」），答完变成「转给了 …」，挂在那条回复下面。"""
    text = body.text.strip()
    if not text:
        raise HTTPException(400, L("空消息", "Empty message"))
    import cards as cards_mod  # 延迟导入：cards.py 依赖本模块
    t0 = time.time()
    deadline = t0 + min(max(body.timeout, 10), 600)
    run: Run | None = None
    if relay_slot_free(body.thread):
        try:
            run = start_run(body.thread, PREFIX_RELAY + text, None, origin="relay", level="none")
        except HTTPException as e:
            if e.status_code != 409:
                raise
    if run is None:  # 那个 Agent 正忙：排队
        hid = cards_mod.handoff_start(body.thread, text, None, status="queued")
        RELAY_WAITING[body.thread] = RELAY_WAITING.get(body.thread, 0) + 1
        try:
            run = await relay_when_free(body.thread, text, deadline)
        except BaseException:
            RELAY_WAITING[body.thread] = max(0, RELAY_WAITING.get(body.thread, 1) - 1)
            cards_mod.handoff_dropped(hid, L("未转交", "Not handed over"))
            raise
        if run is None:
            task = asyncio.create_task(relay_later(body.thread, text, hid, t0 + RELAY_WAIT_S))
            _relay_tasks.add(task)
            task.add_done_callback(_relay_tasks.discard)
            return {"ok": False, "status": "queued", "thread": body.thread, "seconds": round(time.time() - t0, 1)}
        RELAY_WAITING[body.thread] = max(0, RELAY_WAITING.get(body.thread, 1) - 1)
        cards_mod.handoff_begin(hid, run)
    else:
        cards_mod.handoff_start(body.thread, text, run)
    while not run.done and time.time() < deadline:
        await asyncio.sleep(0.5)
    if not run.done:
        return {"ok": False, "status": "timeout", "thread": body.thread, "text": run.text, "seconds": round(time.time() - run.t0, 1)}
    return {"ok": run.status == "ok", "status": run.status, "thread": body.thread, "text": run.text, "error": run.error,
            "modelId": run.model, "seconds": round(time.time() - run.t0, 1), "replyId": f"db{run.reply_id}" if run.reply_id else None}


@router.get("/api/chat/stream")
async def stream(thread: str = "main"):
    """重新接上正在进行的回复（app 切后台回来、断网重连时用）。刚回完的（15 分钟内）也能接上，直接拿到全文；再没有就 204。"""
    run = RUNS.get(thread)
    if not run:
        return StreamingResponse(iter(()), status_code=204)
    return StreamingResponse(attach(run), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


class MessageRef(BaseModel):
    thread: str = "main"
    id: str


def row_id(ref: str) -> int:
    if not ref.startswith("db") or not ref[2:].isdigit():
        raise HTTPException(400, L("这条消息还没存进服务器，刷新后再试", "This message isn't saved on the server yet. Refresh and try again."))
    return int(ref[2:])


@router.post("/api/chat/delete")
def delete_message(body: MessageRef):
    """只从 app 的对话记录里删掉这一条，Gateway 会话（模型记得的内容）不变。"""
    with _lock, db() as conn:
        n = conn.execute("DELETE FROM messages WHERE thread=? AND id=?", (body.thread, row_id(body.id))).rowcount
    if n:
        log_activity(L("从对话记录里删了 1 条消息", "Deleted 1 message from the chat history"), "deleted")
    return {"ok": True, "deleted": n}


async def run_cli(argv: list[str], timeout: float) -> tuple[int, bytes, bytes]:
    """跑一个命令行（openclaw …）→ (退出码, stdout, stderr)。超时或请求被取消（客户端断开）时连它起的子进程一起杀掉：
    openclaw 外壳会再起一个干活的 node（约 160 MB），只杀外壳那个会留下来；Gateway 一慢，轮询每次起一个、越积越多
    （2026-09-28 18:10 服务器 OOM 时堆了 7 个）。自己一个进程组，超时就整组 SIGKILL。"""
    proc = await asyncio.create_subprocess_exec(*argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=True)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    finally:
        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(proc.pid, signal.SIGKILL)
    return proc.returncode or 0, out, err


async def gateway_call(method: str, params: dict, timeout: float = 30) -> dict:
    if not claw.is_openclaw():  # 只有 OpenClaw 有 Gateway 的这些方法；别的 claw 当成「没有这项」，调用方各自降级
        raise HTTPException(501, L(f"{claw.name()} 没有这项（Gateway {method} 只有 OpenClaw 有）", f"{claw.name()} doesn't have this (Gateway {method} is OpenClaw-only)"))
    exe = shutil.which(settings.openclaw_bin) or settings.openclaw_bin
    code, out, err = await run_cli([exe, "gateway", "call", method, "--json", "--params", json.dumps(params, ensure_ascii=False)], timeout)
    if code != 0:
        detail = (err or out).decode("utf8", "replace")[-300:]
        raise HTTPException(502, L(f"Gateway {method} 失败：{detail}", f"Gateway {method} failed: {detail}"))
    return json.loads(out)


def plain_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text")
    return ""


async def find_entry(thread: str, text: str, ts: str) -> str | None:
    """在 Gateway 会话里找到这条用户消息的 entry id：文字相同、时间最接近（主会话里还有 Telegram 的消息）。"""
    hist = await gateway_call("chat.history", {"sessionKey": session_key(thread), "limit": 300})
    want = datetime.fromisoformat(ts).timestamp() * 1000
    best: tuple[float, str] | None = None
    for m in hist.get("messages", []):
        if m.get("role") != "user" or plain_text(m.get("content")).strip() != text.strip():
            continue
        gap = abs((m.get("timestamp") or 0) - want)
        eid = (m.get("__openclaw") or {}).get("id")
        if eid and gap < 10 * 60 * 1000 and (best is None or gap < best[0]):
            best = (gap, eid)
    return best[1] if best else None


@router.post("/api/chat/rewind")
async def rewind(body: MessageRef):
    """撤回 / 重新编辑：会话退回到这条用户消息之前，它和之后的记录一起删掉，原文返回给输入框。"""
    rid = row_id(body.id)
    if (cur := RUNS.get(body.thread)) and not cur.done:
        raise HTTPException(409, L(f"{settings.app_name} 还在回复，等它回完再撤回", f"{settings.app_name} is still replying. Wait until it's done to unsend."))
    with _lock, db() as conn:
        r = conn.execute("SELECT * FROM messages WHERE thread=? AND id=?", (body.thread, rid)).fetchone()
    if not r:
        raise HTTPException(404, L("找不到这条消息", "Message not found"))
    if r["role"] != "user":
        raise HTTPException(400, L("只能撤回自己发的消息", "You can only unsend your own messages"))
    entry = await find_entry(body.thread, (r["gw_text"] if "gw_text" in r.keys() and r["gw_text"] else r["text"]), r["ts"]) if claw.is_openclaw() else None
    if entry:
        await gateway_call("sessions.rewind", {"sessionKey": session_key(body.thread), "entryId": entry})
    elif claw.is_builtin():  # 自己的内核：那一天的会话换叶子，回到这句话之前（库里那一枝还留着）
        from engine import owner as engine_owner
        entry = engine_owner.rewind(body.thread, day_of(r["ts"]), rid) or None
    with _lock, db() as conn:
        n = conn.execute("DELETE FROM messages WHERE thread=? AND id>=?", (body.thread, rid)).rowcount
    tail = L(f"，{settings.app_name} 的会话也退回到那之前", f"; {settings.app_name}'s session was rewound to before them too") if entry else ""
    log_activity(L(f"撤回了 {n} 条消息{tail}", f"Unsent {n} message{'' if n == 1 else 's'}{tail}"), "deleted")
    return {"ok": True, "text": r["text"], "removed": n, "rewound": bool(entry)}
