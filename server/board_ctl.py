#!/usr/bin/env python3
"""积木看板：你（Agent）自己的数据表和看板，从命令行建表、记数据、搭看板。走服务的 HTTP 接口（/api/boards、/api/collections、/api/rows）。

  python3 board_ctl.py show [--json]                      # 现在的看板（每块显示什么）+ 你的表
  python3 board_ctl.py get                                # 现在的看板配置（JSON），改完用 apply / propose 交回去
  python3 board_ctl.py table list
  python3 board_ctl.py table add pantry --title 家里的食物 --field name:text:名字 --field qty:number:数量:份 \\
      --field expires:date:到期 --field price:money:价格:GBP --field category:choice:类别:肉蛋,奶,蔬果,主食,其他 [--draft]
  python3 board_ctl.py table alter pantry [--title …] [--add-field key:type:标签] [--fields-file 文件]
  python3 board_ctl.py table archive pantry
  python3 board_ctl.py rows add pantry --json '{"name": "鸡胸肉", "qty": 2}'      # 或 --file 行.json（一个对象或一个列表），- 是标准输入
  python3 board_ctl.py rows update r-1a2b3c --json '{"expires": "2026-09-30"}' [--inc qty=-1]
  python3 board_ctl.py rows delete r-1a2b3c | rows restore r-1a2b3c
  python3 board_ctl.py rows query pantry [--where 'qty>0'] [--where 'expires<=+3d'] [--sort expires] [--limit 20] [--json]
  python3 board_ctl.py rows list pantry [--deleted]
  python3 board_ctl.py check --file board.json            # 只校验，打印每块会显示什么，不存
  python3 board_ctl.py apply --file board.json --note "按你说的加了 4 块"          # 用户明确让你加的：直接换上
  python3 board_ctl.py propose --file board.json --title "在饮食看板加一块「快过期」" --why "…" --change "…" [--dedupe 键]
  python3 board_ctl.py history | revert <版本>
  python3 board_ctl.py check --plan 方案.json             # 建 Agent 之前：方案（表 + 积木，表可以带 rows 示例行）画出来什么样
  python3 board_ctl.py pack list | pack show pantry | pack guide pantry      # 功能包：有哪些、装了会怎样、怎么用
  python3 board_ctl.py pack install pantry [--check] [--propose --why "…"]   # 用户让装的直接装；你自己想到的 --propose
  python3 board_ctl.py pack remove pantry                  # 看板上拿掉这个包的积木（表和数据留着）
  python3 board_ctl.py alert list | alert check --file 提醒.json | alert propose --file 提醒.json --why "…"
  python3 board_ctl.py alert pause|resume|delete <提醒 id>  # 提醒是新推送：只能 propose，用户点了同意才开

--where：字段 运算 值，运算是 = != > >= < <= ~（包含）；日期可以写 today / tomorrow / week / month / +3d / -30d / 2026-09-30。
不给 --agent：当前目录在哪个 Agent 的工作区里就算它的。积木写法、什么时候直接加、什么时候提案，见 skills/board/SKILL.md。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from config import settings
from i18n import L, lang

WHERE = re.compile(r"^\s*([a-z_][a-z0-9_]*)\s*(>=|<=|!=|=|>|<|~)\s*(.*?)\s*$")


def description() -> str:
    return L(__doc__, """Board blocks: your (the Agent's) own data tables and dashboard. Create tables, record data and build the board from the command line.
Goes through the server's HTTP API (/api/boards, /api/collections, /api/rows).

  python3 board_ctl.py show [--json]                      # the current board (what each block shows) + your tables
  python3 board_ctl.py get                                # the current board config (JSON); edit it and hand it back with apply / propose
  python3 board_ctl.py table list
  python3 board_ctl.py table add pantry --title Pantry --field name:text:Name --field qty:number:Qty:pcs \\
      --field expires:date:Expires --field price:money:Price:GBP --field category:choice:Category:Meat,Dairy,Veg,Staples,Other [--draft]
  python3 board_ctl.py table alter pantry [--title …] [--add-field key:type:label] [--fields-file FILE]
  python3 board_ctl.py table archive pantry
  python3 board_ctl.py rows add pantry --json '{"name": "Chicken breast", "qty": 2}'   # or --file rows.json (an object or a list), - for stdin
  python3 board_ctl.py rows update r-1a2b3c --json '{"expires": "2026-09-30"}' [--inc qty=-1]
  python3 board_ctl.py rows delete r-1a2b3c | rows restore r-1a2b3c
  python3 board_ctl.py rows query pantry [--where 'qty>0'] [--where 'expires<=+3d'] [--sort expires] [--limit 20] [--json]
  python3 board_ctl.py rows list pantry [--deleted]
  python3 board_ctl.py check --file board.json            # validate only, print what each block would show, save nothing
  python3 board_ctl.py apply --file board.json --note "Added 4 blocks as you asked"   # the user explicitly asked: switch to it now
  python3 board_ctl.py propose --file board.json --title "Add an 'Expiring soon' block to the diet board" --why "…" --change "…" [--dedupe KEY]
  python3 board_ctl.py history | revert <version>
  python3 board_ctl.py check --plan plan.json            # before an Agent exists: how its plan (tables + blocks, tables may carry sample rows) looks
  python3 board_ctl.py pack list | pack show pantry | pack guide pantry      # feature packs: what exists, what installing does, how to use it
  python3 board_ctl.py pack install pantry [--check] [--propose --why "…"]   # the user asked: install; your own idea: --propose
  python3 board_ctl.py pack remove pantry                  # take the pack's blocks off the board (tables and data stay)
  python3 board_ctl.py alert list | alert check --file alert.json | alert propose --file alert.json --why "…"
  python3 board_ctl.py alert pause|resume|delete <alert id>  # reminders are new notifications: propose only, on after the user says yes

--where: field op value, op is = != > >= < <= ~ (contains); dates can be today / tomorrow / week / month / +3d / -30d / 2026-09-30.
Without --agent: the Agent whose workspace contains the current directory. How to write blocks, and when to apply vs propose: skills/board/SKILL.md.
""")


def call(method: str, path: str, body: dict | None = None) -> dict:
    url = f"http://{settings.host}:{settings.port}{path}"
    raw = json.dumps(body, ensure_ascii=False).encode("utf8") if body is not None else None
    headers = {"Content-Type": "application/json", "Accept": "application/json", "Accept-Language": "zh-CN" if lang() == "zh" else "en"}
    tokens = settings.tokens()
    if tokens:
        headers["Authorization"] = f"Bearer {next(iter(tokens.values()))}"
    req = urllib.request.Request(url, data=raw, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310
            return json.loads(r.read().decode("utf8"))
    except urllib.error.HTTPError as e:
        try:
            j = json.loads(e.read().decode("utf8"))
        except ValueError:
            j = {}
        if e.code == 409 and j.get("error") == "rejected_before":
            when = str(j.get("rejectedAt") or "")[:16].replace("T", " ")
            note = str(j.get("note") or "").strip()
            print(L(f"没提交：同一个提案 {when} 被拒绝过（{('理由：' + note) if note else '没说理由'}）。30 天内别再提。",
                    f"Not submitted: the same proposal was rejected on {when} ({('reason: ' + note) if note else 'no reason given'}). "
                    "Don't propose it again within 30 days."))
            sys.exit(3)
        msg = j.get("detail") or j.get("error") or str(e)
        sys.exit(L(f"失败（HTTP {e.code}）：{msg}", f"Failed (HTTP {e.code}): {msg}"))
    except urllib.error.URLError as e:
        sys.exit(L(f"连不上服务 {url}：{e.reason}", f"Can't reach the server at {url}: {e.reason}"))


def guess_agent() -> str:
    here = Path.cwd().resolve()
    for aid, ws in settings.agent_workspaces.items():
        try:
            root = ws.resolve()
        except OSError:
            continue
        if here == root or root in here.parents:
            return aid
    sys.exit(L("不知道是哪个 Agent 的看板：加 --agent <你的 id>（在自己的工作区里跑就不用写）",
               "Which Agent's board? Add --agent <your id> (not needed when run inside your own workspace)"))


def q(s: str) -> str:
    return urllib.parse.quote(s, safe="")


def read_json(src: str | None, inline: str | None) -> object:
    if inline is not None:
        text = inline
    elif src:
        try:
            text = sys.stdin.read() if src == "-" else Path(src).expanduser().read_text(encoding="utf8")
        except OSError as e:
            sys.exit(L(f"读不了 {src}：{e}", f"Can't read {src}: {e}"))
    else:
        sys.exit(L("要给 --json 或 --file", "Give --json or --file"))
    try:
        return json.loads(text)
    except ValueError as e:
        sys.exit(L(f"不是合法的 JSON：{e}", f"Not valid JSON: {e}"))


def field_spec(s: str) -> dict:
    """key:type[:标签[:单位 / 货币 / 选项（逗号分隔）]]"""
    parts = s.split(":", 3)
    if len(parts) < 2:
        sys.exit(L(f"字段「{s}」写成 key:type[:标签[:单位或选项]]", f'Field "{s}": write key:type[:label[:unit or options]]'))
    f: dict = {"key": parts[0].strip(), "type": parts[1].strip()}
    if len(parts) > 2 and parts[2].strip():
        f["label"] = parts[2].strip()
    if len(parts) > 3 and parts[3].strip():
        extra = parts[3].strip()
        if f["type"] == "choice":
            f["options"] = [x.strip() for x in re.split(r"[,，]", extra) if x.strip()]
        elif f["type"] == "money":
            f["currency"] = extra
        else:
            f["unit"] = extra
    return f


def where_of(items: list[str] | None) -> list[list]:
    out = []
    for w in items or []:
        m = WHERE.match(w)
        if not m:
            sys.exit(L(f"--where「{w}」看不懂：写成 字段 运算 值，比如 qty>0、expires<=+3d", f'Can\'t read --where "{w}": field op value, e.g. qty>0, expires<=+3d'))
        k, op, v = m.groups()
        out.append([k, "contains" if op == "~" else op, v])
    return out


def board_file(path: str) -> tuple[list, list | None]:
    """看板文件：{"blocks": [...], "sections": [...]}（sections 可以不写 = 内置小节照现在的）。"""
    raw = read_json(path, None)
    blocks = raw.get("blocks") if isinstance(raw, dict) else raw
    if not isinstance(blocks, list):
        sys.exit(L('看板文件写成 {"blocks": [ … ]}', 'The board file is {"blocks": [ … ]}'))
    sections = raw.get("sections") if isinstance(raw, dict) else None
    return blocks, sections if isinstance(sections, list) else None


def summary(b: dict) -> str:
    """一块积木现在显示什么，一两行字（给 show / check 看）。"""
    d = b.get("data") or {}
    if d.get("error"):
        return L(f"出错：{d['error']}", f"error: {d['error']}")
    t = b["type"]
    if t == "stat":
        return " · ".join(f"{i['label']} {i['text']}{(' (' + i['deltaText'] + ')') if i.get('deltaText') else ''}" for i in d.get("items", []))
    if t == "progress":
        return f"{d.get('text')} / {d.get('targetText')}"
    if t == "chart":
        pts = d.get("points", [])
        return " ".join(f"{p['key'][5:]}:{p['text'] or '0'}" for p in pts[-8:])
    if t in ("list", "checklist"):
        rows = d.get("rows", [])
        head = L(f"{d.get('total', 0)} 行", f"{d.get('total', 0)} rows")
        lines = []
        for r in rows[:6]:
            mark = ("[x] " if r.get("checked") else "[ ] ") if t == "checklist" else ""
            extra = " · ".join(x for x in (r.get("sub"), r.get("right"), (r.get("badge") or {}).get("text")) if x)
            lines.append(f"    {mark}{r['title']}{('  ' + extra) if extra else ''}")
        return head + ("\n" + "\n".join(lines) if lines else "")
    if t == "text":
        return (d.get("text") or b.get("text") or "")[:80]
    return " / ".join(f"[{a['label']}]" for a in b.get("actions", []))


def show_board(r: dict, preview: bool = False) -> None:
    if not preview:
        print(L(f"看板第 {r['version']} 版（{r.get('note') or '没写说明'}）", f"Board version {r['version']} ({r.get('note') or 'no note'})") if r["version"]
              else L("还没有看板配置", "No board config yet"))
    if r.get("anchors"):
        print(L("能插的位置（after）：", "Anchors for after: ") + ", ".join(r["anchors"]))
    if r.get("sections"):
        print(L("内置小节（用户能挪、能藏，按这个顺序）：", "Built-in sections (the user can move and hide them), in order: ")
              + ", ".join(f"{s['id']}" + (L("（藏着）", " (hidden)") if s.get("hidden") else "") for s in r["sections"]))
    for b in r["blocks"]:
        where = f" after={b['after']}" if b.get("after") else ""
        hid = L(" (藏着)", " (hidden)") if b.get("hidden") else ""
        print(f"- {b['id']} [{b['type']}] {b.get('title', '')}{where}{hid}: {summary(b)}")
    if r.get("collections"):
        print(L("表：", "Tables:"))
        for c in r["collections"]:
            st = "" if c["status"] == "active" else f" ({c['status']})"
            print(f"- {c['name']}{st} {c['title']} · {c.get('count', 0)} " + L("行", "rows") + " · " + ", ".join(f"{f['key']}:{f['type']}" for f in c["fields"]))
    packs = sorted({b["pack"] for b in r["blocks"] if b.get("pack")})
    if packs:
        print(L(f"装着的功能包：{', '.join(packs)}（用法：pack guide <包名>）", f"Installed packs: {', '.join(packs)} (how to use: pack guide <name>)"))


def pack_cmd(a: argparse.Namespace) -> None:
    if a.action == "list":
        for p in call("GET", "/api/packs")["packs"]:
            on = L(f"装在：{', '.join(p['installedOn'])}", f"installed on: {', '.join(p['installedOn'])}") if p.get("installedOn") else L("还没装", "not installed")
            print(f"{p['name']}\t{p['title']}\t{on}\t{p.get('summary') or ''}")
        return
    if not a.name:
        sys.exit(L("要写包名（pack list 能看到）", "Give the pack name (see pack list)"))
    p = call("GET", f"/api/packs/{q(a.name)}")
    if a.action == "guide":
        print(p.get("guide") or L("这个包没写用法", "This pack has no guide"))
        return
    print(f"{p['name']} · {p['title']}\n{p.get('summary') or ''}")
    print(L("表：", "Tables: ") + ", ".join(f"{t['name']}（{t['title']}）" for t in p["tables"]))
    print(L("积木：", "Blocks: ") + ", ".join(f"{b['id']} [{b['type']}] {b['title']}" for b in p["blocks"]))
    if p.get("alerts"):
        print(L("提醒（装了以后另外出卡，用户同意才开）：", "Reminders (separate cards after installing; on only if the user says yes): ")
              + ", ".join(x["title"] for x in p["alerts"]))
    if p.get("installedOn"):
        print(L("装在：", "Installed on: ") + ", ".join(p["installedOn"]))


def pack_install(a: argparse.Namespace, agent: str) -> None:
    if not a.name:
        sys.exit(L("要写包名（pack list 能看到）", "Give the pack name (see pack list)"))
    if a.action == "remove":
        r = call("POST", f"/api/packs/{q(a.name)}/remove", {"agent": agent})
        print(L(f"拿掉了 {r['removed']} 块（表和数据都在；history / revert 能回去）", f"Removed {r['removed']} block(s) (tables and data kept; history / revert brings them back)"))
        return
    if a.action != "install":
        sys.exit(L("pack 的动作是 list / show / guide / install / remove", "pack actions: list / show / guide / install / remove"))
    mode = "check" if a.check else ("propose" if a.propose else "apply")
    r = call("POST", f"/api/packs/{q(a.name)}/install", {"agent": agent, "mode": mode, "why": a.why, "title": a.title})
    lines = r.get("changes") or []
    if r.get("clashes"):
        lines.append(L("这些字段你原来的类型和包里不一样，留着你原来的：", "These fields already exist with a different type; yours are kept: ")
                     + ", ".join(f"{t}.{k}" for t, ks in r["clashes"].items() for k in ks))
    if mode == "check":
        head = L("已经装过了；再装会：", "Already installed; installing again would:") if r.get("installed") else L("装了会：", "Installing would:")
        print(head + ("\n- " + "\n- ".join(lines) if lines else L("（什么都不用改）", " (nothing to change)")))
    elif mode == "propose":
        print(r["inboxId"])
    else:
        print(L("装好了：", "Installed: ") + ("\n- " + "\n- ".join(lines) if lines else L("（本来就有，什么都没改）", "(already there, nothing changed)")))
        if r.get("alertCards"):
            print(L(f"包里的 {len(r['alertCards'])} 条提醒各出了一张卡，用户同意才开：{', '.join(r['alertCards'])}",
                    f"The pack's {len(r['alertCards'])} reminder(s) each got a card; they're on only if the user says yes: {', '.join(r['alertCards'])}"))
        print(L("用法：board_ctl.py pack guide " + a.name, "How to use it: board_ctl.py pack guide " + a.name))


def alert_cmd(a: argparse.Namespace, agent: str) -> None:
    if a.action == "list":
        rows = call("GET", f"/api/alerts/{q(agent)}")["alerts"]
        if not rows:
            print(L("没有开着的提醒", "No reminders"))
        for x in rows:
            st = L("暂停中", "paused") if x["status"] == "paused" else L("开着", "on")
            print(f"{x['id']}\t{st}\t{x['title']}\t{x['when']}\t{x['levelText']}\t{x.get('preview') or L('（现在查出来是空的）', '(nothing matches now)')}")
        return
    if a.action in ("pause", "resume", "delete"):
        if not a.id:
            sys.exit(L("要写提醒 id（alert list 能看到）", "Give the reminder id (see alert list)"))
        call("POST", f"/api/alerts/item/{q(a.id)}", {"status": {"pause": "paused", "resume": "live", "delete": "deleted"}[a.action]})
        print(L("好了", "Done"))
        return
    if not a.file:
        sys.exit(L("要给 --file（一条提醒的 JSON）", "Give --file (one reminder as JSON)"))
    spec = read_json(a.file, None)
    if a.action == "check":
        r = call("POST", f"/api/alerts/{q(agent)}/check", {"spec": spec})
        print(L(f"没问题：{r['when']}，{r['levelText']}", f"Looks fine: {r['when']}, {r['levelText']}"))
        print(L(f"按现在的数据会推：{r['preview']}", f"With today's data it would say: {r['preview']}") if r.get("preview")
              else L("按现在的数据查出来是空的，到点不会推", "Nothing matches today, so it wouldn't send anything"))
        return
    r = call("POST", f"/api/alerts/{q(agent)}/propose", {"spec": spec, "why": a.why, "title": a.title})
    if r.get("updated"):
        print(L("（原地更新了还在等的同一个提醒）", "(updated the pending reminder with the same id)"), file=sys.stderr)
    print(r["inboxId"])


def main() -> None:
    ap = argparse.ArgumentParser(description=description(), formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--agent", help=L("哪个 Agent 的看板，默认按当前目录认", "which Agent's board; defaults to the current workspace"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    sh = sub.add_parser("show", help=L("现在的看板和表", "the current board and tables"))
    sh.add_argument("--json", action="store_true")
    sub.add_parser("get", help=L("现在的看板配置（JSON）", "the current board config (JSON)"))
    tb = sub.add_parser("table", help=L("表：list / add / alter / archive", "tables: list / add / alter / archive"))
    tb.add_argument("action", choices=("list", "add", "alter", "archive"))
    tb.add_argument("name", nargs="?")
    tb.add_argument("--title")
    tb.add_argument("--field", action="append", help="key:type[:label[:unit|currency|options]]")
    tb.add_argument("--add-field", action="append", help="key:type[:label[:…]]")
    tb.add_argument("--fields-file", help=L("整份字段（JSON 列表）", "all fields (a JSON list)"))
    tb.add_argument("--draft", action="store_true", help=L("草稿表：给还没点头的提案用", "draft table, for a proposal that isn't approved yet"))
    rw = sub.add_parser("rows", help=L("数据：add / update / delete / restore / query / list", "rows: add / update / delete / restore / query / list"))
    rw.add_argument("action", choices=("add", "update", "delete", "restore", "query", "list"))
    rw.add_argument("target", help=L("表名（add / query / list）或行 id（update / delete / restore）", "table name (add / query / list) or row id (update / delete / restore)"))
    rw.add_argument("--json", dest="inline", help=L("一行（或一个列表）的 JSON", "one row (or a list) as JSON"))
    rw.add_argument("--file", help=L("从文件读 JSON，- 是标准输入", "read JSON from a file, - for stdin"))
    rw.add_argument("--inc", action="append", help=L("数字字段加减，比如 qty=-1", "add to a number field, e.g. qty=-1"))
    rw.add_argument("--where", action="append")
    rw.add_argument("--sort", action="append")
    rw.add_argument("--limit", type=int, default=50)
    rw.add_argument("--deleted", action="store_true")
    rw.add_argument("--out-json", action="store_true", help=L("query / list 输出 JSON", "print JSON for query / list"))
    for name, text in (("check", L("只校验看板文件，打印每块会显示什么", "validate a board file and print what each block would show")),
                       ("apply", L("直接换上（用户明确让你加的）", "switch to it now (the user explicitly asked)")),
                       ("propose", L("交收件箱等用户点头（你自己想到的）", "submit to the inbox for the user's OK (your own idea)"))):
        p = sub.add_parser(name, help=text)
        p.add_argument("--file", required=name != "check", help=L('看板文件：{"blocks": [ … ]}', 'board file: {"blocks": [ … ]}'))
        if name == "check":
            p.add_argument("--plan", help=L('建 Agent 的方案：{"tables": [...], "blocks": [...]}（Agent 还不存在也能看）',
                                            'a new Agent\'s plan: {"tables": [...], "blocks": [...]} (works before the Agent exists)'))
        if name == "apply":
            p.add_argument("--note", default="", help=L("这一版改了什么，一句话（看板顶上的撤回条里显示）", "what changed, one line (shown in the undo strip)"))
        if name == "propose":
            p.add_argument("--title", required=True, help=L("收件箱卡的标题：动作本身，大白话", "the inbox card title: the action, in plain words"))
            p.add_argument("--why", default="", help=L("为什么：证据（哪天说过、几次）", "why: the evidence (dates, counts)"))
            p.add_argument("--change", action="append", default=[], help=L("会改变的一件事，可以写多次", "one thing that will change; repeat for more"))
            p.add_argument("--note", default="", help=L("这一版改了什么，一句话", "what changed, one line"))
            p.add_argument("--dedupe", default="", help=L("同一个提案的固定键（改好重交会原地更新同一张卡）", "a stable key (resubmitting updates the same card)"))
    pk = sub.add_parser("pack", help=L("功能包：list / show / guide / install / remove", "feature packs: list / show / guide / install / remove"))
    pk.add_argument("action", choices=("list", "show", "guide", "install", "remove"))
    pk.add_argument("name", nargs="?")
    pk.add_argument("--check", action="store_true", help=L("只看会做什么，不装", "only show what it would do"))
    pk.add_argument("--propose", action="store_true", help=L("交收件箱等用户点头（你自己想到的）", "submit to the inbox for the user's OK (your own idea)"))
    pk.add_argument("--why", default="", help=L("为什么：证据", "why: the evidence"))
    pk.add_argument("--title", default="", help=L("收件箱卡的标题（不给就用默认的）", "the inbox card title (optional)"))
    al = sub.add_parser("alert", help=L("提醒：list / check / propose / pause / resume / delete", "reminders: list / check / propose / pause / resume / delete"))
    al.add_argument("action", choices=("list", "check", "propose", "pause", "resume", "delete"))
    al.add_argument("id", nargs="?", help=L("pause / resume / delete 的提醒 id（alert list 能看到）", "the reminder id for pause / resume / delete (see alert list)"))
    al.add_argument("--file", help=L("提醒文件（一条规则的 JSON）", "reminder file (one rule as JSON)"))
    al.add_argument("--why", default="", help=L("为什么：证据", "why: the evidence"))
    al.add_argument("--title", default="", help=L("收件箱卡的标题（不给就用默认的）", "the inbox card title (optional)"))
    sub.add_parser("history", help=L("看板的每一版", "every version of the board"))
    rv = sub.add_parser("revert", help=L("回到某一版（0 = 空看板）", "go back to a version (0 = empty board)"))
    rv.add_argument("version", type=int)
    a = ap.parse_args()
    if a.cmd == "check" and a.plan:  # Agent 还不存在：不用认是谁
        r = call("POST", "/api/boards/plan", {"plan": read_json(a.plan, None)})
        print(L("方案没问题，建好以后看板会是这样" + ("（按示例行画的）：" if r.get("sample") else "（还没有数据，每块显示空的样子）："),
                "The plan is fine. The board would look like this" + (" (drawn from the sample rows):" if r.get("sample") else " (no data yet, so each block is empty):")))
        show_board(r, preview=True)
        return
    if a.cmd == "check" and not a.file:
        sys.exit(L("check 要给 --file（看板文件）或 --plan（建 Agent 的方案）", "check needs --file (a board file) or --plan (a new Agent's plan)"))
    if a.cmd == "pack" and a.action in ("list", "show", "guide"):
        pack_cmd(a)
        return
    agent = a.agent or guess_agent()

    if a.cmd == "show":
        r = call("GET", f"/api/boards/{q(agent)}")
        if a.json:
            print(json.dumps(r, ensure_ascii=False, indent=1))
        else:
            show_board(r)
    elif a.cmd == "get":
        r = call("GET", f"/api/boards/{q(agent)}")
        out: dict = {"blocks": [{k: v for k, v in b.items() if k != "data"} for b in r["blocks"]]}
        if r.get("sections"):  # 内置小节的顺序和藏没藏（用户在 app 里挪的）：原样交回去就不变
            out["sections"] = [{"id": s["id"], **({"hidden": True} if s.get("hidden") else {})} for s in r["sections"]]
        print(json.dumps(out, ensure_ascii=False, indent=1))
    elif a.cmd == "table":
        if a.action == "list":
            for c in call("GET", f"/api/collections/{q(agent)}")["collections"]:
                st = "" if c["status"] == "active" else f" ({c['status']})"
                print(f"{c['name']}{st}\t{c['title']}\t{c.get('count', 0)}\t" + ", ".join(f"{f['key']}:{f['type']}" for f in c["fields"]))
            return
        if not a.name:
            sys.exit(L("要写表名", "Give the table name"))
        if a.action == "archive":
            call("DELETE", f"/api/collections/{q(agent)}/{q(a.name)}")
            print(L(f"归档了「{a.name}」（行都留着）", f'Archived "{a.name}" (rows are kept)'))
            return
        fields = read_json(a.fields_file, None) if a.fields_file else [field_spec(s) for s in (a.field or [])]
        if a.action == "add":
            if not fields:
                sys.exit(L("建表要给 --field（可以多次）或 --fields-file", "Give --field (repeatable) or --fields-file"))
            r = call("POST", f"/api/collections/{q(agent)}", {"name": a.name, "title": a.title or a.name, "fields": fields, "draft": a.draft})
            print(L(f"建好了「{a.name}」（{r['status']}）", f'Created "{a.name}" ({r["status"]})'))
        else:
            body: dict = {}
            if a.title:
                body["title"] = a.title
            if a.fields_file:
                body["fields"] = fields
            elif a.add_field:
                cur = next((c for c in call("GET", f"/api/collections/{q(agent)}")["collections"] if c["name"] == a.name), None)
                if not cur:
                    sys.exit(L(f"没有「{a.name}」这张表", f'No table called "{a.name}"'))
                body["fields"] = cur["fields"] + [field_spec(s) for s in a.add_field]
            if not body:
                sys.exit(L("没有要改的：--title / --add-field / --fields-file", "Nothing to change: --title / --add-field / --fields-file"))
            call("PATCH", f"/api/collections/{q(agent)}/{q(a.name)}", body)
            print(L("改好了", "Updated"))
    elif a.cmd == "rows":
        if a.action == "add":
            rows = read_json(a.file, a.inline)
            rows = rows if isinstance(rows, list) else [rows]
            r = call("POST", f"/api/collections/{q(agent)}/{q(a.target)}/rows", {"rows": rows, "by": "agent"})
            print("\n".join(r["ids"]))
        elif a.action == "update":
            body = {"data": read_json(a.file, a.inline) if (a.file or a.inline) else {}, "inc": {}, "by": "agent"}
            for s in a.inc or []:
                k, _, v = s.partition("=")
                try:
                    body["inc"][k.strip()] = float(v)
                except ValueError:
                    sys.exit(L(f"--inc「{s}」写成 字段=数字", f'--inc "{s}": write field=number'))
            r = call("PATCH", f"/api/rows/{q(a.target)}", body)
            print(json.dumps(r["row"]["data"], ensure_ascii=False))
        elif a.action == "delete":
            call("DELETE", f"/api/rows/{q(a.target)}?by=agent")
            print(L("删了（30 天内能用 rows restore 找回）", "Deleted (rows restore brings it back within 30 days)"))
        elif a.action == "restore":
            call("POST", f"/api/rows/{q(a.target)}/restore", {})
            print(L("找回来了", "Restored"))
        elif a.action == "query":
            query: dict = {"from": a.target, "where": where_of(a.where), "limit": a.limit}
            if a.sort:
                query["sort"] = a.sort
            r = call("POST", f"/api/boards/{q(agent)}/query", {"query": query, "kind": "rows"})
            if a.out_json:
                print(json.dumps(r, ensure_ascii=False, indent=1))
            else:
                print(L(f"共 {r['total']} 行", f"{r['total']} rows"))
                for row in r["rows"]:
                    print(row["id"] + "\t" + json.dumps({k: v for k, v in row.items() if k not in ("id", "_created", "_updated")}, ensure_ascii=False))
        else:
            r = call("GET", f"/api/collections/{q(agent)}/{q(a.target)}/rows?limit={a.limit}{'&deleted=1' if a.deleted else ''}")
            if a.out_json:
                print(json.dumps(r, ensure_ascii=False, indent=1))
            else:
                print(L(f"共 {r['total']} 行", f"{r['total']} rows"))
                for row in r["rows"]:
                    print(row["id"] + "\t" + json.dumps(row["data"], ensure_ascii=False))
    elif a.cmd == "pack":
        pack_install(a, agent)
    elif a.cmd == "alert":
        alert_cmd(a, agent)
    elif a.cmd in ("check", "apply", "propose"):
        blocks, sections = board_file(a.file)
        extra = {"sections": sections} if sections is not None else {}
        if a.cmd == "check":
            r = call("PUT", f"/api/boards/{q(agent)}", {"blocks": blocks, "dryRun": True, **extra})
            print(L("没问题，会显示成这样：", "Looks fine. It would show:"))
            show_board(r, preview=True)
        elif a.cmd == "apply":
            r = call("PUT", f"/api/boards/{q(agent)}", {"blocks": blocks, "mode": "apply", "note": a.note, **extra})
            print(L(f"换上了，第 {r['version']} 版。app 看板顶上有「撤回」；对话里告诉用户加了什么。",
                    f"Done, version {r['version']}. The app shows an undo strip on the board; tell the user what you added."))
        else:
            r = call("PUT", f"/api/boards/{q(agent)}", {"blocks": blocks, "mode": "propose", "note": a.note, "title": a.title, "why": a.why,
                                                       "changes": a.change, "dedupe": a.dedupe, **extra})
            if r.get("updated"):
                print(L("（原地更新了还在等的同一个提案）", "(updated the pending proposal with the same dedupe key)"), file=sys.stderr)
            print(r["inboxId"])
    elif a.cmd == "history":
        for v in call("GET", f"/api/boards/{q(agent)}/history")["versions"]:
            titles = "、".join(b["title"] or b["id"] for b in v["blocks"]) if lang() == "zh" else ", ".join(b["title"] or b["id"] for b in v["blocks"])
            print(f"v{v['version']}\t{v['status']}\t{v['by']}\t{v['createdAt'][:16].replace('T', ' ')}\t{v['note']}\t[{titles}]")
    else:
        r = call("POST", f"/api/boards/{q(agent)}/revert", {"version": a.version})
        print(L(f"回到了第 {a.version} 版（新的一版是 {r['version']}）", f"Back to version {a.version} (now version {r['version']})"))


if __name__ == "__main__":
    main()
