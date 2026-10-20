"""会话换新（第 2 批，10/19）：一个对话的会话什么时候换一条新的。

  每天   逻辑日从 04:00 开始（../chat.py 的 DAY_START_HOUR，蓝图 4.3），会话键 mousse:<对话>:<逻辑日>，换天就是新的一条。
         engine.reset.at = "HH:MM" 改这个钟点（只在 builtin；OpenClaw 那条路照旧 04:00）：chat.day_of / day_bounds（app 按天的记录、
         会话键、上次聊到哪、日结）、记忆检索的按天（recall.day_of）、用了哪条的按天（used.day_of）、赛博的按天（cyber.logical_day）
         都问 day_start()。app 里写着的「04:00 起」那几个字（ChatView）照旧，等设计。
  闲置   engine.reset.idle_minutes = N：这个对话的会话 N 分钟没动静（engine_sessions.updated_at：说话、回复、补进来的都算），下一句话
         进来时先换一条新的（owner.open_day：同一个键、新的一条，DbSession.latest 认它，老的拆法照样认），开头带上一条聊到哪（owner.recap）。
         没有计时器、不在后台写东西：等下一句话进来才发现（OpenClaw 的 idle 重置、Hermes 的 session-reset 插件都是这样）。
  手动   /new [要点]（engine/commands.py → owner.start_new）。
不写 engine.reset = 照旧：04:00 换天、不按闲置换。
借（10/19 读的，只借设计）：OpenClaw docs/concepts/session.md 和 src/config/sessions/reset-policy.ts（reset.atHour 默认 4、idleMinutes
默认 0 = 不按闲置、下一句话进来才查）；Hermes hermes_cli/session_reset_retirement.py 和 hermes-session-reset-policy 插件（at_hour、
idle_minutes，第一句越过边界的话进来时换新）。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from config import raw, settings

DAY_START = timedelta(hours=4)   # 同 chat.DAY_START_HOUR（这里不导入 chat：recall、used 这些也要用，命令行里也是）
AT = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def cfg() -> dict:
    e = raw().get("engine")
    r = e.get("reset") if isinstance(e, dict) else None
    return r if isinstance(r, dict) else {}


def _builtin() -> bool:
    c = raw().get("claw")
    return isinstance(c, dict) and str(c.get("kind") or "").strip().lower() == "builtin"


def at() -> timedelta | None:
    """engine.reset.at（"HH:MM"）离零点多久；没写、写错了、不是 builtin → None。"""
    m = AT.match(str(cfg().get("at") or "").strip())
    if not m or not _builtin():
        return None
    return timedelta(hours=int(m.group(1)), minutes=int(m.group(2)))


def day_start() -> timedelta:
    """逻辑日从几点开始（离零点多久）：engine.reset.at，不写 = 04:00。"""
    a = at()
    return DAY_START if a is None else a  # "00:00" 是 timedelta(0)，不能用 or


def idle_minutes() -> int | None:
    v = cfg().get("idle_minutes")
    return v if isinstance(v, int) and not isinstance(v, bool) and v > 0 and _builtin() else None


def stale(updated_at: str | None, now: datetime | None = None) -> bool:
    """这条会话闲太久了吗（engine.reset.idle_minutes；没开 → False）。"""
    n = idle_minutes()
    if n is None or not updated_at:
        return False
    try:
        t = datetime.fromisoformat(str(updated_at))
    except ValueError:
        return False
    if t.tzinfo is None:
        t = t.replace(tzinfo=settings.tz)
    return (now or datetime.now(settings.tz)) - t >= timedelta(minutes=n)
