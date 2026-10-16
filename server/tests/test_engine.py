"""内核（engine/）：一轮的循环、三种回合的规矩、插话 / 停、会话树、事件表、外人回合的一次性 JSON、接到 llmjson / 名片 Agent / claw 上。
模型一律是假的（FakeModel 按剧本回放），不花钱、不连网。"""
from __future__ import annotations

import asyncio

import pytest

import engine
from engine import events, loop, models, oneshot, providers
from engine.errors import EngineError
from engine.session import DbSession, MemorySession, system_note, text_of, user


class FakeModel:
    """按顺序回放：每次调模型拿剧本的下一条。一条 = {"text" | "chunks", "calls": [(id, name, args)], "stop", "delay", "thinking"}，
    或者一个异常（这次调用抛它）。requests 记下每次收到的 (模型, Request)。"""

    def __init__(self, script: list) -> None:
        self.script = list(script)
        self.requests: list = []

    async def stream(self, ref, req):
        self.requests.append((ref, req))
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        if step.get("thinking"):
            yield {"type": "thinking", "text": step["thinking"]}
        for chunk in step.get("chunks") or ([step["text"]] if step.get("text") else []):
            if step.get("delay"):
                await asyncio.sleep(step["delay"])
            yield {"type": "text", "text": chunk}
        content = [{"type": "text", "text": "".join(step.get("chunks") or [step.get("text") or ""])}] if (step.get("chunks") or step.get("text")) else []
        for cid, name, args in step.get("calls") or []:
            yield {"type": "tool_start", "id": cid, "name": name}
            content.append({"type": "tool_call", "id": cid, "name": name, "args": args})
        stop = step.get("stop") or ("tool" if step.get("calls") else "end")
        provider, _, model = ref.partition("/")
        yield {"type": "done", "message": {"role": "assistant", "content": content, "api": "openai", "provider": provider, "model": model,
                                           "stop": stop, "usage": {"in": 10, "out": 5, "cache_read": 0, "cache_write": 0}, "raw": {"content": ""}}}


@pytest.fixture
def fake(monkeypatch):
    def make(script):
        f = FakeModel(script)
        monkeypatch.setattr(providers, "stream", f.stream)
        return f
    return make


def run(coro):
    return asyncio.run(coro)


def echo_tool(log: list, kinds=("owner",), delay: float = 0.0) -> engine.Tool:
    async def go(args, ctx):
        if delay:
            await asyncio.sleep(delay)
        log.append((args, ctx.kind))
        return {"echo": args.get("x")}
    return engine.Tool(name="echo", description="Echo x back.", schema={"type": "object", "required": ["x"], "properties": {"x": {"type": "string"}}},
                       run=go, kinds=kinds, title=lambda a: f"echo {a.get('x')}")


# —— 一轮的循环 ————————————————————————————————————————————————————————————

def test_plain_turn_records_turn_and_steps(fake):
    fake([{"chunks": ["你", "好"]}])
    seen: list = []
    res = run(engine.run_turn(engine.Spec(kind="owner", model="deepseek/deepseek-flash", conversation="t-plain"), MemorySession(), user("hi"),
                              on_event=seen.append))
    assert res.status == "done" and res.text == "你好"
    assert [e["text"] for e in seen if e["type"] == "text"] == ["你", "好"]
    assert [m["role"] for m in res.messages] == ["user", "assistant"]
    t = events.turns("t-plain")[0]
    assert t["status"] == "done" and t["steps"] == 1 and t["usage"]["in"] == 10 and t["usage"]["cost"] is None  # 价目里没有 DeepSeek
    assert [s["type"] for s in events.steps(t["id"])] == ["model"]


def test_tool_round_trip_and_kinds(fake):
    log: list = []
    f = fake([{"text": "let me", "calls": [("c1", "echo", {"x": "a"})]}, {"text": "done"}])
    res = run(engine.run_turn(engine.Spec(kind="owner", model="deepseek/x", tools=[echo_tool(log)], conversation="t-tool"), MemorySession(), user("go")))
    assert res.status == "done" and res.text == "done"
    assert log == [({"x": "a"}, "owner")]
    roles = [m["role"] for m in res.messages]
    assert roles == ["user", "assistant", "tool", "assistant"]
    assert '"echo": "a"' in text_of(res.messages[2])
    assert [t["name"] for t in f.requests[0][1].tools] == ["echo"]
    steps = events.steps(res.turn)
    assert [(s["type"], s["status"]) for s in steps] == [("model", "ok"), ("tool", "ok"), ("model", "ok")]
    assert steps[1]["detail"] == "echo a"


def test_tool_not_offered_to_other_kinds(fake):
    f = fake([{"text": "hello"}])
    run(engine.run_turn(engine.Spec(kind="cyber", model="deepseek/x", tools=[echo_tool([])]), MemorySession(), user("hi")))
    assert f.requests[0][1].tools == []  # echo 只给主人回合：赛博回合里模型根本看不到


def test_outsider_turn_refuses_tools(fake):
    fake([])
    with pytest.raises(EngineError) as e:
        run(engine.run_turn(engine.Spec(kind="outsider", model="deepseek/x", tools=[echo_tool([], kinds=("outsider",))]), MemorySession(), user("x")))
    assert e.value.kind == "rule"


def test_unknown_tool_and_bad_args_get_error_results(fake):
    log: list = []
    fake([{"calls": [("c1", "nope", {}), ("c2", "echo", {"y": 1}), ("c3", "echo", {"x": 5})]}, {"text": "ok"}])
    res = run(engine.run_turn(engine.Spec(kind="owner", model="deepseek/x", tools=[echo_tool(log)]), MemorySession(), user("go")))
    tools = [m for m in res.messages if m["role"] == "tool"]
    assert [t["error"] for t in tools] == [True, True, True]
    assert "Unknown tool" in text_of(tools[0]) and "missing 'x'" in text_of(tools[1]) and "'x' should be string" in text_of(tools[2])
    assert log == []


def test_gate_can_refuse(fake):
    log: list = []

    async def gate(tool, args, ctx):
        return "Needs the owner's OK first; it went to the inbox." if not tool.reversible else None
    t = echo_tool(log)
    t.reversible = False
    fake([{"calls": [("c1", "echo", {"x": "a"})]}, {"text": "asked"}])
    res = run(engine.run_turn(engine.Spec(kind="owner", model="deepseek/x", tools=[t], gate=gate), MemorySession(), user("go")))
    assert log == [] and "inbox" in text_of(res.messages[2]) and res.messages[2]["error"] is True
    assert [s["status"] for s in events.steps(res.turn)][1] == "denied"


def test_steer_waits_for_the_batch(fake):
    log: list = []
    control = engine.Control()

    async def first(args, ctx):
        if args["x"] == "1":
            assert ctx.control.steer(user("wait, one more thing"))  # 用户在第一个工具跑着的时候插话
        log.append(args["x"])
        return "ok"
    t = engine.Tool(name="echo", description="", schema={"type": "object", "properties": {"x": {"type": "string"}}}, run=first)
    f = fake([{"calls": [("c1", "echo", {"x": "1"}), ("c2", "echo", {"x": "2"})]}, {"text": "ok, noted"}])
    res = run(engine.run_turn(engine.Spec(kind="owner", model="deepseek/x", tools=[t]), MemorySession(), user("go"), control=control))
    assert log == ["1", "2"]  # 这一批照样做完（pi 0.58.4 起的做法），不半路丢下
    msgs = f.requests[1][1].messages
    assert [m["role"] for m in msgs[-3:]] == ["tool", "tool", "user"] and text_of(msgs[-1]) == "wait, one more thing"
    assert res.text == "ok, noted" and "steer" in [s["type"] for s in events.steps(res.turn)]
    assert not control.steer(user("too late")) and res.pending == []  # 这一轮结束后再插话：没送到


def test_follow_up_continues_after_reply(fake):
    control = engine.Control()
    control.follow_up(user("and one more thing"))
    fake([{"text": "first"}, {"text": "second"}])
    res = run(engine.run_turn(engine.Spec(kind="owner", model="deepseek/x"), MemorySession(), user("hi"), control=control))
    assert res.text == "second" and [m["role"] for m in res.messages] == ["user", "assistant", "user", "assistant"]


def test_abort_mid_stream_keeps_partial_text(fake):
    control = engine.Control()
    fake([{"chunks": ["a", "b", "c", "d"], "delay": 0.05}])

    async def go():
        task = asyncio.create_task(engine.run_turn(engine.Spec(kind="owner", model="deepseek/x"), MemorySession(), user("hi"), control=control))
        await asyncio.sleep(0.12)
        control.abort()
        return await task
    res = run(go())
    assert res.status == "aborted" and 0 < len(res.text) < 4
    assert res.messages[-1]["stop"] == "aborted"


def test_abort_mid_tool(fake):
    control = engine.Control()
    fake([{"calls": [("c1", "echo", {"x": "a"}), ("c2", "echo", {"x": "b"})]}])

    async def go():
        task = asyncio.create_task(engine.run_turn(engine.Spec(kind="owner", model="deepseek/x", tools=[echo_tool([], delay=5)]), MemorySession(),
                                                   user("go"), control=control))
        await asyncio.sleep(0.1)
        control.abort()
        return await task
    res = run(go())
    tools = [m for m in res.messages if m["role"] == "tool"]
    assert res.status == "aborted" and len(tools) == 2  # 两个调用都有结果，会话保持完整
    assert "Stopped" in text_of(tools[0]) and "Not run" in text_of(tools[1])


def test_outer_cancel_is_recorded_and_raised(fake):
    fake([{"chunks": ["a", "b", "c"], "delay": 0.2}])

    async def go():
        task = asyncio.create_task(engine.run_turn(engine.Spec(kind="owner", model="deepseek/x", conversation="t-cancel"), MemorySession(), user("hi")))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    run(go())
    assert events.turns("t-cancel")[0]["status"] == "aborted"


def test_max_steps_and_cut_off_tool_call(fake):
    fake([{"calls": [("c1", "echo", {"x": "a"})]}, {"calls": [("c2", "echo", {"x": "b"})]}])
    res = run(engine.run_turn(engine.Spec(kind="owner", model="deepseek/x", tools=[echo_tool([])], max_steps=2), MemorySession(), user("go")))
    assert res.status == "max_steps" and res.messages[-1]["role"] == "assistant" and "Stopped after 2 steps" in text_of(res.messages[-1])
    fake([{"calls": [("c1", "echo", {"x": "a"})], "stop": "length"}])
    res = run(engine.run_turn(engine.Spec(kind="owner", model="deepseek/x", tools=[echo_tool([])]), MemorySession(), user("go")))
    assert res.status == "length" and res.messages[-1]["role"] == "tool" and res.messages[-1]["error"]


def test_provider_error_becomes_error_status(fake, monkeypatch):
    monkeypatch.setattr(loop, "MODEL_RETRIES", (0, 0))
    f = fake([EngineError("deepseek HTTP 500: boom", retryable=True)] * 3)
    res = run(engine.run_turn(engine.Spec(kind="owner", model="deepseek/x", conversation="t-err"), MemorySession(), user("hi")))
    assert res.status == "error" and "HTTP 500" in res.error and res.retryable and len(f.requests) == 3  # 还没出字：再试两次
    assert events.turns("t-err")[0]["status"] == "error"
    fake([EngineError("deepseek HTTP 401: bad key", kind="config")])
    res = run(engine.run_turn(engine.Spec(kind="owner", model="deepseek/x"), MemorySession(), user("hi")))
    assert res.status == "error" and res.error_kind == "config"  # 不能重试的不重试
    fake([EngineError("deepseek HTTP 503", retryable=True), {"text": "back"}])
    res = run(engine.run_turn(engine.Spec(kind="owner", model="deepseek/x"), MemorySession(), user("hi")))
    assert res.status == "done" and res.text == "back"


# —— 会话树 ——————————————————————————————————————————————————————————————

def test_db_session_tree_and_rewind(fake):
    s = DbSession.create("test:tree", kind="cyber", system="be brief")
    a = s.append(user("one"))
    s.append({"role": "assistant", "content": [{"type": "text", "text": "1"}]})
    c = s.append(user("two"))
    s.append({"role": "assistant", "content": [{"type": "text", "text": "2"}]})
    assert [text_of(m) for m in s.messages()] == ["one", "1", "two", "2"]
    s.rewind(c)  # 撤回第二句：叶子回到「1」那条，旧的枝还在
    assert [text_of(m) for m in DbSession.get(s.id).messages()] == ["one", "1"]
    s.append(user("two again"))
    assert [text_of(m) for m in s.messages()] == ["one", "1", "two again"]
    tree = s.tree()
    assert len(tree) == 5 and sum(1 for e in tree if e["on_path"]) == 3
    assert DbSession.open("test:tree", kind="cyber", system="other").system == "be brief"  # 已有的会话不改开头
    s.rewind(a)
    assert s.messages() == []


def test_turn_appends_into_db_session(fake):
    fake([{"text": "hi there"}])
    s = DbSession.create("test:turn", kind="owner")
    run(engine.run_turn(engine.Spec(kind="owner", model="deepseek/x"), s, [user("hello"), system_note("today is a test")]))
    assert [m["role"] for m in DbSession.get(s.id).messages()] == ["user", "system", "assistant"]


# —— 外人回合的一次性 JSON ——————————————————————————————————————————————————

def test_ask_json_retries_bad_json_once(fake):
    f = fake([{"text": "Sure! here it is"}, {"text": '```json\n{"verdict": "pass", "reasons": []}\n```'}])
    v, via = run(engine.ask_json("judge it", {"draft": "hi"}, model="moonshot/kimi-k3", need="verdict", purpose="doorman"))
    assert v["verdict"] == "pass" and via == "builtin:moonshot/kimi-k3"
    req = f.requests[0][1]
    assert req.tools == [] and req.json is True and "INPUT_JSON" in text_of(req.messages[0]) and "ONE JSON value" in req.system
    assert events.turns("doorman")[0]["kind"] == "outsider"


def test_ask_json_gives_up_and_maps_errors(fake):
    fake([{"text": "nope"}, {"text": "still nope"}])
    with pytest.raises(EngineError) as e:
        run(engine.ask_json("x", {}, model="deepseek/x"))
    assert e.value.kind == "output"
    fake([{"text": "", "stop": "refusal"}])
    with pytest.raises(EngineError) as e:
        run(engine.ask_json("x", {}, model="deepseek/x"))
    assert e.value.kind == "refusal"
    assert oneshot.parse('noise {"a": [1, 2]} trailing') == {"a": [1, 2]}


# —— 配置和接线 ——————————————————————————————————————————————————————————————

def test_routes_and_providers(server_json, monkeypatch):
    server_json({"engine": {"model": "deepseek/deepseek-flash", "use": {"card": True, "doorman": "moonshot/kimi-k3", "json": False},
                            "providers": {"myollama": {"api": "openai", "url": "http://127.0.0.1:11434/v1/", "key_env": ""}}}})
    assert models.route("card") == "deepseek/deepseek-flash"
    assert models.route("card", "anthropic/claude-opus-5-5") == "anthropic/claude-opus-5-5"
    assert models.route("doorman") == "moonshot/kimi-k3"
    assert models.route("json") is None and models.route("cyber") is None
    assert models.route("card", "not a ref") == "deepseek/deepseek-flash"
    p = models.provider("myollama")
    assert p["url"] == "http://127.0.0.1:11434/v1" and p["key"] == "" and models.has_key("myollama/qwen3")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    assert not models.has_key("deepseek/deepseek-flash")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    assert models.has_key("deepseek/deepseek-flash")
    with pytest.raises(EngineError):
        models.provider("nobody")
    with pytest.raises(EngineError):
        models.parse_ref("claude")
    assert models.cost("anthropic/claude-opus-5-5", {"in": 1_000_000, "out": 100_000}) == 6.0
    caps = models.anthropic_caps("claude-opus-5-5")
    assert caps["adaptive"] and caps["mid_system"] and caps["fallbacks"]
    assert not models.anthropic_caps("claude-sonnet-5")["mid_system"]
    assert not models.anthropic_caps("claude-haiku-4-5-20251001")["adaptive"] and models.anthropic_caps("claude-opus-6")["adaptive"]


def test_llmjson_uses_engine_then_falls_back(server_json, fake, monkeypatch):
    import llmjson
    server_json({"engine": {"model": "deepseek/deepseek-flash", "use": {"json": True}}})
    fake([{"text": '{"todos": ["a"]}'}])
    v, via = run(llmjson.ask("rules", {"x": 1}, {"type": "object", "required": ["todos"]}))
    assert v == {"todos": ["a"]} and via == "builtin:deepseek/deepseek-flash"

    async def old_path(*a, **k):
        return {"todos": []}, "llm-task"
    monkeypatch.setattr(llmjson, "via_llm_task", old_path)
    fake([EngineError("deepseek HTTP 500"), ])
    v, via = run(llmjson.ask("rules", {"x": 1}, {"type": "object", "required": ["todos"]}))
    assert via == "llm-task"  # 内核出错：照旧走原来的路
    server_json({"engine": {"model": "deepseek/deepseek-flash", "use": {"json": True}, "fallback": False}})
    fake([EngineError("deepseek HTTP 500")])
    with pytest.raises(llmjson.LLMError):
        run(llmjson.ask("rules", {"x": 1}))


def test_card_agent_and_doorman_backends(server_json):
    import cardagent
    import sentinel
    server_json({})
    assert cardagent.backend() == "llm-task" and sentinel.backend() == "llm-task"
    server_json({"engine": {"model": "deepseek/deepseek-flash", "use": {"card": True}}})
    assert cardagent.backend() == "builtin" and cardagent.available()
    assert sentinel.backend() == "llm-task"  # Doorman 没配内核：照旧
    server_json({"engine": {"model": "deepseek/deepseek-flash", "use": {"card": True, "doorman": True}}, "card": {"sentinel": False}})
    assert sentinel.backend() == "off"  # 关掉的 Doorman 不会因为配了内核又开起来
    server_json({"engine": {"model": "deepseek/deepseek-flash", "use": {"doorman": True}}})
    assert sentinel.backend() == "builtin" and cardagent.backend() == "llm-task"


def test_card_agent_think_via_engine(server_json, fake):
    import cardagent
    server_json({"engine": {"model": "deepseek/deepseek-flash", "use": {"card": True}}, "card": {"sentinel": False}})
    fake([{"text": '{"reply": "Leo is free on Friday evening.", "used": ["cal"], "ask_owner": null, "declined": []}'}])
    out, via = run(cardagent.think({"name": "Ann", "tier": "friend"}, [{"id": "cal", "label": "calendar", "text": "Fri evening free"}], [],
                                   "Is Leo free Friday?", "en"))
    assert via == "builtin:deepseek/deepseek-flash" and out["reply"].startswith("Leo is free")


def test_claw_builtin_skeleton(server_json, fake, monkeypatch):
    import claw
    server_json({"claw": {"kind": "builtin"}, "engine": {"model": "deepseek/deepseek-flash", "models": ["deepseek/deepseek-flash", "moonshot/kimi-k3"]}})
    assert claw.is_builtin() and not claw.is_openclaw()
    assert claw.model() == "deepseek/deepseek-flash" and claw.models() == ["deepseek/deepseek-flash", "moonshot/kimi-k3"]
    assert claw.session_mode()[0] == "history" and claw.caps()["modelSwitch"] and not claw.caps()["steer"]
    f = fake([{"chunks": ["Hel", "lo"]}])
    parts: list = []
    run(claw.stream([{"role": "user", "content": "earlier"}, {"role": "assistant", "content": "ok"}, {"role": "user", "content": "hi"}],
                    "mousse:main", "moonshot/kimi-k3", parts.append))
    assert "".join(parts) == "Hello"
    ref, req = f.requests[0]
    assert ref == "moonshot/kimi-k3" and [m["role"] for m in req.messages] == ["user", "assistant", "user"]
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    ok, why = run(claw.probe())
    assert not ok and "no API key" in why
    fake([EngineError("deepseek HTTP 401: bad key", kind="config")])
    with pytest.raises(claw.ClawError):
        run(claw.stream([{"role": "user", "content": "hi"}], "mousse:main", None, parts.append))


def test_cyber_turn_uses_its_own_session(server_json, fake):
    from engine import cyber
    server_json({"engine": {"model": "deepseek/deepseek-flash"}})
    f = fake([{"text": "Hi, I'm Iris."}, {"text": "Still Iris."}, {"text": "Now terse."}])
    iris = {"id": "iris", "name": "Iris", "identity": "A calm podcast host.", "model": "moonshot/kimi-k3"}
    res = run(cyber.chat(iris, "who are you?"))
    assert res.status == "done" and res.text == "Hi, I'm Iris."
    ref, req = f.requests[0]
    assert ref == "moonshot/kimi-k3" and "You are Iris" in req.system and "A calm podcast host." in req.system and req.tools == []
    run(cyber.chat(iris, "again?"))
    assert [m["role"] for m in f.requests[1][1].messages] == ["user", "assistant", "user"]  # 接在今天的会话后面
    run(cyber.chat({**iris, "identity": "Terse."}, "and now?"))  # 当天改了性格：开头不动，往后补一条 system
    msgs = f.requests[2][1].messages
    assert f.requests[2][1].system == req.system and msgs[-1]["role"] == "system" and "Terse." in text_of(msgs[-1])
    assert events.turns("cyber:iris")[0]["kind"] == "cyber"
    with pytest.raises(EngineError):
        cyber.cyber_id({"id": "../.."[:0]})
