"""重启后被打断的那一轮接着跑一次（第 2 批，10/19，engine.loop.resume，默认关）。

以前（../chat.py 的 resume_engine，现在照旧）：重启打断的一轮只收尾：会话补完整（没结果的工具调用各补一条「可能做了、可能没做」，
  再补一句 RESTART_NOTE），app 那句补一条「没拿到回复」；这一轮不重跑。
开了以后，收完尾再替用户接着说一句（照 OpenClaw docs/gateway/restart-recovery.md 的自动续上，只借设计）：
  在那个对话里开一轮（origin auto，app 里是一行灰字「服务重启了，接着做刚才被打断的那一轮」），交给模型的那句是 PROMPT：上一轮
  被重启打断了、用户没看到后面；没结果的调用（会话里写着 RESTART_NO_RESULT）可能做了也可能没做，先看现在的样子再决定重做还是问用户，
  撤不回的不要没经同意再做一遍。工具的关卡照旧（撤不回的照样交给用户点头），没人在看：死循环检测按无人值守算（engine/guard.py）。
  Claude Code 跑的对话：这一轮照常交给它，同一天的会话 --resume 接着说（claude_brain.plan）。
  三种打断都接：进程被杀（还是 running，loop.recover）、关服务时被取消（events.cancelled）——这两种 app 那句补了「没拿到回复」
  （settle_restarted）；正常关服务时像点了停那样停下的（stop_engine_runs：回复末尾写着「服务重启了」，../chat.py 的
  resume_shutdown_stopped 找，还得是这个对话的最后一条）。
  哪些接（wanted）：主人回合、在 app 的对话里、用户自己说的或者转过来有人等着回答的（origin user / relay）、被打断不到 within_minutes
  分钟（默认 30，同排着的消息；被杀、被取消的按这一轮开始的时候算）。只接一次：接着跑的那一轮 origin 是 auto，它再被打断不再接；
  定时任务、心跳、后台任务的回合（auto、task）归它们自己（第 4 批）。用户点了停的不接（回复已经入库，「停了」）。接不上（这个对话又在跑了、
  起不来）照旧只留「没拿到回复」。只在 builtin、服务里（有事件循环）。
server.json：engine.loop.resume = true 或 {"within_minutes": 30}。
"""
from __future__ import annotations

from datetime import datetime, timezone

from i18n import L

from .context import AUTO_TRIGGER

WITHIN_MINUTES = 30
ORIGINS = frozenset({"user", "relay"})
SHOWN = (f"{AUTO_TRIGGER}服务重启了，接着做刚才被打断的那一轮", f"{AUTO_TRIGGER}The server restarted; picking up the turn it cut off")
PROMPT = ("[Automatic, not from the user] The server restarted while you were in the middle of the previous turn, and the user did not get "
          "the rest of your reply. Pick it up once from where it stopped, using what you already said and did above. Tool calls whose result "
          "says the server restarted may or may not have finished: check the current state before redoing any of them, and don't repeat "
          "anything that can't be undone without the user's OK. If you can't tell what state things are in, say so and ask the user instead "
          "of guessing. If the work was already done, just give the user the rest of your answer.")


def cfg() -> dict | None:
    """engine.loop.resume：true 或 {…}；不写 / false = 不接（None）。"""
    from . import models
    lp = models.cfg().get("loop")
    v = lp.get("resume") if isinstance(lp, dict) else None
    if v is True:
        return {}
    return v if isinstance(v, dict) and v.get("enabled", True) is not False else None


def within() -> float:
    v = (cfg() or {}).get("within_minutes")
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 else float(WITHIN_MINUTES)


def wanted(t: dict, now: datetime | None = None) -> bool:
    """这一轮（events 表里的一行，resume_engine 收完尾、app 那句补了「没拿到回复」的）要不要接着跑一次。"""
    if cfg() is None or t.get("kind") != "owner" or not t.get("session") or t.get("origin") not in ORIGINS:
        return False
    try:
        started = datetime.fromisoformat(str(t.get("started_at")))
    except (TypeError, ValueError):
        return False
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    return ((now or datetime.now(timezone.utc)) - started).total_seconds() <= within() * 60


def shown() -> str:
    return L(*SHOWN)
