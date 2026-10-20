#!/usr/bin/env python3
"""急停的命令行（第 4 批，engine/estop.py）：mousse pause / mousse resume 的实现（mousse.py 只登记这两个子命令）。

  mousse pause [--reason 一句为什么] [--stop-running]   停掉所有自动的事：定时任务、心跳、触发、新的后台任务、工作流、写回和回看
  mousse resume                                       恢复，下一次到点照常跑

只写 <data_dir>/ESTOP 这个文件，不经服务器的接口（服务器停着也能停；在服务器上能敲命令的就是主人）：服务器每次现看，马上生效。
--stop-running：正在跑的也停掉（不写 = 在跑的跑完）；服务器那边下一次看（≤30 秒）照做一次。主人自己在 app 里说话照常。
退出码：0 做了；1 写不了文件。
"""
from __future__ import annotations

import argparse

from i18n import L


def register(sub) -> None:
    """mousse.py 的 main() 登记子命令：pause、resume。"""
    p = sub.add_parser("pause", help=L("急停：停掉所有自动的事（定时任务、触发、后台任务……）", "Emergency stop: pause all automation (jobs, triggers, background tasks…)"))
    p.add_argument("--reason", help=L("一句为什么（app 和通知里看得到）", "One line on why (shown in the app)"))
    p.add_argument("--stop-running", action="store_true", help=L("正在跑的也停掉（不写 = 在跑的跑完）", "Also stop what's running now (default: let it finish)"))
    p.set_defaults(run=pause)
    r = sub.add_parser("resume", help=L("急停解除：自动的事接着来", "Lift the emergency stop: automation resumes"))
    r.set_defaults(run=resume)


def pause(a: argparse.Namespace) -> int:
    from engine import estop
    was = estop.paused()
    s = estop.pause(a.reason, stop_running=a.stop_running, by="cli")
    if not estop.paused():
        print(L(f"没停上：写不了 {estop.path()}", f"Couldn't pause: can't write {estop.path()}"))
        return 1
    why = s.get("reason")
    print(L("还停着" if was else "停了", "Still paused" if was else "Paused") + (L(f"（{why}）", f" ({why})") if why else ""))
    print(L(f"  文件：{estop.path()}", f"  file: {estop.path()}"))
    print(L("  定时任务、心跳、触发、新的后台任务、工作流、写回和回看都不跑；你自己在 app 里说话照常。",
            "  Scheduled jobs, heartbeats, triggers, new background tasks, flows, write-back and reviews are on hold; chatting in the app works as usual."))
    print(L("  正在跑的：服务器下一次看（30 秒内）就停掉。" if a.stop_running else "  正在跑的跑完（要一起停：--stop-running）。",
            "  What's running now is stopped within 30 seconds." if a.stop_running else "  What's running now finishes (add --stop-running to stop it too)."))
    print(L("  恢复：mousse resume", "  Resume with: mousse resume"))
    return 0


def resume(a: argparse.Namespace) -> int:
    from engine import estop
    if estop.resume():
        print(L("恢复了：下一次到点照常跑。", "Resumed: things run again at their next time."))
    else:
        print(L(f"本来就没停着（{estop.path()} 不在）。", f"It wasn't paused (no {estop.path()})."))
    return 0
