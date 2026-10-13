"""集合块（积木 type collection）：一张表，几种视图。参照 Notion「同一份数据，多种视图」，全部自己画。

- 配置：{"id", "type": "collection", "title", "after", "source": {"from": 表, "where": [...], "sort": [...]}, "card": 卡片,
  "views": [视图…], "view": 默认视图 id, "rowActions": [...], "edit": bool}。视图跟着看板配置存（boards 表、按版本），
  所以改视图和改积木是一回事：用户让改的 apply（看板顶上有撤回），Agent 自己想到的 propose（收件箱卡带预览）。
- 视图 type：table 表格 / board 看板 / calendar 日历 / timeline 时间线 / chart 图表。每个视图可以有自己的 where（写法和查询 Q
  一样，和 source 的 where 叠加）、sort、card；看板 group 分列（choice / bool / text 字段，groups 定列和顺序）；日历、时间线 date
  （时间线可加 start、range）；图表 charts 里 1–6 个，kind number / bar / hbar / line / donut，各自的 where、group（日期字段再加
  by、range；数字字段可加 bins）、agg + field（默认数行数）。也认 group_by / filter / date_field / value_field / start_field。
- 卡片 card：{title: 模板, meta: 模板（标题上面一行小字）, tags: [{text: 模板, tone, when: [[字段, 运算, 值]…]}]}，
  模板和列表的 row 一样写（字段名，或「{org} · {role}」）。
- 服务器算好每个视图要画的东西、按请求的语言写好文字；app 只画。GET /api/boards/{agent} 带每个视图的默认样子（行数有上限）；
  切月份、你在表格里点的排序和筛选、看板一列看全部、点柱形看明细走 POST /api/boards/{agent}/view（只读）。
- 拖卡片换列 = 改那一行 group 字段的值（app 直接 PATCH /api/rows/{id}）；只读来源（health:）不能拖、不能加 rowActions。
"""
from __future__ import annotations

import calendar as _cal
import json
import math
import re
import sqlite3
from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import boards
from boards import bad, bdb
from chat import _lock
from i18n import L, lang

router = APIRouter()

VIEW_TYPES = ("table", "board", "calendar", "timeline", "chart")
CHART_KINDS = ("number", "bar", "hbar", "line", "donut")
CHART_AGGS = ("count", "sum", "avg", "min", "max")
ALIASES = {"group_by": "group", "filter": "where", "date_field": "date", "value_field": "field", "start_field": "start"}
VIEW_TITLES = {"table": ("表格", "Table"), "board": ("看板", "Board"), "calendar": ("日历", "Calendar"),
               "timeline": ("时间线", "Timeline"), "chart": ("图表", "Charts")}
MAX_VIEWS, MAX_CHARTS, MAX_COLUMNS, MAX_TAGS, MAX_TABLE_FIELDS = 8, 6, 12, 4, 8
SERIES = 6                 # 分类色 6 个：按顺序用、不循环；排在后面的用中性色或合并为「其他」
INLINE = {"table": 30, "board": 6, "timeline": 8}    # 看板页上每个视图带多少行（全屏页再走 /view 要）
FULL = {"table": 200, "board": 100, "timeline": 100}
RANGE = re.compile(r"^([+-]?)(\d{1,3})([dwm])$")
GROUP_TYPES = ("choice", "bool", "text")            # 看板能按哪些字段分列：拖过去要能写回
DATE_TYPES = ("date", "datetime")
NUM_TYPES = ("number", "money")
TPL = re.compile(r"\{([a-z_][a-z0-9_]*)\}")
TITLE_KEYS = ("title", "name", "item", "role", "org", "what")   # 没写卡片标题时先找这些字段
MONTHS_EN = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
OTHER, EMPTY, BEFORE, AFTER = "*", "", "<", ">"      # 分组的 key：其他 / 没填 / 窗口之前 / 之后


# —— 校验（存之前）——————————————————————————————————————————————————————

def norm(raw: dict) -> dict:
    return {ALIASES.get(k, k): v for k, v in raw.items()}


def check_template(coll: dict, tpl: Any, what: str) -> str:
    """模板里写到的字段要在表里：写错了当场说，别等 app 里空着。"""
    s = str(tpl or "").strip()[:200]
    if not s:
        return ""
    keys = [s] if boards.KEY.match(s) or s in boards.SYSTEM else TPL.findall(s)
    for k in keys:
        try:
            boards.field_of(coll, k)
        except HTTPException:
            raise bad(f"{what}「{s}」里的「{k}」不是「{coll['name']}」的字段；有的是 {', '.join(f['key'] for f in coll['fields'])}",
                      f'{what} "{s}": "{k}" is not a field of "{coll["name"]}"; it has {", ".join(f["key"] for f in coll["fields"])}') from None
    return s


def check_conds(coll: dict, conds: Any, what: str) -> list[list]:
    if conds in (None, []):
        return []
    if isinstance(conds, list) and conds and not isinstance(conds[0], list):
        conds = [conds]  # 只写一条也行
    if not isinstance(conds, list):
        raise bad(f"{what} 写成 [[字段, 运算, 值], …]", f"{what} is [[field, op, value], …]")
    out = []
    for c in conds[:4]:
        if not isinstance(c, list) or len(c) < 2 or c[1] not in boards.OPS:
            raise bad(f"{what} 的一条 {c} 不对：[字段, 运算, 值]，运算是 {' '.join(boards.OPS)}",
                      f"Bad {what} clause {c}: [field, op, value], op is one of {' '.join(boards.OPS)}")
        boards.field_of(coll, str(c[0]))
        out.append([str(c[0]), c[1], *c[2:3]])
    return out


def check_where(coll: dict, where: Any) -> list:
    if where in (None, []):
        return []
    boards.where_sql(coll, where)  # 字段、运算、值写错了这里就 400
    return where


def check_sort(coll: dict, sort: Any) -> list[str]:
    keys = sort if isinstance(sort, list) else ([sort] if sort else [])
    keys = [str(k) for k in keys[:3]]
    boards.order_sql(coll, keys)
    return keys


def title_key(coll: dict) -> str:
    keys = {f["key"]: f for f in coll["fields"]}
    for k in TITLE_KEYS:
        if k in keys and keys[k]["type"] == "text":
            return k
    for f in coll["fields"]:
        if f["type"] == "text" and f["key"] not in ("key", "id"):
            return f["key"]
    return coll["fields"][0]["key"]


def clean_card(coll: dict, raw: Any) -> dict:
    raw = raw if isinstance(raw, dict) else ({"title": raw} if isinstance(raw, str) and raw else {})
    out: dict[str, Any] = {"title": check_template(coll, raw.get("title") or title_key(coll), "card.title")}
    if raw.get("meta"):
        out["meta"] = check_template(coll, raw["meta"], "card.meta")
    tags = raw.get("tags") or []
    if not isinstance(tags, list):
        raise bad("card.tags 写成 [{text, tone, when}, …]", "card.tags is [{text, tone, when}, …]")
    out["tags"] = []
    for t in tags[:MAX_TAGS]:
        t = {"text": t} if isinstance(t, str) else t
        if not isinstance(t, dict) or not t.get("text"):
            raise bad("card.tags 的每一个写成 {text: 模板, tone, when}", "Each card tag is {text: template, tone, when}")
        one: dict[str, Any] = {"text": check_template(coll, t["text"], "card.tags.text"),
                               "tone": t.get("tone") if t.get("tone") in boards.TONES else "neutral"}
        if t.get("when"):
            one["when"] = check_conds(coll, t["when"], "card.tags.when")
        out["tags"].append(one)
    return out


def date_field(coll: dict, key: Any, what: str) -> str:
    k = str(key or "")
    if not k:
        raise bad(f"{what} 要写日期字段", f"{what} needs a date field")
    _, f = boards.field_of(coll, k)
    if f["type"] not in DATE_TYPES:
        raise bad(f"{what} 要是日期字段，「{k}」是 {f['type']}", f'{what} must be a date field; "{k}" is {f["type"]}')
    return k


def check_range(raw: Any) -> str:
    s = str(raw or "").strip().lower()
    if not RANGE.match(s):
        raise bad("range 写成 +4w / -8w / 30d / 6m 这种（+ 往后，- 往前）", "range is like +4w, -8w, 30d or 6m (+ ahead, - back)")
    return s


def clean_chart(coll: dict, raw: Any, i: int) -> dict:
    if not isinstance(raw, dict):
        raise bad("charts 的每一个写成 {kind, title, …}", "Each chart is {kind, title, …}")
    raw = norm(raw)
    kind = str(raw.get("kind") or raw.get("type") or "")
    if kind not in CHART_KINDS:
        raise bad(f"第 {i + 1} 个图表的 kind 只能是 {' / '.join(CHART_KINDS)}", f"Chart {i + 1}: kind must be one of {', '.join(CHART_KINDS)}")
    c: dict[str, Any] = {"kind": kind, "title": str(raw.get("title") or "").strip()[:30]}
    if raw.get("where"):
        c["where"] = check_where(coll, raw["where"])
    g = str(raw.get("group") or "")
    if kind != "number" and not g:
        raise bad(f"图表「{c['title'] or kind}」要写 group（按哪个字段分组）", f'Chart "{c["title"] or kind}" needs group (which field to group by)')
    if g:
        _, f = boards.field_of(coll, g)
        if f["type"] == "photo":
            raise bad(f"不能按照片字段「{g}」分组", f'Can\'t group by the photo field "{g}"')
        if kind == "line" and f["type"] not in DATE_TYPES:
            raise bad("折线 line 的 group 要是日期字段", "A line chart's group must be a date field")
        c["group"] = g
        if f["type"] in DATE_TYPES:
            by = str(raw.get("by") or "week")
            if by not in ("day", "week", "month"):
                raise bad("by 只能是 day / week / month", "by must be day, week or month")
            c["by"] = by
            if raw.get("range"):
                c["range"] = check_range(raw["range"])
        if raw.get("bins") is not None:
            if f["type"] not in NUM_TYPES:
                raise bad("bins 只能用在数字字段上", "bins only works on number fields")
            try:
                bins = sorted({float(x) for x in raw["bins"]}, reverse=True)
            except (TypeError, ValueError):
                raise bad("bins 写成数字列表，比如 [90, 80, 70]", "bins is a list of numbers, e.g. [90, 80, 70]") from None
            if not 1 <= len(bins) <= 10:
                raise bad("bins 要 1–10 个数", "bins needs 1–10 numbers")
            c["bins"] = [int(x) if x.is_integer() else x for x in bins]
    agg = str(raw.get("agg") or "count")
    if agg not in CHART_AGGS:
        raise bad(f"agg 只能是 {' / '.join(CHART_AGGS)}", f"agg must be one of {', '.join(CHART_AGGS)}")
    c["agg"] = agg
    if agg != "count":
        fk = str(raw.get("field") or "")
        if not fk:
            raise bad(f"agg {agg} 要写 field（算哪个字段）", f"agg {agg} needs a field")
        _, f = boards.field_of(coll, fk)
        if f["type"] not in NUM_TYPES:
            raise bad(f"field「{fk}」要是数字字段", f'field "{fk}" must be a number field')
        c["field"] = fk
    if raw.get("limit"):
        c["limit"] = max(2, min(int(raw["limit"]), 6 if kind == "donut" else 12))
    if raw.get("order") in ("value", "group"):
        c["order"] = raw["order"]
    if raw.get("format"):
        if raw["format"] not in boards.FORMATS:
            raise bad(f"format 只能是 {' / '.join(boards.FORMATS)}", f"format must be one of {', '.join(boards.FORMATS)}")
        c["format"] = raw["format"]
    if raw.get("unit"):
        c["unit"] = str(raw["unit"])[:12]
    return c


def clean_view(coll: dict, raw: Any) -> dict:
    if not isinstance(raw, dict):
        raise bad("每个视图写成 {id, type, title, …}", "Each view is {id, type, title, …}")
    raw = norm(raw)
    vid, typ = str(raw.get("id") or "").strip(), str(raw.get("type") or "").strip()
    if not boards.BLOCK_ID.match(vid):
        raise bad(f"视图 id「{vid}」不行：小写字母或数字开头，只用 a-z 0-9 _ -", f'Bad view id "{vid}": a-z 0-9 _ - only')
    if typ not in VIEW_TYPES:
        raise bad(f"视图「{vid}」的 type 只能是 {' / '.join(VIEW_TYPES)}", f'View "{vid}": type must be one of {", ".join(VIEW_TYPES)}')
    v: dict[str, Any] = {"id": vid, "type": typ}
    if raw.get("title"):
        v["title"] = str(raw["title"]).strip()[:20]
    if raw.get("where"):
        v["where"] = check_where(coll, raw["where"])
    if raw.get("sort"):
        v["sort"] = check_sort(coll, raw["sort"])
    if raw.get("card"):
        v["card"] = clean_card(coll, raw["card"])
    if raw.get("limit") and typ in ("board", "timeline", "table"):
        v["limit"] = max(1, min(int(raw["limit"]), 50))
    if typ == "table":
        keys = raw.get("fields") or []
        if not isinstance(keys, list):
            raise bad("表格的 fields 写成字段名列表", "A table's fields is a list of field names")
        for k in keys:
            boards.field_of(coll, str(k))
        if keys:
            v["fields"] = [str(k) for k in keys[:MAX_TABLE_FIELDS]]
    elif typ == "board":
        g = str(raw.get("group") or "")
        if not g:
            raise bad(f"看板视图「{vid}」要写 group（按哪个字段分列）", f'Board view "{vid}" needs group (which field makes the columns)')
        _, f = boards.field_of(coll, g)
        if f["type"] not in GROUP_TYPES:
            raise bad(f"看板只能按 choice / bool / text 字段分列，「{g}」是 {f['type']}", f'A board groups by a choice, bool or text field; "{g}" is {f["type"]}')
        v["group"] = g
        if raw.get("groups") is not None:
            gs = raw["groups"]
            if not isinstance(gs, list) or not 1 <= len(gs) <= MAX_COLUMNS:
                raise bad(f"groups 写成 1–{MAX_COLUMNS} 个值的列表（\"\" = 没填的那一列）", f'groups is a list of 1–{MAX_COLUMNS} values ("" = the not-set column)')
            out = []
            for x in gs:
                if f["type"] == "bool":
                    if x not in (True, False, ""):
                        raise bad("bool 字段的 groups 写 true / false", "groups for a bool field are true / false")
                    out.append(x)
                    continue
                s = str(x).strip()
                if f["type"] == "choice" and s and s not in f["options"]:
                    raise bad(f"「{s}」不是「{g}」的选项；有的是 {' / '.join(f['options'])}", f'"{s}" is not an option of "{g}": {", ".join(f["options"])}')
                out.append(s)
            v["groups"] = list(dict.fromkeys(out))
    elif typ in ("calendar", "timeline"):
        v["date"] = date_field(coll, raw.get("date"), f"{typ} 视图「{vid}」的 date")
        if typ == "calendar" and raw.get("group"):
            _, f = boards.field_of(coll, str(raw["group"]))
            if f["type"] not in ("choice", "bool"):
                raise bad("日历的 group（点的颜色）要是 choice / bool 字段", "A calendar's group (dot colour) must be a choice or bool field")
            v["group"] = str(raw["group"])
        if typ == "timeline":
            if raw.get("start"):
                v["start"] = date_field(coll, raw["start"], f"时间线「{vid}」的 start")
            if raw.get("range"):
                v["range"] = check_range(raw["range"])
    else:
        charts = raw.get("charts")
        if not isinstance(charts, list) or not 1 <= len(charts) <= MAX_CHARTS:
            raise bad(f"图表视图「{vid}」要写 charts：1–{MAX_CHARTS} 个图表", f'Chart view "{vid}" needs charts: 1–{MAX_CHARTS} charts')
        v["charts"] = [clean_chart(coll, c, i) for i, c in enumerate(charts)]
    return v


def clean_collection(conn: sqlite3.Connection, agent: str, raw: dict, b: dict) -> dict:
    bid = b["id"]
    src = raw.get("source")
    if not isinstance(src, dict) or not src.get("from"):
        raise bad(f"集合块「{bid}」要写 source：{{\"from\": 表}}", f'Collection "{bid}" needs source: {{"from": table}}')
    boards.check_query(conn, agent, src, "rows")
    coll = boards.get_coll(conn, agent, str(src["from"]))
    b["source"] = {"from": coll["name"], **({"where": src["where"]} if src.get("where") else {}),
                   **({"sort": check_sort(coll, src["sort"])} if src.get("sort") else {})}
    b["card"] = clean_card(coll, raw.get("card"))
    views = raw.get("views")
    if not isinstance(views, list) or not 1 <= len(views) <= MAX_VIEWS:
        raise bad(f"集合块「{bid}」要写 views：1–{MAX_VIEWS} 个视图", f'Collection "{bid}" needs views: 1–{MAX_VIEWS} views')
    b["views"] = [clean_view(coll, v) for v in views]
    ids = [v["id"] for v in b["views"]]
    if len(set(ids)) != len(ids):
        raise bad("视图 id 不能重复", "View ids must be unique")
    default = str(raw.get("view") or ids[0])
    if default not in ids:
        raise bad(f"默认视图 view「{default}」不在 views 里", f'The default view "{default}" is not in views')
    b["view"] = default
    if coll.get("readonly"):
        if raw.get("rowActions"):
            raise bad(f"「{coll['name']}」是只读的（Apple 健康），不能加 rowActions", f'"{coll["name"]}" is read-only (Apple Health): no rowActions')
        b["edit"] = False
    else:
        b["rowActions"] = boards.clean_row_actions(coll, raw.get("rowActions"))
        if raw.get("edit") is not None:
            b["edit"] = bool(raw["edit"])
    return b


# —— 取数据 ——————————————————————————————————————————————————————————————

def fetch(conn: sqlite3.Connection, agent: str, name: str, where: list, sort: list | None) -> list[dict]:
    """这张表里符合条件的行（最多 MAX_ROWS），按 sort 排好；data 里带 _created / _updated。"""
    coll, frm, params = boards.base(conn, agent, {"from": name, "where": where})
    rows = conn.execute(f"SELECT id, data, created_at, updated_at {frm}{boards.order_sql(coll, sort)} LIMIT {boards.MAX_ROWS}", params).fetchall()  # noqa: S608 — 字段名按表结构校验过，值全是参数
    out = []
    for r in rows:
        d = json.loads(r["data"]) if r["data"] else {}
        d["_created"], d["_updated"] = r["created_at"], r["updated_at"]
        out.append({"id": r["id"], "data": d})
    return out


def count(conn: sqlite3.Connection, agent: str, name: str, where: list) -> int:
    coll, frm, params = boards.base(conn, agent, {"from": name, "where": where})
    return int(conn.execute(f"SELECT COUNT(*) {frm}", params).fetchone()[0])  # noqa: S608


def fields_of(coll: dict) -> dict[str, dict]:
    return {f["key"]: f for f in coll["fields"]}


def field_def(fields: dict[str, dict], key: str) -> dict:
    if key in fields:
        return fields[key]
    if key in boards.SYSTEM:
        return {"key": key, "type": boards.SYSTEM[key], "label": L("创建时间", "Created") if key == "_created" else L("更新时间", "Updated")}
    return {"key": key, "type": "text", "label": key}


REL_DAY = re.compile(r"(今天|明天|昨天) (?=[\u4e00-\u9fff])")


def shown(s: str) -> str:
    """模板渲染的结果：空的、只有「—」的算没有；「今天 截止」这种去掉中间的空格（日期是「10/16 截止」，保留空格）。"""
    return "" if s in ("", "—") else REL_DAY.sub(r"\1", s)


def conds_ok(d: dict, conds: list, fields: dict[str, dict]) -> bool:
    for c in conds:
        rule = {"field": c[0], "op": c[1], "value": c[2] if len(c) > 2 else None, "text": "x", "tone": "neutral"}
        if boards.badge_of(d, [rule], fields) is None:
            return False
    return True


def card_of(r: dict, spec: dict, coll: dict, fields: dict[str, dict]) -> dict:
    """一行 → 卡片：标题、上面一行小字、标签，外加 data / display（点开看全部字段、改、删用）。"""
    d = r["data"]
    title = shown(boards.render(spec["title"], d, fields))
    out: dict[str, Any] = {"id": r["id"], "title": title or L("（无标题）", "(Untitled)"),
                           "data": {k: v for k, v in d.items() if not k.startswith("_")},
                           "display": {f["key"]: boards.fmt_value(f, d.get(f["key"])) for f in coll["fields"]}}
    meta = shown(boards.render(spec.get("meta") or "", d, fields))
    if meta:
        out["meta"] = meta
    tags = []
    for t in spec.get("tags") or []:
        if t.get("when") and not conds_ok(d, t["when"], fields):
            continue
        txt = shown(boards.render(t["text"], d, fields))
        if txt:
            tags.append({"text": txt, "tone": t["tone"]})
    if tags:
        out["tags"] = tags
    return out


def color_of(f: dict, value: Any) -> int | None:
    """选项的颜色按它在选项里的位置（各个视图里同一个选项同一种颜色）；排在第 7 个以后的用中性色。"""
    if f.get("type") == "choice" and value in f.get("options", []):
        i = f["options"].index(value)
        return i if i < SERIES else None
    if f.get("type") == "bool" and value in (True, False):
        return 0 if value else 1
    return None


# —— 日期 ——————————————————————————————————————————————————————————————

def to_date(v: Any) -> date | None:
    try:
        return date.fromisoformat(str(v)[:10]) if v not in (None, "") else None
    except ValueError:
        return None


def add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, 1)


def step(d: date, by: str, n: int = 1) -> date:
    if by == "month":
        return add_months(d, n)
    return d + timedelta(days=n * (7 if by == "week" else 1))


def md(d: date) -> str:
    return f"{d.month}/{d.day}" if lang() == "zh" else f"{d.day} {MONTHS_EN[d.month - 1]}"


def bucket_label(d: date, by: str, short: bool = False) -> str:
    """short：段数多（> 6）时柱子窄，周只写开头那天（10/12），不写「周」。"""
    if by == "week":
        return md(d) if short else L(f"{d.month}/{d.day} 周", f"{d.day} {MONTHS_EN[d.month - 1]}")
    if by == "month":
        if d.year != boards.today().year:
            return L(f"{d.year}年{d.month}月", f"{MONTHS_EN[d.month - 1]} {d.year}")
        return L(f"{d.month}月", MONTHS_EN[d.month - 1])
    return md(d)


def parse_rng(rng: str | None) -> tuple[str, int, str] | None:
    m = RANGE.match(rng or "")
    return (m.group(1), int(m.group(2)), m.group(3)) if m else None


def n_buckets(n: int, unit: str, by: str) -> int:
    days = n * {"d": 1, "w": 7, "m": 30}[unit]
    return max(1, min(boards.SERIES_MAX, round(days / {"day": 1, "week": 7, "month": 30}[by])))


# —— 分组：看板的列、图表的柱和扇区 ——————————————————————————————————————————————

def value_label(f: dict, v: Any) -> str:
    if v in (None, ""):
        return L("未填写", "Not set")
    if f.get("type") == "bool":
        return L("是", "Yes") if v else L("否", "No")
    if f.get("type") in NUM_TYPES:
        return boards.fmt_value(f, v)
    return str(v)


def bin_buckets(f: dict, rows: list[dict], bins: list) -> list[dict]:
    """数字分段：[90, 80, 70] → 90 以上 / 80–89 / 70–79 / 70 以下（没有行的段照样列出，最后一段只在有行时出现）。"""
    ints = all(float(b).is_integer() for b in bins)
    out, k = [], f["key"]
    for i, lo in enumerate(bins):
        if i == 0:
            label = L(f"{lo} 以上", f"{lo}+")
        else:
            hi = bins[i - 1] - 1 if ints else bins[i - 1]
            label = f"{lo}–{hi}"
        out.append({"key": f"#{i}", "label": label, "rows": []})
    low = {"key": f"#{len(bins)}", "label": L(f"{bins[-1]} 以下", f"Under {bins[-1]}"), "rows": []}
    for r in rows:
        v = r["data"].get(k)
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            continue
        for i, lo in enumerate(bins):
            if v >= lo:
                out[i]["rows"].append(r)
                break
        else:
            low["rows"].append(r)
    return out + ([low] if low["rows"] else [])


def date_buckets(f: dict, rows: list[dict], c: dict) -> list[dict]:
    """按天 / 周 / 月：range +4w = 这一段和后面 3 段，再早的归「之前」、更晚的归「之后」；-8w = 到这一段为止的前 8 段（窗口外的不算）。
    不写 range：多数日期在今天以后就往后看 5 段，否则往前看（天 14 / 周 8 / 月 6）。没有日期的不进图。"""
    by, k = c.get("by", "week"), f["key"]
    t = boards.today()
    cur = boards.bucket_start(t, by)
    dated = [(to_date(r["data"].get(k)), r) for r in rows]
    dated = [(d, r) for d, r in dated if d]
    rng = parse_rng(c.get("range"))
    if rng:
        forward = rng[0] != "-"
        n = n_buckets(rng[1], rng[2], by)
    else:
        forward = sum(1 for d, _ in dated if d >= t) * 2 >= len(dated) > 0
        n = 5 if forward else {"day": 14, "week": 8, "month": 6}[by]
    starts = [step(cur, by, i) for i in range(n)] if forward else [step(cur, by, i - n + 1) for i in range(n)]
    out = [{"key": s.isoformat(), "label": bucket_label(s, by, n > 6), "rows": [], "current": s == cur} for s in starts]
    index = {s: i for i, s in enumerate(starts)}
    before = {"key": BEFORE, "label": L("之前", "Earlier"), "rows": []}
    after = {"key": AFTER, "label": L("之后", "Later"), "rows": []}
    for d, r in dated:
        s = boards.bucket_start(d, by)
        if s in index:
            out[index[s]]["rows"].append(r)
        elif forward:
            (before if s < starts[0] else after)["rows"].append(r)
    if forward:
        out = ([before] if before["rows"] else []) + out + ([after] if after["rows"] else [])
    return out


def chart_buckets(f: dict, rows: list[dict], c: dict) -> list[dict]:
    """图表要画的那几段，按显示顺序：日期按时间、分段按段、其余按数值从大到小（order: group 按选项顺序），多出来的并成「其他」。
    没填的不进图。每段 {key, label, rows, current?, color?}。"""
    k = f["key"]
    if f["type"] in DATE_TYPES:
        return date_buckets(f, rows, c)
    if c.get("bins"):
        return bin_buckets(f, rows, c["bins"])
    groups: dict[Any, list] = {}
    for r in rows:
        v = r["data"].get(k)
        if v in (None, ""):
            continue
        groups.setdefault(v, []).append(r)
    if f["type"] == "choice":
        keys = [o for o in f["options"] if o in groups] + [v for v in groups if v not in f["options"]]
    elif f["type"] == "bool":
        keys = [v for v in (True, False) if v in groups]
    else:
        keys = sorted(groups, key=lambda v: (str(v)))
    order = c.get("order") or ("group" if c["kind"] == "number" else "value")
    if order == "value":  # 稳定排序：一样大的保持选项顺序
        keys.sort(key=lambda v: -(agg_rows(groups[v], c) or 0))
    out = [{"key": f"={json.dumps(v, ensure_ascii=False)}", "label": value_label(f, v), "rows": groups[v], "color": color_of(f, v)} for v in keys]
    limit = c.get("limit") or {"donut": 6, "hbar": 6, "bar": 8, "number": 3}.get(c["kind"], 8)
    if len(out) > limit:
        keep, rest = out[:limit - 1], out[limit - 1:]
        keep.append({"key": OTHER, "label": L("其他", "Other"), "rows": [r for b in rest for r in b["rows"]], "color": None})
        out = keep
    return out


def board_columns(f: dict, rows: list[dict], v: dict) -> tuple[list[dict], int]:
    """看板的列，按显示顺序：{key: 拖过来要写的值（None = 清空）, id, label, rows, color, drop}。groups 写了就只列这些（其余的行算「隐藏」）。"""
    k = f["key"]
    explicit = v.get("groups")
    groups: dict[Any, list] = {}
    empty: list = []
    for r in rows:
        val = r["data"].get(k)
        if val in (None, ""):
            empty.append(r)
        else:
            groups.setdefault(val, []).append(r)
    cols: list[dict] = []

    def col(val: Any, label: str, rs: list, color: int | None, drop: bool = True) -> dict:
        cid = "" if val is None else f"={json.dumps(val, ensure_ascii=False)}"
        return {"key": val, "id": cid, "label": label, "rows": rs, "color": color, "drop": drop}

    if explicit is not None:
        seen = set()
        for x in explicit:
            if x == "":
                cols.append(col(None, value_label(f, None), empty, None))
                seen.add(None)
                continue
            color = color_of(f, x) if f["type"] == "choice" else (len(cols) if f["type"] == "text" and len(cols) < SERIES else None)
            cols.append(col(x, value_label(f, x), groups.get(x, []), color))
            seen.add(x)
        hidden = sum(len(rs) for val, rs in groups.items() if val not in seen) + (0 if None in seen else len(empty))
        return cols, hidden
    if f["type"] == "choice":
        vals = [o for o in f["options"]] + [x for x in groups if x not in f["options"]]
        cols = [col(x, value_label(f, x), groups.get(x, []), color_of(f, x)) for x in vals]
    elif f["type"] == "bool":
        cols = [col(x, value_label(f, x), groups.get(x, []), None) for x in (True, False)]
    else:
        vals = sorted(groups, key=lambda x: (-len(groups[x]), str(x)))
        top, rest = vals[:MAX_COLUMNS - 1], vals[MAX_COLUMNS - 1:]
        cols = [col(x, value_label(f, x), groups[x], i if i < SERIES else None) for i, x in enumerate(top)]
        if rest:
            other = col(None, L("其他", "Other"), [r for x in rest for r in groups[x]], None, drop=False)
            other["id"] = OTHER
            cols.append(other)
    if empty:
        cols.append(col(None, value_label(f, None), empty, None))
    return cols, 0


# —— 数值 ——————————————————————————————————————————————————————————————

def agg_rows(rows: list[dict], c: dict) -> float | None:
    agg = c.get("agg", "count")
    if agg == "count":
        return float(len(rows))
    return boards.agg_values([r["data"].get(c["field"]) for r in rows], agg)


def fmt_of(c: dict, fields: dict[str, dict]) -> tuple[str | None, str | None, str]:
    """(format, unit, 货币)：数行数是整数；算字段的跟着字段走（金额、单位）。"""
    if c.get("agg", "count") == "count":
        return c.get("format") or "int", c.get("unit"), "GBP"
    f = fields.get(c.get("field", "")) or {}
    fmt = c.get("format") or ("money" if f.get("type") == "money" else None)
    unit = c.get("unit") or (None if fmt == "money" else f.get("unit"))
    return fmt, unit, f.get("currency", "GBP")


def nice_max(x: float) -> float:
    """纵轴的顶：1、2、4、6、8、10 × 10ⁿ（中间那条线是一半，数次数时也是整数：0 / 3 / 6）。"""
    if x <= 0:
        return 1.0
    e = 10 ** math.floor(math.log10(x))
    m = x / e
    return next(k for k in (1, 2, 4, 6, 8, 10) if m <= k) * e


def axis_text(x: float) -> str:
    return boards.fmt_num(x, 0 if float(x).is_integer() else 1)


# —— 每种视图 ——————————————————————————————————————————————————————————————

def view_label(v: dict) -> str:
    return v.get("title") or L(*VIEW_TITLES[v["type"]])


def spec_of(b: dict, v: dict) -> dict:
    return v.get("card") or b["card"]


def wheres(b: dict, v: dict, opts: dict, extra: list | None = None) -> list:
    return [*(b["source"].get("where") or []), *(v.get("where") or []), *(extra or []), *(opts.get("where") or [])]


def table_view(conn: sqlite3.Connection, agent: str, b: dict, v: dict, coll: dict, opts: dict) -> dict:
    fields = fields_of(coll)
    spec = spec_of(b, v)
    sort = opts.get("sort") or v.get("sort") or b["source"].get("sort") or []
    rows = fetch(conn, agent, coll["name"], wheres(b, v, opts), sort)
    keys = v.get("fields") or default_columns(coll, spec)
    first = (sort[0] if sort else "").lstrip("-")
    columns = []
    for k in keys:
        f = field_def(fields, k)
        columns.append({"key": k, "label": f.get("label") or k, "type": f["type"],
                        "align": "right" if f["type"] in (*NUM_TYPES, *DATE_TYPES) else "left",
                        "sort": ("desc" if sort[0].startswith("-") else "asc") if k == first else None,
                        "sortable": f["type"] not in ("photo", "link")})
    limit = opts.get("limit") or (FULL if opts.get("full") else INLINE)["table"]
    offset = max(0, int(opts.get("offset") or 0))
    page = rows[offset:offset + limit]
    out_rows = []
    for r in page:
        c = card_of(r, spec, coll, fields)
        d = r["data"]
        c["cells"] = {k: boards.fmt_value(field_def(fields, k), d.get(k)) for k in keys}
        colors = {k: color_of(fields[k], d.get(k)) for k in keys if k in fields and fields[k]["type"] == "choice" and d.get(k) not in (None, "")}
        if colors:
            c["colors"] = colors
        out_rows.append(c)
    return {"type": "table", "columns": columns, "rows": out_rows, "total": len(rows), "offset": offset,
            "more": max(0, len(rows) - offset - len(page)), "sort": sort}


def default_columns(coll: dict, spec: dict) -> list[str]:
    title = spec["title"] if boards.KEY.match(spec["title"]) else title_key(coll)
    rest = [f["key"] for f in coll["fields"] if f["key"] != title and f["type"] not in ("photo", "link") and f["key"] not in ("key", "id")]
    return [title, *rest[:3]]


def board_view(conn: sqlite3.Connection, agent: str, b: dict, v: dict, coll: dict, opts: dict) -> dict:
    fields = fields_of(coll)
    spec = spec_of(b, v)
    f = fields[v["group"]]
    sort = opts.get("sort") or v.get("sort") or b["source"].get("sort") or []
    rows = fetch(conn, agent, coll["name"], wheres(b, v, opts), sort)
    cols, hidden = board_columns(f, rows, v)
    per = opts.get("limit") or (FULL["board"] if opts.get("full") else v.get("limit") or INLINE["board"])
    only = opts.get("column")
    offset = max(0, int(opts.get("offset") or 0)) if only is not None else 0
    out = []
    for c in cols:
        if only is not None and c["id"] != only:
            continue
        page = c["rows"][offset:offset + per]
        out.append({"key": c["key"], "id": c["id"], "label": c["label"], "count": len(c["rows"]), "color": c["color"], "drop": c["drop"],
                    "cards": [card_of(r, spec, coll, fields) for r in page], "more": max(0, len(c["rows"]) - offset - len(page))})
    return {"type": "board", "group": f["key"], "groupLabel": f["label"], "columns": out, "total": len(rows), "hidden": hidden,
            "movable": not coll.get("readonly"), "offset": offset}


def calendar_view(conn: sqlite3.Connection, agent: str, b: dict, v: dict, coll: dict, opts: dict) -> dict:
    fields = fields_of(coll)
    spec = spec_of(b, v)
    t = boards.today()
    try:
        first = date.fromisoformat(f"{opts['month']}-01") if opts.get("month") else t.replace(day=1)
    except ValueError:
        raise bad("month 写成 YYYY-MM", "month is YYYY-MM") from None
    nxt = add_months(first, 1)
    start = first - timedelta(days=first.weekday())
    end = nxt - timedelta(days=1)
    end += timedelta(days=6 - end.weekday())
    dk = v["date"]
    rows = fetch(conn, agent, coll["name"], wheres(b, v, opts, [[dk, ">=", start.isoformat()], [dk, "<=", end.isoformat()]]),
                 opts.get("sort") or v.get("sort") or [dk])
    undated = count(conn, agent, coll["name"], wheres(b, v, opts, [[dk, "empty"]]))
    gf = fields.get(v.get("group", ""))
    fd = field_def(fields, dk)
    by_day: dict[date, list] = {}
    used: dict[Any, int | None] = {}
    for r in rows:
        d = to_date(r["data"].get(dk))
        if not d:
            continue
        item = card_of(r, spec, coll, fields)
        if gf:
            gv = r["data"].get(gf["key"])
            item["color"] = color_of(gf, gv)
            if gv not in (None, ""):
                used[gv] = item["color"]
        else:
            item["color"] = 0
        if fd["type"] == "datetime" and len(str(r["data"].get(dk) or "")) > 10:
            item["time"] = str(r["data"][dk])[11:16]
        by_day.setdefault(d, []).append(item)
    weeks, d = [], start
    while d <= end:
        week = []
        for _ in range(7):
            week.append({"date": d.isoformat(), "day": d.day, "inMonth": d.month == first.month, "today": d == t, "items": by_day.get(d, [])})
            d += timedelta(days=1)
        weeks.append(week)
    title = L(f"{first.year} 年 {first.month} 月", f"{_cal.month_name[first.month]} {first.year}")
    out: dict[str, Any] = {"type": "calendar", "month": first.strftime("%Y-%m"), "title": title,
                           "prev": add_months(first, -1).strftime("%Y-%m"), "next": nxt.strftime("%Y-%m"),
                           "weekdays": [L(x, y) for x, y in zip("一二三四五六日", ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"))],
                           "weeks": weeks, "count": sum(len(x) for d, x in by_day.items() if first <= d < nxt),
                           "undated": undated, "dateLabel": fd.get("label") or dk}
    if gf:
        opts_order = gf.get("options") or [True, False]
        out["legend"] = [{"label": value_label(gf, x), "color": used[x]} for x in opts_order if x in used]
    return out


def timeline_view(conn: sqlite3.Connection, agent: str, b: dict, v: dict, coll: dict, opts: dict) -> dict:
    """条 = 从开始（start 字段；没写就是今天）到日期，菱形是那一天；金线是今天。窗口从本周一起，默认往后 5 周。"""
    fields = fields_of(coll)
    spec = spec_of(b, v)
    t = boards.today()
    sign, n, unit = parse_rng(v.get("range")) or ("+", 5, "w")
    back = sign == "-"  # 往回看：窗口到这一周（这个月、今天）为止
    if unit == "w":
        w0 = t - timedelta(days=t.weekday()) - timedelta(weeks=n - 1 if back else 0)
        w1 = w0 + timedelta(weeks=n)
    elif unit == "m":
        w0 = add_months(t.replace(day=1), -(n - 1) if back else 0)
        w1 = add_months(w0, n)
    else:
        w0 = t - timedelta(days=n - 1 if back else 0)
        w1 = w0 + timedelta(days=n)
    span = max(1, (w1 - w0).days)
    dk = v["date"]
    fd = field_def(fields, dk)
    rows = fetch(conn, agent, coll["name"], wheres(b, v, opts, [[dk, ">=", w0.isoformat()]]), opts.get("sort") or v.get("sort") or [dk])
    before = count(conn, agent, coll["name"], wheres(b, v, opts, [[dk, "<", w0.isoformat()]]))
    gf = fields.get(v.get("group", ""))

    def pos(d: date) -> float:
        return round(min(1.0, max(0.0, (d - w0).days / span)), 4)

    limit = opts.get("limit") or (FULL["timeline"] if opts.get("full") else v.get("limit") or INLINE["timeline"])
    offset = max(0, int(opts.get("offset") or 0))
    items = []
    for r in rows[offset:offset + limit]:
        end = to_date(r["data"].get(dk))
        if not end:
            continue
        s = to_date(r["data"].get(v["start"])) if v.get("start") else t
        s = min(s or t, end)
        item = card_of(r, spec, coll, fields)
        item.update(start=pos(max(s, w0)), end=pos(end), endText=md(end), clipped=end >= w1, overdue=end < t,
                    startDate=max(s, w0).isoformat(), endDate=end.isoformat(), color=color_of(gf, r["data"].get(gf["key"])) if gf else 0)
        items.append(item)
    ticks, d = [], w0
    while d < w1:
        ticks.append({"pos": pos(d), "label": md(d)})
        d = step(d, "month" if span > 100 else "week")
    return {"type": "timeline", "from": w0.isoformat(), "to": w1.isoformat(), "today": pos(t) if w0 <= t <= w1 else None, "todayDate": t.isoformat(), "ticks": ticks,
            "items": items, "total": len(rows), "offset": offset, "more": max(0, len(rows) - offset - len(items)), "before": before,
            "dateLabel": fd.get("label") or dk}


def compute_chart(conn: sqlite3.Connection, agent: str, b: dict, v: dict, coll: dict, c: dict, opts: dict, rows: list[dict] | None = None) -> dict:
    fields = fields_of(coll)
    if rows is None:
        rows = fetch(conn, agent, coll["name"], wheres(b, v, opts, c.get("where")), None)
    fmt, unit, cur = fmt_of(c, fields)

    def text(x: float | None) -> str:
        return boards.fmt_stat(x, fmt, unit, cur) if x is not None else "—"

    out: dict[str, Any] = {"kind": c["kind"], "title": c.get("title") or ""}
    if c["kind"] == "number":
        if not c.get("group"):
            val = agg_rows(rows, c)
            return {**out, "value": val, "text": text(val), "label": out["title"]}
        parts = []
        for bk in chart_buckets(fields[c["group"]], rows, c):
            val = agg_rows(bk["rows"], c)
            parts.append({"key": bk["key"], "label": bk["label"], "value": val, "text": text(val)})
        return {**out, "parts": parts, "text": " / ".join(p["text"] for p in parts) or text(0.0),
                "label": out["title"] or " / ".join(p["label"] for p in parts)}
    f = fields[c["group"]]
    bks = chart_buckets(f, rows, c)
    vals = [(bk, agg_rows(bk["rows"], c)) for bk in bks]
    if c["kind"] == "donut":
        total = sum(x or 0 for _, x in vals)
        slices = []
        for bk, x in vals:
            if not x:
                continue
            n = len(slices)
            slices.append({"key": bk["key"], "label": bk["label"], "value": x, "text": text(x), "pct": round(x / total * 100) if total else 0,
                           "color": None if bk["key"] == OTHER or n >= SERIES else n})
        return {**out, "slices": slices, "total": total, "totalText": text(total)}
    if c["kind"] == "line":
        level = c.get("agg", "count") not in ("count", "sum")  # 平均、最高这类：没有记录的那一段是空的，不是 0
        pts = []
        for bk, x in vals:
            if bk["key"] in (BEFORE, AFTER):
                continue
            val = None if level and not bk["rows"] else (x if x is not None else (None if level else 0.0))
            pts.append({"key": bk["key"], "label": bk["label"], "value": val, "text": text(val) if val is not None else "",
                        "current": bool(bk.get("current"))})
        got = [p["value"] for p in pts if p["value"] is not None]
        lo, hi = (min(got), max(got)) if got else (0.0, 1.0)
        if not level:
            lo = 0.0
        pad = (hi - lo or abs(hi) or 1) * 0.12
        return {**out, "points": pts, "lo": lo - (pad if level else 0), "hi": hi + pad,
                "lastText": next((p["text"] for p in reversed(pts) if p["value"] is not None), "")}
    peak = max([x or 0 for _, x in vals] or [0])
    bars = []
    for bk, x in vals:
        one = {"key": bk["key"], "label": bk["label"], "value": x or 0, "text": text(x if x is not None else 0)}
        if bk.get("current"):
            one["current"] = True
        if c["kind"] == "bar" and (bk.get("current") or (x and x == peak)):
            one["show"] = True  # 只标关键数值：这一段和最高的那一段
        bars.append(one)
    top = nice_max(peak)
    res = {**out, "bars": bars, "max": top}
    if c["kind"] == "bar":
        # 刻度：数行数的写纯数字、右上角注明「单位：个」；时长、金额这类带单位的数，刻度和柱子上的写法一样（2h30、£50）
        plain = c.get("agg", "count") == "count" and not unit
        res["axis"] = [{"value": x, "text": axis_text(x) if plain or fmt == "percent" else text(x)} for x in (0, top / 2, top)]
    # 图的右上角写单位（app 的素材库刻度只写数）：数行数「单位：个」，带单位的写单位，金额写货币
    if c.get("agg", "count") == "count" and not unit:
        res["unitLabel"] = L("单位：个", "Count")
    elif fmt == "money":
        res["unitLabel"] = L(f"单位：{boards.CURRENCY.get(cur, cur)}", f"In {boards.CURRENCY.get(cur, cur)}")
    elif unit:
        res["unitLabel"] = L(f"单位：{unit}", f"Unit: {unit}")
    return res


def chart_view(conn: sqlite3.Connection, agent: str, b: dict, v: dict, coll: dict, opts: dict) -> dict:
    charts = []
    for c in v["charts"]:
        try:
            charts.append(compute_chart(conn, agent, b, v, coll, c, opts))
        except HTTPException as e:
            charts.append({"kind": c["kind"], "title": c.get("title") or "", "error": str(e.detail)})
    return {"type": "chart", "charts": charts}


COMPUTE = {"table": table_view, "board": board_view, "calendar": calendar_view, "timeline": timeline_view, "chart": chart_view}


def compute(conn: sqlite3.Connection, agent: str, b: dict, v: dict, coll: dict, opts: dict) -> dict:
    out = COMPUTE[v["type"]](conn, agent, b, v, coll, opts)
    out["label"] = view_label(v)
    return out


def resolve_collection(conn: sqlite3.Connection, agent: str, b: dict) -> dict:
    """看板 GET 里这一块的数据：表的信息 + 每个视图的默认样子（出错只影响那一个视图）。"""
    coll = boards.get_coll(conn, agent, str(b["source"]["from"]))
    views: dict[str, Any] = {}
    for v in b["views"]:
        try:
            views[v["id"]] = compute(conn, agent, b, v, coll, {})
        except HTTPException as e:
            views[v["id"]] = {"type": v["type"], "label": view_label(v), "error": str(e.detail)}
        except (sqlite3.Error, ValueError, TypeError, KeyError) as e:
            views[v["id"]] = {"type": v["type"], "label": view_label(v), "error": L(f"这个视图算不出来：{e}", f"Couldn't work this view out: {e}")}
    return {"collection": coll["name"], "collectionTitle": coll["title"], "fields": coll["fields"],
            "total": count(conn, agent, coll["name"], b["source"].get("where") or []), "readonly": bool(coll.get("readonly")), "views": views}


boards.EXTRA["collection"] = (clean_collection, resolve_collection)


# —— 接口 ————————————————————————————————————————————————————————————————

class ViewIn(BaseModel):
    block: str
    view: str
    version: int | None = None     # 看某一版（提案预览）；不写 = 现在的看板
    sort: list[str] | None = None  # 你在表格表头点的排序，替换视图自己的
    where: list | None = None      # 你加的筛选，和视图自己的叠加
    month: str | None = None       # 日历：YYYY-MM
    column: str | None = None      # 看板：只要这一列（列的 id），配 offset 翻页
    offset: int = 0
    limit: int | None = None
    full: bool = False             # 全屏页：每个视图多给些行
    chart: int | None = None       # 图表：点的是第几个
    bucket: str | None = None      # 点的那一段（柱、扇区、数字的一部分）的 key：返回这一段的行


def find(conn: sqlite3.Connection, agent: str, body: ViewIn) -> tuple[dict, dict, dict]:
    r = (conn.execute("SELECT * FROM boards WHERE agent=? AND version=?", (agent, body.version)).fetchone() if body.version
         else boards.live_row(conn, agent))
    b = next((x for x in boards.blocks_of(r) if x.get("id") == body.block and x.get("type") == "collection"), None)
    if not b:
        raise bad(f"看板上没有「{body.block}」这个集合块", f'No collection block "{body.block}" on this board', 404)
    v = next((x for x in b["views"] if x["id"] == body.view), None)
    if not v:
        raise bad(f"「{body.block}」没有「{body.view}」这个视图", f'"{body.block}" has no view "{body.view}"', 404)
    return b, v, boards.get_coll(conn, agent, str(b["source"]["from"]))


@router.post("/api/boards/{agent}/view")
def view(agent: str, body: ViewIn):
    """集合块的一个视图，按你点的排序、筛选、月份、翻页重新算（只读）。带 chart + bucket：点开的那一段有哪些行。"""
    boards.group_row(agent)
    opts = {"sort": body.sort, "where": body.where, "month": body.month, "column": body.column, "offset": body.offset,
            "limit": max(1, min(body.limit, 500)) if body.limit else None, "full": body.full}
    with _lock, bdb() as conn:
        b, v, coll = find(conn, agent, body)
        if opts["sort"]:
            check_sort(coll, opts["sort"])
        if body.chart is not None:
            if v["type"] != "chart" or not 0 <= body.chart < len(v["charts"]):
                raise bad("没有这个图表", "No such chart", 404)
            return {"ok": True, **bucket_rows(conn, agent, b, v, coll, body.chart, body.bucket, opts)}
        return {"ok": True, "view": compute(conn, agent, b, v, coll, opts)}


def bucket_rows(conn: sqlite3.Connection, agent: str, b: dict, v: dict, coll: dict, ci: int, key: str | None, opts: dict) -> dict:
    c = v["charts"][ci]
    fields = fields_of(coll)
    rows = fetch(conn, agent, coll["name"], wheres(b, v, opts, c.get("where")), v.get("sort") or b["source"].get("sort"))
    title = c.get("title") or ""
    if c.get("group") and key is not None:
        bk = next((x for x in chart_buckets(fields[c["group"]], rows, c) if x["key"] == key), None)
        if not bk:
            raise bad("这一段没有数据了（看板可能刚更新过）", "This part has no data any more (the board may have just changed)", 404)
        rows, title = bk["rows"], f"{title} · {bk['label']}" if title else bk["label"]
    spec = spec_of(b, v)
    limit = opts.get("limit") or FULL["table"]
    return {"title": title, "total": len(rows), "rows": [card_of(r, spec, coll, fields) for r in rows[:limit]]}

