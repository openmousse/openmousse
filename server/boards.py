"""积木看板：每个 Agent 自己的数据表 + 一份看板配置，app 用写好的几种积木按配置画（不下载代码）。

- 表 collections：Agent 定义的数据集合（名字、标题、字段）；records：所有集合的行，data 是 JSON，按字段取，删除只标 deleted_at
  （30 天内能找回）；boards：看板配置按版本存，live 只有一个，draft 是等你点头的提案，回到旧版 = 复制成新的一版（历史不断）。
- 积木 type：stat 数字 / progress 进度 / chart 趋势 / list 列表 / checklist 清单 / text 文字 / action 按钮。每块的数据用查询 Q
  描述（JSON，不是 SQL）：{"from": 表, "where": [[字段, 运算, 值]…], "sort": [字段 或 -字段], "limit": N}，汇总加 agg / field，
  按天周月分组加 by / date / range，比值 {"ratio": [Q1, Q2]}。服务端算好、按语言格式化，随配置一起给 app。
- 放在哪：after = 内置看板的小节（ANCHORS，比如 diet.next）或另一块的 id；不写放最后。
- 只读的系统来源（所有 Agent 都能用，不能改）：`health:daily`（每晚睡眠、HRV、静息心率……，表 health_daily）和
  `health:<指标>`（Apple 健康按天汇总的某个指标，比如 health:StepCount，字段 date / sum / avg / min / max / count，表 health_metrics）。
- 谁能改：用户在对话里让加的，Agent 直接 apply（看板顶上出撤回条）；Agent 自己想到的 propose → 收件箱 kind block，
  同意时这里把草稿换成 live（inbox.HOOKS）。积木只显示、只让用户自己点（改、删、打勾、按钮），不推送、不写外面的系统。
- Agent 用 server/board_ctl.py 走这些接口；用户在 app 里的改动 by=user。
"""
from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import data
import inbox
from chat import TZ, _lock, db, log_activity, now_iso
from i18n import L, lang

router = APIRouter()

TYPES = ("stat", "progress", "chart", "list", "checklist", "text", "action")
FIELD_TYPES = ("text", "number", "money", "date", "datetime", "bool", "choice", "photo", "link")
OPS = ("=", "!=", ">", ">=", "<", "<=", "in", "not_in", "contains", "empty", "not_empty")
AGGS = ("sum", "avg", "count", "min", "max", "last")
FORMATS = ("number", "int", "money", "percent")
TONES = ("neutral", "good", "warn", "bad", "cyan", "gold")
ACTION_KINDS = ("ask", "upload", "form")
KEY = re.compile(r"^[a-z][a-z0-9_]{0,39}$")          # 表名、字段名
BLOCK_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")  # 积木 id
MAX_BLOCKS, MAX_COLLECTIONS, MAX_FIELDS, MAX_ROWS, MAX_TEXT = 30, 20, 24, 5000, 2000
LIST_MAX = 100       # 一块列表最多给多少行（多的只给总数）
SERIES_MAX = 60      # 趋势最多多少根柱子
RESTORE_DAYS = 30    # 删掉的行多久内能找回
SYSTEM = {"_created": "datetime", "_updated": "datetime"}  # 查询里能用的系统字段
# 内置看板（app 写死的那几种）的小节：积木的 after 可以写它们，插在那一节后面。top = 最前面。
ANCHORS = {
    "fitness": ("fitness.now", "fitness.body", "fitness.week", "fitness.long"),
    "diet": ("diet.today", "diet.next", "diet.eaten", "diet.week", "diet.shopping"),
    "health": ("health.sleep", "health.recovery"),
    "apply": ("apply.list",),
    "masters": ("masters.list",),
}
CURRENCY = {"GBP": "£", "USD": "$", "EUR": "€", "CNY": "¥", "JPY": "¥", "HKD": "HK$"}


def bdb() -> sqlite3.Connection:
    conn = db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS collections (agent TEXT NOT NULL, name TEXT NOT NULL, title TEXT NOT NULL, fields TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active', created_at TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(agent, name));
        CREATE TABLE IF NOT EXISTS records (id TEXT PRIMARY KEY, agent TEXT NOT NULL, collection TEXT NOT NULL, data TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'agent', created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT);
        CREATE INDEX IF NOT EXISTS records_coll ON records(agent, collection, deleted_at);
        CREATE TABLE IF NOT EXISTS boards (agent TEXT NOT NULL, version INTEGER NOT NULL, blocks TEXT NOT NULL, status TEXT NOT NULL,
            note TEXT NOT NULL DEFAULT '', by TEXT NOT NULL DEFAULT 'agent', inbox_id TEXT, based_on INTEGER, created_at TEXT NOT NULL,
            acked_at TEXT, PRIMARY KEY(agent, version));
        CREATE INDEX IF NOT EXISTS boards_inbox ON boards(inbox_id);
    """)
    return conn


def bad(zh: str, en: str, code: int = 400) -> HTTPException:
    return HTTPException(code, L(zh, en))


# —— Agent 和它的表 ——————————————————————————————————————————————————

def group_row(agent: str) -> sqlite3.Row:
    with _lock, data.ddb() as conn:
        g = conn.execute("SELECT id, name, dashboard FROM groups WHERE id=?", (agent,)).fetchone()
    if not g:
        raise bad(f"没有「{agent}」这个 Agent（agent_ctl.py list 能看到有哪些）", f'No Agent called "{agent}" (see agent_ctl.py list)', 404)
    return g


def anchors_of(dashboard: str | None) -> tuple[str, ...]:
    return ("top", *ANCHORS.get(dashboard or "", ()))


def coll_json(r: sqlite3.Row, count: int | None = None) -> dict:
    out = {"name": r["name"], "title": r["title"], "fields": json.loads(r["fields"]), "status": r["status"],
           "createdAt": r["created_at"], "updatedAt": r["updated_at"]}
    if count is not None:
        out["count"] = count
    return out


def collections_of(conn: sqlite3.Connection, agent: str, with_draft: bool = True) -> dict[str, dict]:
    rows = conn.execute("SELECT * FROM collections WHERE agent=? AND status IN ('active','draft') ORDER BY created_at", (agent,)).fetchall()
    return {r["name"]: coll_json(r) for r in rows if with_draft or r["status"] == "active"}


HEALTH_DAILY = (("sleep_min", "睡了多久", "Sleep", "min"), ("deep_min", "深睡", "Deep sleep", "min"), ("rem_min", "REM", "REM", "min"),
                ("core_min", "核心睡眠", "Core sleep", "min"), ("awake_min", "醒着", "Awake", "min"), ("hrv_ms", "HRV", "HRV", "ms"),
                ("rhr_bpm", "静息心率", "Resting HR", "bpm"), ("resp_rate", "呼吸频率", "Respiratory rate", "/min"),
                ("wrist_temp_c", "手腕温度", "Wrist temp", "°C"))
METRIC = re.compile(r"^[A-Za-z][A-Za-z0-9_]{1,80}$")


def virtual(conn: sqlite3.Connection, name: str) -> tuple[dict, str, list] | None:
    """只读的系统来源：返回 (表结构, 当作 records 用的子查询, 子查询的参数)。子查询的列和 records 一样，查询代码不用分两套。"""
    if not name.startswith("health:"):
        return None
    shape = "'h-' || date AS id, json_object({}) AS data, date AS created_at, updated_at, NULL AS deleted_at"
    date_f = {"key": "date", "label": L("日期", "Date"), "type": "date"}
    if name == "health:daily":
        fields = [date_f] + [{"key": k, "label": L(zh, en), "type": "number", "unit": u} for k, zh, en, u in HEALTH_DAILY]
        cols = ", ".join(f"'{f['key']}', {f['key']}" for f in fields)
        sub = f"(SELECT {shape.format(cols)} FROM health_daily)"
        params: list = []
    else:
        metric = name[7:]
        if not METRIC.match(metric):
            raise bad(f"「{name}」不对：写 health:指标名（比如 health:StepCount）", f'Bad source "{name}": write health:<metric>, e.g. health:StepCount')
        try:
            r = conn.execute("SELECT unit FROM health_metrics WHERE metric=? ORDER BY date DESC LIMIT 1", (metric,)).fetchone()
        except sqlite3.Error:
            r = None
        if not r:
            raise bad(f"Apple 健康里没有「{metric}」这个指标的数据", f'No Apple Health data for "{metric}"', 404)
        unit = r["unit"] or None
        fields = [date_f] + [{"key": k, "label": lab, "type": "number", **({"unit": unit} if unit and k != "count" else {})}
                             for k, lab in (("sum", L("合计", "Total")), ("avg", L("平均", "Average")), ("min", L("最低", "Min")),
                                            ("max", L("最高", "Max")), ("count", L("次数", "Count")))]
        cols = ", ".join(f"'{f['key']}', {f['key']}" for f in fields)
        sub = f"(SELECT {shape.format(cols)} FROM health_metrics WHERE metric=?)"
        params = [metric]
    return {"name": name, "title": name, "fields": fields, "status": "active", "readonly": True}, sub, params


def get_coll(conn: sqlite3.Connection, agent: str, name: str) -> dict:
    v = virtual(conn, name)
    if v:
        return v[0]
    r = conn.execute("SELECT * FROM collections WHERE agent=? AND name=? AND status IN ('active','draft')", (agent, name)).fetchone()
    if not r:
        raise bad(f"「{agent}」没有「{name}」这张表（board_ctl.py table list 能看到）", f'"{agent}" has no table called "{name}" (see board_ctl.py table list)', 404)
    return coll_json(r)


def clean_fields(raw: Any) -> list[dict]:
    if not isinstance(raw, list) or not raw:
        raise bad("fields 至少要有一个字段", "fields needs at least one field")
    if len(raw) > MAX_FIELDS:
        raise bad(f"一张表最多 {MAX_FIELDS} 个字段", f"At most {MAX_FIELDS} fields per table")
    out, seen = [], set()
    for f in raw:
        if not isinstance(f, dict):
            raise bad("每个字段写成 {key, label, type}", "Each field is {key, label, type}")
        key, typ = str(f.get("key") or "").strip(), str(f.get("type") or "text").strip()
        if not KEY.match(key) or key in seen:
            raise bad(f"字段名「{key}」不行：小写字母开头，只用 a-z 0-9 _，不能重复", f'Bad field key "{key}": lowercase letter first, a-z 0-9 _ only, no duplicates')
        if typ not in FIELD_TYPES:
            raise bad(f"字段「{key}」的 type 只能是 {' / '.join(FIELD_TYPES)}", f'Field "{key}": type must be one of {", ".join(FIELD_TYPES)}')
        seen.add(key)
        d: dict[str, Any] = {"key": key, "label": str(f.get("label") or key).strip()[:40], "type": typ}
        if f.get("unit"):
            d["unit"] = str(f["unit"]).strip()[:12]
        if typ == "money":
            cur = str(f.get("currency") or "GBP").upper()[:3]
            d["currency"] = cur
        if typ == "choice":
            opts = [str(o).strip()[:30] for o in (f.get("options") or []) if str(o).strip()]
            if not opts or len(opts) > 30:
                raise bad(f"字段「{key}」是 choice，要给 1–30 个 options", f'Field "{key}" is a choice: give 1–30 options')
            d["options"] = list(dict.fromkeys(opts))
        if f.get("required"):
            d["required"] = True
        out.append(d)
    return out


# —— 值：存进去之前统一格式 ——————————————————————————————————————————————

def coerce(field: dict, value: Any) -> Any:
    """按字段类型把值变成存进 JSON 的样子；认不出来就 400，告诉写的人该怎么写。"""
    if value is None or value == "":
        return None
    t, key = field["type"], field["key"]
    try:
        if t in ("number", "money"):
            if isinstance(value, bool):
                raise ValueError
            x = float(str(value).replace(",", "").replace("£", "").replace("$", "").strip())
            if t == "money":
                return round(x, 2)
            return int(x) if x.is_integer() else round(x, 4)
        if t == "bool":
            if isinstance(value, bool):
                return value
            s = str(value).strip().lower()
            if s in ("true", "1", "yes", "y", "是"):
                return True
            if s in ("false", "0", "no", "n", "否"):
                return False
            raise ValueError
        if t == "date":
            return date.fromisoformat(str(value).strip()[:10]).isoformat()
        if t == "datetime":
            dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
            dt = dt if dt.tzinfo else dt.replace(tzinfo=TZ)
            return dt.astimezone(TZ).isoformat(timespec="minutes")
        if t == "choice":
            s = str(value).strip()
            if s not in field["options"]:
                raise bad(f"字段「{key}」只能是：{' / '.join(field['options'])}", f'Field "{key}" must be one of: {", ".join(field["options"])}')
            return s
        return str(value).strip()[:MAX_TEXT]
    except (ValueError, TypeError):
        hint = {"number": "数字", "money": "金额（数字）", "bool": "true / false", "date": "YYYY-MM-DD", "datetime": "ISO 时间"}.get(t, t)
        hint_en = {"number": "a number", "money": "an amount (number)", "bool": "true / false", "date": "YYYY-MM-DD", "datetime": "an ISO time"}.get(t, t)
        raise bad(f"字段「{key}」的值「{value}」不对，要写成 {hint}", f'Field "{key}": "{value}" is not valid, write {hint_en}') from None


def clean_row(coll: dict, raw: Any, partial: bool = False) -> dict:
    if not isinstance(raw, dict):
        raise bad("一行写成 {字段: 值}", "A row is {field: value}")
    fields = {f["key"]: f for f in coll["fields"]}
    unknown = [k for k in raw if k not in fields]
    if unknown:
        raise bad(f"「{coll['name']}」没有这些字段：{', '.join(unknown)}；有的是 {', '.join(fields)}",
                  f'"{coll["name"]}" has no field(s) {", ".join(unknown)}; it has {", ".join(fields)}')
    out = {k: coerce(fields[k], v) for k, v in raw.items()}
    if not partial:
        missing = [k for k, f in fields.items() if f.get("required") and out.get(k) is None]
        if missing:
            raise bad(f"缺必填字段：{', '.join(missing)}", f"Missing required field(s): {', '.join(missing)}")
    return out


# —— 格式化：服务端按请求的语言写好，app 只管显示 ——————————————————————————————

def fmt_num(x: float | None, digits: int | None = None) -> str:
    if x is None:
        return "—"
    if digits is None:
        digits = 0 if float(x).is_integer() or abs(x) >= 100 else 1
    return f"{x:,.{digits}f}"


def fmt_money(x: float | None, currency: str = "GBP") -> str:
    if x is None:
        return "—"
    sym = CURRENCY.get(currency, currency + " ")
    sign = "−" if x < 0 else ""
    v = round(abs(x), 2)
    return f"{sign}{sym}{v:,.0f}" if v.is_integer() else f"{sign}{sym}{v:,.2f}"  # £900、£23.40


def fmt_date(s: str | None) -> str:
    if not s:
        return "—"
    try:
        d = date.fromisoformat(s[:10])
    except ValueError:
        return s
    today = datetime.now(TZ).date()
    if d == today:
        return L("今天", "Today")
    if d == today + timedelta(days=1):
        return L("明天", "Tomorrow")
    if d == today - timedelta(days=1):
        return L("昨天", "Yesterday")
    if d.year != today.year:
        return f"{d.year}/{d.month}/{d.day}" if lang() == "zh" else d.strftime("%-d %b %Y")
    return f"{d.month}/{d.day}" if lang() == "zh" else d.strftime("%-d %b")


def fmt_minutes(x: float) -> str:
    """分钟数：一小时以上写成 6h09（和 app 别处的睡眠时长一样），不到一小时写 45 min。"""
    m = round(x)
    return f"{m // 60}h{m % 60:02d}" if m >= 60 else f"{m} min"


def fmt_value(field: dict | None, v: Any) -> str:
    if v is None or v == "":
        return "—"
    t = (field or {}).get("type", "text")
    unit = (field or {}).get("unit")
    if t == "number" and unit == "min":
        return fmt_minutes(float(v))
    if t == "money":
        return fmt_money(float(v), field.get("currency", "GBP"))
    if t == "number":
        return f"{fmt_num(float(v))}{(' ' + unit) if unit else ''}"
    if t == "bool":
        return L("是", "Yes") if v else L("否", "No")
    if t == "date":
        return fmt_date(str(v))
    if t == "datetime":
        s = str(v)
        return f"{fmt_date(s)} {s[11:16]}".strip()
    return str(v)


def fmt_stat(x: float | None, fmt: str | None, unit: str | None, currency: str = "GBP") -> str:
    if x is None:
        return "—"
    if unit == "min" and fmt in (None, "number", "int"):
        return fmt_minutes(x)
    if fmt == "money":
        return fmt_money(x, currency)
    if fmt == "percent":
        return f"{fmt_num(x * 100 if abs(x) <= 1.5 else x, 0)}%"
    s = fmt_num(round(x) if fmt == "int" else x)
    return f"{s}{(' ' + unit) if unit else ''}"


def render(template: str, row: dict, fields: dict[str, dict]) -> str:
    """「{name} · {qty}」这种写法：花括号里是字段名，按字段类型格式化；一个字段名就是那个字段的值。空值那一段连同分隔符去掉。"""
    if not template:
        return ""
    if KEY.match(template) or template in SYSTEM:
        return fmt_value(fields.get(template) or {"type": SYSTEM.get(template, "text")}, row.get(template))

    def one(m: re.Match) -> str:
        k = m.group(1)
        return fmt_value(fields.get(k) or {"type": SYSTEM.get(k, "text")}, row.get(k)) if row.get(k) not in (None, "") else "\x00"
    parts = [p for p in re.sub(r"\{([a-z_][a-z0-9_]*)\}", one, template).split(" · ")]
    return " · ".join(p for p in parts if "\x00" not in p and p.strip()).strip()


# —— 查询 ————————————————————————————————————————————————————————

def today() -> date:
    return datetime.now(TZ).date()


def rel_date(token: Any) -> str | None:
    """today / tomorrow / yesterday / week（这周一）/ lastweek / nextweek / month（这个月 1 号）/ lastmonth / year / ±Nd ±Nw ±Nm / YYYY-MM-DD。"""
    s = str(token).strip().lower()
    t = today()
    monday = t - timedelta(days=t.weekday())
    first = t.replace(day=1)
    named = {"today": t, "tomorrow": t + timedelta(days=1), "yesterday": t - timedelta(days=1), "week": monday,
             "lastweek": monday - timedelta(days=7), "nextweek": monday + timedelta(days=7), "month": first,
             "lastmonth": (first - timedelta(days=1)).replace(day=1), "year": t.replace(month=1, day=1)}
    if s in named:
        return named[s].isoformat()
    m = re.fullmatch(r"([+-])(\d{1,3})([dwm])", s)
    if m:
        n = int(m.group(2)) * (1 if m.group(1) == "+" else -1)
        if m.group(3) == "d":
            return (t + timedelta(days=n)).isoformat()
        if m.group(3) == "w":
            return (t + timedelta(weeks=n)).isoformat()
        y, mo = divmod(t.month - 1 + n, 12)
        return t.replace(year=t.year + y, month=mo + 1, day=min(t.day, 28)).isoformat()
    try:
        return date.fromisoformat(s[:10]).isoformat()
    except ValueError:
        return None


def field_of(coll: dict, key: str) -> tuple[str, dict]:
    """字段名 → (SQL 表达式, 字段定义)。字段名先按表结构校验过，才拼进 JSON 路径。"""
    if key == "_created":
        return "created_at", {"key": key, "type": "datetime"}
    if key == "_updated":
        return "updated_at", {"key": key, "type": "datetime"}
    for f in coll["fields"]:
        if f["key"] == key:
            return f"json_extract(data, '$.{key}')", f
    raise bad(f"「{coll['name']}」没有字段「{key}」；有的是 {', '.join(f['key'] for f in coll['fields'])}",
              f'"{coll["name"]}" has no field "{key}"; it has {", ".join(f["key"] for f in coll["fields"])}')


def cmp_value(f: dict, v: Any) -> Any:
    t = f["type"]
    if t in ("date", "datetime"):
        d = rel_date(v) if t == "date" or len(str(v)) <= 10 or not re.match(r"\d{4}-", str(v)) else str(v)
        if d is None:
            raise bad(f"「{f['key']}」是日期，值「{v}」认不出来（today / week / +3d / 2026-09-30 这种）",
                      f'"{f["key"]}" is a date: can\'t read "{v}" (today / week / +3d / 2026-09-30)')
        return d
    if t in ("number", "money"):
        try:
            return float(v)
        except (TypeError, ValueError):
            raise bad(f"「{f['key']}」是数字，值「{v}」不是数字", f'"{f["key"]}" is a number: "{v}" isn\'t') from None
    if t == "bool":
        return 1 if (v is True or str(v).lower() in ("true", "1", "yes")) else 0
    return str(v)


def where_sql(coll: dict, where: Any) -> tuple[str, list]:
    if where in (None, []):
        return "", []
    if not isinstance(where, list):
        raise bad("where 写成 [[字段, 运算, 值], …]", "where is [[field, op, value], …]")
    parts, params = [], []
    for c in where:
        if not isinstance(c, list) or len(c) < 2 or c[1] not in OPS:
            raise bad(f"where 的一条 {c} 不对：[字段, 运算, 值]，运算是 {' '.join(OPS)}", f"Bad where clause {c}: [field, op, value], op is one of {' '.join(OPS)}")
        expr, f = field_of(coll, str(c[0]))
        op, v = c[1], (c[2] if len(c) > 2 else None)
        if op == "empty":
            parts.append(f"({expr} IS NULL OR {expr} = '')")
        elif op == "not_empty":
            parts.append(f"({expr} IS NOT NULL AND {expr} != '')")
        elif op in ("in", "not_in"):
            vals = [cmp_value(f, x) for x in (v if isinstance(v, list) else [v])]
            if not vals:
                raise bad("in 要给一个列表", "in needs a list")
            parts.append(f"{expr} {'IN' if op == 'in' else 'NOT IN'} ({','.join('?' * len(vals))})")
            params += vals
        elif op == "contains":
            parts.append(f"instr(lower({expr}), lower(?)) > 0")
            params.append(str(v))
        else:
            cv = cmp_value(f, v)
            if f["type"] == "datetime" and isinstance(cv, str) and len(cv) == 10:
                expr = f"substr({expr}, 1, 10)"  # 拿日期比时间：只比日期那一段
            parts.append(f"{expr} {'!=' if op == '!=' else op} ?")
            params.append(cv)
    return " AND " + " AND ".join(parts), params


def order_sql(coll: dict, sort: Any) -> str:
    keys = sort if isinstance(sort, list) else ([sort] if sort else [])
    out = []
    for k in keys[:3]:
        k = str(k)
        desc = k.startswith("-")
        expr, f = field_of(coll, k.lstrip("-"))
        if f["type"] == "choice":  # 选项按定义的顺序排，不按字母；选项是校验过的短字符串，单引号转义后内联
            cases = " ".join(f"WHEN '{o.replace(chr(39), chr(39) * 2)}' THEN {i}" for i, o in enumerate(f["options"]))
            expr = f"CASE {expr} {cases} ELSE {len(f['options'])} END"
        out.append(f"{expr} {'DESC' if desc else 'ASC'} NULLS LAST")
    out.append("created_at DESC")
    return " ORDER BY " + ", ".join(out)


def base(conn: sqlite3.Connection, agent: str, q: dict) -> tuple[dict, str, list]:
    if not isinstance(q, dict) or not q.get("from"):
        raise bad("查询要写 from（哪张表）", "A query needs from (which table)")
    v = virtual(conn, str(q["from"]))
    if v:
        coll, sub, sp = v
        w, p = where_sql(coll, q.get("where"))
        return coll, f"FROM {sub} AS records WHERE 1=1{w}", [*sp, *p]  # noqa: S608 — 子查询是写死的，指标名按规则校验过、走参数
    coll = get_coll(conn, agent, str(q["from"]))
    w, p = where_sql(coll, q.get("where"))
    return coll, f"FROM records WHERE agent=? AND collection=? AND deleted_at IS NULL{w}", [agent, coll["name"], *p]


def run_rows(conn: sqlite3.Connection, agent: str, q: dict) -> tuple[dict, list[dict], int]:
    coll, frm, params = base(conn, agent, q)
    total = conn.execute(f"SELECT COUNT(*) {frm}", params).fetchone()[0]  # noqa: S608 — 字段名已按表结构校验，值全是参数
    limit = max(1, min(int(q.get("limit") or LIST_MAX), LIST_MAX))
    rows = conn.execute(f"SELECT id, data, created_at, updated_at {frm}{order_sql(coll, q.get('sort'))} LIMIT {limit}", params).fetchall()  # noqa: S608
    out = []
    for r in rows:
        d = json.loads(r["data"])
        d["_created"], d["_updated"] = r["created_at"], r["updated_at"]
        out.append({"id": r["id"], "data": d})
    return coll, out, total


def agg_values(vals: list[float | None], agg: str) -> float | None:
    xs = [float(v) for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if agg == "count":
        return float(len(vals))
    if not xs:
        return None if agg != "sum" else 0.0
    return {"sum": sum(xs), "avg": sum(xs) / len(xs), "min": min(xs), "max": max(xs), "last": xs[-1]}[agg]


def run_scalar(conn: sqlite3.Connection, agent: str, q: Any) -> tuple[float | None, dict | None]:
    """一个数：{from, where, agg, field} 或 {ratio: [Q1, Q2]}。返回 (值, 字段定义：给格式化用)。"""
    if isinstance(q, (int, float)) and not isinstance(q, bool):
        return float(q), None
    if not isinstance(q, dict):
        raise bad("数字要写成查询 {from, agg, field} 或直接写数字", "A number is a query {from, agg, field} or a literal number")
    if "ratio" in q:
        pair = q["ratio"]
        if not isinstance(pair, list) or len(pair) != 2:
            raise bad("ratio 要两个查询：[分子, 分母]", "ratio needs two queries: [numerator, denominator]")
        a, _ = run_scalar(conn, agent, pair[0])
        b, _ = run_scalar(conn, agent, pair[1])
        return (a / b if a is not None and b else None), None
    agg = q.get("agg", "count")
    if agg not in AGGS:
        raise bad(f"agg 只能是 {' / '.join(AGGS)}", f"agg must be one of {', '.join(AGGS)}")
    coll, frm, params = base(conn, agent, q)
    if agg == "count":
        return float(conn.execute(f"SELECT COUNT(*) {frm}", params).fetchone()[0]), None  # noqa: S608
    if not q.get("field"):
        raise bad(f"agg {agg} 要写 field（算哪个字段）", f"agg {agg} needs a field")
    expr, f = field_of(coll, str(q["field"]))
    if agg == "last":
        dexpr = field_of(coll, str(q["date"]))[0] if q.get("date") else "created_at"
        r = conn.execute(f"SELECT {expr} v {frm} ORDER BY {dexpr} DESC NULLS LAST, created_at DESC LIMIT 1", params).fetchone()  # noqa: S608
        v = r["v"] if r else None
        return (float(v) if isinstance(v, (int, float)) else None), f
    fn = {"sum": "SUM", "avg": "AVG", "min": "MIN", "max": "MAX"}[agg]
    v = conn.execute(f"SELECT {fn}({expr}) {frm}", params).fetchone()[0]  # noqa: S608
    return (float(v) if v is not None else (0.0 if agg == "sum" else None)), f


def parse_range(rng: Any, by: str) -> int:
    m = re.fullmatch(r"(\d{1,3})([dwm])", str(rng or "").strip().lower())
    if not m:
        return {"day": 14, "week": 8, "month": 6}[by]
    n, u = int(m.group(1)), m.group(2)
    days = n * {"d": 1, "w": 7, "m": 30}[u]
    per = {"day": 1, "week": 7, "month": 30}[by]
    return max(1, min(SERIES_MAX, round(days / per)))


def bucket_start(d: date, by: str) -> date:
    if by == "week":
        return d - timedelta(days=d.weekday())
    if by == "month":
        return d.replace(day=1)
    return d


def run_series(conn: sqlite3.Connection, agent: str, q: dict) -> tuple[list[dict], dict | None]:
    """按天 / 周 / 月分组：[{key: 这一段开头的日期, value, current}]，没有记录的段是 0（count / sum）或 null。"""
    by = q.get("by", "day")
    if by not in ("day", "week", "month"):
        raise bad("by 只能是 day / week / month", "by must be day, week or month")
    agg = q.get("agg", "count")
    if agg not in AGGS:
        raise bad(f"agg 只能是 {' / '.join(AGGS)}", f"agg must be one of {', '.join(AGGS)}")
    coll, frm, params = base(conn, agent, q)
    dexpr, df = field_of(coll, str(q.get("date") or "_created"))
    if df["type"] not in ("date", "datetime"):
        raise bad(f"date 要写日期字段，「{df['key']}」不是", f'date must be a date field; "{df["key"]}" is not')
    vexpr, vf = (field_of(coll, str(q["field"])) if q.get("field") else ("1", None))
    if agg != "count" and not q.get("field"):
        raise bad(f"agg {agg} 要写 field", f"agg {agg} needs a field")
    n = parse_range(q.get("range"), by)
    t = today()
    starts: list[date] = []
    cur = bucket_start(t, by)
    for _ in range(n):
        starts.append(cur)
        cur = bucket_start(cur - timedelta(days=1), by)
    starts.reverse()
    rows = conn.execute(f"SELECT substr({dexpr}, 1, 10) d, {vexpr} v {frm} AND substr({dexpr}, 1, 10) >= ? ORDER BY {dexpr}",  # noqa: S608
                        [*params, starts[0].isoformat()]).fetchall()
    groups: dict[str, list] = {s.isoformat(): [] for s in starts}
    for r in rows:
        try:
            k = bucket_start(date.fromisoformat(r["d"]), by).isoformat()
        except (TypeError, ValueError):
            continue
        if k in groups:
            groups[k].append(r["v"])
    cur_key = bucket_start(t, by).isoformat()
    return [{"key": k, "value": agg_values(v, agg), "current": k == cur_key} for k, v in groups.items()], vf


# —— 积木：校验（存之前）和算数据（给 app 之前） ——————————————————————————————————

def check_query(conn: sqlite3.Connection, agent: str, q: Any, kind: str) -> None:
    """存之前先跑一遍：表、字段、运算写错了当场报错，别等 app 里显示「这一块出错了」。"""
    if kind == "rows":
        run_rows(conn, agent, q)
    elif kind == "series":
        run_series(conn, agent, q)
    else:
        run_scalar(conn, agent, q)


def clean_row_spec(raw: Any) -> dict:
    spec = raw if isinstance(raw, dict) else {"title": str(raw or "")}
    out: dict[str, Any] = {"title": str(spec.get("title") or "")[:200]}
    if not out["title"]:
        raise bad("列表要写 row.title（显示哪个字段，或「{name} · {qty}」）", 'A list needs row.title (a field, or "{name} · {qty}")')
    sub = spec.get("sub")
    if sub:
        out["sub"] = [str(s)[:200] for s in (sub if isinstance(sub, list) else [sub])][:3]
    if spec.get("right"):
        out["right"] = str(spec["right"])[:200]
    badges = spec.get("badge") or []
    if isinstance(badges, dict):
        badges = [badges]
    out["badge"] = []
    for b in badges[:4]:
        if not isinstance(b, dict) or b.get("op") not in OPS or not b.get("field") or not b.get("text"):
            raise bad("badge 写成 {field, op, value, text, tone}", "badge is {field, op, value, text, tone}")
        out["badge"].append({"field": str(b["field"]), "op": b["op"], "value": b.get("value"), "text": str(b["text"])[:20],
                             "tone": b.get("tone") if b.get("tone") in TONES else "neutral"})
    return out


def clean_block(conn: sqlite3.Connection, agent: str, raw: Any, anchors: tuple[str, ...]) -> dict:
    if not isinstance(raw, dict):
        raise bad("每一块写成 {id, type, title, …}", "Each block is {id, type, title, …}")
    bid, typ = str(raw.get("id") or "").strip(), str(raw.get("type") or "").strip()
    if not BLOCK_ID.match(bid):
        raise bad(f"积木 id「{bid}」不行：小写字母或数字开头，只用 a-z 0-9 _ -", f'Bad block id "{bid}": a-z 0-9 _ - only')
    if typ not in TYPES:
        raise bad(f"积木「{bid}」的 type 只能是 {' / '.join(TYPES)}", f'Block "{bid}": type must be one of {", ".join(TYPES)}')
    b: dict[str, Any] = {"id": bid, "type": typ, "title": str(raw.get("title") or "").strip()[:40]}
    for k in ("after", "caption", "empty"):
        if raw.get(k):
            b[k] = str(raw[k]).strip()[:120]
    if raw.get("hidden"):
        b["hidden"] = True
    if typ == "stat":
        items = raw.get("items") or []
        if not isinstance(items, list) or not 1 <= len(items) <= 4:
            raise bad(f"「{bid}」stat 要 1–4 个 items", f'"{bid}": stat needs 1–4 items')
        b["items"] = []
        for it in items:
            if not isinstance(it, dict) or "value" not in it:
                raise bad(f"「{bid}」stat 的每一项写成 {{label, value: 查询}}", f'"{bid}": each stat item is {{label, value: query}}')
            check_query(conn, agent, it["value"], "scalar")
            one = {"label": str(it.get("label") or "")[:30], "value": it["value"]}
            if it.get("compare") is not None:
                check_query(conn, agent, it["compare"], "scalar")
                one["compare"] = it["compare"]
                one["compareLabel"] = str(it.get("compareLabel") or "")[:20]
                one["good"] = it.get("good") if it.get("good") in ("up", "down") else "up"
            for k in ("format", "unit", "sub"):
                if it.get(k):
                    one[k] = str(it[k])[:30]
            if one.get("format") and one["format"] not in FORMATS:
                raise bad(f"format 只能是 {' / '.join(FORMATS)}", f"format must be one of {', '.join(FORMATS)}")
            b["items"].append(one)
    elif typ == "progress":
        check_query(conn, agent, raw.get("value"), "scalar")
        target = raw.get("target")
        if target is None:
            raise bad(f"「{bid}」progress 要写 target（数字或查询）", f'"{bid}": progress needs a target (number or query)')
        check_query(conn, agent, target, "scalar")
        b.update(value=raw["value"], target=target, style="bar" if raw.get("style") == "bar" else "ring")
        for k in ("label", "format", "unit"):
            if raw.get(k):
                b[k] = str(raw[k])[:40]
    elif typ == "chart":
        series = raw.get("series")
        check_query(conn, agent, series, "series")
        b.update(series=series, chart="line" if raw.get("chart") == "line" else "bar")
        for k in ("format", "unit", "summary"):
            if raw.get(k):
                b[k] = str(raw[k])[:40]
    elif typ in ("list", "checklist"):
        check_query(conn, agent, raw.get("source"), "rows")
        b["source"] = raw["source"]
        b["row"] = clean_row_spec(raw.get("row"))
        coll = get_coll(conn, agent, str(raw["source"]["from"]))
        if coll.get("readonly"):
            if typ == "checklist" or raw.get("rowActions"):
                raise bad(f"「{coll['name']}」是只读的（Apple 健康），不能做清单或加 rowActions", f'"{coll["name"]}" is read-only (Apple Health): no checklist or rowActions')
            raw = {**raw, "edit": False}
        for badge in b["row"]["badge"]:
            field_of(coll, badge["field"])
        if raw.get("limit"):
            b["limit"] = max(1, min(int(raw["limit"]), 20))
        if raw.get("group"):
            field_of(coll, str(raw["group"]))
            b["group"] = str(raw["group"])
        if raw.get("edit") is not None:
            b["edit"] = bool(raw["edit"])
        if typ == "list":
            b["style"] = "chips" if raw.get("style") == "chips" else "rows"
            acts = raw.get("rowActions") or []
            b["rowActions"] = []
            for a in acts[:3]:
                if not isinstance(a, dict) or not a.get("label") or not isinstance(a.get("set"), dict):
                    raise bad("rowActions 写成 {label, set: {字段: 值 或 +1 / -1 / today / now}}", "rowActions is {label, set: {field: value, +1, -1, today or now}}")
                for k in a["set"]:
                    field_of(coll, k)
                b["rowActions"].append({"label": str(a["label"])[:16], "set": a["set"]})
        else:
            chk = str(raw.get("check") or "")
            _, f = field_of(coll, chk)
            if f["type"] != "bool":
                raise bad(f"「{bid}」checklist 的 check 要是 bool 字段", f'"{bid}": checklist.check must be a bool field')
            b["check"] = chk
    elif typ == "text":
        if raw.get("source"):
            check_query(conn, agent, raw["source"], "rows")
            coll = get_coll(conn, agent, str(raw["source"]["from"]))
            field_of(coll, str(raw.get("field") or ""))
            b.update(source=raw["source"], field=str(raw["field"]))
        else:
            text = str(raw.get("text") or "").strip()
            if not text:
                raise bad(f"「{bid}」text 要写 text，或者 source + field", f'"{bid}": text needs text, or source + field')
            b["text"] = text[:800]
    else:  # action
        acts = raw.get("actions") or ([raw["action"]] if isinstance(raw.get("action"), dict) else [])
        if not 1 <= len(acts) <= 3:
            raise bad(f"「{bid}」action 要 1–3 个按钮（actions）", f'"{bid}": action needs 1–3 buttons (actions)')
        b["actions"] = []
        for a in acts:
            kind = a.get("kind") if isinstance(a, dict) else None
            if kind not in ACTION_KINDS or not a.get("label"):
                raise bad("按钮写成 {kind: ask|upload|form, label, …}", "A button is {kind: ask|upload|form, label, …}")
            one = {"kind": kind, "label": str(a["label"])[:20]}
            if kind in ("ask", "upload"):
                one["message"] = str(a.get("message") or "").strip()[:300]
                if kind == "ask" and not one["message"]:
                    raise bad("ask 按钮要写 message（点了发给 Agent 的那句话）", "An ask button needs message (what it sends to the Agent)")
                if kind == "upload":
                    acc = [x for x in (a.get("accept") or ["camera", "photos", "files"]) if x in ("camera", "photos", "files")]
                    one["accept"] = acc or ["camera", "photos", "files"]
            else:
                if get_coll(conn, agent, str(a.get("collection") or "")).get("readonly"):
                    raise bad("form 按钮不能写进只读的来源", "A form button can't write to a read-only source")
                one["collection"] = str(a["collection"])
                if isinstance(a.get("defaults"), dict):
                    one["defaults"] = a["defaults"]
            if a.get("primary"):
                one["primary"] = True
            b["actions"].append(one)
    return b


def clean_blocks(conn: sqlite3.Connection, agent: str, raw: Any, dashboard: str | None) -> list[dict]:
    blocks = raw.get("blocks") if isinstance(raw, dict) else raw
    if not isinstance(blocks, list):
        raise bad("看板写成 {\"blocks\": [ … ]}", 'A board is {"blocks": [ … ]}')
    if len(blocks) > MAX_BLOCKS:
        raise bad(f"一个看板最多 {MAX_BLOCKS} 块", f"At most {MAX_BLOCKS} blocks per board")
    anchors = anchors_of(dashboard)
    out = [clean_block(conn, agent, b, anchors) for b in blocks]
    ids = [b["id"] for b in out]
    if len(set(ids)) != len(ids):
        raise bad("积木 id 不能重复", "Block ids must be unique")
    for b in out:
        after = b.get("after")
        if after and after not in anchors and after not in ids:
            raise bad(f"「{b['id']}」的 after「{after}」不存在；能写的是 {', '.join(anchors)} 或别的积木 id",
                      f'"{b["id"]}": after "{after}" doesn\'t exist; use one of {", ".join(anchors)} or another block id')
        if after == b["id"]:
            raise bad("after 不能写自己", "after can't point to the block itself")
    return out


def match(op: str, have: Any, want: Any) -> bool:
    if op == "empty":
        return have in (None, "")
    if op == "not_empty":
        return have not in (None, "")
    if have is None:
        return False
    if op in ("in", "not_in"):
        vals = want if isinstance(want, list) else [want]
        return (have in vals) == (op == "in")
    if op == "contains":
        return str(want).lower() in str(have).lower()
    try:
        a, b = (float(have), float(want)) if isinstance(have, (int, float)) and not isinstance(have, bool) else (str(have), str(want))
    except (TypeError, ValueError):
        return False
    return {"=": a == b, "!=": a != b, ">": a > b, ">=": a >= b, "<": a < b, "<=": a <= b}[op]


def badge_of(row: dict, rules: list[dict], fields: dict[str, dict]) -> dict | None:
    for r in rules:
        f = fields.get(r["field"]) or {"type": SYSTEM.get(r["field"], "text")}
        want = r.get("value")
        if f["type"] in ("date", "datetime") and want is not None and r["op"] not in ("empty", "not_empty"):
            want = rel_date(want)
            have = str(row.get(r["field"]) or "")[:10] or None
        else:
            have = row.get(r["field"])
        if match(r["op"], have, want):
            return {"text": r["text"], "tone": r["tone"]}
    return None


def row_out(r: dict, spec: dict, coll: dict, fields: dict[str, dict]) -> dict:
    d = r["data"]
    out = {"id": r["id"], "title": render(spec["title"], d, fields), "data": {k: v for k, v in d.items() if not k.startswith("_")},
           "display": {f["key"]: fmt_value(f, d.get(f["key"])) for f in coll["fields"]}}
    if spec.get("sub"):
        out["sub"] = " · ".join(x for x in (render(s, d, fields) for s in spec["sub"]) if x and x != "—")
    if spec.get("right"):
        out["right"] = render(spec["right"], d, fields)
    b = badge_of(d, spec.get("badge") or [], fields)
    if b:
        out["badge"] = b
    return out


def resolve(conn: sqlite3.Connection, agent: str, b: dict) -> dict:
    """一块积木要显示的数据。出错只影响这一块（error），不影响整个看板。"""
    typ = b["type"]
    try:
        if typ == "stat":
            items = []
            for it in b["items"]:
                v, f = run_scalar(conn, agent, it["value"])
                cur = (f or {}).get("currency", "GBP")
                fmt = it.get("format") or ("money" if (f or {}).get("type") == "money" else None)
                unit = it.get("unit") or (None if fmt == "money" else (f or {}).get("unit"))
                one = {"label": it["label"], "value": v, "text": fmt_stat(v, fmt, unit, cur)}
                if it.get("sub"):
                    one["sub"] = it["sub"]
                if "compare" in it:
                    c, _ = run_scalar(conn, agent, it["compare"])
                    if v is not None and c is not None:
                        delta = v - c
                        sign = "+" if delta > 0 else ("−" if delta < 0 else "±")
                        mag = fmt_stat(abs(delta), fmt, unit, cur)
                        one["delta"] = delta
                        one["deltaText"] = f"{it.get('compareLabel') or ''} {sign}{mag}".strip()
                        one["tone"] = "neutral" if delta == 0 else ("good" if (delta > 0) == (it.get("good", "up") == "up") else "warn")
                items.append(one)
            return {"items": items}
        if typ == "progress":
            v, f = run_scalar(conn, agent, b["value"])
            tgt, _ = run_scalar(conn, agent, b["target"])
            fmt = b.get("format") or ("money" if (f or {}).get("type") == "money" else None)
            cur = (f or {}).get("currency", "GBP")
            unit = b.get("unit") or (None if fmt == "money" else (f or {}).get("unit"))
            ratio = (v or 0) / tgt if tgt else None
            left = (tgt - (v or 0)) if tgt is not None else None
            return {"value": v, "target": tgt, "ratio": ratio, "text": fmt_stat(v, fmt, unit, cur), "targetText": fmt_stat(tgt, fmt, unit, cur),
                    "leftText": fmt_stat(abs(left), fmt, unit, cur) if left is not None else None, "over": bool(left is not None and left < 0)}
        if typ == "chart":
            pts, f = run_series(conn, agent, b["series"])
            fmt = b.get("format") or ("money" if (f or {}).get("type") == "money" else None)
            cur = (f or {}).get("currency", "GBP")
            unit = b.get("unit") or (None if fmt == "money" else (f or {}).get("unit"))
            for p in pts:
                p["text"] = fmt_stat(p["value"], fmt, unit, cur) if p["value"] is not None else ""
            vals = [p["value"] for p in pts if p["value"] is not None]
            by = b["series"].get("by", "day")
            agg = b["series"].get("agg", "count")
            summary = b.get("summary") or ""
            if not summary and vals:
                if agg in ("sum", "count"):  # 流量（花了多少、练了几次）：平均每段多少
                    avg = fmt_stat(sum(vals) / len(pts), fmt, unit, cur)
                    summary = {"day": L(f"平均每天 {avg}", f"Avg {avg} a day"), "week": L(f"平均每周 {avg}", f"Avg {avg} a week"),
                               "month": L(f"平均每月 {avg}", f"Avg {avg} a month")}[by]
                elif len(vals) > 1:  # 水平（体重、分数）：最近是多少、这段时间里升降了多少
                    d = vals[-1] - vals[0]
                    sign = "+" if d > 0 else ("−" if d < 0 else "±")
                    summary = L(f"最近 {fmt_stat(vals[-1], fmt, unit, cur)}，这段时间 {sign}{fmt_stat(abs(d), fmt, unit, cur)}",
                                f"Latest {fmt_stat(vals[-1], fmt, unit, cur)}, {sign}{fmt_stat(abs(d), fmt, unit, cur)} over this period")
                else:
                    summary = L(f"最近 {fmt_stat(vals[-1], fmt, unit, cur)}", f"Latest {fmt_stat(vals[-1], fmt, unit, cur)}")
            return {"points": pts, "by": by, "level": agg not in ("sum", "count"), "summary": summary,
                    "avgText": fmt_stat(sum(vals) / len(vals), fmt, unit, cur) if vals else None,
                    "maxText": fmt_stat(max(vals), fmt, unit, cur) if vals else None}
        if typ in ("list", "checklist"):
            coll, rows, total = run_rows(conn, agent, b["source"])
            fields = {f["key"]: f for f in coll["fields"]}
            out_rows = [row_out(r, b["row"], coll, fields) for r in rows]
            if typ == "checklist":
                for r, raw in zip(out_rows, rows):
                    r["checked"] = bool(raw["data"].get(b["check"]))
            res: dict[str, Any] = {"rows": out_rows, "total": total, "collection": coll["name"], "fields": coll["fields"]}
            if b.get("group"):
                counts: dict[str, int] = {}
                for r in rows:
                    g = r["data"].get(b["group"])
                    counts[str(g) if g not in (None, "") else ""] = counts.get(str(g) if g not in (None, "") else "", 0) + 1
                res["groups"] = [{"key": k, "count": n} for k, n in counts.items()]
            return res
        if typ == "text":
            if b.get("source"):
                coll, rows, _ = run_rows(conn, agent, {**b["source"], "limit": 1})
                txt = str(rows[0]["data"].get(b["field"]) or "") if rows else ""
                at = rows[0]["data"].get("_updated") if rows else None
                return {"text": txt, "updatedAt": at}
            return {"text": b["text"]}
        forms = {}
        for a in b["actions"]:
            if a["kind"] == "form":
                forms[a["collection"]] = get_coll(conn, agent, a["collection"])["fields"]
        return {"forms": forms} if forms else {}
    except HTTPException as e:
        return {"error": str(e.detail)}
    except (sqlite3.Error, ValueError, TypeError, KeyError) as e:
        return {"error": L(f"这一块算不出来：{e}", f"Couldn't work this block out: {e}")}


# —— 版本 ————————————————————————————————————————————————————————

def live_row(conn: sqlite3.Connection, agent: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM boards WHERE agent=? AND status='live' ORDER BY version DESC LIMIT 1", (agent,)).fetchone()


def next_version(conn: sqlite3.Connection, agent: str) -> int:
    return (conn.execute("SELECT MAX(version) FROM boards WHERE agent=?", (agent,)).fetchone()[0] or 0) + 1


def blocks_of(r: sqlite3.Row | None) -> list[dict]:
    return json.loads(r["blocks"]) if r else []


def activate_colls(conn: sqlite3.Connection, agent: str, blocks: list[dict]) -> None:
    """一版上线：它用到的草稿表转正。"""
    names = set()
    for b in blocks:
        for q in queries_of(b):
            for name in froms(q):
                names.add(name)
    for n in names:
        conn.execute("UPDATE collections SET status='active', updated_at=? WHERE agent=? AND name=? AND status='draft'", (now_iso(), agent, n))


def queries_of(b: dict) -> list[Any]:
    qs = [it.get(k) for it in b.get("items", []) for k in ("value", "compare") if it.get(k) is not None]
    qs += [b.get(k) for k in ("value", "target", "series", "source") if b.get(k) is not None]
    return qs


def froms(q: Any) -> list[str]:
    if isinstance(q, dict):
        if "ratio" in q:
            return [n for x in q["ratio"] for n in froms(x)]
        return [str(q["from"])] if q.get("from") else []
    return []


def put_live(conn: sqlite3.Connection, agent: str, blocks: list[dict], note: str, by: str, inbox_id: str | None = None) -> int:
    cur = live_row(conn, agent)
    v = next_version(conn, agent)
    conn.execute("UPDATE boards SET status='old' WHERE agent=? AND status='live'", (agent,))
    conn.execute("INSERT INTO boards(agent, version, blocks, status, note, by, inbox_id, based_on, created_at, acked_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                 (agent, v, json.dumps(blocks, ensure_ascii=False), "live", note, by, inbox_id, cur["version"] if cur else None, now_iso(),
                  now_iso() if by == "user" else None))
    activate_colls(conn, agent, blocks)
    return v


def merge(base: list[dict], mine: list[dict], theirs: list[dict]) -> list[dict]:
    """提案是照着 base 那一版写的，等点头的时候看板可能又被改过（theirs）：把提案相对 base 的增、改、删套到 theirs 上。"""
    bmap = {b["id"]: b for b in base}
    mmap = {b["id"]: b for b in mine}
    out = []
    for b in theirs:
        if b["id"] in bmap and b["id"] not in mmap:
            continue  # 提案删了它
        if b["id"] in mmap and mmap[b["id"]] != bmap.get(b["id"]):
            out.append(mmap[b["id"]])  # 提案改了它
        else:
            out.append(b)
    have = {b["id"] for b in out}
    out += [b for b in mine if b["id"] not in bmap and b["id"] not in have]  # 提案新加的
    return out


def board_json(conn: sqlite3.Connection, agent: str, r: sqlite3.Row | None, dashboard: str | None) -> dict:
    blocks = blocks_of(r)
    out_blocks = [{**b, "data": resolve(conn, agent, b)} for b in blocks]
    colls = []
    for c in conn.execute("SELECT * FROM collections WHERE agent=? AND status IN ('active','draft') ORDER BY created_at", (agent,)).fetchall():
        n = conn.execute("SELECT COUNT(*) FROM records WHERE agent=? AND collection=? AND deleted_at IS NULL", (agent, c["name"])).fetchone()[0]
        colls.append(coll_json(c, n))
    out: dict[str, Any] = {"agent": agent, "dashboard": dashboard or "none", "anchors": list(anchors_of(dashboard)), "version": r["version"] if r else 0,
                           "status": r["status"] if r else "live", "by": r["by"] if r else None, "note": r["note"] if r else "",
                           "updatedAt": r["created_at"] if r else None, "blocks": out_blocks, "collections": colls, "strip": None}
    if r and r["status"] == "live" and r["by"] != "user" and not r["acked_at"]:
        prev = conn.execute("SELECT blocks FROM boards WHERE agent=? AND version=?", (agent, r["based_on"])).fetchone() if r["based_on"] else None
        before = {b["id"] for b in (json.loads(prev["blocks"]) if prev else [])}
        out["strip"] = {"version": r["version"], "note": r["note"], "by": r["by"], "added": [b["id"] for b in blocks if b["id"] not in before],
                        "undoTo": r["based_on"] or 0}
    return out


# —— 接口 ————————————————————————————————————————————————————————

@router.get("/api/boards/{agent}")
def get_board(agent: str, version: int | None = None):
    """看板：配置 + 每块算好的数据 + 顶上的撤回条（Agent 改了、你还没点「知道了」）。version：看某一版（提案的预览）。"""
    g = group_row(agent)
    with _lock, bdb() as conn:
        r = (conn.execute("SELECT * FROM boards WHERE agent=? AND version=?", (agent, version)).fetchone() if version
             else live_row(conn, agent))
        if version and not r:
            raise bad("没有这一版", "No such version", 404)
        return {"ok": True, **board_json(conn, agent, r, g["dashboard"])}


@router.get("/api/boards/proposal/{iid}")
def proposal(iid: str):
    """收件箱里 kind=block 那张卡的预览：提案那一版 + 现在的数据。"""
    with _lock, bdb() as conn:
        r = conn.execute("SELECT * FROM boards WHERE inbox_id=? ORDER BY version DESC LIMIT 1", (iid,)).fetchone()
    if not r:
        raise bad("这张卡没有看板预览", "This card has no board preview", 404)
    g = group_row(r["agent"])
    with _lock, bdb() as conn:
        out = board_json(conn, r["agent"], r, g["dashboard"])
        base = conn.execute("SELECT blocks FROM boards WHERE agent=? AND version=?", (r["agent"], r["based_on"])).fetchone() if r["based_on"] else None
    before = {b["id"]: b for b in (json.loads(base["blocks"]) if base else [])}
    out["changed"] = [b["id"] for b in out["blocks"] if before.get(b["id"]) != {k: v for k, v in b.items() if k != "data"}]
    out["removed"] = [bid for bid in before if bid not in {b["id"] for b in out["blocks"]}]
    return {"ok": True, **out}


class BoardIn(BaseModel):
    blocks: list[dict]
    note: str = ""                 # 这一版改了什么（一句话，撤回条和改动记录里显示）
    mode: str = "apply"            # apply 直接生效 / propose 交收件箱等你点头
    by: str = "agent"              # agent / user（app 里你自己挪、藏、删）
    title: str = ""                # propose：收件箱卡的标题
    why: str = ""
    changes: list[str] = []
    dedupe: str = ""
    dryRun: bool = False           # 只校验并返回算好的样子，不存


@router.put("/api/boards/{agent}")
async def put_board(agent: str, body: BoardIn):
    """整份看板配置：apply = 马上换成这一版；propose = 存成草稿、交一张收件箱卡（kind block），点了「加上」才换。"""
    g = group_row(agent)
    if body.mode not in ("apply", "propose"):
        raise bad("mode 只能是 apply / propose", "mode must be apply or propose")
    by = "user" if body.by == "user" else "agent"
    note = body.note.strip()[:200]
    with _lock, bdb() as conn:
        blocks = clean_blocks(conn, agent, {"blocks": body.blocks}, g["dashboard"])
        if body.dryRun:
            fake = {"version": 0, "status": "draft", "by": by, "note": note, "created_at": now_iso(), "blocks": json.dumps(blocks, ensure_ascii=False),
                    "acked_at": None, "based_on": None}
            return {"ok": True, "dryRun": True, **board_json(conn, agent, fake, g["dashboard"])}  # type: ignore[arg-type]
        if body.mode == "apply":
            v = put_live(conn, agent, blocks, note, by)
    if body.mode == "apply":
        log_activity(L(f"看板换成第 {v} 版：{note or '（没写说明）'}", f"Board is now version {v}: {note or '(no note)'}"), "board",
                     actor=None if by == "user" else data.agent_label(agent))
        return {"ok": True, "version": v}
    if not body.title.strip():
        raise bad("propose 要写 title（收件箱卡的标题，比如「在饮食看板加一块『快过期』」）", "propose needs a title (the inbox card's title)")
    with _lock, bdb() as conn:
        cur = live_row(conn, agent)
        v = next_version(conn, agent)
        conn.execute("INSERT INTO boards(agent, version, blocks, status, note, by, based_on, created_at) VALUES(?,?,?,?,?,?,?,?)",
                     (agent, v, json.dumps(blocks, ensure_ascii=False), "draft", note or body.title.strip()[:200], "proposal",
                      cur["version"] if cur else None, now_iso()))
    res = await inbox.add(inbox.ItemIn(kind="block", title=body.title.strip(), source=agent, why=body.why, changes=body.changes,
                                       approveLabel=L("加上", "Add it"), dedupe=body.dedupe or f"board:{agent}:v{v}"))
    if not isinstance(res, dict):  # 30 天内被拒过的同一件事（inbox 回的 409）：草稿作废，原样告诉调用的人
        with _lock, bdb() as conn:
            conn.execute("UPDATE boards SET status='rejected' WHERE agent=? AND version=?", (agent, v))
        return res
    iid = res["id"]
    with _lock, bdb() as conn:
        conn.execute("UPDATE boards SET status='superseded' WHERE inbox_id=? AND status='draft'", (iid,))  # 同一张卡上一版的草稿
        conn.execute("UPDATE boards SET inbox_id=? WHERE agent=? AND version=?", (iid, agent, v))
    return {"ok": True, "version": v, "inboxId": iid, **({"updated": True} if res.get("updated") else {})}


async def on_block_decided(it: dict, action: str) -> dict | None:
    """收件箱里 kind=block 的卡被点了：同意 → 草稿并进现在的看板、上线，卡片直接标做完；拒绝 / 撤回 → 草稿作废，草稿里新建的表归档。"""
    iid = it["id"]
    with _lock, bdb() as conn:
        d = conn.execute("SELECT * FROM boards WHERE inbox_id=? AND status='draft' ORDER BY version DESC LIMIT 1", (iid,)).fetchone()
        if not d:
            return None
        agent = d["agent"]
        if action == "approve":
            cur = live_row(conn, agent)
            mine = blocks_of(d)
            if cur and cur["version"] != d["based_on"]:
                base_r = conn.execute("SELECT blocks FROM boards WHERE agent=? AND version=?", (agent, d["based_on"])).fetchone() if d["based_on"] else None
                mine = merge(json.loads(base_r["blocks"]) if base_r else [], mine, blocks_of(cur))
            conn.execute("UPDATE boards SET status='approved' WHERE agent=? AND version=?", (agent, d["version"]))  # 草稿本身不进改动记录，上线的是它的副本
            v = put_live(conn, agent, mine, d["note"], "proposal", iid)
            return {"result": L(f"看板换成了第 {v} 版", f"The board is now version {v}"), "version": v}
        conn.execute("UPDATE boards SET status='rejected' WHERE agent=? AND version=?", (agent, d["version"]))
        used = {n for b in blocks_of(d) for q in queries_of(b) for n in froms(q)}
        live_used = {n for b in blocks_of(live_row(conn, agent)) for q in queries_of(b) for n in froms(q)}
        for n in used - live_used:
            conn.execute("UPDATE collections SET status='archived', updated_at=? WHERE agent=? AND name=? AND status='draft'", (now_iso(), agent, n))
    return {"rejected": True}


inbox.HOOKS["block"] = on_block_decided


class RevertIn(BaseModel):
    version: int


@router.post("/api/boards/{agent}/revert")
def revert(agent: str, body: RevertIn):
    """回到某一版（0 = 空看板）：复制成新的一版，历史不断；数据不动。顶上撤回条的「撤回」「恢复」也走这里。"""
    g = group_row(agent)
    with _lock, bdb() as conn:
        if body.version == 0:
            blocks: list[dict] = []
            note = L("回到没有积木的样子", "Back to no blocks")
        else:
            r = conn.execute("SELECT * FROM boards WHERE agent=? AND version=? AND status IN ('live','old')", (agent, body.version)).fetchone()
            if not r:
                raise bad("没有这一版（提案里没被采纳的版本回不去）", "No such version (drafts that were never approved can't be restored)", 404)
            blocks = blocks_of(r)
            was = (r["note"] or "").strip()
            note = L(f"回到「{was[:30]}」那一版", f'Back to "{was[:40]}"') if was else L(f"回到 {r['created_at'][5:16].replace('T', ' ')} 那一版", f"Back to the version of {r['created_at'][5:16].replace('T', ' ')}")
        # 旧版本里可能引用了之后被归档的表：跑一遍校验，坏了直接说
        blocks = clean_blocks(conn, agent, {"blocks": blocks}, g["dashboard"])
        v = put_live(conn, agent, blocks, note, "user")
    log_activity(L(f"{g['name']}的看板回到第 {body.version} 版", f"{g['name']}'s board went back to version {body.version}"), "board")
    return {"ok": True, "version": v}


@router.post("/api/boards/{agent}/ack")
def ack(agent: str):
    """顶上的撤回条点了「知道了」。"""
    group_row(agent)
    with _lock, bdb() as conn:
        conn.execute("UPDATE boards SET acked_at=? WHERE agent=? AND status='live' AND acked_at IS NULL", (now_iso(), agent))
    return {"ok": True}


@router.get("/api/boards/{agent}/history")
def history(agent: str, limit: int = 30):
    group_row(agent)
    with _lock, bdb() as conn:
        rows = conn.execute("SELECT * FROM boards WHERE agent=? AND status IN ('live','old','draft','rejected') ORDER BY version DESC LIMIT ?",
                            (agent, max(1, min(limit, 100)))).fetchall()
    return {"ok": True, "versions": [{"version": r["version"], "status": r["status"], "note": r["note"], "by": r["by"], "inboxId": r["inbox_id"],
                                      "basedOn": r["based_on"], "createdAt": r["created_at"],
                                      "blocks": [{"id": b["id"], "type": b["type"], "title": b.get("title", "")} for b in blocks_of(r)]} for r in rows]}


class QueryIn(BaseModel):
    query: dict
    kind: str = "rows"   # rows / scalar / series


@router.post("/api/boards/{agent}/query")
def query(agent: str, body: QueryIn):
    """Agent 自己查数据（board_ctl.py rows query），也用来试一个查询写得对不对。"""
    group_row(agent)
    with _lock, bdb() as conn:
        if body.kind == "scalar":
            v, _ = run_scalar(conn, agent, body.query)
            return {"ok": True, "value": v}
        if body.kind == "series":
            pts, _ = run_series(conn, agent, body.query)
            return {"ok": True, "points": pts}
        coll, rows, total = run_rows(conn, agent, body.query)
    return {"ok": True, "total": total, "rows": [{"id": r["id"], **r["data"]} for r in rows]}


# —— 表 ————————————————————————————————————————————————————————

class CollIn(BaseModel):
    name: str
    title: str
    fields: list[dict]
    draft: bool = False   # 草稿表：给还没点头的提案用，提案上线时转正、被拒时归档


class CollPatch(BaseModel):
    title: str | None = None
    fields: list[dict] | None = None   # 整份字段（加字段就在原来的后面接；去掉的字段，行里的值留着不删）


@router.get("/api/collections/{agent}")
def list_colls(agent: str):
    group_row(agent)
    with _lock, bdb() as conn:
        rows = conn.execute("SELECT * FROM collections WHERE agent=? AND status IN ('active','draft') ORDER BY created_at", (agent,)).fetchall()
        out = []
        for c in rows:
            n = conn.execute("SELECT COUNT(*) FROM records WHERE agent=? AND collection=? AND deleted_at IS NULL", (agent, c["name"])).fetchone()[0]
            out.append(coll_json(c, n))
    return {"ok": True, "collections": out}


@router.post("/api/collections/{agent}")
def add_coll(agent: str, body: CollIn):
    group_row(agent)
    name = body.name.strip()
    if not KEY.match(name):
        raise bad(f"表名「{name}」不行：小写字母开头，只用 a-z 0-9 _", f'Bad table name "{name}": lowercase letter first, a-z 0-9 _ only')
    fields = clean_fields(body.fields)
    ts = now_iso()
    with _lock, bdb() as conn:
        old = conn.execute("SELECT status FROM collections WHERE agent=? AND name=?", (agent, name)).fetchone()
        if old and old["status"] in ("active", "draft"):
            raise bad(f"「{name}」已经有了；改字段用 table alter", f'"{name}" already exists; change its fields with table alter', 409)
        n = conn.execute("SELECT COUNT(*) FROM collections WHERE agent=? AND status IN ('active','draft')", (agent,)).fetchone()[0]
        if n >= MAX_COLLECTIONS:
            raise bad(f"一个 Agent 最多 {MAX_COLLECTIONS} 张表", f"At most {MAX_COLLECTIONS} tables per Agent")
        conn.execute("INSERT OR REPLACE INTO collections(agent, name, title, fields, status, created_at, updated_at) VALUES(?,?,?,?,?,?,?)",
                     (agent, name, body.title.strip()[:40] or name, json.dumps(fields, ensure_ascii=False), "draft" if body.draft else "active", ts, ts))
    log_activity(L(f"建了一张表「{body.title.strip() or name}」", f'Created a table "{body.title.strip() or name}"'), "board", actor=data.agent_label(agent))
    return {"ok": True, "name": name, "status": "draft" if body.draft else "active"}


@router.patch("/api/collections/{agent}/{name}")
def patch_coll(agent: str, name: str, body: CollPatch):
    group_row(agent)
    with _lock, bdb() as conn:
        coll = get_coll(conn, agent, name)
        title = body.title.strip()[:40] if body.title is not None else coll["title"]
        fields = clean_fields(body.fields) if body.fields is not None else coll["fields"]
        conn.execute("UPDATE collections SET title=?, fields=?, updated_at=? WHERE agent=? AND name=?",
                     (title, json.dumps(fields, ensure_ascii=False), now_iso(), agent, name))
    return {"ok": True}


@router.delete("/api/collections/{agent}/{name}")
def archive_coll(agent: str, name: str):
    """归档一张表（行都留着）。看板里还有积木用它的话不让归档。"""
    group_row(agent)
    with _lock, bdb() as conn:
        get_coll(conn, agent, name)
        users = [b["id"] for b in blocks_of(live_row(conn, agent)) for q in queries_of(b) if name in froms(q)]
        if users:
            raise bad(f"看板里这些积木还在用「{name}」：{', '.join(users)}，先把它们去掉", f'These blocks still use "{name}": {", ".join(users)}; remove them first', 409)
        conn.execute("UPDATE collections SET status='archived', updated_at=? WHERE agent=? AND name=?", (now_iso(), agent, name))
    return {"ok": True}


# —— 行 ————————————————————————————————————————————————————————

class RowsIn(BaseModel):
    rows: list[dict]
    by: str = "agent"   # agent / user（app 里的表单、「记一样」）


class RowPatch(BaseModel):
    data: dict = {}     # 要改的字段
    inc: dict = {}      # 数字字段加减，比如 {"qty": -1}（「用掉 1 盒」）
    by: str = "user"


def row_json(r: sqlite3.Row) -> dict:
    return {"id": r["id"], "collection": r["collection"], "data": json.loads(r["data"]), "source": r["source"], "createdAt": r["created_at"],
            "updatedAt": r["updated_at"], "deletedAt": r["deleted_at"]}


@router.get("/api/collections/{agent}/{name}/rows")
def list_rows(agent: str, name: str, deleted: int = 0, limit: int = 200, offset: int = 0):
    """整张表（「它在记的数据」「看全部」）；deleted=1 看 30 天内删掉的。"""
    group_row(agent)
    with _lock, bdb() as conn:
        coll = get_coll(conn, agent, name)
        since = (datetime.now(TZ) - timedelta(days=RESTORE_DAYS)).isoformat(timespec="seconds")
        cond = "deleted_at IS NOT NULL AND deleted_at>=?" if deleted else "deleted_at IS NULL"
        params: list = [agent, name] + ([since] if deleted else [])
        total = conn.execute(f"SELECT COUNT(*) FROM records WHERE agent=? AND collection=? AND {cond}", params).fetchone()[0]  # noqa: S608
        rows = conn.execute(f"SELECT * FROM records WHERE agent=? AND collection=? AND {cond} ORDER BY created_at DESC LIMIT ? OFFSET ?",  # noqa: S608
                            [*params, max(1, min(limit, 500)), max(0, offset)]).fetchall()
    fields = {f["key"]: f for f in coll["fields"]}
    return {"ok": True, "collection": coll, "total": total,
            "rows": [{**row_json(r), "display": {k: fmt_value(f, json.loads(r["data"]).get(k)) for k, f in fields.items()}} for r in rows]}


@router.post("/api/collections/{agent}/{name}/rows")
def add_rows(agent: str, name: str, body: RowsIn):
    group_row(agent)
    if not body.rows:
        raise bad("rows 至少一行", "rows needs at least one row")
    if len(body.rows) > 500:
        raise bad("一次最多写 500 行", "At most 500 rows at a time")
    ts = now_iso()
    src = "user" if body.by == "user" else "agent"
    with _lock, bdb() as conn:
        coll = get_coll(conn, agent, name)
        clean = [clean_row(coll, r) for r in body.rows]
        n = conn.execute("SELECT COUNT(*) FROM records WHERE agent=? AND collection=? AND deleted_at IS NULL", (agent, name)).fetchone()[0]
        if n + len(clean) > MAX_ROWS:
            raise bad(f"「{name}」最多 {MAX_ROWS} 行，先删掉些旧的", f'"{name}" holds at most {MAX_ROWS} rows; delete some old ones first')
        ids = []
        for d in clean:
            rid = f"r-{uuid.uuid4().hex[:10]}"
            conn.execute("INSERT INTO records(id, agent, collection, data, source, created_at, updated_at) VALUES(?,?,?,?,?,?,?)",
                         (rid, agent, name, json.dumps({k: v for k, v in d.items() if v is not None}, ensure_ascii=False), src, ts, ts))
            ids.append(rid)
    if src == "user":
        log_activity(L(f"往「{coll['title']}」记了 {len(ids)} 行", f'Added {len(ids)} row(s) to "{coll["title"]}"'), "edit")
    return {"ok": True, "ids": ids}


def the_row(conn: sqlite3.Connection, rid: str, deleted: bool = False) -> sqlite3.Row:
    r = conn.execute(f"SELECT * FROM records WHERE id=? AND deleted_at IS {'NOT ' if deleted else ''}NULL", (rid,)).fetchone()  # noqa: S608
    if not r:
        raise bad("没有这一行（可能已经删了）", "No such row (it may have been deleted)", 404)
    return r


def apply_inc(coll: dict, data_: dict, inc: dict) -> None:
    fields = {f["key"]: f for f in coll["fields"]}
    for k, dv in inc.items():
        f = fields.get(k)
        if not f or f["type"] not in ("number", "money"):
            raise bad(f"inc 只能加减数字字段，「{k}」不是", f'inc only works on number fields; "{k}" is not')
        try:
            was = float(data_.get(k) or 0)
            x = was + float(dv)
        except (TypeError, ValueError):
            raise bad(f"inc 的「{k}」要是数字", f'inc "{k}" must be a number') from None
        if f["type"] == "number" and x < 0 <= was:
            x = 0  # 数量用到 0 就停在 0，不变成负数
        data_[k] = coerce(f, x)


def set_tokens(coll: dict, raw: dict) -> tuple[dict, dict]:
    """积木 rowActions 的 set：{"qty": "-1"} 是加减，{"done_at": "today"} / "now" 是今天 / 现在，别的原样。拆成 (data, inc)。"""
    fields = {f["key"]: f for f in coll["fields"]}
    d, inc = {}, {}
    for k, v in raw.items():
        f = fields.get(k) or {}
        if isinstance(v, str) and re.fullmatch(r"[+-]\d+(\.\d+)?", v) and f.get("type") in ("number", "money"):
            inc[k] = float(v)
        elif v == "today" and f.get("type") == "date":
            d[k] = today().isoformat()
        elif v in ("now", "today") and f.get("type") == "datetime":
            d[k] = datetime.now(TZ).isoformat(timespec="minutes")
        else:
            d[k] = v
    return d, inc


@router.patch("/api/rows/{rid}")
def patch_row(rid: str, body: RowPatch):
    """改一行：data 是要改的字段（null = 清空），inc 是数字加减。app 里的「改」「打勾」「用掉 1 盒」和 Agent 的 rows update 都走这里。"""
    with _lock, bdb() as conn:
        r = the_row(conn, rid)
        coll = get_coll(conn, r["agent"], r["collection"])
        cur = json.loads(r["data"])
        d, inc2 = set_tokens(coll, body.data) if body.by == "user" else (body.data, {})
        cur.update(clean_row(coll, d, partial=True))
        apply_inc(coll, cur, {**inc2, **body.inc})
        cur = {k: v for k, v in cur.items() if v is not None}
        conn.execute("UPDATE records SET data=?, updated_at=? WHERE id=?", (json.dumps(cur, ensure_ascii=False), now_iso(), rid))
        out = row_json(conn.execute("SELECT * FROM records WHERE id=?", (rid,)).fetchone())
    return {"ok": True, "row": out}


@router.delete("/api/rows/{rid}")
def delete_row(rid: str, by: str = "user"):
    """删一行：只标 deleted_at，30 天内能找回。"""
    with _lock, bdb() as conn:
        r = the_row(conn, rid)
        conn.execute("UPDATE records SET deleted_at=? WHERE id=?", (now_iso(), rid))
        coll = get_coll(conn, r["agent"], r["collection"])
    if by == "user":
        log_activity(L(f"从「{coll['title']}」删了一行", f'Deleted a row from "{coll["title"]}"'), "edit")
    return {"ok": True}


@router.post("/api/rows/{rid}/restore")
def restore_row(rid: str):
    with _lock, bdb() as conn:
        the_row(conn, rid, deleted=True)
        conn.execute("UPDATE records SET deleted_at=NULL, updated_at=? WHERE id=?", (now_iso(), rid))
    return {"ok": True}
