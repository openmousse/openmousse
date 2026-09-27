#!/usr/bin/env python3
"""日结提案：回看这一周的对话，把值得固定下来的做法提成「加一个 skill」或「建一个 Agent」，交进收件箱等用户点头。走服务的 HTTP 接口（/api/proposals）。

  python3 proposals_ctl.py context [--days 7] [--json]     # 回看的材料：这几天用户说了什么、现有的 skills 和 Agent、提过的提案、今天还能提几条
  python3 proposals_ctl.py skill --slug pdf-zh --name pdf-translate --agents main[,diet] --title "加一个做法：英文 PDF 全文译成中文" \\
      --why "这周你第 3 次让我把英文 PDF 翻成中文" --evidence "9/24|主对话|把这个 PDF 翻成中文" [--evidence …] [--change …] --file 草稿.md
  python3 proposals_ctl.py agent --slug reading --name 读书 --purpose "记在读的书、读书笔记和进度" --icon book --color purple \\
      [--board-file 看板.json] --title "新建 Agent「读书」" --why "…" --evidence "…" [--change …]
  python3 proposals_ctl.py list [--status pending|installed|rejected|withdrawn|failed]
  python3 proposals_ctl.py show <提案 id 或收件箱 id>
  python3 proposals_ctl.py withdraw <提案 id>

--evidence 写成「日期|在哪个对话|原话」，至少一条。--file 是 SKILL.md 全文（开头 frontmatter：name 和 --name 一样，description 写什么时候用）。
用户点了同意，服务端自己装好 skill（加进那些 Agent 的允许列表）/ 建好 Agent，你只会收到一句知会；拒绝了就永远别再提同一件事。
退出码：3 = 这件事提过了（别再提）；4 = 今天的额度用完了；5 = 已经有同名的 skill。
"""
from __future__ import annotations

import argparse
import json
import signal
import sys
import urllib.error
import urllib.parse
import urllib.request

from config import settings
from i18n import L, lang


def description() -> str:
    return L(__doc__, """Nightly proposals: look back over this week's conversations and turn what is worth keeping into "add a skill" or "create an Agent",
submitted to the inbox for the user's OK. Goes through the server's HTTP API (/api/proposals).

  python3 proposals_ctl.py context [--days 7] [--json]     # the material: what the user said, existing skills and Agents, past proposals, today's quota
  python3 proposals_ctl.py skill --slug pdf-zh --name pdf-translate --agents main[,diet] --title "New skill: translate English PDFs into Chinese" \\
      --why "Third time this week you asked me to translate a PDF" --evidence "9/24|Main chat|translate this PDF" [--evidence …] [--change …] --file DRAFT.md
  python3 proposals_ctl.py agent --slug reading --name Reading --purpose "Books in progress, notes and pace" --icon book --color purple \\
      [--board-file BOARD.json] --title "New Agent: Reading" --why "…" --evidence "…" [--change …]
  python3 proposals_ctl.py list [--status pending|installed|rejected|withdrawn|failed]
  python3 proposals_ctl.py show <proposal id or inbox id>
  python3 proposals_ctl.py withdraw <proposal id>

--evidence is "date|which chat|the words used", at least one. --file is the full SKILL.md (frontmatter first: name equal to --name, and a description of when to use it).
When the user approves, the server installs the skill (and adds it to those Agents' allowlists) or creates the Agent; you only get a note.
Rejected means never propose the same thing again.
Exit codes: 3 = proposed before (don't again); 4 = today's quota is used up; 5 = a skill with that name exists.
""")


def call(method: str, path: str, body: dict | None = None) -> dict:
    url = f"http://{settings.host}:{settings.port}{path}"
    data = json.dumps(body, ensure_ascii=False).encode("utf8") if body is not None else None
    headers = {"Content-Type": "application/json", "Accept": "application/json", "Accept-Language": "zh-CN" if lang() == "zh" else "en"}
    tokens = settings.tokens()
    if tokens:  # 本机跑，用第一个令牌；没令牌时靠 Tailscale 白名单 / trust_loopback
        headers["Authorization"] = f"Bearer {next(iter(tokens.values()))}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:  # noqa: S310 — 本机的服务
            return json.loads(r.read().decode("utf8"))
    except urllib.error.HTTPError as e:
        try:
            j = json.loads(e.read().decode("utf8"))
        except ValueError:
            j = {}
        msg = j.get("detail") or j.get("error") or str(e)
        if e.code == 409 and j.get("error") in ("proposed_before", "rejected_before"):
            print(L(f"没提交：{msg}。同一件事别再提，也别换个说法再提。", f"Not submitted: {msg}. Don't propose it again, not even reworded."))
            sys.exit(3)
        if e.code == 429:
            print(L(f"没提交：{msg}", f"Not submitted: {msg}"))
            sys.exit(4)
        if e.code == 409 and j.get("error") == "skill_exists":
            print(L(f"没提交：{msg}", f"Not submitted: {msg}"))
            sys.exit(5)
        sys.exit(L(f"失败（HTTP {e.code}）：{msg}", f"Failed (HTTP {e.code}): {msg}"))
    except urllib.error.URLError as e:
        sys.exit(L(f"连不上服务（{url}）：{e.reason}", f"Can't reach the server ({url}): {e.reason}"))


def evidence(items: list[str]) -> list[dict]:
    out = []
    for raw in items or []:
        parts = raw.split("|", 2)
        if len(parts) == 3:
            out.append({"date": parts[0].strip(), "thread": parts[1].strip(), "quote": parts[2].strip()})
        else:
            out.append({"date": "", "thread": "", "quote": raw.strip()})
    return out


STATUS_ZH = {"pending": "等点头", "installed": "做好了", "rejected": "被拒了", "withdrawn": "撤回了", "failed": "没做成", "expired": "过期了"}


def print_context(c: dict) -> None:
    zh = lang() == "zh"
    q = c.get("quota") or {}
    left = max(0, int(q.get("max", 0)) - int(q.get("used", 0)))
    print(L(f"今天（逻辑日）{c['today']}：还能提 {left} 条（每天最多 {q.get('max')} 条）。多数晚上没有值得提的，那就不提。",
            f"Today (logical day) {c['today']}: {left} more allowed (max {q.get('max')} a day). Most nights there is nothing worth proposing; then propose nothing."))
    print()
    print(L(f"## 这 {len(c['days'])} 天里用户说过的话（→ 后面是回复的开头）", f"## What the user said over the last {len(c['days'])} days (→ starts the reply)"))
    if not c["days"]:
        print(L("（没有对话）", "(no conversations)"))
    for d in c["days"]:
        print(f"### {d['day']}")
        for m in d["messages"]:
            files = L(f"（附件 {m['files']}）", f" ({m['files']} files)") if m.get("files") else ""
            reply = f" → {m['reply']}" if m.get("reply") else ""
            print(f"- {m['time']} [{m['where']}] {m['text']}{files}{reply}")
    print()
    print(L("## 现有的 skills（名字：做什么｜谁能用）", "## Existing skills (name: what it does | who has it)"))
    names = {a["id"]: a["name"] for a in c.get("agents") or []}
    for s in c.get("skills") or []:
        who = ("、" if zh else ", ").join(names.get(a, a) for a in s.get("agents") or []) or L("没人", "nobody")
        print(f"- {s['name']}：{s['description']}｜{who}")
    print()
    print(L("## Agent（skill 给谁：写这里的 id）", "## Agents (give a skill to: use these ids)"))
    for a in c.get("agents") or []:
        print(f"- {a['id']}（{a['name']}）：{a['purpose']}")
    print()
    print(L("## 提过的提案（同一件事、换个说法的也算，都别再提；被拒的理由要照做）", "## Past proposals (don't propose any of these again, reworded or not; respect the reasons given)"))
    past = c.get("proposals") or []
    if not past:
        print(L("（还没有）", "(none yet)"))
    for p in past:
        st = STATUS_ZH.get(p["status"], p["status"]) if zh else p["status"]
        note = L(f"，理由：{p['note']}", f", reason: {p['note']}") if p.get("note") else ""
        print(f"- {p['day']} {p['kind']} {p['name']}「{p['title']}」：{st}{note}")


def main() -> None:
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)  # 接 | head 时安静退出，不打一屏 BrokenPipeError
    ap = argparse.ArgumentParser(description=description(), formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("context", help=L("回看的材料", "the material to look back at"))
    c.add_argument("--days", type=int, default=7)
    c.add_argument("--json", action="store_true")

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--slug", required=True, help=L("这件事的固定键：小写字母、数字、连字符", "a fixed key for this idea: lowercase letters, digits, hyphens"))
        p.add_argument("--title", required=True, help=L("卡片标题，大白话", "card title, plain words"))
        p.add_argument("--why", required=True, help=L("为什么：带次数和时间，「这周你第 3 次让我……」", 'why, with how often: "third time this week you asked me to…"'))
        p.add_argument("--evidence", action="append", default=[], help=L("日期|在哪个对话|原话（可以写多条）", "date|which chat|the words (repeatable)"))
        p.add_argument("--change", action="append", default=[], help=L("再补一行「会改什么」（可以写多条）", "one more \"what changes\" line (repeatable)"))
        p.add_argument("--source", default="main")

    s = sub.add_parser("skill", help=L("提一个新 skill", "propose a new skill"))
    common(s)
    s.add_argument("--name", required=True, help=L("skill 名 = 目录名", "skill name = directory name"))
    s.add_argument("--agents", required=True, help=L("给谁：main 或 Agent id，逗号隔开", "for whom: main or Agent ids, comma-separated"))
    s.add_argument("--file", required=True, help=L("SKILL.md 全文（- = 从标准输入读）", "the full SKILL.md (- = stdin)"))
    g = sub.add_parser("agent", help=L("提一个新 Agent", "propose a new Agent"))
    common(g)
    g.add_argument("--name", required=True)
    g.add_argument("--purpose", required=True)
    g.add_argument("--icon", default="moon")
    g.add_argument("--color", default=None)
    g.add_argument("--board-file", default=None, help=L('起步看板 {"tables": [...], "blocks": [...]}（写法见 board skill）', 'starter board {"tables": [...], "blocks": [...]} (see the board skill)'))
    ls = sub.add_parser("list")
    ls.add_argument("--status", default=None)
    sh = sub.add_parser("show")
    sh.add_argument("id")
    w = sub.add_parser("withdraw")
    w.add_argument("id")
    a = ap.parse_args()

    if a.cmd == "context":
        ctx = call("GET", f"/api/proposals/context?days={a.days}")
        if a.json:
            print(json.dumps(ctx, ensure_ascii=False, indent=1))
        else:
            print_context(ctx)
    elif a.cmd in ("skill", "agent"):
        body: dict = {"kind": a.cmd, "slug": a.slug, "title": a.title, "why": a.why, "evidence": evidence(a.evidence), "changes": a.change, "source": a.source}
        if a.cmd == "skill":
            try:
                md = sys.stdin.read() if a.file == "-" else open(a.file, encoding="utf8").read()
            except OSError as e:
                sys.exit(L(f"--file 读不了：{e}", f"Can't read --file: {e}"))
            body["skill"] = {"name": a.name, "agents": [x.strip() for x in a.agents.split(",") if x.strip()], "markdown": md}
        else:
            board = None
            if a.board_file:
                try:
                    board = json.loads(open(a.board_file, encoding="utf8").read())
                except (OSError, ValueError) as e:
                    sys.exit(L(f"--board-file 读不了：{e}", f"Can't read --board-file: {e}"))
            body["agent"] = {"name": a.name, "purpose": a.purpose, "icon": a.icon, "color": a.color, "board": board}
        r = call("POST", "/api/proposals", body)
        print(json.dumps({"ok": True, "id": r["id"], "inboxId": r["inboxId"]}, ensure_ascii=False))
    elif a.cmd == "list":
        q = f"?status={urllib.parse.quote(a.status)}" if a.status else ""
        for p in call("GET", f"/api/proposals{q}")["items"]:
            print(f"{p['id']}  {p['day']}  {p['kind']:<5}  {p['status']:<9}  {p['name']}「{p['title']}」" + (f"  — {p['note']}" if p.get("note") else ""))
    elif a.cmd == "show":
        print(json.dumps(call("GET", f"/api/proposals/{urllib.parse.quote(a.id, safe='')}")["item"], ensure_ascii=False, indent=1))
    elif a.cmd == "withdraw":
        p = call("GET", f"/api/proposals/{urllib.parse.quote(a.id, safe='')}")["item"]
        if not p.get("inboxId"):
            sys.exit(L("这条提案没有收件箱卡", "This proposal has no inbox card"))
        call("POST", f"/api/inbox/{urllib.parse.quote(p['inboxId'], safe='')}/withdraw")
        print(json.dumps({"ok": True, "withdrawn": p["id"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
