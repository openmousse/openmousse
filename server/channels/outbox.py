"""出去的（第 6 批）：先记进队列再发（channel_outbox），一条条按聊天的先后发；失败按退避重试，渠道说要等（Telegram 429 的
retry_after）就照它等，试够次数进死信（dead：设置里看得到、能重发）。照 Hermes 的 delivery_ledger、OpenClaw 的 durable ingress 的意思：
宁可在重启那一刻重发一遍，不丢。

  say()      回一段话：按渠道的长度分好段（format.chunks，不切断代码块），第一段回那一条，按钮只放最后一段；叫醒发的那一圈
  pump()     发一圈（hub 的发循环调）：每个聊天最早一条、到了时间的；同一个聊天两条之间至少隔 min_gap 秒
  deliver()  cardagent.DELIVER[渠道]：主人在名片 Agent 的卡上点了以后，把那句话送回对方（ref = 渠道:账号:聊天[:话题]）
"""
from __future__ import annotations

import json
import time

from chat import log_activity
from i18n import L

from . import format as fmt
from . import hub, store
from .base import ChannelError

RETRY = (2, 5, 15, 60, 300, 900)   # 第几次没发成以后等几秒；再不行进死信
RATE_EXTRA = 5                     # 429 等完再发不算次数，但最多这么多次


def limit_of(kind: str) -> int:
    cls = hub.channel_class(kind)
    return int(getattr(cls, "max_len", 4000) or 4000)


def say(kind: str, account: str, chat: str, text: str, *, topic: str = "", reply_to: str = "", why: str = "reply", ref: str = "",
        buttons: list | None = None, extra: dict | None = None, format: str = "markdown", edit: str = "") -> list[int]:
    """排进去一段话（分好段）→ 队列里的行 id。空的什么都不排。"""
    text = (text or "").strip()
    if not text:
        return []
    parts = fmt.chunks(text, limit_of(kind)) if format != "html" else [text]
    ids: list[int] = []
    for i, p in enumerate(parts):
        last = i == len(parts) - 1
        ids.append(store.enqueue(kind, account, chat, p, topic=topic, format=format, reply_to=reply_to if i == 0 else "",
                                 buttons=buttons if last else None, edit=edit if i == 0 else "", extra=extra, why=why, ref=ref))
    hub.wake_out(kind, account)
    return ids


def _fail(row, e: Exception) -> str:
    """没发成：排下一次或进死信。→ 新的状态。"""
    tries = int(row["tries"]) + 1
    err = str(e)[:300] or type(e).__name__
    if isinstance(e, ChannelError) and e.permanent:
        store.set_out(row["id"], status="dead", error=err)
        return "dead"
    if isinstance(e, ChannelError) and e.retry_after is not None and tries <= len(RETRY) + RATE_EXTRA:
        store.set_out(row["id"], status="queued", next_at=time.time() + max(1.0, float(e.retry_after)), error=err, tries=max(0, tries - 1))
        return "queued"
    if tries > len(RETRY):
        store.set_out(row["id"], status="dead", error=err)
        return "dead"
    store.set_out(row["id"], status="queued", next_at=time.time() + RETRY[tries - 1], error=err)
    return "queued"


async def pump(runner) -> float | None:
    """发一圈。→ 下一圈最好隔几秒再来（有聊天刚发过、要等 min_gap）；None = 照常等。"""
    kind, account = runner.key
    wait: float | None = None
    for row in store.due(kind, account):
        if runner.stopped:
            break
        gap = float(getattr(runner.ch, "min_gap", 0) or 0)
        last = runner.last_sent.get(row["chat"], 0.0)
        if gap and time.time() - last < gap:  # 这个聊天刚发过：等够了再发
            left = gap - (time.time() - last)
            wait = left if wait is None else min(wait, left)
            continue
        if not store.claim_out(row["id"]):
            continue
        out = store.outbound(row)
        try:
            ids = await runner.ch.send(out)
        except Exception as e:  # noqa: BLE001 — ChannelError 按它说的办；别的（连不上、渠道的毛病）当能重试的
            st = _fail(row, e)
            if st == "dead":
                runner.note(runner.status.get("state") or "ok", runner.status.get("detail") or "", error=str(e)[:300])
                log_activity(L(f"没能发到{runner.ch.label or kind}（试够次数了，进了死信，设置里能重发）：{str(e)[:120]}",
                               f"Couldn't send to {runner.ch.label or kind} (gave up; it's in dead letters and can be resent from settings): "
                               f"{str(e)[:120]}"), "failed")
            continue
        runner.last_sent[row["chat"]] = time.time()
        store.set_out(row["id"], status="sent", sent_ids=[str(x) for x in ids], error="")
        store.remember_sent(kind, account, row["chat"], [str(x) for x in ids], row["why"], row["ref"])
        if gap:
            wait = gap if wait is None else min(wait, gap)
    return wait


async def deliver(ask: dict, text: str, data: dict) -> bool:
    """cardagent.DELIVER：把主人在卡上定的那句话送回对方。ref = 渠道:账号:聊天[:话题]。账号不在 / 关了 → False（卡上写「没能告诉对方」）。"""
    from . import accounts
    kind, _, rest = str(ask.get("ref") or "").partition(":")
    account, _, lane = rest.partition(":")
    acc = accounts.find(kind, account)
    if acc is None or not acc.enabled or not lane or not text.strip():
        return False
    chat, _, topic = lane.partition(":")
    last = reply_state(kind, account, lane)
    say(kind, account, chat, text, topic=topic, reply_to=last.get("reply_to") or "", extra=last.get("extra") or {}, why="deliver",
        ref=str(ask.get("inbox_id") or ""), format="plain")
    return True


def reply_state(kind: str, account: str, lane: str) -> dict:
    """这个聊天最近进来的那一条：回它要的东西（回的哪条、邮件的主题和线程头）。"""
    try:
        v = json.loads(store.state(kind, account, f"reply:{lane}") or "{}")
    except ValueError:
        return {}
    return v if isinstance(v, dict) else {}


def remember_reply(kind: str, account: str, lane: str, reply_to: str, extra: dict) -> None:
    store.set_state(kind, account, **{f"reply:{lane}": json.dumps({"reply_to": reply_to, "extra": extra}, ensure_ascii=False)})
