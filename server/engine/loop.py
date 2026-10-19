"""一轮：模型 → 工具 → 模型 …… 直到模型不再调工具。形状借 pi（pi-agent-core）：插话队列、后续队列、中止；
10/16 对照 pi、OpenClaw、Hermes 的源码补了一遍（见 ~/openmousse-wt/engine/research/）。

三种回合（harness 方案第 10 张）：谁在说话，决定看得到什么、能做什么、用哪个模型。
  owner     主人回合：你自己说话。工具按 Agent 给；Spec.gate 是关卡（第 2 项：撤得回直接做，撤不回进收件箱）
  cyber     赛博回合：你和赛博聊天。第 0 步零工具（以后只有只读的「问搭档」）
  outsider  外人回合：别人的话进来（名片 Agent、A2A、Doorman 复查）。零工具，硬卡：挂了工具直接报错，不靠提示词
别人的话永远只是资料、不算同意。工具本身也标了能在哪种回合用（Tool.kinds），不在名单里的根本不给模型看；读写记忆的工具再照
engine/memgate.py 的读写表筛一遍（screen：主人回合全给，赛博、外人回合只给自己标了 memory_scope 的）。
撤不回的调用（reversible=False，或者 classify 分成 ask / off 的那一次）没有关卡就不做（Hermes：没人能点头的地方一律不做）。
一个工具管好几件事时（命令行脚本的子命令）按这一次的参数分：Tool.classify → read / undo / ask / off（engine/tools.py 的分类表）。

一轮外面能做的（Control）：
  steer(消息)      插话：这一批工具做完（pi 0.58.4 起的做法，不半路丢下）就带着新消息再问模型；模型已经说完、没调工具的，也带着再问一次。
                   said=True（用户自己在 app 里刚说的，chat.steer_engine）而 ask_user 正在等回答（engine/ask.py）：这一句就是回答，不进插话队列
  follow_up(消息)  后续：等它说完（也没有插话了）再接着说
  abort()          停：正在流的回复留下已经出来的字，正在跑的工具收到取消；还在备料、压缩的也停下（这句话照样记进会话）
  这一轮结束后、停了以后、或者排着没用上的插话已经有 STEER_CAP 条，再 steer 回 False（没送到）；follow_up 结束后回 False。
  送到了不等于用上了：插话只有真写进会话（on_event 收到 steer 事件）才算并进了这一轮（OpenClaw 的规矩：提交进记录才算接住）；
  没来得及用上的在 Result.pending 里，调用方自己决定怎么办（app 那边变成下一轮，chat.py）。

on_event 收到的（app 的进度条、插话从这里接，第 3 项；回调自己出错不影响这一轮）：
  {"type": "model", "step": n}                 第 n 次调模型，马上开始（思考从头算）
  {"type": "text" | "thinking", "text"}        模型吐的字、思考摘要（providers.py 原样转过来）；{"type": "tool_start", "id", "name"}：开始写一个工具调用
  {"type": "tool_run", "id", "name", "detail"} 一个工具调用开始执行（detail = Tool.title，一句标题，不放参数原文）
  {"type": "tool_end", "id", "name", "ok", "status"}  结束：status = ok / error / timeout / stopped（用户点了停）/ denied（关卡没放）
                                               / pending（交给用户点头了，不算出错）
  {"type": "steer" | "follow_up", "ref"}       插话、后续写进会话了（ref = 消息上带的 ref，app 里那条消息的 id）
  {"type": "model_switch", "from", "to", "reason"}  换模型了（第 13 步，engine/fallback.py 的备用链；reason 见那里，主模型在冷却 = cooldown）。
                                               app 先不认它（chat.note_engine 不认识的事件不画），事件表里那一步的 data.switch 也有
  {"type": "rewrite", "text"}                  前面给过的字撤回了一段，text 是这一轮现在的全部字（只有 Claude Code 跑的发，
                                               engine/claude_brain.py；chat.py 整段换掉，推 SSE text）
  工具自己也能经 ToolCtx.emit 发事件（后台任务派出去、有进展：{"type": "task", "task": 台账一行}，第 5 项；⑫b 起：
  {"type": "say", "text"} 工具自己说给用户看的一段（ask_user 的问题、propose_plan 的计划，owner.respond 当成回复里单独的一段），
  {"type": "ask_user" | "ask_user_done" | "answer", …}（engine/ask.py）、{"type": "todo", "items"}（engine/todo.py）、
  {"type": "plan_mode", "on", "by"}（engine/plan.py））
工具结果里可以带图（⑫b，engine/images.py）：run 回 ToolOutput(字, 图)，图是存进图库以后的样子，跟着字一起写进那条工具结果

会话始终是完整的：每个工具结果做完就存；停了、出错了、被外面取消了，没结果的调用也补一条「没做」（不然下一轮 Claude 和
多数兼容接口都会报错）；同一个会话同时只跑一轮（后来的等前一轮）。

两个钩子（第 4 项，主人回合用，engine/owner.py 配）：
  Spec.prepare(一轮 id, 进来的消息)  拿到会话锁以后、这句话进会话之前：太长了先压缩（engine/compact.py），备料变了在这句话后面补一条
                                    system（engine/context.py）→ 回真正要放进会话的那几条。出错就照原样放进去，不拖累这一轮
  Spec.recover(一轮 id, 错)          模型说上下文太长（还没出字）：压缩一次再问（pi 的 compact-and-retry，一轮最多一次）→ True = 压了，再来
"""
from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from . import events, fallback, models, providers, store
from .errors import EngineError
from .session import DbSession, system_note, text_of, tool_calls, tool_result

KINDS = ("owner", "cyber", "outsider")
TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "array": list, "object": dict}
MAX_OUTPUT = 30000            # 一个工具结果最多给模型多少字（和 /mcp 一样；多了留头尾、掐中间）
MODEL_RETRIES = (1.0, 3.0)    # 一次模型调用还没出字就报了能重试的错：隔 1 秒、3 秒再试
STEER_CAP = 20                # 排着没用上的插话最多几条（OpenClaw 队列的上限也是 20）；再多的 steer 回 False，调用方改成下一轮
_locks: dict[str, asyncio.Lock] = {}


@dataclass
class ToolCtx:
    turn: str
    kind: str
    agent: str | None
    conversation: str
    call_id: str
    control: Control
    emit: Callable[[dict], None] | None = None  # 往这一轮的 on_event 发自己的事件（后台任务出任务卡：{"type": "task", …}，第 5 项）
    origin: str | None = None  # 这一轮的来路（Spec.origin）：工具跑的脚本再引出一轮时带上（via，ChatGPT 套餐据此认后台，engine/tools.py）


@dataclass
class ToolOutput:
    """工具结果里除了字还有图（⑫b）：images 是 engine/images.py 存好的图块（会话里只记文件名和指纹，发给模型时再读）；
    extra 并进那条工具结果（ask_user 的回答是 app 里哪条消息：{"ref": id}）。"""
    text: str
    images: list[dict] = field(default_factory=list)
    extra: dict = field(default_factory=dict)


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
    # 一个工具管好几件事（命令行脚本的子命令）时按这一次的参数分：→ (read / undo / ask / off, 一句理由)；给了就不看 reversible
    classify: Callable[[dict], tuple[str, str]] | None = None
    prepare: Callable[[Any], Any] | None = None  # 按 schema 查之前修一下参数（pi 的 prepareArguments：比如整条命令写成了一个字符串）

    def spec(self) -> dict:
        return {"name": self.name, "description": self.description, "schema": self.schema}

    def level(self, args: dict) -> tuple[str, str]:
        """这一次调用怎么过关卡：read 只读 / undo 撤得回 / ask 撤不回或对外（要用户点头）/ off 关掉了。分不出来按 ask。"""
        if self.classify is None:
            return ("undo", "") if self.reversible else ("ask", "")
        try:
            return self.classify(args)
        except Exception:  # noqa: BLE001 — 分类自己出错：宁可多问
            return "ask", "could not classify this call"


class ToolFailure(Exception):
    """工具自己说的失败（脚本退出码不是 0、参数不许……）：原话当出错的工具结果回给模型，不加类型名、不截到 300 字。"""


@dataclass
class Spec:
    kind: str                     # owner / cyber / outsider
    model: str                    # provider/model
    system: str = ""
    tools: list[Tool] = field(default_factory=list)
    conversation: str = ""        # 对话：app 的线程 id、card:<人>、doorman……（事件表按它归）
    agent: str | None = None
    origin: str | None = None     # 这一轮是被什么引出来的：user / auto / a2a / card / doorman …
    via: str | None = None        # 经谁的请求引出来的（外面的 MCP 客户端 = mcp；内核工具跑的脚本 = 那一轮的来路）：ChatGPT 套餐和 origin 取更严的
    effort: str | None = None
    display: str | None = None
    max_tokens: int | None = None
    max_steps: int = 40           # 一轮最多调几次模型
    json: bool = False
    timeout: float = 600          # 一次模型调用最多等多久
    cache_ttl: str | None = None  # Claude 的缓存时长：主人、赛博回合用 "1h"
    cache_tail: bool = True       # Claude 的顶层自动缓存：一次性的外人回合关掉（只缓存 system）
    gate: Callable[[Tool, dict, ToolCtx], Awaitable[str | dict | None]] | None = None
    # 关卡：None = 放行；字符串 = 不做，这句当出错的工具结果回给模型；
    # dict = 交出去等人点头（比如 {"status": "approval-pending", "id": 收件箱 id, …}），原样当工具结果回给模型、不算出错；
    # 别的 status = 关卡当场等到了用户的决定（对外接口，engine/openai_api.py）：照样原样回给模型，带 "error": true 的算出错
    prepare: Callable[[str, list[dict]], Awaitable[list[dict]]] | None = None  # 见文件开头「两个钩子」；可以改 spec.system（压缩后重写的开头）
    recover: Callable[[str, EngineError], Awaitable[bool]] | None = None
    fallbacks: list[str] | None = None  # 这一轮的备用链（engine/fallback.py）：None = 按模型找（它自己的链、engine.fallbacks）；[] = 不换


class Control:
    def __init__(self) -> None:
        self._steer: list[dict] = []
        self._follow: list[dict] = []
        self.aborted = asyncio.Event()
        self.closed = False
        self._inner: asyncio.Future | None = None
        self._answer: asyncio.Future | None = None  # ask_user 正在等的回答（engine/ask.py）

    def steer(self, msg: dict, *, said: bool = False) -> bool:
        """said：用户自己在 app 里刚说的（chat.steer_engine）。ask_user 正在等回答的话，这一句就是回答（不进插话队列）。"""
        if said and self.answer(msg):
            return True
        if not self.can_steer():
            return False
        self._steer.append(msg)
        return True

    def expect_answer(self) -> asyncio.Future:
        """ask_user 开始等（engine/ask.py）：之后用户说的下一句（steer said=True）或 /api/engine/answer 交到这里。"""
        self._answer = asyncio.get_running_loop().create_future()
        return self._answer

    def stop_expecting(self) -> None:
        self._answer = None

    def asking(self) -> bool:
        return self._answer is not None and not self._answer.done()

    def answer(self, msg: dict) -> bool:
        """交一个回答给正在等的 ask_user。→ 收下了没有（没在问、已经答过、停了、这一轮结束了 → False：插话那条路照常走）。"""
        if self.closed or self.aborted.is_set() or not self.asking():
            return False
        assert self._answer is not None
        self._answer.set_result(msg)
        return True

    def steer_back(self, msg: dict) -> None:
        """ask_user 收到的那句不算回答（没对上只认的那几个选项）：照常当插话（已经收下过一次，不再看上限）。"""
        if not self.closed:
            self._steer.append(msg)

    def can_steer(self) -> bool:
        """现在插话进得去吗（这一轮没结束、没被停、排着的没满）。"""
        return not self.closed and not self.aborted.is_set() and len(self._steer) < STEER_CAP

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

    def reopen(self, msgs: list[dict] | None = None) -> None:
        """这一轮换个模型从头再来（engine/claude_brain.py：Claude Code 跑不了，换备用链里的）：又收插话了；上一个没用上的那几条（msgs）
        放回插话的最前面，换上的那一轮接着用。停（aborted）照旧。"""
        self.closed = False
        if msgs:
            self._steer[:0] = list(msgs)


@dataclass
class Result:
    turn: str
    status: str                   # done / aborted / error / refused / length / max_steps
    text: str                     # 最后给人看的字
    messages: list[dict]          # 这一轮新加进会话的（含进来的那句）
    usage: dict
    model: str                    # 出了回复的那个模型（换过模型就是换上的那个；没出回复 = 最后试的那个）
    error: str | None = None
    error_kind: str | None = None  # EngineError.kind（config / http / timeout / output …）
    retryable: bool = False
    first_ms: int | None = None   # 第一个字（或思考）出来用了多久
    pending: list[dict] = field(default_factory=list)  # 没来得及用上的插话、后续
    models: list[str] = field(default_factory=list)    # 这一轮真的调过的模型（按先后；换过模型就不止一个）


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
    if isinstance(res, ToolOutput):
        return res.text or ("(image)" if res.images else "(empty)")
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
    sub = models.subscription(spec.model)
    if sub == "claude-code":  # 用户自己的 Claude Code（claude-code/…）不走这里：只有 engine/claude_brain.py 跑它，只给主人回合
        raise EngineError(f"{spec.model} runs only on the user's own Claude Code, in their own turns", kind="rule")
    if sub and not models.may_spend(spec.model, kind=spec.kind, origin=spec.origin, via=spec.via):  # ChatGPT 套餐：调用方该先换掉，这里再挡一道
        raise EngineError(f"{spec.model} is the user's own ChatGPT plan, which this turn can't use (only the user's own turns; background "
                          "ones once they allow it)", kind="rule")
    sid = getattr(session, "id", None)
    if not sid:
        return await _run(spec, session, prompt, on_event, control or Control())
    lock = _locks.setdefault(sid, asyncio.Lock())
    async with lock:
        return await _run(spec, session, prompt, on_event, control or Control())


async def wait_idle(session_id: str, timeout: float) -> bool:
    """等这条会话正在跑的那一轮跑完（04:00 换新的时候：前一天那条会话还在回话，新的一天等它说完再备料，昨天的结尾才接得上）。
    → True = 现在没在跑了；False = 等到点了还在跑。"""
    lock = _locks.get(session_id)
    if lock is None or not lock.locked():
        return True
    try:
        async with asyncio.timeout(timeout):
            async with lock:
                return True
    except TimeoutError:
        return False


class _Aborted(Exception):
    """abort() 打断了备料、压缩（不是外面取消这一轮）。"""


async def _abortable(control: Control, coro) -> Any:
    """跑一段能被「停」打断的准备工作（备料、压缩）：已经停了就不跑；跑到一半停了抛 _Aborted。外面取消照常往外抛。"""
    if control.aborted.is_set():
        coro.close()
        raise _Aborted()
    task = asyncio.ensure_future(coro)
    control._inner = task
    try:
        return await task
    except asyncio.CancelledError:
        if control.aborted.is_set() and not _being_cancelled():
            raise _Aborted() from None
        raise
    finally:
        control._inner = None


async def _run(spec: Spec, session, prompt, on_event, control: Control) -> Result:
    from . import memgate
    tools = memgate.screen(spec.kind, [t for t in spec.tools if spec.kind in t.kinds])  # 13d：读写记忆的工具再照读写表筛一遍
    by_name = {t.name: t for t in tools}
    conv = spec.conversation or "-"

    def emit(ev: dict) -> None:
        if on_event is None:
            return
        try:
            on_event(ev)
        except Exception:  # noqa: BLE001 — 界面那边（进度条、插话）的毛病不拖累这一轮
            pass
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
    prompts, added, recovered = _as_list(prompt), False, False
    pick = fallback.Picker(spec)  # 这一轮用哪个模型（第 13 步：备用链、冷却；没配备用链 = 一直是 spec.model）

    def add_prompts() -> None:
        nonlocal added
        if not added:
            added = True
            for m in prompts:
                add(m)

    def commit(m: dict, kind: str) -> None:
        """插话、后续写进会话：这时才算用上了（调用方据此认定那句并进了这一轮）。"""
        _note_steer(tid, conv, kind)
        add(m)
        emit({"type": kind, "ref": m.get("ref")})

    try:
        if spec.prepare is not None:
            try:
                prompts = list(await _abortable(control, spec.prepare(tid, prompts)))
            except Exception:  # noqa: BLE001 — 备料、压缩是锦上添花：出错了（或者被停了，_Aborted）这句话照原样进会话
                pass
            history = session.messages()  # 可能刚压缩过、补过备料指纹
        add_prompts()
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
            emit({"type": "model", "step": steps})
            try:
                msg, fm = await _model_step(spec, tid, history, tools, emit, control, partial, pick)
            except EngineError as e:
                # 上下文太长（模型还没出字就报的）：压缩一次再问；别的错、压不了、这一轮已经压过一次 → 照常报错
                if spec.recover is None or recovered or control.aborted.is_set():
                    raise
                recovered = True
                try:
                    redo = await _abortable(control, spec.recover(tid, e))
                except _Aborted:  # 压到一半用户点了停
                    status = "aborted"
                    break
                if not redo:
                    raise
                history = session.messages()
                steps -= 1
                continue
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
                    commit(m, "steer")
                continue
            if stop == "pause":  # 服务端工具停在半路（现在不用服务端工具，留着）：接着问
                continue
            # 说完了：先看有没有插话（有就带着再问一次），没有插话才轮到后续（pi：后续等它真要停下的时候）
            steers = control.take_steers()
            follows = [] if steers else control.take_follow()
            if steers or follows:
                for m in steers:
                    commit(m, "steer")
                for m in follows:
                    commit(m, "follow_up")
                continue
            status = "length" if stop == "length" else "done"
            break
    except asyncio.CancelledError:
        add_prompts()  # 压缩到一半被停：这句话照样记进会话（app 里已经有它了）
        _close_open_calls(history, add, "Not run: the turn was cancelled.", partial)
        control.close()
        events.turn_close(tid, conversation=conv, status="aborted", usage=usage, error="cancelled", models=pick.used)
        raise
    except EngineError as e:
        status, error, ekind, retryable = "error", str(e), e.kind, e.retryable
    except Exception as e:  # noqa: BLE001 — 这一轮记成出错，不让一个意外把调用方带崩
        status, error, ekind = "error", f"{type(e).__name__}: {str(e)[:200]}", "internal"
    add_prompts()
    _close_open_calls(history, add, "Not run: the turn ended before this call.")
    left = control.close()
    events.turn_close(tid, conversation=conv, status=status, usage=usage, error=error, models=pick.used)
    return Result(turn=tid, status=status, text=last_text, messages=new, usage=usage, model=pick.final or pick.ref, error=error, error_kind=ekind,
                  retryable=retryable, first_ms=first_ms, pending=left, models=list(pick.used))


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


RESTART_NO_RESULT = "No result: the server restarted while this call was running, so it may or may not have finished."
CANCELLED_MID_CALL = "No result: the turn was cancelled while this call was running, so it may or may not have finished."
RESTART_NOTE = ("(The server restarted while the previous turn was still running, so it stopped there and the user did not get the rest "
                "of your reply. Tool calls without a proper result may or may not have finished: check before redoing anything.)")


def recover() -> list[dict]:
    """服务启动时（chat.resume_engine 调）：上次停机时还在跑的回合标成 aborted（events.recover），存在库里的会话补完整——
    最后一条回复里没结果的工具调用各补一条（可能做了、可能没做，别当成没做），主人回合再补一句 system 说上一轮被重启打断了
    （OpenClaw 补一条隐藏的 <turn_aborted>，我们照它的意思往后补；只补这一轮真写进过会话的）。正在流的半截字在内存里，没了。
    → 这些回合（chat.py 按它给 app 那边补「没拿到回复」）。"""
    out = events.recover()
    for t in out:
        sid = t.get("session")
        s = DbSession.get(sid) if sid else None
        if s is None:
            continue
        try:
            if not any(r.get("turn") == t["id"] for r in s.path()):
                continue
            history = s.messages()

            def add(m: dict, _s=s, _tid=t["id"], _h=history) -> None:
                _h.append(m)
                _s.append(m, turn=_tid)
            _close_open_calls(history, add, RESTART_NO_RESULT)
            if t.get("kind") == "owner":
                add(system_note(RESTART_NOTE))
        except Exception as e:  # noqa: BLE001 — 补不上：发给模型之前 session.repair 还会补没结果的调用，不至于整轮报错
            print(f"[engine] 重启后补会话 {sid} 没成：{type(e).__name__}: {str(e)[:120]}")
    return out


def _session_of(turn_id: str, session_id: str | None) -> DbSession | None:
    """这一轮写进去过的那条会话（session_id 不给就按这一轮记的找）；没写进去过 → None。"""
    if session_id is None:
        with store.read() as conn:
            r = conn.execute("SELECT session FROM engine_turns WHERE id=?", (turn_id,)).fetchone()
        session_id = r["session"] if r else None
    s = DbSession.get(session_id) if session_id else None
    return s if s is not None and any(r.get("turn") == turn_id for r in s.path()) else None


def note_restart(turn_id: str, session_id: str | None = None) -> None:
    """关服务时停下（chat.stop_engine_runs）或者被取消、app 那边没拿到后面的一轮：会话当时已经补完整了，这里只补那句 system
    （RESTART_NOTE），下一轮它知道上一轮被重启打断、用户没看到后面。session_id 不给就按这一轮记的找。"""
    if (s := _session_of(turn_id, session_id)) is not None:
        s.append(system_note(RESTART_NOTE), turn=turn_id)


STOPPED_NOTE = ("(The user stopped this turn here. Treat the request above as dropped, not as unfinished work: don't pick it back up or "
                "finish it on your own unless the user asks for it again.)")


def note_stopped(turn_id: str, session_id: str | None = None) -> None:
    """用户点了停（chat.engine_stream；关服务停的补 RESTART_NOTE，定时任务的看门狗停的不补）：会话里补一句 system（带 "stopped"），
    下一轮、压缩摘要（compact.serialize）都知道这件事是用户叫停的，不当成没做完的待办接着做。session_id 同 note_restart。"""
    if (s := _session_of(turn_id, session_id)) is not None:
        s.append({**system_note(STOPPED_NOTE), "stopped": True}, turn=turn_id)


def _note_steer(tid: str, conv: str, kind: str = "steer") -> None:
    sid = events.step_open(tid, type_=kind, conversation=conv)
    events.step_close(sid, status="ok", conversation=conv, turn=tid)


def _switched(tid: str, conv: str, emit, sw: dict) -> None:
    """换模型了（engine/fallback.py）：交给 on_event、实时广播，服务器日志里记一行。"""
    ev = {"type": "model_switch", "from": sw["from"], "to": sw["to"], "reason": sw["reason"]}
    emit(ev)
    events.publish(conv, {**ev, "turn": tid})
    print(f"[engine] {conv}：{sw['from']} → {sw['to']}（{sw['reason']}）")


async def _model_step(spec: Spec, tid: str, history: list[dict], tools: list[Tool], emit, control: Control,
                      partial: list[str], pick: fallback.Picker | None = None) -> tuple[dict | None, int | None]:
    """调一次模型。→ (assistant 消息, 第一个字用了多少毫秒)。还没出字就报了能重试的错：等一下再试（最多两次）；重试用完了（或者本来
    就不能重试）、备用链里有能换的（engine/fallback.py）：换一把密钥或者换下一个模型，重试次数从头算，事件表里这一步记成两条
    （出错的那条 data.switch 写着换到哪）。被 abort 时回已经出来的字（stop = aborted；一个字都没有就回 None）。"""
    conv = spec.conversation or "-"
    pick = pick or fallback.Picker(spec)
    if (sw := pick.take_skipped()) is not None:  # 主模型在冷却：一开始就用了链里的下一个
        _switched(tid, conv, emit, sw)
    ref = pick.ref
    sid = events.step_open(tid, type_="model", name=ref, conversation=conv)
    req = providers.Request(system=spec.system, messages=list(history), tools=[t.spec() for t in tools], max_tokens=spec.max_tokens,
                            effort=spec.effort, display=spec.display, json=spec.json, timeout=spec.timeout, cache_ttl=spec.cache_ttl,
                            cache_tail=spec.cache_tail)
    t0 = time.monotonic()
    first: list[float] = []
    attempt = 0

    async def consume() -> dict:
        pick.calling(ref)
        req.quick = pick.can_switch()  # 备用链里真有能换的：各家那一层不重试（MODEL_RETRIES 照旧）
        async for ev in providers.stream(ref, req):
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
            if (e.retryable and not first and attempt < len(MODEL_RETRIES) and not control.aborted.is_set()
                    and not (e.kind == "timeout" and req.quick and spec.kind == "outsider")):  # 一次性的外人回合有总时限：超时直接换
                await asyncio.sleep(MODEL_RETRIES[attempt])  # 还没出字：再来一次（pi、Hermes 都在这一层重试）
                attempt += 1
                continue
            # 重试用完了：换一把密钥 / 换下一个模型（这一步已经给用户看到字了就不换；外人回合的字没人看）
            visible = spec.kind != "outsider" and bool("".join(partial).strip())
            sw = None if control.aborted.is_set() else pick.failed(ref, e, visible)
            events.step_close(sid, status="error", data={"error": str(e)[:200], "kind": e.kind, "attempts": attempt + 1,
                                                         **({"switch": sw} if sw else {})}, conversation=conv, turn=tid)
            if sw is None:
                out = pick.exhausted(e)
                if out is e:
                    raise
                raise out from e
            if sw["to"] != ref:
                _switched(tid, conv, emit, sw)
            ref, attempt = sw["to"], 0
            first.clear()
            partial.clear()
            sid = events.step_open(tid, type_="model", name=ref, conversation=conv)
            continue
        finally:
            control._inner = None
    fm = int(first[0] * 1000) if first else None
    partial.clear()
    pick.ok(ref, req.key)
    events.step_close(sid, status="ok", data={"usage": msg.get("usage"), "stop": msg.get("stop"), "first_ms": fm, "model": msg.get("model"),
                                              "served": msg.get("served"), "attempts": attempt + 1, "ref": ref}, conversation=conv, turn=tid)
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
        raw = c.get("args") if isinstance(c.get("args"), dict) else {}
        if tool.prepare is not None:
            try:
                raw = tool.prepare(raw)
            except Exception:  # noqa: BLE001 — 修不了就原样交给下面查
                pass
        args, problem = check_args(raw, tool.schema)
        if c.get("bad_args"):
            problem = "the input is not valid JSON"
        if problem:
            add(tool_result(cid, name, f"Not run: {problem}. Fix the input and call again.", True))
            continue
        ctx = ToolCtx(turn=tid, kind=spec.kind, agent=spec.agent, conversation=conv, call_id=cid, control=control, emit=emit,
                      origin=models.stricter(spec.origin, spec.via))
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
        elif tool.level(args)[0] in ("ask", "off"):
            verdict = ("Not run: this action can't be undone and needs the user's OK, but nobody can approve it here. "
                       "The user has NOT consented. Do not retry it or try to reach it another way; tell the user what you wanted to do.")
        stopped = verdict is None and control.aborted.is_set()  # 关卡问到一半用户点了停
        if stopped:
            verdict = "Not run: the user stopped this turn."
        if verdict is not None:
            handed = isinstance(verdict, dict)  # 交给用户了（approval-pending）；或者关卡这一轮就等到了用户的决定（对外接口，engine/openai_api.py）
            bad = not handed or verdict.get("error") is True
            how = ("stopped" if stopped else "denied") if not handed else "pending" if verdict.get("status") == "approval-pending" \
                else "error" if bad else "ok"
            add(tool_result(cid, name, _result_text(verdict), bad))
            events.step_close(sid, status="denied" if how == "stopped" else how, conversation=conv, turn=tid)
            emit({"type": "tool_end", "id": cid, "name": name, "ok": not bad, "status": how})
            continue
        task = asyncio.ensure_future(asyncio.wait_for(tool.run(args, ctx), tool.timeout))
        control._inner = task
        ok, res, how = True, None, "ok"
        try:
            res = await task
        except asyncio.CancelledError:
            if control.aborted.is_set() and not _being_cancelled():
                ok, res, how = False, "Stopped by the user.", "stopped"
            else:  # 外面取消（关服务）：跑到一半的这一个不能说「没做」，后面没轮到的才是没做（_run 收尾时补）
                add(tool_result(cid, name, CANCELLED_MID_CALL, True))
                events.step_close(sid, status="aborted", conversation=conv, turn=tid)
                raise
        except asyncio.TimeoutError:
            ok, res, how = False, f"Timed out after {tool.timeout:.0f}s.", "timeout"
        except ToolFailure as e:
            ok, res, how = False, str(e) or "The tool failed.", "error"
        except Exception as e:  # noqa: BLE001 — 工具出错回给模型，它自己决定怎么办
            ok, res, how = False, f"{type(e).__name__}: {str(e)[:300]}", "error"
        finally:
            control._inner = None
        text = _result_text(res)
        pics = res.images if isinstance(res, ToolOutput) else []
        add({**tool_result(cid, name, _clip(text, tool.max_output), not ok, images=pics), **(res.extra if isinstance(res, ToolOutput) else {})})
        events.step_close(sid, status="ok" if ok else "error", data={"chars": len(text), **({"images": len(pics)} if pics else {})},
                          conversation=conv, turn=tid)
        emit({"type": "tool_end", "id": cid, "name": name, "ok": ok, "status": how})
