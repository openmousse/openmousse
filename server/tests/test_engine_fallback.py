"""内核第 13 步 A：备用链（engine/fallback.py）。什么错换、什么错不换；链从哪来；出了字不换；冷却和一家多把密钥；上下文太长换窗口更大的；
同一个会话 Claude → OpenAI 兼容 → 再回 Claude；外人回合的一次性 JSON 走完链才退回原来的路。模型全是假的：脚本回放，或者
evals/fakemodel.py 的假模型服务器（真的走 HTTP），不连网、不花钱。"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

import engine
from engine import events, fallback, loop, models, oneshot, provider_openai, providers
from engine.errors import EngineError
from engine.session import MemorySession, text_of, user
from evals.fakemodel import Fail, FakeModelServer, Hang, Reply


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    """冷却记在进程里：每个测试前后清掉。备用的模型要有密钥才会换过去（没密钥的不换），这里每家给一把假的。"""
    fallback.reset()
    monkeypatch.setattr(loop, "MODEL_RETRIES", (0, 0))
    monkeypatch.setattr(provider_openai, "RETRY_DELAYS", (0, 0))
    for name in ("DEEPSEEK_API_KEY", "MOONSHOT_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.setenv(name, "test-" + name.lower())
    yield
    fallback.reset()


class Scripted:
    """按模型回放：script[模型引用] 是一个列表，每次调那个模型拿下一条：EngineError（直接抛）、("partial", 字, EngineError)
    （先吐字再抛）、dict（{"text", "calls": [(id, name, args)]}，正常回复）。calls 记下每次调的是哪个模型。"""

    def __init__(self, script: dict) -> None:
        self.script = {k: list(v) for k, v in script.items()}
        self.calls: list[str] = []
        self.requests: list = []
        self.quick: list[bool] = []  # 每次调用时 req.quick 是什么（同一个 Request 会被改，得当场记）

    async def stream(self, ref, req):
        self.calls.append(ref)
        self.requests.append((ref, req))
        self.quick.append(bool(getattr(req, "quick", False)))
        step = self.script[ref].pop(0)
        if isinstance(step, Exception):
            raise step
        if isinstance(step, tuple):
            yield {"type": "text", "text": step[1]}
            raise step[2]
        if step.get("text"):
            yield {"type": "text", "text": step["text"]}
        content = [{"type": "text", "text": step["text"]}] if step.get("text") else []
        for cid, name, args in step.get("calls") or []:
            content.append({"type": "tool_call", "id": cid, "name": name, "args": args})
        provider, _, model = ref.partition("/")
        yield {"type": "done", "message": {"role": "assistant", "content": content, "api": "openai", "provider": provider, "model": model,
                                           "stop": "tool" if step.get("calls") else "end", "usage": {"in": 100, "out": 10, "cache_read": 0,
                                                                                                     "cache_write": 0}}}


@pytest.fixture
def scripted(monkeypatch):
    def make(script):
        f = Scripted(script)
        monkeypatch.setattr(providers, "stream", f.stream)
        return f
    return make


def overloaded() -> EngineError:
    return EngineError("deepseek HTTP 529: overloaded", status=529, retryable=True)


# —— 什么错换 ————————————————————————————————————————————————————————————

def test_reasons():
    r = fallback.reason_of

    def e(msg, **kw):
        return EngineError(msg, **kw)
    assert r(e("x HTTP 429: slow down", status=429, retryable=True)) == "rate_limit"
    assert r(e("x HTTP 429: You exceeded your current quota (insufficient_quota)", status=429, retryable=True)) == "billing"
    assert r(e("x HTTP 402: payment required", status=402)) == "billing"
    assert r(e("x HTTP 401: bad key", status=401, kind="config")) == "auth"
    assert r(e("no API key for deepseek (put DEEPSEEK_API_KEY in the .env)", kind="config")) == "auth"
    assert r(e("x HTTP 403: forbidden", status=403, kind="config")) == "auth"
    assert r(e("x HTTP 404: model gpt-x does not exist", status=404)) == "model_not_found"
    assert r(e("x HTTP 400: The model `kimi-k9` does not exist", status=400)) == "model_not_found"
    assert r(e("Claude HTTP 400: Your credit balance is too low to access the Anthropic API", status=400)) == "billing"
    assert r(e("x HTTP 400: messages.3.content: field required", status=400)) is None  # 我们自己写错了：换谁都一样
    assert r(e("Claude HTTP 400: prompt is too long: 1200000 tokens > 1000000 maximum", status=400)) == "context_overflow"
    assert r(e("x HTTP 413: request too large", status=413)) == "context_overflow"
    assert r(e("x HTTP 504: gateway timeout", status=504, retryable=True)) == "timeout"
    assert r(e("deepseek timed out", kind="timeout", retryable=True)) == "timeout"
    assert r(e("x HTTP 503: unavailable", status=503, retryable=True)) == "overloaded"
    assert r(e("x HTTP 500: boom", status=500, retryable=True)) == "server"
    assert r(e("can't reach moonshot: ConnectError", retryable=True)) == "network"
    assert r(e("moonshot: the stream ended before the model finished", kind="output", retryable=True)) == "network"
    assert r(e("Claude HTTP 200: Overloaded", status=200, retryable=True)) == "overloaded"  # 流到一半来的 overloaded_error
    assert r(e("Claude sent a tool input that isn't JSON: …", kind="output", retryable=True)) is None
    assert r(e("stopped", kind="aborted")) is None and r(e("declined", kind="refusal")) is None
    assert r(e("openrouter HTTP 403: Your input was flagged by moderation", status=403, kind="config")) is None  # 审核拦下的：同拒答
    assert r(e("deepseek HTTP 400: Content Exists Risk", status=400)) is None


def test_chains(server_json):
    server_json({"engine": {"model": ["anthropic/claude-opus-5-5", "moonshot/kimi-k3"], "fallbacks": ["deepseek/deepseek-flash", "nope/x"],
                            "models": ["deepseek/deepseek-flash", ["moonshot/kimi-k3", "anthropic/claude-haiku-5-5"]],
                            "use": {"json": ["moonshot/kimi-k3", "deepseek/deepseek-flash"], "doorman": ["moonshot/kimi-k3"], "card": True}}})
    assert models.default_model() == "anthropic/claude-opus-5-5" and models.models() == ["deepseek/deepseek-flash", "moonshot/kimi-k3"]
    assert models.route("json") == "moonshot/kimi-k3" and models.route("card") == "anthropic/claude-opus-5-5"
    assert models.fallbacks() == ["deepseek/deepseek-flash"]  # 不认识的 provider 去掉

    def chain(model, **kw):
        return fallback.chain(engine.Spec(kind="owner", model=model, **kw))
    assert chain("anthropic/claude-opus-5-5") == ["anthropic/claude-opus-5-5", "moonshot/kimi-k3"]  # engine.model 写成列表：它自己的链
    assert chain("moonshot/kimi-k3") == ["moonshot/kimi-k3", "anthropic/claude-haiku-5-5"]          # engine.models 里的那一项
    assert chain("anthropic/claude-sonnet-5-5") == ["anthropic/claude-sonnet-5-5", "deepseek/deepseek-flash"]  # 默认链
    assert chain("deepseek/deepseek-flash") == ["deepseek/deepseek-flash"]  # 默认链里只有它自己
    assert chain("moonshot/kimi-k3", fallbacks=[]) == ["moonshot/kimi-k3"]  # 调用方说了不换
    assert models.purpose_chain("json", "moonshot/kimi-k3") == ["deepseek/deepseek-flash"]
    assert models.purpose_chain("doorman", "moonshot/kimi-k3") == []  # 只写一个 = 这件事不换
    assert models.purpose_chain("json", "anthropic/claude-opus-5-5") is None  # 调用方换了模型：按模型找
    server_json({"engine": {"model": "claude-code/opus", "models": [["moonshot/kimi-k3", "deepseek/deepseek-flash"]]}})
    assert models.outside() == "moonshot/kimi-k3" and fallback.chain(engine.Spec(kind="owner", model="claude-code/opus")) == ["claude-code/opus"]
    # 默认模型是用户的套餐（第 11 项）：不花套餐的那几件事用 engine.models 里写成列表的那一项的第一个；套餐模型不进链


# —— 一轮里换 ————————————————————————————————————————————————————————————

def test_switch_after_retries_and_record(server_json, scripted):
    server_json({"engine": {"model": "deepseek/a", "fallbacks": ["anthropic/claude-haiku-5-5"]}})
    f = scripted({"deepseek/a": [overloaded(), overloaded(), overloaded()], "anthropic/claude-haiku-5-5": [{"text": "hi from haiku"}]})
    seen: list = []
    res = run(engine.run_turn(engine.Spec(kind="owner", model="deepseek/a", conversation="t-fb-1"), MemorySession(), user("hi"),
                              on_event=seen.append))
    assert res.status == "done" and res.text == "hi from haiku"
    assert f.calls == ["deepseek/a"] * 3 + ["anthropic/claude-haiku-5-5"]  # 重试（MODEL_RETRIES）用完了才换
    assert [e for e in seen if e["type"] == "model_switch"] == [{"type": "model_switch", "from": "deepseek/a", "to": "anthropic/claude-haiku-5-5",
                                                                 "reason": "overloaded"}]
    assert res.model == "anthropic/claude-haiku-5-5" and res.models == ["deepseek/a", "anthropic/claude-haiku-5-5"]
    assert res.usage["cost"] == models.cost("anthropic/claude-haiku-5-5", {"in": 100, "out": 10})  # 按真正回答的模型算钱
    t = events.turns("t-fb-1")[0]
    assert t["model"] == "deepseek/a" and t["models"] == ["deepseek/a", "anthropic/claude-haiku-5-5"]
    steps = events.steps(res.turn)
    assert [(s["type"], s["name"], s["status"]) for s in steps] == [("model", "deepseek/a", "error"), ("model", "anthropic/claude-haiku-5-5", "ok")]
    assert steps[0]["data"]["switch"]["to"] == "anthropic/claude-haiku-5-5" and steps[0]["data"]["attempts"] == 3


def test_no_chain_behaves_as_before(server_json, scripted):
    server_json({"engine": {"model": "deepseek/a"}})
    f = scripted({"deepseek/a": [overloaded(), overloaded(), overloaded()]})
    seen: list = []
    res = run(engine.run_turn(engine.Spec(kind="owner", model="deepseek/a"), MemorySession(), user("hi"), on_event=seen.append))
    assert res.status == "error" and res.retryable and "529" in res.error and "also tried" not in res.error
    assert f.calls == ["deepseek/a"] * 3 and res.model == "deepseek/a" and res.models == ["deepseek/a"]
    assert not any(e["type"] == "model_switch" for e in seen)


def test_what_does_not_switch(server_json, scripted):
    server_json({"engine": {"fallbacks": ["moonshot/b"]}})
    bad_request = EngineError("deepseek HTTP 400: messages.1.content must be a string", status=400)
    f = scripted({"deepseek/a": [bad_request, ("partial", "Here is the first half", overloaded())], "moonshot/b": []})
    res = run(engine.run_turn(engine.Spec(kind="owner", model="deepseek/a"), MemorySession(), user("x")))
    assert res.status == "error" and f.calls == ["deepseek/a"]  # 我们自己写错的 400：不换
    res = run(engine.run_turn(engine.Spec(kind="owner", model="deepseek/a"), MemorySession(), user("x")))
    assert res.status == "error" and f.calls == ["deepseek/a"] * 2  # 已经给用户看到字了：不换（也不重试）
    # 外人回合的字没人看：流到一半断了也换
    f = scripted({"deepseek/a": [("partial", '{"repl', overloaded())], "moonshot/b": [{"text": '{"reply": "ok"}'}]})
    res = run(engine.run_turn(engine.Spec(kind="outsider", model="deepseek/a"), MemorySession(), user("x")))
    assert res.status == "done" and res.text == '{"reply": "ok"}' and f.calls == ["deepseek/a", "moonshot/b"]


def test_user_stop_does_not_switch(server_json, monkeypatch):
    server_json({"engine": {"fallbacks": ["moonshot/b"]}})
    calls: list = []
    control = engine.Control()

    async def stream(ref, req):
        calls.append(ref)
        control.abort()  # 用户点了停（还在等第一个字的时候）
        await asyncio.sleep(5)
        yield {}
    monkeypatch.setattr(providers, "stream", stream)
    res = run(engine.run_turn(engine.Spec(kind="owner", model="deepseek/a"), MemorySession(), user("x"), control=control))
    assert res.status == "aborted" and calls == ["deepseek/a"]


def test_switch_sticks_for_the_rest_of_the_turn(server_json, scripted):
    server_json({"engine": {"fallbacks": ["moonshot/b"]}})
    log: list = []

    async def echo(args, ctx):
        log.append(args["x"])
        return "ok"
    tool = engine.Tool(name="echo", description="", schema={"type": "object", "required": ["x"], "properties": {"x": {"type": "string"}}}, run=echo)
    slow = EngineError("timed out", kind="timeout", retryable=True)
    f = scripted({"deepseek/a": [slow, slow, slow, {"text": "next turn on a"}],
                  "moonshot/b": [{"text": "let me", "calls": [("c1", "echo", {"x": "1"})]}, {"text": "done on b"}]})
    s = MemorySession()
    res = run(engine.run_turn(engine.Spec(kind="owner", model="deepseek/a", tools=[tool]), s, user("go")))
    assert res.text == "done on b" and log == ["1"]
    assert f.calls == ["deepseek/a"] * 3 + ["moonshot/b"] * 2  # 超时也照 MODEL_RETRIES 重试完才换；换过以后这一轮接着用 b
    res = run(engine.run_turn(engine.Spec(kind="owner", model="deepseek/a"), s, user("again")))
    assert res.text == "next turn on a" and res.model == "deepseek/a"  # 下一轮从主模型重新来（超时不冷却）


def test_a_chain_that_cannot_be_used_changes_nothing(server_json, scripted, monkeypatch):
    """链里的模型没有密钥：主模型在冷却也不跳过去，各家那一层照样重试（quick 不开），超时照样重试完。"""
    monkeypatch.delenv("MOONSHOT_API_KEY")
    server_json({"engine": {"fallbacks": ["moonshot/kimi-k3"]}})
    fallback.note("deepseek/deepseek-flash", None, "billing")  # 主模型的密钥在冷却
    slow = EngineError("timed out", kind="timeout", retryable=True)
    f = scripted({"deepseek/deepseek-flash": [slow, {"text": "still the primary"}]})
    res = run(engine.run_turn(engine.Spec(kind="owner", model="deepseek/deepseek-flash"), MemorySession(), user("hi")))
    assert res.text == "still the primary" and f.calls == ["deepseek/deepseek-flash"] * 2 and f.quick == [False, False]
    monkeypatch.setenv("MOONSHOT_API_KEY", "test-key")  # 有密钥了：这才跳过冷却的主模型，各家那一层也不重试
    f = scripted({"moonshot/kimi-k3": [{"text": "on the fallback"}]})
    res = run(engine.run_turn(engine.Spec(kind="owner", model="deepseek/deepseek-flash"), MemorySession(), user("hi")))
    assert res.text == "on the fallback" and f.calls == ["moonshot/kimi-k3"] and f.quick == [False]  # 链里没有别的了：不 quick


def test_moderation_blocks_are_not_switched(server_json, scripted):
    server_json({"engine": {"fallbacks": ["moonshot/kimi-k3"]}})
    f = scripted({"deepseek/deepseek-flash": [EngineError("openrouter HTTP 403: Your input was flagged", status=403, kind="config")]})
    res = run(engine.run_turn(engine.Spec(kind="owner", model="deepseek/deepseek-flash"), MemorySession(), user("hi")))
    assert res.status == "error" and f.calls == ["deepseek/deepseek-flash"] and fallback.status() == []  # 不换、不冷却那把密钥


# —— 冷却、一家多把密钥 ———————————————————————————————————————————————————————

def test_rate_limit_cools_down_then_the_primary_comes_back(server_json, scripted, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k-ds")
    monkeypatch.setenv("MOONSHOT_API_KEY", "k-ms")
    server_json({"engine": {"fallbacks": ["moonshot/kimi-k3"]}})
    limited = EngineError("deepseek HTTP 429: rate limit reached", status=429, retryable=True)
    f = scripted({"deepseek/deepseek-flash": [limited, limited, limited, {"text": "a again"}], "moonshot/kimi-k3": [{"text": "b1"}, {"text": "b2"}]})
    assert run(engine.run_turn(engine.Spec(kind="owner", model="deepseek/deepseek-flash"), MemorySession(), user("1"))).text == "b1"
    cool = fallback.status()
    assert [c["reason"] for c in cool] == ["rate_limit"] and 0 < cool[0]["seconds"] <= 30 and "DEEPSEEK_API_KEY" in cool[0]["what"]
    assert "k-ds" not in json.dumps(cool)  # 不带密钥
    seen: list = []
    res = run(engine.run_turn(engine.Spec(kind="owner", model="deepseek/deepseek-flash"), MemorySession(), user("2"), on_event=seen.append))
    assert res.text == "b2" and f.calls[-1] == "moonshot/kimi-k3" and f.calls.count("deepseek/deepseek-flash") == 3  # 冷却中：直接用下一个
    assert [e["reason"] for e in seen if e["type"] == "model_switch"] == ["cooldown"]
    for c in fallback._cool.values():  # 冷却完了
        c["until"] = 0
    res = run(engine.run_turn(engine.Spec(kind="owner", model="deepseek/deepseek-flash"), MemorySession(), user("3")))
    assert res.text == "a again" and fallback.status() == []  # 主模型回来了；成功一次清掉记录


def test_cooldowns_double_and_billing_covers_the_whole_provider():
    fallback.note("anthropic/claude-opus-5-5", "K#1", "rate_limit")
    fallback.note("anthropic/claude-opus-5-5", "K#1", "rate_limit")
    c = next(iter(fallback._cool.values()))
    assert c["n"] == 2 and 55 < c["until"] - fallback.time.monotonic() <= 60  # 30 秒起、连着再来翻倍
    fallback.reset()
    fallback.note("deepseek/deepseek-flash", None, "billing")
    assert fallback.cooling("deepseek/deepseek-v4-pro") > 500  # 计费错：这一家的所有型号
    fallback.note("moonshot/kimi-k3", None, "model_not_found")
    assert fallback.cooling("moonshot/kimi-k3") > 500 and fallback.cooling("moonshot/kimi-k2") == 0  # 型号不存在：只这个型号


def test_several_keys_rotate_before_switching_models(server_json, monkeypatch):
    monkeypatch.setenv("DS_KEY_1", "key-one")
    monkeypatch.setenv("DS_KEY_2", "key-two")
    monkeypatch.setenv("MOONSHOT_API_KEY", "key-ms")
    server_json({"engine": {"fallbacks": ["moonshot/kimi-k3"], "providers": {"deepseek": {"key_env": ["DS_KEY_1", "DS_KEY_2"]}}}})
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers["authorization"]
        seen.append(auth)
        if auth == "Bearer key-one":
            return httpx.Response(429, json={"error": {"message": "rate limit reached for requests"}})
        body = "".join("data: " + json.dumps(c) + "\n\n" for c in [{"choices": [{"index": 0, "delta": {"content": "from key two"}}]},
                                                                  {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}])
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=(body + "data: [DONE]\n\n").encode())
    monkeypatch.setattr(provider_openai, "TRANSPORT", httpx.MockTransport(handler))
    res = run(engine.run_turn(engine.Spec(kind="owner", model="deepseek/deepseek-flash"), MemorySession(), user("hi")))
    assert res.status == "done" and res.text == "from key two" and res.model == "deepseek/deepseek-flash"  # 先换密钥，没换模型
    assert seen == ["Bearer key-one"] * 3 + ["Bearer key-two"]
    seen.clear()
    res = run(engine.run_turn(engine.Spec(kind="owner", model="deepseek/deepseek-flash"), MemorySession(), user("again")))
    assert res.text == "from key two" and seen == ["Bearer key-two"]  # 第一把还在冷却：直接用第二把
    p = models.provider("deepseek")
    assert [n for n, _ in p["keys"]] == ["DS_KEY_1", "DS_KEY_2"] and p["key"] == "key-one"


# —— 上下文太长 ———————————————————————————————————————————————————————————

def test_context_overflow_moves_to_a_bigger_window_or_compacts(server_json, scripted):
    too_long = EngineError("deepseek HTTP 400: This model's maximum context length is 131072 tokens", status=400)
    server_json({"engine": {"fallbacks": ["moonshot/b", "anthropic/claude-opus-5-5"]}})
    f = scripted({"deepseek/a": [too_long], "moonshot/b": [], "anthropic/claude-opus-5-5": [{"text": "read it all"}]})
    seen: list = []
    res = run(engine.run_turn(engine.Spec(kind="owner", model="deepseek/a"), MemorySession(), user("long"), on_event=seen.append))
    assert res.text == "read it all" and f.calls == ["deepseek/a", "anthropic/claude-opus-5-5"]  # 跳过窗口一样大的 moonshot/b
    assert [e["reason"] for e in seen if e["type"] == "model_switch"] == ["context_overflow"]
    # 链里没有窗口更大的：照旧交给压缩（Spec.recover）
    server_json({"engine": {"fallbacks": ["moonshot/b"]}})
    f = scripted({"deepseek/a": [too_long, {"text": "after compaction"}], "moonshot/b": []})
    recovered: list = []

    async def recover(tid, err):
        recovered.append(str(err))
        return True
    res = run(engine.run_turn(engine.Spec(kind="owner", model="deepseek/a", recover=recover), MemorySession(), user("long")))
    assert res.text == "after compaction" and len(recovered) == 1 and f.calls == ["deepseek/a", "deepseek/a"]


# —— 同一个会话换着家发：Claude → OpenAI 兼容 → 再回 Claude ————————————————————————————————

def test_one_session_claude_then_openai_then_claude(server_json, request):
    pytest.importorskip("anthropic")
    fm = FakeModelServer([
        # 第 1 轮：Claude 想了一下（带签名）、调工具、说完
        Reply(text="Let me look.", thinking="Checking the list.", signature="sig-one", calls=[("echo", {"x": "a"})]), Reply(text="Done one."),
        # 第 2 轮：Claude 过载（重试也是），换到 OpenAI 兼容的 fake/kimi：调工具、说完（这一轮剩下的步接着用它）
        Fail(529, "Overloaded"), Fail(529, "Overloaded"), Fail(529, "Overloaded"),
        Reply(text="Kimi here.", thinking="kimi thinks", calls=[("echo", {"x": "b"})]), Reply(text="Done two."),
        # 第 3 轮：回到 Claude
        Reply(text="Back on Claude."),
    ]).start()
    request.addfinalizer(fm.stop)
    server_json({"engine": {"fallbacks": ["fake/kimi"], "providers": {"fake": {"api": "openai", "url": fm.openai_url, "key_env": "",
                                                                                "reasoning_replay": "all", "reasoning_pad": True},
                                                                       "anthropic": {"url": fm.anthropic_url, "key": "test-key"}}}})

    async def echo(args, ctx):
        return "echoed " + args["x"]
    tool = engine.Tool(name="echo", description="Echo.", schema={"type": "object", "required": ["x"], "properties": {"x": {"type": "string"}}},
                       run=echo)
    s = MemorySession()

    async def turn(text):
        seen: list = []
        res = await engine.run_turn(engine.Spec(kind="owner", model="anthropic/claude-opus-5-5", tools=[tool], system="be brief"), s, user(text),
                                    on_event=seen.append)
        return res, [e for e in seen if e["type"] == "model_switch"]

    async def three():  # 一个事件循环里跑三轮（和服务器里一样；SDK 的连接池绑着事件循环）
        return await turn("one"), await turn("two"), await turn("three")
    (r1, sw1), (r2, sw2), (r3, sw3) = run(three())
    assert (r1.text, r2.text, r3.text) == ("Done one.", "Done two.", "Back on Claude.")
    assert sw1 == [] and sw2 == [{"type": "model_switch", "from": "anthropic/claude-opus-5-5", "to": "fake/kimi", "reason": "overloaded"}] and sw3 == []
    assert r2.model == "fake/kimi" and r3.model == "anthropic/claude-opus-5-5"
    claude = fm.of("anthropic")
    oa = fm.of("openai")
    assert len(claude) == 2 + 3 + 1 and len(oa) == 2
    # 发给 OpenAI 兼容的：Claude 的思考不带，Claude 的工具调用照发（id 合规），没想过的回复补一个空格的 reasoning_content
    first_oa = oa[0]["body"]["messages"]
    claude_turn = next(m for m in first_oa if m["role"] == "assistant" and m.get("tool_calls"))
    assert claude_turn["content"] == "Let me look." and claude_turn["reasoning_content"] == " " and "Checking" not in json.dumps(first_oa)
    assert claude_turn["tool_calls"][0]["id"].startswith("toolu_fake_")
    # 回到 Claude：第 1 轮的思考块和签名原样发回去；第 2 轮 Kimi 的回复变成文字 + tool_use，它的思考不发
    last = claude[-1]["body"]["messages"]
    signed = next(m for m in last if m["role"] == "assistant" and any(b.get("type") == "thinking" for b in m["content"]))
    assert signed["content"][0] == {"type": "thinking", "thinking": "Checking the list.", "signature": "sig-one"}
    kimi_turn = next(m for m in last if m["role"] == "assistant" and any(b.get("text") == "Kimi here." for b in m["content"]))
    assert [b["type"] for b in kimi_turn["content"]] == ["text", "tool_use"] and "kimi thinks" not in json.dumps(last)
    kimi_call = kimi_turn["content"][1]["id"]
    assert all(c.isalnum() or c in "_-" for c in kimi_call)
    results = [b for m in last if m["role"] == "user" for b in m["content"] if isinstance(b, dict) and b.get("type") == "tool_result"]
    assert kimi_call in [b["tool_use_id"] for b in results]  # 调用和结果的 id 对得上


# —— 外人回合的一次性 JSON ——————————————————————————————————————————————————————

def test_ask_json_walks_the_chain_then_gives_up(server_json, request):
    fm = FakeModelServer([Fail(503, "unavailable"), Fail(503, "unavailable"), Fail(503, "unavailable"), Reply(text='{"reply": "from b"}')]).start()
    request.addfinalizer(fm.stop)
    server_json({"engine": {"use": {"json": ["fake/a", "fake/b"]}, "providers": {"fake": {"api": "openai", "url": fm.openai_url, "key_env": ""}}}})
    v, via = run(engine.ask_json("x", {"q": 1}, model=engine.route("json"), purpose="json", need="reply"))
    assert v == {"reply": "from b"} and via == "builtin:fake/b"
    assert [r["model"] for r in fm.requests] == ["a", "a", "a", "b"]  # 链里还有能换的：兼容接口那一层不重试，只剩 MODEL_RETRIES
    fm.default = Fail(503, "unavailable")
    with pytest.raises(EngineError) as e:  # 链走完了：抛错（llmjson 这时才退回原来的路）
        run(oneshot.ask_json("x", {}, model="fake/a", purpose="json"))
    assert "also tried: fake/a" in str(e.value) and e.value.retryable


def test_llmjson_falls_back_only_after_the_chain(server_json, request, monkeypatch):
    import llmjson
    fm = FakeModelServer(default=Fail(503, "unavailable")).start()
    request.addfinalizer(fm.stop)
    server_json({"engine": {"use": {"json": ["fake/a", "fake/b"]}, "providers": {"fake": {"api": "openai", "url": fm.openai_url, "key_env": ""}}}})
    old: list = []

    async def via_complete(prompt, input_, schema, timeout, origin=None):  # 原来的路（think.complete）
        old.append([r["model"] for r in fm.requests])
        return {"from": "old path"}, "complete"
    monkeypatch.setattr(llmjson, "via_complete", via_complete)
    monkeypatch.setattr(llmjson.claw, "is_openclaw", lambda: False)
    v, via = run(llmjson.ask("x", {"a": 1}, {"type": "object"}))
    assert v == {"from": "old path"} and via == "complete"
    assert set(old[0]) == {"a", "b"}  # 先把链走完，才退回原来的路


def test_ask_json_switches_when_a_model_does_not_answer(server_json, request):
    fm = FakeModelServer([Hang(6), Reply(text='{"ok": true}')]).start()
    request.addfinalizer(fm.stop)
    server_json({"engine": {"use": {"json": ["fake/slow", "fake/quick"]}, "providers": {"fake": {"api": "openai", "url": fm.openai_url, "key_env": ""}}}})
    v, via = run(oneshot.ask_json("x", {}, model="fake/slow", purpose="json", timeout=5))
    assert v == {"ok": True} and via == "builtin:fake/quick"  # 不回话：等了一半时间就换，同一个模型不再等一遍


# —— 别的 ————————————————————————————————————————————————————————————————

def test_app_ignores_the_new_event():
    import chat
    r = SimpleNamespace(progress=None, t0=0.0, thought="")
    assert chat.note_engine(r, {"type": "model_switch", "from": "a/x", "to": "b/y", "reason": "overloaded"}) is False and r.progress is None


def test_cyber_model_can_carry_a_chain(server_json):
    from engine import cyber
    server_json({"engine": {"model": "deepseek/deepseek-flash", "use": {"cyber": ["moonshot/kimi-k3", "deepseek/deepseek-flash"]}}})
    c = {"id": "c1", "model": ["anthropic/claude-sonnet-5-5", "moonshot/kimi-k3"]}
    assert cyber.model_of(c) == "anthropic/claude-sonnet-5-5" and cyber.fallbacks_of(c, "anthropic/claude-sonnet-5-5") == ["moonshot/kimi-k3"]
    assert cyber.model_of({"id": "c2"}) == "moonshot/kimi-k3" and cyber.fallbacks_of({"id": "c2"}, "moonshot/kimi-k3") == ["deepseek/deepseek-flash"]


def test_reasoning_pad_for_providers_that_need_it():
    msgs = [user("hi"), {"role": "assistant", "api": "anthropic", "provider": "anthropic", "model": "claude-opus-5-5",
                         "content": [{"type": "thinking", "text": "secret", "sig": "s"}, {"type": "text", "text": "hey"}]}, user("next")]
    ds = provider_openai.to_openai("", msgs, models.provider("deepseek"), "deepseek-flash")
    assert ds[1] == {"role": "assistant", "content": "hey", "reasoning_content": " "}  # DeepSeek 不收没有思考字段的 assistant
    plain = provider_openai.to_openai("", msgs, models.provider("openrouter"), "x/y")
    assert plain[1] == {"role": "assistant", "content": "hey"} and text_of(msgs[1]) == "hey"
    own = {"role": "assistant", "api": "openai", "provider": "deepseek", "model": "deepseek-flash", "raw": {"content": "sure"},
           "content": [{"type": "text", "text": "sure"}]}
    again = provider_openai.to_openai("", [user("hi"), own, user("next")], models.provider("deepseek"), "deepseek-flash")
    assert again[1] == {"role": "assistant", "content": "sure"}  # 同一型号自己的回复照原样发回：不配备用链、不换模型，和以前一样


# —— app 看得到换过的模型（done 的 modelId / fallbackFrom）————————————————————————————————————

BUILTIN = {"claw": {"kind": "builtin"}, "engine": {"model": "deepseek/deepseek-flash", "fallbacks": ["moonshot/kimi-k3"]}}


def chat_turn(thread: str, text: str):
    """在 app 的对话里说一句（builtin），等这一轮回完 → (Run, done 事件)。"""
    import chat

    async def go():
        r = chat.start_run(thread, text, None)
        while not r.done:
            await asyncio.sleep(0.01)
        return r
    r = run(go())
    return r, chat.done_payload(r)


def test_done_names_the_model_that_answered_after_a_switch(server_json, scripted):
    import chat
    server_json(BUILTIN)
    scripted({"deepseek/deepseek-flash": [overloaded() for _ in range(3)], "moonshot/kimi-k3": [{"text": "From the backup."}]})
    r, done = chat_turn("t-fb-done", "hello")
    assert done["modelId"] == "moonshot/kimi-k3" and done["fallbackFrom"] == "deepseek/deepseek-flash" and done["text"] == "From the backup."
    msg = chat.history("t-fb-done", all=1)["messages"][-1]  # 存进库的那一行也一样：重开 app 照样标出来
    assert msg["modelId"] == "moonshot/kimi-k3" and msg["fallbackFrom"] == "deepseek/deepseek-flash"


def test_done_is_unchanged_without_a_switch(server_json, scripted):
    server_json(BUILTIN)
    scripted({"deepseek/deepseek-flash": [{"text": "Hi."}]})
    _, done = chat_turn("t-fb-same", "hello")
    assert done["modelId"] == "deepseek/deepseek-flash" and done["fallbackFrom"] is None
    scripted({"deepseek/deepseek-flash": [overloaded() for _ in range(3)],
              "moonshot/kimi-k3": [EngineError("moonshot HTTP 503", status=503, retryable=True) for _ in range(3)]})
    _, done = chat_turn("t-fb-fail", "hello")  # 整条链都不行：没有回答的模型，不标换过
    assert done["status"] == "error" and done["modelId"] == "deepseek/deepseek-flash" and done["fallbackFrom"] is None


def test_unattended_replies_record_the_backup_model(server_json, scripted):
    """定时任务、心跳发进对话的那条（engine/unattended.py）也记上真正回答的模型。"""
    import chat
    from engine import unattended
    server_json(BUILTIN)
    scripted({"deepseek/deepseek-flash": [overloaded() for _ in range(3)], "moonshot/kimi-k3": [{"text": "Morning summary."}]})
    out = run(unattended.fresh("main", "main", "cron:t-fb", "summarise my morning", model="deepseek/deepseek-flash", light=True))
    assert out.status == "done" and out.model == "deepseek/deepseek-flash" and out.served == "moonshot/kimi-k3"
    rid = run(unattended.post("t-fb-cron", "Morning", "gw", out.text, model=out.model, level="none", tag="cron:t-fb", note="n",
                              feed_mark=0, inbox_mark=0, served=out.served))
    with chat._lock, chat.db() as conn:
        row = conn.execute("SELECT model, requested FROM messages WHERE id=?", (rid,)).fetchone()
    assert (row["model"], row["requested"]) == ("moonshot/kimi-k3", "deepseek/deepseek-flash")



def test_switch_is_pushed_live_and_on_reattach(server_json, scripted, monkeypatch):
    """10/20：换的当场推 SSE model_switch（app 在回复下面写一行），重连时补发；done 带 fallbackReason（只在这次推送里）。"""
    import bridges
    import chat
    server_json(BUILTIN)
    hold = asyncio.Event()

    async def fake_run(name, args, stdin, agent, *, kill_on_cancel=False):  # 命令行工具停在这里：换完模型以后有时间重连
        await hold.wait()
        return "nothing today"
    monkeypatch.setattr(bridges, "run", fake_run)
    scripted({"deepseek/deepseek-flash": [overloaded() for _ in range(3)],
              "moonshot/kimi-k3": [{"calls": [("c1", "schedule", {"args": ["day"]})]}, {"text": "From the backup."}]})

    async def go():
        r = chat.start_run("t-fb-live", "hello", None)
        first = asyncio.create_task(_collect(chat.attach(r)))
        while not r.switches:
            await asyncio.sleep(0.01)
        again = await _collect(chat.attach(r), until="model_switch")  # 断线重连：补发换模型的那一条
        hold.set()
        return await first, again
    first, again = run(go())
    sw = {"from": "deepseek/deepseek-flash", "to": "moonshot/kimi-k3", "reason": "overloaded"}
    assert dict(first)["model_switch"] == sw and again[-1] == ("model_switch", sw)
    kinds = [e for e, _ in first]
    assert kinds.index("model_switch") < kinds.index("delta")  # 换的当场就推了，回复还没出字
    done = dict(first)["done"]
    assert done["fallbackFrom"] == "deepseek/deepseek-flash" and done["fallbackReason"] == "overloaded"
    assert "fallbackReason" not in chat.history("t-fb-live", all=1)["messages"][-1]  # 不进库


def test_a_key_rotation_is_not_a_model_switch(server_json, monkeypatch):
    """同一个型号换一把密钥再来（engine/fallback.py 的 from = to）：不推 model_switch，done 也没有 fallbackFrom / fallbackReason。"""
    import chat
    monkeypatch.setenv("DS_KEY_1", "key-one")
    monkeypatch.setenv("DS_KEY_2", "key-two")
    server_json({**BUILTIN, "engine": {**BUILTIN["engine"], "providers": {"deepseek": {"key_env": ["DS_KEY_1", "DS_KEY_2"]}}}})

    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers["authorization"] == "Bearer key-one":
            return httpx.Response(429, json={"error": {"message": "rate limit reached for requests"}})
        body = "".join("data: " + json.dumps(c) + "\n\n" for c in [{"choices": [{"index": 0, "delta": {"content": "from key two"}}]},
                                                                  {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}])
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=(body + "data: [DONE]\n\n").encode())
    monkeypatch.setattr(provider_openai, "TRANSPORT", httpx.MockTransport(handler))

    async def go():
        r = chat.start_run("t-fb-key", "hello", None)
        return r, await _collect(chat.attach(r))
    r, evs = run(go())
    done = dict(evs)["done"]
    assert done["text"] == "from key two" and "model_switch" not in dict(evs) and r.switches == []
    assert done["fallbackFrom"] is None and "fallbackReason" not in done


async def _collect(gen, until: str = "done", timeout: float = 10.0) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []

    async def go() -> None:
        async for chunk in gen:
            for block in chunk.decode().split("\n\n"):
                if not block.strip():
                    continue
                ev, data = "message", {}
                for line in block.split("\n"):
                    if line.startswith("event:"):
                        ev = line[6:].strip()
                    elif line.startswith("data:"):
                        data = json.loads(line[5:].strip())
                out.append((ev, data))
                if ev == until:
                    return
    await asyncio.wait_for(go(), timeout)
    return out
