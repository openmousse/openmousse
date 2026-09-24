"""Grava API：app 里对话和看板之外的全部数据。2026-09-23 起每一项都来自真实来源，不再有示例。

| 页面 | 真源 |
|---|---|
| Groups、独立空间、目标、建议、形象设置、app 侧活动 | grava.db（记忆规范的 L4） |
| 等你点头 | OpenClaw 审批队列（`openclaw approvals pending / resolve`） |
| 接下来会自动做的事 | OpenClaw cron（`cron.list / cron.update`）+ systemd user timer（只读） |
| 任务 | OpenClaw 子会话（`tasks.list`；详情读子会话的 `chat.history`） |
| 活动记录 | app 的 activity_log + Gateway 审计（`audit.activity.list`，只有元数据）+ 定时任务的运行记录 |
| 基础档案 | L0 `~/.openclaw/shared/profile/USER.md`：改一条就写回，旧版本存 `grava/profile-history.md`（不在检索路径里） |
| 记忆 | L1 各 agent 工作区的 `MEMORY.md`：忘记 = 删掉这一条，活动记录只留一行、不含内容 |
| 日志（Group 记忆页、我 → 日志） | grava.db `journal`（Grava 经 `scripts/grava_journal.py` 写） |
| 求职 / 申请学校看板 | grava.db `applications`（Grava 经 `scripts/grava_apps.py` 写；kind=masters 进「申请学校」，其余进「求职」） |
| 模型与计费 | openclaw.json 的默认链、子会话默认、允许列表 + `models.authStatus` |
| 安全 | 配置和运行状态的实测结果，外加蓝图里还没做的计划项（标明"计划"） |

Gateway 走 `openclaw gateway call`，每次起一个 node 进程（约 2 秒），所以读接口都带短缓存。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import shutil
import time
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from chat import OPENCLAW, TZ, _lock, db, gateway_call, log_activity, now_iso, session_key, start_run

router = APIRouter()

import agents  # noqa: E402
from config import settings as cfg  # noqa: E402（这个模块里 settings 是接口函数名）

HOME = cfg.openclaw_home
PROFILE = cfg.profile
PROFILE_HISTORY = cfg.profile_history
MEMORY = cfg.workspace / "MEMORY.md"
# 每个 agent 的长期记忆（L1）。有独立 workspace 的 Agent 各有自己的 MEMORY.md（server.json 的 agent_workspaces）。
WEEKDAYS = "一二三四五六日"


# —— 基础设施 ————————————————————————————————————————————————

def ddb():
    """应用数据库，加上本模块的表。白板：新实例没有任何预置 Agent、目标或记录。"""
    conn = db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS groups (id TEXT PRIMARY KEY, name TEXT NOT NULL, icon TEXT NOT NULL, purpose TEXT,
            dashboard TEXT NOT NULL DEFAULT 'none', position INTEGER NOT NULL DEFAULT 99, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS side_chats (id TEXT PRIMARY KEY, title TEXT NOT NULL, purpose TEXT,
            archived INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS goals (id TEXT PRIMARY KEY, category TEXT NOT NULL, title TEXT NOT NULL, detail TEXT,
            metric TEXT, unit TEXT, target_low REAL, target_high REAL, due TEXT, group_id TEXT, source TEXT,
            status TEXT NOT NULL DEFAULT 'active', position INTEGER NOT NULL DEFAULT 99, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS feed_items (id TEXT PRIMARY KEY, group_id TEXT, title TEXT NOT NULL, body TEXT, cta TEXT,
            created_at TEXT NOT NULL, dismissed INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS journal (id TEXT PRIMARY KEY, ts TEXT NOT NULL, group_id TEXT, kind TEXT NOT NULL, text TEXT NOT NULL,
            tags TEXT, context TEXT, source TEXT NOT NULL DEFAULT 'chat', status TEXT NOT NULL DEFAULT 'active');
        CREATE TABLE IF NOT EXISTS applications (id TEXT PRIMARY KEY, kind TEXT NOT NULL, org TEXT NOT NULL, role TEXT NOT NULL, deadline TEXT,
            status TEXT NOT NULL DEFAULT 'planned', progress INTEGER NOT NULL DEFAULT 0, next_step TEXT, notes TEXT, link TEXT, materials TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
    """)
    return conn


_cache: dict[str, tuple[float, Any]] = {}


async def cached(key: str, ttl: float, fn: Callable[[], Awaitable[Any]]) -> Any:
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    val = await fn()
    _cache[key] = (time.time(), val)
    return val


def forget_cache(*prefixes: str) -> None:
    for k in list(_cache):
        if k.startswith(prefixes):
            _cache.pop(k, None)


async def openclaw_cli(*args: str, timeout: float = 30) -> Any:
    exe = shutil.which(cfg.openclaw_bin) or cfg.openclaw_bin
    proc = await asyncio.create_subprocess_exec(exe, *args, "--json", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await asyncio.wait_for(proc.communicate(), timeout)
    if proc.returncode != 0:
        raise HTTPException(502, f"openclaw {' '.join(args[:2])} 失败：{(err or out).decode('utf8', 'replace')[-300:]}")
    return json.loads(out)


def london(ms: float | None) -> datetime | None:
    return datetime.fromtimestamp(ms / 1000, TZ) if ms else None


def when(dt: datetime | None, with_time: bool = True) -> str:
    """今天 09:00 / 明天 09:00 / 昨天 21:05 / 周五 09:00 / 9 月 30 日 09:00。"""
    if not dt:
        return ""
    d = (dt.date() - datetime.now(TZ).date()).days
    hm = dt.strftime("%H:%M") if with_time else ""
    day = {0: "今天", 1: "明天", -1: "昨天"}.get(d) or (f"周{WEEKDAYS[dt.weekday()]}" if 1 < d < 7 else f"{dt.month} 月 {dt.day} 日")
    return f"{day} {hm}".strip()


def config() -> dict:
    try:
        return json.loads(OPENCLAW.read_text(encoding="utf8"))
    except (OSError, ValueError):
        return {}


def thread_names() -> dict[str, str]:
    with _lock, ddb() as conn:
        names = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM groups")}
        names |= {r["id"]: r["title"] for r in conn.execute("SELECT id, title FROM side_chats")}
    return names


def surface(key: str, names: dict[str, str]) -> str:
    """session key → 人话：主对话 / 某个 Group / 某个独立空间 / 子会话 / 定时任务。"""
    if not key:
        return ""
    if key == "agent:main:main":
        return "主对话"
    parts = key.split(":")
    if len(parts) >= 4 and parts[2] == "grava":
        tid = ":".join(parts[3:])
        return names.get(tid) or f"app 线程 {tid}"
    if len(parts) >= 3 and parts[2] == "subagent":
        return "子会话"
    if len(parts) >= 3 and parts[2] == "cron":
        return "定时任务"
    if len(parts) >= 3 and parts[2] in ("telegram", "discord"):
        return parts[2].capitalize()
    return key


def last_lines() -> dict[str, tuple[str, str]]:
    """每个线程最后一条消息：(文字, 时间)。"""
    with _lock, ddb() as conn:
        rows = conn.execute("""SELECT m.thread, m.text, m.ts FROM messages m
            JOIN (SELECT thread, MAX(id) mid FROM messages GROUP BY thread) x ON x.mid = m.id""").fetchall()
    return {r["thread"]: (r["text"], r["ts"]) for r in rows}


def short(text: str, n: int = 60) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= n else text[: n - 1] + "…"


# —— Groups 与独立空间 ————————————————————————————————————————————

class GroupIn(BaseModel):
    name: str
    purpose: str = ""
    icon: str = "moon"
    model: str
    skills: list[str] | None = None  # 不给就用 server.json 的 agent_default_skills


@router.get("/api/groups")
def groups():
    last = last_lines()
    with _lock, ddb() as conn:
        rows = conn.execute("SELECT g.*, t.model FROM groups g LEFT JOIN threads t ON t.id = g.id ORDER BY position, created_at").fetchall()
    return {"ok": True, "groups": [{"id": r["id"], "name": r["name"], "icon": r["icon"], "purpose": r["purpose"] or "", "modelId": r["model"],
                                    "dashboard": r["dashboard"], "lastLine": short(last.get(r["id"], ("", ""))[0])} for r in rows]}


@router.post("/api/groups")
def create_group(body: GroupIn):
    """新建 Agent = 建一个独立的 OpenClaw agent（workspace、记忆、skills）+ groups 表一行。失败就什么都不留。"""
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Agent 要有名字")
    gid = f"g-{uuid.uuid4().hex[:8]}"
    ts = now_iso()
    try:
        agents.provision(gid, name, body.purpose.strip(), body.icon, skills=body.skills)
    except agents.ProvisionError as e:
        raise HTTPException(502, str(e)) from e
    with _lock, ddb() as conn:
        conn.execute("INSERT INTO groups(id, name, icon, purpose, created_at) VALUES(?,?,?,?,?)", (gid, name, body.icon, body.purpose.strip(), ts))
        conn.execute("INSERT INTO threads(id, model, updated_at) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET model=excluded.model", (gid, body.model, ts))
    log_activity(f"新建 Agent「{name}」", "edit")
    return {"ok": True, "id": gid}


@router.delete("/api/groups/{gid}")
def delete_group(gid: str):
    """删 Agent：OpenClaw 里的 agent 条目去掉，workspace 整个移到 archive/（记忆不删），groups / threads 行删掉；对话记录、日志、卡片留着当历史。"""
    with _lock, ddb() as conn:
        r = conn.execute("SELECT name FROM groups WHERE id=?", (gid,)).fetchone()
    if not r:
        raise HTTPException(404, "没有这个 Agent")
    try:
        archived = agents.remove(gid)
    except agents.ProvisionError as e:
        raise HTTPException(502, str(e)) from e
    with _lock, ddb() as conn:
        conn.execute("DELETE FROM groups WHERE id=?", (gid,))
        conn.execute("DELETE FROM threads WHERE id=?", (gid,))
    log_activity(f"删了 Agent「{r['name']}」（工作区已归档）", "deleted")
    return {"ok": True, "archived": str(archived) if archived else None}


class SideChatIn(BaseModel):
    title: str
    purpose: str = ""
    model: str


class SideChatPatch(BaseModel):
    title: str | None = None
    archived: bool | None = None


@router.get("/api/sidechats")
def side_chats():
    last = last_lines()
    with _lock, ddb() as conn:
        rows = conn.execute("SELECT s.*, t.model FROM side_chats s LEFT JOIN threads t ON t.id = s.id").fetchall()
    out = []
    for r in rows:
        text, ts = last.get(r["id"], ("", ""))
        updated = max(r["updated_at"], ts or "")
        out.append({"id": r["id"], "title": r["title"], "purpose": r["purpose"] or "", "modelId": r["model"], "archived": bool(r["archived"]),
                    "lastLine": short(text) or "新空间，说点什么开始吧。", "createdAt": when(datetime.fromisoformat(r["created_at"]), False),
                    "updatedAt": int(datetime.fromisoformat(updated).timestamp() * 1000)})
    return {"ok": True, "sideChats": out}


@router.post("/api/sidechats")
def create_side_chat(body: SideChatIn):
    sid = f"sc-{uuid.uuid4().hex[:8]}"
    ts = now_iso()
    with _lock, ddb() as conn:
        conn.execute("INSERT INTO side_chats(id, title, purpose, created_at, updated_at) VALUES(?,?,?,?,?)", (sid, body.title.strip(), body.purpose.strip(), ts, ts))
        conn.execute("INSERT INTO threads(id, model, updated_at) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET model=excluded.model", (sid, body.model, ts))
    log_activity(f"开了独立空间「{body.title.strip()}」", "edit")
    return {"ok": True, "id": sid}


@router.patch("/api/sidechats/{sid}")
def patch_side_chat(sid: str, body: SideChatPatch):
    with _lock, ddb() as conn:
        r = conn.execute("SELECT * FROM side_chats WHERE id=?", (sid,)).fetchone()
        if not r:
            raise HTTPException(404, "没有这个空间")
        if body.title is not None and body.title.strip():
            conn.execute("UPDATE side_chats SET title=?, updated_at=? WHERE id=?", (body.title.strip(), now_iso(), sid))
        if body.archived is not None:
            conn.execute("UPDATE side_chats SET archived=?, updated_at=? WHERE id=?", (int(body.archived), now_iso(), sid))
    if body.archived is not None:
        log_activity(f"{'归档' if body.archived else '恢复'}了独立空间「{r['title']}」", "edit")
    return {"ok": True}


@router.delete("/api/sidechats/{sid}")
async def delete_side_chat(sid: str):
    with _lock, ddb() as conn:
        r = conn.execute("SELECT * FROM side_chats WHERE id=?", (sid,)).fetchone()
        if not r:
            raise HTTPException(404, "没有这个空间")
        conn.execute("DELETE FROM messages WHERE thread=?", (sid,))
        conn.execute("DELETE FROM threads WHERE id=?", (sid,))
        conn.execute("DELETE FROM side_chats WHERE id=?", (sid,))
    try:  # Gateway 那边的会话也删掉（OpenClaw 会压缩存档一份到 sessions/ 下，不是彻底抹掉）
        await gateway_call("sessions.delete", {"key": session_key(sid)}, timeout=20)
    except HTTPException:
        pass  # 从没发过消息的空间在 Gateway 里没有会话
    log_activity(f"删除了独立空间「{r['title']}」的对话记录", "deleted")
    return {"ok": True}


# —— 目标 ——————————————————————————————————————————————————————

def bodyfat_readings() -> list[dict]:
    """体脂读数：训记最新一条 + Apple 健康按天的均值。按日期新到旧。"""
    out = []
    try:
        from sources import xunji  # noqa: PLC0415 — 可选数据源，没接就是 None
        data = xunji.call("body_query", {"include_latest": True, "include_records": False, "limit": 1, "offset": 0}, ttl=3600)
        bf = ((data.get("res") or {}).get("latest") or {}).get("bodyfat") or {}
        if bf.get("value") is not None:
            out.append({"value": float(bf["value"]), "date": bf.get("datestr"), "source": "训记"})
    except Exception:  # noqa: BLE001
        pass
    with _lock, ddb() as conn:
        try:
            for r in conn.execute("SELECT date, avg FROM health_metrics WHERE metric='BodyFatPercentage' AND avg IS NOT NULL ORDER BY date DESC LIMIT 60"):
                v = r["avg"] * 100 if r["avg"] <= 1 else r["avg"]
                out.append({"value": round(v, 1), "date": r["date"], "source": "Apple 健康"})
        except Exception:  # noqa: BLE001  health_metrics 还没建（从没同步过）
            pass
    return sorted(out, key=lambda x: x["date"] or "", reverse=True)


@router.get("/api/goals")
def goals():
    with _lock, ddb() as conn:
        rows = conn.execute("SELECT * FROM goals WHERE status='active' ORDER BY position, created_at").fetchall()
    out = []
    for r in rows:
        g = {"id": r["id"], "category": r["category"], "title": r["title"], "detail": r["detail"] or "", "due": r["due"] or "",
             "groupId": r["group_id"], "source": r["source"] or "", "unit": r["unit"], "targetLow": r["target_low"], "targetHigh": r["target_high"],
             "current": None, "currentDate": None, "currentSource": None, "start": None, "stale": False}
        if r["metric"] == "bodyfat":
            reads = bodyfat_readings()
            if reads:
                cur = reads[0]
                since = [x for x in reads if (x["date"] or "") >= r["created_at"][:10]]
                g.update(current=cur["value"], currentDate=cur["date"], currentSource=cur["source"],
                         start=since[-1]["value"] if since else cur["value"],
                         stale=(date.today() - date.fromisoformat(cur["date"])).days > 30 if cur["date"] else True)
        out.append(g)
    return {"ok": True, "goals": out}


# —— 日志（L4 journal，Grava 经 scripts/grava_journal.py 写） ——————————————————

@router.get("/api/journal")
def journal(group: str | None = None, days: int = 90, limit: int = 100):
    since = (datetime.now(TZ) - timedelta(days=min(max(days, 1), 3650))).isoformat(timespec="seconds")
    q, args = "SELECT * FROM journal WHERE status='active' AND ts>=?", [since]
    if group:
        q += " AND group_id=?"
        args.append(group)
    with _lock, ddb() as conn:
        rows = conn.execute(q + " ORDER BY ts DESC LIMIT ?", (*args, min(max(limit, 1), 500))).fetchall()
    return {"ok": True, "entries": [{"id": r["id"], "ts": r["ts"], "date": r["ts"][:10], "time": r["ts"][11:16], "groupId": r["group_id"], "kind": r["kind"],
                                     "text": r["text"], "tags": json.loads(r["tags"]) if r["tags"] else [], "context": r["context"], "source": r["source"]} for r in rows]}


@router.delete("/api/journal/{jid}")
def delete_journal(jid: str):
    with _lock, ddb() as conn:
        n = conn.execute("UPDATE journal SET status='deleted', text='', tags=NULL, context=NULL WHERE id=? AND status='active'", (jid,)).rowcount
    if n:
        log_activity("已按要求删掉 1 条日志", "forgot")
    return {"ok": bool(n)}


# —— 求职与申请（L4 applications，Grava 经 scripts/grava_apps.py 写） ——————————————

@router.get("/api/applications")
def applications(all: int = 0):
    q, args = "SELECT * FROM applications", []
    if not all:
        q += " WHERE status NOT IN ('offer','rejected','closed')"
    with _lock, ddb() as conn:
        rows = conn.execute(q + " ORDER BY CASE WHEN deadline IS NULL THEN 1 ELSE 0 END, deadline, updated_at DESC", args).fetchall()
    today = datetime.now(TZ).date()
    out = []
    for r in rows:
        try:
            left = (date.fromisoformat(r["deadline"]) - today).days if r["deadline"] else None
        except ValueError:
            left = None
        out.append({"id": r["id"], "kind": r["kind"], "org": r["org"], "role": r["role"], "deadline": r["deadline"], "daysLeft": left, "status": r["status"],
                    "progress": r["progress"], "nextStep": r["next_step"], "notes": r["notes"], "link": r["link"],
                    "materials": json.loads(r["materials"]) if r["materials"] else [], "updatedAt": r["updated_at"][:16].replace("T", " ")})
    return {"ok": True, "applications": out}


# —— 建议（起床报告和主动性，第 8 步之后才有内容） —————————————————————

@router.get("/api/feed")
def feed(date: str | None = None):
    """不带 date：最近 20 条没划掉的（「今天」页默认）。带 date（YYYY-MM-DD）：那一天的建议卡，翻看过去 / 未来用。"""
    with _lock, ddb() as conn:
        if date:
            if len(date) != 10 or date[4] != "-" or date[7] != "-":
                raise HTTPException(400, "date 要写成 YYYY-MM-DD")
            rows = conn.execute("SELECT * FROM feed_items WHERE dismissed=0 AND substr(created_at,1,10)=? ORDER BY created_at DESC LIMIT 50", (date,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM feed_items WHERE dismissed=0 ORDER BY created_at DESC LIMIT 20").fetchall()
    return {"ok": True, "feed": [{"id": r["id"], "groupId": r["group_id"], "title": r["title"], "body": r["body"] or "", "cta": r["cta"] or "", "kind": r["kind"] if "kind" in r.keys() else None, "data": json.loads(r["data"]) if "data" in r.keys() and r["data"] else None, "createdAt": r["created_at"],
                                  "time": when(datetime.fromisoformat(r["created_at"]))} for r in rows]}


@router.post("/api/feed/{fid}/dismiss")
def dismiss_feed(fid: str):
    with _lock, ddb() as conn:
        conn.execute("UPDATE feed_items SET dismissed=1 WHERE id=?", (fid,))
    return {"ok": True}


# —— 接下来会自动做的事：OpenClaw cron + systemd timer ——————————————————

CRON_TITLES = {
    "heartbeat-main": "心跳检查（main）",
    "heartbeat-gemini": "心跳检查（gemini）",
    "memory-dreaming-promotion": "记忆整理：把工作记忆晋升为长期记忆（Dreaming）",
    "skill-collection-review-main": "技能库复查（main）",
    "skill-collection-review-gemini": "技能库复查（gemini）",
}
TIMERS = {
    "lunar-birthday.timer": "父母农历生日提醒",
    "daily-backup.timer": "每日加密备份",
    "weekly-maintenance.timer": "每周系统维护",
    "system-alert.timer": "系统告警检查",
    "log-sanitizer.timer": "日志脱敏",
}


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def repeat_of(schedule: dict, nxt: datetime | None) -> str:
    kind = schedule.get("kind")
    hm = nxt.strftime("%H:%M") if nxt else ""
    if kind == "every":
        ms = schedule.get("everyMs") or 0
        if ms == 86_400_000:
            return f"每天 {hm}".strip()
        if ms == 604_800_000:
            return f"每周{WEEKDAYS[nxt.weekday()]} {hm}".strip() if nxt else "每周"
        if ms and ms % 3_600_000 == 0:
            return f"每 {ms // 3_600_000} 小时"
        return f"每 {round(ms / 60000)} 分钟" if ms else "重复"
    if kind == "cron":
        return cron_words(schedule.get("expr") or "", hm, schedule.get("tz"))
    if kind == "at":
        return "一次"
    return kind or ""


DOW = {"*": "每天", "1-5": "工作日", "2-6": "周二至周六", "0,6": "周末", "6,0": "周末"}
TZ_NAMES = {"Asia/Shanghai": "北京时间", "Europe/London": "伦敦时间", "America/New_York": "纽约时间", "UTC": "UTC", cfg.timezone: ""}  # 自己的时区不加后缀


def cron_words(expr: str, hm: str, tz: str | None) -> str:
    """把常见的 cron 表达式说成人话；认不出来就原样给。hm 是按下次运行算出的伦敦时间，停用的任务没有。"""
    f = expr.split()
    if len(f) != 5:
        return expr or "定时"
    minute, hour, dom, month, dow = f
    zone = TZ_NAMES.get(tz or "", tz or "")
    at = hm or (f"{int(hour):02d}:{int(minute):02d}{'（' + zone + '）' if zone else ''}" if minute.isdigit() and hour.isdigit() else "")
    if minute.startswith("*/") and "-" in hour:
        at = f"{hour.replace('-', '–')} 点每 {minute[2:]} 分钟"
    if month != "*":
        return expr
    if dom.startswith("*/") and dow == "*":
        return f"每 {dom[2:]} 天 {at}".strip()
    if dom != "*":
        return expr
    day = DOW.get(dow) or (f"每周{WEEKDAYS[(int(dow) - 1) % 7]}" if dow.isdigit() else None)
    return f"{day} {at}".strip() if day else expr


async def cron_jobs() -> list[dict]:
    data = await cached("cron", 30, lambda: gateway_call("cron.list", {"includeDisabled": True}, timeout=30))
    out = []
    for j in data.get("jobs", []):
        st = j.get("state") or {}
        nxt = london(st.get("nextRunAtMs")) if j.get("enabled") else None
        name = j.get("displayName") or j.get("name") or j["id"]
        payload = j.get("payload") or {}
        last_status = st.get("lastRunStatus") or st.get("lastStatus")
        out.append({"id": j["id"], "source": "cron", "title": CRON_TITLES.get(slug(j.get("name") or name)) or CRON_TITLES.get(slug(name)) or name,
                    "rawName": name, "agent": j.get("agentId") or "main", "modelId": payload.get("model"),
                    "when": when(nxt) if nxt else "", "repeat": repeat_of(j.get("schedule") or {}, nxt or london(st.get("nextRunAtMs"))),
                    "enabled": bool(j.get("enabled")), "toggleable": True, "nextAt": st.get("nextRunAtMs") or 0,
                    "last": ({"status": last_status, "when": when(london(st.get("lastRunAtMs")))} if st.get("lastRunAtMs") else None)})
    return out


async def timers() -> list[dict]:
    proc = await asyncio.create_subprocess_exec("systemctl", "--user", "list-timers", "--all", "--output=json",
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, _ = await asyncio.wait_for(proc.communicate(), 10)
    rows = []
    for t in json.loads(out or b"[]"):
        unit = t.get("unit") or ""
        if unit.startswith("grava-reminder-") and unit.endswith(".timer"):
            rows.append(await reminder_row(t))
            continue
        if unit not in TIMERS:
            continue
        nxt = datetime.fromtimestamp(t["next"] / 1e6, TZ) if t.get("next") else None
        last = datetime.fromtimestamp(t["last"] / 1e6, TZ) if t.get("last") else None
        rows.append({"id": t["unit"], "source": "systemd", "title": TIMERS[t["unit"]], "rawName": t["unit"], "agent": "系统", "modelId": None,
                     "when": when(nxt), "repeat": "每天" if t["unit"] not in ("weekly-maintenance.timer",) else "每周",
                     "enabled": bool(nxt), "toggleable": False, "nextAt": int(nxt.timestamp() * 1000) if nxt else 0,
                     "last": {"status": None, "when": when(last)} if last else None})
    return rows


async def reminder_row(t: dict) -> dict:
    """Grava 一次性提醒（systemd-run --unit=grava-reminder-*），标题取 unit 的 Description。"""
    unit = t["unit"]
    proc = await asyncio.create_subprocess_exec("systemctl", "--user", "show", unit, "-p", "Description", "--value",
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    desc, _ = await asyncio.wait_for(proc.communicate(), 10)
    title = (desc or b"").decode().strip() or unit
    nxt = datetime.fromtimestamp(t["next"] / 1e6, TZ) if t.get("next") else None
    return {"id": unit, "source": "reminder", "title": title, "rawName": unit, "agent": cfg.app_name, "modelId": None,
            "when": when(nxt) if nxt else "", "repeat": "一次", "enabled": bool(nxt), "toggleable": False,
            "nextAt": int(nxt.timestamp() * 1000) if nxt else 0, "last": None}


@router.get("/api/upcoming")
async def upcoming():
    jobs, sys_timers = await asyncio.gather(cron_jobs(), timers())
    items = jobs + sys_timers
    items.sort(key=lambda x: (not x["enabled"], x["nextAt"] or 1e18))
    return {"ok": True, "upcoming": items}


class Toggle(BaseModel):
    enabled: bool


@router.post("/api/upcoming/{job_id}")
async def toggle_job(job_id: str, body: Toggle):
    jobs = await cron_jobs()
    job = next((j for j in jobs if j["id"] == job_id), None)
    if not job:
        raise HTTPException(404, "只有 OpenClaw 的定时任务能在这里开关；系统定时器请在服务器上改")
    await gateway_call("cron.update", {"id": job_id, "patch": {"enabled": body.enabled}}, timeout=30)
    forget_cache("cron")
    log_activity(f"{'启用' if body.enabled else '停用'}了定时任务「{job['title']}」", "toggled")
    return {"ok": True}


# —— 等你点头：OpenClaw 审批队列 ——————————————————————————————————

@router.get("/api/approvals")
async def approvals():
    data = await cached("approvals", 5, lambda: openclaw_cli("approvals", "pending", timeout=20))
    out = []
    for a in data.get("approvals", []):
        req = a.get("request") or a
        command = req.get("command") or req.get("commandText") or req.get("summary") or a.get("title") or ""
        fields = [{"k": k, "v": str(v)} for k, v in (("命令", command), ("目录", req.get("cwd")), ("agent", req.get("agentId") or a.get("agentId")),
                                                     ("主机", req.get("host"))) if v]
        created = a.get("createdAtMs") or a.get("createdAt")
        out.append({"id": a.get("id"), "kind": a.get("kind") or "exec", "action": short(command or a.get("kind") or "一个待审批的动作", 80),
                    "detail": a.get("reason") or req.get("reason") or "", "fields": fields, "groupId": None,
                    "requestedAt": when(london(created)) if isinstance(created, (int, float)) else ""})
    return {"ok": True, "approvals": out}


class Decision(BaseModel):
    allow: bool


@router.post("/api/approvals/{aid}")
async def decide(aid: str, body: Decision):
    await openclaw_cli("approvals", "resolve", aid, "allow-once" if body.allow else "deny", timeout=20)
    forget_cache("approvals")
    log_activity(f"{'批准' if body.allow else '拒绝'}了一个待审批的动作", "approved" if body.allow else "denied")
    return {"ok": True}


# —— 任务：OpenClaw 子会话 ————————————————————————————————————————

TASK_STATUS = {"completed": "完成", "succeeded": "完成", "failed": "失败", "timed_out": "失败", "lost": "失败",
               "cancelled": "已取消", "canceled": "已取消", "running": "进行中", "queued": "进行中", "pending": "进行中"}
_task_detail: dict[str, dict] = {}  # 做完的子会话不会再变，详情缓存起来


def origin_of(key: str) -> str:
    if key == "agent:main:main":
        return "main"
    parts = (key or "").split(":")
    if len(parts) >= 4 and parts[2] == "grava":
        return ":".join(parts[3:])
    return key or "main"


def hm(ms: float | None) -> str:
    dt = london(ms)
    return dt.strftime("%H:%M") if dt else ""


async def task_rows() -> list[dict]:
    data = await cached("tasks", 10, lambda: gateway_call("tasks.list", {}, timeout=30))
    return [t for t in data.get("tasks", []) if t.get("kind") == "subagent"]


def task_summary(t: dict, detail: dict | None) -> dict:
    status = TASK_STATUS.get(t.get("status") or "", "进行中" if (t.get("execution") or {}).get("state") != "finished" else "完成")
    info = (detail or {}).get("info") or {}
    model = f"{info['modelProvider']}/{info['model']}" if info.get("model") and info.get("modelProvider") else None
    return {"id": t["id"], "title": t.get("title") or "子会话任务", "status": status, "origin": origin_of(t.get("ownerKey") or t.get("sessionKey") or ""),
            "sessionKey": t.get("childSessionKey") or "", "modelId": model, "createdAt": when(london(t.get("createdAt"))),
            "startedAt": hm(t.get("startedAt")), "finishedAt": hm(t.get("endedAt")), "summary": t.get("progressSummary") or t.get("terminalSummary") or "",
            "error": t.get("error"), "toolUseCount": t.get("toolUseCount") or 0, "lastTool": t.get("lastToolName"),
            "tokens": info.get("totalTokens") or 0, "costUsd": info.get("estimatedCostUsd"), "updatedAt": t.get("updatedAt") or 0}


def text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text")
    return ""


def brief_of(text: str) -> str:
    """子会话收到的第一条前面有一段 OpenClaw 自带的说明，只留 [Subagent Task] 之后的任务本身。"""
    m = re.search(r"\[Subagent Task\]\s*(.*)", text, re.S)
    return (m.group(1) if m else text).strip()


async def task_detail(t: dict) -> dict:
    key = t.get("childSessionKey")
    done = (t.get("execution") or {}).get("state") == "finished"
    if key in _task_detail and done:
        return _task_detail[key]
    hist = await gateway_call("chat.history", {"sessionKey": key, "limit": 300}, timeout=30) if key else {}
    info = hist.get("sessionInfo") or {}
    runs: list[dict] = []
    for m in hist.get("messages", []):
        ts = hm(m.get("timestamp"))
        role = m.get("role")
        if role == "user":
            text = text_of(m.get("content"))
            runs.append({"version": len(runs) + 1, "note": None if not runs else short(text, 300), "brief": brief_of(text) if not runs else None,
                         "startedAt": ts, "finishedAt": None, "tokens": 0, "status": "running", "steps": [{"time": ts, "kind": "start", "text": "收到任务" if len(runs) == 0 else "收到修改意见"}], "result": None})
            continue
        if not runs:
            continue
        run = runs[-1]
        if role == "assistant":
            run["tokens"] += ((m.get("usage") or {}).get("totalTokens") or 0)
            for c in m.get("content") or []:
                if not isinstance(c, dict):
                    continue
                if c.get("type") == "thinking":
                    run["steps"].append({"time": ts, "kind": "think", "text": short(c.get("thinking") or "思考", 140)})
                elif c.get("type") in ("toolCall", "tool_use"):
                    args = c.get("arguments") or c.get("input") or {}
                    label = args.get("title") or args.get("description") or args.get("command") or args.get("path") or args.get("query") or ""
                    run["steps"].append({"time": ts, "kind": "tool", "text": short(f"{c.get('name')}：{label}" if label else c.get("name") or "工具", 140)})
                elif c.get("type") == "text" and c.get("text", "").strip():
                    run["result"] = {"summary": c["text"].strip()}
            if m.get("stopReason") in ("stop", "end_turn"):
                run["status"], run["finishedAt"] = "done", ts
                run["steps"].append({"time": ts, "kind": "done", "text": "做完，交回派发者"})
        elif role == "toolResult" and m.get("isError"):
            run["steps"].append({"time": ts, "kind": "check", "text": short(f"{m.get('toolName')} 出错：{text_of(m.get('content'))}", 140)})
    if runs and t.get("status") == "failed" and runs[-1]["status"] == "running":
        runs[-1]["status"] = "failed"
        runs[-1]["steps"].append({"time": hm(t.get("endedAt")), "kind": "check", "text": short(t.get("error") or "失败", 140)})
    out = {"info": info, "runs": runs}
    if key and done:
        _task_detail[key] = out
    return out


@router.get("/api/tasks")
async def tasks():
    rows = await task_rows()
    rows.sort(key=lambda t: t.get("createdAt") or 0, reverse=True)
    rows = rows[:30]
    details = await asyncio.gather(*(task_detail(t) for t in rows), return_exceptions=True)
    return {"ok": True, "tasks": [task_summary(t, d if isinstance(d, dict) else None) for t, d in zip(rows, details)]}


@router.get("/api/tasks/{tid}")
async def task(tid: str):
    t = next((x for x in await task_rows() if x["id"] == tid), None)
    if not t:
        raise HTTPException(404, "找不到这个任务")
    d = await task_detail(t)
    first = next((r for r in d["runs"] if r.get("brief")), None)
    return {"ok": True, "task": task_summary(t, d) | {"brief": first["brief"] if first else "", "runs": d["runs"]}}


@router.post("/api/tasks/{tid}/cancel")
async def cancel_task(tid: str):
    t = next((x for x in await task_rows() if x["id"] == tid), None)
    if not t:
        raise HTTPException(404, "找不到这个任务")
    await gateway_call("tasks.cancel", {"taskId": tid}, timeout=30)
    forget_cache("tasks")
    log_activity(f"取消了任务「{t.get('title')}」", "denied")
    return {"ok": True}


class Revise(BaseModel):
    note: str


@router.post("/api/tasks/{tid}/revise")
async def revise_task(tid: str, body: Revise):
    """修改意见发给同一个子会话：它记得前面做了什么。回复存在 app 的 task:<id> 线程里，过程看子会话记录。"""
    t = next((x for x in await task_rows() if x["id"] == tid), None)
    if not t or not t.get("childSessionKey"):
        raise HTTPException(404, "找不到这个任务的子会话")
    d = await task_detail(t)
    info = d.get("info") or {}
    model = f"{info['modelProvider']}/{info['model']}" if info.get("model") and info.get("modelProvider") else None
    start_run(f"task:{tid}", body.note.strip(), model, key=t["childSessionKey"])
    _task_detail.pop(t["childSessionKey"], None)
    forget_cache("tasks")
    log_activity(f"给任务「{t.get('title')}」发了修改意见", "edit")
    return {"ok": True}


# —— 活动记录 ————————————————————————————————————————————————————

def agent_label(agent_id: str | None) -> str:
    """活动记录里显示的名字：main 是助手本名，其余是「助手 · Agent 名」。"""
    if not agent_id or agent_id == "main":
        return cfg.app_name
    with _lock, ddb() as conn:
        r = conn.execute("SELECT name FROM groups WHERE id=?", (agent_id,)).fetchone()
    return f"{cfg.app_name} · {r['name']}" if r else agent_id


@router.get("/api/activity")
async def activity(limit: int = 80):
    audit, task_list = await asyncio.gather(
        cached("audit", 15, lambda: gateway_call("audit.activity.list", {"limit": 200}, timeout=30)),
        cached("tasks", 10, lambda: gateway_call("tasks.list", {}, timeout=30)), return_exceptions=True)
    names = thread_names()
    items: list[tuple[float, dict]] = []
    # 1) app 自己的动作
    with _lock, ddb() as conn:
        for r in conn.execute("SELECT * FROM activity_log ORDER BY id DESC LIMIT ?", (limit,)):
            dt = datetime.fromisoformat(r["ts"])
            items.append((dt.timestamp(), {"id": f"a{r['id']}", "time": when(dt), "actor": r["actor"], "text": r["text"], "kind": r["kind"]}))
    # 2) Gateway 审计：一次回复一行，带上用了哪些工具（审计只有元数据，没有内容）
    if isinstance(audit, dict):
        tools: dict[str, dict[str, int]] = {}
        for e in audit.get("events", []):
            if e.get("action") == "tool.action.finished" and e.get("runId"):
                tools.setdefault(e["runId"], {})
                tools[e["runId"]][e.get("toolName") or "工具"] = tools[e["runId"]].get(e.get("toolName") or "工具", 0) + 1
        seen: set[str] = set()
        for e in audit.get("events", []):
            if e.get("action") != "agent.run.finished" or (e.get("runId") or e["eventId"]) in seen:
                continue
            seen.add(e.get("runId") or e["eventId"])
            where = surface(e.get("sessionKey") or "", names)
            used = tools.get(e.get("runId") or "", {})
            tool_text = f"，用了工具 {'、'.join(f'{k} ×{v}' if v > 1 else k for k, v in used.items())}" if used else ""
            ok = e.get("status") == "succeeded"
            who = agent_label(e.get("agentId"))
            items.append((e["occurredAt"] / 1000, {"id": e["eventId"], "time": when(london(e["occurredAt"])), "actor": f"{who} · {where}" if where else who,
                                                   "text": f"{'回复了一次' if ok else '一次回复失败了'}{tool_text}", "kind": "reply" if ok else "failed"}))
    # 3) 定时任务的运行结果
    if isinstance(task_list, dict):
        for t in task_list.get("tasks", []):
            if t.get("kind") != "automation_run" or not t.get("endedAt"):
                continue
            title = CRON_TITLES.get(slug(t.get("title") or "")) or t.get("title") or "定时任务"
            ok = t.get("status") == "completed"
            items.append((t["endedAt"] / 1000, {"id": t["id"], "time": when(london(t["endedAt"])), "actor": "定时任务",
                                                "text": f"「{title}」{'跑完了' if ok else '没跑成：' + short(t.get('error') or '失败', 60)}", "kind": "cron" if ok else "failed"}))
    items.sort(key=lambda x: x[0], reverse=True)
    return {"ok": True, "activity": [x for _, x in items[:limit]]}


# —— 基础档案（L0） ——————————————————————————————————————————————

TAIL = re.compile(r"\s*((?:\[[A-Za-z]+\])+)\s*(\d{4}-\d{2}(?:-\d{2})?)?\s*$")


def item_id(section: str, line: str) -> str:
    return hashlib.sha1(f"{section}\n{line}".encode()).hexdigest()[:12]


def parse_bullets(path: Path) -> tuple[list[str], list[dict]]:
    """把 Markdown 拆成「小节 → 顶层要点」。要点下面缩进的子行算在同一条里。文件不存在就是空。"""
    try:
        lines = path.read_text(encoding="utf8").splitlines()
    except OSError:
        return [], []
    items, section, cur = [], "", None
    for i, line in enumerate(lines):
        if line.startswith("## "):
            section, cur = line[3:].strip(), None
        elif line.startswith("- ") and section:
            cur = {"section": section, "start": i, "end": i, "raw": line}
            items.append(cur)
        elif cur and line.startswith(("  ", "\t")) and line.strip():
            cur["end"] = i
            cur["raw"] += "\n" + line
        else:
            cur = None
    for it in items:
        it["id"] = item_id(it["section"], it["raw"])
    return lines, items


def split_tags(raw: str) -> tuple[str, list[str], str | None]:
    first = raw.split("\n")[0][2:]
    m = TAIL.search(first)
    body = first[: m.start()] if m else first
    tags = re.findall(r"\[([A-Za-z]+)\]", m.group(1)) if m else []
    sub = [ln.strip()[2:] if ln.strip().startswith("- ") else ln.strip() for ln in raw.split("\n")[1:]]
    return (body.strip() + ("\n" + "\n".join(sub) if sub else "")), tags, (m.group(2) if m else None)


SOURCE = {"G": "Gemini 导出", "C": "Claude 导出", "P": "ChatGPT 导出", "Gr": "对话", "L": "你确认过"}


@router.get("/api/profile")
def profile():
    _, items = parse_bullets(PROFILE)
    out = []
    for it in items:
        text, tags, dt = split_tags(it["raw"])
        out.append({"id": it["id"], "section": it["section"], "text": text, "sources": [SOURCE.get(t, t) for t in tags], "date": dt})
    return {"ok": True, "file": str(PROFILE).replace(str(Path.home()), "~"), "exists": PROFILE.is_file(), "items": out}


class ProfileEdit(BaseModel):
    text: str | None = None  # None = 删掉这一条


def rewrite(path: Path, lines: list[str], start: int, end: int, new: list[str]) -> None:
    out = lines[:start] + new + lines[end + 1:]
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("\n".join(out) + "\n", encoding="utf8")
    tmp.replace(path)


@router.put("/api/profile/{iid}")
def edit_profile(iid: str, body: ProfileEdit):
    """改一条：写成用户当天确认的版本（[L] 日期）；旧的一版存到 profile-history.md（不在检索路径里）。"""
    with _lock:
        lines, items = parse_bullets(PROFILE)
        it = next((x for x in items if x["id"] == iid), None)
        if not it:
            raise HTTPException(409, "这一条已经变了，刷新再改")
        today = date.today().isoformat()
        new = [] if body.text is None else [f"- {re.sub(r'\s+', ' ', body.text).strip()} [L] {today}"]
        rewrite(PROFILE, lines, it["start"], it["end"], new)
        PROFILE_HISTORY.parent.mkdir(parents=True, exist_ok=True)
        with PROFILE_HISTORY.open("a", encoding="utf8") as f:
            f.write(f"\n## {now_iso()} · {'删除' if body.text is None else '改写'} · {it['section']}\n{it['raw']}\n")
    log_activity(f"{'删了' if body.text is None else '改了'}档案「{it['section']}」里的一条", "edit")
    return {"ok": True}


# —— 记忆（L1） ——————————————————————————————————————————————————

@router.get("/api/memories")
def memories():
    """所有 agent 的长期记忆：main + 各 Group。id 带 scope 前缀，忘记时按前缀找文件。"""
    out, files = [], {}
    for scope, path in cfg.memory_files.items():
        if not path.exists():
            continue
        _, items = parse_bullets(path)
        files[scope] = str(path).replace(str(HOME) + "/", "")
        out += [{"id": f"{scope}:{it['id']}", "scope": scope, "section": it["section"], "text": re.sub(r"\*\*", "", split_tags(it["raw"])[0])} for it in items]
    try:
        learned = datetime.fromtimestamp(MEMORY.stat().st_mtime, TZ)
    except OSError:
        learned = None
    return {"ok": True, "file": files.get("main", ""), "files": files, "updated": when(learned, False), "items": out}


@router.delete("/api/memories/{iid}")
def forget(iid: str):
    scope, _, raw = iid.partition(":")
    path = cfg.memory_files.get(scope)
    if not path or not raw or not path.exists():
        raise HTTPException(404, "没有这条记忆")
    with _lock:
        lines, items = parse_bullets(path)
        it = next((x for x in items if x["id"] == raw), None)
        if not it:
            raise HTTPException(409, "这一条已经变了，刷新再试")
        rewrite(path, lines, it["start"], it["end"], [])
    log_activity(f"遗忘了 1 条长期记忆（{scope}）", "forgot")  # 按规范不留内容
    return {"ok": True}


# —— 模型与计费 ——————————————————————————————————————————————————

@router.get("/api/models")
async def models():
    c = config()
    d = (c.get("agents") or {}).get("defaults") or {}
    try:
        auth = await cached("auth", 300, lambda: gateway_call("models.authStatus", {}, timeout=30))
    except HTTPException:
        auth = {}
    providers = []
    for p in auth.get("providers", []):
        profs = p.get("profiles") or []
        profs = profs if isinstance(profs, list) else []
        sub = next((x for x in profs if x.get("type") not in ("api_key", None) or x.get("expiry")), None)
        providers.append({"provider": p.get("provider"), "name": p.get("displayName") or p.get("provider"), "status": p.get("status"),
                          "subscription": bool(sub), "expires": (p.get("expiry") or {}).get("label")})
    return {"ok": True, "primary": (d.get("model") or {}).get("primary"), "fallbacks": (d.get("model") or {}).get("fallbacks") or [],
            "subagent": (d.get("subagents") or {}).get("model"), "allowed": ((d.get("modelPolicy") or {}).get("allow") or []), "providers": providers}


# —— 安全 ————————————————————————————————————————————————————————

def channel_fact(name: str, c: dict) -> dict:
    """私聊要白名单或配对，群聊不能是 open（没有白名单），两样都满足才算过。"""
    dm, group = c.get("dmPolicy"), c.get("groupPolicy")
    ok = dm in ("allowlist", "pairing") and group != "open"
    note = "" if ok else "群聊策略是 open，没有白名单。" if group == "open" else "私聊没有限制。"
    return {"title": name, "sub": f"私聊 {dm}，群聊 {group}。{note}", "state": "已满足" if ok else "注意", "tone": "good" if ok else "warn"}


@router.get("/api/security")
async def security():
    c = config()
    gw = c.get("gateway") or {}
    ch = c.get("channels") or {}
    tokens, nodes = cfg.tokens(), cfg.tailscale_nodes()
    try:
        policy = await cached("policy", 300, lambda: openclaw_cli("approvals", "get", timeout=20))
        scope = next((s for s in (policy.get("effectivePolicy") or {}).get("scopes", []) if s.get("agentId") == "main"), {})
        sec = (scope.get("security") or {}).get("effective")
        ask = (scope.get("ask") or {}).get("effective")
    except HTTPException:
        sec = ask = None
    pending = len((await approvals())["approvals"])
    d = (c.get("agents") or {}).get("defaults") or {}
    facts = [
        {"title": "Gateway 只听本机", "sub": f"绑定 {gw.get('bind')}，端口 {gw.get('port')}，公网连不上。", "state": "已满足" if gw.get("bind") == "loopback" else "注意", "tone": "good" if gw.get("bind") == "loopback" else "warn"},
        {"title": "app 接口认证", "sub": f"接入令牌 {len(tokens)} 个（{', '.join(tokens) or '无'}）；Tailscale 免令牌设备：{', '.join(sorted(nodes)) or '无'}；监听 {cfg.host}:{cfg.port}。", "state": "已满足" if (tokens or nodes) else "无认证", "tone": "good" if (tokens or nodes) else "warn"},
        *[channel_fact(name, ch.get(key) or {}) for key, name in (("telegram", "Telegram"), ("discord", "Discord")) if (ch.get(key) or {}).get("enabled")],
        {"title": "执行命令的权限", "sub": f"安全级别 {sec}，询问 {ask}：{cfg.app_name} 现在跑命令不需要你批准。安全底座（第 9 步）会改成要审批。", "state": "未设防" if ask == "off" else "有审批", "tone": "warn" if ask == "off" else "good"},
        {"title": "沙箱", "sub": f"代办任务还没放进隔离环境，和 {cfg.app_name} 同一台机器、同一个用户。" if not d.get("sandbox") else "已配置。", "state": "未启用" if not d.get("sandbox") else "已启用", "tone": "warn" if not d.get("sandbox") else "good"},
        {"title": "密钥存放", "sub": "密钥同时明文存在 openclaw.json 的 env 段和 .env 里，应收敛到一处。" if c.get("env") else "只在 .env。", "state": "待收敛" if c.get("env") else "已满足", "tone": "warn" if c.get("env") else "good"},
        {"title": "待审批", "sub": f"审批队列里现在有 {pending} 个动作等你决定。", "state": str(pending), "tone": "neutral"},
    ]
    plan = [
        {"title": "隔离执行环境", "sub": "浏览器、填表等代办任务在沙箱里跑，碰不到服务器上的密钥和文件。"},
        {"title": "Sentinel 出网审批", "sub": "沙箱的出网请求先过一个独立模型；白名单外的转成审批卡。"},
        {"title": "凭证代位", "sub": "沙箱里只有占位 token，真实凭证在出口处才注入。"},
    ]
    rules = [["后台执行", "免审"], ["浏览器只读", "免审"], ["发邮件", "每次审批"], ["登录后操作", "每次审批"], ["填表", "提交前审批"], ["订行程", "下单前审批"], ["付款", "每笔审批 + 限额虚拟卡"]]
    return {"ok": True, "facts": facts, "plan": plan, "rules": rules}


# —— 设置 ————————————————————————————————————————————————————————

@router.get("/api/settings")
def settings():
    with _lock, ddb() as conn:
        r = conn.execute("SELECT value FROM settings WHERE key='avatar'").fetchone()
    return {"ok": True, "avatar": json.loads(r["value"]) if r else None}


class AvatarIn(BaseModel):
    style: str
    ring: str
    stream: str


@router.put("/api/settings/avatar")
def set_avatar(body: AvatarIn):
    with _lock, ddb() as conn:
        conn.execute("INSERT INTO settings(key, value) VALUES('avatar', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (body.model_dump_json(),))
    return {"ok": True}

