"""邮件触发（第 4 批，7.12）：盯一个 IMAP 邮箱，新来的信只进零工具的那一读（engine/triggers.deliver，kind mail），出通知或卡。
只用标准库 imaplib（不加依赖）：EXAMINE（只读地打开：连「已读」都不会改）、取信用 BODY.PEEK、按 UIDVALIDITY + UID 记到哪了；
第一次（换了邮箱、UIDVALIDITY 变了也是）只记下现在到哪了（baseline：以前的信一封都不读）。从不发信、从不改标记、从不删、从不移动。

配置（server.json engine.triggers.mail.<名字>，默认没有 = 关）：
  host · port（默认 993）· tls（ssl 默认 / starttls：不加密的连法不认，密码不能明着走）· user · password（只能写 ${变量名}，值放 secrets.env）
  · folder（默认 INBOX）· interval（多久看一次，秒，默认 INTERVAL，最少 INTERVAL_MIN）
  · known（认识的寄件人：完整地址 a@example.com 或整个域名 @example.com）· authserv_id（你的收信服务器在 Authentication-Results 里
    写的名字，比如 mx.example.com：只认它写的结果）· others（不认识的怎么办：ignore 默认 / notify）
  · agent · thread · label · action（notify 默认 / card：信是别人的话，从不直接叫醒 Agent，没有 agent / job）· say · task · level · limit（一小时最多读几封）
认识 = 只有一个 From、一个地址，它在 known 里，而且最上面那条由 authserv_id 写的 Authentication-Results 说 dmarc=pass、header.from 和寄件人的
  域名对得上（自己伪造一条 Authentication-Results 的，authserv_id 对不上；收信服务器没加这一条的，当不认识）。
  没写 known：每封新信照 action；写了：认识的照 action，不认识的照 others。
和 channels.email（第 6 批的邮件对话）配的是同一个邮箱（imap 的 host、user、folder 一样）：这个触发不跑，GET /api/engine/triggers 里写着为什么
  （同一封信不该既当对话又当通知）。这里不导入第 6 批的代码；Reader 是读信的那一层，第 6 批要的话可以照这个口子换掉或共用。
一次最多读 MAX_BATCH 封（停机久了攒下一堆：只读最新的，前面的记一条跳过了几封）；读了的就往后记（读的时候模型出错也往后记，
  免得一封坏信卡住后面的；事件表里有这一条）；花费到了上限不往后记，下次接着读。急停着不连、不取。
服务里一个循环（start()），每 TICK 秒看一遍哪个邮箱到点了；claw 不是 builtin 时只转不连。
只读地打开、UIDVALIDITY + UID 的记法和第一次只记到哪了、认寄件人（matches、dmarc_pass）改写自 OpenClaw 的 extensions/imap/src/watcher.ts、
state.ts、sender-gate.ts，openclaw/openclaw commit 1099ccc，MIT，Copyright (c) 2026 OpenClaw Foundation；许可全文见仓库根目录的 THIRD_PARTY_NOTICES。
"""
from __future__ import annotations

import asyncio
import contextlib
import email
import email.policy
import hashlib
import imaplib
import re
import ssl
import time
from email.utils import getaddresses
from typing import Any

from fastapi import APIRouter, Depends, Request

from i18n import L

from . import estop, models, store, triggers

PORT, INTERVAL, INTERVAL_MIN = 993, 300, 60
TICK, START_DELAY = 30.0, 15.0
TIMEOUT = 30
MAX_BATCH = 20                # 一次最多读几封
MAX_BYTES = 2 * 1024 * 1024   # 一封信最多取多少字节（BODY.PEEK[]<0.N>：大附件不整个下）
LIMIT = 30                    # 一小时最多读几封
VAR = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")
NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")
SCHEMA = """
CREATE TABLE IF NOT EXISTS engine_mail_state (
  name TEXT PRIMARY KEY, what TEXT NOT NULL, uidvalidity TEXT, last_uid INTEGER, at TEXT, checked_at TEXT, error TEXT);
"""
_ready: set[str] = set()
_bg: set[asyncio.Task] = set()
_next: dict[str, float] = {}     # 名字 → 下一次什么时候看（monotonic）


def ready() -> None:
    from config import settings
    path = str(settings.db)
    if path in _ready:
        return
    with store.tx() as conn:
        conn.executescript(SCHEMA)
    _ready.add(path)


# —— 配置 ————————————————————————————————————————————————————————————————————

def entries() -> dict:
    v = triggers.cfg().get("mail")
    return v if isinstance(v, dict) else {}


def _int(v: Any, default: int, lo: int, hi: int) -> int:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return default
    return int(min(max(v, lo), hi))


def clean(name: str, e: Any) -> dict:
    """配置里的一项 → 整理好的 {…, "problem"}（配不对的一句；有它就不连）。不抛。"""
    import bridges
    m: dict[str, Any] = {"name": name, "problem": None}
    problems: list[str] = []
    if not NAME.match(name):
        problems.append(L("名字只能是小写字母、数字、- 和 _", "The name can only have lowercase letters, digits, - and _"))
    if not isinstance(e, dict):
        return {**m, "problems": [L("要写成一个对象", "It must be an object")], "problem": L("要写成一个对象", "It must be an object")}
    m["host"] = str(e.get("host") or "").strip()
    m["user"] = str(e.get("user") or "").strip()
    if not m["host"] or not m["user"]:
        problems.append(L("要写 host 和 user", "host and user are required"))
    m["port"] = _int(e.get("port"), PORT, 1, 65535)
    m["tls"] = str(e.get("tls") or "ssl").strip().lower()
    if m["tls"] not in ("ssl", "starttls"):
        problems.append(L("tls 写 ssl（默认，993）或 starttls：不加密的连法不认", "tls is ssl (default, port 993) or starttls: unencrypted connections aren't allowed"))
    vm = VAR.match(str(e.get("password") or "").strip())
    m["var"] = vm.group(1) if vm else None
    m["password"] = models.env_value(m["var"]) if m["var"] else ""
    unset = None
    if not m["var"]:
        problems.append(L("password 只能写成 ${变量名}，值放进 secrets.env", "password must be written as ${VARIABLE}, with the value in secrets.env"))
    elif not m["password"]:
        unset = L(f"还没有 {m['var']} 这个密码的值", f"{m['var']} has no value yet")
    m["folder"] = str(e.get("folder") or "INBOX").strip() or "INBOX"
    m["interval"] = _int(e.get("interval"), INTERVAL, INTERVAL_MIN, 86400)
    known = e.get("known")
    m["known"] = [str(x).strip() for x in known if isinstance(x, str) and "@" in x] if isinstance(known, list) else []
    m["authserv_id"] = str(e.get("authserv_id") or "").strip().lower() or None
    if m["known"] and not m["authserv_id"]:
        problems.append(L("写了 known 就要写 authserv_id（你的收信服务器在 Authentication-Results 里的名字）：不然认不出寄件人是不是冒充的",
                          "known needs authserv_id (your mail server's name in Authentication-Results): otherwise a forged sender can't be told apart"))
    m["others"] = str(e.get("others") or "ignore").strip().lower()
    if m["others"] not in ("ignore", "notify"):
        problems.append(L("others 写 ignore 或 notify", "others is ignore or notify"))
    m["action"] = str(e.get("action") or "notify").strip().lower()
    if m["action"] not in ("notify", "card"):
        problems.append(L("邮件的 action 只能是 notify 或 card：信是别人的话，从不直接叫醒 Agent",
                          "A mail trigger's action is notify or card: mail is someone else's text and never wakes an Agent directly"))
    m["agent"] = str(e.get("agent") or "main").strip()
    if not bridges.known_agent(m["agent"]):
        problems.append(L(f"没有「{m['agent']}」这个 Agent", f"No Agent called {m['agent']}"))
    m["thread"] = str(e.get("thread") or "").strip() or None
    m["label"] = " ".join(str(e.get("label") or name).split())[:40] or name
    m["say"] = str(e.get("say") or "").strip()[:1000] or None
    m["task"] = str(e.get("task") or "").strip()[:2000] or None
    m["level"] = e.get("level") if e.get("level") in triggers.LEVELS else "quiet"
    m["limit"] = _int(e.get("limit"), LIMIT, 1, 500)
    m["clash"] = clash(m) if m["host"] and m["user"] else None
    m["problems"] = problems
    m["problem"] = L("；", "; ").join(problems + ([unset] if unset else [])) or None
    return m


def what_of(m: dict) -> str:
    """哪个邮箱（换了就重新 baseline）。"""
    return hashlib.sha256(f"{m['host'].lower()}|{m['port']}|{m['user'].lower()}|{m['folder']}".encode()).hexdigest()[:24]


def clash(m: dict) -> str | None:
    """第 6 批的邮件对话（channels.email 开着的账号，一个或 accounts 里几个）接的是不是同一个邮箱 → 说明一句；不是 → None。
    和邮件对话登录时一样认：host、登录名（没写 imap.user 的用 address）、文件夹。"""
    from channels import accounts
    for acc in accounts.enabled("email"):
        imap = acc.get("imap") if isinstance(acc.get("imap"), dict) else {}
        user = str(imap.get("user") or acc.get("address") or "").strip().lower()
        same = (str(imap.get("host") or "").strip().lower() == m["host"].strip().lower() and user == m["user"].strip().lower()
                and (str(imap.get("folder") or "INBOX").strip() or "INBOX") == m["folder"])
        if same:
            return L("这个邮箱已经接成了邮件对话（channels.email）：同一封信不该既当对话又当通知，这个触发不跑",
                     "This mailbox is already set up as an email chat (channels.email): the same mail shouldn't be both a chat and a "
                     "notice, so this trigger doesn't run")
    return None


# —— 读信那一层（imaplib；测试换成假的）————————————————————————————————————————————————

class Reader:
    """一个邮箱的只读连接。open() → UIDVALIDITY；只用 LOGIN、EXAMINE、UID SEARCH、UID FETCH (BODY.PEEK…)、LOGOUT：
    不发 STORE / COPY / MOVE / EXPUNGE / APPEND，也没有发信的那一半。"""

    def __init__(self, m: dict) -> None:
        self.m = m
        self.conn: imaplib.IMAP4 | None = None

    def open(self) -> str:
        m, ctx = self.m, ssl.create_default_context()
        if m["tls"] == "starttls":
            self.conn = imaplib.IMAP4(m["host"], m["port"], timeout=TIMEOUT)
            self.conn.starttls(ssl_context=ctx)
        else:
            self.conn = imaplib.IMAP4_SSL(m["host"], m["port"], ssl_context=ctx, timeout=TIMEOUT)
        self.conn.login(m["user"], m["password"])
        folder = m["folder"]
        typ, _ = self.conn.select(f'"{folder}"' if " " in folder else folder, readonly=True)  # readonly → EXAMINE
        if typ != "OK":
            raise RuntimeError(f"can't open the folder {folder!r}")
        v = (self.conn.response("UIDVALIDITY")[1] or [None])[-1]
        return v.decode() if isinstance(v, bytes) else str(v or "")

    def uids(self, after: int | None = None) -> list[int]:
        """这个文件夹里的 UID（after 给了只要比它大的；IMAP 的 n:* 在最后一封比 n 小时也会回它，这里再筛一遍）。"""
        assert self.conn is not None
        typ, data = self.conn.uid("SEARCH", None, "ALL" if after is None else f"UID {after + 1}:*")
        if typ != "OK":
            raise RuntimeError("UID SEARCH failed")
        out = sorted({int(x) for x in b" ".join(d for d in data if d).split() if x.isdigit()})
        return [u for u in out if after is None or u > after]

    def fetch(self, uid: int) -> bytes:
        """一封信的原文（最多 MAX_BYTES 字节）。BODY.PEEK：不会把它标成已读。"""
        assert self.conn is not None
        typ, data = self.conn.uid("FETCH", str(uid), f"(BODY.PEEK[]<0.{MAX_BYTES}>)")
        if typ != "OK":
            raise RuntimeError(f"UID FETCH {uid} failed")
        for part in data:
            if isinstance(part, tuple) and len(part) >= 2 and isinstance(part[1], bytes):
                return part[1]
        return b""

    def close(self) -> None:
        if self.conn is not None:
            with contextlib.suppress(Exception):
                self.conn.logout()
            self.conn = None


READER = Reader   # 测试换掉：假的邮箱，不连网


# —— 认不认识 ——————————————————————————————————————————————————————————————————

def matches(sender: str, entries_: list[str]) -> bool:
    """寄件人在不在 known 里：@域名（不分大小写）或者完整地址（@ 前面照原样比，域名不分大小写）。照 OpenClaw 的 matchesImapSender。"""
    at = sender.rfind("@")
    if at < 1:
        return False
    local, domain = sender[:at], sender[at + 1:].lower()
    for e in entries_:
        if e.startswith("@"):
            if e[1:].lower() == domain:
                return True
            continue
        ea = e.rfind("@")
        if ea > 0 and e[:ea] == local and e[ea + 1:].lower() == domain:
            return True
    return False


def sender_of(msg: email.message.Message) -> str | None:
    """唯一的那个寄件人地址；From 不止一个、一个 From 里写了几个地址、没有地址 → None（当不认识）。"""
    froms = msg.get_all("From") or []
    if len(froms) != 1:
        return None
    addrs = [a for _, a in getaddresses([str(froms[0])]) if a]
    return addrs[0] if len(addrs) == 1 and "@" in addrs[0] else None


def dmarc_pass(msg: email.message.Message, authserv_id: str, sender: str) -> bool:
    """最上面那条 authserv_id 写的 Authentication-Results：dmarc=pass，header.from 写了的话和寄件人的域名一样。"""
    domain = sender.rsplit("@", 1)[-1].lower()
    for v in msg.get_all("Authentication-Results") or []:
        text = " ".join(str(v).split())
        head, sep, methods = text.partition(";")
        if not sep or re.sub(r"\s+\d+$", "", head.strip()).lower() != authserv_id:
            continue
        for seg in methods.split(";"):
            s = seg.strip()
            if re.match(r"dmarc\s*=", s, re.I):
                ok = re.match(r"dmarc\s*=\s*pass\b", s, re.I) is not None
                hf = re.search(r"header\.from\s*=\s*([^\s;]+)", s, re.I)
                return ok and (hf is None or hf.group(1).strip().lower().rstrip(".") == domain)
        return False  # 我们的收信服务器写的这一条里没有 dmarc：不认
    return False


def known(m: dict, msg: email.message.Message) -> bool:
    s = sender_of(msg)
    return bool(s and m["known"] and m["authserv_id"] and matches(s, m["known"]) and dmarc_pass(msg, m["authserv_id"], s))


def content_of(msg: email.message.Message) -> str:
    """给零工具那一读的：几个信头 + 正文（纯文本优先；只有 HTML 的转成好读的字）+ 附件的名字。"""
    from . import web

    def h(k: str) -> str:
        return " ".join(str(msg.get(k) or "").split())[:300]
    body = ""
    try:
        part = msg.get_body(preferencelist=("plain", "html")) if hasattr(msg, "get_body") else None
        if part is not None:
            text = part.get_content()
            body = web.readable(text, "")[1] if part.get_content_type() == "text/html" else str(text)
    except Exception:  # noqa: BLE001 — 编码坏了：只给信头
        body = ""
    files = []
    with contextlib.suppress(Exception):
        files = [p.get_filename() for p in msg.iter_attachments() if p.get_filename()] if hasattr(msg, "iter_attachments") else []
    head = f"From: {h('From')}\nTo: {h('To')}\nDate: {h('Date')}\nSubject: {h('Subject')}\n"
    if files:
        head += "Attachments: " + ", ".join(str(f)[:80] for f in files[:10]) + "\n"
    return triggers.clip(head + "\n" + body.strip(), triggers.STORE_MAX)


# —— 记到哪了 ——————————————————————————————————————————————————————————————————

def state(name: str) -> dict | None:
    ready()
    with store.read() as conn:
        r = conn.execute("SELECT * FROM engine_mail_state WHERE name=?", (name,)).fetchone()
    return dict(r) if r else None


def _save(name: str, **cols: Any) -> None:
    ready()
    cur = state(name)
    with store.tx() as conn:
        if cur is None:
            conn.execute("INSERT INTO engine_mail_state(name, what) VALUES(?, ?)", (name, cols.get("what") or ""))
        sets = {**cols, "checked_at": store.now_iso()}
        conn.execute("UPDATE engine_mail_state SET " + ", ".join(f"{k}=?" for k in sets) + " WHERE name=?", (*sets.values(), name))


# —— 看一遍 ——————————————————————————————————————————————————————————————————

def _pull(m: dict, st: dict | None) -> dict:
    """（在线程里）连上、只读地看一眼 → {"uidvalidity", "baseline": 有没有重新记, "last": 记到哪, "mail": [(uid, 原文)], "skipped": 跳过几封}。"""
    r = READER(m)
    try:
        uv = r.open()
        what = what_of(m)
        if st is None or st.get("what") != what or str(st.get("uidvalidity") or "") != uv or st.get("last_uid") is None:
            all_ = r.uids()
            return {"uidvalidity": uv, "what": what, "baseline": True, "last": all_[-1] if all_ else 0, "mail": [], "skipped": 0}
        last = int(st["last_uid"])
        new = r.uids(last)
        skipped = max(0, len(new) - MAX_BATCH)
        take = new[-MAX_BATCH:]
        return {"uidvalidity": uv, "what": what, "baseline": False, "last": take[0] - 1 if take else last, "skipped": skipped,
                "mail": [(u, r.fetch(u)) for u in take]}
    finally:
        r.close()


async def check(name: str, m: dict | None = None) -> dict:
    """看一遍这个邮箱（见文件开头）→ {"status": paused / clash / problem / baseline / ok / error, "read", "error"?}。"""
    m = m or clean(name, entries().get(name))
    if estop.paused():
        return {"status": "paused", "read": 0}
    if m.get("problem"):
        return {"status": "problem", "read": 0, "error": m["problem"]}
    if m.get("clash"):
        return {"status": "clash", "read": 0, "error": m["clash"]}
    st = state(name)
    try:
        got = await asyncio.to_thread(_pull, m, st)
    except Exception as e:  # noqa: BLE001 — 连不上、登录不了、文件夹没有：记下来，下次再试
        why = f"{type(e).__name__}: {str(e)[:200]}"
        _save(name, what=(st or {}).get("what") or what_of(m), error=why)
        return {"status": "error", "read": 0, "error": why}
    if got["baseline"]:
        _save(name, what=got["what"], uidvalidity=got["uidvalidity"], last_uid=got["last"], at=store.now_iso(), error=None)
        return {"status": "baseline", "read": 0}
    source = f"mail:{name}"
    if got["skipped"]:  # 攒下太多：前面的不读了（记到最新那几封的前面）
        triggers.record(source, "mail", event="skipped", status="ignored", summary=L(f"一次来了太多封：前面 {got['skipped']} 封没读，只读最新的",
                                                                                   f"Too many at once: the {got['skipped']} older ones weren't read"))
        _save(name, what=got["what"], uidvalidity=got["uidvalidity"], last_uid=got["last"], error=None)
    last, done = got["last"], 0
    for uid, raw in got["mail"]:
        if triggers.hits(source) >= m["limit"]:
            break  # 这一小时读够了：不往后记，下次接着读
        st_ = await one(name, m, got["uidvalidity"], uid, raw)
        if st_ == "limit":
            break  # 花费到了上限：不往后记，下次接着读
        last, done = uid, done + 1
        _save(name, what=got["what"], uidvalidity=got["uidvalidity"], last_uid=last, at=store.now_iso(), error=None)
    if not got["mail"]:
        _save(name, what=got["what"], uidvalidity=got["uidvalidity"], last_uid=last, error=None)
    return {"status": "ok", "read": done}


async def one(name: str, m: dict, uidvalidity: str, uid: int, raw: bytes) -> str:
    """一封新信：认不认识 → 记一件 → 照设置做（零工具的读、通知 / 卡）。→ 最后的状态。"""
    source = f"mail:{name}"
    try:
        msg = email.message_from_bytes(raw, policy=email.policy.default)
    except Exception:  # noqa: BLE001 — 解析不了的信：跳过（每次都一样解析不了，别卡住后面的）
        triggers.record(source, "mail", delivery=f"{uidvalidity}:{uid}", event="unreadable", status="ignored", summary="couldn't parse")
        return "ignored"
    is_known = known(m, msg)
    action = m["action"] if (is_known or not m["known"]) else ("notify" if m["others"] == "notify" else None)
    event = "known" if is_known else "unknown" if m["known"] else "mail"
    if action is None:
        triggers.record(source, "mail", delivery=f"{uidvalidity}:{uid}", event=event, status="ignored",
                        summary=L("不认识的寄件人（others: ignore）", "unknown sender (others: ignore)"))
        return "ignored"
    spec = {"source": source, "kind": "mail", "agent": m["agent"], "thread": m["thread"], "action": action, "say": m["say"], "task": m["task"],
            "level": m["level"], "label": m["label"], "trust": None, "event": event}
    content = content_of(msg)
    eid = triggers.record(source, "mail", delivery=f"{uidvalidity}:{uid}", event=event, data={"spec": spec, "content": content})
    if eid is None:  # 这封以前读过：上次没做成（花费到了上限、出错、重启时被打断）就再做一次，做成了的才是重复
        eid = triggers.again(source, f"{uidvalidity}:{uid}", None)
        if eid is None:
            return "duplicate"
    return await triggers.deliver(eid, spec, content)


# —— 一直转 ——————————————————————————————————————————————————————————————————

async def tick() -> list[str]:
    """看一遍哪个邮箱到点了（每个按自己的 interval）。→ 这一遍看了的名字。只在 builtin、没急停时连。"""
    import claw
    if not claw.is_builtin() or estop.paused():
        return []
    now = time.monotonic()
    seen: list[str] = []
    for name, e in entries().items():
        m = clean(str(name), e)
        if m["problem"] or m["clash"] or _next.get(name, 0) > now:
            continue
        _next[name] = now + m["interval"]
        await check(str(name), m)
        seen.append(str(name))
    return seen


async def loop() -> None:
    await asyncio.sleep(START_DELAY)
    while True:
        try:
            await tick()
        except Exception as e:  # noqa: BLE001 — 这一遍出错了下一遍接着看
            print(f"[engine] 看邮箱这一遍出错：{type(e).__name__}: {e}")
        await asyncio.sleep(TICK)


def start() -> None:
    """服务启动时（engine/triggers.start）：开始转。配了邮箱才会连（每遍现读配置：加了、改了不用重启）。"""
    t = asyncio.create_task(loop())
    _bg.add(t)
    t.add_done_callback(_bg.discard)


# —— 给 app 看、改配置 ——————————————————————————————————————————————————————————————

def view(m: dict) -> dict:
    st = state(m["name"]) if store.exists() else None
    return {"name": m["name"], "host": m.get("host"), "port": m.get("port"), "tls": m.get("tls"), "user": m.get("user"), "folder": m.get("folder"),
            "password": {"var": m.get("var"), "set": bool(m.get("password"))}, "interval": m.get("interval"), "known": m.get("known"),
            "authservId": m.get("authserv_id"), "others": m.get("others"), "agent": m.get("agent"), "thread": m.get("thread"),
            "label": m.get("label"), "action": m.get("action"), "say": m.get("say"), "task": m.get("task"), "level": m.get("level"),
            "limit": m.get("limit"), "problem": m.get("problem"), "clash": m.get("clash"),
            "lastChecked": (st or {}).get("checked_at"), "lastError": (st or {}).get("error"), "lastUid": (st or {}).get("last_uid")}


def listing() -> list[dict]:
    return [view(clean(str(k), v)) for k, v in entries().items()]


triggers.LISTS["mail"] = listing


def _owner(request: Request) -> str:
    from . import setup_api
    return setup_api.owner(request)


api = APIRouter(dependencies=[Depends(_owner)])
FIELDS = ("host", "port", "tls", "user", "password", "folder", "interval", "known", "authserv_id", "others", "agent", "thread", "label", "action",
          "say", "task", "level", "limit")


@api.put("/api/engine/triggers/mail/{name}")
async def api_put(name: str, request: Request):
    """整项换上（{host, user, password: "${变量名}", …}；见文件开头）。passwordValue 给了就先存进 secrets.env。写不对回 400；差密码的值照存。"""
    import secretstore

    from . import setup_api
    from .setup_api import SetupError
    if not NAME.match(name):
        raise SetupError(400, L("名字只能是小写字母、数字、- 和 _", "The name can only have lowercase letters, digits, - and _"))
    b = await setup_api.body_of(request)
    unknown = set(b) - set(FIELDS) - {"passwordValue"}
    if unknown:
        raise SetupError(400, L(f"不认识的键：{'、'.join(sorted(unknown))}", f"Unknown keys: {', '.join(sorted(unknown))}"))
    entry = {k: b[k] for k in FIELDS if k in b and b[k] is not None}
    vm = VAR.match(str(entry.get("password") or "").strip())
    if vm is None:
        raise SetupError(400, L("password 只能写成 ${变量名}（值放进 secrets.env，或者一起给 passwordValue）",
                                "password must be written as ${VARIABLE} (the value goes in secrets.env, or give it as passwordValue)"), field="password")
    if b.get("passwordValue") not in (None, ""):
        try:
            secretstore.put(vm.group(1), str(b["passwordValue"]))
        except ValueError as e:
            raise SetupError(400, str(e), field="passwordValue") from None
    m = clean(name, entry)
    if m["problems"]:
        raise SetupError(400, L("；", "; ").join(m["problems"]))

    def mutate(d: dict) -> None:
        setup_api.section(d, "engine", "triggers", "mail")[name] = entry
    res = setup_api.edit(mutate)
    _next.pop(name, None)
    return {"ok": True, "mail": view(clean(name, entry)), "changed": res["changed"], "backup": res["backup"]}


@api.delete("/api/engine/triggers/mail/{name}")
async def api_delete(name: str):
    from . import setup_api

    def mutate(d: dict) -> None:
        setup_api.section(d, "engine", "triggers", "mail").pop(name, None)
        setup_api.prune(d, "engine", "triggers", "mail")
    res = setup_api.edit(mutate)
    return {"ok": True, "changed": res["changed"], "backup": res["backup"]}
