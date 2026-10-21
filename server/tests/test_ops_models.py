"""第 7 批 2.9 / 2.11：冷却存盘（engine/fallback.py）、通用别名（engine.aliases，engine/models.py）。不连网、不花钱。"""
from __future__ import annotations

import asyncio
import json
import stat
import time

import pytest

from engine import agent_cli, fallback, models, openai_api
from engine.errors import EngineError

KEY = "sk-test-0123456789abcdWXYZ"


@pytest.fixture(autouse=True)
def clean(monkeypatch, tmp_path):
    from config import settings
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    fallback.reset()
    monkeypatch.setenv("DEEPSEEK_API_KEY", KEY)
    monkeypatch.setenv("MOONSHOT_API_KEY", "test-moonshot-key-000")
    yield
    fallback.reset()


def restart() -> None:
    """服务重启：进程里的冷却没了，下一次用到时从盘上读回。"""
    fallback._cool.clear()  # noqa: SLF001
    fallback._loaded = False  # noqa: SLF001


def test_cooldowns_survive_a_restart(server_json):
    server_json({"engine": {"model": "deepseek/deepseek-flash", "fallbacks": ["moonshot/kimi-k3"]}})
    err = EngineError("429 too many requests", status=429)
    p = fallback.Picker(type("S", (), {"model": "deepseek/deepseek-flash", "fallbacks": None})())
    assert p.failed("deepseek/deepseek-flash", err, visible=False)["to"] == "moonshot/kimi-k3"
    fallback.note("moonshot/kimi-k3", None, "model_not_found")
    path = fallback.store_path()
    assert path.exists() and stat.S_IMODE(path.stat().st_mode) == 0o600
    raw = path.read_text()
    assert KEY not in raw and "DEEPSEEK_API_KEY#" in raw  # 记号是变量名 + 指纹，不是密钥
    before = {c["what"]: c for c in fallback.status()}
    restart()
    after = {c["what"]: c for c in fallback.status()}
    assert set(after) == set(before) and all(abs(after[k]["seconds"] - before[k]["seconds"]) <= 2 for k in after)
    assert fallback.cooling("deepseek/deepseek-flash") > 0 and fallback.cooling("moonshot/kimi-k3") > 500
    # 连着再错照样翻倍（次数也存着）
    fallback.note("moonshot/kimi-k3", None, "model_not_found")
    assert 1100 < fallback.cooling("moonshot/kimi-k3") <= 1200
    # 成功一次：清掉、写盘；重启以后也没有了
    fallback.clear("moonshot/kimi-k3", None)
    restart()
    assert fallback.cooling("moonshot/kimi-k3") == 0 and fallback.cooling("deepseek/deepseek-flash") > 0


def test_expired_and_broken_entries_are_dropped(server_json):
    server_json({"engine": {"model": "deepseek/deepseek-flash"}})
    path = fallback.store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    now = time.time()
    path.write_text(json.dumps({"v": 1, "slots": {
        "model:deepseek/old": {"until": now - 5, "at": now - 100, "reason": "model_not_found", "n": 1, "label": "deepseek/old"},
        "model:deepseek/deepseek-flash": {"until": now + 300, "at": now - 10, "reason": "model_not_found", "n": 2, "label": "x"},
        "model:deepseek/weird": {"until": "soon"},
        "model:deepseek/odd": {"until": now + 300, "at": now, "reason": "overloaded", "n": 1, "label": "y"}}}), encoding="utf8")
    restart()
    assert [c["what"] for c in fallback.status()] == ["x"] and 290 < fallback.cooling("deepseek/deepseek-flash") <= 300
    path.write_text("{not json", encoding="utf8")
    restart()
    assert fallback.status() == []  # 读不了：当没有


ALIASES = {"fast": "deepseek/deepseek-flash", "smart": "moonshot/kimi-k3", "plan": "claude-code/opus", "gpt": "chatgpt/gpt-6.1-sol",
           "bad/name": "deepseek/deepseek-flash", "broken": "not a ref"}


def test_aliases_resolve_everywhere_a_ref_is_read(server_json):
    server_json({"engine": {"model": "fast", "models": ["fast", "smart"], "fallbacks": ["smart"], "aliases": ALIASES,
                            "use": {"card": "fast", "background": "smart"}}})
    assert models.aliases() == {k: v for k, v in ALIASES.items() if k in ("fast", "smart", "plan", "gpt")}  # 写错的不要
    assert models.resolve("fast") == "deepseek/deepseek-flash" and models.resolve("nope") == "nope" and models.resolve("x/y") == "x/y"
    assert models.parse_ref("fast") == ("deepseek", "deepseek-flash") and models.valid_ref("smart") and not models.valid_ref("broken")
    assert models.default_model() == "deepseek/deepseek-flash" and models.fallbacks() == ["moonshot/kimi-k3"]
    assert models.route("card") == "deepseek/deepseek-flash" and models.background_model() == "moonshot/kimi-k3"
    assert models.has_key("fast") and models.known("fast") and models.chain_after("fast") == ["moonshot/kimi-k3"]
    assert models.outside("smart") == "moonshot/kimi-k3"
    ms = models.models()
    assert "deepseek/deepseek-flash" in ms and "fast" in ms and "smart" in ms and "plan" not in ms and "gpt" not in ms  # app 里能选按量的别名


def test_aliases_never_get_round_may_spend(server_json):
    """指到套餐的别名：先解析成真的引用，再照 may_spend 判。自己的内核里，用户自己的赛博回合能花；外人回合、对外接口一律花不了。
    OpenClaw 那条路照旧只给主人回合，别名也绕不过。"""
    server_json({"claw": {"kind": "builtin"}, "engine": {"model": "deepseek/deepseek-flash", "aliases": ALIASES,
                 "use": {"doorman": "plan", "json": "gpt"}}})
    assert models.subscription("plan") == "claude-code" and models.subscription("gpt") == "chatgpt"
    assert models.may_spend("plan", kind="cyber", origin="user") and models.may_spend("gpt", kind="cyber", origin="user")
    for kind, origin in (("outsider", "card"), ("outsider", None), ("owner", "api"), ("owner", "acp"), ("cyber", "butler")):
        assert not models.may_spend("plan", kind=kind, origin=origin) and not models.may_spend("gpt", kind=kind, origin=origin)
    assert models.may_spend("plan", kind="owner", origin="user")
    assert models.route("doorman") == "deepseek/deepseek-flash"  # 外人回合：换成不花套餐的
    assert models.route("json", kind="outsider") == "deepseek/deepseek-flash"
    assert not models.valid_ref("plan") and not models.known("plan")  # 定时任务、心跳不认指到套餐的别名（写真的引用）
    # mousse agent --model plan：解析成 claude-code/opus 再判，这台电脑上没有登录的 Claude Code → 403，不会拿别名混过去
    with pytest.raises(agent_cli.Refused) as e:
        asyncio.run(agent_cli.pick("main", "plan", None))
    assert e.value.status == 403 and "claude-code/opus" in str(e.value)
    assert asyncio.run(agent_cli.pick("main", "fast", None)) == "deepseek/deepseek-flash"
    server_json({"claw": {"kind": "openclaw"}, "engine": {"model": "deepseek/deepseek-flash", "aliases": ALIASES}})
    assert not models.may_spend("plan", kind="cyber", origin="user") and not models.may_spend("gpt", kind="cyber", origin="user")
    assert models.may_spend("plan", kind="owner", origin="user")


def test_the_api_reads_aliases(server_json, monkeypatch):
    server_json({"engine": {"model": "deepseek/deepseek-flash", "aliases": ALIASES, "api": True}})
    monkeypatch.setattr(openai_api, "chosen", lambda agent: "smart")
    assert openai_api.model_for("main") == "moonshot/kimi-k3"
    monkeypatch.setattr(openai_api, "chosen", lambda agent: "plan")  # 对外接口不花套餐：别名指到套餐也一样
    assert openai_api.model_for("main") == "deepseek/deepseek-flash"
