"""赛博选模型的页（主线第三步第 8 件，cybermodels.py）：推荐 / 全部 / 本机，开源的在前，推荐带一句理由；套餐列出来但标「赛博暂时
不能用套餐」；「还能接」四条渠道。不连真模型、不探真的本机：本机探到什么是编的，密钥是假的。"""
from __future__ import annotations

import time

import pytest

from test_cybers import BASE, H, env, make  # noqa: F401 — 同一套 server.json、令牌


@pytest.fixture
def setup(env, monkeypatch):  # noqa: F811 — env 是导入的 fixture
    """多一家闭源的（有密钥）、一个登录着的 Claude Code 套餐（只列出来）、本机的 Ollama 探到一个模型。"""
    from engine import models, setup_api
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-cybermodels-2222")
    monkeypatch.setattr(models, "subscription_models", lambda: ["claude-code/sonnet"])
    monkeypatch.setattr(setup_api, "_local", (time.monotonic(), {"ollama": {"detected": True, "url": "http://127.0.0.1:11434/v1", "version": "0.0-test",
                                                                            "models": ["qwen3:8b"]}}))
    env.cfg(engine={**BASE["engine"], "models": [*BASE["engine"]["models"], "anthropic/claude-sonnet-5-5"]})
    return env


def test_the_model_page_is_behind_the_cybers_switch(env):  # noqa: F811 — env 是导入的 fixture
    env.cfg(cybers=False)
    assert env.client.get("/api/cyber-models", headers=H).status_code == 404


def test_open_weight_first_plans_listed_but_greyed_and_a_reason_for_the_pick(setup):
    v = make(setup, "Victor", kind="pro")
    r = setup.client.get("/api/cyber-models", params={"cyber": v["id"]}, headers=H).json()
    refs = [x["ref"] for x in r["all"]]
    assert r["class"] == "pro" and r["current"] is None and r["inUse"] == "deepseek/deepseek-flash" and r["inUseName"] == "DeepSeek Flash"
    assert refs.index("moonshot/kimi-k3") < refs.index("anthropic/claude-sonnet-5-5") < refs.index("claude-code/sonnet")  # 开源在前，套餐最后
    assert "ollama/qwen3:8b" in refs and refs[-1] == "claude-code/sonnet"
    plan = r["all"][-1]
    assert plan["billing"] == "plan" and plan["allowed"] is False and plan["note"] == "Cybers can't use subscription plans yet"
    closed = next(x for x in r["all"] if x["ref"] == "anthropic/claude-sonnet-5-5")
    assert closed["open"] is False and closed["cost"] == "high" and closed["billing"] == "metered" and "goes to this vendor" in closed["use"]
    rec = r["recommended"]
    assert rec[0]["ref"] == "moonshot/kimi-k2.6" and "capable open-weight" in rec[0]["reason"]  # 专业的挑强一些的（中档的开源）
    assert all(x["allowed"] and x["usable"] for x in rec) and len(rec) == 3
    assert [x["ref"] for x in r["local"]] == ["ollama/qwen3:8b"] and r["local"][0]["cost"] == "low" and r["local"][0]["open"] is True
    ch = {c["id"]: c for c in r["channels"]}
    assert list(ch) == ["openrouter", "local", "keys", "chatgpt"]
    assert ch["local"]["connected"] and ch["keys"]["connected"] and not ch["openrouter"]["connected"] and ch["chatgpt"]["allowed"] is False
    deepseek = next(x for x in r["all"] if x["ref"] == "deepseek/deepseek-flash")
    assert deepseek["inUse"] and not deepseek["current"] and deepseek["vendor"] == "DeepSeek"


def test_a_new_friend_gets_a_cheap_one_and_a_bad_class_is_refused(setup):
    r = setup.client.get("/api/cyber-models", params={"class": "friend"}, headers=H).json()
    assert r["recommended"][0]["ref"] == "deepseek/deepseek-flash" and "cheap" in r["recommended"][0]["reason"]
    assert setup.client.get("/api/cyber-models", params={"class": "robot"}, headers=H).status_code == 400
    assert setup.client.get("/api/cyber-models", params={"cyber": "cy-00000000"}, headers=H).status_code == 404
