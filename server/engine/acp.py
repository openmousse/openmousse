#!/usr/bin/env python3
"""ACP 适配器（Agent Client Protocol v1，2026-10-16）：编辑器（Zed 之类）以 stdio 起这个小进程，把 OpenMousse 的一个 Agent 当成编辑器里的
agent 用。它只经对外接口（engine/openai_api.py 的 /v1，令牌是 api 或 api-<agent id>）跟服务器说话，不导入服务器的任何模块，只用标准库：
拷到哪台机器上都能跑（服务器那边要开 server.json 的 engine.api）。

  python3 server/engine/acp.py --url http://127.0.0.1:8080 --token <api 令牌> [--agent main]
  令牌也可以放环境变量 OPENMOUSSE_TOKEN（地址 OPENMOUSSE_URL、Agent OPENMOUSSE_AGENT），免得写进编辑器的设置。
  Zed 的 settings.json：
    "agent_servers": {"OpenMousse": {"command": "python3", "args": ["<仓库>/server/engine/acp.py", "--url", "http://127.0.0.1:8080"],
                                     "env": {"OPENMOUSSE_TOKEN": "<api 令牌>"}}}

协议：JSON-RPC 2.0，一行一个 JSON（stdout 只放协议，出错的说明写 stderr）。
  initialize                 protocolVersion 1；会话能 list / resume / close；提示词收字、图片、嵌进来的文字资源（不收音频）
  authenticate               没有要登录的（令牌在启动参数里）：回 {}
  session/new                新会话：sessionId 就是交给服务器的会话键（X-Session-Id）。configOptions 里有一项 agent（选哪个 Agent，
                             api-<id> 的令牌只有它自己）。cwd 只记在服务器的会话上（session/list 按它分），mcpServers 不接
  session/list               服务器上这把令牌的会话（/v1/openmousse/sessions），新的在前，翻页用 nextCursor
  session/resume             接上一个会话：服务器那边还记着，不重放记录
  session/close              停掉在跑的那一轮（同 session/cancel），这边忘掉它；服务器上的会话还在，能再 resume
  session/prompt             POST /v1/chat/completions（流式，带 X-OpenMousse-Events: 1、X-OpenMousse-Approvals: wait）：
                               字 → agent_message_chunk，思考摘要 → agent_thought_chunk，工具开始执行 → tool_call（in_progress），
                               做完 → tool_call_update（completed / failed；在等点头的停在 pending）。结束 → stopReason：
                               end_turn；写到上限 max_tokens；拒答 refusal；点了取消 cancelled。服务器报错 → JSON-RPC 错 -32603
  撤不回的调用                服务器出了收件箱卡、关卡当场等着：发 session/request_permission（同意一次 / 这个会话里这一类都同意 /
                             拒绝），用户点的结果 POST /v1/openmousse/approvals/<卡>（= 在 app 里点了那张卡），模型这一轮就拿到真结果
                             接着做。编辑器取消了这次询问、点了不认识的选项：不替用户决定，卡留在 app 的收件箱里
  session/cancel             断开这一轮的连接（服务器那边 = 点了停：出来的字留着、会话补完整），session/prompt 回 cancelled
  session/set_config_option  configId agent：换一个 Agent（同一个 sessionId 在服务器上是另一个对话）
  没有 session/load（要重放记录；用 resume）。不认识的方法回 -32601。

改写自 Hermes（NousResearch/hermes-agent，MIT，Copyright (c) 2025 Nous Research）commit e4635a16a6367f63003feb9c0447c5e6375fbd04：
  acp_adapter/permissions.py（_OPTION_ID_TO_HERMES、_build_permission_options、_map_outcome_to_hermes：选项 once / session / deny
  和它们的 kind，点的结果怎么认）、acp_adapter/events.py（AssistantMessageIdAllocator：一段回复一个 messageId；flush_open_tool_calls：
  一轮结束时还开着的工具调用补一个 failed）、acp_adapter/tools.py（TOOL_KIND_MAP、build_tool_abandoned）。许可全文见仓库根目录
  THIRD_PARTY_NOTICES。和 Hermes 不同：「取消」不算拒绝（卡还在 app 里等）；没有「永远同意」（要不要点头是 app 里的设置）。
线上的格式照 DeepSeek Harness（deepseek-ai/deepseek-harness，MIT）的 packages/acp/acp（src/index.ts、session.ts、updates.ts、codec.ts、
content.ts 和 snapshots/acp 里录下的对话）核对过，没有拿代码。
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import socket
import ssl
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
from itertools import count

PROTOCOL = 1
VERSION = "2026.10.16"
HTTP_TIMEOUT = 30      # 一般的请求（列会话、点头）最多等几秒
READ_TIMEOUT = 180     # 流式这一轮多久一个字节都没有就算断了（服务器每 15 秒发一行保活）
ABANDONED = "This tool call ended without a result (blocked, denied, or interrupted)."
WAITING_APP = "Waiting for your OK in the OpenMousse app's inbox (card {inbox})."

# 改写自 Hermes acp_adapter/permissions.py（见文件开头）：选项 id → 怎么办。ACP 没有「这个会话里」的 kind：用最接近的 allow_always，
# 意思写在 id 里（Hermes 的做法）。没有 allow_always / deny_always 那两项：永远同意、永远拒绝是 app 里的设置
OPTIONS = [{"optionId": "allow_once", "name": "Allow once", "kind": "allow_once"},
           {"optionId": "allow_session", "name": "Allow for this session", "kind": "allow_always"},
           {"optionId": "deny", "name": "Deny", "kind": "reject_once"}]
DECISIONS = {"allow_once": "approve", "allow_session": "approve", "deny": "reject"}
# 工具 → ACP 的 ToolKind（改写自 Hermes acp_adapter/tools.py 的 TOOL_KIND_MAP；没列的、命令行工具都是 other，连接器的 fetch）
KINDS = {"memory_search": "search", "memory_get": "read", "skill": "read"}
FINISH = {"stop": "end_turn", "length": "max_tokens", "content_filter": "refusal"}
DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 直连 --url（流式那条 http.client 本来就不走代理，两边一样）


def log(text: str) -> None:
    print(f"[openmousse-acp] {text}", file=sys.stderr, flush=True)


class RpcError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


def invalid(message: str) -> RpcError:
    return RpcError(-32602, f"Invalid params: {message}")


class ServerError(Exception):
    """服务器那边的错（HTTP 不是 200、流里的 error、连不上）：str 是给人看的一句。"""


# —— stdio 上的 JSON-RPC ——————————————————————————————————————————————————————

class Rpc:
    """一行一个 JSON。读在主线程（serve），写加锁；往编辑器发的请求（session/request_permission）按 id 等回话。"""

    def __init__(self, out) -> None:
        self.out = out
        self.lock = threading.Lock()
        self.ids = count(1)
        self.waiting: dict[int, tuple[threading.Event, list]] = {}

    def send(self, msg: dict) -> None:
        line = json.dumps({"jsonrpc": "2.0", **msg}, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self.lock:
            self.out.write(line.encode("utf-8"))
            self.out.flush()

    def notify(self, method: str, params: dict) -> None:
        self.send({"method": method, "params": params})

    def reply(self, rid, result: dict) -> None:
        self.send({"id": rid, "result": result})

    def fail(self, rid, code: int, message: str) -> None:
        self.send({"id": rid, "error": {"code": code, "message": message}})

    def request(self, method: str, params: dict, stop: threading.Event) -> dict | None:
        """发一个请求等回话（{"result": …} 或 {"error": …}）；stop 设上了（这一轮取消了）就不等了 → None。"""
        rid = next(self.ids)
        done, box = threading.Event(), []
        self.waiting[rid] = (done, box)
        try:
            self.send({"id": rid, "method": method, "params": params})
            while not done.wait(0.2):
                if stop.is_set():
                    return None
            return box[0]
        finally:
            self.waiting.pop(rid, None)

    def resolve(self, msg: dict) -> None:
        w = self.waiting.get(msg.get("id"))  # type: ignore[arg-type]
        if w is not None:
            w[1].append(msg)
            w[0].set()


# —— 服务器（/v1）——————————————————————————————————————————————————————————————

def error_text(status: int, raw: bytes) -> str:
    try:
        e = json.loads(raw or b"{}").get("error")
        msg = e.get("message") if isinstance(e, dict) else e
    except (ValueError, AttributeError):
        msg = None
    return f"HTTP {status}: {msg or raw[:200].decode('utf-8', 'replace') or 'error'}"


class Server:
    def __init__(self, url: str, token: str) -> None:
        self.url = url.rstrip("/")
        self.token = token
        u = urllib.parse.urlsplit(self.url)
        if u.scheme not in ("http", "https") or not u.hostname:
            raise SystemExit(f"--url should look like http://host:port, got {url!r}")
        self.parts = u

    def headers(self, extra: dict | None = None) -> dict:
        return {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json", "Accept": "application/json", **(extra or {})}

    def call(self, method: str, path: str, body: dict | None = None) -> dict:
        req = urllib.request.Request(self.url + path, method=method, headers=self.headers(),
                                     data=None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8"))
        try:
            with DIRECT.open(req, timeout=HTTP_TIMEOUT) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            raise ServerError(error_text(e.code, e.read())) from None
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise ServerError(f"can't reach {self.url}: {e}") from None

    def connect(self) -> http.client.HTTPConnection:
        p = self.parts
        if p.scheme == "https":
            return http.client.HTTPSConnection(p.hostname, p.port or 443, timeout=READ_TIMEOUT, context=ssl.create_default_context())
        return http.client.HTTPConnection(p.hostname, p.port or 80, timeout=READ_TIMEOUT)

    def path(self, path: str) -> str:
        return (self.parts.path.rstrip("/") + path) or "/"


def sse(resp):
    """读 SSE：→ (事件名, data)。注释行（: keep-alive）跳过；读完或者连接断了就结束。"""
    event, data = None, []
    while True:
        raw = resp.readline()
        if not raw:
            break
        line = raw.decode("utf-8", "replace").rstrip("\r\n")
        if not line:
            if data:
                yield event or "message", "\n".join(data)
            event, data = None, []
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if field == "event":
            event = value
        elif field == "data":
            data.append(value)
    if data:
        yield event or "message", "\n".join(data)


# —— 会话 ——————————————————————————————————————————————————————————————————

class Turn:
    """session/prompt 正在跑的这一轮：连接在这里（取消 = 关掉它）。"""

    def __init__(self) -> None:
        self.stop = threading.Event()
        self.conn: http.client.HTTPConnection | None = None

    def cancel(self) -> None:
        self.stop.set()
        conn = self.conn
        sock = getattr(conn, "sock", None) if conn is not None else None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)  # 读这一轮的线程马上醒过来；服务器那边看到断开 = 点了停
            except OSError:
                pass


class Session:
    def __init__(self, sid: str, cwd: str, agent: str) -> None:
        self.id, self.cwd, self.agent = sid, cwd, agent
        self.allowed: set[str] = set()   # 这个会话里用户说过「这一类都同意」的（工具 子命令）
        self.turn: Turn | None = None
        self.lock = threading.Lock()


def to_openai(blocks) -> list[dict]:
    """ACP 的提示词（ContentBlock 列表）→ OpenAI 的 content 块：字、图片（data URL）、嵌进来的文字资源、资源链接（写成一行字）。"""
    if not isinstance(blocks, list) or not blocks:
        raise invalid("prompt must be a non-empty list")
    out: list[dict] = []
    for b in blocks:
        t = b.get("type") if isinstance(b, dict) else None
        if t == "text":
            out.append({"type": "text", "text": str(b.get("text") or "")})
        elif t == "image":
            if not b.get("data") or not b.get("mimeType"):
                raise invalid("image needs data and mimeType")
            out.append({"type": "image_url", "image_url": {"url": f"data:{b['mimeType']};base64,{b['data']}"}})
        elif t == "resource_link":
            out.append({"type": "text", "text": f"[resource_link name={json.dumps(b.get('name'))} uri={json.dumps(b.get('uri'))}]"})
        elif t == "resource":
            r = b.get("resource") if isinstance(b.get("resource"), dict) else {}
            if isinstance(r.get("text"), str):
                out.append({"type": "text", "text": f"[resource {r.get('uri') or ''}]\n{r['text']}"})
            elif r.get("blob") and str(r.get("mimeType") or "").startswith("image/"):
                out.append({"type": "image_url", "image_url": {"url": f"data:{r['mimeType']};base64,{r['blob']}"}})
            else:
                raise invalid("only text resources and images are supported")
        else:
            raise invalid(f"{t} prompt content is not supported")
    if not any((p["type"] == "text" and p["text"].strip()) or p["type"] == "image_url" for p in out):
        raise invalid("empty prompt")
    return out


def text_content(text: str) -> list[dict]:
    return [{"type": "content", "content": {"type": "text", "text": text}}]


class Agent:
    def __init__(self, server: Server, rpc: Rpc, agent: str | None) -> None:
        self.server, self.rpc, self.default = server, rpc, agent
        self.sessions: dict[str, Session] = {}
        self._agents: dict[str, str] | None = None

    # —— Agent 列表（= /v1/models）——

    def agents(self) -> dict[str, str]:
        if self._agents is None:
            data = self.server.call("GET", "/v1/models").get("data") or []
            self._agents = {str(m["id"]).split("/", 1)[1]: str(m.get("name") or m["id"]) for m in data
                            if isinstance(m, dict) and str(m.get("id") or "").startswith("openmousse/")}
        return self._agents

    def first_agent(self) -> str:
        names = self.agents()
        if self.default:
            if self.default not in names:
                raise RpcError(-32603, f"Internal error: this token can't use the Agent {self.default!r} (it can use {', '.join(names) or 'none'})")
            return self.default
        if not names:
            raise RpcError(-32603, "Internal error: this token can't use any Agent")
        return "main" if "main" in names else next(iter(names))

    def config_options(self, s: Session) -> list[dict]:
        names = self.agents()
        return [{"id": "agent", "name": "Agent", "category": "model", "type": "select", "currentValue": s.agent,
                 "options": [{"value": a, "name": n} for a, n in names.items()]}]

    def session(self, params: dict) -> Session:
        s = self.sessions.get(str(params.get("sessionId") or ""))
        if s is None:
            raise invalid(f"unknown session: {params.get('sessionId')}")
        return s

    # —— 方法 ——

    def initialize(self, params: dict) -> dict:
        return {"protocolVersion": PROTOCOL, "agentInfo": {"name": "openmousse", "title": "OpenMousse", "version": VERSION},
                "agentCapabilities": {"loadSession": False, "promptCapabilities": {"image": True, "audio": False, "embeddedContext": True},
                                      "mcpCapabilities": {"http": False, "sse": False},
                                      "sessionCapabilities": {"list": {}, "resume": {}, "close": {}}},
                "authMethods": []}

    def new(self, params: dict) -> dict:
        cwd = str(params.get("cwd") or "")
        if not os.path.isabs(cwd):
            raise invalid(f"cwd must be an absolute path: {cwd}")
        s = Session(str(uuid.uuid4()), cwd, self.first_agent())
        self.sessions[s.id] = s
        return {"sessionId": s.id, "configOptions": self.config_options(s)}

    def list(self, params: dict) -> dict:
        q = {"limit": "50"}
        if params.get("cwd"):
            q["cwd"] = str(params["cwd"])
        if params.get("cursor"):
            q["before"] = str(params["cursor"])
        got = self.server.call("GET", "/v1/openmousse/sessions?" + urllib.parse.urlencode(q))
        rows = got.get("data") or []
        out = {"sessions": [{"sessionId": r["id"], "cwd": r.get("cwd") or "", "title": r.get("title") or None, "updatedAt": r.get("updated_at")}
                            for r in rows]}
        if got.get("has_more") and rows:
            out["nextCursor"] = rows[-1]["updated_at"]
        return out

    def resume(self, params: dict) -> dict:
        sid, cwd = str(params.get("sessionId") or ""), str(params.get("cwd") or "")
        if not sid:
            raise invalid("sessionId is required")
        s = self.sessions.get(sid)
        if s is None:
            rows = self.server.call("GET", "/v1/openmousse/sessions?" + urllib.parse.urlencode({"id": sid, "limit": "1"})).get("data") or []
            agent = rows[0]["agent"] if rows and rows[0].get("agent") in self.agents() else self.first_agent()
            s = self.sessions[sid] = Session(sid, cwd or (rows[0].get("cwd") if rows else "") or "", agent)
        elif cwd:
            s.cwd = cwd
        return {"configOptions": self.config_options(s)}

    def close(self, params: dict) -> dict:
        s = self.session(params)
        if s.turn is not None:
            s.turn.cancel()
        self.sessions.pop(s.id, None)
        return {}

    def cancel(self, params: dict) -> None:
        s = self.sessions.get(str(params.get("sessionId") or ""))
        if s is not None and s.turn is not None:
            s.turn.cancel()

    def set_config_option(self, params: dict) -> dict:
        s = self.session(params)
        if params.get("configId") != "agent":
            raise invalid(f"unknown session config option: {params.get('configId')}")
        value = str(params.get("value") or "")
        if value not in self.agents():
            raise invalid(f"unknown agent: {value}")
        s.agent = value
        return {"configOptions": self.config_options(s)}

    # —— 一轮 ——

    def prompt(self, rid, params: dict) -> None:
        """session/prompt：检查完在新线程里跑，主线程接着读（取消、点头的回话要靠它）。"""
        s = self.session(params)
        content = to_openai(params.get("prompt"))
        with s.lock:
            if s.turn is not None:
                raise invalid("a prompt is already in flight for this session")
            turn = s.turn = Turn()

        def go() -> None:
            result, error = None, None
            try:
                result = {"stopReason": self.run(s, turn, content)}
            except ServerError as e:
                error = f"Internal error: turn failed: {e}"
            except Exception as e:  # noqa: BLE001 — 别让一轮的毛病把整个进程带崩
                log(f"prompt failed: {type(e).__name__}: {e}")
                error = "Internal error"
            with s.lock:  # 先放开再回话：编辑器一收到回话就发下一句，也进得来
                if s.turn is turn:
                    s.turn = None
            if result is not None:
                self.rpc.reply(rid, result)
            else:
                self.rpc.fail(rid, -32603, error or "Internal error")
        threading.Thread(target=go, name=f"prompt-{s.id[:8]}", daemon=True).start()

    def update(self, s: Session, update: dict) -> None:
        self.rpc.notify("session/update", {"sessionId": s.id, "update": update})

    def run(self, s: Session, turn: Turn, content: list[dict]) -> str:
        body = {"model": f"openmousse/{s.agent}", "stream": True, "messages": [{"role": "user", "content": content}]}
        headers = self.server.headers({"Accept": "text/event-stream", "X-Session-Id": s.id, "X-OpenMousse-Events": "1",
                                       "X-OpenMousse-Approvals": "wait", "X-OpenMousse-Cwd": urllib.parse.quote(s.cwd)})
        conn = turn.conn = self.server.connect()
        message, thought = str(uuid.uuid4()), str(uuid.uuid4())  # 一段回复、一段思考各一个 messageId（Hermes 的 AssistantMessageIdAllocator）
        tools: dict[str, dict] = {}   # 还开着的工具调用：id → {"name", "status"}
        finish = None
        try:
            try:
                conn.request("POST", self.server.path("/v1/chat/completions"), body=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                             headers=headers)
                resp = conn.getresponse()
            except OSError as e:
                if turn.stop.is_set():
                    return "cancelled"
                raise ServerError(f"can't reach {self.server.url}: {e}") from None
            if turn.stop.is_set():  # 连上之前就点了取消：现在断开（服务器那边 = 点了停）
                turn.cancel()
                return "cancelled"
            if resp.status != 200:
                raise ServerError(error_text(resp.status, resp.read()))
            try:
                for event, data in sse(resp):
                    if data == "[DONE]" or turn.stop.is_set():
                        break
                    try:
                        j = json.loads(data)
                    except ValueError:
                        continue
                    if event == "message":
                        if j.get("error"):
                            e = j["error"]
                            raise ServerError(str(e.get("message") if isinstance(e, dict) else e))
                        for ch in j.get("choices") or []:
                            d = ch.get("delta") or {}
                            if d.get("reasoning_content"):
                                self.update(s, {"sessionUpdate": "agent_thought_chunk", "messageId": thought,
                                                "content": {"type": "text", "text": d["reasoning_content"]}})
                            if d.get("content"):
                                self.update(s, {"sessionUpdate": "agent_message_chunk", "messageId": message,
                                                "content": {"type": "text", "text": d["content"]}})
                            if ch.get("finish_reason"):
                                finish = ch["finish_reason"]
                    elif event == "openmousse.tool":
                        self.on_tool(s, tools, j)
                    elif event == "openmousse.approval":
                        self.on_approval(s, turn, tools, j)
                    elif event == "openmousse.approval_result":
                        self.on_result(s, tools, j)
            except (OSError, http.client.HTTPException):  # 断在一块的中间是 IncompleteRead（不是 OSError）
                if not turn.stop.is_set():
                    raise ServerError("the connection to the server broke off") from None
            if turn.stop.is_set():
                return "cancelled"
            return FINISH.get(str(finish), "end_turn")
        finally:
            for tid, t in list(tools.items()):  # 还开着的补一个 failed（Hermes 的 flush_open_tool_calls）；在等点头的留着 pending
                if t["status"] != "pending":
                    self.update(s, {"sessionUpdate": "tool_call_update", "toolCallId": tid, "status": "failed",
                                    "content": text_content(ABANDONED)})
            turn.conn = None
            conn.close()

    def on_tool(self, s: Session, tools: dict, ev: dict) -> None:
        tid, name = str(ev.get("id") or ""), str(ev.get("name") or "")
        if not tid:
            return
        if ev.get("phase") == "start":
            tools[tid] = {"name": name, "status": "in_progress"}
            kind = KINDS.get(name) or ("fetch" if "__" in name else "other")
            self.update(s, {"sessionUpdate": "tool_call", "toolCallId": tid, "title": str(ev.get("title") or name), "kind": kind,
                            "status": "in_progress"})
            return
        st = str(ev.get("status") or "")
        t = tools.get(tid)
        if st == "pending":  # 交给用户点头了、这一轮没等到：卡在 app 的收件箱里
            if t is not None:
                t["status"] = "pending"
            return
        if t is not None and t["status"] in ("completed", "failed"):  # 点头的结果已经报过了
            tools.pop(tid, None)
            return
        tools.pop(tid, None)
        self.update(s, {"sessionUpdate": "tool_call_update", "toolCallId": tid, "status": "completed" if st == "ok" else "failed",
                        **({} if st == "ok" else {"content": text_content(f"Not done ({st}).")})})

    def on_approval(self, s: Session, turn: Turn, tools: dict, card: dict) -> None:
        """撤不回的调用出了卡。服务器在等（waiting）就问编辑器里的用户，把点的结果交回去；不然告诉用户去 app 里点。"""
        tid, iid = str(card.get("id") or ""), str(card.get("inbox") or "")
        title = str(card.get("title") or card.get("tool") or "")
        lines = [str(card.get("why") or "").strip(), *(f"- {c}" for c in card.get("changes") or [])]
        detail = "\n".join(x for x in lines if x) or title
        if tid in tools:
            tools[tid]["status"] = "pending"
        waiting = bool(card.get("waiting"))
        self.update(s, {"sessionUpdate": "tool_call_update", "toolCallId": tid, "title": title, "status": "pending",
                        "content": text_content(detail if waiting else WAITING_APP.format(inbox=iid))})
        if not waiting:
            return
        key = str(card.get("key") or card.get("tool") or "")
        if key and key in s.allowed:
            choice = "allow_session"
        else:
            res = self.rpc.request("session/request_permission", {
                "sessionId": s.id, "options": OPTIONS,
                "toolCall": {"toolCallId": tid, "title": title, "status": "pending", "content": text_content(detail),
                             "rawInput": {"inbox": iid, "action": key}}}, turn.stop)
            result = (res or {}).get("result")
            outcome = result.get("outcome") if isinstance(result, dict) else None
            # Hermes 的 _map_outcome_to_hermes：只认「选了」而且是给出去的选项；别的（取消、不认识、出错）不替用户决定，卡还在 app 里等
            choice = outcome.get("optionId") if isinstance(outcome, dict) and outcome.get("outcome") == "selected" else None
            if choice not in DECISIONS:
                return
        if choice == "allow_session" and key:
            s.allowed.add(key)
        try:
            self.server.call("POST", f"/v1/openmousse/approvals/{urllib.parse.quote(iid)}", {"action": DECISIONS[choice]})
        except ServerError as e:  # 这边点不了（engine.api.approve 关了、卡已经在 app 里点过）：服务器那边照常等或者已经有结果
            log(f"couldn't decide {iid}: {e}")
            self.update(s, {"sessionUpdate": "tool_call_update", "toolCallId": tid, "status": "pending",
                            "content": text_content(f"{WAITING_APP.format(inbox=iid)} ({e})")})

    def on_result(self, s: Session, tools: dict, ev: dict) -> None:
        tid, outcome = str(ev.get("id") or ""), str(ev.get("outcome") or "")
        ok = outcome == "approved"
        text = str(ev.get("output") or "") if ok else str(ev.get("error") or ev.get("note") or "") or outcome
        if tid in tools:
            tools[tid]["status"] = "completed" if ok else "failed"
        self.update(s, {"sessionUpdate": "tool_call_update", "toolCallId": tid, "status": "completed" if ok else "failed",
                        "content": text_content(text if ok else f"Not done: {outcome}" + (f" ({text})" if text and text != outcome else ""))})

    # —— 收发 ——

    REQUESTS = {"initialize": "initialize", "session/new": "new", "session/list": "list", "session/resume": "resume", "session/close": "close",
                "session/set_config_option": "set_config_option"}

    def handle(self, msg: dict) -> None:
        method, rid = msg.get("method"), msg.get("id")
        params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
        if rid is None:  # 通知
            if method == "session/cancel":
                self.cancel(params)
            return
        try:
            if method == "authenticate":
                self.rpc.reply(rid, {})
            elif method == "session/prompt":
                self.prompt(rid, params)
            elif method in self.REQUESTS:
                self.rpc.reply(rid, getattr(self, self.REQUESTS[method])(params))
            else:
                self.rpc.fail(rid, -32601, f"Method not found: {method}")
        except RpcError as e:
            self.rpc.fail(rid, e.code, str(e))
        except ServerError as e:
            self.rpc.fail(rid, -32603, f"Internal error: {e}")
        except Exception as e:  # noqa: BLE001
            log(f"{method} failed: {type(e).__name__}: {e}")
            self.rpc.fail(rid, -32603, "Internal error")

    def serve(self, inp) -> None:
        for raw in inp:
            line = raw.decode("utf-8", "replace").strip() if isinstance(raw, bytes) else raw.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                self.rpc.send({"id": None, "error": {"code": -32700, "message": "Parse error"}})
                continue
            if not isinstance(msg, dict):
                self.rpc.send({"id": None, "error": {"code": -32600, "message": "Invalid Request"}})
            elif "method" in msg:
                self.handle(msg)
            elif "id" in msg:
                self.rpc.resolve(msg)  # 编辑器回了我们的请求（request_permission）
        for s in list(self.sessions.values()):  # stdin 关了：编辑器走了，停掉还在跑的
            if s.turn is not None:
                s.turn.cancel()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="acp.py", description="OpenMousse ACP adapter: run an OpenMousse Agent as an ACP agent over stdio.")
    ap.add_argument("--url", default=os.environ.get("OPENMOUSSE_URL") or "http://127.0.0.1:8080", help="the OpenMousse server (env OPENMOUSSE_URL)")
    ap.add_argument("--token", default=os.environ.get("OPENMOUSSE_TOKEN") or "", help="an api or api-<agent id> token (env OPENMOUSSE_TOKEN)")
    ap.add_argument("--agent", default=os.environ.get("OPENMOUSSE_AGENT") or None, help="which Agent new sessions use (default main)")
    a = ap.parse_args(argv)
    if not a.token:
        ap.error("needs --token (or OPENMOUSSE_TOKEN): a token named api or api-<agent id> from auth.tokens in server.json")
    agent = Agent(Server(a.url, a.token), Rpc(sys.stdout.buffer), a.agent)
    agent.serve(sys.stdin.buffer)
    return 0


if __name__ == "__main__":
    sys.exit(main())
