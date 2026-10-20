"""webhook 触发（第 4 批，7.10）：别的服务往 /webhooks/<名字> POST 一下 → 验签、限速、过滤、去重 → 回 202，后台照这个 webhook 的设置做
（engine/triggers.deliver：零工具读一遍出通知 / 出卡；签名对上、trust: owner 的能直接叫醒 Agent、叫起一个定时任务）。只在 claw 是 builtin 时收。

不在 /api 下面（main.py 的 guard 不认令牌）：每个 webhook 自己验——
  github  X-Hub-Signature-256 = "sha256=" + HMAC-SHA256(secret, 请求体)；事件 X-GitHub-Event、投递 id X-GitHub-Delivery
          （投递 id 不在签名里：同一个请求体再来也当重复的，换了 id 重放的认得出）
  hmac    X-Webhook-Timestamp（Unix 秒）+ X-Webhook-Signature（也认 X-Webhook-Signature-V2）= "sha256=" 可有可无 +
          HMAC-SHA256(secret, "<时间>.<请求体>") 的 16 进制；时间差 WINDOW 秒以上不收（防重放）
  bearer  Authorization: Bearer <secret>（家里的设备、发不了签名的小工具）：不能配 trust: owner（没签名就不算主人自己的东西）
配置（server.json engine.triggers.webhooks.<名字>，名字是小写字母、数字、- _；默认没有 = 关）：
  verify（上面三种之一，必写）· secret（必写，只能写成 ${变量名}，值放 secrets.env：models.env_value 读，engine/mcp_client.py 的写法）
  · agent（谁的事，默认 main）· thread（发进哪个对话，默认那个 Agent 自己的）· label（对话里、卡上叫什么，默认名字）
  · events（只要这几种事件，不写 = 都要）· filters（照 Hermes 的写法：[{"field": "action", "in": ["opened"]}]，all / any / not、
    exists / equals / not_equals / contains / in / regex，field 用点连：pull_request.draft、event）
  · coalesce（{"key": "pull_request.number" 或 "{repository.full_name}#{number}", "window": 30, "max_wait": 300}：同一件事接连来的几下
    只处理最后一下：安静 window 秒、最多等 max_wait 秒）
  · action：notify（默认：零工具读一遍、发一条通知）/ card（读一遍、出一张收件箱卡，点了才让 Agent 动手）/ agent（直接叫醒它）/
    job（直接叫起定时任务 job）；agent、job 只有 trust: owner 而且 verify 是 github / hmac 的才行
  · say（零工具那一读照着写通知的话：「只说要我 review 的 PR」）· task（让 Agent 动手时做什么）· job（job 的 id）
  · level（ring / quiet / none，默认 quiet）· limit（一小时最多收几件，默认 LIMIT）· max_bytes（请求体最大，默认 MAX_BYTES）
  · public（true 才挂到对外的小服务 ../public.py：Funnel 指过去的那个；不写只在主服务上，自己的设备、Tailscale 才够得着）
回什么：没有这个名字、没配好、不是 builtin → 404；验不过 → 401（都不说为什么）；太大 → 413；一小时超过 limit 件 → 429；急停 → 503 +
  Retry-After；收下了 → 202（后台做）；同一个投递再来 → 200 duplicate（上次没做成的再做一次）；不要的事件、被过滤掉的 → 200 ignored。
不会自己开 Funnel、开端口：要公网就自己把 Funnel / 反向代理指到 share.public_port（README 写了）。
接口（只认主人的设备）：GET /api/engine/triggers（engine/triggers.py：有哪些、最近的事件），PUT / DELETE /api/engine/triggers/webhooks/<名字>
  （写 server.json，先备份；secretValue 给了就存进 secrets.env）。
过滤（filter_matches、_resolve）和合并（_enqueue、_fire）改写自 Hermes 的 gateway/platforms/webhook_filters.py、webhook_coalesce.py，
NousResearch/hermes-agent commit f4ac0bea，MIT，Copyright (c) 2025 Nous Research；许可全文见仓库根目录的 THIRD_PARTY_NOTICES。
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import re
import time
import urllib.parse
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from i18n import L

from . import estop, models, triggers

NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")
VAR = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")
VERIFY = ("github", "hmac", "bearer")
SIGNED = ("github", "hmac")
WINDOW = 300                               # hmac：时间差多少秒以内收（Hermes 的 _V2_REPLAY_WINDOW_SECONDS）
MAX_BYTES, MAX_BYTES_CAP = 1 << 20, 10 << 20
LIMIT, LIMIT_CAP = 60, 1000
COALESCE_WINDOW, COALESCE_MAX = 30.0, 300.0
RETRY_LATER = 600                          # 429 的 Retry-After（秒）
_MISSING = object()
_pending: dict[str, dict] = {}             # 合并着的：组 → {"eid", "spec", "content", "count", "first", "timer", "merged"}（只在服务的事件循环里动）


def entries() -> dict:
    v = triggers.cfg().get("webhooks")
    return v if isinstance(v, dict) else {}


def _secret_var(v: Any) -> str | None:
    m = VAR.match(str(v or "").strip())
    return m.group(1) if m else None


def clean(name: str, e: Any) -> dict:
    """配置里的一项 → {"name", "problem"（配不对的话一句；有它就不收）, 其余整理好的字段}。不抛：配错的照样列出来，app 看得见哪里不对。"""
    import bridges
    h: dict[str, Any] = {"name": name, "problem": None}
    problems: list[str] = []
    if not NAME.match(name):
        problems.append(L("名字只能是小写字母、数字、- 和 _", "The name can only have lowercase letters, digits, - and _"))
    if not isinstance(e, dict):
        return {**h, "problems": [L("要写成一个对象", "It must be an object")], "problem": L("要写成一个对象", "It must be an object")}
    h["verify"] = str(e.get("verify") or "").strip().lower()
    if h["verify"] not in VERIFY:
        problems.append(L("verify 写 github / hmac / bearer", "verify must be github, hmac or bearer"))
    h["var"] = _secret_var(e.get("secret"))
    h["secret"] = models.env_value(h["var"]) if h["var"] else ""
    unset = None
    if not h["var"]:
        problems.append(L("secret 只能写成 ${变量名}，值放进 secrets.env（或者 PUT 时给 secretValue）",
                          "secret must be written as ${VARIABLE}, with the value in secrets.env (or given as secretValue when saving)"))
    elif not h["secret"].strip():
        unset = L(f"还没有 {h['var']} 这个密钥的值", f"{h['var']} has no value yet")
    h["agent"] = str(e.get("agent") or "main").strip()
    if not bridges.known_agent(h["agent"]):
        problems.append(L(f"没有「{h['agent']}」这个 Agent", f"No Agent called {h['agent']}"))
    h["thread"] = str(e.get("thread") or "").strip() or None
    h["label"] = " ".join(str(e.get("label") or name).split())[:40] or name
    h["action"] = str(e.get("action") or "notify").strip().lower()
    h["trust"] = "owner" if e.get("trust") == "owner" else None
    if h["action"] not in triggers.ACTIONS:
        problems.append(L("action 写 notify / card / agent / job", "action must be notify, card, agent or job"))
    elif h["action"] in triggers.OWNER_ONLY and (h["trust"] != "owner" or h["verify"] not in SIGNED):
        problems.append(L("action agent / job 只给签名对上的（verify github / hmac）、写了 trust: owner 的：外面来的东西不直接叫醒 Agent",
                          "action agent / job needs a signed hook (verify github / hmac) with trust: owner: things from outside never wake "
                          "an Agent directly"))
    h["job"] = str(e.get("job") or "").strip() or None
    if h["action"] == "job" and not h["job"]:
        problems.append(L("action job 要写 job（定时任务的 id）", "action job needs job (a scheduled job's id)"))
    h["say"] = str(e.get("say") or "").strip()[:1000] or None
    h["task"] = str(e.get("task") or "").strip()[:2000] or None
    h["level"] = e.get("level") if e.get("level") in triggers.LEVELS else "quiet"
    ev = e.get("events")
    h["events"] = [str(x) for x in ev if isinstance(x, str)] if isinstance(ev, list) else None
    f = e.get("filters")
    h["filters"] = f if isinstance(f, (list, dict)) and f else None
    if f not in (None, [], {}) and not isinstance(f, (list, dict)):
        problems.append(L("filters 写成一个列表或对象", "filters must be a list or an object"))
    c = e.get("coalesce")
    h["coalesce"] = None
    if c is not None:
        key = c.get("key") if isinstance(c, dict) else None
        if not isinstance(key, str) or not key.strip():
            problems.append(L("coalesce 要写 key（比如 pull_request.number）", "coalesce needs a key (e.g. pull_request.number)"))
        else:
            h["coalesce"] = {"key": key.strip(), "window": _num(c.get("window", c.get("window_seconds")), COALESCE_WINDOW, 3600),
                             "max_wait": _num(c.get("max_wait", c.get("max_wait_seconds")), COALESCE_MAX, 86400)}
    h["limit"] = int(_num(e.get("limit"), LIMIT, LIMIT_CAP))
    h["max_bytes"] = int(_num(e.get("max_bytes"), MAX_BYTES, MAX_BYTES_CAP))
    h["public"] = e.get("public") is True
    h["problems"] = problems  # 写不对的（PUT 时不存）；unset：只是还没有密钥的值（照存，补上就能用）
    h["problem"] = L("；", "; ").join(problems + ([unset] if unset else [])) or None
    return h


def _num(v: Any, default: float, cap: float) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0:
        return default
    return float(min(v, cap))


def hook(name: str) -> dict | None:
    """这个名字的 webhook（整理好的）；没有 → None。"""
    e = entries().get(name)
    return None if e is None else clean(name, e)


def view(h: dict) -> dict:
    """给 app 看的样子：不带密钥的值，只说变量名、有没有值。"""
    return {"name": h["name"], "path": f"/webhooks/{h['name']}", "verify": h.get("verify"), "secret": {"var": h.get("var"), "set": bool(h.get("secret"))},
            "agent": h.get("agent"), "thread": h.get("thread"), "label": h.get("label"), "action": h.get("action"), "trust": h.get("trust"),
            "job": h.get("job"), "say": h.get("say"), "task": h.get("task"), "level": h.get("level"), "events": h.get("events"),
            "filters": h.get("filters"), "coalesce": h.get("coalesce"), "limit": h.get("limit"), "maxBytes": h.get("max_bytes"),
            "public": h.get("public"), "problem": h.get("problem")}


def listing() -> list[dict]:
    return [view(clean(str(k), v)) for k, v in entries().items()]


triggers.LISTS["webhooks"] = listing


# —— 收 ——————————————————————————————————————————————————————————————————————

def _eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf8", "replace"), b.encode("utf8", "replace"))


def verify(h: dict, headers: Any, body: bytes, now: float | None = None) -> tuple[bool, str | None]:
    """验这一下 → (对不对, 去重用的指纹)。github：请求体的指纹；hmac：签名本身（时间 + 请求体）；bearer：没有。"""
    secret = h.get("secret") or ""
    if not secret.strip():
        return False, None
    if h["verify"] == "github":
        got = headers.get("x-hub-signature-256") or ""
        want = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return (True, hashlib.sha256(body).hexdigest()) if got and _eq(got, want) else (False, None)
    if h["verify"] == "hmac":
        ts = (headers.get("x-webhook-timestamp") or "").strip()
        sig = (headers.get("x-webhook-signature") or headers.get("x-webhook-signature-v2") or "").strip()
        if not ts or not sig:
            return False, None
        try:
            if abs((now or time.time()) - int(ts)) > WINDOW:
                return False, None
        except ValueError:
            return False, None
        want = hmac.new(secret.encode(), ts.encode() + b"." + body, hashlib.sha256).hexdigest()
        sig = sig[7:] if sig.lower().startswith("sha256=") else sig
        return (True, want) if _eq(sig.lower(), want) else (False, None)
    if h["verify"] == "bearer":
        auth = headers.get("authorization") or ""
        tok = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        return (bool(tok) and _eq(tok, secret.strip())), None
    return False, None


async def _body(request: Request, cap: int) -> bytes | None:
    """读请求体，超过 cap 字节 → None（先看 Content-Length，再边读边数：分块传的、谎报长度的也挡得住）。"""
    try:
        if int(request.headers.get("content-length") or 0) > cap:
            return None
    except ValueError:
        return None
    buf = bytearray()
    async for chunk in request.stream():
        buf += chunk
        if len(buf) > cap:
            return None
    return bytes(buf)


def parse(body: bytes, ctype: str) -> Any:
    """JSON；不是就按表单读；都不是就当一段字。"""
    text = body.decode("utf8", "replace")
    try:
        return json.loads(text) if text.strip() else {}
    except ValueError:
        pass
    if "form" in ctype.lower() or ("=" in text and "\n" not in text.strip()):
        try:
            form = dict(urllib.parse.parse_qsl(text, keep_blank_values=True, strict_parsing=True))
        except ValueError:
            form = None
        if form is not None:
            if set(form) == {"payload"}:  # GitHub 选了表单格式：payload=<JSON>
                try:
                    return json.loads(form["payload"])
                except ValueError:
                    pass
            return form
    return {"text": text}


def event_of(headers: Any, payload: Any) -> str:
    ev = headers.get("x-github-event") or headers.get("x-gitlab-event") or headers.get("x-event-type") or ""
    if not ev and isinstance(payload, dict):
        ev = payload.get("event") or payload.get("event_type") or payload.get("type") or ""
    return str(ev if isinstance(ev, (str, int)) else "")[:80]


def delivery_of(headers: Any) -> str | None:
    for k in ("x-github-delivery", "x-gitlab-event-uuid", "webhook-id", "x-delivery-id", "x-request-id"):
        if v := (headers.get(k) or "").strip():
            return v[:200]
    return None


def slim(v: Any, depth: int = 0) -> Any:
    """交给模型之前去掉没用的（一串串 API 地址、头像、node_id）：GitHub 的请求体能小一大半。"""
    if depth > 8:
        return "…"
    if isinstance(v, dict):
        web = "html_url" in v  # 有给人看的网页地址：那个 url 是 API 的，不要
        return {k: slim(x, depth + 1) for k, x in v.items()
                if not ((k.endswith("_url") and k != "html_url") or (k == "url" and web) or k in ("node_id", "gravatar_id", "_links"))}
    if isinstance(v, list):
        return [slim(x, depth + 1) for x in v[:20]] + ([f"… {len(v) - 20} more"] if len(v) > 20 else [])
    return v


def content_of(event: str, payload: Any) -> str:
    body = payload["text"] if isinstance(payload, dict) and set(payload) == {"text"} else json.dumps(slim(payload), ensure_ascii=False, indent=1)
    return triggers.clip((f"event: {event}\n" if event else "") + body, triggers.STORE_MAX)


# —— 过滤（照 Hermes 的 webhook_filters.py）——————————————————————————————————————————————————

def _resolve(field: Any, payload: Any, event: str) -> Any:
    """点连着的 field → 值；没有 → _MISSING。开头是 event / payload 的从那里起，不然从请求体起。"""
    if not isinstance(field, str) or not field.strip():
        return _MISSING
    parts = [p for p in field.strip().split(".") if p]
    if not parts:
        return _MISSING
    if parts[0] in ("event", "event_type"):
        return event if len(parts) == 1 else _MISSING
    value: Any = payload
    if parts[0] == "payload":
        parts.pop(0)
    for p in parts:
        if isinstance(value, dict):
            value = value.get(p, _MISSING)
        elif isinstance(value, list) and p.isdigit():
            i = int(p)
            value = value[i] if 0 <= i < len(value) else _MISSING
        else:
            return _MISSING
        if value is _MISSING:
            return _MISSING
    return value


def _text(v: Any) -> str:
    return "" if v is _MISSING else json.dumps(v, sort_keys=True) if isinstance(v, (dict, list)) else str(v)


def _regex(v: Any, pattern: Any) -> bool:
    if v is _MISSING:
        return False
    try:
        return re.search(str(pattern), _text(v)) is not None
    except re.error:
        return False


OPS = (("exists", lambda v, a: (v is not _MISSING) is bool(a)),
       ("equals", lambda v, a: v is not _MISSING and v == a),
       ("not_equals", lambda v, a: v is _MISSING or v != a),
       ("contains", lambda v, a: v is not _MISSING and (a in v if isinstance(v, (list, dict)) else str(a) in _text(v))),
       ("in", lambda v, a: isinstance(a, list) and v in a),
       ("regex", _regex))


def filter_matches(spec: Any, payload: Any, event: str) -> bool:
    """一条过滤（all / any / not 套着，最里面是 field + 一个比较）。写不对的当不过（宁可少收）。"""
    if not isinstance(spec, dict):
        return False
    for key, combine in (("all", all), ("any", any)):
        if key in spec:
            items = spec.get(key)
            return isinstance(items, list) and combine(filter_matches(x, payload, event) for x in items)
    if "not" in spec:
        return not filter_matches(spec.get("not"), payload, event)
    v = _resolve(spec.get("field"), payload, event)
    if "exists" not in spec and spec.get("missing") is True:
        return v is _MISSING
    for key, pred in OPS:
        if key in spec:
            return bool(pred(v, spec.get(key)))
    return False


def filters_match(filters: Any, payload: Any, event: str) -> bool:
    if not filters:
        return True
    if isinstance(filters, dict):
        return filter_matches(filters, payload, event)
    return all(filter_matches(f, payload, event) for f in filters)


# —— 合并（照 Hermes 的 webhook_coalesce.py）——————————————————————————————————————————————————

PLACEHOLDER = re.compile(r"\{([a-zA-Z0-9_.]+)\}")


def group_key(key: str, payload: Any, event: str) -> str | None:
    """「pull_request.number」或「{repository.full_name}#{number}」→ 这一件属于哪一组；有一处取不到 → None（不合并，马上做）。"""
    template = key if "{" in key else "{" + key + "}"
    missing = False

    def sub(m: re.Match) -> str:
        nonlocal missing
        v = _resolve(m.group(1), payload, event)
        if v is _MISSING or isinstance(v, (dict, list)):
            missing = True
            return ""
        return str(v)
    out = PLACEHOLDER.sub(sub, template)
    return None if missing else out[:200]


async def _enqueue(group: str, eid: str, spec: dict, content: str, c: dict) -> None:
    """这一组又来了一件：前一件记成 merged，计时从现在重新算（最多等到这一组第一件之后 max_wait 秒）。"""
    now = time.monotonic()
    old = _pending.get(group)
    if old is not None:
        if old["timer"] is not None and not old["timer"].done():
            old["timer"].cancel()
        triggers.set_status(old["eid"], "merged", grp=group)
    first = old["first"] if old else now
    entry = {"eid": eid, "spec": spec, "content": content, "count": (old["count"] + 1) if old else 1, "first": first, "timer": None}
    _pending[group] = entry
    delay = min(c["window"], max(0.0, first + c["max_wait"] - now))
    entry["timer"] = asyncio.ensure_future(_fire(group, entry, delay))


async def _fire(group: str, entry: dict, delay: float) -> None:
    try:
        if delay > 0:
            await asyncio.sleep(delay)
    except asyncio.CancelledError:
        return  # 后来的那一件接着等
    if _pending.get(group) is not entry:
        return
    _pending.pop(group, None)
    content = entry["content"]
    if entry["count"] > 1:
        content += L(f"\n\n（同一件事接连来了 {entry['count']} 下，合成了这一下：这是最新的那一下）",
                     f"\n\n({entry['count']} events for the same thing came in quick succession; this is the latest one)")
    await triggers.deliver(entry["eid"], entry["spec"], content)


async def handle(eid: str, h: dict, spec: dict, payload: Any, content: str) -> str | None:
    """收下的一件（在服务的事件循环里）：要合并的进组，别的马上照设置做。"""
    c = h.get("coalesce")
    if c and (g := group_key(c["key"], payload, spec.get("event") or "")) is not None:
        await _enqueue(f"{h['name']}|{g}", eid, spec, content, c)
        return "queued"
    return await triggers.deliver(eid, spec, content)


async def settle(timeout: float = 30) -> None:
    """等合并着的都到点、做完（测试用）。"""
    for _ in range(50):
        timers = [e["timer"] for e in list(_pending.values()) if e.get("timer") is not None and not e["timer"].done()]
        if not timers:
            break
        await asyncio.wait(timers, timeout=timeout)
    await triggers.settle(timeout)


def _no(status: int, **headers: str) -> JSONResponse:
    return JSONResponse({"ok": False}, status_code=status, headers=headers or None)


async def receive(request: Request, name: str, *, public: bool = False) -> JSONResponse:
    """一下 POST（见文件开头「回什么」）。public：从对外的小服务进来的（只认 public: true 的）。"""
    import claw
    h = hook(name) if claw.is_builtin() else None
    if h is None or h["problem"] or (public and not h["public"]):
        return _no(404)
    body = await _body(request, h["max_bytes"])
    if body is None:
        return _no(413)
    ok, digest = verify(h, request.headers, body)
    if not ok:
        return _no(401)
    if estop.paused():
        return JSONResponse({"ok": False, "paused": True}, status_code=503, headers={"Retry-After": str(estop.RETRY_AFTER)})
    source = f"webhook:{name}"
    if triggers.hits(source) >= h["limit"]:
        return _no(429, **{"Retry-After": str(RETRY_LATER)})
    payload = parse(body, request.headers.get("content-type") or "")
    event = event_of(request.headers, payload)
    if (h["events"] and event not in h["events"]) or not filters_match(h["filters"], payload, event):
        triggers.record(source, "webhook", event=event, status="ignored",
                        summary=L(f"不要的事件：{event}", f"event not wanted: {event}") if h["events"] and event not in h["events"] else
                        L("被 filters 过滤掉了", "filtered out"))
        return JSONResponse({"ok": True, "ignored": True})
    spec = {"source": source, "kind": "webhook", "agent": h["agent"], "thread": h["thread"], "action": h["action"], "say": h["say"],
            "task": h["task"], "job": h["job"], "level": h["level"], "label": h["label"], "trust": h["trust"], "event": event}
    content = content_of(event, payload)
    delivery = delivery_of(request.headers)
    eid = triggers.record(source, "webhook", delivery=delivery, digest=digest, event=event, data={"spec": spec, "content": content})
    if eid is None:  # 同一个投递再来：上次没做成的（出错、花费到了上限）再做一次，别的就是重复的
        again = triggers.again(source, delivery, digest)
        if again is None:
            return JSONResponse({"ok": True, "duplicate": True})
        eid = again
    triggers.submit(handle(eid, h, spec, payload, content))
    return JSONResponse({"ok": True, "id": eid}, status_code=202)


router = APIRouter()           # 主服务：/webhooks/<名字>（不在 /api 下，不认令牌，自己验）
public_router = APIRouter()    # 对外的小服务（../public.py）：只认 public: true 的


@router.post("/webhooks/{name}")
async def api_receive(name: str, request: Request):
    return await receive(request, name)


@public_router.post("/webhooks/{name}")
async def public_receive(name: str, request: Request):
    return await receive(request, name, public=True)


# —— 改配置（只认主人的设备；写 server.json 之前先备份）——————————————————————————————————————————

def _owner(request: Request) -> str:
    from . import setup_api
    return setup_api.owner(request)


api = APIRouter(dependencies=[Depends(_owner)])
FIELDS = ("verify", "secret", "agent", "thread", "label", "events", "filters", "coalesce", "action", "say", "task", "job", "level", "trust",
          "limit", "max_bytes", "public")


@api.put("/api/engine/triggers/webhooks/{name}")
async def api_put(name: str, request: Request):
    """整项换上（{verify, secret: "${变量名}", …}；见文件开头）。secretValue 给了就先存进 secrets.env（变量名照 secret 里写的）。
    写不对的回 400 说哪里不对；还差密钥的值这种照存，回的 webhook.problem 写着。"""
    import secretstore

    from . import setup_api
    from .setup_api import SetupError
    if not NAME.match(name):
        raise SetupError(400, L("名字只能是小写字母、数字、- 和 _", "The name can only have lowercase letters, digits, - and _"))
    b = await setup_api.body_of(request)
    unknown = set(b) - set(FIELDS) - {"secretValue"}
    if unknown:
        raise SetupError(400, L(f"不认识的键：{'、'.join(sorted(unknown))}", f"Unknown keys: {', '.join(sorted(unknown))}"))
    entry = {k: b[k] for k in FIELDS if k in b and b[k] is not None}
    var = _secret_var(entry.get("secret"))
    if var is None:
        raise SetupError(400, L("secret 只能写成 ${变量名}（值放进 secrets.env，或者一起给 secretValue）",
                                "secret must be written as ${VARIABLE} (the value goes in secrets.env, or give it as secretValue)"), field="secret")
    if b.get("secretValue") not in (None, ""):
        try:
            secretstore.put(var, str(b["secretValue"]))
        except ValueError as e:
            raise SetupError(400, str(e), field="secretValue") from None
    h = clean(name, entry)
    if h["problems"]:  # 差密钥的值照存（以后补上就能用），别的写不对不存
        raise SetupError(400, L("；", "; ").join(h["problems"]))

    def mutate(d: dict) -> None:
        setup_api.section(d, "engine", "triggers", "webhooks")[name] = entry
    res = setup_api.edit(mutate)
    return {"ok": True, "webhook": view(hook(name) or h), "changed": res["changed"], "backup": res["backup"]}


@api.delete("/api/engine/triggers/webhooks/{name}")
async def api_delete(name: str):
    """去掉这一项（secrets.env 里的值不动：别的地方可能也用它）。"""
    from . import setup_api

    def mutate(d: dict) -> None:
        setup_api.section(d, "engine", "triggers", "webhooks").pop(name, None)
        setup_api.prune(d, "engine", "triggers", "webhooks")
    res = setup_api.edit(mutate)
    return {"ok": True, "changed": res["changed"], "backup": res["backup"]}
