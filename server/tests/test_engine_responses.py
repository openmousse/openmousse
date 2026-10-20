"""OpenAI 的 Responses API 后端（engine/provider_responses.py）：普通密钥写 api = "openai-responses" 就走它，ChatGPT 套餐也走它。
模型是 evals/fakemodel.py 的假服务（真的走 HTTP 的 /v1/responses），不连网、不花钱。
重点：流里的字 / 思考摘要 / 函数调用拼回来；store = false 时思考项带加密内容原样发回（不带 id）、换了型号不发；加密的思考被拒去掉再来；
不认的可选字段去掉再来；错误按状态码和错误码分（备用链要的 kind / retryable / status）；没写 api 的照旧走 /chat/completions。
最后几个是 ChatGPT 套餐线上回包的回放（chatgpt_plan_stream.sse：照 10/20 录到的事件、字段和头的样子，值都是编的）：不标 Content-Type 的流。"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

import engine
from engine import loop, provider_responses, providers
from engine.errors import EngineError
from engine.session import MemorySession, system_note, text_of, user
from evals.fakemodel import Broken, Fail, FakeModelServer, Raw, Refusal, Reply


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def oa(server_json, monkeypatch, request):
    """一个假服务；provider oa 走 Responses（不要密钥），provider plain 是同一个服务的 /chat/completions。"""
    fm = FakeModelServer([]).start()
    request.addfinalizer(fm.stop)
    monkeypatch.setattr(loop, "MODEL_RETRIES", (0, 0))
    monkeypatch.setattr(provider_responses, "RETRY_DELAYS", (0, 0))
    server_json({"engine": {"model": "oa/gpt-x", "providers": {
        "oa": {"api": "openai-responses", "url": fm.openai_url, "key_env": ""},
        "plain": {"api": "openai", "url": fm.openai_url, "key_env": ""}}}})
    return fm


def echo_tool(log: list) -> engine.Tool:
    async def go(args, ctx):
        log.append(args)
        return f"echoed {args['x']}"
    return engine.Tool(name="echo", description="Echo x back.", schema={"type": "object", "required": ["x"], "properties": {"x": {"type": "string"}}},
                       run=go)


def other_tool() -> engine.Tool:
    async def go(args, ctx):
        return "ok"
    return engine.Tool(name="alpha", description="A tool.", schema={"type": "object", "properties": {}}, run=go)


def test_stream_text_thinking_and_a_tool_loop_with_encrypted_reasoning_replay(oa):
    oa.push(Reply(thinking="Plan it.", text="Let me check.", calls=[("echo", {"x": "a"})], encrypted="enc-blob-1"), Reply(text="Done."))
    log: list = []
    seen: list = []
    session = MemorySession()
    spec = engine.Spec(kind="owner", model="oa/gpt-x", system="Be brief.", tools=[echo_tool(log), other_tool()], display="summarized",
                       max_tokens=900, effort="max")
    res = run(engine.run_turn(spec, session, [user("hi"), system_note("Background changed: it is raining.")], on_event=seen.append))
    assert res.status == "done" and res.text == "Done." and log == [{"x": "a"}]
    kinds = [e["type"] for e in seen]
    assert "thinking" in kinds and "tool_start" in kinds and "text" in kinds
    assert "".join(e["text"] for e in seen if e["type"] == "thinking") == "Plan it."
    first, second = oa.of("responses")[0]["body"], oa.of("responses")[1]["body"]
    assert first["stream"] is True and first["store"] is False and first["instructions"] == "Be brief."
    assert [t["name"] for t in first["tools"]] == ["alpha", "echo"] and first["tool_choice"] == "auto"  # 按名字排
    assert first["include"] == ["reasoning.encrypted_content"] and first["reasoning"] == {"effort": "xhigh", "summary": "auto"}
    assert first["max_output_tokens"] == 900  # 普通密钥照给（套餐不给，见 test_engine_chatgpt）
    assert first["input"][0] == {"role": "user", "content": [{"type": "input_text", "text": "hi"}]}
    assert first["input"][1] == {"role": "developer", "content": [{"type": "input_text", "text": "Background changed: it is raining."}]}
    items = second["input"]
    reasoning = [i for i in items if i.get("type") == "reasoning"]
    assert reasoning == [{"type": "reasoning", "summary": [{"type": "summary_text", "text": "Plan it."}], "encrypted_content": "enc-blob-1"}]
    call = next(i for i in items if i.get("type") == "function_call")
    out = next(i for i in items if i.get("type") == "function_call_output")
    assert call["name"] == "echo" and json.loads(call["arguments"]) == {"x": "a"} and out["call_id"] == call["call_id"]
    assert out["output"] == "echoed a" and "id" not in call  # store = false：不带服务端的 id
    msg = next(i for i in items if i.get("type") == "message")
    assert msg["role"] == "assistant" and msg["content"][0]["text"] == "Let me check." and "id" not in msg
    final = res.messages[-1]
    assert final["api"] == "openai-responses" and final["provider"] == "oa" and final["usage"]["out"] > 0 and isinstance(final["raw"], list)


def test_reasoning_from_another_model_is_not_sent_back(oa):
    oa.push(Reply(text="ok"))
    prev = {"role": "assistant", "content": [{"type": "thinking", "text": "old"}, {"type": "text", "text": "Earlier answer."}],
            "api": "openai-responses", "provider": "oa", "model": "gpt-old", "stop": "end", "usage": {},
            "raw": [{"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "enc-old"},
                    {"type": "message", "id": "msg_1", "role": "assistant", "content": [{"type": "output_text", "text": "Earlier answer."}]}]}
    session = MemorySession([user("before"), prev])
    res = run(engine.run_turn(engine.Spec(kind="owner", model="oa/gpt-x"), session, user("now")))
    assert res.status == "done"
    items = oa.of("responses")[0]["body"]["input"]
    assert not [i for i in items if i.get("type") == "reasoning"]
    assert {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Earlier answer.", "annotations": []}],
            "status": "completed"} in items


def test_refused_encrypted_reasoning_is_dropped_and_sent_once_more(oa):
    oa.push(Reply(thinking="t", calls=[("echo", {"x": "b"})]), Fail(400, "The encrypted content could not be decrypted.", code="invalid_encrypted_content"),
            Reply(text="fine"))
    res = run(engine.run_turn(engine.Spec(kind="owner", model="oa/gpt-x", tools=[echo_tool([])]), MemorySession(), user("go")))
    assert res.status == "done" and res.text == "fine"
    second, third = oa.of("responses")[1]["body"], oa.of("responses")[2]["body"]
    assert [i for i in second["input"] if i.get("type") == "reasoning"] and not [i for i in third["input"] if i.get("type") == "reasoning"]


def test_unsupported_optional_fields_are_dropped_and_retried(oa):
    oa.push(Fail(400, "Unsupported parameter: 'reasoning.effort' is not supported with this model.", param="reasoning.effort"),
            Fail(400, "Encrypted content is not supported with this model.", param="include"), Reply(text="ok"))
    res = run(engine.run_turn(engine.Spec(kind="owner", model="oa/gpt-x", effort="low"), MemorySession(), user("x")))
    assert res.status == "done"
    bodies = [r["body"] for r in oa.of("responses")]
    assert "reasoning" in bodies[0] and "reasoning" not in bodies[1] and "include" in bodies[1] and "include" not in bodies[2]


async def _error(fm, step, *, quick: bool = True) -> EngineError:
    fm.push(step)
    p = {"name": "oa", "api": "openai-responses", "url": fm.openai_url, "key": "", "key_env": ""}
    req = providers.Request(messages=[user("x")], quick=quick)
    with pytest.raises(EngineError) as ei:
        async for _ in provider_responses.stream(p, "gpt-x", req):
            pass
    return ei.value


def test_errors_carry_kind_retryable_and_status_for_the_fallback_chain(oa):
    e = run(_error(oa, Fail(429, "limit", code="subscription_sharing_usage_limit_exceeded")))
    assert e.status == 429 and e.kind == "http" and not e.retryable and "chatgpt.com/settings/usage" in str(e)
    assert e.code == "subscription_sharing_usage_limit_exceeded"
    e = run(_error(oa, Fail(503, "try later", code="subscription_sharing_usage_unavailable")))
    assert e.status == 503 and e.retryable
    e = run(_error(oa, Fail(401, "bad token")))
    assert e.kind == "config" and e.status == 401 and not e.retryable
    e = run(_error(oa, Fail(403, "no", code="subscription_sharing_user_not_eligible")))
    assert e.kind == "config" and "can't use its plan" in str(e)
    e = run(_error(oa, Fail(503, "Direct routing is unavailable", type="detail")))  # 套餐准入那一层的 {"detail": "…"}
    assert e.status == 503 and "Direct routing is unavailable" in str(e) and e.retryable
    e = run(_error(oa, Fail(404, "The model 'gpt-x' does not exist", code="model_not_found")))
    assert e.status == 404 and not e.retryable
    e = run(_error(oa, Fail(400, "Your input exceeds the context window of this model.", code="context_length_exceeded")))
    from engine import compact, fallback
    assert compact.is_overflow(e) and fallback.reason_of(e) == "context_overflow"
    e = run(_error(oa, Broken("half", error="usage limit", code="subscription_sharing_usage_limit_exceeded")))
    assert e.status == 429 and not e.retryable
    e = run(_error(oa, Broken("half")))  # 断了、没收尾
    assert e.kind == "output" and "ended before" in str(e) and not e.retryable  # 已经出了字
    e = run(_error(oa, Broken("", after=0)))
    assert e.kind == "output" and e.retryable  # 还没出字：能再来


def test_whole_json_reply_incomplete_and_refusal(oa):
    whole = {"id": "resp_1", "object": "response", "status": "completed", "model": "gpt-x", "usage": {"input_tokens": 12, "output_tokens": 3,
             "input_tokens_details": {"cached_tokens": 10}},
             "output": [{"type": "message", "id": "m1", "role": "assistant", "content": [{"type": "output_text", "text": "Whole reply."}]}]}
    oa.push(Raw(json.dumps(whole)))
    res = run(engine.run_turn(engine.Spec(kind="owner", model="oa/gpt-x"), MemorySession(), user("x")))
    assert res.status == "done" and res.text == "Whole reply." and res.usage["cache_read"] == 10 and res.usage["in"] == 2
    oa.push(Reply(text="cut", stop="length"))
    res = run(engine.run_turn(engine.Spec(kind="owner", model="oa/gpt-x"), MemorySession(), user("x")))
    assert res.status == "length"
    oa.push(Refusal("No."))
    res = run(engine.run_turn(engine.Spec(kind="owner", model="oa/gpt-x"), MemorySession(), user("x")))
    assert res.status == "refused" and res.text == "No."


def test_json_mode_and_images(oa, tmp_path):
    oa.push(Reply(text='{"a": 1}'))
    from engine import oneshot
    v, via = run(oneshot.ask_json("Give JSON.", {"q": 1}, model="oa/gpt-x"))
    assert v == {"a": 1} and via == "builtin:oa/gpt-x"
    body = oa.of("responses")[0]["body"]
    assert body["text"] == {"format": {"type": "json_object"}} and "tools" not in body
    oa.push(Reply(text="I see a cat."))
    pic = {"role": "user", "content": [{"type": "text", "text": "what is it"}, {"type": "image", "mime": "image/png", "data": "aGVsbG8="}]}
    run(engine.run_turn(engine.Spec(kind="owner", model="oa/gpt-x"), MemorySession(), pic))
    parts = oa.of("responses")[1]["body"]["input"][0]["content"]
    assert parts[1] == {"type": "input_image", "image_url": "data:image/png;base64,aGVsbG8=", "detail": "auto"}


def test_the_fake_server_speaks_responses_both_ways(oa):
    import httpx

    from evals.fakemodel import step_from_dict
    oa.push(Reply(text="Whole.", thinking="hm", calls=[("echo", {"x": "q"})]), Reply(text="Streamed.", thinking="hm"))
    body = {"model": "gpt-x", "input": [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}], "instructions": "Be brief."}
    j = httpx.post(f"{oa.openai_url}/responses", json={**body, "stream": False}).json()
    assert j["status"] == "completed" and [i["type"] for i in j["output"]] == ["reasoning", "message", "function_call"]
    assert j["output"][0]["encrypted_content"].startswith("enc:gpt-x:") and j["usage"]["output_tokens"] > 0
    lines = httpx.post(f"{oa.openai_url}/responses", json={**body, "stream": True}).text.splitlines()
    kinds = [json.loads(ln[5:])["type"] for ln in lines if ln.startswith("data:")]
    assert kinds[0] == "response.created" and kinds[-1] == "response.completed" and "response.reasoning_summary_text.delta" in kinds
    rec = oa.of("responses")[0]
    assert rec["system"] == "Be brief." and rec["user"] == "hi" and rec["bearer"] is None
    models = httpx.get(f"{oa.openai_url}/models").json()
    assert models["models"][0]["visibility"] == "list" and models["data"][0]["id"] == "fake-model"
    s = step_from_dict({"status": 429, "message": "limit", "code": "subscription_sharing_usage_limit_exceeded", "param": "x"})
    b = step_from_dict({"broken": {"text": "half", "error": "limit", "code": "subscription_sharing_usage_limit_exceeded"}})
    r = step_from_dict({"text": "a", "thinking": "t", "encrypted": "enc-1"})
    assert (s.code, s.param, b.code, r.encrypted) == ("subscription_sharing_usage_limit_exceeded", "x", "subscription_sharing_usage_limit_exceeded", "enc-1")


def test_providers_without_the_api_field_still_use_chat_completions(oa):
    oa.push(Reply(text="old path"))
    res = run(engine.run_turn(engine.Spec(kind="owner", model="plain/m"), MemorySession(), user("x")))
    assert res.text == "old path" and oa.of("openai") and not oa.of("responses")


# —— ChatGPT 套餐线上的回包（回放）——————————————————————————————————————————————————

PLAN_STREAM = (Path(__file__).parent / "chatgpt_plan_stream.sse").read_bytes()
PLAN_TEXT = "Hi there — from the fake plan ☕."
# 套餐 /responses 回包的头：名字是 10/20 录到的那些，值是编的。没有 Content-Type；请求 id 只在 x-oai-request-id
PLAN_HEADERS = {"date": "Tue, 20 Oct 2026 00:00:00 GMT", "connection": "keep-alive", "server": "cloudflare", "cf-cache-status": "DYNAMIC",
                "x-content-type-options": "nosniff", "x-oai-request-id": "00000000-0000-4000-8000-0000000fa4e0",
                "x-codex-turn-state": "fake-turn-state"}
PLAN_P = {"name": "chatgpt", "api": "openai-responses", "url": "https://plan.invalid/v1", "key": "fake-access-token-0001", "plan": True}


def replay(monkeypatch, body: bytes, *, chunk: int = 0, headers: dict | None = None) -> list[httpx.Request]:
    """回包原样交给 provider_responses（httpx.MockTransport，不经网络），分块发（chunked，和线上一样）；chunk 写了就切成这么多字节一块。
    → 收到的请求。"""
    seen: list[httpx.Request] = []

    async def pieces():
        step = chunk or max(len(body), 1)
        for i in range(0, len(body), step):
            yield body[i:i + step]

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, headers=PLAN_HEADERS if headers is None else headers, content=pieces())

    monkeypatch.setattr(provider_responses, "TRANSPORT", httpx.MockTransport(handle))
    return seen


async def _events(p: dict = PLAN_P) -> list[dict]:
    return [ev async for ev in provider_responses.stream(p, "gpt-fake-plan", providers.Request(messages=[user("hi")]))]


def test_the_plan_stream_without_a_content_type_is_read_as_a_stream(monkeypatch):
    """10/20 线上：套餐回的流不标 Content-Type，原来当整段 JSON 读 → JSONDecodeError → mousse agent 找 Astra 是 502。"""
    for body, chunk in ((PLAN_STREAM, 0), (PLAN_STREAM, 7), (b": keep-alive\n\n" + PLAN_STREAM, 0), (b"\xef\xbb\xbf" + PLAN_STREAM, 5)):
        seen = replay(monkeypatch, body, chunk=chunk)  # 一整块；7 字节一块（行、多字节的字都从中间断开）；注释开头；带 BOM
        evs = run(_events())
        assert "".join(e["text"] for e in evs if e["type"] == "text") == PLAN_TEXT
        msg = evs[-1]["message"]
        # response.completed 的 output 是空的（线上就这样）：字和输出项从 output_item.done 里拿
        assert evs[-1]["type"] == "done" and text_of(msg) == PLAN_TEXT and msg["stop"] == "end" and msg["raw"][0]["id"] == "msg_fake_plan_0001"
        assert msg["usage"] == {"in": 176, "out": 9, "cache_read": 1024, "cache_write": 0}
        assert seen[0].url.path == "/v1/responses" and seen[0].headers["authorization"] == "Bearer fake-access-token-0001"


def test_a_plan_stream_error_names_the_oai_request_id(monkeypatch):
    err = {"type": "error", "error": {"type": "invalid_request_error", "code": "subscription_sharing_usage_limit_exceeded",
                                      "message": "You have reached your usage limit.", "param": None}, "sequence_number": 1}
    created = PLAN_STREAM.split(b"\n\n")[0] + b"\n\n"
    replay(monkeypatch, created + f"event: error\ndata: {json.dumps(err)}\n\n".encode())
    with pytest.raises(EngineError) as ei:
        run(_events())
    assert ei.value.status == 429 and not ei.value.retryable and "(request 00000000-0000-4000-8000-0000000fa4e0)" in str(ei.value)


def test_unlabelled_whole_json_still_works_and_junk_is_a_clear_error(monkeypatch):
    whole = {"id": "resp_2", "object": "response", "status": "completed", "model": "gpt-fake-plan",
             "output": [{"type": "message", "id": "m2", "role": "assistant", "content": [{"type": "output_text", "text": "Whole, unlabelled."}]}]}
    replay(monkeypatch, json.dumps(whole, indent=1).encode(), chunk=9)
    assert text_of(run(_events())[-1]["message"]) == "Whole, unlabelled."
    errors = []
    for headers, page in ((PLAN_HEADERS, b"<html>\n<body>Bad gateway sk-proj-abcdefgh12345678 fake-access-token-0001</body>\n</html>\n"),
                          ({"content-type": "text/html; charset=utf-8"}, b"<!doctype html><title>Sign in</title>")):
        replay(monkeypatch, page, headers=headers)
        with pytest.raises(EngineError) as ei:  # 不再是 JSONDecodeError 漏出去
            run(_events())
        assert ei.value.kind == "output" and not ei.value.retryable
        errors.append(str(ei.value))
    assert errors == ["chatgpt: the reply is neither an event stream nor JSON (content-type: none): <html> <body>Bad gateway *** ***</body> </html> "
                      "(request 00000000-0000-4000-8000-0000000fa4e0)",
                      "chatgpt: the reply is neither an event stream nor JSON (content-type: text/html; charset=utf-8): "
                      "<!doctype html><title>Sign in</title>"]
