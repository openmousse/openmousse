"""工具输出太长存成文件、让模型翻页（第 2 批，10/19，engine.loop.spill，默认关）。

以前（loop._clip）：一个结果超过 Tool.max_output（默认 3 万字）就留头 70%、尾 25%，掐掉的中间再也拿不回来。开了以后：
  全文存进 <data_dir>/engine/spill/<对话>/<编号>.txt（文件 0600；按对话分：翻页工具只认这个对话自己的），工具结果里还是那么长：开头、
  结尾，中间一句「掐掉了第几行到第几行、全文在哪、怎么翻」；read_output(id, offset, limit) 按行读（照 pi 的 read：offset 从 1 数，一次最多
  limit 行、PAGE_CHARS 字，后面还有就写明下一次的 offset）。
  只给主人回合（有工具才翻得了）：外人回合零工具、照旧掐；Claude Code 的回合走 /mcp，照旧掐（它那边没有这个翻页工具）。
  定期清：KEEP_DAYS 天以前的、总量超过 STORE_MAX 的（旧的先删）顺手清掉（照 engine/images.py 的 prune，一小时最多一次）；
  翻到清掉了的：回一句「已经不在了，要的话重新跑一次那个工具」（Hermes：那一次调用可能已经做了事，先看看现在的样子）。
借（10/19 读的，只借设计和说法）：Hermes tools/tool_result_storage.py（全文落盘、给开头、一句「全文在哪、用 read_file 带 offset / limit 翻」、
  按会话清）、pi 的 bash.ts / read.ts（超了存成文件，[Showing lines X-Y of N. Use offset=Z to continue.]）。
server.json：engine.loop.spill = true 或 {"keep_days": 7, "store_max_mb": 512}。
"""
from __future__ import annotations

import hashlib
import os
import re
import time
from pathlib import Path

from config import settings
from i18n import L

from .loop import Tool, ToolCtx, ToolFailure

NAME = "read_output"
KEEP_DAYS = 7
STORE_MAX = 512 * 1024 * 1024
PRUNE_EVERY = 3600
PAGE_LINES, PAGE_MAX_LINES, PAGE_CHARS = 400, 2_000, 20_000
SAFE = re.compile(r"[^A-Za-z0-9_.-]")
_pruned = [0.0]


def cfg() -> dict | None:
    """engine.loop.spill：true 或 {…}；不写 / false = 不落盘（None）。"""
    from . import models
    lp = models.cfg().get("loop")
    v = lp.get("spill") if isinstance(lp, dict) else None
    if v is True:
        return {}
    return v if isinstance(v, dict) and v.get("enabled", True) is not False else None


def _int(key: str, dflt: int) -> int:
    v = (cfg() or {}).get(key)
    return v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else dflt


def store_dir() -> Path:
    return settings.data_dir / "engine" / "spill"


def _safe(name: str) -> str:
    """文件名：只留字母、数字、_ . -；换过字的加一段指纹（照 Hermes 的做法，免得两个不同的名字撞成一个）。"""
    s = SAFE.sub("_", name or "-")[:80].strip(".") or "-"
    return s if s == name else f"{s}_{hashlib.sha256((name or '').encode()).hexdigest()[:10]}"


def folder(conversation: str) -> Path:
    return store_dir() / _safe(conversation or "-")


def save(conversation: str, turn: str, call_id: str, text: str) -> str:
    """全文存下来 → 编号（翻页时用）。"""
    sid = _safe(f"{turn[-8:]}-{call_id}")
    d = folder(conversation)
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{sid}.txt"
    tmp = p.with_name(f".{p.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf8") as f:
        f.write(text)
    tmp.replace(p)
    if time.time() - _pruned[0] > PRUNE_EVERY:
        _pruned[0] = time.time()
        prune()
    return sid


def shown(text: str, limit: int, sid: str) -> str:
    """工具结果里留下的：开头约六成、结尾约两成，中间一句掐掉了哪几行、怎么翻（总长和以前 _clip 的差不多）。"""
    head, tail = int(limit * 0.6), int(limit * 0.2)
    a, b = text[:head], text[-tail:] if tail else ""
    total = text.count("\n") + 1
    first = a.count("\n") + 1                    # 开头最后那一行（可能只留了半行）
    last = total - (b.count("\n") + 1) + 1       # 结尾第一行
    note = (f"[… {len(text) - len(a) - len(b)} characters (lines {first}–{last} of {total}) are not shown here. The full output is saved: "
            f"read it with {NAME}(id=\"{sid}\", offset={first}), a page at a time.]")
    return f"{a}\n\n{note}\n\n{b}"


def read(conversation: str, sid: str, offset: int = 1, limit: int = PAGE_LINES) -> str:
    """翻一页：offset 从 1 数的行号，最多 limit 行、PAGE_CHARS 字。找不到 → ToolFailure（说清楚）。"""
    p = folder(conversation) / f"{_safe(sid)}.txt"
    if not sid or _safe(sid) != sid or not p.is_file():
        raise ToolFailure(L(f"没有 {sid} 这份输出（只认这个对话里存的；可能已经过期清掉了）：要的话重新跑一次那个工具，先看看现在的样子，那一次可能已经做了事。",
                            f"There's no saved output {sid} (only this conversation's count, and old ones are cleaned up): if you still need it, run "
                            "that tool again, after checking the current state, since the first call may already have done something."))
    lines = p.read_text(encoding="utf8", errors="replace").split("\n")
    total = len(lines)
    start = max(1, offset)
    if start > total:
        return f"[The output has {total} lines; offset {start} is past the end.]"
    out, size = [], 0
    for ln in lines[start - 1:start - 1 + max(1, min(PAGE_MAX_LINES, limit))]:
        if out and size + len(ln) + 1 > PAGE_CHARS:
            break
        out.append(ln if len(ln) <= PAGE_CHARS else ln[:PAGE_CHARS] + " …[line cut]")
        size += len(out[-1]) + 1
    end = start + len(out) - 1
    more = f" Use offset={end + 1} to continue." if end < total else ""
    return "\n".join(out) + f"\n\n[Showing lines {start}-{end} of {total}.{more}]"


def tool() -> Tool:
    async def run(args: dict, ctx: ToolCtx) -> str:
        return read(ctx.conversation, str(args.get("id") or "").strip(), int(args.get("offset") or 1), int(args.get("limit") or PAGE_LINES))

    return Tool(name=NAME, kinds=("owner",), run=run, timeout=30, classify=lambda args: ("read", "只读：翻存下来的工具输出"),
                title=lambda args: f"{NAME} {args.get('id') or ''} @{args.get('offset') or 1}".strip(),
                description=L(
                    "翻一份太长、存下来了的工具输出（工具结果里写着 id 和从哪一行接着读）。offset 是从 1 数的行号，limit 是一次几行"
                    f"（默认 {PAGE_LINES}，最多 {PAGE_MAX_LINES}；一页最多 {PAGE_CHARS} 字）。只读；只认这个对话里存的。",
                    "Read a page of a tool output that was too long and got saved (the tool result names its id and the line to continue "
                    f"from). offset is a line number counted from 1; limit is how many lines (default {PAGE_LINES}, at most {PAGE_MAX_LINES}; "
                    f"at most {PAGE_CHARS} characters a page). Read-only; only this conversation's saved outputs."),
                schema={"type": "object", "required": ["id"], "properties": {
                    "id": {"type": "string", "description": L("工具结果里写的那个 id", "The id given in the tool result")},
                    "offset": {"type": "integer", "description": L("从第几行读（从 1 数）", "Line to start from (counted from 1)")},
                    "limit": {"type": "integer", "description": L(f"几行，默认 {PAGE_LINES}", f"How many lines, default {PAGE_LINES}")}}})


def prune() -> int:
    """清：KEEP_DAYS 天以前存的，再按总量从旧的删（照 images.prune）。→ 删了几份。"""
    d = store_dir()
    try:
        files = sorted(((p.stat().st_mtime, p.stat().st_size, p) for p in d.glob("*/*.txt") if p.is_file()), key=lambda x: x[0])
    except OSError:
        return 0
    cutoff = time.time() - _int("keep_days", KEEP_DAYS) * 86400
    total = sum(s for _, s, _ in files)
    limit = _int("store_max_mb", STORE_MAX // (1024 * 1024)) * 1024 * 1024
    n = 0
    for mtime, size, p in files:
        if mtime >= cutoff and total <= limit:
            break
        try:
            p.unlink()
            total -= size
            n += 1
        except OSError:
            pass
    for sub in (x for x in d.iterdir() if x.is_dir()) if d.is_dir() else ():
        try:
            sub.rmdir()  # 空了的对话文件夹
        except OSError:
            pass
    return n
