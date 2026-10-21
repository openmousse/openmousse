"""内核第 0 步 10/16 加固（对照 pi、OpenClaw、Hermes 的源码查出来的）：
会话始终完整（停、取消、出错都补齐工具结果）、回放前修历史、同一个会话一次一轮、关卡默认不放、参数小毛病自动修、输出截断、
兼容接口的各家脾气（断流、Ollama 的 index、<think>、Kimi 的 schema、Mistral 的 id、不认图、system 怎么放）、Claude 的
fallback 块、签名被拒再试、流里的 overloaded。模型都是假的，不连网。"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

import engine
from engine import loop, models, provider_anthropic, provider_openai, providers
from engine.errors import EngineError
from engine.session import DbSession, MemorySession, repair, system_note, text_of, tool_result, user
from test_engine import FakeModel, echo_tool, run
from test_engine_providers import block, delta, finish, msg_start, oa_chunk, sse, stop


@pytest.fixture
def fake(monkeypatch):
    def make(script):
        f = FakeModel(script)
        monkeypatch.setattr(providers, "stream", f.stream)
        return f
    return make


# —— 会话始终完整 ——————————————————————————————————————————————————————————

def test_repair_fills_missing_results_and_drops_bad_turns():
    a = {"role": "assistant", "api": "openai", "provider": "x", "model": "y", "stop": "tool",
         "content": [{"type": "tool_call", "id": "c1", "name": "echo", "args": {}}, {"type": "tool_call", "id": "c2", "name": "echo", "args": {}}]}
    msgs = [user("go"), a, system_note("held note"), tool_result("c1", "echo", "ok"), tool_result("zz", "echo", "orphan"),
            {"role": "assistant", "content": [{"type": "text", "text": "no"}], "stop": "refusal"},
            {"role": "assistant", "content": [{"type": "text", "text": "  "}], "stop": "end"},
            user("next"), {"role": "assistant", "content": [{"type": "text", "text": "half"}], "stop": "aborted"}]
    out = repair(msgs)
    roles = [(m["role"], text_of(m)[:20]) for m in out]
    assert roles[:5] == [("user", "go"), ("assistant", ""), ("tool", "ok"), ("tool", "No result: the turn "), ("system", "held note")]
    assert ("tool", "orphan") not in roles and ("assistant", "no") not in roles  # 对不上的结果、拒答、空的都不发
    assert out[-1]["stop"] == "aborted" and text_of(out[-1]).startswith("half") and "cut off" in text_of(out[-1])


def test_outer_cancel_mid_tool_leaves_a_valid_session(fake):
    fake([{"calls": [("c1", "echo", {"x": "a"}), ("c2", "echo", {"x": "b"})]}])
    s = DbSession.create("test:cancel-tool", kind="owner")

    async def go():
        task = asyncio.create_task(engine.run_turn(engine.Spec(kind="owner", model="deepseek/x", tools=[echo_tool([], delay=5)]), s, user("go")))
        await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    run(go())
    msgs = DbSession.get(s.id).messages()
    assert [m["role"] for m in msgs] == ["user", "assistant", "tool", "tool"]  # 两个调用都有结果，下一轮发得出去
    # 跑到一半被取消的那个不说「没做」（可能做了一半，第 3 项改的），没轮到的才是「没做」
    assert text_of(msgs[2]) == loop.CANCELLED_MID_CALL and "Not run" in text_of(msgs[3])


def test_outer_cancel_mid_stream_keeps_the_partial_reply(fake):
    fake([{"chunks": ["a", "b", "c", "d"], "delay": 0.05}])
    s = DbSession.create("test:cancel-text", kind="owner")

    async def go():
        task = asyncio.create_task(engine.run_turn(engine.Spec(kind="owner", model="deepseek/x"), s, user("hi")))
        await asyncio.sleep(0.12)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    run(go())
    msgs = DbSession.get(s.id).messages()
    assert [m["role"] for m in msgs] == ["user", "assistant"] and msgs[1]["stop"] == "aborted" and text_of(msgs[1])


def test_one_turn_at_a_time_per_session(fake):
    order: list[str] = []
    f = fake([{"chunks": ["first"], "delay": 0.1}, {"text": "second"}])
    s = DbSession.create("test:lock", kind="cyber")

    async def go():
        async def one(text):
            res = await engine.run_turn(engine.Spec(kind="cyber", model="deepseek/x"), s, user(text))
            order.append(res.text)
        await asyncio.gather(one("a"), one("b"))
    run(go())
    assert order == ["first", "second"]
    assert [text_of(m) for m in f.requests[1][1].messages] == ["a", "first", "b"]  # 第二轮看得到第一轮，没有交错
    assert [m["role"] for m in DbSession.get(s.id).messages()] == ["user", "assistant", "user", "assistant"]


def test_rewind_only_to_user_messages():
    s = DbSession.create("test:rewind-rule", kind="owner")
    s.append(user("hi"))
    a = s.append({"role": "assistant", "content": [{"type": "text", "text": "yo"}]})
    with pytest.raises(ValueError):
        s.rewind(a)


def test_pending_steers_come_back_when_the_turn_fails(fake, monkeypatch):
    monkeypatch.setattr(loop, "MODEL_RETRIES", ())
    control = engine.Control()
    control.steer(user("also this"))
    fake([EngineError("boom")])
    res = run(engine.run_turn(engine.Spec(kind="owner", model="deepseek/x"), MemorySession(), user("hi"), control=control))
    assert res.status == "error" and [text_of(m) for m in res.pending] == ["also this"]


# —— 工具和关卡 ———————————————————————————————————————————————————————————

def test_irreversible_tool_needs_a_gate(fake):
    log: list = []
    t = echo_tool(log)
    t.reversible = False
    fake([{"calls": [("c1", "echo", {"x": "a"})]}, {"text": "ok"}])
    res = run(engine.run_turn(engine.Spec(kind="owner", model="deepseek/x", tools=[t]), MemorySession(), user("go")))
    assert log == [] and "NOT consented" in text_of(res.messages[2])

    async def gate(tool, args, ctx):
        return {"status": "approval-pending", "id": "ib_1", "message": "Sent to the inbox for the user's OK."}
    fake([{"calls": [("c1", "echo", {"x": "a"})]}, {"text": "asked"}])
    res = run(engine.run_turn(engine.Spec(kind="owner", model="deepseek/x", tools=[t], gate=gate), MemorySession(), user("go")))
    tr = res.messages[2]
    assert log == [] and tr["error"] is False and json.loads(text_of(tr))["status"] == "approval-pending"  # 等点头不算出错


def test_stop_while_the_gate_is_asking(fake):
    log: list = []
    control = engine.Control()

    async def gate(tool, args, ctx):
        ctx.control.abort()  # 关卡问到一半用户点了停
        return None
    fake([{"calls": [("c1", "echo", {"x": "a"})]}])
    res = run(engine.run_turn(engine.Spec(kind="owner", model="deepseek/x", tools=[echo_tool(log)], gate=gate), MemorySession(), user("go"),
                              control=control))
    assert log == [] and res.status == "aborted" and "stopped" in text_of(res.messages[2])


def test_args_are_coerced_and_output_clipped(fake):
    seen: list = []

    async def big(args, ctx):
        seen.append(args)
        return "x" * 50000
    t = engine.Tool(name="big", description="", run=big, max_output=1000,
                    schema={"type": "object", "required": ["n"], "properties": {"n": {"type": "integer"}, "on": {"type": "boolean"},
                                                                             "note": {"type": "string"}}})
    fake([{"calls": [("c1", "big", {"n": "42", "on": "true", "note": None})]}, {"text": "done"}])
    res = run(engine.run_turn(engine.Spec(kind="owner", model="deepseek/x", tools=[t]), MemorySession(), user("go")))
    assert seen == [{"n": 42, "on": True}]
    out = text_of(res.messages[2])
    assert len(out) < 1200 and "characters cut" in out


# —— OpenAI 兼容接口的各家脾气 ———————————————————————————————————————————————

@pytest.fixture
def oa(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    monkeypatch.setenv("MOONSHOT_API_KEY", "k")
    monkeypatch.setattr(provider_openai, "RETRY_DELAYS", (0, 0))
    monkeypatch.setattr(loop, "MODEL_RETRIES", ())
    state = {"responses": [], "seen": []}

    def handler(request):
        state["seen"].append(json.loads(request.content))
        code, body = state["responses"].pop(0)
        return httpx.Response(code, headers={"content-type": "text/event-stream"}, content=body)
    monkeypatch.setattr(provider_openai, "TRANSPORT", httpx.MockTransport(handler))
    return state


def raw_sse(chunks: list[dict], done: bool = True) -> bytes:
    return ("".join("data: " + json.dumps(c) + "\n\n" for c in chunks) + ("data: [DONE]\n\n" if done else "")).encode()


def test_stream_that_ends_early_is_an_error(oa):
    oa["responses"] = [(200, raw_sse([oa_chunk({"content": "half"})], done=False))] * 3
    res = run(engine.run_turn(engine.Spec(kind="owner", model="deepseek/deepseek-flash"), MemorySession(), user("hi")))
    assert res.status == "error" and "ended before" in res.error


def test_ollama_parallel_calls_reuse_index_zero(oa, monkeypatch):
    oa["responses"] = [
        (200, raw_sse([oa_chunk({"tool_calls": [{"index": 0, "id": "a", "function": {"name": "echo", "arguments": '{"x": "1"}'}}]}),
                       oa_chunk({"tool_calls": [{"index": 0, "id": "b", "function": {"name": "echo", "arguments": '{"x": "2"}'}}]}),
                       oa_chunk(finish="tool_calls")])),
        (200, raw_sse([oa_chunk({"content": "ok"}, finish="stop")])),
    ]
    log: list = []
    run(engine.run_turn(engine.Spec(kind="owner", model="deepseek/deepseek-flash", tools=[echo_tool(log)]), MemorySession(), user("go")))
    assert [a for a, _ in log] == [{"x": "1"}, {"x": "2"}]


def test_think_tags_become_thinking(oa):
    oa["responses"] = [(200, raw_sse([oa_chunk({"content": "<thi"}), oa_chunk({"content": "nk>hmm</th"}), oa_chunk({"content": "ink>\n{\"a\": 1}"}),
                                      oa_chunk(finish="stop")]))]
    seen: list = []
    v, _ = run(engine.ask_json("x", {}, model="deepseek/deepseek-flash"))
    assert v == {"a": 1}
    oa["responses"] = [(200, raw_sse([oa_chunk({"content": "<think>a"}), oa_chunk({"content": "b</think>Hi"}), oa_chunk(finish="stop")]))]
    res = run(engine.run_turn(engine.Spec(kind="owner", model="deepseek/deepseek-flash"), MemorySession(), user("hi"), on_event=seen.append))
    assert res.text == "Hi" and "".join(e["text"] for e in seen if e["type"] == "thinking") == "ab"


def test_moonshot_quirks_in_the_request(oa):
    oa["responses"] = [(200, raw_sse([oa_chunk({"content": "ok"}, finish="stop"), {"choices": [{"index": 0, "delta": {},
                                                                                                "usage": {"prompt_tokens": 7, "completion_tokens": 2}}]}]))]
    t = engine.Tool(name="t", description="", run=lambda a, c: None,
                    schema={"type": "object", "properties": {"q": {}, "r": {"anyOf": [{"type": "string"}, {"description": "x"}]}}})
    res = run(engine.run_turn(engine.Spec(kind="owner", model="moonshot/kimi-k3", tools=[t], effort="xhigh"), MemorySession(), user("hi")))
    body = oa["seen"][0]
    params = body["tools"][0]["function"]["parameters"]
    assert params["required"] == [] and params["properties"]["q"]["type"] == "string"  # Kimi：补 required 和 type
    assert params["properties"]["r"]["anyOf"][1]["type"] == "string"
    assert body["reasoning_effort"] == "high"  # Kimi K3 只认 low / medium / high / max
    assert res.usage["in"] == 7  # Moonshot 把用量放在 choice 里


def test_ids_images_and_merging():
    mistral = {"name": "mistral", "tool_id": "mistral9"}
    a = {"role": "assistant", "content": [{"type": "tool_call", "id": "toolu_0123456789abcdef", "name": "e", "args": {}}], "stop": "tool"}
    out = provider_openai.to_openai("", [user("go"), a, tool_result("toolu_0123456789abcdef", "e", "ok")], mistral, "m")
    cid = out[1]["tool_calls"][0]["id"]
    assert len(cid) == 9 and cid.isalnum() and out[2]["tool_call_id"] == cid
    pic = user("look", [{"type": "image", "mime": "image/png", "data": "AAAA"}])
    ds = provider_openai.to_openai("", [pic], {"name": "deepseek", "images": False}, "deepseek-flash")
    assert "can't read images" in ds[0]["content"] and "image_url" not in json.dumps(ds)
    merged = provider_openai.to_openai("", [user("a"), user("b")], {"name": "x"}, "y")
    assert len(merged) == 1 and merged[0]["content"] == "a\n\nb"


# —— Claude ————————————————————————————————————————————————————————————————

anthropic = pytest.importorskip("anthropic")
httpx2 = pytest.importorskip("httpx2")


@pytest.fixture
def claude(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setattr(loop, "MODEL_RETRIES", (0, 0))
    state = {"responses": [], "seen": []}

    def handler(request):
        state["seen"].append(json.loads(request.content))
        code, body = state["responses"].pop(0)
        ctype = "text/event-stream" if code == 200 else "application/json"
        return httpx2.Response(code, headers={"content-type": ctype}, content=body)

    def make(p):
        return anthropic.AsyncAnthropic(api_key="k", max_retries=0, http_client=anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler)))
    monkeypatch.setattr(provider_anthropic, "make_client", make)
    return state


def test_claude_fallback_block_and_whitespace_text(claude):
    claude["responses"] = [(200, sse([msg_start(model="claude-opus-5"), *block(0, {"type": "thinking", "thinking": "", "signature": ""}),
                                delta(0, {"type": "signature_delta", "signature": "s0"}), stop(0),
                                *block(1, {"type": "text", "text": ""}), delta(1, {"type": "text_delta", "text": "partial "}), stop(1),
                                *block(2, {"type": "fallback", "from": {"model": "claude-opus-5-5"}, "to": {"model": "claude-opus-5"}}), stop(2),
                                *block(3, {"type": "text", "text": ""}), delta(3, {"type": "text_delta", "text": "\n\n"}), stop(3),
                                *block(4, {"type": "text", "text": ""}), delta(4, {"type": "text_delta", "text": "rest"}), stop(4),
                                *finish("end_turn")], named=True)),
                           (200, sse([msg_start(), *block(0, {"type": "text", "text": ""}), delta(0, {"type": "text_delta", "text": "k"}), stop(0),
                                      *finish("end_turn")], named=True))]
    s = MemorySession()
    res = run(engine.run_turn(engine.Spec(kind="owner", model="anthropic/claude-opus-5-5"), s, user("hi")))
    assert res.status == "done", res.error
    msg = res.messages[1]
    assert msg["served"] == "claude-opus-5" and msg["model"] == "claude-opus-5-5"
    assert [b["type"] for b in msg["raw"]] == ["text", "text", "text"]  # 分界之前只留文字；fallback 块不留
    run(engine.run_turn(engine.Spec(kind="owner", model="anthropic/claude-opus-5-5"), s, user("again")))
    replay = claude["seen"][1]["messages"][1]["content"]
    assert [b["text"] for b in replay] == ["partial ", "rest"]  # 空白的文字块不发


def test_claude_signature_rejected_then_retried_without_thinking(claude):
    signed = {"role": "assistant", "api": "anthropic", "provider": "anthropic", "model": "claude-opus-5-5", "stop": "end",
              "content": [{"type": "thinking", "text": "t", "sig": "bad"}, {"type": "text", "text": "hi"}],
              "raw": [{"type": "thinking", "thinking": "t", "signature": "bad"}, {"type": "text", "text": "hi"}]}
    err = json.dumps({"type": "error", "error": {"type": "invalid_request_error", "message": "messages.1.content.0: Invalid `signature` in `thinking` block"}})
    claude["responses"] = [(400, err.encode()),
                           (200, sse([msg_start(), *block(0, {"type": "text", "text": ""}), delta(0, {"type": "text_delta", "text": "ok"}), stop(0),
                                      *finish("end_turn")], named=True))]
    res = run(engine.run_turn(engine.Spec(kind="owner", model="anthropic/claude-opus-5-5"), MemorySession([user("a"), signed]), user("b")))
    assert res.status == "done" and res.text == "ok"
    retry = claude["seen"][1]["messages"][1]["content"]
    assert [b["type"] for b in retry] == ["text"]


def test_claude_midstream_overload_is_retried(claude):
    overload = ("event: message_start\ndata: " + json.dumps(msg_start()) + "\n\n" +
                "event: error\ndata: " + json.dumps({"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}) + "\n\n").encode()
    ok = sse([msg_start(), *block(0, {"type": "text", "text": ""}), delta(0, {"type": "text_delta", "text": "fine"}), stop(0), *finish("end_turn")],
             named=True)
    claude["responses"] = [(200, overload), (200, ok)]
    res = run(engine.run_turn(engine.Spec(kind="owner", model="anthropic/claude-opus-5-5", cache_ttl="1h", tools=[echo_tool([])]),
                              MemorySession(), user("hi")))
    assert res.status == "done" and res.text == "fine" and len(claude["seen"]) == 2
    body = claude["seen"][1]
    assert body["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert [t["name"] for t in body["tools"]] == ["echo"]


def test_claude_tool_ids_capped():
    assert len(provider_anthropic._tid("x" * 100)) == 64
    assert provider_anthropic._tid("functions.echo:0") == "functions_echo_0"


def test_unknown_provider_settings_default_safely():
    p = models.provider("deepseek")
    assert p["reasoning_replay"] == "all" and p["images"] is False
    assert models.provider("ollama")["require_finish"] is False


def test_claude_one_shot_caches_only_the_rules(claude):
    claude["responses"] = [(200, sse([msg_start(), *block(0, {"type": "text", "text": ""}), delta(0, {"type": "text_delta", "text": '{"verdict": "pass"}'}),
                                      stop(0), *finish("end_turn")], named=True))]
    v, _ = run(engine.ask_json("judge it", {"draft": "hi"}, model="anthropic/claude-opus-5-5", need="verdict"))
    body = claude["seen"][0]
    assert v == {"verdict": "pass"} and "cache_control" not in body  # 资料每次都不一样：不缓存尾巴
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"}  # 开头的规矩照样缓存
