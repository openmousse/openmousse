"""用户自己的 ChatGPT 套餐（内核第 11b 项，2026-10-17）：照 OpenAI 官方给开源 / 本地应用的「Sign in with ChatGPT」（SIWC：ChatGPT plan usage，
按钮写 Continue with ChatGPT）那条路，用户在浏览器里授权一次，这台服务器拿到用户的令牌，用公开的 Responses API（https://api.openai.com/v1/responses，
engine/provider_responses.py）跑 chatgpt/<型号>，花的是用户自己的 Plus / Pro 额度。不碰 ChatGPT 的 backend-api（官方明说不许）。
Claude 那一半（主人回合交给用户自己登录的 Claude Code）在 engine/claude_brain.py。

条款（Sign in with ChatGPT Terms，openai.com/policies/sign-in-with-chatgpt-terms，2026-09-29 版）里卡我们的几句和这里怎么守：
  - 「Any persistent storage of Authentication Tokens must be local and under the user's control, not in a remote or managed environment.」
    「Requests must originate from the user's local runtime or a remote runtime only that user controls.」
    → 令牌只存在这台服务器（用户自己的机器）的文件里（0600、原子写），不进日志、不进 git、不进网址、不回给 app；请求只从这台服务器发。
  - 「Another user's activity must not trigger requests to the authenticated user's account.」「Do not provide general-purpose API access
    for other tools or unrelated requests.」→ 只有主人回合能花（models.may_spend）：赛博、外人回合（名片 Agent、A2A、Doorman）、对外接口
    （⑭ /v1、ACP）、经 /mcp 来的（外面的 MCP 客户端，via mcp）、一问一答（owner.stream）一律换成非套餐的模型（models.outside），挑不到就报错。
  - 「Obtain express consent before background use.」→ 后台任务、定时任务、心跳（来路 auto / task）默认不花；用户在设置里明确同意一次
    （PUT /api/engine/chatgpt/background，记下同意的时间），之后才花。没同意时换非套餐的模型，没有就不跑、说清楚为什么。
  - 「Users must be able to use their ChatGPT plan through SIWC without paying you」→ OpenMousse 不收钱，这条天然满足。
  - 只连一个账号（旧分支 server/siwc.py 的护栏，不做多账号轮换）：连了另一个账号 / 工作区，成功以后先撤销、清掉旧的那份。

授权（官方 token-sharing-open-source/sign-in，10/17 重读核对；做法参考 OpenClaw 4cb8e645 的 extensions/openai/token-sharing-oauth.runtime.ts，
没拿代码）：
  首次：client_id=dynamic_agent_client、agent_name_hint=OpenMousse（应用自己的名字，每台都一样，不用 app_name）、ext_agent_host_id（这台
    机器的，第一次用时生成、存下来：RFC 9278 公钥指纹 URI，官方推荐的写法）、scope = openid profile email offline_access resource.invoke
    chatgpt.tokens.use.direct、resource=https://api.openai.com/v1、state、nonce、PKCE S256。回调带回为这个用户和工作区发的 client_id
    （oaiapp_…），存下来，之后重连都用它（不带 agent_name_hint；带 login_hint = 邮箱；先前没开套餐的，加 prompt=consent 重新问一次）。
  回调地址：http://127.0.0.1:<端口>/auth/callback（先试 1455，被占了用随机端口；不写 localhost），先起监听再给授权网址。
  换令牌：POST /api/accounts/oauth/token，带签发的 client_id、code、code_verifier、同一个 redirect_uri、resource，不要 secret。
  验 ID token：签名（官方 JWKS，RS256 / ES256，用 cryptography 自己验）、iss、aud = client_id、exp、nonce；sub 当账号身份，重连时必须是同一个。
  看 scope：要有 chatgpt.tokens.use.direct（光有 ID token 不算）；没有就保留登录、套餐标「未开启」，app 里不出现 chatgpt/… 模型，
    提示重新连一次打开，或者用自己的 API 密钥。
无头服务器（服务器常常没有浏览器，用户在手机或另一台电脑上；官方 self-hosted-vms 那一页说 127.0.0.1 的回调只到得了开浏览器的那台）：
  这里用「把回调整串粘回 app」：服务器起好监听、生成 PKCE / state / nonce，授权网址交给 app；用户在任何设备的浏览器里打开、登录、同意，
  浏览器跳到 http://127.0.0.1:<端口>/auth/callback?code=…&state=… —— 在服务器本机或者开了 SSH 转发（ssh -N -L <端口>:127.0.0.1:<端口>）
  时直接落到监听上，自动完成；手机上这一页打不开，把地址栏里整串复制回 app（POST /api/engine/chatgpt/callback）。为什么合规：授权码只能
  配这台服务器手里的 PKCE verifier 用（verifier 从不离开服务器），state 防串，nonce 绑 ID token；换令牌、存令牌、刷新、推理都在这台服务器上，
  粘回来的只有一次性的 code、state、client_id，没有任何令牌；redirect_uri 照官方写法、授权和换令牌用同一个；host id 用的是这台服务器自己的
  （比官方的「在本地授权再把凭据文件搬过去」更准：搬过去的那份带的是笔记本的 host id）。
令牌（官方 profiles-and-sessions、token-reference、errors-and-recovery）：
  访问令牌 1 小时、刷新令牌 30 天、每次刷新都换新的。快过期（剩不到 5 分钟）先刷新，不早于 earliest_refresh_at（令牌还能用时）；刷新带签发的
  client_id 和 resource、不带 scope；同一份凭据的刷新排队（single-flight：进程里一把锁 + 文件锁，锁里重读文件，别人刚刷过就直接用新的；
  照 Hermes e31c674f 的 hermes_cli/auth_codex.py 的「锁里重读、对方换过就接着用」自己写的，没拿代码）。
  刷新说令牌不能用了（invalid_grant、refresh_token_expired、refresh_token_reused……）：清掉令牌，标「要重连」；网络 / 5xx 不清（官方：临时的
  毛病不删凭据）。推理回 401：强制刷新一次再试；刷新后还是 401，标「要重连」（用户在 ChatGPT 里断开不会通知我们，只能这样发现）。
  断开：用发现文档（/.well-known/openid-configuration）里的 revocation_endpoint 撤销刷新令牌（空的 200 = 成功；网络错、5xx 退避重试），
  再清掉本机令牌；没确认撤销也清，并告诉用户去 ChatGPT 设置里断开。client_id 和账号的对应、host id 留着，下次重连用。
文件（<data_dir>/chatgpt/，目录 0700，文件 0600、先写临时文件再改名）：host.json（host id 和它的私钥）、client-<client_id>.json（一个 client
  一份：邮箱、sub、client_id、host id、各令牌、scope、到期时间，格式照官方的例子）、state.json（不含令牌：用哪个 client、后台同意没有和时间、
  模型目录、最近一次的错、要不要重连）、refresh.lock（刷新的文件锁）。
模型：chatgpt/<型号>。连上了、scope 对了才出现在 app 能切换的模型里：engine.chatgpt.models 写了就用它，不写就用这个账号的目录（官方：
  GET /v1/models 的 models 里 visibility = list 的，按服务器给的顺序；连上时和之后每 6 小时在后台取一次）。
接口（都要 /api 的令牌；回给 app 的只有连没连、哪个套餐、邮箱，不回令牌、不回邮箱以外的身份信息）：
  GET    /api/engine/chatgpt              状态（同 /api/engine/status 的 chatgpt）
  POST   /api/engine/chatgpt/login        {"account": "auto" | "new"} 开始授权 → {"url", "redirect", "expiresAt", "tunnel"}；auto = 有存下的
                                          client 就重连它，new = 连另一个账号 / 工作区（成功后旧的那份撤销清掉）
  GET    /api/engine/chatgpt/login        授权进行到哪（pending / done / error / expired / cancelled）
  DELETE /api/engine/chatgpt/login        取消
  POST   /api/engine/chatgpt/callback     {"url": "地址栏里那一整串"}：手机上授权完，把回调交回来
  POST   /api/engine/chatgpt/disconnect   断开（撤销 + 清掉本机令牌）
  PUT    /api/engine/chatgpt/background   {"allow": true | false}：后台任务、定时任务、心跳能不能花套餐（记下改的时间）
server.json 的 engine.chatgpt（可选，每次现读）：false = 关掉；{"models": ["chatgpt/gpt-6.1-sol", …]}：app 里列哪几个（不写用账号的目录）；
  {"headers": {…}}：推理请求多带的请求头（官方以后改了要求时不用改代码）。
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import fcntl
import getpass
import hashlib
import json
import os
import re
import secrets
import socket
import time
import weakref
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config import settings
from i18n import L

from .errors import EngineError

ISSUER = "https://auth.openai.com"
API = "https://api.openai.com/v1"           # 推理和模型目录都在这里（测试时换成假的服务）
RESOURCE = "https://api.openai.com/v1"      # 授权里的 resource：一个标识，不是要去连的地址
REGISTER = "dynamic_agent_client"           # 首次注册的入口，不是要存的 client_id
APP = "OpenMousse"                          # agent_name_hint：应用自己的名字，每台都一样
SCOPE = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
PLAN_SCOPE = "chatgpt.tokens.use.direct"
CLIENT = re.compile(r"^oaiapp_[A-Za-z0-9_-]{1,200}$")
CALLBACK = "/auth/callback"
PORT = 1455                                 # 先试这个端口（官方例子、Codex 也用它），被占了用随机的
LOGIN_TTL = 600                             # 一次授权最多等多久（秒）
REFRESH_BEFORE = 300                        # 访问令牌剩不到这么多秒就先刷新
CATALOG_EVERY = 6 * 3600                    # 模型目录多久在后台重取一次
LEEWAY = 300                                # 验 ID token 时放宽的钟差（秒）
REVOKE_DELAYS = (1.0, 3.0)                  # 撤销遇到网络错 / 5xx：隔几秒再试
USAGE_URL = "https://chatgpt.com/settings/usage"
TERMINAL = {"invalid_grant", "invalid_refresh_token", "token_expired", "refresh_token_expired", "refresh_token_invalidated",
            "refresh_token_reused"}         # 刷新令牌不能用了：清掉、要重连（官方 errors-and-recovery 的 Refresh errors）
TRANSPORT: httpx.AsyncBaseTransport | None = None  # 测试时换成 httpx.MockTransport（授权服务器和 /v1/models 都走它）
SECRET_KEYS = ("access_token", "refresh_token", "id_token")


class PlanError(Exception):
    """授权、断开这类操作没成：一句给人看的原因（不含令牌）。"""


def cfg() -> dict:
    """server.json 的 engine.chatgpt（false = 关掉）。"""
    from . import models
    v = models.cfg().get("chatgpt")
    return {"enabled": False} if v is False else v if isinstance(v, dict) else {}


def enabled() -> bool:
    return cfg().get("enabled", True) is not False


# —— 文件：目录 0700、文件 0600、原子写 ———————————————————————————————————————————————

def data_path() -> Path:
    """<data_dir>/chatgpt（只算路径，不建：没用过套餐的服务器上什么都不多）。"""
    return settings.data_dir / "chatgpt"


def base_dir() -> Path:
    """要写东西时才建：目录 0700，再放一个 .gitignore。"""
    d = data_path()
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    ignore = d / ".gitignore"  # 万一 data_dir 放在仓库里：这一夹东西也不进 git
    if not ignore.exists():
        with contextlib.suppress(OSError):
            ignore.write_text("*\n", encoding="utf8")
    return d


def write_private(path: Path, data: dict) -> None:
    """原子写：同一目录里先写一个 0600 的临时文件、fsync，再 os.replace 换上去（中途断电不会留下半个文件，也不会有别人读得到的一刻）。"""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    with contextlib.suppress(OSError):  # 目录项也落盘
        dfd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)


def read_json(path: Path) -> dict | None:
    try:
        v = json.loads(path.read_text(encoding="utf8"))
    except (OSError, ValueError):
        return None
    return v if isinstance(v, dict) else None


def state() -> dict:
    return read_json(data_path() / "state.json") or {}


def save_state(**changes) -> dict:
    s = {**state(), **changes}
    write_private(base_dir() / "state.json", s)
    return s


def record_path(client_id: str) -> Path:
    return data_path() / f"client-{re.sub(r'[^A-Za-z0-9_-]', '_', client_id)[:120]}.json"


def record(client_id: str | None = None) -> dict | None:
    """一个 client 的凭据记录；不给 client_id = 现在用的那个。"""
    cid = client_id or state().get("active")
    return read_json(record_path(cid)) if cid else None


def has_tokens(rec: dict | None) -> bool:
    return bool(rec and rec.get("access_token") and rec.get("refresh_token"))


def plan_on(rec: dict | None) -> bool:
    return bool(rec) and PLAN_SCOPE in (rec.get("scopes") or [])


# —— 这台机器的 host id（RFC 9278 公钥指纹 URI，第一次用时生成） ————————————————————————————

def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def host_id() -> str:
    """ext_agent_host_id：这台机器一个，生成一次存下来（P-256 公钥的 RFC 7638 指纹，写成 RFC 9278 的 URI；私钥一起存着，官方现在不验，
    留着以后要证明持有时用）。不是身份、不是令牌。"""
    path = base_dir() / "host.json"  # 只在开始授权时调：这时才建目录
    h = read_json(path)
    if h and isinstance(h.get("ext_agent_host_id"), str):
        return h["ext_agent_host_id"]
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    key = ec.generate_private_key(ec.SECP256R1())
    nums = key.public_key().public_numbers()
    jwk = json.dumps({"crv": "P-256", "kty": "EC", "x": _b64(nums.x.to_bytes(32, "big")), "y": _b64(nums.y.to_bytes(32, "big"))},
                     separators=(",", ":"), sort_keys=True)
    hid = "urn:ietf:params:oauth:jwk-thumbprint:sha-256:" + _b64(hashlib.sha256(jwk.encode()).digest())
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    write_private(path, {"ext_agent_host_id": hid, "private_key": pem, "created_at": _iso(time.time())})
    return hid


# —— 时间、JWT ———————————————————————————————————————————————————————————————————

def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


def _when(v: Any) -> float | None:
    """earliest_refresh_at 这类时间：Unix 秒、毫秒，或者 ISO 8601 → Unix 秒；认不出 → None。"""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v) / 1000 if v > 1e12 else float(v)
    if isinstance(v, str) and v.strip():
        s = v.strip()
        if re.fullmatch(r"\d+(?:\.\d+)?", s):
            return _when(float(s))
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
        return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp()
    return None


def jwt_parts(token: str) -> tuple[dict, dict, bytes, bytes]:
    """→ (头, 载荷, 签名, 签名的那一段)。不验签。格式不对抛 ValueError。"""
    parts = (token or "").split(".")
    if len(parts) != 3:
        raise ValueError("not a JWT")
    head, body = json.loads(_unb64(parts[0])), json.loads(_unb64(parts[1]))
    if not isinstance(head, dict) or not isinstance(body, dict):
        raise ValueError("not a JWT")
    return head, body, _unb64(parts[2]), f"{parts[0]}.{parts[1]}".encode()


def _expiry(access: str, expires_in: Any) -> float:
    """访问令牌什么时候过期：expires_in 算出来的；访问令牌本身是 JWT、写了更早的 exp 就用它（OpenClaw 的做法：只会更早）。"""
    now = time.time()
    try:
        at = now + max(0.0, float(expires_in))
    except (TypeError, ValueError):
        at = now + 3600
    with contextlib.suppress(ValueError, TypeError):
        exp = jwt_parts(access)[1].get("exp")
        if isinstance(exp, (int, float)) and not isinstance(exp, bool) and exp > 0:
            at = min(at, float(exp))
    return at


def verify_id_token(token: str, jwks: dict, *, client_id: str, nonce: str | None = None, now: float | None = None) -> dict:
    """验 ID token（官方 sign-in 第 4 步）：签名对得上官方 JWKS 里的公钥（只认 RS256 / ES256），iss = ISSUER，aud 含 client_id（多个时
    azp 要是它），没过期，nonce 对得上（给了的话），有 sub。→ 载荷；不对抛 PlanError。"""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
    bad = PlanError(L("ChatGPT 返回的身份对不上，没有连上（请重新开始授权）", "ChatGPT's sign-in identity couldn't be verified; start again"))
    try:
        head, claims, sig, signed = jwt_parts(token)
    except (ValueError, TypeError) as e:
        raise bad from e
    alg, kid = head.get("alg"), head.get("kid")
    keys = [k for k in (jwks or {}).get("keys") or [] if isinstance(k, dict) and (not kid or k.get("kid") == kid)]
    try:
        if alg == "RS256":
            k = next(k for k in keys if k.get("kty") == "RSA")
            pub = rsa.RSAPublicNumbers(int.from_bytes(_unb64(k["e"]), "big"), int.from_bytes(_unb64(k["n"]), "big")).public_key()
            pub.verify(sig, signed, padding.PKCS1v15(), hashes.SHA256())
        elif alg == "ES256":
            k = next(k for k in keys if k.get("kty") == "EC" and k.get("crv") == "P-256")
            pub = ec.EllipticCurvePublicNumbers(int.from_bytes(_unb64(k["x"]), "big"), int.from_bytes(_unb64(k["y"]), "big"),
                                                ec.SECP256R1()).public_key()
            if len(sig) != 64:
                raise bad
            pub.verify(encode_dss_signature(int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big")), signed, ec.ECDSA(hashes.SHA256()))
        else:
            raise bad  # 不认 none、HS256 这类
    except (StopIteration, KeyError, ValueError, TypeError, InvalidSignature) as e:
        raise bad from e
    t = time.time() if now is None else now
    aud = claims.get("aud")
    auds = aud if isinstance(aud, list) else [aud]
    exp, iat = claims.get("exp"), claims.get("iat")
    if (claims.get("iss") != ISSUER or client_id not in auds or (len(auds) > 1 and claims.get("azp") != client_id)
            or (claims.get("azp") is not None and claims.get("azp") != client_id)
            or not isinstance(exp, (int, float)) or exp < t - LEEWAY or not isinstance(iat, (int, float)) or iat > t + LEEWAY
            or (nonce is not None and not secrets.compare_digest(str(claims.get("nonce") or ""), nonce))
            or not isinstance(claims.get("sub"), str) or not claims["sub"]):
        raise bad
    return claims


def plan_type(claims: dict) -> str | None:
    """ID token 里写了套餐档位就记下（plus / pro……），没写 = None。"""
    auth = claims.get("https://api.openai.com/auth")
    v = auth.get("chatgpt_plan_type") if isinstance(auth, dict) else None
    return v if isinstance(v, str) and re.fullmatch(r"[\w .-]{1,40}", v) else None


# —— 跟授权服务器说话 ——————————————————————————————————————————————————————————————

def _client(timeout: float = 30) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=10), transport=TRANSPORT, follow_redirects=False)


_discovery: tuple[float, dict] | None = None
_jwks: tuple[float, dict] | None = None


async def discovery() -> dict:
    """发现文档（一小时的缓存）：jwks_uri、revocation_endpoint。取不到就用官方的默认位置。"""
    global _discovery
    if _discovery and time.monotonic() - _discovery[0] < 3600:
        return _discovery[1]
    doc: dict = {}
    with contextlib.suppress(httpx.HTTPError, ValueError):
        async with _client() as c:
            r = await c.get(f"{ISSUER}/.well-known/openid-configuration")
            if r.status_code == 200 and isinstance(r.json(), dict):
                doc = r.json()
    if doc:
        _discovery = (time.monotonic(), doc)
    return doc


async def jwks(fresh: bool = False) -> dict:
    global _jwks
    if not fresh and _jwks and time.monotonic() - _jwks[0] < 3600:
        return _jwks[1]
    url = str((await discovery()).get("jwks_uri") or f"{ISSUER}/.well-known/jwks.json")
    try:
        async with _client() as c:
            r = await c.get(url)
        keys = r.json() if r.status_code == 200 else {}
    except (httpx.HTTPError, ValueError) as e:
        raise PlanError(L("连不上 ChatGPT 取验证身份用的公钥，过一会儿再试", "Couldn't reach ChatGPT to verify the sign-in; try again shortly")) from e
    if not isinstance(keys, dict) or not isinstance(keys.get("keys"), list):
        raise PlanError(L("ChatGPT 给的公钥不像样，没有连上", "ChatGPT returned an invalid key set; not connected"))
    _jwks = (time.monotonic(), keys)
    return keys


async def check_id_token(token: str, client_id: str, nonce: str | None) -> dict:
    try:
        return verify_id_token(token, await jwks(), client_id=client_id, nonce=nonce)
    except PlanError:
        return verify_id_token(token, await jwks(fresh=True), client_id=client_id, nonce=nonce)  # 换过公钥：重取一次


async def token_request(form: dict) -> tuple[int, dict]:
    """POST 令牌端点（表单）→ (状态码, JSON)。连不上抛 httpx.HTTPError。回来的东西可能带令牌：不打印、不放进错误里。"""
    async with _client() as c:
        r = await c.post(f"{ISSUER}/api/accounts/oauth/token", data=form, headers={"Accept": "application/json"})
    try:
        j = r.json()
    except ValueError:
        j = {}
    return r.status_code, j if isinstance(j, dict) else {}


def _oauth_error(j: dict) -> str:
    e = j.get("error")
    if isinstance(e, dict):
        e = e.get("code") or e.get("type")
    return str(e or "")[:60]


# —— 授权：开始、回调（浏览器落到监听上 / 用户粘回来）、换令牌 ————————————————————————————————————

@dataclass
class Attempt:
    client_id: str                     # 这一次用的：首次注册是 dynamic_agent_client，重连是存下的 oaiapp_…
    registering: bool
    state: str
    nonce: str
    verifier: str
    redirect: str
    port: int
    expires: float
    previous: str | None = None        # 之前连着的 client：这一次连上别的账号以后撤销、清掉它
    status: str = "pending"            # pending / done / error / expired / cancelled
    error: str | None = None
    server: Any = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    timer: Any = None


_attempt: Attempt | None = None


async def _listen(handler) -> tuple[Any, int]:
    """在 127.0.0.1 上起回调监听：先试 1455，被占了用随机端口。"""
    for port in (PORT, 0):
        try:
            srv = await asyncio.start_server(handler, "127.0.0.1", port)
        except OSError:
            continue
        return srv, srv.sockets[0].getsockname()[1]
    raise PlanError(L("这台服务器上起不了回调监听", "Couldn't start the sign-in callback listener on this server"))


def _page(ok: bool, text: str) -> bytes:
    title = "OpenMousse"
    body = (f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width'><title>{title}</title></head>"
            f"<body style='font-family:system-ui;margin:3em auto;max-width:32em;padding:0 1em'><h2>{'✓' if ok else '✗'} {title}</h2>"
            f"<p>{text}</p></body></html>")
    return body.encode("utf8")


async def _serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """回调监听的一个连接：只认 GET /auth/callback，别的 404。回的页面只有固定的话，不回显参数。"""
    status, body = 404, _page(False, "Not found")
    try:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
        parts = head.split(b"\r\n", 1)[0].decode("latin-1").split(" ")
        if len(parts) >= 2 and parts[0] == "GET" and urlsplit(parts[1]).path == CALLBACK:
            try:
                await finish(parse_qs(urlsplit(parts[1]).query))
                status, body = 200, _page(True, L("ChatGPT 套餐连好了，回 OpenMousse 就行，这一页可以关掉。",
                                                  "Your ChatGPT plan is connected. Go back to OpenMousse; you can close this page."))
            except PlanError as e:
                status, body = 400, _page(False, L("没有连上：", "Not connected: ") + _html(str(e)))
            except Exception as e:  # noqa: BLE001 — 存凭据这类意外：给浏览器一句话，服务器日志里留个名字（不带内容）
                print(f"[engine] ChatGPT 套餐的回调没处理完：{type(e).__name__}")
                status, body = 500, _page(False, L("服务器这边出错了，回 OpenMousse 重新开始", "Something went wrong on the server; start again in OpenMousse"))
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, asyncio.TimeoutError, ConnectionError, UnicodeError):
        pass
    try:
        writer.write(f"HTTP/1.1 {status} {'OK' if status == 200 else 'Error'}\r\nContent-Type: text/html; charset=utf-8\r\n"
                     f"Content-Length: {len(body)}\r\nCache-Control: no-store\r\nReferrer-Policy: no-referrer\r\nConnection: close\r\n\r\n".encode()
                     + body)
        await writer.drain()
    except ConnectionError:
        pass
    finally:
        writer.close()


def _html(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _close(a: Attempt) -> None:
    if a.timer is not None:
        a.timer.cancel()
        a.timer = None
    if a.server is not None:
        a.server.close()
        a.server = None


def _expire(a: Attempt) -> None:
    if a.status == "pending":
        a.status, a.error = "expired", L("等了太久，这次授权作废了，请重新开始", "This sign-in took too long and expired; start again")
    _close(a)


async def start_login(account: str = "auto") -> dict:
    """开始一次授权（见文件开头）。→ {"url", "redirect", "expiresAt", "tunnel", "registering"}。同时只有一次：再开始就把上一次作废。"""
    global _attempt
    if not enabled():
        raise PlanError(L("server.json 里关掉了 ChatGPT 套餐（engine.chatgpt = false）", "The ChatGPT plan is turned off in server.json (engine.chatgpt = false)"))
    cancel_login()
    st = state()
    active = st.get("active")
    saved = st.get("saved") or active
    rec = record(saved) if saved and account != "new" else None
    registering = not (rec and CLIENT.match(str(rec.get("client_id") or "")))
    client_id = REGISTER if registering else rec["client_id"]
    hid = host_id()
    a = Attempt(client_id=client_id, registering=registering, state=secrets.token_urlsafe(32), nonce=secrets.token_urlsafe(32),
                verifier=secrets.token_urlsafe(64), redirect="", port=0, expires=time.time() + LOGIN_TTL, previous=active)
    a.server, a.port = await _listen(_serve)
    a.redirect = f"http://127.0.0.1:{a.port}{CALLBACK}"
    a.timer = asyncio.get_running_loop().call_later(LOGIN_TTL, _expire, a)
    params = {"response_type": "code", "client_id": client_id, "redirect_uri": a.redirect, "scope": SCOPE, "resource": RESOURCE,
              "state": a.state, "nonce": a.nonce, "code_challenge": _b64(hashlib.sha256(a.verifier.encode()).digest()),
              "code_challenge_method": "S256", "ext_agent_host_id": hid}
    if registering:
        params["agent_name_hint"] = APP
    else:
        if rec.get("email"):
            params["login_hint"] = rec["email"]  # 不带 id_token_hint：授权网址要交给 app，ID token 不出这台服务器
        if not plan_on(rec):
            params["prompt"] = "consent"  # 上次没开套餐：完整的 scope 再问一次（官方：force_reconsent 等 OpenAI 确认上线以后才用）
    _attempt = a
    try:
        user, host = getpass.getuser(), socket.gethostname()
    except (OSError, KeyError):
        user, host = "you", "this-server"
    return {"url": f"{ISSUER}/api/accounts/authorize?{urlencode(params)}", "redirect": a.redirect, "expiresAt": _iso(a.expires),
            "tunnel": f"ssh -N -L {a.port}:127.0.0.1:{a.port} {user}@{host}", "registering": registering}


def login_status() -> dict:
    """授权进行到哪。只读（同步接口在线程池里也调它）：过期了照实说，关监听交给事件循环里的定时器（_expire）。"""
    a = _attempt
    if a is None:
        return {"status": "none"}
    status, error = a.status, a.error
    if status == "pending" and time.time() > a.expires:
        status, error = "expired", L("等了太久，这次授权作废了，请重新开始", "This sign-in took too long and expired; start again")
    return {"status": status, "error": error, "redirect": a.redirect, "expiresAt": _iso(a.expires)}


def cancel_login() -> None:
    global _attempt
    if _attempt is not None:
        if _attempt.status == "pending":
            _attempt.status = "cancelled"
        _close(_attempt)
    _attempt = None


def _one(q: dict, key: str) -> str | None:
    v = q.get(key)
    if isinstance(v, list):
        if len(v) != 1:
            return None
        v = v[0]
    return v if isinstance(v, str) else None


async def submit_callback(text: str) -> dict:
    """用户从手机浏览器地址栏粘回来的那一整串（或者只有 ?code=…&state=… 那一段）。端口、路径要和这一次的回调地址一样。"""
    a = _attempt
    s = (text or "").strip()
    if a is None or not s:
        raise PlanError(L("现在没有在等的授权，请先开始", "No sign-in is waiting; start one first"))
    if s.startswith(("127.0.0.1", "localhost")):  # 有的浏览器复制出来不带 http://
        s = "http://" + s
    u = urlsplit(s if "://" in s or s.startswith("/") or s.startswith("?") else "?" + s)
    if u.scheme or u.netloc:
        if u.scheme != "http" or u.hostname != "127.0.0.1" or u.port != a.port or u.path != CALLBACK:
            raise PlanError(L("这不是这一次授权的回调地址（端口或路径对不上），请把浏览器地址栏里的整串复制过来",
                              "That isn't this sign-in's callback address (port or path differ); copy the whole address from the browser"))
    return await finish(parse_qs(u.query))


async def finish(q: dict) -> dict:
    """回调（浏览器落到监听上，或者用户粘回来）：验 state → 处理 error → 认 client_id → 换令牌 → 验 ID token、scope → 存。→ status()。"""
    a = _attempt
    if a is None:
        raise PlanError(L("现在没有在等的授权，请先开始", "No sign-in is waiting; start one first"))
    async with a.lock:
        if a.status == "done":
            return status()
        if a.status == "pending" and time.time() > a.expires:
            _expire(a)
        if a.status != "pending":
            raise PlanError(a.error or L("这次授权已经结束了，请重新开始", "This sign-in is over; start again"))
        got = _one(q, "state")
        if not got or not secrets.compare_digest(got, a.state):  # 对不上不作废这一次：可能是旧的标签页，真的那个还会来
            raise PlanError(L("state 对不上（不是这一次授权的回调），没有处理", "The state doesn't match this sign-in; ignored"))
        if err := _one(q, "error"):
            a.status = "error"
            a.error = (L("你在 ChatGPT 里拒绝了授权，没有连上", "You declined in ChatGPT, so nothing was connected") if err == "access_denied"
                       else L(f"ChatGPT 授权没成（{err[:60]}）", f"ChatGPT sign-in failed ({err[:60]})"))
            _close(a)
            raise PlanError(a.error)
        returned = _one(q, "client_id")
        if a.registering:
            if not returned or not CLIENT.match(returned):  # 首次注册的回调必须带签发的 client_id，没有就是注册没完成
                raise _fail(a, L("ChatGPT 没有给出这次注册的 client_id，注册没完成，请重新开始",
                                 "ChatGPT didn't return this registration's client_id; start again"))
            client_id = returned
        else:
            if returned is not None and returned != a.client_id:  # 重连不许换掉已有的注册
                raise _fail(a, L("ChatGPT 返回的 client_id 和这个账号存下的不一样，没有处理", "ChatGPT returned a different client_id; ignored"))
            client_id = a.client_id
        code = _one(q, "code")
        if not code:
            raise _fail(a, L("回调里没有授权码", "The callback has no authorization code"))
        try:
            st, j = await token_request({"grant_type": "authorization_code", "client_id": client_id, "code": code, "code_verifier": a.verifier,
                                         "redirect_uri": a.redirect, "resource": RESOURCE})
        except httpx.HTTPError as e:
            raise PlanError(L("连不上 ChatGPT 换令牌，过一会儿再粘一次", "Couldn't reach ChatGPT to finish signing in; try again shortly")) from e
        if st != 200:
            code_ = _oauth_error(j)
            raise _fail(a, L("授权码过期或者用过了，请重新开始", "The authorization code expired or was already used; start again")
                        if code_ == "invalid_grant" else L(f"ChatGPT 没给令牌（HTTP {st}{' ' + code_ if code_ else ''}）",
                                                           f"ChatGPT didn't issue tokens (HTTP {st}{' ' + code_ if code_ else ''})"))
        try:
            rec = await _record_from(j, client_id, a.nonce, previous=None if a.registering else record(client_id))
        except PlanError as e:  # 授权码已经用掉了：这一次作废，重新开始
            raise _fail(a, str(e)) from None
        await _connected(rec, a.previous)  # 在锁里做完：浏览器和粘贴同时到时，后到的那个看到的就是连好了的
        a.status = "done"
        _close(a)
    return status()


def _fail(a: Attempt, why: str) -> PlanError:
    a.status, a.error = "error", why
    _close(a)
    return PlanError(why)


async def _record_from(j: dict, client_id: str, nonce: str | None, previous: dict | None) -> dict:
    """令牌端点的回话 → 凭据记录（格式照官方的例子）。验 ID token；重连时身份必须是原来那个。"""
    access, refresh, idt = j.get("access_token"), j.get("refresh_token"), j.get("id_token")
    if not (isinstance(access, str) and access and isinstance(refresh, str) and refresh and isinstance(idt, str) and idt
            and str(j.get("token_type") or "").lower() == "bearer"):
        raise PlanError(L("ChatGPT 给的令牌不全（要有 access、refresh、ID token），没有连上", "ChatGPT didn't return a complete set of tokens; not connected"))
    claims = await check_id_token(idt, client_id, nonce)
    if previous and previous.get("subject") and previous["subject"] != claims["sub"]:
        raise PlanError(L("登录的是另一个 ChatGPT 账号：要换账号请选「连另一个账号」", "That's a different ChatGPT account; choose \"another account\" to switch"))
    scopes = sorted(set(str(j.get("scope") or "").split()))  # 以令牌端点给的为准（官方）；没给 = 没开套餐
    now = time.time()
    return {"email": claims.get("email") if isinstance(claims.get("email"), str) else None, "issuer": ISSUER, "subject": claims["sub"],
            "client_id": client_id, "ext_agent_host_id": host_id(), "id_token": idt, "access_token": access, "refresh_token": refresh,
            "token_type": "Bearer", "expires_in": j.get("expires_in"), "expires_at": _expiry(access, j.get("expires_in")),
            "earliest_refresh_at": _when(j.get("earliest_refresh_at")), "scopes": scopes, "plan": plan_type(claims), "saved_at": _iso(now)}


async def _connected(rec: dict, previous: str | None) -> None:
    """存下新连上的这份；之前连着别的 client（另一个账号 / 工作区）：撤销、清掉它（只连一个账号）；模型目录当场取一次（取不到也算连上了）。"""
    base_dir()
    write_private(record_path(rec["client_id"]), rec)
    save_state(active=rec["client_id"], saved=rec["client_id"], error=None, reconnect=None, catalog=None, connected_at=_iso(time.time()))
    from . import fallback
    fallback.forget("chatgpt")  # 以前因为没连上 / 401 记的冷却作废
    if previous and previous != rec["client_id"]:
        old = record(previous)
        if has_tokens(old):
            await _revoke_and_clear(old)
    if plan_on(rec):
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(refresh_catalog(), 10)


_bg: set[asyncio.Task] = set()


def _spawn(coro) -> None:
    try:
        t = asyncio.get_running_loop().create_task(coro)
    except RuntimeError:
        coro.close()
        return
    _bg.add(t)
    t.add_done_callback(_bg.discard)


# —— 令牌：现在能用的、刷新（排队）、401、撤销 ——————————————————————————————————————————————

_locks: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()  # 事件循环 → 刷新的锁（测试里每个 asyncio.run 一个新循环）


def _lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    lk = _locks.get(loop)
    if lk is None:
        lk = _locks[loop] = asyncio.Lock()
    return lk


def usable() -> bool:
    """现在能不能跑 chatgpt/…：没关、连着、scope 里有用套餐那一项、没标要重连。不连网。"""
    if not enabled():
        return False
    st = state()
    rec = record(st.get("active"))
    return has_tokens(rec) and plan_on(rec) and not st.get("reconnect")


def _not_ready() -> EngineError:
    st = state()
    rec = record(st.get("active"))
    if not enabled():
        why = L("server.json 里关掉了 ChatGPT 套餐", "The ChatGPT plan is turned off in server.json")
    elif st.get("reconnect"):
        why = L("ChatGPT 套餐要重新连一次（登录过期，或者在 ChatGPT 里断开了）：在设置里点 Continue with ChatGPT",
                "Your ChatGPT plan needs to be connected again (the sign-in expired or was disconnected in ChatGPT): Continue with ChatGPT in Settings")
    elif not has_tokens(rec):
        why = L("还没连 ChatGPT 套餐：在设置里点 Continue with ChatGPT 连一次，或者换一个用 API 密钥的模型",
                "Your ChatGPT plan isn't connected: connect it in Settings (Continue with ChatGPT), or pick a model with an API key")
    else:
        why = L("连上了 ChatGPT，但没有开启用套餐：重新连一次并同意用套餐，或者用你自己的 API 密钥",
                "ChatGPT is connected, but plan use isn't enabled: connect again and allow plan use, or use your own API key")
    return EngineError(why, kind="config", status=401)


async def access_token(*, force: bool = False, failed: str | None = None, min_valid: float = 0) -> str:
    """现在能用的访问令牌：快过期了先刷新（排队）。force = 推理回了 401，强制换一个（failed = 回 401 的那个：锁里发现别人已经换过就直接用）。
    min_valid = 至少还要能用这么多秒（交给 Codex 跑一个任务时）。没连、没开套餐、要重连 → EngineError(config, 401)。"""
    if not usable():
        raise _not_ready()
    rec = record()
    assert rec is not None
    left = float(rec.get("expires_at") or 0) - time.time()
    if not force and left > max(REFRESH_BEFORE, min_valid):
        return rec["access_token"]
    early = rec.get("earliest_refresh_at")
    if not force and left > 30 and min_valid <= 0 and isinstance(early, (int, float)) and time.time() < early:
        return rec["access_token"]  # 还没到能刷新的时候、令牌又还能用：先用着
    return (await refresh(rec, force=force, failed=failed, min_valid=min_valid))["access_token"]


async def _flock(fd: int, timeout: float = 30.0) -> None:
    """文件锁（别的进程也在刷新时排队）：不阻塞地试，最多等 timeout 秒。"""
    t0 = time.monotonic()
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() - t0 > timeout:
                raise EngineError("another process has been refreshing the ChatGPT sign-in for too long", retryable=True) from None
            await asyncio.sleep(0.05)


async def refresh(rec: dict, *, force: bool = False, failed: str | None = None, min_valid: float = 0) -> dict:
    """刷新（同一份凭据排队：进程里一把锁 + 文件锁，锁里重读文件，排在前面的刷过了就直接用新的）。→ 新的记录。
    刷新令牌不能用了：清掉、标要重连、抛 EngineError(config)；网络 / 5xx：凭据不动，抛能重试的 EngineError。"""
    async with _lock():
        fd = os.open(base_dir() / "refresh.lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            await _flock(fd)
            cur = record(rec["client_id"]) or rec
            if not has_tokens(cur):
                raise _not_ready()
            fresh = float(cur.get("expires_at") or 0) - time.time() > max(REFRESH_BEFORE, min_valid)
            if fresh and (not force or (failed and cur["access_token"] != failed)):
                return cur  # 排在前面的已经刷过了（或者本来就够新）：直接用，不拿旧的刷新令牌再刷（那会被当成重放）
            try:
                st, j = await token_request({"grant_type": "refresh_token", "client_id": cur["client_id"], "refresh_token": cur["refresh_token"],
                                             "resource": RESOURCE})
            except httpx.HTTPError as e:
                note_error("refresh_unreachable", f"can't reach the token endpoint: {type(e).__name__}")
                raise EngineError(f"can't reach ChatGPT to refresh the sign-in: {type(e).__name__}", retryable=True) from e
            if st != 200:
                code = _oauth_error(j)
                if code in TERMINAL or code == "invalid_client" or (st in (400, 401) and not code):
                    _clear_tokens(cur)
                    mark_reconnect(code or f"refresh_http_{st}")
                    if code == "invalid_client":  # 这个注册本身不认了：下次连接重新注册，不再用它
                        save_state(active=None, saved=None)
                    raise _not_ready()
                note_error(code or f"refresh_http_{st}", f"token refresh failed (HTTP {st})")
                raise EngineError(f"ChatGPT sign-in refresh failed (HTTP {st}{' ' + code if code else ''})", status=st,
                                  retryable=st >= 500 or st == 429)
            access, new_refresh = j.get("access_token"), j.get("refresh_token") or cur["refresh_token"]
            if not isinstance(access, str) or not access:
                raise EngineError("ChatGPT's refresh response had no access token", kind="output", retryable=True)
            out = dict(cur)
            if isinstance(j.get("id_token"), str) and j["id_token"]:
                try:
                    claims = await check_id_token(j["id_token"], cur["client_id"], None)
                except PlanError as e:
                    raise EngineError(str(e), kind="config", status=401) from e
                if claims["sub"] != cur.get("subject"):
                    _clear_tokens(cur)
                    mark_reconnect("account_changed")
                    raise _not_ready()
                out["id_token"] = j["id_token"]
                out["plan"] = plan_type(claims) or cur.get("plan")
            if j.get("scope"):
                out["scopes"] = sorted(set(str(j["scope"]).split()))  # 刷新时没给 scope = 授权没变
            out.update(access_token=access, refresh_token=new_refresh, expires_in=j.get("expires_in"),
                       expires_at=_expiry(access, j.get("expires_in")), earliest_refresh_at=_when(j.get("earliest_refresh_at")),
                       saved_at=_iso(time.time()))
            write_private(record_path(cur["client_id"]), out)  # 访问令牌、到期、scope、新的刷新令牌一起换
            return out
        finally:
            with contextlib.suppress(OSError):
                fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)


async def after_401(failed: str) -> str:
    """推理回了 401：强制刷新一次，拿新令牌再试一次（providers.stream 调）。刷新也不行 → 抛 EngineError(config)。"""
    return await access_token(force=True, failed=failed)


def mark_reconnect(why: str) -> None:
    """要重连（刷新令牌不能用了、刷新以后还是 401）：不再用这份令牌，app 里提示重新连。"""
    save_state(reconnect={"why": str(why)[:80], "at": _iso(time.time())}, error={"code": str(why)[:80], "message": "sign in again", "at": _iso(time.time())})


def note_error(code: str | None, message: str) -> None:
    """记最近一次的错（给 /api/engine/status；不含令牌）。"""
    save_state(error={"code": (code or "")[:80] or None, "message": str(message)[:300], "at": _iso(time.time())})


def note_ok() -> None:
    if state().get("error"):
        save_state(error=None)


def _clear_tokens(rec: dict) -> None:
    """清掉一份凭据的令牌（留着 client_id、sub、邮箱、host id：下次重连用同一个注册）。"""
    path = record_path(rec["client_id"])
    cur = read_json(path) or rec
    write_private(path, {k: v for k, v in cur.items() if k not in SECRET_KEYS and k not in ("expires_at", "expires_in", "earliest_refresh_at")})


async def revoke(rec: dict) -> bool:
    """撤销刷新令牌（官方 End the renewable session）。→ 撤销确认了没有。网络错、5xx 退避重试几次。"""
    if not rec.get("refresh_token"):
        return True
    url = str((await discovery()).get("revocation_endpoint") or "")
    if not url:
        return False
    form = {"token": rec["refresh_token"], "token_type_hint": "refresh_token", "client_id": rec["client_id"]}
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


async def _revoke_and_clear(rec: dict) -> bool:
    ok = await revoke(rec)
    _clear_tokens(rec)
    return ok


async def disconnect() -> dict:
    """断开：先撤销（发现文档里的 revocation_endpoint），再清掉本机令牌；没确认撤销也清，告诉用户去 ChatGPT 设置里断开。"""
    cancel_login()
    st = state()
    rec = record(st.get("active"))
    confirmed = True
    if has_tokens(rec):
        confirmed = await _revoke_and_clear(rec)
    save_state(active=None, saved=st.get("saved") or st.get("active"), reconnect=None, error=None, catalog=None)
    return {"ok": True, "revoked": confirmed,
            "note": None if confirmed else L("本机的令牌已经清掉了，但没能确认 ChatGPT 那边撤销了：可以去 ChatGPT 设置里把 OpenMousse 断开",
                                             "Cleared on this server, but ChatGPT didn't confirm the revocation: you can disconnect OpenMousse in ChatGPT settings")}


# —— 模型目录 ——————————————————————————————————————————————————————————————————————

_catalog_busy = False


async def refresh_catalog() -> list[dict]:
    """这个账号能用的模型（官方 models-and-inference：GET /v1/models 的 models，visibility = list 的，按服务器的顺序）→ 存进 state.json。
    取不到就留着上一次的。"""
    global _catalog_busy
    if _catalog_busy:
        return list((state().get("catalog") or {}).get("models") or [])
    _catalog_busy = True
    try:
        tok = await access_token()
        async with _client() as c:
            r = await c.get(f"{API}/models", headers={"Authorization": f"Bearer {tok}", "Accept": "application/json"})
        if r.status_code != 200:
            return list((state().get("catalog") or {}).get("models") or [])
        j = r.json()
        rows = j.get("models") if isinstance(j, dict) else None
        out: list[dict] = []
        if isinstance(rows, list):
            for m in rows:
                slug = m.get("slug") if isinstance(m, dict) else None
                if isinstance(slug, str) and m.get("visibility") == "list" and _slug_ok(slug):
                    out.append({"id": slug, "name": str(m.get("display_name") or slug)[:80]})
        save_state(catalog={"at": time.time(), "models": out})
        return out
    except (EngineError, httpx.HTTPError, ValueError):
        return list((state().get("catalog") or {}).get("models") or [])
    finally:
        _catalog_busy = False


def _slug_ok(slug: str) -> bool:
    from . import models
    return bool(models.NAME.match(slug))


def model_ids() -> list[str]:
    """app 里多出来的 chatgpt/…：能用（usable）才有。engine.chatgpt.models 写了就用它，不写用账号的目录（旧了在后台重取）。"""
    if not usable():
        return []
    conf = cfg().get("models")
    if isinstance(conf, list):
        out = []
        for x in conf:
            s = str(x).strip() if isinstance(x, str) else ""
            s = s if s.startswith("chatgpt/") else f"chatgpt/{s}" if s else ""
            if s and _slug_ok(s.partition("/")[2]) and s not in out:
                out.append(s)
        return out
    cat = state().get("catalog") or {}
    if not cat or time.time() - float(cat.get("at") or 0) > CATALOG_EVERY:
        _spawn(refresh_catalog())
    return [f"chatgpt/{m['id']}" for m in cat.get("models") or [] if isinstance(m, dict) and isinstance(m.get("id"), str) and _slug_ok(m["id"])]


# —— 推理用的 provider（providers.stream 调） ————————————————————————————————————————————

def provider_stub() -> dict:
    """models.provider("chatgpt")：不连网的那一份（备用链认冷却用）。key 是空的，推理前 provider() 再填真的令牌。"""
    from . import models
    rec = record() if usable() else None
    return {"name": "chatgpt", "api": models.RESPONSES, "url": API, "key": "", "key_env": None, "plan": True, "images": True,
            "keys": [("chatgpt", rec["client_id"])] if rec else [], "key_name": "chatgpt",
            "headers": cfg().get("headers") if isinstance(cfg().get("headers"), dict) else {}}


async def provider() -> dict:
    """推理用的：provider_stub + 现在能用的访问令牌（快过期先刷新）。模型目录旧了顺手在后台重取（同步的接口里起不了后台任务）。"""
    p = provider_stub()
    p["key"] = await access_token()
    cat = state().get("catalog") or {}
    if not isinstance(cfg().get("models"), list) and time.time() - float(cat.get("at") or 0) > CATALOG_EVERY:
        _spawn(refresh_catalog())
    return p


# —— 后台同意、状态 ———————————————————————————————————————————————————————————————————

def background_allowed() -> bool:
    """用户在设置里明确同意过后台任务、定时任务、心跳花自己的套餐吗（默认没有）。"""
    b = state().get("background")
    return isinstance(b, dict) and b.get("allowed") is True


def set_background(allow: bool) -> dict:
    """记下用户的选择和时间（同意 / 收回都记）。"""
    save_state(background={"allowed": bool(allow), "at": _iso(time.time())})
    return status()


def status() -> dict:
    """/api/engine/status 的 chatgpt、GET /api/engine/chatgpt：连没连、哪个套餐、令牌快过期没有、最近一次的错。不带令牌、不带 sub / client_id。"""
    st = state()
    rec = record(st.get("active"))
    connected = has_tokens(rec)
    left = int(float(rec.get("expires_at") or 0) - time.time()) if connected else None
    bg = st.get("background") if isinstance(st.get("background"), dict) else {}
    err = st.get("error") if isinstance(st.get("error"), dict) else None
    return {"enabled": enabled(), "connected": connected, "planEnabled": connected and plan_on(rec), "ready": usable(),
            "plan": (rec or {}).get("plan") if connected else None, "email": (rec or {}).get("email") if connected else None,
            "expiresIn": left, "expiringSoon": left is not None and left < REFRESH_BEFORE, "needsReconnect": bool(st.get("reconnect")),
            "lastError": {k: err.get(k) for k in ("code", "message", "at")} if err else None,
            "background": {"allowed": bg.get("allowed") is True, "since": bg.get("at")}, "models": model_ids(), "usageUrl": USAGE_URL,
            "login": login_status()}


# —— 接口 ——————————————————————————————————————————————————————————————————————————

router = APIRouter()


class LoginIn(BaseModel):
    account: str = "auto"


class CallbackIn(BaseModel):
    url: str


class BackgroundIn(BaseModel):
    allow: bool


# 都写成 async：state.json 的读改写都在事件循环这一个线程里，不和线程池里的请求抢
@router.get("/api/engine/chatgpt")
async def get_status():
    return {"ok": True, **status()}


@router.post("/api/engine/chatgpt/login")
async def post_login(body: LoginIn):
    try:
        return {"ok": True, **await start_login("new" if body.account == "new" else "auto")}
    except PlanError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/api/engine/chatgpt/login")
async def get_login():
    return {"ok": True, **login_status()}


@router.delete("/api/engine/chatgpt/login")
async def delete_login():
    cancel_login()
    return {"ok": True}


@router.post("/api/engine/chatgpt/callback")
async def post_callback(body: CallbackIn):
    try:
        return {"ok": True, **await submit_callback(body.url)}
    except PlanError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/api/engine/chatgpt/disconnect")
async def post_disconnect():
    return await disconnect()


@router.put("/api/engine/chatgpt/background")
async def put_background(body: BackgroundIn):
    return {"ok": True, **set_background(body.allow)}


def reset() -> None:
    """测试用：进程里的缓存和进行中的授权都清掉（文件不动）。"""
    global _discovery, _jwks, _catalog_busy
    cancel_login()
    _discovery = _jwks = None
    _catalog_busy = False
