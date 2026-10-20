"""mousse diagnostics（第 7 批，2026-10-19）：打一个给人看问题用的包（报 bug、问人时附上）。

  mousse diagnostics [--out FILE.zip 或目录] [--print] [--lines 2000]

一个 zip（0600），默认放在 server.json 旁边的 diagnostics/（0700），名字 openmousse-diagnostics-<日期-时间>.zip；--print 不写文件，
把里面的每一份 JSON 打到终端（好贴）。里面：
  README.txt         这是什么、哪些没放进来
  summary.json       版本、commit、通道、Python、系统（名字、版本、架构；不要主机名）、服务管理器、装在 Docker 里没有、PATH 上有哪些命令
  doctor.json        mousse doctor 的结果（只查，不修）
  status.json        mousse status 的摘要
  audit.json         mousse security audit 的结果（只查，不修）
  config-shape.json  server.json 的样子：键都在，值只留安全的那几种（模型引用、claw 的种类、语言、时区、开关和数字……），别的字换成
                     <str>、<url>、<set> 这样的记号；auth.tokens 只留名字，配对码、Tailscale 设备名只留几个
  config-check.json  mousse config validate 找到的
  engine.json        内核：主对话的模型、备用、别名、各家有没有密钥（只说有没有、从哪来）、正在冷却的
  databases.json     每个 SQLite 库：多大、quick_check、每张表几行（不读内容）
  usage.json         最近 7 天的 token 和花费（按天、模型、用途加总）
  logs.json          日志里的信号：扫最后 --lines 行，按 [模块] 和级别数、HTTP 状态按类数、报错的类型和出在哪几行代码（Traceback 的
                     文件:行号:函数）；日志的原文一行都不放（里面可能有对话的片段）
  update.json        上一次 mousse update --check 存下的、上一次更新的回执
不放：对话、消息、记忆、日志原文、密钥、令牌、邮箱。每一份写进包之前都过一遍：engine/errors.py 的打码 + 这台机器上真的令牌和密钥
  （common.scrub）、邮箱换成 <email>、路径里的家目录和用户名换成 ~、~ 底下不是 OpenMousse 自己的目录换成 ~/<path>、不是本机的地址只留
  协议（Tailscale 的名字、IP 换成 <tailnet host>、<tailnet ip>）、配对来的设备令牌的名字换成 device-1、device-2……、claw 的名字、
  你的名字、Tailscale 设备名换成记号；最后再查一遍整包，真的令牌、密钥、邮箱、Tailscale 的名字、上面那些名字还在的那一份不放（README 里写明）。
服务器没开着也能打。退出码：0 打好了；1 写不了；2 参数不对。
参考：Hermes 的 hermes dump（给人看的环境摘要、不带密钥）、OpenClaw 的 openclaw status --all（能直接贴的诊断）；只借设计，没拿代码。
"""
from __future__ import annotations

import argparse
import collections
import contextlib
import ipaddress
import json
import os
import platform
import re
import shutil
import sqlite3
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from i18n import L

from . import common as C
from .confcheck import ENV, secret_key

FORMAT = 1
LINES = 2000
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
HOMES = re.compile(r"(?<![\w.-])(?:/home|/Users)/[^/\s\"'<>]+")  # 别的用户名的家目录（不是这一个 HOME 的）
URL = re.compile(r"\b(https?|wss?|ssh|git)://([^\s/@\"'<>]+@)?(\[[0-9A-Fa-f:.]+\]|[^\s/:\"'<>()\[\]]+)(:\d+)?([^\s\"'<>()]*)")
SCP = re.compile(r"(?<![\w.-])[\w.-]+@[\w-]+(?:\.[\w-]+)*:[\w~][\w./~-]*")  # git@host:谁/仓库.git
TAILNET = re.compile(r"(?<![\w.-])(?:[A-Za-z0-9-]+\.)+ts\.net\b", re.IGNORECASE)
TAILNET_IPS = (ipaddress.ip_network("100.64.0.0/10"), ipaddress.ip_network("fd7a:115c:a1e0::/48"))
IPV4 = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?!\.?\d)")
IPV6 = re.compile(r"(?<![\w:])(?=[0-9A-Fa-f:]*:[0-9A-Fa-f:]*:)[0-9A-Fa-f:]{3,39}(?![\w:])")  # 对上了还要 ipaddress 认
TILDE = re.compile(r"~/([^/\s\"'<>:,;()\[\]]+)((?:/[^\s\"'<>:,;()\[\]]*)*)")
OURS = {".openmousse", "openmousse", ".openclaw", ".mousse", ".local", ".config", ".cache", "Library", ".claude", ".codex", ".copilot"}  # ~ 底下照留的
BUILTIN_TOKENS = ("phone", "local", "mcp", "api")  # 安装器、tokens.py 起的名字；别的（配对来的 device-<设备名>-…、自己起的）换成 device-N
TOOLS = ("git", "docker", "tailscale", "systemctl", "launchctl", "journalctl", "claude", "codex", "gemini", "copilot", "ollama", "node")
MOUSSE_ENV = ("MOUSSE_INSTALL", "MOUSSE_SERVICE_MANAGER", "MOUSSE_SERVER_CONFIG", "MOUSSE_TREE_HOME", "MOUSSE_REPO")
# config-shape.json 里原样留下的字符串（点路径，* = 任意一段）：不是人名、不是内容、不是密钥的那几种
KEEP = ("language", "timezone", "claw.kind", "claw.model", "claw.models.*", "engine.model", "engine.models.*", "engine.fallbacks.*",
        "engine.use.*", "engine.use.*.*", "engine.aliases.*", "engine.providers.*.api", "engine.providers.*.key_env", "engine.recall.embeddings",
        "engine.recall.embeddings.model", "engine.recall.embeddings.provider", "engine.writeback.model", "engine.search.provider", "update.channel",
        "update.remote", "chat.transport", "engine.chatgpt.models.*", "engine.sandbox.network", "engine.host_exec.security")
COUNT_ONLY = ("auth.pairing", "auth.tailscale_nodes", "share.private_words", "agent_default_skills")
NUMBERS_HIDDEN = ("nutrition_targets",)


# —— 打码 ——————————————————————————————————————————————————————————————————————

def personal(cfg: dict) -> list[tuple[re.Pattern, str]]:
    """server.json 里认得出人的名字 → 换成什么：配对来的、自己起的令牌名（device-1……，按名字排）、claw 的名字、你的名字、Tailscale
    设备名。太短的（< 3 个字）不换（认不出谁，换了反倒把别的字弄乱）。"""
    auth = cfg.get("auth") if isinstance(cfg.get("auth"), dict) else {}
    tokens = auth.get("tokens") if isinstance(auth.get("tokens"), dict) else {}
    claw = cfg.get("claw") if isinstance(cfg.get("claw"), dict) else {}
    pairs = [(str(k), f"device-{i}") for i, k in enumerate(sorted(str(k) for k in tokens if str(k) not in BUILTIN_TOKENS), 1)]
    pairs += [(str(claw.get("name") or ""), "<claw name>"), (str(cfg.get("user_name") or ""), "<name>")]
    pairs += [(str(n), "<tailnet node>") for n in auth.get("tailscale_nodes") or [] if isinstance(n, str)]
    out = [(re.compile(rf"(?<![\w-]){re.escape(w.strip())}(?![\w-])"), to) for w, to in pairs if len(w.strip()) >= 3]
    return sorted(out, key=lambda p: -len(p[0].pattern))  # 长的先换（device-alex-phone-… 在 alex-phone 前头）


def _ip(s: str) -> str | None:
    """IP → 记号；本机的、0.0.0.0、不是 IP 的 → None（照留）。"""
    try:
        ip = ipaddress.ip_address(s.strip("[]"))
    except ValueError:
        return None
    if ip.is_loopback or ip.is_unspecified:
        return None
    if any(ip in n for n in TAILNET_IPS):
        return "<tailnet ip>"
    return "<lan ip>" if ip.is_private else "<ip>"


def _local(host: str) -> bool:
    h = host.lower()
    return h in ("localhost", "0.0.0.0", "[::1]", "[::]") or h.startswith("127.") or h.endswith(".local")


def _url_text(m: re.Match) -> str:
    """字里的地址：本机的照留（去掉 user:pw@）；IP、Tailscale 的名字换成记号、留端口；别的只留协议。"""
    scheme, host, port = m.group(1), m.group(3), m.group(4) or ""
    if _local(host):
        return f"{scheme}://{host}{port}{m.group(5)}"
    label = _ip(host) or ("<tailnet host>" if TAILNET.fullmatch(host) else None)
    return f"{scheme}://{label}{port}" if label else f"<{scheme} url>"


def _tilde(m: re.Match) -> str:
    return m.group(0) if m.group(1) in OURS else "~/<path>"


def clean_text(s: str, words: list[tuple[re.Pattern, str]] = ()) -> str:
    """一段字 → 能放进包里的：打码（engine/errors.py + 这台机器上真的令牌和密钥）、认得出人的名字（personal）、邮箱、地址（_url_text；
    不带协议的 Tailscale 名字、IP 也换）、路径里的家目录（带着用户名）换成 ~、~ 底下不是 OURS 的换成 ~/<path>。"""
    s = C.scrub(str(s))
    for pat, to in words:
        s = pat.sub(to, s)
    s = SCP.sub("<git remote>", s)
    s = EMAIL.sub("<email>", s)
    s = URL.sub(_url_text, s)
    s = TAILNET.sub("<tailnet host>", s)
    s = IPV4.sub(lambda m: _ip(m.group(0)) or m.group(0), s)
    s = IPV6.sub(lambda m: _ip(m.group(0)) or m.group(0), s)
    s = HOMES.sub("~", s)  # /home/<谁>、/Users/<谁>（别的用户的也算）
    home = str(C.home()).rstrip("/")
    if home and home != "/":  # 家目录不在 /home、/Users 底下的（/root、/var/lib/…）
        s = re.sub(rf"(?<![\w.-]){re.escape(home)}(?![\w.-])", "~", s)
    return TILDE.sub(_tilde, s)


def clean(v, words: list[tuple[re.Pattern, str]] = ()):
    """JSON 里的每一个字（键也算）都过一遍 clean_text；整个字是 ~/ 开头的路径（里面可能有空格）：头一段不是 OURS 的整个换掉。"""
    if isinstance(v, dict):
        out: dict = {}
        for k, x in v.items():
            key = base = clean(str(k), words)
            n = 2
            while key in out:  # 换完撞上了（databases.json 按路径排的两个库都成了 ~/<path>）：编号，不丢
                key, n = f"{base} ({n})", n + 1
            out[key] = clean(x, words)
        return out
    if isinstance(v, (list, tuple)):
        return [clean(x, words) for x in v]
    if isinstance(v, str):
        t = clean_text(v, words)
        return "~/<path>" if t.startswith("~/") and t[2:].split("/", 1)[0] not in OURS else t
    return v


def _match(path: str, patterns: tuple[str, ...]) -> bool:
    parts = path.split(".")
    for pat in patterns:
        pp = pat.split(".")
        if len(pp) == len(parts) and all(a == "*" or a == b for a, b in zip(pp, parts, strict=True)):
            return True
    return False


def _url(v: str) -> str:
    """地址只留看得出是哪一种的：本机的照留（端口也是），别的只留协议。"""
    m = re.match(r"^(https?|wss?)://([^/@:]+@)?(\[[^\]]+\]|[^/:]+)(:\d+)?", v)
    if not m:
        return "<str>"
    host = m.group(3)
    if _local(host):
        return f"{m.group(1)}://{host}{m.group(4) or ''}"
    return f"<{m.group(1)} url>"


def shape(v, path: str = ""):
    """server.json → 它的样子（见文件开头）。"""
    last = path.rsplit(".", 1)[-1]
    if _match(path, COUNT_ONLY) and isinstance(v, list):
        return f"<{len(v)} items>"
    if isinstance(v, dict):
        if path == "auth.tokens":
            return {str(k): "<set>" for k in v}
        return {str(k): shape(x, f"{path}.{k}" if path else str(k)) for k, x in v.items()}
    if isinstance(v, list):
        return [shape(x, f"{path}.*") for x in v]
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, (int, float)):
        return "<number>" if any(path == p or path.startswith(p + ".") for p in NUMBERS_HIDDEN) else v
    s = str(v)
    if secret_key(last) and not ENV.match(s) and not s.startswith("${"):
        return "<set>"
    if _match(path, KEEP):
        return s
    if re.match(r"^(https?|wss?)://", s):
        return _url(s)
    if s.startswith(("/", "~")):
        return C.tilde(s) if s.startswith(str(C.home())) or s.startswith("~") else "<path>"
    if re.fullmatch(r"\$\{[A-Z_][A-Z0-9_]*\}", s) or ENV.match(s):
        return s  # 变量名（${NAME}、key_env 那种）：不是值
    return f"<str:{len(s)}>" if s else ""


# —— 各份 ——————————————————————————————————————————————————————————————————————

def summary() -> dict:
    out = {"format": FORMAT, "kind": "openmousse-diagnostics", "createdAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "version": C.repo_version() or None, "commit": C.head_commit(), "python": platform.python_version(),
           "system": {"os": platform.system(), "release": platform.release(), "machine": platform.machine()},
           "serviceManager": C.manager(), "docker": os.environ.get("MOUSSE_INSTALL") == "docker",
           "env": {k: os.environ[k] for k in MOUSSE_ENV if k in os.environ},
           "tools": {t: bool(shutil.which(t)) for t in TOOLS}}
    with contextlib.suppress(Exception):
        out["sqlite"] = sqlite3.sqlite_version
    with contextlib.suppress(Exception):
        st = C.update_state() or {}
        out["channel"] = st.get("channel")
    return out


def doctor_part() -> dict:
    from . import doctor
    return doctor.report(doctor.run_checks())


def status_part() -> dict:
    from . import status
    return status.gather()


def audit_part() -> dict:
    from . import audit
    found, problem = audit.run_audit()
    return {"config": problem, "findings": [f.to_json() for f in found]}


def config_parts() -> tuple[dict, dict]:
    from . import confcheck
    data, problem = C.load_config()
    if data is None:
        return {"problem": problem}, {"problem": problem}
    found = confcheck.validate(data)
    return ({"problem": problem, "shape": shape(data)},
            {"problem": problem, "errors": sum(1 for f in found if f.level == "error"), "warnings": sum(1 for f in found if f.level == "warn"),
             "findings": [{"path": f.path, "level": f.level, "message": f.message} for f in found]})


def engine_part() -> dict:
    from engine import fallback, models
    refs = [models.default_model(), *models.fallbacks(), *models.models()]
    seen: list[str] = []
    for r in refs:
        if r and r not in seen:
            seen.append(r)
    keys = {}
    for r in seen:
        with contextlib.suppress(Exception):
            keys[r] = {"key": bool(models.has_key(r)), "plan": models.subscription(r)}
    return {"chatModel": models.default_model() or None, "fallbacks": models.fallbacks(), "aliases": models.aliases(), "models": keys,
            "cooling": fallback.status()}


def databases_part(cfg: dict) -> dict:
    from . import doctor
    out = {}
    for p in doctor.sqlite_files(cfg):
        ok, msg = C.quick_check(p)
        info: dict = {"bytes": p.stat().st_size, "quickCheck": "ok" if ok else msg[:300]}
        wal = p.with_name(p.name + "-wal")
        if wal.exists():
            info["walBytes"] = wal.stat().st_size
        try:
            conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=5)
            try:
                names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
                info["journalMode"] = conn.execute("PRAGMA journal_mode").fetchone()[0]
                info["tables"] = {}
                for n in names:
                    with contextlib.suppress(sqlite3.Error):  # FTS 的影子表、坏了的表：数不了就不数
                        info["tables"][n] = conn.execute(f'SELECT COUNT(*) FROM "{n}"').fetchone()[0]
            finally:
                conn.close()
        except sqlite3.Error as e:
            info["error"] = str(e)[:200]
        out[C.tilde(p)] = info
    return out


def usage_part() -> dict:
    from engine import usage
    return usage.summary(7)


# —— 日志里的信号 ——————————————————————————————————————————————————————————————

JOURNAL = re.compile(r"^(\d{4}-\d\d-\d\dT[\d:]+[+-]\d{2}:?\d{2})\s+\S+\s+[^:]+:\s?(.*)$")  # short-iso：时间 主机 单元[pid]: 原文
TAG = re.compile(r"^\[([A-Za-z][\w-]{0,30})\]")
LEVEL = re.compile(r"^(DEBUG|INFO|WARNING|ERROR|CRITICAL):")
ACCESS = re.compile(r'"(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS) (/[^\s?"]*)\S* HTTP/[\d.]+" (\d{3})')
FRAME = re.compile(r'^\s+File "([^"]+)", line (\d+), in (\S+)')
EXC = re.compile(r"\b([A-Z][A-Za-z0-9_]*(?:Error|Exception|Warning|Exit|Interrupt|Timeout|Refused|Failed))\b")
EXC_LINE = re.compile(r"^([A-Za-z_][\w.]*)(?::|$)")


def _where(path: str) -> str:
    """Traceback 里的文件 → 仓库里的相对路径（server/chat.py），装的包 → <lib>/包/模块，别的只留文件名。"""
    p = path.replace("\\", "/")
    for mark in ("/site-packages/", "/dist-packages/"):
        if mark in p:
            return "<lib>/" + p.split(mark, 1)[1]
    for mark in ("/server/", "/tree/", "/packs/"):
        if mark in p:
            return mark.strip("/") + "/" + p.rsplit(mark, 1)[1]
    return Path(p).name


def signals(lines: list[str]) -> dict:
    """日志的行 → 信号（见文件开头）。原文不留。"""
    tags: dict[str, collections.Counter] = {}
    levels: collections.Counter = collections.Counter()
    http: collections.Counter = collections.Counter()
    routes: collections.Counter = collections.Counter()
    traces: collections.OrderedDict = collections.OrderedDict()
    first = last = None
    frames: list[str] | None = None
    for raw in lines:
        line = raw.rstrip("\n")
        m = JOURNAL.match(line)
        if m:
            first = first or m.group(1)
            last = m.group(1)
            line = m.group(2)
        if line.startswith("Traceback (most recent call last)"):
            frames = []
            continue
        if frames is not None:
            fm = FRAME.match(line)
            if fm:
                frames.append(f"{_where(fm.group(1))}:{fm.group(2)}:{fm.group(3)}")
                continue
            if line.startswith((" ", "\t")) or not line.strip():
                continue  # 代码那一行、空行
            em = EXC_LINE.match(line)
            sig = (em.group(1) if em else "?", tuple(frames[-6:]))
            traces[sig] = traces.pop(sig, 0) + 1  # 最近出现的排后面
            frames = None
            continue
        if lm := LEVEL.match(line):
            levels[lm.group(1)] += 1
        if am := ACCESS.search(line):
            http[am.group(3)[0] + "xx"] += 1
            segs = [s for s in am.group(2).split("/") if s][:2]
            routes[f"{am.group(1)} /{'/'.join(segs)}" + ("/…" if len(am.group(2).strip("/").split("/")) > 2 else "")] += 1
            continue
        if tm := TAG.match(line):
            c = tags.setdefault(tm.group(1), collections.Counter())
            c["lines"] += 1
            for e in set(EXC.findall(line)):
                c[e] += 1
    recent = list(traces.items())[-10:]
    return {"lines": len(lines), "from": first, "to": last, "levels": dict(levels), "http": dict(http),
            "routes": dict(routes.most_common(20)), "tags": {k: dict(v) for k, v in sorted(tags.items())},
            "tracebacks": [{"exception": sig[0], "frames": list(sig[1]), "count": n} for sig, n in reversed(recent)]}


def log_lines(n: int) -> tuple[str, list[str]]:
    """服务的日志最后 n 行 → (从哪读的, 行)。"""
    from . import logs
    if C.manager() == "systemd":
        r = C.run(["journalctl", "--user", "-u", C.SERVER.unit, "--no-pager", "-o", "short-iso", "-n", str(n)], timeout=60)
        if r.returncode == 0:
            return "journalctl", r.stdout.splitlines()
    p = logs.log_file(C.SERVER)
    if p.is_file():
        return C.tilde(p), logs.tail_lines(p, n)
    return "none", []


def logs_part(n: int) -> dict:
    src, lines = log_lines(n)
    return {"source": src, **signals(lines)}


def update_part() -> dict:
    out: dict = {"check": C.update_state()}
    last = C.state_dir() / "update" / "last.json"
    with contextlib.suppress(OSError, ValueError):
        out["last"] = json.loads(last.read_text(encoding="utf8"))
    logs_dir = C.state_dir() / "update" / "logs"
    out["logs"] = sorted(p.name for p in logs_dir.glob("update-*.log"))[-10:] if logs_dir.is_dir() else []
    return out


README = """OpenMousse diagnostics (format {fmt}), made {at}.

For whoever is helping with a problem. Every file is JSON:
  summary.json       version, commit, Python, OS (no host name), service manager, which commands are installed
  doctor.json        mousse doctor (checks only, nothing repaired)
  status.json        mousse status
  audit.json         mousse security audit (checks only)
  config-shape.json  the shape of server.json: every key, but only safe values (model references, claw kind, language,
                     time zone, switches and numbers); other strings are markers like <str:12>, <url>, <set>
  config-check.json  mousse config validate
  engine.json        main model, fallbacks, aliases, whether each model has a key (never the key), cooldowns
  databases.json     each SQLite file: size, quick_check, rows per table (no contents read)
  usage.json         tokens and spend over the last 7 days, summed by day, model and purpose
  logs.json          signals from the last {lines} log lines: counts by [module] and level, HTTP status classes, error types and
                     the code lines they came from. No log line is copied (lines can hold bits of conversations).
  update.json        the last update check and receipt

Left out on purpose: conversations, messages, memory, log text, keys, tokens, email addresses. Every file was scrubbed (key-like
strings and this machine's real tokens and keys), emails became <email>, and the home folder and user name in paths became ~.
{dropped}"""


def collect(lines: int = LINES) -> tuple[dict[str, dict], list[str]]:
    """→ ({文件名: 内容}, 没放进来的和为什么)。一份出错不拦着别的（那一份写成 {"error": …}）。"""
    cfg = C.cfg()
    words = personal(cfg)
    parts = {"summary.json": summary, "doctor.json": doctor_part, "status.json": status_part, "audit.json": audit_part,
             "engine.json": engine_part, "databases.json": lambda: databases_part(cfg), "usage.json": usage_part,
             "logs.json": lambda: logs_part(lines), "update.json": update_part}
    out: dict[str, dict] = {}
    for name, fn in parts.items():
        try:
            out[name] = clean(fn(), words)
        except Exception as e:  # noqa: BLE001
            out[name] = {"error": clean_text(f"{type(e).__name__}: {e}", words)[:300]}
    try:
        shape_, check = config_parts()
        out["config-shape.json"], out["config-check.json"] = clean(shape_, words), clean(check, words)
    except Exception as e:  # noqa: BLE001
        out["config-shape.json"] = out["config-check.json"] = {"error": clean_text(f"{type(e).__name__}: {e}", words)[:300]}
    dropped = []
    secrets = [s for s in C.known_secrets() if len(s) >= 8]
    for name in list(out):
        text = json.dumps(out[name], ensure_ascii=False)
        if any(s in text for s in secrets) or EMAIL.search(text) or TAILNET.search(text) or any(p.search(text) for p, _ in words):
            # 最后再查一遍：打码漏了的那一份不放
            del out[name]
            dropped.append(name)
    return out, dropped


def write(out_path: Path, docs: dict[str, dict], dropped: list[str], lines: int) -> Path:
    note = (f"Not included: {', '.join(dropped)} (failed the last leak check; please report this).\n" if dropped else "")
    readme = README.format(fmt=FORMAT, at=datetime.now(timezone.utc).isoformat(timespec="seconds"), lines=lines, dropped=note)
    tmp = out_path.with_name(out_path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "wb") as f, zipfile.ZipFile(f, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("README.txt", readme)
            for name, doc in docs.items():
                z.writestr(name, json.dumps(doc, ensure_ascii=False, indent=2) + "\n")
        os.replace(tmp, out_path)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise
    return out_path


def target(out: str | None) -> Path:
    """--out：.zip 结尾 = 文件，别的当目录；不写 = server.json 旁边的 diagnostics/。"""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    name = f"openmousse-diagnostics-{stamp}.zip"
    if out and out.endswith(".zip"):
        p = Path(out).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
    d = Path(out).expanduser() if out else C.state_dir() / "diagnostics"
    d.mkdir(parents=True, exist_ok=True)
    if not out:
        with contextlib.suppress(OSError):
            os.chmod(d, 0o700)
    p, n = d / name, 1
    while p.exists():
        p, n = d / f"openmousse-diagnostics-{stamp}-{n}.zip", n + 1
    return p


def main(a: argparse.Namespace) -> int:
    n = a.lines if a.lines is not None else LINES
    if n < 0:
        C.say(L("--lines 不能是负数", "--lines can't be negative"))
        return 2
    docs, dropped = collect(n)
    if a.print:
        for name, doc in docs.items():
            print(f"===== {name}")
            print(json.dumps(doc, ensure_ascii=False, indent=2))
        if dropped:
            print(L(f"（没放：{', '.join(dropped)}：最后一遍查出还有像令牌或邮箱的字）", f"(left out: {', '.join(dropped)}: the last check still "
                    "found something like a token or an email)"), file=sys.stderr)
        return 0
    try:
        p = write(target(a.out), docs, dropped, n)
    except OSError as e:
        C.say(L(f"✗ 写不了：{e}", f"✗ Can't write it: {e}"))
        return 1
    C.say(L(f"✓ 打好了：{C.tilde(p)}（{C.human_size(p.stat().st_size)}）", f"✓ Written: {C.tilde(p)} ({C.human_size(p.stat().st_size)})"))
    C.say(L("  里面没有对话、消息、记忆、日志原文、密钥、令牌、邮箱；发给别人之前可以先看一眼（unzip -l、unzip -p 文件 README.txt）。",
            "  It holds no conversations, messages, memory, log text, keys, tokens or emails; have a look before you send it (unzip -l, "
            "unzip -p FILE README.txt)."))
    if dropped:
        C.say(L(f"  ! 没放 {', '.join(dropped)}：最后一遍查出还有像令牌或邮箱的字（请报告这个问题）",
                f"  ! Left out {', '.join(dropped)}: the last check still found something like a token or an email (please report this)"))
    return 0
