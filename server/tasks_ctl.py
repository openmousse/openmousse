#!/usr/bin/env python3
"""后台任务的额度（给 Agent 用）：派子会话（sessions_spawn）之前先看今天还能派几个。走服务的 HTTP 接口（/api/tasks/quota、/api/tasks）。

  python3 tasks_ctl.py quota          # 今天派了几个、上限、还能派几个、单个最长几分钟。额度用完了退出码 3：这时要派就先交收件箱（inbox_ctl.py add --kind task）
  python3 tasks_ctl.py list [--json]  # 最近的后台任务：状态、标题、谁派的、用的模型、多久

额度在 server.json 的 tasks（daily_limit，默认 10；max_minutes，默认 30）。「今天」从 04:00 算起。
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

from config import settings
from i18n import L, lang


def description() -> str:
    """--help 的说明，按 server.json 的 language。"""
    return L(__doc__, """Background-task allowance (for agents): check how many sub-sessions (sessions_spawn) you can still start today.
Goes through the server's HTTP API (/api/tasks/quota, /api/tasks).

  python3 tasks_ctl.py quota          # started today, the limit, how many are left, max minutes per task. Exit code 3 when the allowance
                                      # is used up: to start one anyway, submit it to the inbox first (inbox_ctl.py add --kind task)
  python3 tasks_ctl.py list [--json]  # recent background tasks: status, title, who started it, model, duration

The limits live in server.json under tasks (daily_limit, default 10; max_minutes, default 30). "Today" starts at 04:00.
""")


def call(path: str) -> dict:
    url = f"http://{settings.host}:{settings.port}{path}"
    headers = {"Accept": "application/json", "Accept-Language": "zh-CN" if lang() == "zh" else "en"}
    tokens = settings.tokens()
    if tokens:  # 本机跑，用第一个令牌；没令牌时靠 Tailscale 白名单 / trust_loopback
        headers["Authorization"] = f"Bearer {next(iter(tokens.values()))}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as r:  # noqa: S310 — 本机服务
            return json.loads(r.read().decode("utf8"))
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode("utf8")).get("detail")
        except ValueError:
            msg = None
        sys.exit(L(f"失败（HTTP {e.code}）：{msg or e}", f"Failed (HTTP {e.code}): {msg or e}"))
    except (urllib.error.URLError, TimeoutError) as e:
        sys.exit(L(f"连不上服务：{e}", f"Can't reach the server: {e}"))


def quota() -> None:
    q = call("/api/tasks/quota")
    limit, minutes = q.get("limit"), q.get("maxMinutes")
    if q.get("today") is None:
        print(L(f"读不到 OpenClaw 的任务台账，数不出今天派了几个。上限：每天 {limit} 个，单个最长 {minutes} 分钟。",
                f"Can't read OpenClaw's task ledger, so today's count is unknown. Limits: {limit} a day, {minutes} minutes each."))
        return
    n, left, running = q["today"], q["left"], q.get("running") or 0
    busy = L(f"（{running} 个还在跑）", f" ({running} still running)") if running else ""
    print(L(f"今天派了 {n} 个后台任务{busy}，上限 {limit} 个，还能派 {left} 个；单个最长 {minutes} 分钟（spawn 时 runTimeoutSeconds 不超过 {minutes * 60}）。",
            f"Started {n} background task{'' if n == 1 else 's'} today{busy}; the limit is {limit}, {left} left. "
            f"Each may run up to {minutes} minutes (runTimeoutSeconds at most {minutes * 60} when you spawn)."))
    if left <= 0:
        print(L("额度用完了：要派就先交收件箱（inbox_ctl.py add --kind task），写清楚为什么值得多派一个、预计多久，等用户点「派出去」。",
                "The allowance is used up: to start one anyway, submit it to the inbox first (inbox_ctl.py add --kind task) with why it is "
                "worth it and how long it should take, and wait for the user's OK."))
        sys.exit(3)


def list_tasks(as_json: bool) -> None:
    r = call("/api/tasks")
    tasks = r.get("tasks") or []
    if as_json:
        print(json.dumps(tasks, ensure_ascii=False, indent=2))
        return
    if not tasks:
        print(L("还没有后台任务。", "No background tasks yet."))
        return
    for t in tasks[:20]:
        model = (t.get("modelId") or "").split("/")[-1] or "—"
        minutes = t.get("minutes")
        took = L(f"{minutes} 分钟", f"{minutes} min") if minutes is not None else ""
        print(f"{t['status']}\t{t['createdAt']}\t{t['title']}\t{t.get('origin')} → {model}\t{took}\t{t['id']}")


def main() -> None:
    ap = argparse.ArgumentParser(description=description(), formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("quota")
    ls = sub.add_parser("list")
    ls.add_argument("--json", action="store_true")
    a = ap.parse_args()
    if a.cmd == "quota":
        quota()
    else:
        list_tasks(a.json)


if __name__ == "__main__":
    main()
