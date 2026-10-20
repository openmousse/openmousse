"""旧会话、分叉、切枝（第 2 批，10/19）：一个对话的会话不止今天这一条（每个逻辑日一条，/new、闲置换新、/resume、分叉又各开一条，
键都是 mousse:<对话>:<逻辑日>，老拆法照样认），一条会话本身又是一棵树（撤回 = 换叶子，旧枝还在，engine/session.py）。

列（sessions_of）：这个对话的会话，新的在前：哪天、什么时候开的 / 最后动过、几轮、第一句、最后一句、标题（第 14 项，engine/titles.py：
  先是第一句截短的；engine.titles 开了，列的时候在后台起一个名，titleFrom = derived / llm）、是不是现在这条、怎么开的（new / idle /
  resume / fork，从哪一条来）。
挑一条接着聊（resume，/resume N）：开一条新会话（今天的键，成了现在这条），开头带挑的那一条聊到哪（owner.recap，同 /new 的写法）；
  现在这条照 /new 算这一段结束（写回开了就写回）。为什么不直接接到那一条后面：会话按逻辑日分（那一条的键是那天的，老拆法、写回、检索都
  按键认天）；那一条的开头是那天备的料（日程、档案都旧了）；模型可能换过（思考块的签名只能回放给同一家）；整条搬过来可能一上来就要压缩。
  带要点、留原样：要原样接着说的，用分叉（fork，不给 entry = 整条照抄）。
分叉（fork）：从某一条会话的某一句话分出一条新会话：根到那一句之前原样抄过来（新的条目 id；压缩的 first_kept、裁剪的 ids 跟着换；
  Claude Code 的会话 id 不抄：它那边的会话里还有分叉以后的话，新会话下一轮开新的 Claude Code 会话），键是今天的（成了现在这条）。
  分在用户说的一句上 = 那一句和后面的都不要（同撤回），回的 text 是那一句的原话（app 放回输入框重新说）；不给 = 整条照抄到现在的叶子。
枝（branches / switch）：这条会话树上的每一枝（叶子）：最后一句、几条消息、从哪里岔出去的、是不是现在这一枝；切过去 = 换叶子。
  Claude Code 跑的对话：切到别的枝，它那边的会话对不上了，下一轮开新的（claude_brain.plan 认出「撤回过」）。
app 的界面等设计；接口（只在 builtin，改东西的这一轮在跑 → 409）：
  GET  /api/engine/chats/{thread}/sessions           → {"ok", "sessions": [{"id", "day", "created", "updated", "turns", "first", "last",
                                                         "title", "titleFrom", "current", "how"?: {"reason", "from"}}]}
  POST /api/engine/chats/{thread}/resume  {"session"} → {"ok", "session": 新的 id, "from"}
  POST /api/engine/chats/{thread}/fork    {"session"?: 不给 = 现在这条, "entry"? | "message"?: "db123"（app 里用户那句的 id）}
                                                      → {"ok", "session": 新的 id, "from", "text"?: 分在用户那句上时那句原话}
  GET  /api/engine/chats/{thread}/branches?session=   → {"ok", "session", "branches": [{"leaf", "ts", "last", "messages", "from"?, "current"}]}
  POST /api/engine/chats/{thread}/branch  {"leaf", "session"?} → {"ok"}
  GET  /api/engine/chats/{thread}/export?format=md|json|html&tools=0|1&session=
                                                      → 文件（下载；engine/export.py，同 /export）
"""
from __future__ import annotations

import json
import re
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from i18n import L

from . import store, titles
from .session import DbSession, day_key, text_of

DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
LIST_MAX = 30
LINE_MAX = 120
router = APIRouter()


def day_of_key(key: str, thread: str) -> str | None:
    """mousse:<对话>:<逻辑日> → 逻辑日；不是这个对话的 → None。"""
    pre = f"mousse:{thread}:"
    day = key[len(pre):] if key.startswith(pre) else ""
    return day if DAY.match(day) else None


def _line(text: str) -> str:
    t = " ".join((text or "").split())
    return t if len(t) <= LINE_MAX else t[:LINE_MAX - 1] + "…"


def _said(m: dict) -> bool:
    """用户自己说的一句（app 发来的带 ref / at；备料的补充、压缩的摘要、自动触发的不算）。"""
    return m.get("role") == "user" and not m.get("compaction") and ("ref" in m or "at" in m)


def current(thread: str, today: str) -> DbSession | None:
    return DbSession.latest(day_key(thread, today))


def sessions_of(thread: str, today: str, limit: int = LIST_MAX) -> list[dict]:
    """这个对话的会话，新的在前（见文件开头）。"""
    pre = f"mousse:{thread}:"
    with store.read() as conn:
        rows = conn.execute("SELECT * FROM engine_sessions WHERE key >= ? AND key < ? ORDER BY julianday(created_at) DESC, rowid DESC LIMIT ?",
                            (pre, pre[:-1] + ";", limit * 2)).fetchall()
        rows = [r for r in rows if day_of_key(r["key"], thread)][:limit]
        turns = {r["session"]: r["n"] for r in conn.execute(
            f"SELECT session, COUNT(*) AS n FROM engine_turns WHERE session IN ({','.join('?' * len(rows))}) GROUP BY session",
            [r["id"] for r in rows]).fetchall()} if rows else {}
    now = current(thread, today)
    out = []
    for r in rows:
        s = DbSession(r)
        path = s.path()
        msgs = [json.loads(e["data"]) for e in path if e["kind"] == "message"]
        said = [_line(text_of(m)) for m in msgs if _said(m)]
        last = next((_line(text_of(m)) for m in reversed(msgs) if m.get("role") in ("user", "assistant") and text_of(m).strip()), "")
        how = None
        if path and path[0]["kind"] == "context":
            fresh = json.loads(path[0]["data"]).get("fresh")
            how = {"reason": fresh.get("reason"), "from": fresh.get("from")} if isinstance(fresh, dict) else None
        fork = next((json.loads(e["data"]).get("fork") for e in path if e["kind"] == "context" and '"fork"' in e["data"]), None)
        if isinstance(fork, dict):
            how = {"reason": "fork", "from": fork.get("from")}
        first = said[0] if said else ""
        out.append({"id": s.id, "day": day_of_key(s.key, thread), "created": s.created_at, "updated": s.updated_at, "turns": turns.get(s.id, 0),
                    "first": first, "last": last, "title": titles.derived(first) or L("（还没说话）", "(nothing said yet)"), "titleFrom": "derived",
                    "current": now is not None and now.id == s.id, **({"how": how} if how else {})})
    named = titles.stored([o["id"] for o in out])  # 第 14 项：起过名的（engine.titles）用起的名
    for o in out:
        if o["id"] in named:
            o["title"], o["titleFrom"] = named[o["id"]], "llm"
    return out


def owned(thread: str, sid: str) -> DbSession:
    """这个对话的那一条会话；不是 → KeyError。"""
    s = DbSession.get(sid)
    if s is None or day_of_key(s.key, thread) is None:
        raise KeyError(sid)
    return s


def fork(thread: str, today: str, source: DbSession, entry: str | None = None) -> tuple[DbSession, str | None]:
    """从 source 的 entry 分出一条新会话（见文件开头）→ (新的, 分在用户那一句上时那句原话)。entry 不在这条会话里 → KeyError。"""
    rows = source._rows()
    text = None
    cut = source.leaf
    if entry is not None:
        if entry not in rows:
            raise KeyError(entry)
        e = rows[entry]
        if e["kind"] == "message" and e["role"] == "user":  # 分在用户说的一句上：那一句和后面的都不要（同撤回），原话还回去
            text = text_of(json.loads(e["data"]))
            cut = e["parent"]
        else:
            cut = entry
    chain, cur, seen = [], cut, set()
    while cur and cur in rows and cur not in seen:
        seen.add(cur)
        chain.append(rows[cur])
        cur = rows[cur]["parent"]
    chain.reverse()
    new = DbSession.create(day_key(thread, today), kind=source.kind, agent=source.agent, model=source.model, system=source.system)
    ids: dict[str, str] = {}
    parent = None
    with store.tx() as conn:
        for r in chain:
            d = json.loads(r["data"])
            if r["kind"] == "message" and d.get("role") == "assistant":
                d.pop("cc", None)  # Claude Code 的会话 id 不抄：它那边的会话还有分叉以后的话
                d.pop("cc_total", None)
            elif r["kind"] == "compaction" and d.get("first_kept") in ids:
                d["first_kept"] = ids[d["first_kept"]]
            elif r["kind"] == "prune":
                d["ids"] = [ids.get(str(i), i) for i in d.get("ids") or []]
            nid = "e_" + uuid.uuid4().hex[:16]
            conn.execute("INSERT INTO engine_entries(id, session, parent, kind, role, data, turn, ts) VALUES(?,?,?,?,?,?,?,?)",
                         (nid, new.id, parent, r["kind"], r["role"], json.dumps(d, ensure_ascii=False), r["turn"], r["ts"]))
            ids[r["id"]] = parent = nid
        conn.execute("UPDATE engine_sessions SET leaf=? WHERE id=?", (parent, new.id))
    new.leaf = parent
    new.append({"role": "", "fork": {"from": source.id, "at": cut}}, kind="context")  # 记一笔从哪里分出来的（不是消息，不发给模型）
    return new, text


def branches(s: DbSession) -> list[dict]:
    """这条会话树上的每一枝（见文件开头），新的在前。"""
    rows = s._rows()
    kids: dict[str | None, list[str]] = {}
    for r in rows.values():
        kids.setdefault(r["parent"], []).append(r["id"])
    on = [r["id"] for r in s.path()]
    ends = [r["id"] for r in rows.values() if r["id"] not in kids]
    if s.leaf and s.leaf not in ends:  # 撤回以后还没说新的：现在停在半中间，也算一枝
        ends.append(s.leaf)
    out = []
    for leaf in ends:
        chain, cur = [], leaf
        while cur and cur in rows:
            chain.append(rows[cur])
            cur = rows[cur]["parent"]
        chain.reverse()
        msgs = [json.loads(r["data"]) for r in chain if r["kind"] == "message"]
        last = next((_line(text_of(m)) for m in reversed(msgs) if m.get("role") in ("user", "assistant") and text_of(m).strip()), "")
        split = next((r["parent"] for r in chain if r["id"] not in on), None)  # 从现在这一枝的哪一条岔出去的
        out.append({"leaf": leaf, "ts": rows[leaf]["ts"], "last": last, "messages": len(msgs), "current": leaf == s.leaf,
                    **({"from": split} if split else {})})
    return sorted(out, key=lambda b: str(b["ts"] or ""), reverse=True)


def switch(s: DbSession, leaf: str) -> None:
    """切到那一枝（branches 列出来的）；不是 → KeyError。"""
    if leaf not in {b["leaf"] for b in branches(s)}:
        raise KeyError(leaf)
    s.set_leaf(leaf)


def entry_of_message(s: DbSession, ref: int) -> str | None:
    """app 里用户那句（messages.id）在这条会话里是哪一条。"""
    for r in s._rows().values():
        if r["kind"] == "message" and r["role"] == "user" and json.loads(r["data"]).get("ref") == ref:
            return r["id"]
    return None


# —— 接口 ————————————————————————————————————————————————————————————————————————

def _builtin() -> None:
    import claw
    if not claw.is_builtin():
        raise HTTPException(404, "only with the built-in engine")


def _today(thread: str) -> str:
    from . import commands
    return commands.today(thread)


def _idle(thread: str) -> None:
    from . import commands
    if commands.busy(thread):
        raise HTTPException(409, L("还在回复：等它说完，或者先停下。", "A reply is still running: wait for it to finish, or stop it first."))


@router.get("/api/engine/chats/{thread}/sessions")
async def sessions_endpoint(thread: str, limit: int = LIST_MAX):
    import asyncio
    _builtin()
    out = await asyncio.to_thread(sessions_of, thread, _today(thread), max(1, min(limit, 100)))
    titles.upgrade(out)  # engine.titles 开了：还没起过名的在后台起，下次列就有
    return {"ok": True, "sessions": out}


class ResumeIn(BaseModel):
    session: str


@router.post("/api/engine/chats/{thread}/resume")
async def resume_endpoint(thread: str, body: ResumeIn):
    from . import commands, owner
    _builtin()
    _idle(thread)
    try:
        src = owned(thread, body.session)
    except KeyError as e:
        raise HTTPException(404, "no such session in this chat") from e
    s, _ = await owner.resume_from(thread, _today(thread), src, agent=owner.agent_of(thread), model=commands.current_model(thread))
    return {"ok": True, "session": s.id, "from": src.id}


class ForkIn(BaseModel):
    session: str | None = None
    entry: str | None = None
    message: str | None = None  # app 里用户那句的 id（"db123"）


@router.post("/api/engine/chats/{thread}/fork")
def fork_endpoint(thread: str, body: ForkIn):
    _builtin()
    _idle(thread)
    today = _today(thread)
    try:
        src = owned(thread, body.session) if body.session else current(thread, today)
    except KeyError as e:
        raise HTTPException(404, "no such session in this chat") from e
    if src is None:
        raise HTTPException(404, "this chat has no session yet")
    entry = body.entry
    if body.message:
        m = re.match(r"^(?:db)?(\d+)$", body.message.strip())
        entry = entry_of_message(src, int(m.group(1))) if m else None
        if entry is None:
            raise HTTPException(404, "that message isn't in this session")
    try:
        new, text = fork(thread, today, src, entry)
    except KeyError as e:
        raise HTTPException(404, "no such entry in this session") from e
    return {"ok": True, "session": new.id, "from": src.id, **({"text": text} if text is not None else {})}


@router.get("/api/engine/chats/{thread}/branches")
def branches_endpoint(thread: str, session: str | None = None):
    _builtin()
    try:
        s = owned(thread, session) if session else current(thread, _today(thread))
    except KeyError as e:
        raise HTTPException(404, "no such session in this chat") from e
    if s is None:
        return {"ok": True, "session": None, "branches": []}
    return {"ok": True, "session": s.id, "branches": branches(s)}


class BranchIn(BaseModel):
    leaf: str
    session: str | None = None


@router.post("/api/engine/chats/{thread}/branch")
def branch_endpoint(thread: str, body: BranchIn):
    _builtin()
    _idle(thread)
    try:
        s = owned(thread, body.session) if body.session else current(thread, _today(thread))
        if s is None:
            raise KeyError("none")
        switch(s, body.leaf)
    except KeyError as e:
        raise HTTPException(404, "no such branch in this session") from e
    return {"ok": True}


@router.get("/api/engine/chats/{thread}/export")
def export_endpoint(thread: str, format: str = "md", tools: int = 0, session: str | None = None):  # noqa: A002 — 查询参数就叫 format
    """导出一条会话（engine/export.py）：不给 session = 现在这条。当附件下载；HTML 照不可信的文件发（沙箱）。"""
    import files
    from fastapi.responses import Response

    from . import export
    _builtin()
    try:
        s = owned(thread, session) if session else current(thread, _today(thread))
    except KeyError as e:
        raise HTTPException(404, "no such session in this chat") from e
    if s is None:
        raise HTTPException(404, "this chat has no session yet")
    try:
        body, name, mime = export.render(thread, s, format, bool(tools))
    except ValueError as e:
        raise HTTPException(400, "format should be md, json or html") from e
    return Response(body, media_type=f"{mime}; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"', **files.untrusted_headers(mime)})
