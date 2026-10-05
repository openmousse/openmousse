"""朋友（2026-09-28，社交第二层）：邀请码、加好友、朋友聊天、分享发给朋友、分享的追问（名片 agent 代答）。
协议见 docs/social-protocol.zh-CN.md；身份、签名、表和档位在 social.py。

- 邀请码 = 一个网址 <我的根地址>/f/i/<令牌>/<我的公钥>：一次性，默认 7 天，只存令牌的 sha256。GET 那个网址是落地页（从不消耗令牌，
  聊天软件的链接预览会先打开一次）；对方的服务器带着自己的签名名片、签过名 POST /f/hello 兑换，两边各建一行 friends。
- 朋友聊天：一条消息一个签名的 POST /f/msg。发出去的先进 friend_messages（queued），投递循环按朋友排队发（同一个朋友按顺序，
  前一条没送到后面的等着），失败按 30 秒、2 分钟、10 分钟、1 小时、6 小时、之后每 6 小时重试，3 天放弃。收到的按 (朋友, id) 去重。
  edit / revoke 改我先前发的那条；card 是对方换了地址或名字（验签、kid 一样才认）；bye 是对方把我删了。
- 分享发给朋友：share.py 的一条分享（挡过私事的快照：挡住的地方是 ▇▇▇，原文不出服务器）作为 share 消息发过去；分享页「朋友能追问」开着、
  对方那一档 shares = ask 才 can_ask。只发给朋友、不开链接的分享 status = friends，/s/ 打不开。收回分享 → 给收到过的朋友各发一条 revoke。
- 分享的追问：朋友发来 ask → 这条分享还能追问就交给名片 agent（cardagent.answer，第三层；它不经 claw，没有工具），答的存成 answer
  （review pending），你在对话里「没问题 / 我来改 / 收回」；不能追问的回一句固定的「得问他本人」。没有 cardagent 时不自动答，只给你看。
- 朋友发来的算未读（unread.py 把它算进角标）。推送是新的推送类型：server.json social.push 里开了才推（{"message": "ring",
  "answered": "quiet", "friend": "quiet"}，不写 = 不推）。
"""
from __future__ import annotations

import asyncio
import hashlib
import html
import inspect
import json
import logging
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, quote, urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

import i18n
import qr
import share
import social
from chat import _lock, log_activity, now_iso
from config import settings
from i18n import L, LS

log = logging.getLogger("mousse.friends")
router = APIRouter()          # 给自己的：/api/friends…、/api/card、/api/shares/{sid}/send（要令牌）
public_router = APIRouter()   # 给别人的：/f/i/…、/f/hello、/f/msg（public.py 挂）

INVITE_DAYS, INVITE_MAX_DAYS, INVITE_OPEN_MAX = 7, 30, 20
HELLO_MAX = 16_000
TEXT_MAX, ASK_MAX, SHARE_TEXT_MAX, NOTE_MAX = 4000, 1000, 60_000, 500
PER_MINUTE, PER_DAY, HELLO_PER_HOUR = 30, 500, 30
RETRY = (30, 120, 600, 3600, 21600)   # 之后每 6 小时
GIVE_UP = 3 * 86400
MID_RE = re.compile(r"^[0-9a-f]{32}$")
FID_RE = re.compile(r"^fr-[0-9a-f]{8}$")
IID_RE = re.compile(r"^iv-[0-9a-f]{8}$")
CODE_RE = re.compile(r"(https?://[^\s/?#<>\"']+)/f/i/([A-Za-z0-9_-]{22})/([A-Za-z0-9_-]{43})")
SHOW = ("text", "share", "ask", "answer", "system")   # 在对话里显示的；edit / revoke / card / bye 是控制消息
CONTROL = ("edit", "revoke", "card", "bye")
NAME_OF_TIER = {"close": ("亲近", "Close"), "friend": ("朋友", "Friend"), "mate": ("同学", "Classmate"), "stranger": ("陌生", "Stranger")}


def tier_name(tier: str) -> str:
    zh, en = NAME_OF_TIER.get(tier, NAME_OF_TIER["stranger"])
    return L(zh, en)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_ts(value: str | None) -> datetime | None:
    try:
        d = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def db():
    return social.sdb()


def bad(code: int, zh: str, en: str) -> HTTPException:
    return HTTPException(code, L(zh, en))


# —— 准备好了没有 ——

def not_ready() -> str | None:
    """不能加朋友的原因：no_url（没有公网根地址，朋友打不回来）/ no_name（没设称呼），都好了是 None。"""
    if not social.my_url():
        return "no_url"
    if not social.my_name():
        return "no_name"
    return None


GENERIC_NAMES = {"ubuntu", "root", "admin", "administrator", "user", "debian", "pi", "ec2-user", "default", "openmousse"}
MAC_TAILSCALE = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"
_funnel: dict = {"url": None, "at": 0.0, "off": False}


def suggest_name() -> str:
    """还没设名字时给的建议：这台机器的用户全名（macOS 上一般是真名）；Linux 服务器上常是「Ubuntu」这种，就不建议。"""
    try:
        import pwd
        full = pwd.getpwuid(os.getuid()).pw_gecos.split(",")[0].strip()
    except (ImportError, KeyError, OSError):
        return ""
    return full if full and full.lower() not in GENERIC_NAMES and len(full) <= social.NAME_MAX else ""


def funnel_off() -> bool:
    """对外地址是 Tailscale 的 <机器>.ts.net，但 Funnel 没把 /f 开到公网：朋友的服务器连不进来（2026-10-05 第一个朋友就是这样，
    他发来的到了，回他的一直送不到）。看不出来（没有 tailscale 命令、读不了状态、用的是自己的域名）一律当开着。结果留 5 分钟。"""
    url = social.my_url()
    host = (urlsplit(url).hostname or "").lower() if url else ""
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


def public_fix() -> str:
    """Funnel 没开 /f 时，在服务器上要跑的那一句。"""
    port = share.cfg().get("public_port") or 8089
    return f"tailscale funnel --bg --set-path=/f http://127.0.0.1:{port}/f"


def need_ready() -> None:
    why = not_ready()
    if why == "no_url":
        raise bad(409, "先要有一个外面打得进来的地址（server.json 的 share.public_url）：朋友的服务器要能打回来。",
                  "You need an address people can reach first (share.public_url in server.json): your friends' servers have to reach you.")
    if why == "no_name":
        raise bad(409, "先设一个称呼（我 → 身份），朋友那边显示它。", "Set the name to call you first (Me → Identity); friends see it.")


# —— 邀请码 ——

def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def invite_state(r) -> str:
    if r["revoked_at"]:
        return "revoked"
    if r["used_at"]:
        return "used"
    exp = parse_ts(r["expires_at"])
    return "expired" if not exp or exp <= utcnow() else "open"


def invite_json(r) -> dict:
    used = social.friend_by_kid(r["used_by"]) if r["used_by"] else None
    return {"id": r["id"], "note": r["note"] or "", "tier": r["tier"], "createdAt": r["created_at"], "expiresAt": r["expires_at"],
            "status": invite_state(r), "usedAt": r["used_at"], "usedBy": {"id": used["id"], "name": used["name"]} if used else None}


def code_of(token: str) -> str:
    return f"{social.my_url()}/f/i/{token}/{social.identity()['x']}"


def parse_code(text: str) -> tuple[str, str, str]:
    """邀请码（或含邀请码的一段文字、openmousse:// 链接）→ (根地址, 令牌, 公钥 x)。认不出就 400。"""
    s = str(text or "")
    if "code=" in s and "/f/i/" not in s:
        s = (parse_qs(urlsplit(s.strip()).query).get("code") or [""])[0]
    m = CODE_RE.search(s)
    origin = social.origin_of(m.group(1)) if m else None
    if not m or not origin:
        raise bad(400, "这不像邀请码：应该是一个 …/f/i/… 的链接。", "That doesn't look like an invite: it should be a …/f/i/… link.")
    if not social.url_ok(origin):
        raise bad(400, "邀请码里的地址不是 https，不收。", "The invite's address isn't https.")
    return origin, m.group(2), m.group(3)


def create_invite(note: str, tier: str, days: int) -> dict:
    need_ready()
    if tier not in social.FRIEND_TIERS:
        raise bad(400, "档位只能是 close / friend / mate", "tier must be close, friend or mate")
    days = max(1, min(INVITE_MAX_DAYS, int(days or INVITE_DAYS)))
    with _lock, db() as conn:
        rows = conn.execute("SELECT * FROM friend_invites WHERE used_at IS NULL AND revoked_at IS NULL").fetchall()
        if sum(1 for r in rows if invite_state(r) == "open") >= INVITE_OPEN_MAX:
            raise bad(409, f"已经有 {INVITE_OPEN_MAX} 张没用过的邀请码了，先收回几张。",
                      f"You already have {INVITE_OPEN_MAX} unused invites; withdraw some first.")
        token = secrets.token_urlsafe(16)
        iid = f"iv-{uuid.uuid4().hex[:8]}"
        exp = (utcnow() + timedelta(days=days)).isoformat(timespec="seconds")
        conn.execute("INSERT INTO friend_invites(id, token_hash, note, tier, created_at, expires_at) VALUES(?,?,?,?,?,?)",
                     (iid, token_hash(token), " ".join(str(note or "").split())[:80] or None, tier, now_iso(), exp))
        r = conn.execute("SELECT * FROM friend_invites WHERE id=?", (iid,)).fetchone()
    code = code_of(token)
    rows = qr.matrix(code)
    return {**invite_json(r), "code": code, "qr": {"size": len(rows) + 8, "path": qr.path(rows)}}


def find_invite(token: str):
    with _lock, db() as conn:
        return conn.execute("SELECT * FROM friend_invites WHERE token_hash=?", (token_hash(token),)).fetchone()


# —— 好友行 ——

def new_fid(conn) -> str:
    while True:
        fid = f"fr-{uuid.uuid4().hex[:8]}"
        if not conn.execute("SELECT 1 FROM friends WHERE id=?", (fid,)).fetchone():
            return fid


def upsert_friend(info: dict, *, tier: str, via: str, note: str | None = None, alias: str | None = None) -> tuple[dict, bool]:
    """按对方的 kid 建或更新一行。返回 (朋友, 是不是刚成为朋友)：新建、或者以前删过 / 被删 / 拉黑过又重新加，都算刚成为朋友。"""
    ts = now_iso()
    card = json.dumps(info["card"], ensure_ascii=False)
    caps = json.dumps(info["caps"], ensure_ascii=False)
    alias = social.clean_name(alias) or None
    with _lock, db() as conn:
        r = conn.execute("SELECT * FROM friends WHERE kid=?", (info["kid"],)).fetchone()
        if r:
            fresh = r["status"] != "active"
            conn.execute("UPDATE friends SET pub=?, url=?, name=?, caps=?, card=?, a2a=?, updated_at=?, seen_at=? WHERE id=?",
                         (info["x"], info["url"], info["name"], caps, card, info["a2a"], ts, ts, r["id"]))
            if fresh:
                conn.execute("UPDATE friends SET status='active', tier=?, via=?, note=COALESCE(?, note), alias=COALESCE(?, alias) WHERE id=?",
                             (tier, via, note, alias, r["id"]))
            fid = r["id"]
        else:
            fresh = True
            fid = new_fid(conn)
            conn.execute("INSERT INTO friends(id, kid, pub, url, name, alias, tier, status, caps, card, a2a, note, via, created_at, updated_at, seen_at) "
                         "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (fid, info["kid"], info["x"], info["url"], info["name"], alias, tier, "active", caps, card, info["a2a"], note, via, ts, ts, ts))
    f = social.friend(fid)
    assert f is not None
    return f, fresh


def system_line(fid: str, text: str) -> None:
    """对话里一行灰字（只在本机，不发出去）。"""
    with _lock, db() as conn:
        conn.execute("INSERT INTO friend_messages(friend, mid, dir, kind, text, status, ts) VALUES(?,?,?,?,?,?,?)",
                     (fid, uuid.uuid4().hex, "out", "system", text, "local", now_iso()))


def became_friends(f: dict, how: str) -> None:
    system_line(f["id"], LS("你们成了朋友", "You're now friends"))
    log_activity(LS(f"和 {f['name']} 成了朋友（{how}）", f"Became friends with {f['name']} ({how})"), "social")


def friend_or_404(fid: str) -> dict:
    f = social.friend(fid) if FID_RE.match(fid or "") else None
    if not f:
        raise bad(404, "找不到这个朋友", "Friend not found")
    return f


# —— 消息 ——

_loop: asyncio.AbstractEventLoop | None = None   # 主服务的事件循环：投递循环在它上面跑（公网小服务在另一个线程）
_wake: asyncio.Event | None = None


def kick() -> None:
    """叫醒投递循环（哪个线程调都行）。"""
    if _loop is not None and _wake is not None:
        _loop.call_soon_threadsafe(_wake.set)


def insert_out(fid: str, kind: str, text: str = "", *, data: dict | None = None, reply_to: str | None = None, by: str = "person",
               review: str | None = None) -> int:
    """写一条要发出去的消息（queued），叫醒投递循环。返回本机的行 id。"""
    with _lock, db() as conn:
        cur = conn.execute("INSERT INTO friend_messages(friend, mid, dir, kind, by, text, data, reply_to, status, review, ts) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                           (fid, uuid.uuid4().hex, "out", kind, by, text, json.dumps(data, ensure_ascii=False) if data else None, reply_to,
                            "queued", review, now_iso()))
        rid = cur.lastrowid
    kick()
    assert rid is not None
    return rid


def row_data(r) -> dict:
    try:
        d = json.loads(r["data"] or "{}")
    except ValueError:
        d = {}
    return d if isinstance(d, dict) else {}


def wire(r) -> dict:
    """本机的一行 → 发出去的消息。"""
    d = row_data(r)
    out: dict = {"v": 1, "id": r["mid"], "kind": r["kind"], "at": r["ts"]}
    k = r["kind"]
    if k == "text":
        out["text"] = r["text"]
        if r["reply_to"]:
            out["reply_to"] = r["reply_to"]
    elif k == "share":
        out["share"] = d.get("share") or {}
        if r["text"]:
            out["text"] = r["text"]
    elif k == "ask":
        out.update(about=r["reply_to"], text=r["text"])
    elif k == "answer":
        out.update(about=r["reply_to"], text=r["text"], used=d.get("used") or [], defer=bool(d.get("defer")),
                   by="person" if r["by"] == "person" else "agent")
    elif k == "edit":
        out.update(target=d.get("target"), text=d.get("text") or "", by=d.get("by") or "person")
    elif k == "revoke":
        out["target"] = d.get("target")
    elif k == "card":
        out["card"] = social.my_card()
    return out


def msg_json(r) -> dict:
    d = row_data(r)
    out = {"id": r["id"], "mid": r["mid"], "dir": r["dir"], "kind": r["kind"], "by": r["by"], "text": r["text"], "replyTo": r["reply_to"],
           "status": r["status"], "review": r["review"], "ts": r["ts"], "edited": bool(r["edited_at"])}
    if r["kind"] == "share":
        out["share"] = d.get("share") or {}
    if r["kind"] in ("ask", "answer"):
        out["about"] = r["reply_to"]
    if r["kind"] == "answer":
        out["used"] = d.get("used") or []
        out["usedLabel"] = d.get("usedLabel") or ""
        out["defer"] = bool(d.get("defer"))
        if d.get("outcome"):
            out["outcome"] = d["outcome"]
        if r["dir"] == "out" and isinstance(d.get("sentinel"), dict):  # Doorman 的结论（只给你看）
            out["sentinel"] = {"verdict": str(d["sentinel"].get("verdict") or ""), "reasons": d["sentinel"].get("reasons") or [],
                               "via": str(d["sentinel"].get("via") or "")}
    if r["dir"] == "out" and r["status"] == "failed":
        out["error"] = r["error"]
    elif r["dir"] == "out" and r["status"] == "queued" and r["tries"]:  # 试过没送到、还在按间隔重试：app 不再只写「发送中…」
        out["error"] = r["error"]
        out["nextTry"] = r["next_try"]
    return out


def message_row(rid: int):
    with _lock, db() as conn:
        r = conn.execute("SELECT * FROM friend_messages WHERE id=?", (rid,)).fetchone()
    if not r:
        raise bad(404, "找不到这条消息", "Message not found")
    return r


# —— 投递 ——

def _next_try(tries: int) -> str:
    wait = RETRY[tries - 1] if tries - 1 < len(RETRY) else RETRY[-1]
    return (utcnow() + timedelta(seconds=wait)).isoformat(timespec="seconds")


def _set(rid: int, **cols) -> None:
    with _lock, db() as conn:
        conn.execute(f"UPDATE friend_messages SET {', '.join(f'{k}=?' for k in cols)} WHERE id=?", (*cols.values(), rid))  # noqa: S608


async def send_one(r) -> bool:
    """发一条；送到了回 True。没送到的按次数排下一次（或放弃），回 False（同一个朋友后面的先等着）。"""
    f = social.friend(r["friend"])
    if f is None:
        _set(r["id"], status="failed", error="no friend")
        return True
    if f["status"] in ("removed", "gone", "blocked") and r["kind"] != "bye":
        _set(r["id"], status="failed", error="not_friends")
        return True
    payload = wire(r)
    if r["kind"] == "card" and payload.get("card") is None:
        _set(r["id"], status="failed", error="no card")
        return True
    tries = r["tries"] + 1
    try:
        resp = await social.signed_post(f["url"] + "/f/msg", payload, to_kid=f["kid"])
        code, err = resp.status_code, None
        try:
            err = (resp.json() or {}).get("error")
        except ValueError:
            err = None
    except (httpx.HTTPError, HTTPException, OSError) as e:
        code, err = 0, type(e).__name__
    if code == 200:
        _set(r["id"], status="sent", tries=tries, next_try=None, error=None)
        return True
    first = parse_ts(r["ts"]) or utcnow()
    if code in (0, 429) or code >= 500:
        if (utcnow() - first).total_seconds() > GIVE_UP:
            _set(r["id"], status="failed", tries=tries, next_try=None, error=f"{code or 'network'} {err or ''}".strip())
            return True
        _set(r["id"], tries=tries, next_try=_next_try(tries), error=f"{code or 'network'} {err or ''}".strip())
        return False
    _set(r["id"], status="failed", tries=tries, next_try=None, error=f"{code} {err or ''}".strip())
    if code == 403 and err == "not_friends" and f["status"] == "active":
        with _lock, db() as conn:
            conn.execute("UPDATE friends SET status='gone', updated_at=? WHERE id=?", (now_iso(), f["id"]))
    return True


def announce_card() -> None:
    """名片变了（地址、名字、能力）：给每个朋友排一条 card。第一次只记下现在的样子。"""
    digest = social.card_digest()
    if not digest:
        return
    before = social.get_setting("announced")
    if before == digest:
        return
    social.set_setting("announced", digest)
    if before is None:
        return
    for f in social.friends():
        insert_out(f["id"], "card")


async def deliver_due() -> None:
    await asyncio.to_thread(announce_card)
    with _lock, db() as conn:
        fids = [r["friend"] for r in conn.execute("SELECT DISTINCT friend FROM friend_messages WHERE dir='out' AND status='queued'")]
    now = utcnow()
    for fid in fids:
        while True:
            with _lock, db() as conn:
                r = conn.execute("SELECT * FROM friend_messages WHERE friend=? AND dir='out' AND status='queued' ORDER BY id LIMIT 1", (fid,)).fetchone()
            if not r:
                break
            due = parse_ts(r["next_try"])
            if due and due > now:
                break
            if not await send_one(r):
                break


async def outbox_loop() -> None:
    assert _wake is not None
    while True:
        try:
            await deliver_due()
        except Exception:  # noqa: BLE001 — 投递循环不能停
            log.exception("friends outbox")
        try:
            await asyncio.wait_for(_wake.wait(), timeout=15)
        except TimeoutError:
            pass
        _wake.clear()


def start() -> None:
    """主服务启动时调（main.py lifespan）：在主事件循环上起投递循环。"""
    global _loop, _wake
    _loop = asyncio.get_running_loop()
    _wake = asyncio.Event()
    _loop.create_task(outbox_loop())


_tasks: set = set()


def run_on_main(coro) -> None:
    """把一件后台的事交给主事件循环（公网小服务的请求在另一个线程里，不在那边跑模型）。没有主循环（测试）就在当前循环跑。"""
    if _loop is not None:
        asyncio.run_coroutine_threadsafe(coro, _loop)
    else:
        t = asyncio.get_running_loop().create_task(coro)
        _tasks.add(t)
        t.add_done_callback(_tasks.discard)


# —— 推送 ——

def push_level(kind: str) -> str:
    """server.json social.push：{"message": "ring", "answered": "quiet", "friend": "quiet", "agents": "quiet"}；不写 = 不推（新的推送类型要你点头才开）。
    agents（你的名片 agent 问过的事、对方本人定了）没写就跟着 answered（朋友问了你的名片 agent）那一档（2026-09-28 定）。"""
    p = social.cfg().get("push")
    lv = p.get(kind) if isinstance(p, dict) else None
    if lv is None and kind == "agents" and isinstance(p, dict):
        lv = p.get("answered")
    return lv if lv in ("ring", "quiet") else "none"


async def notify(f: dict, kind: str, body: str) -> None:
    level = push_level(kind)
    if level == "none":
        return
    try:
        import push
        await push.send_push(f["name"], body, {"thread": "today", "target": {"type": "friend", "id": f["id"]}}, thread_id=f"friend:{f['id']}",
                             level=level, collapse=f"friend:{f['id']}", kind="friend")
    except Exception:  # noqa: BLE001 — 推不出去不影响收消息
        log.exception("friends push")


# —— 限流 ——

_rate_lock = threading.Lock()
_minute: dict[str, deque] = {}
_day: dict[str, deque] = {}
_hello: deque = deque()


def rate_ok(kid: str) -> bool:
    now = time.time()
    with _rate_lock:
        m = _minute.setdefault(kid, deque())
        d = _day.setdefault(kid, deque())
        while m and m[0] < now - 60:
            m.popleft()
        while d and d[0] < now - 86400:
            d.popleft()
        if len(m) >= PER_MINUTE or len(d) >= PER_DAY:
            return False
        m.append(now)
        d.append(now)
        return True


def hello_ok() -> bool:
    now = time.time()
    with _rate_lock:
        while _hello and _hello[0] < now - 3600:
            _hello.popleft()
        if len(_hello) >= HELLO_PER_HOUR:
            return False
        _hello.append(now)
        return True


# —— 公开：落地页、兑换、收消息 ——

INVITE_CSS = """.code{font:14px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;background:var(--q);border-radius:12px;padding:12px 14px;word-break:break-all;user-select:all}
.btn{display:inline-block;margin:8px 0 18px;padding:12px 18px;border-radius:12px;background:#D9AE62;color:#2A1E06;font-weight:700;text-decoration:none}
ol{padding-left:1.3em}li{margin:.3em 0}.fp{font:15px ui-monospace,SFMono-Regular,Menlo,monospace;letter-spacing:.08em}
.qr{display:flex;gap:16px;align-items:center;margin:8px 0 16px;color:var(--ink3);font-size:14px}.qr svg{flex-shrink:0;border-radius:8px}"""


@public_router.get("/f/i/{token}/{x}")
async def invite_page(token: str, x: str, request: Request):
    """邀请落地页：只看，不消耗令牌。按浏览器的语言显示。"""
    lang = i18n.use(request.headers.get("accept-language"))
    try:
        me = social.identity()
        r = await asyncio.to_thread(find_invite, token) if re.fullmatch(r"[A-Za-z0-9_-]{22}", token) else None
        name = social.my_name()
        if not r or x != me["x"] or invite_state(r) != "open" or not social.my_url() or not name:
            t = L("这个邀请码用过了或者过期了", "This invite has been used or has expired")
            s = L("请对方再发一个新的。", "Ask for a new one.")
            return share.page(t, f'<div class="gone"><h1>{html.escape(t)}</h1><p>{html.escape(s)}</p></div>', status=404)
        code = code_of(token)
        deep = "openmousse://friends/add?code=" + quote(code, safe="")
        title = L(f"{name} 邀请你加他为朋友", f"{name} invited you to be friends")
        steps = L("<ol><li>打开 OpenMousse</li><li>对话 → 朋友 → 加朋友</li><li>粘贴下面这个链接</li></ol>",
                  "<ol><li>Open OpenMousse</li><li>Chat → Friends → Add a friend</li><li>Paste the link below</li></ol>")
        fp = L("核对指纹", "Fingerprint")
        scan = L("在电脑上看到的？用手机相机扫这个码，在手机上打开。", "On a computer? Scan this with your phone's camera to open it there.")
        body = (f"<p class=by>{html.escape(settings.app_name)}</p><h1>{html.escape(title)}</h1>"
                f'<a class="btn" href="{html.escape(deep)}">{html.escape(L("在 app 里打开", "Open in the app"))}</a>'
                f"{steps}<p class=code>{html.escape(code)}</p>"
                f'<div class="qr">{qr.svg(code, 200, L("邀请码二维码", "Invite QR code"))}<p>{html.escape(scan)}</p></div>'
                f"<p>{html.escape(fp)}：<span class=fp>{html.escape(me['fingerprint'])}</span></p>"
                f"<footer>{html.escape(L('这个链接只能用一次，用过就失效。', 'This link works once.'))}</footer>")
        return share.page(title, body, head=f"<style>{INVITE_CSS}</style>")
    finally:
        i18n.reset(lang)


def _err(e: HTTPException):
    return social.err(e.status_code, str(e.detail))


@public_router.post("/f/hello")
async def hello(request: Request):
    """兑换邀请码：{"v": 1, "token", "card"}，请求要用 card 上的钥匙签。"""
    try:
        if not hello_ok():
            return social.err(429, "slow_down")
        body = await social.read_body(request, HELLO_MAX)
        try:
            data = json.loads(body)
            token, card = str(data["token"]), data["card"]
        except (ValueError, KeyError, TypeError):
            return social.err(400, "bad_request")
        try:
            info = social.verify_card(card)
        except ValueError:
            return social.err(400, "bad_card")
        peer = social.verify_signed(request, body, key_x=info["x"])
        if not peer.signed or peer.kid != info["kid"]:
            return social.err(401, "bad_signature")
        mine = await asyncio.to_thread(social.my_card)
        if mine is None or info["kid"] == mine["kid"] or not re.fullmatch(r"[A-Za-z0-9_-]{22}", token):
            return social.err(404, "invite_invalid")
        inv = await asyncio.to_thread(find_invite, token)
        if not inv or inv["revoked_at"] or (inv["used_at"] and inv["used_by"] != info["kid"]) or \
                (not inv["used_at"] and invite_state(inv) != "open"):
            return social.err(404, "invite_invalid")
        f, fresh = await asyncio.to_thread(upsert_friend, info, tier=inv["tier"], via=f"invite:{inv['id']}", note=inv["note"])
        if not inv["used_at"]:
            with _lock, db() as conn:
                conn.execute("UPDATE friend_invites SET used_at=?, used_by=? WHERE id=? AND used_at IS NULL", (now_iso(), info["kid"], inv["id"]))
        if fresh:
            await asyncio.to_thread(became_friends, f, LS(f"{f['name']} 用了你的邀请码", f"{f['name']} used your invite"))
            run_on_main(notify(f, "friend", LS(f"{f['name']} 用了你的邀请码，你们成了朋友", f"{f['name']} used your invite; you're now friends")))
        # 试一下能不能连回对方（之后的消息都要从这边送过去）：连不上就告诉对方，多半是对方的 Funnel 没开（老版本对方不认这个字段）
        try:
            await asyncio.wait_for(social.fetch_card(info["url"], info["x"]), 6)
            reach = True
        except (HTTPException, httpx.HTTPError, OSError, asyncio.TimeoutError, ValueError):
            reach = False
        return {"ok": True, "card": mine, "reach": reach}
    except HTTPException as e:
        return _err(e)


def _str(v, cap: int, *, empty: bool = False) -> str:
    if not isinstance(v, str):
        raise ValueError("not a string")
    v = v.replace("\r\n", "\n").strip()
    if len(v) > cap or (not v and not empty):
        raise ValueError("bad length")
    return v


def _mid(v) -> str:
    if not isinstance(v, str) or not MID_RE.match(v):
        raise ValueError("bad id")
    return v


def clean_share(s) -> dict:
    """朋友发来的分享快照：只留认得的字段、压长度。"""
    if not isinstance(s, dict):
        raise ValueError("share is not an object")
    link = s.get("link")
    link = link if isinstance(link, str) and link.startswith(("https://", "http://")) and len(link) <= 300 else None
    return {"sid": _str(s.get("sid") or "", 40, empty=True), "kind": _str(s.get("kind") or "", 16, empty=True),
            "title": _str(s.get("title") or "", 200, empty=True), "text": _str(s.get("text") or "", SHARE_TEXT_MAX, empty=True),
            "quote": _str(s.get("quote") or "", 500, empty=True), "when": _str(s.get("when") or "", 40, empty=True),
            "link": link, "can_ask": s.get("can_ask") is True}


def store_in(fid: str, mid: str, kind: str, text: str, *, at: str, data: dict | None = None, reply_to: str | None = None,
             by: str = "person", status: str = "new") -> int | None:
    """存一条收到的；重复的（同一个朋友、同一个 id）回 None。"""
    ts = at if parse_ts(at) else now_iso()
    with _lock, db() as conn:
        try:
            cur = conn.execute("INSERT INTO friend_messages(friend, mid, dir, kind, by, text, data, reply_to, status, ts, recv_at) "
                               "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                               (fid, mid, "in", kind, by, text, json.dumps(data, ensure_ascii=False) if data else None, reply_to, status, ts, now_iso()))
        except sqlite3.IntegrityError:  # 同一个朋友、同一个 id：重复的
            return None
        return cur.lastrowid


def apply_control(f: dict, m: dict) -> bool:
    """edit / revoke：改对方先前发给我的那条。找不到就忽略（回 False）。"""
    target = _mid(m.get("target"))
    with _lock, db() as conn:
        r = conn.execute("SELECT * FROM friend_messages WHERE friend=? AND dir='in' AND mid=?", (f["id"], target)).fetchone()
        if not r or r["kind"] not in ("text", "share", "answer"):
            return False
        if m["kind"] == "revoke":
            d = row_data(r)
            if r["kind"] == "share":
                d["share"] = {**(d.get("share") or {}), "text": "", "quote": "", "title": ""}
            conn.execute("UPDATE friend_messages SET status='revoked', text='', data=?, edited_at=? WHERE id=?",
                         (json.dumps(d, ensure_ascii=False), now_iso(), r["id"]))
            return True
        text = _str(m.get("text"), TEXT_MAX)
        by = "person" if m.get("by") == "person" else r["by"]
        conn.execute("UPDATE friend_messages SET text=?, by=?, edited_at=? WHERE id=?", (text, by, now_iso(), r["id"]))
        return True


@public_router.post("/f/msg")
async def receive(request: Request):
    """朋友的服务器投一条消息（签过名）。"""
    try:
        body, peer = await social.authenticate(request)
    except HTTPException as e:
        return _err(e)
    f = peer.friend
    if f is None:
        if peer.status in ("removed", "gone"):
            return social.err(403, "not_friends")
        if peer.blocked:
            return {"ok": True}
        return social.err(401, "unknown_sender")
    if not rate_ok(peer.kid or ""):
        return social.err(429, "slow_down", retry_after=60)
    if '"ok": false' in (social.get_setting("reach") or ""):  # 朋友的服务器送进来了：之前「连不到你」的结论作废
        social.set_setting("reach", json.dumps({"ok": True, "at": now_iso(), "by": f["name"]}, ensure_ascii=False))
    try:
        m = json.loads(body)
        if not isinstance(m, dict) or m.get("v") != 1:
            raise ValueError("bad version")
        mid, kind, at = _mid(m.get("id")), m.get("kind"), str(m.get("at") or "")
        if kind == "text":
            text = _str(m.get("text"), TEXT_MAX)
            reply_to = m.get("reply_to") if isinstance(m.get("reply_to"), str) and MID_RE.match(m["reply_to"]) else None
            rid = store_in(f["id"], mid, "text", text, at=at, reply_to=reply_to)
        elif kind == "share":
            sh = clean_share(m.get("share"))
            note = _str(m.get("text") or "", TEXT_MAX, empty=True)
            rid = store_in(f["id"], mid, "share", note, at=at, data={"share": sh})
        elif kind == "ask":
            about, text = _mid(m.get("about")), _str(m.get("text"), ASK_MAX)
            rid = store_in(f["id"], mid, "ask", text, at=at, reply_to=about)
            if rid:
                run_on_main(answer_ask(f["id"], rid))
        elif kind == "answer":
            about, text = _mid(m.get("about")), _str(m.get("text"), TEXT_MAX)
            used = [str(u)[:60] for u in (m.get("used") or [])[:5]] if isinstance(m.get("used"), list) else []
            rid = store_in(f["id"], mid, "answer", text, at=at, reply_to=about, by="person" if m.get("by") == "person" else "agent",
                           data={"used": used, "defer": m.get("defer") is True})
        elif kind in ("edit", "revoke"):
            target = _mid(m.get("target"))
            if kind == "edit":
                _str(m.get("text"), TEXT_MAX)
            if store_in(f["id"], mid, kind, "", at=at, data={"target": target}, status="applied") is None:
                return {"ok": True, "id": mid, "dup": True}
            done = await asyncio.to_thread(apply_control, f, m)
            return {"ok": True, "id": mid, "dup": False, **({} if done else {"ignored": True})}
        elif kind == "card":
            try:
                info = social.verify_card(m.get("card"), f["pub"])
            except ValueError:
                return social.err(400, "bad_card")
            if store_in(f["id"], mid, "card", "", at=at, status="applied") is None:
                return {"ok": True, "id": mid, "dup": True}
            with _lock, db() as conn:
                conn.execute("UPDATE friends SET url=?, name=?, caps=?, card=?, a2a=?, updated_at=? WHERE id=?",
                             (info["url"], info["name"], json.dumps(info["caps"]), json.dumps(info["card"], ensure_ascii=False), info["a2a"], now_iso(), f["id"]))
            return {"ok": True, "id": mid, "dup": False}
        elif kind == "bye":
            if store_in(f["id"], mid, "bye", "", at=at, status="applied") is None:
                return {"ok": True, "id": mid, "dup": True}
            with _lock, db() as conn:
                conn.execute("UPDATE friends SET status='gone', updated_at=? WHERE id=?", (now_iso(), f["id"]))
            system_line(f["id"], LS(f"{f['name']} 不再是朋友了", f"{f['name']} is no longer your friend"))
            log_activity(LS(f"{f['name']} 把你从朋友里删了", f"{f['name']} removed you as a friend"), "social", actor=f["name"])
            return {"ok": True, "id": mid, "dup": False}
        else:
            return social.err(400, "bad_request", why="unknown kind")
    except (ValueError, TypeError, KeyError):
        return social.err(400, "bad_request")
    if rid is None:
        return {"ok": True, "id": mid, "dup": True}
    with _lock, db() as conn:
        conn.execute("UPDATE friends SET seen_at=? WHERE id=?", (now_iso(), f["id"]))
    if kind in ("text", "share", "answer"):
        preview = {"text": m.get("text") or "", "share": LS("分享了：", "Shared: ") + ((m.get("share") or {}).get("title") or ""),
                   "answer": LS("名片 agent 答了你：", "Their card agent answered: ") + (m.get("text") or "")}[kind]
        run_on_main(notify(f, "message", preview))
    return {"ok": True, "id": mid, "dup": False}


# —— 分享的追问：交给名片 agent ——

def cardagent_mod():
    try:
        import cardagent  # 第三层；没有就不自动答
    except ImportError:
        return None
    return cardagent


def agent_available() -> bool:
    ca = cardagent_mod()
    if ca is None:
        return False
    fn = getattr(ca, "available", None)
    try:
        return bool(fn()) if callable(fn) else True
    except Exception:  # noqa: BLE001
        return False


def history_of(fid: str, before_id: int, n: int = 10) -> list[dict]:
    """这段聊天最近几轮（给名片 agent 当资料）：收回的、改掉的原文不给。"""
    with _lock, db() as conn:
        rows = conn.execute("SELECT * FROM friend_messages WHERE friend=? AND id<? AND kind IN ('text','ask','answer','share') "
                            "AND status!='revoked' ORDER BY id DESC LIMIT ?", (fid, before_id, n)).fetchall()
    out = []
    for r in reversed(rows):
        if r["kind"] == "share":
            text = LS("[分享] ", "[shared] ") + ((row_data(r).get("share") or {}).get("title") or "")
        else:
            text = r["text"]
        who = "friend" if r["dir"] == "in" else ("agent" if r["by"] == "agent" and r["review"] not in ("edited",) else "owner")
        out.append({"from": who, "kind": r["kind"], "text": text, "at": r["ts"]})
    return out


async def answer_ask(fid: str, ask_id: int) -> None:
    """朋友对着我发的分享追问：能追问就交给名片 agent；不能就回一句固定的「得问他本人」；没有名片 agent 就不答（用户自己看着回）。"""
    try:
        ca = cardagent_mod()
        if ca is None or not agent_available():
            return
        f = social.friend(fid)
        ask = message_row(ask_id)
        if not f or f["status"] != "active":
            return
        with _lock, db() as conn:
            sm = conn.execute("SELECT * FROM friend_messages WHERE friend=? AND dir='out' AND kind='share' AND mid=?", (fid, ask["reply_to"])).fetchone()
        snap = (row_data(sm).get("share") or {}) if sm else {}
        live = False
        if sm and sm["status"] != "revoked" and snap.get("sid"):
            try:
                live = share.row(snap["sid"])["status"] in ("live", "friends")
            except HTTPException:
                live = False
        can = live and snap.get("can_ask") is True and social.tier_scopes(f["tier"])["shares"] == "ask"
        if not can:
            me = social.my_name()
            text = LS(f"这个得问{me}本人。", f"You'd have to ask {me} directly.")
            insert_out(fid, "answer", text, data={"used": [], "defer": True, "template": True}, reply_to=ask["mid"], by="agent", review="ok")
            log_activity(LS(f"名片 agent 回了 {f['name']}：{text}", f"Card agent replied to {f['name']}: {text}"), "social",
                         actor=LS("名片 agent", "Card agent"))
            return
        material = [{"id": f"share:{snap['sid']}", "kind": "share", "title": snap.get("title") or "", "text": snap.get("text") or ""}]
        res = ca.answer(f, ask["text"], channel="chat", material=material, history=history_of(fid, ask_id), ref=f"share:{snap['sid']}")
        if inspect.isawaitable(res):
            res = await res
        res = res if isinstance(res, dict) else {}
        text = str(res.get("text") or "").strip()[:TEXT_MAX]
        if not text:
            return
        names = res.get("usedNames") if isinstance(res.get("usedNames"), list) else res.get("used")  # 给人看的名字（「这期节目」），不是资料 id
        defer = res.get("defer") if isinstance(res.get("defer"), dict) else None
        data = {"used": [str(u)[:60] for u in (names or [])][:5], "usedLabel": str(res.get("usedLabel") or "")[:80],
                "defer": bool(res.get("defer")), "log_id": res.get("log_id"), "declined": bool(res.get("declined")),
                **({"sentinel": res["sentinel"]} if isinstance(res.get("sentinel"), dict) else {}),
                "limited": bool(res.get("limited")), **({"inbox_id": defer.get("inbox_id")} if defer and defer.get("inbox_id") else {})}
        insert_out(fid, "answer", text, data=data, reply_to=ask["mid"], by="agent", review="pending")
        await notify(f, "answered", LS(f"{f['name']} 问了你的名片 agent，它答了", f"{f['name']} asked your card agent; it answered"))
    except Exception:  # noqa: BLE001 — 答不了就不答，用户在对话里看得到这条追问
        log.exception("friends answer_ask")


async def deliver_chat(ask: dict, text: str, data: dict) -> bool:
    """名片 agent 出的收件箱卡（要用户表态的）点完以后，第三层把结果交回来：作为一条 by=agent 的 answer 发给那个朋友。
    ask = cardagent 的 card_asks 一行（peer = 好友 id、ref = share:<sid>、inbox_id）；回的是哪条追问：先按那张卡找当初那条代答，
    找不到就用这个朋友关于这条分享最近的一条追问。朋友不在了、分享收回了 → False（卡上会写「没能告诉对方」）。"""
    fid = str(ask.get("peer") or "")
    f = social.friend(fid) if FID_RE.match(fid) else None
    text = str(text or "").strip()[:TEXT_MAX]
    if not f or f["status"] != "active" or not text:
        return False
    sid = str(ask.get("ref") or "").removeprefix("share:")
    about = None
    with _lock, db() as conn:
        rows = conn.execute("SELECT * FROM friend_messages WHERE friend=? AND dir='out' AND kind='answer' ORDER BY id DESC LIMIT 200", (fid,)).fetchall()
        for r in rows:
            if ask.get("inbox_id") and row_data(r).get("inbox_id") == ask["inbox_id"]:
                about = r["reply_to"]
                break
        shares = {r["mid"]: row_data(r).get("share") or {} for r in conn.execute(
            "SELECT mid, data FROM friend_messages WHERE friend=? AND dir='out' AND kind='share' AND status!='revoked'", (fid,))}
        if about is None:
            for r in conn.execute("SELECT * FROM friend_messages WHERE friend=? AND dir='in' AND kind='ask' ORDER BY id DESC LIMIT 50", (fid,)):
                if (shares.get(r["reply_to"]) or {}).get("sid") == sid:
                    about = r["mid"]
                    break
        if about is not None:
            ask_row = conn.execute("SELECT reply_to FROM friend_messages WHERE friend=? AND dir='in' AND kind='ask' AND mid=?", (fid, about)).fetchone()
            if not ask_row or ask_row["reply_to"] not in shares:
                about = None   # 那条分享收回了
    if about is None:
        return False
    owner = (data or {}).get("by") == "owner"  # 你在卡上自己写的（Doorman 扣下后「改一下」）：算你说的
    insert_out(fid, "answer", text, data={"used": [str(u)[:60] for u in ((data or {}).get("usedNames") or [])][:5] if not owner else [],
                                         "usedLabel": str((data or {}).get("label") or "")[:80], "defer": False,
                                         "outcome": str((data or {}).get("outcome") or "")[:20],
                                         **({"inbox_id": ask["inbox_id"]} if ask.get("inbox_id") else {})},
               reply_to=about, by="person" if owner else "agent", review="edited" if owner else "ok")
    return True


def register_delivery() -> None:
    ca = cardagent_mod()
    reg = getattr(ca, "DELIVER", None) if ca else None
    if isinstance(reg, dict):
        reg["chat"] = deliver_chat


register_delivery()
# 装了名片 agent（有 cardagent 模块）就在名片上写 "agent"：朋友那边显示「有 agent」。按装没装算、不按此刻能不能调模型，
# 免得模型一时不通就改名片、给所有朋友发一遍 card。
social.CARD_HOOKS.append(lambda: {"caps": ["agent"]} if cardagent_mod() is not None else {})


# —— 给 app 的：朋友、邀请码 ——

def unread_counts() -> dict[str, dict]:
    """{朋友 id: {n, last: {text, ts, kind}}}：朋友发来、还没看的（unread.py 用）。"""
    with _lock, db() as conn:
        rows = conn.execute("SELECT friend, COUNT(*) n, MAX(id) last FROM friend_messages WHERE dir='in' AND status='new' "
                            "AND kind IN ('text','share','ask','answer') GROUP BY friend").fetchall()
        out = {}
        for r in rows:
            m = conn.execute("SELECT text, ts, kind, data FROM friend_messages WHERE id=?", (r["last"],)).fetchone()
            out[r["friend"]] = {"n": r["n"], "last": {"text": preview_of(m), "ts": m["ts"], "kind": m["kind"]} if m else None}
    return out


def preview_of(r) -> str:
    if r is None:
        return ""
    if r["kind"] == "share":
        return L("分享：", "Shared: ") + ((row_data(r).get("share") or {}).get("title") or "")
    return " ".join((r["text"] or "").split())[:80]


def friend_json(f: dict, unread: dict | None = None, last=None) -> dict:
    return {"id": f["id"], "name": f["name"], "cardName": f["card_name"], "alias": f.get("alias"), "tier": f["tier"], "tierName": tier_name(f["tier"]),
            "status": f["status"], "caps": f["caps"], "agent": "agent" in f["caps"] or "a2a" in f["caps"],
            "fingerprint": social.fingerprint(f["pub"]), "url": f["url"], "note": f.get("note"), "createdAt": f["created_at"],
            "unread": (unread or {}).get("n", 0),
            "last": {"text": preview_of(last), "ts": last["ts"], "kind": last["kind"], "dir": last["dir"], "by": last["by"]} if last else None}


def friends_payload() -> dict:
    ids = social.identity()
    unread = unread_counts()
    with _lock, db() as conn:
        frows = conn.execute("SELECT * FROM friends WHERE status IN ('active','gone','blocked') ORDER BY created_at").fetchall()
        lasts = {}
        for r in frows:
            lasts[r["id"]] = conn.execute("SELECT * FROM friend_messages WHERE friend=? AND kind IN ('text','share','ask','answer','system') "
                                          "ORDER BY id DESC LIMIT 1", (r["id"],)).fetchone()
        inv = conn.execute("SELECT * FROM friend_invites ORDER BY created_at DESC LIMIT 50").fetchall()
    fl = [friend_json(social.friend_dict(r), unread.get(r["id"]), lasts.get(r["id"])) for r in frows]  # type: ignore[arg-type]
    fl.sort(key=lambda x: (x["last"] or {}).get("ts") or x["createdAt"], reverse=True)
    why = not_ready()
    reach = social.get_setting("reach")  # 最近一次朋友的服务器回来说「连不到你」（hello 时它试过）
    try:
        reach = json.loads(reach) if reach else None
    except ValueError:
        reach = None
    return {"ok": True, "ready": why is None, "why": why,
            "me": {"name": social.my_name(), "fingerprint": ids["fingerprint"], "url": social.my_url(),
                   "suggest": "" if social.my_name() else suggest_name()},
            # 有对外地址，但外面连不进来：Funnel 没开 /f，或者朋友的服务器试过、连不上（publicFix = 在服务器上要跑的那一句）
            "unreachable": (why is None and (funnel_off() or bool(reach and reach.get("ok") is False))),
            "publicFix": public_fix(),
            "agent": agent_available(), "friends": fl,
            "invites": [invite_json(r) for r in inv if invite_state(r) == "open"]}


@router.get("/api/friends")
async def list_friends():
    return await asyncio.to_thread(friends_payload)


class InviteIn(BaseModel):
    note: str | None = None
    tier: str = "friend"
    days: int = INVITE_DAYS


@router.post("/api/friends/invites")
async def new_invite(body: InviteIn):
    inv = await asyncio.to_thread(create_invite, body.note or "", body.tier, body.days)
    return {"ok": True, "invite": inv}


@router.delete("/api/friends/invites/{iid}")
async def withdraw_invite(iid: str):
    if not IID_RE.match(iid):
        raise bad(404, "找不到这张邀请码", "Invite not found")
    with _lock, db() as conn:
        conn.execute("UPDATE friend_invites SET revoked_at=? WHERE id=? AND used_at IS NULL AND revoked_at IS NULL", (now_iso(), iid))
    return {"ok": True}


class CodeIn(BaseModel):
    code: str


@router.post("/api/friends/preview")
async def preview(body: CodeIn):
    """贴进来的邀请码 → 对方是谁（取对方名片、用邀请码里的公钥核对）。"""
    need_ready()
    origin, _token, x = parse_code(body.code)
    if x == social.identity()["x"]:
        raise bad(400, "这是你自己的邀请码", "That's your own invite")
    try:
        info = await social.fetch_card(origin, x)
    except HTTPException as e:
        if e.status_code == 400:
            raise bad(400, "对方的名片和邀请码对不上，不加。", "Their card doesn't match the invite.") from e
        raise bad(502, "连不上对方的服务器。可能是对方的公网访问（Tailscale Funnel）还没开，或者服务器没在运行；请对方检查后再试。",
                  "Couldn't reach their server. Their public access (Tailscale Funnel) may not be on yet, or the server isn't running; ask them to check, then try again.") from e
    old = social.friend_by_kid(info["kid"])
    return {"ok": True, "name": info["name"], "fingerprint": social.fingerprint(info["x"]), "url": info["url"],
            "agent": "agent" in info["caps"] or "a2a" in info["caps"],
            "already": old["id"] if old and old["status"] == "active" else None}


class AcceptIn(BaseModel):
    code: str
    tier: str = "friend"
    alias: str | None = None


@router.post("/api/friends/accept")
async def accept(body: AcceptIn):
    """兑换对方的邀请码：带着自己的签名名片 POST 到对方的 /f/hello，核对回来的名片，建好友。"""
    need_ready()
    if body.tier not in social.FRIEND_TIERS:
        raise bad(400, "档位只能是 close / friend / mate", "tier must be close, friend or mate")
    origin, token, x = parse_code(body.code)
    if x == social.identity()["x"]:
        raise bad(400, "这是你自己的邀请码", "That's your own invite")
    mine = await asyncio.to_thread(social.my_card)
    try:
        r = await social.signed_post(origin + "/f/hello", {"v": 1, "token": token, "card": mine}, to_kid=social.thumbprint(x))
    except (httpx.HTTPError, HTTPException, OSError) as e:
        raise bad(502, "连不上对方的服务器，过会儿再试。", "Couldn't reach their server. Try again later.") from e
    if r.status_code == 404:
        raise bad(410, "这个邀请码用过了或者过期了，请对方再发一个。", "This invite has been used or has expired. Ask for a new one.")
    if r.status_code == 429:
        raise bad(429, "对方那边现在太忙，过会儿再试。", "Their server is busy. Try again later.")
    if r.status_code != 200:
        raise bad(502, f"对方没接受（{r.status_code}）", f"They didn't accept it ({r.status_code})")
    try:
        info = social.verify_card((r.json() or {}).get("card"), x)
        if info["url"] != origin:
            raise ValueError("url")
    except (ValueError, AttributeError) as e:
        raise bad(502, "对方回来的名片和邀请码对不上，没加。", "Their reply doesn't match the invite; not added.") from e
    f, fresh = await asyncio.to_thread(upsert_friend, info, tier=body.tier, via="code", alias=body.alias)
    if fresh:
        await asyncio.to_thread(became_friends, f, LS(f"你用了 {f['name']} 的邀请码", f"you used {f['name']}'s invite"))
    reach = (r.json() or {}).get("reach")  # 对方试着连回这边的结果（老版本没有 = 不知道）
    if isinstance(reach, bool):
        social.set_setting("reach", json.dumps({"ok": reach, "at": now_iso(), "by": f["name"]}, ensure_ascii=False))
        _funnel["at"] = 0.0  # Funnel 的状态也重新看
    return {"ok": True, "friend": friend_json(f), "unreachable": reach is False, "publicFix": public_fix()}


class FriendPatch(BaseModel):
    alias: str | None = None
    tier: str | None = None


@router.patch("/api/friends/{fid}")
async def patch_friend(fid: str, body: FriendPatch):
    f = friend_or_404(fid)
    sets: dict = {}
    if body.alias is not None:
        sets["alias"] = social.clean_name(body.alias) or None
    if body.tier is not None:
        if body.tier not in social.FRIEND_TIERS:
            raise bad(400, "档位只能是 close / friend / mate", "tier must be close, friend or mate")
        sets["tier"] = body.tier
    if sets:
        sets["updated_at"] = now_iso()
        with _lock, db() as conn:
            conn.execute(f"UPDATE friends SET {', '.join(f'{k}=?' for k in sets)} WHERE id=?", (*sets.values(), fid))  # noqa: S608
        if "tier" in sets and sets["tier"] != f["tier"]:
            log_activity(L(f"把 {f['name']} 放进了「{tier_name(sets['tier'])}」", f"Moved {f['name']} to {tier_name(sets['tier'])}"), "social")
    return {"ok": True, "friend": friend_json(friend_or_404(fid))}


@router.delete("/api/friends/{fid}")
async def remove_friend(fid: str):
    """删朋友：告诉对方一声（bye），这边标成 removed；聊天记录留在本机。"""
    f = friend_or_404(fid)
    if f["status"] in ("active", "blocked"):
        with _lock, db() as conn:
            conn.execute("UPDATE friends SET status='removed', updated_at=? WHERE id=?", (now_iso(), fid))
        insert_out(fid, "bye")
        log_activity(L(f"删了朋友 {f['name']}", f"Removed {f['name']} as a friend"), "social")
    return {"ok": True}


class BlockIn(BaseModel):
    blocked: bool


@router.post("/api/friends/{fid}/block")
async def block_friend(fid: str, body: BlockIn):
    """拉黑：对方发来的一律收下不存、不提示（他那边看起来送到了）；解开回到朋友。"""
    f = friend_or_404(fid)
    new = "blocked" if body.blocked else "active"
    if f["status"] in ("active", "blocked") and f["status"] != new:
        with _lock, db() as conn:
            conn.execute("UPDATE friends SET status=?, updated_at=? WHERE id=?", (new, now_iso(), fid))
        log_activity(L(f"{'拉黑了' if body.blocked else '解开了'} {f['name']}", f"{'Blocked' if body.blocked else 'Unblocked'} {f['name']}"), "social")
    return {"ok": True, "friend": friend_json(friend_or_404(fid))}


# —— 给 app 的：聊天 ——

@router.get("/api/friends/{fid}/messages")
async def messages(fid: str, before: int | None = None, after: int | None = None, limit: int = 60):
    f = friend_or_404(fid)
    limit = max(1, min(200, limit))
    q, args = "SELECT * FROM friend_messages WHERE friend=? AND kind IN ('text','share','ask','answer','system')", [fid]
    if after is not None:
        q += " AND id>?"
        args.append(after)
    if before is not None:
        q += " AND id<?"
        args.append(before)
    q += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    with _lock, db() as conn:
        rows = conn.execute(q, args).fetchall()  # noqa: S608 — 条件是上面写死的
        changed = []
        if after is not None:  # 轮询时顺带拿回已经显示过、后来被改过 / 收回 / 送达状态变了的
            changed = conn.execute("SELECT * FROM friend_messages WHERE friend=? AND id<=? AND kind IN ('text','share','ask','answer') "
                                   "ORDER BY id DESC LIMIT 60", (fid, after)).fetchall()
    return {"ok": True, "friend": friend_json(f), "messages": [msg_json(r) for r in reversed(rows)],
            "recent": [msg_json(r) for r in reversed(changed)], "agent": agent_available(),
            "canAsk": social.tier_scopes(f["tier"])["shares"] == "ask"}


class SendIn(BaseModel):
    text: str
    replyTo: str | None = None


def need_active(f: dict) -> None:
    if f["status"] != "active":
        raise bad(409, "你们现在不是朋友，发不了。", "You aren't friends now; can't send.")


@router.post("/api/friends/{fid}/messages")
async def send_text(fid: str, body: SendIn):
    f = friend_or_404(fid)
    need_active(f)
    text = body.text.replace("\r\n", "\n").strip()
    if not text:
        raise bad(400, "没有内容", "Nothing to send")
    if len(text) > TEXT_MAX:
        raise bad(400, f"太长了（最多 {TEXT_MAX} 字）", f"Too long (max {TEXT_MAX} characters)")
    reply = body.replyTo if body.replyTo and MID_RE.match(body.replyTo) else None
    rid = await asyncio.to_thread(insert_out, fid, "text", text, reply_to=reply)
    return {"ok": True, "message": msg_json(message_row(rid))}


class AskIn(BaseModel):
    about: str
    text: str


@router.post("/api/friends/{fid}/ask")
async def ask(fid: str, body: AskIn):
    """对着朋友发来的一条分享追问他的名片 agent。"""
    f = friend_or_404(fid)
    need_active(f)
    text = body.text.replace("\r\n", "\n").strip()
    if not text or len(text) > ASK_MAX:
        raise bad(400, f"问题要在 1–{ASK_MAX} 字之间", f"A question is 1–{ASK_MAX} characters")
    with _lock, db() as conn:
        sm = conn.execute("SELECT * FROM friend_messages WHERE friend=? AND dir='in' AND kind='share' AND mid=?", (fid, body.about)).fetchone()
    if not sm or sm["status"] == "revoked":
        raise bad(404, "找不到这条分享（可能收回了）", "That share is gone (maybe withdrawn)")
    if not (row_data(sm).get("share") or {}).get("can_ask"):
        raise bad(409, "这条分享不能追问", "This share doesn't take questions")
    rid = await asyncio.to_thread(insert_out, fid, "ask", text, reply_to=body.about)
    return {"ok": True, "message": msg_json(message_row(rid))}


class ReadIn(BaseModel):
    upto: int | None = None


@router.post("/api/friends/{fid}/read")
async def mark_read(fid: str, body: ReadIn):
    friend_or_404(fid)
    with _lock, db() as conn:
        if body.upto is None:
            conn.execute("UPDATE friend_messages SET status='read' WHERE friend=? AND dir='in' AND status='new'", (fid,))
        else:
            conn.execute("UPDATE friend_messages SET status='read' WHERE friend=? AND dir='in' AND status='new' AND id<=?", (fid, body.upto))
    return {"ok": True}


class ReviewIn(BaseModel):
    action: str          # ok | edit | revoke
    text: str | None = None


def retract(d: dict, replaced: bool) -> None:
    ca = cardagent_mod()
    fn = getattr(ca, "retract", None) if ca else None
    if callable(fn) and d.get("log_id"):
        try:
            fn(d["log_id"], replaced=replaced)
        except Exception:  # noqa: BLE001
            log.exception("cardagent.retract")


@router.post("/api/friends/messages/{rid}/review")
async def review(rid: int, body: ReviewIn):
    """名片 agent 替你答的那条：没问题 / 我来改（替换它那条，对方看到「改过」）/ 收回（对方那边清空）。"""
    r = message_row(rid)
    if r["dir"] != "out" or r["kind"] != "answer":
        raise bad(400, "这条不是名片 agent 的代答", "That isn't a card agent answer")
    if r["status"] == "revoked":
        raise bad(409, "这条已经收回了", "Already withdrawn")
    d = row_data(r)
    f = friend_or_404(r["friend"])
    if body.action == "ok":
        _set(rid, review="ok")
    elif body.action == "edit":
        text = (body.text or "").replace("\r\n", "\n").strip()
        if not text or len(text) > TEXT_MAX:
            raise bad(400, f"改后的内容要在 1–{TEXT_MAX} 字之间", f"The new text must be 1–{TEXT_MAX} characters")
        _set(rid, text=text, by="person", review="edited", edited_at=now_iso())
        insert_out(r["friend"], "edit", data={"target": r["mid"], "text": text, "by": "person"})
        retract(d, replaced=True)
        log_activity(L(f"改了名片 agent 给 {f['name']} 的一条代答", f"Rewrote a card agent answer to {f['name']}"), "social")
    elif body.action == "revoke":
        _set(rid, status="revoked", review="revoked", edited_at=now_iso())
        insert_out(r["friend"], "revoke", data={"target": r["mid"]})
        retract(d, replaced=False)
        log_activity(L(f"收回了名片 agent 给 {f['name']} 的一条代答", f"Withdrew a card agent answer to {f['name']}"), "social")
    else:
        raise bad(400, "action 只能是 ok / edit / revoke", "action must be ok, edit or revoke")
    return {"ok": True, "message": msg_json(message_row(rid))}


@router.post("/api/friends/messages/{rid}/revoke")
async def revoke_message(rid: int):
    """收回我发的一条（文字或分享）：对方那边清空，显示「收回了这条」。"""
    r = message_row(rid)
    if r["dir"] != "out" or r["kind"] not in ("text", "share"):
        raise bad(400, "只能收回你自己发的话或分享", "You can only withdraw your own messages or shares")
    if r["status"] != "revoked":
        if r["status"] == "queued":  # 还没发出去：直接不发了
            _set(rid, status="revoked")
        else:
            _set(rid, status="revoked", edited_at=now_iso())
            insert_out(r["friend"], "revoke", data={"target": r["mid"]})
    return {"ok": True, "message": msg_json(message_row(rid))}


@router.post("/api/friends/messages/{rid}/retry")
async def retry(rid: int):
    r = message_row(rid)
    if r["dir"] == "out" and r["status"] == "queued" and r["tries"]:  # 还在等下一次重试：现在就试（次数、时间照旧算）
        _set(rid, next_try=None)
        kick()
        await asyncio.sleep(1.5)  # 多半一两秒就有结果：回去的就是这次试过的样子
        return {"ok": True, "message": msg_json(message_row(rid))}
    if r["dir"] != "out" or r["status"] != "failed":
        raise bad(400, "这条不用重发", "Nothing to retry")
    _set(rid, status="queued", tries=0, next_try=None, error=None, ts=now_iso())
    kick()
    return {"ok": True, "message": msg_json(message_row(rid))}


# —— 分享发给朋友 ——

class ShareSendIn(BaseModel):
    friends: list[str]
    ask: bool = True        # 朋友能追问（还要看对方那一档 shares 是不是 ask）
    link: bool = False      # 有链接的人都能看（开 /s/ 链接）；否则只发给这些朋友
    text: str | None = None # 附一句话


def share_snapshot(r) -> dict:
    masks = json.loads(r["masks"] or "[]")
    rel = set(json.loads(r["released"] or "[]"))
    return {"sid": r["id"], "kind": r["kind"], "title": share.mask_title(r), "text": share.apply_masks(r["body"] or "", masks, rel),
            "quote": share.quote_of(r), "when": share.local_day(r["published_at"] or r["created_at"]), "link": share.link_of(r)}


@router.post("/api/shares/{sid}/send")
async def send_share(sid: str, body: ShareSendIn):
    r = share.row(sid)
    if r["status"] == "revoked":
        raise bad(409, "这条已经收回了", "This share was withdrawn")
    targets = []
    for fid in dict.fromkeys(body.friends):
        f = friend_or_404(fid)
        need_active(f)
        targets.append(f)
    if not targets:
        raise bad(400, "先选发给谁", "Pick who to send it to")
    ts = now_iso()
    with _lock, share.sdb() as conn:
        if body.link and r["status"] in ("draft", "friends"):
            conn.execute("UPDATE shares SET status='live', published_at=COALESCE(published_at, ?), updated_at=? WHERE id=?", (ts, ts, sid))
        elif r["status"] == "draft":
            conn.execute("UPDATE shares SET status='friends', published_at=?, updated_at=? WHERE id=?", (ts, ts, sid))
    r = share.row(sid)
    snap = share_snapshot(r)
    note = " ".join((body.text or "").split())[:NOTE_MAX]
    for f in targets:
        can = body.ask and social.tier_scopes(f["tier"])["shares"] == "ask"
        insert_out(f["id"], "share", note, data={"share": {**snap, "can_ask": can}})
    names = "、".join(f["name"] for f in targets) if L("zh", "en") == "zh" else ", ".join(f["name"] for f in targets)
    log_activity(L(f"把分享「{snap['title']}」发给了 {names}", f"Sent the share \"{snap['title']}\" to {names}"), "share")
    return {"ok": True, "share": share.share_json(r, full=True), "sent": len(targets)}


def sent_to(sid: str) -> list[dict]:
    """这条分享发给过谁（share_json 的 sentTo）。"""
    with _lock, db() as conn:
        rows = conn.execute("SELECT friend, data, status FROM friend_messages WHERE dir='out' AND kind='share' ORDER BY id").fetchall()
    out = {}
    for r in rows:
        if (row_data(r).get("share") or {}).get("sid") == sid and r["status"] != "revoked":
            f = social.friend(r["friend"])
            if f:
                out[f["id"]] = {"id": f["id"], "name": f["name"]}
    return list(out.values())


def on_share_revoked(sid: str) -> None:
    """分享收回了：发出去的每一份也收回（对方那边清空）。"""
    with _lock, db() as conn:
        rows = conn.execute("SELECT * FROM friend_messages WHERE dir='out' AND kind='share' AND status!='revoked'").fetchall()
    for r in rows:
        if (row_data(r).get("share") or {}).get("sid") != sid:
            continue
        if r["status"] == "queued":
            _set(r["id"], status="revoked")
        else:
            _set(r["id"], status="revoked", edited_at=now_iso())
            insert_out(r["friend"], "revoke", data={"target": r["mid"]})


share.REVOKE_HOOKS.append(on_share_revoked)
share.JSON_HOOKS.append(lambda r: {"sentTo": sent_to(r["id"])})


# —— 我的名片 agent（档位、近况）——

@router.get("/api/card")
async def get_card():
    t = await asyncio.to_thread(social.tiers)
    people: dict[str, list[dict]] = {k: [] for k in social.FRIEND_TIERS}
    for f in await asyncio.to_thread(social.friends):
        people.setdefault(f["tier"], []).append({"id": f["id"], "name": f["name"]})
    return {"ok": True, "tiers": t, "scopes": {k: list(v) for k, v in social.SCOPES.items()}, "status": social.card_status(),
            "people": people, "agent": agent_available(), "tierNames": {k: tier_name(k) for k in social.TIERS},
            "name": social.my_name(), "suggest": "" if social.my_name() else suggest_name()}


class CardPatch(BaseModel):
    tiers: dict[str, dict[str, str]] | None = None
    status: str | None = None
    name: str | None = None  # 朋友看到的名字（2026-10-05）


def set_my_name(raw_name: str) -> None:
    """朋友看到的名字：就是 server.json 的 user_name（也是给模型的称呼），和 settings_ctl.py user-name 一样写进档案。
    名片跟着变，投递循环给每个朋友发一条新名片。"""
    import settings_ctl
    name = " ".join(raw_name.split())
    if not name:
        raise bad(400, "名字不能为空", "The name can't be empty")
    if len(name) > settings_ctl.MAX_NAME:
        raise bad(400, f"名字最多 {settings_ctl.MAX_NAME} 个字", f"A name is at most {settings_ctl.MAX_NAME} characters")
    data = settings_ctl.load()
    if data is None:
        raise bad(500, "server.json 读不了（不是合法的 JSON？），没改。", "Can't read server.json (not valid JSON?); nothing changed.")
    if str(data.get("user_name") or "").strip() == name:
        return
    data["user_name"] = name
    settings_ctl.save(data)
    try:
        settings_ctl.write_profile(name)
    except OSError:
        pass  # 档案写不了不影响名字本身
    kick()


@router.patch("/api/card")
async def patch_card(body: CardPatch):
    if body.name is not None:
        await asyncio.to_thread(set_my_name, body.name)
    if body.tiers:
        await asyncio.to_thread(social.set_tier_scopes, body.tiers)
        log_activity(L("改了名片 agent 的档位", "Changed the card agent's tiers"), "social")
    if body.status is not None:
        text = body.status.replace("\r\n", "\n").strip()[:1000]
        await asyncio.to_thread(social.set_setting, "status", text or None)
    return await get_card()
