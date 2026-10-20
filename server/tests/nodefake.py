"""假的节点客户端（第 8 批的测试用）：经 ASGI 直接打 main.app（和测试同一个事件循环，不开端口），hello、长轮询、照 handlers 回结果。
也拿它当假的手机节点（用主人设备的令牌、报手机那三样、切后台发 bye）。不连真的设备。"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import httpx


class Fail(Exception):
    """handler 抛它 = 节点回 ok: false（code、message）。"""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message)
        self.code, self.message = code, message


HANG = object()  # handler 回它 = 拿到活以后一直不回（测超时、断线）


def client() -> httpx.AsyncClient:
    import main
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://testserver", timeout=30)


class FakeNode:
    def __init__(self, http: httpx.AsyncClient, token: str, *, commands: list[str], platform: str = "macos", name: str | None = None,
                 handlers: dict[str, Callable[[dict], Awaitable]] | None = None, permissions: dict | None = None, folders: list | None = None,
                 foreground: bool | None = None, allow: dict | None = None, wait: float = 1.0) -> None:
        self.http, self.token, self.commands, self.platform, self.name = http, token, commands, platform, name
        self.handlers = handlers or {}
        self.permissions, self.folders, self.foreground, self.allow, self.wait = permissions, folders, foreground, allow, wait
        self.got: list[dict] = []        # 拿到的活
        self.cancelled: list[str] = []   # 叫它停的
        self.results: list[dict] = []    # 回过的结果（和服务器的回话）
        self._task: asyncio.Task | None = None
        self._jobs: set[asyncio.Task] = set()

    @property
    def headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"}

    async def hello(self, **extra) -> httpx.Response:
        body = {"platform": self.platform, "commands": self.commands, "version": "test", **extra}
        for k in ("name", "permissions", "folders", "foreground", "allow"):
            if getattr(self, k) is not None and k not in extra:
                body[k] = getattr(self, k)
        return await self.http.post("/api/nodes/hello", json=body, headers=self.headers)

    async def poll(self, wait: float | None = None) -> dict:
        r = await self.http.get("/api/nodes/poll", params={"wait": self.wait if wait is None else wait}, headers=self.headers)
        r.raise_for_status()
        return r.json()

    async def answer(self, inv: dict) -> None:
        fn = self.handlers.get(inv["command"])
        try:
            if fn is None:
                raise Fail("NO_HANDLER", f"no handler for {inv['command']}")
            payload = await fn(inv["params"])
            if payload is HANG:
                await asyncio.sleep(3600)
            body = {"id": inv["id"], "ok": True, "payload": payload}
        except Fail as e:
            body = {"id": inv["id"], "ok": False, "error": {"code": e.code, "message": e.message}}
        r = await self.http.post("/api/nodes/result", json=body, headers=self.headers)
        self.results.append({"status": r.status_code, **body})

    async def serve(self) -> None:
        """一直等活：拿到就开一个 handler 去做，马上再来等（这样干活的时候也算在线）。"""
        while True:
            got = await self.poll()
            self.cancelled += got.get("cancel") or []
            for inv in got.get("invokes") or []:
                self.got.append(inv)
                t = asyncio.ensure_future(self.answer(inv))
                self._jobs.add(t)
                t.add_done_callback(self._jobs.discard)

    async def start(self) -> None:
        r = await self.hello()
        r.raise_for_status()
        self._task = asyncio.ensure_future(self.serve())
        await asyncio.sleep(0.05)  # 让第一次长轮询先挂上

    async def stop(self, bye: str | None = None) -> None:
        """不再来等（像进程没了）；bye 给了就先说一声（background / shutdown）。"""
        for t in [self._task, *self._jobs]:
            if t is not None:
                t.cancel()
        for t in [self._task, *self._jobs]:
            if t is not None:
                try:
                    await t
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
        self._task = None
        if bye:
            await self.http.post("/api/nodes/bye", json={"reason": bye}, headers=self.headers)
