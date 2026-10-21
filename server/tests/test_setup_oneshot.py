"""10/20：「按用途选模型」里一问一答（json）、压缩摘要（compact）各占一行；每一行都说清现在真用哪个、是不是套餐、套餐跑不跑得了、
出问题时换谁、最近一次套餐没做成；PUT json / compact；PUT background 不再顺手改单独设过的一问一答。
不连真的服务商、不花钱：套餐的登录是假的（monkeypatch），回合记录直接写进测试的库。数据都是编的。"""
from __future__ import annotations

import pytest

import config
from stored_values import SESSION_TAG
from test_setup import H, KEY, OTHER, env  # noqa: F401 — env 是那边的 fixture

FLASH, K3, K26 = "deepseek/deepseek-flash", "moonshot/kimi-k3", "moonshot/kimi-k2.6"


def rows(c) -> dict:
    return {x["id"]: x for x in c.get("/api/setup/purposes?probe=0", headers=H).json()["purposes"]}


def put(c, body: dict):
    return c.put("/api/setup/purposes", headers=H, json=body)


@pytest.fixture
def api(request):
    """test_setup 的 env（builtin、几把令牌，列过的模型、密钥都在 tmp）。"""
    return request.getfixturevalue("env")


@pytest.fixture
def plans(monkeypatch):
    """这台电脑上登录着 Claude Code（假的）：ready["claude-code"] = False 就是登录掉了（app 里也就选不到它）。"""
    from engine import models
    ready = {"claude-code": True}
    monkeypatch.setattr(models, "subscription_models", lambda: ["claude-code/opus", "claude-code/sonnet"] if ready["claude-code"] else [])
    monkeypatch.setattr(models, "subscription_ready", lambda ref: bool(ready.get(models.subscription(ref) or "")))
    return ready


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", KEY)
    monkeypatch.setenv("MOONSHOT_API_KEY", OTHER)


def test_the_owners_setup_shows_the_plan_and_that_nothing_switches_by_itself(api, plans):
    """主人那份（10/19 定的）：主对话、一问一答都是套餐，没写备用 → 出问题不自动换；登录掉了看得出来。"""
    api.cfg(engine={"model": "claude-code/opus", "use": {"json": "claude-code/opus"}})
    r = rows(api.client)
    assert list(r) == ["chat", "background", "writing", "json", "compact", "outsider", "embeddings"]
    j = r["json"]
    assert j["applies"] is True and j["accepts"] == {"plans": True} and j["mayPlan"] is True and j["recommended"] is None
    assert j["current"] == j["effective"] == "claude-code/opus" and j["follows"] is None
    assert j["plan"] == "claude-code" and j["planReady"] is True and j["usable"] is True and j["backups"] == [] and j["lastFailure"] is None
    cp = r["compact"]  # 没写 = 这一轮自己的模型；主对话的是套餐，套餐没做成以后再试这一轮自己的模型（不是 Claude Code 时）
    assert cp["current"] is None and cp["follows"] == "chat" and cp["effective"] == "claude-code/opus" and cp["thenOwn"] is True
    for k in ("chat", "background", "writing"):  # 老的几行也多给这几样
        assert r[k]["plan"] == "claude-code" and r[k]["planReady"] is True and r[k]["mayPlan"] is True and r[k]["backups"] == [], k
    assert r["outsider"]["mayPlan"] is False and r["outsider"]["plan"] is None
    assert r["embeddings"]["mayPlan"] is False and r["embeddings"]["backups"] == []
    plans["claude-code"] = False  # 登录掉了
    j = rows(api.client)["json"]
    assert j["planReady"] is False and j["usable"] is False and j["plan"] == "claude-code"


def test_not_a_plan_and_where_the_backups_come_from(api, plans, keys, monkeypatch):
    api.cfg(engine={"model": "claude-code/opus", "fallbacks": [K3, "openai/gpt-test"],
                    "use": {"json": ["claude-code/opus", FLASH, K3, "claude-code/sonnet"], "compact": FLASH}})
    r = rows(api.client)
    assert r["json"]["effective"] == "claude-code/opus" and r["json"]["backups"] == [FLASH, K3]  # 列表后面的；套餐不进链
    c = r["compact"]
    assert c["plan"] is None and c["planReady"] is None and c["lastFailure"] is None and c["thenOwn"] is False
    assert c["backups"] == [K3]  # 不是列表：engine.fallbacks，没有密钥的（openai）换不过去，不列
    assert r["chat"]["backups"] == [K3] and r["background"]["backups"] == [K3]
    monkeypatch.delenv("MOONSHOT_API_KEY")
    assert rows(api.client)["json"]["backups"] == [FLASH]
    api.cfg(engine={"model": "claude-code/opus", "use": {"json": ["claude-code/opus"]}, "fallbacks": [FLASH]})
    assert rows(api.client)["json"]["backups"] == []  # 只写一个的列表 = 这件事不换模型


def test_one_shots_without_use_json_take_the_main_chats_model(api, plans):
    api.cfg(engine={"model": "claude-code/opus"})
    j = rows(api.client)["json"]
    assert j["current"] is None and j["follows"] == "chat" and j["effective"] == "claude-code/opus" and j["plan"] == "claude-code"


def test_on_openclaw_the_rows_are_listed_and_left_to_the_claw(api, plans):
    api.cfg(claw={"kind": "openclaw"}, engine={"model": FLASH, "use": {"json": FLASH}})
    r = rows(api.client)
    for k in ("json", "compact"):
        assert r[k]["applies"] is False and "claw" in r[k]["why"] and "one-shot answers" in r[k]["why"], k
    for body in ({"json": FLASH}, {"compact": "default"}, {"json": None}):
        res = put(api.client, body)
        assert res.status_code == 409 and res.json()["field"] == next(iter(body)), body


def test_the_last_failed_plan_call_shows_until_one_works(api, plans, tmp_path, monkeypatch):
    from engine import claude_brain, setup_api, store
    monkeypatch.setattr(config.settings, "db", tmp_path / "turns.db")
    api.cfg(engine={"model": "claude-code/opus", "use": {"json": "claude-code/opus", "compact": "claude-code/sonnet"}})
    n = iter(range(1, 100))

    def turn(conv: str, model: str, status: str, error: str | None = None, origin: str = "user") -> None:
        i = next(n)
        with store.tx() as conn:
            conn.execute("INSERT INTO engine_turns(id, conversation, kind, model, origin, status, error, started_at) VALUES(?,?,?,?,?,?,?,?)",
                         (f"t{i}", conv, "owner", model, origin, status, error, f"2026-10-20T{i:02d}:05:00+01:00"))
    assert rows(api.client)["json"]["lastFailure"] is None  # 还没用过
    turn("json", "claude-code/opus", "done")
    turn("json", "claude-code/opus", "error", claude_brain.advice("quota", "You've hit your session limit · resets 3pm"))
    turn("compact:main", "claude-code/sonnet", "error", "Claude Code didn't answer within 120s")
    turn("main", "claude-code/opus", "error", claude_brain.advice("auth", "Not logged in · Please run /login"))
    turn("json", "deepseek/deepseek-flash", "done")  # 别的模型做成的不算
    r = rows(api.client)
    assert r["json"]["lastFailure"]["kind"] == "quota" and r["json"]["lastFailure"]["at"] == "2026-10-20T02:05:00+01:00"
    assert "usage limit" in r["json"]["lastFailure"]["error"]
    assert r["compact"]["lastFailure"]["kind"] == "timeout" and r["chat"]["lastFailure"]["kind"] == "auth"
    assert r["background"]["lastFailure"] is None  # 后台没跑过
    turn("json", "claude-code/opus", "done")  # 之后又做成了：不再报
    assert rows(api.client)["json"]["lastFailure"] is None
    turn(f"agent:main:{SESSION_TAG}:json-0a0a0a0a0a", "claude-code/opus", "error", "Claude Code: exited without a result")  # 没配 use.json 时的退路
    assert rows(api.client)["json"]["lastFailure"]["kind"] == "error"
    kinds = {t: setup_api.plan_failure_kind(t) for t in (
        claude_brain.advice("broken", "unknown option '--x'"), "Claude Code went quiet for 600s, so it was stopped",
        "Claude Code 7 秒没答完", "Claude Code 的登录掉了（这台电脑上的 Claude Code 没登录）：在终端里跑 claude 重新登录一次",
        "Claude 套餐的额度用完了：等额度恢复", "Claude Code is turned off in the settings")}
    assert list(kinds.values()) == ["broken", "timeout", "timeout", "auth", "quota", "error"]


def test_put_one_shots_and_summaries_by_hand_and_undo(api, plans, keys):
    c = api.client
    api.cfg(engine={"model": "claude-code/opus", "use": {"background": FLASH, "json": "claude-code/opus"}})
    use = lambda: api.conf()["engine"]["use"]  # noqa: E731
    r = put(c, {"json": FLASH})  # 套餐出问题了：自己换成按量的
    assert r.status_code == 200, r.text
    assert use()["json"] == FLASH and r.json()["backup"]
    j = {x["id"]: x for x in r.json()["purposes"]}["json"]
    assert j["effective"] == FLASH and j["plan"] is None and j["follows"] is None  # 单独选的：正好和后台一样也不算跟着后台
    assert put(c, {"json": "claude-code/opus"}).status_code == 200 and use()["json"] == "claude-code/opus"  # 撤销：换回套餐
    put(c, {"json": "default"})  # 跟「后台和定时」一样
    assert use()["json"] == FLASH and rows(c)["json"]["follows"] == "background"
    put(c, {"json": None})  # 不写：走以前的退路（主对话的模型）
    j = rows(c)["json"]
    assert "json" not in use() and j["follows"] == "chat" and j["effective"] == "claude-code/opus"
    r = put(c, {"compact": "claude-code/sonnet"})  # 摘要能写套餐（真用的时候能不能花照 may_spend）
    assert r.status_code == 200 and use()["compact"] == "claude-code/sonnet"
    cp = {x["id"]: x for x in r.json()["purposes"]}["compact"]
    assert cp["plan"] == "claude-code" and cp["current"] == "claude-code/sonnet" and cp["follows"] is None
    put(c, {"compact": "default"})
    assert "compact" not in use() and rows(c)["compact"]["follows"] == "chat"
    put(c, {"compact": FLASH})
    put(c, {"compact": None})
    assert "compact" not in use()
    for field, bad in (("json", "together/some-model"), ("compact", "nope"), ("json", 3)):
        res = put(c, {field: bad})
        assert res.status_code == 400 and res.json()["field"] == field, (field, bad)
    plans["claude-code"] = False  # 登录掉了的套餐选不上（只列连上的），已经写着的照样能留
    assert put(c, {"compact": "claude-code/sonnet"}).status_code == 400
    # 写成列表的（主模型 + 它自己的链）：只换主模型，链留着，撤销原样回来
    plans["claude-code"] = True
    api.cfg(engine={"model": "claude-code/opus", "use": {"json": ["claude-code/opus", FLASH]}})
    put(c, {"json": "claude-code/sonnet"})
    assert use()["json"] == ["claude-code/sonnet", FLASH] and rows(c)["json"]["backups"] == [FLASH]
    put(c, {"json": "claude-code/opus"})
    assert use()["json"] == ["claude-code/opus", FLASH]


def test_command_line_plans_are_not_offered_for_one_shots(api, plans, monkeypatch):
    """10/21 审查：Codex、Copilot 这种命令行的套餐不接一次性的调用（route 只会换成按量的）：一问一答、压缩摘要不让选，PUT 回 400。"""
    from engine import models
    monkeypatch.setattr(models, "subscription_models", lambda: ["claude-code/opus", "codex/default"])
    api.cfg(engine={"model": "claude-code/opus"})
    got = {c["ref"]: c for c in api.client.get("/api/setup/purposes?probe=0", headers=H).json()["choices"]["models"]}
    assert got["codex/default"]["plan"] == "codex" and got["codex/default"]["oneShot"] is False and got["claude-code/opus"]["oneShot"] is True
    for k in ("json", "compact"):
        r = put(api.client, {k: "codex/default"})
        assert r.status_code == 400 and "command-line plan" in r.text, (k, r.text)
    assert "json" not in (api.conf().get("engine") or {}).get("use", {})
    assert put(api.client, {"json": "claude-code/opus"}).status_code == 200
    assert models.route("json", kind="owner", origin="user") == "claude-code/opus"


def test_background_moves_one_shots_only_while_they_follow_it(api, keys):
    """PUT background 以前每次都把 use.json 改成一样的；10/20 起只在一问一答还跟着后台（没单独设过）时才一起改。"""
    c = api.client
    api.cfg(engine={"model": FLASH})
    use = lambda: api.conf()["engine"]["use"]  # noqa: E731
    put(c, {"background": K3})
    assert use()["json"] == K3  # 没写过：跟着
    put(c, {"background": FLASH})
    assert use()["json"] == FLASH  # 和后台一样：还跟着
    put(c, {"json": K26})  # 单独选一个
    put(c, {"background": K3})
    assert use()["json"] == K26 and use()["background"] == K3
    put(c, {"background": None})
    assert use()["json"] == K26 and "background" not in use()
    put(c, {"background": K3})
    put(c, {"json": K3})  # 单独选的正好和后台一样：照样不跟着
    put(c, {"background": FLASH})
    assert use()["json"] == K3
    put(c, {"json": "default"})  # 回到跟着后台
    assert use()["json"] == FLASH
    put(c, {"background": K3})
    assert use()["json"] == K3
    put(c, {"background": None})
    assert use()["json"] is True and "background" not in use()  # 跟着后台一起回到默认模型
    put(c, {"json": "default"})  # 后台没写：跟着它 = 默认模型
    assert use()["json"] is True and rows(c)["json"]["follows"] == "background"
    api.cfg(engine={"model": FLASH, "use": {"background": FLASH, "json": K26}})  # 手改成和后台不一样的：也算单独设过
    put(c, {"background": K3})
    assert use()["json"] == K26


def test_the_feature_name_tells_the_app():
    import version
    assert "setup.purposes.oneshot" in version.FEATURES
