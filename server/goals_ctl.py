#!/usr/bin/env python3
"""目标：Agent 在命令行里看、加、改。走服务的 HTTP 接口（/api/goals）。

读
  python3 goals_ctl.py list [--all] [--json]                    # 进行中的目标（--all 连完成的、不做了的），每个带 id、当前值和进度
  python3 goals_ctl.py trend [--metric weight|bodyfat] [--days 90] [--json]   # 体重 / 体脂的读数：最新、近 7 天平均、30 天变化
  python3 goals_ctl.py log [--limit N] [--goal <id>]            # 最近的改动（谁改了什么），带改动号
改（每次改动都记下来，app 目标页顶上能撤销；输出里有改动号）
  python3 goals_ctl.py add --title "体重回到 75 kg 以下" --category 健康 [--metric weight] [--low 72] [--high 75] [--unit kg]
      [--due 2026-12-31] [--detail "…"] [--agent fitness]
  python3 goals_ctl.py update <id> [--title …] [--category …] [--detail … | --no-detail] [--due … | --no-due]
      [--metric weight|bodyfat | --no-metric] [--low N | --no-low] [--high N | --no-high] [--unit …] [--agent <id> | --no-agent] [--position N]
  python3 goals_ctl.py done <id>        # 做到了
  python3 goals_ctl.py drop <id>        # 不做了（不删，app 里折在「不做了的」）
  python3 goals_ctl.py reopen <id>      # 放回进行中
  python3 goals_ctl.py undo <改动号> [--redo]

分类：健康 / 学业 / 职业 / 财务（也认 health / study / career / finance）。
--metric：服务端自动读当前值（bodyfat 体脂、weight 体重：训记为主，Apple 健康对照）；不给 = 不自动读。体脂从不自动算，只读记下的。
--low / --high：目标区间，可以只给一头（只给 --high = 降到这个以下）。--due：YYYY-MM-DD、YYYY-MM，或者「2027 秋」这样的说法。
--agent：这个目标挂在哪个 Agent 下（app 里「去看板」）。
不给 --source：当前目录在哪个 Agent 的工作区里就算它的，否则是 main。
服务地址按 server.json 的 bind（MOUSSE_SERVER_CONFIG 换一份 server.json 就指向别的服务，测试用）。
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.parse

from i18n import L
from inbox_ctl import call, guess_source

CATEGORY_EN = {"健康": "Health", "学业": "Study", "职业": "Career", "财务": "Finance"}
CATEGORIES = list(CATEGORY_EN)


def description() -> str:
    return L(__doc__, """Goals: read, add and change them from the command line. Uses the server's HTTP API (/api/goals).

Read
  python3 goals_ctl.py list [--all] [--json]                    # active goals (--all: also done / dropped), with id, current value and progress
  python3 goals_ctl.py trend [--metric weight|bodyfat] [--days 90] [--json]   # weight / body fat readings: latest, 7-day average, 30-day change
  python3 goals_ctl.py log [--limit N] [--goal <id>]            # recent changes (who changed what), with change numbers
Change (every change is logged and can be undone from the top of the app's Goals page; the output has the change number)
  python3 goals_ctl.py add --title "Back under 75 kg" --category health [--metric weight] [--low 72] [--high 75] [--unit kg]
      [--due 2026-12-31] [--detail "…"] [--agent fitness]
  python3 goals_ctl.py update <id> [--title …] [--category …] [--detail … | --no-detail] [--due … | --no-due]
      [--metric weight|bodyfat | --no-metric] [--low N | --no-low] [--high N | --no-high] [--unit …] [--agent <id> | --no-agent] [--position N]
  python3 goals_ctl.py done <id>        # reached
  python3 goals_ctl.py drop <id>        # not doing it any more (kept, folded under "Dropped" in the app)
  python3 goals_ctl.py reopen <id>      # back to active
  python3 goals_ctl.py undo <change number> [--redo]

Categories: 健康 / 学业 / 职业 / 财务 (or health / study / career / finance).
--metric: the server reads the current value itself (bodyfat, weight: the body data source first, Apple Health as a cross-check); none = no automatic reading.
Body fat is never calculated, only read from what was recorded.
--low / --high: the target range; one end is enough (only --high = get below it). --due: YYYY-MM-DD, YYYY-MM, or words like "fall 2027".
--agent: the Agent this goal belongs to ("Go to board" in the app).
Without --source: the Agent whose workspace contains the current directory, otherwise main.
The server address comes from bind in server.json (point MOUSSE_SERVER_CONFIG at another server.json to use another server, e.g. for tests).
""")


def num(v: float) -> str:
    x = round(float(v), 2)
    return str(int(x)) if x == int(x) else f"{x:.2f}".rstrip("0").rstrip(".")


def with_unit(v: float, unit: str | None) -> str:
    u = (unit or "").strip()
    return f"{num(v)}{u}" if not u or u in ("%", "‰", "°") else f"{num(v)} {u}"


def target(g: dict) -> str:
    lo, hi, u = g.get("targetLow"), g.get("targetHigh"), g.get("unit")
    if lo is not None and hi is not None:
        return with_unit(lo, u) if lo == hi else f"{num(lo)}–{with_unit(hi, u)}"
    if hi is not None:
        return L(f"{with_unit(hi, u)} 以下", f"{with_unit(hi, u)} or less")
    if lo is not None:
        return L(f"{with_unit(lo, u)} 以上", f"{with_unit(lo, u)} or more")
    return ""


def category(c: str) -> str:
    return L(c, CATEGORY_EN.get(c, c))


def agent_names() -> dict[str, str]:
    try:
        return {g["id"]: g["name"] for g in call("GET", "/api/groups").get("groups") or []}
    except SystemExit:
        return {}


def goal_lines(g: dict, names: dict[str, str]) -> list[str]:
    parts = []
    if target(g):
        parts.append(L(f"目标 {target(g)}", f"target {target(g)}"))
    if g.get("current") is not None:
        cur = with_unit(g["current"], g.get("unit"))
        where = f"{g.get('currentSource') or ''} {g.get('currentDate') or ''}".strip()
        note = L("，超过 30 天没量了", ", over 30 days old") if g.get("stale") else ""
        pct = f" · {round(g['progress'] * 100)}%" if g.get("progress") is not None and not g.get("stale") else ""
        parts.append(L(f"现在 {cur}（{where}{note}）{pct}", f"now {cur} ({where}{note}){pct}"))
    elif g.get("metric"):
        parts.append(L("还没有读数", "no reading yet"))
    if g.get("due"):
        left = g.get("daysLeft")
        parts.append(L(f"截止 {g['due']}", f"due {g['due']}") + (L(f"（还有 {left} 天）", f" ({left} days left)") if isinstance(left, int) and left >= 0 else ""))
    if g.get("groupId"):
        parts.append(names.get(g["groupId"], g["groupId"]))
    if g.get("status") == "done":
        parts.append(L("做到了", "done") + (f" {g['closedAt'][:10]}" if g.get("closedAt") else ""))
    elif g.get("status") == "dropped":
        parts.append(L("不做了", "dropped") + (f" {g['closedAt'][:10]}" if g.get("closedAt") else ""))
    lines = [f"  {g['id']:<14} {g['title']}"]
    if parts:
        lines.append(" " * 17 + " · ".join(parts))
    if g.get("detail"):
        lines.append(" " * 17 + g["detail"].replace("\n", " "))
    return lines


def show_list(d: dict, everything: bool) -> None:
    names = agent_names()
    goals = d.get("goals") or []
    if not goals:
        print(L("（还没有进行中的目标）", "(no active goals)"))
    last = None
    for g in sorted(goals, key=lambda x: CATEGORIES.index(x["category"]) if x["category"] in CATEGORIES else 9):
        if g["category"] != last:
            print(("\n" if last else "") + category(g["category"]))
            last = g["category"]
        print("\n".join(goal_lines(g, names)))
    if everything:
        for status, head in (("done", L("完成了的", "Done")), ("dropped", L("不做了的", "Dropped"))):
            rows = [g for g in d.get("closed") or [] if g.get("status") == status]
            if rows:
                print(f"\n{head}")
                for g in rows:
                    print("\n".join(goal_lines(g, names)))
    if d.get("readError"):
        print(L(f"\n注意：读数这次没读到：{d['readError']}", f"\nNote: couldn't read the numbers this time: {d['readError']}"))


def show_trend(d: dict) -> None:
    unit = d.get("unit")
    s = d.get("summary")
    print(L(f"{d.get('label')}，近 {d.get('days')} 天", f"{d.get('label')}, last {d.get('days')} days"))
    if not s:
        print(L("  还没有读数。", "  No readings yet."))
    else:
        latest = s["latest"]
        print(L(f"  最新 {with_unit(latest['value'], unit)}（{latest['date']}，{s['sourceName']}）",
                f"  Latest {with_unit(latest['value'], unit)} ({latest['date']}, {s['sourceName']})"))
        if s.get("avg7"):
            print(L(f"  近 7 天平均 {with_unit(s['avg7']['value'], unit)}（{s['avg7']['n']} 次）",
                    f"  7-day average {with_unit(s['avg7']['value'], unit)} ({s['avg7']['n']} reading{'' if s['avg7']['n'] == 1 else 's'})"))
        if s.get("change30"):
            c = s["change30"]
            sign = "+" if c["value"] > 0 else ""
            print(L(f"  30 天变化 {sign}{with_unit(c['value'], unit)}（比 {c['since']}）", f"  30-day change {sign}{with_unit(c['value'], unit)} (since {c['since']})"))
        if s.get("check"):
            k = s["check"]
            print(L(f"  对不上：{k['sourceName']}同一天是 {with_unit(k['value'], unit)}", f"  Mismatch: {k['sourceName']} says {with_unit(k['value'], unit)} that day"))
    by_src: dict[str, list[str]] = {}
    for p in d.get("series") or []:
        by_src.setdefault(p["source"], []).append(f"{p['date'][5:]} {num(p['value'])}")
    for src in d.get("sources") or []:
        pts = by_src.get(src["key"])
        if pts:
            print(L(f"  {src['name']}：", f"  {src['name']}: ") + " · ".join(pts[-12:]))
        elif src.get("error"):
            print(L(f"  {src['name']}：没读到（{src['error']}）", f"  {src['name']}: couldn't read ({src['error']})"))


def changed(r: dict) -> None:
    if not r.get("changed", True) or not r.get("logId"):
        print(L("没有变化", "No change"))
        return
    g = r.get("goal") or {}
    print(L(f"改好了（改动号 {r['logId']}）：{r.get('summary')}", f"Done (change {r['logId']}): {r.get('summary')}"))
    if g.get("id"):
        print(f"id={g['id']}")


def goal_path(gid: str) -> str:
    return "/api/goals/" + urllib.parse.quote(gid, safe="")


def main() -> None:
    p = argparse.ArgumentParser(description=description(), formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--source", help=L("你的 Agent id（默认按当前目录猜）", "your Agent id (guessed from the current directory)"))
    common = argparse.ArgumentParser(add_help=False)  # --source 写在子命令后面也认
    common.add_argument("--source", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    sub = p.add_subparsers(dest="cmd", required=True)

    def parser(name: str, text: str) -> argparse.ArgumentParser:
        return sub.add_parser(name, parents=[common], help=text)
    s = parser("list", L("看目标", "show the goals"))
    s.add_argument("--all", action="store_true", help=L("连完成的、不做了的一起", "include done and dropped ones"))
    s.add_argument("--json", action="store_true")
    s = parser("trend", L("体重 / 体脂的读数和趋势", "weight / body fat readings and trend"))
    s.add_argument("--metric", default="weight", choices=["weight", "bodyfat"])
    s.add_argument("--days", type=int, default=90)
    s.add_argument("--json", action="store_true")
    s = parser("log", L("最近的改动", "recent changes"))
    s.add_argument("--limit", type=int, default=15)
    s.add_argument("--goal")
    for name, text in (("add", L("加一个目标", "add a goal")), ("update", L("改一个目标（只改给了的）", "change a goal (only what you pass)"))):
        s = parser(name, text)
        if name == "update":
            s.add_argument("id")
        s.add_argument("--title", required=name == "add")
        s.add_argument("--category")
        s.add_argument("--detail")
        s.add_argument("--due")
        s.add_argument("--metric", choices=["weight", "bodyfat"])
        s.add_argument("--low", type=float)
        s.add_argument("--high", type=float)
        s.add_argument("--unit")
        s.add_argument("--agent", help=L("挂在哪个 Agent 下（Agent 的 id）", "the Agent this goal belongs to (its id)"))
        s.add_argument("--position", type=int)
        if name == "update":
            for flag in ("detail", "due", "metric", "low", "high", "agent"):
                s.add_argument(f"--no-{flag}", action="store_true", help=argparse.SUPPRESS)
    for name, text in (("done", L("做到了", "reached")), ("drop", L("不做了", "not doing it any more")), ("reopen", L("放回进行中", "back to active"))):
        s = parser(name, text)
        s.add_argument("id")
    s = parser("undo", L("撤销一次改动", "undo a change"))
    s.add_argument("log", type=int)
    s.add_argument("--redo", action="store_true")
    a = p.parse_args()
    src = a.source or guess_source()

    if a.cmd == "list":
        d = call("GET", "/api/goals")
        if a.json:
            print(json.dumps({k: d.get(k) for k in ("goals", "closed")} if a.all else d.get("goals"), ensure_ascii=False, indent=1))
        else:
            show_list(d, a.all)
    elif a.cmd == "trend":
        d = call("GET", f"/api/goals/trend?metric={a.metric}&days={max(7, min(a.days, 400))}")
        if a.json:
            print(json.dumps(d, ensure_ascii=False, indent=1))
        else:
            show_trend(d)
    elif a.cmd == "log":
        q = f"/api/goals/log?limit={max(1, min(a.limit, 200))}" + (f"&goal={urllib.parse.quote(a.goal)}" if a.goal else "")
        for c in call("GET", q)["changes"]:
            who = c.get("actorName") or c["actor"]
            print(f"{c['logId']:>5}  {c['at'][5:16].replace('T', ' ')}  {who:<8} {c['summary']}" + (L("（已撤销）", " (undone)") if c["status"] == "undone" else ""))
    elif a.cmd in ("add", "update"):
        body: dict = {"source": src}
        for k, key in (("title", "title"), ("category", "category"), ("detail", "detail"), ("due", "due"), ("metric", "metric"),
                       ("low", "targetLow"), ("high", "targetHigh"), ("unit", "unit"), ("agent", "groupId"), ("position", "position")):
            if getattr(a, k) is not None:
                body[key] = getattr(a, k)
        if a.cmd == "update":
            for flag, key in (("detail", "detail"), ("due", "due"), ("metric", "metric"), ("low", "targetLow"), ("high", "targetHigh"), ("agent", "groupId")):
                if getattr(a, f"no_{flag}"):
                    body[key] = None
            if len(body) == 1:
                sys.exit(L("没有要改的：给 --title / --high 这些", "Nothing to change: pass --title, --high and so on"))
            changed(call("PATCH", goal_path(a.id), body))
        else:
            r = call("POST", "/api/goals", body)
            g = r["goal"]
            print(L(f"加好了（改动号 {r['logId']}）：{g['title']}", f"Added (change {r['logId']}): {g['title']}"))
            print(f"id={g['id']}")
    elif a.cmd in ("done", "drop", "reopen"):
        status = {"done": "done", "drop": "dropped", "reopen": "active"}[a.cmd]
        changed(call("PATCH", goal_path(a.id), {"status": status, "source": src}))
    elif a.cmd == "undo":
        r = call("POST", f"/api/goals/undo/{a.log}", {"redo": a.redo, "source": src})
        c = r["change"]
        print(L(f"{'做回来了' if a.redo else '撤销了'}：{c['summary']}", f"{'Redone' if a.redo else 'Undone'}: {c['summary']}"))
        if r.get("kept"):
            print(L("（后来又改过的那几样没动）", "(fields changed again since were left as they are)"))


if __name__ == "__main__":
    main()
