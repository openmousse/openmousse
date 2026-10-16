"""一轮：模型 → 工具 → 模型 …… 直到模型不再调工具。形状借 pi（pi-agent-core）：插话队列、后续队列、中止。

三种回合（harness 方案第 10 张）：谁在说话，决定看得到什么、能做什么、用哪个模型。
  owner     主人回合：你自己说话。工具按 Agent 给；Spec.gate 是关卡（第 2 步：撤得回直接做，撤不回进收件箱）
  cyber     赛博回合：你和赛博聊天。第 0 步零工具（以后只有只读的「问搭档」）
  outsider  外人回合：别人的话进来（名片 Agent、A2A、Doorman 复查）。零工具，硬卡：挂了工具直接报错，不靠提示词
别人的话永远只是资料、不算同意。工具本身也标了能在哪种回合用（Tool.kinds），不在名单里的根本不给模型看。

一轮外面能做的（Control）：
  steer(消息)      插话：手上这个工具做完就停，剩下的工具不做（回一句「先看新消息」），带着新消息再问模型
  follow_up(消息)  后续：等它说完再接着说
  abort()          停：正在流的回复留下已经出来的字，正在跑的工具收到取消
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
    reversible: bool = True              # 撤得回：关卡直接放；撤不回：进收件箱（第 2 步）
    title: Callable[[dict], str] | None = None  # 进度条上的一句（不放参数原文）
    timeout: float = 120

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
    gate: Callable[[Tool, dict, ToolCtx], Awaitable[str | None]] | None = None  # 关卡：None = 放行；回一句话 = 不做，这句回给模型


class Control:
    def __init__(self) -> None:
        self._steer: list[dict] = []
        self._follow: list[dict] = []
        self.aborted = asyncio.Event()
        self._inner: asyncio.Future | None = None

    def steer(self, msg: dict) -> None:
        self._steer.append(msg)

    def follow_up(self, msg: dict) -> None:
        self._follow.append(msg)

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


@dataclass
class Result:
    turn: str
    status: str                   # done / aborted / error / refused / length / max_steps
    text: str                     # 最后给人看的字
    messages: list[dict]          # 这一轮新加进会话的（含进来的那句）
    usage: dict
    model: str
    error: str | None = None
    first_ms: int | None = None   # 第一个字（或思考）出来用了多久


def _being_cancelled() -> bool:
    t = asyncio.current_task()
    return bool(t and t.cancelling())


def check_args(args: Any, schema: dict) -> str | None:
    """跑工具前按 schema 粗查一遍（必填的有没有、类型对不对）：流式工具参数可能是半截的。"""
    if not isinstance(args, dict):
        return "the input must be a JSON object"
    for k in schema.get("required") or []:
        if k not in args:
            return f"missing {k!r}"
    for k, v in args.items():
        t = ((schema.get("properties") or {}).get(k) or {}).get("type")
        want = TYPES.get(t) if isinstance(t, str) else None
        if want and (not isinstance(v, want) or (t in ("integer", "number") and isinstance(v, bool))):
            return f"{k!r} should be {t}"
    return None


def _as_list(prompt: dict | list[dict] | None) -> list[dict]:
    return [] if prompt is None else [prompt] if isinstance(prompt, dict) else list(prompt)


def _add_usage(total: dict, msg: dict) -> None:
    u = msg.get("usage") or {}
    for k in ("in", "out", "cache_read", "cache_write"):
        total[k] = total.get(k, 0) + int(u.get(k) or 0)
    if not msg.get("provider"):  # 被停下的半截回复：没有用量可算
        return
    c = models.cost(f"{msg.get('provider')}/{msg.get('model')}", u)
    if c is None:
        total["cost"] = None
    elif total.get("cost", 0.0) is not None:
        total["cost"] = round(total.get("cost", 0.0) + c, 6)


async def run_turn(spec: Spec, session, prompt: dict | list[dict] | None, *, on_event: Callable[[dict], None] | None = None,
                   control: Control | None = None) -> Result:
    """跑一轮。prompt = 这一轮进来的消息（一般是一条 user）。回 Result；模型、接口出的错记成 status=error 不抛；
    被外面取消（asyncio 的 cancel）照样记下来再往外抛。回合规矩不对（外人回合挂工具）直接抛 EngineError(rule)。"""
    if spec.kind not in KINDS:
        raise EngineError(f"unknown turn kind {spec.kind!r}", kind="rule")
    if spec.kind == "outsider" and spec.tools:
        raise EngineError("outsider turns take no tools", kind="rule")
    models.parse_ref(spec.model)
    tools = [t for t in spec.tools if spec.kind in t.kinds]
    by_name = {t.name: t for t in tools}
    control = control or Control()
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
    status, error, last_text, first_ms, steps = "done", None, "", None, 0
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
                break
            msg, fm = await _model_step(spec, tid, history, tools, emit, control)
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
                for r in await _run_tools(spec, tid, calls, by_name, emit, control):
                    add(r)
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
        events.turn_close(tid, conversation=conv, status="aborted", usage=usage, error="cancelled")
        raise
    except EngineError as e:
        status, error = "error", str(e)
    except Exception as e:  # noqa: BLE001 — 这一轮记成出错，不让一个意外把调用方带崩
        status, error = "error", f"{type(e).__name__}: {str(e)[:200]}"
    events.turn_close(tid, conversation=conv, status=status, usage=usage, error=error)
    return Result(turn=tid, status=status, text=last_text, messages=new, usage=usage, model=spec.model, error=error, first_ms=first_ms)


def _note_steer(tid: str, conv: str) -> None:
    sid = events.step_open(tid, type_="steer", conversation=conv)
    events.step_close(sid, status="ok", conversation=conv, turn=tid)


async def _model_step(spec: Spec, tid: str, history: list[dict], tools: list[Tool], emit, control: Control) -> tuple[dict | None, int | None]:
    """调一次模型。→ (assistant 消息, 第一个字用了多少毫秒)。被 abort 时回已经出来的字（stop = aborted；一个字都没有就回 None）。"""
    conv = spec.conversation or "-"
    sid = events.step_open(tid, type_="model", name=spec.model, conversation=conv)
    req = providers.Request(system=spec.system, messages=list(history), tools=[t.spec() for t in tools], max_tokens=spec.max_tokens,
                            effort=spec.effort, display=spec.display, json=spec.json, timeout=spec.timeout)
    text: list[str] = []
    t0 = time.monotonic()
    first: list[float] = []

    async def consume() -> dict:
        async for ev in providers.stream(spec.model, req):
            if ev["type"] == "done":
                return ev["message"]
            if not first:
                first.append(time.monotonic() - t0)
            if ev["type"] == "text":
                text.append(ev["text"])
                events.publish(conv, {"type": "delta", "turn": tid, "text": ev["text"]})
            emit(ev)
        raise EngineError("the model stream ended without a reply", kind="output", retryable=True)

    task = asyncio.ensure_future(consume())
    control._inner = task
    fm = None
    try:
        msg = await task
    except asyncio.CancelledError:
        fm = int(first[0] * 1000) if first else None
        if control.aborted.is_set() and not _being_cancelled():
            events.step_close(sid, status="aborted", data={"first_ms": fm}, conversation=conv, turn=tid)
            partial = "".join(text)
            if not partial:
                return None, fm
            return ({"role": "assistant", "content": [{"type": "text", "text": partial}], "api": None, "provider": None, "model": None,
                     "stop": "aborted", "usage": {}}, fm)
        events.step_close(sid, status="aborted", data={"first_ms": fm}, conversation=conv, turn=tid)
        raise
    except EngineError as e:
        events.step_close(sid, status="error", data={"error": str(e)[:200], "kind": e.kind}, conversation=conv, turn=tid)
        raise
    finally:
        control._inner = None
    fm = int(first[0] * 1000) if first else None
    events.step_close(sid, status="ok", data={"usage": msg.get("usage"), "stop": msg.get("stop"), "first_ms": fm, "model": msg.get("model")},
                      conversation=conv, turn=tid)
    return msg, fm


def _result_text(res: Any) -> str:
    if res is None:
        return "(done)"
    if isinstance(res, str):
        return res or "(empty)"
    try:
        return json.dumps(res, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(res)


async def _run_tools(spec: Spec, tid: str, calls: list[dict], by_name: dict[str, Tool], emit, control: Control) -> list[dict]:
    """一个一个跑（以后只读的可以并行）。每个调用都回一条结果（做了、没做、出错），会话保持完整。"""
    conv = spec.conversation or "-"
    out: list[dict] = []
    for i, c in enumerate(calls):
        name, cid = str(c.get("name") or ""), str(c.get("id") or "")
        if control.aborted.is_set():
            out.append(tool_result(cid, name, "Not run: the user stopped this turn.", True))
            continue
        if i > 0 and control.has_steers():
            out.append(tool_result(cid, name, "Skipped: the user just sent a new message. Read it first.", True))
            continue
        tool = by_name.get(name)
        if tool is None:
            out.append(tool_result(cid, name, f"Unknown tool {name!r}. Available: {', '.join(by_name) or 'none'}.", True))
            continue
        args = c.get("args") if isinstance(c.get("args"), dict) else {}
        problem = "the input is not valid JSON" if c.get("bad_args") else check_args(args, tool.schema)
        if problem:
            out.append(tool_result(cid, name, f"Not run: {problem}. Fix the input and call again.", True))
            continue
        ctx = ToolCtx(turn=tid, kind=spec.kind, agent=spec.agent, conversation=conv, call_id=cid, control=control)
        try:
            detail = tool.title(args) if tool.title else ""
        except Exception:  # noqa: BLE001
            detail = ""
        sid = events.step_open(tid, type_="tool", name=name, detail=detail, conversation=conv)
        emit({"type": "tool_run", "id": cid, "name": name, "detail": detail})
        if spec.gate is not None:
            verdict = await spec.gate(tool, args, ctx)
            if verdict:
                out.append(tool_result(cid, name, verdict, True))
                events.step_close(sid, status="denied", conversation=conv, turn=tid)
                emit({"type": "tool_end", "id": cid, "name": name, "ok": False})
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
        out.append(tool_result(cid, name, text, not ok))
        events.step_close(sid, status="ok" if ok else "error", data={"chars": len(text)}, conversation=conv, turn=tid)
        emit({"type": "tool_end", "id": cid, "name": name, "ok": ok})
    return out
