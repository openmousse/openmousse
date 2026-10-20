"""认人（第 6 批）：主人、联系人、陌生人。谁是谁决定走哪种回合，所以这里的规矩最要紧：

主人
  Telegram：认主人的码记下的账号 id（channel_people，role owner），或者账号配置里写的 owners。
  邮件：发件人在账号配置的 owners 里、而且 DMARC 过了（或 DKIM 对齐过了），见 email.py。
  认主人的码：只能从主人自己的设备上出（/api/setup/channels/owner-code 只认主人的设备；命令行 mousse channels owner-code
  在 Agent 跑的命令里不跑），照 pairing.py：8 位、去掉易混的字母表、默认 10 分钟过期、只能用一次、只存 sha256；
  在渠道里（只认私聊）发 /start <码>，就把这个渠道账号记成主人。每个来源 10 分钟最多错 5 次、全部最多错 30 次。
  聊天里没有任何一句话能把人升成主人：没有「设成主人」的命令，联系人的设置也改不了 role。
联系人（role contact）
  配对批了的、allowlist 模式里名单上的、在设置里加的。有档位（close / friend / mate，名片 Agent 按它放资料）和绑哪个 Agent；
  status：active 照常 / blocked 不理（不调模型、不回）。
陌生人
  私聊默认走配对（照 OpenClaw 的 pairing-store）：回一个配对码（不调模型），一小时过期；同一个人还有没过期的就还是那个码、
  不再回第二遍；每个账号最多 3 个待批，满了新来的什么都不回。主人在收件箱卡、app 或命令行批，批的时候定档位和绑哪个 Agent；
  批了成联系人，告诉对方一句「可以说话了」；拒了不告诉。
"""
from __future__ import annotations

import hashlib
import secrets
import sqlite3
import time
from datetime import datetime, timezone

from chat import _lock, now_iso

from . import store
from .base import Account, norm_id
from .store import sdb

ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"   # 和 pairing.py 一样（没有 0 O 1 I L）
CODE_LEN = 8
CLAIM_MINUTES = 10
PAIR_TTL = 3600                 # 陌生人的配对码多久过期（秒）
PAIR_MAX_PENDING = 3            # 每个账号最多几个待批
TIERS = ("close", "friend", "mate")
WINDOW, PER_SOURCE, TOTAL = 600, 5, 30   # 认主人的码：10 分钟里每个来源最多错 5 次，全部最多错 30 次
_fails: dict[str, list[float]] = {}


def normalize(code: str) -> str:
    return "".join(ch for ch in str(code or "").upper() if ch.isalnum())


def _hash(code: str) -> str:
    return hashlib.sha256(normalize(code).encode()).hexdigest()


def new_code() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(CODE_LEN))


def shown(code: str) -> str:
    """给人看的写法：ABCD-EFGH。"""
    c = normalize(code)
    return f"{c[:4]}-{c[4:]}" if len(c) == 8 else c


# —— 人 ——————————————————————————————————————————————————————————————————————

def row_dict(r: sqlite3.Row | None) -> dict | None:
    return dict(r) if r else None


def person(channel: str, sender: str) -> dict | None:
    with _lock, sdb() as conn:
        return row_dict(conn.execute("SELECT * FROM channel_people WHERE channel=? AND sender=?", (channel, norm_id(channel, sender))).fetchone())


def person_by_id(pid: str) -> dict | None:
    with _lock, sdb() as conn:
        return row_dict(conn.execute("SELECT * FROM channel_people WHERE id=?", (pid,)).fetchone())


def is_owner(acc: Account, sender: str) -> bool:
    """这个渠道账号是不是主人（Telegram：配置里的 owners 或认主人的码记下的；邮件只看配置，还要发件人验过，见 email.py）。"""
    s = norm_id(acc.kind, sender)
    if not s:
        return False
    if s in acc.owners():
        return True
    p = person(acc.kind, s)
    return bool(p and p["role"] == "owner" and p["status"] == "active")


def owners(channel: str) -> list[dict]:
    with _lock, sdb() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM channel_people WHERE channel=? AND role='owner' AND status='active' ORDER BY created_at",
                                              (channel,))]


def people(channel: str | None = None, role: str | None = None) -> list[dict]:
    q, args, conds = "SELECT * FROM channel_people", [], []
    if channel:
        conds.append("channel=?")
        args.append(channel)
    if role:
        conds.append("role=?")
        args.append(role)
    if conds:
        q += " WHERE " + " AND ".join(conds)
    with _lock, sdb() as conn:
        return [dict(r) for r in conn.execute(q + " ORDER BY created_at", args)]


def _upsert(channel: str, sender: str, *, role: str, name: str, tier: str, agent: str, via: str, account: str) -> dict:
    s = norm_id(channel, sender)
    ts = now_iso()
    with _lock, sdb() as conn:
        r = conn.execute("SELECT * FROM channel_people WHERE channel=? AND sender=?", (channel, s)).fetchone()
        if r:
            conn.execute("UPDATE channel_people SET role=?, name=?, tier=?, agent=?, status='active', via=?, account=?, updated_at=? WHERE id=?",
                         (role, name[:80] or r["name"], tier, agent, via, account, ts, r["id"]))
            pid = r["id"]
        else:
            pid = store.new_id("cp")
            conn.execute("INSERT INTO channel_people(id, channel, sender, role, name, tier, agent, status, via, account, created_at, updated_at) "
                         "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (pid, channel, s, role, name[:80], tier, agent, "active", via, account, ts, ts))
    return person_by_id(pid) or {}


def add_owner(channel: str, sender: str, *, name: str = "", account: str = "", via: str = "claim") -> dict:
    return _upsert(channel, sender, role="owner", name=name, tier="close", agent="", via=via, account=account)


def add_contact(channel: str, sender: str, *, name: str = "", tier: str = "friend", agent: str = "", account: str = "", via: str = "manual") -> dict:
    """加一个联系人（已经是主人的不降级：返回原样）。"""
    cur = person(channel, sender)
    if cur and cur["role"] == "owner" and cur["status"] == "active":
        return cur
    return _upsert(channel, sender, role="contact", name=name, tier=tier if tier in TIERS else "friend", agent=agent, via=via, account=account)


def update_person(pid: str, *, tier: str | None = None, agent: str | None = None, status: str | None = None, name: str | None = None) -> dict | None:
    """改联系人的档位、绑定、拉黑 / 放回。role 不在这里改（主人只能靠认主人的码）。"""
    sets: dict = {}
    if tier is not None:
        if tier not in TIERS:
            raise ValueError("tier")
        sets["tier"] = tier
    if agent is not None:
        sets["agent"] = agent
    if status is not None:
        if status not in ("active", "blocked", "removed"):
            raise ValueError("status")
        sets["status"] = status
    if name is not None:
        sets["name"] = name[:80]
    if not sets:
        return person_by_id(pid)
    sets["updated_at"] = now_iso()
    with _lock, sdb() as conn:
        n = conn.execute(f"UPDATE channel_people SET {', '.join(f'{k}=?' for k in sets)} WHERE id=?", (*sets.values(), pid)).rowcount  # noqa: S608
    return person_by_id(pid) if n else None


def remove_person(pid: str) -> bool:
    with _lock, sdb() as conn:
        return conn.execute("DELETE FROM channel_people WHERE id=?", (pid,)).rowcount == 1


def seen_name(pid: str, name: str) -> None:
    """对方改了名字：记下最新的（只拿来显示，不可信）。"""
    if not name:
        return
    with _lock, sdb() as conn:
        conn.execute("UPDATE channel_people SET name=? WHERE id=? AND name!=?", (name[:80], pid, name[:80]))


# —— 认主人的码 ————————————————————————————————————————————————————————————————

def new_claim(channel: str = "", minutes: int = CLAIM_MINUTES) -> tuple[str, str]:
    """出一个认主人的码（只存哈希）。channel 空 = 哪个渠道都认。→ (码, 过期时间 ISO)。"""
    code = new_code()
    t = time.time()
    exp = t + max(1, min(60, int(minutes))) * 60
    with _lock, sdb() as conn:
        conn.execute("INSERT INTO channel_claims(hash, channel, t, expires, created_at) VALUES(?,?,?,?,?)", (_hash(code), channel, t, exp, now_iso()))
    return code, datetime.fromtimestamp(exp, timezone.utc).isoformat(timespec="seconds")


def limited(source: str) -> bool:
    now = time.monotonic()
    for k in list(_fails):
        _fails[k] = [x for x in _fails[k] if now - x < WINDOW]
        if not _fails[k]:
            del _fails[k]
    return len(_fails.get(source, [])) >= PER_SOURCE or sum(len(v) for v in _fails.values()) >= TOTAL


def redeem_claim(channel: str, code: str, sender: str) -> str:
    """在渠道里发的 /start <码>：对了 → "ok"（码当场作废）；错了 / 过期 / 用过 → "bad"；试太多次 → "limited"。"""
    source = f"{channel}:{sender}"
    if limited(source):
        return "limited"
    if len(normalize(code)) != CODE_LEN:
        _fails.setdefault(source, []).append(time.monotonic())
        return "bad"
    h, now = _hash(code), time.time()
    with _lock, sdb() as conn:
        rows = conn.execute("SELECT * FROM channel_claims WHERE used_at IS NULL AND expires>? AND (channel='' OR channel=?)", (now, channel)).fetchall()
        hit = next((r for r in rows if secrets.compare_digest(r["hash"], h)), None)
        if hit is not None:
            n = conn.execute("UPDATE channel_claims SET used_at=?, used_by=? WHERE hash=? AND used_at IS NULL",
                             (now_iso(), f"{channel}:{sender}", hit["hash"])).rowcount
            if n == 1:
                return "ok"
    _fails.setdefault(source, []).append(time.monotonic())
    return "bad"


def open_claims() -> int:
    with _lock, sdb() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM channel_claims WHERE used_at IS NULL AND expires>?", (time.time(),)).fetchone()[0])


# —— 配对 ——————————————————————————————————————————————————————————————————————

def expire_pairs() -> None:
    with _lock, sdb() as conn:
        conn.execute("UPDATE channel_pairs SET status='expired', decided_at=? WHERE status='pending' AND expires<=?", (now_iso(), time.time()))


def pair_request(acc: Account, sender: str, chat: str, name: str, lang: str = "") -> tuple[dict | None, bool]:
    """陌生人私聊进来：→ (配对那一行, 是不是新出的)。还有没过期的 = 那一行、False（不再回第二遍）；这个账号待批的满了 → (None, False)。"""
    expire_pairs()
    s = norm_id(acc.kind, sender)
    with _lock, sdb() as conn:
        cur = conn.execute("SELECT * FROM channel_pairs WHERE channel=? AND account=? AND sender=? AND status='pending'", (acc.kind, acc.id, s)).fetchone()
        if cur:
            return dict(cur), False
        n = conn.execute("SELECT COUNT(*) FROM channel_pairs WHERE channel=? AND account=? AND status='pending'", (acc.kind, acc.id)).fetchone()[0]
        if n >= PAIR_MAX_PENDING:
            return None, False
        codes = {r[0] for r in conn.execute("SELECT code FROM channel_pairs WHERE status='pending'")}
        code = new_code()
        while code in codes:
            code = new_code()
        pid, t = store.new_id("pr"), time.time()
        conn.execute("INSERT INTO channel_pairs(id, channel, account, sender, chat, name, code, status, lang, t, expires, created_at) "
                     "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (pid, acc.kind, acc.id, s, str(chat), name[:80], code, "pending", lang, t, t + PAIR_TTL,
                                                         now_iso()))
        return dict(conn.execute("SELECT * FROM channel_pairs WHERE id=?", (pid,)).fetchone()), True


def set_pair_card(pid: str, inbox_id: str) -> None:
    with _lock, sdb() as conn:
        conn.execute("UPDATE channel_pairs SET inbox_id=? WHERE id=?", (inbox_id, pid))


def pair(ident: str) -> dict | None:
    """按 id（pr-…）、配对码（大小写、横杠都认）或收件箱卡的 id 找一个。"""
    ident = str(ident or "").strip()
    with _lock, sdb() as conn:
        r = conn.execute("SELECT * FROM channel_pairs WHERE id=? OR inbox_id=?", (ident, ident)).fetchone()
        if r is None and len(normalize(ident)) == CODE_LEN:
            r = conn.execute("SELECT * FROM channel_pairs WHERE code=? ORDER BY t DESC LIMIT 1", (normalize(ident),)).fetchone()
    return dict(r) if r else None


def pairs(status: str | None = "pending") -> list[dict]:
    expire_pairs()
    with _lock, sdb() as conn:
        if status:
            rows = conn.execute("SELECT * FROM channel_pairs WHERE status=? ORDER BY t DESC LIMIT 100", (status,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM channel_pairs ORDER BY t DESC LIMIT 100").fetchall()
    return [dict(r) for r in rows]


def decide_pair(ident: str, approve: bool, *, tier: str | None = None, agent: str | None = None) -> tuple[dict | None, str]:
    """批 / 拒一个配对。→ (那一行（批了带 person = 联系人的 id）, "ok" | "missing" | "expired" | "done")。批了成联系人（档位、绑定照给的，
    不给按账号的默认）。告诉对方、收卡由调用方做（router / cards）。"""
    expire_pairs()
    p = pair(ident)
    if p is None:
        return None, "missing"
    if p["status"] == "expired":
        return p, "expired"
    if p["status"] != "pending":
        return p, "done"
    from . import accounts
    acc = accounts.find(p["channel"], p["account"])
    pid = None
    if approve:
        who = add_contact(p["channel"], p["sender"], name=p["name"], tier=tier or (acc.tier if acc else "friend"),
                          agent=agent or (acc.agent if acc else "main"), account=p["account"], via="pairing")
        pid = who.get("id")
    with _lock, sdb() as conn:
        conn.execute("UPDATE channel_pairs SET status=?, person=?, decided_at=? WHERE id=? AND status='pending'",
                     ("approved" if approve else "rejected", pid, now_iso(), p["id"]))
    return {**p, "status": "approved" if approve else "rejected", "person": pid}, "ok"
