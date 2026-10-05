"""中继（2026-10-05）：没有公网入口的服务器，朋友也找得到。

为什么：朋友之间（/f）要两边的服务器都能从外网连进来。以前只能靠 Tailscale Funnel 或自己的域名，安装器里「开公网」默认不开，
开了也常卡在 tailnet 后台授权、macOS 上开不成，于是新用户默认加不了朋友。

怎么做：服务器自己往外连中继（默认 https://relay.openmousse.ai，代码在仓库的 relay/），用社交那把 Ed25519 钥匙证明身份，
保持一条 WebSocket。中继给它一个地址 https://relay.openmousse.ai/u/<kid>；朋友的服务器往这个地址发的请求（只有 /f/ 下面的），
中继经这条连接转过来，这里交给对外小服务（public.py 的 app，进程内调用，不需要它监听端口），回应再送回去。
- 隐私：发给新版服务器的消息、握手、A2A 都是端到端信封（social.py 的 /f/sealed），中继只看得到密文、收件人和时间。
  名片、公钥本来就是公开的。中继不存任何东西：这边不在线时它回 503，发件那边按原来的规矩重试（最多 3 天）。
- 什么时候用：share.public_url 能用就直连（Funnel 没开 /f 的不算能用），否则用中继；直连的名片上也写一个 relay 备用地址。
  连上过中继才把它的地址写进名片（中继没部署、连不上时不冒充能加朋友）。
- 关掉：server.json 写 "relay": false；换一个中继：{"relay": {"url": "https://…"}}。

线上的帧（JSON 文本帧）：中继先发 {"t": "challenge", "nonce"}，这边回 {"t": "hello", "v": 1, "key": <JWK>, "sig": 签名(
"openmousse-relay/1|<kid>|<nonce>")}，中继回 {"t": "ok"}。之后中继发 {"t": "req", "id", "m", "p"（含 /u/<kid> 的完整路径）, "q", "h", "b"
（base64url）}，这边回 {"t": "res", "id", "s", "h", "b"}。
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import shutil
import subprocess
import time
from urllib.parse import urlsplit

import aiohttp
import httpx

from config import raw

log = logging.getLogger("relay")

DEFAULT_URL = "https://relay.openmousse.ai"
PROTO = "openmousse-relay/1"
SEEN_KEEP = 7 * 86400   # 连上过中继，这么久之内都算有中继地址（短暂断线不改名片）
REQ_TIMEOUT = 110       # 一个转过来的请求最多处理多久（A2A 等对方名片 agent 想一想要到 90 秒）
FORWARD_HEADERS = ("content-type", "content-digest", "mousse-to", "signature-input", "signature", "accept", "a2a-version",
                   "a2a-extensions", "x-a2a-notification-token", "authorization", "user-agent")
MAC_TAILSCALE = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"

state: dict = {"connected": False, "since": None, "error": None, "seen": None}
_funnel: dict = {"url": None, "at": 0.0, "off": False}
_task: asyncio.Task | None = None


# —— 配置 ——

def cfg() -> dict | None:
    """None = 关了（server.json "relay": false 或 {"enabled": false}）。"""
    c = raw().get("relay")
    if c is False or (isinstance(c, dict) and c.get("enabled") is False):
        return None
    return c if isinstance(c, dict) else {}


def base_url() -> str | None:
    c = cfg()
    if c is None:
        return None
    return str(c.get("url") or DEFAULT_URL).rstrip("/")


def _seen_recently() -> bool:
    if state["connected"]:
        return True
    if state["seen"] is None:  # 这个进程还没连过：读一次库里记的上次连上的时间（命令行工具、刚重启的服务）
        import social
        try:
            state["seen"] = float(social.get_setting("relay_seen") or 0)
        except ValueError:
            state["seen"] = 0.0
    return time.time() - float(state["seen"] or 0) < SEEN_KEEP


def my_base() -> str | None:
    """中继给我的根地址；关了或者从没连上过就是 None。"""
    b = base_url()
    if not b or not _seen_recently():
        return None
    import social
    return f"{b}/u/{social.my_kid()}"


# —— 直连还能不能用 ——

def direct_broken(url: str) -> bool:
    """对外地址是 Tailscale 的 <机器>.ts.net，但 Funnel 没把 /f 开到公网：朋友的服务器连不进来（2026-10-05 第一个朋友就是这样，
    他发来的到了，回他的一直送不到）。看不出来（没有 tailscale 命令、读不了状态、用的是自己的域名）一律当能用。结果留 5 分钟。"""
    host = (urlsplit(url).hostname or "").lower()
    if not host.endswith(".ts.net"):
        return False
    now = time.time()
    if _funnel["url"] == url and now - _funnel["at"] < 300:
        return bool(_funnel["off"])
    exe = shutil.which("tailscale") or (MAC_TAILSCALE if os.path.exists(MAC_TAILSCALE) else None)
    off = False
    if exe:
        try:
            r = subprocess.run([exe, "funnel", "status", "--json"], capture_output=True, text=True, timeout=5)
            d = json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else None
        except (OSError, subprocess.TimeoutExpired, ValueError):
            d = None
        if isinstance(d, dict):
            hp = f"{host}:{urlsplit(url).port or 443}"
            handlers = ((d.get("Web") or {}).get(hp) or {}).get("Handlers") or {}
            off = not ((d.get("AllowFunnel") or {}).get(hp) and any(str(k).rstrip("/") == "/f" for k in handlers))
    _funnel.update(url=url, at=now, off=off)
    return off


def recheck() -> None:
    """下次问 direct_broken 时重新看 Funnel（朋友的服务器说连不进来了）。"""
    _funnel["at"] = 0.0


# —— 连接 ——

def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


async def handle(ws: aiohttp.ClientWebSocketResponse, send_lock: asyncio.Lock, app, kid: str, d: dict) -> None:
    """中继转过来的一个请求：只收 /u/<kid>/f/… 的 GET / POST，交给对外小服务处理，回应送回去。"""
    rid = d.get("id")
    prefix = f"/u/{kid}"
    status, headers, body = 502, {"content-type": "application/json"}, b'{"ok":false,"error":"relay_handler"}'
    try:
        method, path, query = str(d.get("m") or "GET").upper(), str(d.get("p") or ""), str(d.get("q") or "")
        if method not in ("GET", "POST", "HEAD") or not path.startswith(prefix + "/f/") or ".." in path:
            status, body = 404, b'{"ok":false,"error":"not_found"}'
        else:
            hdrs = {k: str(v) for k, v in (d.get("h") or {}).items() if str(k).lower() in FORWARD_HEADERS}
            transport = httpx.ASGITransport(app=app, root_path=prefix, client=("relay", 0))
            async with httpx.AsyncClient(transport=transport, base_url="http://relay.local", timeout=REQ_TIMEOUT) as c:
                r = await c.request(method, path + query, content=unb64(str(d.get("b") or "")), headers=hdrs)
            status, body = r.status_code, r.content
            headers = {k: v for k, v in (("content-type", r.headers.get("content-type")), ("cache-control", r.headers.get("cache-control"))) if v}
    except Exception:  # noqa: BLE001 — 一个请求出错不能断了整条连接
        log.exception("relay request")
    async with send_lock:
        await ws.send_str(json.dumps({"t": "res", "id": rid, "s": status, "h": headers, "b": b64(body)}, separators=(",", ":")))


async def session(app) -> None:
    import social
    base = base_url()
    if not base:
        return
    kid = social.my_kid()
    ws_url = base.replace("https://", "wss://", 1).replace("http://", "ws://", 1) + f"/c/{kid}"
    async with aiohttp.ClientSession() as s, s.ws_connect(ws_url, heartbeat=25, max_msg_size=4 * 1024 * 1024, timeout=15) as ws:
        first = await ws.receive_json(timeout=15)
        if first.get("t") != "challenge" or not isinstance(first.get("nonce"), str):
            raise RuntimeError("no challenge")
        sig = social.private_key().sign(f"{PROTO}|{kid}|{first['nonce']}".encode())
        await ws.send_json({"t": "hello", "v": 1, "key": social.identity()["jwk"], "sig": b64(sig)})
        ok = await ws.receive_json(timeout=15)
        if ok.get("t") != "ok":
            raise RuntimeError(str(ok.get("error") or "refused"))
        first_time = not _seen_recently()
        state.update(connected=True, since=time.time(), error=None, seen=time.time())
        await asyncio.to_thread(social.set_setting, "relay_seen", str(time.time()))
        if first_time:
            _announce()
        log.info("relay connected: %s/u/%s", base, kid)
        send_lock = asyncio.Lock()
        tasks: set[asyncio.Task] = set()
        async for msg in ws:
            if msg.type != aiohttp.WSMsgType.TEXT:
                continue
            try:
                d = json.loads(msg.data)
            except ValueError:
                continue
            if d.get("t") == "req":
                t = asyncio.create_task(handle(ws, send_lock, app, kid, d))
                tasks.add(t)
                t.add_done_callback(tasks.discard)


def _announce() -> None:
    """第一次连上（名片地址可能刚从「没有」变成中继地址）：叫醒好友投递，名片变了会自己发给朋友。"""
    try:
        import friends
        friends.kick()
    except Exception:  # noqa: BLE001
        pass


async def loop(app) -> None:
    delay = 2.0
    while True:
        if cfg() is None:
            state.update(connected=False)
            await asyncio.sleep(60)
            continue
        started = time.time()
        try:
            await session(app)
            state["error"] = None
        except Exception as e:  # noqa: BLE001 — 连不上、被踢、网络断：等一会儿再连
            state["error"] = f"{type(e).__name__}: {e}"[:200]
        state["connected"] = False
        if time.time() - started > 120:
            delay = 2.0  # 连上过一阵子才断的：很快重连
        await asyncio.sleep(delay)
        delay = min(delay * 2, 120)


def start() -> None:
    """主服务启动时调（main.py lifespan）：在主事件循环上起中继连接。用的是对外小服务的 app（进程内调用，不需要它监听端口）。"""
    global _task
    if _task is not None:
        return
    import public
    _task = asyncio.get_running_loop().create_task(loop(public.app))


def status() -> dict:
    """给 check.sh 和 app 看：开没开、连没连上、地址、上次的错。"""
    b = base_url()
    return {"enabled": b is not None, "url": b, "connected": bool(state["connected"]), "base": my_base(), "error": state["error"]}
