"""后台任务的台账（内核第 5 项，2026-10-16）：派出去的后台任务一行一个，表 engine_tasks（store.py）。engine/tasks.py 写，
cards.py（任务卡、额度）、data.py（任务页 /api/tasks）、settle.py 的那一行灰字在 claw 选 builtin 时从这里读，不再读 OpenClaw 的
<openclaw_home>/state/openclaw.sqlite。

一行（表里的样子）：
  id            tk_…
  thread        派它的那个 app 对话（嵌套的子任务也记最上面那个对话：额度、推送、做完回到哪都按它）
  agent         谁派的：main 或 Agent 的 id（子 Agent 就是这个 Agent，用它的工作区、它的工具的子集）
  parent/depth  嵌套：派它的那个后台任务、第几层（app 对话派的是第 1 层）
  turn / call   派它的那一轮、那一次工具调用
  label / task  卡片标题、派活时写的说明（目标 / 要交 / 约束）
  runner        谁来跑：engine（内核自己）/ claude-code / codex（本机用户自己登录的官方 CLI）
  model         要的模型（engine：provider/model；外援：它自己的型号名；空 = 默认）
  level         子 Agent 工具的上限：read 只读 / undo 撤得回的写也行（撤不回的一律不做）
  timeout_s     到点停（server.json 的 tasks.max_minutes，或者用户在收件箱同意的更长）
  status        queued（记下了、等空位）→ running → done / failed / timed_out / cancelled / interrupted（服务重启打断了）
  created_at / started_at / ended_at    Unix 毫秒（和 OpenClaw 的台账一样，cards.py 两边通用）
  session / child_turn                   子会话（DbSession，键 mousse:task:<id>）、最近一轮
  tools / last_tool / last_title         用了几次工具、最后一个工具和它的标题（任务卡上「正在做哪一步」）
  usage / result / error                 用量（in / out / cache_read / cache_write / cost）、第一轮的结果（子 Agent 最后说的话）、出错原因
  delivery      结果交回去没有：none（还在跑）/ pending（做完了、还没交）/ steered（插进了派它的对话正在跑的那一轮）/ sent（开了一轮）/
                delivered（交到了）/ noted（用户自己停的：只在会话里补一句，不开一轮）/ dropped（交不出去了：太久、试了太多次）
  attempts / delivered_at / retry_of / inbox / data（JSON：为什么、截断了没有、外援的诊断、步骤……）

tasks() 给 cards.py / data.py 的是 OpenClaw 台账那种形状（cards.task_json / title_of / status_of / result_of 两边通用），多一个 thread。
状态对过去：done → succeeded，interrupted → lost，别的照旧。
"""
from __future__ import annotations

import json
import time
from datetime import datetime

from . import store

LIVE = ("queued", "running")
STATUS_OUT = {"done": "succeeded", "interrupted": "lost"}
COLS = ("id", "thread", "agent", "parent", "depth", "turn", "call", "label", "task", "runner", "model", "level", "timeout_s", "status",
        "created_at", "started_at", "ended_at", "session", "child_turn", "tools", "last_tool", "last_title", "usage", "result", "error",
        "delivery", "delivered_at", "attempts", "retry_of", "inbox", "data")
JSON_COLS = ("usage", "data")


def now_ms() -> int:
    return int(time.time() * 1000)


def available() -> bool:
    """有这本台账（claw 选 builtin 时才会读到这里；表在 store.py 里随用随建）。"""
    return True


def _row(r) -> dict:
    d = dict(r)
    for k in JSON_COLS:
        try:
            d[k] = json.loads(d.get(k) or "{}")
        except ValueError:
            d[k] = {}
    return d


def _dump(fields: dict) -> dict:
    for k in JSON_COLS:
        if k in fields and not isinstance(fields[k], str):
            fields[k] = json.dumps(fields[k] or {}, ensure_ascii=False)
    return fields


def insert(**fields) -> dict:
    """记一行（派活时先记下来，再开跑）。→ 记下的那一行。"""
    fields = _dump({k: v for k, v in fields.items() if k in COLS})
    keys = list(fields)
    with store.tx() as conn:
        conn.execute(f"INSERT INTO engine_tasks({', '.join(keys)}) VALUES({', '.join('?' * len(keys))})", [fields[k] for k in keys])
    row = get(fields["id"])
    assert row is not None
    return row


def get(task_id: str) -> dict | None:
    with store.read() as conn:
        r = conn.execute("SELECT * FROM engine_tasks WHERE id=?", (task_id,)).fetchone()
    return _row(r) if r else None


def update(task_id: str, *, where: str = "", where_args: tuple = (), **fields) -> bool:
    """改几栏。where：只在满足这个条件时改（比如只改还在跑的，免得把结束了的又改回去）。→ 改了没有。"""
    fields = _dump({k: v for k, v in fields.items() if k in COLS and k != "id"})
    if not fields:
        return False
    sets = ", ".join(f"{k}=?" for k in fields)
    with store.tx() as conn:
        return conn.execute(f"UPDATE engine_tasks SET {sets} WHERE id=?" + (f" AND ({where})" if where else ""),
                            [*fields.values(), task_id, *where_args]).rowcount == 1


def merge_data(task_id: str, **extra) -> None:
    """data（JSON）里加几样。"""
    with store.tx() as conn:
        r = conn.execute("SELECT data FROM engine_tasks WHERE id=?", (task_id,)).fetchone()
        if r is None:
            return
        try:
            d = json.loads(r["data"] or "{}")
        except ValueError:
            d = {}
        d.update(extra)
        conn.execute("UPDATE engine_tasks SET data=? WHERE id=?", (json.dumps(d, ensure_ascii=False), task_id))


def rows(where: str = "1", args: tuple = (), limit: int = 200, order: str = "created_at DESC") -> list[dict]:
    """表里的原样（默认新的在前）。where / order 只用本模块和 engine/tasks.py 里写死的条件。"""
    with store.read() as conn:
        found = conn.execute(f"SELECT * FROM engine_tasks WHERE {where} ORDER BY {order} LIMIT ?", (*args, limit)).fetchall()
    return [_row(r) for r in found]


def children(task_id: str, live: bool = False) -> list[dict]:
    return rows("parent=?" + (" AND status IN ('queued','running')" if live else ""), (task_id,), 100, "created_at")


def count_since(since_ms: int) -> int:
    """从 since_ms 起派了几个（嵌套的也算，和 OpenClaw 的台账一样每个子会话一行）。"""
    with store.read() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM engine_tasks WHERE created_at>=?", (since_ms,)).fetchone()[0])


# —— 给 cards.py / data.py 的样子（OpenClaw 台账那种）——————————————————————————————————————

def shape(r: dict) -> dict:
    """一行 → OpenClaw 台账的样子（cards.task_json、data.ledger_task 直接能用）。"""
    status = r.get("status") or "queued"
    data = r.get("data") or {}
    payload = {"model": r.get("model") or data.get("model") or None, "runTimeoutSeconds": r.get("timeout_s"),
               "completion": {"resultText": r.get("result") or ""}, "requesterOrigin": {"channel": "webchat"}, "runner": r.get("runner")}
    if status == "timed_out":
        payload["endedReason"] = "timeout"
    return {"task_id": r["id"], "thread": r.get("thread"), "owner_key": r.get("thread"), "child_session_key": f"task:{r['id']}", "run_id": r["id"],
            "label": r.get("label") or "", "task": r.get("task") or "", "status": STATUS_OUT.get(status, status),
            "created_at": r.get("created_at"), "started_at": r.get("started_at"), "ended_at": r.get("ended_at"),
            "tool_use_count": r.get("tools") or 0, "last_tool_name": r.get("last_tool"), "error": r.get("error"),
            "progress_summary": None, "terminal_summary": r.get("result") or None, "payload": payload,
            "parent": r.get("parent"), "depth": r.get("depth"), "runner": r.get("runner"), "usage": r.get("usage") or {}}


def tasks(*, thread: str | None = None, since_ms: int | None = None, until_ms: int | None = None, ended: bool | None = None,
          task_id: str | None = None, limit: int = 200) -> list[dict]:
    """台账里的任务，新的在前（OpenClaw 台账的样子）。thread：只看这个对话直接派的（嵌套的子任务不单独出卡）；
    since_ms / until_ms：按 created_at（ended=True 时按 ended_at）；ended：True 只要结束了的、False 只要还在跑的；task_id：就这一个。"""
    if task_id is not None:
        r = get(task_id)
        return [shape(r)] if r else []
    where, args = ["1"], []
    if thread is not None:
        where.append("thread=? AND parent IS NULL")
        args.append(thread)
    if ended is True:
        where.append("status NOT IN ('queued','running')")
    elif ended is False:
        where.append("status IN ('queued','running')")
    col = "IFNULL(ended_at, created_at)" if ended else "created_at"
    if since_ms is not None:
        where.append(f"{col}>=?")
        args.append(int(since_ms))
    if until_ms is not None:
        where.append(f"{col}<?")
        args.append(int(until_ms))
    return [shape(r) for r in rows(" AND ".join(where), tuple(args), limit)]


def step(task_id: str) -> tuple[str, str] | None:
    """进行中的任务正在做哪一步：(工具名, 一句标题)，没有 → None（cards.step_of 画成人话）。"""
    r = get(task_id)
    if not r or r.get("status") not in LIVE or not r.get("last_tool"):
        return None
    return str(r["last_tool"]), str(r.get("last_title") or "")


# —— 任务页的详情（data.py 的 /api/tasks/{id}）——————————————————————————————————————————

def _hm(ts) -> str:
    """毫秒或 ISO 字符串 → HH:MM（服务器的时区）。"""
    from config import settings
    try:
        dt = (datetime.fromtimestamp(ts / 1000, settings.tz) if isinstance(ts, (int, float))
              else datetime.fromisoformat(str(ts)).astimezone(settings.tz))
    except (TypeError, ValueError, OSError):
        return ""
    return dt.strftime("%H:%M")


def _clip(text: str, n: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _turn_steps(turn_id: str, first: bool, started, ended, status: str, error: str | None) -> list[dict]:
    from i18n import L
    steps = [{"time": _hm(started), "kind": "start", "text": L("收到任务", "Got the task") if first else L("收到修改意见", "Got revision notes")}]
    with store.read() as conn:
        found = conn.execute("SELECT type, name, detail, status, started_at FROM engine_steps WHERE turn=? AND type IN ('tool','steer') ORDER BY seq",
                             (turn_id,)).fetchall()
    for st in found:
        if st["type"] == "steer":
            steps.append({"time": _hm(st["started_at"]), "kind": "check", "text": L("收到补充", "Got a note")})
            continue
        label = st["detail"] or st["name"] or L("工具", "Tool")
        bad = st["status"] in ("error", "denied")
        steps.append({"time": _hm(st["started_at"]), "kind": "check" if bad else "tool",
                      "text": _clip(L(f"{label} 没成", f"{label} failed") if bad else label, 140)})
    if ended:
        ok = status == "done"
        steps.append({"time": _hm(ended), "kind": "done" if ok else "check",
                      "text": L("做完，交回派它的对话", "Done, handed back") if ok else _clip(error or L("没做完", "Didn't finish"), 140)})
    return steps


def detail(task_id: str) -> dict:
    """任务页的每一轮（和 data.task_detail 的形状一样）：{"info": {"totalTokens", "model", "modelProvider", "estimatedCostUsd"},
    "runs": [{version, note, brief, startedAt, finishedAt, tokens, status, steps: [{time, kind, text}], result}]}。内核跑的从子会话和
    事件表读（子会话里的一轮就是一段）；外援跑的只有台账里记下的那几步。"""
    from i18n import L
    r = get(task_id)
    if r is None:
        return {"info": {}, "runs": []}
    model = r.get("model") or (r.get("data") or {}).get("model") or ""
    provider, _, name = model.partition("/") if "/" in model else ("", "", model)
    usage = r.get("usage") or {}
    info = {"totalTokens": int(usage.get("in") or 0) + int(usage.get("out") or 0) + int(usage.get("cache_read") or 0),
            "model": name or None, "modelProvider": provider or (r.get("runner") if r.get("runner") != "engine" else None),
            "estimatedCostUsd": usage.get("cost")}
    turns: list[dict] = []
    if r.get("session"):
        with store.read() as conn:
            turns = [dict(x) for x in conn.execute("SELECT id, status, usage, started_at, ended_at, error FROM engine_turns WHERE session=? "
                                                   "ORDER BY julianday(started_at), rowid", (r["session"],))]  # 按真时间排，见 store.py 开头
    runs: list[dict] = []
    if turns:
        from .session import DbSession, text_of
        s = DbSession.get(r["session"])
        by_turn: dict[str, list[dict]] = {}
        for e in (s.path() if s else []):
            if e["kind"] == "message" and e.get("turn"):
                by_turn.setdefault(e["turn"], []).append(json.loads(e["data"]))
        for i, t in enumerate(turns):
            msgs = by_turn.get(t["id"], [])
            asked = next((text_of(m) for m in msgs if m.get("role") == "user"), "")
            said = next((text_of(m) for m in reversed(msgs) if m.get("role") == "assistant" and text_of(m).strip()), "")
            try:
                u = json.loads(t.get("usage") or "{}")
            except ValueError:
                u = {}
            st = "done" if t["status"] == "done" else "running" if t["status"] == "running" else "failed"
            runs.append({"version": i + 1, "note": None if i == 0 else _clip(asked, 300), "brief": r.get("task") if i == 0 else None,
                         "startedAt": _hm(t["started_at"]), "finishedAt": _hm(t["ended_at"]) if t.get("ended_at") else None,
                         "tokens": int(u.get("in") or 0) + int(u.get("out") or 0), "status": st,
                         "steps": _turn_steps(t["id"], i == 0, t["started_at"], t.get("ended_at"), t["status"], t.get("error")),
                         "result": {"summary": said} if said else None})
        return {"info": info, "runs": runs}
    st = r.get("status")
    steps = [{"time": _hm(r.get("started_at") or r["created_at"]), "kind": "start", "text": L("收到任务", "Got the task")}]
    for x in ((r.get("data") or {}).get("steps") or [])[-60:]:
        steps.append({"time": _hm(x.get("t")), "kind": "tool", "text": _clip(x.get("title") or x.get("name") or "", 140)})
    if st not in LIVE and r.get("ended_at"):
        ok = st == "done"
        steps.append({"time": _hm(r["ended_at"]), "kind": "done" if ok else "check",
                      "text": L("做完，交回派它的对话", "Done, handed back") if ok else _clip(r.get("error") or st or "", 140)})
    runs.append({"version": 1, "note": None, "brief": r.get("task"), "startedAt": _hm(r.get("started_at") or r["created_at"]),
                 "finishedAt": _hm(r["ended_at"]) if r.get("ended_at") else None, "tokens": info["totalTokens"],
                 "status": "running" if st in LIVE else "done" if st == "done" else "failed", "steps": steps,
                 "result": {"summary": r.get("result")} if r.get("result") else None})
    return {"info": info, "runs": runs}
