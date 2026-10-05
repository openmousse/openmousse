"""经 Gateway 的 WebSocket 对话通道发消息（2026-09-28）。server.json 的 chat.transport = "ws" 时 chat.py 走这里，默认 "http"
（OpenAI 兼容的 /v1/chat/completions）。和 HTTP 比：
- 回复进行中再发能「插话」：chat.send 带 queueMode steer，Gateway 在这一轮的下一步把它交给模型，还是同一条回复；
- 停止用 chat.abort；
- 一轮由 Gateway 跑到底：这边的连接断了、服务重启了都不掐断，重连以后按 runId 接着收，或者从 chat.history 补回回复。

连接
- 设备身份：Ed25519 密钥存 <data_dir>/gateway-device.json（0600，只有私钥）；device.id = 公钥原始字节的 sha256。
  第一次在本机回环地址连上时 Gateway 自动配对，拿到 operator.read / operator.write。
- 客户端身份 webchat / webchat（和 app 以前经 HTTP 进来是同一个渠道，Gateway 按它定排队方式），Origin 设成 Gateway 自己的地址
  （webchat 身份要求 Origin，否则 CONTROL_UI_ORIGIN_NOT_ALLOWED）。
- 握手：Gateway 先发 connect.challenge {nonce, ts}；签 v3 载荷
  `v3|deviceId|clientId|clientMode|role|scopes|signedAtMs|token|nonce|platform|deviceFamily`（openclaw 的 device-auth.ts），发 connect，回 hello-ok。
- 帧：请求 {type: req, id, method, params} → 回应 {type: res, id, ok, payload | error}；事件 {type: event, event, payload}。
- chat 事件 {runId, sessionKey, seq, state: status | delta | final | aborted | error}：delta 带 deltaText（replace = 整段换掉），
  final / aborted 带 message（content 数组）。要收到某个会话的事件先 sessions.messages.subscribe {key}，断线重连后重新订阅。
  （实测 2026-09-28：chat 事件对 operator 连接是广播的，没订阅的会话也收得到，所以 Telegram、别的 Agent 的轮次也会经过这里。）
- agent 事件（同一连接、订阅了会话就有）：只转 run_status（准备阶段）、item（工具步骤的标题和状态）、thinking（思考摘要），
  进这一轮的队列时 state = "agent"，chat.py 拼成给 app 的 progress（2026-10-05）。
- 没人认领的一轮第一次出事件时问 ADOPT（settle.py 挂上：后台任务做完后派它的会话里那一轮 announce:…，是 app 派的就接过去）。
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import time
import uuid
from pathlib import Path
from typing import Any, AsyncIterator, Callable

import aiohttp
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from config import settings

CLIENT = {"id": "webchat", "version": "openmousse-server", "platform": "linux", "mode": "webchat"}
ROLE, SCOPES = "operator", ["operator.read", "operator.write"]
PROTOCOL = 4
PROGRESS_STREAMS = ("run_status", "item", "thinking")  # agent 事件里转给 app 看进度的（chat.py 的 note_progress）
BUFFER_SECONDS = 30  # 还没人认领的 runId 的事件留多久（chat.send 的回应和它第一批事件谁先到说不准）
# 没人认领的一轮第一次出事件时调它（同步）：(payload) → True = 它已经 watch 了这个 runId，这个事件和之后的都进那个队列
ADOPT: Callable[[dict], bool] | None = None


class GatewayError(Exception):
    def __init__(self, message: str, code: str = "", details: Any = None):
        super().__init__(message)
        self.code, self.details = code, details


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def message_text(message: Any) -> str:
    """chat 事件 / chat.history 里一条消息的文字（content 可能是字符串，也可能是 [{type: text, text}, …]）。"""
    if not isinstance(message, dict):
        return ""
    c = message.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text")
    return ""


class GatewayWS:
    def __init__(self, url: str, token: str, identity: Path):
        self.url = url.replace("http://", "ws://").replace("https://", "wss://").rstrip("/")
        self.origin = url.rstrip("/")
        self.token = token
        self.identity = identity
        self.session: aiohttp.ClientSession | None = None
        self.ws: aiohttp.ClientWebSocketResponse | None = None
        self.reader: asyncio.Task | None = None
        self.lock = asyncio.Lock()
        self.n = 0
        self.pending: dict[str, asyncio.Future] = {}
        self.runs: dict[str, asyncio.Queue] = {}  # runId → 这一轮的 chat 事件
        self.early: dict[str, list[tuple[float, dict]]] = {}  # 还没人认领的 runId 的事件
        self.asked: dict[str, float] = {}  # 问过 ADOPT 的 runId（每个只问一次）→ 问的时间
        self.pre: dict[str, list[tuple[float, dict]]] = {}  # 还没人认领的 runId 的进度事件（progress），认领时补进队列
        self.subscribed: set[str] = set()
        self.models: dict[str, str] = {}  # 会话 → 现在用的模型（provider/model），发之前对一下

    # —— 连接 ——————————————————————————————————————————————

    def key(self) -> Ed25519PrivateKey:
        if self.identity.exists():
            raw = base64.urlsafe_b64decode(json.loads(self.identity.read_text())["privateKey"] + "==")
            return Ed25519PrivateKey.from_private_bytes(raw)
        k = Ed25519PrivateKey.generate()
        raw = k.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
        self.identity.parent.mkdir(parents=True, exist_ok=True)
        self.identity.write_text(json.dumps({"privateKey": b64u(raw)}))
        self.identity.chmod(0o600)
        return k

    @property
    def connected(self) -> bool:
        return self.ws is not None and not self.ws.closed

    async def ensure(self) -> None:
        if self.connected:
            return
        async with self.lock:
            if not self.connected:
                await self.connect()

    async def connect(self) -> None:
        key = self.key()
        pub = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        device_id = hashlib.sha256(pub).hexdigest()
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
        self.ws = await self.session.ws_connect(self.url, origin=self.origin, max_msg_size=32 * 1024 * 1024, heartbeat=30)
        first = await self.ws.receive_json(timeout=10)
        if first.get("event") != "connect.challenge":
            raise GatewayError(f"没收到 connect.challenge：{str(first)[:120]}")
        nonce, ts = first["payload"]["nonce"], first["payload"]["ts"]
        payload = "|".join(["v3", device_id, CLIENT["id"], CLIENT["mode"], ROLE, ",".join(SCOPES), str(ts), self.token, nonce, CLIENT["platform"].lower(), ""])
        rid = self.next_id()
        await self.ws.send_json({"type": "req", "id": rid, "method": "connect", "params": {
            "minProtocol": PROTOCOL, "maxProtocol": PROTOCOL, "client": CLIENT, "role": ROLE, "scopes": SCOPES, "caps": [], "commands": [],
            "permissions": {}, "auth": {"token": self.token}, "locale": "zh-CN", "userAgent": "openmousse-server",
            "device": {"id": device_id, "publicKey": b64u(pub), "signature": b64u(key.sign(payload.encode())), "signedAt": ts, "nonce": nonce}}})
        while True:  # hello-ok 之前可能先来几个事件
            msg = await self.ws.receive_json(timeout=15)
            if msg.get("type") == "res" and msg.get("id") == rid:
                break
        if not msg.get("ok"):
            err = msg.get("error") or {}
            await self.ws.close()
            raise GatewayError(err.get("message") or "connect failed", err.get("code", ""), err.get("details"))
        self.reader = asyncio.create_task(self.read())
        again, self.subscribed = self.subscribed, set()
        for k in again:  # 断线重连：原来订阅的会话重新订阅
            try:
                await self.subscribe(k)
            except GatewayError:
                pass

    def next_id(self) -> str:
        self.n += 1
        return f"om{self.n}"

    async def read(self) -> None:
        ws = self.ws
        try:
            async for m in ws:
                if m.type != aiohttp.WSMsgType.TEXT:
                    continue
                try:
                    j = json.loads(m.data)
                except ValueError:
                    continue
                if j.get("type") == "res":
                    fut = self.pending.pop(str(j.get("id")), None)
                    if fut and not fut.done():
                        fut.set_result(j)
                elif j.get("type") == "event" and j.get("event") == "chat":
                    self.route(j.get("payload") or {})
                elif j.get("type") == "event" and j.get("event") == "agent":
                    self.progress(j.get("payload") or {})
        except Exception:  # noqa: BLE001 — 连接断了：下面统一收尾
            pass
        finally:
            for fut in self.pending.values():
                if not fut.done():
                    fut.set_exception(GatewayError("Gateway 连接断了"))
            self.pending.clear()
            for q in self.runs.values():  # 在等事件的：告诉它们断线了（它们会重连后按 runId 接着收，或者去 chat.history 补）
                q.put_nowait({"state": "disconnected"})

    def route(self, p: dict) -> None:
        rid = str(p.get("runId") or "")
        if not rid:
            return
        q = self.runs.get(rid)
        now = time.time()
        if q is None and ADOPT is not None and rid not in self.asked and rid not in self.early:
            self.asked[rid] = now
            if len(self.asked) > 2000:
                self.asked = {k: t for k, t in self.asked.items() if now - t < 3600}
            try:
                if ADOPT(p):
                    q = self.runs.get(rid)
            except Exception:  # noqa: BLE001 — 钩子出错：当没人认领
                pass
        if q is not None:
            q.put_nowait(p)
            return
        self.early.setdefault(rid, []).append((now, p))
        for k in [k for k, v in self.early.items() if v and now - v[-1][0] > BUFFER_SECONDS]:
            self.early.pop(k, None)

    def progress(self, p: dict) -> None:
        """agent 事件里给人看进度的三种（准备阶段、工具步骤、思考摘要）：放进这一轮的队列，state 记成 "agent"。
        命令参数和输出（tool / command_output）不转。还没人认领的 runId 先攒着（chat.send 的回应可能比它晚到），不问 ADOPT。"""
        rid = str(p.get("runId") or "")
        if not rid or p.get("stream") not in PROGRESS_STREAMS or p.get("isHeartbeat"):
            return
        item = {"runId": rid, "state": "agent", "stream": p.get("stream"), "data": p.get("data") or {}}
        q = self.runs.get(rid)
        if q is not None:
            q.put_nowait(item)
            return
        now = time.time()
        if rid not in self.asked:  # 问过 ADOPT 又没人要的（Telegram、定时任务的轮次）不攒；和 early 分开放，不影响 route 问 ADOPT
            self.pre.setdefault(rid, []).append((now, item))
        for k in [k for k, v in self.pre.items() if v and now - v[-1][0] > BUFFER_SECONDS]:
            self.pre.pop(k, None)

    async def call(self, method: str, params: dict, timeout: float = 30) -> dict:
        await self.ensure()
        rid = self.next_id()
        fut = asyncio.get_running_loop().create_future()
        self.pending[rid] = fut
        await self.ws.send_json({"type": "req", "id": rid, "method": method, "params": params})
        try:
            res = await asyncio.wait_for(fut, timeout)
        finally:
            self.pending.pop(rid, None)
        if not res.get("ok"):
            err = res.get("error") or {}
            raise GatewayError(err.get("message") or f"{method} failed", err.get("code", ""), err.get("details"))
        return res.get("payload") or {}

    async def subscribe(self, key: str) -> None:
        if key in self.subscribed and self.connected:
            return
        await self.call("sessions.messages.subscribe", {"key": key})
        self.subscribed.add(key)

    # —— 一轮 ———————————————————————————————————————————————

    def watch(self, run_id: str) -> asyncio.Queue:
        """认领一个 runId：之后它的 chat 事件进这个队列（认领前已经到的也补进去）。"""
        q = self.runs.setdefault(run_id, asyncio.Queue())
        for _, p in sorted(self.pre.pop(run_id, []) + self.early.pop(run_id, []), key=lambda x: x[0]):
            q.put_nowait(p)
        return q

    def unwatch(self, run_id: str) -> None:
        self.runs.pop(run_id, None)

    def requeue(self, run_id: str, p: dict) -> None:
        """把已经读出来的一个事件放回去（交给接下来认领这个 runId 的人）。"""
        self.early.setdefault(run_id, []).insert(0, (time.time(), p))

    async def events(self, run_id: str, idle: float = 900) -> AsyncIterator[dict]:
        """这一轮的 chat 事件，到 final / aborted / error 为止。连接断了自动重连、重新订阅，接着收；
        重连后这一轮已经结束了（事件错过了）就产出一个 {"state": "lost"}，调用方去 chat.history 补。"""
        q = self.watch(run_id)
        try:
            while True:
                try:
                    p = await asyncio.wait_for(q.get(), idle)
                except asyncio.TimeoutError:
                    yield {"state": "lost"}
                    return
                if p.get("state") == "disconnected":
                    await self.reconnect_soon()
                    yield {"state": "lost"}  # 断线期间可能已经回完了：让调用方查一下，没回完的它会接着 watch
                    return
                yield p
                if p.get("state") in ("final", "aborted", "error"):
                    return
        finally:
            self.unwatch(run_id)

    async def reconnect_soon(self) -> None:
        for delay in (0.5, 1, 2, 4, 8):
            try:
                await self.ensure()
                return
            except Exception:  # noqa: BLE001
                await asyncio.sleep(delay)

    async def ensure_model(self, key: str, model: str | None) -> None:
        """chat.send 不能按条指定模型：会话现在用的和要的不一样，就先把会话的模型改掉（sessions.patch）。"""
        if not model:
            return
        cur = self.models.get(key)
        if cur is None:
            try:
                s = (await self.call("sessions.describe", {"key": key})).get("session") or {}
                cur = f"{s.get('modelProvider')}/{s.get('model')}" if s.get("modelProvider") and s.get("model") else ""
            except GatewayError:
                cur = ""  # 新会话还没有：按要的设一次
        if cur != model:
            await self.call("sessions.patch", {"key": key, "model": model})
        self.models[key] = model

    async def send(self, key: str, text: str, queue_mode: str | None = None, model: str | None = None) -> str:
        """发一条，返回 runId。queue_mode：steer 插话（这个会话正在回我们的上一条）/ followup（其余，别插进 Telegram 那边正在跑的一轮）。"""
        await self.subscribe(key)
        await self.ensure_model(key, model)
        params = {"sessionKey": key, "message": text, "idempotencyKey": uuid.uuid4().hex}
        if queue_mode:
            params["queueMode"] = queue_mode
        res = await self.call("chat.send", params, timeout=60)
        return str(res.get("runId") or "")

    async def abort(self, key: str, run_id: str) -> None:
        await self.call("chat.abort", {"sessionKey": key, "runId": run_id})

    async def history(self, key: str, limit: int = 20) -> dict:
        return await self.call("chat.history", {"sessionKey": key, "limit": limit})


_client: GatewayWS | None = None


def client() -> GatewayWS:
    global _client
    if _client is None:
        import chat  # 延迟导入：chat.py 依赖本模块
        _client = GatewayWS(settings.gateway, chat.gateway_token(), settings.data_dir / "gateway-device.json")
    return _client
