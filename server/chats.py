"""对话列表（2026-10-13，「对话」tab 的 Grava 一边）：顶上一排 Agent、下面主对话 + 会议 / Agent 群 + 项目，每行带未读、置顶、免打扰和状态。

- GET /api/chats → {agents, rows, ended, archived}
  agents：每个 Agent 一项 {id, name, icon, color, purpose, line, lineRole, at, unread, pinned, muted, state}。
  rows：主对话（kind main，永远第一行、算置顶、不能删）+ 会议 / Agent 群（kind meeting / room，由 meetings 模块给）+ 没归档的项目（kind project）。
  ended：已结束的会议条数；archived：已归档的项目条数（app 在列表底下各放一个入口）。
  GET /api/chats?archived=1 → {rows}：已结束的会议 + 已归档的项目。
- 排序只算你的话和给你的回复：at = 最后一条 role=user 或 (role=grava 且 origin=user) 的时间；定时器、收件箱、主对话转来（origin auto / relay）
  的回复不改排序、不算未读（unread = 读到的位置之后 origin=user 的回复条数）。line 是同一条的开头；一条都没有时用最新一条，at 为 null。
  顺序：置顶的在前，各自按 at 新的在前；没有 at 的按建的先后。主对话不参与排序。
- state：working = 正在回复，或它派的后台任务还在跑；waiting = 收件箱里有等你点头的（thread 或 source 是它，含 OpenClaw 执行审批）。两样都有算 waiting。
- 置顶 / 免打扰存 grava.db 的 chat_prefs（thread, pinned_at, muted）：PATCH /api/chats/{thread} {pinned?, muted?}。
  免打扰的线程：回复不推送（push.run_level → none），未读不算进 app 图标的角标（unread.badge）。好友的置顶 / 免打扰在 friends 表（friends.py）。
- DELETE /api/chats/{thread}：项目（同 DELETE /api/sidechats/{id}）、会议（meetings.delete_meeting）。主对话不能删；删 Agent 走 DELETE /api/groups/{id}。

会议 / Agent 群的数据由会议模块（meetings.py，另一条线在做）提供，这里只认两个函数，没有这个模块就没有会议：
  meetings.chat_rows() -> list[dict]：每个会议或群一项 {id: "grp-…", kind: "meeting" | "room", title, members: [Agent id 或 "main"],
      ended: bool, state?: "working" | "waiting" | None, line?, lineRole?, at?, unread?}
      line / at / unread 不给就按上面的规则从 messages 表算（thread = id）。
  meetings.delete_meeting(id) -> None：删一个会议（找不到抛 HTTPException 404）。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import chat
import inbox
from chat import _lock, now_iso
from i18n import L
from push import preview
from unread import udb

router = APIRouter()

# 你的话和给你的回复（排序、未读、最后一句都只看这些）
MINE = "(role='user' OR (role='grava' AND origin='user'))"


def pdb() -> sqlite3.Connection:
    conn = udb()
    conn.execute("""CREATE TABLE IF NOT EXISTS chat_prefs (thread TEXT PRIMARY KEY, pinned_at TEXT, muted INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL)""")
    return conn


def prefs(conn: sqlite3.Connection) -> dict[str, dict]:
    return {r["thread"]: {"pinned_at": r["pinned_at"], "muted": bool(r["muted"])} for r in conn.execute("SELECT * FROM chat_prefs")}


def muted_threads() -> set[str]:
    """免打扰的线程（push.run_level、unread.badge 用）。表还没建就是空。"""
    try:
        with _lock, udb() as conn:
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='chat_prefs'").fetchone():
                return set()
            return {r["thread"] for r in conn.execute("SELECT thread FROM chat_prefs WHERE muted=1")}
    except sqlite3.Error:
        return set()


def activity(conn: sqlite3.Connection, thread: str, mark: int) -> dict:
    """一个线程的 line / lineRole / at / unread（规则见文件开头）。"""
    last = conn.execute(f"SELECT role, text, ts FROM messages WHERE thread=? AND {MINE} ORDER BY id DESC LIMIT 1", (thread,)).fetchone()  # noqa: S608
    at = last["ts"] if last else None
    if last is None:
        last = conn.execute("SELECT role, text, ts FROM messages WHERE thread=? ORDER BY id DESC LIMIT 1", (thread,)).fetchone()
    n = conn.execute("SELECT COUNT(*) FROM messages WHERE thread=? AND role='grava' AND origin='user' AND id>?", (thread, mark)).fetchone()[0]
    return {"line": preview(last["text"]) if last and (last["text"] or "").strip() else "", "lineRole": last["role"] if last else None,
            "at": at, "unread": n}


def meeting_rows() -> list[dict]:
    """会议模块给的行；没有这个模块（或它出错）就是空。"""
    try:
        import meetings  # type: ignore[import-not-found]  # 会议（另一条线）：没合进来之前没有这个模块
    except ImportError:
        return []
    try:
        return [r for r in meetings.chat_rows() if isinstance(r, dict) and r.get("id")]
    except Exception as e:  # noqa: BLE001 — 会议那边出错不影响对话列表
        print(f"[chats] 读会议列表失败：{e}")
        return []


async def busy_threads() -> set[str]:
    """正在回复的线程 + 有后台任务在跑的线程。"""
    out = {th for th, run in chat.RUNS.items() if not run.done}
    try:
        import data  # 延迟导入：data 依赖 chat
        for t in await data.task_rows():
            if (t.get("execution") or {}).get("state") == "running":
                out.add(data.origin_of(t.get("ownerKey") or ""))
    except Exception:  # noqa: BLE001 — 任务台账读不到：只看正在回复的
        pass
    return out


async def waiting_threads() -> set[str]:
    """收件箱里有等你点头的线程（条目的 thread 和 source 都算），含 OpenClaw 执行审批（60 秒缓存）。"""
    with _lock, inbox.idb() as conn:
        inbox.expire(conn)
        rows = conn.execute("SELECT thread, source FROM inbox WHERE status='pending'").fetchall()
    out = {r["thread"] for r in rows} | {r["source"] for r in rows}
    try:
        import data  # 延迟导入
        execs, _ = await inbox.exec_pending(60)
        out |= {data.exec_approval(a)["agent"] or "main" for a in execs}
    except Exception:  # noqa: BLE001
        pass
    return out


def state_of(thread: str, busy: set[str], waiting: set[str]) -> str | None:
    return "waiting" if thread in waiting else "working" if thread in busy else None


def order(items: list[dict]) -> list[dict]:
    """置顶的在前；各自按 at 新的在前，没有 at 的排后面（按原来的先后）。"""
    def key(x: dict):
        return (0 if x["pinned"] else 1, 0 if x.get("at") else 1, -stamp(x.get("at")))
    return sorted(items, key=key)


def stamp(ts: str | None) -> float:
    if not ts:
        return 0.0
    try:
        return datetime.fromisoformat(ts).timestamp()
    except ValueError:
        return 0.0


async def build(archived: bool = False) -> dict:
    busy, waiting = await busy_threads(), await waiting_threads()
    meets = meeting_rows()
    with _lock, pdb() as conn:
        marks = {r["thread"]: r["last_read"] for r in conn.execute("SELECT thread, last_read FROM read_marks")}
        pref = prefs(conn)

        def base(thread: str) -> dict:
            p = pref.get(thread) or {}
            return {"pinned": bool(p.get("pinned_at")), "muted": bool(p.get("muted")), **activity(conn, thread, marks.get(thread) or 0)}

        projects = conn.execute("SELECT id, title, purpose, archived, created_at FROM side_chats ORDER BY created_at").fetchall()
        rows: list[dict] = []
        for m in meets:
            if bool(m.get("ended")) != archived:
                continue
            row = {"kind": m.get("kind") if m.get("kind") in ("meeting", "room") else "meeting", "id": m["id"], "title": m.get("title") or "",
                   "members": [x for x in m.get("members") or [] if isinstance(x, str)], **base(m["id"])}
            for k in ("line", "lineRole", "at", "unread"):  # 会议模块自己算了就用它的
                if m.get(k) is not None:
                    row[k] = m[k]
            row["state"] = m.get("state") if m.get("state") in ("working", "waiting") else state_of(m["id"], busy, waiting)
            rows.append(row)
        for p in projects:
            if bool(p["archived"]) != archived:
                continue
            rows.append({"kind": "project", "id": p["id"], "title": p["title"], "purpose": p["purpose"] or "", **base(p["id"]),
                         "state": state_of(p["id"], busy, waiting)})
        if archived:
            for r in rows:
                r["pinned"] = False
            return {"ok": True, "rows": order(rows)}
        main = {"kind": "main", "id": "main", "title": "", **base("main"), "state": state_of("main", busy, waiting)}
        main["pinned"] = True  # 主对话固定第一行
        groups = conn.execute("SELECT id, name, icon, color, purpose FROM groups ORDER BY position, created_at").fetchall()
        agents = [{"id": g["id"], "name": g["name"], "icon": g["icon"], "color": g["color"], "purpose": g["purpose"] or "", **base(g["id"]),
                   "state": state_of(g["id"], busy, waiting)} for g in groups]
        ended = sum(1 for m in meets if m.get("ended"))
        n_archived = sum(1 for p in projects if p["archived"])
    return {"ok": True, "agents": order(agents), "rows": [main, *order(rows)], "ended": ended, "archived": n_archived}


@router.get("/api/chats")
async def chats(archived: bool = False):
    """对话列表（见文件开头）。archived=1：已结束的会议和已归档的项目。"""
    return await build(archived)


def known(thread: str) -> str | None:
    """这个线程是什么：main / agent / project / meeting；不认识 = None。"""
    if thread == "main":
        return "main"
    with _lock, udb() as conn:
        if conn.execute("SELECT 1 FROM groups WHERE id=?", (thread,)).fetchone():
            return "agent"
        if conn.execute("SELECT 1 FROM side_chats WHERE id=?", (thread,)).fetchone():
            return "project"
    if any(m["id"] == thread for m in meeting_rows()):
        return "meeting"
    return None


class PrefsIn(BaseModel):
    pinned: bool | None = None  # 主对话固定第一行，给了也不改
    muted: bool | None = None   # 不推送，未读不算进 app 图标的角标


@router.patch("/api/chats/{thread}")
async def patch_chat(thread: str, body: PrefsIn):
    kind = known(thread)
    if kind is None:
        raise HTTPException(404, L("没有这个对话", "No such chat"))
    with _lock, pdb() as conn:
        cur = conn.execute("SELECT pinned_at, muted FROM chat_prefs WHERE thread=?", (thread,)).fetchone()
        pinned_at = cur["pinned_at"] if cur else None
        muted = bool(cur["muted"]) if cur else False
        if body.pinned is not None and kind != "main" and body.pinned != bool(pinned_at):
            pinned_at = now_iso() if body.pinned else None
        if body.muted is not None:
            muted = body.muted
        conn.execute("""INSERT INTO chat_prefs(thread, pinned_at, muted, updated_at) VALUES(?,?,?,?)
            ON CONFLICT(thread) DO UPDATE SET pinned_at=excluded.pinned_at, muted=excluded.muted, updated_at=excluded.updated_at""",
                     (thread, pinned_at, int(muted), now_iso()))
    return {"ok": True, "thread": thread, "pinned": kind == "main" or bool(pinned_at), "muted": muted}


@router.delete("/api/chats/{thread}")
async def delete_chat(thread: str):
    """删一行：项目（对话记录、项目卡、日程里它的截止）或会议（会议记录；已确认的待办留在日程和看板）。"""
    kind = known(thread)
    if kind is None:
        raise HTTPException(404, L("没有这个对话", "No such chat"))
    if kind == "main":
        raise HTTPException(400, L("主对话不能删除", "The main chat can't be deleted"))
    if kind == "agent":
        raise HTTPException(400, L("删除 Agent 请用 DELETE /api/groups/{id}", "Delete an Agent with DELETE /api/groups/{id}"))
    if kind == "project":
        import data  # 延迟导入
        await data.delete_side_chat(thread)
    else:
        import meetings  # type: ignore[import-not-found]
        res = meetings.delete_meeting(thread)
        if hasattr(res, "__await__"):
            await res
    with _lock, pdb() as conn:
        conn.execute("DELETE FROM chat_prefs WHERE thread=?", (thread,))
    return {"ok": True}
