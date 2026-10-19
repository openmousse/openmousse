"""心跳（内核第 6 项，claw 选 builtin 时）：每个 Agent 可选的定期「看一眼有没有要告诉你的」，照 OpenClaw 的 heartbeat。

默认关。server.json：
  "engine": {"heartbeat": {
      "main":    {"every": "30m", "active": ["08:00", "22:00"], "level": "quiet"},
      "fitness": {"every": "2h", "prompt": "…清单…", "model": "deepseek/deepseek-flash", "light": false}}}
  每个键是一个 Agent（main 或 Agent id），值可以是 true（全用默认）或一个对象，都能省：
    every   多久看一次（默认 30m，OpenClaw 的默认；"0" / false = 关）
    active  活跃时段 [开始, 结束]（HH:MM，按 server.json 的时区，能跨午夜，结束可以写 24:00；不写 = 全天；写错了 = 全天；
            开始等于结束 = 从不）——OpenClaw 的 activeHours，同样的规矩
    level   有话说时的推送档位（默认 quiet）
    model   默认用这个 Agent 对话的模型；心跳一天几十次，挑个便宜的就省下来了。对话选的是用户自己的套餐（claude-code/…、chatgpt/…）时
            心跳不跟：换成 engine.models 里非套餐的那个（一个都没有就不跑、记成出错）；写明 claude-code/… 才花套餐（chatgpt/… 还要
            用户同意过后台用，engine/unattended.py）
    light   默认 true：开头只放它是谁和规矩，不放工作区文件、档案、世界树、技能目录（OpenClaw 的 lightContext）
    prompt  清单写在这里（代替工作区的 HEARTBEAT.md）
看什么：这个 Agent 工作区的 HEARTBEAT.md（OpenClaw 的心跳清单，用户和它自己都能写），或者 server.json 的 prompt。
不调模型就跳过（省钱；OpenClaw 的几条，外加一条）：
  没有清单（OpenClaw 没有 HEARTBEAT.md 也照跑：它还要处理别的系统事件，我们没有，所以不跑），或者 HEARTBEAT.md 只有标题、空的勾选框、
  注释（OpenClaw 的 isHeartbeatContentEffectivelyEmpty）→ empty；不在活跃时段 → quiet-hours；这个 Agent 有对话正在回话、有定时任务
  正在跑 → busy。跳过了也等下一个周期（OpenClaw：还没开始准备的那次记成 skipped，等下一个 tick），不补。
跑：新会话（engine/unattended.py fresh，OpenClaw 的 isolatedSession，不带聊天记录），那句话是 OpenClaw 的默认心跳提示：照清单看；
  要定期做的事用定时任务、不写在这里；别从以前的聊天里推断、重提旧事；没要用户注意的就只回 NO_REPLY。
  回 NO_REPLY（或者 HEARTBEAT_OK 式的确认、剩下不到 300 字）→ 什么都不发、不推送，对话里不留；
  有话 → 发进这个 Agent 的对话（一行灰字「主动看了一眼」+ 回复，按档位推送，再给它今天的会话补一条 system）。
表 engine_heartbeats：每个 Agent 一行（下一次、上一次、上一次怎样、为什么跳过）；调了模型的那几次也记进 engine_job_runs（job = heartbeat:<Agent>，
kind = heartbeat，不算定时任务的每日上限）。
OpenClaw 自己的心跳（openclaw.json 的 agents.defaults.heartbeat）一点不动。
"""
from __future__ import annotations

import asyncio
import json
import re
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from config import raw, settings
from i18n import L, LS

from . import models, store, unattended, when

DEFAULT_EVERY = 30   # 分钟（OpenClaw 的 DEFAULT_HEARTBEAT_EVERY）
SCHEMA = """
CREATE TABLE IF NOT EXISTS engine_heartbeats (
  agent TEXT PRIMARY KEY, every INTEGER, next_run TEXT, last_run TEXT, last_status TEXT, last_reason TEXT, updated_at TEXT);
"""
_ready: set[str] = set()
_running: dict[str, asyncio.Task] = {}
HM = re.compile(r"^(?:([01]\d|2[0-3]):([0-5]\d)|24:00)$")


def ready() -> None:
    path = str(settings.db)
    if path in _ready:
        return
    with store.tx() as conn:
        conn.executescript(SCHEMA)
    _ready.add(path)


# —— 设置 ————————————————————————————————————————————————————————————————

def _minutes(v) -> int:
    if v in (None, ""):
        return DEFAULT_EVERY
    if v is False:
        return 0
    if isinstance(v, (int, float)):
        return max(0, int(v))
    s = str(v).strip().lower()
    if s in ("0", "0m", "off", "false"):
        return 0
    try:
        return when.parse_duration(s)
    except when.WhenError:
        return DEFAULT_EVERY


def _hm(s) -> int | None:
    m = HM.match(str(s or "").strip())
    if not m:
        return None
    return 24 * 60 if m.group(0) == "24:00" else int(m.group(1)) * 60 + int(m.group(2))


def config() -> dict[str, dict]:
    """server.json 里开了心跳的 Agent → 设置（见文件开头）。"""
    e = raw().get("engine")
    hb = e.get("heartbeat") if isinstance(e, dict) else None
    out: dict[str, dict] = {}
    for agent, v in (hb.items() if isinstance(hb, dict) else []):
        if v is True:
            v = {}
        if not isinstance(v, dict):
            continue
        every = _minutes(v.get("every"))
        if every <= 0:
            continue
        active = v.get("active")
        lv = str(v.get("level") or "quiet").lower()
        out[str(agent)] = {"every": every, "active": list(active) if isinstance(active, (list, tuple)) and len(active) == 2 else None,
                           "level": lv if lv in ("ring", "quiet", "none") else "quiet", "model": str(v.get("model") or "").strip() or None,
                           "light": v.get("light") is not False, "prompt": str(v.get("prompt") or "").strip()}
    return out


def in_active_hours(active: list | None, at: datetime) -> bool:
    """OpenClaw 的 isWithinActiveHours：不写 = 全天；写错了 = 全天；开始 = 结束 = 从不；结束能写 24:00；能跨午夜。"""
    if not active:
        return True
    a, b = _hm(active[0]), _hm(active[1])
    if a is None or b is None or a == 24 * 60:
        return True
    if a == b:
        return False
    t = when.local(at)
    cur = t.hour * 60 + t.minute
    return a <= cur < b if b > a else (cur >= a or cur < b)


# —— 清单 ————————————————————————————————————————————————————————————————

def effectively_empty(text: str) -> bool:
    """OpenClaw 的 isHeartbeatContentEffectivelyEmpty：只有空行、注释（<!-- -->）、标题（#）、``` 围栏、空的列表项 / 勾选框。"""
    in_comment = False
    for line in text.split("\n"):
        while in_comment or line.lstrip().startswith("<!--"):
            search = line if in_comment else line.lstrip()
            end = search.find("-->")
            in_comment = end == -1
            if in_comment:
                line = ""
                break
            line = search[end + 3:]
        t = line.strip()
        if not t or re.match(r"^#+(\s|$)", t) or re.match(r"^[-*+]\s*(\[[\sXx]?\]\s*)?$", t) or re.match(r"^```[A-Za-z0-9_-]*$", t):
            continue
        return False
    return True


def checklist(agent: str, conf: dict) -> tuple[str, str] | None:
    """(清单, 从哪来)：server.json 的 prompt 优先，否则工作区的 HEARTBEAT.md；没有、或者实际是空的 → None（不调模型）。"""
    if conf.get("prompt"):
        return conf["prompt"], "server.json"
    from .context import workspace_of
    ws = workspace_of(agent)
    p = Path(ws) / "HEARTBEAT.md" if ws else None
    if p is None or not p.is_file() or p.is_symlink():
        return None
    try:
        text = p.read_text(encoding="utf8", errors="replace")[:8000]
    except OSError:
        return None
    return None if effectively_empty(text) else (text.strip(), "HEARTBEAT.md")


# —— 状态 ————————————————————————————————————————————————————————————————

def state(agent: str) -> dict | None:
    ready()
    with store.read() as conn:
        r = conn.execute("SELECT * FROM engine_heartbeats WHERE agent=?", (agent,)).fetchone()
    return dict(r) if r else None


def _save(agent: str, **cols) -> None:
    cols["updated_at"] = store.now_iso()
    keys = ["agent", *cols]
    with store.tx() as conn:
        conn.execute(f"INSERT INTO engine_heartbeats({', '.join(keys)}) VALUES({', '.join('?' * len(keys))}) ON CONFLICT(agent) DO UPDATE SET "
                     + ", ".join(f"{k}=excluded.{k}" for k in cols), (agent, *cols.values()))


def earliest() -> datetime | None:
    """开着的心跳里最早的下一次（ticker 睡多久用）。"""
    conf = config()
    if not conf:
        return None
    ready()
    with store.read() as conn:
        rows = conn.execute("SELECT agent, next_run FROM engine_heartbeats").fetchall()
    ts = [when.local(datetime.fromisoformat(r["next_run"])) for r in rows if r["agent"] in conf and r["next_run"]]
    return min(ts) if ts else None


def busy(agent: str) -> bool:
    """这个 Agent 有对话正在回话、或者有它的定时任务正在跑（OpenClaw：requests-in-flight / cron-in-progress 时不打扰）。"""
    import chat

    from . import jobs
    from .owner import agent_of
    if any(not r.done and agent_of(t) == agent for t, r in list(chat.RUNS.items())):
        return True
    return any((j := jobs.get(jid)) is not None and j["agent"] == agent for jid in list(jobs._running))


# —— 到点 ————————————————————————————————————————————————————————————————

def prompt_for(agent: str, conf: dict, text: str, source: str, at: datetime) -> str:
    """OpenClaw 的默认心跳提示（HEARTBEAT_PROMPT），加上清单。按 server.json 的语言。"""
    where = unattended.thread_name(jobs_own_thread(agent))
    zh = (f"【心跳 · {at:%H:%M}】这是定时的「看一眼」，不是用户发来的话。照下面的清单看一遍。要定期做的事是定时任务（cron 工具，这里没有），"
          "别写在清单里。别从以前的聊天里推断或重提旧事。现在没有要用户注意的，就只回 NO_REPLY（照原样写这个英文记号），别的什么都不写；"
          f"有，就只写要告诉他的那几句：会发进「{where}」并推送给他。\n\n清单（{source}）：\n{text}")
    en = (f"[Heartbeat · {at:%H:%M}] This is a scheduled check-in, not a message from the user. Follow the checklist below. Recurring work "
          "belongs in scheduled jobs (the cron tool, not available here), not in the checklist. Do not infer or repeat old tasks from earlier "
          "chats. If nothing needs the user's attention right now, reply with exactly NO_REPLY and nothing else; otherwise write only what "
          f"they should see: it is posted into \"{where}\" and pushed to them.\n\nChecklist ({source}):\n{text}")
    return LS(zh, en)


def jobs_own_thread(agent: str) -> str:
    return "main" if agent == "main" else agent


async def tick(at: datetime | None = None) -> list[str]:
    """看一遍到点的心跳。→ 这一次要调模型的 Agent（在后台跑，settle() 等它们跑完）。只在 builtin 时跑。"""
    import bridges
    import claw
    if not claw.is_builtin():
        return []
    conf = config()
    if not conf:
        return []
    ready()
    at = at or when.now()
    ran: list[str] = []
    for agent, c in conf.items():
        st = state(agent)
        nxt = when.parse_iso(st["next_run"]) if st else None
        if st is None or nxt is None or (st["every"] or 0) != c["every"]:  # 第一次、或者改了间隔：从现在起排
            _save(agent, every=c["every"], next_run=when.iso(at + timedelta(minutes=c["every"])))
            continue
        if nxt > at or agent in _running:
            continue
        follow = when.iso(at + timedelta(minutes=c["every"]))
        reason = None
        if not bridges.known_agent(agent):
            reason = "no-agent"
        elif not in_active_hours(c["active"], at):
            reason = "quiet-hours"
        elif busy(agent):
            reason = "busy"
        got = None if reason else checklist(agent, c)
        if not reason and got is None:
            reason = "empty"
        if reason:
            _save(agent, next_run=follow, last_status="skipped", last_reason=reason)
            continue
        _save(agent, next_run=follow, last_run=when.iso(at), last_status="running", last_reason=None)
        task = asyncio.create_task(_go(agent, c, got[0], got[1], at))
        _running[agent] = task
        task.add_done_callback(lambda t, a=agent: _running.pop(a, None) if _running.get(a) is t else None)
        ran.append(agent)
    return ran


async def _go(agent: str, c: dict, text: str, source: str, at: datetime) -> None:
    try:
        status = await run(agent, c, text, source, at)
    except Exception as e:  # noqa: BLE001 — 一次出错不影响下一次
        status = "error"
        print(f"[engine] {agent} 的心跳出错：{type(e).__name__}: {e}")
    _save(agent, last_status=status)


async def settle(timeout: float = 30) -> None:
    """等正在跑的心跳都跑完（测试用）。"""
    tasks = list(_running.values())
    if tasks:
        await asyncio.wait(tasks, timeout=timeout)


def model_of(agent: str, c: dict) -> str:
    """这一次用哪个模型：心跳上写了的用它（写 claude-code/… 就花用户的套餐，他自己定的）；没写跟这个 Agent 对话的模型，但不跟套餐：
    对话选的是 claude-code/… 就换成非套餐的（models.outside），一个都没有 → ""（这一次记成出错，不花套餐）。定时任务照旧跟对话的模型。"""
    from . import jobs
    if c["model"] and models.known(c["model"]):
        return c["model"]
    if bg := models.background_model():  # 第 9 步「后台和定时」选的：用户自己定的，套餐也照用
        return bg
    m = jobs.model_for(jobs_own_thread(agent), None)
    return (models.outside(m) or "") if models.subscription(m) else m


async def run(agent: str, c: dict, text: str, source: str, at: datetime) -> str:
    """调一次模型（新会话），有话就发。→ 状态（ok / silent / error / timeout）。"""
    from . import jobs
    thread = jobs_own_thread(agent)
    rid = "jr-" + uuid.uuid4().hex[:10]
    with store.tx() as conn:
        conn.execute("INSERT INTO engine_job_runs(id, job, agent, due, started_at, kind, status) VALUES(?,?,?,?,?,?,?)",
                     (rid, f"heartbeat:{agent}", agent, when.iso(at), when.iso(at), "heartbeat", "running"))
    model = model_of(agent, c)
    fm, im = unattended.marks()
    if not model:
        out = unattended.Outcome(status="error", error=L(
            f"心跳没写模型，这个对话用的又是你自己的套餐（claude-code/…、chatgpt/…）：心跳默认不花套餐。在 engine.heartbeat.{agent}.model "
            "写一个模型（写 claude-code/… 就用套餐），或者在 engine.models 里加一个非套餐的",
            f"The heartbeat has no model and this chat uses your own subscription (claude-code/… or chatgpt/…), which heartbeats don't "
            f"spend by default. Set engine.heartbeat.{agent}.model (claude-code/… to use the subscription) or add a non-subscription model "
            "to engine.models"))
    else:
        out = await unattended.fresh(agent, thread, f"heartbeat:{agent}", prompt_for(agent, c, text, source, at), model=model,
                                     light=c["light"], idle=min(jobs.idle_timeout(), max(60.0, c["every"] * 60.0)))
    if out.status == "done":
        stamp = when.now().strftime("%H:%M")
        label = L("主动看了一眼", "Checked in on its own")
        gw = f"[Heartbeat at {stamp}: you looked in on your own and posted the message below in this chat.]"
        note = unattended.mirror_note(f"[{stamp}] Your heartbeat check-in (a separate session following your checklist) posted this in this chat:",
                                      out.text)
        out.message = await unattended.post(thread, label, gw, out.text, model=out.model, level=c["level"], tag=f"heartbeat:{agent}", note=note,
                                            feed_mark=fm, inbox_mark=im, served=out.served)
    status = {"done": "ok"}.get(out.status, out.status)
    with store.tx() as conn:
        conn.execute("UPDATE engine_job_runs SET ended_at=?, status=?, turn=?, message=?, error=?, preview=?, usage=? WHERE id=?",
                     (store.now_iso(), status, out.turn, out.message, out.error, (out.text or "")[:2000] if out.status == "done" else None,
                      json.dumps(out.usage or {}), rid))
    return status


def status() -> dict:
    """GET /api/engine/heartbeat：哪些 Agent 开了、下一次、上一次怎样。"""
    conf = config()
    out = {}
    for agent, c in conf.items():
        st = state(agent) or {}
        out[agent] = {**c, "checklist": (checklist(agent, c) or (None, None))[1], "nextRun": st.get("next_run"), "lastRun": st.get("last_run"),
                      "lastStatus": st.get("last_status"), "lastReason": st.get("last_reason")}
    return out
