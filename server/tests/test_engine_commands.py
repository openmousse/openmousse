"""第 2 批：斜杠命令（engine/commands.py，入口在 ../chat.py 的 command_reply）。模型是假的（FakeModel），不连真的模型、不花钱。"""
from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

import pytest

import bridges
from engine import commands, owner, providers
from engine.session import DbSession, day_key, text_of
from test_engine import FakeModel

MODEL = "deepseek/deepseek-flash"
ON = {"claw": {"kind": "builtin"}, "engine": {"model": MODEL, "commands": True}}
OFF = {"claw": {"kind": "builtin"}, "engine": {"model": MODEL}}


def run(coro):
    return asyncio.run(coro)


async def until(cond, timeout: float = 5.0) -> None:
    t0 = time.monotonic()
    while not cond():
        if time.monotonic() - t0 > timeout:
            raise AssertionError("timed out waiting")
        await asyncio.sleep(0.01)


async def events(gen, timeout: float = 10.0) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []

    async def go() -> None:
        async for chunk in gen:
            for block in chunk.decode().split("\n\n"):
                if not block.strip():
                    continue
                ev, data = "message", {}
                for ln in block.split("\n"):
                    if ln.startswith("event:"):
                        ev = ln[6:].strip()
                    elif ln.startswith("data:"):
                        data = json.loads(ln[5:].strip())
                out.append((ev, data))
                if ev == "done":
                    return
    await asyncio.wait_for(go(), timeout)
    return out


@pytest.fixture
def fake(monkeypatch):
    def make(script):
        f = FakeModel(script)
        monkeypatch.setattr(providers, "stream", f.stream)
        return f
    return make


@pytest.fixture
def tools(monkeypatch):
    """假的命令行：每个调用等 delay 秒。"""
    b = SimpleNamespace(delay=0.0)

    async def fake_run(name, args, stdin, agent, *, kill_on_cancel=False):
        await asyncio.sleep(b.delay)
        return f"ok: {name}"
    monkeypatch.setattr(bridges, "run", fake_run)
    return b


def rows(thread: str) -> list[dict]:
    import chat
    with chat._lock, chat.db() as conn:
        return [dict(r) for r in conn.execute("SELECT id, role, text, status, origin FROM messages WHERE thread=? ORDER BY id", (thread,))]


async def say(thread: str, text: str) -> list[tuple[str, dict]]:
    import chat
    resp = await chat.send(chat.SendBody(thread=thread, text=text))
    return await events(resp.body_iterator)


# —— 解析、开关、只认主人 ————————————————————————————————————————————————————————————

@pytest.mark.parametrize("text, want", [
    ("/help", ("help", "")),
    ("/Model   opus", ("model", "opus")),
    ("／status", ("status", "")),
    ("/btw what time is it\nand where", ("btw", "what time is it\nand where")),
    ("  /new  carry this  ", ("new", "carry this")),
    ("/path/to/file is broken", None),
    ("/12 apples", None),
    ("hello /help", None),
    ("/", None),
])
def test_parse(text, want):
    assert commands.parse(text) == want


def test_off_by_default_and_only_for_the_owner(server_json):
    server_json(OFF)
    assert not commands.enabled() and run(commands.handle("main", "/help")) is None
    server_json(ON)
    assert commands.enabled() and run(commands.handle("main", "/help", who="friend")) is None  # 别人的话里的 /xxx 只是文字
    assert run(commands.handle("main", "plain words")) is None
    server_json({**ON, "claw": {"kind": "openclaw"}})
    assert not commands.enabled()  # OpenClaw 那条路没有


def test_when_off_a_slash_message_still_goes_to_the_model(server_json, fake, tools):
    server_json(OFF)
    f = fake([{"text": "A slash, how fun."}])
    evs = run(say("t-cmd-off", "/help"))
    assert evs[-1][0] == "done" and evs[-1][1]["text"] == "A slash, how fun." and len(f.requests) == 1
    assert text_of(f.requests[0][1].messages[-1]).endswith("/help")


# —— 走 app 的入口：记进对话、不进会话、不调模型 ——————————————————————————————————————————

def test_a_command_is_answered_without_the_model_and_kept_out_of_the_session(server_json, fake, tools):
    server_json(ON)
    f = fake([])
    evs = run(say("t-cmd-status", "/status"))
    names = [e for e, _ in evs]
    assert names == ["start", "delta", "done"]
    done = evs[-1][1]
    assert "Not running" in done["text"] and MODEL in done["text"] and done["status"] == "ok"
    assert f.requests == []  # 没调模型
    got = rows("t-cmd-status")
    assert [(r["role"], r["origin"]) for r in got] == [("user", "command"), ("grava", "command")]
    assert got[0]["text"] == "/status" and evs[0][1]["userId"] == f"db{got[0]['id']}" and done["id"] == f"db{got[1]['id']}"
    import chat
    assert DbSession.latest(day_key("t-cmd-status", chat.day_of(chat.now_iso()))) is None  # 没开会话


def test_unknown_commands_are_not_sent_to_the_model(server_json, fake, tools):
    server_json(ON)
    f = fake([])
    evs = run(say("t-cmd-unknown", "/frobnicate now"))
    assert "/frobnicate" in evs[-1][1]["text"] and "/help" in evs[-1][1]["text"] and f.requests == []


def test_commands_run_while_a_reply_is_running_and_stop_stops_it(server_json, fake, tools):
    import chat
    server_json(ON)
    tools.delay = 5.0
    fake([{"text": "Checking.", "calls": [("c1", "schedule", {"args": ["day"]})]}])

    async def go():
        r = chat.start_run("t-cmd-stop", "what's on", None)
        await until(lambda: r.progress and any(s.get("status") == "running" for s in r.progress.get("steps") or []))
        status = await say("t-cmd-stop", "/status")
        stop = await say("t-cmd-stop", "/stop")
        await until(lambda: r.done)
        again = await say("t-cmd-stop", "/stop")
        return r, status, stop, again
    r, status, stop, again = run(go())
    assert "Running" in status[-1][1]["text"] and "schedule" in status[-1][1]["text"]
    assert stop[-1][1]["text"] == "Stopped." and r.status == "stopped"
    assert again[-1][1]["text"] == "Nothing is running."


def test_idle_only_commands_wait_for_the_turn(server_json, fake, tools):
    import chat
    server_json(ON)
    tools.delay = 5.0
    fake([{"text": "Checking.", "calls": [("c1", "schedule", {"args": ["day"]})]}])
    hits: list[str] = []

    async def touch(c: commands.Call) -> str:
        hits.append(c.args)
        return "touched"
    commands.register(commands.Command("touch", touch, ("改会话", "Change the session"), idle=True, source="test"))
    try:
        async def go():
            r = chat.start_run("t-cmd-idle", "what's on", None)
            await until(lambda: r.progress and any(s.get("status") == "running" for s in r.progress.get("steps") or []))
            busy = await say("t-cmd-idle", "/touch it")
            await chat.stop(chat.StopBody(thread="t-cmd-idle"))
            await until(lambda: r.done)
            free = await say("t-cmd-idle", "/touch it")
            return busy, free
        busy, free = run(go())
    finally:
        commands.unregister("touch")
    assert "/stop" in busy[-1][1]["text"] and free[-1][1]["text"] == "touched" and hits == ["it"]


# —— 第一批命令的样子 ——————————————————————————————————————————————————————————————————

def test_help_lists_the_commands(server_json):
    server_json({**ON, "engine": {**ON["engine"], "commands": {"off": ["usage"]}}})
    out = run(commands.handle("main", "/help")).text
    for name in ("/help", "/status", "/context", "/tools", "/model", "/stop"):
        assert f"`{name}" in out
    assert "/usage" not in out and "only what you type yourself counts" in out
    assert run(commands.handle("main", "/usage")).ok is False  # 关掉的那几条当不认识


def test_context_and_tools(server_json, tools, monkeypatch):
    from engine import tools as tools_mod
    server_json(ON)
    monkeypatch.setattr(owner.tools_mod, "for_agent", lambda agent: [tools_mod.bridge_tool("schedule", agent or "main")])
    ctx = run(commands.handle("t-cmd-context", "/context")).text  # 归 main 的一个新对话：今天还没有会话
    assert "Opening:" in ctx and "Tools:" in ctx and "window" in ctx and "nothing yet today" in ctx
    tl = run(commands.handle("t-cmd-context", "/tools")).text
    assert "`schedule`" in tl and "`todo`" in tl and "`ask_user`" in tl  # 和 owner.respond 给的一样：for_agent + app 对话的那几样


def test_model_switches_this_chat(server_json):
    import chat
    server_json({**ON, "engine": {**ON["engine"], "models": [MODEL, "deepseek/deepseek-v4-pro"]}})
    out = run(commands.handle("t-cmd-model", "/model"))
    assert "deepseek/deepseek-v4-pro" in out.text
    out = run(commands.handle("t-cmd-model", "/model v4-pro"))
    assert out.ok and "now uses `deepseek/deepseek-v4-pro`" in out.text
    with chat._lock, chat.db() as conn:
        assert chat.thread_model(conn, "t-cmd-model") == "deepseek/deepseek-v4-pro"
    bad = run(commands.handle("t-cmd-model", "/model gpt-9"))
    assert not bad.ok and "There's no model `gpt-9`" in bad.text


def test_usage_counts_this_chat(server_json, fake, tools):
    server_json(ON)
    fake([{"text": "Hi."}])
    out: list[str] = []
    import chat
    run(owner.turn("t-cmd-usage", chat.day_of(chat.now_iso()), "hello", None, out.append))
    u = run(commands.handle("t-cmd-usage", "/usage")).text
    assert "This chat today: 1 turns" in u and "Last 7 days" in u


# —— 登记表能从外面加 ———————————————————————————————————————————————————————————————————

def test_other_modules_can_register_commands(server_json):
    server_json(ON)

    async def ping(c: commands.Call) -> commands.Reply:
        return commands.Reply(f"pong {c.args} from {c.agent}")
    commands.register(commands.Command("ping", ping, ("测一下", "Ping"), args=("[字]", "[text]"), aliases=("pg",), source="test"))
    commands.add_source("skills-test", lambda agent: [commands.Command("weekly-review", ping, ("技能", "A skill"), source="skill")])
    try:
        assert run(commands.handle("main", "/pg hi")).text == "pong hi from main"
        assert run(commands.handle("main", "/weekly-review")).text == "pong  from main"
        names = [c.name for c in commands.listed("main")]
        assert "ping" in names and "weekly-review" in names
        with pytest.raises(ValueError):
            commands.register(commands.Command("pong", ping, ("x", "x"), aliases=("help",)))  # 别名不能撞
        listed = commands.commands_list(thread="main")["commands"]
        assert {"name": "ping", "aliases": ["pg"], "args": "[text]", "description": "Ping", "idle": False, "source": "test"} in listed
    finally:
        commands.unregister("ping")
        commands.remove_source("skills-test")
    server_json(OFF)
    assert commands.commands_list(thread="main") == {"ok": True, "commands": []}


# —— 命令那几行不进新会话、不进「上次聊到哪」——————————————————————————————————————————————————

def test_command_rows_stay_out_of_seed_and_tail(server_json):
    import chat
    from engine import context
    server_json(ON)
    thread = "t-cmd-rows"
    with chat._lock, chat.db() as conn:
        for role, text, origin, ts in (("user", "plan the week", None, "2026-10-18T20:00:00+00:00"),
                                       ("grava", "Here's a plan.", "user", "2026-10-18T20:01:00+00:00"),
                                       ("user", "/status", "command", "2026-10-18T20:02:00+00:00"),
                                       ("grava", "**t** · not running", "command", "2026-10-18T20:02:01+00:00"),
                                       ("user", "/btw what's a fortnight", "command", "2026-10-19T08:00:00+00:00"),
                                       ("grava", "(Side question) Two weeks.", "btw", "2026-10-19T08:00:05+00:00")):
            last = conn.execute("INSERT INTO messages(thread, role, text, ts, status, origin) VALUES(?,?,?,?,?,?)",
                                (thread, role, text, ts, "ok", origin)).lastrowid
    tail = context.tail_part(thread, "2026-10-19").text
    assert "plan the week" in tail and "/status" not in tail and "not running" not in tail
    assert owner.seed(thread, "2026-10-19", last + 1) == []  # 当天只有 /btw 那两行：不抄
