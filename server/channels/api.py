"""/api/setup/channels…（第 6 批）：设置中心的渠道那一页、命令行之外的另一个入口。只认主人的设备（engine/setup_api.owner：app 的令牌、
Tailscale 里主人的设备；带 X-Mousse-Via 的脚本、/mcp 替 Agent 发的一律 403），令牌和邮箱密码只进 secrets.env（server.json 里只写 ${变量名}），
永远不回给 app（只回有没有、从哪来、末四位）；写 server.json 之前先备份（setup_api.edit）。出错一律 {"ok": false, "error"}。

  GET    /api/setup/channels                          渠道和账号（不带密钥）、主人、联系人、待批的配对
  GET    /api/setup/channels/status                   每个账号：连上没有、最后一次收到、最后一个错、死信、配置的毛病、待批的配对
  PUT    /api/setup/channels/{渠道}                    渠道这一层：{"enabled"?, "bindings"?, "limits"?}
  PUT    /api/setup/channels/{渠道}/{账号}              加 / 改一个账号（字段见 README；"token" / imap.password / smtp.password 进 secrets.env）
  DELETE /api/setup/channels/{渠道}/{账号}              删掉这个账号的配置（secrets.env 里的值不动）
  POST   /api/setup/channels/{渠道}/{账号}/test         测一下（Telegram：getMe；邮件：IMAP 登录 + 打开文件夹、SMTP 登录；不发消息）
  POST   /api/setup/channels/{渠道}/{账号}/enable       {"enabled": true | false}：开关（开的时候渠道那一层也打开）
  POST   /api/setup/channels/owner-code               {"channel"?, "minutes"?}：出一个认主人的码（一次有效，默认 10 分钟）
  DELETE /api/setup/channels/owners/{id}              这个渠道账号不再是主人
  GET    /api/setup/channels/pairs?status=            配对（默认待批的）
  POST   /api/setup/channels/pairs/{码或 id}           {"action": "approve" | "reject", "tier"?, "agent"?}
  GET    /api/setup/channels/contacts                 联系人
  POST   /api/setup/channels/contacts                 {"channel", "sender", "name"?, "tier"?, "agent"?}：直接加一个（不经配对）
  PUT    /api/setup/channels/contacts/{id}            {"tier"?, "agent"?, "status"?, "name"?}
  DELETE /api/setup/channels/contacts/{id}
  GET    /api/setup/channels/outbox?status=dead       出去的队列（死信）
  POST   /api/setup/channels/outbox/{id}/retry        死信重发
"""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, Request

import secretstore
from engine import setup_api
from engine.setup_api import SetupError
from i18n import L

from . import accounts, cards, hub, people, store
from .base import ACCOUNT_ID, CARD_MODES, DM_POLICIES, VAR

router = APIRouter(dependencies=[Depends(setup_api.owner)])
TELEGRAM_KEYS = {"token", "agent", "dm", "cards", "tier", "groups", "owners", "allow_from", "stream", "api_base", "allow_bots", "limits",
                 "max_media_mb", "enabled", "poll_timeout"}
EMAIL_KEYS = {"address", "imap", "smtp", "owners", "authserv_id", "agent", "dm", "cards", "tier", "poll", "replies_per_hour", "max_media_mb",
              "allow_from", "enabled", "limits", "name"}
DISCORD_KEYS = (TELEGRAM_KEYS - {"poll_timeout"}) | {"message_content"}
CHANNEL_KEYS = {"enabled", "bindings", "limits", "accounts"}
HOST = re.compile(r"^[A-Za-z0-9.-]{1,253}$")


def kind_ok(kind: str) -> str:
    if kind not in accounts.KINDS:
        raise SetupError(404, L(f"没有 {kind} 这个渠道（有：{', '.join(accounts.KINDS)}）", f"No channel called {kind} (there are: {', '.join(accounts.KINDS)})"))
    return kind


def secret_info(acc, value) -> dict:
    """配置里的一个密钥：${变量名} → secretstore.info；直接写在 server.json 里的 → 只说有、是直写的。永远不带值。"""
    s = str(value or "").strip()
    m = VAR.match(s)
    if m:
        return secretstore.info(m.group(1))
    return {"name": None, "present": bool(s), "source": "server.json" if s else None, "hint": None, "savedAt": None}


def view(acc) -> dict:
    out = {"id": acc.id, "kind": acc.kind, "enabled": acc.enabled, "agent": acc.agent, "dm": acc.dm, "cards": acc.cards, "tier": acc.tier,
           "groups": acc.groups(), "owners": sorted(acc.owners()), "allowFrom": sorted(acc.allow_from()), "problems": accounts.problems(acc)}
    if acc.kind in ("telegram", "discord"):
        out.update(token=secret_info(acc, acc.get("token")), stream=acc.get("stream") is True, allowBots=acc.get("allow_bots") is True)
        bot = store.state(acc.kind, acc.id, "bot")
        out["bot"] = bot or None
        if acc.kind == "discord":
            out["messageContent"] = acc.get("message_content") is True
    elif acc.kind == "email":
        imap = acc.get("imap") if isinstance(acc.get("imap"), dict) else {}
        smtp = acc.get("smtp") if isinstance(acc.get("smtp"), dict) else {}
        out.update(address=acc.get("address") or "", authservId=acc.get("authserv_id") or "", poll=acc.get("poll"),
                   imap={k: imap.get(k) for k in ("host", "port", "user", "folder", "security", "idle") if k in imap} | {"password": secret_info(acc, imap.get("password"))},
                   smtp={k: smtp.get(k) for k in ("host", "port", "user", "security") if k in smtp} | {"password": secret_info(acc, smtp.get("password"))})
    return out


def runtime(acc) -> dict:
    r = hub.runner(acc.kind, acc.id)
    st = store.states(acc.kind, acc.id)
    live = dict(r.status) if r else {}
    dead = len(store.outbox(acc.kind, acc.id, "dead", 500))
    queued = len(store.outbox(acc.kind, acc.id, "queued", 500))
    pairs = [p for p in people.pairs("pending") if p["channel"] == acc.kind and p["account"] == acc.id]
    return {"kind": acc.kind, "id": acc.id, "enabled": acc.enabled, "running": r is not None,
            "state": live.get("state") or st.get("state") or ("off" if not acc.enabled else "stopped"),
            "detail": live.get("detail") or st.get("detail") or "", "since": live.get("since"),
            "lastIn": st.get("last_in") or None, "lastError": st.get("error") or None, "lastErrorAt": st.get("error_at") or None,
            "deadLetters": dead, "queued": queued, "pendingPairs": [{"id": p["id"], "code": people.shown(p["code"]), "name": p["name"],
                                                                      "sender": p["sender"], "expires": p["expires"]} for p in pairs],
            "problems": accounts.problems(acc)}


@router.get("/api/setup/channels")
async def overview():
    accs = accounts.accounts()
    return {"ok": True, "builtin": accounts.builtin(), "channels": [
        {"kind": k, "name": accounts.NAMES.get(k, k), "enabled": accounts.channel_cfg(k).get("enabled") is True,
         "bindings": accounts.bindings(k), "accounts": [view(a) for a in accs if a.kind == k]} for k in accounts.KINDS],
        "owners": [owner_view(p) for k in accounts.KINDS for p in people.owners(k)],
        "contacts": len([p for p in people.people(role="contact") if p["status"] == "active"]),
        "pendingPairs": len(people.pairs("pending"))}


def owner_view(p: dict) -> dict:
    return {"id": p["id"], "channel": p["channel"], "sender": p["sender"], "name": p["name"], "account": p["account"], "since": p["created_at"]}


def contact_view(p: dict) -> dict:
    return {"id": p["id"], "channel": p["channel"], "sender": p["sender"], "name": p["name"], "tier": p["tier"], "agent": p["agent"] or "main",
            "status": p["status"], "via": p["via"], "account": p["account"], "since": p["created_at"]}


@router.get("/api/setup/channels/status")
async def status():
    return {"ok": True, "builtin": accounts.builtin(), "accounts": [runtime(a) for a in accounts.accounts()],
            "openOwnerCodes": people.open_claims()}


# —— 写配置 ——————————————————————————————————————————————————————————————————————

def var_name(kind: str, account: str, what: str) -> str:
    base = re.sub(r"[^A-Z0-9]+", "_", account.upper()).strip("_") or "DEFAULT"
    if kind == "telegram":
        return "TELEGRAM_BOT_TOKEN" if account == "default" else f"TELEGRAM_{base}_TOKEN"
    if kind == "discord":
        return "DISCORD_BOT_TOKEN" if account == "default" else f"DISCORD_{base}_TOKEN"
    return f"EMAIL_{base}_{what.upper()}_PASSWORD" if what else f"EMAIL_{base}_PASSWORD"


def put_secret(name: str, value: str, todo: list) -> str:
    """密钥先查一遍像不像样，记进 todo（整个请求都查过了才真的存，免得存了一半）→ 配置里写的 ${变量名}。"""
    try:
        todo.append((name, secretstore.check_value(name, value)))
    except secretstore.SecretError:
        raise SetupError(400, L("令牌 / 密码里有它不该有的字（空格、换行、引号、中文……）", "The token or password has characters it can't have (spaces, line breaks, "
                                "quotes, non-ASCII)"), field="secret") from None
    return "${" + name + "}"


def _list_of_ids(v, field: str) -> list[str]:
    if not isinstance(v, list) or len(v) > 50 or not all(isinstance(x, (str, int)) and len(str(x)) <= 200 for x in v):
        raise SetupError(400, L(f"{field} 要是一串 id / 地址", f"{field} must be a list of ids / addresses"), field=field)
    return [str(x).strip() for x in v if str(x).strip()]


def _choice(b: dict, key: str, allowed) -> str | None:
    v = b.get(key)
    if v is None:
        return None
    if v not in allowed:
        raise SetupError(400, L(f"{key} 只能是 {' / '.join(allowed)}", f"{key} must be one of {', '.join(allowed)}"), field=key)
    return v


def _endpoint(b: dict, part: str, kind_: str, account: str, cur: dict, todo: list) -> dict:
    """imap / smtp 的那一段：host、port、user、folder、security、password（密码进 secrets.env）。"""
    v = b.get(part)
    if v is None:
        return cur
    if not isinstance(v, dict):
        raise SetupError(400, L(f"{part} 要是一个对象", f"{part} must be an object"), field=part)
    out = dict(cur)
    host = v.get("host")
    if host is not None:
        if not isinstance(host, str) or not HOST.match(host.strip()):
            raise SetupError(400, L(f"{part}.host 不像一个主机名", f"{part}.host doesn't look like a host name"), field=f"{part}.host")
        out["host"] = host.strip()
    if v.get("port") is not None:
        if not isinstance(v["port"], int) or not 1 <= v["port"] <= 65535:
            raise SetupError(400, L(f"{part}.port 要是 1–65535", f"{part}.port must be 1–65535"), field=f"{part}.port")
        out["port"] = v["port"]
    for key in ("user", "folder"):
        if v.get(key) is not None:
            if not isinstance(v[key], str) or len(v[key]) > 200:
                raise SetupError(400, L(f"{part}.{key} 要是一个不太长的字符串", f"{part}.{key} must be a short string"), field=f"{part}.{key}")
            out[key] = v[key].strip()
    if v.get("security") is not None:
        if v["security"] not in ("tls", "starttls", "plain"):
            raise SetupError(400, L(f"{part}.security 只能是 tls / starttls / plain", f"{part}.security must be tls, starttls or plain"), field=f"{part}.security")
        out["security"] = v["security"]
    if part == "imap" and v.get("idle") is not None:
        out["idle"] = v["idle"] is True
    if isinstance(v.get("password"), str) and v["password"]:
        out["password"] = put_secret(var_name(kind_, account, "" if part == "imap" else "smtp"), v["password"], todo)
    return out


def account_patch(kind: str, account: str, b: dict, cur: dict) -> dict:
    """请求体 → 这个账号新的配置（在原来的上面改）。不认的键 400（不回原样的值）。"""
    allowed = {"telegram": TELEGRAM_KEYS, "discord": DISCORD_KEYS}.get(kind, EMAIL_KEYS)
    bad = [k for k in b if k not in allowed and k != "id"]
    if bad:
        raise SetupError(400, L(f"不认的字段：{', '.join(sorted(bad))}", f"Unknown fields: {', '.join(sorted(bad))}"), field=sorted(bad)[0])
    out = {**cur, "id": account}
    todo: list = []
    if kind == "telegram" and isinstance(b.get("token"), str) and b["token"].strip():
        tok = b["token"].strip()
        if not re.fullmatch(r"\d{3,20}:[A-Za-z0-9_-]{20,100}", tok):
            raise SetupError(400, L("这不像 Telegram 机器人的令牌（数字:一串字母）", "That doesn't look like a Telegram bot token (digits:letters)"), field="token")
        out["token"] = put_secret(var_name(kind, account, ""), tok, todo)
    if kind == "discord" and isinstance(b.get("token"), str) and b["token"].strip():
        tok = b["token"].strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{20,100}\.[A-Za-z0-9_-]{4,20}\.[A-Za-z0-9_-]{20,100}", tok):
            raise SetupError(400, L("这不像 Discord 机器人的令牌（三段，用点隔开）", "That doesn't look like a Discord bot token (three parts separated by dots)"),
                             field="token")
        out["token"] = put_secret(var_name(kind, account, ""), tok, todo)
    if kind == "email":
        out["imap"] = _endpoint(b, "imap", kind, account, cur.get("imap") if isinstance(cur.get("imap"), dict) else {}, todo)
        out["smtp"] = _endpoint(b, "smtp", kind, account, cur.get("smtp") if isinstance(cur.get("smtp"), dict) else {}, todo)
        for key in ("address", "authserv_id"):
            if b.get(key) is not None:
                v = b[key]
                if not isinstance(v, str) or len(v) > 200 or (key == "address" and "@" not in v):
                    raise SetupError(400, L(f"{key} 不像样", f"{key} doesn't look right"), field=key)
                out[key] = v.strip().lower() if key == "address" else v.strip()
        for key, lo, hi in (("poll", 15, 3600), ("replies_per_hour", 1, 100)):
            if b.get(key) is not None:
                if not isinstance(b[key], int) or not lo <= b[key] <= hi:
                    raise SetupError(400, L(f"{key} 要在 {lo}–{hi} 之间", f"{key} must be {lo}–{hi}"), field=key)
                out[key] = b[key]
    if (d := _choice(b, "dm", DM_POLICIES)) is not None:
        out["dm"] = d
    if (c := _choice(b, "cards", CARD_MODES)) is not None:
        out["cards"] = c
    if (t := _choice(b, "tier", people.TIERS)) is not None:
        out["tier"] = t
    if b.get("agent") is not None:
        if not isinstance(b["agent"], str) or not accounts.known_agent(b["agent"].strip()):
            raise SetupError(400, L("没有这个 Agent", "No such Agent"), field="agent")
        out["agent"] = b["agent"].strip()
    for key in ("owners", "allow_from"):
        if b.get(key) is not None:
            out[key] = _list_of_ids(b[key], key)
    for key in ("stream", "allow_bots", "enabled", "message_content"):
        if b.get(key) is not None:
            out[key] = b[key] is True
    if b.get("max_media_mb") is not None:
        if not isinstance(b["max_media_mb"], int) or not 1 <= b["max_media_mb"] <= 50:
            raise SetupError(400, L("max_media_mb 要在 1–50 之间", "max_media_mb must be 1–50"), field="max_media_mb")
        out["max_media_mb"] = b["max_media_mb"]
    if b.get("groups") is not None:
        g = b["groups"]
        if not isinstance(g, dict):
            raise SetupError(400, L("groups 要是一个对象", "groups must be an object"), field="groups")
        ng = {}
        if "enabled" in g:
            ng["enabled"] = g["enabled"] is True
        if "allow" in g:
            ng["allow"] = _list_of_ids(g["allow"], "groups.allow")
        if "require_mention" in g:
            ng["require_mention"] = g["require_mention"] is not False
        if "history" in g:
            if not isinstance(g["history"], int) or not 0 <= g["history"] <= 200:
                raise SetupError(400, L("groups.history 要在 0–200 之间", "groups.history must be 0–200"), field="groups.history")
            ng["history"] = g["history"]
        if "names" in g:
            ng["names"] = _list_of_ids(g["names"], "groups.names")
        out["groups"] = {**(cur.get("groups") if isinstance(cur.get("groups"), dict) else {}), **ng}
    if b.get("api_base") is not None:
        v = b["api_base"]
        if not isinstance(v, str) or not re.fullmatch(r"https?://[A-Za-z0-9.\-\[\]:]+(?:/[^\s?#]*)?", v.strip()):
            raise SetupError(400, L("api_base 要写成 http(s)://主机[:端口]", "api_base must look like http(s)://host[:port]"), field="api_base")
        out["api_base"] = v.strip().rstrip("/")
    if b.get("limits") is not None:
        if not isinstance(b["limits"], dict) or not all(k in ("owner", "contact", "stranger") and isinstance(v, int) and v > 0 for k, v in b["limits"].items()):
            raise SetupError(400, L("limits 写成 {\"contact\": 30, \"stranger\": 5}", "limits looks like {\"contact\": 30, \"stranger\": 5}"), field="limits")
        out["limits"] = b["limits"]
    for name, value in todo:  # 都查过了：现在才存密钥
        secretstore.put(name, value)
    return out


def _accounts_list(c: dict) -> list[dict]:
    """渠道这一层的 accounts 列表；单账号的写法（没有 accounts）先挪成 accounts 里 id = default 的那一个。"""
    accs = c.get("accounts")
    if isinstance(accs, list):
        return accs
    moved = {k: v for k, v in c.items() if k not in CHANNEL_KEYS}
    for k in list(moved):
        c.pop(k, None)
    c["accounts"] = [{"id": "default", **moved}] if moved else []
    return c["accounts"]


# —— 认主人的码、主人 ——————————————————————————————————————————————————————————————

@router.post("/api/setup/channels/owner-code")
async def owner_code(request: Request):
    b = await setup_api.body_of(request)
    channel = b.get("channel") or ""
    if channel and channel not in accounts.KINDS:
        raise SetupError(400, L("没有这个渠道", "No such channel"), field="channel")
    minutes = b.get("minutes") if isinstance(b.get("minutes"), int) else 10
    code, expires = people.new_claim(channel, minutes)
    links = []
    for acc in accounts.enabled("telegram") if channel in ("", "telegram") else []:
        bot = store.state("telegram", acc.id, "bot_username")
        if bot:
            links.append({"account": acc.id, "url": f"https://t.me/{bot}?start={people.normalize(code)}"})
    return {"ok": True, "code": people.shown(code), "expires": expires, "links": links,
            "how": L(f"在 Telegram 或 Discord 里私聊你的机器人发：/start {people.normalize(code)}（Telegram 也可以点上面的链接）。"
                     f"码 {minutes} 分钟内有效、只能用一次。",
                     f"In a private chat with your bot on Telegram or Discord, send: /start {people.normalize(code)} (on Telegram you can tap the "
                     f"link instead). The code works once, for {minutes} minutes.")}


@router.delete("/api/setup/channels/owners/{pid}")
async def remove_owner(pid: str):
    p = people.person_by_id(pid)
    if p is None or p["role"] != "owner":
        raise SetupError(404, L("没有这个主人账号", "No such owner account"))
    people.update_person(pid, status="removed")
    return {"ok": True}


# —— 配对、联系人 ————————————————————————————————————————————————————————————————

@router.get("/api/setup/channels/pairs")
async def list_pairs(status: str = "pending"):
    rows = people.pairs(None if status == "all" else status)
    return {"ok": True, "pairs": [{"id": p["id"], "channel": p["channel"], "account": p["account"], "sender": p["sender"], "name": p["name"],
                                   "code": people.shown(p["code"]), "status": p["status"], "inboxId": p["inbox_id"], "person": p["person"],
                                   "createdAt": p["created_at"]} for p in rows]}


@router.post("/api/setup/channels/pairs/{ident}")
async def decide_pair(ident: str, request: Request):
    b = await setup_api.body_of(request)
    action = b.get("action")
    if action not in ("approve", "reject"):
        raise SetupError(400, L("action 只能是 approve 或 reject", "action must be approve or reject"), field="action")
    tier = _choice(b, "tier", people.TIERS)
    agent = b.get("agent")
    if agent is not None and (not isinstance(agent, str) or not accounts.known_agent(agent)):
        raise SetupError(400, L("没有这个 Agent", "No such Agent"), field="agent")
    p, st = cards.decide(ident, action == "approve", tier=tier, agent=agent)
    if st == "missing":
        raise SetupError(404, L("没有这个配对", "No such pairing request"))
    if st in ("expired", "done"):
        raise SetupError(409, L(f"这个配对已经{'过期了' if st == 'expired' else '定过了'}", f"This pairing request {'expired' if st == 'expired' else 'was already settled'}"))
    person = people.person_by_id(p["person"]) if p and p.get("person") else None
    return {"ok": True, "contact": contact_view(person) if person else None}


@router.get("/api/setup/channels/contacts")
async def list_contacts():
    return {"ok": True, "contacts": [contact_view(p) for p in people.people(role="contact")]}


@router.post("/api/setup/channels/contacts")
async def add_contact(request: Request):
    b = await setup_api.body_of(request)
    channel = b.get("channel")
    sender = b.get("sender")
    if channel not in accounts.KINDS or not isinstance(sender, (str, int)) or not str(sender).strip() or len(str(sender)) > 200:
        raise SetupError(400, L("要写 channel 和 sender（渠道上的 id / 邮件地址）", "channel and sender (the id / address on the channel) are required"),
                         field="sender")
    tier = _choice(b, "tier", people.TIERS) or "friend"
    agent = b.get("agent") or ""
    if agent and not accounts.known_agent(agent):
        raise SetupError(400, L("没有这个 Agent", "No such Agent"), field="agent")
    cur = people.person(channel, str(sender))
    if cur and cur["role"] == "owner" and cur["status"] == "active":
        raise SetupError(409, L("这个账号是主人", "That account is the owner"))
    p = people.add_contact(channel, str(sender), name=str(b.get("name") or "")[:80], tier=tier, agent=agent, account=str(b.get("account") or ""),
                           via="manual")
    return {"ok": True, "contact": contact_view(p)}


@router.put("/api/setup/channels/contacts/{pid}")
async def update_contact(pid: str, request: Request):
    b = await setup_api.body_of(request)
    p = people.person_by_id(pid)
    if p is None or p["role"] != "contact":
        raise SetupError(404, L("没有这个联系人", "No such contact"))
    bad = [k for k in b if k not in ("tier", "agent", "status", "name")]
    if bad:
        raise SetupError(400, L(f"不认的字段：{', '.join(sorted(bad))}", f"Unknown fields: {', '.join(sorted(bad))}"), field=sorted(bad)[0])
    if b.get("agent") is not None and not (isinstance(b["agent"], str) and (b["agent"] == "" or accounts.known_agent(b["agent"]))):
        raise SetupError(400, L("没有这个 Agent", "No such Agent"), field="agent")
    if b.get("status") is not None and b["status"] not in ("active", "blocked"):
        raise SetupError(400, L("status 只能是 active 或 blocked", "status must be active or blocked"), field="status")
    try:
        p = people.update_person(pid, tier=_choice(b, "tier", people.TIERS), agent=b.get("agent"), status=b.get("status"),
                                 name=str(b["name"])[:80] if isinstance(b.get("name"), str) else None)
    except ValueError:
        raise SetupError(400, L("改不了", "Can't change that")) from None
    return {"ok": True, "contact": contact_view(p) if p else None}


@router.delete("/api/setup/channels/contacts/{pid}")
async def delete_contact(pid: str):
    p = people.person_by_id(pid)
    if p is None or p["role"] != "contact":
        raise SetupError(404, L("没有这个联系人", "No such contact"))
    people.remove_person(pid)
    return {"ok": True}


# —— 出去的队列 ——————————————————————————————————————————————————————————————————

@router.get("/api/setup/channels/outbox")
async def list_outbox(status: str = "dead", kind: str | None = None, account: str | None = None, limit: int = 100):
    rows = store.outbox(kind, account, None if status == "all" else status, max(1, min(limit, 500)))
    return {"ok": True, "items": [{"id": r["id"], "channel": r["channel"], "account": r["account"], "chat": r["chat"], "why": r["why"],
                                   "status": r["status"], "tries": r["tries"], "error": r["error"], "createdAt": r["created_at"],
                                   "preview": store.outbound(r).text[:120]} for r in rows]}


@router.post("/api/setup/channels/outbox/{oid}/retry")
async def retry_outbox(oid: int):
    r = store.out_row(oid)
    if r is None:
        raise SetupError(404, L("没有这一条", "No such message"))
    if not store.retry_out(oid):
        raise SetupError(409, L("这一条不在死信里", "This message isn't a dead letter"))
    hub.wake_out(r["channel"], r["account"])
    return {"ok": True}


# —— 账号（放在最后：/api/setup/channels/{渠道}/{账号} 别先接走上面 contacts / owners / pairs 的路由）——————————

@router.put("/api/setup/channels/{kind}")
async def put_channel(kind: str, request: Request):
    kind_ok(kind)
    b = await setup_api.body_of(request)
    bad = [k for k in b if k not in ("enabled", "bindings", "limits")]
    if bad:
        raise SetupError(400, L(f"不认的字段：{', '.join(sorted(bad))}", f"Unknown fields: {', '.join(sorted(bad))}"), field=sorted(bad)[0])
    if "bindings" in b:
        bs = b["bindings"]
        if not isinstance(bs, list) or len(bs) > 100 or not all(isinstance(x, dict) and set(x) <= {"account", "chat", "peer", "agent"} and
                                                                 isinstance(x.get("agent"), str) and accounts.known_agent(x["agent"]) for x in bs):
            raise SetupError(400, L("bindings 写成 [{\"account\"?, \"chat\"?, \"peer\"?, \"agent\"}]，agent 要是有的 Agent",
                                    "bindings looks like [{\"account\"?, \"chat\"?, \"peer\"?, \"agent\"}] with an existing agent"), field="bindings")

    def mutate(d: dict) -> None:
        c = setup_api.section(d, "channels", kind)
        if "enabled" in b:
            c["enabled"] = b["enabled"] is True
        if "bindings" in b:
            c["bindings"] = [{k: str(v) for k, v in x.items()} for x in b["bindings"]]
        if "limits" in b and isinstance(b["limits"], dict):
            c["limits"] = b["limits"]
    res = setup_api.edit(mutate)
    for a in accounts.accounts(kind):
        await hub.restart(kind, a.id)
    return {"ok": True, "backup": res["backup"]}


@router.put("/api/setup/channels/{kind}/{account}")
async def put_account(kind: str, account: str, request: Request):
    kind_ok(kind)
    if not ACCOUNT_ID.match(account):
        raise SetupError(400, L("账号 id 只能用小写字母、数字、- _（最多 32 个）", "Account ids use lowercase letters, digits, - and _ (up to 32)"), field="id")
    b = await setup_api.body_of(request)
    state: dict = {}

    def mutate(d: dict) -> None:
        c = setup_api.section(d, "channels", kind)
        accs = _accounts_list(c)
        i = next((n for n, a in enumerate(accs) if isinstance(a, dict) and a.get("id") == account), None)
        cur = accs[i] if i is not None else {}
        new = account_patch(kind, account, b, cur)
        if i is None:
            accs.append(new)
        else:
            accs[i] = new
        state["new"] = new
    res = setup_api.edit(mutate)
    await hub.restart(kind, account)
    acc = accounts.find(kind, account)
    return {"ok": True, "backup": res["backup"], "account": view(acc) if acc else None}


@router.delete("/api/setup/channels/{kind}/{account}")
async def delete_account(kind: str, account: str):
    kind_ok(kind)

    def mutate(d: dict) -> None:
        c = setup_api.section(d, "channels", kind)
        accs = _accounts_list(c)
        c["accounts"] = [a for a in accs if not (isinstance(a, dict) and a.get("id") == account)]
    res = setup_api.edit(mutate)
    await hub.restart(kind, account)
    return {"ok": True, "backup": res["backup"]}


@router.post("/api/setup/channels/{kind}/{account}/enable")
async def enable_account(kind: str, account: str, request: Request):
    kind_ok(kind)
    b = await setup_api.body_of(request)
    on = b.get("enabled") is True
    if accounts.find(kind, account) is None:
        raise SetupError(404, L("没有这个账号", "No such account"))

    def mutate(d: dict) -> None:
        c = setup_api.section(d, "channels", kind)
        for a in _accounts_list(c):
            if isinstance(a, dict) and a.get("id") == account:
                a["enabled"] = on
        if on:
            c["enabled"] = True
    res = setup_api.edit(mutate)
    r = await hub.restart(kind, account)
    return {"ok": True, "backup": res["backup"], "running": r is not None,
            **({} if accounts.builtin() else {"note": L("claw 不是 builtin：渠道归它自己，这里不起", "The claw isn't builtin, so channels don't start here")})}


@router.post("/api/setup/channels/{kind}/{account}/test")
async def test_account(kind: str, account: str):
    kind_ok(kind)
    acc = accounts.find(kind, account)
    if acc is None:
        raise SetupError(404, L("没有这个账号", "No such account"))
    cls = hub.channel_class(kind)
    r = hub.runner(kind, account)
    ch = r.ch if r is not None else cls(acc, None)
    from .base import ChannelError
    try:
        res = await ch.test()
    except ChannelError as e:
        return {"ok": False, "error": str(e), "kind": e.kind or None}
    finally:
        if r is None:
            try:
                await ch.close()
            except Exception:  # noqa: BLE001
                pass
    return {"ok": True, **res}
