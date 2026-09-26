"""对话里的卡片：主对话转给 Agent 的「转交卡」、派出去的后台任务的「任务卡」（2026-09-26）。

转交（skills/handoff → scripts/ask_agent.py → POST /api/chat/relay）
- relay 开跑时在 grava.db 的 handoffs 表记一行：从哪个线程转给谁、问了什么。「从哪个线程」= 这时正在回复、又不是 relay 的那个线程
  （source_run：exec 起的脚本不知道自己在哪个会话里，只能这样认；同时有几个在回复时优先主 agent 的线程，再取最晚开始的）。
  Telegram 那边转的在 app 里没有对应的回复，from_thread 为空，只在 Agent 那边显示「主对话转来」。
- 给发起转交的那次回复发 SSE 事件 card（app 当场出「正在问 …」），被转的 Agent 答完 / 出错再发一次。发起转交的那次回复结束时
  （chat.run_gateway → on_run_end）把它挂到那条回复下面（message_id）。被转的 Agent 正忙（409）也记一行，status=busy。
- 状态：running 在问 → done 答完 / error 出错；busy 那边正忙，没转过去；lost 服务重启了，没等到（回答在 Agent 那边的对话里）。

任务（OpenClaw 子会话，sessions_spawn）
- 台账在 OpenClaw 的 <openclaw_home>/state/openclaw.sqlite（task_runs、subagent_runs），这里只读，一次几毫秒；
  `openclaw gateway call tasks.list` 要起一个 node 进程（约 2 秒）。所以回复进行中每 2 秒看一眼这个会话有没有新派的（watch_run），
  有就发 SSE 事件 card；回复结束时把这次派的挂到这条回复下面（表 task_links）。读不到台账（OpenClaw 太老、换了位置）就没有任务卡，
  任务页照旧走 tasks.list。
- 「正在做哪一步」：台账只有最后一个工具的名字；读哪个文件、搜什么要读子会话记录（chat.history，起 node 进程），
  进行中的任务最多 20 秒读一次，做完的读一次就留着（顺带拿 token 用量）。
- 改一下：POST /api/tasks/{id}/revise 把意见发给同一个子会话（app 线程 task:<id>）；第几轮、意见、这一轮的结果从这个线程的记录算。

接口
- GET /api/chat/cards?thread=&day=：这个线程这一天（04:00 起）的转交卡和任务卡，旧的在前（messageId = 挂在哪条回复下面，
  回复还没结束的是 null），外加别的线程转给它的（incoming：Agent 那边的「主对话转来」点一下回去）。
- GET /api/tasks/quota：今天（逻辑日）派了几个后台任务、上限、单个最长几分钟。server.json 的 tasks：
  {"daily_limit": 10, "max_minutes": 30, "notify_done": true}。额度只是给 Agent 看的数（tasks_ctl.py quota）；
  真正到点停掉任务的是 OpenClaw 的 agents.defaults.subagents.runTimeoutSeconds。

做完推送（tasks.notify_done，默认开；每次读文件，不用重启）
- watch_tasks 每 30 秒看一眼台账：从 app 派的（requesterOrigin.channel = webchat）后台任务做完、没做成、到点停了 → 静默推一条，
  点开到派它的那个对话。Telegram 派的 OpenClaw 自己在 Telegram 里回，不重复推；你自己取消的不推。「改一下」的那一轮做完也静默推一条。
"""
from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import time
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException

import chat
import push
from chat import _lock, db, now_iso
from config import TZ, raw, settings
from i18n import L

router = APIRouter()
LEDGER = settings.openclaw_home / "state" / "openclaw.sqlite"
RUNNING = ("running", "queued", "pending")
# 状态值和任务页一样是给 app 比较的枚举，不翻译
STATUS = {"succeeded": "完成", "completed": "完成", "failed": "失败", "timed_out": "失败", "lost": "失败", "cancelled": "已取消", "canceled": "已取消"}
DAILY_LIMIT, MAX_MINUTES = 10, 30
WATCH_EVERY = 2.0      # 回复进行中多久看一眼台账（秒）
DETAIL_EVERY = 20.0    # 进行中的任务多久读一次子会话记录
DONE_EVERY = 30.0      # 多久看一眼有没有做完的（推送）
PUSH_WINDOW = 15 * 60  # 做完多久以内的才推：服务停过一阵再起来，不补推老的
RESULT_MAX = 1200      # 卡片上结果的字数上限（app 只显示开头几行，全文在任务详情）
_bg: set[asyncio.Task] = set()                   # 后台协程（留个引用，免得被回收）
_sources: dict[str, chat.Run] = {}               # 转交 id → 发起转交的那次回复：被转的 Agent 答完时给它补发一次 card
_detail: dict[str, tuple[float, dict]] = {}      # 任务 id → (读的时间, {name, label, tokens, final})
_fetching: set[str] = set()


def cdb() -> sqlite3.Connection:
    conn = db()
    conn.execute("""CREATE TABLE IF NOT EXISTS handoffs (id TEXT PRIMARY KEY, from_thread TEXT, to_thread TEXT NOT NULL,
        question TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'running', error TEXT, relay_id INTEGER, reply_id INTEGER,
        message_id INTEGER, created_at TEXT NOT NULL, finished_at TEXT, seconds REAL)""")
    conn.execute("CREATE INDEX IF NOT EXISTS handoffs_from ON handoffs(from_thread, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS handoffs_to ON handoffs(to_thread, created_at)")
    conn.execute("CREATE TABLE IF NOT EXISTS task_links (task_id TEXT PRIMARY KEY, thread TEXT NOT NULL, message_id INTEGER, created_at TEXT NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS task_seen (task_id TEXT PRIMARY KEY, status TEXT NOT NULL, seen_at TEXT NOT NULL)")
    return conn


def spawn(coro) -> None:
    t = asyncio.create_task(coro)
    _bg.add(t)
    t.add_done_callback(_bg.discard)


def names() -> dict[str, str]:
    """线程 id → 名字：主对话、各 Agent、独立空间。"""
    try:
        import data  # 延迟导入：data.py 依赖本模块
        nm = data.thread_names()
    except (ImportError, sqlite3.Error):
        nm = {}
    return {"main": L("主对话", "Main chat"), **nm}


def tasks_config() -> dict:
    t = raw().get("tasks")
    return t if isinstance(t, dict) else {}


def limits() -> tuple[int, int]:
    """每天几个后台任务、单个最长几分钟（server.json 的 tasks.daily_limit / max_minutes）。"""
    t = tasks_config()
    try:
        return max(1, int(t.get("daily_limit") or DAILY_LIMIT)), max(1, int(t.get("max_minutes") or MAX_MINUTES))
    except (TypeError, ValueError):
        return DAILY_LIMIT, MAX_MINUTES


def notify_enabled() -> bool:
    return tasks_config().get("notify_done", True) is not False


# —— 时间 ——————————————————————————————————————————————————————————

def iso(ms: float | None) -> str | None:
    return datetime.fromtimestamp(ms / 1000, TZ).isoformat(timespec="seconds") if ms else None


def hm(ms: float | None) -> str | None:
    return datetime.fromtimestamp(ms / 1000, TZ).strftime("%H:%M") if ms else None


def ms_of(iso_text: str) -> int:
    return int(datetime.fromisoformat(iso_text).timestamp() * 1000)


# —— OpenClaw 的任务台账（只读） ——————————————————————————————————————————

COLS = ("task_id, owner_key, child_session_key, run_id, label, task, status, created_at, started_at, ended_at, "
        "tool_use_count, last_tool_name, error, progress_summary, terminal_summary")


def ledger(where: str = "1", args: tuple = (), limit: int = 200) -> list[dict] | None:
    """台账里的子会话任务（新的在前），每行带上 subagent_runs 的 payload（model、runTimeoutSeconds、requesterOrigin、completion…）。
    where 只用本模块里写死的条件。读不到（没有这个文件、表结构变了）返回 None。"""
    if not LEDGER.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{LEDGER}?mode=ro", uri=True, timeout=2)
    except sqlite3.Error:
        return None
    conn.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in conn.execute(f"SELECT {COLS} FROM task_runs WHERE runtime='subagent' AND ({where}) "
                                              "ORDER BY created_at DESC LIMIT ?", (*args, limit))]
        ids = [r["run_id"] for r in rows if r["run_id"]]
        payloads: dict[str, dict] = {}
        for i in range(0, len(ids), 200):
            chunk = ids[i:i + 200]
            q = ",".join("?" * len(chunk))
            for p in conn.execute(f"SELECT run_id, payload_json FROM subagent_runs WHERE run_id IN ({q})", chunk):
                try:
                    payloads[p["run_id"]] = json.loads(p["payload_json"] or "{}")
                except ValueError:
                    pass
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    for r in rows:
        r["payload"] = payloads.get(r["run_id"]) or {}
    return rows


def status_of(r: dict) -> str:
    s = (r.get("status") or "").lower()
    if not s or s in RUNNING:
        return "进行中"
    return STATUS.get(s, "失败" if r.get("error") else "完成")


def outcome(r: dict) -> dict:
    return ((r.get("payload") or {}).get("execution") or {}).get("outcome") or {}


def timed_out(r: dict) -> bool:
    return (r.get("status") == "timed_out" or outcome(r).get("status") == "timeout"
            or "timeout" in str((r.get("payload") or {}).get("endedReason") or ""))


def result_of(r: dict) -> str:
    done = (r.get("payload") or {}).get("completion") or {}
    return str(done.get("resultText") or r.get("terminal_summary") or r.get("progress_summary") or "").strip()


def limit_minutes(r: dict) -> int | None:
    try:
        s = int((r.get("payload") or {}).get("runTimeoutSeconds") or 0)
    except (TypeError, ValueError):
        return None
    return s // 60 if s > 0 else None


def title_of(r: dict) -> str:
    label = (r.get("label") or "").strip()
    if label:
        return label
    return push.clip(push.first_line(r.get("task") or ""), 40) or L("后台任务", "Background task")


DELIVER = re.compile(r"^\s*(?:[-*•]\s*)?(?:\*\*)?(要交|交付物?|产出|交什么|Deliverables?|Output)(?:\*\*)?\s*[:：]\s*(.*)$", re.I)
BULLET = re.compile(r"^\s*(?:[-*•]|\d{1,2}[.)、])\s+(.*)$")


def deliverable(task: str) -> list[str]:
    """任务卡上的「要交」：任务正文里「要交：…」那一行，加上紧跟着的列表项，最多 3 条。没写就是空（skills 里让 Agent 写上）。"""
    lines = (task or "").splitlines()
    for i, ln in enumerate(lines):
        m = DELIVER.match(ln)
        if not m:
            continue
        out = [m.group(2).strip()] if m.group(2).strip() else []
        for nxt in lines[i + 1:]:
            b = BULLET.match(nxt)
            if not b:
                break
            out.append(b.group(1).strip())
        return [push.clip(push._inline(x), 90) for x in out if x][:3]
    return []


# —— 「正在做哪一步」：读子会话记录 ————————————————————————————————————————

# 工具 → (没有参数时的说法, 有参数时的前缀)，中英各一份
STEP = {"read": ("在读文件", "Reading a file", "在读", "Reading"), "pdf": ("在读 PDF", "Reading a PDF", "在读", "Reading"),
        "image": ("在看图", "Looking at an image", "在看", "Looking at"), "write": ("在写文件", "Writing a file", "在写", "Writing"),
        "edit": ("在改文件", "Editing a file", "在改", "Editing"), "apply_patch": ("在改文件", "Editing files", "", ""),
        "exec": ("在跑命令", "Running a command", "", ""), "process": ("在跑命令", "Running a command", "", ""),
        "web_search": ("在搜网页", "Searching the web", "在搜", "Searching"), "web_fetch": ("在看网页", "Reading a page", "在看", "Reading"),
        "browser": ("在用浏览器", "Using the browser", "", ""), "memory_search": ("在查记忆", "Searching memory", "", ""),
        "memory_get": ("在查记忆", "Reading memory", "", ""), "sessions_spawn": ("在派子任务", "Delegating", "", ""),
        "sessions_yield": ("在等子任务", "Waiting for a sub-task", "", "")}
FILE_TOOLS = ("read", "pdf", "image", "write", "edit")


def human_step(name: str | None, label: str = "") -> str:
    """一步的人话：read + 路径 → 「在读 L2.pdf」，没有路径 → 「在读文件」；web_search + 词 → 「在搜「…」」；web_fetch + 网址 → 「在看 域名」；
    命令行不给人看，只说「在跑命令」；不认识的工具 → 「在用 X」。"""
    if not name:
        return ""
    zh, en, zh_pre, en_pre = STEP.get(name, (f"在用 {name}", f"Using {name}", "", ""))
    label = (label or "").strip()
    if label and name in FILE_TOOLS:
        target = Path(label.split("\n")[0]).name or label
        return L(f"{zh_pre} {push.clip(target, 36)}", f"{en_pre} {push.clip(target, 40)}")
    if label and name == "web_search":
        return L(f"{zh_pre}「{push.clip(label, 24)}」", f'{en_pre} "{push.clip(label, 32)}"')
    if label and name == "web_fetch":
        m = re.match(r"https?://([^/\s]+)", label)
        if m:
            return L(f"{zh_pre} {m.group(1)}", f"{en_pre} {m.group(1)}")
    return L(zh, en)


def last_tool(hist: dict) -> tuple[str, str] | None:
    """子会话记录里最后一次工具调用：(工具名, 最能说明它在干什么的参数)。"""
    for m in reversed(hist.get("messages") or []):
        if m.get("role") != "assistant":
            continue
        for c in reversed(m.get("content") or []):
            if not isinstance(c, dict) or c.get("type") not in ("toolCall", "tool_use"):
                continue
            args = c.get("arguments") or c.get("input") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    args = {}
            args = args if isinstance(args, dict) else {}
            label = next((str(args[k]) for k in ("path", "file_path", "url", "query", "title", "description") if args.get(k)), "")
            return str(c.get("name") or ""), label
    return None


async def fetch_detail(tid: str, key: str, final: bool) -> None:
    try:
        hist = await chat.gateway_call("chat.history", {"sessionKey": key, "limit": 40}, timeout=25)
        step = last_tool(hist)
        info = hist.get("sessionInfo") or {}
        _detail[tid] = (time.time(), {"name": step[0] if step else None, "label": step[1] if step else "",
                                      "tokens": info.get("totalTokens"), "final": final})
    except Exception:  # noqa: BLE001 — 读不到只是卡上少一行步骤
        old = (_detail.get(tid) or (0.0, {}))[1]
        _detail[tid] = (time.time(), {**old, "final": False})
    finally:
        _fetching.discard(tid)


def want_detail(r: dict) -> None:
    """需要的话在后台读一次子会话记录：进行中的最多 20 秒一次，做完的读一次就不再读。"""
    tid, key = r.get("task_id"), r.get("child_session_key")
    if not tid or not key or tid in _fetching:
        return
    hit = _detail.get(tid)
    if hit and (hit[1].get("final") or time.time() - hit[0] < DETAIL_EVERY):
        return
    _fetching.add(tid)
    spawn(fetch_detail(tid, key, final=status_of(r) != "进行中"))


def forget(tid: str) -> None:
    """发了修改意见：用量和步骤会变，下次重读。"""
    _detail.pop(tid, None)


def step_of(tid: str, last_tool: str | None) -> str:
    """进行中的任务正在做哪一步：读过子会话记录就用它（带文件名、搜的词），否则只凭台账里最后一个工具的名字。"""
    det = (_detail.get(tid) or (0.0, {}))[1]
    return human_step(det.get("name") or last_tool, det.get("label") or "")


# —— 卡片的样子 ——————————————————————————————————————————————————————

def rounds(conn: sqlite3.Connection | None, tid: str) -> tuple[int, str | None, str | None, str | None]:
    """「改一下」之后的轮次：(第几轮, 最近一次的意见, 这一轮 running / done / failed, 这一轮的结果)。没改过 = (1, None, None, None)。"""
    if conn is None:
        return 1, None, None, None
    rows = conn.execute("SELECT role, text, status FROM messages WHERE thread=? ORDER BY id", (f"task:{tid}",)).fetchall()
    notes = [r["text"] for r in rows if r["role"] == "user"]
    if not notes:
        return 1, None, None, None
    run = chat.RUNS.get(f"task:{tid}")
    if run and not run.done:
        return 1 + len(notes), notes[-1], "running", None
    last = rows[-1] if rows and rows[-1]["role"] == "grava" else None
    return 1 + len(notes), notes[-1], "failed" if last is None or last["status"] == "error" else "done", last["text"] if last else None


def task_json(r: dict, message_id: int | None = None, conn: sqlite3.Connection | None = None, seq: dict[str, int] | None = None) -> dict:
    """seq：今天派的任务 id → 第几个（today_seq），卡片上写「今天第 3 个」；不是今天派的没有。"""
    tid = r["task_id"]
    st = status_of(r)
    now = time.time() * 1000
    t0 = r.get("started_at") or r.get("created_at") or now
    t1 = r.get("ended_at") if st != "进行中" else None
    det = (_detail.get(tid) or (0.0, {}))[1]
    n, note, round_status, round_text = rounds(conn, tid)
    return {"kind": "task", "id": tid, "thread": chat.thread_of(r.get("owner_key")), "messageId": message_id,
            "createdAt": iso(r.get("created_at")), "status": st, "timedOut": timed_out(r), "title": title_of(r),
            "deliverable": deliverable(r.get("task") or ""), "modelId": (r.get("payload") or {}).get("model") or None,
            "minutes": max(0, round(((t1 or now) - t0) / 60000)), "startedAt": hm(t0), "finishedAt": hm(t1),
            "tools": int(r.get("tool_use_count") or 0), "step": step_of(tid, r.get("last_tool_name")) if st == "进行中" else "",
            "result": push.clip(result_of(r), RESULT_MAX) if st != "进行中" else "", "error": r.get("error") or None,
            "round": n, "roundStatus": round_status, "note": note, "roundResult": push.clip(round_text, RESULT_MAX) if round_text else None,
            "tokens": det.get("tokens"), "limitMinutes": limit_minutes(r), "seq": (seq or {}).get(tid), "dailyLimit": limits()[0]}


def handoff_json(h: sqlite3.Row, nm: dict[str, str]) -> dict:
    to, frm = h["to_thread"], h["from_thread"]
    status = h["status"]
    if status == "running":  # 被转的那次回复已经不在了（服务重启过）：等不到了，回答在 Agent 那边
        live = chat.RUNS.get(to)
        if not (live and not live.done and live.user_id == h["relay_id"]):
            status = "lost"
    return {"kind": "handoff", "id": h["id"], "thread": frm, "messageId": h["message_id"], "createdAt": h["created_at"],
            "status": status, "to": to, "toName": nm.get(to) or to, "from": frm or "main", "fromName": nm.get(frm or "main") or frm,
            "question": h["question"], "seconds": round(h["seconds"]) if h["seconds"] is not None else None,
            "relayId": f"db{h['relay_id']}" if h["relay_id"] else None, "replyId": f"db{h['reply_id']}" if h["reply_id"] else None,
            "error": h["error"]}


def publish(run: chat.Run, card: dict) -> None:
    """给进行中的回复发一张卡（SSE card 事件）；记在 run.cards 里，客户端重新接上时补发。"""
    run.cards[card["id"]] = card
    run.publish(("card", card))


# —— 转交 ——————————————————————————————————————————————————————————

def source_run(target: str) -> chat.Run | None:
    """谁在转：正在回复、不是被转的那个、也不是 relay 本身的回复。几个同时在回复时优先主 agent 的线程（handoff skill 在它身上），再取最晚开始的。"""
    live = [r for t, r in chat.RUNS.items() if not r.done and t != target and r.origin != "relay" and not t.startswith(("task:", "study-"))]
    if not live:
        return None
    main = [r for r in live if chat.agent_of(r.thread) == "main"]
    return max(main or live, key=lambda r: r.t0)


def handoff_start(target: str, question: str, run: chat.Run | None, status: str = "running") -> str | None:
    """relay 开跑（或 409 没转过去）时记一张转交卡，给发起转交的回复发 card。出错不影响转交本身。"""
    try:
        src = source_run(target)
        hid = f"ho-{uuid.uuid4().hex[:8]}"
        with _lock, cdb() as conn:
            conn.execute("INSERT INTO handoffs(id, from_thread, to_thread, question, status, relay_id, created_at) VALUES(?,?,?,?,?,?,?)",
                         (hid, src.thread if src else None, target, question.strip(), status, run.user_id if run else None, now_iso()))
            row = conn.execute("SELECT * FROM handoffs WHERE id=?", (hid,)).fetchone()
        if src:
            if run:
                _sources[hid] = src
            publish(src, handoff_json(row, names()))
        return hid
    except Exception:  # noqa: BLE001
        return None


def on_run_end(run: chat.Run) -> None:
    """一次回复入库之后、done 之前（chat.run_gateway）：
    1) 这次回复期间从这个线程转出去的、派出去的，挂到这条回复下面；
    2) 这次是被转的 Agent 在答（origin=relay）：更新那张转交卡，发起转交的回复还在进行就给它补发 card。"""
    if run.reply_id is None:
        return
    row = None
    with _lock, cdb() as conn:
        conn.execute("UPDATE handoffs SET message_id=? WHERE from_thread=? AND message_id IS NULL AND rowid>?",
                     (run.reply_id, run.thread, run.handoff_mark))
        if run.origin == "relay":
            conn.execute("UPDATE handoffs SET status=?, reply_id=?, finished_at=?, seconds=?, error=? WHERE relay_id=? AND to_thread=?",
                         ("done" if run.status == "ok" else "error", run.reply_id, now_iso(), round(time.time() - run.t0, 1),
                          run.error, run.user_id, run.thread))
            row = conn.execute("SELECT * FROM handoffs WHERE relay_id=? AND to_thread=?", (run.user_id, run.thread)).fetchone()
    if row:
        src = _sources.pop(row["id"], None)
        if src and not src.done:
            publish(src, handoff_json(row, names()))
    if run.thread.startswith(("task:", "study-")):
        return
    rows = ledger("owner_key=? AND created_at>=?", (run.key or chat.session_key(run.thread), int(run.t0 * 1000) - 3000), 20) or []
    if rows:
        with _lock, cdb() as conn:
            conn.executemany("INSERT OR IGNORE INTO task_links(task_id, thread, message_id, created_at) VALUES(?,?,?,?)",
                             [(r["task_id"], run.thread, run.reply_id, now_iso()) for r in rows])


# —— 回复进行中：新派的任务当场出卡 ————————————————————————————————————————————

def watch(run: chat.Run) -> None:
    if run.thread.startswith(("task:", "study-")) or not LEDGER.exists():
        return
    spawn(watch_run(run))


async def watch_run(run: chat.Run) -> None:
    """每 2 秒看一眼台账：这个会话在这次回复里新派的子任务 → 发 card；状态、步骤变了再发。回复结束就停。"""
    key = run.key or chat.session_key(run.thread)
    since = int(run.t0 * 1000) - 3000
    while not run.done:
        await asyncio.sleep(WATCH_EVERY)
        if run.done:
            return
        rows = await asyncio.to_thread(ledger, "owner_key=? AND created_at>=?", (key, since), 20)
        if rows is None:
            return
        seq = await asyncio.to_thread(today_seq) if rows else {}
        for r in reversed(rows):
            want_detail(r)
            card = task_json(r, seq=seq)
            old = run.cards.get(card["id"])
            if not old or any(old.get(k) != card.get(k) for k in ("status", "tools", "step", "result", "modelId")):
                publish(run, card)


# —— 额度 ——————————————————————————————————————————————————————————

def today_rows() -> list[dict] | None:
    lo, _hi = chat.day_bounds(chat.day_of(now_iso()))
    return ledger("created_at>=?", (ms_of(lo),), 500)


def today_seq(rows: list[dict] | None = None) -> dict[str, int]:
    """今天派的任务 id → 第几个（按派的先后，从 1 数）。"""
    rows = today_rows() if rows is None else rows
    return {r["task_id"]: i for i, r in enumerate(sorted(rows or [], key=lambda r: r.get("created_at") or 0), 1)}


def quota() -> dict:
    """今天（逻辑日，04:00 起）派了几个后台任务。读不到台账时 today / left 是 null。"""
    daily, max_min = limits()
    rows = today_rows()
    if rows is None:
        return {"ok": True, "today": None, "running": None, "limit": daily, "left": None, "maxMinutes": max_min}
    return {"ok": True, "today": len(rows), "running": sum(1 for r in rows if status_of(r) == "进行中"), "limit": daily,
            "left": max(0, daily - len(rows)), "maxMinutes": max_min}


@router.get("/api/tasks/quota")
def get_quota():
    return quota()


# —— 接口：一个线程的卡片 ————————————————————————————————————————————————

@router.get("/api/chat/cards")
async def cards(thread: str = "main", day: str | None = None):
    """这个线程这一天的转交卡和任务卡（旧的在前），加上别的线程转给它的（incoming）。tasksAvailable=false：读不到 OpenClaw 的任务台账。"""
    try:
        day = day or chat.day_of(now_iso())
        lo, hi = chat.day_bounds(day)
    except ValueError as exc:
        raise HTTPException(400, L("day 要写成 YYYY-MM-DD", "day must be YYYY-MM-DD")) from exc
    nm = names()
    rows = await asyncio.to_thread(ledger, "owner_key=? AND created_at>=? AND created_at<?", (chat.session_key(thread), ms_of(lo), ms_of(hi)), 50)
    with _lock, cdb() as conn:
        # ts 是带时区的 ISO 字符串，同一时区下字符串比较等价于时间比较（和 /api/chat/history 一样）
        out = [handoff_json(h, nm) for h in conn.execute("SELECT * FROM handoffs WHERE from_thread=? AND created_at>=? AND created_at<? "
                                                         "ORDER BY created_at, rowid", (thread, lo, hi))]
        incoming = [handoff_json(h, nm) for h in conn.execute("SELECT * FROM handoffs WHERE to_thread=? AND created_at>=? AND created_at<? "
                                                              "ORDER BY created_at, rowid", (thread, lo, hi))]
        links = {r["task_id"]: r["message_id"] for r in conn.execute("SELECT task_id, message_id FROM task_links WHERE thread=?", (thread,))}
        seq = today_seq() if rows else {}
        out += [task_json(r, links.get(r["task_id"]), conn, seq) for r in reversed(rows or [])]
    for r in rows or []:
        want_detail(r)
    out.sort(key=lambda c: c["createdAt"] or "")
    return {"ok": True, "thread": thread, "day": day, "cards": out, "incoming": incoming, "tasksAvailable": rows is not None}


# —— 做完推送 ——————————————————————————————————————————————————————

async def push_done(r: dict) -> None:
    st = status_of(r)
    if st == "已取消":
        return
    channel = ((r.get("payload") or {}).get("requesterOrigin") or {}).get("channel")
    if channel and channel != "webchat":  # Telegram 这类渠道派的：OpenClaw 自己在那边回
        return
    thread = chat.thread_of(r.get("owner_key"))
    if not thread or thread.startswith("study-"):
        return
    title = title_of(r)
    t0, t1 = r.get("started_at") or r.get("created_at") or 0, r.get("ended_at") or time.time() * 1000
    minutes = max(1, round((t1 - t0) / 60000))
    if timed_out(r):
        sub, line = L("后台任务到点停了", "Background task hit its time limit"), L(f"做了 {minutes} 分钟，没做完", f"Stopped after {minutes} min, unfinished")
    elif st == "完成":
        sub, line = L("后台任务做完了", "Background task done"), push.first_line(result_of(r))
    else:
        sub, line = L("后台任务没做成", "Background task failed"), str(r.get("error") or "")
    body = f"{title} · {push.cut(line, 80)}" if line else title
    try:
        await push.send_push(push.thread_title(thread), body, {"thread": thread, "target": {"type": "thread", "thread": thread}},
                             thread_id=thread, subtitle=sub, level="quiet", collapse=f"task:{r['task_id']}", kind="done")
    except Exception:  # noqa: BLE001 — 推送失败不影响别的
        pass


async def check_done() -> None:
    """台账里新结束的任务记进 task_seen；刚结束的（15 分钟内）推一条。"""
    since = int((time.time() - 2 * 86400) * 1000)
    rows = await asyncio.to_thread(ledger, "status NOT IN ('running','queued','pending') AND IFNULL(ended_at, created_at)>=?", (since,), 200)
    if not rows:
        return
    with _lock, cdb() as conn:
        seen = {r[0] for r in conn.execute("SELECT task_id FROM task_seen")}
        new = [r for r in rows if r["task_id"] not in seen]
        if new:
            conn.executemany("INSERT OR IGNORE INTO task_seen(task_id, status, seen_at) VALUES(?,?,?)",
                             [(r["task_id"], r.get("status") or "", now_iso()) for r in new])
    if not new or not notify_enabled():
        return
    now = time.time() * 1000
    for r in reversed(new):
        if now - (r.get("ended_at") or r.get("created_at") or 0) <= PUSH_WINDOW * 1000:
            await push_done(r)


async def watch_tasks() -> None:
    await asyncio.sleep(15)
    while True:
        try:
            await check_done()
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(DONE_EVERY)


async def after_run(run: chat.Run) -> None:
    """「改一下」的那一轮回完了（线程 task:<id>）：静默推一条到派这个任务的对话。"""
    if not run.thread.startswith("task:") or run.origin != "user" or not notify_enabled():
        return
    tid = run.thread[5:]
    rows = await asyncio.to_thread(ledger, "task_id=?", (tid,), 1)
    thread = chat.thread_of(rows[0].get("owner_key")) if rows else None
    if not thread or thread.startswith("study-"):
        return
    with _lock, cdb() as conn:
        n = rounds(conn, tid)[0]
    ok = run.status == "ok"
    line = push.first_line(run.text) if ok else ""
    title = title_of(rows[0])
    await push.send_push(push.thread_title(thread), f"{title} · {push.cut(line, 80)}" if line else title,
                         {"thread": thread, "target": {"type": "thread", "thread": thread}}, thread_id=thread,
                         subtitle=L(f"改好了 · 第 {n} 轮", f"Revised · round {n}") if ok else L("这一轮没改成", "This round failed"),
                         level="quiet", collapse=f"task:{tid}", kind="done")


def start() -> None:
    """服务启动时（main.py）：开始盯台账推「做完了」。"""
    spawn(watch_tasks())
