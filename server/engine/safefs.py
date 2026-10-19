"""不跟符号链接的文件读写（内核 ⑫，2026-10-16）：从一个可信的根目录（Agent 的工作区）往下，一层层用 openat 走（O_NOFOLLOW），
路上哪一层是符号链接就停；写 = 同一个目录里先写临时文件、再 rename 换上（原子，原来的权限留着）。
engine/host_files.py（主机上的文件工具）和 engine/versions.py（留旧版本）用。

为什么不是 Path.resolve() 查一遍再打开：查完到打开之间，别的进程（主机上跑的命令、能写工作区的沙箱）可以把路上的某一层换成
符号链接，打开时就出了工作区（TOCTOU）。这里每一步都在已经打开的那个目录里找下一层、而且不跟链接，换不掉。
根目录自己可以是符号链接（server.json 里写的工作区路径，可信）。只在 Linux / macOS 上用（dir_fd、scandir(fd)）。
"""
from __future__ import annotations

import errno
import os
import stat
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from i18n import L

DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
NONBLOCK = getattr(os, "O_NONBLOCK", 0)   # 打开管道、设备时别卡住（打开以后再看是不是普通文件）


class Refused(Exception):
    """路径走不通：有 ..、经过符号链接、不是普通文件……str(e) 是给模型看的一句（按 server.json 的语言）。"""


def link_refused(name: str) -> Refused:
    return Refused(L(f"{name} 是符号链接：不跟着走（可能指到工作区外面）", f"{name} is a symbolic link; links aren't followed (they could lead outside)"))


def split(rel: str) -> list[str]:
    """相对路径 → 一层层的名字。. 和多余的 / 去掉；不许 ..、NUL。"""
    out = []
    for p in str(rel).split("/"):
        if p in ("", "."):
            continue
        if p == "..":
            raise Refused(L("路径里不能有 ..", "No .. in paths"))
        if "\x00" in p:
            raise Refused(L("路径里有非法字符", "The path has an invalid character"))
        out.append(p)
    return out


@dataclass
class Entry:
    name: str
    kind: str          # f 文件 / d 目录 / l 符号链接（不跟）/ o 别的（管道、设备……）
    size: int
    mtime: float


def kind_of(mode: int) -> str:
    return "d" if stat.S_ISDIR(mode) else "f" if stat.S_ISREG(mode) else "l" if stat.S_ISLNK(mode) else "o"


class Root:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def _child(self, fd: int, name: str, create: bool) -> int:
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
                raise link_refused(name) from None
            raise Refused(L(f"{name} 不是文件夹", f"{name} isn't a folder")) from None

    @contextmanager
    def dir(self, parts: list[str], create: bool = False) -> Iterator[int]:
        """打开 parts 那一层目录（一层层走，不跟链接）；create = 没有的目录顺手建。用完自己关。"""
        fd = os.open(self.path, DIR_FLAGS)
        try:
            for name in parts:
                nxt = self._child(fd, name, create)
                os.close(fd)
                fd = nxt
            yield fd
        finally:
            os.close(fd)

    def lstat(self, parts: list[str]) -> os.stat_result | None:
        """不跟链接的 stat；没有 → None（路上的目录没有也是 None）。"""
        if not parts:
            return os.stat(self.path)
        try:
            with self.dir(parts[:-1]) as d:
                return os.stat(parts[-1], dir_fd=d, follow_symlinks=False)
        except FileNotFoundError:
            return None

    def read(self, parts: list[str], limit: int | None = None) -> tuple[bytes, int]:
        """读一个普通文件 → (内容（最多 limit 字节）, 文件多大)。是链接、目录、管道这类都不读。"""
        if not parts:
            raise IsADirectoryError(str(self.path))
        with self.dir(parts[:-1]) as d:
            try:
                fd = os.open(parts[-1], os.O_RDONLY | NOFOLLOW | NONBLOCK | os.O_CLOEXEC, dir_fd=d)
            except OSError as e:
                if e.errno == errno.ELOOP:
                    raise link_refused(parts[-1]) from None
                raise
        with open(fd, "rb") as f:   # 拿到 fd 以后交给文件对象关
            st = os.fstat(f.fileno())
            if stat.S_ISDIR(st.st_mode):
                raise IsADirectoryError(parts[-1])
            if not stat.S_ISREG(st.st_mode):
                raise Refused(L(f"{parts[-1]} 不是普通文件", f"{parts[-1]} isn't a regular file"))
            return (f.read(limit) if limit is not None else f.read()), st.st_size

    def write(self, parts: list[str], data: bytes, *, create_dirs: bool = True) -> bool:
        """整个换掉（没有就建）：同一个目录里写临时文件再 rename，原来的权限留着。→ True = 新建的。是链接、目录的不写。"""
        if not parts:
            raise IsADirectoryError(str(self.path))
        name = parts[-1]
        with self.dir(parts[:-1], create=create_dirs) as d:
            try:
                old = os.stat(name, dir_fd=d, follow_symlinks=False)
            except FileNotFoundError:
                old = None
            if old is not None and stat.S_ISLNK(old.st_mode):
                raise link_refused(name)
            if old is not None and not stat.S_ISREG(old.st_mode):
                raise Refused(L(f"{name} 不是普通文件", f"{name} isn't a regular file"))
            tmp = f".{name[:40]}.tmp-{uuid.uuid4().hex[:8]}"
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | NOFOLLOW | os.O_CLOEXEC, 0o666, dir_fd=d)
            try:
                with open(fd, "wb") as f:
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
            return old is None

    def remove(self, parts: list[str]) -> bool:
        """删一个文件（是链接就删链接本身，不碰它指的东西）→ 删了没有。目录不删。"""
        if not parts:
            raise IsADirectoryError(str(self.path))
        try:
            with self.dir(parts[:-1]) as d:
                try:
                    os.unlink(parts[-1], dir_fd=d)
                    return True
                except FileNotFoundError:
                    return False
        except FileNotFoundError:
            return False

    def entries(self, parts: list[str]) -> list[Entry]:
        """一层目录里有什么（不跟链接）。"""
        out = []
        with self.dir(parts) as d:
            with os.scandir(d) as it:
                for e in it:
                    try:
                        st = e.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    out.append(Entry(e.name, kind_of(st.st_mode), st.st_size, st.st_mtime))
        return out

    def walk(self, parts: list[str], skip: frozenset[str] | set[str] = frozenset(),
             prune: Callable[[list[str], Entry], bool] | None = None) -> Iterator[tuple[list[str], Entry]]:
        """往下走（先序：一个文件夹列完它里面的再往后；不跟链接，名字在 skip 里的目录不进去也不列）→ (相对路径的一层层, 条目)。
        prune(路径, 条目) 为真的文件夹照样列出来，但不往里走。一层里按名字排；调用方自己数着停。"""
        def kids(p: list[str]) -> Iterator[Entry]:
            try:
                return iter(sorted(self.entries(p), key=lambda x: x.name))
            except (OSError, Refused):
                return iter(())
        stack = [(list(parts), kids(list(parts)))]
        while stack:
            cur, it = stack[-1]
            e = next(it, None)
            if e is None:
                stack.pop()
                continue
            if e.name in skip:
                continue
            yield cur + [e.name], e
            if e.kind == "d" and not (prune is not None and prune(cur + [e.name], e)):
                stack.append((cur + [e.name], kids(cur + [e.name])))
