"""多文件补丁（第 3 批第 1 项，2026-10-19）：apply_patch 工具，照 OpenClaw 的 *** Begin Patch 格式，一次增、改、改名、删几个文件。
普通 Agent 自己的工作区里（和 engine/host_files.py 的文件工具同一个工作区、同一道路径守卫）；只在沙箱里的 Agent（代办）不给。

格式（OpenClaw docs/tools/apply-patch.md、src/agents/apply-patch-parse.ts）：
  *** Begin Patch
  *** Add File: notes/new.md            新建：后面每行以 + 开头（目标不能已经有：要换掉先在同一个补丁里删）
  +第一行
  *** Update File: src/app.py           改：一段段 @@（可以带一行定位用的上下文：@@ def main():），段里 空格 = 不变的上下文、- = 删、+ = 加
  *** Move to: src/main.py               （可选，紧跟在 Update 下面）改名：改完写到新路径、删掉原来的（新路径不能已经有）
  @@ def main():
  -    old()
  +    new()
  *** End of File                        （可选）这一段贴着文件末尾
  *** Delete File: obsolete.txt          删
  *** End Patch
  也认：外面套一层 <<EOF … EOF（模型照 shell 的写法抄过来的）、Hermes 的 *** Move File: a -> b（只改名）、@@ 上下文 @@ 这种两头都有 @@ 的。
找那几行（照 OpenClaw 的 apply-patch-update.ts 的 seekSequence）：从上一段找到的地方往下找，按越来越宽松的几档比：一字不差 → 去掉行尾空白 →
  去掉两头空白 → 再把弯引号、各种横线和空格、省略号换成普通的、连着的空格和 Tab 并成一个（这一档借 Hermes tools/fuzzy_match.py 的
  UNICODE_MAP 和 _strategy_whitespace_normalized，NousResearch/hermes-agent commit f4ac0be，MIT，Copyright (c) 2025 Nous Research；许可全文见
  仓库根目录的 THIRD_PARTY_NOTICES）。哪一档先找到就按哪一档：只找到一处才算，找到好几处报「不唯一」、让它多带几行上下文；
  有 @@ 定位的那一段例外：定位那一行之后的第一处就是它（Codex 原来的 seek_sequence 就是这样，@@ 本来就是用来指明在哪的）。
  Hermes 后面那两档按相似度找的（block_anchor、context_aware）不要：它自己也说那两档可能偏一行，补丁错一行比报错更糟。
  @@ 那一行定位的上下文多一档：整行对不上时，只要那一行里含着它、而且只有一行含着，也认（模型常只写半行：@@ def apply_edits）。
  宽松地找到的上下文行照原文留着（OpenClaw 的 contextOldIndexes：中文的弯引号、行尾空格不被改成补丁里的样子），只换真加、真删的那几行；
  BOM、CRLF / LF、文件末尾有没有换行照原样留着。
全有或全无：先在内存里把整个补丁走一遍（后面的一段看得到前面改过的样子，同 Hermes 的 _validate_operations）：哪一个文件对不上、路径不许、
  目标已经有了，整个补丁一个字都不落；都对得上了先给每个要动的文件留旧版本（engine/versions.py），再一个个写；写到一半出错，已经写了的
  照内存里的原样换回去，再报错。
路径守卫：每个路径都过 host_files.place(write=True)：只在自己的工作区里、不许 ..、不跟符号链接（读写都经 engine/safefs.py）、像密钥的不碰、
  别的 Agent 和服务器自己的不碰、.git 里不写、.mousse-versions 写不了、主机命令放行名单里会跑到的脚本不许改也不许新建。
分类（关卡看）：
  undo  默认。改、增、改名、删之前都留了旧版本（增 = 留一版「那时还没有」，删 = 留整份），restore_file 能一个个换回去。
  ask   动到规矩文件（AGENTS.md、SOUL.md、IDENTITY.md、TOOLS.md、USER.md、skills/…，同 write_file 的 host_files.asks）；
        删、改名的那个文件大过 versions.MAX_BYTES（10 MB）留不了旧版本：删了就回不来，交卡（handkit 的通用卡，卡上是整个补丁），
        用户同意了照卡上的补丁跑一次（这一次才允许不留版本地删那个大文件）。要改的文件太大的不交卡，直接不改（同 edit_file）。
  engine.tools.levels 的 apply_patch 照样能改（off / ask / auto）。
开关：server.json 的 engine.files.patch = true 才有这个工具（不写 = 没有：老实例合进来一个字不变）；engine.files.agents.<id> = false 的 Agent
  连同别的文件工具一起没有。
沙箱版：不做。沙箱是能整个扔掉的草稿区，里面有 bash、git、python3（git apply、patch 都能用）；access rw 的工作区就是主机上这同一个工作区，
  用这里的 apply_patch 改还能留版本；要在容器里再跑一份就得把这套补丁的逻辑另写一份塞进容器，两份要一起维护，不值。
"""
from __future__ import annotations

import asyncio
import json
import re
import threading
from dataclasses import dataclass, field

from i18n import L

from . import host_files, safefs, versions
from .loop import Tool, ToolCtx, ToolFailure

NAME = "apply_patch"
NAMES = (NAME,)
READ, UNDO, ASK, OFF = "read", "undo", "ask", "off"
BEGIN, END = "*** Begin Patch", "*** End Patch"
ADD, DELETE, UPDATE, MOVE_TO, EOF = "*** Add File:", "*** Delete File:", "*** Update File:", "*** Move to:", "*** End of File"
MOVE_FILE = re.compile(r"^\*\*\*\s*Move\s+File:\s*(.+?)\s*->\s*(.+?)\s*$")
RANGE = re.compile(r"-\d+(?:,\d+)? \+\d+(?:,\d+)?(?: @@.*)?")
INPUT_MAX = 400_000      # 一个补丁最多多少字
FILES_MAX = 100          # 一个补丁最多动几个文件
DIFF_MAX = 6000          # 结果里的 diff 最多多少字
_locks: dict[str, threading.Lock] = {}
_guard = threading.Lock()


def enabled() -> bool:
    """engine.files.patch = true 才有（不写 = 没有）。"""
    return host_files.cfg().get("patch") is True


# —— 解析 ————————————————————————————————————————————————————————————————————

@dataclass
class Chunk:
    context: str | None
    ops: list[tuple[str, str]]          # (" " | "-" | "+", 那一行的字)
    eof: bool = False

    @property
    def old(self) -> list[str]:
        return [t for m, t in self.ops if m != "+"]


@dataclass
class Hunk:
    kind: str                           # add / delete / update
    path: str
    move: str | None = None
    chunks: list[Chunk] = field(default_factory=list)
    contents: str = ""
    line: int = 0                       # 补丁里第几行（报错用）


def bad(zh: str, en: str) -> ToolFailure:
    return ToolFailure(L(zh, en))


def _strip_wrapper(lines: list[str]) -> list[str]:
    """外面套了一层 <<EOF … EOF 的去掉（照 OpenClaw 的 checkPatchBoundaries）。"""
    if len(lines) >= 4 and lines[0].strip() in ("<<EOF", "<<'EOF'", '<<"EOF"') and lines[-1].strip().endswith("EOF"):
        return lines[1:-1]
    return lines


def parse(text: str) -> list[Hunk]:
    """补丁 → 一个个文件的操作。写法不对抛 ToolFailure（说是第几行、哪里不对）。"""
    if not str(text or "").strip():
        raise bad("补丁是空的：要从 *** Begin Patch 写到 *** End Patch", "The patch is empty: write it from *** Begin Patch to *** End Patch")
    if len(text) > INPUT_MAX:
        raise bad(f"补丁太长（{len(text)} 字，最多 {INPUT_MAX}）：拆成几个", f"The patch is too long ({len(text)} characters, at most {INPUT_MAX}); split it up")
    lines = _strip_wrapper(text.strip().replace("\r\n", "\n").replace("\r", "\n").split("\n"))
    if lines[0].strip() != BEGIN:
        raise bad(f"补丁的第一行要是 {BEGIN}", f"The first line of the patch must be '{BEGIN}'")
    if lines[-1].strip() != END:
        raise bad(f"补丁的最后一行要是 {END}", f"The last line of the patch must be '{END}'")
    body = lines[1:-1]
    out: list[Hunk] = []
    i = 0
    while i < len(body):
        line = body[i]
        head = line.strip()
        n = i + 2  # 补丁里的行号（从 1 数，第 1 行是 Begin）
        if not head:
            i += 1
            continue
        if head.startswith(ADD):
            h = Hunk("add", head[len(ADD):].strip(), line=n)
            i += 1
            got: list[str] = []
            while i < len(body):
                cur = body[i]
                if cur.startswith("+"):
                    got.append(cur[1:])
                    i += 1
                elif cur == "" and any(x.startswith("+") for x in _until_header(body, i)):  # 空行忘了写 +：后面还有 + 行就当空行
                    got.append("")
                    i += 1
                else:
                    break
            h.contents = "".join(x + "\n" for x in got)
            out.append(h)
            continue
        if head.startswith(DELETE):
            out.append(Hunk("delete", head[len(DELETE):].strip(), line=n))
            i += 1
            continue
        if m := MOVE_FILE.match(head):  # Hermes 的写法：只改名
            out.append(Hunk("update", m.group(1).strip(), move=m.group(2).strip(), line=n))
            i += 1
            continue
        if head.startswith(UPDATE):
            h = Hunk("update", head[len(UPDATE):].strip(), line=n)
            i += 1
            if i < len(body) and body[i].strip().startswith(MOVE_TO):
                h.move = body[i].strip()[len(MOVE_TO):].strip()
                i += 1
            while i < len(body):
                cur = body[i]
                if cur.strip() == "":
                    i += 1
                    continue
                if cur.startswith("***") and cur.strip() != EOF:
                    break
                chunk, used = _chunk(body, i, first=not h.chunks)
                h.chunks.append(chunk)
                i += used
            if not h.chunks and not h.move:
                raise bad(f"第 {n} 行：Update File {h.path} 下面一段都没有", f"Line {n}: the Update File hunk for {h.path} is empty")
            out.append(h)
            continue
        raise bad(f"第 {n} 行「{line[:80]}」不是文件的开头：要是 *** Add File: / *** Update File: / *** Delete File: 路径",
                  f"Line {n} ('{line[:80]}') isn't a file header: use '*** Add File: path', '*** Update File: path' or '*** Delete File: path'")
    for h in out:
        for p in (h.path, h.move):
            if p is not None and not p:
                raise bad(f"第 {h.line} 行：没写路径", f"Line {h.line}: the path is missing")
    if not out:
        raise bad("补丁里一个文件都没有", "The patch doesn't touch any file")
    if len(out) > FILES_MAX:
        raise bad(f"一个补丁最多动 {FILES_MAX} 个文件", f"A patch can touch at most {FILES_MAX} files")
    return out


def _until_header(body: list[str], i: int) -> list[str]:
    """从 i 往后到下一个 *** 开头的行为止的那几行。"""
    out = []
    for x in body[i:]:
        if x.startswith("***"):
            break
        out.append(x)
    return out


def _chunk(body: list[str], i: int, first: bool) -> tuple[Chunk, int]:
    """一段 @@（照 OpenClaw 的 parseUpdateFileChunk）→ (这一段, 用了几行)。第一段可以不写 @@。"""
    n = i + 2
    head = body[i]
    context, start = None, i
    if head.strip() == "@@":
        start = i + 1
    elif head.startswith("@@ "):
        context = head[3:].strip()
        if context.endswith("@@") and not RANGE.fullmatch(context):  # Hermes / 老 V4A：@@ 上下文 @@
            context = context[:-2].strip()
        context = context or None
        start = i + 1
    elif not first:
        raise bad(f"第 {n} 行：每一段要从 @@ 开始，这里是「{head[:80]}」", f"Line {n}: each hunk must start with an @@ line, got '{head[:80]}'")
    if context and RANGE.fullmatch(context):  # 统一 diff 的段头（@@ -12,7 +12,8 @@）：不是上下文，不拿它定位
        context = None
    ops: list[tuple[str, str]] = []
    eof = False
    j = start
    while j < len(body):
        cur = body[j]
        if cur.strip() == EOF:
            eof = True
            j += 1
            break
        mark = cur[:1]
        if mark in ("", " ", "+", "-"):
            ops.append((mark or " ", cur[1:]))
            j += 1
            continue
        if not ops:
            raise bad(f"第 {j + 2} 行「{cur[:80]}」：段里每一行要以空格（不变）、+（加）、-（删）开头",
                      f"Line {j + 2} ('{cur[:80]}'): every line in a hunk starts with ' ' (unchanged), '+' (added) or '-' (removed)")
        break
    while ops and ops[-1] == (" ", "") and not eof:  # 段尾的空行（多半是段和段之间空的那一行）不算上下文：上下文照原文留着，少比一行不会改错
        ops.pop()
    if not ops:
        raise bad(f"第 {n} 行：这一段里一行都没有", f"Line {n}: this hunk has no lines")
    return Chunk(context, ops, eof), j - i


# —— 找到那几行、换上 ————————————————————————————————————————————————————————————————

ELLIPSIS = {"…": "..."}


def _loose(s: str) -> str:
    """最宽松的那一档：两头空白去掉，弯引号、横线、各种空格换成普通的（host_files.FOLD），省略号换成三个点，连着的空格、Tab 并成一个。"""
    s = "".join(host_files.FOLD.get(ch, ELLIPSIS.get(ch, ch)) for ch in s.strip())
    return re.sub(r"[ \t]+", " ", s)


TIERS = (lambda s: s, str.rstrip, str.strip, _loose)


def seek(lines: list[str], pattern: list[str], start: int, eof: bool = False, first: bool = False) -> tuple[str, int]:
    """在 lines[start:] 里找 pattern（一档档放宽，先找到的那一档定）→ ("found", 下标) / ("ambiguous", 几处) / ("missing", 0)。
    eof = 这一段贴着文件末尾：只在最后那几行找。first = 这一段有 @@ 定位：定位之后第一处就是它（Codex 原来的做法），不要求唯一。"""
    if not pattern:
        return "found", start
    top = len(lines) - len(pattern)
    begin = max(start, top) if eof else start
    if top < 0 or begin > top:
        return "missing", 0
    for norm in TIERS:
        want = [norm(x) for x in pattern]
        hits = [k for k in range(begin, top + 1) if all(norm(lines[k + d]) == want[d] for d in range(len(want)))]
        if hits:
            return ("found", hits[0]) if len(hits) == 1 or first else ("ambiguous", len(hits))
    return "missing", 0


def seek_context(lines: list[str], context: str, start: int) -> tuple[str, int]:
    """@@ 那一行的上下文：整行比（同 seek），对不上再看哪一行含着它（只许一行）。"""
    kind, at = seek(lines, [context], start)
    if kind != "missing":
        return kind, at
    want = _loose(context)
    hits = [k for k in range(start, len(lines)) if want and want in _loose(lines[k])]
    if not hits:
        return "missing", 0
    return ("found", hits[0]) if len(hits) == 1 else ("ambiguous", len(hits))


@dataclass
class Source:
    bom: str
    lines: list[list[str]]              # [字, 换行符]（换行符 "" = 文件末尾没有换行）
    ending: str                          # 这个文件用的换行
    open_end: bool                       # 末尾没有换行


def split_source(text: str) -> Source:
    bom = "﻿" if text.startswith("﻿") else ""
    body = text[len(bom):]
    lines: list[list[str]] = []
    for m in re.finditer(r"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+", body):
        raw = m.group(0)
        end = "\r\n" if raw.endswith("\r\n") else raw[-1] if raw[-1:] in ("\r", "\n") else ""
        lines.append([raw[: len(raw) - len(end)], end])
    ending = next((e for _, e in lines if e), "\n")
    return Source(bom, lines, ending, bool(lines) and lines[-1][1] == "")


def _hint(lines: list[str], old: list[str]) -> str:
    """找不到的时候：最像的那一行在第几行（Hermes 的 find_closest_lines 的意思，只给一处）。"""
    import difflib
    first = next((x for x in old if x.strip()), "")
    if not first or not lines:
        return ""
    best = difflib.get_close_matches(first.strip(), [x.strip() for x in lines], n=1, cutoff=0.6)
    if not best:
        return ""
    k = next(i for i, x in enumerate(lines) if x.strip() == best[0])
    return f" The closest line is {k + 1}: {lines[k].strip()[:120]!r}; read the file again and copy the lines exactly."


def apply_chunks(text: str, chunks: list[Chunk], rel: str) -> str:
    """一个文件的几段改动（都对着改之前的原文、从上往下找）→ 改完的全文。对不上抛 ToolFailure。"""
    src = split_source(text)
    plain = [t for t, _ in src.lines]
    reps: list[tuple[int, int, list[list[str]]]] = []
    at = 0
    for c in chunks:
        if c.context:
            kind, k = seek_context(plain, c.context, at)
            if kind == "ambiguous":
                raise ToolFailure(f"Found {k} lines matching the context '{c.context}' in {rel}. The @@ context must be unique; use a more "
                                  "specific line.")
            if kind == "missing":
                raise ToolFailure(f"Failed to find the context '{c.context}' in {rel}.{_hint(plain, [c.context])}")
            at = k + 1
        ops = list(c.ops)
        old = [t for m, t in ops if m != "+"]
        if not old:  # 只加不删：有上下文就加在它后面，没有就加在末尾
            where = at if c.context and not c.eof else len(src.lines)
            reps.append((where, 0, [[t, ""] for _, t in ops]))
            at = where
            continue
        anchored = bool(c.context)
        kind, k = seek(plain, old, at, c.eof, anchored)
        if kind == "missing" and old[-1] == "":  # 段尾多带了一个空行（文件末尾那个换行）：去掉再找（OpenClaw 也这样）
            last = max(i for i, (m, _) in enumerate(ops) if m != "+")
            ops.pop(last)
            old = [t for m, t in ops if m != "+"]
            kind, k = seek(plain, old, at, c.eof, anchored) if old else ("found", at)
        if kind == "ambiguous":
            raise ToolFailure(f"Found {k} places in {rel} matching these lines; they must be unique, so include more surrounding lines in "
                              "the hunk:\n" + "\n".join(c.old)[:2000])
        if kind == "missing":
            raise ToolFailure(f"Failed to find the expected lines in {rel}:\n" + "\n".join(c.old)[:2000] + _hint(plain, c.old))
        new: list[list[str]] = []
        j = k
        for m, t in ops:
            if m == " ":
                new.append(list(src.lines[j]))  # 上下文照原文（宽松地找到的也是原文的样子）
                j += 1
            elif m == "-":
                j += 1
            else:
                new.append([t, ""])
        reps.append((k, len(old), new))
        at = k + len(old)
    out = [list(x) for x in src.lines]
    for start, n, new in sorted(reps, key=lambda r: r[0], reverse=True):
        out[start:start + n] = new
    for row in out[:-1]:  # 中间的行都要有换行；新加的用这个文件自己的换行
        row[1] = row[1] or src.ending
    if out:
        out[-1][1] = "" if src.open_end else (out[-1][1] or src.ending)
    return src.bom + "".join(t + e for t, e in out)


# —— 整个补丁：先在内存里走一遍，再写 ——————————————————————————————————————————————————————

@dataclass
class State:
    pl: host_files.Place
    before: bytes | None                 # 磁盘上原来的样子（None = 没有这个文件）
    size: int = 0                        # 原来多大（太大的没读进来：big）
    big: bool = False
    after: bytes | None = None           # 补丁走完以后的样子
    touched: bool = False


def _lock(root) -> threading.Lock:
    with _guard:
        return _locks.setdefault(str(root), threading.Lock())


def _load(pl: host_files.Place) -> State:
    """磁盘上这个路径现在的样子（不跟链接）。是文件夹、链接、特殊文件的抛 ToolFailure。"""
    try:
        st = pl.fs.lstat(pl.parts)
    except (OSError, safefs.Refused) as e:
        raise host_files._fail(pl, e) from None
    if st is None:
        return State(pl, None)
    kind = safefs.kind_of(st.st_mode)
    if kind == "d":
        raise bad(f"{pl.rel} 是文件夹，补丁只动文件", f"{pl.rel} is a folder; patches only change files")
    if kind == "l":
        raise ToolFailure(f"{pl.rel}: {safefs.link_refused(pl.parts[-1])}")
    if kind != "f":
        raise bad(f"{pl.rel} 不是普通文件", f"{pl.rel} isn't a regular file")
    if st.st_size > versions.MAX_BYTES:
        return State(pl, b"", st.st_size, big=True)
    try:
        data = pl.fs.read(pl.parts, versions.MAX_BYTES + 1)[0]
    except (OSError, safefs.Refused) as e:
        raise host_files._fail(pl, e) from None
    return State(pl, data, len(data))


class Plan:
    """一个补丁在内存里走一遍的结果：每个碰到的路径原来什么样、走完什么样（后面的操作看得到前面改过的）。"""

    def __init__(self, agent: str) -> None:
        self.agent = agent
        self.states: dict[str, State] = {}
        self.order: list[str] = []
        self.marks: list[tuple[str, str]] = []   # (A / M / D / R, 显示的路径)

    def get(self, path: str) -> State:
        pl = host_files.place(self.agent, path, write=True)
        if pl.rel not in self.states:
            self.states[pl.rel] = _load(pl)
            self.order.append(pl.rel)
        return self.states[pl.rel]

    @staticmethod
    def now(s: State) -> bytes | None:
        return s.after if s.touched else s.before

    def put(self, s: State, data: bytes | None) -> None:
        s.after, s.touched = data, True


def plan(agent: str, hunks: list[Hunk]) -> Plan:
    """整个补丁在内存里走一遍（不写盘）。对不上、不许、目标已经有了 → 抛 ToolFailure，一个字都不落。"""
    p = Plan(agent)
    for h in hunks:
        s = p.get(h.path)
        cur = p.now(s)
        if h.kind == "add":
            if cur is not None:
                raise bad(f"{s.pl.rel} 已经有了：要改它用 *** Update File:，要换掉先在同一个补丁里 *** Delete File:",
                          f"{s.pl.rel} already exists: use '*** Update File: {s.pl.rel}' to change it, or delete it earlier in the same patch")
            p.put(s, h.contents.encode("utf8"))
            p.marks.append(("A", s.pl.rel))
            continue
        if cur is None:
            raise bad(f"{s.pl.rel}：没有这个文件（list_dir、find_files 看有什么）", f"{s.pl.rel}: no such file (list_dir or find_files shows what's there)")
        if h.kind == "delete":
            p.put(s, None)
            p.marks.append(("D", s.pl.rel))
            continue
        if s.big and not s.touched and h.chunks:
            raise bad(f"{s.pl.rel} 有 {s.size // 1024 // 1024} MB，太大，留不了旧版本，不改", f"{s.pl.rel} is {s.size // 1024 // 1024} MB, too big to keep an "
                      "old version of, so it isn't changed")
        if h.chunks:
            try:
                text = cur.decode("utf-8")
            except UnicodeDecodeError:
                raise bad(f"{s.pl.rel} 不是合法的 UTF-8，不能安全地改", f"{s.pl.rel} isn't valid UTF-8 and can't be patched safely") from None
            new = apply_chunks(text, h.chunks, s.pl.rel).encode("utf8")
        else:
            new = cur
        if h.move:
            d = p.get(h.move)
            if d.pl.rel != s.pl.rel:
                if p.now(d) is not None:
                    raise bad(f"{d.pl.rel} 已经有了：改名不盖掉别的文件（要换掉先在同一个补丁里删）",
                              f"{d.pl.rel} already exists: a move doesn't overwrite another file (delete it earlier in the same patch)")
                if s.big and not s.touched:  # 大文件改名：新的那份要写出来，内存里没有它
                    raise bad(f"{s.pl.rel} 太大，补丁里改不了名", f"{s.pl.rel} is too big to move in a patch")
                p.put(d, new)
                p.put(s, None)
                p.marks.append(("R", f"{s.pl.rel} -> {d.pl.rel}"))
                continue
        if new != cur:
            p.put(s, new)
            p.marks.append(("M", s.pl.rel))
    changed = [r for r in p.order if p.states[r].touched and p.states[r].after != p.states[r].before]
    if not changed:
        raise ToolFailure(L("补丁没有改动任何东西（改完和原来一模一样）", "No changes made: the patch leaves every file exactly as it was."))
    return p


def _changed(p: Plan) -> list[State]:
    return [p.states[r] for r in p.order if p.states[r].touched and (p.states[r].after != p.states[r].before or p.states[r].big)]


def unversioned(agent: str, hunks: list[Hunk]) -> list[str]:
    """要删的那些里留不了旧版本的（大过 versions.MAX_BYTES）：分类要问用户（删了回不来）。只看大小，不读内容。
    （大文件改名、改内容本来就不做：plan 里直接报错。）"""
    out = []
    for h in hunks:
        if h.kind != "delete":
            continue
        try:
            pl = host_files.place(agent, h.path, write=True)
            st = pl.fs.lstat(pl.parts)
        except (ToolFailure, OSError, safefs.Refused):
            continue
        if st is not None and st.st_size > versions.MAX_BYTES:
            out.append(pl.rel)
    return out


def commit(p: Plan, *, approved: bool = False) -> list[tuple[State, dict | None]]:
    """先给每个要动的文件留旧版本，再一个个写（先写、再删）；写到一半出错，已经写了的换回原样再报错。→ [(那个文件, 留的那一版)]。"""
    todo = _changed(p)
    kept: list[tuple[State, dict | None]] = []
    for s in todo:
        if s.big:
            if not approved:
                raise bad(f"{s.pl.rel} 太大，留不了旧版本：删它要用户点头", f"{s.pl.rel} is too big to keep an old version of; deleting it needs the user's OK")
            kept.append((s, None))
            continue
        try:
            v = versions.keep(s.pl.root, s.pl.rel, by=p.agent, note=f"before {NAME}")
        except versions.TooBig as e:
            raise ToolFailure(L(f"{e}：整个补丁没有落", f"{e}; nothing in the patch was applied")) from None
        except (OSError, safefs.Refused) as e:
            raise host_files._fail(s.pl, e) from None
        kept.append((s, v))
    done: list[State] = []
    try:
        for s in sorted(todo, key=lambda x: (x.after is None, x.big)):  # 先写、再删，留不了版本的大文件最后删
            if s.after is None:
                s.pl.fs.remove(s.pl.parts)
            else:
                s.pl.fs.write(s.pl.parts, s.after)
            done.append(s)
    except (OSError, safefs.Refused) as e:
        failed = host_files._fail(s.pl, e)
        for d in reversed(done):  # 换回原样（内存里有原来的字节；原来没有的删掉）
            try:
                if d.before is None:
                    d.pl.fs.remove(d.pl.parts)
                elif not d.big:
                    d.pl.fs.write(d.pl.parts, d.before)
            except (OSError, safefs.Refused):
                pass
        raise ToolFailure(L(f"{failed}：写到一半出错，已经写了的都换回去了，整个补丁没有落", f"{failed} — it failed partway, everything already written "
                            "was put back, and nothing in the patch was applied")) from None
    return kept


def _diffs(p: Plan) -> str:
    import difflib
    parts, used = [], 0
    for s in _changed(p):
        if s.big or s.before is None or s.after is None:
            continue
        a, b = s.before.decode("utf-8", "replace"), s.after.decode("utf-8", "replace")
        d = "\n".join(x for x in difflib.unified_diff(a.splitlines(), b.splitlines(), f"a/{s.pl.rel}", f"b/{s.pl.rel}", lineterm="", n=1))
        if not d:
            continue
        if used + len(d) > DIFF_MAX:
            parts.append("(… diffs for the other files left out; read_file shows them)")
            break
        parts.append(d)
        used += len(d)
    return "\n".join(parts)


def run(agent: str, text: str, *, approved: bool = False) -> tuple[str, list[str]]:
    """解析、在内存里走一遍、留版本、写。→ (给模型的结果, 改到的路径)。出错抛 ToolFailure（整个补丁没落）。"""
    hunks = parse(text)
    root = host_files.root_of(agent)
    if root is None:
        raise bad("你没有自己的工作区，补丁打不了", "You have no workspace of your own, so patches can't be applied")
    with _lock(root):
        p = plan(agent, hunks)
        kept = commit(p, approved=approved)
    lines = [L("补丁打好了：", "Success. Updated the following files:")] + [f"{m} {x}" for m, x in p.marks]
    undo = []
    for s, v in kept:
        if v is None:
            undo.append(L(f"{s.pl.rel}：太大，没留旧版本（用户同意了不留）", f"{s.pl.rel}: too big, no old version kept (the user approved that)"))
        else:
            undo.append(host_files.undo_note(s.pl, v))
    out = "\n".join(lines) + "\n" + "\n".join(undo)
    diff = _diffs(p)
    return (out + (f"\n\n{diff}" if diff else "")), [s.pl.rel for s, _ in kept]


# —— 工具 ——————————————————————————————————————————————————————————————————————

def prepare(a):
    """input 写成了 patch、写成了列表（一行一项）的也认。"""
    if not isinstance(a, dict):
        return a
    a = dict(a)
    if "input" not in a and isinstance(a.get("patch"), str):
        a["input"] = a.pop("patch")
    if isinstance(a.get("input"), list):
        a["input"] = "\n".join(str(x) for x in a["input"])
    return a


def judge(agent: str, args: dict) -> tuple[str, str]:
    """→ (undo / ask, 一句理由)。写法不对的算 undo：跑的时候报出来哪里不对，一个字都不落。"""
    from .handkit import levelled
    try:
        hunks = parse(str(args.get("input") or ""))
    except ToolFailure:
        return levelled(NAME, (UNDO, L("补丁写法不对：跑的时候报哪里不对，什么都不改", "The patch isn't valid: running it reports what's wrong and changes nothing")))
    rules = [p for h in hunks for p in (h.path, h.move) if p and host_files.asks(agent, p)]
    if rules:
        return levelled(NAME, (ASK, L(f"动到档案或规矩文件（{', '.join(rules[:3])}；做法：每次开工都照着它做），先提议、用户点了头才改",
                                      f"It touches the profile or a rules file ({', '.join(rules[:3])}: followed every session), so it's proposed "
                                      "and applied only once the user approves")))
    if big := unversioned(agent, hunks):
        return levelled(NAME, (ASK, L(f"要删、要改名的 {', '.join(big[:3])} 太大，留不了旧版本，删了就回不来", f"{', '.join(big[:3])} is too big to keep an "
                                      "old version of, so deleting or moving it can't be undone")))
    return levelled(NAME, (UNDO, L("每个改到的文件改之前都留了旧版本（新建的留一版「那时还没有」，删的留整份）；撤：restore_file 一个个换回",
                                   "Every file it touches keeps its old version first (an added file keeps 'didn't exist', a deleted one its "
                                   "whole content); undo: restore_file for each")))


def tools(agent: str) -> list[Tool]:
    """这个 Agent 的 apply_patch（engine.files.patch = true、有文件工具、有自己的工作区才有）。"""
    if not enabled() or not host_files.enabled(agent) or host_files.root_of(agent) is None:
        return []

    async def go(args: dict, ctx: ToolCtx) -> str:
        from .handkit import approved_run
        who = ctx.agent or "main"
        text, touched = await asyncio.to_thread(run, who, str(args.get("input") or ""), approved=approved_run())
        for rel in touched:
            host_files.noted(ctx, rel, "edit")
        return text

    def title(a: dict) -> str:
        try:
            hs = parse(str(a.get("input") or ""))
        except ToolFailure:
            return NAME
        names = [h.path for h in hs]
        return (", ".join(names[:3]) + (f" (+{len(names) - 3})" if len(names) > 3 else ""))[:80]

    return [Tool(name=NAME, kinds=("owner",), run=go, timeout=120, max_output=20_000, prepare=prepare, title=title,
                 classify=lambda a: judge(agent, a),
                 description=L(
                     "一次改你工作区里的好几个文件：input 是整个补丁，从 *** Begin Patch 写到 *** End Patch。*** Add File: 路径（后面每行 + 开头）新建；"
                     "*** Update File: 路径 改（一段段 @@，可以带一行定位：@@ def main():；段里空格开头 = 不变的上下文、- = 删、+ = 加，上下文前后各带 3 行左右，"
                     "要唯一；紧跟一行 *** Move to: 新路径 = 改名；*** End of File = 这一段贴着文件末尾）；*** Delete File: 路径 删。"
                     "全有或全无：哪一个文件对不上，整个补丁都不落。每个文件改之前留旧版本，restore_file 能换回。只改一处用 edit_file 更简单。",
                     "Change several files in your workspace at once: input is the whole patch, from *** Begin Patch to *** End Patch. "
                     "'*** Add File: path' (then lines starting with +) creates a file; '*** Update File: path' changes one (hunks start with @@, "
                     "optionally with a line to anchor on: '@@ def main():'; inside a hunk ' ' = unchanged context, '-' = removed, '+' = added; "
                     "give about 3 lines of context around each change so it's unique; a '*** Move to: new/path' line right after the header "
                     "renames it; '*** End of File' pins a hunk to the end); '*** Delete File: path' deletes one. All or nothing: if any file "
                     "doesn't match, nothing is applied. Every file keeps its old version first (restore_file brings it back). For a single "
                     "change, edit_file is simpler."),
                 schema={"type": "object", "required": ["input"], "properties": {
                     "input": {"type": "string", "description": L("整个补丁（*** Begin Patch … *** End Patch）", "The whole patch (*** Begin Patch … *** End Patch)")},
                     "why": {"type": "string", "description": L("要用户点头时写（动规矩文件、删大文件）：一句话说做什么、为什么",
                                                                "When it needs the user's OK (rules files, deleting a big file): one sentence on what and why")}}})]


async def ask(tool: Tool, args: dict, ctx: ToolCtx) -> dict | str:
    """关卡说 ask（规矩文件、删留不了版本的大文件）：交一张 kind tool 的卡，卡上是整个补丁（一个字不截：同意了跑的就是它）和要动的文件。
    同意了由 handkit.run_approved 照卡上的参数调一次（算点过头的）。"""
    import hashlib

    import inbox

    from . import handkit
    from . import tools as tools_mod
    agent = ctx.agent or "main"
    text = str(args.get("input") or "")
    try:
        hunks = parse(text)
    except ToolFailure as e:
        return str(e)
    if (no := handkit.too_long(text)) is not None:
        return no
    who = inbox.source_name(agent, inbox.names())
    reason = tool.level(args)[1].rstrip("。. ")
    files = [f"{ {'add': 'A', 'delete': 'D', 'update': 'M'}[h.kind] if not h.move else 'R'} {h.path}" + (f" -> {h.move}" if h.move else "")
             for h in hunks]
    fence = tools_mod._fence(text)
    card = {"title": L(f"打补丁：{', '.join(h.path for h in hunks)[:60]}", f"Apply a patch: {', '.join(h.path for h in hunks)[:60]}"),
            "why": str(args.get("why") or "").strip() or L(f"{who}想打一个补丁。{reason}。", f"{who} wants to apply a patch. {reason}."),
            "changes": [L(f"文件：{f}", f"File: {f}") for f in files[:20]],
            "detail": L("同意后，服务器照下面的补丁改一次（全有或全无），结果发回对话。", "If you approve, the server applies exactly this patch once "
                        "(all or nothing) and posts the result to the chat.") + f"\n\n{fence}diff\n{text}\n{fence}",
            "approveLabel": L("照做", "Do it")}
    payload = {"input": text}
    dedupe = "patch:" + hashlib.sha256(json.dumps([agent, text], ensure_ascii=False).encode()).hexdigest()[:32]
    return await tools_mod.submit_card(NAME, json.dumps(payload, ensure_ascii=False), None, card, dedupe, ctx, who=agent)
