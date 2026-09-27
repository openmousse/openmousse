"""目标（2026-09-27）：你和各个 Agent 都能改的目标，外加体重、体脂的读数和趋势。

表（grava.db）
- goals（data.py 建的，本模块补列）：一行一个目标。category 存 健康 / 学业 / 职业 / 财务（这四个中文词就是值，app 按语言显示）；
  due 是 YYYY-MM-DD，也收 YYYY-MM 和「2027 秋」这种说法；数字目标 target_low / target_high / unit（可以只给一头）；
  metric = bodyfat / weight 时服务端自动读当前值，空 = 不自动读；group_id = 挂在哪个 Agent 下（app 里「去看板」）；
  status active / done / dropped（「不做了」= dropped，没有删除）。补的列：added_by 谁加的（leo / main / Agent id）、updated_at、
  closed_at（完成或不做了的时间）、deleted_at（只有撤销「加了一个目标」时才写，这样的行哪里都不显示）。
- goal_log：每次改动一行（谁、哪个目标、做了什么、之前 / 之后的快照），同时写一行活动记录。摘要读的时候按请求的语言现拼。
  撤销只把这次改动动过、之后没人再改过的字段改回去；撤销「加了」= 藏起来（deleted_at）。
  Agent 24 小时内的改动、还没点「知道了」（seen_at）的，在目标页顶上一条，能撤销。

读数（metric）
- 训记（body 数据源）是真源：近 400 天的记录 + 每种最新一条，一次查询给 /api/goals 和趋势共用（训记同一个查询 15 秒只让打一次，
  所以串行、有缓存）。Apple 健康（health_metrics 按天的均值）是对照。当前值 = 两边最新的那次，同一天以训记为准。
  体脂从不自动算（没有公式），只读记下的。
- 进度：起点（设目标那天或之前最近的一次读数；那之前没有就用之后的第一次）→ 目标区间，进了区间 = 100%。往下走（体脂、体重）
  往上走都行；一开始就在区间里的是「保持」。最新读数超过 30 天算 stale（app 灰掉，提醒去量一次）。

接口
  GET   /api/goals                  goals（进行中）+ closed（完成 / 不做了）+ recent（Agent 最近的改动）+ metrics（能自动读的）
  POST  /api/goals                  加一个 {title, category, detail?, due?, unit?, targetLow?, targetHigh?, metric?, groupId?, position?, source?}
  PATCH /api/goals/{id}             只带要改的字段（null = 清掉），外加 status / position；source = 谁在改（默认 leo）
  POST  /api/goals/undo/{log}       撤销一次改动（{redo: true} 再做回来）
  GET   /api/goals/log?limit=       最近的改动，新的在前
  POST  /api/goals/seen {ids}       目标页顶上那条点了「知道了」
  GET   /api/goals/trend?metric=weight&days=180[&fresh=1]   两个来源的点 + 摘要（最新、近 7 天平均、30 天变化）
Agent 用 goals_ctl.py（走这些接口，--source 写自己的 id）。
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
import threading
import uuid
from datetime import date, datetime, timedelta

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import schedule
import sources
from chat import _lock, log_activity, now_iso
from config import TZ
from data import ddb
from i18n import L

router = APIRouter()

CATEGORIES = ("健康", "学业", "职业", "财务")
CATEGORY_EN = {"健康": "Health", "学业": "Study", "职业": "Career", "财务": "Finance"}
CATEGORY_ALIAS = {"health": "健康", "body": "健康", "身体": "健康", "study": "学业", "school": "学业", "学习": "学业",
                  "career": "职业", "work": "职业", "job": "职业", "工作": "职业", "finance": "财务", "money": "财务", "钱": "财务"}
STATUSES = ("active", "done", "dropped")
# 能自动读当前值的指标：训记里的类型、Apple 健康里的指标名、单位；cross = 两边同一天差多少算对不上
METRICS = {
    "bodyfat": {"zh": "体脂", "en": "body fat", "unit": "%", "xunji": "bodyfat", "health": "BodyFatPercentage", "cross": 1.0},
    "weight": {"zh": "体重", "en": "weight", "unit": "kg", "xunji": "weight", "health": "BodyMass", "cross": 0.3},
}
TITLE_MAX, DETAIL_MAX, DUE_MAX, UNIT_MAX = 80, 500, 24, 12
BODY_DAYS = 400             # 训记、Apple 健康往前读多远（找起点要往前看）
BODY_TTL, FRESH_TTL = 1800, 90  # 训记缓存：平时 30 分钟；下拉刷新（fresh=1）超过 90 秒就重读
STALE_DAYS = 30
RECENT_HOURS = 24
EDIT_COLS = ("title", "category", "detail", "due", "unit", "target_low", "target_high", "metric", "group_id", "status", "position")
SNAP_COLS = EDIT_COLS + ("closed_at",)
EXTRA_COLS = {"added_by": "TEXT", "updated_at": "TEXT", "closed_at": "TEXT", "deleted_at": "TEXT"}
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")
LB = 0.45359237

_ready = False
_xj_lock = threading.Lock()  # 两个请求同时要训记：后一个等前一个读完直接用缓存，不去撞 15 秒的限频


def gdb() -> sqlite3.Connection:
    """goals 表（data.ddb 建）补上本模块的列，再加 goal_log。只查一次。"""
    global _ready
    conn = ddb()
    if not _ready:
        have = {r[1] for r in conn.execute("PRAGMA table_info(goals)")}
        for col, typ in EXTRA_COLS.items():
            if col not in have:
                conn.execute(f"ALTER TABLE goals ADD COLUMN {col} {typ}")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS goal_log (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, actor TEXT NOT NULL,
                goal TEXT NOT NULL, action TEXT NOT NULL, before TEXT, after TEXT, undone_at TEXT, seen_at TEXT);
            CREATE INDEX IF NOT EXISTS goal_log_goal ON goal_log(goal, id);
        """)
        _ready = True
    return conn


def today() -> date:
    return datetime.now(TZ).date()


def num(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


# —— 显示 ————————————————————————————————————————————————————————————

def metric_label(m: str | None) -> str:
    spec = METRICS.get(m or "")
    return L(spec["zh"], spec["en"]) if spec else ""


def category_label(c: str | None) -> str:
    return L(c or "", CATEGORY_EN.get(c or "", c or ""))


def fmt_num(v: float) -> str:
    """15 / 79.83 / 50000（最多两位小数，不用科学记数法）。"""
    x = round(float(v), 2)
    return str(int(x)) if x == int(x) else f"{x:.2f}".rstrip("0").rstrip(".")


def unit_suffix(unit: str | None) -> str:
    u = (unit or "").strip()
    return u if not u or u in ("%", "‰", "°") else f" {u}"


def fmt_target(low: float | None, high: float | None, unit: str | None) -> str:
    """15–18% / 72–75 kg / 18% 以下 / 5000 GBP 以上；都没有是空字符串。"""
    u = unit_suffix(unit)
    if low is not None and high is not None:
        return f"{fmt_num(low)}{u}" if low == high else f"{fmt_num(low)}–{fmt_num(high)}{u}"
    if high is not None:
        return L(f"{fmt_num(high)}{u} 以下", f"{fmt_num(high)}{u} or less")
    if low is not None:
        return L(f"{fmt_num(low)}{u} 以上", f"{fmt_num(low)}{u} or more")
    return ""


def who(actor: str, names: dict[str, str]) -> str:
    """谁改的，显示用：你自己改的是空的；主对话是助手的名字；Agent 是它的名字。"""
    return schedule.who_name(actor, names)


def field_words() -> list[tuple[tuple[str, ...], str]]:
    """一次改了好几处时，摘要里列出改了哪几样（按请求的语言，所以每次现取）。"""
    return [(("title",), L("名字", "name")), (("target_low", "target_high", "unit"), L("目标数字", "target")),
            (("due",), L("截止", "due date")), (("metric",), L("自动读数", "automatic reading")),
            (("group_id",), L("Agent", "agent")), (("category",), L("分类", "category")),
            (("detail",), L("说明", "note")), (("position",), L("顺序", "order"))]


def describe(before: dict | None, after: dict | None, names: dict[str, str]) -> tuple[str, str]:
    """(action, 一句话摘要)：加了 / 完成 / 不做了 / 放回来 / 改了什么。按请求的语言拼。"""
    a = after or before or {}
    title = a.get("title") or ""
    q = L(f"「{title}」", f'"{title}"')
    if before is None:
        return "add", L(f"加了目标{q}", f"Added the goal {q}")
    after = after or {}
    changed = [c for c in EDIT_COLS if before.get(c) != after.get(c)]
    if "status" in changed:
        s = after.get("status")
        if s == "done":
            return "done", L(f"把{q}标成完成", f"Marked {q} done")
        if s == "dropped":
            return "drop", L(f"{q}不做了", f"Dropped {q}")
        return "reopen", L(f"把{q}放回进行中", f"Put {q} back in progress")
    what = metric_label(a.get("metric"))
    phrases: list[str] = []
    if {"target_low", "target_high", "unit"} & set(changed):
        rng = fmt_target(after.get("target_low"), after.get("target_high"), after.get("unit"))
        if not rng:
            phrases.append(L(f"去掉了{q}的数字目标", f"Removed the number from {q}"))
        elif what:
            phrases.append(L(f"把{what}目标改成 {rng}", f"Changed the {what} target to {rng}"))
        else:
            phrases.append(L(f"把{q}的目标改成 {rng}", f"Changed the target of {q} to {rng}"))
    if "title" in changed:
        phrases.append(L(f"把「{before.get('title')}」改名为「{after.get('title')}」", f'Renamed "{before.get("title")}" to "{after.get("title")}"'))
    if "due" in changed:
        phrases.append(L(f"把{q}的截止改成 {after['due']}", f"Moved the due date of {q} to {after['due']}") if after.get("due")
                       else L(f"去掉了{q}的截止", f"Removed the due date from {q}"))
    if "metric" in changed:
        m = metric_label(after.get("metric"))
        phrases.append(L(f"{q}改成自动读{m}", f"{q} now reads {m} automatically") if m else L(f"{q}不再自动读数", f"{q} no longer reads a number automatically"))
    if "group_id" in changed:
        g = names.get(after.get("group_id") or "", after.get("group_id") or "")
        phrases.append(L(f"把{q}交给{g}", f"Linked {q} to {g}") if g else L(f"{q}不挂 Agent 了", f"Unlinked {q} from its agent"))
    if "category" in changed:
        phrases.append(L(f"把{q}挪到{category_label(after.get('category'))}", f"Moved {q} to {category_label(after.get('category'))}"))
    if "detail" in changed:
        phrases.append(L(f"改了{q}的说明", f"Edited the note on {q}"))
    if "position" in changed:
        phrases.append(L(f"调了{q}的顺序", f"Reordered {q}"))
    if len(phrases) == 1:
        return "edit", phrases[0]
    # 一次改了好几处：一句话说改了哪几样，细节在目标里看
    words = [w for cols, w in field_words() if set(cols) & set(changed)]
    if not words:
        return "edit", L(f"改了{q}", f"Edited {q}")
    head = "、".join(words[:-1])
    listed = words[0] if len(words) == 1 else L(head + (" 和" if head[-1:].isascii() else "和") + words[-1], ", ".join(words[:-1]) + " and " + words[-1])
    return "edit", L(f"改了{q}的{' ' if listed[:1].isascii() else ''}{listed}", f"Changed the {listed} of {q}")


# —— 读数：训记（主）+ Apple 健康（对照） ————————————————————————————————————

def xunji_body(fresh: bool = False) -> tuple[dict[str, dict[str, float]], str | None]:
    """训记里的体重、体脂：{类型: {日期: 值}}（近 BODY_DAYS 天的记录 + 每种最新一条，latest 不受日期范围限制）和出错原因。
    没接训记 = ({}, None)。"""
    if not sources.AVAILABLE.get("body") or sources.xunji is None:
        return {}, None
    end = today()
    body = {"include_latest": True, "include_records": True, "start_date": (end - timedelta(days=BODY_DAYS)).isoformat(),
            "end_date": end.isoformat(), "types": sorted({m["xunji"] for m in METRICS.values()}), "limit": 500, "offset": 0}
    try:
        with _xj_lock:
            data = sources.xunji.call("body_query", body, ttl=FRESH_TTL if fresh else BODY_TTL)
    except Exception as e:  # noqa: BLE001 — 网络、Key、限频：这一边这次没有读数，别的照常
        return {}, (str(e) or type(e).__name__)[:200]
    res = data.get("res") or {}
    rows = [r for r in res.get("records") or [] if isinstance(r, dict)]
    rows += [{**r, "type": r.get("type") or k} for k, r in (res.get("latest") or {}).items() if isinstance(r, dict)]
    out: dict[str, dict[str, float]] = {}
    for r in rows:  # 记录按日期新到旧；同一天有两条的留第一条（训记按 日期 + 类型 覆盖写，一般不会有）
        d, v = str(r.get("datestr") or "")[:10], num(r.get("value"))
        if r.get("type") and DATE_RE.match(d) and v is not None:
            out.setdefault(str(r["type"]), {}).setdefault(d, round(v, 2))
    return out, None


def health_points(metric: str) -> dict[str, float]:
    """Apple 健康按天的均值 {日期: 值}：近 BODY_DAYS 天 + 最新一天（再早也算上，当前值要看）。体脂存的是小数，换成 %；磅换成 kg。"""
    key = METRICS[metric]["health"]
    since = (today() - timedelta(days=BODY_DAYS)).isoformat()
    try:
        with _lock, gdb() as conn:
            rows = conn.execute("SELECT date, avg, unit FROM health_metrics WHERE metric=? AND avg IS NOT NULL AND date>=?", (key, since)).fetchall()
            rows += conn.execute("SELECT date, avg, unit FROM health_metrics WHERE metric=? AND avg IS NOT NULL ORDER BY date DESC LIMIT 1", (key,)).fetchall()
    except sqlite3.Error:  # health_metrics 还没建（手机从没同步过）
        return {}
    out: dict[str, float] = {}
    for r in rows:
        v = num(r["avg"])
        if v is None or not DATE_RE.match(r["date"] or ""):
            continue
        if metric == "bodyfat" and v <= 1:
            v *= 100
        if metric == "weight" and (r["unit"] or "").lower() in ("lb", "lbs"):
            v *= LB
        out[r["date"]] = round(v, 2)
    return out


def source_name(key: str) -> str:
    return sources.xunji_name() if key == "body" else L("Apple 健康", "Apple Health")


class Reads:
    """一次请求里的读数：训记只查一次，按指标拆开。"""

    def __init__(self, fresh: bool = False) -> None:
        self.fresh = fresh
        self.error: str | None = None
        self._xj: dict[str, dict[str, float]] | None = None

    def xunji(self) -> dict[str, dict[str, float]]:
        if self._xj is None:
            self._xj, self.error = xunji_body(self.fresh)
        return self._xj

    def points(self, metric: str) -> list[dict]:
        """两个来源的全部读数 [{date, value, source: body / health}]，按日期旧到新，同一天训记在前。"""
        xj = self.xunji().get(METRICS[metric]["xunji"], {})
        pts = [{"date": d, "value": v, "source": "body"} for d, v in xj.items()]
        pts += [{"date": d, "value": v, "source": "health"} for d, v in health_points(metric).items()]
        return sorted(pts, key=lambda p: (p["date"], p["source"] != "body"))


def merged(pts: list[dict]) -> list[dict]:
    """一天一个读数（训记优先），旧到新。"""
    by: dict[str, dict] = {}
    for p in pts:
        if p["date"] not in by or p["source"] == "body":
            by[p["date"]] = p
    return [by[d] for d in sorted(by)]


def baseline(pts: list[dict], day: str) -> dict | None:
    """起点：那天或之前最近的一次；那之前没有就用之后的第一次。pts 旧到新。"""
    before = [p for p in pts if p["date"] <= day]
    return before[-1] if before else (pts[0] if pts else None)


def progress_of(start: float | None, cur: float | None, low: float | None, high: float | None) -> tuple[float | None, str | None, str | None]:
    """(进度 0–1, 方向 down / up / keep, 现在在哪 in / above / below)。进了区间 = 1；走过头了也是 1（state 说在哪一边）。"""
    if cur is None or (low is None and high is None):
        return None, None, None
    lo = low if low is not None else -math.inf
    hi = high if high is not None else math.inf
    state = "in" if lo <= cur <= hi else "above" if cur > hi else "below"
    ref = start if start is not None else cur
    direction = "down" if ref > hi else "up" if ref < lo else "keep"
    if state == "in":
        return 1.0, direction, state
    if direction == "down":
        if cur < lo:
            return 1.0, direction, state
        span = ref - hi
        return (round(max(0.0, min(1.0, (ref - cur) / span)), 3) if span > 0 else 0.0), direction, state
    if direction == "up":
        if cur > hi:
            return 1.0, direction, state
        span = lo - ref
        return (round(max(0.0, min(1.0, (cur - ref) / span)), 3) if span > 0 else 0.0), direction, state
    return 0.0, direction, state


def days_left(due: str | None) -> int | None:
    """截止还有几天：YYYY-MM-DD 按那天算，YYYY-MM 按那个月最后一天；别的写法（「2027 秋」）不算。"""
    s = (due or "").strip()
    try:
        if DATE_RE.match(s):
            d = date.fromisoformat(s)
        elif (m := MONTH_RE.match(s)):
            y, mo = int(m.group(1)), int(m.group(2))
            d = date(y + (mo == 12), mo % 12 + 1, 1) - timedelta(days=1)
        else:
            return None
    except ValueError:
        return None
    return (d - today()).days


# —— 一行 → JSON ——————————————————————————————————————————————————————

def snap(r: sqlite3.Row | dict) -> dict:
    return {c: r[c] for c in SNAP_COLS}


def goal_json(r: sqlite3.Row, reads: Reads | None) -> dict:
    """GET /api/goals 的一项。前 15 个字段和以前一样（老 app 只认这些）；reads 为 None 时不读当前值（写接口的返回、已关的目标）。"""
    g = {"id": r["id"], "category": r["category"], "title": r["title"], "detail": r["detail"] or "", "due": r["due"] or "",
         "groupId": r["group_id"], "source": r["source"] or "", "unit": r["unit"], "targetLow": r["target_low"], "targetHigh": r["target_high"],
         "current": None, "currentDate": None, "currentSource": None, "start": None, "stale": False,
         "status": r["status"], "metric": r["metric"], "position": r["position"], "addedBy": r["added_by"],
         "createdAt": r["created_at"], "updatedAt": r["updated_at"] or r["created_at"], "closedAt": r["closed_at"],
         "daysLeft": days_left(r["due"]), "startDate": None, "progress": None, "direction": None, "state": None}
    if reads is not None and r["metric"] in METRICS:
        pts = merged(reads.points(r["metric"]))
        if pts:
            cur = pts[-1]
            base = baseline(pts, (r["created_at"] or "")[:10])
            g.update(current=cur["value"], currentDate=cur["date"], currentSource=source_name(cur["source"]),
                     start=base["value"] if base else None, startDate=base["date"] if base else None,
                     stale=(today() - date.fromisoformat(cur["date"])).days > STALE_DAYS)
            p, d, s = progress_of(g["start"], cur["value"], r["target_low"], r["target_high"])
            g.update(progress=p, direction=d, state=s)
    return g


def change_json(r: sqlite3.Row, names: dict[str, str]) -> dict:
    """一次改动：谁、哪个目标、一句话摘要（按请求的语言现拼）、撤销了没有。"""
    before = json.loads(r["before"]) if r["before"] else None
    after = json.loads(r["after"]) if r["after"] else None
    _, summary = describe(before, after, names)
    return {"logId": r["id"], "at": r["at"], "actor": r["actor"], "actorName": who(r["actor"], names), "goal": r["goal"],
            "title": (after or before or {}).get("title") or "", "action": r["action"], "summary": summary,
            "status": "undone" if r["undone_at"] else "done", "seen": bool(r["seen_at"])}


def metric_title(m: str) -> str:
    """单独显示的名字（选择器、趋势卡的标题）：英文首字母大写。"""
    return L(METRICS[m]["zh"], METRICS[m]["en"].capitalize())


def metrics_json() -> list[dict]:
    return [{"key": k, "label": metric_title(k), "unit": v["unit"]} for k, v in METRICS.items()]


# —— 读 ——————————————————————————————————————————————————————————————

@router.get("/api/goals")
def goals(fresh: int = 0):
    """进行中的目标（带当前值和进度）+ 完成 / 不做了的 + Agent 24 小时内的改动（还没点「知道了」的）。"""
    since = (datetime.now(TZ) - timedelta(hours=RECENT_HOURS)).isoformat(timespec="seconds")
    with _lock, gdb() as conn:
        rows = conn.execute("SELECT * FROM goals WHERE deleted_at IS NULL ORDER BY position, created_at").fetchall()
        logs = conn.execute("SELECT * FROM goal_log WHERE actor<>'leo' AND at>=? AND seen_at IS NULL ORDER BY id DESC LIMIT 10", (since,)).fetchall()
        names = schedule.group_names(conn)
    reads = Reads(bool(fresh))
    active = [goal_json(r, reads) for r in rows if r["status"] == "active"]
    closed = [goal_json(r, None) for r in rows if r["status"] != "active"]
    closed.sort(key=lambda g: g["closedAt"] or g["updatedAt"] or "", reverse=True)
    return {"ok": True, "goals": active, "closed": closed, "recent": [change_json(r, names) for r in logs], "metrics": metrics_json(),
            "readError": reads.error}


@router.get("/api/goals/log")
def goal_log(limit: int = 30, goal: str | None = None):
    """最近的改动，新的在前（goal：只看这一个目标的）。"""
    q, args = "SELECT * FROM goal_log", []
    if goal:
        q += " WHERE goal=?"
        args.append(goal)
    with _lock, gdb() as conn:
        rows = conn.execute(q + " ORDER BY id DESC LIMIT ?", (*args, max(1, min(limit, 200)))).fetchall()
        names = schedule.group_names(conn)
    return {"ok": True, "changes": [change_json(r, names) for r in rows]}


def summarize(pts: list[dict], other: list[dict], metric: str, source: str) -> dict:
    """摘要：最新一次、近 7 天平均、30 天变化（和 30 天前那次比；那时候还没记，就和窗口里最早的一次比，since 写的是那天）。
    check：对照来源同一天的读数，差得多才给（体重 0.3 kg、体脂 1%）。pts 旧到新、一天一个。"""
    t = today()
    latest = pts[-1]
    week = [p["value"] for p in pts if p["date"] >= (t - timedelta(days=6)).isoformat()]
    cut = t - timedelta(days=30)
    old = [p for p in pts if (cut - timedelta(days=14)).isoformat() <= p["date"] <= cut.isoformat()]
    base = old[-1] if old else next((p for p in pts if p["date"] > cut.isoformat()), None)
    change = {"value": round(latest["value"] - base["value"], 2), "since": base["date"]} if base and base["date"] < latest["date"] else None
    same = next((p for p in other if p["date"] == latest["date"]), None)
    check = {"date": same["date"], "value": same["value"], "sourceName": source_name(same["source"])} \
        if same and abs(same["value"] - latest["value"]) >= METRICS[metric]["cross"] else None
    return {"source": source, "sourceName": source_name(source), "latest": {"date": latest["date"], "value": latest["value"]},
            "avg7": {"value": round(sum(week) / len(week), 2), "n": len(week)} if week else None, "change30": change, "check": check}


@router.get("/api/goals/trend")
def trend(metric: str = "weight", days: int = 180, fresh: int = 0):
    """一个指标近 days 天的读数：series 是两个来源各自的点（source = body 训记 / health Apple 健康），summary 按训记算，
    训记在这段时间里没有才用 Apple 健康。哪边都没有 = series 空、summary null（没接数据源不算错，看 sources）。"""
    m = check_metric(metric)
    if not m:
        raise HTTPException(400, L(f"metric 只能是 {' / '.join(METRICS)}", f"metric must be one of {' / '.join(METRICS)}"))
    days = max(7, min(days, BODY_DAYS))
    reads = Reads(bool(fresh))
    lo = (today() - timedelta(days=days)).isoformat()
    pts = [p for p in reads.points(m) if p["date"] >= lo]
    body = [p for p in pts if p["source"] == "body"]
    health = [p for p in pts if p["source"] == "health"]
    summary = summarize(body, health, m, "body") if body else summarize(health, [], m, "health") if health else None
    return {"ok": True, "metric": m, "label": metric_title(m), "unit": METRICS[m]["unit"], "days": days, "from": lo, "to": today().isoformat(),
            "series": pts, "summary": summary,
            "sources": [{"key": "body", "name": source_name("body"), "primary": True, "connected": bool(sources.AVAILABLE.get("body")), "error": reads.error},
                        {"key": "health", "name": source_name("health"), "primary": False, "connected": True, "error": None}]}


# —— 写：校验 ————————————————————————————————————————————————————————

def clean(v: str | None, limit: int, zh: str, en: str, required: bool = False, lines: bool = False) -> str | None:
    """去掉首尾空白、连续空白并成一个（lines：保留换行）。空 = None（required 就 400）；超长 400。"""
    s = (v or "").strip()
    s = "\n".join(re.sub(r"[ \t]+", " ", x).strip() for x in s.splitlines()).strip() if lines else re.sub(r"\s+", " ", s)
    if not s:
        if required:
            raise HTTPException(400, L(f"{zh}不能空", f"The {en} can't be empty"))
        return None
    if len(s) > limit:
        raise HTTPException(400, L(f"{zh}太长了（最多 {limit} 个字）", f"The {en} is too long (at most {limit} characters)"))
    return s


def check_category(v: str | None) -> str:
    c = (v or "").strip()
    c = CATEGORY_ALIAS.get(c.lower(), c)
    if c not in CATEGORIES:
        raise HTTPException(400, L(f"分类只能是 {' / '.join(CATEGORIES)}", "category must be one of 健康 / 学业 / 职业 / 财务 (or health / study / career / finance)"))
    return c


def check_due(v: str | None) -> str | None:
    s = clean(v, DUE_MAX, "截止", "due date")
    if s is None:
        return None
    ok = True
    if DATE_RE.match(s):
        try:
            date.fromisoformat(s)
        except ValueError:
            ok = False
    elif (m := MONTH_RE.match(s)):
        ok = 1 <= int(m.group(2)) <= 12
    if not ok:
        raise HTTPException(400, L(f"截止「{s}」不是一个真的日子（写成 2026-12-31、2026-12，或者「2027 秋」这样的说法）",
                                   f'The due date "{s}" isn\'t a real date (use 2026-12-31, 2026-12, or words like "fall 2027")'))
    return s


def check_metric(v: str | None) -> str | None:
    m = (v or "").strip().lower()
    if not m or m in ("none", "null", "-", "off"):
        return None
    if m not in METRICS:
        raise HTTPException(400, L(f"metric 只能是 {' / '.join(METRICS)}，或者不填（不自动读数）",
                                   f"metric must be one of {' / '.join(METRICS)}, or empty (no automatic reading)"))
    return m


def check_number(v: float | None, zh: str, en: str) -> float | None:
    if v is None:
        return None
    x = num(v)
    if x is None:
        raise HTTPException(400, L(f"{zh}要是一个数", f"The {en} must be a number"))
    return round(x, 3)


def check_group(conn: sqlite3.Connection, v: str | None) -> str | None:
    g = (v or "").strip()
    if not g:
        return None
    if not conn.execute("SELECT 1 FROM groups WHERE id=?", (g,)).fetchone():
        raise HTTPException(400, L(f"没有 id 是 {g} 的 Agent（用 agent_ctl.py list 看）", f"There's no agent with id {g} (see agent_ctl.py list)"))
    return g


def check_status(v: str) -> str:
    s = (v or "").strip().lower()
    if s not in STATUSES:
        raise HTTPException(400, L("status 只能是 active / done / dropped", "status must be active / done / dropped"))
    return s


def check_range(g: dict) -> None:
    if g["target_low"] is not None and g["target_high"] is not None and g["target_low"] > g["target_high"]:
        raise HTTPException(400, L("目标的下限比上限还大", "The low end of the target is above the high end"))


def goal_row(conn: sqlite3.Connection, gid: str) -> sqlite3.Row:
    r = conn.execute("SELECT * FROM goals WHERE id=?", (gid,)).fetchone()
    if r is None or r["deleted_at"]:
        raise HTTPException(404, L("没有这个目标", "No such goal"))
    return r


def write_goal(conn: sqlite3.Connection, gid: str, state: dict) -> None:
    sets = ", ".join(f"{c}=?" for c in state)
    conn.execute(f"UPDATE goals SET {sets}, updated_at=? WHERE id=?", (*state.values(), now_iso(), gid))  # noqa: S608 — 列名是常量


def log_change(conn: sqlite3.Connection, actor: str, gid: str, action: str, before: dict | None, after: dict | None) -> int:
    cur = conn.execute("INSERT INTO goal_log(at, actor, goal, action, before, after) VALUES(?,?,?,?,?,?)",
                       (now_iso(), actor, gid, action, json.dumps(before, ensure_ascii=False) if before is not None else None,
                        json.dumps(after, ensure_ascii=False) if after is not None else None))
    return int(cur.lastrowid or 0)


def announce(actor: str, summary: str, names: dict[str, str]) -> None:
    """活动记录一行。你自己改的记成「你」，Agent 改的写它的名字。"""
    log_activity(L(f"目标：{summary}", f"Goals: {summary}"), "edit", None if actor == "leo" else (who(actor, names) or actor))


# —— 写：接口 ————————————————————————————————————————————————————————

class GoalIn(BaseModel):
    title: str
    category: str | None = None     # 健康 / 学业 / 职业 / 财务（也认 health / study / career / finance）；有 metric 时默认健康
    detail: str | None = None
    due: str | None = None          # YYYY-MM-DD / YYYY-MM / 一句话（「2027 秋」）
    unit: str | None = None         # 有 metric 时默认用它的单位
    targetLow: float | None = None  # noqa: N815 — 和 app 的字段名一致
    targetHigh: float | None = None  # noqa: N815
    metric: str | None = None       # bodyfat / weight：自动读当前值
    groupId: str | None = None      # noqa: N815 — 挂在哪个 Agent 下
    position: int | None = None
    source: str | None = None       # 谁在加：leo（默认）/ main / Agent id


class GoalPatch(BaseModel):
    """只带要改的字段。detail / due / unit / targetLow / targetHigh / metric / groupId 给 null = 清掉；title / category / status / position 给 null = 不改。"""
    title: str | None = None
    category: str | None = None
    detail: str | None = None
    due: str | None = None
    unit: str | None = None
    targetLow: float | None = None  # noqa: N815
    targetHigh: float | None = None  # noqa: N815
    metric: str | None = None
    groupId: str | None = None      # noqa: N815
    status: str | None = None       # active / done / dropped（「不做了」）
    position: int | None = None
    source: str | None = None


def new_id(conn: sqlite3.Connection) -> str:
    while True:
        gid = f"goal-{uuid.uuid4().hex[:6]}"
        if not conn.execute("SELECT 1 FROM goals WHERE id=?", (gid,)).fetchone():
            return gid


@router.post("/api/goals")
def add_goal(body: GoalIn):
    actor = schedule.actor_of(body.source)
    metric = check_metric(body.metric)
    if body.category is None and not metric:
        raise HTTPException(400, L("要给分类：健康 / 学业 / 职业 / 财务", "Give a category: 健康 / 学业 / 职业 / 财务 (health / study / career / finance)"))
    g = {"title": clean(body.title, TITLE_MAX, "目标", "title", required=True),
         "category": check_category(body.category) if body.category is not None else "健康",
         "detail": clean(body.detail, DETAIL_MAX, "说明", "note", lines=True), "due": check_due(body.due),
         "unit": clean(body.unit, UNIT_MAX, "单位", "unit") or (METRICS[metric]["unit"] if metric else None),
         "target_low": check_number(body.targetLow, "下限", "low end"), "target_high": check_number(body.targetHigh, "上限", "high end"),
         "metric": metric}
    check_range(g)
    ts = now_iso()
    with _lock, gdb() as conn:
        g["group_id"] = check_group(conn, body.groupId)
        gid = new_id(conn)
        pos = body.position if body.position is not None else \
            (conn.execute("SELECT MAX(position) FROM goals WHERE status='active' AND deleted_at IS NULL").fetchone()[0] or 0) + 1
        conn.execute("""INSERT INTO goals(id, category, title, detail, metric, unit, target_low, target_high, due, group_id, source, status, position,
            created_at, added_by, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,NULL,'active',?,?,?,?)""",
                     (gid, g["category"], g["title"], g["detail"], g["metric"], g["unit"], g["target_low"], g["target_high"], g["due"], g["group_id"],
                      pos, ts, actor, ts))
        row = goal_row(conn, gid)
        log_id = log_change(conn, actor, gid, "add", None, snap(row))
        names = schedule.group_names(conn)
    _, summary = describe(None, snap(row), names)
    announce(actor, summary, names)
    return {"ok": True, "goal": goal_json(row, None), "logId": log_id, "summary": summary}


@router.patch("/api/goals/{gid}")
def patch_goal(gid: str, body: GoalPatch):
    actor = schedule.actor_of(body.source)
    given = body.model_fields_set - {"source"}
    with _lock, gdb() as conn:
        r = goal_row(conn, gid)
        before = snap(r)
        new = dict(before)
        if "title" in given and body.title is not None:
            new["title"] = clean(body.title, TITLE_MAX, "目标", "title", required=True)
        if "category" in given and body.category is not None:
            new["category"] = check_category(body.category)
        if "detail" in given:
            new["detail"] = clean(body.detail, DETAIL_MAX, "说明", "note", lines=True)
        if "due" in given:
            new["due"] = check_due(body.due)
        if "metric" in given:
            new["metric"] = check_metric(body.metric)
            if new["metric"] and "unit" not in given:
                new["unit"] = METRICS[new["metric"]]["unit"]
        if "unit" in given:
            new["unit"] = clean(body.unit, UNIT_MAX, "单位", "unit")
        if "targetLow" in given:
            new["target_low"] = check_number(body.targetLow, "下限", "low end")
        if "targetHigh" in given:
            new["target_high"] = check_number(body.targetHigh, "上限", "high end")
        check_range(new)
        if "groupId" in given:
            new["group_id"] = check_group(conn, body.groupId)
        if "status" in given and body.status is not None:
            new["status"] = check_status(body.status)
            if new["status"] != before["status"]:
                new["closed_at"] = None if new["status"] == "active" else now_iso()
        if "position" in given and body.position is not None:
            new["position"] = int(body.position)
        if all(new[c] == before[c] for c in EDIT_COLS):
            return {"ok": True, "changed": False, "goal": goal_json(r, None), "logId": None, "summary": ""}
        names = schedule.group_names(conn)
        action, summary = describe(before, new, names)
        write_goal(conn, gid, {c: new[c] for c in SNAP_COLS if new[c] != before[c]})
        log_id = log_change(conn, actor, gid, action, before, new)
        row = goal_row(conn, gid)
    announce(actor, summary, names)
    return {"ok": True, "changed": True, "goal": goal_json(row, None), "logId": log_id, "summary": summary}


class UndoIn(BaseModel):
    redo: bool = False
    source: str | None = None


@router.post("/api/goals/undo/{log_id}")
def undo(log_id: int, body: UndoIn | None = None):
    """撤销一次改动（redo = 再做回来）。只改回这次动过、之后没人再改过的字段（kept 里是后来又改过、没动的）；
    全都后来又改过 = 409。撤销「加了」= 这个目标藏起来。"""
    redo = bool(body and body.redo)
    actor = schedule.actor_of(body.source if body else None)
    with _lock, gdb() as conn:
        row = conn.execute("SELECT * FROM goal_log WHERE id=?", (log_id,)).fetchone()
        if row is None:
            raise HTTPException(404, L("没有这条改动", "No such change"))
        if bool(row["undone_at"]) != redo:
            raise HTTPException(409, L("已经撤销过了" if not redo else "没撤销过", "Already undone" if not redo else "Not undone"))
        g = conn.execute("SELECT * FROM goals WHERE id=?", (row["goal"],)).fetchone()
        if g is None:
            raise HTTPException(404, L("这个目标不在了", "That goal is gone"))
        before = json.loads(row["before"]) if row["before"] else None
        after = json.loads(row["after"]) if row["after"] else None
        kept: list[str] = []
        if row["action"] == "add":
            conn.execute("UPDATE goals SET deleted_at=?, updated_at=? WHERE id=?", (None if redo else now_iso(), now_iso(), g["id"]))
        else:
            if g["deleted_at"]:
                raise HTTPException(409, L("这个目标已经撤掉了", "That goal was taken back"))
            src, dst = (before or {}, after or {}) if redo else (after or {}, before or {})
            cur, sets = snap(g), {}
            for c in EDIT_COLS:
                if src.get(c) == dst.get(c):
                    continue
                if cur.get(c) == src.get(c):
                    sets[c] = dst.get(c)
                    if c == "status":
                        sets["closed_at"] = dst.get("closed_at")
                else:
                    kept.append(c)
            if not sets:
                raise HTTPException(409, L("这个目标后来又改过了，撤销不了；直接改回去吧", "This goal has changed since, so this can't be undone; just edit it back"))
            write_goal(conn, g["id"], sets)
        conn.execute("UPDATE goal_log SET undone_at=? WHERE id=?", (None if redo else now_iso(), log_id))
        row = conn.execute("SELECT * FROM goal_log WHERE id=?", (log_id,)).fetchone()
        g = conn.execute("SELECT * FROM goals WHERE id=?", (row["goal"],)).fetchone()
        names = schedule.group_names(conn)
    change = change_json(row, names)
    log_activity(L(f"目标：{'做回来了' if redo else '撤销了'} · {change['summary']}", f"Goals: {'redid' if redo else 'undid'} · {change['summary']}"),
                 "edit", None if actor == "leo" else (who(actor, names) or actor))
    return {"ok": True, "change": change, "goal": goal_json(g, None) if not g["deleted_at"] else None, "kept": kept}


class SeenIn(BaseModel):
    ids: list[int]


@router.post("/api/goals/seen")
def seen(body: SeenIn):
    """目标页顶上那条点了「知道了」：这几条改动不再显示（撤销照样能用 undo）。"""
    ids = [int(i) for i in body.ids][:100]
    if not ids:
        return {"ok": True, "n": 0}
    with _lock, gdb() as conn:
        n = conn.execute(f"UPDATE goal_log SET seen_at=? WHERE seen_at IS NULL AND id IN ({','.join('?' * len(ids))})",  # noqa: S608 — 只有占位符
                         (now_iso(), *ids)).rowcount
    return {"ok": True, "n": n}
