"""Agent 的产出（2026-10-13，界面重做第 6 块）：新产出的浮动标签、按 kind 套模板的小板子、对话里的产出卡片。

产出 = 建议流里的一张卡（grava.db feed_items：kind + data，Agent 经 workspace 的 grava_feed.py 写，见 data.py）。
- 新产出 = 还没看过的卡：seen_at 为空、没被划掉、这个逻辑日（04:00 起）出的。GET /api/outputs 最新在前，每份带小板子要画的数据（board）。
  app 把它们放在标签栏上方的浮动标签里；点开小板子、或下滑关掉浮动标签，就 POST /api/feed/seen（data.py）标成看过，哪台设备上看过都算。
- 小板子（board）按 kind 套模板，要画的数都在这里算好、按请求的语言写好，app 只画（和看板积木一样）：
  training_review 练后报告 / meal_plan 饮食建议 / sleep_report 睡眠报告 / training_plan 训练计划；别的 kind 是 text（标题 + 正文）。
  练后报告的 data（session / minutes / sets / last / main / moves / skipped / verdict / next_24h / next）格式见 review_board，Agent 照着写；
  作者的实例里健身 Agent 用 workspace 的 scripts/training_review.py 从训练记录算好本次和上次。没有 data 的老卡按正文的小标题拆。
  饮食建议的蛋白质目标：data.targets.protein → server.json 的 nutrition_targets → 饮食看板那天的目标（/api/diet/day 的 targets；最多等 3 秒，读到了留 30 分钟）。
  睡眠报告的近 7 天恢复分读健康数据源（apple_health.recovery_series，留 5 分钟）；报告写的时候健康数据没同步、现在同步了，带上 updated。
- 对话里的产出卡片：Agent 在哪一次回复里写的卡，就挂在那条回复下面（feed_items.thread / message_id；回复结束时 cards.on_run_end → link_run，
  看的是开跑以后新写的行，和 push.new_card 认卡的办法一样）。GET /api/chat/cards 里是 kind output。加列之前的老卡按时间补挂一次（backfill）。
  同一天同 kind 的新卡会把旧卡划掉（dismissed）：旧卡还留在对话里，标 replaced。
- GET /api/outputs/{id}：一份产出（对话卡片、今天页的报告点开用；看过的、被替换的也给）。GET /api/outputs?all=1[&day=]：那个逻辑日的全部产出。
"""
from __future__ import annotations

import concurrent.futures
import json
import re
import sqlite3
import time
from datetime import date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException

import chat
import sources
from chat import TZ, _lock, now_iso
from config import raw, settings
from data import ddb
from i18n import L

router = APIRouter()
RETIRED = ("reminder", "mail_digest")  # 「要记得的」接替了的老卡（今天页也不显示），不算产出
LIST_MAX = 10
SLEEP_TARGET = (420, 540)  # 睡眠时长目标（分钟），server.json outputs.sleep_target_min 可改
MONTHS_EN = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_migrated = False


def odb() -> sqlite3.Connection:
    """应用数据库；feed_items 第一次用时补两列（挂在哪个线程的哪条回复下），并把老卡按时间补挂一次。"""
    global _migrated
    conn = ddb()
    if not _migrated:
        have = {r[1] for r in conn.execute("PRAGMA table_info(feed_items)")}
        if "thread" not in have:
            conn.execute("ALTER TABLE feed_items ADD COLUMN thread TEXT")
        if "message_id" not in have:
            conn.execute("ALTER TABLE feed_items ADD COLUMN message_id INTEGER")
        conn.execute("CREATE INDEX IF NOT EXISTS feed_items_thread ON feed_items(thread, created_at)")
        if not conn.execute("SELECT 1 FROM settings WHERE key='outputs.backfill'").fetchone():
            backfill(conn)
            conn.execute("INSERT OR REPLACE INTO settings(key, value) VALUES('outputs.backfill', ?)", (now_iso(),))
        _migrated = True
    return conn


def _dt(ts: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(ts).astimezone(TZ) if ts else None
    except ValueError:
        return None


def backfill(conn: sqlite3.Connection) -> int:
    """加列之前的卡：那个 Agent 的线程里，卡写下之后 15 分钟内的第一条回复、且这条回复问的那句（你说的或自动触发的）在卡之前，就是写卡的那一轮。"""
    n = 0
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='messages'").fetchone():
        return 0
    for r in conn.execute("SELECT rowid, group_id, created_at FROM feed_items WHERE message_id IS NULL").fetchall():
        at = _dt(r["created_at"])
        if at is None:
            continue
        thread = r["group_id"] if r["group_id"] and r["group_id"] != "main" else "main"
        m = conn.execute("SELECT id, ts FROM messages WHERE thread=? AND role='grava' AND ts>=? ORDER BY id LIMIT 1", (thread, r["created_at"])).fetchone()
        reply = _dt(m["ts"]) if m else None
        if reply is None or reply - at > timedelta(minutes=15):
            continue
        asked = conn.execute("SELECT ts FROM messages WHERE thread=? AND id<? AND role!='grava' ORDER BY id DESC LIMIT 1", (thread, m["id"])).fetchone()
        asked_at = _dt(asked["ts"]) if asked else None
        if asked_at is None or asked_at > at:
            continue
        conn.execute("UPDATE feed_items SET thread=?, message_id=? WHERE rowid=?", (thread, m["id"], r["rowid"]))
        n += 1
    return n


def link_run(run: chat.Run) -> None:
    """一次回复结束：这次回复期间这个线程写的卡挂到这条回复下面（main 认没挂 Agent 的卡，见 push.new_card）。"""
    if run.reply_id is None:
        return
    main = run.thread == "main"
    where = "(group_id IS NULL OR group_id='' OR group_id='main')" if main else "group_id=?"
    with _lock, odb() as conn:
        conn.execute(f"UPDATE feed_items SET thread=?, message_id=? WHERE rowid>? AND message_id IS NULL AND {where}",  # noqa: S608 — where 是常量
                     (run.thread, run.reply_id, run.feed_mark) if main else (run.thread, run.reply_id, run.feed_mark, run.thread))


# —— 格式 ————————————————————————————————————————————————————————————

def num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # NaN


def nv(v: Any) -> int | float | None:
    """给 app 画图的数：整数就不带小数点。"""
    f = num(v)
    return None if f is None else int(f) if f == int(f) else round(f, 1)


def fmt(v: float | None) -> str:
    if v is None:
        return "—"
    return str(int(v)) if v == int(v) else f"{v:.1f}".rstrip("0").rstrip(".")


def md(d: date | None) -> str:
    """10/14（英文 Oct 14）。"""
    if d is None:
        return ""
    return L(f"{d.month}/{d.day}", f"{MONTHS_EN[d.month - 1]} {d.day}")


def weekday(d: date) -> str:
    return L("周" + "一二三四五六日"[d.weekday()], ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")[d.weekday()])


def day_of_text(s: Any) -> date | None:
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def strings(v: Any, limit: int = 8) -> list[str]:
    return [str(x).strip() for x in v if str(x).strip()][:limit] if isinstance(v, list) else []


def duration(minutes: float | None) -> str:
    if minutes is None:
        return ""
    h, m = divmod(int(round(minutes)), 60)
    if not h:
        return L(f"{m} 分钟", f"{m} min")
    return L(f"{h} 小时 {m} 分", f"{h} h {m} min") if m else L(f"{h} 小时", f"{h} h")


def tail(title: str) -> str:
    """卡标题里冒号后面那半句（「练完了：大虾意面 + 睡前酸奶」→「大虾意面 + 睡前酸奶」）；没有冒号就是整句。"""
    parts = re.split(r"[：:]", title or "", maxsplit=1)
    return (parts[1] if len(parts) == 2 and parts[1].strip() else title or "").strip()


def trend_of(v: Any) -> str:
    s = str(v or "").strip().lower()
    if s in ("up", "flat", "down", "new"):
        return s
    if re.search(r"进步|加重|提高|减少辅助|辅助减少|up|better|improv", s):
        return "up"
    if re.search(r"退步|下降|掉|down|worse", s):
        return "down"
    return "flat" if s else "new"


def md_sections(body: str) -> list[tuple[str, list[str]]]:
    """正文按粗体 / # 小标题分段：[(小标题, [要点…])]。小标题前的算一段无名的。"""
    out: list[tuple[str, list[str]]] = [("", [])]
    for raw_line in (body or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        bullet = re.match(r"^(?:[-*•]|\d+[.、)])\s+(.*)$", line)
        head = None if bullet else (re.match(r"^(?:#{1,6}\s*)?\*\*(.+?)\*\*\s*[：:]?\s*(.*)$", line) or re.match(r"^#{1,6}\s+(.+)()$", line))
        if head:
            rest = clean(head.group(2) or "")
            out.append((clean(head.group(1)), [rest] if rest and not rest.startswith(("（", "(")) else []))
            continue
        out[-1][1].append(clean(bullet.group(1) if bullet else line))
    return [(h, ls) for h, ls in out if h or ls]


def clean(s: str) -> str:
    return re.sub(r"\*\*|__|`", "", s).strip()


# —— 各 kind 的小板子 ————————————————————————————————————————————————————

def kind_label(kind: str | None, data: dict | None = None) -> str:
    """产出的名字（浮动标签、小板子、对话卡片的小标题）。饮食建议按第一顿的时间叫早餐 / 午餐 / 晚餐建议。"""
    if kind == "training_review":
        return L("练后报告", "Workout report")
    if kind == "training_plan":
        return L("训练计划", "Training plan")
    if kind == "sleep_report":
        return L("睡眠报告", "Sleep report")
    if kind == "meal_plan":
        times = [m.get("time") for m in (data or {}).get("meals") or [] if isinstance(m, dict) and re.match(r"^\d{1,2}:\d{2}$", str(m.get("time") or ""))]
        hours = [int(str(x).split(":")[0]) for x in times]
        if hours and min(hours) >= 16:
            return L("晚餐建议", "Dinner plan")
        if hours and max(hours) < 11:
            return L("早餐建议", "Breakfast plan")
        if hours and min(hours) >= 11 and max(hours) < 16:
            return L("午餐建议", "Lunch plan")
        return L("饮食建议", "Meal plan")
    return L("报告", "Report")


def review_board(r: sqlite3.Row, d: dict) -> tuple[dict, str, dict | None]:
    """练后报告：时长 / 组数、主项和上次对比、各动作本次 vs 上次 + 结论、接下来 24 小时、下次训练。没有 data 的老卡按正文拆。"""
    when = day_of_text(d.get("date")) or (_dt(r["created_at"]) or datetime.now(TZ)).date()
    session = str(d.get("session") or "").strip()
    m = re.match(r"^(?:练后|练完了?|post[- ]?workout)\s*(?:（[^）]*）|\([^)]*\))?\s*[：:]\s*(.+?)[，,]\s*(.+)$", r["title"] or "", re.I)
    if not session and m:
        session = m.group(1).strip()
    minutes, sets = num(d.get("minutes")), num(d.get("sets"))
    secs = md_sections(r["body"] or "")
    if minutes is None and (hit := re.search(r"(\d+)\s*分钟", r["body"] or "")):
        minutes = float(hit.group(1))
    if sets is None and (hit := re.search(r"(\d+)\s*组", r["body"] or "")):
        sets = float(hit.group(1))
    last = d.get("last") if isinstance(d.get("last"), dict) else {}
    last_day = day_of_text(last.get("date"))
    stats = [{"value": session or "—", "label": L("训练", "Session")}]
    if minutes is not None:
        stats.append({"value": fmt(minutes), "label": L("分钟", "min")})
    if sets is not None:
        stats.append({"value": fmt(sets), "label": L("组", "sets")})
    main = None
    mn = d.get("main") if isinstance(d.get("main"), dict) else {}
    w = mn.get("weight") if isinstance(mn.get("weight"), list) and len(mn.get("weight")) == 2 else None
    if mn.get("name") and w and num(w[1]) is not None:
        unit = str(mn.get("unit") or "kg")
        a, b = num(w[0]), num(w[1])
        delta = (b - a) if a is not None and b is not None else None
        tr = "new" if a is None else "up" if delta and delta > 0 else "down" if delta and delta < 0 else "flat"
        e = mn.get("e1rm") if isinstance(mn.get("e1rm"), list) and len(mn.get("e1rm")) == 2 else None
        note = L(f"估算最大重量 {fmt(num(e[0]))} → {fmt(num(e[1]))} {unit}", f"Estimated max {fmt(num(e[0]))} → {fmt(num(e[1]))} {unit}") if e and num(e[0]) and num(e[1]) else ""
        points = ([{"label": md(last_day), "value": nv(a)}] if a is not None else []) + [{"label": md(when), "value": nv(b)}]
        main = {"name": str(mn["name"]), "value": f"{fmt(b)} {unit}", "delta": (("+" if delta > 0 else "−") + f"{fmt(abs(delta))} {unit}") if delta else None,
                "trend": tr, "note": note, "points": points,
                "a11y": L(f"{mn['name']}工作重量：", f"{mn['name']} working weight: ") + L("，", ", ").join(f"{p['label']} {fmt(p['value'])} {unit}" for p in points)}
    moves = []
    for x in d.get("moves") or []:
        if not isinstance(x, dict) or not x.get("name"):
            continue
        tr = trend_of(x.get("trend") or x.get("verdict"))
        moves.append({"name": str(x["name"]), "now": str(x.get("now") or ""), "last": str(x.get("last") or ""),
                      "verdict": str(x.get("verdict") or {"up": L("进步", "Up"), "down": L("退步", "Down"), "flat": L("持平", "Same"), "new": L("新动作", "New")}[tr]),
                      "delta": str(x.get("delta") or "") or None, "trend": tr})
    find = lambda pat: next((ls for h, ls in secs if re.search(pat, h, re.I)), None)  # noqa: E731
    next24 = strings(d.get("next_24h")) or strings(d.get("recovery_24h")) or (find(r"接下来|24|next|recover") or [])[:6]
    nxt = d.get("next")
    next_text, next_day = "", None
    if isinstance(nxt, dict):
        next_day = day_of_text(nxt.get("date"))
        head = " ".join(x for x in (f"{md(next_day)} {weekday(next_day)}" if next_day else "", str(nxt.get("time") or "")) if x)
        next_text = " · ".join(x for x in (head, str(nxt.get("session") or "")) if x)
    elif isinstance(nxt, str) and nxt.strip():
        next_text = nxt.strip()
    else:
        next_text = (str(d.get("tomorrow") or "") or ((find(r"下次|明天|tomorrow|next session") or [""])[0])).rstrip("。.；;")
    verdict = str(d.get("verdict") or "")
    if not verdict and m:
        verdict = m.group(2).strip()
    vtrend = main["trend"] if main and main["trend"] != "new" else trend_of(verdict) if verdict else None
    rest = next_day is not None and (next_day - when).days >= 2
    footer = " · ".join(x for x in (L("明天休息", "Rest tomorrow") if rest else "", L(f"下次 {md(next_day)} {nxt.get('session') or ''}".strip(), f"Next {md(next_day)} {nxt.get('session') or ''}".strip()) if next_day else next_text) if x)
    notes = [] if moves else (find(r"怎么样|这次|how|session") or (secs[0][1] if secs and not secs[0][0] else []))[:5]
    skipped = strings(d.get("skipped"))
    board = {
        "template": "training_review", "session": session, "stats": stats, "recovery": nv(d.get("recovery")),
        "main": main, "compare": L(f"与 {md(last_day)} 相比", f"Compared with {md(last_day)}") if last_day else L("与上次相比", "Compared with last time"),
        "moves": moves[:8], "skipped": L(f"本次未做：{'、'.join(skipped)}", f"Skipped: {', '.join(skipped)}") if skipped else None,
        "next24h": next24[:6], "next": next_text or None, "verdict": verdict or None, "verdictTrend": vtrend, "footer": footer or None, "notes": notes,
    }
    if main:
        change = (L(f"，比上次加 {main['delta'][1:]}", f", up {main['delta'][1:]} on last time") if main["trend"] == "up"
                  else L(f"，比上次减 {main['delta'][1:]}", f", down {main['delta'][1:]} on last time") if main["trend"] == "down"
                  else L("，与上次持平", ", same as last time") if main["trend"] == "flat" else "")
        line = f"{session} · {main['name']} {main['value']}{change}" if session else f"{main['name']} {main['value']}{change}"
        pts = main["points"]
        spark = {"type": "pair", "values": [p["value"] for p in pts], "labels": [p["label"] for p in pts], "a11y": main["a11y"]} if len(pts) == 2 else None
    else:
        line, spark = tail(r["title"] or "") or session, None
    return board, line, spark


def macro_split(p: float | None, c: float | None, f: float | None) -> list[int] | None:
    """三大营养素按热量的占比（蛋白质、碳水 4 kcal/g，脂肪 9），四舍五入后补齐到 100。"""
    if p is None or c is None or f is None:
        return None
    parts = [p * 4, c * 4, f * 9]
    total = sum(parts)
    if total <= 0:
        return None
    raw_pct = [x * 100 / total for x in parts]
    out = [int(x) for x in raw_pct]
    for i in sorted(range(3), key=lambda i: raw_pct[i] - out[i], reverse=True)[: 100 - sum(out)]:
        out[i] += 1
    return out


_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="outputs")
_targets: dict[str, tuple[float, float | None]] = {}
_target_jobs: dict[str, concurrent.futures.Future] = {}


def _read_limits(day: str) -> float | None:
    """那天的蛋白质目标，和饮食看板读的同一份（GET /api/diet/day 的 targets：现在是训记的 foods.limits，换了饮食数据源也跟着走）。"""
    import main  # 延迟导入：main 在启动时就加载好了（uvicorn main:app），这里只是拿它的 diet_day
    return num(((main.diet_day(day) or {}).get("targets") or {}).get("protein"))


def protein_target(day: str, data: dict) -> float | None:
    """蛋白质目标（克）：卡里写了用卡里的（data.targets.protein）→ server.json nutrition_targets → 饮食看板那天的目标。数据源慢或出错就先不画目标线。"""
    t = data.get("targets") if isinstance(data.get("targets"), dict) else {}
    if num(t.get("protein")):
        return num(t.get("protein"))
    over = settings.nutrition_targets() or {}
    if num(over.get("protein")):
        return num(over.get("protein"))
    hit = _targets.get(day)
    if hit and time.time() - hit[0] < 1800:
        return hit[1]
    job = _target_jobs.get(day)
    if job is None or job.done():
        job = _target_jobs[day] = _pool.submit(_read_limits, day)
    try:
        val = job.result(timeout=3)
    except concurrent.futures.TimeoutError:
        return None
    except Exception:  # noqa: BLE001 — 训记读不到：这次不画目标线，30 分钟后再试
        val = None
    _targets[day] = (time.time(), val)
    return val


def expiring(d: dict) -> str | None:
    """「大虾和酸奶今天到期」这种提醒：卡里写了 expiring 用它，不然从 why / 每顿的备注里找含「到期」的那一小句。"""
    exp = strings(d.get("expiring"), 4)
    if exp:
        return L(f"{'、'.join(exp)}今天到期", f"Use today: {', '.join(exp)}")
    texts = [str(d.get("why") or "")] + [str(m.get("note") or "") for m in d.get("meals") or [] if isinstance(m, dict)]
    for text in texts:
        for part in re.split(r"[。；;，,\n]", text):
            p = part.strip()
            if "到期" in p and 0 < len(p) <= 18:
                return p
    return None


def meal_board(r: sqlite3.Row, d: dict) -> tuple[dict, str, dict | None]:
    """饮食建议：营养环（按热量）、蛋白质对目标、各顿明细。"""
    meals = [m for m in d.get("meals") or [] if isinstance(m, dict)]
    tot = d.get("totals") if isinstance(d.get("totals"), dict) else {}
    pick = lambda k: num(tot.get(k)) if num(tot.get(k)) is not None else (sum(num(m.get(k)) or 0 for m in meals) if meals and all(num(m.get(k)) is not None for m in meals) else None)  # noqa: E731
    kcal, p, c, f = pick("kcal"), pick("protein"), pick("carb"), pick("fat")
    split = macro_split(p, c, f)
    count = len(meals)
    cn = {1: "一", 2: "两", 3: "三", 4: "四", 5: "五", 6: "六"}.get(count, str(count))
    ring = None
    if split:
        names = (L("蛋白质", "Protein"), L("碳水", "Carbs"), L("脂肪", "Fat"))
        ring = {"total": fmt(kcal) if kcal is not None else "—", "kcal": nv(kcal), "protein": nv(p), "carb": nv(c), "fat": nv(f),
                "caption": L(f"kcal · {cn}顿", f"kcal · {count} meal{'s' if count != 1 else ''}"),
                "parts": [{"key": k, "label": n, "grams": f"{fmt(g)} g", "pct": pct} for k, n, g, pct in zip(("protein", "carb", "fat"), names, (p, c, f), split)],
                "a11y": L("按热量计：", "By calories: ") + L("，", ", ").join(f"{n} {pct}%" for n, pct in zip(names, split))}
    day = (_dt(r["created_at"]) or datetime.now(TZ)).date().isoformat()
    target = protein_target(day, d) if p is not None else None
    protein = None
    if p is not None:
        protein = {"value": fmt(p), "target": fmt(target) if target else None, "grams": nv(p), "targetGrams": nv(target) if target else None,
                   "a11y": L(f"蛋白质 {fmt(p)} 克", f"Protein {fmt(p)} g") + (L(f"，目标 {fmt(target)} 克", f", target {fmt(target)} g") if target else "")}
    rows = []
    for m in meals:
        items = [i for i in m.get("items") or [] if isinstance(i, dict) and i.get("name")]
        text = " · ".join(" ".join(x for x in (str(i["name"]), fmt(num(i.get("amount"))) if num(i.get("amount")) is not None else str(i.get("amount") or ""),
                                               str(i.get("unit") or "") if num(i.get("amount")) is not None else "") if x) for i in items)
        rows.append({"time": str(m.get("time") or ""), "label": str(m.get("label") or ""), "items": text,
                     "kcal": f"{fmt(num(m.get('kcal')))} kcal" if num(m.get("kcal")) is not None else "", "note": str(m.get("note") or "") or None})
    board = {"template": "meal_plan", "ring": ring, "protein": protein, "note": str(d.get("vs_target") or "") or None, "tag": expiring(d),
             "meals": rows, "why": str(d.get("why") or "") or None, "shopping": strings(d.get("shopping")), "loggable": any(row["items"] for row in rows)}
    line = " · ".join(x for x in (tail(r["title"] or ""), f"{fmt(kcal)} kcal" if kcal is not None else "") if x)
    spark = {"type": "ring", "values": split, "a11y": ring["a11y"]} if ring else None
    return board, line, spark


_series: tuple[float, list[dict]] | None = None


def recovery_days() -> list[dict]:
    """近 8 天的恢复分（健康数据源），留 5 分钟。没接健康数据源 = 空。"""
    global _series
    if _series and time.time() - _series[0] < 300:
        return _series[1]
    days: list[dict] = []
    ah = sources.apple_health
    if ah is not None and hasattr(ah, "recovery_series"):
        try:
            days = [x for x in ah.recovery_series(8).get("days") or [] if isinstance(x, dict)]
        except Exception:  # noqa: BLE001 — 算不出来就不画趋势
            days = []
    _series = (time.time(), days)
    return days


BAND_TONE = {"good": "good", "ok": "warn", "low": "bad"}


def sleep_board(r: sqlite3.Row, d: dict) -> tuple[dict, str, dict | None]:
    """睡眠报告：恢复分、睡眠时长对目标、近 7 天恢复分；健康数据没同步时说清楚，同步了以后带上新的数。"""
    day = day_of_text(d.get("date")) or (_dt(r["created_at"]) or datetime.now(TZ)).date()
    sleep_min = num(d.get("sleep_min"))
    synced = bool(d["synced"]) if "synced" in d else sleep_min is not None
    provisional = bool(d.get("score_provisional")) or not synced
    score = num(d.get("score"))
    verdict = str(d.get("verdict") or "")
    lo_hi = (raw().get("outputs") or {}).get("sleep_target_min") or SLEEP_TARGET
    try:
        lo, hi = float(lo_hi[0]), float(lo_hi[1])
    except (TypeError, ValueError, IndexError, KeyError):
        lo, hi = SLEEP_TARGET
    series = [x for x in recovery_days() if x.get("date") and x["date"] <= day.isoformat()][-7:]
    live = next((x for x in series if x["date"] == day.isoformat()), None)
    updated = None
    if live and not synced and num(live.get("score")) is not None:
        live_sleep = num(((live.get("components") or {}).get("sleep") or {}).get("minutes"))
        updated = {"score": fmt(num(live["score"])), "label": str(live.get("label") or ""), "tone": BAND_TONE.get(live.get("band") or "", "neutral"),
                   "sleep": duration(live_sleep) if live_sleep is not None else None, "minutes": nv(live_sleep)}
    stages = [{"label": n, "minutes": nv(d.get(k)), "text": duration(num(d.get(k)))} for k, n in
              (("deep_min", L("深睡", "Deep")), ("rem_min", "REM"), ("core_min", L("核心", "Core"))) if num(d.get(k)) is not None]
    vitals = [x for x in (
        {"label": "HRV", "value": f"{fmt(round(num(d.get('hrv_ms'))))} ms"} if num(d.get("hrv_ms")) is not None else None,
        {"label": L("静息心率", "Resting HR"), "value": f"{fmt(num(d.get('rhr_bpm')))} bpm"} if num(d.get("rhr_bpm")) is not None else None,
        {"label": L("醒来", "Woke"), "value": str(d.get("woke") or d.get("woke_signal"))} if d.get("woke") or d.get("woke_signal") else None) if x]
    secs = md_sections(r["body"] or "")
    notes = [x for _, ls in secs for x in ls][:4]
    tone = "neutral" if score is None or provisional else "good" if score >= 70 else "warn" if score >= 45 else "bad"
    board = {
        "template": "sleep_report", "synced": synced,
        "score": {"value": fmt(score) if score is not None else None, "label": verdict or None, "tone": tone, "provisional": provisional},
        "sleep": {"text": duration(sleep_min) if sleep_min is not None else None, "minutes": nv(sleep_min), "low": nv(lo), "high": nv(hi),
                  "target": L(f"目标 {fmt(lo / 60)}–{fmt(hi / 60)} 小时", f"Target {fmt(lo / 60)}–{fmt(hi / 60)} h"),
                  "range": L(f"{fmt(lo / 60)}–{fmt(hi / 60)} 小时", f"{fmt(lo / 60)}–{fmt(hi / 60)} h")},
        "stages": stages, "vitals": vitals,
        "trend": {"points": [{"label": md(day_of_text(x["date"])), "value": nv(x.get("score"))} for x in series if num(x.get("score")) is not None],
                  "caption": L("近 7 天恢复分", "Recovery, last 7 days")} if series else None,
        "notice": None if synced else L("健康数据尚未同步，以下为推断。", "Health data hasn't synced yet; the figures below are estimates."),
        "updated": updated, "notes": notes,
    }
    if updated:
        line = L(f"已同步 · 恢复分 {updated['score']}" + (f"（{updated['label']}）" if updated["label"] else ""), f"Synced · recovery {updated['score']}")
    elif synced:
        line = " · ".join(x for x in (L(f"睡眠 {duration(sleep_min)}", f"Sleep {duration(sleep_min)}") if sleep_min is not None else "",
                                      L(f"恢复分 {fmt(score)}", f"Recovery {fmt(score)}") if score is not None else "") if x)
    else:
        line = L("健康数据未同步", "Health data not synced") + (L(f" · 恢复分 {fmt(score)}（暂估）", f" · recovery {fmt(score)} (estimate)") if score is not None else "")
    values = [p["value"] for p in (board["trend"] or {}).get("points") or []]
    spark = {"type": "trend", "values": values, "a11y": board["trend"]["caption"] + L("：", ": ") + L("、", ", ").join(fmt(v) for v in values)} if len(values) >= 2 else None
    return board, line or tail(r["title"] or ""), spark


def plan_tone(decision: str) -> str:
    """和 app 的 decisionTone 一样：休息 = bad，减量 / 轻 = warn，练 = good。"""
    if "休息" in decision or re.search(r"\brest\b", decision, re.I):
        return "bad"
    if "减量" in decision or "轻" in decision or re.search(r"deload|\blight|\beasy|\breduce", decision, re.I):
        return "warn"
    return "good" if "练" in decision or re.search(r"\btrain|\bworkout|\blift", decision, re.I) else "neutral"


def plan_board(r: sqlite3.Row, d: dict) -> tuple[dict, str, dict | None]:
    decision = str(d.get("decision") or "")
    board = {"template": "training_plan", "decision": decision or None, "tone": plan_tone(decision), "session": str(d.get("session") or "") or None,
             "time": str(d.get("time") or "") or None, "duration": duration(num(d.get("duration_min"))) or None,
             "intensity": str(d.get("intensity") or "") or None, "focus": strings(d.get("focus")), "cautions": strings(d.get("cautions")),
             "why": str(d.get("why") or "") or None}
    line = " · ".join(x for x in (board["session"], decision, board["time"]) if x) or tail(r["title"] or "")
    return board, line, None


def text_board(r: sqlite3.Row) -> tuple[dict, str, dict | None]:
    import push  # 延迟导入：push 依赖 chat，推送的「第一行」和这里同一个算法
    return {"template": "text"}, push.first_line(r["body"] or "") or tail(r["title"] or ""), None


BUILDERS = {"training_review": review_board, "meal_plan": meal_board, "sleep_report": sleep_board, "training_plan": plan_board}


def output_json(r: sqlite3.Row) -> dict:
    kind = r["kind"] or None
    try:
        data = json.loads(r["data"]) if r["data"] else {}
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    builder = BUILDERS.get(kind or "")
    if builder and (data or kind == "training_review"):
        try:
            board, line, spark = builder(r, data)
        except Exception:  # noqa: BLE001 — Agent 写的 data 不合规矩：退回正文
            board, line, spark = text_board(r)
    else:
        board, line, spark = text_board(r)
    at = _dt(r["created_at"])
    return {"id": r["id"], "kind": kind, "label": kind_label(kind, data), "agent": r["group_id"] if r["group_id"] and r["group_id"] != "main" else None,
            "title": r["title"], "body": r["body"] or "", "line": line, "spark": spark, "createdAt": r["created_at"],
            "time": at.strftime("%H:%M") if at else "", "day": chat.day_of(r["created_at"]), "seen": bool(r["seen_at"]), "replaced": bool(r["dismissed"]),
            "thread": r["thread"], "messageId": r["message_id"], "board": board}


# —— 接口 ————————————————————————————————————————————————————————————

@router.get("/api/outputs")
def outputs(all: int = 0, day: str | None = None, limit: int = LIST_MAX):
    """不带参数：这个逻辑日还没看过的产出，最新在前（浮动标签）。all=1：那一天（day，默认今天）的全部产出，被替换的不算（今天页的报告）。"""
    try:
        lo, hi = chat.day_bounds(day or chat.day_of(now_iso()))
    except ValueError as exc:
        raise HTTPException(400, L("day 要写成 YYYY-MM-DD", "day must be YYYY-MM-DD")) from exc
    seen = "" if all else " AND seen_at IS NULL"
    with _lock, odb() as conn:
        rows = conn.execute(f"SELECT * FROM feed_items WHERE dismissed=0{seen} AND created_at>=? AND created_at<? AND IFNULL(kind,'') NOT IN (?, ?) "  # noqa: S608
                            "ORDER BY created_at DESC LIMIT ?", (lo, hi, *RETIRED, max(1, min(limit, 50)))).fetchall()
    return {"ok": True, "outputs": [output_json(r) for r in rows]}


@router.get("/api/outputs/{fid}")
def output(fid: str):
    with _lock, odb() as conn:
        r = conn.execute("SELECT * FROM feed_items WHERE id=?", (fid,)).fetchone()
    if r is None:
        raise HTTPException(404, L("没有这份产出", "No such output"))
    return {"ok": True, "output": output_json(r)}


def changes_for(thread: str, lo: str, hi: str) -> list[dict]:
    """这个线程这一天（lo–hi）里挂在回复下面的产出（给 /api/chat/cards，kind output）。"""
    with _lock, odb() as conn:
        rows = conn.execute("SELECT * FROM feed_items WHERE thread=? AND message_id IS NOT NULL AND created_at>=? AND created_at<? ORDER BY created_at",
                            (thread, lo, hi)).fetchall()
    return [{"kind": "output", "id": r["id"], "thread": thread, "messageId": r["message_id"], "createdAt": r["created_at"], "output": output_json(r)} for r in rows]
