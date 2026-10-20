"""假模型服务器：本机一个 HTTP 服务，按剧本回放，不连网、不花钱。四种说法同时会：
  POST /v1/chat/completions（也认 /chat/completions）  OpenAI 兼容接口，SSE 流式或一次性 JSON（Kimi、DeepSeek、Qwen 都是这种）
  POST /v1/messages                                    Claude 的接口（官方 anthropic SDK 指过来就能用），SSE 流式或一次性 JSON
  POST /v1/responses（也认 /responses）                 OpenAI 的 Responses API（ChatGPT 套餐、api = openai-responses 的密钥）：SSE 流式
                                                       （response.created … output_item / 思考摘要 / 函数参数的增量 … response.completed）
                                                       或一次性 JSON；思考项带 encrypted_content（不写就生成一个，内容带着型号名）
  POST /tools/invoke（tool = llm-task）                 OpenClaw Gateway 的 llm-task（cardagent.via_llm_task 指过来就能用）
  GET  /v1/models                                      型号列表：OpenAI 的 data，外加 ChatGPT 套餐那种 models（slug / display_name / visibility）
内核的 provider 直接指过来，server.json 的 engine.providers 写：
  {"fake": {"api": "openai", "url": fm.openai_url, "key_env": ""}}   模型写 fake/随便什么名字
  {"fake": {"api": "openai-responses", "url": fm.openai_url, "key_env": ""}}   同上，走 Responses API
  {"anthropic": {"url": fm.anthropic_url, "key": "x"}}               模型写 anthropic/claude-…（写了 url 就不带 beta 的 block_binding、fallbacks）
key 可以是一个函数（拿到 Bearer 后面那一段 → 认不认）：测 ChatGPT 套餐的令牌换过以后旧的不认。
plan_quirks=True：/v1/responses 的流照 ChatGPT 套餐线上真的回法（10/20 录到的）：不标 Content-Type、请求 id 只在 x-oai-request-id、
  response.completed 的 output 是空的（输出项只在 response.output_item.done 里）。

剧本：一个列表（每来一个请求用掉一条）或一个函数 responder(请求记录) → 一条；列表用完了回 default（也可以是函数）。一条是下面几种之一：
  Reply      正常回复：思考、文字、工具调用；可以慢（first_delay 首字前等、delay 每段之间等），可以写到上限（stop="length"）
  Refusal    拒答（OpenAI：finish_reason content_filter；Claude：stop_reason refusal；Responses：refusal 内容块）
  Fail       HTTP 出错（429 / 500 / 529 …，带各家的错误信封、Retry-After）；Responses 的错误码写 code、param（type = "detail" 时回
             ChatGPT 套餐准入那一层的 {"detail": "…"}）
  Broken     流到一半出事：先发几个字，再来一条流里的错（写了 error 的话；Claude 那边是 overloaded_error 这类，Responses 是 response.failed，
             错误码写 code）或者直接断开、不收尾
  Hang       收下请求不回，seconds 秒后断开（测超时）
  Raw        原样回一段（坏 JSON、HTML 错误页……）
请求记录（requests 里，responder 拿到的也是它）：{"api": openai | anthropic | responses | llm-task, "path", "headers"（不含密钥）, "auth",
  "bearer"（Bearer 后面那一段的短指纹，不是原文）, "body", "model", "system", "user"（最后一条 user 的字）, "input"（user 里 INPUT_JSON: 后面
  那段解析出来的；llm-task 是 args.input）, "t"}。
用量没写就按字数估（字数 / 4，pi 的 faux provider 的办法），离线也能算 token 和钱。

命令行（手动试、给测试服当模型）：python -m evals.fakemodel --port 8765 [--script 剧本.json] [--key 密钥] [--echo]
剧本文件是一个列表：[{"text": "…", "thinking": "…", "calls": [["工具", {参数}]], "stop": "length", "first_delay": 2},
  {"refusal": ""}, {"status": 529, "message": "Overloaded"}, {"broken": {"text": "半截", "error": "overloaded"}}, {"hang": 30}, {"raw": "<html>"}]

改自 Hermes（NousResearch/hermes-agent，MIT，Copyright (c) 2025 Nous Research）commit 848fc0ad 的 tests/fakes/fake_llm_provider.py
（剧本的几种、录下每个请求、ThreadingHTTPServer 那一套）和 tests/fakes/providers/anthropic_messages.py（Claude 流里事件的顺序、
错误信封；它用 SDK 校验请求的那部分没拿）。许可全文见仓库根目录 THIRD_PARTY_NOTICES。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


@dataclass
class Reply:
    text: str = ""
    thinking: str = ""
    calls: list = field(default_factory=list)   # [(工具名, 参数)]：参数是 dict 就发 JSON，是字符串就原样发（测坏 JSON）
    stop: str | None = None                      # 不写：有工具调用 = 调工具，否则说完了；"length" = 写到上限；别的原样发
    chunk: int = 8                               # 每段几个字
    delay: float = 0.0                           # 每段之间等几秒
    first_delay: float = 0.0                     # 第一个字之前等几秒（首字慢）
    usage: dict | None = None                    # {"in", "out", "cache_read", "cache_write"}；不写按字数估
    signature: str = "sig-fake"                  # Claude 思考块的签名
    model: str | None = None                     # 回报的型号；不写 = 请求的型号（写别的 = 服务端换过型号）
    encrypted: str | None = None                 # Responses 思考项的 encrypted_content；不写 = 有思考就生成一个（带型号名）


@dataclass
class Refusal:
    text: str = ""


@dataclass
class Fail:
    status: int = 500
    message: str = "scripted failure"
    type: str | None = None                      # Claude 的错误类型；不写按状态码配（Responses：写 "detail" 回 {"detail": message}）
    retry_after: float | None = None
    code: str | None = None                      # Responses 的 error.code（subscription_sharing_usage_limit_exceeded……）
    param: str | None = None                     # Responses 的 error.param（点名不认的字段）


@dataclass
class Broken:
    text: str = "partial answer"
    after: int = 8                               # 先发多少字
    error: str | None = None                     # 写了：发一条流里的错再断开；没写：直接断开（不发收尾）
    code: str | None = None                      # Responses 流里的错（response.failed）的 error.code


@dataclass
class Hang:
    seconds: float = 60.0


@dataclass
class Raw:
    body: str = "this is not json"
    status: int = 200
    content_type: str = "application/json"


Step = Reply | Refusal | Fail | Broken | Hang | Raw
Responder = Callable[[dict], Step]
CLAUDE_ERRORS = {400: "invalid_request_error", 401: "authentication_error", 403: "permission_error", 404: "not_found_error",
                 413: "request_too_large", 429: "rate_limit_error", 500: "api_error", 529: "overloaded_error"}
FENCE = re.compile(r"^```(?:json)?\s*([\s\S]*?)\s*```$")


def step_from_dict(d: dict) -> Step:
    """剧本文件里的一条（JSON）→ Step。"""
    if "refusal" in d:
        return Refusal(str(d.get("refusal") or ""))
    if "broken" in d:
        b = d["broken"] if isinstance(d["broken"], dict) else {}
        return Broken(str(b.get("text") or "partial answer"), int(b.get("after") or 8), b.get("error"), b.get("code"))
    if "hang" in d:
        return Hang(float(d["hang"]))
    if "raw" in d:
        return Raw(str(d["raw"]), int(d.get("status") or 200), str(d.get("content_type") or "application/json"))
    if "status" in d:
        return Fail(int(d["status"]), str(d.get("message") or "scripted failure"), d.get("type"), d.get("retry_after"), d.get("code"), d.get("param"))
    return Reply(text=str(d.get("text") or ""), thinking=str(d.get("thinking") or ""),
                 calls=[(c[0], c[1] if len(c) > 1 else {}) for c in d.get("calls") or []], stop=d.get("stop"),
                 chunk=int(d.get("chunk") or 8), delay=float(d.get("delay") or 0), first_delay=float(d.get("first_delay") or 0),
                 usage=d.get("usage"), model=d.get("model"), encrypted=d.get("encrypted"))


def estimate_tokens(*parts) -> int:
    """粗估 token：字数 / 4（pi 的 faux provider 也这么算），不是字符串的先转成 JSON。"""
    n = sum(len(p if isinstance(p, str) else json.dumps(p, ensure_ascii=False)) for p in parts if p)
    return math.ceil(n / 4)


def _text(content) -> str:
    if isinstance(content, str):
        return content
    return "\n\n".join(str(b.get("text") or "") for b in content or [] if isinstance(b, dict) and b.get("type") == "text")


def _input_json(user: str):
    i = user.find("INPUT_JSON:")
    if i < 0:
        return None
    try:
        return json.loads(user[i + len("INPUT_JSON:"):].strip())
    except ValueError:
        return None


def _bearer(headers: dict) -> str:
    auth = headers.get("authorization") or ""
    return auth[7:] if auth.startswith("Bearer ") else ""


def _record(api: str, path: str, headers: dict, body: dict) -> dict:
    tok = _bearer(headers)
    rec = {"api": api, "path": path, "headers": {k: v for k, v in headers.items() if k not in ("authorization", "x-api-key")},
           "auth": bool(headers.get("authorization") or headers.get("x-api-key")), "body": body, "t": time.time(),
           "bearer": hashlib.sha256(tok.encode()).hexdigest()[:12] if tok else None}
    if api == "llm-task":
        args = body.get("args") if isinstance(body.get("args"), dict) else {}
        rec.update(model=str(args.get("model") or ""), system=str(args.get("prompt") or ""), user="", input=args.get("input"))
        return rec
    if api == "responses":  # instructions + developer 消息；input 里最后一条 user 的 input_text
        items = [m for m in body.get("input") or [] if isinstance(m, dict)]

        def words(c) -> str:
            return c if isinstance(c, str) else "\n\n".join(str(b.get("text") or "") for b in c or []
                                                             if isinstance(b, dict) and b.get("type") in ("input_text", "output_text"))
        system = "\n\n".join([str(body.get("instructions") or ""), *(words(m.get("content")) for m in items if m.get("role") == "developer")]).strip()
        user = next((words(m.get("content")) for m in reversed(items) if m.get("role") == "user"), "")
        rec.update(model=str(body.get("model") or ""), system=system, user=user, input=_input_json(user))
        return rec
    msgs = [m for m in body.get("messages") or [] if isinstance(m, dict)]
    if api == "openai":
        system = "\n\n".join(_text(m.get("content")) for m in msgs if m.get("role") in ("system", "developer"))
    else:
        s = body.get("system")
        system = s if isinstance(s, str) else _text(s)
    user = next((_text(m.get("content")) for m in reversed(msgs) if m.get("role") == "user"), "")
    rec.update(model=str(body.get("model") or ""), system=system, user=user, input=_input_json(user))
    return rec


def _authorized(headers: dict, key) -> bool:
    if callable(key):  # 测 ChatGPT 套餐：只认现在那一把访问令牌
        return bool(key(_bearer(headers)))
    return headers.get("authorization") == f"Bearer {key}" or headers.get("x-api-key") == key


def _pieces(s: str, n: int) -> list[str]:
    n = max(1, n)
    return [s[i:i + n] for i in range(0, len(s), n)]


class FakeModelServer:
    """一个本机 HTTP 服务（后台线程）。with FakeModelServer([...]) as fm: …，或 start() / stop()。"""

    def __init__(self, script: list[Step] | Responder | None = None, *, default: Step | Responder | None = None,
                 key: str | Callable[[str], bool] | None = None, host: str = "127.0.0.1", port: int = 0,
                 catalog: list[dict] | None = None, plan_quirks: bool = False) -> None:
        self._script: list[Step] = list(script) if isinstance(script, list) else []
        self._responder: Responder | None = script if callable(script) else None
        self.default: Step | Responder = default or Reply(text="ok")
        self.key = key                     # 设了就核对密钥（Authorization: Bearer 或 x-api-key）；函数 = 拿 Bearer 那一段问它认不认
        # GET /v1/models 里 ChatGPT 套餐那种目录（slug / display_name / visibility）
        self.catalog: list[dict] = catalog if catalog is not None else [{"slug": "fake-model", "display_name": "Fake", "visibility": "list"}]
        self.plan_quirks = plan_quirks     # True：/v1/responses 的流照 ChatGPT 套餐线上真的回法（见文件开头）
        self.requests: list[dict] = []
        self._addr = (host, port)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._httpd: ThreadingHTTPServer | None = None
        self._seq = 0

    def __enter__(self) -> FakeModelServer:
        return self.start()

    def __exit__(self, *_exc) -> None:
        self.stop()

    def start(self) -> FakeModelServer:
        self._stop.clear()
        self._httpd = ThreadingHTTPServer(self._addr, _handler(self))
        self._httpd.daemon_threads = True
        threading.Thread(target=self._httpd.serve_forever, kwargs={"poll_interval": 0.05}, name="fake-model", daemon=True).start()
        return self

    def stop(self) -> None:
        self._stop.set()  # 还在 Hang、在慢慢吐字的请求马上放手
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None

    @property
    def url(self) -> str:
        assert self._httpd is not None, "the fake model server isn't running"
        host, port = self._httpd.server_address[:2]
        return f"http://{host}:{port}"

    @property
    def openai_url(self) -> str:
        """给 OpenAI 兼容的 provider（engine.providers.<名>.url）：…/v1"""
        return self.url + "/v1"

    @property
    def anthropic_url(self) -> str:
        """给 Claude（engine.providers.anthropic.url）：SDK 自己接 /v1/messages。"""
        return self.url

    def push(self, *steps: Step) -> None:
        with self._lock:
            self._script.extend(steps)

    def pending(self) -> int:
        with self._lock:
            return len(self._script)

    def of(self, api: str) -> list[dict]:
        with self._lock:
            return [r for r in self.requests if r["api"] == api]

    def next_step(self, rec: dict) -> Step:
        if self._responder is not None:
            return self._responder(rec)
        with self._lock:
            if self._script:
                return self._script.pop(0)
        return self.default(rec) if callable(self.default) else self.default

    def next_id(self, prefix: str) -> str:
        with self._lock:
            self._seq += 1
            return f"{prefix}{self._seq:04d}"

    def pause(self, seconds: float) -> None:
        if seconds > 0:
            self._stop.wait(seconds)


def _usage(step: Reply, rec: dict, calls: list[tuple[str, str, str]]) -> dict:
    u = dict(step.usage or {})
    body = rec["body"]
    u.setdefault("in", estimate_tokens(body.get("system") or body.get("instructions"), body.get("messages") or body.get("input"), body.get("tools")))
    u.setdefault("out", estimate_tokens(step.thinking, step.text, *(a for _, _, a in calls)))
    u.setdefault("cache_read", 0)
    u.setdefault("cache_write", 0)
    return u


def _handler(fm: FakeModelServer) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_a) -> None:
            pass

        # —— 发送 ——
        def _send(self, status: int, payload, ctype: str = "application/json", headers: dict | None = None) -> None:
            data = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def _sse_start(self, plan: bool = False) -> None:
            self.send_response(200)
            if plan:  # ChatGPT 套餐的 /responses：不标类型，请求 id 在 x-oai-request-id
                self.send_header("x-oai-request-id", str(uuid.uuid4()))
            else:
                self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True

        def _ev(self, payload, name: str | None = None) -> None:
            data = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
            self.wfile.write(((f"event: {name}\n" if name else "") + f"data: {data}\n\n").encode())
            self.wfile.flush()

        def _hang(self, step: Hang) -> None:
            fm.pause(step.seconds)
            self.close_connection = True

        # —— 收 ——
        def do_GET(self) -> None:
            if self.path.split("?", 1)[0].rstrip("/").endswith("/models"):
                headers = {k.lower(): v for k, v in self.headers.items()}
                if fm.key and not _authorized(headers, fm.key):
                    self._send(401, {"error": {"message": "invalid api key", "code": "invalid_api_key"}})
                    return
                self._send(200, {"object": "list", "data": [{"id": m.get("slug"), "object": "model"} for m in fm.catalog],
                                 "models": fm.catalog})
                return
            self._send(404, {"error": {"message": f"no route {self.path}"}})

        def do_POST(self) -> None:
            raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            path = self.path.split("?", 1)[0].rstrip("/")
            api = ("anthropic" if path.endswith("/v1/messages") else "openai" if path.endswith("/chat/completions")
                   else "responses" if path.endswith("/responses") else "llm-task" if path.endswith("/tools/invoke") else None)
            if api is None:
                self._send(404, {"error": {"message": f"no route {self.path}"}})
                return
            try:
                body = json.loads(raw or b"{}")
            except ValueError:
                self._send(400, {"error": {"message": "invalid json"}})
                return
            headers = {k.lower(): v for k, v in self.headers.items()}
            rec = _record(api, self.path, headers, body if isinstance(body, dict) else {})
            with fm._lock:
                fm.requests.append(rec)
            if fm.key and not _authorized(headers, fm.key):
                step: Step = Fail(401, "invalid api key", code="invalid_api_key")
            else:
                try:
                    step = fm.next_step(rec)
                except Exception as e:  # noqa: BLE001 — responder 自己出错：回 500，测试里看得到
                    step = Fail(500, f"responder error: {type(e).__name__}: {e}")
            rec["step"] = type(step).__name__
            if isinstance(step, Hang):
                self._hang(step)
            elif isinstance(step, Raw):
                self._send(step.status, step.body.encode(), step.content_type)
            elif api == "llm-task":
                self._llm_task(step, rec)
            elif api == "openai":
                self._openai(step, rec)
            elif api == "responses":
                self._responses(step, rec)
            else:
                self._claude(step, rec)

        # —— OpenAI 兼容 ——
        def _openai(self, step: Step, rec: dict) -> None:
            body = rec["body"]
            if isinstance(step, Fail):
                self._send(step.status, {"error": {"message": step.message, "type": "server_error" if step.status >= 500 else "invalid_request_error"}},
                           headers={"Retry-After": str(step.retry_after)} if step.retry_after is not None else None)
                return
            if isinstance(step, Refusal):
                step = Reply(text=step.text, stop="content_filter")
            cid = f"chatcmpl-{uuid.uuid4().hex[:12]}"
            model = str(getattr(step, "model", None) or body.get("model") or "fake-model")

            def chunk(delta: dict, finish: str | None = None) -> dict:
                return {"id": cid, "object": "chat.completion.chunk", "created": int(time.time()), "model": model,
                        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}

            if isinstance(step, Broken):
                self._sse_start()
                self._ev(chunk({"role": "assistant", "content": ""}))
                self._ev(chunk({"content": step.text[:step.after]}))
                if step.error:
                    self._ev({"error": {"message": step.error, "type": "server_error"}})
                return
            assert isinstance(step, Reply)
            calls = [(fm.next_id("call_fake_"), str(n), a if isinstance(a, str) else json.dumps(a, ensure_ascii=False)) for n, a in step.calls]
            finish = {None: "tool_calls" if calls else "stop", "length": "length", "refusal": "content_filter"}.get(step.stop, step.stop)
            u = _usage(step, rec, calls)
            usage = {"prompt_tokens": u["in"] + u["cache_read"], "completion_tokens": u["out"], "total_tokens": u["in"] + u["cache_read"] + u["out"],
                     "prompt_tokens_details": {"cached_tokens": u["cache_read"]}}
            if not body.get("stream"):
                msg: dict = {"role": "assistant", "content": step.text or None}
                if step.thinking:
                    msg["reasoning_content"] = step.thinking
                if calls:
                    msg["tool_calls"] = [{"id": i, "type": "function", "function": {"name": n, "arguments": a}} for i, n, a in calls]
                fm.pause(step.first_delay)
                self._send(200, {"id": cid, "object": "chat.completion", "created": int(time.time()), "model": model,
                                 "choices": [{"index": 0, "message": msg, "finish_reason": finish}], "usage": usage})
                return
            self._sse_start()
            self._ev(chunk({"role": "assistant", "content": ""}))
            fm.pause(step.first_delay)
            for p in _pieces(step.thinking, step.chunk):
                self._ev(chunk({"reasoning_content": p}))
                fm.pause(step.delay)
            for p in _pieces(step.text, step.chunk):
                self._ev(chunk({"content": p}))
                fm.pause(step.delay)
            for i, (tid, name, args) in enumerate(calls):
                self._ev(chunk({"tool_calls": [{"index": i, "id": tid, "type": "function", "function": {"name": name, "arguments": ""}}]}))
                for p in _pieces(args, step.chunk):
                    self._ev(chunk({"tool_calls": [{"index": i, "function": {"arguments": p}}]}))
            self._ev(chunk({}, finish))
            if (body.get("stream_options") or {}).get("include_usage"):
                self._ev({"id": cid, "object": "chat.completion.chunk", "created": int(time.time()), "model": model, "choices": [], "usage": usage})
            self._ev("[DONE]")

        # —— OpenAI Responses ——
        def _responses(self, step: Step, rec: dict) -> None:
            body = rec["body"]
            if isinstance(step, Fail):
                payload = {"detail": step.message} if step.type == "detail" else \
                    {"error": {"message": step.message, "type": "server_error" if step.status >= 500 else "invalid_request_error",
                               "code": step.code, "param": step.param}}
                headers = {"x-request-id": f"req_fake_{uuid.uuid4().hex[:12]}"}
                if step.retry_after is not None:
                    headers["Retry-After"] = str(step.retry_after)
                self._send(step.status, payload, headers=headers)
                return
            refusal = isinstance(step, Refusal)
            if isinstance(step, Refusal):
                step = Reply(text=step.text or "I can't help with that.")
            rid = f"resp_fake_{uuid.uuid4().hex[:12]}"
            model = str(getattr(step, "model", None) or body.get("model") or "fake-model")
            seq = [0]

            def ev(kind: str, **kw) -> None:
                seq[0] += 1
                self._ev({"type": kind, "sequence_number": seq[0], **kw}, kind)

            def resp(status: str, output: list, usage: dict | None = None, **extra) -> dict:
                return {"id": rid, "object": "response", "created_at": int(time.time()), "status": status, "model": model, "output": output,
                        "usage": usage, "error": None, "incomplete_details": None, **extra}

            if isinstance(step, Broken):
                self._sse_start(fm.plan_quirks)
                ev("response.created", response=resp("in_progress", []))
                ev("response.output_item.added", output_index=0, item={"type": "message", "id": "msg_fake_b", "role": "assistant", "content": []})
                ev("response.output_text.delta", item_id="msg_fake_b", output_index=0, content_index=0, delta=step.text[:step.after])
                if step.error:
                    ev("response.failed", response={**resp("failed", []), "error": {"code": step.code or "server_error", "message": step.error}})
                return
            assert isinstance(step, Reply)
            calls = [(fm.next_id("call_fake_"), str(n), a if isinstance(a, str) else json.dumps(a, ensure_ascii=False)) for n, a in step.calls]
            u = _usage(step, rec, calls)
            usage = {"input_tokens": u["in"] + u["cache_read"], "input_tokens_details": {"cached_tokens": u["cache_read"]},
                     "output_tokens": u["out"], "output_tokens_details": {"reasoning_tokens": 0}, "total_tokens": u["in"] + u["cache_read"] + u["out"]}
            output: list[dict] = []
            if step.thinking or step.encrypted:
                output.append({"type": "reasoning", "id": fm.next_id("rs_fake_"), "summary": [{"type": "summary_text", "text": step.thinking}]
                               if step.thinking else [], "encrypted_content": step.encrypted or f"enc:{model}:{fm.next_id('')}"})
            if step.text:
                part = {"type": "refusal", "refusal": step.text} if refusal else {"type": "output_text", "text": step.text, "annotations": []}
                output.append({"type": "message", "id": fm.next_id("msg_fake_"), "role": "assistant", "status": "completed", "content": [part]})
            for cid, name, args in calls:
                output.append({"type": "function_call", "id": fm.next_id("fc_fake_"), "call_id": cid, "name": name, "arguments": args,
                               "status": "completed"})
            length = step.stop == "length"
            final = resp("incomplete" if length else "completed", output, usage,
                         **({"incomplete_details": {"reason": "max_output_tokens"}} if length else {}))
            if not body.get("stream"):
                fm.pause(step.first_delay)
                self._send(200, final)
                return
            self._sse_start(fm.plan_quirks)
            ev("response.created", response=resp("in_progress", []))
            ev("response.in_progress", response=resp("in_progress", []))
            fm.pause(step.first_delay)
            for i, it in enumerate(output):
                if it["type"] == "reasoning":
                    ev("response.output_item.added", output_index=i, item={**it, "summary": [], "encrypted_content": None})
                    if step.thinking:
                        ev("response.reasoning_summary_part.added", item_id=it["id"], output_index=i, summary_index=0,
                           part={"type": "summary_text", "text": ""})
                        for p in _pieces(step.thinking, step.chunk):
                            ev("response.reasoning_summary_text.delta", item_id=it["id"], output_index=i, summary_index=0, delta=p)
                            fm.pause(step.delay)
                        ev("response.reasoning_summary_text.done", item_id=it["id"], output_index=i, summary_index=0, text=step.thinking)
                    ev("response.output_item.done", output_index=i, item=it)
                elif it["type"] == "message":
                    ev("response.output_item.added", output_index=i, item={**it, "status": "in_progress", "content": []})
                    kind = "response.refusal.delta" if refusal else "response.output_text.delta"
                    for p in _pieces(step.text, step.chunk):
                        ev(kind, item_id=it["id"], output_index=i, content_index=0, delta=p)
                        fm.pause(step.delay)
                    ev("response.output_item.done", output_index=i, item=it)
                else:
                    ev("response.output_item.added", output_index=i, item={**it, "arguments": "", "status": "in_progress"})
                    for p in _pieces(it["arguments"], step.chunk):
                        ev("response.function_call_arguments.delta", item_id=it["id"], output_index=i, delta=p)
                    ev("response.function_call_arguments.done", item_id=it["id"], output_index=i, arguments=it["arguments"])
                    ev("response.output_item.done", output_index=i, item=it)
            ev("response.incomplete" if length else "response.completed", response={**final, "output": []} if fm.plan_quirks else final)

        # —— Claude ——
        def _claude(self, step: Step, rec: dict) -> None:
            body = rec["body"]
            if isinstance(step, Fail):
                headers = {"request-id": f"req_fake_{uuid.uuid4().hex[:12]}"}
                if step.retry_after is not None:
                    headers["retry-after"] = str(step.retry_after)
                self._send(step.status, {"type": "error", "error": {"type": step.type or CLAUDE_ERRORS.get(step.status, "api_error"),
                                                                    "message": step.message}}, headers=headers)
                return
            if isinstance(step, Refusal):
                step = Reply(text=step.text, stop="refusal")
            mid = f"msg_fake_{uuid.uuid4().hex[:12]}"
            model = str(getattr(step, "model", None) or body.get("model") or "claude-fake")
            if isinstance(step, Broken):
                self._sse_start()
                self._ev({"type": "message_start", "message": self._message(mid, model, [], None, {"in": 10, "out": 1})}, "message_start")
                self._ev({"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}, "content_block_start")
                self._ev({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": step.text[:step.after]}},
                         "content_block_delta")
                if step.error:
                    kind = "overloaded_error" if "overload" in step.error.lower() else "api_error"
                    self._ev({"type": "error", "error": {"type": kind, "message": step.error}}, "error")
                return
            assert isinstance(step, Reply)
            calls = [(fm.next_id("toolu_fake_"), str(n), a if isinstance(a, str) else json.dumps(a, ensure_ascii=False)) for n, a in step.calls]
            stop = {None: "tool_use" if calls else "end_turn", "length": "max_tokens"}.get(step.stop, step.stop)
            u = _usage(step, rec, calls)
            blocks: list[dict] = []
            if step.thinking:
                blocks.append({"type": "thinking", "thinking": step.thinking, "signature": step.signature})
            if step.text:
                blocks.append({"type": "text", "text": step.text})
            for tid, name, args in calls:
                try:
                    inp = json.loads(args)
                except ValueError:
                    inp = {}
                blocks.append({"type": "tool_use", "id": tid, "name": name, "input": inp})
            if not body.get("stream"):
                fm.pause(step.first_delay)
                self._send(200, self._message(mid, model, blocks, stop, u))
                return
            self._sse_start()
            self._ev({"type": "message_start", "message": self._message(mid, model, [], None, {**u, "out": 1})}, "message_start")
            self._ev({"type": "ping"}, "ping")
            fm.pause(step.first_delay)
            i = 0
            if step.thinking:
                self._block(i, {"type": "thinking", "thinking": "", "signature": ""},
                            [{"type": "thinking_delta", "thinking": p} for p in _pieces(step.thinking, step.chunk)]
                            + [{"type": "signature_delta", "signature": step.signature}], step.delay)
                i += 1
            if step.text:
                self._block(i, {"type": "text", "text": ""}, [{"type": "text_delta", "text": p} for p in _pieces(step.text, step.chunk)], step.delay)
                i += 1
            for tid, name, args in calls:
                self._block(i, {"type": "tool_use", "id": tid, "name": name, "input": {}},
                            [{"type": "input_json_delta", "partial_json": p} for p in _pieces(args, step.chunk)], 0)
                i += 1
            self._ev({"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None}, "usage": {"output_tokens": u["out"]}},
                     "message_delta")
            self._ev({"type": "message_stop"}, "message_stop")

        def _message(self, mid: str, model: str, content: list, stop: str | None, u: dict) -> dict:
            return {"id": mid, "type": "message", "role": "assistant", "model": model, "content": content, "stop_reason": stop,
                    "stop_sequence": None, "usage": {"input_tokens": u.get("in", 0), "output_tokens": u.get("out", 0),
                                                     "cache_read_input_tokens": u.get("cache_read", 0),
                                                     "cache_creation_input_tokens": u.get("cache_write", 0)}}

        def _block(self, i: int, start: dict, deltas: list[dict], delay: float) -> None:
            self._ev({"type": "content_block_start", "index": i, "content_block": start}, "content_block_start")
            for d in deltas:
                self._ev({"type": "content_block_delta", "index": i, "delta": d}, "content_block_delta")
                fm.pause(delay)
            self._ev({"type": "content_block_stop", "index": i}, "content_block_stop")

        # —— Gateway 的 llm-task ——
        def _llm_task(self, step: Step, rec: dict) -> None:
            if isinstance(step, Fail):
                self._send(step.status, {"ok": False, "error": {"message": step.message}})
                return
            if not isinstance(step, Reply):  # 拒答、断流：llm-task 那边都是「回的不是 JSON」
                self._send(200, {"ok": False, "error": {"message": "LLM returned invalid JSON"}})
                return
            fm.pause(step.first_delay)
            text = step.text.strip()
            m = FENCE.match(text)
            try:
                parsed = json.loads(m.group(1) if m else text)
            except ValueError:
                self._send(200, {"ok": False, "error": {"message": "LLM returned invalid JSON"}})
                return
            self._send(200, {"ok": True, "result": {"content": [{"type": "text", "text": json.dumps(parsed, ensure_ascii=False, indent=2)}],
                                                    "details": {"json": parsed, "provider": "fake", "model": step.model or "fake-llm-task"}}})

    return Handler


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m evals.fakemodel", description="假模型服务器（OpenAI 兼容 + Responses + Claude + llm-task），按剧本回放")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--script", help="剧本 JSON 文件：一个列表，每条见本文件开头")
    ap.add_argument("--key", help="要求带的密钥（不写就不查）")
    ap.add_argument("--echo", action="store_true", help="剧本用完以后回「echo: 最后一句 user」（不写就回 ok）")
    a = ap.parse_args(argv)
    steps: list[Step] = []
    if a.script:
        with open(a.script, encoding="utf8") as f:
            steps = [step_from_dict(d) for d in json.load(f)]
    default: Step | Responder = (lambda rec: Reply(text="echo: " + rec.get("user", "")[-400:])) if a.echo else Reply(text="ok")
    fm = FakeModelServer(steps, default=default, key=a.key, host=a.host, port=a.port).start()
    print(f"fake model on {fm.url}  OpenAI: {fm.openai_url}（Responses: {fm.openai_url}/responses）  Claude: {fm.anthropic_url}  "
          f"llm-task: {fm.url}/tools/invoke", flush=True)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        fm.stop()


if __name__ == "__main__":
    main()
