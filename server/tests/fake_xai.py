"""假的 xAI（测 engine/grok_plan.py 的 Grok 订阅，不连真的 auth.x.ai / api.x.ai、不用真账号、不花钱）：本机一个 HTTP 服务（后台线程），
账号服务和 api.x.ai 在同一个端口上，照 Hermes / OpenClaw 读到的样子回话。

账号服务（RFC 8628 设备码）：
  GET  /.well-known/openid-configuration   发现文档：device_authorization_endpoint、token_endpoint、revocation_endpoint（都在这个服务上）
  POST /oauth2/device/code                 {client_id, scope} → device_code、user_code、verification_uri(_complete)、expires_in、interval；
                                           deny_device = True 回 403（这个账号拿不到 OAuth）
  POST /oauth2/token                       设备码：前 pending_polls 次 authorization_pending（slow_down = True 时第一次回 slow_down），之后照 outcome：
                                           approve 发令牌 / deny access_denied / tier 403 / expire expired_token；刷新：每次换新的一对，旧的再用 =
                                           refresh_token_reused；refresh_fail（一次性）：{"status", "error"} 或 {"status", "html": True}（Cloudflare 的挑战页）
  POST /oauth2/revoke                      撤销刷新令牌（空的 200；revoke_status 改状态码）
api.x.ai（Bearer 只认现在发出去的访问令牌，或者 api_key；api_403 = True 时一律 403，plan_403 = True 时只有订阅的令牌 403；
api_fail = 状态码：一律回这个错）：
  GET  /v1/models                          models（OpenAI 的 data 列表）
  POST /v1/responses                       tools 里有 web_search / x_search：回答 + url_citation（x_search 还有顶层的 citations；x_cites = False
                                           时两路都没有）；不然是一句对话（chat_text），一次性的 JSON
  POST /v1/images/generations、/v1/images/edits   JSON → data[].b64_json（一张小 PNG）
  POST /v1/videos/generations              → request_id；GET /v1/videos/<id>：前 video_polls 次 pending，之后 done + video.url（这个服务上的
                                           /files/clip.mp4；video_host 写了就用 http://<它>/files/clip.mp4：测试里解析到这个服务）；video_fail = 回 failed
  POST /v1/tts                             → 一段假的 MP3
  GET  /v1/billing?format=credits          SuperGrok 的用量（billing，可以改）
requests 录下每个请求：path、method、form / json（不含令牌原文：bearer 只记是哪一种）、headers（不带 Authorization）。
数据都是编的。
"""
from __future__ import annotations

import base64
import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from evals.fakemedia import FAKE_MP3, FAKE_MP4, png

CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"


def b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def jwt(claims: dict) -> str:
    """不签名的 JWT（grok_plan 只读载荷：邮箱、sub、exp）。"""
    return f"{b64(json.dumps({'alg': 'none'}).encode())}.{b64(json.dumps(claims).encode())}.sig"


class FakeXai:
    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.devices: dict[str, dict] = {}      # device_code → {"user_code", "polls", "outcome"}
        self.refresh: dict[str, dict] = {}      # 刷新令牌 → {"access", "live"}
        self.access: set[str] = set()
        self.api_key: str | None = None
        self.token_ttl = 21600
        self.subject, self.email = "xai-user-fake-1", "alex@example.com"
        self.deny_device = False
        self.outcome = "approve"                # approve / deny / tier / expire
        self.pending_polls = 1
        self.interval: float = 1                # 设备码回的 interval（测试里改小）
        self.slow_down = False
        self.refresh_fail: dict | None = None
        self.revoke_status = 200
        self.no_refresh_token = False
        self.api_403 = False
        self.plan_403 = False
        self.api_fail: int | None = None
        self.x_cites = True
        self.models =[{"id": "grok-4.7", "object": "model"}, {"id": "grok-4.6", "object": "model"},
                       {"id": "grok-imagine-image", "object": "model"}, {"id": "grok-imagine-video", "object": "model"},
                       {"id": "grok-4.20-multi-agent-0309", "object": "model"}, {"id": "grok-build-0.1", "object": "model"}]
        self.chat_text = "Hello from the fake Grok."
        self.video_polls = 1
        self.videos: dict[str, int] = {}
        self.video_host: str | None = None
        self.video_fail = False
        self.billing: dict = {"subscription_tier": "SuperGrok Heavy",
                              "config": {"creditUsagePercent": 12.5, "currentPeriod": {"type": "BILLING_PERIOD_TYPE_WEEKLY",
                                                                                     "start": "2026-10-18T00:00:00Z",
                                                                                     "end": "2026-10-25T00:00:00Z"}}}
        self.lock = threading.Lock()
        self._httpd: ThreadingHTTPServer | None = None

    def __enter__(self) -> FakeXai:
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

    @property
    def api(self) -> str:
        return self.url + "/v1"

    def of(self, path: str) -> list[dict]:
        return [r for r in self.requests if r["path"] == path]

    def valid(self, token: str) -> bool:
        with self.lock:
            return token in self.access or (self.api_key is not None and token == self.api_key)

    def revoke_all(self) -> None:
        """模拟用户在 xAI 那边撤销了：访问令牌、刷新令牌都不认了，不通知谁。"""
        with self.lock:
            self.access.clear()
            for r in self.refresh.values():
                r["live"] = False

    def issue(self) -> dict:
        now = int(time.time())
        access = jwt({"sub": self.subject, "exp": now + self.token_ttl, "iat": now, "n": secrets.token_hex(6)})
        refresh = f"xrt-{secrets.token_hex(8)}"
        with self.lock:
            self.access.add(access)
            self.refresh[refresh] = {"access": access, "live": True}
        out = {"access_token": access, "token_type": "Bearer", "expires_in": self.token_ttl, "scope": "openid profile email offline_access",
               "id_token": jwt({"sub": self.subject, "email": self.email, "name": "Alex Example", "iat": now, "exp": now + 3600})}
        if not self.no_refresh_token:
            out["refresh_token"] = refresh
        return out


def _handler(fx: FakeXai) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_a) -> None:
            pass

        def _send(self, status: int, payload=None, *, ctype: str = "application/json", headers: dict | None = None) -> None:
            data = b"" if payload is None else payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            self.send_response(status)
            if payload is not None:
                self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def _bearer(self) -> str:
            h = self.headers.get("Authorization") or ""
            return h[7:] if h.startswith("Bearer ") else ""

        def _record(self, path: str, **extra) -> None:
            tok = self._bearer()
            kind = "none" if not tok else "api_key" if tok == fx.api_key else "plan" if tok in fx.access else "other"
            fx.requests.append({"path": path, "method": self.command, "bearer": kind,
                                "headers": {k.lower(): v for k, v in self.headers.items() if k.lower() != "authorization"}, **extra})

        def _api_ok(self) -> bool:
            if fx.api_403 or (fx.plan_403 and self._bearer() in fx.access):
                self._send(403, {"code": "The caller does not have permission to execute the specified operation",
                                 "error": "Your account is not eligible for API access with this sign-in"})
                return False
            if fx.api_fail:
                self._send(fx.api_fail, {"code": "rate_limited" if fx.api_fail == 429 else "unavailable", "error": "Try again later"})
                return False
            if not fx.valid(self._bearer()):
                self._send(401, {"code": "unauthenticated", "error": "Incorrect API key or token"})
                return False
            return True

        # —— GET ——
        def do_GET(self) -> None:  # noqa: N802
            u = urlsplit(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            self._record(u.path, query=q)
            if u.path == "/.well-known/openid-configuration":
                self._send(200, {"issuer": fx.url, "device_authorization_endpoint": f"{fx.url}/oauth2/device/code",
                                 "token_endpoint": f"{fx.url}/oauth2/token", "revocation_endpoint": f"{fx.url}/oauth2/revoke",
                                 "authorization_endpoint": f"{fx.url}/oauth2/auth"})
            elif u.path == "/v1/models":
                if self._api_ok():
                    self._send(200, {"object": "list", "data": fx.models})
            elif u.path.startswith("/v1/videos/"):
                if self._api_ok():
                    vid = u.path.rsplit("/", 1)[-1]
                    with fx.lock:
                        n = fx.videos.get(vid)
                        if n is not None:
                            fx.videos[vid] = n + 1
                    if n is None:
                        self._send(404, {"error": "no such request"})
                    elif n < fx.video_polls:
                        self._send(200, {"status": "pending"})
                    elif fx.video_fail:
                        self._send(200, {"status": "failed", "error": {"message": "The prompt was rejected by moderation"}})
                    else:
                        host = f"http://{fx.video_host}" if fx.video_host else fx.url
                        self._send(200, {"status": "done", "video": {"url": f"{host}/files/clip.mp4", "duration": 6}})
            elif u.path == "/files/clip.mp4":
                self._send(200, FAKE_MP4, ctype="video/mp4")
            elif u.path == "/v1/billing":
                if self._api_ok():
                    self._send(200, fx.billing)
            else:
                self._send(404, {"error": "not_found"})

        # —— POST ——
        def do_POST(self) -> None:  # noqa: N802
            raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            u = urlsplit(self.path)
            ctype = self.headers.get("Content-Type") or ""
            if "json" in ctype:
                try:
                    body = json.loads(raw or b"{}")
                except ValueError:
                    body = {}
                self._record(u.path, json=body)
            else:
                body = {k: v[0] for k, v in parse_qs(raw.decode()).items()}
                self._record(u.path, form={k: ("<secret>" if k in ("device_code", "refresh_token", "token") else v) for k, v in body.items()})
            if u.path == "/oauth2/device/code":
                self._device(body)
            elif u.path == "/oauth2/token":
                self._token(body)
            elif u.path == "/oauth2/revoke":
                if fx.revoke_status == 200:
                    with fx.lock:
                        r = fx.refresh.get(body.get("token") or "")
                        if r and body.get("client_id") == CLIENT_ID:
                            r["live"] = False
                            fx.access.discard(r["access"])
                    self._send(200)
                else:
                    self._send(fx.revoke_status, {"error": "server_error"})
            elif u.path == "/v1/responses":
                if self._api_ok():
                    self._responses(body)
            elif u.path in ("/v1/images/generations", "/v1/images/edits"):
                if self._api_ok():
                    n = max(1, min(4, int(body.get("n") or 1)))
                    self._send(200, {"data": [{"b64_json": base64.b64encode(png(4 + i, 4)).decode()} for i in range(n)]})
            elif u.path == "/v1/videos/generations":
                if self._api_ok():
                    vid = f"vid_{secrets.token_hex(4)}"
                    with fx.lock:
                        fx.videos[vid] = 0
                    self._send(200, {"request_id": vid})
            elif u.path == "/v1/tts":
                if self._api_ok():
                    self._send(200, FAKE_MP3, ctype="audio/mpeg")
            else:
                self._send(404, {"error": "not_found"})

        def _device(self, form: dict) -> None:
            if fx.deny_device:
                self._send(403, {"error": "access_denied", "error_description": "This account can't use OAuth for third-party apps"})
                return
            if form.get("client_id") != CLIENT_ID or "offline_access" not in (form.get("scope") or "").split():
                self._send(400, {"error": "invalid_request"})
                return
            dc, uc = f"dc-{secrets.token_hex(8)}", f"{secrets.token_hex(2).upper()}-{secrets.token_hex(2).upper()}"
            with fx.lock:
                fx.devices[dc] = {"user_code": uc, "polls": 0, "outcome": fx.outcome}
            self._send(200, {"device_code": dc, "user_code": uc, "verification_uri": f"{fx.url}/device",
                             "verification_uri_complete": f"{fx.url}/device?user_code={uc}", "expires_in": 600, "interval": fx.interval})

        def _token(self, form: dict) -> None:
            if form.get("client_id") != CLIENT_ID:
                self._send(400, {"error": "invalid_client"})
                return
            grant = form.get("grant_type")
            if grant == "urn:ietf:params:oauth:grant-type:device_code":
                with fx.lock:
                    d = fx.devices.get(form.get("device_code") or "")
                    if d is not None:
                        d["polls"] += 1
                if d is None:
                    self._send(400, {"error": "invalid_grant"})
                elif fx.slow_down and d["polls"] == 1:
                    self._send(400, {"error": "slow_down"})
                elif d["polls"] <= fx.pending_polls:
                    self._send(400, {"error": "authorization_pending"})
                elif d["outcome"] == "deny":
                    self._send(400, {"error": "access_denied"})
                elif d["outcome"] == "tier":
                    self._send(403, {"error": "permission_denied", "error_description": "Your plan doesn't include API access"})
                elif d["outcome"] == "expire":
                    self._send(400, {"error": "expired_token"})
                else:
                    with fx.lock:
                        fx.devices.pop(form.get("device_code") or "", None)
                    self._send(200, fx.issue())
                return
            if grant == "refresh_token":
                if fx.refresh_fail:
                    f, fx.refresh_fail = fx.refresh_fail, None
                    if f.get("html"):
                        self._send(int(f.get("status") or 403), b"<!doctype html><html><title>Just a moment...</title></html>", ctype="text/html")
                    else:
                        self._send(int(f.get("status") or 400), {"error": f.get("error") or "invalid_grant"})
                    return
                with fx.lock:
                    r = fx.refresh.get(form.get("refresh_token") or "")
                if not r:
                    self._send(400, {"error": "invalid_grant"})
                    return
                if not r["live"]:
                    self._send(400, {"error": "refresh_token_reused"})
                    return
                with fx.lock:
                    r["live"] = False
                    fx.access.discard(r["access"])
                time.sleep(0.05)  # 让并发的刷新真有机会撞上
                self._send(200, fx.issue())
                return
            self._send(400, {"error": "unsupported_grant_type"})

        def _responses(self, body: dict) -> None:
            tools = [t for t in body.get("tools") or [] if isinstance(t, dict)]
            kinds = {t.get("type") for t in tools}
            if "x_search" in kinds:
                text = "On X, people are arguing about the fictional ferry timetable."
                cites = [{"type": "url_citation", "url": "https://x.com/example_user/status/1", "title": "A post"}] if fx.x_cites else []
                top = ["https://x.com/example_user/status/1", "https://x.com/other_user/status/2"] if fx.x_cites else []
                self._send(200, {"id": "resp_x", "status": "completed", "model": body.get("model"), "citations": top,
                                 "output": [{"type": "x_search_call"}, {"type": "message", "role": "assistant", "content": [
                                     {"type": "output_text", "text": text, "annotations": cites}]}]})
                return
            if "web_search" in kinds:
                cites = [{"type": "url_citation", "url": "https://shop.example/hours", "title": "Opening hours"}]
                self._send(200, {"id": "resp_w", "status": "completed", "model": body.get("model"),
                                 "output": [{"type": "web_search_call"}, {"type": "message", "role": "assistant", "content": [
                                     {"type": "output_text", "text": "Grok says the shop opens at 9.", "annotations": cites}]}]})
                return
            self._send(200, {"id": f"resp_{secrets.token_hex(4)}", "object": "response", "status": "completed", "model": body.get("model"),
                             "output": [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": fx.chat_text}]}],
                             "usage": {"input_tokens": 20, "output_tokens": 7}})

    return Handler
