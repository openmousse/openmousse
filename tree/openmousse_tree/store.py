"""存储：SQLite 一张 `tree` 表 + FTS5 trigram 全文索引（中英文都能搜）。

一条记忆 = 一句话 + kind + tags + source（哪个平台写的）+ observed_at（事情发生的日期）+ status。
状态：active / pending（等主人确认）/ superseded（被新条目取代）/ retracted（已遗忘，正文清空只留骨架）。
主人的档案（USER.md 的要点行）只读进树，source=profile，不在这里改。
"""
from __future__ import annotations

import hashlib
import sqlite3
import uuid
from pathlib import Path

from . import config as C

KINDS = ("fact", "preference", "decision", "event", "profile")
STATUSES = ("active", "pending", "superseded", "retracted")

SCHEMA = """
CREATE TABLE IF NOT EXISTS tree (
  id TEXT PRIMARY KEY,
  text TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'fact',
  tags TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  supersedes TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS tree_status ON tree(status, observed_at);
CREATE VIRTUAL TABLE IF NOT EXISTS tree_fts USING fts5(text, tags, content='tree', content_rowid='rowid', tokenize='trigram');
CREATE TRIGGER IF NOT EXISTS tree_ai AFTER INSERT ON tree BEGIN
  INSERT INTO tree_fts(rowid, text, tags) VALUES (new.rowid, new.text, new.tags);
END;
CREATE TRIGGER IF NOT EXISTS tree_ad AFTER DELETE ON tree BEGIN
  INSERT INTO tree_fts(tree_fts, rowid, text, tags) VALUES ('delete', old.rowid, old.text, old.tags);
END;
CREATE TRIGGER IF NOT EXISTS tree_au AFTER UPDATE ON tree BEGIN
  INSERT INTO tree_fts(tree_fts, rowid, text, tags) VALUES ('delete', old.rowid, old.text, old.tags);
  INSERT INTO tree_fts(rowid, text, tags) VALUES (new.rowid, new.text, new.tags);
END;
CREATE TABLE IF NOT EXISTS activity (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, actor TEXT NOT NULL, text TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""


def now_iso() -> str:
    return C.now().isoformat(timespec="seconds")


def today() -> str:
    return C.now().date().isoformat()


def connect() -> sqlite3.Connection:
    C.HOME.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(C.DB, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def log(conn: sqlite3.Connection, actor: str, text: str) -> None:
    conn.execute("INSERT INTO activity(ts, actor, text) VALUES (?,?,?)", (now_iso(), actor, text))


# ---------- 写 ----------

def add(conn: sqlite3.Connection, *, text: str, source: str, kind: str = "fact", tags: str = "",
        observed_at: str | None = None, status: str = "active", supersedes: str | None = None) -> dict:
    text = " ".join(text.split())
    if not text:
        raise ValueError("empty text")
    kind = kind if kind in KINDS[:-1] else "fact"
    status = status if status in STATUSES else "active"
    dup = conn.execute("SELECT id FROM tree WHERE text = ? AND status IN ('active','pending')", (text,)).fetchone()
    if dup:
        return {"id": dup["id"], "duplicate": True}
    mid = uuid.uuid4().hex[:12]
    ts = now_iso()
    conn.execute(
        "INSERT INTO tree(id, text, kind, tags, source, observed_at, status, supersedes, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (mid, text, kind, tags.strip(), source, observed_at or today(), status, supersedes or None, ts, ts),
    )
    if supersedes:
        conn.execute("UPDATE tree SET status='superseded', updated_at=? WHERE id=? AND status IN ('active','pending')", (ts, supersedes))
    log(conn, source, f"新增 1 条（{kind}，{status}）")
    conn.commit()
    return {"id": mid, "duplicate": False}


def set_status(conn: sqlite3.Connection, mid: str, status: str, actor: str) -> bool:
    row = conn.execute("SELECT id, source FROM tree WHERE id=?", (mid,)).fetchone()
    if not row or row["source"] == "profile" or status not in STATUSES:
        return False
    ts = now_iso()
    if status == "retracted":
        conn.execute("UPDATE tree SET status='retracted', text='', tags='', updated_at=? WHERE id=?", (ts, mid))
        log(conn, actor, "遗忘 1 条")
    else:
        conn.execute("UPDATE tree SET status=?, updated_at=? WHERE id=?", (status, ts, mid))
        log(conn, actor, f"1 条改为 {status}")
    conn.commit()
    return True


def edit(conn: sqlite3.Connection, mid: str, text: str, actor: str) -> bool:
    text = " ".join(text.split())
    row = conn.execute("SELECT id, source FROM tree WHERE id=?", (mid,)).fetchone()
    if not row or row["source"] == "profile" or not text:
        return False
    conn.execute("UPDATE tree SET text=?, updated_at=? WHERE id=?", (text, now_iso(), mid))
    log(conn, actor, "改写 1 条")
    conn.commit()
    return True


# ---------- 档案只读进树 ----------

def profile_path() -> Path:
    return Path(C.load()["profile_path"]).expanduser()


def profile_text() -> str:
    try:
        return profile_path().read_text(encoding="utf8")
    except OSError:
        return ""


def save_profile(text: str) -> None:
    p = profile_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf8")


def sync_profile(conn: sqlite3.Connection) -> int:
    """把档案里 `- ` 开头的要点行按 `## ` 小节读进树。文件没变就不动。"""
    raw = profile_text()
    digest = hashlib.sha1(raw.encode()).hexdigest()
    old = conn.execute("SELECT v FROM meta WHERE k='profile_digest'").fetchone()
    if old and old["v"] == digest:
        return 0
    conn.execute("DELETE FROM tree WHERE source='profile'")
    section, n, ts = "", 0, now_iso()
    for line in raw.splitlines():
        if line.startswith("## "):
            section = line[3:].strip()
        elif line.startswith("- ") and section:
            text = line[2:].strip()
            pid = "p" + hashlib.sha1((section + text).encode()).hexdigest()[:11]
            conn.execute(
                "INSERT OR REPLACE INTO tree(id, text, kind, tags, source, observed_at, status, supersedes, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (pid, text, "profile", section, "profile", today(), "active", None, ts, ts),
            )
            n += 1
    conn.execute("INSERT OR REPLACE INTO meta(k, v) VALUES ('profile_digest', ?)", (digest,))
    conn.commit()
    return n


# ---------- 读 ----------

def _fts_query(q: str) -> str:
    words = [w for w in q.replace('"', " ").split() if len(w) >= 3]
    return " OR ".join(f'"{w}"' for w in words)


def recall(conn: sqlite3.Connection, q: str, limit: int = 8) -> list[dict]:
    rows: list[sqlite3.Row] = []
    fq = _fts_query(q)
    if fq:
        rows = conn.execute(
            "SELECT t.* FROM tree_fts JOIN tree t ON t.rowid = tree_fts.rowid "
            "WHERE tree_fts MATCH ? AND t.status IN ('active','pending') ORDER BY bm25(tree_fts) LIMIT ?",
            (fq, limit),
        ).fetchall()
    if len(rows) < limit:
        seen = {r["id"] for r in rows}
        for w in q.split():
            for r in conn.execute(
                "SELECT * FROM tree WHERE (text LIKE ? OR tags LIKE ?) AND status IN ('active','pending') ORDER BY observed_at DESC LIMIT ?",
                (f"%{w}%", f"%{w}%", limit),
            ):
                if r["id"] not in seen:
                    rows.append(r)
                    seen.add(r["id"])
            if len(rows) >= limit:
                break
    return [dict(r) for r in rows[:limit]]


def recent(conn: sqlite3.Connection, days: int = 7, limit: int = 30) -> list[dict]:
    since = C.now().date().toordinal() - days
    out = []
    for r in conn.execute(
        "SELECT * FROM tree WHERE source != 'profile' AND status IN ('active','pending') ORDER BY created_at DESC LIMIT ?", (limit,)
    ):
        try:
            d = C.now().date().fromisoformat(r["observed_at"]).toordinal()
        except ValueError:
            d = since
        if d >= since:
            out.append(dict(r))
    return out


def list_all(conn: sqlite3.Connection, status: str | None = None, source: str | None = None, limit: int = 500) -> list[dict]:
    sql, args = "SELECT * FROM tree WHERE source != 'profile'", []
    if status:
        sql += " AND status=?"
        args.append(status)
    else:
        sql += " AND status IN ('active','pending')"
    if source:
        sql += " AND source=?"
        args.append(source)
    sql += " ORDER BY created_at DESC LIMIT ?"
    args.append(limit)
    return [dict(r) for r in conn.execute(sql, args)]


def stats(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT source, status, COUNT(*) n FROM tree GROUP BY source, status ORDER BY source, status")]


def fmt(rows: list[dict]) -> str:
    """recall / recent 工具和命令行的输出（给模型读），按配置的语言。"""
    if not rows:
        return C.L("（没有相关记忆）", "(no matching memories)")
    zh = C.lang() == "zh"
    lines = []
    for r in rows:
        flag = (" [待确认]" if zh else " [pending]") if r["status"] == "pending" else ""
        tags = f" #{r['tags']}" if r.get("tags") else ""
        meta = f"（{r['source']}，{r['observed_at']}{flag}）" if zh else f"({r['source']}, {r['observed_at']}{flag})"
        lines.append(f"- [{r['id']}] {r['text']}{tags} {meta}")
    return "\n".join(lines)


# ---------- 导出给 OpenClaw 各 agent 检索 ----------

def export(conn: sqlite3.Connection) -> Path:
    path = Path(C.load()["export_path"]).expanduser()
    rows = conn.execute(
        "SELECT * FROM tree WHERE source != 'profile' AND status IN ('active','pending') ORDER BY observed_at DESC, created_at DESC"
    ).fetchall()
    path.parent.mkdir(parents=True, exist_ok=True)
    zh = C.lang() == "zh"  # 这份导出给 OpenClaw 的 agent 检索着读，跟配置的语言
    if zh:
        out = ["# 世界树 TREE.md（mousse-tree 自动导出，勿手改）", "",
               f"> 各 AI 平台共享的记忆，{len(rows)} 条，导出于 {now_iso()}。来源标在方括号里。", ""]
    else:
        out = ["# Memory tree TREE.md (exported automatically by mousse-tree, do not edit by hand)", "",
               f"> Memories shared by all AI platforms: {len(rows)} entries, exported at {now_iso()}. The source is in square brackets.", ""]
    by_kind: dict[str, list] = {}
    for r in rows:
        by_kind.setdefault(r["kind"], []).append(r)
    for kind in ("preference", "decision", "fact", "event"):
        if kind in by_kind:
            out.append(f"## {kind}")
            for r in by_kind[kind]:
                flag = ("（待确认）" if zh else " (pending)") if r["status"] == "pending" else ""
                tags = f" #{r['tags']}" if r["tags"] else ""
                out.append(f"- {r['text']}{tags} [{r['source']} {r['observed_at']}]{flag}")
            out.append("")
    path.write_text("\n".join(out), encoding="utf8")
    return path
