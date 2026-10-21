"""Kimi 会员的 Kimi Code（kimi/<型号>，engine/kimi_plan.py）：假的 HTTP，不连网。

密钥是虚构的。断言它不出现在报错、状态和打出来的字里。
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

import secretstore
from config import settings
from engine import kimi_plan, models, probe, provider_openai, providers, tasks
from engine.loop import ToolFailure
from engine.session import user

KEY = "sk-kimi-fictional-0000abcd"
REF = "kimi/kimi-for-coding"


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def home(tmp_path, monkeypatch, server_json):
    """一份临时的 server.json 和 data_dir。进程环境里不留这把密钥（lookup 先看环境）。"""
    server_json({"claw": {"kind": "builtin"}, "language": "en",
                 "engine": {"model": "deepseek/deepseek-flash", "models": ["deepseek/deepseek-flash"]}})
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(settings, "language", "en")
    monkeypatch.delenv(kimi_plan.KEY_ENV, raising=False)
    return tmp_path


def save_key():
    secretstore.put(kimi_plan.KEY_ENV, KEY)


def test_naming_splits_the_membership_from_the_metered_platform(home):
    assert models.subscription(REF) == "kimi" and models.subscription("kimi/k3") == "kimi"
    assert models.subscription("kimi/k3-256k") == "kimi" and models.subscription("kimi/kimi-for-coding-highspeed") == "kimi"
    assert models.valid_ref(REF) and models.one_shot_plan(REF)
    assert models.subscription("moonshot/kimi-k3") is None  # 开放平台按量，不是这张会员
    assert models.API_PLANS["kimi"] == "kimi"


def test_site_follows_the_app_language_until_the_user_picks_one(home, monkeypatch, server_json):
    monkeypatch.setattr(settings, "language", "zh")
    assert kimi_plan.site_id() == "cn" and kimi_plan.base_url() == "https://api.kimi.com/coding/v1"
    monkeypatch.setattr(settings, "language", "en")
    assert kimi_plan.site_id() == "intl" and kimi_plan.base_url() == "https://api.kimi.ai/coding/v1"
    server_json({"claw": {"kind": "builtin"}, "engine": {"kimi": {"site": "intl", "model": "k3"}}})
    monkeypatch.setattr(settings, "language", "zh")
    assert kimi_plan.site_id() == "intl" and kimi_plan.model_id() == "k3"  # server.json 先于语言
    kimi_plan.save(site="cn", model="kimi/k3-256k")
    assert kimi_plan.site_id() == "cn" and kimi_plan.model_id() == "k3-256k"  # 设置里存过的再盖过 server.json


def test_foreground_can_spend_background_needs_consent_and_outsiders_never_can(home, monkeypatch):
    save_key()
    assert kimi_plan.usable() and models.subscription_ready(REF)
    for origin in ("user", "relay"):
        assert models.may_spend(REF, kind="owner", origin=origin)
        assert models.may_spend(REF, kind="cyber", origin=origin)  # 用户自己的赛博是前台
    for origin in ("auto", "task"):
        assert not models.may_spend(REF, kind="owner", origin=origin)
    for origin in ("card", "doorman", "butler", "api", "acp", "mcp", "trigger", None):
        assert not models.may_spend(REF, kind="owner", origin=origin), origin
    assert not models.may_spend(REF, kind="owner", origin="user", via="mcp")  # 经 /mcp 更严
    assert not models.may_spend(REF, kind="owner", origin="relay", via="task")  # 后台里转交还是后台
    assert not kimi_plan.background_allowed()
    assert "interactive" in kimi_plan.risk_text()  # 同意那一处写明条款风险
    kimi_plan.set_background(True)
    assert kimi_plan.background_allowed()
    assert models.may_spend(REF, kind="owner", origin="auto") and models.may_spend(REF, kind="cyber", origin="task")
    assert not models.may_spend(REF, kind="owner", origin="api") and not models.may_spend(REF, kind="owner", origin="user", via="mcp")
    from engine import grok_plan
    assert not grok_plan.background_allowed()  # 和 Grok 的同意分开记

    server_json = home  # 名字占位，下面换 claw
    del server_json
    import config
    config._cache = None
    # OpenClaw 那条路照旧：只有主人回合能花
    raw = json.loads(config.CONFIG_PATH.read_text(encoding="utf8"))
    raw["claw"] = {"kind": "openclaw"}
    config.CONFIG_PATH.write_text(json.dumps(raw), encoding="utf8")
    config._cache = None
    assert not models.may_spend(REF, kind="outsider", origin="user")
    assert models.may_spend(REF, kind="owner", origin="user")


def test_a_background_task_refuses_kimi_until_the_user_agrees(home, monkeypatch):
    save_key()
    monkeypatch.setattr(tasks, "model_for", lambda thread: REF)
    with pytest.raises(ToolFailure) as denied:
        tasks._model_arg({"model": REF}, tasks.ENGINE, "thread-1")
    assert "Kimi" in str(denied.value) and KEY not in str(denied.value)
    assert tasks._model_arg({}, tasks.ENGINE, "thread-1") == "deepseek/deepseek-flash"  # 没同意：换成按量的
    kimi_plan.set_background(True)
    assert tasks._model_arg({"model": REF}, tasks.ENGINE, "thread-1") == REF
    with pytest.raises(ToolFailure):
        tasks._model_arg({"model": REF}, tasks.ENGINE, "thread-1", parent="mcp")  # 外人经 /mcp 派的仍不花


def test_the_request_uses_openmousse_user_agent_and_hides_the_key(home, monkeypatch, capsys, server_json):
    save_key()
    server_json({"claw": {"kind": "builtin"}, "language": "en",
                 "engine": {"model": "deepseek/deepseek-flash",
                            "kimi": {"headers": {"User-Agent": "KimiCLI/9.9", "X-Test": "fictional"}, "site": "cn"}}})
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/chat/completions") and len(seen) == 1:
            body = {"choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 2, "completion_tokens": 1}}
            return httpx.Response(200, json=body, headers={"content-type": "application/json"})
        echoed = f"model not in this membership for key {KEY}"
        return httpx.Response(403, json={"error": {"message": echoed}}, headers={"content-type": "application/json"})

    monkeypatch.setattr(provider_openai, "TRANSPORT", httpx.MockTransport(handler))
    monkeypatch.setattr(probe, "TRANSPORT", httpx.MockTransport(handler))

    async def once(ref: str):
        out = []
        async for ev in providers.stream(ref, providers.Request(system="be brief", messages=[user("hi")])):
            out.append(ev)
        return out

    events = run(once(REF))
    assert any(ev.get("type") == "text" and ev.get("text") == "ok" for ev in events)
    req = seen[0]
    assert req.url == "https://api.kimi.com/coding/v1/chat/completions"
    assert req.headers["user-agent"] == f"OpenMousse/{__import__('version').VERSION}"
    assert "KimiCLI" not in req.headers["user-agent"]
    assert req.headers["x-test"] == "fictional"
    assert req.headers["authorization"] == f"Bearer {KEY}"
    assert json.loads(req.content)["model"] == "kimi-for-coding"

    with pytest.raises(providers.EngineError) as err:
        run(once("kimi/k3"))
    text = str(err.value)
    assert KEY not in text and "sk-kimi" not in text
    assert "membership" in text  # 403 → 哪一档会员能用哪个
    state = (settings.data_dir / "kimi" / "state.json").read_text(encoding="utf8")
    assert KEY not in state and "sk-kimi" not in state
    logged = capsys.readouterr()
    assert KEY not in logged.out and KEY not in logged.err

    bad = run(kimi_plan.run_test(key=KEY, site="intl", model="k3"))
    assert bad["ok"] is False and KEY not in json.dumps(bad) and "sk-kimi" not in json.dumps(bad)
    assert bad["site"] == "intl" and "membership" in (bad["error"] or {}).get("message", "")

    seen.clear()

    def denied_key(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(401, json={"error": {"message": f"invalid api key {KEY}"}}, headers={"content-type": "application/json"})

    monkeypatch.setattr(probe, "TRANSPORT", httpx.MockTransport(denied_key))
    wrong = run(kimi_plan.run_test(key=KEY, model="kimi-for-coding"))
    blob = json.dumps(wrong)
    assert wrong["ok"] is False and KEY not in blob and "sk-" not in blob
    assert "revoked" in (wrong["error"] or {}).get("message", "") or "wrong" in (wrong["error"] or {}).get("message", "")
    status = kimi_plan.status()
    assert KEY not in json.dumps(status)
    assert status["key"]["present"] is True and status["key"]["hint"] == "…abcd"
