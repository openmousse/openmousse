"""Markdown 存储（config.json 的 storage = "markdown"）：一条记忆一篇笔记，放在 notes_dir（比如 Obsidian 库里的一个文件夹）。

笔记就是真身，SQLite（index.db）只当检索索引和操作记录，删了能从笔记完整重建：
- 属性（YAML）放 id / kind / source / observed_at / status / tags / supersedes / created_at / updated_at，正文就是那句话。
- notes_dir 根下只放当前有效的（active / pending）；被取代的挪进归档子文件夹（archive_dir，默认「归档」/ Archive）。
  放进归档的笔记一律不算当前记忆。
- 遗忘 = 笔记改成空壳（只剩属性，文件名换成 id，因为原文件名带着内容）挪进归档 + 删索引 + 记一条不含内容的操作记录；
  以前 SQLite 存储留下的 tree.db 里同一条也清空。
- 手机上新建的、没有属性的笔记也收（id 按路径算，source = owner）；格式坏的笔记跳过并记进 issue 表，管理页顶部会提示。
- 软链不跟、隐藏文件不收：通过 MCP 碰不到这个文件夹以外的东西。
- 写入一律临时文件 + rename；服务每 5 秒看一眼文件夹（mtime / 大小 / inode 的签名），变了就整个重建索引。
- profile_note（可选）：在 notes_dir 里放一份档案，和 profile_path 双向同步。不用软链是因为 OpenClaw 的检索会跳过软链。
- 枝（2026-10-16，见 branches.py）：叶子的属性 branch: "[[枝名]]"（"[[档案]]" = 直接挂主干，没有这个属性的老笔记也算主干）；
  枝是 branches/ 里一枝一篇的笔记（属性 parent / aliases / agents / order，正文第一行写它管什么），规则是根下的 rules.md。
  这两样不算记忆：扫笔记时不当叶子收，只把枝读进索引的 branch 表。
- 改写一篇笔记（确认、取代、挪枝、改正文）只换我们写的那几个属性，用户在 Obsidian 里自己加的属性原样留着（原文一行不动）。
  遗忘例外：只留我们的骨架，别的属性一起清掉（里面可能也有内容）。
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
import sqlite3
import stat
import threading
import time
import uuid
from datetime import date, datetime
from pathlib import Path

import yaml

from . import branches as B
from . import config as C
from . import store as S

MAX_NOTE_BYTES = 64 * 1024        # 一条记忆是一句话，比这大的多半放错了地方
SETTLE_SECONDS = 2                # 档案同步：改了的那边不到 2 秒先不动，可能还没写完
CURRENT = ("active", "pending")
COLS = ("id", "path", "text", "kind", "tags", "source", "observed_at", "status", "supersedes", "created_at", "updated_at", "branch",
        *S.EXTRA)

INDEX_SCHEMA = """
CREATE TABLE IF NOT EXISTS tree (
  id TEXT PRIMARY KEY,
  path TEXT NOT NULL,
  text TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'fact',
  tags TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  supersedes TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  branch TEXT,
  quote TEXT NOT NULL DEFAULT '',
  platform TEXT NOT NULL DEFAULT '',
  thread TEXT NOT NULL DEFAULT '',
  message TEXT NOT NULL DEFAULT '',
  cues TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS issue (path TEXT NOT NULL, problem TEXT NOT NULL);
""" + S.SCHEMA  # tree 已经建好，S.SCHEMA 里的 CREATE TABLE IF NOT EXISTS tree 跳过；索引、FTS、触发器、activity、meta、branch 照建


def root() -> Path:
    return C.notes_dir()


def archive() -> str:
    return C.archive_name()


def connect(sync: bool = True) -> sqlite3.Connection:
    """sync=False：只读索引，不看文件夹、不刷新（内核备料这类只读的地方用）。"""
    C.HOME.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(C.INDEX, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.executescript(INDEX_SCHEMA)
    if S.migrate(conn):  # 0.2 的索引没有 branch 列、0.3 的没有出处和 cues：补上了，下次看文件夹整个重建（从笔记里读回来）
        _set_meta(conn, "sig", None)
        conn.commit()
    if sync:
        refresh(conn)
        S.setup(conn)
    return conn


def _meta(conn: sqlite3.Connection, k: str) -> str | None:
    r = conn.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
    return r[0] if r else None


def _set_meta(conn: sqlite3.Connection, k: str, v: str | None) -> None:
    if v is None:
        conn.execute("DELETE FROM meta WHERE k=?", (k,))
    else:
        conn.execute("INSERT OR REPLACE INTO meta(k, v) VALUES (?,?)", (k, v))


# ---------- 锁和文件 ----------

_held = threading.local()


@contextlib.contextmanager
def locked():
    """服务、命令行、你自己的 agent 可能同时写：写笔记和重建索引一律排队（跨进程文件锁，同一线程可重入）。"""
    if getattr(_held, "n", 0):
        _held.n += 1
        try:
            yield
        finally:
            _held.n -= 1
        return
    C.HOME.mkdir(parents=True, exist_ok=True)
    with open(C.LOCK, "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        _held.n = 1
        try:
            yield
        finally:
            _held.n = 0
            fcntl.flock(f, fcntl.LOCK_UN)


def atomic_write(path: Path, data: str | bytes, mode: int = 0o600) -> None:
    """临时文件 + rename：同步软件和别的进程只会看到旧文件或完整的新文件。临时文件以 . 开头，同步和扫描都跳过。"""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    raw = data.encode("utf8") if isinstance(data, str) else data
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _read_bytes(p: Path) -> bytes | None:
    try:
        return p.read_bytes()
    except OSError:
        return None


# ---------- 笔记格式 ----------

_PLAIN = re.compile(r"[^\W\d][\w./-]*")
_YAML_WORDS = {"y", "n", "yes", "no", "true", "false", "on", "off", "null"}
_TIME = re.compile(r"\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2}(\.\d+)?)?([+-]\d{2}:\d{2}|Z)?)?")
_FM = re.compile(r"\A﻿?---[ \t]*\r?\n(.*?)^(?:---|\.\.\.)[ \t]*(?:\r?\n|\Z)", re.S | re.M)
_ID = re.compile(r"[\w-]{1,64}")


def _yaml_str(v: str) -> str:
    s = str(v)
    return s if _PLAIN.fullmatch(s) and s.lower() not in _YAML_WORDS else json.dumps(s, ensure_ascii=False)


def _yaml_time(v: str) -> str:
    s = str(v)
    return s if _TIME.fullmatch(s) else json.dumps(s, ensure_ascii=False)


def split_tags(raw) -> list[str]:
    """标签：逗号 / 顿号分隔的字符串或列表 → 去 #、空格换成 -（Obsidian 的标签不能带空格）、去重。"""
    items = raw if isinstance(raw, list) else re.split(r"[,，、]", raw) if isinstance(raw, str) else []
    out: list[str] = []
    for t in items:
        t = re.sub(r"\s+", "-", str(t).strip().lstrip("#").strip()) if isinstance(t, str) else ""
        if t and t not in out:
            out.append(t)
    return out


NOTE_KEYS = ("id", "kind", "source", "observed_at", "status", "branch", "tags", "supersedes", *S.EXTRA, "created_at", "updated_at")
BRANCH_KEYS = ("parent", "aliases", "agents", "order")
_KEY = re.compile(r"""^(?:"([^"]*)"|'([^']*)'|([^\s#\-'"][^:]*?))[ \t]*:(?:[ \t]|$)""")


def _blocks(fm: str) -> list[tuple[str | None, list[str]]]:
    """属性区原文按顶层的键切开：[(键, 这一段的原文行)]。列表项、缩进的续行、注释跟着上一个键；最前面的空行、注释键是 None。"""
    out: list[tuple[str | None, list[str]]] = []
    for line in fm.splitlines():
        m = _KEY.match(line) if line and not line[0].isspace() else None
        if m:
            out.append(((m.group(1) if m.group(1) is not None else m.group(2) if m.group(2) is not None else m.group(3)).strip(), [line]))
        elif out:
            out[-1][1].append(line)
        else:
            out.append((None, [line]))
    return out


def _merge(ours: dict[str, list[str]], keys: tuple[str, ...], keep: str | None) -> list[str]:
    """我们要写的属性（键 → 几行）放回原来的属性区：原来有的键就地换掉，别的键（用户加的、这次不写的）原文一行不动；
    原来没有的接在我们的最后一个键后面（一个都没有就放最前面）。keep = None：只写我们的。"""
    if keep is None:
        return [x for k in keys if k in ours for x in ours[k]]
    blocks = _blocks(keep)
    last = max((i for i, (k, _) in enumerate(blocks) if k in keys), default=-1)
    lines: list[str] = []
    done: set[str] = set()

    def rest() -> list[str]:
        out = [x for k in keys if k in ours and k not in done for x in ours[k]]
        done.update(ours)
        return out
    if last < 0:
        lines += rest()
    for i, (k, blk) in enumerate(blocks):
        if k in ours:
            if k not in done:
                lines += ours[k]
                done.add(k)
        else:
            lines += blk
        if i == last:
            lines += rest()
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def render_note(m: dict, keep: str | None = None) -> str:
    """一片叶子的笔记。m["branch_link"]：写进 branch 属性的（"[[人]]"）；None = 不写（原来有就原样留着）。
    keep = 原来的属性区原文：用户加的属性原样留着（见 _merge）。"""
    ours = {"id": [f"id: {_yaml_str(m['id'])}"], "kind": [f"kind: {_yaml_str(m['kind'])}"], "source": [f"source: {_yaml_str(m['source'])}"],
            "observed_at": [f"observed_at: {_yaml_time(m['observed_at'])}"], "status": [f"status: {_yaml_str(m['status'])}"]}
    if m.get("branch_link"):
        ours["branch"] = [f"branch: {json.dumps(m['branch_link'], ensure_ascii=False)}"]
    tags = split_tags(m.get("tags") or "")
    if tags:
        ours["tags"] = ["tags:", *(f"  - {_yaml_str(t)}" for t in tags)]
    if m.get("supersedes"):
        ours["supersedes"] = [f"supersedes: {_yaml_str(m['supersedes'])}"]
    for k in S.PROV:  # 出处（原话一律加引号：里面可能有冒号、井号）
        if m.get(k):
            ours[k] = [f"{k}: {json.dumps(str(m[k]), ensure_ascii=False)}"]
    cues = S.cue_list(m.get("cues") or "")
    if cues:
        ours["cues"] = ["cues:", *(f"  - {json.dumps(c, ensure_ascii=False)}" for c in cues)]
    ours["created_at"] = [f"created_at: {_yaml_time(m['created_at'])}"]
    ours["updated_at"] = [f"updated_at: {_yaml_time(m['updated_at'])}"]
    body = (m["body"] if m.get("body") is not None else m.get("text") or "").strip()
    return "\n".join(["---", *_merge(ours, NOTE_KEYS, keep), "---"]) + "\n" + (body + "\n" if body else "")


def render_branch(*, parent: str, aliases: list[str], agents: list[str], order: int, about: str, keep: str | None = None,
                  body: str | None = None, only: tuple[str, ...] | None = None) -> str:
    """一根枝的笔记：parent 链到上一级（"[[档案]]" = 长在主干上）、aliases、agents、order；正文第一行写它管什么。
    only：只写这几个属性（改一根枝时），别的原样留着。"""
    ours = {"parent": [f"parent: {json.dumps(f'[[{parent}]]', ensure_ascii=False)}"],
            "aliases": ["aliases:", *(f"  - {json.dumps(a, ensure_ascii=False)}" for a in aliases)] if aliases else ["aliases: []"],
            "agents": ["agents:", *(f"  - {_yaml_str(a)}" for a in agents)] if agents else ["agents: []"],
            "order": [f"order: {int(order)}"]}
    if only is not None:
        ours = {k: v for k, v in ours.items() if k in only}
    text = (body if body is not None else about).strip()
    return "\n".join(["---", *_merge(ours, BRANCH_KEYS, keep), "---"]) + "\n" + (text + "\n" if text else "")


def front(raw: str) -> tuple[str | None, str]:
    """笔记原文 → (属性区原文，没有就 None；正文)。"""
    m = _FM.match(raw)
    return (m.group(1), raw[m.end():]) if m else (None, raw)


def parse_note(raw: str) -> tuple[dict | None, str, str | None]:
    """笔记 → (属性, 正文, 问题)。没有属性区的笔记：属性是 {}，全文算正文。"""
    m = _FM.match(raw)
    if not m:
        if raw.lstrip("﻿").startswith("---"):
            return None, "", C.L("属性区开头有 ---，但找不到结尾的 ---", "the properties block opens with --- but never closes")
        return {}, raw, None
    try:
        # BaseLoader 不做任何类型推断（日期、数字都当字符串），也不会构造对象
        fm = yaml.load(m.group(1), Loader=yaml.BaseLoader)  # noqa: S506
    except yaml.YAMLError as e:
        return None, "", C.L("属性（YAML）写坏了：", "broken properties (YAML): ") + " ".join(str(e).split())[:120]
    if fm is None:
        fm = {}
    if not isinstance(fm, dict):
        return None, "", C.L("属性区不是「名字: 值」的格式", "the properties block is not a list of name: value pairs")
    return fm, raw[m.end():], None


def _s(v) -> str:
    if isinstance(v, list):
        v = v[0] if v else ""
    return v.strip() if isinstance(v, str) else ""


_CUT = re.compile(r"[：；。！？（(，\n]|[:;!?,.](?=\s)")
_BAD = re.compile(r'[\\/:*?"<>|#^\[\]\x00-\x1f]')


def title_of(text: str, limit: int = 48) -> str:
    """文件名里的标题：那句话的第一个分句（冒号、分号、句号、括号、逗号前面）；不到 8 个字就接着往后取，太长截断。"""
    text = " ".join(text.split())
    end = len(text)
    for m in _CUT.finditer(text):
        if m.start() >= 8:
            end = m.start()
            break
    t = text[:end]
    if len(t) > limit:
        cut = t.rfind(" ", limit - 16, limit)
        t = t[:cut if cut > 0 else limit].rstrip() + "…"
    t = " ".join(_BAD.sub(" ", t).split()).strip(" .")
    return t or C.L("记忆", "memory")


def _unique_path(folder: Path, stem: str, mid: str) -> Path:
    """同名（不分大小写：Mac / iPhone 的文件系统不分）就在后面加 id。"""
    taken = {p.name.lower() for p in folder.iterdir()} if folder.is_dir() else set()
    for name in (stem, f"{stem} {mid[:4]}", f"{stem} {mid}"):
        if f"{name}.md".lower() not in taken:
            return folder / f"{name}.md"
    return folder / f"{mid}.md"


def _day(v: str | None) -> str:
    try:
        return date.fromisoformat((v or "")[:10]).isoformat()
    except ValueError:
        return S.today()


def _new_id(conn: sqlite3.Connection) -> str:
    while True:
        mid = uuid.uuid4().hex[:12]
        # 至少带一个字母：纯数字的 id 在别的 YAML 读法里会被当成数
        if not mid.isdigit() and not conn.execute("SELECT 1 FROM tree WHERE id=?", (mid,)).fetchone():
            return mid


# ---------- 扫描、重建索引 ----------

def _walk(base: Path) -> tuple[list[tuple[str, int, int, int]], list[tuple[str, str]]]:
    """→ ([(相对路径, mtime_ns, 大小, inode)], [(相对路径, 问题)])。跳过隐藏文件和非 .md；软链不跟，也不收。"""
    files: list[tuple[str, int, int, int]] = []
    issues: list[tuple[str, str]] = []
    link = C.L("是软链，跳过（只收这个文件夹里的真实文件）", "is a symlink, skipped (only real files in this folder count)")
    for dirpath, dirnames, filenames in os.walk(base):
        here = Path(dirpath)
        keep = []
        for d in sorted(dirnames):
            if d.startswith("."):
                continue
            if (here / d).is_symlink():
                issues.append(((here / d).relative_to(base).as_posix(), link))
                continue
            keep.append(d)
        dirnames[:] = keep
        for name in sorted(filenames):
            if name.startswith(".") or not name.endswith(".md"):
                continue
            p = here / name
            try:
                st = os.lstat(p)
            except OSError:
                continue
            rel = p.relative_to(base).as_posix()
            if stat.S_ISLNK(st.st_mode):
                issues.append((rel, link))
            elif stat.S_ISREG(st.st_mode):
                files.append((rel, st.st_mtime_ns, st.st_size, st.st_ino))
    return files, issues


def _profile_source() -> tuple[Path, str | None]:
    """档案要点从哪读：设了 profile_note 就读笔记文件夹里那份（真身），否则读 profile_path。
    → (文件, 它在笔记文件夹里的相对路径；在文件夹外是 None)"""
    name = C.load().get("profile_note") or ""
    p = root() / name if name else S.profile_path()
    try:
        return p, p.resolve().relative_to(root().resolve()).as_posix()
    except (ValueError, OSError):
        return p, None


def _profile_rows(raw: str, mtime: datetime, path: str) -> list[dict]:
    """档案里 `## 小节` 下 `- ` 开头的要点行，一行一条（source=profile）。和 SQLite 存储的 id 算法一样。"""
    fm, body, _ = parse_note(raw)
    if fm is None:
        body = raw
    ts = mtime.isoformat(timespec="seconds")
    section, rows = "", {}
    for line in body.splitlines():
        if line.startswith("## "):
            section = line[3:].strip()
        elif line.startswith("- ") and section and line[2:].strip():
            text = line[2:].strip()
            pid = "p" + hashlib.sha1((section + text).encode()).hexdigest()[:11]
            rows[pid] = {"id": pid, "path": path, "text": text, "kind": "profile", "tags": section, "source": "profile",
                         "observed_at": mtime.date().isoformat(), "status": "active", "supersedes": None,
                         "created_at": ts, "updated_at": ts, "branch": None, **dict.fromkeys(S.EXTRA, "")}
    return list(rows.values())


def _note_row(rel: str, raw: str, mtime: datetime, issues: list) -> dict | None:
    fm, body, problem = parse_note(raw)
    if fm is None:
        issues.append((rel, problem))
        return None
    nid = _s(fm.get("id")) or "n" + hashlib.sha1(rel.encode()).hexdigest()[:11]
    if not _ID.fullmatch(nid):
        issues.append((rel, C.L("id 只能用字母、数字、- 和 _，跳过", "id may only use letters, digits, - and _; skipped")))
        return None
    kind = _s(fm.get("kind")) or "fact"
    if kind not in S.KINDS[:-1]:
        issues.append((rel, C.L(f"kind「{kind}」不认识，按 fact 算", f'unknown kind "{kind}", treated as fact')))
        kind = "fact"
    status = _s(fm.get("status")) or "active"
    if status not in S.STATUSES:
        issues.append((rel, C.L(f"status「{status}」不认识，按 active 算", f'unknown status "{status}", treated as active')))
        status = "active"
    if rel.startswith(archive() + "/") and status in CURRENT:
        status = "superseded"   # 放进归档 = 不再是当前记忆
    text = " ".join(body.split())
    tags = ",".join(split_tags(fm.get("tags")))
    prov = S.provenance(**{k: _s(fm.get(k)) for k in S.PROV})
    cues = "\n".join(S.cue_list(fm.get("cues")))
    if status == "retracted":
        text = tags = cues = ""
        prov = dict.fromkeys(S.PROV, "")
    elif not text and status in CURRENT:
        return None             # 空笔记（多半是刚新建、还没写），不收也不报
    observed = _s(fm.get("observed_at"))
    try:
        observed = date.fromisoformat(observed[:10]).isoformat()
    except ValueError:
        if observed:
            issues.append((rel, C.L(f"observed_at「{observed}」不是日期，按文件修改日期算",
                                    f'observed_at "{observed}" is not a date; using the file date')))
        observed = mtime.date().isoformat()
    created = _s(fm.get("created_at")) or mtime.isoformat(timespec="seconds")
    source = _s(fm.get("source")) or "owner"
    return {"id": nid, "path": rel, "text": text, "kind": kind, "tags": tags,
            "source": "owner" if source == "profile" else source, "observed_at": observed, "status": status,
            "supersedes": _s(fm.get("supersedes")) or None, "created_at": created,
            "updated_at": _s(fm.get("updated_at")) or created, "branch": B.link_name(fm.get("branch")) or None, **prov, "cues": cues}


def _branch_row(rel: str, raw: str, mtime: datetime, issues: list) -> dict | None:
    """branches/ 里的一篇 → 一根枝（索引的 branch 表一行）。文件名就是枝名。"""
    fm, body, problem = parse_note(raw)
    if fm is None:
        issues.append((rel, problem))
        return None
    name = Path(rel).stem
    try:
        B.clean_name(name)
    except ValueError as e:
        issues.append((rel, str(e)))
        return None
    try:
        order = int(_s(fm.get("order")) or 0)
    except ValueError:
        order = 0
    about = next((x.strip().lstrip("#").strip() for x in body.splitlines() if x.strip()), "")
    ts = mtime.isoformat(timespec="seconds")
    return {"name": name, "parent": B.link_name(fm.get("parent")), "about": about,
            "aliases": json.dumps(B.as_list(fm.get("aliases")), ensure_ascii=False), "agents": json.dumps(B.as_list(fm.get("agents"))),
            "ord": order, "path": rel, "created_at": ts, "updated_at": ts}


def _special(rel: str) -> str | None:
    """不算记忆的：branches/ 下的（枝）、根下的 rules.md（规则）。"""
    if rel == C.RULES:
        return "rules"
    if rel.startswith(C.BRANCHES + "/"):
        return "branch" if rel.count("/") == 1 else "skip"
    return None


def _read_all(base: Path, files: list, issues: list) -> tuple[list[dict], list[dict]]:
    """→ (叶子和档案要点, 枝)。叶子的 branch 按枝名、别名认成枝名（主干 = None，对不上的原样留着）。"""
    tz = C.now().tzinfo
    prof_file, prof_name = _profile_source()
    rows: list[dict] = []
    brows: list[dict] = []
    seen: dict[str, str] = {}
    for rel, mtime_ns, _size, _ino in files:
        if _special(rel) != "branch":
            continue
        try:
            r = _branch_row(rel, (base / rel).read_text(encoding="utf8"), datetime.fromtimestamp(mtime_ns / 1e9, tz=tz), issues)
        except (OSError, UnicodeDecodeError):
            continue
        if r is None:
            continue
        if any(b["name"].casefold() == r["name"].casefold() for b in brows):
            issues.append((rel, C.L("和另一根枝重名（不分大小写），跳过", "same name as another branch (ignoring case); skipped")))
            continue
        brows.append(r)
    look = B.lookup(None, [{"name": b["name"], "aliases": B.as_list(b["aliases"])} for b in brows])
    if prof_name is None:  # 档案在笔记文件夹外（profile_path）
        with contextlib.suppress(OSError, UnicodeDecodeError):
            st = prof_file.stat()
            rows += _profile_rows(prof_file.read_text(encoding="utf8"), datetime.fromtimestamp(st.st_mtime, tz=tz), str(prof_file))
            seen.update({r["id"]: str(prof_file) for r in rows})
    # 根下排在归档前面：id 撞了留根下那篇
    for rel, mtime_ns, size, _ino in sorted(files, key=lambda f: (f[0].startswith(archive() + "/"), f[0])):
        if _special(rel) and rel != prof_name:
            continue
        mtime = datetime.fromtimestamp(mtime_ns / 1e9, tz=tz)
        if size > (1024 * 1024 if rel == prof_name else MAX_NOTE_BYTES):
            issues.append((rel, C.L(f"文件太大（{size // 1024} KB），不像一条记忆，跳过",
                                    f"file too large ({size // 1024} KB) for a memory; skipped")))
            continue
        try:
            raw = (base / rel).read_text(encoding="utf8")
        except UnicodeDecodeError:
            issues.append((rel, C.L("不是 UTF-8 文本，跳过", "not UTF-8 text; skipped")))
            continue
        except OSError:
            continue
        found = _profile_rows(raw, mtime, rel) if rel == prof_name else [_note_row(rel, raw, mtime, issues)]
        for r in found:
            if r is None:
                continue
            if r["id"] in seen:
                issues.append((rel, C.L(f"id {r['id']} 和「{seen[r['id']]}」重复，跳过", f"id {r['id']} duplicates {seen[r['id']]}; skipped")))
                continue
            seen[r["id"]] = rel
            if r.get("branch"):
                r["branch"] = look.get(r["branch"].casefold(), r["branch"]) or None
            rows.append(r)
    return rows, brows


BCOLS = ("name", "parent", "about", "aliases", "agents", "ord", "path", "created_at", "updated_at")


def _write_index(conn: sqlite3.Connection, rows: list[dict], brows: list[dict], issues: list, sig: str) -> dict:
    def key(r):
        return (r["path"], r["text"], r["status"], r["tags"], r["kind"], r["branch"], r["quote"], r["cues"])
    old = {r["id"]: key(r) for r in conn.execute("SELECT * FROM tree WHERE source != 'profile'")}
    new = {r["id"]: key(r) for r in rows if r["source"] != "profile"}
    conn.execute("DELETE FROM tree")
    conn.executemany(f"INSERT INTO tree({','.join(COLS)}) VALUES ({','.join('?' * len(COLS))})",
                     [tuple(r[c] for c in COLS) for r in rows])
    conn.execute("DELETE FROM branch")
    conn.executemany(f"INSERT INTO branch({','.join(BCOLS)}) VALUES ({','.join('?' * len(BCOLS))})",
                     [tuple(b[c] for c in BCOLS) for b in brows])
    conn.execute("DELETE FROM issue")
    conn.executemany("INSERT INTO issue(path, problem) VALUES (?,?)", issues)
    _set_meta(conn, "sig", sig)
    conn.commit()
    return {"fresh": not old, "added": new.keys() - old.keys(), "removed": old.keys() - new.keys(),
            "changed": {k for k in new.keys() & old.keys() if new[k] != old[k]}}


def _mirror_profile(conn: sqlite3.Connection, base: Path) -> bool:
    """profile_note ↔ profile_path 双向同步：哪边改了抄到另一边；两边都改了以笔记为准，另一版追加到 profile-conflicts.md。"""
    name = C.load().get("profile_note") or ""
    if not name or not base.parent.is_dir():
        return False
    note, other = base / name, S.profile_path()
    a, b = _read_bytes(note), _read_bytes(other)
    if a is None and b is None:
        return False
    ha = hashlib.sha1(a).hexdigest() if a is not None else None
    hb = hashlib.sha1(b).hexdigest() if b is not None else None
    last = _meta(conn, "profile")
    if ha == hb:
        if last != ha:
            _set_meta(conn, "profile", ha)
            conn.commit()
        return False
    now = time.time()
    for p, h in ((note, ha), (other, hb)):
        with contextlib.suppress(OSError):
            if h is not None and h != last and now - p.stat().st_mtime < SETTLE_SECONDS:
                return False  # 改了的那边可能还没写完，下一轮再看
    target = other.resolve()
    mode = target.stat().st_mode & 0o777 if b is not None else 0o644
    if a is None:
        atomic_write(note, b)
        final, msg = hb, C.L(f"笔记文件夹里还没有档案，从 {other.name} 抄了一份", f"No profile note yet; copied {other.name} into the notes folder")
    elif b is None:
        atomic_write(target, a, mode)
        final, msg = ha, C.L(f"{other.name} 不见了，从笔记里的档案恢复了一份", f"{other.name} was missing; restored it from the profile note")
    elif hb == last:
        atomic_write(target, a, mode)
        final, msg = ha, C.L(f"档案笔记改了，已同步到 {other.name}", f"The profile note changed; synced to {other.name}")
    elif ha == last:
        atomic_write(note, b)
        final, msg = hb, C.L(f"{other.name} 改了，已同步到档案笔记", f"{other.name} changed; synced to the profile note")
    else:
        with (C.HOME / "profile-conflicts.md").open("a", encoding="utf8") as f:
            f.write(f"\n## {S.now_iso()} · {other}\n{b.decode('utf8', 'replace')}\n")
        atomic_write(target, a, mode)
        final, msg = ha, C.L(f"档案两边同时改了，以笔记为准；{other.name} 那版存进了 profile-conflicts.md",
                             f"Both copies of the profile changed; kept the note, saved the other {other.name} to profile-conflicts.md")
    _set_meta(conn, "profile", final)
    conn.commit()
    S.log(conn, "tree", msg)
    conn.commit()
    return True


def _settling(files: list) -> bool:
    now = time.time_ns()
    return any(0 <= now - m < 1_000_000_000 for _, m, _, _ in files)


def refresh(conn: sqlite3.Connection, *, force: bool = False, expect: set[str] | frozenset = frozenset(),
            actor: str | None = "notes") -> dict:
    """看一眼笔记文件夹：档案同步；有变化（或 force）就整个重建索引、需要时重新导出 TREE.md。
    expect = 这次自己写的 id，不算进「文件夹里有变动」；actor=None 不记操作记录。"""
    base = root()
    with locked():
        wrote = _mirror_profile(conn, base)
        if not base.is_dir():
            if _meta(conn, "missing") is None:
                _set_meta(conn, "missing", str(base))
                S.log(conn, "tree", C.L("笔记文件夹不见了，检索先用上次的索引", "The notes folder is missing; search keeps using the last index"))
                conn.commit()
            return {}
        if _meta(conn, "missing") is not None:
            _set_meta(conn, "missing", None)
            conn.commit()
        files, issues = _walk(base)
        prof_file, prof_name = _profile_source()
        extra = []
        if prof_name is None:  # 档案在文件夹外：它变了也要重建
            with contextlib.suppress(OSError):
                st = prof_file.stat()
                extra = [str(prof_file), st.st_mtime_ns, st.st_size, st.st_ino]
        sig = hashlib.sha1(json.dumps([files, extra], ensure_ascii=False).encode()).hexdigest()
        force = force or wrote  # 档案同步刚写了库里那份：自己写的是完整的，不用等
        if not force and _meta(conn, "sig") == sig:
            return {}
        if not force and _settling(files):
            return {}  # 有文件是 1 秒内刚写的（同步可能还没写完），下一轮再重建
        rows, brows = _read_all(base, files, issues)
        diff = _write_index(conn, rows, brows, issues, sig)
        S.export(conn)
        if actor and not diff["fresh"]:
            n = {k: len(diff[k] - expect) for k in ("added", "changed", "removed")}
            if any(n.values()):
                S.log(conn, actor, C.L(f"笔记文件夹有变动：新增 {n['added']}、改了 {n['changed']}、删了 {n['removed']}",
                                       f"Notes folder changed: {n['added']} added, {n['changed']} changed, {n['removed']} removed"))
                conn.commit()
        issue_sig = hashlib.sha1(json.dumps(sorted(issues), ensure_ascii=False).encode()).hexdigest()
        if _meta(conn, "issues") != issue_sig:
            _set_meta(conn, "issues", issue_sig)
            if issues:
                S.log(conn, "tree", C.L(f"有 {len(issues)} 处笔记格式问题（跳过了或按默认值算），见管理页或 mousse-tree check",
                                        f"{len(issues)} note format problem(s) (skipped or defaulted); see the admin page or mousse-tree check"))
            conn.commit()
        return diff


# ---------- 写 ----------

def _find(conn: sqlite3.Connection, mid: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM tree WHERE id=?", (mid,)).fetchone()


def _base_ok(base: Path) -> None:
    if not base.parent.is_dir():
        raise RuntimeError(C.L(f"笔记文件夹的上一级不存在：{base.parent}", f"the notes folder's parent does not exist: {base.parent}"))


def add(conn: sqlite3.Connection, *, text: str, source: str, kind: str = "fact", tags: str = "",
        observed_at: str | None = None, status: str = "active", supersedes: str | None = None,
        branch: str | None = None, agent: str | None = None, prov: dict | None = None, cues: list[str] = ()) -> dict:
    """写一片叶子（查重、自动挂枝和 SQLite 存储一样，见 store.prepare）。新笔记一律写上 branch 属性（主干就是 "[[档案]]"）。
    prov / cues：store.add 整理好的出处和「用得上的时候」（没有就不写这几个属性）。"""
    if not " ".join((text or "").split()):
        raise ValueError("empty text")
    kind = kind if kind in S.KINDS[:-1] else "fact"
    status = status if status in CURRENT else "active"
    base = root()
    _base_ok(base)
    with locked():
        refresh(conn, force=True)  # 去重、找旧笔记、认枝都要对着文件夹的最新状态
        p = S.prepare(conn, text=text, source=source, tags=tags, supersedes=supersedes, branch=branch, agent=agent)
        if p["duplicate"]:
            return p
        text = p["text"]
        mid, ts, obs = _new_id(conn), S.now_iso(), _day(observed_at)
        note = {"id": mid, "text": text, "kind": kind, "tags": tags, "source": source, "observed_at": obs,
                "status": status, "supersedes": supersedes or None, "created_at": ts, "updated_at": ts,
                "branch_link": f"[[{p['branch'] or S.trunk()}]]", **(prov or {}), "cues": list(cues) or S.inherited_cues(p)}
        atomic_write(_unique_path(base, f"{obs} {title_of(text)}", mid), render_note(note))
        touched = {mid}
        if p["old"]:
            _restatus(base, p["old"], "superseded", ts)
            touched.add(p["old"]["id"])
        refresh(conn, force=True, expect=touched)
        S.log(conn, source, C.L(f"新增 1 条（{kind}，{status}）", f"Added 1 ({kind}, {status})"))
        conn.commit()
    return S.result(mid, p)


def write(conn: sqlite3.Connection, *, text: str, source: str, kind: str, tags: str, observed_at: str | None,
          branch: str | None, replaces: list[str], actor: str, prov: dict | None = None, cues: list[str] = ()) -> str:
    """每周修剪用（store.write）：不查重，长一片新叶子，replaces 里当前的旧叶子都标成被取代、挪进归档。→ 新叶子的 id。"""
    base = root()
    _base_ok(base)
    with locked():
        refresh(conn, force=True)
        mid, ts, obs = _new_id(conn), S.now_iso(), _day(observed_at)
        note = {"id": mid, "text": text, "kind": kind, "tags": tags, "source": source, "observed_at": obs, "status": "active",
                "supersedes": replaces[0] if replaces else None, "created_at": ts, "updated_at": ts,
                "branch_link": f"[[{branch or S.trunk()}]]", **(prov or {}), "cues": list(cues)}
        atomic_write(_unique_path(base, f"{obs} {title_of(text)}", mid), render_note(note))
        touched = {mid}
        for oid in replaces:
            old = _find(conn, oid)
            if old and old["status"] in CURRENT and old["source"] != "profile":
                _restatus(base, old, "superseded", ts)
                touched.add(oid)
        refresh(conn, force=True, expect=touched)
        S.log(conn, actor, C.L(f"新增 1 条（{kind}，取代 {len(replaces)} 条）", f"Added 1 ({kind}, replacing {len(replaces)})"))
        conn.commit()
    return mid


def _restatus(base: Path, row, status: str, ts: str, text: str | None = None, branch_link: str | None = None) -> None:
    """改状态（和 / 或正文、挂的枝），顺便挪位置：当前的放根下，不是当前的放归档。用户加的属性原样留着（render_note 的 keep）。"""
    src = base / row["path"]
    try:
        raw = src.read_text(encoding="utf8")
    except OSError:
        raw = None
    fm, body, _ = parse_note(raw) if raw is not None else (None, "", None)
    keep = front(raw)[0] if raw is not None and fm is not None else None
    m = dict(row)
    m.update(status=status, updated_at=ts, branch_link=branch_link,
             body=text if text is not None else body.strip() if fm is not None and body.strip() else row["text"])
    folder = base if status in CURRENT else base / archive()
    dst = src if src.parent == folder else _unique_path(folder, src.stem, row["id"])
    atomic_write(dst, render_note(m, keep=keep))
    if dst != src:
        src.unlink(missing_ok=True)


def set_branch(conn: sqlite3.Connection, mid: str, target: str, actor: str) -> str:
    """挪枝（store.set_branch 已经认好枝名，"" = 主干）：只换 branch 属性，别的一个字不动。→ 挪到的那根的名字。"""
    with locked():
        refresh(conn, force=True)
        row = _find(conn, mid)
        if not row or row["source"] == "profile" or row["status"] == "retracted":
            raise LookupError(mid)
        if (row["branch"] or "") != target:
            _restatus(root(), row, row["status"], S.now_iso(), branch_link=f"[[{target or S.trunk()}]]")
            refresh(conn, force=True, expect={mid})
            S.log(conn, actor, C.L(f"1 片挪到「{target or S.trunk()}」", f'Moved 1 to "{target or S.trunk()}"'))
            conn.commit()
    return target or S.trunk()


def write_branch(conn: sqlite3.Connection, name: str, *, parent: str, about: str, aliases: list[str], agents: list[str], order: int) -> None:
    """长一根新枝：branches/<枝名>.md（branches.create 已经查过重名）。"""
    base = root()
    _base_ok(base)
    path = base / C.BRANCHES / f"{name}.md"
    with locked():
        if path.exists():
            raise ValueError(C.L(f"已经有「{name}」这根枝的笔记了", f'There\'s already a note for branch "{name}"'))
        atomic_write(path, render_branch(parent=parent or S.trunk(), aliases=aliases, agents=agents, order=order, about=about))
        refresh(conn, force=True, actor=None)


def update_branch(conn: sqlite3.Connection, name: str, *, agents: list[str] | None = None, parent: str | None = None) -> None:
    """改一根枝的笔记（加一个 Agent、换上一级）：只换这几个属性，用户加的属性、正文原样留着。"""
    with locked():
        refresh(conn, force=True, actor=None)
        b = conn.execute("SELECT * FROM branch WHERE name=?", (name,)).fetchone()
        if b is None or not b["path"]:
            raise LookupError(name)
        path = root() / b["path"]
        raw = path.read_text(encoding="utf8")
        keep, body = front(raw)
        only = tuple(k for k, v in (("agents", agents), ("parent", parent)) if v is not None)
        atomic_write(path, render_branch(parent=(parent if parent is not None else b["parent"]) or S.trunk(),
                                         aliases=B.as_list(b["aliases"]), agents=agents if agents is not None else B.as_list(b["agents"]),
                                         order=b["ord"] or 0, about=b["about"] or "", keep=keep, body=body, only=only))
        refresh(conn, force=True, actor=None)


def _hollow(base: Path, row: sqlite3.Row, ts: str) -> None:
    """遗忘：只留属性的空壳，文件名换成 id（原文件名带着内容），放进归档。"""
    shell = {k: row[k] for k in ("id", "kind", "source", "observed_at", "supersedes", "created_at")}
    shell.update(status="retracted", updated_at=ts, tags="", text="")
    dst = base / archive() / f"{row['id']}.md"
    atomic_write(dst, render_note(shell))
    src = base / row["path"]
    if src != dst:
        src.unlink(missing_ok=True)


def _scrub_sqlite(mid: str, ts: str) -> None:
    """以前用 SQLite 存储留下的 tree.db 里同一条也清空，遗忘要彻底。"""
    if not C.DB.exists():
        return
    with contextlib.suppress(sqlite3.Error), contextlib.closing(sqlite3.connect(C.DB, timeout=10)) as c:
        if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='tree'").fetchone():
            have = {r[1] for r in c.execute("PRAGMA table_info(tree)")}
            more = "".join(f", {k}=''" for k in ("quote", "thread", "message", "cues") if k in have)  # 0.4 起的原话、「用得上的时候」
            c.execute(f"UPDATE tree SET status='retracted', text='', tags=''{more}, updated_at=? "  # noqa: S608 — 列名是常量
                      "WHERE id=? AND (text != '' OR tags != '' OR status != 'retracted')", (ts, mid))
            c.commit()


def set_status(conn: sqlite3.Connection, mid: str, status: str, actor: str) -> bool:
    if status not in S.STATUSES:
        return False
    base = root()
    with locked():
        refresh(conn, force=True)  # 按最新状态找文件，用户可能刚改过名
        row = _find(conn, mid)
        if not row or row["source"] == "profile":
            return False
        ts = S.now_iso()
        if status == "retracted":
            if row["status"] != "retracted":  # 已经是空壳就不再动
                _hollow(base, row, ts)
                _scrub_sqlite(mid, ts)
                refresh(conn, force=True, expect={mid})
                S.log(conn, actor, C.L(f"遗忘 1 条（{mid}）", f"Forgot 1 ({mid})"))
                conn.commit()
            return True
        if row["status"] == "retracted":
            return False  # 空壳没有内容，回不来
        _restatus(base, row, status, ts)
        refresh(conn, force=True, expect={mid})
        S.log(conn, actor, C.L(f"1 条改为 {status}", f"1 set to {status}"))
        conn.commit()
    return True


def edit(conn: sqlite3.Connection, mid: str, text: str, actor: str) -> bool:
    text = " ".join((text or "").split())
    with locked():
        refresh(conn, force=True)  # 按最新状态找文件，用户可能刚改过名
        row = _find(conn, mid)
        if not row or row["source"] == "profile" or row["status"] == "retracted" or not text:
            return False
        _restatus(root(), row, row["status"], S.now_iso(), text=text)
        refresh(conn, force=True, expect={mid})
        S.log(conn, actor, C.L("改写 1 条", "Edited 1"))
        conn.commit()
    return True


def save_profile(text: str) -> None:
    """管理页保存档案：有 profile_note 就写笔记（真身，同步会带到 profile_path），否则写 profile_path。都跟着软链写到真文件。"""
    name = C.load().get("profile_note") or ""
    if name:
        atomic_write(root() / name, text)
        return
    target = S.profile_path().resolve()
    mode = target.stat().st_mode & 0o777 if target.exists() else 0o644
    atomic_write(target, text, mode)


def profile_text() -> str | None:
    name = C.load().get("profile_note") or ""
    if name:
        with contextlib.suppress(OSError):
            return (root() / name).read_text(encoding="utf8")
    return None


def issues(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT path, problem FROM issue ORDER BY path")]


# ---------- 换存储 ----------

def from_sqlite() -> tuple[int, list[str]]:
    """tree.db 的 tree 表 → 笔记（档案行不导，它们来自档案文件；已有的 id 跳过）。→ (写了几篇, 核对不一致的地方)"""
    with contextlib.closing(sqlite3.connect(f"file:{C.DB}?mode=ro", uri=True)) as old:
        old.row_factory = sqlite3.Row
        rows = [dict(r) for r in old.execute("SELECT * FROM tree ORDER BY created_at, rowid")]
        try:  # 0.3 起有枝（老库没有这张表）
            branches = [dict(r) for r in old.execute("SELECT * FROM branch ORDER BY ord, name")]
        except sqlite3.Error:
            branches = []
    base = root()
    conn = connect(sync=False)
    wrote: list[str] = []
    with locked():
        refresh(conn, force=True, actor=None)
        have = {r["id"] for r in conn.execute("SELECT id FROM tree")}
        named = {r["name"].casefold() for r in conn.execute("SELECT name FROM branch")}
        for b in branches:  # 枝 → branches/ 里一枝一篇（已经有同名的跳过）
            if b["name"].casefold() in named:
                continue
            atomic_write(base / C.BRANCHES / f"{b['name']}.md",
                         render_branch(parent=b["parent"] or S.trunk(), aliases=B.as_list(b["aliases"]), agents=B.as_list(b["agents"]),
                                       order=b["ord"] or 0, about=b["about"] or ""))
        for r in rows:
            r.setdefault("branch", None)
            if r["source"] == "profile" or r["id"] in have:
                continue
            if r["status"] == "retracted":
                r.update(text="", tags="")
                path = base / archive() / f"{r['id']}.md"
            else:
                folder = base if r["status"] in CURRENT else base / archive()
                path = _unique_path(folder, f"{_day(r['observed_at'])} {title_of(r['text'])}", r["id"])
            atomic_write(path, render_note({**r, "branch_link": f"[[{r['branch']}]]" if r["branch"] else None}))
            wrote.append(r["id"])
        refresh(conn, force=True, expect=set(wrote), actor=None)
        if wrote:
            S.log(conn, "tree", C.L(f"换成 Markdown 存储：写了 {len(wrote)} 篇笔记", f"Switched to Markdown storage: wrote {len(wrote)} notes"))
            conn.commit()
    idx = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM tree")}
    bad = []
    for r in rows:
        got = idx.get(r["id"])
        if got is None:
            bad.append(f"{r['id']}: " + C.L("索引里没有", "missing from the index"))
            continue
        fields = ("text", "tags") if r["source"] == "profile" else COLS[2:]
        bad += [f"{r['id']}: {f}" for f in fields if (got[f] or None) != (r.get(f) or None)]
    return len(wrote), bad


def to_sqlite() -> int:
    """笔记 → tree.db 的 tree 表（换回 SQLite 存储前用）。笔记里删掉的在 tree.db 里清空。→ 写了几条"""
    conn = connect()
    rows = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM tree WHERE source != 'profile'")}
    cols = COLS[2:]
    ts = S.now_iso()
    branches = [dict(r) for r in conn.execute("SELECT * FROM branch")]
    with contextlib.closing(sqlite3.connect(C.DB, timeout=10)) as c:
        c.executescript(S.SCHEMA)
        S.migrate(c)
        have = {r[0] for r in c.execute("SELECT id FROM tree WHERE source != 'profile'")}
        c.execute("DELETE FROM branch")  # 枝以笔记为准
        c.executemany("INSERT INTO branch(name, parent, about, aliases, agents, ord, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                      [(b["name"], b["parent"] or "", b["about"] or "", b["aliases"] or "[]", b["agents"] or "[]", b["ord"] or 0,
                        b["created_at"], b["updated_at"]) for b in branches])
        for mid, r in rows.items():
            vals = [r[k] for k in cols]
            if mid in have:
                c.execute(f"UPDATE tree SET {', '.join(f'{k}=?' for k in cols)} WHERE id=?", (*vals, mid))  # noqa: S608
            else:
                c.execute(f"INSERT INTO tree(id, {', '.join(cols)}) VALUES (?{', ?' * len(cols)})", (mid, *vals))  # noqa: S608
        for mid in have - rows.keys():
            c.execute("UPDATE tree SET status='retracted', text='', tags='', quote='', thread='', message='', cues='', updated_at=? WHERE id=?",
                      (ts, mid))
        c.execute("DELETE FROM meta WHERE k='profile_digest'")  # 下次读档案时重新进树
        c.commit()
    return len(rows)
