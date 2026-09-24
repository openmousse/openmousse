"""MCP 服务 + 管理页。只监听 127.0.0.1，公网入口交给 Tailscale Funnel 或反代。

两种接入口，令牌同一套（config.json 的 tokens: token -> 平台名）：
  /t/<token>/mcp                        路径令牌，给 Claude.ai / ChatGPT / Gemini 这类"无认证"连接器
  /m/mcp + Authorization: Bearer <token>  请求头令牌，给 Notion 这类必须带认证的（也认 X-API-Key）
  /ui                                    管理页（看 / 改 / 忘 / 确认 / 编辑档案）。页面本身公开，/ui/api/* 要带 ui_token
                                         （Authorization: Bearer，链接见 mousse-tree urls）。仍不要用 Funnel 暴露它；用 tailscale serve 或 SSH 隧道
  /health
"""
from __future__ import annotations

import contextlib
import secrets
from importlib import resources

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse
from starlette.routing import Mount, Route

from . import config as C
from . import store as S


def instructions(cfg: dict) -> str:
    who = cfg.get("owner_name") or "主人"
    return (
        f"这是 {who} 的个人记忆树，所有 AI 平台共用。对话开始时先调 profile 了解 {who}；"
        f"回答涉及 {who} 的偏好、习惯、近况、决定时先 recall；"
        f"{who} 说出关于自己的新事实、偏好、决定或近况时调 remember，一条一句话，写 {who} 而不是写对话。"
        "不要记临时闲聊、不要记你自己的推测。"
    )


def security(cfg: dict) -> TransportSecuritySettings:
    port = int(cfg.get("port", 8787))
    hosts = [f"127.0.0.1:{port}", f"localhost:{port}", *cfg.get("public_hosts", [])]
    origins = [f"http://{h}" for h in hosts[:2]] + [f"https://{h}" for h in cfg.get("public_hosts", [])]
    return TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=hosts, allowed_origins=origins)


def platform_server(platform: str, cfg: dict) -> FastMCP:
    who = cfg.get("owner_name") or "主人"
    mcp = FastMCP(f"tree-{platform}", instructions=instructions(cfg), stateless_http=True, json_response=True,
                  transport_security=security(cfg))

    @mcp.tool()
    def profile() -> str:
        """主人的基础档案：身份、经历、目标、偏好、沟通方式。对话开始时读一次。"""
        return S.profile_text() or "（档案为空）"

    @mcp.tool()
    def recall(query: str, limit: int = 8) -> str:
        """按关键词在记忆树里找关于主人的记忆。query 用 2 到 4 个关键词，中英文都可以；返回带来源和日期的条目。"""
        conn = S.connect()
        S.sync_profile(conn)
        return S.fmt(S.recall(conn, query, max(1, min(limit, 20))))

    @mcp.tool()
    def remember(text: str, kind: str = "fact", tags: str = "", observed_at: str = "", supersedes: str = "") -> str:
        """把关于主人的一条新信息写进记忆树。text 一句话、第三人称、可独立理解（如"X 早餐改成燕麦加鸡蛋"）。
        kind：fact 事实 / preference 偏好 / decision 决定 / event 近况。tags 逗号分隔。
        observed_at 是这件事的日期（YYYY-MM-DD），不填就是今天。若这条取代了旧记忆，把旧的 id 填进 supersedes。"""
        conn = S.connect()
        status = "pending" if C.load().get("require_confirm") else "active"
        r = S.add(conn, text=text, source=platform, kind=kind, tags=tags, observed_at=observed_at or None,
                  status=status, supersedes=supersedes or None)
        S.export(conn)
        if r["duplicate"]:
            return f"已有相同记忆 [{r['id']}]，未重复写入"
        return f"已记住 [{r['id']}]" + (f"（待 {who} 确认）" if status == "pending" else "")

    @mcp.tool()
    def recent(days: int = 7) -> str:
        """最近几天各平台写进树的记忆，用来了解主人最近在忙什么。"""
        return S.fmt(S.recent(S.connect(), max(1, min(days, 90))))

    @mcp.tool()
    def forget(memory_id: str) -> str:
        """主人明确要求忘记某条记忆时调用（先用 recall 找到 id）。档案条目不能在这里删。"""
        conn = S.connect()
        ok = S.set_status(conn, memory_id, "retracted", platform)
        S.export(conn)
        return "已遗忘" if ok else "没找到这条，或它是档案条目"

    return mcp


# ---------- 管理页接口（只在 loopback / tailnet 用） ----------

def ui_allowed(req: Request) -> bool:
    """/ui/api/* 只认请求头里的管理令牌。浏览器跨站请求带不上这个头，所以也挡住了 CSRF 和 DNS rebinding。"""
    want = C.load().get("ui_token") or ""
    auth = req.headers.get("authorization", "")
    got = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    return bool(want and got) and secrets.compare_digest(want, got)


def ui_api(handler):
    async def guarded(req: Request):
        if not ui_allowed(req):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await handler(req)
    return guarded


async def ui_page(_: Request):
    return HTMLResponse(resources.files("openmousse_tree").joinpath("ui.html").read_text(encoding="utf8"))


async def api_list(req: Request):
    conn = S.connect()
    S.sync_profile(conn)
    q = req.query_params
    rows = S.list_all(conn, status=q.get("status") or None, source=q.get("source") or None)
    return JSONResponse({"items": rows, "stats": S.stats(conn), "require_confirm": bool(C.load().get("require_confirm"))})


async def api_action(req: Request):
    body = await req.json()
    conn = S.connect()
    act, mid = body.get("action"), body.get("id", "")
    ok = False
    if act == "forget":
        ok = S.set_status(conn, mid, "retracted", "owner")
    elif act == "confirm":
        ok = S.set_status(conn, mid, "active", "owner")
    elif act == "edit":
        ok = S.edit(conn, mid, body.get("text", ""), "owner")
    elif act == "add":
        r = S.add(conn, text=body.get("text", ""), source="owner", kind=body.get("kind", "fact"), tags=body.get("tags", ""))
        ok = not r["duplicate"]
    S.export(conn)
    return JSONResponse({"ok": ok})


async def api_profile(req: Request):
    if req.method == "GET":
        return JSONResponse({"text": S.profile_text(), "path": str(S.profile_path())})
    body = await req.json()
    S.save_profile(body.get("text", ""))
    conn = S.connect()
    n = S.sync_profile(conn)
    return JSONResponse({"ok": True, "lines": n})


async def health(_: Request):
    conn = S.connect()
    n = conn.execute("SELECT COUNT(*) FROM tree WHERE status IN ('active','pending')").fetchone()[0]
    return JSONResponse({"ok": True, "memories": n, "platforms": sorted(set(C.load().get("tokens", {}).values()))})


def build_app() -> Starlette:
    cfg = C.load()
    if C.ensure_ui_token(cfg):  # 旧配置没有管理页令牌：补一个，链接用 mousse-tree urls 看
        C.save(cfg)
    tokens: dict[str, str] = cfg.get("tokens", {})
    servers = {tok: platform_server(name, cfg) for tok, name in tokens.items()}
    apps = {tok: srv.streamable_http_app() for tok, srv in servers.items()}

    async def header_auth(scope, receive, send):
        if scope["type"] != "http":
            return await PlainTextResponse("not found", status_code=404)(scope, receive, send)
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        auth = headers.get("authorization", "")
        tok = auth[7:].strip() if auth.lower().startswith("bearer ") else headers.get("x-api-key", "").strip()
        target = apps.get(tok)
        if target is None:
            return await JSONResponse({"error": "unauthorized"}, status_code=401, headers={"WWW-Authenticate": "Bearer"})(scope, receive, send)
        return await target(scope, receive, send)

    async def not_found(_: Request):
        return PlainTextResponse("not found", status_code=404)

    routes = [Mount(f"/t/{tok}", app=a) for tok, a in apps.items()]
    routes += [
        Mount("/m", app=header_auth),
        Route("/ui", ui_page),
        Route("/ui/api/list", ui_api(api_list)),
        Route("/ui/api/action", ui_api(api_action), methods=["POST"]),
        Route("/ui/api/profile", ui_api(api_profile), methods=["GET", "POST"]),
        Route("/health", health),
        Route("/{rest:path}", not_found),
    ]

    @contextlib.asynccontextmanager
    async def lifespan(_: Starlette):
        async with contextlib.AsyncExitStack() as stack:
            for srv in servers.values():
                await stack.enter_async_context(srv.session_manager.run())
            yield

    return Starlette(routes=routes, lifespan=lifespan)


def serve() -> None:
    import uvicorn

    cfg = C.load()
    uvicorn.run(build_app(), host="127.0.0.1", port=int(cfg.get("port", 8787)), log_level="warning")
