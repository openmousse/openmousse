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
import json
import shutil
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import AsyncIterator

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from config import TZ, raw, settings  # noqa: E402
from i18n import L  # noqa: E402

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
    inbox_mark: int = 0  # 开跑时 inbox 的最大 rowid：回完把这之后这个线程新交的收件箱条目挂到这条回复下（created_at 只到秒，不够准）
    handoff_mark: int = 0  # 开跑时 handoffs 的最大 rowid：回完把这之后从这个线程转出去的挂到这条回复下（见 cards.py）
    cards: dict = field(default_factory=dict)  # 这次回复里出的转交卡、任务卡（id → 最新的样子）：客户端重新接上时补发（见 cards.py）

    def publish(self, item: tuple[str, dict]) -> None:
        for q in list(self.queues):
            q.put_nowait(item)


RUNS: dict[str, Run] = {}
RUN_KEEP_SECONDS = 15 * 60  # 回完的回复留一会儿：手机断线后重新接上还能拿到完整内容，不至于显示"没发出去"


def gateway_token() -> str:
    try:
        return json.loads(OPENCLAW.read_text(encoding="utf8"))["gateway"]["auth"]["token"]
    except (OSError, ValueError, KeyError) as e:  # noqa: BLE001
        raise HTTPException(503, L(f"读不到 Gateway token：{e}", f"Can't read the Gateway token: {e}")) from e


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


def thread_model(conn: sqlite3.Connection, thread: str) -> str:
    r = conn.execute("SELECT model FROM threads WHERE id=?", (thread,)).fetchone()
    return (r["model"] if r and r["model"] else None) or DEFAULT_MODEL


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
        run = RUNS.get(thread)
        return {"ok": True, "thread": thread, "sessionKey": session_key(thread), "modelId": thread_model(conn, thread),
                "messages": [row_to_msg(r) for r in reversed(rows)],
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


def sse(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode()


async def run_gateway(run: Run, text: str | list, token: str) -> None:
    """后台把一条消息发给 Gateway，流式攒回复，结束后入库。不依赖任何客户端连接。text 可以是 OpenAI 的 content 数组（带图片）。"""
    try:
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
    except (httpx.HTTPError, RuntimeError, OSError) as e:  # noqa: BLE001
        run.status = "error"
        run.error = str(e)
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
    token = gateway_token()
    import files as files_mod  # 延迟导入：files.py 依赖本模块
    rows = files_mod.load_pending(thread, attachment_ids or [])
    role = "auto" if origin in ("auto", "relay") else "user"
    content, gw_text = files_mod.build_content(text, rows)
    try:  # 项目：每天第一句话、项目卡改过以后，把项目卡带给模型（OpenClaw 每天重置会话，靠它接上；见 projects.py）
        import projects as projects_mod  # 延迟导入：projects.py 依赖本模块
        card = projects_mod.context_for(thread)
    except Exception:  # noqa: BLE001 — 带不上只是模型少看一眼项目卡，消息照发
        card = None
    if card:
        context = f"{card}\n\n{context}" if context else card
    try:  # 思考主题：每天第一句话、碎片变了以后，把碎片和「陪你想」的规矩带给模型（见 think.py）
        import think as think_mod  # 延迟导入：think.py 依赖本模块
        topic = think_mod.context_for(thread)
    except Exception:  # noqa: BLE001 — 带不上只是模型少看一眼碎片
        topic = None
    if topic:
        context = f"{topic}\n\n{context}" if context else topic
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
    run = Run(thread=thread, model=model, user_id=user_id, started=ts, key=key, origin=origin, level=level, feed_mark=feed_mark(),
              inbox_mark=max_rowid("inbox"), handoff_mark=max_rowid("handoffs"))
    RUNS[thread] = run
    asyncio.create_task(run_gateway(run, content, token))
    try:  # 回复进行中盯着 OpenClaw 的任务台账：这次新派的子任务当场出任务卡（见 cards.py）
        import cards as cards_mod  # 延迟导入：cards.py 依赖本模块
        cards_mod.watch(run)
    except Exception:  # noqa: BLE001
        pass
    return run


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
    if body.save and not quoted:  # 收藏里点「问问」「翻译」带过来的
        import saves as saves_mod  # 延迟导入：saves.py 依赖本模块
        context = await asyncio.to_thread(saves_mod.save_context, body.save)
    run = start_run(body.thread, text, body.model, attachment_ids=body.attachments, origin=body.origin, context=context)
    if reply_to:
        inbox_mod.mark_revising(reply_to[0], text)
    elif follow:
        inbox_mod.mark_followed(follow[0], text)
    return StreamingResponse(attach(run), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


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


async def gateway_call(method: str, params: dict, timeout: float = 30) -> dict:
    exe = shutil.which(settings.openclaw_bin) or settings.openclaw_bin
    proc = await asyncio.create_subprocess_exec(exe, "gateway", "call", method, "--json", "--params", json.dumps(params, ensure_ascii=False),
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await asyncio.wait_for(proc.communicate(), timeout)
    if proc.returncode != 0:
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
    entry = await find_entry(body.thread, (r["gw_text"] if "gw_text" in r.keys() and r["gw_text"] else r["text"]), r["ts"])
    if entry:
        await gateway_call("sessions.rewind", {"sessionKey": session_key(body.thread), "entryId": entry})
    with _lock, db() as conn:
        n = conn.execute("DELETE FROM messages WHERE thread=? AND id>=?", (body.thread, rid)).rowcount
    tail = L(f"，{settings.app_name} 的会话也退回到那之前", f"; {settings.app_name}'s session was rewound to before them too") if entry else ""
    log_activity(L(f"撤回了 {n} 条消息{tail}", f"Unsent {n} message{'' if n == 1 else 's'}{tail}"), "deleted")
    return {"ok": True, "text": r["text"], "removed": n, "rewound": bool(entry)}
