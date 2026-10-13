"""「今天」页（2026-10-13 重做，设计见 redesign-1012 画布第 5 排）：服务器按三块分好组，app 只画。

三块各按一条标准，一件事只出现在一块里：
- 待处理（要动手）：等你点头的（收件箱）、可疑安全提醒、7 天内到期的待办和截止（过了期还没勾的留 3 天），按紧急程度排：
  待确认 → 安全 → 已逾期 → 按日子。右边一枚标签（今天 / 明天 / 周三 / 周五截止 / 安全 / 已逾期）。
  没日子的待办单独给（app 收在末尾）。推迟（app 里左滑）记在 today_snooze，到那天再出现。
- 日程（要到场）：课、你和 Agent 排的安排、邮件里写了时刻的活动（买了票、报了名）。同一活动去重：同一天同一时刻、
  标题相近或同一个链接的并成一条，状态并成标签（「已购票」）。过去的按实际时间放（训练用训练记录补，见 schedule.fill_training）。
  训练计划、练后复盘这类卡归到当天的训练（ATTACH），点开日程看。
- 报告（要阅读）：没归到日程的 Agent 卡（睡眠、饮食建议……）+ 邮件摘要（钱、状态这类只需知悉的，按类别计数）。
  睡眠卡带近 6 天恢复分，饮食卡带这一餐的营养和当天记了没有。
定时任务、后台任务不在这里（定时任务以后放设置；后台任务看对话页 Agent 栏的状态）。

GET  /api/today?day=YYYY-MM-DD        一天；不给 = 今天。别的日子：待处理只列那天到期的，没有收件箱、安全提醒和推迟
GET  /api/today/range?from=&days=     周 / 月视图：每天的日程、截止、待办（days ≤ 42；不补实际时间，免得一次查几十天训练记录）
POST /api/today/snooze {ref, until}   推迟一条待处理到 until（YYYY-MM-DD）；until 为 null = 取消推迟
"""
from __future__ import annotations

import asyncio
import json
import re
import sqlite3
from collections import Counter
from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

import data
import inbox
import schedule as sch
import sources
from chat import _lock, now_iso
from i18n import L

router = APIRouter()

WINDOW = 6           # 待处理看到今天之后第 6 天（含今天共 7 天，标签里的星期几不会重名）
RANGE_MAX = 42       # 周 / 月视图一次最多几天（6 周的月历）
DEFAULT_MIN = 60     # 没写结束时间的活动按一小时画
ATTACH = {"training_plan": "plan", "training_review": "review"}   # 归到当天训练日程的卡：计划、复盘
CARD_LABEL = {"training_plan": ("训练计划", "Training plan"), "training_review": ("练后记录", "Workout review")}
RETIRED = {"reminder", "mail_digest"}   # 旧卡：内容已经在待处理和邮件摘要里
APP_STATUS = {"planned": ("计划中", "Planned"), "in_progress": ("准备中", "Preparing")}   # 和 app 的申请状态同一套说法
MEALS_ON = None      # main.py 塞进来：某天记了几条饮食 {count, kcal}（饮食卡上的「今日尚未记录饮食」）；没有就不写这一行


# —— 表：推迟 ——————————————————————————————————————————————————————————

_ready = False


def tdb() -> sqlite3.Connection:
    global _ready
    conn = sch.sdb()
    if not _ready:
        conn.execute("CREATE TABLE IF NOT EXISTS today_snooze (ref TEXT PRIMARY KEY, until TEXT NOT NULL, updated_at TEXT NOT NULL)")
        _ready = True
    return conn


def hm(m: int) -> str:
    m = max(0, min(24 * 60, m))
    return f"{m // 60:02d}:{m % 60:02d}"


def clock(v: str | None) -> str:
    """「9:05」→「09:05」；认不出来是空字符串。"""
    m = sch.TIME_RE.match((v or "").strip())
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else ""


# —— 待处理 ——————————————————————————————————————————————————————————

def from_label(badge: str) -> str:
    """邮件条目从哪来：来自邮件 / 来自转发进来的那个邮箱（badge，比如「来自 Work」）。"""
    if not badge or badge in ("邮件", "Mail"):
        return L("来自邮件", "From email")
    return L(f"来自 {badge}", f"From {badge}")


def pending_sub(e: dict, status: dict[str, str]) -> str:
    """标题下面那一行：从哪来 / 哪门课 / 哪个 Agent 和申请进度，有时刻的带上时刻。"""
    parts: list[str] = []
    o = e["origin"]
    if e.get("project"):
        parts.append(e["project"]["title"])
    elif o == "mail":
        parts.append(from_label(e["badge"]))
    elif o in ("canvas", "course"):
        parts.append(e.get("course") or e["badge"])
    elif o == "apply":
        st = APP_STATUS.get(status.get(e["id"], ""))
        parts.append(e["badge"] + (f" · {L(*st)}" if st else ""))
    elif e["badge"]:
        parts.append(e["badge"])
    if e["start"]:
        parts.append(L(f"{e['start']} 截止", f"due {e['start']}") if e["kind"] == "deadline" else e["start"])
    return " · ".join(p for p in parts if p)


def chip(e: dict, d0: date) -> tuple[str, str]:
    """右边的标签和色调（app 配色：bad = 安全 / 逾期，warn = 今天，gold = 待确认，neutral = 其余）。"""
    if e["kind"] == "security":
        return L("安全", "Security"), "bad"
    if not e["date"]:
        return "", "neutral"
    due = date.fromisoformat(e["date"])
    diff = (due - d0).days
    if diff < 0:
        return L("已逾期", "Overdue"), "bad"
    zh = "今天" if diff == 0 else "明天" if diff == 1 else f"周{sch.WD_ZH[due.weekday()]}"
    en = "today" if diff == 0 else "tomorrow" if diff == 1 else sch.WD_EN[due.weekday()]
    label = L(f"{zh}截止", f"Due {en}") if e["kind"] == "deadline" else L(zh, en[:1].upper() + en[1:])
    return label, "warn" if diff == 0 else "neutral"


def pending_row(e: dict, d0: date, status: dict[str, str], other_day: bool = False) -> dict:
    title = e["title"]
    if e["origin"] == "apply":  # 「Northwind · Northwind Analyst Program」：岗位名里已经有机构名就不重复
        org, _, role = title.partition(" · ")
        if role.lower().startswith(org.lower()):
            title = role
    if other_day:
        lab, tone = ((L("已完成", "Done"), "good") if e["done"] else
                     (L("截止", "Due"), "neutral") if e["kind"] == "deadline" else (L("待办", "To do"), "neutral"))
    else:
        lab, tone = chip(e, d0)
    return {"id": e["id"], "ref": e["id"], "kind": e["kind"], "origin": e["origin"], "title": title, "sub": pending_sub(e, status),
            "chip": lab, "tone": tone, "date": e["date"], "time": e["start"], "detail": e["detail"], "link": e["link"],
            "done": e["done"], "urgent": e["urgent"], "agent": e["by"], "project": e.get("project"), "study": e.get("study"),
            "canTick": True, "canSnooze": not other_day}


def inbox_row(it: dict) -> dict:
    """等你点头的一张卡（完整的卡 app 从 /api/inbox 读，这里只给一行和顺序）。"""
    sub = " · ".join(x for x in (it.get("sourceName") or "", inbox.kind_label(it.get("kind") or "other")) if x)
    return {"id": f"inbox:{it['id']}", "ref": None, "kind": "inbox", "origin": "inbox", "inboxId": it["id"], "title": it.get("title") or "",
            "sub": sub, "chip": L("待确认", "Approve"), "tone": "gold", "date": None, "time": "", "detail": it.get("why") or "",
            "link": None, "done": False, "urgent": it.get("level") == "ring", "agent": it.get("source"), "project": None, "study": None,
            "canTick": False, "canSnooze": False}


def gather_pending(d0: date) -> tuple[list[dict], dict[str, str], dict[str, str], dict[str, str]]:
    """待处理的候选：课程 ddl、邮件条目、求职 / 申请、自己加的截止（带 done）。返回 (条目, 申请状态, 推迟到哪天, 出错)。"""
    now_dt = sch.now()
    rows_canvas, canvas_err = sch.canvas_rows()
    lo = (d0 - timedelta(days=sch.OVERDUE_DAYS)).isoformat()
    hi = (d0 + timedelta(days=WINDOW)).isoformat()
    with _lock, tdb() as conn:
        mk = sch.all_marks(conn)
        apps = sch.app_rows(conn)
        names = sch.group_names(conn)
        own = conn.execute("SELECT * FROM schedule_items WHERE deleted_at IS NULL AND kind='deadline' AND date>=? AND date<=?",
                           (lo, hi)).fetchall()
        snoozed = {r["ref"]: r["until"] for r in conn.execute("SELECT ref, until FROM today_snooze")}
    items = [sch.canvas_entry(x, mk, now_dt) for x in rows_canvas]
    items += [sch.mail_entry(x, mk, now_dt) for x in sch.mail_items()]
    items += [sch.app_entry(r, mk, names, now_dt) for r in apps if lo <= r["deadline"] <= hi]
    items += [sch.item_entry(r, mk, names, now_dt) for r in own]
    sch.with_projects(items)
    status = {f"app:{r['id']}": r["status"] for r in apps}
    return items, status, snoozed, ({"canvas": canvas_err} if canvas_err else {})


def build_pending(d0: date, inbox_items: list[dict]) -> tuple[list[dict], list[dict], dict[str, str]]:
    """(待处理, 没日子的待办, 出错)。"""
    items, status, snoozed, errors = gather_pending(d0)
    d0s = d0.isoformat()
    if d0 != sch.today():  # 别的日子：只列那天到期的（做没做完都列）
        rows = [pending_row(e, d0, status, other_day=True) for e in items if e["date"] == d0s and e["kind"] in ("todo", "deadline")]
        rows.sort(key=lambda r: (r["done"], r["time"] or "99:99", r["title"]))
        return rows, [], errors
    lo = (d0 - timedelta(days=sch.OVERDUE_DAYS)).isoformat()
    hi = (d0 + timedelta(days=WINDOW)).isoformat()
    sec, dated, nodate = [], [], []
    for e in items:
        if e["done"] or snoozed.get(e["id"], "") > d0s:
            continue
        if e["kind"] == "security":
            sec.append(e)
        elif e["kind"] in ("todo", "deadline"):
            if not e["date"]:
                nodate.append(e)
            elif lo <= e["date"] <= hi:
                dated.append(e)
    sec.sort(key=lambda e: str(e.get("firstSeen") or ""))
    dated.sort(key=lambda e: (e["date"], not e["urgent"], e["start"] or "99:99", e["kind"] != "deadline", e["title"]))
    nodate.sort(key=lambda e: (not e["urgent"], str(e.get("firstSeen") or ""), e["title"]))
    rows = [inbox_row(i) for i in inbox_items]
    rows += [pending_row(e, d0, status) for e in sec + dated]
    return rows, [pending_row(e, d0, status) for e in nodate], errors


# —— 日程：去重、归并 —————————————————————————————————————————————————————

# 邮件里的状态（买了票、报了名）：标题里认出来变成日程上的标签，标题去掉这几个字
STATUS_TAGS = (
    (re.compile(r"票|ticket", re.I), ("已购票", "Ticket")),
    (re.compile(r"报名|regist", re.I), ("已报名", "Registered")),
    (re.compile(r"预约|预订|book|reserv", re.I), ("已预约", "Booked")),
    (re.compile(r"确认|confirm", re.I), ("已确认", "Confirmed")),
)
STATUS_HEAD = re.compile(r"^\s*(?:已购票|已有票|已购|已报名|已预约|已确认|(?:tickets?|booking)\s*(?:confirmed|booked)?|booked|registered|confirmed)"
                         r"\s*[：:·\-–]\s*", re.I)
STATUS_TAIL = re.compile(r"\s*[（(]?(?:已购票|已有票|门票已购|票已购|已购|已报名|报名成功|已预约|预约成功|已确认)[）)]?\s*$")
STOP = {"the", "and", "for", "with", "from", "your", "you", "our", "event", "events", "ticket", "tickets", "order", "booking",
        "confirmation", "confirmed", "registered"}
LATIN = re.compile(r"[A-Za-z][A-Za-z0-9'&.-]{2,}")
CJK = re.compile(r"[一-鿿]+")
END_RE = re.compile(r"(\d{1,2}:\d{2})\s*(?:–|-|—|~|至|到)\s*(\d{1,2}:\d{2})")


def clean_title(t: str) -> str:
    out = STATUS_TAIL.sub("", STATUS_HEAD.sub("", t)).strip()
    return out or t


def tag_of(title: str) -> str | None:
    return next((L(*lab) for rx, lab in STATUS_TAGS if rx.search(title)), None)


def latin(s: str) -> set[str]:
    return {w.lower().strip(".'-&") for w in LATIN.findall(s)} - STOP


def grams(s: str) -> set[str]:
    out: set[str] = set()
    for run in CJK.findall(s):
        out |= {run[i:i + 2] for i in range(len(run) - 1)} if len(run) > 1 else {run}
    return out


def core(e: dict) -> str:
    return clean_title(e["title"]) if e["origin"] == "mail" else e["title"]


def same_activity(a: dict, b: dict) -> bool:
    """同一活动：同一天，同一个链接；或者同一时刻、标题相近（字面重合过半，或者一边标题里的名字都出现在另一边的标题和正文里）。
    例：「Acme AI 创始人炉边谈已购票」和「已有票：Sam Lee 炉边对话」都是 17:00，前者正文写着 Sam Lee。"""
    if a["date"] != b["date"]:
        return False
    if a.get("link") and a.get("link") == b.get("link"):
        return True
    if not a["start"] or a["start"] != b["start"]:
        return False
    if a["kind"] == "class" and b["kind"] == "class":  # 课表里同一时刻的两节只在完全同名时算重复
        return a["title"] == b["title"]
    ta, tb = core(a), core(b)
    sa, sb = latin(ta) | grams(ta), latin(tb) | grams(tb)
    if sa and sb and len(sa & sb) / min(len(sa), len(sb)) >= 0.5:
        return True
    for x, y in ((a, b), (b, a)):
        names = latin(core(x))
        if names and len(names & latin(f"{y['title']} {y.get('detail') or ''}")) >= max(1, round(len(names) * 0.6)):
            return True
    return False


def end_from(e: dict) -> str:
    """邮件只写了开始时刻：正文里写着「17:00–19:00」就用它的结束。"""
    for m in END_RE.finditer(e.get("detail") or ""):
        if clock(m.group(1)) == e["start"] and clock(m.group(2)) > e["start"]:
            return clock(m.group(2))
    return ""


def prio(e: dict) -> tuple:
    """并成一条时谁做主：自己和 Agent 排的（能改）> 课表 > 邮件（先到的那封）。"""
    return ({"own": 0, "calendar": 1}.get(e["origin"], 2), str(e.get("firstSeen") or ""), e["id"])


def combine(g: list[dict]) -> dict:
    p = dict(g[0])
    tags: list[str] = []
    for m in g:
        tag = tag_of(m["title"]) if m["origin"] == "mail" else None
        if tag and tag not in tags:
            tags.append(tag)
    if p["origin"] == "mail":
        p["title"] = clean_title(p["title"])
    if not p["end"] and p["start"]:
        ends = [x for x in (m["end"] or end_from(m) for m in g) if x]
        p["end"] = max(ends) if ends else ""
    p["link"] = p["link"] or next((m["link"] for m in g if m["link"]), None)
    p["detail"] = p["detail"] or next((m["detail"] for m in g if m["detail"]), "")
    p["note"] = p["note"] or next((m["note"] for m in g if m["note"]), "")
    p["skip"] = any(m["skip"] for m in g)
    p["refs"] = [m["id"] for m in g]
    p["tags"] = tags
    return p


def merge_events(evs: list[dict]) -> list[dict]:
    groups: list[list[dict]] = []
    for e in sorted(evs, key=prio):
        for g in groups:
            if any(same_activity(m, e) for m in g):
                g.append(e)
                break
        else:
            groups.append([e])
    return [combine(g) for g in groups]


def is_event(e: dict) -> bool:
    """要到场的：课、一段安排、邮件里写了时刻的活动（状态类：买了票、报了名）。截止、待办、钱不算。"""
    return e["kind"] in ("class", "event") or (e["origin"] == "mail" and e["kind"] == "status" and bool(e["start"]))


def with_marks(evs: list[dict], mk: dict[str, dict]) -> None:
    """邮件里的活动在这一层的标记（跳过、备注、去没去、实际时间，见 schedule.mark）。课的标记 build_timeline 已经套过。"""
    for e in evs:
        m = mk.get(e["id"]) if e["origin"] == "mail" else None
        if not m:
            continue
        e["skip"] = bool(m.get("skip"))
        e["note"] = m.get("note") or ""
        e["attended"] = None if m.get("attended") is None else bool(m["attended"])
        e["actualStart"], e["actualEnd"] = m.get("actual_start") or "", m.get("actual_end") or ""
        e["editable"] = True


def place(e: dict, d0: date, t: date, now_m: int | None) -> None:
    """时间格里放在哪（slotStart / slotEnd）和现在是什么状态。过去的有实际时间就按实际的放，原定的 app 写在旁边（moved）。"""
    over = e["past"] or d0 < t
    actual = over and bool(e["actualStart"])
    s, en = (e["actualStart"], e["actualEnd"]) if actual else (e["start"], e["end"])
    e["moved"] = actual and (e["actualStart"], e["actualEnd"]) != (e["start"], e["end"])
    if s and not e["allDay"]:
        sm = sch.minutes(s)
        em = sch.minutes(en) if en else sm + DEFAULT_MIN
        if em <= sm:  # 跨午夜的画到 24:00
            em = min(sm + DEFAULT_MIN, 24 * 60)
        e["slotStart"], e["slotEnd"] = hm(sm), hm(em)
    else:
        sm = em = None
        e["slotStart"] = e["slotEnd"] = ""
    if e["skip"] or e["attended"] is False:
        state = "skipped"
    elif e["attended"] is True:
        state = "attended"
    elif over:
        state = "past"
    elif now_m is not None and sm is not None and em is not None and sm <= now_m < em:
        state = "now"
    else:
        state = "upcoming"
    e["state"] = state


def feed_cards(d0: date) -> list[dict]:
    """那天没划掉的卡（同一天同一种只留最新的，旧的出卡时已划掉），早的在前。"""
    with _lock, data.ddb() as conn:
        rows = conn.execute("SELECT * FROM feed_items WHERE dismissed=0 AND substr(created_at,1,10)=? ORDER BY created_at",
                            (d0.isoformat(),)).fetchall()
    out = []
    for r in rows:
        try:
            dd = json.loads(r["data"]) if r["data"] else None
        except ValueError:
            dd = None
        out.append({"id": r["id"], "kind": r["kind"] or "", "agent": r["group_id"], "title": r["title"], "body": r["body"] or "",
                    "cta": r["cta"] or "", "data": dd, "createdAt": r["created_at"], "time": str(r["created_at"])[11:16],
                    "unseen": not r["seen_at"]})
    return out


def attach(events: list[dict], cards: list[dict]) -> set[str]:
    """训练计划、练后复盘归到当天的训练（Agent 排训练的 key 是 <agent>:training:<日期>）。没有训练日程就留在报告里。"""
    used: set[str] = set()
    train = next((e for e in events if ":training:" in str(e.get("key") or "")), None)
    if train is None:
        return used
    for c in cards:
        if c["kind"] in ATTACH:
            train.setdefault("cards", []).append({**c, "slot": ATTACH[c["kind"]], "label": L(*CARD_LABEL[c["kind"]])})
            used.add(c["id"])
    return used


def build_schedule(d0: date, now_m: int | None) -> tuple[list[dict], dict[str, str], bool]:
    t = sch.today()
    tl = sch.build_timeline(d0, 1)
    with _lock, tdb() as conn:
        mk = sch.all_marks(conn)
    evs = [e for e in tl["events"] if is_event(e)]
    with_marks(evs, mk)
    for e in evs:
        if e["origin"] == "mail":  # 邮件里的活动能标跳过、备注（schedule.mark 收 mail:）
            e["editable"] = True
    out = merge_events(evs)
    for e in out:
        place(e, d0, t, now_m)
    out.sort(key=lambda e: (bool(e["slotStart"]), e["slotStart"], e["title"]))
    return out, tl["errors"], tl["calendar"]


# —— 报告 ————————————————————————————————————————————————————————————

MEAL_WORDS = (("早餐", "Breakfast", 10 * 60 + 30), ("午餐", "Lunch", 15 * 60), ("晚餐", "Dinner", 24 * 60))


def sleep_of(d0: date) -> dict | None:
    """睡眠卡的数字：那天的恢复分、睡了多久，近 6 天的恢复分（卡上画折线）和最低的那天。"""
    ah = sources.apple_health
    if ah is None:
        return None
    try:
        try:
            s = ah.recovery_series(6, end=d0)
        except TypeError:  # 老版本的脚本没有 end：多取几天再截
            n = (sch.today() - d0).days + 6
            if n > 60:
                return None
            s = ah.recovery_series(n)
    except Exception:  # noqa: BLE001 — 健康数据读不到，卡片照常（只是没有数字）
        return None
    days = [x for x in s.get("days") or [] if str(x.get("date")) <= d0.isoformat()][-6:]
    if not days:
        return None
    cur = days[-1] if days[-1].get("date") == d0.isoformat() else {}
    series = [{"date": x["date"], "score": x.get("score")} for x in days]
    valid = [x for x in series if x["score"] is not None]
    sleep = ((cur.get("components") or {}).get("sleep")) or {}
    return {"score": cur.get("score"), "label": cur.get("label") or "", "band": cur.get("band"), "sleepMin": sleep.get("minutes"),
            "series": series, "low": min(valid, key=lambda x: x["score"]) if len(valid) >= 2 else None}


def meal_of(c: dict, d0: date) -> dict:
    """饮食卡：哪一餐的建议（按第一餐的时刻）、吃什么（卡片标题冒号后面那段）、营养合计、那天记了没有。"""
    dd = c["data"] if isinstance(c.get("data"), dict) else {}
    meals = [m for m in dd.get("meals") or [] if isinstance(m, dict)]
    first = next((clock(m.get("time")) for m in meals if clock(m.get("time"))), "") or c["time"]
    heading = L("饮食建议", "Meal plan")
    if clock(first):
        mins = sch.minutes(clock(first))
        zh, en, _ = next(w for w in MEAL_WORDS if mins < w[2])
        heading = L(f"{zh}建议", f"{en} plan")
    parts = re.split(r"[：:]", c["title"], maxsplit=1)
    dishes = re.sub(r"\s*\+\s*", L("，", ", "), parts[1].strip()) if len(parts) == 2 else ""
    if not dishes:
        dishes = L("，", ", ").join(str(m["items"][0].get("name")) for m in meals if m.get("items") and isinstance(m["items"][0], dict))
    totals = dd.get("totals") if isinstance(dd.get("totals"), dict) else None
    logged = None
    if MEALS_ON is not None:
        try:
            logged = MEALS_ON(d0)  # pylint: disable=not-callable
        except Exception:  # noqa: BLE001 — 读不到就不写「记了没有」
            logged = None
    return {"heading": heading, "dishes": dishes, "totals": totals, "logged": logged}


# 邮件摘要的类别：先认钱（扣费 / 退款），再按标题里的字认（票 → 预约与提交 → 账户），都不是 = 其他
MAIL_CATS = (
    ("ticket", ("票", "ticket")),
    ("booking", ("预约", "预订", "提交", "报名", "申请", "登记", "排队", "book", "submit", "regist", "appointment", "application")),
    ("account", ("账户", "账号", "注册", "开通", "转入", "号码", "会员", "订阅", "account", "sign", "subscri", "member")),
)
CAT_LABEL = {"charge": ("扣费", "Charges"), "refund": ("退款", "Refunds"), "ticket": ("已购票", "Tickets"),
             "booking": ("预约与提交", "Bookings"), "account": ("账户", "Accounts"), "other": ("其他", "Other")}
CAT_ORDER = ("booking", "ticket", "account", "charge", "refund", "other")


def mail_category(e: dict) -> str:
    low = e["title"].lower()
    if e["kind"] == "money":
        return "refund" if ("退款" in low or "refund" in low) else "charge"
    return next((k for k, words in MAIL_CATS if any(w in low for w in words)), "other")


def mail_digest(d0: date) -> dict | None:
    """只需知悉的邮件（钱、状态）合成一张：那天的 + （今天）还没过期的没日子的。打过勾的不算。"""
    now_dt = sch.now()
    with _lock, tdb() as conn:
        mk = sch.all_marks(conn)
    d0s, is_today = d0.isoformat(), d0 == sch.today()
    keep = [e for e in (sch.mail_entry(x, mk, now_dt) for x in sch.mail_items())
            if e["kind"] in ("status", "money") and not e["done"] and (e["date"] == d0s or (is_today and not e["date"]))]
    if not keep:
        return None
    for e in keep:
        e["category"] = mail_category(e)
    n = Counter(e["category"] for e in keep)
    cats = sorted(n, key=lambda k: (-n[k], CAT_ORDER.index(k)))
    keep.sort(key=lambda e: (cats.index(e["category"]), str(e.get("firstSeen") or ""), e["title"]))
    return {"type": "mail", "id": f"mail-digest:{d0s}", "kind": "mail_digest", "title": L("邮件摘要", "Email digest"), "count": len(keep),
            "categories": [{"key": k, "label": L(*CAT_LABEL[k]), "count": n[k]} for k in cats],
            "items": [{"ref": e["id"], "title": e["title"], "kind": e["kind"], "category": e["category"], "date": e["date"], "time": e["start"],
                       "detail": e["detail"], "link": e["link"], "source": e["badge"]} for e in keep]}


def build_reports(d0: date, cards: list[dict], used: set[str]) -> list[dict]:
    out = []
    for c in cards:
        if c["id"] in used or c["kind"] in RETIRED:
            continue
        r = {**c, "type": "card"}
        if c["kind"] == "sleep_report":
            r["sleep"] = sleep_of(d0)
        elif c["kind"] == "meal_plan":
            r["meal"] = meal_of(c, d0)
        out.append(r)
    digest = mail_digest(d0)
    if digest:
        out.append(digest)
    return out


# —— 一天 ————————————————————————————————————————————————————————————

def build_day(d0: date, inbox_items: list[dict], errors: dict[str, str]) -> dict:
    t, now_dt = sch.today(), sch.now()
    now_m = now_dt.hour * 60 + now_dt.minute if d0 == t else None
    pending, nodate, perr = build_pending(d0, inbox_items)
    events, serr, calendar = build_schedule(d0, now_m)
    cards = feed_cards(d0)
    used = attach(events, cards)
    reports = build_reports(d0, cards, used)
    timed = [e for e in events if e["slotStart"]]
    return {"ok": True, "day": d0.isoformat(), "today": t.isoformat(), "weekday": sch.weekday(d0),
            "now": hm(now_m) if now_m is not None else None,
            "pending": {"items": pending, "nodate": nodate},
            "schedule": {"items": timed, "allDay": [e for e in events if not e["slotStart"]], "calendar": calendar},
            "reports": reports, "errors": {**errors, **perr, **serr}}


@router.get("/api/today")
async def get_today(request: Request, day: str | None = None):
    """「今天」页的三块，按 day（不给 = 今天）。收件箱只在今天给。"""
    d0 = date.fromisoformat(sch.check_date(day, "day")) if day else sch.today()
    errors: dict[str, str] = {}
    items: list[dict] = []
    if d0 == sch.today():
        try:
            got = await inbox.list_items(request, "pending")
            items = got.get("items") or []
        except Exception as exc:  # noqa: BLE001 — 收件箱读不到，其余照常
            errors["inbox"] = str(exc)[:200]
    return await asyncio.to_thread(build_day, d0, items, errors)


# —— 周 / 月 ——————————————————————————————————————————————————————————

def range_row(e: dict, typ: str, status: dict[str, str]) -> dict:
    title = e["title"]
    if typ == "event":
        loc = e["location"] if re.search(r"\w", e["location"] or "") else ""  # 课表里有写成「-」的
        sub = " · ".join(x for x in (loc, *e.get("tags", [])) if x) or (e["badge"] if e["origin"] == "own" else "")
    else:
        sub = pending_sub(e, status)
        if e["origin"] == "apply":
            org, _, role = title.partition(" · ")
            title = role if role.lower().startswith(org.lower()) else title
    return {"id": e["id"], "refs": e.get("refs") or [e["id"]], "date": e["date"], "type": typ, "kind": e["kind"], "origin": e["origin"],
            "title": title, "sub": sub, "start": e["start"], "end": e["end"], "allDay": e["allDay"], "done": e["done"],
            "skip": bool(e["skip"] or e["attended"] is False), "past": e["past"], "tags": e.get("tags") or [], "agent": e["by"],
            "link": e["link"], "detail": e["detail"], "study": e.get("study"), "project": e.get("project")}


def build_range(lo: date, days: int) -> dict:
    tl = sch.build_timeline(lo, days, actual=False)
    with _lock, tdb() as conn:
        mk = sch.all_marks(conn)
        status = {f"app:{r['id']}": r["status"] for r in sch.app_rows(conn)}
    evs = [e for e in tl["events"] if is_event(e)]
    with_marks(evs, mk)
    by_day: dict[str, list[dict]] = {}
    for e in evs:
        by_day.setdefault(e["date"], []).append(e)
    items = [range_row(e, "event", status) for d in sorted(by_day) for e in merge_events(by_day[d])]
    items += [range_row(e, "deadline" if e["kind"] == "deadline" else "todo", status)
              for e in tl["events"] if e["kind"] in ("deadline", "todo")]
    rank = {"event": 0, "deadline": 1, "todo": 2}
    items.sort(key=lambda r: (r["date"], rank[r["type"]], not r["allDay"], r["start"] or "", r["title"]))
    return {"ok": True, "from": lo.isoformat(), "days": days, "today": sch.today().isoformat(), "items": items, "errors": tl["errors"]}


@router.get("/api/today/range")
async def get_range(from_: str = Query(..., alias="from"), days: int = 7):
    if not 1 <= days <= RANGE_MAX:
        raise HTTPException(400, L(f"days 取 1 到 {RANGE_MAX}", f"days must be between 1 and {RANGE_MAX}"))
    lo = date.fromisoformat(sch.check_date(from_, "from"))
    return await asyncio.to_thread(build_range, lo, days)


# —— 推迟 ————————————————————————————————————————————————————————————

class SnoozeIn(BaseModel):
    ref: str
    until: str | None = None


@router.post("/api/today/snooze")
async def snooze(body: SnoozeIn):
    """推迟一条待处理（左滑）：到 until 那天再出现在「今天」；until 为 null = 取消。只影响「今天」页，推送和起床报告照旧。"""
    ref = body.ref.strip()
    if ref.split(":", 1)[0] not in sch.SNAP_ORIGIN or len(ref) > 400:
        raise HTTPException(400, L("只有待处理里的条目能推迟", "Only items under To do can be postponed"))
    until = sch.check_date(body.until, "until") if body.until else None
    with _lock, tdb() as conn:
        if until:
            conn.execute("INSERT OR REPLACE INTO today_snooze(ref, until, updated_at) VALUES(?,?,?)", (ref, until, now_iso()))
        else:
            conn.execute("DELETE FROM today_snooze WHERE ref=?", (ref,))
    return {"ok": True, "ref": ref, "until": until}
