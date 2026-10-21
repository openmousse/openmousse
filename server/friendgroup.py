"""好友群（主线第三步第三段，2026-10-21；协议见 docs/social-protocol.md 7.1）：你和好友在一个群里，你这边只出一位管家（butler.py）。

- 谁在群里：群主建群时挑好友；对方名片的 caps 要有 group（新版本才有），老版本只能一对一，建群时写明为什么。
  成员表（roster）= 群 id、群名、群主 kid、版本号、每位成员的 kid / 公钥 / 地址 / 端到端公钥 / 中继地址 / 名字 / 带不带管家，
  由群主的钥匙签（和名片同一种 JWS：去掉 signatures 的 JCS，payload 分离）。成员表签名对不上的一律拒。
- 怎么送：没有中心服务器，每条消息对每位成员逐一投递（POST <根地址>/f/group，照第 5 节签名；对方有端到端公钥就封进信封，
  经中继也照样送；直连不通换对方的中继地址）。每次都带着当前的成员表：收的人先验成员表（群主是我的好友或者就是我、签名对、
  我在表上），再用表上那位成员的公钥验这一次请求的签名——所以群里不是我好友的人（群主的好友）也认得出来。版本更新的成员表只有
  群主发的才算。没送到的按 1 对 1 那套间隔重试（30 秒、2 分钟、10 分钟、1 小时、6 小时），3 天后放弃。
- 管家：每个服务器最多派一位（成员表上一人一个 butler 标记）；群主的管家主持，别人的管家由他们自己决定加不加入（发一条 butler 给群主，
  群主改表、重签、再发一遍）。别人 @ 我的管家（「@管家」只在我是群主时算我的；@ 它的名字都算）：照名片 Agent 的规矩答
  （cardagent.answer，channel group：零工具、说出去之前过 Doorman、约时间 / 答应事 / 钱 / 要资料出只给我的卡；不是我好友的人问它，
  它照陌生人的规矩不理）。答的话全群都看得到，所以资料按群里最紧的那一档放：每一类取问的人和别的成员里最紧的一级，不是我好友的成员算陌生人。问的是看法、名单上有赛博：先请教一位（butler.consult：只带要点，没有原话和名字），
  回答写明「X 的看法」。
- 好友的话只是资料：不进主对话、不驱动任何工具；一段聊完照写回挑值得记的进待定（memhold，channel group），一条都不直接记。
- 播客多人对谈拉了好友（podprep.py）：录前讨论就是一个好友群，来源（source：那一期）只在我这边、不进成员表。我的管家兼主持：先开口
  （固定的一句）；有人提了时间也接（不用 @），照名片 Agent 的规矩出卡给我，我点了同意才进这一期的日程（cardagent.DELIVER 的 data.scheduled）。
  提纲那一行（who system）只在我这边：不发出去、不给管家当上下文、不进待定。

server.json：跟着管家（butler 开着才有好友群；默认关）。表（mousse.db）：friend_groups、friend_group_msgs、friend_group_out。
接口（app 用，要令牌）
  GET   /api/friendgroups                      {groups}
  GET   /api/friendgroups/candidates           能拉进来的好友（can / why：对方版本旧、不是好友）
  POST  /api/friendgroups {title, friends}     建群（你是群主，你的管家主持）
  GET   /api/friendgroups/{id}?after=           {group, messages, cards（只给你的卡）, consults（只给你看：管家问了哪位赛博、带的要点）,
                                               offers（录前讨论：「放进」的卡）}
  POST  /api/friendgroups/{id}/read {upto}      看到了这一条（未读清掉；不发已读回执）
  POST  /api/friendgroups/{id}/send {text}     你说一句
  PATCH /api/friendgroups/{id} {butler}        你的管家进不进这个群（群主的管家一直在）
对别的服务器（公开小服务，签名）
  POST  /f/group {v, type: roster | msg | butler, roster, msg?, butler?}
"""
from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

import chat
import social
from chat import _lock, log_activity, now_iso
from i18n import L, LS

GID_RE = re.compile(r"^fg-[0-9a-f]{16}$")
MID_RE = re.compile(r"^[0-9a-f]{32}$")
TITLE_MAX, TEXT_MAX = 40, 4000
MEMBERS_MAX = 9           # 连你在内
BODY_MAX = 64_000
HISTORY = 12              # 管家答的时候带几条之前的
RETRY = (30, 120, 600, 3600, 21600)
GIVE_UP = 3 * 86400
CAP = "group"
HOLD_IDLE_MIN, HOLD_TICK = 30, 300   # 最后一条过了多久算「一段聊完了」、多久看一眼


def on() -> bool:
    """好友群跟着管家：管家开着（butler.py，要用自己的内核）才有。"""
    import butler
    return butler.on()


def enabled() -> None:
    if not on():
        raise HTTPException(404, L("这台服务器没开好友群（要先开管家：server.json 的 butler）", "Friend groups are off on this server (turn on the butler first: butler in server.json)"))


router = APIRouter(dependencies=[Depends(enabled)])
public_router = APIRouter()

SCHEMA = """
CREATE TABLE IF NOT EXISTS friend_groups (id TEXT PRIMARY KEY, owner TEXT NOT NULL, title TEXT NOT NULL, roster TEXT NOT NULL,
    version INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'active', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    held INTEGER NOT NULL DEFAULT 0, source TEXT, seen INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS friend_group_msgs (id INTEGER PRIMARY KEY AUTOINCREMENT, grp TEXT NOT NULL, mid TEXT NOT NULL, sender TEXT NOT NULL,
    who TEXT NOT NULL, text TEXT NOT NULL, reply_to TEXT, at TEXT NOT NULL, dir TEXT NOT NULL, meta TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL, UNIQUE (grp, sender, mid));
CREATE INDEX IF NOT EXISTS friend_group_msgs_grp ON friend_group_msgs(grp, id);
CREATE TABLE IF NOT EXISTS friend_group_out (id INTEGER PRIMARY KEY AUTOINCREMENT, grp TEXT NOT NULL, to_kid TEXT NOT NULL, payload TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued', tries INTEGER NOT NULL DEFAULT 0, next_try TEXT, error TEXT, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS friend_group_out_q ON friend_group_out(status, to_kid, id);
"""
_ready: set[str] = set()


def gdb() -> sqlite3.Connection:
    conn = chat.db()
    key = str(chat.DB)
    if key not in _ready:
        conn.executescript(SCHEMA)
        cols = {c[1] for c in conn.execute("PRAGMA table_info(friend_groups)")}
        if "source" not in cols:  # 来源（一期播客的录前讨论）：只在我这边，不进成员表
            conn.execute("ALTER TABLE friend_groups ADD COLUMN source TEXT")
        if "seen" not in cols:  # 我看到了哪一条（未读 = 这之后别人发来的）
            conn.execute("ALTER TABLE friend_groups ADD COLUMN seen INTEGER NOT NULL DEFAULT 0")
        _ready.add(key)
    return conn


def clean(v, n: int) -> str:
    return " ".join(str(v or "").split())[:n]


def card_caps() -> dict:
    """social.CARD_HOOKS：开着好友群的服务器在名片 caps 里加 group（对方据此知道能不能拉它进群）。"""
    return {"caps": [CAP]} if on() else {}


social.CARD_HOOKS.append(card_caps)


# —— 成员表 ——————————————————————————————————————————————————————————————————————————————

def me_entry(butler_on: bool) -> dict:
    ident = social.identity()
    card = social.card_body() or {}
    return {"kid": ident["kid"], "x": ident["x"], "enc": social.enc_x(), "url": social.my_url() or "", "relay": card.get("relay") or "",
            "name": social.my_name(), "butler": butler_on}


def friend_entry(f: dict) -> dict:
    card = social.friend_card(f)
    return {"kid": f["kid"], "x": f["pub"], "enc": social.card_enc(card) or "", "url": f["url"], "relay": social.card_relay(card) or "",
            "name": f.get("card_name") or f["name"], "butler": False}


def sign_roster(gid: str, title: str, members: list[dict], version: int) -> dict:
    body = {"openmousse": "1", "group": gid, "title": title, "owner": social.my_kid(), "version": version, "at": now_iso(),
            "members": members}
    return {**body, "signatures": [social.sign_jws(body)]}


def verify_roster(roster) -> dict:
    """验成员表：形状对、群主是我或者我的（在的）好友、群主的钥匙签的、每位成员的 kid 就是表上公钥的指纹、地址像样、我在表上。
    → 干净的一份（members 里每位多一个 me）。不对就 ValueError（调用方一律拒）。"""
    if not isinstance(roster, dict) or roster.get("openmousse") != "1":
        raise ValueError("bad roster")
    gid, owner, version = roster.get("group"), roster.get("owner"), roster.get("version")
    if not isinstance(gid, str) or not GID_RE.match(gid) or not isinstance(owner, str) or not isinstance(version, int) or isinstance(version, bool):
        raise ValueError("bad roster")
    mine = social.my_kid()
    if owner == mine:
        x = social.identity()["x"]
    else:
        f = social.friend_by_kid(owner)
        if not f or f["status"] != "active":
            raise ValueError("owner is not a friend")
        x = f["pub"]
    if owner not in social.jws_kids(roster) or not social.verify_jws(roster, x):
        raise ValueError("bad roster signature")
    members = roster.get("members")
    if not isinstance(members, list) or not 2 <= len(members) <= MEMBERS_MAX:
        raise ValueError("bad roster")
    out, seen = [], set()
    for m in members:
        if not isinstance(m, dict) or not isinstance(m.get("x"), str) or not isinstance(m.get("kid"), str):
            raise ValueError("bad member")
        if social.thumbprint(m["x"]) != m["kid"] or m["kid"] in seen:
            raise ValueError("bad member")
        url = social.origin_of(str(m.get("url") or "")) if m["kid"] != mine else str(m.get("url") or "")
        if m["kid"] != mine and (not url or not social.url_ok(url)):
            raise ValueError("bad member url")
        relay = social.origin_of(str(m.get("relay") or "")) if m.get("relay") else ""
        seen.add(m["kid"])
        out.append({"kid": m["kid"], "x": m["x"], "enc": str(m.get("enc") or ""), "url": url, "relay": relay if relay and social.url_ok(relay) else "",
                    "name": social.clean_name(m.get("name")) or m["kid"][:6], "butler": m.get("butler") is True, "me": m["kid"] == mine})
    if owner not in seen or mine not in seen:
        raise ValueError("not a member")
    return {"group": gid, "title": clean(roster.get("title"), TITLE_MAX) or gid, "owner": owner, "version": version, "members": out, "raw": roster}


def group_row(conn: sqlite3.Connection, gid: str):
    r = conn.execute("SELECT * FROM friend_groups WHERE id=?", (gid,)).fetchone() if GID_RE.match(gid or "") else None
    if r is None:
        raise HTTPException(404, L("没有这个好友群", "No such friend group"))
    return r


def roster_of(r) -> dict:
    return verify_roster(json.loads(r["roster"]))


def store_roster(ro: dict) -> bool:
    """存（或换成更新的）成员表。旧的不覆盖新的；群主是第一次存下时的那位，换了群主的一律不认（别的成员拿同一个群 id 另签一份，
    版本给得再大也盖不过去）。→ 这一份是不是用上了。"""
    ts = now_iso()
    with _lock, gdb() as conn:
        r = conn.execute("SELECT version, owner FROM friend_groups WHERE id=?", (ro["group"],)).fetchone()
        if r is not None and (r["owner"] != ro["owner"] or r["version"] >= ro["version"]):
            return False
        raw = json.dumps(ro["raw"], ensure_ascii=False)
        if r is None:
            conn.execute("INSERT INTO friend_groups(id, owner, title, roster, version, status, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?)",
                         (ro["group"], ro["owner"], ro["title"], raw, ro["version"], "active", ts, ts))
        else:
            conn.execute("UPDATE friend_groups SET title=?, roster=?, version=?, updated_at=? WHERE id=?", (ro["title"], raw, ro["version"], ts, ro["group"]))
    return True


def member_of(ro: dict, kid: str) -> dict | None:
    return next((m for m in ro["members"] if m["kid"] == kid), None)


def my_butler_in(ro: dict) -> bool:
    me = next((m for m in ro["members"] if m["me"]), None)
    return bool(me and me["butler"])


# —— 投递 ——————————————————————————————————————————————————————————————————————————————————

_loop: asyncio.AbstractEventLoop | None = None
_wake: asyncio.Event | None = None


def kick() -> None:
    if _loop is not None and _wake is not None:
        _loop.call_soon_threadsafe(_wake.set)


def queue(gid: str, payload: dict, ro: dict, *, skip: tuple[str, ...] = ()) -> int:
    """给成员表上除了我（和 skip 里的）每一位排一份。→ 排了几份。"""
    body = json.dumps(payload, ensure_ascii=False)
    n = 0
    with _lock, gdb() as conn:
        for m in ro["members"]:
            if m["me"] or m["kid"] in skip:
                continue
            conn.execute("INSERT INTO friend_group_out(grp, to_kid, payload, created_at) VALUES(?,?,?,?)", (gid, m["kid"], body, now_iso()))
            n += 1
    kick()
    return n


async def post_to(m: dict, payload: dict) -> tuple[int, str | None]:
    """发给一位成员：先直连，连不上（网络、502 / 503 / 504）换它的中继地址；对方有端到端公钥就封进信封（signed_post 自己封）。"""
    code, err = 0, None
    for base in [m["url"]] + ([m["relay"]] if m.get("relay") and m["relay"] != m["url"] else []):
        try:
            r = await social.signed_post(base + "/f/group", payload, to_kid=m["kid"], seal_to=m.get("enc") or None)
            code = r.status_code
            try:
                err = (r.json() or {}).get("error")
            except ValueError:
                err = None
        except (httpx.HTTPError, HTTPException, OSError) as e:
            code, err = 0, type(e).__name__
        if code not in (0, 502, 503, 504):
            break
    return code, err


def _next_try(tries: int) -> str:
    wait = RETRY[tries - 1] if tries - 1 < len(RETRY) else RETRY[-1]
    return (datetime.now(timezone.utc) + timedelta(seconds=wait)).isoformat(timespec="seconds")


async def send_one(r) -> bool:
    """发一份；送到了（或者再也送不到了）回 True，要等下次的回 False。"""
    with _lock, gdb() as conn:
        g = conn.execute("SELECT * FROM friend_groups WHERE id=?", (r["grp"],)).fetchone()
    try:
        m = member_of(roster_of(g), r["to_kid"]) if g is not None else None
    except ValueError:
        m = None
    if m is None:
        with _lock, gdb() as conn:
            conn.execute("UPDATE friend_group_out SET status='failed', error='not a member' WHERE id=?", (r["id"],))
        return True
    code, err = await post_to(m, json.loads(r["payload"]))
    tries = r["tries"] + 1
    with _lock, gdb() as conn:
        if code == 200:
            conn.execute("UPDATE friend_group_out SET status='sent', tries=?, next_try=NULL, error=NULL WHERE id=?", (tries, r["id"]))
            return True
        first = datetime.fromisoformat(r["created_at"])
        if (code in (0, 429) or code >= 500) and (datetime.now(first.tzinfo) - first).total_seconds() <= GIVE_UP:
            conn.execute("UPDATE friend_group_out SET tries=?, next_try=?, error=? WHERE id=?", (tries, _next_try(tries), f"{code or 'network'} {err or ''}".strip(), r["id"]))
            return False
        conn.execute("UPDATE friend_group_out SET status='failed', tries=?, next_try=NULL, error=? WHERE id=?", (tries, f"{code} {err or ''}".strip(), r["id"]))
    return True


async def deliver_due() -> None:
    """排着的按收件人一个一个送（同一个人的按先后：前一份没送到，后面的先等着）。好友群关着不送：排着的等开回来再送。"""
    if not on():
        return
    with _lock, gdb() as conn:
        kids = [x["to_kid"] for x in conn.execute("SELECT DISTINCT to_kid FROM friend_group_out WHERE status='queued'")]
    now = datetime.now(timezone.utc)
    for kid in kids:
        while True:
            with _lock, gdb() as conn:
                r = conn.execute("SELECT * FROM friend_group_out WHERE to_kid=? AND status='queued' ORDER BY id LIMIT 1", (kid,)).fetchone()
            if r is None:
                break
            if r["next_try"] and datetime.fromisoformat(r["next_try"]) > now:
                break
            if not await send_one(r):
                break


async def outbox_loop() -> None:
    assert _wake is not None
    while True:
        try:
            await deliver_due()
        except Exception as e:  # noqa: BLE001 — 投递循环不能停
            print(f"[friendgroup] 投递出错：{type(e).__name__}: {e}")
        try:
            await asyncio.wait_for(_wake.wait(), timeout=15)
        except TimeoutError:
            pass
        _wake.clear()


def start() -> None:
    """主服务启动时（main.py 的 lifespan）：起投递循环和挑待定的循环；开没开好友群每次现看（关着不送、不挑，排着的等开回来再送）。"""
    global _loop, _wake
    _loop = asyncio.get_running_loop()
    _wake = asyncio.Event()
    _loop.create_task(outbox_loop())
    _loop.create_task(sweep_loop())


# —— 待定：好友说的，一条都不直接记 ————————————————————————————————————————————————————————————

async def hold_pass(gid: str) -> list[dict]:
    """一段聊完了：照写回挑值得记的进待定（groupchat.hold_lines，channel group）；好友说的只是资料，进待定由你逐条定。
    我的话一起给模型当上下文（不会被记成别人说的），谁的管家说的不算。游标（held）做完才往前挪。→ 进了待定的那几条。"""
    import groupchat
    from engine import writeback
    if not writeback.enabled():
        return []
    with _lock, gdb() as conn:
        g = group_row(conn, gid)
        rows = conn.execute("SELECT * FROM friend_group_msgs WHERE grp=? AND id>? ORDER BY id", (gid, g["held"])).fetchall()
    if not rows:
        return []
    ro = roster_of(g)
    mine = social.my_kid()
    msgs = [{"id": r["id"], "text": r["text"], "who": name_in(ro, r["sender"], r["who"]), "speaker": "" if r["sender"] == mine else r["sender"]}
            for r in rows if r["who"] == "person"]
    upto = rows[-1]["id"]
    if not any(m["speaker"] for m in msgs) or writeback.words(" ".join(m["text"] for m in msgs)) < writeback.MIN_WORDS:
        with _lock, gdb() as conn:
            conn.execute("UPDATE friend_groups SET held=? WHERE id=?", (upto, gid))
        return []
    got = await groupchat.hold_lines(msgs, thread=gid, conversation=f"friendgroup:{gid}", via=LS(f"好友群「{g['title']}」", f'friend group "{g["title"]}"'),
                                     prefix="fg")
    if got is None:
        return []
    with _lock, gdb() as conn:
        conn.execute("UPDATE friend_groups SET held=? WHERE id=?", (upto, gid))
    return got[0]


def idle_groups(now: datetime | None = None) -> list[str]:
    cut = (now or datetime.now(timezone.utc)) - timedelta(minutes=HOLD_IDLE_MIN)
    out = []
    with _lock, gdb() as conn:
        for g in conn.execute("SELECT id, held FROM friend_groups WHERE status='active'").fetchall():
            last = conn.execute("SELECT id, created_at FROM friend_group_msgs WHERE grp=? ORDER BY id DESC LIMIT 1", (g["id"],)).fetchone()
            if last is not None and last["id"] > g["held"] and datetime.fromisoformat(last["created_at"]) < cut:
                out.append(g["id"])
    return out


async def sweep_loop() -> None:
    while True:
        await asyncio.sleep(HOLD_TICK)
        if not on():
            continue
        for gid in idle_groups():
            try:
                await hold_pass(gid)
            except Exception as e:  # noqa: BLE001 — 一个群没挑成不影响别的
                print(f"[friendgroup] {gid} 挑待定出错：{type(e).__name__}: {e}")


def run_on_main(coro) -> None:
    """公网小服务的请求在另一个线程：管家答话这种要调模型的事交给主事件循环（测试里没有主循环就在当前循环跑）。"""
    if _loop is not None:
        asyncio.run_coroutine_threadsafe(coro, _loop)
    else:
        t = asyncio.get_running_loop().create_task(coro)
        _tasks.add(t)
        t.add_done_callback(_tasks.discard)


_tasks: set = set()


# —— 消息 ——————————————————————————————————————————————————————————————————————————————————

def add_msg(gid: str, mid: str, sender: str, who: str, text: str, *, at: str, dir_: str, reply_to: str | None = None, meta: dict | None = None) -> int | None:
    """存一条；同一个群、同一个人、同一个 id 的重复 → None。"""
    with _lock, gdb() as conn:
        try:
            cur = conn.execute("INSERT INTO friend_group_msgs(grp, mid, sender, who, text, reply_to, at, dir, meta, created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                               (gid, mid, sender, who, text, reply_to, at, dir_, json.dumps(meta or {}, ensure_ascii=False), now_iso()))
        except sqlite3.IntegrityError:
            return None
        conn.execute("UPDATE friend_groups SET updated_at=? WHERE id=?", (now_iso(), gid))
        return cur.lastrowid


def wire_msg(mid: str, who: str, text: str, at: str, reply_to: str | None = None) -> dict:
    return {"id": mid, "who": who, "text": text, "at": at, **({"reply_to": reply_to} if reply_to else {})}


def say(gid: str, text: str, who: str = "person", *, meta: dict | None = None) -> dict:
    """我这边在群里说一句（我本人 / 我的管家）：先记下，再给每位成员排一份。"""
    with _lock, gdb() as conn:
        r = group_row(conn, gid)
    ro = roster_of(r)
    if who == "butler" and not my_butler_in(ro):
        raise HTTPException(409, L("你的管家不在这个群里", "Your butler isn't in this group"))
    mid, at = uuid.uuid4().hex, now_iso()
    rid = add_msg(gid, mid, social.my_kid(), who, text, at=at, dir_="out", meta=meta)
    queue(gid, {"v": 1, "type": "msg", "roster": ro["raw"], "msg": wire_msg(mid, who, text, at)}, ro)
    with _lock, gdb() as conn:
        row = conn.execute("SELECT * FROM friend_group_msgs WHERE id=?", (rid,)).fetchone()
    return msg_json(row, ro)  # 锁外拼：名字要读管家的资料（social 的设置也要这把锁）


def note(gid: str, text: str) -> dict:
    """只在我这边的一行灰字（「管家更新了提纲第 2、4 条」）：不发给成员，也不给管家当上下文、不进待定。"""
    with _lock, gdb() as conn:
        r = group_row(conn, gid)
    rid = add_msg(gid, uuid.uuid4().hex, social.my_kid(), "system", clean(text, 300), at=now_iso(), dir_="local")
    with _lock, gdb() as conn:
        row = conn.execute("SELECT * FROM friend_group_msgs WHERE id=?", (rid,)).fetchone()
    return msg_json(row, roster_of(r))


def butler_says(gid: str, text: str) -> dict:
    """我的管家对全群说一句固定的话（录前讨论的开场、约好的录制时间）：发出去，也记进「说过的话」（card_log，channel group）。
    固定的话不调模型；里面只有你自己定的东西（群名、你定的时间）。"""
    import cardagent
    msg = say(gid, clean(text, TEXT_MAX), "butler")
    cardagent.write_log({"key": gid, "name": title_of(gid), "tier": "friend"}, "group", f"fg:{gid}", "out", msg["text"], "sent", [], None,
                        {"by": "fixed"})
    return msg


def title_of(gid: str) -> str:
    with _lock, gdb() as conn:
        r = conn.execute("SELECT title FROM friend_groups WHERE id=?", (gid,)).fetchone() if GID_RE.match(gid or "") else None
    return r["title"] if r else ""


def source_of(gid: str) -> dict:
    """这个群从哪来（只在我这边）：一期播客的录前讨论 = {kind: podcast, id, title}；普通的好友群 = {}。"""
    with _lock, gdb() as conn:
        r = conn.execute("SELECT source FROM friend_groups WHERE id=?", (gid,)).fetchone() if GID_RE.match(gid or "") else None
    try:
        src = json.loads(r["source"]) if r and r["source"] else {}
    except ValueError:
        src = {}
    return src if isinstance(src, dict) else {}


def prep_episode(gid: str, ro: dict) -> str | None:
    """这个群是不是我这边一期播客的录前讨论（我是群主、我的管家兼主持）→ 那一期的 id；不是 → None。"""
    src = source_of(gid)
    if src.get("kind") == "podcast" and src.get("id") and ro["owner"] == social.my_kid() and my_butler_in(ro):
        return str(src["id"])
    return None


def my_lines(gid: str, limit: int = 40) -> list[dict]:
    """我本人在这个群里说的（新的在后）：给提纲用（帮忙排提纲的是赛博：好友说的不给）。"""
    with _lock, gdb() as conn:
        rows = conn.execute("SELECT text FROM friend_group_msgs WHERE grp=? AND sender=? AND who='person' ORDER BY id DESC LIMIT ?",
                            (gid, social.my_kid(), limit)).fetchall()
    return [{"who": "user", "text": clean(x["text"], 1500)} for x in reversed(rows)]


def waiting_card(gid: str) -> bool:
    """这个群有没有一张卡在等你点（管家问你的：约时间、扣下的一句）。"""
    import cardagent
    import inbox
    with _lock, inbox.idb():  # 收件箱表可能还没建：先建好再 JOIN
        pass
    with _lock, cardagent.cdb() as conn:
        r = conn.execute("""SELECT 1 FROM card_asks a JOIN inbox i ON i.id = a.inbox_id WHERE a.channel='group' AND a.ref=?
            AND i.status IN ('pending','revising') LIMIT 1""", (f"fg:{gid}",)).fetchone()
    return r is not None


def prep_fields(gid: str) -> dict:
    """录前讨论是好友群时「制作中」那一行要的（podprep.row_fields）：参与者（我的管家主持在前，再是各位好友）、最新一句、在不在等你。"""
    import butler
    with _lock, gdb() as conn:
        g = group_row(conn, gid)
        last = conn.execute("SELECT * FROM friend_group_msgs WHERE grp=? AND who != 'system' ORDER BY id DESC LIMIT 1", (gid,)).fetchone()
    ro = roster_of(g)
    host = {"id": "butler", "kind": "butler", "name": butler.display_name(), "avatar": butler.avatar(), "host": True}
    people = [{"id": m["kid"], "kind": "friend", "name": m["name"], "host": False} for m in ro["members"] if not m["me"]]
    latest = f"{name_in(ro, last['sender'], last['who'])}{L('：', ': ')}{clean(last['text'], 60)}" if last is not None else None
    return {"participants": [host, *people][:4], "latest": latest, "waiting": waiting_card(gid)}


def name_in(ro: dict, kid: str, who: str) -> str:
    """群里一条是谁说的：人照成员表上的名字；管家写成「<他的名字>的管家」（名字从成员表来，发的人改不了）；我的管家用它自己的名字。"""
    import butler
    if who == "system":
        return ""
    m = member_of(ro, kid)
    base = (m or {}).get("name") or L("对方", "Someone")
    if who != "butler":
        return L("你", "You") if m and m["me"] else base
    if m and m["me"]:
        return butler.display_name()
    return butler.butler_of(base)


def msg_json(r, ro: dict) -> dict:
    meta = json.loads(r["meta"] or "{}")
    mine = r["sender"] == social.my_kid()
    return {"id": r["id"], "mid": r["mid"], "sender": r["sender"], "who": r["who"], "mine": mine, "name": name_in(ro, r["sender"], r["who"]),
            "text": r["text"], "replyTo": r["reply_to"], "at": r["at"], "dir": r["dir"], "system": r["who"] == "system",
            **({"view": meta["view"]} if meta.get("view") else {}), **({"sentinel": meta["sentinel"]} if mine and meta.get("sentinel") else {}),
            **({"card": meta["card"]} if mine and meta.get("card") else {})}


def group_json(r, *, last=None, unread: int | None = None) -> dict:
    import butler
    ro = roster_of(r)
    mine = social.my_kid()
    members = [{"kid": m["kid"], "name": L("你", "You") if m["me"] else m["name"], "me": m["me"], "owner": m["kid"] == ro["owner"],
                "butler": m["butler"], "butlerName": (butler.display_name() if m["me"] else butler.butler_of(m["name"])) if m["butler"] else None}
               for m in ro["members"]]
    out = {"id": r["id"], "kind": "friendgroup", "title": r["title"], "owner": ro["owner"], "mine": ro["owner"] == mine, "version": ro["version"],
           "status": r["status"], "members": members, "myButler": my_butler_in(ro), "createdAt": r["created_at"], "updatedAt": r["updated_at"],
           "note": L("好友的话只给管家看要点，不驱动你的工具；说出去之前经 Doorman 复查。", "Friends' words reach your butler only as data and never drive your tools; Doorman checks every reply before it goes out.")}
    if last is not None:
        out["last"] = {"name": name_in(ro, last["sender"], last["who"]), "text": clean(last["text"], 80), "at": last["at"]}
    if unread is not None:
        out["unread"] = unread
    src = source_of(r["id"])
    if src.get("kind") == "podcast":  # 一期播客的录前讨论（podprep.py）：顶上钉着这一期的提纲、约好的录制时间
        import podprep
        out["source"] = src
        out["episode"] = podprep.episode_header(src.get("id") or "")
    return out


# —— 管家在群里答话 ——————————————————————————————————————————————————————————————————————————

VIEW_RE = re.compile(r"怎么看|觉得|看法|建议|该不该|要不要|值不值|好不好|意见|\b(?:think|opinion|view|advice|should|recommend)\b", re.I)


def mentions_me(text: str, ro: dict) -> bool:
    """这句是不是叫我的管家：@ 它的名字（或者「<我的名字>的管家」）；我是群主时「@管家」也算（群主的管家主持）。"""
    import butler
    t = text or ""
    names = {butler.display_name(L), butler.display_name(LS), f"{social.my_name()}的管家", f"{social.my_name()} 的管家", f"{social.my_name()}'s butler"}
    if any(f"@{n}" in t for n in names if n):
        return True
    return ro["owner"] == social.my_kid() and bool(re.search(r"[@＠](?:管家|butler)(?![\w一-鿿])", t, re.I))


def proposes(text: str) -> bool:
    """这句像是在约（说了时间又在问）或者涉及钱：和名片 Agent 认「要本人定」的是同一个判断。"""
    import cardagent
    return cardagent.proposal_like(text or "")


def history_of(gid: str, before: int, ro: dict) -> list[dict]:
    """给管家的上下文：这条之前的几条（them = 别人，you = 我的管家，owner = 我本人）。"""
    mine = social.my_kid()
    with _lock, gdb() as conn:
        rows = conn.execute("SELECT * FROM friend_group_msgs WHERE grp=? AND id<? AND who != 'system' ORDER BY id DESC LIMIT ?",
                            (gid, before, HISTORY)).fetchall()
    out = []
    for x in reversed(rows):
        frm = ("you" if x["who"] == "butler" else "owner") if x["sender"] == mine else "them"
        who = name_in(ro, x["sender"], x["who"])
        out.append({"from": frm, "text": x["text"] if frm != "them" else f"{who}: {x['text']}"})
    return out


def tier_here(kid: str) -> str:
    """成员表上的一位在我这边是哪一档：还是我好友的按好友表，不是的（删了、拉黑了、本来就不认识）算陌生人。"""
    f = social.friend_by_kid(kid)
    return str(f.get("tier") or "friend") if f and f["status"] == "active" else "stranger"


async def butler_reply(gid: str, rid: int) -> dict | None:
    """有人 @ 了我的管家：照名片 Agent 的规矩答（见文件开头），答的话发进群里（who butler）。→ 发出去的那条；不答 → None。"""
    import butler
    import cardagent
    with _lock, gdb() as conn:
        g = group_row(conn, gid)
        m = conn.execute("SELECT * FROM friend_group_msgs WHERE id=? AND grp=?", (rid, gid)).fetchone()
    ro = roster_of(g)
    if m is None or not my_butler_in(ro) or m["sender"] == social.my_kid():
        return None
    friend = social.friend_by_kid(m["sender"])
    friend = friend if friend and friend["status"] == "active" else None
    asker = member_of(ro, m["sender"]) or {}
    material, view = [], None
    if (friend is not None and VIEW_RE.search(m["text"]) and butler.consultable()
            and not cardagent.over_limit(cardagent.peer_of(friend, m["sender"], asker.get("name") or ""))):  # 到了今天的上限：不理要点、不问赛博
        view = await butler.consult(butler.consultable()[0], m["text"], peer=friend["id"], peer_name=asker.get("name") or friend["name"], ref=f"fg:{gid}",
                                    also=[x["name"] for x in ro["members"] if not x["me"]])  # 要点里成员表上的名字都藏掉（不是我好友的也算）
        if view:
            material.append({"id": f"view:{view['cyber']}", "kind": "view", "title": LS(f"{view['name']} 的看法", f"{view['name']}'s view"),
                             "text": view["view"]})
    audience = [tier_here(x["kid"]) for x in ro["members"] if not x["me"] and x["kid"] != m["sender"]]  # 答的话全群都看得到：按最紧的那一档答
    out = await cardagent.answer(friend, m["text"], channel="group", material=material, history=history_of(gid, rid, ro), ref=f"fg:{gid}",
                                 kid=m["sender"], name=asker.get("name") or "", audience=audience)
    text = (out.get("text") or "").strip()
    if not text:
        return None
    if view and f"view:{view['cyber']}" in (out.get("used") or []) and view["name"] not in text:
        # 用了赛博的看法：写明是谁的（固定的一句，跟着问的人的语言）
        text += f"\n（{view['name']} 的看法）" if cardagent.lang_of(m["text"]) == "zh" else f"\n({view['name']}'s view)"
    meta = {"log": out.get("log_id"), "sentinel": out.get("sentinel"), **({"card": out["defer"]["inbox_id"]} if (out.get("defer") or {}).get("inbox_id") else {}),
            **({"view": {"cyber": view["cyber"], "name": view["name"], "consult": view["id"]}} if view else {})}
    return say(gid, text[:TEXT_MAX], "butler", meta=meta)


async def deliver_group(ask: dict, text: str, data: dict) -> bool:
    """cardagent.DELIVER["group"]：你在卡上点完（约时间、Doorman 扣下的那句），结果由你的管家发回那个群。"""
    gid = str(ask.get("ref") or "").removeprefix("fg:")
    text = str(text or "").strip()[:TEXT_MAX]
    if not GID_RE.match(gid) or not text:
        return False
    try:
        with _lock, gdb() as conn:
            g = group_row(conn, gid)
        ro = roster_of(g)
        if g["status"] != "active" or not my_butler_in(ro):
            return False
        say(gid, text, "butler", meta={"outcome": str((data or {}).get("outcome") or "")[:20]})
    except (HTTPException, ValueError):
        return False
    p = (data or {}).get("proposal") or {}
    eid = prep_episode(gid, ro)
    if eid and data and data.get("outcome") == "accepted" and p.get("date") and p.get("start"):
        # 录前讨论里约好的录制时间：进这一期的那条日程（和你自己约的是同一条），cardagent 就不另加一条「见面」
        import podprep
        if sid := await podprep.booked_in_group(eid, p):
            data["scheduled"] = sid
    return True


def register_delivery() -> None:
    import cardagent
    cardagent.DELIVER["group"] = deliver_group


register_delivery()


# —— 收：/f/group ————————————————————————————————————————————————————————————————————————————

def keyid_of(request: Request) -> str | None:
    try:
        inputs = social.parse_dict(request.headers.get("signature-input") or "")
        return dict(inputs[social.SIG_LABEL][1]).get("keyid")
    except (KeyError, ValueError, TypeError, IndexError):
        return None


@public_router.post("/f/group")
async def receive(request: Request):
    """别的成员的服务器投来一份（签过名，见文件开头）：成员表（群主发的）/ 一条消息 / 一位成员要不要带管家（发给群主）。"""
    if not on():
        return social.err(404, "not_found")
    try:
        body = await social.read_body(request, BODY_MAX)
        p = json.loads(body)
    except HTTPException as e:
        return social.err(e.status_code, str(e.detail))
    except ValueError:
        return social.err(400, "bad_request")
    if not isinstance(p, dict) or p.get("v") != 1 or p.get("type") not in ("roster", "msg", "butler"):
        return social.err(400, "bad_request")  # 老服务器不认的新 type 也是 400：新加的先看对方 caps 再发
    try:
        ro = await asyncio.to_thread(verify_roster, p.get("roster"))
    except (ValueError, TypeError):
        return social.err(400, "bad_roster")
    kid = keyid_of(request)
    with _lock, gdb() as conn:
        have = conn.execute("SELECT * FROM friend_groups WHERE id=?", (ro["group"],)).fetchone()
    if have is not None and have["owner"] != ro["owner"]:
        return social.err(403, "not_owner")  # 同一个群 id、换了群主签的：不认（消息、管家请求里夹着的也一样，见 store_roster）
    cur = ro
    if have is not None and have["version"] > ro["version"]:
        try:
            cur = roster_of(have)  # 我手上的更新：按它认人
        except ValueError:
            cur = ro
    sender = member_of(cur, kid or "") if kid else None
    if sender is None or sender["me"]:
        return social.err(401, "not_a_member")
    try:
        await asyncio.to_thread(social.verify_signed, request, body, sender["x"])  # 用成员表上那位的公钥验：不是我好友的成员也认得出
    except HTTPException as e:
        return social.err(e.status_code, str(e.detail))
    import friends
    if not friends.rate_ok(kid or ""):
        return social.err(429, "slow_down", retry_after=60)
    if p["type"] == "roster":
        if kid != ro["owner"]:
            return social.err(403, "not_owner")
        fresh = await asyncio.to_thread(store_roster, ro)
        if fresh and have is None:
            log_activity(LS(f"{sender['name']} 拉你进了好友群「{ro['title']}」", f'{sender["name"]} added you to the friend group "{ro["title"]}"'), "social", actor=sender["name"])
        return {"ok": True, "version": ro["version"]}
    # 消息里带的成员表也是群主签的：比我手上的新就换上（先到的是消息、成员表那份还在路上，也照样能用）；旧的 store_roster 不理
    await asyncio.to_thread(store_roster, ro)
    if p["type"] == "butler":
        return await on_butler_request(cur, sender, p)
    m = p.get("msg")
    if not isinstance(m, dict):
        return social.err(400, "bad_request")
    mid, who, text = str(m.get("id") or ""), m.get("who"), str(m.get("text") or "")[:TEXT_MAX]
    if not MID_RE.match(mid) or who not in ("person", "butler") or not text.strip():
        return social.err(400, "bad_request")
    if who == "butler" and not sender["butler"]:
        return social.err(403, "no_butler")  # 成员表上他没带管家：不认「管家说的」
    reply_to = m.get("reply_to") if isinstance(m.get("reply_to"), str) and MID_RE.match(m["reply_to"]) else None
    at = sent_at(m.get("at"))
    rid = await asyncio.to_thread(add_msg, ro["group"], mid, kid or "", who, text, at=at, dir_="in", reply_to=reply_to)
    if rid is None:
        return {"ok": True, "id": mid, "dup": True}
    if who == "person" and (mentions_me(text, cur) or prep_episode(ro["group"], cur) and proposes(text)):
        # 我的管家答（零工具、过 Doorman）；好友的话不进主对话、不驱动工具。录前讨论里管家兼主持：有人提了时间也接（约录制时间先出卡给你）
        run_on_main(butler_reply(ro["group"], rid))
    return {"ok": True, "id": mid, "dup": False}


def sent_at(v) -> str:
    """消息上对方报的发送时间（app 拿它显示）：只认 40 字以内、带时区的 ISO 时间，别的用收到的时间。"""
    if isinstance(v, str) and len(v) <= 40:
        try:
            if datetime.fromisoformat(v).tzinfo is not None:
                return v
        except ValueError:
            pass
    return now_iso()


async def on_butler_request(ro: dict, sender: dict, p: dict) -> dict:
    """一位成员说他的管家进 / 不进这个群：只有群主改表（版本加一、重签），再发给每一位。跟表上一样的：回现在的版本，不重签、不群发。"""
    if ro["owner"] != social.my_kid():
        return social.err(403, "not_owner")
    want = p.get("butler") is True
    if want == bool(sender["butler"]):
        return {"ok": True, "version": ro["version"]}
    members = [{k: v for k, v in m.items() if k != "me"} | ({"butler": want} if m["kid"] == sender["kid"] else {}) for m in ro["members"]]
    new = await asyncio.to_thread(sign_roster, ro["group"], ro["title"], members, ro["version"] + 1)
    nr = verify_roster(new)
    await asyncio.to_thread(store_roster, nr)
    queue(ro["group"], {"v": 1, "type": "roster", "roster": new}, nr)
    return {"ok": True, "version": nr["version"]}


# —— app ——————————————————————————————————————————————————————————————————————————————————

def candidate_json(f: dict) -> dict:
    ok = CAP in (f.get("caps") or [])
    return {"id": f["id"], "name": f["name"], "can": ok,
            "why": None if ok else L(f"{f['name']} 的 OpenMousse 版本旧，还不能进好友群，只能一对一聊", f"{f['name']}'s OpenMousse is too old for friend groups; one-to-one only")}


@router.get("/api/friendgroups/candidates")
def candidates():
    return {"ok": True, "friends": [candidate_json(f) for f in social.friends()],
            "note": L("有好友在：消息端到端加密；你这边只出一位管家，它需要时去问你的赛博；要执行的事只发给你确认。",
                      "With friends here, messages are end-to-end encrypted; only your butler speaks for you, asks your Cybers when it needs to, and sends anything to act on to you to confirm.")}


class GroupIn(BaseModel):
    title: str
    friends: list[str]


@router.post("/api/friendgroups")
def create(body: GroupIn):
    """建群：你是群主，你的管家主持（成员表上你带管家）；好友的管家由他们自己决定加不加入。"""
    gid = new_group(body.title, body.friends)
    with _lock, gdb() as conn:
        r = group_row(conn, gid)
    return {"ok": True, "group": group_json(r)}


def new_group(title: str, friend_ids: list[str], *, source: dict | None = None) -> str:
    """建群（建群接口、播客多人对谈的录前讨论都走这里）：签成员表、存下、给每位好友排一份。source = 只在我这边的来源。→ 群 id。"""
    title = clean(title, TITLE_MAX)
    if not title:
        raise HTTPException(400, L("群名不能空", "The name can't be empty"))
    picked = list(dict.fromkeys(friend_ids))
    if not 1 <= len(picked) <= MEMBERS_MAX - 1:
        raise HTTPException(400, L(f"好友群要 1–{MEMBERS_MAX - 1} 位好友", f"A friend group needs 1–{MEMBERS_MAX - 1} friends"))
    fs = []
    for fid in picked:
        f = social.friend(fid)
        if not f or f["status"] != "active":
            raise HTTPException(400, L(f"不是你的好友：{fid}", f"Not your friend: {fid}"))
        if CAP not in (f.get("caps") or []):
            raise HTTPException(409, candidate_json(f)["why"])
        fs.append(f)
    if not social.my_url():
        raise HTTPException(409, L("这台服务器还没有对外地址，好友连不回来", "This server has no public address yet, so friends can't reach it"))
    gid = f"fg-{uuid.uuid4().hex[:16]}"
    roster = sign_roster(gid, title, [me_entry(True)] + [friend_entry(f) for f in fs], 1)
    ro = verify_roster(roster)
    store_roster(ro)
    if source:
        with _lock, gdb() as conn:
            conn.execute("UPDATE friend_groups SET source=? WHERE id=?", (json.dumps(source, ensure_ascii=False), gid))
    queue(gid, {"v": 1, "type": "roster", "roster": roster}, ro)
    log_activity(L(f"建了好友群「{title}」（{'、'.join(f['name'] for f in fs)}）", f'Started the friend group "{title}" ({", ".join(f["name"] for f in fs)})'), "social")
    return gid


@router.get("/api/friendgroups")
def list_groups():
    """好友一边的群（新的在前）：带最后一句（只在我这边的灰字不算）和未读（别人发来、我还没看到的）。"""
    with _lock, gdb() as conn:
        rows = conn.execute("SELECT * FROM friend_groups WHERE status='active' ORDER BY updated_at DESC").fetchall()
        lasts = {r["id"]: conn.execute("SELECT * FROM friend_group_msgs WHERE grp=? AND who != 'system' ORDER BY id DESC LIMIT 1", (r["id"],)).fetchone()
                 for r in rows}
        unread = {r["id"]: conn.execute("SELECT COUNT(*) FROM friend_group_msgs WHERE grp=? AND dir='in' AND id>?", (r["id"], r["seen"])).fetchone()[0]
                  for r in rows}
    out = []
    for r in rows:
        try:
            out.append(group_json(r, last=lasts.get(r["id"]), unread=unread.get(r["id"], 0)))
        except ValueError:
            continue  # 成员表坏了（群主不再是好友之类）：不列
    return {"ok": True, "groups": out}


def cards_of(rows) -> list[dict]:
    """我的管家在这个群里出给我的卡（约时间、Doorman 扣下的一句）：收件箱的样子（app 用 InboxCard 画），带 groupMessage（挂在哪一条下面）。
    只在我这边：成员看不到。收件箱里删了的不列。"""
    import inbox
    mine = social.my_kid()
    out = []
    for r in rows:
        card = json.loads(r["meta"] or "{}").get("card") if r["sender"] == mine and r["who"] == "butler" else None
        if not card:
            continue
        try:
            out.append({**inbox.item(card), "groupMessage": r["id"]})
        except HTTPException:
            continue
    return out


@router.get("/api/friendgroups/{gid}")
def get_group(gid: str, after: int = 0):
    """一个群：成员、消息（after = 只要这条之后的，app 轮询用）、只给我的卡、请教过哪位赛博（仅你可见）。"""
    import butler
    with _lock, gdb() as conn:
        r = group_row(conn, gid)
        rows = conn.execute("SELECT * FROM friend_group_msgs WHERE grp=? AND id>? ORDER BY id DESC LIMIT 300", (gid, max(0, after))).fetchall()
        mine = conn.execute("SELECT * FROM friend_group_msgs WHERE grp=? AND who='butler' AND sender=? ORDER BY id DESC LIMIT 50",
                            (gid, social.my_kid())).fetchall()
    ro = roster_of(r)
    out = {"ok": True, "group": group_json(r), "messages": [msg_json(x, ro) for x in reversed(rows)], "cards": cards_of(reversed(mine)),
           "consults": butler.consults(limit=20, ref=f"fg:{gid}")["items"]}
    src = source_of(gid)
    if src.get("kind") == "podcast" and src.get("id"):  # 录前讨论：「放进」的卡（只给你看）
        import podprep
        out["offers"] = podprep.offers_of(str(src["id"]))
    return out


class ReadIn(BaseModel):
    upto: int


@router.post("/api/friendgroups/{gid}/read")
def read(gid: str, body: ReadIn):
    """看到了这一条（和之前的）：未读清掉。只在我这边，不发已读回执。"""
    with _lock, gdb() as conn:
        group_row(conn, gid)
        conn.execute("UPDATE friend_groups SET seen=MAX(seen, ?) WHERE id=?", (max(0, body.upto), gid))
    return {"ok": True}


class SendIn(BaseModel):
    text: str


@router.post("/api/friendgroups/{gid}/send")
def send(gid: str, body: SendIn):
    text = (body.text or "").strip()[:TEXT_MAX]
    if not text:
        raise HTTPException(400, L("空消息", "Empty message"))
    return {"ok": True, "message": say(gid, text)}


class GroupPatch(BaseModel):
    butler: bool | None = None


@router.patch("/api/friendgroups/{gid}")
def patch(gid: str, body: GroupPatch):
    """你的管家进不进这个群：群主的一直在（它主持）；别人的群发一条 butler 给群主，等群主改表。"""
    with _lock, gdb() as conn:
        r = group_row(conn, gid)
    ro = roster_of(r)
    if body.butler is not None:
        if ro["owner"] == social.my_kid():
            raise HTTPException(409, L("你是群主：你的管家主持这个群，一直在", "You own this group: your butler hosts it and stays in"))
        owner = member_of(ro, ro["owner"])
        assert owner is not None
        with _lock, gdb() as conn:
            conn.execute("INSERT INTO friend_group_out(grp, to_kid, payload, created_at) VALUES(?,?,?,?)",
                         (gid, owner["kid"], json.dumps({"v": 1, "type": "butler", "roster": ro["raw"], "butler": body.butler}, ensure_ascii=False), now_iso()))
        kick()
    with _lock, gdb() as conn:
        r = group_row(conn, gid)
    return {"ok": True, "group": group_json(r), "pending": body.butler is not None}
