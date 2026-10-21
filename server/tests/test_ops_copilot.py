"""copilot/… 已不再支持（10/21）。当它不存在：subscription 不认，valid_ref 为假，解析走 outside()。
校验给一条提示。不连网，不用假的 copilot 命令。
"""
import asyncio

import pytest
from config import settings
from engine import heartbeat, models, providers
from engine.errors import EngineError
from ops import confcheck


def _engine(**over) -> dict:
    base = {"model": "deepseek/deepseek-flash", "models": ["deepseek/deepseek-flash"]}
    base.update(over)
    return {"claw": {"kind": "builtin"}, "engine": base}


def test_a_configured_copilot_model_falls_through_outside(server_json, monkeypatch):
    """前台、后台的解析都换成按量的。Codex 还是套餐。自定义的同名服务商也不接。"""
    monkeypatch.setattr(settings, "language", "en")
    server_json(_engine(model="copilot/auto", models=["copilot/auto", "deepseek/deepseek-flash"], copilot=True,
                        use={"json": "copilot/auto", "card": "copilot/auto"},
                        providers={"copilot": {"api": "openai", "url": "http://127.0.0.1:9/v1", "key": "sk-test-fictional"}}))
    assert models.subscription("copilot/auto") is None
    assert models.subscription("copilot/a/b") is None
    assert not models.valid_ref("copilot/auto") and not models.known("copilot/auto")
    assert models.retired_plan("copilot/auto") and models.retired_plan("copilot/a/b")
    assert "copilot" not in models.CLI_PLANS and models.subscription("codex/default") == "codex"
    assert models.outside("copilot/auto") == "deepseek/deepseek-flash"
    assert models.default_model() == "deepseek/deepseek-flash"
    assert "copilot/auto" not in models.models()
    assert models.route("json", kind="owner", origin="auto") == "deepseek/deepseek-flash"
    assert models.route("card") == "deepseek/deepseek-flash"
    # 原来就能花的按量模型，和 Codex 的前台 / 外人来路，一点不变
    assert models.may_spend("deepseek/deepseek-flash", kind="owner", origin="user")
    assert models.may_spend("codex/default", kind="owner", origin="user")
    assert not models.may_spend("codex/default", kind="owner", origin="card")
    assert not models.may_spend("codex/default", kind="outsider", origin="api")
    with pytest.raises(EngineError) as e:
        models.provider("copilot")
    assert e.value.kind == "config" and "no longer supported" in str(e.value) and "sk-test-fictional" not in str(e.value)
    found = confcheck.validate({"claw": {"kind": "builtin"}, "engine": {"model": "copilot/auto", "copilot": True}})
    notes = [f for f in found if f.message == confcheck.copilot_gone()]
    assert len(notes) == 1 and notes[0].level == "warn" and confcheck.errors(found) == []
    assert confcheck.copilot_leftover_text({"engine": {"model": "deepseek/deepseek-flash"}}) is None


def test_no_other_model_does_not_crash(server_json, monkeypatch):
    """只配了 copilot/…、没有别的模型：解析成空，不抛「不认识的服务商」。"""
    monkeypatch.setattr(settings, "language", "en")
    server_json(_engine(model="copilot/auto", models=["copilot/auto"]))
    assert models.default_model() == "" and models.outside("copilot/auto") is None
    assert all(not str(m).startswith("copilot/") for m in models.models())

    async def pull() -> None:
        async for _ev in providers.stream("copilot/auto", providers.Request()):
            pass

    with pytest.raises(EngineError) as e:
        asyncio.run(pull())
    assert e.value.kind == "config" and "unknown model provider" not in str(e.value)


def test_an_alias_and_a_heartbeat_model_are_retired(server_json, monkeypatch):
    monkeypatch.setattr(settings, "language", "en")
    server_json(_engine(aliases={"fast": "copilot/auto"}))
    assert models.retired_plan("fast") and not models.valid_ref("fast")
    monkeypatch.setattr("engine.jobs.model_for", lambda _thread, _pinned: "copilot/auto")
    assert heartbeat.model_of("main", {"model": ""}) == "deepseek/deepseek-flash"
    assert heartbeat.model_of("main", {"model": "copilot/auto"}) == "deepseek/deepseek-flash"


def test_a_claw_section_copilot_model_uses_the_engine_fallback(server_json):
    """claw 段自己写了 copilot/…：自己的内核换成 engine 里的按量模型，不把 copilot/… 交出去。"""
    import claw
    server_json({"claw": {"kind": "builtin", "model": "copilot/auto"}, "engine": {"model": "deepseek/deepseek-flash"}})
    assert claw.model() == "deepseek/deepseek-flash"
    server_json({"claw": {"kind": "builtin", "model": "copilot/auto"}, "engine": {"model": "copilot/auto"}})
    assert claw.model() == "default"


def test_startup_text_matches_the_validator(server_json, monkeypatch):
    monkeypatch.setattr(settings, "language", "zh")
    server_json(_engine(copilot=False))
    note = confcheck.copilot_leftover_text({"engine": {"copilot": False}})
    assert note and "不再支持" in note and "设置" in note
    monkeypatch.setattr(settings, "language", "en")
    assert "no longer supported" in confcheck.copilot_gone()
