"""10/19 彩排补的第 1 件：主人回合交给 Claude Code 跑时的退路（engine/claude_brain.py 的 kind_of / switch、engine/fallback.backup、
setup_api.no_model、runners.checking）。

假的 claude 是 tests/fake_claude.py（它自己编的那条 <synthetic> 回复、读标准输入之前就退出、命令不在了都能演），换上的模型是 FakeModel，
不连真的模型、不花钱。"""
from __future__ import annotations

import asyncio
import json
import time

import pytest

import engine
from engine import claude_brain, events, models, runners, setup_api
from engine.session import DbSession, day_key, text_of
from test_engine_claude_code import cc, collect, finish, serving  # noqa: F401 — cc 是那边的 fixture（假的 claude + /mcp），这里经 claude 拿

pytest.importorskip("mcp")

CHAIN = {"claw": {"kind": "builtin"}, "engine": {"model": "claude-code/opus", "fallbacks": ["deepseek/deepseek-flash"]},
         "tasks": {"runners": {"codex": False}}}
SIGNED_OUT = {"api_error": "Not logged in · Please run /login", "error": "authentication_failed"}
LIMIT = {"api_error": "You've hit your session limit · resets 3pm", "error": "rate_limit"}
OVERLOADED = {"api_error": "API Error: Repeated 529 Overloaded errors", "error": "server_error"}


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def claude(request):
    """test_engine_claude_code 的 cc：假的 claude（登录了、套餐）+ 本机的 /mcp + 假的命令行工具 + 假模型。"""
    return request.getfixturevalue("cc")


@pytest.fixture
def chain(claude, server_json, monkeypatch):
    """claude + 备用链（engine.fallbacks 里一个按量的模型，给它一把假密钥）。"""
    server_json(CHAIN)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-deepseek-key")
    return claude


async def said(thread: str, text: str, model: str = "claude-code/opus"):
    """在 app 的对话里说一句（经 chat.start_run，和 app 一样），等它回完 → (Run, 收到的 SSE 事件)。要在 serving 里面调。"""
    import chat
    r = chat.start_run(thread, text, model)
    evs = await collect(chat.attach(r))
    await finish(chat, thread)
    return r, evs


def chat_turn(st, thread: str, text: str, model: str = "claude-code/opus"):
    async def go():
        async with serving(st):  # /mcp 的端口一个测试只能起一次
            return await said(thread, text, model)
    return run(go())


# —— 哪一类 ————————————————————————————————————————————————————————————————————————

@pytest.mark.parametrize("detail,errors,stderr,kind", [
    ("Not logged in · Please run /login", ["authentication_failed"], "", "auth"),
    ("OAuth token revoked · Please run /login", [], "", "auth"),
    ("API Error: 401 {\"type\":\"error\"} · Please run /login", ["authentication_failed"], "", "auth"),
    ("You've hit your session limit · resets 3pm", ["rate_limit"], "", "quota"),
    ("You've hit your weekly limit · resets Oct 20, 3pm", ["rate_limit"], "", "quota"),
    ("You're out of extra usage · resets 3pm", ["rate_limit"], "", "quota"),
    ("Credit balance is too low", ["billing_error"], "", "quota"),
    ("", [], "error: unknown option '--permission-prompts'\n", "broken"),
    ("There's an issue with the selected model (claude-fake-x). It may not exist or you may not have access to it.", ["invalid_request"], "",
     "broken"),
    ("API Error: Repeated 529 Overloaded errors", ["server_error"], "", "http"),
    ("API Error: Rate limit reached", ["rate_limit"], "", "http"),
    ("Request timed out", ["unknown"], "", "http"),
    ("Opus is experiencing high load, please use /model to switch to Sonnet", ["rate_limit"], "", "http"),
])
def test_what_kind_of_failure(detail, errors, stderr, kind):
    t = claude_brain.Turn(tid="t", conversation="c", model="claude-code/opus", tools={}, emit=lambda e: None, on_text=lambda s: None,
                          control=engine.Control(), api_errors=list(errors), notices=[detail] if detail else [])
    t.stderr = stderr
    assert t.kind_of(detail) == kind
    t.api_errors = []  # 不标 error 的（老一点的版本）：照字眼也认得出
    assert t.kind_of(detail) == kind


# —— 换不换 ————————————————————————————————————————————————————————————————————————

def test_signed_out_switches_to_the_backup_and_the_app_marks_it(chain):
    import chat
    model = chain.fake_model([{"text": "Breakfast is logged."}])
    chain.scenario_([[SIGNED_OUT]])
    r, evs = chat_turn(chain, "t-ccfb-auth", "oats and two eggs for breakfast")
    done = dict(evs)["done"]
    assert r.status == "ok" and done["text"] == "Breakfast is logged." and "/login" not in r.text  # 它编的那条不当回复
    assert done["modelId"] == "deepseek/deepseek-flash" and done["fallbackFrom"] == "claude-code/opus"  # app 照它标出「换成了 …」
    assert [x[0] for x in model.requests] == ["deepseek/deepseek-flash"]
    asked = [m for m in model.requests[0][1].messages if m["role"] == "user"]
    assert [text_of(m) for m in asked] == ["oats and two eggs for breakfast"]  # 从头答这一句，只有一遍
    s = DbSession.latest(day_key("t-ccfb-auth", chat.day_of(r.started)))
    assert [(m["role"], text_of(m)) for m in s.messages()] == [("user", "oats and two eggs for breakfast"), ("assistant", "Breakfast is logged.")]
    ts = sorted(events.turns("t-ccfb-auth"), key=lambda t: t["started_at"])
    assert [(t["model"], t["status"]) for t in ts] == [("claude-code/opus", "error"), ("deepseek/deepseek-flash", "done")]
    assert "signed out" in ts[0]["error"]  # 事件表里看得出为什么换了


def test_plan_limit_switches_and_the_backup_keeps_tools_and_session(chain):
    chain.replies["diet"] = "logged: oats, eggs"
    model = chain.fake_model([{"calls": [("c1", "diet", {"args": ["add", "oats"]})]}, {"text": "Done: oats and eggs."}])
    chain.scenario_([[LIMIT]])
    r, _ = chat_turn(chain, "t-ccfb-quota", "log my breakfast")
    assert r.status == "ok" and r.text == "Done: oats and eggs." and r.model == "deepseek/deepseek-flash"
    assert chain.calls == [("diet", ["add", "oats"], "main")]  # 换上的模型照样有这个 Agent 的工具和关卡
    assert len(model.requests) == 2


def test_command_that_wont_run_switches(chain, monkeypatch, tmp_path):
    chain.fake_model([{"text": "From the backup."}, {"text": "From the backup again."}])
    chain.scenario_([[{"die": "error: unknown option '--permission-prompts'\n", "exit": 1}]])  # 版本不对：认不得我们的参数
    gone = tmp_path / "removed" / "claude"

    async def go():
        async with serving(chain):
            r1, _ = await said("t-ccfb-old", "hi")
            monkeypatch.setattr(runners, "_found", {"claude-code": {"bin": str(gone), "auth": "claude.ai", "billing": "subscription"}})
            r2, _ = await said("t-ccfb-gone", "hi again")  # 查过以后命令不在了
            return r1, r2
    r, r2 = run(go())
    assert r.status == "ok" and r.text == "From the backup." and r.model == "deepseek/deepseek-flash"
    assert "unknown option" in events.turns("t-ccfb-old")[-1]["error"]
    assert r2.status == "ok" and r2.text == "From the backup again."
    assert "won't run" in events.turns("t-ccfb-gone")[-1]["error"]


def test_not_installed_or_signed_out_before_starting_switches(chain, server_json, monkeypatch, tmp_path):
    """还没起它就知道跑不了（runners 查不到）：一样换；没装 = broken，装了没登录 = auth。"""
    from engine import owner
    chain.fake_model([{"text": "one"}, {"text": "two"}])
    monkeypatch.setattr(runners, "_found", {})
    server_json({**CHAIN, "tasks": {"runners": {"codex": False, "claude-code": {"bin": str(tmp_path / "nope")}}}})
    a = run(owner.respond("t-ccfb-none", "2026-10-19", "hi", "claude-code/opus", lambda t: None))
    server_json({**CHAIN, "tasks": {"runners": {"codex": False, "claude-code": {"bin": str(chain.exe)}}}})
    monkeypatch.setenv("FAKE_CLAUDE_AUTH", json.dumps({"loggedIn": False, "authMethod": "none"}))
    b = run(owner.respond("t-ccfb-out", "2026-10-19", "hi", "claude-code/opus", lambda t: None))
    assert (a.text, a.model, a.models) == ("one", "deepseek/deepseek-flash", ["claude-code/opus", "deepseek/deepseek-flash"])
    assert (b.text, b.model) == ("two", "deepseek/deepseek-flash") and chain.records("start") == []
    assert "won't run" in events.turns("t-ccfb-none")[-1]["error"] and "signed out" in events.turns("t-ccfb-out")[-1]["error"]


def test_temporary_errors_do_not_switch(chain):
    model = chain.fake_model([{"text": "never"}])
    chain.scenario_([[OVERLOADED]])
    r, evs = chat_turn(chain, "t-ccfb-529", "hi")
    assert r.status == "error" and "Repeated 529 Overloaded errors" in r.error and model.requests == []
    assert dict(evs)["done"]["fallbackFrom"] is None


def test_no_switch_once_words_or_tools_reached_the_user(chain):
    model = chain.fake_model([{"text": "never"}])
    chain.scenario_([[{"say": "Let me look."}, SIGNED_OUT], [{"tool": "schedule", "input": {"args": ["day"]}}, LIMIT]])

    async def go():
        async with serving(chain):
            return (await said("t-ccfb-said", "what's on?"))[0], (await said("t-ccfb-tool", "what's on?"))[0]
    r, r2 = run(go())
    assert r.status == "error" and r.text.startswith("Let me look.") and "sign in again" in r.error  # 出过字：照旧报错（和备用链一样）
    assert r2.status == "error" and "wait until it resets" in r2.error and chain.calls  # 调过工具：可能已经做了事，不从头再答
    assert model.requests == []


def test_without_a_chain_the_error_says_what_to_do(claude, server_json):
    """没配备用链：和以前一样报错，但说清是哪一类、用户该做什么。"""
    from engine import owner
    server_json({**CHAIN, "engine": {"model": "claude-code/opus"}})
    claude.scenario_([[SIGNED_OUT], [LIMIT], [{"die": "error: unknown option '--input-format'\n", "exit": 1}]])

    async def go():
        async with serving(claude):
            return [await owner.respond("t-ccfb-nochain", "2026-10-19", f"hi {i}", "claude-code/opus", lambda t: None) for i in range(3)]
    auth, quota, broken = run(go())
    assert [x.status for x in (auth, quota, broken)] == ["error"] * 3 and [x.error_kind for x in (auth, quota, broken)] == ["auth", "quota", "broken"]
    assert "signed out" in auth.error and "sign in again" in auth.error and "pick another model for this chat in the app" in auth.error
    assert "usage limit is used up" in quota.error and "resets 3pm" in quota.error and "wait until it resets" in quota.error
    assert "won't run" in broken.error and "unknown option" in broken.error and "update or reinstall" in broken.error
    s = DbSession.latest(day_key("t-ccfb-nochain", "2026-10-19"))
    assert [text_of(m) for m in s.messages() if m["role"] == "user"] == ["hi 0", "hi 1", "hi 2"]  # 没换：这一轮照旧留在会话里


def test_scheduled_runs_switch_too(chain):
    from engine import unattended
    chain.fake_model([{"text": "Morning summary."}])
    chain.scenario_([[SIGNED_OUT]])

    async def go():
        async with serving(chain):
            return await unattended.fresh("main", "main", "cron:j-ccfb", "summarise my morning", model="claude-code/haiku", light=True)
    out = run(go())
    assert out.status == "done" and out.text == "Morning summary." and out.model == "claude-code/haiku" and out.served == "deepseek/deepseek-flash"


def test_a_steer_that_came_in_meanwhile_goes_to_the_backup():
    """换模型时这一轮没读到的插话放回 Control：换上的那一轮接着用（停照旧）。"""
    c = engine.Control()
    assert c.steer({"role": "user", "content": [{"type": "text", "text": "also tea"}]})
    left = c.close()
    assert not c.can_steer()
    c.reopen(left)
    assert c.can_steer() and [text_of(m) for m in c.take_steers()] == ["also tea"]


def test_the_chain_can_be_the_main_models_own_list(server_json, monkeypatch):
    from engine import fallback
    server_json({"claw": {"kind": "builtin"}, "engine": {"model": ["claude-code/opus", "moonshot/kimi-k3", "chatgpt/gpt-fake", "deepseek/deepseek-flash"],
                                                         "fallbacks": ["anthropic/claude-sonnet-5-5"]}})
    assert models.default_model() == "claude-code/opus"
    assert models.chain_after("claude-code/opus") == ["moonshot/kimi-k3", "deepseek/deepseek-flash"]  # 它自己写的链，盖过默认的；套餐的去掉
    assert models.chain_after("claude-code/sonnet") == ["anthropic/claude-sonnet-5-5"]
    fallback.reset()
    monkeypatch.delenv("MOONSHOT_API_KEY", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    assert fallback.backup("claude-code/opus") == ["deepseek/deepseek-flash"]  # 没密钥的跳过
    monkeypatch.setenv("MOONSHOT_API_KEY", "k")
    assert fallback.backup("claude-code/opus") == ["moonshot/kimi-k3", "deepseek/deepseek-flash"]
    fallback.note("moonshot/kimi-k3", None, "billing")  # 在冷却的往后放
    assert fallback.backup("claude-code/opus") == ["deepseek/deepseek-flash", "moonshot/kimi-k3"]
    fallback.reset()


# —— no_model 和刚启动那几秒 ——————————————————————————————————————————————————————————————

def test_no_model_counts_a_usable_chain(server_json, monkeypatch):
    monkeypatch.setattr(runners, "_found", {})  # Claude Code 现在用不了
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    server_json({"claw": {"kind": "builtin"}, "engine": {"model": "claude-code/opus"}})
    assert setup_api.no_model()
    server_json(CHAIN)
    assert setup_api.no_model()  # 链上的也用不了（没密钥）
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    assert not setup_api.no_model()  # 主对话的模型暂时用不了、链上有能用的：不算「没有模型」
    server_json({"claw": {"kind": "builtin"}, "engine": {"model": ["claude-code/opus", "deepseek/deepseek-flash"]}})
    assert not setup_api.no_model()
    server_json({"claw": {"kind": "builtin"}, "engine": {"model": "moonshot/kimi-k3", "fallbacks": ["deepseek/deepseek-flash"]}})
    monkeypatch.delenv("MOONSHOT_API_KEY", raising=False)
    assert not setup_api.no_model()  # 按量的主模型没密钥也一样
    server_json({"claw": {"kind": "builtin"}, "engine": {}})
    assert setup_api.no_model()  # 没选模型：链再好也算没有


def test_the_first_seconds_after_start_count_claude_code_as_there(claude, server_json, monkeypatch):
    """服务刚起来、登录还在后台查：当它能用（app 别闪一下选模型的引导）；查完了照查到的算，过了 STARTUP_GRACE 也照实算。"""
    server_json({"claw": {"kind": "builtin"}, "engine": {"model": "claude-code/opus"},
                 "tasks": {"runners": {"codex": False, "claude-code": {"bin": str(claude.exe)}}}})
    from engine import owner
    monkeypatch.setattr(runners, "_refresh_bg", lambda: None)  # 后台那一次查不真的起
    monkeypatch.setattr(runners, "_found", {})
    monkeypatch.setattr(runners, "_checked_at", 0.0)
    monkeypatch.setattr(runners, "_warm", False)
    monkeypatch.setattr(runners, "_warm_at", 0.0)
    assert setup_api.no_model()  # 没 warm（测试、没起服务）：照查到的算
    runners.warm()
    assert runners.checking("claude-code") and not setup_api.no_model() and "claude-code/opus" in models.models()
    claude.scenario_([[{"say": "up and running"}]])

    async def go():
        async with serving(claude):
            return await owner.respond("t-ccfb-boot", "2026-10-19", "hi", "claude-code/opus", lambda t: None)
    assert run(go()).text == "up and running"  # 这几秒里来的一句：现查一次，照常跑
    monkeypatch.setattr(runners, "_found", {})  # 后台那一次查完了：没登录
    monkeypatch.setattr(runners, "_checked_at", time.monotonic())
    assert not runners.checking("claude-code") and setup_api.no_model()
    monkeypatch.setattr(runners, "_checked_at", 0.0)
    monkeypatch.setattr(runners, "_warm_at", time.monotonic() - runners.STARTUP_GRACE - 1)  # 一直没查完：过了时限照实算
    assert setup_api.no_model()
    monkeypatch.setattr(runners, "_warm_at", time.monotonic())
    server_json({"claw": {"kind": "builtin"}, "engine": {"model": "claude-code/opus"},
                 "tasks": {"runners": {"codex": False, "claude-code": {"bin": str(claude.exe.parent / "no-such-claude")}}}})
    assert setup_api.no_model()  # 这台电脑上根本没有这个命令：不用等
