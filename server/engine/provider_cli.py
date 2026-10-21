"""用户自己在这台电脑上登录的官方命令行，当主人回合的模型（第 7 批，2026-10-19）：
  codex/<型号>    OpenAI 的 Codex CLI：codex/default = 不带 --model（它自己配的那个）；别的照原样交给 -m
默认关：server.json 的 engine.codex 写 true（或写成设置）才有（新功能对已经在用的安装默认关，上线时问主人）。
10/21：copilot/…（GitHub Copilot CLI）已不再支持。这里不再起那个命令；配过的由 models.retired_plan 当成不存在。

条款和做法照 engine/claude_brain.py：只在这台电脑上起官方的命令，用它自己存的登录；我们不读、不存、不传它的令牌，不拿它的令牌直接调接口
（Hermes 那种把别家命令行的登录借过来自己刷新的不做）；环境变量里像密钥的都去掉（runners.child_env），它只能用它自己存的登录，不会悄悄
改用这台机器上的 OPENAI_API_KEY、GH_TOKEN 这些。这里是用户自己的服务器替他本人跑，不给别人用他的登录（两家的条款都不许把账号交给别人用）。
  Codex（10/19 读）：learn.chatgpt.com/docs/pricing 把「Codex SDK、codex exec、能写脚本的用法」列在套餐的用法里；/docs/non-interactive-mode：
    「codex exec reuses saved CLI authentication by default」。登录了没有：照 runners 在后台查好的（runners.detect：codex login status 的
    退出码，0 = 登录着、那一行字是 Logged in using ChatGPT / an API key；没登录退出 1、Not logged in；10/19 拿 0.163.0 核对过），app 里
    列不列只看它；真要跑之前在线程里再现查一次（STATUS_TTL 秒内记着）。只认 ChatGPT 登录：用 API 密钥登录的按量记在用户自己的 OpenAI
    账上，当成套餐的话花费上限看不到它，所以不列、不跑（那把密钥照常加成 OpenAI 的服务商）。
  不做的（10/19 读的条款）：Gemini CLI 用 Google 登录的那条（geminicli.com/docs/resources/tos-privacy/，2026-04-10：用别的软件经 Gemini CLI
    的 OAuth 去用它背后的服务算违规，举的例子就是 OpenClaw）；Cursor、Qwen、Kimi 这几家没核实到允许、或者已经停了，也不做。
    Copilot CLI 10/21 起也不做（只留四家套餐：Claude、Grok、GPT、Kimi）。
谁能花（models.subscription = "codex"：是用户自己的套餐）：照 ChatGPT 套餐的规矩（models.may_spend：替用户自己的能花，后台的要用户在设置里
同意过）；替外人答的、对外接口不行。loop.run_turn 先挡一道；这里再按这一轮的 Spec（fallback.current）问 may_spend（备用链里不该有的
fallback.chain 已经去掉，这里是最后一道）。10/21 起不另看 kind：来路认了就能花。
一次模型调用 = 起一次命令，说完就退：
  开头 + 记录：我们这条会话（req.system、req.messages，先过 session.repair：用户的话带上当地时间）整理成一段字从标准输入给它（它不接着
    上一次：每次都是完整的记录，太长的留后面的 TRANSCRIPT_MAX 字；工具结果只留开头；不放命令行里：别的用户 ps 看得见，也有长度上限）；
    这一句里的图存进工作目录交给它（codex -i），完了删掉。
  工具：经 MCP 给（engine/mcp_engine.py 发这一次的临时令牌）：这一轮的整张工具表（Spec.tools，去掉 ask_user：等回答要这一轮的 Control，
    这里拿不到；要问就写在回复里）、同一套关卡（Spec.gate，没给 = tools.gate）。工具自己发的事件（todo 的清单……）和 MCP 调用的开始、
    结束（tool_run / tool_end）照样交给这一轮（app 照常画）。
  回：它说的每一段接起来（段之间空一行）当这一次的回复（assistant，没有工具调用：工具它已经经 MCP 用过了），stop = end；思考 → thinking
    事件（不存进会话：别家回放不了）。用量照它报的记，钱不记（套餐）。
  codex：runners.codex_cmd（--json --ephemeral、只读沙箱、approval_policy never、不读用户的 config.toml、shell / 看图 / 浏览器 / 电脑操作 /
    插件 / 再派子 Agent … 都关掉）；这里再加：hooks 关掉（用户的 hooks 会在主机上跑命令）、不读用户的 execpolicy 规则（--ignore-rules）、
    不上网搜（web_search = disabled）；我们的 MCP 是 required（连不上它就不跑，不会没有工具硬答），default_tools_approval_mode = approve
    （exec 的 approval_policy 是 never，要点头的 MCP 调用它一律拒掉，可我们的关卡在服务器这边，它那边不用再问）。令牌放环境变量。
隔离：工作目录 <data_dir>/codex/<Agent>/（0700、空的）；环境照 runners.child_env，再去掉换它连哪、走哪个账的（OPENAI_BASE_URL 这类）。
同时跑几个：engine.codex.max_concurrent（默认 2），多的排队（事件 phase queued）。一直没动静（req.timeout 秒，默认 600）就停下、
记成超时。停 = 整个进程组 SIGTERM，3 秒后 SIGKILL（runners.kill），这一次的令牌收回、还没跑的 MCP 调用一律不做。
出错：没装、没登录、登录失效（401）→ config / 401（备用链里有就换，fallback.py 冷却一阵）；额度用完、限流 → 429；型号不认 → 404；
  别的 → 502。
参考：runners.py 的 codex 那一套（后台任务的外援：同一个命令、同一份事件）、claude_brain.py 的隔离和规矩。只借设计，没拿代码。
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import os
import re
import shutil
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

from config import raw, settings

from . import fallback, models
from .errors import EngineError

CLI = "cli"                          # models.PROVIDERS 里这几家的 api
NAMES = {"codex": "Codex"}           # provider 名 → 给人看的名字
DEFAULTS = {"codex": "default"}       # 不指定型号的那个（codex/default：不带 --model）
DEFAULT_MODEL = DEFAULTS["codex"]
MAX_CONCURRENT = 2
TRANSCRIPT_MAX = 120_000             # 交给它的整段记录最多多少字（多了留后面的）
TOOL_RESULT_MAX = 1_500              # 记录里一条工具结果留多少字
STATUS_TTL = 60.0
EXTRA_OFF = ("hooks",)               # runners.CODEX_OFF 之外再关的
EXTRA_ARGS = ("--ignore-rules", "-c", 'web_search="disabled"', "-c", 'mcp_servers.openmousse.default_tools_approval_mode="approve"', "-c",
              "mcp_servers.openmousse.required=true", "-c", "mcp_servers.openmousse.tool_timeout_sec=330")
ERR_KEEP = 4000
UNSET = ("OPENAI_BASE_URL", "OPENAI_API_BASE", "OPENAI_ORG_ID", "OPENAI_ORGANIZATION", "OPENAI_PROJECT")
NOT_IN_CLI = {"ask_user"}            # 不经 MCP 给它的工具（见上）
TOOL_HINT = {"codex": "mcp__openmousse__…"}   # 它那边我们的工具叫什么
IMAGE_EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif"}
_status: dict[str, tuple[float, str | None]] = {}
_sems: dict[str, tuple[int, asyncio.Semaphore]] = {}


def cfg(name: str = "codex") -> dict:
    """server.json 的 engine.<name>：写了 true 或一份设置（enabled 不是 false）才开；没写、false = 关（{"enabled": False}）。"""
    e = raw().get("engine")
    v = e.get(name) if isinstance(e, dict) else None
    if v is True:
        return {}
    return v if isinstance(v, dict) and v.get("enabled", True) is not False else {"enabled": False}


def enabled(name: str) -> bool:
    return name in NAMES and cfg(name).get("enabled", True) is not False


def stub(name: str) -> dict:
    """models.provider 给这几家的那一份（没有地址、没有密钥：登录是命令行自己存的）。"""
    return {"name": name, "api": CLI, "url": "", "key": "", "keys": [], "key_name": "", "key_env": ""}


def bin_of(name: str = "codex") -> str | None:
    """照 runners（tasks.runners.codex.bin，没写找 PATH）。"""
    from . import runners
    return runners.bin_of(name)


def child_env(name: str = "codex") -> dict[str, str]:
    from . import runners
    env = runners.child_env()
    return {k: v for k, v in env.items() if k not in UNSET}


def login(name: str = "codex") -> str | None:
    """它自己的登录方式（chatgpt / api key），照 runners 在后台查好、记着的（runners.detect：login status 的退出码和那一行字，令牌
    一概不碰）；没装、没登录、还没查过 → None。借我们连着的 ChatGPT 套餐跑的那种（runners 的 chatgpt_plan：它自己没登录）只给后台任务，
    这里不算。不起命令：models.models() 这些在请求里调，不能卡住。"""
    from . import runners
    info = runners.available().get(name)
    if not info or info.get("chatgpt_plan"):
        return None
    return "chatgpt" if info.get("auth") == "chatgpt" else None  # 用 API 密钥登录的按量记在用户自己的 OpenAI 账上，不算套餐：不列、不跑


async def login_now(name: str = "codex") -> str | None:
    """跑之前现查一次（在线程里起 login status，STATUS_TTL 秒内记着）：后台查过以后登出了的，这一次就不起 exec。
    回的是它自己说的登录方式（chatgpt / api key），stream 只认 chatgpt（见 login）。"""
    from . import runners
    now = time.monotonic()
    hit = _status.get(name)
    if hit and now - hit[0] < STATUS_TTL:
        return hit[1]
    info = await asyncio.to_thread(runners.detect, name)
    how = str(info.get("auth") or "") or None if info else None
    _status[name] = (now, how)
    return how


def ready(ref: str | None) -> bool:
    """这台电脑上现在跑得了这个模型吗（models.subscription_ready 问这里）：开着、装了、登录着。"""
    try:
        name, _ = models.parse_ref(ref or "")
    except EngineError:
        return False
    return enabled(name) and login(name) is not None


def offered() -> list[str]:
    """app 里多出来的：开着、登录着才有（engine.codex.models，默认只有 codex/default）。"""
    out: list[str] = []
    for name, default in DEFAULTS.items():
        if not ready(f"{name}/{default}"):
            continue
        ms = cfg(name).get("models")
        for m in [str(x).strip() for x in ms] if isinstance(ms, list) else [f"{name}/{default}"]:
            if models.subscription(m) == name and models.NAME.match(m.partition("/")[2]) and m not in out:
                out.append(m)
    return out


def _limit(name: str) -> asyncio.Semaphore:
    try:
        n = max(1, int(cfg(name).get("max_concurrent") or MAX_CONCURRENT))
    except (TypeError, ValueError):
        n = MAX_CONCURRENT
    hit = _sems.get(name)
    if hit is None or hit[0] != n:
        hit = _sems[name] = (n, asyncio.Semaphore(n))
    return hit[1]


def workdir(name: str, agent: str | None) -> Path:
    base = settings.data_dir / name
    base.mkdir(parents=True, exist_ok=True)
    os.chmod(base, 0o700)
    d = base / re.sub(r"[^A-Za-z0-9._-]", "_", agent or "main")
    d.mkdir(mode=0o700, exist_ok=True)
    return d


def _turn_of(conversation: str | None) -> str | None:
    """这个对话正在跑的那一轮（收件箱的卡挂在它上面）。"""
    from . import store
    try:
        with store.read() as conn:
            r = conn.execute("SELECT id FROM engine_turns WHERE conversation=? AND status='running' ORDER BY julianday(started_at) DESC, rowid DESC "
                             "LIMIT 1", (conversation or "-",)).fetchone()
    except Exception:  # noqa: BLE001
        return None
    return r["id"] if r else None


# —— 记录 → 一段字 ——————————————————————————————————————————————————————————————

def _text(msg: dict) -> str:
    return "".join(str(b.get("text") or "") for b in msg.get("content") or [] if isinstance(b, dict) and b.get("type") == "text")


def transcript(system: str, messages: list[dict], name: str = "codex") -> tuple[str, list[dict]]:
    """→ (交给它的整段字, 最后那句用户的话里的图)。见文件开头。"""
    from .session import repair  # 用户的话前面的当地时间 repair 已经带上（with_time）
    msgs = repair(list(messages))
    last_user = max((i for i, m in enumerate(msgs) if m.get("role") == "user"), default=None)
    lines: list[str] = []
    pictures: list[dict] = []
    for i, m in enumerate(msgs):
        role = m.get("role")
        if role == "user":
            imgs = [b for b in m.get("content") or [] if isinstance(b, dict) and b.get("type") == "image"]
            if i == last_user:
                pictures = [b for b in imgs if b.get("data")]
            lines.append("User: " + _text(m) + "".join(" [image]" for _ in imgs))
        elif role == "assistant":
            said = _text(m).strip()
            calls = [b.get("name") for b in m.get("content") or [] if isinstance(b, dict) and b.get("type") == "tool_call"]
            if said:
                lines.append("You: " + said)
            if calls:
                lines.append("(You called: " + ", ".join(str(c) for c in calls) + ")")
        elif role == "tool":
            out = _text(m)
            lines.append(f"Tool result ({m.get('name')}): " + (out if len(out) <= TOOL_RESULT_MAX else out[:TOOL_RESULT_MAX] + " …"))
        elif role == "system":
            lines.append("System note: " + _text(m))
    convo = "\n\n".join(lines)
    if len(convo) > TRANSCRIPT_MAX:
        convo = "…" + convo[-TRANSCRIPT_MAX:]
    tools = (f"Your tools are the openmousse MCP server's tools ({TOOL_HINT.get(name, 'openmousse')}); they run on the user's server with the "
             "usual safety checks. You have no shell and no file access here. Answer the user's latest message, in their language.")
    return f"{system.strip()}\n\n{tools}\n\n# The conversation so far (oldest first; the last line from the user is what to answer now)\n\n{convo}\n", pictures


# —— 出错 ——————————————————————————————————————————————————————————————————————

def failure(name: str, text: str) -> EngineError:
    """它报的错 → EngineError（fallback.reason_of 照 status 认：401 换、429 冷却、404 型号）。"""
    who, low = NAMES.get(name, name), text.lower()
    if re.search(r"401|unauthori[sz]ed|not logged in|log ?in again|authentication|expired", low):
        return EngineError(f"{who} isn't signed in on this computer (run {name} login): {text[:200]}", kind="config", status=401)
    if re.search(r"429|usage limit|rate limit|quota|too many requests|limit reached|limit exceeded|out of (ai )?credits|premium request", low):
        return EngineError(f"{who} hit its usage limit: {text[:200]}", status=429, retryable=False)
    if re.search(r"model .{0,60}(not found|not supported|does not exist|unknown|not available)|unsupported model|404", low):
        return EngineError(f"{who} can't use that model: {text[:200]}", status=404)
    return EngineError(f"{who} failed: {text[:300]}", status=502, retryable=False)


# —— 跑 ——————————————————————————————————————————————————————————————————————————

@dataclass
class _Seen:
    """一次调用里从它的事件里收到的。"""
    texts: list[str] = field(default_factory=list)
    usage: dict = field(default_factory=lambda: {"in": 0, "out": 0, "cache_read": 0, "cache_write": 0})
    failed: EngineError | None = None      # 它明说这一轮失败了
    last_error: str | None = None          # 中途报过的错（之后可能又好了）
    tools: dict = field(default_factory=dict)


def _usage(u: dict) -> dict:
    i, c, w = int(u.get("input_tokens") or 0), int(u.get("cached_input_tokens") or 0), int(u.get("cache_write_input_tokens") or 0)
    return {"in": max(0, i - c - w), "out": int(u.get("output_tokens") or 0), "cache_read": c, "cache_write": w}


def _codex_event(e: dict, seen: _Seen) -> list[dict]:
    """codex exec --json 的一行 → 我们的事件。"""
    t = e.get("type")
    it = e.get("item") if isinstance(e.get("item"), dict) else {}
    what = it.get("type") or it.get("item_type")
    if t == "item.started" and what == "mcp_tool_call":
        tool = str(it.get("tool") or "")
        return [{"type": "tool_run", "id": str(it.get("id") or ""), "name": tool, "detail": tool}]
    if t == "item.completed" and what == "mcp_tool_call":
        res = it.get("result") if isinstance(it.get("result"), dict) else {}
        bad = it.get("status") == "failed" or bool(it.get("error")) or res.get("is_error") is True or res.get("isError") is True
        return [{"type": "tool_end", "id": str(it.get("id") or ""), "name": str(it.get("tool") or ""), "status": "error" if bad else "ok"}]
    if t == "item.completed" and what == "agent_message" and str(it.get("text") or "").strip():
        piece = str(it["text"]).strip()
        seen.texts.append(piece)
        return [{"type": "text", "text": ("\n\n" if len(seen.texts) > 1 else "") + piece}]
    if t == "item.completed" and what == "reasoning" and str(it.get("text") or "").strip():
        return [{"type": "thinking", "text": str(it["text"]).strip()}]
    if t == "turn.completed":
        seen.usage = _usage(e.get("usage") if isinstance(e.get("usage"), dict) else {})
    elif t == "turn.failed":
        why = e.get("error")
        seen.failed = failure("codex", str((why.get("message") if isinstance(why, dict) else why) or "the turn failed"))
    elif t == "error" and not str(e.get("message") or "").startswith("Reconnecting"):
        seen.last_error = str(e.get("message") or "error")
    return []


def _codex_cmd(exe: str, work: Path, model_id: str, tok: str, files: list[Path]) -> tuple[list[str], dict[str, str], list[Path]]:
    from . import runners
    base = runners.codex_cmd(exe, work, runners.mcp_url(), None if model_id == DEFAULTS["codex"] else model_id)
    cmd = [*base[:-1], *EXTRA_ARGS, *(x for f in EXTRA_OFF for x in ("-c", f"features.{f}=false")), *(x for f in files for x in ("-i", str(f))),
           base[-1]]
    return cmd, {**child_env("codex"), "MOUSSE_TASK_MCP_TOKEN": tok}, []


def _tidy(paths: list[Path]) -> None:
    """跑完删掉：文件 unlink，目录整个删（符号链接只删链接本身，不跟进去）。"""
    for p in paths:
        with contextlib.suppress(OSError):
            if p.is_dir() and not p.is_symlink():
                shutil.rmtree(p)
            elif p.exists() or p.is_symlink():
                p.unlink()


def _guard(ref: str):
    """这一轮能花它吗（见文件开头）→ 这一轮的 Spec；不能抛 EngineError(rule)。"""
    pick = fallback.current()
    spec = pick.spec if pick is not None else None
    if spec is None or not models.may_spend(ref, kind=spec.kind, origin=spec.origin, via=getattr(spec, "via", None)):
        raise EngineError(f"{ref} is the user's own {NAMES.get(ref.split('/', 1)[0], 'login')} on this computer, which only turns for them "
                          "can use", kind="rule")
    return spec


async def stream(p: dict, model_id: str, req) -> AsyncIterator[dict]:
    """providers.stream 交过来的（api = cli）。事件同各家：text / thinking / tool_run / tool_end / phase …，最后 done。"""
    from . import loop, mcp_engine, runners
    from . import tools as tools_mod
    name = p["name"]
    who = NAMES.get(name, name)
    if not models.NAME.match(model_id):  # 交给 --model / -m 的：不许带空格、/、以 - 开头（会被当成选项）
        raise EngineError(f"{name}/{model_id} isn't a model name {who} takes", kind="config")
    spec = _guard(f"{name}/{model_id}")
    if not enabled(name):
        raise EngineError(f"engine.{name} is off in server.json", kind="config")
    exe = bin_of(name)
    if not exe:
        raise EngineError(f"{who} isn't installed on this computer", kind="config", status=401)
    how = await login_now(name)
    if how is None:
        raise EngineError(f"{who} isn't signed in on this computer (run {name} login in a terminal)", kind="config", status=401)
    if name == "codex" and how != "chatgpt":
        raise EngineError("Codex is signed in with an API key on this computer; codex/… only runs on a ChatGPT sign-in "
                          "(add the key as an OpenAI provider instead)", kind="config", status=401)
    parse = _codex_event
    sem = _limit(name)
    if sem.locked():
        yield {"type": "phase", "phase": "queued"}
    async with sem:
        q: asyncio.Queue = asyncio.Queue()
        control = loop.Control()
        tools = [t for t in spec.tools if t.name not in NOT_IN_CLI]
        tok = mcp_engine.grant(agent=spec.agent, tools=tools, gate=spec.gate or tools_mod.gate, conversation=spec.conversation or "-",
                               turn=await asyncio.to_thread(_turn_of, spec.conversation), control=control, emit=lambda ev: q.put_nowait(("event", ev)),
                               origin=spec.origin)
        proc = None
        files: list[Path] = []
        leftovers: list[Path] = []
        seen = _Seen()
        done: dict | None = None
        err = bytearray()
        try:
            work = await asyncio.to_thread(workdir, name, spec.agent)
            prompt, pictures = transcript(req.system, req.messages, name)
            for k, b in enumerate(pictures[:8]):
                f = work / f"image-{uuid.uuid4().hex[:8]}-{k}{IMAGE_EXT.get(str(b.get('mime')), '.png')}"
                with contextlib.suppress(Exception):
                    f.write_bytes(base64.b64decode(str(b["data"])))
                    files.append(f)
            cmd, env, leftovers = _codex_cmd(exe, work, model_id, tok, files)
            proc = await asyncio.create_subprocess_exec(*cmd, cwd=str(work), env=env, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                                                        stderr=asyncio.subprocess.PIPE, start_new_session=True, limit=runners.LINE_LIMIT)

            async def feed() -> None:
                try:
                    assert proc.stdin is not None
                    proc.stdin.write(prompt.encode("utf8"))
                    await proc.stdin.drain()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    if proc.stdin is not None:
                        proc.stdin.close()

            async def read() -> None:
                assert proc.stdout is not None
                async for line in proc.stdout:
                    q.put_nowait(("line", line))
                q.put_nowait(("eof", None))

            async def drain_err() -> None:
                assert proc.stderr is not None
                while chunk := await proc.stderr.read(65536):
                    err.extend(chunk)
                    if len(err) > ERR_KEEP:
                        del err[:-ERR_KEEP]
            side = [asyncio.ensure_future(feed()), asyncio.ensure_future(read()), asyncio.ensure_future(drain_err())]
            idle = max(30.0, float(req.timeout or 600))
            try:
                while True:
                    try:
                        kind, data = await asyncio.wait_for(q.get(), idle)
                    except TimeoutError:
                        raise EngineError(f"{who} went quiet for {idle:.0f}s", kind="timeout", retryable=False) from None
                    if kind == "eof":
                        break
                    if kind == "event":
                        yield data
                        continue
                    try:
                        e = json.loads(data)
                    except ValueError:
                        continue
                    if isinstance(e, dict):
                        for ev in parse(e, seen):
                            yield ev
            finally:
                for s in side:
                    s.cancel()
            rc = await proc.wait()
            if seen.failed or (not seen.texts and (seen.last_error or rc)):
                tail = [x for x in err.decode("utf8", "replace").splitlines() if x.startswith(("Error:", "error:")) or " ERROR " in x]
                raise seen.failed or failure(name, seen.last_error or (tail[-1][-300:] if tail else f"exit code {rc}"))
            if not seen.texts:
                raise EngineError(f"{who} ended without an answer", kind="output", retryable=True)
            done = {"role": "assistant", "content": [{"type": "text", "text": "\n\n".join(seen.texts)}], "api": CLI, "provider": name,
                    "model": model_id, "stop": "end", "usage": seen.usage}
        finally:  # 先收拾（令牌马上收回），再交出回复：loop 拿到 done 就不再往下读这个生成器了
            control.abort()  # 还在等的 MCP 调用一律不做
            mcp_engine.revoke(tok)
            if proc is not None and proc.returncode is None:
                runners.kill(proc)
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(proc.wait(), runners.KILL_GRACE + 2)
                if proc.returncode is None:
                    runners.kill(proc, hard=True)
            await asyncio.to_thread(_tidy, [*files, *leftovers])
    yield {"type": "done", "message": done}
