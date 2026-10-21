"""第 7 批 12：codex/… 当主人回合的模型（engine/provider_cli.py）。codex 是 tests/fake_codex.py（照 0.163.0 的样子吐 --json 事件，拿环境变量里
的临时令牌真的回调我们的 /mcp）；/mcp 用 uvicorn 起在这一轮测试的事件循环里；备用的模型是假模型服务器。不碰真的 Codex、不连真的模型。"""
from __future__ import annotations

import asyncio
import contextlib
import json
import socket
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import bridges
from engine import fallback, loop, mcp_engine, models, provider_cli, runners
from engine import tools as tools_mod
from engine.errors import EngineError
from engine.loop import Control, Spec
from engine.session import MemorySession, user
from evals.fakemodel import FakeModelServer, Reply

pytest.importorskip("mcp")
pytest.importorskip("uvicorn")

FAKE = Path(__file__).resolve().parent / "fake_codex.py"
CONV = "codex-test"


def run(coro):
    return asyncio.run(coro)


async def until(cond, timeout: float = 8.0) -> None:
    t0 = time.monotonic()
    while not cond():
        if time.monotonic() - t0 > timeout:
            raise AssertionError("timed out waiting")
        await asyncio.sleep(0.02)


@pytest.fixture
def cx(tmp_path, monkeypatch, server_json, request):
    """假的 codex（ChatGPT 登录）+ 本机的 /mcp + 假的命令行工具 + 一个假的按量模型（备用链用）。"""
    import mcp_bridge  # noqa: F401 — Gate 认临时令牌要它
    from config import settings
    fm = FakeModelServer([]).start()
    request.addfinalizer(fm.stop)
    st = SimpleNamespace(fm=fm, log=tmp_path / "codex.log", calls=[])

    def cfg(codex=True, **engine) -> None:  # codex="unset"：不写 engine.codex（默认关）
        server_json({"claw": {"kind": "builtin"}, "engine": {"model": "deepseek/deepseek-flash", "models": ["deepseek/deepseek-flash", "codex/default"],
                                                             "providers": {"fake": {"api": "openai", "url": fm.openai_url, "key_env": ""}},
                                                             **({} if codex == "unset" else {"codex": codex}), **engine}})
    cfg()
    st.cfg = cfg
    exe = tmp_path / "codex"
    exe.write_text(f"#!{sys.executable}\n" + FAKE.read_text(encoding="utf8"), encoding="utf8")
    exe.chmod(0o755)
    st.exe = exe
    real_bin = runners.bin_of
    monkeypatch.setattr(runners, "bin_of", lambda name: str(exe) if name == "codex" else real_bin(name))
    monkeypatch.setattr(runners, "_warm", False)  # 不在后台再查：查到的就是下面这一份
    monkeypatch.setattr(runners, "_found", {"codex": {"bin": str(exe), "auth": "chatgpt", "billing": "subscription"}})
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setenv("FAKE_CODEX_LOG", str(st.log))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-should-not-pass")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://proxy.invalid")
    monkeypatch.setattr(loop, "MODEL_RETRIES", (0, 0))
    provider_cli._status.clear()  # noqa: SLF001
    fallback.reset()
    mcp_engine._grants.clear()  # noqa: SLF001

    async def fake_run(name, args, stdin, agent, *, kill_on_cancel=False):
        st.calls.append((name, list(args), agent))
        return f"ok: {name} {' '.join(args[:1])}"
    monkeypatch.setattr(bridges, "run", fake_run)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(16)
    st.sock = sock
    st.url = f"http://127.0.0.1:{sock.getsockname()[1]}/mcp"
    monkeypatch.setattr(runners, "mcp_url", lambda: st.url)

    def scenario(steps: list) -> None:
        monkeypatch.setenv("FAKE_CODEX_SCENARIO", json.dumps(steps))
    st.scenario = scenario

    def records(kind: str | None = None) -> list[dict]:
        rows = [json.loads(x) for x in st.log.read_text(encoding="utf8").splitlines() if x.strip()] if st.log.exists() else []
        return [r for r in rows if kind is None or r["kind"] == kind]
    st.records = records
    yield st
    sock.close()
    fallback.reset()
    mcp_engine._grants.clear()  # noqa: SLF001


@contextlib.asynccontextmanager
async def serving(st):
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


def owner_spec(**kw) -> Spec:
    tools = tools_mod.for_agent("main")
    base = {"kind": "owner", "model": "codex/default", "system": "You are Mousse, a fictional assistant.", "tools": tools, "conversation": CONV,
            "agent": "main", "origin": "user", "gate": tools_mod.gate}
    return Spec(**{**base, **kw})


# —— 谁能花 ————————————————————————————————————————————————————————————————————————

def test_codex_is_the_users_plan_and_only_their_own_turns_spend_it(cx, monkeypatch):
    from engine import chatgpt_plan
    assert models.subscription("codex/default") == "codex" and models.subscription("codex/gpt-6-codex") == "codex"
    assert models.valid_ref("codex/default") and models.known("codex/default") and models.subscription("codex/rm -rf") is None
    assert models.may_spend("codex/default", kind="owner", origin="user") and models.may_spend("codex/default", kind="owner", origin="relay")
    assert models.may_spend("codex/default", kind="cyber", origin="user")  # 用户自己的赛博
    for kind, origin in (("owner", "api"), ("owner", "acp"), ("outsider", "card"), ("cyber", "butler"), ("owner", "cli")):
        assert not models.may_spend("codex/default", kind=kind, origin=origin), (kind, origin)
    monkeypatch.setattr(chatgpt_plan, "background_allowed", lambda: False)
    assert not models.may_spend("codex/default", kind="owner", origin="auto")  # 后台：和 ChatGPT 套餐同一个同意
    monkeypatch.setattr(chatgpt_plan, "background_allowed", lambda: True)
    assert models.may_spend("codex/default", kind="owner", origin="auto")
    cx.cfg(model="codex/default", fallbacks=["codex/default", "deepseek/deepseek-flash"], use={"card": "codex/default", "doorman": True, "json": "codex/default"})
    assert models.outside("codex/default") == "deepseek/deepseek-flash" and models.outside() == "deepseek/deepseek-flash"
    assert models.route("card") != "codex/default" and models.route("doorman") == "deepseek/deepseek-flash"
    assert models.route("json", kind="owner", origin="user") == "deepseek/deepseek-flash"  # 一次性的那几件不接（只会当外人跑）
    assert models.fallbacks() == ["deepseek/deepseek-flash"]  # 不进链
    outsider = SimpleNamespace(kind="outsider", model="deepseek/deepseek-flash", fallbacks=["codex/default", "fake/m1"], origin="card", via=None)
    assert fallback.chain(outsider) == ["deepseek/deepseek-flash", "fake/m1"]
    from engine import openai_api
    monkeypatch.setattr(openai_api, "chosen", lambda agent: "codex/default")
    assert openai_api.model_for("main") == "deepseek/deepseek-flash"  # 对外接口不花它


def test_listed_only_while_signed_in(cx, monkeypatch):
    assert "codex/default" in models.models() and models.has_key("codex/default") and models.subscription_ready("codex/default")
    cx.cfg("unset")  # 不写 engine.codex = 关（新功能默认关），登录着也不列
    assert not models.subscription_ready("codex/default") and "codex/default" not in models.models() and provider_cli.offered() == []
    cx.cfg({"models": ["codex/default"], "enabled": False})
    assert not provider_cli.ready("codex/default")
    cx.cfg({"models": ["codex/default", "codex/gpt-6-codex"]})
    assert provider_cli.offered() == ["codex/default", "codex/gpt-6-codex"]
    cx.cfg()
    monkeypatch.setattr(runners, "_found", {})  # runners 在后台查到的：没登录
    assert provider_cli.login() is None and "codex/default" not in models.models() and not models.has_key("codex/default")
    monkeypatch.setattr(runners, "_found", {"codex": {"bin": "/x/codex", "auth": "chatgpt-plan", "billing": "subscription", "chatgpt_plan": True}})
    assert provider_cli.login() is None  # 借我们连着的 ChatGPT 套餐的那种只给后台任务
    monkeypatch.setattr(runners, "_found", {"codex": {"bin": str(cx.exe), "auth": "chatgpt", "billing": "subscription"}})
    cx.cfg(codex=False)
    assert not models.subscription_ready("codex/default") and "codex/default" not in models.models()
    # 跑之前现查（runners.detect：login status 的退出码）
    for said, want in (("Not logged in", None), ("Logged in using an API key - sk-***", "api key"), ("Logged in using ChatGPT", "chatgpt")):
        provider_cli._status.clear()  # noqa: SLF001
        monkeypatch.setenv("FAKE_CODEX_LOGIN", said)
        assert run(provider_cli.login_now()) == want, said


def in_turn(spec: Spec, model_id: str):
    """在一轮里（经 Picker）直接调 provider_cli.stream，把吐的事件收齐。"""
    async def go():
        tok = fallback._current.set(fallback.Picker(spec))  # noqa: SLF001
        try:
            return [ev async for ev in provider_cli.stream(provider_cli.stub("codex"), model_id, SimpleNamespace(system="", messages=[], timeout=30))]
        finally:
            fallback._current.reset(tok)  # noqa: SLF001
    return run(go())


def test_an_api_key_sign_in_is_not_the_plan(cx, monkeypatch):
    """codex 用 API 密钥登录的：按量记在用户自己的 OpenAI 账上，当成套餐的话花费上限看不到它。不列、不跑（那把密钥照常加成服务商）。"""
    monkeypatch.setattr(runners, "_found", {"codex": {"bin": str(cx.exe), "auth": "api key", "billing": "api_key"}})
    assert provider_cli.login() is None and not provider_cli.ready("codex/default") and provider_cli.offered() == []
    assert "codex/default" not in models.models() and not models.subscription_ready("codex/default")
    # 后台查到的还是 ChatGPT、刚换成密钥登录：跑之前那一次现查拦下，不起 exec
    monkeypatch.setattr(runners, "_found", {"codex": {"bin": str(cx.exe), "auth": "chatgpt", "billing": "subscription"}})
    monkeypatch.setenv("FAKE_CODEX_LOGIN", "Logged in using an API key - sk-***")
    provider_cli._status.clear()  # noqa: SLF001
    with pytest.raises(EngineError) as e:
        in_turn(owner_spec(), "default")
    assert e.value.kind == "config" and e.value.status == 401 and "API key" in str(e.value) and not cx.records("start")


def test_odd_model_names_are_still_the_plan(cx, monkeypatch):
    """codex/a/b、codex/-c… 也是 valid_ref：照样算套餐、照 may_spend 判（后台没同意就不能花）；型号名不对的不交给命令行。"""
    from engine import chatgpt_plan
    monkeypatch.setattr(chatgpt_plan, "background_allowed", lambda: False)
    for ref in ("codex/a/b", "codex/-cfoo=1"):
        assert models.valid_ref(ref) and models.subscription(ref) == ref.split("/")[0], ref
        assert not models.may_spend(ref, kind="owner", origin="auto") and not models.may_spend(ref, kind="outsider", origin="card"), ref
    cx.cfg({"models": ["codex/default", "codex/-cfoo=1", "codex/a/b"]})
    assert provider_cli.offered() == ["codex/default"]
    with pytest.raises(EngineError) as e:
        in_turn(owner_spec(model="codex/-cfoo=1"), "-cfoo=1")
    assert e.value.kind == "config" and not cx.records("start")


def test_the_transcript():
    msgs = [{"role": "user", "content": [{"type": "text", "text": "My cat is called Pixel."}], "at": "Mon 2026-10-19 08:00 UTC"},
            {"role": "assistant", "content": [{"type": "thinking", "text": "secret"}, {"type": "text", "text": "Noted."},
                                              {"type": "tool_call", "id": "c1", "name": "journal", "args": {}}]},
            {"role": "tool", "id": "c1", "name": "journal", "content": [{"type": "text", "text": "x" * 5000}], "error": False},
            {"role": "system", "content": [{"type": "text", "text": "The date changed."}]},
            {"role": "user", "content": [{"type": "text", "text": "Look at this"}, {"type": "image", "mime": "image/png", "data": "iVBORw0KGgo="}]}]
    text, pics = provider_cli.transcript("RULES", msgs)
    assert text.startswith("RULES\n\n") and "mcp__openmousse__" in text
    assert "User: [Mon 2026-10-19 08:00 UTC] My cat is called Pixel." in text and "You: Noted." in text and "(You called: journal)" in text
    assert "secret" not in text and ("x" * provider_cli.TOOL_RESULT_MAX + " …") in text and "x" * (provider_cli.TOOL_RESULT_MAX + 1) not in text
    assert "System note: The date changed." in text and text.rstrip().endswith("User: Look at this [image]")
    assert pics == [{"type": "image", "mime": "image/png", "data": "iVBORw0KGgo="}]


# —— 跑一轮 ——————————————————————————————————————————————————————————————————————

def test_an_owner_turn_runs_on_codex_with_our_tools(cx):
    cx.scenario([{"think": "Checking the day."}, {"tool": "schedule", "input": {"args": ["day"]}}, {"say": "You have a quiet day."}])
    evs: list[dict] = []

    async def go():
        async with serving(cx):
            return await loop.run_turn(owner_spec(), MemorySession(), user("what's on today?"), on_event=evs.append)
    res = run(go())
    assert res.status == "done" and res.text == "You have a quiet day." and res.models == ["codex/default"]
    assert res.usage["in"] == 200 and res.usage["cache_read"] == 1000 and res.usage["out"] == 40 and res.usage["cost"] is None
    start = cx.records("start")[0]
    argv = start["argv"]
    for flag in ("--json", "--ephemeral", "--skip-git-repo-check", "--ignore-user-config", "--ignore-rules", "features.shell_tool=false",
                 "features.hooks=false", 'approval_policy="never"', "mcp_servers.openmousse.tool_timeout_sec=330", 'web_search="disabled"',
                 'mcp_servers.openmousse.default_tools_approval_mode="approve"', "mcp_servers.openmousse.required=true"):
        assert flag in argv, flag
    assert argv[argv.index("-s") + 1] == "read-only" and "-m" not in argv and argv[-1] == "-"
    assert start["env"]["MOUSSE_TASK_MCP_TOKEN"] and not start["env"]["OPENAI_API_KEY"] and not start["env"]["OPENAI_BASE_URL"]
    assert start["cwd"].endswith("/data/codex/main")
    assert "You are Mousse, a fictional assistant." in start["stdin"] and "what's on today?" in start["stdin"]
    [call] = cx.records("mcp")
    assert call["response"]["result"]["content"][0]["text"] == "ok: schedule day" and cx.calls == [("schedule", ["day"], "main")]
    kinds = [e["type"] for e in evs]
    assert kinds.index("thinking") < kinds.index("tool_run") < kinds.index("tool_end") < kinds.index("text")
    assert next(e for e in evs if e["type"] == "tool_end")["status"] == "ok"
    assert mcp_engine._grants == {} and not cx.records("denied")  # 这一次的令牌收回了；没有被它自己拒掉的调用  # noqa: SLF001


def test_without_our_mcp_it_does_not_answer(cx):
    cx.scenario([{"say": "I answered without tools."}])  # /mcp 没起：required 的服务连不上，它根本不开始

    async def go():
        return await loop.run_turn(owner_spec(), MemorySession(), user("hi"))
    res = run(go())
    assert res.status == "error" and "required MCP servers failed" in (res.error or "") and "I answered" not in (res.text or "")


def test_a_turn_that_cannot_spend_it_is_refused(cx):
    async def outsider():
        return await loop.run_turn(owner_spec(kind="outsider", tools=[], origin="card"), MemorySession(), user("hi"))
    with pytest.raises(EngineError) as e:
        run(outsider())
    assert e.value.kind == "rule"

    async def direct():  # 不在一轮里（没经 Picker）：一次都不跑
        return [ev async for ev in provider_cli.stream(provider_cli.stub("codex"), "default", SimpleNamespace(system="", messages=[], timeout=30))]
    with pytest.raises(EngineError) as e:
        run(direct())
    assert e.value.kind == "rule" and not cx.records("start")


def test_not_signed_in_or_out_of_quota_switches_to_the_fallback(cx, monkeypatch):
    cx.fm.push(Reply(text="Answered by the fallback."), Reply(text="Fallback again."))
    monkeypatch.setenv("FAKE_CODEX_LOGIN", "Not logged in")

    async def go():
        async with serving(cx):
            first = await loop.run_turn(owner_spec(fallbacks=["fake/m1"]), MemorySession(), user("hi"))
            started = bool(cx.records("start"))  # 没登录：根本没起 exec
            fallback.reset()
            provider_cli._status.clear()  # noqa: SLF001
            monkeypatch.setenv("FAKE_CODEX_LOGIN", "Logged in using ChatGPT")
            cx.scenario([{"fail": "You've hit your usage limit. Try again later. (429)"}])
            second = await loop.run_turn(owner_spec(fallbacks=["fake/m1"]), MemorySession(), user("hi again"))
            return first, started, second
    res, started, again = run(go())
    assert res.status == "done" and res.text == "Answered by the fallback." and res.models == ["codex/default", "fake/m1"] and not started
    assert again.text == "Fallback again.", (again.status, again.error, again.models)
    assert again.models == ["codex/default", "fake/m1"] and cx.records("start")
    err = provider_cli.failure("codex", "unexpected status 401 Unauthorized: Missing bearer")
    assert err.status == 401 and err.kind == "config" and fallback.reason_of(err) == "auth"
    assert fallback.reason_of(provider_cli.failure("codex", "usage limit reached")) == "rate_limit"
    assert provider_cli.failure("codex", "model gpt-x is not supported").status == 404


def test_stop_ends_the_process_and_takes_the_token_back(cx):
    cx.scenario([{"say": "Starting."}, {"sleep": 30}, {"say": "never"}])
    control = Control()

    async def go():
        async with serving(cx):
            task = asyncio.create_task(loop.run_turn(owner_spec(), MemorySession(), user("take your time"), control=control))
            await until(lambda: bool(cx.records("start")) and bool(mcp_engine._grants), 10)  # noqa: SLF001
            await asyncio.sleep(0.3)
            control.abort()
            return await asyncio.wait_for(task, 15)
    t0 = time.monotonic()
    res = run(go())
    assert res.status == "aborted" and time.monotonic() - t0 < 15 and "never" not in (res.text or "")
    assert mcp_engine._grants == {}  # noqa: SLF001
    for _ in range(50):
        if cx.records("signal"):
            break
        time.sleep(0.05)
    assert cx.records("signal")  # 整个进程组收到了 SIGTERM
