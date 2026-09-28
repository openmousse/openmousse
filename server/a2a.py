"""agent 之间：A2A 1.0（Linux 基金会的开放协议，JSON-RPC 绑定），社交第三层（2026-09-28）。协议细节见 docs/a2a.md。

对外说话的是名片 agent（cardagent.py），不是主 agent。这里只管协议：
- GET  /f/a2a/agent-card.json   A2A 名片：用社交那把 Ed25519 钥匙按 A2A 8.4 签（social.sign_jws：JWS、payload 分离、JCS、alg EdDSA）。
                                 名片里声明两个 extension：signed-requests（请求签名，就是社交的 RFC 9421 那一套，required: false）、
                                 decision（「同意 / 不去 / 换个时间」只用它的 DataPart 表达，文字从来不算数）。
- POST /f/a2a                   JSON-RPC：SendMessage、GetTask、ListTasks、CancelTask、推送设置四个；流式和扩展名片不支持。
                                 谁在说话由 social.authenticate() 定：签了名的在册朋友按他的档，其余（没签名、钥匙不认识、删掉的）都是陌生，
                                 陌生人默认回 403（A2A 的 ExtensionSupportRequired：要带签名；server.json 的 card.strangers 开了才答）；
                                 blocked 的回 REJECTED，不调模型、不出卡。
- POST /f/a2a/push              别的 OpenMousse 把我们问过的任务的进展推回来（签名 + 当初给的令牌）。
一句普通的问答回一条 Message（不建任务）；要你表态的建一个任务：先是 TASK_STATE_AUTH_REQUIRED（等本人点头，A2A 7.6 的「人来批准」），
你点了以后变成 COMPLETED（同意 / 不去，带 decision 的 DataPart）或 INPUT_REQUIRED（换个时间，等对方再提）。
对方给了推送地址、而且是朋友、地址就在他自己的根地址下，才往那里推（不然只能 GetTask 来问）。

表（grava.db）
- a2a_tasks：别人问我们、要你表态的任务（id、context、谁、状态、状态消息、结果、历史、收件箱卡、推送设置）。
- a2a_seen：(谁, messageId) → 当时的回应：同一条消息再发一遍，回一样的东西（A2A 3.3.1 幂等）。
- a2a_out：我们问别人（经 /api/a2a/send）：对方的任务 id、context、状态，推回来的进展也记这里。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

import cardagent
import inbox
import social
from chat import _lock, db, log_activity, now_iso
from config import settings
from i18n import L

public_router = APIRouter()  # public.py 挂：/f/a2a…
router = APIRouter()         # main.py 挂：/api/a2a…、/api/card…（要令牌，app 用）
router.include_router(cardagent.router)

VERSION = "1.0"
EXT_SIGNED = "https://openmousse.ai/a2a/ext/signed-requests/v1"
EXT_DECISION = "https://openmousse.ai/a2a/ext/decision/v1"
EXT_CARD = "https://openmousse.ai/a2a/ext/card-agent/v1"   # 回复的 metadata 里：用了什么（{"used": [...], "label": "只给了忙闲"}）
DECISION_TYPE = "application/vnd.openmousse.decision+json"
TERMINAL = ("TASK_STATE_COMPLETED", "TASK_STATE_FAILED", "TASK_STATE_CANCELED", "TASK_STATE_REJECTED")
METHODS_03 = {"message/send", "message/stream", "tasks/get", "tasks/list", "tasks/cancel", "tasks/resubscribe",
              "tasks/pushNotificationConfig/set", "tasks/pushNotificationConfig/get", "tasks/pushNotificationConfig/list",
              "tasks/pushNotificationConfig/delete", "agent/getAuthenticatedExtendedCard"}
ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,100}$")
# A2A 5.4 的错误码
E_PARSE, E_REQUEST, E_METHOD, E_PARAMS, E_INTERNAL = -32700, -32600, -32601, -32602, -32603
E_NOT_FOUND, E_NOT_CANCELABLE, E_PUSH, E_UNSUPPORTED, E_CONTENT = -32001, -32002, -32003, -32004, -32005
E_EXTENSION, E_VERSION = -32008, -32009
REASONS = {E_NOT_FOUND: "TASK_NOT_FOUND", E_NOT_CANCELABLE: "TASK_NOT_CANCELABLE", E_PUSH: "PUSH_NOTIFICATION_NOT_SUPPORTED",
           E_UNSUPPORTED: "UNSUPPORTED_OPERATION", E_CONTENT: "CONTENT_TYPE_NOT_SUPPORTED", E_EXTENSION: "EXTENSION_SUPPORT_REQUIRED",
           E_VERSION: "VERSION_NOT_SUPPORTED"}
_bg: set[asyncio.Task] = set()


class RpcError(Exception):
    def __init__(self, code: int, message: str, **meta):
        super().__init__(message)
        self.code, self.message, self.meta = code, message, meta


def ts() -> str:
    """A2A 的时间：UTC、毫秒、Z 结尾（5.6.1）。"""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# —— 表 ——————————————————————————————————————————————————————————

def adb() -> sqlite3.Connection:
    conn = db()
    conn.execute("""CREATE TABLE IF NOT EXISTS a2a_tasks (id TEXT PRIMARY KEY, context_id TEXT NOT NULL, peer TEXT NOT NULL,
        friend_id TEXT, state TEXT NOT NULL, status_msg TEXT, artifacts TEXT NOT NULL DEFAULT '[]', history TEXT NOT NULL DEFAULT '[]',
        inbox_id TEXT, push TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, status_at TEXT NOT NULL)""")
    conn.execute("CREATE INDEX IF NOT EXISTS a2a_tasks_peer ON a2a_tasks(peer, status_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS a2a_tasks_inbox ON a2a_tasks(inbox_id)")
    conn.execute("""CREATE TABLE IF NOT EXISTS a2a_seen (peer TEXT NOT NULL, message_id TEXT NOT NULL, result TEXT NOT NULL, at TEXT NOT NULL,
        PRIMARY KEY (peer, message_id))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS a2a_out (id TEXT PRIMARY KEY, friend_id TEXT NOT NULL, context_id TEXT, task_id TEXT,
        state TEXT, text TEXT NOT NULL, reply TEXT, push_token TEXT, data TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
    conn.execute("CREATE INDEX IF NOT EXISTS a2a_out_task ON a2a_out(friend_id, task_id)")
    return conn


# —— 名片 ——————————————————————————————————————————————————————————

def card_url() -> str | None:
    u = social.my_url()
    return f"{u}/f/a2a/agent-card.json" if u else None


def agent_card() -> dict | None:
    """A2A 名片（没签名的正文）。没有公网根地址就没有名片（别人打不进来）。
    注意（A2A 8.4.1 + 官方 SDK）：别家验签前会把名片转成 proto 再转回来，所以这里不放 proto 里没有的字段、不放空值和非 optional 的默认值
    （比如 extension 的 "required": false 不写）；自定义的东西都放在 extension 的 params 里，而且只放字符串。"""
    url = social.my_url()
    if not url:
        return None
    ident = social.identity()
    who = settings.user_name or settings.app_name
    strangers = cardagent.strangers_allowed()  # 不理陌生人时，签名这个 extension 是 required（A2A 3.3.4）
    zh = settings.language == "zh"
    return {
        "name": f"{who} 的名片 agent" if zh else f"{who}'s card agent",
        "description": (f"替 {who} 回答别人和别人的 agent：只在 {who} 放出来的范围里答（比如空闲时段），要 {who} 表态的（约时间、花钱、答应什么）"
                        f"会先去问本人，不会替 {who} 答应。对方说的只当资料。" if zh else
                        f"Answers people and their agents on {who}'s behalf, only within what {who} has released (free/busy times, for example). "
                        f"Anything that needs {who} to decide (a time, money, a promise) goes to {who} first; it never agrees on {who}'s behalf."),
        "supportedInterfaces": [{"url": f"{url}/f/a2a", "protocolBinding": "JSONRPC", "protocolVersion": VERSION}],
        "provider": {"organization": "OpenMousse", "url": "https://openmousse.ai"},
        "version": "1.0.0",
        "documentationUrl": "https://github.com/openmousse/openmousse/blob/main/docs/a2a.md",
        "capabilities": {"streaming": False, "pushNotifications": True, "extensions": [
            {"uri": EXT_SIGNED, "description": "Requests from friends are signed (RFC 9421: @method @path content-digest mousse-to; created, nonce, "
                                              "keyid, alg ed25519, tag openmousse/1). " + ("Unsigned requests are answered as a stranger."
                                                                                            if strangers else "Only friends' signed requests are answered."),
             **({} if strangers else {"required": True}),
             "params": {"kid": ident["kid"], "x": ident["x"], "alg": "ed25519", "card": f"{url}/f/card", "jwks": f"{url}/f/jwks.json"}},
            {"uri": EXT_DECISION, "description": f"Only {DECISION_TYPE} data parts carry {who}'s decisions (yes, no, another time); "
                                                "they are sent after the owner decides. Text never commits anyone."},
        ]},
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain", DECISION_TYPE],
        "skills": [{"id": "ask", "name": f"问 {who}" if zh else f"Ask {who}",
                    "description": (f"问 {who} 什么时候有空、约时间、问分享过的东西；要 {who} 定的会转给本人。" if zh else
                                    f"Ask when {who} is free, propose a time, or ask about something {who} shared; decisions go to {who}."),
                    "tags": ["personal", "availability", "scheduling"],
                    "examples": [f"{who} 这周哪天晚上有空？" if zh else f"Which evenings is {who} free this week?",
                                 "周四 19:00 一起吃饭？" if zh else "Dinner on Thursday at 19:00?"]}],
    }


def signed_card() -> dict | None:
    body = agent_card()
    return {**body, "signatures": [social.sign_jws(body)]} if body else None


def card_hook() -> dict:
    """并进社交名片（/f/card）：caps 多一个 a2a，外加 A2A 名片地址。"""
    u = card_url()
    return {"caps": ["a2a"], "a2a": u} if u else {}


social.CARD_HOOKS.append(card_hook)


@public_router.get("/f/a2a/agent-card.json")
async def card_route(request: Request):
    card = await asyncio.to_thread(signed_card)
    if card is None:
        return JSONResponse({"error": "not_ready"}, status_code=404)
    body = json.dumps(card, ensure_ascii=False, separators=(",", ":")).encode()
    etag = '"' + hashlib.sha256(body).hexdigest()[:32] + '"'
    headers = {"Cache-Control": "public, max-age=300", "ETag": etag, "X-Robots-Tag": "noindex, nofollow"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(body, media_type="application/json", headers=headers)


# —— 消息、任务的 JSON ——————————————————————————————————————————————————

def agent_message(text: str, context_id: str, task_id: str | None = None, *, used: list[str] | None = None, label: str = "",
                  data: dict | None = None) -> dict:
    parts: list[dict] = [{"text": text, "mediaType": "text/plain"}] if text else []
    if data is not None:
        parts.append({"data": data, "mediaType": DECISION_TYPE})
    m: dict = {"messageId": uuid.uuid4().hex, "contextId": context_id, "role": "ROLE_AGENT", "parts": parts}
    if task_id:
        m["taskId"] = task_id
    if used or label or data is not None:
        m["extensions"] = [u for u, on in ((EXT_CARD, bool(used or label)), (EXT_DECISION, data is not None)) if on]
    if used or label:
        m["metadata"] = {EXT_CARD: {"used": used or [], "label": label}}
    return m


def task_json(r: sqlite3.Row, history_length: int | None = None) -> dict:
    t: dict = {"id": r["id"], "contextId": r["context_id"], "status": {"state": r["state"], "timestamp": r["status_at"]}}
    if r["status_msg"]:
        t["status"]["message"] = json.loads(r["status_msg"])
    arts = json.loads(r["artifacts"] or "[]")
    if arts:
        t["artifacts"] = arts
    hist = json.loads(r["history"] or "[]")
    if history_length is None or history_length > 0:
        hist = hist[-history_length:] if history_length else hist
        if hist:
            t["history"] = hist
    return t


def task_row(tid: str, peer_key: str) -> sqlite3.Row:
    """这个人的任务（别人的一律当不存在：A2A 13.1）。等你点头的卡要是过期了，先把任务收个尾。"""
    with _lock, adb() as conn:
        r = conn.execute("SELECT * FROM a2a_tasks WHERE id=?", (tid,)).fetchone()
    if not r or r["peer"] != peer_key:
        raise RpcError(E_NOT_FOUND, "Task not found", taskId=tid)
    if r["state"] == "TASK_STATE_AUTH_REQUIRED" and r["inbox_id"] and card_status(r["inbox_id"]) in ("expired", "withdrawn"):
        expire_task(dict(r))
        with _lock, adb() as conn:
            r = conn.execute("SELECT * FROM a2a_tasks WHERE id=?", (tid,)).fetchone()
    return r


def card_status(inbox_id: str) -> str | None:
    with _lock, inbox.idb() as conn:
        inbox.expire(conn)
        r = conn.execute("SELECT status FROM inbox WHERE id=?", (inbox_id,)).fetchone()
    return r["status"] if r else None


def expire_task(task: dict) -> None:
    """你一直没点（约的那天都过了）：任务结束，告诉对方这次先算了（不说原因）。"""
    ask = cardagent.ask_row(task["inbox_id"]) or {}
    lang = ask.get("lang") or settings.language
    who = settings.user_name or settings.app_name
    text = cardagent.spaced(f"{who}没来得及回，这次先算了。") if lang == "zh" else f"{who} didn't get to this in time; let's leave it."
    msg = agent_message(text, task["context_id"], task["id"], data={"outcome": "expired", "by": "owner", "at": ts()})
    hist = json.loads(task["history"] or "[]") + [msg]
    task.update(state="TASK_STATE_COMPLETED", status_msg=json.dumps(msg, ensure_ascii=False), history=json.dumps(hist[-40:], ensure_ascii=False),
                updated_at=now_iso(), status_at=ts())
    save_task(task)


def save_task(r: dict) -> None:
    with _lock, adb() as conn:
        conn.execute("""INSERT INTO a2a_tasks(id, context_id, peer, friend_id, state, status_msg, artifacts, history, inbox_id, push,
            created_at, updated_at, status_at) VALUES(:id,:context_id,:peer,:friend_id,:state,:status_msg,:artifacts,:history,:inbox_id,:push,
            :created_at,:updated_at,:status_at) ON CONFLICT(id) DO UPDATE SET state=excluded.state, status_msg=excluded.status_msg,
            artifacts=excluded.artifacts, history=excluded.history, inbox_id=excluded.inbox_id, push=excluded.push,
            updated_at=excluded.updated_at, status_at=excluded.status_at""", r)


def row_dict(r: sqlite3.Row) -> dict:
    return dict(r)


# —— 进来的消息 ————————————————————————————————————————————————————

def peer_key(peer: social.Peer) -> str:
    return str(peer.friend["id"]) if peer.friend else "anon"


def read_message(params: dict) -> tuple[dict, str]:
    """SendMessageRequest → (message, 文字)。只收文字；别的 part 一律不认（A2A 的 ContentTypeNotSupported）。"""
    m = params.get("message")
    if not isinstance(m, dict):
        raise RpcError(E_PARAMS, "message is required")
    mid = m.get("messageId")
    if not isinstance(mid, str) or not ID_RE.match(mid):
        raise RpcError(E_PARAMS, "message.messageId is required")
    if m.get("role") not in ("ROLE_USER", "user"):
        raise RpcError(E_PARAMS, "message.role must be ROLE_USER")
    parts = m.get("parts")
    if not isinstance(parts, list) or not parts:
        raise RpcError(E_PARAMS, "message.parts must not be empty")
    texts = [p["text"] for p in parts if isinstance(p, dict) and isinstance(p.get("text"), str) and p["text"].strip()]
    if not texts:
        raise RpcError(E_CONTENT, "Only text parts are supported")
    text = "\n".join(texts)
    if len(text) > 8000:
        raise RpcError(E_PARAMS, "message is too long")
    for k in ("taskId", "contextId"):
        if m.get(k) is not None and not (isinstance(m[k], str) and ID_RE.match(m[k])):
            raise RpcError(E_PARAMS, f"message.{k} is not valid")
    return m, text


def push_of(peer: social.Peer, cfg: dict | None) -> dict | None:
    """对方要我们推进展：只给在册朋友，而且地址必须在他自己的根地址下（不然就是让我们替他去敲别人的门）。"""
    if not cfg or not peer.friend or not isinstance(cfg, dict):
        return None
    url = str(cfg.get("url") or "")
    root = str(peer.friend.get("url") or "").rstrip("/")
    if not root or not (url == root or url.startswith(root + "/")):
        return None
    token = str(cfg.get("token") or "")[:200]
    auth = cfg.get("authentication") if isinstance(cfg.get("authentication"), dict) else None
    return {"id": str(cfg.get("id") or uuid.uuid4().hex)[:100], "url": url, "token": token,
            **({"authentication": {"scheme": str(auth.get("scheme") or "")[:20], "credentials": str(auth.get("credentials") or "")[:500]}}
               if auth else {})}


async def send_message(peer: social.Peer, params: dict) -> dict:
    m, text = read_message(params)
    key = peer_key(peer)
    with _lock, adb() as conn:
        seen = conn.execute("SELECT result FROM a2a_seen WHERE peer=? AND message_id=?", (key, m["messageId"])).fetchone()
    if seen:  # 同一条消息又来了一遍（网断了重试）：回当时的结果，不再问模型、不再记一句
        return json.loads(seen["result"])
    task = None
    if m.get("taskId"):
        task = row_dict(task_row(m["taskId"], key))
        if task["state"] in TERMINAL:
            raise RpcError(E_UNSUPPORTED, "Task is in a terminal state", taskId=task["id"])
        if m.get("contextId") and m["contextId"] != task["context_id"]:
            raise RpcError(E_PARAMS, "contextId does not match the task")
    ctx = task["context_id"] if task else (m.get("contextId") or uuid.uuid4().hex)
    user_msg = {"messageId": m["messageId"], "contextId": ctx, "role": "ROLE_USER", "parts": [{"text": text}],
                **({"taskId": task["id"]} if task else {})}
    now = ts()
    if peer.blocked:
        # 拉黑的：看起来办了，其实什么都不做（不调模型、不出卡、不记对方的话）
        result = {"task": {"id": uuid.uuid4().hex, "contextId": ctx, "status": {"state": "TASK_STATE_REJECTED", "timestamp": now}}}
        remember(key, m["messageId"], result)
        return result
    ref = f"a2a:{key}:{ctx}"
    ans = await cardagent.answer(peer.friend, text, channel="a2a", ref=ref, kid=peer.kid)
    reply = agent_message(ans["text"], ctx, task["id"] if task else None, used=ans["used"], label=ans["usedLabel"])
    if ans["limited"]:
        result = {"task": {"id": task["id"] if task else uuid.uuid4().hex, "contextId": ctx,
                           "status": {"state": "TASK_STATE_REJECTED", "timestamp": now, "message": reply}}}
        remember(key, m["messageId"], result)
        return result
    defer = ans.get("defer")
    if not task and not defer:
        result = {"message": reply}  # 普通的一问一答：回一条消息，不建任务
        remember(key, m["messageId"], result)
        return result
    if task is None:
        task = {"id": uuid.uuid4().hex, "context_id": ctx, "peer": key, "friend_id": peer.friend["id"] if peer.friend else None,
                "state": "", "status_msg": None, "artifacts": "[]", "history": "[]", "inbox_id": None, "push": None,
                "created_at": now_iso(), "updated_at": now_iso(), "status_at": now}
        reply["taskId"] = task["id"]
        user_msg["taskId"] = task["id"]
    hist = json.loads(task["history"] or "[]") + [user_msg, reply]
    waiting = defer or (task.get("inbox_id") and pending_card(task["inbox_id"]))
    state = "TASK_STATE_AUTH_REQUIRED" if waiting else "TASK_STATE_INPUT_REQUIRED"  # 等本人点头 / 轮到对方说
    push = push_of(peer, (params.get("configuration") or {}).get("taskPushNotificationConfig")) or \
        (json.loads(task["push"]) if task.get("push") else None)
    task.update(state=state, status_msg=json.dumps(reply, ensure_ascii=False), history=json.dumps(hist[-40:], ensure_ascii=False),
                inbox_id=(defer or {}).get("inbox_id") or task.get("inbox_id"), push=json.dumps(push) if push else None,
                updated_at=now_iso(), status_at=now)
    save_task(task)
    with _lock, adb() as conn:
        r = conn.execute("SELECT * FROM a2a_tasks WHERE id=?", (task["id"],)).fetchone()
    conf = params.get("configuration") if isinstance(params.get("configuration"), dict) else {}
    result = {"task": task_json(r, int_param(conf, "historyLength", 0, 100))}
    remember(key, m["messageId"], result)
    return result


def remember(key: str, mid: str, result: dict) -> None:
    """记下这条消息当时的回应（7 天内同一个 messageId 再来，回一样的）。"""
    now = datetime.now(timezone.utc)
    with _lock, adb() as conn:
        conn.execute("INSERT OR REPLACE INTO a2a_seen(peer, message_id, result, at) VALUES(?,?,?,?)",
                     (key, mid, json.dumps(result, ensure_ascii=False), now.isoformat(timespec="seconds")))
        conn.execute("DELETE FROM a2a_seen WHERE at < ?", ((now - timedelta(days=7)).isoformat(timespec="seconds"),))


def pending_card(inbox_id: str) -> bool:
    """这张卡还在等你点头吗。"""
    with _lock, inbox.idb() as conn:
        r = conn.execute("SELECT status FROM inbox WHERE id=?", (inbox_id,)).fetchone()
    return bool(r and r["status"] in inbox.OPEN)


# —— 你点了以后：告诉对方（cardagent.DELIVER["a2a"]） ———————————————————————————

async def deliver(ask: dict, text: str, data: dict) -> bool:
    """你在收件箱卡上点了：把对应任务改成新状态（对方 GetTask 就能看到），有推送地址就推过去。"""
    with _lock, adb() as conn:
        r = conn.execute("SELECT * FROM a2a_tasks WHERE inbox_id=? ORDER BY updated_at DESC LIMIT 1", (ask["inbox_id"],)).fetchone()
    if not r or r["state"] in TERMINAL:
        return False
    task = row_dict(r)
    outcome = data.get("outcome")
    decision = {"outcome": outcome, **({"proposal": data["proposal"]} if data.get("proposal") else {}),
                **({"note": data["note"]} if data.get("note") else {}), "by": "owner", "at": ts()}
    msg = agent_message(text, task["context_id"], task["id"], data=decision)
    state = "TASK_STATE_INPUT_REQUIRED" if outcome == "counter" else "TASK_STATE_COMPLETED"
    arts = json.loads(task["artifacts"] or "[]")
    if state == "TASK_STATE_COMPLETED":
        arts.append({"artifactId": f"decision-{uuid.uuid4().hex[:8]}", "name": "decision",
                     "parts": [{"data": decision, "mediaType": DECISION_TYPE}, {"text": text, "mediaType": "text/plain"}]})
    now = ts()
    hist = json.loads(task["history"] or "[]") + [msg]
    task.update(state=state, status_msg=json.dumps(msg, ensure_ascii=False), artifacts=json.dumps(arts, ensure_ascii=False),
                history=json.dumps(hist[-40:], ensure_ascii=False), updated_at=now_iso(), status_at=now)
    save_task(task)
    if task.get("push"):
        spawn(push_update(json.loads(task["push"]), task["id"]))
    return True


cardagent.DELIVER["a2a"] = deliver


def spawn(coro) -> None:
    t = asyncio.create_task(coro)
    _bg.add(t)
    t.add_done_callback(_bg.discard)


async def push_update(cfg: dict, tid: str) -> None:
    """推一次任务的新状态（A2A 4.3.3：StreamResponse 的 statusUpdate）：朋友的服务器用签名请求推，带上他给的令牌。失败隔一会儿再试，最多 4 次。"""
    with _lock, adb() as conn:
        r = conn.execute("SELECT * FROM a2a_tasks WHERE id=?", (tid,)).fetchone()
    if not r:
        return
    t = task_json(r, 0)
    body = {"statusUpdate": {"taskId": t["id"], "contextId": t["contextId"], "status": t["status"]}}
    headers = {"Content-Type": "application/a2a+json", "A2A-Version": VERSION}
    if cfg.get("token"):
        headers["X-A2A-Notification-Token"] = cfg["token"]
    auth = cfg.get("authentication") or {}
    if auth.get("scheme") and auth.get("credentials"):
        headers["Authorization"] = f"{auth['scheme']} {auth['credentials']}"
    fr = social.friend(r["friend_id"]) if r["friend_id"] else None
    for delay in (0, 5, 30, 120):
        await asyncio.sleep(delay)
        try:
            if fr:
                res = await social.signed_post(cfg["url"], body, to_kid=fr["kid"], headers=headers)
            else:
                return
            if 200 <= res.status_code < 300:
                return
        except (httpx.HTTPError, HTTPException):
            continue


# —— JSON-RPC ————————————————————————————————————————————————————————

def rpc_ok(id_, result) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": id_, "result": result})


def rpc_err(id_, code: int, message: str, **meta) -> JSONResponse:
    err: dict = {"code": code, "message": message}
    if code in REASONS:
        err["data"] = [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": REASONS[code], "domain": "a2a-protocol.org",
                        "metadata": {k: str(v) for k, v in {**meta, "timestamp": ts()}.items()}}]
    return JSONResponse({"jsonrpc": "2.0", "id": id_, "error": err})


def int_param(params: dict, name: str, lo: int, hi: int) -> int | None:
    v = params.get(name)
    if v is None:
        return None
    if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
        raise RpcError(E_PARAMS, f"{name} is not valid")
    return v


async def dispatch(peer: social.Peer, method: str, params: dict):
    key = peer_key(peer)
    if method == "SendMessage":
        return await send_message(peer, params)
    if method == "GetTask":
        r = task_row(str(params.get("id") or ""), key)
        return task_json(r, int_param(params, "historyLength", 0, 100))
    if method == "ListTasks":
        size = int_param(params, "pageSize", 1, 100) or 50
        hl = int_param(params, "historyLength", 0, 100)
        if key == "anon":  # 没签名的陌生人之间分不出谁是谁：不列（拿着任务 id 照样能 GetTask）
            return {"tasks": [], "nextPageToken": "", "pageSize": size, "totalSize": 0}
        q, args = "SELECT * FROM a2a_tasks WHERE peer=?", [key]
        if params.get("contextId"):
            q += " AND context_id=?"
            args.append(str(params["contextId"]))
        if params.get("status"):
            q += " AND state=?"
            args.append(str(params["status"]))
        tok = str(params.get("pageToken") or "")
        if tok:
            q += " AND status_at < ?"
            args.append(tok)
        with _lock, adb() as conn:
            total = conn.execute(q.replace("SELECT *", "SELECT COUNT(*)"), args).fetchone()[0]
            rows = conn.execute(q + " ORDER BY status_at DESC LIMIT ?", [*args, size + 1]).fetchall()
        more = len(rows) > size
        rows = [task_row(r["id"], key) for r in rows[:size]]  # 顺手收尾过期的
        tasks = [task_json(r, hl) for r in rows]
        if not params.get("includeArtifacts"):
            for t in tasks:
                t.pop("artifacts", None)
        return {"tasks": tasks, "nextPageToken": rows[-1]["status_at"] if more and rows else "", "pageSize": size, "totalSize": total}
    if method == "CancelTask":
        r = row_dict(task_row(str(params.get("id") or ""), key))
        if r["state"] in TERMINAL:
            raise RpcError(E_NOT_CANCELABLE, "Task is not cancelable", taskId=r["id"])
        r.update(state="TASK_STATE_CANCELED", updated_at=now_iso(), status_at=ts())
        save_task(r)
        if r.get("inbox_id") and pending_card(r["inbox_id"]):  # 对方不约了：你那张卡撤掉
            try:
                await inbox.withdraw(r["inbox_id"])
            except HTTPException:
                pass
        with _lock, adb() as conn:
            return task_json(conn.execute("SELECT * FROM a2a_tasks WHERE id=?", (r["id"],)).fetchone())
    if method in ("SendStreamingMessage", "SubscribeToTask"):
        raise RpcError(E_UNSUPPORTED, "Streaming is not supported")
    if method == "GetExtendedAgentCard":
        raise RpcError(E_UNSUPPORTED, "No extended agent card")
    if method in ("CreateTaskPushNotificationConfig", "GetTaskPushNotificationConfig", "ListTaskPushNotificationConfigs",
                  "DeleteTaskPushNotificationConfig"):
        return push_config(peer, method, params)
    if method in METHODS_03:
        raise RpcError(E_VERSION, "This agent speaks A2A 1.0", supportedVersions=VERSION)
    raise RpcError(E_METHOD, "Method not found")


def push_config(peer: social.Peer, method: str, params: dict):
    key = peer_key(peer)
    tid = str(params.get("taskId") or "")
    r = row_dict(task_row(tid, key))
    cur = json.loads(r["push"]) if r.get("push") else None

    def view(c: dict) -> dict:
        return {"id": c["id"], "taskId": tid, "url": c["url"], **({"token": c["token"]} if c.get("token") else {})}

    if method == "CreateTaskPushNotificationConfig":
        cfg = push_of(peer, params)
        if not cfg:
            raise RpcError(E_UNSUPPORTED, "Push notifications only go to a friend's own server")
        r.update(push=json.dumps(cfg), updated_at=now_iso())
        save_task(r)
        return view(cfg)
    if method == "GetTaskPushNotificationConfig":
        if not cur or cur["id"] != str(params.get("id") or ""):
            raise RpcError(E_NOT_FOUND, "Push notification config not found", taskId=tid)
        return view(cur)
    if method == "ListTaskPushNotificationConfigs":
        return {"configs": [view(cur)] if cur else [], "nextPageToken": ""}
    if cur and cur["id"] == str(params.get("id") or ""):
        r.update(push=None, updated_at=now_iso())
        save_task(r)
    return {}


@public_router.post("/f/a2a")
async def rpc(request: Request):
    body, peer = await social.authenticate(request)
    try:
        req = json.loads(body or b"null")
    except ValueError:
        return rpc_err(None, E_PARSE, "Invalid JSON payload")
    if not isinstance(req, dict) or req.get("jsonrpc") != "2.0" or not isinstance(req.get("method"), str):
        return rpc_err(req.get("id") if isinstance(req, dict) else None, E_REQUEST, "Request payload validation error")
    id_ = req.get("id")
    if not peer.friend and not peer.blocked and not cardagent.strangers_allowed():
        # 只理朋友：没签名、钥匙不认识、删掉的一律 403（A2A 的 ExtensionSupportRequired：请求要用 signed-requests 签名）
        r = rpc_err(id_, E_EXTENSION, "This agent only answers its owner's friends: sign requests with a key it knows", extension=EXT_SIGNED)
        r.status_code = 403
        return r
    ver = (request.headers.get("a2a-version") or request.query_params.get("A2A-Version") or "").strip()
    if ver and ver not in ("1.0", "1"):
        return rpc_err(id_, E_VERSION, f"A2A version {ver} is not supported", supportedVersions=VERSION)
    params = req.get("params") if isinstance(req.get("params"), dict) else {}
    try:
        return rpc_ok(id_, await dispatch(peer, req["method"], params))
    except RpcError as e:
        return rpc_err(id_, e.code, e.message, **e.meta)


# —— 我们问别人（给 app 和测试用；对方的回话一律当资料存着，只给你看） ——————————————

class SendIn(BaseModel):
    friend: str          # 好友 id
    text: str            # 你要问的话（原样发过去）
    contextId: str | None = None
    taskId: str | None = None


async def remote_card(fr: dict) -> dict:
    """对方的 A2A 名片（验过签：必须是好友表里那把钥匙签的）。"""
    url = fr.get("a2a") or ""
    if not url:
        raise HTTPException(400, L("对方还没有 A2A 接口", "This friend has no A2A endpoint"))
    await social.check_host(url)
    async with httpx.AsyncClient(timeout=10, follow_redirects=False) as c:
        r = await c.get(url, headers={"Accept": "application/json"})
    if r.status_code != 200 or len(r.content) > social.FETCH_MAX:
        raise HTTPException(502, L("取不到对方的 A2A 名片", "Couldn't fetch their A2A card"))
    card = r.json()
    if not isinstance(card, dict) or not social.verify_jws(card, fr["pub"]):
        raise HTTPException(502, L("对方的 A2A 名片签名对不上", "Their A2A card signature doesn't check out"))
    return card


@router.post("/api/a2a/send")
async def send(body: SendIn):
    """让名片 agent 替你问一个朋友的 agent 一句（原样发过去；对方回的话只给你看，不进任何 Agent）。"""
    fr = social.friend(body.friend)
    if not fr or fr.get("status") != "active":
        raise HTTPException(404, L("没有这个朋友", "No such friend"))
    text = cardagent.clean_text(body.text, 2000)
    if not text:
        raise HTTPException(400, L("要问的话是空的", "Nothing to ask"))
    card = await remote_card(fr)
    root = str(fr.get("url") or "").rstrip("/")
    iface = next((i for i in card.get("supportedInterfaces") or [] if isinstance(i, dict) and i.get("protocolBinding") == "JSONRPC"
                  and str(i.get("protocolVersion")) in ("1.0", "1") and str(i.get("url") or "").startswith(root + "/")), None)
    if not iface:
        raise HTTPException(502, L("对方没有能用的 A2A 1.0 接口", "They have no usable A2A 1.0 endpoint"))
    oid, token = f"ao-{uuid.uuid4().hex[:10]}", secrets.token_urlsafe(24)
    msg: dict = {"messageId": uuid.uuid4().hex, "role": "ROLE_USER", "parts": [{"text": text, "mediaType": "text/plain"}]}
    if body.contextId:
        msg["contextId"] = body.contextId
    if body.taskId:
        msg["taskId"] = body.taskId
    me = social.my_url()
    params: dict = {"message": msg, "configuration": {"acceptedOutputModes": ["text/plain", DECISION_TYPE], "historyLength": 0,
                                                      "returnImmediately": True}}
    if me:
        params["configuration"]["taskPushNotificationConfig"] = {"url": f"{me}/f/a2a/push", "token": token}
    req = {"jsonrpc": "2.0", "id": oid, "method": "SendMessage", "params": params}
    res = await social.signed_post(str(iface["url"]), req, to_kid=fr["kid"],
                                   headers={"A2A-Version": VERSION, "A2A-Extensions": EXT_SIGNED}, timeout=90)
    try:
        j = res.json()
    except ValueError:
        raise HTTPException(502, f"HTTP {res.status_code}") from None
    if "error" in j:
        raise HTTPException(502, str((j["error"] or {}).get("message") or "error")[:200])
    result = j.get("result") or {}
    got = result.get("task") or {}
    m = result.get("message") or (got.get("status") or {}).get("message") or {}
    reply = "\n".join(p.get("text") for p in m.get("parts") or [] if isinstance(p, dict) and isinstance(p.get("text"), str))
    ctx = got.get("contextId") or m.get("contextId") or body.contextId
    state = (got.get("status") or {}).get("state")
    with _lock, adb() as conn:
        conn.execute("INSERT INTO a2a_out(id, friend_id, context_id, task_id, state, text, reply, push_token, data, created_at, updated_at) "
                     "VALUES(?,?,?,?,?,?,?,?,?,?,?)", (oid, fr["id"], ctx, got.get("id"), state, text, reply[:4000], token,
                                                       json.dumps(result, ensure_ascii=False)[:20000], now_iso(), now_iso()))
    log_activity(L(f"问{fr['name']}的 agent：「{text}」", f'Asked {fr["name"]}\'s agent: "{text}"'), "social", actor=L("名片 agent", "Card agent"))
    return {"ok": True, "id": oid, "contextId": ctx, "taskId": got.get("id"), "state": state, "reply": reply,
            "used": ((m.get("metadata") or {}).get(EXT_CARD) or {}).get("label") or ""}


@public_router.post("/f/a2a/push")
async def push_in(request: Request):
    """朋友的服务器把我们问过的任务的进展推回来（A2A 4.3.3）：签名 + 当初给的令牌都对才收。对方写的话只存着给你看。"""
    body, peer = await social.authenticate(request, 64_000)
    if not peer.friend:
        return JSONResponse({"error": "not_friends"}, status_code=403)
    try:
        j = json.loads(body)
    except ValueError:
        return JSONResponse({"error": "bad_request"}, status_code=400)
    upd = j.get("statusUpdate") if isinstance(j, dict) else None
    if not isinstance(upd, dict) or not isinstance(upd.get("taskId"), str):
        return JSONResponse({"error": "bad_request"}, status_code=400)
    token = request.headers.get("x-a2a-notification-token") or ""
    with _lock, adb() as conn:
        r = conn.execute("SELECT * FROM a2a_out WHERE friend_id=? AND task_id=? ORDER BY created_at DESC LIMIT 1",
                         (peer.friend["id"], upd["taskId"])).fetchone()
        if not r or not token or not secrets.compare_digest(token, r["push_token"] or ""):
            return JSONResponse({"error": "unknown_task"}, status_code=404)
        st = upd.get("status") or {}
        m = st.get("message") or {}
        text = "\n".join(p.get("text") for p in m.get("parts") or [] if isinstance(p, dict) and isinstance(p.get("text"), str))
        dec = next((p.get("data") for p in m.get("parts") or [] if isinstance(p, dict) and p.get("mediaType") == DECISION_TYPE), None)
        conn.execute("UPDATE a2a_out SET state=?, reply=?, data=?, updated_at=? WHERE id=?",
                     (str(st.get("state") or "")[:40], text[:4000], json.dumps({"statusUpdate": upd}, ensure_ascii=False)[:20000], now_iso(), r["id"]))
    outcome = (dec or {}).get("outcome") if isinstance(dec, dict) else None
    log_activity(L(f"{peer.friend['name']}那边回了：「{cardagent.clean_line(text, 200)}」", f'{peer.friend["name"]} replied: "{cardagent.clean_line(text, 200)}"')
                 + (f" [{outcome}]" if outcome else ""), "social", actor=L("名片 agent", "Card agent"))
    return {"ok": True}


def out_facts(data: str | None) -> tuple[str, str]:
    """a2a_out 存下的最后一次结果（SendMessage 的 result 或推回来的 statusUpdate）里：(对方本人的决定 outcome, 对方名片 agent 的「用了什么」)。"""
    try:
        j = json.loads(data or "{}")
    except ValueError:
        return "", ""
    if not isinstance(j, dict):
        return "", ""
    st = (j.get("statusUpdate") or {}).get("status") or (j.get("task") or {}).get("status") or {}
    m = st.get("message") or j.get("message") or {}
    parts = m.get("parts") if isinstance(m, dict) else None
    dec = next((p.get("data") for p in parts or [] if isinstance(p, dict) and p.get("mediaType") == DECISION_TYPE), None)
    card = ((m.get("metadata") or {}).get(EXT_CARD) or {}) if isinstance(m, dict) else {}
    return str((dec or {}).get("outcome") or "") if isinstance(dec, dict) else "", str(card.get("label") or "") if isinstance(card, dict) else ""


@router.get("/api/a2a/out")
async def out_list(friend: str | None = None, limit: int = 50):
    """我们问过别人的（对方的回话原样给你看）。outcome = 对方本人在卡上的决定（accepted / declined / counter / ack / private_declined / expired），
    usedLabel = 对方的名片 agent 说它用了什么。"""
    limit = max(1, min(limit, 200))
    with _lock, adb() as conn:
        rows = conn.execute("SELECT * FROM a2a_out" + (" WHERE friend_id=?" if friend else "") + " ORDER BY created_at DESC LIMIT ?",
                            ([friend] if friend else []) + [limit]).fetchall()
    items = []
    for r in rows:
        outcome, label = out_facts(r["data"])
        items.append({"id": r["id"], "friend": r["friend_id"], "contextId": r["context_id"], "taskId": r["task_id"], "state": r["state"],
                      "text": r["text"], "reply": r["reply"], "outcome": outcome, "usedLabel": label, "createdAt": r["created_at"],
                      "updatedAt": r["updated_at"]})
    return {"ok": True, "items": items}
