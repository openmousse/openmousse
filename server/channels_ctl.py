#!/usr/bin/env python3
"""渠道（第 6 批）的命令行：mousse channels …（../server/channels/，和 /api/setup/channels… 是同一套，直接读写同一个库，服务器不用开着）。

  mousse channels list                       渠道和账号（不带密钥）、主人、联系人数、待批的配对
  mousse channels status [--json]            每个账号：连上没有、最后一次收到、最后一个错、死信、配置的毛病
  mousse channels owner-code [--channel telegram] [--minutes 10]
                                             出一个认主人的码：在 Telegram / Discord 里私聊机器人发 /start <码>，就把那个账号记成你（一次有效）
  mousse channels pairs [--all]              待批的配对（陌生人的码）
  mousse channels approve <码或 id> [--tier close|friend|mate] [--agent <id>]    批一个配对：成联系人，告诉对方可以说话了
  mousse channels reject <码或 id>           拒一个配对（不告诉对方）
  mousse channels contacts                   联系人
  mousse channels contacts set <id> [--tier …] [--agent …] [--block | --unblock]
认主人、批配对、改联系人只能你自己在终端里跑：在 Agent 跑的命令里（环境里有 MOUSSE_AGENT / MOUSSE_VIA）一律不跑，
聊天里的话没法借 Agent 的手把谁升成主人。
退出码：0 好了；1 没成（说为什么）；2 参数不对。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from i18n import L


def usage() -> str:
    return L(__doc__, """Channels (batch 6) on the command line: mousse channels … (../server/channels/; the same as /api/setup/channels…, reading
and writing the same database, so the server needn't be running).

  mousse channels list                       channels and accounts (no secrets), owners, contact count, pending pairing requests
  mousse channels status [--json]            per account: connected or not, last message in, last error, dead letters, config problems
  mousse channels owner-code [--channel telegram] [--minutes 10]
                                             make an owner code: send /start <code> to your bot in a private chat on Telegram or Discord, and
                                             that account is you (works once)
  mousse channels pairs [--all]              pending pairing requests (strangers' codes)
  mousse channels approve <code or id> [--tier close|friend|mate] [--agent <id>]   approve one: they become a contact and are told so
  mousse channels reject <code or id>        decline one (they aren't told)
  mousse channels contacts                   contacts
  mousse channels contacts set <id> [--tier …] [--agent …] [--block | --unblock]
Owner codes, approvals and contact changes only run in your own terminal: inside an Agent's command (MOUSSE_AGENT / MOUSSE_VIA in the
environment) they refuse, so nothing said in a chat can use an Agent's hands to make someone the owner.
Exit codes: 0 done; 1 didn't work (says why); 2 bad arguments.
""")


def refuse_in_agent() -> None:
    if os.environ.get("MOUSSE_AGENT") or os.environ.get("MOUSSE_VIA"):
        print(L("这个只能你自己在终端里跑（Agent 跑的命令里不跑：认主人、批配对得是你本人）。",
                "Run this yourself in a terminal (not inside an Agent's command: owner codes and approvals must come from you)."), file=sys.stderr)
        sys.exit(1)


def show(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def cmd_list(_a) -> int:
    from channels import accounts, people
    rows = []
    for acc in accounts.accounts():
        rows.append(f"{acc.kind}/{acc.id}: {'on' if acc.enabled else 'off'} · agent {acc.agent} · dm {acc.dm} · cards {acc.cards}")
    print("\n".join(rows) or L("（server.json 里没有配渠道）", "(no channels in server.json)"))
    owners = [f"  {p['channel']}: {p['sender']} {p['name']}" for k in accounts.KINDS for p in people.owners(k)]
    if owners:
        print(L("主人：", "Owners:"))
        print("\n".join(owners))
    contacts = [p for p in people.people(role="contact") if p["status"] == "active"]
    print(L(f"联系人 {len(contacts)} 个；待批的配对 {len(people.pairs('pending'))} 个", f"{len(contacts)} contacts; {len(people.pairs('pending'))} pending pairing requests"))
    if not accounts.builtin():
        print(L("（claw 不是 builtin：渠道归它自己，这里不起）", "(the claw isn't builtin: channels belong to it and don't start here)"))
    return 0


def cmd_status(a) -> int:
    import asyncio

    from channels import api
    res = asyncio.run(api.status())
    if a.json:
        show(res)
        return 0
    for r in res["accounts"]:
        line = f"{r['kind']}/{r['id']}: {r['state']}" + (f" ({r['detail']})" if r["detail"] else "")
        line += L(f" · 最后一次收到 {r['lastIn'] or '—'}", f" · last in {r['lastIn'] or '—'}")
        if r["lastError"]:
            line += L(f" · 最后一个错：{r['lastError'][:120]}", f" · last error: {r['lastError'][:120]}")
        if r["deadLetters"]:
            line += L(f" · 死信 {r['deadLetters']}", f" · {r['deadLetters']} dead letters")
        print(line)
        for p in r["problems"]:
            print("  ! " + p)
        for p in r["pendingPairs"]:
            print(L(f"  待批：{p['code']} {p['name']}（{p['sender']}）", f"  pending: {p['code']} {p['name']} ({p['sender']})"))
    if not res["accounts"]:
        print(L("（server.json 里没有配渠道）", "(no channels in server.json)"))
    return 0


def cmd_owner_code(a) -> int:
    refuse_in_agent()
    from channels import accounts, people, store
    code, expires = people.new_claim(a.channel or "", a.minutes)
    print(L(f"认主人的码：{people.shown(code)}（{a.minutes} 分钟内有效，只能用一次，到 {expires}）",
            f"Owner code: {people.shown(code)} (works once, for {a.minutes} minutes, until {expires})"))
    print(L(f"在 Telegram 或 Discord 里私聊你的机器人发：/start {people.normalize(code)}",
            f"In a private chat with your bot on Telegram or Discord, send: /start {people.normalize(code)}"))
    for acc in accounts.enabled("telegram"):
        if bot := store.state("telegram", acc.id, "bot_username"):
            print(f"  https://t.me/{bot}?start={people.normalize(code)}")
    return 0


def cmd_pairs(a) -> int:
    from channels import people
    rows = people.pairs(None if a.all else "pending")
    for p in rows:
        print(f"{people.shown(p['code'])}  {p['status']:8}  {p['channel']}/{p['account']}  {p['name'] or '—'} ({p['sender']})  {p['id']}")
    if not rows:
        print(L("（没有）", "(none)"))
    return 0


def cmd_decide(a, approve: bool) -> int:
    refuse_in_agent()
    from channels import accounts, cards
    if approve and a.agent and not accounts.known_agent(a.agent):
        print(L(f"没有 {a.agent} 这个 Agent", f"No Agent called {a.agent}"), file=sys.stderr)
        return 2
    p, st = cards.decide(a.ident, approve, tier=getattr(a, "tier", None), agent=getattr(a, "agent", None))
    if st != "ok":
        print({"missing": L("没有这个配对", "No such pairing request"), "expired": L("这个配对已经过期了", "That request has expired"),
               "done": L("这个配对已经定过了", "That request was already settled")}[st], file=sys.stderr)
        return 1
    print(L("批了：成了联系人，已经告诉对方可以说话了。" if approve else "拒了。", "Approved: they're a contact now and have been told." if approve else "Declined."))
    return 0


def cmd_contacts(a) -> int:
    from channels import accounts, people
    if a.action == "set":
        refuse_in_agent()
        if not a.id:
            print(L("要写联系人的 id", "Give the contact's id"), file=sys.stderr)
            return 2
        if a.agent and not accounts.known_agent(a.agent):
            print(L(f"没有 {a.agent} 这个 Agent", f"No Agent called {a.agent}"), file=sys.stderr)
            return 2
        status = "blocked" if a.block else "active" if a.unblock else None
        p = people.person_by_id(a.id)
        if p is None or p["role"] != "contact":
            print(L("没有这个联系人", "No such contact"), file=sys.stderr)
            return 1
        people.update_person(a.id, tier=a.tier, agent=a.agent, status=status)
        print(L("改好了。", "Updated."))
        return 0
    rows = people.people(role="contact")
    for p in rows:
        print(f"{p['id']}  {p['channel']:8}  {p['tier']:6}  {p['status']:7}  agent {p['agent'] or 'main'}  {p['name'] or '—'} ({p['sender']})")
    if not rows:
        print(L("（没有）", "(none)"))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="mousse channels", description=usage(), formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help=L("渠道和账号", "Channels and accounts"))
    p = sub.add_parser("status", help=L("每个账号现在怎么样", "How each account is doing"))
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("owner-code", help=L("出一个认主人的码", "Make an owner code"))
    p.add_argument("--channel", choices=("telegram", "discord"))
    p.add_argument("--minutes", type=int, default=10)
    p = sub.add_parser("pairs", help=L("待批的配对", "Pending pairing requests"))
    p.add_argument("--all", action="store_true")
    p = sub.add_parser("approve", help=L("批一个配对", "Approve a pairing request"))
    p.add_argument("ident")
    p.add_argument("--tier", choices=("close", "friend", "mate"))
    p.add_argument("--agent")
    p = sub.add_parser("reject", help=L("拒一个配对", "Decline a pairing request"))
    p.add_argument("ident")
    p = sub.add_parser("contacts", help=L("联系人", "Contacts"))
    p.add_argument("action", nargs="?", choices=("set",))
    p.add_argument("id", nargs="?")
    p.add_argument("--tier", choices=("close", "friend", "mate"))
    p.add_argument("--agent")
    p.add_argument("--block", action="store_true")
    p.add_argument("--unblock", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "owner-code" and not 1 <= a.minutes <= 60:
        ap.error(L("--minutes 要在 1–60 之间", "--minutes must be 1–60"))
    return {"list": cmd_list, "status": cmd_status, "owner-code": cmd_owner_code, "pairs": cmd_pairs,
            "approve": lambda x: cmd_decide(x, True), "reject": lambda x: cmd_decide(x, False), "contacts": cmd_contacts}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
