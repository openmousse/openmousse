"""内核 ⑫：普通 Agent（主对话、用户建的）自己的沙箱——给谁、名字不撞、代办不变；分类跟着工作区的挂法走（none / ro / rw，rw 的命令要点头）；
挂工作区时盖住像密钥的和放行名单里的脚本；rw 时沙箱里的写在主机这边留旧版本；Doorman 按容器的地址认请求是谁的；浏览器截图；
主对话在内核上带着手跑一轮。docker、CDP、模型都是假的（同 test_engine_sandbox.py），不起真容器。"""
from __future__ import annotations

import asyncio
import base64
import os
import time

import pytest

import engine
from engine import browser, owner, sandbox, tools, versions
from engine.session import MemorySession, text_of, user
from test_engine import FakeModel
from test_engine_sandbox import FakeCDP, FakeDocker, FakeProc

ERRAND = ["exec", "process", "read", "write", "edit", "ls", "browser"]
BOX = ["sandbox_exec", "sandbox_process", "sandbox_read", "sandbox_write", "sandbox_edit", "sandbox_ls", "browser"]
NET = sandbox.Net("mousse-errand", "172.30.99.1", "172.30.99.0/24")
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg==")


def run(coro):
    return asyncio.run(coro)


def ctx(agent: str = "main", kind: str = "owner") -> engine.ToolCtx:
    return engine.ToolCtx(turn="t", kind=kind, agent=agent, conversation=agent, call_id="c1", control=engine.Control())


def names(agent: str) -> list[str]:
    return [t.name for t in tools.for_agent(agent)]


def tool(name: str, agent: str = "main") -> engine.Tool:
    return next(t for t in tools.for_agent(agent) if t.name == name)


def opts(args: list[str], flag: str) -> list[str]:
    return [args[i + 1] for i, a in enumerate(args) if a == flag]


@pytest.fixture
def ws(tmp_path, monkeypatch, server_json):
    from config import settings
    w = tmp_path / "main-ws"
    w.mkdir()
    monkeypatch.setattr(settings, "workspace", w)
    server_json({})
    return w


@pytest.fixture
def on(server_json):
    """写一份开着沙箱的 server.json：engine.sandbox.agents 就是 agents（别的 engine.sandbox 键写在后面）；levels / host_exec / 别的 Agent 的
    工作区按需加。"""
    def write(agents: dict | None = None, *, levels: dict | None = None, host_exec: dict | None = None, workspaces: dict | None = None,
              **sandbox_extra) -> None:
        e: dict = {"sandbox": {"agents": agents or {}, **sandbox_extra}}
        if levels:
            e["tools"] = {"levels": levels}
        if host_exec:
            e["host_exec"] = {"agents": host_exec}
        server_json({"engine": e, **({"agent_workspaces": workspaces} if workspaces else {})})
    return write


@pytest.fixture
def fd(monkeypatch):
    """假的 docker（test_engine_sandbox 的），另外清掉上一个测试留在内存里的容器记录（按地址认 Agent 时不串）。"""
    f = FakeDocker()
    sandbox.set_client(f)

    async def fw(unit):
        return f.firewall
    monkeypatch.setattr(sandbox, "firewall_ok", fw)
    monkeypatch.setattr(sandbox, "proxy_up", lambda url, timeout=1.5: f.proxy)
    for d in (sandbox._live, sandbox._checked, sandbox._jobs, browser._browsers, browser._locks):
        d.clear()
    yield f
    sandbox.set_client(None)
    for d in (sandbox._live, sandbox._checked, sandbox._jobs, browser._browsers, browser._locks):
        d.clear()


# —— 给谁：装好了沙箱才给，名字不撞，代办不变 ——————————————————————————————————————————————

def test_ordinary_agents_get_their_own_sandbox_once_it_is_set_up(ws, server_json, on, tmp_path):
    diet = tmp_path / "diet-ws"
    diet.mkdir()
    server_json({"agent_workspaces": {"diet": str(diet)}})
    assert not sandbox.hands_on() and not set(names("main")) & set(BOX), "no sandbox on a server that never set one up"
    assert "## Your hands" in owner.rules("main", tools.for_agent("main"))
    errand_ws = tmp_path / "errand-ws"
    errand_ws.mkdir()
    server_json({"agent_workspaces": {"diet": str(diet), "errand": str(errand_ws)}})  # 跑过 errand.py setup：有代办
    assert sandbox.hands_on()
    for agent in ("main", "diet"):
        got = names(agent)
        assert [n for n in got if n in BOX] == BOX and {"read_file", "write_file", "edit_file", "web_fetch"} <= set(got), agent
        assert not set(got) & {"exec", "process", "read", "write", "edit", "ls"} and len(got) == len(set(got)), agent
        assert all(tool(n, agent).kinds == ("owner",) for n in BOX)
    assert sandbox.box("main") == {"access": "none", "browser": True} and not sandbox.confined("main")
    assert names("errand") == ERRAND
    # 什么都开着，代办还是只有沙箱那一套
    server_json({"agent_workspaces": {"diet": str(diet), "errand": str(errand_ws)},
                 "engine": {"host_exec": {"agents": {"errand": {"allow": ["ls"]}, "main": {"allow": ["ls"]}}},
                            "web": {"search": {"provider": "duckduckgo"}}, "files": {"agents": {"errand": True}},
                            "sandbox": {"agents": {"*": {"access": "ro"}, "errand": {"only": False, "access": "rw"}}}}})
    assert names("errand") == ERRAND and sandbox.box("errand") == {"access": "none", "browser": True}
    assert {"host_exec", "web_search"} <= set(names("main"))
    # 开关
    on({"diet": False, "*": {"browser": False}}, workspaces={"diet": str(diet)})
    assert not set(names("diet")) & set(BOX) and "browser" not in names("main") and "sandbox_exec" in names("main")
    on({}, hands=False)
    assert not set(names("main")) & set(BOX) and names("errand") == ERRAND
    on({}, levels={"sandbox_ls": "off", "browser": "off"})
    assert "sandbox_ls" not in names("main") and "browser" not in names("main") and "sandbox_read" in names("main")
    # 第 8 项的写法（不写 only）还是「只在沙箱里」；写了 only: false 是普通 Agent 自己沙箱的设置
    on({"diet": {"access": "ro"}}, workspaces={"diet": str(diet)})
    assert sandbox.confined("diet") and names("diet") == ERRAND[:-1]
    on({"diet": {"only": False, "access": "rw", "browser": False}}, workspaces={"diet": str(diet)})
    assert not sandbox.confined("diet") and sandbox.box("diet") == {"access": "rw", "browser": False}
    assert "sandbox_exec" in names("diet") and "browser" not in names("diet")
    on({"main": {"access": "bogus", "browser": "yes"}})
    assert sandbox.box("main") == {"access": "none", "browser": True}


def test_sandbox_levels_follow_how_the_workspace_is_mounted(ws, on):
    def lv(name: str, args: dict) -> tuple[str, str]:
        return tool(name).level(args)
    on({"main": {"access": "none"}})
    assert lv("sandbox_exec", {"command": "rm -rf /workspace"})[0] == "undo" and lv("sandbox_read", {"path": "x"})[0] == "read"
    assert lv("sandbox_write", {"path": "x"})[0] == lv("sandbox_edit", {"path": "x"})[0] == "undo" and lv("sandbox_ls", {})[0] == "read"
    assert lv("sandbox_process", {"action": "poll"})[0] == "read" and lv("sandbox_process", {"action": "kill"})[0] == "undo"
    assert lv("browser", {"action": "screenshot"})[0] == "read" and lv("browser", {"action": "click", "ref": "e1"})[0] == "undo"
    on({"main": {"access": "ro"}})
    assert lv("sandbox_exec", {"command": "ls"})[0] == "undo"
    on({"main": {"access": "rw"}})
    level, why = lv("sandbox_exec", {"command": "ls"})
    assert level == "ask" and "access rw" in why
    assert lv("sandbox_write", {"path": "x"})[0] == "undo" and lv("sandbox_read", {"path": "x"})[0] == "read"
    on({"main": {"access": "rw"}}, levels={"sandbox_exec": "auto", "sandbox_read": "ask"})
    assert lv("sandbox_exec", {"command": "ls"})[0] == "undo" and lv("sandbox_read", {"path": "x"})[0] == "ask"
    # 描述里说的是新名字
    assert "sandbox_exec background" in tool("sandbox_process").description and "exec background" not in tool("sandbox_exec").description


# —— 挂工作区：盖住像密钥的、放行名单里的脚本 ——————————————————————————————————————————————

def test_workspace_mounts_hide_secrets_and_keep_allowlisted_scripts_read_only(ws, on, tmp_path):
    (ws / ".env").write_text("API_TOKEN=fake-not-real\n")
    (ws / "notes.md").write_text("n\n")
    (ws / "secrets").mkdir()
    (ws / "secrets" / "a.txt").write_text("x")
    (ws / "sub").mkdir()
    (ws / "sub" / "api_key.txt").write_text("x")
    (ws / "scripts").mkdir()
    (ws / "scripts" / "tidy.py").write_text("print(1)\n")
    root = ws.resolve()

    def args() -> list[str]:
        return sandbox.build("main", "shell", NET, "sha256:x").args
    on({"main": {"access": "ro"}})
    a = args()
    mounts, tmpfs = opts(a, "--mount"), opts(a, "--tmpfs")
    assert mounts[0] == f"type=bind,source={root},target=/workspace,readonly"
    assert "type=bind,source=/dev/null,target=/workspace/.env,readonly" in mounts
    assert "type=bind,source=/dev/null,target=/workspace/sub/api_key.txt,readonly" in mounts
    assert "/workspace/secrets:ro,size=4k,mode=0555" in tmpfs and "/workspace/.mousse-versions:ro,size=4k,mode=0555" in tmpfs
    assert "/workspace" not in {x.split(":", 1)[0] for x in tmpfs} and "--user" not in a and (ws / ".mousse-versions").is_dir()
    assert not any("notes.md" in m or "scripts" in m for m in mounts)
    # rw：能写、按服务器这个用户写；放行名单里会跑到的脚本文件夹只读盖上（盖在工作区那一层之后）
    on({"main": {"access": "rw"}}, host_exec={"main": {"allow": ["python3 <workspace>/scripts/*.py"]}})
    a = args()
    mounts = opts(a, "--mount")
    assert mounts[0] == f"type=bind,source={root},target=/workspace" and opts(a, "--user") == [f"{os.getuid()}:{os.getgid()}"]
    assert mounts[1] == f"type=bind,source={root / 'scripts'},target=/workspace/scripts,readonly"
    # 盖不住的写法 → 整个只读：工作区根目录下的通配、从外面伸进工作区的通配、还没有的文件夹
    for allow in ("python3 <workspace>/*.py", "python3 /**/tidy.py", f"python3 {tmp_path}/*/scripts/tidy.py", "python3 <workspace>/later/*.py"):
        on({"main": {"access": "rw"}}, host_exec={"main": {"allow": [allow]}})
        a = args()
        assert opts(a, "--mount")[0].endswith(",readonly") and "--user" not in a, allow
        # 实际只读挂：命令不用问，会话开头也照实说
        assert sandbox.mounted("main") == "ro" and tool("sandbox_exec").level({"command": "ls"})[0] == "undo", allow
        assert "read-only" in owner.rules("main", tools.for_agent("main")), allow
    # access none：/workspace 是空的草稿区
    on({"main": {"access": "none"}})
    a = args()
    assert not opts(a, "--mount") and "/workspace" in {x.split(":", 1)[0] for x in opts(a, "--tmpfs")}
    # 主工作区只挂给主对话：别的 Agent 的工作区指到它也不挂（它的沙箱就是空的草稿区）
    on({"diet": {"only": False, "access": "ro"}}, workspaces={"diet": str(ws)})
    assert sandbox.workspace_of("diet") is None and not opts(sandbox.build("diet", "shell", NET).args, "--mount")
    assert sandbox.mounted("diet") == "none" and sandbox.mounted("nobody") == "none"


def test_mount_scan_covers_git_configs_skips_dependencies_and_fails_closed(ws, on, monkeypatch):
    (ws / ".git" / "modules" / "lib").mkdir(parents=True)
    (ws / ".git" / "config").write_text('[remote "origin"]\n\turl = https://someone:fake-token@git.example/r.git\n')
    (ws / ".git" / "modules" / "lib" / "config").write_text("[core]\n")
    (ws / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (ws / "node_modules" / "object-keys").mkdir(parents=True)
    (ws / "node_modules" / "object-keys" / "index.js").write_text("module.exports = Object.keys;\n")
    (ws / "deep" / "er" / "still").mkdir(parents=True)
    (ws / "deep" / "er" / "still" / ".env").write_text("TOKEN=fake\n")
    (ws / "proj" / ".git" / "objects" / "ab").mkdir(parents=True)  # 工作区里套着的另一个仓库
    (ws / "proj" / ".git" / "config").write_text("[core]\n")
    (ws / "proj" / ".git" / "objects" / "ab" / "cdef").write_text("blob")
    on({"main": {"access": "ro"}})
    a = sandbox.build("main", "shell", NET, "sha256:x").args
    mounts = opts(a, "--mount")
    for hidden in (".git/config", ".git/modules/lib/config", "deep/er/still/.env", "proj/.git/config"):
        assert f"type=bind,source=/dev/null,target=/workspace/{hidden},readonly" in mounts, hidden
    assert not any("node_modules" in m or "HEAD" in m or "objects" in m for m in mounts + opts(a, "--tmpfs"))
    sandbox._plans.clear()
    assert sandbox.mounted("main") == "ro"
    # 查不完、要盖的太多：不挂（空的草稿区），会话开头也照实说；分类按「可能能写」算（只会更严）
    for knob, value in (("HIDE_SCAN", 3), ("HIDE_MAX", 2)):
        orig = getattr(sandbox, knob)
        monkeypatch.setattr(sandbox, knob, value)
        for access in ("ro", "rw"):
            on({"main": {"access": access}})
            sandbox._plans.clear()
            a = sandbox.build("main", "shell", NET, "sha256:x").args
            assert not opts(a, "--mount") and "/workspace" in {x.split(":", 1)[0] for x in opts(a, "--tmpfs")}, (knob, access)
            assert sandbox.mounted("main") == "none" and "--user" not in a, (knob, access)
            assert "empty scratch folder" in owner.rules("main", tools.for_agent("main"))
        assert tool("sandbox_exec").level({"command": "ls"})[0] == "ask"
        monkeypatch.setattr(sandbox, knob, orig)


def test_host_file_writes_refuse_scripts_a_broad_allowlist_would_run(ws, on):
    (ws / "tidy.py").write_text("print(1)\n")
    on({}, host_exec={"main": {"allow": ["python3 /**/tidy.py"]}})
    with pytest.raises(engine.ToolFailure):
        run(tool("write_file").run({"path": "tidy.py", "content": "print(2)\n"}, ctx()))
    assert (ws / "tidy.py").read_text() == "print(1)\n"


# —— rw：命令要点头；写之前在主机这边留旧版本 ——————————————————————————————————————————————

def test_rw_sandbox_commands_ask_first_and_run_once_approved(ws, on, fd):
    import inbox
    tools.install_hooks()
    on({"main": {"access": "rw"}})
    fd.proc = lambda args: FakeProc([b"built\n"])
    t = tool("sandbox_exec")

    async def go():
        verdict = await tools.gate(t, {"command": "make all"}, ctx())
        assert verdict["status"] == "approval-pending" and not fd.procs  # 还没跑
        it = inbox.item(verdict["inbox"])
        return it, await inbox.act(verdict["inbox"], inbox.ActIn(action="approve"))
    it, out = run(go())
    assert it["kind"] == "tool" and it["title"] == "Run a command in the sandbox: make all" and "access rw" in it["why"]
    assert '"command": "make all"' in it["detail"]
    assert out["item"]["status"] == "done" and "built" in out["item"]["result"] and "[exit code 0" in out["item"]["result"]
    assert fd.procs and fd.procs[-1][0][-1] == "make all"
    c = fd.containers[sandbox.container_name("main")]
    assert c["labels"][f"{sandbox.LABEL}.agent"] == "main" and opts(c["args"], "--user")


def test_rw_sandbox_writes_keep_the_old_version_on_the_host(ws, on, fd):
    on({"main": {"access": "rw"}})
    (ws / "notes.md").write_text("first\n")
    (ws / ".env").write_text("API_TOKEN=fake-not-real\n")

    def write(args, inp):  # 容器里写 /workspace 下的 = 写进挂进去的工作区（这里直接写主机上那个文件）
        if args[-1].startswith("/workspace/"):
            (ws / args[-1][len("/workspace/"):]).write_bytes(inp)
        return {"bytes": len(inp)}
    fd.py["PY_WRITE"] = write
    out = run(tool("sandbox_write").run({"path": "notes.md", "content": "second\n"}, ctx()))
    assert out.startswith("Wrote 7 bytes to /workspace/notes.md.") and "restore_file" in out and "notes.md" in out
    assert (ws / "notes.md").read_text() == "second\n" and versions.history(ws, "notes.md")[0]["id"] == "v1"
    restored = run(tool("restore_file").run({"path": "notes.md", "version": "v1"}, ctx()))
    assert "notes.md" in restored and (ws / "notes.md").read_text() == "first\n"
    fd.calls.clear()
    with pytest.raises(engine.ToolFailure):
        run(tool("sandbox_write").run({"path": ".env", "content": "API_TOKEN=other\n"}, ctx()))
    assert not any("python3" in c for c in fd.calls) and (ws / ".env").read_text() == "API_TOKEN=fake-not-real\n"
    out = run(tool("sandbox_write").run({"path": "/tmp/scratch.txt", "content": "x"}, ctx()))  # 不在工作区里：只是沙箱里的草稿
    assert out == "Wrote 1 bytes to /tmp/scratch.txt."
    out = run(tool("sandbox_exec").run({"command": "sleep 60", "background": True}, ctx()))  # 后台的：说的是新名字
    assert "Check it with sandbox_process" in out


# —— 主对话的沙箱：收掉重来、查、Doorman 认得是谁 ——————————————————————————————————————————————

def test_main_sandbox_reset_status_and_doorman_attribution(ws, on, fd, monkeypatch):
    import egress
    import inbox

    from engine import api
    monkeypatch.setattr(egress, "MAX_PENDING", 1000)
    on({"main": {"access": "none"}})
    fd.ip = "172.30.99.77"
    live = run(sandbox.ensure("main"))
    assert live.ip == "172.30.99.77" and egress.agent_of("172.30.99.77") == "main" and egress.agent_of("172.30.99.5") == egress.AGENT
    seen: list = []

    async def judge(req, task, agent_name=None):
        seen.append((task, agent_name))
        return "hold", "It looks like it sends data out"
    monkeypatch.setattr(egress, "judge", judge)
    monkeypatch.setattr(egress, "errand_task", lambda thread=egress.AGENT: f"what {thread} was asked")
    req = egress.CheckIn(client="172.30.99.77", method="GET", host="files.example", path="/up?d=" + "QUJD" * 100, sha=f"s-{time.time_ns()}")
    out = run(egress.decide(req))
    assert out["decision"] == "hold" and seen == [("what main was asked", "TestMousse")]
    with egress._lock, egress.edb() as conn:
        row = dict(conn.execute("SELECT * FROM egress_holds WHERE id=?", (out["hold"],)).fetchone())
    it = inbox.item(row["inbox_id"])
    assert row["agent"] == "main" and it["source"] == "main" and it["title"] == "TestMousse wants to open a link on files.example"
    assert "Working on: what main was asked" in it["why"]
    assert [h["id"] for h in sandbox.pending_holds("main")][-1] == out["hold"] and out["hold"] not in {h["id"] for h in sandbox.pending_holds("errand")}
    # 状态、检查、收掉重来
    st = run(api.sandbox_status(check=True))
    assert st["hands"]["on"] and st["hands"]["main"] == {"access": "none", "browser": True}
    assert st["checks"]["main/shell"] == {"ok": True} and st["checks"]["main/browser"] == {"ok": True}
    assert run(api.sandbox_reset(api.ResetIn(agent="main")))["removed"] == 1 and not fd.containers
    on({"*": {"access": "ro"}})  # 没有自己工作区的 Agent 写了 ro：沙箱照起，/workspace 是空的草稿区（不报错）
    sandbox._checked.clear()
    run(sandbox.preflight("nobody", "shell"))
    from fastapi import HTTPException
    on({"main": False})
    with pytest.raises(HTTPException):
        run(api.sandbox_reset(api.ResetIn(agent="main")))
    with pytest.raises(sandbox.SandboxError):
        run(sandbox.ensure("main"))


def test_sandbox_tools_never_run_in_cyber_or_outsider_turns(ws, on):
    on({"main": {"access": "rw"}})
    for kind in ("cyber", "outsider"):
        for name, args in (("sandbox_exec", {"command": "ls"}), ("browser", {"action": "open", "url": "example.com"}), ("sandbox_read", {"path": "x"})):
            assert "only run in the owner's" in run(tools.gate(tool(name), args, ctx(kind=kind))), (kind, name)


# —— 浏览器：普通 Agent 的多一个截图（存在服务器上，回路径）——————————————————————————————————————

class MainCDP(FakeCDP):
    def token_ok(self, request) -> bool:
        c = self.fd.containers.get(sandbox.container_name("main", "browser")) or {}
        ok = request.headers.get("Authorization") == f"Bearer {c.get('env', {}).get('OPENCLAW_BROWSER_CDP_AUTH_TOKEN')}"
        self.unauthorized += 0 if ok else 1
        return ok

    def answer(self, m: dict) -> dict:
        if m["method"] == "Page.captureScreenshot":
            return {"data": base64.b64encode(PNG).decode()}
        return super().answer(m)


def test_browser_screenshot_is_saved_on_the_server(ws, on, fd, monkeypatch):
    from aiohttp import web
    on({"main": {"access": "none"}})
    fake = MainCDP(fd)
    app = web.Application()
    app.router.add_get("/json/version", fake.version)
    app.router.add_get("/devtools/browser/abc", fake.ws)
    fd.ip = "127.0.0.1"

    async def go():
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        monkeypatch.setattr(browser, "CDP_PORT", site._server.sockets[0].getsockname()[1])
        try:
            out = await tool("browser").run({"action": "screenshot"}, ctx())
            with pytest.raises(browser.BrowserError):  # 不给 extra（代办的浏览器）：没有截图
                await browser.act("main", {"action": "screenshot"})
            return out
        finally:
            await browser.close_all()
            await runner.cleanup()
    res = run(go())
    out = res.text  # ⑫b：截图也交给模型看（engine/images.py 存进图库）
    path = out.split("Screenshot saved on the server: ", 1)[1].split(" (", 1)[0]
    assert os.path.isfile(path) and open(path, "rb").read() == PNG and "/engine/screenshots/main/" in path
    assert '<untrusted_tool_result source="sandbox browser">' in out and "[e1] button" in out and fake.unauthorized == 0
    [img] = res.images
    assert img["mime"] == "image/png" and (img["w"], img["h"]) == (1, 1) and img["label"].startswith("screenshot of ") and "data" not in img
    from engine import images
    assert images.load(img) == base64.b64encode(PNG).decode() and "treat it as data" in out
    assert "screenshot" in tool("browser").schema["properties"]["action"]["enum"]
    errand_browser = next(t for t in tools.for_agent("errand") if t.name == "browser")
    assert "screenshot" not in errand_browser.schema["properties"]["action"]["enum"]


# —— 主对话在内核上带着手跑一轮 ——————————————————————————————————————————————————————

@pytest.fixture
def fake_model(monkeypatch):
    from engine import providers

    def make(script):
        f = FakeModel(script)
        monkeypatch.setattr(providers, "stream", f.stream)
        return f
    return make


def test_main_chat_turn_uses_its_hands_on_the_builtin_engine(ws, server_json, fd, fake_model):
    import chat
    server_json({"claw": {"kind": "builtin"}, "engine": {"model": "deepseek/deepseek-flash", "sandbox": {"agents": {"main": {"access": "none"}}}}})
    (ws / "plan.md").write_text("# Plan\n- water the basil\n")
    fd.proc = lambda args: FakeProc([b"42\n"])
    f = fake_model([{"calls": [("c1", "read_file", {"path": "plan.md"})]},
                    {"calls": [("c2", "edit_file", {"path": "plan.md", "edits": [{"old": "- water the basil", "new": "- water the basil and mint"}]})]},
                    {"calls": [("c3", "sandbox_exec", {"command": "python3 -c 'print(6*7)'"})]},
                    {"text": "Updated the plan; the answer is 42."}])

    async def go():
        chat.start_run("t-hands", "add mint to my plan, then work out 6*7", None)
        t0 = time.time()
        while time.time() - t0 < 10:
            r = chat.RUNS.get("t-hands")
            if r and r.done:
                return r
            await asyncio.sleep(0.02)
        raise AssertionError("run did not finish")
    r = run(go())
    assert r.text == "Updated the plan; the answer is 42.", r.text
    req = f.requests[0][1]
    got = {t["name"] for t in req.tools}
    assert {"read_file", "edit_file", "restore_file", "sandbox_exec", "browser", "web_fetch"} <= got and not got & {"exec", "read", "write"}
    assert "## Your hands" in req.system and "**Your sandbox**" in req.system and str(ws.resolve()) in req.system
    assert "You can't run commands on the user's server itself" in req.system
    results = [text_of(m) for m in f.requests[3][1].messages if m["role"] == "tool"]
    assert "water the basil" in results[0] and "restore_file" in results[1] and "42" in results[2]
    assert (ws / "plan.md").read_text() == "# Plan\n- water the basil and mint\n"
    assert fd.procs[-1][0][-1] == "python3 -c 'print(6*7)'" and fd.containers[sandbox.container_name("main")]["labels"][f"{sandbox.LABEL}.agent"] == "main"


def test_cyber_and_outsider_turns_on_main_get_no_hands(ws, on, fake_model):
    from engine.errors import EngineError
    on({"main": {"access": "rw"}}, host_exec={"main": {"allow": ["ls"]}})
    f = fake_model([{"text": "hi"}])
    every = tools.for_agent("main")
    assert {"sandbox_exec", "host_exec", "read_file", "web_fetch"} <= {t.name for t in every}
    run(engine.run_turn(engine.Spec(kind="cyber", model="deepseek/x", tools=every, gate=tools.gate), MemorySession(), user("hi")))
    assert f.requests[0][1].tools == []
    with pytest.raises(EngineError):  # 外人回合挂了工具：直接不跑
        run(engine.run_turn(engine.Spec(kind="outsider", model="deepseek/x", tools=every, gate=tools.gate), MemorySession(), user("hi")))
