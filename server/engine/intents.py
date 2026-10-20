"""要记得的事（第 5 批第 7 项，prospective memory）：「下次我提到护照，提醒我续签」。照 OpenClaw memory-core 的 standing intents
（extensions/memory-core/src/standing-intents*.ts：触发词、最多几次、冷却、过期，每句话不调模型地对一遍，对上了在这一轮开头带一行）自己写的，
没有拿代码。默认关：server.json 的 engine.intents.enabled = true 才有（新功能对已在用的安装默认关）。

remember_when 工具（只给主人回合，tools.for_agent 挂）：
  create  triggers（几个词或短语）+ reminder（到时候要提起的事）[+ max_fires、cooldown_hours、expires]：存一条（撤得回 = undo 档：
          cancel 就撤掉，不交卡）
  list    这个 Agent 还开着的（all = 连用完、撤掉、过期的一起）
  cancel  撤掉一条（只有用户要撤才撤）
存哪：mousse.db 的 engine_intents（一条 = Agent、触发词、提醒、过期时间、最多几次、冷却、已经几次、上次什么时候、状态）。
  状态：armed（等着）/ done（次数用完）/ cancelled（撤了）/ expired（过期了）。
对哪句话（match，不调模型，确定的）：主人自己说的每一句（engine/manuals.py 的 prep_for，按句搜手册的那一步；收件箱知会、定时脚本这些
  系统自己发的不算，manuals.skip；Claude Code 跑的主人回合同样走 prep_for）。这句话小写、拆成词（字母、数字、_、-）；一个触发词也这样拆，
  每个词都在这句话里才算对上（OpenClaw 的 triggerMatchesPrompt）；中日韩、泰文这些词和词之间不空格的，按字串找（「日本」对得上
  「下周去日本出差」）。冷却没过、次数用完、过期的不算。对上了：次数 + 1、记下时刻（用完就 done），这一轮开头那条 system（手册命中的那条；
  没有就单独一条）后面多一行「用户之前让你记着的：…」，最多 SHOW 条、合起来不超过 SHOW_CHARS 字（OpenClaw 的 3 条、1200 字）；
  记号进事件流（engine/used.py，kind intent，via prep）。别人的话（赛博、外人回合）从来不对，也建不了。
记性的闸（engine/memgate.py）里算「经历」（experience）：
  - 它是主人在某个时刻说的话的记录（「那时候让我记着这个」），像 HEARTBEAT.md（心跳时要看的那张单子，也算经历）：有时效、会自己用完、
    过期，不是关于世界的事实（叶子），也不是长期的做法（做法要先提议、点头才改，对一句「到时候提醒我」太重）。
  - 所以主人回合写、撤都是直接做（GATE：经历 add / forget = write），工具档位是 undo（撤得回）；赛博、外人回合按闸 deny，
    何况这个工具只标 owner。每次建、撤都记进闸的事件流（memgate.record，target = intent:<id>）。
接口：GET /api/engine/intents?agent=main[&all=1]、DELETE /api/engine/intents/{id}（撤掉一条）。
配置：engine.intents = {"enabled": false, "max_fires": 3, "cooldown_hours": 24, "expiry_days": 90}（create 不写时的默认）。
"""
from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from datetime import datetime
from datetime import time as dtime

from fastapi import APIRouter, HTTPException

from config import settings
from i18n import L

from . import memgate, models, store
from .loop import Tool, ToolCtx, ToolFailure

router = APIRouter()

TOOL = "remember_when"
KIND = "experience"                  # 记性的闸里算哪种（见文件开头）
ACTIONS = ("create", "list", "cancel")
STATUSES = ("armed", "done", "cancelled", "expired")
DEFAULTS = {"max_fires": 3, "cooldown_hours": 24.0, "expiry_days": 90}
SHOW, SHOW_CHARS = 3, 1200           # 一句话最多带几条、合起来最多多少字（OpenClaw 的 INTENT_INJECTION_MAX_COUNT / _MAX_CHARS）
TRIGGERS_MAX, TRIGGER_CHARS, REMINDER_CHARS = 24, 120, 500
ACTIVE_MAX = 50                      # 一个 Agent 最多同时开着几条
TOKEN = re.compile(r"[\w-]+")
SPACELESS = re.compile(r"[฀-๿぀-ヿ㐀-䶿一-鿿가-힯豈-﫿]")  # 泰文、假名、汉字、谚文
SCHEMA = """
CREATE TABLE IF NOT EXISTS engine_intents (
  id TEXT PRIMARY KEY, agent TEXT NOT NULL, triggers TEXT NOT NULL, reminder TEXT NOT NULL, status TEXT NOT NULL,
  created REAL NOT NULL, expires REAL NOT NULL, max_fires INTEGER NOT NULL, cooldown REAL NOT NULL, fires INTEGER NOT NULL DEFAULT 0,
  last_fired REAL, conversation TEXT, turn TEXT);
CREATE INDEX IF NOT EXISTS engine_intents_agent ON engine_intents(agent, status);
"""
_ready: set[str] = set()


class IntentError(Exception):
    """给模型、给人看的一句话。"""


def cfg() -> dict | None:
    """engine.intents：enabled 是 true 才开（→ 合过范围的默认值）；不写、写别的 → None（关着：没有工具，也不对话）。"""
    v = models.cfg().get("intents")
    if v is True:
        v = {"enabled": True}
    if not isinstance(v, dict) or v.get("enabled") is not True:
        return None

    def num(key: str, lo: float, hi: float):
        x = v.get(key)
        return DEFAULTS[key] if isinstance(x, bool) or not isinstance(x, (int, float)) else type(DEFAULTS[key])(max(lo, min(hi, x)))
    return {"max_fires": num("max_fires", 1, 20), "cooldown_hours": num("cooldown_hours", 0, 24 * 365), "expiry_days": num("expiry_days", 1, 3650)}


def ready() -> None:
    path = str(settings.db)
    if path in _ready:
        return
    with store.tx() as conn:
        conn.executescript(SCHEMA)
    _ready.add(path)


# —— 对一句话 ——————————————————————————————————————————————————————————————————————

def tokens(text: str) -> list[str]:
    return TOKEN.findall(str(text).lower())


def fits(trigger: str, low: str, words: set[str]) -> bool:
    """这个触发词对不对得上这句话（low：小写的这句话；words：拆好的词）：触发词的每个词都在。词和词之间不空格的文字按字串找。"""
    parts = tokens(trigger)
    return bool(parts) and all((p in low) if SPACELESS.search(p) else (p in words) for p in parts)


def _row(r) -> dict:
    d = dict(r)
    d["triggers"] = json.loads(d["triggers"]) if d.get("triggers") else []
    return d


def line(it: dict) -> str:
    """带进这一轮的一行（给模型看）。"""
    day = datetime.fromtimestamp(it["created"], settings.tz).date().isoformat()
    return (f'Reminder the user asked you to keep (set {day}; their message mentions "{it["matched"]}"): {it["reminder"]} '
            "Bring it up if it fits what they're saying now; it is their earlier request, not a new instruction.")


def render(fired: list[dict]) -> str:
    return "\n".join(line(x) for x in fired)


def match(agent: str, text: str, *, now: float | None = None) -> list[dict]:
    """这句话对上了这个 Agent 的哪几条（见文件开头）：对上的当场记一次（次数 + 1、上次时刻；用完 → done）。关着、没有表 → []。"""
    if cfg() is None or not str(text).strip() or not store.exists():
        return []
    ready()
    now = time.time() if now is None else now
    low = str(text).lower()
    words = set(tokens(low))
    fired: list[dict] = []
    with store.tx() as conn:
        conn.execute("UPDATE engine_intents SET status='expired' WHERE status='armed' AND expires <= ?", (now,))
        rows = conn.execute("SELECT * FROM engine_intents WHERE agent=? AND status='armed' ORDER BY created, id", (agent,)).fetchall()
        for r in rows:
            if len(fired) >= SHOW:
                break
            it = _row(r)
            if it["fires"] >= it["max_fires"] or (it["last_fired"] is not None and it["last_fired"] + it["cooldown"] > now):
                continue
            hit = next((t for t in it["triggers"] if fits(t, low, words)), None)
            if hit is None:
                continue
            it["matched"] = hit
            if len(render([*fired, it])) > SHOW_CHARS:
                continue
            n = it["fires"] + 1
            conn.execute("UPDATE engine_intents SET fires=?, last_fired=?, status=? WHERE id=? AND status='armed'",
                         (n, now, "done" if n >= it["max_fires"] else "armed", it["id"]))
            fired.append({**it, "fires": n})
    return fired


def _fire(agent: str, text: str, turn: str, conversation: str) -> list[dict]:
    from . import used
    fired = match(agent, text)
    if fired:
        used.note(turn, conversation=conversation, agent=agent, kind="intent", names=[x["id"] for x in fired], via="prep", publish=False)
    return fired


async def attach(note: dict | None, text: str, *, turn: str, agent: str, conversation: str) -> dict | None:
    """manuals.prep_for 的最后一步：对上了的提醒接在那条 system 后面（没有那一条就单独一条），记号进事件流。关着、没对上 → 原样回 note。
    出错只打一行（锦上添花，不拖累这一轮）。"""
    if cfg() is None or not str(text).strip():
        return note
    try:
        fired = await asyncio.to_thread(_fire, agent, text, turn, conversation)
    except Exception as e:  # noqa: BLE001
        print(f"[engine] 要记得的事没对上：{type(e).__name__}: {str(e)[:120]}")
        return note
    if not fired:
        return note
    from . import events, used
    events.publish(conversation or "-", used.event(turn, "intent", [x["id"] for x in fired], "prep"))
    extra = render(fired)
    if note is None:
        return {"role": "system", "content": [{"type": "text", "text": extra}], "intents": [x["id"] for x in fired]}
    from .session import text_of
    return {**note, "content": [{"type": "text", "text": text_of(note) + "\n\n" + extra}], "intents": [x["id"] for x in fired]}


# —— 建、列、撤 ——————————————————————————————————————————————————————————————————————

def _triggers(v) -> list[str]:
    if isinstance(v, str):
        v = [v]
    if not isinstance(v, list) or not v:
        raise IntentError(L("triggers 要写几个词或短语（列表）", "triggers must be a list of words or short phrases"))
    out: list[str] = []
    for x in v:
        t = " ".join(str(x).split()).lower()
        if not t or len(t) > TRIGGER_CHARS:
            raise IntentError(L(f"每个触发词最多 {TRIGGER_CHARS} 字", f"Each trigger must be 1 to {TRIGGER_CHARS} characters"))
        if len("".join(tokens(t))) < 2:
            raise IntentError(L(f"触发词「{t[:20]}」太短了，几乎每句话都会对上", f"The trigger {t[:20]!r} is too short; it would match almost anything"))
        if t not in out:
            out.append(t)
    if len(out) > TRIGGERS_MAX:
        raise IntentError(L(f"触发词最多 {TRIGGERS_MAX} 个", f"At most {TRIGGERS_MAX} triggers"))
    return out


def _expires(v, days: int, now: float) -> float:
    if v in (None, ""):
        return now + days * 86400
    try:
        s = str(v).strip()
        t = datetime.combine(datetime.fromisoformat(s).date(), dtime(23, 59, 59)) if len(s) == 10 else datetime.fromisoformat(s)
        t = t if t.tzinfo else t.replace(tzinfo=settings.tz)
    except ValueError:
        raise IntentError(L("expires 写成日期或时间（2026-12-01 / 2026-12-01T18:00）", "expires must be an ISO date or time (2026-12-01 / "
                            "2026-12-01T18:00)")) from None
    ts = t.timestamp()
    if ts <= now or ts > now + 3650 * 86400:
        raise IntentError(L("expires 要在以后、十年以内", "expires must be in the future and within ten years"))
    return ts


def create(agent: str, triggers, reminder, *, max_fires=None, cooldown_hours=None, expires=None, conversation: str = "", turn: str = "",
           now: float | None = None) -> dict:
    c = cfg() or {**DEFAULTS}
    now = time.time() if now is None else now
    words = _triggers(triggers)
    text = " ".join(str(reminder or "").split())
    if not text or len(text) > REMINDER_CHARS:
        raise IntentError(L(f"reminder 写到时候要提起的事（1 到 {REMINDER_CHARS} 字）", f"reminder must say what to bring up (1 to {REMINDER_CHARS} characters)"))
    mf = c["max_fires"] if max_fires is None else max_fires
    cd = c["cooldown_hours"] if cooldown_hours is None else cooldown_hours
    if isinstance(mf, bool) or not isinstance(mf, int) or not 1 <= mf <= 20:
        raise IntentError(L("max_fires 是 1 到 20 的整数", "max_fires must be a whole number from 1 to 20"))
    if isinstance(cd, bool) or not isinstance(cd, (int, float)) or not 0 <= cd <= 24 * 365:
        raise IntentError(L("cooldown_hours 是 0 到 8760 之间的数", "cooldown_hours must be a number from 0 to 8760"))
    until = _expires(expires, int(c["expiry_days"]), now)
    ready()
    iid = f"int-{uuid.uuid4().hex[:8]}"
    with store.tx() as conn:
        live = conn.execute("SELECT COUNT(*) FROM engine_intents WHERE agent=? AND status='armed' AND expires > ?", (agent, now)).fetchone()[0]
        if live >= ACTIVE_MAX:
            raise IntentError(L(f"这个 Agent 已经记着 {ACTIVE_MAX} 条了：先撤掉几条用不上的", f"This Agent already keeps {ACTIVE_MAX}; cancel some first"))
        conn.execute("INSERT INTO engine_intents(id, agent, triggers, reminder, status, created, expires, max_fires, cooldown, fires, conversation, turn) "
                     "VALUES(?,?,?,?,'armed',?,?,?,?,0,?,?)", (iid, agent, json.dumps(words, ensure_ascii=False), text, now, until, mf, float(cd) * 3600,
                                                              conversation or None, turn or None))
    return get(iid) or {}


def get(iid: str) -> dict | None:
    if not store.exists():
        return None
    ready()
    with store.read() as conn:
        r = conn.execute("SELECT * FROM engine_intents WHERE id=?", (iid,)).fetchone()
    return _row(r) if r else None


def listing(agent: str | None, *, everything: bool = False, now: float | None = None) -> list[dict]:
    """这个 Agent 的（agent 不给 = 全部）：开着的；everything 连用完、撤掉、过期的。先把过期的标上。"""
    if not store.exists():
        return []
    ready()
    now = time.time() if now is None else now
    with store.tx() as conn:
        conn.execute("UPDATE engine_intents SET status='expired' WHERE status='armed' AND expires <= ?", (now,))
        q = "SELECT * FROM engine_intents" + (" WHERE agent=?" if agent else "")
        if not everything:
            q += (" AND" if agent else " WHERE") + " status='armed'"
        rows = conn.execute(q + " ORDER BY created, id", (agent,) if agent else ()).fetchall()
    return [_row(r) for r in rows]


def cancel(iid: str, *, agent: str | None = None) -> dict | None:
    """撤掉一条（还开着的）。agent 给了就只撤这个 Agent 的。→ 撤后的样子；没有、已经不开着了 → None。"""
    if not store.exists():
        return None
    ready()
    with store.tx() as conn:
        q = "UPDATE engine_intents SET status='cancelled' WHERE id=? AND status='armed'" + (" AND agent=?" if agent else "")
        n = conn.execute(q, (iid, agent) if agent else (iid,)).rowcount
    return get(iid) if n else None


def _iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, settings.tz).isoformat(timespec="seconds") if ts else None


def view(it: dict) -> dict:
    return {"id": it["id"], "agent": it["agent"], "triggers": it["triggers"], "reminder": it["reminder"], "status": it["status"],
            "fires": it["fires"], "maxFires": it["max_fires"], "cooldownHours": round(it["cooldown"] / 3600, 2), "createdAt": _iso(it["created"]),
            "expiresAt": _iso(it["expires"]), "lastFiredAt": _iso(it["last_fired"]), "conversation": it.get("conversation")}


def brief(it: dict) -> str:
    """list 的一行（给模型看）。"""
    when = " | ".join(f'"{t}"' for t in it["triggers"])
    day = datetime.fromtimestamp(it["expires"], settings.tz).date().isoformat()
    return f"- {it['id']} [{it['status']}] when {when} → {it['reminder']} (fired {it['fires']}/{it['max_fires']}, until {day})"


def describe(iid: str) -> dict | None:
    """记号详情用（engine/used.py 经 manuals.describe）：触发词当「什么时候用」，提醒当说明。"""
    it = get(iid)
    return None if it is None else {"when": it["triggers"], "about": it["reminder"], "source": "intent", "own": True, "status": it["status"]}


# —— 工具 ——————————————————————————————————————————————————————————————————————

def _gate(op: str, ctx: ToolCtx) -> memgate.Decision:
    """过记性的闸（经历：主人回合直接写；别的回合 deny）。"""
    d = memgate.decide(KIND, op, turn=ctx.kind)
    if d.action != "write":
        raise ToolFailure(L("这一回合不能记这个", "This turn can't keep reminders") + f"（{d.why}）")
    return d


def _record(d: memgate.Decision, ctx: ToolCtx, iid: str, result: str) -> None:
    """记进闸的事件流（不记内容，只记是哪一条）。"""
    memgate.record(d, target=f"intent:{iid}", author=ctx.agent or "main", conversation=ctx.conversation, turn_id=ctx.turn,
                   agent=ctx.agent or "main", result=result)


def tool(me: str) -> Tool | None:
    """remember_when（见文件开头）。engine.intents 没开 → None。"""
    if cfg() is None:
        return None

    async def run(args: dict, ctx: ToolCtx) -> str:
        action = str(args.get("action") or "").strip().lower()
        who = ctx.agent or me
        if action not in ACTIONS:
            raise ToolFailure(L(f"action 只能是 {' / '.join(ACTIONS)}", f"action must be one of {', '.join(ACTIONS)}"))
        try:
            if action == "list":
                rows = await asyncio.to_thread(listing, who, everything=bool(args.get("all")))
                return "\n".join(brief(x) for x in rows) or L("（没有记着的）", "(nothing kept yet)")
            if action == "cancel":
                iid = str(args.get("id") or "").strip()
                if not iid:
                    raise ToolFailure(L("要写 id（先 list 看看）", "id is required (list first)"))
                d = _gate("forget", ctx)
                got = await asyncio.to_thread(cancel, iid, agent=who)
                if got is None:
                    raise ToolFailure(L(f"没有开着的 {iid}", f"No active reminder {iid}"))
                _record(d, ctx, iid, "cancelled")
                return L(f"撤了：{iid}", f"Cancelled {iid}.")
            d = _gate("add", ctx)
            it = await asyncio.to_thread(create, who, args.get("triggers"), args.get("reminder"), max_fires=args.get("max_fires"),
                                         cooldown_hours=args.get("cooldown_hours"), expires=args.get("expires"), conversation=ctx.conversation,
                                         turn=ctx.turn)
            _record(d, ctx, it["id"], "armed")
        except IntentError as e:
            raise ToolFailure(str(e)) from None
        return (L(f"记下了：{it['id']}。", f"Kept as {it['id']}.") + "\n" + brief(it) + "\n" +
                L("用户之后说的话里对上了触发词，提醒会自动带进那一轮；别提前说，用户没让撤就别撤。",
                  "When a later message from the user contains a trigger, the reminder is brought into that turn automatically. Don't "
                  "deliver it early, and don't cancel it unless the user asks."))

    def classify(args: dict) -> tuple[str, str]:
        from . import tools as tools_mod
        if str(args.get("action") or "").strip().lower() == "list":
            return tools_mod.READ, "只读：列出记着的"
        return tools_mod.UNDO, "记性的闸里算经历（主人自己的话直接写）；cancel 就撤掉"

    return Tool(name=TOOL, kinds=("owner",), run=run, classify=classify, timeout=30,
                title=lambda args: f"{TOOL} {args.get('action') or ''}".strip(),
                description=L(
                    "要记得的事：用户说「下次我提到 X 时提醒我 Y」这类话时用。create：triggers 写几个词或短语（用户之后哪句话里这些词都出现了就算对上；"
                    "多写几种说法，中英文都说的话两种都写），reminder 写到时候要提起的事（写给自己看的）。之后用户说的每一句都会不调模型地对一遍，"
                    f"对上了提醒自动带进那一轮，最多 max_fires 次（默认 {DEFAULTS['max_fires']}），两次之间至少隔 cooldown_hours 小时（默认 "
                    f"{DEFAULTS['cooldown_hours']:.0f}），到 expires 为止（默认 {DEFAULTS['expiry_days']} 天）。list 列出来；cancel 撤掉一条（建错了也这样撤）。"
                    "别提前说，用户没让撤就别撤。按时间提醒的用 cron。",
                    "Prospective memory: use it when the user says something like \"next time I mention X, remind me of Y\". create: triggers = "
                    "a few words or short phrases (a later message from the user matches when it contains all the words of one of them; give "
                    "several variants, in both languages if they mix them) and reminder = what to bring up, as a note to yourself. Each later "
                    "message from the user is checked without a model; on a match the reminder is brought into that turn automatically, at most "
                    f"max_fires times (default {DEFAULTS['max_fires']}), at least cooldown_hours apart (default {DEFAULTS['cooldown_hours']:.0f}), "
                    f"until expires (default {DEFAULTS['expiry_days']} days). list shows them; cancel stops one (also how to undo a create). "
                    "Don't deliver a reminder early, and don't cancel one unless the user asks. For reminders at a time, use cron."),
                schema={"type": "object", "required": ["action"], "properties": {
                    "action": {"type": "string", "enum": list(ACTIONS)},
                    "triggers": {"type": "array", "items": {"type": "string"}, "description": L(
                        f"create：几个触发词或短语（最多 {TRIGGERS_MAX} 个）", f"create: trigger words or short phrases (at most {TRIGGERS_MAX})")},
                    "reminder": {"type": "string", "description": L(f"create：到时候要提起的事（最多 {REMINDER_CHARS} 字）",
                                                                    f"create: what to bring up (at most {REMINDER_CHARS} characters)")},
                    "max_fires": {"type": "integer", "minimum": 1, "maximum": 20},
                    "cooldown_hours": {"type": "number", "minimum": 0},
                    "expires": {"type": "string", "description": L("create：到哪天为止（2026-12-01 或 2026-12-01T18:00）",
                                                                   "create: until when (2026-12-01 or 2026-12-01T18:00)")},
                    "id": {"type": "string", "description": L("cancel：哪一条", "cancel: which one")},
                    "all": {"type": "boolean", "description": L("list：连用完、撤掉、过期的一起列", "list: include finished, cancelled and expired ones")}}})


# —— 接口 ——————————————————————————————————————————————————————————————————————

@router.get("/api/engine/intents")
def api_list(agent: str | None = None, all: int = 0):
    """记着的事（engine.intents 关着也能看、能撤以前记下的）。"""
    return {"ok": True, "enabled": cfg() is not None, "intents": [view(x) for x in listing(agent, everything=bool(all))]}


@router.delete("/api/engine/intents/{iid}")
def api_cancel(iid: str):
    got = cancel(iid)
    if got is None:
        raise HTTPException(404, L("没有这一条，或者它已经不开着了", "No such reminder, or it's no longer active"))
    memgate.record(memgate.decide(KIND, "forget"), target=f"intent:{iid}", author="owner", agent=got["agent"], result="cancelled")
    return {"ok": True, "intent": view(got)}
