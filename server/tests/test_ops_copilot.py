"""第 7 批 13：copilot/… 当主人回合的模型（engine/provider_cli.py）。copilot 是 tests/fake_copilot.py（照 1.0.100 的样子吐 --output-format json
的会话事件，拿 --additional-mcp-config 那个文件真的回调我们的 /mcp）；/mcp 用 uvicorn 起在这一轮测试的事件循环里；备用的模型是假模型服务器。
不碰真的 Copilot、不连真的模型。"""
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

FAKE = Path(__file__).resolve().parent / "fake_copilot.py"
CONV = "copilot-test"


def run(coro):
    return asyncio.run(coro)


async def until(cond, timeout: float = 8.0) -> None:
    t0 = time.monotonic()
    while not cond():
        if time.monotonic() - t0 > timeout:
            raise AssertionError("timed out waiting")
        await asyncio.sleep(0.02)


@pytest.fixture
def cp(tmp_path, monkeypatch, server_json, request):
    """假的 copilot（engine.copilot.bin 指着它）+ 本机的 /mcp + 假的命令行工具 + 一个假的按量模型（备用链用）。"""
    import mcp_bridge  # noqa: F401 — Gate 认临时令牌要它
    from config import settings
    fm = FakeModelServer([]).start()
    request.addfinalizer(fm.stop)
    exe = tmp_path / "copilot"
    exe.write_text(f"#!{sys.executable}\n" + FAKE.read_text(encoding="utf8"), encoding="utf8")
    exe.chmod(0o755)
    st = SimpleNamespace(fm=fm, exe=exe, log=tmp_path / "copilot.log", calls=[], home=tmp_path / "copilot-home")

    def cfg(copilot=None, **engine) -> None:
        on = {"bin": str(exe)} if copilot is None else copilot
        server_json({"claw": {"kind": "builtin"}, "engine": {"model": "deepseek/deepseek-flash", "models": ["deepseek/deepseek-flash", "copilot/auto"],
                                                             "providers": {"fake": {"api": "openai", "url": fm.openai_url, "key_env": ""}},
                                                             **({} if on == "unset" else {"copilot": on}), **engine}})
    cfg()
    st.cfg = cfg
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setenv("FAKE_COPILOT_LOG", str(st.log))
    monkeypatch.setenv("COPILOT_HOME", str(st.home))
    for k, v in (("GH_TOKEN", "gho_fake"), ("GITHUB_TOKEN", "ghp_fake"), ("COPILOT_GITHUB_TOKEN", "github_pat_fake"),
                 ("COPILOT_PROVIDER_BASE_URL", "https://byok.invalid"), ("COPILOT_ALLOW_ALL", "true"), ("COPILOT_MODEL", "gpt-x"),
                 ("GITHUB_COPILOT_PROMPT_MODE_REPO_HOOKS", "true"), ("OTEL_EXPORTER_OTLP_ENDPOINT", "https://otel.invalid"),
                 ("OPENAI_API_KEY", "sk-fake-should-not-pass")):
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(loop, "MODEL_RETRIES", (0, 0))
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
        monkeypatch.setenv("FAKE_COPILOT_SCENARIO", json.dumps(steps))
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
    base = {"kind": "owner", "model": "copilot/auto", "system": "You are Mousse, a fictional assistant.", "tools": tools, "conversation": CONV,
            "agent": "main", "origin": "user", "gate": tools_mod.gate}
    return Spec(**{**base, **kw})


# —— 谁能花、什么时候有 ——————————————————————————————————————————————————————————

def test_copilot_is_the_users_plan_and_only_their_own_turns_spend_it(cp):
    assert models.subscription("copilot/auto") == "copilot" and models.subscription("copilot/claude-sonnet-5.5") == "copilot"
    assert models.valid_ref("copilot/auto") and models.known("copilot/auto") and models.subscription("copilot/rm -rf") is None
    assert models.may_spend("copilot/auto", kind="owner", origin="user") and models.may_spend("copilot/auto", kind="owner", origin="relay")
    assert models.may_spend("copilot/auto", kind="cyber", origin="user")  # 用户自己的赛博
    for kind, origin in (("owner", "api"), ("owner", "acp"), ("outsider", "card"), ("cyber", "butler"), ("owner", "cli")):
        assert not models.may_spend("copilot/auto", kind=kind, origin=origin), (kind, origin)
    assert not models.may_spend("copilot/auto", kind="owner", origin="auto")  # 后台：默认不行（engine.copilot.background）
    assert not models.may_spend("copilot/auto", kind="owner", origin="user", via="task")
    cp.cfg({"bin": str(cp.exe), "background": True})
    assert models.may_spend("copilot/auto", kind="owner", origin="auto")
    cp.cfg(model="copilot/auto", fallbacks=["copilot/auto", "deepseek/deepseek-flash"], use={"card": "copilot/auto", "json": "copilot/auto"})
    assert models.outside("copilot/auto") == "deepseek/deepseek-flash" and models.route("card") != "copilot/auto"
    assert models.route("json", kind="owner", origin="user") == "deepseek/deepseek-flash" and models.fallbacks() == ["deepseek/deepseek-flash"]
    outsider = SimpleNamespace(kind="outsider", model="deepseek/deepseek-flash", fallbacks=["copilot/auto", "fake/m1"], origin="card", via=None)
    assert fallback.chain(outsider) == ["deepseek/deepseek-flash", "fake/m1"]


def test_off_until_engine_copilot_is_set(cp, monkeypatch):
    assert "copilot/auto" in models.models() and models.has_key("copilot/auto") and provider_cli.ready("copilot/auto")
    cp.cfg("unset")  # 不写 = 关（新功能默认关）
    assert not provider_cli.ready("copilot/auto") and "copilot/auto" not in models.models() and not models.has_key("copilot/auto")
    cp.cfg(False)
    assert not provider_cli.ready("copilot/auto")
    cp.cfg({"bin": str(cp.exe), "enabled": False})
    assert not provider_cli.ready("copilot/auto")
    cp.cfg({"bin": str(cp.exe), "models": ["copilot/auto", "copilot/claude-sonnet-5.5", "deepseek/deepseek-flash"]})
    assert [m for m in provider_cli.offered() if m.startswith("copilot/")] == ["copilot/auto", "copilot/claude-sonnet-5.5"]
    monkeypatch.setenv("PATH", "/nonexistent")  # 没写 bin、PATH 里也没有：不列
    cp.cfg(True)
    assert provider_cli.bin_of("copilot") is None and "copilot/auto" not in models.models()
    cp.cfg({"bin": str(cp.exe.parent / "missing")})
    assert provider_cli.bin_of("copilot") is None


# —— 跑一轮 ——————————————————————————————————————————————————————————————————————

def test_an_owner_turn_runs_on_copilot_with_only_our_tools(cp):
    cp.scenario([{"think": "Checking the day."}, {"tool": "schedule", "input": {"args": ["day"]}}, {"tool": "ask_user"},
                 {"say": "You have a quiet day."}])
    evs: list[dict] = []
    session = MemorySession([user("Hello, I'm Robin (fictional)."), {"role": "assistant", "content": [{"type": "text", "text": "Hi Robin."}]}])

    async def go():
        async with serving(cp):
            return await loop.run_turn(owner_spec(), session, user("what's on today?"), on_event=evs.append)
    res = run(go())
    assert res.status == "done" and res.text == "You have a quiet day." and res.models == ["copilot/auto"]
    assert res.usage["in"] == 500 and res.usage["cache_read"] == 1000 and res.usage["out"] == 30 and res.usage["cost"] is None
    start = cp.records("start")[0]
    argv = start["argv"]
    for flag in ("--allow-all-tools", "--allow-tool=openmousse", "--no-ask-user", "--no-custom-instructions", "--disable-builtin-mcps",
                 "--no-auto-update", "--no-remote-export", "--disallow-temp-dir", "--disable-client-telemetry", "--trust-folder",
                 "--deny-tool=shell", "--deny-tool=write", "--deny-tool=read", "--deny-tool=url", "--deny-tool=memory"):
        assert flag in argv, flag
    assert argv[argv.index("--output-format") + 1] == "json" and argv[argv.index("--model") + 1] == "auto"
    shown = next(a for a in argv if a.startswith("--available-tools=")).split("=", 1)[1].split(",")
    assert "openmousse-schedule" in shown and "openmousse-ask_user" not in shown and all(t.startswith("openmousse-") for t in shown)
    assert "-p" not in argv and not any("what's on today" in a for a in argv)  # 记录从标准输入给，不进命令行
    assert "You are Mousse, a fictional assistant." in start["stdin"] and "User: Hello, I'm Robin (fictional)." in start["stdin"]
    assert "You: Hi Robin." in start["stdin"] and "what's on today?" in start["stdin"] and "openmousse-…" in start["stdin"]
    env = start["env"]
    for k in ("GH_TOKEN", "GITHUB_TOKEN", "COPILOT_GITHUB_TOKEN", "COPILOT_PROVIDER_BASE_URL", "COPILOT_PROVIDER_API_KEY", "COPILOT_ALLOW_ALL",
              "COPILOT_MODEL", "GITHUB_COPILOT_PROMPT_MODE_REPO_HOOKS", "OTEL_EXPORTER_OTLP_ENDPOINT", "OPENAI_API_KEY", "MOUSSE_TASK_MCP_TOKEN"):
        assert not env[k], k
    assert env["COPILOT_HOME"] and env["COPILOT_AUTO_UPDATE"]  # 登录在哪留着
    assert start["cwd"].endswith("/data/copilot/main") and start["settings"] == {"disableAllHooks": True} and start["mcp_mode"] == "0o600"
    [call] = cp.records("mcp")
    assert call["response"]["result"]["content"][0]["text"] == "ok: schedule day" and cp.calls == [("schedule", ["day"], "main")]
    assert [r["name"] for r in cp.records("hidden")] == ["openmousse-ask_user"]  # 没给它的工具它看不见
    kinds = [e["type"] for e in evs]
    assert kinds.index("thinking") < kinds.index("tool_run") < kinds.index("tool_end") < kinds.index("text")
    assert next(e for e in evs if e["type"] == "tool_end")["status"] == "ok" and next(e for e in evs if e["type"] == "tool_run")["name"] == "schedule"
    assert mcp_engine._grants == {}  # 这一次的令牌收回了  # noqa: SLF001
    work = Path(start["cwd"])
    assert not list(work.glob("openmousse-mcp-*.json"))  # 写着令牌的 MCP 配置跑完就删了
    sid = argv[argv.index("--session-id") + 1]
    assert not (cp.home / "session-state" / sid).exists() and not (cp.home / "session-state" / ".session-operation-locks" / f"{sid}.lock").exists()
    assert (cp.home / "session-state").is_dir()  # 只删我们那一份


def test_pictures_go_in_as_attachments_and_are_removed(cp):
    cp.scenario([{"say": "A cat."}])

    async def go():
        async with serving(cp):
            msg = user("what is this?", [{"type": "image", "mime": "image/png", "data": "iVBORw0KGgo="}])
            return await loop.run_turn(owner_spec(model="copilot/claude-sonnet-5.5"), MemorySession(), msg)
    res = run(go())
    assert res.status == "done" and res.text == "A cat."
    start = cp.records("start")[0]
    assert start["argv"][start["argv"].index("--model") + 1] == "claude-sonnet-5.5"
    [(path, existed)] = start["attachments"].items()
    assert existed and path.endswith(".png") and not Path(path).exists()


def test_a_turn_that_cannot_spend_it_is_refused(cp):
    async def background():
        return await loop.run_turn(owner_spec(origin="auto"), MemorySession(), user("hi"))
    with pytest.raises(EngineError) as e:
        run(background())
    assert e.value.kind == "rule"

    async def direct():  # 不在一轮里（没经 Picker）：一次都不跑
        return [ev async for ev in provider_cli.stream(provider_cli.stub("copilot"), "auto", SimpleNamespace(system="", messages=[], timeout=30))]
    with pytest.raises(EngineError) as e:
        run(direct())
    assert e.value.kind == "rule" and not cp.records("start")


def test_not_signed_in_or_out_of_credits_switches_to_the_fallback(cp, monkeypatch):
    cp.fm.push(Reply(text="Answered by the fallback."), Reply(text="Fallback again."))
    monkeypatch.setenv("FAKE_COPILOT_LOGIN", "none")

    async def go():
        async with serving(cp):
            first = await loop.run_turn(owner_spec(fallbacks=["fake/m1"]), MemorySession(), user("hi"))
            fallback.reset()
            monkeypatch.delenv("FAKE_COPILOT_LOGIN")
            cp.scenario([{"error": "quota", "message": "You have used all of your AI credits for this month.", "status": 402}])
            second = await loop.run_turn(owner_spec(fallbacks=["fake/m1"]), MemorySession(), user("hi again"))
            return first, second
    res, again = run(go())
    assert res.status == "done" and res.text == "Answered by the fallback." and res.models == ["copilot/auto", "fake/m1"]
    assert again.text == "Fallback again." and again.models == ["copilot/auto", "fake/m1"] and cp.records("start")
    err = provider_cli.failure("copilot", "Error: No authentication information found.")
    assert err.status == 401 and err.kind == "config" and fallback.reason_of(err) == "auth"
    assert fallback.reason_of(provider_cli.copilot_failure("rate_limit", "slow down")) == "rate_limit"
    assert fallback.reason_of(provider_cli.copilot_failure("policy", "Copilot CLI is disabled by your organization")) == "auth"
    assert provider_cli.copilot_failure("authorization", "no Copilot seat").status == 401
    assert fallback.reason_of(provider_cli.copilot_failure("context_limit", "prompt too long")) == "context_overflow"
    assert provider_cli.copilot_failure("unknown", "model gpt-x is not available").status == 404


def test_stop_ends_the_process_and_takes_the_token_back(cp):
    cp.scenario([{"say": "Starting."}, {"sleep": 30}, {"say": "never"}])
    control = Control()

    async def go():
        async with serving(cp):
            task = asyncio.create_task(loop.run_turn(owner_spec(), MemorySession(), user("take your time"), control=control))
            await until(lambda: bool(cp.records("start")) and bool(mcp_engine._grants), 10)  # noqa: SLF001
            await asyncio.sleep(0.3)
            control.abort()
            return await asyncio.wait_for(task, 15)
    t0 = time.monotonic()
    res = run(go())
    assert res.status == "aborted" and time.monotonic() - t0 < 15 and "never" not in (res.text or "")
    assert mcp_engine._grants == {}  # noqa: SLF001
    for _ in range(50):
        if cp.records("signal"):
            break
        time.sleep(0.05)
    assert cp.records("signal")  # 整个进程组收到了 SIGTERM
    start = cp.records("start")[0]
    assert not (cp.home / "session-state" / start["argv"][start["argv"].index("--session-id") + 1]).exists()
