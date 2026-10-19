"""赛博回合（内核第 0 步：后端 + 测试入口；新建、形象、通讯录这些界面在主线第三步，见 ~/openmousse-wt/agents-groups/README.md）。

赛博 = 只说话的 Agent：性格（identity：身份、语气、规矩三行）、自己的模型、认识你多少（sources）、自己的会话。
不建 OpenClaw agent，由内核直接跑；零工具（以后加只读的「问搭档」，经 /mcp 按 mcp-<id> 令牌过滤，不靠提示词自觉）。

cyber 是一个 dict：{"id", "name", "identity", "model"?, "sources"?: ["card"], "effort"?}
  model    不写 = server.json 的 engine.use.cyber，再不行用 engine.model。写成列表 ["anthropic/claude-sonnet-5-5", "moonshot/kimi-k3"]
           = 第一个是它的模型、后面是它自己的备用链（第 13 步，engine/fallback.py；只写一个 = 不换）
  sources  第 0 步只有 "card" = 名片（名片 Agent 给「朋友」这一档公开的那部分）。「它记住的」「问搭档」等「认识你」定了再做。
           赛博用哪家模型，这些资料就发到哪家：默认什么都不给。
会话：每个赛博每个逻辑日一条（cyber:<id>:<逻辑日>，04:00 换新）。开头的规矩在当天第一句时定下；当天改了性格，往后补一条 system，不改开头。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from config import settings

from . import models
from .errors import EngineError
from .loop import Control, Result, Spec, run_turn
from .session import DbSession, system_note, text_of, user

DAY_START_HOUR = 4  # 和 chat.py 一样：凌晨 1 点还在聊的算前一天
CHANGED = "Your settings were updated. From now on, follow these instead of the earlier ones:\n\n"


def logical_day(now: datetime | None = None) -> str:
    now = now or datetime.now(settings.tz)
    return (now - timedelta(hours=DAY_START_HOUR)).date().isoformat()


def cyber_id(cyber: dict) -> str:
    cid = re.sub(r"[^a-zA-Z0-9_-]", "", str(cyber.get("id") or ""))[:40]
    if not cid:
        raise EngineError("a Cyber needs an id (letters, digits, - and _)", kind="config")
    return cid


def card_text() -> str:
    """名片：名片 Agent 给「朋友」这一档公开的资料（日程只到忙闲、近况一句……按 card 段的档位设置）。"""
    import cardagent  # 用到时才导入：内核本身不依赖名片 Agent
    mats, _ = cardagent.build_material(cardagent.scopes("friend"), settings.language)
    return "\n".join(f"- {m['label']}: {m['text']}" for m in mats if m.get("text"))


def system_prompt(cyber: dict) -> str:
    name = str(cyber.get("name") or "").strip() or "Cyber"
    owner = settings.user_name or "the user"
    identity = str(cyber.get("identity") or "").strip()
    parts = [f"You are {name}, one of {owner}'s Cybers in {settings.app_name}: a character {owner} talks with. "
             "You only talk. You have no tools: you cannot look anything up, remember things on your own, message anyone or act in the world, "
             "so don't offer to. If something needs doing, say which of their agents or which step would do it.",
             f"Who you are and how you talk:\n{identity}" if identity else "",
             "Reply in the language the user writes in. Keep replies short unless they ask for more."]
    if "card" in (cyber.get("sources") or []):
        card = card_text()
        if card:
            parts.append(f"What {owner} has shared about themselves (their public card; it may be out of date):\n{card}")
    return "\n\n".join(p for p in parts if p)


def model_of(cyber: dict) -> str:
    own = models.chain_of(cyber.get("model"))
    if own:
        return own[0]
    m = str(cyber.get("model") or "").strip() if isinstance(cyber.get("model"), str) else ""
    if m and models.valid_ref(m):
        return m
    m = models.route("cyber") or models.default_model()
    if models.subscription(m):  # 赛博回合不花用户的套餐：换成默认的非套餐模型
        m = models.outside(m) or ""
    if not m:
        raise EngineError("no model for this Cyber: give it one, or set engine.model in server.json", kind="config")
    return m


def fallbacks_of(cyber: dict, model: str) -> list[str] | None:
    """它自己的备用链：model 写成列表的后面那几个；用的是 engine.use.cyber 的就看那一项；都不是 → None（按模型找默认链）。"""
    own = models.chain_of(cyber.get("model"))
    if own and own[0] == model:
        return own[1:]
    return models.purpose_chain("cyber", model)


async def chat(cyber: dict, text: str, *, on_event=None, control: Control | None = None, origin: str = "user") -> Result:
    """跟一个赛博说一句：接在它今天的会话后面，跑一轮赛博回合（零工具），回 Result（text = 它的回复）。"""
    cid = cyber_id(cyber)
    model = model_of(cyber)
    rules = system_prompt(cyber)
    session = DbSession.open(f"cyber:{cid}:{logical_day()}", kind="cyber", agent=cid, model=model, system=rules)
    notes = [text_of(m) for m in session.messages() if m.get("role") == "system"]
    current = notes[-1][len(CHANGED):] if notes and notes[-1].startswith(CHANGED) else session.system
    prompt = [user(text)]
    if current != rules:  # 当天改了性格：跟在这句话后面补一条 system（Claude 要求它跟在 user 后面），不回头改开头
        prompt.append(system_note(CHANGED + rules))
    spec = Spec(kind="cyber", model=model, system=session.system, conversation=f"cyber:{cid}", agent=cid, origin=origin,
                effort=cyber.get("effort") if cyber.get("effort") in ("low", "medium", "high", "xhigh", "max") else None,
                display="summarized", cache_ttl="1h", fallbacks=fallbacks_of(cyber, model))
    return await run_turn(spec, session, prompt, on_event=on_event, control=control)
