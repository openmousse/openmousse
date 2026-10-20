"""/btw 旁问（第 2 批，10/19，engine/commands.py 的一条命令）：正在聊的时候顺手问一句，不进会话、零工具、不打断正在跑的那一轮。

怎么问（answer）：这个对话今天这条会话（DbSession.view()：压缩过的先是摘要，再是保留的和后来的；换新时带进来的那一段也算）写成一段
  文字记录当背景（transcript：一条最多 PER_MESSAGE 字、合计 BUDGET 字，新的优先、放不下的旧的写一句省略了；工具调用只写名字、工具结果
  截短；开头的 system 不带），连同问题交给这个对话现在的模型一次性地问（oneshot.ask_text：零工具、不落会话）。
  用户自己的套餐照 may_spend：是主人自己在 app 里问的（origin user），claude-code/… 交给他自己登录的 Claude Code 一次性地跑
  （claude_brain.ask），chatgpt/… 照主人回合跑；没做成而备用链里有按量的就换（oneshot 的规矩）。
  这一轮还在跑也能问：问的是那一刻会话里已经记下的样子，不等它、不打断它。
记在哪：问题那一句（/btw …）和回答记进 app 的对话（origin command / btw；回答开头标「（旁问）」，app 现在的气泡照画，界面等设计），
  不进会话、不进「上次聊到哪」、不抄进新会话（../chat.py 的 COMMAND_ORIGINS）：下一轮发给模型的会话里没有。用量记在这个对话名下
  （事件表，/usage 算得到）。
借：Hermes Agent（NousResearch/hermes-agent，commit f4ac0bea）agent/side_question.py 一次性的那条路（_ONESHOT_INSTRUCTIONS 的说法、
  render_history_for_side_question 的写法和上限），MIT，Copyright (c) 2025 Nous Research，许可全文见仓库根目录的 THIRD_PARTY_NOTICES；
  OpenClaw docs/tools/btw.md（拿当前会话当背景、不进会话、不打断正在跑的那一轮，只借设计）。
"""
from __future__ import annotations

import asyncio

from .session import DbSession, text_of, tool_calls

PER_MESSAGE, BUDGET, SUMMARY_MAX = 2_000, 24_000, 6_000
TIMEOUT = 180
LABELS = {"user": "USER", "assistant": "ASSISTANT", "tool": "TOOL RESULT"}
INSTRUCTIONS = (
    "You are the same AI assistant that is currently working inside the conversation transcribed below. The user has asked a quick "
    "SIDE question with /btw while the main work continues.\nRules:\n- Answer ONLY the side question. Do not continue, redo, or critique "
    "the main task.\n- Use the transcript as your primary context; it is a snapshot and may not include the very latest activity.\n"
    "- If the transcript does not contain enough information to answer, say so plainly instead of guessing.\n- Be concise and direct.")


def transcript(messages: list[dict], *, summary: str | None = None, carry: str | None = None, budget: int = BUDGET) -> str:
    """会话 → 一段文字记录（Hermes render_history_for_side_question 的写法）：新的优先、合计不超过 budget 字。"""
    lines: list[str] = []
    if carry:
        lines.append(f"EARLIER (carried over from a previous session): {carry.strip()[:PER_MESSAGE]}")
    if summary:
        lines.append(f"EARLIER (summary): {summary.strip()[:SUMMARY_MAX]}")
    for m in messages:
        if m.get("compaction"):  # 摘要那一条：上面已经写了
            continue
        role = m.get("role")
        calls = tool_calls(m)
        if role == "assistant" and calls:
            lines.append(f"ASSISTANT [called tools: {', '.join(str(c.get('name') or '?') for c in calls)}]")
        label, t = LABELS.get(str(role)), text_of(m).strip()
        if label and t:
            lines.append(f"{label}: {t[:PER_MESSAGE]}")
    kept: list[str] = []
    used = 0
    for ln in reversed(lines):
        if used + len(ln) + 1 > budget and kept:
            break
        kept.append(ln)
        used += len(ln) + 1
    if not kept:
        return "(no prior conversation)"
    return ("[...older conversation omitted...]\n" if len(kept) < len(lines) else "") + "\n".join(reversed(kept))


def background(session: DbSession | None) -> str:
    """这条会话现在的样子 → 文字记录（没有会话 = 还没聊过）。"""
    if session is None:
        return transcript([])
    from . import owner
    v = session.view()
    carry = owner.carried(session)
    return transcript(v.messages, summary=v.summary, carry=str(carry["text"]) if carry else None)


async def answer(conversation: str, question: str, *, model: str) -> tuple[str, str]:
    """问一句 → (回答, "builtin:<真正回答的模型>")。失败抛 EngineError。"""
    from . import commands, oneshot
    bg = await asyncio.to_thread(lambda: background(commands.session_now(conversation)))
    text = f"Conversation transcript (snapshot):\n-----\n{bg}\n-----\n\nSide question: {question.strip()}"
    return await oneshot.ask_text(INSTRUCTIONS, text, model=model, purpose="btw", timeout=TIMEOUT, conversation=conversation,
                                  origin="user", kind="owner")
