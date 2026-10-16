"""接 Claude：官方 anthropic SDK（1.x），只用 API 密钥（Anthropic 条款：第三方产品不许借 Claude 订阅登录，见 engine/README）。

跟着 2026 年的接口走（claude-api 技能 2026-10 的说法）：
  - 思考：新型号一律 thinking adaptive，深浅靠 output_config.effort（Opus 5.5 关不掉思考，默认 medium）；老型号什么都不带。
  - 回放：assistant 的内容块（带思考签名）原样存在 raw 里，下一轮原样发回去；换了型号也照发，接口自己丢掉读不了的。
    改开头、删中间、把一句提醒塞进旧消息再删掉，都会让后面的思考作废（还白掉缓存），所以会话只往后补（session.py）。
  - 对话中间的规矩用 role=system 的消息（支持的型号）；不支持的折成 <system-reminder> 文字块放进前面那条 user。
  - 缓存：开头的 system 打一个断点 + 顶层自动缓存对话尾巴。
  - 拒答：stop_reason = refusal；Opus 5.5 / Fable 5.1 / Opus 5 / Sonnet 5.5 默认带服务端的 fallbacks: "default"（拒答时接口自己换型号再答）。
  - 工具：eager_input_streaming 开着，参数边生成边流过来；SDK 的宽松解析可能给半截参数，所以 loop.py 跑工具前按 schema 再查一遍。
  - 强制调工具（tool_choice any / tool）新型号会 400：不用。
"""
from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator

from . import models
from .errors import EngineError
from .session import text_of

STOP = {"end_turn": "end", "stop_sequence": "end", "tool_use": "tool", "max_tokens": "length", "model_context_window_exceeded": "length",
        "refusal": "refusal", "pause_turn": "pause"}
DEFAULT_MAX_TOKENS = 32000  # 思考也算在里面（Opus 5.5 关不掉思考），流式不怕超时，给宽一点
BETA_UPDATES = "thinking-display-updates-2026-08-18"
BETA_FALLBACK = "server-side-fallback-2026-07-01"
TOOL_ID = re.compile(r"[^a-zA-Z0-9_-]")

_clients: dict[tuple, object] = {}


def make_client(p: dict):
    """AsyncAnthropic（按密钥 + 地址缓存）。测试时换掉这个函数，塞一个假的 http_client。"""
    try:
        import anthropic
    except ImportError as e:
        raise EngineError("the anthropic package isn't installed (pip install -r server/requirements.txt)", kind="config") from e
    if not p.get("key"):
        raise EngineError("no Anthropic API key: put ANTHROPIC_API_KEY in the .env (or set engine.providers.anthropic.key_env)", kind="config")
    k = (p["key"], p.get("url") or "", json.dumps(p.get("headers") or {}, sort_keys=True))
    c = _clients.get(k)
    if c is None:
        kw: dict = {"api_key": p["key"], "max_retries": 2}
        if p.get("url"):
            kw["base_url"] = p["url"]
        if isinstance(p.get("headers"), dict):
            kw["default_headers"] = {str(a): str(b) for a, b in p["headers"].items()}
        c = anthropic.AsyncAnthropic(**kw)
        _clients[k] = c
    return c


# —— 中立格式 → Claude ———————————————————————————————————————————————————

def _tid(s: str) -> str:
    """工具调用的 id：Claude 只认字母数字、下划线、连字符（Kimi 的 functions.x:0 这种换掉）。两边用同一个函数，配得上。"""
    return TOOL_ID.sub("_", str(s or "")) or "call"


def _media(blocks: list[dict]) -> list[dict]:
    out: list[dict] = []
    for b in blocks or []:
        if b.get("type") == "text" and b.get("text"):
            out.append({"type": "text", "text": b["text"]})
        elif b.get("type") == "image" and b.get("data"):
            out.append({"type": "image", "source": {"type": "base64", "media_type": b.get("mime") or "image/png", "data": b["data"]}})
    return out


def _assistant(m: dict) -> list[dict]:
    if m.get("api") == models.ANTHROPIC and isinstance(m.get("raw"), list) and m["raw"]:
        return m["raw"]  # Claude 自己回的：原样发回去（思考块带签名，一个字都不能动）
    out: list[dict] = []
    for b in m.get("content") or []:
        t = b.get("type")
        if t == "text" and b.get("text"):
            out.append({"type": "text", "text": b["text"]})
        elif t == "tool_call":
            out.append({"type": "tool_use", "id": _tid(b.get("id") or ""), "name": b.get("name") or "", "input": b.get("args") or {}})
        elif t == "thinking" and m.get("api") == models.ANTHROPIC and b.get("sig"):
            out.append({"type": "thinking", "thinking": b.get("text") or "", "signature": b["sig"]})
        # 别家的思考（没有签名）不发：Claude 不认，也不该当成它自己想过的
    return out


def to_anthropic(messages: list[dict], caps: dict) -> list[dict]:
    """中立格式 → messages。同一份历史每次翻出来一模一样（缓存和思考签名都靠这个）。"""
    out: list[dict] = []
    pending: list[str] = []  # 前面还没有 user 的 system 条目：并进下一条 user 的开头
    for i, m in enumerate(messages):
        role = m.get("role")
        if role == "user":
            blocks = _media(m.get("content") or []) or [{"type": "text", "text": "…"}]
            if pending:
                blocks = [{"type": "text", "text": "<system-reminder>\n" + "\n\n".join(pending) + "\n</system-reminder>"}, *blocks]
                pending = []
            out.append({"role": "user", "content": blocks})
        elif role == "tool":
            block: dict = {"type": "tool_result", "tool_use_id": _tid(m.get("id") or ""), "content": _media(m.get("content") or []) or "(empty)"}
            if m.get("error"):
                block["is_error"] = True
            last = out[-1] if out else None
            if last and last["role"] == "user" and all(isinstance(b, dict) and b.get("type") == "tool_result" for b in last["content"]):
                last["content"].append(block)  # 同一批工具结果放进同一条 user（拆开会让模型以后不再并行调工具）
            else:
                out.append({"role": "user", "content": [block]})
        elif role == "assistant":
            content = _assistant(m)
            if content:
                out.append({"role": "assistant", "content": content})
        elif role == "system":
            text = text_of(m).strip()
            if not text:
                continue
            nxt = next((x.get("role") for x in messages[i + 1:] if x.get("role") != "system"), None)
            last = out[-1] if out else None
            if last and last["role"] == "system":
                last["content"] += "\n\n" + text
            elif caps.get("mid_system") and last and last["role"] == "user" and nxt in (None, "assistant"):
                out.append({"role": "system", "content": text})
            elif last and last["role"] == "user":
                last["content"].append({"type": "text", "text": "<system-reminder>\n" + text + "\n</system-reminder>"})
            else:
                pending.append(text)
    if pending:
        out.append({"role": "user", "content": [{"type": "text", "text": "<system-reminder>\n" + "\n\n".join(pending) + "\n</system-reminder>"}]})
    return out


def build(model_id: str, req, p: dict) -> tuple[dict, list[str]]:
    """→ (请求参数, betas)。"""
    caps = models.anthropic_caps(model_id)
    params: dict = {"model": model_id, "max_tokens": int(req.max_tokens or DEFAULT_MAX_TOKENS), "messages": to_anthropic(req.messages, caps),
                    "cache_control": {"type": "ephemeral"}}
    betas: list[str] = []
    if req.system:
        params["system"] = [{"type": "text", "text": req.system, "cache_control": {"type": "ephemeral"}}]
    if req.tools:
        params["tools"] = [{"name": t["name"], "description": t["description"], "input_schema": t["schema"], "eager_input_streaming": True}
                           for t in req.tools]
    if caps["adaptive"]:
        thinking: dict = {"type": "adaptive"}
        display = req.display
        if display == "updates" and not caps["updates"]:
            display = "summarized"
        if display:
            thinking["display"] = display
        if display == "updates":
            betas.append(BETA_UPDATES)
        params["thinking"] = thinking
        if req.effort:
            params["output_config"] = {"effort": req.effort}
    if caps["fallbacks"] and p.get("fallbacks", True) is not False and not p.get("url"):
        params["fallbacks"] = "default"
        betas.append(BETA_FALLBACK)
    return params, betas


# —— Claude → 中立格式 ———————————————————————————————————————————————————

def from_anthropic(final, model_id: str, provider_name: str) -> dict:
    content: list[dict] = []
    raw: list[dict] = []
    for b in final.content or []:
        t = getattr(b, "type", "")
        if t == "text":
            content.append({"type": "text", "text": b.text or ""})
            raw.append({"type": "text", "text": b.text or ""})
        elif t == "thinking":
            content.append({"type": "thinking", "text": b.thinking or "", "sig": b.signature})
            raw.append({"type": "thinking", "thinking": b.thinking or "", "signature": b.signature})
        elif t == "redacted_thinking":
            content.append({"type": "thinking", "text": "", "redacted": True})
            raw.append({"type": "redacted_thinking", "data": b.data})
        elif t == "tool_use":
            args = b.input if isinstance(b.input, dict) else {}
            content.append({"type": "tool_call", "id": b.id, "name": b.name, "args": args})
            raw.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
        else:  # fallback 等别的块：原样留着发回去
            raw.append(b.model_dump(mode="json", exclude_none=True))
    u = final.usage
    usage = {"in": int(getattr(u, "input_tokens", 0) or 0), "out": int(getattr(u, "output_tokens", 0) or 0),
             "cache_read": int(getattr(u, "cache_read_input_tokens", 0) or 0), "cache_write": int(getattr(u, "cache_creation_input_tokens", 0) or 0)}
    return {"role": "assistant", "content": content, "api": models.ANTHROPIC, "provider": provider_name, "model": getattr(final, "model", None) or model_id,
            "stop": STOP.get(str(final.stop_reason or ""), "end"), "usage": usage, "raw": raw}


async def stream(p: dict, model_id: str, req) -> AsyncIterator[dict]:
    """流式：{"type": "text" | "thinking", "text"}、{"type": "tool_start", "id", "name"}，最后 {"type": "done", "message"}。出错抛 EngineError。"""
    import anthropic
    client = make_client(p)
    params, betas = build(model_id, req, p)
    try:
        ctx = client.beta.messages.stream(**params, betas=betas, timeout=req.timeout) if betas else \
            client.messages.stream(**params, timeout=req.timeout)
        async with ctx as s:
            async for ev in s:
                t = ev.type
                if t == "content_block_start" and getattr(ev.content_block, "type", "") == "tool_use":
                    yield {"type": "tool_start", "id": ev.content_block.id, "name": ev.content_block.name}
                elif t == "content_block_delta":
                    d = ev.delta
                    if d.type == "text_delta" and d.text:
                        yield {"type": "text", "text": d.text}
                    elif d.type == "thinking_delta" and d.thinking:
                        yield {"type": "thinking", "text": d.thinking}
            final = await s.get_final_message()
    except anthropic.APITimeoutError as e:
        raise EngineError("Claude timed out", kind="timeout", retryable=True) from e
    except anthropic.APIConnectionError as e:
        raise EngineError(f"can't reach Claude: {type(e).__name__}", retryable=True) from e
    except anthropic.APIStatusError as e:
        st = int(e.status_code)
        msg = str(getattr(e, "message", "") or e)[:240]
        raise EngineError(f"Claude HTTP {st}: {msg}", status=st, retryable=st in (408, 409, 429, 529) or st >= 500,
                          kind="config" if st in (401, 403) else "http") from e
    except ValueError as e:  # 工具参数的 JSON 坏到 SDK 都解析不了：这一次作废，可以再来
        raise EngineError(f"Claude sent a tool input that isn't JSON: {str(e)[:120]}", kind="output", retryable=True) from e
    yield {"type": "done", "message": from_anthropic(final, model_id, p["name"])}
