"""execute_code（第 3 批第 13 项，2026-10-19）：在这个 Agent 的沙箱里跑一段 Python，代码里经本地 RPC 调我们的工具——好几步的活
（搜 → 打开 → 筛 → 归纳）一步做完，只有 print 的东西回给模型。照 Hermes tools/code_execution_tool.py（MIT）：工具做成同名函数
（另有 call_tool(name, **args)、tools()），调用一次一问一答，结果是工具原样回的字，标准输出太长留头尾；只借设计，代码是重写的。

开关 engine.tools.execute_code：true，或者 {"tools": ["write_file", …], "timeout": 300, "max_calls": 50}；不写 = 没有。这个 Agent
  有沙箱（engine.sandbox，服务器装好了）才有；只在沙箱里的 Agent（代办）不给。
怎么接：sandbox.piped 起 docker exec -i … python3 -I -u -c RUNNER，标准输入、输出就是 RPC 的线（一行一个 JSON）：
  → 第一行：代码、这一次的令牌、工具名单、输出放哪
  ← {"t": "call", "id", "token", "tool", "args"}   → {"id", "text", "error"?, "pending"?}
  ← {"t": "done", "ok", "error", "size", "head", "tail", "kept"}
  RUNNER 先把这两条线 dup 走、0 号接 /dev/null、1 / 2 号接容器里的一个文件（print、子进程的输出都进它，混不进 RPC），-I 让它自己的
  import 不被 /workspace 里同名的文件顶掉（跑用户代码前再把当前目录放回 sys.path）。不用联网，不用多挂目录：沙箱没有网也能用。
令牌：一次执行一把（mcp_engine.grant，只在内存里），每个调用都拿它比（mcp_engine.lookup），跑完（做完、出错、到点、点了停）收回
  （mcp_engine.revoke）。工具 = 这个 Agent 这一轮本来就有的（tools.for_agent，去掉 NEVER），按它跑（mcp_engine.call：prepare、按 schema
  查、关卡、超时、点了停就不做），关卡是 gate_for：不是 read 的调用不做，除非用户在 tools 里放了那个工具；放了的照 tools.gate：
  撤得回的直接做，要点头的照样交卡（代码里抛 NeedsApproval，脚本停在那里），engine.tools.levels 写 off 的照样不做。
分类：同 sandbox_exec：只动沙箱 = undo；沙箱能写真的工作区（access rw）= ask（卡上是整段代码，同意了照原样跑一次，里面的调用照样过关卡）。
没人在看的那几轮、后台子 Agent 不给（unattended.NO_TOOLS、tasks.CHILD_NEVER）。一次最多 max_calls 个调用、timeout 秒。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import shlex
import time
import uuid

from i18n import L

from .loop import Tool, ToolCtx, ToolFailure

NAME = "execute_code"
READ, UNDO, ASK = "read", "undo", "ask"
TIMEOUT, TIMEOUT_MAX = 300, 900
MAX_CALLS = 50             # 一次最多几个工具调用（Hermes 的 DEFAULT_MAX_TOOL_CALLS）
CODE_MAX = 100_000
FRAME_MAX = 8 * 1024 * 1024
HEAD, TAIL = 30_000, 15_000   # 标准输出留头、尾多少字节（Hermes 50 KB，四六开；出错多在后面）
OUT_DIR = "/tmp/.mousse-code"  # 容器里放这次输出的地方（太长的留着，sandbox_read 能翻）
NEVER = frozenset({NAME, "tasks", "skill_manage"})  # 代码里不给的：自己、派后台任务、改自己的做法

RUNNER = r'''
import json, linecache, os, sys, threading, traceback, types
_pin = os.fdopen(os.dup(0), "rb")
_pout = os.fdopen(os.dup(1), "wb")
_first = json.loads(_pin.readline() or b"{}")
_null = os.open(os.devnull, os.O_RDONLY)
os.dup2(_null, 0)
os.close(_null)
_path = _first["out"]
os.makedirs(os.path.dirname(_path), mode=0o700, exist_ok=True)
_fd = os.open(_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
sys.stdout.flush()
sys.stderr.flush()
os.dup2(_fd, 1)
os.dup2(_fd, 2)
os.close(_fd)
_lock = threading.Lock()
_n = [0]


def _send(obj):
    _pout.write((json.dumps(obj, ensure_ascii=False, default=str) + "\n").encode("utf-8", "replace"))
    _pout.flush()


class ToolError(Exception):
    """A tool call failed or was refused; the message says why."""


class NeedsApproval(Exception):
    """The call is waiting for the user's OK (an inbox card): it runs later, outside this script."""


def call_tool(name, **args):
    """Call one of your tools by name; returns its text."""
    with _lock:
        _n[0] += 1
        _send({"t": "call", "id": _n[0], "token": _first["token"], "tool": name, "args": args})
        line = _pin.readline()
    if not line:
        raise ToolError("the server stopped answering")
    r = json.loads(line)
    if r.get("pending"):
        raise NeedsApproval(r.get("text") or "waiting for the user's OK")
    if r.get("error"):
        raise ToolError(r.get("text") or "the tool failed")
    return r.get("text", "")


def tools():
    """The tools you can call here: name -> one line."""
    return dict(_first.get("tools") or {})


_mod = types.ModuleType("mousse_tools")
_g = {"__name__": "__main__", "__builtins__": __builtins__}
for _k, _v in (("call_tool", call_tool), ("tools", tools), ("ToolError", ToolError), ("NeedsApproval", NeedsApproval)):
    setattr(_mod, _k, _v)
    _g[_k] = _v
for _name, _doc in (_first.get("tools") or {}).items():
    if _name.isidentifier() and not _name.startswith("_"):
        _f = (lambda n: lambda **kw: call_tool(n, **kw))(_name)
        _f.__name__, _f.__doc__ = _name, _doc
        setattr(_mod, _name, _f)
        _g.setdefault(_name, _f)
sys.modules["mousse_tools"] = _mod
sys.path.insert(0, os.getcwd())
_code = _first.get("code") or ""
linecache.cache["<execute_code>"] = (len(_code), None, _code.splitlines(True), "<execute_code>")
_ok, _err = True, None
try:
    exec(compile(_code, "<execute_code>", "exec"), _g)
except SystemExit as e:
    _ok = e.code in (None, 0)
    _err = None if _ok else "SystemExit: %s" % (e.code,)
except BaseException as e:
    _ok, _err = False, "%s: %s" % (type(e).__name__, e)
    _tb = [f for f in traceback.extract_tb(e.__traceback__) if f.filename == "<execute_code>"]
    sys.stderr.write("Traceback (most recent call last):\n" + "".join(traceback.format_list(_tb))
                     + "".join(traceback.format_exception_only(type(e), e)))
for _s in (sys.stdout, sys.stderr):
    try:
        _s.flush()
    except Exception:
        pass
_h, _t = int(_first.get("head") or 30000), int(_first.get("tail") or 15000)
try:
    _size = os.path.getsize(_path)
    with open(_path, "rb") as _f:
        _head = _f.read(_h + _t if _size <= _h + _t else _h)
        _tail = b""
        if _size > _h + _t:
            _f.seek(_size - _t)
            _tail = _f.read()
except OSError:
    _size, _head, _tail = 0, b"", b""
_kept = _size > _h + _t
if not _kept:
    try:
        os.unlink(_path)
    except OSError:
        pass
_send({"t": "done", "ok": _ok, "error": _err, "size": _size, "head": _head.decode("utf-8", "replace"),
       "tail": _tail.decode("utf-8", "replace"), "kept": _kept})
'''


def cfg() -> dict | None:
    """engine.tools.execute_code：开着 → 设置（true = {}）；关着 → None。"""
    from config import raw
    e = raw().get("engine")
    t = e.get("tools") if isinstance(e, dict) else None
    v = t.get(NAME) if isinstance(t, dict) else None
    if v is True:
        return {}
    if isinstance(v, dict) and v.get("enabled", True) is not False:
        return v
    return None


def _int(v, default: int, lo: int, hi: int) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def writes(c: dict) -> set[str]:
    """用户放了的、代码里能改东西的工具（照样过关卡）。"""
    v = c.get("tools")
    return {str(x).strip() for x in v if isinstance(x, str) and x.strip()} if isinstance(v, list) else set()


def gate_for(allowed: set[str]):
    """代码里的每个调用：不是 read 的不做（用户放了的工具除外）；剩下的照主人回合的关卡（tools.gate：levels 的 off、计划模式、要点头的交卡）。"""
    from . import tools as tools_mod

    async def gate(tool: Tool, args: dict, ctx: ToolCtx):
        if tool.level(args)[0] != READ and tool.name not in allowed:
            return L(f"没跑：execute_code 里只做只读的调用（{tool.name} 这次会改东西）。要做就直接调这个工具，或者请用户在 "
                     "engine.tools.execute_code.tools 里放它。",
                     f"Not run: inside execute_code only read-only calls run ({tool.name} would change something here). Call that tool "
                     "directly instead, or ask the user to allow it in engine.tools.execute_code.tools.")
        return await tools_mod.gate(tool, args, ctx)
    return gate


def one_line(t: Tool) -> str:
    first = next((ln.strip() for ln in str(t.description or "").splitlines() if ln.strip()), "")
    keys = ", ".join(list((t.schema.get("properties") or {}))[:12])
    return (first[:160] + ("…" if len(first) > 160 else "")) + (f" (args: {keys})" if keys else "")


def pending_of(text: str) -> dict | None:
    try:
        v = json.loads(text)
    except ValueError:
        return None
    return v if isinstance(v, dict) and v.get("status") == "approval-pending" else None


class Lines:
    """一行一行读（自己切，不受 StreamReader 每行 64 KB 的限制）。"""

    def __init__(self, stream) -> None:
        self.s, self.buf = stream, bytearray()

    async def next(self) -> bytes | None:
        while True:
            i = self.buf.find(b"\n")
            if i >= 0:
                line = bytes(self.buf[:i])
                del self.buf[: i + 1]
                return line
            if len(self.buf) > FRAME_MAX:
                raise ToolFailure(L("代码那边发来的一条太大了", "A message from the code was too big"))
            chunk = await self.s.read(65536)
            if not chunk:
                line, self.buf = bytes(self.buf), bytearray()
                return line or None
            self.buf += chunk


HINTS = (
    (r"^NeedsApproval", "A call is waiting for the user's OK, so the script stopped there. Tell the user; once they approve, the server "
                        "runs that call and posts the result into this chat. Do the rest after that."),
    (r"^ToolError", "A tool call failed: read the message, fix the arguments (tools() lists the tools and their arguments) and run again."),
    (r"^ModuleNotFoundError: No module named '([\w.]+)'", "That module isn't installed in the sandbox: use the standard library (requests is "
                                                         "there) or what sandbox_exec can install."),
)


def hint_of(err: str | None) -> str:
    for pat, text in HINTS:
        if err and re.search(pat, err):
            return text
    return ""


async def execute(agent: str, code: str, ctx: ToolCtx, *, timeout: int, max_calls: int, allowed: set[str]) -> str:
    from . import handkit, mcp_engine, sandbox
    from . import tools as tools_mod
    table = [t for t in tools_mod.for_agent(agent) if t.name not in NEVER]
    tok = mcp_engine.grant(agent=agent, tools=table, gate=gate_for(allowed), conversation=ctx.conversation, turn=ctx.turn or None,
                           control=ctx.control, emit=ctx.emit, origin=ctx.origin)
    out_path = f"{OUT_DIR}/{uuid.uuid4().hex[:16]}.out"
    first = {"code": code, "token": tok, "tools": {t.name: one_line(t) for t in table}, "out": out_path, "head": HEAD, "tail": TAIL}
    st: dict = {"calls": 0, "errors": 0, "pending": [], "noise": []}
    t0 = time.monotonic()
    done: dict | str | None = None
    try:
        async with sandbox.piped(agent, ["python3", "-I", "-u", "-c", RUNNER]) as proc:
            async def send(obj: dict) -> None:
                proc.stdin.write(json.dumps(obj, ensure_ascii=False, default=str).encode("utf8") + b"\n")
                await proc.stdin.drain()

            async def answer(f: dict) -> dict:
                rid = f.get("id")
                if st["calls"] >= max_calls:
                    return {"id": rid, "error": True, "text": f"Not run: this run already made {max_calls} tool calls, the most one run may make."}
                g = mcp_engine.lookup(str(f.get("token") or ""))
                if g is None:
                    return {"id": rid, "error": True, "text": "Not run: this run's token isn't valid any more."}
                st["calls"] += 1
                with handkit.unapproved():  # 卡上点过头的是整段代码；里面每一次调用照样过关卡、web 的照样交 Doorman
                    text, err = await mcp_engine.call(g, str(f.get("tool") or ""), f.get("args") if isinstance(f.get("args"), dict) else {})
                if not err and (p := pending_of(text)) is not None:
                    st["pending"].append(str(p.get("inbox") or ""))
                    return {"id": rid, "pending": True, "text": str(p.get("message") or text)}
                st["errors"] += int(err)
                return {"id": rid, "error": err, "text": text}

            async def converse() -> dict | None:
                lines = Lines(proc.stdout)
                await send(first)
                while True:
                    raw = await lines.next()
                    if raw is None:
                        return None
                    try:
                        f = json.loads(raw)
                    except ValueError:
                        st["noise"].append(raw.decode("utf8", "replace")[:500])
                        continue
                    if not isinstance(f, dict):
                        continue
                    if f.get("t") == "done":
                        return f
                    if f.get("t") == "call":
                        await send(await answer(f))
            try:
                done = await asyncio.wait_for(converse(), timeout)
            except asyncio.TimeoutError:
                done = "timeout"
            except (BrokenPipeError, ConnectionResetError):
                done = None
    except sandbox.SandboxError as e:
        raise ToolFailure(str(e)) from None
    finally:
        mcp_engine.revoke(tok)
    secs = f"{time.monotonic() - t0:.1f}s"
    calls = f"{st['calls']} tool call{'s' if st['calls'] != 1 else ''}" + (f", {st['errors']} failed" if st["errors"] else "")
    if st["pending"]:
        calls += f", {len(st['pending'])} waiting for the user's OK (inbox card {', '.join(st['pending'])})"
    if done == "timeout":
        tail = ""
        try:
            got = await sandbox.run(agent, f"tail -c {TAIL} {shlex.quote(out_path)} 2>/dev/null", timeout=15)
            tail = got.output.strip()
        except Exception:  # noqa: BLE001 — 拿不到就不给
            pass
        body = _wrap(tail) if tail else ""
        return (body + "\n" if body else "") + (f"[stopped after {timeout}s: the code didn't finish; everything it started was stopped · {calls}]"
                                                f"{' Its output so far is in the sandbox at ' + out_path if tail else ''}")
    if not isinstance(done, dict):
        said = " ".join(st["noise"])[-800:]
        return L(f"代码没跑完就退出了（{secs}，{calls}）" + (f"：{said}" if said else ""),
                 f"The code process ended without finishing ({secs}, {calls})" + (f": {said}" if said else "")
                 + (" (is python3 installed in the sandbox?)" if "python3" in said else ""))
    head, tail, size = str(done.get("head") or ""), str(done.get("tail") or ""), int(done.get("size") or 0)
    text = head if not done.get("kept") else (f"{head}\n[… {max(0, size - HEAD - TAIL)} bytes cut from the middle; the whole output is in the "
                                              f"sandbox at {out_path} (sandbox_read pages it) …]\n{tail}")
    status = f"[ok · {secs} · {calls}]" if done.get("ok") else f"[error: {done.get('error') or 'failed'} · {secs} · {calls}]"
    if not done.get("ok") and (h := hint_of(str(done.get("error") or ""))):
        status += f"\n{h}"
    return (_wrap(text.rstrip("\n")) if text.strip() else "(no output)") + "\n" + status


def _wrap(text: str) -> str:
    from .tools import wrap_untrusted
    return wrap_untrusted("execute_code", text)


async def run(args: dict, ctx: ToolCtx) -> str:
    c = cfg() or {}
    code = str(args.get("code") or "")
    if not code.strip():
        raise ToolFailure(L("要写 code（一段 Python）", "Give code (a Python script)"))
    if len(code) > CODE_MAX or "\x00" in code:
        raise ToolFailure(L(f"代码太长（最多 {CODE_MAX} 字）", f"The code is too long (at most {CODE_MAX} characters)"))
    timeout = _int(args.get("timeout") or c.get("timeout") or TIMEOUT, TIMEOUT, 1, TIMEOUT_MAX)
    return await execute(ctx.agent or "main", code, ctx, timeout=timeout, max_calls=_int(c.get("max_calls"), MAX_CALLS, 1, 500),
                         allowed=writes(c))


async def ask(tool: Tool, args: dict, ctx: ToolCtx) -> dict | str:
    """沙箱能写真的工作区（rw）：交一张卡，卡上是整段代码（一个字不截）；同意了 handkit.run_approved 照原样跑一次，里面的调用照样过关卡。"""
    import inbox

    from . import handkit
    from . import tools as tools_mod
    agent = ctx.agent or "main"
    code = str(args.get("code") or "")
    if (no := handkit.too_long(code)) is not None:
        return no
    who = inbox.source_name(agent, inbox.names())
    reason = tool.level(args)[1].rstrip("。. ")
    fence = tools_mod._fence(code)
    card = {"title": L(f"在沙箱里跑代码：{title(args)}", f"Run code in the sandbox: {title(args)}"),
            "why": str(args.get("why") or "").strip() or L(f"{who}想在沙箱里跑一段代码。{reason}。", f"{who} wants to run code in the sandbox. {reason}."),
            "changes": [L("在沙箱里跑下面这段 Python；它能改你的工作区", "Runs the Python below in the sandbox; it can change your workspace")],
            "detail": L("同意后，服务器照下面的代码跑一次（里面调的工具照样过关卡），结果发回对话。", "If you approve, the server runs exactly this "
                        "code once (the tools it calls still go through their checks) and posts the result to the chat.")
            + f"\n\n{fence}python\n{code}\n{fence}",
            "approveLabel": L("照做", "Do it")}
    dedupe = "code:" + hashlib.sha256(json.dumps([agent, code], ensure_ascii=False).encode()).hexdigest()[:32]
    return await tools_mod.submit_card(NAME, json.dumps({"code": code, "timeout": args.get("timeout")}, ensure_ascii=False), None, card, dedupe, ctx,
                                       who=agent)


def title(a: dict) -> str:
    first = next((ln.strip() for ln in str(a.get("code") or "").splitlines() if ln.strip() and not ln.strip().startswith("#")), "")
    return first[:60]


def tool(agent: str) -> Tool | None:
    c = cfg()
    if c is None:
        return None
    from . import hands, sandbox
    if sandbox.box(agent) is None or hands.confined(agent):
        return None
    from .handkit import levelled
    rw = sandbox.mounted(agent, scan=False) == "rw"

    def cls(a: dict) -> tuple[str, str]:
        return levelled(NAME, (ASK, hands.why_rw()) if rw else (UNDO, hands.why_sandbox()))
    n, secs = _int(c.get("max_calls"), MAX_CALLS, 1, 500), _int(c.get("timeout"), TIMEOUT, 1, TIMEOUT_MAX)
    return Tool(
        name=NAME, kinds=("owner",), run=run, classify=cls, title=title, timeout=TIMEOUT_MAX + 60, max_output=HEAD + TAIL + 4000,
        description=L(
            "在你的沙箱里跑一段 Python（标准库和 requests；在 /workspace 里跑，上网经 Doorman），代码里能把你的工具当函数调，好几步的活一步做完："
            "比如 hits = web_search(query=\"…\")、text = read_file(path=\"notes.md\")，回的是工具原样的字；call_tool(name, **args) 按名字调任何一个，"
            "tools() 列出有哪些。只有 print 的东西回给你（太长留头尾）。代码里只做只读的调用（用户放了的工具除外）；要用户点头的会交卡并抛 "
            f"NeedsApproval，出错的抛 ToolError。一次最多 {n} 个调用、默认 {secs} 秒。",
            "Run a Python script in your sandbox (standard library and requests; runs in /workspace; online only through Doorman) that calls "
            "your tools as functions, so a multi-step job takes one step: e.g. hits = web_search(query=\"…\"), text = read_file(path=\"notes.md\"); "
            "each returns the tool's text, call_tool(name, **args) reaches any tool by name and tools() lists them. Only what you print comes "
            "back (long output keeps the start and the end). Only read-only calls run inside the code (unless the user allowed a tool); a call "
            f"that needs the user's OK sends a card and raises NeedsApproval, a failed one raises ToolError. At most {n} calls; {secs} s by default."),
        schema={"type": "object", "required": ["code"], "properties": {
            "code": {"type": "string", "description": L("Python 代码", "The Python code")},
            "timeout": {"type": "integer", "description": L(f"几秒后停掉（默认 {secs}，最多 {TIMEOUT_MAX}）", f"Seconds before it's stopped (default {secs}, max {TIMEOUT_MAX})")}}})
