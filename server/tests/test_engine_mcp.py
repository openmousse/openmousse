"""内核第 13 步 B：通用 MCP 客户端（engine/mcp_client.py）。测试里用官方 mcp 包的 FastMCP 写一个小 stdio 服务（sys.executable 起），
不连网：起、列工具、调、超时（发 notifications/cancelled）、用户点停、崩了退避重启、闲置停、服务器关时连进程组一起收、环境不漏、
ask 出卡（kind tool）、off 不给、不在名单里的 Agent 看不到、赛博 / 外人回合没有、resources 两个只读工具、远端（固定请求头）。"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
import time
from pathlib import Path

import pytest

import engine
from engine import loop, mcp_client, tools
from engine.session import MemorySession, text_of, user
from test_engine import FakeModel

pytest.importorskip("mcp")

SERVER = '''
import asyncio, os, subprocess, sys
from pathlib import Path
from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations

mcp = FastMCP("demo")
MARK = Path(os.environ["MARK_DIR"])
RO = ToolAnnotations(readOnlyHint=True)


@mcp.tool(annotations=RO)
def echo(text: str) -> str:
    """Echo the text back."""
    return "echo: " + text


@mcp.tool(annotations=RO)
def env_names() -> str:
    """Names of the environment variables this server sees."""
    return ",".join(sorted(os.environ))


@mcp.tool(annotations=RO)
def show_env(name: str) -> str:
    """One environment variable."""
    return os.environ.get(name, "<unset>")


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
def wipe(target: str) -> str:
    """Delete everything in target. It can't be undone."""
    (MARK / "wiped").write_text(target)
    return "wiped " + target


@mcp.tool()
def rename_things(old: str, new: str) -> str:
    """Rename a thing (no hints: counts as a write)."""
    return f"renamed {old} to {new}"


@mcp.tool()
def get_weather(city: str) -> str:
    """Weather for a city (no hints, but the name says it reads)."""
    return "sunny in " + city


@mcp.tool(annotations=RO)
async def slow(seconds: float) -> str:
    """Sleep for a while."""
    try:
        await asyncio.sleep(seconds)
    except BaseException:
        (MARK / "cancelled").write_text("yes")
        raise
    return "slept"


@mcp.tool(annotations=RO)
def crash() -> str:
    """Exit hard."""
    os._exit(3)


@mcp.tool(annotations=RO)
def pid() -> str:
    """This process's id."""
    return str(os.getpid())


@mcp.tool(annotations=RO)
def spawn() -> str:
    """Start a long-running child process (same process group) and return its pid."""
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    return str(p.pid)


@mcp.tool(annotations=RO)
def big() -> str:
    """A very long result."""
    return "x" * 50000


@mcp.tool(annotations=RO)
async def grow(ctx: Context) -> str:
    """Add a tool and tell the client the list changed."""
    def extra(x: str) -> str:
        """An extra tool."""
        return "extra " + x
    mcp.add_tool(extra, name="extra", annotations=RO)
    await ctx.session.send_tool_list_changed()
    return "grown"


@mcp.resource("note://hello", name="hello", description="A note")
def hello() -> str:
    return "hello note"


if __name__ == "__main__":
    mcp.run()
'''

LONG = "a sentence long enough to be wrapped as outside content"


def run(coro):
    return asyncio.run(coro)


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:  # 僵尸（父进程还没收）也算没了
        return Path(f"/proc/{pid}/stat").read_text().split()[2] != "Z"
    except OSError:
        return True


@pytest.fixture(scope="module")
def script(tmp_path_factory) -> Path:
    p = tmp_path_factory.mktemp("mcp") / "demo_server.py"
    p.write_text(SERVER, encoding="utf8")
    return p


@pytest.fixture
def env(server_json, tmp_path, script, monkeypatch):
    """cfg(**demo 这一项多写的) 写 server.json；marks = 服务往里写记号的目录。"""
    from config import settings
    mcp_client.reset()
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(mcp_client, "BACKOFF_BASE", 0.2)
    marks = tmp_path / "marks"
    marks.mkdir()

    def cfg(extra: dict | None = None, **demo):
        entry = {"command": sys.executable, "args": [str(script)], "env": {"MARK_DIR": str(marks)}, **demo}
        server_json({"mcp_servers": {"demo": entry}, **(extra or {})})
    cfg()
    yield type("Env", (), {"cfg": staticmethod(cfg), "marks": marks})
    for s in list(mcp_client._servers.values()):  # 万一还有活着的：连进程组一起收
        if getattr(s, "proc", None) is not None:
            mcp_client._signal_group(s.proc.pid, 9)
    mcp_client.reset()


def ctx_for(agent="main", kind="owner", control=None) -> engine.ToolCtx:
    return engine.ToolCtx(turn="t", kind=kind, agent=agent, conversation=agent, call_id="1", control=control or engine.Control())


async def tool_named(name: str, agent: str = "main"):
    await mcp_client.ready(agent)
    return {t.name: t for t in tools.for_agent(agent)}.get(name)


# —— 配置 ————————————————————————————————————————————————————————————————

def test_config_rules(monkeypatch):
    monkeypatch.setenv("DEMO_TOKEN", "t-123")
    p = mcp_client.parse
    # Claude Code / Claude Desktop 的 mcpServers 原样能用
    c = p("files", {"command": "npx", "args": ["-y", "@example/files", "${HOME_DIR:-/srv}"], "env": {"API_TOKEN": "${DEMO_TOKEN}"}})
    assert c.ok and c.kind == "stdio" and c.args[-1] == "/srv" and c.env == {"API_TOKEN": "t-123"} and c.agents == ["main"]
    r = p("remote", {"type": "http", "url": "https://mcp.example.com/mcp", "headers": {"Authorization": "Bearer ${DEMO_TOKEN}"}, "timeout": 120000})
    assert r.ok and r.kind == "http" and r.headers == {"Authorization": "Bearer t-123"} and r.timeout == 120
    assert p("s", {"type": "streamable-http", "url": "https://x.example/mcp"}).kind == "http" and p("s", {"url": "https://x.example/mcp"}).kind == "http"
    assert p("s", {"type": "sse", "url": "https://x.example/sse"}).kind == "sse"
    # 密钥只能写成 ${变量名}
    assert "${" in p("bad", {"command": "x", "env": {"OPENAI_API_KEY": "sk-literal"}}).error
    assert "Authorization" in p("bad", {"url": "https://x.example/mcp", "headers": {"Authorization": "Bearer literal"}}).error
    assert p("ok", {"command": "x", "env": {"LOG_LEVEL": "debug"}}).ok  # 不像密钥的照写
    assert "NOPE_VAR" in p("bad", {"command": "x", "env": {"API_KEY": "${NOPE_VAR}"}}).error  # 取不到的变量：这个服务不起
    assert p("a__b", {"command": "x"}).error and p("a b", {"command": "x"}).error  # 服务名
    assert p("o", {"url": "https://x.example/mcp", "oauth": {"clientId": "c"}}).error  # OAuth 的远端走连接器
    assert p("w", {"type": "ws", "url": "wss://x.example"}).error
    assert not p("d", {"command": "x", "disabled": True}).ok and p("d", {"command": "x", "enabled": False}).disabled
    assert p("t", {"command": "x", "timeout": 30}).timeout == 30 and p("t", {"command": "x", "idle": 5}).idle == 5


def test_tools_are_listed_called_and_wrapped(env):
    env.cfg(tools={"rename_things": "auto"})

    async def go():
        await mcp_client.ready("main")
        mine = {t.name: t for t in tools.for_agent("main")}
        assert {"demo__echo", "demo__wipe", "demo__get_weather", "demo__resources_list", "demo__resources_read"} <= set(mine)
        echo = mine["demo__echo"]
        assert echo.kinds == ("owner",) and echo.description.startswith("[demo] Echo") and echo.level({})[0] == tools.READ
        assert mine["demo__wipe"].level({})[0] == tools.ASK and "user's OK first" in mine["demo__wipe"].description  # destructiveHint
        assert mine["demo__get_weather"].level({})[0] == tools.READ  # 什么都没说：照连接器按名字判断
        assert mine["demo__rename_things"].level({})[0] == tools.UNDO  # 设成 auto 的写
        out = await echo.run({"text": LONG}, ctx_for())
        assert out.startswith('<untrusted_tool_result source="demo__echo">') and "echo: " + LONG in out
        p1 = await mine["demo__pid"].run({}, ctx_for())
        p2 = await mine["demo__pid"].run({}, ctx_for())
        assert p1 == p2  # 一个服务一个常驻进程
        listing = await mine["demo__resources_list"].run({}, ctx_for())
        assert "note://hello" in listing
        assert "hello note" in await mine["demo__resources_read"].run({"uri": "note://hello"}, ctx_for())
        big = await mine["demo__big"].run({}, ctx_for())
        assert len(big) < 31000 and "omitted" in big  # 输出最多 30000 字
        with pytest.raises(engine.ToolFailure):
            await echo.run({"text": 5}, ctx_for())  # 参数照 schema 查
        st = {s["name"]: s for s in mcp_client.status()}["demo"]
        assert st["status"] == "running" and st["pid"] == int(p1)
        levels = {t["name"]: t["level"] for t in st["tools"]}
        assert levels["echo"] == "auto" and levels["wipe"] == "ask" and levels["rename_things"] == "auto" and st["resources"] is True
        assert "MARK_DIR" not in json.dumps(st)  # env、headers 不回
        await mcp_client.shutdown()
    run(go())


def test_the_server_does_not_see_the_servers_secrets(env, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-not-leak")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "should-not-leak")
    monkeypatch.setenv("DEMO_TOKEN", "t-123")
    env.cfg(env={"MARK_DIR": str(env.marks), "SERVICE_TOKEN": "${DEMO_TOKEN}"})

    async def go():
        names = (await (await tool_named("demo__env_names")).run({}, ctx_for())).split(",")
        token = await (await tool_named("demo__show_env")).run({"name": "SERVICE_TOKEN"}, ctx_for())
        await mcp_client.shutdown()
        return names, token
    names, token = run(go())
    assert "ANTHROPIC_API_KEY" not in names and "DEEPSEEK_API_KEY" not in names and "MOUSSE_SERVER_CONFIG" not in names
    assert "PATH" in names and "MARK_DIR" in names and "SERVICE_TOKEN" in names and token == "t-123"


def test_timeout_and_stop_send_cancelled(env):
    env.cfg(timeout=1)

    async def go():
        slow = await tool_named("demo__slow")
        t0 = time.monotonic()
        with pytest.raises(engine.ToolFailure) as e:
            await slow.run({"seconds": 10}, ctx_for())
        assert "1s" in str(e.value) and time.monotonic() - t0 < 5
        for _ in range(50):
            if (env.marks / "cancelled").exists():
                break
            await asyncio.sleep(0.1)
        assert (env.marks / "cancelled").exists()  # 超时：给服务发了 notifications/cancelled，它那边停了
        await mcp_client.shutdown()
    run(go())


def test_user_stop_cancels_the_call_on_the_server(env, monkeypatch):
    env.cfg(timeout=30)
    f = FakeModel([{"calls": [("c1", "demo__slow", {"seconds": 20})]}])
    from engine import providers
    monkeypatch.setattr(providers, "stream", f.stream)

    async def go():
        await mcp_client.ready("main")
        control = engine.Control()
        spec = engine.Spec(kind="owner", model="deepseek/x", tools=tools.for_agent("main"), agent="main", gate=tools.gate)

        async def stop_soon():
            await asyncio.sleep(1.5)
            control.abort()  # 用户点了停
        asyncio.ensure_future(stop_soon())
        res = await engine.run_turn(spec, MemorySession(), user("go"), control=control)
        assert res.status == "aborted" and "Stopped by the user" in text_of(res.messages[2])
        for _ in range(50):
            if (env.marks / "cancelled").exists():
                break
            await asyncio.sleep(0.1)
        assert (env.marks / "cancelled").exists()
        await mcp_client.shutdown()
    run(go())


def test_crash_restarts_after_backoff(env):
    async def go():
        crash = await tool_named("demo__crash")
        pid = await tool_named("demo__pid")
        first = await pid.run({}, ctx_for())
        with pytest.raises(engine.ToolFailure) as e:
            await crash.run({}, ctx_for())
        assert "demo" in str(e.value)
        await asyncio.sleep(0.3)
        st = {s["name"]: s for s in mcp_client.status()}["demo"]
        assert st["status"] == "error" and "3" in st["error"]  # 退出码 3
        second = await pid.run({}, ctx_for())  # 下一次用到：退避过了就重启
        assert second != first and {s["name"]: s for s in mcp_client.status()}["demo"]["restarts"] == 1
        await mcp_client.shutdown()
    run(go())


def test_two_calls_during_the_backoff_start_one_process(env):
    async def go():
        crash = await tool_named("demo__crash")
        with pytest.raises(engine.ToolFailure):
            await crash.run({}, ctx_for())
        await asyncio.sleep(0.05)
        s = mcp_client._servers["demo"]
        assert s.status == "error" and s.backoff_until > time.monotonic()  # 在退避
        a, b = await asyncio.gather(s.ensure(), s.ensure())  # 两个一起等退避过去
        assert a is b and s.restarts == 1 and s.status == "running"  # 只起了一个
        await mcp_client.shutdown()
        assert s.proc is None or s.proc.returncode is not None
    run(go())


def test_a_server_that_failed_does_not_hold_up_the_next_turn(env, monkeypatch):
    env.cfg(command="/no/such/command")
    from engine import Control

    async def go():
        t0 = time.monotonic()
        await mcp_client.ready("main")  # 第一次：等它起（起不来）
        assert {s["name"]: s for s in mcp_client.status()}["demo"]["status"] == "error"
        slow = []

        async def never(_wait=None):
            slow.append(1)
            await asyncio.sleep(30)
        monkeypatch.setattr(mcp_client._servers["demo"], "ensure", never)
        t1 = time.monotonic()
        await mcp_client.ready("main")  # 上次没起来：这一轮不等（后台再试）
        assert time.monotonic() - t1 < 1 and t1 - t0 < 10
        await asyncio.sleep(0)
        assert slow == [1]
        mcp_client._servers["demo"].status = "stopped"  # 当它是第一次用：要等，但用户点了停就不等了
        c = Control()
        asyncio.get_running_loop().call_later(0.2, c.aborted.set)
        t2 = time.monotonic()
        await mcp_client.ready("main", c)
        assert time.monotonic() - t2 < 2
        for t in list(mcp_client._bg):
            t.cancel()
    run(go())


def test_secrets_stay_out_of_the_status_and_errors(server_json, monkeypatch):
    mcp_client.reset()
    monkeypatch.setenv("EXAMPLE_PATH_KEY", "s3cr3t-path-key")
    server_json({"mcp_servers": {"zap": {"url": "https://mcp.example.invalid/s/${EXAMPLE_PATH_KEY}/mcp"}}})
    conf = mcp_client.configs()["zap"]
    assert "s3cr3t-path-key" in conf.url and conf.shown == "https://mcp.example.invalid/s/${EXAMPLE_PATH_KEY}/mcp"
    s = mcp_client.server(conf)
    s.status, s.error = "error", f"can't reach {conf.url}"
    st = mcp_client.status()[0]
    assert st["url"] == conf.shown and "s3cr3t-path-key" not in json.dumps(st) and "***" in st["error"]
    assert mcp_client.scrub(conf, f"GET {conf.url} failed") == "GET https://mcp.example.invalid/s/***/mcp failed"
    mcp_client.reset()


def test_tools_without_hints_ask_unless_the_name_only_reads():
    import apps
    conf = mcp_client.parse("x", {"command": "true"})

    def lv(name, ann=None):
        return mcp_client.level_of(conf, {"name": name, "annotations": ann or {}, "kind": apps.kind_of(name, ann or {}, {"x"})})
    assert [lv(n) for n in ("get_weather", "list_files", "search_notes", "listRunningJobs")] == ["auto"] * 4
    assert [lv(n) for n in ("search_and_replace", "get_or_create_issue", "getOrCreateIssue", "wipe")] == ["ask"] * 4
    assert lv("wipe", {"readOnlyHint": True}) == "auto" and lv("get_x", {"destructiveHint": True}) == "ask"


def test_idle_server_stops_and_comes_back(env):
    env.cfg(idle=1)

    async def go():
        pid_tool = await tool_named("demo__pid")
        pid = int(await pid_tool.run({}, ctx_for()))
        await asyncio.sleep(2.5)
        assert mcp_client._servers["demo"].status == "stopped" and not alive(pid)  # 闲置停了
        again = int(await pid_tool.run({}, ctx_for()))  # 用到再起（不算崩）
        assert again != pid and mcp_client._servers["demo"].crashes == 0
        await mcp_client.shutdown()
    run(go())


def test_shutdown_takes_the_whole_process_group(env):
    async def go():
        pid = int(await (await tool_named("demo__pid")).run({}, ctx_for()))
        child = int(await (await tool_named("demo__spawn")).run({}, ctx_for()))
        assert alive(pid) and alive(child)
        await mcp_client.shutdown()
        return pid, child
    pid, child = run(go())
    for _ in range(40):
        if not alive(child):
            break
        time.sleep(0.1)
    assert not alive(pid) and not alive(child)  # 它起的子进程也收了


def test_list_changed_refreshes_the_tools(env):
    async def go():
        grow = await tool_named("demo__grow")
        assert await tool_named("demo__extra") is None
        await grow.run({}, ctx_for())
        for _ in range(30):
            if "demo__extra" in {t.name for t in tools.for_agent("main")}:
                break
            await asyncio.sleep(0.1)
        extra = {t.name: t for t in tools.for_agent("main")}.get("demo__extra")
        assert extra is not None and "extra hi" in await extra.run({"x": "hi"}, ctx_for())
        await mcp_client.shutdown()
    run(go())
    cache = json.loads((mcp_client._cache_path()).read_text())
    assert "extra" in [t["name"] for t in cache["demo"]["tools"]]  # 工具表存下了：服务器重启以后不用先起进程


def test_who_gets_the_tools(env):
    env.cfg(agents=["main", "g-mcp"], tools={"rename_things": "off"})

    async def go():
        await mcp_client.ready("main")
        main = {t.name: t for t in tools.for_agent("main")}
        assert "demo__echo" in main and "demo__rename_things" not in main  # off：根本不给
        assert "demo__echo" in {t.name for t in tools.for_agent("g-mcp")}
        assert not any(t.name.startswith("demo__") for t in tools.for_agent("g-other"))  # 不在名单里的 Agent 看不到
        echo = main["demo__echo"]
        assert echo.kinds == ("owner",)  # 赛博、外人回合一个都不给
        assert "owner's own turns" in await tools.gate(echo, {"text": "x"}, ctx_for(kind="cyber"))
        await mcp_client.shutdown()
    run(go())
    from engine import cyber, cyber_tools
    # 赛博回合只有装载工具（和 manual）：MCP 的工具一个都不给（上面 kinds 只有 owner）；装载的 ask_agent 只给赛博回合，没装载就零工具
    ask = cyber_tools.ask_agent_tool({"id": "c", "name": "C"}, [{"id": "g-notes", "name": "Notes"}], thread="cy-0000abcd", ask=None)
    assert ask.kinds == ("cyber",) and "You have no tools" in cyber.system_prompt({"id": "c"})
    assert cyber_tools.ask_agent_tool({"id": "c", "name": "C"}, [], thread="cy-0000abcd", ask=None) is None
    assert cyber_tools.propose_tool(propose=None).kinds == ("cyber",)  # 提改法的也只给赛博回合


def test_sandboxed_agents_and_background_tasks_get_none(env):
    from engine import sandbox, tasks
    errand = sandbox.errand_id()
    env.cfg(agents=["main", errand])

    async def go():
        await mcp_client.ready(errand)
        assert mcp_client.status()[0]["status"] == "stopped"  # 在沙箱里干活的 Agent：写进 agents 里也不起、不给
        assert mcp_client.exposed(errand) == {} and not any(t.name.startswith("demo__") for t in tools.for_agent(errand))
        await mcp_client.ready("main")
        assert any(t.name == "demo__echo" for t in tools.for_agent("main"))
        child = tasks.child_tools({"id": "t-1", "agent": "main", "thread": "main", "depth": 1, "level": "undo"})
        assert not any(t.name.startswith("demo__") for t in child)  # 后台任务的子 Agent 问不了用户、也不经 /mcp 给外援：先不给
        await mcp_client.shutdown()
    run(go())


def test_ask_makes_a_tool_card_and_runs_once_when_approved(env, server_json, monkeypatch):
    import chat
    import inbox
    from engine import providers
    env.cfg(extra={"claw": {"kind": "builtin"}, "engine": {"model": "deepseek/deepseek-flash"}})
    tools.install_hooks()
    f = FakeModel([{"text": "Done, it was wiped."}])
    monkeypatch.setattr(providers, "stream", f.stream)

    async def go():
        wipe = await tool_named("demo__wipe")
        out = await tools.gate(wipe, {"target": "old-notes"}, ctx_for())
        assert isinstance(out, dict) and out["status"] == "approval-pending" and not (env.marks / "wiped").exists()  # 没跑
        again = await tools.gate(wipe, {"target": "old-notes"}, ctx_for())
        assert again["inbox"] == out["inbox"]  # 同一次调用：还是那张卡
        with chat._lock, inbox.idb() as conn:
            card = dict(conn.execute("SELECT * FROM inbox WHERE id=?", (out["inbox"],)).fetchone())
        assert card["kind"] == "tool" and card["title"] == "demo: wipe" and "old-notes" in card["detail"]  # 复用 kind tool 的卡
        res = await inbox.act(out["inbox"], inbox.ActIn(action="approve"))
        assert res["item"]["status"] == "done" and "wiped old-notes" in res["item"]["result"]
        assert (env.marks / "wiped").read_text() == "old-notes"
        with pytest.raises(Exception):  # 再点一次：卡已经不在等了
            await inbox.act(out["inbox"], inbox.ActIn(action="approve"))
        for _ in range(100):  # 结果当「做完了」发回 main，等那一轮跑完
            r = chat.RUNS.get("main")
            if r and r.done:
                break
            await asyncio.sleep(0.05)
        row = tools.call_of(out["inbox"])
        assert row["status"] == "done" and row["tool"] == "mcp:demo__wipe" and json.loads(row["args"])["arguments"] == {"target": "old-notes"}
        await mcp_client.shutdown()
    run(go())


def test_a_card_from_an_api_session_reports_back_there(env, monkeypatch):
    import inbox
    from engine import openai_api, store
    env.cfg(extra={"claw": {"kind": "builtin"}, "engine": {"model": "deepseek/deepseek-flash"}})
    tools.install_hooks()
    told: list[tuple] = []

    async def tell(conv, iid, text):
        told.append((conv, iid, text))
    monkeypatch.setattr(openai_api, "tell", tell)
    conv = "api:main:k1"
    with store.tx() as conn:
        conn.execute("INSERT INTO engine_turns(id, conversation, kind, agent, model, status, started_at) VALUES(?,?,?,?,?,?,?)",
                     ("t-api", conv, "owner", "main", "deepseek/deepseek-flash", "running", store.now_iso()))

    async def go():
        wipe = await tool_named("demo__wipe")
        ctx = engine.ToolCtx(turn="t-api", kind="owner", agent="main", conversation=conv, call_id="c1", control=engine.Control())
        out = await tools.gate(wipe, {"target": "old-notes"}, ctx)
        res = await inbox.act(out["inbox"], inbox.ActIn(action="approve"))
        assert res["item"]["status"] == "done" and (env.marks / "wiped").read_text() == "old-notes"
        assert [(c, i) for c, i, _ in told] == [(conv, out["inbox"])] and "wiped old-notes" in told[0][2]  # 结果交回那个 API 会话
        await mcp_client.shutdown()
    run(go())


def test_owner_turn_gets_the_tools_and_the_rules_mention_them(env, server_json, monkeypatch):
    from config import settings
    from engine import owner, providers
    env.cfg(extra={"claw": {"kind": "builtin"}, "engine": {"model": "deepseek/deepseek-flash"}})
    monkeypatch.setattr(settings, "workspace", env.marks)
    f = FakeModel([{"calls": [("c1", "demo__echo", {"text": LONG})]}, {"text": "It echoed."}])
    monkeypatch.setattr(providers, "stream", f.stream)

    async def go():
        out: list[str] = []
        res = await owner.respond("main", "2026-10-16", "echo something", None, out.append)
        await mcp_client.shutdown()
        return res
    res = run(go())
    assert res.text == "It echoed." and "echo: " + LONG in text_of(res.messages[2])
    req = f.requests[0][1]
    assert "demo__echo" in [t["name"] for t in req.tools] and "## MCP servers" in req.system and "from demo" in req.system


# —— 远端：固定请求头（apps.session_run 那一套）——————————————————————————————————————

def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


REMOTE = '''
import sys
from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations

app = FastMCP("remote-demo", host="127.0.0.1", port=int(sys.argv[1]), log_level="WARNING")


@app.tool(annotations=ToolAnnotations(readOnlyHint=True))
def whoami(ctx: Context) -> str:
    """Who is calling (the Authorization header)."""
    return "auth=" + str(ctx.request_context.request.headers.get("authorization"))


app.run(transport="streamable-http")
'''


@pytest.fixture
def remote(tmp_path):
    """本机另起一个进程跑 streamable HTTP 的 FastMCP，它的 whoami 工具回请求里的 Authorization。"""
    import subprocess
    port = free_port()
    script = tmp_path / "remote_server.py"
    script.write_text(REMOTE, encoding="utf8")
    proc = subprocess.Popen([sys.executable, str(script), str(port)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(200):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                break
        except OSError:
            time.sleep(0.05)
    yield f"http://127.0.0.1:{port}/mcp"
    proc.terminate()
    proc.wait(5)


def test_remote_server_with_a_fixed_token(env, server_json, remote, monkeypatch):
    monkeypatch.setenv("REMOTE_DEMO_TOKEN", "rt-42")
    server_json({"mcp_servers": {"far": {"type": "http", "url": remote, "headers": {"Authorization": "Bearer ${REMOTE_DEMO_TOKEN}"}}}})

    async def go():
        who = await tool_named("far__whoami")
        assert who is not None and who.level({})[0] == tools.READ, [s["error"] for s in mcp_client.status()]
        return await who.run({}, ctx_for())
    assert run(go()) == "auth=Bearer rt-42"
    st = {s["name"]: s for s in mcp_client.status()}["far"]
    assert st["type"] == "http" and st["status"] == "connected" and "rt-42" not in json.dumps(st)


def test_remote_uses_the_new_client_name_and_old_mcp_still_works(env, server_json, remote, monkeypatch):
    """apps._http_streams：新版 mcp 包走 streamable_http_client（不再报弃用警告）；老版本只有 streamablehttp_client 也照样连得上。"""
    import types as pytypes
    import warnings

    import apps
    from mcp.client import streamable_http
    monkeypatch.setenv("REMOTE_DEMO_TOKEN", "rt-42")
    server_json({"mcp_servers": {"far": {"type": "http", "url": remote, "headers": {"Authorization": "Bearer ${REMOTE_DEMO_TOKEN}"}}}})

    async def whoami():
        who = await tool_named("far__whoami")
        return await who.run({}, ctx_for())
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter("always")
        assert run(whoami()) == "auth=Bearer rt-42"
    assert not [w for w in seen if issubclass(w.category, DeprecationWarning) and "streamable_http_client" in str(w.message)]
    used = []

    def legacy(*a, **kw):
        used.append((kw.get("headers") or {}).get("Authorization"))
        return streamable_http.streamablehttp_client(*a, **kw)
    monkeypatch.setattr(apps, "streamable_http", pytypes.SimpleNamespace(streamablehttp_client=legacy))  # 老版本：没有新名字
    mcp_client.reset()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        assert run(whoami()) == "auth=Bearer rt-42"
    assert used and set(used) == {"Bearer rt-42"}


def test_status_route_and_no_config(server_json):
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    mcp_client.reset()
    server_json({})
    app = FastAPI()
    app.include_router(mcp_client.router)
    assert TestClient(app).get("/api/engine/mcp").json() == {"ok": True, "servers": []}
    run(mcp_client.ready("main"))  # 没配：什么都不做
    assert not mcp_client._servers and not [t for t in tools.for_agent("main") if "__" in t.name and getattr(t, "server", "")]
    server_json({"mcp_servers": {"bad": {"command": "x", "env": {"GITHUB_TOKEN": "ghp_literal"}}}})
    st = TestClient(app).get("/api/engine/mcp").json()["servers"][0]
    assert st["name"] == "bad" and st["status"] == "error" and "${" in st["error"] and "ghp_literal" not in json.dumps(st)


def test_loop_constants_untouched():
    assert loop.MAX_OUTPUT == 30000  # MCP 的输出上限和内核工具一样
