"""用户在 app 里（或命令行的三步引导 setup_ctl.py 里）填的密钥（内核第 9 步「通用的选择层」，2026-10-17）：存在这台服务器上一个只有
自己能读的文件里。

文件：server.json 旁边的 secrets.env（默认 ~/.openmousse/secrets.env；server.json 的 secrets_file 能换）。一行一个 KEY=VALUE（和 .env
  一样，别的工具也认），上面一行注释记哪天存的（# saved 2026-10-17T10:00:00+00:00）。权限 0600；先在同一目录写一个 0600 的临时文件、
  fsync，再换上去（写到一半断电不会留下半个文件，也没有别人读得到的一刻）；同一时间只有一个在写（进程里一把锁 + 文件锁：命令行和服务器
  不打架）。用户自己往里加的行、注释原样留着。
谁读：lookup() 是全服务器读密钥的顺序：先看进程环境，再看这里，再看 server.json 的 env_file（默认 <openclaw_home>/.env）。
  engine/models.env_value（模型、搜索、向量、MCP 的 ${变量}）和 claw.env_value 都走它。每次现读（按修改时间缓存），改了不用重启。
永远不回给 app：info() 只说有没有、从哪来（进程环境 / 这里 / env_file）、末四位、哪天存的；值不写进 server.json、日志、错误信息
  （这里抛的错只说哪一个变量名、什么问题，不带值）。删 = 从文件里拿掉这一行和它的注释。
"""
from __future__ import annotations

import contextlib
import os
import re
import secrets
import threading
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

import config

try:
    import fcntl
except ImportError:  # Windows：只有进程里的锁
    fcntl = None  # type: ignore[assignment]

NAME = re.compile(r"^[A-Z_][A-Z0-9_]{0,63}$")
VALUE = re.compile(r"^[\x21-\x7e]{1,4096}$")   # 看得见的 ASCII：放得进请求头（没有空格、换行、中文）
BAD = set("'\"`\\")                            # 引号、反斜杠：.env 的读法会吃掉它们，存进去就读不回原样
SAVED = re.compile(r"^#\s*saved\s+(\S+)\s*$")
HEADER = ("# OpenMousse: API keys saved from the app or setup_ctl.py. Only this user can read this file (0600).\n"
          "# Don't commit it or send it to anyone. One KEY=VALUE per line; the line above each says when it was saved.\n")
HINT_MIN = 12                                  # 短于这个的不给末四位（露出来的占比太大）

_lock = threading.Lock()
_cache: tuple[str, int, int, list[str]] | None = None  # (路径, mtime_ns, 大小, 行)


class SecretError(ValueError):
    """存不进去：名字或值不像样（一句给人看的话，不带值）。"""


def path() -> Path:
    """secrets.env 在哪：server.json 的 secrets_file，没写就是 server.json 旁边（每次现算：测试会换 config.CONFIG_PATH）。"""
    p = config.raw().get("secrets_file")
    return Path(str(p)).expanduser() if p else config.CONFIG_PATH.parent / "secrets.env"


def env_file() -> Path:
    """server.json 的 env_file（没写就是 <openclaw_home>/.env）：老实例放密钥的地方，这里只读不写。"""
    e = config.raw().get("env_file")
    return Path(str(e)).expanduser() if e else config.settings.env_file


def check_name(name: str) -> str:
    n = str(name or "").strip()
    if not NAME.match(n):
        raise SecretError(f"{n[:64]!r} isn't a valid variable name (A-Z, 0-9 and _)")
    return n


def check_value(name: str, value: str) -> str:
    v = str(value or "").strip()
    if not v:
        raise SecretError(f"{name}: the value is empty")
    if not VALUE.match(v) or BAD & set(v):
        raise SecretError(f"{name}: the value has characters a key can't have (spaces, line breaks, quotes, non-ASCII)")
    return v


# —— 读 ——————————————————————————————————————————————————————————————————————

def _lines() -> list[str]:
    """文件的每一行（按修改时间 + 大小缓存）。没有文件 = []。"""
    global _cache
    p = path()
    try:
        st = p.stat()
    except OSError:
        return []
    if _cache and _cache[0] == str(p) and _cache[1:3] == (st.st_mtime_ns, st.st_size):
        return _cache[3]
    try:
        lines = p.read_text(encoding="utf8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []
    _cache = (str(p), st.st_mtime_ns, st.st_size, lines)
    return lines


def _parse(line: str) -> tuple[str, str] | None:
    k, sep, v = line.strip().removeprefix("export ").partition("=")
    return (k.strip(), v.strip().strip("'\"")) if sep and not k.lstrip().startswith("#") else None


def entries() -> dict[str, tuple[str, str | None]]:
    """这个文件里的：名字 → (值, 哪天存的)。同名的取最后一行（和 .env 一样后写的算）。"""
    out: dict[str, tuple[str, str | None]] = {}
    prev = ""
    for line in _lines():
        kv = _parse(line)
        if kv and kv[0]:
            m = SAVED.match(prev.strip())
            out[kv[0]] = (kv[1], m.group(1) if m else None)
        prev = line
    return out


def get(name: str) -> str:
    """这个文件里的值；没有 = 空。"""
    hit = entries().get(str(name or ""))
    return hit[0] if hit else ""


def _from_env_file(name: str) -> str:
    try:
        for line in env_file().read_text(encoding="utf8").splitlines():
            kv = _parse(line)
            if kv and kv[0] == name:
                return kv[1]
    except (OSError, UnicodeDecodeError):
        pass
    return ""


def lookup(name: str) -> tuple[str, str | None]:
    """→ (值, 从哪来：env | app | env_file)；哪儿都没有 → ("", None)。顺序见文件开头。"""
    if not name:
        return "", None
    if os.environ.get(name):
        return os.environ[name], "env"
    if v := get(name):
        return v, "app"
    if v := _from_env_file(name):
        return v, "env_file"
    return "", None


def hint(value: str) -> str | None:
    """末四位（「…abcd」）；太短的不给。"""
    v = str(value or "").strip()
    return f"…{v[-4:]}" if len(v) >= HINT_MIN else None


def info(name: str) -> dict:
    """给 app 看的一个密钥：{"name", "present", "source", "hint", "savedAt"}。永远不带值。"""
    value, source = lookup(name)
    saved = entries().get(name, (None, None))[1] if source == "app" else None
    return {"name": name, "present": bool(value), "source": source, "hint": hint(value) if value else None, "savedAt": saved}


# —— 写 ——————————————————————————————————————————————————————————————————————

@contextlib.contextmanager
def _locked() -> Iterator[None]:
    """进程里一把锁 + 文件锁（secrets.env.lock）：服务器和命令行同时存的时候一个一个来。"""
    p = path()
    with _lock:
        p.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if fcntl is None:
            yield
            return
        fd = os.open(f"{p}.lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)


def _write(lines: list[str]) -> None:
    """原子写：同一目录里先写一个 0600 的临时文件、fsync，再 os.replace 换上去。"""
    global _cache
    p = path()
    tmp = p.with_name(f".{p.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf8") as f:
            f.write("\n".join(lines).rstrip("\n") + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    with contextlib.suppress(OSError):
        os.chmod(p, 0o600)  # 换上去的就是 0600；这一句给万一被人改松过的老文件
    _cache = None


def _fresh_lines() -> list[str]:
    """写之前现读（不走缓存）：别的进程刚写过的也算上。"""
    try:
        return path().read_text(encoding="utf8").splitlines()
    except FileNotFoundError:
        return []


def _without(lines: list[str], name: str) -> tuple[list[str], bool]:
    """去掉 name 的每一行（连同它上面那行「# saved …」）。→ (剩下的, 去掉过没有)。"""
    out: list[str] = []
    found = False
    for line in lines:
        kv = _parse(line)
        if kv and kv[0] == name:
            found = True
            if out and SAVED.match(out[-1].strip()):
                out.pop()
            continue
        out.append(line)
    return out, found


def put(name: str, value: str, *, at: datetime | None = None) -> dict:
    """存一个（同名的换掉）→ info(name)。名字、值不像样抛 SecretError（不带值）。"""
    name = check_name(name)
    value = check_value(name, value)
    stamp = (at or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(timespec="seconds")
    with _locked():
        lines, _ = _without(_fresh_lines(), name)
        if not lines:
            lines = HEADER.rstrip("\n").split("\n")
        while lines and not lines[-1].strip():
            lines.pop()
        _write([*lines, f"# saved {stamp}", f"{name}={value}"])
    return info(name)


def delete(name: str) -> bool:
    """从文件里拿掉（文件里没有 = False；进程环境、env_file 里的这里删不了）。"""
    name = check_name(name)
    with _locked():
        lines = _fresh_lines()
        rest, found = _without(lines, name)
        if found:
            _write(rest)
    return found


def mode() -> int | None:
    """文件现在的权限位（没有文件 = None）：状态里提醒「权限太松」用。"""
    try:
        return path().stat().st_mode & 0o777
    except OSError:
        return None
