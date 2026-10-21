"""连接器的授权（apps.begin / finish / bearer）：本机起一个假服务（要 Bearer 的 MCP + 资源元数据 + 授权服务器：DCR、授权码 + PKCE、
刷新），不连真服务。管：连一遍；回调幂等（同一个 state 来两次、同时来两次、请求断了线，都只换一次令牌）；超时、不认得、删掉了的
state 各说一句；注册过的客户端什么时候沿用；刷新时哪些错才要重新授权；目录里连不了的。
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
import socket
import time
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import HTTPException
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

import apps
from config import settings

REDIRECT = "testmousse://oauth/callback"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Fake:
    """假服务的状态和开关：注册了几次、换了几次令牌、下一次 /token 回什么错。"""

    def __init__(self, port: int):
        self.base = f"http://127.0.0.1:{port}"
        self.clients: dict[str, dict] = {}
        self.codes: dict[str, dict] = {}
        self.access: set[str] = set()
        self.refresh: dict[str, str] = {}
        self.registers = 0
        self.token_calls = 0
        self.register_status = 201
        self.token_fail: list[tuple[int, dict]] = []
        self.token_delay = 0.0
        self.meta = True
        self.issued: list[str] = []

    def grant(self, authorize_url: str) -> tuple[str, str]:
        """用户在授权页登录、同意 → (code, state)。客户端或回调对不上就像真服务那样卡在授权页（app 收不到任何东西）。"""
        q = {k: v[0] for k, v in parse_qs(urlsplit(authorize_url).query).items()}
        c = self.clients.get(q["client_id"])
        if not c or q["redirect_uri"] not in c["redirect_uris"]:
            raise AssertionError("invalid_client at the authorize page")
        code = "code-" + secrets.token_hex(8)
        self.codes[code] = {"client_id": q["client_id"], "redirect_uri": q["redirect_uri"], "challenge": q["code_challenge"]}
        return code, q["state"]

    def tokens(self, client_id: str) -> dict:
        at, rt = "at-" + secrets.token_hex(8), "rt-" + secrets.token_hex(8)
        self.access.add(at)
        self.refresh[rt] = client_id
        self.issued += [at, rt]
        return {"access_token": at, "token_type": "Bearer", "expires_in": 3600, "refresh_token": rt}


def fake_app(f: Fake):
    mcp = FastMCP("fake", host="127.0.0.1", log_level="ERROR", stateless_http=True, json_response=True,
                  transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))

    @mcp.tool()
    async def ping() -> str:
        """Say pong."""
        return "pong"

    async def prm(_: Request):
        return JSONResponse({"resource": f"{f.base}/mcp", "authorization_servers": [f.base], "scopes_supported": ["read"]})

    async def as_meta(_: Request):
        if not f.meta:
            return JSONResponse({}, status_code=404)
        return JSONResponse({"issuer": f.base, "authorization_endpoint": f"{f.base}/authorize", "token_endpoint": f"{f.base}/token",
                             "registration_endpoint": f"{f.base}/register", "code_challenge_methods_supported": ["S256"],
                             "token_endpoint_auth_methods_supported": ["none"], "response_types_supported": ["code"]})

    async def register(req: Request):
        f.registers += 1
        if f.register_status != 201:
            return JSONResponse({"error": "invalid_redirect_uri", "error_description": "not on our allowlist"}, status_code=f.register_status)
        body = await req.json()
        cid = f"client-{f.registers}"
        f.clients[cid] = body
        return JSONResponse({**body, "client_id": cid}, status_code=201)

    async def token(req: Request):
        f.token_calls += 1
        if f.token_delay:
            await asyncio.sleep(f.token_delay)
        if f.token_fail:
            status, body = f.token_fail.pop(0)
            return JSONResponse(body, status_code=status)
        form = dict((await req.form()).items())
        if form.get("grant_type") == "authorization_code":
            c = f.codes.pop(str(form.get("code") or ""), None)
            verifier = str(form.get("code_verifier") or "").encode()
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier).digest()).rstrip(b"=").decode()
            if not c or c["client_id"] != form.get("client_id") or c["redirect_uri"] != form.get("redirect_uri") or c["challenge"] != challenge:
                return JSONResponse({"error": "invalid_grant"}, status_code=400)
            return JSONResponse(f.tokens(c["client_id"]))
        if form.get("grant_type") == "refresh_token":
            cid = f.refresh.pop(str(form.get("refresh_token") or ""), None)
            if not cid:
                return JSONResponse({"error": "invalid_grant"}, status_code=400)
            return JSONResponse(f.tokens(cid))
        return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)

    inner = mcp.streamable_http_app()
    inner.router.routes[:0] = [Route("/.well-known/oauth-protected-resource/mcp", prm), Route("/.well-known/oauth-authorization-server", as_meta),
                               Route("/register", register, methods=["POST"]), Route("/token", token, methods=["POST"])]

    async def app(scope, receive, send):
        if scope["type"] == "http" and scope["path"].startswith("/mcp"):
            auth = dict(scope["headers"]).get(b"authorization", b"").decode()
            if not auth.startswith("Bearer ") or auth[7:] not in f.access:
                meta = f"{f.base}/.well-known/oauth-protected-resource/mcp"
                await JSONResponse({"error": "invalid_token"}, status_code=401,
                                   headers={"WWW-Authenticate": f'Bearer resource_metadata="{meta}", scope="read"'})(scope, receive, send)
                return
        await inner(scope, receive, send)

    return app


def scenario(tmp_path, monkeypatch, body):
    """新的数据目录、起假服务，跑 body(f)；log_activity 记到 f.acts（不碰数据库）。"""
    import uvicorn
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(apps, "PARALLEL", asyncio.Semaphore(4))
    monkeypatch.setattr(apps, "_refreshing", {})
    monkeypatch.setattr(apps, "_finishing", {}, raising=False)
    acts: list[str] = []
    monkeypatch.setattr(apps, "log_activity", lambda text, kind, actor=None: acts.append(text))

    async def go():
        port = free_port()
        f = Fake(port)
        f.acts = acts
        server = uvicorn.Server(uvicorn.Config(fake_app(f), host="127.0.0.1", port=port, log_level="error", access_log=False, lifespan="on"))
        task = asyncio.create_task(server.serve())
        for _ in range(500):
            if server.started:
                break
            await asyncio.sleep(0.01)
        try:
            await body(f)
            return f
        finally:
            server.should_exit = True
            await task

    return asyncio.run(go())


async def add(f: Fake) -> dict:
    return await apps.add_app(apps.AddIn(name="Fake", url=f"{f.base}/mcp", auth="oauth", redirect_uri=REDIRECT))


async def reconnect() -> dict:
    return await apps.connect("fake", apps.ConnectIn(redirect_uri=REDIRECT))


async def callback(code: str | None, state: str, error: str | None = None) -> dict:
    return await apps.oauth_callback(apps.CallbackIn(state=state, code=code, error=error))


async def connect(f: Fake) -> dict:
    r = await add(f)
    code, state = f.grant(r["authorizeUrl"])
    return (await callback(code, state))["app"]


def client_of(url: str) -> str:
    return parse_qs(urlsplit(url).query)["client_id"][0]


def expire_token() -> None:
    s = apps.load("secrets.json")
    s["fake"]["tokens"]["expires_at"] = time.time() - 1
    apps.store("secrets.json", s)


# —— 连一遍 ——————————————————————————————————————————————————————————————

def test_connect_lists_tools_and_keeps_the_client_that_worked(tmp_path, monkeypatch):
    async def body(f):
        app = await connect(f)
        assert app["status"] == "connected" and [t["name"] for t in app["tools"]] == ["ping"]
        cs = apps.load("clients.json")
        assert [v["client_id"] for v in cs.values()] == ["client-1"] and all(v.get("ok_at") for v in cs.values())
        assert await apps.call("fake", "ping", {}) == ("pong", False)

    f = scenario(tmp_path, monkeypatch, body)
    assert f.registers == 1 and f.token_calls == 1 and f.acts == ["Started connecting Fake", "Connected Fake"]


# —— 回调幂等 ————————————————————————————————————————————————————————————

def test_the_same_callback_twice_returns_the_connector(tmp_path, monkeypatch):
    async def body(f):
        r = await add(f)
        code, state = f.grant(r["authorizeUrl"])
        first = await callback(code, state)
        again = await callback(code, state)
        assert first["app"]["id"] == again["app"]["id"] == "fake" and again["app"]["status"] == "connected"
        raw = (settings.data_dir / "apps" / "states.json").read_text()
        assert state not in raw and code not in raw  # 只记指纹

    f = scenario(tmp_path, monkeypatch, body)
    assert f.token_calls == 1 and f.acts.count("Connected Fake") == 1


def test_two_copies_at_once_exchange_the_code_once(tmp_path, monkeypatch):
    async def body(f):
        f.token_delay = 0.3
        r = await add(f)
        code, state = f.grant(r["authorizeUrl"])
        a, b = await asyncio.gather(callback(code, state), callback(code, state))
        assert a["app"]["status"] == b["app"]["status"] == "connected"

    assert scenario(tmp_path, monkeypatch, body).token_calls == 1


def test_a_dropped_request_still_finishes_and_the_retry_gets_the_connector(tmp_path, monkeypatch):
    async def body(f):
        f.token_delay = 0.3
        r = await add(f)
        code, state = f.grant(r["authorizeUrl"])
        first = asyncio.ensure_future(callback(code, state))
        await asyncio.sleep(0.1)
        first.cancel()  # app 那头断了线
        with pytest.raises(asyncio.CancelledError):
            await first
        again = await callback(code, state)
        assert again["app"]["status"] == "connected"
        await asyncio.sleep(0.4)
        assert (await callback(code, state))["app"]["status"] == "connected"  # 做完以后再来一次：按 states.json

    assert scenario(tmp_path, monkeypatch, body).token_calls == 1


def test_a_late_callback_says_timed_out(tmp_path, monkeypatch):
    async def body(f):
        r = await add(f)
        code, state = f.grant(r["authorizeUrl"])
        pend = apps.load("pending.json")
        pend[state]["created"] -= apps.PENDING_TTL + 5
        apps.store("pending.json", pend)
        for _ in range(2):  # 再交一次也是这句
            with pytest.raises(HTTPException) as e:
                await callback(code, state)
            assert e.value.status_code == 400 and "timed out" in e.value.detail and "30 minutes" in e.value.detail
        att = apps.public(apps.get("fake"))["attempt"]
        assert att["failed"] == "authorize" and att["title"] == "The sign-in timed out"

    assert scenario(tmp_path, monkeypatch, body).token_calls == 0


def test_a_callback_cleared_out_while_waiting_still_says_timed_out(tmp_path, monkeypatch):
    async def body(f):
        r = await add(f)
        code, state = f.grant(r["authorizeUrl"])
        pend = apps.load("pending.json")
        pend[state]["created"] -= apps.PENDING_TTL + 5
        apps.store("pending.json", pend)
        await reconnect()  # 又点了一次：过期的那次顺手清掉
        assert state not in apps.load("pending.json")
        with pytest.raises(HTTPException) as e:
            await callback(code, state)
        assert "timed out" in e.value.detail

    scenario(tmp_path, monkeypatch, body)


def test_an_unknown_state_is_explained(tmp_path, monkeypatch):
    async def body(f):
        with pytest.raises(HTTPException) as e:
            await callback("whatever", "not-a-state-we-made")
        assert e.value.status_code == 400 and "no longer valid" in e.value.detail

    scenario(tmp_path, monkeypatch, body)


def test_a_failed_exchange_answers_the_same_way_again(tmp_path, monkeypatch):
    async def body(f):
        f.token_fail = [(400, {"error": "invalid_grant", "error_description": "code expired"})]
        r = await add(f)
        code, state = f.grant(r["authorizeUrl"])
        with pytest.raises(HTTPException) as first:
            await callback(code, state)
        with pytest.raises(HTTPException) as again:
            await callback(code, state)
        assert first.value.status_code == again.value.status_code == 502
        assert first.value.detail == again.value.detail == "Token exchange failed: invalid_grant: code expired"
        app = apps.public(apps.get("fake"))
        assert app["status"] == "needs_auth" and app["error"] == first.value.detail
        assert app["attempt"]["failed"] == "token" and app["attempt"]["title"] == "Token exchange failed"
        assert [s["state"] for s in app["attempt"]["steps"]] == ["done", "done", "done", "failed", "todo"]

    assert scenario(tmp_path, monkeypatch, body).token_calls == 1


def test_a_callback_for_a_removed_connector_says_so(tmp_path, monkeypatch):
    async def body(f):
        r = await add(f)
        code, state = f.grant(r["authorizeUrl"])
        await apps.delete_app("fake")
        with pytest.raises(HTTPException) as e:
            await callback(code, state)
        assert e.value.status_code == 404 and "removed" in e.value.detail

    scenario(tmp_path, monkeypatch, body)


def test_an_earlier_attempt_still_finishes(tmp_path, monkeypatch):
    async def body(f):
        first = await add(f)
        second = await reconnect()
        assert first["state"] != second["state"]
        code, state = f.grant(first["authorizeUrl"])
        assert (await callback(code, state))["app"]["status"] == "connected"

    scenario(tmp_path, monkeypatch, body)


# —— 每一步记下来 ——————————————————————————————————————————————————————————

def logged() -> list[dict]:
    return [json.loads(line) for line in (settings.data_dir / "apps" / "log.jsonl").read_text().splitlines()]


def test_every_step_is_recorded_and_nothing_secret_is_written(tmp_path, monkeypatch):
    async def body(f):
        r = await add(f)
        att = apps.public(apps.get("fake"))["attempt"]
        assert [(s["key"], s["state"]) for s in att["steps"]] == [("discover", "done"), ("register", "done"), ("authorize", "waiting"),
                                                                   ("token", "todo"), ("tools", "todo")]
        assert att["title"] is None and att["steps"][2]["label"] == "Sign in and approve at Fake"
        code, state = f.grant(r["authorizeUrl"])
        await callback(code, state)
        assert [s["ok"] for s in apps.get("fake")["attempt"]["steps"]] == [True] * 5
        assert apps.public(apps.get("fake"))["attempt"] is None  # 连上了：那次是旧事，详情页不再说
        expire_token()
        await apps.call("fake", "ping", {})  # 刷新一次
        rows = logged()
        assert [(r["step"], r["ok"]) for r in rows] == [("discover", True), ("register", True), ("authorize", None), ("authorize", True),
                                                        ("token", True), ("tools", True), ("refresh", True)]
        assert {r["attempt"] for r in rows[:6]} == {apps.sid_of(state)} and rows[1]["client"] == "new" and rows[6]["rotated"] is True
        d = settings.data_dir / "apps"
        for name in ("log.jsonl", "apps.json", "states.json"):
            text = (d / name).read_text()
            assert all(s not in text for s in [state, code, *f.issued]), name

    scenario(tmp_path, monkeypatch, body)


def test_a_refused_registration_names_the_step(tmp_path, monkeypatch):
    async def body(f):
        f.register_status = 400
        with pytest.raises(HTTPException) as e:
            await add(f)
        assert e.value.status_code == 502
        assert e.value.detail == "Registration was refused: the server answered invalid_redirect_uri: not on our allowlist"
        assert apps.get("fake") is None  # 新加的：什么都不留
        f.register_status = 201
        await add(f)
        f.register_status = 400
        with pytest.raises(HTTPException):
            await reconnect()  # 上一个注册没用成过：重新注册，被拒
        app = apps.public(apps.get("fake"))
        assert app["attempt"]["failed"] == "register" and app["attempt"]["title"] == "Registration was refused"
        assert [s["state"] for s in app["attempt"]["steps"]] == ["done", "failed", "todo", "todo", "todo"]
        assert "invalid_redirect_uri" in app["attempt"]["steps"][1]["note"] and app["error"].startswith("Registration was refused: ")

    scenario(tmp_path, monkeypatch, body)


def test_no_oauth_metadata_means_no_authorization_server(tmp_path, monkeypatch):
    async def body(f):
        f.meta = False
        with pytest.raises(HTTPException) as e:
            await add(f)
        assert e.value.detail.startswith("Couldn't find a usable authorization server: 127.0.0.1 doesn't publish OAuth metadata")

    scenario(tmp_path, monkeypatch, body)


def test_an_unreachable_token_endpoint_is_a_network_problem(tmp_path, monkeypatch):
    monkeypatch.setattr(apps, "cfg", lambda: {"allow_local": True})  # 换令牌的地址换到本机另一个没人听的端口

    async def body(f):
        r = await add(f)
        code, state = f.grant(r["authorizeUrl"])
        pend = apps.load("pending.json")
        pend[state]["token_endpoint"] = f"http://127.0.0.1:{free_port()}/token"
        apps.store("pending.json", pend)
        with pytest.raises(HTTPException) as e:
            await callback(code, state)
        assert e.value.status_code == 502 and e.value.detail.startswith("Token exchange failed: Couldn't reach 127.0.0.1 (")
        att = apps.public(apps.get("fake"))["attempt"]
        assert att["failed"] == "token" and att["title"] == "Couldn't reach Fake"

    scenario(tmp_path, monkeypatch, body)


def test_a_closed_sign_in_page_is_recorded_and_a_late_callback_still_works(tmp_path, monkeypatch):
    async def body(f):
        r = await add(f)
        out = (await apps.oauth_closed(apps.ClosedIn(state=r["state"], how="cancel")))["app"]
        assert out["attempt"]["failed"] == "authorize" and out["attempt"]["title"] == "The sign-in page was closed without finishing"
        assert out["error"].startswith("The sign-in page was closed without finishing: If the page showed an error")
        assert apps.load("clients.json") == {}  # 这个注册下次不用
        assert r["state"] in apps.load("pending.json")  # 安卓上回调可能晚到一步
        code, state = f.grant(r["authorizeUrl"])
        assert (await callback(code, state))["app"]["status"] == "connected"
        assert [v["client_id"] for v in apps.load("clients.json").values()] == ["client-1"]  # 用成了，又记回来
        again = (await apps.oauth_closed(apps.ClosedIn(state=state, how="dismiss")))["app"]  # 做完了的：什么都不改
        assert again["status"] == "connected" and again["error"] == ""
        assert (await apps.oauth_closed(apps.ClosedIn(state="not-a-state-we-made")))["app"] is None
        assert [(r["step"], r["ok"], r.get("how")) for r in logged()][3] == ("authorize", False, "cancel")

    scenario(tmp_path, monkeypatch, body)


def test_errors_from_the_sign_in_page(tmp_path, monkeypatch):
    async def body(f):
        r = await add(f)
        with pytest.raises(HTTPException) as e:
            await callback(None, r["state"], error="access_denied")
        assert e.value.detail == "You cancelled the sign-in at Fake"
        att = apps.public(apps.get("fake"))["attempt"]
        assert att["title"] == "You cancelled the sign-in" and att["steps"][2]["note"] == "access_denied"
        r = await reconnect()
        with pytest.raises(HTTPException) as e:
            await apps.oauth_callback(apps.CallbackIn(state=r["state"], error="invalid_scope", error_description="no such scope"))
        assert e.value.detail == "The sign-in page returned an error: invalid_scope: no such scope"
        assert apps.public(apps.get("fake"))["attempt"]["title"] == "The sign-in page returned an error"

    scenario(tmp_path, monkeypatch, body)


def test_waiting_too_long_reads_as_timed_out(tmp_path, monkeypatch):
    async def body(f):
        await add(f)
        app = apps.get("fake")
        app["attempt"]["t"] -= apps.PENDING_TTL + 5
        apps.put(app)
        att = apps.public(apps.get("fake"))["attempt"]
        assert att["title"] == "The sign-in timed out" and att["steps"][2]["state"] == "failed" and "30 minutes" in att["steps"][2]["note"]

    scenario(tmp_path, monkeypatch, body)


def test_refresh_results_and_failed_calls_are_logged(tmp_path, monkeypatch):
    async def body(f):
        await connect(f)
        expire_token()
        f.token_fail = [(503, {"error": "temporarily_unavailable"})]
        with pytest.raises(apps.AppError):
            await apps.call("fake", "ping", {})
        f.token_fail = [(400, {"error": "invalid_grant"})]
        with pytest.raises(apps.AuthError):
            await apps.call("fake", "ping", {})
        rows = logged()[6:]
        assert [(r["step"], r["ok"], r.get("then"), r.get("status")) for r in rows] == [
            ("refresh", False, "retry", None), ("call", False, None, None), ("refresh", False, "reconnect", None),
            ("status", False, None, "needs_auth"), ("call", False, None, None)]
        assert rows[-1]["tool"] == "ping" and "invalid_grant" in rows[2]["note"]

    scenario(tmp_path, monkeypatch, body)


def test_the_log_keeps_the_newer_half(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(apps, "LOG_MAX", 2000)
    for i in range(200):
        apps.log_step("fake", "call", False, f"failure {i}", tool="ping")
    p = settings.data_dir / "apps" / "log.jsonl"
    notes = [r["note"] for r in logged()]
    assert p.stat().st_size <= 2000 and notes == [f"failure {i}" for i in range(200 - len(notes), 200)]  # 整行、连着、最新的在
    assert p.stat().st_mode & 0o777 == 0o600


# —— 客户端什么时候沿用 ——————————————————————————————————————————————————————

def test_a_registration_that_never_worked_is_not_reused(tmp_path, monkeypatch):
    async def body(f):
        first = await add(f)
        pend = apps.load("pending.json")  # 隔了 11 分钟再点（10/21 前只在 10 分钟内又点才换新的，过了就一直用没成过的那个）
        pend[first["state"]]["created"] -= 11 * 60
        apps.store("pending.json", pend)
        second = await reconnect()  # 第一次没做完（比如授权页报了错、被关掉）：换一个新注册的
        assert client_of(first["authorizeUrl"]) == "client-1" and client_of(second["authorizeUrl"]) == "client-2"
        code, state = f.grant(second["authorizeUrl"])
        await callback(code, state)
        third = await reconnect()  # 用成过的沿用
        assert client_of(third["authorizeUrl"]) == "client-2"

    assert scenario(tmp_path, monkeypatch, body).registers == 2


def test_invalid_client_at_the_token_endpoint_forgets_the_client(tmp_path, monkeypatch):
    async def body(f):
        await connect(f)
        r = await reconnect()
        assert client_of(r["authorizeUrl"]) == "client-1"
        f.token_fail = [(401, {"error": "invalid_client"})]
        code, state = f.grant(r["authorizeUrl"])
        with pytest.raises(HTTPException):
            await callback(code, state)
        assert apps.load("clients.json") == {}
        assert apps.get("fake")["status"] == "connected"  # 换账号没成：原来的授权还在
        assert client_of((await reconnect())["authorizeUrl"]) == "client-2"

    scenario(tmp_path, monkeypatch, body)


# —— 刷新 ——————————————————————————————————————————————————————————————

@pytest.mark.parametrize("status,answer", [(503, {"error": "temporarily_unavailable"}), (400, {"error": "server_error"}), (500, {}),
                                           (429, {"error": "slow_down"})])
def test_a_provider_hiccup_on_refresh_keeps_the_grant(tmp_path, monkeypatch, status, answer):
    async def body(f):
        await connect(f)
        expire_token()
        f.token_fail = [(status, answer)]
        with pytest.raises(apps.AppError) as e:
            await apps.call("fake", "ping", {})
        assert not isinstance(e.value, apps.AuthError) and "retry" in str(e.value)
        assert apps.get("fake")["status"] == "connected"
        assert await apps.call("fake", "ping", {}) == ("pong", False)  # 下一次刷新成了

    scenario(tmp_path, monkeypatch, body)


@pytest.mark.parametrize("status,answer,said", [(400, {"error": "invalid_grant", "error_description": "revoked"}, "invalid_grant: revoked"),
                                                (401, {"error": "invalid_client"}, "invalid_client"), (400, {}, "HTTP 400")])
def test_a_refused_refresh_asks_to_reconnect(tmp_path, monkeypatch, status, answer, said):
    async def body(f):
        await connect(f)
        expire_token()
        f.token_fail = [(status, answer)]
        with pytest.raises(apps.AuthError):
            await apps.call("fake", "ping", {})
        app = apps.get("fake")
        assert app["status"] == "needs_auth" and said in app["error"]
        if answer.get("error") == "invalid_client":
            assert apps.load("clients.json") == {}

    scenario(tmp_path, monkeypatch, body)


def test_calls_that_find_the_token_expired_refresh_it_once(tmp_path, monkeypatch):
    async def body(f):
        await connect(f)
        expire_token()
        f.token_delay = 0.3
        before = f.token_calls
        got = await asyncio.gather(*[apps.call("fake", "ping", {}) for _ in range(3)])
        assert got == [("pong", False)] * 3
        assert f.token_calls == before + 1  # 刷新令牌换一次就作废：一起过期的几个调用等同一次刷新，不然后面的会被当成授权失效
        assert apps.get("fake")["status"] == "connected" and [r["step"] for r in logged()].count("refresh") == 1

    scenario(tmp_path, monkeypatch, body)


# —— 目录里连不了的 ————————————————————————————————————————————————————————

def test_blocked_catalog_entries_are_listed_last_and_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    listed = apps.list_apps()["catalog"]
    blocked = [c["id"] for c in listed if c["blocked"]]
    assert sorted(blocked) == ["canva", "figma", "stripe"] and [c["id"] for c in listed[-3:]] == blocked
    assert all(not c["blocked"] for c in listed if c["id"] in ("notion", "todoist", "huggingface"))
    with pytest.raises(HTTPException) as e:
        asyncio.run(apps.add_app(apps.AddIn(catalog="figma", redirect_uri=REDIRECT)))
    assert e.value.status_code == 400 and "approved" in e.value.detail
    assert apps.get("figma") is None
    monkeypatch.setattr(apps, "cfg", lambda: {"catalog": {"stripe": {"blocked": None}}})  # server.json 放开
    assert apps.catalog_json(apps.catalog()["stripe"], False)["blocked"] is None
