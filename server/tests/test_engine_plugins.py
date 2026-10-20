"""第 5 批第 8 项：带代码的插件（engine/plugins.py、../plugins_ctl.py）。插件是测试里现写的小文件夹，git 的那个是本机现建的仓库，不连网。
默认一个都不载；只给 manifest 声明了的（工具、钩子、技能、MCP）；工具只给主人回合，撤得回的直接调、别的交卡；载入有时限、出错只坏它自己；
能力多了要重新同意（交卡）；git 只认 40 位提交；扫描和 Python 静态检查只提醒；命令行、设置接口改 server.json 先备份。"""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import config
import engine
from engine import agent_skills, mcp_client, plugins, store
from engine import tools as tools_mod
from engine import user_hooks as U

PLUGIN_PY = '''
import json

CALLS = []


def forecast(args):
    CALLS.append(args)
    return {"city": args.get("city"), "sky": "clear"}


async def note(args):
    return "noted: " + args.get("text", "")


async def guard(payload):
    if payload.get("tool_name") == "host_exec":
        return {"decision": "block", "reason": "weather plugin says no"}
    return None


def register(ctx):
    ctx.tool("forecast", forecast, description="Weather forecast for a city.", schema={"type": "object", "properties": {"city": {"type": "string"}}})
    ctx.tool("note", note, description="Keep a weather note.", undoable=True)
    ctx.hook("tool:before", guard, matcher="host_exec")
    ctx.skills_dir("skills")
    ctx.mcp_server("weather-mcp", {"command": "weather-mcp-server", "args": ["--fast"]})
'''

MANIFEST = {"name": "weather", "version": "0.1.0", "description": "Weather tools for tests.",
            "capabilities": {"tools": [{"name": "forecast"}, {"name": "note", "undoable": True}], "hooks": ["tool:before"], "skills": "skills",
                             "mcp": ["weather-mcp"]}}


def run(coro):
    return asyncio.run(coro)


def write_plugin(root: Path, *, manifest: dict | None = None, code: str = PLUGIN_PY, name: str = "weather") -> Path:
    d = root / name
    (d / "skills" / "rain-check").mkdir(parents=True, exist_ok=True)
    (d / "skills" / "rain-check" / "SKILL.md").write_text("---\nname: rain-check\ndescription: Check whether it will rain.\n---\n\n# rain-check\n",
                                                          encoding="utf8")
    (d / plugins.MANIFEST).write_text(json.dumps(manifest or {**MANIFEST, "name": name}), encoding="utf8")
    (d / plugins.ENTRY).write_text(code, encoding="utf8")
    return d


@pytest.fixture
def home(tmp_path, monkeypatch, server_json):
    import sources
    ws = tmp_path / "ws-main"
    (ws / "memory").mkdir(parents=True)
    (ws / "skills").mkdir()
    monkeypatch.setattr(config.settings, "workspace", ws)
    monkeypatch.setattr(config.settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(config.settings, "profile", tmp_path / "no-profile.md")
    monkeypatch.setattr(sources, "memory_tree", None)
    base = {"claw": {"kind": "builtin"}, "auth": {"tokens": {"phone": "tok-phone", "mcp": "tok-mcp"}}, "engine": {"model": "deepseek/deepseek-flash"}}

    def cfg(**plug) -> None:
        server_json({**base, "engine": {**base["engine"], **({"plugins": plug} if plug else {})}})
    cfg()
    plugins.unload_all()
    src = tmp_path / "src"
    yield type("Home", (), {"cfg": staticmethod(cfg), "src": src, "base": base, "tmp": tmp_path})
    plugins.unload_all()


def installed(home, **kw) -> dict:
    write_plugin(home.src, **kw)
    st = plugins.stage(str(home.src / (kw.get("name") or "weather")))
    return plugins.install(st, by="test")


def ctx(agent: str = "main") -> engine.ToolCtx:
    return engine.ToolCtx(turn="t-plug", kind="owner", agent=agent, conversation=agent, call_id="c1", control=engine.Control())


def test_nothing_loads_unless_listed(home):
    installed(home)
    assert plugins.enabled() == [] and plugins.load_all() == {}
    assert not any(t.name.startswith("weather__") for t in tools_mod.for_agent("main"))
    assert not [h for h in U.PLUGINS if h.origin.startswith("plugin:")]
    home.cfg(enabled="weather")  # 不是列表：照样一个都不载
    assert plugins.enabled() == []


def test_declared_tools_hooks_skills_and_mcp(home):
    installed(home)
    home.cfg(enabled=["weather"])
    assert plugins.load_all()["weather"]["state"] == "loaded"
    got = {t.name: t for t in tools_mod.for_agent("main")}
    fc, nt = got["weather__forecast"], got["weather__note"]
    assert fc.kinds == ("owner",) and fc.level({})[0] == tools_mod.ASK and nt.level({})[0] == tools_mod.UNDO
    assert "[plugin weather]" in fc.description and "needs the user's OK" in fc.description
    assert not any(t.name.startswith("weather__") for t in tools_mod.for_agent("g-other"))  # 默认只给 main
    assert run(nt.run({"text": "umbrella"}, ctx())) == "noted: umbrella"  # 撤得回的直接调（长的结果包成资料，tools.wrap_untrusted）

    async def ask():
        res = await tools_mod.gate(fc, {"city": "Lisbon"}, ctx())
        assert isinstance(res, dict) and res["status"] == "approval-pending"  # 先问的：交卡
        text = await plugins._call_approved("weather__forecast", {"plugin": "weather", "tool": "forecast", "arguments": {"city": "Lisbon"}},
                                            {"agent": "main"})
        assert '"sky": "clear"' in text and "Lisbon" in text
    run(ask())
    out = run(U.fire("tool:before", {"event": "tool:before", "tool_name": "host_exec", "tool_input": {}}, agent="main", tool="host_exec"))
    assert out.blocked and "weather plugin says no" in out.reason  # 插件的钩子
    assert not run(U.fire("tool:before", {"event": "tool:before", "tool_name": "web", "tool_input": {}}, agent="main", tool="web")).blocked
    skills = agent_skills.skills_for("main")
    assert "rain-check" in skills and agent_skills.source_of(skills["rain-check"][1]) == "plugin"
    confs = mcp_client.configs()
    assert confs["weather-mcp"].command == "weather-mcp-server" and confs["weather-mcp"].args == ["--fast"]
    from engine.setup_api import SetupError
    with pytest.raises(SetupError) as e:
        mcp_client.delete_entry("weather-mcp")
    assert e.value.status == 404 and "plugin" in str(e.value)
    row = {r["name"]: r for r in plugins.status()}["weather"]
    assert row["state"] == "loaded" and row["consented"] and row["tools"] == ["weather__forecast", "weather__note"] and row["mcp"] == ["weather-mcp"]


def test_undeclared_capabilities_fail_the_whole_plugin(home):
    code = PLUGIN_PY.replace('    ctx.mcp_server("weather-mcp"', '    ctx.tool("secret", forecast)\n    ctx.mcp_server("weather-mcp"')
    installed(home, code=code)
    home.cfg(enabled=["weather"])
    st = plugins.load_all()["weather"]
    assert st["state"] == "error" and "secret" in st["error"]
    assert not any(t.name.startswith("weather__") for t in tools_mod.for_agent("main"))  # 前面登记的也作废
    assert not [h for h in U.PLUGINS if h.origin == "plugin:weather"] and "weather-mcp" not in mcp_client.configs()


def test_load_timeout_and_crashes_only_hurt_that_plugin(home):
    installed(home)
    installed(home, name="sleepy", manifest={"name": "sleepy", "capabilities": {}}, code="import time\n\ndef register(ctx):\n    time.sleep(5)\n")
    installed(home, name="broken", manifest={"name": "broken", "capabilities": {}}, code="raise RuntimeError('boom at import')\n")
    home.cfg(enabled=["sleepy", "broken", "weather"], load_timeout=0.5)
    t0 = time.monotonic()
    st = plugins.load_all()
    assert time.monotonic() - t0 < 4
    err = st["sleepy"]["error"]
    assert st["sleepy"]["state"] == "error" and ("within" in err or "没载完" in err)
    assert st["broken"]["state"] == "error" and "boom at import" in st["broken"]["error"]
    assert st["weather"]["state"] == "loaded"


def test_more_capabilities_need_a_new_ok_with_a_card(home):
    installed(home)
    home.cfg(enabled=["weather"])
    grown = json.loads(json.dumps(MANIFEST))
    grown["capabilities"]["hooks"].append("message:received")
    (config.settings.data_dir / "plugins" / "weather" / plugins.MANIFEST).write_text(json.dumps(grown), encoding="utf8")
    st = plugins.load("weather")
    assert st["state"] == "needs-consent" and st["extra"] == ["hook:message:received"]
    assert not any(t.name.startswith("weather__") for t in tools_mod.for_agent("main"))
    with store.read() as conn:
        row = conn.execute("SELECT inbox, args FROM engine_calls WHERE tool='plugin:weather' ORDER BY created_at DESC LIMIT 1").fetchone()
    assert row is not None and row["inbox"]
    import inbox
    card = inbox.item(row["inbox"])
    assert "no sandbox" in card["why"] and any("message:received" in c for c in card["changes"])
    args = json.loads(row["args"])
    msg = run(plugins._consent_approved("weather", args, {"agent": "main"}))
    assert "loaded" in msg and plugins._status["weather"]["state"] == "loaded"
    fewer = json.loads(json.dumps(MANIFEST))
    fewer["capabilities"]["hooks"] = []
    (config.settings.data_dir / "plugins" / "weather" / plugins.MANIFEST).write_text(json.dumps(fewer), encoding="utf8")
    assert plugins.consent_state("weather")[0]  # 能力少了不用重新同意


def test_scan_and_python_checks_only_warn(home):
    code = PLUGIN_PY + '\nimport os, subprocess\nKEY = os.environ.get("OPENAI_API_KEY")\nos.system("echo hi")\neval("1+1")\nCFG = "server.json"\n'
    write_plugin(home.src, code=code)
    st = plugins.stage(str(home.src / "weather"))
    rules = {f["rule"] for f in st.scan["findings"] if f.get("category") == "python"}
    assert {"environ", "shell", "dynamic_code", "secret_files"} <= rules
    assert plugins.install(st, by="test")["name"] == "weather"  # 只提醒，不拦


def test_bad_packages_are_refused(home):
    from engine import skill_market
    write_plugin(home.src)
    (home.src / "weather" / "evil-link").symlink_to("/etc/passwd")
    with pytest.raises(plugins.PluginError):
        plugins.stage(str(home.src / "weather"))
    (home.src / "weather" / "evil-link").unlink()
    (home.src / "weather" / "tool.so").write_bytes(b"\x7fELF\x02\x01\x01" + b"\x00" * 64)
    with pytest.raises(plugins.PluginError):
        plugins.stage(str(home.src / "weather"))
    assert skill_market  # 同一套上限、路径规矩
    for bad in ({"name": "Bad Name"}, {"name": "a__b"}, {"name": "ok", "capabilities": {"hooks": ["no:such"]}}):
        write_plugin(home.tmp / "bad", manifest=bad, name="ok")
        with pytest.raises(plugins.PluginError):
            plugins.stage(str(home.tmp / "bad" / "ok"))
    assert not list((config.settings.data_dir / "plugins" / ".staging").iterdir())  # 没装成的不留在暂存区


def git(*args, cwd) -> str:
    env = {"GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.com", "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.com",
           "PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(cwd)}
    return subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True).stdout.strip()


def test_git_needs_a_full_commit(home):
    repo = write_plugin(home.tmp / "gitsrc")
    git("init", "-q", "-b", "main", cwd=repo)
    git("add", "-A", cwd=repo)
    git("commit", "-q", "-m", "first", cwd=repo)
    sha = git("rev-parse", "HEAD", cwd=repo)
    git("tag", "v1", cwd=repo)
    st = plugins.stage(str(repo), sha)
    from engine import skill_market
    assert st.commit == sha and st.manifest.name == "weather" and ".git" not in {Path(p).parts[0] for p in skill_market.disk_files(st.root)}
    for ref in ("main", "v1", sha[:12], "HEAD"):
        with pytest.raises(plugins.PluginError):
            plugins.stage(str(repo), ref)
    with pytest.raises(plugins.PluginError):
        plugins.stage(str(repo), "0" * 40)  # 没有这个提交
    with pytest.raises(plugins.PluginError, match="40"):
        plugins.stage("https://git.example.com/weather.git")  # git 地址没写提交
    with pytest.raises(plugins.PluginError, match="[Nn]o such folder|没有这个文件夹"):
        plugins.stage(str(home.tmp / "nowhere"))
    res = plugins.install(st, by="test")
    assert plugins.lock()["weather"]["commit"] == sha and res["name"] == "weather"


def cli(*argv) -> int:
    import plugins_ctl
    ap = argparse.ArgumentParser()
    plugins_ctl.register(ap.add_subparsers(dest="cmd"))
    a = ap.parse_args(["plugins", *argv])
    return a.run(a)


def test_cli_install_enable_disable_remove(home, capsys):
    write_plugin(home.src)
    before = sorted(Path(config.CONFIG_PATH).parent.glob("server.json.bak-*"))
    assert cli("install", str(home.src / "weather")) == 1  # 不在终端里、没 --yes：不装
    assert "no sandbox" in capsys.readouterr().out
    assert cli("install", str(home.src / "weather"), "--yes", "--enable") == 0
    assert plugins.enabled() == ["weather"] and (config.settings.data_dir / "plugins" / "weather" / plugins.ENTRY).is_file()
    assert len(sorted(Path(config.CONFIG_PATH).parent.glob("server.json.bak-*"))) > len(before)
    assert cli("install", str(home.src / "weather"), "--yes") == 1  # 同名的不盖（--replace 才换）
    assert cli("install", str(home.src / "weather"), "--yes", "--replace") == 0
    capsys.readouterr()
    assert cli("list") == 0 and "weather" in capsys.readouterr().out
    assert cli("disable", "weather") == 0 and plugins.enabled() == []
    assert cli("remove", "weather") == 0 and not (config.settings.data_dir / "plugins" / "weather").exists()
    assert list((config.settings.data_dir / "plugins" / ".removed").iterdir())  # 留了一份
    assert cli("remove", "weather") == 1


def test_setup_api_is_owner_only(home):
    import main
    write_plugin(home.src)
    c = TestClient(main.app)
    H = {"Authorization": "Bearer tok-phone"}
    assert c.get("/api/setup/plugins", headers={"Authorization": "Bearer tok-mcp"}).status_code == 403
    pv = c.post("/api/setup/plugins/preview", headers=H, json={"source": str(home.src / "weather")}).json()
    assert pv["ok"] and pv["name"] == "weather" and "no sandbox" in pv["warning"] and any(x["id"] == "undoable:note" for x in pv["capabilities"])
    assert c.post("/api/setup/plugins/preview", headers=H, json={"source": "https://git.example.com/x.git", "commit": "main"}).status_code == 400
    got = c.post("/api/setup/plugins/install", headers=H, json={"staged": pv["staged"]}).json()
    assert got["ok"] and got["name"] == "weather"
    assert c.put("/api/setup/plugins/weather", headers=H, json={"enabled": True}).json()["enabled"] == ["weather"]
    listing = c.get("/api/setup/plugins", headers=H).json()
    assert listing["enabled"] == ["weather"] and listing["plugins"][0]["consented"] is True
    assert c.delete("/api/setup/plugins/weather", headers=H).json()["ok"]
    assert c.delete("/api/setup/plugins/weather", headers=H).status_code == 404


def test_default_server_start_loads_nothing(home):
    installed(home)
    run(plugins.start())
    assert plugins._status == {} and plugins._loaded == {}


def test_sys_exit_and_a_bad_manifest_only_hurt_that_plugin(home):
    from engine.loop import ToolFailure
    code = (PLUGIN_PY.replace("    CALLS.append(args)\n", "    import sys\n    sys.exit(2)\n")
            .replace('    if payload.get("tool_name") == "host_exec":', '    import sys\n    sys.exit(3)\n    if payload.get("tool_name") == "host_exec":'))
    installed(home, code=code)
    home.cfg(enabled=["weather"])
    assert plugins.load_all()["weather"]["state"] == "loaded"
    with pytest.raises(ToolFailure, match="SystemExit"):  # sys.exit 在工具里：只是这次调用出错，服务器不退
        run(plugins.call_tool(plugins._find("weather__forecast"), {"city": "Lisbon"}))
    out = run(U.fire("tool:before", {"event": "tool:before", "tool_name": "host_exec", "tool_input": {}}, agent="main", tool="host_exec"))
    assert not out.blocked  # 钩子里 sys.exit：当它出错（不是 fail_closed：照常做）
    bad = json.loads(json.dumps(MANIFEST))
    bad["capabilities"]["tools"] = True
    (config.settings.data_dir / "plugins" / "weather" / plugins.MANIFEST).write_text(json.dumps(bad), encoding="utf8")
    plugins.unload_all()
    st = plugins.load("weather")
    assert st["state"] == "error" and "must be a list" in st["error"]
    assert {r["name"]: r for r in plugins.status()}["weather"]["state"] == "error"  # 列状态也不炸（设置页不回 500）
    plugins.unload_all()
    run(plugins.start())  # 服务器照起
    bad["capabilities"] = {"mcp": 5}
    (config.settings.data_dir / "plugins" / "weather" / plugins.MANIFEST).write_text(json.dumps(bad), encoding="utf8")
    with pytest.raises(plugins.PluginError, match="must be a list"):
        plugins.read_manifest(config.settings.data_dir / "plugins" / "weather")


def test_big_files_are_refused_before_they_are_read(home, monkeypatch):
    from engine import skill_market
    write_plugin(home.src)
    (home.src / "weather" / "big.txt").write_bytes(b"x" * (skill_market.FILE_MAX + 1))
    real = Path.read_bytes

    def guarded(self):
        assert self.name != "big.txt", "read before its size was checked"
        return real(self)
    monkeypatch.setattr(Path, "read_bytes", guarded)
    with pytest.raises(plugins.PluginError, match="too big"):
        plugins.stage(str(home.src / "weather"))
