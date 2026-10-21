"""mousse security audit：安全体检（第 7 批，4.21，2026-10-19）。照 OpenClaw 的 docs/cli/security.md、gateway/security/audit-checks.md（冷检查：
只看配置和文件，不起服务、不联网；每一条有 checkId、轻重、为什么危险、改哪儿、--fix 能不能修）。只借做法。

  mousse security audit [--json] [--fix]

查什么（每条写清危险在哪、怎么改）：
  文件权限    secrets.env、server.json、data_dir、ChatGPT 套餐的凭据、连接器的令牌（data_dir/apps）、Doorman 的密钥（sentinel）、设备私钥
              （gateway-device.json）、朋友认你的钥匙（social/identity.json）、世界树的配置、备份包：别的用户读得到 = !（谁都读得到、写得了 = ✗）
  密钥        server.json 里直接写着的密钥（provider 的 key、claw 的 token、MCP 服务 env 里不是 ${变量} 的……）
  令牌        太短的、两个名字同一把的（程序的令牌和人的令牌一样 = ✗：拿着程序令牌的就能走 /api）、一把都没有
  监听        auth.trust_loopback 开着；监听地址不是 loopback 也不是 Tailscale（0.0.0.0 / :: = ✗）
  公网        有 tailscale 命令才查：Funnel 把 /api（或整个服务）开到了公网 = ✗，开了安装器那几条（/t /m /s /f）以外的 = !
  对外接口    engine.api 开着：调用方能当场点头（approve 没关）、没有 api 令牌
  手          engine.host_exec 里 security: full 的 Agent；engine.tools.levels 把本来要点头的改成了 auto；MCP 服务里写工具设成 auto
  套餐        ChatGPT 套餐的「后台也能花」开着
  沙箱        engine.sandbox.network 不是 Doorman 的网络也不是 none（直接上网；host = ✗）
  配对码      server.json 里没清的过期配对码
--fix 只做安全的：收紧权限（chmod 600 / 700）、清过期的配对码（先备份 server.json）；别的只给命令。能用的现成判断照用
（engine/host_exec.policy、engine/tools.POLICY、apps.kind_of、appdata 的安全页同一套说法）。
退出码：有 ✗ = 1，没有 = 0。
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from i18n import L

from . import common as C

SEVERITIES = ("critical", "warn", "info")
MARK = {"critical": "✗", "warn": "!", "info": "·"}
PUBLIC_PATHS = ("/t", "/m", "/s", "/f")  # 安装器只开这几条到公网（packs/core/setup.py 的 PUBLIC_PATHS）
SHORT_TOKEN = 20
PROGRAM_TOKENS = ("mcp", "api", "sentinel")


@dataclass
class Finding:
    id: str
    severity: str           # critical | warn | info
    title: str
    why: str                # 危险在哪
    fix: str = ""           # 怎么改
    fixable: bool = False   # --fix 能替你做
    repair: Callable[[], str] | None = field(default=None, repr=False, compare=False)
    fixed: bool = False
    note: str = ""

    def to_json(self) -> dict:
        return {"id": self.id, "severity": self.severity, "title": self.title, "why": self.why, "fix": self.fix, "fixable": self.fixable,
                "fixed": self.fixed, "note": self.note}


# —— 文件权限 ——————————————————————————————————————————————————————————————————————————

def _chmod(p: Path, mode: int) -> Callable[[], str]:
    def go() -> str:
        os.chmod(p, mode)
        return f"chmod {mode:o} {C.tilde(p)}"
    return go


def perm_targets(cfg: dict) -> list[tuple[str, str, Path, str]]:
    """(id, 名字, 路径, 里面是什么)。"""
    d = C.data_dir(cfg)
    return [
        ("fs.secrets", "secrets.env", C.secrets_path(cfg), L("API 密钥", "API keys")),
        ("fs.config", "server.json", C.config_path(), L("接入令牌和全部设置", "access tokens and every setting")),
        ("fs.data_dir", L("数据目录", "data folder"), d, L("对话、记忆、上传的文件", "chats, memories, uploads")),
        ("fs.chatgpt", L("ChatGPT 套餐的凭据", "ChatGPT plan credentials"), C.chatgpt_dir(cfg), L("ChatGPT 套餐的令牌", "the ChatGPT plan's tokens")),
        ("fs.grok", L("Grok 订阅的凭据", "Grok subscription credentials"), C.grok_dir(cfg), L("Grok 订阅的令牌", "the Grok subscription's tokens")),
        ("fs.apps", L("连接器的令牌", "connector tokens"), d / "apps", L("第三方应用的 OAuth 令牌", "third-party OAuth tokens")),
        ("fs.sentinel", L("Doorman 的密钥", "Doorman's keys"), d / "sentinel", L("代位用的真密钥、证书的私钥", "the real keys it substitutes, its CA key")),
        ("fs.device_key", L("设备私钥", "device key"), d / "gateway-device.json", L("连 Gateway 的设备私钥", "the device key for the Gateway")),
        ("fs.identity", L("朋友认你的钥匙", "friend identity key"), d / "social" / "identity.json", L("朋友网络里认你的私钥", "your private key for friends")),
        ("fs.tree_config", L("世界树的配置", "memory tree config"), C.tree_home() / "config.json", L("各平台连世界树的令牌", "the platforms' tokens")),
    ]


def check_perms(cfg: dict) -> list[Finding]:
    out = []
    for cid, name, p, what in perm_targets(cfg):
        m = C.mode_of(p)
        if m is None:
            continue
        want = 0o700 if p.is_dir() else 0o600
        if m & 0o022:
            out.append(Finding(cid, "critical", L(f"{name} 别的用户写得了（{m:o}）", f"{name} is writable by other users ({m:o})"),
                               L(f"{C.tilde(p)} 里是{what}：别的用户能改、能换掉", f"{C.tilde(p)} holds {what}: other users can change or replace it"),
                               f"chmod {want:o} {C.tilde(p)}", True, _chmod(p, want)))
        elif m & 0o077:
            world = bool(m & 0o004)
            out.append(Finding(cid, "critical" if world and cid != "fs.data_dir" else "warn",
                               L(f"{name} 别的用户读得到（{m:o}）", f"{name} is readable by other users ({m:o})"),
                               L(f"{C.tilde(p)} 里是{what}", f"{C.tilde(p)} holds {what}"), f"chmod {want:o} {C.tilde(p)}", True, _chmod(p, want)))
    bdir = C.state_dir() / "backups"
    loose = [p for p in (bdir.iterdir() if bdir.is_dir() else []) if p.name.endswith(".tar.gz") and (C.mode_of(p) or 0) & 0o077]
    if loose:
        def fix_all() -> str:
            for p in loose:
                os.chmod(p, 0o600)
            return L(f"{len(loose)} 个备份改成了 600", f"{len(loose)} backups set to 600")
        out.append(Finding("fs.backups", "warn", L(f"{len(loose)} 个备份别的用户读得到", f"{len(loose)} backups are readable by other users"),
                           L("备份包里有接入令牌、可能还有 API 密钥和全部数据", "backups hold your access tokens, maybe your API keys, and all your data"),
                           f"chmod 600 {C.tilde(bdir)}/*.tar.gz", True, fix_all))
    return out


# —— 密钥、令牌 ——————————————————————————————————————————————————————————————————————

def inline_secrets(cfg: dict) -> list[str]:
    """server.json 里直接写着的密钥（键名像 key / token / secret / password、值不是 ${变量}、不是变量名）→ 点路径。auth 段（接入令牌、
    配对码的哈希）本来就在这儿，不算。"""
    from .confcheck import ENV, secret_key
    found: list[str] = []

    def walk(v, path: str) -> None:
        if isinstance(v, dict):
            for k, x in v.items():
                walk(x, f"{path}.{k}" if path else str(k))
        elif isinstance(v, list):
            for i, x in enumerate(v):
                walk(x, f"{path}[{i}]")
        elif isinstance(v, str) and v.strip() and secret_key(path.rsplit(".", 1)[-1].split("[")[0]) and not v.startswith("${") \
                and not ENV.match(v) and len(v) >= 8:
            found.append(path)

    walk({k: v for k, v in cfg.items() if k != "auth"}, "")
    # MCP 服务的 env：值不是 ${变量} 的都算（{"GITHUB_TOKEN": "ghp_…"}）
    for sec in ("mcp_servers", "mcpServers"):
        for name, s in (cfg.get(sec).items() if isinstance(cfg.get(sec), dict) else []):
            env = s.get("env") if isinstance(s, dict) else None
            for k, v in (env.items() if isinstance(env, dict) else []):
                path = f"{sec}.{name}.env.{k}"
                if isinstance(v, str) and v and not re.fullmatch(r"\$\{[A-Z_][A-Z0-9_]*\}", v) and secret_key(k) and path not in found:
                    found.append(path)
    return found


def check_secrets(cfg: dict) -> list[Finding]:
    hits = inline_secrets(cfg)
    if not hits:
        return []
    return [Finding("config.inline_secrets", "warn", L(f"server.json 里直接写着 {len(hits)} 个密钥", f"{len(hits)} keys are written right in server.json"),
                    L(f"{', '.join(hits[:5])}：server.json 会进备份、被各处读，密钥容易跟着漏出去", f"{', '.join(hits[:5])}: server.json goes into backups and "
                      "is read in many places, so keys leak more easily"),
                    L("放进 secrets.env（setup_ctl.py add <服务商>，或者手写一行），server.json 里写 ${变量名} 或 key_env",
                      "move them to secrets.env (setup_ctl.py add <provider>, or add a line by hand) and write ${NAME} or key_env in server.json"))]


def check_tokens(cfg: dict) -> list[Finding]:
    a = cfg.get("auth") if isinstance(cfg.get("auth"), dict) else {}
    t = a.get("tokens") if isinstance(a.get("tokens"), dict) else {}
    tokens = {str(k): str(v) for k, v in t.items() if v}
    nodes = a.get("tailscale_nodes") or []
    out = []
    if not tokens and not nodes:
        out.append(Finding("auth.none", "info", L("没有接入令牌", "No access tokens"), L("/api 谁都进不来（安全，但 app 也连不上）", "nobody can use /api (safe, but the app can't connect either)"),
                           f"{C.PY} {C.REPO_TILDE}/server/tokens.py add phone"))
    short = sorted(k for k, v in tokens.items() if len(v) < SHORT_TOKEN)
    if short:
        out.append(Finding("auth.short_tokens", "warn", L(f"令牌太短：{', '.join(short)}", f"Tokens too short: {', '.join(short)}"),
                           L(f"不到 {SHORT_TOKEN} 个字符：好猜（tokens.py 生成的是 32 个）", f"under {SHORT_TOKEN} characters: guessable (tokens.py makes 32)"),
                           L("tokens.py remove <名字> 再 tokens.py add <名字>，换一把新的", "tokens.py remove <name>, then tokens.py add <name> for a new one")))
    by_value: dict[str, list[str]] = {}
    for k, v in tokens.items():
        by_value.setdefault(v, []).append(k)
    for names in by_value.values():
        if len(names) < 2:
            continue
        program = [n for n in names if n in PROGRAM_TOKENS or n.startswith(("mcp-", "api-"))]
        crit = bool(program) and len(program) < len(names)
        out.append(Finding("auth.reused_tokens", "critical" if crit else "warn", L(f"同一把令牌给了 {', '.join(sorted(names))}", f"One token is shared by {', '.join(sorted(names))}"),
                           L("给程序的令牌（只能走 /mcp、/v1）和给人的一样：拿着它的程序也能走 /api，替你点头、配对设备" if crit else
                             "撤一个就得一起撤，也分不清是谁在用", "a program token (meant only for /mcp or /v1) equals a person's token: the program can use "
                             "/api too, approve cards for you and pair devices" if crit else "revoking one revokes them all, and you can't tell who is using it"),
                           L("给其中几个换新的：tokens.py remove <名字> && tokens.py add <名字>", "give some of them new ones: tokens.py remove <name> && tokens.py add <name>")))
    if a.get("trust_loopback"):
        out.append(Finding("auth.trust_loopback", "warn", L("本机来的请求免令牌（auth.trust_loopback）", "Requests from this machine skip the token (auth.trust_loopback)"),
                           L("本机有反向代理（Caddy、nginx、tailscale serve）时，外面来的请求在服务器看来都是本机的：等于谁都免令牌",
                             "with a reverse proxy on this machine (Caddy, nginx, tailscale serve), outside requests look local: nobody needs a token"),
                           "mousse config set auth.trust_loopback false"))
    return out


def _tailscale_ip(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return ip in ipaddress.ip_network("100.64.0.0/10") or ip in ipaddress.ip_network("fd7a:115c:a1e0::/48")


def check_bind(cfg: dict) -> list[Finding]:
    b = cfg.get("bind") if isinstance(cfg.get("bind"), dict) else {}
    host = str(b.get("host") or "127.0.0.1").strip()
    if host in ("0.0.0.0", "::", "", "*"):
        return [Finding("net.bind_all", "critical", L(f"服务在所有网卡上听（{host}）", f"The server listens on every interface ({host})"),
                        L("同一网络里的谁都能连上来试令牌；/api 本来只该在本机或者 Tailscale 里", "anyone on the same network can connect and try tokens; "
                          "/api should only be on this machine or on Tailscale"),
                        L("bind.host 改成 127.0.0.1（配反向代理）或这台机器的 Tailscale 地址（tailscale ip -4），再重启服务",
                          "set bind.host to 127.0.0.1 (behind a reverse proxy) or this machine's Tailscale address (tailscale ip -4), then restart"))]
    if host in ("127.0.0.1", "::1", "localhost") or _tailscale_ip(host):
        return []
    return [Finding("net.bind_other", "warn", L(f"服务听的不是本机也不是 Tailscale 地址（{host}）", f"The server listens on neither loopback nor Tailscale ({host})"),
                    L("局域网里的设备都连得上来", "devices on that network can reach it"),
                    L("bind.host 改成 127.0.0.1 或 Tailscale 地址", "set bind.host to 127.0.0.1 or the Tailscale address"))]


def ts_bin() -> str | None:
    app = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"
    return shutil.which("tailscale") or (app if sys.platform == "darwin" and os.access(app, os.X_OK) else None)


def check_funnel(cfg: dict) -> list[Finding]:
    """有 tailscale 命令才查：Funnel 开到公网的路径里有没有 /api（或整个服务）。"""
    exe = ts_bin()
    if not exe:
        return []
    r = C.run([exe, "serve", "status", "--json"], timeout=20)
    try:
        st = json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else {}
    except ValueError:
        st = {}
    if not isinstance(st, dict):
        return []
    b = cfg.get("bind") if isinstance(cfg.get("bind"), dict) else {}
    port = str(b.get("port") or 8080)
    out = []
    for hostport, allowed in (st.get("AllowFunnel") or {}).items():
        if not allowed:
            continue
        handlers = ((st.get("Web") or {}).get(hostport) or {}).get("Handlers") or {}
        for path, h in handlers.items():
            target = str((h or {}).get("Proxy") or "")
            ours = bool(re.search(rf":{re.escape(port)}(/|$)", target))
            if path.rstrip("/") in PUBLIC_PATHS:
                continue
            if ours or path in ("/", "/api", "/api/"):
                out.append(Finding("net.funnel_api", "critical", L(f"Funnel 把服务器开到了公网：{hostport}{path}", f"Funnel exposes the server publicly: {hostport}{path}"),
                                   L(f"{path} → {target}：/api 这些本来只在 Tailscale 里，现在公网上谁都能来试", f"{path} → {target}: /api is meant to stay "
                                     "inside Tailscale, now anyone on the internet can try it"),
                                   f"tailscale funnel --https={hostport.rsplit(':', 1)[-1]} --set-path={path} off"))
            else:
                out.append(Finding("net.funnel_other", "warn", L(f"Funnel 开了 {hostport}{path}", f"Funnel exposes {hostport}{path}"),
                                   L(f"安装器只开 {' '.join(PUBLIC_PATHS)}；{path} → {target} 是另外开的", f"the installer only opens {' '.join(PUBLIC_PATHS)}; "
                                     f"{path} → {target} was opened separately"),
                                   L("不要了就关：", "close it if it's not needed: ") + f"tailscale funnel --https={hostport.rsplit(':', 1)[-1]} --set-path={path} off"))
    return out


# —— 内核：对外接口、手、套餐、沙箱 ——————————————————————————————————————————————————————

def _engine(cfg: dict) -> dict:
    e = cfg.get("engine")
    return e if isinstance(e, dict) else {}


def check_api(cfg: dict) -> list[Finding]:
    v = _engine(cfg).get("api")
    if v is not True and not (isinstance(v, dict) and v.get("enabled", True) is not False):
        return []
    api = v if isinstance(v, dict) else {}
    a = cfg.get("auth") if isinstance(cfg.get("auth"), dict) else {}
    t = a.get("tokens") if isinstance(a.get("tokens"), dict) else {}
    names = [str(k) for k, x in t.items() if x and (k == "api" or str(k).startswith("api-"))]
    out = []
    if not names:
        out.append(Finding("api.no_tokens", "info", L("对外接口开着，但没有 api 令牌", "The external API is on, but there are no api tokens"),
                           L("/v1 谁都用不了（安全，但开着也没用）", "nobody can use /v1 (safe, but pointless)"),
                           f"{C.PY} {C.REPO_TILDE}/server/tokens.py add api"))
    if api.get("approve", True) is not False:
        extra = [x for x, on in (("/v1/responses", api.get("responses")), ("/v1/a2a", api.get("a2a"))) if on]
        out.append(Finding("api.approve", "warn", L("对外接口的调用方能当场点头（engine.api.approve）", "External API callers can approve on the spot (engine.api.approve)"),
                           L(f"拿着 {', '.join(names) or 'api'} 令牌的程序，能对它自己会话里交的卡点头（撤不回的也算）" + (f"；还开着 {', '.join(extra)}" if extra else ""),
                             f"a program holding {', '.join(names) or 'an api'} token can approve cards from its own sessions, irreversible ones included"
                             + (f"; {', '.join(extra)} are on too" if extra else "")),
                           L("只信得过的程序才给令牌；或者 mousse config set engine.api.approve false（卡只在 app 里等你）",
                             "only give tokens to programs you trust; or mousse config set engine.api.approve false (cards wait for you in the app)")))
    return out


def check_hands(cfg: dict) -> list[Finding]:
    out = []
    e = _engine(cfg)
    hx = e.get("host_exec") if isinstance(e.get("host_exec"), dict) else {}
    for aid, v in (hx.get("agents").items() if isinstance(hx.get("agents"), dict) else []):
        if isinstance(v, dict) and str(v.get("security") or "").strip().lower() == "full":
            out.append(Finding("exec.security_full", "warn", L(f"{aid} 在服务器上跑命令不看名单（security: full）", f"{aid} runs server commands without an allowlist (security: full)"),
                               L("每条命令都直接跑，只有认出来的危险命令才先问你：别人说的话、网页里藏的指令都可能变成你服务器上的命令",
                                 "every command runs at once and only recognisably risky ones ask first: words from other people or hidden in web pages can "
                                 "become commands on your server"),
                               L(f"去掉 engine.host_exec.agents.{aid}.security（回到放行名单）：mousse config unset engine.host_exec.agents.{aid}.security",
                                 f"remove engine.host_exec.agents.{aid}.security (back to the allowlist): mousse config unset engine.host_exec.agents.{aid}.security")))
    tl = e.get("tools") if isinstance(e.get("tools"), dict) else {}
    lv = tl.get("levels") if isinstance(tl.get("levels"), dict) else {}
    loosened = [str(k) for k, v in lv.items() if str(v).strip().lower() == "auto" and default_asks(str(k).strip())]
    if loosened:
        out.append(Finding("tools.auto", "warn", L(f"本来要点头的改成了直接做：{', '.join(loosened[:6])}", f"Things that needed your OK now run on their own: {', '.join(loosened[:6])}"),
                           L("engine.tools.levels 写了 auto：撤不回的、对外的这几样不再进收件箱等你", "engine.tools.levels says auto: these irreversible or outward "
                             "actions no longer wait in your inbox"),
                           L("改回 ask，或者去掉那一项：mousse config unset engine.tools.levels.<键>", "set them back to ask, or remove them: "
                             "mousse config unset engine.tools.levels.<key>")))
    for sec in ("mcp_servers", "mcpServers"):
        for name, s in (cfg.get(sec).items() if isinstance(cfg.get(sec), dict) else []):
            tools = s.get("tools") if isinstance(s, dict) and isinstance(s.get("tools"), dict) else {}
            autos = [str(k) for k, v in tools.items() if str(v).strip().lower() == "auto" and (k == "*" or mcp_writes(str(name), str(k), cfg))]
            if autos:
                out.append(Finding("mcp.write_auto", "warn", L(f"MCP 服务 {name} 的写工具设成了 auto：{', '.join(autos[:6])}", f"MCP server {name} has write tools on auto: "
                                                                                                                       f"{', '.join(autos[:6])}"),
                                   L("这几个工具（\"*\" = 没单独写的全部）改东西也不先问你", "these tools (\"*\" = every tool not listed) change things without asking"),
                                   L(f"改成 ask：mousse config set {sec}.{name}.tools.<工具> ask", f"set them to ask: mousse config set {sec}.{name}.tools.<tool> ask")))
    return out


def default_asks(key: str) -> bool:
    """engine.tools.levels 的一个键（「工具」「工具 子命令」「工具 子命令 动作」）默认要不要点头：底下有一条是 ask 就算。不认识的工具当要点头
    （宁可多提醒）；host_exec 的 auto 不跳过名单（engine/host_exec.py），不算。"""
    if key in ("host_exec",):
        return False
    try:
        from engine import tools
    except Exception:  # noqa: BLE001 — 依赖缺了：当要点头
        return True
    parts = key.split()
    node = tools.POLICY.get(parts[0]) if parts else None
    if node is None:
        return True
    for p in parts[1:]:
        if not isinstance(node, dict):
            break
        node = node.get(p, tools.UNKNOWN)

    def any_ask(n) -> bool:
        if isinstance(n, dict):
            return any(any_ask(x) for x in n.values())
        return getattr(n, "level", "ask") == tools.ASK
    return any_ask(node)


def mcp_writes(server: str, tool: str, cfg: dict) -> bool:
    """MCP 服务的这个工具会改东西吗：工具表缓存里有它的 annotations 就照 apps.kind_of（readOnlyHint 说了算，没写看名字），没有就看名字。"""
    ann: dict = {}
    try:
        cache = json.loads((C.data_dir(cfg) / "mcp" / "tools.json").read_text(encoding="utf8"))
        for t in ((cache.get(server) or {}).get("tools") or []):
            if isinstance(t, dict) and t.get("name") == tool:
                ann = t.get("annotations") if isinstance(t.get("annotations"), dict) else {}
    except (OSError, ValueError, AttributeError):
        pass
    try:
        import apps
        return apps.kind_of(tool, ann, set()) == "write"
    except Exception:  # noqa: BLE001
        return not re.match(r"^(get|list|search|read|find|fetch|query|describe|show)[_-]", tool)


def check_plan(cfg: dict) -> list[Finding]:
    try:
        from engine import chatgpt_plan
        on = chatgpt_plan.background_allowed() and chatgpt_plan.enabled()
    except Exception:  # noqa: BLE001
        on = False
    out = [] if not on else [
        Finding("plan.chatgpt_background", "warn", L("ChatGPT 套餐的「后台也能花」开着", "The ChatGPT plan may be used in the background"),
                L("定时任务、心跳、后台任务没人在看的时候也花你的套餐额度", "scheduled jobs, heartbeats and background tasks spend your plan while nobody watches"),
                L("不想要就在 app 的设置里关掉（ChatGPT 套餐 → 后台）", "turn it off in the app's settings (ChatGPT plan → background) if you don't want that"))]
    try:  # 10/20：Grok 订阅（engine/grok_plan.py）的后台同意单独记
        from engine import grok_plan
        grok_on = grok_plan.background_allowed() and grok_plan.enabled()
    except Exception:  # noqa: BLE001
        grok_on = False
    if grok_on:
        out.append(Finding("plan.grok_background", "warn", L("Grok 订阅的「后台也能花」开着", "The Grok subscription may be used in the background"),
                           L("定时任务、心跳、后台任务没人在看的时候也花你的订阅额度", "scheduled jobs, heartbeats and background tasks spend your subscription while nobody watches"),
                           L("不想要就关掉：python3 setup_ctl.py grok background off", "turn it off if you don't want that: python3 setup_ctl.py grok background off")))
    return out


def check_sandbox(cfg: dict) -> list[Finding]:
    s = _engine(cfg).get("sandbox")
    net = str(s.get("network") or "").strip() if isinstance(s, dict) else ""
    if not net or net in ("none", "mousse-errand"):
        return []
    return [Finding("sandbox.network", "critical" if net == "host" else "warn", L(f"沙箱直接上网（network: {net}）", f"The sandbox reaches the internet directly (network: {net})"),
                    L("不经 Doorman：提交、发送、登录、上传不先问你，真的密钥也可能被带出去" + ("；host 网络还看得到这台机器上的服务" if net == "host" else ""),
                      "it bypasses Doorman: submitting, sending, signing in and uploading don't ask you first, and real keys can leave"
                      + ("; the host network also reaches this machine's services" if net == "host" else "")),
                    L("去掉 engine.sandbox.network（回到 Doorman 的 mousse-errand），或者写 none 断网", "remove engine.sandbox.network (back to Doorman's mousse-errand), "
                      "or set it to none for no network"))]


def check_pairing(cfg: dict) -> list[Finding]:
    from . import doctor
    n = doctor.expired_pairing(cfg)
    if not n:
        return []
    return [Finding("auth.expired_pairing", "info", L(f"{n} 个过期的配对码还留在 server.json 里", f"{n} expired pairing codes are still in server.json"),
                    L("用不了了（只存哈希），留着只是乱", "they can't be used (only hashes are kept); they're just clutter"),
                    L("mousse security audit --fix（先备份 server.json）", "mousse security audit --fix (backs up server.json first)"), True, doctor.drop_expired_pairing)]


CHECKS = (check_perms, check_secrets, check_tokens, check_bind, check_funnel, check_api, check_hands, check_plan, check_sandbox, check_pairing)


def run_audit() -> tuple[list[Finding], str | None]:
    """→ (找到的, server.json 的问题)。server.json 读不了：只查文件权限。"""
    cfg, problem = C.load_config()
    if problem in ("broken", "unreadable"):
        return check_perms({}), problem
    out: list[Finding] = []
    for fn in CHECKS:
        try:
            out += fn(cfg or {})
        except Exception as e:  # noqa: BLE001 — 一项出错不拦着别的
            out.append(Finding(f"audit.{fn.__name__}", "info", fn.__name__, L(f"这一项没查成：{type(e).__name__}", f"this check failed to run: {type(e).__name__}")))
    out.sort(key=lambda f: SEVERITIES.index(f.severity))
    return out, problem


def main(a) -> int:
    found, problem = run_audit()
    if a.fix:
        for f in found:
            if f.fixable and f.repair:
                try:
                    f.note, f.fixed = f.repair(), True
                except Exception as e:  # noqa: BLE001
                    f.note = L(f"没修成：{C.scrub(str(e))[:200]}", f"couldn't fix it: {C.scrub(str(e))[:200]}")
    left = [f for f in found if not f.fixed]
    crit = sum(1 for f in left if f.severity == "critical")
    if a.json:
        print(json.dumps({"ok": crit == 0, "config": problem, "findings": [f.to_json() for f in found],
                          "summary": {s: sum(1 for f in left if f.severity == s) for s in SEVERITIES} | {"fixed": sum(1 for f in found if f.fixed)}},
                         ensure_ascii=False, indent=2))
        return 1 if crit else 0
    print(L("OpenMousse 安全体检（只看配置和文件）", "OpenMousse security audit (config and files only)"))
    if problem in ("broken", "unreadable"):
        print(L("  server.json 读不了：只查了文件权限（mousse doctor 看为什么）", "  server.json can't be read: only file permissions were checked (see mousse doctor)"))
    if not found:
        print(L("  ✓ 没发现问题", "  ✓ Nothing found"))
    for f in found:
        mark = "✓" if f.fixed else MARK[f.severity]
        print(f"  {mark} [{f.id}] {f.title}")
        print(f"      {f.why}")
        if f.fixed:
            print(f"      → {f.note}")
        elif f.fix:
            print(f"      → {f.fix}" + (L("（--fix 能修）", " (--fix can do this)") if f.fixable else ""))
    s = {sv: sum(1 for f in left if f.severity == sv) for sv in SEVERITIES}
    print(L(f"{s['critical']} 项危险 · {s['warn']} 项要留意 · {s['info']} 项提一句", f"{s['critical']} critical · {s['warn']} to look at · {s['info']} for your information")
          + (L(f" · 修好了 {sum(1 for f in found if f.fixed)} 项", f" · {sum(1 for f in found if f.fixed)} fixed") if a.fix else ""))
    return 1 if crit else 0
