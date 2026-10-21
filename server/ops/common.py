"""运维命令共用的小件（第 7 批，2026-10-19）。只用标准库：venv 坏了（依赖缺了）doctor、logs 照样起得来。

路径：安装器把一切放在 ~/.openmousse（仓库软链 repo、venv、logs）；server.json 在 MOUSSE_SERVER_CONFIG（默认 ~/.openmousse/server.json），
  运维命令自己的东西（更新检查的结果和日志、备份）放 server.json 旁边（state_dir），测试换一份 server.json 就全换了。
服务：Linux（含 WSL）是 systemd user 服务，macOS 是 launchd 的 LaunchAgent，两样都没有的是自己起的（packs/core/setup.py 的
  start_cmd / stop_cmd，这里的命令和它一字不差）。systemd 认不认照安装器：有 systemctl 而且 /run/systemd/system 在（容器、没开 systemd
  的 WSL 只有命令）；环境变量 MOUSSE_SERVICE_MANAGER = systemd | launchd | none 能直接指定（测试、认错了的机器）。
打码：engine/errors.py 的 scrub（像密钥的串），再加上这台机器上真的令牌和密钥（server.json 的 auth.tokens、secrets.env 的值）。
  直接按文件读进那一个模块，不经 engine 包（engine/__init__.py 要导入一串依赖）。
"""
from __future__ import annotations

import contextlib
import importlib.util
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
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import config
from i18n import L

REPO = config.REPO
BACKUPS_KEPT = 20           # server.json.bak-*：和 engine/setup_api.py 同一个名字、同一个上限（两边一起修剪）
MARK = {"ok": "✓", "warn": "!", "error": "✗", "info": "·"}
PY = "~/.openmousse/venv/bin/python"
REPO_TILDE = "~/.openmousse/repo"


# —— 路径 ——————————————————————————————————————————————————————————————————————————

def home() -> Path:
    return Path.home()


def mousse_home() -> Path:
    return home() / ".openmousse"


def repo_link() -> Path:
    return mousse_home() / "repo"


def venv() -> Path:
    return mousse_home() / "venv"


def log_dir() -> Path:
    return mousse_home() / "logs"


def config_path() -> Path:
    return config.CONFIG_PATH


def state_dir() -> Path:
    """运维命令自己的东西放哪：server.json 旁边（默认 ~/.openmousse）。"""
    return config.CONFIG_PATH.parent


def tree_home() -> Path:
    return Path(os.environ.get("MOUSSE_TREE_HOME") or home() / ".mousse-tree").expanduser()


def tilde(p: Path | str | None) -> str:
    """路径给人看：家目录写成 ~（诊断包里也靠它把用户名去掉）。"""
    if p is None:
        return ""
    s, h = str(p), str(home())
    return "~" + s[len(h):] if s == h or s.startswith(h + os.sep) else s


def load_config() -> tuple[dict | None, str | None]:
    """server.json 现读（不走 config.raw 的缓存）→ (内容, 问题)。问题：None | missing（没有文件）| unreadable（读不了）| broken（不是 JSON
    对象）。有问题时内容是 None（missing 给 {}：没有文件的机器照默认值跑）。"""
    p = config_path()
    try:
        text = p.read_text(encoding="utf8")
    except FileNotFoundError:
        return {}, "missing"
    except (OSError, UnicodeDecodeError):
        return None, "unreadable"
    try:
        data = json.loads(text) if text.strip() else {}
    except ValueError:
        return None, "broken"
    return (data, None) if isinstance(data, dict) else (None, "broken")


def cfg() -> dict:
    """读得了的 server.json；读不了 = {}（各处照默认值）。"""
    data, _ = load_config()
    return data or {}


def _p(value, default: Path) -> Path:
    return Path(str(value)).expanduser() if value else default


def data_dir(c: dict | None = None) -> Path:
    c = cfg() if c is None else c
    return _p(c.get("data_dir"), mousse_home() / "data")


def db_path(c: dict | None = None) -> Path:
    c = cfg() if c is None else c
    return _p(c.get("db"), data_dir(c) / "mousse.db")


def recall_db(c: dict | None = None) -> Path:
    """记忆检索的索引（engine/recall.py 的 db_path：engine.recall.db，默认 <data_dir>/recall.db）。"""
    c = cfg() if c is None else c
    e = c.get("engine") if isinstance(c.get("engine"), dict) else {}
    r = e.get("recall") if isinstance(e.get("recall"), dict) else {}
    return _p(r.get("db"), data_dir(c) / "recall.db")


def secrets_path(c: dict | None = None) -> Path:
    """secrets.env（同 secretstore.path：server.json 的 secrets_file，默认 server.json 旁边）。"""
    c = cfg() if c is None else c
    return _p(c.get("secrets_file"), config_path().parent / "secrets.env")


def chatgpt_dir(c: dict | None = None) -> Path:
    """ChatGPT 套餐的凭据目录（engine/chatgpt_plan.py 的 data_path）：也算密钥。"""
    return data_dir(c) / "chatgpt"


def grok_dir(c: dict | None = None) -> Path:
    """Grok 订阅的令牌目录（10/20，engine/grok_plan.py 的 data_path）：也算密钥。"""
    return data_dir(c) / "grok"


def workspaces(c: dict | None = None) -> dict[str, Path]:
    """各 Agent 的工作区：main = server.json 的 workspace（默认 <openclaw_home>/workspace），加上 agent_workspaces。"""
    c = cfg() if c is None else c
    oc_home = _p(c.get("openclaw_home"), home() / ".openclaw")
    out = {"main": _p(c.get("workspace"), oc_home / "workspace")}
    aw = c.get("agent_workspaces")
    for k, v in (aw.items() if isinstance(aw, dict) else []):
        if v:
            out[str(k)] = Path(str(v)).expanduser()
    return out


# —— 版本 ——————————————————————————————————————————————————————————————————————————

def git(*args: str, repo: Path | None = None, timeout: float = 60, check: bool = False) -> subprocess.CompletedProcess:
    """在仓库里跑 git（不弹终端问密码：GIT_TERMINAL_PROMPT=0）。git 不在 → returncode 127。"""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}
    try:
        return subprocess.run(["git", "-C", str(repo or REPO), *args], capture_output=True, text=True, timeout=timeout, env=env,  # noqa: S603,S607
                              check=check)
    except FileNotFoundError:
        return subprocess.CompletedProcess(["git", *args], 127, "", "git not found")
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(["git", *args], 124, "", "timeout")


def head_commit(repo: Path | None = None, short: bool = True) -> str | None:
    r = git("rev-parse", *(["--short=7"] if short else []), "HEAD", repo=repo, timeout=10)
    return (r.stdout.strip() or None) if r.returncode == 0 else None


def repo_version(repo: Path | None = None) -> str:
    """仓库里 server/version.py 的 VERSION（不导入：导入它会跑 git）。读不到 = 空。"""
    try:
        text = ((repo or REPO) / "server/version.py").read_text(encoding="utf8")
    except OSError:
        return ""
    m = re.search(r'^VERSION = "([^"]+)"', text, re.M)
    return m.group(1) if m else ""


def update_state_path() -> Path:
    """mousse update --check 存的结果（status 只读它，不联网）。"""
    return state_dir() / "update" / "check.json"


def update_state() -> dict | None:
    try:
        d = json.loads(update_state_path().read_text(encoding="utf8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


# —— 服务 ——————————————————————————————————————————————————————————————————————————

@dataclass(frozen=True)
class Service:
    name: str          # 给人看的短名：server / daily-close / tree / node
    unit: str          # systemd user 单元
    label: str         # launchd 标签
    log: str | None    # launchd、自己起的服务的输出写到哪个文件（相对 ~/.openmousse/logs；世界树在它自己的家里）
    timer: bool = False


SERVER = Service("server", "openmousse-server.service", "ai.openmousse.server", "server.log")
DAILY_CLOSE = Service("daily-close", "openmousse-daily-close.timer", "ai.openmousse.daily-close", "daily-close.log", timer=True)
TREE = Service("tree", "mousse-tree.service", "ai.openmousse.tree", None)
NODE = Service("node", "openmousse-node.service", "ai.openmousse.node", "node.log")  # 第 8 批的节点主机（uninstall 要认得它）
SERVICES = {s.name: s for s in (SERVER, DAILY_CLOSE, TREE, NODE)}


def manager() -> str:
    """systemd | launchd | none（见文件开头）。"""
    forced = (os.environ.get("MOUSSE_SERVICE_MANAGER") or "").strip().lower()
    if forced in ("systemd", "launchd", "none"):
        return forced
    if shutil.which("systemctl") and Path("/run/systemd/system").is_dir():
        return "systemd"
    if sys.platform == "darwin" and shutil.which("launchctl"):
        return "launchd"
    return "none"


def _env() -> dict[str, str]:
    env = dict(os.environ)
    run_dir = f"/run/user/{os.getuid()}"
    if not env.get("XDG_RUNTIME_DIR") and Path(run_dir).is_dir():
        env["XDG_RUNTIME_DIR"] = run_dir  # sudo -iu 之类进来的 shell 没有它，systemctl --user 就找不到自己的服务（同 check.sh）
    return env


def run(cmd: list[str], timeout: float = 30, **kw) -> subprocess.CompletedProcess:
    """跑一条命令，不抛：没有这个命令 → 127，超时 → 124。"""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=kw.pop("env", None) or _env(), check=False, **kw)  # noqa: S603
    except FileNotFoundError:
        return subprocess.CompletedProcess(cmd, 127, "", f"{cmd[0]}: not found")
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 124, "", f"{cmd[0]}: timed out")
    except OSError as e:
        return subprocess.CompletedProcess(cmd, 126, "", f"{cmd[0]}: {e}")


def unit_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or home() / ".config") / "systemd" / "user"


def plist(svc: Service) -> Path:
    return home() / "Library" / "LaunchAgents" / f"{svc.label}.plist"


def _launchd_print(svc: Service) -> tuple[str | None, str]:
    uid = os.getuid()
    for d in (f"gui/{uid}", f"user/{uid}"):
        r = run(["launchctl", "print", f"{d}/{svc.label}"], timeout=15)
        if r.returncode == 0:
            return d, r.stdout
    return None, ""


def state(svc: Service) -> dict:
    """{"manager", "installed": True/False/None（认不出）, "active": bool, "state": 原样的状态}。"""
    m = manager()
    if m == "systemd":
        st = run(["systemctl", "--user", "is-active", svc.unit], timeout=15).stdout.strip() or "unknown"
        installed = (unit_dir() / svc.unit).exists() or st not in ("inactive", "unknown")
        return {"manager": m, "installed": installed, "active": st == "active", "state": st}
    if m == "launchd":
        domain, out = _launchd_print(svc)
        if domain is None:
            return {"manager": m, "installed": plist(svc).exists(), "active": False, "state": "not loaded"}
        s = re.search(r"^\s*state = (.+)$", out, re.M)
        st = s.group(1).strip() if s else "loaded"
        # 定时跑的（日结）：装上了就算开着；常驻的要真在跑
        return {"manager": m, "installed": True, "active": svc.timer or st == "running", "state": st, "domain": domain}
    return {"manager": m, "installed": None, "active": False, "state": "unmanaged"}


def manual_stop_cmd(svc: Service) -> str:
    """自己起的服务怎么停（同 packs/core/setup.py 的 stop_cmd：只认自己的进程，命令行要以 venv 的 python 开头）。"""
    py = "^$HOME/.openmousse/venv/bin/python[0-9.]*"
    pat = f"{py} [^ ]*mousse-tree serve" if svc is TREE else f"{py} run.py"
    return f'pkill -u "$(id -u)" -f "{pat}"'


def manual_start_cmd(svc: Service) -> str:
    """自己起的服务怎么起（同 packs/core/setup.py 的 start_cmd）。"""
    if svc is TREE:
        return "nohup ~/.openmousse/venv/bin/mousse-tree serve >> ~/.mousse-tree/tree.log 2>&1 &"
    return f"cd {REPO_TILDE}/server && nohup {PY} run.py >> ~/.openmousse/logs/{svc.log or 'server.log'} 2>&1 &"


def restart_cmd(svc: Service = SERVER) -> str:
    """给人看的：在这台机器上怎么重启它。"""
    m = manager()
    if m == "systemd":
        return f"systemctl --user restart {svc.unit.removesuffix('.service')}"
    if m == "launchd":
        return f"launchctl kickstart -k gui/$(id -u)/{svc.label}"
    return f"{manual_stop_cmd(svc)}; {manual_start_cmd(svc)}"


def stop_cmd(svc: Service = SERVER) -> str:
    m = manager()
    if m == "systemd":
        return f"systemctl --user stop {svc.unit.removesuffix('.service')}"
    if m == "launchd":
        return f"launchctl bootout gui/$(id -u)/{svc.label}"
    return manual_stop_cmd(svc)


def start_cmd(svc: Service = SERVER) -> str:
    m = manager()
    if m == "systemd":
        return f"systemctl --user start {svc.unit.removesuffix('.service')}"
    if m == "launchd":
        return f"launchctl bootstrap gui/$(id -u) {tilde(plist(svc))}"
    return manual_start_cmd(svc)


def _manual(svc: Service, action: str) -> tuple[bool, str]:
    """没有服务管理器：照安装器打印的那两行停 / 起（只认 venv 的 python 起的那个进程）。"""
    py = str(venv() / "bin" / "python")
    pat = f"^{re.escape(py)}[0-9.]* run.py" if svc is not TREE else f"^{re.escape(py)}[0-9.]* [^ ]*mousse-tree serve"
    if action in ("stop", "restart"):
        r = run(["pkill", "-u", str(os.getuid()), "-f", pat], timeout=15)
        if r.returncode not in (0, 1):  # 1 = 没有在跑的
            return False, (r.stderr or r.stdout).strip()[-300:]
        if action == "stop":
            return True, ""
        time.sleep(1)
    if svc is TREE:
        args, cwd, log = [str(venv() / "bin" / "mousse-tree"), "serve"], None, tree_home() / "tree.log"
    else:
        args, cwd, log = [py, "run.py"], repo_link() / "server", log_dir() / (svc.log or "server.log")
    log.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(log, "ab") as out:
            subprocess.Popen(args, cwd=cwd, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,  # noqa: S603
                             start_new_session=True)
    except OSError as e:
        return False, str(e)
    return True, ""


def control(svc: Service, action: str) -> tuple[bool, str]:
    """停 / 起 / 重启一个服务（start | stop | restart）→ (成了没有, 出错时的原文)。"""
    m = manager()
    if m == "systemd":
        r = run(["systemctl", "--user", action, svc.unit], timeout=120)
        return r.returncode == 0, (r.stderr or r.stdout).strip()[-300:]
    if m == "launchd":
        uid = os.getuid()
        domain, _ = _launchd_print(svc)
        if action == "stop":
            if domain is None:
                return True, ""
            r = run(["launchctl", "bootout", f"{domain}/{svc.label}"], timeout=60)
        elif domain is None:  # 没装进 launchd（停过、bootout 过）：按 plist 装回去，RunAtLoad 的常驻服务装上就起
            r = run(["launchctl", "bootstrap", f"gui/{uid}", str(plist(svc))], timeout=60)
            if r.returncode != 0:
                r = run(["launchctl", "bootstrap", f"user/{uid}", str(plist(svc))], timeout=60)
        else:
            r = run(["launchctl", "kickstart", *(["-k"] if action == "restart" else []), f"{domain}/{svc.label}"], timeout=60)
        return r.returncode == 0, (r.stderr or r.stdout).strip()[-300:]
    return _manual(svc, action)


# —— 服务器在不在（/api/health）—————————————————————————————————————————————————————

def base_url(c: dict | None = None) -> str:
    c = cfg() if c is None else c
    b = c.get("bind") if isinstance(c.get("bind"), dict) else {}
    host = str(b.get("host") or "127.0.0.1")
    if host in ("0.0.0.0", "::", ""):
        host = "127.0.0.1"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    try:
        port = int(b.get("port") or 8080)
    except (TypeError, ValueError):
        port = 8080
    return f"http://{host}:{port}"


def local_token(c: dict | None = None) -> str | None:
    """本机脚本调 /api 用的令牌（config.pick_api_token：mcp、api、sentinel 那几把在 /api 上不通）。"""
    c = cfg() if c is None else c
    a = c.get("auth") if isinstance(c.get("auth"), dict) else {}
    t = a.get("tokens") if isinstance(a.get("tokens"), dict) else {}
    return config.pick_api_token({str(k): str(v) for k, v in t.items() if v})


def http_json(url: str, token: str | None = None, *, method: str = "GET", body: dict | None = None,
              timeout: float = 5) -> tuple[int, dict | None, str]:
    """→ (HTTP 状态（连不上 = 0）, JSON 对象或 None, 出错的原文（打过码）)。"""
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 — 用户自己的服务器
            raw = r.read().decode("utf8", "replace")
            code = r.status
    except urllib.error.HTTPError as e:
        raw, code = e.read().decode("utf8", "replace"), e.code
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        return 0, None, scrub(str(getattr(e, "reason", "") or e))
    try:
        j = json.loads(raw)
    except ValueError:
        j = None
    return code, j if isinstance(j, dict) else None, "" if 200 <= code < 300 else scrub(raw[:300])


def health(c: dict | None = None, timeout: float = 3) -> dict:
    """GET /api/health → {"up": 回话了（200）, "status", "data", "error", "url"}。"""
    c = cfg() if c is None else c
    url = base_url(c)
    code, j, err = http_json(f"{url}/api/health", local_token(c), timeout=timeout)
    return {"up": code == 200 and bool(j), "status": code, "data": j or {}, "error": err, "url": url}


def wait_health(c: dict | None = None, seconds: float = 60, every: float = 1.0) -> dict:
    """等 /api/health 通，最多 seconds 秒 → 最后一次的 health()。"""
    end = time.monotonic() + seconds
    while True:
        h = health(c, timeout=min(5.0, max(1.0, seconds)))
        if h["up"] or time.monotonic() >= end:
            return h
        time.sleep(every)


def busy(c: dict | None = None) -> bool | None:
    """有回复在跑吗（/api/chat/busy，safe_restart.py 用的那一个）：读不到 = None。"""
    c = cfg() if c is None else c
    code, j, _ = http_json(f"{base_url(c)}/api/chat/busy", local_token(c), timeout=5)
    return None if code != 200 or j is None else not j.get("idle")


def wait_idle(c: dict | None = None, seconds: float = 600, every: float = 3.0) -> bool:
    """等进行中的回复都结束再动服务（同 safe_restart.py；读不到忙闲 = 服务没在跑或太老，当空闲）→ 空了没有。"""
    end = time.monotonic() + seconds
    while True:
        if not busy(c):
            return True
        if time.monotonic() >= end:
            return False
        time.sleep(every)


# —— 打码 ——————————————————————————————————————————————————————————————————————————

_errors_mod = None
_known: list[str] | None = None


def _errors():
    """engine/errors.py（只依赖 re）：已经导入过就用那一个，没有就按文件读进来，不经 engine 包。"""
    global _errors_mod
    if _errors_mod is None:
        mod = sys.modules.get("engine.errors")
        if mod is None:
            spec = importlib.util.spec_from_file_location("_mousse_ops_errors", REPO / "server" / "engine" / "errors.py")
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
        _errors_mod = mod
    return _errors_mod


def _env_values(p: Path) -> list[str]:
    out = []
    with contextlib.suppress(OSError, UnicodeDecodeError):
        for line in p.read_text(encoding="utf8").splitlines():
            k, sep, v = line.strip().removeprefix("export ").partition("=")
            if sep and not k.lstrip().startswith("#"):
                v = v.strip().strip("'\"")
                if len(v) >= 6:
                    out.append(v)
    return out


def known_secrets(c: dict | None = None) -> list[str]:
    """这台机器上真的令牌和密钥（只在内存里，用来打码）：auth.tokens、secrets.env、server.json 里直接写着的 key / token。"""
    global _known
    if _known is not None and c is None:
        return _known
    c = cfg() if c is None else c
    out: list[str] = []

    def walk(v, key: str = "") -> None:
        if isinstance(v, dict):
            for k, x in v.items():
                walk(x, str(k))
        elif isinstance(v, list):
            for x in v:
                walk(x, key)
        elif (isinstance(v, str) and len(v) >= 8 and re.search(r"token|key|secret|password", key, re.I) and not key.endswith("_env")
              and not v.startswith("${") and not re.fullmatch(r"[A-Z_][A-Z0-9_]*", v)):  # 变量名（key_env、token_env 的值）不是密钥
            out.append(v)

    a = c.get("auth") if isinstance(c.get("auth"), dict) else {}
    t = a.get("tokens") if isinstance(a.get("tokens"), dict) else {}
    out += [str(v) for v in t.values() if v and len(str(v)) >= 6]
    walk({k: v for k, v in c.items() if k != "auth"})
    out += _env_values(secrets_path(c))
    out = sorted(set(out), key=len, reverse=True)  # 长的先换：一个是另一个的前缀时不留半截
    _known = out
    return out


def scrub(text: str, extra: Iterable[str] = ()) -> str:
    """打码：这台机器上真的令牌、密钥，加上 engine/errors.py 认得的像密钥的串。"""
    return _errors().scrub(text, *known_secrets(), *extra)


# —— 文件、权限、SQLite ——————————————————————————————————————————————————————————————

def mode_of(p: Path) -> int | None:
    try:
        return p.stat().st_mode & 0o777
    except OSError:
        return None


def is_sqlite(p: Path) -> bool:
    try:
        with open(p, "rb") as f:
            return f.read(16) == b"SQLite format 3\x00"
    except OSError:
        return False


def quick_check(p: Path, timeout: float = 30) -> tuple[bool, str]:
    """只读打开、PRAGMA quick_check → (过了没有, 一句话)。"""
    try:
        conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=timeout)
    except sqlite3.Error as e:
        return False, str(e)
    try:
        rows = [str(r[0]) for r in conn.execute("PRAGMA quick_check").fetchall()]
    except sqlite3.Error as e:
        return False, str(e)
    finally:
        conn.close()
    return rows == ["ok"], "ok" if rows == ["ok"] else "; ".join(rows[:3])


SQLITE_BUSY_WAIT = 15.0  # 库被别的程序锁着（独占）时最多等几秒


def sqlite_copy(src: Path, dst: Path) -> None:
    """在线拷一份 SQLite（sqlite3 的 backup()：服务器照写，拷到的是一个一致的样子；同 Hermes backup_sqlite.py 的做法）。
    backup() 遇到锁着的库会一直重试：timeout=0 + progress 里看时限，锁了 SQLITE_BUSY_WAIT 秒还拷不出来就抛 sqlite3.OperationalError
    （照 Hermes 的 _safe_copy_db），备份、更新、恢复前的快照不会卡死。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + SQLITE_BUSY_WAIT

    def progress(status: int, _remaining: int, _total: int) -> None:
        if status in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED) and time.monotonic() > deadline:
            raise sqlite3.OperationalError(f"locked by another program for {SQLITE_BUSY_WAIT:.0f} s")

    s = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=0)
    try:
        d = sqlite3.connect(dst)
        try:
            s.backup(d, progress=progress)
        finally:
            d.close()
    finally:
        s.close()


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


# —— server.json：先备份、再原子写 ————————————————————————————————————————————————————

def backup_config() -> Path | None:
    """server.json 在同一目录备份一份（server.json.bak-<年月日-时分秒>，0600，只留最近 BACKUPS_KEPT 份；同 engine/setup_api.backup）。"""
    p = config_path()
    if not p.exists():
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dst, n = p.with_name(f"{p.name}.bak-{stamp}"), 1
    while dst.exists():
        dst, n = p.with_name(f"{p.name}.bak-{stamp}-{n}"), n + 1
    fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f, open(p, "rb") as src:
        shutil.copyfileobj(src, f)
    olds = sorted(p.parent.glob(f"{p.name}.bak-*"), key=lambda x: (x.stat().st_mtime, x.name))
    for old in olds[:-BACKUPS_KEPT]:
        with contextlib.suppress(OSError):
            old.unlink()
    return dst


def config_backups() -> list[Path]:
    """server.json 的备份，新的在前。"""
    p = config_path()
    return sorted(p.parent.glob(f"{p.name}.bak-*"), key=lambda x: (x.stat().st_mtime, x.name), reverse=True)


def write_config(data: dict) -> None:
    """原子写（config.save：0600 的临时文件再换名）。调用方先 backup_config()。"""
    config.save(data)


def say(msg: str = "") -> None:
    print(msg, flush=True)


def ask_yes(prompt: str) -> bool:
    """终端里问一句 y/n（没有终端 = 不）。"""
    if not sys.stdin.isatty():
        return False
    try:
        a = input(prompt + L("（y/n）", " (y/n) ")).strip().lower()
    except EOFError:
        return False
    return a.startswith(("y", "是"))
