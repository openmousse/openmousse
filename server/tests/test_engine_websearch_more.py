"""第 3 批第 9 项：搜索多四家（Gemini 的 Google 搜索接地、Grok、Kimi、Firecrawl，engine/web.py）和 web_fetch 交沙箱浏览器渲染只有脚本的壳。
密钥本来就为模型存着的老实例：一个字不变（新的四家要写明 provider，或者开 more 才按 OpenClaw 的顺序参与挑选）；每一家的请求、结果、回答、出处；
报错不带密钥；记次数；web_fetch：开了 render、字太少、这个 Agent 有沙箱浏览器才渲染，不然照旧。搜索后端和网站都是本机的假服务，docker 是假的。"""
from __future__ import annotations

import asyncio
import json

import pytest

import engine
from engine import browser, store, tools, web
from engine.loop import ToolFailure
from test_engine_hands import MainCDP, fd, on  # noqa: F401 — 假 docker、假浏览器

KEY = "sk-fake-search-0123456789abcdef"
ENVS = ("GEMINI_API_KEY", "XAI_API_KEY", "MOONSHOT_API_KEY", "KIMI_API_KEY", "FIRECRAWL_API_KEY", "BRAVE_API_KEY", "TAVILY_API_KEY",
        "EXA_API_KEY", "PERPLEXITY_API_KEY", "SEARXNG_URL")
PUBLIC_IP = "93.184.216.34"


def run(coro):
    return asyncio.run(coro)


def ctx(agent: str = "main") -> engine.ToolCtx:
    return engine.ToolCtx(turn="t", kind="owner", agent=agent, conversation=agent, call_id="c1", control=engine.Control())


def tool(name: str, agent: str = "main") -> engine.Tool | None:
    return next((t for t in tools.for_agent(agent) if t.name == name), None)


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for k in ENVS:
        monkeypatch.delenv(k, raising=False)
    web._cache.clear()
    yield
    web._cache.clear()


def test_old_instances_with_model_keys_get_no_new_search(server_json, monkeypatch):
    for k in ("GEMINI_API_KEY", "XAI_API_KEY", "MOONSHOT_API_KEY", "FIRECRAWL_API_KEY"):
        monkeypatch.setenv(k, KEY)
    server_json({})
    assert web.provider() is None and tool("web_search") is None  # 模型的密钥在，搜索照旧没有
    server_json({"engine": {"web": {"search": {"more": True}}}})
    assert web.provider() == "gemini"  # 开了 more：照 OpenClaw 的顺序（gemini 20 → grok 30 → kimi 40 → perplexity 50 → firecrawl 60 → exa 65……）
    monkeypatch.delenv("GEMINI_API_KEY")
    assert web.provider() == "grok"
    monkeypatch.delenv("XAI_API_KEY")
    assert web.provider() == "kimi"
    monkeypatch.setenv("PERPLEXITY_API_KEY", KEY)
    monkeypatch.delenv("MOONSHOT_API_KEY")
    assert web.provider() == "perplexity"
    monkeypatch.setenv("BRAVE_API_KEY", KEY)
    assert web.provider() == "brave"  # brave 10 照旧在最前
    server_json({"engine": {"web": {"search": {"provider": "firecrawl"}}}})
    assert web.provider() == "firecrawl"  # 写明了就用它
    monkeypatch.delenv("MOONSHOT_API_KEY", raising=False)
    monkeypatch.setenv("KIMI_API_KEY", KEY)
    server_json({"engine": {"web": {"search": {"provider": "kimi"}}}})
    assert web.provider() == "kimi"  # Kimi 两个变量名都认


@pytest.fixture
def backend(server_json, monkeypatch):
    """假的四家：/gemini/models/<型号>:generateContent、/grok、/kimi、/firecrawl。"""
    from aiohttp import web as aw
    seen: list[tuple[str, dict, dict]] = []

    async def gemini(request):
        body = await request.json()
        seen.append(("gemini", dict(request.headers), body))
        if request.headers.get("x-goog-api-key") != KEY:
            return aw.json_response({"error": {"code": 400, "message": "API key not valid"}}, status=400)
        return aw.json_response({"candidates": [{"content": {"parts": [{"text": "The fictional festival is on 12 May."}]},
                                                 "groundingMetadata": {"groundingChunks": [{"web": {"uri": "https://news.example/fest", "title": "Festival news"}},
                                                                                          {"web": {"uri": "https://city.example/events", "title": "City events"}}]}}]})

    async def grok(request):
        body = await request.json()
        seen.append(("grok", dict(request.headers), body))
        return aw.json_response({"output": [{"type": "web_search_call"}, {"type": "message", "content": [
            {"type": "output_text", "text": "Grok says the shop opens at 9.", "annotations": [
                {"type": "url_citation", "url": "https://shop.example/hours", "title": "Opening hours"}]}]}]})

    async def kimi(request):
        body = await request.json()
        seen.append(("kimi", dict(request.headers), body))
        return aw.json_response({"search_results": [{"title": "Kimi hit", "url": "https://kimi.example/a", "snippet": "A fictional snippet.",
                                                     "date": "2026-10-01"}]})

    async def firecrawl(request):
        body = await request.json()
        seen.append(("firecrawl", dict(request.headers), body))
        return aw.json_response({"success": True, "data": {"web": [{"url": "https://fire.example/x", "title": "Fire hit", "description": "Hot."}]}})

    app = aw.Application()
    app.router.add_post("/gemini/models/{model}:generateContent", gemini)
    app.router.add_post("/grok", grok)
    app.router.add_post("/kimi", kimi)
    app.router.add_post("/firecrawl", firecrawl)
    holder: dict = {"seen": seen}

    async def start():
        runner = aw.AppRunner(app)
        await runner.setup()
        s = aw.TCPSite(runner, "127.0.0.1", 0)
        await s.start()
        holder["runner"] = runner
        port = s._server.sockets[0].getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        holder["urls"] = {"gemini": {"url": base + "/gemini"}, "grok": {"url": base + "/grok"}, "kimi": {"url": base + "/kimi"},
                          "firecrawl": {"url": base + "/firecrawl"}}
    holder["start"] = start
    for k in ("GEMINI_API_KEY", "XAI_API_KEY", "MOONSHOT_API_KEY", "FIRECRAWL_API_KEY"):
        monkeypatch.setenv(k, KEY)
    return holder


def rows() -> list[dict]:
    with store.read() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM engine_media_usage WHERE purpose='search' ORDER BY id")]


def test_each_new_backend(backend, server_json):
    async def go():
        await backend["start"]()
        out = {}
        try:
            for name in ("gemini", "grok", "kimi", "firecrawl"):
                server_json({"engine": {"web": {"search": {"provider": name, "providers": backend["urls"]}}}})
                out[name] = await tool("web_search").run({"query": "fictional festival date", "count": 3}, ctx())
            return out
        finally:
            await backend["runner"].cleanup()
    before = len(rows()) if store.exists() else 0
    out = run(go())
    assert "Answer: The fictional festival is on 12 May." in out["gemini"] and "https://news.example/fest" in out["gemini"] and "Sources:" in out["gemini"]
    assert "Answer: Grok says the shop opens at 9." in out["grok"] and "https://shop.example/hours" in out["grok"]
    assert "Kimi hit" in out["kimi"] and "2026-10-01" in out["kimi"] and "Fire hit" in out["firecrawl"]
    assert all("<untrusted_tool_result" in v for v in out.values())  # 结果照旧包成资料
    seen = {n: (h, b) for n, h, b in backend["seen"]}
    assert seen["gemini"][1]["tools"] == [{"google_search": {}}] and seen["gemini"][1]["contents"][0]["parts"][0]["text"] == "fictional festival date"
    assert seen["grok"][1]["tools"] == [{"type": "web_search"}] and seen["grok"][1]["model"] == "grok-4.7" and seen["grok"][1]["store"] is False
    assert seen["grok"][0]["Authorization"] == f"Bearer {KEY}"
    assert seen["kimi"][1] == {"text_query": "fictional festival date", "limit": 3} and seen["firecrawl"][1] == {"query": "fictional festival date", "limit": 3}
    got = rows()[before:]
    assert [r["provider"] for r in got] == ["gemini", "grok", "kimi", "firecrawl"] and all(r["cost"] is None and r["unit"] == "query" for r in got)


def test_errors_never_carry_the_key(backend, server_json, monkeypatch):
    wrong = "sk-wrong-search-9876543210zyxw"
    monkeypatch.setenv("GEMINI_API_KEY", wrong)

    async def go():
        await backend["start"]()
        try:
            server_json({"engine": {"web": {"search": {"provider": "gemini", "providers": backend["urls"]}}}})
            with pytest.raises(ToolFailure) as e:
                await tool("web_search").run({"query": "x"}, ctx())
            return str(e.value)
        finally:
            await backend["runner"].cleanup()
    msg = run(go())
    assert "HTTP 400" in msg and wrong not in msg


# —— web_fetch：只有脚本的壳 ——————————————————————————————————————————————————————————

SHELL = "<html><head><script src='/app.js'></script></head><body><div id='root'></div><noscript>Enable JavaScript</noscript></body></html>"


class RenderCDP(MainCDP):
    def answer(self, m: dict) -> dict:
        if m["method"] == "Runtime.evaluate" and "innerText" in m["params"]["expression"]:
            return {"result": {"value": "Rendered fictional article: the garden club meets on Tuesdays at the library hall." * 3}}
        return super().answer(m)


def test_web_fetch_renders_script_shells_in_the_sandbox_browser_when_switched_on(on, fd, server_json, monkeypatch):  # noqa: F811 — 导入的 fixture
    from aiohttp import web as aw
    holder: dict = {}

    async def shell(request):
        return aw.Response(text=SHELL, content_type="text/html")

    async def resolve(host: str, port: int) -> list[str]:
        return [PUBLIC_IP] if host == "spa.example" else [host]

    def dial(ip: str, port: int) -> tuple[str, int]:
        return "127.0.0.1", holder["site"]
    monkeypatch.setattr(web, "resolve_host", resolve)
    monkeypatch.setattr(web, "dial", dial)
    monkeypatch.setattr(web, "proxied", lambda: False)
    fake = RenderCDP(fd)
    fd.ip = "127.0.0.1"

    async def go():
        site = aw.Application()
        site.router.add_get("/app", shell)
        cdp = aw.Application()
        cdp.router.add_get("/json/version", fake.version)
        cdp.router.add_get("/devtools/browser/abc", fake.ws)
        runners = []
        for app, key in ((site, "site"), (cdp, "cdp")):
            r = aw.AppRunner(app)
            await r.setup()
            s = aw.TCPSite(r, "127.0.0.1", 0)
            await s.start()
            runners.append(r)
            holder[key] = s._server.sockets[0].getsockname()[1]
        monkeypatch.setattr(browser, "CDP_PORT", holder["cdp"])
        try:
            on({"main": {"access": "none"}})
            plain = await tool("web_fetch").run({"url": "http://spa.example/app"}, ctx())
            web._cache.clear()
            server_json({"engine": {"sandbox": {"agents": {"main": {"access": "none"}}}, "web": {"fetch": {"render": True}}}})
            rendered = await tool("web_fetch").run({"url": "http://spa.example/app"}, ctx())
            web._cache.clear()
            server_json({"engine": {"sandbox": {"agents": {"main": False}}, "web": {"fetch": {"render": True}}}})
            no_box = await tool("web_fetch").run({"url": "http://spa.example/app"}, ctx())
            return plain, rendered, no_box
        finally:
            await browser.close_all()
            for r in runners:
                await r.cleanup()
    plain, rendered, no_box = run(go())
    assert "Rendered fictional article" not in plain  # 没开：照旧
    assert "rendered in your sandbox browser" in rendered and "Rendered fictional article" in rendered and "<untrusted_tool_result" in rendered
    assert "Rendered fictional article" not in no_box  # 没有沙箱浏览器：照旧
    navs = [json.dumps(c["params"]) for c in fake.cmds if c["method"] == "Page.navigate"]
    assert navs and "spa.example/app" in navs[0]
