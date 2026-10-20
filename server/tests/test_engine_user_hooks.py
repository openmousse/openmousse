"""第 5 批第 4 项：用户钩子（engine/user_hooks.py、../hooks_ctl.py）。钩子是测试里现写的小脚本（sys.executable 跑），模型是假的。
每种事件都触发；tool:before 拦（退出码 2、decision block）、改参数（改过的照样过关卡，分类不能变严）；超时、fail_closed；没同意的不跑、
改了命令（脚本）要重新同意；环境里没有像密钥的变量；工作区的 HOOK.md；boot-md、command-logger 默认关、开了才动。"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import config
import engine
from engine import loop, owner, tools
from engine import user_hooks as U
from engine.session import DbSession, MemorySession, day_key, text_of, user
from test_engine import FakeModel

SCRIPT = r'''
import json, os, sys, time
log, mode = sys.argv[1], sys.argv[2]
p = json.load(sys.stdin)
with open(log, "a") as f:
    f.write(json.dumps({"event": p.get("event"), "payload": p, "env": sorted(os.environ)}) + "\n")
if mode == "block":
    print(json.dumps({"decision": "block", "reason": "not on my watch"}))
elif mode == "hermes-block":
    print(json.dumps({"action": "block", "message": "hermes says no"}))
elif mode == "exit2":
    print("denied by policy", file=sys.stderr); sys.exit(2)
elif mode == "modify":
    print(json.dumps({"decision": "modify", "tool_input": {**p.get("tool_input", {}), "x": "changed"}}))
elif mode == "escalate":
    print(json.dumps({"hookSpecificOutput": {"updatedInput": {"x": "danger"}}}))
elif mode == "sleep":
    time.sleep(10)
elif mode == "garbage":
    print("this is not json")
elif mode == "crash":
    sys.exit(3)
elif mode == "slow-block":
    time.sleep(1.0)
    if "do it" in p.get("text", ""):
        print(json.dumps({"decision": "block", "reason": "not on my watch"}))
'''


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def home(tmp_path, monkeypatch, server_json):
    import sources
    ws = tmp_path / "ws-main"
    (ws / "memory").mkdir(parents=True)
    monkeypatch.setattr(config.settings, "workspace", ws)
    monkeypatch.setattr(config.settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(config.settings, "profile", tmp_path / "no-profile.md")
    monkeypatch.setattr(sources, "memory_tree", None)
    monkeypatch.setattr(U, "_asked", set())
    monkeypatch.setattr(U, "_last", {})
    script = tmp_path / "hook.py"
    script.write_text(SCRIPT, encoding="utf8")
    log = tmp_path / "hook-log.jsonl"
    base = {"claw": {"kind": "builtin"}, "engine": {"model": "deepseek/deepseek-flash"}}

    def cfg(hooks: dict | None = None, **engine_extra) -> None:
        server_json({**base, "engine": {**base["engine"], **({"hooks": hooks} if hooks is not None else {}), **engine_extra}})

    def hook(mode: str = "log", **extra) -> dict:
        return {"command": [sys.executable, str(script), str(log), mode], **extra}

    def lines() -> list[dict]:
        return [json.loads(x) for x in log.read_text(encoding="utf8").splitlines()] if log.exists() else []

    def approve_all() -> None:
        for h in U.hooks():
            if not h.error:
                U.approve(h)
    cfg()
    tools.install_hooks()
    return SimpleNamespace(ws=ws, data=tmp_path / "data", script=script, log=log, cfg=cfg, hook=hook, lines=lines, approve_all=approve_all,
                           tmp=tmp_path)


async def until(cond, timeout: float = 8.0) -> None:
    t0 = time.monotonic()
    while not cond():
        if time.monotonic() - t0 > timeout:
            raise AssertionError("timed out waiting")
        await asyncio.sleep(0.05)


def ctx(agent: str = "main", conversation: str = "main") -> engine.ToolCtx:
    return engine.ToolCtx(turn="t-hooks", kind="owner", agent=agent, conversation=conversation, call_id="c1", control=engine.Control())


def echo_tool(log: list) -> engine.Tool:
    async def go(args, ctx_):
        log.append(dict(args))
        return f"echo {args.get('x')}"
    return engine.Tool(name="echo", description="Echo x.", schema={"type": "object", "required": ["x"], "properties": {"x": {"type": "string"}}},
                       run=go, classify=lambda a: ("ask", "dangerous") if a.get("x") == "danger" else ("undo", "fine"))


# —— 同意 ——————————————————————————————————————————————————————————————————————

def test_unapproved_hooks_dont_run_and_changes_need_a_new_ok(home):
    import inbox
    home.cfg({"session:start": [home.hook()]})
    [h] = U.hooks("session:start")
    fp = h.fingerprint()

    async def go():
        await U.fire("session:start", U.base_payload("session:start", "main", "main"), agent="main")
        await asyncio.sleep(0.3)
    run(go())
    assert home.lines() == []  # 没同意：不跑
    st = U.status()["hooks"][0]
    assert st["state"] == "waiting" and st["approved"] is False and st["last"][0]["outcome"] == "waiting"
    from engine import store
    with store.read() as conn:
        row = conn.execute("SELECT * FROM engine_calls WHERE tool='hooks:session:start' ORDER BY created_at DESC LIMIT 1").fetchone()
    assert row is not None and json.loads(row["args"])["fingerprint"] == fp and row["inbox"]  # 交了一张卡
    assert inbox.item(row["inbox"])["title"] == "Allow this hook to run: session:start"
    U.approve(h)
    run(U.fire("session:start", U.base_payload("session:start", "main", "main"), agent="main"))
    assert [x["event"] for x in home.lines()] == ["session:start"]
    home.script.write_text(SCRIPT + "\n# changed\n", encoding="utf8")  # 脚本改了：指纹变了，要重新同意
    [h2] = U.hooks("session:start")
    assert h2.fingerprint() != fp and not U.approved(h2)
    run(U.fire("session:start", U.base_payload("session:start", "main", "main"), agent="main"))
    assert len(home.lines()) == 1
    home.cfg({"session:start": [home.hook(timeout=5)]})  # 命令（参数）改了也一样
    assert not U.approved(U.hooks("session:start")[0])


def test_approving_the_card_puts_the_hook_on_the_allowlist(home, monkeypatch):
    import chat
    import data
    import inbox
    from engine import providers
    monkeypatch.setattr(providers, "stream", FakeModel([{"text": "ok"}] * 3).stream)
    with chat._lock, data.ddb() as conn:
        conn.execute("INSERT OR REPLACE INTO groups(id, name, icon, purpose, created_at) VALUES(?,?,?,?,?)", ("g-hooks", "Hooks", "x", "", chat.now_iso()))
    ws = home.tmp / "ws-g"
    (ws / "hooks" / "greet").mkdir(parents=True)
    (ws / "hooks" / "greet" / "HOOK.md").write_text("---\nname: greet\nevents: [session:start]\n---\n\nSays hello.\n", encoding="utf8")
    (ws / "hooks" / "greet" / "handler.py").write_text(SCRIPT.replace("log, mode = sys.argv[1], sys.argv[2]",
                                                                       f"log, mode = {str(home.log)!r}, 'log'"), encoding="utf8")
    monkeypatch.setattr(type(config.settings), "agent_workspaces", property(lambda self: {"g-hooks": ws}))
    home.cfg({"workspace": False})
    assert U.hooks("session:start", "g-hooks") == []  # 默认不认工作区的钩子
    home.cfg({"workspace": True})
    [h] = U.hooks("session:start", "g-hooks")
    assert h.origin == "workspace:g-hooks/greet" and h.agent == "g-hooks" and not h.error

    async def go():
        await U.fire("session:start", U.base_payload("session:start", "g-hooks", "g-hooks"), agent="g-hooks")
        await U.fire("session:start", U.base_payload("session:start", "main", "main"), agent="main")  # 别的 Agent 的事件：不管
        from engine import store

        def card_id():
            with store.read() as conn:
                r = conn.execute("SELECT inbox FROM engine_calls WHERE tool='hooks:session:start' AND inbox IS NOT NULL AND agent='g-hooks' "
                                 "ORDER BY created_at DESC LIMIT 1").fetchone()
            return r["inbox"] if r else None
        await until(lambda: card_id() is not None)
        iid = card_id()
        it = inbox.item(iid)
        assert it["thread"] == "g-hooks" and "handler.py" in it["detail"] and h.fingerprint() in it["detail"]
        res = await inbox.act(iid, inbox.ActIn(action="approve"))
        assert res["item"]["status"] == "done"
        for _ in range(100):
            r = chat.RUNS.get("g-hooks")
            if r and r.done:
                break
            await asyncio.sleep(0.05)
        await U.fire("session:start", U.base_payload("session:start", "g-hooks", "g-hooks"), agent="g-hooks")
    run(go())
    assert U.approved(h) and home.lines() and {x["payload"]["agent"] for x in home.lines()} == {"g-hooks"}  # main 的事件不管


# —— tool:before / tool:after ———————————————————————————————————————————————————————

@pytest.mark.parametrize("mode, why", [("block", "not on my watch"), ("hermes-block", "hermes says no"), ("exit2", "denied by policy")])
def test_tool_before_can_block(home, mode, why):
    home.cfg({"tool:before": [home.hook(mode, matcher="echo")]})
    home.approve_all()
    calls: list = []
    [t] = U.wrap([echo_tool(calls)], "main")
    with pytest.raises(engine.ToolFailure, match=why):
        run(t.run({"x": "hi"}, ctx()))
    assert calls == [] and home.lines()[0]["payload"]["tool_name"] == "echo" and home.lines()[0]["payload"]["tool_input"] == {"x": "hi"}


def test_tool_before_modifies_and_the_change_still_goes_through_the_gate(home, fake_model):
    home.cfg({"tool:before": [home.hook("modify", matcher="ech*")]})
    home.approve_all()
    calls: list = []
    [t] = U.wrap([echo_tool(calls)], "main")
    f = fake_model([{"text": "", "calls": [("c1", "echo", {"x": "hi"})]}, {"text": "done"}])
    res = run(loop.run_turn(loop.Spec(kind="owner", model="deepseek/deepseek-flash", tools=[t], gate=tools.gate, conversation="t-hk", agent="main"),
                            MemorySession(), user("go")))
    assert res.status == "done" and calls == [{"x": "changed"}]
    assert "echo changed" in text_of(f.requests[1][1].messages[-1])
    home.cfg({"tool:before": [home.hook("escalate", matcher="echo")]})  # 改成要点头的那一类：不做（钩子不能替用户点头）
    home.approve_all()
    [t] = U.wrap([echo_tool(calls)], "main")
    with pytest.raises(engine.ToolFailure, match="stricter check"):
        run(t.run({"x": "hi"}, ctx()))
    assert calls == [{"x": "changed"}]
    home.cfg({"tool:before": [home.hook("modify", matcher="other_tool")]})  # matcher 对不上：不管
    home.approve_all()
    [t] = U.wrap([echo_tool(calls)], "main")
    assert run(t.run({"x": "plain"}, ctx())) == "echo plain"


def test_a_bridge_tool_can_change_arguments_but_not_the_subcommand(home, monkeypatch):
    import bridges
    ran: list = []

    async def fake_run(name, args, stdin, agent, *, kill_on_cancel=False):
        ran.append(list(args))
        return "ok"
    monkeypatch.setattr(bridges, "run", fake_run)
    hook_py = home.tmp / "swap.py"
    hook_py.write_text("import json,sys\np=json.load(sys.stdin)\nargs=p['tool_input']['args']\n"
                       "print(json.dumps({'decision':'modify','tool_input':{**p['tool_input'],'args':[sys.argv[1]]+args[1:]}}))\n", encoding="utf8")
    home.cfg({"tool:before": [{"command": [sys.executable, str(hook_py), "list"], "matcher": "journal"}]})
    home.approve_all()
    t = next(x for x in tools.for_agent("main") if x.name == "journal")
    with pytest.raises(engine.ToolFailure, match="subcommand"):
        run(t.run({"args": ["recent", "--days", "3"]}, ctx()))
    assert ran == []


def test_timeouts_fail_closed_and_bad_output(home):
    calls: list = []
    home.cfg({"tool:before": [home.hook("sleep", timeout=1, fail_closed=True)]})
    home.approve_all()
    [t] = U.wrap([echo_tool(calls)], "main")
    t0 = time.monotonic()
    with pytest.raises(engine.ToolFailure, match="blocked"):
        run(t.run({"x": "hi"}, ctx()))
    assert time.monotonic() - t0 < 6 and calls == []  # 到点整组收掉
    home.cfg({"tool:before": [home.hook("sleep", timeout=1)]})  # 不是 fail_closed：超时了照常做
    home.approve_all()
    [t] = U.wrap([echo_tool(calls)], "main")
    assert run(t.run({"x": "hi"}, ctx())) == "echo hi"
    for mode in ("garbage", "crash"):
        home.cfg({"tool:before": [home.hook(mode, fail_closed=True)]})
        home.approve_all()
        [t] = U.wrap([echo_tool(calls)], "main")
        with pytest.raises(engine.ToolFailure):
            run(t.run({"x": "hi"}, ctx()))
        home.cfg({"tool:before": [home.hook(mode)]})
        home.approve_all()
        [t] = U.wrap([echo_tool(calls)], "main")
        assert run(t.run({"x": "hi"}, ctx())) == "echo hi"


def test_the_environment_is_minimal(home, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "should-not-leak")
    monkeypatch.setenv("SOME_SECRET_TOKEN", "should-not-leak")
    monkeypatch.setenv("MOUSSE_TOKEN", "should-not-leak")
    home.cfg({"session:start": [home.hook()]})
    home.approve_all()
    run(U.fire("session:start", U.base_payload("session:start", "main", "main"), agent="main"))
    env = home.lines()[0]["env"]
    assert "DEEPSEEK_API_KEY" not in env and "SOME_SECRET_TOKEN" not in env and "MOUSSE_TOKEN" not in env
    assert "MOUSSE_HOOK_EVENT" in env and set(env) <= set(__import__("engine.mcp_client", fromlist=["x"]).SAFE_ENV) | {"MOUSSE_HOOK_EVENT", "PYTHONDONTWRITEBYTECODE"} | {
        k for k in env if k.startswith("XDG_")}


def test_tool_after_sees_the_result(home):
    home.cfg({"tool:after": [home.hook()]})
    home.approve_all()
    calls: list = []
    [t] = U.wrap([echo_tool(calls)], "main")

    async def go():
        assert await t.run({"x": "after"}, ctx()) == "echo after"
        await until(lambda: home.lines())
    run(go())
    p = home.lines()[0]["payload"]
    assert p["event"] == "tool:after" and p["ok"] is True and p["tool_response"] == "echo after" and p["tool_input"] == {"x": "after"}


def test_no_hooks_no_wrapping(home):
    t = echo_tool([])
    assert U.wrap([t], "main")[0] is t


# —— 别的事件 ——————————————————————————————————————————————————————————————————

def test_session_message_reply_and_startup_events(home, fake_model):
    import chat
    every = {ev: [home.hook()] for ev in ("startup", "session:start", "session:end", "message:received", "reply:sent")}
    home.cfg(every)
    home.approve_all()
    fake_model([{"text": "first"}, {"text": "second"}])
    thread = "t-hk-" + os.urandom(3).hex()

    async def go():
        U.start()
        await until(lambda: any(x["event"] == "startup" for x in home.lines()))
        await owner.respond(thread, "2026-10-18", "hello there", None, lambda t: None)
        await owner.respond(thread, "2026-10-19", "next day", None, lambda t: None)  # 新的一天：前一天那条 session:end
        await until(lambda: sum(x["event"] == "session:start" for x in home.lines()) == 2 and any(x["event"] == "session:end" for x in home.lines()))
    run(go())
    evs = [x["event"] for x in home.lines()]
    assert evs.count("session:start") == 2 and evs.count("session:end") == 1
    end = next(x for x in home.lines() if x["event"] == "session:end")["payload"]
    assert end["session"] == DbSession.latest(day_key(thread, "2026-10-18")).id

    async def new_and_resume():  # /new、/resume 也是一段结束、一段开始
        before = DbSession.latest(day_key(thread, "2026-10-19"))
        s, old = await owner.start_new(thread, "2026-10-19", agent="main", model="deepseek/deepseek-flash", reason="new")
        await until(lambda: sum(x["event"] == "session:start" for x in home.lines()) == 3)
        await owner.resume_from(thread, "2026-10-19", before, agent="main", model="deepseek/deepseek-flash")
        await until(lambda: sum(x["event"] == "session:end" for x in home.lines()) == 3 and sum(x["event"] == "session:start" for x in home.lines()) == 4)
        return s, old
    s, old = run(new_and_resume())
    ends = [x["payload"] for x in home.lines() if x["event"] == "session:end"]
    starts = [x["payload"] for x in home.lines() if x["event"] == "session:start"]
    assert (ends[1]["session"], ends[1]["reason"]) == (old.id, "new") and starts[2]["session"] == s.id
    assert (ends[2]["session"], ends[2]["reason"]) == (s.id, "resume")  # 结束的是 /new 开的那条，不是挑的那条
    assert [x["reason"] for x in starts] == ["day", "day", "new", "resume"]

    async def chat_turn():
        chat.start_run("t-hk-chat", "just a message", None)
        await until(lambda: (r := chat.RUNS.get("t-hk-chat")) is not None and r.done)
        await until(lambda: any(x["event"] == "reply:sent" for x in home.lines()))
        chat.RUNS.pop("t-hk-chat", None)
    fake_model([{"text": "the reply"}])
    run(chat_turn())
    got = {x["event"]: x["payload"] for x in home.lines()}
    assert got["message:received"]["text"] == "just a message" and got["message:received"]["from"] == "owner" and got["message:received"]["trusted"]
    assert got["reply:sent"]["text"] == "the reply"


def test_message_received_can_stop_a_message(home, fake_model):
    import chat
    home.cfg({"message:received": [home.hook("block")]})
    home.approve_all()
    f = fake_model([{"text": "should not be asked"}])

    async def go():
        chat.start_run("t-hk-block", "please do it", None)
        await until(lambda: (r := chat.RUNS.get("t-hk-block")) is not None and r.done)
        r = chat.RUNS.pop("t-hk-block")
        return r.text
    text = run(go())
    assert "A hook stopped this message" in text and "not on my watch" in text and f.requests == []


def test_a_line_sent_while_the_hook_runs_is_not_left_hanging(home, fake_model):
    """message:received 的钩子还在跑的时候又说了一句（插话进了这一轮）：钩子拦下了这一轮，那一句照样排回去、回完变成下一轮，不会一直挂着。"""
    import chat
    from test_engine_events import collect
    home.cfg({"message:received": [home.hook("slow-block")]})
    home.approve_all()
    f = fake_model([{"text": "About the dentist: noted."}])

    async def go():
        r = chat.start_run("t-hk-steer", "please do it", None)
        await until(lambda: r.control is not None)
        assert not r.done and r.control.can_steer()  # 钩子在跑（睡 1 秒）
        resp = await chat.send(chat.SendBody(thread="t-hk-steer", text="dentist at 3"))
        return r, await collect(resp.body_iterator)
    r, second = run(go())
    assert second[0][0] == "queued" and second[0][1]["steer"] is True
    assert "A hook stopped this message" in r.text and dict(second)["done"]["text"] == "About the dentist: noted."
    assert len(f.requests) == 1 and "dentist at 3" in text_of(f.requests[0][1].messages[-1])
    chat.RUNS.pop("t-hk-steer", None)


def test_compact_events_wrap_the_compaction(home):
    from engine import compact
    assert getattr(compact.compact, "__wrapped__", None) is not None
    home.cfg({"compact:before": [home.hook()], "compact:after": [home.hook()]})
    home.approve_all()

    async def fake(session, spec, tid, **kw):
        return "new system"
    wrapped = U.compacting(fake)

    async def go():
        out = await wrapped(SimpleNamespace(id="s-1"), SimpleNamespace(agent="main", conversation="main"), "t-1", reason="threshold", before=9000)
        await until(lambda: len(home.lines()) == 2)
        return out
    assert run(go()) == "new system"
    got = {x["event"]: x["payload"] for x in home.lines()}
    assert got["compact:before"]["tokens_before"] == 9000 and got["compact:after"]["ok"] is True


# —— 自带的两个 ——————————————————————————————————————————————————————————————————

def test_bundled_hooks_are_off_until_turned_on(home, fake_model, monkeypatch):
    (home.ws / "BOOT.md").write_text("Check the inbox and say hi.\n", encoding="utf8")
    booted: list = []
    real = U.boot_all

    async def spy():
        booted.append(1)
        return await real()
    monkeypatch.setattr(U, "boot_all", spy)

    async def go():
        U.start()
        await asyncio.sleep(0.3)
    run(go())
    assert booted == [] and not (home.data / "logs" / "commands.jsonl").exists()
    run(U.fire("session:start", U.base_payload("session:start", "main", "main"), agent="main"))
    assert not (home.data / "logs" / "commands.jsonl").exists()
    home.cfg({"bundled": {"boot-md": True, "command-logger": True}})
    f = fake_model([{"text": "NO_REPLY"}])
    out = run(real())
    assert out and out[0]["agent"] == "main" and out[0]["status"] == "silent"
    asked = f.requests[0][1].messages[-1]
    assert "Check the inbox and say hi." in text_of(asked) and "BOOT.md" in text_of(asked)
    run(U.fire("session:start", U.base_payload("session:start", "main", "main"), agent="main"))
    run(U.fire("message:received", U.base_payload("message:received", "main", "main", text="/new please", **{"from": "owner"}), agent="main"))
    rows = [json.loads(x) for x in (home.data / "logs" / "commands.jsonl").read_text(encoding="utf8").splitlines()]
    assert [r["action"] for r in rows] == ["session:start", "command:new"] and rows[1]["source"] == "owner"


def test_cli_lists_and_approves(home, capsys):
    import argparse

    import hooks_ctl
    home.cfg({"tool:before": [home.hook("log", matcher="echo")]})
    ap = argparse.ArgumentParser()
    hooks_ctl.register(ap.add_subparsers(dest="cmd"))

    def cli(*argv) -> int:
        a = ap.parse_args(["hooks", *argv])
        return a.run(a)
    assert cli("list") == 0 and "waiting" in capsys.readouterr().out
    fp = U.hooks("tool:before")[0].fingerprint()
    assert cli("approve", fp[:10]) == 0 and U.approved(U.hooks("tool:before")[0])
    assert cli("test", "tool:before", "--tool", "echo", "--payload", '{"tool_input": {"x": "1"}}') == 0
    assert home.lines()[0]["payload"]["tool_input"] == {"x": "1"}
    assert cli("revoke", fp[:10]) == 0 and not U.approved(U.hooks("tool:before")[0])
    assert Path(home.data / "hooks" / "allowlist.json").stat().st_mode & 0o777 == 0o600


@pytest.fixture
def fake_model(monkeypatch):
    from engine import providers

    def make(script):
        f = FakeModel(script)
        monkeypatch.setattr(providers, "stream", f.stream)
        return f
    return make


def test_the_fingerprint_follows_every_file_the_hook_runs(home, monkeypatch):
    """指纹算的是真跑的那些文件：选项后面的脚本、env 开头的、裸名字（按工作目录）、argv 里别的代码文件；日志这类数据文件不算；
    工作区的钩子整个文件夹都算（旁边放个 json.py 也要重新同意）。"""
    lib = home.tmp / "lib.sh"
    lib.write_text("echo one\n", encoding="utf8")
    cases = {"option first": [sys.executable, "-u", str(home.script), str(home.log)],
             "env prefix": ["/usr/bin/env", "FOO=1", sys.executable, str(home.script)],
             "bare name, cwd": [sys.executable, "hook.py"]}
    for why, argv in cases.items():
        h = U.Hook(event="session:start", argv=argv, cwd=str(home.tmp))
        assert h.script() == home.script, why
        fp = h.fingerprint()
        home.log.write_text("a log line\n", encoding="utf8")  # 数据文件变了：指纹不变
        assert h.fingerprint() == fp, why
        home.script.write_text(SCRIPT + f"\n# {why}\n", encoding="utf8")
        assert h.fingerprint() != fp, why
    h = U.Hook(event="session:start", argv=[sys.executable, str(home.script), "--source", str(lib)])
    fp = h.fingerprint()
    lib.write_text("echo two\n", encoding="utf8")  # argv 里的另一个脚本也算
    assert h.fingerprint() != fp
    ws = home.tmp / "ws-g"
    (ws / "hooks" / "greet").mkdir(parents=True)
    (ws / "hooks" / "greet" / "HOOK.md").write_text("---\nname: greet\nevents: [session:start]\n---\n", encoding="utf8")
    (ws / "hooks" / "greet" / "handler.py").write_text("import json\n", encoding="utf8")
    monkeypatch.setattr(type(config.settings), "agent_workspaces", property(lambda self: {"g-hooks": ws}))
    home.cfg({"workspace": True})
    [h] = U.hooks("session:start", "g-hooks")
    fp = h.fingerprint()
    (ws / "hooks" / "greet" / "json.py").write_text("import os\n", encoding="utf8")  # handler.py 的 import json 会先找到它
    [h2] = U.hooks("session:start", "g-hooks")
    assert h2.fingerprint() != fp and not h2.error
    monkeypatch.setattr(U, "FOLDER_MAX", 2)
    [h3] = U.hooks("session:start", "g-hooks")
    assert "Too many files" in h3.error
    assert U.env_for("session:start")["PYTHONDONTWRITEBYTECODE"] == "1"
