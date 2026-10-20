#!/usr/bin/env python3
"""OpenMousse 节点主机（第 8 批，2026-10-19，13.3）：在你自己的电脑（Mac、Linux）上跑的小程序，把这台电脑当成 OpenMousse 的一个「节点」：
Agent 经服务器叫它跑命令、看和读写你给出来的文件夹、截屏、弹通知（服务器那一层见 nodes.py，Agent 的工具见 engine/nodes.py）。
只用标准库、单文件，macOS 自带的 python3（3.9）就能跑；不导入服务器的任何模块（照 engine/acp.py）：拷到哪台电脑上都能用。
它总是自己连服务器（长轮询），这台电脑上不开端口。

  python3 node_host.py pair '<配对链接>' [--name 名字]   拿节点配对码换一把节点令牌（服务器上 tokens.py pair --node 出的；
                                                         存在 ~/.openmousse-node/node.json，文件 0600、文件夹 0700）
  python3 node_host.py run                               前台跑：连上服务器、报能做什么、等活、干活（Ctrl-C 停）
  python3 node_host.py install                           开机自己跑：macOS 用 launchd（ai.openmousse.node），Linux 用 systemd 的
                                                         用户服务（openmousse-node.service；没登录也要跑：loginctl enable-linger）
  python3 node_host.py uninstall                         停掉、删掉上面那个服务（令牌和配置留着；不要了就删 ~/.openmousse-node）
  python3 node_host.py status                            看配置、服务装没装、服务器连不连得上
也挂成 mousse node …（server/mousse.py）。目录能用环境变量 OPENMOUSSE_NODE_HOME 换。

本机的第二道门：~/.openmousse-node/config.json（你自己写；服务器批过的也要过它，照 OpenClaw 的节点本机审批）：
  {"allow": ["/usr/bin/uname ...", "git status"],             跑命令的放行名单（空的 = 不跑命令，也不报 system.run）
   "folders": {"Documents": "~/Documents",                     给出来的文件夹：名字 → 路径（没有 = 不报 fs.*）
               "Code": {"path": "~/code", "write": true}},     write = 也能写（不写只能看、读）
   "notify": true, "screen": false,                            弹通知、截屏（截屏在 macOS 上还要「屏幕录制」权限）
   "computer": false}                                          操作这台 Mac（点、打字、按键；要 screen 也开着、还要「辅助功能」权限）
  allow 的写法同服务器的 host_exec：一条一个命令、按词比；* 不跨 /、** 跨 /、? 一个字；第一个词带 / 的按真实路径比（符号链接指到的也要对得上），
  不带 / 的是 PATH 上的那个程序；最后写 ... = 后面还能跟任意参数。改了这个文件不用重启：下一次等活前它会重新报一遍。
能做的（命令面；服务器那边还要主人批、隐私的还要逐项打开）：
  system.run       不经 shell；程序先在（干净的）PATH 上找成绝对路径，再对本机的名单；环境变量去掉像密钥的、能改程序行为的；有时限；
                   输出留头尾；到点、被取消（服务器说 cancel）整个进程组收掉（SIGTERM，2 秒后 SIGKILL）。照 engine/host_exec.py
  system.which     这几个程序在不在 PATH 上
  fs.list / fs.read / fs.write   只在给出来的文件夹里：路径从文件夹名起；不许 ..、不跟符号链接（openat 一层层走、O_NOFOLLOW）、
                   像密钥的文件和文件夹不碰（.env、*key*、.ssh……，照 engine/host_files.py），写 = 同一个文件夹里先写临时文件再换名；
                   allow 里会跑到的（带 / 的词：程序或参数）和它们所在的子文件夹不改、不新建（照 engine/host_exec.protected：
                   先改写名单里的脚本再跑它 = 不出卡就能跑任意代码）。可写的文件夹里别给 make、npm、pytest、python3 … 这类会读
                   当前目录文件的程序开名单：它们读的文件这里认不出来
  screen.snapshot  macOS：screencapture -x（要「屏幕录制」权限），sips 缩到 maxWidth；Linux：grim / gnome-screenshot / scrot / import
  system.notify    macOS：osascript（标题、正文当参数传，不拼进脚本）；Linux：notify-send
  computer.act     只有 macOS：点（单击、双击、右键）、移过去、滚动、按键、打字。鼠标和按键是 CoreGraphics 事件，经 osascript 的 JXA 发
                   （只要「辅助功能」权限：自己跑给终端，装成服务给 python3）；打字走剪贴板（pbcopy + ⌘V，打完放回原来的字）。
                   坐标是最近一张第 0 块屏幕截屏里的像素（10 分钟内），按截屏时量的屏幕大小换算成点。服务器那边每个动作都要主人点头
断了：1 秒起翻倍、最多 30 秒再连（照 OpenClaw 的节点）；令牌被收回（401）就停下、退出码 0（服务不会一直重启）；服务器上节点没开（404）
每分钟再看一次。关的时候（Ctrl-C、服务停）先跟服务器说一声（bye），正在跑的命令整组收掉。
"""
from __future__ import annotations

import base64
import errno
import fnmatch
import json
import os
import platform as platform_mod
import random
import re
import shlex
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

VERSION = "2026.10.19"
LABEL = "ai.openmousse.node"            # macOS launchd 的标签（别改：卸载 OpenMousse 时认这个名字）
UNIT = "openmousse-node.service"        # Linux systemd 用户服务的名字（同上）
WAIT = 20                               # 长轮询一次等几秒
HTTP_SLACK = 15
BACKOFF_MAX = 30.0
HEAD, TAIL = 8_000, 24_000              # 命令输出留头多少、尾多少字节（同 engine/sandbox.py）
INPUT_MAX = 200_000
READ_BYTES = 20 * 1024 * 1024
READ_LINES, READ_MAX_LINES, READ_CHARS = 400, 2000, 40_000
WRITE_MAX = 2_000_000
IMAGE_MAX = 12 * 1024 * 1024
LIST_MAX = 500
KILL_GRACE = 2.0
SLOTS = threading.Semaphore(4)          # 同时最多跑几个命令
DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 直连服务器：不走环境变量里的代理（同 engine/acp.py）
SECRETS = (".env", ".env.*", "*.env", ".envrc", "secrets", ".secrets", "*secret*", "*key*", "*credential*", "*password*", "*passwd*",
           "*token*", "*oauth*", "auth.json", "*.pem", "*.p12", "*.pfx", "*.jks", "*.keystore", "id_rsa*", "id_dsa*", "id_ecdsa*", "id_ed25519*",
           ".netrc", ".pgpass", ".npmrc", ".pypirc", ".git-credentials", ".ssh", ".gnupg", ".aws", ".kube", ".docker",
           ".openmousse", ".openmousse-node", "keychains")   # 同 engine/host_files.py 的 SECRETS，再加上 OpenMousse 自己的、钥匙串
SECRET_WORDS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL", "CREDS", "AUTH", "BEARER", "DSN", "WEBHOOK", "COOKIE")
DROP_ENV = {"BASH_ENV", "ENV", "IFS", "PS4", "PROMPT_COMMAND", "NODE_OPTIONS", "PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONINSPECT",
            "PERL5OPT", "PERL5LIB", "RUBYOPT", "JAVA_TOOL_OPTIONS", "_JAVA_OPTIONS", "GLIBC_TUNABLES", "GIT_EXTERNAL_DIFF", "GIT_SSH_COMMAND",
            "GIT_ASKPASS", "SSH_ASKPASS", "SUDO_ASKPASS", "LESSOPEN", "LESSCLOSE", "OPENMOUSSE_NODE_HOME"}
DROP_PREFIX = ("LD_", "DYLD_", "BASH_FUNC_", "MOUSSE_", "OPENCLAW_", "CLAUDECODE", "CLAUDE_CODE")   # 同 engine/host_exec.py
URL_CRED = re.compile(r"://[^/\s:@]+:[^/\s@]+@")
FALLBACK_PATH = "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
NONBLOCK = getattr(os, "O_NONBLOCK", 0)
DIR_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)


def home() -> Path:
    return Path(os.environ.get("OPENMOUSSE_NODE_HOME") or "~/.openmousse-node").expanduser()


def log(text: str) -> None:
    print(f"[openmousse-node] {time.strftime('%H:%M:%S')} {text}", file=sys.stderr, flush=True)


def system() -> str:
    """macos / linux / 别的（照小写的 sys.platform）。"""
    return "macos" if sys.platform == "darwin" else "linux" if sys.platform.startswith("linux") else sys.platform


class Failure(Exception):
    """做不了：code 给程序认（回给服务器的 error.code），str 给人看。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


# —— 文件：令牌、配置 ——————————————————————————————————————————————————————————————

def read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_json(path: Path, data: dict, mode: int = 0o600) -> None:
    """先写临时文件再换名（半截的文件不会被当成配置）；文件夹 0700。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    os.chmod(tmp, mode)
    os.replace(tmp, path)


DEFAULT_CONFIG = {
    "_readme": ["The second gate on this computer: the server's approval isn't enough, these must allow it too.",
                "allow: commands the node may run, one pattern each (like engine/host_exec: * within a folder, ** across, ... = any arguments).",
                "folders: name -> folder it may list and read; {\"path\": ..., \"write\": true} lets it write there too.",
                "notify / screen: show notifications / take screenshots (macOS needs Screen Recording permission for screenshots).",
                "computer: let it click, type and press keys on this Mac (needs screen too, and Accessibility permission); each action "
                "still needs your OK in the app.",
                "Changes take effect without a restart."],
    "allow": [],
    "folders": {},
    "notify": True,
    "screen": False,
    "computer": False,
}


class Config:
    """本机的第二道门（config.json）。读不了、写错的项当没写（都是更严的那一边）。"""

    def __init__(self, data: dict) -> None:
        allow = data.get("allow")
        self.allow = [str(x) for x in allow if isinstance(x, str) and x.strip()] if isinstance(allow, list) else []
        self.folders: dict = {}
        folders = data.get("folders")
        for name, v in (folders.items() if isinstance(folders, dict) else []):
            name = str(name).strip()
            if not name or "/" in name or name in (".", "..") or "\x00" in name:
                continue
            path, write = (v, False) if isinstance(v, str) else (v.get("path"), v.get("write") is True) if isinstance(v, dict) else (None, False)
            if isinstance(path, str) and path.strip():
                self.folders[name] = (Path(path).expanduser(), write)
        self.notify = data.get("notify", True) is not False
        self.screen = data.get("screen") is True
        self.computer = data.get("computer") is True

    @staticmethod
    def load() -> Config:
        return Config(read_json(home() / "config.json"))


def config_mtime() -> float:
    try:
        return (home() / "config.json").stat().st_mtime
    except OSError:
        return 0.0


# —— 命令面：照本机的配置和这台电脑上有的程序 ——————————————————————————————————————————————————

def screen_tool() -> list[str] | None:
    """截屏用哪个程序 → 前半截命令（后面接文件名）；没有 → None。"""
    if system() == "macos":
        return ["screencapture", "-x", "-t", "jpg"] if shutil.which("screencapture", path=clean_path()) else None
    for cmd in (["grim"], ["gnome-screenshot", "-f"], ["scrot", "-o"], ["import", "-window", "root"]):
        if shutil.which(cmd[0], path=clean_path()):
            return cmd
    return None


def notify_tool() -> str | None:
    name = "osascript" if system() == "macos" else "notify-send"
    return name if shutil.which(name, path=clean_path()) else None


def surface(cfg: Config) -> list[str]:
    out = ["system.which"]
    if cfg.allow:
        out.append("system.run")
    if cfg.folders:
        out += ["fs.list", "fs.read"]
        if any(w for _, w in cfg.folders.values()):
            out.append("fs.write")
    if cfg.screen and screen_tool():
        out.append("screen.snapshot")
    if cfg.notify and notify_tool():
        out.append("system.notify")
    if cfg.computer and cfg.screen and computer_ready():  # 操作电脑要先截屏看着：截屏也得开着
        out.append("computer.act")
    return out


# —— 文件夹：路径守卫（照 engine/safefs.py、engine/host_files.py）——————————————————————————————————————

def secret_name(name: str) -> bool:
    low = name.lower()
    return any(fnmatch.fnmatchcase(low, p) for p in SECRETS)


def place(cfg: Config, rel: str, write: bool = False) -> tuple[Path, list[str], str]:
    """「文件夹名/相对路径」→ (那个文件夹的根, 往下的一层层, 文件夹名)。不许 ..、像密钥的、写只在 write 的文件夹里。"""
    rel = str(rel or "").strip().strip("/")
    if "\x00" in rel:
        raise Failure("INVALID_REQUEST", "the path has an invalid character")
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    if not parts:
        raise Failure("INVALID_REQUEST", "give a path that starts with a shared folder's name")
    if ".." in parts:
        raise Failure("PATH_DENIED", "no .. in paths: only inside the shared folders")
    name, rest = parts[0], parts[1:]
    if name not in cfg.folders:
        raise Failure("PATH_DENIED", f"there's no shared folder called {name} (shared: {', '.join(sorted(cfg.folders)) or 'none'})")
    root, can_write = cfg.folders[name]
    if write and not can_write:
        raise Failure("PATH_DENIED", f"{name} is shared read-only")
    hit = next((p for p in rest if secret_name(p)), None)
    low = [p.lower() for p in rest]  # macOS 默认不分大小写：.GIT/hooks、.Git/config 就是 .git 里的（审查）
    if hit is None and len(low) >= 2 and low[-1] == "config" and ".git" in low[:-1]:
        hit = ".git/config"
    if hit is not None:
        raise Failure("PATH_DENIED", f"{hit} looks like a secret or credential, so it's off-limits")
    if write and ".git" in low:
        raise Failure("PATH_DENIED", "nothing inside .git is changed")
    if not root.is_dir():
        raise Failure("NOT_FOUND", f"the shared folder {name} isn't there ({root})")
    return root, rest, name


def guarded(cfg: Config, target: Path) -> bool:
    """fs.write 写之前问：target 是不是 allow 里会跑到的（带 / 的词：程序或参数；* 不跨 /、** 跨 /），或者在这些脚本所在的子文件夹里
    （共享文件夹的根不整个护住，只护对得上的文件；同 engine/host_exec.protected）。路径和它的真实路径都比；macOS 默认不分大小写：一律按小写比。"""
    paths = {str(target).lower(), os.path.realpath(target).lower()}
    roots = {str(r).lower() for r, _ in cfg.folders.values()} | {os.path.realpath(r).lower() for r, _ in cfg.folders.values()}
    for text in cfg.allow:
        try:
            toks = shlex.split(text)
        except ValueError:
            continue
        for t in toks:
            if "/" not in t:
                continue
            t = os.path.expanduser(t).lower()
            if any(glob_rx(t).fullmatch(p) for p in paths):
                return True
            lit = re.split(r"[*?]", t, maxsplit=1)[0]
            d = lit[: lit.rfind("/")].rstrip("/")
            if d and d not in roots and any(d.startswith(r.rstrip("/") + "/") for r in roots) and any(p == d or p.startswith(d + "/") for p in paths):
                return True
    return False


def _child(fd: int, name: str, create: bool = False) -> int:
    try:
        return os.open(name, DIR_FLAGS | NOFOLLOW, dir_fd=fd)
    except FileNotFoundError:
        if not create:
            raise
        try:
            os.mkdir(name, 0o777, dir_fd=fd)
        except FileExistsError:
            pass
        return os.open(name, DIR_FLAGS | NOFOLLOW, dir_fd=fd)
    except OSError as e:
        if e.errno not in (errno.ELOOP, errno.ENOTDIR, errno.EMLINK):
            raise
        st = os.stat(name, dir_fd=fd, follow_symlinks=False)
        if stat.S_ISLNK(st.st_mode):
            raise Failure("PATH_DENIED", f"{name} is a symbolic link; links aren't followed") from None
        raise Failure("INVALID_REQUEST", f"{name} isn't a folder") from None


def open_dir(root: Path, parts: list[str], create: bool = False) -> int:
    """一层层往下走（openat、O_NOFOLLOW）：路上哪一层是符号链接就停。→ 那一层目录的 fd（调用方关）。"""
    fd = os.open(str(root), DIR_FLAGS)
    try:
        for name in parts:
            nxt = _child(fd, name, create)
            os.close(fd)
            fd = nxt
        return fd
    except BaseException:
        os.close(fd)
        raise


def fs_list(cfg: Config, params: dict) -> dict:
    rel = str(params.get("path") or "").strip().strip("/")
    if not rel:  # 没写：列出给出来的文件夹
        return {"path": ".", "entries": [{"name": n, "kind": "d", **({"write": True} if w else {})} for n, (_, w) in sorted(cfg.folders.items())]}
    root, parts, _ = place(cfg, rel)
    try:
        fd = open_dir(root, parts)
    except FileNotFoundError:
        raise Failure("NOT_FOUND", f"{rel}: no such folder") from None
    except NotADirectoryError:
        raise Failure("INVALID_REQUEST", f"{rel} isn't a folder") from None
    try:
        rows = []
        with os.scandir(fd) as it:
            for e in it:
                if secret_name(e.name) and not e.name.startswith(".git"):
                    rows.append({"name": e.name, "kind": "x"})
                    continue
                try:
                    st = e.stat(follow_symlinks=False)
                except OSError:
                    continue
                kind = "d" if stat.S_ISDIR(st.st_mode) else "f" if stat.S_ISREG(st.st_mode) else "l" if stat.S_ISLNK(st.st_mode) else "o"
                rows.append({"name": e.name, "kind": kind, **({"size": st.st_size} if kind == "f" else {}), "mtime": int(st.st_mtime)})
    finally:
        os.close(fd)
    rows.sort(key=lambda r: (r["kind"] != "d", r["name"].lower()))
    return {"path": rel, "entries": rows[:LIST_MAX], **({"truncated": True} if len(rows) > LIST_MAX else {})}


def _sniff(data: bytes) -> str | None:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def fs_read(cfg: Config, params: dict) -> dict:
    rel = str(params.get("path") or "")
    root, parts, _ = place(cfg, rel)
    if not parts:
        raise Failure("INVALID_REQUEST", f"{rel} is a folder; use fs.list")
    try:
        d = open_dir(root, parts[:-1])
    except FileNotFoundError:
        raise Failure("NOT_FOUND", f"{rel}: no such file") from None
    try:
        try:
            fd = os.open(parts[-1], os.O_RDONLY | NOFOLLOW | NONBLOCK | getattr(os, "O_CLOEXEC", 0), dir_fd=d)
        except FileNotFoundError:
            raise Failure("NOT_FOUND", f"{rel}: no such file") from None
        except OSError as e:
            if e.errno == errno.ELOOP:
                raise Failure("PATH_DENIED", f"{parts[-1]} is a symbolic link; links aren't followed") from None
            raise
    finally:
        os.close(d)
    with os.fdopen(fd, "rb") as f:
        st = os.fstat(f.fileno())
        if stat.S_ISDIR(st.st_mode):
            raise Failure("INVALID_REQUEST", f"{rel} is a folder; use fs.list")
        if not stat.S_ISREG(st.st_mode):
            raise Failure("INVALID_REQUEST", f"{rel} isn't a regular file")
        data = f.read(READ_BYTES)
    kind = _sniff(data[:16])
    if kind:
        if len(data) > IMAGE_MAX:
            raise Failure("TOO_LARGE", f"{rel} is an image of {st.st_size} bytes, too large to send")
        return {"path": rel, "format": kind, "base64": base64.b64encode(data).decode(), "size": st.st_size}
    if b"\x00" in data[:8192]:
        return {"path": rel, "text": f"(binary file, {st.st_size} bytes)", "size": st.st_size}
    text = data.decode("utf-8", "replace")
    lines = text.splitlines()
    try:
        offset = max(1, int(params.get("offset") or 1))
        limit = max(1, min(int(params.get("limit") or READ_LINES), READ_MAX_LINES))
    except (TypeError, ValueError):
        offset, limit = 1, READ_LINES
    chunk = lines[offset - 1: offset - 1 + limit]
    out, used, shown = [], 0, 0
    for i, line in enumerate(chunk, start=offset):
        row = f"{i}\t{line[:2000]}"
        if used + len(row) > READ_CHARS:
            break
        out.append(row)
        used += len(row) + 1
        shown += 1
    nxt = offset + shown
    more = nxt <= len(lines) or len(data) >= READ_BYTES
    return {"path": rel, "text": "\n".join(out), "size": st.st_size, "lines": len(lines), **({"truncated": True, "next": nxt} if more else {})}


def fs_write(cfg: Config, params: dict) -> dict:
    rel = str(params.get("path") or "")
    content = params.get("content")
    if not isinstance(content, str):
        raise Failure("INVALID_REQUEST", "content must be text")
    data = content.encode("utf-8")
    if len(data) > WRITE_MAX:
        raise Failure("TOO_LARGE", f"at most {WRITE_MAX} bytes per write")
    root, parts, _ = place(cfg, rel, write=True)
    if not parts:
        raise Failure("INVALID_REQUEST", "give a file path inside the folder")
    if guarded(cfg, root.joinpath(*parts)):
        raise Failure("PATH_DENIED", f"{rel.strip('/')} is on this computer's command allowlist (it gets run as a program), so it isn't "
                      "changed from here; edit it on the computer itself")
    name = parts[-1]
    d = open_dir(root, parts[:-1], create=True)
    try:
        try:
            old = os.stat(name, dir_fd=d, follow_symlinks=False)
        except FileNotFoundError:
            old = None
        if old is not None and stat.S_ISLNK(old.st_mode):
            raise Failure("PATH_DENIED", f"{name} is a symbolic link; links aren't followed")
        if old is not None and not stat.S_ISREG(old.st_mode):
            raise Failure("INVALID_REQUEST", f"{name} isn't a regular file")
        tmp = f".{name[:40]}.tmp-{uuid.uuid4().hex[:8]}"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | NOFOLLOW | getattr(os, "O_CLOEXEC", 0), 0o666, dir_fd=d)
        try:
            with os.fdopen(fd, "wb") as f:
                if old is not None:
                    os.fchmod(f.fileno(), stat.S_IMODE(old.st_mode))
                f.write(data)
            os.rename(tmp, name, src_dir_fd=d, dst_dir_fd=d)
        except BaseException:
            try:
                os.unlink(tmp, dir_fd=d)
            except OSError:
                pass
            raise
    finally:
        os.close(d)
    return {"path": rel, "bytes": len(data), "created": old is None}


# —— 跑命令（照 engine/host_exec.py：不经 shell、干净的环境、进程组、留头尾）——————————————————————————————————

def clean_path() -> str:
    p = os.pathsep.join(x for x in os.environ.get("PATH", "").split(os.pathsep) if x.startswith("/"))
    return p or FALLBACK_PATH


def child_env() -> dict:
    out = {}
    for k, v in os.environ.items():
        up = k.upper()
        if any(w in up for w in SECRET_WORDS) or up in DROP_ENV or up.startswith(DROP_PREFIX) or URL_CRED.search(v or ""):
            continue
        out[k] = v
    out["PATH"] = clean_path()
    out.setdefault("PYTHONIOENCODING", "utf-8")
    return out


def glob_rx(pat: str):
    """* 不跨 /、** 跨 /、? 一个字（不是 /），别的照字面（同 engine/host_exec.glob_rx）。"""
    out = []
    for tok in re.findall(r"\*\*|[*?]|[^*?]+", pat):
        out.append(".*" if tok == "**" else "[^/]*" if tok == "*" else "[^/]" if tok == "?" else re.escape(tok))
    return re.compile("".join(out), re.S)


def _abs(arg: str, cwd: Path) -> str:
    return os.path.normpath(os.path.join(str(cwd), os.path.expanduser(arg)))


def allowed(cfg: Config, argv: list[str], exe: str, cwd: Path) -> str | None:
    """本机名单里对得上的那一条（同 engine/host_exec.matches：带 / 的位置按真实路径比，链接指到的也要对得上）。"""
    real = os.path.realpath(exe)
    for text in cfg.allow:
        try:
            toks = shlex.split(text)
        except ValueError:
            continue
        rest = bool(toks) and toks[-1] in ("...", "…")
        toks = [os.path.expanduser(t) if t.startswith("~") else t for t in (toks[:-1] if rest else toks)]
        n = len(toks)
        if not toks or len(argv) < n or (not rest and len(argv) != n):
            continue
        t0 = toks[0]
        if "/" in t0:
            if not (glob_rx(t0).fullmatch(exe) and glob_rx(t0).fullmatch(real)):
                continue
        elif "/" in argv[0] or not glob_rx(t0).fullmatch(argv[0]):
            continue
        ok = True
        for t, a in zip(toks[1:], argv[1:n]):
            if "/" in t:
                a = _abs(a, cwd)
                ok = bool(glob_rx(t).fullmatch(a) and glob_rx(t).fullmatch(os.path.realpath(a)))
            else:
                ok = bool(glob_rx(t).fullmatch(a))
            if not ok:
                break
        if ok:
            return text
    return None


def resolve(cfg: Config, params: dict) -> tuple[list[str], str, Path]:
    """→ (argv, 要 exec 的程序的绝对路径, 工作目录)。工作目录：不写 = 家目录；写了 = 给出来的文件夹里的一个（不跟链接）。"""
    argv = params.get("argv")
    if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv) or not argv[0].strip():
        raise Failure("INVALID_REQUEST", "argv must be a non-empty list of strings")
    if any("\x00" in a for a in argv) or sum(len(a) for a in argv) > 100_000:
        raise Failure("INVALID_REQUEST", "the command is invalid or too long")
    rel = str(params.get("cwd") or "").strip()
    if rel:
        root, parts, _ = place(cfg, rel)
        try:
            os.close(open_dir(root, parts))
        except FileNotFoundError:
            raise Failure("NOT_FOUND", f"there's no folder {rel}") from None
        cwd = root.joinpath(*parts)
    else:
        cwd = Path.home()
    a0 = argv[0]
    if "/" in a0:
        exe = _abs(a0, cwd)
        if not (os.path.isfile(exe) and os.access(exe, os.X_OK)):
            raise Failure("NOT_FOUND", f"{a0} isn't an executable program")
    else:
        found = shutil.which(a0, path=clean_path())
        if not found:
            raise Failure("NOT_FOUND", f"{a0} isn't on this computer's PATH")
        exe = os.path.abspath(found)
    return argv, exe, cwd


class Capture:
    """边读边截：留头 HEAD、尾 TAIL 字节，中间只记数（同 engine/sandbox.Capture）。"""

    def __init__(self) -> None:
        self.head, self.tail, self.size = bytearray(), bytearray(), 0

    def add(self, b: bytes) -> None:
        self.size += len(b)
        room = HEAD - len(self.head)
        if room > 0:
            self.head += b[:room]
            b = b[room:]
        if b:
            self.tail += b
            if len(self.tail) > TAIL:
                del self.tail[: len(self.tail) - TAIL]

    @property
    def cut(self) -> int:
        return self.size - len(self.head) - len(self.tail)

    def text(self) -> str:
        if self.cut <= 0:
            return (bytes(self.head) + bytes(self.tail)).decode("utf-8", "replace")
        return f"{self.head.decode('utf-8', 'replace')}\n[… {self.cut} bytes cut from the middle …]\n{self.tail.decode('utf-8', 'replace')}"


RUNNING: dict = {}            # 活的 id → 它起的进程（服务器说 cancel 就整组收掉）
CANCELLED: set = set()
_lock = threading.Lock()


def _killpg(proc, sig: int) -> None:
    try:
        os.killpg(proc.pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def stop_proc(proc) -> None:
    """整组收掉：SIGTERM，KILL_GRACE 秒后还在就 SIGKILL。"""
    _killpg(proc, signal.SIGTERM)
    try:
        proc.wait(KILL_GRACE)
    except subprocess.TimeoutExpired:
        _killpg(proc, signal.SIGKILL)
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            pass


def system_run(cfg: Config, params: dict, iid: str) -> dict:
    argv, exe, cwd = resolve(cfg, params)
    hit = allowed(cfg, argv, exe, cwd)
    if hit is None:
        raise Failure("SYSTEM_RUN_DENIED", f"not on this computer's own allowlist ({home() / 'config.json'}): {shlex.join(argv)[:200]}")
    stdin = params.get("input") if isinstance(params.get("input"), str) and params.get("input") else None
    if stdin is not None and len(stdin) > INPUT_MAX:
        raise Failure("INVALID_REQUEST", f"standard input is limited to {INPUT_MAX} characters")
    try:
        timeout = max(1.0, min(float(params.get("timeoutMs") or 120_000) / 1000, 3600.0))
    except (TypeError, ValueError):
        timeout = 120.0
    with SLOTS:
        with _lock:
            if iid in CANCELLED:  # 排着等位子的时候服务器说不用了（到点、被停）：不起（审查：位子空出来照样跑完）
                raise Failure("CANCELLED", "stopped before it started: the server said this isn't needed any more")
        t0 = time.monotonic()
        proc = subprocess.Popen([exe, *argv[1:]], cwd=str(cwd), env=child_env(), start_new_session=True,
                                stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        with _lock:
            RUNNING[iid] = proc
            late = iid in CANCELLED
        if late:  # 起进程的那一下里叫停的：cancel() 那时还看不到进程，这里收掉
            threading.Thread(target=stop_proc, args=(proc,), daemon=True).start()
        cap = Capture()

        def pump() -> None:
            while True:
                chunk = proc.stdout.read1(65536) if hasattr(proc.stdout, "read1") else proc.stdout.read(65536)
                if not chunk:
                    break
                cap.add(chunk)

        def feed() -> None:
            try:
                proc.stdin.write((stdin or "").encode("utf-8"))
            except (BrokenPipeError, OSError):
                pass
            finally:
                try:
                    proc.stdin.close()
                except OSError:
                    pass
        reader = threading.Thread(target=pump, daemon=True)
        reader.start()
        if stdin is not None:
            threading.Thread(target=feed, daemon=True).start()
        timed_out = False
        try:
            proc.wait(timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            stop_proc(proc)
        finally:
            with _lock:
                RUNNING.pop(iid, None)
        reader.join(5)
        if iid in CANCELLED:
            raise Failure("CANCELLED", "stopped: the server said this isn't needed any more")
        return {"exitCode": proc.returncode, "timedOut": timed_out, "output": cap.text(), "size": cap.size, "cut": max(0, cap.cut),
                "ms": int((time.monotonic() - t0) * 1000), "program": exe}


def cancel(iid: str) -> None:
    with _lock:
        CANCELLED.add(iid)
        proc = RUNNING.get(iid)
    if proc is not None:
        log(f"stopping {iid} (the server cancelled it)")
        threading.Thread(target=stop_proc, args=(proc,), daemon=True).start()


def system_which(params: dict) -> dict:
    bins = params.get("bins")
    if isinstance(bins, str):
        bins = [bins]
    if not isinstance(bins, list) or not bins or len(bins) > 64:
        raise Failure("INVALID_REQUEST", "bins must be a list of 1 to 64 program names")
    return {"bins": {str(b): (shutil.which(str(b), path=clean_path()) if "/" not in str(b) else None) for b in bins}}


# —— 截屏、通知 ——————————————————————————————————————————————————————————————————

def image_size(data: bytes) -> tuple[int, int] | None:
    """宽、高（只读文件头：PNG、JPEG）。"""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    i = 2
    while data[:3] == b"\xff\xd8\xff" and i + 9 < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        m = data[i + 1]
        if m in (0xD8, 0x01, 0xFF) or 0xD0 <= m <= 0xD7:
            i += 1 if m == 0xFF else 2
            continue
        if m in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            return int.from_bytes(data[i + 7:i + 9], "big"), int.from_bytes(data[i + 5:i + 7], "big")
        i += 2 + int.from_bytes(data[i + 2:i + 4], "big")
    return None


def screen_snapshot(cfg: Config, params: dict) -> dict:
    tool = screen_tool() if cfg.screen else None
    if tool is None:
        raise Failure("UNAVAILABLE", "screenshots aren't on for this computer (screen in config.json), or there's no screenshot program")
    try:
        width = max(320, min(int(params.get("maxWidth") or 1600), 4000))
        screen = max(0, int(params.get("screen") or 0))
    except (TypeError, ValueError):
        width, screen = 1600, 0
    with tempfile.TemporaryDirectory(prefix="openmousse-shot-") as d:
        out = os.path.join(d, "shot.jpg" if system() == "macos" else "shot.png")
        cmd = list(tool)
        if system() == "macos" and screen:
            cmd += ["-D", str(screen + 1)]
        r = subprocess.run(cmd + [out], capture_output=True, timeout=30, env=child_env())
        if r.returncode != 0 or not os.path.isfile(out) or os.path.getsize(out) == 0:
            why = (r.stderr or r.stdout or b"").decode("utf-8", "replace").strip()[:200]
            if system() == "macos":
                raise Failure("PERMISSION_MISSING", "screenRecording: allow Screen Recording for the program running the node "
                                                    "(System Settings → Privacy & Security → Screen Recording)" + (f" ({why})" if why else ""))
            raise Failure("UNAVAILABLE", f"the screenshot failed{': ' + why if why else ''}")
        wide = (image_size(Path(out).read_bytes()[:65536]) or (0, 0))[0] > width
        if system() == "macos" and wide and shutil.which("sips", path=clean_path()):  # 缩到 maxWidth（只缩不放）
            subprocess.run(["sips", "--resampleWidth", str(width), out], capture_output=True, timeout=30, env=child_env())
        data = Path(out).read_bytes()
    if len(data) > IMAGE_MAX:
        raise Failure("TOO_LARGE", "the screenshot is too large to send")
    fmt = _sniff(data[:16]) or "jpeg"
    w, h = image_size(data[:65536]) or (0, 0)
    if screen == 0 and cfg.computer:  # 操作电脑的坐标按这一张换算
        remember_shot(w, h)
    return {"format": fmt, "base64": base64.b64encode(data).decode(), "width": w, "height": h}


def system_notify(cfg: Config, params: dict) -> dict:
    tool = notify_tool() if cfg.notify else None
    if tool is None:
        raise Failure("UNAVAILABLE", "notifications aren't on for this computer, or there's no notification program")
    title = " ".join(str(params.get("title") or "").split())[:120]
    body = str(params.get("body") or "").strip()[:1000]
    if not title and not body:
        raise Failure("INVALID_REQUEST", "empty notification")
    if tool == "osascript":  # 标题、正文当参数传：不拼进 AppleScript（里面有引号也不会变成脚本）
        cmd = ["osascript", "-e", "on run argv", "-e", "display notification (item 2 of argv) with title (item 1 of argv)", "-e", "end run",
               title or "OpenMousse", body or title]
    else:
        cmd = ["notify-send", "--app-name=OpenMousse", "--", title or "OpenMousse", body]
    r = subprocess.run(cmd, capture_output=True, timeout=20, env=child_env())
    if r.returncode != 0:
        raise Failure("UNAVAILABLE", (r.stderr or b"").decode("utf-8", "replace").strip()[:200] or "the notification failed")
    return {"shown": True}


# —— 操作电脑（macOS，computer.act）————————————————————————————————————————————————————————————
# 鼠标、按键都是 CoreGraphics 的事件（CGEventPost），经 osascript 的 JavaScript（JXA）发：只要「辅助功能」一项权限，不用再给「控制
# System Events」。打字走剪贴板（pbcopy + ⌘V，中文也行），打完把剪贴板里原来的字放回去。坐标是最近一张截屏（第 0 块屏幕）里的像素，
# 按那时量的屏幕大小（点）换算：所以先截屏、再动手；截屏过了 10 分钟或者没截过就不动。每个动作服务器那边都要主人点头（engine/nodes.py）。

JXA = """ObjC.import('CoreGraphics'); ObjC.import('AppKit');
function trusted() { try { ObjC.import('ApplicationServices'); return $.AXIsProcessTrusted(); } catch (e) { return true; } }
function mouse(type, x, y, button, clicks) {
  var e = $.CGEventCreateMouseEvent(null, type, {x: x, y: y}, button);
  if (clicks > 0) $.CGEventSetIntegerValueField(e, 1, clicks);
  $.CGEventPost(0, e);
}
function key(code, flags, down) {
  var e = $.CGEventCreateKeyboardEvent(null, code, down);
  $.CGEventSetFlags(e, flags);
  $.CGEventPost(0, e);
}
function run(argv) {
  var op = argv[0];
  if (op === 'points') { var f = $.NSScreen.screens.objectAtIndex(0).frame; return f.size.width + ' ' + f.size.height; }
  if (!trusted()) return 'untrusted';
  if (op === 'key') {
    key(Number(argv[1]), Number(argv[2]), true); delay(0.02); key(Number(argv[1]), Number(argv[2]), false);
    return 'ok';
  }
  var x = Number(argv[1]), y = Number(argv[2]);
  mouse(5, x, y, 0, 0);
  if (op === 'move') return 'ok';
  if (op === 'scroll') {
    delay(0.05);
    var dy = Number(argv[3]), dx = Number(argv[4]), e;
    try { e = $.CGEventCreateScrollWheelEvent2(null, 1, 2, dy, dx, 0); } catch (err) { e = $.CGEventCreateScrollWheelEvent(null, 1, 2, dy, dx); }
    $.CGEventPost(0, e);
    return 'ok';
  }
  var right = op === 'right_click', n = op === 'double_click' ? 2 : 1;
  for (var i = 1; i <= n; i++) {
    delay(0.05);
    mouse(right ? 3 : 1, x, y, right ? 1 : 0, i);
    mouse(right ? 4 : 2, x, y, right ? 1 : 0, i);
  }
  return 'ok';
}"""

COMPUTER_ACTS = ("click", "double_click", "right_click", "move", "type", "key", "scroll")
SHOT_TTL = 600  # 截屏多久以后坐标就不认了（秒）
MODS = {"cmd": 1 << 20, "command": 1 << 20, "shift": 1 << 17, "ctrl": 1 << 18, "control": 1 << 18, "alt": 1 << 19, "option": 1 << 19}
# 美式键盘上的键码（kVK_*）：字母、数字按键位，别的键盘布局上 ⌘ 加字母按的是同一个位置
KEYCODES = {"a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9, "b": 11, "q": 12, "w": 13, "e": 14, "r": 15,
            "y": 16, "t": 17, "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23, "9": 25, "7": 26, "8": 28, "0": 29, "o": 31, "u": 32,
            "i": 34, "p": 35, "l": 37, "j": 38, "k": 40, "n": 45, "m": 46,
            "return": 36, "enter": 36, "tab": 48, "space": 49, "delete": 51, "backspace": 51, "escape": 53, "esc": 53,
            "forwarddelete": 117, "home": 115, "end": 119, "pageup": 116, "pagedown": 121, "left": 123, "right": 124, "down": 125, "up": 126,
            "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97, "f7": 98, "f8": 100, "f9": 101, "f10": 109, "f11": 103, "f12": 111}
_shot: dict | None = None  # 最近一张第 0 块屏幕的截屏：{w, h, pw, ph, at}（图的像素、屏幕的点）
_shot_lock = threading.Lock()


def computer_ready() -> bool:
    return system() == "macos" and bool(shutil.which("osascript", path=clean_path())) and bool(shutil.which("pbcopy", path=clean_path()))


def jxa(*args) -> str:
    """跑上面那段 JXA，参数当 argv 传（不拼进脚本）→ 它返回的那一行。"""
    try:
        r = subprocess.run(["osascript", "-l", "JavaScript", "-e", JXA, *[str(a) for a in args]], capture_output=True, timeout=20, env=child_env())
    except (OSError, subprocess.TimeoutExpired) as e:
        raise Failure("UNAVAILABLE", f"osascript didn't run: {e}") from None
    out = (r.stdout or b"").decode("utf-8", "replace").strip()
    if r.returncode != 0:
        raise Failure("UNAVAILABLE", (r.stderr or b"").decode("utf-8", "replace").strip()[:200] or "osascript failed")
    if out == "untrusted":
        raise Failure("PERMISSION_MISSING", "accessibility: allow the program running the node (Terminal, or python3 for the service) in "
                                            "System Settings → Privacy & Security → Accessibility")
    return out


def remember_shot(w: int, h: int) -> None:
    """截了第 0 块屏幕：记下图多大、屏幕多大（点），之后的坐标按它换算。量不出屏幕大小就不记（动手时说先截屏）。"""
    global _shot
    pts = None
    if computer_ready() and w and h:
        try:
            pw, ph = (float(v) for v in jxa("points").split())
            pts = (pw, ph) if pw > 0 and ph > 0 else None
        except (Failure, ValueError):
            pts = None
    with _shot_lock:
        _shot = {"w": w, "h": h, "pw": pts[0], "ph": pts[1], "at": time.monotonic()} if pts else None


def to_points(params: dict) -> tuple[int, int]:
    with _shot_lock:
        shot = dict(_shot) if _shot else None
    if not shot or time.monotonic() - shot["at"] > SHOT_TTL:
        raise Failure("COMPUTER_NEEDS_SCREENSHOT", "take a screenshot of screen 0 first (screen.snapshot): x and y are pixels in the latest "
                                                   "one, and it's more than 10 minutes old or there isn't one")
    try:
        x, y = int(params.get("x")), int(params.get("y"))
    except (TypeError, ValueError):
        raise Failure("INVALID_REQUEST", "give x and y (pixels in the latest screenshot)") from None
    if not (0 <= x < shot["w"] and 0 <= y < shot["h"]):
        raise Failure("INVALID_REQUEST", f"x and y must be inside the latest screenshot ({shot['w']}×{shot['h']})")
    return round(x * shot["pw"] / shot["w"]), round(y * shot["ph"] / shot["h"])


def paste_text(text: str) -> None:
    """打字：先记下剪贴板里的字，换成要打的字按 ⌘V，再放回去（剪贴板里原来是图片的话放不回去，留着打的字）。"""
    env = {**child_env(), "LC_CTYPE": "en_US.UTF-8", "LANG": "en_US.UTF-8"}  # pbcopy / pbpaste 按 locale 编码：服务里常常没设
    try:
        old = subprocess.run(["pbpaste"], capture_output=True, timeout=5, env=env).stdout
        subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True, timeout=5, env=env)
    except (OSError, subprocess.SubprocessError) as e:
        raise Failure("UNAVAILABLE", f"the clipboard isn't available: {e}") from None
    jxa("key", KEYCODES["v"], MODS["cmd"])
    time.sleep(0.4)  # 等它贴完再放回去
    if old:
        try:
            subprocess.run(["pbcopy"], input=old, timeout=5, env=env)
        except (OSError, subprocess.SubprocessError):
            pass


def computer_act(params: dict) -> dict:
    act = str(params.get("act") or "").strip().lower()
    if act not in COMPUTER_ACTS:
        raise Failure("INVALID_REQUEST", f"act must be one of: {', '.join(COMPUTER_ACTS)}")
    if act == "type":
        text = str(params.get("text") or "")
        if not text or len(text) > 2000:
            raise Failure("INVALID_REQUEST", "type needs text (at most 2000 characters)")
        paste_text(text)
        return {"act": act, "chars": len(text)}
    if act == "key":
        keys = str(params.get("keys") or "").strip().lower()
        *mods, name = keys.split("+") if keys else [""]
        if name not in KEYCODES or any(m not in MODS for m in mods):
            raise Failure("INVALID_REQUEST", f"unknown keys: {keys or '(none)'} (e.g. cmd+s, enter, tab, esc, up, pagedown, f5)")
        flags = 0
        for m in mods:
            flags |= MODS[m]
        jxa("key", KEYCODES[name], flags)
        return {"act": act, "keys": keys}
    x, y = to_points(params)
    if act == "scroll":
        steps = max(1, min(int(params.get("amount") or 3), 20))
        direction = str(params.get("direction") or "down")
        dy = steps if direction == "up" else -steps if direction == "down" else 0
        dx = steps if direction == "left" else -steps if direction == "right" else 0
        jxa("scroll", x, y, dy, dx)
        return {"act": act, "direction": direction, "amount": steps, "at": [x, y]}
    jxa(act, x, y)
    return {"act": act, "at": [x, y]}


def handle(cfg: Config, inv: dict) -> dict:
    """做一件活 → payload；做不了抛 Failure。只做本机配置里开着的（服务器批了也要过这一关）。"""
    cmd, params, iid = str(inv.get("command") or ""), inv.get("params") if isinstance(inv.get("params"), dict) else {}, str(inv.get("id") or "")
    if cmd not in surface(cfg):
        raise Failure("NOT_ENABLED", f"{cmd} isn't turned on in this computer's own config ({home() / 'config.json'})")
    if cmd == "system.run":
        return system_run(cfg, params, iid)
    if cmd == "system.which":
        return system_which(params)
    if cmd == "fs.list":
        return fs_list(cfg, params)
    if cmd == "fs.read":
        return fs_read(cfg, params)
    if cmd == "fs.write":
        return fs_write(cfg, params)
    if cmd == "screen.snapshot":
        return screen_snapshot(cfg, params)
    if cmd == "system.notify":
        return system_notify(cfg, params)
    if cmd == "computer.act":
        return computer_act(params)
    raise Failure("UNKNOWN_COMMAND", f"this node doesn't know {cmd}")


# —— 跟服务器说话 ————————————————————————————————————————————————————————————————————

class HttpError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"HTTP {status}: {message}")
        self.status = status


class Server:
    def __init__(self, url: str, token: str) -> None:
        self.url, self.token = url.rstrip("/"), token

    def call(self, method: str, path: str, body: dict | None = None, timeout: float = 30) -> dict:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, method=method, headers={
            "Authorization": f"Bearer {self.token}", "Content-Type": "application/json", "Accept": "application/json",
            "User-Agent": f"openmousse-node/{VERSION}"})
        try:
            with DIRECT.open(req, timeout=timeout) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read() or b"{}").get("error") or e.reason
            except (ValueError, AttributeError):
                msg = e.reason
            raise HttpError(e.code, str(msg)) from None
        try:
            out = json.loads(raw or b"{}")
        except ValueError:
            raise HttpError(502, "the server's reply wasn't JSON") from None
        return out if isinstance(out, dict) else {}


def node_file() -> dict:
    return read_json(home() / "node.json")


def node_name() -> str:
    n = str(node_file().get("name") or "").strip()
    if n:
        return n
    h = socket.gethostname().split(".")[0]
    return h.replace("-", " ")[:60] or "computer"


def hello(server: Server, cfg: Config) -> dict:
    body = {"platform": system(), "name": node_name(), "version": VERSION, "commands": surface(cfg),
            "folders": [{"name": n, **({"write": True} if w else {})} for n, (_, w) in sorted(cfg.folders.items())],
            "permissions": {}, "machine": platform_mod.machine()}
    out = server.call("POST", "/api/nodes/hello", body)
    pending = out.get("pending") or {}
    log(f"connected as {out.get('id')}: usable {', '.join(out.get('usable') or []) or 'nothing yet'}"
        + (f"; waiting for approval in the app's inbox: {', '.join(pending.get('commands') or [])}" if pending else ""))
    return out


def post_result(server: Server, iid: str, body: dict) -> None:
    for i in range(3):
        try:
            server.call("POST", "/api/nodes/result", {"id": iid, **body}, timeout=60)
            return
        except HttpError as e:
            if e.status == 404:  # 服务器那边已经不等了（到点、被停）
                return
            log(f"result for {iid} not delivered: {e}")
            return
        except (urllib.error.URLError, OSError) as e:
            log(f"result for {iid} not delivered yet ({e}); retrying")
            time.sleep(1 + i)


def work(server: Server, inv: dict) -> None:
    iid = str(inv.get("id") or "")
    cmd = str(inv.get("command") or "")
    log(f"{iid}: {cmd}")
    try:
        payload = handle(Config.load(), inv)
        body = {"ok": True, "payload": payload}
    except Failure as e:
        body = {"ok": False, "error": {"code": e.code, "message": str(e)}}
    except Exception as e:  # noqa: BLE001 — 给服务器一句原因，这台节点接着干活
        body = {"ok": False, "error": {"code": "NODE_ERROR", "message": f"{type(e).__name__}: {str(e)[:300]}"}}
    finally:
        with _lock:
            CANCELLED.discard(iid)
    post_result(server, iid, body)


class Stop(Exception):
    """收到 SIGTERM / SIGINT：先跟服务器说一声再走。"""


def _stop(signum, frame) -> None:
    raise Stop()


def run(argv: list[str] | None = None) -> int:
    nf = node_file()
    if not nf.get("server") or not nf.get("token"):
        print(f"Not paired yet: run  python3 {Path(__file__).name} pair '<pairing link>'  first (make the link on the server with "
              "tokens.py pair --node).", file=sys.stderr)
        return 2
    server = Server(str(nf["server"]), str(nf["token"]))
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    state = {"said": False, "cfg": -1.0}
    hello_lock = threading.Lock()

    def say_hello() -> None:
        with hello_lock:
            state["cfg"] = config_mtime()
            hello(server, Config.load())
            state["said"] = True

    def watch() -> None:
        """改了 config.json：马上再报一遍（不等这一次长轮询回来）。报不了就交给主循环下一次连上时报。"""
        while True:
            time.sleep(2)
            if state["said"] and config_mtime() != state["cfg"]:
                try:
                    say_hello()
                except Exception as e:  # noqa: BLE001
                    log(f"couldn't report the new config yet ({e})")
                    state["said"] = False
    threading.Thread(target=watch, daemon=True).start()
    backoff, off_noted = 1.0, False
    log(f"node host {VERSION} for {server.url} (config: {home() / 'config.json'})")
    try:
        while True:
            try:
                if not state["said"]:
                    say_hello()
                    off_noted = False
                got = server.call("GET", f"/api/nodes/poll?wait={WAIT}", timeout=WAIT + HTTP_SLACK)
                backoff = 1.0
                for iid in got.get("cancel") or []:
                    cancel(str(iid))
                for inv in got.get("invokes") or []:
                    if isinstance(inv, dict):
                        threading.Thread(target=work, args=(server, inv), daemon=True).start()
            except HttpError as e:
                if e.status == 401:
                    log("the server doesn't accept this node's token any more (it was removed). Pair again to reconnect.")
                    return 0
                if e.status == 404:
                    if not off_noted:
                        log("nodes are turned off on the server (engine.nodes in server.json); checking again every minute")
                        off_noted = True
                    state["said"] = False
                    time.sleep(60)
                    continue
                state["said"] = False  # 409（服务器重启后不认得它了）、别的错：再连上时先报一遍
                log(f"server said {e}; retrying in {backoff:.0f}s")
                time.sleep(backoff * random.uniform(1.0, 1.2))
                backoff = min(backoff * 2, BACKOFF_MAX)
            except (urllib.error.URLError, OSError, ValueError) as e:
                log(f"can't reach the server ({getattr(e, 'reason', None) or e}); retrying in {backoff:.0f}s")
                state["said"] = False
                time.sleep(backoff * random.uniform(1.0, 1.2))
                backoff = min(backoff * 2, BACKOFF_MAX)
    except Stop:
        log("stopping")
    finally:
        with _lock:
            procs = list(RUNNING.values())
        for p in procs:
            stop_proc(p)
        try:
            server.call("POST", "/api/nodes/bye", {"reason": "shutdown"}, timeout=5)
        except Exception:  # noqa: BLE001 — 走的时候连不上也照走
            pass
    return 0


# —— 配对 ————————————————————————————————————————————————————————————————————————

def parse_link(link: str) -> tuple[str, str]:
    """openmousse://pair?s=<服务器>&c=<码>&k=node → (服务器地址, 码)。"""
    u = urllib.parse.urlsplit(link.strip())
    q = urllib.parse.parse_qs(u.query)
    server, code = (q.get("s") or [""])[0].strip().rstrip("/"), (q.get("c") or [""])[0].strip()
    if not re.match(r"^https?://[^\s/]+", server) or not code:
        raise SystemExit("That doesn't look like a pairing link (openmousse://pair?s=…&c=…). Make one on the server: tokens.py pair --node")
    if (q.get("k") or [""])[0] != "node":
        print("Note: this link wasn't made for a node (no k=node); the server will say if it's a phone's code.", file=sys.stderr)
    return server, code


def pair(argv: list[str]) -> int:
    if not argv:
        print("Usage: node_host.py pair '<pairing link>' [--name NAME]", file=sys.stderr)
        return 2
    name = ""
    if "--name" in argv:
        i = argv.index("--name")
        name = argv[i + 1] if i + 1 < len(argv) else ""
        argv = argv[:i] + argv[i + 2:]
    server, code = parse_link(argv[0])
    name = name.strip() or socket.gethostname().split(".")[0].replace("-", " ")
    req = urllib.request.Request(server + "/api/pair", data=json.dumps({"code": code, "device": name, "node": True}).encode("utf-8"),
                                 method="POST", headers={"Content-Type": "application/json", "Accept": "application/json"})
    try:
        with DIRECT.open(req, timeout=30) as r:
            out = json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read() or b"{}").get("error") or e.reason
        except (ValueError, AttributeError):
            msg = e.reason
        print(f"Pairing didn't work: {msg}", file=sys.stderr)
        return 1
    except (urllib.error.URLError, OSError) as e:
        print(f"Can't reach the server at {server}: {getattr(e, 'reason', None) or e}", file=sys.stderr)
        return 1
    if not out.get("ok") or not out.get("token"):
        print(f"Pairing didn't work: {out.get('error') or out}", file=sys.stderr)
        return 1
    write_json(home() / "node.json", {"server": server, "token": out["token"], "id": out.get("name"), "name": name})
    if not (home() / "config.json").exists():
        write_json(home() / "config.json", DEFAULT_CONFIG)
    print(f"Paired as {out.get('name')} with {server}. The token is in {home() / 'node.json'} (only you can read it).")
    print(f"Next: say what this computer may do in {home() / 'config.json'} (commands to allow, folders to share, screenshots),")
    print(f"then run  python3 {Path(__file__).name} run  (or install, to start it with your login) and approve its commands in the app's inbox.")
    return 0


# —— 开机自己跑：launchd / systemd ——————————————————————————————————————————————————————

def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def unit_path() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "systemd" / "user" / UNIT


def program() -> list[str]:
    return [sys.executable or "/usr/bin/python3", str(Path(__file__).resolve()), "run"]


def plist() -> str:
    """launchd 的配置：登录就起；崩了再起（正常退出——比如令牌被收回——不再起）；日志在节点目录里。"""
    from xml.sax.saxutils import escape
    args = "".join(f"\n    <string>{escape(a)}</string>" for a in program())
    env = ""
    if os.environ.get("OPENMOUSSE_NODE_HOME"):
        env = (f"\n  <key>EnvironmentVariables</key>\n  <dict>\n    <key>OPENMOUSSE_NODE_HOME</key>\n"
               f"    <string>{escape(str(home()))}</string>\n  </dict>")
    logf = escape(str(home() / "node.log"))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{LABEL}</string>
  <key>ProgramArguments</key>
  <array>{args}
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <dict>
    <key>SuccessfulExit</key>
    <false/>
  </dict>
  <key>ThrottleInterval</key>
  <integer>10</integer>
  <key>ProcessType</key>
  <string>Interactive</string>
  <key>StandardInPath</key>
  <string>/dev/null</string>
  <key>StandardOutPath</key>
  <string>{logf}</string>
  <key>StandardErrorPath</key>
  <string>{logf}</string>{env}
</dict>
</plist>
"""


def unit() -> str:
    """systemd 用户服务：网络起来以后起；崩了再起（正常退出不再起）。"""
    env = f"Environment=OPENMOUSSE_NODE_HOME={home()}\n" if os.environ.get("OPENMOUSSE_NODE_HOME") else ""
    return (f"[Unit]\nDescription=OpenMousse node host\nAfter=network-online.target\nWants=network-online.target\n\n"
            f"[Service]\nExecStart={shlex.join(program())}\n{env}Restart=on-failure\nRestartSec=5\nKillMode=mixed\nTimeoutStopSec=30\n\n"
            f"[Install]\nWantedBy=default.target\n")


def _run(cmd: list[str]) -> int:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        print(f"{cmd[0]}: {e}", file=sys.stderr)
        return 1
    if r.returncode != 0 and (r.stderr or r.stdout).strip():
        print((r.stderr or r.stdout).strip(), file=sys.stderr)
    return r.returncode


def install() -> int:
    nf = node_file()
    if not nf.get("token"):
        print("Pair first: node_host.py pair '<pairing link>'", file=sys.stderr)
        return 2
    home().mkdir(parents=True, exist_ok=True)
    if system() == "macos":
        p = plist_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        uid = os.getuid()
        if p.exists():
            _run(["launchctl", "bootout", f"gui/{uid}/{LABEL}"])
        p.write_text(plist(), encoding="utf-8")
        os.chmod(p, 0o644)
        if _run(["launchctl", "bootstrap", f"gui/{uid}", str(p)]) != 0:
            print(f"launchctl couldn't start it; the plist is at {p}", file=sys.stderr)
            return 1
        print(f"Installed {LABEL}: it starts when you log in. Logs: {home() / 'node.log'}")
        print("Screenshots need Screen Recording permission for this Python (System Settings → Privacy & Security).")
        return 0
    if system() == "linux":
        p = unit_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(unit(), encoding="utf-8")
        if _run(["systemctl", "--user", "daemon-reload"]) != 0 or _run(["systemctl", "--user", "enable", "--now", UNIT]) != 0:
            print(f"systemctl couldn't start it; the unit is at {p}", file=sys.stderr)
            return 1
        print(f"Installed {UNIT}. To keep it running when you're logged out: loginctl enable-linger {os.environ.get('USER') or ''}".rstrip())
        return 0
    print(f"install only knows macOS (launchd) and Linux (systemd); on {sys.platform} run it yourself: {shlex.join(program())}", file=sys.stderr)
    return 2


def uninstall() -> int:
    if system() == "macos":
        p = plist_path()
        if p.exists():
            _run(["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"])
            p.unlink()
        print(f"Removed {LABEL}. The token and config stay in {home()} (delete that folder to forget this node).")
        return 0
    if system() == "linux":
        p = unit_path()
        if p.exists():
            _run(["systemctl", "--user", "disable", "--now", UNIT])
            p.unlink()
            _run(["systemctl", "--user", "daemon-reload"])
        print(f"Removed {UNIT}. The token and config stay in {home()} (delete that folder to forget this node).")
        return 0
    print(f"uninstall only knows macOS and Linux (this is {sys.platform})", file=sys.stderr)
    return 2


def status() -> int:
    nf, cfg = node_file(), Config.load()
    print(f"node folder   {home()}")
    print(f"paired        {'yes, as ' + str(nf.get('id') or '?') + ' with ' + str(nf.get('server')) if nf.get('token') else 'no'}")
    print(f"offers        {', '.join(surface(cfg))}")
    shared = [n + " (" + str(p) + (", writable" if w else "") + ")" for n, (p, w) in sorted(cfg.folders.items())]
    print(f"folders       {', '.join(shared) or 'none'}")
    print(f"allowlist     {'; '.join(cfg.allow) or 'empty (no commands)'}")
    svc = plist_path() if system() == "macos" else unit_path()
    print(f"service       {'installed (' + str(svc) + ')' if svc.exists() else 'not installed'}")
    if nf.get("token"):
        try:  # 再报一遍（和 run 一样：hello 不会拿走等着的活）
            out = hello(Server(str(nf["server"]), str(nf["token"])), cfg)
            print(f"server        reachable; usable now: {', '.join(out.get('usable') or []) or 'nothing yet'}")
        except HttpError as e:
            print(f"server        {e}")
        except (urllib.error.URLError, OSError) as e:
            print(f"server        not reachable ({getattr(e, 'reason', None) or e})")
    return 0


USAGE = """usage: node_host.py pair '<pairing link>' [--name NAME] | run | install | uninstall | status
  pair       trade a node pairing code (tokens.py pair --node on the server) for this computer's node token
  run        connect and work in the foreground (Ctrl-C stops)
  install    start it with your login (macOS launchd ai.openmousse.node / Linux systemd user openmousse-node.service)
  uninstall  stop and remove that service (token and config stay)
  status     show the config, the service and whether the server is reachable
What it may do is up to ~/.openmousse-node/config.json (see the top of this file)."""


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd, rest = (argv[0], argv[1:]) if argv else ("", [])
    if cmd == "pair":
        return pair(rest)
    if cmd == "run":
        return run(rest)
    if cmd == "install":
        return install()
    if cmd == "uninstall":
        return uninstall()
    if cmd == "status":
        return status()
    print(USAGE, file=sys.stderr if cmd not in ("-h", "--help", "help") else sys.stdout)
    return 0 if cmd in ("-h", "--help", "help") else 2


if __name__ == "__main__":
    sys.exit(main())
