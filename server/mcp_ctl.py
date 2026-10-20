#!/usr/bin/env python3
"""mousse mcp：你自己配的 MCP 服务（server.json 的 mcp_servers，engine/mcp_client.py）的命令行（第 5 批第 5 项）。主人在自己机器上跑：
改 server.json 之前先备份（engine/setup_api.edit：server.json.bak-<时间>，留最近 20 份）；内核每轮现读配置，改了下一轮就生效，不用重启。

  mousse mcp list [--json]                     有哪些、开没开、缓存着几个工具
  mousse mcp add <名字> --command <命令> [--arg <参数>]… [--env K=V]… [--cwd <目录>]
                         | --url <地址> [--type http|sse] [--header K=V]…
                         [--agent <id>]… [--tool <工具>=auto|ask|off]… [--disabled] [--no-probe] [--save-anyway]
                                               先真连一次、列出工具（--no-probe 不连）；连不上不存，--save-anyway 存成关着的
  mousse mcp remove <名字>
  mousse mcp enable <名字> / disable <名字>
  mousse mcp probe <名字> [--json]             真连一次：列出工具、有没有 resources / prompts
密钥（env、请求头里名字像密钥的）只能写成 ${变量名}，值放进环境、app 里存的密钥（secrets.env）或 env_file；照写的值不收。
退出码：0 成了；1 没成（说了为什么）；2 参数不对。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

from i18n import L


def register(sub) -> None:
    p = sub.add_parser("mcp", help=L("你配的 MCP 服务：列出、加、删、开关、试连", "Your MCP servers: list, add, remove, enable, disable, probe"),
                       description=L(__doc__, "mousse mcp: your own MCP servers (mcp_servers in server.json). See engine/mcp_client.py."),
                       formatter_class=argparse.RawDescriptionHelpFormatter)
    s = p.add_subparsers(dest="mcp_cmd", required=True)
    a = s.add_parser("list")
    a.add_argument("--json", action="store_true")
    a = s.add_parser("add")
    a.add_argument("name")
    a.add_argument("--command")
    a.add_argument("--arg", action="append", default=[])
    a.add_argument("--env", action="append", default=[], help="K=V（密钥写 K=${VAR}）")
    a.add_argument("--cwd")
    a.add_argument("--url")
    a.add_argument("--type", choices=["http", "sse"])
    a.add_argument("--header", action="append", default=[], help="K=V（密钥写 K=${VAR}）")
    a.add_argument("--agent", action="append", default=[])
    a.add_argument("--tool", action="append", default=[], help="工具=auto|ask|off（* = 没单独写的）")
    a.add_argument("--disabled", action="store_true")
    a.add_argument("--no-probe", action="store_true")
    a.add_argument("--save-anyway", action="store_true", help=L("连不上也存（存成关着的）", "Save even if it can't connect (saved disabled)"))
    for name in ("remove", "enable", "disable"):
        a = s.add_parser(name)
        a.add_argument("name")
    a = s.add_parser("probe")
    a.add_argument("name")
    a.add_argument("--json", action="store_true")
    p.set_defaults(run=run)


def _pairs(items: list[str], what: str) -> dict[str, str]:
    out = {}
    for x in items:
        k, sep, v = x.partition("=")
        if not sep or not k.strip():
            raise SystemExit(L(f"{what} 写成 K=V：{x}", f"{what} must be K=V: {x}"))
        out[k.strip()] = v
    return out


def _entry(a) -> dict:
    if bool(a.command) == bool(a.url):
        raise SystemExit(L("写 --command（本机的）或者 --url（远端的），只写一个", "Give either --command (local) or --url (remote)"))
    e: dict = {}
    if a.command:
        e["command"] = a.command
        if a.arg:
            e["args"] = list(a.arg)
        if a.env:
            e["env"] = _pairs(a.env, "--env")
        if a.cwd:
            e["cwd"] = a.cwd
    else:
        e["url"] = a.url
        if a.type:
            e["type"] = a.type
        if a.header:
            e["headers"] = _pairs(a.header, "--header")
    if a.agent:
        e["agents"] = list(dict.fromkeys(a.agent))
    if a.tool:
        e["tools"] = _pairs(a.tool, "--tool")
    if a.disabled:
        e["disabled"] = True
    return e


def _print_probe(name: str, out: dict) -> None:
    if not out["ok"]:
        print(L(f"{name}：连不上（{out['error']}）", f"{name}: couldn't connect ({out['error']})"))
        return
    extra = [x for x, on in (("resources", out["resources"]), ("prompts", out["prompts"])) if on]
    print(L(f"{name}：{len(out['tools'])} 个工具", f"{name}: {len(out['tools'])} tools") + (f"（{', '.join(extra)}）" if extra else "")
          + f"  {out['ms']} ms")
    for t in out["tools"]:
        print(f"  {t['name']:<28} {t['level']:<5} {t['description'][:80]}")


def run(a) -> int:
    from engine import mcp_client as M
    from engine.setup_api import SetupError
    cmd = a.mcp_cmd
    try:
        if cmd == "list":
            rows = M.status()
            if a.json:
                print(json.dumps(rows, ensure_ascii=False, indent=2))
                return 0
            for r in rows:
                where = r.get("command") or r.get("url") or ""
                print(f"{r['name']:<20} {r['type']:<6} {r['status']:<9} {len(r['tools'])} tools  {where}" + (f"  ({r['error']})" if r["error"] else ""))
            return 0
        if cmd == "add":
            entry = _entry(a)
            conf = M.parse(a.name, entry)
            if conf.error:
                print(L(f"没加：{conf.error}", f"Not added: {conf.error}"))
                return 1
            if M.raw_servers().get(a.name) is not None:
                print(L(f"已经有一个叫 {a.name} 的了：先 remove，或者换个名字", f"There's already one called {a.name}: remove it first or pick another name"))
                return 1
            if not a.no_probe and not conf.disabled:
                out = asyncio.run(M.probe(conf))
                _print_probe(a.name, out)
                if not out["ok"]:
                    if not a.save_anyway:
                        print(L("没存（--save-anyway 存成关着的）", "Not saved (--save-anyway saves it disabled)"))
                        return 1
                    entry["disabled"] = True
            res = M.save_entry(a.name, entry)
            print(L(f"加好了：{a.name}（备份 {res['backup']}）", f"Added {a.name} (backup {res['backup']})"))
            return 0
        if cmd == "remove":
            res = M.delete_entry(a.name)
            print(L(f"删了：{a.name}（备份 {res['backup']}）", f"Removed {a.name} (backup {res['backup']})"))
            return 0
        if cmd in ("enable", "disable"):
            res = M.set_enabled(a.name, cmd == "enable")
            print(L(f"{a.name}：{'开了' if cmd == 'enable' else '关了'}", f"{a.name}: {cmd}d") + (f"（备份 {res['backup']}）" if res["backup"] else ""))
            return 0
        if cmd == "probe":
            conf = M.configs().get(a.name)
            if conf is None:
                print(L(f"没有叫 {a.name} 的", f"No server called {a.name}"))
                return 1
            out = asyncio.run(M.probe(conf))
            if a.json:
                print(json.dumps(out, ensure_ascii=False, indent=2))
            else:
                _print_probe(a.name, out)
            return 0 if out["ok"] else 1
    except SetupError as e:
        print(L(f"没成：{e}", f"Failed: {e}"))
        return 1
    return 2


if __name__ == "__main__":
    ap = argparse.ArgumentParser(prog="mcp_ctl.py")
    register(ap.add_subparsers(dest="cmd", required=True))
    args = ap.parse_args(["mcp", *sys.argv[1:]])
    sys.exit(args.run(args))
