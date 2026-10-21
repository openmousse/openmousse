"""用户自己的 Grok 订阅（engine/grok_plan.py，10/20）：xAI 的 OAuth 设备码登录、令牌存在本机、刷新、退出、被 xAI 拒（403）。

不连真的 xAI、不用真账号、不花钱：账号服务和 api.x.ai 都是 tests/fake_xai.py 的假服务（发现文档、设备码、令牌、刷新、撤销；对话、搜索、
生图、生视频、朗读、用量都在同一个端口上）。数据都是编的。"""
from __future__ import annotations

import asyncio
import json
import stat
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import engine
from config import settings
from engine import chatgpt_plan, fallback, grok_plan, loop, models, provider_responses
from engine.errors import EngineError
from engine.session import MemorySession, user
from evals.fakemodel import FakeModelServer, Reply
from fake_xai import FakeXai

TOKENS = {"phone": "tok-phone", "mcp": "tok-mcp"}
H = {"Authorization": "Bearer tok-phone"}
GROK = "grok/grok-4.7"
DAY = "2026-09-29"  # main 对话这一天的会话：过去的、别的测试不用的一天（写成今天会混进别的测试今天的 main 会话）


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def xai(tmp_path, monkeypatch, server_json):
    """假的 xAI + 非套餐的假模型服务 other/m + 这个测试自己的 data_dir；轮询、退避、重试都改成几毫秒。conf(**engine 段) 改 server.json
    （默认 engine.model = grok/grok-4.7，engine.models 里还有 other/m）。这台机器上真的 Claude Code / Codex 当没查到（同 no_cli）：
    设置页的接口没查过会在后台真去查，查到的留在 runners 里，后面别的测试就会去调真的 Claude Code。"""
    from engine import runners
    for k, v in (("_warm", True), ("_found", {}), ("_checked_at", time.monotonic()), ("_codex_bin", None)):
        monkeypatch.setattr(runners, k, v)
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(loop, "MODEL_RETRIES", (0, 0))
    monkeypatch.setattr(provider_responses, "RETRY_DELAYS", (0, 0))
    for k, v in (("MIN_INTERVAL", 0.01), ("INTERVAL", 0.01), ("SLOW_DOWN", 0.01), ("REFRESH_RETRY", 0.01), ("REVOKE_DELAYS", (0.01, 0.01))):
        monkeypatch.setattr(grok_plan, k, v)
    with FakeXai() as fx:
        other = FakeModelServer([], default=Reply(text="from other")).start()
        fx.interval = 0.02
        monkeypatch.setattr(grok_plan, "ISSUER", fx.url)
        monkeypatch.setattr(grok_plan, "API", fx.api)
        monkeypatch.setattr(grok_plan, "PROXY", fx.api)  # 订阅代理（额度、route = proxy）：也是假服务
        grok_plan.reset()
        chatgpt_plan.reset()
        fallback.reset()

        def conf(**engine) -> None:
            server_json({"claw": {"kind": "builtin"}, "auth": {"tokens": TOKENS}, "timezone": "UTC", "language": "en",
                         "engine": {"model": GROK, "models": [GROK, "other/m"],
                                    "providers": {"other": {"api": "openai", "url": other.openai_url, "key_env": ""}}, **engine}})
        conf()
        try:
            yield SimpleNamespace(fx=fx, other=other, conf=conf, dir=tmp_path / "data" / "grok")
        finally:
            grok_plan.reset()
            fallback.reset()
            other.stop()


@pytest.fixture
def quiet_bridges(monkeypatch):
    """主人回合的工具表里有命令行脚本：真跑它们的地方换成什么都不做。"""
    import bridges

    async def fake_run(name, args, stdin, agent, *, kill_on_cancel=False):
        return "ok"
    monkeypatch.setattr(bridges, "run", fake_run)


async def sign_in() -> dict:
    """开始登录 → 假服务器上「用户点了同意」（第二次轮询就给令牌）→ 等它走完。→ {"start", "end"}。"""
    start = await grok_plan.start_login()
    end = await grok_plan.wait_login(10)
    return {"start": start, "end": end}


def cred() -> dict:
    return grok_plan.record()


def set_cred(**changes) -> None:
    grok_plan.write_private(grok_plan.record_path(), {**cred(), **changes})


def mode(p: Path) -> int:
    return stat.S_IMODE(p.stat().st_mode)


# —— 登录 ————————————————————————————————————————————————————————————————————————

def test_device_login_stores_a_private_credential_and_shows_no_tokens(xai):
    out = run(sign_in())
    s = out["start"]
    assert s["url"].startswith(xai.fx.url + "/device?user_code=") and s["code"] and s["interval"] >= 0.01 and "device_code" not in json.dumps(s)
    assert out["end"]["status"] == "done"
    dev = xai.fx.of("/oauth2/device/code")[0]
    assert dev["form"]["client_id"] == grok_plan.CLIENT_ID and set(dev["form"]["scope"].split()) >= {"offline_access", "grok-cli:access", "api:access"}
    polls = xai.fx.of("/oauth2/token")
    assert len(polls) == 2 and all(p["form"]["grant_type"] == grok_plan.DEVICE_GRANT for p in polls)  # 第一次 pending，第二次给了
    assert polls[0]["headers"]["user-agent"].startswith("OpenMousse/")
    st = grok_plan.status()
    assert st["connected"] and st["ready"] and st["email"] == "alex@example.com" and st["expiresIn"] > 20000 and not st["expiringSoon"]
    rec = cred()
    assert rec["access_token"] and rec["refresh_token"].startswith("xrt-") and rec["subject"] == "xai-user-fake-1"
    assert rec["token_endpoint"] == xai.fx.url + "/oauth2/token" and rec["revocation_endpoint"] == xai.fx.url + "/oauth2/revoke"
    assert mode(xai.dir) == 0o700 and mode(grok_plan.record_path()) == 0o600 and mode(xai.dir / "state.json") == 0o600
    assert (xai.dir / ".gitignore").read_text() == "*\n" and not [p for p in xai.dir.iterdir() if p.name.endswith(".tmp")]
    text = json.dumps(st) + json.dumps(grok_plan.login_status())
    for secret in (rec["access_token"], rec["refresh_token"], rec["subject"]):
        assert secret not in text  # 回给 app 的状态里没有令牌、没有 sub
    assert grok_plan.login_status()["status"] == "done" and "code" not in grok_plan.login_status()


def test_slow_down_and_pending_keep_polling(xai):
    xai.fx.slow_down, xai.fx.pending_polls = True, 3
    out = run(sign_in())
    assert out["end"]["status"] == "done" and len(xai.fx.of("/oauth2/token")) == 4


@pytest.mark.parametrize("outcome,status,words", [("deny", "error", "declined"), ("expire", "expired", "expired")])
def test_declined_or_expired_codes_say_so(xai, outcome, status, words):
    xai.fx.outcome = outcome
    out = run(sign_in())
    assert out["end"]["status"] == status and words in out["end"]["error"]
    assert not grok_plan.usable() and grok_plan.record() is None


def test_xai_refusing_the_account_says_what_happened_and_points_to_an_api_key(xai):
    xai.fx.outcome = "tier"  # 用户点了同意，xAI 不给这个账号令牌（403）
    out = run(sign_in())
    assert out["end"]["status"] == "denied" and "403" in out["end"]["error"] and "XAI_API_KEY" in out["end"]["error"]
    assert "isn't an integration xAI has named or supports" in out["end"]["error"]  # 10/21：不说成官方支持
    st = grok_plan.status()
    assert not st["ready"] and st["denied"]["code"] == "device_token_403" and "xai/<model>" in st["denied"]["message"]
    xai.fx.deny_device = True  # 连设备码都不给
    with pytest.raises(grok_plan.PlanError, match="403"):
        run(grok_plan.start_login())
    xai.fx.deny_device, xai.fx.outcome = False, "approve"
    run(sign_in())  # 后来换了档位、登录成功：被拒的记号清掉
    assert grok_plan.status()["denied"] is None and grok_plan.usable()


def test_untrusted_discovery_endpoints_are_refused(xai, monkeypatch):
    async def doc(fresh: bool = False):
        raise grok_plan.PlanError("xAI's discovery document points outside x.ai (or isn't https); not used")
    assert grok_plan.trusted("https://auth.x.ai/oauth2/token") and grok_plan.trusted("https://x.ai/a")
    assert not grok_plan.trusted("http://auth.x.ai/oauth2/token") and not grok_plan.trusted("https://evil.example/x.ai")
    assert not grok_plan.trusted("https://x.ai.evil.example/token") and not grok_plan.trusted("https://user:fake@auth.x.ai/t")
    monkeypatch.setattr(grok_plan, "ISSUER", "https://auth.x.ai")  # 换回真的源：假服务器（127.0.0.1）就不算可信了
    assert not grok_plan.trusted(xai.fx.url + "/oauth2/token")


def test_cancel_and_restart(xai):
    xai.fx.pending_polls = 1000

    async def go():
        a = await grok_plan.start_login()
        await asyncio.sleep(0.05)
        grok_plan.cancel_login()
        st = grok_plan.login_status()
        b = await grok_plan.start_login()
        await asyncio.sleep(0.05)
        live = grok_plan.login_status()
        grok_plan.cancel_login()
        return a, st, b, live
    a, st, b, live = run(go())
    assert st == {"status": "none"} and live["status"] == "pending" and live["code"] == b["code"] != a["code"]


# —— 令牌：刷新、排队、被拒 ——————————————————————————————————————————————————————————————

def test_tokens_refresh_before_they_expire_and_only_once_at_a_time(xai):
    run(sign_in())
    old = cred()
    set_cred(expires_at=time.time() + 60)  # 6 小时的令牌只剩 1 分钟：要刷新

    async def many():
        return await asyncio.gather(*(grok_plan.access_token() for _ in range(5)))
    toks = run(many())
    assert len(set(toks)) == 1 and toks[0] != old["access_token"]
    assert len([r for r in xai.fx.of("/oauth2/token") if r["form"]["grant_type"] == "refresh_token"]) == 1  # 排队：只刷了一次
    new = cred()
    assert new["refresh_token"] != old["refresh_token"] and xai.fx.valid(new["access_token"]) and not xai.fx.valid(old["access_token"])
    assert run(grok_plan.access_token()) == new["access_token"]  # 新的够新：不再刷


def test_short_lived_tokens_refresh_two_minutes_early():
    assert grok_plan.skew({"issued_at": 0, "expires_at": 21600}) == 3600  # 6 小时：提前 1 小时
    assert grok_plan.skew({"issued_at": 0, "expires_at": 900}) == 150     # 15 分钟：提前 2.5 分钟
    assert grok_plan.skew({"issued_at": 0, "expires_at": 300}) == 120     # 再短也至少提前 2 分钟
    assert grok_plan.skew({}) == 3600


def test_a_dead_refresh_token_clears_the_tokens_and_asks_to_sign_in_again(xai):
    run(sign_in())
    xai.fx.revoke_all()  # 用户在 xAI 那边撤销了，不通知谁
    set_cred(expires_at=time.time() + 10)
    with pytest.raises(EngineError, match="signing in again") as e:
        run(grok_plan.access_token())
    assert e.value.kind == "config"
    st = grok_plan.status()
    assert st["needsReconnect"] and not st["connected"] and not st["ready"] and cred().get("access_token") is None and cred()["email"]


def test_refresh_403_means_the_account_is_refused_not_a_new_sign_in(xai):
    run(sign_in())
    xai.fx.refresh_fail = {"status": 403, "error": "permission_denied"}
    set_cred(expires_at=time.time() + 10)
    with pytest.raises(EngineError, match="403") as e:
        run(grok_plan.access_token())
    assert e.value.status == 403 and "XAI_API_KEY" in str(e.value)
    st = grok_plan.status()
    assert st["denied"] and not st["needsReconnect"] and not st["ready"]


def test_cloudflare_challenge_is_retried_and_outages_keep_the_old_token(xai):
    run(sign_in())
    xai.fx.refresh_fail = {"status": 403, "html": True}  # 网页挑战：不是 OAuth 的错，再试一次就好
    set_cred(expires_at=time.time() + 120)
    tok = run(grok_plan.access_token())
    assert tok == cred()["access_token"] and grok_plan.status()["denied"] is None
    assert len([r for r in xai.fx.of("/oauth2/token") if r["form"]["grant_type"] == "refresh_token"]) == 2
    before = cred()
    xai.fx.refresh_fail = {"status": 503, "error": "server_error"}
    set_cred(expires_at=time.time() + 120)  # 刷新一时不成、旧的还能用两分钟：先用旧的
    assert run(grok_plan.access_token()) == before["access_token"] and grok_plan.usable()
    assert grok_plan.status()["lastError"]["code"] == "server_error"


def test_a_401_forces_one_refresh_and_a_racing_caller_reuses_it(xai):
    run(sign_in())
    old = cred()["access_token"]
    new = run(grok_plan.after_401(old))
    assert new != old and cred()["access_token"] == new
    assert run(grok_plan.after_401(old)) == new  # 别人已经换过了：直接用新的，不拿用过的刷新令牌再刷
    assert len([r for r in xai.fx.of("/oauth2/token") if r["form"]["grant_type"] == "refresh_token"]) == 1


# —— 退出 ————————————————————————————————————————————————————————————————————————

def test_logout_revokes_and_removes_the_local_copy(xai):
    run(sign_in())
    rt = cred()["refresh_token"]
    out = run(grok_plan.logout())
    assert out == {"ok": True, "revoked": True, "note": None}
    assert not grok_plan.record_path().exists() and not grok_plan.status()["connected"]
    assert xai.fx.of("/oauth2/revoke")[0]["form"] == {"token": "<secret>", "token_type_hint": "refresh_token", "client_id": grok_plan.CLIENT_ID}
    assert not xai.fx.refresh[rt]["live"]
    run(sign_in())
    xai.fx.revoke_status = 500  # 撤销没确认：本机照样删，告诉用户去 accounts.x.ai
    out = run(grok_plan.logout())
    assert out["revoked"] is False and "accounts.x.ai" in out["note"] and not grok_plan.record_path().exists()


def test_without_signing_in_nothing_changes(xai):
    st = grok_plan.status()
    assert not st["connected"] and not st["ready"] and st["login"] == {"status": "none"}
    assert not xai.dir.exists()  # 看状态不在 data_dir 里留东西
    with pytest.raises(EngineError, match="isn't signed in"):
        run(grok_plan.access_token())
    xai.conf(grok=False)
    assert not grok_plan.enabled() and not grok_plan.status()["enabled"]
    with pytest.raises(grok_plan.PlanError, match="turned off"):
        run(grok_plan.start_login())


# —— 接口、命令行 ——————————————————————————————————————————————————————————————————————

def test_endpoints_only_answer_the_owners_devices(xai):
    import main
    from starlette.testclient import TestClient
    c = TestClient(main.app)
    assert c.get("/api/engine/grok", headers={**H, "X-Mousse-Via": "mcp"}).status_code == 403  # 脚本、/mcp 替 Agent 发的：不认
    assert c.post("/api/engine/grok/login", headers={**H, "X-Mousse-Via": "auto"}).status_code == 403
    assert c.get("/api/engine/grok", headers={"Authorization": "Bearer tok-mcp"}).status_code in (401, 403)
    r = c.post("/api/engine/grok/login", headers=H)
    assert r.status_code == 200 and r.json()["code"] and r.json()["url"].startswith(xai.fx.url) and "device_code" not in r.text
    assert "isn't an integration xAI has named or supports" in r.json()["note"] and "403" in r.json()["note"]
    st = c.get("/api/engine/grok", headers=H).json()
    assert st["ok"] and not st["connected"] and st["login"]["status"] in ("pending", "cancelled", "error")
    assert c.delete("/api/engine/grok/login", headers=H).json() == {"ok": True}
    assert c.post("/api/engine/grok/logout", headers=H).json()["ok"] is True
    rows = c.get("/api/setup/models?probe=0", headers=H).json()["providers"]
    g = next(r for r in rows if r["id"] == "grok")
    assert g["kind"] == "login" and g["ready"] is False and g["login"]["start"] == "POST /api/engine/grok/login" and "grok login" in g["how"]
    assert "isn't an integration xAI has named or supports" in g["how"] and "XAI_API_KEY" in g["how"]


def test_setup_ctl_grok_login_waits_for_the_approval(xai, capsys):
    import setup_ctl
    assert setup_ctl.main(["grok"]) == 0 and "not signed in" in capsys.readouterr().out
    assert setup_ctl.main(["grok", "login"]) == 0
    out = capsys.readouterr().out
    assert xai.fx.url + "/device?user_code=" in out and "Code:" in out and "Signed in: alex@example.com" in out
    assert "isn't an integration xAI has named or supports" in out and "may refuse it (403)" in out
    assert grok_plan.usable() and setup_ctl.main(["grok"]) == 0 and "signed in · alex@example.com" in capsys.readouterr().out
    assert setup_ctl.main(["grok", "logout"]) == 0 and "Signed out" in capsys.readouterr().out and not grok_plan.usable()


# —— 算作订阅：谁能花、谁不能花 ——————————————————————————————————————————————————————————

def test_who_may_spend_the_grok_subscription(xai):
    assert models.subscription(GROK) == "grok" and models.valid_ref(GROK) and models.known(GROK)
    assert models.subscription("grok/a/b") == "grok" and models.subscription("xai/grok-4.7") is None  # 写得怪也算套餐；API 密钥那家照旧按量
    for origin in ("user", "relay"):
        assert models.may_spend(GROK, kind="owner", origin=origin)
    for origin in ("auto", "task"):  # 后台的：没同意过不花
        assert not models.may_spend(GROK, kind="owner", origin=origin)
    assert models.may_spend(GROK, kind="cyber", origin="user")  # 用户自己的赛博是前台
    for kind, origin, via in (("owner", "api", None), ("owner", "acp", None), ("owner", "compact", None), ("owner", None, None),
                              ("cyber", "butler", None), ("outsider", "card", None), ("outsider", "a2a", None), ("outsider", "doorman", None),
                              ("owner", "relay", "mcp"), ("owner", "user", "mcp"), ("owner", "auto", "mcp"), ("cyber", "auto", None)):
        assert not models.may_spend(GROK, kind=kind, origin=origin, via=via), (kind, origin, via)
    assert not models.may_spend(GROK, kind="owner", origin="relay", via="auto")  # 后台的一轮里调了转交：还是后台
    grok_plan.set_background(True)
    assert grok_plan.status()["background"]["allowed"] and grok_plan.status()["background"]["since"]
    assert models.may_spend(GROK, kind="owner", origin="auto") and models.may_spend(GROK, kind="owner", origin="task")
    assert models.may_spend(GROK, kind="cyber", origin="auto")  # 同意过以后，用户自己的后台赛博也花
    assert not models.may_spend(GROK, kind="owner", origin="relay", via="mcp") and models.may_spend(GROK, kind="cyber", origin="user")
    assert not models.may_spend("chatgpt/gpt-test", kind="owner", origin="auto")  # 两家的后台同意分开记
    grok_plan.set_background(False)
    assert not models.may_spend(GROK, kind="owner", origin="auto")


def test_outside_route_and_fallback_chains_never_pick_grok(xai):
    run(sign_in())
    xai.conf(fallbacks=[GROK, "other/m"], use={"card": True, "doorman": GROK, "json": GROK})
    assert models.outside(GROK) == "other/m" and models.route("card") == "other/m" and models.route("doorman") == "other/m"
    assert models.route("json") == "other/m"  # 不报来路 = 外人回合
    assert models.route("json", kind="owner", origin="user") == GROK  # 主人自己的一次性调用能花
    assert models.route("json", kind="owner", origin="auto") == "other/m"  # 后台的、没同意过
    assert models.fallbacks() == ["other/m"] and models.chain_of([GROK, "other/m"]) == ["other/m"]
    assert fallback.chain(engine.Spec(kind="outsider", model="other/m", fallbacks=[GROK])) == ["other/m"]  # 外人回合的链里混进来也不用
    from engine import openai_api
    assert openai_api.model_for("main") == "other/m"  # 对外接口（/v1）：默认模型是订阅也换成按量的


def test_a_grok_turn_calls_api_x_ai_with_the_access_token(xai):
    run(sign_in())
    spec = engine.Spec(kind="owner", model=GROK, system="You help.", origin="user", max_tokens=500, display="summarized")
    res = run(engine.run_turn(spec, MemorySession(), user("hello")))
    assert res.status == "done" and res.text == "Hello from the fake Grok." and res.usage["cost"] is None
    req = xai.fx.of("/v1/responses")[0]
    body = req["json"]
    assert req["bearer"] == "plan" and body["model"] == "grok-4.7" and body["store"] is False and body["instructions"] == "You help."
    assert "max_output_tokens" not in body and req["headers"]["user-agent"].startswith("OpenMousse/") and "x-grok-model-override" not in req["headers"]
    assert not models.has_key("xai/grok-4.7")  # 订阅不等于有 API 密钥：xai/… 还是要 XAI_API_KEY


def test_the_grok_cli_proxy_route_sends_its_headers_only_there(xai, monkeypatch):
    run(sign_in())
    monkeypatch.setattr(grok_plan, "PROXY", xai.fx.url + "/proxy/v1")
    xai.conf(grok={"route": "proxy"})
    assert grok_plan.chat_url() == xai.fx.url + "/proxy/v1" and grok_plan.status()["route"] == "proxy"
    run(engine.run_turn(engine.Spec(kind="owner", model=GROK, origin="user"), MemorySession(), user("hi")))
    req = xai.fx.of("/proxy/v1/responses")[0]
    h = req["headers"]
    assert h["x-xai-token-auth"] == "xai-grok-cli" and h["x-grok-model-override"] == "grok-4.7" and h["x-grok-client-version"]
    xai.conf(grok={"route": "anything-else"})  # 只认这两个地址：别的写法照默认的 api.x.ai
    assert grok_plan.chat_url() == xai.fx.api


def test_a_rejected_access_token_is_refreshed_once_then_sign_in_again(xai):
    run(sign_in())
    xai.fx.access.clear()  # 访问令牌不认了，刷新令牌还能用：刷新一次、再试就好
    res = run(engine.run_turn(engine.Spec(kind="owner", model=GROK, origin="user"), MemorySession(), user("x")))
    assert res.status == "done" and len(xai.fx.of("/v1/responses")) == 2
    assert len([r for r in xai.fx.of("/oauth2/token") if r["form"]["grant_type"] == "refresh_token"]) == 1
    xai.fx.revoke_all()  # 在 xAI 那边撤销了：下一次 401 才知道
    res = run(engine.run_turn(engine.Spec(kind="owner", model=GROK, origin="user"), MemorySession(), user("y")))
    assert res.status == "error" and res.error_kind == "config" and "signing in again" in res.error
    assert grok_plan.status()["needsReconnect"] and not models.subscription_ready(GROK)


def test_a_403_on_a_request_says_the_account_is_refused_and_points_to_an_api_key(xai):
    run(sign_in())
    xai.fx.api_403 = True
    res = run(engine.run_turn(engine.Spec(kind="owner", model=GROK, origin="user", fallbacks=[]), MemorySession(), user("x")))
    assert res.status == "error" and "403" in res.error and "XAI_API_KEY" in res.error and "xai/<model>" in res.error
    tok = grok_plan.record()["access_token"]
    assert tok not in res.error and tok not in json.dumps(grok_plan.status())
    assert grok_plan.status()["lastError"]["code"] and grok_plan.usable()  # 一次 403 不停用：可能只是这个型号


def test_owner_turns_in_the_app_use_grok_but_api_mcp_and_unconsented_background_dont(xai, quiet_bridges):
    run(sign_in())
    from engine import owner
    res = run(owner.respond("main", DAY, "hi", GROK, lambda _t: None))
    assert res.status == "done" and res.text == "Hello from the fake Grok." and res.model == GROK
    assert run(owner.respond("main", DAY, "again", GROK, lambda _t: None, via="mcp")).model == "other/m"
    assert run(owner.respond("api:main:x", DAY, "from the api", GROK, lambda _t: None, origin="api", agent="main", history=[])).model == "other/m"
    assert run(owner.respond("main", DAY, "inbox said go on", GROK, lambda _t: None, origin="auto")).model == "other/m"
    grok_plan.set_background(True)
    assert run(owner.respond("main", DAY, "inbox said go on", GROK, lambda _t: None, origin="auto")).model == GROK
    assert len(xai.fx.of("/v1/responses")) == 2
    xai.conf(models=[GROK])  # 不能花、又没有按量的可换：不跑，说清楚
    with pytest.raises(EngineError, match="can't use your Grok subscription"):
        run(owner.respond("main", DAY, "x", GROK, lambda _t: None, via="mcp"))


def test_background_runs_need_consent_then_use_grok(xai, quiet_bridges):
    run(sign_in())
    from engine import tasks, unattended
    out = run(unattended.fresh("main", "main", "cron:j1", "check the ferry", model=GROK, light=True))
    assert out.model == "other/m" and not xai.fx.of("/v1/responses")
    with pytest.raises(engine.ToolFailure, match="allow"):
        tasks._model_arg({"model": GROK}, tasks.ENGINE, "main")
    assert tasks._model_arg({}, tasks.ENGINE, "main") == "other/m"  # 默认的是订阅、没同意：换非套餐的
    xai.conf(models=[GROK])  # 没有别的模型可换：不跑，说清楚
    out = run(unattended.fresh("main", "main", "cron:j1", "check", model=GROK, light=True))
    assert out.status == "error" and "Grok subscription" in out.error and "allow it once (python3 setup_ctl.py grok background on)" in out.error
    xai.conf()
    grok_plan.set_background(True)
    out = run(unattended.fresh("main", "main", "cron:j1", "check the ferry", model=GROK, light=True))
    assert out.model == GROK and out.text == "Hello from the fake Grok." and len(xai.fx.of("/v1/responses")) == 1
    assert tasks._model_arg({"model": GROK}, tasks.ENGINE, "main") == GROK and tasks.pick_runner("", GROK, "main")[:2] == (tasks.ENGINE, GROK)
    # 经 /mcp、对外接口引出来的那一轮派的任务：同意过后台用也不花
    assert tasks._model_arg({}, tasks.ENGINE, "main", "mcp") == "other/m" and tasks.pick_runner("", "", "main", "api")[:2] == (tasks.ENGINE, "other/m")
    with pytest.raises(engine.ToolFailure):
        tasks.pick_runner("", GROK, "main", "mcp")


def test_one_shots_and_compaction_on_grok_run_as_the_owners_turn(xai):
    run(sign_in())
    from engine import compact, oneshot
    from engine.session import View
    xai.fx.chat_text = '{"ok": true}'
    v, via = run(oneshot.ask_json("JSON please.", {"q": 1}, model=GROK, kind="owner", origin="user"))
    assert v == {"ok": True} and via == f"builtin:{GROK}"
    with pytest.raises(EngineError, match="own plan"):  # 不报来路 = 外人：不花
        run(oneshot.ask_json("JSON please.", {"q": 1}, model=GROK))
    xai.fx.chat_text = "<summary>We talked about the ferry.</summary>"
    old = [user("old message"), {"role": "assistant", "content": [{"type": "text", "text": "old reply"}], "api": None, "provider": None,
                                 "model": None, "stop": "end", "usage": {}}]
    view = View(messages=old, rows=[({}, m) for m in old], summary=None, system="sys")
    summary, _u, _t = run(compact.summarize(view, 2, engine.Spec(kind="owner", model=GROK, origin="user", conversation="main")))
    assert summary == "We talked about the ferry."
    with pytest.raises(EngineError):  # 后台的一轮、没同意：压缩也不花
        run(compact.summarize(view, 2, engine.Spec(kind="owner", model=GROK, origin="auto", conversation="main")))


def test_a_users_cyber_spends_grok_and_other_peoples_turns_do_not(xai):
    run(sign_in())
    from engine import cyber
    res = run(cyber.chat({"id": "cy1", "name": "Cy", "identity": "a friend"}, "hello"))
    assert res.model == GROK and res.text == "Hello from the fake Grok."  # 用户自己的赛博
    res = run(cyber.chat({"id": "cy2", "name": "Cy", "identity": "a friend"}, "hello", origin="butler"))
    assert res.model == "other/m" and res.text == "from other"  # 管家替外人答
    res = run(cyber.chat({"id": "cy3", "name": "Cy", "identity": "a friend"}, "later", origin="auto"))
    assert res.model == "other/m" and res.text == "from other"  # 后台的 Grok 还没同意
    for spec in (engine.Spec(kind="cyber", model=GROK), engine.Spec(kind="owner", model=GROK, origin="user", via="mcp"),
                 engine.Spec(kind="outsider", model=GROK, origin="card")):
        with pytest.raises(EngineError):
            run(engine.run_turn(spec, MemorySession(), user("x")))
    grok_plan.set_background(True)
    res = run(cyber.chat({"id": "cy4", "name": "Cy", "identity": "a friend"}, "check", origin="auto"))
    assert res.model == GROK and res.text == "Hello from the fake Grok."
    assert len(xai.fx.of("/v1/responses")) == 2


def test_background_consent_only_from_the_owners_devices(xai, capsys):
    import main
    import setup_ctl
    from starlette.testclient import TestClient
    from ops import audit
    c = TestClient(main.app)
    r = c.put("/api/engine/grok/background", headers={**H, "X-Mousse-Via": "auto"}, json={"allow": True})
    assert r.status_code == 403 and not grok_plan.background_allowed()  # Agent 的脚本给自己开不了
    assert c.put("/api/engine/grok/background", headers=H, json={"allow": "yes"}).status_code == 400
    r = c.put("/api/engine/grok/background", headers=H, json={"allow": True})
    assert r.status_code == 200 and r.json()["background"]["allowed"] and grok_plan.background_allowed()
    assert "plan.grok_background" in [f.id for f in audit.check_plan({})]
    assert setup_ctl.main(["grok", "background", "off"]) == 0 and not grok_plan.background_allowed() and "won't use" in capsys.readouterr().out
    assert "plan.grok_background" not in [f.id for f in audit.check_plan({})]


# —— 型号目录 ——————————————————————————————————————————————————————————————————————

@pytest.fixture
def no_cli(monkeypatch):
    """这台机器上真的 Claude Code / Codex 当没查到（同 test_setup 的 env）。"""
    from engine import runners
    monkeypatch.setattr(runners, "_warm", True)
    monkeypatch.setattr(runners, "_found", {})
    monkeypatch.setattr(runners, "_checked_at", time.monotonic())
    monkeypatch.setattr(runners, "_codex_bin", None)


def test_models_come_from_the_account_catalog_with_the_chat_default_first(xai, no_cli):
    assert grok_plan.model_ids() == [] and models.subscription_models() == [] and GROK not in models.models()  # 没登录：一个都不列
    xai.fx.models = [{"id": "grok-4.6"}, {"id": "grok-imagine-image"}, {"id": "grok-4.7"}, {"id": "grok-4.20-multi-agent-0309"},
                     {"id": "grok-build-0.1"}, {"id": "grok-imagine-video"}, {"id": "grok-tts"}]
    run(sign_in())
    assert grok_plan.model_ids() == ["grok/grok-4.7", "grok/grok-4.6", "grok/grok-build-0.1"]  # 默认的排最前；生图、生视频、朗读、多 agent 的不列
    st = grok_plan.status()
    assert st["defaultModel"] == GROK and st["catalog"] == "account" and st["models"][0] == GROK
    assert models.models() == [GROK, "other/m", "grok/grok-4.6", "grok/grok-build-0.1"]
    assert all(models.subscription_ready(m) and models.has_key(m) for m in st["models"])
    assert xai.fx.of("/v1/models")[0]["bearer"] == "plan"
    xai.conf(grok={"models": ["grok-4.6", "grok/grok-4.20-multi-agent-0309"]})  # 自己写了就照它（多 agent 的要试就写进来）
    assert grok_plan.model_ids() == ["grok/grok-4.6", "grok/grok-4.20-multi-agent-0309"] and grok_plan.catalog_source() == "config"


def test_without_a_grok_4_7_the_first_chat_model_is_the_default(xai):
    xai.fx.models = [{"id": "grok-imagine-image"}, {"id": "grok-4.6"}, {"id": "grok-4.5"}]
    run(sign_in())
    assert grok_plan.default_model() == "grok/grok-4.6" and grok_plan.model_ids() == ["grok/grok-4.6", "grok/grok-4.5"]


def test_when_the_catalog_cant_be_read_the_default_is_still_offered(xai):
    xai.fx.models = [{"id": "grok-imagine-image"}]  # 目录里一个对话的都没有（或者 /models 不回）：先列默认的那个，写明不是从账号拿的
    run(sign_in())
    assert grok_plan.model_ids() == [GROK] and grok_plan.catalog_source() == "default"
    assert grok_plan.chat_rows([{"model": "grok-composer-2.5-fast", "name": "Composer", "api_backend": "responses"},
                                {"model": "grok-imagine-image-2.0", "api_backend": "image"}, {"id": "x y z"}, "junk"]) == \
        [{"id": "grok-composer-2.5-fast", "name": "Composer"}]  # 订阅代理那种目录也认


def test_purpose_pickers_and_the_models_page_offer_grok(xai, no_cli):
    import main
    from starlette.testclient import TestClient
    run(sign_in())
    c = TestClient(main.app)
    view = c.get("/api/setup/purposes?probe=0", headers=H).json()
    refs = {r["ref"]: r for r in view["choices"]["models"]}
    assert refs[GROK]["kind"] == "login" and refs[GROK]["plan"] == "grok" and refs[GROK]["providerName"] == "Grok subscription"
    chat = next(p for p in view["purposes"] if p["id"] == "chat")
    assert chat["current"] == GROK and chat["usable"] and chat["recommended"]["value"] == GROK  # 默认挑对话的那个
    assert c.put("/api/setup/purposes", headers=H, json={"background": "grok/grok-4.6"}).status_code == 200
    assert models.background_model() == "grok/grok-4.6"
    r = c.put("/api/setup/purposes", headers=H, json={"outsider": GROK})
    assert r.status_code == 400 and "plan" in r.json()["error"]  # 外人回合不能选订阅
    page = c.get("/api/models", headers=H).json()
    row = next(p for p in page["providers"] if p["provider"] == "grok")
    assert row["subscription"] and row["status"] == "ok" and row["name"] == "Grok subscription"
    assert page["grok"]["ready"] and page["grok"]["defaultModel"] == GROK and GROK in page["grok"]["models"]
    g = next(r for r in c.get("/api/setup/models?probe=0", headers=H).json()["providers"] if r["id"] == "grok")
    assert g["ready"] and g["models"][0] == GROK and g["defaultModel"] == GROK


def test_health_probe_names_the_grok_subscription(xai):
    import claw
    assert run(claw.probe()) == (False, "your Grok subscription isn't signed in (python3 setup_ctl.py grok login)")
    run(sign_in())
    assert run(claw.probe()) == (True, f"built-in engine · {GROK} (your Grok subscription)")


# —— 搜索：grok 先走订阅，x_search 默认只用密钥（10/21） ————————————————————————————————————————————

KEY = "xai-fake-api-key-0123456789abcdef"


def ctx(origin: str | None = "user", kind: str = "owner", via: str | None = None) -> engine.ToolCtx:
    return engine.ToolCtx(turn="t", kind=kind, agent="main", conversation="main", call_id="c1", control=engine.Control(),
                          origin=models.stricter(origin, via))


def hand(name: str) -> engine.Tool | None:
    from engine import tools
    return next((t for t in tools.for_agent("main") if t.name == name), None)


def search_rows() -> list[dict]:
    """engine_media_usage 里搜索的那几行（库是这一轮测试共用的：比较时只看自己新加的）。"""
    from engine import store
    with store.read() as conn:
        return [dict(r) for r in conn.execute("SELECT provider, model, cost, unit FROM engine_media_usage WHERE purpose='search' ORDER BY id")]


def store_ready() -> bool:
    from engine import store
    if not store.exists():
        return False
    with store.read() as conn:
        return conn.execute("SELECT 1 FROM sqlite_master WHERE name='engine_media_usage'").fetchone() is not None


@pytest.fixture
def searching(xai, monkeypatch):
    """搜索写明了 grok（API 密钥那一路的地址也指到假服务），XAI_API_KEY 先不在。web(**engine 段) 改设置时带上这一段；key() 放进假服务认的那把密钥。"""
    from engine import web
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    web._cache.clear()
    grok_web = {"provider": "grok", "providers": {"grok": {"url": xai.fx.api + "/responses"}}}

    def conf(search: dict | None = None, x: object = None, **engine) -> None:
        w = {"search": search or grok_web, **({"x_search": x} if x is not None else {})}
        xai.conf(web=w, **engine)

    def key() -> None:
        xai.fx.api_key = KEY
        monkeypatch.setenv("XAI_API_KEY", KEY)
    conf()
    return SimpleNamespace(fx=xai.fx, conf=conf, key=key, grok_web=grok_web)


def bearers(fx) -> list[str]:
    return [r["bearer"] for r in fx.of("/v1/responses")]


def test_grok_web_search_uses_the_subscription_first(searching):
    from engine import web
    fx = searching.fx
    assert web.provider() is None and hand("web_search") is None  # 写明了 grok，可没有密钥、没登录：没有这个工具
    run(sign_in())
    assert web.provider() == "grok" and hand("web_search") is not None
    out = run(hand("web_search").run({"query": "when does the shop open"}, ctx()))
    assert "Answer: Grok says the shop opens at 9." in out and "https://shop.example/hours" in out and "<untrusted_tool_result" in out
    req = fx.of("/v1/responses")[-1]
    assert req["bearer"] == "plan" and req["json"]["tools"] == [{"type": "web_search"}] and req["json"]["model"] == "grok-4.7"
    assert req["json"]["store"] is False and req["headers"]["user-agent"].startswith("OpenMousse/")
    assert search_rows()[-1] == {"provider": "grok", "model": "grok-4.7", "cost": 0.0, "unit": "query"}  # 算订阅的额度：记一行，钱记 0
    searching.conf(search={"more": True})
    assert web.provider() is None  # 只登录了订阅不会自己换成 grok 搜：要写明 provider
    searching.conf(grok={"search": False})
    assert web.provider() is None and not web.on_plan()  # engine.grok.search = false：搜索不走订阅，又没密钥：没有


def test_grok_search_falls_back_to_the_api_key_only_when_the_subscription_cant_be_used(searching):
    fx = searching.fx
    run(sign_in())
    t = hand("web_search")
    with pytest.raises(engine.ToolFailure, match="background runs need your OK"):  # 后台的、没同意过，又没有密钥：不搜
        run(t.run({"query": "x"}, ctx("auto")))
    with pytest.raises(engine.ToolFailure, match="through /mcp never"):
        run(t.run({"query": "x"}, ctx("user", via="mcp")))
    assert not fx.of("/v1/responses")
    searching.key()
    before = len(search_rows()) if store_ready() else 0
    for c in (ctx("auto"), ctx("user", via="mcp"), ctx(None)):  # 有密钥：这几轮退到 XAI_API_KEY（照旧按量）
        assert "shop opens at 9" in run(t.run({"query": "x"}, c))
    assert bearers(fx) == ["api_key"] * 3 and [r["cost"] for r in search_rows()[before:]] == [None] * 3
    grok_plan.set_background(True)
    run(t.run({"query": "x"}, ctx("auto")))
    assert bearers(fx)[-1] == "plan"  # 同意过后台用：后台的也走订阅
    fx.plan_403 = True  # 订阅这一样被 xAI 拒了：退到密钥，状态里记下
    assert "shop opens at 9" in run(t.run({"query": "x"}, ctx()))
    assert bearers(fx)[-2:] == ["plan", "api_key"] and grok_plan.status()["lastError"]["code"] == "http_403"
    fx.plan_403, fx.api_fail = False, 429  # 订阅这一次限流了：照实报，不悄悄换成按量
    with pytest.raises(engine.ToolFailure, match="429") as e:
        run(t.run({"query": "x"}, ctx()))
    assert "allowance may be used up" in str(e.value) and bearers(fx)[-1] == "plan" and len(bearers(fx)) == 7
    fx.api_fail = None
    searching.conf(grok={"search": False})  # 设成不走订阅：登录着也用密钥
    run(t.run({"query": "x"}, ctx()))
    assert bearers(fx)[-1] == "api_key"


def test_x_search_is_off_until_switched_on_and_skips_the_subscription_by_default(searching):
    from engine import xsearch
    fx = searching.fx
    run(sign_in())
    assert hand("x_search") is None  # 默认没有：新工具，老实例不变
    searching.conf(x=True)
    assert not xsearch.on_plan() and hand("x_search") is None  # 10/21 定：搜 X 不默认走订阅（engine.grok.x_search 默认关）；没密钥就没有
    searching.key()
    t = hand("x_search")
    assert t is not None and "XAI_API_KEY" in t.description and "subscription" not in t.description
    out = run(t.run({"query": "ferry"}, ctx()))  # 登录着、主人自己的回合：照样用密钥
    assert "XAI_API_KEY, billed per use" in out and bearers(fx) == ["api_key"] and search_rows()[-1]["cost"] is None


def test_x_search_switched_onto_the_subscription_prefers_it(searching):
    fx = searching.fx
    run(sign_in())
    searching.conf(x=True, grok={"x_search": True})  # 用户单独开了：先走订阅
    t = hand("x_search")
    assert t is not None and t.kinds == ("owner",) and t.level({"query": "x"})[0] == "read" and "Grok subscription" in t.description
    out = run(t.run({"query": "ferry timetable", "allowed_x_handles": ["@example_user", "example_user"], "from_date": "2026-10-01",
                     "enable_image_understanding": True}, ctx()))
    req = fx.of("/v1/responses")[-1]
    assert req["bearer"] == "plan" and req["json"]["model"] == "grok-4.7" and req["json"]["store"] is False
    assert req["json"]["tools"] == [{"type": "x_search", "allowed_x_handles": ["example_user"], "from_date": "2026-10-01",
                                     "enable_image_understanding": True}]
    assert "your Grok subscription" in out and "fictional ferry timetable" in out and "<untrusted_tool_result" in out
    assert "https://x.com/example_user/status/1" in out and "https://x.com/other_user/status/2" in out  # 顶层的 citations 也算出处
    assert "No X posts came back" not in out and search_rows()[-1]["cost"] == 0.0
    for bad, words in (({"allowed_x_handles": ["a"], "excluded_x_handles": ["b"]}, "together"), ({"from_date": "10/01/2026"}, "YYYY-MM-DD"),
                       ({"from_date": "2026-10-05", "to_date": "2026-10-01"}, "after"), ({"from_date": "2099-01-01"}, "future"),
                       ({"allowed_x_handles": [f"user{i}" for i in range(11)]}, "at most 10"),
                       ({"excluded_x_handles": ["not a handle"]}, "isn't an X handle")):
        with pytest.raises(engine.ToolFailure, match=words):
            run(t.run({"query": "x", **bad}, ctx()))
    assert len(fx.of("/v1/responses")) == 1  # 参数不对的先在这边挡下，一次都没搜
    fx.x_cites = False  # 订阅的令牌有时回一段没有出处的：写明可能不是搜出来的
    out = run(t.run({"query": "ferry", "from_date": "2026-10-01"}, ctx()))
    assert "No X posts came back as sources" in out and "unverified" in out and "filters: from_date" in out
    with pytest.raises(engine.ToolFailure, match="through /mcp never"):  # 经 /mcp 来的：不花订阅，又没有密钥
        run(t.run({"query": "x"}, ctx("user", via="mcp")))
    searching.key()
    out = run(t.run({"query": "x"}, ctx("user", via="mcp")))
    assert "XAI_API_KEY, billed per use" in out and bearers(fx)[-1] == "api_key" and search_rows()[-1]["cost"] is None
    searching.conf(x={"model": "grok-4.6"}, grok={"x_search": True})
    run(hand("x_search").run({"query": "x"}, ctx()))
    assert fx.of("/v1/responses")[-1]["json"]["model"] == "grok-4.6"
    searching.conf(x={"enabled": False}, grok={"x_search": True})
    assert hand("x_search") is None


def test_x_search_without_the_subscription_uses_the_api_key_and_agents_can_be_left_out(searching, monkeypatch):
    fx = searching.fx
    searching.conf(x=True)
    assert hand("x_search") is None  # 没登录、没密钥：没有
    searching.key()
    out = run(hand("x_search").run({"query": "ferry"}, ctx()))
    assert "XAI_API_KEY, billed per use" in out and bearers(fx) == ["api_key"] and fx.of("/v1/responses")[0]["json"]["model"] == "grok-4.7"
    xai_web = {"search": searching.grok_web, "x_search": True, "agents": {"main": {"x_search": False}}}
    from engine import web
    monkeypatch.setattr(web, "cfg", lambda: xai_web)
    assert hand("x_search") is None and hand("web_search") is not None  # 这个 Agent 不给 x_search，web_search 照旧


def test_the_search_settings_show_grok_connected_through_the_subscription(searching):
    import main
    from starlette.testclient import TestClient
    c = TestClient(main.app)
    g = next(r for r in c.get("/api/setup/search", headers=H).json()["providers"] if r["id"] == "grok")
    assert not g["connected"] and not g["viaPlan"]
    run(sign_in())
    v = c.get("/api/setup/search", headers=H).json()
    g = next(r for r in v["providers"] if r["id"] == "grok")
    assert g["connected"] and g["viaPlan"] and v["provider"] == "grok"
    r = c.post("/api/setup/search/grok/test", headers=H, json={}).json()  # 设置页的「测一下」：主人自己点的，测订阅
    assert r["ok"] and r["results"] == 1 and bearers(searching.fx) == ["plan"]
    assert c.put("/api/setup/search", headers=H, json={"enabled": True, "provider": "grok"}).status_code == 200  # 只有订阅也能选它


# —— 生图、生视频、朗读：Grok 订阅这一家（默认关） ————————————————————————————————————————————

PUBLIC_IP = "93.184.216.34"


@pytest.fixture
def making(xai, tmp_path, monkeypatch):
    """媒体那几样要的：工作区、附件目录、视频轮询改成几毫秒、往对话补一句换成记下来；use(**engine.use 段) 改设置。"""
    import files
    from engine import grok_media, host_process
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setattr(settings, "workspace", ws)
    monkeypatch.setattr(files, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(grok_media, "POLL", 0.01)
    told: list = []

    async def deliver(thread, line, text):
        told.append((thread, line, text))
    monkeypatch.setattr(host_process, "deliver", deliver)

    def use(**purposes) -> None:
        xai.conf(use=purposes)
    return SimpleNamespace(fx=xai.fx, ws=ws, use=use, told=told)


def media_rows() -> dict:
    from engine import setup_api
    return {r["id"]: r for r in setup_api.media_rows(True)}


def last_media(purpose: str) -> dict:
    from engine import store
    with store.read() as conn:
        return dict(conn.execute("SELECT provider, model, units, unit, cost, ok FROM engine_media_usage WHERE purpose=? ORDER BY id DESC LIMIT 1",
                                 (purpose,)).fetchone())


def mctx(origin: str | None = "user", via: str | None = None, conversation: str = "t-grok-media") -> engine.ToolCtx:
    return engine.ToolCtx(turn="t", kind="owner", agent="main", conversation=conversation, call_id="c1", control=engine.Control(),
                          origin=models.stricter(origin, via))


def test_grok_media_is_offered_only_after_sign_in_and_stays_off_until_chosen(making):
    import main
    from starlette.testclient import TestClient
    c = TestClient(main.app)
    assert all("grok" not in media_rows()[k]["providers"] for k in ("image", "video_gen", "tts"))
    assert c.put("/api/setup/purposes", headers=H, json={"image": "grok/grok-imagine-image"}).status_code == 400  # 没登录：选不了
    run(sign_in())
    rows = media_rows()
    assert all(rows[k]["providers"][-1] == "grok" for k in ("image", "video_gen", "tts"))
    assert "grok" not in rows["vision"]["providers"] and "grok" not in rows["music"]["providers"]
    assert all(hand(n) is None for n in ("image_generate", "speak", "video_generate"))  # 登录了也默认关：选了才有
    r = c.put("/api/setup/purposes", headers=H, json={"image": "grok/grok-imagine-image", "tts": {"model": "grok/grok-tts", "voice": "ara"}})
    assert r.status_code == 200
    rows = media_rows()
    assert rows["image"]["current"] == "grok/grok-imagine-image" and rows["image"]["problem"] is None
    assert rows["tts"]["current"] == "grok/grok-tts" and rows["tts"]["options"] == {"voice": "ara"}
    assert hand("image_generate") is not None and hand("speak") is not None and hand("video_generate") is None
    assert c.put("/api/setup/purposes", headers=H, json={"vision": "grok/grok-4.7"}).status_code == 400  # 看图这几样不收订阅
    run(grok_plan.logout())
    assert "isn't signed in" in media_rows()["image"]["problem"]  # 退出了：设置里说清楚


def test_grok_images_come_from_the_subscription(making, monkeypatch):
    from engine import images, spend
    fx = making.fx
    run(sign_in())
    making.use(image={"model": "grok/grok-imagine-image", "aspect_ratio": "16:9", "resolution": "2k"})
    t = hand("image_generate")
    out = run(t.run({"prompt": "A fictional lighthouse at dusk", "count": 2}, mctx()))
    assert isinstance(out, engine.ToolOutput) and "Generated 2 images with grok/grok-imagine-image" in out.text and len(out.images) == 2
    req = fx.of("/v1/images/generations")[-1]
    assert req["bearer"] == "plan" and req["json"] == {"model": "grok-imagine-image", "prompt": "A fictional lighthouse at dusk", "n": 2,
                                                       "response_format": "b64_json", "aspect_ratio": "16:9", "resolution": "2k"}
    assert last_media("image") == {"provider": "grok", "model": "grok-imagine-image", "units": 2.0, "unit": "image", "cost": 0.0, "ok": 1}
    with monkeypatch.context() as mp:  # 今天的钱到了上限：按量的不生，订阅的照样（算订阅的额度、不花钱）
        mp.setattr(spend, "enabled", lambda: True)
        mp.setattr(spend, "over", lambda **_k: {"scope": "day", "name": None, "spent": 9.0, "limit": 5.0})
        spend.before_tool(mctx(), "grok")
        with pytest.raises(engine.ToolFailure, match="daily limit"):
            spend.before_tool(mctx(), "openai")
    (making.ws / "sketch.png").write_bytes(images_png())
    run(t.run({"prompt": "Make it night", "image": "sketch.png"}, mctx()))
    edit = fx.of("/v1/images/edits")[-1]["json"]
    assert edit["model"] == "grok-imagine-image-quality" and edit["image"]["type"] == "image_url"  # 改图换成收图的那个
    assert edit["image"]["url"].startswith("data:image/png;base64,") and "aspect_ratio" not in edit
    with pytest.raises(engine.ToolFailure, match="aspect_ratio"):
        run(t.run({"prompt": "x", "aspect_ratio": "21:9"}, mctx()))
    n = len(fx.of("/v1/images/generations"))
    for c in (mctx("user", via="mcp"), mctx("api"), mctx("auto")):  # 经 /mcp、/v1 来的、后台没同意过的：不生、不换别家
        with pytest.raises(engine.ToolFailure, match="can't use your Grok subscription"):
            run(t.run({"prompt": "x"}, c))
    assert len(fx.of("/v1/images/generations")) == n
    fx.plan_403 = True  # 订阅里没有这一样：说清楚，提示换一家
    with pytest.raises(engine.ToolFailure, match="403") as e:
        run(t.run({"prompt": "x"}, mctx()))
    assert "Settings → Purposes" in str(e.value) and grok_plan.usable()
    assert images.sniff(images_png())


def images_png() -> bytes:
    from evals.fakemedia import png
    return png(8, 8)


def test_grok_videos_come_from_the_subscription_and_download_without_the_token(making, monkeypatch):
    from engine import web
    fx = making.fx
    run(sign_in())
    making.use(video_gen="grok/grok-imagine-video")
    fx.video_host = "vidgen.example"  # xAI 给的临时地址：照公网的规矩下载（测试里解析到假服务）

    async def resolve(host: str, port: int) -> list[str]:
        return [PUBLIC_IP] if host == "vidgen.example" else [host]
    monkeypatch.setattr(web, "resolve_host", resolve)
    monkeypatch.setattr(web, "dial", lambda ip, port: ("127.0.0.1", int(fx.url.rsplit(":", 1)[1])))
    monkeypatch.setattr(web, "proxied", lambda: False)
    t = hand("video_generate")
    level, why = t.level({"prompt": "x"})
    assert level == "ask" and "Grok subscription" in why and "silent" in t.description  # 照旧每段先问；说明里写着没有声音
    out = run(t.run({"prompt": "A fictional ferry at dawn", "seconds": 6, "aspect_ratio": "9:16"}, mctx()))
    assert "[Video ready]" in out and "grok/grok-imagine-video" in out
    sub = fx.of("/v1/videos/generations")[0]
    assert sub["bearer"] == "plan" and sub["headers"]["x-idempotency-key"]
    assert sub["json"] == {"model": "grok-imagine-video", "prompt": "A fictional ferry at dawn", "duration": 6, "aspect_ratio": "9:16"}
    assert [r["bearer"] for r in fx.of("/files/clip.mp4")] == ["none"]  # 下载不带令牌
    assert last_media("video_gen") == {"provider": "grok", "model": "grok-imagine-video", "units": 1.0, "unit": "video", "cost": 0.0, "ok": 1}
    for bad, words in (({"seconds": 30}, "1–15"), ({"aspect_ratio": "21:9"}, "aspect_ratio"), ({"resolution": "1080p"}, "resolution")):
        with pytest.raises(engine.ToolFailure, match=words):
            run(t.run({"prompt": "y", **bad}, mctx()))
    with pytest.raises(engine.ToolFailure, match="can't use your Grok subscription"):
        run(t.run({"prompt": "z"}, mctx("user", via="mcp")))
    assert len(fx.of("/v1/videos/generations")) == 1  # 参数不对的、不能花的：一次都没交
    (making.ws / "start.png").write_bytes(images_png())
    fx.video_fail = True
    with pytest.raises(engine.ToolFailure, match="rejected by moderation"):
        run(t.run({"prompt": "From this picture", "image": "start.png"}, mctx()))
    sub = fx.of("/v1/videos/generations")[-1]["json"]
    assert sub["model"] == "grok-imagine-video-1.5" and sub["image"]["url"].startswith("data:image/png;base64,")  # 图生视频换 1.5


def test_an_approved_card_keeps_the_turns_origin(making, monkeypatch):
    """10/21 审查：点了卡同意，照交卡那一轮的来路算谁能花：/v1 的调用方对自己的卡点头、经 /mcp 来的、没同意过的后台出的，都不花订阅。"""
    import inbox
    from engine import handkit, store, tools
    fx = making.fx
    run(sign_in())
    making.use(video_gen="grok/grok-imagine-video", image="grok/grok-imagine-image")
    tools.install_hooks()
    told: list[str] = []
    monkeypatch.setattr(inbox, "kick", lambda thread, text: told.append(text) or "started")
    t = hand("video_generate")

    async def approve(c: engine.ToolCtx, prompt: str) -> dict:
        verdict = await tools.gate(t, {"prompt": prompt, "seconds": 6}, c)
        assert verdict["status"] == "approval-pending"
        return await tools.on_decided(inbox.item(verdict["inbox"]), "approve")
    for i, c in enumerate((mctx("api"), mctx("user", via="mcp"), mctx("auto"))):
        res = run(approve(c, f"A fictional ferry, take {i}"))
        assert "can't use your Grok subscription" in res["failed"], res
    assert fx.of("/v1/videos/generations") == [] and len(told) == 3  # 一段都没交给 xAI；对话里说了没做成
    with store.read() as conn:
        rows = [tuple(r) for r in conn.execute("SELECT kind, origin FROM engine_calls WHERE tool='video_generate' ORDER BY created_at")]
    assert rows[-3:] == [("owner", "api"), ("owner", "mcp"), ("owner", "auto")]
    img = {"args": json.dumps({"prompt": "A fictional lighthouse"}), "agent": "main", "tool": "image_generate", "turn": "t", "thread": "main",
           "call": "c1"}
    with pytest.raises(engine.ToolFailure, match="can't use your Grok subscription"):  # 以前交的卡没记来路：不花
        run(handkit.run_approved(img, {}))
    n = len(fx.of("/v1/images/generations"))
    run(handkit.run_approved({**img, "kind": "owner", "origin": "user"}, {}))  # 主人自己那一轮交的：照旧走订阅
    assert len(fx.of("/v1/images/generations")) == n + 1 and fx.of("/v1/images/generations")[-1]["bearer"] == "plan"


def test_grok_speech_and_who_may_use_it(making):
    from engine import media, tts
    fx = making.fx
    run(sign_in())
    making.use(tts={"model": "grok/grok-tts", "voice": "ara", "language": "en"})
    out = run(hand("speak").run({"text": "The fictional ferry leaves at nine."}, mctx()))
    assert "grok/grok-tts" in out and "mp3" in out
    req = fx.of("/v1/tts")[-1]
    assert req["bearer"] == "plan" and req["json"] == {"text": "The fictional ferry leaves at nine.", "voice_id": "ara", "language": "en",
                                                       "output_format": {"codec": "mp3"}}
    assert last_media("tts") == {"provider": "grok", "model": "grok-tts", "units": 35.0, "unit": "char", "cost": 0.0, "ok": 1}
    with pytest.raises(media.MediaError, match="background runs need your OK"):  # 「用语音回」算后台：没同意过不念
        run(tts.synthesize("hi", kind="owner", origin="auto"))
    with pytest.raises(media.MediaError):  # 不说是谁的（渠道）：不念
        run(tts.synthesize("hi"))
    grok_plan.set_background(True)
    assert run(tts.synthesize("hi", kind="owner", origin="auto", format="wav")).ext == "wav"
    assert fx.of("/v1/tts")[-1]["json"]["output_format"] == {"codec": "wav"}
    fx.plan_403 = True  # Hermes 遇到过：订阅的令牌调朗读回 403
    with pytest.raises(engine.ToolFailure, match="403") as e:
        run(hand("speak").run({"text": "x"}, mctx()))
    assert "Settings → Purposes" in str(e.value)


# —— 用量：订阅这一段的额度 ——————————————————————————————————————————————————————————————

def test_the_subscription_allowance_shows_on_the_usage_page_and_the_command_line(xai, no_cli, capsys):
    import main
    import setup_ctl
    from starlette.testclient import TestClient
    fx = xai.fx
    c = TestClient(main.app)
    assert grok_plan.quota() is None and c.get("/api/setup/usage", headers=H).json()["plans"] == {}  # 没登录：没有这一栏
    run(sign_in())
    q = run(grok_plan.refresh_quota())
    assert {k: q[k] for k in ("plan", "period", "usedPercent", "periodStart", "resetsAt", "error")} == {
        "plan": "SuperGrok Heavy", "period": "weekly", "usedPercent": 12.5, "periodStart": "2026-10-18T00:00:00+00:00",
        "resetsAt": "2026-10-25T00:00:00+00:00", "error": None}
    req = fx.of("/v1/billing")[-1]
    assert req["bearer"] == "plan" and req["query"] == {"format": "credits"} and req["headers"]["x-grok-client-mode"] == "cli"
    u = c.get("/api/setup/usage", headers=H).json()
    assert u["plans"]["grok"]["usedPercent"] == 12.5 and len(fx.of("/v1/billing")) == 1  # 存着的不旧：不再问
    fx.billing["config"]["creditUsagePercent"] = 40
    assert c.get("/api/engine/grok/usage", headers=H).json()["quota"]["usedPercent"] == 40  # 这个接口当场问
    assert c.get("/api/engine/grok/usage", headers={**H, "X-Mousse-Via": "mcp"}).status_code == 403
    assert c.get("/api/models", headers=H).json()["grok"]["quota"]["usedPercent"] == 40 and grok_plan.status()["quota"]["usedPercent"] == 40
    assert setup_ctl.main(["grok"]) == 0 and "SuperGrok Heavy · 40% used this week, resets 2026-10-25" in capsys.readouterr().out
    assert setup_ctl.main(["usage"]) == 0 and "Grok subscription: SuperGrok Heavy · 40% used this week" in capsys.readouterr().out
    tok = grok_plan.record()["access_token"]
    assert tok not in json.dumps(grok_plan.state()) and tok not in json.dumps(u)
    fx.api_fail = 503  # 问不到：留着上一次的数，说一声
    q = run(grok_plan.refresh_quota())
    assert q["usedPercent"] == 40 and "503" in q["error"]
    fx.api_fail, fx.billing = None, {"nothing": "here"}
    assert "isn't readable" in run(grok_plan.refresh_quota())["error"]
    run(grok_plan.logout())
    assert grok_plan.quota() is None and grok_plan.state().get("quota") is None


def test_reading_the_billing_answer():
    p = grok_plan.parse_quota
    assert p({"config": {"used": {"val": "250"}, "monthlyLimit": {"val": "1000"}, "billingPeriodEnd": "2026-11-01T00:00:00Z"}}) == {
        "plan": "SuperGrok", "period": "monthly", "usedPercent": 25.0, "periodStart": None, "resetsAt": "2026-11-01T00:00:00+00:00"}
    weekly = p({"subscription_tier": "SuperGrok", "config": {"current_period": {"type": "BILLING_PERIOD_TYPE_WEEKLY", "end": "2026-10-25T00:00:00Z"}}})
    assert weekly["usedPercent"] is None and weekly["period"] == "weekly"  # 没给百分比就不编
    assert grok_plan.quota_line(weekly) == "SuperGrok · xAI didn't say how much is used this week, resets 2026-10-25"
    assert p({"config": {"credit_usage_percent": 130}})["usedPercent"] == 100.0
    assert p({"config": {"prepaidBalance": {"val": "5000"}}}) is None  # API 的预付余额是按量的另一本账：不算订阅的额度
    assert p({"subscription_tier": "x" * 200, "config": {"creditUsagePercent": 1}})["plan"] == "SuperGrok"
    assert p("junk") is None and p({"config": {"creditUsagePercent": -1}}) is None
