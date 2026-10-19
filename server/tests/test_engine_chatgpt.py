"""内核第 11b 项：用户自己的 ChatGPT 套餐（engine/chatgpt_plan.py：Sign in with ChatGPT 的授权、令牌、刷新、撤销；chatgpt/<型号> 走
Responses API；谁能花它：models.may_spend）。

不连真的 OpenAI、不花钱：授权服务器是 tests/fake_openai_auth.py（发现文档、JWKS、authorize 回跳、token、refresh、revoke），推理和模型目录是
evals/fakemodel.py 的假服务（/v1/responses、/v1/models，只认授权服务器现在发出去的那把访问令牌）；非套餐的模型是另一个假服务。"""
from __future__ import annotations

import asyncio
import json
import os
import stat
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

import engine
from config import settings
from engine import chatgpt_plan, fallback, loop, models, provider_responses, runners
from engine.errors import EngineError
from engine.session import MemorySession, user
from evals.fakemodel import Fail, FakeModelServer, Reply
from fake_openai_auth import FakeAuthServer

PLAN = "chatgpt/fake-model"


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def plan(tmp_path, monkeypatch, server_json):
    """假授权服务器 + 套餐的假模型服务（只认现在的访问令牌）+ 非套餐的假模型服务 other/m；这个测试自己的 data_dir。"""
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(loop, "MODEL_RETRIES", (0, 0))
    monkeypatch.setattr(provider_responses, "RETRY_DELAYS", (0, 0))
    with FakeAuthServer() as fa:
        fm = FakeModelServer([], key=fa.valid_access).start()
        other = FakeModelServer([], default=Reply(text="from other")).start()
        monkeypatch.setattr(chatgpt_plan, "ISSUER", fa.url)
        monkeypatch.setattr(chatgpt_plan, "API", fm.openai_url)
        monkeypatch.setattr(chatgpt_plan, "PORT", 0)
        monkeypatch.setattr(chatgpt_plan, "REVOKE_DELAYS", (0.01, 0.01))
        chatgpt_plan.reset()
        fallback.reset()

        def config(**engine_extra):
            server_json({"claw": {"kind": "builtin"}, "engine": {"model": PLAN, "models": [PLAN, "other/m"],
                         "providers": {"other": {"api": "openai", "url": other.openai_url, "key_env": ""}}, **engine_extra}})
        config()
        try:
            yield SimpleNamespace(fa=fa, fm=fm, other=other, config=config, dir=tmp_path / "data" / "chatgpt")
        finally:
            chatgpt_plan.reset()
            fallback.reset()
            fm.stop()
            other.stop()


async def authorize(url: str) -> str:
    """当一回浏览器：打开授权网址，假授权服务器当成用户同意了，回 302；→ 回跳的地址（不跟过去）。"""
    async with httpx.AsyncClient() as c:
        r = await c.get(url, follow_redirects=False)
    assert r.status_code == 302, r.text
    return r.headers["location"]


async def connect(account: str = "auto", *, paste: bool = False) -> dict:
    """开始授权 → 浏览器同意 → 回调落到这台服务器的监听上（同一台机器 / SSH 转发），或者粘回来（手机）。"""
    start = await chatgpt_plan.start_login(account)
    loc = await authorize(start["url"])
    if paste:
        return {"start": start, "loc": loc, "status": await chatgpt_plan.submit_callback(loc)}
    async with httpx.AsyncClient() as c:
        page = await c.get(loc)
    return {"start": start, "loc": loc, "page": page}


def params(url: str) -> dict:
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


def cred() -> dict:
    return chatgpt_plan.record()


def set_cred(**changes) -> None:
    rec = cred()
    chatgpt_plan.write_private(chatgpt_plan.record_path(rec["client_id"]), {**rec, **changes})


def mode(p: Path) -> int:
    return stat.S_IMODE(p.stat().st_mode)


# —— 授权 ————————————————————————————————————————————————————————————————————————

def test_browser_callback_on_this_machine_connects_and_stores_a_private_credential(plan):
    out = run(connect())
    q = params(out["start"]["url"])
    assert q["client_id"] == "dynamic_agent_client" and q["agent_name_hint"] == "OpenMousse" and q["response_type"] == "code"
    assert q["ext_agent_host_id"].startswith("urn:ietf:params:oauth:jwk-thumbprint:sha-256:")
    assert set(q["scope"].split()) == {"openid", "profile", "email", "offline_access", "resource.invoke", "chatgpt.tokens.use.direct"}
    assert q["resource"] == "https://api.openai.com/v1" and q["code_challenge_method"] == "S256" and q["state"] and q["nonce"]
    redirect = urlsplit(q["redirect_uri"])
    assert redirect.scheme == "http" and redirect.hostname == "127.0.0.1" and redirect.path == "/auth/callback" and "localhost" not in q["redirect_uri"]
    assert out["page"].status_code == 200 and "connected" in out["page"].text
    st = chatgpt_plan.status()
    assert st["connected"] and st["planEnabled"] and st["ready"] and st["plan"] == "plus" and st["email"] == "alex@example.com"
    assert st["models"] == [PLAN] and PLAN in models.models() and st["expiresIn"] > 3000 and not st["expiringSoon"]  # 目录连上时就取了
    rec = cred()
    assert rec["client_id"] == "oaiapp_fake1" and rec["subject"] == "user-fake-1" and rec["access_token"].startswith("at-")
    assert rec["refresh_token"].startswith("rt-") and rec["id_token"].count(".") == 2 and rec["ext_agent_host_id"] == q["ext_agent_host_id"]
    assert "chatgpt.tokens.use.direct" in rec["scopes"] and rec["expires_at"] > time.time() + 3000
    tok = plan.fa.of("/api/accounts/oauth/token")[0]
    assert tok["grant"] == "authorization_code" and tok["client_id"] == "oaiapp_fake1" and tok["resource"] == "https://api.openai.com/v1"
    assert tok["redirect_uri"] == q["redirect_uri"]  # 授权和换令牌用同一个回调地址（PKCE 假服务器也验过了）
    assert mode(plan.dir) == 0o700 and mode(chatgpt_plan.record_path("oaiapp_fake1")) == 0o600 and mode(plan.dir / "host.json") == 0o600
    assert mode(plan.dir / "state.json") == 0o600 and not [p for p in plan.dir.iterdir() if p.name.endswith(".tmp")]
    assert (plan.dir / ".gitignore").read_text() == "*\n"
    text = json.dumps(st) + json.dumps(chatgpt_plan.login_status())
    for secret in (rec["access_token"], rec["refresh_token"], rec["id_token"], rec["subject"], rec["client_id"]):
        assert secret not in text  # 回给 app 的状态里没有令牌、没有邮箱以外的身份
    assert chatgpt_plan.login_status()["status"] == "done"


def test_headless_server_takes_the_pasted_callback_and_reconnects_on_the_saved_client(plan):
    out = run(connect(paste=True))
    assert out["status"]["connected"] and cred()["client_id"] == "oaiapp_fake1"
    hid = cred()["ext_agent_host_id"]

    async def again():  # 手机上复制的地址有时不带 http://；重连用存下的 client_id，不带 agent_name_hint，带 login_hint
        start = await chatgpt_plan.start_login()
        loc = await authorize(start["url"])
        return start, await chatgpt_plan.submit_callback(loc.removeprefix("http://"))
    start, st = run(again())
    q = params(start["url"])
    assert q["client_id"] == "oaiapp_fake1" and "agent_name_hint" not in q and q["login_hint"] == "alex@example.com"
    assert "id_token_hint" not in q and "prompt" not in q and q["ext_agent_host_id"] == hid  # 授权网址交给 app：不放 ID token
    assert st["connected"] and plan.fa.clients == 1


def test_wrong_state_or_port_is_ignored_and_the_real_callback_still_works(plan):
    async def go():
        start = await chatgpt_plan.start_login()
        loc = await authorize(start["url"])
        real = params(loc)
        bad = loc.replace(real["state"], "forged-state")
        with pytest.raises(chatgpt_plan.PlanError, match="state"):
            await chatgpt_plan.submit_callback(bad)
        port = urlsplit(loc).port
        with pytest.raises(chatgpt_plan.PlanError, match="port"):
            await chatgpt_plan.submit_callback(loc.replace(f":{port}/", f":{port + 1}/"))
        assert chatgpt_plan.login_status()["status"] == "pending"
        return await chatgpt_plan.submit_callback(loc)
    assert run(go())["connected"]
    assert len(plan.fa.of("/api/accounts/oauth/token")) == 1


def test_declined_consent_and_incomplete_registration_exchange_nothing(plan):
    plan.fa.deny = True
    with pytest.raises(chatgpt_plan.PlanError, match="declined"):
        run(connect(paste=True))
    assert chatgpt_plan.login_status()["status"] == "error"
    plan.fa.deny, plan.fa.omit_client_id = False, True
    with pytest.raises(chatgpt_plan.PlanError, match="client_id"):
        run(connect(paste=True))
    assert not plan.fa.of("/api/accounts/oauth/token") and not chatgpt_plan.status()["connected"]


def test_an_id_token_that_doesnt_match_the_nonce_is_refused(plan):
    plan.fa.wrong_nonce = True
    with pytest.raises(chatgpt_plan.PlanError, match="verified"):
        run(connect(paste=True))
    assert not chatgpt_plan.status()["connected"]


def test_identity_only_grant_keeps_the_sign_in_but_no_plan_models_until_reconnected(plan):
    plan.fa.scope = "openid profile email offline_access"
    run(connect(paste=True))
    st = chatgpt_plan.status()
    assert st["connected"] and not st["planEnabled"] and not st["ready"] and st["models"] == []
    assert PLAN not in models.models() and models.models() == ["other/m"]
    with pytest.raises(EngineError, match="plan use isn't enabled"):
        run(chatgpt_plan.access_token())
    plan.fa.scope = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
    out = run(connect(paste=True))
    q = params(out["start"]["url"])
    assert q["client_id"] == "oaiapp_fake1" and q["prompt"] == "consent"  # 上次没开套餐：完整的 scope 再问一次
    assert chatgpt_plan.status()["planEnabled"] and chatgpt_plan.usable()


def test_a_different_account_on_the_saved_client_is_refused(plan):
    run(connect(paste=True))
    plan.fa.subject = "someone-else"
    with pytest.raises(chatgpt_plan.PlanError, match="different ChatGPT account"):
        run(connect(paste=True))
    assert cred()["subject"] == "user-fake-1"


def test_only_one_account_switching_revokes_and_clears_the_old_one(plan):
    run(connect(paste=True))
    old = cred()
    plan.fa.subject, plan.fa.email = "user-fake-2", "sam@example.com"
    out = run(connect("new", paste=True))
    assert params(out["start"]["url"])["client_id"] == "dynamic_agent_client"
    assert cred()["client_id"] == "oaiapp_fake2" and chatgpt_plan.status()["email"] == "sam@example.com"
    gone = chatgpt_plan.record("oaiapp_fake1")
    assert gone["client_id"] == "oaiapp_fake1" and "access_token" not in gone and "refresh_token" not in gone
    rv = plan.fa.of("/oauth/revoke")
    assert rv and rv[0]["client_id"] == "oaiapp_fake1" and rv[0]["hint"] == "refresh_token"
    assert not plan.fa.valid_access(old["access_token"])


def test_turned_off_in_server_json(plan):
    plan.config(chatgpt=False)
    with pytest.raises(chatgpt_plan.PlanError, match="turned off"):
        run(chatgpt_plan.start_login())
    assert not chatgpt_plan.enabled() and not models.subscription_ready(PLAN)


# —— 凭据文件 ————————————————————————————————————————————————————————————————————

def test_credential_writes_are_atomic_and_owner_only(tmp_path, monkeypatch):
    path = tmp_path / "x" / "client-a.json"
    old = os.umask(0)
    try:
        chatgpt_plan.write_private(path, {"v": 1})
    finally:
        os.umask(old)
    assert mode(path) == 0o600 and json.loads(path.read_text()) == {"v": 1}

    def boom(*_a, **_k):
        raise OSError("disk full")
    monkeypatch.setattr(json, "dump", boom)
    with pytest.raises(OSError):
        chatgpt_plan.write_private(path, {"v": 2})
    assert json.loads(path.read_text()) == {"v": 1} and [p.name for p in path.parent.iterdir()] == ["client-a.json"]  # 旧的还在、没有残留


# —— 刷新 ————————————————————————————————————————————————————————————————————————

def test_an_expiring_token_is_refreshed_before_use_and_rotated(plan):
    run(connect(paste=True))
    before = cred()
    set_cred(expires_at=time.time() + 60)
    tok = run(chatgpt_plan.access_token())
    after = cred()
    assert tok == after["access_token"] != before["access_token"] and after["refresh_token"] != before["refresh_token"]
    r = plan.fa.of("/api/accounts/oauth/token")[-1]
    assert r["grant"] == "refresh_token" and r["client_id"] == "oaiapp_fake1" and r["resource"] == "https://api.openai.com/v1"
    assert not r["has_scope"]  # 刷新不带 scope
    assert after["scopes"] == before["scopes"] and after["id_token"] == before["id_token"] and mode(chatgpt_plan.record_path("oaiapp_fake1")) == 0o600


def test_concurrent_refreshes_go_out_once(plan):
    run(connect(paste=True))
    set_cred(expires_at=time.time() + 10)

    async def go():
        return await asyncio.gather(*(chatgpt_plan.access_token() for _ in range(6)))
    toks = run(go())
    assert len(set(toks)) == 1 and len([r for r in plan.fa.of("/api/accounts/oauth/token") if r["grant"] == "refresh_token"]) == 1


def test_a_refresh_done_by_another_process_is_adopted(plan):
    run(connect(paste=True))
    stale = {**cred(), "expires_at": time.time() + 10}
    newer = plan.fa._issue("oaiapp_fake1", None, "", with_id=False)  # 别的进程刚刷过，写进了文件
    set_cred(access_token=newer["access_token"], refresh_token=newer["refresh_token"], expires_at=time.time() + 3600)
    out = run(chatgpt_plan.refresh(stale))
    assert out["access_token"] == newer["access_token"] and not [r for r in plan.fa.of("/api/accounts/oauth/token") if r["grant"] == "refresh_token"]


def test_earliest_refresh_at_is_respected_while_the_token_still_works(plan):
    run(connect(paste=True))
    first = cred()["access_token"]
    set_cred(expires_at=time.time() + 200, earliest_refresh_at=time.time() + 600)
    assert run(chatgpt_plan.access_token()) == first
    set_cred(expires_at=time.time() + 5)  # 快不能用了：照样刷新
    assert run(chatgpt_plan.access_token()) != first


def test_a_dead_refresh_token_asks_to_reconnect(plan):
    run(connect(paste=True))
    set_cred(expires_at=time.time() + 10)
    plan.fa.refresh_fail = {"status": 400, "error": "invalid_grant"}
    with pytest.raises(EngineError, match="connected again") as ei:
        run(chatgpt_plan.access_token())
    assert ei.value.kind == "config" and ei.value.status == 401
    st = chatgpt_plan.status()
    assert st["needsReconnect"] and not st["connected"] and st["models"] == [] and PLAN not in models.models()
    rec = cred()
    assert rec["client_id"] == "oaiapp_fake1" and not ({"access_token", "refresh_token", "id_token"} & set(rec))


def test_a_refresh_server_error_keeps_the_credentials(plan):
    run(connect(paste=True))
    set_cred(expires_at=time.time() + 10)
    keep = cred()["refresh_token"]
    plan.fa.refresh_fail = {"status": 503, "error": "server_error"}
    with pytest.raises(EngineError) as ei:
        run(chatgpt_plan.access_token())
    assert ei.value.retryable and cred()["refresh_token"] == keep and not chatgpt_plan.status()["needsReconnect"]
    assert chatgpt_plan.status()["lastError"]["code"] == "server_error"


# —— 推理 ————————————————————————————————————————————————————————————————————————

def test_a_plan_turn_calls_the_public_responses_api_with_the_access_token(plan):
    run(connect(paste=True))
    plan.fm.push(Reply(text="Hi from your plan."))
    spec = engine.Spec(kind="owner", model=PLAN, system="You help.", origin="user", max_tokens=500, display="summarized")
    res = run(engine.run_turn(spec, MemorySession(), user("hello")))
    assert res.status == "done" and res.text == "Hi from your plan." and res.usage["cost"] is None
    req = plan.fm.of("responses")[0]
    assert req["path"] == "/v1/responses" and req["model"] == "fake-model" and req["auth"]
    body = req["body"]
    assert body["store"] is False and body["stream"] is True and "max_output_tokens" not in body and body["instructions"] == "You help."
    assert not {"temperature", "top_p", "metadata", "user", "previous_response_id", "prompt_cache_retention"} & set(body)
    assert "backend-api" not in chatgpt_plan.API


def test_a_rejected_token_is_refreshed_once_then_reconnect_is_asked(plan):
    run(connect(paste=True))
    plan.fa.access.clear()  # 访问令牌不认了，刷新令牌还能用：刷新一次、再试就好
    plan.fm.push(Reply(text="after refresh"))
    res = run(engine.run_turn(engine.Spec(kind="owner", model=PLAN, origin="user"), MemorySession(), user("x")))
    assert res.status == "done" and res.text == "after refresh" and len(plan.fm.of("responses")) == 2
    assert len([r for r in plan.fa.of("/api/accounts/oauth/token") if r["grant"] == "refresh_token"]) == 1
    plan.fa.revoke_access()  # 用户在 ChatGPT 设置里断开了：不通知我们，下一次 401 才知道
    res = run(engine.run_turn(engine.Spec(kind="owner", model=PLAN, origin="user"), MemorySession(), user("y")))
    assert res.status == "error" and res.error_kind == "config" and "connected again" in res.error
    st = chatgpt_plan.status()
    assert st["needsReconnect"] and PLAN not in models.models()


def test_usage_limit_is_reported_with_the_manage_usage_link(plan):
    run(connect(paste=True))
    plan.fm.push(Fail(429, "You have reached your usage limit.", code="subscription_sharing_usage_limit_exceeded"))
    res = run(engine.run_turn(engine.Spec(kind="owner", model=PLAN, origin="user"), MemorySession(), user("x")))
    assert res.status == "error" and "chatgpt.com/settings/usage" in res.error and len(plan.fm.of("responses")) == 1  # 不重试
    assert chatgpt_plan.status()["lastError"]["code"] == "subscription_sharing_usage_limit_exceeded"


def test_an_error_echoing_the_access_token_is_scrubbed_before_it_is_kept(plan):
    """套餐的报错原文要是带回了访问令牌：engine_steps、engine_turns、回给对话的那句、套餐最近一次的错（lastError）里都只剩 ***。"""
    from engine import events
    run(connect(paste=True))
    token = next(iter(plan.fa.access))
    plan.fm.push(Fail(400, f"Malformed request for token {token}.", code="invalid_request_error"))
    res = run(engine.run_turn(engine.Spec(kind="owner", model=PLAN, origin="user", conversation="t-plan-token"), MemorySession(), user("x")))
    assert res.status == "error" and "***" in res.error
    step = next(s for s in events.steps(res.turn) if s["type"] == "model")
    texts = (res.error, step["data"]["error"], events.turns("t-plan-token")[0]["error"], chatgpt_plan.status()["lastError"]["message"])
    assert all(token not in t for t in texts)


def test_disconnect_revokes_the_refresh_token_and_clears_it_here(plan):
    run(connect(paste=True))
    rt = cred()["refresh_token"]
    out = run(chatgpt_plan.disconnect())
    assert out["revoked"] and not chatgpt_plan.status()["connected"] and not chatgpt_plan.usable()
    rv = plan.fa.of("/oauth/revoke")[0]
    assert rv["hint"] == "refresh_token" and rv["client_id"] == "oaiapp_fake1" and not plan.fa.refresh[rt]["live"]
    rec = chatgpt_plan.record("oaiapp_fake1")
    assert rec["subject"] == "user-fake-1" and "refresh_token" not in rec
    out = run(connect(paste=True))  # 下次重连用同一个注册
    assert params(out["start"]["url"])["client_id"] == "oaiapp_fake1"


def test_unconfirmed_revocation_still_clears_the_tokens_and_says_so(plan):
    run(connect(paste=True))
    plan.fa.revoke_status = 503
    out = run(chatgpt_plan.disconnect())
    assert not out["revoked"] and "ChatGPT" in out["note"] and len(plan.fa.of("/oauth/revoke")) == 3  # 退避重试过
    assert "access_token" not in chatgpt_plan.record("oaiapp_fake1")


def test_models_come_from_the_account_catalog_or_server_json(plan):
    plan.fm.catalog = [{"slug": "gpt-6.1-sol", "display_name": "GPT-6.1 Sol", "visibility": "list"},
                       {"slug": "secret-model", "display_name": "Hidden", "visibility": "hide"}]
    run(connect(paste=True))
    run(chatgpt_plan.refresh_catalog())
    assert chatgpt_plan.model_ids() == ["chatgpt/gpt-6.1-sol"] and "chatgpt/gpt-6.1-sol" in models.models()
    plan.config(chatgpt={"models": ["gpt-6.1-sol", "chatgpt/gpt-6.1-terra"]})
    assert chatgpt_plan.model_ids() == ["chatgpt/gpt-6.1-sol", "chatgpt/gpt-6.1-terra"]


# —— 谁能花 ————————————————————————————————————————————————————————————————————————

def test_who_may_spend_the_chatgpt_plan(plan):
    assert models.subscription(PLAN) == "chatgpt" and not models.valid_ref(PLAN) and models.known(PLAN)
    for origin in ("user", "relay"):
        assert models.may_spend(PLAN, kind="owner", origin=origin)
    for origin in ("auto", "task"):
        assert not models.may_spend(PLAN, kind="owner", origin=origin)
    for kind, origin, via in (("owner", "api", None), ("owner", "acp", None), ("owner", "compact", None), ("owner", "skill-review", None),
                              ("owner", None, None), ("cyber", "user", None), ("outsider", "card", None), ("outsider", "a2a", None),
                              ("owner", "relay", "mcp"), ("owner", "user", "-"), ("owner", "auto", "mcp")):
        assert not models.may_spend(PLAN, kind=kind, origin=origin, via=via), (kind, origin, via)
    assert not models.may_spend(PLAN, kind="owner", origin="relay", via="auto")  # 后台的一轮里调了转交：还是后台
    chatgpt_plan.set_background(True)
    assert chatgpt_plan.status()["background"]["allowed"] and chatgpt_plan.status()["background"]["since"]
    assert models.may_spend(PLAN, kind="owner", origin="auto") and models.may_spend(PLAN, kind="owner", origin="relay", via="task")
    assert not models.may_spend(PLAN, kind="owner", origin="relay", via="mcp") and not models.may_spend(PLAN, kind="cyber", origin="user")
    assert models.may_spend("claude-code/opus", kind="owner", origin="relay", via="mcp")  # Claude Code 那边照旧（不登录时一切照旧）
    assert models.stricter("user", "auto") == "auto" and models.stricter("auto", None) == "auto" and models.plan_class("user", "mcp") == "never"


def test_outside_route_and_fallback_chains_never_pick_the_plan(plan):
    run(connect(paste=True))
    plan.config(fallbacks=[PLAN, "other/m"], use={"card": True, "json": PLAN})
    assert models.outside(PLAN) == "other/m" and models.route("card") == "other/m" and models.route("json") == "other/m"
    assert models.fallbacks() == ["other/m"] and fallback.chain(engine.Spec(kind="owner", model=PLAN)) == [PLAN, "other/m"]
    assert models.chain_of([PLAN, "other/m"]) == ["other/m"]


def test_cyber_outsider_one_shot_and_external_turns_dont_spend_the_plan(plan, monkeypatch):
    run(connect(paste=True))
    from engine import cyber, oneshot, owner
    res = run(cyber.chat({"id": "cy1", "name": "Cy", "identity": "a friend"}, "hello"))
    assert res.model == "other/m" and res.text == "from other"
    plan.other.push(Reply(text='{"ok": true}'))
    v, via = run(oneshot.ask_json("JSON please.", {"q": 1}, model=models.route("json") or models.outside(PLAN) or ""))
    assert via == "builtin:other/m"
    assert run(owner.stream([{"role": "user", "content": "quick question"}], "k", PLAN, lambda _t: None)) == "from other"
    with pytest.raises(EngineError, match="ChatGPT plan"):
        run(engine.run_turn(engine.Spec(kind="cyber", model=PLAN), MemorySession(), user("x")))
    with pytest.raises(EngineError, match="ChatGPT plan"):
        run(engine.run_turn(engine.Spec(kind="owner", model=PLAN, origin="user", via="mcp"), MemorySession(), user("x")))
    assert not plan.fm.of("responses")  # 套餐的服务一次都没被调过


def test_owner_turns_in_the_app_use_the_plan_but_api_and_mcp_ones_dont(plan, monkeypatch):
    run(connect(paste=True))
    from engine import owner
    calls: list = []
    import bridges

    async def fake_run(name, args, stdin, agent, *, kill_on_cancel=False):
        calls.append(name)
        return "ok"
    monkeypatch.setattr(bridges, "run", fake_run)
    plan.fm.push(Reply(text="on your plan"))
    res = run(owner.respond("main", "2026-10-17", "hi", PLAN, lambda _t: None))
    assert res.status == "done" and res.text == "on your plan" and res.model == PLAN
    res = run(owner.respond("main", "2026-10-17", "again", PLAN, lambda _t: None, via="mcp"))
    assert res.model == "other/m"
    res = run(owner.respond("api:main:x", "2026-10-17", "from the api", PLAN, lambda _t: None, origin="api", agent="main", history=[]))
    assert res.model == "other/m" and len(plan.fm.of("responses")) == 1


def test_background_runs_need_consent_then_use_the_plan(plan, monkeypatch):
    run(connect(paste=True))
    from engine import tasks, unattended
    import bridges

    async def fake_run(name, args, stdin, agent, *, kill_on_cancel=False):
        return "ok"
    monkeypatch.setattr(bridges, "run", fake_run)
    out = run(unattended.fresh("main", "main", "cron:j1", "check the weather", model=PLAN, light=True))
    assert out.model == "other/m" and not plan.fm.of("responses")
    with pytest.raises(engine.ToolFailure, match="allow"):
        tasks._model_arg({"model": PLAN}, tasks.ENGINE, "main")
    assert tasks._model_arg({}, tasks.ENGINE, "main") == "other/m"  # 默认的是套餐、没同意：换非套餐的
    plan.config(models=[PLAN])  # 没有别的模型可换：不跑，说清楚
    out = run(unattended.fresh("main", "main", "cron:j1", "check", model=PLAN, light=True))
    assert out.status == "error" and "allow it once in Settings" in out.error
    plan.config()
    chatgpt_plan.set_background(True)
    plan.fm.push(Reply(text="Sunny."))
    out = run(unattended.fresh("main", "main", "cron:j1", "check the weather", model=PLAN, light=True))
    assert out.model == PLAN and out.text == "Sunny." and len(plan.fm.of("responses")) == 1
    assert tasks._model_arg({"model": PLAN}, tasks.ENGINE, "main") == PLAN and tasks.pick_runner("", PLAN, "main")[:2] == (tasks.ENGINE, PLAN)
    # 经 /mcp、对外接口引出来的那一轮派的任务：同意过后台用也不花
    assert tasks._model_arg({}, tasks.ENGINE, "main", "mcp") == "other/m" and tasks.pick_runner("", "", "main", "api")[:2] == (tasks.ENGINE, "other/m")
    with pytest.raises(engine.ToolFailure):
        tasks.pick_runner("", PLAN, "main", "mcp")


def test_compaction_of_a_plan_turn_runs_as_that_turn(plan):
    run(connect(paste=True))
    from engine import compact
    from engine.session import View
    plan.fm.push(Reply(text="<summary>We talked about the weather.</summary>"))
    old = [user("old message"), {"role": "assistant", "content": [{"type": "text", "text": "old reply"}], "api": None, "provider": None,
                                 "model": None, "stop": "end", "usage": {}}]
    view = View(messages=old, rows=[({}, m) for m in old], summary=None, system="sys")
    spec = engine.Spec(kind="owner", model=PLAN, origin="user", conversation="main")
    summary, _usage, _turns = run(compact.summarize(view, 2, spec))
    assert summary == "We talked about the weather." and len(plan.fm.of("responses")) == 1
    with pytest.raises(EngineError):  # 后台的一轮、没同意：压缩也不花
        run(compact.summarize(view, 2, engine.Spec(kind="owner", model=PLAN, origin="auto", conversation="main")))


def test_task_results_coming_back_count_as_background(plan, monkeypatch):
    import chat
    started: list = []

    async def fake_gateway(run_, content, token):
        started.append(run_)
    monkeypatch.setattr(chat, "run_gateway", fake_gateway)

    async def go():
        r = chat.begin("t-x", PLAN, 1, chat.now_iso(), "results", "", via="task")
        token = chat.REQUEST_VIA.set("mcp")
        try:
            r2 = chat.begin("t-y", PLAN, 2, chat.now_iso(), "relay", "", origin="relay")
        finally:
            chat.REQUEST_VIA.reset(token)
        await asyncio.sleep(0)
        return r, r2
    r, r2 = run(go())
    assert r.via == "task" and r2.via == "mcp" and r.origin == "user"
    chat.RUNS.pop("t-x", None)
    chat.RUNS.pop("t-y", None)


# —— /mcp 和内核工具跑的脚本带上 via ————————————————————————————————————————————————————

def test_bridge_scripts_get_via_from_mcp_and_from_the_engine_turn(plan, monkeypatch, tmp_path):
    import bridges
    script = tmp_path / "show_via.py"
    script.write_text("import os\nprint(os.environ.get('MOUSSE_VIA') or 'none')\n", encoding="utf8")
    plan.config()
    raw = json.loads(Path(__import__("config").CONFIG_PATH).read_text())
    raw["mcp"] = {"scripts": {"journal": [sys.executable, str(script)]}}
    Path(__import__("config").CONFIG_PATH).write_text(json.dumps(raw))
    __import__("config")._cache = None
    assert run(bridges.run("journal", [], None, "main")).strip() == "none"
    pytest.importorskip("mcp")
    import mcp_bridge
    assert run(mcp_bridge.run("journal", [], None, "main")).strip() == "mcp"
    from engine import tools as tools_mod
    tool = {t.name: t for t in tools_mod.for_agent("main")}["journal"]
    ctx = engine.ToolCtx(turn="t1", kind="owner", agent="main", conversation="main", call_id="c1", control=engine.Control(), origin="auto")
    assert run(tool.run({"args": ["list"]}, ctx)).strip() == "auto"


def test_the_guard_reads_the_via_header(plan):
    import chat
    import config
    import main
    from fastapi.routing import APIRoute
    from starlette.testclient import TestClient
    seen: list = []

    def probe():
        seen.append(chat.REQUEST_VIA.get())
        return {"ok": True}
    raw = json.loads(Path(config.CONFIG_PATH).read_text())
    raw["auth"] = {"tokens": {"phone": "tok-phone"}}
    Path(config.CONFIG_PATH).write_text(json.dumps(raw))
    config._cache = None
    main.app.router.routes.insert(0, APIRoute("/api/_test_via", probe, methods=["GET"]))  # 放在最前面：后面的 / 静态挂载会接走别的
    try:
        c = TestClient(main.app)
        h = {"Authorization": "Bearer tok-phone"}
        for extra in ({}, {"X-Mousse-Via": "mcp"}, {"X-Mousse-Via": "User Agent!"}):
            assert c.get("/api/_test_via", headers={**h, **extra}).status_code == 200
    finally:
        main.app.router.routes[:] = [r for r in main.app.router.routes if getattr(r, "path", "") != "/api/_test_via"]
    assert seen == [None, "mcp", "-"] and chat.REQUEST_VIA.get() is None


# —— Codex 外援用套餐 ———————————————————————————————————————————————————————————————————

FAKE_CODEX = '''#!{python}
import json, os, sys
prompt = sys.stdin.read()
json.dump({{"argv": sys.argv[1:], "token": os.environ.get("ACCESS_TOKEN"), "mcp": bool(os.environ.get("MOUSSE_TASK_MCP_TOKEN"))}},
          open(os.environ["FAKE_LOG"], "w"))
for e in ({{"type": "thread.started", "thread_id": "x"}}, {{"type": "item.completed", "item": {{"type": "agent_message", "text": "Done on the plan."}}}},
          {{"type": "turn.completed", "usage": {{"input_tokens": 5, "output_tokens": 2}}}}):
    print(json.dumps(e), flush=True)
'''


def test_codex_without_its_own_login_runs_on_the_plan_token_after_consent(plan, monkeypatch, tmp_path):
    run(connect(paste=True))
    exe = tmp_path / "codex"
    exe.write_text(FAKE_CODEX.format(python=sys.executable), encoding="utf8")
    exe.chmod(0o755)
    log = tmp_path / "codex.json"
    monkeypatch.setenv("FAKE_LOG", str(log))
    monkeypatch.setattr(runners, "_found", {})
    monkeypatch.setattr(runners, "_codex_bin", str(exe))
    assert "codex" not in runners.offered()  # 没同意后台用：不出现
    chatgpt_plan.set_background(True)
    assert runners.offered() == ["codex"] and runners.available()["codex"]["billing"] == "subscription"
    from engine import tasks
    assert tasks.pick_runner("codex", "", "main", "user")[0] == "codex"
    with pytest.raises(engine.ToolFailure):  # /mcp 引出来的那一轮派的：Codex 也不拿套餐的令牌跑
        tasks.pick_runner("codex", "", "main", "mcp")
    row = {"id": "tk_codex_plan", "runner": "codex", "model": "fake-model", "agent": "main", "level": "read", "thread": "t"}
    live = SimpleNamespace(proc=None, reason=None)
    out = run(runners.run(row, live, prompt="Do it.", system="Rules.", tools=[], timeout=60, on_step=lambda *_a: None))
    assert out.status == "done" and out.text == "Done on the plan."
    got = json.loads(log.read_text())
    argv = got["argv"]
    assert got["token"] == cred()["access_token"] and not any(got["token"] in a for a in argv) and got["mcp"]  # 令牌只在环境变量里
    assert f'model_provider="{runners.PLAN_PROVIDER}"' in argv and f'model_providers.{runners.PLAN_PROVIDER}.env_key="ACCESS_TOKEN"' in argv
    assert f'model_providers.{runners.PLAN_PROVIDER}.wire_api="responses"' in argv and "-m" in argv and argv[argv.index("-m") + 1] == "fake-model"
    assert f'model_providers.{runners.PLAN_PROVIDER}.base_url="{chatgpt_plan.API}"' in argv


# —— 不登录：一切照旧 ———————————————————————————————————————————————————————————————————

def test_without_signing_in_nothing_changes(tmp_path, monkeypatch, server_json):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    chatgpt_plan.reset()
    server_json({"claw": {"kind": "builtin"}, "engine": {"model": "deepseek/deepseek-flash", "models": ["deepseek/deepseek-flash", "moonshot/kimi-k3"]}})
    from engine import api
    assert models.models() == ["deepseek/deepseek-flash", "moonshot/kimi-k3"] and models.subscription_models() == []
    st = api.status()
    assert st["chatgpt"]["connected"] is False and st["chatgpt"]["models"] == [] and "chatgpt" not in [p["name"] for p in st["providers"]]
    assert not models.subscription_ready(PLAN) and not models.has_key(PLAN) and models.outside(PLAN) == "deepseek/deepseek-flash"
    assert models.may_spend("deepseek/deepseek-flash", kind="cyber", origin="user")
    assert not (tmp_path / "data" / "chatgpt").exists()  # 看状态、列模型都不在 data_dir 里留东西
    with pytest.raises(EngineError, match="isn't connected"):
        run(chatgpt_plan.access_token())
