"""用了哪条，留个记号（内核第 13c 项，2026-10-19；「Busabase 对照」画布第 9 张）：每一轮在事件流里记下真实读到的技能、手册、叶子。
记号来自真实发生的读取（备料带进来的、工具读的），不靠 Agent 自己报。

一条记号（engine_used，mousse.db；一轮里同一样只记一条，几种来路并在 via 里）：
  turn / conversation / agent  哪一轮、哪个对话、哪个 Agent
  kind                         manual（世界树的手册）/ skill（技能）/ guide（功能包自带的用法）/ leaf（世界树的叶子）
  name                         名字（叶子是它的 id）
  via                          怎么读到的：prep = 这一轮开头按这句话搜到、带进来的（engine/manuals.py）；tool = 工具读的（skill、manual
                               工具读了正文；tree 工具的 recall / recent、memory_search、memory_get 回来的叶子；wrap() 接的）。几种用逗号连着
  source                       在哪：技能 core / workspace，手册 tree，功能包的用法是包名
  at / day                     什么时候（逻辑日 04:00 换）
不算的：会话开头那一大块背景（当天的世界树、规矩文件）：那是一天的底子，不是为这句话读的，算进来每天第一句都是「用了 80 片叶子」；
  skill 工具只列名字、搜（query）回的名单也不算（只看了名字和说明，没读正文）。
账本（engine_used_days）：按天、按样、按 Agent 累计用了几轮（一轮只算一次）、最后一次用的时间。last_used() 给「多久没用」，
  技能列表（GET /api/engine/skills）stale_days 天（engine.manuals.stale_days，默认 14）没用标旧，只标不删；「这个月没用上的」见 ../unused.py。
  ⑬ 的「用完提议改技能」只对这一轮真用过的技能提（engine/skill_write.py 的提醒按 skills_of(上一轮) 点名）。
事件：记进表的同时往这个对话广播 {"type": "used", "turn", "kind", "names", "via"}（app 以后画实时的）。
done 事件和回复那一行（chat.py 的 messages.used）带上 summary(这一轮)：{"turn", "manuals", "skills", "guides", "leaves"}；
  line()（L()）拼回复卡片底下那一行：「按『写文书』手册 · 用了 2 片叶子 ›」。
接口：GET /api/engine/used/{一轮 id}、GET /api/engine/used?message=db123：这一轮用到的那几样（手册、技能、用法的名字和说明，叶子的那句话、在哪一枝）；
  GET /api/engine/skills?agent=main：这个 Agent 的技能、手册、用法，带最后一次用的时间、多久没用、标没标旧。
"""
from __future__ import annotations

import dataclasses
import json
import re
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException

from config import settings
from i18n import L

from . import events, store

router = APIRouter()
KINDS = ("manual", "skill", "guide", "leaf", "intent")  # intent：第 5 批，要记得的事这一轮对上了（engine/intents.py，名字是它的 id）
DAY_START_HOUR = 4
NAME_MAX = 200
SCHEMA = """
CREATE TABLE IF NOT EXISTS engine_used (
  turn TEXT NOT NULL, kind TEXT NOT NULL, name TEXT NOT NULL, conversation TEXT NOT NULL, agent TEXT, via TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT '', at TEXT NOT NULL, day TEXT NOT NULL, PRIMARY KEY(turn, kind, name));
CREATE INDEX IF NOT EXISTS engine_used_name ON engine_used(kind, name);
CREATE TABLE IF NOT EXISTS engine_used_days (
  day TEXT NOT NULL, kind TEXT NOT NULL, name TEXT NOT NULL, agent TEXT NOT NULL DEFAULT '', n INTEGER NOT NULL DEFAULT 0, last_at TEXT NOT NULL,
  PRIMARY KEY(day, kind, name, agent));
"""
LEAF_LINE = re.compile(r"^\s*-\s*\[([A-Za-z0-9_-]{4,64})\]", re.M)   # tree recall / recent、memory_search 附的世界树：「- [id] 那句话（…）」
TREE_REF = re.compile(r"\btree:([A-Za-z0-9_-]{4,64})\b")            # 叶子在记忆索引里时的出处（engine/recall_tree.py）
PROFILE_ID = re.compile(r"p[0-9a-f]{11}")                            # 档案要点（不是叶子）：store.profile_id 的样子
_ready: set[str] = set()


def ready() -> None:
    path = str(settings.db)
    if path in _ready:
        return
    with store.tx() as conn:
        conn.executescript(SCHEMA)
    _ready.add(path)


def day_of(at: datetime | None = None) -> str:
    """逻辑日（04:00 换，和 chat.day_of 一样；第 2 批起照 engine.reset.at，engine/reset.py）。"""
    from .reset import day_start
    t = at or datetime.now(settings.tz)
    return (t - day_start()).date().isoformat()


def event(turn: str, kind: str, names: list[str], via: str) -> dict:
    """往对话广播的那一条（见文件开头）。"""
    return {"type": "used", "turn": turn, "kind": kind, "names": names, "via": via}


def note(turn: str | None, *, conversation: str, agent: str | None, kind: str, names, via: str, source: str = "",
         publish: bool = True) -> list[str]:
    """记下这一轮读到的几样（见文件开头）→ 记下的那几个名字（去掉重复的；没记 → []）。不在一轮里（turn 空的）不记；出错不抛（记号是
    顺手记的，不能拖累这一轮）。publish = False：不广播（在线程里记的，调用方回到事件循环再发 event()）。"""
    names = [n for n in dict.fromkeys(" ".join(str(x).split())[:NAME_MAX] for x in names or []) if n]
    if not turn or kind not in KINDS or not names:
        return []
    try:
        ready()
        now = datetime.now(settings.tz)
        at, day = now.isoformat(timespec="milliseconds"), day_of(now)
        with store.tx() as conn:
            for name in names:
                row = conn.execute("SELECT via FROM engine_used WHERE turn=? AND kind=? AND name=?", (turn, kind, name)).fetchone()
                if row is not None:
                    vias = row["via"].split(",")
                    if via not in vias:
                        conn.execute("UPDATE engine_used SET via=? WHERE turn=? AND kind=? AND name=?", (",".join([*vias, via]), turn, kind, name))
                    continue
                conn.execute("INSERT INTO engine_used(turn, kind, name, conversation, agent, via, source, at, day) VALUES(?,?,?,?,?,?,?,?,?)",
                             (turn, kind, name, conversation or "-", agent, via, source or "", at, day))
                conn.execute("INSERT INTO engine_used_days(day, kind, name, agent, n, last_at) VALUES(?,?,?,?,1,?) "
                             "ON CONFLICT(day, kind, name, agent) DO UPDATE SET n = n + 1, last_at = excluded.last_at",
                             (day, kind, name, agent or "", at))
    except Exception as e:  # noqa: BLE001
        print(f"[engine] 记号没记上（{kind}）：{type(e).__name__}: {str(e)[:120]}")
        return []
    if publish:
        events.publish(conversation or "-", event(turn, kind, names, via))
    return names


def of_turn(turn: str) -> list[dict]:
    """这一轮的记号（按记下的先后）。"""
    if not turn or not store.exists():
        return []
    ready()
    with store.read() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM engine_used WHERE turn=? ORDER BY rowid", (turn,))]


def summary(turn: str | None) -> dict | None:
    """回复卡片底下那一行要的：{"turn", "manuals", "skills", "guides", "leaves"（几片）}；这一轮什么都没用 → None。"""
    rows = of_turn(turn or "")
    if not rows:
        return None
    pick = lambda k: [r["name"] for r in rows if r["kind"] == k]
    return {"turn": turn, "manuals": pick("manual"), "skills": pick("skill"), "guides": pick("guide"), "leaves": len(pick("leaf"))}


def skills_of(turn: str | None) -> list[str]:
    """这一轮真用过（读过正文）的技能和手册的名字（⑬ 的「用完提议改技能」只提这几个）。"""
    return [r["name"] for r in of_turn(turn or "") if r["kind"] in ("skill", "manual")]


def _names(names: list[str], zh: bool) -> str:
    shown = names[:2]
    if zh:
        s = "".join(f"『{n}』" for n in shown)
        return s + (f"等 {len(names)} 份" if len(names) > 2 else "")
    s = " and ".join(f'"{n}"' for n in shown)
    return s + (f" and {len(names) - 2} more" if len(names) > 2 else "")


def line(u: dict | None) -> str:
    """回复卡片底下那一行（按请求的语言）：「按『写文书』手册 · 用了 2 片叶子 ›」/ 'Following the "写文书" manual · used 2 leaves ›'。
    什么都没用 → ""（不画）。app 照这个拼法画，点开看详情（GET /api/engine/used/{turn}）。"""
    if not u:
        return ""
    zh = L("zh", "en") == "zh"
    kinds = [(k, u.get(k) or []) for k in ("manuals", "skills", "guides")]
    words = {"manuals": ("手册", "manual", "manuals"), "skills": ("技能", "skill", "skills"), "guides": ("用法", "guide", "guides")}
    named = [(_names(ns, zh), words[k], len(ns)) for k, ns in kinds if ns]
    parts = []
    if named:
        if zh:
            parts.append("按" + "、".join(f"{n}{w[0]}" for n, w, _ in named))
        else:
            parts.append("Following the " + ", the ".join(f"{n} {w[1] if c == 1 else w[2]}" for n, w, c in named))
    n = int(u.get("leaves") or 0)
    if n:
        parts.append(L(f"用了 {n} 片叶子", f"used {n} leaf" if n == 1 else f"used {n} leaves"))
    if not parts:
        return ""
    if not zh and parts and not named:
        parts[0] = parts[0][0].upper() + parts[0][1:]
    return " · ".join(parts) + " ›"


def with_line(u: dict | None) -> dict | None:
    return {**u, "line": line(u)} if u else None


# —— 账本 ——————————————————————————————————————————————————————————————————————

def last_used(kind: str | None = None) -> dict[tuple[str, str], str]:
    """每一样最后一次用的时间：(kind, 名字) → ISO 时间。没用过内核的库 → {}（不建表）。"""
    if not store.exists():
        return {}
    ready()
    q = "SELECT kind, name, MAX(last_at) AS at FROM engine_used_days" + (" WHERE kind=?" if kind else "") + " GROUP BY kind, name"
    with store.read() as conn:
        return {(r["kind"], r["name"]): r["at"] for r in conn.execute(q, (kind,) if kind else ())}


def uses(kind: str, name: str, days: int) -> int:
    """最近 days 天里用了几轮（所有 Agent 加起来）。"""
    if not store.exists():
        return 0
    ready()
    since = (datetime.now(settings.tz) - timedelta(days=days)).date().isoformat()
    with store.read() as conn:
        r = conn.execute("SELECT SUM(n) FROM engine_used_days WHERE kind=? AND name=? AND day>=?", (kind, name, since)).fetchone()
    return int(r[0] or 0)


def idle_days(at: str | None, now: datetime | None = None) -> int | None:
    """多久没用（天）；没用过 → None。"""
    if not at:
        return None
    try:
        t = datetime.fromisoformat(at)
    except ValueError:
        return None
    now = now or datetime.now(settings.tz)
    return max(0, (now - (t if t.tzinfo else t.replace(tzinfo=settings.tz))).days)


def age_days(since: str | None, now: datetime | None = None) -> int | None:
    return idle_days(since, now)


def stale(last: str | None, since: str | None, days: int, now: datetime | None = None) -> bool:
    """标旧：days 天没用。没用过的从它出现的时候（since：建的时间）算起，刚建不到 days 天的不算旧（照 Hermes curator 的规矩：没用过不等于旧）。"""
    anchor = last or since
    n = idle_days(anchor, now)
    return n is not None and n >= days


# —— 工具读到的叶子、手册（wrap）——————————————————————————————————————————————————————

def leaves_in(text: str) -> list[str]:
    """工具回来的字里的叶子 id：「- [id] …」那几行、tree:<id> 的出处；档案要点不算。"""
    ids = [*LEAF_LINE.findall(text or ""), *TREE_REF.findall(text or "")]
    return [i for i in dict.fromkeys(ids) if not PROFILE_ID.fullmatch(i)]


def _reads(name: str, args: dict, out: str) -> list[tuple[str, list[str], str]]:
    """这一次工具调用读到了什么 → [(kind, 名字们, source)]。"""
    if name == "tree":
        argv = [str(x) for x in args.get("args") or []]
        if argv[:1] in (["recall"], ["recent"]):
            return [("leaf", leaves_in(out), "tree")]
        if argv[:2] == ["manual", "show"] and len(argv) > 2:
            return [("manual", [argv[2]], "tree")]
        return []
    if name == "memory_search":
        return [("leaf", leaves_in(out), "tree")]
    if name == "memory_get":
        ref = str(args.get("ref") or "")
        return [("leaf", [ref[5:]], "tree")] if ref.startswith("tree:") else []
    return []


WATCHED = ("tree", "memory_search", "memory_get")


def wrap(tools: list) -> list:
    """给会读叶子、手册的几个工具（tree、memory_search、memory_get）套一层：跑成了就照回来的字记下读到了哪几片（engine/tools.for_agent 用）。
    skill、manual 工具读了正文自己记（engine/tools.py、engine/manuals.py）。别的工具原样。"""
    out = []
    for t in tools:
        if t.name not in WATCHED or getattr(t, "_traced", False):
            out.append(t)
            continue
        out.append(_traced(t))
    return out


def _traced(t):
    inner = t.run
    name = t.name

    async def run(args: dict, ctx):
        res = await inner(args, ctx)
        try:
            text = res if isinstance(res, str) else getattr(res, "text", "") or ""
            for kind, names, source in _reads(name, args, text):
                note(ctx.turn, conversation=ctx.conversation, agent=ctx.agent, kind=kind, names=names, via="tool", source=source)
        except Exception as e:  # noqa: BLE001 — 记号是顺手记的
            print(f"[engine] {name} 读到的没记上：{type(e).__name__}: {str(e)[:120]}")
        return res
    new = dataclasses.replace(t, run=run)
    object.__setattr__(new, "_traced", True)
    return new


# —— 接口 ——————————————————————————————————————————————————————————————————————

def _leaf_rows(ids: list[str]) -> dict[str, dict]:
    """叶子现在的样子（那句话、在哪一枝、状态）；世界树读不了 → {}。"""
    import contextlib

    import sources
    mt = sources.memory_tree
    if mt is None or not ids:
        return {}
    try:
        with contextlib.closing(mt.connect(sync=False)) as conn:
            col = "branch" if getattr(mt, "HAS_BRANCHES", True) else "NULL AS branch"
            q = f"SELECT id, text, status, {col} FROM tree WHERE id IN ({','.join('?' * len(ids))})"  # noqa: S608 — 列名是常量
            return {r["id"]: {"text": r["text"], "status": r["status"], "branch": r["branch"] or str(mt.TRUNK)} for r in conn.execute(q, ids)}
    except Exception:  # noqa: BLE001
        return {}


def detail(turn: str) -> dict:
    """GET /api/engine/used/{turn} 回的：这一轮的摘要 + 用到的每一样（现在的名字和说明 / 叶子的那句话）。"""
    rows = of_turn(turn)
    t = None
    if store.exists():
        with store.read() as conn:
            t = conn.execute("SELECT conversation, agent, started_at FROM engine_turns WHERE id=?", (turn,)).fetchone()
    if t is None and not rows:
        raise HTTPException(404, L("没有这一轮", "No such turn"))
    from . import manuals
    leaves = _leaf_rows([r["name"] for r in rows if r["kind"] == "leaf"])
    items = []
    for r in rows:
        it = {"kind": r["kind"], "name": r["name"], "via": r["via"].split(","), "source": r["source"], "at": r["at"]}
        if r["kind"] == "leaf":
            it.update(leaves.get(r["name"]) or {"text": "", "status": "gone"})
        else:
            info = manuals.describe(r["kind"], r["name"], r["agent"] or "main")
            it.update(info or {"when": [], "about": "", "gone": True})
        items.append(it)
    u = summary(turn)
    return {"ok": True, "turn": turn, "conversation": t["conversation"] if t else rows[0]["conversation"],
            "agent": (t["agent"] if t else rows[0]["agent"]) or "main", "startedAt": t["started_at"] if t else None,
            "used": with_line(u), "items": items}


@router.get("/api/engine/used/{turn}")
def api_turn(turn: str):
    return detail(turn)


@router.get("/api/engine/used")
def api_message(message: str):
    """按回复的 id（db123，app 手上的那个）找它那一轮。"""
    import chat
    mid = message[2:] if message.startswith("db") else message
    if not mid.isdigit():
        raise HTTPException(400, L("message 写回复的 id（db123）", "message must be a reply id (db123)"))
    with chat._lock, chat.db() as conn:
        r = conn.execute("SELECT used FROM messages WHERE id=?", (int(mid),)).fetchone()
    try:
        u = json.loads(r["used"]) if r and r["used"] else None
    except ValueError:
        u = None
    if not u or not u.get("turn"):
        raise HTTPException(404, L("这条回复没有记号", "This reply has no record of what it used"))
    return detail(u["turn"])


@router.get("/api/engine/skills")
def api_skills(agent: str = "main"):
    """这个 Agent 的技能、手册、功能包的用法：名字、类别、什么时候用、说明、最后一次用的时间、多久没用、标旧（stale_days 天没用；只标不删）。"""
    from . import manuals, tools as tools_mod
    offered = {t.name for t in tools_mod.for_agent(agent)}
    days = manuals.setting("stale_days")
    last = last_used()
    now = datetime.now(settings.tz)
    out = []
    for e in manuals.catalog(agent, offered, kinds=("manual", "skill", "guide"), disabled=True):
        at = last.get((e["kind"], e["name"]))
        out.append({"kind": e["kind"], "name": e["name"], "when": e["when"], "about": e["about"], "source": e["source"], "own": e["own"],
                    "disabled": bool(e.get("disabled")), "since": e.get("since"), "lastUsed": at, "idleDays": idle_days(at, now),
                    "stale": not e.get("disabled") and stale(at, e.get("since"), days, now)})
    return {"ok": True, "agent": agent, "staleDays": days, "items": out}
