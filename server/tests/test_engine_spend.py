"""第 4 批：按钱的花费上限（engine/spend.py）。按天、按轮、按 Agent、按用途；套餐不算钱；价目里没有的只记 token、不拦；一轮跑到一半到了就停
（不重试、不换备用链）；后台的（定时任务、心跳、后台任务）跳过、记 limit；提醒线一天推一次；GET / PUT（只认主人的设备、写之前先备份）。
模型是假的，价钱也是假的：test/metered 一个输入 token 一毛钱（FakeModel 每次回 10 个输入 token = $1），test/free 价目里没有。
库是这个测试自己的（settings.db 换到 tmp_path），别的测试的花费算不进来。"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

import config
import engine
from engine import heartbeat, jobs, models, runners, semantic, spend, store, tasks, usage, when
from engine.errors import EngineError
from engine.session import MemorySession, user
from test_engine import echo_tool
import test_engine_jobs as tj
from test_engine_jobs import fire, run, runs, user_job

builtin, fake, pushes = tj.builtin, tj.fake, tj.pushes  # 定时任务那边的 fixture


@pytest.fixture(autouse=True)
def own_db(tmp_path, monkeypatch):
    """这个测试自己的库和价目：test/metered 一个输入 token $0.1、输出 token 不要钱；test/other 同价（备用链用）。"""
    monkeypatch.setattr(config.settings, "db", tmp_path / "spend.db")
    monkeypatch.setitem(models.PRICES, "test/metered", (100_000.0, 0.0, 0.0, 0.0))
    monkeypatch.setitem(models.PRICES, "test/other", (100_000.0, 0.0, 0.0, 0.0))
    monkeypatch.setattr(runners, "_found", {})
    spend._live.clear()
    spend._blocked.clear()
    spend._invalidate()
    spend._warned_at[0] = 0.0
    jobs._running.clear()
    heartbeat._running.clear()
    yield
    spend._live.clear()
    spend._invalidate()


def caps(server_json, **sp):
    server_json({"claw": {"kind": "builtin"}, "engine": {"model": "test/metered", "spend": sp}})
    spend._invalidate()


def seed(cost: float | None, *, agent: str = "main", kind: str = "owner", origin: str = "user", model: str = "test/metered",
         ago: timedelta = timedelta(0), tokens: int = 10) -> None:
    """往今天的账上记一轮结束了的（花了 cost 美元；None = 没算钱）。"""
    import uuid
    at = (datetime.now(config.settings.tz) - ago).isoformat(timespec="milliseconds")
    with store.tx() as conn:
        conn.execute("INSERT INTO engine_turns(id, conversation, kind, agent, model, origin, status, usage, started_at) VALUES(?,?,?,?,?,?,?,?,?)",
                     ("t_seed_" + uuid.uuid4().hex[:8], agent, kind, agent, model, origin, "done",
                      json.dumps({"in": tokens, "out": 1, "cost": cost}), at))
    spend._invalidate()


def turn(model: str = "test/metered", *, agent: str | None = "main", origin: str = "user", kind: str = "owner", script=None,
         fallbacks=None, tools=None):
    seen: list = []
    spec = engine.Spec(kind=kind, model=model, agent=agent, origin=origin, conversation=f"spend-{agent}", tools=tools or [], fallbacks=fallbacks)
    res = run(engine.run_turn(spec, MemorySession(), user("go"), on_event=seen.append))
    return res, "".join(e["text"] for e in seen if e.get("type") == "text")


# —— 按天、按轮、按 Agent、按用途 ——————————————————————————————————————————————————————————

def test_no_config_means_no_limit(server_json, fake):
    server_json({"claw": {"kind": "builtin"}, "engine": {"model": "test/metered"}})
    seed(1000.0)
    fake([{"text": "fine"}])
    res, _ = turn()
    assert res.status == "done" and not spend.enabled() and spend.view()["configured"] is False


def test_the_daily_total_stops_the_next_model_call(server_json, fake):
    caps(server_json, day=5)
    seed(4.5)
    f = fake([{"text": "one dollar"}, {"text": "never"}])
    res, _ = turn()
    assert res.status == "done"  # 4.5 < 5：照跑，这一步花了 $1
    t = spend.today()
    assert t["total"] == pytest.approx(5.5) and t["purposes"]["chat"] == pytest.approx(5.5)
    res, said = turn()
    assert res.status == "error" and res.error_kind == "budget" and len(f.requests) == 1  # 一次模型都没再调
    assert "$5.50" in res.error and "$5.00 daily limit" in res.error and "engine.spend.day" in res.error
    assert said.strip().startswith("(") and "engine.spend.day" in said  # 主人回合：回复里一句话说清
    with pytest.raises(EngineError) as e:  # 一次性 JSON（名片 Agent、Doorman、llmjson 都经它）：一样停，不再问第二次
        run(engine.ask_json("say ok", {}, model="test/metered", purpose="card", timeout=10))
    assert e.value.kind == "budget" and len(f.requests) == 1


def test_a_turn_that_crosses_its_limit_stops_without_retry_or_fallback(server_json, fake):
    server_json({"claw": {"kind": "builtin"}, "engine": {"model": "test/metered", "fallbacks": ["test/other"], "spend": {"turn": 1.5}}})
    log: list = []
    f = fake([{"calls": [("c1", "echo", {"x": "a"})]}, {"calls": [("c2", "echo", {"x": "b"})]}, {"text": "never"}, {"text": "never"}])
    res, said = turn(tools=[echo_tool(log)])
    assert res.status == "error" and res.error_kind == "budget" and "per-turn limit" in res.error
    assert len(f.requests) == 2 and [r[0] for r in f.requests] == ["test/metered", "test/metered"]  # 没换到 test/other，也没再试
    assert res.models == ["test/metered"] and log == [({"x": "a"}, "owner"), ({"x": "b"}, "owner")]
    assert "engine.spend.turn" in said


def test_running_turns_count_before_they_finish(server_json, fake):
    caps(server_json, day=100)
    seen: list = []

    async def peek(args, ctx):
        seen.append(spend.today()["total"])
        return "ok"
    tool = engine.Tool(name="peek", description="peek", schema={"type": "object", "properties": {}}, run=peek)
    fake([{"calls": [("c1", "peek", {})]}, {"text": "done"}])
    res, _ = turn(tools=[tool])
    assert res.status == "done" and seen == [pytest.approx(1.0)]  # 这一轮还没结束：库里还没有它的用量，进程里记着
    # 后台的提醒线检查（_warn_soon → warn → today）可能正好在最后一步和写库之间读了一次库、缓存里这一轮还在跑：
    # 那样总数照样对（库里没有，进程里有），只是进程里那条要等缓存过期才清。这里先作废缓存，看写完库以后的样子
    spend._invalidate()
    assert spend.today()["total"] == pytest.approx(2.0) and spend._live == {}


def test_agent_and_purpose_limits(server_json, fake):
    caps(server_json, agents={"g-study": {"day": 1}}, purposes={"background": {"day": 2}, "outsider": {"turn": 0.5}})
    seed(1.0, agent="g-study")
    seed(2.0, origin="auto")
    fake([{"text": "main is fine"}])
    assert turn(agent="main")[0].status == "done"
    res, _ = turn(agent="g-study")
    assert res.error_kind == "budget" and "engine.spend.agents.g-study.day" in res.error
    res, _ = turn(agent="main", origin="auto")
    assert res.error_kind == "budget" and "Background work" in res.error and "engine.spend.purposes.background.day" in res.error
    f = fake([{"text": '{"ok": true}'}, {"text": "never"}])
    assert run(engine.ask_json("say ok", {}, model="test/metered", purpose="card", timeout=10))[0] == {"ok": True}  # 外人回合：一轮 $1 ≥ 0.5 的只拦下一步
    v = spend.view()
    assert v["agents"]["g-study"]["left"] == 0 and v["purposes"]["background"]["left"] == 0 and len(f.requests) == 1
    assert {(b["scope"], b["name"]) for b in v["blocked"]} == {("agent", "g-study"), ("purpose", "background")}


def test_plans_and_unpriced_models_are_not_capped(server_json, fake, monkeypatch):
    caps(server_json, day=1)
    seed(1.0)
    seed(None, model="claude-code/sonnet")
    fake([{"text": "free"}])
    res, _ = turn(model="test/free")  # 价目里没有：算不出钱，照跑
    assert res.status == "done"
    t = spend.today()
    assert t["total"] == pytest.approx(1.0) and t["plan"] == 1 and t["unpriced"] == 1 and t["unpricedModels"] == ["test/free"]
    v = spend.view()
    assert v["plan"] == {"turns": 1} and v["unpriced"] == {"turns": 1, "models": ["test/free"]} and "without a price" in v["note"]
    assert not spend.metered("chatgpt/gpt-test") and not spend.metered("test/free") and spend.metered("test/metered")
    monkeypatch.setattr(runners, "_found", {"claude-code": {"bin": "/x/claude", "auth": "claude.ai", "billing": "subscription"}})
    assert not spend.metered("claude-code/opus")
    spend.brain(runners.available()["claude-code"])  # 套餐登录：不管
    monkeypatch.setattr(runners, "_found", {"claude-code": {"bin": "/x/claude", "auth": "api_key_helper", "billing": "api_key"}})
    assert spend.metered("claude-code/opus")  # API 密钥登录的照算钱
    with pytest.raises(EngineError) as e:
        spend.brain(runners.available()["claude-code"])
    assert e.value.kind == "budget"


def test_embeddings_count_toward_the_day_and_background(server_json):
    caps(server_json, day=3, purposes={"background": {"day": 1}})
    with store.tx() as conn:
        conn.executescript(usage.EMBED_SCHEMA)
        conn.execute("INSERT INTO engine_embed_usage(at, model, provider, kind, requests, texts, tokens, estimated) VALUES(?,?,?,?,?,?,?,?)",
                     (datetime.now(config.settings.tz).isoformat(timespec="milliseconds"), "openai/text-embedding-3-small", "openai", "index",
                      10, 100, 50_000_000, 0))
    spend._invalidate()
    t = spend.today()
    assert t["embeddings"] == pytest.approx(1.0) and t["total"] == pytest.approx(1.0) and t["purposes"]["background"] == pytest.approx(1.0)
    assert not spend.embed_ok() and semantic._held()  # background 到了：不补向量


def test_media_counts_toward_the_day_and_stops_paid_media_tools(server_json, monkeypatch):
    import asyncio

    from engine import imagegen, media, mediagen
    caps(server_json, day=3, agents={"g-art": {"day": 1}})
    media.record("image", media.Choice("image", "openai", "gpt-image-2"), cost=1.5, agent="g-art", conversation="g-art")
    spend._invalidate()
    t = spend.today()
    assert t["media"] == pytest.approx(1.5) and t["total"] == pytest.approx(1.5) and t["agents"]["g-art"] == pytest.approx(1.5)
    ctx = engine.ToolCtx(turn="t-art", kind="owner", agent="g-art", conversation="g-art", call_id="c", control=engine.Control())
    with pytest.raises(engine.ToolFailure, match="engine.spend.agents.g-art.day"):
        spend.before_tool(ctx, "openai")
    spend.before_tool(ctx, media.LOCAL)  # 本机的不拦
    spend.before_tool(engine.ToolCtx(turn="t-main", kind="owner", agent="main", conversation="main", call_id="c", control=engine.Control()),
                      "fal")  # 主对话还没到它的上限（一天 $3）

    calls: list = []

    async def never(*a, **k):
        calls.append(a)
        raise AssertionError("shouldn't be called")
    monkeypatch.setattr(imagegen, "generate", never)
    monkeypatch.setattr(mediagen, "generate", never)
    server_json({"claw": {"kind": "builtin"}, "engine": {"model": "test/metered", "spend": {"day": 3, "agents": {"g-art": {"day": 1}}},
                                                         "use": {"image": "openai/gpt-image-2", "video_gen": "fal/fictional/video"}}})
    spend._invalidate()
    img = imagegen.tool("g-art")
    with pytest.raises(engine.ToolFailure, match="engine.spend.agents.g-art.day"):
        asyncio.run(img.run({"prompt": "a fictional lighthouse"}, ctx))
    with pytest.raises(engine.ToolFailure, match="engine.spend.agents.g-art.day"):
        asyncio.run(mediagen.run(mediagen.VIDEO, {"prompt": "a fictional lighthouse at dusk"}, ctx))
    assert calls == [] and not [j for j in mediagen._jobs.values() if j.conversation == "g-art"]


# —— 后台的：跳过、记 limit ——————————————————————————————————————————————————————————————————

def test_background_runs_skip_and_log_limit(server_json, builtin, fake, pushes):
    caps(server_json, day=1)
    seed(1.0)
    f = fake([])
    j = user_job("main")
    due = when.parse_iso(j["next_run"])
    assert fire(due + timedelta(seconds=1)) == []
    r = runs(j["id"])[-1]
    k = jobs.get(j["id"])
    assert r["status"] == "limit" and "daily limit" in r["error"] and k["fails"] == 0 and k["state"] == "on"
    server_json({"claw": {"kind": "builtin"}, "engine": {"model": "test/metered", "spend": {"day": 1},
                                                         "heartbeat": {"main": {"every": "1m", "prompt": "Remind the user to stretch."}}}})
    t0 = when.now()
    run(heartbeat.tick(at=t0))
    assert fire(t0 + timedelta(seconds=61), beat=True) == []
    assert heartbeat.state("main")["last_reason"] == "limit" and jobs.runs_of("heartbeat:main")[0]["status"] == "limit"
    with pytest.raises(engine.ToolFailure, match="daily limit"):
        tasks.create(thread="main", agent="main", params={"task": "Goal: something long"})
    assert f.requests == []


def test_a_job_that_hits_the_limit_mid_run_is_not_a_failure(server_json, builtin, fake, pushes):
    server_json({"claw": {"kind": "builtin"}, "engine": {"model": "test/metered", "spend": {"turn": 1}}})
    f = fake([{"calls": [("c1", "cron", {"action": "list"})]}, {"text": "never"}])
    j = user_job("main", task="List my jobs, then tell me about them.")
    fire(when.parse_iso(j["next_run"]) + timedelta(seconds=1))
    r = runs(j["id"])[-1]
    k = jobs.get(j["id"])
    assert r["status"] == "limit" and k["fails"] == 0 and len(f.requests) == 1


# —— 提醒线：一天一次 ————————————————————————————————————————————————————————————————————————

def test_warning_and_cap_notices_go_out_once_a_day(server_json, pushes):
    caps(server_json, day=5, warn=0.8, purposes={"background": {"day": 10}})
    seed(4.1)
    assert run(spend.warn()) == ["day::warn"]
    assert run(spend.warn()) == []  # 今天推过了
    assert pushes[-1]["level"] == "quiet" and "82%" in pushes[-1]["body"]
    seed(1.0)
    assert run(spend.warn()) == ["day::cap"] and "daily limit" in pushes[-1]["body"]
    assert run(spend.warn()) == [] and len(pushes) == 2


# —— 接口 ——————————————————————————————————————————————————————————————————————————————

def test_get_and_put_only_from_the_owners_devices_with_a_backup(server_json, monkeypatch, tmp_path):
    import main
    from fastapi.testclient import TestClient
    path = server_json({"claw": {"kind": "builtin"}, "engine": {"model": "test/metered"},
                        "auth": {"tokens": {"phone": "tok-phone", "mcp": "tok-mcp"}}})
    c = TestClient(main.app)
    h = {"Authorization": "Bearer tok-phone"}
    g = c.get("/api/engine/spend", headers=h).json()
    assert g["ok"] and g["configured"] is False and g["limits"]["day"] is None and g["currency"] == "USD"
    r = c.put("/api/engine/spend", headers=h, json={"day": 5, "turn": 1, "warn": 0.9, "agents": {"g-study": {"day": 1}},
                                                     "purposes": {"background": {"day": 2}}})
    assert r.status_code == 200 and r.json()["changed"] and r.json()["backup"].startswith("server.json.bak-")
    conf = json.loads(path.read_text(encoding="utf8"))
    assert conf["engine"]["spend"] == {"day": 5, "turn": 1, "warn": 0.9, "agents": {"g-study": {"day": 1.0}}, "purposes": {"background": {"day": 2.0}}}
    assert conf["auth"]["tokens"]["phone"] == "tok-phone" and list(tmp_path.glob("server.json.bak-*"))
    g = c.get("/api/engine/spend", headers=h).json()
    assert g["configured"] and g["limits"]["day"] == 5 and g["left"] == 5 and g["purposes"]["background"]["dayLimit"] == 2
    assert c.put("/api/engine/spend", headers=h, json={"purposes": {"friends": {"day": 1}}}).status_code == 400
    assert c.put("/api/engine/spend", headers=h, json={"day": -1}).status_code == 400
    assert c.put("/api/engine/spend", headers=h, json={"warn": 2}).status_code == 400
    assert c.put("/api/engine/spend", headers={**h, "X-Mousse-Via": "auto"}, json={"day": 50}).status_code == 403
    assert c.put("/api/engine/spend", headers={"Authorization": "Bearer tok-mcp"}, json={"day": 50}).status_code == 403
    monkeypatch.setattr(main, "principal_of", lambda req: "loopback")
    assert c.get("/api/engine/spend").status_code == 403
    monkeypatch.setattr(main, "principal_of", lambda req: "tailscale:my-phone")
    r = c.put("/api/engine/spend", json={"day": None, "agents": {"g-study": None}})
    assert r.status_code == 200 and json.loads(path.read_text(encoding="utf8"))["engine"]["spend"] == {"turn": 1, "warn": 0.9, "purposes": {"background": {"day": 2.0}}}
    r = c.put("/api/engine/spend", json={"spend": None})
    assert r.status_code == 200 and "spend" not in json.loads(path.read_text(encoding="utf8"))["engine"] and r.json()["configured"] is False
