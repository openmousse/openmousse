"""后台跑这句（第 4 批，7.13）：POST /api/engine/bg {thread, text, label?, minutes?, model?}：把你在这个对话里说的一句当成一个后台任务派出去，
主对话照常聊。走的是 tasks 工具派任务的那条路（engine/tasks.py 的 pick_runner、create、start）：一样有台账、任务页、能「改一下」，
做完结果回到这个对话（它在那个对话里说一轮人话）。是你自己要的：不问额度（不出那张「多派一个后台任务」的卡，和任务页的重派一样）；
单个最长照 tasks.max_minutes；急停着、今天的花费到了上限照样不派（tasks.create 挡，回 409 说为什么）。只认你自己的设备
（app 的令牌或者 Tailscale 设备，engine/setup_api.owner）。
斜杠命令 /bg（engine/commands.py 的 _bg）调的也是这里的 start()。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from i18n import L

from . import tasks, unattended
from .loop import ToolFailure

TEXT_MAX = 8000


async def start(thread: str, text: str, *, label: str | None = None, minutes: int | None = None, model: str | None = None) -> dict:
    """派出去 → tasks.accepted 那个样子（id、label、谁来跑、最长几分钟）。不许、跑不了抛 ToolFailure（一句给人看的话）。"""
    import claw

    from .owner import agent_of
    if not claw.is_builtin():
        raise ToolFailure(L("后台跑这句只在自己的内核下有（claw 是 builtin）", "Running a message in the background needs the built-in engine"))
    text = (text or "").strip()
    if not text:
        raise ToolFailure(L("要写 text：要在后台做什么", "text is required: what to do in the background"))
    if len(text) > TEXT_MAX:
        raise ToolFailure(L(f"text 最多 {TEXT_MAX} 字", f"text is at most {TEXT_MAX} characters"))
    if thread.startswith(("cron:", "heartbeat:", "task:")) or not unattended.thread_exists(thread):
        raise ToolFailure(L(f"没有「{thread}」这个对话", f"No chat called {thread}"))
    runner, m, picked = tasks.pick_runner("", (model or "").strip(), thread, None)
    _, max_min = tasks.limits()
    try:
        n = int(minutes or max_min)
    except (TypeError, ValueError):
        n = max_min
    params = {"task": text, "label": tasks.label_of(label, text), "runner": runner, "model": m, "minutes": max(1, min(n, max_min))}
    row = tasks.create(thread=thread, agent=agent_of(thread), params=params, depth=1)
    tasks.start(row["id"])
    out = tasks.accepted(row)
    if picked:
        out["note"] = picked
    return out


def _owner(request: Request) -> str:
    from . import setup_api
    return setup_api.owner(request)


router = APIRouter(dependencies=[Depends(_owner)])


@router.post("/api/engine/bg")
async def api_bg(request: Request):
    from . import setup_api
    b: dict[str, Any] = await setup_api.body_of(request)
    try:
        out = await start(str(b.get("thread") or "main"), str(b.get("text") or ""), label=b.get("label") if isinstance(b.get("label"), str) else None,
                          minutes=b.get("minutes") if isinstance(b.get("minutes"), int) else None,
                          model=b.get("model") if isinstance(b.get("model"), str) else None)
    except ToolFailure as e:
        raise HTTPException(409, str(e)) from None
    return {"ok": True, "task": out}
