"""改手册（内核第 13c 项，2026-10-19）：手册是做法（engine/memgate.py：先提议，点了头才改，改之前留版本）。Agent 想新写、改、停用、
删、换回一份手册，用 manual_manage：过闸（memgate.decide("procedure", …)）分到 ask，交一张 kind tool 的卡（engine/calls.py，前缀 manual），
用户同意了服务器照卡改一次（世界树的 openmousse_tree.manuals：改之前整份留一版，mousse-tree manual history / restore 找得回来），
闸的结果和点了头的那一下都记进事件流（memgate.record / approved）。和 skill_manage（engine/skill_write.py）一个套路：
engine.tools.levels 写 {"manual_manage": "auto"}（或某个动作 "manual_manage edit"）才直接改。

动作：add（name、when、about、body）/ edit（name，要改的那几样：when、about、body、rename）/ disable / enable / rm /
  restore（name、version）/ history（只读）
别的路：tree 工具（命令行的桥，内核和 /mcp 都走它）里的手册只读，写、改、删挡在 bridges.guard；世界树给各平台的 MCP 只有 find_manual、
  read_manual；用户自己在命令行（mousse-tree manual …）、Obsidian 里改的照旧直接写（那是用户本人）。
只给主人回合；世界树没有手册（不是开源版、包太旧）或者 engine.manuals = false：没有这个工具。
"""
from __future__ import annotations

import asyncio
import contextlib
import re

from i18n import L

from . import calls, memgate
from .loop import ToolCtx, ToolFailure

TOOL_NAME = "manual_manage"
PREFIX = "manual"  # engine_calls.tool = manual:<名字>（engine/calls.py 按它找回 _approved）
ACTIONS = ("add", "edit", "disable", "enable", "rm", "restore", "history")
OP = {"add": "add", "edit": "edit", "disable": "edit", "enable": "edit", "restore": "edit", "rm": "forget"}  # 过闸时算什么动作
WORDS = {"add": ("新写", "add"), "edit": ("改", "edit"), "disable": ("停用", "disable"), "enable": ("换回", "re-enable"), "rm": ("删", "remove"),
         "restore": ("换回一版", "restore")}
BODY_SHOW = 3000  # 卡上的细节里最多放多少字正文


def _policy():
    from .tools import Rule, r, u
    return {
        "history": r("看存过的版本"),
        "add": u("新写一份；撤：rm（删之前整份留一版，能 restore）"),
        "edit": u("改之前整份留一版；撤：restore <版本>"),
        "disable": u("只停用不删，留一版；撤：enable"),
        "enable": u("换回；撤：disable"),
        "rm": u("删之前整份留一版；撤：restore <版本>"),
        "restore": u("换回之前也先存一版；撤：再 restore 回去"),
    }, Rule


def _tree():
    from . import manuals
    return manuals.tree()


def _when(v) -> list[str] | None:
    from . import manuals
    return None if v is None else manuals._phrases(v)


def _prepare(args):
    """when 写成一串字的（「改 SOP、准备推荐信材料」）也认：拆成几句。"""
    if isinstance(args, dict) and isinstance(args.get("when"), str):
        return {**args, "when": _when(args["when"])}
    return args


def _schema() -> dict:
    return {"type": "object", "required": ["action", "name"], "properties": {
        "action": {"type": "string", "enum": list(ACTIONS), "description": L(
            "add 新写；edit 改（只改给了的那几样）；disable 停用 / enable 换回；rm 删（留一版）；restore 换回存过的一版；history 看存过的版本",
            "add; edit (only what you pass); disable / enable; rm (a copy is kept); restore (a saved version); history (saved versions)")},
        "name": {"type": "string", "description": L("手册的名字（用户平时会说的话）", "The manual's name (in the user's everyday words)")},
        "when": {"type": "array", "items": {"type": "string"}, "description": L(
            "add / edit：什么时候用，两到五句用户平时会说的话", "add / edit: when to use it, two to five things the user would actually say")},
        "about": {"type": "string", "description": L("add / edit：一行说明", "add / edit: a one-line description")},
        "body": {"type": "string", "description": L("add / edit：正文（做法，Markdown）；edit 不写 = 正文不改",
                                                    "add / edit: the body (the procedure, Markdown); leave it out in edit to keep the body")},
        "rename": {"type": "string", "description": L("edit：新名字", "edit: a new name")},
        "version": {"type": "string", "description": L("restore：哪一版（history 里的）", "restore: which version (from history)")}}}


def _description() -> str:
    return L(
        "改手册（用户自己写的做法，各个平台共用）：新写 add、改 edit（只改给了的那几样）、停用 disable / 换回 enable、删 rm、换回存过的一版 restore、"
        "看存过的版本 history。手册是做事的办法：改动都先变成一张卡给用户点头（调用马上返回 approval-pending），他同意了服务器照卡改一次，"
        "改之前整份留一版、能撤。照手册做事时发现缺步骤、写错了，就用 edit 提议改；名字和 when 用用户平时会说的话。",
        "Change the user's manuals (their own written procedures, shared by every platform): add one, edit (only what you pass), disable / "
        "enable, rm, restore a saved version, or see history. Manuals are how things get done, so every change becomes an approval card for "
        "the user first (the call returns approval-pending at once); once they approve, the server makes exactly that change, keeping the "
        "previous version so it can be undone. When a manual you followed was missing steps or wrong, propose an edit; write its name and "
        "when-to-use phrases in the words the user would say.")


def _action(args: dict) -> str:
    return str(args.get("action") or "").strip().lower()


def check(args: dict) -> dict | None:
    """交卡之前先看一眼这一次改得成改不成（不写）：动作、名字、要改的那一份在不在、改了没有。→ 现在的那一份（add 是 None）。不行抛 ToolFailure。"""
    action, name = _action(args), " ".join(str(args.get("name") or "").split())
    if action not in ACTIONS:
        raise ToolFailure(L(f"action 只能是 {' / '.join(ACTIONS)}", f"action must be one of {', '.join(ACTIONS)}"))
    if not name:
        raise ToolFailure(L("要写 name：哪一份手册", "name is required: which manual"))
    mt = _tree()
    if mt is None:
        raise ToolFailure(L("这棵世界树没有手册", "This memory tree has no manuals"))
    MA = mt.manuals()
    with contextlib.closing(mt.connect(sync=False)) as conn:
        cur = MA.get(conn, name)
    if action == "add":
        if cur is not None:
            raise ToolFailure(L(f"已经有「{cur['name']}」这份手册了：改它用 edit", f'There\'s already a manual called "{cur["name"]}": use edit'))
        try:
            MA.clean_name(name)
        except MA.ManualError as e:
            raise ToolFailure(str(e)) from None
        if not str(args.get("body") or "").strip():
            raise ToolFailure(L("add 要写 body：做法的正文", "add needs body: the procedure"))
        return None
    if cur is None and action != "restore":
        raise ToolFailure(MA.not_found(name))
    if action == "edit" and not any(args.get(k) not in (None, "", []) for k in ("when", "about", "body", "rename")):
        raise ToolFailure(L("edit 要写要改的那几样：when、about、body、rename", "edit needs something to change: when, about, body or rename"))
    if action == "restore" and str(args.get("version") or "") not in {v["id"] for v in MA.history(name)}:
        raise ToolFailure(L(f"「{name}」没有这一版：先 history 看看", f'No such version of "{name}": see history first'))
    return cur


def apply(args: dict, actor: str) -> str:
    """照这一次的参数改一次（点了头的卡、或者设成直接改的）→ 给对话看的一句（存了哪一版、怎么撤）。不行抛 ToolFailure。"""
    action, name = _action(args), " ".join(str(args.get("name") or "").split())
    mt = _tree()
    if mt is None:
        raise ToolFailure(L("这棵世界树没有手册", "This memory tree has no manuals"))
    MA = mt.manuals()
    try:
        with contextlib.closing(mt.connect(sync=False)) as conn:
            if action == "history":
                vs = MA.history(name)
                return "\n".join(f"- {v['id']}: {v.get('note') or ''} {v.get('at') or ''} ({v.get('by') or ''})" for v in vs) or \
                    L(f"「{name}」还没有存过的版本", f'"{name}" has no saved versions yet')
            if action == "add":
                m = MA.add(conn, name=name, when=_when(args.get("when")) or [], about=str(args.get("about") or ""), body=str(args.get("body") or ""),
                           actor=actor)
                return L(f"写好了手册「{m['name']}」。撤：rm（删之前留一版）", f'Wrote the manual "{m["name"]}". To undo: rm (a copy is kept)')
            if action == "edit":
                r = MA.edit(conn, name, when=_when(args.get("when")), about=args.get("about"), body=args.get("body"), rename=args.get("rename") or None,
                            actor=actor)
            elif action in ("disable", "enable"):
                r = MA.set_status(conn, name, "disabled" if action == "disable" else "active", actor=actor)
            elif action == "rm":
                vid = MA.remove(conn, name, actor=actor)
                return L(f"删了手册「{name}」（存成 {vid}；restore 找得回来）", f'Removed the manual "{name}" (kept as {vid}; restore brings it back)')
            elif action == "restore":
                r = MA.restore(conn, name, str(args.get("version") or ""), actor=actor)
            else:
                raise ToolFailure(L(f"action 只能是 {' / '.join(ACTIONS)}", f"action must be one of {', '.join(ACTIONS)}"))
    except MA.ManualError as e:
        raise ToolFailure(str(e)) from None
    kept = r.get("version")
    return L(f"{WORDS[action][0]}好了手册「{r['manual']['name']}」" + (f"（改之前存成 {kept}；restore 找得回来）" if kept else ""),
             f'Did "{WORDS[action][1]}" on the manual "{r["manual"]["name"]}"' + (f" (kept {kept} first; restore brings it back)" if kept else ""))


def _card(args: dict, cur: dict | None, who: str) -> dict:
    action, name = _action(args), " ".join(str(args.get("name") or "").split())
    zh, en = WORDS[action]
    sep = L("、", "; ")
    changes = [L(f"动作：{zh}", f"Action: {en}"), L(f"手册：{name}", f"Manual: {name}")]
    if args.get("rename"):
        changes.append(L(f"名字：{name} → {args['rename']}", f"Name: {name} → {args['rename']}"))
    if args.get("when") is not None and action in ("add", "edit"):
        old = sep.join(cur["when"]) if cur else ""
        changes.append(L(f"什么时候用：{old or '（没写）'} → {sep.join(_when(args['when']) or [])}",
                         f"When to use: {old or '(none)'} → {sep.join(_when(args['when']) or [])}"))
    if args.get("about") is not None and action in ("add", "edit"):
        changes.append(L(f"说明：{(cur or {}).get('about') or '（没写）'} → {args['about']}", f"Description: {(cur or {}).get('about') or '(none)'} → {args['about']}"))
    if args.get("body") is not None and action in ("add", "edit"):
        n_old, n_new = len((cur or {}).get("body") or ""), len(str(args["body"]).strip())
        changes.append(L(f"正文：{n_old} 字 → {n_new} 字", f"Body: {n_old} → {n_new} characters") if cur else L(f"正文：{n_new} 字", f"Body: {n_new} characters"))
    if action == "restore":
        changes.append(L(f"换回：{args.get('version')}", f"Back to: {args.get('version')}"))
    detail = L("同意后，服务器照上面改一次（改之前整份留一版，mousse-tree manual history / restore 找得回来），结果发回对话。",
               "If you approve, the server makes exactly this change once (keeping the previous version: mousse-tree manual history / "
               "restore brings it back) and posts the result to the chat.")
    if args.get("body") and action in ("add", "edit"):
        body = str(args["body"]).strip()
        body = body if len(body) <= BODY_SHOW else body[:BODY_SHOW] + "…"
        fence = "`" * max(3, max((len(m) for m in re.findall(r"`+", body)), default=0) + 1)
        detail += "\n\n" + L("新的正文：", "The new body:") + f"\n{fence}markdown\n{body}\n{fence}"
    return {"title": L(f"改手册：{name}（{zh}）", f"Change the manual {name} ({en})"),
            "why": L(f"{who}想{zh}手册「{name}」。手册是做事的办法，各个平台共用，改之前先问你。",
                     f'{who} wants to {en} the manual "{name}". Manuals are how things get done and every platform shares them, so changes '
                     "ask you first."),
            "changes": changes, "detail": detail, "approveLabel": L("照这样改", "Change it")}


async def propose(args: dict, ctx: ToolCtx) -> dict | str:
    """交一张改手册的卡（kind tool）：同意了照原样改一次；闸的结果记进事件流。→ approval-pending，或者没交成的原因（一句，关卡当结果回给模型）。"""
    import inbox
    try:
        cur = await asyncio.to_thread(check, args)
    except ToolFailure as e:
        return L(f"没交卡：{e}", f"No card was made: {e}")
    name = " ".join(str(args.get("name") or "").split())
    who = inbox.source_name(ctx.agent or "main", inbox.names())
    d = memgate.decide("procedure", OP.get(_action(args), "edit"), turn="owner" if ctx.turn else "background")
    if d.action == "deny":
        memgate.record(d, target=f"manual:{name}", author=ctx.agent or "main", conversation=ctx.conversation, turn_id=ctx.turn,
                       agent=ctx.agent or "main", ok=False)
        return L(f"没改：{d.why}", f"Not done: {d.why}")
    res = await calls.ask(PREFIX, name, dict(args), ctx, _card(args, cur, who))
    memgate.record(d, target=f"manual:{name}", author=ctx.agent or "main", conversation=ctx.conversation, turn_id=ctx.turn, agent=ctx.agent or "main",
                   result=str(res.get("inbox") or "") if isinstance(res, dict) else str(res)[:200], ok=isinstance(res, dict))
    return res


def tool(me: str):
    """这个 Agent 的 manual_manage（世界树没有手册、engine.manuals = false 的不给）。"""
    from . import manuals
    from . import tools as tools_mod
    if manuals.cfg() is None or _tree() is None:
        return None
    table, rule_cls = _policy()

    def level(args: dict) -> tuple[str, str]:
        action = _action(args)
        rule = table.get(action) or rule_cls(tools_mod.ASK, "不认识的动作：先问用户", "", "")
        if rule.level != tools_mod.READ:  # 手册是做法：一律先提议、点了头才改（engine/memgate.py）；engine.tools.levels 写 auto 照旧直接改
            d = memgate.decide("procedure", OP.get(action, "edit"))
            rule = rule_cls(d.level, f"{d.why}；{rule.why}", "", "")
        return tools_mod._level(f"{TOOL_NAME} {action}", rule, tools_mod.levels()), rule.why

    async def run(args: dict, ctx: ToolCtx) -> str:
        from . import jobs
        if ctx.kind != "owner":
            raise ToolFailure("Not run: manuals are only changed in the owner's own turns.")
        if _action(args) != "history" and (jobs.in_job_turn(ctx) or ctx.conversation.startswith("task:")):
            raise ToolFailure(L("没改：这一轮是定时任务 / 心跳 / 后台任务自己在跑，没人在看，这里不改手册（要的话等用户在对话里说）。",
                                "Not done: this turn is a scheduled job, heartbeat or background task running with nobody watching; manuals "
                                "aren't changed here (wait for the user to ask in the chat)."))
        if _action(args) != "history":
            await asyncio.to_thread(check, args)
        out = await asyncio.to_thread(apply, dict(args), L("你 · 设成直接改", "You · set to change directly"))
        if _action(args) != "history":  # 设成直接改的（engine.tools.levels）：照样记一笔
            memgate.record(memgate.Decision("write", "procedure", OP.get(_action(args), "edit"), "owner", False,
                                            L("设置里定了直接改", "the settings say to change it directly")),
                           target=f"manual:{args.get('name')}", author=ctx.agent or "main", conversation=ctx.conversation, turn_id=ctx.turn,
                           agent=ctx.agent or "main", result=out[:200])
        return out

    async def ask_user(args: dict, ctx: ToolCtx) -> dict | str:
        """要点头的（默认就是：手册是做法，先提议）：交一张 kind tool 的卡，同意后照原样改一次（改之前照样存一份）。"""
        return await propose(args, ctx)

    return calls.AskTool(name=TOOL_NAME, description=_description(), schema=_schema(), run=run, kinds=("owner",), timeout=30,
                         title=lambda args: f"manual {args.get('action') or ''} {args.get('name') or ''}".strip(), classify=level,
                         prepare=_prepare, ask_user=ask_user)


async def _approved(name: str, args: dict, row) -> str:
    out = await asyncio.to_thread(apply, dict(args), L("你 · 点头改手册", "You · approved a manual change"))
    memgate.approved("procedure", OP.get(_action(args), "edit"), target=f"manual:{name}", author=row["agent"], conversation=row["thread"],
                     agent=row["agent"], result=str(row["inbox"] or ""))
    return out


calls.register(PREFIX, _approved)
