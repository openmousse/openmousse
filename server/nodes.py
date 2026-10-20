"""设备节点（第 8 批，2026-10-19，13.1）：主人自己的设备配成「节点」，替 Agent 做服务器做不了的事——Mac 上跑命令、截屏、读写
指定的文件夹，手机定位、拍照、收通知。照 OpenClaw 的节点（docs/nodes/ 的 pairing-and-status、command-policy、node-exec），
传话换成长轮询：节点主动连服务器，Mac、手机上不开端口。Agent 经 nodes 工具用它们（engine/nodes.py，交卡、放行在那边）。

两段批准（OpenClaw 的设备配对 + 命令面）：
  1. 配对    主人出一个节点配对码：python3 tokens.py pair --node（app「设备和节点」里也能出：POST /api/nodes/pair），在要当节点的
             那台电脑上跑 node_host.py pair <链接>（mousse node pair），拿码换一把 node-<设备> 令牌（pairing.py）。main.py 的 guard 只放
             这把令牌走 /api/nodes/*；它不算「设备」（pairing.program_token），本机脚本也不会拿它（config.pick_api_token）。
  2. 命令面  节点连上来（POST /api/nodes/hello）报它能做什么：平台、命令、系统权限、给它看的文件夹。服务器交一张收件箱卡（kind node），
             主人点了头才算数（记在 engine.nodes.<节点>.approved）。之后报得比批过的多，多出来的再交一张；没批之前，批过的、还报着的照旧
             能用（OpenClaw：初次没批的命令面一样都不能用）。拒了的同一组 30 天里不再问（收件箱的规矩）；app 的节点页上能直接批。
             手机（app 用自己的设备令牌当节点，kind phone）不交卡：主人就拿着这台手机，在 app 里点「开」就是批准。
第三道门：隐私的、撤不回的命令（PRIVATE：跑命令、写文件、截屏、拍照、定位、操作电脑）批了命令面也默认关，主人逐项开：
  server.json 的 engine.nodes.<节点>.allow = {"location.get": "allow", "camera.snap": "ask", …}：off 关 / ask 每次问 / allow 直接给。
  system.run 的 allow = 这个节点的放行名单（engine.nodes.<节点>.exec）里的直接跑、别的问，危险的每次都问；computer.act 最多 ask。
  别的命令（通知、看文件夹、读文件、查程序）批了就是 allow，也能写 off 关掉。app 的节点页、「这台手机」都能改（POST …/allow）。
  这里只挡没报、没批、关着的；ask 和 allow 怎么交卡、怎么放行是 nodes 工具的事。

节点那一边（令牌是 node-*，或者手机 app 自己的设备令牌）：
  POST /api/nodes/hello   {platform, commands, permissions, folders, version, name, foreground} → 登记 / 更新；回批了哪些、能用哪些
  GET  /api/nodes/poll    长轮询等活（?wait= 秒，最多 25）→ {"invokes": [{id, command, params, timeoutMs}], "cancel": [不用做了的 id]}
  POST /api/nodes/result  {id, ok, payload | error: {code, message}}：回结果（只认交给它的那几件）
  POST /api/nodes/bye     {reason}：要走了（手机切到后台、节点主机关了）→ 马上算不在线，等着它的活马上报错
服务器这边：invoke(节点, 命令, 参数, 时限) 等结果。节点不在线、手机 app 不在前台、命令没报 / 没批 / 关着：马上抛 NodeError（说得清的一句，
  带稳定的 code），不卡住这一轮。在线 = 有一个长轮询在等，或者 GRACE 秒内来过（节点拿到活马上再来等，所以干活的时候也算在线）；
  交出去的活还没回、它就不再来了 = 连不上了（NODE_LOST）。到点、被取消（用户点了停）：下一次长轮询带上 cancel，节点那边整组收掉。
主人那一边（只认主人的设备，同 engine/setup_api.owner）：GET /api/nodes、GET /api/nodes/{id}、POST /api/nodes/{id}/allow（某一项开到哪一档：
  不要了就 off，不用撤批准——撤了它还报着，就又来问）、POST /api/nodes/{id}/approve（直接批它报了、还没批的）、DELETE /api/nodes/{id}
  （节点主机的令牌一起收回）、POST /api/nodes/pair（出节点配对码）。
存：节点报上来的（命令、权限、文件夹、上次见到）在库里的 nodes 表；主人的决定（批了哪些、各项开到哪一档、跑命令的放行名单）在 server.json：
  "engine": {"nodes": {"agents": ["main"], "node-mac-mini": {"approved": [...], "allow": {...}, "exec": ["/usr/bin/uname", "git status ..."]}}}
开关：不写 engine.nodes（claw 也要是 builtin）就是关：/api/nodes/* 一律 404，nodes 工具也没有。agents：哪些 Agent 有 nodes 工具（默认只有 main）。
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

import claw
import config
import pairing
from config import TZ, raw
from i18n import L

# —— 命令 ———————————————————————————————————————————————————————————————————————
# 节点能报的命令（别的不收：命令面是封闭的）→ (中文, English) 说明。隐私的、撤不回的（PRIVATE）批了也默认关
COMMANDS: dict[str, tuple[str, str]] = {
    "system.notify": ("发一条通知", "Show a notification"),
    "system.which": ("查有没有装某个程序", "Check whether a program is installed"),
    "system.run": ("跑命令", "Run commands"),
    "fs.list": ("看给它的文件夹里有什么", "List files in the folders it shares"),
    "fs.read": ("读给它的文件夹里的文件", "Read files in the folders it shares"),
    "fs.write": ("往给它的文件夹里写文件", "Write files in the folders it shares"),
    "screen.snapshot": ("截屏", "Take a screenshot"),
    "camera.snap": ("拍照", "Take a photo"),
    "location.get": ("看位置", "Get the location"),
    "computer.act": ("操作电脑（点、打字、按键）", "Control the computer (click, type, press keys)"),
}
PRIVATE = ("system.run", "fs.write", "screen.snapshot", "camera.snap", "location.get", "computer.act")
LEVELS = ("off", "ask", "allow")
MAX_LEVEL = {"computer.act": "ask"}   # 每个动作都撤不回：最多「每次问」
PHONE = ("system.notify", "camera.snap", "location.get")   # 手机 app 能报的
RESERVED = {"agents"}                 # engine.nodes 里不是节点的键
WAIT, WAIT_MAX = 20, 25               # 长轮询默认等几秒、最多几秒
GRACE = 8.0                           # 没在等的节点，多久内来过还算在线（拿到活到再来等之间、两次长轮询之间）
INFLIGHT_MAX = 8                      # 一个节点同时最多几件活
BATCH = 4                             # 一次长轮询最多交几件
BODY_MAX = 24 * 1024 * 1024           # 回结果最多多大（截屏、照片 base64 以后）
HELLO_MAX = 256 * 1024
_save_lock = threading.Lock()


class NodeError(Exception):
    """节点做不了（不在线、没批、关着、超时……）：code 稳定（给程序认），str 是给模型、给人看的一句。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def describe_command(name: str) -> str:
    zh, en = COMMANDS.get(name, (name, name))
    return L(zh, en)


# —— 设置（server.json 的 engine.nodes）————————————————————————————————————————————

def cfg() -> dict | None:
    """engine.nodes：没写，或者 claw 不是 builtin → None = 整个功能关着。true = {}。每次现读。"""
    if not claw.is_builtin():
        return None
    e = raw().get("engine")
    v = e.get("nodes") if isinstance(e, dict) else None
    if v is True:
        return {}
    return v if isinstance(v, dict) else None


def on() -> bool:
    return cfg() is not None


def agents() -> list[str]:
    """哪些 Agent 有 nodes 工具（engine.nodes.agents，默认只有 main）。"""
    a = (cfg() or {}).get("agents")
    return [str(x) for x in a if str(x).strip()] if isinstance(a, list) else ["main"]


def entry(nid: str) -> dict:
    v = (cfg() or {}).get(nid)
    return v if isinstance(v, dict) and nid not in RESERVED else {}


def approved(nid: str) -> list[str]:
    a = entry(nid).get("approved")
    return sorted({str(x) for x in a if str(x) in COMMANDS}) if isinstance(a, list) else []


def level(nid: str, command: str) -> str:
    """这一项开到哪一档：主人写的（不认识的当 off），没写的隐私命令 off、别的 allow；computer.act 最多 ask。"""
    a = entry(nid).get("allow")
    v = a.get(command) if isinstance(a, dict) else None
    lv = (str(v).strip().lower() if str(v).strip().lower() in LEVELS else "off") if v is not None else ("off" if command in PRIVATE else "allow")
    cap = MAX_LEVEL.get(command)
    return cap if cap and LEVELS.index(lv) > LEVELS.index(cap) else lv


def exec_allow(nid: str) -> list[str]:
    """这个节点上跑命令的放行名单（服务器这一边；节点主机自己的配置里还有一道）。"""
    v = entry(nid).get("exec")
    return [str(x) for x in v if str(x).strip()] if isinstance(v, list) else []


def update_entry(nid: str, fn) -> dict:
    """改 server.json 的 engine.nodes.<节点>（fn 就地改那个 dict）。整个功能关着 → NodeError。→ 改完的那一项。"""
    with _save_lock:
        data = copy.deepcopy(config.raw(fresh=True))
        e = data.get("engine") if isinstance(data.get("engine"), dict) else {}
        n = e.get("nodes")
        n = {} if n is True else dict(n) if isinstance(n, dict) else None
        if n is None or not claw.is_builtin():
            raise NodeError("NODES_OFF", off_text())
        if nid in RESERVED:
            raise NodeError("NODE_UNKNOWN", L(f"「{nid}」不能当节点的名字", f'"{nid}" can\'t be a node name'))
        cur = copy.deepcopy(n.get(nid)) if isinstance(n.get(nid), dict) else {}
        fn(cur)
        n[nid] = cur
        e["nodes"], data["engine"] = n, e
        config.save(data)
        return cur


def drop_entry(nid: str) -> None:
    with _save_lock:
        data = copy.deepcopy(config.raw(fresh=True))
        e = data.get("engine") if isinstance(data.get("engine"), dict) else {}
        n = e.get("nodes")
        if isinstance(n, dict) and nid in n and nid not in RESERVED:
            n = dict(n)
            del n[nid]
            e["nodes"], data["engine"] = n, e
            config.save(data)


def off_text() -> str:
    return L('节点没开：claw 要是 builtin，并且在 server.json 的 engine 里加上 "nodes": {}。',
             'Nodes are off: the claw must be builtin, with "nodes": {} under engine in server.json.')


# —— 登记（库里的 nodes 表）—————————————————————————————————————————————————————————

def ndb():
    import chat
    conn = chat.db()
    conn.execute("""CREATE TABLE IF NOT EXISTS nodes (id TEXT PRIMARY KEY, kind TEXT NOT NULL, label TEXT NOT NULL DEFAULT '',
        platform TEXT NOT NULL DEFAULT '', version TEXT NOT NULL DEFAULT '', commands TEXT NOT NULL DEFAULT '[]',
        permissions TEXT NOT NULL DEFAULT '{}', folders TEXT NOT NULL DEFAULT '[]', foreground INTEGER, pending TEXT NOT NULL DEFAULT '[]',
        card TEXT, card_hash TEXT, created_at TEXT NOT NULL, seen_at TEXT)""")
    return conn


def _lock():
    import chat
    return chat._lock


def now_iso() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def _list(v) -> list:
    try:
        x = json.loads(v or "[]")
    except ValueError:
        return []
    return x if isinstance(x, list) else []


def _dict(v) -> dict:
    try:
        x = json.loads(v or "{}")
    except ValueError:
        return {}
    return x if isinstance(x, dict) else {}


@dataclass
class Node:
    id: str
    kind: str                 # host（节点主机：Mac、Linux）/ phone（手机 app）
    label: str
    platform: str
    version: str
    commands: list[str]       # 它报的（只留认识的）
    permissions: dict
    folders: list
    foreground: bool | None
    pending: list[str]        # 开着的那张卡上要批的
    card: str | None
    card_hash: str | None
    created_at: str
    seen_at: str | None

    @staticmethod
    def of(r) -> Node:
        return Node(r["id"], r["kind"], safe_label(r["label"]) or label_of(r["id"]), r["platform"], r["version"], [str(x) for x in _list(r["commands"])],
                    _dict(r["permissions"]), _list(r["folders"]), None if r["foreground"] is None else bool(r["foreground"]),
                    [str(x) for x in _list(r["pending"])], r["card"], r["card_hash"], r["created_at"], r["seen_at"])


def safe_label(v) -> str:
    """节点自己报的名字：会写进给模型的话（出错的那一句、nodes 工具的说明），只留字、数字、空格和 .'-，最长 40（审查：名字里能塞指令）。"""
    return " ".join(re.sub(r"[^\w .'-]", " ", str(v or "")).split())[:40].strip()


def label_of(nid: str) -> str:
    """node-mac-mini → mac mini；手机用它的令牌名（device-iphone-15-09291530 → iphone 15）。"""
    if nid.startswith("node-"):
        return nid[5:].replace("-", " ") or nid
    return pairing.device_label(nid)


def get(nid: str) -> Node | None:
    with _lock(), ndb() as conn:
        r = conn.execute("SELECT * FROM nodes WHERE id=?", (nid,)).fetchone()
    return Node.of(r) if r else None


def all_nodes() -> list[Node]:
    with _lock(), ndb() as conn:
        rows = conn.execute("SELECT * FROM nodes ORDER BY created_at").fetchall()
    return [Node.of(r) for r in rows]


def _set(nid: str, **cols) -> None:
    if not cols:
        return
    with _lock(), ndb() as conn:
        conn.execute("UPDATE nodes SET " + ", ".join(f"{k}=?" for k in cols) + " WHERE id=?", (*cols.values(), nid))


def find(name: str) -> Node | None:
    """按 id 或名字找（模型写的「Mac mini」「mac-mini」都认）；几台重名 → None。"""
    name = str(name or "").strip()
    if not name:
        return None
    hit = get(name)
    if hit is not None:
        return hit
    key = re.sub(r"[^a-z0-9]+", "", name.lower())
    cands = [n for n in all_nodes() if key and key in (re.sub(r"[^a-z0-9]+", "", n.label.lower()), re.sub(r"[^a-z0-9]+", "", n.id.lower()),
                                                       re.sub(r"[^a-z0-9]+", "", n.id.lower().removeprefix("node-")))]
    return cands[0] if len(cands) == 1 else None


# —— 在线、传话（内存里：服务器只有一个进程）——————————————————————————————————————————

@dataclass
class Invoke:
    id: str
    node: str
    command: str
    params: dict
    timeout: float
    future: asyncio.Future
    delivered: float = 0.0


@dataclass
class Link:
    polls: int = 0                    # 正在等的长轮询
    seen: float = 0.0                 # 它最近一次自己来（hello、poll 开始和结束、result）的时间（monotonic）
    away: str | None = None           # bye 说的（background / shutdown）：下一次 hello / poll 清掉
    queue: list[Invoke] = field(default_factory=list)
    cancel: set[str] = field(default_factory=set)
    wake: asyncio.Event | None = None

    def event(self) -> asyncio.Event:
        if self.wake is None:
            self.wake = asyncio.Event()
        return self.wake


LINKS: dict[str, Link] = {}
INVOKES: dict[str, Invoke] = {}


def link(nid: str) -> Link:
    return LINKS.setdefault(nid, Link())


def connected(nid: str) -> bool:
    lk = LINKS.get(nid)
    if lk is None or lk.away:
        return False
    return lk.polls > 0 or time.monotonic() - lk.seen < GRACE


def _when(iso: str | None) -> str:
    if not iso:
        return L("从来没连上过", "never connected")
    try:
        d = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    mins = int((datetime.now(TZ) - d).total_seconds() // 60)
    if mins < 1:
        return L("刚才", "just now")
    if mins < 60:
        return L(f"{mins} 分钟前", f"{mins} min ago")
    return d.strftime("%m-%d %H:%M")


def offline(n: Node) -> NodeError:
    """不在线时那一句：手机 = app 不在前台（OpenClaw 的 NODE_BACKGROUND_UNAVAILABLE）；节点主机 = 不在线。"""
    if n.kind == "phone":
        return NodeError("NODE_BACKGROUND_UNAVAILABLE", L(
            f"「{n.label}」上的 app 现在不在前台：这一样要主人把 app 打开、放在前台才能做。跟主人说一声，等他打开再试。",
            f"The app on {n.label} isn't in the foreground: this needs the user to open the app and keep it in front. Tell them, "
            "and try again once they have."))
    return NodeError("NODE_OFFLINE", L(
        f"「{n.label}」现在不在线（上次连上：{_when(n.seen_at)}）：那台设备上的节点程序（mousse node run）可能没开，或者设备睡了。跟主人说一声。",
        f"{n.label} is offline (last seen: {_when(n.seen_at)}): the node program on it (mousse node run) may not be running, or the "
        "device is asleep. Tell the user."))


def ready(n: Node, command: str) -> None:
    """三道门（OpenClaw：报了、批了、没关）。过不了抛 NodeError。不管在不在线。"""
    if not on():
        raise NodeError("NODES_OFF", off_text())
    if command not in COMMANDS:
        raise NodeError("NODE_UNKNOWN_COMMAND", L(f"没有「{command}」这一样", f"There's no {command} command"))
    if command not in n.commands:
        raise NodeError("NODE_COMMAND_NOT_DECLARED", L(f"「{n.label}」没报能{describe_command(command)}：它做不了这一样。",
                                                       f"{n.label} doesn't offer this ({describe_command(command).lower()})."))
    if command not in approved(n.id):
        raise NodeError("NODE_COMMAND_NOT_APPROVED", L(
            f"「{n.label}」的「{describe_command(command)}」主人还没批：它报的命令要主人在收件箱里批准。跟主人说一声。",
            f"The user hasn't approved {describe_command(command).lower()} on {n.label} yet: they approve what a node offers in the inbox. "
            "Tell them."))
    if level(n.id, command) == "off":
        raise NodeError("NODE_COMMAND_OFF", L(
            f"主人没给「{n.label}」开「{describe_command(command)}」：要用请他在 app 的「设备和节点」里打开。别换个办法绕过去。",
            f"The user hasn't turned on {describe_command(command).lower()} for {n.label}: they can turn it on under Devices and nodes "
            "in the app. Don't work around it."))


async def invoke(nid: str, command: str, params: dict | None = None, timeout: float = 30) -> dict:
    """交给节点做一件事，等它回（最多 timeout 秒）。→ 它回的 payload（dict）。做不了、没回、它说没做成都抛 NodeError（马上，不等）。
    被取消（这一轮停了）：还没交出去的撤掉，交出去的下一次长轮询带上 cancel，再往外抛。"""
    n = get(nid)
    if n is None:
        raise NodeError("NODE_UNKNOWN", L(f"没有「{nid}」这个节点", f"No node called {nid}"))
    ready(n, command)
    if not connected(nid):
        raise offline(n)
    lk = link(nid)
    if sum(1 for i in INVOKES.values() if i.node == nid) >= INFLIGHT_MAX:
        raise NodeError("NODE_BUSY", L(f"「{n.label}」手上的活太多了，等一下再来", f"{n.label} is busy with other work; try again shortly"))
    inv = Invoke(uuid.uuid4().hex[:16], nid, command, dict(params or {}), max(1.0, float(timeout)), asyncio.get_running_loop().create_future())
    INVOKES[inv.id] = inv
    lk.queue.append(inv)
    lk.event().set()
    end = time.monotonic() + inv.timeout
    try:
        while True:
            try:
                res = await asyncio.wait_for(asyncio.shield(inv.future), max(0.01, min(0.5, end - time.monotonic())))
                break
            except asyncio.TimeoutError:
                pass
            if time.monotonic() >= end:
                _give_up(inv)
                raise NodeError("NODE_TIMEOUT", L(f"「{n.label}」{inv.timeout:.0f} 秒没回：已经叫它停下。它可能做了一半。",
                                                  f"{n.label} didn't answer within {inv.timeout:.0f}s; it was told to stop. It may have done part of it."))
            if not connected(nid):
                _give_up(inv)
                if inv.delivered and n.kind != "phone":
                    raise NodeError("NODE_LOST", L(f"「{n.label}」拿到这件事以后断了，没回结果：它可能做了，也可能没做。",
                                                   f"{n.label} dropped off after taking this on, without an answer: it may or may not have done it."))
                raise offline(get(nid) or n)
    except asyncio.CancelledError:
        _give_up(inv)
        raise
    finally:
        INVOKES.pop(inv.id, None)
    if not res.get("ok"):
        err = res.get("error") if isinstance(res.get("error"), dict) else {}
        code = re.sub(r"[^A-Z0-9_]", "", str(err.get("code") or "NODE_ERROR").upper())[:40] or "NODE_ERROR"
        msg = " ".join(str(err.get("message") or "").split())[:500]
        if msg:  # 节点自己说的那一句也是外来的字：包起来（审查）
            from engine.tools import wrap_untrusted
            msg = wrap_untrusted(f"node {nid}", msg)
        raise NodeError(code, L(f"「{n.label}」没做成（{code}）：{msg}" if msg else f"「{n.label}」没做成（{code}）",
                                f"{n.label} couldn't do it ({code}): {msg}" if msg else f"{n.label} couldn't do it ({code})"))
    payload = res.get("payload")
    return payload if isinstance(payload, dict) else {"value": payload}


def _give_up(inv: Invoke) -> None:
    """不等了：还在排队的撤掉；交出去的记进 cancel（下一次长轮询告诉它停下）。"""
    lk = LINKS.get(inv.node)
    if lk is None:
        return
    if inv in lk.queue:
        lk.queue.remove(inv)
    elif inv.delivered:
        lk.cancel.add(inv.id)
        lk.event().set()


def drop_link(nid: str, reason: str) -> None:
    """不在线了（bye、收回）：排着的、交出去的全部马上报错（invoke 那边每半秒看一次在不在线）。"""
    lk = link(nid)
    lk.away = reason
    lk.event().set()


# —— 收件箱的卡（kind node）：命令面 ——————————————————————————————————————————————————

def card_hash(title: str, why: str, changes: list[str], detail: str) -> str:
    """卡上给主人看的内容的指纹（同 engine/tools.card_hash）：同意时对一下，卡被改过就不批。"""
    body = json.dumps([title.strip(), why.strip(), [c.strip() for c in changes if c and c.strip()], detail.strip()], ensure_ascii=False)
    return hashlib.sha256(body.encode()).hexdigest()


def surface_card(n: Node, new: list[str], first: bool) -> dict:
    private = [c for c in new if c in PRIVATE]
    where = f"{n.label}（{n.platform}）" if n.platform else n.label
    where_en = f"{n.label} ({n.platform})" if n.platform else n.label
    title = L(f"让 {where} 当节点", f"Let {where_en} act as a node") if first else L(f"{where} 想多做 {len(new)} 样事", f"{where_en} wants to do {len(new)} more things")
    changes = [f"{c}：{describe_command(c)}" + (L("（隐私，批了也先关着）", " (private: stays off until you turn it on)") if c in PRIVATE else "")
               for c in new]
    perms = ", ".join(f"{k}: {'✓' if v else '✗'}" for k, v in n.permissions.items()) or L("（没报）", "(none reported)")
    folders = ", ".join(str(f.get("name") if isinstance(f, dict) else f) for f in n.folders) or L("（没有）", "(none)")
    why = L("这是你自己的设备吗？是的话批准，Agent 才能经 nodes 工具用下面这些。", "Is this your own device? If so, approve it and Agents can use the "
            "commands below through the nodes tool.")
    if private:
        why += " " + L("拍照、截屏、定位、跑命令、写文件、操作电脑批了也是关着的，要在 app 的「设置 → 硬件 → 设备和节点」里逐项打开。",
                       "Photos, screenshots, location, running commands, writing files and controlling the computer stay off even once "
                       "approved: turn them on one by one in the app under Settings → Hardware → Devices and nodes.")
    detail = (L(f"节点：{n.id}\n平台：{n.platform or '?'}　版本：{n.version or '?'}\n系统权限：{perms}\n给它看的文件夹：{folders}\n\n"
                "不想要了：在「设备和节点」里移除它（令牌一起收回），或者在服务器上 tokens.py remove " + n.id + "。",
                f"Node: {n.id}\nPlatform: {n.platform or '?'}  Version: {n.version or '?'}\nSystem permissions: {perms}\nShared folders: {folders}\n\n"
                f"To undo: remove it under Devices and nodes (its token goes too), or run tokens.py remove {n.id} on the server."))
    return {"title": title, "why": why, "changes": changes, "detail": detail, "approveLabel": L("批准", "Approve")}


async def ask_surface(n: Node) -> dict | None:
    """节点主机报得比批过的多：交一张卡（同一组还开着就不再交；变了就撤掉旧的、交新的）。→ {"card", "commands"} 或 None（不用问）。
    同一组 30 天里拒过：不交，记成没在等。"""
    import inbox
    have = set(approved(n.id))
    new = sorted(c for c in n.commands if c not in have)
    open_card = None
    if n.card:
        try:
            open_card = inbox.item(n.card)
        except HTTPException:
            open_card = None
        if open_card is not None and open_card.get("status") not in inbox.OPEN:
            open_card = None
    if not new:
        if open_card is not None:
            await _withdraw(n.card)
        if n.card or n.pending:
            _set(n.id, card=None, card_hash=None, pending="[]")
        return None
    if open_card is not None and sorted(n.pending) == new:
        return {"card": n.card, "commands": new}
    if open_card is not None:
        await _withdraw(n.card)
    card = surface_card(n, new, first=not have)
    dedupe = "node:" + hashlib.sha256(json.dumps([n.id, new]).encode()).hexdigest()[:24]
    try:
        res = await inbox.add(inbox.ItemIn(kind="node", source="main", thread="main", dedupe=dedupe, level="ring" if not have else "quiet", **card))
    except HTTPException as e:
        print(f"[nodes] {n.id} 的命令面交不了卡：{e.detail}")
        _set(n.id, card=None, card_hash=None, pending="[]")
        return None
    if not isinstance(res, dict) or not res.get("ok"):  # 409：30 天内拒过一模一样的这一组
        _set(n.id, card=None, card_hash=None, pending="[]")
        return None
    it = inbox.item(res["id"])
    _set(n.id, card=res["id"], card_hash=card_hash(it["title"], it["why"], it["changes"], it["detail"]), pending=json.dumps(new))
    return {"card": res["id"], "commands": new}


async def _withdraw(iid: str | None) -> None:
    import inbox
    if not iid:
        return
    try:
        if inbox.item(iid).get("status") in inbox.OPEN:
            await inbox.withdraw(iid)
    except HTTPException:
        pass


def approve(nid: str, commands: list[str]) -> list[str]:
    """批准这几样（写进 engine.nodes.<节点>.approved）。→ 批完的整个名单。"""
    want = {c for c in commands if c in COMMANDS}

    def fn(e: dict) -> None:
        old = e.get("approved") if isinstance(e.get("approved"), list) else []
        e["approved"] = sorted({str(x) for x in old if str(x) in COMMANDS} | want)
    return list(update_entry(nid, fn)["approved"])


async def on_decided(it: dict, action: str) -> dict | None:
    """inbox.HOOKS["node"]：同意 = 卡上那几样记成批过（卡被改过就不批）；拒绝、撤回 = 这一组不批（拒了的 30 天里不再问）；
    「改一下」没有意思：命令面只能批或不批，这张卡收起来，下次它连上再问。都不往对话里发话。"""
    with _lock(), ndb() as conn:
        r = conn.execute("SELECT * FROM nodes WHERE card=?", (it["id"],)).fetchone()
    n = Node.of(r) if r else None
    if n is not None:
        _set(n.id, card=None, card_hash=None, pending="[]")
    if action == "revise":
        return {"silent": True, "status": "done", "result": L("没改：节点报的命令只能批准或拒绝；它下次连上会再问", "Not changed: a node's commands can "
                                                              "only be approved or declined; it will ask again next time it connects")}
    if action != "approve":
        return None
    if n is None:
        return {"failed": L("找不到这张卡对应的节点（可能已经移除了），没有批", "Couldn't find the node behind this card (it may have been removed); "
                            "nothing was approved")}
    if n.card_hash != card_hash(it["title"], it["why"], it["changes"], it["detail"]):
        return {"failed": L("这张卡交上来以后被改过，和节点报的对不上，没有批", "This card was edited after it was sent, so it no longer matches "
                            "what the node reported; nothing was approved")}
    try:
        approve(n.id, n.pending)
    except NodeError as e:
        return {"failed": str(e)}
    priv = [c for c in n.pending if c in PRIVATE]
    text = L(f"已批准 {n.label} 的 {len(n.pending)} 样：{'、'.join(n.pending)}", f"Approved {len(n.pending)} for {n.label}: {', '.join(n.pending)}")
    if priv:
        text += L(f"。{'、'.join(priv)} 还关着，要用在「设备和节点」里打开", f". {', '.join(priv)} stay off until you turn them on under Devices and nodes")
    return {"silent": True, "result": text}


def install() -> None:
    """收件箱多一种卡：kind node（命令面）。服务器自己出的卡（Agent 改不了），「改一下」交给钩子。导入时就挂上（main.py 启动时导入）。
    nodes 工具交的卡（kind tool，engine/nodes.py）也在这时挂进关卡：重启后之前交的卡点了同意照样做。"""
    import inbox
    inbox.HOOKS["node"] = on_decided
    inbox.REVISE_BY_HOOK.add("node")
    inbox.FIXED_KINDS.add("node")
    from engine import nodes as node_tools
    node_tools.install()


# —— 给模型、给 app 看的样子 ——————————————————————————————————————————————————————————————

def view(n: Node) -> dict:
    ok = set(approved(n.id))
    cmds = []
    for c in sorted(set(n.commands) | ok, key=lambda x: list(COMMANDS).index(x) if x in COMMANDS else 99):
        cmds.append({"name": c, "label": describe_command(c), "declared": c in n.commands, "approved": c in ok, "private": c in PRIVATE,
                     "level": level(n.id, c), "maxLevel": MAX_LEVEL.get(c, "allow")})
    lk = LINKS.get(n.id)
    return {"id": n.id, "kind": n.kind, "label": n.label, "platform": n.platform, "version": n.version, "online": connected(n.id),
            "away": lk.away if lk else None, "foreground": n.foreground, "seenAt": n.seen_at, "createdAt": n.created_at, "commands": cmds,
            "pending": {"card": n.card, "commands": n.pending} if n.card else None, "permissions": n.permissions, "folders": n.folders,
            "exec": exec_allow(n.id)}


def summary(n: Node) -> dict:
    """给模型的一个节点（nodes 工具的 list / describe）：能用的、关着的、没批的分开说。"""
    ok = set(approved(n.id))
    usable = [c for c in n.commands if c in ok and level(n.id, c) != "off"]
    return {"node": n.id, "name": n.label, "kind": n.kind, "platform": n.platform, "online": connected(n.id),
            **({"inForeground": bool(connected(n.id))} if n.kind == "phone" else {}),
            "lastSeen": n.seen_at, "commands": {c: level(n.id, c) for c in usable},
            "off": [c for c in n.commands if c in ok and level(n.id, c) == "off"],
            "notApproved": [c for c in n.commands if c not in ok],
            **({"folders": [f.get("name") if isinstance(f, dict) else f for f in n.folders]} if n.folders else {}),
            **({"permissions": n.permissions} if n.permissions else {})}


# —— 接口 ————————————————————————————————————————————————————————————————————————————

def enabled() -> None:
    """整个功能关着：/api/nodes/* 一律 404（app 据此说「服务器上没开」）。"""
    if not on():
        raise HTTPException(404, off_text())


router = APIRouter(dependencies=[Depends(enabled)])


def owner(request: Request) -> str:
    """只认主人的设备（同 engine/setup_api.owner）：app 的令牌、Tailscale 里自己的设备；节点、程序的令牌、经脚本来的不行。"""
    who = str(getattr(request.state, "principal", "") or "")
    if request.headers.get("x-mousse-via") is None:
        if who.startswith("tailscale:"):
            return who
        if who.startswith("token:") and not pairing.program_token(who[6:]):
            return who
    raise HTTPException(403, L("节点只能在你自己的设备上管：用 app 的令牌，或者 Tailscale 里你自己的设备",
                               "Nodes can only be managed from your own devices: the app's token, or one of your devices on Tailscale"))


def caller(request: Request) -> tuple[str, str]:
    """哪个节点在说话 → (节点 id, kind)：node-* 令牌 = 节点主机；主人设备的令牌 = 手机 app 自己（id = 令牌的名字）。"""
    who = str(getattr(request.state, "principal", "") or "")
    if who.startswith("token:node-"):
        return who[6:], "host"
    if who.startswith("token:") and not pairing.program_token(who[6:]) and request.headers.get("x-mousse-via") is None and who[6:] not in RESERVED:
        return who[6:], "phone"
    raise HTTPException(403, L("节点要用自己的令牌连：电脑用 node_host.py pair 换来的 node-… 令牌，手机用 app 自己的令牌",
                               "A node connects with a token of its own: node-… from node_host.py pair on a computer, the app's own token on a phone"))


async def _json(request: Request, limit: int) -> dict:
    """请求体（JSON 对象），最多 limit 字节；太大 413，不是 JSON 对象 400。"""
    size, chunks = 0, []
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise HTTPException(413, L("太大了", "Too large"))
        chunks.append(chunk)
    raw_ = b"".join(chunks)
    if not raw_.strip():
        return {}
    try:
        body = json.loads(raw_)
    except ValueError:
        raise HTTPException(400, L("要 JSON", "Expected JSON")) from None
    if not isinstance(body, dict):
        raise HTTPException(400, L("要 JSON 对象", "Expected a JSON object"))
    return body


def _text(v, n: int) -> str:
    return " ".join(str(v or "").split())[:n]


@router.post("/api/nodes/hello")
async def hello(request: Request):
    """节点连上来报它能做什么。节点主机：多报的交卡；手机：主人在 app 里点的就是批准，顺手记下每一项开到哪一档（allow）。"""
    nid, kind = caller(request)
    body = await _json(request, HELLO_MAX)
    raw_cmds = [str(c) for c in body.get("commands") or [] if isinstance(c, str)] if isinstance(body.get("commands"), list) else []
    commands = sorted({c for c in raw_cmds if c in COMMANDS and (kind == "host" or c in PHONE)}, key=list(COMMANDS).index)
    ignored = sorted({c for c in raw_cmds if c not in commands})[:20]
    perms = body.get("permissions") if isinstance(body.get("permissions"), dict) else {}
    permissions = {_text(k, 40): bool(v) for k, v in list(perms.items())[:20] if _text(k, 40)}
    folders = []
    for f in (body.get("folders") if isinstance(body.get("folders"), list) else [])[:20]:
        if isinstance(f, dict) and _text(f.get("name"), 60):
            folders.append({"name": _text(f.get("name"), 60), **({"write": True} if f.get("write") is True else {})})
        elif isinstance(f, str) and _text(f, 60):
            folders.append({"name": _text(f, 60)})
    fg = body.get("foreground")
    foreground = None if kind == "host" else (fg is not False)
    label = safe_label(body.get("name")) or label_of(nid)
    ts = now_iso()
    with _lock(), ndb() as conn:
        had = conn.execute("SELECT kind FROM nodes WHERE id=?", (nid,)).fetchone()
        if had and had["kind"] != kind:
            raise HTTPException(409, L("这把令牌已经登记成另一种节点了", "This token is already registered as another kind of node"))
        conn.execute("""INSERT INTO nodes(id, kind, label, platform, version, commands, permissions, folders, foreground, created_at, seen_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET label=excluded.label, platform=excluded.platform, version=excluded.version,
            commands=excluded.commands, permissions=excluded.permissions, folders=excluded.folders, foreground=excluded.foreground,
            seen_at=excluded.seen_at""",
                     (nid, kind, label, _text(body.get("platform"), 20).lower(), _text(body.get("version"), 40), json.dumps(commands),
                      json.dumps(permissions, ensure_ascii=False), json.dumps(folders, ensure_ascii=False),
                      None if foreground is None else int(foreground), ts, ts))
    lk = link(nid)
    lk.seen, lk.away = time.monotonic(), (None if foreground is not False else "background")
    n = get(nid)
    assert n is not None
    pending = None
    if kind == "phone":  # 主人在这台手机上点了「开」：报的就是批的；各项的档也是他在这里定的
        allow = body.get("allow") if isinstance(body.get("allow"), dict) else {}
        levels_ = {c: str(v).lower() for c, v in allow.items() if c in commands and str(v).lower() in LEVELS}
        if set(commands) - set(approved(nid)) or any(level(nid, c) != v for c, v in levels_.items()):
            def fn(e: dict) -> None:
                old = e.get("approved") if isinstance(e.get("approved"), list) else []
                e["approved"] = sorted({str(x) for x in old if str(x) in COMMANDS} | set(commands))
                if levels_:
                    e["allow"] = {**(e.get("allow") if isinstance(e.get("allow"), dict) else {}), **levels_}
            update_entry(nid, fn)
    else:
        pending = await ask_surface(n)
    n = get(nid) or n
    ok = approved(nid)
    return {"ok": True, "id": nid, "kind": kind, "label": n.label, "approved": ok,
            "usable": [c for c in n.commands if c in ok and level(nid, c) != "off"],
            "levels": {c: level(nid, c) for c in n.commands if c in ok}, "pending": pending, "ignored": ignored,
            "poll": {"wait": WAIT, "path": "/api/nodes/poll"}}


@router.get("/api/nodes/poll")
async def poll(request: Request, wait: float = WAIT):
    """长轮询：有活马上回，没活等 wait 秒回空的。节点拿到活要马上再来等（干活的时候也在等，才算在线）。"""
    nid, kind = caller(request)
    if get(nid) is None:
        raise HTTPException(409, L("先 hello 再等活", "Say hello first"))
    lk = link(nid)
    lk.seen, lk.away = time.monotonic(), None
    lk.polls += 1
    _set(nid, seen_at=now_iso(), **({"foreground": 1} if kind == "phone" else {}))
    end = time.monotonic() + max(0.0, min(float(wait), WAIT_MAX))
    try:
        while True:
            if lk.away:  # 这期间它说要走了（另一个连接发的 bye）：这一个也收了
                return {"ok": True, "invokes": [], "cancel": [], "away": lk.away}
            if lk.queue or lk.cancel:
                take, lk.queue = lk.queue[:BATCH], lk.queue[BATCH:]
                stop, lk.cancel = sorted(lk.cancel), set()
                now = time.monotonic()
                for inv in take:
                    inv.delivered = now
                return {"ok": True, "invokes": [{"id": i.id, "command": i.command, "params": i.params, "timeoutMs": int(i.timeout * 1000)}
                                                for i in take], "cancel": stop}
            left = end - time.monotonic()
            if left <= 0:
                return {"ok": True, "invokes": [], "cancel": []}
            ev = lk.event()
            ev.clear()
            try:
                await asyncio.wait_for(ev.wait(), min(1.0, left))
            except asyncio.TimeoutError:
                if await request.is_disconnected():
                    return {"ok": True, "invokes": [], "cancel": []}
    finally:
        lk.polls -= 1
        lk.seen = time.monotonic()


@router.post("/api/nodes/result")
async def result(request: Request):
    """节点回结果：{id, ok, payload} 或 {id, ok: false, error: {code, message}}。不是交给它的、已经不等了的 → 404。"""
    nid, _ = caller(request)
    body = await _json(request, BODY_MAX)
    lk = link(nid)
    lk.seen = time.monotonic()
    inv = INVOKES.get(str(body.get("id") or ""))
    if inv is None or inv.node != nid:
        return JSONResponse({"ok": False, "error": L("这件事已经不等了（到点了、被停了，或者不是交给你的）", "Nobody is waiting for this any more "
                                                     "(it timed out, was stopped, or wasn't yours)")}, status_code=404)
    if not inv.future.done():
        inv.future.set_result({"ok": body.get("ok") is True, "payload": body.get("payload"), "error": body.get("error")})
    return {"ok": True}


@router.post("/api/nodes/bye")
async def bye(request: Request):
    """节点要走了（手机切到后台、节点主机关了）：马上算不在线，等着它的活马上报错。"""
    nid, kind = caller(request)
    body = await _json(request, 4096)
    reason = "background" if str(body.get("reason") or "") == "background" else "shutdown"
    if get(nid) is not None:
        _set(nid, seen_at=now_iso(), **({"foreground": 0} if kind == "phone" else {}))
        drop_link(nid, reason)
    return {"ok": True}


@router.get("/api/nodes")
async def list_nodes(request: Request):
    owner(request)
    return {"ok": True, "nodes": [view(n) for n in all_nodes()], "agents": agents(), "commands": [
        {"name": c, "label": describe_command(c), "private": c in PRIVATE, "maxLevel": MAX_LEVEL.get(c, "allow")} for c in COMMANDS]}


@router.post("/api/nodes/pair")
async def pair_node(request: Request):
    """给一台电脑出节点配对码（app「设备和节点」→「添加一台电脑」）：10 分钟、一次；回在那台电脑上要跑的命令。"""
    owner(request)
    body = await _json(request, 4096)
    server = str(body.get("server") or "").strip().rstrip("/")
    if not re.match(r"^https?://[^\s/]+(/[^\s]*)?$", server) or len(server) > 200:
        server = pairing.server_url()
    code, expires = pairing.new_code(_text(body.get("name"), 40), 10, node=True)
    url = pairing.link(server, code, node=True)
    return {"ok": True, "code": code, "expires": expires, "link": url, "command": f"python3 node_host.py pair '{url}'",
            "mousse": f"mousse node pair '{url}'"}


def _node(nid: str) -> Node:
    n = get(nid)
    if n is None:
        raise HTTPException(404, L("没有这个节点", "No such node"))
    return n


@router.get("/api/nodes/{nid}")
async def get_node(nid: str, request: Request):
    owner(request)
    return {"ok": True, "node": view(_node(nid))}


@router.post("/api/nodes/{nid}/allow")
async def set_level(nid: str, request: Request):
    """{command, level}：这一项开到哪一档（off / ask / allow；computer.act 最多 ask）。没批、没报的也能先定档。"""
    owner(request)
    n = _node(nid)
    body = await _json(request, 4096)
    cmd, lv = str(body.get("command") or ""), str(body.get("level") or "").lower()
    if cmd not in COMMANDS or lv not in LEVELS:
        raise HTTPException(400, L("command 要是认识的命令，level 只能是 off / ask / allow", "command must be a known command; level off, ask or allow"))
    cap = MAX_LEVEL.get(cmd)
    if cap and LEVELS.index(lv) > LEVELS.index(cap):
        raise HTTPException(400, L(f"{cmd} 最多只能开到 {cap}（每次都问）", f"{cmd} can be set to {cap} at most (it always asks)"))

    def fn(e: dict) -> None:
        e["allow"] = {**(e.get("allow") if isinstance(e.get("allow"), dict) else {}), cmd: lv}
    update_entry(nid, fn)
    return {"ok": True, "node": view(n)}


@router.post("/api/nodes/{nid}/approve")
async def approve_now(nid: str, request: Request):
    """主人在节点页上直接批（{commands}；不给 = 它报了、还没批的全部）：开着的那张卡收起来。"""
    owner(request)
    n = _node(nid)
    body = await _json(request, 16384)
    want = [str(c) for c in body.get("commands") or [] if str(c) in n.commands] if isinstance(body.get("commands"), list) else list(n.commands)
    if not want:
        raise HTTPException(400, L("没有要批的", "Nothing to approve"))
    try:
        approve(nid, want)
    except NodeError as e:
        raise HTTPException(409, str(e)) from None
    if n.card:
        _set(nid, card=None, card_hash=None, pending="[]")
        await _withdraw(n.card)
    n = _node(nid)
    if n.kind == "host":
        await ask_surface(n)
    return {"ok": True, "node": view(_node(nid))}


@router.delete("/api/nodes/{nid}")
async def remove(nid: str, request: Request):
    """移除一个节点：登记、批过的、各项的档都删；节点主机的 node-* 令牌一起收回（手机的设备令牌不动，在「能连它的设备」里收）。"""
    owner(request)
    n = _node(nid)
    drop_link(nid, "removed")
    if n.card:
        await _withdraw(n.card)
    with _lock(), ndb() as conn:
        conn.execute("DELETE FROM nodes WHERE id=?", (nid,))
    drop_entry(nid)
    if n.kind == "host" and nid.startswith("node-"):
        with _save_lock:
            data = copy.deepcopy(config.raw(fresh=True))
            auth = dict(data.get("auth") or {})
            tokens = dict(auth.get("tokens") or {})
            if tokens.pop(nid, None) is not None:
                auth["tokens"], data["auth"] = tokens, auth
                config.save(data)
    LINKS.pop(nid, None)
    return {"ok": True}


install()


def node_list_for_model() -> list[dict]:
    return [summary(n) for n in all_nodes()]


def is_private(command: str) -> bool:
    return command in PRIVATE


def payload_text(payload: Any, limit: int = 40_000) -> str:
    """节点回的东西变成给模型看的字（JSON 原样，太长截掉）：nodes 工具再包成外来内容。"""
    s = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, indent=1, default=str)
    return s if len(s) <= limit else s[:limit] + f"\n…[{len(s) - limit} more characters cut]"
