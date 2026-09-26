#!/usr/bin/env python3
"""项目（有始有终的事：目标、截止、下一步、已定的、进度）：Agent 在命令行里看、开、改。走服务的 HTTP 接口（/api/projects）。

看
  python3 project_ctl.py list [--all] [--json]                 # 没归档的项目（--all 连归档的）：最近的截止、还剩几件下一步
  python3 project_ctl.py show <项目 id> [--json]                # 一张项目卡，每条带 id
开
  python3 project_ctl.py create --title "CS 小组作业" [--goal "交出视频和书面报告"] [--deadline "交小组视频|2026-10-02 09:00"]…
      [--link canvas:…]… [--step "…"]… [--decision "…"]… [--brief "主对话里聊过的要点" | --brief-file 文件|-] [--model …]
                                                              # 用户让开的：直接开；--brief 转进新项目（出转交卡），不等它回
  python3 project_ctl.py propose （参数同 create）--why "这周第 3 次聊、有两个截止"   # 你自己想到的：进收件箱等用户点头
改（改完回复下面出一张小卡，能撤销；截止的增删改和打勾出的是日程卡）
  python3 project_ctl.py goal <项目 id> "…"                     # 目标（空字符串 = 清掉）
  python3 project_ctl.py progress <项目 id> "一句话"             # 进度（日结时更新）
  python3 project_ctl.py rename <项目 id> "新名字"
  python3 project_ctl.py add <项目 id> step|decision "…"        # 下一步 / 已定的
  python3 project_ctl.py add <项目 id> deadline "交小组视频" --due "2026-10-02 09:00"   # 新截止（进日程和「要记得的」）
  python3 project_ctl.py link <项目 id> <要记得的 id>            # 挂上一条已有的截止（作业、邮件里的事、求职 ddl；id 从 schedule_ctl.py remember 拿）
  python3 project_ctl.py done <项目 id> <条目 id> [--undo]       # 下一步、截止打勾
  python3 project_ctl.py edit <项目 id> <条目 id> [--text …] [--due "YYYY-MM-DD[ HH:MM]"]
  python3 project_ctl.py remove <项目 id> <条目 id>              # 自己的截止从日程里删掉；挂上的只从卡上拿掉
  python3 project_ctl.py undo <改动号> [--redo]
转进项目、收尾
  python3 project_ctl.py ask <项目 id> "问题或要点" [--timeout 150]   # 主对话把项目的事转进项目，等它答完把答案带回来
  python3 project_ctl.py conclude <项目 id> --done "做成了什么" [--decided "…"]… [--learned "下次记得"] [--saved "存到了哪"] [--keep-open]
  python3 project_ctl.py archive <项目 id> [--no-summary]         # 归档（默认让它先写结论）
  python3 project_ctl.py review                                    # 日结用：截止都过了几天的项目问一次「归档？」

条目 id 从 show 的输出里拿：pi-… 下一步 / 已定的，item:… 自己的截止，canvas:… / mail:… / app:… 挂上的截止。
不给 --source：当前目录在哪个 Agent 的工作区里就算它的，否则是 main。
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
from datetime import date

from i18n import L, lang
from inbox_ctl import call, guess_source
from schedule_ctl import parse_day


def description() -> str:
    return L(__doc__, """Projects (things with an end: goal, deadlines, next steps, decisions, progress): look at, open and change them from the command line.
Uses the server's HTTP API (/api/projects).

Read
  python3 project_ctl.py list [--all] [--json]
  python3 project_ctl.py show <project id> [--json]
Open
  python3 project_ctl.py create --title "Group project" [--goal "…"] [--deadline "Submit video|2026-10-02 09:00"]…
      [--link canvas:…]… [--step "…"]… [--decision "…"]… [--brief "what was discussed" | --brief-file FILE|-] [--model …]
  python3 project_ctl.py propose (same options as create) --why "third time this week; two deadlines"   # your own idea: goes to the inbox
Change (a small card with Undo appears under your reply; deadline changes and ticks show as schedule cards)
  python3 project_ctl.py goal <id> "…" | progress <id> "…" | rename <id> "…"
  python3 project_ctl.py add <id> step|decision "…"
  python3 project_ctl.py add <id> deadline "Submit video" --due "2026-10-02 09:00"
  python3 project_ctl.py link <id> <reminder id>
  python3 project_ctl.py done <id> <item id> [--undo]
  python3 project_ctl.py edit <id> <item id> [--text …] [--due "YYYY-MM-DD[ HH:MM]"]
  python3 project_ctl.py remove <id> <item id>
  python3 project_ctl.py undo <change number> [--redo]
Hand over, wrap up
  python3 project_ctl.py ask <id> "question or notes" [--timeout 150]
  python3 project_ctl.py conclude <id> --done "…" [--decided "…"]… [--learned "…"] [--saved "…"] [--keep-open]
  python3 project_ctl.py archive <id> [--no-summary]
  python3 project_ctl.py review

Item ids come from show: pi-… steps / decisions, item:… own deadlines, canvas:… / mail:… / app:… linked deadlines.
Without --source: the Agent whose workspace contains the current directory, otherwise main.
""")


def path(pid: str, tail: str = "") -> str:
    return "/api/projects/" + urllib.parse.quote(pid) + tail


def fmt_day(d: str | None, t: str | None = None) -> str:
    if not d:
        return L("没定日子", "no date")
    x = date.fromisoformat(d)
    return L(f"{x.month}/{x.day} 周{'一二三四五六日'[x.weekday()]}", f"{x:%a} {x.day} {x:%b}") + (f" {t}" if t else "")


def left_words(n: int | None) -> str:
    if n is None:
        return ""
    if n < 0:
        return L(f"过了 {-n} 天", f"{-n} days ago")
    return L("今天" if n == 0 else f"还剩 {n} 天", "today" if n == 0 else f"{n} days left")


def show(c: dict) -> None:
    head = f"{c['title']}（{c['id']}）" if lang() == "zh" else f"{c['title']} ({c['id']})"
    print(head + (L(" · 已归档", " · archived") if c.get("archived") else ""))
    if c.get("goal"):
        print(L(f"目标：{c['goal']}", f"Goal: {c['goal']}"))
    if c.get("deadlines"):
        print(L("截止：", "Deadlines:"))
        for d in c["deadlines"]:
            tail = L("已勾", "ticked") if d["done"] else left_words(d.get("left"))
            src = f" [{d['badge']}]" if d.get("badge") and not d.get("own") else ""
            print(f"  [{'x' if d['done'] else ' '}] {fmt_day(d.get('date'), d.get('start') or None)} {d['title']}{src}" + (f"（{tail}）" if tail else ""))
            print(f"        id={d['id']}")
    for kind, label in (("steps", L("下一步：", "Next steps:")), ("decisions", L("已定的：", "Decided:"))):
        if c.get(kind):
            print(label)
            for s in c[kind]:
                box = f"[{'x' if s['done'] else ' '}] " if kind == "steps" else "- "
                print(f"  {box}{s['text']}  id={s['id']}")
    if c.get("progress"):
        print(L(f"进度（{(c.get('progressAt') or '')[:10]}）：{c['progress']}", f"Progress ({(c.get('progressAt') or '')[:10]}): {c['progress']}"))
    tk = c.get("tasks") or {}
    if tk.get("items"):
        print(L(f"任务：{tk['running']} 个在跑，{tk['done']} 个结束了", f"Tasks: {tk['running']} running, {tk['done']} finished"))
        for x in tk["items"]:
            print(f"  · {x['title']}（{x['status']}）")
    s = c.get("summary")
    if s:
        print(L("结论：", "Summary:"))
        for k, lab in (("done", L("做成了", "Done")), ("learned", L("下次记得", "Next time"))):
            if s.get(k):
                print(f"  {lab}：{s[k]}")
        for d in s.get("decided") or []:
            print(f"  - {d}")


def changed(r: dict) -> None:
    cards = r.get("cards") or ([r["card"]] if r.get("card") else [])
    if not cards:
        print(L("没有变化", "No change") if r.get("changed") is False else (r.get("id") or "ok"))
        return
    for c in cards:
        if c.get("kind") == "schedule":  # 截止的改动记在日程层：撤销用 schedule_ctl.py undo
            print(L(f"改好了（日程改动号 {c['logId']}，撤销用 schedule_ctl.py undo {c['logId']}）：{c['title']} · {c['summary']}",
                    f"Done (schedule change {c['logId']}; undo with schedule_ctl.py undo {c['logId']}): {c['title']} · {c['summary']}"))
        else:
            print(L(f"改好了（改动号 {c['logId']}）：{c['title']} · {c['summary']}", f"Done (change {c['logId']}): {c['title']} · {c['summary']}"))
    if r.get("id"):
        print(f"id={r['id']}")


def deadline_arg(v: str) -> dict:
    """「交小组视频|2026-10-02 09:00」→ {title, due}。日子也能写 明天 / +3。"""
    title, sep, when = v.rpartition("|")
    if not sep or not title.strip():
        sys.exit(L(f"--deadline 写成「交什么|YYYY-MM-DD HH:MM」：{v}", f"--deadline looks like \"what|YYYY-MM-DD HH:MM\": {v}"))
    return {"title": title.strip(), "due": due_arg(when)}


def due_arg(v: str) -> str:
    day, _, t = v.strip().partition(" ")
    return f"{parse_day(day)} {t.strip()}".strip()


def read_brief(a: argparse.Namespace) -> str:
    if a.brief_file:
        return (sys.stdin.read() if a.brief_file == "-" else open(a.brief_file, encoding="utf8").read()).strip()
    return (a.brief or "").strip()


def project_body(a: argparse.Namespace, src: str) -> dict:
    deadlines = [deadline_arg(v) for v in a.deadline] + [{"ref": r} for r in a.link]
    body = {"title": a.title, "goal": a.goal or "", "deadlines": deadlines, "steps": a.step, "decisions": a.decision, "brief": read_brief(a),
            "source": src}
    if a.model:
        body["model"] = a.model
    return body


def main() -> None:
    p = argparse.ArgumentParser(description=description(), formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--source", help=L("你的 Agent id（默认按当前目录猜）", "your Agent id (guessed from the current directory)"))
    common = argparse.ArgumentParser(add_help=False)  # --source 写在子命令后面也认
    common.add_argument("--source", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    sub = p.add_subparsers(dest="cmd", required=True)

    def parser(name: str) -> argparse.ArgumentParser:
        return sub.add_parser(name, parents=[common])

    s = parser("list")
    s.add_argument("--all", action="store_true")
    s.add_argument("--json", action="store_true")
    s = parser("show")
    s.add_argument("id")
    s.add_argument("--json", action="store_true")
    for name in ("create", "propose"):
        s = parser(name)
        s.add_argument("--title", required=True)
        s.add_argument("--goal", default="")
        s.add_argument("--deadline", action="append", default=[], help=L("交什么|YYYY-MM-DD HH:MM", "what|YYYY-MM-DD HH:MM"))
        s.add_argument("--link", action="append", default=[], help=L("挂一条已有的截止（要记得的 id）", "link an existing deadline (reminder id)"))
        s.add_argument("--step", action="append", default=[])
        s.add_argument("--decision", action="append", default=[])
        s.add_argument("--brief", default="")
        s.add_argument("--brief-file")
        s.add_argument("--model")
        if name == "propose":
            s.add_argument("--why", required=True)
            s.add_argument("--dedupe", default="")
    for name in ("goal", "progress", "rename"):
        s = parser(name)
        s.add_argument("id")
        s.add_argument("text")
    s = parser("add")
    s.add_argument("id")
    s.add_argument("kind", choices=["step", "decision", "deadline"])
    s.add_argument("text")
    s.add_argument("--due")
    s = parser("link")
    s.add_argument("id")
    s.add_argument("ref")
    s = parser("done")
    s.add_argument("id")
    s.add_argument("item")
    s.add_argument("--undo", action="store_true")
    s = parser("edit")
    s.add_argument("id")
    s.add_argument("item")
    s.add_argument("--text")
    s.add_argument("--due")
    s = parser("remove")
    s.add_argument("id")
    s.add_argument("item")
    s = parser("undo")
    s.add_argument("log", type=int)
    s.add_argument("--redo", action="store_true")
    s = parser("ask")
    s.add_argument("id")
    s.add_argument("text")
    s.add_argument("--timeout", type=int, default=150)
    s = parser("conclude")
    s.add_argument("id")
    s.add_argument("--done", default="")
    s.add_argument("--decided", action="append", default=[])
    s.add_argument("--learned", default="")
    s.add_argument("--saved", default="")
    s.add_argument("--keep-open", action="store_true", help=L("写完不归档", "don't archive after writing"))
    s = parser("archive")
    s.add_argument("id")
    s.add_argument("--no-summary", action="store_true")
    parser("review")
    a = p.parse_args()
    src = a.source or guess_source()

    if a.cmd == "list":
        d = call("GET", f"/api/projects?all={1 if a.all else 0}")
        if a.json:
            print(json.dumps(d, ensure_ascii=False, indent=1))
            return
        for x in d["projects"]:
            nxt = x.get("next")
            when = f"{fmt_day(nxt['date'], nxt.get('start') or None)} {nxt['title']}（{left_words(nxt.get('left'))}）" if nxt else L("没有截止", "no deadline")
            flag = L(" · 已归档", " · archived") if x.get("archived") else ""
            print(f"{x['id']}  {x['title']}{flag}  ·  {when}  ·  {L('下一步', 'steps')} {x.get('stepsLeft', 0)}")
    elif a.cmd == "show":
        c = call("GET", path(a.id))["project"]
        if a.json:
            print(json.dumps(c, ensure_ascii=False, indent=1))
        else:
            show(c)
    elif a.cmd == "create":
        r = call("POST", "/api/projects", project_body(a, src))
        print(L(f"开好了：{a.title}（id {r['id']}）", f"Opened: {a.title} (id {r['id']})"))
        if r.get("relay") and str(r["relay"]).startswith("failed"):
            print(L(f"要点没转进去：{r['relay']}（稍后用 ask 再转）", f"Couldn't hand over the notes: {r['relay']} (use ask later)"))
        elif r.get("relay"):
            print(L("要点转进去了，它在那边接着聊（不用等）。", "The notes were handed over; it continues there (no need to wait)."))
    elif a.cmd == "propose":
        body = project_body(a, src) | {"why": a.why, "dedupe": a.dedupe}
        r = call("POST", "/api/projects/propose", body)
        print(L(f"交到收件箱了（{r['inboxId']}），等用户点头；同意了服务端会开好。",
                f"Sent to the inbox ({r['inboxId']}); waiting for the user. The server opens it once approved."))
    elif a.cmd in ("goal", "progress", "rename"):
        key = {"goal": "goal", "progress": "progress", "rename": "title"}[a.cmd]
        changed(call("PATCH", path(a.id), {key: a.text, "source": src}))
    elif a.cmd == "add":
        body = {"kind": a.kind, "text": a.text, "source": src}
        if a.kind == "deadline":
            if not a.due:
                sys.exit(L("截止要给 --due \"YYYY-MM-DD HH:MM\"（已有的截止用 link）", "A deadline needs --due \"YYYY-MM-DD HH:MM\" (use link for an existing one)"))
            body["due"] = due_arg(a.due)
        changed(call("POST", path(a.id, "/items"), body))
    elif a.cmd == "link":
        changed(call("POST", path(a.id, "/items"), {"kind": "deadline", "ref": a.ref, "source": src}))
    elif a.cmd == "done":
        changed(call("POST", path(a.id, "/items/update"), {"id": a.item, "done": not a.undo, "source": src}))
    elif a.cmd == "edit":
        body = {"id": a.item, "source": src}
        if a.text is not None:
            body["text"] = a.text
        if a.due:
            body["due"] = due_arg(a.due)
        changed(call("POST", path(a.id, "/items/update"), body))
    elif a.cmd == "remove":
        changed(call("POST", path(a.id, "/items/delete"), {"id": a.item, "source": src}))
    elif a.cmd == "undo":
        c = call("POST", f"/api/projects/undo/{a.log}", {"redo": a.redo})["card"]
        print(L(f"{'做回来了' if a.redo else '撤销了'}：{c['title']} · {c['summary']}", f"{'Redone' if a.redo else 'Undone'}: {c['title']} · {c['summary']}"))
    elif a.cmd == "ask":
        r = call("POST", "/api/chat/relay", {"thread": a.id, "text": a.text, "timeout": a.timeout})
        if r.get("status") == "timeout":
            print(L(f"项目还在回（{r.get('seconds')} 秒了），回完会留在项目的对话里。", f"The project is still replying ({r.get('seconds')}s); the answer will stay in its chat."))
            sys.exit(2)
        if not r.get("ok"):
            sys.exit(L(f"没问成：{r.get('error') or r.get('status')}", f"Failed: {r.get('error') or r.get('status')}"))
        print(r.get("text") or "")
    elif a.cmd == "conclude":
        changed(call("POST", path(a.id, "/conclude"), {"done": a.done, "decided": a.decided, "learned": a.learned, "saved": a.saved,
                                                         "archive": not a.keep_open, "source": src}))
    elif a.cmd == "archive":
        r = call("POST", path(a.id, "/archive"), {"summarize": not a.no_summary, "source": src})
        print(L("归档了" + ("，结论在写" if r.get("summarizing") else ""), "Archived" + ("; the summary is being written" if r.get("summarizing") else "")))
    elif a.cmd == "review":
        r = call("POST", "/api/projects/review", {})
        for x in r.get("asked") or []:
            print(L(f"问了：归档「{x['title']}」？（{x.get('inboxId')}）", f"Asked: archive \"{x['title']}\"? ({x.get('inboxId')})"))
        if not r.get("asked"):
            print(L("没有要问的", "Nothing to ask"))


if __name__ == "__main__":
    main()
