"""世界树的叶子进记忆检索（2026-10-16）：配了向量（engine.recall.embeddings）才有，和记忆文件、对话一起按意思和关键词排。

读哪一份：sources.py 选中的那一份，读法和 engine/context.py 的 tree_part 一样 ——
  工作区里自己写的 scripts/memory_tree.py，或开源包 openmousse_tree（treelib.py 包好的）：connect(sync=False) 只读它的索引（世界树服务
  自己每几秒刷一次，这里不抢锁、不重建、不导出），tree 表里当前的叶子（active / pending；档案要点不算：档案文件本身就在共享资料里），
  branch_tree 的枝。
  读不了（模块里没有这些、库打不开）：退回读它的 TREE.md 导出（server.json engine.recall.tree_md 写路径；没写就用开源包配置里的
  export_path）。导出里一行一片、只有已确认的，没有 id 和枝：id 用字的指纹，挂在主干上 —— 只有主对话搜得到。
  平时读得了的那一份这会儿读不了（库正在重建……）：索引里的旧叶子先留着，不换成没有枝的导出（不然功能 Agent 的叶子一会儿没了、
  一会儿又回来，向量也跟着重算）。真的换了一份（工作区的脚本 ↔ 开源包 ↔ 导出）：旧的清掉，字一样的叶子接着用原来的向量。
一片叶子一段：sources.root = tree、path = 叶子 id、day = observed_at（事情发生的那天）；leaves 表记它在哪一枝、谁写的、什么类、确认没有，
  还有它的出处（原话、来自哪个平台的哪次对话、哪条消息，2026-10-17）。信几分（13d，2026-10-19）按它算：用户自己写的、带用户原话的
  → quoted，别的 → inferred（tier_of / engine/memgate.py 的 tier）；搜出来、memory_get 读到的都写上这一档。
  这一段的字 = 叶子的字 + 标签 +「用得上的时候」那几句（cues，平时会说的话）：关键词和向量两路都按它搜，说法一换也找得回来。
  内容指纹（字、标签、类、状态、枝、来源、日期，有 cues / 原话的再加上它们）没变的不动；字、标签、cues 没变的（只是挪了枝、确认了）向量接着用。
范围（照 tree_part 的规矩）：主对话看全部；别的 Agent 只看 agents 写了它的那几枝（连小枝），主干上的不给；在沙箱里干活的 Agent 一片都没有。
  每次同步把枝的样子存进 recall.db 的 meta（tree_branches），搜和读原文的时候按它算。
engine.recall.tree = false：不收世界树（和关掉 memory_search 顺手查世界树是同一个开关）。
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from config import settings
from i18n import L, LS

ROOT = "tree"
REF = "tree:"
MODULES = ("package", "script")  # 从模块读的那两种（导出是 export:<路径>）
PROV = ("quote", "platform", "thread", "message")  # 叶子的出处（世界树的 tree 表有这几列才读：开源包 0.4、工作区那份 10/17 起）


@dataclass
class Leaf:
    id: str
    text: str
    kind: str = "fact"
    tags: str = ""
    source: str = ""
    status: str = "active"
    branch: str | None = None
    observed_at: str = ""
    created_at: str = ""
    trust: float | None = None  # 13d 的「信几分」：世界树的 tree 表有 trust 列就读进来（乘数，1 = 照常），排序时盖过 semantic.trust 的几档
    cues: tuple[str, ...] = ()  # 「用得上的时候」：几句平时会说的话（进这一段的字）
    said: tuple[str, str, str, str] = ("", "", "", "")  # 出处：原话、平台、对话、消息（进 leaves 表）

    def body(self) -> str:
        """这一段的字（索引、显示、算向量用的都是它）：叶子的字，后面带上标签和「用得上的时候」。"""
        out = self.text + (f" #{self.tags}" if self.tags else "")
        return out + (LS("（用得上：" + "；".join(self.cues) + "）", " (useful when: " + "; ".join(self.cues) + ")") if self.cues else "")

    def words(self) -> str:
        """进关键词索引的正文：叶子的字和「用得上的时候」（标签另放一栏）。"""
        return " ".join((self.text, *self.cues))

    def fingerprint(self) -> str:
        parts: list = [self.text, self.tags, self.kind, self.status, self.branch, self.source, self.observed_at, self.trust]
        if self.cues or any(self.said):  # 没有 cues、没有出处的老叶子指纹不变：不用为这次升级重建
            parts += [list(self.cues), list(self.said)]
        return hashlib.sha1(json.dumps(parts, ensure_ascii=False).encode(), usedforsecurity=False).hexdigest()


@dataclass
class Snapshot:
    origin: str                 # package（开源包）/ script（工作区的 memory_tree.py 脚本）/ export:<路径>（TREE.md）
    leaves: list[Leaf]
    branches: list[dict]        # [{"name", "parent", "agents": [...]}]（先序）
    trunk: str
    note: str | None = None     # 退回读导出时：为什么
    read_at: float = 0.0        # 什么时候读的（两次同步同时跑：旧的那一份别盖过新的）


def wanted() -> bool:
    from . import recall
    return recall.config().get("tree") is not False


def available() -> bool:
    """接了世界树（sources.py 选中了一份），或者有 TREE.md 导出可读。"""
    import sources
    if sources.memory_tree is not None:
        return True
    p = export_path(None)
    return p is not None and p.is_file()


def _day(v) -> str | None:
    s = str(v or "")[:10]
    try:
        return date.fromisoformat(s).isoformat()
    except ValueError:
        return None


def _agents(v) -> list[str]:
    if isinstance(v, list | tuple):
        return [str(a).strip() for a in v if str(a).strip()]
    return [a.strip() for a in str(v or "").split(",") if a.strip()]


def _trust(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if 0 < x <= 10 else None


def _cues(v) -> tuple[str, ...]:
    return tuple(c for c in (" ".join(x.split()) for x in str(v or "").split("\n")) if c)[:5]


def _from_module(mt) -> Snapshot:
    with contextlib.closing(mt.connect(sync=False)) as conn:
        branches = mt.branch_tree(conn) if callable(getattr(mt, "branch_tree", None)) else []
        col = "branch" if getattr(mt, "HAS_BRANCHES", True) else "NULL AS branch"
        have = {r[1] for r in conn.execute("PRAGMA table_info(tree)")}
        trust = "trust" if "trust" in have else "NULL AS trust"  # 13d 以后才有
        more = "".join(f", {k}" if k in have else f", '' AS {k}" for k in ("cues", *PROV))  # 出处和 cues（10/17 起才有）
        rows = conn.execute(f"SELECT id, text, kind, tags, source, status, {col}, observed_at, created_at, {trust}{more} FROM tree "  # noqa: S608 — 列名是常量
                            "WHERE source != 'profile' AND status IN ('active','pending') AND text != ''").fetchall()
    leaves = [Leaf(str(r[0]), " ".join(str(r[1] or "").split()), str(r[2] or "fact"), str(r[3] or "").strip(), str(r[4] or ""),
                   str(r[5] or "active"), str(r[6]) if r[6] else None, str(r[7] or ""), str(r[8] or ""), _trust(r[9]), _cues(r[10]),
                   tuple(str(x or "") for x in r[11:15])) for r in rows]
    out = [{"name": str(b["name"]), "parent": str(b.get("parent") or ""), "agents": _agents(b.get("agents"))} for b in branches]
    return Snapshot("package" if getattr(mt, "PACKAGE", False) else "script", leaves, out, str(getattr(mt, "TRUNK", "") or ""))


# TREE.md 的一行：- 字 #标签 [来源 日期]（待确认）（用得上：a；b）—— openmousse_tree.store.export 的写法（工作区那份脚本导出的也是这个样子）
_LINE = re.compile(r"^- (?P<text>.+?)(?: #(?P<tags>[^\s\[\]]+))? \[(?P<source>[^\]\s]+) (?P<day>\d{4}-\d{2}-\d{2})\]"
                   r"(?P<pending>（待确认）| \(pending\))?(?:（用得上：(?P<zh>[^）]*)）| \(useful when: (?P<en>[^)]*)\))?\s*$")
_KINDS = ("preference", "decision", "fact", "event")


def export_path(mt) -> Path | None:
    from . import recall
    p = recall.config().get("tree_md")
    if p:
        return Path(str(p)).expanduser()
    if mt is not None and getattr(mt, "PACKAGE", False):
        with contextlib.suppress(Exception):  # noqa: BLE001 — 配置读不了：就是没有导出
            return Path(mt.load_config()["export_path"]).expanduser()
    return None


def _from_export(path: Path, note: str | None) -> Snapshot:
    kind = "fact"
    leaves: list[Leaf] = []
    for line in path.read_text(encoding="utf8", errors="replace").splitlines():
        if line.startswith("## "):
            k = line[3:].strip().lower()
            kind = k if k in _KINDS else "fact"
            continue
        m = _LINE.match(line.strip())
        if not m:
            continue
        text = " ".join(m.group("text").split())
        lid = "md" + hashlib.sha1(text.encode(), usedforsecurity=False).hexdigest()[:10]
        cues = tuple(c.strip() for c in re.split(r"[；;]", m.group("zh") or m.group("en") or "") if c.strip())
        leaves.append(Leaf(lid, text, kind, (m.group("tags") or "").strip(), m.group("source"), "pending" if m.group("pending") else "active",
                           None, m.group("day"), "", cues=cues))
    return Snapshot(f"export:{path}", leaves, [], "", note)


def read() -> tuple[Snapshot | None, str | None]:
    """现在的世界树 → (快照, 读不了的原因)。没接世界树：(None, None)；接了但读不了：(None, 原因)，索引里的旧叶子先留着。"""
    import sources
    mt = sources.memory_tree
    note = None
    at = time.time()
    if mt is not None and callable(getattr(mt, "connect", None)):
        try:
            snap = _from_module(mt)
            snap.read_at = at
            return snap, None
        except Exception as e:  # noqa: BLE001 — 工作区的脚本、开源包的库都可能出各种错：退回读导出
            note = f"{type(e).__name__}: {str(e)[:160]}"
    path = export_path(mt)
    if path is not None and path.is_file():
        try:
            snap = _from_export(path, note)
            snap.read_at = at
            return snap, None
        except OSError as e:
            note = f"{path}: {e.strerror}"
    if mt is None and path is None:
        return None, None
    return None, note or L("世界树读不了，也没有 TREE.md 导出", "The memory tree can't be read and there's no TREE.md export")


# —— 进索引（recall._sync 在写锁里调）——————————————————————————————————————————————

def apply(conn: sqlite3.Connection, snap: Snapshot, stats: dict) -> None:
    from . import recall, semantic
    if snap.read_at and 0 < float(recall._meta(conn, "tree_read_at") or 0) - snap.read_at < 60:
        return  # 同时跑的另一次同步已经放进来一份更新的（差得更多 = 钟被往回拨了，照常放）
    prev = conn.execute("SELECT path FROM roots WHERE key=?", (ROOT,)).fetchone()
    if prev and prev[0] in MODULES and snap.origin.startswith("export:") and snap.note:
        note_error(conn, snap.note)  # 平时读得了的那一份这会儿读不了：旧的叶子先留着（文件头）
        return
    carried: dict = {}
    if prev and prev[0] != snap.origin:  # 真的换了一份世界树：旧的整个清掉，字一样的叶子接着用原来的向量
        for (sid,) in conn.execute("SELECT id FROM sources WHERE root=?", (ROOT,)).fetchall():
            carried.update(semantic.stash(conn, sid))
        stats["removed"] += recall._drop_root(conn, ROOT)
    conn.execute("INSERT OR REPLACE INTO roots(key, path) VALUES(?,?)", (ROOT, snap.origin))
    known = {r["path"]: r for r in conn.execute("SELECT id, path, sha FROM sources WHERE root=?", (ROOT,))}
    seen: set[str] = set()
    for leaf in snap.leaves:
        if leaf.id in seen:
            continue
        seen.add(leaf.id)
        fp = leaf.fingerprint()
        k = known.get(leaf.id)
        if k is not None and k["sha"] == fp:
            continue
        keep = carried
        if k is not None:
            keep = {**carried, **semantic.stash(conn, k["id"])}
            recall._drop_source(conn, k["id"])
        body = leaf.body()
        sid = conn.execute("INSERT INTO sources(root, path, thread, day, ts, role, size, sha) VALUES(?,?,?,?,?,?,?,?)",
                           (ROOT, leaf.id, None, _day(leaf.observed_at), leaf.created_at or leaf.observed_at or None, None, len(body), fp)).lastrowid
        conn.execute("INSERT INTO leaves(source, branch, author, kind, status, trust, quote, platform, thread, message) VALUES(?,?,?,?,?,?,?,?,?,?)",
                     (sid, leaf.branch, leaf.source, leaf.kind, leaf.status, leaf.trust, *leaf.said))
        cid = conn.execute("INSERT INTO chunks(source, start_line, end_line, heading, text) VALUES(?,1,1,'',?)", (sid, body)).lastrowid
        conn.execute("INSERT INTO chunks_fts(rowid, body, title) VALUES(?,?,?)", (cid, recall.segment(leaf.words()), recall.segment(leaf.tags.replace(",", " "))))
        semantic.reuse(conn, cid, sid, semantic.embed_input(ROOT, leaf.id, "", body), keep)
        stats["leaves"] = stats.get("leaves", 0) + 1
        stats["chunks"] += 1
    for lid, k in known.items():
        if lid not in seen:
            recall._drop_source(conn, k["id"])
            stats["removed"] += 1
    recall._set_meta(conn, "tree_branches", json.dumps(snap.branches, ensure_ascii=False))
    recall._set_meta(conn, "tree_trunk", snap.trunk)
    recall._set_meta(conn, "tree_note", snap.note or "")
    recall._set_meta(conn, "tree_read_at", repr(snap.read_at))


def note_error(conn: sqlite3.Connection, why: str) -> None:
    from . import recall
    recall._set_meta(conn, "tree_note", why)


# —— 范围 ————————————————————————————————————————————————————————————————————

def branches(conn: sqlite3.Connection) -> tuple[list[dict], str]:
    from . import recall
    try:
        bs = json.loads(recall._meta(conn, "tree_branches") or "[]")
    except ValueError:
        bs = []
    return (bs if isinstance(bs, list) else []), recall._meta(conn, "tree_trunk") or ""


def visible(conn: sqlite3.Connection, agent: str) -> list[str] | None:
    """这个 Agent 看得到哪几枝的叶子：None = 全部（主对话）；[] = 一片都没有；[枝名…]。照 tree_part：agents 写了它的枝连小枝。"""
    if agent == "main":
        return None
    from . import sandbox
    if sandbox.confined(agent):
        return []
    bs, _ = branches(conn)
    own = {b["name"] for b in bs if isinstance(b, dict) and agent in _agents(b.get("agents"))}
    kids: dict[str, list[str]] = {}
    for b in bs:
        if isinstance(b, dict):
            kids.setdefault(str(b.get("parent") or ""), []).append(str(b["name"]))
    mine, stack = set(), list(own)
    while stack:
        n = stack.pop()
        if n not in mine:
            mine.add(n)
            stack += kids.get(n, [])
    return sorted(mine)


def scope_sql(conn: sqlite3.Connection, agent: str, params: list) -> str | None:
    """_scope_sql 里世界树的那一块（params 跟着加）；一片都看不到 → None。"""
    vis = visible(conn, agent)
    if vis is None:
        return f"s.root = '{ROOT}'"
    if not vis:
        return None
    params.extend(vis)
    return f"(s.root = '{ROOT}' AND s.id IN (SELECT source FROM leaves WHERE branch IN ({','.join('?' * len(vis))})))"


def meta_of(conn: sqlite3.Connection, sids: list[int]) -> dict[int, sqlite3.Row]:
    out: dict[int, sqlite3.Row] = {}
    for i in range(0, len(sids), 500):
        part = sids[i:i + 500]
        for r in conn.execute(f"SELECT source, branch, author, kind, status, trust, quote, platform FROM leaves WHERE source IN ({','.join('?' * len(part))})", part):
            out[r["source"]] = r
    return out


def branch_label(branch: str | None, conn_branches: list[dict], trunk: str) -> str:
    names = {b.get("name") for b in conn_branches if isinstance(b, dict)}
    return branch if branch and branch in names else (trunk or L("档案", "Profile"))


# —— 谁写的 ——————————————————————————————————————————————————————————————————

def _memtree():
    try:
        import memtree
        return memtree
    except Exception:  # noqa: BLE001 — 命令行里导入不了服务器的模块：只认最基本的
        return None


def owner_sources() -> frozenset[str]:
    mt = _memtree()
    return frozenset(getattr(mt, "OWNER_SOURCES", ("owner",))) if mt else frozenset({"owner"})


def author_label(source: str, names: dict[str, str]) -> str:
    """叶子是谁记的（给模型看）：AI 平台的名字、用户自己、每周修剪、哪个 Agent。"""
    mt = _memtree()
    if mt is None:
        return source
    if source in mt.PLATFORMS:
        return mt.PLATFORMS[source]
    if source in owner_sources():
        return L("用户自己", "the user")
    if source == "prune":
        return L("每周修剪", "weekly pruning")
    aid = mt.agent_of(source, names)
    if aid:
        return settings.app_name if aid == "main" else names.get(aid) or aid
    return source


def tier_of(author: str, quote: str, platform: str) -> str:
    """信几分（13d，engine/memgate.py）：用户自己写的、带原话的 → quoted；别的 → inferred（档案要点不进这张表）。"""
    from . import memgate
    return memgate.tier(author, quote, platform, owner_sources=owner_sources())


# —— 读原文（memory_get tree:<id>）————————————————————————————————————————————————

def get(ref: str, agent: str) -> str:
    from . import recall
    lid = ref[len(REF):].strip()
    try:
        with recall._reader() as conn:
            row = conn.execute("SELECT s.id, s.day, c.text, l.branch, l.author, l.kind, l.status, l.quote, l.platform FROM sources s "
                               "JOIN chunks c ON c.source = s.id LEFT JOIN leaves l ON l.source = s.id WHERE s.root=? AND s.path=?",
                               (ROOT, lid)).fetchone()
            vis = visible(conn, agent) if row else []
            bs, trunk = branches(conn)
    except sqlite3.Error:
        row = None
    if row is None or (vis is not None and row["branch"] not in vis):
        raise recall.RecallError(L(f"读不了 {ref}：世界树里没有这片叶子，或者不在你的范围里", f"Can't read {ref}: no such leaf, or it isn't in your scope"))
    bits = [f"{REF}{lid}", L(f"世界树「{branch_label(row['branch'], bs, trunk)}」", f"memory tree, branch {branch_label(row['branch'], bs, trunk)}")]
    if row["day"]:
        bits.append(row["day"])
    if row["author"]:
        bits.append(L(f"{author_label(row['author'], recall._names())} 记的", f"recorded by {author_label(row['author'], recall._names())}"))
    from . import memgate
    bits.append(L(f"信几分：{memgate.tier_label(tier_of(row['author'] or '', row['quote'] or '', row['platform'] or ''))}",
                  f"trust: {memgate.tier_label(tier_of(row['author'] or '', row['quote'] or '', row['platform'] or ''))}"))
    if row["status"] == "pending":
        bits.append(L("还没确认", "not confirmed yet"))
    said = ("\n" + L(f"原话：「{row['quote']}」", f'In their words: "{row["quote"]}"')) if row["quote"] else ""  # 出处：用户当时那句
    return " · ".join(bits) + "\n" + row["text"] + said + "\n" + L("（世界树的一片叶子：用户在各个 AI 那里说过的、关于自己的事。）",
                                                               "(A leaf of the memory tree: something the user told one of their AIs about themselves.)") \
        + "\n" + memgate.tier_note()
