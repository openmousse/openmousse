"""假的 Discord：Gateway（WebSocket）和 REST v10，跑在本机一个后台线程里（aiohttp.web），不连网。测试里用它代替 discord.com。

FakeDiscord
  push(t, d)          往 Gateway 推一个事件：连着就马上发；还没有会话就排着，连上（READY 以后）再发；断开期间推的按 seq 记着，RESUME 时补发
  close(code)         让服务器用这个关闭码断开当前连接（4004 令牌不对、4014 intent 不行、4000 一般的断开……）
  reconnect()         发 op 7 RECONNECT；invalid(resumable) 发 op 9 INVALID_SESSION
  identifies / resumes / beats   客户端发来的 IDENTIFY / RESUME / 心跳（看 intents、seq）
  requests            REST：(方法, 路径, JSON)；sent：发出去的消息（POST /channels/{id}/messages 的 JSON，加上 channel_id、id）
  fail(method, path_re, status, body, times)   这个 REST 接口接下来几次回错（429 带 retry_after……）
  dm(user)            给这个用户的私聊频道 id（POST /users/@me/channels 也按它回）
  files               CDN 上的附件：名字 → bytes（附件的 url 写 f"{fake.root}/cdn/<名字>"）
"""
from __future__ import annotations

import asyncio
import itertools
import json
import re
import threading

from aiohttp import WSMsgType, web


class FakeDiscord:
    def __init__(self, token: str = "FAKE0discord0bot0token0001.Gx0001.fake-signature-part-for-tests-0001", bot_id: str = "7100001",
                 username: str = "mousse-test", app_id: str = "7100002", heartbeat_ms: int = 45_000) -> None:
        self.token, self.bot_id, self.username, self.app_id, self.heartbeat_ms = token, bot_id, username, app_id, heartbeat_ms
        self.requests: list[tuple[str, str, object]] = []
        self.sent: list[dict] = []
        self.edits: list[dict] = []
        self.followups: list[dict] = []
        self.callbacks: list[dict] = []
        self.identifies: list[dict] = []
        self.resumes: list[dict] = []
        self.beats: list[object] = []
        self.connections = 0
        self.files: dict[str, bytes] = {}
        self.channels: dict[str, dict] = {}      # GET /channels/{id} 回的（子区要写 type 11、parent_id）
        self.dms: dict[str, str] = {}
        self.app_flags = 1 << 19
        self.resumable = True
        self.close_on_identify: int | None = None
        self._fail: list[dict] = []
        self._ids = itertools.count(600001)
        self._session: str | None = None
        self._log: list[tuple[int, str, dict]] = []
        self._seq = 0
        self._backlog: list[tuple[str, dict]] = []
        self._ws: web.WebSocketResponse | None = None
        self._ready = False
        self._lock = threading.Lock()
        self.loop = asyncio.new_event_loop()
        self.port = 0

    # —— 起停 ——
    def start(self) -> FakeDiscord:
        started = threading.Event()

        def go() -> None:
            asyncio.set_event_loop(self.loop)
            self.loop.run_until_complete(self._serve())
            started.set()
            self.loop.run_forever()
        threading.Thread(target=go, daemon=True, name="fake-discord").start()
        started.wait(10)
        return self

    async def _serve(self) -> None:
        app = web.Application()
        app.router.add_get("/gw/", self._gateway)
        app.router.add_route("*", "/api/v10/{path:.*}", self._rest)
        app.router.add_get("/cdn/{name}", self._cdn)
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]  # noqa: SLF001

    def stop(self) -> None:
        async def down() -> None:
            if self._ws is not None and not self._ws.closed:
                await self._ws.close(code=1001)
            await self._runner.cleanup()
        try:
            asyncio.run_coroutine_threadsafe(down(), self.loop).result(10)
        except Exception:  # noqa: BLE001
            pass
        self.loop.call_soon_threadsafe(self.loop.stop)

    @property
    def root(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def url(self) -> str:
        return f"{self.root}/api/v10"

    # —— 测试用的操作（从测试的线程调）——
    def _run(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(10)

    def push(self, t: str, d: dict) -> None:
        self._run(self._push(t, d))

    def close(self, code: int) -> None:
        async def go() -> None:
            if self._ws is not None and not self._ws.closed:
                await self._ws.close(code=code)
        self._run(go())

    def reconnect(self) -> None:
        self._run(self._send({"op": 7, "d": None}))

    def invalid(self, resumable: bool) -> None:
        self._run(self._send({"op": 9, "d": resumable}))

    def fail(self, method: str, path_re: str, status: int, body: dict | None = None, times: int = 1) -> None:
        with self._lock:
            self._fail.append({"method": method.upper(), "path": re.compile(path_re), "status": status, "body": body or {}, "times": times})

    def dm(self, user: str) -> str:
        return self.dms.setdefault(str(user), f"95{int(user) % 100000:05d}")

    def of(self, method: str, path_re: str) -> list:
        return [b for m, p, b in self.requests if m == method and re.search(path_re, p)]

    def to(self, channel_id: str) -> list[dict]:
        return [m for m in self.sent if m["channel_id"] == str(channel_id)]

    # —— Gateway ——
    async def _send(self, p: dict) -> None:
        if self._ws is not None and not self._ws.closed:
            await self._ws.send_str(json.dumps(p))

    async def _push(self, t: str, d: dict) -> None:
        if self._session is None:
            self._backlog.append((t, d))
            return
        self._seq += 1
        self._log.append((self._seq, t, d))
        if self._ready:
            await self._send({"op": 0, "t": t, "s": self._seq, "d": d})

    async def _gateway(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(autoping=True)
        await ws.prepare(request)
        if self._ws is not None and not self._ws.closed:
            await self._ws.close(code=4000)
        self._ws, self._ready = ws, False
        self.connections += 1
        await ws.send_str(json.dumps({"op": 10, "d": {"heartbeat_interval": self.heartbeat_ms}}))
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            p = json.loads(msg.data)
            op, d = p.get("op"), p.get("d")
            if op == 1:
                self.beats.append(d)
                await ws.send_str(json.dumps({"op": 11}))
            elif op == 2:
                self.identifies.append(d)
                if d.get("token") != self.token:
                    await ws.close(code=4004)
                    break
                if self.close_on_identify:
                    await ws.close(code=self.close_on_identify)
                    break
                await self._identified(ws)
            elif op == 6:
                self.resumes.append(d)
                if not self.resumable or d.get("session_id") != self._session or d.get("token") != self.token:
                    await ws.send_str(json.dumps({"op": 9, "d": False}))
                    continue
                for s, t, ev in self._log:
                    if s > int(d.get("seq") or 0):
                        await ws.send_str(json.dumps({"op": 0, "t": t, "s": s, "d": ev}))
                self._seq += 1
                await ws.send_str(json.dumps({"op": 0, "t": "RESUMED", "s": self._seq, "d": {}}))
                self._ready = True
                await self._flush()
        if self._ws is ws:
            self._ws, self._ready = None, False
        return ws

    async def _identified(self, ws) -> None:
        self._session = f"sess-{next(self._ids)}"
        self._log, self._seq = [], 1
        await ws.send_str(json.dumps({"op": 0, "t": "READY", "s": 1, "d": {
            "v": 10, "session_id": self._session, "resume_gateway_url": f"ws://127.0.0.1:{self.port}/gw",
            "user": {"id": self.bot_id, "username": self.username, "global_name": None, "bot": True},
            "application": {"id": self.app_id, "flags": self.app_flags}, "guilds": []}}))
        self._ready = True
        await self._flush()

    async def _flush(self) -> None:
        backlog, self._backlog = self._backlog, []
        for t, d in backlog:
            await self._push(t, d)

    # —— REST ——
    async def _rest(self, request: web.Request) -> web.Response:
        path = "/" + request.match_info["path"]
        body = await request.json() if request.can_read_body else None
        self.requests.append((request.method, path, body))
        if request.headers.get("Authorization") != f"Bot {self.token}":
            return web.json_response({"message": "401: Unauthorized", "code": 0}, status=401)
        with self._lock:
            for f in self._fail:
                if f["times"] > 0 and f["method"] == request.method and f["path"].search(path):
                    f["times"] -= 1
                    return web.json_response(f["body"], status=f["status"])
        m = request.method
        if m == "GET" and path == "/gateway/bot":
            return web.json_response({"url": f"ws://127.0.0.1:{self.port}/gw", "shards": 1,
                                      "session_start_limit": {"total": 1000, "remaining": 999, "reset_after": 0, "max_concurrency": 1}})
        if m == "GET" and path == "/users/@me":
            return web.json_response({"id": self.bot_id, "username": self.username, "bot": True})
        if m == "GET" and path == "/applications/@me":
            return web.json_response({"id": self.app_id, "flags": self.app_flags})
        if m == "POST" and path == "/users/@me/channels":
            rid = str((body or {}).get("recipient_id") or "")
            if rid in self.channels:  # 不是用户
                return web.json_response({"message": "Invalid Recipient(s)", "code": 50033}, status=400)
            return web.json_response({"id": self.dm(rid), "type": 1, "recipients": [{"id": rid}]})
        if mm := re.fullmatch(r"/channels/(\d+)/messages", path):
            if m == "POST":
                msg = {**(body or {}), "channel_id": mm.group(1), "id": str(next(self._ids))}
                self.sent.append(msg)
                return web.json_response({"id": msg["id"], "channel_id": msg["channel_id"], "content": msg.get("content", "")})
        if mm := re.fullmatch(r"/channels/(\d+)/messages/(\d+)", path):
            if m == "PATCH":
                self.edits.append({**(body or {}), "channel_id": mm.group(1), "id": mm.group(2)})
                return web.json_response({"id": mm.group(2), "channel_id": mm.group(1)})
        if re.fullmatch(r"/channels/\d+/typing", path) and m == "POST":
            return web.Response(status=204)
        if mm := re.fullmatch(r"/channels/(\d+)", path):
            if m == "GET":
                c = self.channels.get(mm.group(1))
                return web.json_response(c) if c else web.json_response({"message": "Unknown Channel", "code": 10003}, status=404)
        if re.fullmatch(r"/interactions/\d+/[^/]+/callback", path) and m == "POST":
            self.callbacks.append({"path": path, **(body or {})})
            return web.Response(status=204)
        if re.fullmatch(r"/webhooks/\d+/[^/]+", path) and m == "POST":
            self.followups.append(body or {})
            return web.json_response({"id": str(next(self._ids))})
        return web.json_response({"message": "404: Not Found", "code": 0}, status=404)

    async def _cdn(self, request: web.Request) -> web.Response:
        name = request.match_info["name"]
        self.requests.append(("GET", f"/cdn/{name}", None))
        if name not in self.files:
            return web.Response(status=404)
        return web.Response(body=self.files[name], content_type="application/octet-stream")
