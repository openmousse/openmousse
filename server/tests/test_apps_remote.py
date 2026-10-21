"""连接器远端调用的超时和并发（apps.remote）：单次默认 60 秒，同时最多 4 个。
一个挂住的调用超时退出，不占着名额拖住别的。本机起一个假的慢 MCP，不连真服务。
"""
from __future__ import annotations

import asyncio
import socket
import time

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

import apps
from config import settings


def run(coro):
    return asyncio.run(coro)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def fake_mcp() -> FastMCP:
    # 关掉它自己的 DNS 防护：这个假服务只给本测试连，Host 检查会让超时用例变得不好懂
    return FastMCP("slow", host="127.0.0.1", log_level="ERROR", stateless_http=True, json_response=True,
                   transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))


async def serve(mcp: FastMCP):
    import uvicorn
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(mcp.streamable_http_app(), host="127.0.0.1", port=port, log_level="error", access_log=False))
    task = asyncio.create_task(server.serve())
    for _ in range(200):
        if server.started:
            return port, server, task
        await asyncio.sleep(0.01)
    server.should_exit = True
    await task
    raise RuntimeError("fake MCP server did not start")


async def stop(server, task) -> None:
    server.should_exit = True
    await task


def connect(tmp_path, monkeypatch, port: int) -> None:
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    # 每个测试一个新的（同 apps.PARALLEL 的 4 个位子）：排过队的信号量绑在那个测试的事件循环上，后面的测试再等它会报错
    monkeypatch.setattr(apps, "PARALLEL", asyncio.Semaphore(4))
    apps.put({"id": "slow", "name": "Slow", "url": f"http://127.0.0.1:{port}/mcp", "auth": "none", "status": "connected",
              "transport": "http", "agents": ["main"], "tools": []})


def test_one_call_times_out_at_the_configured_limit(tmp_path, monkeypatch):
    assert apps.CALL_TIMEOUT == 60
    monkeypatch.setattr(apps, "CALL_TIMEOUT", 0.4)
    mcp = fake_mcp()

    @mcp.tool()
    async def stall() -> str:
        """Hang until the caller gives up."""
        await asyncio.sleep(8)
        return "too late"

    async def go():
        port, server, task = await serve(mcp)
        connect(tmp_path, monkeypatch, port)
        started = time.monotonic()
        try:
            with pytest.raises(apps.AppError) as caught:
                await apps.call("slow", "stall", {})
            return str(caught.value), time.monotonic() - started
        finally:
            await stop(server, task)

    text, elapsed = run(go())
    assert elapsed < 3, text
    assert "didn't answer within" in text and "0.4" in text


def test_four_calls_run_at_once(tmp_path, monkeypatch):
    mcp = fake_mcp()
    spans: list[tuple[str, float]] = []

    @mcp.tool()
    async def nap(seconds: float) -> str:
        """Sleep, so the test can see whether calls overlap."""
        spans.append(("s", time.monotonic()))
        await asyncio.sleep(seconds)
        spans.append(("e", time.monotonic()))
        return "ok"

    async def go():
        port, server, task = await serve(mcp)
        connect(tmp_path, monkeypatch, port)
        try:
            got = await asyncio.gather(*[apps.call("slow", "nap", {"seconds": 0.4}) for _ in range(4)])
            return got
        finally:
            await stop(server, task)

    got = run(go())
    assert got == [("ok", False)] * 4
    starts = [t for k, t in spans if k == "s"]
    ends = [t for k, t in spans if k == "e"]
    assert len(starts) == 4 and max(starts) < min(ends)  # 四个同时在睡，不是一个接一个


def test_a_hung_call_does_not_hold_up_the_others(tmp_path, monkeypatch):
    monkeypatch.setattr(apps, "CALL_TIMEOUT", 0.4)
    mcp = fake_mcp()

    @mcp.tool()
    async def stall() -> str:
        """Hang until the caller gives up."""
        await asyncio.sleep(8)
        return "too late"

    @mcp.tool()
    async def nap(seconds: float) -> str:
        """A short call that should still finish while others are hung."""
        await asyncio.sleep(seconds)
        return "ok"

    async def hung():
        try:
            await apps.call("slow", "stall", {})
        except apps.AppError as e:
            return str(e)
        return "unexpected-ok"

    async def go():
        port, server, task = await serve(mcp)
        connect(tmp_path, monkeypatch, port)
        started = time.monotonic()
        try:
            results = await asyncio.gather(hung(), hung(), hung(), hung(), apps.call("slow", "nap", {"seconds": 0.2}))
            return results, time.monotonic() - started
        finally:
            await stop(server, task)

    results, elapsed = run(go())
    assert elapsed < 3, results
    assert all(isinstance(r, str) and "didn't answer within" in r for r in results[:4])
    assert results[4] == ("ok", False)
