"""往渠道发消息（第 6 批，3.21 / 9.6）。

message 工具（给主人回合，engine/tools.py 的 for_agent 登记；没开渠道就没有）：
  action contacts       只读：列出能发的联系人（main 看全部，别的 Agent 只看绑给自己的；名字是对方自己设的，包成资料）
  action send, to owner 发给主人自己（主人在渠道上的私聊 / 邮箱）：和推送一样，直接发、不用点头
  action send, to <id>  发给一个联系人：对外、撤不回，一律交一张 kind tool 的卡（engine/calls.py），主人点了同意服务器才照原样发一次
send_to_owner(text, channel=None)：服务器内部（以后定时任务、通知）往主人的渠道投一条，见下。接 engine/jobs.py 那一下归指挥台在第 4 批
  合了以后做：jobs 的「投到哪」多一种 channel 时调它就行。
"""
from __future__ import annotations

from i18n import L

from . import accounts, cards, hub, outbox, people
from . import format as fmt

NAME = "message"
ORDER = ("telegram", "discord", "email")   # 发给主人时先试哪个渠道


def owner_targets(channel: str | None = None) -> list[tuple]:
    """主人在哪些渠道上找得到：[(账号, 聊天)]，Telegram 在前。"""
    accs = sorted(accounts.enabled(channel), key=lambda a: (ORDER.index(a.kind) if a.kind in ORDER else 9, a.id))
    return [(a, c) for a in accs for c in cards.owner_chats(a)]


def send_to_owner(text: str, channel: str | None = None, *, why: str = "notify", ref: str = "") -> dict:
    """往主人自己的渠道发一条（不用点头，和推送一样）：channel 不给 = 第一个找得到主人的（Telegram 优先），给了就只用那个渠道。
    → {"ok": True, "channel", "account", "ids"}；没有开着的、认得主人的渠道 → {"ok": False, "error"}。text 是 Markdown，渠道自己转。"""
    text = (text or "").strip()
    if not text:
        return {"ok": False, "error": L("要发的话是空的", "Nothing to send")}
    targets = owner_targets(channel)
    if not targets:
        return {"ok": False, "error": L("没有开着的、认得主人的渠道（先在渠道里用认主人的码认一下）",
                                        "No enabled channel knows the owner yet (claim it with an owner code first)")}
    acc, chat = targets[0]
    ids = outbox.say(acc.kind, acc.id, chat, text, why=why, ref=ref)
    return {"ok": True, "channel": acc.kind, "account": acc.id, "ids": ids}


def visible(me: str) -> list[dict]:
    """这个 Agent 能发的联系人。"""
    out = []
    for p in people.people(role="contact"):
        if p["status"] != "active":
            continue
        if me == "main" or (p.get("agent") or "main") == me:
            out.append(p)
    return out


def _target(me: str, to: str) -> dict | None:
    p = people.person_by_id(to)
    if p is None or p["role"] != "contact" or p["status"] != "active":
        return None
    if me != "main" and (p.get("agent") or "main") != me:
        return None
    return p


def _account_for(p: dict):
    acc = accounts.find(p["channel"], p["account"]) if p.get("account") else None
    if acc is None or not acc.enabled:
        acc = next(iter(accounts.enabled(p["channel"])), None)
    return acc


def tool(me: str):
    """给这一轮的 Agent 的 message 工具；没有开着的渠道 → None。"""
    if not accounts.enabled():
        return None
    from engine import calls
    from engine import tools as engine_tools
    from engine.loop import ToolFailure

    def classify(args: dict) -> tuple[str, str]:
        if str(args.get("action") or "send") == "contacts":
            return engine_tools.READ, "只列出能发的联系人"
        if str(args.get("to") or "owner").strip() in ("", "owner"):
            return engine_tools.UNDO, "发给主人自己的渠道：和推送一样，不用点头"
        return engine_tools.ASK, "发给别人：对外、撤不回，先交卡等主人点头"

    async def run(args: dict, ctx) -> str:
        action = str(args.get("action") or "send")
        if action == "contacts":
            rows = visible(ctx.agent or me)
            if not rows:
                return L("（没有能发的联系人）", "(no contacts you can message)")
            lines = [f"- {p['id']}: {fmt.clip(' '.join(str(p['name'] or p['sender']).split()), 60)} ({p['channel']}, {p['tier']})" for p in rows]
            return engine_tools.wrap_untrusted("channels.contacts", "\n".join(lines))
        if action != "send":
            raise ToolFailure(L("action 只能是 send 或 contacts", "action must be send or contacts"))
        text = str(args.get("text") or "").strip()
        if not text:
            raise ToolFailure(L("text 是空的", "text is empty"))
        to = str(args.get("to") or "owner").strip() or "owner"
        if to != "owner":  # 发给别人的从来不在这里发：关卡交卡，主人点了以后 run_approved 发
            raise ToolFailure(L("发给别人要先交卡等主人点头", "Messages to others need the owner's OK first"))
        who = ctx.agent or me
        if who != "main":
            import inbox
            text = f"**{fmt.md_escape(inbox.source_name(who, inbox.names()))}**\n{text}"
        res = send_to_owner(text, args.get("channel") or None, why="message")
        if not res.get("ok"):
            raise ToolFailure(res.get("error") or "failed")
        return L(f"发给主人了（{res['channel']}）。", f"Sent to the owner ({res['channel']}).")

    async def ask(args: dict, ctx) -> dict | str:
        who = ctx.agent or me
        to = str(args.get("to") or "").strip()
        text = str(args.get("text") or "").strip()
        p = _target(who, to)
        if p is None:
            return L(f"没跑：没有 {to} 这个联系人，或者它不归你（先用 action contacts 看能发给谁）。",
                     f"Not run: there's no contact {to}, or it isn't yours (use action contacts to see who you can message).")
        if not text:
            return L("没跑：text 是空的。", "Not run: text is empty.")
        if _account_for(p) is None:
            return L(f"没跑：{p['channel']} 这个渠道现在没开。", f"Not run: the {p['channel']} channel isn't enabled.")
        import inbox
        name = p["name"] or p["sender"]
        label = accounts.NAMES.get(p["channel"], p["channel"])
        card = {"title": L(f"经 {label} 发给 {fmt.clip(name, 30)}：{fmt.clip(text, 40)}", f"Send to {fmt.clip(name, 30)} on {label}: {fmt.clip(text, 40)}"),
                "why": L(f"{inbox.source_name(who, inbox.names())}想经 {label} 给 {name} 发一条消息。对外、撤不回，所以先问你。",
                         f"{inbox.source_name(who, inbox.names())} wants to message {name} on {label}. It goes out and can't be undone, so it asks you first."),
                "changes": [L(f"发给：{name}（{label}，{cards.tier_label(p['tier'])}档）", f"To: {name} ({label}, {cards.tier_label(p['tier'])} tier)"),
                            L(f"内容：{fmt.clip(text, 200)}", f"Text: {fmt.clip(text, 200)}")],
                "detail": L("同意后，服务器照下面的原话发一次。", "If you approve, the server sends exactly this, once.") + f"\n\n```\n{text[:4000]}\n```",
                "approveLabel": L("发出去", "Send")}
        return await calls.ask("channels", NAME, {"action": "send", "to": to, "text": text}, ctx, card)

    return calls.AskTool(
        name=NAME, kinds=("owner",), run=run, classify=classify, ask_user=ask, timeout=30,
        title=lambda a: L("列出联系人", "List contacts") if str(a.get("action") or "send") == "contacts" else
        L("发给主人", "Message the owner") if str(a.get("to") or "owner") in ("", "owner") else L("给联系人发消息（等点头）", "Message a contact (needs OK)"),
        description=L("往渠道（Telegram、邮件）发消息。to = owner（默认）：发给你的主人自己，和推送一样，直接发、不用点头，用在要主人知道的事上；"
                      "to = 联系人的 id（先用 action contacts 列出来）：发给主人的一个联系人，对外、撤不回，会先交一张卡等主人点头，点了服务器才发。"
                      "text 是要发的话（可以用 Markdown）；channel 只在要指定渠道时写。",
                      "Send a message on a channel (Telegram, email). to = owner (default): to your owner themselves, like a push "
                      "notification, sent right away without approval; use it for things they should know. to = a contact's id (list them "
                      "with action contacts): to one of the owner's contacts; it goes out and can't be undone, so it makes a card for the "
                      "owner's OK and the server sends it only once they approve. text is the message (Markdown is fine); set channel only "
                      "to pick a specific channel."),
        schema={"type": "object", "required": [], "properties": {
            "action": {"type": "string", "enum": ["send", "contacts"], "description": L("send（默认）或 contacts", "send (default) or contacts")},
            "to": {"type": "string", "description": L("owner（默认）或联系人的 id（cp-…）", "owner (default) or a contact id (cp-…)")},
            "text": {"type": "string", "description": L("要发的话", "The message")},
            "channel": {"type": "string", "description": L("telegram / email；不写 = 找得到主人的第一个", "telegram / email; leave empty for the first that reaches the owner")}}})


async def run_approved(name: str, args: dict, row) -> str:
    """engine/calls.py 的 RUNNERS["channels"]：主人在卡上点了同意，照原样发一次。"""
    from engine.loop import ToolFailure
    p = people.person_by_id(str(args.get("to") or ""))
    if p is None or p["role"] != "contact" or p["status"] != "active":
        raise ToolFailure(L("这个联系人已经不在了，没发", "That contact is gone; nothing was sent"))
    acc = _account_for(p)
    if acc is None:
        raise ToolFailure(L(f"{p['channel']} 这个渠道现在没开，没发", f"The {p['channel']} channel isn't enabled; nothing was sent"))
    ids = outbox.say(acc.kind, acc.id, p["sender"], str(args.get("text") or ""), why="message", ref=str(row["inbox"] or ""), format="plain")
    hub.wake_out(acc.kind, acc.id)
    return L(f"排进了发给 {p['name'] or p['sender']} 的队列（{acc.kind}，{len(ids)} 段）", f"Queued for {p['name'] or p['sender']} ({acc.kind}, {len(ids)} part(s))")


def install() -> None:
    from engine import calls
    calls.register("channels", run_approved)
