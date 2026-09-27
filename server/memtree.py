"""世界树（「我 → 世界树」，2026-09-27）：你在 Claude、ChatGPT、Gemini 和各个 Agent 那里说过的关于你的事，一条一片叶子，挂在枝上。

真身是 Obsidian 库里的笔记（世界树/，一条一篇）。索引、写入、遗忘全部交给 workspace 的 scripts/memory_tree.py
（可选数据源 tree，见 sources.py；没有它，这两个接口回 ok=false + missing_source=tree）。这里只替 app 读，外加三件事：
确认（pending → active）、忘记（retracted：笔记掏空成只剩属性的空壳挪进归档，索引里删掉）、挪到别的枝。
memory_tree 的函数都拿跨进程锁、会先按库的最新状态重建索引，所以放进线程里跑；sqlite 连接在同一个线程里开、关。

  GET  /api/tree        枝（先序：大枝后面跟着它的小枝；leaves = 直接挂在这根枝上的叶子数，total = 连小枝一起的）
                        + 当前的叶子（active / pending，不含档案要点；挂的枝不存在的算主干「档案」）+ 档案条数 + 各来源条数 + 格式有问题的笔记数。
                        叶子的 source 是笔记里写的；origin 是最早记下它的平台：每周修剪改写 / 合并出来的（source=prune）顺着 supersedes 找回去，
                        「按来源」和 counts.bySource 都按 origin 算
  POST /api/tree/{id}   {action: confirm | forget | move, branch?}：确认 / 忘记 / 挪枝（branch 写枝名，写「档案」= 直接挂主干）

活动记录由 memory_tree 自己写，只写做了什么、不写记忆内容；actor 是「你 · 世界树」。这里不打印、不记录叶子的内容。
"""
from __future__ import annotations

import asyncio
import contextlib
import re
import sqlite3
from types import ModuleType

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import sources
from chat import _lock, db
from config import settings
from i18n import L

router = APIRouter()
ID_RE = re.compile(r"[\w-]{1,64}")
ACTIONS = ("confirm", "forget", "move")
# 各 AI 平台（世界树 MCP 的接入名 → 显示名）；Grava 自己的 Agent 写的是 grava-<agent id>
PLATFORMS = {"claude": "Claude", "chatgpt": "ChatGPT", "gemini": "Gemini", "claude-code": "Claude Code", "notion": "Notion"}


def tree() -> ModuleType:
    """memory_tree 脚本；没接就抛 NoSource（main.py 变成 200 + ok=false + missing_source=tree）。"""
    sources.require("tree")
    return sources.memory_tree


def agent_names() -> dict[str, str]:
    """Agent id → 名字（groups 表）；main 是助手自己。"""
    try:
        with _lock, db() as conn:
            names = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM groups")}
    except sqlite3.Error:  # 新实例还没有 groups 表
        names = {}
    return {**names, "main": settings.app_name}


def agent_of(source: str) -> str | None:
    """grava-<id> → 那个 Agent 的 id（grava / grava-main → main）；别的来源不是 Agent。"""
    if source == "grava":
        return "main"
    return (source.removeprefix("grava-") or "main") if source.startswith("grava-") else None


def source_name(source: str, agents: dict[str, str]) -> str:
    """来源的显示名：AI 平台、Agent 名、你自己（手机上直接写的笔记）、每周修剪（修剪时合并 / 改写出来的）。"""
    if source in PLATFORMS:
        return PLATFORMS[source]
    if source == "leo":
        return L("你自己", "You")
    if source == "prune":
        return L("每周修剪", "Weekly pruning")
    aid = agent_of(source)
    if aid:
        return agents.get(aid) or aid
    return source


def origin_of(mid: str, links: dict[str, tuple[str, str | None]]) -> str:
    """每周修剪改写 / 合并出来的叶子（source=prune）顺着 supersedes 找回最早是哪个平台记下的；找不到就还是 prune。"""
    source, seen = links[mid][0], {mid}
    cur = links[mid][1]
    while source == "prune" and cur and cur in links and cur not in seen:
        seen.add(cur)
        source, cur = links[cur]
    return source


def snapshot() -> dict:
    """整棵树（在线程里跑）。"""
    mt = sources.memory_tree
    agents = agent_names()
    with contextlib.closing(mt.connect()) as conn:  # connect() 先按库的最新状态刷新索引
        branches = mt.branch_tree(conn)
        rows = conn.execute(
            "SELECT id, text, kind, source, status, branch, tags, observed_at, created_at FROM tree "
            "WHERE source != 'profile' AND status IN ('active','pending') ORDER BY observed_at DESC, created_at DESC").fetchall()
        links = {r["id"]: (r["source"], r["supersedes"]) for r in conn.execute("SELECT id, source, supersedes FROM tree WHERE source != 'profile'")}
        trunk = conn.execute("SELECT COUNT(*) FROM tree WHERE source = 'profile'").fetchone()[0]
        issues = conn.execute("SELECT COUNT(*) FROM issue").fetchone()[0]
    names = {b["name"] for b in branches}
    total = {b["name"]: b["leaves"] for b in branches}
    for b in reversed(branches):  # 先序倒过来走：小枝先算完，再加到上一级
        if b["parent"] in total:
            total[b["parent"]] += total[b["name"]]
    leaves = []
    for r in rows:
        origin = origin_of(r["id"], links)
        leaves.append({"id": r["id"], "text": r["text"], "kind": r["kind"], "source": r["source"], "sourceName": source_name(r["source"], agents),
                       "origin": origin, "originName": source_name(origin, agents), "agent": agent_of(origin),
                       "status": r["status"], "branch": r["branch"] if r["branch"] in names else mt.TRUNK,
                       "tags": [t for t in (r["tags"] or "").split(",") if t], "observedAt": r["observed_at"], "createdAt": r["created_at"]})
    per: dict[str, int] = {}
    for x in leaves:  # 按最早记下它的平台数（修剪过的算回原来的平台）
        per[x["origin"]] = per.get(x["origin"], 0) + 1
    by_source = [{"source": s, "name": source_name(s, agents), "agent": agent_of(s), "count": n}
                 for s, n in sorted(per.items(), key=lambda kv: -kv[1])]
    return {
        "ok": True,
        "trunk": {"name": mt.TRUNK, "count": trunk},
        "branches": [{"name": b["name"], "parent": b["parent"], "about": b["about"], "depth": b["depth"], "leaves": b["leaves"],
                      "total": total[b["name"]], "agents": [{"id": a, "name": agents.get(a) or a} for a in b["agents"].split(",") if a]}
                     for b in branches],
        "leaves": leaves,
        "counts": {"total": len(leaves), "pending": sum(1 for x in leaves if x["status"] == "pending"), "bySource": by_source},
        "issues": issues,
    }


@router.get("/api/tree")
async def get_tree():
    tree()
    try:
        return await asyncio.to_thread(snapshot)
    except (OSError, RuntimeError, sqlite3.Error) as exc:
        raise HTTPException(503, L(f"世界树读不了：{exc}", f"Couldn't read the memory tree: {exc}")) from exc


class TreeAction(BaseModel):
    action: str
    branch: str | None = None  # move：枝名（或别名）；「档案」= 直接挂主干


def act(mid: str, action: str, branch: str, actor: str) -> dict | None:
    """确认 / 忘记 / 挪枝（在线程里跑）。叶子不存在、是档案要点、已经不是当前的 → None。"""
    mt = sources.memory_tree
    with contextlib.closing(mt.connect()) as conn:
        row = conn.execute("SELECT status, source, branch FROM tree WHERE id=?", (mid,)).fetchone()
        if not row or row["source"] == "profile" or row["status"] not in mt.CURRENT:
            return None
        if action == "confirm":
            if row["status"] == "active":
                return {"changed": False}
            return {"changed": True} if mt.set_status(conn, mid, "active", actor) else None
        if action == "forget":
            return {"changed": True} if mt.set_status(conn, mid, "retracted", actor) else None
        where = mt.set_branch(conn, mid, branch, actor)  # 枝对不上抛 ValueError
        return {"changed": where != (row["branch"] or mt.TRUNK), "branch": where}


@router.post("/api/tree/{mid}")
async def tree_action(mid: str, body: TreeAction):
    tree()
    if body.action not in ACTIONS:
        raise HTTPException(400, L("action 只能是 confirm、forget 或 move", "action must be confirm, forget or move"))
    branch = " ".join((body.branch or "").split())
    if body.action == "move" and not branch:
        raise HTTPException(400, L("挪到哪根枝？branch 不能空", "Move it where? branch can't be empty"))
    if not ID_RE.fullmatch(mid):
        raise HTTPException(404, L("没有这片叶子", "No such leaf"))
    try:
        res = await asyncio.to_thread(act, mid, body.action, branch, L("你 · 世界树", "You · Memory tree"))
    except ValueError as exc:  # 枝对不上：刚在 Obsidian 里改了名或删了
        raise HTTPException(409, L("没有这根枝了，刷新再挪", "That branch is gone. Refresh and try again.")) from exc
    except (OSError, RuntimeError, sqlite3.Error) as exc:
        raise HTTPException(503, L(f"世界树没改成：{exc}", f"Couldn't change the memory tree: {exc}")) from exc
    if res is None:
        raise HTTPException(404, L("这片叶子已经不在了，可能刚在别处改过。刷新看看。", "That leaf is gone; it may have just changed elsewhere. Refresh."))
    return {"ok": True, **res}
