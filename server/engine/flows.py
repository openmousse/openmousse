"""轻量的多步工作流（第 4 批，7.14）：主人在 server.json 里写好几步，一步一步往下走；不是一个大框架。

server.json：
  "engine": {"flows": {"weekly-review": {"agent": "main", "thread": "main", "level": "quiet", "steps": [
      {"run": {"tool": "board", "args": ["rows", "query", "tasks"]}},
      {"do": "Summarise what got done this week from the rows above."},
      {"ask": "Post the summary to the team chat?"},
      {"do": "Post the summary to the team chat."}]}}}
  一步是三种之一：
    do   主人写的一句要做的事：在 thread 那个对话里开一轮主人回合（engine/unattended.in_chat，看得到今天聊了什么，前面几步的结果都在里面）；
         前面 run 那几步的输出包成资料接在后面。回「没要说的」也算这一步做完了。
    run  跑一条命令（和只跑命令的定时任务同一个规矩：BRIDGES 的工具或 engine.jobs.scripts 白名单里的脚本，engine/watch.run）；
         退出码不是 0 算这一步没做成，整个停下（failed）；输出留给后面的 do。
    ask  出一张收件箱卡（kind flow）问主人：点了「接着做」往下走（写了一句的话这句交给下一步）；拒绝 → 停（stopped）。等着的时候重启也不丢。
  每一步开始前、做完以后都记进库（engine_flow_runs：做到第几步、这一步开始了没有、各步的结果）：
    服务重启：停在两步之间的、等着点头的接着走；做到一半的那一步不重做（可能已经做了事），记成 interrupted，POST …/resume 才从那一步再来。
  急停（engine/estop.py）：走到下一步之前看一眼，停着就停在那里（status 还是 running），恢复了接着走；--stop-running 停掉正在做的那一步，
    记成 interrupted（不算失败）。
  定时任务也能跑它（engine/jobs.py：调模型的任务写 flow 代替 task）：到点开一次（同一个工作流已经有一次在走就不再开）。
接口（只认主人的设备）：GET /api/engine/flows（有哪些、配得对不对、最近几次）、POST /api/engine/flows/{名字}/start、
  GET /api/engine/flows/runs/{id}、POST …/runs/{id}/stop、POST …/runs/{id}/resume。
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from i18n import L, LS

from . import estop, store

MAX_STEPS = 20
MAX_LIVE = 3                  # 同时最多走几次
OUTPUT_MAX = 8000             # 留每一步多少字的结果
KINDS = ("do", "run", "ask")
LIVE = ("running", "waiting")
SCHEMA = """
CREATE TABLE IF NOT EXISTS engine_flow_runs (
  id TEXT PRIMARY KEY, flow TEXT NOT NULL, agent TEXT NOT NULL, thread TEXT NOT NULL, steps TEXT NOT NULL, at INTEGER NOT NULL DEFAULT 0,
  started INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL, outputs TEXT NOT NULL DEFAULT '[]', note TEXT, inbox TEXT, error TEXT,
  by TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS engine_flow_runs_status ON engine_flow_runs(status);
CREATE INDEX IF NOT EXISTS engine_flow_runs_inbox ON engine_flow_runs(inbox);
"""
_ready: set[str] = set()
_live: dict[str, asyncio.Task] = {}


def ready() -> None:
    from config import settings
    path = str(settings.db)
    if path in _ready:
        return
    with store.tx() as conn:
        conn.executescript(SCHEMA)
    _ready.add(path)


# —— 配置 ————————————————————————————————————————————————————————————————————

def entries() -> dict:
    from . import models
    v = models.cfg().get("flows")
    return v if isinstance(v, dict) else {}


def clean(name: str, e: Any) -> dict:
    """一个工作流 → {"name", "agent", "thread", "level", "steps": [{"kind", "text" | "command"}], "problem"}。不抛：配错的列出来、不能开。"""
    import bridges

    from . import jobs
    f: dict[str, Any] = {"name": name, "steps": [], "problem": None}
    if not isinstance(e, dict):
        return {**f, "problem": L("要写成一个对象", "It must be an object")}
    f["agent"] = str(e.get("agent") or "main").strip()
    f["thread"] = str(e.get("thread") or ("main" if f["agent"] == "main" else f["agent"])).strip()
    f["level"] = e.get("level") if e.get("level") in ("ring", "quiet", "none") else "quiet"
    problems: list[str] = []
    if not bridges.known_agent(f["agent"]):
        problems.append(L(f"没有「{f['agent']}」这个 Agent", f"No Agent called {f['agent']}"))
    raw = e.get("steps")
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_STEPS:
        problems.append(L(f"steps 写 1 到 {MAX_STEPS} 步", f"steps takes 1 to {MAX_STEPS} steps"))
        raw = []
    for i, s in enumerate(raw, 1):
        kind = next((k for k in KINDS if isinstance(s, dict) and k in s), None)
        if kind is None or sum(k in s for k in KINDS) != 1:
            problems.append(L(f"第 {i} 步写 do / run / ask 之一", f"Step {i} takes one of do, run or ask"))
            continue
        if kind == "run":
            try:
                f["steps"].append({"kind": "run", "command": jobs.clean_command(f["agent"], s["run"])})
            except jobs.JobError as ex:
                problems.append(L(f"第 {i} 步：{ex}", f"Step {i}: {ex}"))
            continue
        text = " ".join(str(s[kind] or "").split()) if kind == "ask" else str(s[kind] or "").strip()
        if not text or len(text) > 2000:
            problems.append(L(f"第 {i} 步要写一句话（最多 2000 字）", f"Step {i} needs a sentence (at most 2000 characters)"))
            continue
        f["steps"].append({"kind": kind, "text": text})
    f["problem"] = L("；", "; ").join(problems) or None
    return f


def flow(name: str) -> dict | None:
    e = entries().get(name)
    return None if e is None else clean(name, e)


def step_text(s: dict) -> str:
    """一步给人看的样子。"""
    from . import jobs
    if s["kind"] == "run":
        return L(f"跑 {jobs.command_text(s['command'])}", f"run {jobs.command_text(s['command'])}")
    return L(f"问你：{s['text']}", f"ask you: {s['text']}") if s["kind"] == "ask" else s["text"]


# —— 一次 ————————————————————————————————————————————————————————————————————

def _row(r) -> dict:
    d = dict(r)
    d["steps"] = json.loads(d["steps"])
    d["outputs"] = json.loads(d["outputs"] or "[]")
    return d


def get(rid: str) -> dict | None:
    ready()
    with store.read() as conn:
        r = conn.execute("SELECT * FROM engine_flow_runs WHERE id=?", (rid,)).fetchone()
    return _row(r) if r else None


def _set(rid: str, **cols: Any) -> None:
    cols["updated_at"] = store.now_iso()
    if "outputs" in cols:
        cols["outputs"] = json.dumps(cols["outputs"], ensure_ascii=False)
    with store.tx() as conn:
        conn.execute("UPDATE engine_flow_runs SET " + ", ".join(f"{k}=?" for k in cols) + " WHERE id=?", (*cols.values(), rid))


def runs(limit: int = 20, flow_name: str | None = None) -> list[dict]:
    ready()
    q = "SELECT * FROM engine_flow_runs" + (" WHERE flow=?" if flow_name else "") + " ORDER BY julianday(created_at) DESC, rowid DESC LIMIT ?"
    with store.read() as conn:
        rows = conn.execute(q, (flow_name, limit) if flow_name else (limit,)).fetchall()
    return [_row(r) for r in rows]


def view(r: dict) -> dict:
    return {"id": r["id"], "flow": r["flow"], "agent": r["agent"], "thread": r["thread"], "status": r["status"], "at": r["at"],
            "steps": [step_text(s) for s in r["steps"]], "inbox": r["inbox"], "error": r["error"], "by": r["by"], "createdAt": r["created_at"],
            "updatedAt": r["updated_at"], "outputs": [str(o or "")[:300] for o in r["outputs"]]}


class FlowError(ValueError):
    """开不了、不许：原话回给 app（400 / 409）或定时任务（出错）。"""


class FlowBusy(FlowError):
    """同一个工作流已经有一次在走（定时任务记成 busy，不算失败）。"""


def start(name: str, *, by: str = "user") -> dict:
    """开一次（记进库，马上在后台走）。同一个工作流已经有一次在走（还没做完、在等点头）→ FlowError。"""
    import claw
    if not claw.is_builtin():
        raise FlowError(L("工作流只在自己的内核下有（claw 是 builtin）", "Workflows need the built-in engine"))
    if estop.paused():
        raise FlowError(estop.notice())
    f = flow(name)
    if f is None:
        raise FlowError(L(f"没有「{name}」这个工作流（server.json 的 engine.flows）", f"No workflow called {name} (engine.flows in server.json)"))
    if f["problem"]:
        raise FlowError(f["problem"])
    ready()
    rid = "fr-" + uuid.uuid4().hex[:10]
    now = store.now_iso()
    with store.tx() as conn:
        if conn.execute("SELECT 1 FROM engine_flow_runs WHERE flow=? AND status IN ('running','waiting')", (name,)).fetchone():
            raise FlowBusy(L(f"「{name}」已经有一次在走了", f"{name} is already running"))
        conn.execute("INSERT INTO engine_flow_runs(id, flow, agent, thread, steps, status, note, by, created_at, updated_at) "
                     "VALUES(?,?,?,?,?,?,?,?,?,?)", (rid, name, f["agent"], f["thread"], json.dumps(f["steps"], ensure_ascii=False), "running",
                                                     json.dumps({"level": f["level"]}), by, now, now))
    _go(rid)
    return get(rid) or {}


def _go(rid: str) -> None:
    """在后台往下走（已经在走的不再起）。"""
    if rid in _live and not _live[rid].done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # 没有事件循环（命令行）：等服务里的 tick 接着走
    t = loop.create_task(advance(rid))
    _live[rid] = t

    def done(_t: asyncio.Task, r: str = rid) -> None:
        if _live.get(r) is _t:
            _live.pop(r, None)
    t.add_done_callback(done)


def _meta(r: dict) -> dict:
    """note 栏：{"level": 推送档位, "ask_note": 点头时补的那一句（只给下一步）}。"""
    try:
        v = json.loads(r.get("note") or "{}")
    except ValueError:
        v = {}
    return v if isinstance(v, dict) else {}


def _level(r: dict) -> str:
    return _meta(r).get("level") or "quiet"


async def advance(rid: str) -> str:
    """从记下的那一步往下走，直到做完、等点头、出错、停下或者急停。→ 最后的状态。"""
    while True:
        r = get(rid)
        if r is None or r["status"] != "running":
            return r["status"] if r else "missing"
        meta = _meta(r)
        if estop.paused():  # 急停：停在这一步前面（status 还是 running），恢复了 tick 接着走
            return "running"
        if r["at"] >= len(r["steps"]):
            _set(rid, status="done")
            return "done"
        i, s = r["at"], r["steps"][r["at"]]
        _set(rid, started=1)
        try:
            ok, out = await _step(r, i, s)
        except asyncio.CancelledError:
            _set(rid, status="interrupted", error="stopped")
            raise
        except Exception as e:  # noqa: BLE001 — 一步出了意外：停下，说清楚
            ok, out = False, f"{type(e).__name__}: {str(e)[:200]}"
        outputs = list(r["outputs"]) + [None] * (i + 1 - len(r["outputs"]))
        outputs[i] = (out or "")[:OUTPUT_MAX] if ok else None
        if s["kind"] == "ask" and ok:
            _set(rid, status="waiting", started=0, inbox=out, outputs=outputs)
            return "waiting"
        if not ok:
            _set(rid, status="failed", started=0, error=(out or "")[:300], outputs=outputs)
            _say(r, L(f"工作流「{r['flow']}」第 {i + 1} 步没做成，停下了：{(out or '')[:160]}", f"Workflow \"{r['flow']}\" stopped at step {i + 1}: "
                      f"{(out or '')[:160]}"))
            return "failed"
        _set(rid, at=i + 1, started=0, outputs=outputs, error=None, note=json.dumps({"level": meta.get("level") or "quiet"}))  # 点头时补的那句只给下一步


async def _step(r: dict, i: int, s: dict) -> tuple[bool, str]:
    """做一步 → (做成没有, 结果 / 出错的一句；ask 那一步回卡的 id)。"""
    from . import unattended, watch
    if s["kind"] == "run":
        return await watch.run(r["agent"], s["command"])
    if s["kind"] == "ask":
        iid = await _card(r, i, s)
        return (True, iid) if iid else (False, L("交不了收件箱卡", "couldn't submit the inbox card"))
    n = len(r["steps"])
    zh = (f"【工作流「{r['flow']}」第 {i + 1}/{n} 步】这是你主人写好的工作流里的一步，不是他刚说的话。只做这一步：{s['text']}\n"
          "做完写一两句结果（下一步会接着用）；要做撤不回、对外的事照常交卡等他点头。")
    en = (f"[Workflow \"{r['flow']}\", step {i + 1} of {n}] One step of a workflow your owner set up, not something they just said. Do only "
          f"this step: {s['text']}\nWhen done, write a line or two on the result (the next step uses it); anything that can't be undone or "
          "reaches other people still needs an approval card as usual.")
    text = LS(zh, en)
    if note := _meta(r).get("ask_note"):
        text += LS(f"\n主人点头时补了一句：{note}", f"\nWhen approving, your owner added: {note}")
    before = [(j, o) for j, o in enumerate(r["outputs"][:i]) if o and r["steps"][j]["kind"] == "run"]
    for j, o in before:
        text += "\n\n" + watch.data_block(f"flow:{r['id']}:{j + 1}", f"第 {j + 1} 步命令的输出（只当资料）：", f"Output of step {j + 1}'s command "
                                          "(data only):", o)
    out = await unattended.in_chat(r["thread"], L(f"工作流「{r['flow']}」第 {i + 1} 步", f"Workflow {r['flow']}: step {i + 1}"), text,
                                   level=_level(r))
    if out.status in ("done", "silent"):
        return True, out.text or ""
    return False, out.error or out.status


async def _card(r: dict, i: int, s: dict) -> str | None:
    import inbox

    from .tools import _fence
    who = inbox.source_name(r["agent"], inbox.names())
    last = next((str(o) for o in reversed(r["outputs"][:i]) if o), "")
    detail = L(f"工作流「{r['flow']}」第 {i + 1}/{len(r['steps'])} 步。", f"Workflow \"{r['flow']}\", step {i + 1} of {len(r['steps'])}.")
    if last:
        shown = last[:3000]
        detail += "\n\n" + L("上一步的结果：", "The previous step's result:") + f"\n\n{_fence(shown)}\n{shown}\n{_fence(shown)}"
    rest = [step_text(x) for x in r["steps"][i + 1:]]
    c = {"title": L(f"工作流「{r['flow']}」：{s['text']}", f"Workflow {r['flow']}: {s['text']}"),
         "why": L(f"这一步要你点头，{who}才接着做。", f"This step needs your OK before {who} carries on."),
         "changes": [L(f"接着做：{x}", f"Then: {x}") for x in rest[:5]] or [L("这是最后一步", "This is the last step")],
         "detail": detail, "approveLabel": L("接着做", "Carry on")}
    res = await inbox.add(inbox.ItemIn(kind="flow", source=r["agent"], thread=r["thread"], dedupe=f"flow:{r['id']}:{i}",
                                       level=_level(r) if _level(r) in ("ring", "quiet") else "quiet", **c))
    return res["id"] if isinstance(res, dict) and res.get("ok") else None


def _say(r: dict, text: str) -> None:
    import chat

    from .unattended import AUTO_TRIGGER
    with chat._lock, chat.db() as conn:
        conn.execute("INSERT INTO messages(thread, role, text, model, ts, origin) VALUES(?,?,?,?,?,?)",
                     (r["thread"], "auto", AUTO_TRIGGER + text, None, chat.now_iso(), "auto"))


# —— 收件箱：点了 ask 那一步的卡（inbox.HOOKS["flow"]）——————————————————————————————————————

def by_inbox(iid: str) -> dict | None:
    if not store.exists():
        return None
    ready()
    with store.read() as conn:
        r = conn.execute("SELECT * FROM engine_flow_runs WHERE inbox=? ORDER BY julianday(updated_at) DESC LIMIT 1", (iid,)).fetchone()
    return _row(r) if r else None


async def on_decided(it: dict, action: str) -> dict | None:
    """接着做（「改一下」写的那句也当点头时补的一句）→ 往下走；拒绝、撤回 → 停。不是工作流的卡 → None。"""
    r = by_inbox(it["id"])
    if r is None:
        return None
    if r["status"] != "waiting":
        return {"silent": True, "failed": L("这次工作流已经不在等了", "This workflow run isn't waiting anymore")}
    if action in ("reject", "withdraw"):
        _set(r["id"], status="stopped", inbox=None, error=L("你没让它接着做", "you said no"))
        return None
    if action not in ("approve", "revise"):
        return None
    note = " ".join(str(it.get("note") or "").split())[:1000] or None
    _set(r["id"], status="running", at=r["at"] + 1, inbox=None, note=json.dumps({"level": _level(r), "ask_note": note}))
    _go(r["id"])
    done = L("接着做了", "carrying on")
    return {"silent": True, "result": done, **({"status": "done"} if action == "revise" else {})}


def install() -> None:
    import inbox
    inbox.HOOKS["flow"] = on_decided


# —— 重启、急停、定时 ————————————————————————————————————————————————————————————————

def recover() -> int:
    """服务刚起来（engine/jobs.loop 第一遍）：做到一半的那一步不重做，记成 interrupted；停在两步之间的 tick 接着走。→ 几次。"""
    if not store.exists():
        return 0
    ready()
    with store.tx() as conn:
        return conn.execute("UPDATE engine_flow_runs SET status='interrupted', started=0, error=?, updated_at=? WHERE status='running' AND started=1",
                            ("the server restarted during this step", store.now_iso())).rowcount


async def tick() -> list[str]:
    """（engine/jobs.loop 每一遍）停在两步之间、没在走的接着走（重启以后、急停恢复以后）。→ 这一遍接着走的。"""
    if estop.paused() or not store.exists():
        return []
    ready()
    with store.read() as conn:
        ids = [r["id"] for r in conn.execute("SELECT id FROM engine_flow_runs WHERE status='running' AND started=0 ORDER BY created_at").fetchall()]
    out = []
    for rid in ids:
        if rid in _live and not _live[rid].done():
            continue
        if sum(1 for t in _live.values() if not t.done()) >= MAX_LIVE:
            break
        _go(rid)
        out.append(rid)
    return out


async def stop(rid: str) -> dict:
    """停下这一次（正在做的那一步也停掉；等着的卡撤掉）。"""
    r = get(rid)
    if r is None:
        raise FlowError(L("没有这一次", "No such run"))
    if r["status"] not in (*LIVE, "interrupted"):
        return r
    if (t := _live.pop(rid, None)) is not None and not t.done():
        t.cancel()
        await asyncio.gather(t, return_exceptions=True)
    if r["inbox"]:
        import inbox
        with contextlib.suppress(HTTPException):
            await inbox.withdraw(r["inbox"])
    _set(rid, status="stopped", started=0, inbox=None, error=L("你停的", "stopped by you"))
    return get(rid) or r


async def stop_running() -> int:
    """急停的 --stop-running（engine/estop.py）：正在做的那一步都停掉，记成 interrupted（不算失败）。→ 停了几个。"""
    n = 0
    for rid, t in list(_live.items()):
        if not t.done():
            t.cancel()
            await asyncio.gather(t, return_exceptions=True)
            n += 1
        _live.pop(rid, None)
    return n


def resume(rid: str) -> dict:
    """被打断的（重启、急停停掉的）从停下的那一步再来；急停着不行。"""
    if estop.paused():
        raise FlowError(estop.notice())
    r = get(rid)
    if r is None:
        raise FlowError(L("没有这一次", "No such run"))
    if r["status"] != "interrupted":
        raise FlowError(L(f"这一次是 {r['status']}：只有被打断的能接着走", f"This run is {r['status']}: only an interrupted run can resume"))
    _set(rid, status="running", started=0, error=None)
    _go(rid)
    return get(rid) or r


async def settle(timeout: float = 30) -> None:
    """等正在走的都停下（测试用）。"""
    for _ in range(50):
        live = [t for t in list(_live.values()) if not t.done()]
        if not live:
            return
        await asyncio.wait(live, timeout=timeout)


# —— 接口（只认主人的设备）——————————————————————————————————————————————————————————

def _owner(request: Request) -> str:
    from . import setup_api
    return setup_api.owner(request)


router = APIRouter(dependencies=[Depends(_owner)])


@router.get("/api/engine/flows")
def api_list():
    out = []
    for name, e in entries().items():
        f = clean(str(name), e)
        out.append({"name": f["name"], "agent": f.get("agent"), "thread": f.get("thread"), "steps": [step_text(s) for s in f["steps"]],
                    "problem": f["problem"]})
    return {"ok": True, "flows": out, "runs": [view(r) for r in runs()] if store.exists() else [], "paused": estop.paused()}


@router.post("/api/engine/flows/{name}/start")
async def api_start(name: str):
    try:
        return {"ok": True, "run": view(start(name, by="user"))}
    except FlowError as e:
        raise HTTPException(409, str(e)) from None


@router.get("/api/engine/flows/runs/{rid}")
def api_get(rid: str):
    r = get(rid)
    if r is None:
        raise HTTPException(404, L("没有这一次", "No such run"))
    return {"ok": True, "run": view(r)}


@router.post("/api/engine/flows/runs/{rid}/{action}")
async def api_action(rid: str, action: str):
    try:
        if action == "stop":
            return {"ok": True, "run": view(await stop(rid))}
        if action == "resume":
            return {"ok": True, "run": view(resume(rid))}
    except FlowError as e:
        raise HTTPException(409, str(e)) from None
    raise HTTPException(404, L("只有 stop / resume", "Only stop or resume"))
