"""日结提案（App 优化 ⑧）：每晚日结时主对话回看这一周的对话，把用户反复让它做的事、一次跑通的多步流程，
提成「加一个 skill」或「建一个 Agent」，进收件箱等用户点头。

- 谁提、怎么提：main 在日结后收到「【自动触发】日结提案」，照 proposals skill 跑 `proposals_ctl.py context` 看材料
  （GET /api/proposals/context：这一周各个对话里用户说的话、现有的 skills 和 Agent、提过的提案和被拒的理由），
  值得提的用 `proposals_ctl.py skill|agent` 交（POST /api/proposals）。多数晚上没有要提的。
- 服务端把关：依据（evidence：哪天、在哪个对话、原话）至少一条；每个逻辑日最多 MAX_PER_DAY 条（429 daily_limit）；
  同一件事（slug）只提一次：还在等的、做过的、拒过的都 409 proposed_before（撤回的、没做成的、过期的可以再提）；
  用户引用卡片说了要改（收件箱里是 revising）时，同一个 slug 再交 = 原地改这张卡（不占当天的名额）；
  skill 不能和已有的重名（409 skill_exists），草稿开头要有 frontmatter（name 和目录名一样、有 description）；
  Agent 不能重名，看板方案要画得出来（boards.plan_board）。
- 收件箱卡（kind skill / agent，默认静默）：标题、为什么（「这周你第 3 次让我……」）、会改什么。卡上的预览走 inbox.EXTRAS
  （给谁、这周的原话、skill 全文 / Agent 的职责）；Agent 的看板预览照旧是 boards.py 的 board_plans。
- 点了同意（inbox.HOOKS）服务端直接做完，卡片标完成，main 只收到一句知会：
  skill → 写进 <workspace>/skills/<名字>/SKILL.md，加进目标 Agent 的 skills 允许列表（openclaw.json，走 agents.edit_openclaw_json：
  先备份、改完 validate、不通过就恢复）。一个 Agent 的列表 = agents.entries.<id>.skills，没有就继承 agents.defaults.skills；
  都没设（= 不限制）就不用动。给 main 加是加进它继承的那一份；给别的 Agent 加、而它原来继承 defaults 的，给它写一份自己的。
  agent → 和「新建 Agent」同一条路建好（data.create_group），再建表、换上起步看板（和 agent_ctl.py create --board-file 一样）。
  没做成 → 卡片标「没做成」并写明原因（inbox.act 认 {"failed": …}），main 不用管。
- 拒绝：inbox 的 note 记下理由，以后 context 里带着，模型不再提同类的；撤回：记成 withdrawn。

不是给任何 kind=skill / agent 的卡都接手：只接这里交的（proposals.inbox_id 对得上），「先聊聊」建 Agent 那种照旧由 Agent 自己建。
表 proposals（grava.db）一条提案一行。
"""
from __future__ import annotations

import asyncio
import json
import re
import shutil
import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import yaml
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import agents
import boards
import claw
import data
import inbox
from chat import _lock, day_bounds, day_of, db, hhmm, log_activity, now_iso
from config import settings
from i18n import L

router = APIRouter()
MAX_PER_DAY = 2                 # 每个逻辑日最多几条（蓝图：每天最多一两条）
KEY = re.compile(r"[a-z0-9][a-z0-9-]{1,39}")   # slug 和 skill 名
BLOCKING = ("pending", "installed", "rejected")  # 这几种状态下同一个 slug 不能再提
MAX_SKILL_CHARS = 20000
MAX_EVIDENCE = 8
CONTEXT_DAYS = 7


def pdb() -> sqlite3.Connection:
    conn = db()
    conn.execute("""CREATE TABLE IF NOT EXISTS proposals (id TEXT PRIMARY KEY, kind TEXT NOT NULL, slug TEXT NOT NULL, title TEXT NOT NULL,
        why TEXT NOT NULL DEFAULT '', evidence TEXT NOT NULL DEFAULT '[]', name TEXT NOT NULL, agents TEXT NOT NULL DEFAULT '[]',
        content TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT 'main', status TEXT NOT NULL DEFAULT 'pending', inbox_id TEXT,
        day TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', result TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, decided_at TEXT)""")
    conn.execute("CREATE INDEX IF NOT EXISTS proposals_slug ON proposals(slug)")
    conn.execute("CREATE INDEX IF NOT EXISTS proposals_inbox ON proposals(inbox_id)")
    return conn


def clip(s: str, n: int) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def bad(zh: str, en: str, code: int = 400, **extra) -> JSONResponse:
    return JSONResponse({"ok": False, "detail": L(zh, en), **extra}, status_code=code)


# —— skill 文件和允许列表 ————————————————————————————————————————————————

def skills_root() -> Path:
    if not claw.is_openclaw() and claw.cfg().get("skills"):  # 别的 claw：它自己的 skills 文件夹（server.json 的 claw.skills）
        return Path(str(claw.cfg()["skills"])).expanduser()
    return settings.workspace / "skills"


_FM = re.compile(r"\A﻿?---[ \t]*\r?\n(.*?)^---[ \t]*(?:\r?\n|\Z)", re.S | re.M)


def frontmatter(text: str) -> dict:
    m = _FM.match(text or "")
    if not m:
        return {}
    try:
        fm = yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        return {}
    return fm if isinstance(fm, dict) else {}


def body_of(text: str) -> str:
    m = _FM.match(text or "")
    return (text[m.end():] if m else text or "").strip()


def installed_skills() -> list[dict]:
    root = skills_root()
    out = []
    for d in sorted(root.iterdir()) if root.is_dir() else []:
        f = d / "SKILL.md"
        if not (d.is_dir() and f.is_file()):
            continue
        try:
            fm = frontmatter(f.read_text(encoding="utf8", errors="replace")[:6000])
        except OSError:
            continue
        out.append({"name": str(fm.get("name") or d.name), "dir": d.name, "description": clip(str(fm.get("description") or ""), 160)})
    return out


def read_config() -> dict:
    try:
        return json.loads(settings.openclaw_json.read_text(encoding="utf8"))
    except (OSError, ValueError):
        return {}


def allowlist(cfg: dict, agent_id: str) -> list | None:
    """这个 Agent 的 skills 允许列表：自己的（agents.entries.<id>.skills），没有就继承 defaults；None = 没设，不限制。"""
    ag = cfg.get("agents") or {}
    entry = (ag.get("entries") or {}).get(agent_id)
    if isinstance(entry, dict) and isinstance(entry.get("skills"), list):
        return entry["skills"]
    d = (ag.get("defaults") or {}).get("skills")
    return d if isinstance(d, list) else None


def grant(agent_ids: list[str], name: str) -> list[str]:
    """把 skill 加进这些 Agent 的允许列表（openclaw.json）。返回真的改了的 Agent（没设列表 = 本来就能用，不算）。
    别的 claw 没有 OpenClaw 的允许列表：放进它的 skills 文件夹就算装好了。"""
    if not claw.is_openclaw():
        return []
    changed: list[str] = []

    def change(cfg: dict) -> bool:
        ag = cfg.setdefault("agents", {})
        entries = ag.setdefault("entries", {})
        defaults = ag.get("defaults") if isinstance(ag.get("defaults"), dict) else {}
        dlist = defaults.get("skills") if isinstance(defaults.get("skills"), list) else None
        changed.clear()
        for aid in agent_ids:
            entry = entries.get(aid)
            if isinstance(entry, dict) and isinstance(entry.get("skills"), list):
                target = entry["skills"]
            elif dlist is None:
                continue  # 没设列表：什么 skill 都能用
            elif aid == "main":
                target = dlist  # main 用的就是 defaults 那一份
            elif isinstance(entry, dict):
                entry["skills"] = list(dlist)  # 原来继承 defaults：给它写一份自己的，免得连带改了 main
                target = entry["skills"]
            else:
                continue  # openclaw.json 里没有这个 Agent 的条目：不凭空造一条
            if name not in target:
                target.append(name)
                changed.append(aid)
        return bool(changed)

    agents.edit_openclaw_json(change, f"skill-{name}")
    return changed


def agent_names() -> dict[str, str]:
    with _lock, data.ddb() as conn:
        rows = conn.execute("SELECT id, name FROM groups").fetchall()
    return {"main": settings.app_name, **{r["id"]: r["name"] for r in rows}}


def thread_names() -> dict[str, str]:
    with _lock, data.ddb() as conn:
        groups = conn.execute("SELECT id, name FROM groups").fetchall()
        sides = conn.execute("SELECT id, title FROM side_chats").fetchall()
    out = {"main": L("主对话", "Main chat")}
    out.update({r["id"]: r["name"] for r in groups})
    out.update({r["id"]: L(f"项目「{r['title']}」", f'Project "{r["title"]}"') for r in sides})
    return out


# —— 提交 ————————————————————————————————————————————————————————————

class Evidence(BaseModel):
    date: str                 # 哪天（YYYY-MM-DD 或 9/24）
    thread: str = ""          # 在哪个对话（名字或 id）
    quote: str                # 原话（短）


class SkillSpec(BaseModel):
    name: str                 # skill 名 = 目录名：小写字母、数字、连字符
    agents: list[str]         # 给谁：main 或 Agent id
    markdown: str             # SKILL.md 全文（开头 frontmatter：name、description）


class AgentSpec(BaseModel):
    name: str
    purpose: str
    icon: str = "moon"
    color: str | None = None
    board: dict | None = None     # 起步看板方案 {tables, blocks}（写法见 skills/board）
    skills: list[str] | None = None


class ProposalIn(BaseModel):
    kind: str                     # skill / agent
    slug: str                     # 这件事的固定键（同一件事只提一次）
    title: str                    # 卡片标题，大白话
    why: str                      # 为什么：「这周你第 3 次让我……」
    evidence: list[Evidence]
    changes: list[str] = []       # 额外几行「会改什么」
    source: str = "main"
    skill: SkillSpec | None = None
    agent: AgentSpec | None = None


def sync_status(conn: sqlite3.Connection) -> None:
    """还挂着的提案，收件箱那边已经定了（过期、钩子没接上的拒绝 / 撤回）：跟上。"""
    try:
        rows = conn.execute("""SELECT p.id, i.status, i.note FROM proposals p JOIN inbox i ON i.id = p.inbox_id
            WHERE p.status='pending' AND i.status IN ('expired','rejected','withdrawn')""").fetchall()
    except sqlite3.OperationalError:  # 新装的实例还没有 inbox 表
        return
    for r in rows:
        conn.execute("UPDATE proposals SET status=?, note=?, decided_at=IFNULL(decided_at, ?) WHERE id=?",
                     (r["status"], r["note"] or "", now_iso(), r["id"]))


@router.post("/api/proposals")
async def propose(body: ProposalIn):
    """main 交一条提案（经 proposals_ctl.py）：存下来、交一张收件箱卡。"""
    kind = body.kind.strip().lower()
    if kind not in ("skill", "agent"):
        return bad("kind 只能是 skill 或 agent", "kind must be skill or agent")
    slug = body.slug.strip().lower()
    if not KEY.fullmatch(slug):
        return bad("slug 写成小写字母、数字和连字符，2–40 个字符", "slug: lowercase letters, digits and hyphens, 2–40 characters")
    title, why = clip(body.title, 80), body.why.strip()
    if not title or not why:
        return bad("要写 title（做什么）和 why（为什么，带次数）", "title (what) and why (with how often) are required")
    evidence = [{"date": clip(e.date, 12), "thread": clip(e.thread, 30), "quote": clip(e.quote, 120)} for e in body.evidence if e.quote.strip()]
    if not evidence:
        return bad("要写依据：哪天、在哪个对话、原话", "Evidence is required: which day, which chat, the words used")
    today = day_of(now_iso())
    with _lock, pdb() as conn:
        sync_status(conn)
        old = conn.execute(f"SELECT * FROM proposals WHERE slug=? AND status IN ({','.join('?' * len(BLOCKING))}) "
                           "ORDER BY created_at DESC LIMIT 1", (slug, *BLOCKING)).fetchone()
        used = conn.execute("SELECT COUNT(*) FROM proposals WHERE day=? AND status NOT IN ('withdrawn','failed')", (today,)).fetchone()[0]
    # 用户引用这张卡说了要怎么改（收件箱里是 revising）：同一个 slug 再交 = 原地改，不算新的一条
    revising = None
    if old is not None and old["status"] == "pending" and old["kind"] == kind and old["inbox_id"]:
        try:
            revising = old if inbox.item(old["inbox_id"])["status"] == "revising" else None
        except HTTPException:
            revising = None
    if old is not None and revising is None:
        return bad(f"这件事提过了（{old['status']}），别再提", f"This was proposed before ({old['status']}); don't propose it again", 409,
                   error="proposed_before", status=old["status"])
    if revising is None and used >= MAX_PER_DAY:
        return bad(f"今天已经提了 {used} 条，最多 {MAX_PER_DAY} 条：留到以后", f"Already {used} today (max {MAX_PER_DAY}); keep it for later", 429,
                   error="daily_limit")
    names = agent_names()
    extra = [clip(c, 100) for c in body.changes if c.strip()]
    if kind == "skill":
        s = body.skill
        if s is None:
            return bad("kind skill 要带 skill：{name, agents, markdown}", "kind skill needs skill: {name, agents, markdown}")
        name = s.name.strip().lower()
        if not KEY.fullmatch(name):
            return bad("skill 名写成小写字母、数字和连字符", "Skill name: lowercase letters, digits and hyphens")
        if (skills_root() / name).exists():  # 还没装，所以改稿时同名也在这里挡不到自己
            return bad(f"已经有叫 {name} 的 skill 了：换个名字，或者别提", f"A skill named {name} already exists", 409, error="skill_exists")
        md = s.markdown.replace("\r\n", "\n").strip() + "\n"
        if len(md) > MAX_SKILL_CHARS:
            return bad(f"SKILL.md 太长（最多 {MAX_SKILL_CHARS} 字）", f"SKILL.md is too long (max {MAX_SKILL_CHARS} characters)")
        fm = frontmatter(md)
        if str(fm.get("name") or "") != name or not str(fm.get("description") or "").strip():
            return bad(f"SKILL.md 开头要有 frontmatter：name: {name} 和一行 description", f"SKILL.md must start with frontmatter: name: {name} and a description")
        targets = list(dict.fromkeys(a.strip() for a in s.agents if a.strip()))
        unknown = [a for a in targets if a not in names]
        if not targets or unknown:
            return bad(f"agents 写 main 或已有的 Agent id{('：没有 ' + '、'.join(unknown)) if unknown else ''}",
                       f"agents: main or existing Agent ids{(' (unknown: ' + ', '.join(unknown) + ')') if unknown else ''}")
        content, agents_json, pname = md, json.dumps(targets), name
        who = "、".join(names[a] for a in targets)
        changes = [L(f"新 skill「{name}」：{clip(str(fm['description']), 80)}", f'New skill "{name}": {clip(str(fm["description"]), 80)}'),
                   L(f"给：{who}", f"For: {', '.join(names[a] for a in targets)}"), *extra[:3]]
        approve = L("加上", "Add it")
    else:
        a = body.agent
        if a is None:
            return bad("kind agent 要带 agent：{name, purpose, icon, color, board}", "kind agent needs agent: {name, purpose, icon, color, board}")
        name = data.agent_name(a.name)      # 空 400、重名 409
        purpose = a.purpose.strip()
        if not purpose:
            return bad("要写这个 Agent 管什么（purpose）", "purpose is required")
        icon, color = data.icon_key(a.icon), data.color_key(a.color)
        if a.board is not None:
            boards.plan_board(a.board)      # 画不出来就 400
        content = json.dumps({"name": name, "purpose": purpose, "icon": icon, "color": color, "board": a.board, "skills": a.skills}, ensure_ascii=False)
        agents_json, pname = "[]", name
        changes = [L(f"新 Agent「{name}」：{clip(purpose, 80)}", f'New Agent "{name}": {clip(purpose, 80)}'), *extra[:4]]
        approve = L("建好它", "Create it")
    ev_json = json.dumps(evidence, ensure_ascii=False)
    if revising is not None:
        pid = revising["id"]
        with _lock, pdb() as conn:
            conn.execute("UPDATE proposals SET title=?, why=?, evidence=?, name=?, agents=?, content=? WHERE id=?",
                         (title, why, ev_json, pname, agents_json, content, pid))
    else:
        pid, ts = f"pp-{uuid.uuid4().hex[:8]}", now_iso()
        with _lock, pdb() as conn:
            conn.execute("""INSERT INTO proposals(id, kind, slug, title, why, evidence, name, agents, content, source, status, day, created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,'pending',?,?)""", (pid, kind, slug, title, why, ev_json, pname, agents_json, content,
                                                           body.source.strip() or "main", today, ts))
    res = await inbox.add(inbox.ItemIn(kind=kind, title=title, source=body.source.strip() or "main", thread="main", why=why,
                                       changes=changes, approveLabel=approve, dedupe=f"proposal:{kind}:{slug}"))
    if isinstance(res, JSONResponse):  # 收件箱那边 30 天内拒过同一个 dedupe
        if revising is None:
            with _lock, pdb() as conn:
                conn.execute("DELETE FROM proposals WHERE id=?", (pid,))
        return res
    iid = res["id"]
    with _lock, pdb() as conn:
        conn.execute("UPDATE proposals SET inbox_id=? WHERE id=?", (iid, pid))
    if kind == "agent" and body.agent and body.agent.board:
        boards.save_plan(iid, boards.PlanIn(plan=body.agent.board))
    return {"ok": True, "id": pid, "inboxId": iid, **({"updated": True} if revising is not None else {})}


# —— 点了同意 / 拒绝 ————————————————————————————————————————————————————

def install_skill(p: sqlite3.Row) -> str:
    name = p["name"]
    target = skills_root() / name
    if target.exists():
        raise ValueError(L(f"已经有叫 {name} 的 skill 了", f"A skill named {name} already exists"))
    target.mkdir(parents=True)
    tmp = target / ".SKILL.md.tmp"
    tmp.write_text(p["content"], encoding="utf8")
    tmp.replace(target / "SKILL.md")
    targets = json.loads(p["agents"] or "[]")
    try:
        grant(targets, name)
    except agents.ProvisionError:
        shutil.rmtree(target, ignore_errors=True)  # 允许列表没改成：文件也收回，不留半套
        raise
    names = agent_names()
    return L(f"加好了：{name}（给了{'、'.join(names.get(a, a) for a in targets)}，下一次对话起用上）",
             f"Added: {name} (for {', '.join(names.get(a, a) for a in targets)}; in use from the next conversation)")


async def create_agent(p: sqlite3.Row) -> str:
    spec = json.loads(p["content"])
    res = await asyncio.to_thread(data.create_group, data.GroupIn(name=spec["name"], purpose=spec["purpose"], icon=spec.get("icon") or "moon",
                                                                   color=spec.get("color"), model=settings.default_model, skills=spec.get("skills")))
    gid = res["id"]
    plan = spec.get("board") or {}
    blocks, problem = 0, ""
    try:  # Agent 已经建好了：看板没建成也算建好，结果里说一声
        for tb in plan.get("tables") or []:
            boards.add_coll(gid, boards.CollIn(name=str(tb.get("name") or ""), title=str(tb.get("title") or tb.get("name") or ""), fields=tb.get("fields") or []))
        if plan.get("blocks"):
            await boards.put_board(gid, boards.BoardIn(blocks=plan["blocks"], mode="apply",
                                                       note=str(plan.get("note") or L("按方案建好的看板", "The board from the plan"))))
            blocks = len(plan["blocks"])
    except (HTTPException, ValueError, KeyError) as e:
        problem = str(e.detail) if isinstance(e, HTTPException) else str(e)
    return (L(f"建好了：{spec['name']}（{gid}）", f"Created: {spec['name']} ({gid})")
            + (L(f"，看板 {blocks} 块", f", {blocks} board blocks") if blocks else "")
            + (L(f"；看板没建成：{problem}", f"; the board wasn't set up: {problem}") if problem else ""))


async def on_decided(it: dict, action: str) -> dict | None:
    """收件箱里 kind=skill / agent 的卡被点了。只接这里交的提案，别的照旧（返回 None）。"""
    with _lock, pdb() as conn:
        p = conn.execute("SELECT * FROM proposals WHERE inbox_id=?", (it["id"],)).fetchone()
    if p is None:
        return None
    ts = now_iso()
    if action in ("reject", "withdraw"):
        with _lock, pdb() as conn:
            conn.execute("UPDATE proposals SET status=?, note=?, decided_at=? WHERE id=?",
                         ("rejected" if action == "reject" else "withdrawn", it.get("note") or "", ts, p["id"]))
        return None
    if action != "approve":
        return None
    try:
        result = install_skill(p) if p["kind"] == "skill" else await create_agent(p)
    except (agents.ProvisionError, HTTPException, OSError, ValueError, KeyError) as e:
        msg = str(e.detail) if isinstance(e, HTTPException) else str(e)
        with _lock, pdb() as conn:
            conn.execute("UPDATE proposals SET status='failed', result=?, decided_at=? WHERE id=?", (msg, ts, p["id"]))
        return {"failed": L(f"没做成：{msg}", f"Didn't work: {msg}")}
    with _lock, pdb() as conn:
        conn.execute("UPDATE proposals SET status='installed', result=?, decided_at=? WHERE id=?", (result, ts, p["id"]))
    log_activity(result, "edit", actor=settings.app_name)
    return {"result": result}


def extra(iid: str) -> dict | None:
    """卡上多给 app 的：给谁、这周的原话、skill 全文 / Agent 的职责。inbox.item_json 调（放在条目的 skill / agent 字段）。"""
    with _lock, pdb() as conn:
        p = conn.execute("SELECT * FROM proposals WHERE inbox_id=?", (iid,)).fetchone()
    if p is None:
        return None
    out: dict = {"id": p["id"], "name": p["name"], "evidence": json.loads(p["evidence"] or "[]")}
    if p["kind"] == "skill":
        names = agent_names()
        fm = frontmatter(p["content"])
        out.update(description=str(fm.get("description") or ""), markdown=body_of(p["content"]),
                   agents=[{"id": a, "name": names.get(a, a)} for a in json.loads(p["agents"] or "[]")])
    else:
        spec = json.loads(p["content"])
        out.update(purpose=spec.get("purpose") or "", icon=spec.get("icon"), color=spec.get("color"))
    return out


inbox.HOOKS["skill"] = on_decided
inbox.HOOKS["agent"] = on_decided
inbox.EXTRAS["skill"] = extra
inbox.EXTRAS["agent"] = extra


# —— 给 main 回看的材料、提过的提案 ——————————————————————————————————————————

def row_json(r: sqlite3.Row, full: bool = False) -> dict:
    out = {"id": r["id"], "kind": r["kind"], "slug": r["slug"], "title": r["title"], "name": r["name"], "status": r["status"],
           "day": r["day"], "why": r["why"], "note": r["note"], "result": r["result"], "inboxId": r["inbox_id"],
           "createdAt": r["created_at"], "decidedAt": r["decided_at"]}
    if full:
        out.update(evidence=json.loads(r["evidence"] or "[]"), agents=json.loads(r["agents"] or "[]"), content=r["content"])
    return out


@router.get("/api/proposals")
def list_proposals(status: str | None = None, limit: int = 100):
    with _lock, pdb() as conn:
        sync_status(conn)
        q = "SELECT * FROM proposals" + (" WHERE status=?" if status else "") + " ORDER BY created_at DESC LIMIT ?"
        rows = conn.execute(q, ((status,) if status else ()) + (min(max(limit, 1), 500),)).fetchall()
    return {"ok": True, "items": [row_json(r) for r in rows]}


@router.get("/api/proposals/context")
def context(days: int = CONTEXT_DAYS):
    """main 回看用：这几天用户在各个对话里说的话（配上回复的开头）、现有的 skills 和谁能用、Agent、提过的提案、今天还能提几条。"""
    days = min(max(days, 1), 14)
    today = day_of(now_iso())
    first = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    since = day_bounds(first)[0]
    names = thread_names()
    with _lock, db() as conn:
        rows = conn.execute("""SELECT id, thread, role, text, ts, attachments FROM messages WHERE ts>=? AND role IN ('user','grava')
            ORDER BY thread, id""", (since,)).fetchall()
    by_day: dict[str, list[dict]] = {}
    for i, r in enumerate(rows):
        th = r["thread"]
        if r["role"] != "user" or (th.startswith("study-") and th.endswith("-gen")):
            continue
        reply = next((x for x in rows[i + 1:i + 6] if x["thread"] == th and x["role"] == "grava"), None)
        files = len(json.loads(r["attachments"])) if r["attachments"] else 0
        where = names.get(th) or (L("学习台", "Study desk") if th.startswith("study-") else th)
        by_day.setdefault(day_of(r["ts"]), []).append({"time": hhmm(r["ts"]), "ts": r["ts"], "thread": th, "where": where, "text": clip(r["text"], 300),
                                                       "files": files, "reply": clip(reply["text"], 120) if reply else ""})
    cfg = read_config()
    ids = list(agent_names())
    lists = {a: allowlist(cfg, a) for a in ids}
    skills = [{**s, "agents": [a for a in ids if lists[a] is None or s["dir"] in lists[a]]} for s in installed_skills()]
    with _lock, data.ddb() as conn:
        groups = conn.execute("SELECT id, name, purpose FROM groups ORDER BY position, created_at").fetchall()
    with _lock, pdb() as conn:
        sync_status(conn)
        past = conn.execute("SELECT * FROM proposals ORDER BY created_at DESC LIMIT 60").fetchall()
        used = conn.execute("SELECT COUNT(*) FROM proposals WHERE day=? AND status NOT IN ('withdrawn','failed')", (today,)).fetchone()[0]
    return {"ok": True, "today": today, "days": [{"day": d, "messages": sorted(v, key=lambda m: m["ts"])} for d, v in sorted(by_day.items())],
            "skills": skills, "agents": [{"id": "main", "name": settings.app_name, "purpose": L("主对话", "Main chat")},
                                         *[{"id": g["id"], "name": g["name"], "purpose": g["purpose"] or ""} for g in groups]],
            "proposals": [row_json(r) for r in past], "quota": {"max": MAX_PER_DAY, "used": used}}


@router.get("/api/proposals/{pid}")
def show(pid: str):
    with _lock, pdb() as conn:
        sync_status(conn)
        r = conn.execute("SELECT * FROM proposals WHERE id=? OR inbox_id=?", (pid, pid)).fetchone()
    if not r:
        raise HTTPException(404, L("没有这条提案", "No such proposal"))
    return {"ok": True, "item": row_json(r, full=True)}
