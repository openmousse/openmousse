#!/usr/bin/env python3
"""页面：积木画不了的互动和可视化，你（Agent）写一份网页，app 放进盒子里显示。走服务的 HTTP 接口（/api/pages，见 pages.py）。

  python3 page_ctl.py check   --file page.html [--data 名字='{"from": "表", "sort": ["deadline"], "limit": 300}']…   # 只检查，不存
  python3 page_ctl.py publish --name jobs --title 可投岗位 [--summary "一句话"] --file page.html [--data …]… [--data-file caps.json] [--note "这版改了什么"]
  python3 page_ctl.py list
  python3 page_ctl.py show <id 或 name>                     # 第几版、读哪些数据、用户同意了没有
  python3 page_ctl.py get <id 或 name> [--version N] [--caps]   # 取回这一版的 HTML（改之前先取回来）；--caps 打印它要读的数据
  python3 page_ctl.py data <id 或 name> <数据名> [--rows 5]  # 这份数据现在读出来什么（你自己的表，不经用户授权）
  python3 page_ctl.py rm <id 或 name>

--file - 从标准输入读（经 MCP 调时 HTML 放 input）。同一个 name 再 publish 就是新的一版，地址不变。
不给 --agent：当前目录在哪个 Agent 的工作区里就算它的（主工作区 = main）。怎么写页面、什么时候用页面而不是积木，见 skills/page/SKILL.md。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
from pathlib import Path

from board_ctl import call
from config import settings
from i18n import L, lang

USAGE_EN = """Pages: interactive views and charts the board blocks can't draw. You (an Agent) write one web page; the app shows it in a sandbox.
Uses the server's HTTP API (/api/pages, see pages.py).

  python3 page_ctl.py check   --file page.html [--data NAME='{"from": "table", "sort": ["deadline"], "limit": 300}']…   # check only, save nothing
  python3 page_ctl.py publish --name jobs --title "Open roles" [--summary "one line"] --file page.html [--data …]… [--data-file caps.json] [--note "what changed"]
  python3 page_ctl.py list
  python3 page_ctl.py show <id or name>                     # version, the data it reads, whether the user allowed it
  python3 page_ctl.py get <id or name> [--version N] [--caps]   # this version's HTML (fetch it before editing); --caps prints the data it reads
  python3 page_ctl.py data <id or name> <data name> [--rows 5]  # what that data returns now (your own tables, no user grant needed)
  python3 page_ctl.py rm <id or name>

--file - reads stdin (over MCP, put the HTML in input). Publishing the same name again makes a new version at the same address.
Without --agent: the Agent whose workspace holds the current directory (the main workspace = main). How to write pages and when to use
a page instead of blocks: skills/page/SKILL.md.
"""


def guess_agent() -> str:
    if aid := os.environ.get("MOUSSE_AGENT", "").strip():  # 经 MCP 调的：服务端按令牌或 agent 参数告诉我们是谁（mcp_bridge.py）
        return aid
    here = Path.cwd().resolve()
    for aid, ws in settings.agent_workspaces.items():
        try:
            root = ws.resolve()
        except OSError:
            continue
        if here == root or root in here.parents:
            return aid
    try:
        main = settings.workspace.resolve()
        if here == main or main in here.parents:
            return "main"
    except (OSError, AttributeError):
        pass
    sys.exit(L("不知道是哪个 Agent 的页面：加 --agent <你的 id>（在自己的工作区里跑就不用写；主对话是 main）",
               "Which Agent's page? Add --agent <your id> (not needed inside your own workspace; the main chat is main)"))


def q(s: str) -> str:
    return urllib.parse.quote(s, safe="")


def read_html(src: str) -> str:
    try:
        return sys.stdin.read() if src == "-" else Path(src).expanduser().read_text(encoding="utf8")
    except OSError as e:
        sys.exit(L(f"读不了 {src}：{e}", f"Can't read {src}: {e}"))


def caps_of(a: argparse.Namespace) -> dict:
    binds: dict = {}
    if a.data_file:
        try:
            raw = json.loads(Path(a.data_file).expanduser().read_text(encoding="utf8"))
        except (OSError, ValueError) as e:
            sys.exit(L(f"读不了 {a.data_file}：{e}", f"Can't read {a.data_file}: {e}"))
        binds.update(raw.get("data", raw) if isinstance(raw, dict) else {})
    for spec in a.data or []:
        name, sep, js = spec.partition("=")
        if not sep:
            sys.exit(L(f"--data 写成 名字='{{\"from\": \"表\", …}}'（{spec}）", f"--data is NAME='{{\"from\": \"table\", …}}' ({spec})"))
        try:
            binds[name.strip()] = json.loads(js)
        except ValueError as e:
            sys.exit(L(f"--data {name} 不是合法的 JSON：{e}", f"--data {name} is not valid JSON: {e}"))
    return {"data": binds} if binds else {}


def resolve(ref: str, agent: str) -> str:
    """id 或 name → id。"""
    if ref.startswith("pg-"):
        return ref
    for p in call("GET", f"/api/pages?agent={q(agent)}").get("pages", []):
        if p["name"] == ref:
            return p["id"]
    sys.exit(L(f"没有叫「{ref}」的页面（page_ctl.py list 看有哪些）", f'No page named "{ref}" (see page_ctl.py list)'))


def print_needs(needs: list[dict]) -> None:
    for n in needs:
        miss = L("（表不存在）", " (no such table)") if n.get("missing") else ""
        fields = L("、", ", ").join(n.get("fields") or [])
        print(f"  · {n['name']} ← {n['title']}{miss}" + (f"：{fields}" if fields and lang() == "zh" else f": {fields}" if fields else ""))


def print_check(r: dict) -> None:
    for e in r.get("errors") or []:
        print(L(f"错误：{e}", f"Error: {e}"))
    for w in r.get("warnings") or []:
        print(L(f"提醒：{w}", f"Note: {w}"))
    if r.get("needs"):
        print(L("要读的数据（用户第一次打开时会被问，只读）：", "Data it reads (the user is asked on first open; read-only):"))
        print_needs(r["needs"])


def grant_label(g: str | None) -> str:
    return {"allow": L("用户已允许", "allowed by the user"), "deny": L("用户拒绝了", "denied by the user"),
            "none": L("不读数据", "reads no data")}.get(g or "", L("用户还没打开过", "not opened yet"))


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help", "help"):
        print(__doc__ if lang() == "zh" else USAGE_EN)
        return
    ap = argparse.ArgumentParser(prog="page_ctl.py", add_help=False)
    ap.add_argument("cmd", choices=["check", "publish", "list", "show", "get", "data", "rm"])
    ap.add_argument("ref", nargs="?")
    ap.add_argument("dataname", nargs="?")
    ap.add_argument("--agent")
    ap.add_argument("--file")
    ap.add_argument("--name")
    ap.add_argument("--title")
    ap.add_argument("--summary", default="")
    ap.add_argument("--note", default="")
    ap.add_argument("--data", action="append")
    ap.add_argument("--data-file")
    ap.add_argument("--version", type=int)
    ap.add_argument("--caps", action="store_true")
    ap.add_argument("--rows", type=int, default=5)
    a = ap.parse_args()
    agent = a.agent or guess_agent()

    if a.cmd in ("check", "publish"):
        if not a.file:
            sys.exit(L("要给 --file page.html（- 是标准输入）", "Give --file page.html (- for stdin)"))
        html = read_html(a.file)
        body = {"agent": agent, "name": a.name or "check", "title": a.title or "check", "summary": a.summary, "html": html, "caps": caps_of(a),
                "note": a.note, "check": a.cmd == "check"}
        if a.cmd == "publish" and not (a.name and a.title):
            sys.exit(L("publish 要写 --name（以后更新用同一个）和 --title", "publish needs --name (reuse it to update) and --title"))
        r = call("POST", "/api/pages", body)
        print_check(r)
        if r.get("errors"):
            sys.exit(L("没存：先把上面的错误改掉。", "Not saved: fix the errors above first."))
        if a.cmd == "check":
            print(L("检查通过。", "Check passed."))
            return
        if r.get("unchanged"):
            print(L(f"内容和要读的数据都没变，还是第 {r['version']} 版（{r['id']}）。", f"Nothing changed: still version {r['version']} ({r['id']})."))
            return
        print(L(f"已发布「{a.title}」第 {r['version']} 版（{r['id']}）。用户会在这次回复下面看到页面卡片；页面读数据的话，第一次打开时会问他同不同意。"
                "在回复里用一句话说做了什么、要他看什么。",
                f'Published "{a.title}" v{r["version"]} ({r["id"]}). The user sees a page card under this reply; if the page reads data, they are '
                "asked on first open. Say in one line what you made and what to look at."))
        return

    if a.cmd == "list":
        pages = call("GET", f"/api/pages?agent={q(agent)}").get("pages", [])
        if not pages:
            print(L("还没有页面。", "No pages yet."))
        for p in pages:
            print(f"{p['id']}  {p['name']}  「{p['title']}」  v{p['version']}  {p['updatedAt'][:16].replace('T', ' ')}  {grant_label(p.get('grant'))}")
        return

    if not a.ref:
        sys.exit(L("要给页面的 id 或 name", "Give the page id or name"))
    pid = resolve(a.ref, agent)

    if a.cmd == "show":
        p = call("GET", f"/api/pages/{pid}")
        print(f"{p['id']}  {p['name']}  「{p['title']}」  v{p['version']}  {grant_label(p.get('grant'))}")
        if p.get("summary"):
            print(p["summary"])
        if p.get("needs"):
            print(L("读的数据：", "Reads:"))
            print_needs(p["needs"])
        for v in call("GET", f"/api/pages/{pid}/versions").get("versions", [])[:5]:
            print(f"  v{v['version']}  {v['createdAt'][:16].replace('T', ' ')}  {v['note']}")
    elif a.cmd == "get":
        r = call("GET", f"/api/pages/{pid}/doc?raw=1" + (f"&version={a.version}" if a.version else ""))
        print(json.dumps(r.get("caps") or {}, ensure_ascii=False, indent=2) if a.caps else r["html"])
    elif a.cmd == "data":
        if not a.dataname:
            sys.exit(L("要给数据名（publish 时 --data 的名字）", "Give the data name (the NAME in --data)"))
        r = call("GET", f"/api/pages/{pid}/preview-data?name={q(a.dataname)}")
        print(L(f"「{r['title']}」共 {r['total']} 行，页面拿到 {len(r['rows'])} 行。字段：", f'"{r["title"]}": {r["total"]} rows, the page gets {len(r["rows"])}. Fields:'))
        print("  " + ", ".join(f"{f['key']}（{f.get('label') or f['key']}，{f.get('type')}）" for f in r["fields"]))
        for row in r["rows"][: max(a.rows, 0)]:
            print("  " + json.dumps(row, ensure_ascii=False)[:400])
    elif a.cmd == "rm":
        call("DELETE", f"/api/pages/{pid}?by=agent")
        print(L(f"已删除 {pid}（钉在看板上的那一块会显示「已删除」）。", f"Deleted {pid} (a pinned board block shows it as deleted)."))


if __name__ == "__main__":
    main()
