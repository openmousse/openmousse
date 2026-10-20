"""第 5 批第 5 项：MCP 补全（engine/mcp_client.py、../mcp_ctl.py）。测试里用官方 mcp 包的 FastMCP 起小 stdio 服务（sys.executable 跑），
模型是假的，不连网。prompts 两个只读工具（服务没有 prompts 就不给）；sampling 没开回拒绝、开了是零工具的外人回合、限速、不花套餐、记进用量；
mousse mcp add / remove / enable / disable / probe 改 server.json 前有备份；设置接口只认主人的设备。"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import config
import engine
from engine import events, mcp_client, providers
from engine import tools as tools_mod
from test_engine import FakeModel

pytest.importorskip("mcp")

SERVER = '''
from mcp.server.fastmcp import Context, FastMCP
from mcp.types import SamplingMessage, TextContent, ToolAnnotations

mcp = FastMCP("more")
RO = ToolAnnotations(readOnlyHint=True)


@mcp.tool(annotations=RO)
def echo(text: str) -> str:
    """Echo the text back."""
    return "echo: " + text


@mcp.tool(annotations=RO)
async def ask_llm(question: str, ctx: Context) -> str:
    """Ask the client's model a question (MCP sampling)."""
    res = await ctx.session.create_message(messages=[SamplingMessage(role="user", content=TextContent(type="text", text=question))],
                                           max_tokens=5000, system_prompt="Answer in five words.")
    return f"model said: {res.content.text} (model {res.model}, stop {res.stopReason})"


@mcp.prompt()
def greeting(name: str) -> str:
    """Greet someone by name."""
    return f"Please greet {name} warmly."


@mcp.prompt()
def code_review(code: str, style: str = "short") -> list:
    """Review a piece of code."""
    from mcp.server.fastmcp.prompts.base import AssistantMessage, UserMessage
    return [UserMessage(f"Review this ({style}):\\n{code}"), AssistantMessage("Sure, looking now.")]


if __name__ == "__main__":
    mcp.run()
'''

PLAIN = '''
from mcp.server.fastmcp import FastMCP
mcp = FastMCP("plain")


@mcp.tool()
def get_time() -> str:
    """The time."""
    return "noon"


if __name__ == "__main__":
    mcp.run()
'''


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(scope="module")
def scripts(tmp_path_factory) -> dict:
    d = tmp_path_factory.mktemp("mcp-more")
    (d / "more_server.py").write_text(SERVER, encoding="utf8")
    (d / "plain_server.py").write_text(PLAIN, encoding="utf8")
    return {"more": d / "more_server.py", "plain": d / "plain_server.py"}


@pytest.fixture
def env(server_json, tmp_path, scripts, monkeypatch):
    mcp_client.reset()
    monkeypatch.setattr(config.settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(mcp_client, "_rates", {})
    base = {"claw": {"kind": "builtin"}, "auth": {"tokens": {"phone": "tok-phone", "mcp": "tok-mcp"}},
            "engine": {"model": "deepseek/deepseek-flash"}}

    def cfg(more: dict | None = None, plain: dict | None = None, **extra) -> None:
        servers = {"more": {"command": sys.executable, "args": [str(scripts["more"])], **(more or {})},
                   "plain": {"command": sys.executable, "args": [str(scripts["plain"])], **(plain or {})}}
        server_json({**base, "mcp_servers": servers, **extra})
    cfg()
    yield type("Env", (), {"cfg": staticmethod(cfg), "base": base, "scripts": scripts, "dir": tmp_path})
    for s in list(mcp_client._servers.values()):
        if getattr(s, "proc", None) is not None:
            mcp_client._signal_group(s.proc.pid, 9)
    mcp_client.reset()


def ctx(agent: str = "main") -> engine.ToolCtx:
    return engine.ToolCtx(turn="t-mcp", kind="owner", agent=agent, conversation=agent, call_id="1", control=engine.Control())


async def mine(agent: str = "main") -> dict:
    await mcp_client.ready(agent)
    return {t.name: t for t in tools_mod.for_agent(agent)}


# —— prompts ——————————————————————————————————————————————————————————————————————

def test_prompts_tools_only_when_the_server_has_prompts(env):
    async def go():
        t = await mine()
        assert "more__prompts_list" in t and "more__prompts_get" in t
        assert "plain__prompts_list" not in t and "plain__prompts_get" not in t  # 没有 prompts 的不给（FastMCP 什么都宣称，按列出来的算）
        assert t["more__prompts_list"].level({})[0] == tools_mod.READ and t["more__prompts_get"].level({"name": "x"})[0] == tools_mod.READ
        listing = await t["more__prompts_list"].run({}, ctx())
        assert "greeting" in listing and "code_review" in listing and "name*" in listing and "style" in listing
        got = await t["more__prompts_get"].run({"name": "code_review", "arguments": {"code": "print(1)"}}, ctx())
        assert "[user]" in got and "Review this (short):" in got and "[assistant]" in got and got.startswith("<untrusted_tool_result")
        with pytest.raises(engine.ToolFailure):
            await t["more__prompts_get"].run({"name": "greeting"}, ctx())  # 少了必填的参数：服务回错
        st = {s["name"]: s for s in mcp_client.status()}
        assert st["more"]["prompts"] is True and st["plain"]["prompts"] is False
        await mcp_client.shutdown()
    run(go())
    env.cfg(more={"prompts": False})

    async def off():
        t = await mine()
        assert "more__prompts_list" not in t
        await mcp_client.shutdown()
    run(off())


# —— sampling ——————————————————————————————————————————————————————————————————————

def test_sampling_is_refused_unless_configured(env, monkeypatch):
    f = FakeModel([{"text": "should not be asked"}])
    monkeypatch.setattr(providers, "stream", f.stream)

    async def go():
        t = await mine()
        with pytest.raises(engine.ToolFailure, match="[Ss]ampling not supported"):
            await t["more__ask_llm"].run({"question": "hi?"}, ctx())
        await mcp_client.shutdown()
    run(go())
    assert f.requests == []


def test_sampling_is_a_zero_tool_outsider_turn_with_a_rate_limit(env, monkeypatch):
    f = FakeModel([{"text": "Hello from the model"}, {"text": "Second answer here"}])
    monkeypatch.setattr(providers, "stream", f.stream)
    env.cfg(more={"sampling": {"model": "deepseek/deepseek-flash", "max_tokens": 64, "max_rpm": 2}})

    async def go():
        t = await mine()
        out = [await t["more__ask_llm"].run({"question": f"question {i}?"}, ctx()) for i in range(2)]
        with pytest.raises(engine.ToolFailure, match="rate limit"):
            await t["more__ask_llm"].run({"question": "third?"}, ctx())
        await mcp_client.shutdown()
        return out
    out = run(go())
    assert "model said: Hello from the model (model deepseek/deepseek-flash" in out[0] and "Second answer here" in out[1]
    ref, req = f.requests[0]
    assert ref == "deepseek/deepseek-flash" and req.tools in (None, []) and "Answer in five words." in req.system
    assert req.max_tokens == 64  # 服务要 5000，配的上限是 64
    turns = events.turns("mcp-sampling:more")
    assert turns and turns[0]["kind"] == "outsider" and turns[0]["origin"] == "mcp-sampling"  # 零工具的外人回合，记进用量


def test_sampling_never_spends_the_plan(env, monkeypatch):
    f = FakeModel([{"text": "nope"}])
    monkeypatch.setattr(providers, "stream", f.stream)
    env.cfg(more={"sampling": {"model": "claude-code/opus"}}, engine={"model": "claude-code/opus"})

    async def go():
        t = await mine()
        with pytest.raises(engine.ToolFailure, match="plan"):
            await t["more__ask_llm"].run({"question": "hi?"}, ctx())
        await mcp_client.shutdown()
    run(go())
    assert f.requests == []


# —— 管理：命令行、设置接口 ——————————————————————————————————————————————————————————————

def cli(*argv) -> int:
    import mcp_ctl
    ap = argparse.ArgumentParser()
    mcp_ctl.register(ap.add_subparsers(dest="cmd"))
    a = ap.parse_args(["mcp", *argv])
    return a.run(a)


def backups(env) -> list[str]:
    return sorted(p.name for p in Path(config.CONFIG_PATH).parent.glob("server.json.bak-*"))


def conf() -> dict:
    return json.loads(Path(config.CONFIG_PATH).read_text(encoding="utf8"))


def test_cli_add_probe_enable_disable_remove(env, capsys, monkeypatch):
    env.cfg()
    before = backups(env)
    assert cli("add", "extra", "--command", sys.executable, "--arg", str(env.scripts["plain"]), "--agent", "main", "--agent", "g-x") == 0
    out = capsys.readouterr().out
    assert "get_time" in out  # 加之前真连了一次
    c = conf()["mcp_servers"]["extra"]
    assert c["command"] == sys.executable and c["args"] == [str(env.scripts["plain"])] and c["agents"] == ["main", "g-x"]
    assert len(backups(env)) == len(before) + 1  # 写之前备份
    assert cli("add", "extra", "--command", sys.executable) == 1  # 同名的不盖
    assert cli("add", "leaky", "--command", sys.executable, "--env", "API_KEY=sk-literal-value", "--no-probe") == 1  # 密钥要写成 ${…}
    assert "leaky" not in conf()["mcp_servers"]
    assert cli("add", "broken", "--command", "/nonexistent/mcp-server") == 1 and "broken" not in conf()["mcp_servers"]  # 连不上：不存
    assert cli("add", "broken", "--command", "/nonexistent/mcp-server", "--save-anyway") == 0 and conf()["mcp_servers"]["broken"]["disabled"] is True
    assert cli("disable", "extra") == 0 and conf()["mcp_servers"]["extra"]["disabled"] is True
    assert cli("enable", "extra") == 0 and "disabled" not in conf()["mcp_servers"]["extra"]
    capsys.readouterr()
    assert cli("probe", "more") == 0 and "ask_llm" in capsys.readouterr().out
    assert cli("probe", "broken") == 1
    assert cli("list") == 0
    listing = capsys.readouterr().out
    assert "extra" in listing and "broken" in listing and "disabled" in listing
    assert cli("remove", "extra") == 0 and "extra" not in conf()["mcp_servers"]
    assert cli("remove", "nope") == 1


def test_setup_api_is_owner_only_and_backs_up(env):
    import main
    c = TestClient(main.app)
    H = {"Authorization": "Bearer tok-phone"}
    assert c.get("/api/setup/mcp", headers={"Authorization": "Bearer tok-mcp"}).status_code == 403
    listing = c.get("/api/setup/mcp", headers=H).json()
    assert {s["name"] for s in listing["servers"]} == {"more", "plain"}
    r = c.put("/api/setup/mcp/notes", headers=H, json={"command": sys.executable, "args": [str(env.scripts["plain"])], "tools": {"*": "ask"}})
    body = r.json()
    assert r.status_code == 200 and body["changed"] and body["backup"] and conf()["mcp_servers"]["notes"]["tools"] == {"*": "ask"}
    bad = c.put("/api/setup/mcp/leaky", headers=H, json={"url": "https://mcp.example.com/mcp", "headers": {"Authorization": "Bearer literal"}})
    assert bad.status_code == 400 and "${" in bad.json()["error"] and "literal" not in bad.text
    assert c.put("/api/setup/mcp/a__b", headers=H, json={"command": "x"}).status_code == 400
    got = c.get("/api/setup/mcp/notes", headers=H).json()
    assert got["config"]["command"] == sys.executable and got["status"]["name"] == "notes"
    raw = conf()
    raw["mcp_servers"]["plain"]["env"] = {"API_KEY": "sk-handwritten-value", "MODE": "fast"}  # 手写进 server.json 的：读回来打码
    Path(config.CONFIG_PATH).write_text(json.dumps(raw), encoding="utf8")
    shown = c.get("/api/setup/mcp/plain", headers=H)
    assert shown.json()["config"]["env"] == {"API_KEY": "***", "MODE": "fast"} and "sk-handwritten" not in shown.text
    p = c.post("/api/setup/mcp/notes/probe", headers=H).json()
    assert p["ok"] and [t["name"] for t in p["tools"]] == ["get_time"]
    d = c.delete("/api/setup/mcp/notes", headers=H).json()
    assert d["ok"] and d["changed"] and "notes" not in conf()["mcp_servers"]
    assert c.delete("/api/setup/mcp/notes", headers=H).status_code == 404
