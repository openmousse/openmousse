"""赛博回合（内核第 0 步：后端 + 测试入口；新建、形象、通讯录这些界面在主线第三步，存起来的赛博在 ../cybers.py）。

赛博 = 只说话的 Agent：性格（identity：身份、语气、规矩三行）、自己的模型、认识你多少（sources）、自己的会话。
不建 OpenClaw agent，由内核直接跑；工具只有只读的 manual（13c，engine/manuals.py 的 cyber_tool：找手册、读手册的正文，世界树有手册才有；
它自己决定读才读，不像主人回合那样按句自动带进来：赛博用哪家模型，资料就发到哪家）。
主线第三步（10/20）：存起来的赛博（../cybers.py）多两个只给赛博回合的工具（engine/cyber_tools.py，Tool.kinds 只有 cyber）：
ask_agent 找它装载的功能 Agent（目标限死在装载名单里）、propose_change 提「改好的样子」（交卡，点了头才改）；chat(tools=…) 带进来。
记下的和知识库（memory）放在开头的最后一节，这一句相关的旧笔记（note）跟在这句话后面补一条 system。
群里（主线第三步第二段，../groupchat.py）走 speak()：同样是赛博回合、同样的工具，但不接一对一的会话，一轮一条不落库的会话。

cyber 是一个 dict：{"id", "name", "identity", "model"?, "sources"?: ["card"], "effort"?}
  model    不写 = server.json 的 engine.use.cyber，再不行用 engine.model。写成列表 ["anthropic/claude-sonnet-5-5", "moonshot/kimi-k3"]
           = 第一个是它的模型、后面是它自己的备用链（第 13 步，engine/fallback.py；只写一个 = 不换）
  sources  第 0 步只有 "card" = 名片（名片 Agent 给「朋友」这一档公开的那部分）。「它记住的」「问搭档」等「认识你」定了再做。
           赛博用哪家模型，这些资料就发到哪家：默认什么都不给。
会话：每个赛博每个逻辑日一条（cyber:<id>:<逻辑日>，04:00 换新）。开头的规矩在当天第一句时定下；当天改了性格，往后补一条 system，不改开头。
"""
from __future__ import annotations

import re
from datetime import datetime

from config import settings

from . import models
from .errors import EngineError
from .loop import Control, Result, Spec, run_turn
from .session import DbSession, system_note, text_of, time_stamp, user

DAY_START_HOUR = 4  # 和 chat.py 一样：凌晨 1 点还在聊的算前一天
CHANGED = "Your settings were updated. From now on, follow these instead of the earlier ones:\n\n"


def logical_day(now: datetime | None = None) -> str:
    from .reset import day_start  # 第 2 批：engine.reset.at 改了换天的钟点就照它（engine/reset.py）
    now = now or datetime.now(settings.tz)
    return (now - day_start()).date().isoformat()


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


def system_prompt(cyber: dict, manuals: bool = False, tools: list | None = None, memory: str = "") -> str:
    """manuals：有只读的 manual 工具（13c）。tools：engine/cyber_tools.py 的那几样（存起来的赛博才有）；memory：记下的、知识库（../cybers.py 备好的）。"""
    name = str(cyber.get("name") or "").strip() or "Cyber"
    owner = settings.user_name or "the user"
    identity = str(cyber.get("identity") or "").strip()
    extra = [t for t in tools or [] if getattr(t, "prompt_line", "")]
    if extra:  # 主线第三步：装载的功能 Agent、提改法的工具，各自写了一行给开头的（Tool.prompt_line）
        lines = [t.prompt_line for t in extra] + ([f"- manual (read-only): finds and reads {owner}'s manuals, their own written procedures; "
                                                   "read one before helping with what it covers."] if manuals else [])
        how = (f" To change something, ask the loaded Agent that looks after it; it puts a card in front of {owner}, and nothing changes until "
               "they approve." if any(t.name == "ask_agent" for t in extra) else "")
        tools_text = ("Your tools:\n" + "\n".join(lines) + f"\nYou don't touch {owner}'s data yourself: you can't write records, change the "
                      f"schedule or goals, message anyone or spend money.{how}")
    elif manuals:
        tools_text = (f"Your only tool is manual (read-only): it finds and reads {owner}'s manuals, their own written procedures; when they ask "
                      "you to do something one of them may cover, read it first and follow it. Otherwise you cannot look anything up, remember "
                      "things on your own, message anyone or act in the world, so don't offer to.")
    else:
        tools_text = "You have no tools: you cannot look anything up, remember things on your own, message anyone or act in the world, so don't offer to."
    parts = [f"You are {name}, one of {owner}'s Cybers in {settings.app_name}: a character {owner} talks with. "
             f"You only talk. {tools_text} If something needs doing, say which of their agents or which step would do it.",
             f"Who you are and how you talk:\n{identity}" if identity else "",
             "Reply in the language the user writes in. Keep replies short unless they ask for more."]
    if "card" in (cyber.get("sources") or []):
        card = card_text()
        if card:
            parts.append(f"What {owner} has shared about themselves (their public card; it may be out of date):\n{card}")
    if memory.strip():
        parts.append(memory.strip())
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


async def chat(cyber: dict, text: str, *, on_event=None, control: Control | None = None, origin: str = "user", tools: list | None = None,
               memory: str = "", note: str = "", images: list[dict] | None = None, ref: int | None = None, stamp: bool = False) -> Result:
    """跟一个赛博说一句：接在它今天的会话后面，跑一轮赛博回合，回 Result（text = 它的回复）。
    tools：存起来的赛博的工具（engine/cyber_tools.py，Tool.kinds 只有 cyber）；memory：记下的、知识库，放在开头最后一节；
    note：这一句相关的旧笔记，跟在这句话后面补一条 system（不进开头，不让开头天天变）；images：这句话带的图（中立格式的图块）；
    ref：app 里这条消息的 id（同主人回合，撤回时认）；stamp：这句话带上当地时间（同主人回合，session.with_time）。
    都不给 = 原来的样子（测试入口 /api/engine/cyber）。"""
    cid = cyber_id(cyber)
    model = model_of(cyber)
    from . import manuals  # 13c：只读的 manual 工具（世界树有手册才有）
    tool = manuals.cyber_tool()
    extra = [t for t in tools or [] if "cyber" in t.kinds]
    rules = system_prompt(cyber, manuals=tool is not None, tools=extra, memory=memory)
    session = DbSession.open(f"cyber:{cid}:{logical_day()}", kind="cyber", agent=cid, model=model, system=rules)
    # 只认改性格补的那几条（这一句相关的旧笔记也是 system，不算）
    notes = [t for t in (text_of(m) for m in session.messages() if m.get("role") == "system") if t.startswith(CHANGED)]
    current = notes[-1][len(CHANGED):] if notes else session.system
    prompt = [{**user(text, images), **({"ref": ref} if ref is not None else {}), **({"at": time_stamp()} if stamp else {})}]
    if current != rules:  # 当天改了性格：跟在这句话后面补一条 system（Claude 要求它跟在 user 后面），不回头改开头
        prompt.append(system_note(CHANGED + rules))
    if note.strip():
        prompt.append(system_note(note.strip()))
    spec = Spec(kind="cyber", model=model, system=session.system, tools=[*extra, *([tool] if tool is not None else [])], conversation=f"cyber:{cid}", agent=cid,
                origin=origin,
                effort=cyber.get("effort") if cyber.get("effort") in ("low", "medium", "high", "xhigh", "max") else None,
                display="summarized", cache_ttl="1h", fallbacks=fallbacks_of(cyber, model))
    return await run_turn(spec, session, prompt, on_event=on_event, control=control)


async def speak(cyber: dict, text: str, *, where: str, conversation: str, on_event=None, control: Control | None = None,
                origin: str = "user", tools: list | None = None, timeout: float = 600) -> Result:
    """在群里说一句（主线第三步第二段，../groupchat.py）：还是赛博回合（kind cyber：不花套餐，工具只有 Tool.kinds 带 cyber 的那几样），
    但不接它一对一的会话：一轮一条不落库的会话（MemorySession），开头 = 它的设定 + where（这个群的规矩），text = 这一次它该读的
    （群那边拼好：钉住的小结、最近几条、@ 它的那条）。conversation：事件表里记成哪个对话（group:<群>:<赛博>，它在这个群自己的会话）。
    不带它记下的和知识库（群里只读设定、小结、最近几条和 @ 它的那条），名片照旧（sources card）。"""
    from .session import MemorySession
    cid = cyber_id(cyber)
    model = model_of(cyber)
    extra = [t for t in tools or [] if "cyber" in t.kinds]
    system = "\n\n".join(p for p in [system_prompt(cyber, tools=extra), where.strip()] if p)
    spec = Spec(kind="cyber", model=model, system=system, tools=extra, conversation=conversation, agent=cid, origin=origin,
                effort=cyber.get("effort") if cyber.get("effort") in ("low", "medium", "high", "xhigh", "max") else None,
                display="summarized", timeout=timeout, fallbacks=fallbacks_of(cyber, model))
    return await run_turn(spec, MemorySession(), {**user(text), "at": time_stamp()}, on_event=on_event, control=control)
