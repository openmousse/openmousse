"""一轮：模型 → 工具 → 模型 …… 直到模型不再调工具。形状借 pi（pi-agent-core）：插话队列、后续队列、中止；
10/16 对照 pi、OpenClaw、Hermes 的源码补了一遍（见 ~/openmousse-wt/engine/research/）。

三种回合（harness 方案第 10 张）：谁在说话，决定看得到什么、能做什么、用哪个模型。
  owner     主人回合：你自己说话。工具按 Agent 给；Spec.gate 是关卡（第 2 项：撤得回直接做，撤不回进收件箱）
  cyber     赛博回合：你和赛博聊天。第 0 步零工具（以后只有只读的「问搭档」）
  outsider  外人回合：别人的话进来（名片 Agent、A2A、Doorman 复查）。零工具，硬卡：挂了工具直接报错，不靠提示词
别人的话永远只是资料、不算同意。工具本身也标了能在哪种回合用（Tool.kinds），不在名单里的根本不给模型看。
撤不回的工具（reversible=False）没有关卡就不做（Hermes：没人能点头的地方一律不做）。

一轮外面能做的（Control）：
  steer(消息)      插话：这一批工具做完（pi 0.58.4 起的做法，不半路丢下）就带着新消息再问模型
  follow_up(消息)  后续：等它说完再接着说
  abort()          停：正在流的回复留下已经出来的字，正在跑的工具收到取消
  这一轮结束后再 steer / follow_up 回 False（没送到）；没来得及用上的在 Result.pending 里，调用方自己决定怎么办。

会话始终是完整的：每个工具结果做完就存；停了、出错了、被外面取消了，没结果的调用也补一条「没做」（不然下一轮 Claude 和
多数兼容接口都会报错）；同一个会话同时只跑一轮（后来的等前一轮）。
"""
from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from . import events, models, providers
from .errors import EngineError
from .session import text_of, tool_calls, tool_result

KINDS = ("owner", "cyber", "outsider")
TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "array": list, "object": dict}
MAX_OUTPUT = 30000            # 一个工具结果最多给模型多少字（和 /mcp 一样；多了留头尾、掐中间）
MODEL_RETRIES = (1.0, 3.0)    # 一次模型调用还没出字就报了能重试的错：隔 1 秒、3 秒再试
_locks: dict[str, asyncio.Lock] = {}


@dataclass
class ToolCtx:
    turn: str
    kind: str
    agent: str | None
    conversation: str
    call_id: str
    control: Control


@dataclass
class Tool:
    name: str
    description: str
    schema: dict
    run: Callable[[dict, ToolCtx], Awaitable[Any]]
    kinds: tuple[str, ...] = ("owner",)  # 哪几种回合能用（外人回合永远没有）
    reversible: bool = True              # 撤得回：关卡直接放；撤不回：进收件箱（第 2 项）；没有关卡就不做
    title: Callable[[dict], str] | None = None  # 进度条上的一句（不放参数原文）
    timeout: float = 120
    max_output: int = MAX_OUTPUT

    def spec(self) -> dict:
        return {"name": self.name, "description": self.description, "schema": self.schema}


@dataclass
class Spec:
    kind: str                     # owner / cyber / outsider
    model: str                    # provider/model
    system: str = ""
    tools: list[Tool] = field(default_factory=list)
    conversation: str = ""        # 对话：app 的线程 id、card:<人>、doorman……（事件表按它归）
    agent: str | None = None
    origin: str | None = None     # 这一轮是被什么引出来的：user / auto / a2a / card / doorman …
    effort: str | None = None
    display: str | None = None
    max_tokens: int | None = None
    max_steps: int = 40           # 一轮最多调几次模型
    json: bool = False
    timeout: float = 600          # 一次模型调用最多等多久
    cache_ttl: str | None = None  # Claude 的缓存时长：主人、赛博回合用 "1h"
    gate: Callable[[Tool, dict, ToolCtx], Awaitable[str | dict | None]] | None = None
    # 关卡：None = 放行；字符串 = 不做，这句当出错的工具结果回给模型；
    # dict = 交出去等人点头（比如 {"status": "approval-pending", "id": 收件箱 id, …}），原样当工具结果回给模型、不算出错


class Control:
    def __init__(self) -> None:
        self._steer: list[dict] = []
        self._follow: list[dict] = []
        self.aborted = asyncio.Event()
        self.closed = False
        self._inner: asyncio.Future | None = None

    def steer(self, msg: dict) -> bool:
        if self.closed:
            return False
        self._steer.append(msg)
        return True

    def follow_up(self, msg: dict) -> bool:
        if self.closed:
            return False
        self._follow.append(msg)
        return True

    def abort(self) -> None:
        self.aborted.set()
        if self._inner is not None and not self._inner.done():
            self._inner.cancel()

    def has_steers(self) -> bool:
        return bool(self._steer)

    def take_steers(self) -> list[dict]:
        out, self._steer = self._steer, []
        return out

    def take_follow(self) -> list[dict]:
        out, self._follow = self._follow, []
        return out

    def close(self) -> list[dict]:
        """这一轮结束：之后的插话、后续都不收；回还没用上的。"""
        self.closed = True
        return self.take_steers() + self.take_follow()


@dataclass
class Result:
    turn: str
    status: str                   # done / aborted / error / refused / length / max_steps
    text: str                     # 最后给人看的字
    messages: list[dict]          # 这一轮新加进会话的（含进来的那句）
    usage: dict
    model: str
    error: str | None = None
    error_kind: str | None = None  # EngineError.kind（config / http / timeout / output …）
    retryable: bool = False
    first_ms: int | None = None   # 第一个字（或思考）出来用了多久
    pending: list[dict] = field(default_factory=list)  # 没来得及用上的插话、后续


def _being_cancelled() -> bool:
    t = asyncio.current_task()
    return bool(t and t.cancelling())


def check_args(args: Any, schema: dict) -> tuple[dict, str | None]:
    """跑工具前按 schema 查一遍（流式工具参数可能是半截的）→ (修过的参数, 问题)。顺手修常见的小毛病（pi、Hermes 的做法）：
    数字写成字符串的转成数字、"true" / "false" 转成布尔、可选参数给了 null 的去掉。"""
    if not isinstance(args, dict):
        return {}, "the input must be a JSON object"
    props = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    fixed: dict = {}
    for k, v in args.items():
        t = (props.get(k) or {}).get("type")
        if v is None and k not in required:
            continue
        if isinstance(v, str) and t in ("integer", "number"):
            try:
                v = int(v) if t == "integer" and v.strip().lstrip("-").isdigit() else float(v) if t == "number" else v
            except ValueError:
                pass
        elif isinstance(v, str) and t == "boolean" and v.strip().lower() in ("true", "false"):
            v = v.strip().lower() == "true"
        fixed[k] = v
    for k in required:
        if k not in fixed:
            return fixed, f"missing {k!r}"
    for k, v in fixed.items():
        t = (props.get(k) or {}).get("type")
        want = TYPES.get(t) if isinstance(t, str) else None
        if want and (not isinstance(v, want) or (t in ("integer", "number") and isinstance(v, bool))):
            return fixed, f"{k!r} should be {t}"
    return fixed, None


def _as_list(prompt: dict | list[dict] | None) -> list[dict]:
    return [] if prompt is None else [prompt] if isinstance(prompt, dict) else list(prompt)


def _add_usage(total: dict, msg: dict) -> None:
    u = msg.get("usage") or {}
    for k in ("in", "out", "cache_read", "cache_write"):
        total[k] = total.get(k, 0) + int(u.get(k) or 0)
    if not msg.get("provider"):  # 被停下的半截回复：没有用量可算
        return
    c = models.cost(f"{msg.get('provider')}/{msg.get('served') or msg.get('model')}", u)
    if c is None:
        total["cost"] = None
    elif total.get("cost", 0.0) is not None:
        total["cost"] = round(total.get("cost", 0.0) + c, 6)


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head, tail = int(limit * 0.7), int(limit * 0.25)
    return f"{text[:head]}\n\n[… {len(text) - head - tail} characters cut …]\n\n{text[-tail:]}"


def _result_text(res: Any) -> str:
    if res is None:
        return "(done)"
    if isinstance(res, str):
        return res or "(empty)"
    try:
        return json.dumps(res, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(res)


async def run_turn(spec: Spec, session, prompt: dict | list[dict] | None, *, on_event: Callable[[dict], None] | None = None,
                   control: Control | None = None) -> Result:
    """跑一轮。prompt = 这一轮进来的消息（一般是一条 user）。回 Result；模型、接口出的错记成 status=error 不抛；
    被外面取消（asyncio 的 cancel）照样记下来、把会话补完整再往外抛。回合规矩不对（外人回合挂工具）直接抛 EngineError(rule)。
    存在库里的会话同时只跑一轮：后来的等前一轮跑完。"""
    if spec.kind not in KINDS:
        raise EngineError(f"unknown turn kind {spec.kind!r}", kind="rule")
    if spec.kind == "outsider" and spec.tools:
        raise EngineError("outsider turns take no tools", kind="rule")
    models.parse_ref(spec.model)
    sid = getattr(session, "id", None)
    if not sid:
        return await _run(spec, session, prompt, on_event, control or Control())
    lock = _locks.setdefault(sid, asyncio.Lock())
    async with lock:
        return await _run(spec, session, prompt, on_event, control or Control())


async def _run(spec: Spec, session, prompt, on_event, control: Control) -> Result:
    tools = [t for t in spec.tools if spec.kind in t.kinds]
    by_name = {t.name: t for t in tools}
    conv = spec.conversation or "-"
    emit = on_event or (lambda ev: None)
    history = session.messages()
    new: list[dict] = []
    tid = events.turn_open(conversation=conv, kind=spec.kind, model=spec.model, session=getattr(session, "id", None), agent=spec.agent,
                           origin=spec.origin)

    def add(m: dict) -> None:
        history.append(m)
        new.append(m)
        session.append(m, turn=tid)

    usage: dict = {"in": 0, "out": 0, "cache_read": 0, "cache_write": 0, "cost": 0.0}
    status, error, ekind, retryable, last_text, first_ms, steps = "done", None, None, False, "", None, 0
    partial: list[str] = []  # 正在流的这一次已经出来的字（被外面取消时存成半截回复）
    try:
        for m in _as_list(prompt):
            add(m)
        while True:
            if control.aborted.is_set():
                status = "aborted"
                break
            steps += 1
            if steps > spec.max_steps:
                status, error = "max_steps", f"stopped after {spec.max_steps} model calls"
                add({"role": "assistant", "content": [{"type": "text", "text": f"(Stopped after {spec.max_steps} steps without finishing.)"}],
                     "api": None, "provider": None, "model": None, "stop": "end", "usage": {}})
                break
            partial.clear()
            msg, fm = await _model_step(spec, tid, history, tools, emit, control, partial)
            if first_ms is None:
                first_ms = fm
            if msg is None:
                status = "aborted"
                break
            _add_usage(usage, msg)
            add(msg)
            last_text = text_of(msg) or last_text
            stop = msg.get("stop")
            if stop == "aborted":
                status = "aborted"
                break
            if stop == "refusal":
                status = "refused"
                break
            calls = tool_calls(msg)
            if calls:
                if stop == "length":  # 工具参数写到一半被截断：不跑，每个调用回一句，会话保持完整（下一轮还能接着用）
                    for c in calls:
                        add(tool_result(c.get("id") or "", c.get("name") or "", "Not run: the call was cut off (max_tokens).", True))
                    status, error = "length", "a tool call was cut off"
                    break
                await _run_tools(spec, tid, calls, by_name, emit, control, add)
                if control.aborted.is_set():
                    status = "aborted"
                    break
                for m in control.take_steers():
                    _note_steer(tid, conv)
                    add(m)
                continue
            if stop == "pause":  # 服务端工具停在半路（现在不用服务端工具，留着）：接着问
                continue
            pending = control.take_steers() + control.take_follow()
            if pending:
                for m in pending:
                    _note_steer(tid, conv)
                    add(m)
                continue
            status = "length" if stop == "length" else "done"
            break
    except asyncio.CancelledError:
        _close_open_calls(history, add, "Not run: the turn was cancelled.", partial)
        control.close()
        events.turn_close(tid, conversation=conv, status="aborted", usage=usage, error="cancelled")
        raise
    except EngineError as e:
        status, error, ekind, retryable = "error", str(e), e.kind, e.retryable
    except Exception as e:  # noqa: BLE001 — 这一轮记成出错，不让一个意外把调用方带崩
        status, error, ekind = "error", f"{type(e).__name__}: {str(e)[:200]}", "internal"
    _close_open_calls(history, add, "Not run: the turn ended before this call.")
    left = control.close()
    events.turn_close(tid, conversation=conv, status=status, usage=usage, error=error)
    return Result(turn=tid, status=status, text=last_text, messages=new, usage=usage, model=spec.model, error=error, error_kind=ekind,
                  retryable=retryable, first_ms=first_ms, pending=left)


def _close_open_calls(history: list[dict], add, why: str, partial: list[str] | None = None) -> None:
    """会话补完整：被取消时把已经流出来的字存成半截回复；最后一条 assistant 里没结果的工具调用各补一条「没做」。"""
    if partial and "".join(partial).strip():
        add({"role": "assistant", "content": [{"type": "text", "text": "".join(partial)}], "api": None, "provider": None, "model": None,
             "stop": "aborted", "usage": {}})
        return
    last = next((i for i in range(len(history) - 1, -1, -1) if history[i].get("role") == "assistant"), None)
    if last is None:
        return
    done = {str(m.get("id")) for m in history[last + 1:] if m.get("role") == "tool"}
    for c in tool_calls(history[last]):
        if str(c.get("id") or "") not in done:
            add(tool_result(str(c.get("id") or ""), str(c.get("name") or ""), why, True))


def _note_steer(tid: str, conv: str) -> None:
    sid = events.step_open(tid, type_="steer", conversation=conv)
    events.step_close(sid, status="ok", conversation=conv, turn=tid)


async def _model_step(spec: Spec, tid: str, history: list[dict], tools: list[Tool], emit, control: Control,
                      partial: list[str]) -> tuple[dict | None, int | None]:
    """调一次模型。→ (assistant 消息, 第一个字用了多少毫秒)。还没出字就报了能重试的错：等一下再试（最多两次）。
    被 abort 时回已经出来的字（stop = aborted；一个字都没有就回 None）。"""
    conv = spec.conversation or "-"
    sid = events.step_open(tid, type_="model", name=spec.model, conversation=conv)
    req = providers.Request(system=spec.system, messages=list(history), tools=[t.spec() for t in tools], max_tokens=spec.max_tokens,
                            effort=spec.effort, display=spec.display, json=spec.json, timeout=spec.timeout, cache_ttl=spec.cache_ttl)
    t0 = time.monotonic()
    first: list[float] = []
    attempt = 0

    async def consume() -> dict:
        async for ev in providers.stream(spec.model, req):
            if ev["type"] == "done":
                return ev["message"]
            if not first:
                first.append(time.monotonic() - t0)
            if ev["type"] == "text":
                partial.append(ev["text"])
                events.publish(conv, {"type": "delta", "turn": tid, "text": ev["text"]})
            emit(ev)
        raise EngineError("the model stream ended without a reply", kind="output", retryable=True)

    while True:
        task = asyncio.ensure_future(consume())
        control._inner = task
        try:
            msg = await task
            break
        except asyncio.CancelledError:
            fm = int(first[0] * 1000) if first else None
            events.step_close(sid, status="aborted", data={"first_ms": fm}, conversation=conv, turn=tid)
            if control.aborted.is_set() and not _being_cancelled():
                text = "".join(partial)
                partial.clear()
                if not text.strip():
                    return None, fm
                return ({"role": "assistant", "content": [{"type": "text", "text": text}], "api": None, "provider": None, "model": None,
                         "stop": "aborted", "usage": {}}, fm)
            raise
        except EngineError as e:
            if e.retryable and not first and attempt < len(MODEL_RETRIES) and not control.aborted.is_set():
                await asyncio.sleep(MODEL_RETRIES[attempt])  # 还没出字：再来一次（pi、Hermes 都在这一层重试）
                attempt += 1
                continue
            events.step_close(sid, status="error", data={"error": str(e)[:200], "kind": e.kind, "attempts": attempt + 1},
                              conversation=conv, turn=tid)
            raise
        finally:
            control._inner = None
    fm = int(first[0] * 1000) if first else None
    partial.clear()
    events.step_close(sid, status="ok", data={"usage": msg.get("usage"), "stop": msg.get("stop"), "first_ms": fm, "model": msg.get("model"),
                                              "served": msg.get("served"), "attempts": attempt + 1}, conversation=conv, turn=tid)
    return msg, fm


async def _run_tools(spec: Spec, tid: str, calls: list[dict], by_name: dict[str, Tool], emit, control: Control, add) -> None:
    """一个一个跑（以后只读的可以并行），每个结果出来就存。每个调用都有一条结果（做了、没做、出错），会话保持完整。"""
    conv = spec.conversation or "-"
    for c in calls:
        name, cid = str(c.get("name") or ""), str(c.get("id") or "")
        if control.aborted.is_set():
            add(tool_result(cid, name, "Not run: the user stopped this turn.", True))
            continue
        tool = by_name.get(name)
        if tool is None:
            add(tool_result(cid, name, f"Unknown tool {name!r}. Available: {', '.join(by_name) or 'none'}.", True))
            continue
        args, problem = check_args(c.get("args") if isinstance(c.get("args"), dict) else {}, tool.schema)
        if c.get("bad_args"):
            problem = "the input is not valid JSON"
        if problem:
            add(tool_result(cid, name, f"Not run: {problem}. Fix the input and call again.", True))
            continue
        ctx = ToolCtx(turn=tid, kind=spec.kind, agent=spec.agent, conversation=conv, call_id=cid, control=control)
        try:
            detail = tool.title(args) if tool.title else ""
        except Exception:  # noqa: BLE001
            detail = ""
        sid = events.step_open(tid, type_="tool", name=name, detail=detail, conversation=conv)
        emit({"type": "tool_run", "id": cid, "name": name, "detail": detail})
        verdict: str | dict | None = None
        if spec.gate is not None:
            try:
                verdict = await spec.gate(tool, args, ctx)
            except Exception as e:  # noqa: BLE001 — 关卡自己出错：不做（宁可少做，不可错做）
                verdict = f"Not run: the safety check failed ({type(e).__name__})."
        elif not tool.reversible:
            verdict = ("Not run: this action can't be undone and needs the user's OK, but nobody can approve it here. "
                       "The user has NOT consented. Do not retry it or try to reach it another way; tell the user what you wanted to do.")
        if verdict is None and control.aborted.is_set():  # 关卡问到一半用户点了停
            verdict = "Not run: the user stopped this turn."
        if verdict is not None:
            pending_ok = isinstance(verdict, dict)
            add(tool_result(cid, name, _result_text(verdict), not pending_ok))
            events.step_close(sid, status="pending" if pending_ok else "denied", conversation=conv, turn=tid)
            emit({"type": "tool_end", "id": cid, "name": name, "ok": pending_ok})
            continue
        task = asyncio.ensure_future(asyncio.wait_for(tool.run(args, ctx), tool.timeout))
        control._inner = task
        ok, res = True, None
        try:
            res = await task
        except asyncio.CancelledError:
            if control.aborted.is_set() and not _being_cancelled():
                ok, res = False, "Stopped by the user."
            else:
                events.step_close(sid, status="aborted", conversation=conv, turn=tid)
                raise
        except asyncio.TimeoutError:
            ok, res = False, f"Timed out after {tool.timeout:.0f}s."
        except Exception as e:  # noqa: BLE001 — 工具出错回给模型，它自己决定怎么办
            ok, res = False, f"{type(e).__name__}: {str(e)[:300]}"
        finally:
            control._inner = None
        text = _result_text(res)
        add(tool_result(cid, name, _clip(text, tool.max_output), not ok))
        events.step_close(sid, status="ok" if ok else "error", data={"chars": len(text)}, conversation=conv, turn=tid)
        emit({"type": "tool_end", "id": cid, "name": name, "ok": ok})
