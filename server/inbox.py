"""收件箱（app 里的「等你点头」）：Agent 想做、但要你同意才能做的事，和它们自己的提议，都进这里；OpenClaw 的执行审批也合进来。

- 表 inbox（grava.db）一件事一行。Agent 经 server/inbox_ctl.py（→ POST /api/inbox）提交：要做什么（title，大白话）、为什么（why，理由和证据）、
  会改变什么（changes，一条一件具体的事）、细节（detail，Markdown）、同意按钮上的字（approveLabel）。
- 你在 app 里点：同意 → 在那个 Agent 的线程里发一句「【收件箱】已同意…」让它去做，它做完用 inbox_ctl.py done / fail 报结果（静默推一条）；
  拒绝 → 记下理由，同一个 dedupe 键 30 天内再提会被 409 rejected_before 挡回去；
  改一下 → 在 Agent 的对话里引用这张卡回复（/api/chat/send 带 inboxId：条目变 revising，模型另外看到是在回复哪一条），
  它改好用 update 重新提交（同一个 id）。POST /api/inbox/{id} 的 revise 仍然能用（把意见当一条【收件箱】消息发进线程）。
- 状态：pending 等你点头 → approved 同意了 → done 做完 / failed 没做成；rejected 拒绝；revising 等它改；withdrawn 它自己撤回；expired 过了 expires_at。
- 跟进：定下来以后（「已处理」里点「跟进」）在 Agent 的对话里引用这一条说话（/api/chat/send 带 inboxId）：记下 followed_at / follow_note，
  模型另外看到是哪一条、现在什么状态、怎么报结果；做完 / 没做成的改回 approved（在做），等它再用 done / fail 报。
- kind 决定默认推送档位：task / write / send / spend / calendar / egress 响铃，skill / agent / block / project / code / schedule / push / other 静默。
  exec 是虚拟的：OpenClaw 的执行审批（`openclaw approvals pending`），id 写成 exec:<审批 id>，同意 = allow-once，拒绝 = deny，不能「改一下」。
- kind social：名片 agent（cardagent.py）要你表态、问你私事的卡。线程是虚拟的 card（不挂在任何对话里，app 看到的 thread 是空的，
  所以没有「去对话里说」「跟进」）；点了由钩子自己把话告诉对方，不往任何 Agent 的线程里发话：对方说的一个字都不进主 agent。
  Agent 用 inbox_ctl.py 列不出、读不到这类卡（请求带 X-Mousse-Client: ctl）。
- kind egress：Sentinel 出口（egress.py）扣下的代办请求（在沙箱里提交表单、发信、用你的凭证……）。虚拟线程 sentinel，Agent 读不到；
  点了由钩子直接告诉正在等的代理（放行 / 不放行 / 改一下的话），不往任何线程里发话。
- kind app：连接器（apps.py）里设成「先问我」的工具，Agent 调一次就是一张卡（响铃），挂在那个 Agent 的线程里。卡上的参数是定死的：
  同意 = 钩子照原样调一次，结果当「做完了」发给 Agent；改一下 = 让它改好参数重新调（新卡），不能 update 这一张。
- 这是征得同意的界面，不是沙箱：不检查是谁提交的；真正拦住危险动作的是 OpenClaw 的执行审批和各 skill 自己的规则。
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Awaitable, Callable

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import chat
import claw
import data
import push
from chat import LEVELS, _lock, db, log_activity, now_iso
from config import TZ, settings
from i18n import L, LS

router = APIRouter()
KINDS = ("task", "write", "send", "spend", "schedule", "push", "skill", "agent", "block", "project", "code", "calendar", "social", "egress", "app",
         "other")
RING_KINDS = {"task", "write", "send", "spend", "calendar", "egress", "app"}  # 你让它做、它要动外面的东西：响铃；它自己的提议：静默
OPEN = ("pending", "revising")  # 还没定下来的
CTL = Path(__file__).resolve().parent / "inbox_ctl.py"
MARK = "【收件箱】"  # 发进 Agent 线程的系统消息的开头（协议标记，不翻译；inbox skill 按它认）
RECENT_DAYS, RECENT_MAX = 7, 50
REJECTED_DAYS = 30  # 拒绝过的同一件事（dedupe 键）多久内不许再提
WAIT_BUSY_S = 15 * 60  # 同意 / 改一下时那个线程正在回复：最多等多久再把消息发进去
_tasks: set[asyncio.Task] = set()  # 后台等待中的任务（留个引用，免得被回收）
# kind → 点了同意 / 拒绝 / 撤回之后服务端自己先做的事。返回 {"result": …} = 已经做完了（条目直接标 done，Agent 只收到一句知会）；
# {"failed": …} = 服务端动了手但没做成（条目标 failed、写明原因，不再让 Agent 去做）；None = 照常让 Agent 去做。
# boards.py 注册 block：同意就把提案那一版看板换上去，拒绝就把草稿作废。proposals.py 注册 skill / agent：日结提案同意了就装好 / 建好。
HOOKS: dict[str, Callable[[dict, str], Awaitable[dict | None]]] = {}
# 这些 kind 的「改一下」也交给钩子（hook(it, "revise")，你写的话在 it["note"]），不往线程里发话。钩子回 {"silent": True}（可以带 result）
# = 不往线程里发任何话：同意后不发「【收件箱】已同意…」，改一下直接算处理完。
REVISE_BY_HOOK = {"social", "egress", "app"}
# 「改一下」必须写话的卡（别的 social 卡可以空着，比如「换个时间」）：fn(iid) -> bool，在锁外调。cardagent 注册：Sentinel 扣下的那句（kind review）。
NEEDS_NOTE: list[Callable[[str], bool]] = []
PRIVATE_KINDS = {"social", "egress"}  # 线程是虚拟的、Agent 读不到的
# 这些卡挂在哪个虚拟线程：social → card（名片 agent），egress → sentinel（Sentinel 出口扣下的代办请求，egress.py）
VIRTUAL_THREADS = {"social": "card", "egress": "sentinel"}
# kind → 条目 JSON 里多给 app 的东西（在锁外调）。projects.py 注册 project：提案内容（预览）和开好的项目（「去看看」）。
EXTRAS: dict[str, Callable[[str], dict | None]] = {}


def idb() -> sqlite3.Connection:
    conn = db()
    conn.execute("""CREATE TABLE IF NOT EXISTS inbox (id TEXT PRIMARY KEY, kind TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'main',
        thread TEXT NOT NULL DEFAULT 'main', title TEXT NOT NULL, why TEXT NOT NULL DEFAULT '', changes TEXT NOT NULL DEFAULT '[]',
        detail TEXT NOT NULL DEFAULT '', approve_label TEXT NOT NULL DEFAULT '', level TEXT, status TEXT NOT NULL DEFAULT 'pending',
        dedupe TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '', result TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL, decided_at TEXT, expires_at TEXT, message_id INTEGER)""")
    global _migrated
    if not _migrated:  # 早先建的表没有后来加的列：message_id（卡片挂在哪条回复下面）、followed_at / follow_note（跟进）。补上，只查一次
        have = {r[1] for r in conn.execute("PRAGMA table_info(inbox)")}
        for col, decl in (("message_id", "INTEGER"), ("followed_at", "TEXT"), ("follow_note", "TEXT NOT NULL DEFAULT ''")):
            if col not in have:
                conn.execute(f"ALTER TABLE inbox ADD COLUMN {col} {decl}")
        _migrated = True
    conn.execute("CREATE INDEX IF NOT EXISTS inbox_status ON inbox(status, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS inbox_dedupe ON inbox(dedupe)")
    conn.execute("CREATE INDEX IF NOT EXISTS inbox_thread ON inbox(thread, created_at)")
    return conn


_migrated = False


def default_level(kind: str) -> str:
    return "ring" if kind in RING_KINDS else "quiet"


def kind_label(kind: str) -> str:
    """推送副标题里的种类名。"""
    return {"task": L("任务", "Task"), "write": L("写入", "Write"), "send": L("发送", "Send"), "spend": L("花钱", "Spend"),
            "schedule": L("定时任务", "Schedule"), "push": L("推送", "Notification"), "skill": L("新技能", "New skill"),
            "agent": L("新 Agent", "New agent"), "block": L("看板", "Board"), "project": L("项目", "Project"), "code": L("改代码", "Code change"),
            "calendar": L("日历", "Calendar"), "social": L("朋友", "Friends"), "exec": L("运行命令", "Run a command"),
            "egress": L("代办", "Errand"), "app": L("连接器", "Connector"),
            "other": L("其他", "Other")}.get(kind, kind)


def names() -> dict[str, str]:
    """线程 / Agent id → 名字（groups 的名字、独立空间的标题）。"""
    try:
        return data.thread_names()
    except sqlite3.Error:
        return {}


def source_name(source: str | None, nm: dict[str, str]) -> str:
    if not source or source == "main":
        return settings.app_name
    if source == "card":
        return L("名片 agent", "Card agent")
    return nm.get(source) or source


def actor_label(source: str) -> str:
    """活动记录里谁提的：名片 agent 不是一个 Agent。"""
    return L("名片 agent", "Card agent") if source == "card" else data.agent_label(source)


def parse_time(value: str | None) -> datetime | None:
    """ISO 时间 → 带时区的 datetime（没写时区按 server.json 的 timezone；只写日期 = 那天结束）。认不出来是 None。"""
    s = (value or "").strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if len(s) == 10:
        dt += timedelta(days=1)
    return dt if dt.tzinfo else dt.replace(tzinfo=TZ)


def ts_of(value) -> float:
    dt = parse_time(value) if isinstance(value, str) else None
    return dt.timestamp() if dt else 0.0


def changes_of(raw: str | None) -> list[str]:
    try:
        v = json.loads(raw or "[]")
    except ValueError:
        return []
    return [str(x) for x in v] if isinstance(v, list) else []


def clean_changes(items: list[str]) -> str:
    return json.dumps([c.strip() for c in items if c and c.strip()], ensure_ascii=False)


def item_json(r: sqlite3.Row, nm: dict[str, str]) -> dict:
    return {"id": r["id"], "kind": r["kind"], "source": r["source"], "sourceName": source_name(r["source"], nm),
            "thread": "" if r["kind"] in PRIVATE_KINDS else r["thread"],
            "title": r["title"], "why": r["why"] or "", "changes": changes_of(r["changes"]), "detail": r["detail"] or "",
            "approveLabel": r["approve_label"] or "", "status": r["status"], "note": r["note"] or "", "result": r["result"] or "",
            "level": r["level"] or default_level(r["kind"]), "createdAt": r["created_at"], "updatedAt": r["updated_at"],
            "decidedAt": r["decided_at"], "expiresAt": r["expires_at"], "messageId": r["message_id"],
            "day": chat.day_of(r["created_at"]),  # 提它的那天（04:00 为界的逻辑日）：app 的「看原对话」按它打开那天的记录
            "followedAt": r["followed_at"], "followNote": r["follow_note"] or "", **extra_of(r)}


def extra_of(r: sqlite3.Row) -> dict:
    fn = EXTRAS.get(r["kind"])
    if not fn:
        return {}
    try:
        got = fn(r["id"])
    except Exception:  # noqa: BLE001 — 多给的东西拿不到，卡片照常
        return {}
    return {r["kind"]: got} if got else {}


def expire(conn: sqlite3.Connection) -> None:
    """过了 expires_at 还没点头的标成 expired。"""
    now = datetime.now(TZ)
    for r in conn.execute("SELECT id, expires_at FROM inbox WHERE status='pending' AND IFNULL(expires_at, '')!=''").fetchall():
        exp = parse_time(r["expires_at"])
        if exp and exp <= now:
            conn.execute("UPDATE inbox SET status='expired', updated_at=? WHERE id=? AND status='pending'", (now_iso(), r["id"]))


def item(iid: str) -> dict:
    with _lock, idb() as conn:
        r = conn.execute("SELECT * FROM inbox WHERE id=?", (iid,)).fetchone()
    if not r:
        raise HTTPException(404, L("收件箱里没有这一条", "No such inbox item"))
    return item_json(r, names())


def check_level(level: str | None) -> None:
    if level is not None and level not in LEVELS:
        raise HTTPException(400, L("level 只能是 ring / quiet / none", "level must be ring, quiet or none"))


def check_expires(value: str | None) -> str | None:
    if not value:
        return None
    dt = parse_time(value)
    if not dt:
        raise HTTPException(400, L("expiresAt 要写成 ISO 时间，比如 2026-09-30T18:00", "expiresAt must be an ISO time, e.g. 2026-09-30T18:00"))
    return dt.astimezone(TZ).isoformat(timespec="seconds")


def check_thread(thread: str, kind: str = "") -> None:
    """同意之后要在这个线程里让 Agent 去做：只能是 main、某个 Agent 或某个独立空间（social 类是虚拟的 card）。"""
    if kind in PRIVATE_KINDS:
        want = VIRTUAL_THREADS[kind]
        if thread != want:
            raise HTTPException(400, L(f"{kind} 类的卡 thread 只能是 {want}", f"{kind} items must use thread {want}"))
        return
    if thread != "main" and thread not in names():
        raise HTTPException(400, L(f"没有「{thread}」这个线程：thread / source 写 main 或 Agent 的 id（agent_ctl.py list 能看到）",
                                   f'No thread called "{thread}": thread / source must be main or an Agent id (see agent_ctl.py list)'))


# —— OpenClaw 执行审批（虚拟的 exec 条目） ——————————————————————————————————

async def exec_pending(ttl: float = 5) -> tuple[list[dict], str | None]:
    """OpenClaw 待审批列表的原始行（和 /api/approvals 同一个 CLI）。ttl 5 秒给列表，60 秒给未读 / 角标（轮询多，别每次起 CLI）。
    读不到（没装 OpenClaw、Gateway 没开）不抛错：返回空列表和原因；失败也缓存，免得轮询时每次都重试。"""
    async def fetch() -> dict:
        try:
            if not claw.is_openclaw():  # 执行审批只有 OpenClaw 有
                return {"approvals": []}
            return await data.openclaw_cli("approvals", "pending", timeout=20)
        except Exception as e:  # noqa: BLE001
            return {"approvals": [], "error": str(getattr(e, "detail", None) or e)[:300]}
    raw = await data.cached("approvals:list" if ttl <= 5 else "approvals:count", ttl, fetch)
    rows = [a for a in (raw.get("approvals") or []) if isinstance(a, dict) and a.get("id")]
    return rows, raw.get("error")


def exec_item(a: dict, nm: dict[str, str]) -> dict:
    """一条 OpenClaw 执行审批 → 和收件箱条目一样的形状（id = exec:<审批 id>，多一个 fields）。"""
    e = data.exec_approval(a)
    src = e["agent"] or "main"
    created = e["created"]
    if isinstance(created, (int, float)):
        created = datetime.fromtimestamp(created / 1000, TZ).isoformat(timespec="seconds")
    elif not isinstance(created, str):
        created = None
    return {"id": f"exec:{e['id']}", "kind": "exec", "source": src, "sourceName": source_name(src, nm), "thread": src,
            "title": data.short(e["command"] or L("一个待审批的动作", "An action awaiting approval"), 80), "why": e["reason"], "changes": [],
            "detail": "", "approveLabel": L("允许一次", "Allow once"), "fields": e["fields"], "status": "pending", "note": "", "result": "",
            "level": "ring", "createdAt": created, "updatedAt": created, "decidedAt": None, "expiresAt": None, "messageId": None}


def link_message(thread: str, mark: int, message_id: int) -> None:
    """一次回复结束（chat.py run_gateway）：这次回复期间在这个线程里新交的条目（rowid 大于开跑时的 mark），挂到这条回复下面
    （app 在对话里把卡片显示在它下面）。不是在回复里交的（脚本、别的线程、开跑前交的）保持 NULL。"""
    with _lock, idb() as conn:
        conn.execute("UPDATE inbox SET message_id=? WHERE thread=? AND message_id IS NULL AND rowid>?", (message_id, thread, mark))


async def pending_count() -> int:
    """待你点头的条数（含 OpenClaw 执行审批，那部分走 60 秒缓存）。未读和角标用。"""
    with _lock, idb() as conn:
        expire(conn)
        n = conn.execute("SELECT COUNT(*) FROM inbox WHERE status='pending'").fetchone()[0]
    rows, _ = await exec_pending(60)
    return n + len(rows)


# —— 推送与「让 Agent 去做」 ————————————————————————————————————————————

async def push_new(it: dict, revised: bool = False) -> None:
    """新条目（或改好重新提交的）推一条：标题是谁提的，副标题「要你点头 · 写入」，正文是要做的事，why 短就接在下一行。"""
    if it["level"] == "none":
        return
    kl = kind_label(it["kind"])
    why = it["why"]
    body = it["title"] + (f"\n{why}" if why and push.width(why) <= 90 else "")
    # 通知长按展开的卡（app 1.0.5 的通知内容扩展）：要做的事、为什么、会改什么
    card = {"k": kl, "t": push.clip(it["title"], 60), "l": ([push.cut(why, 90)] if why else []) + [push.cut(c, 70) for c in it["changes"][:4]]}
    try:
        await push.send_push(it["sourceName"], body, {"thread": "today", "target": {"type": "inbox", "id": it["id"]}, "card": card}, thread_id="inbox",
                             subtitle=L(f"改好了 · {kl}", f"Revised · {kl}") if revised else L(f"要你点头 · {kl}", f"Needs your OK · {kl}"),
                             level=it["level"], category="inbox", collapse=f"inbox:{it['id']}", kind="inbox")
    except Exception:  # noqa: BLE001 — 推送失败不影响收件箱本身
        pass


def approve_text(it: dict, note: str) -> str:
    """同意之后发进 Agent 线程的那句。标记不翻译（和【自动触发】一样是协议，skill 按它认），后面按 server.json 的语言：这是给 Agent 看的。"""
    t, i = it["title"], it["id"]
    zh_note = f"补充：{note.rstrip('。.')}。" if note else ""
    en_note = f"Note from the user: {note}. " if note else ""
    return MARK + LS(f'已同意「{t}」（{i}）。{zh_note}现在去做；做完运行 `python3 {CTL} done {i} --result "一句话结果"`，做不成用 fail。',
                     f'Approved "{t}" ({i}). {en_note}Do it now; when it is done run `python3 {CTL} done {i} --result "one-line result"`, '
                     "or use fail if it can't be done.")


def done_text(it: dict, note: str, result: str) -> str:
    """同意之后服务端已经替它做完了（HOOKS）：只告诉 Agent 一声，不用再 done；有要接着做的（补数据）现在做。"""
    t, i = it["title"], it["id"]
    zh_note = f"补充：{note.rstrip('。.')}。" if note else ""
    en_note = f"Note from the user: {note}. " if note else ""
    return MARK + LS(f"已同意「{t}」（{i}），{result.rstrip('。.')}，已经生效，不用再报 done。{zh_note}有要接着做的（比如补数据）现在做，没有就简短回一句。",
                     f'Approved "{t}" ({i}): {result.rstrip(".")}. It has taken effect; no need to report done. {en_note}'
                     "If something follows from it (e.g. filling in data), do it now; otherwise reply briefly.")


def revise_text(it: dict, note: str) -> str:
    t, i, n = it["title"], it["id"], note.rstrip("。.")
    return MARK + LS(f"「{t}」（{i}）要改一下：{n}。改好后用 `python3 {CTL} update {i} …` 重新提交（同一个 id）。",
                     f'"{t}" ({i}) needs changes: {n}. When it is revised, resubmit with `python3 {CTL} update {i} …` (same id).')


def reply_context(iid: str) -> tuple[dict, str] | None:
    """/api/chat/send 带了 inboxId：用户在 Agent 的对话里引用这张卡回复（app 里「改一下」就是这样）。还没定下来的（pending / revising）
    返回 (条目, 只给模型看的前情)；exec、不存在、已经定了的返回 None（消息照常发，不带前情）。前情按 server.json 的语言：这是给 Agent 看的。"""
    if not iid or iid.startswith("exec:"):
        return None
    with _lock, idb() as conn:
        r = conn.execute("SELECT * FROM inbox WHERE id=?", (iid,)).fetchone()
    if not r or r["status"] not in OPEN or r["kind"] in PRIVATE_KINDS:
        return None
    it = item_json(r, names())
    t, i = it["title"], it["id"]
    if it["kind"] == "app":  # 连接器的调用（apps.py）：参数定死在卡上，update 改不了要调的东西
        return it, LS(f"（这条是在回复收件箱里的「{t}」（{i}）：用户要改这次调用。卡上的参数改不了：按他说的改好参数，再调用一次那个工具"
                      f"（会出一张新卡等他点头），然后用 `python3 {CTL} withdraw {i}` 撤回这一张。）",
                      f'(This message is a reply to the inbox item "{t}" ({i}): the user wants this call changed. Its arguments can\'t be edited: '
                      f"call the tool again with the arguments changed as they say (that makes a new card for their OK), then withdraw this one "
                      f"with `python3 {CTL} withdraw {i}`.)")
    return it, LS(f"（这条是在回复收件箱里的「{t}」（{i}）：用户要改。原来交的内容用 `python3 {CTL} get {i}` 看；"
                  f"按他的意见改好后用 `python3 {CTL} update {i} …` 重新提交，同一个 id，改完还是等他点头再做。）",
                  f'(This message is a reply to the inbox item "{t}" ({i}): the user wants changes. See what you submitted with `python3 {CTL} get {i}`; '
                  f"revise it as asked, then resubmit with `python3 {CTL} update {i} …` (same id) and wait for the user's OK again before doing it.)")


def mark_revising(it: dict, note: str) -> None:
    """用户在对话里回复了这张卡：状态改成 revising，note = 他说的话。"""
    with _lock, idb() as conn:
        conn.execute("UPDATE inbox SET status='revising', note=?, updated_at=? WHERE id=? AND status IN ('pending','revising')", (note, now_iso(), it["id"]))
    log_activity(L(f"让{it['sourceName']}把「{it['title']}」改一下", f'Asked {it["sourceName"]} to revise "{it["title"]}"'), "edit")


FOLLOWABLE = ("approved", "done", "failed", "rejected", "withdrawn", "expired")  # 定下来以后还能跟进的


def follow_context(iid: str) -> tuple[dict, str] | None:
    """/api/chat/send 带了 inboxId、那一条已经定下来了：用户在「已处理」里点了「跟进」，在对话里接着说这件事。
    返回 (条目, 只给模型看的前情)；exec、不存在、还没定下来的（那是「改一下」，见 reply_context）返回 None。前情按 server.json 的语言。"""
    if not iid or iid.startswith("exec:"):
        return None
    with _lock, idb() as conn:
        r = conn.execute("SELECT * FROM inbox WHERE id=?", (iid,)).fetchone()
    if not r or r["status"] not in FOLLOWABLE or r["kind"] in PRIVATE_KINDS:
        return None
    it = item_json(r, names())
    t, i, st, res = it["title"], it["id"], it["status"], it["result"]
    zh_st = {"approved": "同意了、还没报结果", "done": "报过做完了", "failed": "报过没做成", "rejected": "他当时没要",
             "withdrawn": "你撤回了", "expired": "等太久作废了"}[st]
    en_st = {"approved": "approved, no result reported yet", "done": "reported done", "failed": "reported as not done",
             "rejected": "the user declined it", "withdrawn": "you withdrew it", "expired": "it expired unanswered"}[st]
    zh = f"（这条是在跟进收件箱里已经处理过的「{t}」（{i}）：{zh_st}" + (f"，结果是「{res}」" if res else "") + f"。原来交的内容用 `python3 {CTL} get {i}` 看。"
    en = f'(This message follows up on the inbox item "{t}" ({i}), which was already settled: {en_st}' + (f' (result: "{res}")' if res else "") + \
        f". See what was submitted with `python3 {CTL} get {i}`. "
    if st == "approved":
        zh += f"先回答他问的，接着做；做完用 `python3 {CTL} done {i} --result \"一句话结果\"` 报，做不成用 fail。）"
        en += f'Answer what they ask and carry on; when it is done run `python3 {CTL} done {i} --result "one-line result"`, or fail if it can\'t be done.)'
    elif st in ("done", "failed"):
        zh += (f"他来跟进，多半是还没做完或者有后续：这一条已经改回「在做」。按他说的接着做（他同意过，不用重新提交），"
               f"做完用 `python3 {CTL} done {i} --result \"这次的结果\"` 报（盖掉原来的结果），做不成用 fail；"
               "如果只是问问、事情确实做完了，回答以后也用 done 报一次，写清现在的情况。）")
        en += ("A follow-up usually means it isn't finished or something follows from it, so the item is back to in progress. "
               f'Carry on as they say (already approved; no need to resubmit), then run `python3 {CTL} done {i} --result "this time\'s result"` '
               "(replaces the old result), or fail if it can't be done. If they are only asking and it really is done, answer and report done "
               "again anyway, saying where things stand.)")
    else:
        zh += (f"他现在又提起：先看他怎么说；要重新做的话按他说的用 `python3 {CTL} add …` 提一条新的，照常等他点头"
               "（别带原来那条的 dedupe 键，带了会被当成拒绝过的挡回来）。）")
        en += (f"They are bringing it up again: see what they say; if it should be done after all, submit a new item with `python3 {CTL} add …` "
               "and wait for their OK as usual (without the old item's dedupe key, which would be turned away as declined before).)")
    return it, LS(zh, en)


def mark_followed(it: dict, note: str) -> None:
    """用户跟进了一件定下来的事：记下时间和他说的话；做完 / 没做成的改回 approved（在做），等 Agent 再报结果。"""
    ts = now_iso()
    reopen = it["status"] in ("done", "failed")
    with _lock, idb() as conn:
        conn.execute("UPDATE inbox SET followed_at=?, follow_note=?, updated_at=?" + (", status='approved'" if reopen else "")
                     + " WHERE id=? AND status=?", (ts, note, ts, it["id"], it["status"]))
    log_activity(L(f"跟进{it['sourceName']}的「{it['title']}」", f'Followed up on "{it["title"]}" with {it["sourceName"]}'), "edit")


def start(thread: str, text: str) -> None:
    chat.start_run(thread, text, None, origin="auto", level="none")  # 回复不推：结果由 Agent 用 done / fail 报


async def start_when_free(thread: str, text: str) -> None:
    """那个线程正在回复：每 2 秒看一次，回完了再发；最多等 15 分钟。"""
    deadline = time.time() + WAIT_BUSY_S
    while time.time() < deadline:
        await asyncio.sleep(2)
        cur = chat.RUNS.get(thread)
        if cur and not cur.done:
            continue
        try:
            start(thread, text)
            return
        except HTTPException as e:
            if e.status_code != 409:
                break
        except Exception:  # noqa: BLE001
            break
    log_activity(L(f"收件箱的消息没能发进「{thread}」（一直在忙或出错了）", f'Could not deliver the inbox message to "{thread}" (busy or failed)'), "failed")


def kick(thread: str, text: str) -> str:
    """在 thread 里发一条系统消息让 Agent 去做。返回 started / queued（那个线程在回复，后台等它回完再发）/ failed: 原因。"""
    try:
        start(thread, text)
        return "started"
    except HTTPException as e:
        if e.status_code != 409:
            return f"failed: {e.detail}"
    except Exception as e:  # noqa: BLE001
        return f"failed: {e}"
    task = asyncio.create_task(start_when_free(thread, text))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return "queued"


# —— 接口 ————————————————————————————————————————————————————————

def from_ctl(request: Request | None) -> bool:
    """请求是 inbox_ctl.py 发的（Agent 在用）：social 类的卡不给它看。"""
    return bool(request and request.headers.get("x-mousse-client") == "ctl")


@router.get("/api/inbox")
async def list_items(request: Request, status: str = "pending", thread: str | None = None):
    """pending：等你点头的（过期的先标成 expired）+ OpenClaw 执行审批；recent：最近 7 天定下来 / 做完的，最多 50 条。都是新的在前。
    thread=<线程>：这个线程的条目（对话里按时间线显示成卡片，挂在 messageId 那条回复下面）：等你点头的 + 最近 7 天定下来 / 做完的，
    旧的在前；不含 OpenClaw 执行审批（它们不属于哪条消息）。给了 thread 就不看 status。"""
    if status not in ("pending", "recent"):
        raise HTTPException(400, L("status 只能是 pending 或 recent", "status must be pending or recent"))
    nm = names()
    since = (datetime.now(TZ) - timedelta(days=RECENT_DAYS)).isoformat(timespec="seconds")
    if thread:
        with _lock, idb() as conn:
            expire(conn)
            rows = conn.execute("SELECT * FROM inbox WHERE thread=? AND (status='pending' OR updated_at>=?) ORDER BY created_at, rowid",
                                (thread, since)).fetchall()
        return {"ok": True, "items": [item_json(r, nm) for r in rows]}
    with _lock, idb() as conn:
        expire(conn)
        if status == "pending":
            rows = conn.execute("SELECT * FROM inbox WHERE status='pending'").fetchall()
        else:
            rows = conn.execute("SELECT * FROM inbox WHERE status!='pending' AND updated_at>=? ORDER BY updated_at DESC LIMIT ?",
                                (since, RECENT_MAX)).fetchall()
    items = [item_json(r, nm) for r in rows if not (from_ctl(request) and r["kind"] in PRIVATE_KINDS)]
    out: dict = {"ok": True}
    if status == "pending":
        raw, err = await exec_pending(5)
        items += [exec_item(a, nm) for a in raw]
        if err:
            out["execError"] = err  # 读不到 OpenClaw 的审批队列：收件箱自己的照常给
    items.sort(key=lambda x: ts_of(x.get("updatedAt") or x.get("createdAt")), reverse=True)
    out["items"] = items
    return out


@router.get("/api/inbox/{iid}")
async def get_item(iid: str, request: Request):
    if iid.startswith("exec:"):
        raw, _ = await exec_pending(5)
        a = next((x for x in raw if str(x.get("id")) == iid[5:]), None)
        if not a:
            raise HTTPException(404, L("这个执行审批已经不在队列里了", "This exec approval is no longer in the queue"))
        return {"ok": True, "item": exec_item(a, names())}
    with _lock, idb() as conn:
        expire(conn)
    it = item(iid)
    if from_ctl(request) and it["kind"] in PRIVATE_KINDS:
        raise HTTPException(404, L("收件箱里没有这一条", "No such inbox item"))
    return {"ok": True, "item": it}


class ItemIn(BaseModel):
    kind: str
    title: str
    source: str = "main"         # 谁提的：main 或 Agent 的 id
    thread: str | None = None    # 同意后在哪个线程里让它去做，不给 = source
    why: str = ""
    changes: list[str] = []
    detail: str = ""             # Markdown
    approveLabel: str = ""       # 同意按钮上的字，空 = app 默认的「同意」
    level: str | None = None     # ring / quiet / none，不给按 kind
    dedupe: str = ""             # 同一件事的固定键：还在等的就原地更新；30 天内被拒过就 409
    expiresAt: str | None = None


@router.post("/api/inbox")
async def add(body: ItemIn):
    """Agent 提交一件要你点头的事（经 inbox_ctl.py add）。"""
    kind = body.kind.strip().lower()
    if kind not in KINDS:
        raise HTTPException(400, L(f"kind 只能是 {' / '.join(KINDS)}", f"kind must be one of: {', '.join(KINDS)}"))
    title = body.title.strip()
    if not title:
        raise HTTPException(400, L("要写 title：用大白话说要做什么", "title is required: the action, in plain words"))
    check_level(body.level)
    level = body.level or default_level(kind)
    source = body.source.strip() or "main"
    thread = (body.thread or "").strip() or VIRTUAL_THREADS.get(kind) or source
    check_thread(thread, kind)
    changes, expires, dedupe = clean_changes(body.changes), check_expires(body.expiresAt), body.dedupe.strip()
    fields = (kind, source, thread, title, body.why.strip(), changes, body.detail.strip(), body.approveLabel.strip(), level)
    ts = now_iso()
    with _lock, idb() as conn:
        expire(conn)
        cur = conn.execute("SELECT id, status FROM inbox WHERE dedupe=? AND status IN ('pending','revising') ORDER BY created_at DESC LIMIT 1",
                           (dedupe,)).fetchone() if dedupe else None
        if dedupe and not cur:
            since = (datetime.now(TZ) - timedelta(days=REJECTED_DAYS)).isoformat(timespec="seconds")
            rej = conn.execute("""SELECT IFNULL(decided_at, updated_at) rejected_at, note FROM inbox WHERE dedupe=? AND status='rejected'
                AND IFNULL(decided_at, updated_at)>=? ORDER BY 1 DESC LIMIT 1""", (dedupe, since)).fetchone()
            if rej:
                return JSONResponse({"ok": False, "error": "rejected_before", "rejectedAt": rej["rejected_at"], "note": rej["note"] or ""},
                                    status_code=409)
        if cur:
            iid = cur["id"]
            conn.execute("""UPDATE inbox SET kind=?, source=?, thread=?, title=?, why=?, changes=?, detail=?, approve_label=?, level=?,
                status='pending', updated_at=?, expires_at=IFNULL(?, expires_at) WHERE id=?""", (*fields, ts, expires, iid))
        else:
            iid = f"ib-{uuid.uuid4().hex[:8]}"
            conn.execute("""INSERT INTO inbox(kind, source, thread, title, why, changes, detail, approve_label, level, id, status, dedupe,
                created_at, updated_at, expires_at) VALUES(?,?,?,?,?,?,?,?,?,?,'pending',?,?,?,?)""", (*fields, iid, dedupe, ts, ts, expires))
    it = item(iid)
    log_activity(L(f"{'改了' if cur else '想做'}「{title}」，等你点头", f'{"Updated" if cur else "Wants to do"} "{title}", waiting for your OK'),
                 "inbox", actor=actor_label(source))
    await push_new(it, revised=bool(cur and cur["status"] == "revising"))
    return {"ok": True, "id": iid, **({"updated": True} if cur else {})}


class ActIn(BaseModel):
    action: str      # approve 同意 / reject 拒绝 / revise 改一下
    note: str = ""   # 拒绝的理由、要怎么改、同意时的补充


async def act_exec(iid: str, action: str, note: str) -> dict:
    if action == "revise":
        raise HTTPException(400, L("执行审批只能允许或拒绝，不能改", "Exec approvals can only be allowed or denied, not revised"))
    aid = iid[5:]
    raw, _ = await exec_pending(5)
    a = next((x for x in raw if str(x.get("id")) == aid), None)
    await data.resolve_exec(aid, action == "approve")
    it = exec_item(a or {"id": aid}, names())
    it.update(status="approved" if action == "approve" else "rejected", decidedAt=now_iso(), updatedAt=now_iso(), note=note)
    return {"ok": True, "item": it}


@router.post("/api/inbox/{iid}")
async def act(iid: str, body: ActIn):
    """你在 app 里点的：同意 / 拒绝 / 改一下。同意和改一下会在条目的线程里发一句话让 Agent 接着做（线程正忙就等它回完再发）。"""
    action, note = body.action.strip().lower(), body.note.strip()
    if action not in ("approve", "reject", "revise"):
        raise HTTPException(400, L("action 只能是 approve / reject / revise", "action must be approve, reject or revise"))
    if iid.startswith("exec:"):
        return await act_exec(iid, action, note)
    if action == "revise" and not note and any(f(iid) for f in NEEDS_NOTE):
        raise HTTPException(400, L("「改一下」要写你想发的那句", "Write what you want to send"))
    ts = now_iso()
    with _lock, idb() as conn:
        expire(conn)
        r = conn.execute("SELECT * FROM inbox WHERE id=?", (iid,)).fetchone()
        if not r:
            raise HTTPException(404, L("收件箱里没有这一条", "No such inbox item"))
        if r["status"] != "pending":
            raise HTTPException(409, L(f"这一条已经不在等你点头了（{r['status']}）", f"This item isn't waiting for you anymore ({r['status']})"))
        if action == "revise" and not note and r["kind"] not in REVISE_BY_HOOK:  # social 的「换个时间」可以不写：名片 agent 按空着的时间提一个
            raise HTTPException(400, L("「改一下」要说怎么改", "Say what to change"))
        status = {"approve": "approved", "reject": "rejected", "revise": "revising"}[action]
        conn.execute("UPDATE inbox SET status=?, note=?, updated_at=?, decided_at=? WHERE id=?",
                     (status, (note or r["note"]) if action == "approve" else note, ts, None if action == "revise" else ts, iid))
    it = item(iid)
    who, title = it["sourceName"], it["title"]
    hook = HOOKS.get(it["kind"]) if action != "revise" or it["kind"] in REVISE_BY_HOOK else None
    done = await hook(it, action) if hook else None
    silent = bool(done and done.get("silent"))  # 钩子自己办完了，不往线程里发话（social：对方说的不能进 Agent 的线程）
    out: dict = {"ok": True, "item": it}
    if action == "revise" and done and done.get("status") == "done" and not done.get("failed"):
        # 钩子按你写的话办完了（Sentinel 扣下的那句，你改了以后发出去了）：不用再等谁改，直接算处理完
        with _lock, idb() as conn:
            conn.execute("UPDATE inbox SET status='done', result=?, decided_at=?, updated_at=? WHERE id=?", (done.get("result") or "", now_iso(), now_iso(), iid))
        out["item"] = it = item(iid)
    if action != "approve" and done and done.get("failed"):
        with _lock, idb() as conn:  # 你的决定记下了，只是没办成（比如没能告诉对方）：写在结果里
            conn.execute("UPDATE inbox SET result=?, updated_at=? WHERE id=?", (done["failed"], now_iso(), iid))
        out["item"] = it = item(iid)
    if action == "approve" and done and done.get("failed"):
        # 服务端自己动手没做成（比如 proposals.py 装 skill 时 openclaw.json 校验不过）：卡片标「没做成」写明原因，不再让 Agent 去做
        log_activity(L(f"同意了{who}的「{title}」，没做成", f'Approved "{title}" from {who}, but it failed'), "failed")
        with _lock, idb() as conn:
            conn.execute("UPDATE inbox SET status='failed', result=?, updated_at=? WHERE id=?", (done["failed"], now_iso(), iid))
        out["item"] = it = item(iid)
    elif action == "approve" and done and done.get("result"):
        log_activity(L(f"同意了{who}的「{title}」", f'Approved "{title}" from {who}'), "approved")
        with _lock, idb() as conn:
            conn.execute("UPDATE inbox SET status='done', result=?, updated_at=? WHERE id=?", (done["result"], now_iso(), iid))
        out["item"] = it = item(iid)
        if not silent:
            out["run"] = kick(it["thread"], done_text(it, note, done["result"]))
    elif action == "approve":
        log_activity(L(f"同意了{who}的「{title}」", f'Approved "{title}" from {who}'), "approved")
        if not silent:
            out["run"] = kick(it["thread"], approve_text(it, note))
    elif action == "reject":
        log_activity(L(f"拒绝了{who}的「{title}」", f'Declined "{title}" from {who}'), "denied")
        if done and done.get("result") and not done.get("failed"):  # 钩子写了办成什么样（Sentinel 那张：没发，告诉对方答不了）
            with _lock, idb() as conn:
                conn.execute("UPDATE inbox SET result=?, updated_at=? WHERE id=?", (done["result"], now_iso(), iid))
            out["item"] = it = item(iid)
    else:
        log_activity(L(f"让{who}把「{title}」改一下", f'Asked {who} to revise "{title}"'), "edit")
        if not hook:
            out["run"] = kick(it["thread"], revise_text(it, note))
    return out


class ItemPatch(BaseModel):
    title: str | None = None
    why: str | None = None
    changes: list[str] | None = None
    detail: str | None = None
    approveLabel: str | None = None
    level: str | None = None
    expiresAt: str | None = None  # "" = 去掉期限


@router.patch("/api/inbox/{iid}")
async def update(iid: str, body: ItemPatch):
    """Agent 改好了重新提交（经 inbox_ctl.py update）：内容换掉，回到 pending，再推一次。"""
    if iid.startswith("exec:"):
        raise HTTPException(400, L("执行审批不能改", "Exec approvals can't be edited"))
    check_level(body.level)
    sets: dict = {}
    if body.title is not None:
        if not body.title.strip():
            raise HTTPException(400, L("title 不能是空的", "title can't be empty"))
        sets["title"] = body.title.strip()
    for key, col in (("why", "why"), ("detail", "detail"), ("approveLabel", "approve_label")):
        if getattr(body, key) is not None:
            sets[col] = getattr(body, key).strip()
    if body.changes is not None:
        sets["changes"] = clean_changes(body.changes)
    if body.level is not None:
        sets["level"] = body.level
    if body.expiresAt is not None:
        sets["expires_at"] = check_expires(body.expiresAt)
    if not sets:
        raise HTTPException(400, L("没有要改的内容", "Nothing to change"))
    with _lock, idb() as conn:
        r = conn.execute("SELECT status FROM inbox WHERE id=?", (iid,)).fetchone()
        if not r:
            raise HTTPException(404, L("收件箱里没有这一条", "No such inbox item"))
        if r["status"] not in OPEN:
            raise HTTPException(409, L(f"这一条已经定了（{r['status']}），不能再改；要重新提就 add 一条新的",
                                       f"This item is already settled ({r['status']}); add a new one instead"))
        sets.update(status="pending", updated_at=now_iso())
        cols = ", ".join(f"{k}=?" for k in sets)
        conn.execute("UPDATE inbox SET " + cols + " WHERE id=?", (*sets.values(), iid))
    it = item(iid)
    log_activity(L(f"改了「{it['title']}」，等你点头", f'Updated "{it["title"]}", waiting for your OK'), "inbox", actor=data.agent_label(it["source"]))
    await push_new(it, revised=r["status"] == "revising")
    return {"ok": True, "item": it}


class ResultIn(BaseModel):
    status: str       # done / failed
    result: str = ""  # 一句话结果


@router.post("/api/inbox/{iid}/result")
async def report(iid: str, body: ResultIn):
    """Agent 做完（或没做成）之后报结果（经 inbox_ctl.py done / fail），静默推一条。只有同意过的才能报。"""
    st, res = body.status.strip().lower(), body.result.strip()
    if st not in ("done", "failed"):
        raise HTTPException(400, L("status 只能是 done 或 failed", "status must be done or failed"))
    with _lock, idb() as conn:
        r = conn.execute("SELECT status FROM inbox WHERE id=?", (iid,)).fetchone()
        if not r:
            raise HTTPException(404, L("收件箱里没有这一条", "No such inbox item"))
        if r["status"] not in ("approved", "done", "failed"):
            raise HTTPException(409, L(f"这一条还没被同意（{r['status']}），不能报结果：先等用户点头",
                                       f"This item hasn't been approved ({r['status']}), so there's no result to report yet"))
        conn.execute("UPDATE inbox SET status=?, result=?, updated_at=? WHERE id=?", (st, res, now_iso(), iid))
    it = item(iid)
    ok = st == "done"
    tail = L(f"：{res}", f": {res}") if res else ""
    log_activity(L(f"{'做完了' if ok else '没做成'}「{it['title']}」{tail}", f'{"Done" if ok else "Could not do"} "{it["title"]}"{tail}'),
                 "inbox" if ok else "failed", actor=data.agent_label(it["source"]))
    if it["level"] != "none":
        try:
            await push.send_push(it["sourceName"], f"{it['title']} · {res}" if res else it["title"],
                                 {"thread": "today", "target": {"type": "inbox", "id": iid}}, thread_id="inbox",
                                 subtitle=L("做完了", "Done") if ok else L("没做成", "Didn't work"), level="quiet", collapse=f"inbox:{iid}",
                                 kind="done")
        except Exception:  # noqa: BLE001
            pass
    return {"ok": True, "item": it}


@router.post("/api/inbox/{iid}/withdraw")
async def withdraw(iid: str):
    """Agent 自己撤回还没定下来的一条（经 inbox_ctl.py withdraw）。"""
    with _lock, idb() as conn:
        r = conn.execute("SELECT status FROM inbox WHERE id=?", (iid,)).fetchone()
        if not r:
            raise HTTPException(404, L("收件箱里没有这一条", "No such inbox item"))
        if r["status"] not in OPEN:
            raise HTTPException(409, L(f"这一条已经定了（{r['status']}），不能撤回", f"This item is already settled ({r['status']}) and can't be withdrawn"))
        conn.execute("UPDATE inbox SET status='withdrawn', updated_at=? WHERE id=?", (now_iso(), iid))
    it = item(iid)
    hook = HOOKS.get(it["kind"])
    if hook:
        await hook(it, "withdraw")
    log_activity(L(f"撤回了「{it['title']}」", f'Withdrew "{it["title"]}"'), "inbox", actor=data.agent_label(it["source"]))
    return {"ok": True, "item": it}
