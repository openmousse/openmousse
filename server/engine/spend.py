"""按钱的花费上限（第 4 批，G1）：一天 / 一轮最多花多少美元，到了停下、说一声。两家都没有（Claude Agent SDK 有 max_budget_usd：超了就停；
OpenClaw 只有 X 渠道的 costLimits.dailyUsd），照这两样的意思自己写的。

server.json（不写 = 没有上限，和以前一样）：
  "engine": {"spend": {"day": 5, "turn": 1, "warn": 0.8,
                       "agents": {"g-study": {"day": 1, "turn": 0.3}},
                       "purposes": {"background": {"day": 2}, "outsider": {"day": 1}}}}
  day      一天（server.json 时区的日历日，和用量页一样）按量计费的一共最多花多少美元
  turn     一轮（一句话进来到模型说完，中间可能调好几次模型）最多花多少
  warn     到了上限的几成推一条静默通知（默认 0.8；每个上限一天只推一次，真到了上限再推一次）
  agents   某个 Agent 自己的 day / turn；purposes：某种用途自己的 day / turn（用途照 usage.purpose_of：chat 你自己的对话 / background
           定时任务、心跳、后台任务、写回 / outsider 别人的话：名片 Agent、Doorman、触发那一次零工具的读 / api 对外接口 / cyber 赛博 /
           warming 提示缓存保温，第 7 批的 engine/warming.py）
  写 0 = 这一项一分钱都不许花（按量的不跑，套餐的照跑）。
算什么：只算按量计费、算得出钱的（usage.billing_of 的 metered：记下的 usage.cost）。用户自己的套餐（claude-code/…、chatgpt/…）记「套餐、
  不计钱」，不受上限；Claude Code 用 API 密钥登录的那几轮照算钱（它报的估算），也受上限。价目里没有的型号算不出钱：照记 token、在
  GET /api/engine/spend 里标出来（unpriced：几轮、哪几个型号），不拦——拦了，本机的 Ollama 这种不要钱的、刚出的型号就一律用不了，
  用户又看不出为什么；要管它们，把价写进 models.PRICES，或者别给它们配按量的密钥。向量（engine/embeddings.py）的钱算进当天的总数，
  建索引那部分算 background。媒体那几样（生图、生成视频 / 音乐、看图、语音……，engine/media.py 记的）算得出的钱算进当天的总数和那个
  Agent 的；生图、生成视频 / 音乐开跑之前 before_tool 看一眼（本机的不拦）。
拦在哪：engine/loop.py 每次调模型之前 check（到了抛 EngineError(kind="budget")：不重试、不换备用链；主人回合先往回复里写一句为什么），
  每一步用量算完 step（这一步的钱记进今天：一轮结束才写进库的那部分先记在进程里，几轮同时跑也算得准）。一次性 JSON、名片 Agent、
  Doorman 都经 run_turn，所以都管得到。claude_brain（用户自己的 Claude Code）用 API 密钥登录的那几轮开跑前 brain() 查一次（只看一天的总数）；
  向量补齐（engine/semantic.py）先看一眼当天的总数和 background。管不到的：名片 Agent 配了 card.llm 的直连、OpenClaw 的 llm-task，都不经内核。
到了以后：你在 app 里的那一轮停在那里，回复里一句话说清（花了多少、上限多少、在哪改）；后台的（定时任务、心跳、触发、后台任务、工作流）
  开跑前 blocked() 看一眼，到了就跳过、运行记录记 limit（不算失败）；跑到一半才到的，这一轮的错照样记成 limit（was_blocked）。
接口：GET /api/engine/spend（今天各项花了多少、离上限多远、哪些算不出钱）；PUT /api/engine/spend（改上限：只认主人的设备、写 server.json
  之前先备份，照 engine/setup_api.py）。
"""
from __future__ import annotations

import asyncio
import math
import threading
import time
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Request

from config import settings
from i18n import L

from . import models, store
from . import usage as usage_mod
from .errors import EngineError

PURPOSES = ("chat", "background", "outsider", "api", "cyber", "warming")  # warming：提示缓存保温（engine/warming.py，第 7 批）
WARN = 0.8
CACHE = 1.0                 # 今天的花费从库里算一次留多久（秒）：一轮里每步都要看
MAX_USD = 100_000.0
_live: dict[str, dict] = {}  # 还在跑的那几轮：一轮 id → {"cost", "agent", "purpose", "day"}（usage 一轮结束才写进库）
_blocked: dict[str, dict] = {}  # 被上限拦下的那几轮：一轮 id → 拦在哪（定时任务据此记 limit，不算失败）
_lock = threading.Lock()
_cache: dict[str, Any] = {"key": None, "at": 0.0, "db": None}
_bg: set[asyncio.Task] = set()
NOTES = """
CREATE TABLE IF NOT EXISTS engine_spend_notes (day TEXT NOT NULL, scope TEXT NOT NULL, kind TEXT NOT NULL, at TEXT NOT NULL,
  PRIMARY KEY(day, scope, kind));
CREATE INDEX IF NOT EXISTS engine_turns_at ON engine_turns(julianday(started_at));
"""
_ready: set[str] = set()


# —— 设置 ——————————————————————————————————————————————————————————————————————

def cfg() -> dict:
    s = models.cfg().get("spend")
    return s if isinstance(s, dict) else {}


def _money(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
        return None
    return float(min(v, MAX_USD))


def _pair(v: Any) -> dict:
    v = v if isinstance(v, dict) else {}
    return {k: m for k in ("day", "turn") if (m := _money(v.get(k))) is not None}


def limits() -> dict:
    """{"day", "turn", "warn", "agents": {Agent: {"day"?, "turn"?}}, "purposes": {用途: {…}}}；没写的项是 None / 没有。"""
    c = cfg()
    w = c.get("warn")
    warn = float(w) if isinstance(w, (int, float)) and not isinstance(w, bool) and 0 < w <= 1 else WARN
    agents = {str(a): p for a, v in (c.get("agents") or {}).items() if (p := _pair(v))} if isinstance(c.get("agents"), dict) else {}
    purposes = {str(k): p for k, v in (c.get("purposes") or {}).items() if k in PURPOSES and (p := _pair(v))} \
        if isinstance(c.get("purposes"), dict) else {}
    return {"day": _money(c.get("day")), "turn": _money(c.get("turn")), "warn": warn, "agents": agents, "purposes": purposes}


def enabled() -> bool:
    lim = limits()
    return lim["day"] is not None or lim["turn"] is not None or bool(lim["agents"]) or bool(lim["purposes"])


def metered(ref: str | None) -> bool:
    """这个模型的调用算不算钱：价目里有的按量模型算；用户自己的套餐不算，Claude Code 用 API 密钥登录的算；价目里没有的不算（见文件开头）。"""
    sub = models.subscription(ref)
    if sub == "claude-code":
        from . import runners
        info = runners.available().get("claude-code")
        return bool(info) and not runners.is_subscription(info)
    if sub:
        return False
    return bool(ref) and models.price(str(ref)) is not None


def ready() -> None:
    path = str(settings.db)
    if path in _ready or not store.exists():
        return
    with store.tx() as conn:
        conn.executescript(NOTES)
    _ready.add(path)


# —— 今天花了多少 ————————————————————————————————————————————————————————————————————

def _start(now: datetime | None = None) -> datetime:
    t = (now or datetime.now(settings.tz)).astimezone(settings.tz)
    return datetime.combine(t.date(), datetime.min.time(), settings.tz)


def today(now: datetime | None = None) -> dict:
    """今天到现在（server.json 时区的日历日）→ {"day", "total", "agents", "purposes", "embeddings", "media", "unpriced", "unpricedModels", "plan"}。
    库里结束了的那几轮 + 进程里记着、还在跑的那几轮 + 向量 + 媒体。"""
    start = _start(now)
    key = (str(settings.db), start.date().isoformat())
    mono = time.monotonic()
    if _cache["key"] == key and mono - _cache["at"] < CACHE and _cache["db"] is not None:
        db = _cache["db"]
    else:
        ready()
        db = usage_mod.spent_since(start)
        try:
            emb = usage_mod.embeddings_summary(start)
        except Exception:  # noqa: BLE001 — 向量的账读不了：只是少算这一份
            emb = {"total": {"cost": 0.0}, "byKind": []}
        db["embeddings"] = float(emb["total"].get("cost") or 0.0)
        db["indexing"] = float(next((k.get("cost") or 0.0 for k in emb.get("byKind") or [] if k.get("kind") == "index"), 0.0))
        try:
            from . import media
            db["media"], db["mediaAgents"] = media.spent_since(start)
        except Exception:  # noqa: BLE001 — 媒体的账读不了：只是少算这一份
            db["media"], db["mediaAgents"] = 0.0, {}
        _cache.update(key=key, at=mono, db=db)
    out = {"day": key[1], "total": db["total"] + db["embeddings"] + db["media"], "agents": dict(db["agents"]), "purposes": dict(db["purposes"]),
           "embeddings": db["embeddings"], "media": db["media"], "unpriced": db["unpriced"], "unpricedModels": list(db["unpricedModels"]),
           "plan": db["plan"]}
    for a, c in db["mediaAgents"].items():
        out["agents"][a] = out["agents"].get(a, 0.0) + c
    if db["indexing"]:
        out["purposes"]["background"] = out["purposes"].get("background", 0.0) + db["indexing"]
    running, snap = set(db["running"]), _cache["at"]
    with _lock:
        for tid, e in list(_live.items()):
            # 昨天的；或者库里这一轮已经不在跑了、而且库是在它最后一步之后读的（钱已经在库里了）：不再记在进程里
            if e["day"] != key[1] or (tid not in running and e["at"] < snap):
                _live.pop(tid, None)
        live = list(_live.values())
    for e in live:
        out["total"] += e["cost"]
        out["purposes"][e["purpose"]] = out["purposes"].get(e["purpose"], 0.0) + e["cost"]
        if e["agent"]:
            out["agents"][e["agent"]] = out["agents"].get(e["agent"], 0.0) + e["cost"]
    return out


def _invalidate() -> None:
    _cache["at"] = 0.0


# —— 拦 ——————————————————————————————————————————————————————————————————————————

def over(*, agent: str | None = None, purpose: str | None = None, turn_cost: float | None = None, now: datetime | None = None) -> dict | None:
    """到了哪一个上限 → {"scope": day | agent | purpose | turn, "name", "spent", "limit"}；都没到 → None。先看一轮的，再看一天的。"""
    lim = limits()
    if turn_cost is not None:
        caps = [("turn", "", lim["turn"]), ("turn", agent or "", (lim["agents"].get(agent or "") or {}).get("turn")),
                ("turn", purpose or "", (lim["purposes"].get(purpose or "") or {}).get("turn"))]
        for scope, name, cap in caps:
            if cap is not None and turn_cost >= cap:
                return {"scope": scope, "name": name, "spent": turn_cost, "limit": cap}
    day_caps = [("day", "", lim["day"])]
    if agent and (lim["agents"].get(agent) or {}).get("day") is not None:
        day_caps.append(("agent", agent, lim["agents"][agent]["day"]))
    if purpose and (lim["purposes"].get(purpose) or {}).get("day") is not None:
        day_caps.append(("purpose", purpose, lim["purposes"][purpose]["day"]))
    if all(c is None for _, _, c in day_caps):
        return None
    t = today(now)
    for scope, name, cap in day_caps:
        if cap is None:
            continue
        spent = t["total"] if scope == "day" else t["agents"].get(name, 0.0) if scope == "agent" else t["purposes"].get(name, 0.0)
        if spent >= cap:
            return {"scope": scope, "name": name, "spent": spent, "limit": cap}
    return None


def usd(x: float) -> str:
    return f"${x:.4f}" if 0 < x < 0.01 else f"${x:.2f}"


PURPOSE_NAMES = {"chat": ("你自己的对话", "your own chats"), "background": ("后台的事（定时任务、心跳、后台任务、写回）", "background work (jobs, heartbeats, tasks, write-back)"),
                 "outsider": ("别人的话（名片 Agent、Doorman、外面进来的触发）", "other people's messages (card agent, Doorman, outside triggers)"),
                 "api": ("对外接口", "the outside API"), "cyber": ("赛博", "cyber chats"), "warming": ("提示缓存保温", "prompt-cache warming")}


def _who(agent: str) -> str:
    try:
        import inbox
        return inbox.source_name(agent, inbox.names())
    except Exception:  # noqa: BLE001
        return agent


def where(hit: dict) -> str:
    """这个上限写在 server.json 的哪儿。"""
    scope, name = hit["scope"], hit["name"]
    if scope == "agent":
        return f"engine.spend.agents.{name}.day"
    if scope == "purpose":
        return f"engine.spend.purposes.{name}.day"
    if scope == "turn" and name:
        return f"engine.spend.{'purposes' if name in PURPOSES else 'agents'}.{name}.turn"
    return f"engine.spend.{'turn' if scope == 'turn' else 'day'}"


def message(hit: dict) -> str:
    """一句话说清：花了多少、上限多少、在哪改。"""
    s, cap, w = usd(hit["spent"]), usd(hit["limit"]), where(hit)
    if hit["scope"] == "turn":
        zh, en = f"这一轮已经花了 {s}，到了每一轮 {cap} 的上限", f"This turn has already cost {s}, which is the {cap} per-turn limit"
    elif hit["scope"] == "agent":
        who = _who(hit["name"])
        zh, en = f"「{who}」今天已经花了 {s}，到了它每天 {cap} 的上限", f"{who} has spent {s} today, which is its {cap} daily limit"
    elif hit["scope"] == "purpose":
        p = PURPOSE_NAMES.get(hit["name"], (hit["name"], hit["name"]))
        zh, en = f"{p[0]}今天已经花了 {s}，到了每天 {cap} 的上限", f"{p[1][0].upper() + p[1][1:]} cost {s} today, which is the {cap} daily limit"
    else:
        zh, en = f"今天按量计费的模型已经花了 {s}，到了每天 {cap} 的上限", f"Pay-as-you-go models have cost {s} today, which is the {cap} daily limit"
    return L(f"{zh}，先停在这里。要放宽就改 server.json 的 {w}（或者 PUT /api/engine/spend）；你自己的套餐和价目里没有的模型不受影响。",
             f"{en}, so it stops here. To raise it, change {w} in server.json (or PUT /api/engine/spend); your own plans and models "
             "without a price aren't affected.")


def check(tid: str, spec, ref: str, emit=None) -> None:
    """engine/loop.py 每次调模型之前：这一次算钱、到了上限 → 抛 EngineError(budget)（主人回合先往回复里写一句为什么）。"""
    if not enabled() or not metered(ref):
        return
    purpose = usage_mod.purpose_of(spec.kind, spec.origin)
    with _lock:
        cost = (_live.get(tid) or {}).get("cost", 0.0)
    hit = over(agent=spec.agent, purpose=purpose, turn_cost=cost)
    if hit is None:
        return
    msg = message(hit)
    _blocked[tid] = hit
    while len(_blocked) > 500:
        _blocked.pop(next(iter(_blocked)))
    if emit is not None and spec.kind == "owner":  # 回复里一句话说清（app 里看得到；已经说了一半的接在后面）
        emit({"type": "text", "text": L(f"\n\n（{msg}）", f"\n\n({msg})")})
    raise EngineError(msg, kind="budget")


def before_tool(ctx, provider: str) -> None:
    """要花钱的工具（生图、生成视频 / 音乐，第 3 批）开跑之前：到了上限 → 抛 ToolFailure（模型收到这句，跟用户说）。本机的（media.FREE）不拦。"""
    from . import media
    from .loop import ToolFailure
    if not enabled() or provider in media.FREE:
        return
    with _lock:
        cost = (_live.get(ctx.turn) or {}).get("cost", 0.0)
    hit = over(agent=ctx.agent, purpose=usage_mod.purpose_of(ctx.kind, ctx.origin), turn_cost=cost)
    if hit is not None:
        raise ToolFailure(message(hit))


def step(tid: str, spec, msg: dict) -> None:
    """engine/loop.py 每一步用量算完：这一步的钱记进今天（还在跑的那一轮），到了提醒线推一条静默通知。"""
    if not enabled() or not msg.get("provider"):
        return
    c = models.cost(f"{msg.get('provider')}/{msg.get('served') or msg.get('model')}", msg.get("usage") or {})
    if c is None:
        return
    with _lock:
        e = _live.setdefault(tid, {"cost": 0.0, "agent": spec.agent, "purpose": usage_mod.purpose_of(spec.kind, spec.origin),
                                   "day": _start().date().isoformat()})
        e["cost"] = e["cost"] + c
        e["at"] = time.monotonic()
    _invalidate()
    _warn_soon()


def was_blocked(turn: str | None) -> dict | None:
    """这一轮是被上限拦下的吗（定时任务、心跳据此把出错记成 limit，不算失败）。"""
    return _blocked.get(turn or "")


def blocked(*, purpose: str = "background", agent: str | None = None, model: str | None = None) -> dict | None:
    """后台的事开跑之前看一眼：今天这一项到了上限（model 给了而且不算钱 → 不拦）→ 拦在哪；没到 → None。"""
    if not enabled() or (model is not None and not metered(model)):
        return None
    return over(agent=agent, purpose=purpose)


def brain(info: dict | None) -> None:
    """claude_brain 开跑前（用户自己的 Claude Code）：API 密钥登录的那种照算钱，今天的总数到了上限就不开跑。套餐登录的不管。"""
    if not enabled() or not info:
        return
    from . import runners
    if runners.is_subscription(info):
        return
    hit = over()
    if hit is not None:
        raise EngineError(message(hit), kind="budget")


def embed_ok() -> bool:
    """向量补齐（engine/semantic.py）：今天的总数、background 都没到上限。"""
    return blocked(purpose="background") is None


# —— 提醒线：一天一次的静默通知 ——————————————————————————————————————————————————————————————

WARN_EVERY = 5.0  # 一轮里每一步都会来：最多这么多秒看一次提醒线
_warned_at = [0.0]


def _warn_soon() -> None:
    """算完一步：过一会儿在后台看一眼提醒线（不拖这一轮；正在看的、刚看过的不再起）。"""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    if _bg or time.monotonic() - _warned_at[0] < WARN_EVERY:
        return
    _warned_at[0] = time.monotonic()
    t = loop.create_task(warn())
    _bg.add(t)
    t.add_done_callback(_bg.discard)


def _noted(day: str, scope: str, kind: str) -> bool:
    """记一笔「今天这一项推过了」；已经推过 → False。"""
    ready()
    with store.tx() as conn:
        return conn.execute("INSERT OR IGNORE INTO engine_spend_notes(day, scope, kind, at) VALUES(?,?,?,?)",
                            (day, scope, kind, store.now_iso())).rowcount == 1


async def warn(now: datetime | None = None) -> list[str]:
    """一天的上限（总数、某个 Agent、某种用途）到了提醒线 / 到了上限：各推一条静默通知（一天一次）。→ 这一次推了哪几项。"""
    lim = limits()
    t = await asyncio.to_thread(today, now)
    caps = [("day", "", lim["day"], t["total"])]
    caps += [("agent", a, p["day"], t["agents"].get(a, 0.0)) for a, p in lim["agents"].items() if p.get("day") is not None]
    caps += [("purpose", k, p["day"], t["purposes"].get(k, 0.0)) for k, p in lim["purposes"].items() if p.get("day") is not None]
    sent = []
    for scope, name, cap, spent in caps:
        if cap is None or cap <= 0:
            continue
        kind = "cap" if spent >= cap else "warn" if spent >= cap * lim["warn"] else None
        if kind is None or not await asyncio.to_thread(_noted, t["day"], f"{scope}:{name}", kind):
            continue
        hit = {"scope": scope, "name": name, "spent": spent, "limit": cap}
        if kind == "cap":
            body = message(hit)
        else:
            body = L(f"今天已经花了 {usd(spent)}（{_scope_name(scope, name)[0]}），每天的上限是 {usd(cap)}：到了 {round(spent / cap * 100)}%。",
                     f"{usd(spent)} spent today ({_scope_name(scope, name)[1]}); the daily limit is {usd(cap)}: {round(spent / cap * 100)}% used.")
        sent.append(f"{scope}:{name}:{kind}")
        try:
            import push
            await push.send_push(L("花费提醒", "Spending"), body, {"thread": "main"}, level="quiet", collapse=f"spend:{scope}:{name}", kind="report")
        except Exception as e:  # noqa: BLE001 — 推不出去：用量页照样看得到
            print(f"[engine] 花费提醒没推出去：{type(e).__name__}: {str(e)[:120]}")
        import chat
        chat.log_activity(body, "failed" if kind == "cap" else "push")
    return sent


def _scope_name(scope: str, name: str) -> tuple[str, str]:
    if scope == "agent":
        who = _who(name)
        return who, who
    if scope == "purpose":
        return PURPOSE_NAMES.get(name, (name, name))
    return "一共", "in total"


# —— 接口 ——————————————————————————————————————————————————————————————————————————

def view(now: datetime | None = None) -> dict:
    """GET /api/engine/spend 的样子：上限、今天各项花了多少、离上限多远、拦着的、算不出钱的。"""
    lim = limits()
    t = today(now)

    def left(cap: float | None, spent: float) -> float | None:
        return None if cap is None else round(max(0.0, cap - spent), 6)
    agents = {a: {"spent": round(t["agents"].get(a, 0.0), 6), **{f"{k}Limit": v for k, v in p.items()},
                  "left": left(p.get("day"), t["agents"].get(a, 0.0))} for a, p in lim["agents"].items()}
    purposes = {k: {"spent": round(t["purposes"].get(k, 0.0), 6), **{f"{x}Limit": v for x, v in (lim["purposes"].get(k) or {}).items()},
                    "left": left((lim["purposes"].get(k) or {}).get("day"), t["purposes"].get(k, 0.0))}
                for k in PURPOSES if k in lim["purposes"] or t["purposes"].get(k)}
    hits = [h for h in (over(agent=a, now=now) for a in lim["agents"]) if h and h["scope"] != "day"]
    hits += [h for h in (over(purpose=k, now=now) for k in lim["purposes"]) if h and h["scope"] != "day"]
    if (h := over(now=now)) is not None:
        hits.insert(0, h)
    return {"configured": enabled(), "day": t["day"], "currency": "USD", "timezone": settings.timezone,
            "limits": {"day": lim["day"], "turn": lim["turn"], "warn": lim["warn"], "agents": lim["agents"], "purposes": lim["purposes"]},
            "spent": round(t["total"], 6), "left": left(lim["day"], t["total"]), "embeddings": round(t["embeddings"], 6),
            "agents": agents, "purposes": purposes, "blocked": [{**h, "spent": round(h["spent"], 6), "message": message(h)} for h in hits],
            "plan": {"turns": t["plan"]}, "unpriced": {"turns": t["unpriced"], "models": t["unpricedModels"]},
            "note": L("只算按量计费、价目里有的；你自己的套餐不算钱、不受上限；价目里没有的型号算不出钱，只记 token、不拦",
                      "Only pay-as-you-go models with a known price count; your own plans cost nothing here and aren't capped; models "
                      "without a price only log tokens and aren't stopped")}


def _owner(request: Request) -> str:
    from . import setup_api
    return setup_api.owner(request)


router = APIRouter(dependencies=[Depends(_owner)])


@router.get("/api/engine/spend")
def api_get():
    return {"ok": True, **view()}


def _clean_pair(field: str, v: Any) -> dict | None:
    from .setup_api import SetupError
    if v is None:
        return None
    if not isinstance(v, dict) or set(v) - {"day", "turn"}:
        raise SetupError(400, L(f"{field} 写成 {{\"day\": 美元, \"turn\": 美元}}", f'{field} is {{"day": dollars, "turn": dollars}}'), field=field)
    out = {}
    for k, x in v.items():
        if x is None:
            continue
        if _money(x) is None:
            raise SetupError(400, L(f"{field}.{k} 要是不小于 0 的美元数", f"{field}.{k} must be a number of dollars, 0 or more"), field=f"{field}.{k}")
        out[k] = float(x)
    return out or None


@router.put("/api/engine/spend")
async def api_put(request: Request):
    """{"day"?, "turn"?, "warn"?, "agents"?: {Agent: {"day"?, "turn"?} | null}, "purposes"?: {用途: … | null}}：给了的换上，null = 去掉。
    整个 {"spend": null} 或空的上限 = 没有上限。先备份 server.json（engine/setup_api.edit）。"""
    import chat

    from . import setup_api
    from .setup_api import SetupError
    b = await setup_api.body_of(request)
    unknown = set(b) - {"day", "turn", "warn", "agents", "purposes", "spend"}
    if unknown:
        raise SetupError(400, L(f"不认识的键：{'、'.join(sorted(unknown))}", f"Unknown keys: {', '.join(sorted(unknown))}"))
    new: dict[str, Any] = {}
    for k in ("day", "turn"):
        if k in b:
            if b[k] is not None and _money(b[k]) is None:
                raise SetupError(400, L(f"{k} 要是不小于 0 的美元数，或者 null（不设）", f"{k} must be dollars (0 or more), or null for none"), field=k)
            new[k] = b[k]
    if "warn" in b:
        w = b["warn"]
        if w is not None and (isinstance(w, bool) or not isinstance(w, (int, float)) or not 0 < w <= 1):
            raise SetupError(400, L("warn 写 0 到 1 之间的小数（0.8 = 到了八成推一条）", "warn is a fraction between 0 and 1 (0.8 = at 80%)"), field="warn")
        new["warn"] = w
    for group in ("agents", "purposes"):
        if group not in b:
            continue
        v = b[group]
        if v is not None and not isinstance(v, dict):
            raise SetupError(400, L(f"{group} 要是一个对象", f"{group} must be an object"), field=group)
        if group == "purposes" and isinstance(v, dict) and (bad := set(v) - set(PURPOSES)):
            raise SetupError(400, L(f"用途只能是 {'、'.join(PURPOSES)}（不认识：{'、'.join(sorted(bad))}）",
                                    f"Purposes are {', '.join(PURPOSES)} (unknown: {', '.join(sorted(bad))})"), field=group)
        new[group] = None if v is None else {str(k): _clean_pair(f"{group}.{k}", x) for k, x in v.items()}

    def mutate(d: dict) -> None:
        sp = setup_api.section(d, "engine", "spend")
        if "spend" in b and b["spend"] is None:
            d["engine"].pop("spend", None)
            return
        for k, v in new.items():
            if k in ("agents", "purposes"):
                if v is None:
                    sp.pop(k, None)
                    continue
                if not isinstance(sp.get(k), dict):
                    sp[k] = {}
                grp = sp[k]
                for name, pair in v.items():
                    if pair is None:
                        grp.pop(name, None)
                    else:
                        grp[name] = pair
                if not grp:
                    sp.pop(k, None)
            elif v is None:
                sp.pop(k, None)
            else:
                sp[k] = v
        setup_api.prune(d, "engine", "spend")
    res = setup_api.edit(mutate)
    if res["changed"]:
        chat.log_activity(L("改了花费上限", "Changed the spending limits"), "edit")
        _invalidate()
    return {"ok": True, **view(), "backup": res["backup"], "changed": res["changed"]}
