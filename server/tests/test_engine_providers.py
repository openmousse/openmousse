"""内核接模型的两家（engine/provider_anthropic.py、provider_openai.py）：用假的 HTTP 服务回放流，查请求长什么样、流怎么拼回来。
重点：Claude 的思考块（带签名）原样回放、工具结果并进一条 user、对话中间的 system 怎么放；兼容接口的工具参数分段拼、
reasoning_content 只在这一轮工具来回中间带、不认的字段去掉再试。不连网、不花钱。"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

import engine
from engine import models, provider_anthropic, provider_openai
from engine.session import MemorySession, system_note, text_of, tool_result, user


def run(coro):
    return asyncio.run(coro)


def sse(events: list[dict], named: bool = False) -> bytes:
    out = []
    for e in events:
        out.append((f"event: {e['type']}\n" if named else "") + "data: " + json.dumps(e) + "\n\n")
    if not named:
        out.append("data: [DONE]\n\n")
    return "".join(out).encode()


def echo_tool(log: list) -> engine.Tool:
    async def go(args, ctx):
        log.append(args)
        return f"echoed {args['x']}"
    return engine.Tool(name="echo", description="Echo x back.", schema={"type": "object", "required": ["x"], "properties": {"x": {"type": "string"}}}, run=go)


# —— OpenAI 兼容接口 ————————————————————————————————————————————————————————

def oa_chunk(delta: dict | None = None, finish: str | None = None, **extra) -> dict:
    return {"choices": [{"index": 0, "delta": delta or {}, "finish_reason": finish}], **extra}


@pytest.fixture
def oa(monkeypatch):
    """假的 /chat/completions：按顺序回放 responses（(状态码, 字节)），seen 记下每次请求的 JSON。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    state = {"responses": [], "seen": []}

    def handler(request: httpx.Request) -> httpx.Response:
        state["seen"].append(json.loads(request.content))
        assert request.headers["authorization"] == "Bearer test-key"
        code, body = state["responses"].pop(0)
        return httpx.Response(code, headers={"content-type": "text/event-stream" if code == 200 else "application/json"}, content=body)
    monkeypatch.setattr(provider_openai, "TRANSPORT", httpx.MockTransport(handler))
    return state


def test_openai_tool_loop_and_reasoning(oa):
    oa["responses"] = [
        (200, sse([oa_chunk({"role": "assistant", "reasoning_content": "Think"}), oa_chunk({"content": "Hello"}),
                   oa_chunk({"tool_calls": [{"index": 0, "id": "call_1", "type": "function", "function": {"name": "echo", "arguments": ""}}]}),
                   oa_chunk({"tool_calls": [{"index": 0, "function": {"arguments": '{"x": '}}]}),
                   oa_chunk({"tool_calls": [{"index": 0, "function": {"arguments": '"a"}'}}]}),
                   oa_chunk(finish="tool_calls"),
                   {"choices": [], "usage": {"prompt_tokens": 100, "completion_tokens": 20, "prompt_cache_hit_tokens": 60}}])),
        (200, sse([oa_chunk({"content": "done"}, finish="stop"), {"choices": [], "usage": {"prompt_tokens": 130, "completion_tokens": 5}}])),
        (200, sse([oa_chunk({"content": "again"}, finish="stop")])),
    ]
    log: list = []
    session = MemorySession()
    spec = engine.Spec(kind="owner", model="deepseek/deepseek-chat", system="be brief", tools=[echo_tool(log)])
    seen_events: list = []
    res = run(engine.run_turn(spec, session, user("hi"), on_event=seen_events.append))
    assert res.status == "done" and res.text == "done" and log == [{"x": "a"}]
    first, second = oa["seen"][0], oa["seen"][1]
    assert first["stream"] is True and first["stream_options"] == {"include_usage": True} and first["tools"][0]["function"]["name"] == "echo"
    assert "max_tokens" not in first  # 没给就不带（各家上限不一样）
    assert first["messages"][0] == {"role": "system", "content": "be brief"}
    a = second["messages"][2]
    assert a["role"] == "assistant" and a["content"] == "Hello" and a["reasoning_content"] == "Think"  # 这一轮工具来回中间：带思考
    assert a["tool_calls"][0]["function"]["arguments"] == '{"x": "a"}'
    assert second["messages"][3] == {"role": "tool", "tool_call_id": "call_1", "content": "echoed a"}
    assert res.usage["in"] == 40 + 130 and res.usage["cache_read"] == 60 and res.usage["out"] == 25
    assert any(e["type"] == "thinking" for e in seen_events) and any(e["type"] == "tool_start" for e in seen_events)
    run(engine.run_turn(spec, session, user("next")))
    third = oa["seen"][2]
    old = next(m for m in third["messages"] if m.get("tool_calls"))
    assert "reasoning_content" not in old  # 之前那一轮的思考不再带（DeepSeek 带了会报错）


def test_openai_drops_unsupported_field_and_json_mode(oa):
    oa["responses"] = [(400, b'{"error": {"message": "response_format is not supported"}}'),
                       (200, sse([oa_chunk({"content": '{"ok": true}'}, finish="stop")]))]
    v, _ = run(engine.ask_json("x", {}, model="deepseek/deepseek-chat"))
    assert v == {"ok": True}
    assert oa["seen"][0]["response_format"] == {"type": "json_object"} and "response_format" not in oa["seen"][1]


def test_openai_errors(oa, monkeypatch):
    oa["responses"] = [(401, b'{"error": {"message": "bad key"}}')]
    res = run(engine.run_turn(engine.Spec(kind="owner", model="deepseek/deepseek-chat"), MemorySession(), user("hi")))
    assert res.status == "error" and "HTTP 401" in res.error
    monkeypatch.delenv("DEEPSEEK_API_KEY")
    res = run(engine.run_turn(engine.Spec(kind="owner", model="deepseek/deepseek-chat"), MemorySession(), user("hi")))
    assert res.status == "error" and "no API key" in res.error


def test_openai_bad_tool_json_is_not_run(oa):
    oa["responses"] = [
        (200, sse([oa_chunk({"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "echo", "arguments": '{"x": "a'}}]}, finish="tool_calls")])),
        (200, sse([oa_chunk({"content": "sorry"}, finish="stop")])),
    ]
    log: list = []
    res = run(engine.run_turn(engine.Spec(kind="owner", model="deepseek/deepseek-chat", tools=[echo_tool(log)]), MemorySession(), user("go")))
    assert log == [] and "not valid JSON" in text_of(res.messages[2]) and res.text == "sorry"


def test_to_openai_converts_other_providers():
    msgs = [user("hi"), {"role": "assistant", "api": "anthropic", "provider": "anthropic", "model": "claude-opus-5-5",
                         "content": [{"type": "thinking", "text": "secret", "sig": "s"}, {"type": "text", "text": "hey"},
                                     {"type": "tool_call", "id": "toolu_1", "name": "echo", "args": {"x": "a"}}], "raw": [{"type": "text"}]},
            tool_result("toolu_1", "echo", "ok"), system_note("note")]
    out = provider_openai.to_openai("", msgs, "deepseek", "deepseek-chat")
    assert out[1] == {"role": "assistant", "content": "hey", "tool_calls": [{"id": "toolu_1", "type": "function",
                                                                          "function": {"name": "echo", "arguments": '{"x": "a"}'}}]}
    assert out[2]["role"] == "tool" and out[3] == {"role": "system", "content": "note"}


# —— Claude（官方 SDK，假的 HTTP 服务） ————————————————————————————————————————

anthropic = pytest.importorskip("anthropic")
httpx2 = pytest.importorskip("httpx2")


def msg_start(model="claude-opus-5-5", input_tokens=20):
    return {"type": "message_start", "message": {"id": "msg_1", "type": "message", "role": "assistant", "model": model, "content": [],
                                                 "stop_reason": None, "stop_sequence": None,
                                                 "usage": {"input_tokens": input_tokens, "output_tokens": 1, "cache_read_input_tokens": 5,
                                                           "cache_creation_input_tokens": 7}}}


def block(i, b):
    return [{"type": "content_block_start", "index": i, "content_block": b}]


def delta(i, d):
    return {"type": "content_block_delta", "index": i, "delta": d}


def stop(i):
    return {"type": "content_block_stop", "index": i}


def finish(reason, out=30):
    return [{"type": "message_delta", "delta": {"stop_reason": reason, "stop_sequence": None}, "usage": {"output_tokens": out}},
            {"type": "message_stop"}]


@pytest.fixture
def claude(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    state = {"responses": [], "seen": [], "headers": []}

    def handler(request):
        state["seen"].append(json.loads(request.content))
        state["headers"].append(dict(request.headers))
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=state["responses"].pop(0))

    def make(p):
        return anthropic.AsyncAnthropic(api_key=p["key"], max_retries=0,
                                        http_client=anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler)))
    monkeypatch.setattr(provider_anthropic, "make_client", make)
    return state


def test_claude_tool_loop_replays_signed_thinking(claude):
    claude["responses"] = [
        sse([msg_start(), *block(0, {"type": "thinking", "thinking": "", "signature": ""}),
             delta(0, {"type": "thinking_delta", "thinking": "Checking."}), delta(0, {"type": "signature_delta", "signature": "sig123"}), stop(0),
             *block(1, {"type": "text", "text": ""}), delta(1, {"type": "text_delta", "text": "Let me look."}), stop(1),
             *block(2, {"type": "tool_use", "id": "toolu_01", "name": "echo", "input": {}}),
             delta(2, {"type": "input_json_delta", "partial_json": '{"x": "a"}'}), stop(2), *finish("tool_use")], named=True),
        sse([msg_start(input_tokens=40), *block(0, {"type": "text", "text": ""}), delta(0, {"type": "text_delta", "text": "Done."}), stop(0),
             *finish("end_turn", 4)], named=True),
    ]
    log: list = []
    spec = engine.Spec(kind="owner", model="anthropic/claude-opus-5-5", system="be brief", tools=[echo_tool(log)], effort="low", display="summarized")
    seen: list = []
    res = run(engine.run_turn(spec, MemorySession(), [user("hi"), system_note("the user is on a phone")], on_event=seen.append))
    assert res.status == "done" and res.text == "Done." and log == [{"x": "a"}]
    first, second = claude["seen"]
    assert first["model"] == "claude-opus-5-5" and first["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert first["output_config"] == {"effort": "low"} and first["cache_control"] == {"type": "ephemeral"}
    assert first["system"] == [{"type": "text", "text": "be brief", "cache_control": {"type": "ephemeral"}}]
    assert first["tools"][0]["eager_input_streaming"] is True and first["tools"][0]["input_schema"]["required"] == ["x"]
    assert first["fallbacks"] == "default" and "server-side-fallback-2026-07-01" in claude["headers"][0].get("anthropic-beta", "")
    assert first["messages"][1] == {"role": "system", "content": "the user is on a phone"}  # 对话中间的 system 跟在 user 后面
    assert second["messages"][2] == {"role": "assistant", "content": [{"type": "thinking", "thinking": "Checking.", "signature": "sig123"},
                                                                       {"type": "text", "text": "Let me look."},
                                                                       {"type": "tool_use", "id": "toolu_01", "name": "echo", "input": {"x": "a"}}]}
    assert second["messages"][3] == {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_01",
                                                                   "content": [{"type": "text", "text": "echoed a"}]}]}
    assert second["messages"][:2] == first["messages"]  # 只往后补：前面一个字没动
    assert res.usage == {"in": 60, "out": 34, "cache_read": 10, "cache_write": 14, "cost": models.cost("anthropic/claude-opus-5-5",
                                                                                                      {"in": 60, "out": 34, "cache_read": 10, "cache_write": 14})}
    assert [e["text"] for e in seen if e["type"] == "thinking"] == ["Checking."]


def test_claude_refusal(claude):
    claude["responses"] = [sse([msg_start(), *finish("refusal", 0)], named=True)]
    res = run(engine.run_turn(engine.Spec(kind="owner", model="anthropic/claude-opus-5-5"), MemorySession(), user("hi")))
    assert res.status == "refused"


def test_claude_no_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    res = run(engine.run_turn(engine.Spec(kind="owner", model="anthropic/claude-opus-5-5"), MemorySession(), user("hi")))
    assert res.status == "error" and "ANTHROPIC_API_KEY" in res.error


def test_to_anthropic_placement_rules():
    caps_new, caps_old = models.anthropic_caps("claude-opus-5-5"), models.anthropic_caps("claude-sonnet-5")
    kimi = {"role": "assistant", "api": "openai", "provider": "moonshot", "model": "kimi-k3",
            "content": [{"type": "thinking", "text": "hmm"}, {"type": "text", "text": "ok"},
                        {"type": "tool_call", "id": "functions.echo:0", "name": "echo", "args": {"x": "a"}},
                        {"type": "tool_call", "id": "functions.echo:1", "name": "echo", "args": {"x": "b"}}]}
    msgs = [system_note("before anything"), user("hi"), kimi, tool_result("functions.echo:0", "echo", "1"), tool_result("functions.echo:1", "echo", "2"),
            system_note("tool output is hidden from the user")]
    out = provider_anthropic.to_anthropic(msgs, caps_new)
    assert out[0]["content"][0]["text"].startswith("<system-reminder>\nbefore anything")  # 第一条之前的 system 并进第一条 user
    assert out[1]["content"] == [{"type": "text", "text": "ok"}, {"type": "tool_use", "id": "functions_echo_0", "name": "echo", "input": {"x": "a"}},
                                 {"type": "tool_use", "id": "functions_echo_1", "name": "echo", "input": {"x": "b"}}]  # 别家的思考不发
    assert [b["tool_use_id"] for b in out[2]["content"]] == ["functions_echo_0", "functions_echo_1"]  # 一批工具结果一条 user
    assert out[3] == {"role": "system", "content": "tool output is hidden from the user"}
    old = provider_anthropic.to_anthropic(msgs, caps_old)  # Sonnet 5 不认对话中间的 system：折进前面那条 user
    assert old[-1]["role"] == "user" and "<system-reminder>" in old[-1]["content"][-1]["text"]
    then_user = provider_anthropic.to_anthropic([user("a"), system_note("x"), user("b")], caps_new)
    assert [m["role"] for m in then_user] == ["user", "user"]  # system 后面紧跟 user 不合规矩：折进前一条
