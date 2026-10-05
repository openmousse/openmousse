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
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from dataclasses import dataclass, field
from typing import AsyncIterator

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
router = APIRouter()
_lock = threading.Lock()


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
    progress: dict | None = None  # 走对话通道时：准备阶段、工具步骤、最近的思考摘要（note_progress），客户端重新接上时补发

    def publish(self, item: tuple[str, dict]) -> None:
        for q in list(self.queues):
            q.put_nowait(item)


RUNS: dict[str, Run] = {}
RUN_KEEP_SECONDS = 15 * 60  # 回完的回复留一会儿：手机断线后重新接上还能拿到完整内容，不至于显示"没发出去"


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
    """线程归哪个 agent：main 和独立空间在 main，Group 各自一个，学习台归 study.agent。groups 表里有但配置里没建 agent 的先留在 main。"""
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
    """一条消息属于哪个逻辑日（YYYY-MM-DD，伦敦时间，04:00 为界）。"""
    try:
        dt = datetime.fromisoformat(ts).astimezone(TZ)
    except ValueError:
        return ts[:10]
    return (dt - timedelta(hours=DAY_START_HOUR)).strftime("%Y-%m-%d")


def day_bounds(day: str) -> tuple[str, str]:
    """逻辑日 day 对应的 [起, 止) ISO 时间（含时区）。"""
    d = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=TZ, hour=DAY_START_HOUR)
    return d.isoformat(), (d + timedelta(days=1)).isoformat()


def hhmm(ts: str) -> str:
    return ts[11:16]


def row_to_msg(r: sqlite3.Row) -> dict:
    fallback = r["requested"] if r["requested"] and r["requested"] != r["model"] else None
    att = r["attachments"] if "attachments" in r.keys() else None
    return {"id": f"db{r['id']}", "role": "user" if r["role"] == "user" else "auto" if r["role"] == "auto" else "grava", "text": r["text"],
            "modelId": r["model"], "fallbackFrom": fallback, "time": hhmm(r["ts"]), "status": r["status"],
            "attachments": json.loads(att) if att else None}


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
            # ts 是带时区的 ISO 字符串，同一时区下字符串比较等价于时间比较（伦敦夏令时切换的那两小时忽略）
            rows = conn.execute("SELECT * FROM messages WHERE thread=? AND ts>=? AND ts<? ORDER BY id DESC LIMIT 1000", (thread, lo, hi)).fetchall()
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
    hits.sort(key=lambda h: h["ts"], reverse=True)
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
    """别的 claw（claw.py，OpenAI 兼容接口）：history 模式把这个对话今天在这一条之前的记录一起发过去，claw 自己不用记会话。"""
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


PROGRESS_STEPS = 6       # 进度里最多留最近几步
PROGRESS_THOUGHT = 600   # 思考摘要留最后多少字


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
        detail = str(d.get("meta") or "").strip()
        if name in ("read", "write", "edit", "apply_patch") and detail:
            detail = re.sub(r"^(from|to|in)\s+", "", detail)
            detail = detail.rstrip("/").rsplit("/", 1)[-1]
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
        use_ws = text is None or ws_ok(text)
        run.stream_task = asyncio.create_task(gateway_ws_stream(run, text) if use_ws else gateway_stream(run, text, token))
    try:
        await run.stream_task
    except asyncio.CancelledError:
        if not run.stopping:  # 不是用户点的「停」（服务关了之类）：照常往外抛
            raise
        run.status = "stopped"
        run.level = "none"  # 自己停的不用推
    except (httpx.HTTPError, RuntimeError, OSError) as e:  # noqa: BLE001
        run.status = "error"
        run.error = str(e)
    except Exception as e:  # noqa: BLE001 — WebSocket 那条路的错（连不上、Gateway 拒了）：这一条记成没拿到回复
        run.status = "error"
        run.error = str(e)
    if run.status == "stopped":
        run.text = (run.text.rstrip() + "\n\n" if run.text.strip() else "") + L("（停了）", "(Stopped)")
    if run.status == "error" and not run.text:
        run.text = L(f"（没拿到回复：{run.error}）", f"(Didn't get a reply: {run.error})")
    run.requested = run.model
    if run.status == "ok":
        run.model = await actual_model(run.key or session_key(run.thread)) or run.model
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
    OpenClaw 每天重置会话，靠它接上。两个 context_for 都会记下「今天带过了」，所以只在真要发出去的时候调。"""
    parts = []
    try:
        import think as think_mod  # 延迟导入：think.py 依赖本模块
        parts.append(think_mod.context_for(thread))
    except Exception:  # noqa: BLE001 — 带不上只是模型少看一眼碎片
        pass
    try:
        import projects as projects_mod  # 延迟导入：projects.py 依赖本模块
        parts.append(projects_mod.context_for(thread))
    except Exception:  # noqa: BLE001 — 带不上只是模型少看一眼项目卡，消息照发
        pass
    if not claw.is_openclaw() and first_today(thread):
        parts += [agent_role(thread), last_digest(thread)]
    return "\n\n".join(x for x in parts if x) or None


def first_today(thread: str) -> bool:
    """这个对话今天（逻辑日）还没说过话：开跑前调，这一句就是今天的第一句。"""
    since = day_bounds(day_of(now_iso()))[0]
    with _lock, db() as conn:
        return conn.execute("SELECT 1 FROM messages WHERE thread=? AND ts>=? LIMIT 1", (thread, since)).fetchone() is None


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
          level: str | None = None) -> Run:
    """开跑：记下这一轮，后台发给 Gateway。"""
    run = Run(thread=thread, model=model, user_id=user_id, started=ts, key=key, origin=origin, level=level, feed_mark=feed_mark(),
              inbox_mark=max_rowid("inbox"), handoff_mark=max_rowid("handoffs"))
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
    """这个线程没在回复了：排着的全部合成一轮发出去，等着的连接都接到这一轮上。"""
    items = QUEUED.get(thread) or []
    if not items or busy(thread):
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
            WHERE m.role='user' AND m.status IN ('ok','steered') AND m.ts >= ?""", (cutoff,)).fetchall()
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


def resume_queued() -> None:
    """服务启动时：库里还排着的（上次重启前没来得及发的）。QUEUE_RESUME_MINUTES 以内的按线程合成一轮接着发（附件只剩文字），更早的标成没发出去。"""
    cutoff = (datetime.now(TZ) - timedelta(minutes=QUEUE_RESUME_MINUTES)).isoformat(timespec="seconds")
    with _lock, db() as conn:
        rows = conn.execute("SELECT id, thread, text, gw_text, ts FROM messages WHERE status='queued' ORDER BY id").fetchall()
        old = [r["id"] for r in rows if r["ts"] < cutoff]
        if old:
            conn.execute(f"UPDATE messages SET status='error' WHERE id IN ({','.join('?' * len(old))})", old)
    loop = asyncio.get_running_loop()
    for r in rows:
        if r["ts"] >= cutoff:
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
    if body.origin == "user" and busy(body.thread) and cur and cur.gw_run and not body.attachments and not QUEUED.get(body.thread) and transport() == "ws":
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
    """停掉这个线程正在进行的回复：断开到 Gateway 的连接（Gateway 就中止这一轮），已经说了的留着、末尾标「停了」。排着的消息接着发。"""
    run = RUNS.get(body.thread)
    if not run or run.done:
        return {"ok": True, "stopped": False}
    run.stopping = True
    if run.gw_run:  # WebSocket 那条路：让 Gateway 中止这一轮（收到 aborted 事件这一轮就结束）；它不认再断连接
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


@router.get("/api/chat/busy")
def busy_threads():
    """正在回复的线程和排着的消息数。safe_restart.py 等它们都没了再重启服务（重启会掐断进行中的回复）。"""
    running = [t for t, r in RUNS.items() if not r.done]
    queued = {t: len(v) for t, v in QUEUED.items() if v}
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


@router.post("/api/chat/relay")
async def relay(body: RelayBody):
    """主对话把问题转给某个 Agent：记进那个 Agent 的线程（role=auto，app 里显示成「主对话转来」），等它答完，把答案带回去。
    不推送（用户在主对话里等着）。上一条没回完 → 409。超时 → 200 但 status=timeout，回复仍会在后台完成并入库。
    同时记一张转交卡（cards.py）：发起转交的那次回复当场出「正在问 …」，答完变成「转给了 …」，挂在那条回复下面。"""
    text = body.text.strip()
    if not text:
        raise HTTPException(400, L("空消息", "Empty message"))
    import cards as cards_mod  # 延迟导入：cards.py 依赖本模块
    try:
        run = start_run(body.thread, PREFIX_RELAY + text, None, origin="relay", level="none")
    except HTTPException as e:
        if e.status_code == 409:  # 那个 Agent 上一条还没回完：也记一张（没转过去），主对话里看得到
            cards_mod.handoff_start(body.thread, text, None, status="busy")
        raise
    cards_mod.handoff_start(body.thread, text, run)
    deadline = time.time() + min(max(body.timeout, 10), 600)
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
    with _lock, db() as conn:
        n = conn.execute("DELETE FROM messages WHERE thread=? AND id>=?", (body.thread, rid)).rowcount
    tail = L(f"，{settings.app_name} 的会话也退回到那之前", f"; {settings.app_name}'s session was rewound to before them too") if entry else ""
    log_activity(L(f"撤回了 {n} 条消息{tail}", f"Unsent {n} message{'' if n == 1 else 's'}{tail}"), "deleted")
    return {"ok": True, "text": r["text"], "removed": n, "rewound": bool(entry)}
