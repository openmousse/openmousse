#!/usr/bin/env bash
# OpenMousse 自检：一条命令看服务器上每一块通不通，打出 ✓ / ! / ✗ 和怎么修。
# 不打印任何令牌、密钥或带令牌的地址：输出可以整段发给帮你的人。
#
#   bash ~/openmousse/check.sh
#   curl -fsSL https://raw.githubusercontent.com/openmousse/openmousse/main/check.sh | bash
#
# 默认会经 Gateway 发一句测试消息看模型能不能回话（单独一个会话，不进主对话；用一点点额度），--no-chat 跳过。
# 只用系统自带的 python3：服务、venv 坏了也能跑。
set -u
command -v python3 >/dev/null 2>&1 || { echo "✗ 缺 python3 / python3 is missing (sudo apt install python3)"; exit 1; }
exec python3 - "$@" <<'PY'
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

HOME = Path.home()
MOUSSE = HOME / ".openmousse"
SERVER_JSON = Path(os.environ.get("MOUSSE_SERVER_CONFIG") or MOUSSE / "server.json").expanduser()
TREE_HOME = Path(os.environ.get("MOUSSE_TREE_HOME") or HOME / ".mousse-tree").expanduser()
CHAT = "--no-chat" not in sys.argv
SKILLS = ("handoff", "agent-builder", "journal", "memory-tree", "inbox", "dispatch", "project", "board", "proposals", "goals", "onboarding")


def load(p: Path):
    try:
        return json.loads(Path(p).read_text(encoding="utf8"))
    except (OSError, ValueError):
        return None


cfg = load(SERVER_JSON) or {}
_lang = str(cfg.get("language") or os.environ.get("LC_ALL") or os.environ.get("LANG") or "")
LANG = "zh" if _lang.lower().startswith("zh") else "en"


def L(zh: str, en: str) -> str:
    return zh if LANG == "zh" else en


SEP = "：" if LANG == "zh" else ": "
marks: list[str] = []


def line(mark: str, title: str, detail: str = "", fix: str = "") -> None:
    marks.append(mark)
    print(f"  {mark} {title}" + (f"{SEP}{detail}" if detail else ""))
    if fix:
        print(f"      → {fix}")


def ok(title, detail="", fix=""):
    line("✓", title, detail, fix)


def warn(title, detail="", fix=""):
    line("!", title, detail, fix)


def bad(title, detail="", fix=""):
    line("✗", title, detail, fix)


def section(title: str) -> None:
    print()
    print(title)


SECRETISH = re.compile(r"(?=[A-Za-z0-9_-]*[0-9])(?=[A-Za-z0-9_-]*[A-Za-z])[A-Za-z0-9_-]{24,}")


def clean(s, n: int = 160) -> str:
    """报错原文里可能夹着令牌：像随机串的长串一律换掉，再截短。"""
    return SECRETISH.sub("<…>", " ".join(str(s).split()))[:n]


ENV = dict(os.environ)
if not ENV.get("XDG_RUNTIME_DIR") and Path(f"/run/user/{os.getuid()}").is_dir():
    ENV["XDG_RUNTIME_DIR"] = f"/run/user/{os.getuid()}"  # sudo -iu 之类进来的 shell 没有它，systemctl --user 就找不到自己的服务


def run(cmd: list[str], timeout: float = 20) -> tuple[int, str, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=ENV)
        return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()
    except FileNotFoundError:
        return 127, "", "not found"
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"


HAS_SYSTEMD = shutil.which("systemctl") is not None


def unit(name: str) -> str:
    return run(["systemctl", "--user", "is-active", name])[1] or "unknown" if HAS_SYSTEMD else "no-systemd"


def http(url: str, token: str = "", method: str = "GET", body: dict | None = None, headers: dict | None = None, timeout: float = 5) -> tuple[int, str]:
    h = {**({"Authorization": f"Bearer {token}"} if token else {}), **({"Content-Type": "application/json"} if body is not None else {}), **(headers or {})}
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 — 本机的服务
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError, ValueError) as e:
        return 0, str(getattr(e, "reason", "") or type(e).__name__)


def restart_hint(u: str) -> str:
    return f"systemctl --user restart {u}" + L(f"；看日志 journalctl --user -u {u} -n 50", f"; logs: journalctl --user -u {u} -n 50")


# —— 开头：版本 ——
repo = (MOUSSE / "repo").resolve() if (MOUSSE / "repo").exists() else None
code, ver, _ = run(["git", "-C", str(repo), "log", "-1", "--format=%h %cd", "--date=short"]) if repo else (1, "", "")
tz = cfg.get("timezone") or ""
try:
    TZ = ZoneInfo(tz) if tz else None
except (ValueError, KeyError):
    TZ = None
print(L("OpenMousse 自检", "OpenMousse check") + f" · {datetime.now(TZ):%Y-%m-%d %H:%M}" + (f" · {tz}" if tz else "")
      + f" · OpenMousse {ver or '?'}" + f" · Python {sys.version.split()[0]}")

# —— 系统 ——
section(L("系统", "System"))
if sys.version_info >= (3, 11):
    ok("Python", sys.version.split()[0])
else:
    bad("Python", sys.version.split()[0], L("要 3.11 以上", "needs 3.11 or newer"))
free = shutil.disk_usage(HOME).free / 1e9
(ok if free >= 5 else warn if free >= 1 else bad)(L("磁盘剩余", "Free disk"), f"{free:.1f} GB", "" if free >= 5 else L("清一清空间，数据库和上传的文件都在家目录", "free some space; the database and uploads live in your home directory"))
if HAS_SYSTEMD:
    c, o, _ = run(["loginctl", "show-user", os.environ.get("USER") or "", "-p", "Linger", "--value"])
    if o == "yes":
        ok(L("登出后服务照跑（linger）", "Services keep running after logout (linger)"))
    elif c == 0:
        warn(L("登出后服务会停（linger 没开）", "Services stop when you log out (linger is off)"), fix="sudo loginctl enable-linger $USER")
else:
    warn(L("这台机器没有 systemctl", "No systemctl on this machine"), L("服务要自己手动跑", "run the services yourself"))

# —— OpenMousse 服务 ——
section(L("OpenMousse 服务", "OpenMousse server"))
if not cfg:
    bad("server.json", L(f"{SERVER_JSON} 读不到", f"can't read {SERVER_JSON}"), L("跑一遍安装命令：curl -fsSL https://openmousse.ai/install | bash", "run the installer: curl -fsSL https://openmousse.ai/install | bash"))
bind = cfg.get("bind") or {}
host, port = str(bind.get("host") or "127.0.0.1"), int(bind.get("port") or 8080)
tokens = (cfg.get("auth") or {}).get("tokens")
tokens = tokens if isinstance(tokens, dict) else {}
claw_cfg = cfg.get("claw") if isinstance(cfg.get("claw"), dict) else {}
is_openclaw = str(claw_cfg.get("kind") or "openclaw").lower() == "openclaw"
if cfg:
    ok("server.json", L(f"助手 {cfg.get('app_name') or 'OpenMousse'} · 时区 {tz or '?'} · 监听 {host}:{port} · 令牌 {len(tokens)} 个（{', '.join(tokens) or '无'}）",
                        f"assistant {cfg.get('app_name') or 'OpenMousse'} · timezone {tz or '?'} · listening on {host}:{port} · {len(tokens)} tokens ({', '.join(tokens) or 'none'})"))
if not (MOUSSE / "venv/bin/python").exists():
    bad("venv", L("~/.openmousse/venv 不在", "~/.openmousse/venv is missing"), L("再跑一遍安装命令", "run the installer again"))
if not repo:
    bad(L("仓库", "Repository"), L("~/.openmousse/repo 不在", "~/.openmousse/repo is missing"), L("再跑一遍安装命令", "run the installer again"))
st = unit("openmousse-server")
if HAS_SYSTEMD and st != "active":
    bad("openmousse-server", st, restart_hint("openmousse-server"))
tok = str(tokens.get("local") or tokens.get("phone") or next(iter(tokens.values()), ""))
code, body = http(f"http://{host}:{port}/api/health", tok)
health = {}
if code == 200:
    try:
        health = json.loads(body)
    except ValueError:
        health = {}
    ok(L("服务在回话", "Server answers"), f"http://{host}:{port}" + (L("（还没用过：第一次打开 app 会出「从这里开始」）", " (not used yet: the app will show “Start here”)") if health.get("first_run") else ""))
elif code in (401, 403):
    bad(L("服务在，但令牌对不上", "Server is up but the token doesn't match"), f"HTTP {code}", L("令牌改过的话重启一下服务：", "if you changed tokens, restart it: ") + "systemctl --user restart openmousse-server")
else:
    bad(L("服务连不上", "Server not reachable"), f"http://{host}:{port} · {clean(body, 80)}", restart_hint("openmousse-server"))

# —— 手机怎么连 ——
section(L("手机怎么连（Tailscale）", "Reaching it from the phone (Tailscale)"))
ts_ip = ""
if not shutil.which("tailscale"):
    warn(L("没装 Tailscale", "Tailscale isn't installed"), L("手机在外面连不上服务器", "the phone can't reach the server from outside"),
         L("curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up，再跑一遍安装命令", "curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up, then run the installer again"))
else:
    c, o, e = run(["tailscale", "status", "--json"])
    state = ""
    try:
        state = json.loads(o).get("BackendState", "") if c == 0 else ""
    except ValueError:
        pass
    ts_ip = (run(["tailscale", "ip", "-4"])[1].splitlines() or [""])[0]
    if state == "Running" and ts_ip:
        ok("Tailscale", L(f"在线，这台机器是 {ts_ip}", f"online, this machine is {ts_ip}"))
    else:
        bad("Tailscale", state or clean(e, 80) or L("没连上", "not connected"), "sudo tailscale up")
if host.startswith("100.") and ts_ip and host != ts_ip:
    bad(L("服务听的地址不是这台机器现在的 Tailscale 地址", "The server listens on an old Tailscale address"), f"{host} ≠ {ts_ip}", L("再跑一遍安装命令（它会换过来）", "run the installer again (it switches over)"))
elif host in ("127.0.0.1", "localhost") and ts_ip:
    warn(L("服务只听本机，手机连不上", "The server only listens on this machine; the phone can't reach it"), f"{host}:{port}", L("再跑一遍安装命令，它会改听 Tailscale 地址", "run the installer again; it switches to the Tailscale address"))
elif host == ts_ip:
    ok(L("app 连接页填", "In the app, enter"), f"http://{host}:{port}" + L(" 和手机令牌", " and your phone token"))
if shutil.which("ufw"):
    c, o, _ = run(["sudo", "-n", "ufw", "status"], timeout=10)
    if c == 0 and o.startswith(("Status: active", "状态： 激活")):
        rules = [ln for ln in o.splitlines() if re.search(rf"(^|\s){port}(/tcp)?\b", ln) and "ALLOW" in ln]
        if rules:
            ok(L("防火墙放行了", "Firewall allows"), f"{port}/tcp")
        else:
            bad(L("防火墙（UFW）挡着这个端口", "The firewall (UFW) blocks this port"), f"{port}/tcp", f"sudo ufw allow in on tailscale0 to any port {port} proto tcp")

# —— OpenClaw ——
oc_home = Path(str(cfg.get("openclaw_home") or HOME / ".openclaw")).expanduser()
oc_path = oc_home / "openclaw.json"
oc = load(oc_path) or {}
if is_openclaw:
    section("OpenClaw")
    exe = str(cfg.get("openclaw_bin") or "") if cfg.get("openclaw_bin") and Path(str(cfg.get("openclaw_bin"))).exists() else (shutil.which("openclaw") or "")
    oc_ver = (run([exe, "--version"])[1].splitlines() or [""])[0] if exe else ""
    if exe:
        ok(L("OpenClaw 已装", "OpenClaw installed"), oc_ver or exe)
    else:
        bad(L("找不到 openclaw 命令", "openclaw command not found"), fix=L("装 OpenClaw（openclaw.ai），跑 openclaw onboard", "install OpenClaw (openclaw.ai) and run openclaw onboard"))
    if not oc:
        bad("openclaw.json", L(f"{oc_path} 读不到", f"can't read {oc_path}"), "openclaw onboard")
    gw = oc.get("gateway") or {}
    gw_port = int(gw.get("port") or 18789)
    gw_url = str(cfg.get("gateway") or f"http://127.0.0.1:{gw_port}")
    ep = (((gw.get("http") or {}).get("endpoints") or {}).get("chatCompletions") or {})
    if oc and not ep.get("enabled"):
        bad(L("Gateway 的 OpenAI 兼容接口没开（app 的对话走它）", "The Gateway's OpenAI-compatible API is off (the app's chat uses it)"), fix=L("再跑一遍安装命令", "run the installer again"))
    st = unit("openclaw-gateway")
    if HAS_SYSTEMD and st != "active":
        bad("openclaw-gateway", st, restart_hint("openclaw-gateway"))
    auth = gw.get("auth") or {}
    gw_tok = auth.get("token") if isinstance(auth.get("token"), str) else ""
    gw_code, b = http(gw_url.rstrip("/") + "/v1/models", gw_tok)
    gw_up = bool(gw_code) and gw_code < 500 and gw_code not in (401, 403)
    if gw_up:
        ok(L("Gateway 连得上", "Gateway reachable"), gw_url)
    elif gw_code in (401, 403):
        bad(L("Gateway 在，但不认令牌", "Gateway is up but rejects the token"), f"HTTP {gw_code}",
            L("OpenMousse 用的是 openclaw.json 里 gateway.auth.token；Gateway 改成了密码登录的话，换回令牌", "OpenMousse uses gateway.auth.token from openclaw.json; if the Gateway uses a password, switch it back to a token"))
    else:
        mismatch = cfg.get("gateway") and f":{gw_port}" not in str(cfg.get("gateway"))
        bad(L("Gateway 连不上", "Gateway not reachable"), f"{gw_url} · {clean(b, 80)}",
            L(f"server.json 的 gateway 指的端口和 openclaw.json（{gw_port}）不一样：改成 http://127.0.0.1:{gw_port} 再重启服务", f"server.json's gateway points at a different port from openclaw.json ({gw_port}): set it to http://127.0.0.1:{gw_port} and restart the server")
            if mismatch else L("openclaw gateway status 看它在不在跑；", "check openclaw gateway status; ") + restart_hint("openclaw-gateway"))
    # 模型：谁在答、登没登录
    if exe:
        ms_code, o, e = run([exe, "models", "status", "--json"], timeout=60)
        try:
            ms = json.loads(o) if ms_code == 0 else {}
        except ValueError:
            ms = {}
        if ms:
            model = ms.get("resolvedDefault") or ms.get("defaultModel") or "?"
            au = ms.get("auth") or {}
            missing = au.get("missingProvidersInUse") or []
            expired = [p.get("provider") for p in (au.get("oauth") or {}).get("providers") or [] if p.get("status") not in ("ok", "static", None)]
            if missing:
                bad(L("默认模型没登录", "The default model isn't signed in"), f"{model} · {', '.join(map(str, missing))}", L("openclaw configure（或 openclaw onboard）登录", "sign in with openclaw configure (or openclaw onboard)"))
            elif expired:
                bad(L("模型登录过期了", "A model sign-in has expired"), ", ".join(map(str, expired)), "openclaw models status")
            else:
                ok(L("默认模型", "Default model"), model)
        else:
            warn(L("读不到模型状态", "Couldn't read the model status"), clean(e or o, 100), "openclaw models status")
    if CHAT and gw_tok and gw_up:
        t0 = time.time()
        c2, b2 = http(gw_url.rstrip("/") + "/v1/chat/completions", gw_tok, "POST",
                      {"model": "openclaw/main", "stream": False, "messages": [{"role": "user", "content": L("自检：只回一个字「好」。", "Self-check: reply with just the word OK.")}]},
                      {"x-openclaw-session-key": "agent:main:mousse-check", "x-openclaw-agent-id": "main"}, timeout=120)
        secs = time.time() - t0
        reply = ""
        if c2 == 200:
            try:
                reply = (json.loads(b2).get("choices") or [{}])[0].get("message", {}).get("content") or ""
            except (ValueError, AttributeError, IndexError):
                reply = ""
        if reply.strip():
            ok(L("模型能回话", "The model answers"), L(f"{secs:.0f} 秒回了「{clean(reply, 30)}」", f"answered “{clean(reply, 30)}” in {secs:.0f}s"))
        else:
            err = clean(b2, 200)
            if "claude_code_version_too_old" in b2:
                fix = L("这个版本的 OpenClaw 报给 Anthropic 的 Claude Code 版本太旧，新模型不让用：先换一个模型（openclaw models set anthropic/claude-opus-5），或者 openclaw update 后再试",
                        "this OpenClaw reports too old a Claude Code version to Anthropic, so the newest models are refused: switch model (openclaw models set anthropic/claude-opus-5) or run openclaw update and try again")
            elif re.search(r"rate.?limit|quota|usage limit|429", b2, re.I):
                fix = L("额度用完了或太频繁：等一会儿，或者在 OpenClaw 里加一个回退模型", "out of quota or rate-limited: wait, or add a fallback model in OpenClaw")
            elif re.search(r"auth|api key|401|unauthorized|credential", b2, re.I):
                fix = L("模型那边不认你的登录：openclaw configure 重新登录", "the model provider rejects the sign-in: sign in again with openclaw configure")
            else:
                fix = L("看 Gateway 日志：journalctl --user -u openclaw-gateway -n 80", "check the Gateway logs: journalctl --user -u openclaw-gateway -n 80")
            bad(L("模型没回话", "The model didn't answer"), f"HTTP {c2} · {err}" if c2 else err, fix)
    # 工作区：skills、规则、首次对话
    agents = oc.get("agents") or {}
    ws = ((agents.get("entries") or {}).get("main") or {}).get("workspace") or (agents.get("defaults") or {}).get("workspace")
    ws = Path(str(cfg.get("workspace") or ws or oc_home / "workspace")).expanduser()
    broken = [s for s in SKILLS if not (ws / "skills" / s / "SKILL.md").exists()]
    # OpenClaw 不加载指到工作区外面的 skill 软链（Gateway 日志 reason=symlink-escape），除非目标在 skills.load.allowSymlinkTargets 里
    allowed = [Path(str(p)).expanduser().resolve() for p in ((oc.get("skills") or {}).get("load") or {}).get("allowSymlinkTargets") or []]
    root = (ws / "skills").resolve()
    blocked = [s for s in SKILLS if s not in broken and (ws / "skills" / s).is_symlink()
               and not (ws / "skills" / s).resolve().is_relative_to(root)
               and not any((ws / "skills" / s).resolve().is_relative_to(a) for a in allowed)]
    if broken:
        bad("skills", L(f"{len(broken)} 个没装好：{', '.join(broken)}", f"{len(broken)} missing: {', '.join(broken)}"), L("再跑一遍安装命令", "run the installer again"))
    elif blocked:
        bad("skills", L(f"{len(blocked)} 个是软链，OpenClaw 不加载（Gateway 日志里是 symlink-escape）", f"{len(blocked)} are symlinks OpenClaw won't load (symlink-escape in the Gateway log)"),
            L("再跑一遍安装命令（新版会把它们的目录加进 skills.load.allowSymlinkTargets）", "run the installer again (newer versions add their folder to skills.load.allowSymlinkTargets)"))
    else:
        ok("skills", L(f"{len(SKILLS)} 个都在 {ws}/skills，OpenClaw 能加载", f"all {len(SKILLS)} in {ws}/skills, loadable by OpenClaw"))
    agents_md = ws / "AGENTS.md"
    if not (agents_md.exists() and "## OpenMousse" in agents_md.read_text(encoding="utf8", errors="replace")):
        warn(L("AGENTS.md 里没有 OpenMousse 的规则", "AGENTS.md has no OpenMousse rules"), fix=L("再跑一遍安装命令", "run the installer again"))
    if (ws / "BOOTSTRAP.md").exists():
        warn(L("OpenClaw 的起名仪式（BOOTSTRAP.md）还在", "OpenClaw's naming ritual (BOOTSTRAP.md) is still there"),
             L("第一次对话会先让你给助手起名，app 的带路要问第二遍才出来", "the first conversation asks you to name the assistant before the app's walk-through"),
             L("再跑一遍安装命令（新版会把它挪进备份）", "run the installer again (newer versions move it to a backup)"))
elif claw_cfg:
    section(L("你的 claw", "Your claw"))
    url = str(claw_cfg.get("url") or "").rstrip("/")
    c, b = http(url + "/models", str(claw_cfg.get("token") or ""))
    if c and c < 500 and c not in (401, 403):
        ok(str(claw_cfg.get("name") or "claw"), url)
    else:
        bad(str(claw_cfg.get("name") or "claw"), f"{url} · HTTP {c or '-'} · {clean(b, 80)}", L("看它在不在跑、地址和令牌对不对（改 server.json 的 claw 段不用重启）", "check it's running and the URL / token are right (editing server.json's claw section needs no restart)"))

# —— 世界树 ——
section(L("世界树", "Memory tree"))
tc = load(TREE_HOME / "config.json")
if not tc:
    warn(L("没装世界树", "The memory tree isn't set up"), fix=L("再跑一遍安装命令", "run the installer again"))
else:
    st = unit("mousse-tree")
    if HAS_SYSTEMD and st != "active":
        bad("mousse-tree", st, restart_hint("mousse-tree"))
    tport = int(tc.get("port") or 8787)
    c, b = http(f"http://127.0.0.1:{tport}/health")
    try:
        th = json.loads(b) if c == 200 else {}
    except ValueError:
        th = {}
    if th.get("ok"):
        ok(L("世界树在回话", "Memory tree answers"), L(f"{th.get('memories', 0)} 条记忆 · {len(th.get('platforms') or [])} 个平台令牌", f"{th.get('memories', 0)} memories · {len(th.get('platforms') or [])} platform tokens"))
        if th.get("note_issues"):
            warn(L("有格式坏的笔记", "Some notes are malformed"), str(th["note_issues"]), "~/.openmousse/venv/bin/mousse-tree check")
    else:
        bad(L("世界树连不上", "Memory tree not reachable"), f"127.0.0.1:{tport} · {clean(b, 60)}", restart_hint("mousse-tree"))
    hosts = tc.get("public_hosts") or []
    if hosts:
        ok(L("别的 AI 能从公网连它", "Other AIs can reach it over the internet"), ", ".join(map(str, hosts)))
    else:
        print("    " + L("（没开公网：Claude.ai、ChatGPT 这些还连不上它；要开就再跑一遍安装命令，「让 AI 平台连世界树」答 y）",
                        "(not public: Claude.ai, ChatGPT and the like can't reach it yet; to open it, run the installer again and answer y)"))

# —— 日结和推送 ——
section(L("日结和推送", "Daily digest and notifications"))
if HAS_SYSTEMD:
    st = unit("openmousse-daily-close.timer")
    if st == "active":
        o = run(["systemctl", "--user", "list-timers", "openmousse-daily-close.timer", "--output=json", "--no-pager"])[1]
        try:  # next 是微秒时间戳 → 按 server.json 的时区显示（老 systemd 没有 --output=json，就不写时间）
            nxt = datetime.fromtimestamp(json.loads(o)[0]["next"] / 1e6, timezone.utc).astimezone(TZ).strftime("%m-%d %H:%M")
        except (ValueError, KeyError, IndexError, TypeError):
            nxt = "?"
        res = run(["systemctl", "--user", "show", "openmousse-daily-close.service", "-p", "Result", "--value"])[1]
        (ok if res in ("success", "") else warn)(L("每晚日结", "Nightly digest"), L(f"下次 {nxt}", f"next {nxt}") + (L(f"，上次结果 {res}", f", last result {res}") if res else ""),
                                                "" if res in ("success", "") else L("看日志：journalctl --user -u openmousse-daily-close -n 50", "logs: journalctl --user -u openmousse-daily-close -n 50"))
    else:
        bad(L("每晚日结的定时器没开", "The nightly digest timer is off"), st, "systemctl --user enable --now openmousse-daily-close.timer")
db = Path(str(cfg.get("db") or "")).expanduser() if cfg.get("db") else Path(str(cfg.get("data_dir") or MOUSSE / "data")).expanduser() / "mousse.db"
if db.exists():
    try:
        with sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5) as conn:
            rows = conn.execute("SELECT disabled, last_seen, note FROM push_tokens").fetchall()
    except sqlite3.Error:
        rows = None
    if rows is None or not rows:
        warn(L("还没有手机登记推送", "No phone has registered for notifications yet"), fix=L("在 iPhone 上打开 app、连上服务器、允许通知", "open the app on the iPhone, connect, and allow notifications"))
    else:
        live = [r for r in rows if not r[0]]
        last = max((r[1] or "" for r in rows), default="")
        (ok if live else bad)(L("推送设备", "Push devices"), L(f"{len(live)} 台能推（最近 {last[:16]}）", f"{len(live)} active (last seen {last[:16]})"),
                              "" if live else L("在 iPhone 设置里给 app 打开通知，再打开一次 app", "turn on notifications for the app in iPhone Settings, then open the app once"))
        for d, _seen, note in rows:
            if d and note:
                print(f"      {clean(note, 120)}")
else:
    warn(L("还没有数据库", "No database yet"), str(db), L("服务启动一次就会建", "the server creates it on first start"))

# —— 总结 ——
n_bad, n_warn, n_ok = marks.count("✗"), marks.count("!"), marks.count("✓")
print()
print(L(f"{n_ok} 项正常 · {n_warn} 项要留意 · {n_bad} 项不通。", f"{n_ok} ok · {n_warn} to look at · {n_bad} failing.")
      + L("这段输出里没有令牌和密钥，可以整段发给帮你的人。", " There are no tokens or keys in this output; you can send all of it to whoever helps you."))
sys.exit(1 if n_bad else 0)
PY
