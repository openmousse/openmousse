"""定时任务（内核第 6 项，claw 选 builtin 时）：Agent 定的提醒和定期要做的事，到点自己跑一轮主人回合。
10/19 起多一种只跑命令的（command）：到点跑一个命令行工具，不调模型（见下面「只跑命令的」）。

表（mousse.db）：
  engine_jobs      一条一个：id job-xxxxxxxx · agent（谁的：main 或 Agent id）· name · schedule（engine/when.py 存的样子，JSON）
                   · prompt（要做什么，到点原样交给模型；只跑命令的是那条命令的样子，给列表和卡看）
                   · command（只跑命令的：{"tool" | "script", "args", "input"?, "timeout"}，JSON；调模型的是空的）
                   · session（isolated 单独跑 / chat 在 deliver 那个对话当天的会话里跑）
                   · deliver（结果投到哪个对话：这个 Agent 自己的对话；主对话的也可以是它的项目）· level（推送档位 ring / quiet / none）
                   · times（跑几次就停，空 = 一直）· continuity（每次带上上一次发的话）· model（空 = deliver 那个对话的模型；只有用户能定）
                   · state：draft 等用户点「开」/ on 开着 / paused 停着 / done 跑完了 / missed 错过了（一次的，服务器停太久）
                     / rejected 用户没要 / withdrawn 它自己撤了 / deleted
                   · approved（用户同意过现在这一版）· pending（等点头的改动 {整版} 或删除 {"delete": true}）· inbox（等点头的那张卡）
                   · card（卡上内容的指纹：卡被改过就不照做）· digest（这一版的指纹：一模一样的再提就是同一张卡）
                   · created_by（agent:<id> / user）· next_run · last_run / last_status / last_error · runs · fails（连着出错几次）
                   · running（这一次什么时候开跑的，跑完清掉）
                   · retry（第 4 批：没跑成、等着重试的那一次 {"n" 第几次, "back" 重试完回到哪一次, "due" 本该什么时候, "hold" 挂在配额窗口之后,
                     "said" 已经说过一声, "digest" 哪一版的}；见下面「失败重试」）
                   · watch（第 4 批：盯着看 {"command" | "board" | "url": …}）· pre（调模型前先跑的命令）：见 engine/watch.py
  engine_job_runs  每跑一次一行：本该什么时候、实际开跑、按时 on_time / 晚了 late / 补跑 catch_up / 手动 manual / 重试 retry /
                   被外面的事叫起来 trigger（第 4 批：engine/triggers.py）、状态（ok / silent / error /
                   timeout / busy / interrupted / missed / skipped / limit / estop：急停着没跑，engine/estop.py / unchanged：盯着的没变 /
                   gated：pre 说这一次不叫醒）、一轮 id、发进对话的那条回复、出错、回复开头（continuity 用）、
                   output（只跑命令的：命令的输出，最多 OUTPUT_MAX 字）
  engine_job_meta  ticker 最后一次看是什么时候（服务重启后知道停了多久：停着的时候到点的一次性任务多给 2 小时）

谁能开（OpenHuman 的 tinyflows：带自动触发的一律存成停用，没有任何工具能启用；见 workspace 的 memory/context/openhuman-borrow.md）：
  Agent 新建的一律先停用（draft），交一张收件箱卡（kind schedule，app 里叫「定时任务」）：几点、做什么、在哪跑、投到哪、怎么推、一天几次、
  到点会发给它的话；用户点「开」才开（inbox.HOOKS["schedule"]，点了就生效，不用 Agent 再报 done）。
  改大的（什么时候、做什么、在哪跑、投到哪、推得更响、跑更多次）：改动先存着，原来那一版照常跑，点了「换上」才换。
  改名字、推得更轻、少跑几次、continuity、暂停、恢复用户同意过的、手动跑一次同意过的：直接做。
  删掉用户同意过的算撤不回：交一张卡问（还没开过的草稿、已经跑完的直接删）。
  用户自己在 app 里建的、改的（/api/engine/jobs，界面以后做）直接生效。
到点（tick；ticker 每 ≤30 秒看一次，有快到的就睡到那一刻；照 Hermes 的 cron/jobs.py）：
  先推进下一次再跑（advance_next_run：中途崩了重启也不会再跑一遍，宁可少一次）；同一条正在跑就不再起；同时最多跑 3 条。
  晚了多久：补跑窗口（when.grace_seconds：周期的一半，120 秒到 2 小时）以内照跑；超过了把攒下的合成一次、现在跑一次（catch_up；
  server.json engine.jobs.catch_up_missed = false 就跳过这次）；一次的晚了 120 秒以上（服务器停着时到点的 2 小时以上）记成 missed，不跑。
  isolated：新会话里跑（engine/unattended.py fresh），回 [SILENT] 就什么都不发；有话就发进 deliver（一行灰字 + 回复，按档位推送，再给那个
    对话今天的会话补一条 system：接着聊接得上）。chat：照 chat.trigger 那条路在 deliver 里跑（unattended.in_chat）。
  这一轮里不给 cron 工具（chat 模式给了也不许建、改、删、手动跑：Hermes 的规矩，免得任务里一句「每天」就再建一个）。
  看门狗：600 秒（engine.jobs.idle_timeout）没有一点动静就停。连着出错：第一次在对话里说一声，连着 5 次就停用并说一声。
  没跑成（第 4 批，见「失败重试」那一节）：临时的错（连不上、429、过载、超时，还没出字、没调工具）隔 30 秒、1 分、5 分、15 分、60 分重试
  （engine.jobs.retry），不越过下一次正常到点；配额窗口关了挂到窗口之后；永久的错不重试。重试那几次不算进「连着几次」。
  盯着看、先跑一条命令（第 4 批，engine/watch.py）：调模型的任务可以写 watch（命令的输出、看板表、网页：没变不调模型，变了才叫醒，
  网页的变化只出通知或卡）和 pre（先跑一条命令，最后一行 {"wake": false} 就不叫醒）；没叫醒的记 unchanged / gated，不算进每日上限。
  一天最多跑 engine.jobs.daily_limit 次（默认 1000，调模型的任务合计；只跑命令的不调模型，不算），到了就跳过、记一条活动。
  急停着（engine/estop.py）：到点的这一次不跑、推到下一次，记一条 estop（不算失败、不累计连错；一次的记成错过）；Agent 用 cron 工具手动跑也不行。
  今天的花费到了上限（engine/spend.py，这个任务要用的模型算钱时）：调模型的到点跳过、记 limit；跑到一半才到的这一次也记 limit，都不算失败。
  服务重启：next_run 都在库里，接着排；上次停机时正在跑的记成 interrupted，不重跑。
只在 claw.kind = builtin 时跑（每次 tick 现看）；OpenClaw 那边的 cron 一点不动。

只跑命令的（command，10/19；照 OpenClaw cron 的 command 任务：argv、不经 shell、有时限、输出记进运行记录、退出码不是 0 不马上重试）：
  跑什么：只有两种。BRIDGES 里登记过的命令行工具（board、schedule……：和这个 Agent 平时调的一样，它得有这个工具、没被
    engine.tools.levels 关掉，参数照工具的规矩查：tools.problem → bridges.guard，不许冒充别人、不许读服务器上的文件）；或者
    server.json 的 engine.jobs.scripts 里列的脚本（绝对路径的白名单，参数照 bridges.guard 查）。建、改的时候查一遍，到点再查一遍
    （白名单改了、工具关了就不跑，记一次出错）。OpenClaw 只许管理员建这种任务；这里 Agent 也能提，但和别的定时任务一样先停着、
    交卡等用户点「开」（卡上写明到点会跑的整条命令、多久停）；用户在 app 里建的直接开。
  重试（第 4 批）：脚本不一定能重跑，默认不重试；command 里写了 "retry": true（整张表）或次数才按表重试。
  怎么跑：一个子进程（bridges.execute：参数一项一个词、不经 shell，在这个 Agent 的工作区里，MOUSSE_AGENT = 它，MOUSSE_VIA = auto：
    脚本再请求服务器开的回合算后台），时限 timeout 秒（工具默认它自己的，脚本默认 SCRIPT_TIMEOUT，最多 COMMAND_TIMEOUT_MAX），
    到点连它起的子进程一起停掉、记成 timeout。退出码 0 算跑成，输出（标准输出，有错误输出的也带上）记进运行记录，不发进对话：
    要说话由脚本自己决定（比如经 /api/chat/trigger 往对话里发一句，README 有例子）。没跑成照上面的规矩：不马上重试、第一次在对话里
    说一声、连着 5 次停用。不调模型：session、continuity、model 对它没用，也不算进 daily_limit。

接口（app 以后用）：GET /api/engine/jobs、POST（用户建的，直接开）、GET / PATCH / DELETE /api/engine/jobs/{id}、
  POST /api/engine/jobs/{id}/pause | resume | run、GET /api/engine/jobs/proposal/{收件箱 id}（卡片预览）。
"""
from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import hashlib
import json
import math
import os
import re
import shlex
import sys
import uuid
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config import raw, settings
from i18n import L, LS

from . import estop, models, spend, store, unattended, when
from .loop import Tool, ToolCtx, ToolFailure
from .unattended import CRON_TOOL, Outcome

router = APIRouter()
SESSIONS = ("isolated", "chat")
LEVELS = ("ring", "quiet", "none")
LOUD = {"none": 0, "quiet": 1, "ring": 2}
LIVE = ("draft", "on", "paused")       # 还在的（算进每个 Agent 的上限）
ENDED = ("done", "missed")             # 跑完了、错过了：能改了再开，能直接删
GONE = ("rejected", "withdrawn", "deleted")
MAX_JOBS = 20                          # 每个 Agent 最多几条还在的
MAX_PARALLEL = 3
TICK_MAX = 30.0
START_DELAY = 10.0                     # 服务起来以后先等一会儿再看（别和启动时的别的事抢）
LATE_OK = 300                          # 晚了这么久以内都算按时（Hermes 的 _LATE_DISPATCH_TOLERANCE_SECONDS）
FAILS_TO_PAUSE = 5
NAME_MAX, TASK_MAX, PREVIEW_MAX = 40, 2000, 2000
DAILY_LIMIT = 1000
SCRIPT_TIMEOUT, COMMAND_TIMEOUT_MAX = 120, 600  # 只跑命令的：白名单脚本默认多久停、最多能写多久（秒）
OUTPUT_MAX = 4000                      # 运行记录里留命令的多少字输出（多了留头尾）
BACKOFF = (30, 60, 300, 900, 3600)     # 第 4 批：临时出错隔多久重试（秒；OpenClaw 的 DEFAULT_ERROR_BACKOFF_SCHEDULE_MS），engine.jobs.retry 能改
ONCE_RETRIES = 3                       # 一次的任务临时出错最多重试几次（OpenClaw 的 DEFAULT_MAX_TRANSIENT_RETRIES）
HOLD_MAX = 7 * 86400                   # 服务商说配额窗口关了：最多挂多久（秒）
OFF_SCHEDULE = ("manual", "trigger")   # 不是到点跑的：手动跑的、被外面的事叫起来的（第 4 批）：不改下一次、不重试、不算跑完

SCHEMA = """
CREATE TABLE IF NOT EXISTS engine_jobs (
  id TEXT PRIMARY KEY, agent TEXT NOT NULL, name TEXT NOT NULL, schedule TEXT NOT NULL, prompt TEXT NOT NULL,
  session TEXT NOT NULL DEFAULT 'isolated', deliver TEXT NOT NULL, level TEXT NOT NULL DEFAULT 'quiet', times INTEGER,
  continuity INTEGER NOT NULL DEFAULT 0, model TEXT, state TEXT NOT NULL, approved INTEGER NOT NULL DEFAULT 0, pending TEXT,
  inbox TEXT, card TEXT, digest TEXT, created_by TEXT NOT NULL, next_run TEXT, last_run TEXT, last_status TEXT, last_error TEXT,
  runs INTEGER NOT NULL DEFAULT 0, fails INTEGER NOT NULL DEFAULT 0, running TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  command TEXT);
CREATE INDEX IF NOT EXISTS engine_jobs_due ON engine_jobs(state, next_run);
CREATE INDEX IF NOT EXISTS engine_jobs_agent ON engine_jobs(agent, state);
CREATE INDEX IF NOT EXISTS engine_jobs_inbox ON engine_jobs(inbox);
CREATE TABLE IF NOT EXISTS engine_job_runs (
  id TEXT PRIMARY KEY, job TEXT NOT NULL, agent TEXT NOT NULL, due TEXT, started_at TEXT NOT NULL, ended_at TEXT, kind TEXT NOT NULL,
  status TEXT NOT NULL, turn TEXT, message INTEGER, error TEXT, preview TEXT, usage TEXT, output TEXT);
DROP INDEX IF EXISTS engine_job_runs_job;
CREATE INDEX IF NOT EXISTS engine_job_runs_job_at ON engine_job_runs(job, julianday(started_at));
CREATE TABLE IF NOT EXISTS engine_job_meta (key TEXT PRIMARY KEY, value TEXT);
"""
_ready: set[str] = set()
_running: dict[str, asyncio.Task] = {}     # 正在跑的：任务 id → asyncio 任务
_trigger_refs: set[int] = set()            # chat 模式开跑的那条灰字的 id：这一轮里 cron 工具不许建 / 改 / 删
_sem: tuple[Any, asyncio.Semaphore] | None = None
_wake: tuple[Any, asyncio.Event] | None = None
_bg: set[asyncio.Task] = set()
_down: dict[str, datetime | None] = {"since": None, "started": None}   # 上一个进程最后一次看的时刻、这一个进程开始看的时刻
_limit_noted: set[str] = set()
# 跑完一次以后接着做的：created_by → fn(任务, 结果)。treeprune.py 登记它那条每周修剪（13c：模型没交计划，也把「这个月没用上的」出成一张卡）
AFTER: dict[str, Any] = {}


class JobError(ValueError):
    """不许、写错了：原话回给模型（ToolFailure）或 app（400）。"""


def cfg() -> dict:
    e = raw().get("engine")
    j = e.get("jobs") if isinstance(e, dict) else None
    return j if isinstance(j, dict) else {}


def idle_timeout() -> float:
    try:
        return max(5.0, float(cfg().get("idle_timeout") or unattended.IDLE))
    except (TypeError, ValueError):
        return float(unattended.IDLE)


def catch_up_missed() -> bool:
    return cfg().get("catch_up_missed") is not False


def daily_limit() -> int:
    try:
        return max(1, int(cfg().get("daily_limit") or DAILY_LIMIT))
    except (TypeError, ValueError):
        return DAILY_LIMIT


_SPAN = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(s|sec|secs|m|min|mins|h|hr|hrs)?\s*$", re.I)


def backoff() -> list[int]:
    """调模型的任务临时出错隔多久重试（秒）：engine.jobs.retry 写 true = 照默认的 BACKOFF；写成列表（30、"30s"、"5m"、"1h"）换掉它；
    不写 / false = 不重试（默认关：重试会多花套餐的周额度、按量的钱，要主人自己开）。"""
    v = cfg().get("retry")
    if v is True:
        return list(BACKOFF)
    if not isinstance(v, list):
        return []
    out = []
    for x in v[:10]:
        m = _SPAN.match(str(x)) if not isinstance(x, bool) else None
        if m:
            n = float(m.group(1)) * {"m": 60, "min": 60, "mins": 60, "h": 3600, "hr": 3600, "hrs": 3600}.get((m.group(2) or "s").lower(), 1)
            out.append(int(min(max(n, 1), 86400)))
    return out


def ready() -> None:
    path = str(settings.db)
    if path in _ready:
        return
    with store.tx() as conn:
        conn.executescript(SCHEMA)
        for table, col in (("engine_jobs", "command"), ("engine_job_runs", "output"), ("engine_jobs", "retry"), ("engine_jobs", "watch"),
                           ("engine_jobs", "pre"), ("engine_jobs", "flow")):  # 老表：补上只跑命令的那两栏；重试、盯着看、先跑的、工作流那几栏（第 4 批）都在这里补
            if col not in {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} TEXT")
    _ready.add(path)


def exists() -> bool:
    """库里有定时任务的表吗。只读地看一眼、不建表（store.connect 会建内核的表）：claw 不是 builtin 时收件箱的钩子也会来问，
    没用过内核的库（比如一直跑 OpenClaw 的实例）什么都不碰（和第 3 项的 store.exists 一个规矩）。"""
    import sqlite3
    if str(settings.db) in _ready:
        return True
    if not settings.db.exists():
        return False
    try:
        conn = sqlite3.connect(f"file:{settings.db}?mode=ro", uri=True, timeout=10)
    except sqlite3.Error:
        return False
    try:
        return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='engine_jobs'").fetchone() is not None
    except sqlite3.Error:
        return False
    finally:
        conn.close()


iso, parse_iso = when.iso, when.parse_iso


def own_thread(agent: str) -> str:
    return "main" if agent == "main" else agent


# —— 读 ——————————————————————————————————————————————————————————————————

def _job(r) -> dict:
    d = dict(r)
    d["schedule"] = json.loads(d["schedule"])
    d["pending"] = json.loads(d["pending"]) if d.get("pending") else None
    d["command"] = json.loads(d["command"]) if d.get("command") else None
    d["retry"] = json.loads(d["retry"]) if d.get("retry") else None
    d["watch"] = json.loads(d["watch"]) if d.get("watch") else None
    d["pre"] = json.loads(d["pre"]) if d.get("pre") else None
    return d


def get(jid: str) -> dict | None:
    ready()
    with store.read() as conn:
        r = conn.execute("SELECT * FROM engine_jobs WHERE id=?", (jid,)).fetchone()
    return _job(r) if r else None


def by_inbox(iid: str) -> dict | None:
    if not exists():  # 收件箱的钩子（OpenClaw 交的 schedule 卡也会来问）：没用过内核就不是我们的卡
        return None
    ready()
    with store.read() as conn:
        r = conn.execute("SELECT * FROM engine_jobs WHERE inbox=? ORDER BY updated_at DESC LIMIT 1", (iid,)).fetchone()
    return _job(r) if r else None


def listing(agent: str | None = None, *, everything: bool = False) -> list[dict]:
    ready()
    q = "SELECT * FROM engine_jobs WHERE 1=1" + (" AND agent=?" if agent else "") + ("" if everything else " AND state NOT IN ('rejected','withdrawn','deleted')")
    with store.read() as conn:
        rows = conn.execute(q + " ORDER BY created_at", (agent,) if agent else ()).fetchall()
    return [_job(r) for r in rows]


def runs_of(jid: str, limit: int = 20) -> list[dict]:
    ready()
    with store.read() as conn:
        rows = conn.execute("SELECT * FROM engine_job_runs WHERE job=? ORDER BY julianday(started_at) DESC, rowid DESC LIMIT ?", (jid, limit)).fetchall()
    return [{**dict(r), "usage": json.loads(r["usage"] or "{}")} for r in rows]


def spec_of(j: dict) -> dict:
    return {k: j.get(k) for k in ("name", "schedule", "prompt", "session", "deliver", "level", "times", "continuity", "model", "command", "watch", "pre",
                                  "flow")}


STATE_TEXT = {"draft": ("等你点「开」", "waiting for your OK"), "on": ("开着", "on"), "paused": ("停着", "paused"), "done": ("跑完了", "finished"),
              "missed": ("错过了", "missed"), "rejected": ("你没要", "declined"), "withdrawn": ("撤回了", "withdrawn"), "deleted": ("删了", "deleted")}
LEVEL_TEXT = {"ring": ("响铃推送", "push with sound"), "quiet": ("静默推送（不响，进通知中心）", "silent push (no sound, goes to Notification Centre)"),
              "none": ("不推送，只发进对话", "no push, only posted in the chat")}


def view(j: dict) -> dict:
    """给 app 的样子。"""
    return {"id": j["id"], "agent": j["agent"], "name": j["name"], "when": when.describe(j["schedule"]), "schedule": j["schedule"],
            "task": j["prompt"], "session": j["session"], "deliver": j["deliver"], "deliverName": unattended.thread_name(j["deliver"]),
            "level": j["level"], "times": j["times"], "continuity": bool(j["continuity"]), "model": j["model"], "command": j.get("command"),
            "state": j["state"],
            "stateText": L(*STATE_TEXT.get(j["state"], (j["state"], j["state"]))), "approved": bool(j["approved"]), "pending": j["pending"],
            "inbox": j["inbox"], "createdBy": j["created_by"], "nextRun": j["next_run"], "lastRun": j["last_run"], "lastStatus": j["last_status"],
            "lastError": j["last_error"], "runs": j["runs"], "fails": j["fails"], "running": bool(j["running"]), "createdAt": j["created_at"],
            "retry": {"n": j["retry"].get("n"), "back": j["retry"].get("back")} if j.get("retry") else None,
            "watch": j.get("watch"), "pre": j.get("pre"), "flow": j.get("flow")}


def line(j: dict) -> str:
    """给模型看的一行。"""
    st = L(*STATE_TEXT.get(j["state"], (j["state"], j["state"])))
    bits = [j["id"], j["name"], when.describe(j["schedule"]), st]
    if j["state"] == "on":
        bits.append(L(f"下一次 {when.clock_text(parse_iso(j['next_run']))}", f"next {when.clock_text(parse_iso(j['next_run']))}"))
    if j.get("command"):
        bits.append(L("只跑命令，不调模型", "runs a command, no model"))
    elif j["session"] == "chat":
        bits.append(L("在对话里跑", "runs in the chat"))
    if j.get("watch"):
        from . import watch
        bits.append(L(f"盯着{watch.describe(j['watch'])}，变了才叫醒", f"watches {watch.describe(j['watch'])}, wakes only on a change"))
    if j.get("pre"):
        bits.append(L(f"先跑 {command_text(j['pre'])}", f"runs {command_text(j['pre'])} first"))
    if j["deliver"] != own_thread(j["agent"]):
        bits.append(L(f"发到「{unattended.thread_name(j['deliver'])}」", f"posts to {unattended.thread_name(j['deliver'])}"))
    if j["level"] != "quiet":
        bits.append(L(*LEVEL_TEXT[j["level"]]))
    if j["times"]:
        bits.append(L(f"跑了 {j['runs']}/{j['times']} 次", f"ran {j['runs']}/{j['times']} times"))
    if j["last_status"]:
        bits.append(L(f"上次：{j['last_status']}", f"last: {j['last_status']}") + (f"（{j['last_error'][:80]}）" if j["last_error"] else ""))
    if j["pending"]:
        bits.append(L("有一处改动等你点头", "a change is waiting for the user's OK") if not j["pending"].get("delete")
                    else L("删除等你点头", "deletion is waiting for the user's OK"))
    return " · ".join(bits) + (f"\n  command: {j['prompt'][:200]}" if j.get("command") else f"\n  task: {j['prompt'][:200]}")


# —— 写法 → 一版 ———————————————————————————————————————————————————————————

def _deliver_ok(agent: str, thread: str) -> bool:
    from .owner import agent_of
    return unattended.thread_exists(thread) and agent_of(thread) == agent


def scripts() -> list[str]:
    """server.json 的 engine.jobs.scripts：只跑命令的任务另外能跑的脚本（绝对路径，~ 展开；相对路径不认）。"""
    v = cfg().get("scripts")
    out: list[str] = []
    for x in v if isinstance(v, list) else []:
        p = os.path.expanduser(str(x).strip())
        if os.path.isabs(p):
            out.append(os.path.normpath(p))
    return out


def command_text(c: dict) -> str:
    """一条命令给人看的样子（列表、卡上）：「board rows query habits」「/srv/scripts/check.py --window 60」。"""
    return shlex.join([str(c.get("tool") or c.get("script") or ""), *[str(a) for a in c.get("args") or []]]) + (" < input" if c.get("input") else "")


def command_argv(agent: str, c: dict) -> tuple[str, list[str]]:
    """一条命令 → (参数照哪个工具的规矩查（脚本是 ""）, 要起的命令（不含 args）)。跑不了、不许抛 JobError。建、改的时候查一遍，
    到点再查一遍（白名单改了、工具关了、脚本没了就不跑）。工具：这个 Agent 有它（tools.offered：服务器上有、没被 engine.tools.levels
    整个关掉、主对话专用的只给 main），参数照它平时被调时的规矩查（tools.problem：不许冒充别人、bridges.guard），这一条没被关掉；
    脚本：在 engine.jobs.scripts 里、还在；能执行的直接起，.py 用服务器自己的 Python。"""
    import bridges

    from . import tools as tools_mod
    args = [str(a) for a in c.get("args") or []]
    if tool := c.get("tool"):
        if tool not in tools_mod.offered(agent):
            raise JobError(L(f"你没有 {tool} 这个工具（或者它在这台服务器上关掉了）", f"You don't have the {tool} tool (or it's turned off on this server)"))
        if why := tools_mod.problem(tool, args, agent):
            raise JobError(why)
        if tools_mod.classify(tool, args)[0] == tools_mod.OFF:
            raise JobError(L(f"{tool} 的这个子命令在设置里关掉了", f"This {tool} subcommand is turned off in the settings"))
        cmd = bridges.command(tool)
        if not cmd:
            raise JobError(L(f"这台服务器上没有 {tool} 这个功能", f"{tool} isn't available on this server"))
        return tool, cmd
    path = str(c.get("script") or "")
    if path not in scripts():
        raise JobError(L(f"只能跑 BRIDGES 里的工具，或者 server.json 的 engine.jobs.scripts 里列的脚本（写绝对路径）：{path} 不在里面",
                         f"Only the command-line tools, or scripts listed in server.json's engine.jobs.scripts (absolute paths), can run: "
                         f"{path} isn't listed"))
    if not os.path.isfile(path):
        raise JobError(L(f"找不到这个脚本：{path}", f"Script not found: {path}"))
    try:
        bridges.guard("", args)
    except bridges.BridgeError as e:
        raise JobError(str(e)) from None
    if os.access(path, os.X_OK):
        return "", [path]
    if path.endswith(".py"):
        return "", [sys.executable, path]
    raise JobError(L(f"{path} 不能执行：给它执行权限（chmod +x），或者写成 .py", f"{path} can't be run: make it executable (chmod +x) or use a .py script"))


def clean_command(agent: str, v: Any) -> dict:
    """写法 → 一条命令 {"tool" | "script", "args", "input"?, "timeout"}（见文件开头「只跑命令的」）。写错、不许抛 JobError。"""
    import bridges
    if not isinstance(v, dict):
        raise JobError(L('command 写成 {"tool": "board", "args": [...]} 或 {"script": "/绝对路径", "args": [...]}',
                         'command is {"tool": "board", "args": [...]} or {"script": "/absolute/path", "args": [...]}'))
    tool, script = str(v.get("tool") or "").strip(), os.path.expanduser(str(v.get("script") or "").strip())
    if bool(tool) == bool(script):
        raise JobError(L("command 里 tool 和 script 写一个", "command takes either tool or script, not both"))
    if tool and tool not in bridges.BRIDGES:
        raise JobError(L(f"没有 {tool} 这个命令行工具（能用的：{'、'.join(bridges.BRIDGES)}）",
                         f"There's no command-line tool called {tool} (the tools: {', '.join(bridges.BRIDGES)})"))
    args = v.get("args") or []
    if isinstance(args, str):
        args = shlex.split(args)
    if not isinstance(args, list) or any(isinstance(a, (dict, list)) or a is None for a in args):
        raise JobError(L("args 写成一串词（一项一个，不经 shell）", "args is a list of words (one item each, no shell)"))
    c: dict = {"tool": tool} if tool else {"script": os.path.normpath(script) if os.path.isabs(script) else script}
    c["args"] = [str(a) for a in args]
    if v.get("input") not in (None, ""):
        c["input"] = str(v["input"])
    t = v.get("timeout")
    try:
        c["timeout"] = (bridges.BRIDGES[tool][3] if tool else SCRIPT_TIMEOUT) if t in (None, "") else int(t)
    except (TypeError, ValueError):
        raise JobError(L("timeout 写秒数", "timeout is a number of seconds")) from None
    if not 1 <= c["timeout"] <= COMMAND_TIMEOUT_MAX:
        raise JobError(L(f"timeout 在 1 到 {COMMAND_TIMEOUT_MAX} 秒之间", f"timeout is between 1 and {COMMAND_TIMEOUT_MAX} seconds"))
    r = v.get("retry")
    if r not in (None, False, 0, ""):  # 第 4 批：没跑成要不要按退避重试（脚本不一定能重跑：写了才重试）；true = 整张表，数字 = 最多几次
        if r is not True and (isinstance(r, bool) or not isinstance(r, int) or not 1 <= r <= len(BACKOFF)):
            raise JobError(L(f"retry 写 true（按 30 秒、1 分、5 分……重试）或者 1 到 {len(BACKOFF)} 的次数", f"retry is true (back off 30s, 1m, 5m…) "
                             f"or a number of retries from 1 to {len(BACKOFF)}"))
        c["retry"] = r
    if sum(len(a) for a in c["args"]) + len(c.get("input") or "") > bridges.MAX_ARGS or any("\x00" in a for a in c["args"]):
        raise JobError(L("参数太长或者有非法字符", "Arguments too long or contain invalid characters"))
    command_argv(agent, c)
    return c


def clean(agent: str, given: dict, *, base: dict | None = None, conversation: str | None = None, at: datetime | None = None,
          by_user: bool = False) -> dict:
    """新建（base None）或在 base 上改 → 完整的一版 {name, schedule, prompt, session, deliver, level, times, continuity, model, command}。
    写错抛 JobError。给了 command 的是只跑命令的（见文件开头）：不要 task，prompt 记那条命令的样子；调模型的和只跑命令的不能互相改。"""
    at = at or when.now()
    out: dict = dict(base) if base else {"session": "isolated", "level": "quiet", "times": None, "continuity": False, "model": None,
                                         "command": None, "watch": None, "pre": None, "flow": None}
    if base is None or given.get("when") not in (None, ""):
        try:
            out["schedule"] = when.parse(str(given.get("when") or ""), at)
        except when.WhenError as e:
            raise JobError(str(e)) from None
    if given.get("command") not in (None, "", {}):
        if base is not None and not base.get("command"):
            raise JobError(L("这个任务是调模型的：要只跑命令就新建一个", "This job runs the model; create a new job to run a command instead"))
        out["command"] = clean_command(agent, given["command"])
        out["prompt"] = command_text(out["command"])
    elif out.get("command"):
        if given.get("task") not in (None, ""):
            raise JobError(L("只跑命令的任务没有 task：要改就改 command", "A command job has no task: change its command instead"))
    elif (base is None and given.get("flow") in (None, "")) or given.get("task") not in (None, ""):
        task = str(given.get("task") or "").strip()
        if not task:
            raise JobError(L("要写 task：到点要做什么（一句完整的话）", "task is required: what to do when it runs (a full sentence)"))
        if len(task) > TASK_MAX:
            raise JobError(L(f"task 最多 {TASK_MAX} 字", f"task is at most {TASK_MAX} characters"))
        out["prompt"] = task
    if given.get("flow") not in (None, ""):  # 第 4 批：到点开一次工作流（engine/flows.py），代替 task
        if out.get("command"):
            raise JobError(L("只跑命令的任务不能跑工作流", "A command job can't run a workflow"))
        from . import flows
        f = flows.flow(str(given["flow"]).strip())
        if f is None or f["problem"]:
            raise JobError(f["problem"] if f else L(f"没有「{given['flow']}」这个工作流（server.json 的 engine.flows）",
                                                    f"No workflow called {given['flow']} (engine.flows in server.json)"))
        out["flow"] = f["name"]
        if given.get("task") in (None, ""):
            out["prompt"] = L(f"跑工作流「{f['name']}」", f"Run the workflow {f['name']}")
    elif given.get("flow") == "" and out.get("flow"):
        raise JobError(L("要停掉工作流就删掉这个任务，或者写一个 task 换掉", "To drop the workflow, delete the job or give it a task instead"))
    if given.get("name") not in (None, ""):
        out["name"] = " ".join(str(given["name"]).split())[:NAME_MAX]
    elif base is None:
        out["name"] = out["prompt"][:20] + ("…" if len(out["prompt"]) > 20 else "")
    if given.get("session") not in (None, ""):
        s = str(given["session"]).strip().lower()
        if s not in SESSIONS:
            raise JobError(L("session 只能是 isolated（单独跑）或 chat（在对话里跑）", "session must be isolated or chat"))
        out["session"] = s
    if given.get("level") not in (None, ""):
        lv = str(given["level"]).strip().lower()
        if lv not in LEVELS:
            raise JobError(L("level 只能是 ring / quiet / none", "level must be ring, quiet or none"))
        out["level"] = lv
    if "times" in given and given["times"] not in ("",):
        t = given["times"]
        if t in (None, 0, "0", "forever"):
            out["times"] = None
        else:
            try:
                t = int(t)
            except (TypeError, ValueError):
                raise JobError(L("times 写一个正整数（跑几次就停），不写 = 一直跑", "times is a positive integer (stop after that many runs)")) from None
            if t < 1:
                raise JobError(L("times 写一个正整数", "times is a positive integer"))
            out["times"] = t
    if "continuity" in given and given["continuity"] is not None:
        out["continuity"] = bool(given["continuity"]) if not isinstance(given["continuity"], str) else given["continuity"].lower() == "true"
    if "model" in given and by_user:
        m = str(given.get("model") or "").strip() or None
        if m and not models.known(m):  # 用户自己的 Claude Code（claude-code/…）也认：他自己的定时任务能花他的套餐
            raise JobError(L(f"不认识的模型：{m}", f"Unknown model: {m}"))
        out["model"] = m
    deliver = str(given.get("deliver") or "").strip()
    if deliver:
        if not _deliver_ok(agent, deliver):
            raise JobError(L(f"deliver 只能是你自己的对话（「{deliver}」不是）", f"deliver must be one of your own chats ({deliver} isn't)"))
        out["deliver"] = deliver
    elif base is None:
        out["deliver"] = conversation if conversation and _deliver_ok(agent, conversation) else own_thread(agent)
    for key in ("watch", "pre"):  # 第 4 批：盯着看、先跑一条命令（engine/watch.py）；写 false / {} 去掉
        if key not in given or given[key] is None:
            continue
        if given[key] in (False, "", {}):
            out[key] = None
        elif out.get("command"):
            raise JobError(L("只跑命令的任务没有 watch / pre：它本来就只跑命令", "A command job has no watch or pre: it only runs its command"))
        else:
            from . import watch
            out[key] = watch.clean(agent, given[key]) if key == "watch" else clean_command(agent, given[key])
    if out.get("command"):  # 不调模型：在哪跑、接着说、用哪个模型都用不上
        out.update(session="isolated", continuity=False, model=None, watch=None, pre=None)
    if out["schedule"]["kind"] == "once":
        out["times"] = None
        if when.next_after(out["schedule"], at) is None:
            raise JobError(L(f"那个时刻已经过了（{when.describe(out['schedule'])}）", f"That time has already passed ({when.describe(out['schedule'])})"))
    return out


def digest(spec: dict) -> str:
    """一版的指纹（只看会改变它做什么的：什么时候、做什么、在哪、投到哪、档位、几次、谁）。"""
    sched = {k: v for k, v in spec["schedule"].items() if k != "text"}
    body = json.dumps([sched, spec["prompt"], spec["session"], spec["deliver"], spec["level"], spec["times"], spec.get("model"),
                       *([spec["command"]] if spec.get("command") else []), *([{"watch": spec["watch"]}] if spec.get("watch") else []),
                       *([{"pre": spec["pre"]}] if spec.get("pre") else []), *([{"flow": spec["flow"]}] if spec.get("flow") else [])],
                      ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(body.encode()).hexdigest()[:24]


def major(old: dict, new: dict) -> list[str]:
    """改大的（要点头）是哪几样。"""
    keys = []
    if {k: v for k, v in old["schedule"].items() if k != "text"} != {k: v for k, v in new["schedule"].items() if k != "text"}:
        keys.append("when")
    if (old.get("command") or None) != (new.get("command") or None):
        keys.append("command")
    for k in ("watch", "pre", "flow"):
        if (old.get(k) or None) != (new.get(k) or None):
            keys.append(k)
    for k, name in (("prompt", "task"), ("session", "session"), ("deliver", "deliver"), ("model", "model")):
        if k == "prompt" and (old.get("command") or new.get("command")):
            continue
        if (old.get(k) or None) != (new.get(k) or None):
            keys.append(name)
    if LOUD[new["level"]] > LOUD[old["level"]]:
        keys.append("level")
    if (new["times"] or math.inf) > (old["times"] or math.inf):
        keys.append("times")
    return keys


# —— 卡 ——————————————————————————————————————————————————————————————————

def _where(spec: dict) -> str:
    name = unattended.thread_name(spec["deliver"])
    if spec["session"] == "chat":
        return L(f"在「{name}」当天的对话里跑（看得到今天聊了什么）", f'runs inside today\'s "{name}" chat (sees today\'s conversation)')
    return L(f"单独跑（不带聊天记录），结果发进「{name}」", f'runs on its own (no chat history) and posts into "{name}"')


def _often(spec: dict, at: datetime) -> str:
    n = when.runs_per_day(spec["schedule"], at)
    if spec["schedule"]["kind"] == "every":  # 每隔多久：从点「开」那一刻算起，卡上写不出几点
        gap = when.span_text(int(spec["schedule"]["minutes"]))
        head = L(f"什么时候：{when.describe(spec['schedule'])}（开了以后 {gap}第一次", f"When: {when.describe(spec['schedule'])} (first run {gap} after you turn it on")
    else:
        nxt = when.clock_text(when.next_after(spec["schedule"], at), at)
        head = L(f"什么时候：{when.describe(spec['schedule'])}（下一次 {nxt}", f"When: {when.describe(spec['schedule'])} (next {nxt}")
    if n and n >= 2 and not (spec.get("times") and spec["times"] <= n):  # 跑几次就停的、一天之内就跑完的：写「共几次」那一行就够了
        head += (L(f"；一天约 {round(n)} 次，每次跑一次命令，不调模型", f"; about {round(n)} runs a day, one command each, no model call")
                 if spec.get("command") else L(f"；一天约 {round(n)} 次，每次调一次模型", f"; about {round(n)} runs a day, one model call each"))
    return head + L("）", ")")


def _asks(c: dict) -> bool:
    """这条命令平时被 Agent 调时要不要用户点头（撤不回、对外的：tools.classify 分成 ask）。卡上要说清：开了以后每次都直接跑。"""
    from . import tools as tools_mod
    return bool(c.get("tool")) and tools_mod.classify(str(c["tool"]), [str(a) for a in c.get("args") or []])[0] == tools_mod.ASK


def card_of(agent: str, spec: dict, kind: str, *, old: dict | None, why: str, jid: str, at: datetime) -> dict:
    """收件箱卡上的字（kind schedule）。kind：new 新建 / change 改大的 / delete 删掉。"""
    import inbox
    who = inbox.source_name(agent, inbox.names())
    name = spec["name"]
    if kind == "delete":
        return {"title": L(f"删掉定时任务「{name}」", f'Delete the scheduled job "{name}"'),
                "why": why or L(f"{who}想删掉这个定时任务。删了就不再跑，恢复不了（以前跑过的记录留着）。",
                                f"{who} wants to delete this scheduled job. Once deleted it won't run again and can't be restored "
                                "(past runs stay in the log)."),
                "changes": [L(f"现在：{when.describe(spec['schedule'])} · {spec['prompt'][:80]}", f"Now: {when.describe(spec['schedule'])} · {spec['prompt'][:80]}")],
                "detail": L(f"任务 {jid}", f"Job {jid}"), "approveLabel": L("删掉", "Delete")}
    lines: list[str] = []
    if kind == "change" and old is not None:
        for k in major(old, spec) + [x for x in ("name",) if old.get(x) != spec.get(x)]:
            a, b = _field_text(k, old), _field_text(k, spec)
            lines.append(L(f"{_FIELD_NAMES[k][0]}：{a} → {b}", f"{_FIELD_NAMES[k][1]}: {a} → {b}"))
        lines.append(L("点「换上」之前，原来那一版照常跑", "Until you apply it, the current version keeps running"))
    elif c := spec.get("command"):
        lines += [_often(spec, at), L(f"跑什么：{spec['prompt'][:300]}（只跑这条命令，不调模型）", f"Runs: {spec['prompt'][:300]} (just this command, no model)"),
                  L(f"在哪跑：{who}的工作区，最多 {c['timeout']} 秒，到点停掉", f"Where: {who}'s workspace, stopped after {c['timeout']}s"),
                  L("输出记在运行记录里，不发进对话；要不要说话由它自己定（比如经 /api/chat/trigger 发一句）；没跑成在对话里说一声",
                    "Its output goes to the run log, not the chat; it decides itself whether to say something (e.g. through /api/chat/trigger); "
                    "failures are noted in the chat")]
        if _asks(c):
            lines.append(L("这条命令平时要你点头（撤不回或对外）：开了以后每次到点都直接跑，不再一次次问你",
                           "This command normally needs your OK (can't be undone or reaches others): once on, it runs every time without asking"))
        if c.get("retry"):
            n = len(BACKOFF) if c["retry"] is True else int(c["retry"])
            lines.append(L(f"没跑成按 30 秒、1 分、5 分……重试，最多 {n} 次，不越过下一次到点（同一条命令会再跑一遍）",
                           f"If it fails it's retried after 30s, 1m, 5m…, at most {n} times and never past its next run (the same command runs again)"))
        if spec["times"]:
            lines.append(L(f"跑 {spec['times']} 次就停", f"Stops after {spec['times']} runs"))
        lines.append(L("开了以后：跟它说一声就能暂停、改、删", "Once on: just tell it to pause, change or delete it"))
    else:
        lines += [_often(spec, at), L(f"做什么：{spec['prompt'][:300]}", f"What: {spec['prompt'][:300]}"), L(f"在哪跑：{_where(spec)}", f"Where: {_where(spec)}"),
                  L(f"推送：{L(*LEVEL_TEXT[spec['level']])}", f"Push: {L(*LEVEL_TEXT[spec['level']])}")]
        if spec["times"]:
            lines.append(L(f"跑 {spec['times']} 次就停", f"Stops after {spec['times']} runs"))
        if spec["continuity"]:
            lines.append(L("每次带上上一次发的话，别重复", "Each run sees what the last one said, to avoid repeats"))
        if spec.get("watch") or spec.get("pre"):
            from . import watch
            lines += watch.card_lines(spec.get("watch"), spec.get("pre"))
        if spec.get("flow"):
            from . import flows
            f = flows.flow(spec["flow"])
            steps = "；".join(flows.step_text(x) for x in (f or {}).get("steps") or [])[:400]
            lines.append(L(f"到点开一次工作流「{spec['flow']}」：{steps}", f"Each time it starts the workflow {spec['flow']}: {steps}"))
        lines.append(L("开了以后：跟它说一声就能暂停、改、删", "Once on: just tell it to pause, change or delete it"))
    nxt: list[str] = []
    t: datetime | None = at
    for _ in range(0 if spec["schedule"]["kind"] == "every" else 3):  # 每隔多久的：几点要看什么时候点「开」，不列
        t = when.next_after(spec["schedule"], t) if t else None
        if t:
            nxt.append(when.clock_text(t, at))
    fence = "```"
    if c := spec.get("command"):  # 整条命令照原样写在卡上（一项一个词）：点了「开」跑的就是它
        argv = json.dumps([c.get("tool") or c.get("script"), *(c.get("args") or [])], ensure_ascii=False)
        what = L("到点会跑的命令（一项一个词，不经 shell）：", "The command it runs each time (one word per item, no shell):") + f"\n\n{fence}\n{argv}\n{fence}\n\n"
        if c.get("input"):
            what += L("交给它的标准输入：", "Its standard input:") + f"\n\n{fence}\n{c['input'][:2000]}\n{fence}\n\n"
    else:
        what = L("到点会交给它的话：", "What it will be told each time:") + f"\n\n{fence}\n{spec['prompt']}\n{fence}\n\n"
    detail = what + (L("接下来几次：", "Next runs: ") + L("、", ", ").join(nxt) if nxt else "") + "\n\n" + L(f"任务 {jid}", f"Job {jid}")
    if kind == "change":
        return {"title": L(f"改定时任务「{name}」", f'Change the scheduled job "{name}"'),
                "why": why or L(f"{who}想改这个定时任务。改大的要你点头才换上。", f"{who} wants to change this scheduled job. Big changes wait for your OK."),
                "changes": lines, "detail": detail, "approveLabel": L("换上", "Apply")}
    return {"title": L(f"开一个定时任务：{name}（{when.describe(spec['schedule'])}）", f"Turn on a scheduled job: {name} ({when.describe(spec['schedule'])})"),
            "why": why or L(f"{who}想定一个定时任务。新的定时任务要你点头才开。", f"{who} wants to set up a scheduled job. New jobs stay off until you approve."),
            "changes": lines, "detail": detail, "approveLabel": L("开", "Turn on")}


_FIELD_NAMES = {"when": ("什么时候", "When"), "task": ("做什么", "What"), "session": ("在哪跑", "Where"), "deliver": ("投到", "Posts to"),
                "level": ("推送", "Push"), "times": ("跑几次", "Runs"), "model": ("模型", "Model"), "name": ("名字", "Name"),
                "command": ("命令", "Command"), "watch": ("盯着", "Watches"), "pre": ("先跑", "Runs first"), "flow": ("工作流", "Workflow")}


def _field_text(k: str, spec: dict) -> str:
    if k == "when":
        return when.describe(spec["schedule"])
    if k == "task":
        return spec["prompt"][:120]
    if k == "session":
        return L("在对话里跑", "in the chat") if spec["session"] == "chat" else L("单独跑", "on its own")
    if k == "deliver":
        return unattended.thread_name(spec["deliver"])
    if k == "level":
        return L(*LEVEL_TEXT[spec["level"]])
    if k == "times":
        return str(spec["times"]) if spec["times"] else L("一直", "no limit")
    if k == "model":
        return spec.get("model") or L("跟着对话", "the chat's model")
    if k == "command":
        return f"{command_text(spec['command'])} ({spec['command']['timeout']}s)" if spec.get("command") else "—"
    if k == "watch":
        from . import watch
        return watch.describe(spec.get("watch")) or "—"
    if k == "pre":
        return command_text(spec["pre"]) if spec.get("pre") else "—"
    if k == "flow":
        return spec.get("flow") or "—"
    return str(spec.get(k) or "")


def _hash(card: dict) -> str:
    from . import tools as tools_mod
    return tools_mod.card_hash(card["title"], card["why"], card["changes"], card["detail"])


async def _submit(j: dict, card: dict, *, dedupe: str, thread: str, expires: str | None) -> dict:
    """交卡（inbox.add），把卡的 id 和指纹记在任务上。→ {"inbox": id} 或 {"rejected": 日期, "note": …}。"""
    import inbox
    from fastapi.responses import JSONResponse
    res = await inbox.add(inbox.ItemIn(kind="schedule", source=j["agent"], thread=thread, dedupe=dedupe, expiresAt=expires, **card))
    if not isinstance(res, dict) or not res.get("ok"):
        try:
            body = json.loads(bytes(res.body)) if isinstance(res, JSONResponse) else {}
        except ValueError:
            body = {}
        return {"rejected": str(body.get("rejectedAt") or "")[:10], "note": " ".join(str(body.get("note") or "").split())[:200]}
    with store.tx() as conn:
        conn.execute("UPDATE engine_jobs SET inbox=?, card=?, updated_at=? WHERE id=?", (res["id"], _hash(card), store.now_iso(), j["id"]))
    return {"inbox": res["id"]}


async def _withdraw_card(iid: str | None) -> None:
    """撤掉还在等的那张卡（换了新的改动、草稿被删）。已经定了的不管。"""
    if not iid:
        return
    import inbox
    try:
        await inbox.withdraw(iid)
    except HTTPException:
        pass


def pending_reply(jid: str, iid: str, what: str, again: bool = False) -> dict:
    """交给用户点头以后回给模型的（不算出错）：和关卡的 approval-pending 一个口径。"""
    msg = (LS(f"这个已经在等用户点头了（收件箱卡 {iid}）：别再调。", f"This is already waiting for the user's OK (inbox card {iid}): don't call again.") if again else
           LS(f"{what}已经交给用户点头（收件箱卡 {iid}，就在你这条回复下面）。他点了才生效；别再调一次，也别换个办法绕过去，"
              "也不用另外交收件箱卡。现在跟用户说一声在等他点头就行。",
              f"{what} is waiting for the user's OK (inbox card {iid}, shown under your reply). It takes effect only when they approve; "
              "don't call again, don't work around it and don't submit another inbox card. Just tell the user it's waiting for their OK."))
    return {"status": "approval-pending", "job": jid, "inbox": iid, "message": msg}


# —— 建、改、停、删（Agent 和用户共用；by_user = 用户在 app 里做的，直接生效）——————————————————————————

def _insert(agent: str, spec: dict, *, state: str, approved: bool, by: str, next_run: datetime | None) -> dict:
    ready()
    jid = "job-" + uuid.uuid4().hex[:8]
    now = store.now_iso()
    with store.tx() as conn:
        live = conn.execute("SELECT COUNT(*) FROM engine_jobs WHERE agent=? AND state IN ('draft','on','paused')", (agent,)).fetchone()[0]
        if live >= MAX_JOBS:
            raise JobError(L(f"一个 Agent 最多 {MAX_JOBS} 个定时任务，先删掉用不着的", f"At most {MAX_JOBS} scheduled jobs per Agent; delete some first"))
        conn.execute("""INSERT INTO engine_jobs(id, agent, name, schedule, prompt, session, deliver, level, times, continuity, model, state,
            approved, digest, created_by, next_run, created_at, updated_at, command, watch, pre, flow)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                     (jid, agent, spec["name"], json.dumps(spec["schedule"], ensure_ascii=False), spec["prompt"], spec["session"], spec["deliver"],
                      spec["level"], spec["times"], int(bool(spec["continuity"])), spec.get("model"), state, int(approved), digest(spec), by,
                      iso(next_run), now, now, _command_json(spec), _json(spec.get("watch")), _json(spec.get("pre")), spec.get("flow")))
    j = get(jid)
    assert j is not None
    return j


def _write(jid: str, spec: dict | None = None, **cols: Any) -> None:
    """改一条：spec 给了就整版换上（下一次不在这里算）；cols 是别的栏。"""
    sets: dict[str, Any] = {}
    if spec is not None:
        sets.update(name=spec["name"], schedule=json.dumps(spec["schedule"], ensure_ascii=False), prompt=spec["prompt"], session=spec["session"],
                    deliver=spec["deliver"], level=spec["level"], times=spec["times"], continuity=int(bool(spec["continuity"])), model=spec.get("model"),
                    digest=digest(spec), command=_command_json(spec), watch=_json(spec.get("watch")), pre=_json(spec.get("pre")),
                    flow=spec.get("flow"))
    sets.update(cols)
    sets["updated_at"] = store.now_iso()
    with store.tx() as conn:
        conn.execute("UPDATE engine_jobs SET " + ", ".join(f"{k}=?" for k in sets) + " WHERE id=?", (*sets.values(), jid))


def _command_json(spec: dict) -> str | None:
    return json.dumps(spec["command"], ensure_ascii=False) if spec.get("command") else None


def _json(v: Any) -> str | None:
    return json.dumps(v, ensure_ascii=False) if v else None


def first_next(spec: dict, at: datetime | None = None) -> datetime | None:
    return when.next_after(spec["schedule"], at or when.now())


async def create(agent: str, given: dict, *, conversation: str | None = None, why: str = "", by_user: bool = False) -> dict:
    """新建。用户建的直接开 → {"job": …}；Agent 建的先停用、交卡 → pending_reply(...)；一模一样的已经在等 → 同一张卡。"""
    import chat
    at = when.now()
    spec = clean(agent, given, conversation=conversation, at=at, by_user=by_user)
    if by_user:
        j = _insert(agent, spec, state="on", approved=True, by="user", next_run=first_next(spec, at))
        chat.log_activity(L(f"建了定时任务「{spec['name']}」：{when.describe(spec['schedule'])}", f'Created the scheduled job "{spec["name"]}": '
                            f"{when.describe(spec['schedule'])}"), "edit")
        wake()
        return {"job": view(j)}
    d = digest(spec)
    ready()
    with store.read() as conn:
        dup = conn.execute("SELECT id, inbox FROM engine_jobs WHERE agent=? AND state='draft' AND digest=? AND inbox IS NOT NULL", (agent, d)).fetchone()
    if dup:
        return pending_reply(dup["id"], dup["inbox"], "", again=True)
    j = _insert(agent, spec, state="draft", approved=False, by=f"agent:{agent}", next_run=None)
    from . import tools as tools_mod
    thread = tools_mod.thread_for(conversation or own_thread(agent), agent)
    expires = spec["schedule"]["at"] if spec["schedule"]["kind"] == "once" else None
    try:
        got = await _submit(j, card_of(agent, spec, "new", old=None, why=why, jid=j["id"], at=at), dedupe=f"job:new:{agent}:{d}", thread=thread,
                            expires=expires)
    except HTTPException as e:
        _write(j["id"], state="withdrawn")
        raise JobError(L(f"没建：交不了收件箱卡（{e.detail}）", f"Not created: couldn't submit the inbox card ({e.detail})")) from None
    if "rejected" in got:
        _write(j["id"], state="rejected")
        raise JobError(LS(f"没建：用户{got['rejected']}拒过一模一样的定时任务" + (f"（{got['note']}）" if got["note"] else "") + "。用户没有同意，别再提，"
                          "也别换个办法做，除非他又让你做。",
                          f"Not created: the user declined this exact job on {got['rejected']}" + (f" ({got['note']})" if got["note"] else "")
                          + ". The user has NOT consented: don't ask again or do it another way unless they bring it up."))
    return pending_reply(j["id"], got["inbox"], LS(f"定时任务 {j['id']}（先停着）", f"Scheduled job {j['id']} (off for now)"))


def _unwatch(j: dict) -> None:
    """删掉的任务：盯着看记下的那份也不要了（engine/watch.py）。"""
    if j.get("watch"):
        from . import watch
        watch.drop(j["id"])


def _mine(jid: str, agent: str | None) -> dict:
    j = get(jid)
    if j is None or (agent is not None and j["agent"] != agent) or j["state"] in ("deleted",):
        raise JobError(L(f"没有 {jid} 这个定时任务（list 看看有哪些）", f"No scheduled job {jid} (list them first)"))
    return j


async def update(jid: str, given: dict, *, agent: str | None = None, why: str = "", by_user: bool = False) -> dict:
    """改。用户改的、小改：直接生效 → {"job": …}；Agent 改大的：改动先存着、交卡 → pending_reply(...)。"""
    import chat
    j = _mine(jid, None if by_user else agent)
    if j["state"] in GONE:
        raise JobError(L(f"这个定时任务{L(*STATE_TEXT[j['state']])}，要的话新建一个", f"This job is {STATE_TEXT[j['state']][1]}; create a new one instead"))
    at = when.now()
    old = spec_of(j)
    new = clean(j["agent"], given, base=old, at=at, by_user=by_user)
    if by_user:  # 用户改的：直接生效；还在等的卡（草稿、改动、删除）撤掉
        await _withdraw_card(j["inbox"] if j["state"] == "draft" or j["pending"] else None)
        state = "on" if j["state"] in ("draft", *ENDED) else j["state"]
        keep = "when" not in major(old, new) and j["state"] == state and j["next_run"]
        _write(jid, new, state=state, approved=1, pending=None, inbox=None,
               next_run=j["next_run"] if keep else iso(first_next(new, at)), runs=j["runs"] if keep else 0)
        chat.log_activity(L(f"改了定时任务「{new['name']}」", f'Changed the scheduled job "{new["name"]}"'), "edit")
        wake()
        return {"job": view(get(jid) or j)}
    if j["state"] == "draft":  # 还没开过：直接改草稿，卡原地换成新的样子
        _write(jid, new)
        if j["inbox"]:
            import inbox
            card = card_of(j["agent"], new, "new", old=None, why=why, jid=jid, at=at)
            try:
                await inbox.update(j["inbox"], inbox.ItemPatch(title=card["title"], why=card["why"], changes=card["changes"], detail=card["detail"],
                                                               approveLabel=card["approveLabel"]))
                _write(jid, card=_hash(card))
            except HTTPException:  # 卡已经定了（拒了 / 过期）：交一张新的
                got = await _submit(get(jid) or j, card, dedupe=f"job:new:{j['agent']}:{digest(new)}", thread=own_thread(j["agent"]), expires=None)
                if "rejected" in got:
                    _write(jid, state="rejected")
                    raise JobError(LS("没改：用户拒过一模一样的定时任务。", "Not changed: the user declined this exact job before.")) from None
        jj = get(jid) or j
        return pending_reply(jid, jj["inbox"] or "", LS(f"改过的定时任务 {jid}（还停着）", f"The revised job {jid} (still off)"))
    keys = major(old, new)
    direct: dict[str, Any] = {}  # 小改：名字、continuity、推得更轻、少跑几次，直接生效
    if new["name"] != old["name"]:
        direct["name"] = new["name"]
    if bool(new["continuity"]) != bool(old["continuity"]):
        direct["continuity"] = int(bool(new["continuity"]))
    if "level" not in keys and new["level"] != old["level"]:
        direct["level"] = new["level"]
    if "times" not in keys and new["times"] != old["times"]:
        direct["times"] = new["times"]
    if direct:
        _write(jid, **direct)
    if not keys:
        return {"ok": True, "job": jid, "message": LS("改好了，直接生效。" if direct else "没有要改的。", "Done; in effect now." if direct else "Nothing to change."),
                "line": line(get(jid) or j)}
    await _withdraw_card(j["inbox"] if j["pending"] else None)
    _write(jid, pending=json.dumps(new, ensure_ascii=False), inbox=None)
    try:
        got = await _submit(get(jid) or j, card_of(j["agent"], new, "change", old=old, why=why, jid=jid, at=at),
                            dedupe=f"job:{jid}:change:{digest(new)}", thread=own_thread(j["agent"]), expires=None)
    except HTTPException as e:
        _write(jid, pending=None)
        raise JobError(L(f"没改：交不了收件箱卡（{e.detail}）", f"Not changed: couldn't submit the inbox card ({e.detail})")) from None
    if "rejected" in got:
        _write(jid, pending=None)
        raise JobError(LS(f"没改：用户{got['rejected']}拒过一模一样的改动。用户没有同意，别再提，除非他又让你改。",
                          f"Not changed: the user declined this exact change on {got['rejected']}. The user has NOT consented: don't ask again "
                          "unless they bring it up."))
    return pending_reply(jid, got["inbox"], LS(f"对 {jid} 的改动（原来那一版照常跑）", f"The change to {jid} (the current version keeps running)"))


def pause(jid: str, *, agent: str | None = None) -> dict:
    j = _mine(jid, agent)
    if j["state"] != "on":
        raise JobError(L(f"它现在{L(*STATE_TEXT[j['state']])}，不用暂停", f"It's {STATE_TEXT[j['state']][1]}, nothing to pause"))
    _write(jid, state="paused")
    return get(jid) or j


def resume(jid: str, *, agent: str | None = None, by_user: bool = False) -> dict:
    j = _mine(jid, agent)
    if j["state"] == "on":
        return j
    if j["state"] in GONE:
        raise JobError(L(f"它{L(*STATE_TEXT[j['state']])}，恢复不了：要的话新建一个", f"It's {STATE_TEXT[j['state']][1]} and can't be resumed; create a new one"))
    if j["state"] == "draft":
        raise JobError(L("这个定时任务还没被同意：点它那张卡上的「开」", "This job hasn't been approved yet: approve its inbox card") if by_user else
                       L("这个定时任务还没被同意：等用户点那张卡的「开」", "This job hasn't been approved yet: wait for the user to approve its card"))
    if not (j["approved"] or by_user):
        raise JobError(L("这一版还没被同意", "This version hasn't been approved"))
    at = when.now()
    nxt = parse_iso(j["next_run"]) if j["state"] == "paused" and j["next_run"] else first_next(spec_of(j), at)  # 停着的时候到点的：恢复后补跑一次（Hermes）
    if nxt is None:
        raise JobError(L("没有下一次了（一次的那个时刻已经过了）：要的话改一个时间", "There's no next run (the one-off time has passed); change the time"))
    _write(jid, state="on", approved=1, next_run=iso(nxt), runs=0 if j["state"] in ENDED else j["runs"], fails=0)
    wake()
    return get(jid) or j


async def delete(jid: str, *, agent: str | None = None, why: str = "", by_user: bool = False) -> dict:
    """删。草稿、跑完的、用户删的：直接删 → {"ok"}；Agent 删开着 / 停着的：交卡 → pending_reply(...)。"""
    import chat
    j = _mine(jid, None if by_user else agent)
    if by_user or j["state"] in ("draft", *ENDED, "rejected", "withdrawn"):
        await _withdraw_card(j["inbox"] if j["state"] == "draft" or j["pending"] else None)
        _write(jid, state="withdrawn" if j["state"] == "draft" and not by_user else "deleted", pending=None, next_run=None)
        _unwatch(j)
        if by_user:
            chat.log_activity(L(f"删了定时任务「{j['name']}」", f'Deleted the scheduled job "{j["name"]}"'), "edit")
        return {"ok": True, "message": LS("删了。", "Deleted.")}
    if j["pending"] and j["pending"].get("delete") and j["inbox"]:
        return pending_reply(jid, j["inbox"], "", again=True)
    await _withdraw_card(j["inbox"] if j["pending"] else None)
    _write(jid, pending=json.dumps({"delete": True}), inbox=None)
    got = await _submit(get(jid) or j, card_of(j["agent"], spec_of(j), "delete", old=None, why=why, jid=jid, at=when.now()),
                        dedupe=f"job:{jid}:delete", thread=own_thread(j["agent"]), expires=None)
    if "rejected" in got:
        _write(jid, pending=None)
        raise JobError(LS(f"没删：用户{got['rejected']}说过不删。别再提，除非他又让你删。",
                          f"Not deleted: the user said no on {got['rejected']}. Don't ask again unless they bring it up."))
    return pending_reply(jid, got["inbox"], LS(f"删掉 {jid}", f"Deleting {jid}"))


# —— 用户点了卡（inbox.HOOKS["schedule"]）—————————————————————————————————————————

async def on_decided(it: dict, action: str) -> dict | None:
    """同意 → 开 / 换上改动 / 删掉，当场生效；拒绝 / 撤回 → 草稿作废、改动和删除作罢。不是定时任务的卡（OpenClaw 那边交的）→ None，照旧。"""
    import chat
    j = by_inbox(it["id"])
    if j is None:
        return None
    if action in ("reject", "withdraw"):
        if j["state"] == "draft":
            _write(j["id"], state="rejected" if action == "reject" else "withdrawn")
        else:
            _write(j["id"], pending=None, inbox=None)
        return None
    if action != "approve":
        return None
    if j["card"] != _hash(it):
        return {"failed": L("这张卡交上来以后被改过，和定时任务对不上，没有照做", "This card was edited after it was sent, so it no longer matches the job; nothing was done")}
    at = when.now()
    p = j["pending"]
    if j["state"] == "draft":
        nxt = first_next(spec_of(j), at)
        if nxt is None:
            _write(j["id"], state="missed", inbox=None)
            return {"failed": L(f"那个时刻已经过了（{when.describe(j['schedule'])}），没开；要的话让它重新定一个时间",
                                f"That time has passed ({when.describe(j['schedule'])}), so it wasn't turned on; ask for a new time")}
        _write(j["id"], state="on", approved=1, next_run=iso(nxt), inbox=None)
        chat.log_activity(L(f"开了定时任务「{j['name']}」：{when.describe(j['schedule'])}", f'Turned on the scheduled job "{j["name"]}"'), "approved")
        wake()
        return {"result": L(f"开了：{when.describe(j['schedule'])}，下一次 {when.clock_text(nxt, at)}（{j['id']}）",
                            f"it's on: {when.describe(j['schedule'])}, next run {when.clock_text(nxt, at)} ({j['id']})")}
    if p and p.get("delete"):
        _write(j["id"], state="deleted", pending=None, inbox=None, next_run=None)
        _unwatch(j)
        chat.log_activity(L(f"删了定时任务「{j['name']}」", f'Deleted the scheduled job "{j["name"]}"'), "approved")
        return {"result": L(f"删掉了（{j['id']}），不会再跑", f"it's deleted ({j['id']}) and won't run again")}
    if p:
        nxt = first_next(p, at) if major(spec_of(j), p).count("when") or j["state"] != "on" or not j["next_run"] else parse_iso(j["next_run"])
        if nxt is None:
            _write(j["id"], pending=None, inbox=None)
            return {"failed": L("改过的时刻已经过了，没换上", "The new time has already passed, so the change wasn't applied")}
        _write(j["id"], p, state="on", approved=1, pending=None, inbox=None, next_run=iso(nxt), runs=0 if j["state"] in ENDED else j["runs"])
        wake()
        return {"result": L(f"换上了：{when.describe(p['schedule'])}，下一次 {when.clock_text(nxt, at)}（{j['id']}）",
                            f"the change is applied: {when.describe(p['schedule'])}, next run {when.clock_text(nxt, at)} ({j['id']})")}
    return {"failed": L("这张卡已经没有要做的了", "There's nothing left to do for this card")}


def extra(iid: str) -> dict | None:
    """inbox.EXTRAS["schedule"]：卡片多给 app 的（以后画预览）。不是定时任务的卡 → None。"""
    j = by_inbox(iid)
    if j is None:
        return None
    spec = j["pending"] if j["pending"] and not j["pending"].get("delete") else spec_of(j)
    t, nxt = when.now(), []
    for _ in range(3):
        t = when.next_after(spec["schedule"], t) if t else None
        if t:
            nxt.append(iso(t))
    return {"job": j["id"], "action": "delete" if j["pending"] and j["pending"].get("delete") else "change" if j["pending"] else "new",
            "when": when.describe(spec["schedule"]), "task": spec["prompt"], "session": spec["session"], "deliver": spec["deliver"],
            "level": spec["level"], "next": nxt, "perDay": when.runs_per_day(spec["schedule"]), "command": spec.get("command"),
            "watch": spec.get("watch"), "pre": spec.get("pre")}


def revise_text(it: dict, note: str) -> str | None:
    """inbox 的「改一下」（POST revise）：这是定时任务的卡就告诉它用 cron 改，别去改卡上的字。"""
    import inbox
    j = by_inbox(it["id"])
    if j is None:
        return None
    n = note.rstrip("。.")
    return inbox.MARK + LS(f"「{it['title']}」（{it['id']}）要改一下：{n}。这是定时任务 {j['id']} 的卡：别用 inbox_ctl update 改卡上的字，"
                           f"用 cron 工具 update（id {j['id']}）按他说的改，会换一张新卡等他点头。",
                           f'"{it["title"]}" ({it["id"]}) needs changes: {n}. This card belongs to the scheduled job {j["id"]}: don\'t edit the card with '
                           f"inbox_ctl update; change the job with the cron tool's update (id {j['id']}) as they say, which makes a new card for their OK.")


def reply_text(it: dict) -> str | None:
    """在对话里引用这张卡回复（/api/chat/send 带 inboxId）：同上，给模型看的前情。"""
    j = by_inbox(it["id"])
    if j is None:
        return None
    return LS(f"（这条是在回复收件箱里的「{it['title']}」（{it['id']}）：用户要改定时任务 {j['id']}。按他说的用 cron 工具的 update 改"
              "（会换一张新卡等他点头）；别用 inbox_ctl update 改卡上的字。）",
              f'(This message replies to the inbox item "{it["title"]}" ({it["id"]}): the user wants the scheduled job {j["id"]} changed. Use '
              "the cron tool's update as they say (that makes a new card for their OK); don't edit the card with inbox_ctl update.)")


def install() -> None:
    """服务启动时挂上（engine/tools.py 的 install_hooks）：重启后点卡也照样生效。"""
    import inbox
    inbox.HOOKS["schedule"] = on_decided
    inbox.EXTRAS["schedule"] = extra
    inbox.REVISE_TEXT["schedule"] = revise_text
    inbox.REPLY_TEXT["schedule"] = reply_text
    from . import flows
    flows.install()  # 第 4 批：工作流要点头的那一步（kind flow）


# —— 到点 ————————————————————————————————————————————————————————————————

def _loop_bound(slot: str):
    """每个事件循环一份（测试里每个 asyncio.run 是新的循环）。"""
    global _sem, _wake
    loop = asyncio.get_running_loop()
    if slot == "sem":
        if _sem is None or _sem[0] is not loop:
            _sem = (loop, asyncio.Semaphore(max(1, int(cfg().get("max_parallel") or MAX_PARALLEL))))
        return _sem[1]
    if _wake is None or _wake[0] is not loop:
        _wake = (loop, asyncio.Event())
    return _wake[1]


def wake() -> None:
    """有任务改了（开了、换了时间）：ticker 马上重新看一遍。"""
    try:
        if _wake is not None:
            _wake[0].call_soon_threadsafe(_wake[1].set)
    except RuntimeError:
        pass


def _meta(key: str, value: str | None = None) -> str | None:
    ready()
    if value is None:
        with store.read() as conn:
            r = conn.execute("SELECT value FROM engine_job_meta WHERE key=?", (key,)).fetchone()
        return r["value"] if r else None
    with store.tx() as conn:
        conn.execute("INSERT INTO engine_job_meta(key, value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
    return value


def recover(at: datetime | None = None) -> int:
    """服务刚起来：上一个进程停机时正在跑的记成 interrupted（不重跑：下一次早就推进过了；一次的算跑过了）；记下停了多久。→ 几条。"""
    ready()
    at = at or when.now()
    _down["since"] = parse_iso(_meta("tick"))
    _down["started"] = at
    n = 0
    with store.tx() as conn:
        for r in conn.execute("SELECT * FROM engine_jobs WHERE running IS NOT NULL").fetchall():
            once = json.loads(r["schedule"]).get("kind") == "once"
            conn.execute("UPDATE engine_jobs SET running=NULL, last_status='interrupted', last_error=?, state=CASE WHEN ? AND state='on' THEN 'done' "
                         "ELSE state END, updated_at=? WHERE id=?", (LS("跑到一半服务重启了", "The server restarted mid-run"), int(once), store.now_iso(),
                                                                    r["id"]))
            n += 1
        conn.execute("UPDATE engine_job_runs SET status='interrupted', ended_at=? WHERE status='running'", (store.now_iso(),))
    return n


def _outage(due: datetime) -> bool:
    """due 那一刻服务器没开着（上一个进程最后一次看之后、这一个进程开始看之前）。"""
    since, started = _down["since"], _down["started"]
    return since is not None and started is not None and since < due <= started


def _today_runs(at: datetime) -> int:
    """今天已经跑了几次调模型的（daily_limit 数的就是它；心跳另算，只跑命令的不调模型，不算）。"""
    start = at.replace(hour=0, minute=0, second=0, microsecond=0)
    with store.read() as conn:
        return conn.execute("SELECT COUNT(*) FROM engine_job_runs r LEFT JOIN engine_jobs j ON j.id=r.job WHERE julianday(r.started_at)>=julianday(?) "
                            "AND r.kind!='heartbeat' AND r.status NOT IN ('skipped','missed','limit','estop','unchanged','gated') AND j.command IS NULL",
                            (iso(start),)).fetchone()[0]


def _add_run(j: dict, due: datetime | None, kind: str, status: str, at: datetime, error: str | None = None) -> str:
    rid = "jr-" + uuid.uuid4().hex[:10]
    with store.tx() as conn:
        conn.execute("INSERT INTO engine_job_runs(id, job, agent, due, started_at, ended_at, kind, status, error) VALUES(?,?,?,?,?,?,?,?,?)",
                     (rid, j["id"], j["agent"], iso(due), iso(at), None if status == "running" else iso(at), kind, status, error))
    return rid


def _claim(j: dict, due_raw: str | None, nxt: datetime | None, at: datetime, *, retry: dict | None = None) -> bool:
    """先推进下一次再跑（Hermes 的 advance_next_run）：next_run 还是我们看到的那个、没在跑、还开着，才抢得到。
    retry：这是重试那一次（第 4 批）：同一次的不再算进跑了几次，重试到第几次留着（跑完 _finish 看）；正常的那一次清掉旧的重试记录。"""
    with store.tx() as conn:
        return conn.execute("UPDATE engine_jobs SET next_run=?, running=?, runs=runs+?, retry=?, updated_at=? WHERE id=? AND state='on' "
                            "AND running IS NULL AND next_run IS ?",
                            (iso(nxt), iso(at), 0 if retry else 1, json.dumps(retry) if retry else None, store.now_iso(), j["id"],
                             due_raw)).rowcount == 1


def _skip(j: dict, due_raw: str | None, nxt: datetime | None) -> bool:
    """这一次不跑，只把下一次往后推（catch_up_missed = false、到了每日上限、急停、花费上限）；要是在重试，重试作罢。"""
    with store.tx() as conn:
        return conn.execute("UPDATE engine_jobs SET next_run=?, retry=NULL, updated_at=? WHERE id=? AND state='on' AND running IS NULL "
                            "AND next_run IS ?", (iso(nxt), store.now_iso(), j["id"], due_raw)).rowcount == 1


async def tick(at: datetime | None = None) -> list[str]:
    """看一遍到点的（见文件开头）。→ 这一次开跑的任务 id。只在 builtin 时跑；不等它们跑完（settle() 等）。"""
    import claw
    if not claw.is_builtin():
        return []
    ready()
    at = at or when.now()
    _meta("tick", iso(at))
    halted = estop.note("jobs")  # 急停（engine/estop.py）：到点的这一次不跑，记一条 estop
    with store.read() as conn:
        rows = conn.execute("SELECT * FROM engine_jobs WHERE state='on' AND running IS NULL AND next_run IS NOT NULL").fetchall()
    started: list[str] = []
    for r in rows:
        j = _job(r)
        due = parse_iso(j["next_run"])
        if due is None or due > at or j["id"] in _running:
            continue
        sched = j["schedule"]
        retry = j["retry"] if (j.get("retry") or {}).get("digest") == j["digest"] else None  # 第 4 批：重试那一次（这一版的才算）
        back = parse_iso((retry or {}).get("back"))
        if retry is not None and back is not None and back <= at:  # 自然的那一次也到了：重试作罢，照那一次跑
            retry, due = None, back
        late = (at - due).total_seconds()
        grace = when.grace_seconds(sched, outage=_outage(due), at=at)
        if retry is not None:
            nxt, kind, late = back, "retry", 0.0
            due = parse_iso(retry.get("due")) or due  # 运行记录、交给模型的话里写原来本该跑的那一刻
        elif sched["kind"] == "once":
            if late > grace:  # Hermes：一次的过了窗口不再跑，留一条记录
                _write(j["id"], state="missed", next_run=None, last_status="missed", last_error=LS(f"晚了 {int(late // 60)} 分钟，没跑",
                                                                                                 f"{int(late // 60)} min late; not run"))
                _add_run(j, due, "missed", "missed", at)
                continue
            nxt, kind = None, ("late" if late > LATE_OK else "on_time")
        else:
            nxt = when.next_after(sched, at, anchor=due)
            if late > grace:
                kind = "catch_up"
                if not catch_up_missed():
                    if _skip(j, j["next_run"], nxt):
                        _add_run(j, due, "catch_up", "skipped", at, error="catch_up_missed is false")
                    continue
            else:
                kind = "late" if late > LATE_OK else "on_time"
        if halted:  # 急停：推到下一次、记一条 estop（不算失败、不累计连错；一次的那种和每日上限一样记成错过）
            if _skip(j, j["next_run"], nxt):
                if sched["kind"] == "once":
                    _write(j["id"], state="missed", last_status="estop")
                _add_run(j, due, kind, "estop", at, error=estop.reason())
            continue
        if not j.get("command") and spend.enabled() and (hit := spend.blocked(agent=j["agent"], model=model_for(j["deliver"], j["model"]))):
            if _skip(j, j["next_run"], nxt):  # 第 4 批：今天的花费到了上限（engine/spend.py）：和每日上限一样跳过、记 limit
                if sched["kind"] == "once":
                    _write(j["id"], state="missed", last_status="limit")
                _add_run(j, due, kind, "limit", at, error=spend.message(hit))
            continue
        if not j.get("command") and _today_runs(at) >= daily_limit():
            if _skip(j, j["next_run"], nxt):
                if sched["kind"] == "once":
                    _write(j["id"], state="missed", last_status="limit")
                _add_run(j, due, kind, "limit", at)
                _note_limit(at)
            continue
        if not _claim(j, j["next_run"], nxt, at, retry=retry):
            continue
        rid = _add_run(j, due, kind, "running", at)
        _start(j, rid, due, kind, late, at)
        started.append(j["id"])
    return started


def _note_limit(at: datetime) -> None:
    import chat
    day = at.date().isoformat()
    if day in _limit_noted:
        return
    _limit_noted.add(day)
    chat.log_activity(L(f"定时任务今天已经跑了 {daily_limit()} 次，到上限了，今天剩下的先不跑（server.json engine.jobs.daily_limit）",
                        f"Scheduled jobs hit today's limit of {daily_limit()} runs; the rest of today's runs are skipped (engine.jobs.daily_limit)"), "failed")


def _start(j: dict, rid: str, due: datetime | None, kind: str, late: float, at: datetime, extra: str | None = None) -> None:
    task = asyncio.create_task(_run(j, rid, due, kind, late, at, extra))
    _running[j["id"]] = task

    def done(_t: asyncio.Task, jid: str = j["id"]) -> None:
        if _running.get(jid) is _t:
            _running.pop(jid, None)
    task.add_done_callback(done)


async def settle(timeout: float = 30) -> None:
    """等正在跑的都跑完（测试、关机前）。"""
    tasks = list(_running.values())
    if tasks:
        await asyncio.wait(tasks, timeout=timeout)


async def run_now(jid: str, *, agent: str | None = None, extra: str | None = None, kind: str = "manual") -> dict:
    """手动跑一次（同意过的才行；不改下一次的时间）。kind trigger（第 4 批）：被外面的事叫起来的一次（engine/triggers.py：签名对上、
    trust: owner 的 webhook 写了 action job），extra 是交给模型的那段（收到的内容包成资料）；只跑命令的照常跑那条命令。"""
    j = _mine(jid, agent)
    if agent is not None and estop.paused():  # 急停：Agent 自己手动跑也不行（你在 app 里点的照跑：那是你自己的动作）
        raise JobError(estop.notice())
    if not j["approved"] or j["state"] not in ("on", "paused"):
        raise JobError(L("只能手动跑开着或停着的、用户同意过的任务", "Only an approved job that's on or paused can be run by hand"))
    if j["id"] in _running or j["running"]:
        raise JobError(L("它正在跑", "It's running right now"))
    at = when.now()
    with store.tx() as conn:
        if conn.execute("UPDATE engine_jobs SET running=?, updated_at=? WHERE id=? AND running IS NULL", (iso(at), store.now_iso(), jid)).rowcount != 1:
            raise JobError(L("它正在跑", "It's running right now"))
    kind = kind if kind in OFF_SCHEDULE else "manual"
    rid = _add_run(j, None, kind, "running", at)
    _start(j, rid, None, kind, 0.0, at, extra)
    return {"ok": True, "run": rid}


def model_for(thread: str, pinned: str | None) -> str:
    """定时任务用哪个模型：任务上写了的 > engine.use.background（第 9 步「后台和定时」）> 那个对话的模型 > engine.model。"""
    if pinned and models.known(pinned):
        return pinned
    if bg := models.background_model():
        return bg
    import chat
    with chat._lock, chat.db() as conn:
        m = chat.thread_model(conn, thread)
    return m if m and models.known(m) else (models.default_model() or "")


def instructions(j: dict, due: datetime | None, kind: str, late: float, at: datetime, last: str | None, extra: str | None = None) -> str:
    """到点交给模型的话（Hermes 的 _CRON_HINT + OpenClaw 的 unattended run contract）。按 server.json 的语言。
    extra：被外面的事叫起来的那一次（kind trigger）接在最后的那段（收到的内容已经包成资料）。"""
    where = unattended.thread_name(j["deliver"])
    due_s = (due or at).strftime("%H:%M")
    zh_late = (f"（晚了 {int(late // 60)} 分钟：服务器那会儿没开着，攒下的几次合成了这一次）" if kind == "catch_up" else
               f"（晚了 {int(late // 60)} 分钟）" if kind == "late" else "（手动跑的一次）" if kind == "manual" else
               "（重试：上一次没跑成，什么都还没做）" if kind == "retry" else "（被外面来的一件事叫起来的一次，不是到点）" if kind == "trigger" else "")
    en_late = (f" ({int(late // 60)} min late: the server was down, so the missed runs are folded into this one)" if kind == "catch_up" else
               f" ({int(late // 60)} min late)" if kind == "late" else " (run by hand)" if kind == "manual" else
               " (a retry: the last attempt failed before doing anything)" if kind == "retry" else
               " (started by something that came in from outside, not by the schedule)" if kind == "trigger" else "")
    chat_mode = j["session"] == "chat"
    zh = (f"【定时任务】「{j['name']}」（{j['id']}，{when.describe(j['schedule'])}）本该 {due_s} 跑，现在 {at:%H:%M}{zh_late}。\n"
          "这一轮没人在看：没法问用户问题，也没人替你点头。现在就把下面这件事做完，最后只写要给用户看的那几句：就是提醒或结果本身，"
          "不写打算，不写「好的我会提醒你」。"
          + ("你的回复就出现在这个对话里，" if chat_mode else f"你的回复会发进「{where}」那个对话，")
          + "并按设置推送给用户；别用别的办法另外发。这一次确实没有要告诉用户的，就只回 [SILENT]（照原样写这个英文记号），别的什么都不写。"
          "这是一个已经开着的定时任务在跑：别因为下面写了「每天」「每两分钟」就再建或改定时任务。\n\n"
          f"要做的事：{j['prompt']}")
    en = (f"[Scheduled job] \"{j['name']}\" ({j['id']}, {when.describe(j['schedule'])}) was due at {due_s}; it is now {at:%H:%M}{en_late}.\n"
          "Nobody is watching this run: you can't ask the user anything and nobody can approve things for you. Do the task below now, then "
          "write only what the user should see: the reminder or the result itself, not a plan and not \"OK, I'll remind you\". "
          + ("Your reply appears in this chat " if chat_mode else f"Your reply is posted into the \"{where}\" chat ")
          + "and pushed to the user as set; don't send it any other way. If there's genuinely nothing to tell them this time, reply with exactly "
          "[SILENT] and nothing else. This is a run of an existing scheduled job: don't create or change scheduled jobs because the task "
          "says \"every day\" or \"every 2 minutes\".\n\n"
          f"Task: {j['prompt']}")
    text = LS(zh, en)
    if last:
        text += LS(f"\n\n上一次你发的是：\n{last}\n别重复上次说过的，接着往下说。", f"\n\nWhat you posted last time:\n{last}\nDon't repeat it; carry on from there.")
    if extra:
        text += "\n\n" + extra
    return text


def _last_said(jid: str) -> str | None:
    with store.read() as conn:
        r = conn.execute("SELECT preview FROM engine_job_runs WHERE job=? AND status='ok' AND preview IS NOT NULL "
                         "ORDER BY julianday(started_at) DESC, rowid DESC LIMIT 1", (jid,)).fetchone()
    return r["preview"] if r else None


async def execute(j: dict, due: datetime | None, kind: str, late: float, at: datetime, extra: str | None = None) -> Outcome:
    """跑一次（不记账，_run 记）。"""
    import bridges
    agent, thread = j["agent"], j["deliver"]
    if not bridges.known_agent(agent) or not unattended.thread_exists(thread):
        return Outcome(status="error", error=L("这个 Agent 或要投进去的对话已经不在了", "The Agent or the chat it posts to no longer exists"))
    if j.get("command"):
        return await run_command(j)
    parts = [extra] if extra else []
    if j.get("watch") or j.get("pre"):  # 第 4 批：先看一眼、先跑一条命令，再决定调不调模型（engine/watch.py）
        from . import watch
        if w := j.get("watch"):
            seen = await watch.check(j)
            if seen.status in ("baseline", "same"):
                return Outcome(status="unchanged", output=L("第一次看：记下来了", "first look: remembered") if seen.status == "baseline" else None)
            if seen.status == "error":
                return Outcome(status="error", error=seen.error)
            if w.get("url"):
                return await watch_url(j, seen.content)
            parts.insert(0, watch.changed_extra(j, seen))
        if c := j.get("pre"):
            verdict, more = await watch.pre(agent, c, f"pre:{j['id']}")
            if verdict != "wake":
                return Outcome(status="gated") if verdict == "sleep" else Outcome(status="error", error=more)
            if more:
                parts.append(more)
    if j.get("flow"):  # 第 4 批：到点开一次工作流（engine/flows.py：它自己一步一步走，结果在对话里）
        from . import flows
        try:
            r = flows.start(j["flow"], by=f"job:{j['id']}")
        except flows.FlowBusy as e:
            return Outcome(status="busy", error=str(e))
        except flows.FlowError as e:
            return Outcome(status="error", error=str(e), kind="config")
        return Outcome(status="done", text=L(f"开始了工作流「{j['flow']}」（{r.get('id')}）", f"Started the workflow {j['flow']} ({r.get('id')})"))
    text = instructions(j, due, kind, late, at, _last_said(j["id"]) if j["continuity"] else None, "\n\n".join(parts) or None)
    label = L(f"定时「{j['name']}」", f"Scheduled: {j['name']}")
    if j["session"] == "chat":
        refs: list[int] = []

        def started(user_id: int) -> None:  # 这一轮里 cron 工具认得出是定时任务自己在跑
            refs.append(user_id)
            _trigger_refs.add(user_id)
        try:
            return await unattended.in_chat(thread, label, text, level=j["level"], idle=idle_timeout(),
                                            model=j["model"] or models.background_model(), on_start=started)
        finally:
            for r in refs:
                _trigger_refs.discard(r)
    fm, im = unattended.marks()
    out = await unattended.fresh(agent, thread, f"cron:{j['id']}", text, model=model_for(thread, j["model"]), idle=idle_timeout())
    if out.status == "done":
        stamp = when.now().strftime("%H:%M")
        gw = f"[Scheduled job \"{j['name']}\" ({j['id']}) ran on its own at {stamp} and posted the message below in this chat.]"
        note = unattended.mirror_note(f"[{stamp}] Your scheduled job \"{j['name']}\" ({j['id']}, {when.describe(j['schedule'])}) ran on its own "
                                      "in a separate session and posted this in this chat:", out.text)
        out.message = await unattended.post(thread, label, gw, out.text, model=out.model, level=j["level"], tag=f"cron:{j['id']}", note=note,
                                            feed_mark=fm, inbox_mark=im, served=out.served)
    return out


async def watch_url(j: dict, content: str) -> Outcome:
    """盯的网页变了（engine/watch.py）：网页上的字是别人的，只交给零工具的那一读（engine/triggers.deliver，照这个任务的 task 写通知），
    发通知或者出卡（watch.action）；不开主人回合。→ done（通知、卡）/ silent（读了觉得不用说）/ limit / error。"""
    from . import triggers
    w = j["watch"]
    spec = {"source": f"watch:{j['id']}", "kind": "watch", "agent": j["agent"], "thread": j["deliver"], "action": w.get("action") or "notify",
            "say": j["prompt"], "task": j["prompt"], "level": j["level"], "label": j["name"], "event": "changed"}
    eid = triggers.record(spec["source"], "watch", event="changed", data={"spec": spec, "content": content})
    if eid is None:
        return Outcome(status="error", error="couldn't record the change")
    st = await triggers.deliver(eid, spec, content)
    ev = triggers.get(eid) or {}
    if st in ("notified", "carded"):
        return Outcome(status="done", text=ev.get("summary") or "")
    if st == "skipped":
        return Outcome(status="silent", text=ev.get("summary") or "")
    return Outcome(status="limit" if st == "limit" else "error", error=ev.get("error") or st)


def output_text(out: str, err: str) -> str:
    """命令的输出（运行记录里留的）：标准输出；有错误输出也带上（两样都有就分开写）。太长的留头尾（OUTPUT_MAX）。"""
    text = f"stdout:\n{out}\n\nstderr:\n{err}" if out and err else out or err
    if len(text) <= OUTPUT_MAX:
        return text
    head, tail = text[:OUTPUT_MAX * 2 // 3], text[-OUTPUT_MAX // 4:]
    return f"{head}\n…（{L('中间省略', 'omitted')} {len(text) - len(head) - len(tail)} {L('字', 'chars')}）…\n{tail}"


async def run_command(j: dict) -> Outcome:
    """只跑命令的任务跑一次（见文件开头）：跑之前再查一遍能不能跑（command_argv），起一个子进程，到点停掉。不调模型、不往对话里发。"""
    import bridges
    c, agent = j["command"], j["agent"]
    try:
        name, cmd = command_argv(agent, c)
    except JobError as e:  # 不许跑、找不到：kind config（写了 retry 也不重试）
        return Outcome(status="error", error=str(e), kind="config")
    via = bridges.VIA.set("auto")  # 脚本再请求服务器（/api/chat/trigger……）开的回合算后台：ChatGPT 套餐据此认（engine/models.may_spend）
    try:
        rc, out, err = await bridges.execute(name, cmd, c.get("args") or [], c.get("input"), agent, timeout=float(c["timeout"]), kill_on_cancel=True,
                                             shared=False)  # 不占 Agent 调工具的那 4 个：定时任务自己有 max_parallel
    except bridges.BridgeTimeout as e:
        return Outcome(status="timeout", error=str(e))
    except bridges.BridgeError as e:
        return Outcome(status="error", error=str(e), kind="config")
    finally:
        bridges.VIA.reset(via)
    output = output_text(out, err)
    if rc != 0:
        last = (err or out).strip().splitlines()[-1][:200] if (err or out).strip() else ""
        return Outcome(status="error", error=L(f"退出码 {rc}", f"exit code {rc}") + (f": {last}" if last else ""), output=output)
    if name and (after := bridges.AFTER.get(name)) is not None:  # 和 Agent 平时调这个工具一样：服务端接着做的（世界树交了修剪计划就出卡）
        output = await after([str(a) for a in c.get("args") or []], output, agent)
    return Outcome(status="done", output=output)


def _say(thread: str, text: str) -> None:
    """往对话里写一行灰字（不推送，不给模型跑）：出错、停用的说明。"""
    import chat
    with chat._lock, chat.db() as conn:
        conn.execute("INSERT INTO messages(thread, role, text, model, ts, origin) VALUES(?,?,?,?,?,?)",
                     (thread, "auto", unattended.AUTO_TRIGGER + text, None, chat.now_iso(), "auto"))


async def _run(j: dict, rid: str, due: datetime | None, kind: str, late: float, at: datetime, extra: str | None = None) -> None:
    out: Outcome
    try:
        async with _loop_bound("sem"):
            out = await execute(j, due, kind, late, at, extra)
    except asyncio.CancelledError:
        _finish(j, rid, Outcome(status="interrupted", error="cancelled"), kind)
        raise
    except Exception as e:  # noqa: BLE001 — 一条出错不拖累别的
        out = Outcome(status="error", error=f"{type(e).__name__}: {str(e)[:200]}")
    _finish(j, rid, out, kind)
    if (after := AFTER.get(str(j.get("created_by") or ""))) is not None:
        try:
            await after(j, out)
        except Exception as e:  # noqa: BLE001 — 接着做的出错不影响这条任务
            print(f"[engine] 定时「{j.get('name')}」跑完以后接着做的没做成：{type(e).__name__}: {str(e)[:160]}")


# —— 失败重试（第 4 批）————————————————————————————————————————————————————————
# 调模型的任务没跑成：先分是哪一类（failure）。
#   临时的（transient）：连不上、429 / 过载 / 5xx、超时、流断了，而且这一次还没出字、没调过工具（再来一次不会把做过的事再做一遍）
#     → 按 backoff() 的表（30 秒、1 分、5 分、15 分、60 分，OpenClaw 的 DEFAULT_ERROR_BACKOFF_SCHEDULE_MS）重试同一次；
#     不越过下一次正常到点（到了就照那一次跑）；一次的最多 ONCE_RETRIES 次。重试那几次不算进「跑了几次」「连着几次没跑成」，
#     要重试的先不在对话里说，重试完了还不行才照旧说一声；服务商给了要等多久（「try again in 20s」）就至少等那么久。
#   配额窗口关了（quota，Hermes 的 cron/quota_hold.py）：服务商说了什么时候恢复（「resets 3pm」「retry after 7200s」）→ 窗口里的
#     那几次合成一次：下一次挂到窗口之后的第一格（多等 HOLD_SLACK 秒）；一天一次这种稀的（下一格离窗口结束还有半个周期以上）在窗口
#     一过补跑这一次（只补一回）；一次的挂到窗口之后。没说什么时候恢复的、要等 HOLD_MAX 以上的：当永久的。
#   永久的（permanent）：规矩不许、配置不对、没登录、命令跑不起来、花费到了上限、急停、模型拒了、已经出了字或调过工具 → 不重试。
# 只跑命令的：脚本不一定能重跑，默认不重试；command 写了 retry（true = 整张表，数字 = 最多几次）才重试（命令本身不许跑的不重试）。
# 手动跑的不重试。心跳不重试（下一拍就是）。
# 哪些字眼算临时的：照 OpenClaw 的 src/cron/retry-hint.ts（TRANSIENT_PATTERNS）：状态码要带上 HTTP / status 这类上下文，
# 不然「exited with 503 lines」「pid 511」这种正文里的数也会被当成服务器出错；表和一次的最多三次照 OpenClaw 的 cron；配额窗口照 Hermes 的
# cron/quota_hold.py（HOLD_SLACK_SECONDS、_recovery_worthwhile）。OpenClaw、Hermes 都是 MIT，见仓库根目录的 THIRD_PARTY_NOTICES。
HOLD_SLACK = 60
PERMANENT = frozenset({"rule", "config", "budget", "refusal", "auth", "broken", "max_steps", "aborted", "estop"})
QUOTA = re.compile(r"usage limit|insufficient_quota|exceeded your current quota|you've hit your|out of extra usage|limit will reset|"
                   r"credit balance|billing", re.I)
TRANSIENT = re.compile(r"\b(?:https?|status(?:[ _-]?code)?|response(?:[ _-]?code)?)\b[\s:=#\"'(]{0,6}(?:408|409|425|429|5\d\d)\b"
                       r"|\b(?:429|5\d\d)\b[\s:)\].,-]*(?:too many requests|rate[_ -]?limit|internal server error|server error|bad gateway|"
                       r"service unavailable|gateway time-?out|overloaded)"
                       r"|rate[_ -]?limit(?:ed|ing)?|too many requests|resource has been exhausted|overloaded|high demand|"
                       r"temporar(?:ily|y) (?:overloaded|unavailable)|capacity exceeded|internal server error|bad gateway|service unavailable|"
                       r"gateway time-?out|network|fetch failed|socket|econnreset|econnrefused|eai_again|enetdown|ehostunreach|enetunreach|epipe|"
                       r"can't reach|cannot reach|unreachable|connect(?:ion)?\s*(?:error|reset|refused|closed|aborted)|readerror|"
                       r"remoteprotocolerror|timeout|timed out|etimedout|stream ended", re.I)
_WAIT = re.compile(r"(?:retry|try again)\s+(?:after|in)\s+((?:\d+(?:\.\d+)?\s*(?:ms|s|sec|secs|seconds?|m|min|mins|minutes?|h|hr|hrs|hours?)\b[\s,]*)+)"
                   r"|retry-after:?\s*(\d+)|resets?\s+in\s+((?:\d+(?:\.\d+)?\s*(?:s|sec|secs|seconds?|m|min|mins|minutes?|h|hr|hrs|hours?|d|days?)\b[\s,]*)+)",
                   re.I)
_PART = re.compile(r"(\d+(?:\.\d+)?)\s*(ms|s|sec|secs|seconds?|m|min|mins|minutes?|h|hr|hrs|hours?|d|days?)\b", re.I)
_RESET_AT = re.compile(r"(?:resets?|reset at|until)\s+(?:at\s+)?(\d{4}-\d\d-\d\d[T ]\d\d:\d\d(?::\d\d(?:\.\d+)?)?(?:Z|[+-]\d\d:?\d\d)?)", re.I)
_RESET_CLOCK = re.compile(r"resets?\s+(?:at\s+)?(\d{1,2})(?::(\d\d))?\s*(am|pm)?(?:\s*\(([A-Za-z_]+/[A-Za-z_/]+|UTC)\))?", re.I)


def _seconds(text: str) -> float:
    unit = {"ms": 0.001, "s": 1, "m": 60, "h": 3600, "d": 86400}
    return sum(float(n) * unit["ms" if u.lower() == "ms" else u[0].lower()] for n, u in _PART.findall(text))


def window_end(text: str, now: datetime) -> datetime | None:
    """报错里说的什么时候恢复（「resets 3pm」「resets 2026-10-20T14:20:00Z」「retry after 7200s」「Please try again in 20s」）→ 那一刻；
    没说 → None。只写了几点的按 now 的时区（Claude Code 括号里写了时区就按它），已经过了的算明天那一刻。"""
    s = str(text or "")
    if m := _RESET_AT.search(s):
        t = parse_iso(m.group(1).replace(" ", "T"))
        if t is not None and t > now:
            return t
    if m := _WAIT.search(s):
        n = _seconds(m.group(1) or m.group(3) or "") if not m.group(2) else float(m.group(2))
        return now + timedelta(seconds=n) if n > 0 else None
    if m := _RESET_CLOCK.search(s):
        h, mi, ap = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower()
        if ap or m.group(2):
            h = h % 12 + (12 if ap == "pm" else 0) if ap else h
            if h < 24 and mi < 60:
                tz = now.tzinfo
                if m.group(4):
                    with contextlib.suppress(Exception):
                        tz = ZoneInfo(m.group(4))
                base = now.astimezone(tz)
                t = base.replace(hour=h, minute=mi, second=0, microsecond=0)
                return t if t > base else t + timedelta(days=1)
    return None


def _side_effects(out: Outcome) -> bool:
    """这一次调过工具没有（事件表里有 tool 那一步）：调过的再来一次可能把做过的事再做一遍。"""
    from . import events
    return bool(out.turn) and any(s["type"] == "tool" for s in events.steps(out.turn))


def failure(j: dict, out: Outcome) -> tuple[str, datetime | None]:
    """没跑成的这一次是哪一类（见上面）→ ("transient" | "quota" | "permanent", 配额窗口什么时候恢复)。"""
    err = str(out.error or "")
    if out.status in ("limit", "estop") or out.kind in PERMANENT:
        return "permanent", None
    if j.get("command"):  # 只跑命令的：命令本身不许跑、找不到（kind config）不重试；别的照 retry 写的
        return ("permanent" if out.kind == "config" else "transient"), None
    if out.kind == "quota" or QUOTA.search(err):
        end = window_end(err, when.now())
        return ("quota", end) if end is not None else ("permanent", None)
    if (out.text or "").strip() or _side_effects(out):
        return "permanent", None
    if out.status == "timeout" or out.kind == "timeout" or out.retryable or TRANSIENT.search(err):
        return "transient", None
    return "permanent", None


def retry_ladder(j: dict) -> list[int]:
    """这个任务临时出错按什么表重试（秒）：调模型的 backoff()；一次的只取前 ONCE_RETRIES 个；只跑命令的看它自己的 command.retry
    （表照 engine.jobs.retry 写的列表，没写列表就是默认的 BACKOFF）。"""
    if c := j.get("command"):
        r = c.get("retry")
        ladder = backoff() if isinstance(cfg().get("retry"), list) else list(BACKOFF)
        return [] if not r else ladder if r is True else ladder[:int(r)]
    ladder = backoff()
    return ladder[:ONCE_RETRIES] if j["schedule"].get("kind") == "once" else ladder


def retry_plan(j: dict, out: Outcome, kind: str, due: datetime | None, now: datetime) -> dict | None:
    """没跑成以后怎么办（见上面）→ {"at": 下一次什么时候, "retry": 存在任务上的重试记录 | None（只是往后挪自然的那一次）,
    "note": 写在出错后面的那半句} 或者 None（照旧：等下一次正常到点）。j 是跑完时库里的样子（next_run 是已经推进过的那一次）。"""
    if not j.get("command") and not backoff():  # 调模型的任务：engine.jobs.retry 没开 = 照旧（不重试、不挂到配额窗口之后、不补跑）
        return None
    what, end = failure(j, out)
    cur = j.get("retry") or {}
    natural = parse_iso(j["next_run"])
    if what == "quota" and end is not None:
        w = end + timedelta(seconds=HOLD_SLACK)
        if (w - now).total_seconds() > HOLD_MAX:
            return None

        def until(at: datetime) -> str:
            return L(f"；服务商的配额窗口到 {when.clock_text(w, now)} 才开，下一次 {when.clock_text(at, now)}",
                     f"; the provider's usage window is closed until {when.clock_text(w, now)}, next run {when.clock_text(at, now)}")
        did = bool((out.text or "").strip()) or _side_effects(out)  # 调过工具、出过字的这一次不再来
        if natural is None:  # 一次的：窗口一过再来一次
            if cur.get("hold") or did:
                return None
            return {"at": w, "retry": {"n": 1, "hold": True, "back": None, "due": iso(due), "digest": j["digest"]}, "note": until(w)}
        if natural < w:  # 窗口里的那几次合成一次：挂到窗口之后的第一格
            at = when.next_after(j["schedule"], w, anchor=natural)
            return {"at": at, "retry": None, "note": until(at)} if at else None
        period = when.period_seconds(j["schedule"], now) or 0
        if kind != "retry" and not cur.get("hold") and not did and period and (natural - w).total_seconds() >= period / 2:  # 稀的：窗口一过补跑这一次（只补一回）
            return {"at": w, "retry": {"n": 1, "hold": True, "back": iso(natural), "due": iso(due), "digest": j["digest"]}, "note": until(w)}
        return None
    if what != "transient" or cur.get("hold") or (j.get("session") == "chat" and not j.get("command")):
        return None  # 发进对话的任务（session chat）临时出错不重试：那一轮已经在对话里留了「自动触发」和没拿到回复，再来一遍就是两份
    ladder = retry_ladder(j)
    n = int(cur.get("n") or 0) if kind == "retry" else 0
    if n >= len(ladder):
        return None
    hint = window_end(str(out.error or ""), now)  # 服务商说了要等多久（「try again in 20s」）：至少等那么久
    wait = max(float(ladder[n]), (hint - now).total_seconds() if hint else 0.0)
    at = now + timedelta(seconds=wait)
    if natural is not None and at >= natural:  # 不越过下一次正常到点
        return None
    span = when.span_text(max(1, round(wait / 60))) if wait >= 60 else L(f"{int(wait)} 秒", f"{int(wait)}s")
    return {"at": at, "retry": {"n": n + 1, "back": iso(natural), "due": iso(due), "digest": j["digest"]},
            "note": L(f"；{span}后重试（第 {n + 1} 次）", f"; retrying in {span} (retry {n + 1})")}


def _finish(j: dict, rid: str, out: Outcome, kind: str) -> None:
    if out.status == "error" and (out.kind == "budget" or spend.was_blocked(out.turn)):  # 第 4 批：跑到一半花费到了上限：记 limit，不算失败
        out = dataclasses.replace(out, status="limit")
    ok = out.status in ("done", "silent", "busy", "unchanged", "gated")  # unchanged：盯着的没变；gated：pre 说这一次不叫醒（第 4 批）
    status = {"done": "ok"}.get(out.status, out.status)
    now = when.now()
    plan = None
    with store.tx() as conn:
        r = conn.execute("SELECT * FROM engine_jobs WHERE id=?", (j["id"],)).fetchone()
        if r is None:
            return
        cur = _job(r)
        if not ok and kind not in OFF_SCHEDULE and out.status not in ("interrupted", "limit") and cur["state"] == "on" and cur["digest"] == j["digest"]:
            d = conn.execute("SELECT due FROM engine_job_runs WHERE id=?", (rid,)).fetchone()
            plan = retry_plan(cur, out, kind, parse_iso(d["due"]) if d else None, now)  # 第 4 批：重试、挂到配额窗口之后（见上面）
        rerun = plan is not None and plan["retry"] is not None  # 再跑一遍这一次（不是只把下一次往后挪）
        held = rerun and bool(plan["retry"].get("hold"))  # 挂到配额窗口之后再来：现在就说一声（可能要等好几个小时），那一次再不行不再说
        if held:
            plan["retry"]["said"] = True
        said = kind == "retry" and bool((cur.get("retry") or {}).get("said"))
        fails = 0 if ok else int(r["fails"] or 0) + (0 if out.status in ("interrupted", "limit") or kind == "retry" else 1)  # 重试那几次不重复算
        state = r["state"]
        once = cur["schedule"].get("kind") == "once"
        if state == "on" and kind not in OFF_SCHEDULE and not rerun and (once or (r["times"] and r["runs"] >= r["times"])):
            state = "done"
        paused = state == "on" and fails >= FAILS_TO_PAUSE and not rerun
        if paused:
            state = "paused"
        error = (out.error or out.status) + plan["note"] if plan else out.error
        conn.execute("UPDATE engine_jobs SET running=NULL, last_run=?, last_status=?, last_error=?, fails=?, state=?, "
                     "next_run=CASE WHEN ?='done' THEN NULL WHEN ? IS NOT NULL THEN ? ELSE next_run END, "
                     "retry=CASE WHEN ? THEN retry ELSE ? END, updated_at=? WHERE id=?",  # 手动跑的、被叫起来的那一次不动等着的重试
                     (iso(now), status, error, fails, state, state, iso(plan["at"]) if plan else None, iso(plan["at"]) if plan else None,
                      int(kind in OFF_SCHEDULE), json.dumps(plan["retry"]) if rerun and state == "on" else None, store.now_iso(), j["id"]))
        conn.execute("UPDATE engine_job_runs SET ended_at=?, status=?, turn=?, message=?, error=?, preview=?, usage=?, output=? WHERE id=?",
                     (iso(when.now()), status, out.turn, out.message, error,
                      (out.text or "")[:PREVIEW_MAX] if out.status == "done" and not j.get("command") else None, json.dumps(out.usage or {}),
                      out.output, rid))
    if (not ok and out.status not in ("interrupted", "limit") and j["session"] != "chat" and fails == 1 and (held or not rerun)
            and not said):  # chat 模式出错时对话里已经有一条「没拿到回复」；要重试的先不说，重试完了还不行才说
        why = (error or out.status)[:240]
        _say(j["deliver"], L(f"定时「{j['name']}」这次没跑成：{why}", f"Scheduled \"{j['name']}\" didn't run this time: {why}"))
    if paused:
        _say(j["deliver"], L(f"定时「{j['name']}」连着 {fails} 次没跑成，先停了；修好了跟它说一声就能恢复",
                             f"Scheduled \"{j['name']}\" failed {fails} times in a row, so it's paused; tell it to resume once it's fixed"))
    if j.get("watch"):  # 盯着的变了：叫醒的那一轮跑成了才记下这一次看到的（没跑成下一次还认得出变了）
        from . import watch
        (watch.commit if out.status in ("done", "silent", "gated") else watch.forget)(j["id"])
    wake()


def _earliest() -> datetime | None:
    """开着的任务里最早的下一次（ISO 的时区偏移不一定一样：解出来按真时间比）。"""
    ready()
    with store.read() as conn:
        rows = conn.execute("SELECT next_run FROM engine_jobs WHERE state='on' AND running IS NULL AND next_run IS NOT NULL").fetchall()
    ts = [t for t in (parse_iso(r["next_run"]) for r in rows) if t]
    return min(ts) if ts else None


async def loop() -> None:
    """服务里一直转：每 ≤30 秒看一遍到点的任务和心跳；有快到的就睡到那一刻，任务改了马上醒。
    claw 不是 builtin 的时候只转、不碰库（换成 builtin 不用重启；第一次是 builtin 时先收拾上次停机时跑到一半的）。"""
    import claw

    from . import flows, heartbeat
    await asyncio.sleep(START_DELAY)
    ev = _loop_bound("wake")
    recovered = False
    while True:
        delay = TICK_MAX
        if claw.is_builtin():
            try:
                if not recovered:
                    recovered = True
                    recover()
                    flows.recover()  # 第 4 批：做到一半的那一步不重做（记 interrupted）
                await estop.enforce()  # 命令行写的 stop_running（engine/estop.py）：服务器这边照做一次
                await tick()
                await heartbeat.tick()
                await flows.tick()  # 第 4 批：停在两步之间的工作流接着走（重启、急停恢复以后）
                nxt = [t for t in (_earliest(), heartbeat.earliest()) if t]
                if nxt:
                    delay = min(TICK_MAX, max(0.5, (min(nxt) - when.now()).total_seconds() + 0.05))
            except Exception as e:  # noqa: BLE001 — 这一遍出错了下一遍接着看
                print(f"[engine] 定时任务这一遍出错：{type(e).__name__}: {e}")
        ev.clear()
        try:
            await asyncio.wait_for(ev.wait(), delay)
        except TimeoutError:
            pass


def start() -> None:
    """服务启动时（main.py 的 lifespan）：开始转。claw 不是 builtin 的时候也转，只是什么都不跑（换成 builtin 不用重启）。"""
    t = asyncio.create_task(loop())
    _bg.add(t)
    t.add_done_callback(_bg.discard)


# —— 工具（主人回合；engine/tools.py 的 for_agent 接上）——————————————————————————————————

ACTIONS = ("list", "create", "update", "pause", "resume", "delete", "run")


def in_job_turn(ctx: ToolCtx) -> bool:
    """这一轮是不是定时任务自己在跑（chat 模式：这一轮的那句话是定时任务发的灰字）。"""
    if ctx.conversation.startswith(("cron:", "heartbeat:")):
        return True
    if not _trigger_refs:
        return False
    with store.read() as conn:
        rows = conn.execute("SELECT data FROM engine_entries WHERE turn=? AND kind='message' AND role='user'", (ctx.turn,)).fetchall()
    return any(json.loads(r["data"]).get("ref") in _trigger_refs for r in rows)


def schema() -> dict:
    return {"type": "object", "required": ["action"], "properties": {
        "action": {"type": "string", "enum": list(ACTIONS), "description": L(
            "list 看你有哪些；create 新建；update 改；pause 暂停；resume 恢复；delete 删；run 马上跑一次（同意过的）",
            "list yours; create; update; pause; resume; delete; run (once now, approved jobs only)")},
        "id": {"type": "string", "description": L("任务 id（job-…）：update / pause / resume / delete / run 要写；先 list，别猜",
                                                  "The job id (job-…) for update / pause / resume / delete / run; list first, never guess")},
        "name": {"type": "string", "description": L("短名字，用户看得懂（「喝水提醒」）", "A short name the user understands (\"Water reminder\")")},
        "when": {"type": "string", "description": L(
            "什么时候：每隔多久「every 2m」「every 2h」；多久以后一次「in 30m」；某个时刻一次「2026-10-17 09:00」「tomorrow 9am」；"
            "每天几点「every day 9am」；哪几天「weekdays 9am」「every monday 9am」「mon,wed 18:00」；cron「0 9 * * 1-5」。按用户的时区",
            "When: interval 'every 2m', 'every 2h'; once after a delay 'in 30m'; once at a time '2026-10-17 09:00', 'tomorrow 9am'; daily "
            "'every day 9am'; some days 'weekdays 9am', 'every monday 9am', 'mon,wed 18:00'; cron '0 9 * * 1-5'. In the user's time zone")},
        "task": {"type": "string", "description": L(
            "到点要做什么：写给到时候的你看的一句完整的话，不靠这次聊天也看得懂（「提醒用户起来喝一杯水」），别写「每两分钟提醒我」",
            "What to do when it runs: a complete instruction for your future self that makes sense without this chat (\"Remind the user to "
            "drink a glass of water\"), not \"remind me every 2 minutes\"")},
        "session": {"type": "string", "enum": list(SESSIONS), "description": L(
            "isolated（默认）：单独跑，不带聊天记录，回复发进 deliver，没要说的可以不说；chat：在 deliver 那个对话当天的会话里跑，看得到今天聊了什么",
            "isolated (default): runs on its own without chat history, posts its reply into deliver and may stay quiet; chat: runs inside "
            "today's conversation in deliver and sees what was said today")},
        "deliver": {"type": "string", "description": L("结果发进哪个对话：不写 = 这个对话（是你的话），否则你自己的对话",
                                                       "Which chat the result goes to: leave out for this chat (if it's yours), else your own chat")},
        "level": {"type": "string", "enum": list(LEVELS), "description": L(
            "推送：quiet（默认，不响，进通知中心）；ring 响铃（到点非叫醒他不可的才用）；none 只发进对话不推送",
            "Push: quiet (default, no sound); ring (only when it really must interrupt); none (only posted in the chat)")},
        "times": {"type": "integer", "description": L("跑几次就停（不写 = 一直跑；一次的不用写）", "Stop after this many runs (leave out = no limit)")},
        "continuity": {"type": "boolean", "description": L("每次带上上一次发的话（不重复、接着往下说）", "Each run sees what the last run posted (no repeats)")},
        "command": {"type": "object", "description": L(
            "只跑命令、不调模型（不写 task）：{\"tool\": 你的命令行工具（board、schedule……，参数和平时调它一样）, \"args\": [一项一个词], "
            "\"input\": 标准输入（可以不写）, \"timeout\": 秒（可以不写）}；或者 {\"script\": 用户在 server.json engine.jobs.scripts 里列的脚本"
            "（绝对路径）, \"args\": […]}。输出记在运行记录里，不发进对话",
            "Run a command instead of the model (no task): {\"tool\": one of your command-line tools (board, schedule…, same args as when you "
            "call it), \"args\": [one word each], \"input\": stdin (optional), \"timeout\": seconds (optional)}; or {\"script\": a script the "
            "user listed in server.json's engine.jobs.scripts (absolute path), \"args\": […]}. Its output goes to the run log, not the chat"),
            "properties": {"tool": {"type": "string"}, "script": {"type": "string"}, "args": {"type": "array", "items": {"type": "string"}},
                           "input": {"type": "string"}, "timeout": {"type": "integer"}}},
        "watch": {"type": "object", "description": L(
            "盯着看（和 task 一起写）：到点先看一眼，第一次只记下来，没变就什么都不做、不调模型，变了才叫醒你（带上变了哪些）。三选一："
            "{\"command\": 同上面 command 的写法} 或 {\"board\": {\"from\": 你的看板表, \"where\": …}}：变了照 task 跑；"
            "{\"url\": \"https://…\", \"match\": 只看匹配这个正则的行（可以不写）, \"action\": \"notify\" | \"card\"}：网页是别人的字，"
            "变了只读一遍照 task 写通知（card = 出一张卡，用户点了你才动手），不会直接叫醒你",
            "Watch something (together with task): each time it looks first; the first look is only remembered, when nothing changed it "
            "does nothing and calls no model, and only a change wakes you (with what changed). One of: {\"command\": same as command above} "
            "or {\"board\": {\"from\": one of your board tables, \"where\": …}}: on a change it runs task; {\"url\": \"https://…\", \"match\": "
            "only lines matching this regex (optional), \"action\": \"notify\" | \"card\"}: a web page is someone else's text, so a change "
            "is only read once to write a notice following task (card = an inbox card; you act only after the user approves), never "
            "waking you directly"),
            "properties": {"command": {"type": "object"}, "board": {"type": "object"}, "url": {"type": "string"}, "match": {"type": "string"},
                           "action": {"type": "string", "enum": ["notify", "card"]}}},
        "pre": {"type": "object", "description": L(
            "调模型之前先跑一条命令（写法同 command）：它最后一行输出 {\"wake\": false} 就这一次不叫醒你；不然它的输出当资料交给你",
            "A command to run before the model (same form as command): if its last output line is {\"wake\": false} you aren't woken this "
            "time; otherwise its output is handed to you as data"),
            "properties": {"tool": {"type": "string"}, "script": {"type": "string"}, "args": {"type": "array", "items": {"type": "string"}},
                           "input": {"type": "string"}, "timeout": {"type": "integer"}}},
        "flow": {"type": "string", "description": L("到点开一次用户在 server.json 里写好的工作流（engine.flows 的名字），代替 task",
                                                    "Start one of the user's workflows (a name from engine.flows in server.json) each time, instead of task")},
        "why": {"type": "string", "description": L("一句话写给用户看：为什么要这个（写在卡上）", "One sentence for the user on why (shown on the card)")}}}


def description(me: str) -> str:
    return L(
        "定时任务：到点你自己做的事。比如 9:00 提醒用户、每两小时看一眼某件事、30 分钟后提醒一次。用户说「提醒我…」「每天…」「过一会儿…」就用它"
        "（日程是用户的日历和待办，用 schedule；看板的提醒是按表推送，用 board alert）。\n"
        "你新建的、改大的（什么时候、做什么、在哪跑、发到哪、推得更响、跑更多次）都先停着，调用会马上回 approval-pending，并给用户发一张卡："
        "他点了「开」才生效，跟他说一声在等他点头就行，别再调。改名字、推得更轻、暂停、恢复他同意过的：直接生效。删掉他同意过的会先问他（一张卡）。\n"
        "到点时没人在看：task 要写成到时候你能直接照做的一句话。默认单独跑（isolated），回复发进 deliver 那个对话并推送；"
        "那一次确实没什么可说的，它会回 [SILENT]，什么都不发。",
        "Scheduled jobs: things you do on your own at set times — remind the user at 9:00, check something every 2 hours, a one-off in 30 "
        "minutes. When the user says \"remind me…\", \"every day…\", \"in a bit…\", use this (the schedule tool is the user's calendar and "
        "to-dos; board alerts push rows from a table).\n"
        "Anything you create, or change in a big way (when, what, where it runs or posts, louder pushes, more runs), stays off: the call "
        "returns approval-pending at once and sends the user a card; it takes effect when they approve. Tell them it's waiting for their "
        "OK and don't call again. Renaming, quieter pushes, pausing, and resuming a job they approved take effect right away. Deleting a job "
        "they approved asks them first (a card).\n"
        "Nobody is watching when it runs, so write task as an instruction you can carry out on the spot. By default it runs on its own "
        "(isolated) and its reply is posted into deliver and pushed; when there's genuinely nothing to say that time, it replies [SILENT] "
        "and nothing is sent.") + L(
        "\n只要到点跑一个命令、用不着想（同步、检查、整理数据）：写 command 不写 task，不调模型，便宜；只能是你自己的命令行工具或者用户列进白名单的"
        "脚本，一样先停着等用户点头。",
        "\nWhen it only needs to run a command at set times, with no thinking (syncing, checking, tidying data): give command instead of "
        "task. It runs no model, so it's cheap. Only your own command-line tools or scripts the user allowlisted can run, and it also waits "
        "for the user's OK.") + L(
        "\n要「变了再告诉我」（看板某张表、某个命令的输出、某个网页）：写 watch，没变就不调模型。",
        "\nFor \"tell me when it changes\" (a board table, a command's output, a web page): add watch, so nothing runs the model until it "
        "changes.") + L(f"\n按你（{me}）的：只看得到、改得了你自己的定时任务。", f"\nRuns as you ({me}): you only see and change your own jobs.")


def tool(me: str) -> Tool:
    async def run(args: dict, ctx: ToolCtx) -> Any:
        action = str(args.get("action") or "").strip().lower()
        who = ctx.agent or "main"
        if action not in ACTIONS:
            raise ToolFailure(L(f"action 只能是 {' / '.join(ACTIONS)}", f"action must be one of {', '.join(ACTIONS)}"))
        if action not in ("list", "pause") and in_job_turn(ctx):
            raise ToolFailure(LS("没做：这一轮是定时任务自己在跑，这里不许建、改、删、跑定时任务（要的话等用户在对话里说）。",
                                 "Not done: this turn is a scheduled job running; jobs can't be created, changed, deleted or run from here "
                                 "(wait for the user to ask in the chat)."))
        jid = str(args.get("id") or "").strip()
        if action not in ("list", "create") and not jid:
            raise ToolFailure(L("要写 id（先 list 看看）", "id is required (list first)"))
        why = str(args.get("why") or "").strip()
        try:
            if action == "list":
                js = [j for j in listing(who) if j["state"] in (*LIVE, *ENDED)]
                return "\n".join(line(j) for j in js) or L("（还没有定时任务）", "(no scheduled jobs yet)")
            if action == "create":
                return await create(who, args, conversation=ctx.conversation, why=why)
            if action == "update":
                return await update(jid, args, agent=who, why=why)
            if action == "pause":
                return LS("暂停了。", "Paused.") + "\n" + line(pause(jid, agent=who))
            if action == "resume":
                return LS("恢复了。", "Resumed.") + "\n" + line(resume(jid, agent=who))
            if action == "delete":
                return await delete(jid, agent=who, why=why)
            await run_now(jid, agent=who)
            j = get(jid)
            if j and j.get("command"):  # 只跑命令的不往对话里发
                return LS("开始跑了（只跑命令）：输出记在运行记录里，list 看得到上一次怎样。",
                          "Started (it only runs a command): its output goes to the run log; list shows how the last run went.")
            return LS(f"开始跑了，结果会发进「{unattended.thread_name(j['deliver'] if j else own_thread(who))}」。",
                      f"Started; the result goes to {unattended.thread_name(j['deliver'] if j else own_thread(who))}.")
        except JobError as e:
            raise ToolFailure(str(e)) from None

    def level(args: dict) -> tuple[str, str]:
        from . import tools as tools_mod
        action = str(args.get("action") or "").strip().lower()
        rule = tools_mod.POLICY[CRON_TOOL].get(action) or tools_mod.UNKNOWN
        lv = tools_mod.levels()
        return ("off", rule.why) if lv.get(f"{CRON_TOOL} {action}") == "off" or lv.get(CRON_TOOL) == "off" else (rule.level, rule.why)

    return Tool(name=CRON_TOOL, description=description(me), schema=schema(), run=run, kinds=("owner",), timeout=60,
                title=lambda args: f"cron {args.get('action') or ''} {args.get('id') or args.get('name') or ''}".strip(), classify=level)


# —— 接口（app 以后用；用户做的直接生效）——————————————————————————————————————————

class JobIn(BaseModel):
    agent: str = "main"
    name: str | None = None
    when: str | None = None
    task: str | None = None
    command: dict | None = None  # 只跑命令的（见文件开头）：{"tool" | "script", "args", "input"?, "timeout"?}
    watch: dict | bool | None = None  # 第 4 批：盯着看（engine/watch.py）：{"command" | "board" | "url": …}；false 去掉
    flow: str | None = None           # 第 4 批：到点开一次工作流（server.json 的 engine.flows，engine/flows.py），代替 task
    pre: dict | bool | None = None    # 第 4 批：调模型前先跑的命令（同 command 的写法）；false 去掉
    session: str | None = None
    deliver: str | None = None
    level: str | None = None
    times: int | None = None
    continuity: bool | None = None
    model: str | None = None


def _bad(e: Exception) -> HTTPException:
    return HTTPException(400, str(e))


@router.get("/api/engine/jobs")
def api_list(agent: str | None = None, all: int = 0):
    return {"ok": True, "jobs": [view(j) for j in listing(agent, everything=bool(all))]}


@router.post("/api/engine/jobs")
async def api_create(body: JobIn):
    import bridges
    if not bridges.known_agent(body.agent):
        raise HTTPException(404, L(f"没有「{body.agent}」这个 Agent", f"No Agent called {body.agent}"))
    try:
        return {"ok": True, **(await create(body.agent, body.model_dump(exclude_none=True), by_user=True))}
    except JobError as e:
        raise _bad(e) from None


@router.get("/api/engine/jobs/proposal/{iid}")
def api_proposal(iid: str):
    got = extra(iid)
    if got is None:
        raise HTTPException(404, L("这张卡不是定时任务的", "This card isn't for a scheduled job"))
    return {"ok": True, **got}


@router.get("/api/engine/jobs/{jid}")
def api_get(jid: str):
    j = get(jid)
    if j is None:
        raise HTTPException(404, L("没有这个定时任务", "No such scheduled job"))
    return {"ok": True, "job": view(j), "runs": runs_of(jid)}


@router.patch("/api/engine/jobs/{jid}")
async def api_update(jid: str, body: JobIn):
    try:
        return {"ok": True, **(await update(jid, body.model_dump(exclude_none=True, exclude={"agent"}), by_user=True))}
    except JobError as e:
        raise _bad(e) from None


@router.post("/api/engine/jobs/{jid}/{action}")
async def api_action(jid: str, action: str):
    try:
        if action == "pause":
            return {"ok": True, "job": view(pause(jid))}
        if action == "resume":
            return {"ok": True, "job": view(resume(jid, by_user=True))}
        if action == "run":
            return await run_now(jid)
    except JobError as e:
        raise _bad(e) from None
    raise HTTPException(404, L("只有 pause / resume / run", "Only pause, resume or run"))


@router.delete("/api/engine/jobs/{jid}")
async def api_delete(jid: str):
    try:
        return await delete(jid, by_user=True)
    except JobError as e:
        raise _bad(e) from None
