"""diff（第 3 批第 12 项，2026-10-19）：两段文字比一比，或者工作区里一个文件和它留着的旧版本（engine/versions.py）比，回统一格式的
diff（太长留头尾）；可选再做成一页给用户看（pages.py 的页面，这次回复结束时挂在回复下面）。参数照 OpenClaw 的 diffs 工具（before /
after、path、title；它的看图页、渲染文件这里换成我们的页面），只借设计。

开关 engine.tools.diff = true（不写 = 没有）。
  before + after     两段文字（各最多 TEXT_MAX 字）；path 这时只是显示的文件名
  只给 path          工作区里的这个文件：version（不写 = 最近留的那一版）比现在；再写 to（另一个版本号）= 比两个旧版本
文件照 host_files 的路径守卫（只在自己的工作区、不跟链接、像密钥的不读），二进制的只说大小和指纹。比较在线程里做（difflib），行数有上限。
分类：只看 = read；page: true = undo（发一个页面：同一个 name 再发是新的一版，旧版留着）。
"""
from __future__ import annotations

import asyncio
import difflib
import hashlib
import html
import re
from dataclasses import dataclass

from i18n import L

from .loop import Tool, ToolCtx, ToolFailure

NAME = "diff"
READ, UNDO = "read", "undo"
TEXT_MAX = 512 * 1024      # before / after 各最多多少字（OpenClaw 的上限）
FILE_MAX = 2 * 1024 * 1024  # 文件、旧版本最多读多少字节
LINES_MAX = 40_000         # 一边最多多少行（difflib 太大会慢）
LINE_MAX = 2000            # 一行最多显示多少字
OUT_MAX, HEAD = 20_000, 14_000   # 回给模型的最多多少字；太长时前面留多少（后面留剩下的）
PAGE_TEXT_MAX = 180_000    # 做成页面时最多放多少字的 diff（pages.MAX_HTML 是 256 KB）
CONTEXT, CONTEXT_MAX = 3, 20


def enabled() -> bool:
    from config import raw
    e = raw().get("engine")
    t = e.get("tools") if isinstance(e, dict) else None
    v = t.get(NAME) if isinstance(t, dict) else None
    return v is True or (isinstance(v, dict) and v.get("enabled", True) is not False)


@dataclass
class Side:
    label: str
    text: str | None = None     # None = 没有这个文件（那时还没有 / 现在删了），或者是二进制
    binary: bytes | None = None


def side(label: str, data: bytes | None) -> Side:
    if data is None:
        return Side(label)
    if b"\x00" in data[:8192]:
        return Side(label, binary=data)
    return Side(label, data.decode("utf-8", "replace").removeprefix("﻿"))


def lines_of(s: Side) -> list[str]:
    return [] if s.text is None else s.text.replace("\r\n", "\n").replace("\r", "\n").splitlines()


def compare(a: Side, b: Side, context: int) -> tuple[list[str], int, int]:
    """→ (diff 的每一行, 加了几行, 删了几行)；一模一样 → ([], 0, 0)。"""
    if a.binary is not None or b.binary is not None:
        def what(s: Side) -> str:
            if s.binary is not None:
                return f"{len(s.binary)} bytes, sha256 {hashlib.sha256(s.binary).hexdigest()[:12]}"
            return "absent" if s.text is None else f"text, {len(s.text.encode('utf8'))} bytes"
        if a.binary is not None and b.binary is not None and a.binary == b.binary:
            return [], 0, 0
        return [f"Binary files differ: {a.label} ({what(a)}) → {b.label} ({what(b)})"], 0, 0
    al, bl = lines_of(a), lines_of(b)
    if max(len(al), len(bl)) > LINES_MAX:
        raise ToolFailure(L(f"太长了（最多 {LINES_MAX} 行）：比一部分", f"Too long to compare (at most {LINES_MAX} lines a side): compare a part"))
    out = list(difflib.unified_diff(al, bl, a.label if a.text is not None else "/dev/null", b.label if b.text is not None else "/dev/null",
                                    lineterm="", n=context))
    adds = sum(1 for x in out if x.startswith("+") and not x.startswith("+++"))
    dels = sum(1 for x in out if x.startswith("-") and not x.startswith("---"))
    return [x if len(x) <= LINE_MAX else x[:LINE_MAX] + "…" for x in out], adds, dels


def clip(lines: list[str], limit: int, head: int) -> tuple[list[str], int]:
    """太长：留头 head 字、尾巴剩下的，中间换成一行。→ (留下的行, 去掉了几行)。"""
    if sum(len(x) + 1 for x in lines) <= limit:
        return lines, 0
    top, used = [], 0
    for x in lines:
        if used + len(x) + 1 > head:
            break
        top.append(x)
        used += len(x) + 1
    tail, room = [], limit - used - 200
    for x in reversed(lines[len(top):]):
        if room - len(x) - 1 < 0:
            break
        tail.append(x)
        room -= len(x) + 1
    tail.reverse()
    cut = len(lines) - len(top) - len(tail)
    return top + [f"… {cut} diff lines not shown …"] + tail, cut


# —— 两边从哪来 ——————————————————————————————————————————————————————————————————

def at_of(v: dict) -> str:
    return str(v.get("at") or "")[:16].replace("T", " ")


def file_sides(agent: str, path: str, version: str | None, to: str | None) -> tuple[Side, Side, str]:
    """工作区里的文件：旧版本（version，不写 = 最近留的那一版）和现在（或者 to 那一版）。→ (旧, 新, 相对路径)。"""
    from . import host_files, safefs, versions
    pl = host_files.place(agent, path)
    try:
        hist = versions.history(pl.root, pl.rel)
    except (OSError, safefs.Refused) as e:
        raise host_files._fail(pl, e) from None
    if not hist:
        raise ToolFailure(L(f"{pl.rel} 没有留着的旧版本（写、改、打补丁之前才留）：要比两段文字用 before / after",
                            f"No old versions of {pl.rel} are kept (they're kept before each write, edit or patch); to compare two texts use "
                            "before / after"))

    def pick(vid: str) -> dict:
        v = next((x for x in hist if x["id"] == vid.strip().lower()), None)
        if v is None:
            raise ToolFailure(L(f"{pl.rel} 没有 {vid} 这一版（有：{'、'.join(x['id'] for x in hist)}）",
                                f"There's no version {vid} of {pl.rel} (kept: {', '.join(x['id'] for x in hist)})"))
        return v

    def kept(v: dict) -> Side:
        if not v.get("absent") and int(v.get("size") or 0) > FILE_MAX:
            raise ToolFailure(L(f"{pl.rel} 的 {v['id']} 太大（最多 {FILE_MAX // 1024 // 1024} MB）", f"{v['id']} of {pl.rel} is too big "
                                f"(at most {FILE_MAX // 1024 // 1024} MB)"))
        try:
            data = versions.read(pl.root, pl.rel, v["id"])
        except (KeyError, OSError, safefs.Refused) as e:
            raise ToolFailure(f"{pl.rel} {v['id']}: {e}") from None
        return side(f"{pl.rel} ({v['id']}, kept {at_of(v)})", data)

    old = kept(pick(version) if version else hist[0])
    if to:
        return old, kept(pick(to)), pl.rel
    try:
        data, size = pl.fs.read(pl.parts, FILE_MAX + 1)
    except FileNotFoundError:
        return old, Side(f"{pl.rel} (now: deleted)"), pl.rel
    except (OSError, safefs.Refused) as e:
        raise host_files._fail(pl, e) from None
    if size > FILE_MAX:
        raise ToolFailure(L(f"{pl.rel} 太大（最多 {FILE_MAX // 1024 // 1024} MB）", f"{pl.rel} is too big (at most {FILE_MAX // 1024 // 1024} MB)"))
    return old, side(f"{pl.rel} (now)", data), pl.rel


# —— 做成页面 ——————————————————————————————————————————————————————————————————

PAGE_CSS = ("pre{margin:0;font:12.5px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;white-space:pre-wrap;overflow-wrap:anywhere}"
            ".l{display:block;min-height:1.5em;padding:0 6px}.add{background:var(--good-soft)}.del{background:var(--bad-soft)}"
            ".hunk{color:var(--data)}.file{font-weight:700}.meta{margin-bottom:12px}")


def page_html(title: str, meta: str, lines: list[str]) -> str:
    def row(x: str) -> str:
        cls = ("file" if x.startswith(("---", "+++")) else "hunk" if x.startswith("@@") else "add" if x.startswith("+")
               else "del" if x.startswith("-") else "")
        return f'<span class="l {cls}">{html.escape(x)}</span>'
    return (f"<style>{PAGE_CSS}</style><h1>{html.escape(title)}</h1><p class=\"meta muted\">{html.escape(meta)}</p>"
            "<pre>" + "".join(row(x) for x in lines) + "</pre>")


def page_name(v: object, base: str) -> str:
    s = re.sub(r"[^a-z0-9_-]+", "-", str(v or "").strip().lower()).strip("-_")
    if not s:
        s = "diff-" + (re.sub(r"[^a-z0-9_-]+", "-", base.lower()).strip("-_") or "text")
    return s[:40].rstrip("-_") or "diff"


def publish(agent: str, name: str, title: str, page: str, summary: str) -> dict:
    import pages
    from fastapi import HTTPException
    try:
        r = pages.publish(pages.PageIn(agent=agent, name=name, title=title, summary=summary, html=page, note="diff"))
    except HTTPException as e:
        raise ToolFailure(L(f"页面没发出去：{e.detail}", f"The page couldn't be published: {e.detail}")) from None
    if not r.get("ok"):
        raise ToolFailure(L("页面没发出去：", "The page couldn't be published: ") + "; ".join(r.get("errors") or []))
    return r


# —— 工具 ——————————————————————————————————————————————————————————————————————————

async def run(args: dict, ctx: ToolCtx) -> str:
    who = ctx.agent or "main"
    before, after = args.get("before"), args.get("after")
    path = str(args.get("path") or "").strip()
    context = max(0, min(CONTEXT_MAX, int(args.get("context") if args.get("context") is not None else CONTEXT)))
    if before is not None or after is not None:
        if not isinstance(before, str) or not isinstance(after, str):
            raise ToolFailure(L("before 和 after 要一起给（两段文字）", "Give both before and after (two texts)"))
        if max(len(before), len(after)) > TEXT_MAX:
            raise ToolFailure(L(f"文字太长（各最多 {TEXT_MAX} 字）", f"The texts are too long (at most {TEXT_MAX} characters each)"))
        name = path[:200] or "text"
        a, b, base = Side(f"a/{name}", before), Side(f"b/{name}", after), name
    elif path:
        a, b, base = await asyncio.to_thread(file_sides, who, path, str(args.get("version") or "").strip() or None,
                                             str(args.get("to") or "").strip() or None)
    else:
        raise ToolFailure(L("给 before + after（两段文字），或者 path（工作区里的文件，和它留着的旧版本比）",
                            "Give before + after (two texts), or path (a workspace file, compared with a kept old version)"))
    lines, adds, dels = await asyncio.to_thread(compare, a, b, context)
    if not lines:
        return L(f"没有不同：{a.label} 和 {b.label} 一模一样。", f"No differences: {a.label} and {b.label} are the same.")
    stats = f"+{adds} −{dels} lines" if adds or dels else ""
    shown, cut = clip(lines, OUT_MAX, HEAD)
    out = "\n".join(shown) + (f"\n({stats})" if stats else "")
    if cut:
        out += L(f"\n（太长了：中间 {cut} 行没显示；比一部分、少给几行上下文（context），或者 page: true 做成一页看全部）",
                 f"\n(Long: {cut} lines in the middle aren't shown; compare a part, use less context, or page: true to see it all as a page)")
    if args.get("page") is True:
        title = (" ".join(str(args.get("title") or "").split()) or L(f"对比：{base}", f"Diff: {base}"))[:60]
        body, pcut = clip(lines, PAGE_TEXT_MAX, PAGE_TEXT_MAX * 3 // 4)
        meta = f"{a.label} → {b.label}" + (f" · {stats}" if stats else "") + (f" · {pcut} lines not shown" if pcut else "")
        r = await asyncio.to_thread(publish, who, page_name(args.get("name"), base), title, page_html(title, meta, body), stats or base)
        out += L(f"\n\n页面「{title}」第 {r['version']} 版" + ("（和上一版一样）" if r.get("unchanged") else "") + "：挂在这次回复下面。",
                 f'\n\nPage "{title}" v{r["version"]}' + (" (same as the last version)" if r.get("unchanged") else "")
                 + ": it shows under this reply.")
    return out


def tool(agent: str) -> Tool | None:
    if not enabled():
        return None
    from .handkit import levelled
    return Tool(
        name=NAME, kinds=("owner",), run=run, timeout=120, max_output=OUT_MAX + 2000,
        title=lambda a: str(a.get("path") or a.get("title") or "")[:80],
        classify=lambda a: levelled(NAME, (UNDO, L("发一个页面给用户看（同名再发是新的一版，旧版留着）", "Publishes a page for the user (the same "
                                                   "name again makes a new version; old ones are kept)")) if a.get("page") is True
                                    else (READ, L("只比较，什么都不改", "Only compares; changes nothing"))),
        description=L(
            "比较，回统一格式的 diff（太长留头尾）：before + after 两段文字；或者只给 path（工作区里的文件）= 它留着的旧版本（version，"
            "不写 = 最近留的那一版；restore_file 能列出有哪些）比现在，再写 to = 比两个旧版本。page: true 再做成一页给用户看（挂在这次回复下面）。",
            "Compare and get a unified diff (long ones keep the start and the end): before + after are two texts; or path alone (a workspace "
            "file) compares a kept old version (version; default the most recent one; restore_file lists them) with the file now, and to "
            "compares two old versions. page: true also makes it a page for the user, shown under this reply."),
        schema={"type": "object", "properties": {
            "before": {"type": "string", "description": L("原来的文字", "The original text")},
            "after": {"type": "string", "description": L("改过的文字", "The changed text")},
            "path": {"type": "string", "description": L("只给它：工作区里的文件；和 before / after 一起：只是显示的文件名",
                                                        "Alone: a file in your workspace; with before / after: only the name shown")},
            "version": {"type": "string", "description": L("旧的那一版（v3 这样；不写 = 最近留的那一版）", "The old version (like v3; default the most recent)")},
            "to": {"type": "string", "description": L("新的那一边也用一个旧版本（不写 = 文件现在的样子）", "Use another kept version as the new side (default: the file now)")},
            "context": {"type": "integer", "description": L(f"每处改动前后带几行（默认 {CONTEXT}）", f"Lines of context around each change (default {CONTEXT})")},
            "page": {"type": "boolean", "description": L("再做成一页给用户看", "Also make it a page for the user")},
            "title": {"type": "string", "description": L("页面标题", "Page title")},
            "name": {"type": "string", "description": L("页面的 name（同一个 name 再发是新的一版）", "Page name (the same name again makes a new version)")}}})
