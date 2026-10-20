"""对外接口（2026-10-16）：别的程序（别家的 claw、编辑器、脚本）把 OpenMousse 的 Agent 当成一个模型、一个 agent 来用。
OpenAI 兼容的 HTTP 接口在这里；ACP 适配器（给编辑器用）是另一个小进程 engine/acp.py，经这里的接口说话。

开关（server.json，每次读文件，开、关都不用重启）：
  engine.api   不写 / false = 关：/v1 不挂（Gate 原样往下交），服务和以前一样。true 或 {…} = 开，claw 还要是 builtin
               （同定时任务：内核的主人回合只在 builtin 时跑）。{…} 里都可以不写：
                 agents    ["main", "diet"]：只给这几个 Agent（不写 = main 和所有 Agent）
                 approve   false：调用方不能替自己会话里交的卡点头，卡只在 app 里等（默认 true：ACP 的 session/request_permission 靠它）
                 wait      流式请求带 X-OpenMousse-Approvals: wait 时，关卡当场等点头最多几秒（默认 300，最多 1800）
                 responses true：多开 /v1/responses（OpenAI 的 Responses API，第 7 批；默认关，见文件后面那一节）
                 a2a       true：多开 /v1/a2a/<agent id>（主人自己的 A2A 1.0，第 7 批；默认关，engine/a2a_api.py）
令牌：auth.tokens 里名字是 api（哪个 Agent 都能用）或 api-<agent id>（只能用那个 Agent，同 /mcp 的 mcp-<id>）的（python3 tokens.py add api）；
  Authorization: Bearer <令牌>（也认 X-API-Key）。别的令牌这里一律 401；这几把在 /api 上 403（main.py 的 guard）：拿着它的程序不能经 /api
  替你点头、配对设备，只能对它自己的会话里交的卡点头（approvals，见下）。

接口（都要令牌；出错一律 OpenAI 的样子 {"error": {"message", "type", "param", "code"}}）：
  GET  /v1/models                    能用的 Agent：{"object": "list", "data": [{"id": "openmousse/<agent id>", "object": "model", …, "name"}]}
  GET  /v1/models/{id}               其中一个
  POST /v1/chat/completions          跑一轮（下面）；stream = true 回 SSE（chat.completion.chunk … data: [DONE]），不然一次性的 chat.completion
  GET  /v1/openmousse/sessions       这把令牌的会话，新的在前（ACP 的 session/list、resume）：?agent= ?cwd= ?id=（会话键）?limit=（默认 50）
                                     ?before=（上一页最后一条的 updated_at）
  POST /v1/openmousse/approvals/{收件箱 id}  {"action": "approve" | "reject", "note"?}：对这把令牌自己的会话里交的卡点头 / 拒绝
                                     （和 app 里点一样走 inbox.act；engine.api.approve = false 时 403，不是它的卡 404）
  POST /v1/responses、GET / DELETE /v1/responses/{id}  Responses API（engine.api.responses = true 才有，不然 404；见文件后面那一节）
  GET  /v1/openmousse/history        一个会话里说过的话（ACP 的 session/load 重放用）：?id=（会话键）?agent= ?limit=；只看得到自己这把令牌的
  GET  /v1/a2a、GET /v1/a2a/{agent}/agent-card.json、POST /v1/a2a/{agent}  A2A 1.0 的 JSON-RPC（engine.api.a2a = true 才有，engine/a2a_api.py）

一轮（照 engine/owner.py 的主人会话：备料、这个 Agent 的工具和关卡、压缩，都和 app 里一样）：
  model         openmousse/<agent id>；只写 openmousse 或不写 = main（api-<id> 的令牌 = 它自己）。别的 404 model_not_found，
                api-<id> 的令牌点别的 Agent 403。用哪个大模型：这个 Agent 在 app 里的对话选的那个（没选 = engine.model）
  会话键        请求头 X-Session-Id，没有就看 user 字段，都没有就每次一个新会话（键在回的 X-Session-Id 头里，带上就接得上）；
                最多 256 字、不能有控制字符。对话 = api:<agent>:<令牌名 + Agent + 键 的指纹>，和 app 的对话一样每个逻辑日一条会话
                （04:00 换新；前一天最后几句放进开头，见 tail_part）
  messages      最后一条 user 是这一句（字，或 text / image_url 块；别的块 400）。建会话的那一轮（新的键、新的一天）前面的
                system / developer / user / assistant 一起放进去当前情（无状态的客户端每次带全部记录也接得上），之后只看最后一句。
                tools、temperature 这些不管：Agent 用自己的工具
  回            content = 这一轮说的全部字（几次开口之间隔一个空行，和 app 一样），reasoning_content = 思考摘要；finish_reason
                stop / length / content_filter（拒答）；usage 是这一轮所有模型调用加起来（流式要 stream_options.include_usage）；
                多给一样 "openmousse": {"session", "turn", "status", "pending": 还在等点头的卡}
  撤不回的      照样进收件箱（卡挂在这个 Agent 在 app 里的对话），回的字后面写明在等用户点头、哪张卡、去哪点（pending_note）。
                点了以后结果不发进 app 的对话，交回这个会话（engine/tools.py 的 on_decided → tell）：下一句前面带上【收件箱】…
  出错          还没开始：HTTP 状态 + error。流式已经开始：一条 data: {"error": …} 再 data: [DONE]（OpenAI 的 SDK 照常抛错）。
                模型那边出错 502 upstream_error、超时 504、内核没配好 503。断开连接 = 停（同 app 里点停：出来的字留着、会话补完整）

给 ACP 适配器（也给想要的别的程序）多的三样，都要自己说，普通的 OpenAI 客户端看不到：
  X-OpenMousse-Events: 1          流式里多几种有名字的 SSE 事件（event: openmousse.…）：
      openmousse.tool             {"phase": "start", "id", "name", "title"} / {"phase": "end", "id", "name", "status": ok | error | timeout
                                  | stopped | denied | pending}
      openmousse.approval         {"id": 工具调用, "inbox": 卡, "tool", "title", "why", "changes", "key": 「工具 子命令」, "approvable", "waiting"}
      openmousse.approval_result  {"id", "inbox", "outcome": approved | failed | rejected | revise | withdrawn | expired, "output" | "error" | "note"}
      openmousse.done             {"session", "turn", "status", "pending"}（在 [DONE] 之前）
  X-OpenMousse-Approvals: wait    流式时：撤不回的调用出了卡，关卡当场等这张卡有结果（最多 wait 秒，期间每 15 秒一行 SSE 注释保活）。
                                  调用方那边问用户、把结果 POST 到 approvals（手机上点也一样），模型这一轮就拿到真结果接着做；
                                  没等到（超时、断开）照旧：卡在收件箱里等，回的字写明在等点头
  X-OpenMousse-Cwd: <路径>          记在会话上（ACP 的 session/list 按它分）；请求头只能是 latin-1，路径先 %-编码

参考（照着做法自己写的，没有拿代码）：OpenClaw 的 src/gateway/openai-http.ts、http-utils.ts、models-http.ts、openai-compat-errors.ts
（默认关、model 选 agent、user 当会话键、断开就停、流开始以后的错怎么报）；Hermes 的 gateway/platforms/api_server.py、
api_server_openai_routes.py（会话头、空闲时的保活注释、自己的 SSE 事件、流里的点头请求）。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import time
import urllib.parse
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

import i18n
from config import raw, settings
from i18n import L

from . import models, owner, store
from . import tools as tools_mod
from .errors import EngineError
from .loop import Control, note_restart
from .session import DbSession, system_note, text_of

PREFIX = tools_mod.API_CONV  # 对话 id 的开头：api:<agent>:<指纹>
MODEL = "openmousse"         # model 写 openmousse/<agent id>
WAIT, WAIT_MAX = 300.0, 1800.0
KEEPALIVE = 15.0             # 流式里多久没东西就发一行 SSE 注释（等点头、工具跑得久的时候连接别被掐）
POLL = 0.25                  # 关卡等点头时多久看一眼卡
GRACE = 5.0                  # 断开连接后 abort 这么多秒还没收尾就直接取消（同 chat.STOP_GRACE）
KEY_MAX = 256
BODY_MAX = 20_000_000
TITLE_MAX = 80
NOTES_MAX = 20               # 一个会话最多攒几条「卡有结果了」（下一句前面带上）
CLIENT_NOTE = ("The program calling you through the API sent these instructions with the conversation. Follow them where they don't "
               "conflict with your own rules:\n\n")
FINISH = {"done": "stop", "length": "length", "max_steps": "length", "refused": "content_filter", "aborted": "stop"}
SCHEMA = """
CREATE TABLE IF NOT EXISTS engine_api_sessions (
  conversation TEXT PRIMARY KEY, token TEXT NOT NULL, agent TEXT NOT NULL, key TEXT NOT NULL, title TEXT NOT NULL DEFAULT '', cwd TEXT,
  notes TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS engine_api_sessions_token ON engine_api_sessions(token, updated_at);
"""
_ready: set[str] = set()
RUNS: dict[Control, Run] = {}  # 正在跑的（关服务时 stop_all 停它们）
WAITING: dict[str, int] = {}   # 关卡正当场等着的卡 → 几个在等（tell 看到就不另外记）


# —— 开关 ——————————————————————————————————————————————————————————————————

def cfg() -> dict | None:
    """server.json 的 engine.api：开着 → 设置（dict，可能是空的）；关着 → None。"""
    e = raw().get("engine")
    v = e.get("api") if isinstance(e, dict) else None
    if v is True:
        return {}
    return v if isinstance(v, dict) and v.get("enabled", True) is not False else None


def enabled() -> bool:
    """/v1 挂不挂：engine.api 开着，而且 claw 是 builtin。每次现读。"""
    import claw
    return cfg() is not None and claw.is_builtin()


def approve_allowed() -> bool:
    return (cfg() or {}).get("approve", True) is not False


def wait_seconds() -> float:
    try:
        w = float((cfg() or {}).get("wait", WAIT))
    except (TypeError, ValueError):
        w = WAIT
    return max(0.0, min(WAIT_MAX, w))


# —— 错误：OpenAI 的样子 ————————————————————————————————————————————————————————

class ApiError(Exception):
    def __init__(self, status: int, message: str, type_: str = "invalid_request_error", param: str | None = None, code: str | None = None,
                 headers: dict[str, str] | None = None) -> None:
        super().__init__(message)
        self.status, self.message, self.type, self.param, self.code = status, message, type_, param, code
        self.headers = headers or {}

    def body(self) -> dict:
        return {"error": {"message": self.message, "type": self.type, "param": self.param, "code": self.code}}

    def response(self) -> JSONResponse:
        return JSONResponse(self.body(), status_code=self.status, headers={"Cache-Control": "no-store", **self.headers})


def engine_error(msg: str, kind: str | None) -> ApiError:
    """内核这一轮出的错（EngineError.kind / Result.error_kind）→ OpenAI 的错。"""
    if kind == "config":
        return ApiError(503, L(f"内核没配好：{msg}", f"The engine isn't set up right: {msg}"), "server_error", None, "engine_config")
    if kind == "timeout":
        return ApiError(504, L(f"模型太久没回：{msg}", f"The model took too long: {msg}"), "server_error", None, "upstream_timeout")
    if kind == "rule":
        return ApiError(400, msg, "invalid_request_error", "messages", None)
    return ApiError(502, L(f"模型那边出错了：{msg}", f"The model failed: {msg}"), "server_error", None, "upstream_error")


# —— 令牌 ——————————————————————————————————————————————————————————————————

@dataclass
class Caller:
    token: str            # 令牌名：api 或 api-<agent id>
    agent: str | None     # api-<id> 绑的 Agent；api = None（哪个都能用）


def is_api_token(name: str) -> bool:
    return name == "api" or (name.startswith("api-") and len(name) > 4)


def identify(request: Request) -> Caller | None:
    """只认 api、api-<agent id>（Authorization: Bearer，或 X-API-Key）。"""
    auth = request.headers.get("authorization", "")
    tok = auth[7:].strip() if auth.lower().startswith("bearer ") else (request.headers.get("x-api-key") or "").strip()
    if not tok:
        return None
    for name, value in settings.tokens().items():
        if is_api_token(name) and value and secrets.compare_digest(value.encode(), tok.encode()):
            return Caller(name, name[4:] if name != "api" else None)
    return None


def caller_of(request: Request) -> Caller:
    who = identify(request)
    if who is None:
        raise ApiError(401, L("没有有效的 API 令牌：带上 Authorization: Bearer <server.json 的 auth.tokens 里叫 api 或 api-<agent id> 的那把>",
                              "No valid API token: send Authorization: Bearer <the token named api or api-<agent id> under auth.tokens in "
                              "server.json>"), "invalid_request_error", None, "invalid_api_key", {"WWW-Authenticate": "Bearer"})
    return who


# —— Agent = model ——————————————————————————————————————————————————————————

def agents() -> dict[str, str]:
    """能用的 Agent：id → 名字（main 是助手本名）。engine.api.agents 写了就只给那几个。"""
    out = {"main": settings.app_name}
    try:
        import data
        with data._lock, data.ddb() as conn:
            out.update({r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM groups ORDER BY position, created_at")})
    except Exception:  # noqa: BLE001 — 读不到 groups：只有 main 和有工作区的
        pass
    for a in settings.agent_workspaces:
        out.setdefault(a, a)
    allow = (cfg() or {}).get("agents")
    if isinstance(allow, list):
        keep = {str(x) for x in allow}
        out = {k: v for k, v in out.items() if k in keep}
    return out


def visible(caller: Caller) -> dict[str, str]:
    return {k: v for k, v in agents().items() if caller.agent is None or k == caller.agent}


def model_id(agent: str) -> str:
    return f"{MODEL}/{agent}"


def model_card(agent: str, name: str) -> dict:
    return {"id": model_id(agent), "object": "model", "created": 0, "owned_by": MODEL, "name": name}


def pick(caller: Caller, model: Any) -> str:
    """请求里的 model → 用哪个 Agent。"""
    m = str(model or "").strip()
    if m in ("", MODEL):
        agent = caller.agent or "main"
    elif m.startswith(MODEL + "/") and len(m) > len(MODEL) + 1:
        agent = m[len(MODEL) + 1:]
    else:
        raise ApiError(404, L(f"没有 {m} 这个模型：写 openmousse/<agent id>（GET /v1/models 列出能用的）",
                              f"The model {m!r} does not exist: use openmousse/<agent id> (GET /v1/models lists them)"),
                       "invalid_request_error", "model", "model_not_found")
    if caller.agent is not None and agent != caller.agent:
        raise ApiError(403, L(f"这把令牌只能用 {model_id(caller.agent)}", f"This token can only use {model_id(caller.agent)}"),
                       "permission_error", "model", "model_not_allowed")
    if agent not in agents():
        raise ApiError(404, L(f"没有 {model_id(agent)} 这个模型（GET /v1/models 列出能用的）",
                              f"The model {model_id(agent)!r} does not exist (GET /v1/models lists them)"),
                       "invalid_request_error", "model", "model_not_found")
    return agent


def chosen(agent: str) -> str | None:
    """这个 Agent 在 app 里的对话选的模型（没选 / 读不出 = None）。"""
    import chat
    try:
        with chat._lock, chat.db() as conn:
            return chat.thread_model(conn, agent)
    except Exception:  # noqa: BLE001
        return None


def no_model(agent: str) -> str:
    """model_for 挑不出模型时 503 的那句：只配了用户自己的套餐（claude-code/…、chatgpt/…）的，说清是要再加一个非套餐的模型。"""
    if models.subscription(models.default_model()) or models.subscription(chosen(agent)):
        return L("对外接口不花你自己的套餐（claude-code/…、chatgpt/…）：在 server.json 的 engine.models 里加一个非套餐的模型（比如 deepseek/deepseek-flash）",
                 "The API doesn't spend your own subscription (claude-code/… or chatgpt/…): add a non-subscription model to engine.models in "
                 "server.json (e.g. deepseek/deepseek-flash)")
    return L("内核还没配模型：server.json 的 engine.model", "The engine has no model yet: set engine.model in server.json")


def model_for(agent: str) -> str | None:
    """跑哪个大模型：这个 Agent 在 app 里的对话选的那个，没选或者不能用 = engine.model。都没有 → None。"""
    m = chosen(agent)
    if m and models.valid_ref(m) and not models.subscription(m):  # 命令行的套餐（codex/…，第 7 批）也是 valid_ref：不给外面的程序
        return models.resolve(m)  # 别名（engine.aliases）：换成真的引用
    d = models.default_model()
    if models.subscription(d):  # 默认的是用户自己的套餐（claude-code/…，第 11 项）：外面的程序不花它，换成非套餐的（没有 → None）
        return models.outside(d)
    return d if models.valid_ref(d) else None


# —— 会话 ——————————————————————————————————————————————————————————————————

def session_key(request: Request, body: dict) -> tuple[str, bool]:
    """→ (会话键, 是不是调用方给的)。X-Session-Id 优先，其次 user 字段；都没有就新的一个。"""
    for value, where in ((request.headers.get("x-session-id"), "X-Session-Id"), (body.get("user"), "user")):
        if value is None:
            continue
        if not isinstance(value, str):
            raise ApiError(400, L(f"{where} 要是字符串", f"{where} must be a string"), "invalid_request_error", where, "invalid_session_id")
        v = value.strip()
        if not v:
            continue
        if len(v) > KEY_MAX or any(ord(c) < 32 or ord(c) == 127 for c in v):
            raise ApiError(400, L(f"{where} 太长（最多 {KEY_MAX} 字）或者有控制字符", f"{where} is too long (max {KEY_MAX}) or has control characters"),
                           "invalid_request_error", where, "invalid_session_id")
        return v, True
    return uuid.uuid4().hex, False


def conversation(token: str, agent: str, key: str) -> str:
    """对话 id：api:<agent>:<令牌名 + Agent + 会话键 的指纹>（不同令牌、不同 Agent 用同一个键也是不同的对话）。"""
    h = hashlib.sha256(f"{token}\x00{agent}\x00{key}".encode()).hexdigest()[:24]
    return f"{PREFIX}{agent}:{h}"


def _db_ready() -> None:
    """这张表用到时才建（不开对外接口，库里什么都不多）。"""
    path = str(settings.db)
    if path in _ready:
        return
    with store.tx() as conn:
        conn.executescript(SCHEMA)
    _ready.add(path)


def open_session(caller: Caller, agent: str, key: str, conv: str, title: str, cwd: str | None) -> None:
    """记下（或者碰一下）这个会话：第一次的标题、最近一次的工作目录、什么时候用过。"""
    _db_ready()
    now = store.now_iso()
    with store.tx() as conn:
        conn.execute("""INSERT INTO engine_api_sessions(conversation, token, agent, key, title, cwd, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(conversation) DO UPDATE SET updated_at=excluded.updated_at, cwd=COALESCE(excluded.cwd, engine_api_sessions.cwd),
            title=CASE WHEN engine_api_sessions.title='' THEN excluded.title ELSE engine_api_sessions.title END""",
                     (conv, caller.token, agent, key, title, cwd or None, now, now))


def session_owner(conv: str) -> str | None:
    """这个对话是哪把令牌的（令牌名）。"""
    _db_ready()
    with store.read() as conn:
        r = conn.execute("SELECT token FROM engine_api_sessions WHERE conversation=?", (conv,)).fetchone()
    return r["token"] if r else None


def list_sessions(token: str, *, agent: str | None = None, cwd: str | None = None, key: str | None = None, limit: int = 50,
                  before: str | None = None) -> list[dict]:
    _db_ready()
    q, args = "SELECT * FROM engine_api_sessions WHERE token=?", [token]
    for col, v in (("agent", agent), ("cwd", cwd), ("key", key)):
        if v:
            q += f" AND {col}=?"
            args.append(v)
    if before:
        q += " AND updated_at<?"
        args.append(before)
    with store.read() as conn:
        rows = conn.execute(q + " ORDER BY updated_at DESC LIMIT ?", (*args, limit)).fetchall()
    return [{"id": r["key"], "agent": r["agent"], "model": model_id(r["agent"]), "title": r["title"], "cwd": r["cwd"],
             "created_at": r["created_at"], "updated_at": r["updated_at"]} for r in rows]


def _notes(conv: str, change) -> list[str]:
    _db_ready()
    with store.tx() as conn:
        r = conn.execute("SELECT notes FROM engine_api_sessions WHERE conversation=?", (conv,)).fetchone()
        if r is None:
            return []
        try:
            cur = [str(n) for n in json.loads(r["notes"] or "[]")]
        except ValueError:
            cur = []
        new, out = change(cur)
        if new != cur:
            conn.execute("UPDATE engine_api_sessions SET notes=? WHERE conversation=?", (json.dumps(new[-NOTES_MAX:], ensure_ascii=False), conv))
    return out


def take_notes(conv: str) -> list[str]:
    """攒着的「卡有结果了」：取出来（下一句前面带上），清空。"""
    return _notes(conv, lambda cur: ([], cur))


def add_notes(conv: str, texts: list[str], *, front: bool = False) -> None:
    _notes(conv, lambda cur: ([*texts, *cur] if front else [*cur, *texts], []))


async def tell(conv: str, iid: str, text: str) -> None:
    """engine/tools.py 的 on_decided：这个对话里交的卡有了结果。关卡正当场等着 → 不用管（它自己拿结果交给模型）；不然攒着，下一句带上。"""
    if WAITING.get(iid):
        return
    add_notes(conv, [text])


def tail_part(conv: str, day: str):
    """context.tail_part 给对外接口的对话用：app 里没有它的记录，从前几天（最多 TAIL_DAYS）这个对话最近的那条会话里取最后几句。"""
    from . import context
    empty = context.Part("tail", "Where the last conversation ended", live=False)
    with store.read() as conn:
        r = conn.execute("SELECT id, key FROM engine_sessions WHERE key>=? AND key<? ORDER BY key DESC, created_at DESC LIMIT 1",
                         (f"mousse:{conv}:{context.logical(day, context.TAIL_DAYS)}", f"mousse:{conv}:{day}")).fetchone()
    s = DbSession.get(r["id"]) if r else None
    if s is None:
        return empty
    said = [(m["role"], " ".join(text_of(m).split())) for m in s.messages() if m.get("role") in ("user", "assistant") and not m.get("compaction")]
    said = [(role, t) for role, t in said if t][-context.TAIL_MSGS:]
    if not said:
        return empty
    lines = [f"{'User' if role == 'user' else 'You'}: {t if len(t) <= context.TAIL_MSG_MAX else t[:context.TAIL_MSG_MAX] + '…'}"
             for role, t in said]
    if said[-1][0] == "user":
        lines.append("(That last message never got a reply.)")
    text = "\n".join(lines)
    if len(text) > context.TAIL_MAX:
        text = "…" + text[-context.TAIL_MAX:]
    return context.Part("tail", f"Where the last conversation ended ({r['key'][-10:]})", text, live=False)


# —— 请求里的 messages ————————————————————————————————————————————————————————————

def clean_content(c: Any, where: str) -> str | list:
    """一条消息的 content：字符串，或者 text / image_url 块（OpenAI 的写法；input_text 也认）。别的块 400。"""
    if c is None:
        return ""
    if isinstance(c, str):
        return c
    if not isinstance(c, list):
        raise ApiError(400, L(f"{where} 要是字符串或块的列表", f"{where} must be a string or a list of parts"), "invalid_request_error", where,
                       "invalid_content")
    out: list[dict] = []
    for j, p in enumerate(c):
        t = p.get("type") if isinstance(p, dict) else None
        if t in ("text", "input_text"):
            out.append({"type": "text", "text": str(p.get("text") or "")})
        elif t == "image_url":
            url = p.get("image_url")
            url = url.get("url") if isinstance(url, dict) else url
            if not isinstance(url, str) or not url:
                raise ApiError(400, L(f"{where}[{j}] 的 image_url 没有 url", f"{where}[{j}] has no image_url.url"), "invalid_request_error",
                               f"{where}[{j}]", "invalid_content")
            out.append({"type": "image_url", "image_url": {"url": url}})
        else:
            raise ApiError(400, L(f"{where}[{j}]：这里只认 text 和 image_url 块", f"{where}[{j}]: only text and image_url parts are supported"),
                           "invalid_request_error", f"{where}[{j}]", "unsupported_content_type")
    return out


def flat_text(c: Any) -> str:
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "\n".join(str(p.get("text") or "") for p in c if isinstance(p, dict) and p.get("type") in ("text", "input_text"))
    return ""


def has_input(content: str | list) -> bool:
    if isinstance(content, str):
        return bool(content.strip())
    return any((p["type"] == "text" and p["text"].strip()) or p["type"] == "image_url" for p in content)


def title_of(content: str | list) -> str:
    t = " ".join(flat_text(content).split())
    return t if len(t) <= TITLE_MAX else t[:TITLE_MAX - 1] + "…"


def split_messages(messages: Any) -> tuple[str | list, list[dict]]:
    """→ (这一句, 前情)。这一句 = 最后一条 user；前情 = 它前面的 system / developer（调用方的说明，变成一条 system）、user、assistant
    （中立格式，连着同一边的并成一条；tool 和工具调用不要：那是调用方自己的工具）。前情只在建会话时用（owner.open_day）。"""
    if not isinstance(messages, list) or not messages:
        raise ApiError(400, L("messages 要是一个非空的列表", "messages must be a non-empty list"), "invalid_request_error", "messages", "missing_messages")
    if not all(isinstance(m, dict) for m in messages):
        raise ApiError(400, L("messages 里每一条都要是对象", "every item in messages must be an object"), "invalid_request_error", "messages")
    last = max((i for i, m in enumerate(messages) if m.get("role") == "user"), default=None)
    if last is None:
        raise ApiError(400, L("messages 里没有 user 的话", "No user message in messages"), "invalid_request_error", "messages", "missing_user_message")
    content = clean_content(messages[last].get("content"), f"messages[{last}].content")
    if not has_input(content):
        raise ApiError(400, L("最后一条 user 的话是空的", "The last user message is empty"), "invalid_request_error", f"messages[{last}].content")
    history: list[dict] = []
    for i, m in enumerate(messages[:last]):
        role = m.get("role")
        if role in ("system", "developer"):
            t = flat_text(m.get("content")).strip()
            if t:
                history.append(system_note(CLIENT_NOTE + t))
            continue
        if role not in ("user", "assistant"):
            continue
        c = clean_content(m.get("content"), f"messages[{i}].content") if role == "user" else flat_text(m.get("content"))
        for n in owner.neutral([{"role": role, "content": c}]):
            prev = history[-1] if history else None
            if prev is not None and prev["role"] == role:
                prev["content"] = [*prev["content"], *n["content"]]
            elif role == "assistant" and not any(h["role"] == "user" for h in history):
                continue  # 从用户那句开始（同 owner.seed）：有的模型不收打头的 assistant
            else:
                history.append(n)
    return content, history


def with_notes(notes: list[str], content: str | list) -> str | list:
    """攒着的「卡有结果了」放在这一句前面（【收件箱】开头，同 app 里点了以后发进对话的那句）。"""
    import inbox
    head = "\n\n".join(inbox.MARK + n for n in notes)
    return f"{head}\n\n{content}" if isinstance(content, str) else [{"type": "text", "text": head}, *content]


# —— 一轮 ————————————————————————————————————————————————————————————————————

def usage_of(u: dict) -> dict:
    """内核的用量（in / out / cache_read / cache_write）→ OpenAI 的 usage。prompt_tokens 连缓存读、写都算上。"""
    cr = int(u.get("cache_read") or 0)
    p = int(u.get("in") or 0) + cr + int(u.get("cache_write") or 0)
    c = int(u.get("out") or 0)
    return {"prompt_tokens": p, "completion_tokens": c, "total_tokens": p + c, "prompt_tokens_details": {"cached_tokens": cr}}


def decision(iid: str) -> dict | None:
    """这张卡定下来了吗 → {"outcome": approved | failed | rejected | revise | withdrawn | expired, "output" | "error" | "note"}；
    还在等（或者同意了、正在跑）→ None。先看 engine_calls（on_decided 记的），再看卡本身（过期了、同意了但对不上没跑）。"""
    import inbox
    row = tools_mod.call_of(iid)
    st = row["status"] if row is not None else None
    try:
        it: dict | None = inbox.item(iid)
    except HTTPException:
        it = None
    note = " ".join(str((it or {}).get("note") or "").split())[:300]
    if st == "done":
        return {"outcome": "approved", "output": row["result"] or ""}
    if st == "failed":
        return {"outcome": "failed", "error": row["result"] or ""}
    if st == "revised":
        return {"outcome": "revise", "note": note}
    if st in ("rejected", "withdrawn"):
        return {"outcome": st, "note": note}
    s = (it or {}).get("status")
    if s in ("rejected", "withdrawn", "expired"):
        return {"outcome": s, "note": note}
    if s == "failed":
        return {"outcome": "failed", "error": str((it or {}).get("result") or "")}
    return None


def verdict_of(iid: str, got: dict) -> str | dict:
    """关卡当场等到了用户的决定 → 回给模型的工具结果（dict 不算出错，带 error 的、字符串的算；见 loop.Spec.gate）。"""
    o, note = got["outcome"], got.get("note") or ""
    if o == "approved":
        return {"status": "approved", "inbox": iid, "output": got.get("output") or "",
                "message": L("用户点了同意，服务器照这次的参数跑了一次，输出在 output 里。别再调一次。",
                             "The user approved this call and the server ran it exactly once; its output is in output. Don't call it again.")}
    if o == "failed":
        return {"status": "failed", "inbox": iid, "error": True,
                "message": L(f"用户点了同意，但没做成：{got.get('error') or ''}。跟用户说一声，别自己重试，除非他让你再来。",
                             f"The user approved this call, but it didn't work: {got.get('error') or ''}. Tell the user; don't retry unless "
                             "they ask.")}
    if o == "revise":
        return L(f"没跑：用户要改一下：{note}。卡上的参数改不了：按他说的改好参数，再调用一次（会出一张新卡等他点头）。",
                 f"Not run: the user wants a change: {note}. The card's arguments can't be edited: call the tool again with the arguments "
                 "changed as they say (that makes a new card for their OK).")
    if o == "rejected":
        return L("没跑：用户拒绝了这次调用" + (f"（{note}）" if note else "") + "。用户没有同意：别再试，也别换个办法做，除非他又让你做。",
                 "Not run: the user declined this call" + (f" ({note})" if note else "") + ". The user has NOT consented: don't retry it or "
                 "do it another way unless they ask again.")
    return L(f"没跑：这张卡{'撤回了' if o == 'withdrawn' else '过期了'}，用户没有同意。别再试；还要做的话先跟用户说。",
             f"Not run: the approval card was {o}, so the user has NOT consented. Don't retry; ask the user first if it's still needed.")


def pending_note(cards: list[dict], after_text: bool) -> str:
    """还在等点头的卡：回给调用方的字后面写明（模型自己也会说，这里保证说清楚是哪张、去哪点）。"""
    app = settings.app_name
    lines = [L(f"（在等你点头：{c['title']}，收件箱卡 {c['inbox']}。到 {app} 的收件箱里同意或拒绝。）",
               f"(Waiting for your OK: {c['title']}, inbox card {c['inbox']}. Approve or decline it in {app}'s inbox.)") for c in cards]
    if not lines:
        return ""
    return ("\n\n" if after_text else "") + "\n".join(lines)


def frame(data: Any, event: str | None = None) -> bytes:
    return ((f"event: {event}\n" if event else "") + f"data: {json.dumps(data, ensure_ascii=False)}\n\n").encode()


def _cancel(task: asyncio.Task) -> None:
    if not task.done():
        task.cancel()


class Run:
    """一次 /v1/chat/completions 的这一轮：跑内核（owner.respond）、把字和事件排进队列（流式从这里读）、记下这一轮交的卡。"""

    def __init__(self, caller: Caller, agent: str, key: str, conv: str, content: str | list, history: list[dict], model: str, *,
                 wait: bool) -> None:
        self.caller, self.agent, self.key, self.conv = caller, agent, key, conv
        self.content, self.history, self.model, self.wait = content, history, model, wait
        self.control = Control()
        self.q: asyncio.Queue = asyncio.Queue()
        self.text: list[str] = []
        self.thinking: list[str] = []
        self.cards: dict[str, dict] = {}  # 收件箱 id → 这一轮交的卡（approval 事件那几样 + outcome）
        self.result = None
        self.error: ApiError | None = None
        self.shutdown = False

    def put(self, kind: str, data: Any = None) -> None:
        self.q.put_nowait((kind, data))

    def on_text(self, text: str) -> None:
        self.text.append(text)
        self.put("text", text)

    def on_event(self, ev: dict) -> None:
        t = ev.get("type")
        if t == "thinking" and ev.get("text"):
            self.thinking.append(str(ev["text"]))
            self.put("thinking", str(ev["text"]))
        elif t == "tool_run":
            self.put("tool", {"phase": "start", "id": ev.get("id"), "name": ev.get("name"), "title": ev.get("detail") or ev.get("name")})
        elif t == "tool_end":
            self.put("tool", {"phase": "end", "id": ev.get("id"), "name": ev.get("name"), "status": ev.get("status")})

    async def go(self) -> None:
        import chat
        RUNS[self.control] = self
        notes = take_notes(self.conv)
        content = with_notes(notes, self.content) if notes else self.content
        try:
            self.result = await owner.respond(self.conv, chat.day_of(chat.now_iso()), content, self.model, self.on_text, origin="api",
                                              control=self.control, on_event=self.on_event, agent=self.agent, history=self.history,
                                              gate=self.gate)
            if self.result.status == "error":
                self.error = engine_error(self.result.error or "model error", self.result.error_kind)
            elif self.result.status == "aborted" and self.shutdown:
                note_restart(self.result.turn)  # 关服务时停的：会话里补一句，下一轮它知道调用方没看到后面
        except EngineError as e:  # 还没开始跑（配置不对）：这句话没进会话，攒着的放回去
            self.error = engine_error(str(e), e.kind)
            if notes:
                add_notes(self.conv, notes, front=True)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — 记成出错，别把连接带崩
            self.error = ApiError(500, f"{type(e).__name__}: {str(e)[:200]}", "server_error", None, "internal_error")
        finally:
            RUNS.pop(self.control, None)
            self.put("done")

    # —— 关卡：包着 tools.gate，撤不回的照样出卡；调用方说了能问用户（wait）就当场等这张卡有结果 ——

    async def gate(self, tool, args: dict, ctx) -> str | dict | None:
        verdict = await tools_mod.gate(tool, args, ctx)
        if not (isinstance(verdict, dict) and verdict.get("status") == "approval-pending"):
            return verdict
        iid = str(verdict.get("inbox") or "")
        card = self.card(iid, tool.name, args, ctx.call_id)
        self.put("approval", dict(card))
        if not card["waiting"]:
            return verdict
        got = await self.wait_for(iid)
        if got is None:  # 没等到（超时、断开、停了）：卡照样在收件箱里等
            return verdict
        card["outcome"] = got["outcome"]
        self.put("approval_result", {"id": ctx.call_id, "inbox": iid, **got})
        return verdict_of(iid, got)

    def card(self, iid: str, tool: str, args: dict, call_id: str) -> dict:
        import inbox
        try:
            it: dict = inbox.item(iid)
        except HTTPException:
            it = {}
        argv = [str(x) for x in args.get("args") or []] if isinstance(args.get("args"), list) else []
        mine = owns_card(self.caller, iid)
        approvable = mine and approve_allowed() and it.get("status") == "pending"
        c = {"id": call_id, "inbox": iid, "tool": tool, "title": it.get("title") or tool, "why": it.get("why") or "",
             "changes": it.get("changes") or [], "key": tools_mod.classify(tool, argv)[1], "approvable": approvable,
             "waiting": self.wait and approvable}
        self.cards[iid] = c
        return c

    async def wait_for(self, iid: str) -> dict | None:
        WAITING[iid] = WAITING.get(iid, 0) + 1
        try:
            end = time.monotonic() + wait_seconds()
            while not self.control.aborted.is_set():
                got = decision(iid)
                if got is not None or time.monotonic() >= end:
                    return got
                try:
                    await asyncio.wait_for(self.control.aborted.wait(), POLL)
                except TimeoutError:
                    pass
            return None
        finally:
            n = WAITING.get(iid, 0) - 1
            if n > 0:
                WAITING[iid] = n
            else:
                WAITING.pop(iid, None)

    def pending(self) -> list[dict]:
        """这一轮交的卡里现在还在等点头的。"""
        return [{k: c[k] for k in ("inbox", "tool", "title", "approvable")} for c in self.cards.values()
                if not c.get("outcome") and decision(c["inbox"]) is None]

    # —— 回给调用方 ——

    def completion(self, cid: str, created: int, mid: str) -> dict:
        res = self.result
        pend = self.pending()
        said = "".join(self.text)
        msg: dict = {"role": "assistant", "content": said + pending_note(pend, bool(said))}
        if self.thinking:
            msg["reasoning_content"] = "".join(self.thinking)
        return {"id": cid, "object": "chat.completion", "created": created, "model": mid,
                "choices": [{"index": 0, "message": msg, "finish_reason": FINISH.get(res.status, "stop")}], "usage": usage_of(res.usage),
                "openmousse": {"session": self.key, "turn": res.turn, "status": res.status, "pending": pend}}

    async def sse(self, cid: str, created: int, mid: str, include_usage: bool, events: bool) -> AsyncIterator[bytes]:
        task = asyncio.create_task(self.go())

        def chunk(delta: dict, finish: str | None = None) -> bytes:
            return frame({"id": cid, "object": "chat.completion.chunk", "created": created, "model": mid,
                          "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]})
        try:
            yield chunk({"role": "assistant", "content": ""})
            while True:
                try:
                    kind, data = await asyncio.wait_for(self.q.get(), KEEPALIVE)
                except TimeoutError:
                    yield b": keep-alive\n\n"
                    continue
                if kind == "done":
                    break
                if kind == "text":
                    yield chunk({"content": data})
                elif kind == "thinking":
                    yield chunk({"reasoning_content": data})
                elif events:
                    yield frame(data, f"openmousse.{kind}")
            if self.error is not None:
                yield frame(self.error.body())
                yield b"data: [DONE]\n\n"
                return
            res = self.result
            pend = self.pending()
            if note := pending_note(pend, bool(self.text)):
                yield chunk({"content": note})
            yield chunk({}, FINISH.get(res.status, "stop"))
            if include_usage:
                yield frame({"id": cid, "object": "chat.completion.chunk", "created": created, "model": mid, "choices": [],
                             "usage": usage_of(res.usage)})
            if events:
                yield frame({"session": self.key, "turn": res.turn, "status": res.status, "pending": pend}, "openmousse.done")
            yield b"data: [DONE]\n\n"
        finally:
            if not task.done():  # 调用方断开了（或者关服务）：同 app 里点停；GRACE 秒还没收尾就直接取消
                self.control.abort()
                asyncio.get_running_loop().call_later(GRACE, _cancel, task)


def owns_card(caller: Caller, iid: str) -> bool:
    """这张卡是不是这把令牌自己的会话里交的（engine_calls → 那一轮 → 对话 → 会话表里记的令牌）。"""
    conv = tools_mod.api_conversation(tools_mod.call_of(iid))
    return conv is not None and session_owner(conv) == caller.token


def stop_all() -> int:
    """关服务（chat.stop_engine_runs）：正在跑的都停下，像断开连接一样（出来的字留着、会话补完整），会话里补一句被重启打断了。"""
    n = 0
    for c, r in list(RUNS.items()):
        if not c.aborted.is_set():
            r.shutdown = True
            c.abort()
            n += 1
    return n


def busy() -> bool:
    return bool(RUNS)


# —— 接口 ————————————————————————————————————————————————————————————————————

api = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


@api.exception_handler(ApiError)
async def _api_error(_request: Request, e: ApiError) -> JSONResponse:
    return e.response()


@api.exception_handler(StarletteHTTPException)
async def _http_error(_request: Request, e: StarletteHTTPException) -> JSONResponse:
    code = {404: "not_found", 405: "method_not_allowed"}.get(e.status_code)
    return ApiError(e.status_code, str(e.detail), "invalid_request_error" if e.status_code < 500 else "server_error", None, code).response()


@api.exception_handler(RequestValidationError)
async def _bad_request(_request: Request, e: RequestValidationError) -> JSONResponse:
    first = (e.errors() or [{}])[0]
    where = ".".join(str(x) for x in first.get("loc") or [] if x not in ("query", "path", "body")) or None
    return ApiError(400, str(first.get("msg") or "invalid request"), "invalid_request_error", where).response()


async def json_body(request: Request) -> dict:
    n = request.headers.get("content-length") or ""
    if n.isdigit() and int(n) > BODY_MAX:
        raise ApiError(413, L("请求太大了", "The request is too large"), "invalid_request_error", None, "body_too_large")
    data = await request.body()
    if len(data) > BODY_MAX:
        raise ApiError(413, L("请求太大了", "The request is too large"), "invalid_request_error", None, "body_too_large")
    try:
        body = json.loads(data or b"{}")
    except ValueError:
        raise ApiError(400, L("请求体不是合法的 JSON", "The request body isn't valid JSON"), "invalid_request_error", None, "invalid_json") from None
    if not isinstance(body, dict):
        raise ApiError(400, L("请求体要是一个 JSON 对象", "The request body must be a JSON object"), "invalid_request_error")
    return body


def _flag(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


async def hung_up(request: Request, control: Control) -> None:
    """一次性的请求跑着的时候，每秒看一眼调用方还在不在；断开了就停下这一轮（流式的断开由 Run.sse 收尾）。"""
    while not control.aborted.is_set():
        if await request.is_disconnected():
            control.abort()
            return
        await asyncio.sleep(1)


@api.get("/v1/models")
async def list_models(request: Request):
    caller = caller_of(request)
    return JSONResponse({"object": "list", "data": [model_card(a, n) for a, n in visible(caller).items()]},
                        headers={"Cache-Control": "no-store"})


@api.get("/v1/models/{mid:path}")
async def get_model(mid: str, request: Request):
    caller = caller_of(request)
    for a, n in visible(caller).items():
        if model_id(a) == mid:
            return JSONResponse(model_card(a, n), headers={"Cache-Control": "no-store"})
    raise ApiError(404, L(f"没有 {mid} 这个模型", f"The model {mid!r} does not exist"), "invalid_request_error", "model", "model_not_found")


@api.post("/v1/chat/completions")
async def chat_completions(request: Request):
    caller = caller_of(request)
    body = await json_body(request)
    agent = pick(caller, body.get("model"))
    content, history = split_messages(body.get("messages"))
    key, _ = session_key(request, body)
    # 请求头只能是 latin-1：user 字段里的中文这类键不放头里（正文的 openmousse.session 照样有；用 user 的调用方本来也不靠这个头）
    headers = {**({"X-Session-Id": key} if key.isascii() else {}), "Cache-Control": "no-store"}
    model = model_for(agent)
    if model is None:
        raise ApiError(503, no_model(agent), "server_error", None, "engine_config", headers)
    conv = conversation(caller.token, agent, key)
    cwd = urllib.parse.unquote((request.headers.get("x-openmousse-cwd") or "").strip())[:1000] or None  # 请求头只能是 latin-1：路径先 %-编码
    open_session(caller, agent, key, conv, title_of(content), cwd)
    stream = body.get("stream") is True
    wait = stream and (request.headers.get("x-openmousse-approvals") or "").strip().lower() == "wait"
    run = Run(caller, agent, key, conv, content, history, model, wait=wait)
    cid, created, mid = "chatcmpl-" + uuid.uuid4().hex[:24], int(time.time()), model_id(agent)
    if stream:
        opts = body.get("stream_options")
        include_usage = isinstance(opts, dict) and opts.get("include_usage") is True
        return StreamingResponse(run.sse(cid, created, mid, include_usage, _flag(request.headers.get("x-openmousse-events"))),
                                 media_type="text/event-stream", headers={**headers, "X-Accel-Buffering": "no"})
    watch = asyncio.create_task(hung_up(request, run.control))  # 一次性的请求也一样：调用方断开 = 点了停（OpenClaw 也这样）
    try:
        await run.go()
    finally:
        watch.cancel()
    if run.error is not None:
        run.error.headers.update(headers)
        raise run.error
    if run.result.status == "aborted":
        raise ApiError(503, L("这一轮被停下了（服务在重启）", "The turn was stopped (the server is restarting)"), "server_error", None, "aborted",
                       headers)
    return JSONResponse(run.completion(cid, created, mid), headers=headers)


@api.get("/v1/openmousse/sessions")
async def sessions(request: Request, agent: str | None = None, cwd: str | None = None, id: str | None = None, limit: int = 50,  # noqa: A002
                   before: str | None = None):
    caller = caller_of(request)
    if caller.agent is not None and agent and agent != caller.agent:
        return JSONResponse({"object": "list", "data": [], "has_more": False}, headers={"Cache-Control": "no-store"})
    n = max(1, min(200, limit))
    rows = list_sessions(caller.token, agent=agent or caller.agent, cwd=cwd, key=id, limit=n, before=before)
    return JSONResponse({"object": "list", "data": rows, "has_more": len(rows) == n}, headers={"Cache-Control": "no-store"})


@api.post("/v1/openmousse/approvals/{iid}")
async def decide(iid: str, request: Request):
    """对这把令牌自己的会话里交的卡点头 / 拒绝（ACP 的 session/request_permission 点的结果）。和 app 里点一样走 inbox.act。"""
    import inbox
    caller = caller_of(request)
    if not approve_allowed():
        raise ApiError(403, L("这边不能点头（server.json 的 engine.api.approve 是 false）：到 app 的收件箱里点",
                              "Approvals are turned off here (engine.api.approve is false in server.json): use the app's inbox"),
                       "permission_error", None, "approvals_off")
    if not owns_card(caller, iid):
        raise ApiError(404, L("这把令牌的会话里没有这张卡", "No such approval card for this token's sessions"), "invalid_request_error", None,
                       "approval_not_found")
    body = await json_body(request)
    action = str(body.get("action") or "").strip().lower()
    action = {"allow": "approve", "deny": "reject"}.get(action, action)
    if action not in ("approve", "reject"):
        raise ApiError(400, L("action 只能是 approve 或 reject", "action must be approve or reject"), "invalid_request_error", "action")
    try:
        out = await inbox.act(iid, inbox.ActIn(action=action, note=str(body.get("note") or "")[:1000]))
    except HTTPException as e:
        raise ApiError(e.status_code, str(e.detail), "invalid_request_error", None, "approval_not_pending" if e.status_code == 409 else None) from None
    it = out.get("item") or {}
    return JSONResponse({"object": "openmousse.approval", "inbox": iid, "status": it.get("status"), "result": it.get("result") or ""},
                        headers={"Cache-Control": "no-store"})


# —— /v1/responses（第 7 批 12.2，2026-10-19）—————————————————————————————————————————————————
# OpenAI 的 Responses API（照 Hermes 的 gateway/platforms/api_server_openai_routes.py、api_server_response_store.py 和 OpenAI 的
# 接口说明自己写的，没有拿代码）。开关单独一个：engine.api 开着、而且 engine.api.responses = true 才有，不然这几条一律 404。
#   POST   /v1/responses          跑一轮；令牌、Agent 名单、收件箱点头、会话都照 /v1/chat/completions
#   GET    /v1/responses/{id}     拿回存下的那一次（只看得到自己这把令牌的；别人的、删了的、store: false 的 404）
#   DELETE /v1/responses/{id}     删掉（同上，只删得了自己的）
# 请求：model 同上（openmousse/<agent id>）；input = 字，或者 item 的列表（{"role", "content"} 的消息，content 是字或者 input_text /
#   input_image 块；散着的 input_text / input_image 块当用户的一句）；别的 item（function_call_output、input_file……）400。最后一条 user
#   是这一句，前面的照 chat/completions 当前情（只在建会话时用）。instructions = 这一轮额外的说明（叠在 Agent 自己的开头之上，只管这一轮，
#   照 OpenAI：不跟着 previous_response_id 带到下一轮）。previous_response_id = 接上那一次的会话（同一把令牌的；别人的、没有的 404）；
#   不带就照 chat/completions 看 X-Session-Id、user，都没有就是新会话。store: false = 不存（GET 拿不回、也接不上）。
#   调用方带来的 tools、tool_choice、temperature、max_output_tokens、text.format、background 这些不管：Agent 用自己的工具和设置，当场跑完。
# 回：response 对象（id resp_…、status completed / incomplete（incomplete_details.reason：max_output_tokens、content_filter = 拒答、
#   interrupted = 断开或关服务停下的）/ failed、output 里一条 assistant 的 message、usage 照 Responses 的字段 input_tokens / output_tokens /
#   total_tokens（含缓存读的 input_tokens_details.cached_tokens），多给一样 openmousse（同 chat/completions））。一次性的出错照 chat/completions
#   回 HTTP 的错（不存）。
#   stream = true：Responses 的 SSE 事件（event: + data 里的 type、sequence_number）：response.created → response.in_progress →
#   response.output_item.added → response.content_part.added → response.output_text.delta … → response.output_text.done →
#   response.content_part.done → response.output_item.done → response.completed / response.incomplete（出错 response.failed，也存下）；
#   X-OpenMousse-Events: 1 照样多给 openmousse.tool / approval / approval_result / done。思考摘要不放进 output。
# 存：mousse.db 的 engine_api_responses（用到时才建），每把令牌最多 RESP_KEEP 条，多了删最旧的；重启不丢。

RESP_KEEP = 100
RESP_SCHEMA = """
CREATE TABLE IF NOT EXISTS engine_api_responses (
  id TEXT PRIMARY KEY, token TEXT NOT NULL, agent TEXT NOT NULL, key TEXT NOT NULL, conversation TEXT NOT NULL, created_at INTEGER NOT NULL,
  data TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS engine_api_responses_token ON engine_api_responses(token, created_at);
"""
RESP_NOTE = ("The program calling you through the API added these instructions for this reply only. Follow them where they don't conflict "
             "with your own rules:\n\n")
# 这一轮怎么收的尾（Result.status）→ (Responses 的 status, incomplete_details.reason)；interrupted 是自己加的（断开、关服务停下的）
RESP_STATUS = {"done": ("completed", None), "refused": ("incomplete", "content_filter"), "length": ("incomplete", "max_output_tokens"),
               "max_steps": ("incomplete", "max_output_tokens"), "aborted": ("incomplete", "interrupted")}
_resp_ready: set[str] = set()


def responses_on() -> bool:
    return (cfg() or {}).get("responses") is True


def responses_gate() -> None:
    if not responses_on():
        raise ApiError(404, L("/v1/responses 没开：server.json 的 engine.api.responses 写 true", "/v1/responses is off: set engine.api.responses to "
                              "true in server.json"), "invalid_request_error", None, "not_found")


def _resp_db() -> None:
    path = str(settings.db)
    if path in _resp_ready:
        return
    with store.tx() as conn:
        conn.executescript(RESP_SCHEMA)
    _resp_ready.add(path)


def resp_save(rid: str, caller: Caller, agent: str, key: str, conv: str, created: int, obj: dict) -> None:
    """存下这一次（同一把令牌最多 RESP_KEEP 条，多了删最旧的）。"""
    _resp_db()
    with store.tx() as conn:
        conn.execute("INSERT OR REPLACE INTO engine_api_responses(id, token, agent, key, conversation, created_at, data) VALUES(?,?,?,?,?,?,?)",
                     (rid, caller.token, agent, key, conv, created, json.dumps(obj, ensure_ascii=False)))
        conn.execute("""DELETE FROM engine_api_responses WHERE token=? AND id NOT IN (
            SELECT id FROM engine_api_responses WHERE token=? ORDER BY created_at DESC, rowid DESC LIMIT ?)""", (caller.token, caller.token, RESP_KEEP))


def resp_row(caller: Caller, rid: str):
    """这把令牌存下的那一次（别人的、没有的 → None）。"""
    _resp_db()
    with store.read() as conn:
        return conn.execute("SELECT * FROM engine_api_responses WHERE id=? AND token=?", (str(rid), caller.token)).fetchone()


def resp_not_found(rid: str, param: str | None = None) -> ApiError:
    return ApiError(404, L(f"没有 {rid} 这一次（或者不是这把令牌的）", f"No response {rid!r} for this token"), "invalid_request_error", param,
                    "response_not_found")


def resp_parts(c: Any, where: str) -> str | list:
    """Responses 的 content → 和 chat/completions 一样的写法：字，或者 text / image_url 块。input_text、output_text（前情里 assistant 说的）
    → text，input_image（image_url 写成字符串或者 {"url"}）→ image_url；别的 400。"""
    if c is None or isinstance(c, str):
        return c or ""
    if not isinstance(c, list):
        raise ApiError(400, L(f"{where} 要是字符串或块的列表", f"{where} must be a string or a list of parts"), "invalid_request_error", where,
                       "invalid_content")
    out: list[dict] = []
    for j, p in enumerate(c):
        t = p.get("type") if isinstance(p, dict) else None
        if t in ("input_text", "output_text", "text"):
            out.append({"type": "text", "text": str(p.get("text") or "")})
        elif t == "input_image":
            url = p.get("image_url")
            url = url.get("url") if isinstance(url, dict) else url
            if not isinstance(url, str) or not url:
                raise ApiError(400, L(f"{where}[{j}]：input_image 要写 image_url（file_id 不认）", f"{where}[{j}]: input_image needs image_url "
                                      "(file_id isn't supported)"), "invalid_request_error", f"{where}[{j}]", "invalid_content")
            out.append({"type": "image_url", "image_url": {"url": url}})
        else:
            raise ApiError(400, L(f"{where}[{j}]：这里只认 input_text 和 input_image", f"{where}[{j}]: only input_text and input_image parts are "
                                  "supported"), "invalid_request_error", f"{where}[{j}]", "unsupported_content_type")
    return out


def resp_input(raw_input: Any) -> tuple[str | list, list[dict]]:
    """input → (这一句, 前情)：先换成 chat/completions 的 messages，再照 split_messages。"""
    if isinstance(raw_input, str):
        if not raw_input.strip():
            raise ApiError(400, L("input 是空的", "input is empty"), "invalid_request_error", "input", "missing_input")
        return raw_input, []
    if not isinstance(raw_input, list) or not raw_input:
        raise ApiError(400, L("input 要是字，或者一个非空的 item 列表", "input must be a string or a non-empty list of items"), "invalid_request_error",
                       "input", "invalid_input")
    msgs: list[dict] = []
    for i, item in enumerate(raw_input):
        where = f"input[{i}]"
        if isinstance(item, str):
            msgs.append({"role": "user", "content": item})
            continue
        if not isinstance(item, dict):
            raise ApiError(400, L(f"{where} 要是对象", f"{where} must be an object"), "invalid_request_error", where, "invalid_input")
        t = item.get("type")
        if t in ("input_text", "input_image"):
            msgs.append({"role": "user", "content": resp_parts([item], where)})
            continue
        if t not in (None, "message"):
            raise ApiError(400, L(f"{where}：不认 {t} 这种 item（这里只有消息、input_text、input_image）", f"{where}: {t} items aren't supported "
                                  "(only messages, input_text and input_image)"), "invalid_request_error", where, "unsupported_input_type")
        role = item.get("role") or "user"
        if role not in ("user", "assistant", "system", "developer"):
            raise ApiError(400, L(f"{where}.role 不认 {role}", f"{where}.role {role!r} isn't supported"), "invalid_request_error", f"{where}.role")
        msgs.append({"role": role, "content": resp_parts(item.get("content"), f"{where}.content")})
    return split_messages(msgs)


def resp_usage(u: dict) -> dict:
    """内核的用量 → Responses 的 usage（input_tokens 连缓存读、写都算上，同 usage_of）。"""
    cr = int(u.get("cache_read") or 0)
    i = int(u.get("in") or 0) + cr + int(u.get("cache_write") or 0)
    o = int(u.get("out") or 0)
    return {"input_tokens": i, "input_tokens_details": {"cached_tokens": cr}, "output_tokens": o, "output_tokens_details": {"reasoning_tokens": 0},
            "total_tokens": i + o}


def resp_message(mid: str, text: str, status: str = "completed") -> dict:
    return {"id": mid, "type": "message", "status": status, "role": "assistant", "content": [{"type": "output_text", "text": text, "annotations": []}]}


def resp_object(rid: str, created: int, model: str, status: str, output: list, *, prev: str | None, instructions: str | None, store_it: bool,
                usage: dict | None = None, error: dict | None = None, extra: dict | None = None, reason: str | None = None) -> dict:
    out = {"id": rid, "object": "response", "created_at": created, "status": status, "model": model, "output": output,
           "error": error, "incomplete_details": {"reason": reason} if status == "incomplete" else None,
           "instructions": instructions, "previous_response_id": prev, "parallel_tool_calls": False, "tool_choice": "auto", "tools": [],
           "temperature": None, "top_p": None, "metadata": {}, "store": store_it,
           "usage": usage if usage is not None else {"input_tokens": 0, "input_tokens_details": {"cached_tokens": 0}, "output_tokens": 0,
                                                       "output_tokens_details": {"reasoning_tokens": 0}, "total_tokens": 0}}
    if extra:
        out["openmousse"] = extra
    return out


@dataclass
class RespReq:
    caller: Caller
    agent: str
    key: str
    conv: str
    rid: str
    msg_id: str
    created: int
    model: str              # openmousse/<agent>
    prev: str | None
    instructions: str | None
    store_it: bool


def resp_finish(r: RespReq, run: Run) -> dict:
    """跑完了：→ response 对象（存下来）。"""
    res = run.result
    pend = run.pending()
    said = "".join(run.text)
    text = said + pending_note(pend, bool(said))
    status, reason = RESP_STATUS.get(res.status, ("completed", None))
    obj = resp_object(r.rid, r.created, r.model, status, [resp_message(r.msg_id, text, "completed" if status == "completed" else "incomplete")],
                      prev=r.prev, instructions=r.instructions, store_it=r.store_it, usage=resp_usage(res.usage), reason=reason,
                      extra={"session": r.key, "turn": res.turn, "status": res.status, "pending": pend})
    if r.store_it:
        resp_save(r.rid, r.caller, r.agent, r.key, r.conv, r.created, obj)
    return obj


def resp_failed(r: RespReq, run: Run) -> dict:
    """流式里出错了：→ status failed 的 response 对象（存下来：调用方从 response.failed 里拿到了 id）。"""
    e = run.error
    said = "".join(run.text)
    obj = resp_object(r.rid, r.created, r.model, "failed", [resp_message(r.msg_id, said, "incomplete")] if said else [], prev=r.prev,
                      instructions=r.instructions, store_it=r.store_it, error={"code": (e.code if e else None) or "server_error",
                                                                                "message": e.message if e else "error"})
    if r.store_it:
        resp_save(r.rid, r.caller, r.agent, r.key, r.conv, r.created, obj)
    return obj


async def resp_sse(r: RespReq, run: Run, events: bool) -> AsyncIterator[bytes]:
    """Responses 的 SSE（见上面的说明）。断开 = 停（同 Run.sse）。"""
    task = asyncio.create_task(run.go())
    seq = 0

    def ev(kind: str, data: dict) -> bytes:
        nonlocal seq
        out = frame({"type": kind, "sequence_number": seq, **data}, kind)
        seq += 1
        return out

    def opening() -> list[bytes]:
        return [ev("response.output_item.added", {"output_index": 0, "item": {**resp_message(r.msg_id, "", "in_progress"), "content": []}}),
                ev("response.content_part.added", {"item_id": r.msg_id, "output_index": 0, "content_index": 0,
                                                   "part": {"type": "output_text", "text": "", "annotations": []}})]
    head = resp_object(r.rid, r.created, r.model, "in_progress", [], prev=r.prev, instructions=r.instructions, store_it=r.store_it)
    opened = False
    try:
        yield ev("response.created", {"response": head})
        yield ev("response.in_progress", {"response": head})
        while True:
            try:
                kind, data = await asyncio.wait_for(run.q.get(), KEEPALIVE)
            except TimeoutError:
                yield b": keep-alive\n\n"
                continue
            if kind == "done":
                break
            if kind == "text":
                if not opened:
                    for b in opening():
                        yield b
                    opened = True
                yield ev("response.output_text.delta", {"item_id": r.msg_id, "output_index": 0, "content_index": 0, "delta": data, "logprobs": []})
            elif events and kind in ("tool", "approval", "approval_result"):
                yield frame(data, f"openmousse.{kind}")
        if run.error is not None:
            yield ev("response.failed", {"response": resp_failed(r, run)})
            return
        pend = run.pending()
        note = pending_note(pend, bool(run.text))
        if not opened:
            for b in opening():
                yield b
        if note:
            yield ev("response.output_text.delta", {"item_id": r.msg_id, "output_index": 0, "content_index": 0, "delta": note, "logprobs": []})
        obj = resp_finish(r, run)
        text = obj["output"][0]["content"][0]["text"]
        yield ev("response.output_text.done", {"item_id": r.msg_id, "output_index": 0, "content_index": 0, "text": text, "logprobs": []})
        yield ev("response.content_part.done", {"item_id": r.msg_id, "output_index": 0, "content_index": 0,
                                                "part": {"type": "output_text", "text": text, "annotations": []}})
        yield ev("response.output_item.done", {"output_index": 0, "item": obj["output"][0]})
        yield ev("response.completed" if obj["status"] == "completed" else "response.incomplete", {"response": obj})
        if events:
            yield frame(obj["openmousse"], "openmousse.done")
    finally:
        if not task.done():
            run.control.abort()
            asyncio.get_running_loop().call_later(GRACE, _cancel, task)


@api.post("/v1/responses")
async def create_response(request: Request):
    caller = caller_of(request)
    responses_gate()
    body = await json_body(request)
    prev = body.get("previous_response_id")
    if prev is not None and (not isinstance(prev, str) or not prev.strip()):
        raise ApiError(400, L("previous_response_id 要是字符串", "previous_response_id must be a string"), "invalid_request_error", "previous_response_id")
    instructions = body.get("instructions")
    if instructions is not None and not isinstance(instructions, str):
        raise ApiError(400, L("instructions 要是字符串", "instructions must be a string"), "invalid_request_error", "instructions")
    content, history = resp_input(body.get("input"))
    if prev:
        row = resp_row(caller, prev)
        if row is None:
            raise resp_not_found(prev, "previous_response_id")
        agent = row["agent"]
        if body.get("model") not in (None, "", MODEL) and pick(caller, body.get("model")) != agent:
            raise ApiError(400, L(f"previous_response_id 是 {model_id(agent)} 的，model 不一样", f"previous_response_id belongs to {model_id(agent)}; "
                                  "the model differs"), "invalid_request_error", "model")
        key = row["key"]
    else:
        agent = pick(caller, body.get("model"))
        key, _ = session_key(request, body)
    headers = {**({"X-Session-Id": key} if key.isascii() else {}), "Cache-Control": "no-store"}
    model = model_for(agent)
    if model is None:
        raise ApiError(503, no_model(agent), "server_error", None, "engine_config", headers)
    conv = conversation(caller.token, agent, key)
    cwd = urllib.parse.unquote((request.headers.get("x-openmousse-cwd") or "").strip())[:1000] or None
    open_session(caller, agent, key, conv, title_of(content), cwd)
    if instructions and instructions.strip():  # 这一轮额外的说明：放在这一句前面，标明是调用方的（只管这一轮）
        note = RESP_NOTE + instructions.strip()
        content = f"{note}\n\n---\n\n{content}" if isinstance(content, str) else [{"type": "text", "text": note + "\n\n---"}, *content]
    stream = body.get("stream") is True
    wait = stream and (request.headers.get("x-openmousse-approvals") or "").strip().lower() == "wait"
    run = Run(caller, agent, key, conv, content, history, model, wait=wait)
    r = RespReq(caller, agent, key, conv, "resp_" + uuid.uuid4().hex, "msg_" + uuid.uuid4().hex[:24], int(time.time()), model_id(agent),
                prev or None, instructions, body.get("store") is not False)
    if stream:
        return StreamingResponse(resp_sse(r, run, _flag(request.headers.get("x-openmousse-events"))), media_type="text/event-stream",
                                 headers={**headers, "X-Accel-Buffering": "no"})
    watch = asyncio.create_task(hung_up(request, run.control))
    try:
        await run.go()
    finally:
        watch.cancel()
    if run.error is not None:  # 一次性的出错：照 chat/completions 回 HTTP 的错（拿不到 id，不存）
        run.error.headers.update(headers)
        raise run.error
    if run.result.status == "aborted" and run.shutdown:
        raise ApiError(503, L("这一轮被停下了（服务在重启）", "The turn was stopped (the server is restarting)"), "server_error", None, "aborted", headers)
    return JSONResponse(resp_finish(r, run), headers=headers)


@api.get("/v1/responses/{rid}")
async def get_response(rid: str, request: Request):
    caller = caller_of(request)
    responses_gate()
    row = resp_row(caller, rid)
    if row is None:
        raise resp_not_found(rid)
    return JSONResponse(json.loads(row["data"]), headers={"Cache-Control": "no-store"})


@api.delete("/v1/responses/{rid}")
async def delete_response(rid: str, request: Request):
    caller = caller_of(request)
    responses_gate()
    if resp_row(caller, rid) is None:
        raise resp_not_found(rid)
    with store.tx() as conn:
        conn.execute("DELETE FROM engine_api_responses WHERE id=? AND token=?", (rid, caller.token))
    return JSONResponse({"id": rid, "object": "response", "deleted": True}, headers={"Cache-Control": "no-store"})


# —— 会话记录（第 7 批 12.3，2026-10-19：ACP 的 session/load 要重放）——————————————————————————————————————
#   GET /v1/openmousse/history?id=<会话键>[&agent=<agent id>][&limit=200]
# 只看得到这把令牌自己的会话（照 list_sessions 找、对话 id 里本来就有令牌名的指纹）：别人的、没有的 404。agent 不写 = 这个键最近用的那个
# Agent。回这个对话里说过的话，按先后，旧的在前（每天一条会话，几天的接起来；压缩过的照原样，摘要不算）：
#   {"type": "user", "text", "images": 几张图, "at"}、{"type": "assistant", "text"}、{"type": "thinking", "text"}、
#   {"type": "tool", "id", "name", "status": completed | failed | pending（没有结果的）, "output": 结果的开头}
# 多了只给最近 limit 条（最多 HISTORY_MAX），truncated = true。一条字最多 HISTORY_TEXT_MAX 字、工具结果 HISTORY_TOOL_MAX 字（后面写明截了）。
# 开头的规矩、中间补的 system、调用方带的说明（CLIENT_NOTE）不给。

HISTORY_DEFAULT, HISTORY_MAX = 200, 1000
HISTORY_TEXT_MAX, HISTORY_TOOL_MAX = 20_000, 2_000


def _clip(text: str, n: int) -> str:
    return text if len(text) <= n else text[:n] + f"… [{len(text) - n} more characters]"


def history(conv: str, limit: int) -> tuple[list[dict], bool]:
    """这个对话说过的话（见上）→ (最近 limit 条, 前面还有没有)。"""
    with store.read() as conn:
        rows = conn.execute("SELECT id FROM engine_sessions WHERE key>=? AND key<? ORDER BY key, created_at, rowid",
                            (f"mousse:{conv}:", f"mousse:{conv};")).fetchall()
    items: list[dict] = []
    calls: dict[str, dict] = {}
    for r in rows:
        s = DbSession.get(r["id"])
        for e in s.path() if s else []:
            if e["kind"] != "message":
                continue
            try:
                m = json.loads(e["data"])
            except ValueError:
                continue
            role = m.get("role")
            if role == "user" and not m.get("compaction"):
                blocks = m.get("content") or []
                items.append({"type": "user", "text": _clip(text_of(m), HISTORY_TEXT_MAX), "images": sum(1 for b in blocks if b.get("type") == "image"),
                              "at": e["ts"]})
            elif role == "assistant":
                for b in m.get("content") or []:
                    t = b.get("type")
                    if t == "text" and (b.get("text") or "").strip():
                        items.append({"type": "assistant", "text": _clip(b["text"], HISTORY_TEXT_MAX)})
                    elif t == "thinking" and (b.get("text") or "").strip() and not b.get("redacted"):
                        items.append({"type": "thinking", "text": _clip(b["text"], HISTORY_TEXT_MAX)})
                    elif t == "tool_call" and b.get("id"):
                        calls[str(b["id"])] = item = {"type": "tool", "id": str(b["id"]), "name": str(b.get("name") or ""), "status": "pending",
                                                      "output": ""}
                        items.append(item)
            elif role == "tool" and str(m.get("id") or "") in calls:
                item = calls[str(m["id"])]
                item["status"] = "failed" if m.get("error") else "completed"
                item["output"] = _clip(text_of(m), HISTORY_TOOL_MAX)
    return items[-limit:], len(items) > limit


@api.get("/v1/openmousse/history")
async def session_history(request: Request, id: str, agent: str | None = None, limit: int = HISTORY_DEFAULT):  # noqa: A002
    caller = caller_of(request)
    key = id.strip()
    if not key or len(key) > KEY_MAX or any(ord(c) < 32 or ord(c) == 127 for c in key):
        raise ApiError(400, L("id 不像会话键", "id isn't a session key"), "invalid_request_error", "id", "invalid_session_id")
    missing = ApiError(404, L("这把令牌没有这个会话", "No such session for this token"), "invalid_request_error", "id", "session_not_found")
    if caller.agent is not None and agent and agent != caller.agent:
        raise missing
    rows = list_sessions(caller.token, agent=agent or caller.agent, key=key, limit=1)
    if not rows:
        raise missing
    who = rows[0]["agent"]
    items, more = history(conversation(caller.token, who, key), max(1, min(HISTORY_MAX, limit)))
    return JSONResponse({"object": "list", "session": key, "agent": who, "model": model_id(who), "data": items, "truncated": more},
                        headers={"Cache-Control": "no-store"})


class Gate:
    """main.py 把它加在最外层（app.add_middleware）：/v1/… 而且开着（enabled）才接，交给上面的 api；不然原样往下交，和没有它一样。"""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") == "http" and str(scope.get("path") or "").startswith("/v1/") and enabled():
            lang = next((v.decode("latin-1") for k, v in scope.get("headers") or [] if k.lower() == b"accept-language"), None)
            tok = i18n.use(lang)
            try:
                await api(scope, receive, send)
            finally:
                i18n.reset(tok)
            return
        await self.app(scope, receive, send)


from . import a2a_api  # noqa: E402,F401 — 第 7 批：/v1/a2a（主人自己的 A2A）那几条挂在上面的 api 上
