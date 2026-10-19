"""改之前留一版（2026-10-19，0.6）：包里以前没有通用的版本，手册（manuals.py）改、删、停用、换回之前先把原来的样子整份存一份。
叶子、枝不走这里：叶子有自己的取代和归档，修剪计划做过的改名留着（prune.py）。

放在 ~/.mousse-tree/versions/<类>/<键>/（两种存储一样：Markdown 存储也不放进笔记文件夹，免得 Obsidian 里多出一堆旧稿）：
  v<n>.md    那时的全文，原样
  v<n>.json  {"id", "at", "by", "note"}：什么时候、谁、为什么存的（edit / rm / disable / restore……）
留最近 KEEP 份；和上一份一模一样就不再存。
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from . import config as C

KEEP = 20
_SAFE = re.compile(r'[\\/:*?"<>|#^\[\]\x00-\x1f]')


def folder(kind: str, key: str) -> Path:
    """这一样的版本放哪：名字里不能当文件名的字换掉，不分大小写（Mac 的文件系统不分）。"""
    safe = _SAFE.sub("_", " ".join(str(key).split())).strip(" .").casefold() or "_"
    return C.HOME / "versions" / kind / safe


def _ids(d: Path) -> list[int]:
    if not d.is_dir():
        return []
    return sorted(int(p.stem[1:]) for p in d.glob("v*.md") if p.stem[1:].isdigit())


def history(kind: str, key: str) -> list[dict]:
    """存过的版本，新的在前：[{"id", "at", "by", "note"}]。"""
    d = folder(kind, key)
    out = []
    for n in reversed(_ids(d)):
        try:
            meta = json.loads((d / f"v{n}.json").read_text(encoding="utf8"))
        except (OSError, ValueError):
            meta = {}
        out.append({"id": f"v{n}", "at": str(meta.get("at") or ""), "by": str(meta.get("by") or ""), "note": str(meta.get("note") or "")})
    return out


def read(kind: str, key: str, vid: str) -> str | None:
    """那一版的全文；没有这一版 → None。"""
    vid = str(vid or "").strip()
    if not re.fullmatch(r"v\d{1,6}", vid):
        return None
    try:
        return (folder(kind, key) / f"{vid}.md").read_text(encoding="utf8")
    except OSError:
        return None


def keep(kind: str, key: str, text: str, *, by: str, note: str) -> str:
    """存一版 → 版本号（和上一版一模一样就回上一版的号，不再存）。"""
    from . import store as S
    from .notes import atomic_write
    d = folder(kind, key)
    ids = _ids(d)
    if ids and read(kind, key, f"v{ids[-1]}") == text:
        return f"v{ids[-1]}"
    n = (ids[-1] if ids else 0) + 1
    atomic_write(d / f"v{n}.md", text)
    atomic_write(d / f"v{n}.json", json.dumps({"id": f"v{n}", "at": S.now_iso(), "by": by, "note": note}, ensure_ascii=False) + "\n")
    for old in [*ids, n][:-KEEP]:
        for ext in ("md", "json"):
            (d / f"v{old}.{ext}").unlink(missing_ok=True)
    return f"v{n}"


def move(kind: str, old: str, new: str) -> None:
    """改了名字：存过的版本跟着搬到新名字下（新名字下已经有的不动）。"""
    a, b = folder(kind, old), folder(kind, new)
    if a == b or not a.is_dir() or b.exists():
        return
    b.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(a), str(b))
