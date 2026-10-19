"""计划模式（内核 ⑫b，2026-10-16）：一个对话可以进计划模式：先只看不改，把计划交给用户点头，同意了再照着做。

在里面：只跑 read 的工具；undo / ask 的一律不跑（tools.gate 挡下），回「计划模式：先把计划交给用户」。todo、ask_user、propose_plan、
  enter_plan_mode 照常（不动用户的东西）。DeepSeek Harness 的计划模式只靠提示词、工具照样能用；这里在关卡上真挡（工具表不变，缓存不断）。
交计划：propose_plan（计划的 Markdown + 一个短标题）：计划作为这一轮的一段可见文字发出来，同时交一张收件箱卡（kind tool，tools.submit_card，
  和撤不回的调用同一种卡；卡上内容定死，被改过就不认）。点了以后（tools.on_decided 转到 decided）：
    同意    = 退出计划模式，往这个对话里发一句「已同意，照计划做」（带上计划），模型接着做（新的一轮）
    改一下  = 意见交回去（计划模式还开着：改好再交一次）
    拒绝    = 留在计划模式，那个对话今天的会话里补一句（不开新的一轮，等用户说）
  不在计划模式时也能用：大改动开工前想让用户先看一眼计划。
进出：POST /api/engine/plan_mode {"conversation", "on"}（以后 app 的开关）、GET 看现在开没开；server.json 的 engine.plan.agents.<id> = true
  给某个 Agent 默认开（"*" = 别的 Agent 的默认）；用户在对话里说「先给我计划」时模型自己进（enter_plan_mode，算 read）。
  状态跟着对话的当天（对话 + 逻辑日一行，04:00 换新，和会话一样：默认开的 Agent 第二天又从计划模式开始；当天关掉了就一直关到换天）。
  只认 app 的对话（定时任务、心跳、后台任务、对外接口那些 cron: / task: / api: … 的对话永远不在计划模式）。
模型怎么知道：开着的话会话开头的背景里写着这一节（engine/context.py 的 plan）；当天变了，下一句话后面补一条（context 的「更新了」：
  它自己进的、点同意关掉的不补，工具结果和同意那一句已经说过）。会话开头的规矩里写了什么时候用（owner.rules）。
参考（10/16 现拉的源码，只读）：DeepSeek Harness 1517a4db 的 packages/plan/plan-mode（exit_plan_mode 的说法；计划模式那一节规矩：先看、
  计划要完整到不用再拿主意、用户嘴上说好不算同意、交计划是这一条回复的最后一个调用）、Claude Code 的 plan mode / EnterPlanMode / ExitPlanMode
  （同意 = 退出计划模式，「接着改计划」把意见交回去）。OpenClaw 核心里没有计划模式。只借设计，没拿代码。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config import raw
from i18n import L, LS

from . import events, store
from .loop import Tool, ToolCtx, ToolFailure

router = APIRouter()

ENTER, PROPOSE = "enter_plan_mode", "propose_plan"
FREE = {ENTER, PROPOSE, "todo", "ask_user"}  # 计划模式里照常的（不动用户的东西）
PLAN_MAX = 20_000   # 计划最多多少字（卡上要给用户看全，同 handkit.CARD_MAX）
TITLE_MAX = 60
STEP = re.compile(r"(?:[-*+]|\d{1,3}[.)])\s+\S")  # 计划里的一条（卡上的 changes 列前 6 条）
RULES_ON = (
    "This conversation is in plan mode (the user turned it on, or asked for a plan first). Until the user approves a plan:\n"
    "- Only look, don't change: read-only tools work; anything that changes something comes back blocked by plan mode. A request to "
    "do something means: plan how to do it.\n"
    "- Look around first, then make the plan complete enough to carry out without new decisions: the goal, the steps in order (what "
    "changes where), what you still need from the user, risks, and how to check it worked.\n"
    "- Hand it in with propose_plan (the whole plan in Markdown, starting with a # title), as the last call of your reply; the user "
    "approves it, asks for changes or declines it on its card. The user saying OK in the chat doesn't approve anything: fold what they "
    "agreed into the plan and hand it in.\n"
    "- Ask the user (ask_user, when you have it) only about choices that are theirs to make; look up what you can find out.")
OFF_NOTE = "Plan mode is off now: you can make changes again (follow the plan the user approved, if there is one)."
_bg: set[asyncio.Task] = set()


# —— 状态（对话 + 逻辑日一行）————————————————————————————————————————————————————————

def _today() -> str:
    import chat
    return chat.day_of(chat.now_iso())


def app_conversation(conversation: str) -> bool:
    """app 的对话（main、Agent、项目、学习台……）：只有它们能进计划模式。cron: / heartbeat: / task: / api: / compact: … 不算。"""
    return bool(conversation) and ":" not in conversation and conversation != "-"


def default_on(conversation: str) -> bool:
    """server.json 的 engine.plan.agents：这个对话的 Agent 默认开不开（"*" = 别的 Agent）。"""
    e = raw().get("engine")
    p = e.get("plan") if isinstance(e, dict) else None
    agents = p.get("agents") if isinstance(p, dict) else None
    if not isinstance(agents, dict) or not app_conversation(conversation):
        return False
    from .owner import agent_of
    a = agent_of(conversation)
    v = agents[a] if a in agents else agents.get("*")
    return v is True


def state(conversation: str) -> dict:
    """→ {"on", "by": default / user / model / approved, "at"}。"""
    if not app_conversation(conversation):
        return {"on": False, "by": "default", "at": None}
    try:
        with store.read() as conn:
            r = conn.execute("SELECT active, by, at FROM engine_plan WHERE conversation=? AND day=?", (conversation, _today())).fetchone()
    except Exception:  # noqa: BLE001 — 读不了：照默认
        r = None
    if r is None:
        return {"on": default_on(conversation), "by": "default", "at": None}
    return {"on": bool(r["active"]), "by": r["by"], "at": r["at"]}


def on(conversation: str) -> bool:
    return state(conversation)["on"]


def set_mode(conversation: str, active: bool, by: str) -> bool:
    """开 / 关这个对话今天的计划模式。→ 变了没有。"""
    if not app_conversation(conversation):
        raise ValueError("only app conversations have a plan mode")
    before = on(conversation)
    with store.tx() as conn:
        conn.execute("""INSERT INTO engine_plan(conversation, day, active, by, at) VALUES(?,?,?,?,?)
            ON CONFLICT(conversation, day) DO UPDATE SET active=excluded.active, by=excluded.by, at=excluded.at""",
                     (conversation, _today(), int(bool(active)), by, store.now_iso()))
    events.publish(conversation, {"type": "plan_mode", "on": bool(active), "by": by})
    return before != bool(active)


def blocked(tool_name: str, conversation: str) -> str | None:
    """关卡用（tools.gate，只看 undo / ask 的）：计划模式开着、这个工具又不是 FREE 里的 → 回给模型的一句；不挡 → None。"""
    if tool_name in FREE or not on(conversation):
        return None
    return L("没跑：现在是计划模式，只看不改。先看完要看的，用 propose_plan 把完整的计划交给用户；他同意了才能做这一步。",
             "Not run: plan mode is on, so nothing gets changed yet. Look at what you need, then hand the complete plan to the user with "
             "propose_plan; you can do this once they approve it.")


def part(conversation: str):
    """会话开头的背景里那一节（engine/context.py）：开着就写规矩；关着是空的。它自己进的、点同意关掉的标 own（变了不用往后补）。"""
    from .context import Part
    st = state(conversation)
    return Part("plan", "Plan mode", RULES_ON if st["on"] else "", fp={"on": st["on"]}, own=st["by"] in ("model", "approved"))


# —— 工具 ————————————————————————————————————————————————————————————————————

def enter_tool() -> Tool:
    async def run(args: dict, ctx: ToolCtx) -> str:
        if not app_conversation(ctx.conversation):
            raise ToolFailure(L("这个对话不能进计划模式", "This conversation can't go into plan mode"))
        if on(ctx.conversation):
            return L("已经在计划模式里了。", "Plan mode is already on.")
        set_mode(ctx.conversation, True, "model")
        if ctx.emit is not None:
            ctx.emit({"type": "plan_mode", "on": True, "by": "model"})
        return L("计划模式开了。", "Plan mode is on.") + "\n\n" + RULES_ON

    return Tool(name=ENTER, kinds=("owner",), run=run, timeout=30, title=lambda a: L("进计划模式", "Enter plan mode"),
                classify=lambda args: ("read", "只是先不改东西、先出计划"),
                description=L(
                    "进计划模式：用户要你先给计划再动手（「先给我计划」「想好了再做」），或者活又大又难撤、你想先跟他对一下做法时调。进去以后只看不改，"
                    "想好了用 propose_plan 交计划，他同意了才退出计划模式接着做。",
                    "Enter plan mode: call it when the user wants a plan before you act (\"give me a plan first\", \"think it through "
                    "before doing it\"), or when a job is big and hard to undo and you want to agree on the approach first. In plan mode "
                    "you only look, then hand in a plan with propose_plan; plan mode ends when the user approves it."),
                schema={"type": "object", "required": [], "properties": {
                    "why": {"type": "string", "description": L("一句话：为什么先出计划", "One line: why plan first")}}})


def propose_tool() -> Tool:
    async def run(args: dict, ctx: ToolCtx) -> dict | str:
        from . import tools as tools_mod
        plan = str(args.get("plan") or "").strip()
        if not plan:
            raise ToolFailure(L("要写 plan：完整的计划（Markdown，# 标题开头）", "plan is required: the whole plan (Markdown, starting with a # title)"))
        if len(plan) > PLAN_MAX:
            raise ToolFailure(L(f"计划太长了（{len(plan)} 字，最多 {PLAN_MAX}）：卡上要给用户看全，写紧一点再交",
                                f"The plan is too long ({len(plan)} characters, at most {PLAN_MAX}): the card shows it in full, so tighten it"))
        title = " ".join(str(args.get("title") or "").split()) or next(
            (ln.strip("# ").strip() for ln in plan.splitlines() if ln.strip("# ").strip()), "") or L("计划", "Plan")
        title = title if len(title) <= TITLE_MAX else title[:TITLE_MAX - 1] + "…"
        import inbox
        who = ctx.agent or "main"
        name = inbox.source_name(who, inbox.names())
        steps = [ln.strip() for ln in plan.splitlines() if STEP.match(ln.strip())]  # 卡上列出来的：计划里的条目（- / * / 1.）
        fence = tools_mod._fence(plan)
        card = {"title": L(f"计划：{title}", f"Plan: {title}"),
                "why": str(args.get("why") or "").strip() or L(f"{name}想照这个计划做，先问你。", f"{name} wants to follow this plan and asks you first."),
                "changes": [s if len(s) <= 120 else s[:119] + "…" for s in steps[:6]] or [L("见下面的计划", "See the plan below")],
                "detail": L("同意后计划模式关掉，它照这个计划接着做；「改一下」把你的意见交回去，它改好再交；拒绝就留在计划模式。",
                            "If you approve, plan mode turns off and it carries out this plan; Revise sends your notes back for a new "
                            "plan; Decline keeps plan mode on.") + f"\n\n{fence}markdown\n{plan}\n{fence}",
                "approveLabel": L("照计划做", "Go ahead")}
        if ctx.emit is not None:  # 计划也作为这一轮的一段可见文字
            ctx.emit({"type": "say", "text": plan})
        dedupe = "plan:" + hashlib.sha256(json.dumps([who, ctx.conversation, plan], ensure_ascii=False).encode()).hexdigest()[:32]
        got = await tools_mod.submit_card(PROPOSE, json.dumps({"conversation": ctx.conversation, "title": title, "plan": plan}, ensure_ascii=False),
                                          None, card, dedupe, ctx, who=who)
        if not isinstance(got, dict):
            return got  # 没交成 / 拒过一模一样的：原话
        iid = got.get("inbox")
        got["message"] = L(f"计划已经交给用户（收件箱卡 {iid}，在你这条回复下面），也显示在回复里了。停在这里：用一句话告诉用户计划在等他点头，"
                           "然后结束这一轮。他同意之前别动手；要改会把意见发回来。",
                           f"The plan went to the user on an inbox card ({iid}, under your reply) and shows in your reply. Stop here: tell "
                           "the user in one line that the plan is waiting for their OK, and end this turn. Don't start before they "
                           "approve; if they want changes, their notes come back to you.")
        return got

    return Tool(name=PROPOSE, kinds=("owner",), run=run, timeout=60, title=lambda a: (L("交计划：", "Plan: ") + str(a.get("title") or ""))[:80],
                classify=lambda args: ("undo", "只交一张收件箱卡问用户，不动别的；撤：卡上不同意"),
                description=L(
                    "把计划交给用户点头：一张收件箱卡（同意 / 改一下 / 拒绝），计划也显示在你的回复里。计划模式里想好了就交（这是退出计划模式的路）；"
                    "不在计划模式时，大改动开工前想让他先过目也能用。plan 写完整的 Markdown（# 标题开头：目标、按顺序的步骤、要他定的、风险、怎么验证）；"
                    "这一条回复里最后调它，交完就结束这一轮，等他点。",
                    "Hand a plan to the user for their OK: an inbox card (approve / revise / decline), and the plan shows in your reply. "
                    "In plan mode, call it once the plan is ready (it's the way out of plan mode); outside plan mode, use it when you want "
                    "them to look over a big change before you start. plan is the whole plan in Markdown (a # title, then the goal, the "
                    "steps in order, what they need to decide, risks, how to check it worked). Make it the last call of your reply and end "
                    "the turn after it; wait for their decision."),
                schema={"type": "object", "required": ["plan"], "properties": {
                    "plan": {"type": "string", "description": L("完整的计划，Markdown，# 标题开头", "The whole plan, Markdown, starting with a # title")},
                    "title": {"type": "string", "description": L("卡片上的短标题（不写就用计划的 # 标题）", "A short title for the card (default: the plan's # title)")},
                    "why": {"type": "string", "description": L("可选：一句话说为什么这么做", "Optional: one line on why this approach")}}})


# —— 用户点了计划卡（tools.on_decided 转过来）—————————————————————————————————————————————

async def decided(row, it: dict, action: str) -> dict | None:
    """同意 / 改一下 / 拒绝 / 撤回计划卡（见文件开头）。返回给 inbox 的（同 tools.on_decided）。"""
    import inbox

    from . import tools as tools_mod
    try:
        args = json.loads(row["args"] or "{}")
    except ValueError:
        args = {}
    conv = str(args.get("conversation") or row["thread"] or "main")
    t, i = it["title"], it["id"]
    note = " ".join(str(it.get("note") or "").split())
    if action == "revise":
        tools_mod._set(row["id"], "revised")
        said = (LS(f"：{note.rstrip('。.')}。", f": {note.rstrip('.')}.") if note else
                LS("，没写改哪里：先问他想怎么改。", ", but didn't say what to change: ask them first."))
        inbox.kick(conv, inbox.MARK + LS(f"用户要改一下你的计划「{t}」（{i}）", f'The user wants changes to your plan "{t}" ({i})') + said + LS(
            "计划模式还开着：按他说的改好，再用 propose_plan 交一次。",
            " Plan mode is still on: revise the plan as they say and hand it in again with propose_plan."))
        return {"silent": True, "status": "done", "result": L("没照做：已经让它按你说的改计划", "Not carried out: asked it to revise the plan as you said")}
    if action in ("reject", "withdraw"):
        if row["status"] in ("pending", "asking"):
            tools_mod._set(row["id"], "rejected" if action == "reject" else "withdrawn")
        if action == "reject":  # 后台补（那个对话正在回话的话要等它说完，别让点拒绝的人等）
            task = asyncio.get_running_loop().create_task(note_session(conv, (
                f"(The user declined your plan \"{t}\" ({i})" + (f": {note}" if note else "") + ". Plan mode stays on: don't make "
                "changes; wait for what they say, and ask or propose a different plan if they want one.)")))
            _bg.add(task)
            task.add_done_callback(_bg.discard)
        return None
    if action != "approve":
        return None
    if row["card"] != tools_mod.card_hash(it["title"], it["why"], it["changes"], it["detail"]):
        why = L("这张卡交上来以后被改过，和交的计划对不上，没有照做", "This card was edited after it was sent, so it no longer matches the plan; nothing was done")
        inbox.kick(conv, inbox.MARK + LS(f"已同意「{t}」（{i}），但没照做：{why}。计划模式还开着；跟用户说一声。",
                                         f'Approved "{t}" ({i}), but it wasn\'t carried out: {why}. Plan mode is still on; tell the user.'))
        return {"failed": why}
    if not tools_mod._claim(row["id"]):
        return {"failed": L("这张计划卡已经处理过了", "This plan card was already handled")}
    if app_conversation(conv):
        set_mode(conv, False, "approved")
    plan = str(args.get("plan") or "")
    fence = tools_mod._fence(plan)
    tools_mod._set(row["id"], "done", L("计划模式关了，已经让它照计划做", "Plan mode is off; it's carrying out the plan"))
    inbox.kick(conv, inbox.MARK + LS(
        f"已同意你的计划「{t}」（{i}）" + (f"。用户补充：{note.rstrip('。.')}" if note else "") + "。计划模式已经关了：现在照这个计划做，"
        f"从第一步开始；多步的用 todo 记进度，做完跟用户说一声做了什么。同意的计划是：\n{fence}markdown\n{plan}\n{fence}",
        f'The user approved your plan "{t}" ({i})' + (f". Their note: {note}" if note else "") + ". Plan mode is off: carry out the plan "
        f"now, starting with the first step; track a multi-step job with todo, and tell the user what you did when it's done. The "
        f"approved plan:\n{fence}markdown\n{plan}\n{fence}"))
    return {"silent": True, "result": L("计划模式关了，已经让它照计划做", "Plan mode is off; it's carrying out the plan")}


async def note_session(conversation: str, text: str) -> None:
    """那个对话今天的会话里补一句 system（不开新的一轮；正在跑的那一轮先跑完）。今天还没有会话就不补（建会话时背景里会写计划模式）。"""
    import chat

    from .loop import wait_idle
    from .session import DbSession, day_key, system_note
    s = DbSession.latest(day_key(conversation, chat.day_of(chat.now_iso())))
    if s is None:
        return
    await wait_idle(s.id, 120)
    s.append(system_note(text))


# —— POST / GET /api/engine/plan_mode：以后 app 的开关用 ——————————————————————————————————————

class ModeIn(BaseModel):
    conversation: str
    on: bool


@router.get("/api/engine/plan_mode")
def get_mode(conversation: str):
    if not app_conversation(conversation):
        raise HTTPException(400, L("只有 app 里的对话有计划模式", "Only app conversations have a plan mode"))
    return {"ok": True, "conversation": conversation, **state(conversation), "default": default_on(conversation)}


@router.post("/api/engine/plan_mode")
def post_mode(body: ModeIn):
    """开 / 关这个对话今天的计划模式（用户自己开关的：下一句话后面补一条告诉模型）。正在跑的那一轮，下一个调用就按新的来。"""
    if not app_conversation(body.conversation):
        raise HTTPException(400, L("只有 app 里的对话有计划模式", "Only app conversations have a plan mode"))
    changed = set_mode(body.conversation, body.on, "user")
    try:
        import chat
        run = chat.RUNS.get(body.conversation)
        if run is not None and not run.done:
            run.publish(("plan_mode", {"on": body.on, "by": "user"}))
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True, "conversation": body.conversation, "on": body.on, "changed": changed}
