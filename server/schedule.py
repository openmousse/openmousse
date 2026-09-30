"""日程层和「要记得的」（2026-09-26）。

日程 = 课表（calendar 数据源，只读）+ 这一层自己的日程 + 到期那天的截止，合成一条时间线；
要记得的 = 课程 ddl（study.deadlines_cmd）+ 邮件里抽出来的事（server.json 的 remember.mail）+ 求职 / 申请的 ddl（applications）
+ 自己加的截止。一件事只出现一次：到期那天在那天的时间线里（带勾），之前在「要记得的」里；没日期的钱 / 状态类邮件折成「邮件动态」。

表（grava.db）
- schedule_items：自己的日程。kind = event（一段安排）/ deadline（截止，进「要记得的」）。source = 谁加的（leo / main / Agent id）；
  key = Agent 的幂等键，同一个 key 再加一次就是改那一条（比如 fitness:training:2026-09-28）。删除是软删（deleted_at），能撤销。
  过去的记实际发生的：attended（1 做了 / 0 没做）、actual_start / actual_end。
- schedule_marks：源头改不了的东西在这一层的标记，按 ref 一行。课：skip 不去（1）/ 照去（0，盖过每周那一行）、改地点、备注、
  去没去、实际时间；「要记得的」打勾 = done_at。title / date / time 是快照：源头没了以后（作业交了、邮件条目过期）过去的日子还显示得出来。
- schedule_log：每次改动一行（谁、改了什么、之前 / 之后），给对话里的日程卡和撤销用。Agent 在一次回复里改的，
  回复结束时挂到那条回复下面（message_id，见 cards.on_run_end）；回复进行中当场发一张卡（SSE card）。

ref 的写法（Agent 用 schedule_ctl.py day / remember 的输出，不用自己拼）
- item:<id>                            自己的日程
- ics:<YYYY-MM-DD>T<HH:MM>|<标题>       课表里的一节（全天的：ics:<YYYY-MM-DD>|<标题>）
- icss:<周几 0-6>|<HH:MM>|<标题>         每周同一节（「以后每周这节都不去」）
- canvas:<作业链接>                      课程 ddl（没有链接：canvas:<课程>|<标题>|<截止>）
- mail:<条目 id>                         邮件条目（remember.mail.items 那个 JSON）
- app:<id>                               求职 / 申请（applications 表）

打勾 = 做完了或不用管：从「要记得的」、ddl 推送、起床报告里去掉（Grava 的 suggestion_watcher 直接读 schedule_marks）；
邮件条目同时写回来源（remember.mail.cmd --done / --undo）。改邮件条目（抽错了，main 在对话里改）走 remember.mail.cmd --edit。

server.json（都可选；没有哪一项，那一类就没有）
  "remember": {"mail": {"items": "条目 JSON 的路径", "cmd": ["python3", ".../mail_digest.py"],
                        "sources": {"来源键": "显示名，空字符串 = 「邮件」"}, "link": "原文链接模板，{thread_id} 换成邮件会话 id"}}
iPhone 日历订阅的令牌和四类开关存 grava.db 的 settings（schedule_feed）；GET /cal/<令牌>.ics 在 /api 之外，不要认证，令牌就是密码。
每次来取记下时间和粗分的日历种类（settings 的 schedule_feed_seen，「我 → 连接」据此看订阅通不通）。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import secrets
import sqlite3
import subprocess
import urllib.parse
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel

import chat
import sources
import study
from chat import _lock, db, now_iso
from config import TZ, raw, settings
from i18n import L

router = APIRouter()
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
ID_RE = re.compile(r"^[a-z0-9_-]{1,40}$")
WD_ZH, WD_EN = "一二三四五六日", ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
MAIL_KINDS = ("todo", "money", "status", "security")
REMEMBER_DAYS = {"apply": 30, "own": 60}   # 「要记得的」看多远：求职 / 申请 30 天内的，自己加的截止 60 天内的（课程和邮件由来源自己限）
OVERDUE_DAYS = 3                           # 过了期还没勾的，留几天（「过了的」）
FEED_DAYS = (14, 60)                       # 订阅里往前 / 往后放多少天
TRAINS_ON = None                           # main.py 塞进来：某天训记里的训练（过去的训练日程补实际时间）


# —— 表 ————————————————————————————————————————————————————————————

_ready = False


def sdb() -> sqlite3.Connection:
    global _ready
    conn = db()
    if not _ready:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS schedule_items (id TEXT PRIMARY KEY, kind TEXT NOT NULL DEFAULT 'event', title TEXT NOT NULL,
            date TEXT NOT NULL, start TEXT, end TEXT, location TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT 'leo', key TEXT, attended INTEGER, actual_start TEXT, actual_end TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT);
        CREATE INDEX IF NOT EXISTS schedule_items_date ON schedule_items(date);
        CREATE INDEX IF NOT EXISTS schedule_items_key ON schedule_items(key);
        CREATE TABLE IF NOT EXISTS schedule_marks (ref TEXT PRIMARY KEY, skip INTEGER, location TEXT, note TEXT, done_at TEXT,
            attended INTEGER, actual_start TEXT, actual_end TEXT, title TEXT, date TEXT, time TEXT, source TEXT, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS schedule_log (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, actor TEXT NOT NULL,
            thread TEXT, message_id INTEGER, target TEXT NOT NULL, action TEXT NOT NULL, title TEXT NOT NULL, summary TEXT NOT NULL,
            before TEXT, after TEXT, undone_at TEXT);
        CREATE INDEX IF NOT EXISTS schedule_log_thread ON schedule_log(thread, id);
        """)
        _ready = True
    return conn


ITEM_COLS = ("id", "kind", "title", "date", "start", "end", "location", "note", "source", "key", "attended", "actual_start", "actual_end",
             "created_at", "updated_at", "deleted_at")
MARK_COLS = ("ref", "skip", "location", "note", "done_at", "attended", "actual_start", "actual_end", "title", "date", "time", "source", "updated_at")


def row_dict(r: sqlite3.Row | None) -> dict | None:
    return dict(r) if r is not None else None


# —— 时间 ——————————————————————————————————————————————————————————

def now() -> datetime:
    return datetime.now(TZ)


def today() -> date:
    return now().date()


def weekday(d: date) -> str:
    return L(WD_ZH[d.weekday()], WD_EN[d.weekday()])


def check_date(v: str | None, field: str = "date") -> str | None:
    if v is None:
        return None
    v = v.strip()
    try:
        if not DATE_RE.match(v):
            raise ValueError
        date.fromisoformat(v)
    except ValueError as exc:
        raise HTTPException(400, L(f"{field} 要写成 YYYY-MM-DD", f"{field} must be YYYY-MM-DD")) from exc
    return v


def check_time(v: str | None, field: str = "start") -> str | None:
    if v is None or not v.strip():
        return None
    m = TIME_RE.match(v.strip())
    if not m:
        raise HTTPException(400, L(f"{field} 要写成 HH:MM", f"{field} must be HH:MM"))
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def at(d: str, hhmm: str | None, end_of_day: bool = False) -> datetime:
    """某天某个时刻（没有时刻：当天开始，end_of_day 时是当天结束）。"""
    base = datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=TZ)
    if hhmm:
        return base + timedelta(minutes=minutes(hhmm))
    return base + timedelta(days=1, seconds=-1) if end_of_day else base


def day_label(d: date) -> str:
    """改动摘要里的日子：今天 / 明天 / 周三 / 10/12。"""
    t = today()
    diff = (d - t).days
    if diff == 0:
        return L("今天", "today")
    if diff == 1:
        return L("明天", "tomorrow")
    if diff == -1:
        return L("昨天", "yesterday")
    if 1 < diff < 7:
        return L(f"周{WD_ZH[d.weekday()]}", WD_EN[d.weekday()])
    return f"{d.month}/{d.day}"


def span(d: str | None, s: str | None, e: str | None = None) -> str:
    parts = [day_label(date.fromisoformat(d))] if d else []
    if s:
        parts.append(f"{s}–{e}" if e else s)
    elif d:
        parts.append(L("全天", "all day"))
    return " ".join(parts)


# —— 来源 ——————————————————————————————————————————————————————————

def group_names(conn: sqlite3.Connection) -> dict[str, str]:
    try:
        return {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM groups")}
    except sqlite3.Error:
        return {}


def who_name(by: str | None, names: dict[str, str]) -> str:
    """谁加的，显示用：你自己加的不写；主对话写助手的名字；Agent 写它的名字。"""
    if not by or by == "leo":
        return ""
    if by == "main":
        return settings.app_name
    return names.get(by, by)


def classes(lo: date, days: int) -> tuple[list[dict], str | None]:
    """课表里 [lo, lo+days) 的每一节（原样，没套标记）。没接日历 → ([], None)；拉取失败 → ([], 原因)。"""
    cal = sources.calendar_ics
    if cal is None:
        return [], None
    a = datetime.combine(lo, datetime.min.time(), TZ)
    try:
        items = cal.expand(cal.parse_events(cal.fetch("ic", False), TZ), a, a + timedelta(days=days))
    except (SystemExit, Exception):  # noqa: BLE001 — calendar_ics 拉取失败会 sys.exit
        return [], L("日历拉取失败，链接可能已失效", "Couldn't fetch the calendar; the link may have expired")
    out = []
    for x in items:
        s, e = x["start"].astimezone(TZ), x["end"].astimezone(TZ)
        title, all_day = str(x.get("title") or ""), bool(x.get("all_day"))
        out.append({"ref": f"ics:{s:%Y-%m-%d}|{title}" if all_day else f"ics:{s:%Y-%m-%dT%H:%M}|{title}",
                    "series": None if all_day else f"icss:{s.weekday()}|{s:%H:%M}|{title}",
                    "date": s.strftime("%Y-%m-%d"), "start": "" if all_day else s.strftime("%H:%M"), "end": "" if all_day else e.strftime("%H:%M"),
                    "startAt": s, "endAt": e, "allDay": all_day, "title": title, "location": str(x.get("location") or ""),
                    "busy": str(x.get("busy") or "")})
    return out, None


def canvas_rows() -> tuple[list[dict], str | None]:
    """课程 ddl：study.deadlines_cmd 打印的（缓存 30 分钟：第一次同步读，过期了后台刷）+ 学习台课程档案里的作业和考试（source = course）。"""
    try:
        return study.deadline_rows()
    except Exception:  # noqa: BLE001 — 读不到只是日程里少几条截止
        return [], L("读不到课程 ddl", "Couldn't read course deadlines")


def course_short(name: str) -> str:
    """课程名的缩写：Machine Learning Systems → MLS。本来就短的原样。"""
    name = (name or "").strip()
    words = re.findall(r"[A-Za-z]+", name)
    if len(name) <= 6 or len(words) < 2:
        return name[:8]
    caps = "".join(w[0] for w in words if w[0].isupper())
    return (caps or "".join(w[0].upper() for w in words))[:5]


def mail_cfg() -> dict:
    return ((raw().get("remember") or {}).get("mail") or {})


def mail_items() -> list[dict]:
    p = mail_cfg().get("items")
    if not p:
        return []
    try:
        data = json.loads(Path(p).expanduser().read_text(encoding="utf8"))
    except (OSError, ValueError):
        return []
    items = data.get("items") if isinstance(data, dict) else None
    return [x for x in (items or {}).values() if isinstance(x, dict) and x.get("id") and x.get("type") in MAIL_KINDS]


def mail_label(src: str | None) -> str:
    lab = (mail_cfg().get("sources") or {}).get(src or "")
    return lab or L("邮件", "Mail")


def mail_link(x: dict) -> str | None:
    tpl, tid = mail_cfg().get("link"), str(x.get("thread_id") or "")
    if not tpl or not tid or str(x.get("id", "")).startswith("manual-"):
        return None
    return str(tpl).replace("{thread_id}", urllib.parse.quote(tid))


def mail_cmd(*args: str) -> tuple[bool, str]:
    """把打勾、改动写回邮件条目的来源（remember.mail.cmd + 参数）。"""
    cmd = mail_cfg().get("cmd")
    if not cmd:
        return False, L("没配 remember.mail.cmd", "remember.mail.cmd isn't set")
    argv = [str(Path(str(x)).expanduser()) if str(x).startswith("~") else str(x) for x in (cmd if isinstance(cmd, list) else str(cmd).split())]
    try:
        r = subprocess.run([*argv, *args], capture_output=True, text=True, timeout=60, check=False)  # noqa: S603 — 命令来自本机配置文件
    except (subprocess.SubprocessError, OSError) as e:
        return False, str(e)[:200]
    return r.returncode == 0, (r.stdout if r.returncode == 0 else (r.stderr or r.stdout)).strip()[-300:]


def app_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    try:
        return conn.execute("SELECT * FROM applications WHERE deadline IS NOT NULL AND status IN ('planned','in_progress')").fetchall()
    except sqlite3.Error:  # 没有这张表（新实例）
        return []


def all_marks(conn: sqlite3.Connection) -> dict[str, dict]:
    return {r["ref"]: dict(r) for r in conn.execute("SELECT * FROM schedule_marks")}


# —— 条目 ——————————————————————————————————————————————————————————

def entry(**kw) -> dict:
    """时间线和「要记得的」共用的一行。kind：class 课 / event 一段安排 / deadline 截止 / todo 要办 / money 钱 / status 状态 / security 安全提醒；
    origin：calendar / own / canvas / mail / apply。"""
    base = {"id": "", "kind": "event", "origin": "own", "title": "", "detail": "", "location": "", "note": "", "date": None,
            "start": "", "end": "", "allDay": False, "badge": "", "by": None, "link": None, "done": False, "skip": False, "series": False,
            "attended": None, "actualStart": "", "actualEnd": "", "past": False, "tentative": False, "free": False, "clash": [],
            "editable": False, "group": None, "urgent": False}
    base.update(kw)
    return base


def class_entry(c: dict, mk: dict, now_dt: datetime) -> dict:
    occ = mk.get(c["ref"]) or {}
    ser = (mk.get(c["series"]) or {}) if c["series"] else {}
    skip_v = occ.get("skip") if occ.get("skip") is not None else ser.get("skip")
    skip, past = bool(skip_v), c["endAt"] < now_dt
    attended = bool(occ["attended"]) if occ.get("attended") is not None else (False if skip and past else None)
    return entry(id=c["ref"], kind="class", origin="calendar", title=c["title"], location=occ.get("location") or c["location"],
                 note=occ.get("note") or "", date=c["date"], start=c["start"], end=c["end"], allDay=c["allDay"], badge=L("课表", "Calendar"),
                 skip=skip, series=bool(ser.get("skip")) and occ.get("skip") is None, seriesRef=c["series"], attended=attended,
                 actualStart=occ.get("actual_start") or "", actualEnd=occ.get("actual_end") or "", past=past,
                 tentative=c["busy"] == "TENTATIVE", free=c["busy"] == "FREE", editable=True,
                 locationChanged=bool(occ.get("location")), sourceLocation=c["location"])


def item_entry(r: sqlite3.Row | dict, mk: dict, names: dict[str, str], now_dt: datetime) -> dict:
    ref = f"item:{r['id']}"
    m = mk.get(ref) or {}
    end = at(r["date"], r["end"] or r["start"], end_of_day=not (r["end"] or r["start"]))
    return entry(id=ref, kind="deadline" if r["kind"] == "deadline" else "event", origin="own", title=r["title"], location=r["location"],
                 note=r["note"], date=r["date"], start=r["start"] or "", end=r["end"] or "", allDay=not r["start"],
                 badge=who_name(r["source"], names), by=r["source"], done=bool(m.get("done_at")),
                 attended=None if r["attended"] is None else bool(r["attended"]), actualStart=r["actual_start"] or "",
                 actualEnd=r["actual_end"] or "", past=end < now_dt, editable=True, key=r["key"])


def canvas_ref(x: dict) -> str:
    return f"canvas:{x['url']}" if x.get("url") else f"canvas:{x.get('course')}|{x.get('title')}|{x.get('due')}"


def course_entry(x: dict, mk: dict, now_dt: datetime) -> dict:
    """学习台课程档案里的作业和考试（大纲里读出来、或者你和学习 Agent 加的）：点开直接去学习台那一节（study）。"""
    ref, due = f"course:{x.get('course_id')}/{x.get('id')}", str(x.get("due") or "")
    d, t = due[:10], due[11:16]
    t = "" if t == "23:59" else t
    course = str(x.get("course") or "")
    import coursefile as cf
    return entry(id=ref, kind="deadline", origin="course", title=str(x.get("title") or "").strip(),
                 detail=L(f"{course}{' ' if course[-1:].isascii() else ''}的{cf.ddl_label(x.get('kind'))}，在学习台的课程档案里。", f"{cf.ddl_label(x.get('kind'))} for {course}, from the study desk's course profile."),
                 date=d, start=t, allDay=not t, badge=x.get("code") or course_short(course), link=None,
                 done=bool(x.get("done") or (mk.get(ref) or {}).get("done_at")), past=at(d, t or None, end_of_day=not t) < now_dt, course=course,
                 study={"course": x.get("course_id"), "session": x.get("session_id")})


def canvas_entry(x: dict, mk: dict, now_dt: datetime) -> dict:
    if x.get("source") == "course":
        return course_entry(x, mk, now_dt)
    ref, due = canvas_ref(x), str(x.get("due") or "")
    d, t = due[:10], due[11:16]
    course = str(x.get("course") or "")
    return entry(id=ref, kind="deadline", origin="canvas", title=str(x.get("title") or "").strip(),
                 detail=L(f"{course} 的作业，交了会自动消失。", f"Assignment for {course}; it disappears once submitted."),
                 date=d, start=t, allDay=not t, badge=course_short(course), link=x.get("url"), done=bool((mk.get(ref) or {}).get("done_at")),
                 past=at(d, t or None, end_of_day=not t) < now_dt, course=course, study={"course": study_course_of(course), "session": None})


def study_course_of(name: str) -> str | None:
    """课程网站的课名 → 学习台里的课（文件夹名或课程档案的名字对得上）：「今天」页点截止直接去学习台那门课。"""
    try:
        names = study.courses()
    except Exception:  # noqa: BLE001
        return None
    if name in names:
        return name
    import coursefile as cf
    low = name.strip().lower()
    for c in names:
        p = cf.load(c)
        if p and low in (p["title"].lower(), p["code"].lower()):
            return c
    return None


def short_title(t: str) -> str:
    """早先抽的邮件标题前面带日期（「10/1 13:00 …」）：日期由 app 按 due 写一次，这里去掉。"""
    return re.sub(r"^\s*\d{1,2}/\d{1,2}(\s*[-–]\s*\d{1,2}/\d{1,2})?(\s+\d{1,2}:\d{2})?\s+", "", t).strip() or t


def mail_entry(x: dict, mk: dict, now_dt: datetime) -> dict:
    ref, due = f"mail:{x['id']}", str(x.get("due") or "")
    d, t = (due[:10], due[11:16]) if due else (None, "")
    return entry(id=ref, kind=x["type"], origin="mail", title=short_title(str(x.get("title") or "")), detail=str(x.get("detail") or ""),
                 date=d, start=t, allDay=bool(d) and not t, badge=mail_label(x.get("source")), link=mail_link(x),
                 done=bool(x.get("done") or (mk.get(ref) or {}).get("done_at")), urgent=bool(x.get("urgent")),
                 past=bool(d) and at(d, t or None, end_of_day=not t) < now_dt, firstSeen=x.get("first_seen"))


def app_entry(r: sqlite3.Row, mk: dict, names: dict[str, str], now_dt: datetime) -> dict:
    ref = f"app:{r['id']}"
    agent = "masters" if r["kind"] == "masters" else "apply"
    return entry(id=ref, kind="deadline", origin="apply", title=f"{r['org']} · {r['role']}", detail=r["next_step"] or "",
                 date=r["deadline"], allDay=True, badge=names.get(agent, agent), by=agent, link=r["link"] or None,
                 done=bool((mk.get(ref) or {}).get("done_at")), past=at(r["deadline"], None, end_of_day=True) < now_dt)


SNAP_ORIGIN = {"canvas": "canvas", "mail": "mail", "app": "apply", "item": "own", "course": "course"}


def snapshot_entry(m: dict) -> dict:
    """打过勾、源头已经没了的（作业交了、邮件条目过期）：过去的日子照快照显示。"""
    return entry(id=m["ref"], kind="deadline", origin=SNAP_ORIGIN.get(m["ref"].split(":", 1)[0], "own"), title=m.get("title") or "",
                 date=m["date"], start=m.get("time") or "", allDay=not m.get("time"), done=True, past=True)


def mark_clashes(events: list[dict]) -> None:
    """同一天时间段重叠的标出来（和谁撞了）。截止是一个时刻，不算；不去的课、勾掉的、FREE 的不算。没写结束的按一小时算。"""
    by_day: dict[str, list[tuple[int, int, dict]]] = {}
    for e in events:
        if e["allDay"] or not e["start"] or e["skip"] or e["done"] or e["free"] or e["kind"] in ("deadline", "money"):
            continue
        s = minutes(e["start"])
        en = minutes(e["end"]) if e["end"] else s + 60
        by_day.setdefault(e["date"], []).append((s, en if en > s else s + 60, e))
    for xs in by_day.values():
        for i, (s1, e1, a) in enumerate(xs):
            for s2, e2, b in xs[i + 1:]:
                if s1 < e2 and s2 < e1:
                    a["clash"].append(b["title"])
                    b["clash"].append(a["title"])


def fill_training(events: list[dict]) -> None:
    """过去（和今天练完）的训练日程：实际时间从训练记录来（Agent 排的 key 是 <agent>:training:<日期>）。"""
    if TRAINS_ON is None:
        return
    t = today()
    for e in events:
        if e["origin"] != "own" or ":training:" not in str(e.get("key") or "") or e["actualStart"] or date.fromisoformat(e["date"]) > t:
            continue
        try:
            trains = TRAINS_ON(date.fromisoformat(e["date"]))  # pylint: disable=not-callable
        except Exception:  # noqa: BLE001 — 读不到训练记录就不补
            continue
        first = next((x for x in trains if re.match(r"^\d{1,2}:\d{2}$", str(x.get("start") or ""))), None)
        if not first:
            continue
        s = minutes(first["start"])
        total = sum(int(x.get("minutes") or 0) for x in trains)
        e.update(actualStart=f"{s // 60:02d}:{s % 60:02d}", actualEnd=f"{(s + total) // 60 % 24:02d}:{(s + total) % 60:02d}",
                 actualFrom="workouts")
        if e["attended"] is None:
            e["attended"] = True


def order_key(e: dict) -> tuple:
    return (e["date"] or "9999", not e["allDay"], e["start"] or "", {"class": 0, "event": 1}.get(e["kind"], 2), e["title"])


# —— 时间线 ——————————————————————————————————————————————————————————

def build_timeline(lo: date, days: int) -> dict:
    hi = lo + timedelta(days=days)
    now_dt, lo_s, hi_s = now(), lo.isoformat(), hi.isoformat()
    cls, cal_err = classes(lo, days)
    rows_canvas, canvas_err = canvas_rows()
    with _lock, sdb() as conn:
        mk = all_marks(conn)
        own = conn.execute("SELECT * FROM schedule_items WHERE deleted_at IS NULL AND date>=? AND date<?", (lo_s, hi_s)).fetchall()
        apps = app_rows(conn)
        names = group_names(conn)
    out = [class_entry(c, mk, now_dt) for c in cls]
    out += [item_entry(r, mk, names, now_dt) for r in own]
    out += [canvas_entry(x, mk, now_dt) for x in rows_canvas if lo_s <= str(x["due"])[:10] < hi_s]
    out += [mail_entry(x, mk, now_dt) for x in mail_items() if x.get("due") and lo_s <= str(x["due"])[:10] < hi_s]
    out += [app_entry(r, mk, names, now_dt) for r in apps if lo_s <= r["deadline"] < hi_s]
    seen = {e["id"] for e in out}
    out += [snapshot_entry(m) for m in mk.values() if m.get("done_at") and m.get("date") and lo_s <= m["date"] < hi_s
            and m["ref"] not in seen and m["ref"].split(":", 1)[0] in SNAP_ORIGIN]
    fill_training(out)
    mark_clashes(out)
    out.sort(key=order_key)
    for e in out:
        e["weekday"] = weekday(date.fromisoformat(e["date"]))
    with_projects(out)
    errors = {k: v for k, v in (("calendar", cal_err), ("canvas", canvas_err)) if v}
    return {"ok": True, "from": lo_s, "days": days, "timezone": settings.timezone, "calendar": sources.calendar_ics is not None,
            "events": out, "errors": errors}


# —— 要记得的 ————————————————————————————————————————————————————————

def group_of(e: dict, t: date) -> str | None:
    """security 置顶；过了的（3 天内）；明天；一周内；以后；没日期的要办 = nodate，没日期的钱 / 状态 = news。今天到期的在今天的时间线里，这里不放。"""
    if e["kind"] == "security":
        return "security"
    if not e["date"]:
        return "nodate" if e["kind"] in ("todo", "deadline") else "news"
    diff = (date.fromisoformat(e["date"]) - t).days
    if diff < 0:
        return "overdue" if diff >= -OVERDUE_DAYS else None
    if diff == 0:
        return None
    if diff == 1:
        return "tomorrow"
    return "week" if diff <= 7 else "later"


def build_remember(include_done: bool = False) -> dict:
    t, now_dt = today(), now()
    rows_canvas, canvas_err = canvas_rows()
    lo = (t - timedelta(days=OVERDUE_DAYS)).isoformat()
    with _lock, sdb() as conn:
        mk = all_marks(conn)
        apps = app_rows(conn)
        names = group_names(conn)
        own = conn.execute("SELECT * FROM schedule_items WHERE deleted_at IS NULL AND kind='deadline' AND date>=? AND date<=?",
                           (lo, (t + timedelta(days=REMEMBER_DAYS["own"])).isoformat())).fetchall()
    items = [canvas_entry(x, mk, now_dt) for x in rows_canvas]
    items += [mail_entry(x, mk, now_dt) for x in mail_items()]
    app_hi = (t + timedelta(days=REMEMBER_DAYS["apply"])).isoformat()
    items += [app_entry(r, mk, names, now_dt) for r in apps if lo <= r["deadline"] <= app_hi]
    items += [item_entry(r, mk, names, now_dt) for r in own]
    for e in items:
        e["group"] = group_of(e, t)
    items = [e for e in items if e["group"] and (include_done or not e["done"])]
    # 邮件里写了时间的（面试、咨询、活动）：和那天的课撞了就标出来
    timed = [e for e in items if e["origin"] == "mail" and e["start"] and e["kind"] != "money"]
    if timed:
        cls, _ = classes(t, 15)
        mk_cls = [class_entry(c, mk, now_dt) for c in cls]
        for e in timed:
            s = minutes(e["start"])
            for c in mk_cls:
                if c["date"] == e["date"] and c["start"] and not c["skip"] and not c["free"]:
                    cs = minutes(c["start"])
                    ce = minutes(c["end"]) if c["end"] else cs + 60
                    if s < ce and cs < s + 60:
                        e["clash"].append(c["title"])
    rank = {"security": 0, "overdue": 1, "tomorrow": 2, "week": 3, "later": 4, "nodate": 5, "news": 6}
    kind_rank = {"money": 0, "status": 1}
    items.sort(key=lambda e: (rank[e["group"]], kind_rank.get(e["kind"], 0) if e["group"] == "news" else 0,
                              not e["urgent"] if e["group"] == "nodate" else False, e["date"] or "", e["start"] or "",
                              str(e.get("firstSeen") or "") if not e["date"] else "", e["title"]))
    for e in items:
        if e["date"]:
            e["weekday"] = weekday(date.fromisoformat(e["date"]))
    with_projects(items)
    return {"ok": True, "today": t.isoformat(), "items": items, "errors": {"canvas": canvas_err} if canvas_err else {}}


def with_projects(entries: list[dict]) -> None:
    """属于某个项目的截止带上 project {id, title}，自己的截止小标写项目名（见 projects.py）。在锁外调。"""
    try:
        import projects  # 延迟导入：projects 依赖本模块
        projects.annotate(entries)
    except Exception:  # noqa: BLE001 — 标不上只是少一个小标
        pass


# —— 改动、日志、卡片 ——————————————————————————————————————————————————

def actor_of(source: str | None) -> str:
    s = (source or "leo").strip() or "leo"
    if not ID_RE.match(s):
        raise HTTPException(400, L("source 只能是 Agent 的 id", "source must be an agent id"))
    return s


def run_of(actor: str) -> chat.Run | None:
    """Agent 改的：它这会儿正在回复的那一次（改动卡挂在那条回复下面）。你在 app 里改的没有。"""
    if actor == "leo":
        return None
    live = [r for t, r in chat.RUNS.items() if not r.done and not t.startswith(("task:", "study-")) and chat.agent_of(t) == actor]
    return max(live, key=lambda r: r.t0) if live else None


def log_change(conn: sqlite3.Connection, actor: str, run: chat.Run | None, target: str, action: str, title: str, summary: str,
               before: dict | None, after: dict | None) -> int:
    cur = conn.execute("INSERT INTO schedule_log(at, actor, thread, target, action, title, summary, before, after) VALUES(?,?,?,?,?,?,?,?,?)",
                       (now_iso(), actor, run.thread if run else None, target, action, title[:120], summary[:200],
                        json.dumps(before, ensure_ascii=False) if before is not None else None,
                        json.dumps(after, ensure_ascii=False) if after is not None else None))
    return int(cur.lastrowid or 0)


AREA_REMEMBER = ("done", "undone", "edit")


def change_json(r: sqlite3.Row) -> dict:
    """对话里的日程卡（kind schedule）：改了什么、能撤销。"""
    return {"kind": "schedule", "id": f"sc-{r['id']}", "logId": r["id"], "thread": r["thread"], "messageId": r["message_id"],
            "createdAt": r["at"], "status": "undone" if r["undone_at"] else "done", "action": r["action"], "actor": r["actor"],
            "area": "remember" if r["action"] in AREA_REMEMBER else "schedule", "title": r["title"], "summary": r["summary"]}


def announce(log_id: int, run: chat.Run | None, actor: str) -> dict | None:
    """回复进行中改的：当场给那次回复发一张卡；Agent 改的记一行活动。"""
    with _lock, sdb() as conn:
        row = conn.execute("SELECT * FROM schedule_log WHERE id=?", (log_id,)).fetchone()
        names = group_names(conn)
    if row is None:
        return None
    card = change_json(row)
    if run is not None and not run.done:
        import cards  # 延迟导入：cards 在 /api/chat/cards 里也要用本模块
        cards.publish(run, card)
    if actor != "leo":
        chat.log_activity(L(f"改了日程：{row['title']} · {row['summary']}", f"Changed the schedule: {row['title']} · {row['summary']}"),
                          "schedule", who_name(actor, names) or actor)
    return card


def changes_for(thread: str, lo: str, hi: str) -> list[dict]:
    """这个线程这一天（lo–hi）里 Agent 在回复中改的日程（给 /api/chat/cards）。"""
    with _lock, sdb() as conn:
        rows = conn.execute("SELECT * FROM schedule_log WHERE thread=? AND at>=? AND at<? ORDER BY id", (thread, lo, hi)).fetchall()
    return [change_json(r) for r in rows]


def link_run(run: chat.Run) -> None:
    """一次回复结束：这次回复里这个线程改的日程挂到这条回复下面。"""
    if run.reply_id is None:
        return
    with _lock, sdb() as conn:
        conn.execute("UPDATE schedule_log SET message_id=? WHERE thread=? AND message_id IS NULL AND at>=?", (run.reply_id, run.thread, run.started))


def item_by_id(conn: sqlite3.Connection, iid: str) -> sqlite3.Row:
    iid = iid.removeprefix("item:")
    r = conn.execute("SELECT * FROM schedule_items WHERE id=?", (iid,)).fetchone()
    if r is None or r["deleted_at"]:
        raise HTTPException(404, L("没有这一条日程", "No such schedule item"))
    return r


def write_item(conn: sqlite3.Connection, state: dict) -> None:
    cols = ", ".join(ITEM_COLS)
    conn.execute(f"INSERT OR REPLACE INTO schedule_items({cols}) VALUES({', '.join('?' * len(ITEM_COLS))})",  # noqa: S608 — 列名是常量
                 tuple(state.get(c) for c in ITEM_COLS))


def write_mark(conn: sqlite3.Connection, ref: str, state: dict | None) -> None:
    if state is None:
        conn.execute("DELETE FROM schedule_marks WHERE ref=?", (ref,))
        return
    cols = ", ".join(MARK_COLS)
    conn.execute(f"INSERT OR REPLACE INTO schedule_marks({cols}) VALUES({', '.join('?' * len(MARK_COLS))})",  # noqa: S608
                 tuple(state.get(c) if c != "ref" else ref for c in MARK_COLS))


def item_summary(before: dict, after: dict) -> tuple[str, str]:
    """(action, 一行摘要)：挪时间 / 改名 / 改地点 / 记实际 / 改备注。"""
    if (before["date"], before["start"], before["end"]) != (after["date"], after["start"], after["end"]):
        if before["date"] == after["date"]:
            a, b = before["start"] or L("全天", "all day"), after["start"] or L("全天", "all day")
            return "move", f"{a} → {b}" if a != b else f"{span(None, after['start'], after['end'])}"
        return "move", f"{span(before['date'], before['start'])} → {span(after['date'], after['start'])}"
    if before["title"] != after["title"]:
        return "update", L(f"改名 · {before['title']} → {after['title']}", f"Renamed · {before['title']} → {after['title']}")
    if (before["attended"], before["actual_start"], before["actual_end"]) != (after["attended"], after["actual_start"], after["actual_end"]):
        if after["attended"] == 0:
            return "attend", L("没做", "Didn't happen")
        actual = f"{after['actual_start']}–{after['actual_end']}" if after["actual_start"] else ""
        return "attend", L(f"做了{(' · 实际 ' + actual) if actual else ''}", f"Done{(' · actually ' + actual) if actual else ''}")
    if before["location"] != after["location"]:
        return "update", L(f"地点 · {after['location'] or '去掉了'}", f"Location · {after['location'] or 'removed'}")
    if before["kind"] != after["kind"]:
        return "update", L("改成截止" if after["kind"] == "deadline" else "改成普通日程", "Now a deadline" if after["kind"] == "deadline" else "Now an event")
    return "update", L(f"备注 · {after['note'] or '去掉了'}", f"Note · {after['note'] or 'removed'}")


# —— 接口：时间线、自己的日程 —————————————————————————————————————————————

@router.get("/api/schedule")
async def get_schedule(days: int = 1, from_: str | None = Query(None, alias="from")):
    """合并后的时间线：课表 + 自己的 + 到期那天的截止（带勾）+ 这一层的标记。from 不给 = 今天。"""
    if not 1 <= days <= 14:
        raise HTTPException(400, L("days 取 1 到 14", "days must be between 1 and 14"))
    lo = date.fromisoformat(check_date(from_, "from")) if from_ else today()
    return await asyncio.to_thread(build_timeline, lo, days)


class ItemIn(BaseModel):
    title: str
    date: str
    start: str | None = None
    end: str | None = None
    kind: str = "event"
    location: str = ""
    note: str = ""
    key: str | None = None
    source: str | None = None


def clean_item(title: str | None, kind: str | None) -> tuple[str | None, str | None]:
    if title is not None:
        title = " ".join(title.split())[:120]
        if not title:
            raise HTTPException(400, L("标题不能空", "Title can't be empty"))
    if kind is not None and kind not in ("event", "deadline"):
        raise HTTPException(400, L("kind 只能是 event 或 deadline", "kind must be event or deadline"))
    return title, kind


@router.post("/api/schedule")
async def add_item(body: ItemIn):
    """加一条。带 key 且已经有（没删的）= 改那一条（Agent 重排训练时用）；用户在 app 里挪过时间的，Agent 重排不改时间（kept 里写 time）。"""
    actor = actor_of(body.source)
    title, kind = clean_item(body.title, body.kind)
    d, s, e = check_date(body.date), check_time(body.start), check_time(body.end, "end")
    if e and not s:
        raise HTTPException(400, L("有结束时间就要有开始时间", "An end time needs a start time"))
    if s and e and minutes(e) <= minutes(s):
        raise HTTPException(400, L("结束要晚于开始", "End must be after start"))
    key = (body.key or "").strip()[:120] or None
    run = run_of(actor)
    ts = now_iso()
    with _lock, sdb() as conn:
        old = conn.execute("SELECT * FROM schedule_items WHERE key=? AND deleted_at IS NULL", (key,)).fetchone() if key else None
        kept: list[str] = []
        if old:
            before = dict(old)
            after = {**before, "title": title, "kind": kind, "date": d, "start": s, "end": e, "location": body.location.strip()[:200],
                     "note": body.note.strip()[:500], "updated_at": ts}
            # 用户自己挪过的时间，Agent 重排时不覆盖（改名字、备注照改）
            if actor != "leo" and (before["date"], before["start"], before["end"]) != (d, s, e) and conn.execute(
                    "SELECT 1 FROM schedule_log WHERE target=? AND actor='leo' AND action='move' AND undone_at IS NULL LIMIT 1",
                    (f"item:{old['id']}",)).fetchone():
                after.update(date=before["date"], start=before["start"], end=before["end"])
                kept.append("time")
            if {k: after[k] for k in ITEM_COLS if k != "updated_at"} == {k: before[k] for k in ITEM_COLS if k != "updated_at"}:
                return {"ok": True, "id": f"item:{old['id']}", "changed": False, "kept": kept}
            write_item(conn, after)
            action, summary = item_summary(before, after)
            log = log_change(conn, actor, run, f"item:{old['id']}", action, title or "", summary, before, after)
            iid = old["id"]
        else:
            iid = f"ev-{uuid.uuid4().hex[:8]}"
            after = {"id": iid, "kind": kind, "title": title, "date": d, "start": s, "end": e, "location": body.location.strip()[:200],
                     "note": body.note.strip()[:500], "source": actor, "key": key, "attended": None, "actual_start": None,
                     "actual_end": None, "created_at": ts, "updated_at": ts, "deleted_at": None}
            write_item(conn, after)
            summary = L(f"加了截止 · {span(d, s)}", f"Added a deadline · {span(d, s)}") if kind == "deadline" else \
                L(f"加了 · {span(d, s, e)}", f"Added · {span(d, s, e)}")
            log = log_change(conn, actor, run, f"item:{iid}", "add", title or "", summary, None, after)
    card = announce(log, run, actor)
    return {"ok": True, "id": f"item:{iid}", "changed": True, "card": card, "kept": kept}


class ItemPatch(BaseModel):
    title: str | None = None
    date: str | None = None
    start: str | None = None
    end: str | None = None
    kind: str | None = None
    location: str | None = None
    note: str | None = None
    attended: bool | None = None
    actualStart: str | None = None
    actualEnd: str | None = None
    source: str | None = None


@router.patch("/api/schedule/{iid}")
async def patch_item(iid: str, body: ItemPatch):
    """改自己的日程。给了哪个字段改哪个；start 给空字符串 = 改成全天；attended 给 null = 清掉「做没做」。"""
    actor = actor_of(body.source)
    sent = body.model_fields_set
    title, kind = clean_item(body.title, body.kind)
    run = run_of(actor)
    with _lock, sdb() as conn:
        before = dict(item_by_id(conn, iid))
        after = dict(before)
        if title is not None:
            after["title"] = title
        if kind is not None:
            after["kind"] = kind
        if body.date is not None:
            after["date"] = check_date(body.date)
        if "start" in sent:
            after["start"] = check_time(body.start)
            if not after["start"]:
                after["end"] = None
        if "end" in sent:
            after["end"] = check_time(body.end, "end")
        if body.location is not None:
            after["location"] = body.location.strip()[:200]
        if body.note is not None:
            after["note"] = body.note.strip()[:500]
        if "attended" in sent:
            after["attended"] = None if body.attended is None else int(body.attended)
        if "actualStart" in sent:
            after["actual_start"] = check_time(body.actualStart, "actualStart")
        if "actualEnd" in sent:
            after["actual_end"] = check_time(body.actualEnd, "actualEnd")
        if after["end"] and not after["start"]:
            raise HTTPException(400, L("有结束时间就要有开始时间", "An end time needs a start time"))
        if after["start"] and after["end"] and minutes(after["end"]) <= minutes(after["start"]):
            raise HTTPException(400, L("结束要晚于开始", "End must be after start"))
        if after == before:
            return {"ok": True, "id": f"item:{before['id']}", "changed": False}
        after["updated_at"] = now_iso()
        write_item(conn, after)
        action, summary = item_summary(before, after)
        log = log_change(conn, actor, run, f"item:{before['id']}", action, after["title"], summary, before, after)
    card = announce(log, run, actor)
    return {"ok": True, "id": f"item:{before['id']}", "changed": True, "card": card}


@router.delete("/api/schedule/{iid}")
async def delete_item(iid: str, source: str | None = None):
    actor = actor_of(source)
    run = run_of(actor)
    with _lock, sdb() as conn:
        before = dict(item_by_id(conn, iid))
        after = {**before, "deleted_at": now_iso()}
        write_item(conn, after)
        log = log_change(conn, actor, run, f"item:{before['id']}", "delete", before["title"],
                         L(f"删了 · {span(before['date'], before['start'])}", f"Deleted · {span(before['date'], before['start'])}"), before, after)
    card = announce(log, run, actor)
    return {"ok": True, "card": card}


# —— 接口：课表那一节的标记 ————————————————————————————————————————————————

class MarkIn(BaseModel):
    ref: str
    skip: bool | None = None
    series: bool = False
    location: str | None = None
    note: str | None = None
    attended: bool | None = None
    actualStart: str | None = None
    actualEnd: str | None = None
    source: str | None = None


def parse_ics_ref(ref: str) -> tuple[str, str | None, str]:
    """ics:<日期>T<时刻>|<标题> → (日期, 时刻, 标题)。"""
    m = re.match(r"^ics:(\d{4}-\d{2}-\d{2})(?:T(\d{2}:\d{2}))?\|(.*)$", ref, re.S)
    if not m:
        raise HTTPException(400, L("ref 要是课表里的一节（ics:…），从 /api/schedule 的 id 拿", "ref must be a calendar entry (ics:…) from /api/schedule"))
    return m.group(1), m.group(2), m.group(3)


@router.post("/api/schedule/mark")
async def mark(body: MarkIn):
    """课表里那一节：不去 / 照去（series=true 管每周同一节）、改地点、备注、去没去、实际时间。课表本身不动。"""
    actor = actor_of(body.source)
    sent = body.model_fields_set
    d, t, title = parse_ics_ref(body.ref)
    run = run_of(actor)
    changes: list[tuple[str, str, str, dict | None, dict | None]] = []   # (ref, action, summary, before, after)
    ts = now_iso()
    with _lock, sdb() as conn:
        def upsert(ref: str, fields: dict, action: str, summary: str) -> None:
            old = conn.execute("SELECT * FROM schedule_marks WHERE ref=?", (ref,)).fetchone()
            before = row_dict(old)
            after = {**(before or {c: None for c in MARK_COLS}), **fields, "ref": ref, "title": title, "date": d if ref.startswith("ics:") else None,
                     "time": t, "source": actor, "updated_at": ts}
            if before and {k: after.get(k) for k in MARK_COLS if k not in ("updated_at", "source")} == \
                    {k: before.get(k) for k in MARK_COLS if k not in ("updated_at", "source")}:
                return
            write_mark(conn, ref, after)
            changes.append((ref, action, summary, before, after))

        if "skip" in sent and body.skip is not None:
            if body.series:
                if not t:
                    raise HTTPException(400, L("全天的不能按每周设", "All-day entries can't repeat weekly"))
                series_ref = f"icss:{date.fromisoformat(d).weekday()}|{t}|{title}"
                upsert(series_ref, {"skip": 1 if body.skip else None}, "skip" if body.skip else "unskip",
                       L("每周这节都不去", "Skipping this class every week") if body.skip else L("每周照常去", "Going every week again"))
                occ = conn.execute("SELECT skip FROM schedule_marks WHERE ref=?", (body.ref,)).fetchone()
                if occ is not None and occ["skip"] is not None:  # 这一次单独标过的，让给每周的设置
                    upsert(body.ref, {"skip": None}, "skip" if body.skip else "unskip", "")
            else:
                upsert(body.ref, {"skip": 1 if body.skip else 0}, "skip" if body.skip else "unskip",
                       L("这节标了不去", "Skipping this one") if body.skip else L("照常去", "Going after all"))
        fields: dict = {}
        if body.location is not None:
            fields["location"] = body.location.strip()[:200] or None
        if body.note is not None:
            fields["note"] = body.note.strip()[:500] or None
        if "attended" in sent:
            fields["attended"] = None if body.attended is None else int(body.attended)
        if "actualStart" in sent:
            fields["actual_start"] = check_time(body.actualStart, "actualStart")
        if "actualEnd" in sent:
            fields["actual_end"] = check_time(body.actualEnd, "actualEnd")
        if fields:
            if "attended" in fields:
                action = "attend"
                summary = L("去了", "Went") if fields["attended"] == 1 else L("没去", "Didn't go") if fields["attended"] == 0 else L("清掉了去没去", "Cleared")
            elif "location" in fields:
                action, summary = "update", L(f"地点 · {fields['location'] or '改回课表的'}", f"Location · {fields['location'] or 'back to the timetable'}")
            else:
                action, summary = "update", L(f"备注 · {fields.get('note') or '去掉了'}", f"Note · {fields.get('note') or 'removed'}")
            upsert(body.ref, fields, action, summary)
        logs = [log_change(conn, actor, run, f"mark:{ref}", action, title, summary, before, after)
                for ref, action, summary, before, after in changes if summary]
    cards_out = [announce(x, run, actor) for x in logs]
    return {"ok": True, "changed": bool(changes), "cards": cards_out}


# —— 接口：要记得的 ——————————————————————————————————————————————————————

@router.get("/api/remember")
async def remember(all: int = 0):  # noqa: A002 — 查询参数名
    """要记得的：group = security / overdue / tomorrow / week / later / nodate / news（app 分组、写日子）。all=1 连打过勾的也给。"""
    return await asyncio.to_thread(build_remember, bool(all))


class DoneIn(BaseModel):
    ref: str
    done: bool = True
    title: str | None = None
    date: str | None = None
    time: str | None = None
    source: str | None = None


def snapshot_of(ref: str) -> dict:
    """打勾时存的快照（标题、日期、时刻）：从现在的来源里找。"""
    for e in build_remember(include_done=True)["items"]:
        if e["id"] == ref:
            return {"title": e["title"], "date": e["date"], "time": e["start"] or None}
    t = build_timeline(today(), 1)["events"]
    for e in t:
        if e["id"] == ref:
            return {"title": e["title"], "date": e["date"], "time": e["start"] or None}
    return {}


@router.post("/api/remember/done")
async def remember_done(body: DoneIn):
    """打勾（done=false 取消）。邮件条目同时写回来源；课程 ddl、求职、自己加的只在这一层记。"""
    actor = actor_of(body.source)
    ref = body.ref.strip()
    if ref.split(":", 1)[0] not in SNAP_ORIGIN:
        raise HTTPException(400, L("只有要记得的条目能打勾（canvas: / mail: / app: / item:）", "Only reminder entries can be ticked (canvas: / mail: / app: / item:)"))
    snap = {"title": body.title, "date": check_date(body.date) if body.date else None, "time": check_time(body.time, "time")}
    if not snap["title"]:
        snap = await asyncio.to_thread(snapshot_of, ref) or snap
    title = snap.get("title") or ref
    run = run_of(actor)
    with _lock, sdb() as conn:
        before = row_dict(conn.execute("SELECT * FROM schedule_marks WHERE ref=?", (ref,)).fetchone())
        was = bool(before and before.get("done_at"))
        if was == body.done:
            unchanged = True
        else:
            unchanged = False
        if not unchanged:
            after = {**(before or {c: None for c in MARK_COLS}), "ref": ref, "done_at": now_iso() if body.done else None,
                     "title": title, "date": snap.get("date"), "time": snap.get("time"), "source": actor, "updated_at": now_iso()}
            write_mark(conn, ref, after)
            log = log_change(conn, actor, run, f"mark:{ref}", "done" if body.done else "undone", title,
                             L("勾掉了", "Ticked off") if body.done else L("放回要记得的", "Back on the list"), before, after)
    if ref.startswith("mail:"):  # 邮件条目的来源也记一笔（已经一致的再写一次也无妨）
        ok, msg = await asyncio.to_thread(mail_cmd, "--done" if body.done else "--undo", ref[5:])
        if not ok:
            print(f"[schedule] 邮件条目写回失败：{msg}")
    if unchanged:
        return {"ok": True, "changed": False}
    card = announce(log, run, actor)
    return {"ok": True, "changed": True, "card": card}


class EditIn(BaseModel):
    ref: str
    title: str | None = None
    due: str | None = None
    detail: str | None = None
    type: str | None = None
    source: str | None = None


DUE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}( \d{2}:\d{2})?$")


@router.post("/api/remember/edit")
async def remember_edit(body: EditIn):
    """改一条要记得的（抽错了，main 在对话里改）：邮件条目写回来源（remember.mail.cmd --edit）；自己加的截止直接改。课程和求职的源头改不了。"""
    actor = actor_of(body.source)
    ref = body.ref.strip()
    sent = body.model_fields_set
    if ref.startswith("item:"):
        kw: dict = {"source": body.source, "title": body.title}
        if body.due:
            if not DUE_RE.match(body.due.strip()):
                raise HTTPException(400, L("due 要写成 YYYY-MM-DD 或 YYYY-MM-DD HH:MM", "due must be YYYY-MM-DD or YYYY-MM-DD HH:MM"))
            kw.update(date=body.due.strip()[:10], start=body.due.strip()[11:16] or "")
        if body.detail is not None:
            kw["note"] = body.detail
        return await patch_item(ref, ItemPatch(**kw))
    if not ref.startswith("mail:"):
        raise HTTPException(400, L("课程 ddl 和求职申请的源头改不了；要改在 Canvas / 求职看板里改", "Course and application deadlines can't be edited here"))
    if body.type is not None and body.type not in MAIL_KINDS:
        raise HTTPException(400, L("type 只能是 todo / money / status / security", "type must be todo / money / status / security"))
    if "due" in sent and body.due and not DUE_RE.match(body.due.strip()):
        raise HTTPException(400, L("due 要写成 YYYY-MM-DD 或 YYYY-MM-DD HH:MM", "due must be YYYY-MM-DD or YYYY-MM-DD HH:MM"))
    mid = ref[5:]
    old = next((x for x in mail_items() if str(x.get("id")) == mid), None)
    if old is None:
        raise HTTPException(404, L("没有这一条（可能已经过期清掉了）", "No such entry (it may have expired)"))
    change = {k: (v.strip() if isinstance(v, str) else v) for k, v in
              (("title", body.title), ("due", body.due), ("detail", body.detail), ("type", body.type)) if k in sent}
    if "due" in change and not change["due"]:
        change["due"] = None
    before = {k: old.get(k) for k in change}
    if before == change:
        return {"ok": True, "changed": False}
    ok, msg = await asyncio.to_thread(mail_cmd, "--edit", mid, "--json", json.dumps(change, ensure_ascii=False))
    if not ok:
        raise HTTPException(502, L(f"没改成：{msg}", f"Couldn't edit: {msg}"))
    title = change.get("title") or old.get("title") or mid
    if "due" in change:
        def when(v: str | None) -> str:
            return span(v[:10], v[11:16] or None) if v else L("没日子", "no date")
        summary = f"{when(before.get('due'))} → {when(change['due'])}"
    elif "title" in change:
        summary = L(f"改名 · {before.get('title')} → {change['title']}", f"Renamed · {before.get('title')} → {change['title']}")
    else:
        summary = L("改了内容", "Edited")
    run = run_of(actor)
    with _lock, sdb() as conn:
        log = log_change(conn, actor, run, f"mailedit:{mid}", "edit", title, summary, before, change)
    card = announce(log, run, actor)
    return {"ok": True, "changed": True, "card": card}


def ref_context(ref: str) -> str | None:
    """对话里引用了「要记得的」/ 日程里的一条（app 上点「不对？跟它说」）：只给模型看的前情，说清是哪一条、怎么改。"""
    ref = (ref or "").strip()[:400]
    hit = next((e for e in build_remember(include_done=True)["items"] if e["id"] == ref), None)
    if hit is None and ref.startswith(("item:", "ics:")):
        try:
            d = ref.split(":", 1)[1][:10] if ref.startswith("ics:") else None
            lo = date.fromisoformat(d) if d else today()
            hit = next((e for e in build_timeline(lo, 1)["events"] if e["id"] == ref), None)
        except ValueError:
            hit = None
    if hit is None:
        return None
    due = f"{hit['date']} {hit['start']}".strip() if hit.get("date") else L("没日子", "no date")
    return L(f"【要记得的】用户说的是这一条：「{hit['title']}」（{due}，来源 {hit['badge'] or hit['origin']}，id={ref}）。"
             f"抽错了就用 schedule_ctl.py edit / done 改（见 calendar skill），改完一句话说清改了什么。",
             f"[To remember] The user means this entry: \"{hit['title']}\" ({due}, from {hit['badge'] or hit['origin']}, id={ref}). "
             "If it's wrong, fix it with schedule_ctl.py edit / done (see the calendar skill) and say in one line what you changed.")


# —— 接口：撤销 ——————————————————————————————————————————————————————————

class UndoIn(BaseModel):
    redo: bool = False


def restore(conn: sqlite3.Connection, target: str, state: dict | None) -> tuple[str, dict | None]:
    """把 target 恢复成 state。返回要在锁外做的事（邮件来源的写回）。"""
    kind, _, key = target.partition(":")
    if kind == "item":
        if state is None:
            cur = conn.execute("SELECT * FROM schedule_items WHERE id=?", (key,)).fetchone()
            if cur is not None:
                write_item(conn, {**dict(cur), "deleted_at": now_iso()})
        else:
            write_item(conn, {**state, "updated_at": now_iso()})
        return "", None
    if kind == "mark":
        cur = row_dict(conn.execute("SELECT * FROM schedule_marks WHERE ref=?", (key,)).fetchone())
        write_mark(conn, key, state)
        if key.startswith("mail:") and bool((cur or {}).get("done_at")) != bool((state or {}).get("done_at")):
            return "done" if (state or {}).get("done_at") else "undo", {"id": key[5:]}
        return "", None
    if kind == "mailedit":
        return "edit", {"id": key, "fields": state or {}}
    raise HTTPException(400, L("这条改动撤销不了", "This change can't be undone"))


@router.post("/api/schedule/undo/{log_id}")
async def undo(log_id: int, body: UndoIn | None = None):
    """撤销一次改动（redo=true 再做回来）。对话里日程卡的「撤销」和 app 里打勾后的「撤销」都走这里。"""
    redo = bool(body and body.redo)
    with _lock, sdb() as conn:
        row = conn.execute("SELECT * FROM schedule_log WHERE id=?", (log_id,)).fetchone()
        if row is None:
            raise HTTPException(404, L("没有这条改动", "No such change"))
        if bool(row["undone_at"]) != redo:
            raise HTTPException(409, L("已经撤销过了" if not redo else "没撤销过", "Already undone" if not redo else "Not undone"))
        raw_state = row["after"] if redo else row["before"]
        state = json.loads(raw_state) if raw_state else None
        if row["target"].startswith("mailedit:") and not redo:
            state = {k: v for k, v in (state or {}).items()}
        todo, arg = restore(conn, row["target"], state)
        conn.execute("UPDATE schedule_log SET undone_at=? WHERE id=?", (None if redo else now_iso(), log_id))
        row = conn.execute("SELECT * FROM schedule_log WHERE id=?", (log_id,)).fetchone()
    if todo == "done" or todo == "undo":
        await asyncio.to_thread(mail_cmd, "--done" if todo == "done" else "--undo", arg["id"])
    elif todo == "edit":
        ok, msg = await asyncio.to_thread(mail_cmd, "--edit", arg["id"], "--json", json.dumps(arg["fields"], ensure_ascii=False))
        if not ok:
            raise HTTPException(502, L(f"邮件条目没改回去：{msg}", f"Couldn't restore the mail entry: {msg}"))
    return {"ok": True, "card": change_json(row)}


@router.get("/api/schedule/log")
async def get_log(limit: int = 30):
    """最近的改动（谁、改了什么），新的在前。"""
    with _lock, sdb() as conn:
        rows = conn.execute("SELECT * FROM schedule_log ORDER BY id DESC LIMIT ?", (max(1, min(limit, 200)),)).fetchall()
    return {"ok": True, "changes": [change_json(r) for r in rows]}


# —— iPhone 日历订阅 ————————————————————————————————————————————————————————

FEED_KEY = "schedule_feed"
FEED_SEEN_KEY = "schedule_feed_seen"  # 日历上次来取的时间和是哪种日历（「我 → 连接」看订阅通不通，见 connectors.py）
FEED_DEFAULT = {"classes": False, "mine": True, "deadlines": True, "mail": True}


def feed_client(ua: str) -> str:
    """来取订阅的是哪种日历（按 User-Agent 粗分，只存这个词）：ios / mac / google / outlook / other。"""
    u = ua.lower()
    if "iphone" in u or "ipad" in u or "ios/" in u or "dataaccessd" in u:
        return "ios"
    if "macos" in u or "mac os" in u or "calendaragent" in u:
        return "mac"
    if "google" in u:
        return "google"
    if "microsoft" in u or "outlook" in u:
        return "outlook"
    return "other"


def note_pickup(client: str) -> None:
    with _lock, sdb() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("INSERT OR REPLACE INTO settings(key, value) VALUES(?, ?)", (FEED_SEEN_KEY, json.dumps({"at": now_iso(), "client": client})))


def feed_settings(create: bool = True) -> dict | None:
    with _lock, sdb() as conn:
        r = conn.execute("SELECT value FROM settings WHERE key=?", (FEED_KEY,)).fetchone()
        try:
            cur = json.loads(r["value"]) if r else None
        except ValueError:
            cur = None
        if cur is None and create:
            cur = {"token": secrets.token_urlsafe(18), "include": dict(FEED_DEFAULT)}
            conn.execute("INSERT OR REPLACE INTO settings(key, value) VALUES(?, ?)", (FEED_KEY, json.dumps(cur)))
    return cur


def save_feed(cur: dict) -> None:
    with _lock, sdb() as conn:
        conn.execute("INSERT OR REPLACE INTO settings(key, value) VALUES(?, ?)", (FEED_KEY, json.dumps(cur)))


def feed_json(cur: dict) -> dict:
    return {"ok": True, "path": f"/cal/{cur['token']}.ics", "include": {**FEED_DEFAULT, **(cur.get("include") or {})}, "name": settings.app_name}


@router.get("/api/schedule/feed")
async def get_feed():
    return feed_json(await asyncio.to_thread(feed_settings))


class FeedIn(BaseModel):
    include: dict[str, bool] | None = None
    rotate: bool = False


@router.post("/api/schedule/feed")
async def set_feed(body: FeedIn):
    """四类开关（classes / mine / deadlines / mail）；rotate=true 换一个链接，旧的马上失效。"""
    cur = await asyncio.to_thread(feed_settings)
    if body.include:
        cur["include"] = {**FEED_DEFAULT, **(cur.get("include") or {}), **{k: bool(v) for k, v in body.include.items() if k in FEED_DEFAULT}}
    if body.rotate:
        cur["token"] = secrets.token_urlsafe(18)
    await asyncio.to_thread(save_feed, cur)
    return feed_json(cur)


def ics_text(s: str) -> str:
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r", "").replace("\n", "\\n")


def fold(line: str) -> str:
    """RFC 5545：一行最多 75 个字节，多的折到下一行（前面空一格），不拆开一个字。"""
    out, cur = [], b""
    for ch in line:
        b = ch.encode("utf8")
        if len(cur) + len(b) > (75 if not out else 74):
            out.append(cur.decode("utf8"))
            cur = b""
        cur += b
    out.append(cur.decode("utf8"))
    return "\r\n ".join(out)


def build_ics(include: dict) -> str:
    t = today()
    lo, days = t - timedelta(days=FEED_DAYS[0]), FEED_DAYS[0] + FEED_DAYS[1]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    events: list[dict] = []
    if include.get("classes"):
        cls, _ = classes(lo, days)
        with _lock, sdb() as conn:
            mk = all_marks(conn)
        events += [e for e in (class_entry(c, mk, now()) for c in cls) if not e["skip"]]
    with _lock, sdb() as conn:
        mk = all_marks(conn)
        names = group_names(conn)
        own = conn.execute("SELECT * FROM schedule_items WHERE deleted_at IS NULL AND date>=? AND date<?",
                           (lo.isoformat(), (lo + timedelta(days=days)).isoformat())).fetchall()
        apps = app_rows(conn) if include.get("deadlines") else []
    if include.get("mine"):
        events += [item_entry(r, mk, names, now()) for r in own if r["kind"] == "event"]
    if include.get("deadlines"):
        rows_canvas, _ = canvas_rows()
        events += [canvas_entry(x, mk, now()) for x in rows_canvas]
        events += [app_entry(r, mk, names, now()) for r in apps if r["deadline"] <= (t + timedelta(days=FEED_DAYS[1])).isoformat()]
        events += [item_entry(r, mk, names, now()) for r in own if r["kind"] == "deadline"]
    if include.get("mail"):
        events += [mail_entry(x, mk, now()) for x in mail_items() if x.get("due") and x.get("type") in ("todo", "status")]
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//OpenMousse//Schedule//ZH", "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
             f"X-WR-CALNAME:{ics_text(settings.app_name)}", f"X-WR-TIMEZONE:{settings.timezone}",
             "REFRESH-INTERVAL;VALUE=DURATION:PT30M", "X-PUBLISHED-TTL:PT30M"]
    for e in events:
        if e["done"] or not e["date"]:
            continue
        uid = hashlib.sha1(e["id"].encode("utf8")).hexdigest()[:20] + "@openmousse"
        title = e["title"]
        if e["kind"] == "deadline" or e["origin"] in ("canvas", "apply"):
            title = L(f"截止 · {title}", f"Due · {title}")
        lines += ["BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{stamp}", fold(f"SUMMARY:{ics_text(title)}")]
        if e["allDay"] or not e["start"]:
            d = date.fromisoformat(e["date"])
            lines += [f"DTSTART;VALUE=DATE:{d:%Y%m%d}", f"DTEND;VALUE=DATE:{d + timedelta(days=1):%Y%m%d}"]
        else:
            s = at(e["date"], e["start"])
            en = at(e["date"], e["end"]) if e["end"] and minutes(e["end"]) > minutes(e["start"]) else s + timedelta(
                minutes=15 if e["kind"] == "deadline" or e["origin"] in ("canvas", "apply") else 60)
            lines += [f"DTSTART:{s.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}", f"DTEND:{en.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}"]
        if e["location"]:
            lines.append(fold(f"LOCATION:{ics_text(e['location'])}"))
        desc = "\n".join(x for x in (e.get("note"), e.get("detail"), e.get("badge")) if x)
        if desc:
            lines.append(fold(f"DESCRIPTION:{ics_text(desc)}"))
        if e.get("link"):
            lines.append(fold(f"URL:{e['link']}"))
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


@router.get("/cal/{token}.ics")
async def ics_feed(token: str, request: Request):
    """iPhone 日历订阅（只读）。不在 /api 下、不要认证：链接里的令牌就是密码，app 里能换。每次来取记下时间和是哪种日历。"""
    cur = await asyncio.to_thread(feed_settings, False)
    if not cur or not secrets.compare_digest(str(cur.get("token") or ""), token):
        raise HTTPException(404, "Not found")
    text = await asyncio.to_thread(build_ics, {**FEED_DEFAULT, **(cur.get("include") or {})})
    await asyncio.to_thread(note_pickup, feed_client(request.headers.get("user-agent") or ""))
    return Response(text, media_type="text/calendar; charset=utf-8", headers={"Cache-Control": "no-store"})
