"""接 OpenAI 兼容的 POST <url>/chat/completions（Kimi、DeepSeek、Qwen、Gemini、OpenRouter、Ollama、LM Studio、vLLM…）：httpx 流式，SSE 自己解析。

各家不一样的地方（10/16 对照 pi、OpenClaw、Hermes 的源码 + 真调核对，见 ~/openmousse-wt/engine/research/），按 provider 的设置走（models.py）：
  reasoning_replay  思考（reasoning_content / reasoning）怎么发回去：all = 同一型号的每一轮都带（DeepSeek V4、Kimi K3，三家鼻祖都这么做）
                    / turn = 只带这一轮工具来回中间的（默认）/ none = 都不带（Mistral、Groq 收到会报错）
  mid_system        对话中间的 role=system 能不能直接发（Kimi 能）；不能的折进旁边那条 user，写成 <system-reminder>
  images            不认图的（DeepSeek）换成一句「这里有张图」
  tool_id           工具调用 id 的写法：mistral9 = 9 位字母数字；tool_id_max = 最长几位（OpenAI 40）
  schema_fix        Kimi：每个 object 都要 required、每个属性都要 type
  reasoning_pad     每条 assistant 都得带 reasoning_content（DeepSeek V4、Kimi K3 的思考模式，少了 400）：别的模型的回复（备用链换过来的、
                    app 里换过模型的）没有思考字段，补一个空格；同一型号自己的回复照原样发回（照 Hermes 的 agent/message_sanitization.py
                    apply_reasoning_content_policy 的 must-echo pad：DeepSeek V4 不收空字符串；OpenClaw 补 ""。NousResearch/hermes-agent
                    commit cf71b1aa，MIT，Copyright (c) 2025 Nous Research，许可全文见 THIRD_PARTY_NOTICES）
  require_finish    流必须以 finish_reason 收尾，不然算断流（本机的模型服务有的不发，设 false）
  reasoning_effort / effort_map   思考档位发不发、怎么换名字
  assistant_after_tool  严格的对话模板（tool 后面不许直接跟 user）：工具结果里的图挪到后面那条 user 之前，先垫一句 assistant（pi 的
                    requiresAssistantAfterToolResult，默认不垫）
工具结果里的图（⑫b，engine/images.py 已经读回 base64、或者换成了一句话）：tool 消息只能放字，挪到这一批工具结果后面紧跟着的一条
user 里，每一段前面写明是哪个工具、哪一次调用的结果；tool 消息里只有图的写「(see attached image)」（改写自 pi 的
packages/ai/src/api/openai-completions.ts 的 convertMessages，earendil-works/pi commit 8efd314f，MIT，Copyright (c) 2025 Mario Zechner，
许可全文见 THIRD_PARTY_NOTICES）。
另外通用的：先 repair（补没结果的工具调用……），连着两条 user 并成一条，工具结果后面直接跟 user 时补一句占位的 assistant
（严格的对话模板会报错）；流里的工具调用按 index 拼，同一个 index 换了 id 算新的一个（Ollama）；Gemini 的 extra_content、
OpenRouter 的 reasoning_details 原样存下、发回给同一型号；<think>…</think> 开头的回复把思考拆出去（本机的 Qwen、R1 一类）；
不认的可选字段（stream_options、response_format、reasoning_effort、max_tokens…）报 400 就去掉再试一次，消息里的思考字段同理。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import uuid
from collections.abc import AsyncIterator

import httpx

from . import models
from .errors import EngineError
from .session import repair, text_of, tool_calls

FINISH = {"stop": "end", "end": "end", "end_turn": "end", "eos": "end", "length": "length", "max_tokens": "length",
          "tool_calls": "tool", "function_call": "tool", "tool_use": "tool", "content_filter": "refusal", "safety": "refusal",
          "network_error": "error", "error": "error"}
OPTIONAL = ("stream_options", "response_format", "reasoning_effort", "max_tokens", "max_completion_tokens")
REASONING_KEYS = ("reasoning_content", "reasoning", "reasoning_text")
RETRY_DELAYS = (1.0, 3.0)
INTERRUPTED = "(The previous turn was interrupted before I replied.)"
TRANSPORT: httpx.AsyncBaseTransport | None = None  # 测试时换成 httpx.MockTransport


def _oid(cid: str, p: dict) -> str:
    """工具调用 id 按这家的规矩改写（同一个 id 每次改出来一样，调用和结果两边对得上）。"""
    cid = str(cid or "")
    if p.get("tool_id") == "mistral9":
        return cid if re.fullmatch(r"[A-Za-z0-9]{9}", cid) else hashlib.sha1(cid.encode()).hexdigest()[:9]
    limit = int(p.get("tool_id_max") or 0)
    if limit and len(cid) > limit:
        return cid[:limit - 13] + "_" + hashlib.sha1(cid.encode()).hexdigest()[:12]
    return cid


def _user(m: dict, p: dict):
    parts = m.get("content") or []
    if all(b.get("type") == "text" for b in parts):
        return "\n\n".join(b.get("text") or "" for b in parts) or "…"
    out = []
    for b in parts:
        if b.get("type") == "text" and b.get("text"):
            out.append({"type": "text", "text": b["text"]})
        elif b.get("type") == "image" and b.get("data"):
            if p.get("images", True) is False:
                out.append({"type": "text", "text": "[An image was attached here, but this model can't read images.]"})
            else:
                out.append({"type": "image_url", "image_url": {"url": f"data:{b.get('mime') or 'image/png'};base64,{b['data']}"}})
        elif b.get("type") == "document" and b.get("data"):  # 第 3 批：PDF（收 PDF 的才原样发：engine/media.pdf_chat）
            out.append(_file_part(b) if _takes_pdf(p) else {"type": "text", "text": "[A PDF was attached here, but this model can't read PDFs directly.]"})
    if out and all(x["type"] == "text" for x in out):
        return "\n\n".join(x["text"] for x in out)
    return out or "…"


def _takes_pdf(p: dict) -> bool:
    from .media import pdf_chat
    return pdf_chat(p)


def _file_part(b: dict) -> dict:
    """中立格式的 document 块 → /chat/completions 的 file 块（OpenAI 文件输入的写法：file_data 是 data: 地址）。"""
    name = str(b.get("title") or "document.pdf")[:120]
    return {"type": "file", "file": {"filename": name if name.lower().endswith(".pdf") else name + ".pdf",
                                     "file_data": f"data:application/pdf;base64,{b['data']}"}}


def _merge_user(prev: dict, content) -> None:
    """连着两条 user 并成一条（严格的对话模板不收两条连着的 user）。"""
    a, b = prev["content"], content
    if isinstance(a, str) and isinstance(b, str):
        prev["content"] = a + "\n\n" + b
        return
    la = [{"type": "text", "text": a}] if isinstance(a, str) else list(a)
    lb = [{"type": "text", "text": b}] if isinstance(b, str) else list(b)
    prev["content"] = la + lb


def to_openai(system: str, messages: list[dict], p: dict, model_id: str) -> list[dict]:
    msgs = repair(messages)
    replay = str(p.get("reasoning_replay") or "turn")
    mid_system = bool(p.get("mid_system"))
    out: list[dict] = [{"role": "system", "content": system}] if system else []
    last_user = max((i for i, m in enumerate(msgs) if m.get("role") == "user"), default=-1)
    held: list[str] = []  # 折不进前面那条 user 的 system：并进下一条 user
    shown: list[dict] = []  # 这一批工具结果里的图（⑫b）：这一批完了放进紧跟着的一条 user

    def flush_images() -> None:
        if not shown:
            return
        if p.get("assistant_after_tool"):
            out.append({"role": "assistant", "content": "I have processed the tool results."})
        out.append({"role": "user", "content": list(shown)})
        shown.clear()

    def add_user(content) -> None:
        if held:
            note = "<system-reminder>\n" + "\n\n".join(held) + "\n</system-reminder>"
            content = note + "\n\n" + content if isinstance(content, str) else [{"type": "text", "text": note}, *content]
            held.clear()
        last = out[-1] if out else None
        if last and last["role"] == "tool":  # 工具结果后面直接跟 user：补一句占位的 assistant（严格模板要求）
            out.append({"role": "assistant", "content": INTERRUPTED})
            last = out[-1]
        if last and last["role"] == "user":
            _merge_user(last, content)
        else:
            out.append({"role": "user", "content": content})

    for i, m in enumerate(msgs):
        role = m.get("role")
        if role != "tool":
            flush_images()
        if role == "user":
            add_user(_user(m, p))
        elif role == "assistant":
            same = (m.get("api") == models.OPENAI and m.get("provider") == p["name"] and m.get("model") == model_id
                    and isinstance(m.get("raw"), dict))
            if same:
                r = dict(m["raw"])
                keep = replay == "all" or (replay == "turn" and i > last_user)
                if not keep:
                    for k in (*REASONING_KEYS, "reasoning_details"):
                        r.pop(k, None)
                if r.get("tool_calls"):
                    r["tool_calls"] = [{**tc, "id": _oid(tc.get("id") or "", p)} for tc in r["tool_calls"]]
                if not r.get("content") and not r.get("tool_calls"):
                    continue
                out.append({"role": "assistant", **r})
                continue
            text, calls = text_of(m), tool_calls(m)
            if not text.strip() and not calls:
                continue
            msg: dict = {"role": "assistant", "content": text}
            if p.get("reasoning_pad"):  # 别的模型的回复：这几家每条 assistant 都要带思考字段（见文件开头）；自己的回复照原样
                msg["reasoning_content"] = " "
            if calls:
                msg["tool_calls"] = [{"id": _oid(c.get("id") or "", p), "type": "function",
                                      "function": {"name": c.get("name") or "", "arguments": json.dumps(c.get("args") or {}, ensure_ascii=False)}}
                                     for c in calls]
            out.append(msg)
        elif role == "tool":
            cid = _oid(m.get("id") or "", p)
            pics = [b for b in m.get("content") or [] if b.get("type") == "image" and b.get("data")]
            out.append({"role": "tool", "tool_call_id": cid, "content": text_of(m) or ("(see attached image)" if pics else "(empty)")})
            if pics:
                shown.append({"type": "text", "text": f"Attached image{'s' if len(pics) > 1 else ''} from the result of {m.get('name') or 'a tool'} "
                                                      f"(call {cid}):"})
                shown.extend({"type": "image_url", "image_url": {"url": f"data:{b.get('mime') or 'image/png'};base64,{b['data']}"}} for b in pics)
            docs = [b for b in m.get("content") or [] if b.get("type") == "document" and b.get("data")]
            if docs and _takes_pdf(p):  # 第 3 批：工具结果里的 PDF（engine/pdf.py；不收 PDF 的那里已经换成一句话）：和图一样挪到紧跟着的 user 里
                shown.append({"type": "text", "text": f"Attached PDF{'s' if len(docs) > 1 else ''} from the result of {m.get('name') or 'a tool'} "
                                                      f"(call {cid}):"})
                shown.extend(_file_part(b) for b in docs)
        elif role == "system":
            t = text_of(m).strip()
            if not t:
                continue
            last = out[-1] if out else None
            if mid_system and last and last["role"] in ("user", "tool", "assistant"):
                out.append({"role": "system", "content": t})
            elif last and last["role"] == "user":
                note = "<system-reminder>\n" + t + "\n</system-reminder>"
                _merge_user(last, note)
            else:
                held.append(t)
    flush_images()
    if held:
        add_user("")
        last = out[-1]
        if isinstance(last["content"], str):
            last["content"] = last["content"].rstrip()
    return out


def fix_schema(s):
    """Kimi 的工具 schema 要求：每个 object 有 required、每个属性有 type、anyOf 的每一枝都有 type（Hermes、OpenClaw 的做法，宽松补齐）。"""
    if isinstance(s, list):
        return [fix_schema(x) for x in s]
    if not isinstance(s, dict):
        return s
    s = {k: fix_schema(v) for k, v in s.items()}
    if s.get("type") == "object" or "properties" in s:
        s.setdefault("type", "object")
        s.setdefault("required", [])
        props = s.get("properties") or {}
        for k, v in list(props.items()):
            if isinstance(v, dict) and "type" not in v and not any(x in v for x in ("anyOf", "oneOf", "enum", "$ref", "const")):
                props[k] = {**v, "type": "string"}
    for key in ("anyOf", "oneOf"):
        if isinstance(s.get(key), list):
            s[key] = [b if not isinstance(b, dict) or "type" in b or "$ref" in b else {**b, "type": "string"} for b in s[key]]
    return s


def body_of(p: dict, model_id: str, req) -> dict:
    body: dict = {"model": model_id, "messages": to_openai(req.system, req.messages, p, model_id), "stream": True}
    if p.get("stream_usage", True) is not False:
        body["stream_options"] = {"include_usage": True}
    if req.tools:
        body["tools"] = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                           "parameters": fix_schema(t["schema"]) if p.get("schema_fix") else t["schema"]}}
                         for t in req.tools]
    if req.max_tokens:  # 不给就按各家默认（各家上限不一样，给大了会 400）
        body[str(p.get("max_tokens_field") or "max_tokens")] = int(req.max_tokens)
    if req.json and not req.tools and p.get("json_mode", True) is not False:
        body["response_format"] = {"type": "json_object"}
    if req.effort and p.get("reasoning_effort"):
        emap = p.get("effort_map") if isinstance(p.get("effort_map"), dict) else {"xhigh": "high", "max": "high"}
        body["reasoning_effort"] = emap.get(req.effort, req.effort)
    return body


def _usage(u: dict) -> dict:
    u = u or {}
    prompt = int(u.get("prompt_tokens") or 0)
    cached = int(u.get("prompt_cache_hit_tokens") or (u.get("prompt_tokens_details") or {}).get("cached_tokens") or u.get("cached_tokens") or 0)
    return {"in": max(0, prompt - cached), "out": int(u.get("completion_tokens") or 0), "cache_read": cached, "cache_write": 0}


class ThinkSplitter:
    """回复以 <think> 开头的（本机的 Qwen、R1 一类把思考写进正文）：<think>…</think> 里的算思考，后面的才是回复。只认开头那一段。"""

    def __init__(self) -> None:
        self.state = "start"  # start：还不知道有没有 <think>；think：在思考里；text：正文
        self.buf = ""

    def feed(self, chunk: str) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        self.buf += chunk
        if self.state == "start":
            s = self.buf.lstrip()
            if s.startswith("<think>"):
                self.state, self.buf = "think", s[len("<think>"):]
            elif "<think>".startswith(s):
                return out  # 还不够长，等下一段
            else:
                self.state = "text"
        if self.state == "think":
            j = self.buf.find("</think>")
            if j < 0:
                keep = max(0, len(self.buf) - len("</think>"))  # 留一截，防止结束标签被切在两段之间
                if keep:
                    out.append(("thinking", self.buf[:keep]))
                    self.buf = self.buf[keep:]
                return out
            if j:
                out.append(("thinking", self.buf[:j]))
            self.state, self.buf = "text", self.buf[j + len("</think>"):].lstrip("\n")
        if self.state == "text" and self.buf:
            out.append(("text", self.buf))
            self.buf = ""
        return out

    def flush(self) -> list[tuple[str, str]]:
        if not self.buf:
            return []
        kind = "thinking" if self.state == "think" else "text"
        out, self.buf = [(kind, self.buf)], ""
        return out


def _message(p: dict, model_id: str, text: str, reasoning: str, rkey: str, details: list, calls: list[dict], finish: str | None,
             usage: dict) -> dict:
    content: list[dict] = []
    if reasoning:
        content.append({"type": "thinking", "text": reasoning})
    if text:
        content.append({"type": "text", "text": text})
    raw_calls = []
    for c in calls:
        if not c["name"]:
            continue  # 只来了半截、连名字都没有的调用不算
        try:
            args = json.loads(c["args"] or "{}")
            bad = None if isinstance(args, dict) else c["args"]
        except ValueError:
            args, bad = {}, c["args"]
        block = {"type": "tool_call", "id": c["id"], "name": c["name"], "args": args if isinstance(args, dict) else {}}
        if bad is not None:
            block["bad_args"] = str(bad)[:2000]
        content.append(block)
        rc = {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": json.dumps(block["args"], ensure_ascii=False)}}
        if c.get("extra"):
            rc["extra_content"] = c["extra"]  # Gemini 的 thought_signature：原样发回给同一型号
        raw_calls.append(rc)
    raw: dict = {"content": text}
    if raw_calls:
        raw["tool_calls"] = raw_calls
    if reasoning:
        raw[rkey] = reasoning
    if details:
        raw["reasoning_details"] = details
    stop = FINISH.get(str(finish or "").lower(), "end")
    if raw_calls and stop == "end":
        stop = "tool"  # 有的接口带着工具调用也说 stop
    return {"role": "assistant", "content": content, "api": models.OPENAI, "provider": p["name"], "model": model_id, "stop": stop,
            "usage": _usage(usage), "raw": raw}


def _strip_reasoning(body: dict) -> None:
    for m in body.get("messages") or []:
        if m.get("role") == "assistant":
            for k in (*REASONING_KEYS, "reasoning_details"):
                m.pop(k, None)


async def stream(p: dict, model_id: str, req) -> AsyncIterator[dict]:
    """流式：{"type": "text" | "thinking", "text"}、{"type": "tool_start", "id", "name"}，最后 {"type": "done", "message"}。出错抛 EngineError。"""
    body = body_of(p, model_id, req)
    headers = {"Content-Type": "application/json", **{str(k): str(v) for k, v in (p.get("headers") or {}).items()}}
    if p.get("key"):
        headers["Authorization"] = f"Bearer {p['key']}"
    elif p.get("key_env", "x") != "":
        raise EngineError(f"no API key for {p['name']} (put {p.get('key_env') or p['name'].upper() + '_API_KEY'} in the .env)", kind="config")
    url = f"{p['url']}/chat/completions"
    require_finish = p.get("require_finish", True) is not False
    dropped: set[str] = set()
    attempt = 0
    retries = 0 if getattr(req, "quick", False) else len(RETRY_DELAYS)  # 备用链里还有能换的：这一层不重试（providers.Request.quick）
    while True:
        started = False
        text: list[str] = []
        reasoning: list[str] = []
        details: list = []
        rkey = "reasoning_content"
        calls: list[dict] = []
        by_index: dict[int, int] = {}
        finish: str | None = None
        usage: dict = {}
        split = ThinkSplitter()

        def emit_text(chunk: str):
            nonlocal started
            for kind, part in split.feed(chunk):
                started = True
                (text if kind == "text" else reasoning).append(part)
                yield {"type": kind, "text": part}

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
                        if r.status_code in (400, 422) and "reasoning" in err.lower() and "msg-reasoning" not in dropped:
                            _strip_reasoning(body)  # 消息里的思考字段不收（Mistral、Groq 一类）：都拿掉再来
                            dropped.add("msg-reasoning")
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
                        if isinstance(msg.get("content"), str) and msg["content"]:
                            for ev in emit_text(msg["content"]):
                                yield ev
                        for k in REASONING_KEYS:
                            if isinstance(msg.get(k), str) and msg[k]:
                                rkey = k
                                reasoning.append(msg[k])
                                break
                        for tc in msg.get("tool_calls") or []:
                            f = tc.get("function") or {}
                            calls.append({"id": str(tc.get("id") or "") or f"call_{uuid.uuid4().hex[:12]}", "name": f.get("name") or "",
                                          "args": f.get("arguments") or "", "extra": tc.get("extra_content")})
                        finish, usage = ch.get("finish_reason") or "stop", j.get("usage") or ch.get("usage") or {}
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
                                if ch.get("usage"):  # Moonshot 把用量放在 choice 里
                                    usage = ch["usage"]
                                d = ch.get("delta") or ch.get("message") or {}
                                if isinstance(d.get("content"), str) and d["content"]:
                                    for ev in emit_text(d["content"]):
                                        yield ev
                                for k in REASONING_KEYS:
                                    if isinstance(d.get(k), str) and d[k]:
                                        started, rkey = True, k
                                        reasoning.append(d[k])
                                        yield {"type": "thinking", "text": d[k]}
                                        break
                                if isinstance(d.get("reasoning_details"), list):
                                    details.extend(d["reasoning_details"])  # OpenRouter：原样存下，发回给同一型号
                                for tc in d.get("tool_calls") or []:
                                    f = tc.get("function") or {}
                                    tid = str(tc.get("id") or "")
                                    idx = tc.get("index")
                                    if idx is not None:
                                        slot = by_index.get(idx)
                                    elif tid:
                                        slot = next((i for i, c in enumerate(calls) if c["id"] == tid), None)
                                    else:  # 既没 index 也没 id 的续段：接在最后一个调用后面
                                        slot = len(calls) - 1 if calls else None
                                    if slot is not None and tid and calls[slot]["id"] and calls[slot]["id"] != tid and calls[slot]["name"]:
                                        slot = None  # 同一个 index 换了 id：是新的一个调用（Ollama 并行调用都用 index 0）
                                    if slot is None:
                                        calls.append({"id": tid, "name": "", "args": "", "extra": None})
                                        slot = len(calls) - 1
                                        if idx is not None:
                                            by_index[idx] = slot
                                    cur = calls[slot]
                                    if tid and not cur["id"]:
                                        cur["id"] = tid
                                    if f.get("name") and not cur["name"]:
                                        cur["name"] = str(f["name"])
                                        started = True
                                        yield {"type": "tool_start", "id": cur["id"], "name": cur["name"]}
                                    if isinstance(f.get("arguments"), str):
                                        cur["args"] += f["arguments"]
                                    if tc.get("extra_content"):
                                        cur["extra"] = tc["extra_content"]
                                if ch.get("finish_reason"):
                                    finish = ch["finish_reason"]
            for kind, part in split.flush():
                (text if kind == "text" else reasoning).append(part)
                yield {"type": kind, "text": part}
            if finish is None and require_finish:
                raise EngineError(f"{p['name']}: the stream ended before the model finished", kind="output", retryable=not started)
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
    for c in calls:
        if not c["id"]:
            c["id"] = f"call_{uuid.uuid4().hex[:12]}"  # 没给 id 的：每次都不一样，免得和历史里的撞上
    if str(finish or "").lower() in ("network_error", "error"):
        raise EngineError(f"{p['name']}: the model stopped with an error", retryable=not started)
    yield {"type": "done", "message": _message(p, model_id, "".join(text), "".join(reasoning), rkey, details, calls, finish, usage)}
