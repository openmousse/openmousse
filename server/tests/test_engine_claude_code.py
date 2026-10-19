"""内核第 11 项：主人回合交给本机用户自己登录的 Claude Code 跑（engine/claude_brain.py），内核的工具经 MCP 给外援（engine/mcp_engine.py），
谁能花用户的套餐（models.subscription / may_spend / outside），第 5 项外援的令牌、子会话的压缩。

不碰真的 Claude Code、不连真的模型：claude 是 tests/fake_claude.py（按场景吐 stream-json，拿临时配置里的令牌真的回调我们的 /mcp），
/mcp 在这一轮测试的事件循环里用 uvicorn 起在 127.0.0.1 的随机端口；非套餐的模型是 FakeModel；命令行工具换成假的跑法。"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import socket
import stat
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

import bridges
import engine
from engine import claude_brain, events, mcp_engine, models, runners, tasks
from engine import tools as tools_mod
from engine.errors import EngineError
from engine.session import DbSession, MemorySession, day_key, text_of, user
from test_engine import FakeModel

pytest.importorskip("mcp")
pytest.importorskip("uvicorn")

FAKE = Path(__file__).resolve().parent / "fake_claude.py"
STAMP = re.compile(r"\[(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) \d{4}-\d\d-\d\d \d\d:\d\d [^\]]+\] ")  # 用户那句话前面的当地时间（session.with_time）
BUILTIN = {"claw": {"kind": "builtin"}, "engine": {"model": "deepseek/deepseek-flash"}}


def run(coro):
    return asyncio.run(coro)


async def until(cond, timeout: float = 8.0) -> None:
    t0 = time.monotonic()
    while not cond():
        if time.monotonic() - t0 > timeout:
            raise AssertionError("timed out waiting")
        await asyncio.sleep(0.02)


async def collect(gen, until_event: str = "done", timeout: float = 20.0) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []

    async def go() -> None:
        async for chunk in gen:
            for block in (chunk.decode() if isinstance(chunk, bytes) else chunk).split("\n\n"):
                if not block.strip():
                    continue
                ev, data = "message", {}
                for ln in block.split("\n"):
                    if ln.startswith("event:"):
                        ev = ln[6:].strip()
                    elif ln.startswith("data:"):
                        data = json.loads(ln[5:].strip())
                out.append((ev, data))
                if ev == until_event:
                    return
    await asyncio.wait_for(go(), timeout)
    return out


@pytest.fixture
def cc(tmp_path, monkeypatch, server_json):
    """假的 claude（登录了、套餐）+ 本机的 /mcp（uvicorn，随机端口）+ 假的命令行工具 + 非套餐的假模型。"""
    import mcp_bridge  # noqa: F401 — Gate 认临时令牌要它
    server_json(BUILTIN)
    exe = tmp_path / "claude"
    exe.write_text(f"#!{sys.executable}\n" + FAKE.read_text(encoding="utf8"), encoding="utf8")
    exe.chmod(0o755)
    st = SimpleNamespace(exe=exe, log=tmp_path / "claude.log", state=tmp_path / "state", scenario=tmp_path / "scenario.json", calls=[],
                         replies={}, delays={}, model=None)
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(st.log))
    monkeypatch.setenv("FAKE_CLAUDE_STATE", str(st.state))
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", str(st.scenario))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake-should-not-pass")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://proxy.invalid")
    monkeypatch.setenv("CLAUDE_CODE_MESSAGING_SOCKET", "/tmp/parent-session.sock")  # 服务是在一个 Claude Code 会话里起的
    monkeypatch.setenv("CLAUDE_CODE_HOST_SESSION_ID", "parent-session")
    monkeypatch.setattr(runners, "_found", {"claude-code": {"bin": str(exe), "auth": "claude.ai", "billing": "subscription"}})
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(16)
    st.sock = sock
    st.url = f"http://127.0.0.1:{sock.getsockname()[1]}/mcp"
    monkeypatch.setattr(runners, "mcp_url", lambda: st.url)
    mcp_engine._grants.clear()

    async def fake_run(name, args, stdin, agent, *, kill_on_cancel=False):
        st.calls.append((name, list(args), agent))
        if st.delays.get(name):
            await asyncio.sleep(st.delays[name])
        return st.replies.get(name, f"ok: {name} {' '.join(args[:1])}")
    monkeypatch.setattr(bridges, "run", fake_run)

    def scenario(runs: list) -> None:
        st.scenario.write_text(json.dumps(runs), encoding="utf8")

    def records(kind: str | None = None) -> list[dict]:
        if not st.log.exists():
            return []
        rows = [json.loads(x) for x in st.log.read_text(encoding="utf8").splitlines() if x.strip()]
        return [r for r in rows if kind is None or r["kind"] == kind]

    def fake_model(script: list) -> FakeModel:
        from engine import providers
        f = FakeModel(script)
        monkeypatch.setattr(providers, "stream", f.stream)
        st.model = f
        return f

    st.scenario_ = scenario
    st.records = records
    st.fake_model = fake_model
    scenario([[{"say": "ok"}]])
    yield st
    sock.close()
    mcp_engine._grants.clear()


@contextlib.asynccontextmanager
async def serving(st):
    """在这一轮的事件循环里起 /mcp（mcp_bridge.Gate）。"""
    import uvicorn

    import mcp_bridge
    server = uvicorn.Server(uvicorn.Config(mcp_bridge.gate, log_level="warning", lifespan="off", access_log=False))
    task = asyncio.create_task(server.serve(sockets=[st.sock]))
    await until(lambda: server.started, 5)
    try:
        yield
    finally:
        server.should_exit = True
        await task


async def finish(chat, thread: str, timeout: float = 20) -> object:
    await until(lambda: chat.RUNS.get(thread) is not None and chat.RUNS[thread].done, timeout)
    return chat.RUNS[thread]


def steps_of(evs: list[tuple[str, dict]]) -> list[dict]:
    progress = [d for e, d in evs if e == "progress"]
    return progress[-1]["steps"] if progress else []


async def post(url: str, token: str, method: str, params: dict) -> tuple[int, dict]:
    import httpx
    async with httpx.AsyncClient(trust_env=False, timeout=30) as c:
        r = await c.post(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream"},
                         json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, {}


# —— 有没有、谁能花 ————————————————————————————————————————————————————————————————————

def test_claude_code_models_appear_only_when_it_is_logged_in(server_json, monkeypatch, tmp_path):
    import claw
    from engine import api
    server_json(BUILTIN)
    monkeypatch.setattr(runners, "_found", {})
    assert models.subscription("claude-code/opus") == "claude-code" and models.subscription("claude-code/claude-fake-opus[1m]") == "claude-code"
    assert models.subscription("anthropic/claude-fake-opus") is None and models.subscription("claude-code/rm -rf") is None
    assert not models.valid_ref("claude-code/opus") and models.known("claude-code/opus")
    assert models.models() == ["deepseek/deepseek-flash"] and claw.models() == ["deepseek/deepseek-flash"]
    s = api.status()
    assert s["claudeCode"]["available"] is False and s["claudeCode"]["models"] == [] and s["helpers"] == {}
    server_json({**BUILTIN, "engine": {**BUILTIN["engine"], "models": ["deepseek/deepseek-flash", "claude-code/sonnet"]}})
    assert models.models() == ["deepseek/deepseek-flash"]  # 写在 engine.models 里也不列：这台电脑上没有
    monkeypatch.setattr(runners, "_found", {"claude-code": {"bin": "/x/claude", "auth": "claude.ai", "billing": "subscription", "plan": "max"}})
    assert models.models() == ["deepseek/deepseek-flash", "claude-code/sonnet", "claude-code/opus", "claude-code/haiku"]
    assert claw.models() == models.models() and claw.caps()["modelSwitch"]
    s = api.status()
    assert s["claudeCode"]["available"] and s["claudeCode"]["billing"] == "subscription" and s["claudeCode"]["plan"] == "max"
    assert s["helpers"]["claude-code"] == {"auth": "claude.ai", "billing": "subscription", "plan": "max"} and "claude-code/opus" in s["models"]
    server_json({**BUILTIN, "engine": {**BUILTIN["engine"], "claude_code": {"models": ["claude-code/claude-fake-opus"]}}})
    assert models.models() == ["deepseek/deepseek-flash", "claude-code/claude-fake-opus"]
    monkeypatch.setattr(runners, "_found", {"claude-code": {"bin": "/x/claude", "auth": "api_key_helper", "billing": "api_key"}})
    assert models.models() == ["deepseek/deepseek-flash"] and not api.status()["claudeCode"]["available"]  # 写在设置里的登录：那边不读设置，用不上
    assert "claude-code" not in api.status()["helpers"]  # 后台任务的外援也不读用户的设置了（10/17 对齐成 --setting-sources ""）：同样不给
    monkeypatch.setattr(runners, "_found", {"claude-code": {"bin": "/x/claude", "auth": "claude.ai", "billing": "subscription"}})
    server_json({**BUILTIN, "engine": {**BUILTIN["engine"], "claude_code": False}})
    assert models.models() == ["deepseek/deepseek-flash"] and api.status()["claudeCode"]["available"] is False
    server_json({**BUILTIN, "engine": {"model": "claude-code/opus"}})
    assert claw.model() == "claude-code/opus" and run(claw.probe())[0]
    monkeypatch.setattr(runners, "_found", {})
    assert run(claw.probe()) == (False, "Claude Code isn't installed or logged in on this computer") and models.models() == []


def test_login_check_records_plan_or_api_key(server_json, monkeypatch, tmp_path):
    exe = tmp_path / "claude"
    exe.write_text(f"#!{sys.executable}\n" + FAKE.read_text(encoding="utf8"), encoding="utf8")
    exe.chmod(0o755)
    server_json({**BUILTIN, "tasks": {"runners": {"claude-code": {"bin": str(exe)}, "codex": False}}})
    monkeypatch.setenv("FAKE_CLAUDE_AUTH", json.dumps({"loggedIn": True, "authMethod": "claude.ai", "subscriptionType": "pro",
                                                       "email": "someone@example.invalid"}))
    got = runners.detect("claude-code")
    assert got == {"bin": str(exe), "auth": "claude.ai", "billing": "subscription", "plan": "pro"}  # 邮箱不读
    monkeypatch.setenv("FAKE_CLAUDE_AUTH", json.dumps({"loggedIn": True, "authMethod": "api_key"}))
    assert runners.detect("claude-code")["billing"] == "api_key" and not runners.is_subscription(runners.detect("claude-code"))
    monkeypatch.setenv("FAKE_CLAUDE_AUTH", json.dumps({"loggedIn": False, "authMethod": "none"}))
    assert runners.detect("claude-code") is None
    assert runners.billing_of("chatgpt") == "subscription" and runners.billing_of("api key") == "api_key"


def test_outsiders_cyber_and_external_interfaces_never_spend_the_subscription(cc, server_json):
    from engine import cyber, owner
    server_json({"claw": {"kind": "builtin"}, "engine": {"model": "claude-code/opus", "models": ["deepseek/deepseek-flash"],
                                                         "use": {"card": True, "doorman": "claude-code/sonnet", "json": "claude-code/opus"}}})
    assert models.route("card") == models.route("doorman") == models.route("json", "claude-code/haiku") == "deepseek/deepseek-flash"
    assert models.route("json", "moonshot/kimi-k3") == "moonshot/kimi-k3"
    assert cyber.model_of({"id": "c1", "model": "claude-code/opus"}) == "deepseek/deepseek-flash"
    assert models.outside("claude-code/opus") == "deepseek/deepseek-flash" and models.outside("moonshot/kimi-k3") == "moonshot/kimi-k3"
    assert models.may_spend("claude-code/opus", kind="owner", origin="user") and models.may_spend("claude-code/opus", kind="owner", origin="auto")
    for kind, origin in (("owner", "api"), ("owner", "openai"), ("owner", "acp"), ("owner", None), ("cyber", "user"), ("outsider", "card"),
                         ("outsider", "a2a")):
        assert not models.may_spend("claude-code/opus", kind=kind, origin=origin), (kind, origin)
    assert models.may_spend("deepseek/deepseek-flash", kind="outsider", origin="doorman")
    for kind in ("owner", "cyber", "outsider"):  # 哪条路忘了换，到 run_turn 也过不去
        with pytest.raises(EngineError) as e:
            run(engine.run_turn(engine.Spec(kind=kind, model="claude-code/opus"), MemorySession(), user("hi")))
        assert e.value.kind == "rule"
    f = cc.fake_model([{"text": "from the API model"}, {"text": "one-shot"}, {"text": "with its own gate"}])

    async def go():
        async with serving(cc):
            # ⑭ 的对外接口（OpenAI 兼容、ACP）选到 claude-code/… 的 Agent：外面的程序不算用户本人，换成非套餐模型
            res = await owner.respond("t-cc-ext", "2026-10-16", "hello from a script", "claude-code/opus", lambda t: None, origin="api")
            out: list[str] = []
            await owner.stream([{"role": "user", "content": "think about this"}], "agent:main:x", None, out.append)
            # 对外接口给的 agent / gate（app 自己的对话不给）：来路写成 user 也不交给 Claude Code
            gated = await owner.respond("t-cc-ext3", "2026-10-16", "hi", "claude-code/opus", lambda t: None, origin="user", agent="main",
                                        gate=tools_mod.gate)
            return res, "".join(out), gated
    res, one, gated = run(go())
    assert res.status == "done" and res.text == "from the API model" and res.model == "deepseek/deepseek-flash" and one == "one-shot"
    assert gated.text == "with its own gate" and gated.model == "deepseek/deepseek-flash"
    assert [r[0] for r in f.requests] == ["deepseek/deepseek-flash"] * 3 and cc.records() == []  # 假的 claude 一次没起
    server_json({"claw": {"kind": "builtin"}, "engine": {"model": "claude-code/opus", "use": {"card": True}}})  # 只有套餐：外人回合不走内核
    assert models.route("card") is None and models.outside() is None
    with pytest.raises(EngineError) as e:
        run(owner.respond("t-cc-ext2", "2026-10-16", "hi", None, lambda t: None, origin="acp"))
    assert e.value.kind == "config" and cc.records() == []


def test_the_v1_api_never_spends_the_subscription(cc, server_json):
    """⑭ 的对外接口（OpenAI 兼容的 /v1；ACP 适配器也经它）真走一遍：app 里这个对话选的、默认的都是 claude-code/…，也不起 Claude Code，
    换成非套餐的模型；只有套餐模型时回 503，不替外面的程序花用户的套餐。"""
    import httpx

    import chat
    import main
    tokens = {"api": "tok-api"}
    server_json({"claw": {"kind": "builtin"}, "auth": {"tokens": tokens},
                 "engine": {"model": "claude-code/opus", "models": ["deepseek/deepseek-flash"], "api": True}})
    chat.set_model(chat.ModelBody(thread="main", model="claude-code/sonnet"))
    f = cc.fake_model([{"text": "via the API"}, {"text": "streamed reply"}])
    ask = {"model": "openmousse/main", "messages": [{"role": "user", "content": "hi"}]}
    before = {t["id"] for t in events.turns(limit=1000)}

    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test", timeout=30) as c:
            h = {"Authorization": "Bearer tok-api"}
            plain = await c.post("/v1/chat/completions", headers=h, json=ask)
            streamed = await c.post("/v1/chat/completions", headers={**h, "X-OpenMousse-Events": "1"}, json={**ask, "stream": True})
            server_json({"claw": {"kind": "builtin"}, "auth": {"tokens": tokens}, "engine": {"model": "claude-code/opus", "api": True}})
            only = await c.post("/v1/chat/completions", headers=h, json=ask)
            return plain, streamed, only
    plain, streamed, only = run(go())
    assert plain.status_code == 200 and plain.json()["choices"][0]["message"]["content"] == "via the API"
    assert streamed.status_code == 200 and "streamed reply" in streamed.text
    assert [r[0] for r in f.requests] == ["deepseek/deepseek-flash"] * 2
    assert only.status_code == 503 and only.json()["error"]["code"] == "engine_config"
    assert "engine.models" in only.json()["error"]["message"] and "subscription" in only.json()["error"]["message"]  # 说清要加非套餐的
    assert cc.records() == []  # 假的 claude 一次没起
    api_turns = [t for t in events.turns(limit=1000) if t["id"] not in before]
    assert len(api_turns) == 2 and {(t["model"], t["origin"]) for t in api_turns} == {("deepseek/deepseek-flash", "api")}


# —— 一轮 ————————————————————————————————————————————————————————————————————————————

def test_a_turn_streams_into_the_app_and_is_recorded_on_our_side(cc, monkeypatch):
    import chat
    monkeypatch.setattr(chat, "PROGRESS_EVERY", 0)
    cc.replies["schedule"] = "15:00 dentist"
    cc.scenario_([[{"think": "Let me look at the schedule."}, {"tool": "schedule", "input": {"args": ["day"]}},
                   {"say": "You have the dentist at 3.", "chunks": ["You have ", "the dentist ", "at 3."]}]])

    async def go():
        async with serving(cc):
            r = chat.start_run("t-cc-1", "what's on today?", "claude-code/sonnet")
            evs = await collect(chat.attach(r))
            await finish(chat, "t-cc-1")
            token = cc.records("start")[0]["conf"]["mcpServers"]["openmousse"]["headers"]["Authorization"][7:]
            after = await post(cc.url, token, "tools/list", {})
            return r, evs, token, after
    r, evs, token, after = run(go())
    assert r.status == "ok" and r.text == "You have the dentist at 3." and dict(evs)["done"]["text"] == "You have the dentist at 3."
    assert "".join(d["text"] for e, d in evs if e == "delta") == "You have the dentist at 3."
    progress = [d for e, d in evs if e == "progress"]
    assert progress[0]["phase"] == "preparing_context" and "Let me look at the schedule." in [p["thought"] for p in progress]
    assert [(s["tool"], s["detail"], s["status"]) for s in steps_of(evs)] == [("schedule", "schedule day", "done")]
    assert cc.calls == [("schedule", ["day"], "main")]  # 经 /mcp 走内核的关卡跑的，按 main 跑
    start, stdin = cc.records("start")[0], cc.records("stdin")[0]
    argv = start["argv"]
    for flag in ("-p", "--include-partial-messages", "--strict-mcp-config", "--disable-slash-commands"):
        assert flag in argv
    val = {k: argv[argv.index(k) + 1] for k in ("--input-format", "--output-format", "--setting-sources", "--tools", "--permission-mode",
                                                 "--model", "--session-id", "--settings", "--allowedTools")}
    assert val["--input-format"] == val["--output-format"] == "stream-json" and val["--setting-sources"] == "" and val["--tools"] == ""
    assert val["--permission-mode"] == "dontAsk" and val["--model"] == "sonnet" and "--resume" not in argv
    settings_json = json.loads(val["--settings"])
    assert settings_json["disableAllHooks"] is True and settings_json["autoMemoryEnabled"] is False and settings_json["enabledPlugins"] == {}
    allowed = val["--allowedTools"].split(",")
    assert {"mcp__openmousse__schedule", "mcp__openmousse__skill", "mcp__openmousse__memory_search", "mcp__openmousse__tasks"} <= set(allowed)
    env = start["env"]
    assert env["ANTHROPIC_API_KEY"] is None and env["ANTHROPIC_BASE_URL"] is None  # 它只能用它自己存的登录
    assert env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] == "1" and env["CLAUDE_CODE_DISABLE_CLAUDE_MDS"] == "1"  # 不往用户的 ~/.claude 记我们的聊天
    assert env["ENABLE_CLAUDEAI_MCP_SERVERS"] == "false" and env["MCP_CONNECTION_NONBLOCKING"] == "0" and env["ENABLE_TOOL_SEARCH"] == "false"
    assert not {"CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_HOST_SESSION_ID"} & set(start["env_keys"])  # 上一层会话留下的不给
    assert start["cwd"] == str(engine.claude_brain.settings.data_dir / "claude-code" / "main") and start["cwd_mode"] == 0o700
    assert start["conf_mode"] == start["system_mode"] == 0o600 and token and not any(token in a for a in argv)  # 令牌只在 0600 的文件里
    assert "Today is" in start["system"] and "mcp__openmousse__schedule is the schedule tool" in start["system"]
    assert after[0] == 401 and mcp_engine.lookup(token) is None  # 这一轮完了就收回
    assert not [p for p in claude_brain.base_dir().iterdir() if p.name.startswith((".mcp-", ".system-"))]  # 临时文件删了
    msg = stdin["msg"]
    [said] = msg["message"]["content"]
    assert msg["type"] == "user" and msg["uuid"] and said["type"] == "text" and STAMP.match(said["text"])  # 前面带上当地时间（10/19 起）
    assert unstamped(said["text"]) == "what's on today?"
    s = DbSession.latest(day_key("t-cc-1", chat.day_of(r.started)))
    msgs = s.messages()
    assert [m["role"] for m in msgs] == ["user", "assistant"] and msgs[0]["ref"] == r.user_id
    assert text_of(msgs[1]) == "You have the dentist at 3." and msgs[1]["provider"] == "claude-code" and msgs[1]["cc"] == val["--session-id"]
    assert msgs[1]["served"] == "claude-fake-sonnet" and msgs[1]["usage"] == {} and msgs[1]["cc_usage"]["in"] == 120  # 压缩估算不拿它当上一次多大
    t = events.turns("t-cc-1")[0]
    assert t["model"] == "claude-code/sonnet" and t["status"] == "done" and t["kind"] == "owner" and t["session"] == s.id
    assert t["models"] == ["claude-code/sonnet"]  # 这一轮真调过的（第 13 步那一栏；以前套餐的这一轮是空的）
    assert t["usage"]["in"] == 120 and t["usage"]["out"] == 30 and t["usage"]["cache_read"] == 1000 and t["usage"]["cost"] is None
    assert t["usage"]["notional_cost_usd"] == 0.02  # 套餐：不记成钱，折算另存
    assert [(x["type"], x["status"]) for x in events.steps(t["id"])] == [("model", "ok"), ("model", "ok"), ("tool", "ok"), ("model", "ok")]


def test_same_day_resumes_its_session_and_the_next_day_starts_fresh(cc, server_json):
    from engine import owner
    server_json({**BUILTIN, "engine": {**BUILTIN["engine"], "claude_code": {"effort": "low", "fallback_model": "sonnet,haiku"}}})
    cc.scenario_([[{"say": "first"}], [{"say": "second"}], [{"say": "fourth"}], [{"say": "next day"}]])
    model = cc.fake_model([{"text": "third, from another model"}])
    day = "2026-10-16"

    async def go():
        async with serving(cc):
            for text, m in (("one", "claude-code/opus"), ("two", "claude-code/opus"), ("three", "deepseek/deepseek-flash"),
                            ("four", "claude-code/opus")):
                res = await owner.respond("t-cc-days", day, text, m, lambda t: None)
                assert res.status == "done", res.error
            nxt = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
            return await owner.respond("t-cc-days", nxt, "five", "claude-code/opus", lambda t: None)
    res = run(go())
    assert res.text == "next day" and len(model.requests) == 1
    starts, inputs = cc.records("start"), cc.records("stdin")
    first = starts[0]["argv"][starts[0]["argv"].index("--session-id") + 1]
    argv = starts[0]["argv"]
    assert argv[argv.index("--effort") + 1] == "low" and argv[argv.index("--fallback-model") + 1] == "sonnet,haiku"  # 思考档位、备用型号
    assert [s["argv"][s["argv"].index("--resume") + 1] for s in starts[1:3]] == [first, first]  # 同一天接着它的会话
    assert "--session-id" in starts[3]["argv"] and starts[3]["argv"][starts[3]["argv"].index("--session-id") + 1] != first  # 第二天新的
    assert unstamped(text_of_cc(inputs[1])) == "two" and STAMP.match(text_of_cc(inputs[1]))  # 它都看过：前面不接东西（只有当地时间）
    four = text_of_cc(inputs[2])  # 中间换别的模型聊的那一轮：它没看到，接在这句前面当记录
    assert "since your last reply" in four and "User: three" in four and "Assistant: third, from another model" in four
    assert unstamped(four).endswith("[The user's message:]\nfour") and STAMP.search(four)
    s = DbSession.latest(day_key("t-cc-days", day))
    assert [text_of(m) for m in s.messages()] == ["one", "first", "two", "second", "three", "third, from another model", "four", "fourth"]
    assert model.requests[0][1].messages[-1]["role"] == "user" and text_of(model.requests[0][1].messages[-2]) == "second"  # 换回别的模型接得上


def text_of_cc(rec: dict) -> str:
    return "".join(b.get("text", "") for b in rec["msg"]["message"]["content"] if b.get("type") == "text")


def unstamped(text: str) -> str:
    """去掉用户那句话前面的当地时间（10/19 起每句都带）。"""
    return STAMP.sub("", text)


def test_tools_go_through_the_kernel_gate_and_the_token_is_scoped(cc, monkeypatch):
    import chat
    import data
    import inbox
    monkeypatch.setattr(chat, "PROGRESS_EVERY", 0)
    tools_mod.install_hooks()
    with chat._lock, data.ddb() as conn:
        conn.execute("INSERT OR REPLACE INTO groups(id, name, icon, purpose, created_at) VALUES('g-cc', 'Diet', 'x', 'Meals.', ?)", (chat.now_iso(),))
    cc.scenario_([[{"list": True},
                   {"tool": "diet", "input": {"args": ["day"]}},
                   {"tool": "journal", "input": {"args": ["delete", "j-cc-1"], "why": "Logged twice."}},
                   {"tool": "board", "input": {"args": ["show", "--agent", "main"]}},
                   {"tool": "agents", "input": {"args": ["list"]}},
                   {"say": "Deleting needs your OK."}]])

    async def go():
        async with serving(cc):
            r = chat.start_run("g-cc", "clean up my diet log", "claude-code/haiku")
            evs = await collect(chat.attach(r))
            await finish(chat, "g-cc")
            return r, evs
    r, evs = run(go())
    listed = cc.records("list")[0]["names"]
    assert "diet" in listed and "memory_search" in listed and "skill" in listed and "agents" not in listed  # 这个 Agent 这一轮的整张表
    res = {x["name"]: x["response"]["result"] for x in cc.records("mcp")}
    assert res["diet"]["isError"] is False and cc.calls == [("diet", ["day"], "g-cc")]  # 只读：直接做，按这个 Agent 跑
    pend = json.loads(res["journal"]["content"][0]["text"])
    assert res["journal"]["isError"] is False and pend["status"] == "approval-pending"  # 撤不回：出卡，回 approval-pending，没跑
    assert res["board"]["isError"] and "--agent" in res["board"]["content"][0]["text"]  # 指别的 Agent：关卡挡下
    assert res["agents"]["isError"] and "no agents tool" in res["agents"]["content"][0]["text"]  # 主对话专用的工具：没有
    with chat._lock, inbox.idb() as conn:
        card = conn.execute("SELECT * FROM inbox WHERE id=?", (pend["inbox"],)).fetchone()
    assert card["kind"] == "tool" and card["status"] == "pending" and card["thread"] == "g-cc" and card["why"] == "Logged twice."
    call = tools_mod.call_of(pend["inbox"])
    assert call["tool"] == "journal" and json.loads(call["args"]) == ["delete", "j-cc-1"] and call["turn"] == events.turns("g-cc")[0]["id"]
    detail = {s["tool"]: (s["detail"], s["status"]) for s in steps_of(evs)}
    assert detail["journal"] == ("journal delete · waiting for your OK", "done") and detail["board"][1] == "failed"
    assert r.text == "Deleting needs your OK."


def test_stop_also_cancels_a_tool_that_is_running_for_it(cc, monkeypatch):
    """点了停：经 /mcp 正在跑的我们的工具也收到取消（和内核自己的回合一样），这一轮照常停下。"""
    import chat
    started: list[str] = []
    cancelled: list[str] = []

    async def slow(name, args, stdin, agent, *, kill_on_cancel=False):
        started.append(name)
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.append(name)
            raise
        return "late"
    monkeypatch.setattr(bridges, "run", slow)  # 这个测试里命令行工具一直跑
    cc.scenario_([[{"say": "Looking."}, {"tool": "schedule", "input": {"args": ["day"]}}, {"say": "never"}]])

    async def go():
        async with serving(cc):
            r = chat.start_run("t-cc-stop-tool", "check", "claude-code/opus")
            await until(lambda: started)
            await chat.stop(chat.StopBody(thread="t-cc-stop-tool"))
            await finish(chat, "t-cc-stop-tool", 8)
            await until(lambda: cancelled, 3)
            return r
    r = run(go())
    assert r.status == "stopped" and cancelled == ["schedule"] and r.text.startswith("Looking.")


def test_stop_interrupts_claude_code_and_keeps_what_it_said(cc):
    import chat
    cc.scenario_([[{"say": "Working on it."}, {"sleep": 8}, {"say": "never shown"}]])

    async def go():
        async with serving(cc):
            r = chat.start_run("t-cc-stop", "do the long thing", "claude-code/opus")
            await until(lambda: "Working on it." in r.text)
            t0 = time.monotonic()
            await chat.stop(chat.StopBody(thread="t-cc-stop"))
            await finish(chat, "t-cc-stop", 8)
            return r, time.monotonic() - t0
    r, took = run(go())
    assert r.status == "stopped" and r.text == "Working on it.\n\n(Stopped)" and took < 4
    assert cc.records("sigint") and not cc.records("exit")  # 进程组收到 SIGINT（它结束这一轮、记进它的会话）
    s = DbSession.latest(day_key("t-cc-stop", chat.day_of(r.started)))
    last, note = s.messages()[-2:]  # 后面补了一句「用户停了」（loop.note_stopped）
    assert last["role"] == "assistant" and text_of(last) == "Working on it." and last["stop"] == "aborted" and last["cc"]
    assert note["role"] == "system" and note.get("stopped")
    assert events.turns("t-cc-stop")[0]["status"] == "aborted"


def test_steer_reaches_claude_code_in_the_same_turn(cc):
    import chat
    cc.delays["schedule"] = 0.6
    cc.scenario_([[{"say": "Checking."}, {"tool": "schedule", "input": {"args": ["day"]}}, {"read_steer": 4},
                   {"say": "Noted the dentist too."}]])

    async def go():
        async with serving(cc):
            r = chat.start_run("t-cc-steer", "check my day", "claude-code/opus")
            first = asyncio.create_task(collect(chat.attach(r)))
            await until(lambda: cc.calls)
            resp = await chat.send(chat.SendBody(thread="t-cc-steer", text="also the dentist at 3"))
            second = await collect(resp.body_iterator)
            return r, await first, second
    r, first, second = run(go())
    assert second[0][0] == "queued" and second[0][1]["steer"] is True
    sid = int(second[0][1]["userId"][2:])
    assert dict(first)["done"]["id"] == dict(second)["done"]["id"]  # 还是同一条回复
    assert r.text == "Checking.\n\nNoted the dentist too." and len([x for x in cc.records("stdin")]) == 2
    steer = cc.records("stdin")[1]["msg"]
    assert unstamped(text_of_cc({"msg": steer})) == "also the dentist at 3" and steer["uuid"] and "priority" not in steer
    s = DbSession.latest(day_key("t-cc-steer", chat.day_of(r.started)))
    assert [(m["role"], text_of(m)) for m in s.messages()] == [("user", "check my day"), ("assistant", "Checking."),
                                                              ("user", "also the dentist at 3"), ("assistant", "Noted the dentist too.")]
    assert s.messages()[2]["ref"] == sid and chat.RUNS["t-cc-steer"].user_id == r.user_id


def test_steer_reported_only_on_the_result_keeps_the_answer_after_it(cc):
    """它只在 result 上报这一轮答了哪几条：最后那段话放在插话后面（我们的会话里问在前、答在后）。"""
    from engine import owner
    control = engine.Control()
    cc.delays["schedule"] = 0.4
    cc.scenario_([[{"say": "Checking."}, {"tool": "schedule", "input": {"args": ["day"]}}, {"read_steer": 4, "echo": False},
                   {"say": "Noted the dentist too."}]])

    async def go():
        async with serving(cc):
            task = asyncio.create_task(owner.respond("t-cc-late2", "2026-10-16", "check my day", "claude-code/opus", lambda t: None,
                                                     control=control))
            await until(lambda: cc.calls)
            assert control.steer({**user("also the dentist"), "ref": 7})
            return await task
    res = run(go())
    assert res.status == "done" and res.pending == [] and res.text == "Noted the dentist too."
    s = DbSession.latest(day_key("t-cc-late2", "2026-10-16"))
    assert [(m["role"], text_of(m)) for m in s.messages()] == [("user", "check my day"), ("assistant", "Checking."),
                                                              ("user", "also the dentist"), ("assistant", "Noted the dentist too.")]


def test_steer_after_it_finished_is_still_answered_before_it_exits(cc):
    """插话写进去时它这一轮刚说完：它把收到的处理完才退，我们接着读，还算这一轮（chat.py 那边是同一条回复）。"""
    from engine import owner
    control = engine.Control()
    cc.scenario_([[{"say": "Done."}, {"sleep": 0.3}]])

    async def go():
        async with serving(cc):
            task = asyncio.create_task(owner.respond("t-cc-late", "2026-10-16", "hi", "claude-code/opus", lambda t: None, control=control))
            await until(lambda: cc.records("stdin"))
            control.steer({**user("one more thing"), "ref": 99})
            return await task
    res = run(go())
    assert res.status == "done" and res.pending == []
    s = DbSession.latest(day_key("t-cc-late", "2026-10-16"))
    assert [(m["role"], text_of(m)) for m in s.messages()] == [("user", "hi"), ("assistant", "Done."), ("user", "one more thing"),
                                                              ("assistant", "(later) one more thing")]


def test_ask_user_reaches_claude_code_through_mcp_and_waits_for_the_answer(cc, server_json):
    """⑫b：它自己的 AskUserQuestion 关着，经 /mcp 拿到的是我们的 ask_user：问题是回复里的一段，用户插的下一句就是回答
    （走工具结果回给它，不写进它的标准输入），回答也记进我们的会话。"""
    import chat
    q = "Red or blue for the fictional Wren Bakery?"
    cc.scenario_([[{"say": "Let me ask."}, {"tool": "ask_user", "input": {"question": q, "options": ["Red", "Blue"]}}, {"say": "Blue it is."}]])

    async def go():
        async with serving(cc):
            r = chat.start_run("t-cc-ask", "pick a color", "claude-code/opus")
            first = asyncio.create_task(collect(chat.attach(r)))
            await until(lambda: r.asking is not None)
            resp = await chat.send(chat.SendBody(thread="t-cc-ask", text="2"))
            second = await collect(resp.body_iterator)
            await finish(chat, "t-cc-ask")
            return r, await first, second
    r, first, second = run(go())
    shown = f"{q}\n\n1. Red\n2. Blue\n\nReply with the number, the option text, or your own answer."
    assert r.status == "ok" and r.text == f"Let me ask.\n\n{shown}\n\nBlue it is."
    assert next(d for e, d in first if e == "ask_user")["options"] == ["Red", "Blue"]
    sid = int(second[0][1]["userId"][2:])
    assert second[0][1]["steer"] is True and dict(second)["done"]["id"] == dict(first)["done"]["id"]  # 还是同一条回复
    [call] = cc.records("mcp")
    out = json.loads(call["response"]["result"]["content"][0]["text"])
    assert call["name"] == "ask_user" and out["status"] == "answered" and out["option"] == "Blue"
    assert len(cc.records("stdin")) == 1  # 回答走的是工具结果
    start = cc.records("start")[0]
    allowed = start["argv"][start["argv"].index("--allowedTools") + 1].split(",")
    assert {f"mcp__openmousse__{n}" for n in ("ask_user", "todo", "enter_plan_mode", "propose_plan")} <= set(allowed)
    assert start["argv"][start["argv"].index("--tools") + 1] == "" and start["env"]["MCP_TOOL_TIMEOUT"] == "330000"  # 默认等 5 分钟：放得下
    s = DbSession.latest(day_key("t-cc-ask", chat.day_of(r.started)))
    assert [(m["role"], text_of(m)) for m in s.messages()] == [("user", "pick a color"), ("assistant", f"Let me ask.\n\n{shown}"),
                                                              ("user", "2"), ("assistant", "Blue it is.")]
    assert s.messages()[2]["ref"] == sid
    server_json({**BUILTIN, "engine": {**BUILTIN["engine"], "ask_user": {"timeout": 900}}})
    assert claude_brain.child_env()["MCP_TOOL_TIMEOUT"] == "915000"  # 等得更久：一次 MCP 调用的上限跟着放宽


def test_pictures_reach_claude_code_as_mcp_image_content(cc, monkeypatch, tmp_path):
    """⑫b：工具结果里的图（这里是读工作区里的一张图片）经 /mcp 回给它的是 MCP 的 image 内容，跟在字后面。"""
    import base64
    import io

    import chat
    from config import settings
    from PIL import Image
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setattr(settings, "workspace", ws)
    buf = io.BytesIO()
    Image.new("RGB", (16, 8), (0, 0, 200)).save(buf, "PNG")
    (ws / "chart.png").write_bytes(buf.getvalue())
    cc.scenario_([[{"tool": "read_file", "input": {"path": "chart.png"}}, {"say": "A blue chart."}]])

    async def go():
        async with serving(cc):
            chat.start_run("t-cc-pic", "what's in chart.png?", "claude-code/opus")
            return await finish(chat, "t-cc-pic")
    r = run(go())
    assert r.status == "ok" and r.text == "A blue chart."
    [call] = cc.records("mcp")
    text, pic = call["response"]["result"]["content"]
    assert text["type"] == "text" and text["text"].startswith("chart.png: image file [image/png]") and text["text"].endswith("16x8")
    assert (pic["type"], pic["mimeType"], pic["data"]) == ("image", "image/png", base64.b64encode(buf.getvalue()).decode())


def test_rewind_starts_a_new_claude_session_with_the_earlier_record(cc):
    import chat
    cc.scenario_([[{"say": "first answer"}], [{"say": "second answer"}], [{"say": "third answer"}]])

    async def go():
        async with serving(cc):
            chat.start_run("t-cc-rw", "first question", "claude-code/opus")
            await finish(chat, "t-cc-rw")
            r2 = chat.start_run("t-cc-rw", "second question", "claude-code/opus")
            await finish(chat, "t-cc-rw")
            out = await chat.rewind(chat.MessageRef(thread="t-cc-rw", id=f"db{r2.user_id}"))
            chat.start_run("t-cc-rw", "third question", "claude-code/opus")
            await finish(chat, "t-cc-rw")
            return out
    out = run(go())
    assert out["rewound"]
    starts, inputs = cc.records("start"), cc.records("stdin")
    first = starts[0]["argv"][starts[0]["argv"].index("--session-id") + 1]
    assert starts[1]["argv"][starts[1]["argv"].index("--resume") + 1] == first
    third = starts[2]["argv"]
    assert "--resume" not in third and third[third.index("--session-id") + 1] != first  # 它的会话回不去：开新的
    text = text_of_cc(inputs[2])
    assert "took back a later message" in text and "can't be rewound" in text and "User: first question" in text and "first answer" in text
    assert "second question" not in text and text.endswith("third question")


def test_a_lost_session_is_started_again_with_the_record(cc):
    from engine import owner
    cc.scenario_([[{"say": "hello again"}]])

    async def go():
        async with serving(cc):
            await owner.respond("t-cc-lost", "2026-10-16", "hi", "claude-code/opus", lambda t: None)
            (cc.state / "sessions.json").write_text("[]")  # 它那边把会话清掉了（30 天、claude purge……）
            return await owner.respond("t-cc-lost", "2026-10-16", "are you there?", "claude-code/opus", lambda t: None)
    res = run(go())
    assert res.status == "done" and res.text == "hello again"
    assert len(cc.records("missing")) == 1 and "--session-id" in cc.records("start")[-1]["argv"]
    assert "User: hi" in text_of_cc(cc.records("stdin")[-1])


def test_login_or_api_errors_come_back_as_a_failed_reply(cc):
    import chat
    cc.scenario_([[{"result": {"subtype": "success", "is_error": True, "result": "Invalid API key · Please run /login"}}]])

    async def go():
        async with serving(cc):
            chat.start_run("t-cc-err", "hi", "claude-code/opus")
            return await finish(chat, "t-cc-err")
    r = run(go())
    assert r.status == "error" and "run /login" in r.error and "sign in again" in r.error
    assert events.turns("t-cc-err")[0]["status"] == "error"


def test_scheduled_runs_use_claude_code_once_without_cron_or_tasks(cc, monkeypatch):
    from engine import unattended
    monkeypatch.setattr(runners, "_found", {"claude-code": {"bin": str(cc.exe), "auth": "api_key", "billing": "api_key"}})
    cc.scenario_([[{"list": True}, {"say": "Nothing new.\nNO_REPLY"}]])

    async def go():
        async with serving(cc):
            return await unattended.fresh("main", "main", "cron:j-cc", "check the inbox", model="claude-code/haiku", light=True)
    out = run(go())
    assert out.status == "silent" and out.model == "claude-code/haiku"
    argv = cc.records("start")[0]["argv"]
    assert "--no-session-persistence" in argv and "--session-id" not in argv and "--resume" not in argv
    names = cc.records("list")[0]["names"]
    assert "cron" not in names and "tasks" not in names and "schedule" in names
    t = events.turns("cron:j-cc")[0]
    assert t["model"] == "claude-code/haiku" and t["origin"] == "auto" and t["usage"]["cost"] == 0.02  # 密钥登录：用户自己账上的钱，照记


# —— 第 5 项的外援、子会话 ———————————————————————————————————————————————————————————————

def test_helper_task_tokens_get_skill_and_memory_but_stay_read_only(cc):
    row = {"id": "tk_cc_mcp", "agent": "main", "runner": "claude-code", "depth": 1, "thread": "t-x"}
    tools = [t for t in tasks.child_tools(row) if t.name in runners.allowed(tasks.child_tools(row))]
    names = {t.name for t in tools}
    assert {"skill", "memory_search", "memory_get", "schedule"} <= names and "tasks" not in names and "inbox" not in names
    tok = tasks.mint(row, tools, "read")

    async def go():
        async with serving(cc):
            listed = await post(cc.url, tok, "tools/list", {})
            calls = {}
            for key, name, args in (("skill", "skill", {}), ("search", "memory_search", {"query": "dentist"}),
                                    ("read", "schedule", {"args": ["day"]}), ("write", "schedule", {"args": ["add", "dentist"]}),
                                    ("delete", "journal", {"args": ["delete", "j1"]}), ("other", "schedule", {"args": ["day", "--source", "fitness"]}),
                                    ("board", "board", {"args": ["show"]})):
                calls[key] = (await post(cc.url, tok, "tools/call", {"name": name, "arguments": args}))[1]["result"]
            tasks.revoke(tok)
            gone = await post(cc.url, tok, "tools/list", {})
            return listed, calls, gone
    listed, calls, gone = run(go())
    assert listed[0] == 200 and {t["name"] for t in listed[1]["result"]["tools"]} == names
    assert calls["skill"]["isError"] is False and calls["search"]["isError"] is False and calls["read"]["isError"] is False
    assert calls["write"]["isError"] and "read-only" in calls["write"]["content"][0]["text"]
    assert calls["delete"]["isError"] and calls["delete"]["content"][0]["text"] == tasks.NOT_HERE
    assert calls["other"]["isError"] and "--source" in calls["other"]["content"][0]["text"]
    assert calls["board"]["isError"] is ("board" not in names)
    assert ("schedule", ["day"], "main") in cc.calls and gone[0] == 401
    assert not [c for c in cc.calls if c[0] == "journal" or c[1][:1] == ["add"] or "--source" in c[1]]  # 写的、撤不回的、冒充的一个没跑


def test_server_json_tokens_still_reach_only_the_14_tools(cc, server_json):
    import mcp_bridge
    server_json({**BUILTIN, "auth": {"tokens": {"mcp": "mcp-token-for-test"}}})
    tok = mcp_engine.grant(agent="main", tools=tools_mod.for_agent("main"), gate=tools_mod.gate, conversation="main")
    assert mcp_bridge.identify(tok) is None and mcp_bridge.engine_app(tok) is mcp_engine.app
    assert mcp_bridge.engine_app("mcp-token-for-test") is None and mcp_bridge.identify("mcp-token-for-test") == ("mcp", None)
    mcp_engine.revoke(tok)
    assert mcp_bridge.engine_app(tok) is None


def test_child_sessions_compact_like_owner_turns(server_json, monkeypatch):
    from engine import compact, providers
    server_json(BUILTIN)
    seen: list[str] = []

    async def maybe(session, spec, tid, incoming, render):
        system, state = render()
        seen.append("maybe")
        assert "You are running as a background sub-agent" in system and state == {}
        return "REWRITTEN OPENING"

    async def on_overflow(session, spec, tid, err, render):
        seen.append("overflow")
        return "REWRITTEN AFTER OVERFLOW"
    monkeypatch.setattr(compact, "maybe", maybe)
    monkeypatch.setattr(compact, "on_overflow", on_overflow)
    f = FakeModel([EngineError("prompt is too long: 250000 tokens > 200000 maximum", kind="http", status=400), {"text": "done"}])
    monkeypatch.setattr(providers, "stream", f.stream)
    row = {"id": "tk_cc_compact", "agent": "main", "thread": "t-x", "model": "deepseek/deepseek-flash", "depth": 1, "level": "undo", "tools": 0,
           "timeout_s": 60, "label": "x", "task": "y"}
    from engine import ledger
    ledger.insert(**{**row, "parent": None, "turn": None, "call": None, "runner": "engine", "status": "running", "created_at": ledger.now_ms(),
                     "delivery": "none", "retry_of": None, "inbox": None, "data": {}})
    tools = tasks.child_tools(row)
    session = DbSession.create("mousse:task:tk_cc_compact", kind="task", agent="main", model=row["model"], system=tasks.child_system(row, tools))
    res = run(tasks._drive(row, session, tools, user("go"), engine.Control()))
    assert res.status == "done" and seen == ["maybe", "overflow"]
    assert f.requests[0][1].system == "REWRITTEN OPENING" and f.requests[1][1].system == "REWRITTEN AFTER OVERFLOW"


def test_the_brain_stays_off_the_hosts_settings_even_with_a_hostile_home(cc, tmp_path, monkeypatch):
    """用户的 ~/.claude 里有 hooks、CLAUDE.md、自动记忆：命令行和环境保证它们都不加载（假的 claude 只看我们传了什么）。"""
    from engine import owner
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "settings.json").write_text(json.dumps({"hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": "touch /x"}]}]}}))
    (home / ".claude" / "CLAUDE.md").write_text("Always reply in pirate speak.")
    monkeypatch.setenv("HOME", str(home))
    cc.scenario_([[{"say": "fine"}]])

    async def go():
        async with serving(cc):
            return await owner.respond("t-cc-home", "2026-10-16", "hi", "claude-code/opus", lambda t: None)
    assert run(go()).status == "done"
    start = cc.records("start")[0]
    argv = start["argv"]
    assert argv[argv.index("--setting-sources") + 1] == "" and json.loads(argv[argv.index("--settings") + 1])["disableAllHooks"]
    assert start["env"]["HOME"] == str(home)  # 它的登录在家目录里：家目录照给，设置、记忆、CLAUDE.md 一样不读
    assert "pirate" not in start["system"] and sorted(os.listdir(home / ".claude")) == ["CLAUDE.md", "settings.json"]
    assert stat.S_IMODE(os.stat(start["cwd"]).st_mode) == 0o700 and os.listdir(start["cwd"]) == []
