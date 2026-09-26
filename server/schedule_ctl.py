#!/usr/bin/env python3
"""日程和「要记得的」：Agent 在命令行里读、改。走服务的 HTTP 接口（/api/schedule、/api/remember）。

读
  python3 schedule_ctl.py day [--date 今天|明天|YYYY-MM-DD] [--days N] [--json]   # 合并后的日程：课表 + 自己的 + 当天的截止
  python3 schedule_ctl.py remember [--all] [--json]                               # 要记得的：作业、邮件里的事、求职 / 申请的 ddl
  python3 schedule_ctl.py log [--limit N]                                         # 最近的改动（谁改了什么）
改（改完回复下面自动出一张日程卡，能撤销；输出里有改动号）
  python3 schedule_ctl.py add --title "训练 · Push A" --date 今天 --start 17:30 --end 18:30 [--location …] [--note …] [--deadline] [--key 幂等键]
  python3 schedule_ctl.py update <id> [--title …] [--date …] [--start HH:MM] [--end HH:MM] [--all-day] [--location …] [--note …]
  python3 schedule_ctl.py delete <id>
  python3 schedule_ctl.py skip <课的 id> [--every-week] [--undo]                 # 课「不去」（--undo = 照去）；课表本身不动
  python3 schedule_ctl.py attend <id> yes|no [--actual 17:40-18:35]               # 过去的：去没去 / 做没做、实际时间
  python3 schedule_ctl.py place <课的 id> [--location …] [--note …]                # 课改地点、加备注
  python3 schedule_ctl.py done <要记得的 id> [--undo]                              # 打勾：做完了 / 不用管
  python3 schedule_ctl.py edit <mail:… 或 item:…> [--title …] [--due "YYYY-MM-DD[ HH:MM]" | --no-due] [--detail …] [--type todo|money|status|security]
  python3 schedule_ctl.py undo <改动号> [--redo]

id 从 day / remember 的输出里拿：item:… 自己的，ics:… 课，canvas:… 作业，mail:… 邮件条目，app:… 求职 / 申请。
--key：同一个 key 再 add 就是改那一条（健身排训练用 fitness:training:<日期>）。
不给 --source：当前目录在哪个 Agent 的工作区里就算它的，否则是 main。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
from datetime import date, datetime, timedelta

from config import TZ
from i18n import L
from inbox_ctl import call, guess_source


def description() -> str:
    return L(__doc__, """Schedule and "To remember": read and change them from the command line. Uses the server's HTTP API.

Read
  python3 schedule_ctl.py day [--date today|tomorrow|YYYY-MM-DD] [--days N] [--json]   # merged schedule: timetable + own items + deadlines due that day
  python3 schedule_ctl.py remember [--all] [--json]                                   # to remember: coursework, things from email, application deadlines
  python3 schedule_ctl.py log [--limit N]                                             # recent changes
Change (a schedule card with Undo appears under your reply; the output has the change number)
  python3 schedule_ctl.py add --title "Workout · Push A" --date today --start 17:30 --end 18:30 [--location …] [--note …] [--deadline] [--key KEY]
  python3 schedule_ctl.py update <id> [--title …] [--date …] [--start HH:MM] [--end HH:MM] [--all-day] [--location …] [--note …]
  python3 schedule_ctl.py delete <id>
  python3 schedule_ctl.py skip <class id> [--every-week] [--undo]
  python3 schedule_ctl.py attend <id> yes|no [--actual 17:40-18:35]
  python3 schedule_ctl.py place <class id> [--location …] [--note …]
  python3 schedule_ctl.py done <reminder id> [--undo]
  python3 schedule_ctl.py edit <mail:… or item:…> [--title …] [--due "YYYY-MM-DD[ HH:MM]" | --no-due] [--detail …] [--type todo|money|status|security]
  python3 schedule_ctl.py undo <change number> [--redo]

Ids come from day / remember: item:… your own, ics:… a class, canvas:… coursework, mail:… an email entry, app:… an application.
--key: adding again with the same key updates that item. Without --source: the Agent whose workspace contains the current directory, otherwise main.
""")


def parse_day(v: str | None) -> str:
    """今天 / 明天 / 后天 / +N / YYYY-MM-DD → YYYY-MM-DD（按服务器的时区）。"""
    t = datetime.now(TZ).date()
    if not v:
        return t.isoformat()
    v = v.strip().lower()
    rel = {"今天": 0, "today": 0, "明天": 1, "tomorrow": 1, "后天": 2, "昨天": -1, "yesterday": -1}
    if v in rel:
        return (t + timedelta(days=rel[v])).isoformat()
    if re.fullmatch(r"[+-]\d{1,3}", v):
        return (t + timedelta(days=int(v))).isoformat()
    try:
        return date.fromisoformat(v).isoformat()
    except ValueError:
        sys.exit(L(f"日期看不懂：{v}（写 今天 / 明天 / +3 / YYYY-MM-DD）", f"Can't read the date: {v} (use today / tomorrow / +3 / YYYY-MM-DD)"))


def when(e: dict) -> str:
    if e.get("allDay") or not e.get("start"):
        return L("全天", "all day")
    return f"{e['start']}–{e['end']}" if e.get("end") else e["start"]


def show_day(d: dict) -> None:
    last = None
    for e in d.get("events") or []:
        if e["date"] != last:
            last = e["date"]
            print(f"\n{e['date']} {L('周', '')}{e['weekday']}")
        flags = []
        if e.get("skip"):
            flags.append(L("不去（每周）", "skipping (weekly)") if e.get("series") else L("不去", "skipping"))
        if e.get("done"):
            flags.append(L("已勾", "ticked"))
        if e.get("attended") is True:
            flags.append(L("去了", "went") + (f" {e['actualStart']}–{e['actualEnd']}" if e.get("actualStart") else ""))
        elif e.get("attended") is False:
            flags.append(L("没去", "didn't go"))
        if e.get("clash"):
            flags.append(L("和 ", "clashes with ") + "、".join(e["clash"]) + L(" 撞了", ""))
        if e.get("tentative"):
            flags.append(L("暂定", "tentative"))
        if e.get("free"):
            flags.append("FREE")
        kind = {"class": L("课", "class"), "event": L("日程", "event"), "deadline": L("截止", "due")}.get(e["kind"], e["kind"])
        who = f" · {e['badge']}" if e.get("badge") else ""
        loc = f" @ {e['location']}" if e.get("location") else ""
        print(f"  {when(e):<12} [{kind}{who}] {e['title']}{loc}" + (f"  ({'; '.join(flags)})" if flags else ""))
        print(f"               id={e['id']}")
    errs = d.get("errors") or {}
    for k, v in errs.items():
        print(L(f"\n注意：{k} 读不到：{v}", f"\nNote: couldn't read {k}: {v}"))


GROUPS = {"security": L("可疑的安全提醒", "Security alerts"), "overdue": L("过了的", "Overdue"), "tomorrow": L("明天", "Tomorrow"),
          "week": L("一周内", "This week"), "later": L("以后", "Later"), "nodate": L("没定日子的", "No date"), "news": L("邮件动态", "Email updates")}


def show_remember(d: dict) -> None:
    last = None
    for e in d.get("items") or []:
        if e["group"] != last:
            last = e["group"]
            print(f"\n{GROUPS.get(last, last)}")
        due = f"{e['date']} {e['start']}".strip() if e.get("date") else "-"
        extra = (L("  和 ", "  clashes with ") + "、".join(e["clash"]) + L(" 撞了", "")) if e.get("clash") else ""
        print(f"  {'✓' if e.get('done') else '·'} {due:<16} [{e['badge']}] {e['title']}{extra}")
        print(f"      id={e['id']}")
    for k, v in (d.get("errors") or {}).items():
        print(L(f"\n注意：{k} 读不到：{v}", f"\nNote: couldn't read {k}: {v}"))


def changed(r: dict) -> None:
    if "time" in (r.get("kept") or []):
        print(L("注意：Leo 自己挪过这条的时间，时间按他的，没改。", "Note: Leo moved this one himself, so its time stays as he set it."))
    cards = r.get("cards") or ([r["card"]] if r.get("card") else [])
    if not cards:
        print(L("没有变化", "No change") if not r.get("id") else r["id"])
        return
    for c in cards:
        print(L(f"改好了（改动号 {c['logId']}）：{c['title']} · {c['summary']}", f"Done (change {c['logId']}): {c['title']} · {c['summary']}"))
    if r.get("id"):
        print(f"id={r['id']}")


def main() -> None:
    p = argparse.ArgumentParser(description=description(), formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--source", help=L("你的 Agent id（默认按当前目录猜）", "your Agent id (guessed from the current directory)"))
    common = argparse.ArgumentParser(add_help=False)  # --source 写在子命令后面也认
    common.add_argument("--source", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    sub = p.add_subparsers(dest="cmd", required=True)
    add_parser = sub.add_parser

    def parser(name: str) -> argparse.ArgumentParser:
        return add_parser(name, parents=[common])
    s = parser("day")
    s.add_argument("--date")
    s.add_argument("--days", type=int, default=1)
    s.add_argument("--json", action="store_true")
    s = parser("remember")
    s.add_argument("--all", action="store_true")
    s.add_argument("--json", action="store_true")
    s = parser("log")
    s.add_argument("--limit", type=int, default=15)
    s = parser("add")
    s.add_argument("--title", required=True)
    s.add_argument("--date", required=True)
    s.add_argument("--start")
    s.add_argument("--end")
    s.add_argument("--location", default="")
    s.add_argument("--note", default="")
    s.add_argument("--deadline", action="store_true")
    s.add_argument("--key")
    s = parser("update")
    s.add_argument("id")
    for f in ("--title", "--date", "--start", "--end", "--location", "--note"):
        s.add_argument(f)
    s.add_argument("--all-day", action="store_true")
    s = parser("delete")
    s.add_argument("id")
    s = parser("skip")
    s.add_argument("id")
    s.add_argument("--every-week", action="store_true")
    s.add_argument("--undo", action="store_true")
    s = parser("attend")
    s.add_argument("id")
    s.add_argument("answer", choices=["yes", "no", "unknown"])
    s.add_argument("--actual", help="HH:MM-HH:MM")
    s = parser("place")
    s.add_argument("id")
    s.add_argument("--location")
    s.add_argument("--note")
    s = parser("done")
    s.add_argument("id")
    s.add_argument("--undo", action="store_true")
    s = parser("edit")
    s.add_argument("id")
    s.add_argument("--title")
    s.add_argument("--due")
    s.add_argument("--no-due", action="store_true")
    s.add_argument("--detail")
    s.add_argument("--type", choices=["todo", "money", "status", "security"])
    s = parser("undo")
    s.add_argument("log", type=int)
    s.add_argument("--redo", action="store_true")
    a = p.parse_args()
    src = a.source or guess_source()

    if a.cmd == "day":
        d = call("GET", f"/api/schedule?from={parse_day(a.date)}&days={max(1, min(a.days, 14))}")
        if a.json:
            print(json.dumps(d, ensure_ascii=False, indent=1))
        else:
            show_day(d)
    elif a.cmd == "remember":
        d = call("GET", f"/api/remember?all={1 if a.all else 0}")
        if a.json:
            print(json.dumps(d, ensure_ascii=False, indent=1))
        else:
            show_remember(d)
    elif a.cmd == "log":
        for c in call("GET", f"/api/schedule/log?limit={a.limit}")["changes"]:
            print(f"{c['logId']:>5}  {c['createdAt'][5:16].replace('T', ' ')}  {c['actor']:<8} {c['title']} · {c['summary']}"
                  + (L("（已撤销）", " (undone)") if c["status"] == "undone" else ""))
    elif a.cmd == "add":
        changed(call("POST", "/api/schedule", {"title": a.title, "date": parse_day(a.date), "start": a.start, "end": a.end,
                                               "location": a.location, "note": a.note, "kind": "deadline" if a.deadline else "event",
                                               "key": a.key, "source": src}))
    elif a.cmd == "update":
        body: dict = {"source": src}
        for k in ("title", "location", "note", "start", "end"):
            if getattr(a, k) is not None:
                body[k] = getattr(a, k)
        if a.date:
            body["date"] = parse_day(a.date)
        if a.all_day:
            body["start"] = ""
        changed(call("PATCH", "/api/schedule/" + urllib.parse.quote(a.id, safe=":"), body))
    elif a.cmd == "delete":
        changed(call("DELETE", "/api/schedule/" + urllib.parse.quote(a.id, safe=":") + f"?source={urllib.parse.quote(src)}"))
    elif a.cmd == "skip":
        changed(call("POST", "/api/schedule/mark", {"ref": a.id, "skip": not a.undo, "series": a.every_week, "source": src}))
    elif a.cmd == "attend":
        body = {"source": src, "attended": {"yes": True, "no": False, "unknown": None}[a.answer]}
        if a.actual:
            m = re.fullmatch(r"\s*(\d{1,2}:\d{2})\s*[-–~]\s*(\d{1,2}:\d{2})\s*", a.actual)
            if not m:
                sys.exit(L("--actual 写成 17:40-18:35", "--actual looks like 17:40-18:35"))
            body.update(actualStart=m.group(1), actualEnd=m.group(2))
        if a.id.startswith("item:"):
            changed(call("PATCH", "/api/schedule/" + urllib.parse.quote(a.id, safe=":"), body))
        else:
            changed(call("POST", "/api/schedule/mark", {"ref": a.id, **body}))
    elif a.cmd == "place":
        body = {"ref": a.id, "source": src}
        if a.location is not None:
            body["location"] = a.location
        if a.note is not None:
            body["note"] = a.note
        changed(call("POST", "/api/schedule/mark", body))
    elif a.cmd == "done":
        changed(call("POST", "/api/remember/done", {"ref": a.id, "done": not a.undo, "source": src}))
    elif a.cmd == "edit":
        body = {"ref": a.id, "source": src}
        for k in ("title", "detail", "type"):
            if getattr(a, k) is not None:
                body[k] = getattr(a, k)
        if a.no_due:
            body["due"] = None
        elif a.due:
            body["due"] = a.due.strip()
        changed(call("POST", "/api/remember/edit", body))
    elif a.cmd == "undo":
        r = call("POST", f"/api/schedule/undo/{a.log}", {"redo": a.redo})
        c = r["card"]
        print(L(f"{'做回来了' if a.redo else '撤销了'}：{c['title']} · {c['summary']}", f"{'Redone' if a.redo else 'Undone'}: {c['title']} · {c['summary']}"))


if __name__ == "__main__":
    main()
