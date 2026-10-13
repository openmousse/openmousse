"""饮食记录（2026-10-13）：吃了什么、每样多少、营养从哪来，外加营养目标和个人食物库。训练还在训记，饮食记在服务自己的库里。

表（服务的库；Grava 是 grava.db）
- diet_foods：个人食物库。每 100 g（饮品 100 ml，basis = ml）的 kcal / 蛋白质 / 碳水 / 脂肪（糖、纤维、饱和脂肪、盐放 extra），
  常用份量 portions（[{"label": "1 个（去壳）", "grams": 65}]），来源 source（xunji / off / cofid / usda / label 拍标签 / manual / estimate）、
  出处 source_ref（训记 uniquekey / 条码 / CoFID 编号 / fdcId）和一句说明 source_note，aliases = 别的叫法（训记里随手记的缩写也放这里）。
  被记过几次（uses）、最后一次（last_used）。删 = deleted_at，不真删。
- diet_entries：吃下去的一样一行。date（按用户时区的日历日）、meal（breakfast / lunch / preworkout / postworkout / dinner / snack）、
  time（HH:MM，可空）、name、amount + unit（g / ml）、portion（按份记的说明）、这一样的 kcal / protein / carb / fat、per100（记的那一刻的
  每 100 g 快照：改份量按它重算）、food_id（食物库里哪一样）。
  source = 怎么记的：plan 按建议（一键「记录这一餐」）/ chat 对话 / photo 照片 / manual 手动 / xunji 从训记导入；source_ref = 建议卡和第几餐
  （feed:<卡>#<餐>.<样>）/ 训记记录 id。nutri_source / nutri_ref = 营养从哪来（library / off / cofid / usda / label / estimate / plan / xunji）；
  estimated = 估算的（拍照估、外卖、查不到）。同一次写入的几行有同一个 batch，撤销按 batch。
- diet_targets：营养目标，一行 = 从 effective_from 那天起的目标（某天的目标 = 那天及以前最近的一行；四样都空的一行 = 从那天起不设目标）。
  server.json 的 nutrition_targets 还是能整个盖掉（手动设定）。
外部来源（CoFID、Open Food Facts、USDA）和它们的缓存在 nutrition.py。

查营养（/api/diet/lookup）的顺序：个人食物库 → 包装食品查 Open Food Facts（条码 / 名称）或拍标签 → CoFID → USDA；都查不到就由 Agent 估算并标「估算」。

数据源：server.json 的 diet.source = "local"（默认：这里的表）或 "xunji"（还从训记读：作者迁移前用）。/api/diet/day 两种都给同一个结构
（看板、今天页不用改）；写入接口只写这里的表。训记是可选数据源，OpenMousse 默认用自带的记录。

接口
  GET    /api/diet/day?date=               一天：totals、targets、item_count、meals[{label, key, time, kcal, protein, carb, fat, items}]
  GET    /api/diet/days?from=&to=          逐天合计（有记录的天）
  GET    /api/diet/entries?date=           这一天的每一行（带 id，改 / 删用）
  POST   /api/diet/entries                 记几样 {date?, meal, time?, source, items: [...], commit}（commit false = 预览：算好营养不写）
  PATCH  /api/diet/entries/{id}            改份量 / 餐次 / 时间 / 名字（改份量按 per100 重算）
  DELETE /api/diet/entries/{id}
  POST   /api/diet/undo {batch}            撤销同一次写入的几行
  POST   /api/diet/log-plan                一键「记录这一餐」：按饮食建议卡（feed_items 的 meal_plan）的 data 记，份量能改
  POST   /api/diet/log {feed, meal}        同上的简写（产出小板子用）：记这张卡的第几顿
  GET    /api/diet/foods?q=&recent=        个人食物库
  POST   /api/diet/foods                   加一样（或 {from: {source, ref}} 从 Open Food Facts / CoFID 的结果存进来）
  PATCH  /api/diet/foods/{id}  ·  DELETE /api/diet/foods/{id}
  GET    /api/diet/lookup?q=&en=&barcode=&packaged=   按上面的顺序查
  GET    /api/diet/targets?date=  ·  PUT /api/diet/targets
Agent 用 diet_ctl.py（走这些接口）。
"""
from __future__ import annotations

import json
import re
import sqlite3
import statistics
import threading
import uuid
from datetime import date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

import nutrition
import sources
from chat import _lock, db, log_activity, now_iso
from config import TZ, raw, settings
from i18n import L

router = APIRouter()

MEAL_KEYS = ("breakfast", "lunch", "preworkout", "postworkout", "dinner", "snack")
MEAL_ALIASES = {
    "早餐": "breakfast", "早饭": "breakfast", "breakfast": "breakfast", "morning": "breakfast",
    "午餐": "lunch", "午饭": "lunch", "中饭": "lunch", "lunch": "lunch", "noon": "lunch",
    "练前": "preworkout", "练前餐": "preworkout", "preworkout": "preworkout", "pre-workout": "preworkout",
    "练后": "postworkout", "练后餐": "postworkout", "postworkout": "postworkout", "post-workout": "postworkout",
    "晚餐": "dinner", "晚饭": "dinner", "dinner": "dinner", "evening": "dinner", "night": "dinner", "supper": "dinner",
    "加餐": "snack", "夜宵": "snack", "零食": "snack", "snack": "snack", "snacks": "snack", "extra": "snack",
}
SOURCES = ("plan", "chat", "photo", "manual", "xunji")
NUTRI_SOURCES = ("library", "off", "cofid", "usda", "label", "manual", "estimate", "plan", "xunji")
FOOD_SOURCES = ("xunji", "off", "cofid", "usda", "label", "manual", "estimate")
MACROS = ("kcal", "protein", "carb", "fat")
EXTRA = ("sugars", "fibre", "satfat", "salt")
UNITS = {"g": "g", "克": "g", "gram": "g", "grams": "g", "ml": "ml", "毫升": "ml"}
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
ESTIMATE_RE = re.compile(r"估算|估计|大约|estimate", re.I)
# 带这些字样的多半是包装食品（品牌、超市自有品牌）：自动模式下查名字也去问 Open Food Facts
BRANDS = re.compile(r"m&s|marks|tesco|sainsbury|waitrose|ocado|lidl|aldi|co-?op|asda|morrisons|alpro|huel|pret|belvita|weetabix|optimum|"
                    r"myprotein|danone|müller|muller|yeo|fage|oatly|heinz|kellogg|nestl|cadbury|innocent|graze|nature valley|quaker|\bon\b", re.I)

_ready = False
_xj_lock = threading.Lock()  # 训记模式：同一时间只查一次（训记同一接口 15 秒一次）


def dietdb() -> sqlite3.Connection:
    global _ready
    conn = db()
    if not _ready:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS diet_foods (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, aliases TEXT NOT NULL DEFAULT '[]', brand TEXT, basis TEXT NOT NULL DEFAULT 'g',
            kcal REAL, protein REAL, carb REAL, fat REAL, extra TEXT, portions TEXT NOT NULL DEFAULT '[]',
            source TEXT NOT NULL, source_ref TEXT, source_note TEXT, barcode TEXT, estimated INTEGER NOT NULL DEFAULT 0,
            uses INTEGER NOT NULL DEFAULT 0, last_used TEXT, created_by TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT);
        CREATE INDEX IF NOT EXISTS diet_foods_ref ON diet_foods(source, source_ref);
        CREATE TABLE IF NOT EXISTS diet_entries (
            id TEXT PRIMARY KEY, date TEXT NOT NULL, meal TEXT NOT NULL, time TEXT, name TEXT NOT NULL,
            amount REAL NOT NULL, unit TEXT NOT NULL DEFAULT 'g', portion TEXT,
            kcal REAL NOT NULL DEFAULT 0, protein REAL NOT NULL DEFAULT 0, carb REAL NOT NULL DEFAULT 0, fat REAL NOT NULL DEFAULT 0, per100 TEXT,
            food_id TEXT, source TEXT NOT NULL, source_ref TEXT, nutri_source TEXT, nutri_ref TEXT, estimated INTEGER NOT NULL DEFAULT 0,
            note TEXT, batch TEXT, created_by TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT);
        CREATE INDEX IF NOT EXISTS diet_entries_day ON diet_entries(date);
        CREATE INDEX IF NOT EXISTS diet_entries_ref ON diet_entries(source, source_ref);
        CREATE INDEX IF NOT EXISTS diet_entries_batch ON diet_entries(batch);
        CREATE TABLE IF NOT EXISTS diet_targets (
            id INTEGER PRIMARY KEY AUTOINCREMENT, effective_from TEXT NOT NULL, kcal REAL, protein REAL, carb REAL, fat REAL, water_ml REAL,
            kcal_derived INTEGER NOT NULL DEFAULT 0, source TEXT NOT NULL, note TEXT, created_by TEXT, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS diet_day_types (date TEXT PRIMARY KEY, day_type TEXT NOT NULL, source TEXT, updated_at TEXT NOT NULL);
        """)
        if "day_type" not in {r[1] for r in conn.execute("PRAGMA table_info(diet_targets)")}:
            conn.execute("ALTER TABLE diet_targets ADD COLUMN day_type TEXT")  # 碳循环（10/13）：NULL = 每天一样，train / rest = 训练日 / 休息日
        _ready = True
    return conn


# —— 小工具 ——————————————————————————————————————————————————————————

def bad(zh: str, en: str, code: int = 400) -> HTTPException:
    return HTTPException(code, L(zh, en))


def today() -> date:
    return datetime.now(TZ).date()


def day_arg(s: str | None) -> str:
    if not s:
        return today().isoformat()
    s = s.strip()
    if s in ("today", "今天"):
        return today().isoformat()
    if s in ("yesterday", "昨天"):
        return (today() - timedelta(days=1)).isoformat()
    if not DATE_RE.match(s):
        raise bad("日期要写成 YYYY-MM-DD", "Dates must be YYYY-MM-DD")
    try:
        date.fromisoformat(s)
    except ValueError as e:
        raise bad("日期不对", "Invalid date") from e
    return s


def time_arg(s: str | None) -> str | None:
    if not s:
        return None
    m = TIME_RE.match(s.strip())
    if not m:
        raise bad("时间要写成 HH:MM", "Times must be HH:MM")
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def meal_key(v: Any) -> str | None:
    s = re.sub(r"[（(].*$", "", str(v or "")).strip()  # 「练后（含晚餐）」→ 练后
    low = s.lower()
    if low.endswith("-added"):  # 训记的「上午 / 下午 / 晚上加餐」
        return "snack"
    return MEAL_ALIASES.get(low) or MEAL_ALIASES.get(s)


def meal_label(key: str) -> str:
    return {"breakfast": L("早餐", "Breakfast"), "lunch": L("午餐", "Lunch"), "dinner": L("晚餐", "Dinner"), "preworkout": L("练前", "Pre-workout"),
            "postworkout": L("练后", "Post-workout"), "snack": L("加餐", "Snack")}.get(key) or L("其他", "Other")


def num(v: Any) -> float | None:
    if v is None or v == "" or isinstance(v, bool):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x else None  # NaN


def fmt(x: float | None, nd: int = 1) -> str:
    if x is None:
        return "–"
    v = round(float(x), nd)
    return str(int(v)) if v == int(v) else f"{v:.{nd}f}".rstrip("0").rstrip(".")


def unit_of(u: str | None, default: str = "g") -> str | None:
    if not u:
        return default
    return UNITS.get(u.strip().lower())


def jl(s: str | None, default: Any) -> Any:
    try:
        v = json.loads(s) if s else default
    except ValueError:
        return default
    return v if isinstance(v, type(default)) else default


def per100_of(d: dict | None) -> dict:
    d = d or {}
    out = {k: num(d.get(k)) for k in MACROS}
    for k in EXTRA:
        if num(d.get(k)) is not None:
            out[k] = num(d.get(k))
    return out


def source_mode() -> str:
    """local（默认）/ xunji。写成 xunji 但训记的脚本没接上，也只能是 local。"""
    c = raw().get("diet")
    m = str((c or {}).get("source") or "local").strip().lower() if isinstance(c, dict) else "local"
    return "xunji" if m == "xunji" and sources.xunji is not None else "local"


def source_label() -> str:
    return sources.xunji_name() if source_mode() == "xunji" else L("饮食记录", "Meal log")


def actor_of(by: str | None) -> str:
    b = (by or "").strip()
    return b[:40] if b else "leo"


# —— 食物库 ————————————————————————————————————————————————————————————

def food_out(r: sqlite3.Row) -> dict:
    extra = jl(r["extra"], {})
    per100 = {k: r[k] for k in MACROS} | {k: extra[k] for k in EXTRA if extra.get(k) is not None}
    return {"id": r["id"], "name": r["name"], "aliases": jl(r["aliases"], []), "brand": r["brand"], "basis": r["basis"], "per100": per100,
            "portions": jl(r["portions"], []), "source": r["source"], "sourceRef": r["source_ref"], "sourceNote": r["source_note"],
            "barcode": r["barcode"], "estimated": bool(r["estimated"]), "uses": r["uses"], "lastUsed": r["last_used"],
            "createdAt": r["created_at"], "updatedAt": r["updated_at"]}


def norm(s: str | None) -> str:
    return re.sub(r"\s+", "", str(s or "")).lower()


def find_food(conn: sqlite3.Connection, ref: str | None) -> sqlite3.Row | None:
    """食物库里的一样：id，或者名字 / 别名完全对上（不分大小写、不看空格）；同名的有几样就取用得多的那样。"""
    if not ref:
        return None
    ref = str(ref).strip()
    if ref.startswith("f-"):
        r = conn.execute("SELECT * FROM diet_foods WHERE id=? AND deleted_at IS NULL", (ref,)).fetchone()
        if r:
            return r
    key = norm(ref)
    hits = [r for r in conn.execute("SELECT * FROM diet_foods WHERE deleted_at IS NULL") if norm(r["name"]) == key or key in {norm(a) for a in jl(r["aliases"], [])}]
    if len(hits) > 1:  # 同名多样：用得多的那样
        hits.sort(key=lambda r: (-(r["uses"] or 0), r["created_at"]))
    return hits[0] if hits else None


def search_foods(conn: sqlite3.Connection, q: str | None, limit: int = 20, recent_days: int | None = None) -> list[dict]:
    rows = conn.execute("SELECT * FROM diet_foods WHERE deleted_at IS NULL").fetchall()
    if recent_days:
        since = (today() - timedelta(days=recent_days)).isoformat()
        rows = [r for r in rows if (r["last_used"] or "") >= since]
    key = norm(q)
    if key:
        scored = []
        for r in rows:
            names = [norm(r["name"]), *(norm(a) for a in jl(r["aliases"], []))]
            if key in names:
                s = 3
            elif any(n.startswith(key) for n in names):
                s = 2
            elif any(key in n for n in names) or (r["brand"] and key in norm(r["brand"])):
                s = 1
            else:
                continue
            scored.append((s, r))
        scored.sort(key=lambda x: (-x[0], -(x[1]["uses"] or 0), x[1]["name"]))
        rows = [r for _, r in scored]
    else:  # 用得多的在前，一样多的最近用过的在前
        rows.sort(key=lambda r: r["name"])
        rows.sort(key=lambda r: r["last_used"] or "", reverse=True)
        rows.sort(key=lambda r: -(r["uses"] or 0))
    return [food_out(r) for r in rows[:limit]]


def clean_portions(v: Any) -> list[dict]:
    out = []
    for p in v or []:
        if not isinstance(p, dict):
            continue
        label, grams = str(p.get("label") or "").strip()[:40], num(p.get("grams"))
        if label and grams and grams > 0:
            out.append({"label": label, "grams": round(grams, 1)})
    return out[:12]


def save_food(conn: sqlite3.Connection, f: dict, by: str) -> tuple[str, bool]:
    """写一样进食物库。同一个出处（source + source_ref）已经有了就更新它（别名合并），返回 (id, 新建了没有)。只碰 conn。"""
    name = str(f.get("name") or "").strip()[:80]
    if not name:
        raise bad("食物要有名字", "A food needs a name")
    per100 = {k: (round(v, 3) if v is not None else None) for k, v in per100_of(f.get("per100")).items()}
    if any(per100.get(k) is None for k in MACROS):
        raise bad(f"「{name}」要有每 100 g 的热量、蛋白质、碳水、脂肪四样", f'"{name}" needs kcal, protein, carb and fat per 100 g')
    if any((per100[k] or 0) < 0 for k in MACROS) or (per100["kcal"] or 0) > 950:
        raise bad(f"「{name}」的营养数字不对", f'The nutrition numbers for "{name}" look wrong')
    source = str(f.get("source") or "manual")
    if source not in FOOD_SOURCES:
        raise bad(f"来源只能是 {' / '.join(FOOD_SOURCES)}", f"source must be one of {', '.join(FOOD_SOURCES)}")
    basis = unit_of(f.get("basis") or "g") or "g"
    ref = str(f.get("source_ref") or f.get("sourceRef") or "").strip() or None
    aliases = [a for a in dict.fromkeys(str(x).strip()[:80] for x in (f.get("aliases") or []) if str(x).strip()) if norm(a) != norm(name)]
    extra = {k: per100[k] for k in EXTRA if per100.get(k) is not None}
    ts = now_iso()
    old = conn.execute("SELECT * FROM diet_foods WHERE source=? AND source_ref=? AND deleted_at IS NULL", (source, ref)).fetchone() if ref else None
    if old is None and not ref:  # 手动、拍标签、估算的没有出处编号：同一个来源、同一个名字的就是同一样，更新它（Agent 重复加不会出两份）
        old = next((r for r in conn.execute("SELECT * FROM diet_foods WHERE source=? AND source_ref IS NULL AND deleted_at IS NULL", (source,))
                    if norm(r["name"]) == norm(name)), None)
    if old:
        merged = [a for a in dict.fromkeys([*jl(old["aliases"], []), *aliases, *([old["name"]] if norm(old["name"]) != norm(name) else [])]) if norm(a) != norm(name)]
        portions = clean_portions(f.get("portions")) or jl(old["portions"], [])
        conn.execute("""UPDATE diet_foods SET name=?, aliases=?, brand=COALESCE(?, brand), basis=?, kcal=?, protein=?, carb=?, fat=?, extra=?, portions=?,
            source_note=COALESCE(?, source_note), barcode=COALESCE(?, barcode), estimated=?, updated_at=? WHERE id=?""",
                     (name, json.dumps(merged, ensure_ascii=False), f.get("brand"), basis, per100["kcal"], per100["protein"], per100["carb"], per100["fat"],
                      json.dumps(extra), json.dumps(portions, ensure_ascii=False), f.get("source_note") or f.get("sourceNote"), f.get("barcode"),
                      1 if f.get("estimated") else 0, ts, old["id"]))
        return old["id"], False
    fid = f"f-{uuid.uuid4().hex[:8]}"
    conn.execute("""INSERT INTO diet_foods(id, name, aliases, brand, basis, kcal, protein, carb, fat, extra, portions, source, source_ref, source_note,
        barcode, estimated, uses, last_used, created_by, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                 (fid, name, json.dumps(aliases, ensure_ascii=False), f.get("brand"), basis, per100["kcal"], per100["protein"], per100["carb"], per100["fat"],
                  json.dumps(extra), json.dumps(clean_portions(f.get("portions")), ensure_ascii=False), source, ref,
                  (str(f.get("source_note") or f.get("sourceNote") or "").strip() or None), f.get("barcode"), 1 if f.get("estimated") else 0,
                  int(num(f.get("uses")) or 0), f.get("last_used"), by, f.get("created_at") or ts, ts))
    return fid, True


def candidate_note(c: dict) -> str:
    if c["source"] == "off":
        return f"Open Food Facts（ODbL）{c.get('ref') or ''}{'，按冲调后' if c.get('prepared') else ''}".strip()
    if c["source"] == "cofid":
        return f"CoFID 2021 {c.get('ref') or ''}：{c.get('name') or ''}".strip()
    if c["source"] == "usda":
        return f"USDA FoodData Central {c.get('ref') or ''}：{c.get('name') or ''}".strip()
    return str(c.get("source") or "")


def fetch_candidate(source: str, ref: str) -> dict:
    """把 lookup 的一个结果按出处再拿一遍（Agent 只要报出处，不用自己抄数字）。"""
    try:
        if source == "off":
            c = nutrition.off_barcode(ref)
        elif source == "cofid":
            c = nutrition.cofid_get(ref)
        elif source == "usda":
            c = nutrition.usda_food(ref)
        else:
            raise bad("from.source 只能是 off / cofid / usda", "from.source must be off, cofid or usda")
    except nutrition.SourceError as e:
        raise bad(f"没查成：{e}", f"Lookup failed: {e}", 502) from e
    if not c:
        raise bad(f"{source} 里没有 {ref}", f"{ref} not found in {source}", 404)
    if not c.get("complete"):
        raise bad(f"{source} 的 {ref} 营养数据不全（缺热量或三大营养素），拍标签或换一个", f"{source} {ref} is missing nutrition data; use the label or another match")
    return c


# —— 营养目标 ——————————————————————————————————————————————————————————

def target_out(r: sqlite3.Row | None) -> dict | None:
    if r is None or all(r[k] is None for k in ("kcal", "protein", "carb", "fat")):
        return None
    p, c, f = (r["protein"] or 0), (r["carb"] or 0), (r["fat"] or 0)
    kcal = r["kcal"] if r["kcal"] is not None else p * 4 + c * 4 + f * 9
    src = r["source"]
    label = sources.xunji_name() if src == "xunji" else (L("手动设定", "Set manually") if src in ("leo", "app", "manual") else
                                                         L("档案", "Profile") if src == "profile" else L("Agent 设定", "Set by an Agent"))
    out = {"kcal": round(kcal), "protein": round(p), "carb": round(c), "fat": round(f), "water_ml": round(r["water_ml"]) if r["water_ml"] else None,
           "source": label, "kcal_derived": bool(r["kcal_derived"]), "since": r["effective_from"]}
    if "day_type" in r.keys() and r["day_type"] in DAY_TYPES:
        out["for"] = r["day_type"]
    return out


DAY_TYPES = ("train", "rest")


def day_type_label(t: str | None) -> str:
    return {"train": L("训练日", "Training day"), "rest": L("休息日", "Rest day")}.get(t or "", "")


def day_type_of(conn: sqlite3.Connection, d: str) -> tuple[str, str]:
    """碳循环里这一天算训练日还是休息日，和怎么定的：标过的（Agent 或用户，diet_day_types）> 日程里排了训练
    （Agent 的幂等键带 :training:，比如健身 Agent 的 fitness:training:<日期>，没删、没标「没去」）> 休息日。"""
    r = conn.execute("SELECT day_type FROM diet_day_types WHERE date=?", (d,)).fetchone()
    if r and r["day_type"] in DAY_TYPES:
        return r["day_type"], "set"
    try:
        if conn.execute("""SELECT 1 FROM schedule_items WHERE date=? AND deleted_at IS NULL AND key LIKE '%:training:%'
                AND IFNULL(attended, 1) != 0 LIMIT 1""", (d,)).fetchone():
            return "train", "schedule"
    except sqlite3.Error:  # 没有日程表的实例
        pass
    return "rest", "default"


def targets_on(conn: sqlite3.Connection, d: str) -> dict | None:
    """某天的目标：server.json 的 nutrition_targets（整个盖掉）> 那天及以前最近的一组（同一个生效日期的几行）。
    那一组分了训练日 / 休息日（碳循环）就按这一天的类型取，并带上 day_type、day_type_by（set / schedule / default）和 cycle（两套都给）。"""
    override = settings.nutrition_targets()
    if override:
        return {**override, "source": L("手动设定", "Set manually")}
    head = conn.execute("SELECT effective_from FROM diet_targets WHERE effective_from <= ? ORDER BY effective_from DESC, id DESC LIMIT 1", (d,)).fetchone()
    if not head:
        return None
    by: dict[str | None, sqlite3.Row] = {}
    for r in conn.execute("SELECT * FROM diet_targets WHERE effective_from=? ORDER BY id", (head[0],)):
        by[r["day_type"] if r["day_type"] in DAY_TYPES else None] = r  # 同一类后写的算
    if not any(k in by for k in DAY_TYPES):
        return target_out(by.get(None))
    t, how = day_type_of(conn, d)
    out = target_out(by.get(t) or by.get(None) or by.get("rest" if t == "train" else "train"))
    if out is None:
        return None
    return {**out, "day_type": t, "day_type_label": day_type_label(t), "day_type_by": how,
            "cycle": {k: target_out(by[k]) for k in DAY_TYPES if k in by}}


# —— 记录 ——————————————————————————————————————————————————————————————

def entry_out(r: sqlite3.Row) -> dict:
    return {"id": r["id"], "date": r["date"], "meal": r["meal"], "mealLabel": meal_label(r["meal"]), "time": r["time"], "name": r["name"],
            "amount": float(fmt(r["amount"], 1)), "unit": r["unit"], "portion": r["portion"],
            "kcal": round(r["kcal"]), "protein": round(r["protein"], 1), "carb": round(r["carb"], 1), "fat": round(r["fat"], 1),
            "per100": jl(r["per100"], {}), "foodId": r["food_id"], "source": r["source"], "sourceRef": r["source_ref"],
            "nutriSource": r["nutri_source"], "nutriRef": r["nutri_ref"], "estimated": bool(r["estimated"]), "note": r["note"], "batch": r["batch"],
            "createdBy": r["created_by"], "createdAt": r["created_at"]}


def item_light(r: sqlite3.Row) -> dict:
    """/api/diet/day 里的一样：老结构（name / amount / unit / kcal）再加几个字段。"""
    return {"id": r["id"], "name": r["name"], "amount": float(fmt(r["amount"], 1)), "unit": r["unit"], "kcal": round(r["kcal"]),
            "protein": round(r["protein"], 1), "carb": round(r["carb"], 1), "fat": round(r["fat"], 1), "time": r["time"],
            "source": r["source"], "estimated": bool(r["estimated"])}


def day_rows(conn: sqlite3.Connection, d: str) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM diet_entries WHERE date=? AND deleted_at IS NULL ORDER BY COALESCE(time, '99:99'), created_at, rowid", (d,)).fetchall()


def build_day(d: str, rows: list[sqlite3.Row], targets: dict | None) -> dict:
    meals: dict[str, dict] = {}
    for r in rows:
        key = r["meal"] if r["meal"] in MEAL_KEYS else "snack"
        m = meals.setdefault(key, {"label": meal_label(key), "key": key, "time": None, "kcal": 0.0, "protein": 0.0, "carb": 0.0, "fat": 0.0, "items": []})
        m["items"].append(item_light(r))
        for k in MACROS:
            m[k] += r[k] or 0
        if r["time"] and (not m["time"] or r["time"] < m["time"]):
            m["time"] = r["time"]
    ordered = []
    for k in MEAL_KEYS:
        if k in meals:
            m = meals[k]
            ordered.append({**m, "kcal": round(m["kcal"]), "protein": round(m["protein"], 1), "carb": round(m["carb"], 1), "fat": round(m["fat"], 1)})
    totals = {k: round(sum((r[k] or 0) for r in rows)) for k in MACROS}
    return {"ok": True, "source": source_label(), "date": d, "totals": totals, "targets": targets, "item_count": len(rows), "meals": ordered,
            "estimated_items": sum(1 for r in rows if r["estimated"])}


def local_day(d: str) -> dict:
    with _lock, dietdb() as conn:
        rows = day_rows(conn, d)
        targets = targets_on(conn, d)
    return build_day(d, rows, targets)


def resolve(conn: sqlite3.Connection, it: dict, default_unit: str = "g") -> dict:
    """一样要记的东西 → 名字、份量（g / ml）、每 100 的营养、这一样的营养、出处。认不出就抛 400，说清缺什么。只碰 conn。
    认的写法（按顺序）：food（食物库 id 或名字）+ amount 或 portion × count；per100 + amount；这一样的合计（kcal / protein / carb / fat）+ amount；
    只有 name：食物库里名字或别名对得上就用它。"""
    name = str(it.get("name") or "").strip()[:80]
    food = find_food(conn, it.get("food") or it.get("food_id") or it.get("foodId"))
    if food is None and not (it.get("per100") or num(it.get("kcal")) is not None) and name:
        food = find_food(conn, name)
    if (it.get("food") or it.get("food_id") or it.get("foodId")) and food is None:
        raise bad(f"食物库里没有「{it.get('food') or it.get('food_id') or it.get('foodId')}」", f'"{it.get("food") or it.get("food_id")}" is not in the food library', 404)
    count = num(it.get("count"))
    portion_label = str(it.get("portion") or "").strip()
    amount = num(it.get("amount"))
    unit = unit_of(it.get("unit"), (food["basis"] if food is not None else default_unit))
    portion_text = None
    if food is not None and (portion_label or (it.get("unit") and unit is None)):
        want = norm(portion_label or it.get("unit"))
        ps = jl(food["portions"], [])
        hit = next((p for p in ps if norm(p.get("label")) == want), None) or next((p for p in ps if want and want in norm(p.get("label"))), None)
        if not hit:
            labels = "、".join(p.get("label", "") for p in ps) or L("（没有）", "(none)")
            raise bad(f"「{food['name']}」没有「{portion_label or it.get('unit')}」这个份量，有的是：{labels}",
                      f'"{food["name"]}" has no portion "{portion_label or it.get("unit")}". Known portions: {labels}')
        n = count if count is not None else (amount if amount is not None and not portion_label else 1)
        amount, unit = round(n * float(hit["grams"]), 1), food["basis"]
        portion_text = hit["label"] if n == 1 else f"{hit['label']} × {fmt(n, 2)}"
    if unit is None:
        raise bad(f"「{name or (food['name'] if food is not None else '')}」的单位只能是 g 或 ml（或者食物库里记过的份量）",
                  f'Units must be g or ml (or a portion saved in the food library) for "{name}"')
    if amount is None or amount <= 0:
        raise bad(f"「{name or (food['name'] if food is not None else '')}」要有份量（克或毫升）", f'"{name}" needs an amount in g or ml')
    if amount > 5000:
        raise bad(f"「{name}」的份量 {fmt(amount)} 太大了", f'The amount for "{name}" ({fmt(amount)}) is too large')
    estimated = bool(it.get("estimated")) or bool(ESTIMATE_RE.search(name))
    nsrc, nref = it.get("nutri_source") or it.get("nutriSource"), it.get("nutri_ref") or it.get("nutriRef")
    if food is not None and not it.get("per100") and num(it.get("kcal")) is None:
        per100 = {k: food[k] for k in MACROS}
        nsrc, nref = "library", food["id"]
        estimated = estimated or bool(food["estimated"])
        name = name or food["name"]
    elif it.get("per100"):
        per100 = per100_of(it.get("per100"))
        if any(per100[k] is None for k in MACROS):
            raise bad(f"「{name}」的 per100 要有 kcal、protein、carb、fat 四样", f'per100 for "{name}" needs kcal, protein, carb and fat')
        nsrc = nsrc or ("library" if food is not None else "manual")
    elif num(it.get("kcal")) is not None:
        tot = {k: num(it.get(k)) for k in MACROS}
        missing = [k for k in MACROS if tot[k] is None]
        if missing:
            raise bad(f"「{name}」给了合计就要四样都给（缺 {', '.join(missing)}），或者给 per100", f'Totals for "{name}" need all four values (missing {", ".join(missing)}), or give per100')
        per100 = {k: tot[k] * 100 / amount for k in MACROS}  # type: ignore[operator]
        nsrc = nsrc or ("estimate" if estimated else "manual")
    else:
        raise bad(f"「{name}」没有营养数据：先用 lookup 查（食物库 → Open Food Facts → CoFID → USDA），存进食物库再记，或者给 per100 / 合计；都没有就估一个并标 estimated",
                  f'No nutrition data for "{name}": look it up first (library → Open Food Facts → CoFID → USDA) and save it, or give per100 / totals; '
                  "if nothing is found, estimate and mark it estimated")
    if not name:
        raise bad("每一样要有名字", "Every item needs a name")
    if nsrc and nsrc not in NUTRI_SOURCES:
        raise bad(f"nutri_source 只能是 {' / '.join(NUTRI_SOURCES)}", f"nutri_source must be one of {', '.join(NUTRI_SOURCES)}")
    if nsrc == "estimate":
        estimated = True
    vals = {k: (per100[k] or 0) * amount / 100 for k in MACROS}
    return {"name": name, "amount": amount, "unit": unit, "portion": portion_text, "per100": {k: per100[k] for k in MACROS},
            **vals, "food_id": food["id"] if food is not None else None, "nutri_source": nsrc, "nutri_ref": str(nref) if nref else None,
            "estimated": estimated, "note": (str(it.get("note") or "").strip()[:200] or None),
            "time": time_arg(it.get("time")) if it.get("time") else None, "meal": meal_key(it.get("meal")) if it.get("meal") else None}


def insert_rows(conn: sqlite3.Connection, d: str, meal: str, tm: str | None, source: str, rows: list[dict], by: str, batch: str,
                refs: list[str | None] | None = None, note: str | None = None, ts: str | None = None) -> list[str]:
    """写几行，食物库里用到的那几样 uses + 1。只碰 conn。"""
    ts = ts or now_iso()
    ids = []
    for i, x in enumerate(rows):
        eid = f"de-{uuid.uuid4().hex[:10]}"
        conn.execute("""INSERT INTO diet_entries(id, date, meal, time, name, amount, unit, portion, kcal, protein, carb, fat, per100, food_id, source,
            source_ref, nutri_source, nutri_ref, estimated, note, batch, created_by, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                     (eid, d, x.get("meal") or meal, x.get("time") or tm, x["name"], x["amount"], x["unit"], x.get("portion"),
                      x["kcal"], x["protein"], x["carb"], x["fat"], json.dumps({k: round(v, 3) if v is not None else None for k, v in x["per100"].items()}),
                      x.get("food_id"), source, (refs[i] if refs else None), x.get("nutri_source"), x.get("nutri_ref"), 1 if x.get("estimated") else 0,
                      x.get("note") or note, batch, by, ts, ts))
        if x.get("food_id"):
            conn.execute("UPDATE diet_foods SET uses=uses+1, last_used=MAX(IFNULL(last_used, ''), ?) WHERE id=?", (d, x["food_id"]))
        ids.append(eid)
    return ids


def sums(rows: list[dict]) -> dict:
    return {k: round(sum(x[k] for x in rows)) if k == "kcal" else round(sum(x[k] for x in rows), 1) for k in MACROS}


def preview_rows(rows: list[dict], d: str, meal: str) -> list[dict]:
    return [{"date": d, "meal": x.get("meal") or meal, "mealLabel": meal_label(x.get("meal") or meal), "name": x["name"], "amount": float(fmt(x["amount"], 1)),
             "unit": x["unit"], "portion": x.get("portion"), "kcal": round(x["kcal"]), "protein": round(x["protein"], 1), "carb": round(x["carb"], 1),
             "fat": round(x["fat"], 1), "per100": {k: round(v, 1) if v is not None else None for k, v in x["per100"].items()},
             "foodId": x.get("food_id"), "nutriSource": x.get("nutri_source"), "nutriRef": x.get("nutri_ref"), "estimated": bool(x.get("estimated"))}
            for x in rows]


def day_summary(d: str) -> dict:
    day = local_day(d)
    t = day.get("targets")
    return {"date": d, "totals": day["totals"], "targets": t, "remaining": {k: t[k] - day["totals"][k] for k in MACROS} if t else None,
            "meals": [m["label"] for m in day["meals"]], "items": day["item_count"]}


class EntriesIn(BaseModel):
    date: str | None = None
    meal: str
    time: str | None = None
    source: str = "manual"
    source_ref: str | None = None
    items: list[dict]
    note: str | None = None
    commit: bool = True
    by: str | None = None


@router.post("/api/diet/entries")
def add_entries(body: EntriesIn):
    d, meal = day_arg(body.date), meal_key(body.meal)
    if not meal:
        raise bad("餐次只能是 早餐 / 午餐 / 练前 / 练后 / 晚餐 / 加餐", "meal must be breakfast, lunch, preworkout, postworkout, dinner or snack")
    if body.source not in SOURCES or body.source == "xunji":
        raise bad("source 只能是 plan / chat / photo / manual", "source must be plan, chat, photo or manual")
    if not body.items or len(body.items) > 30:
        raise bad("一次记 1–30 样", "Log 1–30 items at a time")
    tm = time_arg(body.time) or (datetime.now(TZ).strftime("%H:%M") if d == today().isoformat() else None)
    by = actor_of(body.by)
    with _lock, dietdb() as conn:
        rows = [resolve(conn, it) for it in body.items]
        if not body.commit:
            return {"ok": True, "preview": True, "date": d, "meal": meal, "mealLabel": meal_label(meal), "time": tm,
                    "entries": preview_rows(rows, d, meal), "adds": sums(rows), "estimated": sum(1 for x in rows if x["estimated"])}
        batch = f"b-{uuid.uuid4().hex[:8]}"
        refs = [body.source_ref and f"{body.source_ref}.{i}" for i in range(len(rows))] if body.source_ref else None
        ids = insert_rows(conn, d, meal, tm, body.source, rows, by, batch, refs, (body.note or "").strip()[:200] or None)
        out = [entry_out(r) for r in conn.execute(f"SELECT * FROM diet_entries WHERE id IN ({','.join('?' * len(ids))}) ORDER BY rowid", ids)]
    add = sums(rows)
    log_activity(L(f"记了{meal_label(meal)}：{len(rows)} 样，{add['kcal']} kcal", f"Logged {meal_label(meal).lower()}: {len(rows)} item(s), {add['kcal']} kcal"),
                 "diet", None if by == "leo" else by)
    return {"ok": True, "preview": False, "batch": batch, "entries": out, "adds": add, "day": day_summary(d)}


class EntryPatch(BaseModel):
    amount: float | None = None
    unit: str | None = None
    meal: str | None = None
    time: str | None = None
    date: str | None = None
    name: str | None = None
    note: str | None = None
    by: str | None = None


@router.patch("/api/diet/entries/{eid}")
def patch_entry(eid: str, body: EntryPatch):
    with _lock, dietdb() as conn:
        r = conn.execute("SELECT * FROM diet_entries WHERE id=? AND deleted_at IS NULL", (eid,)).fetchone()
        if not r:
            raise bad("没有这一条", "No such entry", 404)
        sets: dict[str, Any] = {}
        if body.amount is not None:
            if body.amount <= 0 or body.amount > 5000:
                raise bad("份量要在 0–5000 之间", "The amount must be between 0 and 5000")
            per100 = jl(r["per100"], {})
            if any(num(per100.get(k)) is None for k in MACROS):  # 老行没有快照：按原来的比例缩放
                f = body.amount / r["amount"]
                sets.update({k: (r[k] or 0) * f for k in MACROS})
            else:
                sets.update({k: float(per100[k]) * body.amount / 100 for k in MACROS})
            sets["amount"] = body.amount
            sets["portion"] = None
        if body.unit is not None:
            u = unit_of(body.unit, "")
            if not u:
                raise bad("单位只能是 g 或 ml", "Units must be g or ml")
            sets["unit"] = u
        if body.meal is not None:
            m = meal_key(body.meal)
            if not m:
                raise bad("餐次不对", "Unknown meal")
            sets["meal"] = m
        if body.time is not None:
            sets["time"] = time_arg(body.time) if body.time else None
        if body.date is not None:
            sets["date"] = day_arg(body.date)
        if body.name is not None and body.name.strip():
            sets["name"] = body.name.strip()[:80]
        if body.note is not None:
            sets["note"] = body.note.strip()[:200] or None
        if not sets:
            return {"ok": True, "entry": entry_out(r), "changed": False}
        sets["updated_at"] = now_iso()
        conn.execute(f"UPDATE diet_entries SET {', '.join(f'{k}=?' for k in sets)} WHERE id=?", (*sets.values(), eid))
        r2 = conn.execute("SELECT * FROM diet_entries WHERE id=?", (eid,)).fetchone()
    return {"ok": True, "entry": entry_out(r2), "changed": True, "day": day_summary(r2["date"])}


@router.delete("/api/diet/entries/{eid}")
def delete_entry(eid: str, by: str | None = None):
    with _lock, dietdb() as conn:
        r = conn.execute("SELECT * FROM diet_entries WHERE id=? AND deleted_at IS NULL", (eid,)).fetchone()
        if not r:
            raise bad("没有这一条", "No such entry", 404)
        conn.execute("UPDATE diet_entries SET deleted_at=?, updated_at=? WHERE id=?", (now_iso(), now_iso(), eid))
        if r["food_id"]:
            conn.execute("UPDATE diet_foods SET uses=MAX(uses-1, 0) WHERE id=?", (r["food_id"],))
    log_activity(L(f"删了一条饮食记录（{r['date']} {meal_label(r['meal'])}）", f"Deleted a meal log entry ({r['date']} {meal_label(r['meal']).lower()})"),
                 "diet", None if actor_of(by) == "leo" else actor_of(by))
    return {"ok": True, "deleted": eid, "day": day_summary(r["date"])}


class UndoIn(BaseModel):
    batch: str
    by: str | None = None


@router.post("/api/diet/undo")
def undo(body: UndoIn):
    with _lock, dietdb() as conn:
        rows = conn.execute("SELECT * FROM diet_entries WHERE batch=? AND deleted_at IS NULL", (body.batch,)).fetchall()
        if not rows:
            raise bad("这一批已经撤销了，或者没有这一批", "Nothing to undo for this batch", 404)
        ts = now_iso()
        conn.execute("UPDATE diet_entries SET deleted_at=?, updated_at=? WHERE batch=? AND deleted_at IS NULL", (ts, ts, body.batch))
        for r in rows:
            if r["food_id"]:
                conn.execute("UPDATE diet_foods SET uses=MAX(uses-1, 0) WHERE id=?", (r["food_id"],))
    d = rows[0]["date"]
    log_activity(L(f"撤销了刚记的 {len(rows)} 样", f"Undid {len(rows)} logged item(s)"), "diet", None if actor_of(body.by) == "leo" else actor_of(body.by))
    return {"ok": True, "undone": len(rows), "day": day_summary(d)}


@router.get("/api/diet/entries")
def list_entries(date_: str | None = Query(None, alias="date"), from_: str | None = Query(None, alias="from"), to: str | None = None,
                 include_deleted: bool = False):
    with _lock, dietdb() as conn:
        if from_ or to:
            lo, hi = day_arg(from_ or to), day_arg(to or from_)
            rows = conn.execute("SELECT * FROM diet_entries WHERE date BETWEEN ? AND ?" + ("" if include_deleted else " AND deleted_at IS NULL") +
                                " ORDER BY date, COALESCE(time, '99:99'), created_at", (lo, hi)).fetchall()
        else:
            d = day_arg(date_)
            rows = conn.execute("SELECT * FROM diet_entries WHERE date=?" + ("" if include_deleted else " AND deleted_at IS NULL") +
                                " ORDER BY COALESCE(time, '99:99'), created_at", (d,)).fetchall()
    return {"ok": True, "entries": [entry_out(r) for r in rows]}


@router.get("/api/diet/days")
def days(from_: str | None = Query(None, alias="from"), to: str | None = None):
    hi = day_arg(to)
    lo = day_arg(from_) if from_ else (date.fromisoformat(hi) - timedelta(days=6)).isoformat()
    if lo > hi:
        lo, hi = hi, lo
    with _lock, dietdb() as conn:
        rows = conn.execute("""SELECT date, COUNT(*) n, SUM(kcal) kcal, SUM(protein) protein, SUM(carb) carb, SUM(fat) fat, SUM(estimated) est,
            GROUP_CONCAT(DISTINCT meal) meals FROM diet_entries WHERE deleted_at IS NULL AND date BETWEEN ? AND ? GROUP BY date ORDER BY date""", (lo, hi)).fetchall()
        tg = {r["date"]: targets_on(conn, r["date"]) for r in rows}
    return {"ok": True, "source": source_label(), "from": lo, "to": hi,
            "days": [{"date": r["date"], "items": r["n"], "kcal": round(r["kcal"] or 0), "protein": round(r["protein"] or 0), "carb": round(r["carb"] or 0),
                      "fat": round(r["fat"] or 0), "estimated_items": r["est"] or 0,
                      "meals": [m for m in MEAL_KEYS if m in (r["meals"] or "").split(",")], "targets": tg.get(r["date"])} for r in rows]}


# —— 一键「记录这一餐」 ——————————————————————————————————————————————————————

def base_name(name: str) -> str:
    """「熟大虾（2 盒）」→ 熟大虾：去掉括号里的份量说明，比名字用。"""
    return norm(re.sub(r"[（(][^）)]*[）)]", "", name or ""))


def similar_food(conn: sqlite3.Connection, name: str) -> sqlite3.Row | None:
    """名字对不上时找个「同类」：食物库里的名字是它的结尾（干意大利面 → 意大利面、King 大蓝莓 → 蓝莓），或它是库里名字的结尾
    （植物油 → vegetable 植物油）。中文、英文的中心词都在后面，所以只认结尾（鸡蛋面不会认成鸡蛋）。只用来估碳水和脂肪的比例，不当成同一样食物。"""
    b = base_name(name)
    if len(b) < 2:
        return None
    best, best_len = None, 1
    for r in conn.execute("SELECT * FROM diet_foods WHERE deleted_at IS NULL"):
        for n in [r["name"], *jl(r["aliases"], [])]:
            bn = base_name(n)
            if len(bn) < 2:
                continue
            k = len(bn) if b.endswith(bn) else len(b) if bn.endswith(b) else 0
            if k > best_len or (k == best_len and best is not None and (r["uses"] or 0) > (best["uses"] or 0)):
                best, best_len = r, k
    return best


def prior_cf(kcal: float, protein: float, amount: float, food: sqlite3.Row | None) -> tuple[float, float]:
    """一样东西的碳水 / 脂肪（克）的先验：它自己的「热量 − 蛋白质」那部分能量，按食物库里同类的比例分成碳水和脂肪；
    没有同类的：每克 7 kcal 以上的（油、黄油）全算脂肪，蛋白质占了七成以上的（虾、鸡胸）对半分，其余碳水六成。"""
    rest = max(kcal - 4 * protein, 0)
    if food is not None and ((food["carb"] or 0) * 4 + (food["fat"] or 0) * 9) > 0:
        share = (food["carb"] or 0) * 4 / ((food["carb"] or 0) * 4 + (food["fat"] or 0) * 9)
    elif amount > 0 and kcal / amount >= 7:
        share = 0.0
    elif kcal > 0 and protein * 4 >= 0.7 * kcal:
        share = 0.5
    else:
        share = 0.6
    return share * rest / 4, (1 - share) * rest / 9


def allocate(total: float, weights: list[float], caps: list[float]) -> tuple[list[float], float]:
    """把 total 按权重分下去，每一份不超过自己的上限（超了的封顶，多出来的再按权重分给别的）。返回 (每一份, 分不下去的余数)。"""
    n = len(weights)
    x = [0.0] * n
    free = [i for i in range(n) if caps[i] > 0]
    left = max(total, 0.0)
    while free and left > 1e-9:
        w = {i: max(weights[i], 0.0) for i in free}
        sw = sum(w.values())
        if sw <= 0:
            w, sw = {i: 1.0 for i in free}, float(len(free))
        over = [i for i in free if x[i] + left * w[i] / sw > caps[i] + 1e-9]
        if not over:
            for i in free:
                x[i] += left * w[i] / sw
            left = 0.0
            break
        for i in over:
            left -= caps[i] - x[i]
            x[i] = caps[i]
            free.remove(i)
    return x, max(left, 0.0)


def plan_rows(conn: sqlite3.Connection, meal: dict) -> tuple[list[dict], list[str]]:
    """建议卡的一餐 → 要记的几行（每样有每 100 的营养，改份量能重算）。
    卡片的每一样有 kcal、protein（新卡还有 carb、fat、food）。只有整餐写了碳水和脂肪的老卡：每一样先按 prior_cf 估，再按比例缩放到整餐的合计
    （每一样不超过它自己的重量），所以记下来的整餐合计和卡片上一样。名字和食物库完全对上的记成那样食物（food_id）。"""
    warnings: list[str] = []
    items = []
    for j, it in enumerate(meal.get("items") or []):
        if not isinstance(it, dict):
            continue
        name = str(it.get("name") or "").strip()
        amt = num(it.get("amount"))
        if not name or not amt or amt <= 0:
            warnings.append(L(f"「{name or '?'}」没有份量，没记", f'"{name or "?"}" has no amount and was skipped'))
            continue
        food = find_food(conn, it.get("food") or it.get("food_id")) or find_food(conn, name) or find_food(conn, re.sub(r"[（(][^）)]*[）)]", "", name).strip())
        like = food if food is not None else similar_food(conn, name)
        x = {"name": name, "amount": amt, "unit": unit_of(it.get("unit"), "g") or "g", "food": food, "like": like, "src": j,
             **{k: num(it.get(k)) for k in MACROS}}
        if x["kcal"] is None and food is not None:
            x["kcal"] = (food["kcal"] or 0) * amt / 100
        if x["protein"] is None and food is not None:
            x["protein"] = (food["protein"] or 0) * amt / 100
        if x["kcal"] is None:
            warnings.append(L(f"「{name}」没有热量，没记", f'"{name}" has no calories and was skipped'))
            continue
        x["protein"] = x["protein"] or 0.0
        x["estimated"] = bool(ESTIMATE_RE.search(name)) or bool(it.get("estimated")) or (food is not None and bool(food["estimated"]))
        x["prior"] = prior_cf(x["kcal"], x["protein"], amt, like)
        items.append(x)
    for n, k in enumerate(("carb", "fat")):
        need = [x for x in items if x[k] is None]
        if not need:
            continue
        total = num(meal.get(k))
        if total is None:  # 卡片连整餐的都没写：用先验（算估算）
            for x in need:
                x[k] = x["prior"][n]
                x["estimated"] = True
            continue
        rest = max(total - sum(x[k] for x in items if x[k] is not None), 0)
        caps = [max(x["amount"] - x["protein"] - ((x["carb"] or 0) if k == "fat" else 0), 0) for x in need]
        vals, short = allocate(rest, [x["prior"][n] for x in need], caps)
        for x, v in zip(need, vals):
            x[k] = v
        if short > 0.5:
            warnings.append(L(f"卡片上{meal.get('label') or ''}的{'碳水' if k == 'carb' else '脂肪'}比各样能装下的多 {fmt(short)} g，按能装下的记",
                              f"The card's {k} for {meal.get('label') or 'this meal'} is {fmt(short)} g more than the items can hold; logged what fits"))
    rows = []
    for x in items:
        amt = x["amount"]
        per100 = {k: x[k] * 100 / amt for k in MACROS}
        rows.append({"name": x["name"], "amount": amt, "unit": x["unit"], "portion": None, "per100": per100, **{k: x[k] for k in MACROS},
                     "food_id": x["food"]["id"] if x["food"] is not None else None, "nutri_source": "plan",
                     "nutri_ref": x["food"]["id"] if x["food"] is not None else None, "estimated": x["estimated"], "note": None, "time": None, "meal": None,
                     "src": x["src"]})
    return rows, warnings


class PlanLogIn(BaseModel):
    card: str | None = None
    meals: list[int | str] | None = None
    scale: float | None = None
    items: list[dict] | None = None
    date: str | None = None
    time: str | None = None
    again: bool = False
    commit: bool = True
    by: str | None = None


def plan_card(conn: sqlite3.Connection, card: str | None, d: str | None) -> sqlite3.Row:
    if card:
        r = conn.execute("SELECT * FROM feed_items WHERE id=?", (card,)).fetchone()
        if not r or (r["kind"] or "") != "meal_plan":
            raise bad("没有这张饮食建议卡", "No such meal plan card", 404)
        return r
    day = d or today().isoformat()
    r = conn.execute("SELECT * FROM feed_items WHERE kind='meal_plan' AND substr(created_at, 1, 10)=? ORDER BY created_at DESC LIMIT 1", (day,)).fetchone()
    if not r:
        raise bad(f"{day} 没有饮食建议卡", f"No meal plan card for {day}", 404)
    return r


@router.post("/api/diet/log-plan")
def log_plan(body: PlanLogIn):
    """按建议记：卡片的第几餐（从 0 数，或者餐名），份量可改（scale 整餐按比例；items: [{meal, item, amount}]，amount 0 = 这样没吃）。
    同一张卡的同一餐记过了就不再记（again: true 才再记一份）。默认记到卡片那一天，时间不给就是现在（今天的话）。"""
    if body.scale is not None and not (0 < body.scale <= 5):
        raise bad("scale 要在 0–5 之间", "scale must be between 0 and 5")
    with _lock, dietdb() as conn:
        try:
            card = plan_card(conn, body.card, day_arg(body.date) if body.date and not body.card else None)
        except sqlite3.OperationalError as e:  # 还没有 feed_items 表的新实例
            raise bad("还没有饮食建议卡", "No meal plan cards yet", 404) from e
        data = jl(card["data"], {})
        meals = [m for m in (data.get("meals") or []) if isinstance(m, dict)]
        if not meals:
            raise bad("这张卡没有结构化的餐（data.meals）", "This card has no structured meals (data.meals)")
        d = day_arg(body.date) if body.date else (str(card["created_at"] or "")[:10] or today().isoformat())
        logged = {int(m.group(1)) for (ref,) in conn.execute(
            "SELECT source_ref FROM diet_entries WHERE source='plan' AND deleted_at IS NULL AND source_ref LIKE ?", (f"feed:{card['id']}#%",))
            if (m := re.match(rf"^feed:{re.escape(card['id'])}#(\d+)\.", ref or ""))}
        if body.meals:
            want = []
            for m in body.meals:
                if isinstance(m, int) or (isinstance(m, str) and m.strip().isdigit()):
                    i = int(m)
                    if not 0 <= i < len(meals):
                        raise bad(f"这张卡只有 {len(meals)} 餐", f"This card only has {len(meals)} meals")
                    want.append(i)
                else:
                    k = meal_key(m)
                    hits = [i for i, x in enumerate(meals) if meal_key(x.get("label")) == k]
                    if not hits:
                        raise bad(f"这张卡里没有「{m}」", f'No "{m}" on this card')
                    nxt = [i for i in hits if i not in logged]
                    want.append(nxt[0] if nxt else hits[0])
        else:
            nxt = [i for i in range(len(meals)) if i not in logged]
            if not nxt:
                raise bad("这张卡上的餐都记过了", "Every meal on this card is already logged", 409)
            want = [nxt[0]]
        want = list(dict.fromkeys(want))
        already = [i for i in want if i in logged and not body.again]
        todo = [i for i in want if i not in already]
        if not todo:
            raise HTTPException(409, L(f"{'、'.join(str(meals[i].get('label') or '') for i in already)}已经记过了", "Already logged"))
        overrides: dict[tuple[int, int], float] = {}
        for o in body.items or []:
            mi, ii, a = num(o.get("meal")), num(o.get("item")), num(o.get("amount"))
            if mi is None or ii is None or a is None or a < 0:
                raise bad("items 里每一项要有 meal、item、amount", "Each items entry needs meal, item and amount")
            overrides[(int(mi), int(ii))] = a
        all_rows, warnings, refs, per_meal = [], [], [], []
        is_today = d == today().isoformat()  # 今天的：时间记成现在（吃完才点）；补记以前的：用卡片上这一餐的时间
        for i in todo:
            m = meals[i]
            key = meal_key(m.get("label")) or "snack"
            rows, warn = plan_rows(conn, m)
            warnings += warn
            kept = []
            for r in rows:
                j = r["src"]
                amt = r["amount"]
                if (i, j) in overrides:
                    amt = overrides[(i, j)]
                elif body.scale is not None:
                    amt = r["amount"] * body.scale
                if amt <= 0:
                    continue
                if amt != r["amount"]:
                    r = {**r, "amount": round(amt, 1), **{k: r["per100"][k] * round(amt, 1) / 100 for k in MACROS}}
                r["meal"] = key
                r["time"] = None if is_today else time_arg(str(m.get("time") or "")) if TIME_RE.match(str(m.get("time") or "").strip()) else None
                kept.append(r)
                refs.append(f"feed:{card['id']}#{i}.{j}")
            all_rows += kept
            per_meal.append({"index": i, "label": m.get("label"), "meal": key, "items": len(kept), **(sums(kept) if kept else {k: 0 for k in MACROS})})
        if not all_rows:
            raise bad("没有要记的（份量都改成 0 了）", "Nothing to log (every amount is 0)")
        tm = time_arg(body.time) or (datetime.now(TZ).strftime("%H:%M") if is_today else None)
        if body.time:
            for r in all_rows:
                r["time"] = None
        if not body.commit:
            return {"ok": True, "preview": True, "card": card["id"], "date": d, "time": tm, "meals": per_meal, "already": [meals[i].get("label") for i in already],
                    "entries": preview_rows(all_rows, d, all_rows[0]["meal"]), "adds": sums(all_rows), "warnings": warnings}
        batch = f"b-{uuid.uuid4().hex[:8]}"
        by = actor_of(body.by)
        ids = insert_rows(conn, d, all_rows[0]["meal"], tm, "plan", all_rows, by, batch, refs)
        out = [entry_out(r) for r in conn.execute(f"SELECT * FROM diet_entries WHERE id IN ({','.join('?' * len(ids))}) ORDER BY rowid", ids)]
    add = sums(all_rows)
    labels = "、".join(str(m["label"]) for m in per_meal)
    log_activity(L(f"按建议记了{labels}：{add['kcal']} kcal", f"Logged {labels} as planned: {add['kcal']} kcal"), "diet", None if by == "leo" else by)
    return {"ok": True, "preview": False, "card": card["id"], "batch": batch, "meals": per_meal, "already": [meals[i].get("label") for i in already],
            "entries": out, "adds": add, "warnings": warnings, "day": day_summary(d)}


class LogIn(BaseModel):
    feed: str
    meal: int | None = None
    by: str | None = None


@router.post("/api/diet/log")
def log_short(body: LogIn):
    """「记录这一餐」的简写（产出小板子按这个约定调）：{feed: 建议卡 id, meal: 第几顿（从 0 数；不给 = 第一顿还没记的）}，
    和 log-plan 记这一顿一样（返回 ok、batch、day…；记过了 409）。"""
    return log_plan(PlanLogIn(card=body.feed, meals=[body.meal] if body.meal is not None else None, by=body.by))


# —— 食物库接口 ——————————————————————————————————————————————————————————

@router.get("/api/diet/foods")
def foods(q: str | None = None, recent: int | None = None, limit: int = 40):
    with _lock, dietdb() as conn:
        out = search_foods(conn, q, max(1, min(limit, 500)), recent)
        n = conn.execute("SELECT COUNT(*) FROM diet_foods WHERE deleted_at IS NULL").fetchone()[0]
    return {"ok": True, "foods": out, "total": n}


class FoodIn(BaseModel):
    name: str | None = None
    aliases: list[str] | None = None
    brand: str | None = None
    basis: str | None = None
    per100: dict | None = None
    portions: list[dict] | None = None
    source: str | None = None
    source_ref: str | None = None
    source_note: str | None = None
    barcode: str | None = None
    estimated: bool = False
    # 从 lookup 的结果直接存：{"source": "off" | "cofid" | "usda", "ref": "…"}；name 不给就用它的名字
    from_: dict | None = Field(None, alias="from")
    by: str | None = None

    model_config = ConfigDict(populate_by_name=True)


@router.post("/api/diet/foods")
def add_food(body: FoodIn):
    f = body.model_dump()
    src = body.from_ or {}
    if src:
        c = fetch_candidate(str(src.get("source") or ""), str(src.get("ref") or ""))
        f = {**f, "per100": f.get("per100") or c["per100"], "basis": f.get("basis") or c["basis"], "source": c["source"], "source_ref": c["ref"],
             "source_note": f.get("source_note") or candidate_note(c), "brand": f.get("brand") or c.get("brand"),
             "barcode": f.get("barcode") or (c["ref"] if c["source"] == "off" else None),
             "portions": f.get("portions") or c.get("portions") or [], "name": f.get("name") or c["name"],
             "aliases": [*(f.get("aliases") or []), *([c["name"]] if f.get("name") and c["name"] and norm(c["name"]) != norm(f.get("name")) else [])]}
    if not f.get("source"):
        f["source"] = "estimate" if body.estimated else "manual"
    by = actor_of(body.by)
    with _lock, dietdb() as conn:
        fid, created = save_food(conn, f, by)
        r = conn.execute("SELECT * FROM diet_foods WHERE id=?", (fid,)).fetchone()
    log_activity(L(f"{'加' if created else '更新'}了食物库：{r['name']}", f"{'Added' if created else 'Updated'} a food: {r['name']}"), "diet", None if by == "leo" else by)
    return {"ok": True, "created": created, "food": food_out(r)}


class FoodPatch(BaseModel):
    name: str | None = None
    aliases: list[str] | None = None
    brand: str | None = None
    basis: str | None = None
    per100: dict | None = None
    portions: list[dict] | None = None
    source_note: str | None = None
    barcode: str | None = None
    estimated: bool | None = None
    by: str | None = None


@router.patch("/api/diet/foods/{fid}")
def patch_food(fid: str, body: FoodPatch):
    with _lock, dietdb() as conn:
        r = conn.execute("SELECT * FROM diet_foods WHERE id=? AND deleted_at IS NULL", (fid,)).fetchone()
        if not r:
            raise bad("食物库里没有这一样", "No such food", 404)
        sets: dict[str, Any] = {}
        if body.name is not None and body.name.strip():
            sets["name"] = body.name.strip()[:80]
        if body.aliases is not None:
            sets["aliases"] = json.dumps([a.strip()[:80] for a in dict.fromkeys(body.aliases) if a.strip()], ensure_ascii=False)
        if body.brand is not None:
            sets["brand"] = body.brand.strip() or None
        if body.basis is not None:
            b = unit_of(body.basis, "")
            if not b:
                raise bad("basis 只能是 g 或 ml", "basis must be g or ml")
            sets["basis"] = b
        if body.per100 is not None:
            p = per100_of(body.per100)
            for k in MACROS:
                if p.get(k) is not None:
                    sets[k] = p[k]
            extra = jl(r["extra"], {}) | {k: p[k] for k in EXTRA if p.get(k) is not None}
            sets["extra"] = json.dumps(extra)
        if body.portions is not None:
            sets["portions"] = json.dumps(clean_portions(body.portions), ensure_ascii=False)
        if body.source_note is not None:
            sets["source_note"] = body.source_note.strip() or None
        if body.barcode is not None:
            sets["barcode"] = body.barcode.strip() or None
        if body.estimated is not None:
            sets["estimated"] = 1 if body.estimated else 0
        if sets:
            sets["updated_at"] = now_iso()
            conn.execute(f"UPDATE diet_foods SET {', '.join(f'{k}=?' for k in sets)} WHERE id=?", (*sets.values(), fid))
        r2 = conn.execute("SELECT * FROM diet_foods WHERE id=?", (fid,)).fetchone()
    return {"ok": True, "food": food_out(r2), "changed": bool(sets)}


@router.delete("/api/diet/foods/{fid}")
def delete_food(fid: str):
    with _lock, dietdb() as conn:
        n = conn.execute("UPDATE diet_foods SET deleted_at=?, updated_at=? WHERE id=? AND deleted_at IS NULL", (now_iso(), now_iso(), fid)).rowcount
    if not n:
        raise bad("食物库里没有这一样", "No such food", 404)
    return {"ok": True, "deleted": fid}


# —— 查营养 ——————————————————————————————————————————————————————————————

def library_candidates(q: str, limit: int) -> list[dict]:
    with _lock, dietdb() as conn:
        found = search_foods(conn, q, limit)
    return [{"source": "library", "ref": f["id"], "name": f["name"], "brand": f["brand"], "basis": f["basis"], "per100": f["per100"],
             "portions": f["portions"], "complete": True, "uses": f["uses"], "note": f["sourceNote"], "estimated": f["estimated"],
             "origin": f["source"]} for f in found]


def lookup(q: str = "", en: str | None = None, barcode: str | None = None, packaged: bool | None = None, limit: int = 5,
           only: set[str] | None = None, everything: bool = False) -> dict:
    """查找顺序：个人食物库 → 包装食品（Open Food Facts：条码，或名字带品牌 / packaged）→ CoFID → USDA。
    默认查到一个来源有结果就停（everything 都查）。en = 给英文来源用的说法（CoFID、USDA 只有英文；不给且 q 是英文就用 q）。"""
    q = (q or "").strip()
    en = (en or "").strip() or (q if q and q.isascii() else "")
    want = only or {"library", "off", "cofid", "usda"}
    is_pack = packaged if packaged is not None else bool(barcode) or bool(BRANDS.search(q) or BRANDS.search(en))
    results: list[dict] = []
    tried: list[dict] = []

    def step(name: str, fn) -> bool:
        try:
            got = fn()
        except nutrition.SourceError as e:
            tried.append({"source": name, "ok": False, "error": str(e)})
            return False
        except Exception as e:  # noqa: BLE001 — 一个来源坏了不拖累别的
            tried.append({"source": name, "ok": False, "error": f"{type(e).__name__}: {str(e)[:120]}"})
            return False
        got = [g for g in (got or []) if g]
        tried.append({"source": name, "ok": True, "count": len(got)})
        results.extend(got)
        return bool(got)

    if "library" in want and (q or en):
        if step("library", lambda: library_candidates(q or en, limit)) and not everything:
            return finish(q, en, barcode, results, tried)
    if "off" in want and (barcode or is_pack):
        if barcode:
            hit = step("off", lambda: [nutrition.off_barcode(barcode)])
        else:
            hit = step("off", lambda: nutrition.off_search(en or q, limit))
        if hit and not everything:
            return finish(q, en, barcode, results, tried)
    elif "off" in want:
        tried.append({"source": "off", "ok": True, "skipped": L("不是包装食品（没给条码、名字里没有品牌）", "not a packaged food (no barcode or brand)")})
    if "cofid" in want and en:
        if step("cofid", lambda: nutrition.cofid_search(en, limit)) and not everything:
            return finish(q, en, barcode, results, tried)
    elif "cofid" in want:
        tried.append({"source": "cofid", "ok": True, "skipped": L("CoFID 只有英文名：给 en", "CoFID is English-only: pass en")})
    if "usda" in want and en:
        if not nutrition.usda_enabled():
            tried.append({"source": "usda", "ok": True, "skipped": L("没有 USDA 的 API key", "no USDA API key")})
        else:
            step("usda", lambda: nutrition.usda_search(en, limit))
    return finish(q, en, barcode, results, tried)


def finish(q: str, en: str, barcode: str | None, results: list[dict], tried: list[dict]) -> dict:
    best = next((r for r in results if r.get("complete")), None)
    return {"ok": True, "query": q, "en": en or None, "barcode": barcode, "best": best, "results": results, "tried": tried,
            "estimate": best is None,
            "hint": None if best else L("都没查到：按同类食物估一个，记的时候标「估算」（estimated）", "Nothing found: estimate from a similar food and mark it estimated")}


@router.get("/api/diet/lookup")
def lookup_route(q: str = "", en: str | None = None, barcode: str | None = None, packaged: bool | None = None, limit: int = 5,
                 sources_: str | None = Query(None, alias="sources"), everything: bool = Query(False, alias="all")):
    if not (q or en or barcode):
        raise bad("给 q（名字）、en（英文说法）或 barcode", "Give q (name), en (English name) or barcode")
    only = {s.strip() for s in sources_.split(",") if s.strip()} if sources_ else None
    return lookup(q, en, barcode, packaged, max(1, min(limit, 10)), only, everything)


@router.get("/api/diet/nutrition-sources")
def nutrition_sources():
    with _lock, dietdb() as conn:
        n = conn.execute("SELECT COUNT(*) FROM diet_foods WHERE deleted_at IS NULL").fetchone()[0]
    return {"ok": True, "library": {"foods": n}, **nutrition.status(), "mode": source_mode()}


# —— 营养目标接口 ——————————————————————————————————————————————————————————

@router.get("/api/diet/targets")
def targets(date_: str | None = Query(None, alias="date")):
    d = day_arg(date_)
    with _lock, dietdb() as conn:
        cur = targets_on(conn, d)
        hist = conn.execute("SELECT * FROM diet_targets ORDER BY effective_from DESC, id DESC LIMIT 50").fetchall()
    return {"ok": True, "date": d, "current": cur, "history": [{"from": r["effective_from"], **(target_out(r) or {"cleared": True}), "note": r["note"],
                                                                "by": r["created_by"], "at": r["created_at"],
                                                                **({"for": r["day_type"]} if r["day_type"] in DAY_TYPES else {})} for r in hist]}


class TargetsIn(BaseModel):
    day_type: str | None = None   # 碳循环：train / rest 只改训练日或休息日那一套；不给 = 每天一样
    protein: float | None = None
    carb: float | None = None
    fat: float | None = None
    kcal: float | None = None
    water_ml: float | None = None
    from_: str | None = Field(None, alias="from")
    note: str | None = None
    clear: bool = False
    by: str | None = None

    model_config = ConfigDict(populate_by_name=True)


@router.put("/api/diet/targets")
def set_targets(body: TargetsIn):
    """从某天起的目标。day_type = train / rest：碳循环里那一套；同一天没写另一套就把之前的另一套抄过来（两套总是成对）。"""
    d = day_arg(body.from_)
    by = actor_of(body.by)
    dt = (body.day_type or "").strip().lower() or None
    if dt not in (None, *DAY_TYPES):
        raise bad("day_type 只能是 train（训练日）或 rest（休息日）", "day_type must be train or rest")
    if body.clear:
        vals: dict[str, Any] = {"kcal": None, "protein": None, "carb": None, "fat": None, "water_ml": None, "kcal_derived": 0}
    else:
        if None in (body.protein, body.carb, body.fat):
            raise bad("目标要有蛋白质、碳水、脂肪三样（克）", "Targets need protein, carb and fat (g)")
        if any(x < 0 or x > 1000 for x in (body.protein, body.carb, body.fat)):  # type: ignore[operator]
            raise bad("目标的数字不对", "Target numbers look wrong")
        derived = body.kcal is None
        kcal = body.kcal if body.kcal is not None else body.protein * 4 + body.carb * 4 + body.fat * 9  # type: ignore[operator]
        vals = {"kcal": kcal, "protein": body.protein, "carb": body.carb, "fat": body.fat, "water_ml": body.water_ml, "kcal_derived": 1 if derived else 0}
    with _lock, dietdb() as conn:
        conn.execute("INSERT INTO diet_targets(effective_from, kcal, protein, carb, fat, water_ml, kcal_derived, source, note, created_by, created_at, day_type) "
                     "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (d, vals["kcal"], vals["protein"], vals["carb"], vals["fat"], vals["water_ml"], vals["kcal_derived"],
                                                        "leo" if by == "leo" else "agent", (body.note or "").strip()[:200] or None, by, now_iso(),
                                                        None if body.clear else dt))
        if dt and not body.clear:
            other = "rest" if dt == "train" else "train"
            if not conn.execute("SELECT 1 FROM diet_targets WHERE effective_from=? AND day_type=?", (d, other)).fetchone():
                prev = conn.execute("SELECT * FROM diet_targets WHERE effective_from < ? AND day_type=? ORDER BY effective_from DESC, id DESC LIMIT 1",
                                    (d, other)).fetchone()
                if prev:
                    conn.execute("INSERT INTO diet_targets(effective_from, kcal, protein, carb, fat, water_ml, kcal_derived, source, note, created_by, created_at, day_type) "
                                 "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (d, prev["kcal"], prev["protein"], prev["carb"], prev["fat"], prev["water_ml"], prev["kcal_derived"],
                                                                    prev["source"], prev["note"], prev["created_by"], now_iso(), other))
        cur = targets_on(conn, today().isoformat())
    log_activity(L(f"营养目标从 {d} 起改了{('（' + day_type_label(dt) + '）') if dt else ''}", f"Nutrition targets changed from {d}"),
                 "diet", None if by == "leo" else by)
    return {"ok": True, "from": d, "current": cur}


class DayTypeIn(BaseModel):
    date: str | None = None
    day_type: str | None = None   # train / rest；null = 不标，按日程推断
    by: str | None = None


@router.put("/api/diet/day-type")
def set_day_type(body: DayTypeIn):
    """碳循环：标这一天是训练日还是休息日（饮食 Agent 配餐前、临时加练或取消训练时）。null = 去掉标记，按日程推断。"""
    d = day_arg(body.date)
    t = (body.day_type or "").strip().lower() or None
    if t not in (None, *DAY_TYPES):
        raise bad("day_type 只能是 train（训练日）或 rest（休息日）", "day_type must be train or rest")
    with _lock, dietdb() as conn:
        if t:
            conn.execute("INSERT INTO diet_day_types(date, day_type, source, updated_at) VALUES(?,?,?,?) ON CONFLICT(date) DO UPDATE SET "
                         "day_type=excluded.day_type, source=excluded.source, updated_at=excluded.updated_at", (d, t, actor_of(body.by), now_iso()))
        else:
            conn.execute("DELETE FROM diet_day_types WHERE date=?", (d,))
        cur = targets_on(conn, d)
        t2, how = day_type_of(conn, d)
    return {"ok": True, "date": d, "day_type": t2, "day_type_by": how, "targets": cur}


# —— 一天（看板、今天页）—————————————————————————————————————————————————————

def xunji_grams(rec: dict) -> float | None:
    try:
        amount = float(rec.get("amount"))
    except (TypeError, ValueError):
        return None
    unit = (rec.get("unit") or "g").strip()
    if unit in ("g", "克", "ml", "毫升"):
        return amount
    for u in (rec.get("ntr") or {}).get("foodUnit") or []:
        if u.get("unit") == unit and u.get("gram"):
            return amount * float(u["gram"])
    return None


def xunji_targets(day: dict | None) -> dict | None:
    """训记把当天的营养目标放在 foods.limits 里（蛋白质 / 碳水 / 脂肪，克）；不存热量目标，按 4/4/9 换算。server.json 的 nutrition_targets 能覆盖。"""
    override = settings.nutrition_targets()
    if override:
        return {**override, "source": L("手动设定", "Set manually")}
    lim = ((day or {}).get("foods") or {}).get("limits") or {}
    try:
        p, c, f = float(lim.get("protein") or 0), float(lim.get("carb") or 0), float(lim.get("fat") or 0)
    except (TypeError, ValueError):
        return None
    if not (p and c and f):
        return None
    return {"kcal": round(p * 4 + c * 4 + f * 9), "protein": round(p), "carb": round(c), "fat": round(f), "source": sources.xunji_name(), "kcal_derived": True}


def xunji_day(d: date) -> dict:
    """还从训记读（diet.source = xunji）：和迁移前的 /api/diet/day 一模一样。"""
    xj = sources.xunji
    ttl = 600 if d >= today() else 12 * 3600  # 过去的日子基本不变
    try:
        with _xj_lock:
            data = xj.call("food_query", {"start_date": d.isoformat(), "end_date": d.isoformat(), "include_detail": True}, ttl=ttl)
    except xj.XunjiError as exc:
        raise HTTPException(502, L(f"训记读取失败：{exc}", f"Couldn't read Xunji: {exc}")) from exc
    day = next((x for x in (data.get("res") or {}).get("days") or [] if x.get("datestr") == d.isoformat()), None)
    totals = (day or {}).get("totals") or {}
    meals: dict[str, dict] = {}
    for rec in ((day or {}).get("foods") or {}).get("records") or []:
        key = meal_key(rec.get("meal_type")) or "other"
        g, ntr = xunji_grams(rec), rec.get("ntr") or {}
        kcal = round(float(ntr.get("cal") or 0) * g / 100) if g is not None else None
        protein = round(float(ntr.get("protein") or 0) * g / 100, 1) if g is not None else None
        meal = meals.setdefault(key, {"label": meal_label(key), "kcal": 0, "protein": 0.0, "items": []})
        meal["items"].append({"name": rec.get("name"), "amount": rec.get("amount"), "unit": rec.get("unit") or "g", "kcal": kcal})
        meal["kcal"] += kcal or 0
        meal["protein"] = round(meal["protein"] + (protein or 0), 1)
    ordered = [meals[k] for k in sorted(meals, key=lambda k: MEAL_KEYS.index(k) if k in MEAL_KEYS else 99)]
    return {"ok": True, "source": sources.xunji_name(), "date": d.isoformat(),
            "totals": {"kcal": round(totals.get("totalCal") or 0), "protein": round(totals.get("totalProtein") or 0),
                       "carb": round(totals.get("totalCarb") or 0), "fat": round(totals.get("totalFat") or 0)},
            "targets": xunji_targets(day), "item_count": (day or {}).get("item_count") or 0, "meals": ordered}


@router.get("/api/diet/day")
def day_view(d: str | None = Query(None, alias="date")):
    """看板和今天页读的一天。结构和迁移前一样（totals / targets / item_count / meals[{label, kcal, protein, items[{name, amount, unit, kcal}]}]），
    本地记录多给 key、time、carb、fat 和每一样的 id、来源、估算。"""
    dd = day_arg(d)
    if source_mode() == "xunji":
        return xunji_day(date.fromisoformat(dd))
    return local_day(dd)


def recent_library(days: int = 30, limit: int = 80) -> list[dict]:
    """给 Agent 配餐用的「常吃的」：近 days 天记过的，按次数排，带常用份量（中位数）和常在哪餐。"""
    since = (today() - timedelta(days=days - 1)).isoformat()
    with _lock, dietdb() as conn:
        rows = conn.execute("""SELECT e.name, e.food_id, e.amount, e.unit, e.meal, e.date, e.per100 FROM diet_entries e
            WHERE e.deleted_at IS NULL AND e.date >= ? ORDER BY e.date""", (since,)).fetchall()
        foods_by_id = {r["id"]: food_out(r) for r in conn.execute("SELECT * FROM diet_foods WHERE deleted_at IS NULL")}
    agg: dict[str, dict] = {}
    for r in rows:
        k = r["food_id"] or f"name:{r['name']}"
        e = agg.setdefault(k, {"food": foods_by_id.get(r["food_id"]) if r["food_id"] else None, "name": r["name"], "times": 0, "amounts": [],
                               "meals": {}, "last": "", "unit": r["unit"], "per100": jl(r["per100"], {})})
        e["times"] += 1
        e["amounts"].append(r["amount"])
        e["meals"][meal_label(r["meal"])] = e["meals"].get(meal_label(r["meal"]), 0) + 1
        e["last"] = max(e["last"], r["date"])
    out = []
    for e in sorted(agg.values(), key=lambda x: -x["times"])[:limit]:
        f = e["food"]
        out.append({"id": f["id"] if f else None, "name": f["name"] if f else e["name"], "logged_as": e["name"], "times": e["times"], "meals": e["meals"],
                    "typical_amount": statistics.median(e["amounts"]), "unit": e["unit"],
                    "per100": (f["per100"] if f else {k: (round(v, 1) if isinstance(v, (int, float)) else v) for k, v in e["per100"].items()}),
                    "portions": f["portions"] if f else [], "source": f["source"] if f else None, "last": e["last"]})
    return out


@router.get("/api/diet/library")
def library(days: int = 30, limit: int = 80):
    return {"ok": True, "days": days, "foods": recent_library(max(1, min(days, 365)), max(1, min(limit, 300)))}
