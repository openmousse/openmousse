"""接 Claude：官方 anthropic SDK（1.x），只用 API 密钥（Anthropic 条款：第三方产品不许借 Claude 订阅登录，见 engine/README）。

跟着 2026 年的接口走（claude-api 技能 2026-10 的说法；10/16 又对照了 pi、OpenClaw、Hermes 的源码，见 ~/openmousse-wt/engine/research/）：
  - 思考：新型号一律 thinking adaptive，深浅靠 output_config.effort（Opus 5.5 关不掉思考，默认 medium）；老型号什么都不带。
  - 回放：assistant 的内容块（带思考签名）原样存在 raw 里，下一轮原样发回去。换了型号也照发（官方说法：接口自己丢掉读不了的，
    自己删反而可能 400；pi、OpenClaw 是删的，这里跟官方）。只有两处动它：空白的文字块不发（接口不收）；签名被拒（400）时
    把思考块全拿掉再试一次（官方给的一次性补救）。
  - 前缀：会话只往后补（session.py）；再带上 thinking.block_binding = drop_block（beta），万一前缀变了只丢掉对不上的思考，
    不至于每轮都 400（pi 也这么做）。
  - 对话中间的规矩用 role=system 的消息（支持的型号）；不支持的折成 <system-reminder> 文字块放进前面那条 user。
  - 工具结果里的图（⑫b，engine/images.py 已经读回 base64）：tool_result 的 content 里放 image 块；只有图没有字的前面垫一句
    「(see attached image)」（pi 的 anthropic-messages.ts，earendil-works/pi commit 8efd314f，MIT，见 THIRD_PARTY_NOTICES）。
  - 缓存：开头的 system 打一个断点 + 顶层自动缓存对话尾巴；主人、赛博回合隔几分钟才一句，用 1 小时（两处都写）。
  - 拒答：stop_reason = refusal；Opus 5.5 / Fable 5.1 / Opus 5 / Sonnet 5.5 默认带服务端的 fallbacks: "default"（拒答时接口自己换型号）。
    换过型号的回复：分界之前的思考、工具调用不再发回去，fallback 块本身也不留（官方说可留可删）。
  - 流到一半 overloaded（HTTP 200 之后才来的错）算能重试。工具参数流式（eager_input_streaming），跑之前 loop.py 再按 schema 查。
  - 强制调工具（tool_choice any / tool）新型号会 400：不用。
"""
from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import re
from collections.abc import AsyncIterator

from . import models
from .errors import EngineError
from .session import repair, text_of

STOP = {"end_turn": "end", "stop_sequence": "end", "tool_use": "tool", "max_tokens": "length", "model_context_window_exceeded": "length",
        "refusal": "refusal", "pause_turn": "pause"}
DEFAULT_MAX_TOKENS = 32000  # 思考也算在里面（Opus 5.5 关不掉思考），流式不怕超时，给宽一点
BETA_UPDATES = "thinking-display-updates-2026-08-18"
BETA_FALLBACK = "server-side-fallback-2026-07-01"
BETA_BINDING = "thinking-binding-controls-2026-08-01"
TOOL_ID = re.compile(r"[^a-zA-Z0-9_-]")

_clients: dict[tuple, object] = {}
_sdk = None


async def sdk():
    """anthropic 包：第一次用到时在后台线程里导入（导入要一两秒、六七十 MB，不能卡住服务的事件循环；不用 Claude 就永远不导入）。"""
    global _sdk
    if _sdk is None:
        try:
            _sdk = await asyncio.to_thread(importlib.import_module, "anthropic")
        except ImportError as e:
            raise EngineError("the anthropic package isn't installed (pip install -r server/requirements.txt)", kind="config") from e
    return _sdk


def make_client(p: dict):
    """AsyncAnthropic（按密钥 + 地址缓存；调之前 sdk() 已经导入好了）。测试时换掉这个函数，塞一个假的 http_client。"""
    import anthropic
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
    """工具调用的 id：Claude 只认字母数字、下划线、连字符，最长 64（Kimi 的 functions.x:0 这种换掉）。两边用同一个函数，配得上。"""
    t = TOOL_ID.sub("_", str(s or "")) or "call"
    return t if len(t) <= 64 else t[:51] + "_" + hashlib.sha1(t.encode()).hexdigest()[:12]


def _media(blocks: list[dict]) -> list[dict]:
    out: list[dict] = []
    for b in blocks or []:
        if b.get("type") == "text" and str(b.get("text") or "").strip():
            out.append({"type": "text", "text": b["text"]})
        elif b.get("type") == "image" and b.get("data"):
            out.append({"type": "image", "source": {"type": "base64", "media_type": b.get("mime") or "image/png", "data": b["data"]}})
        elif b.get("type") == "document" and b.get("data"):  # 第 3 批：PDF（engine/pdf.py 读回的 base64）原样交给 Claude（user 和 tool_result 里都收）
            out.append({"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": b["data"]},
                        **({"title": str(b["title"])[:200]} if b.get("title") else {})})
    return out


def _assistant(m: dict, strip_thinking: bool = False) -> list[dict]:
    if m.get("api") == models.ANTHROPIC and isinstance(m.get("raw"), list) and m["raw"]:
        # Claude 自己回的：原样发回去（思考块带签名，一个字都不能动）；只去掉空白的文字块（接口不收）
        return [b for b in m["raw"] if not (b.get("type") == "text" and not str(b.get("text") or "").strip())
                and not (strip_thinking and b.get("type") in ("thinking", "redacted_thinking"))]
    out: list[dict] = []
    for b in m.get("content") or []:
        t = b.get("type")
        if t == "text" and str(b.get("text") or "").strip():
            out.append({"type": "text", "text": b["text"]})
        elif t == "tool_call":
            out.append({"type": "tool_use", "id": _tid(b.get("id") or ""), "name": b.get("name") or "", "input": b.get("args") or {}})
        elif t == "thinking" and m.get("api") == models.ANTHROPIC and b.get("sig") and not strip_thinking:
            out.append({"type": "thinking", "thinking": b.get("text") or "", "signature": b["sig"]})
        # 别家的思考（没有签名）不发：Claude 不认，也不该当成它自己想过的
    return out


def to_anthropic(messages: list[dict], caps: dict, strip_thinking: bool = False) -> list[dict]:
    """中立格式 → messages。先 repair（补没结果的工具调用、去掉出错的回复），同一份历史每次翻出来一模一样（缓存和思考签名都靠这个）。"""
    msgs = repair(messages)
    out: list[dict] = []
    pending: list[str] = []  # 前面还没有 user 的 system 条目：并进下一条 user 的开头
    for i, m in enumerate(msgs):
        role = m.get("role")
        if role == "user":
            blocks = _media(m.get("content") or []) or [{"type": "text", "text": "…"}]
            if pending:
                blocks = [{"type": "text", "text": "<system-reminder>\n" + "\n\n".join(pending) + "\n</system-reminder>"}, *blocks]
                pending = []
            out.append({"role": "user", "content": blocks})
        elif role == "tool":
            media = _media(m.get("content") or [])
            if media and all(b["type"] == "image" for b in media):  # 只有图没有字：前面垫一句（pi 的做法）
                media.insert(0, {"type": "text", "text": "(see attached image)"})
            block: dict = {"type": "tool_result", "tool_use_id": _tid(m.get("id") or ""), "content": media or "(empty)"}
            if m.get("error"):
                block["is_error"] = True
            last = out[-1] if out else None
            if last and last["role"] == "user" and all(isinstance(b, dict) and b.get("type") == "tool_result" for b in last["content"]):
                last["content"].append(block)  # 同一批工具结果放进同一条 user（拆开会让模型以后不再并行调工具）
            else:
                out.append({"role": "user", "content": [block]})
        elif role == "assistant":
            content = _assistant(m, strip_thinking)
            if content:
                out.append({"role": "assistant", "content": content})
        elif role == "system":
            text = text_of(m).strip()
            if not text:
                continue
            nxt = next((x.get("role") for x in msgs[i + 1:] if x.get("role") != "system"), None)
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


def build(model_id: str, req, p: dict, strip_thinking: bool = False) -> tuple[dict, list[str]]:
    """→ (请求参数, betas)。"""
    caps = models.anthropic_caps(model_id)
    cache: dict = {"type": "ephemeral", **({"ttl": "1h"} if req.cache_ttl == "1h" else {})}
    params: dict = {"model": model_id, "max_tokens": int(req.max_tokens or DEFAULT_MAX_TOKENS),
                    "messages": to_anthropic(req.messages, caps, strip_thinking)}
    if getattr(req, "cache_tail", True):  # 一次性的问答不缓存尾巴：资料每次都不一样，写进去也读不到，白付写入的钱
        params["cache_control"] = cache
    betas: list[str] = []
    if req.system:
        params["system"] = [{"type": "text", "text": req.system, "cache_control": cache}]
    if req.tools:  # 按名字排：工具表是前缀的一部分，顺序稳定才吃得到缓存（OpenClaw 的做法）
        params["tools"] = [{"name": t["name"], "description": t["description"], "input_schema": t["schema"], "eager_input_streaming": True}
                           for t in sorted(req.tools, key=lambda t: t["name"])]
    if caps["adaptive"]:
        thinking: dict = {"type": "adaptive"}
        display = req.display
        if display == "updates" and not caps["updates"]:
            display = "summarized"
        if display:
            thinking["display"] = display
        if display == "updates":
            betas.append(BETA_UPDATES)
        binding = p.get("block_binding", "drop_block")
        if binding in ("drop_block", "error") and not p.get("url"):
            thinking["block_binding"] = {"prefix_mismatch_behavior": binding}
            betas.append(BETA_BINDING)
        params["thinking"] = thinking
        if req.effort:
            params["output_config"] = {"effort": req.effort}
    if caps["fallbacks"] and p.get("fallbacks", True) is not False and not p.get("url"):
        params["fallbacks"] = "default"
        betas.append(BETA_FALLBACK)
    return params, betas


# —— Claude → 中立格式 ———————————————————————————————————————————————————

def _block(b) -> dict:
    t = getattr(b, "type", "")
    if t == "text":
        return {"type": "text", "text": b.text or ""}
    if t == "thinking":
        return {"type": "thinking", "thinking": b.thinking or "", "signature": b.signature}
    if t == "redacted_thinking":
        return {"type": "redacted_thinking", "data": b.data}
    if t == "tool_use":
        return {"type": "tool_use", "id": b.id, "name": b.name, "input": b.input}
    return b.model_dump(mode="json", exclude_none=True)


def from_anthropic(final, model_id: str, provider_name: str) -> dict:
    blocks = [_block(b) for b in final.content or []]
    cut = max((i for i, b in enumerate(blocks) if b.get("type") == "fallback"), default=-1)
    if cut >= 0:  # 服务端换过型号：分界之前只留文字（官方：思考、工具调用不再发回去），fallback 块本身不留
        blocks = [b for i, b in enumerate(blocks) if i > cut or (i < cut and b.get("type") == "text")]
    content: list[dict] = []
    raw: list[dict] = []
    for b in blocks:
        t = b.get("type")
        if t == "text":
            content.append({"type": "text", "text": b["text"]})
        elif t == "thinking":
            content.append({"type": "thinking", "text": b["thinking"], "sig": b["signature"]})
        elif t == "redacted_thinking":
            content.append({"type": "thinking", "text": "", "redacted": True})
        elif t == "tool_use":
            content.append({"type": "tool_call", "id": b["id"], "name": b["name"], "args": b["input"] if isinstance(b["input"], dict) else {}})
        raw.append(b)
    u = final.usage
    usage = {"in": int(getattr(u, "input_tokens", 0) or 0), "out": int(getattr(u, "output_tokens", 0) or 0),
             "cache_read": int(getattr(u, "cache_read_input_tokens", 0) or 0), "cache_write": int(getattr(u, "cache_creation_input_tokens", 0) or 0)}
    msg = {"role": "assistant", "content": content, "api": models.ANTHROPIC, "provider": provider_name, "model": model_id,
           "stop": STOP.get(str(final.stop_reason or ""), "end"), "usage": usage, "raw": raw}
    served = getattr(final, "model", None)
    if served and served != model_id:
        msg["served"] = served
    return msg


def _has_thinking(params: dict) -> bool:
    return any(isinstance(b, dict) and b.get("type") in ("thinking", "redacted_thinking")
               for m in params["messages"] if m["role"] == "assistant" for b in m["content"])


async def stream(p: dict, model_id: str, req) -> AsyncIterator[dict]:
    """流式：{"type": "text" | "thinking", "text"}、{"type": "tool_start", "id", "name"}，最后 {"type": "done", "message"}。出错抛 EngineError。"""
    anthropic = await sdk()
    client = make_client(p)
    if getattr(req, "quick", False):  # 备用链里还有能换的：SDK 自己不重试，交给 loop 的重试和备用链（providers.Request.quick）
        client = client.with_options(max_retries=0)
    strip = False
    while True:
        params, betas = build(model_id, req, p, strip)
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
            if st == 400 and "signature" in msg and not strip and _has_thinking(params):
                strip = True  # 思考签名被拒：拿掉全部思考块再试一次（官方的一次性补救；400 在任何输出之前，不会重复吐字）
                print(f"[engine] Claude 拒了思考签名（多半是前缀变了），这一次去掉思考块重发：{msg[:160]}")
                continue
            overloaded = "overloaded" in msg.lower() or st == 200  # HTTP 200 之后流里来的错（多半是 overloaded_error）
            raise EngineError(f"Claude HTTP {st}: {msg}", status=st, retryable=overloaded or st in (408, 409, 429, 529) or st >= 500,
                              kind="config" if st in (401, 403) else "http") from e
        except ValueError as e:  # 工具参数的 JSON 坏到 SDK 都解析不了：这一次作废，可以再来
            raise EngineError(f"Claude sent a tool input that isn't JSON: {str(e)[:120]}", kind="output", retryable=True) from e
        break
    yield {"type": "done", "message": from_anthropic(final, model_id, p["name"])}
