"""带代码的插件（第 5 批第 8 项，默认关）：插件的 Python 接进内核，给 Agent 加工具、钩子、技能、MCP 服务。照 Hermes 的插件
（hermes_cli/plugins*.py：manifest + register(ctx)、plugins.enabled 白名单、--ref 只认 40 位提交、能力要同意、能力多了要重新同意、
载入有时限、出错只坏它自己）和 OpenClaw 的原生插件自己写的，没有拿代码。

！！插件的 Python 在服务器进程里跑，没有沙箱。它能做服务器进程能做的任何事：读 server.json、secrets.env 和各家的密钥、连网、起进程、
改文件。OpenClaw 的原话："Native OpenClaw plugins run in-process with the Gateway. They are not sandboxed. A loaded native plugin
has the same process-level trust boundary as core code." 这里一样。下面这些规矩管的是「它说自己要做什么」和「你同意了什么」，
挡不住一个存心作恶的插件：只装你信得过的。

默认什么都不载：server.json 的 engine.plugins.enabled 不写 = 一个都不载；写成名字的列表才载那几个（还得装好、你同意过它的能力）。
  "engine": {"plugins": {"enabled": ["weather"], "load_timeout": 10, "entries": {"weather": {"agents": ["main"]}}}}
一个插件 = <data_dir>/plugins/<名字>/ 一个文件夹：
  openmousse.plugin.json  {"name": "weather", "version": "0.1.0", "description": "…",
                           "capabilities": {"tools": [{"name": "forecast", "undoable": false}], "hooks": ["tool:before"],
                                            "skills": "skills", "mcp": ["weather-mcp"]}}
  plugin.py               def register(ctx): …（普通函数，不是 async）
ctx 只给这几样，manifest 里没声明的一概拒（RegisterError）：
  ctx.tool(name, fn, description=…, schema=…, undoable=False)  工具：名字 <插件>__<工具>；只给主人回合，只给 entries.<插件>.agents 里的 Agent
      （默认 main；在沙箱里干活的 Agent 一个都不给）；过关卡：声明了 undoable（撤得回）的直接调，别的每次交卡（kind tool），同意后照原样调一次。
      fn(args) 回字符串或 dict，可以是 async；有时限；结果包成「资料，不是指令」
  ctx.hook(event, fn, matcher="", timeout=30)  钩子（engine/user_hooks.py 的事件：startup、message:received、tool:before…；fn(payload)
      回 {"decision": "block" | "modify", …} 或 None，和脚本钩子一样的规矩；在进程里跑，不另外过钩子的白名单：插件本身要你同意过）
  ctx.skills_dir("skills")  插件里的技能文件夹：所有 Agent 都看得到（在用户级的后面、core 前面），照旧过技能的门槛和名单
  ctx.mcp_server(name, entry)  加一个 MCP 服务（写法和 server.json 的 mcp_servers 一样，照 mcp_client.parse 查；server.json 里同名的盖过它）
  ctx.name、ctx.root、ctx.log(...)
同意（能力）：manifest 声明的能力串（tool:x、undoable:x、hook:事件、skills、mcp:x）就是你同意的东西，记在 <data_dir>/plugins/plugins.json。
  命令行 mousse plugins install（看过能力和扫描结果打了 y）、设置接口的 install 算同意；开着、还没同意（或者更新以后能力多了）的不载，
  交一张卡（kind tool），点了同意才载。能力少了不用重新同意。
装：本机文件夹，或者 git 地址 + 40 位提交（分支、标签、短哈希一概不收；clone 以后核对 HEAD 就是那一个提交）。文件先进暂存区，
  过一遍上限和路径（照技能市场：不许往外跑的路径、链接、可执行的二进制），engine/skills_guard 扫一遍，再加 Python 的静态检查
  （执行动态代码、起 shell、加载本地库、读环境变量、连网……）：都只提醒（写在卡上、命令行里），不拦，信不信由你。
载（start / load）：服务起来时（main.py，claw 是 builtin），每个开着的插件在一个线程里 import + register(ctx)，最多 load_timeout 秒
  （默认 10，最多 120）；超时、出错的这一个不载（它已经登记的全部作废），别的照载，状态里写明。改了插件要重启服务器才换新的
  （Python 卸不掉已经载进来的模块）。
命令行：mousse plugins list / install / enable / disable / approve / remove（../plugins_ctl.py）。
设置接口（只认主人的设备）：GET /api/setup/plugins、POST /api/setup/plugins/preview {"source", "commit"?}、
  POST /api/setup/plugins/install {"staged"}、POST /api/setup/plugins/{名字}/approve、PUT /api/setup/plugins/{名字} {"enabled"}、
  DELETE /api/setup/plugins/{名字}。
"""
from __future__ import annotations

import ast
import asyncio
import contextlib
import hashlib
import importlib.util
import inspect
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import types
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request

from config import settings
from i18n import L

from . import calls, models, setup_api
from .loop import ToolCtx, ToolFailure
from .setup_api import SetupError

MANIFEST, ENTRY = "openmousse.plugin.json", "plugin.py"
NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]|_(?!_)){0,31}$")     # 插件名、工具名：小写字母、数字、- 和 _（不许连着两个 _，那是分隔符）
SHA = re.compile(r"^[0-9a-fA-F]{40}$")
LOAD_TIMEOUT, LOAD_TIMEOUT_MAX = 10.0, 120.0
TOOL_TIMEOUT, HOOK_TIMEOUT = 60.0, 30.0
CONSENT, CALL = "plugin", "plugin-call"   # calls.register 的前缀：同意能力的卡、先问的工具调用的卡
GIT_TIMEOUT = 120
router = APIRouter(dependencies=[Depends(setup_api.owner)])


class PluginError(Exception):
    """装、载、同意出的错：一句给人看的话。"""


class RegisterError(PluginError):
    """register(ctx) 里要了 manifest 没声明的、写得不对的。"""


# —— 配置、位置 ——————————————————————————————————————————————————————————————————————

def cfg() -> dict:
    v = models.cfg().get("plugins")
    return v if isinstance(v, dict) else {}


def enabled() -> list[str]:
    """engine.plugins.enabled：写了的名字（不写、写的不是列表 = 一个都不载）。"""
    v = cfg().get("enabled")
    return [str(x) for x in v if isinstance(x, str) and NAME_RE.match(x)] if isinstance(v, list) else []


def load_timeout() -> float:
    v = cfg().get("load_timeout")
    return LOAD_TIMEOUT if isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0 else min(float(v), LOAD_TIMEOUT_MAX)


def agents_of(name: str) -> list[str]:
    e = cfg().get("entries")
    e = e.get(name) if isinstance(e, dict) else None
    a = e.get("agents") if isinstance(e, dict) else None
    return [str(x) for x in a if isinstance(x, str)] if isinstance(a, list) else ["main"]


def home() -> Path:
    return settings.data_dir / "plugins"


def _lock_path() -> Path:
    return home() / "plugins.json"


def lock() -> dict:
    try:
        v = json.loads(_lock_path().read_text(encoding="utf8"))
    except (OSError, ValueError):
        v = {}
    return v if isinstance(v, dict) else {}


def _save_lock(d: dict) -> None:
    p = _lock_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, p)


_lock_mutex = threading.Lock()


def _update_lock(name: str, change: Callable[[dict], None]) -> dict:
    with _lock_mutex:
        d = lock()
        e = d.get(name) if isinstance(d.get(name), dict) else {}
        change(e)
        if e:
            d[name] = e
        else:
            d.pop(name, None)
        _save_lock(d)
        return e


# —— manifest、能力 ——————————————————————————————————————————————————————————————————

@dataclass
class Manifest:
    name: str
    version: str = ""
    description: str = ""
    tools: dict[str, bool] = field(default_factory=dict)   # 工具名 → 撤得回（undoable）
    hooks: list[str] = field(default_factory=list)
    skills: str = ""                                         # 技能文件夹（相对插件目录）；空 = 没有
    mcp: list[str] = field(default_factory=list)

    def caps(self) -> list[str]:
        """能力串（你同意的就是它）：tool:x、undoable:x、hook:事件、skills、mcp:x。"""
        out = [f"tool:{t}" for t in sorted(self.tools)] + [f"undoable:{t}" for t in sorted(self.tools) if self.tools[t]]
        out += [f"hook:{e}" for e in sorted(set(self.hooks))] + (["skills"] if self.skills else []) + [f"mcp:{m}" for m in sorted(set(self.mcp))]
        return out


def caps_hash(caps: list[str]) -> str:
    return hashlib.sha256("\n".join(sorted(set(caps))).encode()).hexdigest()


def cap_label(c: str) -> str:
    kind, _, what = c.partition(":")
    return {"tool": L(f"工具 {what}（每次先问你）", f"Tool {what} (asks you each time)"),
            "undoable": L(f"工具 {what} 不问就调（它说自己撤得回）", f"Tool {what} runs without asking (it says it's undoable)"),
            "hook": L(f"钩子 {what}（能看到这个事件的内容，能拦、能改）", f"Hook on {what} (sees the event, can block or change it)"),
            "skills": L("自带技能（所有 Agent 都看得到）", "Bundled skills (every Agent sees them)"),
            "mcp": L(f"加一个 MCP 服务 {what}", f"Adds an MCP server {what}")}.get(kind, c)


def _listed(caps: dict, key: str) -> list:
    """capabilities 里的一个列表：没写 = []；写成别的（true、5、一串字）抛 PluginError，不让它在载的时候、列状态的时候炸。"""
    v = caps.get(key)
    if not v:
        return []
    if not isinstance(v, list):
        raise PluginError(L(f"capabilities.{key} 要是一个列表", f"capabilities.{key} must be a list"))
    return v


def read_manifest(root: Path) -> Manifest:
    """读 openmousse.plugin.json → Manifest；写得不对抛 PluginError（不带文件内容）。"""
    from . import user_hooks
    try:
        raw = json.loads((root / MANIFEST).read_text(encoding="utf8"))
    except OSError:
        raise PluginError(L(f"没有 {MANIFEST}", f"No {MANIFEST}")) from None
    except ValueError:
        raise PluginError(L(f"{MANIFEST} 不是 JSON", f"{MANIFEST} isn't valid JSON")) from None
    if not isinstance(raw, dict):
        raise PluginError(L(f"{MANIFEST} 要是一个对象", f"{MANIFEST} must be an object"))
    name = str(raw.get("name") or "").strip()
    if not NAME_RE.match(name):
        raise PluginError(L("name 只能是小写字母、数字、- 和 _（不许连着两个 _），最长 32", "name: lowercase letters, digits, - and _ (no double "
                            "underscore), at most 32"))
    caps = raw.get("capabilities") or {}
    if not isinstance(caps, dict):
        raise PluginError(L("capabilities 要是一个对象", "capabilities must be an object"))
    m = Manifest(name=name, version=str(raw.get("version") or "")[:40], description=" ".join(str(raw.get("description") or "").split())[:300])
    for t in _listed(caps, "tools"):
        tn, undo = (t, False) if isinstance(t, str) else (t.get("name"), t.get("undoable") is True) if isinstance(t, dict) else (None, False)
        if not isinstance(tn, str) or not NAME_RE.match(tn) or len(f"{name}__{tn}") > 64:
            raise PluginError(L(f"工具名不对：{str(tn)[:40]}", f"Bad tool name: {str(tn)[:40]}"))
        m.tools[tn] = undo
    for e in _listed(caps, "hooks"):
        ev = user_hooks.ALIASES.get(str(e), str(e))
        if ev not in user_hooks.EVENTS:
            raise PluginError(L(f"没有 {str(e)[:40]} 这个事件", f"Unknown hook event: {str(e)[:40]}"))
        m.hooks.append(ev)
    sk = caps.get("skills")
    m.skills = "skills" if sk is True else str(sk).strip().strip("/") if isinstance(sk, str) and sk.strip() else ""
    for s in _listed(caps, "mcp"):
        if not isinstance(s, str) or not s.strip():
            raise PluginError(L("mcp 写服务名的列表", "mcp must be a list of server names"))
        m.mcp.append(s.strip())
    return m


# —— 扫描、静态检查（都只提醒）—————————————————————————————————————————————————————————

PY_RULES = {  # 规则 → (轻重, 中文, 英文)
    "dynamic_code": ("high", "执行运行时拼出来的代码（exec / eval / compile）", "runs code built at runtime (exec / eval / compile)"),
    "shell": ("high", "起 shell 命令（os.system / popen / shell=True）", "runs shell commands (os.system / popen / shell=True)"),
    "subprocess": ("medium", "起别的程序（subprocess）", "starts other programs (subprocess)"),
    "native": ("high", "加载本地库（ctypes / cffi）", "loads native code (ctypes / cffi)"),
    "dynamic_import": ("medium", "按运行时的名字导入模块", "imports modules by a name computed at runtime"),
    "deserialize": ("medium", "反序列化（pickle / marshal），喂进来的数据能执行代码", "deserializes data (pickle / marshal), which can run code"),
    "network": ("medium", "连网", "talks to the network"),
    "environ": ("medium", "读环境变量（各家的密钥在里面）", "reads environment variables (the server's API keys are there)"),
    "secret_files": ("high", "提到 server.json、secrets.env、.ssh 这类文件", "mentions server.json, secrets.env, .ssh or similar files"),
    "deletes": ("low", "删文件", "deletes files"),
    "syntax": ("medium", "解析不了（载不起来）", "can't be parsed (it won't load)"),
}
NET_MODULES = {"socket", "http", "urllib", "requests", "httpx", "aiohttp", "websockets", "ftplib", "smtplib", "paramiko"}
SECRET_WORDS = re.compile(r"server\.json|secrets\.env|\.ssh\b|id_rsa|id_ed25519|\.netrc|\.aws/credentials", re.I)


def py_findings(files: dict[str, bytes]) -> list[dict]:
    """插件里每个 .py 过一遍 Python 的静态检查（ast，不跑）→ [{"severity", "rule", "file", "line", "description"}]。只提醒。"""
    out: list[dict] = []
    for rel, data in sorted(files.items()):
        if not rel.endswith(".py"):
            continue
        try:
            tree = ast.parse(data.decode("utf8", "replace"), filename=rel)
        except (SyntaxError, ValueError, RecursionError) as e:
            out.append(_finding("syntax", rel, getattr(e, "lineno", 0) or 0))
            continue
        seen: set[tuple[str, int]] = set()

        def hit(rule: str, node, _rel: str = rel, _seen: set = seen) -> None:
            key = (rule, getattr(node, "lineno", 0))
            if key not in _seen:
                _seen.add(key)
                out.append(_finding(rule, _rel, key[1]))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mods = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
                for mname in mods:
                    top = mname.split(".")[0]
                    if top in ("ctypes", "cffi"):
                        hit("native", node)
                    elif top in NET_MODULES:
                        hit("network", node)
                    elif top in ("pickle", "marshal", "dill", "shelve"):
                        hit("deserialize", node)
            elif isinstance(node, ast.Call):
                f = node.func
                fname = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""
                owner = f.value.id if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) else ""
                if fname in ("exec", "eval", "compile") and isinstance(f, ast.Name):
                    hit("dynamic_code", node)
                elif (owner == "os" and fname in ("system", "popen")) or any(k.arg == "shell" and isinstance(k.value, ast.Constant) and k.value.value
                                                                              for k in node.keywords):
                    hit("shell", node)
                elif owner == "subprocess" or (owner == "os" and (fname.startswith("exec") or fname.startswith("spawn"))):
                    hit("subprocess", node)
                elif (fname == "__import__" or fname == "import_module") and node.args and not isinstance(node.args[0], ast.Constant):
                    hit("dynamic_import", node)
                elif owner == "os" and fname == "getenv":
                    hit("environ", node)
                elif fname in ("rmtree", "unlink", "remove", "rmdir") and owner in ("shutil", "os"):
                    hit("deletes", node)
            elif isinstance(node, ast.Attribute) and node.attr == "environ" and isinstance(node.value, ast.Name) and node.value.id == "os":
                hit("environ", node)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str) and SECRET_WORDS.search(node.value):
                hit("secret_files", node)
    return out


def _finding(rule: str, rel: str, line: int) -> dict:
    sev, zh, en = PY_RULES[rule]
    return {"severity": sev, "rule": rule, "file": rel, "line": line, "description": L(zh, en), "category": "python"}


def scan(d: Path, files: dict[str, bytes]) -> dict:
    """engine/skills_guard 按社区、不受信扫一遍 + Python 的静态检查 → {"verdict", "findings"}（都只提醒，不拦）。"""
    from . import skill_market
    res = skill_market.scan(d)
    verdict, found = skill_market.with_unscanned(res.verdict, skill_market.findings_of(res, n=20) + py_findings(files), files)
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    found.sort(key=lambda f: (order.get(f["severity"], 4), f["file"], f["line"]))
    return {"verdict": verdict, "findings": found[:40], "more": max(0, len(found) - 40)}


# —— 装：暂存、git 只认 40 位提交 ——————————————————————————————————————————————————————————

def _staging() -> Path:
    return home() / ".staging"


def _read_tree(src: Path) -> dict[str, bytes]:
    """一个文件夹里的文件（过技能市场同一套上限和路径；链接不跟，.git 这类不要）。"""
    from . import skill_market
    if not src.is_dir():
        raise PluginError(L(f"不是一个文件夹：{src}", f"Not a folder: {src}"))
    files: dict[str, bytes] = {}
    total = 0
    for base, dirs, names in os.walk(src):
        dirs[:] = sorted(x for x in dirs if x not in skill_market.SKIP_DIRS and not (Path(base) / x).is_symlink())
        for n in sorted(names):
            p = Path(base) / n
            if p.is_symlink():
                raise PluginError(L(f"插件里有链接：{p.relative_to(src)}（不收）", f"The plugin contains a link: {p.relative_to(src)} (not accepted)"))
            if p.is_file():
                rel = p.relative_to(src).as_posix()
                size = p.stat().st_size  # 先看大小再读：仓库里带个几 GB 的文件，别整份读进内存
                total += size
                if size > skill_market.FILE_MAX or total > skill_market.TOTAL_MAX:
                    raise PluginError(L(f"{rel[:80]} 太大（一个文件最多 {skill_market.FILE_MAX // 1024} KB，加起来最多 "
                                        f"{skill_market.TOTAL_MAX // 1024 // 1024} MB）", f"{rel[:80]} is too big (at most "
                                        f"{skill_market.FILE_MAX // 1024} KB per file, {skill_market.TOTAL_MAX // 1024 // 1024} MB in all)"))
                files[rel] = p.read_bytes()
                if len(files) > skill_market.FILES_MAX:
                    raise PluginError(L(f"文件太多（最多 {skill_market.FILES_MAX} 个）", f"Too many files (at most {skill_market.FILES_MAX})"))
    try:
        return skill_market.check_files(files, need=MANIFEST)
    except skill_market.MarketError as e:
        raise PluginError(str(e)) from None


def _git(args: list[str], cwd: Path | None = None) -> str:
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "LANG", "TMPDIR", "SSH_AUTH_SOCK", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY",
                                                       "https_proxy", "http_proxy", "no_proxy", "GIT_SSL_CAINFO", "SSL_CERT_FILE")}
    env.update(GIT_TERMINAL_PROMPT="0", GIT_ALLOW_PROTOCOL="https:ssh:git:file", GIT_CONFIG_NOSYSTEM="1")
    try:
        p = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-c", "protocol.ext.allow=never", *args], cwd=cwd, env=env,  # noqa: S603, S607
                           capture_output=True, text=True, timeout=GIT_TIMEOUT)
    except FileNotFoundError:
        raise PluginError(L("这台电脑上没有 git", "git isn't installed here")) from None
    except subprocess.TimeoutExpired:
        raise PluginError(L(f"git 超过 {GIT_TIMEOUT} 秒没完", f"git took longer than {GIT_TIMEOUT}s")) from None
    if p.returncode != 0:
        raise PluginError(L("git 没成：", "git failed: ") + re.sub(r"https?://[^/\s]*@", "https://***@", (p.stderr or p.stdout).strip())[-300:])
    return p.stdout.strip()


def fetch_git(url: str, commit: str) -> tuple[dict[str, bytes], str]:
    """git 地址 + 40 位提交 → (文件, 提交)。分支、标签、短哈希一概不收；clone 以后 fetch 那一个提交、detach，核对 HEAD。"""
    if not SHA.match(commit or ""):
        raise PluginError(L("提交要写完整的 40 位哈希（分支、标签、短哈希都不收）", "commit must be a full 40-character SHA (no branch, tag or "
                            "short hash)"))
    if url.startswith(("-", "ext::")) or "::" in url:
        raise PluginError(L("这种 git 地址不收", "That kind of git address isn't accepted"))
    commit = commit.lower()
    with tempfile.TemporaryDirectory(prefix="mousse-plugin-") as tmp:
        repo = Path(tmp) / "repo"
        _git(["init", "-q", str(repo)])
        _git(["remote", "add", "origin", url], cwd=repo)
        _git(["fetch", "-q", "--depth", "1", "origin", commit], cwd=repo)
        _git(["checkout", "-q", "--detach", commit], cwd=repo)
        head = _git(["rev-parse", "HEAD"], cwd=repo).lower()
        if head != commit:
            raise PluginError(L(f"取回来的是 {head[:12]}，不是要的 {commit[:12]}", f"Checked out {head[:12]}, not the requested {commit[:12]}"))
        return _read_tree(repo), commit


@dataclass
class Staged:
    token: str
    root: Path
    manifest: Manifest
    source: str
    commit: str
    fingerprint: str
    scan: dict

    def public(self) -> dict:
        return {"staged": self.token, "name": self.manifest.name, "version": self.manifest.version, "description": self.manifest.description,
                "source": self.source, "commit": self.commit or None, "fingerprint": self.fingerprint,
                "capabilities": [{"id": c, "label": cap_label(c)} for c in self.manifest.caps()], "scan": self.scan,
                "warning": IN_PROCESS, "installed": (home() / self.manifest.name).is_dir()}


IN_PROCESS = L("插件的 Python 在服务器进程里跑，没有沙箱：它能做服务器能做的任何事（读你的密钥、连网、改文件）。只装你信得过的。",
               "A plugin's Python runs inside the server process, with no sandbox: it can do anything the server can (read your keys, use the "
               "network, change files). Only install plugins you trust.")


def stage(source: str, commit: str = "") -> Staged:
    """取一个插件放进暂存区、扫一遍：本机文件夹，或者 git 地址 + 40 位提交。不装、不载。"""
    from . import skill_market
    src = str(source or "").strip()
    if not src:
        raise PluginError(L("写插件在哪：本机文件夹，或者 git 地址（加 --commit）", "Say where the plugin is: a local folder, or a git address "
                            "(with a commit)"))
    local = Path(src).expanduser()
    if commit or (not local.exists() and ("://" in src or src.startswith("git@") or src.endswith(".git"))):
        files, commit = fetch_git(src, commit)
        source_id = re.sub(r"https?://[^/\s]*@", "https://", src)
    elif not local.is_dir():
        raise PluginError(L(f"没有这个文件夹：{src[:200]}（git 地址要加 40 位提交）", f"No such folder: {src[:200]} (a git address needs a 40-character commit)"))
    else:
        files, source_id = _read_tree(local.resolve()), str(local.resolve())
    token = "p" + uuid.uuid4().hex[:16]
    d = _staging() / token / "plugin"
    d.mkdir(parents=True)
    try:
        for rel, data in files.items():
            p = d / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o644)
            with os.fdopen(fd, "wb") as f:
                f.write(data)
        m = read_manifest(d)
        if ENTRY not in files:
            raise PluginError(L(f"没有 {ENTRY}", f"No {ENTRY}"))
        st = Staged(token, d, m, source_id, commit, skill_market.digest(files), scan(d, files))
        (d.parent / "staged.json").write_text(json.dumps({"source": source_id, "commit": commit, "fingerprint": st.fingerprint}), encoding="utf8")
    except BaseException:
        shutil.rmtree(d.parent, ignore_errors=True)
        raise
    return st


def staged(token: str) -> Staged:
    from . import skill_market
    if not re.fullmatch(r"p[0-9a-f]{16}", token or ""):
        raise PluginError(L("staged 不对", "Bad staged id"))
    d = _staging() / token / "plugin"
    try:
        meta = json.loads((d.parent / "staged.json").read_text(encoding="utf8"))
    except (OSError, ValueError):
        raise PluginError(L("暂存区里没有这一份（过期了？重新 preview）", "Nothing staged under that id (expired? preview again)")) from None
    files = skill_market.disk_files(d)
    if skill_market.digest(files) != meta.get("fingerprint"):
        raise PluginError(L("暂存区里的文件被改过了：重新 preview", "The staged files changed; preview again"))
    return Staged(token, d, read_manifest(d), meta.get("source") or "", meta.get("commit") or "", meta["fingerprint"], scan(d, files))


def install(st: Staged, *, by: str, replace: bool = False) -> dict:
    """暂存的这一份搬进 <data_dir>/plugins/<名字>/，记进 plugins.json，算你同意了它现在的能力（by：谁同意的）。已经有同名的：replace
    才换（旧的先挪进 .removed/）。改了的要重启服务器才载新的。"""
    name = st.manifest.name
    dst = home() / name
    if dst.exists():
        if not replace:
            raise PluginError(L(f"已经装了一个 {name}：要换就用更新（--replace）", f"{name} is already installed (use --replace to update it)"))
        _shelve(name)
    os.replace(st.root, dst)
    shutil.rmtree(st.root.parent, ignore_errors=True)
    caps = st.manifest.caps()

    def go(e: dict) -> None:
        e.update(source=st.source, commit=st.commit or None, fingerprint=st.fingerprint, version=st.manifest.version,
                 installedAt=datetime.now(settings.tz).isoformat(timespec="seconds"),
                 consent={"caps": caps, "hash": caps_hash(caps), "by": by, "at": datetime.now(settings.tz).isoformat(timespec="seconds")})
    _update_lock(name, go)
    return {"ok": True, "name": name, "path": str(dst), "capabilities": caps, "enabled": name in enabled()}


def _shelve(name: str) -> Path | None:
    """拿掉之前先挪进 <data_dir>/plugins/.removed/<名字>-<时间>/（留一份）。"""
    src = home() / name
    if not src.exists():
        return None
    dst = home() / ".removed" / f"{name}-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"
    dst.parent.mkdir(parents=True, exist_ok=True)
    os.replace(src, dst)
    return dst


def remove(name: str) -> dict:
    if not NAME_RE.match(name or "") or not (home() / name).exists():
        raise PluginError(L(f"没有装 {name}", f"{name} isn't installed"))
    kept = _shelve(name)
    _update_lock(name, lambda e: e.clear())
    set_enabled(name, False)
    return {"ok": True, "name": name, "kept": str(kept) if kept else None}


def set_enabled(name: str, on: bool) -> dict:
    """engine.plugins.enabled 里加上 / 去掉（写 server.json 之前先备份）。改了要重启服务器才载 / 卸。"""
    def go(d: dict) -> None:
        sec = setup_api.section(d, "engine", "plugins")
        cur = [x for x in sec.get("enabled") or [] if isinstance(x, str)] if isinstance(sec.get("enabled"), list) else []
        if on and name not in cur:
            cur.append(name)
        if not on:
            cur = [x for x in cur if x != name]
        if cur or "enabled" in sec:
            sec["enabled"] = cur
        setup_api.prune(d, "engine", "plugins")
    return setup_api.edit(go)


def consent_state(name: str, m: Manifest | None = None) -> tuple[bool, list[str]]:
    """同意过它现在的能力了吗 → (同意了, 多出来的能力)。能力少了不算。"""
    e = lock().get(name) or {}
    granted = set(((e.get("consent") or {}).get("caps")) or [])
    m = m or read_manifest(home() / name)
    extra = [c for c in m.caps() if c not in granted]
    return not extra and bool(e.get("consent")), extra


def approve(name: str, *, by: str, expect: str | None = None) -> dict:
    """同意它现在的能力（卡点了同意、命令行 approve、设置接口）。expect：卡上那一串能力的哈希，manifest 后来又变了就不认。"""
    m = read_manifest(home() / name)
    caps = m.caps()
    if expect is not None and caps_hash(caps) != expect:
        raise PluginError(L("插件的能力在卡交上来以后又变了：要重新同意", "The plugin's capabilities changed after the card was made; it needs a new OK"))
    _update_lock(name, lambda e: e.update(consent={"caps": caps, "hash": caps_hash(caps), "by": by,
                                                   "at": datetime.now(settings.tz).isoformat(timespec="seconds")}))
    return {"ok": True, "name": name, "capabilities": caps}


# —— 载：ctx、有时限、出错只坏它自己 ——————————————————————————————————————————————————

@dataclass
class PluginTool:
    plugin: str
    name: str            # 声明的名字（模型看到的是 <插件>__<名字>）
    fn: Callable
    description: str
    schema: dict
    undoable: bool


class Ctx:
    """register(ctx) 拿到的（见文件开头）：只有这几样。载完（或者超时）以后再调一概拒。"""

    def __init__(self, name: str, root: Path, manifest: Manifest) -> None:
        self.name, self.root = name, root
        self._m = manifest
        self._closed = False
        self.tools: dict[str, PluginTool] = {}
        self.hooks: list = []
        self.skills: Path | None = None
        self.mcp: dict[str, dict] = {}

    def _open(self) -> None:
        if self._closed:
            raise RegisterError(L("载入已经结束了，不能再登记", "Loading is over; nothing more can be registered"))

    def tool(self, name: str, fn: Callable, *, description: str = "", schema: dict | None = None, undoable: bool = False) -> None:
        self._open()
        if name not in self._m.tools:
            raise RegisterError(L(f"manifest 里没声明工具 {name}", f"Tool {name} isn't declared in the manifest"))
        if undoable and not self._m.tools[name]:
            raise RegisterError(L(f"工具 {name} 在 manifest 里没声明 undoable", f"Tool {name} isn't declared undoable in the manifest"))
        if not callable(fn):
            raise RegisterError(L("fn 要是一个函数", "fn must be callable"))
        sch = schema if isinstance(schema, dict) else {"type": "object", "properties": {}}
        self.tools[name] = PluginTool(self.name, name, fn, " ".join(str(description or name).split())[:1000], {**sch, "type": "object"}, undoable)

    def hook(self, event: str, fn: Callable, *, matcher: str = "", timeout: float = HOOK_TIMEOUT) -> None:
        from . import user_hooks
        self._open()
        ev = user_hooks.ALIASES.get(event, event)
        if ev not in self._m.hooks:
            raise RegisterError(L(f"manifest 里没声明钩子 {event}", f"Hook {event} isn't declared in the manifest"))
        if not callable(fn):
            raise RegisterError(L("fn 要是一个函数", "fn must be callable"))
        t = max(1.0, min(float(timeout or HOOK_TIMEOUT), user_hooks.MAX_TIMEOUT))
        self.hooks.append(user_hooks.Hook(event=ev, matcher=str(matcher or ""), timeout=t, origin=f"plugin:{self.name}",
                                          name=getattr(fn, "__name__", "hook"), fn=_as_async(fn)))

    def skills_dir(self, rel: str = "skills") -> None:
        self._open()
        if not self._m.skills or rel.strip("/") != self._m.skills:
            raise RegisterError(L(f"manifest 里没声明技能文件夹 {rel}", f"Skills folder {rel} isn't declared in the manifest"))
        p = (self.root / rel).resolve()
        if not p.is_relative_to(self.root.resolve()) or not p.is_dir():
            raise RegisterError(L(f"插件里没有 {rel} 这个文件夹", f"No folder {rel} in the plugin"))
        self.skills = p

    def mcp_server(self, name: str, entry: dict) -> None:
        from . import mcp_client
        self._open()
        if name not in self._m.mcp:
            raise RegisterError(L(f"manifest 里没声明 MCP 服务 {name}", f"MCP server {name} isn't declared in the manifest"))
        c = mcp_client.parse(name, entry)
        if c.error and not c.missing:
            raise RegisterError(c.error)
        self.mcp[name] = dict(entry)

    def log(self, *parts) -> None:
        print(f"[plugin {self.name}]", *parts)


def _as_async(fn: Callable) -> Callable:
    if inspect.iscoroutinefunction(fn):
        return fn

    async def run(*a, **k):
        res = await asyncio.to_thread(fn, *a, **k)
        return await res if inspect.isawaitable(res) else res
    return run


@dataclass
class Loaded:
    name: str
    manifest: Manifest
    ctx: Ctx
    ms: int


_loaded: dict[str, Loaded] = {}
_status: dict[str, dict] = {}       # 名字 → {"state": loaded / error / needs-consent / not-installed, "error", "extra"}
_asked: set[str] = set()            # 交过同意卡的（名字 + 能力哈希）
PKG = "openmousse_plugins"


def _import(name: str, root: Path, ctx: Ctx) -> None:
    """import plugin.py（包名 openmousse_plugins.<名字>，插件里可以相对导入）+ register(ctx)。在载入线程里跑。"""
    if PKG not in sys.modules:
        pkg = types.ModuleType(PKG)
        pkg.__path__ = []
        sys.modules[PKG] = pkg
    mod_name = f"{PKG}.{name.replace('-', '_')}_{uuid.uuid4().hex[:6]}"
    spec = importlib.util.spec_from_file_location(mod_name, root / ENTRY, submodule_search_locations=[str(root)])
    if spec is None or spec.loader is None:
        raise PluginError(L(f"载不了 {ENTRY}", f"Can't load {ENTRY}"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    try:
        spec.loader.exec_module(mod)
        reg = getattr(mod, "register", None)
        if not callable(reg):
            raise PluginError(L(f"{ENTRY} 里没有 register(ctx)", f"{ENTRY} has no register(ctx)"))
        res = reg(ctx)
        if inspect.isawaitable(res):
            with contextlib.suppress(Exception):
                res.close()
            raise PluginError(L("register 要是普通函数（不是 async）", "register must be a plain function (not async)"))
    except BaseException:
        sys.modules.pop(mod_name, None)
        raise


def load(name: str) -> dict:
    """载一个插件（见文件开头）→ 它的状态。已经载了的不重载。出错、超时只坏它自己：它登记的全部作废。"""
    if name in _loaded:
        return _status[name]
    root = home() / name
    try:
        if not (root / MANIFEST).is_file():
            _status[name] = {"state": "not-installed", "error": L("没装", "not installed")}
            return _status[name]
        m = read_manifest(root)
        if m.name != name:
            raise PluginError(L(f"文件夹叫 {name}，manifest 里写的是 {m.name}", f"Folder {name} but the manifest says {m.name}"))
        ok, extra = consent_state(name, m)
        if not ok:
            _status[name] = {"state": "needs-consent", "error": "", "extra": extra}
            _later_ask(name, m, extra)
            return _status[name]
        ctx = Ctx(name, root, m)
        box: dict[str, BaseException] = {}
        t0 = time.monotonic()

        def worker() -> None:
            try:
                _import(name, root, ctx)
            except BaseException as e:  # noqa: BLE001 — 插件自己的错（含 SystemExit）：只坏它自己
                box["error"] = e
        th = threading.Thread(target=worker, name=f"plugin-load:{name}", daemon=True)
        th.start()
        th.join(load_timeout())
        if th.is_alive():
            ctx._closed = True
            raise PluginError(L(f"{load_timeout():.0f} 秒内没载完（没载；那个线程丢在后台）", f"didn't finish loading within {load_timeout():.0f}s "
                                "(not loaded; its thread was abandoned)"))
        ctx._closed = True
        if "error" in box:
            e = box["error"]
            raise PluginError(str(e) if isinstance(e, PluginError) else f"{type(e).__name__}: {str(e)[:300]}")
        _commit(Loaded(name, m, ctx, int((time.monotonic() - t0) * 1000)))
        _status[name] = {"state": "loaded", "error": "", "ms": _loaded[name].ms}
    except Exception as e:  # noqa: BLE001 — PluginError，或者别的意外（锁文件坏了……）：只坏它自己，服务器照起
        why = str(e) if isinstance(e, PluginError) else f"{type(e).__name__}: {e}"
        _status[name] = {"state": "error", "error": why[:500]}
        print(f"[engine] 插件 {name} 没载上：{why[:300]}")
    return _status[name]


def _commit(ld: Loaded) -> None:
    """载成了：登记的东西接进内核（钩子进 user_hooks.PLUGINS；工具、技能、MCP 由 tools_for / skill_dirs / mcp_servers 现查）。"""
    from . import user_hooks
    _loaded[ld.name] = ld
    user_hooks.PLUGINS.extend(ld.ctx.hooks)


def unload_all() -> None:
    """测试用：忘掉载过的（Python 模块卸不掉，只是不再接进内核）。"""
    from . import user_hooks
    user_hooks.PLUGINS[:] = [h for h in user_hooks.PLUGINS if not str(h.origin).startswith("plugin:")]
    _loaded.clear()
    _status.clear()
    _asked.clear()


def load_all() -> dict[str, dict]:
    for name in enabled():
        load(name)
    return dict(_status)


async def start() -> None:
    """服务起来时（main.py，claw 是 builtin；在 startup 的钩子之前）：载开着的插件（在线程里，每个有时限）。没开就什么都不做。"""
    global _main_loop
    if enabled():
        _main_loop = asyncio.get_running_loop()
        try:
            await asyncio.to_thread(load_all)
        except Exception as e:  # noqa: BLE001 — 插件出什么事都不挡服务器起来
            print(f"[engine] 插件没载完：{type(e).__name__}: {str(e)[:300]}")


# —— 接进内核：工具、技能、MCP ——————————————————————————————————————————————————————————

def skill_dirs() -> list[Path]:
    """载上的插件的技能文件夹（tools.skill_dirs 放在用户级后面、core 前面）。"""
    return [ld.ctx.skills for ld in _loaded.values() if ld.ctx.skills is not None]


def mcp_servers() -> dict[str, dict]:
    """载上的插件加的 MCP 服务（mcp_client.raw_servers 放在最前面，server.json 里同名的盖过它）。"""
    out: dict[str, dict] = {}
    for ld in _loaded.values():
        out.update(ld.ctx.mcp)
    return out


def _tool_text(res: Any) -> str:
    if isinstance(res, str):
        return res
    with contextlib.suppress(TypeError, ValueError):
        return json.dumps(res, ensure_ascii=False)
    return str(res)


async def call_tool(pt: PluginTool, args: dict) -> str:
    try:
        # asyncio.timeout，不用 wait_for：3.11 的 wait_for 把插件放进另一个 Task，它里面的 sys.exit 会直接冒到事件循环、带走服务器
        async with asyncio.timeout(TOOL_TIMEOUT):
            res = await _as_async(pt.fn)(dict(args))
    except asyncio.TimeoutError:
        raise ToolFailure(L(f"插件 {pt.plugin} 的 {pt.name} 超过 {TOOL_TIMEOUT:.0f} 秒没回", f"{pt.plugin}'s {pt.name} didn't answer within "
                            f"{TOOL_TIMEOUT:.0f}s")) from None
    except (ToolFailure, asyncio.CancelledError):
        raise
    except BaseException as e:  # noqa: BLE001 — 插件自己的错（含 sys.exit 的 SystemExit：不许带走服务器）：原话给模型（截短）
        raise ToolFailure(f"{type(e).__name__}: {str(e)[:500]}") from None
    import bridges
    return bridges.clip(_tool_text(res))


def _find(full: str) -> PluginTool | None:
    plugin, _, tname = full.partition("__")
    ld = _loaded.get(plugin)
    return ld.ctx.tools.get(tname) if ld is not None else None


def tools_for(me: str) -> list:
    """这个 Agent 这一轮的插件工具（载上的、entries.<插件>.agents 里有它的；默认只给 main）。"""
    from . import tools as tools_mod
    out = []
    for ld in _loaded.values():
        if me not in agents_of(ld.name):
            continue
        for pt in ld.ctx.tools.values():
            out.append(_tool(f"{ld.name}__{pt.name}", pt, tools_mod))
    return out


def _tool(full: str, pt: PluginTool, tools_mod):
    async def run(args: dict, ctx: ToolCtx) -> str:
        cur = _find(full)
        if cur is None:
            raise ToolFailure(L(f"插件工具 {full} 不在了", f"The plugin tool {full} is gone"))
        return tools_mod.wrap_untrusted(full, await call_tool(cur, args))

    async def ask_user(args: dict, ctx: ToolCtx) -> dict | str:
        import inbox
        who = inbox.source_name(ctx.agent or "main", inbox.names())
        pretty = json.dumps(args, ensure_ascii=False, indent=2)
        card = {"title": L(f"插件 {pt.plugin}：{pt.name}", f"Plugin {pt.plugin}: {pt.name}"),
                "why": L(f"{who}想用插件 {pt.plugin} 的「{pt.name}」。", f"{who} wants to use {pt.name} from the {pt.plugin} plugin.") + " "
                + pt.description[:200],
                "changes": [f"{k}: {json.dumps(v, ensure_ascii=False)[:120]}" for k, v in list(args.items())[:8]],
                "detail": L("同意后，服务器照下面的参数调用一次插件的这个函数（在服务器进程里跑），结果发回对话。", "If you approve, the server calls "
                            "this plugin function once with exactly these arguments (inside the server process) and posts the result to the chat.")
                + f"\n\n```json\n{pretty}\n```",
                "approveLabel": L("照做", "Do it")}
        return await calls.ask(CALL, full, {"plugin": pt.plugin, "tool": pt.name, "arguments": args}, ctx, card)

    def classify(args: dict) -> tuple[str, str]:
        return (tools_mod.UNDO, "插件声明了撤得回、你同意过") if pt.undoable else (tools_mod.ASK, "插件的工具：每次先问")

    desc = f"[plugin {pt.plugin}] {pt.description}" + ("" if pt.undoable else "\n\n" + L(
        "（这个工具每次都要用户先点头：调用会给他发一张卡，马上返回；他同意后服务器照原样调用一次，把结果发进你的对话。）",
        "(Each call needs the user's OK first: calling it sends them a card and returns right away; once they approve, the server makes "
        "exactly that call and posts the result into your chat.)"))
    return calls.AskTool(name=full, description=desc, schema=pt.schema, run=run, kinds=("owner",), timeout=TOOL_TIMEOUT + 15,
                         title=lambda args: f"{pt.plugin} · {pt.name}", classify=classify, ask_user=ask_user)


async def _call_approved(name: str, args: dict, row) -> str:
    """先问的插件工具，卡点了同意：插件还载着、工具还在、这个 Agent 还能用才调。"""
    from . import tools as tools_mod
    pt = _find(name)
    if pt is None:
        raise ToolFailure(L("这个插件工具已经不在了（插件没载、关了或者删了）", "That plugin tool is gone (the plugin isn't loaded, was turned off "
                            "or removed)"))
    if row["agent"] not in agents_of(pt.plugin):
        raise ToolFailure(L(f"{row['agent']} 现在不能用这个插件了", f"{row['agent']} can't use this plugin any more"))
    text = await call_tool(pt, dict(args.get("arguments") or {}))
    room = tools_mod.RESULT_MAX - 600
    return tools_mod.wrap_untrusted(name, text if len(text) <= room else text[:room] + "…")


calls.register(CALL, _call_approved)


# —— 同意卡（开着、还没同意或者能力多了）——————————————————————————————————————————————————

def card(m: Manifest, extra: list[str]) -> dict:
    e = lock().get(m.name) or {}
    src = e.get("source") or ""
    return {"title": L(f"让插件 {m.name} 载进来", f"Let the {m.name} plugin load"),
            "why": IN_PROCESS + " " + (L("它的能力比你上次同意的多了：", "It asks for more than you agreed to last time: ") + "、".join(cap_label(c) for c in extra)
                                       if (e.get("consent") and extra) else L("它要下面这些能力。", "It asks for the capabilities below.")),
            "changes": [cap_label(c) for c in m.caps()],
            "detail": L("同意后，它下次载入时（现在开着的会马上载）拿到这些能力；以后能力再多要重新同意。", "If you approve, it gets these capabilities "
                        "when it loads (right away if it's turned on); it needs a new OK if it ever asks for more.")
            + f"\n\nversion: {m.version or '?'}\nsource: {src or '?'}" + (f"\ncommit: {e.get('commit')}" if e.get("commit") else ""),
            "approveLabel": L("同意，载进来", "Approve and load")}


def _later_ask(name: str, m: Manifest, extra: list[str]) -> None:
    """交同意卡（kind tool，前缀 plugin；同一串能力只交一次）。在不在事件循环里都行。"""
    key = f"{name}:{caps_hash(m.caps())}"
    if key in _asked:
        return
    _asked.add(key)

    async def go() -> None:
        from .loop import Control
        from .tools import thread_for
        ctx = ToolCtx(turn="", kind="owner", agent="main", conversation=thread_for("main", "main"), call_id=f"plugin-{key[-12:]}", control=Control())
        try:
            await calls.ask(CONSENT, name, {"plugin": name, "hash": caps_hash(m.caps()), "caps": m.caps()}, ctx, card(m, extra))
        except Exception as e:  # noqa: BLE001
            _asked.discard(key)
            print(f"[engine] 插件 {name} 的同意卡没交上：{type(e).__name__}: {str(e)[:120]}")
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None:  # 在事件循环里：后台交
        _bg.add(t := loop.create_task(go()))
        t.add_done_callback(_bg.discard)
    elif _main_loop is not None and _main_loop.is_running():  # 载入线程里（服务器起来时）：交给主循环
        asyncio.run_coroutine_threadsafe(go(), _main_loop)
    else:  # 命令行、测试：当场交
        asyncio.run(go())


_bg: set = set()
_main_loop: asyncio.AbstractEventLoop | None = None


async def _consent_approved(name: str, args: dict, row) -> str:
    """同意卡点了同意：能力还是卡上那一串才认；开着的马上载。"""
    global _main_loop
    _main_loop = asyncio.get_running_loop()
    try:
        approve(name, by="owner (card)", expect=str(args.get("hash") or ""))
    except PluginError as e:
        raise ToolFailure(str(e)) from None
    _status.pop(name, None)
    if name in enabled():
        st = await asyncio.to_thread(load, name)
        if st.get("state") != "loaded":
            return L(f"同意了，但 {name} 没载上：{st.get('error')}", f"Approved, but {name} didn't load: {st.get('error')}")
        return L(f"同意了，{name} 载上了。", f"Approved; {name} is loaded.")
    return L(f"同意了。{name} 还没开（engine.plugins.enabled）：开了以后重启服务器就载。", f"Approved. {name} isn't turned on yet "
             "(engine.plugins.enabled); it loads after you turn it on and restart the server.")


calls.register(CONSENT, _consent_approved)


# —— 状态、接口 ——————————————————————————————————————————————————————————————————————

def status() -> list[dict]:
    from . import skill_market
    out = []
    names = ({p.name for p in home().iterdir() if p.is_dir() and not p.name.startswith(".")} if home().is_dir() else set()) | set(enabled())
    lk = lock()
    for name in sorted(names):
        e = lk.get(name) or {}
        row: dict[str, Any] = {"name": name, "enabled": name in enabled(), "installed": (home() / name / MANIFEST).is_file(),
                               "source": e.get("source"), "commit": e.get("commit"), "version": e.get("version"), "agents": agents_of(name),
                               **(_status.get(name) or {"state": "off" if name not in enabled() else "pending", "error": ""})}
        if row["installed"]:
            try:
                m = read_manifest(home() / name)
                ok, extra = consent_state(name, m)
                row.update(description=m.description, capabilities=[{"id": c, "label": cap_label(c)} for c in m.caps()], consented=ok, moreCaps=extra,
                           localChanges=bool(e.get("fingerprint")) and skill_market.digest(skill_market.disk_files(home() / name)) != e.get("fingerprint"))
            except Exception as err:  # noqa: BLE001 — 一个插件的状态读不出来，别的照列
                row.update(state="error", error=str(err) if isinstance(err, PluginError) else f"{type(err).__name__}: {str(err)[:300]}")
        ld = _loaded.get(name)
        if ld is not None:
            row.update(tools=[f"{name}__{t}" for t in ld.ctx.tools], hooks=[h.event for h in ld.ctx.hooks], skills=bool(ld.ctx.skills),
                       mcp=sorted(ld.ctx.mcp), ms=ld.ms)
        out.append(row)
    return out


def _err(e: PluginError) -> SetupError:
    return SetupError(400, str(e))


@router.get("/api/setup/plugins")
def api_list():
    return {"ok": True, "enabled": enabled(), "warning": IN_PROCESS, "plugins": status()}


@router.post("/api/setup/plugins/preview")
async def api_preview(request: Request):
    b = await setup_api.body_of(request)
    try:
        st = await asyncio.to_thread(stage, str(b.get("source") or ""), str(b.get("commit") or ""))
    except PluginError as e:
        raise _err(e) from None
    return {"ok": True, **st.public()}


@router.post("/api/setup/plugins/install")
async def api_install(request: Request):
    b = await setup_api.body_of(request)
    try:  # 重扫一遍整份：在线程里，别卡住别的请求
        st = await asyncio.to_thread(staged, str(b.get("staged") or ""))
        return await asyncio.to_thread(install, st, by="owner (settings)", replace=b.get("replace") is True)
    except PluginError as e:
        raise _err(e) from None


@router.post("/api/setup/plugins/{name}/approve")
def api_approve(name: str):
    try:
        return approve(name, by="owner (settings)")
    except PluginError as e:
        raise _err(e) from None


@router.put("/api/setup/plugins/{name}")
async def api_enable(name: str, request: Request):
    b = await setup_api.body_of(request)
    if not isinstance(b.get("enabled"), bool):
        raise SetupError(400, L("写 {\"enabled\": true | false}", "Send {\"enabled\": true | false}"))
    if not NAME_RE.match(name):
        raise SetupError(400, L("插件名不对", "Bad plugin name"))
    res = set_enabled(name, b["enabled"])
    return {"ok": True, **res, "enabled": enabled(), "restart": True}


@router.delete("/api/setup/plugins/{name}")
def api_remove(name: str):
    try:
        return remove(name)
    except PluginError as e:
        raise SetupError(404, str(e)) from None
