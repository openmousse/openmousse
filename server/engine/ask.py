"""中途问用户（内核 ⑫b，2026-10-16）：ask_user 工具。一轮做到一半要用户拿主意（选哪个、缺一个信息、一件拿不准的事）时问一句，
等他回答，回答作为这个工具的结果回给模型，这一轮接着做。

给谁：只给主人回合里有人在看的那几轮：app 里用户自己说的那一轮（tools.chat_tools 的 watched：owner.respond / claude_brain.respond，
  chat.py 的 Run.watched）。定时任务、心跳、后台任务的子 Agent、交回结果开的那一轮、对外接口（⑭）、赛博、外人回合都没有
  （Hermes：子 Agent、cron、API server、ACP 不给 clarify；OpenClaw：只给主会话；DeepSeek Harness 只挂在 Web 的预设里）。
  子 Agent 要问，把问题写进结果交回去（tasks.child_rules 本来就这么说）。
app 还没有专门的界面，所以不改 app 也能用：
  - 问题当成这一轮的一段可见文字流出去（{"type": "say"}：owner.respond 单独成段；带选项的编上号，最后一句「回个数字、选项的字，
    或者直接说」，照 Hermes / OpenClaw 网关的纯文字版），同时发 {"type": "ask_user", "id", "question", "options", "free", "timeout"}
    （chat.py 推成 SSE 的 ask_user 事件，以后 app 画选项按钮用；重连时补发）。等完发 {"type": "ask_user_done", "id", "status"}。
  - 然后这一轮等着：用户在这一轮还在跑时发的下一句（chat.steer_engine 交进来的插话，Control.steer said=True）就是回答，不进插话队列；
    POST /api/engine/answer（以后 app 的按钮用）也行。两条路只算先到的那一次（Control.answer 只收一次）。
  - 回答 → 这个工具的结果：数字、和选项一样的字认成那个选项（Hermes 的 _coerce_text_response、OpenClaw 的 user-input-bridge）。
    发 {"type": "answer", "ref", "message"}：chat.py 把那条消息的连接接到这一轮上（和插话一样），claude_brain 把它记进会话。
    工具结果带 ref（app 里那条消息的 id）：撤回、重启以后认得出这一句已经用掉了（owner.rewind / refs）。
  - 不算回答的：不许自由回答、又没对上选项的话（Hermes 网关：选项卡上来了别的话 = 取消，照常交给 Agent）照常当插话；带附件的回话
    （OpenClaw：带图的回话取消问题、照常走）问题收起来，这一句照常排成下一轮（chat.send）。
  - 等不到（server.json 的 engine.ask_user.timeout 秒，默认 300）：回「用户还没回答：在回复里问出来、结束这一轮」。点停照常停（工具收到取消）。
Claude Code 跑的主人回合（engine/claude_brain.py，它自己的 AskUserQuestion 是关掉的）：经 engine/mcp_engine.py 拿到的就是这个工具，
  等法相同（它那边一次 MCP 调用的上限跟着这里的 timeout 放宽，claude_brain.child_env）。
参考（10/16 现拉的源码，只读）：Hermes 17c691fd 的 tools/clarify_tool.py、tools/clarify_gateway.py（网关里怎么等、数字和选项字怎么认、
  纯文字的样子、超时）、gateway/run_inbound.py（等着时进来的话先交给 clarify）、cron/scheduler.py、tools/delegate_tool_toolsets.py（谁没有）；
  OpenClaw a20b5882 的 src/agents/tools/ask-user-tool.ts、src/agents/harness/user-input-bridge.ts（选项 2–4 个、一个会话一次只问一个、
  带图的回话、no_answer）；DeepSeek Harness 1517a4db 的 packages/interaction/tool-ask-user（超时以后的说法）；Claude Code 的 AskUserQuestion
  （推荐的放第一个）。只借设计，没拿代码。
"""
from __future__ import annotations

import asyncio
import json
import re
import uuid
from dataclasses import dataclass

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config import raw
from i18n import L

from . import events
from .loop import Control, Tool, ToolCtx, ToolFailure, ToolOutput
from .session import text_of

router = APIRouter()

TOOL = "ask_user"
TIMEOUT = 300           # 默认等多久（秒）
TIMEOUT_MAX = 3600
OPTIONS_MAX = 4         # Hermes 的 MAX_CHOICES、Claude Code / OpenClaw 的 2–4 个
OPTION_MAX = 200
QUESTION_MAX = 4000
CANCEL = "cancel"       # 交给 Control.answer 的「不算回答」（带附件的回话）


@dataclass
class Pending:
    id: str
    conversation: str
    turn: str
    question: str
    options: list[str]
    free: bool
    control: Control


_open: dict[str, Pending] = {}   # 问题 id → 正在等回答的


def timeout() -> int:
    e = raw().get("engine")
    c = e.get("ask_user") if isinstance(e, dict) else None
    v = c.get("timeout") if isinstance(c, dict) else None
    return min(TIMEOUT_MAX, v) if isinstance(v, int) and not isinstance(v, bool) and v > 0 else TIMEOUT


def waiting(conversation: str) -> Pending | None:
    """这个对话正在等回答的那个问题。"""
    return next((p for p in _open.values() if p.conversation == conversation and p.control.asking()), None)


def shown(question: str, options: list[str], free: bool) -> str:
    """给用户看的那一段（问题 + 编号的选项 + 怎么回）。"""
    if not options:
        return question
    rows = "\n".join(f"{i}. {o}" for i, o in enumerate(options, 1))
    hint = (L("回个数字、选项的字，或者直接说你的想法。", "Reply with the number, the option text, or your own answer.") if free else
            L("回个数字或者选项的字。", "Reply with the number or the option text."))
    return f"{question}\n\n{rows}\n\n{hint}"


def match(text: str, options: list[str]) -> int | None:
    """回的是第几个选项（从 1 数）：数字（「2」「2.」「选 2」「option 2」）、和选项一样的字（不分大小写）。都不是 → None。"""
    t = " ".join(str(text or "").split()).strip()
    if not t or not options:
        return None
    m = re.fullmatch(r"(?:选|选择|option|#)?\s*(\d{1,2})\s*[.)。、]?", t, re.I)
    if m and 1 <= int(m.group(1)) <= len(options):
        return int(m.group(1))
    low = t.rstrip("。.!！").casefold()
    for i, o in enumerate(options, 1):
        if low == o.rstrip("。.!！").casefold() or low == o.replace("(Recommended)", "").strip().casefold():
            return i
    return None


def clean_options(raw_opts) -> list[str]:
    out: list[str] = []
    for o in raw_opts if isinstance(raw_opts, list) else []:
        s = " ".join(str(o.get("label") if isinstance(o, dict) else o or "").split())[:OPTION_MAX]
        if s and s not in out:
            out.append(s)
    return out[:OPTIONS_MAX]


def tool() -> Tool:
    secs = timeout()

    async def run(args: dict, ctx: ToolCtx) -> dict | ToolOutput:
        question = str(args.get("question") or "").strip()[:QUESTION_MAX]
        if not question:
            raise ToolFailure(L("要写 question：问用户的那一句", "question is required: what to ask the user"))
        options = clean_options(args.get("options"))
        free = not options or args.get("allow_free_text") is not False
        if ctx.control.asking():
            raise ToolFailure(L("已经有一个问题在等回答了：等它答了再问下一个", "A question is already waiting for an answer: ask the next one after it"))
        emit = ctx.emit or (lambda ev: None)
        qid = "q_" + uuid.uuid4().hex[:12]
        fut = ctx.control.expect_answer()
        _open[qid] = Pending(qid, ctx.conversation, ctx.turn, question, options, free, ctx.control)
        ev = {"type": "ask_user", "id": qid, "question": question, "options": options, "free": free, "timeout": secs}
        status = "stopped"
        try:
            emit({"type": "say", "text": shown(question, options, free)})
            emit(ev)
            events.publish(ctx.conversation, {**ev, "turn": ctx.turn})
            try:
                got = await asyncio.wait_for(fut, secs)
            except asyncio.TimeoutError:
                status = "timeout"
                return {"status": "no_answer", "message": L(
                    f"用户 {secs // 60 or 1} 分钟没回答。在回复里把这个问题问出来，然后结束这一轮；他下一句话就是回答。别当成他同意了。",
                    f"The user hasn't answered in {secs // 60 or 1} minute{'s' if secs >= 120 else ''}. Put the question in your reply and "
                    "end this turn; their next message will be the answer. Don't take the silence as a yes.")}
            if got.get(CANCEL):
                status = "other"
                return {"status": "not_answered", "message": L(
                    "用户没回答，发来了别的（带附件的一条，排在这一轮后面）。简短说一句就结束这一轮，下一轮你就看到他发的。",
                    "The user didn't answer: they sent something else (a message with attachments, queued right after this turn). End this "
                    "turn with a short line; you'll see what they sent next.")}
            text = text_of(got).strip()
            choice = match(text, options)
            if choice is None and options and not free:
                status = "other"
                ctx.control.steer_back(got)  # 不算回答：这句话照常当插话（这一批工具做完就看到）
                return {"status": "not_answered", "message": L(
                    "用户没选这几个选项，说了别的：他的话紧接着就到，照他说的接着来。", "The user didn't pick one of the options; what they "
                    "said comes next as a normal message: read it and go on from there.")}
            status = "answered"
            emit({"type": "answer", "id": qid, "ref": got.get("ref"), "message": got, "choice": choice})
            out: dict = {"status": "answered", "question": question, "answer": text or L("（空的回复）", "(an empty reply)")}
            if choice is not None:
                out.update(choice=choice, option=options[choice - 1])
            out["message"] = L("用户回答了你的问题。照他的回答接着做。", "The user answered your question. Carry on with their answer in mind.")
            return ToolOutput(json.dumps(out, ensure_ascii=False), extra={"ref": got["ref"]}) if isinstance(got.get("ref"), int) else out
        finally:
            ctx.control.stop_expecting()
            _open.pop(qid, None)
            done = {"type": "ask_user_done", "id": qid, "status": status}
            emit(done)
            events.publish(ctx.conversation, {**done, "turn": ctx.turn})

    return Tool(name=TOOL, kinds=("owner",), run=run, timeout=secs + 30,
                title=lambda a: (L("问你：", "Asking you: ") + " ".join(str(a.get("question") or "").split()))[:80],
                classify=lambda args: ("read", "只问用户一句、等他回答，不动别的"),
                description=L(
                    f"问用户一个问题，等他回答（最多等 {secs // 60 or 1} 分钟），回答作为结果回来，这一轮接着做。缺一个只有用户知道的信息、"
                    "或者有几个做法要他拿主意时用；能自己查到的别问，小事自己拿主意。options 最多 4 个，推荐的放第一个；"
                    "allow_free_text = false 时只认这几个选项。问题会显示在你的回复里，别在回复里再写一遍。撤不回的操作不用它问：那些调用自己会出卡。",
                    f"Ask the user one question and wait for the answer (up to {secs // 60 or 1} minutes); it comes back as the result and "
                    "the turn goes on. Use it when you need something only the user knows, or a decision between real options; don't ask "
                    "what you can look up, and decide small things yourself. Up to 4 options, your recommended one first; with "
                    "allow_free_text = false only those options count. The question shows in your reply, so don't repeat it there. Not "
                    "for permission to do things that can't be undone: those calls ask with their own card."),
                schema={"type": "object", "required": ["question"], "properties": {
                    "question": {"type": "string", "description": L("问用户的那一句（选项别写在这里）", "The question (options go in options, not here)")},
                    "options": {"type": "array", "items": {"type": "string"},
                                "description": L("可选：2–4 个短选项，推荐的放第一个", "Optional: 2–4 short options, your recommended one first")},
                    "allow_free_text": {"type": "boolean", "description": L("他能不能不选、直接说自己的（默认能）",
                                                                            "Whether they may answer in their own words instead (default true)")}}})


# —— POST /api/engine/answer：以后 app 的按钮用 ——————————————————————————————————————————

class AnswerIn(BaseModel):
    conversation: str
    id: str
    choice: int | str | None = None   # 选了第几个（从 1 数）或者那个选项的字
    text: str | None = None           # 自己写的回答


@router.post("/api/engine/answer")
async def answer(body: AnswerIn):
    """回答正在等的那个 ask_user（和在对话里直接回一句二选一，只算先到的那一次）。回答记进 app 的对话（是用户说的），交给这一轮。"""
    p = _open.get(body.id)
    if p is None or p.conversation != body.conversation:
        raise HTTPException(404, L("没有这个在等回答的问题（可能已经答过、等过了时间，或者这一轮停了）",
                                   "No such question waiting for an answer (it may have been answered, timed out, or the turn stopped)"))
    choice: int | None = None
    if body.choice is not None and str(body.choice).strip():
        c = body.choice
        choice = c if isinstance(c, int) and not isinstance(c, bool) else match(str(c), p.options)
        if choice is None or not 1 <= choice <= len(p.options):
            raise HTTPException(400, L("choice 要是选项的编号（从 1 数）或者选项的字", "choice must be an option's number (from 1) or its text"))
    text = p.options[choice - 1] if choice is not None else " ".join(str(body.text or "").split())
    if not text:
        raise HTTPException(400, L("要给 choice 或者 text", "Give choice or text"))
    if choice is None and not p.free:
        raise HTTPException(400, L("这个问题只认给出的选项", "This question only takes one of its options"))
    if not p.control.asking():
        raise HTTPException(409, L("这个问题已经答过了", "This question was already answered"))
    import chat

    from . import owner
    ts = chat.now_iso()
    with chat._lock, chat.db() as conn:
        mid = conn.execute("INSERT INTO messages(thread, role, text, model, ts, status) VALUES(?,?,?,?,?,?)",
                           (p.conversation, "user", text, None, ts, "ok")).lastrowid
    msg = owner.user_message(text, mid)
    if msg is None or not p.control.answer({**msg, "via": "button"}):
        with chat._lock, chat.db() as conn:
            conn.execute("DELETE FROM messages WHERE id=?", (mid,))
        raise HTTPException(409, L("这个问题已经答过了", "This question was already answered"))
    return {"ok": True, "id": body.id, "messageId": f"db{mid}", "answer": text, "choice": choice}
