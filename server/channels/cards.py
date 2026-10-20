"""卡发到渠道、在聊天里点（第 6 批，10.16）。

发哪些（账号的 cards）：
  turn（默认）  主人经渠道说的那一轮出的卡 → 发回那个聊天；这个账号上名片 Agent 出的卡（联系人约时间、问私事，dedupe card:<渠道>:<账号>:…）
               和配对的卡 → 发给这个账号上的主人
  all          所有新卡都发给主人（这个账号认得的主人）
  none         一张都不发（照样在 app 的收件箱里）
怎么点：能放按钮的渠道（Telegram）带「同意 / 拒绝」：callback_data = ib:a:<卡> / ib:r:<卡>（不超过 64 字节），只认主人的点击，
  而且这条消息得真是我们发的那张卡（channel_sent 核对）；点了走 inbox.act（和 app 里点的同一条路），点完把按钮去掉、写上结果。
  卡已经定了（在 app 里点过、过期了）回一句「已经定了」。一张卡多条的不给按钮，只说去 app 看。「改一下」要写话：回复那张卡
  （在 Telegram 里引用它说一句）就是「改一下」的那句话（router 认）。点不了的渠道（邮件）不发卡，主人那一轮的回信末尾说一句去 app 看。
配对的卡：kind social（线程是虚拟的 card，Agent 读不到；SOCIAL_HOOKS["pair"]）。同意 = 按账号默认的档位和 Agent 加成联系人、告诉对方
  可以说话了；改一下写档位（亲近 / 朋友 / 同学，close / friend / mate）和 / 或 Agent 的 id = 按写的批；拒绝不告诉对方。
"""
from __future__ import annotations

import logging
import re

from fastapi import HTTPException

from chat import _lock, log_activity, now_iso
from i18n import L

from . import accounts, hub, outbox, owner, people, store
from . import format as fmt

log = logging.getLogger("mousse.channels")
TIER_WORDS = {"close": ("close", "亲近"), "friend": ("friend", "朋友"), "mate": ("mate", "classmate", "同学")}


def tier_label(tier: str) -> str:
    return {"close": L("亲近", "Close"), "friend": L("朋友", "Friend"), "mate": L("同学", "Classmate")}.get(tier, tier)


def owner_chats(acc) -> list[str]:
    """这个账号能找到的主人的聊天（Telegram：主人的用户 id 就是私聊的 chat id；邮件：主人的地址）。"""
    out = list(acc.owners())
    for p in people.owners(acc.kind):
        if p["account"] in ("", acc.id):
            out.append(p["sender"])
    return list(dict.fromkeys(x for x in out if x))


def card_text(it: dict, *, result: str = "") -> tuple[str, list]:
    """一张卡 → (Markdown, 按钮)。别人能写进卡里的字（标题、理由）都转义了，不会变成格式或链接。"""
    import inbox
    kl = inbox.kind_label(it["kind"])
    lines = [f"**{fmt.md_escape(L(f'要你点头 · {kl}', f'Needs your OK · {kl}'))}** · {fmt.md_escape(it.get('sourceName') or '')}",
             fmt.md_escape(fmt.clip(it.get("title") or "", 200))]
    if it.get("why"):
        lines.append(fmt.md_escape(fmt.clip(it["why"], 400)))
    for c in (it.get("changes") or [])[:6]:
        lines.append("• " + fmt.md_escape(fmt.clip(c, 200)))
    items = it.get("items")
    buttons: list = []
    if result:
        lines.append(fmt.md_escape(result))
    elif items:
        lines.append(fmt.md_escape(L(f"这张卡有 {len(items)} 条：去 app 的收件箱里一条条看。", f"This card has {len(items)} items: go through them in the app's inbox.")))
    else:
        lines.append("_" + fmt.md_escape(L("要改一下：回复这条消息说怎么改。", "To change it, reply to this message saying how.")) + "_")
        buttons = [[{"text": "✅ " + fmt.clip(it.get("approveLabel") or L("同意", "Approve"), 30), "data": f"ib:a:{it['id']}"},
                    {"text": "✖ " + L("拒绝", "Decline"), "data": f"ib:r:{it['id']}"}]]
    return "\n".join(lines), buttons


def send_card(kind: str, account: str, chat: str, it: dict, topic: str = "") -> list[int]:
    cls = hub.channel_class(kind)
    if cls is None or not getattr(cls, "buttons", False):
        return []
    text, buttons = card_text(it)
    return outbox.say(kind, account, chat, text, topic=topic, why="card", ref=it["id"], buttons=buttons)


def dedupe_of(iid: str) -> str:
    import inbox
    with _lock, inbox.idb() as conn:
        r = conn.execute("SELECT dedupe FROM inbox WHERE id=?", (iid,)).fetchone()
    return str(r["dedupe"] or "") if r else ""


async def on_new_card(it: dict, revised: bool = False) -> None:
    """inbox 交进来一张新卡（或改好重新交的）：照各账号的 cards 发到主人的渠道（见文件开头）。"""
    targets: list[tuple[str, str, str, str]] = []
    entry = owner.ACTIVE.get(it.get("thread") or "")
    if entry is not None and not entry["run"].done:
        r = entry["route"]
        acc = accounts.find(r["kind"], r["account"])
        if acc is not None and acc.cards != "none":
            if r["buttons"]:
                targets.append((r["kind"], r["account"], r["chat"], r["topic"]))
            else:
                r["cards"] += 1
    dd = dedupe_of(it["id"]) if it.get("kind") == "social" else ""
    for acc in accounts.enabled():
        if acc.cards == "none":
            continue
        # 名片 Agent 的卡：dedupe = card:<渠道>:<ref>，ref = <渠道>:<账号>:<聊天>（router.contact）；配对的卡：pair:<渠道>:<账号>:<id>
        mine = dd.startswith((f"card:{acc.kind}:{acc.kind}:{acc.id}:", f"pair:{acc.kind}:{acc.id}:"))
        if mine or acc.cards == "all":
            targets += [(acc.kind, acc.id, c, "") for c in owner_chats(acc)]
    seen: set[tuple] = set()
    for t in targets:
        if t in seen:
            continue
        seen.add(t)
        try:
            send_card(*t[:3], it, topic=t[3])
        except Exception:  # noqa: BLE001 — 卡发不到渠道：app 里照样有
            log.exception("channel card %s → %s/%s", it.get("id"), t[0], t[1])


# —— 配对的卡 ————————————————————————————————————————————————————————————————————

async def pair_card(runner, p: dict) -> str | None:
    """陌生人要配对：出一张 kind social 的卡（dedupe pair:<渠道>:<账号>:<id>），能放按钮的渠道顺手发给主人。→ 卡的 id。"""
    import inbox
    acc = runner.acc
    label = runner.ch.label or acc.kind
    name = fmt.clip(p.get("name") or p["sender"], 40)
    agent = acc.agent
    body = inbox.ItemIn(kind="social", source="main", thread="card", level="quiet", dedupe=f"pair:{acc.kind}:{acc.id}:{p['id']}",
                        title=L(f"{label} 上的「{name}」想跟你的助理说话", f'"{name}" on {label} wants to talk to your assistant'),
                        why=L(f"配对码 {people.shown(p['code'])}，对方在 {label} 上的 id：{p['sender']}（名字是对方自己设的）。同意了 Ta 就成了联系人："
                              f"Ta 的话由名片 Agent 按「{tier_label(acc.tier)}」档答（零工具、只看这一档放出来的）。改一下可以写别的档位（亲近 / 朋友 / 同学）或 Agent。",
                              f"Pairing code {people.shown(p['code'])}; their {label} id: {p['sender']} (the name is one they chose). If you approve "
                              f"they become a contact: your card agent answers them at the {tier_label(acc.tier)} tier (no tools, only what that tier "
                              "shows). Revise to pick another tier (close / friend / mate) or Agent."),
                        changes=[L(f"加成联系人：{tier_label(acc.tier)}档，绑 {agent}", f"Add as a contact: {tier_label(acc.tier)} tier, bound to {agent}"),
                                 L("告诉 Ta 可以说话了", "Tell them they can talk now")],
                        approveLabel=L("同意配对", "Approve"), expiresAt=None)
    try:
        res = await inbox.add(body)
    except HTTPException as e:
        log.warning("pairing card failed: %s", e.detail)
        return None
    if not isinstance(res, dict):
        return None
    people.set_pair_card(p["id"], res["id"])
    return res["id"]


def parse_choice(note: str) -> tuple[str | None, str | None]:
    """「改一下」写的档位和 Agent：「亲近」「mate」「g-fit」……"""
    low = (note or "").lower()
    tier = next((t for t, words in TIER_WORDS.items() if any(w in low for w in words)), None)
    agent = None
    for tok in re.findall(r"[a-z0-9][a-z0-9_.-]{0,40}", low):
        if tok not in ("close", "friend", "mate", "classmate") and accounts.known_agent(tok):
            agent = tok
            break
    return tier, agent


def welcome(p: dict) -> None:
    """配对批了：告诉对方一句（固定的话，不调模型）。"""
    acc = accounts.find(p["channel"], p["account"])
    if acc is None:
        return
    from config import settings
    zh = (p.get("lang") or settings.language) == "zh"
    outbox.say(acc.kind, acc.id, p["chat"], "主人同意了，现在可以在这里跟我说话了。" if zh else "My owner approved you. You can talk to me here now.",
               why="pair", ref=p["id"], format="plain")


def close_card(iid: str | None, result: str, status: str = "done") -> None:
    """配对在 app 的设置里、命令行里批了：收件箱里那张卡跟着收掉。"""
    if not iid:
        return
    import inbox
    ts = now_iso()
    with _lock, inbox.idb() as conn:
        conn.execute("UPDATE inbox SET status=?, result=?, decided_at=?, updated_at=? WHERE id=? AND status IN ('pending','revising')",
                     (status, result, ts, ts, iid))


def decide(ident: str, approve: bool, *, tier: str | None = None, agent: str | None = None, from_card: bool = False) -> tuple[dict | None, str]:
    """批 / 拒一个配对（卡、设置、命令行都走这里）。批了告诉对方、记一行活动；不是从卡上点的顺手收卡。"""
    p, st = people.decide_pair(ident, approve, tier=tier, agent=agent)
    if st != "ok" or p is None:
        return p, st
    who = p.get("name") or p["sender"]
    if approve:
        welcome(p)
        person = people.person_by_id(p["person"]) if p.get("person") else None
        log_activity(L(f"批了配对：{who}（{p['channel']}）成了联系人，{tier_label((person or {}).get('tier', 'friend'))}档",
                       f"Approved pairing: {who} ({p['channel']}) is now a contact, {tier_label((person or {}).get('tier', 'friend'))} tier"), "approved")
    else:
        log_activity(L(f"拒了配对：{who}（{p['channel']}）", f"Declined pairing: {who} ({p['channel']})"), "denied")
    if not from_card:
        close_card(p.get("inbox_id"), L("已同意", "Approved") if approve else L("已拒绝", "Declined"), "done" if approve else "rejected")
    return p, st


async def pair_hook(it: dict, action: str, note: str = "") -> dict | None:
    """cardagent.SOCIAL_HOOKS["pair"]：配对的卡点了。"""
    p = people.pair(it["id"])
    if p is None or action == "withdraw":
        return None
    if action == "reject":
        decide(p["id"], False, from_card=True)
        return {"silent": True}
    tier, agent = parse_choice(note or str(it.get("note") or "")) if action == "revise" else (None, None)
    got, st = decide(p["id"], True, tier=tier, agent=agent, from_card=True)
    if st != "ok":
        return {"failed": L("这个配对已经过期了，或者已经定过了", "This pairing request has expired or was already settled"), "silent": True}
    person = people.person_by_id(got["person"]) if got and got.get("person") else None
    res = {"result": L(f"加成了联系人（{tier_label((person or {}).get('tier', 'friend'))}档，绑 {(person or {}).get('agent') or 'main'}）",
                       f"Added as a contact ({tier_label((person or {}).get('tier', 'friend'))} tier, bound to {(person or {}).get('agent') or 'main'})"),
           "silent": True}
    if action == "revise":
        res["status"] = "done"
    return res


# —— 聊天里点 ————————————————————————————————————————————————————————————————————

async def on_callback(runner, ib, rid: int = 0) -> dict:
    """按钮点了（只认主人）：ib:a / ib:r → inbox.act；aq → ask_user 的选项。"""
    import inbox
    acc, ch = runner.acc, runner.ch
    cb = ib.callback or {}
    data = str(cb.get("data") or "")
    if not people.is_owner(acc, ib.sender):
        await ch.answer_callback(cb, L("只有主人能点这个。", "Only the owner can use this."))
        return {"status": "skipped", "who": "", "why": "callback_not_owner", "ref": ""}
    if data.startswith("aq:"):
        await ch.answer_callback(cb)
        _, short, idx = (data.split(":", 2) + ["", ""])[:3]
        return await owner.answer_option(runner, ib, rid, short, int(idx) if idx.isdigit() else -1)
    m = re.fullmatch(r"ib:([ar]):([A-Za-z0-9_-]{1,40})", data)
    if not m:
        await ch.answer_callback(cb, L("这个按钮认不出来了。", "This button isn't recognised."))
        return {"status": "skipped", "who": "owner", "why": "callback_unknown", "ref": ""}
    action, iid = {"a": "approve", "r": "reject"}[m.group(1)], m.group(2)
    sent = store.sent_one(acc.kind, acc.id, ib.chat, str(cb.get("message_id") or ""))
    if sent is None or sent["kind"] != "card" or sent["ref"] != iid:
        await ch.answer_callback(cb, L("这个按钮认不出来了。", "This button isn't recognised."))
        return {"status": "skipped", "who": "owner", "why": "callback_unknown", "ref": iid}
    try:
        res = await inbox.act(iid, inbox.ActIn(action=action))
        it = res.get("item") or inbox.item(iid)
        word = L("已同意", "Approved") if action == "approve" else L("已拒绝", "Declined")
        result = f"{'✅' if action == 'approve' else '✖'} {word}" + (f"：{fmt.clip(it.get('result') or '', 200)}" if it.get("result") else "")
        await ch.answer_callback(cb, word)
    except HTTPException as e:
        try:
            it = inbox.item(iid)
        except HTTPException:
            it = None
        result = L(f"这张卡已经定了（{it['status']}）", f"This card is already settled ({it['status']})") if it else L("这张卡已经不在了", "This card is gone")
        await ch.answer_callback(cb, result)
        if e.status_code not in (404, 409) or it is None:
            return {"status": "done", "who": "owner", "why": "callback_failed", "ref": iid}
    text, _ = card_text(it, result=result)
    try:
        await ch.edit(ib.chat, str(cb.get("message_id") or ""), fmt.plain(text), topic=ib.topic, buttons=[])
    except Exception:  # noqa: BLE001 — 改不了那条消息：按钮留着，再点会说已经定了
        pass
    return {"status": "done", "who": "owner", "why": f"callback_{action}", "ref": iid}


async def revise_by_reply(runner, ib, iid: str) -> dict | None:
    """主人在聊天里回复了一张卡：那句话就是「改一下」。卡已经不在等了 → None（当普通的话）。"""
    import inbox
    try:
        it = inbox.item(iid)
    except HTTPException:
        return None
    if it["status"] != "pending" or it.get("items"):
        return None
    try:
        await inbox.act(iid, inbox.ActIn(action="revise", note=ib.text.strip()))
    except HTTPException:
        return None
    from . import router
    router.reply(runner, ib, L("好，按你说的改。", "OK, I'll change it as you said."), why="revise", ref=iid)
    return {"status": "done", "who": "owner", "why": "revise", "ref": iid}


def install() -> None:
    """服务启动时挂上（main.py）：收件箱新卡的钩子、名片 Agent 的 DELIVER、配对卡的钩子。重启后之前的卡点了也照样走。"""
    import cardagent
    import inbox
    hooks = getattr(inbox, "NEW_HOOKS", None)
    if hooks is not None and on_new_card not in hooks:
        hooks.append(on_new_card)
    for kind in hub.KINDS:
        cardagent.DELIVER.setdefault(kind, outbox.deliver)
    cardagent.SOCIAL_HOOKS["pair"] = pair_hook
