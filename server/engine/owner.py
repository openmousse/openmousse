"""claw 选 builtin 时的主对话（内核第 0 步的骨架：只会对话和停止；工具、关卡、备料是第 2、4 步）。

chat.py 照别的 claw 的 history 模式把今天的记录拼成 OpenAI 格式交过来（app 里撤回 = 删记录，下一轮就不带了），
这里翻成中立格式，用内核跑一轮主人回合（零工具），回复一段段交给 on_delta。停 = chat.py 取消这个任务，内核记成 aborted。
"""
from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime

from config import settings

from . import models
from .errors import EngineError
from .loop import Spec, run_turn
from .session import MemorySession

DATA_URL = re.compile(r"^data:([\w/+.-]+);base64,(.+)$", re.S)
KEY = re.compile(r"^mousse:(.+?)(?::\d{4}-\d{2}-\d{2})?$")


def system_prompt() -> str:
    owner = settings.user_name or "the user"
    today = datetime.now(settings.tz).strftime("%Y-%m-%d (%A)")
    return (f"You are {settings.app_name}, {owner}'s personal assistant, running on {settings.app_name}'s built-in engine. "
            f"Today is {today}, time zone {settings.timezone}. You have no tools in this version: you can't look things up, "
            "read files or change anything, so say so plainly when a request needs that. Reply in the language the user writes in.")


def neutral(messages: list[dict]) -> list[dict]:
    """chat.py 给的 OpenAI 格式（role user / assistant，content 是字符串或 text / image_url 块）→ 中立格式。"""
    out: list[dict] = []
    for m in messages:
        role, c = m.get("role"), m.get("content")
        blocks: list[dict] = []
        for part in ([{"type": "text", "text": c}] if isinstance(c, str) else c or []):
            if part.get("type") == "text" and part.get("text"):
                blocks.append({"type": "text", "text": str(part["text"])})
            elif part.get("type") == "image_url":
                url = str((part.get("image_url") or {}).get("url") or "")
                d = DATA_URL.match(url)
                blocks.append({"type": "image", "mime": d.group(1), "data": d.group(2)} if d else {"type": "text", "text": f"[image: {url[:200]}]"})
        if not blocks:
            continue
        if role == "assistant":
            out.append({"role": "assistant", "content": [b for b in blocks if b["type"] == "text"], "api": None, "provider": None, "model": None,
                        "stop": "end", "usage": {}})
        else:
            out.append({"role": "user", "content": blocks})
    return out


async def stream(messages: list[dict], key: str, model: str | None, on_delta: Callable[[str], None], timeout: float = 600) -> str:
    """跑一轮主人回合：messages 最后一条是这次说的话，前面是今天的记录。→ 整段回复。出错抛 EngineError。"""
    ref = model if model and models.valid_ref(model) else models.default_model()
    if not ref:
        raise EngineError("no model: set engine.model in server.json", kind="config")
    msgs = neutral(messages)
    if not msgs or msgs[-1]["role"] != "user":
        raise EngineError("nothing to answer", kind="rule")
    m = KEY.match(key or "")
    spec = Spec(kind="owner", model=ref, system=system_prompt(), conversation=m.group(1) if m else (key or "main"), origin="user",
                display="summarized", timeout=timeout, cache_ttl="1h")
    res = await run_turn(spec, MemorySession(msgs[:-1]), msgs[-1],
                         on_event=lambda ev: on_delta(ev["text"]) if ev.get("type") == "text" else None)
    if res.status == "error":
        raise EngineError(res.error or "model error")
    if res.status == "refused" and not res.text:
        raise EngineError("the model declined to answer", kind="refusal")
    return res.text
