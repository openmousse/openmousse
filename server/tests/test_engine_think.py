"""第 2 批：思考档位（engine/think.py，命令 /think 在 engine/commands.py）：按对话存、按 Agent 的默认、就近夹、主人回合用上
（各家接口的 effort、Claude Code 的 --effort）；都不写和以前一样。模型是假的（FakeModel、tests/fake_claude.py），不花钱。"""
from __future__ import annotations

import asyncio

import pytest

from engine import commands, owner, providers, think
from test_engine import FakeModel
from test_engine_claude_code import BUILTIN, serving

MODEL = "deepseek/deepseek-flash"
ON = {"claw": {"kind": "builtin"}, "engine": {"model": MODEL, "commands": True}}
ACME = {"acme": {"api": "openai", "url": "http://127.0.0.1:9/v1", "key": "test-key", "reasoning_effort": True}}


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def fake(monkeypatch):
    def make(script):
        f = FakeModel(script)
        monkeypatch.setattr(providers, "stream", f.stream)
        return f
    return make


def test_clamp_follows_hermes():
    assert think.clamp("xhigh", ("low", "medium", "high")) == "high"           # 比要的弱的里面最强的
    assert think.clamp("max", ("low", "medium", "high", "xhigh")) == "xhigh"
    assert think.clamp("low", ("high", "max")) == "high"                         # 没有更弱的：用最弱的
    assert think.clamp("minimal", ("none", "low", "high")) == "low"              # none 是关掉，不当弱一档
    assert think.clamp("medium", ("low", "high"), {"medium": "high"}) == "high"  # 写明的换法先看
    assert think.clamp("medium", None) == "medium" and think.clamp("medium", ()) == "medium" and think.clamp(None, ("low",)) is None
    assert think.clamp("turbo", ("low", "high")) == "turbo"                      # 不在梯子上的名字原样
    assert [think.normalize(w) for w in ("High", " extra  high ", "x-high", "maximum", "highest", "big", "", None)] == \
        ["high", "xhigh", "xhigh", "max", "high", None, None, None]


def test_supported_levels_by_route(server_json):
    server_json({**ON, "engine": {**ON["engine"], "providers": {**ACME, "acme2": {**ACME["acme"], "effort_map": {}},
                                                                 "acme3": {"api": "openai", "url": "http://127.0.0.1:9/v1", "key": "k"},
                                                                 "resp": {"api": "openai-responses", "url": "http://127.0.0.1:9/v1", "key": "k"},
                                                                 "resp2": {"api": "openai-responses", "url": "http://127.0.0.1:9/v1", "key": "k",
                                                                           "reasoning": False}}}})
    assert think.supported("anthropic/claude-opus-5-5") == think.LEVELS
    assert think.supported("anthropic/claude-sonnet-4-6") == ("low", "medium", "high")
    assert think.supported("anthropic/claude-opus-4-1") == ()            # 老型号：不带 effort
    assert think.supported("acme/some-model") == ("low", "medium", "high")  # 兼容接口默认 xhigh、max → high
    assert think.supported("acme2/some-model") == think.LEVELS
    assert think.supported("acme3/some-model") == ()                     # 没开 reasoning_effort：不带
    assert think.supported("resp/some-model") == ("low", "medium", "high", "xhigh")
    assert think.supported("resp2/some-model") == ()
    assert think.supported("claude-code/sonnet") == think.LEVELS and think.supported("claude-code/claude-sonnet-4-6") == ("low", "medium", "high")
    assert think.supported("nobody/x") is None                           # 不认识的 provider：不知道，原样发


def test_resolution_order_and_the_command(server_json):
    server_json({**ON, "engine": {**ON["engine"], "think": {"main": "high", "*": "low", "g-notes": "bogus"}}})
    assert think.resolve("t-think-a", "main", "anthropic/claude-opus-5-5") == {"want": "high", "from": "agent", "send": "high",
                                                                               "levels": think.LEVELS}
    assert think.agent_default("g-other") == "low" and think.agent_default("g-notes") == "low"  # 写错的档位不算
    out = run(commands.handle("t-think-a", "/think max", model="anthropic/claude-sonnet-4-6"))
    assert out.ok and "max (set for this chat)" in out.text and "sends high" in out.text  # 4.6 没有 max：按 high 发
    assert think.of_thread("t-think-a") == "max"
    assert think.effort_for("t-think-a", "main", "anthropic/claude-opus-5-5") == "max"  # 存着的是要的那一档：换回支持的模型照它发
    shown = run(commands.handle("t-think-a", "/think", model="anthropic/claude-opus-5-5")).text
    assert shown.startswith("This chat's thinking level: max (set for this chat)")
    assert "Thinking: max" in run(commands.handle("t-think-a", "/status", model="anthropic/claude-opus-5-5")).text
    bad = run(commands.handle("t-think-a", "/think enormous"))
    assert not bad.ok and "enormous" in bad.text and think.of_thread("t-think-a") == "max"  # 写错了不动
    cleared = run(commands.handle("t-think-a", "/think default", model="anthropic/claude-opus-5-5"))
    assert "Cleared" in cleared.text and "high (this Agent's default)" in cleared.text and think.of_thread("t-think-a") is None
    server_json(ON)
    assert think.resolve("t-think-a", "main", MODEL)["send"] is None  # 都不写：不带（和以前一样）
    assert "Thinking:" not in run(commands.handle("t-think-a", "/status")).text


def test_owner_turns_send_the_level(server_json, fake):
    import chat
    server_json({**ON, "engine": {**ON["engine"], "providers": ACME, "models": [MODEL, "acme/some-model"]}})
    day = chat.day_of(chat.now_iso())
    f = fake([{"text": "One."}, {"text": "Two."}])
    run(owner.respond("t-think-b", day, "hi", "acme/some-model", lambda t: None))
    think.set_thread("t-think-b", "max")
    run(owner.respond("t-think-b", day, "again", "acme/some-model", lambda t: None))
    assert [r[1].effort for r in f.requests] == [None, "high"]  # 没设不带；max 夹到这家支持的 high


def test_claude_code_turns_get_effort(server_json, request):
    claude = request.getfixturevalue("cc")
    server_json({**BUILTIN, "engine": {**BUILTIN["engine"], "claude_code": {"effort": "low"}, "think": {"main": "medium"}}})
    claude.scenario_([[{"say": "first"}], [{"say": "second"}], [{"say": "third"}]])
    day = "2026-10-17"

    async def go():
        async with serving(claude):
            await owner.respond("t-think-cc", day, "one", "claude-code/opus", lambda t: None)   # Agent 的默认 medium
            think.set_thread("t-think-cc", "max")
            await owner.respond("t-think-cc", day, "two", "claude-code/opus", lambda t: None)   # 这个对话设了 max
            server_json({**BUILTIN, "engine": {**BUILTIN["engine"], "claude_code": {"effort": "low"}}})
            think.set_thread("t-think-cc", None)
            await owner.respond("t-think-cc", day, "three", "claude-code/opus", lambda t: None)  # 都没设：照旧 engine.claude_code.effort
    run(go())
    efforts = [s["argv"][s["argv"].index("--effort") + 1] for s in claude.records("start")]
    assert efforts == ["medium", "max", "low"]


from test_engine_claude_code import cc  # noqa: E402,F401 — 假的 claude（上面经 request 拿）
