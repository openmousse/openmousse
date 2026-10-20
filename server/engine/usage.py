"""用量（第 9 步，2026-10-17）：engine_turns 里一轮轮的 token 和花费，按天 / 模型 / 用途汇总，给设置页的「用量」（GET /api/setup/usage，
也挂在 /api/engine/usage）。

每一轮记在哪个模型名下：真的调过的最后一个（engine_turns.models；备用链换过模型就是换上的那个），没有就是一开始要的（model）；
  按规范的写法记（models.canonical：以前带 models/ 前缀的 Gemini 型号和不带的算同一个）。
钱：engine_turns.usage.cost（loop._add_usage 按 models.PRICES 算的美元，调的那一刻的价；价目里没有的是 null）。当时没算钱、记了 token 的
  （价目那时还没有这个型号）按这一轮开始那天的价补上（models.cost(…, at=started_at)：PRICES_FROM 写了哪天起换价的照日子挑）。三种：
  metered  按量计费、算得出钱的
  unpriced 按量计费、价目里没有这个型号（钱不知道，token 照数）
  plan     用户自己的套餐（claude-code/…、chatgpt/…）：记「套餐、不计钱」，cost 不进总数；Claude Code 报的折算价（notional_cost_usd）
           单独列，只给看。Claude Code 用 API 密钥登录的那几轮照记成钱（是用户自己账上的）
用途（看 kind 和 origin）：chat = 主人自己的对话（user / relay）；background = 定时任务、心跳、后台任务、收件箱接着做的（auto / task），
  还有一次性 JSON（json）和回看 skill（skill-review）；outsider = 外人回合（名片 Agent、A2A、Doorman、第 4 批的触发：外面进来的 webhook、
  邮件、网页那一次零工具的读）；api = 对外接口（/v1、ACP）；
  cyber = 赛博；claw = OpenClaw 自己的回合（engine.record_openclaw 开了才有）；warming = 提示缓存保温（engine/warming.py，第 7 批）；
  别的 = other。
向量（按意思搜，engine/embeddings.py）不是一轮：另记在 engine_embed_usage，汇总成 embeddings 那一栏（见下面「向量的用量」）。
看图、看文档、转写、朗读、生图（第 3 批，engine/media.py）也不是一轮：另记在 engine_media_usage，汇总成 media 那一栏（media.summary）。
不算进来的：OpenClaw 没记下来的回合、这张表建起来以前算的向量（embeddings.recordedSince 是从哪天起记的）。
天按 server.json 的时区算日历日（不是 04:00 的逻辑日：和各家的账单对得上）。
"""
from __future__ import annotations

import atexit
import contextlib
import json
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from config import settings

from . import catalog, models, store
from . import embeddings as E

PURPOSES = ("chat", "background", "outsider", "api", "cyber", "claw", "warming", "other")
BACKGROUND_ORIGINS = frozenset({"auto", "task", "json", "skill-review", "skills"})
OUTSIDER_ORIGINS = frozenset({"card", "a2a", "doorman", "trigger"})  # trigger：第 4 批从外面进来的触发（webhook、邮件、盯的网页）那一次零工具的读
MAX_DAYS = 366


def purpose_of(kind: str | None, origin: str | None) -> str:
    """一轮算哪种用途（见文件开头）。"""
    k, o = str(kind or ""), str(origin or "")
    if k == "cyber":
        return "cyber"
    if k == "claw":
        return "claw"
    if k == "outsider":
        return "outsider" if o in OUTSIDER_ORIGINS else "background" if o in BACKGROUND_ORIGINS else "other"
    if k == "owner":
        if o in ("user", "relay", ""):
            return "chat"
        if o in BACKGROUND_ORIGINS:
            return "background"
        if o == "api":
            return "api"
        if o == "warming":  # 第 7 批：提示缓存保温（engine/warming.py）
            return "warming"
    return "other"


def _blank() -> dict:
    return {"turns": 0, "in": 0, "out": 0, "cacheRead": 0, "cacheWrite": 0, "cost": 0.0, "unpricedTurns": 0, "planTurns": 0,
            "notional": 0.0}


def _add(acc: dict, u: dict, billing: str) -> None:
    acc["turns"] += 1
    for src, dst in (("in", "in"), ("out", "out"), ("cache_read", "cacheRead"), ("cache_write", "cacheWrite")):
        try:
            acc[dst] += int(u.get(src) or 0)
        except (TypeError, ValueError):
            pass
    if billing == "plan":
        acc["planTurns"] += 1
        n = u.get("notional_cost_usd")
        if isinstance(n, (int, float)) and not isinstance(n, bool):
            acc["notional"] += float(n)
    elif billing == "unpriced":
        acc["unpricedTurns"] += 1
    else:
        acc["cost"] += float(u.get("cost") or 0)


def _done(acc: dict) -> dict:
    out = dict(acc)
    out["cost"] = round(out["cost"], 6)
    out["notional"] = round(out["notional"], 6) if out["notional"] else None
    return out


def billing_of(model: str, u: dict) -> str:
    """metered / unpriced / plan（见文件开头）。"""
    cost = u.get("cost")
    if models.subscription(model) and cost is None:
        return "plan"
    if cost is None or isinstance(cost, bool) or not isinstance(cost, (int, float)):
        return "unpriced"
    return "metered"


def _int(v) -> int:
    try:
        return max(0, int(v or 0))
    except (TypeError, ValueError):
        return 0


def priced(model: str, u: dict, started: str) -> dict:
    """当时没算钱、记了 token 的一轮：按这一轮开始那天的价补上 cost（价目里那天也没有的照旧 null）。套餐的、算过钱的、没有 token 的不动。"""
    if models.subscription(model) or billing_of(model, u) != "unpriced":
        return u
    tokens = {k: _int(u.get(k)) for k in ("in", "out", "cache_read", "cache_write")}
    try:
        at = datetime.fromisoformat(started)
    except (TypeError, ValueError):
        return u
    c = models.cost(model, tokens, at=at) if any(tokens.values()) else None
    return u if c is None else {**u, "cost": c}


def _day(started: str) -> str:
    try:
        return datetime.fromisoformat(started).astimezone(settings.tz).date().isoformat()
    except (TypeError, ValueError):
        return str(started or "")[:10]


def summary(days: int = 30, *, now: datetime | None = None) -> dict:
    """最近 days 天（含今天）→ {"since", "until", "days", "currency", "total", "byDay", "byModel", "byPurpose", "embeddings", "notCounted"}。
    total / byDay / byModel / byPurpose 只算一轮轮的；向量的在 embeddings（embeddings_summary）。没用过内核（库里没有那几张表）→ 全是 0，不建表。"""
    days = max(1, min(MAX_DAYS, int(days or 30)))
    end = (now or datetime.now(settings.tz)).astimezone(settings.tz)
    start = datetime.combine(end.date() - timedelta(days=days - 1), datetime.min.time(), settings.tz)
    total, by_day, by_model, by_purpose = _blank(), {}, {}, {}
    rows = []
    if store.exists():
        with store.read() as conn:
            rows = conn.execute("SELECT kind, origin, model, models, usage, started_at FROM engine_turns "
                                "WHERE julianday(started_at) >= julianday(?) ORDER BY julianday(started_at), rowid",
                                (start.isoformat(),)).fetchall()
    for r in rows:
        try:
            u = json.loads(r["usage"] or "{}")
        except ValueError:
            u = {}
        u = u if isinstance(u, dict) else {}
        try:
            called = json.loads(r["models"] or "[]")
        except ValueError:
            called = []
        model = next((m for m in reversed(called) if isinstance(m, str) and m), None) if isinstance(called, list) else None
        model = models.canonical(model or str(r["model"] or ""))  # 老记录里 google/models/… 和 google/… 记在一起
        u = priced(model, u, r["started_at"])
        billing = billing_of(model, u)
        purpose = purpose_of(r["kind"], r["origin"])
        day = _day(r["started_at"])
        _add(total, u, billing)
        _add(by_day.setdefault(day, _blank()), u, billing)
        _add(by_purpose.setdefault(purpose, _blank()), u, billing)
        m = by_model.setdefault(model, {**_blank(), "billing": set()})
        _add(m, u, billing)
        m["billing"].add(billing)
    model_rows = []
    for name, acc in by_model.items():
        kinds = acc.pop("billing")
        provider = name.split("/", 1)[0] if "/" in name else None
        model_rows.append({"model": name, "provider": provider, "plan": models.subscription(name),
                           "billing": "plan" if kinds == {"plan"} else "metered" if kinds == {"metered"} else
                           "unpriced" if kinds == {"unpriced"} else "mixed", **_done(acc)})
    model_rows.sort(key=lambda x: (-x["cost"], -(x["in"] + x["out"] + x["cacheRead"]), x["model"]))
    return {"since": start.date().isoformat(), "until": end.date().isoformat(), "days": days, "currency": "USD",
            "total": _done(total),
            "byDay": [{"day": d, **_done(a)} for d, a in sorted(by_day.items())],
            "byModel": model_rows,
            "byPurpose": [{"purpose": p, **_done(by_purpose[p])} for p in PURPOSES if p in by_purpose],
            "embeddings": embeddings_summary(start), "notCounted": [],
            "media": _media(start)}  # 第 3 批：看图、看文档、转写、朗读、生图……按次的用量（engine/media.py 的 engine_media_usage）


def _media(start: datetime) -> dict:
    from . import media
    return media.summary(start)


def spent_since(start: datetime) -> dict:
    """花费上限用（第 4 批，engine/spend.py）：start 以来一轮轮算得出钱的（metered，照 billing_of）→ {"total", "agents": {Agent: 美元},
    "purposes": {用途: 美元}, "unpriced": 轮数, "unpricedModels": [...], "plan": 轮数, "running": [还在跑的那几轮的 id]}。
    钱用记下的 usage.cost（调的那一刻的价）；还在跑的那几轮 usage 还没写（一轮结束才写），算 0，由 spend 自己记的补上。
    只读、不建表：没用过内核 = 全是 0。"""
    out: dict = {"total": 0.0, "agents": {}, "purposes": {}, "unpriced": 0, "unpricedModels": [], "plan": 0, "running": []}
    if not store.exists():
        return out
    with store.read() as conn:
        rows = conn.execute("SELECT id, kind, origin, agent, status, model, models, usage FROM engine_turns WHERE julianday(started_at) >= julianday(?)",
                            (start.isoformat(),)).fetchall()
    for r in rows:
        if r["status"] == "running":
            out["running"].append(r["id"])
            continue
        try:
            u = json.loads(r["usage"] or "{}")
            called = json.loads(r["models"] or "[]")
        except ValueError:
            u, called = {}, []
        u = u if isinstance(u, dict) else {}
        model = next((m for m in reversed(called) if isinstance(m, str) and m), None) if isinstance(called, list) else None
        model = models.canonical(model or str(r["model"] or ""))
        billing = billing_of(model, u)
        if billing == "plan":
            out["plan"] += 1
            continue
        if billing == "unpriced":
            if _int(u.get("in")) or _int(u.get("out")):  # 一个 token 都没有的（出错在调模型之前）不算
                out["unpriced"] += 1
                if model not in out["unpricedModels"]:
                    out["unpricedModels"].append(model)
            continue
        c = float(u.get("cost") or 0)
        purpose = purpose_of(r["kind"], r["origin"])
        out["total"] += c
        out["purposes"][purpose] = out["purposes"].get(purpose, 0.0) + c
        agent = str(r["agent"] or "")
        if agent:
            out["agents"][agent] = out["agents"].get(agent, 0.0) + c
    return out


# —— 向量的用量 ——————————————————————————————————————————————————————————————————
# engine/embeddings.embed 每算成一个请求调一次 embedded()：只在内存里加一下（同一个库、同一天、同一个型号、同一种的加在一起），第一笔进来
# FLUSH_SECONDS 秒后在别的线程里写一次库；看用量之前（summary）、进程退出时也写。建索引是后台成批地调，记账不拖慢它。
# engine_embed_usage 这一张归这里（mousse.db，第一次写的时候建，老库也一样）。一行 = 攒下的一份：at = 这一份里第一次调的时间；
# model = 价目表的引用 provider/model（地址认不出哪一家 = custom/<型号>）；kind = index（建索引）/ query（搜的那一句）/ test（设置页
# 测一下）；tokens = 合计，estimated = 其中按字数估的（接口没回用量的那几次）。钱看的时候算：按那一份的日子取价（models.price，价目里有的
# 才算，没有的记进 unpricedTokens）。
EMBED_SCHEMA = """
CREATE TABLE IF NOT EXISTS engine_embed_usage (
  id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, model TEXT NOT NULL, provider TEXT, kind TEXT NOT NULL,
  requests INTEGER NOT NULL, texts INTEGER NOT NULL, tokens INTEGER NOT NULL, estimated INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS engine_embed_usage_at ON engine_embed_usage(julianday(at));
"""
EMBED_KINDS = ("index", "query", "test")
FLUSH_SECONDS = 30.0
_ebuf: dict[tuple, dict] = {}  # (库, 日子, 型号, 家, 种类) → {"at", "requests", "texts", "tokens", "estimated"}
_elock = threading.Lock()
_etimer: threading.Timer | None = None


def embed_ref(spec) -> tuple[str | None, str]:
    """一套向量配置（embeddings.Spec）记在谁名下 → (家, 价目表的引用 provider/model)：Google 自己的接口 = google；OpenAI 兼容的按地址认
    目录里的哪一家（各地区的地址、server.json 里改过的地址都认）；认不出 = (None, custom/<型号>)。"""
    url = spec.url.rstrip("/")
    pid = None
    if spec.backend == "gemini":
        pid = "google" if urlsplit(url).hostname == urlsplit(E.BACKENDS["gemini"]["url"]).hostname else None
    else:
        over = models.cfg().get("providers")
        for name, e in {**catalog.CLOUD, **catalog.LOCAL}.items():
            o = over.get(name) if isinstance(over, dict) else None
            urls = [e["url"], *(r["url"] for r in (e.get("regions") or {}).values()), *([o["url"]] if isinstance(o, dict) and o.get("url") else [])]
            if url in (str(u).rstrip("/") for u in urls):
                pid = name
                break
    return pid, f"{pid or 'custom'}/{spec.model}"


def embedded(spec, texts: list[str], reported: int | None, *, kind: str) -> None:
    """记一个算成了的向量请求（只在内存里加，不碰库）。texts = 发出去的字；reported = 接口回的 token，None = 按字数估（embeddings.tokens）。
    出了错也不往外抛：记账不能让算向量出错。"""
    try:
        pid, ref = embed_ref(spec)
        n = reported if reported is not None else sum(E.tokens(t) for t in texts)
        now = datetime.now(settings.tz)
        key = (str(settings.db), now.date().isoformat(), ref, pid, kind if kind in EMBED_KINDS else "index")
        _buffer(key, {"at": now, "requests": 1, "texts": len(texts), "tokens": n, "estimated": n if reported is None else 0})
    except Exception:  # noqa: BLE001
        pass


def _buffer(key: tuple, part: dict) -> None:
    """加进攒着的那一份；攒着的从空变成有，定好 FLUSH_SECONDS 秒后在后台写。"""
    global _etimer
    with _elock:
        b = _ebuf.get(key)
        if b is None:
            _ebuf[key] = dict(part)
        else:
            b["at"] = min(b["at"], part["at"])
            for k in ("requests", "texts", "tokens", "estimated"):
                b[k] += part[k]
        if _etimer is None:
            _etimer = threading.Timer(FLUSH_SECONDS, flush)
            _etimer.daemon = True
            _etimer.start()


def flush() -> int:
    """把攒着的写进库（一份一行）→ 写了几行。定好的那一次跟着取消（攒着的空了；再来一笔再定）。写不进去的（库锁住太久……）放回去等
    下一次；库所在的目录没了的（测试的临时目录）不替它建。"""
    global _etimer
    with _elock:
        items = list(_ebuf.items())
        _ebuf.clear()
        if _etimer is not None:
            _etimer.cancel()
            _etimer = None
    by_db: dict[str, list[tuple[tuple, dict]]] = {}
    for key, b in items:
        by_db.setdefault(key[0], []).append((key, b))
    done = 0
    for db, part in by_db.items():
        path = Path(db)
        if not path.parent.is_dir():
            continue
        try:
            with contextlib.closing(sqlite3.connect(path, timeout=10)) as conn:
                conn.executescript(EMBED_SCHEMA)  # 老库：第一次写的时候建这张表
                with conn:
                    conn.executemany("INSERT INTO engine_embed_usage(at, model, provider, kind, requests, texts, tokens, estimated) VALUES(?,?,?,?,?,?,?,?)",
                                     [(b["at"].isoformat(timespec="milliseconds"), k[2], k[3], k[4], b["requests"], b["texts"], b["tokens"], b["estimated"])
                                      for k, b in part])
            done += len(part)
        except (sqlite3.Error, OSError):
            for k, b in part:
                _buffer(k, b)
    return done


atexit.register(flush)


def _eblank() -> dict:
    return {"requests": 0, "texts": 0, "tokens": 0, "estimatedTokens": 0, "cost": 0.0, "unpricedTokens": 0}


def _eadd(acc: dict, r, cost: float | None) -> None:
    acc["requests"] += int(r["requests"] or 0)
    acc["texts"] += int(r["texts"] or 0)
    acc["tokens"] += int(r["tokens"] or 0)
    acc["estimatedTokens"] += int(r["estimated"] or 0)
    if cost is None:
        acc["unpricedTokens"] += int(r["tokens"] or 0)
    else:
        acc["cost"] += cost


def _edone(acc: dict) -> dict:
    return {**acc, "cost": round(acc["cost"], 6)}


def embeddings_summary(start: datetime) -> dict:
    """向量的用量（start 以来）→ {"total", "byDay", "byModel", "byKind", "recordedSince"}，每一项 {"requests", "texts", "tokens",
    "estimatedTokens", "cost", "unpricedTokens"}。先把攒着的写进库；只读地看（不建表），还没有这张表 = 全是 0。"""
    flush()
    rows, first = [], None
    path = settings.db
    if path.exists():
        try:
            with contextlib.closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute("SELECT at, model, provider, kind, requests, texts, tokens, estimated FROM engine_embed_usage "
                                    "WHERE julianday(at) >= julianday(?) ORDER BY julianday(at), id", (start.isoformat(),)).fetchall()
                first = conn.execute("SELECT at FROM engine_embed_usage ORDER BY julianday(at) LIMIT 1").fetchone()
        except sqlite3.Error:  # 还没有这张表：没算过向量
            rows, first = [], None
    total, by_day, by_model, by_kind = _eblank(), {}, {}, {}
    for r in rows:
        try:  # 按那一份的日子取价；不像 models.cost 那样一份一份地舍到 6 位小数（搜一句才几个 token，舍完就成 0 了），合计了再舍
            p = models.price(r["model"], datetime.fromisoformat(r["at"]))
        except (TypeError, ValueError):
            p = None
        cost = int(r["tokens"] or 0) * p[0] / 1e6 if p else None
        _eadd(total, r, cost)
        _eadd(by_day.setdefault(_day(r["at"]), _eblank()), r, cost)
        _eadd(by_kind.setdefault(r["kind"], _eblank()), r, cost)
        m = by_model.setdefault(r["model"], {**_eblank(), "provider": r["provider"], "priced": set()})
        _eadd(m, r, cost)
        m["priced"].add(cost is not None)
    model_rows = []
    for name, acc in by_model.items():
        provider, priced = acc.pop("provider"), acc.pop("priced")
        model_rows.append({"model": name, "provider": provider, "billing": "metered" if priced == {True} else "unpriced" if priced == {False} else "mixed",
                           **_edone(acc)})
    model_rows.sort(key=lambda x: (-x["cost"], -x["tokens"], x["model"]))
    kinds = [*EMBED_KINDS, *sorted(set(by_kind) - set(EMBED_KINDS))]
    return {"total": _edone(total), "byDay": [{"day": d, **_edone(a)} for d, a in sorted(by_day.items())], "byModel": model_rows,
            "byKind": [{"kind": k, **_edone(by_kind[k])} for k in kinds if k in by_kind], "recordedSince": _day(first[0]) if first else None}
