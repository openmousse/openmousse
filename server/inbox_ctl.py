#!/usr/bin/env python3
"""收件箱（app 的「等你点头」）：要用户同意才做的事、你自己的提议，从命令行交上去、改、报结果。走服务的 HTTP 接口（/api/inbox）。

  python3 inbox_ctl.py add --kind write --title "把今天的午餐写进训记" --why "你说了「记上」" --change "训记：午餐加 3 项" \\
      [--detail-file 文件|-] [--approve-label 写进训记] [--level ring|quiet|none] [--dedupe 固定键] [--source 你的 id] [--thread 线程] [--expires ISO 时间]
  python3 inbox_ctl.py update <id> [--title …] [--why …] [--change …]… [--detail-file …] [--approve-label …]
  python3 inbox_ctl.py done <id> --result "一句话结果"
  python3 inbox_ctl.py fail <id> --result "为什么没做成"
  python3 inbox_ctl.py withdraw <id>
  python3 inbox_ctl.py list [--status pending|recent] [--json]
  python3 inbox_ctl.py get <id>

kind：task 任务 / write 写进外部系统 / send 发给别人 / spend 花钱 / schedule 新定时任务 / push 新推送 / calendar 日历
      / skill 新技能 / agent 新 Agent / block 看板 / code 改代码、配置、凭证 / other。
add 成功打印 id；--dedupe 相同、还在等的那条会原地更新（同一个 id）。30 天内被拒绝过的同一件事：退出码 3，别再提。
不给 --source：当前目录在哪个 Agent 的工作区里就算它的，否则是 main。
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from config import settings
from i18n import L, lang

KINDS = ("task", "write", "send", "spend", "schedule", "push", "calendar", "skill", "agent", "block", "code", "other")


def description() -> str:
    """--help 的说明，按 server.json 的 language。"""
    return L(__doc__, """The inbox ("Needs your OK" in the app): things that need the user's OK before you do them, and your own proposals.
Submit, revise and report on them from the command line. Goes through the server's HTTP API (/api/inbox).

  python3 inbox_ctl.py add --kind write --title "Log today's lunch in the training app" --why "You said 'log it'" --change "Lunch: 3 items" \\
      [--detail-file FILE|-] [--approve-label "Log it"] [--level ring|quiet|none] [--dedupe KEY] [--source YOUR_ID] [--thread THREAD] [--expires ISO_TIME]
  python3 inbox_ctl.py update <id> [--title …] [--why …] [--change …]… [--detail-file …] [--approve-label …]
  python3 inbox_ctl.py done <id> --result "one-line result"
  python3 inbox_ctl.py fail <id> --result "why it didn't work"
  python3 inbox_ctl.py withdraw <id>
  python3 inbox_ctl.py list [--status pending|recent] [--json]
  python3 inbox_ctl.py get <id>

kind: task / write (to an outside system) / send (to other people) / spend (money) / schedule (new scheduled job) / push (new notification)
      / calendar / skill (new skill) / agent (new Agent) / block (board block) / code (code, config, credentials) / other.
add prints the id; with the same --dedupe, a pending item is updated in place (same id). Rejected in the last 30 days: exit code 3, don't propose it again.
Without --source: the Agent whose workspace contains the current directory, otherwise main.
""")


def call(method: str, path: str, body: dict | None = None) -> dict:
    url = f"http://{settings.host}:{settings.port}{path}"
    data = json.dumps(body, ensure_ascii=False).encode("utf8") if body is not None else None
    # 服务回的文字（错误说明）和这个命令用同一种语言
    headers = {"Content-Type": "application/json", "Accept": "application/json", "Accept-Language": "zh-CN" if lang() == "zh" else "en",
               "X-Mousse-Client": "ctl"}  # 服务据此不给 Agent 看名片 agent 的卡（对方说的话不进 Agent）
    tokens = settings.tokens()
    if tokens:  # 本机跑，用第一个令牌；没令牌时靠 Tailscale 白名单 / trust_loopback
        headers["Authorization"] = f"Bearer {next(iter(tokens.values()))}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
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
            print(L(f"没提交：同一件事 {when} 被拒绝过（{('理由：' + note) if note else '没说理由'}）。30 天内别再提，也别争辩。",
                    f"Not submitted: the same thing was rejected on {when} ({('reason: ' + note) if note else 'no reason given'}). "
                    "Don't propose it again within 30 days, and don't argue."))
            sys.exit(3)
        msg = j.get("detail") or j.get("error") or str(e)
        sys.exit(L(f"失败（HTTP {e.code}）：{msg}", f"Failed (HTTP {e.code}): {msg}"))
    except urllib.error.URLError as e:
        sys.exit(L(f"连不上服务 {url}：{e.reason}", f"Can't reach the server at {url}: {e.reason}"))


def item_path(iid: str, tail: str = "") -> str:
    return "/api/inbox/" + urllib.parse.quote(iid, safe=":") + tail


def guess_source() -> str:
    """没给 --source：当前目录在哪个 Agent 的工作区里就算它的（Agent 跑命令默认在自己的工作区里），否则是 main。"""
    here = Path.cwd().resolve()
    for aid, ws in settings.agent_workspaces.items():
        try:
            root = ws.resolve()
        except OSError:
            continue
        if here == root or root in here.parents:
            return aid
    return "main"


def read_detail(src: str | None) -> str | None:
    if src is None:
        return None
    try:
        return sys.stdin.read() if src == "-" else Path(src).expanduser().read_text(encoding="utf8")
    except OSError as e:
        sys.exit(L(f"读不了 {src}：{e}", f"Can't read {src}: {e}"))


def content_args(p: argparse.ArgumentParser, required: bool) -> None:
    p.add_argument("--title", required=required, help=L("要做什么，大白话、动作开头", "the action, in plain words"))
    p.add_argument("--why", help=L("为什么：理由和证据（哪天说过、出现过几次）", "why: the reason and the evidence (dates, counts)"))
    p.add_argument("--change", action="append", help=L("会改变的一件具体的事，可以写多次（update 时给了就整体替换）",
                                                       "one concrete thing that will change; repeat for more (on update, replaces the list)"))
    p.add_argument("--detail-file", help=L("细节（Markdown）从这个文件读，- 表示标准输入", "details (Markdown) from this file, - for stdin"))
    p.add_argument("--approve-label", help=L("同意按钮上的字，比如「写进训记」", 'text on the approve button, e.g. "Log it"'))
    p.add_argument("--level", choices=("ring", "quiet", "none"), help=L("推送档位，不给按 kind", "notification level; defaults by kind"))
    p.add_argument("--expires", help=L("过了这个时间还没点头就作废（ISO，比如 2026-09-30T18:00）", "expires if not answered by then (ISO time)"))
    p.add_argument("--board-file", help=L('建 Agent 的方案卡（kind agent）：起步看板文件 {"tables": [...], "blocks": [...]}，卡片里画出预览；表可以带 rows 示例行',
                                          'for a new-Agent card (kind agent): the starter board file {"tables": [...], "blocks": [...]}; the card shows a preview; '
                                          'tables may carry sample rows'))


def content_body(a: argparse.Namespace) -> dict:
    body = {"title": a.title, "why": a.why, "changes": a.change, "detail": read_detail(a.detail_file), "approveLabel": a.approve_label,
            "level": a.level, "expiresAt": a.expires}
    return {k: v for k, v in body.items() if v is not None}


def show(items: list[dict]) -> None:
    for it in items:
        print(f"{it['id']}\t{it['status']}\t{it['kind']}\t{it.get('sourceName') or it.get('source')}\t{it['title']}")


def board_plan(path: str) -> dict:
    """读起步看板文件，先让服务端校验一遍（写错了不交卡，照着报错改）。"""
    try:
        text = sys.stdin.read() if path == "-" else Path(path).expanduser().read_text(encoding="utf8")
        plan = json.loads(text)
    except (OSError, ValueError) as e:
        sys.exit(L(f"--board-file 读不了：{e}", f"Can't read --board-file: {e}"))
    if not isinstance(plan, dict):
        sys.exit(L('--board-file 写成 {"tables": [...], "blocks": [...]}', '--board-file must be {"tables": [...], "blocks": [...]}'))
    call("POST", "/api/boards/plan", {"plan": plan})
    return plan


def main() -> None:
    ap = argparse.ArgumentParser(description=description(), formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    add = sub.add_parser("add", help=L("提交一件要用户点头的事，打印 id", "submit something that needs the user's OK; prints the id"))
    add.add_argument("--kind", required=True, choices=KINDS)
    content_args(add, required=True)
    add.add_argument("--dedupe", default="", help=L("同一件事的固定键（比如 diet:xunji:lunch:2026-09-26）", "a stable key for the same thing"))
    add.add_argument("--source", help=L("你是谁：main 或你的 Agent id", "who you are: main or your Agent id"))
    add.add_argument("--thread", help=L("同意后在哪个线程里接着做，默认 = source", "the thread to continue in after approval (default: source)"))
    up = sub.add_parser("update", help=L("改好重新提交（同一个 id）", "resubmit a revised item (same id)"))
    up.add_argument("id")
    content_args(up, required=False)
    for name, text in (("done", L("做完了，报结果", "report that it's done")), ("fail", L("没做成，说为什么", "report that it didn't work"))):
        p = sub.add_parser(name, help=text)
        p.add_argument("id")
        p.add_argument("--result", required=True, help=L("一句话结果", "a one-line result"))
    wd = sub.add_parser("withdraw", help=L("撤回还没定下来的一条", "withdraw an item that hasn't been decided"))
    wd.add_argument("id")
    ls = sub.add_parser("list", help=L("看收件箱", "show the inbox"))
    ls.add_argument("--status", choices=("pending", "recent"), default="pending")
    ls.add_argument("--json", action="store_true")
    gt = sub.add_parser("get", help=L("看一条的全部内容", "show one item in full"))
    gt.add_argument("id")
    a = ap.parse_args()

    plan = board_plan(a.board_file) if getattr(a, "board_file", None) else None
    if a.cmd == "add":
        body = {"kind": a.kind, "source": a.source or guess_source(), "dedupe": a.dedupe, **content_body(a)}
        if a.thread:
            body["thread"] = a.thread
        r = call("POST", "/api/inbox", body)
        if r.get("updated"):
            print(L("（原地更新了还在等的同一件事）", "(updated the pending item with the same dedupe key)"), file=sys.stderr)
        if plan is not None:
            call("PUT", f"/api/boards/plan/{urllib.parse.quote(r['id'], safe='')}", {"plan": plan})
        print(r["id"])
    elif a.cmd == "update":
        body = content_body(a)
        if plan is not None:
            call("PUT", f"/api/boards/plan/{urllib.parse.quote(a.id, safe='')}", {"plan": plan})
            if not body:
                print(a.id)
                return
        if not body:
            sys.exit(L("没有要改的内容", "Nothing to change"))
        it = call("PATCH", item_path(a.id), body)["item"]
        print(json.dumps({"ok": True, "id": it["id"], "status": it["status"]}, ensure_ascii=False))
    elif a.cmd in ("done", "fail"):
        it = call("POST", item_path(a.id, "/result"), {"status": "done" if a.cmd == "done" else "failed", "result": a.result})["item"]
        print(json.dumps({"ok": True, "id": it["id"], "status": it["status"]}, ensure_ascii=False))
    elif a.cmd == "withdraw":
        it = call("POST", item_path(a.id, "/withdraw"), {})["item"]
        print(json.dumps({"ok": True, "id": it["id"], "status": it["status"]}, ensure_ascii=False))
    elif a.cmd == "list":
        r = call("GET", f"/api/inbox?status={a.status}")
        if a.json:
            print(json.dumps(r["items"], ensure_ascii=False, indent=1))
        else:
            show(r["items"])
            if not r["items"]:
                print(L("（空）", "(empty)"))
    else:
        print(json.dumps(call("GET", item_path(a.id))["item"], ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
