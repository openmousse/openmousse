#!/usr/bin/env python3
"""用户的日志（服务数据库的 `journal` 表）：训练感受、对事情的想法、做过的决定、随手记录。agent 在对话里记，app「我 → 日志」和各 Agent 的「记忆」页能翻。

  python3 journal.py add --kind feeling --group <agent id> --text "深蹲第三组腿发抖" [--tags 深蹲,腿] [--context "Leg A 练后"]
  python3 journal.py add --kind thought --text "..."            # 不属于哪个 Agent 就不给 --group
  echo "很长的一段话" | python3 journal.py add --kind decision --stdin --tags 申请
  python3 journal.py list [--group <id>] [--kind feeling] [--days 14] [--limit 30]
  python3 journal.py search --q 肩 [--days 90]
  python3 journal.py delete <id>

kind：feeling（身体 / 训练 / 情绪感受）、thought（想法、观点）、decision（决定、承诺）、note（其它记录）。
删除只标 status=deleted、清空正文，保留 id 和时间；activity_log 记一行不含内容。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mousse_common import db_path, user_now  # noqa: E402

KINDS = ("feeling", "thought", "decision", "note")
SCHEMA = """CREATE TABLE IF NOT EXISTS journal (id TEXT PRIMARY KEY, ts TEXT NOT NULL, group_id TEXT, kind TEXT NOT NULL, text TEXT NOT NULL,
    tags TEXT, context TEXT, source TEXT NOT NULL DEFAULT 'chat', status TEXT NOT NULL DEFAULT 'active');
CREATE INDEX IF NOT EXISTS journal_ts ON journal(ts);
CREATE TABLE IF NOT EXISTS activity_log (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, actor TEXT NOT NULL, text TEXT NOT NULL, kind TEXT NOT NULL);"""


def connect() -> sqlite3.Connection:
    db = db_path()
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def now_iso() -> str:
    return user_now().isoformat(timespec="seconds")


def tags_json(raw: str) -> str | None:
    tags = [t.strip() for t in (raw or "").split(",") if t.strip()]
    return json.dumps(tags, ensure_ascii=False) if tags else None


def row(r: sqlite3.Row) -> dict:
    d = dict(r)
    try:
        d["tags"] = json.loads(d["tags"]) if d.get("tags") else []
    except ValueError:
        d["tags"] = [t for t in (d.get("tags") or "").split(",") if t]
    return d


def cmd_add(a: argparse.Namespace) -> None:
    text = sys.stdin.read().strip() if a.stdin else (a.text or "").strip()
    if not text:
        sys.exit("要有 --text（或 --stdin）")
    jid = uuid.uuid4().hex[:12]
    with connect() as conn:
        conn.execute("INSERT INTO journal(id, ts, group_id, kind, text, tags, context) VALUES(?,?,?,?,?,?,?)",
                     (jid, now_iso(), a.group or None, a.kind, text, tags_json(a.tags), a.context or None))
        conn.execute("INSERT INTO activity_log(ts, actor, text, kind) VALUES(?,?,?,?)",
                     (now_iso(), a.group or "main", f"记了一条日志（{a.kind}）", "edit"))
    print(json.dumps({"ok": True, "id": jid, "kind": a.kind, "group": a.group, "text": text}, ensure_ascii=False))


def cmd_list(a: argparse.Namespace) -> None:
    q, args = "SELECT * FROM journal WHERE status='active'", []
    if a.group:
        q += " AND group_id=?"; args.append(a.group)
    if a.kind:
        q += " AND kind=?"; args.append(a.kind)
    if a.days:
        q += " AND ts>=?"; args.append(_since(a.days))
    q += " ORDER BY ts DESC LIMIT ?"; args.append(a.limit)
    with connect() as conn:
        rows = [row(r) for r in conn.execute(q, args)]
    print(json.dumps(rows, ensure_ascii=False, indent=1))


def _since(days: int) -> str:
    from datetime import timedelta
    return (user_now() - timedelta(days=days)).isoformat(timespec="seconds")


def cmd_search(a: argparse.Namespace) -> None:
    with connect() as conn:
        rows = [row(r) for r in conn.execute(
            "SELECT * FROM journal WHERE status='active' AND ts>=? AND (text LIKE ? OR tags LIKE ? OR context LIKE ?) ORDER BY ts DESC LIMIT 50",
            (_since(a.days), f"%{a.q}%", f"%{a.q}%", f"%{a.q}%"))]
    print(json.dumps(rows, ensure_ascii=False, indent=1))


def cmd_delete(a: argparse.Namespace) -> None:
    with connect() as conn:
        n = conn.execute("UPDATE journal SET status='deleted', text='', tags=NULL, context=NULL WHERE id=? AND status='active'", (a.id,)).rowcount
        if n:
            conn.execute("INSERT INTO activity_log(ts, actor, text, kind) VALUES(?,?,?,?)", (now_iso(), "main", "删了一条日志", "deleted"))
    print(json.dumps({"ok": bool(n), "id": a.id}, ensure_ascii=False))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    q = sp.add_parser("add"); q.add_argument("--kind", choices=KINDS, default="note"); q.add_argument("--group"); q.add_argument("--text")
    q.add_argument("--stdin", action="store_true"); q.add_argument("--tags", default=""); q.add_argument("--context", default=""); q.set_defaults(fn=cmd_add)
    q = sp.add_parser("list"); q.add_argument("--group"); q.add_argument("--kind", choices=KINDS); q.add_argument("--days", type=int, default=14)
    q.add_argument("--limit", type=int, default=30); q.set_defaults(fn=cmd_list)
    q = sp.add_parser("search"); q.add_argument("--q", required=True); q.add_argument("--days", type=int, default=90); q.set_defaults(fn=cmd_search)
    q = sp.add_parser("delete"); q.add_argument("id"); q.set_defaults(fn=cmd_delete)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
