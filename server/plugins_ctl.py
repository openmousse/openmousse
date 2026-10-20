#!/usr/bin/env python3
"""mousse plugins：带代码的插件的命令行（第 5 批第 8 项，engine/plugins.py；默认关）。主人在自己机器上跑：直接读写数据目录
（<data_dir>/plugins/、plugins.json）和 server.json（改之前先备份）。改了的要重启服务器才载 / 卸。

  ！！插件的 Python 在服务器进程里跑，没有沙箱：它能做服务器能做的任何事。只装你信得过的。

  mousse plugins list [--json]
  mousse plugins install <文件夹 | git 地址> [--commit <40 位哈希>] [--yes] [--enable] [--replace]
                         git 只认完整的 40 位提交（分支、标签、短哈希不收）。先打出它要的能力和扫描发现（只提醒），终端里答 y
                         （或者 --yes）才装，装了就算你同意了这些能力；--enable 顺手写进 engine.plugins.enabled
  mousse plugins enable <名字> / disable <名字>   engine.plugins.enabled 里加上 / 去掉
  mousse plugins approve <名字> [--yes]          同意它现在的能力（更新以后能力多了要重新同意）
  mousse plugins remove <名字>                   拿掉（先挪进 <data_dir>/plugins/.removed/ 留一份），也从 enabled 里去掉
退出码：0 成了；1 没成（说了为什么）；2 参数不对。
"""
from __future__ import annotations

import argparse
import json
import sys

from i18n import L


def register(sub) -> None:
    """mousse.py 登记子命令：mousse plugins …"""
    p = sub.add_parser("plugins", help=L("带代码的插件：列出、装、开关、同意、删（默认关）", "Code plugins: list, install, enable, disable, approve, "
                                         "remove (off by default)"),
                       description=L(__doc__, "mousse plugins: code plugins (engine/plugins.py). They run inside the server process with no "
                                     "sandbox; only install plugins you trust."),
                       formatter_class=argparse.RawDescriptionHelpFormatter)
    s = p.add_subparsers(dest="plugins_cmd", required=True)
    a = s.add_parser("list")
    a.add_argument("--json", action="store_true")
    a = s.add_parser("install")
    a.add_argument("source")
    a.add_argument("--commit", default="", help=L("git：完整的 40 位提交", "git: the full 40-character commit"))
    a.add_argument("--yes", action="store_true", help=L("不问，直接装（算你同意了它的能力）", "Don't ask (counts as agreeing to its capabilities)"))
    a.add_argument("--enable", action="store_true")
    a.add_argument("--replace", action="store_true", help=L("已经装了同名的：换掉（旧的先留一份）", "Replace an installed one (the old copy is kept)"))
    for name in ("enable", "disable", "remove"):
        a = s.add_parser(name)
        a.add_argument("name")
    a = s.add_parser("approve")
    a.add_argument("name")
    a.add_argument("--yes", action="store_true")
    p.set_defaults(run=run)


def _ask(prompt: str, yes: bool) -> bool:
    if yes:
        return True
    if not sys.stdin.isatty():
        print(L("（不在终端里：加 --yes 才算同意）", "(Not a terminal: add --yes to agree)"))
        return False
    return input(prompt).strip().lower() in ("y", "yes")


def _show(st) -> None:
    from engine import plugins as P
    print(f"{st.manifest.name} {st.manifest.version}  {st.manifest.description}")
    print(L(f"来源：{st.source}", f"Source: {st.source}") + (f"  @{st.commit}" if st.commit else ""))
    print(L("！！", "!! ") + P.IN_PROCESS)
    print(L("它要的能力：", "It asks for:"))
    for c in st.manifest.caps():
        print(f"  - {P.cap_label(c)}")
    found = st.scan["findings"]
    print(L(f"扫描：{st.scan['verdict']}，{len(found)} 条提醒", f"Scan: {st.scan['verdict']}, {len(found)} notes") + ("" if not found else L("（只提醒，不拦）：", " (warnings only):")))
    for f in found:
        print(f"  {f['severity']:<8} {f['file']}:{f['line']}  {f['description']}")


def run(a) -> int:
    from engine import plugins as P
    from engine.setup_api import SetupError
    cmd = a.plugins_cmd
    try:
        if cmd == "list":
            rows = P.status()
            if a.json:
                print(json.dumps(rows, ensure_ascii=False, indent=2))
                return 0
            if not rows:
                print(L("（没有插件）", "(no plugins)"))
            for r in rows:
                caps = len(r.get("capabilities") or [])
                print(f"{r['name']:<20} {'on ' if r['enabled'] else 'off'} {r.get('state', ''):<14} {caps} caps  {r.get('source') or ''}"
                      + (f"  ({r['error']})" if r.get("error") else ""))
            return 0
        if cmd == "install":
            st = P.stage(a.source, a.commit)
            _show(st)
            if not _ask(L("装上它、同意这些能力吗？[y/N] ", "Install it and agree to these capabilities? [y/N] "), a.yes):
                print(L("没装。", "Not installed."))
                return 1
            res = P.install(st, by="owner (command line)", replace=a.replace)
            print(L(f"装好了：{res['path']}", f"Installed at {res['path']}"))
            if a.enable:
                P.set_enabled(res["name"], True)
                print(L("写进了 engine.plugins.enabled：重启服务器后载入。", "Added to engine.plugins.enabled; it loads after a server restart."))
            elif not res["enabled"]:
                print(L(f"还没开：mousse plugins enable {res['name']}，再重启服务器。", f"Not turned on yet: mousse plugins enable {res['name']}, "
                        "then restart the server."))
            return 0
        if cmd in ("enable", "disable"):
            res = P.set_enabled(a.name, cmd == "enable")
            print(L(f"{a.name}：{'开了' if cmd == 'enable' else '关了'}（重启服务器后生效）", f"{a.name}: {cmd}d (takes effect after a server restart)")
                  + (f"  backup {res['backup']}" if res.get("backup") else ""))
            return 0
        if cmd == "approve":
            m = P.read_manifest(P.home() / a.name)
            print(L("！！", "!! ") + P.IN_PROCESS)
            for c in m.caps():
                print(f"  - {P.cap_label(c)}")
            if not _ask(L("同意这些能力吗？[y/N] ", "Agree to these capabilities? [y/N] "), a.yes):
                return 1
            P.approve(a.name, by="owner (command line)")
            print(L("同意了。", "Approved."))
            return 0
        if cmd == "remove":
            res = P.remove(a.name)
            print(L(f"拿掉了：{a.name}（留了一份：{res['kept']}）", f"Removed {a.name} (a copy is kept at {res['kept']})"))
            return 0
    except (P.PluginError, SetupError) as e:
        print(L(f"没成：{e}", f"Failed: {e}"))
        return 1
    return 2


if __name__ == "__main__":
    ap = argparse.ArgumentParser(prog="plugins_ctl.py")
    register(ap.add_subparsers(dest="cmd", required=True))
    args = ap.parse_args(["plugins", *sys.argv[1:]])
    sys.exit(args.run(args))
