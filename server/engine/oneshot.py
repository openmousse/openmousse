"""一次性问答：规矩（prompt）+ 资料（INPUT_JSON）→ 一个 JSON。零工具、不落会话，事件表只记谁、多久、用量。

替代 OpenClaw 的 llm-task，多了一样：能换模型（llm-task 不许覆盖模型）。名片 Agent（含 A2A）、Doorman、llmjson.ask 按
server.json 的 engine.use 改走这里；出错时照旧退回原来的路（engine.fallback）。
备用链（第 13 步，engine/fallback.py）：engine.use.<这件事> 写成列表就用它自己的链，否则按模型找（engine.fallbacks）；外人回合的字
没人在看，流到一半断了也能换。链走完了还是不行才抛错，调用方（llmjson、名片 Agent、Doorman）这时才退回原来的路。
默认是外人回合（kind outsider）。10/19 起调用方能报上来路（kind / origin / via）：主人自己的（llmjson.ask 的会议纪要、播客、学习页……）
model 是用户自己的套餐时照 may_spend 花：claude-code/… 交给他自己登录的 Claude Code 一次性地跑（claude_brain.ask，零工具、不存
会话）；它没做成（没登录、额度用完、超时……）而链里有按量的就换（有备用时它最多等剩下时间的一半），没有照旧抛错。chatgpt/… 照主人
回合跑（零工具）；grok/…（10/20，用户自己的 Grok 订阅）同 chatgpt/…。不报的照旧是外人回合，route() 根本不会给它套餐。

JSON 只卡最外层（是对象 / 数组、need 那个键是字符串、required 的键都在）：和 llm-task 那时一样，严格校验整个 schema
会因为模型多带一个键就整句作废。回的不像样就再问一次。
ask_text（第 2 批，/btw 旁问）：同样的一问一答，回一段字、不要 JSON。
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any

from . import fallback, models
from .errors import EngineError
from .loop import Spec, run_turn
from .session import MemorySession, user

JSON_RULE = "\n\nReply with ONE JSON value only: no other text, no code fences. You have no tools and cannot look anything up."


def parse(text: str) -> Any:
    """回复里的 JSON（去掉 ```json 围栏和前后的话）。不是 JSON 抛 EngineError(output)。"""
    s = (text or "").strip()
    m = re.search(r"```(?:json)?\s*([\[{].*[\]}])\s*```", s, re.S)
    if m:
        s = m.group(1)
    else:
        starts = [i for i in (s.find("{"), s.find("[")) if i >= 0]
        a, b = (min(starts) if starts else -1), max(s.rfind("}"), s.rfind("]"))
        s = s[a:b + 1] if a >= 0 and b > a else s
    try:
        return json.loads(s)
    except ValueError as e:
        raise EngineError("the reply is not JSON", kind="output", retryable=True) from e


def check(v: Any, need: str | None, required: tuple[str, ...] | list[str]) -> None:
    if need is not None or required:
        if not isinstance(v, dict):
            raise EngineError("the reply is not a JSON object", kind="output", retryable=True)
        if need is not None and not isinstance(v.get(need), str):
            raise EngineError(f"the reply has no {need!r} string", kind="output", retryable=True)
        missing = [k for k in required if k not in v]
        if missing:
            raise EngineError(f"the reply is missing {', '.join(missing)}", kind="output", retryable=True)


async def ask_json(prompt: str, input_: Any, *, model: str, purpose: str = "json", need: str | None = None,
                   required: tuple[str, ...] | list[str] = (), timeout: float = 90, effort: str | None = None,
                   max_tokens: int | None = None, conversation: str | None = None, origin: str | None = None, kind: str = "outsider",
                   via: str | None = None, fallbacks: list[str] | None = None) -> tuple[Any, str]:
    """→ (JSON, "builtin:<真正回答的模型>")。timeout 是这一问的总时间（含再问一次）。失败抛 EngineError。
    kind / origin / via：这一问是谁引出来的（见文件开头；外人回合的 origin 只是个记号：card、doorman……）。model 是用户自己的套餐时
    照 may_spend 认，不能花抛 EngineError(rule)（调用方先用 route() 挑，到不了这里）。fallbacks：这一问的链（不给 = engine.use.<purpose>
    写成列表的那条，没写按模型找）。"""
    sub = models.subscription(model)
    if sub and not models.may_spend(model, kind=kind, origin=origin, via=via):
        raise EngineError(f"{model} is the user's own plan, which this call can't use", kind="rule")
    chain = fallbacks if fallbacks is not None else models.purpose_chain(purpose, model)
    text = "INPUT_JSON:\n" + json.dumps(input_, ensure_ascii=False, indent=1)
    if sub == "claude-code":
        return await _on_claude_code(prompt, text, input_, model=model, purpose=purpose, need=need, required=required, timeout=timeout,
                                     effort=effort, max_tokens=max_tokens, conversation=conversation, origin=origin, kind=kind, via=via,
                                     chain=chain)
    owner = sub == "chatgpt"  # 主人自己的、能花 ChatGPT 套餐（上面问过）：照主人回合跑，零工具
    owner = owner or sub in ("grok", "kimi")  # Grok 订阅、Kimi 会员同上
    spec = Spec(kind="owner" if owner else "outsider", model=model, system=prompt.rstrip() + JSON_RULE, conversation=conversation or purpose,
                origin=origin if owner else (None if kind == "owner" else origin) or purpose, via=via if owner else None,
                effort=effort or models.effort(purpose), max_tokens=max_tokens, json=True, max_steps=1, cache_tail=False, fallbacks=chain)
    deadline = time.monotonic() + timeout
    last: EngineError | None = None
    spare = len(fallback.chain(spec)) > 1
    for _attempt in (1, 2):
        left = deadline - time.monotonic()
        if left < 3:
            break
        spec.timeout = left / 2 if spare else left  # 有备用链：一个模型最多等剩下的一半，不回话也来得及换下一个
        try:
            res = await asyncio.wait_for(run_turn(spec, MemorySession(), user(text)), left)
        except asyncio.TimeoutError as e:
            raise EngineError(f"{model} took longer than {timeout:.0f}s", kind="timeout", retryable=True) from e
        if res.status == "refused":
            raise EngineError(f"{model} declined to answer", kind="refusal")
        if res.status == "error":
            raise EngineError(res.error or "model error", kind=res.error_kind or "http", retryable=res.retryable)
        if res.status == "length":  # 写到上限被截断：同样的上限再问一次还是截断（OpenClaw 的经验），直接报
            raise EngineError(f"{model}'s reply was cut off (max_tokens)", kind="output")
        if res.status != "done":
            raise EngineError(f"turn ended as {res.status}", kind="output")
        try:
            v = parse(res.text)
            check(v, need, required)
            return v, f"builtin:{res.model}"
        except EngineError as e:
            last = e
    raise last or EngineError(f"{model} ran out of time", kind="timeout", retryable=True)


TEXT_RULE = "\n\nYou have no tools and cannot look anything up: answer from what is given here."


async def ask_text(prompt: str, text: str, *, model: str, purpose: str = "text", timeout: float = 120, effort: str | None = None,
                   max_tokens: int | None = None, conversation: str | None = None, origin: str | None = None, kind: str = "outsider",
                   via: str | None = None, fallbacks: list[str] | None = None) -> tuple[str, str]:
    """第 2 批（/btw 旁问用，engine/side.py）：同 ask_json，只是回一段字、不要 JSON（零工具、不落会话；kind / origin / via、套餐、
    备用链的规矩都一样）。→ (回答, "builtin:<真正回答的模型>")。失败抛 EngineError；写到上限被截断的照样回（末尾补一个 …）。"""
    sub = models.subscription(model)
    if sub and not models.may_spend(model, kind=kind, origin=origin, via=via):
        raise EngineError(f"{model} is the user's own plan, which this call can't use", kind="rule")
    chain = fallbacks if fallbacks is not None else models.purpose_chain(purpose, model)
    system = prompt.rstrip() + TEXT_RULE
    deadline = time.monotonic() + timeout
    if sub == "claude-code":
        from . import claude_brain
        spare = fallback.backup(model, kind=kind, origin=origin, via=via, chain=chain)
        res = await claude_brain.ask(system, text, model=model, origin=origin, conversation=conversation or purpose,
                                     timeout=timeout / 2 if spare else timeout, effort_=effort, max_tokens=max_tokens)
        if res.status == "done" and res.text.strip():
            return res.text.strip(), f"builtin:{model}"
        last = EngineError(res.error or f"{model} didn't answer", kind=res.error_kind or "http")
        left = deadline - time.monotonic()
        if spare and last.kind not in NO_SWITCH and left >= 3:
            return await ask_text(prompt, text, model=spare[0], purpose=purpose, timeout=left, effort=effort, max_tokens=max_tokens,
                                  conversation=conversation, origin=origin, kind=kind, via=via, fallbacks=spare[1:])
        raise last
    owner = sub == "chatgpt"
    owner = owner or sub in ("grok", "kimi")  # Grok 订阅、Kimi 会员同 ChatGPT 套餐
    spec = Spec(kind="owner" if owner else "outsider", model=model, system=system, conversation=conversation or purpose,
                origin=origin if owner else (None if kind == "owner" else origin) or purpose, via=via if owner else None,
                effort=effort or models.effort(purpose), max_tokens=max_tokens, max_steps=1, cache_tail=False, fallbacks=chain)
    spec.timeout = timeout / 2 if len(fallback.chain(spec)) > 1 else timeout
    try:
        res = await asyncio.wait_for(run_turn(spec, MemorySession(), user(text)), timeout)
    except asyncio.TimeoutError as e:
        raise EngineError(f"{model} took longer than {timeout:.0f}s", kind="timeout", retryable=True) from e
    if res.status == "refused":
        raise EngineError(f"{model} declined to answer", kind="refusal")
    if res.status == "error":
        raise EngineError(res.error or "model error", kind=res.error_kind or "http", retryable=res.retryable)
    if res.status not in ("done", "length") or not res.text.strip():
        raise EngineError(f"turn ended as {res.status}" if res.status not in ("done", "length") else f"{model} gave an empty answer", kind="output")
    return res.text.strip() + (" …" if res.status == "length" else ""), f"builtin:{res.model}"


NO_SWITCH = ("rule", "refusal")  # Claude Code 没做成时不换的（规矩不许、它拒答）；别的（没登录、额度用完、命令坏了、超时、过载、回的不是 JSON）换


async def _on_claude_code(prompt: str, text: str, input_: Any, *, model: str, purpose: str, need: str | None,
                          required: tuple[str, ...] | list[str], timeout: float, effort: str | None, max_tokens: int | None,
                          conversation: str | None, origin: str | None, kind: str, via: str | None, chain: list[str] | None) -> tuple[Any, str]:
    """model 是 claude-code/…：交给用户自己登录的 Claude Code 一次性地跑（claude_brain.ask，见文件开头），回的不像样再问一次。没做成、
    链里有按量的（fallback.backup：不是套餐、有密钥）就换上从头问，没有照旧抛错。有备用时它最多等剩下时间的一半。"""
    from . import claude_brain, fallback
    spare = fallback.backup(model, kind=kind, origin=origin, via=via, chain=chain)
    deadline = time.monotonic() + timeout
    last: EngineError | None = None
    for _attempt in (1, 2):
        left = deadline - time.monotonic()
        if left < 3:
            break
        res = await claude_brain.ask(prompt.rstrip() + JSON_RULE, text, model=model, origin=origin, conversation=conversation or purpose,
                                     timeout=left / 2 if spare else left, effort_=effort, max_tokens=max_tokens)
        if res.status != "done":
            last = EngineError(res.error or f"{model} didn't answer", kind=res.error_kind or "http")
            break
        try:
            v = parse(res.text)
            check(v, need, required)
            return v, f"builtin:{model}"
        except EngineError as e:
            last = e
    last = last or EngineError(f"{model} ran out of time", kind="timeout", retryable=True)
    left = deadline - time.monotonic()
    if spare and last.kind not in NO_SWITCH and left >= 3:
        try:
            return await ask_json(prompt, input_, model=spare[0], purpose=purpose, need=need, required=required, timeout=left, effort=effort,
                                  max_tokens=max_tokens, conversation=conversation, origin=origin, kind=kind, via=via, fallbacks=spare[1:])
        except EngineError as e:
            e.plan_failure = last  # 备用也没做成：套餐那一下是怎么没做成的记在上面（llmjson.plan_failed 报错时说清楚）
            raise
    raise last
