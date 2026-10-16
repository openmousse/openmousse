"""接 OpenAI 兼容的 POST <url>/chat/completions（Kimi、DeepSeek、Qwen、Gemini、OpenRouter、Ollama、LM Studio、vLLM…）：httpx 流式，SSE 自己解析。

各家不一样的地方：
  - 思考：DeepSeek / Kimi / Qwen 在 delta.reasoning_content 里，OpenRouter 叫 reasoning。同一轮工具来回中间要原样带回去
    （DeepSeek 思考模式 + 工具、Kimi 的 thinking 都要求），之前几轮的不带（DeepSeek 带了会报错）。
  - 用量：prompt_tokens 含命中缓存的那部分（DeepSeek 叫 prompt_cache_hit_tokens，OpenAI 在 prompt_tokens_details.cached_tokens）。
  - 有的不认 stream_options / response_format / reasoning_effort / max_tokens：报 400 且提到这个字段，就去掉它再试一次。
  - 工具调用的 id 各家写法不同（Kimi 是 functions.名字:序号），原样存着、原样发回给同一家。
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

import httpx

from . import models
from .errors import EngineError
from .session import text_of, tool_calls

FINISH = {"stop": "end", "length": "length", "tool_calls": "tool", "function_call": "tool", "content_filter": "refusal"}
OPTIONAL = ("stream_options", "response_format", "reasoning_effort", "max_tokens", "max_completion_tokens")
RETRY_DELAYS = (1.0, 3.0)
TRANSPORT: httpx.AsyncBaseTransport | None = None  # 测试时换成 httpx.MockTransport


def _user(m: dict):
    parts = m.get("content") or []
    if all(b.get("type") == "text" for b in parts):
        return "\n\n".join(b.get("text") or "" for b in parts) or "…"
    out = []
    for b in parts:
        if b.get("type") == "text" and b.get("text"):
            out.append({"type": "text", "text": b["text"]})
        elif b.get("type") == "image" and b.get("data"):
            out.append({"type": "image_url", "image_url": {"url": f"data:{b.get('mime') or 'image/png'};base64,{b['data']}"}})
    return out or "…"


def to_openai(system: str, messages: list[dict], provider_name: str, model_id: str) -> list[dict]:
    out: list[dict] = [{"role": "system", "content": system}] if system else []
    last_user = max((i for i, m in enumerate(messages) if m.get("role") == "user"), default=-1)
    for i, m in enumerate(messages):
        role = m.get("role")
        if role == "user":
            out.append({"role": "user", "content": _user(m)})
        elif role == "assistant":
            if (m.get("api") == models.OPENAI and m.get("provider") == provider_name and m.get("model") == model_id
                    and isinstance(m.get("raw"), dict)):
                r = dict(m["raw"])
                if i < last_user:  # 之前几轮的思考不带回去；这一轮工具来回中间的要带
                    r.pop("reasoning_content", None)
                    r.pop("reasoning", None)
                out.append({"role": "assistant", **r})
                continue
            text, calls = text_of(m), tool_calls(m)
            if not text and not calls:
                continue
            msg: dict = {"role": "assistant", "content": text}
            if calls:
                msg["tool_calls"] = [{"id": c.get("id") or "", "type": "function",
                                      "function": {"name": c.get("name") or "", "arguments": json.dumps(c.get("args") or {}, ensure_ascii=False)}}
                                     for c in calls]
            out.append(msg)
        elif role == "tool":
            out.append({"role": "tool", "tool_call_id": m.get("id") or "", "content": text_of(m) or "(empty)"})
        elif role == "system":
            t = text_of(m).strip()
            if t:
                out.append({"role": "system", "content": t})
    return out


def body_of(p: dict, model_id: str, req) -> dict:
    body: dict = {"model": model_id, "messages": to_openai(req.system, req.messages, p["name"], model_id), "stream": True}
    if p.get("stream_usage", True) is not False:
        body["stream_options"] = {"include_usage": True}
    if req.tools:
        body["tools"] = [{"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["schema"]}}
                         for t in req.tools]
    if req.max_tokens:  # 不给就按各家默认（各家上限不一样，给大了会 400）
        body[str(p.get("max_tokens_field") or "max_tokens")] = int(req.max_tokens)
    if req.json and not req.tools and p.get("json_mode", True) is not False:
        body["response_format"] = {"type": "json_object"}
    if req.effort and p.get("reasoning_effort"):
        body["reasoning_effort"] = {"xhigh": "high", "max": "high"}.get(req.effort, req.effort)
    return body


def _usage(u: dict) -> dict:
    u = u or {}
    prompt = int(u.get("prompt_tokens") or 0)
    cached = int(u.get("prompt_cache_hit_tokens") or (u.get("prompt_tokens_details") or {}).get("cached_tokens") or u.get("cached_tokens") or 0)
    return {"in": max(0, prompt - cached), "out": int(u.get("completion_tokens") or 0), "cache_read": cached, "cache_write": 0}


def _message(p: dict, model_id: str, text: str, reasoning: str, rkey: str, calls: dict[int, dict], finish: str | None, usage: dict) -> dict:
    content: list[dict] = []
    if reasoning:
        content.append({"type": "thinking", "text": reasoning})
    if text:
        content.append({"type": "text", "text": text})
    raw_calls = []
    for _, c in sorted(calls.items()):
        try:
            args = json.loads(c["args"] or "{}")
            bad = None if isinstance(args, dict) else c["args"]
        except ValueError:
            args, bad = {}, c["args"]
        block = {"type": "tool_call", "id": c["id"] or f"call_{len(raw_calls)}", "name": c["name"], "args": args if isinstance(args, dict) else {}}
        if bad is not None:
            block["bad_args"] = str(bad)[:2000]
        content.append(block)
        raw_calls.append({"id": block["id"], "type": "function", "function": {"name": c["name"], "arguments": c["args"] or "{}"}})
    raw: dict = {"content": text}
    if raw_calls:
        raw["tool_calls"] = raw_calls
    if reasoning:
        raw[rkey] = reasoning
    stop = FINISH.get(str(finish or ""), "end")
    if raw_calls and stop == "end":
        stop = "tool"  # 有的接口带着工具调用也说 stop
    return {"role": "assistant", "content": content, "api": models.OPENAI, "provider": p["name"], "model": model_id, "stop": stop,
            "usage": _usage(usage), "raw": raw}


async def stream(p: dict, model_id: str, req) -> AsyncIterator[dict]:
    """流式：{"type": "text" | "thinking", "text"}、{"type": "tool_start", "id", "name"}，最后 {"type": "done", "message"}。出错抛 EngineError。"""
    body = body_of(p, model_id, req)
    headers = {"Content-Type": "application/json", **{str(k): str(v) for k, v in (p.get("headers") or {}).items()}}
    if p.get("key"):
        headers["Authorization"] = f"Bearer {p['key']}"
    elif p.get("key_env", "x") != "":
        raise EngineError(f"no API key for {p['name']} (put {p.get('key_env') or p['name'].upper() + '_API_KEY'} in the .env)", kind="config")
    url = f"{p['url']}/chat/completions"
    dropped: set[str] = set()
    attempt = 0
    while True:
        started = False
        text: list[str] = []
        reasoning: list[str] = []
        rkey = "reasoning_content"
        calls: dict[int, dict] = {}
        finish: str | None = None
        usage: dict = {}
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(req.timeout, connect=10), transport=TRANSPORT) as client:
                async with client.stream("POST", url, headers=headers, json=body) as r:
                    if r.status_code != 200:
                        err = (await r.aread()).decode("utf8", "replace")
                        bad = [f for f in OPTIONAL if f in body and f not in dropped and f in err]
                        if r.status_code in (400, 422) and bad:  # 不认这个字段：去掉再来
                            for f in bad:
                                body.pop(f, None)
                                dropped.add(f)
                            continue
                        raise EngineError(f"{p['name']} HTTP {r.status_code}: {err[:240]}", status=r.status_code,
                                          retryable=r.status_code in (408, 409, 429) or r.status_code >= 500,
                                          kind="config" if r.status_code in (401, 403) else "http")
                    if "text/event-stream" not in r.headers.get("content-type", ""):
                        j = json.loads((await r.aread()).decode("utf8", "replace") or "{}")
                        if j.get("error"):
                            e = j["error"]
                            raise EngineError(f"{p['name']}: {str(e.get('message') if isinstance(e, dict) else e)[:240]}")
                        ch = (j.get("choices") or [{}])[0]
                        msg = ch.get("message") or {}
                        if msg.get("content"):
                            text.append(msg["content"])
                            yield {"type": "text", "text": msg["content"]}
                        rc = msg.get("reasoning_content") or msg.get("reasoning")
                        if isinstance(rc, str) and rc:
                            rkey = "reasoning_content" if msg.get("reasoning_content") else "reasoning"
                            reasoning.append(rc)
                        for i, tc in enumerate(msg.get("tool_calls") or []):
                            f = tc.get("function") or {}
                            calls[i] = {"id": tc.get("id") or "", "name": f.get("name") or "", "args": f.get("arguments") or ""}
                        finish, usage = ch.get("finish_reason"), j.get("usage") or {}
                    else:
                        async for line in r.aiter_lines():
                            if not line.startswith("data:"):
                                continue
                            payload = line[5:].strip()
                            if payload == "[DONE]":
                                break
                            try:
                                j = json.loads(payload)
                            except ValueError:
                                continue
                            if j.get("error"):
                                e = j["error"]
                                raise EngineError(f"{p['name']}: {str(e.get('message') if isinstance(e, dict) else e)[:240]}", retryable=not started)
                            if j.get("usage"):
                                usage = j["usage"]
                            for ch in j.get("choices") or []:
                                d = ch.get("delta") or {}
                                if isinstance(d.get("content"), str) and d["content"]:
                                    started = True
                                    text.append(d["content"])
                                    yield {"type": "text", "text": d["content"]}
                                for k in ("reasoning_content", "reasoning"):
                                    if isinstance(d.get(k), str) and d[k]:
                                        started, rkey = True, k
                                        reasoning.append(d[k])
                                        yield {"type": "thinking", "text": d[k]}
                                        break
                                for tc in d.get("tool_calls") or []:
                                    idx = int(tc.get("index") or 0)
                                    cur = calls.setdefault(idx, {"id": "", "name": "", "args": ""})
                                    f = tc.get("function") or {}
                                    if tc.get("id") and not cur["id"]:
                                        cur["id"] = str(tc["id"])
                                    if f.get("name") and not cur["name"]:
                                        cur["name"] = str(f["name"])
                                        started = True
                                        yield {"type": "tool_start", "id": cur["id"], "name": cur["name"]}
                                    if isinstance(f.get("arguments"), str):
                                        cur["args"] += f["arguments"]
                                if ch.get("finish_reason"):
                                    finish = ch["finish_reason"]
        except httpx.TimeoutException as e:
            if not started and attempt < len(RETRY_DELAYS):
                await asyncio.sleep(RETRY_DELAYS[attempt])
                attempt += 1
                continue
            raise EngineError(f"{p['name']} timed out", kind="timeout", retryable=True) from e
        except httpx.HTTPError as e:
            if not started and attempt < len(RETRY_DELAYS):
                await asyncio.sleep(RETRY_DELAYS[attempt])
                attempt += 1
                continue
            raise EngineError(f"can't reach {p['name']}: {type(e).__name__}", retryable=True) from e
        except EngineError as e:
            if e.retryable and not started and attempt < len(RETRY_DELAYS):
                await asyncio.sleep(RETRY_DELAYS[attempt])
                attempt += 1
                continue
            raise
        break
    yield {"type": "done", "message": _message(p, model_id, "".join(text), "".join(reasoning), rkey, calls, finish, usage)}
