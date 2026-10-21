"""用户自己的 Grok 订阅（SuperGrok / X Premium+，2026-10-20）：xAI 的 OAuth 设备码登录（账号服务 accounts.x.ai / auth.x.ai），不要
API 密钥、不按量付费；登录以后 grok/<型号> 跑对话（engine/models.py 认它是套餐），这台服务器拿到用户的令牌调 https://api.x.ai/v1。
ChatGPT 那一半在 engine/chatgpt_plan.py，Claude 那一半在 engine/claude_brain.py。

参考（10/20 现拉的源码，只读）：Hermes Agent（NousResearch/hermes-agent，MIT，Copyright (c) 2025 Nous Research）commit 49268ca6 的
hermes_cli/auth_xai.py、auth_constants.py、auth_device_flow.py 的 _poll_device_token_generic：共享的 client_id、scope、发现文档、端点只认
https 的 x.ai、设备码轮询（authorization_pending 等着、slow_down 拉长间隔、408 / 429 / 5xx / 边缘的拦截页退避）、刷新时 403 是账号档位不够
（重登没用，改用 API 密钥）、短命令牌提前两分钟刷新、长的提前一小时；照着它的做法用 Python 重写，常量照搬（许可全文见仓库根目录的
THIRD_PARTY_NOTICES）。OpenClaw（openclaw/openclaw，MIT）bd2dbfde 的 extensions/xai/xai-oauth.ts（设备码的字段、拒绝 / 过期的说法、
刷新只在 Cloudflare 的网页挑战时重试——别的错可能已经把刷新令牌用掉了、老的令牌端点 /oauth/token 要重新发现）、provider-catalog.ts
（订阅的型号目录、去掉多 agent 的型号）、stream.ts（Grok CLI 代理那条路要带的三个请求头）、index.ts（SuperGrok 的用量窗口）：只借做法。

不是 xAI 点名支持的接入（10/21 定的说法，依据是 xAI 自己的公告和条款）：xAI 点名能用订阅登录的只有几家第三方 agent（Hermes、OpenClaw、
  OpenCode、Kilo Code、Warp），OpenMousse 不在里面；这里借的是它们共用的那个 OAuth 登录入口（同意页上显示 Grok Build）。README 和给用户看的字
  不写「官方支持」，写明 xAI 随时可能拒（403），被拒了改用 API 密钥（login_note、denied_text）。
授权（RFC 8628 设备码，借 xAI 给几家点名的第三方 agent 共用的那一条路）：
  发现文档 GET https://auth.x.ai/.well-known/openid-configuration：device_authorization_endpoint、token_endpoint（revocation_endpoint 有就记下）。
    端点只认 https 的 x.ai / *.x.ai（发现文档被换了，刷新令牌也不会发到别人那里；Hermes、OpenClaw 都这么钉）。
  POST 设备码端点 {client_id, scope} → device_code、user_code、verification_uri(_complete)、expires_in、interval。client_id 用 xAI 给第三方
    agent 共享的那一个（OpenClaw、Hermes 都用它）：同意页上可能写着「Grok Build」。scope = openid profile email offline_access grok-cli:access
    api:access（offline_access 才有刷新令牌）。
  这台服务器在后台按 interval 轮询令牌端点（grant_type = urn:ietf:params:oauth:grant-type:device_code）；用户在手机或电脑的浏览器里打开网址、
    认一下码、点同意。不要回调、不要 SSH 转发：服务器没有浏览器也一样。
  拒绝：用户在 xAI 那边点了拒绝（access_denied）、码过期（expired_token）各说各的；xAI 回 403 = 这个账号现在拿不到 OAuth 令牌（哪些账号能拿由
    xAI 定，普通 SuperGrok 有人遇到过）：说清楚，提示改用 API 密钥（xai/<型号> + XAI_API_KEY，按量计费）。
令牌（照 ChatGPT 套餐的存法）：<data_dir>/grok/（目录 0700，文件 0600、先写临时文件再改名、目录里放 .gitignore）：account.json（访问令牌、
  刷新令牌、ID token、到期时间、令牌端点、邮箱、账号的 sub）、state.json（不含令牌：后台同意没有、型号目录、最近一次的错、要不要重登、被拒的原因）、
  refresh.lock。不进日志、不进网址、不回给 app（状态里只有连没连、邮箱、快过期没有）。
  刷新：访问令牌按它的寿命提前刷新（寿命的六分之一，夹在 2 分钟到 1 小时之间：6 小时的令牌提前 1 小时，15 分钟的提前 2.5 分钟）；同一份凭据的
  刷新排队（进程里一把锁 + 文件锁，锁里重读文件，别人刚刷过就直接用新的）；xAI 每次都换新的刷新令牌，新旧一起写。
  刷新说令牌不能用了（400 / 401：invalid_grant、refresh_token_reused……）：清掉令牌、标「要重登」；403：账号档位不够（标「被拒」、提示 API 密钥）；
  连不上 / 5xx / 429：凭据不动，这一次报能重试的错（令牌还没过期就先用着旧的）。推理回 401：强制刷新一次再试，还是 401 标「要重登」。
  退出：发现文档有 revocation_endpoint 就撤销刷新令牌，再删掉本机这一份；没确认撤销也删，告诉用户去 accounts.x.ai 的账号设置里撤销。
谁能花（engine/models.py 的 may_spend，照 ChatGPT 套餐的规矩）：grok/<型号> 是套餐。用户自己引出来的（user、relay）能花；后台的（auto：定时任务、
  心跳、收件箱和后台任务接着做的；task：后台任务自己）要用户在设置里明确同意过一次（PUT /api/engine/grok/background，记下时间；和 ChatGPT 的
  同意分开记）；别人的回合（名片 Agent、A2A、Doorman、赛博、外人）、对外接口（/v1、ACP）、经 /mcp 引出来的一律不花，换成 outside() 挑的
  按量模型，挑不到就不跑、说清楚为什么。
推理：grok/<型号> 经 providers.stream 走 provider_responses（xAI 的 Responses API：store = false、思考项加密回放），每次现取访问令牌（快过期
  先刷新）；回 401 强制刷新一次再试，还是 401 标要重登；回 403 换成说清楚的一句（这个账号拿不到 OAuth、改用 API 密钥）。默认发到
  https://api.x.ai/v1（Hermes 的做法）；engine.grok.route = "proxy" 改走 Grok CLI 的订阅代理 https://cli-chat-proxy.grok.com/v1（OpenClaw
  的做法，带它要的三个请求头）：只有这两个地址，令牌不会发到别处。
别的接口（api()：同一份令牌调 api.x.ai，401 刷新一次再试、403 说清楚、429 提示额度可能用完了）：搜网页（engine/web.py 的 grok：登录了就先走
  订阅，engine.grok.search = false 不走）；搜 X（engine/xsearch.py 的 x_search：默认不走订阅，engine.grok.x_search = true 才走——10/21 定：
  xAI 没写过 x_search 算不算在订阅里，API 那条路按抓到的帖子收钱）；生图、生视频、朗读（engine/grok_media.py：engine.use.image / video_gen /
  tts 写成 grok/<型号> 才用，默认关）。工具这一轮能不能花照 may_use（就是 may_spend 的规矩）。
型号：grok/<型号>。登录了才出现在 app 能切换的模型、按用途选模型的地方（主对话、后台、写作；外人回合不列）：engine.grok.models 写了就用它，
  不写用这个账号的目录（对话地址的 GET /models，登录时和之后每 6 小时在后台取一次；只留对话的型号：生图、生视频、朗读、向量的不列；
  多 agent 的型号（grok-4.20-multi-agent…，Heavy 用的）也不列——它不收我们每一轮都带的工具，OpenClaw 也不开放；要试就写进 engine.grok.models）。
  默认挑对话的那个：目录里有 grok-4.7（10/20 OpenClaw 的默认）就是它，没有就是目录里第一个，排在最前（按用途选模型的推荐就是它）。
  目录还没取到（刚登录就断网、/models 不回）：先只列 grok/grok-4.7，状态里写明不是从账号拿的。
接口（只认主人的设备：app 的令牌或 Tailscale 里自己的设备，带 X-Mousse-Via 的脚本一律 403，同 /api/setup）：
  GET    /api/engine/grok           状态（连没连、邮箱、快过期没有、要不要重登、被拒的原因、最近一次的错、后台同意没有、登录进行到哪）
  POST   /api/engine/grok/login     开始设备码登录 → {"url", "verificationUri", "code", "expiresAt", "interval"}（打开网址、认码、同意）
  GET    /api/engine/grok/login     登录进行到哪（pending / done / denied / error / expired / cancelled）
  DELETE /api/engine/grok/login     取消
  POST   /api/engine/grok/logout    退出（撤销 + 删掉本机令牌）
  PUT    /api/engine/grok/background  {"allow": true | false}：后台任务、定时任务、心跳能不能花订阅（记下改的时间；默认不能）
  GET    /api/engine/grok/usage     这一段（一周 / 一个月）的额度用了多少、什么时候重置（当场问 xAI；状态里的 quota 是存着的那份）
额度：订阅代理的 GET /v1/billing?format=credits（OpenClaw 照 Grok CLI 问的那个，带 x-grok-client-mode: cli）→ 套餐名、这一段用了百分之几、
  什么时候重置；存进 state.json（不含令牌），旧了（QUOTA_EVERY）在后台重取。只算订阅里含的那一份：API 的预付余额是按量的另一本账，不算。
  用量页（/api/setup/usage 的 plans.grok）、模型页、python3 setup_ctl.py grok / usage 都看得到。
命令行：python3 setup_ctl.py grok [login | logout | background on | background off]（登录在服务器的终端里打出网址和码，等你点同意）。
server.json 的 engine.grok（可选，每次现读）：false = 关掉；{"models": ["grok/grok-4.7", …]}：app 里列哪几个（不写用账号的目录，型号可以不带
  grok/）；{"route": "proxy"}：对话改走 Grok CLI 的订阅代理（见上）；{"headers": {…}}：推理请求多带的请求头；{"search": false}：搜网页不走订阅，
  只用 XAI_API_KEY（默认 true）；{"x_search": true}：x_search 先走订阅（默认 false：只用 XAI_API_KEY，没有密钥就没有这个工具）。
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import fcntl
import json
import os
import re
import time
import weakref
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, Request

from config import settings
from i18n import L

from .chatgpt_plan import read_json, write_private
from .errors import EngineError

ISSUER = "https://auth.x.ai"                 # xAI 的账号服务（发现文档在这里；测试时换成假的服务）
CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"  # xAI 给第三方 agent 共享的 OAuth client（Hermes、OpenClaw 用的同一个）
SCOPE = "openid profile email offline_access grok-cli:access api:access"
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
API = "https://api.x.ai/v1"                  # 推理、搜索、生图、生视频、朗读（测试时换成假的服务）
PROXY = "https://cli-chat-proxy.grok.com/v1"  # Grok CLI 的订阅代理（OpenClaw 的对话走它；engine.grok.route = "proxy" 才用）
ACCOUNT_URL = "https://accounts.x.ai"        # 用户自己管账号、撤销授权的地方
LOGIN_MAX = 1800                             # 一次登录最多等多久（秒；xAI 给的 expires_in 更短就照它的）
INTERVAL, MIN_INTERVAL, SLOW_DOWN = 5.0, 1.0, 5.0  # 轮询间隔：默认、最短、slow_down 一次加多少（RFC 8628）
EDGE_MAX = 60.0                              # 边缘拦截页 / 5xx 的退避最多等多久
REFRESH_MIN, REFRESH_MAX = 120.0, 3600.0     # 提前刷新：寿命的六分之一，夹在这两个之间（秒）
REFRESH_TRIES, REFRESH_RETRY = 3, 0.25       # 刷新只在 Cloudflare 的网页挑战时重试（OpenClaw：别的错可能已经把刷新令牌用掉了）
REVOKE_DELAYS = (1.0, 3.0)                   # 撤销遇到网络错 / 5xx：隔几秒再试
TERMINAL = {"invalid_grant", "invalid_token", "invalid_refresh_token", "refresh_token_reused", "refresh_token_expired", "unauthorized_client"}
LEGACY_TOKEN_PATH = "/oauth/token"           # 老的令牌端点（OpenClaw：存着的是它就重新发现）
DEFAULT_MODEL = "grok-4.7"                   # 对话默认挑的（10/20 OpenClaw 给 xAI OAuth 的默认；Hermes 是 grok-4.6）
CATALOG_EVERY = 6 * 3600                     # 型号目录多久在后台重取一次
NOT_CHAT = re.compile(r"imagine|image|video|tts|voice|speech|transcri|embed|realtime|multi-agent", re.I)  # 目录里不是对话的、多 agent 的
SECRET_KEYS = ("access_token", "refresh_token", "id_token")
TRANSPORT: httpx.AsyncBaseTransport | None = None  # 测试时换成 httpx.MockTransport


class PlanError(Exception):
    """登录、退出这类操作没成：一句给人看的原因（不含令牌）。"""


def cfg() -> dict:
    """server.json 的 engine.grok（false = 关掉）。"""
    from . import models
    v = models.cfg().get("grok")
    return {"enabled": False} if v is False else v if isinstance(v, dict) else {}


def enabled() -> bool:
    return cfg().get("enabled", True) is not False


def user_agent() -> str:
    try:
        from version import VERSION
    except Exception:  # noqa: BLE001
        VERSION = "unknown"  # noqa: N806
    return f"OpenMousse/{VERSION}"


# —— 文件：目录 0700、文件 0600、原子写（写法同 chatgpt_plan） ————————————————————————————————

def data_path() -> Path:
    """<data_dir>/grok（只算路径，不建：没登录过的服务器上什么都不多）。"""
    return settings.data_dir / "grok"


def base_dir() -> Path:
    d = data_path()
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    ignore = d / ".gitignore"  # 万一 data_dir 放在仓库里：这一夹东西也不进 git
    if not ignore.exists():
        with contextlib.suppress(OSError):
            ignore.write_text("*\n", encoding="utf8")
    return d


def state() -> dict:
    return read_json(data_path() / "state.json") or {}


def save_state(**changes) -> dict:
    s = {**state(), **changes}
    write_private(base_dir() / "state.json", s)
    return s


def record_path() -> Path:
    return data_path() / "account.json"


def record() -> dict | None:
    return read_json(record_path())


def has_tokens(rec: dict | None) -> bool:
    return bool(rec and rec.get("access_token") and rec.get("refresh_token"))


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


def claims_of(token: Any) -> dict:
    """JWT 的载荷（不验签：只拿来显示邮箱、认是不是同一个账号；令牌是从令牌端点经 TLS 直接拿到的）。认不出 → {}。"""
    parts = token.split(".") if isinstance(token, str) else []
    if len(parts) != 3:
        return {}
    try:
        v = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
    except (ValueError, TypeError):
        return {}
    return v if isinstance(v, dict) else {}


def _expiry(access: str, expires_in: Any, now: float) -> float:
    """访问令牌什么时候过期：expires_in 算的；访问令牌本身是 JWT、写了更早的 exp 就用它（只会更早）。都没有 → 一小时。"""
    at = None
    with contextlib.suppress(TypeError, ValueError):
        if expires_in is not None and not isinstance(expires_in, bool):
            at = now + max(0.0, float(expires_in))
    exp = claims_of(access).get("exp")
    if isinstance(exp, (int, float)) and not isinstance(exp, bool) and exp > 0:
        at = min(at, float(exp)) if at is not None else float(exp)
    return at if at is not None else now + 3600


def skew(rec: dict) -> float:
    """提前多少秒刷新：寿命的六分之一，夹在 REFRESH_MIN 到 REFRESH_MAX（Hermes：6 小时的令牌提前 1 小时，十几分钟的提前 2 分钟）。"""
    try:
        life = float(rec.get("expires_at") or 0) - float(rec.get("issued_at") or 0)
    except (TypeError, ValueError):
        life = 0
    return REFRESH_MAX if life <= 0 else max(REFRESH_MIN, min(REFRESH_MAX, life / 6))


# —— 跟账号服务说话 ———————————————————————————————————————————————————————————————————

def _client(timeout: float = 30) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=10), transport=TRANSPORT, follow_redirects=False,
                             headers={"User-Agent": user_agent(), "Accept": "application/json"})


def trusted(url: Any) -> bool:
    """端点能不能用：https 的 x.ai / *.x.ai（测试时换了 ISSUER：和它同一个源的也认）。"""
    try:
        u = urlsplit(str(url or ""))
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    if u.scheme == "https" and (host == "x.ai" or host.endswith(".x.ai")) and not u.username and not u.password:
        return True
    iss = urlsplit(ISSUER)
    return bool(host) and (u.scheme, u.netloc) == (iss.scheme, iss.netloc)


_discovery: tuple[float, dict] | None = None


async def discovery(fresh: bool = False) -> dict:
    """发现文档（一小时的缓存）→ {"device", "token", "revoke"}：端点都过了 trusted。取不到、端点不可信 → PlanError。"""
    global _discovery
    if not fresh and _discovery and time.monotonic() - _discovery[0] < 3600:
        return _discovery[1]
    try:
        async with _client() as c:
            r = await c.get(f"{ISSUER}/.well-known/openid-configuration")
        doc = r.json() if r.status_code == 200 else None
    except (httpx.HTTPError, ValueError) as e:
        raise PlanError(L("连不上 xAI 的账号服务（auth.x.ai），过一会儿再试", "Couldn't reach xAI's account service (auth.x.ai); try again shortly")) from e
    if not isinstance(doc, dict):
        raise PlanError(L(f"xAI 的账号服务没回发现文档（HTTP {r.status_code}），过一会儿再试",
                          f"xAI's account service didn't return its discovery document (HTTP {r.status_code}); try again shortly"))
    out = {"device": doc.get("device_authorization_endpoint"), "token": doc.get("token_endpoint"), "revoke": doc.get("revocation_endpoint")}
    if not (isinstance(out["device"], str) and trusted(out["device"]) and isinstance(out["token"], str) and trusted(out["token"])):
        raise PlanError(L("xAI 发现文档里的登录地址不在 x.ai 上（或者不是 https），没有用它", "xAI's discovery document points outside x.ai (or isn't https); not used"))
    if not (isinstance(out["revoke"], str) and trusted(out["revoke"])):
        out["revoke"] = None
    _discovery = (time.monotonic(), out)
    return out


async def _form(url: str, form: dict) -> tuple[int, dict, httpx.Response]:
    """POST 表单 → (状态码, JSON（不是 JSON = {}）, 响应)。连不上抛 httpx.HTTPError。回来的东西可能带令牌：不打印、不放进错误里。"""
    async with _client() as c:
        r = await c.post(url, data=form)
    try:
        j = r.json()
    except ValueError:
        j = {}
    return r.status_code, j if isinstance(j, dict) else {}, r


def _err_code(j: dict) -> str:
    e = j.get("error")
    if isinstance(e, dict):
        e = e.get("code") or e.get("type")
    return re.sub(r"[^\w.:-]", "", str(e or ""))[:60]


def _err_text(j: dict) -> str:
    """错误的说明（error_description）：只留短的一句，去掉看着像令牌的长串。"""
    d = j.get("error_description") or j.get("message") or ""
    d = re.sub(r"[A-Za-z0-9_\-.=]{32,}", "…", str(d))
    return " ".join(d.split())[:200]


def _challenge(r: httpx.Response, j: dict) -> bool:
    """边缘挡下来的网页（Cloudflare 的人机挑战、Vercel 的拦截）：不是 OAuth 的错，可以等一会儿再来。"""
    if _err_code(j):
        return False
    ctype = (r.headers.get("content-type") or "").lower()
    text = r.text[:2000] if r.content else ""
    return (r.headers.get("cf-mitigated") == "challenge" or bool(r.headers.get("x-vercel-mitigated")) or "text/html" in ctype
            or bool(re.search(r"<!doctype html|<html\b|just a moment|attention required|challenge-platform", text, re.I)))


def login_note() -> str:
    """登录前给人看的那句（setup_ctl、设置页的说明、POST /api/engine/grok/login 回的 note）：这不是 xAI 点名支持的接入（见文件开头）。"""
    return L("同意页上可能写着 Grok Build：这里借的是 xAI 给几家点名的第三方 agent 共用的登录入口，不是 xAI 点名支持 OpenMousse 的接入，"
             "xAI 随时可能拒（403）。被拒了就改用 API 密钥（XAI_API_KEY，模型选 xai/<型号>，按量计费）。",
             "The consent page may say Grok Build: this borrows the sign-in xAI shares with a few third-party agents it has named. It isn't "
             "an integration xAI has named or supports for OpenMousse, and xAI may refuse it (403) at any time. If it does, use an API key "
             "instead (XAI_API_KEY with an xai/<model> model, pay as you go).")


def denied_text(where: str = "") -> str:
    """xAI 回 403 时给人看的那句（登录、刷新、推理都用它）。"""
    head = L("xAI 不让这个账号用 OAuth 登录第三方应用（403）", "xAI won't let this account sign in to third-party apps with OAuth (403)")
    if where:
        head += f" · {where}"
    return head + L("：这条登录不是 xAI 点名支持 OpenMousse 的接入，哪些账号、哪些应用能用由 xAI 定，重新登录没用。可以改用 API 密钥："
                    "在 console.x.ai 拿一把，存成 XAI_API_KEY（设置 → 模型 → xAI），模型选 xai/<型号>（按量计费，和订阅额度是两本账）",
                    ": this sign-in isn't an integration xAI has named or supports for OpenMousse, xAI decides which accounts and apps may "
                    "use it, and signing in again won't help. Use an API key instead: get one at console.x.ai, save it as XAI_API_KEY "
                    "(Settings → Models → xAI) and pick an xai/<model> model (pay as you go; API credit is billed separately from the "
                    "subscription)")


def refused_text(what: str, hint: str | None = None) -> str:
    """登录着、某一样（搜索、生图……）回了 403：订阅里可能没有这一样，不等于整个账号拿不到 OAuth（不标「被拒」）。hint = 怎么办（不给 = 改用
    API 密钥那一句）。"""
    head = L(f"xAI 不让你的 Grok 订阅做这一样（{what}，403）：订阅里可能没有它，或者这个账号拿不到 OAuth 令牌。重新登录多半没用；",
             f"xAI refused this on your Grok subscription ({what}, 403): it may not be included for OAuth sign-ins, or this account can't "
             "get OAuth tokens. Signing in again usually won't help; ")
    return head + (hint or L("可以改用 API 密钥（XAI_API_KEY，按量计费，和订阅额度是两本账）",
                             "an API key works instead (XAI_API_KEY, pay as you go, billed separately from the subscription)"))


# —— 登录：设备码 ————————————————————————————————————————————————————————————————————

@dataclass
class Attempt:
    device_code: str
    user_code: str
    url: str                          # 直接打开就带着码的那个（verification_uri_complete），没有就是 verification_uri
    verification_uri: str
    token_url: str
    revoke_url: str | None
    expires: float
    interval: float
    status: str = "pending"           # pending / done / denied / error / expired / cancelled
    error: str | None = None
    task: Any = None


_attempt: Attempt | None = None


async def start_login() -> dict:
    """开始一次设备码登录（见文件开头）→ {"url", "verificationUri", "code", "expiresAt", "interval", "status"}；在后台轮询到用户点了同意。
    同时只有一次：再开始就把上一次作废。不行抛 PlanError。"""
    global _attempt
    if not enabled():
        raise PlanError(L("server.json 里关掉了 Grok 订阅（engine.grok = false）", "The Grok subscription is turned off in server.json (engine.grok = false)"))
    cancel_login()
    ends = await discovery()
    try:
        st, j, r = await _form(ends["device"], {"client_id": CLIENT_ID, "scope": SCOPE})
    except httpx.HTTPError as e:
        raise PlanError(L("连不上 xAI 要设备码，过一会儿再试", "Couldn't reach xAI for a device code; try again shortly")) from e
    if st == 403 and not _challenge(r, j):
        mark_denied("device_code_403", denied_text())
        raise PlanError(denied_text())
    if st != 200:
        code, why = _err_code(j), _err_text(j)
        raise PlanError(L(f"xAI 没给设备码（HTTP {st}{' ' + code if code else ''}{'：' + why if why else ''}）",
                          f"xAI didn't issue a device code (HTTP {st}{' ' + code if code else ''}{': ' + why if why else ''})"))
    dc, uc, vu, vc = (j.get(k) for k in ("device_code", "user_code", "verification_uri", "verification_uri_complete"))
    if not (isinstance(dc, str) and dc and isinstance(uc, str) and uc and isinstance(vu, str) and trusted(vu)):
        raise PlanError(L("xAI 回的设备码不全（要有 device_code、user_code、verification_uri），没有开始", "xAI's device code answer is incomplete; not started"))
    url = vc if isinstance(vc, str) and trusted(vc) else vu
    try:
        ttl = float(j.get("expires_in") or LOGIN_MAX)
    except (TypeError, ValueError):
        ttl = LOGIN_MAX
    try:
        every = float(j.get("interval") or INTERVAL)
    except (TypeError, ValueError):
        every = INTERVAL
    a = Attempt(device_code=dc, user_code=uc[:32], url=url, verification_uri=vu, token_url=ends["token"], revoke_url=ends["revoke"],
                expires=time.time() + max(10.0, min(ttl, LOGIN_MAX)), interval=max(MIN_INTERVAL, every))
    a.task = asyncio.get_running_loop().create_task(_poll(a))
    _attempt = a
    return {"url": a.url, "verificationUri": a.verification_uri, "code": a.user_code, "expiresAt": _iso(a.expires), "interval": a.interval,
            "status": "pending", "note": login_note()}


def _end(a: Attempt, status: str, error: str | None = None) -> None:
    if a.status == "pending":
        a.status, a.error = status, error


async def _poll(a: Attempt) -> None:
    """按 interval 轮询令牌端点，直到用户点了同意 / 拒绝 / 码过期 / 取消（Hermes 的 _poll_device_token_generic 的规矩）。"""
    wait = a.interval
    edge = 0.0
    try:
        while a.status == "pending":
            left = a.expires - time.time()
            if left <= 0:
                _end(a, "expired", L("等了太久，这个码作废了，请重新开始", "The code expired before it was approved; start again"))
                return
            await asyncio.sleep(min(max(wait, edge), left))
            if a.status != "pending":
                return
            try:
                st, j, r = await _form(a.token_url, {"grant_type": DEVICE_GRANT, "client_id": CLIENT_ID, "device_code": a.device_code})
            except httpx.HTTPError:
                edge = min(max(edge * 2, a.interval * 2, 5.0), EDGE_MAX)  # 连不上：退避着接着等，用户可能还在点
                continue
            if st == 200:
                try:
                    await _connected(j, a)
                except PlanError as e:
                    _end(a, "error", str(e))
                    return
                _end(a, "done")
                return
            code = _err_code(j)
            if code == "authorization_pending":
                edge = 0.0
                continue
            if code == "slow_down":
                wait = min(wait + SLOW_DOWN, 60.0)
                continue
            if code in ("access_denied", "authorization_denied"):
                _end(a, "error", L("你在 xAI 那边拒绝了授权，没有登录", "You declined in xAI, so nothing was signed in"))
                return
            if code == "expired_token":
                _end(a, "expired", L("码过期了，请重新开始", "The code expired; start again"))
                return
            if st in (408, 429) or st >= 500 or _challenge(r, j):
                ra = r.headers.get("retry-after")
                edge = min(max(float(ra) if ra and ra.isdigit() else edge * 2, a.interval * 2, 5.0), EDGE_MAX)
                continue
            if st == 403:
                mark_denied("device_token_403", denied_text())
                _end(a, "denied", denied_text())
                return
            why = _err_text(j)
            _end(a, "error", L(f"xAI 没给令牌（HTTP {st}{' ' + code if code else ''}{'：' + why if why else ''}），请重新开始",
                               f"xAI didn't issue tokens (HTTP {st}{' ' + code if code else ''}{': ' + why if why else ''}); start again"))
            return
    except asyncio.CancelledError:
        _end(a, "cancelled")
        raise
    except Exception as e:  # noqa: BLE001 — 存凭据这类意外：记一句（不带内容），这一次作废
        print(f"[engine] Grok 订阅的登录没走完：{type(e).__name__}")
        _end(a, "error", L("服务器这边出错了，请重新开始", "Something went wrong on the server; start again"))


def login_status() -> dict:
    """登录进行到哪（不带 device_code）。"""
    a = _attempt
    if a is None:
        return {"status": "none"}
    status, error = a.status, a.error
    if status == "pending" and time.time() > a.expires:
        status, error = "expired", L("等了太久，这个码作废了，请重新开始", "The code expired before it was approved; start again")
    out = {"status": status, "error": error, "expiresAt": _iso(a.expires)}
    if status == "pending":
        out.update(url=a.url, verificationUri=a.verification_uri, code=a.user_code)
    return out


def cancel_login() -> None:
    global _attempt
    a = _attempt
    if a is not None:
        _end(a, "cancelled")
        if a.task is not None and not a.task.done():
            a.task.cancel()
    _attempt = None


async def wait_login(timeout: float | None = None) -> dict:
    """等这一次登录走完（命令行 setup_ctl.py grok login 用）→ login_status()。"""
    a = _attempt
    if a is not None and a.task is not None:
        with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError):
            await asyncio.wait_for(asyncio.shield(a.task), timeout)
    return login_status()


async def _connected(j: dict, a: Attempt) -> dict:
    """令牌端点给了令牌：验齐全 → 存成 account.json（0600）→ 状态清干净。换了账号：旧的那份尽量撤销。→ 新的记录。"""
    access, refresh_ = j.get("access_token"), j.get("refresh_token")
    if not (isinstance(access, str) and access and isinstance(refresh_, str) and refresh_):
        raise PlanError(L("xAI 给的令牌不全（没有刷新令牌：offline_access 可能被拒了），没有登录；请重新开始",
                          "xAI didn't return a complete set of tokens (no refresh token: offline_access may have been refused); start again"))
    now = time.time()
    who = claims_of(j.get("id_token")) or claims_of(access)
    old = record()
    rec = {"access_token": access, "refresh_token": refresh_, "id_token": j.get("id_token") if isinstance(j.get("id_token"), str) else None,
           "token_type": str(j.get("token_type") or "Bearer"), "expires_in": j.get("expires_in"), "issued_at": now,
           "expires_at": _expiry(access, j.get("expires_in"), now), "token_endpoint": a.token_url, "revocation_endpoint": a.revoke_url,
           "scopes": sorted(set(str(j.get("scope") or SCOPE).split())), "subject": who.get("sub") if isinstance(who.get("sub"), str) else None,
           "email": who.get("email") if isinstance(who.get("email"), str) else None,
           "name": who.get("name") if isinstance(who.get("name"), str) else None, "saved_at": _iso(now)}
    base_dir()
    write_private(record_path(), rec)
    save_state(connected_at=_iso(now), error=None, reconnect=None, denied=None, catalog=None, quota=None)
    from . import fallback
    fallback.forget("grok")  # 以前因为没登录 / 401 / 403 记的冷却作废
    if has_tokens(old) and old.get("subject") and old.get("subject") != rec["subject"]:
        with contextlib.suppress(Exception):
            await revoke(old)
    with contextlib.suppress(asyncio.TimeoutError):  # 型号目录当场取一次（取不到也算登录了：先列默认的那个）
        await asyncio.wait_for(refresh_catalog(), 10)
    return rec


# —— 令牌：现在能用的、刷新（排队）、401、撤销 ——————————————————————————————————————————————

_locks: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()  # 事件循环 → 刷新的锁（测试里每个 asyncio.run 一个新循环）


def _lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    lk = _locks.get(loop)
    if lk is None:
        lk = _locks[loop] = asyncio.Lock()
    return lk


def usable() -> bool:
    """现在能不能用订阅：没关、登录着、没标要重登、没被 xAI 拒。不连网。"""
    if not enabled():
        return False
    st = state()
    return has_tokens(record()) and not st.get("reconnect") and not st.get("denied")


def _not_ready() -> EngineError:
    st = state()
    if not enabled():
        why = L("server.json 里关掉了 Grok 订阅", "The Grok subscription is turned off in server.json")
    elif isinstance(st.get("denied"), dict):
        why = str(st["denied"].get("message") or denied_text())
    elif st.get("reconnect"):
        why = L("Grok 订阅要重新登录一次（登录过期，或者在 xAI 那边撤销了）：python3 setup_ctl.py grok login，或者设置里的 Grok 订阅",
                "Your Grok subscription needs signing in again (it expired or was revoked at xAI): python3 setup_ctl.py grok login, or Grok "
                "subscription in Settings")
    else:
        why = L("还没登录 Grok 订阅：python3 setup_ctl.py grok login（或者 POST /api/engine/grok/login），或者换一个用 API 密钥的模型",
                "Your Grok subscription isn't signed in: python3 setup_ctl.py grok login (or POST /api/engine/grok/login), or pick a model "
                "with an API key")
    return EngineError(why, kind="config", status=403 if isinstance(st.get("denied"), dict) else 401)


async def access_token(*, force: bool = False, failed: str | None = None) -> str:
    """现在能用的访问令牌：快过期了先刷新（排队）。force = 推理回了 401，强制换一个（failed = 回 401 的那个：锁里发现别人已经换过就直接用）。
    刷新一时不成（连不上、5xx）而旧的还能用：先用旧的。没登录、要重登、被拒 → EngineError(config)。"""
    if not usable():
        raise _not_ready()
    rec = record()
    assert rec is not None
    left = float(rec.get("expires_at") or 0) - time.time()
    if not force and left > skew(rec):
        return rec["access_token"]
    try:
        return (await refresh(rec, force=force, failed=failed))["access_token"]
    except EngineError as e:
        if e.retryable and not force and left > 30:
            return rec["access_token"]
        raise


async def _flock(fd: int, timeout: float = 30.0) -> None:
    """文件锁（别的进程也在刷新时排队）：不阻塞地试，最多等 timeout 秒。"""
    t0 = time.monotonic()
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() - t0 > timeout:
                raise EngineError("another process has been refreshing the Grok sign-in for too long", retryable=True) from None
            await asyncio.sleep(0.05)


async def _token_url(cur: dict) -> str:
    """刷新用的令牌端点：存着的（过了 trusted、不是老的那个）；不然重新发现。"""
    url = cur.get("token_endpoint")
    if isinstance(url, str) and trusted(url) and urlsplit(url).path.rstrip("/") != LEGACY_TOKEN_PATH:
        return url
    try:
        return (await discovery(fresh=True))["token"]
    except PlanError as e:
        raise EngineError(str(e), retryable=True) from e


async def refresh(rec: dict, *, force: bool = False, failed: str | None = None) -> dict:
    """刷新（排队：进程里一把锁 + 文件锁，锁里重读文件，排在前面的刷过了就直接用新的）。→ 新的记录。见文件开头的规矩。"""
    async with _lock():
        fd = os.open(base_dir() / "refresh.lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            await _flock(fd)
            cur = record() or rec
            if not has_tokens(cur):
                raise _not_ready()
            fresh = float(cur.get("expires_at") or 0) - time.time() > skew(cur)
            if fresh and (not force or (failed and cur["access_token"] != failed)):
                return cur  # 排在前面的已经刷过了：直接用，不拿旧的刷新令牌再刷（xAI 每次换新的，旧的再用会被当成重放）
            url = await _token_url(cur)
            for attempt in range(1, REFRESH_TRIES + 1):
                try:
                    st, j, r = await _form(url, {"grant_type": "refresh_token", "client_id": CLIENT_ID, "refresh_token": cur["refresh_token"]})
                except httpx.HTTPError as e:  # 回话丢了：刷新令牌可能已经用掉了，不重试（OpenClaw）
                    note_error("refresh_unreachable", f"can't reach xAI to refresh: {type(e).__name__}")
                    raise EngineError(f"can't reach xAI to refresh the Grok sign-in: {type(e).__name__}", retryable=True) from e
                if st == 200 or not _challenge(r, j) or attempt == REFRESH_TRIES:
                    break
                await asyncio.sleep(REFRESH_RETRY)  # 只有 Cloudflare 的网页挑战值得再试
            if st != 200:
                code = _err_code(j)
                if _challenge(r, j):
                    note_error("refresh_challenge", f"xAI answered the refresh with a web challenge (HTTP {st})")
                    raise EngineError(f"xAI answered the Grok sign-in refresh with a web challenge (HTTP {st}); try again later", status=st,
                                      retryable=True)
                if st == 403:
                    mark_denied(code or "refresh_403", denied_text(L("刷新登录时", "while refreshing the sign-in")))
                    raise _not_ready()
                if code in TERMINAL or st in (400, 401):
                    _clear_tokens()
                    mark_reconnect(code or f"refresh_http_{st}")
                    raise _not_ready()
                note_error(code or f"refresh_http_{st}", f"token refresh failed (HTTP {st})")
                raise EngineError(f"xAI didn't refresh the Grok sign-in (HTTP {st}{' ' + code if code else ''})", status=st,
                                  retryable=st >= 500 or st == 429)
            access, new_refresh = j.get("access_token"), j.get("refresh_token") or cur["refresh_token"]
            if not isinstance(access, str) or not access:
                raise EngineError("xAI's refresh answer had no access token", kind="output", retryable=True)
            who = claims_of(j.get("id_token"))
            if who.get("sub") and cur.get("subject") and who["sub"] != cur["subject"]:
                _clear_tokens()
                mark_reconnect("account_changed")
                raise _not_ready()
            now = time.time()
            out = {**cur, "access_token": access, "refresh_token": new_refresh, "expires_in": j.get("expires_in"), "issued_at": now,
                   "expires_at": _expiry(access, j.get("expires_in"), now), "token_endpoint": url, "saved_at": _iso(now)}
            if isinstance(j.get("id_token"), str) and j["id_token"]:
                out["id_token"] = j["id_token"]
            write_private(record_path(), out)  # 访问令牌、到期、新的刷新令牌一起换
            return out
        finally:
            with contextlib.suppress(OSError):
                fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)


async def after_401(failed: str) -> str:
    """推理回了 401：强制刷新一次，拿新令牌再试一次（providers.stream 调）。刷新也不行 → 抛 EngineError(config)。"""
    return await access_token(force=True, failed=failed)


def mark_reconnect(why: str) -> None:
    """要重登（刷新令牌不能用了、刷新以后还是 401）：不再用这份令牌，状态里提示重新登录。"""
    at = _iso(time.time())
    save_state(reconnect={"why": str(why)[:80], "at": at}, error={"code": str(why)[:80], "message": "sign in again", "at": at})


def mark_denied(code: str, message: str) -> None:
    """xAI 回了 403（账号档位不够 / 不在名单）：标「被拒」，状态里说清楚、提示 API 密钥；重新登录成功才清掉。"""
    at = _iso(time.time())
    save_state(denied={"code": str(code)[:80], "message": str(message)[:600], "at": at},
               error={"code": str(code)[:80], "message": str(message)[:300], "at": at})


def note_error(code: str | None, message: str) -> None:
    """记最近一次的错（给状态看；不含令牌）。"""
    save_state(error={"code": (code or "")[:80] or None, "message": str(message)[:300], "at": _iso(time.time())})


def note_ok() -> None:
    if state().get("error"):
        save_state(error=None)


def _clear_tokens() -> None:
    """清掉令牌（留着邮箱、sub：状态里还能说是哪个账号要重登）。"""
    cur = record()
    if cur is not None:
        write_private(record_path(), {k: v for k, v in cur.items() if k not in SECRET_KEYS and k not in ("expires_at", "expires_in", "issued_at")})


async def revoke(rec: dict) -> bool | None:
    """撤销刷新令牌：True = xAI 确认了；False = 没确认；None = 发现文档里没有撤销的端点。网络错、5xx 退避重试几次。"""
    if not rec.get("refresh_token"):
        return True
    url = rec.get("revocation_endpoint")
    if not (isinstance(url, str) and trusted(url)):
        try:
            url = (await discovery())["revoke"]
        except PlanError:
            url = None
    if not url:
        return None
    form = {"token": rec["refresh_token"], "token_type_hint": "refresh_token", "client_id": CLIENT_ID}
    for i in range(len(REVOKE_DELAYS) + 1):
        try:
            async with _client() as c:
                r = await c.post(url, data=form)
            if r.status_code == 200:
                return True
            if r.status_code < 500:
                return False
        except httpx.HTTPError:
            pass
        if i < len(REVOKE_DELAYS):
            await asyncio.sleep(REVOKE_DELAYS[i])
    return False


async def logout() -> dict:
    """退出：先撤销（有撤销的端点的话），再删掉本机这一份；没确认撤销也删，告诉用户去 accounts.x.ai 撤销。"""
    cancel_login()
    rec = record()
    revoked: bool | None = True
    if has_tokens(rec):
        revoked = await revoke(rec)
    with contextlib.suppress(FileNotFoundError):
        record_path().unlink()
    if data_path().exists():
        save_state(reconnect=None, denied=None, error=None, catalog=None, quota=None)
    note = None if revoked else L(f"本机的令牌已经删掉了，但 xAI 没确认撤销：可以去 {ACCOUNT_URL} 的账号设置里把这次授权（可能写着 Grok Build）撤掉",
                                  f"Removed from this server, but xAI didn't confirm the revocation: you can revoke it (it may say Grok Build) "
                                  f"in your account settings at {ACCOUNT_URL}")
    return {"ok": True, "revoked": bool(revoked), "note": note}


# —— 推理用的 provider（models.provider("grok")、providers.stream 调） ———————————————————————————————

def route() -> str:
    """对话发到哪：api（https://api.x.ai/v1，默认）/ proxy（Grok CLI 的订阅代理，engine.grok.route = "proxy"）。"""
    return "proxy" if str(cfg().get("route") or "").strip().lower() == "proxy" else "api"


def chat_url() -> str:
    return PROXY if route() == "proxy" else API


def provider_stub() -> dict:
    """models.provider("grok")：不连网的那一份（备用链认冷却用）。key 是空的，推理前 providers.stream 再填真的令牌（access_token）。
    keys 里那一把是这个账号的记号（sub 的短指纹，不是令牌）：冷却按它记，换了账号就是另一把。"""
    import hashlib

    from . import models
    rec = record() if usable() else None
    mark = hashlib.sha256(str((rec or {}).get("subject") or "grok").encode()).hexdigest()[:12] if rec else ""
    extra = cfg().get("headers") if isinstance(cfg().get("headers"), dict) else {}
    return {"name": "grok", "api": models.RESPONSES, "url": chat_url(), "key": "", "key_env": None, "plan": True, "images": True,
            "keys": [("grok", mark)] if mark else [], "key_name": "grok",
            "headers": {"User-Agent": user_agent(), **{str(k): str(v) for k, v in extra.items()}}}


def chat_headers(model_id: str) -> dict:
    """这一次对话请求多带的头：走 Grok CLI 的订阅代理时要它那三个（OpenClaw 的 stream.ts：令牌的用法、客户端版本、具体的型号）；api.x.ai 不用。"""
    if route() != "proxy":
        return {}
    return {"X-XAI-Token-Auth": "xai-grok-cli", "x-grok-client-version": user_agent().split("/", 1)[1], "x-grok-model-override": model_id}


def note_failure(e: EngineError) -> None:
    """一次推理没成（providers.stream 调，已经抹过令牌）：401（刷新过了还是）→ 标要重登；403 → 换成说清楚的一句（这个账号拿不到 OAuth、
    改用 API 密钥），记成最近一次的错；别的照原样记。"""
    if e.status == 401:
        mark_reconnect(getattr(e, "code", None) or "unauthorized")
        return
    if e.status == 403:
        e.args = (denied_text(L("推理时", "on a request")) + f" ({str(e)[:160]})",)
        note_error(getattr(e, "code", None) or "http_403", str(e))
        return
    note_error(getattr(e, "code", None), str(e))


# —— 型号目录 ——————————————————————————————————————————————————————————————————————

_catalog_busy = False
_bg: set[asyncio.Task] = set()


def _spawn(coro) -> None:
    try:
        t = asyncio.get_running_loop().create_task(coro)
    except RuntimeError:
        coro.close()
        return
    _bg.add(t)
    t.add_done_callback(_bg.discard)


def _slug_ok(slug: str) -> bool:
    from . import models
    return bool(models.NAME.match(slug))


def chat_rows(rows: Any) -> list[dict]:
    """GET /models 回的那一串 → 对话的型号 [{"id", "name"}]：OpenAI 的 data（{"id"}）和订阅代理的 models（{"id" | "model", "name",
    "api_backend"}）都认；生图、生视频、朗读、向量、多 agent 的不要（NOT_CHAT；写了 api_backend 的只要 responses / chat / language）。"""
    out: list[dict] = []
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict):
            continue
        mid = r.get("id") or r.get("model")
        if not isinstance(mid, str) or not _slug_ok(mid) or NOT_CHAT.search(mid) or any(x["id"] == mid for x in out):
            continue
        backend = r.get("api_backend") or r.get("apiBackend") or r.get("backend")
        if isinstance(backend, str) and backend.lower() not in ("responses", "chat", "language"):
            continue
        out.append({"id": mid, "name": str(r.get("name") or r.get("display_name") or mid)[:80]})
    return out


async def refresh_catalog() -> list[dict]:
    """这个账号能用的对话型号（对话地址的 GET /models）→ 存进 state.json（不含令牌）。取不到就留着上一次的。"""
    global _catalog_busy
    if _catalog_busy:
        return list((state().get("catalog") or {}).get("models") or [])
    _catalog_busy = True
    try:
        tok = await access_token()
        head = {"Authorization": f"Bearer {tok}", **{k: v for k, v in chat_headers("").items() if v}}  # 订阅代理：带它认的头（型号那个是空的不带）
        async with _client() as c:
            r = await c.get(f"{chat_url()}/models", headers=head)
        if r.status_code != 200:
            if r.status_code == 403:
                note_error("models_403", denied_text(L("列型号时", "listing models")))
            return list((state().get("catalog") or {}).get("models") or [])
        j = r.json()
        out = chat_rows(j.get("data") if isinstance(j, dict) and isinstance(j.get("data"), list) else j.get("models") if isinstance(j, dict) else None)
        if out:
            save_state(catalog={"at": time.time(), "models": out})
        return out
    except (EngineError, httpx.HTTPError, ValueError):
        return list((state().get("catalog") or {}).get("models") or [])
    finally:
        _catalog_busy = False


def catalog_source() -> str:
    """型号从哪来：config（engine.grok.models）/ account（账号的目录）/ default（目录还没取到：先列默认的那个）。"""
    if isinstance(cfg().get("models"), list):
        return "config"
    return "account" if (state().get("catalog") or {}).get("models") else "default"


def model_ids() -> list[str]:
    """app 里多出来的 grok/…：能用（usable）才有。engine.grok.models 写了就用它；不写用账号的目录（默认的对话型号排最前，旧了在后台重取），
    目录还没取到先列 grok/<DEFAULT_MODEL>。"""
    if not usable():
        return []
    conf = cfg().get("models")
    if isinstance(conf, list):
        out: list[str] = []
        for x in conf:
            s = str(x).strip() if isinstance(x, str) else ""
            s = s if s.startswith("grok/") else f"grok/{s}" if s else ""
            if s and _slug_ok(s.partition("/")[2]) and s not in out:
                out.append(s)
        return out
    cat = state().get("catalog") or {}
    if not cat or time.time() - float(cat.get("at") or 0) > CATALOG_EVERY:
        _spawn(refresh_catalog())
    ids = [m["id"] for m in cat.get("models") or [] if isinstance(m, dict) and isinstance(m.get("id"), str) and _slug_ok(m["id"])]
    if not ids:
        return [f"grok/{DEFAULT_MODEL}"]
    first = DEFAULT_MODEL if DEFAULT_MODEL in ids else ids[0]
    return [f"grok/{first}", *(f"grok/{i}" for i in ids if i != first)]


def default_model() -> str | None:
    """对话默认挑的那个（目录里的 grok-4.7，没有就第一个）；不能用 → None。"""
    ids = model_ids()
    return ids[0] if ids else None


# —— 订阅调 api.x.ai 的别的接口（搜索、生图、生视频、朗读） ——————————————————————————————————————————

class ApiError(Exception):
    """订阅调 api.x.ai 没成：status（HTTP 状态；连不上 = None）+ 一句给人看的话（不带令牌）。"""

    def __init__(self, status: int | None, message: str) -> None:
        self.status = status
        super().__init__(message)


def search_on() -> bool:
    """登录了订阅时，搜网页（web_search 的 grok）先走订阅吗：engine.grok.search（默认 true；false = 只用 XAI_API_KEY，照旧按量）。"""
    return cfg().get("search", True) is not False


def x_search_on() -> bool:
    """x_search 走不走订阅：engine.grok.x_search = true 才走（默认不走，只用 XAI_API_KEY）。10/21 定：xAI 没写过 x_search 算不算在订阅
    里（公开 API 上它按抓到的帖子另收钱），所以要用户单独开。"""
    return cfg().get("x_search") is True


def may_use(kind: str | None, origin: str | None) -> bool:
    """这一轮的工具能不能花订阅（搜索、生图、生视频、朗读）：照 models.may_spend（origin 是 ToolCtx.origin：已经和 via 取过更严的）。
    卡上点过头的那一次（handkit.run_approved）照交卡那一轮的来路算，不升成主人自己引出来的：/v1、ACP 的调用方能对自己的卡点头。"""
    from . import models
    return models.may_spend(f"grok/{DEFAULT_MODEL}", kind=kind or "outsider", origin=origin)


def why_not() -> str | None:
    """现在为什么用不了订阅（没登录、要重登、被拒、关掉了）；能用 → None。不连网。"""
    return None if usable() else str(_not_ready())


def refusal(kind: str | None, origin: str | None) -> str:
    """这一次为什么不走订阅（给工具报错用）。"""
    if not usable():
        return str(_not_ready())
    return L("这一轮不能花你的 Grok 订阅：后台的要先在设置里同意（python3 setup_ctl.py grok background on），别人引出来的、经 /mcp 来的一律不行",
             "This turn can't use your Grok subscription: background runs need your OK first (python3 setup_ctl.py grok background on), and "
             "turns started by other people or through /mcp never can")


async def api(method: str, path: str, *, json_body: Any = None, want: str = "json", timeout: float = 120,
              what: str = "", hint: str | None = None, headers: dict | None = None) -> tuple[Any, httpx.Response]:
    """用订阅的令牌调 api.x.ai（API + path）→ (JSON / 字节, 响应)。现取令牌（快过期先刷新）；401 强制刷新一次再试，还是 401 标要重登；
    403 → 说清楚（这个账号拿不到 OAuth、这一样订阅里没有；hint = 怎么办）、记成最近一次的错。headers：多带的请求头（不能换掉令牌）。
    出错抛 ApiError（不带令牌）。"""
    from .errors import scrub
    try:
        tok = await access_token()
    except EngineError as e:
        raise ApiError(e.status or 401, str(e)) from None
    extra = {str(k): str(v) for k, v in (headers or {}).items() if str(k).lower() != "authorization"}
    r: httpx.Response | None = None
    for attempt in (1, 2):
        try:
            async with _client(timeout) as c:
                r = await c.request(method, f"{API}{path}", json=json_body, headers={**extra, "Authorization": f"Bearer {tok}",
                                                                                   "Accept": "application/json" if want == "json" else "*/*"})
        except httpx.TimeoutException:
            raise ApiError(None, L(f"xAI {timeout:g} 秒没回", f"xAI didn't answer within {timeout:g}s")) from None
        except httpx.HTTPError as e:
            raise ApiError(None, L(f"连不上 xAI：{type(e).__name__}", f"Can't reach xAI: {type(e).__name__}")) from None
        if r.status_code == 401 and attempt == 1:
            try:
                tok = await after_401(tok)
            except EngineError as e:
                raise ApiError(401, str(e)) from None
            continue
        break
    assert r is not None
    if r.status_code == 401:
        mark_reconnect("unauthorized")
        raise ApiError(401, str(_not_ready()))
    if r.status_code >= 400:
        try:
            j = r.json()
        except ValueError:
            j = {}
        detail = j.get("error") or j.get("message") or j.get("code") if isinstance(j, dict) else None
        detail = scrub(" ".join(str(detail.get("message") if isinstance(detail, dict) else detail or r.text[:300]).split()), tok)[:300]
        if r.status_code == 403:
            msg = (refused_text(what, hint) if what else denied_text()) + (f" ({detail})" if detail else "")
            note_error("http_403", msg)
            raise ApiError(403, msg)
        note_error(f"http_{r.status_code}", f"{what or path}: HTTP {r.status_code} {detail}")
        if r.status_code == 429:
            raise ApiError(429, L(f"xAI 说太频繁了（HTTP 429）：Grok 订阅这一段的额度可能用完了（python3 setup_ctl.py grok 看还剩多少），过一会儿再试"
                                  f"{'（' + detail + '）' if detail else ''}",
                                  f"xAI says too many requests (HTTP 429): this period's Grok subscription allowance may be used up "
                                  f"(python3 setup_ctl.py grok shows what's left); try again later{' (' + detail + ')' if detail else ''}"))
        raise ApiError(r.status_code, L(f"xAI 回了 HTTP {r.status_code}{'：' + detail if detail else ''}",
                                        f"xAI answered HTTP {r.status_code}{': ' + detail if detail else ''}"))
    note_ok()
    if want == "bytes":
        return r.content, r
    try:
        return r.json(), r
    except ValueError:
        raise ApiError(r.status_code, L("xAI 回的不是 JSON", "xAI's answer isn't JSON")) from None


# —— 订阅的额度（SuperGrok 这一段用了多少；OpenClaw 的 index.ts 照 Grok CLI 问的那个接口） ———————————————————————

BILLING = "/billing?format=credits"                                       # 在订阅代理上（PROXY）
BILLING_HEADERS = {"x-grok-client-mode": "cli", "x-grok-client-version": "1.0.4"}  # Grok CLI 问用量时带的（OpenClaw 照抄）
QUOTA_EVERY = 300.0                                                       # 存着的额度多久算旧（秒），旧了在后台重取
_quota_busy = False


def _percent(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or v < 0:
        return None
    return round(min(100.0, float(v)), 2)


def _cents(v: Any) -> int | None:
    """{"val": "1234"} 这种（美分）→ 整数；没写 = 0（OpenClaw：xAI 默认是 0 的不发）；认不出 → None。"""
    raw = v.get("val") if isinstance(v, dict) else None
    if raw is None:
        return 0
    if isinstance(raw, int) and not isinstance(raw, bool):
        return raw
    return int(raw.strip()) if isinstance(raw, str) and re.fullmatch(r"-?\d+", raw.strip()) else None


def _when(v: Any) -> str | None:
    if not isinstance(v, str) or not v.strip():
        return None
    try:
        d = datetime.fromisoformat(v.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_quota(j: Any) -> dict | None:
    """billing 回的 → {"plan", "period": weekly / monthly / None, "usedPercent", "periodStart", "resetsAt"}；认不出 → None。
    只认订阅里含的那一份（creditUsagePercent，或者老的 used / monthlyLimit 算出来的）；API 的预付余额是按量的另一本账，不算进来（同 OpenClaw）。"""
    conf = j.get("config") if isinstance(j, dict) else None
    if not isinstance(conf, dict):
        return None
    cur = conf.get("currentPeriod") or conf.get("current_period")
    cur = cur if isinstance(cur, dict) else {}
    pct = _percent(conf.get("creditUsagePercent", conf.get("credit_usage_percent")))
    limit = _cents(conf.get("monthlyLimit", conf.get("monthly_limit")))
    if pct is None:
        used = _cents(conf.get("used"))
        pct = _percent(used / limit * 100) if used is not None and used >= 0 and limit else None
    kind = str(cur.get("type") or "")
    period = "weekly" if kind.endswith("WEEKLY") else "monthly" if kind.endswith("MONTHLY") or (limit or 0) > 0 else None
    start = _when(cur.get("start") or conf.get("billingPeriodStart") or conf.get("billing_period_start"))
    end = _when(cur.get("end") or conf.get("billingPeriodEnd") or conf.get("billing_period_end"))
    if pct is None and not (period and (start or end)):
        return None
    tier = j.get("subscription_tier") or j.get("subscriptionTier")
    plan = tier.strip() if isinstance(tier, str) and 0 < len(tier.strip()) <= 128 and tier.isprintable() else "SuperGrok"
    return {"plan": plan, "period": period, "usedPercent": pct, "periodStart": start, "resetsAt": end}


async def refresh_quota() -> dict | None:
    """问一次这一段的额度（订阅代理的 billing；令牌只发到 PROXY）→ 存进 state.json（不含令牌）→ quota()。没登录 → None。"""
    global _quota_busy
    if not usable():
        return None
    if _quota_busy:
        return quota(spawn=False)
    _quota_busy = True
    old = state().get("quota") if isinstance(state().get("quota"), dict) else {}
    try:
        tok = await access_token()
        async with _client(20) as c:
            r = await c.get(f"{PROXY}{BILLING}", headers={"Authorization": f"Bearer {tok}", **BILLING_HEADERS})
        got = parse_quota(r.json()) if r.status_code == 200 else None
        why = None if got else (L(f"xAI 没给用量（HTTP {r.status_code}）", f"xAI didn't share the usage (HTTP {r.status_code})") if r.status_code != 200
                                else L("xAI 回的用量认不出来", "xAI's usage answer isn't readable"))
    except EngineError as e:
        got, why = None, str(e)
    except (httpx.HTTPError, ValueError) as e:
        got, why = None, L(f"连不上 xAI 问用量：{type(e).__name__}", f"Couldn't reach xAI for the usage: {type(e).__name__}")
    finally:
        _quota_busy = False
    keep = {k: old.get(k) for k in ("plan", "period", "usedPercent", "periodStart", "resetsAt", "seenAt")} if old else {}
    now = time.time()
    save_state(quota={**({**got, "seenAt": now} if got else keep), "at": now, "error": why})
    return quota(spawn=False)


def quota(*, spawn: bool = True) -> dict | None:
    """存着的额度（不连网；旧了在后台重取）→ {"plan", "period", "usedPercent", "periodStart", "resetsAt", "checkedAt", "error"}。
    没登录 → None；还没取过 → None（后台去取）。checkedAt = 最近一次问的时间；error = 那一次没问到的原因（数字是上一次问到的）。"""
    if not usable():
        return None
    q = state().get("quota")
    q = q if isinstance(q, dict) else None
    if spawn and (q is None or time.time() - float(q.get("at") or 0) > QUOTA_EVERY):
        _spawn(refresh_quota())
    if q is None:
        return None
    return {**{k: q.get(k) for k in ("plan", "period", "usedPercent", "periodStart", "resetsAt")},
            "checkedAt": _iso(float(q["at"])) if q.get("at") else None, "error": q.get("error")}


async def quota_now(timeout: float = 10.0) -> dict | None:
    """用量页要的：存着的不旧就用它，旧了当场问一次（最多等 timeout 秒，等不到先给存着的）。"""
    if not usable():
        return None
    q = state().get("quota")
    if isinstance(q, dict) and time.time() - float(q.get("at") or 0) <= QUOTA_EVERY:
        return quota(spawn=False)
    with contextlib.suppress(asyncio.TimeoutError):
        return await asyncio.wait_for(refresh_quota(), timeout)
    return quota(spawn=False)


def quota_line(q: dict | None) -> str | None:
    """给人看的一句（命令行、状态）：「SuperGrok Heavy · 这一周用了 12.5%，2026-10-25 重置」。没有 → None。"""
    if not q or (q.get("usedPercent") is None and not q.get("resetsAt")):
        return None
    zh_p, en_p = {"weekly": ("这一周", "this week"), "monthly": ("这个月", "this month")}.get(str(q.get("period")), ("这一段", "this period"))
    used = q.get("usedPercent")
    reset = str(q.get("resetsAt") or "")[:10]
    return L(f"{q.get('plan') or 'SuperGrok'} · {zh_p}用了 {used:g}%" if used is not None else f"{q.get('plan') or 'SuperGrok'} · {zh_p}的用量 xAI 没给",
             f"{q.get('plan') or 'SuperGrok'} · {used:g}% used {en_p}" if used is not None else f"{q.get('plan') or 'SuperGrok'} · xAI didn't say how much is used {en_p}") \
        + (L(f"，{reset} 重置", f", resets {reset}") if reset else "")


# —— 后台同意 ——————————————————————————————————————————————————————————————————————

def background_allowed() -> bool:
    """用户在设置里明确同意过后台任务、定时任务、心跳花自己的 Grok 订阅吗（默认没有；和 ChatGPT 套餐的同意分开记）。"""
    b = state().get("background")
    return isinstance(b, dict) and b.get("allowed") is True


def set_background(allow: bool) -> dict:
    """记下用户的选择和时间（同意 / 收回都记）。"""
    save_state(background={"allowed": bool(allow), "at": _iso(time.time())})
    return status()


# —— 状态 ——————————————————————————————————————————————————————————————————————————

def status() -> dict:
    """GET /api/engine/grok、/api/engine/status 的 grok：连没连、邮箱、快过期没有、要不要重登、被拒的原因、最近一次的错、后台同意没有。
    不带令牌、不带 sub。"""
    st = state()
    rec = record()
    connected = has_tokens(rec)
    left = int(float(rec.get("expires_at") or 0) - time.time()) if connected else None
    err = st.get("error") if isinstance(st.get("error"), dict) else None
    den = st.get("denied") if isinstance(st.get("denied"), dict) else None
    bg = st.get("background") if isinstance(st.get("background"), dict) else {}
    return {"enabled": enabled(), "connected": connected, "ready": usable(), "email": (rec or {}).get("email") if connected else None,
            "expiresIn": left, "expiringSoon": left is not None and left < skew(rec or {}), "needsReconnect": bool(st.get("reconnect")),
            "denied": {k: den.get(k) for k in ("code", "message", "at")} if den else None,
            "lastError": {k: err.get(k) for k in ("code", "message", "at")} if err else None,
            "background": {"allowed": bg.get("allowed") is True, "since": bg.get("at")}, "route": route(), "accountUrl": ACCOUNT_URL,
            "models": model_ids(), "defaultModel": default_model(), "catalog": catalog_source(), "login": login_status(),
            "quota": quota()}


# —— 接口 ——————————————————————————————————————————————————————————————————————————

def _owner(request: Request) -> str:
    """只认主人的设备（同 /api/setup：app 的令牌或 Tailscale 里自己的设备，带 X-Mousse-Via 的一律 403）。"""
    from .setup_api import owner
    return owner(request)


router = APIRouter(dependencies=[Depends(_owner)])


def _fail(e: PlanError):
    from .setup_api import SetupError
    return SetupError(400, str(e))


# 都写成 async：state.json 的读改写都在事件循环这一个线程里，不和线程池里的请求抢
@router.get("/api/engine/grok")
async def get_status():
    return {"ok": True, **status()}


@router.post("/api/engine/grok/login")
async def post_login():
    try:
        return {"ok": True, **await start_login()}
    except PlanError as e:
        raise _fail(e) from e


@router.get("/api/engine/grok/login")
async def get_login():
    return {"ok": True, **login_status()}


@router.delete("/api/engine/grok/login")
async def delete_login():
    cancel_login()
    return {"ok": True}


@router.post("/api/engine/grok/logout")
async def post_logout():
    return await logout()


@router.get("/api/engine/grok/usage")
async def get_usage():
    """这一段的额度（当场问一次 xAI）→ {"ok", "quota": {…} | null}（没登录 = null）。"""
    return {"ok": True, "quota": await refresh_quota()}


@router.put("/api/engine/grok/background")
async def put_background(request: Request):
    """{"allow": true | false}（请求体自己查：校验没过时不回原样的输入）。"""
    from .setup_api import SetupError, body_of
    b = await body_of(request)
    if not isinstance(b.get("allow"), bool):
        raise SetupError(400, L("allow 要是 true / false", "allow must be true or false"), field="allow")
    return {"ok": True, **set_background(b["allow"])}


def reset() -> None:
    """测试用：进程里的缓存和进行中的登录都清掉（文件不动）。"""
    global _discovery, _catalog_busy, _quota_busy
    cancel_login()
    _discovery = None
    _catalog_busy = _quota_busy = False
