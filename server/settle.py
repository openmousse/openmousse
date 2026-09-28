"""后台任务做完后，派它的那个对话里模型的回话接回 app（2026-09-28）。

从 app 派的后台任务（OpenClaw sessions_spawn，requesterOrigin.channel = webchat）做完后，OpenClaw 在派它的会话里再跑一轮，
让模型把结果告诉用户（provenance subagent_announce / subagent_settle）。这一轮的回复按 webchat 投递，以前只进了 Gateway 网页版，
app 里看不到。子任务在派它的那一轮还没结束时就做完的，结果并进那一轮（app 本来就收得到），不另起一轮。Telegram 这类渠道派的，
OpenClaw 在那边回，这里不管。这一轮的 runId 有两种（OpenClaw announce-idempotency / requester-settle-wake）：
- announce:v1:<子会话 key>:<子任务 runId>：一个子任务做完，派它的那一轮已经结束；
- announce:requester-settle:<Agent>:<派活的会话>:<这一批子任务的 runId，逗号分隔>[:yield-N][:retry-N]：派活的那一轮 yield 着等，
  或者几个一起结算。太长时 OpenClaw 把中间截成「…」+ 结尾几个字（2026-09-28 实测 `…1f1f:yield-1`），会话记录里也是这个样子。

- 实时（chat.transport = ws）：gateway_ws 看到一轮没人认领，第一次出事件时问 adopt。announce 轮次、会话是 app 的线程，就先认领
  （之后的事件进队列，一条不丢），再核对台账：结算的是 app 派的任务、这个线程在 app 里有记录，就先记一行「后台任务做完了：<标题>」
  （role auto，app 显示成一行灰字），再像插话变成单独一轮那样接管：回复照常流式进 app、入库、这一轮里又派的任务挂到它下面。
  对不上就放掉。
- 补漏（两种通道都跑，每 30 秒）：最近 30 分钟做完、已经投递（delivery.deliveredAt）、还没记下的 app 任务，从派它的会话的
  chat.history 里找结算它的那一轮，把最后一条回复补进来。HTTP 通道、服务重启、断线漏收的都靠它；找不到（并进了派它的那一轮）
  就只记一笔，不再找。
- 表 settle_replies（runId, 任务）记下处理过的，两条路不重复写。
- 不另推送：任务做完已经有一条静默推送（cards.push_done）。这条回复算进未读（origin user：是用户要的活）。
"""
from __future__ import annotations

import asyncio
import re
import time

import cards
import chat
from chat import _lock, now_iso
from i18n import L

MARK = "【自动触发】"   # 协议标记，不翻译：app 按它把这一行显示成灰字
CATCH_UP_EVERY = 30.0   # 补漏多久看一次（秒）
CATCH_UP_WINDOW = 30 * 60  # 做完多久以内的才补：服务停过一阵再起来，不补老的
GRACE = 20              # 投递完多少秒以后补漏才动手：先让实时那条路接
RECENT = 6 * 3600       # requester-settle 那种 runId：在派活的会话这么久以内结束的任务里找
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
TAIL = re.compile(r"…([0-9A-Za-z_-]+)")
_taking: set[str] = set()  # 正在接管的 announce runId


def sdb():
    conn = chat.db()
    conn.execute("""CREATE TABLE IF NOT EXISTS settle_replies (run_id TEXT NOT NULL, task_id TEXT NOT NULL, thread TEXT NOT NULL,
        message_id INTEGER, created_at TEXT NOT NULL, PRIMARY KEY (run_id, task_id))""")
    return conn


# —— 这一轮在结算哪几个任务 ——————————————————————————————————————————————————

def announce_id(r: dict) -> str:
    """台账里一个子任务做完、派它的那一轮已经结束时，派它的会话里结算那一轮的 runId。"""
    return f"announce:v1:{r.get('child_session_key')}:{r.get('run_id')}"


def matches(rid: str, r: dict) -> bool:
    """runId 为 rid 的那一轮是不是在结算台账里的这个任务 r（两种 runId 见文件开头）。"""
    if rid.startswith("announce:v1:"):
        return rid == announce_id(r)
    run = str(r.get("run_id") or "")
    if not rid.startswith("announce:requester-settle:") or not run:
        return False
    if run in UUID.findall(rid):
        return True
    m = TAIL.search(rid)  # 中间被截掉了：按「…」后面的结尾对（结尾是这一批最后一个子任务的 runId）
    return bool(m) and run.endswith(m.group(1))


def requester(r: dict) -> str | None:
    """派这个任务的会话。"""
    return (r.get("payload") or {}).get("requesterSessionKey") or r.get("owner_key")


def tasks_for(rid: str, key: str) -> list[dict]:
    """rid 这一轮结算的台账任务（派活的会话是 key）。读不到台账 → 空。"""
    if rid.startswith("announce:v1:"):
        rows = cards.ledger("run_id=?", (rid.rsplit(":", 1)[-1],), 1) or []
    else:
        since = int((time.time() - RECENT) * 1000)
        rows = cards.ledger("owner_key=? AND status NOT IN ('running','queued','pending') AND IFNULL(ended_at, created_at)>=?",
                            (key, since), 50) or []
    return [r for r in rows if matches(rid, r) and requester(r) == key]


def from_app(r: dict) -> bool:
    return ((r.get("payload") or {}).get("requesterOrigin") or {}).get("channel") == "webchat"


def known(thread: str) -> bool:
    """这个线程 app 里有记录（别的工具在 agent:main:grava:… 下开的会话不算 app 的线程）。"""
    with _lock, chat.db() as conn:
        return conn.execute("SELECT 1 FROM messages WHERE thread=? LIMIT 1", (thread,)).fetchone() is not None


def seen(rid: str) -> bool:
    with _lock, sdb() as conn:
        return conn.execute("SELECT 1 FROM settle_replies WHERE run_id=? LIMIT 1", (rid,)).fetchone() is not None


def settled(task_id: str) -> bool:
    with _lock, sdb() as conn:
        return conn.execute("SELECT 1 FROM settle_replies WHERE task_id=? LIMIT 1", (task_id,)).fetchone() is not None


def mark(rid: str, rows: list[dict], thread: str, message_id: int | None) -> None:
    with _lock, sdb() as conn:
        conn.executemany("INSERT INTO settle_replies(run_id, task_id, thread, message_id, created_at) VALUES(?,?,?,?,?) "
                         "ON CONFLICT(run_id, task_id) DO UPDATE SET message_id=IFNULL(excluded.message_id, message_id)",
                         [(rid, r.get("task_id") or "", thread, message_id, now_iso()) for r in rows])


def line(rows: list[dict]) -> str:
    """回复前面那一行灰字：哪个后台任务、做成没有。"""
    titles = [cards.title_of(r) for r in rows]
    n = len(rows)
    if n > 1:
        return MARK + L(f"{n} 个后台任务结束了：{'、'.join(titles[:2])}{' 等' if n > 2 else ''}",
                        f"{n} background tasks ended: {', '.join(titles[:2])}{', …' if n > 2 else ''}")
    r, title = rows[0], titles[0]
    if cards.timed_out(r):
        return MARK + L(f"后台任务到点停了：{title}", f"Background task hit its time limit: {title}")
    if cards.status_of(r) == "完成":
        return MARK + L(f"后台任务做完了：{title}", f"Background task finished: {title}")
    return MARK + L(f"后台任务没做成：{title}", f"Background task failed: {title}")


def add_line(thread: str, rows: list[dict], ts: str) -> int:
    with _lock, chat.db() as conn:
        uid = conn.execute("INSERT INTO messages(thread, role, text, model, ts) VALUES(?,?,?,?,?)", (thread, "auto", line(rows), None, ts)).lastrowid
        conn.execute("INSERT INTO threads(id, updated_at) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at", (thread, ts))
    return uid


# —— 实时：接管这一轮（chat.transport = ws）——————————————————————————————————————

def adopt(p: dict) -> bool:
    """gateway_ws 的钩子：一轮没人认领、第一次出事件时调一次（同步，在读 WebSocket 的协程里）。announce 轮次、会话是 app 的线程
    → 马上认领（之后的事件进队列，一条不丢），在后台核对；返回 True。别的轮次返回 False。"""
    rid = str(p.get("runId") or "")
    key = str(p.get("sessionKey") or "")
    thread = chat.thread_of(key)
    if not rid.startswith("announce:") or not thread or rid in _taking:
        return False
    import gateway_ws as gw_mod  # 延迟导入：只有 chat.transport = ws 时才走到这里
    gw_mod.client().watch(rid)
    _taking.add(rid)
    cards.spawn(take(thread, key, rid))
    return True


async def take(thread: str, key: str, rid: str) -> None:
    import gateway_ws as gw_mod
    c = gw_mod.client()
    try:
        rows = [r for r in await asyncio.to_thread(tasks_for, rid, key) if from_app(r)]
        if not (rows and await asyncio.to_thread(known, thread) and not await asyncio.to_thread(seen, rid)):
            c.unwatch(rid)  # 不是 app 派的（Telegram 那边回）、不是 app 的线程、补漏已经记过：放掉
            return
        while chat.busy(thread):  # 这个线程上一轮还没收完尾（Gateway 也是等它结束才开始这一轮）：等它
            await asyncio.sleep(0.2)
        ts = now_iso()
        uid = await asyncio.to_thread(add_line, thread, rows, ts)
        await asyncio.to_thread(mark, rid, rows, thread, None)
        with _lock, chat.db() as conn:
            model = chat.thread_model(conn, thread)
        run = chat.Run(thread=thread, model=model, user_id=uid, started=ts, key=key if key != chat.session_key(thread) else None,
                       origin="user", level="none", feed_mark=chat.feed_mark(), inbox_mark=chat.max_rowid("inbox"),
                       handoff_mark=chat.max_rowid("handoffs"), gw_run=rid)
        chat.RUNS[thread] = run
        cards.watch(run)  # 这一轮里又派了任务：当场出任务卡
        await chat.run_gateway(run, None, "")
        await asyncio.to_thread(mark, rid, rows, thread, run.reply_id)
    except Exception:  # noqa: BLE001 — 接不上只是这条回话留给补漏
        c.unwatch(rid)
    finally:
        _taking.discard(rid)


# —— 补漏：从 chat.history 找回来（两种通道都跑）——————————————————————————————————

def message_text(m: dict) -> str:
    c = m.get("content")
    if isinstance(c, str):
        return c
    return "".join(x.get("text", "") for x in (c or []) if isinstance(x, dict) and x.get("type") == "text")


def model_of(m: dict) -> str | None:
    prov, model = m.get("provider") or "", m.get("model")
    if not model:
        return None
    return f"{chat.PROVIDER_ALIAS.get(prov, prov)}/{model}" if prov else model


def save(thread: str, rows: list[dict], text: str, model: str | None, t0: float | None, t1: float | None) -> int:
    """补回来的：一行灰字 + 回复，时间用这一轮实际的时间。"""
    ts0 = cards.iso(t0) or now_iso()
    add_line(thread, rows, ts0)
    with _lock, chat.db() as conn:
        return conn.execute("INSERT INTO messages(thread, role, text, model, ts, status, origin) VALUES(?,?,?,?,?,?,?)",
                            (thread, "grava", text, model, cards.iso(t1) or ts0, "ok", "user")).lastrowid


async def catch_up() -> int:
    """最近做完、已经投递、还没记下的 app 任务：去派它的会话的 chat.history 里找结算它的那一轮。返回补了几条回复。"""
    now = time.time() * 1000
    rows = await asyncio.to_thread(cards.ledger, "status NOT IN ('running','queued','pending') AND IFNULL(ended_at, created_at)>=?",
                                   (int(now - CATCH_UP_WINDOW * 1000),), 50)
    todo: dict[str, list[dict]] = {}  # 派活的会话 → 要补的任务
    for r in rows or []:
        d = (r.get("payload") or {}).get("delivery") or {}
        key = requester(r)
        thread = chat.thread_of(key)
        if not from_app(r) or not thread or d.get("status") != "delivered" or not d.get("deliveredAt") or now - d["deliveredAt"] < GRACE * 1000:
            continue
        if await asyncio.to_thread(settled, r.get("task_id") or "") or not await asyncio.to_thread(known, thread):
            continue
        todo.setdefault(key, []).append(r)
    n = 0
    for key, tasks in todo.items():
        thread = chat.thread_of(key)
        live = chat.RUNS.get(thread)
        if any(matches(rid, r) for rid in _taking for r in tasks) or (live and not live.done and str(live.gw_run or "").startswith("announce:")):
            continue  # 实时那条路正在接
        try:
            hist = await chat.gateway_call("chat.history", {"sessionKey": key, "limit": 120}, timeout=25)
        except Exception:  # noqa: BLE001 — 这次读不到，下次再试
            continue
        active = set((hist.get("sessionInfo") or {}).get("activeRunIds") or [])
        by_run: dict[str, list[dict]] = {}
        for m in hist.get("messages") or []:
            rid = str((m.get("__openclaw") or {}).get("runId") or "")
            if m.get("role") == "assistant" and rid.startswith("announce:"):
                by_run.setdefault(rid, []).append(m)
        for r in tasks:
            rids = [rid for rid in by_run if matches(rid, r)]
            if any(rid in active for rid in rids):
                continue  # 结算它的那一轮还在跑
            if not rids:  # 没有单独的一轮（并进了派它的那一轮）：记一笔，不再找
                await asyncio.to_thread(mark, "", [r], thread, None)
                continue
            rid = rids[-1]
            if await asyncio.to_thread(seen, rid):  # 同一批的别的任务已经把这一轮补进来了
                await asyncio.to_thread(mark, rid, [r], thread, None)
                continue
            batch = [x for x in tasks if matches(rid, x)]
            said = [m for m in by_run[rid] if message_text(m).strip()]
            mid = None
            if said:
                mid = await asyncio.to_thread(save, thread, batch, message_text(said[-1]).strip(), model_of(said[-1]),
                                              by_run[rid][0].get("timestamp"), said[-1].get("timestamp"))
                n += 1
            await asyncio.to_thread(mark, rid, batch, thread, mid)
    return n


async def loop() -> None:
    await asyncio.sleep(20)
    while True:
        try:
            if chat.transport() == "ws":  # 保持对话通道连着：空闲时断了也重连，做完的那一轮才能实时接上
                import gateway_ws as gw_mod
                await gw_mod.client().ensure()
        except Exception:  # noqa: BLE001
            pass
        try:
            await catch_up()
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(CATCH_UP_EVERY)


def start() -> None:
    """服务启动时（main.py）：挂上 gateway_ws 的钩子，开始补漏。"""
    import gateway_ws as gw_mod
    gw_mod.ADOPT = adopt
    cards.spawn(loop())
