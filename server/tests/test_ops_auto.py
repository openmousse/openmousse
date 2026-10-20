"""第 7 批 14：自动选模型（engine/auto.py）。简单的一句交给便宜的模型，同一轮粘住，便宜的没成回到主模型；外人、后台、另选了模型的、
主模型是套餐的都不换。模型是 evals/fakemodel.py 的假模型服务器（真的走 HTTP，照请求里的型号回），不连网、不花钱。"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

import engine
from engine import auto, fallback, loop, provider_openai, store
from engine.session import MemorySession, user
from evals.fakemodel import Fail, FakeModelServer, Reply


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def fm(server_json, monkeypatch, request):
    """两个假型号：fake/big（主模型）、fake/small（便宜的）。st.script[型号] 是要回的，没了就回「answer from <型号>」；st.seen 是请求的型号。"""
    st = SimpleNamespace(script={}, seen=[])

    def responder(rec: dict):
        m = str((rec.get("body") or {}).get("model") or "")
        st.seen.append(m)
        todo = st.script.get(m) or []
        return todo.pop(0) if todo else Reply(text=f"answer from {m}")
    server = FakeModelServer(responder).start()
    request.addfinalizer(server.stop)

    def cfg(**engine_) -> None:
        server_json({"claw": {"kind": "builtin"}, "engine": {"model": "fake/big", "models": ["fake/big", "fake/small"],
                                                             "providers": {"fake": {"api": "openai", "url": server.openai_url, "key_env": ""}},
                                                             "auto": {"cheap": "fake/small"}, **engine_}})
    cfg()
    st.cfg = cfg
    monkeypatch.setattr(loop, "MODEL_RETRIES", (0, 0))
    monkeypatch.setattr(provider_openai, "RETRY_DELAYS", (0, 0))
    fallback.reset()
    yield st
    fallback.reset()


def turn(text: str, **kw):
    spec = engine.Spec(**{"kind": "owner", "model": "fake/big", "system": "You are Mousse, a fictional assistant.", "conversation": "auto-test",
                          "agent": "main", "origin": "user", **kw})
    return run(engine.run_turn(spec, MemorySession(), user(text)))


def test_what_counts_as_simple(server_json):
    server_json({"engine": {"auto": {"cheap": "fake/small", "keywords": ["invoice"]}}})
    for t in ("thanks!", "早上好", "what time is it in Lisbon?", "[Mon 2026-10-19 08:00 BST] ok, see you later", "remind me to water the plants"):
        assert auto.simple(t), t
    for t in ("", "x" * 161, " ".join(["word"] * 29), "one\ntwo\nthree", "run `ls`", "see https://example.com", "please debug this",
              "帮我分析一下这个月的开销", "为什么天是蓝的", "send the invoice", "translate this"):
        assert not auto.simple(t), t
    assert not auto.simple("look", images=True)


def test_a_simple_line_goes_to_the_cheap_model(fm):
    res = turn("thanks, that's all for now")
    assert res.status == "done" and res.text == "answer from small" and fm.seen == ["small"]
    assert res.model == "fake/small" and res.models == ["fake/small"]  # 主模型没调过：不记
    with store.read() as conn:
        row = conn.execute("SELECT models FROM engine_turns WHERE id=?", (res.turn,)).fetchone()
    assert json.loads(row["models"]) == ["fake/small"]
    fm.seen.clear()
    res = turn("Please compare these two plans and explain why one is better")
    assert res.text == "answer from big" and fm.seen == ["big"] and res.model == "fake/big"


def test_it_sticks_for_the_whole_turn(fm):
    log: list = []

    async def echo(args, ctx):
        log.append(args["x"])
        return "ok"
    tool = engine.Tool(name="echo", description="", schema={"type": "object", "required": ["x"], "properties": {"x": {"type": "string"}}}, run=echo)
    fm.script["small"] = [Reply(text="one moment", calls=[("echo", {"x": "1"})]), Reply(text="done")]
    res = turn("ping the echo please", tools=[tool])
    assert res.text == "done" and log == ["1"] and fm.seen == ["small", "small"] and res.models == ["fake/small"]


def test_only_the_users_own_turns_on_the_default_model(fm, monkeypatch):
    cases = [{"kind": "outsider", "origin": "card"}, {"origin": "auto"}, {"origin": "api"}, {"origin": "acp"}, {"model": "fake/other"}]
    for kw in cases:
        fm.seen.clear()
        res = turn("thanks!", **kw)
        assert fm.seen == [kw.get("model", "fake/big").split("/")[1]], (kw, fm.seen, res.error)
    fm.cfg(auto={"cheap": "fake/small", "enabled": False})
    fm.seen.clear()
    assert turn("thanks!").text == "answer from big"
    fm.cfg(auto={"cheap": "nope/x"})  # 写错了 = 关
    fm.seen.clear()
    assert turn("thanks!").text == "answer from big"
    fm.cfg(auto={"cheap": "chatgpt/gpt-6"})  # 套餐当不了便宜的那个
    assert auto.cheap() is None
    fm.cfg(model="chatgpt/gpt-6", auto={"cheap": "fake/small"})  # 主模型是套餐：不换（不按量的换成按量的反倒花钱）
    assert auto._decide(SimpleNamespace(kind="owner", origin="user", model="chatgpt/gpt-6"), "chatgpt/gpt-6", SimpleNamespace(json=False, messages=[user("hi")])) is None  # noqa: SLF001
    fm.cfg()
    assert auto._decide(SimpleNamespace(kind="owner", origin="user", model="fake/big"), "fake/big", SimpleNamespace(json=True, messages=[user("hi")])) is None  # noqa: SLF001


def test_when_the_cheap_model_fails_the_main_one_answers(fm):
    fm.script["small"] = [Fail(429, "rate limited, slow down")]
    res = turn("thanks!")
    assert res.status == "done" and res.text == "answer from big" and fm.seen == ["small", "big"]
    assert res.model == "fake/big" and res.models == ["fake/small", "fake/big"]
    assert fallback.cooling("fake/small") > 0  # 记了冷却：冷却完以前不再换
    fm.seen.clear()
    assert turn("thanks again!").text == "answer from big" and fm.seen == ["big"]
