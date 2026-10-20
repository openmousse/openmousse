"""一条进来以后怎么办（第 6 批）。按顺序：

  1. 按钮回调 → cards.on_callback（只认主人的点击）
  2. 不理的：渠道自己看出来的（自动回复、退信……）、改过的消息（原来那条已经处理过了）、机器人发的（防机器人互相回环，allow_bots 能开）、
     自己发的
  3. 认人（people.py）：主人 / 联系人 / 拉黑的 / 陌生人；邮件没验过的一律当陌生人
  4. 私聊里的 /start <码>：认主人（码对了就是主人；聊天里没有别的路能变成主人）
  5. 群：默认关；开了的群（allow 里写了，或者 "*"）里要 @ 机器人、回复它、或者叫 Agent 的名字才答；没叫的只记进群历史（被叫时当资料）
  6. 每人上限（一小时几条）；急停（第 4 批的 engine/estop.paused()）停着的时候只理主人
  7. 路由：主人 → 主人回合（owner.py，和 app 里说的一样）；/stop = app 里点停
           联系人 → 名片 Agent（cardagent.answer：零工具、按档位只看放出来的、Doorman 复查、要主人表态的出卡；ref = 渠道:账号:聊天）
           陌生人 → 私聊：配对（回一个码、不调模型）/ allowlist（名单上的当联系人，别的不理）/ 不理；群里：名片 Agent 的陌生人那一档
             （card.strangers 没开就什么都不回）
处理出错的这一条隔一会儿再试（同一个聊天后面的等着），试三次进死信。
"""
from __future__ import annotations

import asyncio
import logging
import re
import time

from fastapi import HTTPException

from chat import log_activity
from config import settings
from i18n import L

from . import accounts, outbox, owner, people, store
from .base import Inbound, norm_id

log = logging.getLogger("mousse.channels")
RETRIES = 3
PER_HOUR = {"owner": 600, "contact": 30, "stranger": 5}   # 每人一小时最多进来几条（再多的记下来、不理）；channels.<渠道>.limits 能改


def done(status: str = "done", who: str = "", why: str = "", ref: str = "") -> dict:
    return {"status": status, "who": who, "why": why, "ref": ref}


def skip(why: str, who: str = "") -> dict:
    return done("skipped", who, why)


async def process(runner, row) -> None:
    """hub 交来的一条（已经抢成 working）：处理完写下结果；出错隔一会儿再试，试够了进死信。"""
    try:
        ib = Inbound.from_json(row["payload"])
        res = await handle(runner, ib, row["id"])
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001 — 这一条处理不了：记下来，按次数重试
        log.exception("channel %s/%s: inbound %s failed", runner.acc.kind, runner.acc.id, row["id"])
        tries = int(row["tries"]) + 1
        if tries >= RETRIES:
            store.finish(row["id"], "dead", error=f"{type(e).__name__}: {str(e)[:300]}")
        else:
            store.finish(row["id"], "pending", error=f"{type(e).__name__}: {str(e)[:300]}", next_at=time.time() + 5 * 3 ** tries)
        return
    store.finish(row["id"], res["status"], who=res.get("who") or "", why=res.get("why") or "", ref=res.get("ref") or "")


def identify(acc, ib: Inbound) -> tuple[str, dict | None]:
    """→ (owner | contact | blocked | stranger | unverified, 联系人那一行)。"""
    if acc.kind == "email" and not ib.auth.get("verified"):
        return "unverified", None
    if people.is_owner(acc, ib.sender):
        return "owner", None
    p = people.person(acc.kind, ib.sender)
    if p and p["role"] == "contact":
        if p["status"] != "active":
            return "blocked", p
        if ib.meta.get("name") and p["via"] != "manual":
            people.seen_name(p["id"], str(ib.meta.get("name")))
        return "contact", p
    if p and p["role"] == "owner":  # 收回了主人身份的（status 不是 active）：当陌生人
        return "stranger", None
    if ib.sender and norm_id(acc.kind, ib.sender) in acc.allow_from():
        p = people.add_contact(acc.kind, ib.sender, name=str(ib.meta.get("name") or ""), tier=acc.tier, agent=acc.agent, account=acc.id,
                               via="allowlist")
        return "contact", p
    return "stranger", None


def paused() -> bool:
    """第 4 批的急停（engine/estop.py 的 paused()）：在的话停着的时候自动回复（名片 Agent 答外人、发配对码）不回，主人照常。
    还没合进来就当没停。"""
    try:
        from engine import estop  # type: ignore[attr-defined]
    except ImportError:
        return False
    try:
        return bool(estop.paused())
    except Exception:  # noqa: BLE001
        return False


def per_hour(acc, who: str) -> int:
    lim = acc.get("limits")
    n = lim.get(who) if isinstance(lim, dict) else None
    return int(n) if isinstance(n, int) and n > 0 else PER_HOUR.get(who, PER_HOUR["stranger"])


def names(acc, ib: Inbound) -> list[str]:
    """群里叫哪些名字算叫它：groups.names 里写的 + 这个群绑的 Agent 的名字（main 是 app 的名字）。"""
    out = [str(x).strip() for x in acc.groups().get("names") or [] if str(x).strip()]
    agent = accounts.bound_agent(acc, ib.chat, ib.sender)
    if agent == "main":
        out.append(settings.app_name)
    else:
        try:
            import data
            if nm := data.thread_names().get(agent):
                out.append(nm)
        except Exception:  # noqa: BLE001
            pass
    return [n for n in dict.fromkeys(out) if len(n) >= 2]


def called(name: str, text: str) -> bool:
    if not name or not text:
        return False
    if name.isascii():
        return re.search(rf"(?<![\w@]){re.escape(name)}(?!\w)|@{re.escape(name)}(?!\w)", text, re.I) is not None
    return name in text


def group_gate(acc, ib: Inbound) -> str:
    """群：off（没开）/ not_allowed（不是开了的那几个群）/ history（没叫它：只记下来）/ go。"""
    g = acc.groups()
    if g.get("enabled") is not True:
        return "off"
    allow = {str(x) for x in g.get("allow") or []}
    if "*" not in allow and str(ib.chat) not in allow:
        return "not_allowed"
    if g.get("require_mention", True) is not False and not (ib.mentioned or ib.reply_to_bot or any(called(n, ib.text) for n in names(acc, ib))):
        return "history"
    return "go"


def media_note(ib: Inbound) -> str:
    """联系人、陌生人发的媒体：只记一个占位（类型、大小），不下载。"""
    out = []
    for m in ib.media:
        zh, en = {"image": ("一张图片", "an image"), "voice": ("一条语音", "a voice message"), "audio": ("一段音频", "an audio file"),
                  "video": ("一段视频", "a video"), "sticker": ("一张贴纸", "a sticker")}.get(m.get("kind"), ("一个文件", "a file"))
        size = human(m.get("size")) if m.get("size") else ""
        out.append(L(f"[发了{zh}" + (f"，{size}" if size else "") + "，没打开]", f"[sent {en}" + (f", {size}" if size else "") + ", not opened]"))
    return " ".join(out)


def human(n) -> str:
    try:
        n = int(n)
    except (TypeError, ValueError):
        return ""
    return f"{n / 1024 / 1024:.1f} MB" if n >= 1024 * 1024 else f"{max(1, n // 1024)} KB"


def question(ib: Inbound) -> str:
    text = (ib.text or "").strip() or str(ib.meta.get("forwarded") or "").strip()  # 外人转发来的：转发的字就是他发来的（照样只进外人回合）
    return " ".join(x for x in (text, media_note(ib)) if x)


async def handle(runner, ib: Inbound, rid: int) -> dict:
    acc, ch = runner.acc, runner.ch
    if ib.callback:
        from . import cards
        return await cards.on_callback(runner, ib, rid)
    if ib.skip:
        return skip(ib.skip)
    if ib.edited:
        return skip("edited")
    if ib.is_bot and acc.get("allow_bots") is not True:
        return skip("bot")
    if ch.me.get("id") and ib.sender == str(ch.me.get("id")):
        return skip("self")
    who, person = identify(acc, ib)
    group = ib.chat_type == "group"
    if who == "blocked":
        return skip("blocked", who)
    if ib.command == "start" and not group and ib.args.strip():
        return await claim(runner, ib, who)
    if group:
        gate = group_gate(acc, ib)
        if gate == "history":
            store.remember_group(acc.kind, acc.id, ib.lane, ib.sender, str(ib.meta.get("name") or ""),
                                 (ib.text or str(ib.meta.get("forwarded") or "") or media_note(ib))[:2000],
                                 ib.message_id)
            return skip("history", who)
        if gate != "go":
            return skip(f"group_{gate}", who)
    limit_key = "owner" if who == "owner" else "contact" if who == "contact" else "stranger"
    if store.count_from(acc.kind, ib.sender, 3600, upto=rid) > per_hour(acc, limit_key):
        return skip("limit", who)
    if who != "owner" and paused():
        return skip("paused", who)
    if who == "owner":
        if ib.command == "stop":
            return await stop(runner, ib)
        if ib.command == "start" and not ib.args.strip():
            reply(runner, ib, L("在的，直接说就行。", "I'm here. Just say what you need."), why="hello")
            return done("done", who, "hello")
        if ib.reply_to and ib.text.strip() and not ib.media and not ib.meta.get("forwarded"):  # 回复了一张卡：那句话就是「改一下」（cards.py）
            sent = store.sent_one(acc.kind, acc.id, ib.chat, ib.reply_to)
            if sent is not None and sent["kind"] == "card":
                from . import cards
                if (res := await cards.revise_by_reply(runner, ib, sent["ref"])) is not None:
                    return res
        return await owner.turn(runner, ib, rid)
    if who == "contact" and person is not None:
        return await contact(runner, ib, person)
    if group:
        return await stranger_in_group(runner, ib)
    return await stranger(runner, ib, who)


def reply(runner, ib: Inbound, text: str, *, why: str, ref: str = "", fmt: str = "plain", buttons: list | None = None) -> list[int]:
    """回进来的这一条（回到同一个聊天、同一个话题；邮件带上线程头）。"""
    ch = runner.ch
    return outbox.say(runner.acc.kind, runner.acc.id, ib.chat, text, topic=ib.topic, reply_to=ch.reply_ref(ib), extra=ch.reply_extra(ib),
                      why=why, ref=ref, format=fmt, buttons=buttons)


async def claim(runner, ib: Inbound, who: str) -> dict:
    """私聊里的 /start <码>：认主人。码对了 → 记成主人；错了、过期了说一句；试太多次什么都不回。"""
    acc = runner.acc
    if acc.kind == "email":
        return skip("claim_email", who)
    res = people.redeem_claim(acc.kind, ib.args, ib.sender)
    zh = runner.ch.language(ib) == "zh"
    if res == "ok":
        people.add_owner(acc.kind, ib.sender, name=str(ib.meta.get("name") or ""), account=acc.id)
        log_activity(L(f"在 {runner.ch.label} 上认出了你（账号 {acc.id}）：以后在那里说的话就是你本人说的",
                       f"Recognised you on {runner.ch.label} (account {acc.id}): what you say there now counts as you"), "edit")
        reply(runner, ib, "好的，认出你了。以后你在这里说的话，就和在 app 里说的一样。" if zh else
              "Got it, I know it's you now. What you say here counts the same as in the app.", why="claim")
        return done("done", "owner", "claim")
    if res == "limited":
        return skip("claim_limited", who)
    reply(runner, ib, "这个码不对，或者已经过期 / 用过了。在 app 里重新出一个。" if zh else
          "That code is wrong, expired or already used. Make a new one in the app.", why="claim")
    return done("done", who, "claim_bad")


async def stop(runner, ib: Inbound) -> dict:
    """主人发的 /stop：等同 app 里点停（停的是这个聊天绑的那个对话）。"""
    import chat
    thread = owner.thread_for(runner.acc, ib)
    res = await chat.stop(chat.StopBody(thread=thread))
    zh = runner.ch.language(ib) == "zh"
    if res.get("stopped"):
        text = "停了。" if zh else "Stopped."
    else:
        text = "现在没有在回的。" if zh else "Nothing is running right now."
    reply(runner, ib, text, why="stop")
    return done("done", "owner", "stop")


async def contact(runner, ib: Inbound, person: dict) -> dict:
    """联系人 → 名片 Agent（外人回合：零工具、花不了套餐、按档位只看放出来的、Doorman 复查、要主人表态的出 kind social 的卡）。"""
    import cardagent
    acc, ch = runner.acc, runner.ch
    q = question(ib)
    if not q.strip():
        return skip("empty", "contact")
    ref = f"{acc.kind}:{acc.id}:{ib.lane}"[:120]
    outbox.remember_reply(acc.kind, acc.id, ib.lane, ch.reply_ref(ib), ch.reply_extra(ib))
    typing = owner.keep_typing(runner, ib.chat, ib.topic)
    try:
        res = await cardagent.answer({"id": person["id"], "tier": person["tier"], "name": person["name"] or L("联系人", "A contact")}, q,
                                     channel=acc.kind, ref=ref)
    except HTTPException:
        return skip("empty", "contact")
    finally:
        typing.cancel()
    text = str(res.get("text") or "").strip()
    if res.get("refused") or not text:
        return done("done", "contact", "refused", str(res.get("log_id") or ""))
    if res.get("limited") and store.sent_to(acc.kind, acc.id, ib.chat, 86400, why="limit"):
        return done("done", "contact", "limited", str(res.get("log_id") or ""))
    reply(runner, ib, text, why="limit" if res.get("limited") else "card", ref=str(res.get("log_id") or ""))
    return done("done", "contact", "answered", str(res.get("log_id") or ""))


async def stranger_in_group(runner, ib: Inbound) -> dict:
    """群里叫了它的陌生人：名片 Agent 的陌生人那一档（card.strangers 没开 = 不调模型、什么都不回；开了也只有公开的、不出卡）。"""
    import cardagent
    acc, ch = runner.acc, runner.ch
    q = question(ib)
    if not q.strip() or not cardagent.strangers_allowed():
        return skip("stranger", "stranger")
    ref = f"{acc.kind}:{acc.id}:{ib.lane}"[:120]
    outbox.remember_reply(acc.kind, acc.id, ib.lane, ch.reply_ref(ib), ch.reply_extra(ib))
    try:
        res = await cardagent.answer(None, q, channel=acc.kind, ref=ref, kid=f"{acc.kind}:{ib.sender}", name=str(ib.meta.get("name") or ""))
    except HTTPException:
        return skip("empty", "stranger")
    text = str(res.get("text") or "").strip()
    if res.get("refused") or not text:
        return skip("stranger", "stranger")
    reply(runner, ib, text, why="card", ref=str(res.get("log_id") or ""))
    return done("done", "stranger", "answered", str(res.get("log_id") or ""))


async def stranger(runner, ib: Inbound, who: str) -> dict:
    """私聊的陌生人：配对（默认，邮件除外）/ allowlist（名单上的已经在 identify 里成了联系人，到这里的都不理）/ 不理。"""
    acc = runner.acc
    if who == "unverified":
        return skip("unverified", who)
    if acc.dm != "pairing":
        return skip(f"dm_{acc.dm}", who)
    name = str(ib.meta.get("name") or "")
    p, created = people.pair_request(acc, ib.sender, ib.chat, name, runner.ch.language(ib))
    if p is None:
        return skip("pairs_full", who)
    if not created:
        return skip("pair_pending", who)
    zh = runner.ch.language(ib) == "zh"
    code = people.shown(p["code"])
    if zh:
        text = (f"你好！我是 {settings.app_name} 的助理，还不认识你，所以暂时不能回复。\n\n你的配对码：{code}（一小时内有效）\n"
                f"你在{runner.ch.label}上的 id：{ib.sender}\n请把配对码发给我的主人；主人同意以后，你就可以在这里跟我说话了。")
    else:
        text = (f"Hi! I'm {settings.app_name}'s assistant. I don't know you yet, so I can't reply for now.\n\nYour pairing code: {code} "
                f"(valid for one hour)\nYour {runner.ch.label} id: {ib.sender}\nSend the code to my owner; once they approve, you can talk to me here.")
    reply(runner, ib, text, why="pair", ref=p["id"])
    from . import cards
    await cards.pair_card(runner, p)
    return done("done", who, "pairing", p["id"])
