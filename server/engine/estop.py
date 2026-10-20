"""急停（第 4 批，7.21）：一个开关停掉所有自动的事。照 Hermes 的 hermes pause：一个哨兵文件，停的是新的活，在跑的默认不杀；
文件写坏了、读不了也算停着（宁可多停）。is_engaged / engage / get_state 那几处改写自 NousResearch/hermes-agent commit f4ac0be 的
agent/estop.py（它又是从 gastownhall/gastown 的 estop.go 移过来的），MIT，Copyright (c) 2025 Nous Research；许可全文见仓库根目录的
THIRD_PARTY_NOTICES。

状态：<data_dir>/ESTOP，一个小 JSON（{"reason", "since", "by", "stop_running", "id", "stopped_at"}），0600、先写临时文件再换名；
  重启照样停着；不写 server.json。有这个文件 = 停着；空的、写坏了、读不了也算停着（手动 touch 一下也能停）。
停着的时候：
  定时任务、心跳到点不跑（engine/jobs.py、heartbeat.py）：运行记录记一条 estop（和任务自己的 paused 是两回事），不算失败、不累计连错；
    一次的那种记成错过（和每日上限一个规矩）；挂在任务上的 watch、pre 跟着一起不跑
  这一批的触发不进：webhook 回 503 + Retry-After（engine/webhooks.py），盯邮箱的不去拉（engine/mailwatch.py）
  新的后台任务不派（engine/tasks.py 的 create：tasks 工具回一句为什么；/api/engine/bg 回 503）；工作流不往下走（engine/flows.py）
  别的模块的后台不跑：写回（engine/writeback.py）、技能回看（engine/skill_write.py）、向量补齐（engine/semantic.py）
  Agent 自己用 cron 工具手动跑一次也不行；你在 app 里点「跑一次」、点收件箱的卡，照常（那是你自己的动作）
  主人自己在 app 里说话照常。渠道的自动回复（第 6 批的 server/channels/）看 paused() 自己接
在跑的不杀，除非 pause(stop_running=True)：那时停掉正在跑的定时任务、心跳、后台任务、工作流那一步，和对话里自动开的那几轮（origin auto：
  定时任务和心跳的 chat 模式、触发开的回合、收件箱让 Agent 接着做的）；像点了停，记成 interrupted，不算失败。你自己在 app 里说的那一轮不停。
  命令行（../estop_ctl.py）只写文件：服务器那边下一次看（engine/jobs.py 的 loop，≤30 秒）照做一次（enforce；做过了记 stopped_at，不重复）。
接口：GET / POST /api/engine/pause {"reason"?, "stop_running"?}、POST /api/engine/resume（只认主人的设备，照 setup_api.owner）；
  /api/engine/status 多一个 paused。命令行：mousse pause [--stop-running] [--reason …]、mousse resume。
"""
from __future__ import annotations

import contextlib
import json
import os
import threading
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Request

from config import settings
from i18n import L

NAME = "ESTOP"
RETRY_AFTER = 300           # webhook 停着时回的 Retry-After（秒）：外面的服务过一会儿再投
REASON_MAX = 200
_logged: set[str] = set()   # 这一次停着已经在日志里说过的部件（每次停只说一次；恢复了清掉）
_lock = threading.Lock()


def path() -> Path:
    return settings.data_dir / NAME


def state() -> dict | None:
    """停着 → {"reason", "since", "by", "stop_running", "id", "stopped_at"}（文件坏了、读不了的那几项是 None）；没停 → None。"""
    p = path()
    try:
        if not p.exists():
            return None
    except OSError:  # 连有没有都看不了：照 Hermes，算停着
        return _blank()
    try:
        raw = json.loads(p.read_text(encoding="utf-8-sig") or "null")
    except (OSError, ValueError):
        return _blank()
    if not isinstance(raw, dict):
        return _blank()
    return {**_blank(), **{k: raw.get(k) for k in ("reason", "since", "by", "stop_running", "id", "stopped_at")},
            "stop_running": raw.get("stop_running") is True}


def _blank() -> dict:
    return {"reason": None, "since": None, "by": None, "stop_running": False, "id": None, "stopped_at": None}


def paused() -> bool:
    """停着吗。每次现看文件（一两次 stat，不缓存）：命令行写的、手动 touch 的马上生效。"""
    on = state() is not None
    if not on and _logged:
        with _lock:
            _logged.clear()
    return on


def reason() -> str | None:
    s = state()
    return (s or {}).get("reason") or None


def _write(data: dict) -> None:
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{NAME}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False, indent=1) + "\n")
        tmp.replace(p)
    except OSError:
        with contextlib.suppress(OSError):
            tmp.unlink()
        with contextlib.suppress(OSError):  # 写不进去也要停住：一个空文件照样算停着（fail safe）
            p.touch(exist_ok=True)


def pause(why: str | None = None, *, stop_running: bool = False, by: str = "app") -> dict:
    """停。已经停着：换上新的理由；stop_running 记下来（服务器那边 enforce 一次）。since 留第一次停的时刻。→ state()。"""
    cur = state() or {}
    data = {"reason": " ".join(str(why or "").split())[:REASON_MAX] or None, "since": cur.get("since") or datetime.now(settings.tz).isoformat(timespec="seconds"),
            "by": by, "stop_running": bool(stop_running), "id": uuid.uuid4().hex[:12], "stopped_at": None}
    _write(data)
    return state() or data


def resume() -> bool:
    """恢复：去掉文件。→ 刚才停着没有。"""
    p = path()
    try:
        p.unlink()
        lifted = True
    except FileNotFoundError:
        lifted = False
    except OSError:
        return False
    with _lock:
        _logged.clear()
    return lifted


def note(component: str) -> bool:
    """部件到点了、因为停着没做：每次停只在日志里说一次（照 Hermes 的 check_paused）。→ paused()。"""
    if not paused():
        return False
    with _lock:
        first = component not in _logged
        _logged.add(component)
    if first:
        r = reason()
        print(f"[engine] 急停中：{component} 暂停" + (f"（{r}）" if r else "") + "；恢复：mousse resume 或 POST /api/engine/resume")
    return True


def notice() -> str:
    """给模型、给人看的一句：停着、为什么、怎么恢复。"""
    r = reason()
    return L("自动的事都暂停了（急停" + (f"：{r}" if r else "") + "）。定时任务、触发、后台任务都不跑；主人在 app 里恢复（或者命令行 mousse resume）才接着来。",
             "Automation is paused (emergency stop" + (f": {r}" if r else "") + "). Scheduled jobs, triggers and background tasks don't run until "
             "the owner resumes it in the app (or with mousse resume).")


def view() -> dict:
    s = state()
    if s is None:
        return {"paused": False}
    return {"paused": True, "reason": s["reason"], "since": s["since"], "by": s["by"], "stopRunning": s["stop_running"], "stoppedAt": s["stopped_at"]}


# —— stop_running：停掉正在跑的 ————————————————————————————————————————————————————————

async def enforce() -> int:
    """停着、说了 stop_running、这一次还没照做过 → 停掉正在跑的（见文件开头），记下 stopped_at。→ 停了几个。"""
    s = state()
    if s is None or not s.get("stop_running") or s.get("stopped_at") or not s.get("id"):
        return 0
    n = await stop_running()
    cur = state()
    if cur is not None and cur.get("id") == s["id"]:  # 这中间又换了一次（又停了一次、恢复了）：别把新的盖掉
        _write({**{k: cur[k] for k in ("reason", "since", "by", "stop_running", "id")}, "stopped_at": datetime.now(settings.tz).isoformat(timespec="seconds")})
    return n


async def stop_running() -> int:
    """停掉正在跑的自动的事（不管停没停着）：定时任务、心跳、后台任务、工作流、转了后台的生成视频 / 音乐、对话里 origin auto 的那几轮。
    → 停了几个。"""
    import chat

    from . import heartbeat, jobs, tasks
    n = 0
    for jid, t in list(jobs._running.items()):
        if not t.done():
            t.cancel()  # jobs._run 收到取消：记成 interrupted（不算失败）
            n += 1
    for agent, t in list(heartbeat._running.items()):
        if not t.done():
            t.cancel()
            n += 1
    for tid in tasks.running_ids():
        try:
            await tasks.stop(tid, by="user")  # 像主人自己点了停：结果不另开一轮，只在会话里补一句
            n += 1
        except Exception as e:  # noqa: BLE001 — 一个停不下来不影响别的
            print(f"[engine] 急停没停下后台任务 {tid}：{type(e).__name__}: {str(e)[:120]}")
    with contextlib.suppress(ImportError):
        from . import flows
        n += await flows.stop_running()
    from . import mediagen
    n += await mediagen.stop_all()  # 转了后台的生成视频、音乐（第 3 批）：视频一秒一毛钱上下，别让它接着花
    for thread, run in list(chat.RUNS.items()):
        if not run.done and run.origin == "auto":
            await chat.stop(chat.StopBody(thread=thread))
            n += 1
    if n:
        chat.log_activity(L(f"急停：停掉了 {n} 件正在跑的自动的事", f"Emergency stop: stopped {n} automatic run(s) in progress"), "edit")
    return n


# —— 接口（只认主人的设备）——————————————————————————————————————————————————————————————

def _owner(request: Request) -> str:
    """只认主人的设备（setup_api.owner；用到时才导入：命令行 ../estop_ctl.py 只要上面那几个函数）。"""
    from . import setup_api
    return setup_api.owner(request)


router = APIRouter(dependencies=[Depends(_owner)])


@router.get("/api/engine/pause")
def api_state():
    return {"ok": True, **view()}


@router.post("/api/engine/pause")
async def api_pause(request: Request):
    """{"reason"?: 一句为什么, "stop_running"?: true = 正在跑的也停掉}。已经停着：换上新的理由。"""
    import chat

    from . import jobs, setup_api
    b = await setup_api.body_of(request)
    why = setup_api._str(b, "reason", REASON_MAX)
    stop = b.get("stop_running") is True
    was = paused()
    pause(why, stop_running=stop, by="app")
    stopped = await enforce() if stop else 0
    if not was:
        chat.log_activity(L("急停：自动的事都暂停了" + (f"（{why}）" if why else ""), "Emergency stop: automation paused" + (f" ({why})" if why else "")), "edit")
    jobs.wake()
    return {"ok": True, **view(), "stopped": stopped}


@router.post("/api/engine/resume")
async def api_resume():
    import chat

    from . import jobs
    lifted = resume()
    if lifted:
        chat.log_activity(L("急停解除：自动的事接着来", "Emergency stop lifted: automation resumes"), "edit")
    jobs.wake()
    return {"ok": True, "paused": False, "lifted": lifted}
