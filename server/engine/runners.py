"""外援子 Agent（内核第 5 项，2026-10-16）：本机装了、用户自己登录过的官方 Claude Code / Codex 当后台任务的子 Agent（tasks 工具的
runner = claude-code / codex，engine/tasks.py 调这里）。

条款（底座画布、openmousse-engine-choice）：第三方产品不许存订阅令牌、不许代用订阅额度；用户自己登录、没改过的官方 Claude Code 可以。
所以这里只是在这台电脑上起官方的命令行（claude / codex），用它自己的登录：我们不读、不存、不传它的令牌；起它之前把环境变量里像密钥的
（名字带 KEY / PASSWORD / SECRET / TOKEN，dsh 的 scrubbedParentEnv）都去掉，它只能用它自己存的登录。

有没有这个选项：命令在 PATH 上（或者 server.json 的 tasks.runners.<名>.bin）、用它自己的命令查过登录了（claude auth status --json 的
loggedIn、codex login status 的退出码）、装了 mcp 包（我们的工具经 /mcp 给它）。服务启动时（warm）在后台线程里查一次，
之后 10 分钟查一次（工具表每轮现算，不能卡住它）；没查过就当没有（测试里不会碰到这台机器上真的 claude）。tasks.runners.<名> = false 关掉。
启动后第一次还没查完的那几秒（checking，最多 STARTUP_GRACE 秒）：有这个命令的，主对话的套餐模型先当它在（app 别闪一下选模型的引导）。
另记一样登录的是什么（第 11 项，/api/engine/status 给 app）：auth = 它自己报的（claude auth status 的 authMethod：claude.ai /
api_key / api_key_helper / oauth_token / third_party；Codex 的 chatgpt / api key），billing = subscription（套餐）/ api_key（按量，记在
用户自己的账上）/ third_party；报了套餐档位（subscriptionType）的也记下。邮箱、组织这些一概不读。
跑主人回合的 Claude Code（engine/claude_brain.py，第 11 项）用的也是这里的检测和 bin。

怎么跑（一次性、不续聊，dsh 的 subagent-claude-code / subagent-codex 也是）：
  工作目录：<data_dir>/tasks/<任务 id>/，新建的空目录（0700），每个任务一个；外援写的东西留在里面。
  工具：派它的 Agent 的工具的子集（tasks.child_tools，去掉 tasks：一次性的外援收不到子任务的结果；第 11 项起含 skill、记忆检索），
    经 /mcp 给：一把只给这个任务的临时令牌（tasks.mint → engine/mcp_engine.py，只在内存里，任务结束就收回），tools/list 只列这几个，
    每次调用在服务器上照子 Agent 的关卡再查一遍（tasks.gate_for：默认只读，tasks.runners.<名>.level = "undo" 才放撤得回的写；撤不回的
    一律不做；冒充别人的不跑）。
  Claude Code：claude -p（stream-json）、不存会话（--no-session-persistence）、用户的设置一个不读（--setting-sources "" 加 --settings
    关 hooks / 插件 / 自动记忆 / CLAUDE.md，环境里也关自动记忆和 CLAUDE.md、去掉换登录方式的变量：和第 11 项主人回合同一套，
    ISOLATED_SETTINGS / claude_env；10/17 以前是 --setting-sources user）、只用我们给的 MCP（--strict-mcp-config + 0600 的临时
    配置文件，令牌不进命令行）、不给内置工具（--tools ""：没有 Bash、读写文件、上网）、只放行我们那几个
    （--allowedTools mcp__openmousse__…）、不弹问题（--disallowedTools AskUserQuestion、
    --permission-mode dontAsk、--permission-prompts none）、不加载 skills（--disable-slash-commands）；后台任务的规矩用
    --append-system-prompt 接在它自己的后面，任务从标准输入给；MCP 等连上再开始（MCP_CONNECTION_NONBLOCKING=false，Grava 的
    mail_digest.py 踩过的坑）；我们的工具一开始就全列出来（ENABLE_TOOL_SEARCH=false：工具搜索默认把 MCP 工具藏起来、要内置的 ToolSearch
    去找，--tools "" 把内置工具都拿掉了）；一次调用最多等 330 秒（MCP_TOOL_TIMEOUT：HTTP 的 MCP 它默认一次只等 60 秒，我们的命令行工具
    最长 120 秒、handoff 300 秒）。结果只认最后的 result：subtype success、is_error false、result 不空（dsh 的 successfulResult），
    别的都算没做成。用户是套餐登录（billing subscription）时，它报的 total_cost_usd 只是折算，不记成花费。
  Codex：codex exec --json --ephemeral，只读沙箱（-s read-only）、从不请求批准（approval_policy never）、不读用户的 config.toml
    （--ignore-user-config：用户自己配的 MCP、插件不带上；登录照旧用 CODEX_HOME）、关掉 shell / 看图 / 浏览器 / 电脑操作 / 画图 /
    应用 / 插件 / 再派子 Agent（只剩我们的 MCP）；我们的 MCP 经 -c 配，令牌放环境变量（bearer_token_env_var）。结果 = 最后一条
    agent_message，有 turn.completed、没有 turn.failed 才算做完。**还没真跑过**（10/16 只用假的 codex 测过事件的样子），第一次用先
    跑个小任务核对。
  Codex 用用户的 ChatGPT 套餐（第 11b 项）：这台电脑上装了 codex、它自己没登录，而 ChatGPT 套餐连着（engine/chatgpt_plan.py）、用户同意过
    后台用：照官方 developers.openai.com/siwc 的 codex-app-server 那一页，-c model_provider 配一个 Responses 的 provider（base_url =
    https://api.openai.com/v1、env_key = ACCESS_TOKEN、wire_api responses、requires_openai_auth false、supports_websockets false），
    访问令牌经环境变量 ACCESS_TOKEN 交给它（不进命令行、不进文件）。一次性的任务：起它之前先保证令牌够用到任务时限（不够就先刷新）；
    跑到一半令牌过期就算没做成（官方说的「刷新后重启 app-server、thread/resume 接着做」要换成 app-server 才做得到，还没做）。
    它自己登录过的照旧用它自己的登录。
  到点、点了停、关服务：SIGTERM 整个进程组，3 秒后 SIGKILL。进程号和工作目录记在台账里：服务被杀掉、重启以后那个进程还在、而且
    工作目录对得上，就停掉它（reap）。
参考：dsh（deepseek-ai/deepseek-harness 1517a4db）的 packages/subagent/subagent-claude-code/src/run.ts（claudeQueryOptions：
persistSession false、AskUserQuestion 禁掉、permissionMode dontAsk、清掉凭证的环境；successfulResult）、subagent-codex/README.md
（approvalPolicy never、一次性）、packages/subprocess/subprocess/src/index.ts（SENSITIVE_ENV_PATTERN）。只借设计，没拿代码。
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from config import raw, settings

NAMES = ("claude-code", "codex")
SENSITIVE = re.compile(r"KEY|PASSWORD|SECRET|TOKEN", re.I)   # dsh SENSITIVE_ENV_PATTERN
DROP = {"MCP_CONNECTION_NONBLOCKING"}
# 服务要是在一个 Claude Code 会话里起的（开发时常这样），环境里带着那个会话的一串 CLAUDE*（CLAUDECODE、CLAUDE_CODE_MESSAGING_SOCKET、
# CLAUDE_CODE_HOST_SESSION_ID、CLAUDE_CODE_SDK_HAS_HOST_AUTH_REFRESH、CLAUDE_EFFORT……，版本一新就多几个）：一概不给，只留登录在哪
# （CLAUDE_CONFIG_DIR）和用户自己关掉的上报（CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC）。10/17 服务器冒烟时看到漏进了子进程
DROP_PREFIX = ("MOUSSE_", "CLAUDE")
KEEP = {"CLAUDE_CONFIG_DIR", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"}
# 不碰用户自己的 Claude Code 设置（第 11 项 claude_brain 和第 5 项外援同一套，OpenClaw 受限跑法的那一套）：--setting-sources "" 一个设置
# 都不读，--settings 再关一遍 hooks、插件、自动记忆、CLAUDE.md；环境里关掉自动记忆和 CLAUDE.md、不连用户在 claude.ai 上的连接器；
# 换它登录方式、计费去向的变量去掉（它只用自己存的登录）
ISOLATED_SETTINGS = json.dumps({"disableAllHooks": True, "enabledPlugins": {}, "autoMemoryEnabled": False,
                                "claudeMdExcludes": ["**/CLAUDE.md", "**/CLAUDE.local.md", "**/.claude/rules/**"]})
ISOLATED_ENV = {
    "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",       # 自动记忆：不往用户的 ~/.claude 里记我们的聊天，也不读
    "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1",        # 一个 CLAUDE.md 都不读（用户的、上层目录的）
    "ENABLE_CLAUDEAI_MCP_SERVERS": "false",       # 不连用户在 claude.ai 上的连接器
}
UNSET = ("ANTHROPIC_BASE_URL", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
         "CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST", "CLAUDE_CODE_SIMPLE", "CLAUDE_CODE_SAFE_MODE")
MCP_PREFIX = "mcp__openmousse__"
CHECK_EVERY = 600.0
STARTUP_GRACE = 90.0           # 服务刚起来：第一次在后台查登录（最多两条命令各 30 秒）还没查完时，这么多秒内先当它在（checking）
KILL_GRACE = 3.0
TOOL_TIMEOUT_MS = "330000"     # 一次 MCP 调用最多等多久（毫秒，见文件开头）
SUBSCRIPTION_AUTH = {"claude.ai", "oauth_token", "chatgpt"}  # 这几种登录花的是套餐（别的按量记在用户自己的账上）
LINE_LIMIT = 8 * 1024 * 1024   # stream-json 一行可能很长（工具结果在里面）
ERR_KEEP = 8000
CODEX_OFF = ("shell_tool", "unified_exec", "view_image", "multi_agent", "browser_use", "browser_use_external", "browser_use_full_cdp_access",
             "computer_use", "image_generation", "in_app_browser", "apps", "plugins", "remote_plugin", "skill_search",
             "skill_mcp_dependency_install", "tool_suggest", "memories", "goals", "sleep_tool")
PLAN_PROVIDER = "openmousse_chatgpt_plan"   # Codex 用 ChatGPT 套餐时的 provider 名（只在它这一个进程里）
PLAN_MARGIN = 120.0                          # 交给 Codex 的令牌至少还能用「任务时限 + 这么多秒」
_found: dict[str, dict] = {}
_codex_bin: str | None = None                # 装了 codex 但它自己没登录（能用 ChatGPT 套餐跑，plan_codex）
_checked_at = 0.0
_warm = False
_warm_at = 0.0
_checking = False
_guard = threading.Lock()


# —— 设置 ————————————————————————————————————————————————————————————————————

def _runners_cfg() -> dict:
    t = raw().get("tasks")
    r = t.get("runners") if isinstance(t, dict) else None
    return r if isinstance(r, dict) else {}


def cfg(name: str) -> dict:
    v = _runners_cfg().get(name)
    if v is False:
        return {"enabled": False}
    return v if isinstance(v, dict) else {}


def enabled(name: str) -> bool:
    return cfg(name).get("enabled", True) is not False


def default_model(name: str) -> str | None:
    m = str(cfg(name).get("model") or "").strip()
    return m or None


def level(name: str) -> str:
    """外援拿我们的工具的上限：默认只读；tasks.runners.<名>.level = "undo" 才放撤得回的写。"""
    return "undo" if str(cfg(name).get("level") or "").strip().lower() == "undo" else "read"


def max_running() -> int:
    """外援同时最多几个（一个就是一个 node / codex 进程，几百 MB）：tasks.max_external，默认 1。"""
    t = raw().get("tasks")
    try:
        return max(1, int((t or {}).get("max_external") or 1)) if isinstance(t, dict) else 1
    except (TypeError, ValueError):
        return 1


def bin_of(name: str) -> str | None:
    b = str(cfg(name).get("bin") or "").strip()
    if b:
        p = Path(b).expanduser()
        return str(p) if p.is_file() and os.access(p, os.X_OK) else None
    return shutil.which("claude" if name == "claude-code" else "codex")


def child_env() -> dict[str, str]:
    """起外援用的环境：这个服务的环境去掉像密钥的、我们自己的、上一层 Claude Code 会话留下的（它只能用它自己存的登录）。"""
    return {k: v for k, v in os.environ.items()
            if not SENSITIVE.search(k) and k not in DROP and (k in KEEP or not k.startswith(DROP_PREFIX))}


def claude_env() -> dict[str, str]:
    """起 Claude Code（主人回合和外援都是）：child_env 再去掉换登录方式、计费去向的（UNSET），加上隔离的那几个（ISOLATED_ENV）。"""
    return {**{k: v for k, v in child_env().items() if k not in UNSET}, **ISOLATED_ENV}


# —— 有没有 ——————————————————————————————————————————————————————————————————————

def billing_of(auth: str) -> str:
    """登录方式 → 花的是什么：subscription（套餐）/ api_key（按量，记在用户自己的账上）/ third_party（Bedrock 这类）/ 原样。"""
    a = (auth or "").strip().lower()
    if a in SUBSCRIPTION_AUTH:
        return "subscription"
    if a in ("api_key", "api_key_helper", "api key"):
        return "api_key"
    return a or "unknown"


def detect(name: str) -> dict | None:
    """查一个外援（慢：起它自己的命令查登录；只在后台线程里调）。→ {"bin", "auth", "billing"[, "plan"]} 或 None。
    只看登录了没有、登录的是套餐还是密钥（它自己报的），邮箱、组织这些一概不读。"""
    exe = bin_of(name)
    if not exe:
        return None
    cmd = [exe, "auth", "status", "--json"] if name == "claude-code" else [exe, "login", "status"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30, env=child_env(), cwd=tempfile.gettempdir(),  # noqa: S603
                           stdin=subprocess.DEVNULL, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if name == "claude-code":
        try:
            d = json.loads(r.stdout or "{}")
        except ValueError:
            return None
        if not isinstance(d, dict) or d.get("loggedIn") is not True:
            return None
        auth = str(d.get("authMethod") or "")
        plan = d.get("subscriptionType")
        return {"bin": exe, "auth": auth, "billing": billing_of(auth),
                **({"plan": plan} if isinstance(plan, str) and re.fullmatch(r"[\w .-]{1,40}", plan) else {})}
    if r.returncode != 0:
        return None
    auth = "chatgpt" if "chatgpt" in (r.stdout + r.stderr).lower() else "api key"
    return {"bin": exe, "auth": auth, "billing": billing_of(auth)}


def refresh() -> dict[str, dict]:
    """现在查一遍（同步）。"""
    global _found, _checked_at, _codex_bin
    found: dict[str, dict] = {}
    codex_bin = None
    if importlib.util.find_spec("mcp") is not None:
        for n in NAMES:
            if enabled(n) and (info := detect(n)):
                found[n] = info
        if "codex" not in found and enabled("codex"):
            codex_bin = bin_of("codex")
    _found, _checked_at, _codex_bin = found, time.monotonic(), codex_bin
    return {**found, **({"codex": p} if "codex" not in found and (p := plan_codex()) else {})}


def plan_codex() -> dict | None:
    """codex 装了、它自己没登录，而用户的 ChatGPT 套餐连着、同意过后台用（外援都是后台任务）：用套餐的令牌跑它（见文件开头）。"""
    if not _codex_bin or not enabled("codex"):
        return None
    from . import chatgpt_plan
    if not chatgpt_plan.usable() or not chatgpt_plan.background_allowed():
        return None
    return {"bin": _codex_bin, "auth": "chatgpt-plan", "billing": "subscription", "chatgpt_plan": True}


def _refresh_bg() -> None:
    global _checking
    with _guard:
        if _checking:
            return
        _checking = True

    def go() -> None:
        global _checking
        try:
            refresh()
        finally:
            with _guard:
                _checking = False
    threading.Thread(target=go, name="engine-runner-check", daemon=True).start()


def warm() -> None:
    """服务启动时（main.py，claw 选 builtin）：在后台查一次，之后 available() 每 10 分钟再查。"""
    global _warm, _warm_at
    _warm, _warm_at = True, time.monotonic()
    _refresh_bg()


def checking(name: str) -> bool:
    """服务刚起来，第一次在后台查还没查完（warm 以后最多 STARTUP_GRACE 秒），而这台电脑上有这个命令、没关掉：调用方先当它在
    （models.subscription_ready：主对话选的是 claude-code/… 时，app 别在这几秒里闪一下「没有模型」的引导）。查完了照查到的算。"""
    return (_warm and _checked_at == 0.0 and time.monotonic() - _warm_at < STARTUP_GRACE and enabled(name)
            and bin_of(name) is not None)


def available() -> dict[str, dict]:
    """查到的外援（不等：过了 10 分钟就在后台再查，这次先用上次的）。codex 自己没登录、能用 ChatGPT 套餐跑的也算（plan_codex）。"""
    if _warm and time.monotonic() - _checked_at > CHECK_EVERY:
        _refresh_bg()
    found = dict(_found)
    if "codex" not in found and (p := plan_codex()) is not None:
        found["codex"] = p
    return found


def offered() -> list[str]:
    """能用的外援。Claude Code 也不读用户的设置了（--setting-sources ""，和主人回合一样），写在设置里的登录（apiKeyHelper 这类）
    用不上：只有它自己存的登录才算（own_login）。"""
    found = available()
    return [n for n in NAMES if n in found and enabled(n) and (n != "claude-code" or own_login(found[n]))]


def allowed(tools: list) -> list[str]:
    """外援能经 /mcp 用哪些：子 Agent 的工具去掉 tasks（一次性的外援收不到子任务的结果）。第 11 项起 skill、记忆检索也给
    （engine/mcp_engine.py 给的是内核的那张工具表）。"""
    return [t.name for t in tools if t.name != "tasks"]


def is_subscription(info: dict | None) -> bool:
    """检测结果是套餐登录吗（它报的 total_cost_usd 只是折算，不记成花费）。"""
    return bool(info) and (info.get("billing") or billing_of(str(info.get("auth") or ""))) == "subscription"


def own_login(info: dict | None) -> bool:
    """跑主人回合（engine/claude_brain.py）用得上这个登录吗：那边不读用户的设置（--setting-sources ""），写在设置里的登录（apiKeyHelper、
    Bedrock 这类）用不上，环境变量里的令牌我们本来就去掉了；只认 Claude Code 自己存的登录：claude.ai 套餐、Console 登录的 API 密钥
    （老版本不报 authMethod 的也算）。"""
    return bool(info) and str(info.get("auth") or "") in ("claude.ai", "api_key", "")


# —— 跑 ——————————————————————————————————————————————————————————————————————————

def mcp_url() -> str:
    host = settings.host
    if host in ("", "0.0.0.0", "::"):
        host = "127.0.0.1"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"http://{host}:{settings.port}/mcp"


def task_dir(tid: str) -> Path:
    base = settings.data_dir / "tasks"
    base.mkdir(parents=True, exist_ok=True)
    os.chmod(base, 0o700)
    d = base / tid
    d.mkdir(mode=0o700, exist_ok=True)
    return d


def _private_file(path: Path, text: str) -> Path:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf8") as f:
        f.write(text)
    return path


def claude_cmd(exe: str, conf: Path, allow: list[str], system: str, model: str | None) -> list[str]:
    cmd = [exe, "-p", "--output-format", "stream-json", "--verbose", "--no-session-persistence", "--setting-sources", "",
           "--settings", ISOLATED_SETTINGS,
           "--strict-mcp-config", "--mcp-config", str(conf), "--tools", "", "--permission-mode", "dontAsk", "--permission-prompts", "none",
           "--disable-slash-commands", "--disallowedTools", "AskUserQuestion", "--append-system-prompt", system]
    if allow:
        cmd += ["--allowedTools", ",".join(MCP_PREFIX + n for n in allow)]
    if model:
        cmd += ["--model", model]
    return cmd


def codex_cmd(exe: str, work: Path, url: str, model: str | None, plan: bool = False) -> list[str]:
    cmd = [exe, "exec", "--json", "--ephemeral", "--skip-git-repo-check", "--ignore-user-config", "-s", "read-only", "-C", str(work),
           "-c", 'approval_policy="never"', "-c", f'mcp_servers.openmousse.url="{url}"',
           "-c", 'mcp_servers.openmousse.bearer_token_env_var="MOUSSE_TASK_MCP_TOKEN"']
    if plan:  # 用户的 ChatGPT 套餐（官方 codex-app-server 那一页的 provider 设置；令牌在环境变量 ACCESS_TOKEN 里）
        from . import chatgpt_plan
        p = f"model_providers.{PLAN_PROVIDER}"
        cmd += ["-c", f'model_provider="{PLAN_PROVIDER}"', "-c", f'{p}.name="ChatGPT plan"', "-c", f'{p}.base_url="{chatgpt_plan.API}"',
                "-c", f'{p}.env_key="ACCESS_TOKEN"', "-c", f'{p}.wire_api="responses"', "-c", f"{p}.requires_openai_auth=false",
                "-c", f"{p}.supports_websockets=false"]
    for f in CODEX_OFF:  # 用 -c 而不用 --disable：版本里没有的名字照样不报错
        cmd += ["-c", f"features.{f}=false"]
    if model:
        cmd += ["-m", model]
    return [*cmd, "-"]  # 说明从标准输入给


class ClaudeStream:
    """claude -p --output-format stream-json 的一行行事件。"""

    def __init__(self, subscription: bool) -> None:
        self.result: dict | None = None
        self.text = ""
        self.connected: bool | None = None
        self.served: str | None = None
        self.subscription = subscription

    def feed(self, line: bytes) -> list[tuple[str, Any]]:
        try:
            e = json.loads(line)
        except ValueError:
            return []
        if not isinstance(e, dict):
            return []
        t, steps = e.get("type"), []
        if t == "system" and e.get("subtype") == "init":
            self.served = e.get("model")
            servers = e.get("mcp_servers") or []
            self.connected = any(isinstance(s, dict) and s.get("name") == "openmousse" and s.get("status") == "connected" for s in servers)
        elif t == "assistant":
            for b in (e.get("message") or {}).get("content") or []:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "tool_use":
                    name = str(b.get("name") or "")
                    steps.append((name[len(MCP_PREFIX):] if name.startswith(MCP_PREFIX) else name, b.get("input") or {}))
                elif b.get("type") == "text" and b.get("text"):
                    self.text = str(b["text"])
        elif t == "result":
            self.result = e
        return steps

    def outcome(self, rc: int | None, reason: str | None, err: str):
        from .tasks import Outcome
        r = self.result or {}
        u = r.get("usage") if isinstance(r.get("usage"), dict) else {}
        cost = r.get("total_cost_usd")
        usage = {"in": int(u.get("input_tokens") or 0), "out": int(u.get("output_tokens") or 0),
                 "cache_read": int(u.get("cache_read_input_tokens") or 0), "cache_write": int(u.get("cache_creation_input_tokens") or 0),
                 "cost": None if self.subscription else (float(cost) if isinstance(cost, (int, float)) else None)}
        extra = {"mcp_connected": self.connected, "turns": r.get("num_turns"),
                 **({"notional_cost_usd": cost} if self.subscription and isinstance(cost, (int, float)) else {})}
        if reason:
            st = {"timeout": "timed_out", "shutdown": "interrupted"}.get(reason, "cancelled")
            return Outcome(st, text=self.text, error={"timed_out": "hit the time limit", "interrupted": "the server stopped"}.get(st, "stopped"),
                           usage=usage, served=self.served, extra=extra)
        if r.get("subtype") == "success" and r.get("is_error") is False and str(r.get("result") or "").strip():
            return Outcome("done", text=str(r["result"]), usage=usage, served=self.served, extra=extra)
        why = "Claude Code: " + str(r.get("subtype") or "no result") + (f"; exit code {rc}" if rc else "")
        if self.connected is False:
            why += f"; {settings.app_name}'s tools didn't connect"
        if not r and err.strip():
            why += "; " + err.strip().splitlines()[-1][:200]
        return Outcome("failed", text=self.text, error=why, usage=usage, served=self.served, extra=extra)


class CodexStream:
    """codex exec --json 的一行行事件（thread.started / turn.* / item.* / error）。"""

    def __init__(self) -> None:
        self.text = ""
        self.completed = False
        self.failed: str | None = None
        self.error: str | None = None
        self.usage: dict = {}

    def feed(self, line: bytes) -> list[tuple[str, Any]]:
        try:
            e = json.loads(line)
        except ValueError:
            return []
        if not isinstance(e, dict):
            return []
        t, steps = e.get("type"), []
        if t in ("item.started", "item.completed"):
            it = e.get("item") if isinstance(e.get("item"), dict) else {}
            kind = it.get("type") or it.get("item_type")
            if t == "item.completed" and kind == "agent_message":
                self.text = str(it.get("text") or "")
            elif t == "item.started" and kind == "mcp_tool_call":
                steps.append((str(it.get("tool") or ""), it.get("arguments") or {}))
            elif t == "item.started" and kind in ("command_execution", "web_search", "file_change"):
                steps.append(({"command_execution": "exec", "file_change": "edit"}.get(kind, kind), {}))
        elif t == "turn.completed":
            self.completed = True
            self.usage = e.get("usage") if isinstance(e.get("usage"), dict) else {}
        elif t == "turn.failed":
            self.failed = str((e.get("error") or {}).get("message") or "the turn failed") if isinstance(e.get("error"), dict) else "the turn failed"
        elif t == "error":
            self.error = str(e.get("message") or "error")
        return steps

    def outcome(self, rc: int | None, reason: str | None, err: str):
        from .tasks import Outcome
        u = self.usage
        usage = {"in": int(u.get("input_tokens") or 0), "out": int(u.get("output_tokens") or 0), "cache_read": int(u.get("cached_input_tokens") or 0),
                 "cache_write": 0, "cost": None}
        if reason:
            st = {"timeout": "timed_out", "shutdown": "interrupted"}.get(reason, "cancelled")
            return Outcome(st, text=self.text, error={"timed_out": "hit the time limit", "interrupted": "the server stopped"}.get(st, "stopped"),
                           usage=usage)
        if self.completed and not self.failed and self.text.strip():
            return Outcome("done", text=self.text, usage=usage)
        why = "Codex: " + (self.failed or self.error or "no answer") + (f"; exit code {rc}" if rc else "")
        if not (self.failed or self.error) and err.strip():
            why += "; " + err.strip().splitlines()[-1][:200]
        return Outcome("failed", text=self.text, error=why, usage=usage)


def kill(proc, hard: bool = False) -> None:
    """停整个进程组：先 SIGTERM，3 秒后还在就 SIGKILL。"""
    if proc is None or proc.returncode is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGKILL if hard else signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    if not hard:
        try:
            asyncio.get_running_loop().call_later(KILL_GRACE, kill, proc, True)
        except RuntimeError:
            pass


def reap(row: dict) -> None:
    """服务重启时：上次没跑完的外援进程要是还在（进程号还在、工作目录对得上：别误杀别人的进程），停掉。"""
    d = row.get("data") or {}
    pid, cwd = d.get("pid"), d.get("cwd")
    if not pid or not cwd or row.get("runner") in (None, "engine"):
        return
    try:
        if os.path.realpath(f"/proc/{int(pid)}/cwd") != os.path.realpath(cwd):
            return
        os.killpg(int(pid), signal.SIGKILL)
    except (OSError, ValueError):
        pass


async def run(row: dict, live, *, prompt: str, system: str, tools: list, timeout: float, on_step) -> Any:
    """跑一次外援 → tasks.Outcome。tools = 经 /mcp 给它的那几个（内核的 Tool）。live.proc 是它的进程（停、到点都经它）。"""
    from . import ledger
    from .tasks import Outcome, mint, revoke
    name = row["runner"]
    info = available().get(name) or (await asyncio.to_thread(refresh)).get(name)
    if not info:
        return Outcome("failed", error=f"{name} isn't installed or logged in on this computer")
    work = await asyncio.to_thread(task_dir, row["id"])
    allow = [t.name for t in tools]
    tok = mint(row, tools, row.get("level") or "read")
    conf: Path | None = None
    try:
        env = claude_env() if name == "claude-code" else child_env()
        if name == "claude-code":
            conf = _private_file(work.parent / f".mcp-{row['id']}.json", json.dumps(
                {"mcpServers": {"openmousse": {"type": "http", "url": mcp_url(), "headers": {"Authorization": f"Bearer {tok}"}}}}))
            cmd = claude_cmd(info["bin"], conf, allow, system, row.get("model"))
            stdin, parser = prompt, ClaudeStream(subscription=is_subscription(info))
            env.update({"MCP_CONNECTION_NONBLOCKING": "false", "ENABLE_TOOL_SEARCH": "false", "MCP_TOOL_TIMEOUT": TOOL_TIMEOUT_MS})
        else:
            plan = bool(info.get("chatgpt_plan"))
            cmd = codex_cmd(info["bin"], work, mcp_url(), row.get("model"), plan=plan)
            stdin, parser = f"{system}\n\n{prompt}", CodexStream()
            env["MOUSSE_TASK_MCP_TOKEN"] = tok
            if plan:  # 用户的 ChatGPT 套餐：令牌够用到任务时限（不够先刷新），经环境变量交给它
                from . import chatgpt_plan
                from .errors import EngineError
                try:
                    env["ACCESS_TOKEN"] = await chatgpt_plan.access_token(min_valid=min(timeout + PLAN_MARGIN, 3000.0))
                except EngineError as e:
                    return Outcome("failed", error=f"Codex on the ChatGPT plan: {e}")
        proc = await asyncio.create_subprocess_exec(*cmd, cwd=str(work), env=env, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.PIPE, start_new_session=True, limit=LINE_LIMIT)
        live.proc = proc
        ledger.merge_data(row["id"], pid=proc.pid, cwd=str(work))
        err = bytearray()

        async def feed_in() -> None:
            try:
                assert proc.stdin is not None
                proc.stdin.write(stdin.encode("utf8"))
                await proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                if proc.stdin is not None:
                    proc.stdin.close()

        async def drain_err() -> None:
            assert proc.stderr is not None
            while chunk := await proc.stderr.read(4096):
                err.extend(chunk)
                if len(err) > ERR_KEEP:
                    del err[:-ERR_KEEP]

        async def read_out() -> None:
            assert proc.stdout is not None
            async for line in proc.stdout:
                for step in parser.feed(line):
                    try:
                        on_step(*step)
                    except Exception:  # noqa: BLE001 — 记步骤的毛病不拖累任务
                        pass
        side = [asyncio.ensure_future(feed_in()), asyncio.ensure_future(drain_err())]
        try:
            await asyncio.wait_for(read_out(), timeout)
        except asyncio.TimeoutError:
            live.reason = live.reason or "timeout"
            kill(proc)
        except asyncio.CancelledError:
            kill(proc, hard=True)
            raise
        try:
            await asyncio.wait_for(proc.wait(), KILL_GRACE + 5)
        except asyncio.TimeoutError:
            kill(proc, hard=True)
            await proc.wait()
        for t in side:
            t.cancel()
        return parser.outcome(proc.returncode, live.reason, err.decode("utf8", "replace"))
    finally:
        revoke(tok)
        live.proc = None
        if conf is not None:
            try:
                conf.unlink()
            except OSError:
                pass
