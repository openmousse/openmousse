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


def board_file(path: str) -> list:
    raw = read_json(path, None)
    blocks = raw.get("blocks") if isinstance(raw, dict) else raw
    if not isinstance(blocks, list):
        sys.exit(L('看板文件写成 {"blocks": [ … ]}', 'The board file is {"blocks": [ … ]}'))
    return blocks


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


def show_board(r: dict) -> None:
    print(L(f"看板第 {r['version']} 版（{r.get('note') or '没写说明'}）", f"Board version {r['version']} ({r.get('note') or 'no note'})") if r["version"]
          else L("还没有看板配置", "No board config yet"))
    if r.get("anchors"):
        print(L("能插的位置（after）：", "Anchors for after: ") + ", ".join(r["anchors"]))
    for b in r["blocks"]:
        where = f" after={b['after']}" if b.get("after") else ""
        hid = L(" (藏着)", " (hidden)") if b.get("hidden") else ""
        print(f"- {b['id']} [{b['type']}] {b.get('title', '')}{where}{hid}: {summary(b)}")
    if r.get("collections"):
        print(L("表：", "Tables:"))
        for c in r["collections"]:
            st = "" if c["status"] == "active" else f" ({c['status']})"
            print(f"- {c['name']}{st} {c['title']} · {c.get('count', 0)} " + L("行", "rows") + " · " + ", ".join(f"{f['key']}:{f['type']}" for f in c["fields"]))


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
        p.add_argument("--file", required=True, help=L('看板文件：{"blocks": [ … ]}', 'board file: {"blocks": [ … ]}'))
        if name == "apply":
            p.add_argument("--note", default="", help=L("这一版改了什么，一句话（看板顶上的撤回条里显示）", "what changed, one line (shown in the undo strip)"))
        if name == "propose":
            p.add_argument("--title", required=True, help=L("收件箱卡的标题：动作本身，大白话", "the inbox card title: the action, in plain words"))
            p.add_argument("--why", default="", help=L("为什么：证据（哪天说过、几次）", "why: the evidence (dates, counts)"))
            p.add_argument("--change", action="append", default=[], help=L("会改变的一件事，可以写多次", "one thing that will change; repeat for more"))
            p.add_argument("--note", default="", help=L("这一版改了什么，一句话", "what changed, one line"))
            p.add_argument("--dedupe", default="", help=L("同一个提案的固定键（改好重交会原地更新同一张卡）", "a stable key (resubmitting updates the same card)"))
    sub.add_parser("history", help=L("看板的每一版", "every version of the board"))
    rv = sub.add_parser("revert", help=L("回到某一版（0 = 空看板）", "go back to a version (0 = empty board)"))
    rv.add_argument("version", type=int)
    a = ap.parse_args()
    agent = a.agent or guess_agent()

    if a.cmd == "show":
        r = call("GET", f"/api/boards/{q(agent)}")
        if a.json:
            print(json.dumps(r, ensure_ascii=False, indent=1))
        else:
            show_board(r)
    elif a.cmd == "get":
        r = call("GET", f"/api/boards/{q(agent)}")
        print(json.dumps({"blocks": [{k: v for k, v in b.items() if k != "data"} for b in r["blocks"]]}, ensure_ascii=False, indent=1))
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
    elif a.cmd in ("check", "apply", "propose"):
        blocks = board_file(a.file)
        if a.cmd == "check":
            r = call("PUT", f"/api/boards/{q(agent)}", {"blocks": blocks, "dryRun": True})
            print(L("没问题，会显示成这样：", "Looks fine. It would show:"))
            show_board({**r, "version": 0})
        elif a.cmd == "apply":
            r = call("PUT", f"/api/boards/{q(agent)}", {"blocks": blocks, "mode": "apply", "note": a.note})
            print(L(f"换上了，第 {r['version']} 版。app 看板顶上有「撤回」；对话里告诉用户加了什么。",
                    f"Done, version {r['version']}. The app shows an undo strip on the board; tell the user what you added."))
        else:
            r = call("PUT", f"/api/boards/{q(agent)}", {"blocks": blocks, "mode": "propose", "note": a.note, "title": a.title, "why": a.why,
                                                       "changes": a.change, "dedupe": a.dedupe})
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
