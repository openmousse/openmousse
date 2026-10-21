"""/mcp 的 DNS 重绑定防护（mcp_bridge.py）：Host 不在名单里回 421，Origin 对不上回 403。

名单 = 本机的名字（127.0.0.1、localhost、[::1]，任何端口；bind 写了具体地址再加它）+ share.public_url 的主机
+ mcp.allowed_hosts。不连真的外面的服务：请求打在本进程的 ASGI 上，令牌是测试用的。
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

import mcp_bridge
from config import settings

TOKEN = "tok-mcp-test"
INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "test-client", "version": "0"}}}


def test_allowlist_is_local_until_public_or_extra_hosts_are_set(server_json, monkeypatch):
    monkeypatch.setattr(settings, "host", "127.0.0.1")
    monkeypatch.setattr(settings, "port", 9091)
    server_json({"auth": {"tokens": {"mcp": TOKEN}}})
    hosts, origins = mcp_bridge.allowlist()
    # 本机的名字任何端口都认（和 FastMCP 的默认一样：客户端连的端口不一定是监听的端口）；不带端口的是监听在 80 上
    assert hosts == ["127.0.0.1:*", "127.0.0.1", "localhost:*", "localhost", "[::1]:*", "[::1]"]
    assert origins == ["http://" + h for h in hosts]

    monkeypatch.setattr(settings, "host", "0.0.0.0")  # 通配：客户端不会把 Host 写成 0.0.0.0
    hosts = mcp_bridge.allowlist()[0]
    assert not [h for h in hosts if h.startswith("0.0.0.0")] and "127.0.0.1:*" in hosts

    monkeypatch.setattr(settings, "host", "100.64.1.2")  # 监听在一个具体地址上（虚构的 Tailscale 地址）
    hosts, origins = mcp_bridge.allowlist()
    assert "100.64.1.2:*" in hosts and "http://100.64.1.2:*" in origins

    monkeypatch.setattr(settings, "host", "fd7a:115c::5")  # IPv6：Host 头里带方括号
    assert "[fd7a:115c::5]:*" in mcp_bridge.allowlist()[0]

    server_json({"share": {"public_url": "https://box.example.ts.net"},
                 "mcp": {"allowed_hosts": ["Desk.Example:9091", "https://extra.example/mcp", "https://tls.example:443", "http://bad.example:x"]}})
    hosts, origins = mcp_bridge.allowlist()
    assert "box.example.ts.net" in hosts and "https://box.example.ts.net" in origins
    assert "http://box.example.ts.net" not in origins  # 公网地址按它自己的协议
    assert "desk.example:9091" in hosts and "https://desk.example:9091" in origins and "http://desk.example:9091" in origins  # 按小写比
    assert "extra.example" in hosts and "https://extra.example" in origins
    assert "tls.example" in hosts and "tls.example:443" in hosts  # 写明了默认端口：客户端一般不带，两种都认
    assert not [h for h in hosts if "bad.example" in h]  # 端口写错的那项不认，别的照常


def test_allowed_host_is_200_and_others_are_rejected(server_json, monkeypatch):
    """允许的 Host 回 200；不在名单的回 421；Origin 对得上 200、对不上 403。会话管理器每个实例只能 run 一次：用一个新建的，
    别占掉模块里那个（main 的 lifespan 要用）。"""
    monkeypatch.setattr(settings, "host", "127.0.0.1")
    monkeypatch.setattr(settings, "port", 9091)
    server_json({"auth": {"tokens": {"mcp": TOKEN}}, "share": {"public_url": "https://box.example.ts.net"},
                 "mcp": {"allowed_hosts": ["desk.example:9091"]}})

    fresh = mcp_bridge.build()
    monkeypatch.setattr(mcp_bridge, "server", fresh)
    monkeypatch.setattr(mcp_bridge, "_app", fresh.streamable_http_app())

    async def go() -> dict[str, int]:
        out: dict[str, int] = {}

        async def post(key: str, host: str, origin: str | None = None) -> None:
            headers = {"authorization": f"Bearer {TOKEN}", "content-type": "application/json",
                       "accept": "application/json, text/event-stream", "host": host}
            if origin:
                headers["origin"] = origin
            r = await client.post("/mcp", headers=headers, json=INIT)
            out[key] = r.status_code

        async with fresh.session_manager.run():
            transport = httpx.ASGITransport(app=mcp_bridge.gate)
            async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:9091") as client:
                await post("local", "127.0.0.1:9091")
                await post("localhost", "localhost:9091")
                await post("other-port", "localhost:51234")  # 端口转发过来的：客户端连的不是 9091
                await post("ipv6", "[::1]:9091")
                await post("public", "box.example.ts.net")
                await post("extra", "desk.example:9091")
                await post("local-origin", "127.0.0.1:9091", "http://127.0.0.1:9091")
                await post("public-origin", "box.example.ts.net", "https://box.example.ts.net")
                await post("bad-host", "evil.example")
                await post("bad-origin", "127.0.0.1:9091", "https://evil.example")
                await post("public-http-origin", "box.example.ts.net", "http://box.example.ts.net")
                await post("local-tool-origin", "127.0.0.1:9091", "http://localhost:6274")  # 本机的调试页（比如 MCP Inspector）
                await post("look-alike", "localhost.evil.example:9091")
        return out

    got = asyncio.run(go())
    assert got["local"] == 200 and got["localhost"] == 200 and got["public"] == 200 and got["extra"] == 200
    assert got["other-port"] == 200 and got["ipv6"] == 200 and got["local-tool-origin"] == 200
    assert got["local-origin"] == 200 and got["public-origin"] == 200
    assert got["bad-host"] == 421 and got["look-alike"] == 421
    assert got["bad-origin"] == 403 and got["public-http-origin"] == 403


def test_a_bad_host_on_an_engine_token_is_rejected_too(server_json, monkeypatch):
    """内核发给外援的临时令牌走另一段传输，Host 名单是同一份。"""
    pytest.importorskip("engine.mcp_engine")
    from engine import mcp_engine
    monkeypatch.setattr(settings, "host", "127.0.0.1")
    monkeypatch.setattr(settings, "port", 9091)
    server_json({})

    async def gate(tool, args, ctx):
        return None
    tok = mcp_engine.grant(agent="main", tools=[], gate=gate, conversation="c-sec")  # 工具表空着也行：Host 检查在协议之前

    async def go() -> tuple[int, int]:
        headers = {"authorization": f"Bearer {tok}", "content-type": "application/json",
                   "accept": "application/json, text/event-stream"}

        async def one(host: str) -> int:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=mcp_bridge.gate), base_url="http://127.0.0.1:9091") as client:
                return (await client.post("/mcp", headers={**headers, "host": host}, json=INIT)).status_code

        return await one("127.0.0.1:9091"), await one("evil.example")

    ok, bad = asyncio.run(go())
    assert ok == 200 and bad == 421


def test_the_engines_own_mcp_address_passes_for_every_bind(server_json, monkeypatch):
    """内核给本机 Claude Code / Codex 的 /mcp 地址（runners.mcp_url）：bind 不管怎么写，它带的 Host 都在名单里（照 SDK 的比法）。"""
    pytest.importorskip("engine.runners")
    from urllib.parse import urlsplit

    from engine import runners
    server_json({})
    monkeypatch.setattr(settings, "port", 9091)
    for bind in ("127.0.0.1", "localhost", "", "0.0.0.0", "::", "[::]", "100.64.1.2", "fd7a:115c::5"):
        monkeypatch.setattr(settings, "host", bind)
        host = urlsplit(runners.mcp_url()).netloc
        hosts = mcp_bridge.allowlist()[0]
        assert host in hosts or any(h.endswith(":*") and host.startswith(h[:-2] + ":") for h in hosts), (bind, host)
