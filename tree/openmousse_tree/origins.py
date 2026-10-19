"""来源（2026-10-16 定）：每周修剪合并、改写出来的叶子记下它从哪几片来，并进档案的句子记下它从哪片叶子来。

忘掉一片叶子时（store / cli 的 forget），照这里列出档案里从它来的句子：连着合并、改写过的那几代一起算
（A、B 合成 C，C 并进档案成了一句：忘掉 A 也列出这一句），让用户决定要不要一起删。
存在 ~/.mousse-tree/origins.json（两种存储共用；Markdown 存储重建索引不影响它），写的时候拿树的文件锁。
  {"leaves":  {"<新叶子 id>": {"from": ["<旧叶子 id>", …], "op": "merge" | "rewrite", "at": 时间}},
   "profile": {"<档案要点 id>": {"section": 小节, "text": 那句话, "from": ["<叶子 id>"], "op": "promote", "at": 时间}}}
"""
from __future__ import annotations

import json
from pathlib import Path

from . import config as C


def path() -> Path:
    return C.HOME / "origins.json"


def load() -> dict:
    try:
        d = json.loads(path().read_text(encoding="utf8"))
    except (OSError, ValueError):
        d = {}
    if not isinstance(d, dict):
        d = {}
    d.setdefault("leaves", {})
    d.setdefault("profile", {})
    return d


def _save(d: dict) -> None:
    from .notes import atomic_write
    C.HOME.mkdir(parents=True, exist_ok=True)
    atomic_write(path(), json.dumps(d, ensure_ascii=False, indent=1) + "\n")


def add_leaf(new: str, sources: list[str], op: str) -> None:
    from . import store as S
    with S.locked():
        d = load()
        d["leaves"][new] = {"from": list(dict.fromkeys(sources)), "op": op, "at": S.now_iso()}
        _save(d)


def add_profile(pid: str, section: str, text: str, sources: list[str]) -> None:
    from . import store as S
    with S.locked():
        d = load()
        old = d["profile"].get(pid) or {}
        d["profile"][pid] = {"section": section, "text": text, "from": list(dict.fromkeys([*(old.get("from") or []), *sources])),
                             "op": "promote", "at": S.now_iso()}
        _save(d)


def descendants(leaf: str, d: dict | None = None) -> set[str]:
    """这片叶子，加上从它合并、改写出来的（一代代往下）。"""
    d = d or load()
    out, frontier = {leaf}, {leaf}
    while frontier:
        nxt = {new for new, v in d["leaves"].items() if new not in out and set(v.get("from") or []) & frontier}
        out |= nxt
        frontier = nxt
    return out


def profile_from(leaf: str, current: set[str]) -> list[dict]:
    """档案里（还在的，current = 现在档案要点的 id）从这片叶子来的句子 → [{"id", "section", "text"}]。"""
    d = load()
    ids = descendants(leaf, d)
    return [{"id": pid, "section": v.get("section") or "", "text": v.get("text") or ""}
            for pid, v in d["profile"].items() if pid in current and set(v.get("from") or []) & ids]


def forget_profile(pids: set[str]) -> None:
    """这几句已经从档案里删了：来源也不留。"""
    if not pids:
        return
    from . import store as S
    with S.locked():
        d = load()
        for pid in pids:
            d["profile"].pop(pid, None)
        _save(d)
