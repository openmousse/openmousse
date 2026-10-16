"""外人回合的一次性问答：规矩（prompt）+ 资料（INPUT_JSON）→ 一个 JSON。零工具、不落会话，事件表只记谁、多久、用量。

替代 OpenClaw 的 llm-task，多了一样：能换模型（llm-task 不许覆盖模型）。名片 Agent（含 A2A）、Doorman、llmjson.ask 按
server.json 的 engine.use 改走这里；出错时照旧退回原来的路（engine.fallback）。

JSON 只卡最外层（是对象 / 数组、need 那个键是字符串、required 的键都在）：和 llm-task 那时一样，严格校验整个 schema
会因为模型多带一个键就整句作废。回的不像样就再问一次。
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any

from . import models
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
                   max_tokens: int | None = None, conversation: str | None = None, origin: str | None = None) -> tuple[Any, str]:
    """→ (JSON, "builtin:<模型>")。timeout 是这一问的总时间（含再问一次）。失败抛 EngineError。"""
    spec = Spec(kind="outsider", model=model, system=prompt.rstrip() + JSON_RULE, conversation=conversation or purpose,
                origin=origin or purpose, effort=effort or models.effort(purpose), max_tokens=max_tokens, json=True, max_steps=1)
    text = "INPUT_JSON:\n" + json.dumps(input_, ensure_ascii=False, indent=1)
    deadline = time.monotonic() + timeout
    last: EngineError | None = None
    for _attempt in (1, 2):
        left = deadline - time.monotonic()
        if left < 3:
            break
        spec.timeout = left
        try:
            res = await asyncio.wait_for(run_turn(spec, MemorySession(), user(text)), left)
        except asyncio.TimeoutError as e:
            raise EngineError(f"{model} took longer than {timeout:.0f}s", kind="timeout", retryable=True) from e
        if res.status == "refused":
            raise EngineError(f"{model} declined to answer", kind="refusal")
        if res.status == "error":
            raise EngineError(res.error or "model error", kind="http", retryable=False)
        if res.status not in ("done", "length"):
            raise EngineError(f"turn ended as {res.status}", kind="output")
        try:
            v = parse(res.text)
            check(v, need, required)
            return v, f"builtin:{model}"
        except EngineError as e:
            last = e
    raise last or EngineError(f"{model} ran out of time", kind="timeout", retryable=True)
