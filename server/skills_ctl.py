#!/usr/bin/env python3
"""mousse skills：技能市场的命令行（第 5 批第 3 项，engine/skill_market.py）。主人在自己机器上跑：不经服务器，直接读写数据目录
（用户级的技能目录 <data_dir>/skills、lock <data_dir>/skills-market/lock.json）；装、更新照样下到隔离区、照样扫描。

  mousse skills search <要搜的话> [--source clawhub,github,skills.sh,well-known] [--json]
  mousse skills install <标识> [--agent <id>] [--yes]       标识：搜出来的 id（clawhub:@作者/slug、github:owner/repo/路径@ref、skills.sh:…、
                                                            well-known:https://网站#名字、url:https://…/SKILL.md）
  mousse skills update [<名字> | --all] [--agent <id>] [--yes] [--force]
  mousse skills list [--json]
  mousse skills remove <名字> [--agent <id>]
扫描结论 safe 直接装；caution 先把发现打出来，要 --yes（或者在终端里答 y）才装；dangerous 一律不装。--agent 装进那个 Agent 的工作区，
不写 = 所有 Agent 共用的技能目录。update 比上游的指纹：一样的跳过，本地改过的跳过（--force 照样更新），不一样的照样扫描。
remove 只动技能市场装的（lock 里有的），拿掉之前留一版。退出码：0 成了；1 没成（说了为什么）；2 参数不对。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

from i18n import L


def register(sub) -> None:
    """mousse.py 登记子命令：mousse skills …"""
    p = sub.add_parser("skills", help=L("技能市场：搜、装、更新、列出、卸载", "Skill market: search, install, update, list, remove"),
                       description=L(__doc__, "mousse skills: the skill market from the command line. See engine/skill_market.py."),
                       formatter_class=argparse.RawDescriptionHelpFormatter)
    s = p.add_subparsers(dest="skills_cmd", required=True)
    a = s.add_parser("search", help=L("几家一起搜", "Search every source"))
    a.add_argument("query", nargs="+")
    a.add_argument("--source", default="", help=L("只搜这几家（逗号分隔）", "Only these sources (comma-separated)"))
    a.add_argument("--json", action="store_true")
    a = s.add_parser("install", help=L("装一个（照样扫描）", "Install one (it's scanned first)"))
    a.add_argument("id")
    a.add_argument("--agent", default="", help=L("装进这个 Agent 的工作区（不写 = 所有 Agent 共用）", "Into this Agent's workspace (default: shared)"))
    a.add_argument("--yes", action="store_true", help=L("扫描结论是 caution 也装", "Install even when the scan says caution"))
    a = s.add_parser("update", help=L("更新（比上游的指纹）", "Update (compares with upstream)"))
    a.add_argument("name", nargs="?")
    a.add_argument("--all", action="store_true")
    a.add_argument("--agent", default="")
    a.add_argument("--yes", action="store_true")
    a.add_argument("--force", action="store_true", help=L("本地改过的也更新（盖掉本地的改动）", "Update even if changed locally (overwrites)"))
    a = s.add_parser("list", help=L("列出技能市场装的", "List what the skill market installed"))
    a.add_argument("--json", action="store_true")
    a = s.add_parser("remove", help=L("卸载（先留一版）", "Remove (keeps a copy first)"))
    a.add_argument("name")
    a.add_argument("--agent", default="")
    p.set_defaults(run=run)


def _confirm(rec: dict, yes: bool) -> bool:
    """caution：打出发现；--yes 或者终端里答 y 才装。"""
    if rec["verdict"] == "safe":
        return True
    for f in rec["findings"]:
        print(f"  {f['severity']:<8} {f['file']}:{f['line']}  {f['description']}")
    if yes:
        return True
    if not sys.stdin.isatty():
        print(L("扫描结论是 caution：确认要装请加 --yes", "The scan says caution: add --yes to install anyway"))
        return False
    return input(L("还是要装吗？[y/N] ", "Install anyway? [y/N] ")).strip().lower() in ("y", "yes")


def _show(rec: dict) -> None:
    from engine import skill_market as m
    ver = (rec.get("commit") or rec.get("version") or "-")[:12]
    print(f"{rec['name']}  {rec['source']}  {rec['id']}  {ver}")
    print(L(f"装到：{m.where(rec['target'])}；扫描：{m.verdict_word(rec['verdict'])}", f"Into: {m.where(rec['target'])}; scan: "
            f"{m.verdict_word(rec['verdict'])}"))
    ch = (rec.get("upstream") or {}).get("clawhub")
    if isinstance(ch, dict):
        print(L(f"ClawHub 的结论：{ch['status']}", f"ClawHub's verdict: {ch['status']}"))


async def _install(rec: dict, yes: bool) -> int:
    from engine import skill_market as m
    _show(rec)
    if rec["verdict"] == "dangerous":
        for f in rec["findings"]:
            print(f"  {f['severity']:<8} {f['file']}:{f['line']}  {f['description']}")
        print(L("扫描结论是 dangerous：不装。", "The scan says dangerous: not installed."))
        m._drop_staged(rec["staged"])
        return 1
    if not _confirm(rec, yes):
        m._drop_staged(rec["staged"])
        return 1
    out = await asyncio.to_thread(m.install_staged, rec["staged"], who="owner (command line)")
    print(L(f"装好了：{out['dest']}", f"Installed: {out['dest']}") + (L(f"（旧的留了一版 {out['kept']}）", f" (old copy kept as {out['kept']})")
                                                                      if out.get("kept") else ""))
    return 0


async def _go(a) -> int:
    from engine import skill_market as m
    cmd = a.skills_cmd
    if cmd == "search":
        found = await m.search(" ".join(a.query), sources=[s for s in a.source.split(",") if s] or None)
        if a.json:
            print(json.dumps(found, ensure_ascii=False, indent=2))
            return 0
        for h in found["hits"]:
            print(f"{h['id']}\t{h['name']}\t{' '.join(h['description'].split())[:100]}")
        for f in found["failed"]:
            print(L(f"（{f['source']} 这次没搜成：{f['error']}）", f"({f['source']} failed this time: {f['error']})"), file=sys.stderr)
        return 0
    if cmd == "list":
        rows = m.installed()
        if a.json:
            print(json.dumps(rows, ensure_ascii=False, indent=2))
            return 0
        for r in rows:
            flag = L("  本地改过", "  changed locally") if r["localChanges"] else L("  不在了", "  missing") if r["missing"] else ""
            print(f"{r['name']}\t{r['target']}\t{r['id']}\t{(r.get('commit') or r.get('version') or '-')[:12]}\t{r['verdict']}{flag}")
        return 0
    if cmd == "install":
        rec = await m.prepare(a.id, target=a.agent or "user")
        return await _install(rec, a.yes)
    if cmd == "update":
        names = [(r["name"], r["target"]) for r in m.installed()] if a.all else [(a.name, a.agent or "user")] if a.name else []
        if not names:
            print(L("写要更新的名字，或者 --all", "Give a name to update, or --all"))
            return 2
        code = 0
        for name, target in names:
            res = await m.prepare_update(name, target=target, force=a.force)
            if res["status"] == "current":
                print(L(f"{name}：已经是最新的", f"{name}: up to date"))
            elif res["status"] == "local-changes":
                print(L(f"{name}：本地改过，跳过（--force 照样更新）", f"{name}: changed locally, skipped (--force to update anyway)"))
            else:
                code = max(code, await _install(res, a.yes))
        return code
    if cmd == "remove":
        out = await asyncio.to_thread(m.remove, a.name, target=a.agent or "user", who="owner (command line)")
        print(L(f"卸载了 {a.name}", f"Removed {a.name}") + (L(f"（留了一版 {out['kept']}）", f" (kept version {out['kept']})") if out["kept"] else ""))
        return 0
    return 2


def run(a) -> int:
    from engine import skill_market as m
    try:
        return asyncio.run(_go(a))
    except m.MarketError as e:
        print(L(f"没成：{e}", f"Failed: {e}"), file=sys.stderr)
        return 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(prog="skills_ctl.py")
    register(ap.add_subparsers(dest="cmd", required=True))
    args = ap.parse_args(["skills", *sys.argv[1:]])
    sys.exit(args.run(args))
