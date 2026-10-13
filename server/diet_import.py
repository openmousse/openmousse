#!/usr/bin/env python3
"""从训记搬饮食记录进来（2026-10-13）：一次性导出 → 导入 → 逐天核对 → 写迁移报告。对训记只读，不写任何东西。

  python3 diet_import.py export [--from 2025-10-13] [--to 今天] [--out 文件]      # 只读：按月拉训记的饮食记录（含营养目标），存成一个 JSON
  python3 diet_import.py import --file 导出的文件 [--commit] [--report 报告.md]
        [--current-target 136/240/80[/2224] --current-from 2026-09-27 --current-note "…"]
  python3 diet_import.py verify --file 导出的文件 [--report 报告.md]             # 只核对：训记每天的合计 vs 这里的

训记的 Open API 只给过去一年到未来三个月（2025-10-13 起）；更早的要在训记 App 里自己导出。导出文件放在 <data_dir>/diet/（不进 git）。
不加 --commit 只算不写（报告照样出，标「预演」）。再跑一次是安全的：按训记的记录 id 认，已经导过的、没变的跳过，训记里改过的更新，
这里改过的（导入后在 Grava 里改过）不动、报告里列出来。

导进来的样子：
- 食物库（diet_foods）：每个训记食物（uniquekey）一样；/shiwu/… 是训记官方库，数字的是自定义食物。名字取用得最多的写法，别的写法进 aliases。
  每 100 g 营养取最近一次记录里的；训记的份量单位（foodUnit）变成常用份量。
- 记录（diet_entries）：一条训记记录一行，source = xunji，per100 = 那条记录当时的营养（历史合计不会因为食物改过而变）。
  训记的 record_id 不唯一（App 里记的是食物编号，同一种食物每天都一样），所以 source_ref = 日期:餐次:record_id#当天这一餐第几次；
  再跑一次按天对齐：新的加、训记里改了的改、训记里删了的这边也撤掉（在这边改过的不动，报告里列出来）。
  训记的餐次：morning 早餐、noon 午餐、night 晚餐、preworkout 练前、postworkout 练后、*-added 加餐。训记不存吃的时间，time 空着。
- 营养目标（diet_targets）：训记的目标是按天存的（foods.limits），连续几天一样的合成一行。训记里目标是 0 的日子 = 没设目标；
  --current-target 给一个「从某天起」的目标（比如训记后来没设、平时一直按的那个），写成 source = profile 的一行。
- 喝水记录、饮食模板不导（报告里写有多少）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import uuid
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import HTTPException

import diet
import sources
from chat import _lock, now_iso
from config import TZ, settings
from i18n import L

OUT_DIR = settings.data_dir / "diet"
TOL_KCAL, TOL_G = 0.5, 0.05


def xunji():
    if sources.xunji is None:
        sys.exit(L("没接训记（scripts/xunji.py 不在或加载失败）", "Xunji isn't connected (scripts/xunji.py is missing or failed to load)"))
    return sources.xunji


# —— 导出（只读）——————————————————————————————————————————————————————————

def months(lo: date, hi: date) -> list[tuple[date, date]]:
    out, cur = [], lo
    while cur <= hi:
        nxt = (cur.replace(day=1) + timedelta(days=32)).replace(day=1)
        out.append((cur, min(nxt - timedelta(days=1), hi)))
        cur = nxt
    return out


def export(lo: date, hi: date, out: Path | None) -> Path:
    xj = xunji()
    days: dict[str, dict] = {}
    window = None
    for a, b in months(lo, hi):
        print(L(f"读训记 {a} → {b} …", f"Reading Xunji {a} → {b} …"), file=sys.stderr)
        for attempt in range(6):  # 训记同一接口 15 秒一次，线上服务、watcher 也在读：撞上限频就按它说的等一会儿再试
            try:
                data = xj.call("food_query", {"start_date": a.isoformat(), "end_date": b.isoformat(), "include_detail": True}, refresh=True)
                break
            except xj.XunjiError as e:
                if "too frequent" not in str(e) or attempt == 5:
                    raise
                m = re.search(r"retry_after_ms\D+(\d+)", str(e))
                time.sleep(min(int(m.group(1)) / 1000 if m else 15, 60) + 2 + attempt * 3)
        res = data.get("res") or {}
        window = res.get("window") or window
        for d in res.get("days") or []:
            days[str(d.get("datestr"))] = d
    tpl = None
    try:
        tpl = xj.call("food_tpl_list", {}, refresh=True).get("res")
    except Exception as e:  # noqa: BLE001 — 模板只是报告里提一句
        tpl = {"error": str(e)[:200]}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = out or OUT_DIR / f"xunji-export-{datetime.now(TZ).strftime('%Y%m%d-%H%M')}.json"
    payload = {"exported_at": now_iso(), "from": lo.isoformat(), "to": hi.isoformat(), "window": window, "days": [days[k] for k in sorted(days)],
               "templates": tpl}
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf8")
    path.chmod(0o600)
    n = sum(len(((d.get("foods") or {}).get("records") or [])) for d in days.values())
    print(L(f"导出了 {len(days)} 天、{n} 条记录 → {path}", f"Exported {len(days)} days, {n} records → {path}"), file=sys.stderr)
    return path


# —— 导入 ————————————————————————————————————————————————————————————————

def recs_of(day: dict) -> list[dict]:
    return [r for r in ((day.get("foods") or {}).get("records") or []) if isinstance(r, dict)]


def ntr_of(rec: dict) -> dict:
    n = rec.get("ntr") or {}
    return {"kcal": diet.num(n.get("cal")) if diet.num(n.get("cal")) is not None else diet.num(rec.get("cals")),
            "protein": diet.num(n.get("protein")), "carb": diet.num(n.get("carb")), "fat": diet.num(n.get("fat"))}


def grams(rec: dict) -> tuple[float | None, str]:
    """(克或毫升, 单位)。训记的 amount 一般就是克；别的单位按 foodUnit 换算。"""
    g = diet.xunji_grams(rec)
    unit = (rec.get("unit") or "g").strip()
    return g, ("ml" if unit in ("ml", "毫升") else "g")


def food_key(rec: dict) -> str:
    k = str(rec.get("uniquekey") or "").strip()
    return f"uk:{k}" if k else f"name:{diet.norm(rec.get('name'))}"


def limits_of(day: dict) -> dict | None:
    lim = (day.get("foods") or {}).get("limits") or {}
    p, c, f = diet.num(lim.get("protein")) or 0, diet.num(lim.get("carb")) or 0, diet.num(lim.get("fat")) or 0
    if not (p and c and f):
        return None
    w = diet.num(lim.get("water")) or diet.num(((day.get("foods") or {}).get("water") or {}).get("limit")) or None
    return {"protein": p, "carb": c, "fat": f, "water_ml": w or None}


def build_foods(days: list[dict]) -> dict[str, dict]:
    groups: dict[str, dict] = {}
    for d in days:
        for rec in recs_of(d):
            k = food_key(rec)
            g = groups.setdefault(k, {"names": Counter(), "last": "", "ntr": None, "units": {}, "uses": 0, "uniquekey": rec.get("uniquekey")})
            g["names"][str(rec.get("name") or "").strip()] += 1
            g["uses"] += 1
            if str(d.get("datestr")) >= g["last"]:
                g["last"] = str(d.get("datestr"))
                g["ntr"] = ntr_of(rec)
            for u in (rec.get("ntr") or {}).get("foodUnit") or []:
                if isinstance(u, dict) and u.get("unit") and diet.num(u.get("gram")):
                    cnt = diet.num(u.get("count")) or 1
                    g["units"][f"{diet.fmt(cnt)} {u['unit']}"] = round(float(u["gram"]), 1)  # 「1 份 = 100 g」
    out = {}
    for k, g in groups.items():
        names = [n for n, _ in g["names"].most_common() if n]
        if not names or not g["ntr"] or any(g["ntr"][m] is None for m in diet.MACROS):
            continue
        uk = str(g["uniquekey"] or "")
        official = uk.startswith("/shiwu") or uk.startswith("/")
        out[k] = {"name": names[0], "aliases": names[1:], "per100": g["ntr"], "basis": "g", "source": "xunji", "source_ref": uk or None,
                  "source_note": (L(f"训记官方食物库 {uk}", f"Xunji food database {uk}") if official else
                                  L("训记里的自定义食物", "A custom food in Xunji") if uk else L("训记记录（没有食物编号）", "A Xunji record (no food id)")),
                  "portions": [{"label": lbl, "grams": gr} for lbl, gr in g["units"].items()], "uses": 0, "last_used": g["last"],
                  "official": official}
    return out


def target_runs(days: list[dict]) -> list[dict]:
    """按天的 limits → 连续一样的合成一段：[{from, to, limits | None}]。"""
    runs: list[dict] = []
    for d in sorted(days, key=lambda x: str(x.get("datestr"))):
        lim = limits_of(d)
        ds = str(d.get("datestr"))
        if runs and runs[-1]["limits"] == lim:
            runs[-1]["to"] = ds
        else:
            runs.append({"from": ds, "to": ds, "limits": lim})
    return runs


def do_import(conn, payload: dict, current: dict | None) -> dict:
    """在给的连接上照常写；提交还是回滚由 main 定（预演也真写一遍再回滚，所以预演的核对和真导入一模一样）。只碰 conn。"""
    days = [d for d in payload.get("days") or [] if isinstance(d, dict) and d.get("datestr")]
    foods = build_foods(days)
    stats: Counter = Counter()
    conflicts, skipped = [], []
    batch = f"xunji-{datetime.now(TZ).strftime('%Y%m%d%H%M')}-{uuid.uuid4().hex[:4]}"
    ts = now_iso()
    # 食物库：同一个训记食物（uniquekey）已经导过就只跟着更新用了几次、最后一次；名字和营养导过以后在这里改过的不覆盖
    food_ids: dict[str, str] = {}
    for k, f in foods.items():
        existing = conn.execute("SELECT id FROM diet_foods WHERE source='xunji' AND source_ref=? AND deleted_at IS NULL",
                                (f["source_ref"],)).fetchone() if f["source_ref"] else None
        if existing:
            food_ids[k] = existing["id"]
            stats["foods_existing"] += 1
            continue
        try:
            fid, _ = diet.save_food(conn, f, "migration")
        except HTTPException as e:  # 营养数字不合理（负数、每 100 g 超过 950 kcal）：食物库不收，记录照导（带自己的营养快照）
            skipped.append({"date": f["last_used"], "name": f["name"], "why": f"food not saved: {e.detail}"})
            continue
        food_ids[k] = fid
        stats["foods_new"] += 1
        stats["foods_official" if f["official"] else "foods_custom"] += 1
    # 记录。训记的 record_id 不唯一：App 里记的是食物编号（同一种食物每天都一样），开放接口写的才是 open:… 。
    # 所以一条记录的键 = 日期 : 餐次 : record_id # 当天这一餐里第几次出现；每一天按键对齐：新的加、变了的改、训记里没了的撤掉
    lo, hi = str(payload.get("from") or ""), str(payload.get("to") or "")
    seen_days = set()
    for d in days:
        ds = str(d["datestr"])
        seen_days.add(ds)
        occ: Counter = Counter()
        desired: dict[str, dict] = {}
        for rec in recs_of(d):
            rid = str(rec.get("record_id") or "").strip() or f"noid:{diet.norm(rec.get('name'))}"
            mt = str(rec.get("meal_type") or "")
            occ[(mt, rid)] += 1
            desired[f"{ds}:{mt}:{rid}#{occ[(mt, rid)]}"] = rec
        existing = {r["source_ref"]: r for r in conn.execute("SELECT * FROM diet_entries WHERE source='xunji' AND date=?", (ds,))}
        for key, rec in desired.items():
            g, unit = grams(rec)
            per100 = ntr_of(rec)
            if g is None or any(per100[m] is None for m in diet.MACROS):
                skipped.append({"date": ds, "name": rec.get("name"), "why": "unknown unit" if g is None else "no nutrition"})
                continue
            vals = {m: (per100[m] or 0) * g / 100 for m in diet.MACROS}
            meal = diet.meal_key(rec.get("meal_type")) or "snack"
            row = {"name": str(rec.get("name") or "").strip()[:80] or "?", "amount": g, "unit": unit, "portion": None, "per100": per100, **vals,
                   "food_id": food_ids.get(food_key(rec)), "nutri_source": "xunji", "nutri_ref": str(rec.get("uniquekey") or "") or None,
                   "estimated": False, "note": None, "time": None, "meal": meal}
            old = existing.get(key)
            if old is None:
                stats["entries_new"] += 1
                diet.insert_rows(conn, ds, meal, None, "xunji", [row], "migration", batch, [key], ts=ts)
                continue
            same = (old["meal"] == meal and abs(old["amount"] - g) < 1e-6 and old["name"] == row["name"]
                    and all(abs((old[m] or 0) - vals[m]) < 1e-6 for m in diet.MACROS))
            if same and old["deleted_at"] is None:
                stats["entries_same"] += 1
                continue
            if old["updated_at"] != old["created_at"] or old["deleted_at"] is not None:  # 导入以后在这里改过 / 删过：不动，列出来
                conflicts.append({"date": ds, "name": row["name"], "key": key, "grava": "deleted" if old["deleted_at"] else "edited"})
                continue
            stats["entries_updated"] += 1  # 训记里改过：跟着改（created_at 一起挪，还算「没在这里改过」）
            conn.execute("""UPDATE diet_entries SET meal=?, name=?, amount=?, unit=?, kcal=?, protein=?, carb=?, fat=?, per100=?, nutri_ref=?,
                updated_at=?, created_at=? WHERE id=?""", (meal, row["name"], g, unit, vals["kcal"], vals["protein"], vals["carb"], vals["fat"],
                                                           json.dumps(per100), row["nutri_ref"], ts, ts, old["id"]))
        for key, old in existing.items():
            if key in desired or old["deleted_at"] is not None:
                continue
            if old["updated_at"] != old["created_at"]:
                conflicts.append({"date": ds, "name": old["name"], "key": key, "grava": "edited, gone from Xunji"})
                continue
            stats["entries_removed"] += 1  # 训记里删了
            conn.execute("UPDATE diet_entries SET deleted_at=?, updated_at=? WHERE id=?", (ts, ts, old["id"]))
    # 导出范围里训记整天都没了的（之前导过）：也撤掉
    if lo and hi:
        for old in conn.execute("SELECT * FROM diet_entries WHERE source='xunji' AND deleted_at IS NULL AND date BETWEEN ? AND ?", (lo, hi)).fetchall():
            if old["date"] in seen_days:
                continue
            if old["updated_at"] != old["created_at"]:
                conflicts.append({"date": old["date"], "name": old["name"], "key": old["source_ref"], "grava": "edited, gone from Xunji"})
                continue
            stats["entries_removed"] += 1
            conn.execute("UPDATE diet_entries SET deleted_at=?, updated_at=? WHERE id=?", (ts, ts, old["id"]))
    # 营养目标
    runs = target_runs(days)
    have = {(r["effective_from"], r["source"]) for r in conn.execute("SELECT effective_from, source FROM diet_targets")}
    for run in runs:
        lim = run["limits"]
        if lim is None:
            continue
        if (run["from"], "xunji") in have:
            stats["targets_existing"] += 1
            continue
        stats["targets_new"] += 1
        conn.execute("INSERT INTO diet_targets(effective_from, kcal, protein, carb, fat, water_ml, kcal_derived, source, note, created_by, created_at) "
                     "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                     (run["from"], lim["protein"] * 4 + lim["carb"] * 4 + lim["fat"] * 9, lim["protein"], lim["carb"], lim["fat"], lim["water_ml"], 1, "xunji",
                      L(f"训记 foods.limits（{run['from']} → {run['to']}）", f"Xunji foods.limits ({run['from']} → {run['to']})"), "migration", ts))
    # 训记某天起目标变成 0（没设）：那一天插一行「不设目标」；后面接着按某个目标的话用 --current-target（同一天或更早起就不插）
    for i, run in enumerate(runs):
        if run["limits"] is None and i > 0 and runs[i - 1]["limits"] is not None and (run["from"], "xunji") not in have:
            if current and current["from"] <= run["from"]:
                continue
            stats["targets_cleared"] += 1
            conn.execute("INSERT INTO diet_targets(effective_from, source, note, created_by, created_at) VALUES(?,?,?,?,?)",
                         (run["from"], "xunji", L("训记从这天起没有设营养目标（limits 都是 0）", "No targets in Xunji from this day (limits all 0)"), "migration", ts))
    if current and (current["from"], "profile") not in have:
        stats["targets_current"] += 1
        c = current
        kcal = c.get("kcal") or c["protein"] * 4 + c["carb"] * 4 + c["fat"] * 9
        conn.execute("INSERT INTO diet_targets(effective_from, kcal, protein, carb, fat, water_ml, kcal_derived, source, note, created_by, created_at) "
                     "VALUES(?,?,?,?,?,?,?,?,?,?,?)", (c["from"], kcal, c["protein"], c["carb"], c["fat"], None, 0 if c.get("kcal") else 1, "profile",
                                                      c.get("note") or None, "migration", ts))
    # 食物库的「用了几次 / 最后一次」按现在的记录重算（新建时没设，insert_rows 每写一行加 1，撤掉的不算）
    conn.execute("""UPDATE diet_foods SET uses=(SELECT COUNT(*) FROM diet_entries e WHERE e.food_id=diet_foods.id AND e.deleted_at IS NULL),
        last_used=(SELECT MAX(date) FROM diet_entries e WHERE e.food_id=diet_foods.id AND e.deleted_at IS NULL) WHERE source='xunji'""")
    return {"batch": batch, "stats": dict(stats), "conflicts": conflicts, "skipped": skipped, "foods": len(foods), "runs": runs}


# —— 核对 ————————————————————————————————————————————————————————————————

def verify(conn, payload: dict) -> dict:
    """训记每天的合计（它自己算的 totals）对这里导进来的（source = xunji 的行）。别的来源的行（迁移以后在这里新记的）单独数。只碰 conn。"""
    days = {str(d["datestr"]): d for d in payload.get("days") or [] if isinstance(d, dict) and d.get("datestr")}
    lo, hi = payload.get("from"), payload.get("to")
    rows = conn.execute("SELECT * FROM diet_entries WHERE deleted_at IS NULL AND date BETWEEN ? AND ?", (lo, hi)).fetchall()
    tg = {ds: diet.targets_on(conn, ds) for ds in days}
    by_day: dict[str, list] = defaultdict(list)
    other: dict[str, list] = defaultdict(list)
    for r in rows:
        (by_day if r["source"] == "xunji" else other)[r["date"]].append(r)
    table = []
    for ds in sorted(set(days) | set(by_day)):
        d = days.get(ds) or {}
        recs = recs_of(d)
        t = d.get("totals") or {}
        xj = {"kcal": diet.num(t.get("totalCal")) or 0, "protein": diet.num(t.get("totalProtein")) or 0,
              "carb": diet.num(t.get("totalCarb")) or 0, "fat": diet.num(t.get("totalFat")) or 0}
        recalc = {m: sum(((ntr_of(r)[m] or 0) * (grams(r)[0] or 0) / 100) for r in recs) for m in diet.MACROS}
        gr = {m: sum((r[m] or 0) for r in by_day.get(ds, [])) for m in diet.MACROS}
        if not recs and not by_day.get(ds):
            continue
        diff = {m: gr[m] - xj[m] for m in diet.MACROS}
        ok = abs(diff["kcal"]) <= TOL_KCAL and all(abs(diff[m]) <= TOL_G for m in ("protein", "carb", "fat")) and len(recs) == len(by_day.get(ds, []))
        lim = limits_of(d)
        tgt = tg.get(ds)
        t_ok = None
        if lim:
            t_ok = bool(tgt) and all(abs((tgt or {}).get(m, 0) - round(lim[m])) <= 0.5 for m in ("protein", "carb", "fat"))
        table.append({"date": ds, "items_xunji": len(recs), "items_grava": len(by_day.get(ds, [])), "xunji": xj, "recalc": recalc, "grava": gr,
                      "diff": diff, "ok": ok, "limits": lim, "target": tgt, "target_ok": t_ok, "other": len(other.get(ds, []))})
    return {"days": table, "ok": all(x["ok"] for x in table), "mismatch": [x["date"] for x in table if not x["ok"]],
            "target_mismatch": [x["date"] for x in table if x["target_ok"] is False]}


# —— 报告 ————————————————————————————————————————————————————————————————

def f1(x: float) -> str:
    return diet.fmt(x, 1)


def report(payload: dict, imp: dict | None, ver: dict, commit: bool, path: Path) -> None:
    days = [d for d in payload.get("days") or [] if isinstance(d, dict)]
    recs = [r for d in days for r in recs_of(d)]
    with_recs = [d for d in days if recs_of(d)]
    water = sum(len((((d.get("foods") or {}).get("water") or {}).get("values") or [])) for d in days)
    tpl = payload.get("templates")
    tpl_n = len(tpl.get("templates") or tpl.get("list") or []) if isinstance(tpl, dict) and not tpl.get("error") else None
    uk = Counter("official" if str(r.get("uniquekey") or "").startswith("/") else "custom" if r.get("uniquekey") else "none" for r in recs)
    meals = Counter(str(r.get("meal_type") or "") for r in recs)
    v = ver
    lines = [f"# 训记 → {settings.app_name} 饮食记录迁移报告", "",
             f"- 生成：{now_iso()}（{'已写入' if commit else '预演，没有写入'}）",
             f"- 导出：{payload.get('exported_at')}，范围 {payload.get('from')} → {payload.get('to')}（训记接口窗口 {json.dumps(payload.get('window'), ensure_ascii=False)}）",
             f"- 训记里有记录的天：{len(with_recs)} 天，{len(recs)} 条（最早 {with_recs[0]['datestr'] if with_recs else '–'}，最晚 {with_recs[-1]['datestr'] if with_recs else '–'}）",
             f"- 食物：{uk['official']} 条用训记官方库，{uk['custom']} 条用自定义食物，{uk['none']} 条没有食物编号",
             f"- 餐次：{', '.join(f'{k} {n}' for k, n in meals.most_common())}", ""]
    if imp:
        s = imp["stats"]
        lines += ["## 导入", "",
                  f"- 食物库：新建 {s.get('foods_new', 0)} 样（官方库 {s.get('foods_official', 0)}、自定义 {s.get('foods_custom', 0)}），已有 {s.get('foods_existing', 0)} 样",
                  f"- 记录：新增 {s.get('entries_new', 0)} 条，没变 {s.get('entries_same', 0)} 条，按训记更新 {s.get('entries_updated', 0)} 条，训记里删了、这边撤掉 {s.get('entries_removed', 0)} 条；跳过 {len(imp['skipped'])} 条；"
                  f"在 {settings.app_name} 里改过 / 删过、没动的 {len(imp['conflicts'])} 条",
                  f"- 营养目标：训记的目标段 {s.get('targets_new', 0)} 行新写（已有 {s.get('targets_existing', 0)}），「从这天起不设目标」{s.get('targets_cleared', 0)} 行，"
                  f"当前目标 {s.get('targets_current', 0)} 行", f"- 这次写入的批号：`{imp['batch']}`", ""]
        if imp["skipped"]:
            lines += ["跳过的记录：", ""] + [f"- {x['date']} {x['name']}（{x['why']}）" for x in imp["skipped"]] + [""]
        if imp["conflicts"]:
            lines += ["没动的（这边改过）：", ""] + [f"- {x['date']} {x['name']}（{x['grava']}）" for x in imp["conflicts"]] + [""]
        lines += ["### 营养目标（训记按天存，连续一样的合成一段）", "", "| 从 | 到 | 蛋白质 | 碳水 | 脂肪 | 热量（4/4/9） | 喝水 |", "|---|---|---|---|---|---|---|"]
        for run in imp["runs"]:
            lim = run["limits"]
            if lim:
                lines.append(f"| {run['from']} | {run['to']} | {f1(lim['protein'])} | {f1(lim['carb'])} | {f1(lim['fat'])} | "
                             f"{f1(lim['protein'] * 4 + lim['carb'] * 4 + lim['fat'] * 9)} | {f1(lim['water_ml']) if lim['water_ml'] else '–'} |")
            else:
                lines.append(f"| {run['from']} | {run['to']} | 没设（0） | | | | |")
        lines.append("")
    ok_days = sum(1 for x in v["days"] if x["ok"])
    lines += ["## 逐天核对（训记每天的合计 vs 导入后的）", "",
              f"**{ok_days} / {len(v['days'])} 天一致**（热量差 ≤ {TOL_KCAL} kcal、三大营养素差 ≤ {TOL_G} g、条数相同）。"
              + ("" if v["ok"] else f" 对不上的：{', '.join(v['mismatch'])}"),
              "" if not v["target_mismatch"] else f"\n营养目标对不上的天：{', '.join(v['target_mismatch'])}", "",
              "「重算」= 按训记每条记录的每 100 g 营养 × 克数自己算一遍，用来看训记自己的合计对不对。", "",
              "| 日期 | 条数 训记 / 这里 | 训记 kcal / 蛋白 / 碳水 / 脂肪 | 这里 | 差 | 重算 kcal | 目标 | |",
              "|---|---|---|---|---|---|---|---|"]
    for x in v["days"]:
        xj, gr, df = x["xunji"], x["grava"], x["diff"]
        tg = x["target"]
        mark = ("✓" if x["ok"] else "✗") + (" 目标✗" if x["target_ok"] is False else "") + (f" +{x['other']} 条新记" if x["other"] else "")
        tg_s = f"{tg['kcal']} / {tg['protein']} / {tg['carb']} / {tg['fat']}" if tg else "–"
        lines.append(f"| {x['date']} | {x['items_xunji']} / {x['items_grava']} | {f1(xj['kcal'])} / {f1(xj['protein'])} / {f1(xj['carb'])} / {f1(xj['fat'])} | "
                     f"{f1(gr['kcal'])} / {f1(gr['protein'])} / {f1(gr['carb'])} / {f1(gr['fat'])} | "
                     f"{f1(df['kcal'])} / {f1(df['protein'])} / {f1(df['carb'])} / {f1(df['fat'])} | {f1(x['recalc']['kcal'])} | {tg_s} | {mark} |")
    lines += ["", "## 没导的", "",
              f"- 喝水记录：{water} 条（{settings.app_name} 现在不记喝水）",
              f"- 训记的饮食模板：{tpl_n if tpl_n is not None else '读不到'} 个（{settings.app_name} 里没有模板；饮食 Agent 的固定早餐配方在它的规则里）",
              "- 训记接口只给过去一年：更早的记录要在训记 App 里自己导出",
              "- 训记不存每一餐的时间：导进来的记录时间是空的", ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf8")
    path.chmod(0o600)


def parse_target(s: str | None) -> dict | None:
    if not s:
        return None
    parts = [diet.num(x) for x in re.split(r"[/,\s]+", s.strip()) if x]
    if len(parts) not in (3, 4) or any(p is None for p in parts):
        sys.exit(L("--current-target 写成 蛋白质/碳水/脂肪[/热量]，比如 136/240/80", "--current-target is protein/carb/fat[/kcal], e.g. 136/240/80"))
    return {"protein": parts[0], "carb": parts[1], "fat": parts[2], "kcal": parts[3] if len(parts) == 4 else None}


def main() -> int:
    p = argparse.ArgumentParser(description=L(__doc__, "Move the meal log over from Xunji: export (read-only) → import → day-by-day check → report."),
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export")
    e.add_argument("--from", dest="lo")
    e.add_argument("--to", dest="hi")
    e.add_argument("--out")
    i = sub.add_parser("import")
    i.add_argument("--file", required=True)
    i.add_argument("--commit", action="store_true")
    i.add_argument("--report")
    i.add_argument("--current-target")
    i.add_argument("--current-from")
    i.add_argument("--current-note")
    v = sub.add_parser("verify")
    v.add_argument("--file", required=True)
    v.add_argument("--report")
    a = p.parse_args()
    if a.cmd == "export":
        hi = date.fromisoformat(a.hi) if a.hi else diet.today()
        lo = date.fromisoformat(a.lo) if a.lo else hi - timedelta(days=365)
        print(export(lo, hi, Path(a.out).expanduser() if a.out else None))
        return 0
    payload = json.loads(Path(a.file).expanduser().read_text(encoding="utf8"))
    stamp = datetime.now(TZ).strftime("%Y%m%d-%H%M")
    if a.cmd == "import":
        cur = parse_target(a.current_target)
        if cur:
            if not a.current_from:
                sys.exit(L("--current-target 要配 --current-from（从哪天起）", "--current-target needs --current-from"))
            cur["from"] = diet.day_arg(a.current_from)
            cur["note"] = a.current_note
        t0 = time.time()
        with _lock:
            conn = diet.dietdb()
            try:
                imp = do_import(conn, payload, cur)
                ver = verify(conn, payload)
            except BaseException:
                conn.rollback()
                raise
            if a.commit:
                conn.commit()
            else:
                conn.rollback()
        path = Path(a.report).expanduser() if a.report else OUT_DIR / f"xunji-migration-report-{stamp}.md"
        report(payload, imp, ver, a.commit, path)
        print(json.dumps({"ok": ver["ok"], "commit": a.commit, "stats": imp["stats"], "batch": imp["batch"],
                          "days_checked": len(ver["days"]), "mismatch": ver["mismatch"], "target_mismatch": ver["target_mismatch"],
                          "skipped": len(imp["skipped"]), "conflicts": len(imp["conflicts"]), "report": str(path), "secs": round(time.time() - t0, 1)},
                         ensure_ascii=False, indent=1))
        return 0 if (ver["ok"] or not a.commit) else 1
    with _lock:
        ver = verify(diet.dietdb(), payload)
    path = Path(a.report).expanduser() if a.report else OUT_DIR / f"xunji-verify-{stamp}.md"
    report(payload, None, ver, True, path)
    print(json.dumps({"ok": ver["ok"], "days_checked": len(ver["days"]), "mismatch": ver["mismatch"], "target_mismatch": ver["target_mismatch"],
                      "report": str(path)}, ensure_ascii=False, indent=1))
    return 0 if ver["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
