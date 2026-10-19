"""主机上的文件工具（内核 ⑫，2026-10-16）：普通 Agent 在它自己的工作区里看、找、写、改文件（主对话是主工作区，Agent 是
agent_workspaces 里它自己的那个；没有工作区的 Agent 没有这些工具）。代办这种只在沙箱里的 Agent 不给（它的手全在沙箱里）。

工具（名字和沙箱里的 sandbox_read 这些分开，也不和代办的 read / write 撞）：
  read_file     读一个文本文件，带行号（cat -n 的样子）、分段：offset 从第几行、limit 读几行（默认 400，最多 2000；一次最多 4 万字）；
                PDF / Word / PPT / Excel 抽文字；图片（PNG / JPEG / GIF / WebP，按文件头认）把图本身交给模型（⑫b，engine/images.py：
                卡大小、存进图库）；别的二进制只说是什么、多大
  list_dir      一层目录：文件夹带 /、文件带大小、符号链接标出来（不跟）
  find_files    按名字找：glob（*.md、notes/**/*.txt；不带 / 的在哪一层都算）
  grep_files    按内容找：正则（literal = 按字面），glob 限定文件，忽略大小写；出「路径:行号: 那一行」
  write_file    写一个文件（整个换掉；没有的文件夹会建）
  edit_file     局部改：edits = [{old, new}]，照 pi 的 edit：每个 old 在原文里只能出现一次（不认 replace_all），几处之间不能重叠，
                都对着原文找；一字不差找不到时按宽松的再找（行尾空白、弯引号、各种横线和空格），宽松的也要唯一；只换找到的那一段
                （pi 会把碰到的整行按宽松的样子重写，中文的弯引号会被换成直的：这里不这么做）；BOM、CRLF 照原样留着
  restore_file  换回旧版本：list 列出留着的版本；不写 version = 最近留的那一版
路径守卫（每次调用都查）：
  - 只在工作区里：相对路径从工作区起，绝对路径要在工作区里；不许 ..；路上任何一层是符号链接都不走（engine/safefs.py：一层层
    openat + O_NOFOLLOW，查完到打开之间换不掉）
  - 像密钥的不碰（读、写、搜内容都不行，列目录时标出来）：.env、secrets/、*key*、凭据……（SECRETS；server.json 的
    engine.files.secrets 可以整个换掉），还有 .git 里的 config（带令牌的远程地址、extraheader 在那；secret_parts）
  - 工作区里套着的别的 Agent 的工作区、服务器自己的东西（数据目录、server.json、密钥文件、openclaw.json）：不碰
  - .git/ 里不写；.mousse-versions/（旧版本）看不到、写不了
  - 主机上的命令（engine/host_exec.py）放行名单里会跑到的脚本（和它们所在的子文件夹）：不许改、不许新建
分类（关卡看）：read_file / list_dir / find_files / grep_files = read；write_file / edit_file / restore_file = undo（写之前留旧版本：
  engine/versions.py，撤 = restore_file）；改规矩文件（AGENTS.md、SOUL.md、IDENTITY.md、TOOLS.md、USER.md、skills/ 里的；ASK，
  engine.files.ask 可以换）= ask：每次开工都照着它们做，被网页上的字骗着改了就一直错下去（Hermes 的 protected instruction files
  也是每次都问）；同意了照样留旧版本。MEMORY.md、memory/、HEARTBEAT.md 照常写（OpenClaw 里也是它自己写）。
  engine.tools.levels 照样能改（键是工具名：{"write_file": "ask", "grep_files": "off"}）。
server.json（都可以不写）：
  "engine": {"files": {"agents": {"diet": false}, "secrets": [".env", "*key*", …], "ask": ["AGENTS.md", "skills/**", …]}}
  agents.<id> = false：这个 Agent 不给文件工具（"*" = 别的 Agent 的默认）。
参考（10/16 现拉的源码，只读）：pi 95364a5 的 packages/coding-agent/src/core/tools/edit.ts、edit-diff.ts（edits 数组、对着原文找、
唯一、不重叠、宽松匹配、BOM 和 CRLF、报错的说法）、read.ts / truncate.ts（「Use offset=… to continue」）、grep.ts / find.ts /
ls.ts（上限、输出的样子）；OpenClaw e38e3a42 的 src/agents/sandbox-paths.ts（出根就挡、.. 和链接两种都查）；Hermes f6e2723 的
agent/file_safety.py（凭据文件的名单）、tools/file_tools_write_guards.py（规矩文件每次都问）。只借设计，没拿代码。
"""
from __future__ import annotations

import asyncio
import difflib
import functools
import json
import os
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import config
from config import raw, settings
from i18n import L

from . import images, memgate, safefs, versions
from .loop import Tool, ToolCtx, ToolFailure, ToolOutput

READ, UNDO, ASK, OFF = "read", "undo", "ask", "off"
NAMES = ("read_file", "list_dir", "find_files", "grep_files", "write_file", "edit_file", "restore_file")
READ_LINES, READ_MAX_LINES, READ_CHARS, LINE_MAX = 400, 2000, 40_000, 2000
READ_BYTES = 20 * 1024 * 1024
WRITE_MAX = 2_000_000
GREP_LIMIT, GREP_MAX, GREP_LINE, GREP_FILE_MAX, GREP_SECONDS = 100, 500, 300, 2 * 1024 * 1024, 20.0
FIND_LIMIT, FIND_MAX, LS_MAX, WALK_MAX = 200, 1000, 500, 20_000
SKIP_DIRS = frozenset({".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache", ".ruff_cache", versions.DIR})
DOCS = {".pdf": "application/pdf", ".docx": "", ".pptx": "", ".xlsx": ""}
SECRETS = (".env", ".env.*", "*.env", ".envrc", "secrets", ".secrets", "*secret*", "*key*", "*credential*", "*password*", "*passwd*",
           "*token*", "*oauth*", "auth.json", "*.pem", "*.p12", "*.pfx", "*.jks", "*.keystore", "id_rsa*", "id_dsa*", "id_ecdsa*", "id_ed25519*",
           ".netrc", ".pgpass", ".npmrc", ".pypirc", ".git-credentials", ".ssh", ".gnupg", ".aws", ".kube", ".docker")
ASK_FILES = memgate.ask_files()  # 13b：档案和做法（USER.md、AGENTS.md、SOUL.md、IDENTITY.md、TOOLS.md、skills/**）改之前先问，闸在 engine/memgate.py


# —— 设置 ——————————————————————————————————————————————————————————————————

def cfg() -> dict:
    e = raw().get("engine")
    f = e.get("files") if isinstance(e, dict) else None
    return f if isinstance(f, dict) else {}


def enabled(agent: str) -> bool:
    a = cfg().get("agents")
    if not isinstance(a, dict):
        return True
    return (a[agent] if agent in a else a.get("*")) is not False


def _list(key: str, dflt: tuple[str, ...]) -> tuple[str, ...]:
    v = cfg().get(key)
    return tuple(str(x).strip() for x in v if str(x).strip()) if isinstance(v, list) else dflt


def secret_name(name: str) -> bool:
    return secret_matcher()(name)


def secret_matcher():
    """像密钥的名字的判断（读一次设置）：一次要查很多名字时（挂工作区前走一遍）先拿它。"""
    rx = _secret_rx(_list("secrets", SECRETS))
    return lambda name: rx.fullmatch(name.lower()) is not None


@functools.lru_cache(maxsize=8)
def _secret_rx(pats: tuple[str, ...]) -> re.Pattern:
    """名单合成一个正则（走工作区时每个名字都要查：别每次重编几十个）。"""
    return re.compile("|".join(f"(?:{_glob(p.lower()).pattern})" for p in pats) or "(?!)", re.S)


def secret_parts(parts: list[str]) -> str | None:
    """路径里看着像密钥的那一层 → 它（给模型看的一句里用）；没有 → None。名字像的（secret_name），再加上 .git 里的 config：
    带着令牌 clone 的远程地址、CI 写进去的 extraheader 都在那（子模块的 .git/modules/…/config 也是）。"""
    hit = next((n for n in parts if secret_name(n)), None)
    if hit is None and len(parts) >= 2 and parts[-1].lower() == "config" and ".git" in parts[:-1]:
        hit = ".git/config"
    return hit


def root_of(agent: str) -> Path | None:
    """它自己的工作区（真实路径）：主对话 = 主工作区，Agent = agent_workspaces 里它的那个；没有、不是文件夹、太宽（/、家目录）→ None。"""
    ws = settings.workspace if agent == "main" else settings.agent_workspaces.get(agent)
    if ws is None:
        return None
    try:
        p = ws.expanduser().resolve(strict=True)
    except OSError:
        return None
    if not p.is_dir() or p == Path("/") or p == Path.home().resolve():
        return None
    return p


def fenced(agent: str, root: Path) -> list[Path]:
    """工作区里不碰的（套在里面的话）：别的 Agent 的工作区、服务器自己的东西。"""
    import secretstore
    cands = [settings.data_dir, config.CONFIG_PATH, settings.env_file, settings.openclaw_json, secretstore.path()]
    if agent != "main":
        cands.append(settings.workspace)
    cands += [p for a, p in settings.agent_workspaces.items() if a != agent]
    out = []
    for c in cands:
        try:
            rp = Path(c).expanduser().resolve()
        except (OSError, RuntimeError):
            continue
        if rp != root and rp.is_relative_to(root):
            out.append(rp)
    return out


# —— glob ——————————————————————————————————————————————————————————————————

@functools.lru_cache(maxsize=512)
def _glob(pat: str, anywhere: bool = False) -> re.Pattern:
    """* 不跨 /、** 跨 /（**/ 可以是零层）、? 一个字。anywhere = 不带 / 的在哪一层都算（fd 的做法）。"""
    pat = pat.strip()
    while pat.startswith("./"):
        pat = pat[2:]
    if anywhere and "/" not in pat:
        pat = "**/" + pat
    out = []
    for tok in re.findall(r"\*\*/|/\*\*$|\*\*|[*?]|[^*?]+", pat):
        out.append({"**/": "(?:.*/)?", "/**": "(?:/.*)?", "**": ".*", "*": "[^/]*", "?": "[^/]"}.get(tok) or re.escape(tok))
    return re.compile("".join(out), re.S)


# —— 路径守卫 ——————————————————————————————————————————————————————————————————

@dataclass
class Place:
    agent: str
    root: Path
    parts: list[str]

    @property
    def rel(self) -> str:
        return "/".join(self.parts) or "."

    @property
    def fs(self) -> safefs.Root:
        return safefs.Root(self.root)

    @property
    def path(self) -> Path:
        return self.root.joinpath(*self.parts)


def no(why_zh: str, why_en: str) -> ToolFailure:
    return ToolFailure(L(why_zh, why_en))


def place(agent: str, path: str | None, *, write: bool = False, dir_ok: bool = False) -> Place:
    """模型给的路径 → 工作区里的哪一个（只做字面上的检查；读写时 safefs 再一层层不跟链接地走）。不行抛 ToolFailure。"""
    root = root_of(agent)
    if root is None:
        raise no("你没有自己的工作区，文件工具用不了", "You have no workspace of your own, so the file tools can't be used")
    p = str(path or "").strip()
    if "\x00" in p:
        raise no("路径里有非法字符", "The path has an invalid character")
    if p.startswith("~"):
        raise no(f"{p}：~ 不是你的工作区；用工作区里的路径（相对路径从工作区起）", f"{p}: ~ isn't your workspace; use a path inside it (relative paths start there)")
    if ".." in p.split("/"):
        raise no("路径里不能有 ..：只能在工作区里面", "No .. in paths: you can only work inside your workspace")
    if p.startswith("/"):
        q = os.path.normpath(p)
        bases = {str(root)}
        ws = settings.workspace if agent == "main" else settings.agent_workspaces.get(agent)
        if ws is not None:
            bases.add(os.path.normpath(str(ws.expanduser())))
        for b in bases:
            if q == b:
                p = ""
                break
            if q.startswith(b.rstrip("/") + "/"):
                p = q[len(b.rstrip("/")) + 1:]
                break
        else:
            raise no(f"{p} 不在你的工作区（{root}）里", f"{p} isn't inside your workspace ({root})")
    try:
        parts = safefs.split(p)
    except safefs.Refused as e:
        raise ToolFailure(str(e)) from None
    if not parts and not dir_ok:
        raise no("要写文件的路径", "Give a file path")
    pl = Place(agent, root, parts)
    if versions.DIR in parts:
        raise no(f"{versions.DIR} 是放旧版本的地方：用 restore_file 看、换回旧版本", f"{versions.DIR} holds old versions; use restore_file to list or bring one back")
    hit = secret_parts(parts)
    if hit is not None:
        raise no(f"{pl.rel}：{hit} 看着像密钥、凭据，不碰（读写都不行）", f"{pl.rel}: {hit} looks like a secret or credential, so it's off-limits (no reading or writing)")
    target = pl.path
    for f in fenced(agent, root):
        if target == f or target.is_relative_to(f):
            raise no(f"{pl.rel} 是别的 Agent 或者服务器自己的，不碰", f"{pl.rel} belongs to another Agent or to the server itself, so it's off-limits")
    if write and ".git" in parts:
        raise no(f"{pl.rel}：.git 里的东西不改", f"{pl.rel}: nothing inside .git is changed")
    if write:
        from . import host_exec
        if host_exec.protects(agent, target):
            raise no(f"{pl.rel} 在主机命令的放行名单里（会被当成程序跑），你改不了：要改请用户自己改",
                     f"{pl.rel} is covered by the host-command allowlist (it gets run as a program), so you can't change it; ask the user")
    return pl


def asks(agent: str, path: str | None) -> bool:
    """改的是不是规矩文件（每次开工都照着做的那些）：要用户点头。"""
    try:
        pl = place(agent, path, write=True)
    except ToolFailure:
        return False
    rel = pl.rel.lower()
    return any(_glob(p.lower()).fullmatch(rel) for p in _list("ask", ASK_FILES))


def noted(ctx: ToolCtx, path, op: str) -> None:
    """写完了：改的是记忆文件（MEMORY.md、memory/、USER.md、规矩文件……，engine/memgate.py 的 FILES）就记一笔进事件流。
    要点头的那几种走到这里时已经点过头了（关卡交卡、同意后才照原样跑）。"""
    try:
        pl = place(ctx.agent or "main", str(path or ""), write=True)
    except ToolFailure:
        return
    kind = memgate.file_kind(pl.rel)
    if kind is None:
        return
    d = memgate.decide(kind, op)
    if d.action == "propose":
        memgate.approved(kind, op, target=f"file:{pl.rel}", author=ctx.agent or "main", conversation=ctx.conversation, turn_id=ctx.turn,
                         agent=ctx.agent or "main")
    else:
        memgate.record(d, target=f"file:{pl.rel}", author=ctx.agent or "main", conversation=ctx.conversation, turn_id=ctx.turn,
                       agent=ctx.agent or "main")


def _fail(pl: Place, e: Exception) -> ToolFailure:
    if isinstance(e, safefs.Refused):
        return ToolFailure(f"{pl.rel}: {e}")
    if isinstance(e, FileNotFoundError):
        return ToolFailure(L(f"{pl.rel}：没有这个文件（list_dir、find_files 看有什么）", f"{pl.rel}: no such file (list_dir or find_files shows what's there)"))
    if isinstance(e, IsADirectoryError):
        return ToolFailure(L(f"{pl.rel} 是文件夹：用 list_dir", f"{pl.rel} is a folder; use list_dir"))
    if isinstance(e, NotADirectoryError):
        return ToolFailure(L(f"{pl.rel}：路上有一层不是文件夹", f"{pl.rel}: part of the path isn't a folder"))
    if isinstance(e, PermissionError):
        return ToolFailure(L(f"{pl.rel}：没有权限", f"{pl.rel}: permission denied"))
    return ToolFailure(f"{pl.rel}: {getattr(e, 'strerror', None) or e}")


def size_text(n: int) -> str:
    return f"{n} B" if n < 1024 else f"{n / 1024:.1f} KB" if n < 1024 * 1024 else f"{n / 1024 / 1024:.1f} MB"


# —— 读 ——————————————————————————————————————————————————————————————————————

MAGIC = ((b"\x89PNG", "PNG image"), (b"\xff\xd8\xff", "JPEG image"), (b"GIF8", "GIF image"), (b"RIFF", "RIFF (WebP / WAV / AVI)"),
         (b"%PDF", "PDF"), (b"PK\x03\x04", "zip archive (or an Office file)"), (b"\x7fELF", "ELF program"), (b"SQLite format 3", "SQLite database"))


def _doc_text(data: bytes, ext: str) -> str:
    import files as files_mod  # 用到时才导入（服务器的附件模块）
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / f"doc{ext}"
        p.write_bytes(data)
        text, _ = files_mod.extract_text(p, "doc", DOCS.get(ext, ""))
    return text


def read(agent: str, path: str, offset: int = 1, limit: int = READ_LINES) -> str | ToolOutput:
    pl = place(agent, path)
    try:
        data, size = pl.fs.read(pl.parts, READ_BYTES)
    except (OSError, safefs.Refused) as e:
        raise _fail(pl, e) from None
    ext = Path(pl.parts[-1]).suffix.lower()
    note = ""
    if (kind := images.sniff(data)) is not None:  # ⑫b：图片交给模型看（pi 的 read：「Read image file [image/png]」）
        head = f"{pl.rel}: image file [{kind}], {size_text(size)}"
        if size > len(data):
            return head + L("，太大了，只读了前面一截，不能当图看。", " — too large: only the first part was read, so it can't be shown.")
        block, said = images.make(data, label=pl.rel, mime=kind)
        if block is None:
            return f"{head}\n{said}"
        return ToolOutput(f"{head} · {block['w']}x{block['h']}" + (f"\n{said}" if said else ""), [block])
    if ext in DOCS and data:
        text = _doc_text(data, ext)
        if not text.strip():
            return L(f"{pl.rel}（{size_text(size)}）里抽不出文字（可能是扫描件）", f"No text could be extracted from {pl.rel} ({size_text(size)}; maybe a scan)")
        note = L("（抽出来的文字）", " (extracted text)")
    elif b"\x00" in data[:8192]:
        kind = next((k for m, k in MAGIC if data.startswith(m)), "binary file")
        return L(f"{pl.rel} 是二进制文件（{kind}，{size_text(size)}），不能当文字显示。", f"{pl.rel} is a binary file ({kind}, {size_text(size)}); it can't be shown as text.")
    else:
        text = data.decode("utf-8", "replace").removeprefix("﻿")
    lines = text.splitlines()
    if not lines:
        return L(f"{pl.rel} 是空的。", f"{pl.rel} is empty.")
    total = len(lines)
    start = max(1, int(offset or 1))
    if start > total:
        raise ToolFailure(L(f"offset {start} 超过了文件的行数（一共 {total} 行）", f"Offset {start} is beyond the end of the file ({total} lines in all)"))
    limit = max(1, min(int(limit or READ_LINES), READ_MAX_LINES))
    rows: list[str] = []
    used = 0
    for i in range(start - 1, min(total, start - 1 + limit)):
        line = lines[i] if len(lines[i]) <= LINE_MAX else lines[i][:LINE_MAX] + " … [line cut]"
        row = f"{i + 1:>6}\t{line}"
        if rows and used + len(row) + 1 > READ_CHARS:
            break
        rows.append(row)
        used += len(row) + 1
    end = start + len(rows) - 1
    head = f"{pl.rel}{note} · lines {start}–{end} of {total}" + (" (only the first 20 MB were read)" if size > len(data) and not note else "")
    tail = f"\n[Showing lines {start}–{end} of {total}. Use offset={end + 1} to continue.]" if end < total else ""
    return head + "\n" + "\n".join(rows) + tail


def ls(agent: str, path: str | None) -> str:
    pl = place(agent, path, dir_ok=True)
    try:
        ents = pl.fs.entries(pl.parts)
    except (OSError, safefs.Refused) as e:
        raise _fail(pl, e) from None
    ents = sorted((e for e in ents if not (not pl.parts and e.name == versions.DIR)), key=lambda e: e.name.lower())
    rows = []
    for e in ents[:LS_MAX]:
        mark = L("  [像密钥：不碰]", "  [looks like a secret: off-limits]") if secret_parts([*pl.parts, e.name]) else ""
        rows.append(f"{e.name}/{mark}" if e.kind == "d" else f"{e.name} -> (symbolic link, not followed){mark}" if e.kind == "l"
                    else f"{e.name}  (special file){mark}" if e.kind == "o" else f"{e.name}  ({size_text(e.size)}){mark}")
    more = f"\n(… {len(ents) - LS_MAX} more)" if len(ents) > LS_MAX else ""
    return f"{pl.rel}:\n" + ("\n".join(rows) or L("（空的）", "(empty)")) + more


def find(agent: str, pattern: str, path: str | None = None, limit: int = FIND_LIMIT) -> str:
    pat = str(pattern or "").strip()
    if not pat:
        raise no("要写 pattern（比如 *.md、notes/**/*.txt）", "Give a pattern (like *.md or notes/**/*.txt)")
    pl = place(agent, path, dir_ok=True)
    rx = _glob(pat, anywhere=True)
    limit = max(1, min(int(limit or FIND_LIMIT), FIND_MAX))
    hits, seen, capped = [], 0, False
    try:
        for parts, e in pl.fs.walk(pl.parts, SKIP_DIRS):
            seen += 1
            if seen > WALK_MAX:
                capped = True
                break
            rel = "/".join(parts[len(pl.parts):])
            if secret_parts(parts) or not rx.fullmatch(rel):
                continue
            hits.append(rel + ("/" if e.kind == "d" else ""))
            if len(hits) >= limit:
                break
    except (OSError, safefs.Refused) as e:
        raise _fail(pl, e) from None
    if not hits:
        return L(f"{pl.rel} 里没有对得上 {pat} 的", f"Nothing in {pl.rel} matches {pat}") + (L("（只看了前 2 万个）", " (looked at the first 20,000 entries)") if capped else "")
    notes = []
    if len(hits) >= limit:
        notes.append(f"{limit} results limit reached. Use limit={min(limit * 2, FIND_MAX)} for more, or refine the pattern")
    if capped:
        notes.append(f"stopped after looking at {WALK_MAX} entries")
    where = "" if not pl.parts else f"(in {pl.rel})\n"
    return where + "\n".join(sorted(hits)) + (f"\n[{'; '.join(notes)}]" if notes else "")


def grep(agent: str, pattern: str, path: str | None = None, glob: str | None = None, ignore_case: bool = False, literal: bool = False,
         limit: int = GREP_LIMIT) -> str:
    if not str(pattern or ""):
        raise no("要写 pattern", "Give a pattern")
    try:
        rx = re.compile(re.escape(pattern) if literal else pattern, re.I if ignore_case else 0)
    except re.error as e:
        raise no(f"正则写错了：{e}（按字面找就写 literal: true）", f"Bad regular expression: {e} (set literal: true to search for the text as is)") from None
    pl = place(agent, path, dir_ok=True)
    grx = _glob(glob, anywhere=True) if str(glob or "").strip() else None
    limit = max(1, min(int(limit or GREP_LIMIT), GREP_MAX))
    hits: list[str] = []
    notes: list[str] = []
    t0, seen, cut_lines = time.monotonic(), 0, False
    st = pl.fs.lstat(pl.parts)
    if st is None:
        raise _fail(pl, FileNotFoundError())
    if not os.path.isdir(pl.path) or os.path.islink(pl.path):
        targets = [(pl.parts, safefs.Entry(pl.parts[-1], "f", st.st_size, st.st_mtime))]
        base = pl.parts[:-1]
    else:
        targets, base = pl.fs.walk(pl.parts, SKIP_DIRS), pl.parts
    for parts, e in targets:
        seen += 1
        if seen > WALK_MAX:
            notes.append(f"stopped after looking at {WALK_MAX} entries")
            break
        if time.monotonic() - t0 > GREP_SECONDS:
            notes.append(f"stopped after {GREP_SECONDS:.0f}s")
            break
        if e.kind != "f" or e.size > GREP_FILE_MAX or secret_parts(parts):
            continue
        rel = "/".join(parts[len(base):])
        if grx is not None and not grx.fullmatch(rel):
            continue
        try:
            data = pl.fs.read(parts, GREP_FILE_MAX)[0]
        except (OSError, safefs.Refused):
            continue
        if b"\x00" in data[:8192]:
            continue
        shown = "/".join(parts)
        for n, line in enumerate(data.decode("utf-8", "replace").splitlines(), 1):
            if rx.search(line[:4000]):
                if len(line) > GREP_LINE:
                    line, cut_lines = line[:GREP_LINE] + "…", True
                hits.append(f"{shown}:{n}: {line}")
                if len(hits) >= limit:
                    break
        if len(hits) >= limit:
            notes.insert(0, f"{limit} matches limit reached. Use limit={min(limit * 2, GREP_MAX)} for more, or refine the pattern")
            break
    if cut_lines:
        notes.append(f"some lines cut to {GREP_LINE} characters; use read_file to see them whole")
    if not hits:
        return L("没有找到。", "No matches found.") + (f" [{'; '.join(notes)}]" if notes else "")
    return "\n".join(hits) + (f"\n[{'; '.join(notes)}]" if notes else "")


# —— 写 ——————————————————————————————————————————————————————————————————————

def _keep(pl: Place, note: str) -> dict:
    try:
        return versions.keep(pl.root, pl.rel, by=pl.agent, note=note)
    except versions.TooBig as e:
        raise ToolFailure(L(f"{e}：没有改", f"{e}; nothing was changed")) from None
    except (OSError, safefs.Refused) as e:
        raise _fail(pl, e) from None


def undo_note(pl: Place, v: dict) -> str:
    if v.get("absent"):
        return L(f"撤：restore_file（path {pl.rel}，version {v['id']}）= 删掉它", f"Undo: restore_file with path {pl.rel}, version {v['id']} (removes it)")
    return L(f"撤：restore_file（path {pl.rel}，version {v['id']}）", f"Undo: restore_file with path {pl.rel}, version {v['id']}")


def write(agent: str, path: str, content: str) -> str:
    if len(content) > WRITE_MAX:
        raise no(f"一次最多写 {WRITE_MAX} 字", f"At most {WRITE_MAX} characters per write")
    pl = place(agent, path, write=True)
    v = _keep(pl, "before write_file")
    data = content.encode("utf8")
    try:
        new = pl.fs.write(pl.parts, data)
    except (OSError, safefs.Refused) as e:
        raise _fail(pl, e) from None
    what = L(f"新建了 {pl.rel}", f"Created {pl.rel}") if new else L(f"写好了 {pl.rel}", f"Wrote {pl.rel}")
    return f"{what} ({size_text(len(data))}). {undo_note(pl, v)}."


FOLD = {**dict.fromkeys("‘’‚‛", "'"), **dict.fromkeys("“”„‟", '"'),
        **dict.fromkeys("‐‑‒–—―−", "-"),
        **dict.fromkeys("            　", " ")}


def fold(text: str) -> tuple[str, list[int]]:
    """宽松比较用的样子，和每个字在原文里的位置：行尾空白去掉；弯引号、各种横线、各种空格换成普通的（一个字换一个字）。"""
    out: list[str] = []
    idx: list[int] = []
    pos = 0
    lines = text.split("\n")
    for n, line in enumerate(lines):
        for j in range(len(line.rstrip())):
            out.append(FOLD.get(line[j], line[j]))
            idx.append(pos + j)
        if n < len(lines) - 1:
            out.append("\n")
            idx.append(pos + len(line))
        pos += len(line) + 1
    return "".join(out), idx


NUMBERED = re.compile(r"^\s*\d+\t")


def apply_edits(text: str, edits: list[tuple[str, str]], rel: str) -> tuple[str, int]:
    """pi 的 edit：每个 old 对着原文找、只能出现一次（宽松的样子里也只能一次）、几处不重叠；只换找到的那一段。→ (改完的, 几处)。"""
    many = len(edits) > 1
    folded, idx = fold(text)
    spans = []
    for i, (old, new) in enumerate(edits):
        which = f"edits[{i}]" if many else "the text"
        if not old:
            raise ToolFailure(f"{f'edits[{i}].' if many else ''}old must not be empty in {rel}.")
        f_old = fold(old)[0]
        count = folded.count(f_old) if f_old.strip() else text.count(old)
        if count == 0:
            lines = [x for x in old.split("\n") if x.strip()]
            hint = (" It looks like you copied the line-number prefixes from read_file; leave them out." if lines and all(NUMBERED.match(x) for x in lines)
                    else " Read the file again if it may have changed.")
            raise ToolFailure(f"Could not find {which} in {rel}. The old text must match exactly including all whitespace and newlines.{hint}")
        if count > 1:
            raise ToolFailure(f"Found {count} occurrences of {which} in {rel}. {'Each old text' if many else 'The text'} must be unique. "
                              "Please provide more context to make it unique.")
        at = text.find(old)
        if at >= 0 and text.count(old) == 1:
            s, e = at, at + len(old)
        else:
            fs = folded.find(f_old)
            s, e = idx[fs], idx[fs + len(f_old) - 1] + 1
        spans.append((s, e, new, i))
    spans.sort()
    for a, b in zip(spans, spans[1:]):
        if a[1] > b[0]:
            raise ToolFailure(f"edits[{a[3]}] and edits[{b[3]}] overlap in {rel}. Merge them into one edit or target disjoint regions.")
    out = text
    for s, e, new, _ in reversed(spans):
        out = out[:s] + new + out[e:]
    return out, len(spans)


def _diff(before: str, after: str) -> str:
    lines = [x for x in difflib.unified_diff(before.split("\n"), after.split("\n"), lineterm="", n=2) if not x.startswith(("---", "+++"))]
    text = "\n".join(lines)
    return text if len(text) <= 4000 else text[:4000] + "\n…"


def edit(agent: str, path: str, edits: list[tuple[str, str]]) -> str:
    if not edits:
        raise ToolFailure("Edit tool input is invalid. edits must contain at least one replacement.")
    pl = place(agent, path, write=True)
    try:
        data = pl.fs.read(pl.parts, versions.MAX_BYTES + 1)[0]
    except (OSError, safefs.Refused) as e:
        raise _fail(pl, e) from None
    try:
        whole = data.decode("utf-8")
    except UnicodeDecodeError:
        raise no(f"{pl.rel} 不是合法的 UTF-8，不能安全地改", f"{pl.rel} isn't valid UTF-8 and can't be edited safely") from None
    bom = "﻿" if whole.startswith("﻿") else ""
    text = whole[len(bom):]
    nl = text.find("\n")
    crlf = nl > 0 and text[nl - 1] == "\r"
    norm = text.replace("\r\n", "\n").replace("\r", "\n")
    pairs = [(o.replace("\r\n", "\n").replace("\r", "\n"), n.replace("\r\n", "\n").replace("\r", "\n")) for o, n in edits]
    after, count = apply_edits(norm, pairs, pl.rel)
    if after == norm:
        raise ToolFailure(f"No changes made to {pl.rel}. The replacement produced identical content.")
    v = _keep(pl, "before edit_file")
    try:
        pl.fs.write(pl.parts, (bom + (after.replace("\n", "\r\n") if crlf else after)).encode("utf8"))
    except (OSError, safefs.Refused) as e:
        raise _fail(pl, e) from None
    return f"Replaced {count} block(s) in {pl.rel}. {undo_note(pl, v)}.\n{_diff(norm, after)}"


def restore(agent: str, path: str, version: str | None = None, list_only: bool = False) -> str:
    pl = place(agent, path, write=not list_only)
    try:
        if list_only:
            hist = versions.history(pl.root, pl.rel)
            if not hist:
                return L(f"{pl.rel} 没有留着的旧版本", f"No old versions of {pl.rel} are kept")
            rows = [f"{v['id']}  {str(v.get('at') or '')[:16].replace('T', ' ')}  " + (L("（那时还没有这个文件）", "(didn't exist yet)") if v.get("absent")
                    else size_text(int(v.get("size") or 0))) + (f"  {v['note']}" if v.get("note") else "") for v in hist]
            return L(f"{pl.rel} 留着的旧版本（新的在前）：\n", f"Old versions of {pl.rel} (newest first):\n") + "\n".join(rows)
        res = versions.restore(pl.root, pl.rel, version, by=agent)
    except KeyError as e:
        raise ToolFailure(str(e.args[0]) if e.args else str(e)) from None
    except versions.TooBig as e:
        raise ToolFailure(str(e)) from None
    except (OSError, safefs.Refused) as e:
        raise _fail(pl, e) from None
    t, k = res["restored"], res["kept"]
    what = (L(f"{pl.rel} 换回了 {t['id']}（那时还没有这个文件：删掉了）", f"Restored {pl.rel} to {t['id']} (it didn't exist then, so it was removed)") if t.get("absent")
            else L(f"{pl.rel} 换回了 {t['id']}（{str(t.get('at') or '')[:16].replace('T', ' ')} 留的）", f"Restored {pl.rel} to {t['id']} (kept {str(t.get('at') or '')[:16].replace('T', ' ')})"))
    return f"{what}. {undo_note(pl, k)}."


# —— 工具 ——————————————————————————————————————————————————————————————————

def _edits(args: dict) -> list[tuple[str, str]]:
    out = []
    for e in args.get("edits") or []:
        if isinstance(e, dict):
            out.append((str(e.get("old", e.get("oldText", "")) or ""), str(e.get("new", e.get("newText", "")) or "")))
    return out


def prepare_edit(a):
    """edits 写成了 JSON 字符串、写成了一个对象、写在外面（old / new、oldText / newText）的都认（pi 的 prepareEditArguments）。"""
    if not isinstance(a, dict):
        return a
    a = dict(a)
    e = a.get("edits")
    if isinstance(e, str):
        try:
            e = json.loads(e)
        except ValueError:
            e = None
    if isinstance(e, dict):
        e = [e]
    e = list(e) if isinstance(e, list) else []
    old = a.pop("old", None) if "old" in a else a.pop("oldText", None)
    new = a.pop("new", None) if "new" in a else a.pop("newText", None)
    if old is not None:
        e.append({"old": old, "new": new or ""})
    a["edits"] = e
    return a


def tools(agent: str) -> list[Tool]:
    if not enabled(agent) or root_of(agent) is None:
        return []
    from .handkit import levelled

    def why_read() -> str:
        return L("只看自己工作区里的文件", "Only reads files in your own workspace")

    def write_level(name: str, path) -> tuple[str, str]:
        if asks(agent, path):
            return levelled(name, (ASK, L("改的是档案或规矩文件（做法：每次开工都照着它做），先提议、用户点了头才改", "It changes the profile or a rules "
                                          "file (how things are done, followed every session), so it's proposed and changes only once the user approves")))
        return levelled(name, (UNDO, L("写之前留了旧版本（工作区的 .mousse-versions）；撤：restore_file", "The old version is kept first (.mousse-versions in the workspace); undo: restore_file")))

    def t(name: str, run, desc: tuple[str, str], schema: dict, level, title, prepare=None) -> Tool:
        return Tool(name=name, kinds=("owner",), run=run, timeout=90, max_output=45_000, description=L(*desc), schema=schema, classify=level,
                    title=title, prepare=prepare)

    async def r_read(args: dict, ctx: ToolCtx) -> str | ToolOutput:
        return await asyncio.to_thread(read, ctx.agent or "main", str(args.get("path") or ""), int(args.get("offset") or 1), int(args.get("limit") or READ_LINES))

    async def r_ls(args: dict, ctx: ToolCtx) -> str:
        return await asyncio.to_thread(ls, ctx.agent or "main", args.get("path"))

    async def r_find(args: dict, ctx: ToolCtx) -> str:
        return await asyncio.to_thread(find, ctx.agent or "main", str(args.get("pattern") or ""), args.get("path"), int(args.get("limit") or FIND_LIMIT))

    async def r_grep(args: dict, ctx: ToolCtx) -> str:
        return await asyncio.to_thread(grep, ctx.agent or "main", str(args.get("pattern") or ""), args.get("path"), args.get("glob"),
                                       bool(args.get("ignore_case")), bool(args.get("literal")), int(args.get("limit") or GREP_LIMIT))

    async def r_write(args: dict, ctx: ToolCtx) -> str:
        out = await asyncio.to_thread(write, ctx.agent or "main", str(args.get("path") or ""), str(args.get("content") or ""))
        noted(ctx, args.get("path"), "edit")
        return out

    async def r_edit(args: dict, ctx: ToolCtx) -> str:
        out = await asyncio.to_thread(edit, ctx.agent or "main", str(args.get("path") or ""), _edits(args))
        noted(ctx, args.get("path"), "edit")
        return out

    async def r_restore(args: dict, ctx: ToolCtx) -> str:
        out = await asyncio.to_thread(restore, ctx.agent or "main", str(args.get("path") or ""), args.get("version"), bool(args.get("list")))
        if not args.get("list"):
            noted(ctx, args.get("path"), "edit")
        return out

    path_desc = L("工作区里的路径（相对路径从工作区起）", "Path in your workspace (relative paths start there)")
    p = {"type": "string", "description": path_desc}
    return [
        t("read_file", r_read, (
            "读你工作区里的一个文本文件，每行带行号（edit_file 的 old 里别带行号）。默认从第 1 行读 400 行；长文件用 offset / limit 接着读。PDF / Word / PPT / Excel 给抽出来的文字；"
            "图片（PNG、JPEG、GIF、WebP）直接给你看（太大的会缩小）。",
            "Read a text file in your workspace; each line is numbered (leave the numbers out of edit_file's old text). 400 lines from "
            "line 1 by default; use offset / limit to read on. PDF / Word / PowerPoint / Excel files give their extracted text; images "
            "(PNG, JPEG, GIF, WebP) come back as the picture itself (large ones are scaled down)."),
          {"type": "object", "required": ["path"], "properties": {"path": p,
                                                                 "offset": {"type": "integer", "description": L("从第几行读（从 1 数）", "Line to start from (1-based)")},
                                                                 "limit": {"type": "integer", "description": L("读多少行（最多 2000）", "How many lines (max 2000)")}}},
          lambda a: levelled("read_file", (READ, why_read())), lambda a: str(a.get("path") or "")),
        t("list_dir", r_ls, ("列出你工作区里一个文件夹（默认工作区本身）。", "List a folder in your workspace (the workspace itself by default)."),
          {"type": "object", "required": [], "properties": {"path": p}},
          lambda a: levelled("list_dir", (READ, why_read())), lambda a: str(a.get("path") or ".")),
        t("find_files", r_find, (
            "按名字在你的工作区里找文件：glob（*.md、notes/**/*.txt；不带 / 的在哪一层都算）。",
            "Find files in your workspace by name: a glob (*.md, notes/**/*.txt; one without / matches at any depth)."),
          {"type": "object", "required": ["pattern"], "properties": {
              "pattern": {"type": "string"}, "path": {"type": "string", "description": L("在哪个文件夹里找（默认整个工作区）", "Folder to search in (default: the whole workspace)")},
              "limit": {"type": "integer", "description": L("最多几个（默认 200）", "At most this many (default 200)")}}},
          lambda a: levelled("find_files", (READ, why_read())), lambda a: str(a.get("pattern") or "")),
        t("grep_files", r_grep, (
            "按内容在你的工作区里找：正则（literal = 按字面），glob 只看对得上的文件；回「路径:行号: 那一行」。",
            "Search file contents in your workspace: a regular expression (literal = plain text), glob to limit which files; returns "
            "path:line: text."),
          {"type": "object", "required": ["pattern"], "properties": {
              "pattern": {"type": "string"}, "path": {"type": "string", "description": L("在哪个文件夹或文件里找（默认整个工作区）", "Folder or file to search (default: the whole workspace)")},
              "glob": {"type": "string", "description": L("只看名字对得上的文件，比如 *.md", "Only files whose names match, like *.md")},
              "ignore_case": {"type": "boolean"}, "literal": {"type": "boolean"},
              "limit": {"type": "integer", "description": L("最多几行（默认 100）", "At most this many lines (default 100)")}}},
          lambda a: levelled("grep_files", (READ, why_read())), lambda a: str(a.get("pattern") or "")[:60]),
        t("write_file", r_write, (
            "在你的工作区里写一个文件（整个换掉；没有的文件夹会建）。写之前留旧版本，restore_file 能换回去。",
            "Write a file in your workspace (replaces it whole; creates missing folders). The old version is kept first; restore_file "
            "brings it back."),
          {"type": "object", "required": ["path", "content"], "properties": {"path": p, "content": {"type": "string"}}},
          lambda a: write_level("write_file", a.get("path")), lambda a: str(a.get("path") or "")),
        t("edit_file", r_edit, (
            "改你工作区里的一个文件：edits 里每一项把 old 换成 new。每个 old 要和文件里一字不差（含空格、换行），在原文里只出现一次，几项之间不能重叠；"
            "都对着改之前的原文找。写之前留旧版本，restore_file 能换回去。",
            "Edit a file in your workspace: each item in edits replaces old with new. Each old must match the file exactly (spaces and "
            "newlines included), appear only once, and not overlap another; all are matched against the original. The old version is "
            "kept first; restore_file brings it back."),
          {"type": "object", "required": ["path", "edits"], "properties": {"path": p, "edits": {"type": "array", "items": {
              "type": "object", "required": ["old", "new"], "properties": {"old": {"type": "string"}, "new": {"type": "string"}}}}}},
          lambda a: write_level("edit_file", a.get("path")), lambda a: str(a.get("path") or ""), prepare_edit),
        t("restore_file", r_restore, (
            "把工作区里的一个文件换回旧版本（write_file、edit_file 每次改之前都留了一份）：list 看留着哪些；不写 version = 最近留的那一版。换之前也会留一份现在的。",
            "Bring back an old version of a file in your workspace (write_file and edit_file keep one before every change): list shows "
            "what's kept; no version = the most recent one. The current one is kept first, too."),
          {"type": "object", "required": ["path"], "properties": {"path": p, "version": {"type": "string", "description": L("哪一版（v3 这种）", "Which version (like v3)")},
                                                                 "list": {"type": "boolean", "description": L("只列出留着的版本", "Only list the kept versions")}}},
          lambda a: levelled("restore_file", (READ, why_read())) if a.get("list") else write_level("restore_file", a.get("path")),
          lambda a: str(a.get("path") or "")),
    ]
