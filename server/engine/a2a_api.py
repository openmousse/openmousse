"""主人自己的 A2A（第 7 批，2026-10-20）：主人自己别处的 agent（他自己的别家 claw、自己写的 A2A 客户端）照 A2A 1.0（JSON-RPC 绑定）
跟这里的 Agent 说话。和 server/a2a.py（第 6 批：朋友、陌生人经名片 agent 来问，外人回合）是两回事：这里只认主人自己的 API 令牌
（api、api-<agent id>，同 /v1/chat/completions），一轮就是对外接口的主人回合（engine/openai_api.py 的 Run：origin api，不花套餐，
工具和关卡同 app 里，撤不回的照样进收件箱等他点头）。
开关：engine.api 开着（/v1 挂着），而且 engine.api.a2a = true；不然这几条一律 404。
  GET  /v1/a2a                            这把令牌能用的 Agent 和各自的名片地址
  GET  /v1/a2a/{agent}/agent-card.json    这个 Agent 的 A2A 名片（要令牌：名片里有 Agent 的名字）。接口地址 = 这次请求的根 + /v1/a2a/{agent}
                                          （在反向代理后面照 X-Forwarded-Proto / -Host）
  POST /v1/a2a/{agent}                    JSON-RPC 2.0：SendMessage、GetTask、ListTasks、CancelTask。SendStreamingMessage、SubscribeToTask、
                                          推送设置那几个、GetExtendedAgentCard → UnsupportedOperation；0.3 的方法名（message/send……）→
                                          VersionNotSupported；请求头 A2A-Version 写了就要是 1.0（没写照 1.0 答，同 server/a2a.py）
一条消息（SendMessage）= 一轮：
  message.parts：text 是这一句；raw 的图（mediaType image/png、jpeg、webp、gif，解出来最多 IMAGE_MAX）当图；url、data、别的 → ContentTypeNotSupported。
  contextId = 会话（没给就新开一个，回的 contextId 带上就接得上）：对话 = openai_api.conversation(令牌, Agent, "a2a:" + contextId)，
    和 /v1 的会话一样记在 engine_api_sessions（ACP 的 session/list 也看得到，键是 a2a:<contextId>）。
  taskId 给了：要是这把令牌、这个 Agent 的、停在 INPUT_REQUIRED / AUTH_REQUIRED 的任务，这一句接着记在它下面；结束了的 → UnsupportedOperation
    （下一句带 contextId、不带 taskId）；还在跑的 → UnsupportedOperation。
  同一把令牌发同一个 messageId 两次 → 回第一次的那个任务（A2A 的幂等）。
  configuration：acceptedOutputModes 写了却没有 text/plain（或 text/*、*/*）→ ContentTypeNotSupported；historyLength 限回的记录条数；
    returnImmediately = true：建好任务马上回（TASK_STATE_WORKING），之后 GetTask 来问；默认等这一轮跑完再回。
  回：总是一个任务（{"task": …}）。跑完 = TASK_STATE_COMPLETED：status.message 和 artifacts（一份 reply）里是这一轮说的话；
    还有卡在等主人点头 = TASK_STATE_AUTH_REQUIRED（A2A 里「等人来批准」的中断态），字后面写明哪张卡、去 app 的收件箱点；之后 GetTask
    看到卡都有了结果就变成 COMPLETED（状态消息里说点了什么；结果另外照 /v1 的规矩攒着，同一个 contextId 的下一句前面带上）。
    出错 = TASK_STATE_FAILED（状态消息里是原因）；CancelTask、关服务停下的 = TASK_STATE_CANCELED。metadata.openmousse：
    {"session", "turn", "status", "pending", "usage"}。
  任务存在 mousse.db 的 engine_a2a_tasks（用到时才建），每把令牌最多 KEEP 个，多了删最旧的；重启时还在跑的，下次问到记成 FAILED。
CancelTask：在跑的停下（同 app 里点停），等点头的卡撤回；结束了的 → TaskNotCancelable。
参考：A2A 1.0（a2a-protocol.org 的规范、a2aproject/A2A 的 specification/a2a.proto，10/20 核对：名片、SendMessageConfiguration、TaskState、
SecurityScheme 的写法）；server/a2a.py 的 JSON-RPC 和错误码的样子。只借做法，没拿代码。
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import json
import re
import uuid
from datetime import datetime, timezone

from fastapi import Request
from fastapi.responses import JSONResponse

from config import settings
from i18n import L

from . import openai_api as oa
from . import store

VERSION = "1.0"
ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,100}$")
TEXT_MAX = 100_000
IMAGE_MAX = 10 * 1024 * 1024
IMAGE_TYPES = ("image/png", "image/jpeg", "image/webp", "image/gif")
TEXT_MODES = ("text/plain", "text/*", "*/*")
KEEP = 200                  # 每把令牌最多存多少个任务
HISTORY_MAX = 100           # 一个任务的记录最多留多少条
TERMINAL = ("TASK_STATE_COMPLETED", "TASK_STATE_FAILED", "TASK_STATE_CANCELED", "TASK_STATE_REJECTED")
INTERRUPTED = ("TASK_STATE_INPUT_REQUIRED", "TASK_STATE_AUTH_REQUIRED")
METHODS_03 = {"message/send", "message/stream", "tasks/get", "tasks/list", "tasks/cancel", "tasks/resubscribe",
              "tasks/pushNotificationConfig/set", "tasks/pushNotificationConfig/get", "tasks/pushNotificationConfig/list",
              "tasks/pushNotificationConfig/delete", "agent/getAuthenticatedExtendedCard"}
UNSUPPORTED = {"SendStreamingMessage", "SubscribeToTask", "GetExtendedAgentCard", "CreateTaskPushNotificationConfig",
               "GetTaskPushNotificationConfig", "ListTaskPushNotificationConfigs", "DeleteTaskPushNotificationConfig"}
# A2A 的错误码（同 server/a2a.py）
E_PARSE, E_REQUEST, E_METHOD, E_PARAMS, E_INTERNAL = -32700, -32600, -32601, -32602, -32603
E_NOT_FOUND, E_NOT_CANCELABLE, E_UNSUPPORTED, E_CONTENT, E_VERSION = -32001, -32002, -32004, -32005, -32009
REASONS = {E_NOT_FOUND: "TASK_NOT_FOUND", E_NOT_CANCELABLE: "TASK_NOT_CANCELABLE", E_UNSUPPORTED: "UNSUPPORTED_OPERATION",
           E_CONTENT: "CONTENT_TYPE_NOT_SUPPORTED", E_VERSION: "VERSION_NOT_SUPPORTED"}
SCHEMA = """
CREATE TABLE IF NOT EXISTS engine_a2a_tasks (
  id TEXT PRIMARY KEY, token TEXT NOT NULL, agent TEXT NOT NULL, context_id TEXT NOT NULL, message_id TEXT NOT NULL, conversation TEXT NOT NULL,
  state TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS engine_a2a_tasks_msg ON engine_a2a_tasks(token, message_id);
CREATE INDEX IF NOT EXISTS engine_a2a_tasks_token ON engine_a2a_tasks(token, agent, updated_at);
"""
_ready: set[str] = set()
RUNNING: dict[str, oa.Run] = {}        # 任务 id → 还在跑的那一轮
_bg: set[asyncio.Task] = set()


class RpcError(Exception):
    def __init__(self, code: int, message: str, **meta) -> None:
        super().__init__(message)
        self.code, self.message, self.meta = code, message, meta


def on() -> bool:
    return (oa.cfg() or {}).get("a2a") is True


def gate() -> None:
    if not on():
        raise oa.ApiError(404, L("没开：server.json 里写 engine.api.a2a = true", "Not enabled: set engine.api.a2a to true in server.json"),
                          "invalid_request_error", None, "not_found")


def ts() -> str:
    """A2A 的时间：UTC、毫秒、Z 结尾。"""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def rpc_ok(id_, result) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": id_, "result": result}, headers={"Cache-Control": "no-store"})


def rpc_err(id_, code: int, message: str, **meta) -> JSONResponse:
    err: dict = {"code": code, "message": message}
    if code in REASONS:
        err["data"] = [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": REASONS[code], "domain": "a2a-protocol.org",
                        "metadata": {k: str(v) for k, v in {**meta, "timestamp": ts()}.items()}}]
    return JSONResponse({"jsonrpc": "2.0", "id": id_, "error": err}, headers={"Cache-Control": "no-store"})


# —— 名片 ——————————————————————————————————————————————————————————————————————

def base_url(request: Request) -> str:
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme or "http").split(",")[0].strip()
    host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc).split(",")[0].strip()
    return f"{proto}://{host}"


def card(agent: str, name: str, base: str) -> dict:
    """这个 Agent 的 A2A 名片（a2a.proto 的 AgentCard，proto3 的 JSON 写法；不放空值）。"""
    return {
        "name": name,
        "description": L(f"{settings.app_name} 里的「{name}」：你自己的 Agent，用它自己的工具；撤不回的事先到你的收件箱等你点头。",
                         f"{name} in {settings.app_name}: your own Agent, with its own tools; anything that can't be undone waits in your "
                         "inbox for your OK."),
        "supportedInterfaces": [{"url": f"{base}/v1/a2a/{agent}", "protocolBinding": "JSONRPC", "protocolVersion": VERSION}],
        "provider": {"organization": "OpenMousse", "url": "https://openmousse.ai"},
        "version": "1.0.0",
        "capabilities": {"streaming": False, "pushNotifications": False},
        "securitySchemes": {"bearer": {"httpAuthSecurityScheme": {"scheme": "Bearer", "description": L(
            "server.json 的 auth.tokens 里叫 api 或 api-<agent id> 的那把令牌", "The token named api or api-<agent id> under auth.tokens in server.json")}}},
        "securityRequirements": [{"schemes": {"bearer": {"list": []}}}],
        "defaultInputModes": ["text/plain", *IMAGE_TYPES],
        "defaultOutputModes": ["text/plain"],
        "skills": [{"id": "chat", "name": name, "description": L("和这个 Agent 说一句，它照在 app 里一样做事、回话。",
                                                                 "Say something to this Agent; it works and answers as it does in the app."),
                    "tags": ["assistant", "personal"]}],
    }


# —— 消息 ——————————————————————————————————————————————————————————————————————

def read_message(params: dict) -> tuple[dict, str | list]:
    """SendMessageRequest.message → (消息, 这一句：字或者 openai_api 的块)。见文件开头。"""
    m = params.get("message")
    if not isinstance(m, dict):
        raise RpcError(E_PARAMS, "message is required")
    if not isinstance(m.get("messageId"), str) or not ID_RE.match(m["messageId"]):
        raise RpcError(E_PARAMS, "message.messageId is required (letters, digits, _ . : -; at most 100)")
    if m.get("role") not in ("ROLE_USER", "user"):
        raise RpcError(E_PARAMS, "message.role must be ROLE_USER")
    for k in ("taskId", "contextId"):
        if m.get(k) is not None and not (isinstance(m[k], str) and ID_RE.match(m[k])):
            raise RpcError(E_PARAMS, f"message.{k} is not valid")
    parts = m.get("parts")
    if not isinstance(parts, list) or not parts:
        raise RpcError(E_PARAMS, "message.parts must not be empty")
    blocks: list[dict] = []
    for i, p in enumerate(parts):
        if not isinstance(p, dict):
            raise RpcError(E_PARAMS, f"message.parts[{i}] must be an object")
        if isinstance(p.get("text"), str):
            if p["text"].strip():
                blocks.append({"type": "text", "text": p["text"]})
            continue
        mt = str(p.get("mediaType") or "").lower()
        if isinstance(p.get("raw"), str) and mt in IMAGE_TYPES:
            try:
                n = len(base64.b64decode(p["raw"], validate=True))
            except (binascii.Error, ValueError):
                raise RpcError(E_PARAMS, f"message.parts[{i}].raw is not valid base64") from None
            if n > IMAGE_MAX:
                raise RpcError(E_PARAMS, f"message.parts[{i}] is too large (at most {IMAGE_MAX // (1024 * 1024)} MB)")
            blocks.append({"type": "image_url", "image_url": {"url": f"data:{mt};base64,{p['raw']}"}})
            continue
        raise RpcError(E_CONTENT, f"message.parts[{i}]: only text parts and raw images ({', '.join(IMAGE_TYPES)}) are supported",
                       mediaType=mt or "-")
    if not blocks:
        raise RpcError(E_PARAMS, "message has no text")
    if sum(len(b.get("text") or "") for b in blocks) > TEXT_MAX:
        raise RpcError(E_PARAMS, "message is too long")
    if all(b["type"] == "text" for b in blocks):
        return m, "\n".join(b["text"] for b in blocks)
    return m, blocks


def agent_message(text: str, context_id: str, task_id: str) -> dict:
    return {"messageId": uuid.uuid4().hex, "contextId": context_id, "taskId": task_id, "role": "ROLE_AGENT",
            "parts": [{"text": text, "mediaType": "text/plain"}]}


def user_message(m: dict, context_id: str, task_id: str) -> dict:
    """记进任务的那一条用户的话（图不留原样：只留一句「[image]」）。"""
    parts = [p if isinstance(p.get("text"), str) else {"text": "[image]", "mediaType": "text/plain"} for p in m["parts"]]
    return {"messageId": m["messageId"], "contextId": context_id, "taskId": task_id, "role": "ROLE_USER", "parts": parts}


# —— 任务：存、取 ——————————————————————————————————————————————————————————————

def _db() -> None:
    path = str(settings.db)
    if path in _ready:
        return
    with store.tx() as conn:
        conn.executescript(SCHEMA)
    _ready.add(path)


def save(token: str, agent: str, conv: str, message_id: str, task: dict) -> None:
    _db()
    now = store.now_iso()
    task["history"] = (task.get("history") or [])[-HISTORY_MAX:]
    with store.tx() as conn:
        conn.execute("""INSERT INTO engine_a2a_tasks(id, token, agent, context_id, message_id, conversation, state, data, created_at, updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET state=excluded.state, data=excluded.data, updated_at=excluded.updated_at""",
                     (task["id"], token, agent, task["contextId"], message_id, conv, task["status"]["state"], json.dumps(task, ensure_ascii=False),
                      now, now))
        old = conn.execute("SELECT id FROM engine_a2a_tasks WHERE token=? ORDER BY updated_at DESC LIMIT -1 OFFSET ?", (token, KEEP)).fetchall()
        if old:
            conn.executemany("DELETE FROM engine_a2a_tasks WHERE id=?", [(r["id"],) for r in old])


def load(caller: oa.Caller, agent: str, tid: str):
    """这把令牌、这个 Agent 的任务（别人的一律当不存在）→ 那一行。"""
    _db()
    with store.read() as conn:
        r = conn.execute("SELECT * FROM engine_a2a_tasks WHERE id=?", (tid,)).fetchone()
    if r is None or r["token"] != caller.token or r["agent"] != agent:
        raise RpcError(E_NOT_FOUND, "Task not found", taskId=tid)
    return r


def by_message(caller: oa.Caller, agent: str, message_id: str):
    _db()
    with store.read() as conn:
        r = conn.execute("SELECT * FROM engine_a2a_tasks WHERE token=? AND message_id=?", (caller.token, message_id)).fetchone()
    return r if r is not None and r["agent"] == agent else None


def view(task: dict, history_length: int | None, artifacts: bool = True) -> dict:
    """回给调用方的样子：historyLength 照 a2a.proto（不写 = 不限，0 = 一条都不要）。"""
    t = {k: v for k, v in task.items() if k != "history" and (artifacts or k != "artifacts")}
    hist = task.get("history") or []
    if history_length is None or history_length > 0:
        hist = hist[-history_length:] if history_length else hist
        if hist:
            t["history"] = hist
    return t


def int_param(params: dict, name: str, lo: int, hi: int) -> int | None:
    v = params.get(name)
    if v is None:
        return None
    if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
        raise RpcError(E_PARAMS, f"{name} is not valid")
    return v


def refresh(r) -> dict:
    """存着的任务 → 现在的样子：重启前还在跑的记成 FAILED；等点头的卡都有了结果 → COMPLETED（状态消息里说点了什么）。"""
    task = json.loads(r["data"])
    state = task["status"]["state"]
    if state in ("TASK_STATE_WORKING", "TASK_STATE_SUBMITTED") and r["id"] not in RUNNING:
        task["status"] = {"state": "TASK_STATE_FAILED", "timestamp": ts(), "message": agent_message(
            L("这一轮被服务重启打断了，没有跑完。", "This turn was interrupted by a server restart and didn't finish."), task["contextId"], task["id"])}
        save(r["token"], r["agent"], r["conversation"], r["message_id"], task)
    elif state == "TASK_STATE_AUTH_REQUIRED":
        pend = ((task.get("metadata") or {}).get("openmousse") or {}).get("pending") or []
        got = {c["inbox"]: oa.decision(c["inbox"]) for c in pend}
        if pend and all(got.values()):
            text = L("你点过了：" + "；".join(f"「{c['title']}」{got[c['inbox']]['outcome']}" for c in pend),
                     "You decided: " + "; ".join(f"{c['title']}: {got[c['inbox']]['outcome']}" for c in pend))
            msg = agent_message(text, task["contextId"], task["id"])
            task["status"] = {"state": "TASK_STATE_COMPLETED", "timestamp": ts(), "message": msg}
            task["history"] = [*(task.get("history") or []), msg]
            task.setdefault("metadata", {}).setdefault("openmousse", {})["pending"] = []
            save(r["token"], r["agent"], r["conversation"], r["message_id"], task)
    return task


# —— 跑一轮 ——————————————————————————————————————————————————————————————————

def finish(run: oa.Run, task: dict) -> dict:
    """这一轮跑完了 → 任务的样子（见文件开头）。"""
    tid, ctx = task["id"], task["contextId"]
    res = run.result
    if run.error is not None or res is None:
        msg = agent_message(run.error.message if run.error is not None else L("这一轮没跑起来", "The turn didn't run"), ctx, tid)
        task["status"] = {"state": "TASK_STATE_FAILED", "timestamp": ts(), "message": msg}
        task["history"] = [*task["history"], msg]
        return task
    pend = run.pending()
    said = "".join(run.text)
    text = said + oa.pending_note(pend, bool(said))
    state = ("TASK_STATE_CANCELED" if res.status == "aborted" else "TASK_STATE_AUTH_REQUIRED" if pend else "TASK_STATE_COMPLETED")
    msg = agent_message(text or L("（没有说话）", "(no reply)"), ctx, tid)
    task["status"] = {"state": state, "timestamp": ts(), "message": msg}
    task["history"] = [*task["history"], msg]
    if said:
        task["artifacts"] = [*(task.get("artifacts") or []),
                             {"artifactId": uuid.uuid4().hex, "name": "reply", "parts": [{"text": said, "mediaType": "text/plain"}]}]
    task["metadata"] = {"openmousse": {"session": run.key, "turn": res.turn, "status": res.status, "pending": pend, "usage": oa.usage_of(res.usage)}}
    return task


async def _run(run: oa.Run, caller: oa.Caller, agent: str, message_id: str, task: dict) -> dict:
    RUNNING[task["id"]] = run
    try:
        await run.go()
    finally:
        RUNNING.pop(task["id"], None)
    task = finish(run, task)
    save(caller.token, agent, run.conv, message_id, task)
    return task


async def send_message(caller: oa.Caller, agent: str, params: dict) -> dict:
    m, content = read_message(params)
    conf = params.get("configuration") if isinstance(params.get("configuration"), dict) else {}
    modes = conf.get("acceptedOutputModes")
    if isinstance(modes, list) and modes and not any(str(x).lower() in TEXT_MODES for x in modes):
        raise RpcError(E_CONTENT, "This agent answers in text/plain", acceptedOutputModes=",".join(map(str, modes)))
    hl = int_param(conf, "historyLength", 0, HISTORY_MAX)
    if (seen := by_message(caller, agent, m["messageId"])) is not None:  # 同一条消息又来了一遍：回第一次的那个任务
        return {"task": view(refresh(seen), hl)}
    if m.get("taskId"):
        prev = load(caller, agent, m["taskId"])
        if prev["id"] in RUNNING:
            raise RpcError(E_UNSUPPORTED, "This task is still working: wait for it (GetTask) or cancel it", taskId=prev["id"])
        task = refresh(prev)
        if task["status"]["state"] not in INTERRUPTED:
            raise RpcError(E_UNSUPPORTED, "This task has finished: send the next message with its contextId and no taskId", taskId=prev["id"])
        if m.get("contextId") and m["contextId"] != task["contextId"]:
            raise RpcError(E_PARAMS, "message.contextId doesn't match the task's contextId")
        ctx, tid, message_id = task["contextId"], task["id"], prev["message_id"]
    else:
        ctx, tid, message_id = m.get("contextId") or uuid.uuid4().hex, uuid.uuid4().hex, m["messageId"]
        task = {"id": tid, "contextId": ctx, "history": []}
    model = oa.model_for(agent)
    if model is None:
        raise RpcError(E_INTERNAL, oa.no_model(agent))
    key = "a2a:" + ctx
    conv = oa.conversation(caller.token, agent, key)
    oa.open_session(caller, agent, key, conv, oa.title_of(content), None)
    task["status"] = {"state": "TASK_STATE_WORKING", "timestamp": ts()}
    task["history"] = [*task["history"], user_message(m, ctx, tid)]
    save(caller.token, agent, conv, message_id, task)
    run = oa.Run(caller, agent, key, conv, content, [], model, wait=False)
    job = asyncio.create_task(_run(run, caller, agent, message_id, task))
    _bg.add(job)
    job.add_done_callback(_bg.discard)
    if conf.get("returnImmediately") is True:
        await asyncio.sleep(0)  # 让它先起来（RUNNING 里有它）
        return {"task": view(task, hl)}
    return {"task": view(await asyncio.shield(job), hl)}  # 调用方断开：这一轮照样跑完，之后 GetTask 拿得到


async def cancel_task(caller: oa.Caller, agent: str, params: dict) -> dict:
    import inbox
    from fastapi import HTTPException
    r = load(caller, agent, str(params.get("id") or ""))
    run = RUNNING.get(r["id"])
    if run is not None:
        run.control.abort()
        for _ in range(100):
            if r["id"] not in RUNNING:
                break
            await asyncio.sleep(0.05)
        r = load(caller, agent, r["id"])
    task = refresh(r)
    state = task["status"]["state"]
    if state in TERMINAL and not (run is not None and state == "TASK_STATE_CANCELED"):
        raise RpcError(E_NOT_CANCELABLE, "Task is not cancelable", taskId=r["id"])
    for c in ((task.get("metadata") or {}).get("openmousse") or {}).get("pending") or []:  # 等点头的卡撤回
        try:
            await inbox.withdraw(c["inbox"])
        except HTTPException:
            pass
    task["status"] = {"state": "TASK_STATE_CANCELED", "timestamp": ts()}
    task.setdefault("metadata", {}).setdefault("openmousse", {})["pending"] = []
    save(caller.token, agent, r["conversation"], r["message_id"], task)
    return view(task, None)


def list_tasks(caller: oa.Caller, agent: str, params: dict) -> dict:
    _db()
    size = int_param(params, "pageSize", 1, 100) or 50
    hl = int_param(params, "historyLength", 0, HISTORY_MAX)
    q, args = "SELECT * FROM engine_a2a_tasks WHERE token=? AND agent=?", [caller.token, agent]
    for col, k in (("context_id", "contextId"), ("state", "status")):
        if params.get(k):
            q += f" AND {col}=?"
            args.append(str(params[k]))
    with store.read() as conn:
        total = conn.execute(q.replace("SELECT *", "SELECT COUNT(*)"), args).fetchone()[0]
        if params.get("pageToken"):
            q += " AND updated_at<?"
            args.append(str(params["pageToken"]))
        rows = conn.execute(q + " ORDER BY updated_at DESC LIMIT ?", [*args, size + 1]).fetchall()
    more = len(rows) > size
    rows = rows[:size]
    return {"tasks": [view(refresh(r), hl, bool(params.get("includeArtifacts"))) for r in rows],
            "nextPageToken": rows[-1]["updated_at"] if more and rows else "", "pageSize": size, "totalSize": total}


async def dispatch(caller: oa.Caller, agent: str, method: str, params: dict):
    if method == "SendMessage":
        return await send_message(caller, agent, params)
    if method == "GetTask":
        r = load(caller, agent, str(params.get("id") or ""))
        return view(refresh(r), int_param(params, "historyLength", 0, HISTORY_MAX))
    if method == "ListTasks":
        return list_tasks(caller, agent, params)
    if method == "CancelTask":
        return await cancel_task(caller, agent, params)
    if method in UNSUPPORTED:
        raise RpcError(E_UNSUPPORTED, f"{method} is not supported here (no streaming, push notifications or extended card)")
    if method in METHODS_03:
        raise RpcError(E_VERSION, "This agent speaks A2A 1.0", supportedVersions=VERSION)
    raise RpcError(E_METHOD, "Method not found")


# —— 接口（挂在 openai_api 的 /v1 上）——————————————————————————————————————————————

def _agent(caller: oa.Caller, agent: str) -> tuple[str, str]:
    """路径里的 Agent：这把令牌能用的才认（同 /v1/chat/completions 的 model）→ (id, 名字)。"""
    a = oa.pick(caller, oa.model_id(agent))
    return a, oa.agents()[a]


@oa.api.get("/v1/a2a")
async def a2a_index(request: Request):
    gate()
    caller = oa.caller_of(request)
    base = base_url(request)
    return JSONResponse({"object": "list", "data": [{"agent": a, "name": n, "card": f"{base}/v1/a2a/{a}/agent-card.json", "url": f"{base}/v1/a2a/{a}"}
                                                    for a, n in oa.visible(caller).items()]}, headers={"Cache-Control": "no-store"})


@oa.api.get("/v1/a2a/{agent}/agent-card.json")
async def a2a_card(agent: str, request: Request):
    gate()
    caller = oa.caller_of(request)
    a, name = _agent(caller, agent)
    return JSONResponse(card(a, name, base_url(request)), headers={"Cache-Control": "no-store"})


@oa.api.post("/v1/a2a/{agent}")
async def a2a_rpc(agent: str, request: Request):
    gate()
    caller = oa.caller_of(request)
    a, _ = _agent(caller, agent)
    raw = await request.body()
    if len(raw) > oa.BODY_MAX:
        return rpc_err(None, E_REQUEST, "The request is too large")
    try:
        req = json.loads(raw or b"null")
    except ValueError:
        return rpc_err(None, E_PARSE, "Invalid JSON payload")
    if not isinstance(req, dict) or req.get("jsonrpc") != "2.0" or not isinstance(req.get("method"), str):
        return rpc_err(req.get("id") if isinstance(req, dict) else None, E_REQUEST, "Request payload validation error")
    id_ = req.get("id")
    ver = (request.headers.get("a2a-version") or request.query_params.get("A2A-Version") or "").strip()
    if ver and ver not in ("1.0", "1"):
        return rpc_err(id_, E_VERSION, f"A2A version {ver} is not supported", supportedVersions=VERSION)
    params = req.get("params") if isinstance(req.get("params"), dict) else {}
    try:
        return rpc_ok(id_, await dispatch(caller, a, req["method"], params))
    except RpcError as e:
        return rpc_err(id_, e.code, e.message, **e.meta)
