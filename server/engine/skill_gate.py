"""技能的门槛和配置（第 5 批第 1 项，2026-10-19；对照表 6.4、6.5）：外来的技能（ClawHub、Hermes、自己写的）不用改写就能用。

门槛（不满足的不列：会话开头的技能目录、skill 工具、每句搜索、经 /mcp 给外援的都照它，入口是 engine/agent_skills.py 的 skills_for；
设置接口里照样列出来，写明缺什么：「要 ffmpeg」「要 FOO_API_KEY」）：
  OpenClaw 的写法（frontmatter 的 metadata，JSON 写一行或者 YAML）：
    metadata: {"openclaw": {"requires": {"bins": ["ffmpeg"], "anyBins": ["rg", "grep"], "env": ["FOO_API_KEY"], "config": ["browser.enabled"]},
                            "os": ["darwin", "linux"], "always": false, "primaryEnv": "FOO_API_KEY"}}
    bins      每一个都要在 PATH 上（能执行）；anyBins 有一个就行（列表也认逗号分隔的一串）
    env       engine.skills.entries.<名字>.env（apiKey 算 primaryEnv 那一个）里有；名字不像密钥的，进程环境、app 里存的（secrets.env）、
              env_file 里有也算。像密钥的（secretish）只认 entries 里主人给的：技能声明了要，不等于把模型密钥交给它
    config    server.json 里的路径（a.b.c），值算「真」：非空字符串、不是 false / 0 / null（OpenClaw 的 isConfigPathTruthy）
    os        darwin / linux / win32（macos、windows 也认）；always: true 不看 requires，os 照样看（同 OpenClaw 的 evaluateRuntimeEligibility）
    也认 metadata.clawdbot（ClawHub 上老技能的写法）。
  Hermes 的写法：platforms: [macos, linux, windows]（不合这台机器的不列）；metadata.hermes.requires_tools / requires_toolsets（这个 Agent
    没有那几个工具的不列）、fallback_for_tools / fallback_for_toolsets（有那几个工具时不列：它是没有那个工具时的替代）；
    required_environment_variables（也认 setup.collect_secrets、prerequisites.env_vars）缺了不藏，标 setup_needed：目录里名字后面写
    (setup needed)，读全文时后面附一句 [Skill setup note: …]（Hermes 的 _build_setup_note）。
  我们自己的：metadata.openmousse，键和 OpenClaw 的一样；几家都写了的合在一起（列表并起来，always、primaryEnv 先看我们的）。
配置：server.json 的 engine.skills.entries.<名字>（照 OpenClaw 的 skills.entries）：
  enabled  false = 关：哪儿都不列，设置接口里写「关掉了」
  env      {"FOO_API_KEY": "${FOO_API_KEY}"}：这个技能用的环境变量；名字像密钥的只能写成 ${变量名}（值从进程环境、secrets.env、env_file 取），
           照写的值不认（同 mcp_client）
  apiKey   "${变量名}"：primaryEnv 那一个的简写（OpenClaw）
  config   {"键": 值}：给技能看的非密钥设置。skill 工具读全文时附在后面（照 Hermes 的 [Skill config …]）；技能在 metadata.hermes.config
           声明了键、说明、默认值的，没填的写默认值，没默认值写 (not set)
密钥：缺的变量经设置接口填（PUT /api/setup/skills/config/<名字>，engine/agent_skills.py）：值只进 secrets.env，server.json 只写 ${变量名}。
谁拿得到真值：主机上的命令（engine/host_exec.py 的 child_env 加的那一行 → host_env）只在这个 Agent 有这个技能（名单上有、过了门槛、没关）时，
  拿到这个技能声明的（requires.env、primaryEnv、required_environment_variables）和 entries.<名字>.env 写了的那几个（像密钥的只从
  entries 来）；PATH、LD_*、PYTHONPATH 这类能改程序行为的一律不给（host_exec 的 DROP_ENV / DROP_PREFIX，加上 OpenClaw env-overrides
  挡的那几个）。
  沙箱照旧只有占位符（engine/sandbox.py、../egress_proxy.py），真值不进容器；MCP、连接器不经这里。
读 frontmatter 按 SKILL.md 的 (路径, mtime, 大小) 记着；PATH 上有没有某个程序按 PATH 记着找到的（没找到的下次再找：装好了马上就有）。

required_environment_variables 的读法改写自 Hermes 的 tools/skills_tool_setup.py（_get_required_environment_variables），
[Skill config …] 的样子照 agent/skill_commands.py（_inject_skill_config），metadata.hermes.config 的读法照 agent/skill_utils.py
（extract_skill_config_vars），NousResearch/hermes-agent commit cc6b1f299bd3a7c0309f003d97dc32014e71fa80，MIT，Copyright (c) 2025
Nous Research；许可全文见仓库根目录的 THIRD_PARTY_NOTICES。门槛的算法照 OpenClaw 1099ccc1 的 src/shared/config-eval.ts、requirements.ts、
frontmatter.ts（只借做法）。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from config import raw
from i18n import L

META_KEYS = ("openmousse", "openclaw", "clawdbot")  # 我们的在前：always、primaryEnv 先看它
PLATFORMS = {"macos": "darwin", "mac": "darwin", "osx": "darwin", "darwin": "darwin", "linux": "linux", "windows": "win32", "win32": "win32",
             "win": "win32"}
ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
SECRETISH = re.compile(r"api[-_]?key|token|secret|passw|credential|cookie|private[-_]?key|access[-_]?key|auth", re.I)
# 主机命令里不许技能改的（能改程序的行为、冒充我们自己的）：host_exec 的 DROP_ENV / DROP_PREFIX 再加这几个
NEVER = {"PATH", "HOME", "USER", "LOGNAME", "SHELL", "PWD", "OLDPWD", "TMPDIR", "IFS", "ENV", "BASH_ENV", "BASHOPTS", "SHELLOPTS", "KSH_ENV",
         "ZDOTDIR", "PS4", "PROMPT_COMMAND", "NODE_OPTIONS", "NODE_PATH", "NODE_TLS_REJECT_UNAUTHORIZED", "NODE_EXTRA_CA_CERTS", "PYTHONPATH",
         "PYTHONHOME", "PYTHONSTARTUP", "PYTHONINSPECT", "PYTHONIOENCODING", "PERL5OPT", "PERL5LIB", "RUBYOPT", "RUBYLIB", "JAVA_TOOL_OPTIONS",
         "_JAVA_OPTIONS", "GLIBC_TUNABLES", "GCONV_PATH", "SSLKEYLOGFILE", "OPENSSL_CONF", "MAKEFLAGS", "DOTNET_STARTUP_HOOKS", "HOSTALIASES",
         "BROWSER", "EDITOR", "VISUAL", "PAGER", "GIT_DIR", "GIT_EXEC_PATH", "GIT_TEMPLATE_DIR", "GIT_SSL_NO_VERIFY", "GIT_EXTERNAL_DIFF",
         "GIT_SSH", "GIT_SSH_COMMAND", "GIT_ASKPASS", "SSH_ASKPASS", "SUDO_ASKPASS", "SSH_AUTH_SOCK", "LESSOPEN", "LESSCLOSE", "HTTP_PROXY",
         "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "KUBECONFIG",
         "AWS_CONFIG_FILE", "AWS_SHARED_CREDENTIALS_FILE", "GOOGLE_APPLICATION_CREDENTIALS", "DOCKER_HOST", "DOCKER_CONFIG"}
NEVER_PREFIX = ("LD_", "DYLD_", "BASH_FUNC_", "MOUSSE_", "OPENCLAW_", "CLAUDECODE", "CLAUDE_CODE", "NPM_CONFIG_", "PIP_", "UV_", "GIT_CONFIG_",
                "CARGO_REGISTRIES_", "TF_VAR_", "LUA_INIT", "XDG_")
# Hermes 的工具集 → 我们这边对应的工具（有一个就算有）；不在这里的工具集算没有
TOOLSETS = {"terminal": ("host_exec", "sandbox_exec"), "code_execution": ("execute_code", "sandbox_exec", "host_exec"),
            "web": ("web_search", "web_fetch"), "search": ("web_search",), "browser": ("browser", "chrome"),
            "file": ("read_file", "sandbox_read_file"), "memory": ("memory_search",), "session_search": ("sessions_list",), "cronjob": ("cron",),
            "delegation": ("tasks",), "todo": ("todo",), "skills": ("skill",), "clarify": ("ask_user",),
            "vision": ("view_image",), "image_gen": ("image_generate",), "tts": ("speak",)}  # 后三个：第 3 批的手
# Hermes 的工具名 → 我们的（同名的直接认）
TOOL_ALIASES = {"terminal": ("host_exec", "sandbox_exec"), "execute_code": ("execute_code", "sandbox_exec", "host_exec"),
                "web_extract": ("web_fetch",), "web_crawl": ("web_fetch",), "browser_navigate": ("browser", "chrome"),
                "browser_snapshot": ("browser", "chrome"), "skill_view": ("skill",), "skills_list": ("skill",), "session_search": ("sessions_list",),
                "cronjob": ("cron",), "delegate_task": ("tasks",), "clarify": ("ask_user",), "patch": ("apply_patch", "edit_file"),
                "search_files": ("grep_files", "find_files"), "vision_analyze": ("view_image",), "text_to_speech": ("speak",)}
CONFIG_MAX = 40          # 附在全文后面的 config 最多几项
VALUE_MAX = 300          # 每一项的值最多多少字
_fm: dict[str, tuple[tuple[int, int], dict]] = {}   # SKILL.md 路径 → ((mtime, 大小), frontmatter)
_bins: dict[str, set[str]] = {}                     # PATH → 找到过的程序（没找到的不记：装好了马上就有）
_lock = threading.Lock()
_tools_seen: dict[str, tuple[float, frozenset]] = {}  # Agent → (什么时候算的, 它能有的工具名)：Hermes 的工具条件才用
TOOLS_TTL = 30.0


# —— 读 frontmatter ——————————————————————————————————————————————————————————————

def front(path: Path) -> dict:
    """SKILL.md 的 frontmatter（tools._front 的读法），按 (mtime, 大小) 记着。读不了 → {}。"""
    key = str(path)
    try:
        st = path.stat()
    except OSError:
        return {}
    sig = (st.st_mtime_ns, st.st_size)
    hit = _fm.get(key)
    if hit and hit[0] == sig:
        return hit[1]
    try:
        text = path.read_text(encoding="utf8", errors="replace")
    except OSError:
        return {}
    from .tools import _front
    fm = _front(text)
    with _lock:
        if len(_fm) > 2000:
            _fm.clear()
        _fm[key] = (sig, fm)
    return fm


def _obj(v: Any) -> dict:
    """metadata 这一类：dict 原样；写成 JSON 字符串的解开（OpenClaw 写一行 JSON，YAML 读出来可能还是字符串）；别的 → {}。"""
    if isinstance(v, dict):
        return v
    if isinstance(v, str) and v.strip().startswith("{"):
        try:
            j = json.loads(v)
        except ValueError:
            try:
                import yaml
                j = yaml.safe_load(v)
            except Exception:  # noqa: BLE001
                j = None
        return j if isinstance(j, dict) else {}
    return {}


def _strs(v: Any) -> list[str]:
    """列表或逗号分隔的一串（OpenClaw 的 normalizeCsvOrLooseStringList）→ 去空、去重的字符串。"""
    items = v if isinstance(v, list) else str(v).split(",") if isinstance(v, str) else []
    return list(dict.fromkeys(s for s in (str(x).strip() for x in items if isinstance(x, (str, int, float))) if s))


def _as_dicts(v: Any) -> list:
    if isinstance(v, dict):
        return [v]
    return list(v) if isinstance(v, list) else []


def _clean(v: Any) -> str:
    return " ".join(str(v).split()) if isinstance(v, (str, int, float)) and str(v).strip() else ""


def required_env(fm: dict) -> list[dict]:
    """Hermes 的 required_environment_variables（也认 setup.collect_secrets、prerequisites.env_vars）→ [{"name", "prompt", "help"?, "optional"?}]，
    去重、先写的算（改写自 Hermes 的 _get_required_environment_variables，见文件开头）。"""
    setup = fm.get("setup") if isinstance(fm.get("setup"), dict) else {}
    setup_help = _clean(setup.get("help"))
    prereqs = fm.get("prerequisites")
    legacy = (prereqs.get("env_vars") if isinstance(prereqs, dict) else None) or []
    legacy = [legacy] if isinstance(legacy, str) else legacy if isinstance(legacy, list) else []
    entries = [{"name": i} if isinstance(i, str) else i for i in _as_dicts(fm.get("required_environment_variables")) if isinstance(i, (str, dict))]
    entries += [{"name": i.get("env_var"), "prompt": i.get("prompt"), "url": str(i.get("provider_url") or i.get("url") or "").strip() or None}
                for i in _as_dicts(setup.get("collect_secrets")) if isinstance(i, dict)]
    entries += [{"name": str(v)} for v in legacy if str(v).strip()]
    out: dict[str, dict] = {}
    for e in entries:
        name = str(e.get("name") or e.get("env_var") or "").strip()
        if not name or name in out or not ENV_NAME.match(name):
            continue
        item = {"name": name, "prompt": _clean(e.get("prompt")) or f"Enter value for {name}"}
        if h := _clean(e.get("help") or e.get("provider_url") or e.get("url") or setup_help):
            item["help"] = h
        if e.get("optional"):
            item["optional"] = True
        out[name] = item
    return list(out.values())


def config_vars(fm: dict) -> list[dict]:
    """metadata.hermes.config（也认 metadata.openmousse.config）声明的设置：[{"key", "description", "default"?}]（照 Hermes 的 extract_skill_config_vars）。"""
    meta = _obj(fm.get("metadata"))
    out: dict[str, dict] = {}
    for block in ("openmousse", "hermes"):
        b = _obj(meta.get(block))
        for item in _as_dicts(b.get("config")):
            if not isinstance(item, dict):
                continue
            key, desc = str(item.get("key") or "").strip(), _clean(item.get("description"))
            if not key or key in out or not desc:
                continue
            out[key] = {"key": key, "description": desc, **({"default": item["default"]} if item.get("default") is not None else {})}
    return list(out.values())


@dataclass
class Needs:
    """一个技能要什么（几家的写法合在一起）。"""
    bins: list[str] = field(default_factory=list)
    any_bins: list[str] = field(default_factory=list)
    env: list[str] = field(default_factory=list)
    config: list[str] = field(default_factory=list)
    os: list[str] = field(default_factory=list)
    always: bool = False
    primary_env: str = ""
    setup_env: list[dict] = field(default_factory=list)   # Hermes 的：缺了不藏，标 setup_needed
    tools: list[str] = field(default_factory=list)        # Hermes 的 requires_tools / requires_toolsets（工具集写成 "toolset:名字"）
    fallback: list[str] = field(default_factory=list)     # fallback_for_tools / fallback_for_toolsets
    user_invocable: bool = True                           # OpenClaw 的 user-invocable：false = 不变成斜杠命令（第 6 项）

    def declared_env(self) -> list[str]:
        """这个技能自己声明要的环境变量（主机命令按它给真值）。"""
        return list(dict.fromkeys([*self.env, *([self.primary_env] if self.primary_env else []), *(e["name"] for e in self.setup_env)]))


def needs(fm: dict) -> Needs:
    meta = _obj(fm.get("metadata"))
    n = Needs()
    always: bool | None = None
    for key in META_KEYS:
        b = _obj(meta.get(key))
        if not b:
            continue
        req = _obj(b.get("requires"))
        n.bins += _strs(req.get("bins"))
        n.any_bins += _strs(req.get("anyBins") or req.get("any_bins"))
        n.env += [x for x in _strs(req.get("env")) if ENV_NAME.match(x)]
        n.config += _strs(req.get("config"))
        n.os += _strs(b.get("os"))
        if always is None and isinstance(b.get("always"), bool):
            always = b["always"]
        pe = str(b.get("primaryEnv") or b.get("primary_env") or "").strip()
        if not n.primary_env and ENV_NAME.match(pe):
            n.primary_env = pe
    n.always = bool(always)
    n.os += _strs(fm.get("platforms"))
    h = _obj(meta.get("hermes"))
    n.tools += _strs(h.get("requires_tools")) + [f"toolset:{x}" for x in _strs(h.get("requires_toolsets"))]
    n.fallback += _strs(h.get("fallback_for_tools")) + [f"toolset:{x}" for x in _strs(h.get("fallback_for_toolsets"))]
    n.setup_env = [e for e in required_env(fm) if e["name"] not in n.env]
    ui = fm.get("user-invocable", fm.get("user_invocable"))
    n.user_invocable = not (ui is False or (isinstance(ui, str) and ui.strip().lower() in ("false", "no", "0")))
    for attr in ("bins", "any_bins", "env", "config", "os", "tools", "fallback"):
        setattr(n, attr, list(dict.fromkeys(getattr(n, attr))))
    return n


# —— 配置：engine.skills.entries.<名字> ——————————————————————————————————————————————

def entries() -> dict:
    e = raw().get("engine")
    s = e.get("skills") if isinstance(e, dict) else None
    v = s.get("entries") if isinstance(s, dict) else None
    return v if isinstance(v, dict) else {}


def entry(name: str) -> dict:
    v = entries().get(name)
    return v if isinstance(v, dict) else {}


def enabled(name: str) -> bool:
    return entry(name).get("enabled") is not False


def _value(text: str) -> str:
    """${变量名}（可以夹在别的字中间）换成值（models.env_value：进程环境 → secrets.env → env_file）；取不到 = 空。"""
    from . import models
    missing = False

    def sub(m: re.Match) -> str:
        nonlocal missing
        v = models.env_value(m.group(1))
        if not v:
            missing = True
        return v
    out = VAR.sub(sub, text)
    return "" if missing else out


def entry_env(name: str, n: Needs | None = None) -> dict[str, str]:
    """entries.<名字>.env（和 apiKey → primaryEnv）换成值：{变量: 值}。名字像密钥、值又没写成 ${…} 的不认；取不到的不放进来。"""
    e = entry(name)
    out: dict[str, str] = {}
    env = e.get("env") if isinstance(e.get("env"), dict) else {}
    for k, v in env.items():
        k = str(k).strip()
        if not ENV_NAME.match(k) or not isinstance(v, (str, int, float)):
            continue
        v = str(v)
        if SECRETISH.search(k) and not VAR.search(v):
            continue
        if val := _value(v):
            out[k] = val
    key = e.get("apiKey")
    if isinstance(key, str) and VAR.search(key) and n is not None and n.primary_env and n.primary_env not in out:
        if val := _value(key):
            out[n.primary_env] = val
    return out


def secretish(var: str) -> bool:
    """名字像密钥：SECRETISH，加上 host_exec 从进程环境里剥掉的那几个词（KEY、DSN、WEBHOOK……）。"""
    from .host_exec import SECRET_WORDS
    up = var.upper()
    return bool(SECRETISH.search(var)) or any(w in up for w in SECRET_WORDS)


def has_env(var: str, name: str, n: Needs) -> bool:
    """这个变量有没有：entries.<名字>.env / apiKey 里有；名字不像密钥的，进程环境、secrets.env、env_file 里有也算。像密钥的只认主人在这个
    技能的设置里给了的：app 里存的模型密钥、服务器自己的环境变量，不会因为技能声明了要就交出去（缺了照常列「要 FOO_API_KEY」，设置里给）。"""
    from . import models
    return bool(entry_env(name, n).get(var)) or (not secretish(var) and bool(models.env_value(var)))


def _truthy(path: str) -> bool:
    """server.json 里的路径（a.b.c）的值算不算「真」（OpenClaw 的 isConfigPathTruthy）。"""
    v: Any = raw()
    for part in [p for p in path.split(".") if p]:
        if not isinstance(v, dict) or part.startswith("__"):
            return False
        v = v.get(part)
    if isinstance(v, str):
        return bool(v.strip())
    return v is not None and v is not False and v != 0


def has_bin(name: str) -> bool:
    """PATH 上有没有这个程序（能执行）。找到过的按 PATH 记着；没找到的下次再找（OpenClaw 也是只记找到的）。"""
    path = os.environ.get("PATH", "")
    with _lock:
        hits = _bins.setdefault(path, set())
        if len(_bins) > 16:
            _bins.clear()
            hits = _bins.setdefault(path, set())
        if name in hits:
            return True
    if "/" in name or not name.strip():
        return False
    if shutil.which(name, path=path):
        with _lock:
            _bins.setdefault(path, set()).add(name)
        return True
    return False


def this_os() -> str:
    return "darwin" if sys.platform == "darwin" else "win32" if sys.platform.startswith("win") else "linux" if sys.platform.startswith("linux") \
        else sys.platform


def os_ok(wanted: list[str]) -> bool:
    if not wanted:
        return True
    return this_os() in {PLATFORMS.get(w.strip().lower(), w.strip().lower()) for w in wanted}


def agent_tools(agent: str, offered: set[str] | None) -> set[str]:
    """Hermes 的工具条件看的：这一轮给的工具名，再加上这个 Agent 的手（主机文件、网页、沙箱、主机命令：tools.for_agent 里排在 skill 后面，
    offered 里可能还没有）和总会有的那几样。按 Agent 记 TOOLS_TTL 秒。"""
    have = set(offered or ())
    hit = _tools_seen.get(agent)
    if hit and time.monotonic() - hit[0] < TOOLS_TTL:
        return have | hit[1]
    extra: set[str] = {"skill", "memory_search", "memory_get", "cron", "sessions_list", "sessions_history", "session_status"}
    try:
        from . import handkit
        extra |= {t.name for t in handkit.tools_for(agent)}
    except Exception:  # noqa: BLE001 — 算不出来就只看 offered
        pass
    _tools_seen[agent] = (time.monotonic(), frozenset(extra))
    return have | extra


def _has_tool(want: str, tools: set[str]) -> bool:
    if want.startswith("toolset:"):
        return any(t in tools for t in TOOLSETS.get(want[8:].strip().lower(), ()))
    return want in tools or any(t in tools for t in TOOL_ALIASES.get(want, ()))


# —— 判一个技能 ——————————————————————————————————————————————————————————————————

@dataclass
class Status:
    name: str
    ok: bool                                   # 列不列（门槛都过了、没关）
    enabled: bool = True
    missing: list[dict] = field(default_factory=list)   # 藏起来的原因：{"kind", "what", "label"}
    setup: list[dict] = field(default_factory=list)     # 缺了不藏的环境变量（Hermes）：{"name", "prompt", "help"?}
    needs: Needs = field(default_factory=Needs)

    def labels(self) -> list[str]:
        return [m["label"] for m in self.missing]

    def public(self) -> dict:
        """设置接口给 app 看的（不带值）。"""
        n = self.needs
        return {"eligible": self.ok, "enabled": self.enabled, "missing": [{k: m[k] for k in ("kind", "what", "label")} for m in self.missing],
                "setupNeeded": [s["name"] for s in self.setup], "setup": self.setup, "env": n.declared_env(), "primaryEnv": n.primary_env or None,
                "always": n.always, "os": n.os, "userInvocable": n.user_invocable}


def _miss(kind: str, what: str) -> dict:
    labels = {
        "bin": (f"要 {what}", f"needs {what}"),
        "anyBins": (f"要 {what.replace(', ', ' / ')} 其中一个", f"needs one of {what}"),
        "env": (f"要 {what}", f"needs {what}"),
        "config": (f"要在 server.json 里开 {what}", f"needs {what} set in server.json"),
        "os": (f"只能在 {what} 上用（这台是 {this_os()}）", f"only works on {what} (this machine is {this_os()})"),
        "tool": (f"要 {what} 工具", f"needs the {what} tool"),
        "fallback": (f"有 {what} 时用不上（它是没有那个工具时的替代）", f"not needed while {what} is available (it's a stand-in for it)"),
        "disabled": ("在设置里关掉了", "turned off in settings"),
    }
    zh, en = labels[kind]
    return {"kind": kind, "what": what, "label": L(zh, en)}


def check(name: str, fm: dict, *, agent: str = "main", offered: set[str] | None = None) -> Status:
    """这个技能现在列不列、缺什么（见文件开头）。"""
    n = needs(fm)
    st = Status(name=name, ok=True, enabled=enabled(name), needs=n)
    if not st.enabled:
        st.missing.append(_miss("disabled", ""))
    if not os_ok(n.os):
        st.missing.append(_miss("os", ", ".join(n.os)))
    if not n.always:
        st.missing += [_miss("env", v) for v in n.env if not has_env(v, name, n)]
        st.missing += [_miss("config", p) for p in n.config if not _truthy(p)]
        st.missing += [_miss("bin", b) for b in n.bins if not has_bin(b)]
        if n.any_bins and not any(has_bin(b) for b in n.any_bins):
            st.missing.append(_miss("anyBins", ", ".join(n.any_bins)))
    if n.tools or n.fallback:
        tools = agent_tools(agent, offered)
        st.missing += [_miss("tool", t.removeprefix("toolset:")) for t in n.tools if not _has_tool(t, tools)]
        st.missing += [_miss("fallback", t.removeprefix("toolset:")) for t in n.fallback if _has_tool(t, tools)]
    st.setup = [e for e in n.setup_env if not e.get("optional") and not has_env(e["name"], name, n)]
    st.ok = not st.missing
    return st


def status_of(name: str, path: Path, *, agent: str = "main", offered: set[str] | None = None) -> Status:
    return check(name, front(path), agent=agent, offered=offered)


# —— 读全文时附在后面的 ————————————————————————————————————————————————————————————

def _shown(v: Any) -> str:
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    s = " ".join(str(s).split())
    return s if len(s) <= VALUE_MAX else s[:VALUE_MAX] + "…"


def config_note(name: str, fm: dict) -> str:
    """[Skill config …]（照 Hermes）：entries.<名字>.config 填了的，加上技能自己声明了、还没填的（默认值或 (not set)）。没有 → ""。"""
    cfg = entry(name).get("config")
    cfg = cfg if isinstance(cfg, dict) else {}
    lines: dict[str, str] = {}
    for var in config_vars(fm):
        v = cfg.get(var["key"], var.get("default"))
        lines[var["key"]] = _shown(v) if v not in (None, "") else "(not set)"
    for k, v in cfg.items():
        if str(k) not in lines and len(lines) < CONFIG_MAX:
            lines[str(k)] = _shown(v) if v not in (None, "") else "(not set)"
    if not lines:
        return ""
    body = "\n".join(f"  {k} = {v}" for k, v in list(lines.items())[:CONFIG_MAX])
    return f"\n\n[Skill config (from server.json engine.skills.entries.{name}.config):\n{body}\n]"


def setup_note(st: Status) -> str:
    """[Skill setup note: …]（Hermes 的 _build_setup_note）：缺了不藏的环境变量。没有 → ""。"""
    if not st.setup:
        return ""
    names = ", ".join(s["name"] for s in st.setup)
    helps = " ".join(dict.fromkeys(s["help"] for s in st.setup if s.get("help")))
    return (f"\n\n[Skill setup note: Setup needed before using this skill: missing {names}. The user adds it in the app's skill settings "
            f"(it's stored on their server, not in this chat); never ask them to paste a key into the conversation.{(' ' + helps) if helps else ''}]")


def extra(name: str, path: Path, *, agent: str = "main", offered: set[str] | None = None) -> str:
    """skill 工具读 SKILL.md 全文时接在后面的：[Skill config …] 和 [Skill setup note: …]。出错 → ""（不拖累读技能）。"""
    try:
        fm = front(path)
        return config_note(name, fm) + setup_note(check(name, fm, agent=agent, offered=offered))
    except Exception as e:  # noqa: BLE001
        print(f"[engine] 技能 {name} 的设置没附上：{type(e).__name__}: {str(e)[:120]}")
        return ""


# —— 主机命令拿到的环境变量 ——————————————————————————————————————————————————————————

def allowed_env(var: str) -> bool:
    up = var.upper()
    return bool(ENV_NAME.match(var)) and up not in NEVER and not up.startswith(NEVER_PREFIX)


def host_env(agent: str) -> dict[str, str]:
    """主机上的命令（engine/host_exec.child_env）多拿的：这个 Agent 能用的技能（skills_for：名单、门槛、开关都过了的）声明的环境变量，
    加上 entries.<名字>.env 写了的（名字像密钥的只有技能自己声明了才给，同 OpenClaw 的 env-overrides）；取得到值的那几个。名字像密钥的
    （secretish）只从 entries 里主人给的引用取；别的先看进程环境（外面设了的算数，同 OpenClaw），再看 entries，再看 secrets.env、env_file，
    值里带网址密码的不给。能改程序行为的（NEVER）不给。出错 → {}（命令照跑，只是没有这几个）。"""
    try:
        from . import agent_skills, models
        from .host_exec import URL_CRED
        out: dict[str, str] = {}
        for name, (_desc, path) in agent_skills.skills_for(agent).items():
            n = needs(front(path))
            declared = n.declared_env()
            mine = entry_env(name, n)
            for var in [*declared, *(v for v in mine if v not in declared and not secretish(v))]:
                if var in out or not allowed_env(var):
                    continue
                if secretish(var):
                    val = mine.get(var)  # 像密钥的：只给主人在这个技能的设置里给了的
                else:
                    val = os.environ.get(var) or mine.get(var) or models.env_value(var)
                    if val and var not in mine and URL_CRED.search(val):
                        val = ""
                if val:
                    out[var] = val
        return out
    except Exception as e:  # noqa: BLE001
        print(f"[engine] 技能的环境变量没给上：{type(e).__name__}: {str(e)[:120]}")
        return {}
