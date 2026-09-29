"""连接器（2026-09-29）：把第三方应用（Notion、Linear……或者任何一个远端 MCP 地址）连到这台服务器，它们的工具经 /mcp 交给你的 claw。

和 connectors.py 不是一回事：那个是只读的「连接」状态页（数据来源、渠道、推送……现在怎么样）；这里是你自己接进来的应用。

- 连：app 里点一下 → POST /api/apps → 服务器按 MCP 授权规范（2025-11-25 / 2026-07-28）找到对方的授权服务器：不带令牌 POST 一次
  MCP 地址，401 的 WWW-Authenticate 里有 resource_metadata（和 scope）就按它，没有就查 /.well-known/oauth-protected-resource
  （先带路径、再根上）；资源元数据（RFC 9728）的 authorization_servers[0] → 授权服务器元数据（RFC 8414 路径插入，再 OIDC）。
  都没有资源元数据就按旧规范（2025-03-26），MCP 地址同一个域上的授权服务器元数据。
  客户端身份：server.json 配了 apps.client_id_url、对方又宣称支持 CIMD（client_id_metadata_document_supported）→ 用这个网址当
  client_id，不注册；否则有 registration_endpoint → 动态注册（DCR，token_endpoint_auth_method 要 none，对方非要给 secret 就留着、
  按它说的方式认证），按（issuer、回调地址）记在 clients.json。授权网址带 PKCE S256、随机 state、resource（RFC 8707：MCP 地址，
  资源元数据的 resource 是它的上级时用那个）、scope（WWW-Authenticate 的，没有就资源元数据的 scopes_supported）。
  手机上登录、同意 → 对方跳回 <app 的 scheme>://oauth/callback?code&state → app 交给 POST /api/apps/oauth/callback →
  换令牌（也带 resource）、列工具。进行中的授权按 state 记在 pending.json：10 分钟、只能用一次，服务重启也还在。
- 令牌只在这台服务器上：不回给 app、不写日志、不进错误信息。快过期（60 秒内）先刷新；对方回 401 就刷新一次再试；刷新不了标 needs_auth，
  app 里重新连一下（POST /api/apps/{id}/connect）。删掉时先到授权服务器撤销（有 revocation_endpoint 的话，尽力而为）。
- 用：连上的应用的工具出现在 /mcp 上（mcp_bridge.EXTRA），名字是 <应用 id>__<工具名>（只留 [A-Za-z0-9_-]，最长 64，重名加短哈希），
  说明前面带应用的名字，参数多一个 agent（和桥接工具一个意思；对方自己有 agent 参数就叫 mousse_agent）。每个工具一个档：
  auto 直接调、ask 先交收件箱等你点头、off 不给。默认读的 auto、写的 ask（readOnlyHint；没写就看名字：去掉 notion- 这样的前缀后
  以 get / list / search / read / fetch / find / query / view / describe / show / lookup 开头的算读），可以按工具单独改。
  哪些 Agent 能用按应用设（默认只有 main）；是谁：mcp-<id> 令牌绑的 Agent，否则 agent 参数，否则 main。
  远端调用：官方 mcp 包的 streamable HTTP 客户端（对方只有 SSE 就退到 sse_client），每次一个短会话，单次最长 60 秒，同时最多 4 个；
  工具列表缓存在 apps.json（连上、手动刷新时更新）；回来的文字最多 30000 字，图片之类只说一句。
- ask：收件箱卡（kind app，响铃）：谁要做、要点参数、细节里是这次调用的原样参数（JSON）。同一次调用（应用、工具、参数都一样）还在等
  就还是那张卡，不重推。你点「照做」→ 钩子照原样调一次（不超过 60 秒），结果当「做完了」发回那个 Agent 的对话（收件箱的 done 知会）；
  没做成也告诉它一声。拒绝、撤回：远端什么都不动。「改一下」：参数改不了，让它改好参数重新调（出一张新卡），这张作废。
  卡交上来以后被改过（inbox update）就不照做：卡上写的和要调的对不上。
- 安全：从对方学来的地址（资源元数据、授权服务器元数据、各个端点）要 https、不能解析到本机 / 内网 / 链路本地 / CGNAT 地址
  （和 MCP 地址同源的除外：那就是你选的服务；测试时 MCP 地址是本机、server.json apps.allow_local 为 true 也放行）；元数据跟跳转
  每一跳都查，POST 不跟跳转；MCP 会话只跟同源的跳转。你自己填的 MCP 地址：https，或者只给本机 / Tailscale（100.64.0.0/10）的 http。
  每个请求都有超时。DNS 查过以后到真正连上之间的换绑没挡（和 egress.py 以外的别处一样）。

目录：写死在下面（BUILTIN），server.json 的 apps.catalog 可以加、改、藏。收进来的都是 2026-09-29 在这台机器上验证过的
（不带令牌：POST initialize 看 401 和 WWW-Authenticate、资源元数据、授权服务器元数据；没注册任何客户端。脚本在 worktree 的
tools/apps/probe_catalog.py，不在仓库里）：
  Notion、Linear、Sentry、Canva、Todoist  资源元数据 → 同域的授权服务器；DCR 和 CIMD 都有；S256；能撤销
  Stripe                                  授权服务器在 access.stripe.com/mcp（路径插入才找得到）；DCR（只认 none）；能撤销
  Figma、Hugging Face                     DCR，但只给带 secret 的客户端（client_secret_basic / post）；不能撤销。
                                          Hugging Face 不带令牌也能用（匿名、工具少），连上是你自己的账号
  Atlassian                               没有资源元数据（404）：旧规范的路子，MCP 同域上的授权服务器元数据，DCR，能撤销
  GitHub                                  授权服务器 github.com/login/oauth 没有 DCR / CIMD：用个人访问令牌（auth token）
  Cloudflare 文档、DeepWiki、Context7      不用授权（Context7 也有可选的 OAuth，匿名就够用）
  没收 Asana：v2（/v2/mcp）的授权服务器 app.asana.com 既没有 DCR 也没有 CIMD，要先在 Asana 开发者后台注册应用；
  还能 DCR 的 /sse 是要下线的 v1。

server.json 的 apps 段（都可以不写，每次读文件）：
  "apps": {"catalog": [{"id", "name", "url", "category", "desc", "mono", "bg", "fg", "border", "auth", "hint"}…]
                      或 {"<id>": {…要改的字段} 或 null（藏起来）},
           "client_id_url": "https://…/client.json",   CIMD：对方支持就拿这个网址当 client_id（文档要你自己放在那个网址上）
           "redirect_uris": ["https://…"],             网页版、测试用的 http(s) 回调地址（精确匹配）
           "allow_local": false}                        只给测试：MCP 地址是本机时，对方给的本机 / http 地址也连
文件（<data_dir>/apps/，目录 700、文件 600，先写临时文件再改名）：apps.json 各应用的状态、权限和工具（没有秘密）；
secrets.json 令牌、客户端、授权服务器的端点；clients.json 注册过的客户端；pending.json 进行中的授权；calls.json 等你点头的调用。
"""
from __future__ import annotations

import asyncio
import base64
import contextvars
import copy
import hashlib
import ipaddress
import json
import logging
import os
import re
import secrets
import socket
import sqlite3
import threading
import time
from contextlib import contextmanager, suppress
from datetime import timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlsplit, urlunsplit

import anyio
import httpx
import jsonschema
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from mcp import ClientSession, types
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamablehttp_client
from mcp.server.fastmcp.exceptions import ToolError
from mcp.shared.exceptions import McpError
from pydantic import BaseModel

import chat
import data
import inbox
import mcp_bridge
from chat import log_activity, now_iso
from config import raw, settings
from i18n import L, LS

router = APIRouter()

LEVELS = ("auto", "ask", "off")
DEFAULT_POLICY = {"read": "auto", "write": "ask"}
AUTHS = ("oauth", "token", "none")
CATEGORIES = ("common", "dev", "data")
HTTP_TIMEOUT = httpx.Timeout(15, connect=10)  # 元数据、注册、换令牌、撤销
CALL_TIMEOUT = 60      # 一次远端操作（连上 → 初始化 → 列工具 / 调工具）最长多少秒
PENDING_TTL = 600      # 授权要在 10 分钟内做完
REFRESH_AHEAD = 60     # 令牌离过期不到这么多秒就先刷新
MAX_OUT = 30000        # 回给模型的文字最多多少字（和 mcp_bridge 一样掐中间）
MAX_ARGS = 100000      # 一次调用的参数（JSON）最多多少字
MAX_DESC = 8000        # 远端工具的说明交给 claw 时最多多少字
PARALLEL = asyncio.Semaphore(4)
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
SCHEME_RE = re.compile(r"^([a-z][a-z0-9+.-]{1,30})://oauth/callback$")
BAD_SCHEMES = {"http", "https", "file", "javascript", "data", "about", "blob", "vbscript", "ftp", "ws", "wss", "content", "intent"}
TAILSCALE = ipaddress.ip_network("100.64.0.0/10")
READ_VERBS = ("get", "list", "search", "read", "fetch", "find", "query", "view", "describe", "show", "lookup")
RESERVED = {"oauth"}   # 路由里用了的词，不能当应用 id
TILE_COLORS = ("#2563EB", "#7C3AED", "#DB2777", "#EA580C", "#059669", "#0891B2", "#4B5563", "#B45309")
_lock = threading.Lock()  # apps/ 下文件的读改写
_refreshing: dict[str, asyncio.Lock] = {}
_quiet = contextvars.ContextVar("apps_quiet", default=False)


class QuietHttpx(logging.Filter):
    """httpx 每个请求记一行 INFO，带完整网址：连接器发的不记（自定义的 MCP 地址里可能带着密钥，比如 …/s/<密钥>/mcp）。"""

    def filter(self, record: logging.LogRecord) -> bool:
        return not _quiet.get()


logging.getLogger("httpx").addFilter(QuietHttpx())


@contextmanager
def quiet():
    """这一段里（连同它起的子任务）httpx 不记请求日志。"""
    token = _quiet.set(True)
    try:
        yield
    finally:
        _quiet.reset(token)

# 目录。desc / hint 是（中文, English）。mono 是图标块上的 1–3 个字，bg / fg / border 是它的底色、字色、描边（浅底才要描边）。
BUILTIN: list[dict] = [
    {"id": "notion", "name": "Notion", "url": "https://mcp.notion.com/mcp", "category": "common", "auth": "oauth",
     "desc": ("查找、读写你的 Notion 页面和数据库", "Search, read and write your Notion pages and databases"),
     "mono": "N", "bg": "#FFFFFF", "fg": "#191919", "border": "#E3E2E0"},
    {"id": "todoist", "name": "Todoist", "url": "https://ai.todoist.net/mcp", "category": "common", "auth": "oauth",
     "desc": ("看、加、完成你的 Todoist 任务", "View, add and complete your Todoist tasks"), "mono": "T", "bg": "#E44332", "fg": "#FFFFFF"},
    {"id": "atlassian", "name": "Atlassian", "url": "https://mcp.atlassian.com/v1/mcp", "category": "common", "auth": "oauth",
     "desc": ("Jira 的问题和 Confluence 的页面", "Jira issues and Confluence pages"), "mono": "A", "bg": "#0052CC", "fg": "#FFFFFF"},
    {"id": "canva", "name": "Canva", "url": "https://mcp.canva.com/mcp", "category": "common", "auth": "oauth",
     "desc": ("找你的设计、按你的品牌做新的", "Find your designs and make new ones in your brand"), "mono": "C", "bg": "#7D2AE8", "fg": "#FFFFFF"},
    {"id": "figma", "name": "Figma", "url": "https://mcp.figma.com/mcp", "category": "common", "auth": "oauth",
     "desc": ("读 Figma 文件里的设计、变量和组件", "Read designs, variables and components from your Figma files"),
     "mono": "F", "bg": "#1E1E1E", "fg": "#FFFFFF"},
    {"id": "github", "name": "GitHub", "url": "https://api.githubcopilot.com/mcp/", "category": "dev", "auth": "token",
     "desc": ("仓库、问题和拉取请求", "Repositories, issues and pull requests"), "mono": "GH", "bg": "#181717", "fg": "#FFFFFF",
     "hint": ("在 github.com/settings/personal-access-tokens 建一个细粒度的个人访问令牌，只选要用的仓库和权限，粘贴到这里",
              "Create a fine-grained personal access token at github.com/settings/personal-access-tokens with only the repositories "
              "and permissions it needs, and paste it here")},
    {"id": "linear", "name": "Linear", "url": "https://mcp.linear.app/mcp", "category": "dev", "auth": "oauth",
     "desc": ("看、建、改 Linear 的问题和项目", "View, create and update Linear issues and projects"), "mono": "L", "bg": "#5E6AD2", "fg": "#FFFFFF"},
    {"id": "sentry", "name": "Sentry", "url": "https://mcp.sentry.dev/mcp", "category": "dev", "auth": "oauth",
     "desc": ("查报错、问题和发布", "Look into errors, issues and releases"), "mono": "S", "bg": "#362D59", "fg": "#FFFFFF"},
    {"id": "cloudflare-docs", "name": "Cloudflare Docs", "url": "https://docs.mcp.cloudflare.com/mcp", "category": "dev", "auth": "none",
     "desc": ("查 Cloudflare 的官方文档", "Search Cloudflare's documentation"), "mono": "CF", "bg": "#F38020", "fg": "#FFFFFF"},
    {"id": "deepwiki", "name": "DeepWiki", "url": "https://mcp.deepwiki.com/mcp", "category": "dev", "auth": "none",
     "desc": ("问任何一个公开 GitHub 仓库的问题", "Ask about any public GitHub repository"), "mono": "DW", "bg": "#0B1220", "fg": "#FFFFFF"},
    {"id": "context7", "name": "Context7", "url": "https://mcp.context7.com/mcp", "category": "dev", "auth": "none",
     "desc": ("各种库和框架的最新文档", "Up-to-date docs for libraries and frameworks"), "mono": "C7", "bg": "#0E9F6E", "fg": "#FFFFFF"},
    {"id": "stripe", "name": "Stripe", "url": "https://mcp.stripe.com", "category": "data", "auth": "oauth",
     "desc": ("客户、付款、订阅和退款", "Customers, payments, subscriptions and refunds"), "mono": "S", "bg": "#635BFF", "fg": "#FFFFFF"},
    {"id": "huggingface", "name": "Hugging Face", "url": "https://huggingface.co/mcp", "category": "data", "auth": "oauth",
     "desc": ("搜模型、数据集、Spaces 和论文", "Search models, datasets, Spaces and papers"), "mono": "HF", "bg": "#FFD21E", "fg": "#1F2937"},
]


class AppError(Exception):
    """连远端出的错。话按请求的语言写好了（app 看、模型看），里面没有令牌。"""


class AuthError(AppError):
    """对方不认我们的令牌（刷新也不行）。"""


class TokenError(AppError):
    def __init__(self, message: str, code: str = "", status: int = 0):
        super().__init__(message)
        self.code, self.status = code, status


class Unauthorized(Exception):
    """MCP 会话里对方回了 401。"""


class WrongTransport(Exception):
    """streamable HTTP 的 POST 被 404 / 405 挡回：对方可能只有 SSE。"""


# —— 文件 ——————————————————————————————————————————————————————————————

def home() -> Path:
    return settings.data_dir / "apps"


def load(name: str) -> dict:
    try:
        v = json.loads((home() / name).read_text(encoding="utf8"))
    except (OSError, ValueError):
        return {}
    return v if isinstance(v, dict) else {}


def store(name: str, value: dict) -> None:
    """先写临时文件再改名：写到一半断电也不会留下半个文件。目录 700、文件 600（令牌在里面）。"""
    d = home()
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    tmp = d / f".{name}.{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf8") as f:
        json.dump(value, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, 0o600)  # 上次留下的临时文件权限可能不对：os.open 不改已有文件的权限
    os.replace(tmp, d / name)


def all_apps() -> dict[str, dict]:
    v = load("apps.json").get("apps")
    return v if isinstance(v, dict) else {}


def get(aid: str) -> dict | None:
    return all_apps().get(aid)


def must(aid: str) -> dict:
    app = get(aid) if ID_RE.match(aid or "") else None
    if not app:
        raise HTTPException(404, L("没有这个连接器", "No such connector"))
    return app


def put(app: dict) -> None:
    with _lock:
        apps = all_apps()
        apps[app["id"]] = app
        store("apps.json", {"apps": apps})


def change(aid: str, **fields) -> dict | None:
    """改一个应用的几项（锁里读改写）。应用已经不在了 → None。"""
    with _lock:
        apps = all_apps()
        app = apps.get(aid)
        if app is None:
            return None
        app.update(fields, updatedAt=now_iso())
        store("apps.json", {"apps": apps})
    return app


def forget(aid: str) -> None:
    with _lock:
        apps = all_apps()
        apps.pop(aid, None)
        store("apps.json", {"apps": apps})
        s = load("secrets.json")
        if s.pop(aid, None) is not None:
            store("secrets.json", s)
        p = load("pending.json")
        if any(v.get("app") == aid for v in p.values()):
            store("pending.json", {k: v for k, v in p.items() if v.get("app") != aid})


def secret(aid: str) -> dict:
    v = load("secrets.json").get(aid)
    return v if isinstance(v, dict) else {}


def set_secret(aid: str, value: dict | None) -> None:
    with _lock:
        s = load("secrets.json")
        if value is None:
            s.pop(aid, None)
        else:
            s[aid] = value
        store("secrets.json", s)


def mark(aid: str, status: str, error: str = "") -> None:
    change(aid, status=status, error=error[:300])


# —— 目录 ——————————————————————————————————————————————————————————————

def cfg() -> dict:
    v = raw().get("apps")
    return v if isinstance(v, dict) else {}


def text_of(v: Any) -> str:
    """desc / hint：（中文, English）、{"zh", "en"} 或一句话。"""
    if isinstance(v, (list, tuple)) and len(v) == 2:
        return L(str(v[0]), str(v[1]))
    if isinstance(v, dict):
        return L(str(v.get("zh") or v.get("en") or ""), str(v.get("en") or v.get("zh") or ""))
    return str(v or "")


def catalog() -> dict[str, dict]:
    """内置目录 + server.json apps.catalog（列表按 id 盖上去；字典 {id: {…} 或 null}，null 或 hidden = 藏起来）。填错的条目不要。"""
    out = {e["id"]: dict(e) for e in BUILTIN}
    extra = cfg().get("catalog")
    items: list[tuple[Any, Any]] = []
    if isinstance(extra, dict):
        items = list(extra.items())
    elif isinstance(extra, list):
        items = [(e.get("id"), e) for e in extra if isinstance(e, dict)]
    for cid, e in items:
        cid = str(cid or "")
        if not ID_RE.match(cid) or cid in RESERVED:
            continue
        if e is None or (isinstance(e, dict) and e.get("hidden")):
            out.pop(cid, None)
        elif isinstance(e, dict):
            out[cid] = {**out.get(cid, {}), **e, "id": cid}
    good = {}
    for cid, e in out.items():
        u = urlsplit(str(e.get("url") or ""))
        if e.get("name") and u.scheme in ("https", "http") and u.hostname and e.get("auth", "oauth") in AUTHS:
            good[cid] = {**e, "auth": e.get("auth") or "oauth", "category": e.get("category") if e.get("category") in CATEGORIES else "common"}
    return good


def catalog_json(e: dict, installed: bool) -> dict:
    return {"id": e["id"], "name": e["name"], "url": e["url"], "category": e["category"], "desc": text_of(e.get("desc")),
            "mono": str(e.get("mono") or initials(e["name"]))[:3], "bg": e.get("bg") or "#4B5563", "fg": e.get("fg") or "#FFFFFF",
            "border": e.get("border"), "auth": e["auth"], "hint": text_of(e.get("hint")) or None, "installed": installed}


def initials(name: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", name)
    if len(words) >= 2:
        return (words[0][0] + words[1][0]).upper()
    if words:
        return words[0][:2].capitalize() if len(words[0]) > 1 else words[0].upper()
    return (name.strip()[:1] or "?").upper()


def new_id(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:24].strip("-") or "app"
    taken = set(all_apps()) | set(catalog()) | RESERVED
    aid, n = base, 2
    while aid in taken:
        aid, n = f"{base}-{n}", n + 1
    return aid


# —— 地址安全 ——————————————————————————————————————————————————————————

def canonical(url: str) -> str:
    """RFC 8707 的资源标识：scheme 和主机小写，去掉 #片段。"""
    u = urlsplit(url.strip())
    return urlunsplit((u.scheme.lower(), u.netloc.lower(), u.path, u.query, ""))


def origin(url: str) -> tuple[str, str, int]:
    u = urlsplit(url)
    scheme = u.scheme.lower()
    try:
        port = u.port
    except ValueError:
        port = -1
    return scheme, (u.hostname or "").lower(), port or (443 if scheme == "https" else 80)


def is_loopback_url(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


async def addresses(host: str, port: int) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError) as e:
        raise AppError(L(f"找不到 {host}", f"Can't resolve {host}")) from e
    out = []
    for *_, sa in infos:
        ip = ipaddress.ip_address(str(sa[0]).split("%")[0])
        if ip.version == 6 and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        out.append(ip)
    return out


class Guard:
    """一个应用的规矩：从对方学来的地址能不能连。和 MCP 地址同源的放行（那就是你选的服务）；别的要 https、解析出来全是公网地址。
    测试：MCP 地址本身是本机、server.json apps.allow_local 为 true → 都放行。"""

    def __init__(self, mcp_url: str):
        self.base = origin(mcp_url)
        self.local = bool(cfg().get("allow_local")) and is_loopback_url(mcp_url)

    async def check(self, url: str) -> str:
        u = urlsplit(url)
        host = u.hostname or ""
        bad = AppError(L(f"对方给的地址不安全，没连：{host or url[:60]}（要 https、不能是本机或内网地址）",
                         f"Refused an unsafe address from the server: {host or url[:60]} (it must be https and public)"))
        if u.scheme.lower() not in ("https", "http") or not host or u.username or u.password:
            raise bad
        if self.local or origin(url) == self.base:
            return url
        if u.scheme.lower() != "https":
            raise bad
        for ip in await addresses(host, origin(url)[2]):
            if not ip.is_global:
                raise bad
        return url


async def check_url(url: str | None) -> str:
    """你自己填的 MCP 地址：https；http 只给本机和 Tailscale（100.64.0.0/10）。→ 规范化的地址。"""
    url = (url or "").strip()
    u = urlsplit(url)
    scheme = u.scheme.lower()
    if scheme not in ("https", "http") or not u.hostname or u.username or u.password or len(url) > 500 or any(c.isspace() for c in url):
        raise HTTPException(400, L("MCP 地址要写成 https://…", "The MCP address must look like https://…"))
    if scheme == "http":
        try:
            ips = await addresses(u.hostname, origin(url)[2])
        except AppError as e:
            raise HTTPException(400, str(e)) from None
        if not ips or not all(ip.is_loopback or ip in TAILSCALE for ip in ips):
            raise HTTPException(400, L("http 只能连这台机器或 Tailscale 里的地址，别的请用 https",
                                       "Plain http only works for this machine or a Tailscale address; use https otherwise"))
    return canonical(url)


def check_redirect(uri: str | None) -> str:
    """回调地址：<app 的 scheme>://oauth/callback（不能是 http、https、file、javascript、data 这些），或者 server.json apps.redirect_uris 里的。"""
    uri = (uri or "").strip()
    m = SCHEME_RE.match(uri)
    if m and m.group(1) not in BAD_SCHEMES:
        return uri
    allowed = [str(x) for x in cfg().get("redirect_uris") or []]
    if uri in allowed and uri.lower().startswith(("https://", "http://")):
        return uri
    raise HTTPException(400, L("redirect_uri 要写成 <app 的 scheme>://oauth/callback", "redirect_uri must be <your app's scheme>://oauth/callback"))


# —— OAuth ——————————————————————————————————————————————————————————————

def http() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=False, headers={"User-Agent": f"{settings.app_name} connectors"})


def header_field(header: str, name: str) -> str | None:
    m = re.search(rf'(?:^|[\s,]){name}=(?:"([^"]*)"|([^\s,]+))', header or "")
    return (m.group(1) or m.group(2)) if m else None


def reason(e: BaseException) -> str:
    """异常 → 一句人话（取最里面那个；不带令牌，httpx 的异常里也没有）。"""
    while isinstance(e, BaseExceptionGroup) and e.exceptions:
        e = e.exceptions[0]
    if isinstance(e, httpx.HTTPStatusError):
        return f"HTTP {e.response.status_code}"
    if isinstance(e, (httpx.TimeoutException, TimeoutError)):
        return L("超时", "timed out")
    if isinstance(e, httpx.HTTPError):
        return type(e).__name__
    if isinstance(e, McpError):
        return clean(e.error.message, 300)
    return clean(str(e), 300) or type(e).__name__


def clean(text: Any, n: int) -> str:
    """对方给的话：去掉控制字符，压成一行，最多 n 个字。"""
    s = re.sub(r"[\x00-\x1f\x7f]+", " ", str(text or "")).strip()
    return s if len(s) <= n else s[: n - 1] + "…"


async def get_json(c: httpx.AsyncClient, url: str, guard: Guard) -> dict | None:
    """GET 一份 JSON 元数据：跳转最多跟 3 次，每一跳都过 guard；不是 200、不是 JSON 对象 → None。不安全的地址直接报错（AppError）。"""
    for _ in range(4):
        await guard.check(url)
        try:
            r = await c.get(url, headers={"Accept": "application/json", "MCP-Protocol-Version": types.LATEST_PROTOCOL_VERSION})
        except httpx.HTTPError:
            return None
        if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
            url = urljoin(url, r.headers["location"])
            continue
        if r.status_code != 200:
            return None
        try:
            v = r.json()
        except ValueError:
            return None
        return v if isinstance(v, dict) else None
    return None


async def post(c: httpx.AsyncClient, url: str, guard: Guard, **kw) -> httpx.Response:
    await guard.check(url)
    try:
        return await c.post(url, **kw)
    except httpx.HTTPError as e:
        raise AppError(L(f"连不上 {urlsplit(url).hostname}（{reason(e)}）", f"Couldn't reach {urlsplit(url).hostname} ({reason(e)})")) from e


def prm_candidates(hinted: str | None, url: str) -> list[str]:
    u = urlsplit(url)
    base = f"{u.scheme}://{u.netloc}"
    out = [hinted] if hinted else []
    if u.path.strip("/"):
        out.append(f"{base}/.well-known/oauth-protected-resource{u.path.rstrip('/')}")
    out.append(f"{base}/.well-known/oauth-protected-resource")
    return out


def as_candidates(issuer: str) -> list[str]:
    u = urlsplit(issuer)
    base, path = f"{u.scheme}://{u.netloc}", u.path.rstrip("/")
    if path:
        return [f"{base}/.well-known/oauth-authorization-server{path}", f"{base}/.well-known/openid-configuration{path}",
                f"{base}{path}/.well-known/openid-configuration"]
    return [f"{base}/.well-known/oauth-authorization-server", f"{base}/.well-known/openid-configuration"]


def under(child: str, parent: str) -> bool:
    """parent 是不是 child 本身或者它的上级（同源、路径是前缀）。"""
    if origin(child) != origin(parent):
        return False
    c, p = urlsplit(child).path or "/", urlsplit(parent).path or "/"
    return (c if c.endswith("/") else c + "/").startswith(p if p.endswith("/") else p + "/")


def pick_resource(url: str, theirs: Any) -> str:
    """resource 参数：MCP 地址本身；资源元数据的 resource 是它（或它的上级）就用那个。别的域的 → 这份元数据不是这个服务的，不连。"""
    mine = canonical(url)
    if not theirs:
        return mine
    theirs = str(theirs)
    if origin(theirs) != origin(mine):
        raise AppError(L("资源元数据说的是别的地址，不连", "The resource metadata is for a different address; refusing"))
    return theirs if under(mine, theirs) else mine


async def discover(url: str, guard: Guard) -> dict:
    """MCP 地址 → {issuer, as（授权服务器元数据）, resource, scope}。"""
    with quiet():
        return await _discover(url, guard)


async def _discover(url: str, guard: Guard) -> dict:
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": types.LATEST_PROTOCOL_VERSION, "capabilities": {},
                       "clientInfo": {"name": settings.app_name, "version": "1"}}}
    host = urlsplit(url).hostname
    async with http() as c:
        www = ""
        try:
            r = await c.post(url, json=init, headers={"Accept": "application/json, text/event-stream", "Content-Type": "application/json",
                                                      "MCP-Protocol-Version": types.LATEST_PROTOCOL_VERSION})
            if r.status_code == 401:
                www = r.headers.get("www-authenticate") or ""
        except httpx.HTTPError as e:
            raise AppError(L(f"连不上 {host}（{reason(e)}）", f"Couldn't reach {host} ({reason(e)})")) from e
        hinted, scope = header_field(www, "resource_metadata"), header_field(www, "scope")
        prm = None
        for u in prm_candidates(hinted, url):
            prm = await get_json(c, u, guard)
            if prm and isinstance(prm.get("authorization_servers"), list) and prm["authorization_servers"]:
                break
            prm = None
        if prm:
            issuer = str(prm["authorization_servers"][0])
            resource = pick_resource(url, prm.get("resource"))
            if scope is None and isinstance(prm.get("scopes_supported"), list):
                scope = " ".join(str(s) for s in prm["scopes_supported"]) or None
        else:  # 旧规范（2025-03-26）：没有资源元数据，授权服务器在 MCP 地址的同一个域上
            u = urlsplit(url)
            issuer, resource = f"{u.scheme}://{u.netloc}", canonical(url)
        await guard.check(issuer)
        meta = None
        for u in as_candidates(issuer):
            meta = await get_json(c, u, guard)
            if meta and meta.get("authorization_endpoint") and meta.get("token_endpoint"):
                break
            meta = None
    if not meta:
        raise AppError(L(f"{host} 没有给出 OAuth 授权信息，没法这样连（可以试试用令牌）",
                         f"{host} doesn't publish OAuth metadata, so it can't be connected this way (try a token instead)"))
    if str(meta.get("issuer") or issuer).rstrip("/") != issuer.rstrip("/"):
        raise AppError(L("授权服务器的 issuer 对不上，不连", "The authorization server's issuer doesn't match; refusing"))
    if "S256" not in (meta.get("code_challenge_methods_supported") or []):
        raise AppError(L("授权服务器没说支持 PKCE（S256），按规范不能连", "The authorization server doesn't advertise PKCE (S256), so it can't be used"))
    for key in ("authorization_endpoint", "token_endpoint", "registration_endpoint", "revocation_endpoint"):
        if meta.get(key):
            await guard.check(str(meta[key]))
    return {"issuer": issuer, "as": meta, "resource": resource, "scope": scope}


async def client_for(disc: dict, redirect_uri: str, guard: Guard, fresh: bool = False) -> dict:
    """我们在这个授权服务器上的客户端：CIMD（配了 client_id_url、对方支持）→ 注册过的（clients.json）→ 现在动态注册一个。
    fresh = 不用注册过的，重新注册（上一次授权没做完就又来了：多半是对方已经不认那个客户端，授权页直接报错、回不到我们这里）。"""
    meta, issuer = disc["as"], disc["issuer"]
    cid_url = str(cfg().get("client_id_url") or "")
    u = urlsplit(cid_url)
    if cid_url and u.scheme == "https" and u.path not in ("", "/") and meta.get("client_id_metadata_document_supported") is True:
        return {"client_id": cid_url, "token_endpoint_auth_method": "none"}
    key = f"{issuer}|{redirect_uri}"
    known = None if fresh else load("clients.json").get(key)
    if isinstance(known, dict) and known.get("client_id"):
        exp = known.get("client_secret_expires_at") or 0
        if not exp or exp > time.time() + 300:
            return known
    reg = meta.get("registration_endpoint")
    if not reg:
        raise AppError(L("这个服务不支持自动注册客户端，暂时连不了", "This server doesn't support automatic client registration, so it can't be connected yet"))
    methods = meta.get("token_endpoint_auth_methods_supported") or ["none"]
    want = "none" if "none" in methods else next((m for m in ("client_secret_basic", "client_secret_post") if m in methods), "none")
    body = {"client_name": settings.app_name or "OpenMousse", "redirect_uris": [redirect_uri], "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"], "token_endpoint_auth_method": want}
    if disc.get("scope"):
        body["scope"] = disc["scope"]
    with quiet():
        async with http() as c:
            r = await post(c, str(reg), guard, json=body, headers={"Accept": "application/json"})
    try:
        info = r.json()
    except ValueError:
        info = {}
    if r.status_code not in (200, 201) or not isinstance(info, dict) or not info.get("client_id"):
        raise AppError(L(f"注册客户端没成功（{oauth_error(info, r.status_code)}）", f"Client registration failed ({oauth_error(info, r.status_code)})"))
    client = {"client_id": str(info["client_id"]), "token_endpoint_auth_method": str(info.get("token_endpoint_auth_method") or
                                                                                    ("client_secret_basic" if info.get("client_secret") else "none")),
              "registered_at": now_iso()}
    if info.get("client_secret"):
        client["client_secret"] = str(info["client_secret"])
        client["client_secret_expires_at"] = int(info.get("client_secret_expires_at") or 0)
    with _lock:
        cs = load("clients.json")
        cs[key] = client
        store("clients.json", cs)
    return client


def drop_client(issuer: str, redirect_uri: str) -> None:
    """对方说不认这个客户端（invalid_client）：忘掉，下次重新注册。"""
    with _lock:
        cs = load("clients.json")
        if cs.pop(f"{issuer}|{redirect_uri}", None) is not None:
            store("clients.json", cs)


def client_auth(client: dict, form: dict, headers: dict) -> None:
    """按注册时说好的方式在请求里证明是这个客户端。"""
    cid, sec = str(client.get("client_id") or ""), client.get("client_secret")
    method = client.get("token_endpoint_auth_method") or ("client_secret_basic" if sec else "none")
    if sec and method == "client_secret_basic":
        pair = f"{quote(cid, safe='')}:{quote(str(sec), safe='')}".encode()
        headers["Authorization"] = "Basic " + base64.b64encode(pair).decode()
    elif sec and method == "client_secret_post":
        form.update(client_id=cid, client_secret=str(sec))
    else:
        form["client_id"] = cid


def oauth_error(body: Any, status: int) -> str:
    if isinstance(body, dict) and body.get("error"):
        desc = clean(body.get("error_description"), 160)
        return clean(body["error"], 60) + (f": {desc}" if desc else "")
    return f"HTTP {status}"


async def token_request(endpoint: str, client: dict, form: dict, guard: Guard) -> dict:
    headers = {"Accept": "application/json"}
    form = dict(form)
    client_auth(client, form, headers)
    with quiet():
        async with http() as c:
            r = await post(c, endpoint, guard, data=form, headers=headers)
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code != 200 or not isinstance(body, dict) or not body.get("access_token"):
        raise TokenError(oauth_error(body, r.status_code), code=str((body or {}).get("error") or "") if isinstance(body, dict) else "",
                         status=r.status_code)
    if str(body.get("token_type") or "bearer").lower() != "bearer":
        raise TokenError(L("对方给的不是 Bearer 令牌", "The server didn't issue a Bearer token"))
    return body


def account_of(body: dict) -> str:
    """换令牌时对方顺手给的账号名（有的给 workspace_name、team、user 之类），没有就空。"""
    for k in ("workspace_name", "account_name", "team_name", "organization_name", "user_name", "email", "login"):
        if isinstance(body.get(k), str) and body[k].strip():
            return clean(body[k], 60)
    for k in ("workspace", "team", "organization", "account", "user"):
        v = body.get(k)
        if isinstance(v, dict):
            for f in ("name", "email", "login"):
                if isinstance(v.get(f), str) and v[f].strip():
                    return clean(v[f], 60)
    return ""


def keep_tokens(aid: str, body: dict, oauth: dict | None = None) -> None:
    """存令牌。oauth 给了 = 新授权（整个换掉）；没给 = 刷新（对方没给新的 refresh_token 就留着旧的）。"""
    try:
        ttl = float(body.get("expires_in") or 0)
    except (TypeError, ValueError):
        ttl = 0
    with _lock:
        s = load("secrets.json")
        cur = s.get(aid) if isinstance(s.get(aid), dict) else {}
        old = cur.get("tokens") or {}
        rt = body.get("refresh_token") or (None if oauth else old.get("refresh_token"))
        tok = {"access_token": str(body["access_token"]), "expires_at": time.time() + ttl if ttl > 0 else None,
               "refresh_token": str(rt) if rt else None, "scope": body.get("scope") or old.get("scope")}
        cur = {**cur, "tokens": tok}
        if oauth:
            cur["oauth"] = oauth
            cur.pop("token", None)
        s[aid] = cur
        store("secrets.json", s)


async def begin(app: dict, redirect_uri: str) -> dict:
    """开始授权：找授权服务器、拿客户端、记下这一次（state），给 app 一个授权网址。"""
    guard = Guard(app["url"])
    disc = await discover(app["url"], guard)
    now = time.time()
    # 上一次还没回来就又点了：多半是授权页报了错（对方不认缓存的客户端）。这次换一个新注册的，上一次作废（只换一次）
    unfinished = [k for k, v in load("pending.json").items() if isinstance(v, dict) and v.get("app") == app["id"]
                  and v.get("redirect_uri") == redirect_uri and now - (v.get("created") or 0) < PENDING_TTL]
    client = await client_for(disc, redirect_uri, guard, fresh=bool(unfinished))
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(24)
    params = {"response_type": "code", "client_id": client["client_id"], "redirect_uri": redirect_uri, "state": state,
              "code_challenge": challenge, "code_challenge_method": "S256", "resource": disc["resource"]}
    if disc.get("scope"):
        params["scope"] = disc["scope"]
    ep = urlsplit(str(disc["as"]["authorization_endpoint"]))
    q = parse_qsl(ep.query, keep_blank_values=True) + list(params.items())
    url = urlunsplit((ep.scheme, ep.netloc, ep.path, urlencode(q, quote_via=quote), ""))
    meta = disc["as"]
    with _lock:
        now = time.time()
        pend = {k: v for k, v in load("pending.json").items()
                if isinstance(v, dict) and now - (v.get("created") or 0) < PENDING_TTL and k not in unfinished}
        pend[state] = {"app": app["id"], "created": now, "verifier": verifier, "redirect_uri": redirect_uri, "issuer": disc["issuer"],
                       "client": client, "resource": disc["resource"], "scope": disc.get("scope"), "token_endpoint": str(meta["token_endpoint"]),
                       "revocation_endpoint": str(meta.get("revocation_endpoint") or "") or None}
        while len(pend) > 50:  # 点了又不做完的：最多留 50 个
            pend.pop(min(pend, key=lambda k: pend[k]["created"]))
        store("pending.json", pend)
    return {"authorizeUrl": url, "state": state}


async def finish(state: str, code: str, error: str, error_description: str, iss: str) -> dict:
    """授权回来了：认 state（一次性）、换令牌、存起来、列工具。→ 应用的详情。"""
    with _lock:
        pend = load("pending.json")
        p = pend.pop(state, None) if state else None
        store("pending.json", {k: v for k, v in pend.items() if isinstance(v, dict) and time.time() - (v.get("created") or 0) < PENDING_TTL})
    if not isinstance(p, dict) or time.time() - (p.get("created") or 0) >= PENDING_TTL:
        raise HTTPException(400, L("这次授权已经过期或者用过了：回到连接器重新连一次", "This authorization has expired or was already used: start again from Connectors"))
    app = get(p["app"])
    if not app:
        raise HTTPException(404, L("这个连接器已经删掉了", "This connector was removed"))
    aid, name, connected = app["id"], app["name"], app.get("status") == "connected"
    if error:
        if error == "access_denied":
            why = L(f"你在 {name} 那边取消了", f"you cancelled at {name}")
        else:
            why = clean(error, 60) + (f": {clean(error_description, 160)}" if error_description else "")
        if not connected:
            mark(aid, "needs_auth", why)
        raise HTTPException(400, L(f"{name} 没有授权：{why}", f"{name} didn't grant access: {why}"))
    if iss and iss.rstrip("/") != str(p.get("issuer") or "").rstrip("/"):
        raise HTTPException(400, L("回调里的 iss 和授权服务器对不上，没接受", "The callback's iss doesn't match the authorization server; not accepted"))
    if not code:
        raise HTTPException(400, L("回调里没有 code", "The callback has no code"))
    form = {"grant_type": "authorization_code", "code": code, "redirect_uri": p["redirect_uri"], "code_verifier": p["verifier"],
            "resource": p["resource"]}
    try:
        body = await token_request(p["token_endpoint"], p["client"], form, Guard(app["url"]))
    except AppError as e:
        if isinstance(e, TokenError) and e.code == "invalid_client":
            drop_client(p["issuer"], p["redirect_uri"])
        if not connected:
            mark(aid, "needs_auth", str(e))
        raise HTTPException(502, L(f"向 {name} 换令牌没成功：{e}", f"Couldn't get a token from {name}: {e}")) from None
    keep_tokens(aid, body, oauth={"issuer": p["issuer"], "token_endpoint": p["token_endpoint"], "revocation_endpoint": p.get("revocation_endpoint"),
                                  "resource": p["resource"], "scope": p.get("scope"), "client": p["client"]})
    change(aid, status="connected", error="", connectedAt=now_iso(), account=account_of(body))
    try:
        await refresh_tools(aid)
    except AppError as e:
        mark(aid, "error", str(e))
    log_activity(L(f"连上了 {name}", f"Connected {name}"), "edit")
    return detail(aid)


async def bearer(aid: str, stale: str | None = None) -> str | None:
    """调这个应用带的令牌。stale = 刚被对方 401 退回来的那个：别的请求已经换了新的就用新的，不然刷新。"""
    app = get(aid)
    if not app:
        raise AppError(L("这个连接器已经删掉了", "This connector was removed"))
    name = app["name"]
    expired = L(f"{name} 的授权失效了：请在 设置 → 连接器 → {name} 里重新连一下", f"{name}'s authorization has expired: reconnect it in Settings → Connectors → {name}")
    if app["auth"] == "none":
        return None
    if app["auth"] == "token":
        tok = secret(aid).get("token")
        if not tok or tok == stale:
            mark(aid, "needs_auth", expired)
            raise AuthError(expired)
        return tok

    def usable(tok: dict) -> str | None:
        access = tok.get("access_token")
        if access and access != stale and (not tok.get("expires_at") or tok["expires_at"] - time.time() > REFRESH_AHEAD):
            return access
        return None

    if got := usable(secret(aid).get("tokens") or {}):
        return got
    async with _refreshing.setdefault(aid, asyncio.Lock()):
        sec = secret(aid)
        if got := usable(sec.get("tokens") or {}):  # 等锁的时候别人已经刷新好了
            return got
        oauth, rt = sec.get("oauth") or {}, (sec.get("tokens") or {}).get("refresh_token")
        if not rt or not oauth.get("token_endpoint"):
            mark(aid, "needs_auth", expired)
            raise AuthError(expired)
        form = {"grant_type": "refresh_token", "refresh_token": rt}
        if oauth.get("resource"):
            form["resource"] = oauth["resource"]
        try:
            body = await token_request(oauth["token_endpoint"], oauth.get("client") or {}, form, Guard(app["url"]))
        except TokenError as e:
            if e.status in (400, 401, 403) or e.code:
                mark(aid, "needs_auth", expired)
                raise AuthError(expired) from None
            raise AppError(L(f"刷新 {name} 的授权没成功：{e}", f"Couldn't refresh {name}'s authorization: {e}")) from None
        keep_tokens(aid, body)
        return str(body["access_token"])


async def revoke(app: dict) -> bool:
    """在授权服务器上撤销令牌（RFC 7009；有 revocation_endpoint 才做，出错不管）。"""
    sec = secret(app["id"])
    oauth, tok = sec.get("oauth") or {}, sec.get("tokens") or {}
    ep = oauth.get("revocation_endpoint")
    if app.get("auth") != "oauth" or not ep:
        return False
    ok = False
    guard = Guard(app["url"])
    with quiet():
        async with http() as c:
            for value, hint in ((tok.get("refresh_token"), "refresh_token"), (tok.get("access_token"), "access_token")):
                if not value:
                    continue
                form, headers = {"token": value, "token_type_hint": hint}, {"Accept": "application/json"}
                client_auth(oauth.get("client") or {}, form, headers)
                with suppress(Exception):
                    r = await post(c, ep, guard, data=form, headers=headers)
                    ok = ok or r.status_code == 200
    return ok


# —— 远端 MCP ——————————————————————————————————————————————————————————

async def session_run(url: str, transport: str, token: str | None, op: Callable[[ClientSession], Awaitable[Any]]) -> Any:
    """连上（streamable HTTP 或 SSE）、初始化、跑 op。只跟同源的跳转（令牌只给这一个服务）。"""
    seen: list[tuple[str, int]] = []
    base = origin(url)

    async def on_request(req: httpx.Request) -> None:
        if origin(str(req.url)) != base:
            raise AppError(L("对方把请求转到了别的网站，没跟过去", "The server redirected to another site; not followed"))

    async def on_response(resp: httpx.Response) -> None:
        seen.append((resp.request.method, resp.status_code))

    def factory(headers: dict[str, str] | None = None, timeout: httpx.Timeout | None = None, auth: httpx.Auth | None = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(headers=headers, timeout=timeout, auth=auth, follow_redirects=True,
                                 event_hooks={"request": [on_request], "response": [on_response]})

    headers = {"Authorization": f"Bearer {token}"} if token else {}
    info = types.Implementation(name=settings.app_name or "OpenMousse", version="1")
    try:
        with quiet():
            return await _session(url, transport, headers, factory, info, op)
    except Exception as e:
        if any(code == 401 for _, code in seen):
            raise Unauthorized from e
        posts = [code for method, code in seen if method == "POST"]
        if transport == "http" and posts and posts[0] in (404, 405):
            raise WrongTransport from e
        raise


async def _session(url: str, transport: str, headers: dict, factory: Callable[..., httpx.AsyncClient], info: types.Implementation,
                   op: Callable[[ClientSession], Awaitable[Any]]) -> Any:
    if transport == "sse":
        cm = sse_client(url, headers=headers, timeout=30, sse_read_timeout=CALL_TIMEOUT, httpx_client_factory=factory)
    else:
        cm = streamablehttp_client(url, headers=headers, timeout=30, sse_read_timeout=CALL_TIMEOUT, httpx_client_factory=factory)
    async with cm as streams, ClientSession(streams[0], streams[1], read_timeout_seconds=timedelta(seconds=CALL_TIMEOUT), client_info=info) as s:
        await s.initialize()
        return await op(s)


async def remote(aid: str, op: Callable[[ClientSession], Awaitable[Any]]) -> Any:
    """对这个应用做一次操作：带令牌，401 就换个令牌再试一次，单次最长 60 秒，同时最多 4 个。"""
    app = get(aid)
    if not app:
        raise AppError(L("这个连接器已经删掉了", "This connector was removed"))
    name = app["name"]
    token = await bearer(aid)
    async with PARALLEL:
        for attempt in range(2):
            transport = app.get("transport") or "http"
            try:
                with anyio.fail_after(CALL_TIMEOUT):
                    try:
                        return await session_run(app["url"], transport, token, op)
                    except WrongTransport:
                        if transport != "http":
                            raise
                        out = await session_run(app["url"], "sse", token, op)
                        app = change(aid, transport="sse") or app
                        return out
            except Unauthorized:
                if app["auth"] == "none":
                    msg = L(f"{name} 现在要授权了：删掉以后用 OAuth 或令牌重新加", f"{name} now requires authorization: remove it and add it again with OAuth or a token")
                    mark(aid, "error", msg)
                    raise AuthError(msg) from None
                if attempt:
                    msg = L(f"{name} 不认现在的授权：请在 设置 → 连接器 → {name} 里重新连一下",
                            f"{name} rejected the authorization: reconnect it in Settings → Connectors → {name}")
                    mark(aid, "needs_auth", msg)
                    raise AuthError(msg) from None
                token = await bearer(aid, stale=token)
            except TimeoutError:
                raise AppError(L(f"{name} {CALL_TIMEOUT} 秒没回话，停了", f"{name} didn't answer within {CALL_TIMEOUT} seconds")) from None
            except AppError:
                raise
            except Exception as e:  # noqa: BLE001 — 对方出的各种错（HTTP、JSON-RPC、断线）一律变成一句话
                raise AppError(L(f"{name} 出错了：{reason(e)}", f"{name} returned an error: {reason(e)}")) from None
    raise AppError(L(f"{name} 出错了", f"{name} returned an error"))


async def list_op(s: ClientSession) -> list[types.Tool]:
    tools: list[types.Tool] = []
    cursor = None
    for _ in range(20):  # 翻页
        res = await (s.list_tools(params=types.PaginatedRequestParams(cursor=cursor)) if cursor else s.list_tools())
        tools += res.tools
        cursor = res.nextCursor
        if not cursor:
            break
    return tools


def call_op(tool: str, args: dict) -> Callable[[ClientSession], Awaitable[types.CallToolResult]]:
    async def op(s: ClientSession) -> types.CallToolResult:
        # 不用 s.call_tool：它回来后要校验结构化输出，新会话里为此还得再列一遍工具
        return await s.send_request(types.ClientRequest(types.CallToolRequest(params=types.CallToolRequestParams(name=tool, arguments=args))),
                                    types.CallToolResult, request_read_timeout_seconds=timedelta(seconds=CALL_TIMEOUT))
    return op


def clip(text: str) -> str:
    if len(text) <= MAX_OUT:
        return text
    head, tail = text[: MAX_OUT * 2 // 3], text[-MAX_OUT // 4:]
    return f"{head}\n…（{L('中间省略', 'omitted')} {len(text) - len(head) - len(tail)} {L('字', 'chars')}）…\n{tail}"


def result_text(res: types.CallToolResult) -> tuple[str, bool]:
    """工具的结果 → (文字, 是不是出错)。图片、音频、二进制只说一句。"""
    parts = []
    for c in res.content or []:
        if isinstance(c, types.TextContent):
            parts.append(c.text)
        elif isinstance(c, types.EmbeddedResource) and isinstance(c.resource, types.TextResourceContents):
            parts.append(c.resource.text)
        elif isinstance(c, types.EmbeddedResource):
            parts.append(L(f"（附带一个文件：{c.resource.mimeType or '二进制'}，{c.resource.uri}，这里不转交）",
                           f"(an attached file: {c.resource.mimeType or 'binary'}, {c.resource.uri}; not passed on)"))
        elif isinstance(c, types.ResourceLink):
            parts.append(f"({c.name}: {c.uri})")
        elif isinstance(c, (types.ImageContent, types.AudioContent)):
            kind = L("一张图片", "an image") if isinstance(c, types.ImageContent) else L("一段音频", "audio")
            parts.append(L(f"（{kind}，{c.mimeType}，这里不转交）", f"({kind}, {c.mimeType}; not passed on)"))
    if not parts and res.structuredContent is not None:
        parts.append(json.dumps(res.structuredContent, ensure_ascii=False))
    text = "\n".join(p for p in parts if p).strip() or L("（完成，没有输出）", "(done, no output)")
    return clip(text), bool(res.isError)


async def call(aid: str, tool: str, args: dict) -> tuple[str, bool]:
    return result_text(await remote(aid, call_op(tool, args)))


def reads(name: str) -> bool:
    for v in READ_VERBS:
        if name[: len(v)].lower() == v:
            nxt = name[len(v): len(v) + 1]
            if not nxt or not nxt.isalpha() or (nxt.isupper() and name[len(v) - 1].islower()):
                return True
    return False


def kind_of(name: str, ann: dict, prefixes: set[str]) -> str:
    """read / write：readOnlyHint 说了算；没写就看名字（去掉 notion- 这样的服务前缀后是不是 get、list、search… 开头）。"""
    if ann.get("readOnlyHint") is True:
        return "read"
    if ann.get("readOnlyHint") is False:
        return "write"
    for p in sorted(prefixes, key=len, reverse=True):
        if p and name.lower().startswith(p) and name[len(p): len(p) + 1] in ("-", "_", ".", ":"):
            name = name[len(p) + 1:]
            break
    return "read" if reads(name) else "write"


async def refresh_tools(aid: str) -> dict:
    """重新列这个应用的工具，存进 apps.json。"""
    app = get(aid)
    if not app:
        raise AppError(L("这个连接器已经删掉了", "This connector was removed"))
    tools = await remote(aid, list_op)
    prefixes = {aid, str(app.get("catalog") or ""), re.sub(r"[^a-z0-9]+", "", app["name"].lower()), re.sub(r"[^a-z0-9]+", "-", app["name"].lower())}
    rows, seen = [], set()
    for t in tools:
        if not t.name or t.name in seen:
            continue
        seen.add(t.name)
        ann = t.annotations.model_dump(exclude_none=True) if t.annotations else {}
        rows.append({"name": t.name, "title": t.title or ann.get("title") or "", "description": t.description or "",
                     "inputSchema": t.inputSchema if isinstance(t.inputSchema, dict) else {"type": "object"}, "annotations": ann,
                     "kind": kind_of(t.name, ann, prefixes - {""})})
    with _lock:
        apps = all_apps()
        cur = apps.get(aid)
        if cur is None:
            raise AppError(L("这个连接器已经删掉了", "This connector was removed"))
        ov = {k: v for k, v in (cur.get("overrides") or {}).items() if k in seen}
        cur.update(tools=rows, toolsAt=now_iso(), status="connected", error="", overrides=ov, updatedAt=now_iso())
        store("apps.json", {"apps": apps})
    return cur


# —— 权限 ——————————————————————————————————————————————————————————————

def policy_of(app: dict) -> dict:
    p = app.get("policy") if isinstance(app.get("policy"), dict) else {}
    return {k: p.get(k) if p.get(k) in LEVELS else DEFAULT_POLICY[k] for k in DEFAULT_POLICY}


def level_of(app: dict, tool: dict) -> str:
    o = (app.get("overrides") or {}).get(tool["name"])
    return o if o in LEVELS else policy_of(app)[tool.get("kind") if tool.get("kind") in DEFAULT_POLICY else "write"]


def agent_list() -> list[dict]:
    """能给权限的 Agent：main 在前，然后是 groups 表里的，再是只在 server.json 里有 workspace 的。"""
    out, seen = [{"id": "main", "name": settings.app_name}], {"main"}
    try:
        with chat._lock, data.ddb() as conn:
            rows = conn.execute("SELECT id, name FROM groups ORDER BY position, created_at").fetchall()
    except sqlite3.Error:
        rows = []
    for r in rows:
        if r["id"] not in seen:
            out.append({"id": r["id"], "name": r["name"]})
            seen.add(r["id"])
    for aid in settings.agent_workspaces:
        if aid not in seen:
            out.append({"id": aid, "name": aid})
            seen.add(aid)
    return out


def agent_name(aid: str) -> str:
    return inbox.source_name(aid, inbox.names())


def short(text: str, n: int = 200) -> str:
    s = re.sub(r"\s+", " ", text or "").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def first_sentence(text: str, n: int = 140) -> str:
    s = re.sub(r"\s+", " ", text or "").strip()
    m = re.search(r"(?<=[.!?。！？])\s", s)
    return short(s[: m.start()] if m else s, n)


def public(app: dict) -> dict:
    tools = app.get("tools") or []
    reads_n = sum(1 for t in tools if t.get("kind") == "read")
    look = catalog().get(app.get("catalog") or "") or app
    return {"id": app["id"], "name": app["name"], "url": app["url"], "catalog": app.get("catalog"), "custom": not app.get("catalog"),
            "category": look.get("category") or "common", "desc": text_of(look.get("desc")), "auth": app["auth"],
            "mono": str(look.get("mono") or initials(app["name"]))[:3], "bg": look.get("bg") or "#4B5563", "fg": look.get("fg") or "#FFFFFF",
            "border": look.get("border"), "status": app.get("status") or "needs_auth", "error": app.get("error") or "",
            "account": app.get("account") or "", "connectedAt": app.get("connectedAt"), "createdAt": app.get("createdAt"),
            "updatedAt": app.get("updatedAt"), "toolsAt": app.get("toolsAt"), "toolCount": len(tools), "readCount": reads_n,
            "writeCount": len(tools) - reads_n, "offCount": sum(1 for t in tools if level_of(app, t) == "off"), "policy": policy_of(app),
            "overrides": dict(app.get("overrides") or {}), "agents": list(app.get("agents") or [])}


def detail(aid: str) -> dict:
    app = must(aid)
    ov = app.get("overrides") or {}
    return {**public(app), "tools": [{"name": t["name"], "title": t.get("title") or "", "description": short(t.get("description") or ""),
                                      "kind": t.get("kind") or "write", "level": level_of(app, t), "overridden": ov.get(t["name"]) in LEVELS}
                                     for t in app.get("tools") or []]}


# —— /mcp 上的工具 ————————————————————————————————————————————————————————

def mcp_name(aid: str, tool: str) -> str:
    raw_name = f"{aid}__{tool}"
    name = re.sub(r"[^A-Za-z0-9_-]", "_", raw_name)
    if name == raw_name and len(name) <= 64:
        return name
    return f"{name[:57]}_{hashlib.sha256(raw_name.encode()).hexdigest()[:6]}"


def exposed() -> dict[str, tuple[dict, dict]]:
    """/mcp 上的名字 → (应用, 工具)：连上的应用里没关掉的工具。"""
    out: dict[str, tuple[dict, dict]] = {}
    apps = all_apps()
    for aid in sorted(apps):
        app = apps[aid]
        if app.get("status") != "connected":
            continue
        for t in app.get("tools") or []:
            if level_of(app, t) != "off":
                out[mcp_name(aid, t["name"])] = (app, t)
    return out


def agent_key(tool: dict) -> str:
    props = (tool.get("inputSchema") or {}).get("properties")
    return "mousse_agent" if isinstance(props, dict) and "agent" in props else "agent"


def tool_def(name: str, app: dict, t: dict) -> types.Tool:
    schema = copy.deepcopy(t.get("inputSchema")) if isinstance(t.get("inputSchema"), dict) else {}
    props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    schema.update(type="object", properties={**props, agent_key(t): {"type": "string", "description": L(
        "你是哪个 Agent（它的 id，比如 diet）；主对话不填。这个应用要在用户的设置里给了这个 Agent 才能用",
        "Which Agent you are (its id, e.g. diet); leave empty in the main chat. The user has to allow that Agent for this app in settings")}})
    desc = (t.get("description") or t.get("title") or t["name"]).strip()
    if len(desc) > MAX_DESC:
        desc = desc[:MAX_DESC] + "…"
    text = f"[{app['name']}] {desc}"
    if level_of(app, t) == "ask":
        text += "\n\n" + L("（这个工具每次都要用户先点头：调用会给他发一张卡，马上返回；他同意后服务器照原样调用一次，把结果发进你的对话。）",
                           "(Each call needs the user's OK first: calling it sends them a card and returns right away; once they approve, the server "
                           "makes exactly that call and posts the result into your chat.)")
    title = f"{app['name']}: {t['title']}" if t.get("title") else None
    return types.Tool(name=name, title=title, description=text, inputSchema=schema, annotations=t.get("annotations") or None)


async def mcp_tools(who: dict) -> list[types.Tool]:
    """给 mcp_bridge 列工具用（所有令牌看到的一样；能不能用在调的时候按 Agent 查）。"""
    return [tool_def(name, app, t) for name, (app, t) in exposed().items()]


async def mcp_call(name: str, arguments: dict, who: dict) -> types.CallToolResult | None:
    """给 mcp_bridge 调工具用：不是连接器的工具回 None。"""
    hit = exposed().get(name)
    if hit is None:
        if "__" not in name:
            return None
        return failed(L(f"没有 {name} 这个工具了：连接器可能断开了，或者这个工具在 设置 → 连接器 里关掉了",
                        f"{name} isn't available: the connector may be disconnected, or the tool turned off in Settings → Connectors"))
    app, t = hit
    try:
        text = await run_tool(app, t, dict(arguments or {}), who)
    except (ToolError, AppError) as e:
        return failed(str(e))
    except Exception as e:  # noqa: BLE001
        return failed(L(f"出错了：{reason(e)}", f"Something went wrong: {reason(e)}"))
    return types.CallToolResult(content=[types.TextContent(type="text", text=text)], isError=False)


def failed(text: str) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=text)], isError=True)


async def run_tool(app: dict, t: dict, args: dict, who: dict) -> str:
    name = app["name"]
    asked = str(args.pop(agent_key(t), "") or "").strip()
    bound = who.get("agent")
    if bound and asked and asked != bound:
        raise ToolError(L(f"这把令牌是 {bound} 的，不能替 {asked} 做事", f"This token belongs to {bound}; it can't act for {asked}"))
    agent = bound or asked or "main"
    if agent not in (app.get("agents") or []):
        raise ToolError(L(f"{agent_name(agent)}还不能用 {name}：请用户在 设置 → 连接器 → {name} 里给它打开",
                          f"{agent_name(agent)} can't use {name} yet: ask the user to allow it in Settings → Connectors → {name}"))
    level = level_of(app, t)
    if level == "off":
        raise ToolError(L(f"{name} 的这个工具关掉了（设置 → 连接器 → {name}）", f"This {name} tool is turned off (Settings → Connectors → {name})"))
    if len(json.dumps(args, ensure_ascii=False)) > MAX_ARGS:
        raise ToolError(L("参数太长了", "The arguments are too long"))
    schema = t.get("inputSchema")
    if isinstance(schema, dict):
        try:
            jsonschema.validate(args, schema)
        except jsonschema.ValidationError as e:
            raise ToolError(L(f"参数不对：{clean(e.message, 300)}", f"Invalid arguments: {clean(e.message, 300)}")) from None
        except Exception:  # noqa: BLE001 — 对方的 schema 本身有问题（或引用了外面的 schema）：不在这里查，交给对方
            pass
    if level == "auto":
        text, is_error = await call(app["id"], t["name"], args)
        if is_error:
            raise ToolError(text)
        return text
    return await ask(app, t, args, agent)


# —— ask：收件箱卡 ——————————————————————————————————————————————————————

def thread_of(agent: str) -> str:
    """卡挂在哪个对话：那个 Agent 的（Agent 的线程 id 就是它的 id）；认不出就 main。"""
    return agent if agent == "main" or agent in inbox.names() else "main"


def fence_for(text: str) -> str:
    run = max((len(m) for m in re.findall(r"`+", text)), default=0)
    return "`" * max(3, run + 1)


def arg_lines(args: dict, n: int = 5) -> list[str]:
    out = []
    for k, v in list(args.items())[:n]:
        s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
        out.append(f"{k}: {short(s, 80)}")
    if len(args) > n:
        out.append(L(f"还有 {len(args) - n} 项，见细节", f"{len(args) - n} more in the details"))
    return out


def card_of(app: dict, t: dict, args: dict, agent: str) -> dict:
    label = t.get("title") or t["name"]
    who = agent_name(agent)
    what = first_sentence(t.get("description") or "")
    pretty = json.dumps(args, ensure_ascii=False, indent=2)
    fence = fence_for(pretty)
    return {"title": L(f"用 {app['name']}：{label}", f"{app['name']}: {label}"),
            "why": L(f"{who}想用 {app['name']} 的「{label}」。", f"{who} wants to use {label} in {app['name']}.") + (f" {what}" if what else ""),
            "changes": arg_lines(args),
            "detail": L("同意后，服务器照下面的参数调用一次，结果发回对话。", "If you approve, the server makes exactly this call once and posts the result to the chat.")
            + f"\n\n{fence}json\n{pretty}\n{fence}",
            "approveLabel": L("照做", "Do it")}


def card_hash(title: str, why: str, changes: list[str], detail_md: str) -> str:
    """卡上给你看的内容的指纹（和 inbox.add 存的一样先 strip）：同意时对一下，卡被改过就不照做。"""
    body = json.dumps([title.strip(), why.strip(), [c.strip() for c in changes if c and c.strip()], detail_md.strip()], ensure_ascii=False)
    return hashlib.sha256(body.encode()).hexdigest()


def call_of(iid: str) -> dict | None:
    """收件箱卡 → 它背后那次调用（calls.json，按卡的 dedupe 找）。"""
    calls = load("calls.json")
    for c in calls.values():
        if isinstance(c, dict) and c.get("inbox") == iid:
            return c
    with chat._lock, inbox.idb() as conn:
        r = conn.execute("SELECT dedupe FROM inbox WHERE id=? AND kind='app'", (iid,)).fetchone()
    c = calls.get(r["dedupe"]) if r and r["dedupe"] else None
    return c if isinstance(c, dict) else None


async def ask(app: dict, t: dict, args: dict, agent: str) -> str:
    """ask 档：交一张收件箱卡，马上返回。同一次调用还在等 → 还是那张。"""
    name = app["name"]
    dedupe = "app:" + hashlib.sha256(json.dumps([app["id"], t["name"], args], ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:32]
    with chat._lock, inbox.idb() as conn:
        inbox.expire(conn)
        r = conn.execute("SELECT id FROM inbox WHERE dedupe=? AND status IN ('pending','revising') ORDER BY created_at DESC LIMIT 1", (dedupe,)).fetchone()
    if r:
        return L(f"这次调用已经在等用户点头了（收件箱卡 {r['id']}）：别再调。他同意后服务器会照做，结果发进你的对话。",
                 f"This exact call is already waiting for the user's OK (inbox card {r['id']}): don't call it again. Once they approve, "
                 "the server makes the call and posts the result into your chat.")
    card = card_of(app, t, args, agent)
    with _lock:
        cutoff = time.time() - 60 * 86400
        calls = {k: v for k, v in load("calls.json").items() if isinstance(v, dict) and (v.get("ts") or 0) > cutoff}
        calls[dedupe] = {"app": app["id"], "tool": t["name"], "args": args, "agent": agent, "ts": time.time(), "createdAt": now_iso(),
                         "card": card_hash(card["title"], card["why"], card["changes"], card["detail"])}
        store("calls.json", calls)
    res = await inbox.add(inbox.ItemIn(kind="app", source=agent, thread=thread_of(agent), dedupe=dedupe, **card))
    if not isinstance(res, dict) or not res.get("ok"):  # 409：30 天内拒过一模一样的
        try:
            body = json.loads(bytes(res.body)) if isinstance(res, JSONResponse) else {}
        except ValueError:
            body = {}
        when, note = str(body.get("rejectedAt") or "")[:10], clean(body.get("note"), 200)
        raise ToolError(L(f"用户{when}拒过一模一样的这次调用" + (f"（{note}）" if note else "") + "：别再提，除非他又让你做",
                          f"The user declined this exact call on {when}" + (f" ({note})" if note else "") + ": don't ask again unless they bring it up"))
    iid = res["id"]
    with _lock:
        calls = load("calls.json")
        if isinstance(calls.get(dedupe), dict):
            calls[dedupe]["inbox"] = iid
            store("calls.json", calls)
    return L(f"已经交给用户点头（收件箱卡 {iid}）。他同意后，服务器会照这次的参数调用一次 {name}，把结果发进你的对话；"
             "别再调这一次，也别换个办法绕过去。现在跟用户说一声在等他点头就行。",
             f"Sent to the user for approval (inbox card {iid}). Once they approve, the server calls {name} once with exactly these "
             "arguments and posts the result into your chat; don't call it again or work around it. Just tell the user it's waiting for their OK.")


async def on_decided(it: dict, action: str) -> dict | None:
    """inbox.HOOKS["app"]：同意 → 照卡上的原样调一次；改一下 → 让它改好参数重新来；拒绝、撤回 → 远端什么都不动。"""
    t, i = it["title"], it["id"]
    if action == "revise":
        note = (it.get("note") or "").strip().rstrip("。.")
        inbox.kick(it["thread"], inbox.MARK + LS(
            f"「{t}」（{i}）没有照做：用户要改一下：{note}。卡上的参数改不了：按他说的改好参数，再调用一次那个工具（会出一张新卡等他点头）。",
            f'"{t}" ({i}) was not run: the user wants a change: {note}. The card\'s arguments can\'t be edited: call the tool again with the '
            "arguments changed as they say (that makes a new card for their OK)."))
        return {"silent": True, "status": "done", "result": L("没照做：已经让它按你说的改好了再来", "Not run: asked it to redo the call with your change")}
    if action != "approve":
        return None
    c = call_of(i)
    app = get(c["app"]) if c else None
    if not c:
        why = L("找不到这张卡背后的调用，没有照做", "Couldn't find the call behind this card, so nothing was run")
    elif c.get("card") != card_hash(it["title"], it["why"], it["changes"], it["detail"]):
        why = L("这张卡交上来以后被改过，和要调用的对不上，没有照做", "This card was edited after it was sent, so it no longer matches the call; nothing was run")
    elif not app:
        why = L("这个连接器已经删掉了，没有照做", "That connector was removed, so nothing was run")
    else:
        try:
            text, is_error = await call(app["id"], c["tool"], c["args"])
        except AppError as e:
            text, is_error = str(e), True
        if not is_error:
            return {"result": L(f"照做了，{app['name']} 回的是：\n{text}", f"it was run; {app['name']} returned:\n{text}")}
        why = L(f"{app['name']} 说没成功：{text}", f"{app['name']} said it failed: {text}")
    # 没做成：inbox 把卡标成「没做成」、不再让 Agent 去做；但它答应过用户会有结果，告诉它一声
    inbox.kick(it["thread"], inbox.MARK + LS(f"已同意「{t}」（{i}），但没做成：{why.rstrip('。.')}。不用再报；跟用户说一声，别自己重试，除非他让你再来。",
                                            f'Approved "{t}" ({i}), but it didn\'t work: {why.rstrip(".")}. No need to report; tell the user, '
                                            "and don't retry unless they ask."))
    return {"failed": why}


def card_extra(iid: str) -> dict | None:
    """inbox.EXTRAS["app"]：卡上多给 app 的：哪个应用、哪个工具、原样参数、谁要的。"""
    c = call_of(iid)
    if not c:
        return None
    app = get(c["app"]) or {}
    look = public(app) if app else {}
    return {"app": c["app"], "appName": app.get("name") or c["app"], "tool": c["tool"], "args": c["args"], "agent": c["agent"],
            "mono": look.get("mono"), "bg": look.get("bg"), "fg": look.get("fg"), "border": look.get("border")}


def withdraw_cards(aid: str) -> None:
    """删掉一个应用：它还在等你点头的卡跟着撤回。"""
    keys = [k for k, c in load("calls.json").items() if isinstance(c, dict) and c.get("app") == aid]
    if not keys:
        return
    with chat._lock, inbox.idb() as conn:
        for k in keys:
            conn.execute("UPDATE inbox SET status='withdrawn', updated_at=? WHERE kind='app' AND dedupe=? AND status IN ('pending','revising')",
                         (now_iso(), k))


inbox.HOOKS["app"] = on_decided
inbox.EXTRAS["app"] = card_extra
mcp_bridge.EXTRA.append((mcp_tools, mcp_call))


# —— 接口 ————————————————————————————————————————————————————————————————

@router.get("/api/apps")
def list_apps():
    """连着的应用、目录（installed = 已经加了）、能给权限的 Agent。"""
    apps = all_apps()
    return {"ok": True, "apps": [public(a) for a in sorted(apps.values(), key=lambda a: a.get("createdAt") or "")],
            "catalog": [catalog_json(e, e["id"] in apps) for e in catalog().values()], "agents": agent_list()}


class AddIn(BaseModel):
    catalog: str | None = None    # 目录里的 id；不给 = 自定义（name、url、auth）
    name: str | None = None
    url: str | None = None
    auth: str | None = None       # oauth（默认）/ token / none
    token: str | None = None      # auth token：个人访问令牌之类
    redirect_uri: str | None = None
    redirectUri: str | None = None


@router.post("/api/apps")
async def add_app(body: AddIn):
    """加一个应用：oauth → {app, authorizeUrl, state}（手机打开授权网址）；token / none → 现在就连上、列工具 → {app}。
    已经连上的 409（重新授权用 /connect）。连不上就什么都不留（已经有的只记下错误）。"""
    if body.catalog:
        e = catalog().get(body.catalog.strip())
        if not e:
            raise HTTPException(404, L("目录里没有这个应用", "No such app in the catalog"))
        aid, name, url, auth, custom = e["id"], e["name"], e["url"], e["auth"], False
    else:
        name = (body.name or "").strip()
        if not name or len(name) > 40:
            raise HTTPException(400, L("名字要写（最多 40 个字）", "A name is required (up to 40 characters)"))
        url = await check_url(body.url)
        auth = (body.auth or "oauth").strip().lower()
        if auth not in AUTHS:
            raise HTTPException(400, L("auth 只能是 oauth / token / none", "auth must be oauth, token or none"))
        same = next((a for a in all_apps().values() if not a.get("catalog") and a.get("url") == url), None)
        aid, custom = (same["id"] if same else new_id(name)), True
    cur = get(aid)
    if cur and cur.get("status") == "connected":
        raise HTTPException(409, L(f"{name} 已经连上了；要重新授权或换账号，用「重新连接」", f"{name} is already connected; use reconnect to authorize again or switch accounts"))
    redirect = check_redirect(body.redirect_uri or body.redirectUri) if auth == "oauth" else ""
    token = (body.token or "").strip()
    if auth == "token" and not token:
        hint = text_of((catalog().get(aid) or {}).get("hint")) if not custom else ""
        raise HTTPException(400, L("要填令牌", "A token is required") + (L(f"：{hint}", f": {hint}") if hint else ""))
    ts = now_iso()
    app = {**(cur or {}), "id": aid, "name": name, "url": url, "auth": auth, "catalog": None if custom else aid, "status": "needs_auth", "error": "",
           "createdAt": (cur or {}).get("createdAt") or ts, "updatedAt": ts, "policy": policy_of(cur or {}), "overrides": (cur or {}).get("overrides") or {},
           "agents": (cur or {}).get("agents") or ["main"], "tools": (cur or {}).get("tools") or []}
    if custom:
        app.update(mono=initials(name), bg=TILE_COLORS[int(hashlib.sha256(aid.encode()).hexdigest(), 16) % len(TILE_COLORS)], fg="#FFFFFF")
    if auth == "oauth":
        try:
            got = await begin(app, redirect)
        except AppError as e:
            if cur:
                change(aid, error=str(e))
            raise HTTPException(502, str(e)) from None
        put(app)
        log_activity(L(f"开始连接 {name}", f"Started connecting {name}"), "edit")
        return {"ok": True, "app": detail(aid), **got}
    put(app)
    old = secret(aid)
    set_secret(aid, {"token": token} if auth == "token" else None)
    try:
        await refresh_tools(aid)
    except AppError as e:
        if cur:
            set_secret(aid, old)
            change(aid, status=cur.get("status") or "needs_auth", error=str(e))
        else:
            forget(aid)
        if isinstance(e, AuthError):
            raise HTTPException(400, L(f"{name} 不认这个令牌", f"{name} didn't accept this token") if auth == "token" else str(e)) from None
        raise HTTPException(502, str(e)) from None
    change(aid, connectedAt=now_iso())
    log_activity(L(f"连上了 {name}", f"Connected {name}"), "edit")
    return {"ok": True, "app": detail(aid)}


class CallbackIn(BaseModel):
    state: str = ""
    code: str | None = None
    error: str | None = None
    error_description: str | None = None
    iss: str | None = None  # RFC 9207：带了就核对


@router.post("/api/apps/oauth/callback")
async def oauth_callback(body: CallbackIn):
    """app 收到 <scheme>://oauth/callback?code&state（或 error）以后交上来：换令牌、列工具 → {app}。"""
    return {"ok": True, "app": await finish(body.state.strip(), (body.code or "").strip(), (body.error or "").strip(),
                                            (body.error_description or "").strip(), (body.iss or "").strip())}


class ConnectIn(BaseModel):
    redirect_uri: str | None = None
    redirectUri: str | None = None
    token: str | None = None  # auth token 的应用：换一个令牌


@router.post("/api/apps/{aid}/connect")
async def connect(aid: str, body: ConnectIn):
    """重新连：oauth → {authorizeUrl, state}（重新授权、换账号）；token → 换令牌再列工具；none → 再列一次工具。都带上 app。"""
    app = must(aid)
    name = app["name"]
    if app["auth"] == "oauth":
        redirect = check_redirect(body.redirect_uri or body.redirectUri)
        try:
            got = await begin(app, redirect)
        except AppError as e:
            raise HTTPException(502, str(e)) from None
        return {"ok": True, "app": detail(aid), **got}
    if app["auth"] == "token":
        token = (body.token or "").strip()
        if not token:
            raise HTTPException(400, L("要填新的令牌", "A new token is required"))
        old = secret(aid)
        set_secret(aid, {"token": token})
        try:
            await refresh_tools(aid)
        except AuthError:  # 新令牌不对：旧的放回去，状态也回到原来的
            set_secret(aid, old)
            change(aid, status=app.get("status") or "needs_auth", error=app.get("error") or "")
            raise HTTPException(400, L(f"{name} 不认这个令牌", f"{name} didn't accept this token")) from None
        except AppError as e:
            raise HTTPException(502, str(e)) from None
        change(aid, connectedAt=now_iso())
        return {"ok": True, "app": detail(aid)}
    try:
        await refresh_tools(aid)
    except AppError as e:
        raise HTTPException(502, str(e)) from None
    return {"ok": True, "app": detail(aid)}


@router.get("/api/apps/{aid}")
def app_detail(aid: str):
    return {"ok": True, "app": detail(aid)}


class PatchIn(BaseModel):
    policy: dict[str, str] | None = None             # {"read": "auto", "write": "ask"}
    overrides: dict[str, str | None] | None = None   # {工具名: auto / ask / off，null = 跟着 policy}
    agents: list[str] | None = None                  # 能用的 Agent


@router.patch("/api/apps/{aid}")
def patch_app(aid: str, body: PatchIn):
    app = must(aid)
    sets: dict = {}
    if body.policy is not None:
        pol = policy_of(app)
        for k, v in body.policy.items():
            if k not in DEFAULT_POLICY or v not in LEVELS:
                raise HTTPException(400, L("policy 是 {read, write}，每项 auto / ask / off", "policy is {read, write}, each auto, ask or off"))
            pol[k] = v
        sets["policy"] = pol
    if body.overrides is not None:
        names = {t["name"] for t in app.get("tools") or []}
        ov = dict(app.get("overrides") or {})
        for k, v in body.overrides.items():
            if k not in names:
                raise HTTPException(400, L(f"没有「{k}」这个工具", f"No tool called {k}"))
            if v is None:
                ov.pop(k, None)
            elif v in LEVELS:
                ov[k] = v
            else:
                raise HTTPException(400, L("每个工具只能是 auto / ask / off（null = 跟着默认）", "Each tool must be auto, ask or off (null = follow the default)"))
        sets["overrides"] = ov
    if body.agents is not None:
        known = {a["id"] for a in agent_list()}
        ids = list(dict.fromkeys(a.strip() for a in body.agents if a and a.strip()))
        bad = [a for a in ids if a not in known]
        if bad:
            raise HTTPException(400, L(f"没有「{bad[0]}」这个 Agent", f"No Agent called {bad[0]}"))
        sets["agents"] = ids
    if not sets:
        raise HTTPException(400, L("没有要改的", "Nothing to change"))
    change(aid, **sets)
    log_activity(L(f"改了 {app['name']} 的权限", f"Changed {app['name']}'s permissions"), "edit")
    return {"ok": True, "app": detail(aid)}


@router.post("/api/apps/{aid}/refresh")
async def refresh(aid: str):
    """重新列一次工具（对方加了新工具之类）。"""
    must(aid)
    try:
        await refresh_tools(aid)
    except AppError as e:
        raise HTTPException(502, str(e)) from None
    return {"ok": True, "app": detail(aid)}


@router.delete("/api/apps/{aid}")
async def delete_app(aid: str):
    """断开：先到授权服务器撤销令牌（尽力而为），再忘掉令牌和这一项；还在等你点头的卡撤回。"""
    app = must(aid)
    revoked = False
    with suppress(Exception):
        revoked = await revoke(app)
    forget(aid)
    withdraw_cards(aid)
    log_activity(L(f"断开了 {app['name']}", f"Disconnected {app['name']}"), "deleted")
    return {"ok": True, "revoked": revoked}
