#!/usr/bin/env python3
"""mousse hooks：用户钩子的命令行（第 5 批第 4 项，engine/user_hooks.py）。主人在自己机器上跑，直接读写 <data_dir>/hooks/allowlist.json。

  mousse hooks list [--json]                    有哪些钩子（server.json 的 engine.hooks、工作区的 HOOK.md）、同意了没有、指纹
  mousse hooks approve <指纹前几位> | --all       同意（进白名单）：以后这条钩子在它的事件上会跑；命令或脚本改了要重新同意
  mousse hooks revoke <指纹前几位>               撤回同意
  mousse hooks test <事件> [--tool 工具名] [--payload JSON]  照这个事件跑一次（只跑同意过的），打出每一条的结果
退出码：0 成了；1 没成；2 参数不对。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import shlex
import sys

from i18n import L


def register(sub) -> None:
    p = sub.add_parser("hooks", help=L("用户钩子：列出、同意、撤回、试跑", "User hooks: list, approve, revoke, test"),
                       description=L(__doc__, "mousse hooks: user hooks from the command line. See engine/user_hooks.py."),
                       formatter_class=argparse.RawDescriptionHelpFormatter)
    s = p.add_subparsers(dest="hooks_cmd", required=True)
    a = s.add_parser("list")
    a.add_argument("--json", action="store_true")
    a = s.add_parser("approve")
    a.add_argument("fingerprint", nargs="?")
    a.add_argument("--all", action="store_true")
    a = s.add_parser("revoke")
    a.add_argument("fingerprint")
    a = s.add_parser("test")
    a.add_argument("event")
    a.add_argument("--tool", default="")
    a.add_argument("--agent", default="main")
    a.add_argument("--payload", default="{}")
    p.set_defaults(run=run)


def _pick(prefix: str):
    from engine import user_hooks as U
    hits = [h for h in U.hooks() if h.fingerprint().startswith(prefix or "\x00")]
    return hits


def run(a) -> int:
    from engine import user_hooks as U
    cmd = a.hooks_cmd
    if cmd == "list":
        st = U.status()
        if a.json:
            print(json.dumps(st, ensure_ascii=False, indent=2))
            return 0
        for h in st["hooks"]:
            print(f"{h['fingerprint'][:12]}  {h['event']:<16} {h['state']:<8} {h['origin']}  {shlex.join(h['command'])[:120]}"
                  + (f"  ({h['error']})" if h["error"] else ""))
        b = st["bundled"]
        print(L(f"自带：boot-md {'开' if b['boot-md'] else '关'}，command-logger {'开' if b['command-logger'] else '关'}",
                f"Bundled: boot-md {'on' if b['boot-md'] else 'off'}, command-logger {'on' if b['command-logger'] else 'off'}"))
        return 0
    if cmd == "approve":
        todo = [h for h in U.hooks() if not h.error and not U.approved(h)] if a.all else _pick(a.fingerprint or "")
        if not todo:
            print(L("没有要同意的（指纹对不上，或者都同意过了）", "Nothing to approve (no matching fingerprint, or already approved)"))
            return 1
        if not a.all and len(todo) > 1:
            print(L("指纹前几位对上了不止一条：多写几位", "That prefix matches more than one hook: give more of the fingerprint"))
            return 2
        for h in todo:
            fp = U.approve(h, by="owner (command line)")
            print(L(f"同意了：{fp[:12]} {h.event} {shlex.join(h.argv)[:120]}", f"Approved: {fp[:12]} {h.event} {shlex.join(h.argv)[:120]}"))
        return 0
    if cmd == "revoke":
        hits = [fp for fp in U.allowlist() if fp.startswith(a.fingerprint)]
        if len(hits) != 1:
            print(L("指纹对不上（或者对上了不止一条）", "No single matching fingerprint"))
            return 1
        U.revoke(hits[0])
        print(L(f"撤回了 {hits[0][:12]}", f"Revoked {hits[0][:12]}"))
        return 0
    if cmd == "test":
        event = U.ALIASES.get(a.event, a.event)
        if event not in U.EVENTS:
            print(L(f"认不出这个事件：{a.event}（{', '.join(U.EVENTS)}）", f"Unknown event: {a.event} ({', '.join(U.EVENTS)})"))
            return 2
        try:
            extra = json.loads(a.payload)
        except ValueError:
            print(L("--payload 要是 JSON 对象", "--payload must be a JSON object"))
            return 2
        payload = U.base_payload(event, a.agent, a.agent, **({"tool_name": a.tool} if a.tool else {}), **(extra if isinstance(extra, dict) else {}))
        out = asyncio.run(U.fire(event, payload, agent=a.agent, tool=a.tool or None))
        print(json.dumps({"blocked": out.blocked, "reason": out.reason, "args": out.args, "ran": out.ran,
                          "hooks": [{"fingerprint": h["fingerprint"][:12], "state": h["state"], "last": h["last"][:1]}
                                    for h in U.status()["hooks"] if h["event"] == event]}, ensure_ascii=False, indent=2))
        return 0
    return 2


if __name__ == "__main__":
    ap = argparse.ArgumentParser(prog="hooks_ctl.py")
    register(ap.add_subparsers(dest="cmd", required=True))
    args = ap.parse_args(["hooks", *sys.argv[1:]])
    sys.exit(args.run(args))
