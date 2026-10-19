"""假的 OpenAI 授权服务器（测 engine/chatgpt_plan.py 的 Sign in with ChatGPT，不连真的 auth.openai.com）：本机一个 HTTP 服务（后台线程），
照官方 developers.openai.com/siwc 的 token-sharing-open-source 那几页的样子回话。

  GET  /.well-known/openid-configuration   发现文档：jwks_uri、revocation_endpoint（还有 authorization / token endpoint）
  GET  /.well-known/jwks.json              公钥（RS256，起服务时现生成一对）
  GET  /api/accounts/authorize             当成「用户在浏览器里登录、同意了」：查参数（client_id、redirect_uri 只认 http://127.0.0.1:<端口>/auth/callback、
                                           scope、resource、PKCE S256、state、nonce、ext_agent_host_id；首次注册要 agent_name_hint），302 跳回
                                           redirect_uri?code=…&scope=…&state=…&client_id=…；deny = True 跳回 error=access_denied
  POST /api/accounts/oauth/token           authorization_code（验 PKCE、redirect_uri、client_id、resource）/ refresh_token（每次换新的一对，
                                           旧的刷新令牌再用 = refresh_token_reused）
  POST /oauth/revoke                       撤销刷新令牌（空的 200；那一份的访问令牌也作废）
按需要改的属性：scope（给出去的 scope）、deny、revoke_status、refresh_fail（{"status", "error"}，一次性的）、token_ttl、earliest（earliest_refresh_at）、
subject、email、plan、omit_client_id（注册的回调不带 client_id）、wrong_nonce、token_scope（令牌端点回的 scope，不写同 scope）。
valid_access(令牌) 给假模型服务（FakeModelServer(key=…)）认现在那一把访问令牌。requests 记下每个请求（不含令牌原文）。
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

PLAN_SCOPE = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"


def b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


class FakeAuthServer:
    def __init__(self) -> None:
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.scope = PLAN_SCOPE
        self.token_scope: str | None = None
        self.deny = False
        self.omit_client_id = False
        self.wrong_nonce = False
        self.revoke_status = 200
        self.refresh_fail: dict | None = None
        self.refresh_id_token = False
        self.token_ttl = 3600
        self.earliest: object = None
        self.subject = "user-fake-1"
        self.email = "alex@example.com"
        self.plan = "plus"
        self.requests: list[dict] = []
        self.codes: dict[str, dict] = {}
        self.refresh: dict[str, dict] = {}      # 刷新令牌 → {"client_id", "access", "live"}
        self.access: set[str] = set()           # 现在认的访问令牌
        self.clients = 0
        self.lock = threading.Lock()
        self._httpd: ThreadingHTTPServer | None = None

    # —— 起、停 ——
    def __enter__(self) -> FakeAuthServer:
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), _handler(self))
        self._httpd.daemon_threads = True
        threading.Thread(target=self._httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        return self

    def __exit__(self, *_exc) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()

    @property
    def url(self) -> str:
        assert self._httpd is not None
        host, port = self._httpd.server_address[:2]
        return f"http://{host}:{port}"

    def jwks(self) -> dict:
        n = self.key.public_key().public_numbers()
        return {"keys": [{"kty": "RSA", "kid": "fake-1", "alg": "RS256", "use": "sig",
                          "n": b64(n.n.to_bytes((n.n.bit_length() + 7) // 8, "big")), "e": b64(n.e.to_bytes(3, "big"))}]}

    def id_token(self, client_id: str, nonce: str | None, **over) -> str:
        now = int(time.time())
        claims = {"iss": self.url, "aud": client_id, "sub": self.subject, "email": self.email, "iat": now, "exp": now + 3600,
                  "https://api.openai.com/auth": {"chatgpt_plan_type": self.plan}}
        if nonce is not None:
            claims["nonce"] = "not-the-nonce" if self.wrong_nonce else nonce
        claims.update(over)
        head = b64(json.dumps({"alg": "RS256", "kid": "fake-1", "typ": "JWT"}).encode())
        body = b64(json.dumps(claims).encode())
        sig = self.key.sign(f"{head}.{body}".encode(), padding.PKCS1v15(), hashes.SHA256())
        return f"{head}.{body}.{b64(sig)}"

    def valid_access(self, token: str) -> bool:
        with self.lock:
            return token in self.access

    def revoke_access(self) -> None:
        """模拟用户在 ChatGPT 设置里断开：访问令牌、刷新令牌都不认了，不通知谁。"""
        with self.lock:
            self.access.clear()
            for r in self.refresh.values():
                r["live"] = False

    def of(self, path: str) -> list[dict]:
        return [r for r in self.requests if r["path"] == path]

    def _issue(self, client_id: str, nonce: str | None, scope: str, with_id: bool = True) -> dict:
        access, refresh = f"at-{secrets.token_hex(8)}", f"rt-{secrets.token_hex(8)}"
        with self.lock:
            self.access.add(access)
            self.refresh[refresh] = {"client_id": client_id, "access": access, "live": True}
        out = {"access_token": access, "refresh_token": refresh, "token_type": "Bearer", "expires_in": self.token_ttl}
        if with_id:
            out["id_token"] = self.id_token(client_id, nonce)
        if scope:
            out["scope"] = scope
        if self.earliest is not None:
            out["earliest_refresh_at"] = self.earliest
        return out


def _handler(fa: FakeAuthServer) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_a) -> None:
            pass

        def _send(self, status: int, payload=None, headers: dict | None = None) -> None:
            data = b"" if payload is None else json.dumps(payload).encode()
            self.send_response(status)
            if payload is not None:
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            u = urlsplit(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            fa.requests.append({"path": u.path, "params": {k: v for k, v in q.items() if k not in ("id_token_hint",)}})
            if u.path == "/.well-known/openid-configuration":
                self._send(200, {"issuer": fa.url, "authorization_endpoint": f"{fa.url}/api/accounts/authorize",
                                 "token_endpoint": f"{fa.url}/api/accounts/oauth/token", "jwks_uri": f"{fa.url}/.well-known/jwks.json",
                                 "revocation_endpoint": f"{fa.url}/oauth/revoke"})
            elif u.path == "/.well-known/jwks.json":
                self._send(200, fa.jwks())
            elif u.path == "/api/accounts/authorize":
                self._authorize(q)
            else:
                self._send(404, {"error": "not_found"})

        def _authorize(self, q: dict) -> None:
            redirect = q.get("redirect_uri") or ""
            r = urlsplit(redirect)
            problems = []
            if r.scheme != "http" or r.hostname != "127.0.0.1" or r.path != "/auth/callback" or not r.port:
                problems.append("redirect_uri")
            for k in ("state", "nonce", "code_challenge", "ext_agent_host_id"):
                if not q.get(k):
                    problems.append(k)
            if q.get("code_challenge_method") != "S256" or q.get("response_type") != "code":
                problems.append("pkce")
            if q.get("resource") != "https://api.openai.com/v1" or "chatgpt.tokens.use.direct" not in (q.get("scope") or "").split():
                problems.append("scope")
            registering = q.get("client_id") == "dynamic_agent_client"
            if registering and not q.get("agent_name_hint"):
                problems.append("agent_name_hint")
            if problems:
                self._send(400, {"error": "invalid_request", "problems": problems})
                return
            if fa.deny:
                loc = f"{redirect}?{urlencode({'error': 'access_denied', 'state': q['state']})}"
            else:
                if registering:
                    fa.clients += 1
                    client_id = f"oaiapp_fake{fa.clients}"
                else:
                    client_id = q["client_id"]
                code = f"code-{secrets.token_hex(6)}"
                fa.codes[code] = {"client_id": client_id, "redirect": redirect, "challenge": q["code_challenge"], "nonce": q["nonce"], "used": False}
                params = {"code": code, "scope": fa.scope, "state": q["state"]}
                if registering and not fa.omit_client_id:
                    params["client_id"] = client_id
                loc = f"{redirect}?{urlencode(params)}"
            self.send_response(302)
            self.send_header("Location", loc)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_POST(self) -> None:
            raw = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()
            form = {k: v[0] for k, v in parse_qs(raw).items()}
            u = urlsplit(self.path)
            fa.requests.append({"path": u.path, "grant": form.get("grant_type"), "client_id": form.get("client_id"),
                                "resource": form.get("resource"), "has_scope": "scope" in form, "hint": form.get("token_type_hint"),
                                "redirect_uri": form.get("redirect_uri")})
            if u.path == "/api/accounts/oauth/token":
                self._token(form)
            elif u.path == "/oauth/revoke":
                if fa.revoke_status == 200:
                    with fa.lock:
                        r = fa.refresh.get(form.get("token") or "")
                        if r and r["client_id"] == form.get("client_id"):
                            r["live"] = False
                            fa.access.discard(r["access"])
                    self._send(200)
                else:
                    self._send(fa.revoke_status, {"error": "server_error"})
            else:
                self._send(404, {"error": "not_found"})

        def _token(self, form: dict) -> None:
            if form.get("resource") != "https://api.openai.com/v1":
                self._send(400, {"error": "invalid_target"})
                return
            if form.get("grant_type") == "authorization_code":
                c = fa.codes.get(form.get("code") or "")
                if not c or c["used"]:
                    self._send(400, {"error": "invalid_grant"})
                    return
                c["used"] = True
                verifier = form.get("code_verifier") or ""
                if b64(hashlib.sha256(verifier.encode()).digest()) != c["challenge"]:
                    self._send(400, {"error": "invalid_grant", "error_description": "PKCE verification failed"})
                    return
                if form.get("client_id") != c["client_id"] or form.get("redirect_uri") != c["redirect"]:
                    self._send(400, {"error": "invalid_grant", "error_description": "client or redirect mismatch"})
                    return
                self._send(200, fa._issue(c["client_id"], c["nonce"], fa.token_scope if fa.token_scope is not None else fa.scope))
                return
            if form.get("grant_type") == "refresh_token":
                if "scope" in form:
                    self._send(400, {"error": "invalid_scope"})
                    return
                if fa.refresh_fail:
                    f, fa.refresh_fail = fa.refresh_fail, None
                    self._send(int(f.get("status") or 400), {"error": f.get("error") or "invalid_grant"})
                    return
                with fa.lock:
                    r = fa.refresh.get(form.get("refresh_token") or "")
                if not r or r["client_id"] != form.get("client_id"):
                    self._send(400, {"error": "invalid_grant"})
                    return
                if not r["live"]:
                    self._send(400, {"error": "refresh_token_reused"})
                    return
                with fa.lock:
                    r["live"] = False
                    fa.access.discard(r["access"])
                time.sleep(0.05)  # 让并发的刷新真有机会撞上
                self._send(200, fa._issue(r["client_id"], None, "", with_id=fa.refresh_id_token))
                return
            self._send(400, {"error": "unsupported_grant_type"})

    return Handler
