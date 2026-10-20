"""渠道层的表（mousse.db，和 app 的对话在同一个库，同一把锁 chat._lock）。

  channel_inbox   进来的：先落库再确认（Telegram 的 offset、邮件的水位和这一批写在同一个事务里，写进去了才往前挪），
                  按渠道自己的 id 去重（UNIQUE channel, account, ext_id）。状态：pending 等着 → working 在处理 → done 处理完 /
                  waiting 主人的话交给了对话、等回复回到渠道 / skipped 不理（why 写原因）/ dead 试够次数了。按聊天（lane）串行，
                  重启后 working 的回到 pending 接着处理（at-least-once：主人那句交给对话之前记了 ref，不会再交一遍）。
  channel_outbox  出去的：排队、按聊天的先后发、失败按退避重试（429 照 retry_after）；试够次数进死信（dead，能看、能重发）。
  channel_people  人：主人（role owner：认主人的码记下的）和联系人（role contact：档位、绑哪个 Agent、active / blocked）。
  channel_pairs   配对：陌生人的码（一小时过期）、待批、批了 / 拒了 / 过期。
  channel_claims  认主人的码（只存 sha256，一次有效，会过期）。
  channel_history 群里没 @ 机器人的话（每个群 / 话题留最近几十条，被 @ 时当资料带上）。
  channel_sent    发出去的消息：渠道上的 id → 是什么（卡片 → 收件箱 id、回复、配对……）：按钮回调核对、邮件串线程用。
  channel_state   每个账号的小状态：offset / 水位、最后一次收到、最后一个错。
时间：显示用的 *_at 是带时区的 ISO（chat.now_iso），排队和数窗口用的 t / next_at 是 Unix 秒（不同时区偏移按字符串比会错）。
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from collections.abc import Iterable

from chat import _lock, db, now_iso

from .base import Inbound, Outbound

_ready: set[str] = set()
KEEP_DONE_DAYS = 30        # 处理完的进来的、发完的出去的留多久（去重靠它们）
KEEP_HISTORY = 200         # 每个群最多留几条（配置的 history 是给模型看几条）


def sdb() -> sqlite3.Connection:
    conn = db()
    from config import settings
    if str(settings.db) in _ready:
        return conn
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS channel_inbox (id INTEGER PRIMARY KEY AUTOINCREMENT, channel TEXT NOT NULL, account TEXT NOT NULL,
        ext_id TEXT NOT NULL, lane TEXT NOT NULL, sender TEXT NOT NULL DEFAULT '', payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
        who TEXT NOT NULL DEFAULT '', why TEXT NOT NULL DEFAULT '', ref TEXT NOT NULL DEFAULT '', tries INTEGER NOT NULL DEFAULT 0,
        next_at REAL NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '', t REAL NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        UNIQUE(channel, account, ext_id));
    CREATE INDEX IF NOT EXISTS channel_inbox_lane ON channel_inbox(channel, account, status, lane, id);
    CREATE INDEX IF NOT EXISTS channel_inbox_sender ON channel_inbox(channel, sender, t);
    CREATE TABLE IF NOT EXISTS channel_outbox (id INTEGER PRIMARY KEY AUTOINCREMENT, channel TEXT NOT NULL, account TEXT NOT NULL,
        chat TEXT NOT NULL, topic TEXT NOT NULL DEFAULT '', body TEXT NOT NULL, why TEXT NOT NULL DEFAULT '', ref TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'queued', tries INTEGER NOT NULL DEFAULT 0, next_at REAL NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '',
        sent_ids TEXT NOT NULL DEFAULT '[]', t REAL NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS channel_outbox_due ON channel_outbox(channel, account, status, id);
    CREATE INDEX IF NOT EXISTS channel_outbox_chat ON channel_outbox(channel, account, chat, t);
    CREATE TABLE IF NOT EXISTS channel_people (id TEXT PRIMARY KEY, channel TEXT NOT NULL, sender TEXT NOT NULL, role TEXT NOT NULL,
        name TEXT NOT NULL DEFAULT '', tier TEXT NOT NULL DEFAULT 'friend', agent TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'active',
        via TEXT NOT NULL DEFAULT '', account TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        UNIQUE(channel, sender));
    CREATE TABLE IF NOT EXISTS channel_pairs (id TEXT PRIMARY KEY, channel TEXT NOT NULL, account TEXT NOT NULL, sender TEXT NOT NULL,
        chat TEXT NOT NULL, name TEXT NOT NULL DEFAULT '', code TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', inbox_id TEXT,
        person TEXT, lang TEXT NOT NULL DEFAULT '', t REAL NOT NULL, expires REAL NOT NULL, created_at TEXT NOT NULL, decided_at TEXT);
    CREATE INDEX IF NOT EXISTS channel_pairs_open ON channel_pairs(channel, account, status);
    CREATE TABLE IF NOT EXISTS channel_claims (hash TEXT PRIMARY KEY, channel TEXT NOT NULL DEFAULT '', t REAL NOT NULL, expires REAL NOT NULL,
        used_at TEXT, used_by TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS channel_history (id INTEGER PRIMARY KEY AUTOINCREMENT, channel TEXT NOT NULL, account TEXT NOT NULL,
        lane TEXT NOT NULL, sender TEXT NOT NULL, name TEXT NOT NULL DEFAULT '', text TEXT NOT NULL, message_id TEXT NOT NULL DEFAULT '',
        t REAL NOT NULL);
    CREATE INDEX IF NOT EXISTS channel_history_lane ON channel_history(channel, account, lane, id);
    CREATE TABLE IF NOT EXISTS channel_sent (channel TEXT NOT NULL, account TEXT NOT NULL, chat TEXT NOT NULL, message_id TEXT NOT NULL,
        kind TEXT NOT NULL, ref TEXT NOT NULL DEFAULT '', t REAL NOT NULL, PRIMARY KEY(channel, account, chat, message_id));
    CREATE INDEX IF NOT EXISTS channel_sent_ref ON channel_sent(kind, ref);
    CREATE TABLE IF NOT EXISTS channel_state (channel TEXT NOT NULL, account TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,
        updated_at TEXT NOT NULL, PRIMARY KEY(channel, account, key));
    """)
    _ready.add(str(settings.db))
    return conn


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


# —— 状态 ————————————————————————————————————————————————————————————————————

def state(channel: str, account: str, key: str, default: str = "") -> str:
    with _lock, sdb() as conn:
        r = conn.execute("SELECT value FROM channel_state WHERE channel=? AND account=? AND key=?", (channel, account, key)).fetchone()
    return r["value"] if r else default


def states(channel: str, account: str) -> dict[str, str]:
    with _lock, sdb() as conn:
        return {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM channel_state WHERE channel=? AND account=?", (channel, account))}


def set_state(channel: str, account: str, **kv: str) -> None:
    with _lock, sdb() as conn:
        _set_state(conn, channel, account, kv)


def _set_state(conn: sqlite3.Connection, channel: str, account: str, kv: dict) -> None:
    ts = now_iso()
    for k, v in kv.items():
        conn.execute("INSERT INTO channel_state(channel, account, key, value, updated_at) VALUES(?,?,?,?,?) ON CONFLICT(channel, account, key) "
                     "DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at", (channel, account, k, str(v), ts))


# —— 进来的 ————————————————————————————————————————————————————————————————————

def ingest(channel: str, account: str, items: Iterable[Inbound], state_kv: dict | None = None) -> list[int]:
    """一批落库，去重掉见过的（同一个 ext_id）；state_kv（offset / 水位、最后一次收到）在同一个事务里写。→ 新进来的行 id。
    写不进去就抛（sqlite3.Error）：调用方不往前挪（下次还会收到这一批，见过的去重掉）。"""
    ts, t = now_iso(), time.time()
    new: list[int] = []
    with _lock, sdb() as conn:
        for ib in items:
            cur = conn.execute("INSERT OR IGNORE INTO channel_inbox(channel, account, ext_id, lane, sender, payload, status, why, t, created_at, "
                               "updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                               (channel, account, ib.ext_id, ib.lane, ib.sender, ib.to_json(), "skipped" if ib.skip else "pending",
                                ib.skip, t, ts, ts))
            if cur.rowcount and not ib.skip:
                new.append(int(cur.lastrowid))
        if state_kv:
            _set_state(conn, channel, account, state_kv)
    return new


def seen(channel: str, account: str, ext_id: str) -> bool:
    with _lock, sdb() as conn:
        return conn.execute("SELECT 1 FROM channel_inbox WHERE channel=? AND account=? AND ext_id=?", (channel, account, ext_id)).fetchone() is not None


def lanes(channel: str, account: str) -> list[str]:
    """有等着处理的（到了重试时间的）聊天，按最早的那一条排。"""
    now = time.time()
    with _lock, sdb() as conn:
        rows = conn.execute("SELECT lane, MIN(id) first FROM channel_inbox WHERE channel=? AND account=? AND status='pending' AND next_at<=? "
                            "GROUP BY lane ORDER BY first", (channel, account, now)).fetchall()
    return [r["lane"] for r in rows]


def head(channel: str, account: str, lane: str) -> sqlite3.Row | None:
    """这个聊天里最早一条还没处理完的（pending，或者还没到重试时间的也算：它挡着后面的，同一个聊天按顺序）。"""
    with _lock, sdb() as conn:
        return conn.execute("SELECT * FROM channel_inbox WHERE channel=? AND account=? AND lane=? AND status='pending' ORDER BY id LIMIT 1",
                            (channel, account, lane)).fetchone()


def take(rid: int) -> bool:
    """pending → working（抢到了才处理：同一条不会处理两遍）。"""
    with _lock, sdb() as conn:
        return conn.execute("UPDATE channel_inbox SET status='working', tries=tries+1, updated_at=? WHERE id=? AND status='pending'",
                            (now_iso(), rid)).rowcount == 1


def finish(rid: int, status: str, *, who: str | None = None, why: str | None = None, ref: str | None = None, error: str | None = None,
           next_at: float | None = None) -> None:
    sets, args = ["status=?", "updated_at=?"], [status, now_iso()]
    for col, val in (("who", who), ("why", why), ("ref", ref), ("error", error), ("next_at", next_at)):
        if val is not None:
            sets.append(f"{col}=?")
            args.append(val)
    with _lock, sdb() as conn:
        conn.execute(f"UPDATE channel_inbox SET {', '.join(sets)} WHERE id=?", (*args, rid))  # noqa: S608 — 列名是上面写死的


def inbound(rid: int) -> sqlite3.Row | None:
    with _lock, sdb() as conn:
        return conn.execute("SELECT * FROM channel_inbox WHERE id=?", (rid,)).fetchone()


def recover(channel: str, account: str) -> int:
    """重启后：还在 working 的回到 pending 接着处理。→ 几条。"""
    with _lock, sdb() as conn:
        return conn.execute("UPDATE channel_inbox SET status='pending', updated_at=? WHERE channel=? AND account=? AND status='working'",
                            (now_iso(), channel, account)).rowcount


def waiting(channel: str, account: str) -> list[sqlite3.Row]:
    with _lock, sdb() as conn:
        return conn.execute("SELECT * FROM channel_inbox WHERE channel=? AND account=? AND status='waiting' ORDER BY id", (channel, account)).fetchall()


def count_from(channel: str, sender: str, seconds: float, upto: int | None = None) -> int:
    """这个人最近 seconds 秒里进来了几条（每人上限）；upto = 只数到这一条为止（一起进来的一批里，前面的不被后面的挤掉）。"""
    since = time.time() - seconds
    q = "SELECT COUNT(*) FROM channel_inbox WHERE channel=? AND sender=? AND t>=?"
    args: list = [channel, sender, since]
    if upto is not None:
        q += " AND id<=?"
        args.append(upto)
    with _lock, sdb() as conn:
        return int(conn.execute(q, args).fetchone()[0])


def recent_inbox(channel: str, account: str, limit: int = 50) -> list[sqlite3.Row]:
    with _lock, sdb() as conn:
        return conn.execute("SELECT * FROM channel_inbox WHERE channel=? AND account=? ORDER BY id DESC LIMIT ?", (channel, account, limit)).fetchall()


# —— 出去的 ————————————————————————————————————————————————————————————————————

def enqueue(channel: str, account: str, chat: str, text: str = "", *, topic: str = "", format: str = "markdown", reply_to: str = "",
            buttons: list | None = None, edit: str = "", extra: dict | None = None, why: str = "reply", ref: str = "") -> int:
    ts, t = now_iso(), time.time()
    body = {"text": text, "format": format, "reply_to": reply_to, "buttons": buttons or [], "edit": edit, "extra": extra or {}}
    with _lock, sdb() as conn:
        return int(conn.execute("INSERT INTO channel_outbox(channel, account, chat, topic, body, why, ref, t, created_at, updated_at) "
                                "VALUES(?,?,?,?,?,?,?,?,?,?)", (channel, account, str(chat), str(topic or ""),
                                                                json.dumps(body, ensure_ascii=False), why, ref, t, ts, ts)).lastrowid)


def outbound(row: sqlite3.Row) -> Outbound:
    b = json.loads(row["body"] or "{}")
    return Outbound(id=row["id"], channel=row["channel"], account=row["account"], chat=row["chat"], topic=row["topic"], text=b.get("text") or "",
                    format=b.get("format") or "markdown", reply_to=b.get("reply_to") or "", buttons=b.get("buttons") or [],
                    edit=b.get("edit") or "", extra=b.get("extra") or {}, why=row["why"], tries=row["tries"])


def due(channel: str, account: str) -> list[sqlite3.Row]:
    """该发的：每个聊天最早一条排着的（同一个聊天按顺序：前一条在等重试，后面的也等着），到了时间的。"""
    now = time.time()
    with _lock, sdb() as conn:
        rows = conn.execute("""SELECT o.* FROM channel_outbox o JOIN (SELECT chat, MIN(id) id FROM channel_outbox WHERE channel=? AND account=?
            AND status IN ('queued','sending') GROUP BY chat) h ON h.id = o.id WHERE o.status='queued' AND o.next_at<=? ORDER BY o.id""",
                            (channel, account, now)).fetchall()
    return rows


def claim_out(oid: int) -> bool:
    with _lock, sdb() as conn:
        return conn.execute("UPDATE channel_outbox SET status='sending', tries=tries+1, updated_at=? WHERE id=? AND status='queued'",
                            (now_iso(), oid)).rowcount == 1


def set_out(oid: int, **cols) -> None:
    cols["updated_at"] = now_iso()
    if "sent_ids" in cols and not isinstance(cols["sent_ids"], str):
        cols["sent_ids"] = json.dumps(cols["sent_ids"])
    with _lock, sdb() as conn:
        conn.execute(f"UPDATE channel_outbox SET {', '.join(f'{k}=?' for k in cols)} WHERE id=?", (*cols.values(), oid))  # noqa: S608


def out_row(oid: int) -> sqlite3.Row | None:
    with _lock, sdb() as conn:
        return conn.execute("SELECT * FROM channel_outbox WHERE id=?", (oid,)).fetchone()


def recover_out(channel: str, account: str) -> int:
    """重启后：还在 sending 的回到 queued（可能发过了一遍：宁可重一次，不丢）。"""
    with _lock, sdb() as conn:
        return conn.execute("UPDATE channel_outbox SET status='queued', updated_at=? WHERE channel=? AND account=? AND status='sending'",
                            (now_iso(), channel, account)).rowcount


def outbox(channel: str | None = None, account: str | None = None, status: str | None = None, limit: int = 100) -> list[sqlite3.Row]:
    q, args, conds = "SELECT * FROM channel_outbox", [], []
    for col, val in (("channel", channel), ("account", account), ("status", status)):
        if val:
            conds.append(f"{col}=?")
            args.append(val)
    if conds:
        q += " WHERE " + " AND ".join(conds)
    q += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    with _lock, sdb() as conn:
        return conn.execute(q, args).fetchall()


def retry_out(oid: int) -> bool:
    """死信重发：回到 queued、次数清零。"""
    with _lock, sdb() as conn:
        return conn.execute("UPDATE channel_outbox SET status='queued', tries=0, next_at=0, error='', updated_at=? WHERE id=? AND status='dead'",
                            (now_iso(), oid)).rowcount == 1


def sent_to(channel: str, account: str, chat: str, seconds: float, why: str | None = None) -> int:
    """最近 seconds 秒里发给这个聊天（排着的也算）几条（邮件每小时的回信上限）。"""
    q = "SELECT COUNT(*) FROM channel_outbox WHERE channel=? AND account=? AND chat=? AND t>=? AND status!='cancelled'"
    args: list = [channel, account, chat, time.time() - seconds]
    if why:
        q += " AND why=?"
        args.append(why)
    with _lock, sdb() as conn:
        return int(conn.execute(q, args).fetchone()[0])


# —— 发出去的消息 ————————————————————————————————————————————————————————————————

def remember_sent(channel: str, account: str, chat: str, message_ids: Iterable[str], kind: str, ref: str = "") -> None:
    t = time.time()
    with _lock, sdb() as conn:
        for mid in message_ids:
            if mid:
                conn.execute("INSERT OR REPLACE INTO channel_sent(channel, account, chat, message_id, kind, ref, t) VALUES(?,?,?,?,?,?,?)",
                             (channel, account, str(chat), str(mid), kind, ref, t))


def sent_one(channel: str, account: str, chat: str, message_id: str) -> sqlite3.Row | None:
    with _lock, sdb() as conn:
        return conn.execute("SELECT * FROM channel_sent WHERE channel=? AND account=? AND chat=? AND message_id=?",
                            (channel, account, str(chat), str(message_id))).fetchone()


def sent_id(channel: str, account: str, message_id: str) -> sqlite3.Row | None:
    """这个渠道上的消息 id 是不是我们发出去的（不管发给谁）。"""
    with _lock, sdb() as conn:
        return conn.execute("SELECT * FROM channel_sent WHERE channel=? AND account=? AND message_id=? LIMIT 1",
                            (channel, account, str(message_id))).fetchone()


def sent_by_ref(kind: str, ref: str) -> list[sqlite3.Row]:
    with _lock, sdb() as conn:
        return conn.execute("SELECT * FROM channel_sent WHERE kind=? AND ref=? ORDER BY t", (kind, ref)).fetchall()


# —— 群里的话 ——————————————————————————————————————————————————————————————————

def remember_group(channel: str, account: str, lane: str, sender: str, name: str, text: str, message_id: str = "") -> None:
    with _lock, sdb() as conn:
        conn.execute("INSERT INTO channel_history(channel, account, lane, sender, name, text, message_id, t) VALUES(?,?,?,?,?,?,?,?)",
                     (channel, account, lane, sender, name, text, message_id, time.time()))
        conn.execute("DELETE FROM channel_history WHERE channel=? AND account=? AND lane=? AND id NOT IN (SELECT id FROM channel_history "
                     "WHERE channel=? AND account=? AND lane=? ORDER BY id DESC LIMIT ?)", (channel, account, lane, channel, account, lane, KEEP_HISTORY))


def group_history(channel: str, account: str, lane: str, limit: int) -> list[sqlite3.Row]:
    """被 @ 时带上的：这个群最近 limit 条没 @ 的话（旧的在前）。带过的清掉（下次只带新的）。"""
    if limit <= 0:
        return []
    with _lock, sdb() as conn:
        rows = conn.execute("SELECT * FROM channel_history WHERE channel=? AND account=? AND lane=? ORDER BY id DESC LIMIT ?",
                            (channel, account, lane, limit)).fetchall()
        conn.execute("DELETE FROM channel_history WHERE channel=? AND account=? AND lane=?", (channel, account, lane))
    return list(reversed(rows))


def prune(days: int = KEEP_DONE_DAYS) -> None:
    """处理完的、发完的、过期的老行清掉（启动时一次）。"""
    cutoff = time.time() - days * 86400
    with _lock, sdb() as conn:
        conn.execute("DELETE FROM channel_inbox WHERE t<? AND status IN ('done','skipped','dead')", (cutoff,))
        conn.execute("DELETE FROM channel_outbox WHERE t<? AND status IN ('sent','dead','cancelled')", (cutoff,))
        conn.execute("DELETE FROM channel_sent WHERE t<?", (cutoff,))
        conn.execute("DELETE FROM channel_history WHERE t<?", (time.time() - 7 * 86400,))
        conn.execute("DELETE FROM channel_claims WHERE expires<?", (time.time() - 86400,))
