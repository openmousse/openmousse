"""想法「拿去讨论」（主线第三步第二段，2026-10-21，设计稿第 29–31 张）：Zen 里一条想法长按 →「拿去讨论」。

去哪（一样东西只有一个家：想法上记下它在哪讨论，Zen 里显示「讨论中」，点开就是那里，不复制）
- 和一位赛博（一对一）：在它的对话里说一句「聊聊这条想法」，想法的原文只当前情给它（chat.start_run 的 context，对话里不显示）；
  它照平时的规矩读你的名片、它记下的（健康、饮食、睡眠照旧不给它）。
- 新建群聊：来源 = 这条想法，议题 = 想法（groupchat.py）：先讨论一轮，小结写回这条想法（笔记里那一段）；群结束 = 这条想法标为已想完。
- 放进播客：放进一期的素材（没给哪一期就新建一期，标题是想法的开头）。
- 交给功能 Agent（要动手的事）：在它的对话里说一句（引着这条想法 + 你写的话），它照平时的规矩做：要你点头的照样出卡。
- 发给好友：只发要点，不发原话：先零工具地理成要点（POST …/points，算你自己的一问），app 给你看过、你点了才经 /api/friends/{id}/messages 发。
想法上记的（笔记的属性 talk = {kind: cyber | group | podcast | agent, id, at}；think.frag_json 的 discussion 给 app）。
结果回原处：群的小结写回想法（groupchat.write_back）；群结束时想法挪进「已想完」（groupchat.end 调 finish）。
开关：跟着赛博、群聊（都要自己的内核）。两样都关着 → 下面这些一律 404，app 据此不显示「拿去讨论」（默认关）。

接口
  GET  /api/think/ideas/{id}/discuss      {idea, discussion, cybers（最近聊过的在前）, agents, friends, episodes, can}
  POST /api/think/ideas/{id}/cyber        {cyber}
  POST /api/think/ideas/{id}/group        {members, host?, mode?, title?}
  POST /api/think/ideas/{id}/podcast      {episode?}
  POST /api/think/ideas/{id}/agent        {agent, note?}
  POST /api/think/ideas/{id}/points       → {points}
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import chat
from chat import _lock, log_activity, now_iso
from config import settings
from i18n import L, LS


def enabled() -> None:
    """赛博、群聊都关着（或者 claw 不是 builtin）：一律 404（见文件开头）。"""
    import cybers
    import groupchat
    if not (cybers.on() or groupchat.on()):
        raise HTTPException(404, L("这台服务器没开赛博和群聊（要用自己的内核）", "Cybers and group chats are off on this server (they need the built-in engine)"))


router = APIRouter(dependencies=[Depends(enabled)])
CYBERS_SHOWN = 6
IDEA_CONTEXT = 4000


def clip(s: str, n: int) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def idea(fid: str) -> dict:
    import think
    if not think.ID_RE.match(fid or ""):
        raise HTTPException(404, L("没有这条想法", "No such thought"))
    return think.frag_by_id(fid)


def idea_text(f: dict) -> str:
    text = (f.get("text") or "").strip()
    if f.get("url"):
        text = (text + "\n" + (f.get("linkTitle") or "") + " " + f["url"]).strip()
    return text or f.get("title") or ""


def mark(f: dict, kind: str, target: str) -> dict:
    """想法上记下它在哪讨论（一样东西只有一个家）。"""
    import think

    def change(meta: dict, body: str) -> str:
        meta["talk"] = {"kind": kind, "id": target, "at": now_iso()}
        return body
    return think.rewrite(f, change)


def discussion_of(f: dict) -> dict | None:
    """想法现在在哪讨论 → {kind, id, title, open}；没有 → None。"""
    talk = f.get("talk") or ({"kind": "group", "id": f["group"]} if f.get("group") else None)
    if not isinstance(talk, dict) or not talk.get("id"):
        return None
    kind, tid = str(talk.get("kind") or ""), str(talk["id"])
    title, open_ = "", True
    try:
        if kind == "group":
            import groupchat
            with _lock, groupchat.gdb() as conn:
                g = conn.execute("SELECT title, status FROM groupchats WHERE id=?", (tid,)).fetchone()
            if g is None:
                return None
            title, open_ = g["title"], g["status"] == "active"
        elif kind == "cyber":
            import cybers
            title = cybers.names().get(tid) or ""
        elif kind == "agent":
            import inbox
            title = inbox.names().get(tid) or tid
        elif kind == "podcast":
            import podcast
            r = podcast.load(tid)
            title, open_ = r["title"], r["status"] != "saved"
    except HTTPException:
        return None
    return {"kind": kind, "id": tid, "title": title, "open": open_}


@router.get("/api/think/ideas/{fid}/discuss")
async def options(fid: str):
    """「拿去讨论」那一张：现在在哪讨论、最近聊过的赛博、功能 Agent、好友、最近的几期，和这台服务器能去哪几处。"""
    import cybers
    import groupchat
    f = await asyncio.to_thread(idea, fid)
    out: dict = {"ok": True, "idea": {"id": f["id"], "text": clip(idea_text(f), 300), "title": f.get("title") or ""},
                 "discussion": await asyncio.to_thread(discussion_of, f), "cybers": [], "agents": [], "friends": [], "episodes": []}
    with _lock, cybers.cydb() as conn:
        if cybers.on():
            out["cybers"] = [{"id": c["id"], "name": c["name"], "tag": c["tag"], "avatar": c["avatar"], "last": (c.get("last") or {}).get("ts")}
                             for c in cybers.ordered(cybers.active_rows(conn), conn)][:CYBERS_SHOWN]
        out["agents"] = [cybers.agent_json(g) for g in cybers.group_rows(conn).values()]
    try:
        import social
        out["friends"] = [{"id": x["id"], "name": x.get("alias") or x.get("name") or ""} for x in social.friends()]
    except Exception as e:  # noqa: BLE001 — 好友那边读不了：这一张照样出
        print(f"[ideatalk] 读好友失败：{e}")
    try:
        import podcast
        with _lock, podcast.pdb() as conn:
            out["episodes"] = [{"id": r["id"], "title": r["title"], "mode": r["mode"]} for r in
                               conn.execute("SELECT id, title, mode FROM pod_episodes WHERE status IN ('prep','recording') ORDER BY updated_at DESC LIMIT 6")]
    except Exception as e:  # noqa: BLE001
        print(f"[ideatalk] 读播客失败：{e}")
    out["can"] = {"cyber": cybers.on(), "group": groupchat.on(), "podcast": True, "agent": bool(out["agents"]), "friend": bool(out["friends"])}
    return out


class CyberIn(BaseModel):
    cyber: str


@router.post("/api/think/ideas/{fid}/cyber")
async def to_cyber(fid: str, body: CyberIn):
    """和一位赛博一对一聊这条想法：它的对话里一句「聊聊这条想法」，原文只当前情给它。→ {thread}（app 打开那位赛博的对话）。"""
    import cybers
    cybers.enabled()
    f = await asyncio.to_thread(idea, fid)
    with _lock, cybers.cydb() as conn:
        r = cybers.get_row(conn, body.cyber)
    text = L(f"聊聊这条想法：「{clip(idea_text(f), 80)}」", f'Let\'s talk about this thought: "{clip(idea_text(f), 80)}"')
    context = LS(f"（这条想法的原文，只有这一条：）\n{idea_text(f)[:IDEA_CONTEXT]}", f"(The thought, in full; just this one:)\n{idea_text(f)[:IDEA_CONTEXT]}")
    run = chat.start_run(r["id"], text, None, context=context)
    await asyncio.to_thread(mark, f, "cyber", r["id"])
    log_activity(L(f"把一条想法拿去和「{r['name']}」聊", f'Took a thought to "{r["name"]}"'), "edit")
    return {"ok": True, "thread": r["id"], "messageId": f"db{run.user_id}" if run.user_id else None}


class GroupIn(BaseModel):
    members: list[str]
    host: str | None = None
    mode: str = "mention"
    title: str | None = None


@router.post("/api/think/ideas/{fid}/group")
async def to_group(fid: str, body: GroupIn):
    """新建群聊讨论这条想法：来源 = 想法、议题 = 想法，先讨论一轮；小结写回这条想法，群结束时它标为已想完。→ {group}。"""
    import groupchat
    groupchat.enabled()
    f = await asyncio.to_thread(idea, fid)
    text = idea_text(f)
    gid = groupchat.new_group(body.members, title=body.title or clip(f.get("title") or text, groupchat.TITLE_MAX), topic=text, host=groupchat.clean_host(body.host),
                              mode=body.mode, source={"kind": "idea", "id": f["id"], "title": clip(f.get("title") or text, 80)}, origin="idea")
    await asyncio.to_thread(mark, f, "group", gid)
    groupchat.start_round(gid)
    with _lock, groupchat.gdb() as conn:
        return {"ok": True, "group": groupchat.group_json(groupchat.get_row(conn, gid), groupchat.names(conn), conn)}


class PodcastIn(BaseModel):
    episode: str | None = None


@router.post("/api/think/ideas/{fid}/podcast")
async def to_podcast(fid: str, body: PodcastIn):
    """放进播客：放进那一期的素材（没给就新建一期，标题是想法的开头）。→ {episode}。"""
    import podcast
    import podmaterials
    f = await asyncio.to_thread(idea, fid)
    if body.episode:
        ep = podcast.episode_json(body.episode)
    else:
        ep = (await podcast.create(podcast.EpisodeIn(title=clip(f.get("title") or idea_text(f), 40), mode="host",
                                                     source={"kind": "idea", "label": clip(idea_text(f), 60)})))["episode"]
    m = await asyncio.to_thread(podmaterials.resolve, "idea", f["id"])
    podmaterials.add(ep["id"], m)
    await asyncio.to_thread(mark, f, "podcast", ep["id"])
    return {"ok": True, "episode": podcast.episode_json(ep["id"])}


class AgentIn(BaseModel):
    agent: str
    note: str = ""


@router.post("/api/think/ideas/{fid}/agent")
async def to_agent(fid: str, body: AgentIn):
    """交给功能 Agent（要动手的事）：在它的对话里说一句（引着这条想法 + 你写的话），它照平时的规矩做（要你点头的照样出卡）。→ {thread}。"""
    import cybers
    f = await asyncio.to_thread(idea, fid)
    with _lock, cybers.cydb() as conn:
        g = cybers.group_rows(conn).get(body.agent)
    if g is None:
        raise HTTPException(404, L("没有这个功能 Agent", "No such Agent"))
    note = clip(body.note, 1000)
    text = (note + "\n\n" if note else "") + L(f"（Zen 里的一条想法）「{clip(idea_text(f), 300)}」", f'(A thought from Zen) "{clip(idea_text(f), 300)}"')
    run = chat.start_run(body.agent, text, None)
    await asyncio.to_thread(mark, f, "agent", body.agent)
    return {"ok": True, "thread": body.agent, "messageId": f"db{run.user_id}" if run.user_id else None}


POINTS_RULES = (
    "把 INPUT_JSON 的 text（{user}自己记下的一条想法）理成发给朋友看的要点：2–4 条，每条一句，用{user}的口吻；只写要点，不照抄原话，"
    "不带名字、住址、联系方式这类私事。只回一个 JSON：{{\"points\": [\"…\"]}}",
    "Turn INPUT_JSON's text (a thought {user} jotted down) into key points to send a friend: 2–4, one sentence each, in {user}'s voice; key "
    "points only, not the original wording, and no private details like names, addresses or contact details. Reply with ONE JSON value: "
    "{{\"points\": [\"…\"]}}")


@router.post("/api/think/ideas/{fid}/points")
async def points(fid: str):
    """发给好友之前：零工具地理成要点（算你自己的一问：是你记的想法；来路是这个请求的 REQUEST_VIA，app 里点的 = user）。
    engine.use.json 是你自己的套餐时，app 里点的走套餐；经 /mcp 来的照 may_spend 不花，换成不花套餐的。没配 engine.use.json 用不花套餐的
    默认模型（同 inbox.ask_reviser）。想法可能是收藏的别人的文章：只走内核的零工具一问，不退回带工具的对话回合（这里只在自己的内核上开）。
    app 给你看，你改过、点了才发（/api/friends/{id}/messages）。"""
    import engine
    import llmjson
    from engine import models
    f = await asyncio.to_thread(idea, fid)
    user = (settings.user_name or "").strip() or LS("用户", "the user")
    origin = chat.REQUEST_VIA.get() or "user"
    ref = engine.route("json", None, kind="owner", origin=origin) or models.outside(None)
    if not ref:
        raise HTTPException(502, L("没理出要点：没有能用的模型", "Couldn't make the key points: no model to use"))
    try:
        out, _via = await engine.ask_json(LS(*POINTS_RULES).format(user=user), {"text": idea_text(f)[:IDEA_CONTEXT]}, model=ref, purpose="json",
                                          required=("points",), timeout=45, kind="owner", origin=origin)
    except engine.EngineError as e:
        why = llmjson.plan_failed(ref, e) if e.kind != "refusal" and models.subscription(ref) else str(e)[:160]
        raise HTTPException(502, L(f"没理出要点：{why}", f"Couldn't make the key points: {why}")) from e
    items = [clip(str(x), 200) for x in (out or {}).get("points") or [] if clip(str(x), 200)][:4] if isinstance(out, dict) else []
    if not items:
        raise HTTPException(502, L("没理出要点", "Couldn't make the key points"))
    return {"ok": True, "points": items, "text": "\n".join(f"· {p}" for p in items)}


def finish(fid: str) -> None:
    """群结束了（groupchat.end）：这条想法讨论完了，挪进「已想完」（已经想完、删了的不动）。"""
    import think
    if not think.ID_RE.match(fid or ""):
        return
    try:
        f = think.frag_by_id(fid)
    except HTTPException:
        return
    if f["status"] != "done":
        think.finish_fragment(f)

