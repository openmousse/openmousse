"""提醒：Agent 自己的定时推送规则。到点查一张表，查出东西就推一条，比如「每周六 10:00：常备的东西快没了 → 『家里快没了：鸡蛋、酸奶』」。

- 新推送要用户点头：Agent 只能 propose（收件箱 kind push 的卡，写清几点、查什么、按现在的数据会推什么），点了同意才开（inbox.HOOKS["push"]）。
  开着的在 Agent 看板最底下「提醒」里能暂停、恢复、删掉。功能包带的提醒，装包时每条另外出一张卡（propose_from_pack）。
- 规则（spec）：{id, title, source: 查询 Q（和看板的列表一样）, row: 每一行写成什么（「{name}」）, message: 推送正文（「家里快没了：{items}」，
  {items} 是前 limit 行、{count} 是总共几行）, at: "10:00", days: ["sat"]（mon…sun）或不写 = 每天, level: quiet 静默（默认）| ring 响铃, limit: 6}。
- 到点查出来是空的就不推。服务器那会儿没开着：3 小时内补推一次，过了就等下一次。点开推送进这个 Agent 的看板。
- 表 alerts（grava.db）：draft 等点头 → live 开着 / paused 暂停；rejected 没同意；deleted 删了；superseded 被同一个 id 的新规则换掉。
"""
from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter
from pydantic import BaseModel

import boards
import data
import inbox
import push
from boards import bad, bdb
from chat import TZ, _lock, log_activity, now_iso
from i18n import L

router = APIRouter()
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
HM = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
LATE = timedelta(hours=3)  # 错过了多久以内还补推
EVERY = 30                 # 多少秒看一次到点没有
MAX_ALERTS = 10            # 每个 Agent 最多几条开着的
_bg: set[asyncio.Task] = set()


def adb() -> sqlite3.Connection:
    conn = bdb()
    conn.execute("""CREATE TABLE IF NOT EXISTS alerts (id TEXT PRIMARY KEY, agent TEXT NOT NULL, key TEXT NOT NULL, spec TEXT NOT NULL,
        status TEXT NOT NULL, inbox_id TEXT, pack TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, last_run TEXT, last_sent TEXT,
        last_body TEXT)""")
    conn.execute("CREATE INDEX IF NOT EXISTS alerts_agent ON alerts(agent, status)")
    conn.execute("CREATE INDEX IF NOT EXISTS alerts_inbox ON alerts(inbox_id)")
    return conn


# —— 规则 ————————————————————————————————————————————————————————

def clean(conn: sqlite3.Connection, agent: str, raw: object) -> dict:
    if not isinstance(raw, dict):
        raise bad("提醒写成 {id, title, source, row, at, days, level}", "A reminder is {id, title, source, row, at, days, level}")
    key = str(raw.get("id") or "").strip()
    if not boards.BLOCK_ID.match(key):
        raise bad(f"提醒的 id「{key}」不行：小写字母或数字开头，只用 a-z 0-9 _ -", f'Bad reminder id "{key}": a-z 0-9 _ - only')
    title = str(raw.get("title") or "").strip()[:30]
    if not title:
        raise bad("提醒要写 title（比如「补货提醒」）", 'A reminder needs a title (e.g. "Restock reminder")')
    boards.check_query(conn, agent, raw.get("source"), "rows")
    coll = boards.get_coll(conn, agent, str(raw["source"]["from"]))
    row = str(raw.get("row") or "").strip()[:200] or next((f["key"] for f in coll["fields"] if f["type"] == "text"), coll["fields"][0]["key"])
    at = str(raw.get("at") or "").strip()
    if not HM.match(at):
        raise bad("at 写成 HH:MM（比如 10:00）", "at is HH:MM (e.g. 10:00)")
    h, m = at.split(":")
    days = raw.get("days") or []
    if days in ("daily", "every", ["daily"]):
        days = []
    if isinstance(days, str):
        days = [days]
    days = [str(d).strip().lower()[:3] for d in days]
    if any(d not in DAYS for d in days):
        raise bad(f"days 只能写 {' / '.join(DAYS)}（不写 = 每天）", f"days must be from {', '.join(DAYS)} (leave out for every day)")
    level = str(raw.get("level") or "quiet")
    if level not in ("quiet", "ring"):
        raise bad("level 只能是 quiet（静默）/ ring（响铃）", "level must be quiet or ring")
    message = str(raw.get("message") or "").strip()[:120] or "{items}"
    if "{items}" not in message and "{count}" not in message:
        raise bad("message 里要有 {items}（查出来的前几样）或 {count}（几样）", "message needs {items} (the first few rows) or {count}")
    limit = max(1, min(int(raw.get("limit") or 6), 12))
    return {"id": key, "title": title, "source": raw["source"], "row": row, "message": message, "at": f"{int(h):02d}:{m}",
            "days": [d for d in DAYS if d in days], "level": level, "limit": limit}


def when_text(spec: dict) -> str:
    days, at = spec.get("days") or [], spec["at"]
    if not days or len(days) == 7:
        return L(f"每天 {at}", f"Every day at {at}")
    if days == ["mon", "tue", "wed", "thu", "fri"]:
        return L(f"工作日 {at}", f"Weekdays at {at}")
    if days == ["sat", "sun"]:
        return L(f"周末 {at}", f"Weekends at {at}")
    zh = "、".join("一二三四五六日"[DAYS.index(d)] for d in days)
    en = ", ".join(("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")[DAYS.index(d)] for d in days)
    return L(f"每周{zh} {at}", f"{en} at {at}")


def level_text(level: str) -> str:
    return L("响铃推送", "Rings") if level == "ring" else L("静默推送（不响，进通知中心）", "Silent (no sound, goes to Notification Centre)")


def body_of(conn: sqlite3.Connection, agent: str, spec: dict) -> tuple[str | None, int]:
    """现在查一遍：推送正文（查出来是空的 → None）和总共几行。"""
    coll, rows, total = boards.run_rows(conn, agent, {**spec["source"], "limit": max(spec.get("limit") or 6, 1)})
    if not total:
        return None, 0
    fields = {f["key"]: f for f in coll["fields"]}
    items = [boards.render(spec["row"], r["data"], fields) for r in rows[: spec.get("limit") or 6]]
    items = [x for x in items if x and x != "—"]
    more = total - len(items)
    joined = L("、", ", ").join(items) + (L(f" 等 {total} 样", f" and {more} more") if more > 0 else "")
    return spec["message"].replace("{items}", joined).replace("{count}", str(total)), total


def row_json(conn: sqlite3.Connection, r: sqlite3.Row) -> dict:
    spec = json.loads(r["spec"])
    try:
        preview, n = body_of(conn, r["agent"], spec)
    except Exception as e:  # noqa: BLE001 — 表被改坏了：只影响这一条的预览
        preview, n = L(f"查不了：{getattr(e, 'detail', e)}", f"Can't check: {getattr(e, 'detail', e)}"), 0
    return {"id": r["id"], "key": r["key"], "agent": r["agent"], "title": spec["title"], "status": r["status"], "when": when_text(spec),
            "level": spec["level"], "levelText": level_text(spec["level"]), "preview": preview, "count": n, "pack": r["pack"],
            "lastSent": r["last_sent"], "lastBody": r["last_body"], "inboxId": r["inbox_id"], "spec": spec}


# —— 到点就推 ————————————————————————————————————————————————————————

def due(spec: dict, last_run: str | None, now: datetime) -> bool:
    h, m = (int(x) for x in spec["at"].split(":"))
    at = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if spec.get("days") and DAYS[now.weekday()] not in spec["days"]:
        return False
    if not at <= now < at + LATE:
        return False
    return not (last_run and last_run[:10] == now.date().isoformat() and last_run[11:16] >= spec["at"])


async def fire(r: sqlite3.Row, now: datetime) -> None:
    spec = json.loads(r["spec"])
    with _lock, adb() as conn:
        conn.execute("UPDATE alerts SET last_run=? WHERE id=?", (now.isoformat(timespec="seconds"), r["id"]))
        try:
            body, _ = body_of(conn, r["agent"], spec)
        except Exception:  # noqa: BLE001 — 表归档了、字段改没了：这次不推，也不刷屏
            body = None
    if not body:
        return
    await push.send_push(push.thread_title(r["agent"]), body,
                         {"thread": r["agent"], "target": {"type": "board", "agent": r["agent"]}}, thread_id=f"alert:{r['agent']}",
                         subtitle=spec["title"], level=spec["level"], collapse=f"alert:{r['id']}", kind="report")
    with _lock, adb() as conn:
        conn.execute("UPDATE alerts SET last_sent=?, last_body=? WHERE id=?", (now_iso(), body, r["id"]))
    log_activity(L(f"提醒「{spec['title']}」：{body}", f'Reminder "{spec["title"]}": {body}'), "push", actor=data.agent_label(r["agent"]))


async def check_due(now: datetime | None = None) -> int:
    now = now or datetime.now(TZ)
    with _lock, adb() as conn:
        rows = conn.execute("SELECT * FROM alerts WHERE status='live'").fetchall()
    n = 0
    for r in rows:
        if due(json.loads(r["spec"]), r["last_run"], now):
            try:
                await fire(r, now)
                n += 1
            except Exception:  # noqa: BLE001 — 一条出错不影响别的
                continue
    return n


async def loop() -> None:
    await asyncio.sleep(20)
    while True:
        try:
            await check_due()
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(EVERY)


def start() -> None:
    """服务启动时（main.py）：开始按点检查提醒。"""
    t = asyncio.create_task(loop())
    _bg.add(t)
    t.add_done_callback(_bg.discard)


# —— 提案和收件箱 ————————————————————————————————————————————————————

async def propose(agent: str, spec_raw: dict, *, why: str = "", title: str = "", pack: str | None = None) -> dict:
    """存一条草稿、交一张收件箱卡（kind push）。同一个 Agent 同一个 id 还在等的：原地更新那张卡。"""
    g = boards.group_row(agent)
    with _lock, adb() as conn:
        spec = clean(conn, agent, spec_raw)
        preview, n = body_of(conn, agent, spec)
        live = conn.execute("SELECT COUNT(*) FROM alerts WHERE agent=? AND status IN ('live','paused') AND key!=?", (agent, spec["id"])).fetchone()[0]
    if live >= MAX_ALERTS:
        raise bad(f"一个 Agent 最多 {MAX_ALERTS} 条提醒", f"At most {MAX_ALERTS} reminders per Agent")
    now_line = (L(f"按现在的数据会推：「{preview}」", f'With today\'s data it would say: "{preview}"') if preview
                else L("按现在的数据查出来是空的，到点不会推", "With today's data nothing matches, so it wouldn't send anything"))
    res = await inbox.add(inbox.ItemIn(
        kind="push", source=agent, title=title.strip() or L(f"{spec['title']}：{when_text(spec)}", f"{spec['title']}: {when_text(spec)}"),
        why=why, changes=[L(f"什么时候：{when_text(spec)}", f"When: {when_text(spec)}"), L(f"怎么推：{level_text(spec['level'])}", f"How: {level_text(spec['level'])}"),
                          L("开了以后在看板最底下「提醒」里能暂停、删掉", "Once on, pause or delete it under Reminders at the bottom of the board")],
        detail=now_line,  # app 在卡片上画成一条通知（/api/alerts/proposal），这句留给看不到预览的地方
        approveLabel=L("开这个提醒", "Turn it on"), dedupe=f"alert:{agent}:{spec['id']}"))
    if not isinstance(res, dict):
        return res
    iid, ts = res["id"], now_iso()
    with _lock, adb() as conn:
        conn.execute("UPDATE alerts SET status='superseded', updated_at=? WHERE inbox_id=? AND status='draft'", (ts, iid))
        conn.execute("INSERT INTO alerts(id, agent, key, spec, status, inbox_id, pack, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                     (f"al-{uuid.uuid4().hex[:8]}", agent, spec["id"], json.dumps(spec, ensure_ascii=False), "draft", iid, pack, ts, ts))
    log_activity(L(f"{g['name']}想加一个提醒「{spec['title']}」，等你点头", f'{g["name"]} wants to add a reminder "{spec["title"]}"'), "inbox")
    return {"ok": True, "inboxId": iid, "count": n, **({"updated": True} if res.get("updated") else {})}


async def propose_from_pack(agent: str, pack: str) -> list[str]:
    """功能包装好了：包里的提醒每条出一张卡（已经开着同一个 id 的就不再提）。"""
    import packs  # noqa: PLC0415 — packs 引用本模块
    p = packs.load(pack)
    out = []
    for a in p["alerts"]:
        if not isinstance(a, dict):
            continue
        with _lock, adb() as conn:
            on = conn.execute("SELECT 1 FROM alerts WHERE agent=? AND key=? AND status IN ('live','paused')", (agent, str(a.get("id")))).fetchone()
        if on:
            continue
        try:
            res = await propose(agent, a, why=str(a.get("why") or ""), pack=pack)
        except Exception:  # noqa: BLE001 — 一条写错了不影响装包和别的提醒
            continue
        if isinstance(res, dict) and res.get("inboxId"):
            out.append(res["inboxId"])
    return out


async def on_push_decided(it: dict, action: str) -> dict | None:
    """收件箱 kind=push 的卡被点了：挂着提醒草稿的，同意 → 开（同一个 id 原来开着的换掉），不要 / 撤回 → 作废。没挂提醒的照旧交给 Agent。"""
    with _lock, adb() as conn:
        r = conn.execute("SELECT * FROM alerts WHERE inbox_id=? AND status='draft' ORDER BY created_at DESC LIMIT 1", (it["id"],)).fetchone()
        if not r:
            return None
        ts = now_iso()
        if action != "approve":
            conn.execute("UPDATE alerts SET status='rejected', updated_at=? WHERE id=?", (ts, r["id"]))
            return {"rejected": True}
        conn.execute("UPDATE alerts SET status='superseded', updated_at=? WHERE agent=? AND key=? AND status IN ('live','paused')", (ts, r["agent"], r["key"]))
        # last_run 记成现在：今天那个点已经过了的，不会一同意就推一条（卡片上已经看过会推什么）
        conn.execute("UPDATE alerts SET status='live', updated_at=?, last_run=? WHERE id=?", (ts, datetime.now(TZ).isoformat(timespec="seconds"), r["id"]))
        spec = json.loads(r["spec"])
    return {"result": L(f"提醒开了：{when_text(spec)}，{spec['title']}", f"Reminder on: {spec['title']}, {when_text(spec)}")}


inbox.HOOKS["push"] = on_push_decided


# —— 接口 ————————————————————————————————————————————————————————

@router.get("/api/alerts/{agent}")
def list_alerts(agent: str):
    """开着的和暂停的（看板最底下「提醒」）。"""
    boards.group_row(agent)
    with _lock, adb() as conn:
        rows = conn.execute("SELECT * FROM alerts WHERE agent=? AND status IN ('live','paused') ORDER BY created_at", (agent,)).fetchall()
        return {"ok": True, "alerts": [row_json(conn, r) for r in rows]}


@router.get("/api/alerts/proposal/{iid}")
def proposal(iid: str):
    """收件箱卡的预览：几点、怎么推、按现在的数据会推什么。"""
    with _lock, adb() as conn:
        r = conn.execute("SELECT * FROM alerts WHERE inbox_id=? ORDER BY created_at DESC LIMIT 1", (iid,)).fetchone()
        if not r:
            raise bad("这张卡没有提醒", "This card has no reminder", 404)
        return {"ok": True, **row_json(conn, r)}


class SpecIn(BaseModel):
    spec: dict
    why: str = ""
    title: str = ""


@router.post("/api/alerts/{agent}/check")
def check(agent: str, body: SpecIn):
    """只校验、看看现在会推什么（board_ctl.py alert check）。"""
    boards.group_row(agent)
    with _lock, adb() as conn:
        spec = clean(conn, agent, body.spec)
        preview, n = body_of(conn, agent, spec)
    return {"ok": True, "spec": spec, "when": when_text(spec), "levelText": level_text(spec["level"]), "preview": preview, "count": n}


@router.post("/api/alerts/{agent}/propose")
async def propose_api(agent: str, body: SpecIn):
    return await propose(agent, body.spec, why=body.why, title=body.title)


class StatusIn(BaseModel):
    status: str   # live 恢复 / paused 暂停 / deleted 删掉


@router.post("/api/alerts/item/{aid}")
def set_status(aid: str, body: StatusIn):
    """看板上点的：暂停、恢复、删掉（恢复只能恢复同意过的，没同意过的草稿不能自己开）。"""
    if body.status not in ("live", "paused", "deleted"):
        raise bad("status 只能是 live / paused / deleted", "status must be live, paused or deleted")
    with _lock, adb() as conn:
        r = conn.execute("SELECT * FROM alerts WHERE id=?", (aid,)).fetchone()
        if not r or r["status"] not in ("live", "paused"):
            raise bad("没有这条提醒（或者它还没被同意）", "No such reminder (or it hasn't been approved)", 404)
        conn.execute("UPDATE alerts SET status=?, updated_at=? WHERE id=?", (body.status, now_iso(), aid))
        title = json.loads(r["spec"])["title"]
    verb = {"live": L("恢复了", "Resumed"), "paused": L("暂停了", "Paused"), "deleted": L("删了", "Deleted")}[body.status]
    log_activity(L(f"{verb}提醒「{title}」", f'{verb} the reminder "{title}"'), "edit")
    return {"ok": True}
