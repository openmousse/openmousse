"""通讯录（主线第三步「Agents 和群聊」第 7 件，2026-10-21）：一次给全，app 的通讯录页（对话页 Agent 栏最前面那一格）照着画。

  管家（顶上一行）：第一步还没有管家，这一行就是现在的名片 Agent（点进去到名片 Agent 页）；第三步升级成管家。
  三段：赛博（按最近对话排，带标签和模型）、功能 Agent（带一句职责，照 Agent 栏的先后）、好友（最近聊过的在前；每位下面挂一行
  「X 的管家」：对方的名片上有 Agent（caps 里有 agent / a2a）才有）。
搜索、筛选：GET /api/contacts?q=…&filter=all | cyber | agent | friend。每一项另带 keywords（名字、标签、模型、职责……拼在一起，
  小写），app 打字时就地筛，不用每个字都问服务器；filters 是四个筛选和各自几项。
和赛博一样挂在 cybers 开关后面（关着 404，app 不显示通讯录的入口）。只读：不改任何东西。
"""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException

import cybers
from chat import _lock
from i18n import L

router = APIRouter(dependencies=[Depends(cybers.enabled)])

FILTERS = ("all", "cyber", "agent", "friend")
TITLES = {"all": ("全部", "All"), "cyber": ("赛博", "Cybers"), "agent": ("功能 Agent", "Agents"), "friend": ("好友", "Friends")}


def keywords(*parts) -> str:
    return " ".join(str(p) for p in parts if p).casefold()


def butler_row() -> dict:
    """顶上那一行：没开管家就是名片 Agent（替你答好友的追问、别家 Agent 的问题；说出去之前过 Doorman）；开了管家（butler.py，
    第三步：名片 Agent 升级）是管家，带它的名字和形象，点进去到管家资料页。"""
    import butler
    import friends
    if butler.on():
        name = butler.display_name()
        line = L("只对外 · 替你出面：好友群、好友的追问、别家 Agent", "Outside only · speaks for you: friend groups, friends' questions, other people's Agents")
        return {"kind": "butler", "id": "butler", "name": name, "line": line, "available": True, "open": "Butler", "avatar": butler.avatar(),
                "keywords": keywords(name, line, L("管家", "butler"), L("名片 Agent", "card agent"))}
    name = L("名片 Agent", "Card agent")
    line = L("只对外 · 替你出面：好友的追问、别家 Agent", "Outside only · speaks for you: friends' questions, other people's Agents")
    return {"kind": "butler", "id": "card", "name": name, "line": line, "available": friends.agent_available(), "open": "CardAgent",
            "keywords": keywords(name, line, L("管家", "butler"))}


def cyber_items(conn: sqlite3.Connection) -> list[dict]:
    out = []
    for c in cybers.ordered(cybers.active_rows(conn), conn):
        out.append({"kind": "cyber", "id": c["id"], "thread": c["thread"], "name": c["name"], "tag": c["tag"], "model": c["modelInUse"],
                    "modelName": c["modelName"], "avatar": c["avatar"], "class": c["kind"], "no": c["no"],
                    "line": (c["last"] or {}).get("text") or c["tag"], "at": (c["last"] or {}).get("ts"),
                    "keywords": keywords(c["name"], c["tag"], c["modelName"], c["identity"])})
    return out


def agent_items(conn: sqlite3.Connection) -> list[dict]:
    return [{"kind": "agent", "id": g["id"], "name": g["name"], "icon": g["icon"], "color": g["color"], "purpose": (g["purpose"] or "").strip(),
             "line": (g["purpose"] or "").strip(), "keywords": keywords(g["name"], g["purpose"])} for g in cybers.group_rows(conn).values()]


def friend_items() -> list[dict]:
    """好友（只要在的）：最近一条消息新的在前，没聊过的按加的先后；名片上有 Agent 的下面挂「X 的管家」。"""
    import butler as butler_mod
    import social
    rows = social.friends()
    with _lock, social.sdb() as conn:
        last = {r["friend"]: r["ts"] for r in conn.execute("SELECT friend, MAX(ts) AS ts FROM friend_messages WHERE hidden_at IS NULL GROUP BY friend")}
    rows.sort(key=lambda f: last.get(f["id"]) or f["created_at"], reverse=True)
    out = []
    for f in rows:
        has_agent = "agent" in f["caps"] or "a2a" in f["caps"]
        butler = {"name": butler_mod.butler_of(f["name"]), "line": L("替对方出面", "Speaks for them")} if has_agent else None
        out.append({"kind": "friend", "id": f["id"], "name": f["name"], "tier": f["tier"], "line": f.get("note") or "", "at": last.get(f["id"]),
                    "butler": butler, "keywords": keywords(f["name"], f.get("card_name"), f.get("note"), butler and butler["name"])})
    return out


@router.get("/api/contacts")
def contacts(q: str = "", filter: str = "all"):  # noqa: A002 — 查询参数就叫 filter
    """通讯录（见文件开头）。q：名字、标签、模型、职责里有这几个字的（不分大小写）；filter：只给哪一段。"""
    if filter not in FILTERS:
        raise HTTPException(400, L(f"filter 只能是 {' / '.join(FILTERS)}", f"filter must be one of {', '.join(FILTERS)}"))
    cybers.seed_defaults()  # 第一次来时建好默认的两位（Iris、阿哲）
    with _lock, cybers.cydb() as conn:
        groups = {"cyber": cyber_items(conn), "agent": agent_items(conn)}
    groups["friend"] = friend_items()
    butler = butler_row()
    needle = " ".join(q.split()).casefold()
    if needle:
        groups = {k: [x for x in v if needle in x["keywords"]] for k, v in groups.items()}
    sections = [{"kind": k, "title": L(*TITLES[k]), "count": len(groups[k]), "items": groups[k]}
                for k in ("cyber", "agent", "friend") if filter in ("all", k)]
    show_butler = filter == "all" and (not needle or needle in butler["keywords"])
    counts = {k: len(v) for k, v in groups.items()}
    return {"ok": True, "q": q, "filter": filter, "butler": butler if show_butler else None, "sections": sections,
            "filters": [{"id": k, "label": L(*TITLES[k]), "count": sum(counts.values()) if k == "all" else counts[k]} for k in FILTERS]}
