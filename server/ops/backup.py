"""mousse backup / restore：备份和恢复（第 7 批，14.5，2026-10-19）。照 OpenClaw 的 docs/cli/backup.md（tar.gz + manifest、verify、
restore）和 Hermes 的 hermes_cli/backup_sqlite.py、archive_safe.py（SQLite 用 backup() 在线拷；包里只认普通文件和目录、路径不许跳出去；
恢复前逐个校验，坏一个字节都不写）。只借做法，代码自己写。

  mousse backup create [--out 文件或目录] [--no-secrets] [--no-workspace] [--no-tree] [--keep N] [--json]
  mousse backup verify <文件> [--json]
  mousse backup list
  mousse restore <文件> [--dry-run] [--yes]

包里有什么（一个 tar.gz，0600；名字 openmousse-backup-<年月日-时分秒>.tar.gz，默认放 server.json 旁边的 backups/，--keep 留最近几份，默认 5）：
  config        server.json（里面有接入令牌）
  secrets       secrets.env（默认带上；--no-secrets 不带）
  data          data_dir 整个：SQLite（mousse.db、recall.db、nutrition.db……）用 sqlite3 的 backup() 在线拷一个一致的样子，不直接复制正在写的文件；
                -wal / -shm / *.lock / *.tmp 不要。里面也算密钥的跟着 --no-secrets 走：chatgpt/（ChatGPT 套餐的凭据）、apps/（连接器的
                OAuth 令牌）、sentinel/（Doorman 代位的密钥和证书）、gateway-device.json（设备私钥）、social/identity.json（朋友认你的钥匙）。
                engine/browser-profiles/（沙箱浏览器留着的登录配置档：各网站的 cookie，浏览器开着时锁着）一概不带，恢复以后在沙箱浏览器里重新登录。
                别的程序把库锁住了（独占）的：等 15 秒还拷不出来就报错、说是哪个文件（common.sqlite_copy），不会卡死
  db / uploads  server.json 把库、上传的文件放到 data_dir 外面时单独一份
  tree          世界树的家（~/.mousse-tree，MOUSSE_TREE_HOME）；Markdown 存储的笔记文件夹在外面的另算 tree-notes。--no-tree 都不带
  workspace-*   各 Agent 的工作区（server.json 的 workspace、agent_workspaces；--no-workspace 不带）。软链不跟进去，只记下指向哪
manifest.json：格式版本、建的时间、版本和 commit、带没带密钥、每一块原来在哪（家目录下的记成 ~/…：换一台机器、换了用户名也能恢复）、
每个文件的大小、sha256、权限、是不是 SQLite。包里的路径是 <包名>/files/<块>/<相对路径>，manifest 在 <包名>/manifest.json。
verify：包里只能有一个根目录、只认普通文件和目录、路径不许是绝对的或带 ..；manifest 列的每个文件都在、大小和 sha256 对得上、没有多出来的；
  SQLite 解到临时文件里打开、PRAGMA quick_check 过。
restore：先 verify，不过一个字节都不写；动手前给现在的样子做一份快照（openmousse-snapshot-pre-restore-…，同样的几块），restore 自己也能撤
  （mousse restore <快照>）；服务在跑要先停（库换掉的时候不能有人在写）：有 --yes 才替你停、替你起，不然说要跑什么（退出码 2）。先解到
  server.json 旁边的临时目录、再校验一遍，然后一个一个换上去（同一块盘上是改名）；换 SQLite 之前删掉它旧的 -wal / -shm。只覆盖包里有的，
  包里没有的文件留着。
退出码：0 好了；1 没成（包坏了、写不了）；2 不肯做（服务在跑又没 --yes、参数不对）。
mousse update 动手前的自动快照（openmousse-snapshot-pre-update-…）用的也是这一套（snapshot()）。
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import tarfile
import tempfile
import zlib
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from i18n import L

from . import common as C

FORMAT = 1
PREFIX = "openmousse-backup-"
SNAP_PREFIX = "openmousse-snapshot-"
KEEP = 5
SNAP_KEEP = 5
SKIP_SUFFIX = (".lock", "-wal", "-shm", "-journal", ".tmp")
SKIP_NAMES = {".DS_Store"}
DATA_SECRETS = ({"chatgpt", "apps", "sentinel"}, {"gateway-device.json", "social/identity.json"})  # 目录（第一层）、文件
DATA_SKIP = {"engine/browser-profiles"}  # 一概不进包：沙箱浏览器留着的登录配置档（engine/sandbox.py，各网站的登录 cookie；浏览器开着时独占锁着）
CHUNK = 1 << 20


class BackupError(RuntimeError):
    """备份 / 恢复不成：一句给人看的话（不带密钥）。"""


@dataclass
class Area:
    name: str
    path: Path
    kind: str           # file | dir
    secret: bool = False

    def stored(self) -> str:
        """manifest 里记的位置：家目录下的写成 ~/…（换机器、换用户名也能恢复）。"""
        return C.tilde(self.path)


def expand(stored: str) -> Path:
    return Path(os.path.expanduser(stored)) if stored.startswith("~") else Path(stored)


def _inside(p: Path, root: Path) -> bool:
    try:
        p.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def tree_conf() -> dict:
    try:
        d = json.loads((C.tree_home() / "config.json").read_text(encoding="utf8"))
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def areas(cfg: dict | None = None, *, secrets: bool = True, workspace: bool = True, tree: bool = True) -> list[Area]:
    """这次备份哪几块（见文件开头）。包在别的块里面的不重复（工作区在 data_dir 里这类）。"""
    cfg = C.cfg() if cfg is None else cfg
    out = [Area("config", C.config_path(), "file")]
    if secrets:
        out.append(Area("secrets", C.secrets_path(cfg), "file", secret=True))
    data = C.data_dir(cfg)
    out.append(Area("data", data, "dir"))
    db = C.db_path(cfg)
    if not _inside(db, data):
        out.append(Area("db", db, "file"))
    up = cfg.get("uploads")
    if up and not _inside(Path(str(up)).expanduser(), data):
        out.append(Area("uploads", Path(str(up)).expanduser(), "dir"))
    if tree:
        th = C.tree_home()
        out.append(Area("tree", th, "dir"))
        tc = tree_conf()
        if tc.get("storage") == "markdown":
            notes = Path(str(tc.get("notes_dir") or th / "notes")).expanduser()
            if not _inside(notes, th):
                out.append(Area("tree-notes", notes, "dir"))
    if workspace:
        for agent, ws in C.workspaces(cfg).items():
            out.append(Area(f"workspace-{re.sub(r'[^A-Za-z0-9_.-]', '_', agent)}", ws, "dir"))
    # 一块在另一块里面：里面那块不要（文件不重复进包）
    keep: list[Area] = []
    for a in out:
        if a.kind == "dir" and any(b is not a and b.kind == "dir" and _inside(a.path, b.path) and a.path.resolve() != b.path.resolve()
                                   for b in out):
            continue
        if a.kind == "dir" and any(b.kind == "dir" and b.path.resolve() == a.path.resolve() for b in keep):
            continue
        keep.append(a)
    return keep


def secret_file(area: Area, rel: str) -> bool:
    if area.secret:
        return True
    if area.name != "data":
        return False
    first = rel.split("/", 1)[0]
    return first in DATA_SECRETS[0] or rel in DATA_SECRETS[1]


def skip_name(name: str) -> bool:
    return name in SKIP_NAMES or name.endswith(SKIP_SUFFIX) or name.startswith(".~restore-")


def walk(area: Area, *, secrets: bool, exclude: list[Path]) -> Iterator[tuple[str, Path, str]]:
    """一块里的东西 → (相对路径, 路径, file | sqlite | link)。不跟软链进去（只记下来）；跳过锁、临时文件、SQLite 的 -wal / -shm；
    exclude 里的目录（输出目录、备份目录）不进去。"""
    if area.kind == "file":
        if area.path.is_symlink():
            yield area.path.name, area.path, "link"
        elif area.path.is_file():
            yield area.path.name, area.path, "sqlite" if C.is_sqlite(area.path) else "file"
        return
    if not area.path.is_dir():
        return
    root = area.path
    exclude = [x for x in exclude if _inside(x, root)]  # 只跳过在这一块里面的（输出目录是家目录这种包着整块的：不能整块跳过）
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        d = Path(dirpath)
        keep_dirs = []
        for n in sorted(dirnames):
            p = d / n
            rel = p.relative_to(root).as_posix()
            if p.is_symlink():
                yield rel, p, "link"
            elif any(_inside(p, x) for x in exclude) or (not secrets and secret_file(area, rel)) or (area.name == "data" and rel in DATA_SKIP):
                continue
            else:
                keep_dirs.append(n)
        dirnames[:] = keep_dirs
        for n in sorted(filenames):
            p = d / n
            rel = p.relative_to(root).as_posix()
            if skip_name(n) or (not secrets and secret_file(area, rel)) or (area.name in ("data", "tree") and n.endswith(".log")):
                continue  # 服务自己的日志（世界树的 tree.log）不进包
            if p.is_symlink():
                yield rel, p, "link"
            elif p.is_file():
                yield rel, p, "sqlite" if C.is_sqlite(p) else "file"


class _Hashing(io.RawIOBase):
    """读的时候顺手算 sha256（tarfile 一块块读，进包的就是算的）。"""

    def __init__(self, f) -> None:
        self.f, self.h, self.n = f, hashlib.sha256(), 0

    def readable(self) -> bool:
        return True

    def read(self, size: int = -1) -> bytes:
        b = self.f.read(size)
        self.h.update(b)
        self.n += len(b)
        return b


def default_dir() -> Path:
    return C.state_dir() / "backups"


def stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _target(out: str | None, base: str) -> Path:
    if not out:
        return default_dir() / f"{base}.tar.gz"
    p = Path(out).expanduser()
    if p.name.endswith((".tar.gz", ".tgz")) and not p.is_dir():
        return p
    return p / f"{base}.tar.gz"  # 别的一律当目录（还没有就建）


def create(out: str | None = None, *, secrets: bool = True, workspace: bool = True, tree: bool = True, keep: int | None = KEEP,
           prefix: str = PREFIX, reason: str = "", picked: list[Area] | None = None, protect: Path | None = None) -> dict:
    """打一个包 → {"path", "files", "bytes", "archiveBytes", "secrets", "areas", "links", "pruned"}。出错抛 BackupError（写了一半的包删掉）。
    picked：直接给要备份的那几块（恢复前的快照：照包里那几块，在这台机器上的位置）；不给就照 server.json 算（读不了就不备份：不知道数据在哪）。
    protect：清旧的时候不删的那一份（正要从它恢复的包）。"""
    if picked is None:
        cfg, problem = C.load_config()
        if problem in ("broken", "unreadable"):
            raise BackupError(L("server.json 读不了（mousse doctor 看为什么）：不知道数据在哪，没法备份",
                                "server.json can't be read (see mousse doctor): without it the data can't be found"))
        picked = areas(cfg or {}, secrets=secrets, workspace=workspace, tree=tree)
    base = f"{prefix}{reason + '-' if reason else ''}{stamp()}"
    dest = _target(out, base)
    n = 2
    while dest.exists() and dest.name.startswith(base):  # 自动起的名字：同一秒里第二个加 -2（只有自己指定的文件名才算撞了）
        dest = dest.with_name(f"{base}-{n}.tar.gz")
        n += 1
    root = dest.name.removesuffix(".tar.gz").removesuffix(".tgz") or base
    if dest.exists():
        raise BackupError(L(f"{C.tilde(dest)} 已经有了：不覆盖", f"{C.tilde(dest)} already exists: not overwriting it"))
    for a in picked:
        if a.kind == "dir" and _inside(dest.parent, a.path):
            raise BackupError(L(f"包不能放在要备份的 {C.tilde(a.path)} 里面", f"the archive can't go inside {C.tilde(a.path)}, which is being backed up"))
    if not dest.parent.exists():
        dest.parent.mkdir(mode=0o700, parents=True)  # 新建的备份目录只给自己（包本身也是 0600）
    exclude = [default_dir(), dest.parent]
    plan = [(a, list(walk(a, secrets=secrets, exclude=exclude))) for a in picked]
    need = sum(p.stat().st_size for _a, items in plan for _r, p, k in items if k != "link")
    free = shutil.disk_usage(dest.parent).free
    if free < need:
        raise BackupError(L(f"{C.tilde(dest.parent)} 只剩 {C.human_size(free)}，要 {C.human_size(need)}", f"only {C.human_size(free)} free in {C.tilde(dest.parent)}, {C.human_size(need)} needed"))
    files: list[dict] = []
    links: list[dict] = []
    tmp = dest.with_name(f".{dest.name}.{os.getpid()}.tmp")
    scratch = Path(tempfile.mkdtemp(prefix=".mousse-backup-", dir=dest.parent))
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as raw, tarfile.open(fileobj=raw, mode="w:gz", format=tarfile.PAX_FORMAT) as tf:
            for a, items in plan:
                for rel, p, kind in items:
                    if kind == "link":
                        with contextlib.suppress(OSError):
                            links.append({"area": a.name, "path": rel, "target": os.readlink(p)})
                        continue
                    src = p
                    if kind == "sqlite":
                        src = scratch / "db.sqlite"
                        with contextlib.suppress(FileNotFoundError):
                            src.unlink()
                        try:
                            C.sqlite_copy(p, src)
                        except sqlite3.Error as e:
                            raise BackupError(L(f"{C.tilde(p)} 拷不出来：{e}", f"couldn't copy {C.tilde(p)}: {e}")) from e
                    st = src.stat()
                    info = tarfile.TarInfo(f"{root}/files/{a.name}/{rel}")
                    info.size, info.mode, info.mtime = st.st_size, 0o600, int(p.stat().st_mtime)
                    try:
                        with open(src, "rb") as f:
                            h = _Hashing(f)
                            tf.addfile(info, h)
                    except OSError as e:  # 读的时候变短了（被删、被截断）
                        raise BackupError(L(f"{C.tilde(p)} 读到一半变了：{e}", f"{C.tilde(p)} changed while being read: {e}")) from e
                    files.append({"area": a.name, "path": rel, "size": info.size, "sha256": h.h.hexdigest(),
                                  "mode": f"{(p.stat().st_mode & 0o777):o}", **({"sqlite": True} if kind == "sqlite" else {})})
            manifest = {"format": FORMAT, "kind": "openmousse-backup", "root": root, "createdAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        "version": C.repo_version() or None, "commit": C.head_commit(), "reason": reason or None, "secrets": secrets,
                        "include": {"secrets": secrets, "workspace": any(a.name.startswith("workspace-") for a in picked),
                                    "tree": any(a.name.startswith("tree") for a in picked)},
                        "areas": [{"name": a.name, "path": a.stored(), "kind": a.kind} for a in picked],
                        "files": files, "links": links}
            data = json.dumps(manifest, ensure_ascii=False, indent=1).encode()
            info = tarfile.TarInfo(f"{root}/manifest.json")
            info.size, info.mode, info.mtime = len(data), 0o600, int(datetime.now().timestamp())
            tf.addfile(info, io.BytesIO(data))
        os.replace(tmp, dest)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    pruned = prune(dest.parent, f"{prefix}{reason + '-' if reason else ''}", keep, protect) if keep else []
    return {"path": str(dest), "files": len(files), "bytes": sum(f["size"] for f in files), "archiveBytes": dest.stat().st_size,
            "secrets": secrets, "areas": [a.name for a in picked], "links": len(links), "pruned": [str(p) for p in pruned]}


def listing(d: Path | None = None) -> list[Path]:
    """备份和快照，新的在前。"""
    d = d or default_dir()
    if not d.is_dir():
        return []
    when = re.compile(r"(\d{8})-(\d{6})(?:-(\d+))?\.tar\.gz$")
    found = [(m, p) for p in d.iterdir() if p.name.startswith((PREFIX, SNAP_PREFIX)) and (m := when.search(p.name))]
    return [p for _m, p in sorted(found, key=lambda x: _order(x[0]), reverse=True)]


def _order(m: re.Match) -> tuple[str, str, int]:
    return m.group(1), m.group(2), int(m.group(3) or 1)


def prune(d: Path, prefix: str, keep: int, protect: Path | None = None) -> list[Path]:
    """只留最近 keep 份名字是 <prefix><年月日-时分秒>[-n].tar.gz 的（别的文件不碰）。protect 那一份不删（正要从它恢复）。"""
    pat = re.compile(re.escape(prefix) + r"(\d{8})-(\d{6})(?:-(\d+))?\.tar\.gz$")
    mine = sorted((p for p in d.iterdir() if pat.match(p.name)), key=lambda p: _order(pat.match(p.name)), reverse=True)
    keep_too = protect.resolve() if protect is not None else None
    gone = []
    for p in mine[max(1, keep):]:
        if keep_too is not None and p.resolve() == keep_too:
            continue
        with contextlib.suppress(OSError):
            p.unlink()
            gone.append(p)
    return gone


def snapshot(reason: str, *, secrets: bool = True, workspace: bool = True, tree: bool = True, picked: list[Area] | None = None,
             protect: Path | None = None) -> dict:
    """update / restore 动手前的自动快照：同一套 create，名字 openmousse-snapshot-<reason>-<时间>，各留最近 SNAP_KEEP 份（protect 那份不删）。"""
    return create(None, secrets=secrets, workspace=workspace, tree=tree, keep=SNAP_KEEP, prefix=SNAP_PREFIX, reason=reason, picked=picked,
                  protect=protect)


def areas_of(man: dict) -> list[Area]:
    """包里那几块在这台机器上的位置（恢复前的快照照它拍：恢复要覆盖的正好都在里面；server.json 写坏了也拍得了）。"""
    out = []
    for a in man.get("areas") or []:
        if isinstance(a, dict) and a.get("name") and a.get("path") and a.get("kind") in ("file", "dir"):
            out.append(Area(str(a["name"]), expand(str(a["path"])), str(a["kind"]), secret=a["name"] == "secrets"))
    return out


# —— verify ————————————————————————————————————————————————————————————————————————

def safe_parts(name: str) -> list[str]:
    """包里的一个路径 → 安全的几段；绝对路径、带 ..、空的抛 ValueError（同 Hermes archive_safe.normalize_archive_parts）。"""
    norm = name.replace("\\", "/")
    pp = PurePosixPath(norm)
    parts = [x for x in pp.parts if x not in ("", ".")]
    if not norm or pp.is_absolute() or re.match(r"^[A-Za-z]:", norm) or not parts or ".." in parts:
        raise ValueError(name)
    return parts


def verify(path: str | Path, *, keep_sqlite_dir: Path | None = None) -> dict:
    """→ {"ok", "errors": [...], "manifest", "files", "bytes"}。只读；SQLite 解到临时文件里查完就删。"""
    p = Path(path).expanduser()
    errors: list[str] = []
    seen: dict[str, tuple[int, str]] = {}
    manifest = None
    roots: set[str] = set()
    sqlite_ok: dict[str, str] = {}
    tmpdir = Path(tempfile.mkdtemp(prefix=".mousse-verify-", dir=keep_sqlite_dir or None))
    try:
        try:
            tf = tarfile.open(p, "r:gz")
        except (OSError, tarfile.TarError, EOFError) as e:
            return {"ok": False, "errors": [L(f"打不开：{e}", f"can't open it: {e}")], "manifest": None, "files": 0, "bytes": 0}
        with tf:
            try:
                for m in tf:
                    try:
                        parts = safe_parts(m.name)
                    except ValueError:
                        errors.append(L(f"不安全的路径：{m.name[:120]}", f"unsafe path: {m.name[:120]}"))
                        continue
                    roots.add(parts[0])
                    if m.isdir():
                        continue
                    if not m.isfile():
                        errors.append(L(f"不是普通文件：{m.name[:120]}", f"not a regular file: {m.name[:120]}"))
                        continue
                    f = tf.extractfile(m)
                    if f is None:
                        errors.append(m.name)
                        continue
                    rel = "/".join(parts[1:])
                    if rel == "manifest.json":
                        try:
                            manifest = json.loads(f.read(64 << 20).decode("utf8"))
                        except (ValueError, UnicodeDecodeError):
                            errors.append(L("manifest.json 坏了", "manifest.json is broken"))
                        continue
                    h, n = hashlib.sha256(), 0
                    first = f.read(CHUNK)
                    db = rel.startswith("files/") and first.startswith(b"SQLite format 3\x00")
                    out = open(tmpdir / "db.sqlite", "wb") if db else None  # noqa: SIM115 — SQLite 解到临时文件里打开查
                    try:
                        chunk = first
                        while chunk:
                            h.update(chunk)
                            n += len(chunk)
                            if out:
                                out.write(chunk)
                            chunk = f.read(CHUNK)
                    finally:
                        if out:
                            out.close()
                    seen[rel] = (n, h.hexdigest())
                    if db:
                        good, msg = C.quick_check(tmpdir / "db.sqlite")
                        sqlite_ok[rel] = "ok" if good else msg
                        (tmpdir / "db.sqlite").unlink()
            except (tarfile.TarError, OSError, EOFError, zlib.error) as e:
                errors.append(L(f"包读到一半坏了：{e}", f"the archive is damaged: {e}"))
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    if len(roots) != 1:
        errors.append(L("包里要正好一个根目录", "the archive must have exactly one root folder"))
    if not isinstance(manifest, dict) or manifest.get("kind") != "openmousse-backup":
        errors.append(L("没有 manifest.json（不是 mousse backup 打的包）", "no manifest.json (not made by mousse backup)"))
        return {"ok": False, "errors": errors, "manifest": None, "files": len(seen), "bytes": sum(v[0] for v in seen.values())}
    if int(manifest.get("format") or 0) > FORMAT:
        errors.append(L(f"包的格式（{manifest.get('format')}）比这一版新：先 mousse update", f"the archive format ({manifest.get('format')}) is newer than this version: mousse update first"))
    names = {a.get("name") for a in manifest.get("areas") or [] if isinstance(a, dict)}
    listed: set[str] = set()
    for fe in manifest.get("files") or []:
        if not isinstance(fe, dict):
            errors.append("manifest")
            continue
        rel = f"files/{fe.get('area')}/{fe.get('path')}"
        listed.add(rel)
        try:
            safe_parts(str(fe.get("path") or ""))
        except ValueError:
            errors.append(L(f"不安全的路径：{fe.get('path')}", f"unsafe path: {fe.get('path')}"))
            continue
        if fe.get("area") not in names:
            errors.append(L(f"{rel}：manifest 里没有这一块", f"{rel}: no such area in the manifest"))
        got = seen.get(rel)
        if got is None:
            errors.append(L(f"少了 {rel}", f"missing {rel}"))
        elif got[0] != fe.get("size") or got[1] != fe.get("sha256"):
            errors.append(L(f"{rel} 对不上（大小或 sha256）", f"{rel} doesn't match (size or sha256)"))
        elif fe.get("sqlite") and sqlite_ok.get(rel) != "ok":
            errors.append(L(f"{rel}：SQLite quick_check 没过（{sqlite_ok.get(rel)}）", f"{rel}: SQLite quick_check failed ({sqlite_ok.get(rel)})"))
    for rel in sorted(set(seen) - listed):
        errors.append(L(f"多出来的文件：{rel}", f"unexpected file: {rel}"))
    return {"ok": not errors, "errors": errors, "manifest": manifest, "files": len(listed), "bytes": sum(int(f.get("size") or 0)
                                                                                                     for f in manifest.get("files") or [] if isinstance(f, dict))}


# —— restore ———————————————————————————————————————————————————————————————————————

def _escapes(target: Path, root: Path) -> bool:
    """换上去的位置跳出了这一块吗：离它最近的、已经在的上一层（顺着软链）不在 root 里面就算（软链指到外面去的目录不往里写）。"""
    p = target.parent
    while not p.exists() and p != p.parent:
        p = p.parent
    return not _inside(p, root)


def _place(staged: Path, target: Path, mode: int, sqlite: bool) -> None:
    """把解好的一个文件换上去：同一块盘上直接改名，不是就先拷到目标旁边再改名；SQLite 先删旧的 -wal / -shm。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    if sqlite:
        for suffix in ("-wal", "-shm", "-journal"):
            with contextlib.suppress(FileNotFoundError):
                os.unlink(f"{target}{suffix}")
    try:
        os.replace(staged, target)
    except OSError:
        tmp = target.with_name(f".~restore-{target.name}.{os.getpid()}")
        shutil.copyfile(staged, tmp)
        os.replace(tmp, target)
    os.chmod(target, mode)


def _running(names: list[str]) -> list[C.Service]:
    """要换的东西有服务在用、它正在跑：服务器（看 /api/health 和服务管理器），世界树（服务管理器）。"""
    out = []
    if C.health(timeout=2)["up"] or (C.manager() != "none" and C.state(C.SERVER)["active"]):
        out.append(C.SERVER)
    if any(n.startswith("tree") for n in names) and C.manager() != "none" and C.state(C.TREE)["active"]:
        out.append(C.TREE)
    return out


def restore(path: str, *, dry_run: bool = False, yes: bool = False, quiet: bool = False) -> int:
    say = (lambda *_a, **_k: None) if quiet else print
    rep = verify(path)
    if not rep["ok"]:
        say(L("包没过校验，一个字节都没写：", "The archive failed verification; nothing was written:"))
        for e in rep["errors"][:20]:
            say(f"  ✗ {e}")
        return 1
    man = rep["manifest"]
    targets = {a["name"]: (expand(str(a["path"])), a.get("kind")) for a in man["areas"]}
    say(L(f"包：{C.tilde(path)}（{man.get('createdAt')}，{man.get('version') or '?'} {man.get('commit') or ''}，{rep['files']} 个文件，"
          f"{C.human_size(rep['bytes'])}{'，带密钥' if man.get('secrets') else '，不带密钥'}）",
          f"Archive: {C.tilde(path)} ({man.get('createdAt')}, {man.get('version') or '?'} {man.get('commit') or ''}, {rep['files']} files, "
          f"{C.human_size(rep['bytes'])}{', with keys' if man.get('secrets') else ', without keys'})"))
    for name, (p, _kind) in targets.items():
        n = sum(1 for f in man["files"] if f["area"] == name)
        say(f"  {name:<14} → {C.tilde(p)}  ({n})")
    if dry_run:
        say(L("（--dry-run：只看，没动）", "(--dry-run: nothing changed)"))
        return 0
    running = _running(list(targets))
    if running and not yes:
        say(L("服务在跑：换库的时候不能有人在写。先停：", "The server is running: it can't be writing while the databases are replaced. Stop it first:"))
        for svc in running:
            say(f"  {C.stop_cmd(svc)}")
        say(L("再跑一遍；或者加 --yes，替你停、恢复完再起。", "then run this again; or add --yes and it's stopped and started again for you."))
        return 2
    try:  # 包里带了密钥，快照也带上（恢复会覆盖它们，撤回时要还原）；没带的不碰，快照里也不要
        snap = snapshot("pre-restore", secrets=bool(man.get("secrets")), picked=areas_of(man), protect=Path(path))  # 从最旧的快照恢复：别先把它清掉
    except BackupError as e:
        say(L(f"动手前的快照没做成，没动：{e}", f"The snapshot before restoring failed; nothing changed: {e}"))
        return 1
    say(L(f"现在的样子先存了一份：{C.tilde(snap['path'])}", f"Saved the current state first: {C.tilde(snap['path'])}"))
    stopped = []
    for svc in running:
        ok, err = C.control(svc, "stop")
        if not ok:
            say(L(f"停不了 {svc.name}：{err}", f"couldn't stop {svc.name}: {err}"))
            for s in stopped:
                C.control(s, "start")
            return 1
        stopped.append(svc)
    staging = Path(tempfile.mkdtemp(prefix=".mousse-restore-", dir=C.state_dir()))
    done = 0
    try:
        want = {f"files/{f['area']}/{f['path']}": f for f in man["files"]}
        with tarfile.open(path, "r:gz") as tf:
            for m in tf:
                parts = safe_parts(m.name)
                rel = "/".join(parts[1:])
                fe = want.get(rel)
                if fe is None or not m.isfile():
                    continue
                src = tf.extractfile(m)
                dst = staging / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                h = hashlib.sha256()
                with src, open(dst, "wb") as out:  # type: ignore[union-attr]
                    while chunk := src.read(CHUNK):  # type: ignore[union-attr]
                        h.update(chunk)
                        out.write(chunk)
                if h.hexdigest() != fe["sha256"]:
                    raise BackupError(L(f"{rel} 解出来对不上：包在校验之后变了？", f"{rel} doesn't match once extracted: did the archive change after verifying?"))
        for rel, fe in want.items():
            root, kind = targets[fe["area"]]
            target = root if kind == "file" else root.joinpath(*safe_parts(fe["path"]))
            if kind == "dir" and root.exists() and _escapes(target, root):
                say(L(f"  跳过 {fe['path']}：指到 {C.tilde(root)} 外面去了", f"  skipped {fe['path']}: it leads outside {C.tilde(root)}"))
                continue
            mode = int(str(fe.get("mode") or "600"), 8) & 0o777
            if fe["area"] in ("config", "secrets") or (fe["area"] == "data" and secret_file(Area("data", root, "dir"), fe["path"])):
                mode &= 0o700
            _place(staging / rel, target, mode, bool(fe.get("sqlite")))
            done += 1
        for ln in man.get("links") or []:
            root, kind = targets.get(ln.get("area"), (None, None))
            if root is None or kind != "dir":
                continue
            target = root.joinpath(*safe_parts(str(ln.get("path") or "")))
            if not target.exists() and not target.is_symlink():
                target.parent.mkdir(parents=True, exist_ok=True)
                with contextlib.suppress(OSError):
                    os.symlink(str(ln.get("target")), target)
    except (BackupError, OSError, tarfile.TarError, ValueError) as e:
        say(L(f"没恢复完（换了 {done} 个文件）：{e}", f"Restore didn't finish ({done} files replaced): {e}"))
        say(L(f"撤回到恢复之前：mousse restore {C.tilde(snap['path'])} --yes", f"To go back to before: mousse restore {C.tilde(snap['path'])} --yes"))
        for svc in stopped:
            C.control(svc, "start")
        return 1
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    for svc in stopped:
        ok, err = C.control(svc, "start")
        if not ok:
            say(L(f"{svc.name} 没起来：{err}（{C.start_cmd(svc)}）", f"{svc.name} didn't start: {err} ({C.start_cmd(svc)})"))
    say(L(f"恢复好了：{done} 个文件。撤回：mousse restore {C.tilde(snap['path'])} --yes", f"Restored {done} files. To undo: mousse restore {C.tilde(snap['path'])} --yes"))
    if not stopped and not running:
        say(L("服务没在跑：起它的时候就用上恢复的数据了", "The server wasn't running: it uses the restored data when it starts"))
    return 0


# —— 命令行 ——————————————————————————————————————————————————————————————————————————

def main_backup(a) -> int:
    if a.action == "create":
        if a.keep is not None and a.keep < 1:
            print(L("--keep 至少 1", "--keep must be at least 1"))
            return 2
        try:
            r = create(a.out, secrets=not a.no_secrets, workspace=not a.no_workspace, tree=not a.no_tree, keep=a.keep or KEEP)
        except BackupError as e:
            print(L(f"没备份成：{e}", f"Backup failed: {e}"))
            return 1
        if a.json:
            print(json.dumps(r, ensure_ascii=False, indent=2))
            return 0
        print(L(f"备份好了：{C.tilde(r['path'])}（{r['files']} 个文件，{C.human_size(r['bytes'])}，压缩后 {C.human_size(r['archiveBytes'])}）",
                f"Backed up: {C.tilde(r['path'])} ({r['files']} files, {C.human_size(r['bytes'])}, {C.human_size(r['archiveBytes'])} compressed)"))
        print("  " + ", ".join(r["areas"]))
        if r["pruned"]:
            print(L(f"  删掉了更老的 {len(r['pruned'])} 份（--keep {a.keep or KEEP}）", f"  removed {len(r['pruned'])} older ones (--keep {a.keep or KEEP})"))
        print(L("这个包和密钥一样要收好：里面有接入令牌" + ("和 API 密钥" if r["secrets"] else "（API 密钥没带：--no-secrets）") + "，别发给别人、别放进同步盘的公开目录。",
                "Keep this archive as safe as a key: it holds your access tokens" + (" and API keys" if r["secrets"] else " (API keys left out: --no-secrets)")
                + "; don't send it to anyone or put it in a shared folder."))
        return 0
    if a.action == "verify":
        r = verify(a.file)
        if a.json:
            print(json.dumps({k: v for k, v in r.items() if k != "manifest"} | {"createdAt": (r["manifest"] or {}).get("createdAt")}, ensure_ascii=False, indent=2))
        elif r["ok"]:
            m = r["manifest"]
            print(L(f"✓ 包是好的：{r['files']} 个文件，{C.human_size(r['bytes'])}，{m.get('createdAt')}，{m.get('version') or '?'} {m.get('commit') or ''}",
                    f"✓ The archive is sound: {r['files']} files, {C.human_size(r['bytes'])}, {m.get('createdAt')}, {m.get('version') or '?'} {m.get('commit') or ''}"))
        else:
            print(L("✗ 包有问题：", "✗ The archive has problems:"))
            for e in r["errors"][:30]:
                print(f"  {e}")
        return 0 if r["ok"] else 1
    found = listing()
    if not found:
        print(L(f"{C.tilde(default_dir())} 里还没有备份：mousse backup create", f"No backups in {C.tilde(default_dir())} yet: mousse backup create"))
    for p in found:
        print(f"  {C.tilde(p)}  {C.human_size(p.stat().st_size)}")
    return 0


def main_restore(a) -> int:
    if not Path(a.file).expanduser().is_file():
        print(L(f"没有这个文件：{a.file}", f"No such file: {a.file}"))
        return 2
    return restore(str(Path(a.file).expanduser()), dry_run=a.dry_run, yes=a.yes)
