"""内核 ⑫：主机上跑命令（engine/host_exec.py，照 OpenClaw 的执行审批）。默认关、名单内直接跑、名单外出卡（只这一次 / 以后都放行，
后者写回 server.json）、危险命令出卡、不经 shell、超时和点停连进程组一起杀、环境里没有像密钥的变量、交卡后脚本被改就不跑。
命令都是临时目录里的小 sh 脚本，不碰别的。"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
from pathlib import Path

import pytest

import config
import engine
from engine import host_exec, tools


def run(coro):
    return asyncio.run(coro)


def ctx(agent: str = "main", conversation: str = "main") -> engine.ToolCtx:
    return engine.ToolCtx(turn="t", kind="owner", agent=agent, conversation=conversation, call_id="c1", control=engine.Control())


def tool(name: str = "host_exec", agent: str = "main") -> engine.Tool:
    return next(t for t in tools.for_agent(agent) if t.name == name)


def script(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)
    return path


@pytest.fixture
def ws(tmp_path, monkeypatch, server_json):
    from config import settings
    w = tmp_path / "main-ws"
    w.mkdir()
    monkeypatch.setattr(settings, "workspace", w)
    script(w / "scripts" / "hello.sh", 'echo "hello $1 from $(pwd)"\n')
    return w


def enable(server_json, **main) -> None:
    server_json({"engine": {"host_exec": {"agents": {"main": {"allow": ["<workspace>/scripts/*.sh ..."], **main}}}}})


def test_off_by_default_and_only_for_the_agents_listed(ws, server_json, tmp_path):
    server_json({})
    assert "host_exec" not in {t.name for t in tools.for_agent("main")}
    diet = tmp_path / "diet-ws"
    diet.mkdir()
    server_json({"agent_workspaces": {"diet": str(diet)}, "engine": {"host_exec": {"agents": {"diet": {"allow": ["echo hi"]}}}}})
    assert "host_exec" in {t.name for t in tools.for_agent("diet")} and "host_exec" not in {t.name for t in tools.for_agent("main")}
    server_json({"agent_workspaces": {"diet": str(diet)}, "engine": {"host_exec": {"agents": {"diet": True}}, "tools": {"levels": {"host_exec": "off"}}}})
    assert "host_exec" not in {t.name for t in tools.for_agent("diet")}
    assert "host_exec" not in {t.name for t in tools.for_agent("errand")}  # 只在沙箱里的 Agent 照旧没有主机上的东西


def test_allowlisted_commands_run_right_away_in_the_workspace(ws, server_json):
    enable(server_json)
    t = tool()
    assert t.level({"command": "scripts/hello.sh Sam"})[0] == "undo" and "allowlist" in t.level({"command": "scripts/hello.sh Sam"})[1]
    assert t.level({"command": "./scripts/hello.sh"})[0] == "undo" and t.level({"command": f"{ws}/scripts/hello.sh"})[0] == "undo"
    assert run(tools.gate(t, {"command": "scripts/hello.sh Sam"}, ctx())) is None
    out = run(t.run({"command": "scripts/hello.sh Sam"}, ctx()))
    assert out.startswith('<untrusted_tool_result source="host exec">') and f"hello Sam from {ws}" in out and "\n[exit code 0 · " in out
    # 名单外：..、链接指到外面、改名的脚本、PATH 上没有的
    (ws / "evil.sh").write_text("#!/bin/sh\necho evil\n")
    (ws / "evil.sh").chmod(0o755)
    (ws / "scripts" / "link.sh").symlink_to(ws / "evil.sh")
    for cmd in ("scripts/../evil.sh", "scripts/link.sh", "evil.sh"):
        assert t.level({"command": cmd})[0] == "ask", cmd
    with pytest.raises(engine.ToolFailure, match="isn't on the server's PATH"):
        run(t.run({"command": "definitely-not-a-program-xyz"}, ctx()))


def test_patterns_match_word_by_word(ws, server_json):
    server_json({"engine": {"host_exec": {"agents": {"main": {"allow": ["<workspace>/scripts/hello.sh", "<工作区>/scripts/*.sh --dry-run"]}}}}})
    t = tool()
    assert t.level({"command": "scripts/hello.sh"})[0] == "undo"
    assert t.level({"command": "scripts/hello.sh --force"})[0] == "ask"  # 没写 ... ：不许在后面接参数
    assert t.level({"command": "scripts/hello.sh --dry-run"})[0] == "undo"
    assert t.level({"command": "scripts/hello.sh --dry-run extra"})[0] == "ask"
    if shutil.which("python3", path=host_exec.clean_path()):
        server_json({"engine": {"host_exec": {"agents": {"main": {"allow": ["python3 <workspace>/scripts/*.py ..."]}}}}})
        (ws / "scripts" / "report.py").write_text("print('report')\n")
        assert tool().level({"command": "python3 scripts/report.py --day 2026-10-16"})[0] == "undo"
        assert tool().level({"command": "python3 -c 'print(1)'"})[0] == "ask"  # 内联代码：名单管不住


def test_cwd_must_be_a_real_folder_inside_the_workspace(ws, server_json):
    enable(server_json)
    (ws / "real" / "deep").mkdir(parents=True)
    (ws / "lnk").symlink_to(ws / "real")
    hello = f"{ws}/scripts/hello.sh"  # 相对路径的程序照工作目录找：这里写绝对的
    assert host_exec.parse("main", hello, "real/deep").cwd == ws / "real" / "deep"
    for cwd, why in (("lnk/deep", "symbolic link"), ("lnk", "no folder"), ("missing", "no folder"), ("../", "No ..")):
        with pytest.raises(engine.ToolFailure, match=why):
            host_exec.parse("main", hello, cwd)
    assert tool().level({"command": hello, "cwd": "lnk/deep"})[0] == "ask"  # 关卡：写法不对的当 ask，交给 ask() 回原因
    verdict = run(tools.gate(tool(), {"command": hello, "cwd": "lnk/deep"}, ctx()))
    assert isinstance(verdict, str) and "symbolic link" in verdict


def test_the_card_shows_the_whole_input(ws, server_json):
    import inbox
    enable(server_json)
    script(ws / "tools" / "cat.sh", "cat\n")
    tail = "FICTIONAL-TAIL-LINE"
    verdict = run(tools.gate(tool(), {"command": "tools/cat.sh", "input": "x" * 5000 + tail}, ctx()))
    assert verdict["status"] == "approval-pending" and tail in inbox.item(verdict["inbox"])["detail"]
    refused = run(tools.gate(tool(), {"command": "tools/cat.sh", "input": "y" * 30_000}, ctx()))
    assert isinstance(refused, str) and "too long" in refused


def test_shell_syntax_is_refused_without_a_card(ws, server_json):
    enable(server_json)
    t = tool()
    for cmd in ("scripts/hello.sh | grep x", "scripts/hello.sh && rm -rf /", "scripts/hello.sh > out.txt", "echo $(id)", "echo $HOME"):
        verdict = run(tools.gate(t, {"command": cmd}, ctx()))
        assert isinstance(verdict, str) and "doesn't use a shell" in verdict, cmd
    assert t.level({"command": "scripts/hello.sh 'a | b'"})[0] == "undo"  # 引号里的照字面


def test_risky_commands_always_ask_even_when_allowlisted(ws, server_json):
    server_json({"engine": {"host_exec": {"agents": {"main": {"allow": ["rm ...", "chmod ...", "sh ...", "env ...", "find ...", "git ...", "python3 ...", "curl ..."]}}}}})
    t = tool()
    cases = {"rm -rf scripts": "recursive delete", "chmod 777 scripts/hello.sh": "world/other-writable", "sh -c 'echo hi'": "shell command",
             "env FOO=1 scripts/hello.sh": "runs another program", "find . -delete": "find", "git reset --hard": "git reset --hard",
             "git push --force": "force push", "python3 -c 'import os'": "-e/-c", "curl http://169.254.169.254/latest/": "metadata"}
    for cmd, why in cases.items():
        if not shutil.which(cmd.split()[0], path=host_exec.clean_path()):
            continue
        level, reason = t.level({"command": cmd})
        assert level == "ask" and why in reason, (cmd, reason)
    assert host_exec.danger(host_exec.parse("main", ["rm", "-rf", "/"])) == "recursive delete of root filesystem"
    assert t.level({"command": "rm scripts/old.txt"})[0] == "undo"  # 名单里、不危险的照跑
    for cmd in ("python3 mousse.py update --yes", "python3 mousse.py config set engine.model x/y", "python3 mousse.py doctor --fix"):
        assert "operator command" in (host_exec.danger(host_exec.parse("main", cmd)) or ""), cmd  # 主人的运维命令：要你点头
    assert host_exec.danger(host_exec.parse("main", "python3 mousse.py status")) is None


def test_not_allowlisted_asks_once_or_always_and_always_writes_back(ws, server_json):
    import inbox
    enable(server_json)
    tools.install_hooks()
    script(ws / "tools" / "count.sh", 'echo "counted $1"\n')
    t = tool()

    async def go():
        verdict = await tools.gate(t, {"command": "tools/count.sh 3", "why": "Count the fictional widgets."}, ctx())
        assert verdict["status"] == "approval-pending" and "always allow" in verdict["message"]
        with inbox._lock, inbox.idb() as conn:
            rows = [dict(r) for r in conn.execute("SELECT * FROM inbox WHERE dedupe LIKE 'hostexec%' AND status='pending' AND title LIKE "
                                                  "'%tools/count.sh 3%' ORDER BY created_at")]
        once = next(r for r in rows if r["dedupe"].startswith("hostexec:"))
        always = next(r for r in rows if r["dedupe"].startswith("hostexec-always:"))
        assert once["id"] == verdict["inbox"] and once["kind"] == always["kind"] == "tool" and once["why"] == "Count the fictional widgets."
        assert once["approve_label"] == "Just this once" and always["approve_label"] == "Always allow" and always["level"] == "quiet"
        assert "<workspace>/tools/count.sh 3" in always["title"] and "tools/count.sh 3" in once["title"]
        again = await tools.gate(t, {"command": "tools/count.sh 3"}, ctx())  # 同一条还在等：还是那张
        assert again["inbox"] == once["id"]
        out = await inbox.act(always["id"], inbox.ActIn(action="approve"))
        return once, always, out
    once, always, out = run(go())
    assert out["item"]["status"] == "done" and "counted 3" in out["item"]["result"] and "Added to the allowlist" in out["item"]["result"]
    assert inbox.item(once["id"])["status"] == "withdrawn"  # 另一张跟着撤回
    allow = json.loads(config.CONFIG_PATH.read_text())["engine"]["host_exec"]["agents"]["main"]["allow"]
    assert allow == ["<workspace>/scripts/*.sh ...", "<workspace>/tools/count.sh 3"]
    t = tool()
    assert t.level({"command": "tools/count.sh 3"})[0] == "undo" and t.level({"command": "tools/count.sh 4"})[0] == "ask"
    # 名单里的脚本 Agent 改不了（改一下脚本就等于随便跑什么），所在的子文件夹也是
    for path in ("tools/count.sh", "tools/other.sh", "scripts/new.sh"):
        with pytest.raises(engine.ToolFailure, match="allowlist"):
            run(tool("write_file").run({"path": path, "content": "rm -rf ~"}, ctx()))
    assert run(tool("write_file").run({"path": "notes.md", "content": "ok"}, ctx())).startswith("Created notes.md")


def test_just_once_runs_it_and_rejecting_withdraws_the_other_card(ws, server_json):
    import inbox
    enable(server_json)
    tools.install_hooks()
    script(ws / "tools" / "a.sh", "echo a-ran\n")
    script(ws / "tools" / "b.sh", "echo b-ran\n")
    t = tool()

    async def pair(cmd: str) -> tuple[dict, dict]:
        await tools.gate(t, {"command": cmd}, ctx())
        with inbox._lock, inbox.idb() as conn:
            rows = [dict(r) for r in conn.execute("SELECT * FROM inbox WHERE status='pending' AND title LIKE ?", (f"%{cmd}%",))]
        return next(r for r in rows if r["dedupe"].startswith("hostexec:")), next(r for r in rows if r["dedupe"].startswith("hostexec-always:"))

    async def go():
        once, always = await pair("tools/a.sh")
        out = await inbox.act(once["id"], inbox.ActIn(action="approve"))
        assert "a-ran" in out["item"]["result"] and inbox.item(always["id"])["status"] == "withdrawn"
        once, always = await pair("tools/b.sh")
        await inbox.act(once["id"], inbox.ActIn(action="reject", note="not now"))
        assert inbox.item(always["id"])["status"] == "withdrawn"
        assert (await tools.gate(t, {"command": "tools/b.sh"}, ctx())).startswith("Not run: the user declined")
    run(go())
    assert json.loads(config.CONFIG_PATH.read_text())["engine"]["host_exec"]["agents"]["main"]["allow"] == ["<workspace>/scripts/*.sh ..."]


def test_the_pair_settles_together_for_an_api_session(ws, server_json, monkeypatch):
    """对外接口（engine/openai_api.py）的会话里交的一对卡：点「以后都放行」，关卡等着的「只这一次」那张看到的是真的结果；作废的那张
    不会被说成「用户没同意」；两张都点也只跑一次。"""
    import inbox

    from engine import openai_api
    enable(server_json)
    tools.install_hooks()
    for name in ("d", "e", "f"):
        script(ws / "tools" / f"{name}.sh", f"echo {name}-ran >> ran.txt; echo {name}-ran\n")
    told: list[tuple[str, str]] = []

    async def tell(conv: str, iid: str, text: str) -> None:
        told.append((iid, text))
    monkeypatch.setattr(openai_api, "tell", tell)
    monkeypatch.setattr(tools, "api_conversation", lambda row: "api:main:test" if row is not None else None)
    t = tool()

    async def pair(cmd: str) -> tuple[str, str]:
        verdict = await tools.gate(t, {"command": cmd}, ctx())
        with inbox._lock, inbox.idb() as conn:
            always = conn.execute("SELECT id FROM inbox WHERE status='pending' AND dedupe LIKE 'hostexec-always:%' AND title LIKE ?",
                                  (f"%{cmd}%",)).fetchone()["id"]
        return verdict["inbox"], always

    async def go():
        once, always = await pair("tools/d.sh")
        await inbox.act(always, inbox.ActIn(action="approve"))
        out = {"d": (once, always, openai_api.decision(once), openai_api.decision(always))}
        late = await inbox.act(once, inbox.ActIn(action="approve")) if inbox.item(once)["status"] == "pending" else None
        once, always = await pair("tools/e.sh")
        await inbox.act(once, inbox.ActIn(action="approve"))
        out["e"] = (once, always, openai_api.decision(once), openai_api.decision(always))
        once, always = await pair("tools/f.sh")
        await inbox.act(once, inbox.ActIn(action="reject", note="not today"))
        out["f"] = (once, always, openai_api.decision(once), openai_api.decision(always))
        return out, late
    out, late = run(go())
    once, always, d_once, d_always = out["d"]
    assert d_always["outcome"] == "approved" and d_once["outcome"] == "approved" and "d-ran" in d_once["output"]  # 等着「只这一次」的看到的是真结果
    assert late is None and inbox.item(once)["status"] == "withdrawn"
    once, always, e_once, e_always = out["e"]
    assert e_once["outcome"] == "approved" and e_always["outcome"] == "withdrawn"
    once, always, f_once, f_always = out["f"]
    assert f_once["outcome"] == "rejected" and f_always["outcome"] == "withdrawn"
    assert [iid for iid, _ in told] == [out["d"][1], out["e"][0], out["f"][0]], told  # 一件事一句；作废的那张不说「没同意」
    assert "NOT consented" in told[-1][1] and all("NOT consented" not in text for _, text in told[:2])
    assert (ws / "ran.txt").read_text() == "d-ran\ne-ran\n"  # 每条只跑了一次，拒了的没跑


def test_risky_or_always_ask_has_no_always_card_and_ask_off_does_not_ask(ws, server_json):
    import inbox
    server_json({"engine": {"host_exec": {"agents": {"main": {"allow": ["rm ..."]}}}}})
    tools.install_hooks()
    t = tool()
    if shutil.which("rm", path=host_exec.clean_path()):
        verdict = run(tools.gate(t, {"command": "rm -rf scripts"}, ctx()))
        assert verdict["status"] == "approval-pending" and "always allow" not in verdict["message"]
        assert "recursive delete" in inbox.item(verdict["inbox"])["why"]
    server_json({"engine": {"host_exec": {"agents": {"main": {"allow": [], "ask": "off"}}}}})
    verdict = run(tools.gate(tool(), {"command": "scripts/hello.sh"}, ctx()))
    assert isinstance(verdict, str) and "approvals are off" in verdict


def test_script_changed_after_the_card_is_not_run(ws, server_json):
    import inbox
    enable(server_json)
    tools.install_hooks()
    s = script(ws / "tools" / "c.sh", "echo harmless\n")

    async def go():
        verdict = await tools.gate(tool(), {"command": "tools/c.sh"}, ctx())
        s.write_text("#!/bin/sh\necho swapped\n")
        return await inbox.act(verdict["inbox"], inbox.ActIn(action="approve"))
    out = run(go())
    assert out["item"]["status"] == "failed" and "changed after the card was sent" in out["item"]["result"]


def test_child_env_has_no_secrets_and_keeps_what_the_owner_listed(ws, server_json, monkeypatch):
    monkeypatch.setenv("MOUSSE_TEST_API_KEY", "fake-key-123")
    monkeypatch.setenv("FAKE_SERVICE_TOKEN", "fake-token-456")
    monkeypatch.setenv("FAKE_DB_URL", "postgres://someone:fake-pw-789@db.example/app")
    monkeypatch.setenv("NODE_OPTIONS", "--require /tmp/x.js")
    monkeypatch.setenv("PLAIN_SETTING", "visible")
    script(ws / "scripts" / "env.sh", "env\n")
    enable(server_json, env=["FAKE_SERVICE_TOKEN"])
    out = run(tool().run({"command": "scripts/env.sh"}, ctx()))
    assert "fake-key-123" not in out and "fake-pw-789" not in out and "NODE_OPTIONS" not in out
    assert "FAKE_SERVICE_TOKEN=fake-token-456" in out and "PLAIN_SETTING=visible" in out and "MOUSSE_AGENT=main" in out
    assert all(p.startswith("/") for p in host_exec.child_env("main")["PATH"].split(os.pathsep))


def _gone(pid: int) -> bool:
    try:
        st = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return True
    return st.split()[2] == "Z"


def test_timeout_and_stop_kill_the_whole_process_group(ws, server_json):
    script(ws / "scripts" / "spawn.sh", 'sleep 60 &\necho $! > "$1"\nwait\n')
    enable(server_json)
    t = tool()
    pidf = ws / "child.pid"
    t0 = time.monotonic()
    out = run(t.run({"command": f"scripts/spawn.sh {pidf}", "timeout": 1}, ctx()))
    assert "didn't finish within the timeout" in out and time.monotonic() - t0 < 10
    child = int(pidf.read_text())
    for _ in range(50):
        if _gone(child):
            break
        time.sleep(0.1)
    assert _gone(child), "the background child of the timed-out command was killed too"
    pidf.unlink()

    async def stop():
        task = asyncio.ensure_future(t.run({"command": f"scripts/spawn.sh {pidf}"}, ctx()))
        for _ in range(100):
            await asyncio.sleep(0.05)
            if pidf.exists() and pidf.read_text().strip():
                break
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return int(pidf.read_text())
    child = run(stop())
    for _ in range(50):
        if _gone(child):
            break
        time.sleep(0.1)
    assert _gone(child), "stopping the turn kills the command and everything it started"


def test_rules_mention_host_commands_only_when_enabled(ws, server_json):
    from engine import owner
    server_json({})
    assert "Commands on the user's server" not in owner.rules("main", tools.for_agent("main"))
    enable(server_json)
    text = owner.rules("main", tools.for_agent("main"))
    assert "## Your hands" in text and "host_exec runs one program" in text and "`<workspace>/scripts/*.sh ...`" in text
    names_line = text.split("## Tools", 1)[1].split("\n", 2)[1]
    assert "host_exec" not in names_line and "read_file" not in names_line  # 命令行工具那一行不列它们
