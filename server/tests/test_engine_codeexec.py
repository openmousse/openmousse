"""第 3 批第 13 项：execute_code（engine/codeexec.py）。不写 engine.tools.execute_code、没有沙箱就没有；开了：代码在沙箱里跑
（这里假的 docker 把 docker exec -i … python3 -c RUNNER 交给本机的 python 跑，RUNNER 和 RPC 都是真的），代码里调工具：只读的直接做；
会改东西的不做，除非用户在 tools 里放了它，放了的照样过关卡（要点头的交卡，代码里抛 NeedsApproval）；一次一把临时令牌，跑完收回；
调用次数、超时有上限；print 太长留头尾；出错带回溯。数据都是编的。"""
from __future__ import annotations

import asyncio
import json
import sys

import pytest

import engine
from engine import codeexec, mcp_engine, sandbox, tasks, tools, unattended
from test_engine_hands import fd  # noqa: F401 — 假的 docker

pytest.importorskip("aiohttp")


def run(coro):
    return asyncio.run(coro)


def ctx() -> engine.ToolCtx:
    return engine.ToolCtx(turn="t", kind="owner", agent="main", conversation="main", call_id="c1", control=engine.Control(), origin="user")


def tool() -> engine.Tool | None:
    return next((t for t in tools.for_agent("main") if t.name == "execute_code"), None)


@pytest.fixture
def box(fd, server_json, tmp_path, monkeypatch):  # noqa: F811 — fd 是导入的 fixture
    """主对话有沙箱（access none）、主工作区在 tmp；docker exec -i 的那一次换成本机的 python（在一个草稿目录里跑）。cfg(**) 换设置。"""
    from config import settings
    ws, work = tmp_path / "main-ws", tmp_path / "box-workspace"
    ws.mkdir()
    work.mkdir()
    monkeypatch.setattr(settings, "workspace", ws)
    monkeypatch.setattr(codeexec, "OUT_DIR", str(tmp_path / "code-out"))
    real = fd.spawn

    async def spawn(args, *, stdin=False):
        if "-i" in args and "-c" in args:
            fd.calls.append(list(args))
            return await asyncio.create_subprocess_exec(sys.executable, "-I", "-u", "-c", args[args.index("-c") + 1], cwd=str(work),
                                                        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                                                        stderr=asyncio.subprocess.STDOUT)
        return await real(args, stdin=stdin)
    fd.spawn = spawn

    def cfg(code_cfg=True, **engine_extra) -> None:
        server_json({"claw": {"kind": "builtin"}, "engine": {"model": "deepseek/deepseek-flash",
                                                             "sandbox": {"agents": {"main": {"access": "none", "browser": False}}},
                                                             "tools": {"execute_code": code_cfg}, **engine_extra}})
    cfg()
    return type("Box", (), {"ws": ws, "work": work, "cfg": staticmethod(cfg), "fd": fd})


def code(src: str) -> str:
    return run(tool().run({"code": src}, ctx()))


def test_off_by_default_without_a_sandbox_and_never_unattended(server_json, box):
    server_json({"claw": {"kind": "builtin"}, "engine": {"sandbox": {"agents": {"main": {"access": "none"}}}}})
    assert tool() is None  # 没开
    server_json({"claw": {"kind": "builtin"}, "engine": {"tools": {"execute_code": True}, "sandbox": {"hands": False}}})
    assert tool() is None  # 没有沙箱
    box.cfg()
    t = tool()
    assert t is not None and t.kinds == ("owner",) and t.level({"code": "print(1)"})[0] == "undo"
    assert "execute_code" not in {x.name for x in unattended.tools_for("main")} and "execute_code" in tasks.CHILD_NEVER


def test_code_calls_read_only_tools_over_the_rpc(box):
    (box.ws / "notes.md").write_text("# Fictional notes\n- buy oat milk\n- call the plumber\n", encoding="utf8")
    before = len(mcp_engine._grants)
    out = code("import __main__\n"
               "text = read_file(path='notes.md')\n"
               "todo = [ln for ln in text.splitlines() if '- ' in ln]\n"
               "print('found', len(todo))\n"
               "print(listing := list_dir(path='.'))\n"
               "from mousse_tools import call_tool, tools\n"
               "print('read_file' in tools(), 'execute_code' in tools())\n"
               "print('TOKEN', __main__._first['token'])\n")
    assert "found 2" in out and "notes.md" in out and "True False" in out  # 不给它自己
    assert "[ok ·" in out and "2 tool calls" in out and "<untrusted_tool_result" in out
    tok = out.split("TOKEN ", 1)[1].split()[0]
    assert mcp_engine.lookup(tok) is None and len(mcp_engine._grants) == before  # 跑完令牌就收回了
    exe = next(c for c in box.fd.calls if "-i" in c)
    assert exe[:2] == ["exec", "-i"] and "python3" in exe and "-I" in exe and any(a.startswith(f"{sandbox.MARK}=") for a in exe)
    assert box.fd.kills, "processes started by this run are cleaned up by their mark"


def test_writes_are_refused_unless_allowed_and_cards_still_come(box, monkeypatch):
    out = code("try:\n    write_file(path='new.txt', content='hi')\nexcept ToolError as e:\n    print('refused:', e)\n")
    assert "refused: Not run: inside execute_code only read-only calls run" in out and not (box.ws / "new.txt").exists()
    box.cfg({"tools": ["write_file"]})
    out = code("print(write_file(path='new.txt', content='hello fictional world'))\n")
    assert (box.ws / "new.txt").read_text() == "hello fictional world" and "[ok ·" in out  # 放了的：撤得回的直接做
    import chat
    import inbox
    tools.install_hooks()
    out = code("write_file(path='AGENTS.md', content='# New rules')\nprint('not reached')\n")  # 规矩文件：要点头
    assert "NeedsApproval" in out and "not reached" not in out and "waiting for the user's OK (inbox card" in out
    assert not (box.ws / "AGENTS.md").exists()
    iid = out.split("inbox card ", 1)[1].split(")")[0]
    with chat._lock, inbox.idb() as conn:
        card = dict(conn.execute("SELECT * FROM inbox WHERE id=?", (iid,)).fetchone())
    assert card["kind"] == "tool" and card["status"] == "pending" and "AGENTS.md" in card["detail"]


def test_an_approved_run_does_not_approve_the_calls_inside(box, monkeypatch):
    from engine import handkit
    seen = []
    real = mcp_engine.call

    async def call(g, name, args):
        seen.append((name, handkit.approved_run()))
        return await real(g, name, args)
    monkeypatch.setattr(mcp_engine, "call", call)
    row = {"args": json.dumps({"code": "print(list_dir(path='.'))\n"}), "agent": "main", "tool": "execute_code", "turn": "t", "thread": "main",
           "call": "c1"}
    out = run(handkit.run_approved(row, {}))  # 用户在卡上同意了这段代码
    assert "[ok ·" in out and seen == [("list_dir", False)]  # 里面的调用不算点过头：web 的照样交 Doorman
    assert not handkit.approved_run()


def test_limits_errors_and_long_output(box):
    box.cfg({"max_calls": 2})
    out = code("for i in range(3):\n    try:\n        list_dir(path='.')\n        print('call', i, 'ok')\n"
               "    except ToolError as e:\n        print('call', i, 'refused:', str(e)[:60])\n")
    assert "call 1 ok" in out and "call 2 refused: Not run: this run already made 2 tool calls" in out
    box.cfg()
    out = code("x = 1\ny = x / 0\n")
    assert "[error: ZeroDivisionError: division by zero" in out and 'File "<execute_code>", line 2' in out and "y = x / 0" in out
    out = code("print('a' * 100)\nprint('\\n'.join('line %d' % i for i in range(20000)))\nprint('the end')\n")
    assert "bytes cut from the middle; the whole output is in the sandbox at" in out and "the end" in out and "line 0" in out
    assert len(out) < codeexec.HEAD + codeexec.TAIL + 3000
    out = code("import sys\nsys.exit(3)\n")
    assert "[error: SystemExit: 3" in out
    out = code("import os\nos.system('echo from a child process')\nprint('after')\n")
    assert "from a child process" in out and "after" in out and "[ok ·" in out  # 子进程的输出也收得到，混不进 RPC


def test_timeout_stops_it(box):
    before = len(mcp_engine._grants)
    out = run(tool().run({"code": "import time\nprint('started', flush=True)\ntime.sleep(30)\n", "timeout": 1}, ctx()))
    assert "[stopped after 1s: the code didn't finish; everything it started was stopped" in out and "in the sandbox at" in out
    assert len(mcp_engine._grants) == before and box.fd.kills  # 令牌收回、容器里的进程按记号收掉


def test_workspace_modules_cannot_shadow_the_runner(box):
    (box.work / "json.py").write_text("raise SystemExit('shadowed!')\n")
    (box.work / "helper.py").write_text("def double(x):\n    return 2 * x\n")
    out = code("import helper\nprint(helper.double(21))\n")
    assert "42" in out and "[ok ·" in out and "shadowed" not in out
    assert json.loads('{"ok": 1}')  # 这边的 json 不受影响


def test_stopping_the_turn_revokes_the_token_and_stops_the_code(box):
    before, kills = len(mcp_engine._grants), len(box.fd.kills)

    async def go():
        c = ctx()
        task = asyncio.ensure_future(tool().run({"code": "import time\nlist_dir(path='.')\ntime.sleep(30)\n"}, c))
        for _ in range(100):
            await asyncio.sleep(0.05)
            if len(mcp_engine._grants) > before:
                break
        assert len(mcp_engine._grants) == before + 1  # 跑着的时候有一把
        await asyncio.sleep(0.3)
        c.control.abort()
        task.cancel()  # loop 在用户点了停时取消正在跑的工具
        with pytest.raises(asyncio.CancelledError):
            await task
    run(go())
    assert len(mcp_engine._grants) == before and len(box.fd.kills) > kills
