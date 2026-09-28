"""朋友（2026-09-28，社交第二层）的底子：服务器身份、名片、请求签名、好友表、档位。协议全文见 docs/social-protocol.zh-CN.md。

- 身份：每台服务器一把 Ed25519 密钥（<data_dir>/social/identity.json，0600）。kid = JWK 指纹（RFC 7638），给人看的指纹是
  SHA-256(公钥) 的 base32 前 10 位。地址可以换，钥匙不换；丢了钥匙朋友要重新加。
- 名片 /f/card：名字、公钥、对外根地址（share.public_url）、能力，按 A2A 8.4 的办法签（JWS、payload 分离、JCS 规范化、alg EdDSA）。
  A2A 名片是另一份（第三层的 a2a.py），用同一把钥匙、同一个 sign_jws()。
- 服务器之间的请求：RFC 9421 HTTP Message Signatures 的一个固定用法——签 @method、@path、content-digest（RFC 9530）和
  mousse-to（收件人 kid），带 created / nonce / keyid，alg ed25519。authenticate() 只认人：没签名 = 陌生人；签了就必须全对，否则 401。
- 表都在 grava.db：friends / friend_invites / friend_messages / social_nonces / social_settings（第一次用到时建）。
- 档位：close 亲近 / friend 朋友 / mate 同学 / stranger 陌生（表里没有的都算陌生），每档能问到什么存 social_settings.tiers。
  健康和世界树不是档位里的键：没有能打开的开关。
第三层（a2a.py、cardagent.py）用这里的 identity / sign_jws / verify_jws / authenticate / signed_post / tier_scopes / friend / card_status，
不另起一套。邀请码、加好友、朋友聊天在 friends.py。
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import ipaddress
import json
import os
import re
import secrets
import socket
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from chat import _lock, db, now_iso
from config import raw, settings

public_router = APIRouter()   # 给别的服务器的：/f/card、/f/jwks.json（public.py 挂；邀请、消息在 friends.py）

SIG_LABEL = "om"
SIG_TAG = "openmousse/1"
COMPONENTS = ("@method", "@path", "content-digest", "mousse-to")
WINDOW = 300          # created 和现在最多差几秒
NONCE_KEEP = 600      # nonce 记多久（比 2 × WINDOW 长）
BODY_MAX = 256_000    # 进来的请求 body 上限（/f/hello 自己再压到 16 KB）
FETCH_MAX = 64_000    # 取别人名片时回应的上限
TIERS = ("close", "friend", "mate", "stranger")
FRIEND_TIERS = TIERS[:3]   # 能给某个朋友选的档（陌生 = 表里没有，没法选给人）
SCOPES: dict[str, tuple[str, ...]] = {
    "calendar": ("detail", "busy", "none"),   # 日程：看详情 / 只给忙闲 / 不给
    "status": ("some", "line", "none"),       # 近况：全文 / 第一行 / 不给
    "shares": ("ask", "view", "public"),      # 分享过的东西：能追问 / 只能看 / 只看公开的
    "notes": ("view", "none"),                # 学习笔记（只给标了能分享的）
    "address": ("view", "none"),              # 住址
}
DEFAULT_TIERS: dict[str, dict[str, str]] = {
    "close": {"calendar": "detail", "status": "some", "shares": "ask", "notes": "view", "address": "view"},
    "friend": {"calendar": "busy", "status": "line", "shares": "ask", "notes": "view", "address": "none"},
    "mate": {"calendar": "busy", "status": "none", "shares": "view", "notes": "view", "address": "none"},
    "stranger": {"calendar": "none", "status": "none", "shares": "public", "notes": "none", "address": "none"},
}
FRIEND_STATUSES = ("active", "removed", "gone", "blocked")
CARD_HOOKS: list[Callable[[], dict]] = []   # 第三层往里挂：返回 {"caps": [...], "a2a": "<A2A 名片地址>"}，并进 /f/card


# —— 配置 ——

def cfg() -> dict:
    c = raw().get("social")
    return c if isinstance(c, dict) else {}


def allow_http() -> bool:
    """测试服（两套服务都在 127.0.0.1）才开：收 http:// 的根地址、取本机地址的名片。"""
    return bool(cfg().get("allow_http"))


def my_url() -> str | None:
    """对外的根地址（= share.public_url），没有就是 None：朋友打不回来，不能加朋友。"""
    import share
    u = share.public_url()
    return origin_of(u) if u else None


def my_name() -> str:
    return settings.user_name


# —— base64url、身份 ——

def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64u(text: str) -> bytes:
    if not isinstance(text, str) or not re.fullmatch(r"[A-Za-z0-9_-]*", text):
        raise ValueError("not base64url")
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def identity_file() -> Path:
    return settings.data_dir / "social" / "identity.json"


_key: Ed25519PrivateKey | None = None
_key_lock = threading.Lock()


def private_key() -> Ed25519PrivateKey:
    """这台服务器的钥匙：有就读，没有就生成（0600；两个进程同时生成时后到的读先到的那份）。"""
    global _key
    if _key is not None:
        return _key
    with _key_lock:
        if _key is not None:
            return _key
        path = identity_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(path.parent, 0o700)
        if not path.exists():
            seed = secrets.token_bytes(32)
            doc = json.dumps({"v": 1, "seed": b64u(seed), "created_at": now_iso()}) + "\n"
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                pass
            else:
                with os.fdopen(fd, "w", encoding="utf8") as f:
                    f.write(doc)
        data = json.loads(path.read_text(encoding="utf8"))
        _key = Ed25519PrivateKey.from_private_bytes(unb64u(data["seed"]))
        return _key


def public_x(key: Ed25519PrivateKey | None = None) -> str:
    from cryptography.hazmat.primitives import serialization
    k = key or private_key()
    return b64u(k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))


def jwk_of(x: str) -> dict:
    return {"kty": "OKP", "crv": "Ed25519", "x": x}


def thumbprint(x: str) -> str:
    """kid：JWK 指纹（RFC 7638，必填成员按字典序、没有空白）。"""
    return b64u(hashlib.sha256(json.dumps({"crv": "Ed25519", "kty": "OKP", "x": x}, separators=(",", ":"), sort_keys=True).encode()).digest())


def fingerprint(x: str) -> str:
    """给人看的指纹：SHA-256(公钥 32 字节) 的 base32 前 10 位，分两组（「K7Q2M 9XJ4P」）。"""
    s = base64.b32encode(hashlib.sha256(unb64u(x)).digest()).decode().rstrip("=")
    return f"{s[:5]} {s[5:10]}"


def identity() -> dict:
    x = public_x()
    return {"kid": thumbprint(x), "x": x, "jwk": jwk_of(x), "fingerprint": fingerprint(x)}


def my_kid() -> str:
    return identity()["kid"]


def public_key(x: str) -> Ed25519PublicKey:
    raw_key = unb64u(x)
    if len(raw_key) != 32:
        raise ValueError("bad key length")
    return Ed25519PublicKey.from_public_bytes(raw_key)


# —— JCS、JWS（A2A 8.4 的签法）——

def _no_floats(obj: Any) -> None:
    if isinstance(obj, float):
        raise ValueError("JCS here takes no floats")
    if isinstance(obj, dict):
        for k, v in obj.items():
            if not isinstance(k, str):
                raise ValueError("keys must be strings")
            _no_floats(v)
    elif isinstance(obj, list):
        for v in obj:
            _no_floats(v)


def jcs(obj: Any) -> bytes:
    """RFC 8785 规范化。只收字符串 / 整数 / 布尔 / null / 数组 / 对象、键是 ASCII 的 JSON（名片就是这样），
    这个范围里它和 json.dumps(sort_keys, 紧凑, 不转义非 ASCII) 一字不差。"""
    _no_floats(obj)
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _unsigned(obj: dict) -> dict:
    return {k: v for k, v in obj.items() if k != "signatures"}


def sign_jws(obj: dict) -> dict:
    """签 obj 去掉 signatures 以后的 JCS（payload 分离）：返回 A2A 的 AgentCardSignature 形状 {protected, signature}。"""
    kid = my_kid()
    protected = b64u(json.dumps({"alg": "EdDSA", "kid": kid, "typ": "JOSE"}, separators=(",", ":")).encode())
    signing_input = protected.encode() + b"." + b64u(jcs(_unsigned(obj))).encode()
    return {"protected": protected, "signature": b64u(private_key().sign(signing_input))}


def jws_kids(obj: dict) -> list[str]:
    """签名头里写的 kid（验名片时要和名片的 kid 一致）。"""
    out = []
    for s in obj.get("signatures") or []:
        try:
            out.append(str(json.loads(unb64u(s["protected"]))["kid"]))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def verify_jws(obj: dict, x: str) -> bool:
    """obj 的 signatures 里有一个是 x 这把钥匙签的（alg EdDSA 或 Ed25519），就算通过。"""
    try:
        key = public_key(x)
        payload = b64u(jcs(_unsigned(obj))).encode()
    except ValueError:
        return False
    kid = thumbprint(x)
    for s in obj.get("signatures") or []:
        try:
            header = json.loads(unb64u(s["protected"]))
            if header.get("alg") not in ("EdDSA", "Ed25519") or header.get("kid") != kid:
                continue
            key.verify(unb64u(s["signature"]), s["protected"].encode() + b"." + payload)
            return True
        except (KeyError, TypeError, ValueError, InvalidSignature):
            continue
    return False


# —— 地址 ——

def origin_of(url: str) -> str | None:
    """规范成根地址（scheme://host[:port]，小写，没有路径）；不像根地址的回 None。"""
    try:
        u = urlsplit(str(url).strip())
    except ValueError:
        return None
    if u.scheme not in ("https", "http") or not u.hostname or u.username or u.password or u.query or u.fragment:
        return None
    if u.path not in ("", "/"):
        return None
    host = u.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    default = 443 if u.scheme == "https" else 80
    try:
        port = u.port
    except ValueError:
        return None
    return f"{u.scheme}://{host}" + (f":{port}" if port and port != default else "")


def url_ok(origin: str) -> bool:
    """别人的根地址能不能用：正式环境只收 https；测试服（allow_http）才收 http。"""
    return origin.startswith("https://") or (allow_http() and origin.startswith("http://"))


async def check_host(url: str) -> None:
    """往外发请求前看一眼：解析出来是本机 / 内网 / 保留地址的不发（测试服除外），免得有人拿名片里的地址打我们自己的内网。"""
    if allow_http():
        return
    host = urlsplit(url).hostname or ""
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, host, None)
    except OSError as e:
        raise HTTPException(502, "unreachable") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            raise HTTPException(400, "address not allowed")


# —— 表 ——

_ready = False


def sdb() -> sqlite3.Connection:
    global _ready
    conn = db()
    if not _ready:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS friends (id TEXT PRIMARY KEY, kid TEXT NOT NULL UNIQUE, pub TEXT NOT NULL, url TEXT NOT NULL,
            name TEXT NOT NULL, alias TEXT, tier TEXT NOT NULL DEFAULT 'friend', status TEXT NOT NULL DEFAULT 'active',
            caps TEXT NOT NULL DEFAULT '[]', card TEXT, a2a TEXT, note TEXT, via TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, seen_at TEXT);
        CREATE TABLE IF NOT EXISTS friend_invites (id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE, note TEXT, tier TEXT NOT NULL,
            created_at TEXT NOT NULL, expires_at TEXT NOT NULL, used_at TEXT, used_by TEXT, revoked_at TEXT);
        CREATE TABLE IF NOT EXISTS friend_messages (id INTEGER PRIMARY KEY AUTOINCREMENT, friend TEXT NOT NULL, mid TEXT NOT NULL,
            dir TEXT NOT NULL, kind TEXT NOT NULL, by TEXT NOT NULL DEFAULT 'person', text TEXT NOT NULL DEFAULT '', data TEXT,
            reply_to TEXT, status TEXT NOT NULL, review TEXT, ts TEXT NOT NULL, recv_at TEXT, edited_at TEXT,
            tries INTEGER NOT NULL DEFAULT 0, next_try TEXT, error TEXT, UNIQUE (friend, dir, mid));
        CREATE INDEX IF NOT EXISTS friend_messages_friend ON friend_messages(friend, id);
        CREATE INDEX IF NOT EXISTS friend_messages_outbox ON friend_messages(dir, status, next_try);
        CREATE TABLE IF NOT EXISTS social_nonces (kid TEXT NOT NULL, nonce TEXT NOT NULL, at REAL NOT NULL, PRIMARY KEY (kid, nonce));
        CREATE TABLE IF NOT EXISTS social_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        _ready = True
    return conn


def get_setting(key: str) -> str | None:
    with _lock, sdb() as conn:
        r = conn.execute("SELECT value FROM social_settings WHERE key=?", (key,)).fetchone()
    return r["value"] if r else None


def set_setting(key: str, value: str | None) -> None:
    with _lock, sdb() as conn:
        if value is None:
            conn.execute("DELETE FROM social_settings WHERE key=?", (key,))
        else:
            conn.execute("INSERT INTO social_settings(key, value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


# —— 好友 ——

def friend_dict(r: sqlite3.Row | None) -> dict | None:
    """friends 表一行 → dict：name 按备注优先（card_name 是对方名片上的），caps 解开成数组。"""
    if r is None:
        return None
    d = dict(r)
    d["card_name"] = d["name"]
    d["name"] = (d.get("alias") or "").strip() or d["name"]
    try:
        d["caps"] = [str(c) for c in json.loads(d.get("caps") or "[]")]
    except ValueError:
        d["caps"] = []
    return d


def friend(fid: str) -> dict | None:
    with _lock, sdb() as conn:
        return friend_dict(conn.execute("SELECT * FROM friends WHERE id=?", (fid,)).fetchone())


def friend_by_kid(kid: str) -> dict | None:
    with _lock, sdb() as conn:
        return friend_dict(conn.execute("SELECT * FROM friends WHERE kid=?", (kid,)).fetchone())


def friends(statuses: tuple[str, ...] = ("active",)) -> list[dict]:
    marks = ",".join("?" * len(statuses))
    with _lock, sdb() as conn:
        rows = conn.execute(f"SELECT * FROM friends WHERE status IN ({marks}) ORDER BY created_at", statuses).fetchall()  # noqa: S608
    return [d for d in (friend_dict(r) for r in rows) if d]


# —— 档位、近况 ——

def tiers() -> dict[str, dict[str, str]]:
    """四档各自的范围：存过的覆盖默认值，认不出的键和取值丢掉。"""
    try:
        saved = json.loads(get_setting("tiers") or "{}")
    except ValueError:
        saved = {}
    out: dict[str, dict[str, str]] = {}
    for t in TIERS:
        cur = dict(DEFAULT_TIERS[t])
        mine = saved.get(t) if isinstance(saved, dict) else None
        if isinstance(mine, dict):
            for k, allowed in SCOPES.items():
                if mine.get(k) in allowed:
                    cur[k] = mine[k]
        out[t] = cur
    return out


def tier_scopes(tier: str | None) -> dict[str, str]:
    """某一档能问到什么（认不出的档按陌生算）。"""
    return tiers()[tier if tier in TIERS else "stranger"]


def set_tier_scopes(changes: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
    cur = tiers()
    for t, scopes in changes.items():
        if t not in TIERS or not isinstance(scopes, dict):
            raise HTTPException(400, f"unknown tier {t}")
        for k, v in scopes.items():
            if k not in SCOPES or v not in SCOPES[k]:
                raise HTTPException(400, f"bad scope {k}={v}")
            cur[t][k] = v
    set_setting("tiers", json.dumps(cur, ensure_ascii=False))
    return cur


def card_status() -> str:
    """用户在「我的名片 agent」里自己写的近况原文，没写就空（名片 agent 按档位切：some 全文，line 第一行）。"""
    return get_setting("status") or ""


# —— 名片 ——

def card_body() -> dict | None:
    """没签名的名片正文；没有根地址或没有称呼就是 None（不能加朋友）。"""
    url, name = my_url(), my_name()
    if not url or not name:
        return None
    ident = identity()
    caps = ["chat", "ask"]
    extra: dict[str, str] = {}
    for hook in CARD_HOOKS:
        try:
            more = hook() or {}
        except Exception:  # noqa: BLE001 — 第三层的钩子出错不能让名片出不来
            continue
        caps += [str(c) for c in more.get("caps") or [] if str(c) not in caps]
        if isinstance(more.get("a2a"), str):
            extra["a2a"] = more["a2a"]
    body = {"openmousse": "1", "kid": ident["kid"], "key": ident["jwk"], "url": url, "name": name, "caps": caps, **extra}
    digest = hashlib.sha256(jcs(body)).hexdigest()
    meta = {}
    try:
        meta = json.loads(get_setting("card_meta") or "{}")
    except ValueError:
        pass
    if meta.get("digest") != digest:  # 名片变了（地址、名字、能力）：记下时间，投递那边据此给朋友发 card
        meta = {"digest": digest, "at": now_iso()}
        set_setting("card_meta", json.dumps(meta))
    return {**body, "updated_at": meta["at"]}


def my_card() -> dict | None:
    body = card_body()
    if body is None:
        return None
    return {**body, "signatures": [sign_jws(body)]}


def card_digest() -> str | None:
    """名片内容（不含时间和签名）的摘要：投递那边拿它判断要不要给朋友发一条 card。"""
    body = card_body()
    if body is None:
        return None
    return hashlib.sha256(jcs({k: v for k, v in body.items() if k != "updated_at"})).hexdigest()


NAME_MAX = 60


def clean_name(text: Any) -> str:
    s = re.sub(r"[\x00-\x1f\x7f\u200b-\u200f\u202a-\u202e\u2066-\u2069]", "", str(text or "")).strip()
    return s[:NAME_MAX]


def verify_card(card: Any, x: str | None = None) -> dict:
    """验别人的名片：形状对、kid == 指纹(key)、签名头的 kid 一致、用 key 验签通过；给了 x（邀请码里的公钥）还要 key 就是它。
    通过就回 {kid, x, url, name, caps, a2a, card}，否则 ValueError。"""
    if not isinstance(card, dict):
        raise ValueError("card is not an object")
    key = card.get("key")
    if not isinstance(key, dict) or key.get("kty") != "OKP" or key.get("crv") != "Ed25519" or not isinstance(key.get("x"), str):
        raise ValueError("bad key")
    kx = key["x"]
    try:
        public_key(kx)
    except ValueError as e:
        raise ValueError("bad key") from e
    kid = thumbprint(kx)
    if card.get("kid") != kid:
        raise ValueError("kid does not match key")
    if x is not None and kx != x:
        raise ValueError("key does not match the invite")
    if card.get("openmousse") != "1":
        raise ValueError("unknown card version")
    if kid not in jws_kids(card) or not verify_jws(card, kx):
        raise ValueError("bad signature")
    url = origin_of(card.get("url") or "")
    if not url or not url_ok(url):
        raise ValueError("bad url")
    name = clean_name(card.get("name"))
    if not name:
        raise ValueError("no name")
    caps = card.get("caps")
    caps = [str(c)[:32] for c in caps[:20]] if isinstance(caps, list) else []
    a2a = card.get("a2a")
    a2a = a2a if isinstance(a2a, str) and a2a.startswith(url + "/") else None   # 只认同一个根地址下的 A2A 名片
    return {"kid": kid, "x": kx, "url": url, "name": name, "caps": caps, "a2a": a2a, "card": card}


async def fetch_card(origin: str, x: str | None = None) -> dict:
    """GET <根地址>/f/card 并验过（x：邀请码里的公钥）。取不到或不对都是 HTTPException(502 / 400)。"""
    if not url_ok(origin):
        raise HTTPException(400, "bad url")
    await check_host(origin)
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as c:
            async with c.stream("GET", origin + "/f/card", headers={"Accept": "application/json"}) as r:
                if r.status_code != 200:
                    raise HTTPException(502, f"card {r.status_code}")
                chunks, size = [], 0
                async for chunk in r.aiter_bytes():
                    size += len(chunk)
                    if size > FETCH_MAX:
                        raise HTTPException(502, "card too large")
                    chunks.append(chunk)
        data = json.loads(b"".join(chunks))
    except (httpx.HTTPError, ValueError) as e:
        raise HTTPException(502, "unreachable") from e
    try:
        info = verify_card(data, x)
    except ValueError as e:
        raise HTTPException(400, f"bad card: {e}") from e
    if info["url"] != origin:
        raise HTTPException(400, "bad card: url does not match")
    return info


# —— 请求签名（RFC 9421 的固定用法）——

def content_digest(body: bytes) -> str:
    return "sha-256=:" + base64.b64encode(hashlib.sha256(body).digest()).decode() + ":"


def _sf_string(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _sf_value(v: Any) -> str:
    if v is True:
        return ""
    if isinstance(v, int) and not isinstance(v, bool):
        return f"={v}"
    if isinstance(v, _Token):
        return f"={v.s}"
    if isinstance(v, bytes):
        return "=:" + base64.b64encode(v).decode() + ":"
    if v is False:
        return "=?0"
    return "=" + _sf_string(str(v))


def sig_params(components: list[str], params: list[tuple[str, Any]]) -> str:
    """@signature-params 的值（也是 Signature-Input 里 om= 后面那一段）。"""
    return "(" + " ".join(_sf_string(c) for c in components) + ")" + "".join(f";{k}{_sf_value(v)}" for k, v in params)


def sign_headers(method: str, path: str, body: bytes, to_kid: str) -> dict[str, str]:
    """给一个要发出去的请求算签名头。path 是对方看到的路径（根地址后面那段，比如 /f/msg）。"""
    digest = content_digest(body)
    params = sig_params(list(COMPONENTS), [("created", int(time.time())), ("nonce", b64u(secrets.token_bytes(16))),
                                           ("keyid", my_kid()), ("alg", "ed25519"), ("tag", SIG_TAG)])
    base = "\n".join([f'"@method": {method.upper()}', f'"@path": {path}', f'"content-digest": {digest}',
                      f'"mousse-to": {to_kid}', f'"@signature-params": {params}']).encode()
    sig = private_key().sign(base)
    return {"Content-Type": "application/json", "Content-Digest": digest, "Mousse-To": to_kid,
            "Signature-Input": f"{SIG_LABEL}={params}", "Signature": f"{SIG_LABEL}=:{base64.b64encode(sig).decode()}:"}


class _Token:
    """结构化字段里的 token（不带引号的那种），和字符串分开，重新拼 @signature-params 时原样写回。"""

    def __init__(self, s: str) -> None:
        self.s = s

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _Token) and other.s == self.s

    def __hash__(self) -> int:
        return hash(self.s)


class _SF:
    """RFC 8941 字典的一个小解析器：够解 Signature-Input / Signature / Content-Digest。"""
    KEY = re.compile(r"[a-z*][a-z0-9_\-.*]*")
    TOKEN = re.compile(r"[A-Za-z*][!#$%&'*+\-.^_`|~0-9A-Za-z:/]*")
    INT = re.compile(r"-?\d{1,15}")

    def __init__(self, s: str) -> None:
        self.s, self.i = s, 0

    def ws(self) -> None:
        while self.i < len(self.s) and self.s[self.i] in " \t":
            self.i += 1

    def peek(self) -> str:
        return self.s[self.i] if self.i < len(self.s) else ""

    def expect(self, ch: str) -> None:
        if self.peek() != ch:
            raise ValueError(f"expected {ch!r} at {self.i}")
        self.i += 1

    def key(self) -> str:
        m = self.KEY.match(self.s, self.i)
        if not m:
            raise ValueError(f"bad key at {self.i}")
        self.i = m.end()
        return m.group()

    def string(self) -> str:
        self.expect('"')
        out = []
        while True:
            ch = self.peek()
            if not ch:
                raise ValueError("unterminated string")
            self.i += 1
            if ch == "\\":
                nxt = self.peek()
                if nxt not in ('"', "\\"):
                    raise ValueError("bad escape")
                out.append(nxt)
                self.i += 1
            elif ch == '"':
                return "".join(out)
            elif not (" " <= ch <= "~"):
                raise ValueError("bad character in string")
            else:
                out.append(ch)

    def bare(self) -> Any:
        ch = self.peek()
        if ch == '"':
            return self.string()
        if ch == "-" or ch.isdigit():
            m = self.INT.match(self.s, self.i)
            if not m:
                raise ValueError("bad integer")
            self.i = m.end()
            return int(m.group())
        if ch == ":":
            end = self.s.find(":", self.i + 1)
            if end < 0:
                raise ValueError("bad byte sequence")
            val = base64.b64decode(self.s[self.i + 1:end], validate=True)
            self.i = end + 1
            return val
        if ch == "?":
            val = self.s[self.i:self.i + 2]
            if val not in ("?0", "?1"):
                raise ValueError("bad boolean")
            self.i += 2
            return val == "?1"
        m = self.TOKEN.match(self.s, self.i)
        if not m:
            raise ValueError(f"bad item at {self.i}")
        self.i = m.end()
        return _Token(m.group())

    def params(self) -> list[tuple[str, Any]]:
        out = []
        while self.peek() == ";":
            self.i += 1
            self.ws()
            k = self.key()
            v: Any = True
            if self.peek() == "=":
                self.i += 1
                v = self.bare()
            out.append((k, v))
        return out

    def member(self) -> tuple[Any, list[tuple[str, Any]]]:
        if self.peek() == "(":
            self.i += 1
            items = []
            while True:
                self.ws()
                if self.peek() == ")":
                    self.i += 1
                    break
                item = self.bare()
                if self.params():
                    raise ValueError("component parameters are not supported")
                items.append(item)
                if self.peek() not in (" ", ")"):
                    raise ValueError("bad inner list")
            return items, self.params()
        return self.bare(), self.params()

    def dictionary(self) -> dict[str, tuple[Any, list[tuple[str, Any]]]]:
        out: dict[str, tuple[Any, list[tuple[str, Any]]]] = {}
        self.ws()
        while self.i < len(self.s):
            k = self.key()
            if self.peek() == "=":
                self.i += 1
                out[k] = self.member()
            else:
                out[k] = (True, self.params())
            self.ws()
            if self.i >= len(self.s):
                break
            self.expect(",")
            self.ws()
            if self.i >= len(self.s):
                raise ValueError("trailing comma")
        return out


def parse_dict(value: str) -> dict[str, tuple[Any, list[tuple[str, Any]]]]:
    return _SF(value or "").dictionary()


@dataclass
class Peer:
    """进来的请求是谁。friend 只在对方是 active 的朋友时有；tier 就是他那一档，其余都是陌生。"""
    kid: str | None = None
    friend: dict | None = None
    tier: str = "stranger"
    signed: bool = False
    status: str | None = None   # 好友行的状态：None = 表里没有；active / removed / gone / blocked

    @property
    def blocked(self) -> bool:
        return self.status == "blocked"


def deny(code: int, error: str) -> HTTPException:
    return HTTPException(code, error)


async def read_body(request: Request, limit: int = BODY_MAX) -> bytes:
    """读 body，超过 limit 就 413（先看 Content-Length，再边读边数）。"""
    try:
        declared = int(request.headers.get("content-length") or 0)
    except ValueError:
        raise deny(400, "bad_request") from None
    if declared > limit:
        raise deny(413, "too_large")
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise deny(413, "too_large")
        chunks.append(chunk)
    return b"".join(chunks)


def _component(request: Request, name: str) -> str:
    """底稿里一个部件的值。只认我们会用到的几个派生部件，其余当请求头（多值用 ", " 连起来）。"""
    if name == "@method":
        return request.method.upper()
    if name == "@path":
        rawp = request.scope.get("raw_path")  # 原样的路径（没解码过的），没有再用解码后的
        return (rawp.decode("latin-1") if isinstance(rawp, bytes) else request.url.path) or "/"
    if name == "@query":
        return "?" + (request.url.query or "")
    if name == "@authority":
        return (request.headers.get("host") or "").lower()
    if name == "@scheme":
        return request.url.scheme.lower()
    if name == "@target-uri":
        return str(request.url)
    if name.startswith("@"):
        raise deny(401, "bad_signature")
    values = request.headers.getlist(name)
    if not values:
        raise deny(401, "bad_signature")
    return ", ".join(v.strip() for v in values)


def _remember_nonce(kid: str, nonce: str) -> bool:
    """这个 kid 最近 10 分钟用过这个 nonce 就是重放（回 False）；顺手清掉过期的。"""
    now = time.time()
    with _lock, sdb() as conn:
        conn.execute("DELETE FROM social_nonces WHERE at < ?", (now - NONCE_KEEP,))
        try:
            conn.execute("INSERT INTO social_nonces(kid, nonce, at) VALUES(?,?,?)", (kid, nonce, now))
        except sqlite3.IntegrityError:
            return False
    return True


def verify_signed(request: Request, body: bytes, key_x: str | None = None) -> Peer:
    """验一个已经读好 body 的请求。没签名 → Peer()（陌生人）。签了就必须全对，否则 401。
    key_x：用这把公钥验（/f/hello 里新朋友名片上的钥匙，要求 keyid 就是它的指纹）；不给就按 keyid 查好友表。"""
    si, sig = request.headers.get("signature-input"), request.headers.get("signature")
    if not si and not sig:
        return Peer()
    try:
        inputs, sigs = parse_dict(si or ""), parse_dict(sig or "")
    except (ValueError, TypeError):
        raise deny(401, "bad_signature") from None
    if SIG_LABEL not in inputs or SIG_LABEL not in sigs:
        raise deny(401, "bad_signature")
    components, params = inputs[SIG_LABEL]
    signature = sigs[SIG_LABEL][0]
    if not isinstance(components, list) or not all(isinstance(c, str) for c in components) or not isinstance(signature, bytes):
        raise deny(401, "bad_signature")
    p = dict(params)
    keyid, alg, created, nonce = p.get("keyid"), p.get("alg"), p.get("created"), p.get("nonce")
    if not set(COMPONENTS) <= set(components) or len(set(components)) != len(components):
        raise deny(401, "bad_signature")
    if not isinstance(keyid, str) or alg != "ed25519" or not isinstance(created, int) or isinstance(created, bool):
        raise deny(401, "bad_signature")
    if not isinstance(nonce, str) or not 16 <= len(nonce) <= 64:
        raise deny(401, "bad_signature")
    if abs(time.time() - created) > WINDOW:
        raise deny(401, "bad_signature")
    expires = p.get("expires")
    if isinstance(expires, int) and not isinstance(expires, bool) and time.time() > expires:
        raise deny(401, "bad_signature")
    try:
        digest = parse_dict(request.headers.get("content-digest") or "")
    except (ValueError, TypeError):
        raise deny(401, "bad_signature") from None
    sha = digest.get("sha-256", (None, []))[0]
    if not isinstance(sha, bytes) or not secrets.compare_digest(sha, hashlib.sha256(body).digest()):
        raise deny(401, "bad_signature")
    if request.headers.get("mousse-to") != my_kid():
        raise deny(401, "bad_signature")
    row = None
    if key_x is not None:
        if thumbprint(key_x) != keyid:
            raise deny(401, "bad_signature")
        x = key_x
    else:
        row = friend_by_kid(keyid)
        x = row["pub"] if row else None
    if x is None:
        return Peer()  # 不认识这把钥匙：签名验不了，keyid 也可能是乱写的，当没签名的陌生人
    lines = [f'"{c}": {_component(request, c)}' for c in components]
    lines.append(f'"@signature-params": {sig_params(components, params)}')
    try:
        public_key(x).verify(signature, "\n".join(lines).encode())
    except (InvalidSignature, ValueError):
        raise deny(401, "bad_signature") from None
    if not _remember_nonce(keyid, nonce):
        raise deny(401, "bad_signature")
    if row is None:
        return Peer(kid=keyid, signed=True)
    if row["status"] != "active":
        return Peer(kid=keyid, signed=True, status=row["status"])
    return Peer(kid=keyid, friend=row, tier=row["tier"] if row["tier"] in FRIEND_TIERS else "stranger", signed=True, status="active")


async def authenticate(request: Request, limit: int = BODY_MAX) -> tuple[bytes, Peer]:
    """读 body（有上限）并认人：见 verify_signed。第三层的 A2A 接口也用它。"""
    body = await read_body(request, limit)
    return body, verify_signed(request, body)


async def signed_post(url: str, payload: dict | bytes, *, to_kid: str, headers: dict | None = None, timeout: float = 15.0) -> httpx.Response:
    """往别的服务器发一个签过名的 POST（url 是完整地址，比如 <根地址>/f/msg）。不跟随跳转。"""
    body = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    await check_host(url)
    h = sign_headers("POST", urlsplit(url).path or "/", body, to_kid)
    h.update(headers or {})
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as c:
        return await c.post(url, content=body, headers=h)


def err(code: int, error: str, **extra: Any) -> JSONResponse:
    return JSONResponse({"ok": False, "error": error, **extra}, status_code=code, headers={"Cache-Control": "no-store"})


# —— 公开的 ——

@public_router.get("/f/card")
async def card_route():
    card = await asyncio.to_thread(my_card)
    if card is None:
        return err(404, "not_ready")
    return JSONResponse(card, headers={"Cache-Control": "public, max-age=300", "X-Robots-Tag": "noindex, nofollow"})


@public_router.get("/f/jwks.json")
async def jwks_route():
    ident = await asyncio.to_thread(identity)
    key = {**ident["jwk"], "kid": ident["kid"], "use": "sig", "alg": "EdDSA"}
    return JSONResponse({"keys": [key]}, headers={"Cache-Control": "public, max-age=3600", "X-Robots-Tag": "noindex, nofollow"})


def ts_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")
