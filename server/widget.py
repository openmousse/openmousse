"""小组件（app 1.0.5 起的 iOS 桌面 / 锁屏小组件，app/targets/widgets）要的一小份数据，GET /api/widget。

- recovery：今天的恢复分（apple_health.recovery_series，没接健康数据源就没有）
- meal：今天最新一张三餐建议卡（feed_items kind=meal_plan）里的下一餐
- items：今天和明天还没过去的日程（schedule.build_timeline：课、自己和 Agent 加的安排、到期的截止），最多 8 条，
  带 Unix 秒的 start / end：小组件按 end 排好「下一件」什么时候往后挪，不用再来取
- remember：「要记得的」有几件、第一件是什么（schedule.build_remember）
文字（时间、标签）按请求的语言排好，小组件只画。小组件每半小时左右来取一次，app 在前台时也取一份写进 App Group 当缓存。
每块单独出错不影响别的块；按语言缓存 60 秒。
"""
from __future__ import annotations

import json
import threading
import time
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Request

import schedule
import sources
from chat import _lock, db
from config import TZ, settings
import i18n
from i18n import L

router = APIRouter()

CACHE_SECONDS = 60
MAX_ITEMS = 8
_cache: dict[str, tuple[float, dict]] = {}
_cache_lock = threading.Lock()

BAND_TINT = {"good": "green", "ok": "yellow", "low": "red"}


def band_label(band: str | None, label: str | None) -> str:
    return {"good": L("恢复好", "Recovered"), "ok": L("一般", "Moderate"), "low": L("该休息", "Take it easy")}.get(band or "", label or "")


def hm(minutes: float | int | None) -> str:
    if not minutes:
        return ""
    m = int(round(minutes))
    return L(f"{m // 60} 小时 {m % 60} 分", f"{m // 60}h {m % 60:02d}m")


def recovery() -> dict | None:
    if sources.apple_health is None:
        return None
    series = sources.apple_health.recovery_series(2)
    latest = series.get("latest")
    if not latest or latest.get("date") != schedule.today().isoformat() or latest.get("score") is None:
        return None
    sleep = ((latest.get("components") or {}).get("sleep") or {}).get("minutes")
    if not sleep:
        return None  # 还没有昨晚的睡眠（没睡、手表还没同步）：只凭心率算出来的分不准，先不显示
    line = L(f"睡了 {hm(sleep)}", f"Slept {hm(sleep)}") if sleep else None
    return {"score": latest["score"], "label": band_label(latest.get("band"), latest.get("label")), "tint": BAND_TINT.get(latest.get("band") or ""),
            "line": line}


def next_meal(now: datetime) -> dict | None:
    with _lock, db() as conn:
        try:
            r = conn.execute("SELECT created_at, data FROM feed_items WHERE dismissed=0 AND kind='meal_plan' ORDER BY created_at DESC LIMIT 1").fetchone()
        except Exception:  # noqa: BLE001 — 老库没有 kind 列
            return None
    if not r or not r["data"] or str(r["created_at"])[:10] != now.date().isoformat():
        return None
    try:
        data = json.loads(r["data"])
    except ValueError:
        return None
    for m in data.get("meals") or []:
        if not isinstance(m, dict):
            continue
        t = str(m.get("time") or "")
        if t[:2].isdigit() and ":" in t:
            try:
                hh, mm = int(t.split(":")[0]), int(t.split(":")[1][:2])
                if now.replace(hour=hh, minute=mm) < now - timedelta(minutes=90):
                    continue  # 早就过了饭点的（卡还没更新）不算下一餐
            except ValueError:
                pass
        names = L("、", ", ").join(str(i.get("name")) for i in (m.get("items") or [])[:3] if isinstance(i, dict) and i.get("name"))
        kcal = m.get("kcal")
        return {"label": str(m.get("label") or L("下一餐", "Next meal")), "time": t or None, "text": names or str(m.get("note") or "") or None,
                "kcal": int(round(float(kcal))) if isinstance(kcal, (int, float)) else None}
    return None


def items(now: datetime) -> list[dict]:
    today = schedule.today()
    tl = schedule.build_timeline(today, 2)
    out = []
    for e in tl.get("events") or []:
        if e.get("skip") or e.get("done") or e.get("free"):
            continue
        d = e.get("date")
        if not d:
            continue
        start_s, end_s = e.get("start") or "", e.get("end") or ""
        if e.get("allDay") or not start_s:
            if e.get("kind") != "deadline":
                continue  # 全天的安排（假期之类）不占「下一件」
            start = schedule.at(d, "23:59")
            end = start
        else:
            start = schedule.at(d, start_s)
            end = schedule.at(d, end_s) if end_s else start + timedelta(minutes=60 if e.get("kind") != "deadline" else 0)
        if end < now:
            continue
        key = str(e.get("key") or "")
        kind = "training" if key.startswith("fitness:training") else ("class" if e.get("kind") == "class" else "deadline" if e.get("kind") == "deadline" else "event")
        when = start_s if start_s else L("截止", "Due")
        if date.fromisoformat(d) != today:
            when = L(f"明天 {when}", f"Tomorrow {when}")
        out.append({"start": int(start.timestamp()), "end": int(end.timestamp()), "time": when, "title": e.get("title") or "",
                    "place": e.get("location") or None, "kind": kind})
    out.sort(key=lambda x: x["start"])
    return out[:MAX_ITEMS]


def remember() -> dict | None:
    rem = schedule.build_remember()
    # 只数一周内要办的（安全提醒、过了的、明天、这周）；以后的、没日期的、邮件动态不算，不然数字一直很大
    todo = [e for e in rem.get("items") or [] if e.get("group") in ("security", "overdue", "tomorrow", "week")]
    if not todo:
        return None
    first = todo[0]
    label = first.get("title") or ""
    if first.get("date"):
        try:
            d = date.fromisoformat(first["date"])
            label = f"{label} {d.month}/{d.day}" if i18n.lang() == "zh" else f"{label} {d.day}/{d.month}"
        except (ValueError, AttributeError):
            pass
    return {"count": len(todo), "first": label}


def build(lang_key: str) -> dict:
    now = datetime.now(TZ)
    out: dict = {"ok": True, "name": settings.app_name, "at": int(time.time())}
    for key, fn in (("recovery", recovery), ("meal", lambda: next_meal(now)), ("items", lambda: items(now)), ("remember", remember)):
        try:
            out[key] = fn()
        except Exception:  # noqa: BLE001 — 一块出错不拖累别的块
            out[key] = None
    if not out.get("items"):
        out["empty"] = L("今天没有别的安排", "Nothing else today")
    return out


@router.get("/api/widget")
def widget(request: Request):  # noqa: ARG001 — 语言由中间件从请求头放进 i18n
    lang_key = i18n.lang()
    with _cache_lock:
        hit = _cache.get(lang_key)
        if hit and time.time() - hit[0] < CACHE_SECONDS:
            return hit[1]
    data = build(lang_key)
    with _cache_lock:
        _cache[lang_key] = (time.time(), data)
    return data
