"""留旧版本 / 恢复（内核 ⑫，2026-10-16，通用的小模块）：改一个文件之前把它现在的样子存一份，能列出来、能换回去。
主机上的文件工具（engine/host_files.py 的 write_file、edit_file、restore_file）每次写之前调 keep；⑬ 写 skill（engine/skill_write.py）
也存在这里：它的一版是整份 skill，打包好了用 keep_data 存（键 skills/<名字>），恢复时 read 出来自己写回去。

存在哪：根目录（工作区）下的隐藏目录 .mousse-versions/（文件工具列不出、读不到、写不了）：
  <路径的指纹>/index.json   {"path": 相对路径, "next": 下一个号, "versions": [{"id": "v3", "at", "size", "sha", "absent", "by", "note"}]}
                            （旧的在前）
  <路径的指纹>/v3            那一版的内容（absent = 那时候还没有这个文件：恢复到它 = 删掉）
  usage.json                 大概占了多少字节（超了再细算）
上限：一个文件最多留 KEEP 版（旧的先删）；一版超过 MAX_BYTES 不留（keep 抛 TooBig，调用方别改这个文件）；整个目录超过 TOTAL_MAX，
从最旧的删到八成。和上一版一模一样就不再存一份。读写都经 engine/safefs.py（不跟符号链接）；同一个根目录同一时刻只有一个在改
（进程里的锁；文件工具在线程里跑）。
"""
from __future__ import annotations

import hashlib
import json
import stat
import threading
from datetime import datetime
from pathlib import Path

from config import settings
from i18n import L

from . import safefs

DIR = ".mousse-versions"
KEEP = 20
MAX_BYTES = 10 * 1024 * 1024
TOTAL_MAX = 200 * 1024 * 1024
_locks: dict[str, threading.Lock] = {}
_guard = threading.Lock()


class TooBig(Exception):
    """这个文件太大，不留旧版本（调用方别改它）。"""


def _lock(root: Path) -> threading.Lock:
    with _guard:
        return _locks.setdefault(str(root), threading.Lock())


def key_of(rel: str) -> str:
    return hashlib.sha1(rel.encode("utf8")).hexdigest()[:16]


def _parts(rel: str) -> tuple[list[str], str]:
    parts = safefs.split(rel)
    if not parts:
        raise safefs.Refused(L("要写文件的路径", "Give a file path"))
    if parts[0] == DIR:
        raise safefs.Refused(L("旧版本自己不留旧版本", "The old-versions folder itself isn't versioned"))
    return parts, "/".join(parts)


def _now() -> str:
    return datetime.now(settings.tz).isoformat(timespec="seconds")


def _read_json(r: safefs.Root, parts: list[str], dflt: dict) -> dict:
    try:
        data = json.loads(r.read(parts)[0].decode("utf8"))
        return data if isinstance(data, dict) else dflt
    except (OSError, ValueError, safefs.Refused):
        return dflt


def _write_json(r: safefs.Root, parts: list[str], data: dict) -> None:
    r.write(parts, json.dumps(data, ensure_ascii=False, indent=1).encode("utf8"))


def _index(r: safefs.Root, key: str, rel: str) -> dict:
    idx = _read_json(r, [DIR, key, "index.json"], {})
    if idx.get("path") != rel or not isinstance(idx.get("versions"), list):
        return {"path": rel, "next": 1, "versions": []}
    return idx


def _usage(r: safefs.Root, delta: int) -> int:
    u = _read_json(r, [DIR, "usage.json"], {"bytes": 0})
    total = max(0, int(u.get("bytes") or 0) + delta)
    _write_json(r, [DIR, "usage.json"], {"bytes": total})
    return total


def _keep(r: safefs.Root, parts: list[str], rel: str, by: str, note: str) -> dict:
    st = r.lstat(parts)
    if st is not None and not stat.S_ISREG(st.st_mode):
        raise safefs.Refused(L(f"{rel} 不是普通文件", f"{rel} isn't a regular file"))
    if st is not None and st.st_size > MAX_BYTES:
        raise TooBig(L(f"{rel} 有 {st.st_size // 1024 // 1024} MB，太大，留不了旧版本", f"{rel} is {st.st_size // 1024 // 1024} MB, too big to keep an old version of"))
    data = r.read(parts)[0] if st is not None else None
    return _store(r, rel, data, by, note)


def _store(r: safefs.Root, rel: str, data: bytes | None, by: str, note: str, extra: dict | None = None) -> dict:
    key = key_of(rel)
    idx = _index(r, key, rel)
    sha = hashlib.sha256(data).hexdigest() if data is not None else ""
    last = idx["versions"][-1] if idx["versions"] else None
    if last and last.get("sha") == sha and bool(last.get("absent")) == (data is None):
        return last  # 和上一版一样：不再存一份
    vid = f"v{int(idx.get('next') or 1)}"
    idx["next"] = int(vid[1:]) + 1
    if data is not None:
        r.write([DIR, key, vid], data)
    v = {"id": vid, "at": _now(), "size": len(data or b""), "sha": sha, "absent": data is None, "by": by, "note": note, **(extra or {})}
    idx["versions"].append(v)
    drop, idx["versions"] = idx["versions"][:-KEEP], idx["versions"][-KEEP:]
    for d in drop:
        r.remove([DIR, key, d["id"]])
    _write_json(r, [DIR, key, "index.json"], idx)
    if _usage(r, v["size"] - sum(int(d.get("size") or 0) for d in drop)) > TOTAL_MAX:
        _prune(r)
    return v


def _prune(r: safefs.Root) -> None:
    """整个目录超了：从最旧的版本删到八成（每个文件至少留最新的一版）。"""
    alls: list[tuple[str, str, dict]] = []
    idxs: dict[str, dict] = {}
    for e in r.entries([DIR]):
        if e.kind != "d":
            continue
        idx = _read_json(r, [DIR, e.name, "index.json"], {})
        if isinstance(idx.get("versions"), list):
            idxs[e.name] = idx
            alls += [(str(v.get("at") or ""), e.name, v) for v in idx["versions"][:-1]]
    total = sum(int(v.get("size") or 0) for idx in idxs.values() for v in idx["versions"])
    for _, key, v in sorted(alls, key=lambda x: x[0]):
        if total <= TOTAL_MAX * 0.8:
            break
        r.remove([DIR, key, v["id"]])
        idxs[key]["versions"] = [x for x in idxs[key]["versions"] if x["id"] != v["id"]]
        total -= int(v.get("size") or 0)
    for key, idx in idxs.items():
        _write_json(r, [DIR, key, "index.json"], idx)
    _write_json(r, [DIR, "usage.json"], {"bytes": total})


def keep(root: Path, rel: str, *, by: str = "", note: str = "") -> dict:
    """改 root/rel 之前调：把它现在的样子存一版 → 那一版 {"id", "at", "size", "absent", …}（没有这个文件也记一版：absent）。
    太大抛 TooBig；路径不对抛 safefs.Refused。"""
    parts, rel = _parts(rel)
    r = safefs.Root(root)
    with _lock(r.path):
        return _keep(r, parts, rel, by, note)


def keep_data(root: Path, rel: str, data: bytes | None, *, by: str = "", note: str = "", extra: dict | None = None) -> dict:
    """不读盘，把给的内容存成 rel 的一版（data 是 None = 那时还没有它），规矩和 keep 一样（和上一版一模一样就不再存、留 KEEP 版、
    整个目录的上限）。给不是单个文件的东西用：⑬ 写 skill 把整份 skill 打包成一版。extra：多记在这一版上的字段。太大抛 TooBig。"""
    parts, rel = _parts(rel)
    if data is not None and len(data) > MAX_BYTES:
        raise TooBig(L(f"{rel} 有 {len(data) // 1024 // 1024} MB，太大，留不了旧版本", f"{rel} is {len(data) // 1024 // 1024} MB, too big to keep an old version of"))
    r = safefs.Root(root)
    with _lock(r.path):
        return _store(r, rel, data, by, note, extra)


def read(root: Path, rel: str, vid: str) -> bytes | None:
    """留着的 vid 那一版的内容（那时还没有这个文件 → None）。没有这一版抛 KeyError。"""
    parts, rel = _parts(rel)
    r = safefs.Root(root)
    key, want = key_of(rel), str(vid or "").strip().lower()
    v = next((x for x in _index(r, key, rel)["versions"] if x["id"] == want), None)
    if v is None:
        raise KeyError(L(f"{rel} 没有 {vid} 这一版", f"There's no version {vid} of {rel}"))
    return None if v.get("absent") else r.read([DIR, key, v["id"]])[0]


def history(root: Path, rel: str) -> list[dict]:
    """留着的旧版本，新的在前。"""
    parts, rel = _parts(rel)
    r = safefs.Root(root)
    return list(reversed(_index(r, key_of(rel), rel)["versions"]))


def restore(root: Path, rel: str, vid: str | None = None, *, by: str = "") -> dict:
    """换回 vid 那一版（不写 = 最近留的那一版）。换之前先把现在的也留一版（这次恢复也能撤）。
    → {"restored": 那一版, "kept": 现在的这一版（存成了哪一版）, "removed": 恢复成「没有这个文件」= 删了}。没有这一版抛 KeyError。"""
    parts, rel = _parts(rel)
    r = safefs.Root(root)
    with _lock(r.path):
        versions = _index(r, key_of(rel), rel)["versions"]
        if not versions:
            raise KeyError(L(f"{rel} 没有留着的旧版本", f"No old versions of {rel} are kept"))
        want = str(vid or "").strip().lower()
        target = next((v for v in versions if v["id"] == want), None) if want else versions[-1]
        if target is None:
            raise KeyError(L(f"{rel} 没有 {vid} 这一版（有：{'、'.join(v['id'] for v in reversed(versions))}）",
                             f"There's no version {vid} of {rel} (kept: {', '.join(v['id'] for v in reversed(versions))})"))
        data = None if target.get("absent") else r.read([DIR, key_of(rel), target["id"]])[0]
        cur = _keep(r, parts, rel, by, L(f"恢复到 {target['id']} 之前", f"before restoring {target['id']}"))
        if data is None:
            removed = r.remove(parts)
        else:
            r.write(parts, data)
            removed = False
        return {"restored": target, "kept": cur, "removed": removed}
