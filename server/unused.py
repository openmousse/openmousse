"""每周修剪卡多一节「这个月没用上的」（内核第 13c 项，2026-10-19；「Busabase 对照」画布第 9 张）：一直没被用上的手册，多半是名字没写成
平时说的话，先改说明，别急着删。

哪些算（candidates）：世界树的手册（openmousse_tree.manuals），这台服务器上工作区里的技能（主对话的、有自己工作区的 Agent 的：用户、
  Agent 自己写的；packs/core 自带的不算：新手带路、每周修剪这些本来就少用，改了说明还会在工作区多出一份盖住以后的更新），
  engine.manuals.unused_days 天（默认 30）里一次都没用上：内核的账本（engine/used.py：备料带进来的、工具读了正文的）和世界树自己记的读
  （各平台 read_manual、命令行 show，manual_read 表）都没有。出现还不到这么多天的不算（刚写的还没机会用：没用过不等于没用，照 Hermes curator
  的规矩）；停用了的不列；这一节上定过的（不动、交去改说明），unused_days 天里不再列（一个月看一次）。最多 MAX 条：手册在前，技能在后。
卡上（treeprune.py 出的那张每周修剪卡，inbox 的 items）：一份一条，id 是 u1、u2……（和修剪计划的步 1、2、3 分开），data 带
  {"unused": 类别, "name", "agent", "do": "redescribe" | "disable", "days", "note"}。一条三种选法：
  不动     默认：不点、点拒绝都是不动（记下来，unused_days 天里不再列）
  改说明   点同意：交给 ⑬ 出一张改说明的提议卡（engine/improve.py：名字、什么时候用改成平时说的话，用户点了才改）
  停用     在这一条上「修改」写「停用」：这一条换成停用、默认就做（只停用不删，留版本，engine/improve.disable）
  「修改」写「改说明」换回改说明（默认做）；写「不动」= 默认不动；写别的 = 交给 ⑬ 改说明时的一句提示（note），默认做。不调模型。
修剪计划这周没有可剪的（模型没交、交的是空的）：每周修剪的定时任务跑完以后（engine/jobs.AFTER）看一眼，这周还没有卡就出一张只有这一节的卡。
接口：给画界面的人看的：卡的 items 里 data.unused 有值的就是这一节；三种选法见上（以后 app 可以直接画三个按钮：不动 = 拒绝，
  改说明 = 同意，停用 = 先 POST /api/inbox/{卡}/items/{条} {"action": "revise", "note": "停用"} 再同意）。
"""
from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime

from chat import _lock, db, now_iso
from config import settings
from i18n import L, LS

MAX = 10
KINDS = ("manual", "skill")
DISABLE_WORDS = ("停用", "关掉", "不要了", "disable", "turn off", "switch off")
KEEP_WORDS = ("不动", "留着", "保留", "keep", "leave it")
DESCRIBE_WORDS = ("改说明", "改名", "redescribe", "reword", "rename", "describe")
_bg: set[asyncio.Task] = set()  # 后台出改说明卡的（测试等它们做完）


def udb() -> sqlite3.Connection:
    conn = db()
    conn.execute("""CREATE TABLE IF NOT EXISTS tree_unused (kind TEXT NOT NULL, name TEXT NOT NULL, decision TEXT NOT NULL, at TEXT NOT NULL,
        PRIMARY KEY(kind, name))""")
    return conn


def _decided(days: int, now: datetime) -> set[tuple[str, str]]:
    """这一节上最近 days 天里定过的（不动、交去改说明）：这一阵不再列。"""
    from engine import used
    with _lock, udb() as conn:
        rows = conn.execute("SELECT kind, name, at FROM tree_unused WHERE decision IN ('keep','redescribe')").fetchall()
    return {(r["kind"], r["name"]) for r in rows if (used.idle_days(r["at"], now) or 0) < days}


def mark(kind: str, name: str, decision: str) -> None:
    with _lock, udb() as conn:
        conn.execute("INSERT OR REPLACE INTO tree_unused(kind, name, decision, at) VALUES(?,?,?,?)", (kind, name, decision, now_iso()))


def _later(a: str | None, b: str | None) -> str | None:
    if not a or not b:
        return a or b
    from engine import used
    return a if (used.idle_days(a) or 0) <= (used.idle_days(b) or 0) else b


def _manual_reads() -> dict[str, str]:
    import contextlib

    from engine import manuals
    mt = manuals.tree()
    if mt is None:
        return {}
    try:
        with contextlib.closing(mt.connect(sync=False)) as conn:
            return mt.manuals().last_reads(conn)
    except Exception:  # noqa: BLE001
        return {}


def candidates(now: datetime | None = None) -> list[dict]:
    """这个月没用上的（见文件开头）→ [{"kind", "name", "agent", "source", "last", "idle"（天，没用过 = None）, "when", "about"}]。"""
    import bridges
    from engine import manuals, used
    days = manuals.setting("unused_days")
    now = now or datetime.now(settings.tz)
    last = used.last_used()
    reads = _manual_reads()
    done = _decided(days, now)
    out: list[dict] = []

    def unused(at: str | None, since: str | None) -> bool:
        if at:
            return (used.idle_days(at, now) or 0) >= days
        age = used.idle_days(since, now)
        return age is None or age >= days  # 没用过：出现满 days 天才算（不知道什么时候出现的也算）
    for m in manuals.tree_manuals():
        k = ("manual", m["name"])
        at = _later(last.get(k), reads.get(m["name"].casefold()))
        if k in done or not unused(at, m.get("created_at")):
            continue
        out.append({"kind": "manual", "name": m["name"], "agent": "main", "source": "tree", "last": at, "idle": used.idle_days(at, now),
                    "when": m["when"], "about": m["about"]})
    every = set(bridges.BRIDGES) | {"tasks"}
    seen: set[str] = set()
    skills: list[dict] = []
    for agent in ["main", *settings.agent_workspaces]:
        for e in manuals.catalog(agent, every, kinds=("skill",)):
            if e["name"] in seen or e["source"] == "core":  # core 的是 OpenMousse 自带的（新手带路、每周修剪这些本来就少用），不归这里提
                continue
            seen.add(e["name"])
            k = ("skill", e["name"])
            at = last.get(k)
            if k in done or not unused(at, e.get("since")):
                continue
            skills.append({"kind": "skill", "name": e["name"], "agent": agent if e["source"] == "workspace" else "main", "source": e["source"],
                           "last": at, "idle": used.idle_days(at, now), "when": e["when"], "about": e["about"]})
    skills.sort(key=lambda c: (c["source"] == "core", c["name"]))
    return (out + skills)[:MAX]


def _text(d: dict) -> str:
    word = L("手册", "manual") if d["unused"] == "manual" else L("技能", "skill")
    when = L(f"{d['days']} 天没用上", f"unused for {d['days']} days") if d.get("days") is not None else L("一直没用上", "never used")
    if d["do"] == "disable":
        return L(f"「{d['name']}」（{word}）{when} → 停用（只停用不删，留一版）", f'"{d["name"]}" ({word}) {when} → disable it (not deleted; a copy is kept)')
    return L(f"「{d['name']}」（{word}）{when} → 改说明（出一张改说明的卡，你点了才改）",
             f'"{d["name"]}" ({word}) {when} → reword it (a card with the new wording; nothing changes until you approve)')


def _detail(d: dict, c: dict | None = None) -> str:
    sep = L("、", "; ")
    out = []
    if c is not None:
        if c.get("when"):
            out.append(L(f"现在的「什么时候用」：{sep.join(c['when'])}", f"When to use, now: {sep.join(c['when'])}"))
        if c.get("about"):
            out.append(L(f"现在的说明：{c['about'][:200]}", f"Description, now: {c['about'][:200]}"))
    out.append(L("不点 = 不动；同意 = 照这一条做。想停用：在这一条上「修改」写「停用」；换回改说明写「改说明」；写别的就当改说明的提示。",
                 'Leave it = no change; approve = do what this item says. To disable it instead, revise this item with "disable"; '
                 '"reword" switches back; anything else becomes a hint for the rewording.'))
    if d.get("note"):
        out.append(L(f"提示：{d['note']}", f"Hint: {d['note']}"))
    return "\n".join(out)


def item_of(n: int, c: dict) -> dict:
    d = {"unused": c["kind"], "name": c["name"], "agent": c["agent"], "do": "redescribe", "days": c["idle"], "note": "",
         "when": c["when"][:8], "about": c["about"][:300]}
    return {"id": f"u{n}", "text": _text(d), "detail": _detail(d, c), "data": d, "default": "reject"}


def items() -> list[dict]:
    """这一周卡上这一节的几条（没有 → []）。世界树、内核读不了的话也是 []（这一节只是顺带的）。"""
    try:
        return [item_of(i, c) for i, c in enumerate(candidates(), 1)]
    except Exception as e:  # noqa: BLE001
        print(f"[tree] 「这个月没用上的」没列出来：{type(e).__name__}: {str(e)[:160]}")
        return []


def is_item(x: dict) -> bool:
    d = x.get("data")
    return isinstance(d, dict) and d.get("unused") in KINDS


def split(items_: list[dict] | None) -> tuple[list[dict] | None, list[dict]]:
    """卡上的条目 → (修剪计划的那几步, 这一节的那几条)。"""
    if items_ is None:
        return None, []
    return [x for x in items_ if not is_item(x)], [x for x in items_ if is_item(x)]


def intro() -> str:
    return L("「这个月没用上的」：一直没被用上的手册、技能，多半是名字没写成平时说的话：先改说明，别急着删。默认不动；同意 = 改说明（出一张改说明的卡，"
             "你点了才改）；想停用就在那一条上「修改」写「停用」（只停用不删，留一版）。",
             '"Not used this month": manuals and skills nobody has used, usually because their names aren\'t how you\'d say it: reword them '
             'before deleting anything. Leaving an item = no change; approving = reword (a card with the new wording, nothing changes until you '
             'approve it); to disable one instead, revise that item with "disable" (disabled, not deleted; a copy is kept).')


def section(card: dict | None, extra: list[dict], room: int) -> dict | None:
    """把这一节接到每周修剪卡上（room = 卡上还能放几条）；card 是 None（这周没有可剪的叶子）就出一张只有这一节的卡。没有这一节 → card 原样。"""
    extra = extra[:max(0, room)]
    if not extra:
        return card
    head = L(f"这个月没用上的 {len(extra)} 份", f"{len(extra)} not used this month")
    if card is None:
        from treeprune import ITEMS_KIND
        return {"title": L(f"世界树每周修剪：{head}", f"Weekly memory tree pruning: {head}"), "why": intro(), "detail": intro(), "items": extra,
                "itemsKind": ITEMS_KIND, "approveLabel": L("照这样处理", "Go ahead")}
    return {**card, "title": card["title"] + L(f" · {head}", f" · {head}"), "detail": card["detail"] + "\n\n" + intro(),
            "items": [*card["items"], *extra]}


async def revise(card: dict, x: dict, note: str) -> dict:
    """卡上「修改」这一节的一条（treeprune.revise_step 转过来）：停用 / 改说明 / 不动，或者一句改说明的提示。不调模型。"""
    d = dict(x["data"])
    low = note.casefold()
    default = "approve"
    if any(w in low for w in DISABLE_WORDS):
        d["do"] = "disable"
    elif any(w in low for w in KEEP_WORDS):
        default = "reject"
    elif any(w in low for w in DESCRIBE_WORDS):
        d["do"] = "redescribe"
    else:
        d["do"], d["note"] = "redescribe", " ".join(note.split())[:200]
    return {"id": x["id"], "text": _text(d), "detail": _detail(d, d), "data": d, "default": default}


async def _describe_all(xs: list[dict]) -> None:
    """同意了「改说明」的那几条：一条一条交给 ⑬ 出改说明的卡（每条一次模型调用，在后台做，不拖着修剪卡的提交）。"""
    from engine import improve
    for x in xs:
        d = x["data"]
        kind, name = d["unused"], d["name"]
        try:
            r = await improve.propose_describe(kind, name, agent=d.get("agent") or "main", why=improve.why_unused(kind, name, d.get("days")),
                                               note=d.get("note") or "")
        except Exception as e:  # noqa: BLE001 — 一条没做成不拖累别的
            r = {"ok": False, "why": f"{type(e).__name__}: {e}"}
        if r["ok"]:
            mark(kind, name, "redescribe")
        else:
            print(f"[tree] 「{name}」的改说明卡没出成：{str(r['why'])[:160]}")


async def apply(items_: list[dict]) -> str:
    """修剪卡提交了（treeprune.on_decided）：同意的照它的 do 做，没同意的记成「不动」。→ 一句结果（这一节没有条目 → ""）。
    停用当场做；改说明要调模型想新说法，交到后台一条一条出卡（_bg），这里先回「交去改说明了」。"""
    if not items_:
        return ""
    from engine import improve
    asked, off, kept, failed = [], [], [], []
    for x in items_:
        d = x["data"]
        kind, name = d["unused"], d["name"]
        if x.get("decision") != "approve":
            mark(kind, name, "keep")
            kept.append(name)
        elif d.get("do") == "disable":
            try:
                await improve.disable(kind, name, agent=d.get("agent") or "main")
            except Exception as e:  # noqa: BLE001 — 一条没做成不拖累别的
                failed.append(f"{name}: {str(e)[:120]}")
                continue
            mark(kind, name, "disable")
            off.append(name)
        else:
            asked.append(x)
    if asked:
        t = asyncio.create_task(_describe_all(asked))
        _bg.add(t)
        t.add_done_callback(_bg.discard)
    parts = []
    if asked:
        parts.append(L(f"{len(asked)} 份交去改说明（改说明的卡稍后出现在收件箱，你点了才改）",
                       f"{len(asked)} sent for rewording (the cards show up in the inbox shortly; nothing changes until you approve)"))
    if off:
        parts.append(L(f"{len(off)} 份停用了", f"{len(off)} disabled"))
    if kept:
        parts.append(L(f"{len(kept)} 份不动", f"{len(kept)} left as they are"))
    if failed:
        parts.append(L(f"{len(failed)} 份没做成：{failed[0]}", f"{len(failed)} failed: {failed[0]}"))
    return L("这个月没用上的：", "Not used this month: ") + L("，", ", ").join(parts)


def decline(items_: list[dict]) -> None:
    """整张卡拒了：这一节的每一条都算「不动」。"""
    for x in items_:
        d = x.get("data") or {}
        if d.get("unused") in KINDS:
            mark(d["unused"], d["name"], "keep")


def card_text(n: int) -> str:
    """出卡时回给模型的半句（给每周修剪的那一轮看的）。"""
    return LS(f"；卡上还列了这个月没用上的 {n} 份手册和技能", f"; the card also lists {n} manual(s) and skill(s) not used this month") if n else ""
