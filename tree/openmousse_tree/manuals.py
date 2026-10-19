"""手册（2026-10-19，0.6）：同一套做法（规矩、技能的正文）只存一份，叫「手册」，放在世界树旁边；各处（CLAUDE.md、AGENTS.md、Agent 的
规矩文件）只留几行「去哪取」的指针（pointers）。Claude.ai、ChatGPT、Gemini、云端的 Claude Code、OpenMousse 的内核都从这里取：改一次，各处同时生效。

一份手册：
  name    名字，用平时说的话（「记录客户拜访」，不是「在拜访表建一行」）；要能当文件名（1 到 40 个字，不能有 \\ / : * ? " < > | # ^ [ ]），
          不分大小写不能重名
  when    什么时候用：几句平时会说的话（「丰富文书素材、改 SOP、准备推荐信材料」），最多 WHENS_MAX 句
  about   一句说明
  body    正文：做法（Markdown）
  status  active / disabled（停用：找不到、读不到；留着，能换回来）
  agents  给哪几个 Agent 的（OpenMousse 的 Agent id；空 = 谁都用）：内核搜的时候这几个 Agent 优先，别的照样找得到
手册和规则（rules.md）一样不算记忆：不是叶子，不进修剪的合并、不进档案、不进 TREE.md 导出。

两种存法读写一样：
  Markdown 存储：笔记文件夹里的 manuals/，一份一篇 <名字>.md，属性写 name、when、about、status、agents、created_at、updated_at，正文就是做法；
    扫笔记的时候整个 manuals/ 跳过（notes._walk）。在 Obsidian 里直接改也算数（我们改的时候只换自己写的那几个属性，用户加的原样留着）
  SQLite 存储：tree.db 的 manual 表一份一行
改、改名、停用、删、换回之前整份留一版（versions.py：~/.mousse-tree/versions/manuals/<名字>/），history / restore 找得回来。
读（read：MCP 的 read_manual、命令行 show）记一笔：哪天、哪个平台读了哪份（manual_read 表，在 tree.db / index.db 里）。每周修剪列
「这个月没用上的」时，OpenMousse 服务器把它和内核自己的账（server/engine/used.py）合起来看。
找（find）用 match.py：名字、「什么时候用」比正文要紧。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from . import branches as B
from . import config as C
from . import match as M
from . import store as S
from . import versions as V

STATUSES = ("active", "disabled")
ABOUT_MAX, WHEN_MAX, WHENS_MAX, BODY_MAX = 200, 60, 8, 100_000
FILE_MAX = 512 * 1024            # Markdown 存储里一篇手册最大多少字节（再大的多半放错了地方，跳过）
FIND_LIMIT, FIND_FLOOR = 5, 0.25  # find 最多回几份、分低于多少的不回（只碰上一个词的那种不回）
KIND = "manuals"                  # 存版本的类（versions.py）
KEYS = ("name", "when", "about", "status", "agents", "created_at", "updated_at")

SCHEMA = """
CREATE TABLE IF NOT EXISTS manual (
  name TEXT PRIMARY KEY COLLATE NOCASE,
  use_when TEXT NOT NULL DEFAULT '',
  about TEXT NOT NULL DEFAULT '',
  body TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'active',
  agents TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS manual_read (
  name TEXT NOT NULL COLLATE NOCASE,
  day TEXT NOT NULL,
  platform TEXT NOT NULL,
  n INTEGER NOT NULL DEFAULT 0,
  last_at TEXT NOT NULL,
  PRIMARY KEY(name, day, platform)
);
"""


class ManualError(ValueError):
    """改不了：名字不对、重名、没有这一份、太长……str(e) 给人看（按配置的语言）。"""


def ensure(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def folder() -> Path:
    """Markdown 存储：手册放在笔记文件夹的 manuals/ 里。"""
    return C.notes_dir() / C.MANUALS


def clean_name(name) -> str:
    try:
        return B.clean_name(name)
    except ValueError:
        s = " ".join(str(name or "").split())
        raise ManualError(C.L(f"手册名「{s}」不行：1 到 {B.NAME_MAX} 个字，不能有 \\ / : * ? \" < > | # ^ [ ]",
                              f'Manual name "{s}" won\'t do: 1–{B.NAME_MAX} characters, none of \\ / : * ? " < > | # ^ [ ]')) from None


def when_list(raw) -> list[str]:
    """「什么时候用」：列表或者「、，;；|/」换行隔开的一串 → 几句，一句最多 WHEN_MAX 字，最多 WHENS_MAX 句。"""
    return [S.clip(x, WHEN_MAX) for x in M.phrases(raw)][:WHENS_MAX]


def _s(v) -> str:
    if isinstance(v, list):
        v = v[0] if v else ""
    return " ".join(v.split()) if isinstance(v, str) else ""


def _key(name: str) -> str:
    return " ".join(str(name or "").split()).casefold()


# —— 读 ——————————————————————————————————————————————————————————————————————

def _from_row(r: sqlite3.Row) -> dict:
    return {"name": r["name"], "when": when_list(r["use_when"]), "about": r["about"] or "", "body": r["body"] or "", "status": r["status"] or "active",
            "agents": B.as_list(r["agents"]), "created_at": r["created_at"], "updated_at": r["updated_at"], "path": None}


def _from_file(p: Path) -> dict | None:
    """manuals/ 里的一篇 → 一份手册；格式坏的、名字不像样的 → None（跳过）。"""
    from . import notes
    try:
        if p.stat().st_size > FILE_MAX:
            return None
        raw = p.read_text(encoding="utf8")
    except (OSError, UnicodeDecodeError):
        return None
    fm, body, _problem = notes.parse_note(raw)
    if fm is None:
        return None
    try:
        name = clean_name(_s(fm.get("name")) or p.stem)
    except ManualError:
        return None
    status = _s(fm.get("status")).lower() or "active"
    mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=C.now().tzinfo).isoformat(timespec="seconds")
    created = _s(fm.get("created_at")) or mtime
    return {"name": name, "when": when_list(fm.get("when") if fm.get("when") is not None else fm.get("when_to_use")),
            "about": S.clip(_s(fm.get("about")) or _s(fm.get("description")), ABOUT_MAX), "body": body.strip(),
            "status": status if status in STATUSES else "active", "agents": B.as_list(fm.get("agents")), "created_at": created,
            "updated_at": _s(fm.get("updated_at")) or mtime, "path": str(p)}


def _files() -> list[Path]:
    d = folder()
    if not d.is_dir() or d.is_symlink():
        return []
    return sorted(p for p in d.iterdir() if p.suffix == ".md" and not p.name.startswith(".") and not p.is_symlink() and p.is_file())


def all_manuals(conn: sqlite3.Connection, *, disabled: bool = False) -> list[dict]:
    """所有手册（按名字排）。disabled=True 连停用的一起。Markdown 存储同名的（不分大小写）只认按文件名排在前面的那篇。"""
    if C.markdown():
        out, seen = [], set()
        for p in _files():
            m = _from_file(p)
            if m is None or _key(m["name"]) in seen:
                continue
            seen.add(_key(m["name"]))
            out.append(m)
        out.sort(key=lambda m: _key(m["name"]))
    else:
        ensure(conn)
        out = [_from_row(r) for r in conn.execute("SELECT * FROM manual ORDER BY name COLLATE NOCASE")]
    return out if disabled else [m for m in out if m["status"] == "active"]


def get(conn: sqlite3.Connection, name: str, *, disabled: bool = True) -> dict | None:
    """按名字找一份（不分大小写，空白不算）；disabled=False 停用的当没有。"""
    k = _key(name)
    return next((m for m in all_manuals(conn, disabled=disabled) if _key(m["name"]) == k), None) if k else None


# —— 写 ——————————————————————————————————————————————————————————————————————

def render(m: dict, keep: str | None = None) -> str:
    """一份手册写成一篇 Markdown（Markdown 存储的文件就是它；存版本也存这个样子）。keep = 原来的属性区：用户加的属性原样留着。"""
    from . import notes
    ours = {"name": [f"name: {json.dumps(m['name'], ensure_ascii=False)}"],
            "when": ["when:", *(f"  - {json.dumps(w, ensure_ascii=False)}" for w in m["when"])] if m["when"] else ["when: []"],
            "about": [f"about: {json.dumps(m['about'], ensure_ascii=False)}"],
            "status": [f"status: {m['status']}"],
            "agents": ["agents:", *(f"  - {json.dumps(a, ensure_ascii=False)}" for a in m["agents"])] if m["agents"] else ["agents: []"],
            "created_at": [f"created_at: {json.dumps(m['created_at'])}"],
            "updated_at": [f"updated_at: {json.dumps(m['updated_at'])}"]}
    body = (m.get("body") or "").strip()
    return "\n".join(["---", *notes._merge(ours, KEYS, keep), "---"]) + "\n" + (body + "\n" if body else "")


def parse(text: str, name: str = "") -> dict | None:
    """一篇 Markdown（render 写的、版本里存的）→ 一份手册；读不出来 → None。"""
    from . import notes
    fm, body, _ = notes.parse_note(text or "")
    if fm is None:
        return None
    try:
        nm = clean_name(_s(fm.get("name")) or name)
    except ManualError:
        return None
    status = _s(fm.get("status")).lower() or "active"
    now = S.now_iso()
    return {"name": nm, "when": when_list(fm.get("when")), "about": S.clip(_s(fm.get("about")) or _s(fm.get("description")), ABOUT_MAX),
            "body": body.strip(), "status": status if status in STATUSES else "active", "agents": B.as_list(fm.get("agents")),
            "created_at": _s(fm.get("created_at")) or now, "updated_at": _s(fm.get("updated_at")) or now, "path": None}


def _check(m: dict) -> None:
    if not m["body"].strip():
        raise ManualError(C.L("手册要有正文：写清楚怎么做", "A manual needs a body: how to do it"))
    if len(m["body"]) > BODY_MAX:
        raise ManualError(C.L(f"正文太长了（最多 {BODY_MAX} 字）：拆成几份", f"The body is too long (at most {BODY_MAX} characters): split it up"))


def _save(conn: sqlite3.Connection, m: dict, old: dict | None) -> None:
    """写一份（新的或改过的）：Markdown 存储写那篇笔记（改名 = 换文件），SQLite 存储写那一行。"""
    if C.markdown():
        from . import notes
        base = C.notes_dir()
        if not base.parent.is_dir():
            raise ManualError(C.L(f"笔记文件夹的上一级不存在：{base.parent}", f"the notes folder's parent does not exist: {base.parent}"))
        keep = None
        src = Path(old["path"]) if old and old.get("path") else None
        if src is not None:
            try:
                keep = notes.front(src.read_text(encoding="utf8"))[0]
            except OSError:
                keep = None
        dst = folder() / f"{m['name']}.md"
        if src is not None and src != dst and src.name.casefold() == dst.name.casefold():
            src.rename(dst)  # 只改了大小写：先改文件名（不分大小写的文件系统上，写新的再删旧的会把新的删掉）
            src = dst
        notes.atomic_write(dst, render(m, keep))
        if src is not None and src != dst:
            src.unlink(missing_ok=True)  # 改了名字（或者文件名原来就和名字对不上）：文件名跟着名字走
        return
    ensure(conn)
    row = (m["name"], "\n".join(m["when"]), m["about"], m["body"], m["status"], json.dumps(m["agents"], ensure_ascii=False), m["created_at"],
           m["updated_at"])
    if old is not None:
        conn.execute("UPDATE manual SET name=?, use_when=?, about=?, body=?, status=?, agents=?, created_at=?, updated_at=? WHERE name=?",
                     (*row, old["name"]))
    else:
        conn.execute("INSERT INTO manual(name, use_when, about, body, status, agents, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)", row)


def add(conn: sqlite3.Connection, *, name: str, when, about: str, body: str, agents=(), actor: str = "owner") -> dict:
    """新写一份 → 那一份。重名、名字不行、没有正文抛 ManualError。"""
    with S.locked():
        nm = clean_name(name)
        if get(conn, nm) is not None:
            raise ManualError(C.L(f"已经有「{nm}」这份手册了：改它用 edit", f'There\'s already a manual called "{nm}": use edit'))
        ts = S.now_iso()
        m = {"name": nm, "when": when_list(when), "about": S.clip(about, ABOUT_MAX), "body": (body or "").strip(), "status": "active",
             "agents": B.as_list(list(agents)), "created_at": ts, "updated_at": ts, "path": None}
        _check(m)
        _save(conn, m, None)
        S.log(conn, actor, C.L(f"写了一份手册「{nm}」", f'Wrote a manual "{nm}"'))
        conn.commit()
    return get(conn, nm) or m


def edit(conn: sqlite3.Connection, name: str, *, when=None, about: str | None = None, body: str | None = None, rename: str | None = None,
         agents=None, status: str | None = None, actor: str = "owner", note: str = "edit") -> dict:
    """改一份（只改给了的那几样）：改之前整份留一版。→ {"manual": 改好的那一份, "version": 改之前那一版的号}。
    没有这一份、新名字重名、什么都没改抛 ManualError。"""
    with S.locked():
        old = get(conn, name)
        if old is None:
            raise ManualError(not_found(name))
        m = dict(old)
        if rename is not None and _key(rename) != _key(old["name"]):
            nm = clean_name(rename)
            if get(conn, nm) is not None:
                raise ManualError(C.L(f"已经有「{nm}」这份手册了", f'There\'s already a manual called "{nm}"'))
            m["name"] = nm
        elif rename is not None:
            m["name"] = clean_name(rename)  # 只改大小写、空白
        if when is not None:
            m["when"] = when_list(when)
        if about is not None:
            m["about"] = S.clip(about, ABOUT_MAX)
        if body is not None:
            m["body"] = body.strip()
            _check(m)
        if agents is not None:
            m["agents"] = B.as_list(list(agents))
        if status is not None:
            if status not in STATUSES:
                raise ManualError(C.L(f"status 只能是 {' / '.join(STATUSES)}", f"status must be one of {', '.join(STATUSES)}"))
            m["status"] = status
        if all(m[k] == old[k] for k in ("name", "when", "about", "body", "agents", "status")):
            raise ManualError(C.L("没有要改的", "Nothing to change"))
        vid = V.keep(KIND, old["name"], render(old), by=actor, note=note)
        m["updated_at"] = S.now_iso()
        _save(conn, m, old)
        if _key(m["name"]) != _key(old["name"]):
            V.move(KIND, old["name"], m["name"])
            _rename_reads(conn, old["name"], m["name"])
        what = {"disable": C.L("停用了", "Disabled"), "enable": C.L("换回了", "Re-enabled")}.get(note, C.L("改了", "Changed"))
        S.log(conn, actor, C.L(f"{what}手册「{m['name']}」（改之前存成 {vid}）", f'{what} the manual "{m["name"]}" (kept {vid} first)'))
        conn.commit()
    return {"manual": get(conn, m["name"]) or m, "version": vid}


def set_status(conn: sqlite3.Connection, name: str, status: str, actor: str = "owner") -> dict:
    """停用（disabled）/ 换回（active）：只改状态，改之前留一版。"""
    return edit(conn, name, status=status, actor=actor, note="disable" if status == "disabled" else "enable")


def remove(conn: sqlite3.Connection, name: str, actor: str = "owner") -> str:
    """删一份：删之前整份留一版（restore 找得回来）。→ 那一版的号。没有这一份抛 ManualError。"""
    with S.locked():
        old = get(conn, name)
        if old is None:
            raise ManualError(not_found(name))
        vid = V.keep(KIND, old["name"], render(old), by=actor, note="rm")
        if C.markdown():
            if old.get("path"):
                Path(old["path"]).unlink(missing_ok=True)
        else:
            conn.execute("DELETE FROM manual WHERE name=?", (old["name"],))
        S.log(conn, actor, C.L(f"删了手册「{old['name']}」（存成 {vid}，restore 找得回来）", f'Removed the manual "{old["name"]}" (kept as {vid}; restore brings it back)'))
        conn.commit()
    return vid


def history(name: str) -> list[dict]:
    return V.history(KIND, name)


def restore(conn: sqlite3.Connection, name: str, vid: str, actor: str = "owner") -> dict:
    """换回存过的某一版（现在还有这一份的话，换之前也先存一版）。→ {"manual", "version": 换之前那一版的号或 None}。"""
    text = V.read(KIND, name, vid)
    m = parse(text or "", name)
    if m is None:
        raise ManualError(C.L(f"「{name}」没有 {vid or '（没写）'} 这一版：先 history 看看", f'No version {vid or "(none given)"} of "{name}": see history first'))
    with S.locked():
        old = get(conn, name)
        saved = V.keep(KIND, old["name"], render(old), by=actor, note="restore") if old else None
        if old is None and get(conn, m["name"]) is not None:
            raise ManualError(C.L(f"已经有「{m['name']}」这份手册了", f'There\'s already a manual called "{m["name"]}"'))
        m["updated_at"] = S.now_iso()
        _check(m)
        _save(conn, m, old)
        S.log(conn, actor, C.L(f"手册「{m['name']}」换回了 {vid}", f'Restored the manual "{m["name"]}" to {vid}'))
        conn.commit()
    return {"manual": get(conn, m["name"]) or m, "version": saved}


def not_found(name: str) -> str:
    """没有这份手册（给人、给模型看的那一句）：明说没有，让调用方停下说，别凭说明自己编。"""
    s = " ".join(str(name or "").split())
    return C.L(f"没有「{s}」这份手册（或者停用了）。先取正文再动手；取不到就停下说：告诉用户没找到这份手册，不要凭那一行说明自己编。",
               f'There\'s no manual called "{s}" (or it\'s disabled). Fetch the body before you act; if you can\'t get it, stop and say so: tell '
               "the user the manual wasn't found, and don't improvise from the one-line description.")


def _rename_reads(conn: sqlite3.Connection, old: str, new: str) -> None:
    ensure(conn)
    conn.execute("UPDATE OR REPLACE manual_read SET name=? WHERE name=?", (new, old))


# —— 找、读、记一笔 ————————————————————————————————————————————————————————————

def find(conn: sqlite3.Connection, query: str, *, limit: int = FIND_LIMIT, agent: str | None = None, floor: float = FIND_FLOOR) -> list[dict]:
    """按一句话找手册（match.py）→ [{"name", "when", "about", "score"}]，分高的在前；agent 给了，它自己的那几份优先（× 1.1）。停用的不找。"""
    ms = all_manuals(conn)
    if not ms or not str(query or "").strip():
        return []
    boost = {i: 1.1 for i, m in enumerate(ms) if agent and agent in m["agents"]}
    got = M.Matcher(ms).rank([(str(query), 1.0)], boost=boost, limit=max(1, min(int(limit), 20)), floor=floor)
    return [{"name": x["entry"]["name"], "when": x["entry"]["when"], "about": x["entry"]["about"], "score": round(x["score"], 2)} for x in got]


def read(conn: sqlite3.Connection, name: str, *, platform: str) -> dict | None:
    """读一份的正文（停用的当没有）→ 那一份；读到了就记一笔（谁、哪天）。"""
    m = get(conn, name, disabled=False)
    if m is not None:
        note_read(conn, m["name"], platform)
    return m


def note_read(conn: sqlite3.Connection, name: str, platform: str) -> None:
    ensure(conn)
    ts = S.now_iso()
    conn.execute("INSERT INTO manual_read(name, day, platform, n, last_at) VALUES (?,?,?,1,?) "
                 "ON CONFLICT(name, day, platform) DO UPDATE SET n = n + 1, last_at = excluded.last_at",
                 (name, ts[:10], str(platform or "unknown")[:40], ts))
    conn.commit()


def last_reads(conn: sqlite3.Connection) -> dict[str, str]:
    """每份最后一次被读的时间（不分大小写的名字 → ISO 时间）。"""
    ensure(conn)
    return {_key(r[0]): r[1] for r in conn.execute("SELECT name, MAX(last_at) FROM manual_read GROUP BY name COLLATE NOCASE")}


# —— 指针 ——————————————————————————————————————————————————————————————————

def pointer(m: dict) -> str:
    """一份手册的四行指针（贴进 CLAUDE.md、AGENTS.md、Agent 的规矩文件）：名字、什么时候用、正文在哪、先取正文的规矩。"""
    if C.lang() == "zh":
        when = "、".join(m["when"]) or m["about"] or "（没写）"
        return f"## {m['name']}\n什么时候用：{when}\n正文：世界树 › 手册 › {m['name']}\n先取正文再动手；取不到就停下说。"
    when = "; ".join(m["when"]) or m["about"] or "(not written)"
    return (f"## {m['name']}\nWhen to use: {when}\nBody: Memory tree › Manuals › {m['name']}\n"
            "Fetch the body before you start; if you can't, stop and say so.")


def pointers(ms: list[dict]) -> str:
    """一组手册的指针：前面一段说正文去哪取（世界树的 find_manual / read_manual），再一份四行。"""
    head = C.L("# 手册（正文在世界树里）\n要照下面哪一份做事：先用世界树的 read_manual 取它的正文（不知道叫什么就用 find_manual 找），再动手。"
               "取不到就停下说，不许凭那一行说明自己编。",
               "# Manuals (the bodies live in the memory tree)\nTo do something one of these covers: first fetch its body with the memory tree's "
               "read_manual (use find_manual if you don't know the name), then start. If you can't get it, stop and say so; never improvise "
               "from the one-line description.")
    return head + "\n\n" + "\n\n".join(pointer(m) for m in ms) + "\n"
