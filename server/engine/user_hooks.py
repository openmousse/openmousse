"""用户钩子（第 5 批第 4 项，2026-10-19；对照表 11.4–11.6）：系统里发生的事件跑主人写的脚本。只做「跑一个脚本」这一种
（不在服务器进程里跑用户的 Python：那是带代码的插件，engine/plugins.py，它注册的钩子也走这里的事件）。

配置：server.json 的 engine.hooks（不写 = 没有钩子）：
  "engine": {"hooks": {
    "tool:before":   [{"command": ["python3", "/home/me/hooks/guard.py"], "matcher": "host_exec|demo__*", "timeout": 30, "fail_closed": true}],
    "session:start": [{"command": ["/home/me/hooks/log.sh"]}],
    "workspace": false,                                    Agent 工作区里的 hooks/<名字>/HOOK.md 认不认（默认不认）
    "bundled": {"boot-md": false, "command-logger": false}}}  自带的两个（默认关）
  command      argv 列表，不经 shell（写成一个字符串的按 shell 的规矩切开，照样不经 shell）
  matcher      只看 tool:before / tool:after：工具名，可以带通配（* ?），几个用 | 隔开；不写 = 全部
  timeout      秒，默认 30，最多 300
  fail_closed  能拦的事件（tool:before、message:received）上：脚本超时、出错、回的看不懂 = 拦下；默认 false（照常做）
  cwd          在哪个目录跑（默认 HOME）
工作区的钩子（engine.hooks.workspace = true 才认）：<工作区>/hooks/<名字>/HOOK.md，照 OpenClaw 的格式：frontmatter 的 events（也认
  metadata.openclaw.events，OpenClaw 的几个名字照下面 ALIASES 换）、requires / os（engine/skill_gate.py 那一套）；处理程序只认脚本：
  frontmatter 的 command（相对路径按这个文件夹算），不写就找 handler.py（python3 跑）/ handler.sh（sh 跑）/ handler（要有执行位）。
  只管那个 Agent 自己的事件。Agent 自己写得进自己的工作区：所以和 server.json 里的一样，第一次跑之前要主人同意。
事件（每个 payload 都有 event、hook_event_name（同 Claude Code / Hermes 的名字）、agent、conversation、at）：
  startup           服务起来了（main.py 的 lifespan）
  session:start     一个对话开了一条新会话（engine/owner.py：新的一天、闲久了换新、/new、/reset、/resume；payload 的 reason 写是哪种）；
                    同时换下来的那条算 session:end
  session:end
  message:received  主人在 app 里说了一句（chat.py 的 engine_stream）：能拦。payload 的 from 写是谁说的（owner = 主人自己；以后渠道进来的
                    别人的话由第 6 批发，from 写对方、trusted = false）
  reply:sent        一轮回完、回复存好了（chat.py）
  tool:before       工具调用之前（tools.for_agent 给每个工具包一层）：能拦、能改参数
  tool:after        工具调用之后（只看：tool_response、ok）
  compact:before / compact:after  压缩之前、之后（engine/compact.py）
  能拦的那两个当场等脚本跑完（一个个跑）；别的在后台跑，不拖累这一轮。
协议（和 Claude Code、Hermes 的 shell 钩子一样）：stdin 收一段 JSON（payload），stdout 回 JSON：
  {"decision": "block", "reason": "…"}        拦下（也认 Hermes 的 {"action": "block", "message"}、Claude Code 的
                                              hookSpecificOutput.permissionDecision = "deny"）
  {"decision": "modify", "tool_input": {…}}   改参数，只 tool:before（也认 {"action": "modify", "args"}、hookSpecificOutput.updatedInput）
  退出码 2 = 拦下（原因取 stdout 里写的，没写取 stderr）；别的不是 0 的 = 出错（照常做，fail_closed 的拦下）；什么都不回 = 照常。
  拦下了：这次调用不做，回给模型一句为什么（ToolFailure）；这句话不进模型（message:received）就回一句「被钩子拦下了」。
  改参数：只能改成同一类调用（关卡的分类不能变严，命令行工具的子命令不能换），改完照这个工具的 schema 查、再过一遍关卡（tools.gate：
  冒充别人的、计划模式里不许的照样挡，要点头的照样出卡）。
  脚本回的别的字一概不进模型（Hermes 的 {"context"} 不做）：别人的话进来的事件，脚本只能拦、能记。
同意（照 Hermes 的 shell-hooks-allowlist，比它多一样：脚本文件的内容也算进去）：每条钩子第一次跑之前要主人同意：指纹 = 事件 + argv + 工作目录
  + 它跑的那些文件（1 MB 以内的按内容，大的按大小和修改时间）：它跑的脚本（解释器后面第一个是文件的词，跳过 -u、-X dev 这类选项；开头的
  env 跳过；裸名字按真跑时的工作目录找）、argv 里别的像代码的文件（.py、.sh、.js……或者有执行位；日志、配置这类数据文件不算，钩子每次往里写）；
  工作区的钩子整个文件夹的文件都算（Agent 写得到那里：在 handler.py 旁边放个 json.py 也要重新同意；最多 FOLDER_MAX 个，多了不跑）。
  进白名单 <data_dir>/hooks/allowlist.json。没同意的不跑（fail_closed 的
  也不拦：它根本没开始管事），交一张收件箱卡（kind tool，前缀 hooks；点了 = 进白名单），状态里写「等你同意」；命令行 mousse hooks approve。
  命令、脚本改了 = 指纹变了 = 要重新同意。
环境：只给最小的那几样（mcp_client.SAFE_ENV：PATH、HOME、LANG……和 XDG_*）加上 MOUSSE_HOOK_EVENT、PYTHONDONTWRITEBYTECODE；不继承服务器的环境（里面有各家的密钥）。
  自己一个进程组，到点整组收掉（先 SIGTERM，2 秒后 SIGKILL）。stdout 最多读 64 KB、stderr 8 KB。
自带的两个（engine.hooks.bundled 里写 true 才开，默认关）：
  boot-md         服务起来时，每个 Agent 工作区里非空的 BOOT.md 当一轮主人回合跑一次（engine/unattended.fresh：新会话，不往对话里发；
                  模型照心跳的挑法，不花套餐）。照 OpenClaw 的 boot-md
  command-logger  会话开始 / 结束、斜杠命令（以 / 开头的那句话）各记一行 JSONL：<data_dir>/logs/commands.jsonl
                  （{"timestamp", "action", "agent", "conversation", "source"}，照 OpenClaw 的 command-logger）
状态：GET /api/engine/hooks（有哪些、同意了没有、最近几次跑的结果）。
协议的读法（stdout 的几种写法、退出码 2、matcher、超时上限）照 Hermes 的 agent/shell_hooks.py（_parse_response、_evaluate_result），
NousResearch/hermes-agent commit cc6b1f299bd3a7c0309f003d97dc32014e71fa80，MIT，Copyright (c) 2025 Nous Research；许可全文见仓库根目录的
THIRD_PARTY_NOTICES。事件和 HOOK.md、boot-md、command-logger 照 OpenClaw 1099ccc1 的 docs/automation/hooks*.md、src/hooks/（只借做法）。
"""
from __future__ import annotations

import asyncio
import collections
import contextlib
import dataclasses
import fnmatch
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import sys
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter

from config import raw, settings
from i18n import L

from . import calls
from .loop import Control, ToolCtx, ToolFailure

EVENTS = ("startup", "session:start", "session:end", "message:received", "reply:sent", "tool:before", "tool:after", "compact:before",
          "compact:after")
BLOCKING = {"tool:before", "message:received"}
TOOL_EVENTS = {"tool:before", "tool:after"}
ALIASES = {"gateway:startup": "startup", "command:new": "session:start", "message:sent": "reply:sent", "session:compact:before": "compact:before",
           "session:compact:after": "compact:after", "before_tool_call": "tool:before", "after_tool_call": "tool:after",
           "pre_tool_call": "tool:before", "post_tool_call": "tool:after", "on_session_start": "session:start", "on_session_end": "session:end",
           "PreToolUse": "tool:before", "PostToolUse": "tool:after", "UserPromptSubmit": "message:received", "SessionStart": "session:start",
           "SessionEnd": "session:end", "PreCompact": "compact:before"}
INTERPRETER = re.compile(r"^(?:python\d*(?:\.\d+)?|pypy\d*|sh|bash|zsh|dash|ksh|fish|node|nodejs|deno|bun|ruby|perl|php|lua|osascript|pwsh)$")
TIMEOUT, MAX_TIMEOUT = 30.0, 300.0
OUT_MAX, ERR_MAX = 64 * 1024, 8 * 1024
HASH_MAX = 1024 * 1024
KILL_GRACE = 2.0
PREFIX = "hooks"
CODE_EXT = frozenset({".py", ".pyc", ".pyw", ".sh", ".bash", ".zsh", ".fish", ".ksh", ".js", ".mjs", ".cjs", ".ts", ".mts", ".cts", ".jsx", ".tsx",
                      ".rb", ".pl", ".pm", ".php", ".lua", ".scpt", ".applescript", ".ps1", ".psm1"})  # argv 里这几种文件算进指纹
FOLDER_MAX = 1000  # 工作区的钩子：文件夹里最多这么多个文件（都算进指纹）；多了这条钩子不跑
_hashes: dict[tuple, str] = {}             # (路径, mtime, 大小) → 内容的指纹
_last: dict[str, collections.deque] = {}   # 指纹 → 最近几次跑的结果（状态页看）
_asked: set[str] = set()                   # 这次服务起来以后已经交过卡的指纹（卡还在等就不再交）
_bg: set[asyncio.Task] = set()
PLUGINS: list[Hook] = []                   # 带代码的插件注册的钩子（engine/plugins.py；在进程里跑，不经白名单：插件本身要主人点头才装）
router = APIRouter()


@dataclass
class Hook:
    event: str
    argv: list[str] = field(default_factory=list)
    matcher: str = ""
    timeout: float = TIMEOUT
    fail_closed: bool = False
    cwd: str = ""
    agent: str | None = None      # 工作区的钩子只管这个 Agent
    origin: str = "server.json"   # server.json / workspace:<Agent>/<名字> / plugin:<id>
    name: str = ""
    error: str = ""               # 配置不对：不跑，状态里写明
    fn: Callable[[dict], Awaitable[Any]] | None = None   # 插件的钩子（在进程里跑的 async 函数）

    def _argv(self) -> list[str]:
        """argv 去掉开头的 env（和它的选项、X=1）：env python3 x.py 跑的是 x.py。"""
        argv = list(self.argv)
        while argv and os.path.basename(argv[0]) == "env":
            i = 1
            while i < len(argv) and (argv[i].startswith("-") or ("=" in argv[i] and "/" not in argv[i].split("=", 1)[0])):
                i += 2 if argv[i] in ("-u", "--unset") else 1
            argv = argv[i:]
        return argv

    def _path(self, a: str, *, bare: bool) -> Path | None:
        """argv 里的一个词 → 它指到的文件；相对路径按真跑时的工作目录（run_one：cwd 在就用它，不然家目录）。不是文件 → None。
        bare=False（第一个词）：不带「/」的是 PATH 上的程序，只认装在家目录下面的（自己写的，~/.local/bin 之类）。"""
        if not a or "\0" in a:
            return None
        if "/" not in a and not bare:
            found = shutil.which(a)
            home = Path.home().resolve()
            return Path(found) if found and Path(found).resolve().is_relative_to(home) else None
        p = Path(a) if os.path.isabs(a) else (Path(self.cwd) if self.cwd and os.path.isdir(self.cwd) else Path.home()) / a
        try:
            return p if p.is_file() else None
        except (OSError, ValueError):
            return None

    def script(self) -> Path | None:
        """它跑的脚本（卡上给主人看的那份）：第一个词是解释器（python、sh、node……）的话是后面第一个是文件的词（跳过 -u、-X dev 这类
        选项），不然是第一个词；开头的 env 跳过。不是文件 → None。"""
        argv = self._argv()
        if not argv:
            return None
        if not INTERPRETER.match(os.path.basename(argv[0]).lower()):
            return self._path(argv[0], bare=False)
        for a in argv[1:]:
            if not a.startswith("-") and (p := self._path(a, bare=True)) is not None:
                return p
        return None

    def files(self) -> list[Path] | None:
        """算进指纹的文件：它跑的脚本，argv 里别的像代码的文件（CODE_EXT 或者有执行位），工作区的钩子再加整个文件夹。
        文件夹里超过 FOLDER_MAX 个 → None（这条钩子不跑：没法都算进去）。"""
        out = [s] if (s := self.script()) is not None else []
        for a in self._argv()[1:]:
            p = self._path(a.split("=", 1)[1] if a.startswith("-") and "=" in a else a, bare=True)
            if p is not None and p not in out and (p.suffix.lower() in CODE_EXT or os.access(p, os.X_OK)):
                out.append(p)
        if self.origin.startswith("workspace:") and self.cwd and os.path.isdir(self.cwd):  # Agent 写得到的文件夹：每个文件都算
            for base, dirs, names in os.walk(self.cwd):
                dirs.sort()
                for n in sorted(names):
                    if (p := Path(base) / n) not in out:
                        out.append(p)
                    if len(out) > FOLDER_MAX:
                        return None
        return out

    def fingerprint(self) -> str:
        """指纹 = 事件 + argv + 工作目录 + 它跑的那些文件（files）的内容（1 MB 以内；大的按大小和修改时间）。命令、脚本改了就变。"""
        if self.fn is not None:
            return f"plugin:{self.origin}:{self.event}:{self.name}"
        files = self.files()
        sigs = None if files is None else [[str(p), _file_sig(p)] for p in files]
        return hashlib.sha256(json.dumps([self.event, self.argv, self.cwd, sigs]).encode()).hexdigest()

    def public(self) -> dict:
        return {"event": self.event, "command": self.argv, "matcher": self.matcher or None, "timeout": self.timeout, "fail_closed": self.fail_closed,
                "cwd": self.cwd or None, "agent": self.agent, "origin": self.origin, "name": self.name or None, "error": self.error or None}


def _file_sig(p: Path) -> str:
    try:
        st = p.stat()
    except OSError:
        return "missing"
    key = (str(p), st.st_mtime_ns, st.st_size)
    if key in _hashes:
        return _hashes[key]
    if st.st_size > HASH_MAX:
        sig = f"size:{st.st_size}:mtime:{st.st_mtime_ns}"
    else:
        try:
            sig = "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()
        except OSError:
            sig = "unreadable"
    if len(_hashes) > 500:
        _hashes.clear()
    _hashes[key] = sig
    return sig


# —— 配置 ——————————————————————————————————————————————————————————————————————

def cfg() -> dict:
    e = raw().get("engine")
    h = e.get("hooks") if isinstance(e, dict) else None
    return h if isinstance(h, dict) else {}


def bundled(name: str) -> bool:
    b = cfg().get("bundled")
    return isinstance(b, dict) and b.get(name) is True


def _argv(cmd: Any, base: Path | None = None) -> list[str]:
    if isinstance(cmd, str):
        try:
            argv = shlex.split(cmd)
        except ValueError:
            return []
    elif isinstance(cmd, list) and all(isinstance(x, (str, int, float)) for x in cmd):
        argv = [str(x) for x in cmd]
    else:
        return []
    if base is not None:  # 工作区的钩子：相对路径按它的文件夹算
        argv = [str(base / a) if a.startswith(("./", "../")) or (i > 0 and (base / a).is_file()) else a for i, a in enumerate(argv)]
        if argv and (base / argv[0]).is_file():
            argv[0] = str(base / argv[0])
    return [str(Path(a).expanduser()) if a.startswith("~") else a for a in argv]


def _hook_of(event: str, entry: Any, *, origin: str = "server.json", agent: str | None = None, name: str = "", base: Path | None = None) -> Hook:
    h = Hook(event=event, origin=origin, agent=agent, name=name)
    if not isinstance(entry, dict):
        h.error = L("一条钩子要写成 {\"command\": [...]}", "A hook must be an object: {\"command\": [...]}")
        return h
    h.argv = _argv(entry.get("command"), base)
    if not h.argv:
        h.error = L("command 要写成 argv 列表（不经 shell）", "command must be an argv list (no shell)")
    m = entry.get("matcher")
    h.matcher = str(m).strip() if isinstance(m, str) else ""
    t = entry.get("timeout")
    h.timeout = TIMEOUT if isinstance(t, bool) or not isinstance(t, (int, float)) or t < 1 else min(float(t), MAX_TIMEOUT)
    h.fail_closed = entry.get("fail_closed", entry.get("failClosed")) is True
    cwd = entry.get("cwd")
    h.cwd = str(Path(str(cwd)).expanduser()) if isinstance(cwd, str) and cwd.strip() else (str(base) if base else "")
    return h


def config_hooks() -> list[Hook]:
    out = []
    for key, entries in cfg().items():
        ev = ALIASES.get(key, key)
        if ev not in EVENTS:
            continue
        for e in entries if isinstance(entries, list) else [entries]:
            out.append(_hook_of(ev, e))
    return out


def _handler(d: Path, fm: dict) -> list[str]:
    if fm.get("command"):
        return _argv(fm["command"], d)
    if (d / "handler.py").is_file():
        return [shutil.which("python3") or sys.executable, str(d / "handler.py")]
    if (d / "handler.sh").is_file():
        return ["/bin/sh", str(d / "handler.sh")]
    if (d / "handler").is_file() and os.access(d / "handler", os.X_OK):
        return [str(d / "handler")]
    return []


def workspace_hooks(agent: str) -> list[Hook]:
    """<工作区>/hooks/<名字>/HOOK.md（engine.hooks.workspace = true 才认）。"""
    if cfg().get("workspace") is not True:
        return []
    from . import skill_gate
    from .context import workspace_of
    from .tools import _front
    ws = workspace_of(agent)
    root = ws / "hooks" if ws is not None else None
    if root is None or not root.is_dir() or root.is_symlink():
        return []
    out = []
    for d in sorted(p for p in root.iterdir() if p.is_dir() and not p.is_symlink() and (p / "HOOK.md").is_file()):
        try:
            fm = _front((d / "HOOK.md").read_text(encoding="utf8", errors="replace")[:20000])
        except OSError:
            continue
        meta = skill_gate._obj(fm.get("metadata"))
        oc = skill_gate._obj(meta.get("openclaw"))
        events = skill_gate._strs(fm.get("events")) or skill_gate._strs(oc.get("events"))
        origin = f"workspace:{agent}/{d.name}"
        argv = _handler(d, fm)
        st = skill_gate.check(f"hook:{d.name}", fm)
        for ev in events:
            ev = ALIASES.get(ev, ev)
            h = Hook(event=ev, argv=argv, cwd=str(d), agent=agent, origin=origin, name=str(fm.get("name") or d.name),
                     matcher=str(fm.get("matcher") or ""), fail_closed=fm.get("fail_closed") is True)
            t = fm.get("timeout")
            h.timeout = TIMEOUT if isinstance(t, bool) or not isinstance(t, (int, float)) or t < 1 else min(float(t), MAX_TIMEOUT)
            if ev not in EVENTS:
                h.error = L(f"认不出这个事件：{ev}", f"Unknown event: {ev}")
            elif not argv:
                h.error = L("处理程序只认脚本：写 command，或者放 handler.py / handler.sh / handler（要有执行位）",
                            "Handlers must be scripts: set command, or add handler.py / handler.sh / an executable handler")
            elif not st.ok:
                h.error = L("门槛没过：", "Requirements not met: ") + "; ".join(st.labels())
            elif h.files() is None:
                h.error = L(f"钩子的文件夹里文件太多（最多 {FOLDER_MAX} 个，都要算进指纹）", f"Too many files in the hook folder (at most {FOLDER_MAX}, "
                            "all of them go into its fingerprint)")
            out.append(h)
    return out


def hooks(event: str | None = None, agent: str | None = None) -> list[Hook]:
    """这个事件（不给 = 全部）、这个 Agent（工作区的钩子只管它自己）的钩子：server.json 的、工作区的、插件的。"""
    out = config_hooks() + PLUGINS
    agents = [agent] if agent else _agents()
    for a in agents:
        out += workspace_hooks(a)
    return [h for h in out if event is None or h.event == event]


def _agents() -> list[str]:
    return ["main", *sorted(settings.agent_workspaces)]


def matches(h: Hook, tool: str | None) -> bool:
    if h.event not in TOOL_EVENTS or not h.matcher:
        return True
    return bool(tool) and any(fnmatch.fnmatchcase(tool, m.strip()) for m in h.matcher.split("|") if m.strip())


# —— 白名单（主人同意过的指纹）——————————————————————————————————————————————————————

def _allow_path() -> Path:
    return settings.data_dir / "hooks" / "allowlist.json"


def allowlist() -> dict[str, dict]:
    try:
        v = json.loads(_allow_path().read_text(encoding="utf8"))
    except (OSError, ValueError):
        return {}
    a = v.get("approvals") if isinstance(v, dict) else None
    return a if isinstance(a, dict) else {}


def _save(approvals: dict) -> None:
    p = _allow_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".allowlist.{os.getpid()}.{uuid.uuid4().hex[:6]}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf8") as f:
        json.dump({"approvals": approvals}, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def approved(h: Hook) -> bool:
    return h.fn is not None or h.fingerprint() in allowlist()


def approve(h: Hook, by: str = "owner") -> str:
    fp = h.fingerprint()
    a = allowlist()
    a[fp] = {"event": h.event, "command": h.argv, "origin": h.origin, "at": datetime.now(settings.tz).isoformat(timespec="seconds"), "by": by}
    _save(a)
    return fp


def revoke(fp: str) -> bool:
    a = allowlist()
    if fp not in a:
        return False
    a.pop(fp)
    _save(a)
    return True


def _card(h: Hook) -> dict:
    script = ""
    p = h.script()
    if p is not None:
        with contextlib.suppress(OSError, UnicodeDecodeError):
            body = p.read_text(encoding="utf8")
            script = f"\n\n{p.name}\n```\n" + (body if len(body) <= 20_000 else body[:20_000] + "\n…").replace("```", "ʼʼʼ") + "\n```"
    where = L("server.json 的 engine.hooks", "engine.hooks in server.json") if h.origin == "server.json" else \
        L(f"{h.agent} 工作区里的 hooks/{h.origin.rsplit('/', 1)[-1]}/HOOK.md", f"hooks/{h.origin.rsplit('/', 1)[-1]}/HOOK.md in {h.agent}'s workspace")
    return {"title": L(f"让这条钩子跑：{h.event}", f"Allow this hook to run: {h.event}"),
            "why": L(f"{where}里有一条钩子：每次 {h.event} 时在服务器上跑下面这条命令（不经 shell、只给最小的环境）。第一次跑之前要你同意；"
                     "命令或它跑的脚本改了要重新同意。不同意就不跑。",
                     f"{where} has a hook that runs the command below on the server at every {h.event} (no shell, minimal environment). "
                     "It needs your OK before its first run, and again whenever the command or its script changes. Without it, it never runs."),
            "changes": [L(f"事件：{h.event}", f"Event: {h.event}"), L(f"命令：{shlex.join(h.argv)[:300]}", f"Command: {shlex.join(h.argv)[:300]}")]
            + ([L(f"只管这些工具：{h.matcher}", f"Only these tools: {h.matcher}")] if h.matcher else [])
            + [L(f"时限：{h.timeout:.0f} 秒", f"Time limit: {h.timeout:.0f}s")]
            + ([L("出错、超时也拦下（fail_closed）", "Blocks when it fails or times out (fail_closed)")] if h.fail_closed else []),
            "detail": L("同意后，这条命令的指纹（命令 + 脚本内容）记进白名单，以后每次这个事件都跑它。", "If you approve, this command's fingerprint "
                        "(command + script contents) goes on the allowlist and it runs at every such event.") + f"\n\nfingerprint: {h.fingerprint()}"
            + script,
            "approveLabel": L("允许它跑", "Allow it")}


async def ask(h: Hook) -> None:
    """没同意的钩子：交一张卡（kind tool，前缀 hooks；同一条还在等就不再交）。出错只记一笔。"""
    fp = h.fingerprint()
    if fp in _asked:
        return
    _asked.add(fp)
    who = h.agent or "main"
    from .tools import thread_for
    ctx = ToolCtx(turn="", kind="owner", agent=who, conversation=thread_for(who, who), call_id=f"hook-{fp[:12]}", control=Control())
    try:
        await calls.ask(PREFIX, h.event, {"fingerprint": fp, "event": h.event, "origin": h.origin, "command": h.argv}, ctx, _card(h))
    except Exception as e:  # noqa: BLE001
        _asked.discard(fp)
        print(f"[engine] 钩子的同意卡没交上：{type(e).__name__}: {str(e)[:120]}")


async def _approved(name: str, args: dict, row) -> str:
    """卡点了同意（engine/calls.py）：现在的这条钩子指纹还对得上才进白名单（卡交上来以后脚本被改了就不认）。"""
    fp = str(args.get("fingerprint") or "")
    for h in hooks(str(args.get("event") or name)):
        if h.fingerprint() == fp:
            approve(h, by="owner (card)")
            return L(f"以后 {h.event} 时会跑这条钩子：{shlex.join(h.argv)[:200]}", f"This hook will run at every {h.event}: {shlex.join(h.argv)[:200]}")
    raise ToolFailure(L("这条钩子已经不在了，或者命令、脚本改过了（要重新同意）", "That hook is gone, or its command or script changed (it needs a new OK)"))


calls.register(PREFIX, _approved)


# —— 跑 ————————————————————————————————————————————————————————————————————————

@dataclass
class Outcome:
    blocked: bool = False
    reason: str = ""
    args: dict | None = None      # tool:before 改过的参数
    ran: int = 0


def env_for(event: str) -> dict[str, str]:
    from .mcp_client import SAFE_ENV
    env = {k: v for k, v in os.environ.items() if k in SAFE_ENV or k.startswith("XDG_")}
    env["MOUSSE_HOOK_EVENT"] = event
    env["PYTHONDONTWRITEBYTECODE"] = "1"  # 别往钩子的文件夹里写 __pycache__（工作区的钩子整个文件夹算指纹：写了就要重新同意）
    return env


def _killpg(pid: int, sig: int) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
        os.killpg(pid, sig)


async def _read(stream, limit: int) -> bytes:
    buf = bytearray()
    while True:
        chunk = await stream.read(65536)
        if not chunk:
            return bytes(buf)
        if len(buf) < limit:
            buf += chunk[: limit - len(buf)]


async def run_one(h: Hook, payload: dict) -> dict:
    """跑一条钩子 → {"code", "out", "err", "timed_out", "error", "ms"}。"""
    t0 = time.monotonic()
    if h.fn is not None:  # 插件的钩子：在进程里跑，照样有时限
        try:
            async with asyncio.timeout(h.timeout):  # 不用 wait_for：理由同 plugins.call_tool（3.11 上 sys.exit 会带走服务器）
                res = await h.fn(dict(payload))
        except asyncio.TimeoutError:
            return {"code": None, "out": "", "err": "", "timed_out": True, "error": "", "ms": int((time.monotonic() - t0) * 1000)}
        except asyncio.CancelledError:
            raise
        except BaseException as e:  # noqa: BLE001 — 插件自己的错（含 sys.exit 的 SystemExit：不许带走服务器）：只停这一条
            return {"code": 1, "out": "", "err": f"{type(e).__name__}: {str(e)[:300]}", "timed_out": False, "error": "", "ms": 0}
        out = json.dumps(res) if isinstance(res, dict) else ""
        return {"code": 0, "out": out, "err": "", "timed_out": False, "error": "", "ms": int((time.monotonic() - t0) * 1000)}
    cwd = h.cwd if h.cwd and os.path.isdir(h.cwd) else str(Path.home())
    try:
        proc = await asyncio.create_subprocess_exec(*h.argv, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.PIPE, cwd=cwd, env=env_for(h.event), start_new_session=True)
    except OSError as e:
        return {"code": None, "out": "", "err": "", "timed_out": False, "error": f"{type(e).__name__}: {e.strerror or e}", "ms": 0}
    data = (json.dumps(payload, ensure_ascii=False, default=str) + "\n").encode("utf8")

    async def talk() -> tuple[bytes, bytes, int]:
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            proc.stdin.write(data)
            await proc.stdin.drain()
        with contextlib.suppress(Exception):
            proc.stdin.close()
        out, err = await asyncio.gather(_read(proc.stdout, OUT_MAX), _read(proc.stderr, ERR_MAX))
        return out, err, await proc.wait()
    try:
        out, err, code = await asyncio.wait_for(talk(), h.timeout)
    except asyncio.TimeoutError:
        _killpg(proc.pid, signal.SIGTERM)
        try:
            await asyncio.wait_for(proc.wait(), KILL_GRACE)
        except asyncio.TimeoutError:
            _killpg(proc.pid, signal.SIGKILL)
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(proc.wait(), 5)
        return {"code": None, "out": "", "err": "", "timed_out": True, "error": "", "ms": int((time.monotonic() - t0) * 1000)}
    except asyncio.CancelledError:
        _killpg(proc.pid, signal.SIGKILL)
        raise
    return {"code": code, "out": out.decode("utf8", "replace"), "err": err.decode("utf8", "replace"), "timed_out": False, "error": "",
            "ms": int((time.monotonic() - t0) * 1000)}


def parse(event: str, res: dict, fail_closed: bool) -> tuple[str, str, dict | None]:
    """跑的结果 → (怎么办：ok / block / modify / error, 原因, 改过的参数)（照 Hermes 的 _evaluate_result、_parse_response）。"""
    if res["timed_out"] or res["error"]:
        why = L(f"超过 {res['ms'] // 1000} 秒没跑完", f"didn't finish in {res['ms'] // 1000}s") if res["timed_out"] else res["error"]
        return ("block" if fail_closed else "error"), why, None
    text = res["out"].strip()
    j: Any = None
    if text:
        try:
            j = json.loads(text)
        except ValueError:
            j = None
    directive = None
    if isinstance(j, dict):
        hso = j.get("hookSpecificOutput") if isinstance(j.get("hookSpecificOutput"), dict) else {}
        decision = str(j.get("decision") or j.get("action") or "").lower()
        reason = str(j.get("reason") or j.get("message") or hso.get("permissionDecisionReason") or "").strip()
        if decision == "block" or str(hso.get("permissionDecision") or "").lower() == "deny":
            directive = ("block", reason, None)
        elif event == "tool:before":
            new = j.get("tool_input") if decision == "modify" else None
            new = new if isinstance(new, dict) else j.get("args") if decision == "modify" and isinstance(j.get("args"), dict) else new
            if new is None and isinstance(hso.get("updatedInput"), dict):
                new = hso["updatedInput"]
            if isinstance(new, dict):
                directive = ("modify", reason, new)
    if res["code"] == 2:
        reason = (directive[1] if directive and directive[0] == "block" else "") or res["err"].strip()[:400]
        return "block", reason, None
    if directive is not None:
        return directive
    if res["code"] not in (0, None):
        return ("block" if fail_closed else "error"), L(f"退出码 {res['code']}", f"exit code {res['code']}") + (
            f": {res['err'].strip()[:200]}" if res["err"].strip() else ""), None
    if text and j is None and fail_closed:
        return "block", L("回的不是 JSON", "its output wasn't JSON"), None
    return "ok", "", None


def _note(h: Hook, outcome: str, res: dict | None, why: str = "") -> None:
    q = _last.setdefault(h.fingerprint(), collections.deque(maxlen=5))
    q.appendleft({"at": datetime.now(settings.tz).isoformat(timespec="seconds"), "outcome": outcome, "why": why[:300],
                  "code": (res or {}).get("code"), "ms": (res or {}).get("ms")})


def base_payload(event: str, agent: str | None, conversation: str = "", **extra) -> dict:
    return {"event": event, "hook_event_name": event, "agent": agent or "main", "conversation": conversation or "",
            "at": datetime.now(settings.tz).isoformat(timespec="seconds"), **extra}


async def fire(event: str, payload: dict, *, agent: str | None = None, tool: str | None = None) -> Outcome:
    """跑这个事件的钩子（见文件开头）。能拦的事件一个个跑、拦了就停；改参数的一个个接着改。不抛错。"""
    out = Outcome()
    try:
        todo = [h for h in hooks(event, agent) if (agent is None or h.agent is None or h.agent == agent) and matches(h, tool)]
    except Exception as e:  # noqa: BLE001
        print(f"[engine] 钩子没读出来：{type(e).__name__}: {str(e)[:120]}")
        return out
    _builtin(event, payload, agent)
    for h in todo:
        if h.error:
            _note(h, "error", None, h.error)
            continue
        if not approved(h):
            _note(h, "waiting", None, L("等你同意", "waiting for your OK"))
            _later(ask(h))
            continue
        body = dict(payload)
        if event == "tool:before" and out.args is not None:
            body["tool_input"] = out.args
        try:
            res = await run_one(h, body)
        except Exception as e:  # noqa: BLE001
            res = {"code": None, "out": "", "err": "", "timed_out": False, "error": f"{type(e).__name__}: {str(e)[:200]}", "ms": 0}
        out.ran += 1
        what, why, new = parse(event, res, h.fail_closed and event in BLOCKING)
        _note(h, what, res, why)
        if event not in BLOCKING:
            continue
        if what == "block":
            out.blocked, out.reason = True, why or L("钩子拦下了", "blocked by a hook")
            return out
        if what == "modify" and new is not None:
            out.args = new
    return out


def _later(coro) -> None:
    try:
        t = asyncio.ensure_future(coro)
    except RuntimeError:  # 没有事件循环（命令行里）：不跑
        coro.close()
        return
    _bg.add(t)
    t.add_done_callback(_bg.discard)


LOGGED = {"session:start", "session:end", "message:received"}  # command-logger 记的那几个事件


def fire_soon(event: str, payload: dict, *, agent: str | None = None) -> None:
    """只看的事件：在后台跑，不等。这个事件没有钩子（command-logger 也不记）就什么都不排，不多开一个后台任务。"""
    if not any_for(event, agent) and not (event in LOGGED and bundled("command-logger")):
        return
    _later(fire(event, payload, agent=agent))


def any_for(event: str, agent: str | None) -> bool:
    try:
        return any(agent is None or h.agent is None or h.agent == agent for h in hooks(event, agent))
    except Exception:  # noqa: BLE001
        return False


# —— tool:before / tool:after：给一轮的工具包一层（tools.for_agent 里那一行）————————————————————————————

def wrap(tools: list, agent: str) -> list:
    """这个 Agent 有 tool:before / tool:after 的钩子才包（没有就原样，一点开销都没有）。"""
    if not (any_for("tool:before", agent) or any_for("tool:after", agent)):
        return tools
    return [_wrapped(t) for t in tools]


def _wrapped(t):
    inner = t.run

    async def run(args: dict, ctx: ToolCtx):
        me = ctx.agent or "main"
        payload = base_payload("tool:before", me, ctx.conversation, turn=ctx.turn, call_id=ctx.call_id, tool_name=t.name, tool_input=args,
                               session_id=ctx.conversation, cwd=str(Path.home()))
        got = await fire("tool:before", payload, agent=me, tool=t.name)
        if got.blocked:
            raise ToolFailure(L(f"没做：钩子拦下了这次调用（{got.reason}）。别换个办法绕过去；要做就跟用户说一声。",
                                f"Not run: a hook blocked this call ({got.reason}). Don't work around it; tell the user if it matters."))
        if got.args is not None:
            new, verdict = await regate(wrapped, args, got.args, ctx)
            if isinstance(verdict, dict):
                return verdict
            if verdict is not None:
                raise ToolFailure(verdict)
            args = new
        try:
            res = await inner(args, ctx)
        except BaseException as e:
            if any_for("tool:after", me):
                fire_soon("tool:after", base_payload("tool:after", me, ctx.conversation, turn=ctx.turn, call_id=ctx.call_id, tool_name=t.name,
                                                     tool_input=args, ok=False, error=str(e)[:2000] or type(e).__name__), agent=me)
            raise
        if any_for("tool:after", me):
            text = res if isinstance(res, str) else getattr(res, "text", None) or json.dumps(res, ensure_ascii=False, default=str)
            fire_soon("tool:after", base_payload("tool:after", me, ctx.conversation, turn=ctx.turn, call_id=ctx.call_id, tool_name=t.name,
                                                 tool_input=args, ok=True, tool_response=str(text)[:8000]), agent=me)
        return res
    wrapped = dataclasses.replace(t, run=run)
    for k, v in vars(t).items():  # 不是字段的标记（memory_scope、_traced……）照抄
        if k not in {f.name for f in dataclasses.fields(t)}:
            object.__setattr__(wrapped, k, v)
    return wrapped


async def regate(tool, old: dict, new: dict, ctx: ToolCtx) -> tuple[dict | None, str | dict | None]:
    """钩子改过的参数：照 schema 查；分类不能变严、命令行工具的子命令不能换；再过一遍关卡（tools.gate）。→ (参数, None) 放行；
    (None, 一句话) 不做；(None, dict) 交给用户了（approval-pending）。"""
    import bridges

    from . import tools as tools_mod
    from .loop import check_args
    raw_new = tool.prepare(new) if tool.prepare is not None else new
    fixed, problem = check_args(raw_new, tool.schema)
    if problem:
        return None, L(f"没做：钩子把参数改得不对了（{problem}）", f"Not run: a hook changed the arguments into something invalid ({problem})")
    was, now = tool.level(old)[0], tool.level(fixed)[0]
    if tools_mod.STRICT.get(now, 3) > tools_mod.STRICT.get(was, 3):
        return None, L(f"没做：钩子改过的调用要更严的关卡（{was} → {now}），钩子不能替用户做这个决定",
                       f"Not run: the hook's change would need a stricter check ({was} → {now}); a hook can't decide that for the user")
    if tool.name in bridges.BRIDGES:
        a = [str(x) for x in old.get("args") or []]
        b = [str(x) for x in fixed.get("args") or []]
        if tools_mod.classify(tool.name, a)[1] != tools_mod.classify(tool.name, b)[1]:
            return None, L("没做：钩子换了子命令（只能改参数，不能换成另一件事）", "Not run: the hook changed the subcommand (it may change "
                           "arguments, not turn the call into something else)")
    verdict = await tools_mod.gate(tool, fixed, ctx)
    if verdict is None:
        return fixed, None
    return None, verdict


# —— 别的事件挂在哪（各一行）————————————————————————————————————————————————————————

def _plain(content) -> tuple[str, int]:
    if isinstance(content, str):
        return content, 0
    texts, n = [], 0
    for c in content if isinstance(content, list) else []:
        if isinstance(c, dict) and c.get("type") == "text":
            texts.append(str(c.get("text") or ""))
        else:
            n += 1
    return "\n".join(texts), n


async def received(run, content) -> bool:
    """chat.py 的 engine_stream 开头：message:received（能拦）；回完以后的 reply:sent 在后台等这一轮收尾再发。→ True = 拦下了
    （run.text 写上一句为什么，这一轮不跑）。"""
    agent = _agent_of(run.thread)
    text, attached = _plain(content)
    origin = str(getattr(run, "origin", "") or "user")
    who = "owner" if origin == "user" else origin
    payload = base_payload("message:received", agent, run.thread, text=text[:20000], attachments=attached, origin=origin, **{"from": who},
                           trusted=origin in ("user", "auto"), session_id=run.thread)
    out = await fire("message:received", payload, agent=agent)
    if any_for("reply:sent", agent):
        _later(_reply_sent(run, agent))
    if out.blocked:
        run.text = L(f"（这句话被钩子拦下了，没交给 Agent：{out.reason}）", f"(A hook stopped this message before it reached the Agent: {out.reason})")
        return True
    return False


async def _reply_sent(run, agent: str) -> None:
    for _ in range(int(7200 / 0.25)):
        if getattr(run, "done", False):
            break
        await asyncio.sleep(0.25)
    await fire("reply:sent", base_payload("reply:sent", agent, run.thread, text=str(run.text or "")[:20000], status=getattr(run, "status", ""),
                                          model=getattr(run, "model", ""), reply_id=getattr(run, "reply_id", None)), agent=agent)


def opened(thread: str, agent: str, session, prev=None, reason: str = "day") -> None:
    """engine/owner.py 开了一条新会话：session:start；换下来的那条（有的话）算 session:end。都在后台。reason：day（新的一天）、
    idle（闲久了换新）、new / reset（/new、/reset）、resume（/resume 接一条旧的）。"""
    if prev is not None:
        fire_soon("session:end", base_payload("session:end", agent, thread, session=getattr(prev, "id", None), reason=reason), agent=agent)
    fire_soon("session:start", base_payload("session:start", agent, thread, session=getattr(session, "id", None), reason=reason), agent=agent)


def compacting(fn):
    """engine/compact.py 的 compact 套一层：compact:before / compact:after（都在后台，不等）。"""
    async def wrapped(session, spec, tid, **kw):
        agent, conv = getattr(spec, "agent", None), getattr(spec, "conversation", "")
        fire_soon("compact:before", base_payload("compact:before", agent, conv, session=getattr(session, "id", None), turn=tid,
                                                 reason=kw.get("reason"), tokens_before=kw.get("before")), agent=agent)
        out = await fn(session, spec, tid, **kw)
        fire_soon("compact:after", base_payload("compact:after", agent, conv, session=getattr(session, "id", None), turn=tid,
                                                reason=kw.get("reason"), ok=out is not None), agent=agent)
        return out
    wrapped.__wrapped__ = fn
    wrapped.__doc__ = fn.__doc__
    return wrapped


def _agent_of(thread: str) -> str:
    try:
        from .owner import agent_of
        return agent_of(thread)
    except Exception:  # noqa: BLE001
        return "main"


def start() -> None:
    """服务起来时（main.py 的 lifespan，claw 是 builtin）：startup 的钩子；开了 boot-md 的跑每个工作区的 BOOT.md。都在后台。"""
    fire_soon("startup", base_payload("startup", None, "", app=settings.app_name))
    if bundled("boot-md"):
        _later(boot_all())


# —— 自带的两个 ——————————————————————————————————————————————————————————————————

BOOT_PROMPT = ("Run the workspace boot check now.\n\nYou are running a boot check: the server just started. Follow the BOOT.md instructions "
               "below exactly. Nothing you say here is posted to any chat; use your tools if BOOT.md asks you to tell the user something. "
               "If nothing needs attention, reply with exactly NO_REPLY.\n\nBOOT.md:\n")


async def boot(agent: str) -> dict | None:
    """一个 Agent 工作区里非空的 BOOT.md：当一轮主人回合跑一次（新会话、不往对话里发）。→ 结果；没有 BOOT.md → None。"""
    from . import heartbeat, unattended
    from .context import workspace_of
    ws = workspace_of(agent)
    p = ws / "BOOT.md" if ws is not None else None
    if p is None or not p.is_file() or p.is_symlink():
        return None
    try:
        text = p.read_text(encoding="utf8", errors="replace").strip()[:20000]
    except OSError:
        return None
    if not text:
        return None
    model = heartbeat.model_of(agent, {"model": ""})
    if not model:
        print(f"[engine] {agent} 的 BOOT.md 没跑：没有不花套餐的模型")
        return {"agent": agent, "status": "error", "error": "no model"}
    out = await unattended.fresh(agent, f"boot:{agent}", f"boot:{agent}", BOOT_PROMPT + text, model=model)
    print(f"[engine] {agent} 的 BOOT.md 跑完了：{out.status}")
    return {"agent": agent, "status": out.status, "text": out.text, "error": getattr(out, "error", "")}


async def boot_all() -> list[dict]:
    done: set[str] = set()
    out = []
    for agent in _agents():
        from .context import workspace_of
        ws = workspace_of(agent)
        if ws is None or str(ws.resolve()) in done:  # 几个 Agent 共用一个工作区：只跑一次（同 OpenClaw）
            continue
        done.add(str(ws.resolve()))
        try:
            if (r := await boot(agent)) is not None:
                out.append(r)
        except Exception as e:  # noqa: BLE001 — 一个没跑成不拖累别的
            print(f"[engine] {agent} 的 BOOT.md 没跑成：{type(e).__name__}: {str(e)[:160]}")
    return out


def _builtin(event: str, payload: dict, agent: str | None) -> None:
    """command-logger（开了才记）：会话开始 / 结束、斜杠命令各记一行 JSONL。"""
    if not bundled("command-logger"):
        return
    action = {"session:start": "session:start", "session:end": "session:end"}.get(event)
    if event == "message:received" and str(payload.get("text") or "").lstrip().startswith("/"):
        action = "command:" + str(payload["text"]).lstrip().split()[0][1:][:40]
    if action is None:
        return
    line = {"timestamp": datetime.now(settings.tz).isoformat(timespec="seconds"), "action": action, "agent": agent or "main",
            "conversation": payload.get("conversation") or "", "source": payload.get("from") or payload.get("origin") or "server"}
    p = settings.data_dir / "logs" / "commands.jsonl"
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.is_symlink():
            return
        with open(p, "a", encoding="utf8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"[engine] command-logger 没记上：{e}")


# —— 状态 ——————————————————————————————————————————————————————————————————————

def status() -> dict:
    allowed = allowlist()
    items = []
    for h in hooks():
        fp = h.fingerprint()
        items.append({**h.public(), "fingerprint": fp, "approved": h.fn is not None or fp in allowed,
                      "state": "error" if h.error else "ready" if (h.fn is not None or fp in allowed) else "waiting", "last": list(_last.get(fp, []))})
    return {"hooks": items, "bundled": {"boot-md": bundled("boot-md"), "command-logger": bundled("command-logger")},
            "workspace": cfg().get("workspace") is True}


@router.get("/api/engine/hooks")
def hooks_status():
    """有哪些钩子、同意了没有、最近几次跑的结果（不含环境变量）。"""
    return {"ok": True, **status()}
