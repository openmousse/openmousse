"""接 OpenAI 的 Responses API（POST <url>/responses）：用户自己的 ChatGPT 套餐（chatgpt/<型号>，令牌由 engine/chatgpt_plan.py 给，
providers.py 填进 p["key"]）和普通 OpenAI 密钥（server.json 的 engine.providers.<名>.api 写 "openai-responses"；不写 api 的照旧走
/chat/completions）都走这里。httpx 流式，SSE 自己解析。

请求（10/17 对照官方 SIWC 文档 developers.openai.com/siwc 的 models-and-inference、preview-limitations，OpenClaw 4cb8e645 的
extensions/openai/responses-stream.runtime.ts、packages/ai 的 openai-responses-shared.ts / openai-responses-replay-*.ts，
Codex 5009a99d 的 codex-rs/core/src/client.rs；只借做法，没拿代码）：
  instructions = 会话开头；input = 整段历史（store = false：服务端不存，不用 previous_response_id，每次自己带上）；stream = true。
  对话中间的 system 写成 role developer 的消息（这条路不收 role system 的条目）。
  工具：普通的 function（按名字排：工具表是前缀的一部分，顺序稳定才吃得到缓存），tool_choice auto，parallel_tool_calls。
  思考：reasoning = {"summary": "auto"（要思考摘要的回合）, "effort": low / medium / high / xhigh（max 当 xhigh）}，
    include reasoning.encrypted_content：回来的思考项（加密的）原样存在 raw 里，下一轮发回给同一家的同一型号，不带 id
    （store = false 时 id 什么都指不到；OpenClaw 的 replayResponsesItemIds: false）。换了型号、别家的回复：只发文字和工具调用。
    加密的思考被拒（invalid_encrypted_content，还没出字）：去掉思考再发一次（OpenClaw 的做法，和 Claude 的签名补救一样）。
  JSON：text.format = json_object（没有工具时）。max_output_tokens：普通密钥照给；套餐（p["plan"]）不带，这条路不收
    （官方列的不支持字段：max_output_tokens、temperature、top_p、metadata、user、prompt_cache_retention……这里本来就不发别的）。
  不认的可选字段（reasoning、include、text、max_output_tokens、parallel_tool_calls、tool_choice）报 400 点了名：去掉再来一次。
流里的事件：response.output_text.delta → 字；response.reasoning_summary_text.delta / reasoning_text.delta → 思考（几段摘要之间空一行）；
  response.output_item.added（function_call）→ 开始写一个工具调用，function_call_arguments.delta / done 拼参数；response.refusal.delta
  → 拒答的话（照样给人看）；response.completed → 收尾（output 是完整的输出项，带加密的思考；usage）；response.incomplete → 写到上限
  （max_output_tokens）/ 被拦（content_filter）；response.failed、error → 出错。只认 completed / incomplete 收尾：流断了没收尾算没回完
  （官方：没看到 response.completed 不算成功）。
出错（EngineError：kind / retryable / status 给备用链用，engine/fallback.py）：
  HTTP 不是 200：错误体可能是 {"error": {"code", "message", "param"}}，也可能是套餐准入那一层的 {"detail": "…"}，两种都认；
  401 / 403 → config；429 → http（套餐的用量到顶 subscription_sharing_usage_limit_exceeded 不重试：官方说先停，去 ChatGPT 设置 → 用量看）；
  408 / 409 / 5xx → 能重试（套餐的 usage_unavailable、user_unavailable 是 503）；别的 400 不重试。流里来的 response.failed / error 按
  错误码换成状态码（官方 errors-and-recovery 的表）再照上面分。请求 id（x-request-id）记进错误的原话，方便查。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import AsyncIterator

import httpx

from i18n import L

from .errors import EngineError
from .session import repair, text_of, tool_calls

API = "openai-responses"
RETRY_DELAYS = (1.0, 3.0)
EFFORTS = {"max": "xhigh"}          # 我们的档位 → reasoning.effort（low / medium / high / xhigh 同名）；provider 的 effort_map 能改
OPTIONAL = ("reasoning", "include", "text", "max_output_tokens", "parallel_tool_calls", "tool_choice")
USAGE_URL = "https://chatgpt.com/settings/usage"
# 错误码 → HTTP 状态（流里来的错只有码）：套餐这条路的照官方 errors-and-recovery 的表，后面几个是 Responses 平常的
CODES = {"subscription_sharing_user_not_eligible": 403, "subscription_sharing_usage_limit_exceeded": 429,
         "subscription_sharing_usage_unavailable": 503, "subscription_sharing_unsupported_capability": 400,
         "subscription_sharing_route_not_supported": 403, "subscription_sharing_invalid_user": 401,
         "chatpass_v2_scope_not_authorized": 403, "chatpass_v2_invalid_authorization_context": 403,
         "subscription_sharing_user_unavailable": 503,
         "rate_limit_exceeded": 429, "insufficient_quota": 429, "server_error": 500, "server_is_overloaded": 503,
         "model_not_found": 404, "invalid_api_key": 401, "context_length_exceeded": 400, "invalid_encrypted_content": 400,
         "invalid_prompt": 400, "vector_store_timeout": 504}
NO_RETRY = {"subscription_sharing_usage_limit_exceeded", "insufficient_quota"}  # 429 里不该马上再试的：额度没了，再试还是没有
TRANSPORT: httpx.AsyncBaseTransport | None = None  # 测试时换成 httpx.MockTransport


# —— 中立格式 → Responses 的 input ——————————————————————————————————————————————

def _cid(s) -> str:
    """工具调用的 call_id：只留字母数字、下划线、连字符，最长 64（别家的 id 像 functions.x:0）。调用和结果两边用同一个函数，配得上。"""
    t = re.sub(r"[^A-Za-z0-9_-]", "_", str(s or "")) or "call"
    return t if len(t) <= 64 else t[:51] + "_" + hashlib.sha1(t.encode()).hexdigest()[:12]


def _image(b: dict) -> dict:
    return {"type": "input_image", "image_url": f"data:{b.get('mime') or 'image/png'};base64,{b['data']}", "detail": "auto"}


def _user(m: dict, p: dict) -> list[dict]:
    out: list[dict] = []
    for b in m.get("content") or []:
        if b.get("type") == "text" and b.get("text"):
            out.append({"type": "input_text", "text": b["text"]})
        elif b.get("type") == "image" and b.get("data"):
            out.append(_image(b) if p.get("images", True) is not False else
                       {"type": "input_text", "text": "[An image was attached here, but this model can't read images.]"})
    return out or [{"type": "input_text", "text": "…"}]


def same_model(m: dict, p: dict, model_id: str) -> bool:
    """这条回复是不是这一家的这个型号自己回的（它的思考项才发回去）。"""
    return m.get("api") == API and m.get("provider") == p["name"] and m.get("model") == model_id and isinstance(m.get("raw"), list)


def _replay(items: list, strip: bool) -> list[dict]:
    """同一型号自己的输出项原样发回（去掉 id、status 这类只在服务端有意义的）：思考项要有加密内容才发（没有的只是一个 id，store = false
    时指不到东西）；strip = 思考这一次都不发。"""
    out: list[dict] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        t = it.get("type")
        if t == "reasoning":
            enc = it.get("encrypted_content")
            if strip or not isinstance(enc, str) or not enc:
                continue
            out.append({"type": "reasoning", "summary": [s for s in it.get("summary") or [] if isinstance(s, dict)], "encrypted_content": enc})
        elif t == "message":
            parts = [{"type": "output_text", "text": c["text"], "annotations": []} for c in it.get("content") or []
                     if isinstance(c, dict) and c.get("type") == "output_text" and c.get("text")]
            if parts:
                out.append({"type": "message", "role": "assistant", "content": parts, "status": "completed"})
        elif t == "function_call":
            out.append({"type": "function_call", "call_id": _cid(it.get("call_id")), "name": str(it.get("name") or ""),
                        "arguments": it.get("arguments") if isinstance(it.get("arguments"), str) else "{}"})
        # 别的输出项（托管的工具、web_search_call……这里不用）不发
    return out


def to_input(messages: list[dict], p: dict, model_id: str, strip: bool = False) -> list[dict]:
    """中立格式 → input 条目。先 repair（补没结果的工具调用、去掉出错的回复），同一份历史每次翻出来一样。"""
    out: list[dict] = []
    for m in repair(messages):
        role = m.get("role")
        if role == "user":
            out.append({"role": "user", "content": _user(m, p)})
        elif role == "assistant":
            if same_model(m, p, model_id):
                out.extend(_replay(m["raw"], strip))
                continue
            text, calls = text_of(m), tool_calls(m)
            if text.strip():
                out.append({"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text, "annotations": []}],
                            "status": "completed"})
            for c in calls:  # 别家的思考不发：不认，也不该当成它自己想过的
                out.append({"type": "function_call", "call_id": _cid(c.get("id")), "name": str(c.get("name") or ""),
                            "arguments": json.dumps(c.get("args") or {}, ensure_ascii=False)})
        elif role == "tool":
            pics = [b for b in m.get("content") or [] if b.get("type") == "image" and b.get("data")]
            text = text_of(m) or ("(see attached image)" if pics else "(empty)")
            output: str | list = text
            if pics and p.get("images", True) is not False:
                output = [{"type": "input_text", "text": text}, *(_image(b) for b in pics)]
            out.append({"type": "function_call_output", "call_id": _cid(m.get("id")), "output": output})
        elif role == "system":
            t = text_of(m).strip()
            if t:
                out.append({"role": "developer", "content": [{"type": "input_text", "text": t}]})
    return out


def body_of(p: dict, model_id: str, req, strip: bool = False) -> dict:
    body: dict = {"model": model_id, "input": to_input(req.messages, p, model_id, strip), "stream": True, "store": False}
    if req.system:
        body["instructions"] = req.system
    if req.tools:  # 按名字排（OpenClaw 的做法：前缀稳定）
        body["tools"] = [{"type": "function", "name": t["name"], "description": t["description"], "parameters": t["schema"], "strict": False}
                         for t in sorted(req.tools, key=lambda t: t["name"])]
        body["tool_choice"] = "auto"
        body["parallel_tool_calls"] = True
    if p.get("reasoning", True) is not False:
        r: dict = {}
        if req.effort:
            emap = p.get("effort_map") if isinstance(p.get("effort_map"), dict) else EFFORTS
            r["effort"] = emap.get(req.effort, req.effort)
        if getattr(req, "display", None):
            r["summary"] = "auto"
        if r:
            body["reasoning"] = r
        body["include"] = ["reasoning.encrypted_content"]
    if req.max_tokens and not p.get("plan"):
        body["max_output_tokens"] = int(req.max_tokens)
    if req.json and not req.tools:
        body["text"] = {"format": {"type": "json_object"}}
    return body


# —— 出错 ——————————————————————————————————————————————————————————————————————

def error_of(raw: str) -> dict:
    """错误体 → {"code", "message", "param"}（缺的为 None）。认 {"error": {…}}、{"error": "…"}、套餐准入那一层的 {"detail": …}。"""
    try:
        j = json.loads(raw or "")
    except ValueError:
        return {"code": None, "message": (raw or "").strip()[:240] or None, "param": None}
    e = j.get("error") if isinstance(j, dict) else None
    if isinstance(e, dict):
        return {"code": e.get("code") if isinstance(e.get("code"), str) else None, "message": str(e.get("message") or "")[:400] or None,
                "param": e.get("param") if isinstance(e.get("param"), str) else None}
    if isinstance(j, dict) and isinstance(j.get("detail"), (str, dict, list)):
        d = j["detail"]
        return {"code": None, "message": (d if isinstance(d, str) else json.dumps(d, ensure_ascii=False))[:400], "param": None}
    return {"code": None, "message": str(e or raw)[:240] or None, "param": None}


def _optional_key(err: dict, body: dict, dropped: set[str]) -> list[str]:
    """400 里点了名、我们又带着的可选字段（reasoning.effort → reasoning；text.format → text）。"""
    named = (err.get("param") or "").split(".")[0]
    msg = err.get("message") or ""
    return [f for f in OPTIONAL if f in body and f not in dropped and (f == named or re.search(rf"['\"`]{re.escape(f)}(?:\.\w+)?['\"`]", msg))]


def _encrypted_refused(err: dict, body: dict) -> bool:
    """加密的思考被拒（解不开、对不上）而且这一次真带着思考项：去掉再发有用。别的提到 encrypted 的错（不认 include）不算。"""
    msg = (err.get("message") or "").lower()
    refused = err.get("code") == "invalid_encrypted_content" or "invalid_encrypted_content" in msg or (
        "encrypted" in msg and any(w in msg for w in ("decrypt", "could not be verified", "could not be parsed")))
    return refused and any(isinstance(it, dict) and it.get("type") == "reasoning" for it in body.get("input") or [])


class _StripAndRetry(Exception):
    """流里刚开头就说加密的思考不认：去掉思考再发一次（只一次）。"""


def make_error(p: dict, status: int | None, err: dict, *, started: bool = False, request_id: str | None = None) -> EngineError:
    """一个错（HTTP 的或流里来的）→ EngineError：kind / retryable / status 照文件开头。套餐的几种换成给人看的一句。"""
    code, msg = err.get("code"), err.get("message") or "error"
    st = status if status and status != 200 else CODES.get(code or "")
    text = f"{p['name']}{f' HTTP {st}' if st else ''}: {f'{code}: ' if code else ''}{msg}"[:400]
    if code == "subscription_sharing_usage_limit_exceeded":
        text = L(f"ChatGPT 套餐的用量到上限了（可能是你的套餐，也可能是给 OpenMousse 设的上限）。去 ChatGPT 设置 → 用量看看：{USAGE_URL}（{code}）",
                 f"ChatGPT plan usage limit reached (your plan's, or a limit set for OpenMousse). Review it in ChatGPT settings → Usage: "
                 f"{USAGE_URL} ({code})")
    elif code == "subscription_sharing_user_not_eligible":
        text = L(f"这个 ChatGPT 账号或工作区现在不能把套餐用在别的应用里（{code}）：换一个账号，或者用你自己的 API 密钥",
                 f"This ChatGPT account or workspace can't use its plan in other apps right now ({code}): connect another account, or use your "
                 "own API key")
    if request_id:
        text += f" (request {request_id[:80]})"
    if st in (401, 403):
        kind, retryable = "config", False
    else:
        kind = "http"
        retryable = st is not None and (st in (408, 409, 429) or st >= 500) and code not in NO_RETRY
    e = EngineError(text, kind=kind, retryable=retryable and not started, status=st)
    e.code = code  # type: ignore[attr-defined]  # 原样的错误码（engine/chatgpt_plan.py 记最近一次的错）
    return e


# —— Responses → 中立格式 ———————————————————————————————————————————————————————

def _usage(u: dict | None) -> dict:
    u = u or {}
    total = int(u.get("input_tokens") or 0)
    cached = int((u.get("input_tokens_details") or {}).get("cached_tokens") or 0)
    return {"in": max(0, total - cached), "out": int(u.get("output_tokens") or 0), "cache_read": cached, "cache_write": 0}


def message_of(p: dict, model_id: str, final: dict, items: list[dict], streamed_text: str, streamed_thinking: str) -> dict:
    """收尾的 response（completed / incomplete）→ 中立格式的 assistant 消息。output 以 response 里的为准，没有就用流里收齐的那些项。"""
    output = [it for it in (final.get("output") or items) if isinstance(it, dict)]
    content: list[dict] = []
    thinking = "\n\n".join(str(s.get("text") or "") for it in output if it.get("type") == "reasoning"
                           for s in it.get("summary") or [] if isinstance(s, dict) and s.get("text")) or streamed_thinking
    if thinking:
        content.append({"type": "thinking", "text": thinking})
    text = "".join(str(c.get("text") or "") for it in output if it.get("type") == "message"
                   for c in it.get("content") or [] if isinstance(c, dict) and c.get("type") == "output_text")
    refusal = "".join(str(c.get("refusal") or "") for it in output if it.get("type") == "message"
                      for c in it.get("content") or [] if isinstance(c, dict) and c.get("type") == "refusal")
    if not output:
        text = streamed_text
    if text or refusal:
        content.append({"type": "text", "text": text or refusal})
    calls = 0
    for it in output:
        if it.get("type") != "function_call" or not it.get("name"):
            continue
        calls += 1
        raw_args = it.get("arguments") if isinstance(it.get("arguments"), str) else "{}"
        try:
            args = json.loads(raw_args or "{}")
            bad = None if isinstance(args, dict) else raw_args
        except ValueError:
            args, bad = {}, raw_args
        block = {"type": "tool_call", "id": _cid(it.get("call_id") or it.get("id")), "name": str(it["name"]), "args": args if isinstance(args, dict) else {}}
        if bad is not None:
            block["bad_args"] = str(bad)[:2000]
        content.append(block)
    if final.get("status") == "incomplete":
        reason = str((final.get("incomplete_details") or {}).get("reason") or "")
        stop = "refusal" if reason == "content_filter" and not calls else "length"
    else:
        stop = "tool" if calls else "refusal" if refusal and not text else "end"
    return {"role": "assistant", "content": content, "api": API, "provider": p["name"], "model": model_id, "stop": stop,
            "usage": _usage(final.get("usage")), "raw": output}


# —— 流 ——————————————————————————————————————————————————————————————————————————

async def stream(p: dict, model_id: str, req) -> AsyncIterator[dict]:
    """流式：{"type": "text" | "thinking", "text"}、{"type": "tool_start", "id", "name"}，最后 {"type": "done", "message"}。出错抛 EngineError。"""
    strip = False
    body = body_of(p, model_id, req)
    headers = {"Content-Type": "application/json", "Accept": "text/event-stream",
               **{str(k): str(v) for k, v in (p.get("headers") or {}).items()}}
    if p.get("key"):
        headers["Authorization"] = f"Bearer {p['key']}"
    elif p.get("key_env", "x") != "":
        raise EngineError(f"no API key for {p['name']} (put {p.get('key_env') or p['name'].upper() + '_API_KEY'} in the .env)", kind="config")
    url = f"{p['url']}/responses"
    dropped: set[str] = set()
    attempt = 0
    retries = 0 if getattr(req, "quick", False) else len(RETRY_DELAYS)  # 备用链里还有能换的：这一层不重试（providers.Request.quick）
    while True:
        started = False
        text: list[str] = []
        thinking: list[str] = []
        calls: dict[str, dict] = {}       # 输出项 id → {"call_id", "name", "args"}
        items: dict[int, dict] = {}       # output_index → 做完的输出项（response.completed 没带 output 时用）
        final: dict | None = None
        summary_seen: set[tuple] = set()
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(req.timeout, connect=10), transport=TRANSPORT) as client:
                async with client.stream("POST", url, headers=headers, json=body) as r:
                    rid = r.headers.get("x-request-id")
                    if r.status_code != 200:
                        err = error_of((await r.aread()).decode("utf8", "replace"))
                        if r.status_code in (400, 422) and (bad := _optional_key(err, body, dropped)):  # 不认这个字段：去掉再来
                            for f in bad:
                                body.pop(f, None)
                                dropped.add(f)
                            continue
                        if r.status_code == 400 and not strip and _encrypted_refused(err, body):  # 加密的思考被拒：去掉思考再发一次
                            strip = True
                            print(f"[engine] {p['name']} 拒了加密的思考（{err.get('code') or 'invalid_encrypted_content'}），这一次去掉思考重发")
                            body["input"] = to_input(req.messages, p, model_id, strip=True)
                            continue
                        raise make_error(p, r.status_code, err, request_id=rid)
                    if "text/event-stream" not in r.headers.get("content-type", ""):  # 一次性的整段 JSON（有的代理、测试服）
                        j = json.loads((await r.aread()).decode("utf8", "replace") or "{}")
                        if isinstance(j.get("error"), dict) or j.get("status") == "failed":
                            raise make_error(p, None, error_of(json.dumps({"error": j.get("error") or {}})), request_id=rid)
                        final = j
                        for it in j.get("output") or []:
                            if not isinstance(it, dict):
                                continue
                            if it.get("type") == "reasoning":
                                for s in it.get("summary") or []:
                                    if isinstance(s, dict) and s.get("text"):
                                        started = True
                                        yield {"type": "thinking", "text": str(s["text"])}
                            elif it.get("type") == "message":
                                for c in it.get("content") or []:
                                    if isinstance(c, dict) and c.get("type") in ("output_text", "refusal") and (c.get("text") or c.get("refusal")):
                                        started = True
                                        yield {"type": "text", "text": str(c.get("text") or c.get("refusal"))}
                            elif it.get("type") == "function_call" and it.get("name"):
                                started = True
                                yield {"type": "tool_start", "id": _cid(it.get("call_id")), "name": str(it["name"])}
                    else:
                        async for line in r.aiter_lines():
                            if not line.startswith("data:"):
                                continue
                            payload = line[5:].strip()
                            if not payload or payload == "[DONE]":
                                continue
                            try:
                                ev = json.loads(payload)
                            except ValueError:
                                continue
                            if not isinstance(ev, dict):
                                continue
                            t = ev.get("type")
                            if t in ("response.output_text.delta", "response.refusal.delta"):
                                d = ev.get("delta")
                                if isinstance(d, str) and d:
                                    started = True
                                    text.append(d)
                                    yield {"type": "text", "text": d}
                            elif t in ("response.reasoning_summary_text.delta", "response.reasoning_text.delta"):
                                d = ev.get("delta")
                                if isinstance(d, str) and d:
                                    key = (ev.get("item_id"), ev.get("summary_index", ev.get("content_index")))
                                    if thinking and key not in summary_seen:
                                        thinking.append("\n\n")  # 新的一段摘要：和前一段隔开
                                        yield {"type": "thinking", "text": "\n\n"}
                                    summary_seen.add(key)
                                    started = True
                                    thinking.append(d)
                                    yield {"type": "thinking", "text": d}
                            elif t == "response.output_item.added":
                                it = ev.get("item") if isinstance(ev.get("item"), dict) else {}
                                if it.get("type") == "function_call":
                                    iid = str(it.get("id") or f"idx{ev.get('output_index')}")
                                    calls[iid] = {"call_id": it.get("call_id"), "name": str(it.get("name") or ""), "args": str(it.get("arguments") or "")}
                                    if calls[iid]["name"]:
                                        started = True
                                        yield {"type": "tool_start", "id": _cid(it.get("call_id") or iid), "name": calls[iid]["name"]}
                            elif t == "response.function_call_arguments.delta":
                                c = calls.get(str(ev.get("item_id") or ""))
                                if c is not None and isinstance(ev.get("delta"), str):
                                    c["args"] += ev["delta"]
                            elif t == "response.function_call_arguments.done":
                                c = calls.get(str(ev.get("item_id") or ""))
                                if c is not None and isinstance(ev.get("arguments"), str):
                                    c["args"] = ev["arguments"]
                            elif t == "response.output_item.done":
                                if isinstance(ev.get("item"), dict):
                                    idx = ev.get("output_index")
                                    items[idx if isinstance(idx, int) else len(items)] = ev["item"]
                            elif t in ("response.completed", "response.incomplete"):
                                final = ev.get("response") if isinstance(ev.get("response"), dict) else {}
                                break
                            elif t == "response.failed":
                                resp = ev.get("response") if isinstance(ev.get("response"), dict) else {}
                                err = error_of(json.dumps({"error": resp.get("error") or {"message": "the response failed"}}))
                                if not started and not strip and _encrypted_refused(err, body):
                                    raise _StripAndRetry()
                                raise make_error(p, None, err, started=started, request_id=rid)
                            elif t == "error":
                                src = ev.get("error") if isinstance(ev.get("error"), dict) else ev
                                err = error_of(json.dumps({"error": src}))
                                if not started and not strip and _encrypted_refused(err, body):
                                    raise _StripAndRetry()
                                raise make_error(p, None, err, started=started, request_id=rid)
            if final is None:
                raise EngineError(f"{p['name']}: the stream ended before the model finished", kind="output", retryable=not started)
        except _StripAndRetry:
            strip = True
            print(f"[engine] {p['name']} 在流里拒了加密的思考，这一次去掉思考重发")
            body["input"] = to_input(req.messages, p, model_id, strip=True)
            continue
        except httpx.TimeoutException as e:
            if not started and attempt < retries:
                await asyncio.sleep(RETRY_DELAYS[attempt])
                attempt += 1
                continue
            raise EngineError(f"{p['name']} timed out", kind="timeout", retryable=True) from e
        except httpx.HTTPError as e:
            if not started and attempt < retries:
                await asyncio.sleep(RETRY_DELAYS[attempt])
                attempt += 1
                continue
            raise EngineError(f"can't reach {p['name']}: {type(e).__name__}", retryable=True) from e
        except EngineError as e:
            if e.retryable and not started and attempt < retries:
                await asyncio.sleep(RETRY_DELAYS[attempt])
                attempt += 1
                continue
            raise
        break
    done_items = [items[k] for k in sorted(items)]
    if not final.get("output") and not done_items and calls:  # 只收到了参数的增量：拼成输出项
        done_items = [{"type": "function_call", "id": k, "call_id": c["call_id"] or k, "name": c["name"], "arguments": c["args"]}
                      for k, c in calls.items() if c["name"]]
    yield {"type": "done", "message": message_of(p, model_id, final, done_items, "".join(text), "".join(thinking))}
