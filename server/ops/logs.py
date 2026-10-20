"""mousse logs：看服务的日志（第 7 批，14.1，2026-10-19）。

  mousse logs [-f] [-n 200] [--since 1h] [--grep 词] [--unit server | daily-close | tree]

Linux 的 systemd：journalctl --user -u openmousse-server（--since 交给 journalctl：1h、30m、2d 或者 "2026-10-19 08:00"）；
macOS 的 launchd、自己起的服务：读 ~/.openmousse/logs/<名字>.log（世界树是 ~/.mousse-tree/tree.log）。文件里的行没有时间，--since 用不了，
会说一声，照样给最后几行。
每一行都过一遍打码（engine/errors.py 的 scrub，加上这台机器上真的令牌和密钥）再打出来。--grep 不分大小写，给的是最后 n 行对上的。
-f 跟着看新的（Ctrl-C 停）。退出码：0 打了；1 没有日志（journalctl 不在、文件不在）；2 参数不对。
"""
from __future__ import annotations

import collections
import os
import re
import subprocess
import sys
import time
from collections.abc import Iterable, Iterator
from pathlib import Path

from i18n import L

from . import common as C

SINCE = re.compile(r"^(\d+)\s*(s|m|min|h|d|w)$", re.I)
UNITS = {"server": C.SERVER, "daily-close": C.DAILY_CLOSE, "tree": C.TREE}


def journal_since(v: str) -> str:
    """--since 给 journalctl：1h → -1h（systemd.time 的相对时间）；别的（日期）原样。"""
    m = SINCE.match(v.strip())
    if m:
        unit = {"s": "s", "m": "min", "min": "min", "h": "h", "d": "d", "w": "w"}[m.group(2).lower()]
        return f"-{m.group(1)}{unit}"
    return v.strip()


def log_file(svc: C.Service) -> Path:
    if svc is C.TREE:
        return C.tree_home() / "tree.log"
    return C.log_dir() / (svc.log or "server.log")


def tail_lines(p: Path, n: int) -> list[str]:
    """文件最后 n 行（从后往前读块，大文件也不整个读进来）。"""
    with open(p, "rb") as f:
        f.seek(0, os.SEEK_END)
        pos = f.tell()
        buf = b""
        while pos > 0 and buf.count(b"\n") <= n:
            step = min(65536, pos)
            pos -= step
            f.seek(pos)
            buf = f.read(step) + buf
    lines = buf.decode("utf8", "replace").splitlines()
    if pos > 0 and lines:
        lines = lines[1:]  # 没读到文件头：第一行可能是半截
    return lines[-n:] if n else []


def matches(line: str, word: str | None) -> bool:
    return not word or word.lower() in line.lower()


def emit(lines: Iterable[str]) -> None:
    for line in lines:
        print(C.scrub(line.rstrip("\n")), flush=True)


def journal(svc: C.Service, n: int, since: str | None, word: str | None, follow: bool) -> int:
    base = ["journalctl", "--user", "-u", svc.unit, "--no-pager", "-o", "short-iso"]
    # 先打最后 n 行（有 --grep 就是对上的最后 n 行：要先全拿来再挑，-n 交给 journalctl 会先截再挑）
    r = C.run(base + (["--since", journal_since(since)] if since else []) + ([] if word else ["-n", str(n)]), timeout=120)
    if r.returncode == 127:
        print(L("没有 journalctl", "journalctl isn't available"), file=sys.stderr)
        return 1
    lines = r.stdout.splitlines()
    emit(collections.deque((x for x in lines if matches(x, word)), maxlen=n) if word else lines)
    if r.returncode != 0:
        print(C.scrub(r.stderr.strip())[-500:] or f"journalctl: exit {r.returncode}", file=sys.stderr)
        return 1
    if not follow:
        return 0
    # 再从现在起跟着看（-n 0：前面已经打过了）
    try:
        p = subprocess.Popen([*base, "-n", "0", "-f"], stdout=subprocess.PIPE, text=True, env=C._env())  # noqa: S603, SLF001
    except OSError as e:
        print(str(e), file=sys.stderr)
        return 1
    try:
        for line in p.stdout or ():
            if matches(line, word):
                emit([line])
    except KeyboardInterrupt:
        pass
    finally:
        p.terminate()
    return 0


def follow_file(p: Path, word: str | None) -> Iterator[str]:
    """跟着文件看新写的（被截断、换了新文件就从头读）。"""
    f = open(p, "rb")  # noqa: SIM115 — 一直开着跟
    f.seek(0, os.SEEK_END)
    ino = os.fstat(f.fileno()).st_ino
    rest = b""
    try:
        while True:
            chunk = f.read()
            if chunk:
                rest += chunk
                *done, rest = rest.split(b"\n")
                for line in done:
                    text = line.decode("utf8", "replace")
                    if matches(text, word):
                        yield text
                continue
            time.sleep(0.5)
            try:
                st = os.stat(p)
            except FileNotFoundError:
                continue
            if st.st_ino != ino or st.st_size < f.tell():
                f.close()
                f = open(p, "rb")  # noqa: SIM115
                ino, rest = os.fstat(f.fileno()).st_ino, b""
    finally:
        f.close()


def from_file(svc: C.Service, n: int, since: str | None, word: str | None, follow: bool) -> int:
    p = log_file(svc)
    if not p.exists():
        print(L(f"没有日志文件 {C.tilde(p)}", f"No log file at {C.tilde(p)}"), file=sys.stderr)
        return 1
    if since:
        print(L("（文件里的行没有时间，--since 用不了：给的是最后几行）", "(the log file has no timestamps, so --since can't apply: showing the last lines)"),
              file=sys.stderr)
    if word:
        with open(p, encoding="utf8", errors="replace") as f:
            emit(collections.deque((x for x in f if matches(x, word)), maxlen=n))
    else:
        emit(tail_lines(p, n))
    if follow:
        try:
            for line in follow_file(p, word):
                emit([line])
        except KeyboardInterrupt:
            pass
    return 0


def main(a) -> int:
    if a.lines < 0:
        print(L("-n 不能是负数", "-n can't be negative"), file=sys.stderr)
        return 2
    svc = UNITS[a.unit]
    if C.manager() == "systemd":
        return journal(svc, a.lines, a.since, a.grep, a.follow)
    return from_file(svc, a.lines, a.since, a.grep, a.follow)
