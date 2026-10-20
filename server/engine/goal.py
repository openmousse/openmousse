"""持续目标（第 2 批，10/19，engine.goal，默认关）：/goal <目标>，这个对话每一轮说完，用一次便宜的零工具调用判「做到了没有」，没做到
就替用户接一句「接着做」，直到做到、做不了、到了轮数或 token 上限、或者用户叫停。

为什么默认关、上线要问：它会自己接着跑、接着花钱（每一轮 + 每一次判）。谁付钱照 may_spend：目标是主人自己定的，接着做的那几轮 origin
  auto（Claude Code 能用他的套餐；ChatGPT 套餐的后台要他在设置里同意过，不然换按量的），判的那一次是 kind owner、origin auto 的一次性问答。
一轮完了（../chat.py 每一轮收尾时调 after_run，只在 builtin）：
  这一轮被用户点了停 → 目标停下（paused，/goal resume 接着）；出错 → 停下（写明为什么）；急停着（第 4 批，engine/estop.py）→ 停下，不判、不接着做；
  轮数（用户自己说的那几轮也算，照 Hermes）、token（这个对话从定目标起的进 + 出）到了上限 → limited；
  否则判一次（judge）：done → 做到了；blocked → 做不了 / 要用户给东西（停下，写明卡在哪）；continue → 这个对话闲着、没有排着的话，开一轮
  （origin auto，app 里一行灰字「接着做目标」，交给模型的是 CONTINUE）。判的那一次出错算 continue（照 Hermes 的 fail-open），连着
  JUDGE_FAILS 次出错就停下。
用户一说话就让出来：用户的话照常先回（插话 / 排队照 /queue），有排着的、正在回的就不开「接着做」；那一轮完了照样再判。/new、/resume 不动目标。
结果在 app 的对话里留一行灰字（compact.notice）：做到了、做不了、到上限、停下。
存在 engine_goals（这个模块第一次用时建，一个对话一行）。
server.json：engine.goal = true 或 {"max_turns": 20, "max_tokens": 1000000, "judge": "deepseek/deepseek-flash"}（judge 不写 = 默认模型）。
命令（engine/commands.py）：/goal <目标>（start / set 也行）、/goal（看）、/goal pause、/goal resume、/goal stop（clear）。
借：判的说法（done / blocked / continue 三种、拿不准就 continue）、接着做的那句、默认 20 轮、判不出来几次就停照 Hermes Agent
  （NousResearch/hermes-agent，commit f4ac0bea）hermes_cli/goals.py 的 JUDGE_SYSTEM_PROMPT、CONTINUATION_PROMPT_TEMPLATE、DEFAULT_MAX_TURNS，
  MIT，Copyright (c) 2025 Nous Research，许可全文见仓库根目录的 THIRD_PARTY_NOTICES；命令的样子照 OpenClaw docs/tools/goal.md（只借设计）。
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from config import raw
from i18n import L

from . import store
from .context import AUTO_TRIGGER

MAX_TURNS = 20
MAX_TOKENS = 1_000_000
JUDGE_FAILS = 3
RESPONSE_MAX = 4_000
TABLE = """CREATE TABLE IF NOT EXISTS engine_goals (
  conversation TEXT PRIMARY KEY, goal TEXT NOT NULL, status TEXT NOT NULL, turns INTEGER NOT NULL DEFAULT 0, max_turns INTEGER NOT NULL,
  max_tokens INTEGER NOT NULL, since TEXT NOT NULL, fails INTEGER NOT NULL DEFAULT 0, reason TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"""
CONTINUE = ("[Continuing toward your standing goal]\nGoal: {goal}\n\nContinue working toward this goal. Take the next concrete step. If you "
            "believe the goal is complete, state so explicitly and stop. If you are blocked and need input from the user, say so clearly and stop.")
JUDGE = (
    "You are a strict judge evaluating whether an assistant has achieved the user's stated goal. You receive the goal text and the "
    "assistant's most recent response (INPUT_JSON). Decide one of three verdicts.\n"
    "done: the goal is fully satisfied: the response explicitly confirms it was completed, or clearly shows the final deliverable was "
    "produced. done requires the deliverable to actually exist; if the response only explains why the goal cannot be reached, it is "
    "blocked, not done.\n"
    "blocked: the goal cannot be satisfied as stated (impossible, out of scope, no valid path), or progress is blocked and the next "
    "step needs input from the user. Give the reason describing what is blocking. Never return blocked for a goal that was achieved.\n"
    "continue: not done, and there is a concrete next step the assistant can take right now. This is the default when in doubt.\n"
    'Reply with {"verdict": "done" | "blocked" | "continue", "reason": "<one sentence>"}.')
_bg: set[asyncio.Task] = set()


def cfg() -> dict | None:
    """engine.goal：true 或 {…}；不写 / false = 没开（None）。"""
    e = raw().get("engine")
    v = e.get("goal") if isinstance(e, dict) else None
    if v is True:
        return {}
    return v if isinstance(v, dict) and v.get("enabled", True) is not False else None


def _n(key: str, dflt: int) -> int:
    v = (cfg() or {}).get(key)
    return v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else dflt


def get(conversation: str) -> dict | None:
    with store.tx() as conn:
        conn.execute(TABLE)
        r = conn.execute("SELECT * FROM engine_goals WHERE conversation=?", (conversation,)).fetchone()
    return dict(r) if r else None


def _save(conversation: str, **fields: Any) -> dict | None:
    now = store.now_iso()
    with store.tx() as conn:
        conn.execute(TABLE)
        sets = ", ".join(f"{k}=?" for k in fields)
        conn.execute(f"UPDATE engine_goals SET {sets}, updated_at=? WHERE conversation=?", (*fields.values(), now, conversation))
    return get(conversation)


def start(conversation: str, goal: str) -> dict:
    """定一个目标（这个对话原来有的换掉），从现在起算轮数和 token。"""
    now = store.now_iso()
    with store.tx() as conn:
        conn.execute(TABLE)
        conn.execute("INSERT OR REPLACE INTO engine_goals(conversation, goal, status, turns, max_turns, max_tokens, since, fails, reason, created_at, "
                     "updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (conversation, goal.strip(), "active", 0, _n("max_turns", MAX_TURNS),
                                                                    _n("max_tokens", MAX_TOKENS), now, 0, None, now, now))
    return get(conversation) or {}


def stop(conversation: str) -> bool:
    with store.tx() as conn:
        conn.execute(TABLE)
        return conn.execute("DELETE FROM engine_goals WHERE conversation=?", (conversation,)).rowcount > 0


def pause(conversation: str, reason: str) -> dict | None:
    g = get(conversation)
    return _save(conversation, status="paused", reason=reason) if g and g["status"] == "active" else g


def resume(conversation: str) -> dict | None:
    """接着做：轮数、token 从现在重新算（到了上限的也能接着）。"""
    g = get(conversation)
    if g is None:
        return None
    return _save(conversation, status="active", turns=0, since=store.now_iso(), fails=0, reason=None)


def tokens(conversation: str, since: str) -> int:
    """这个对话从 since 起的每一轮（含判的那几次）进 + 出的 token。"""
    with store.read() as conn:
        rows = conn.execute("SELECT usage FROM engine_turns WHERE conversation=? AND julianday(started_at) >= julianday(?)",
                            (conversation, since)).fetchall()
    n = 0
    for r in rows:
        try:
            u = json.loads(r["usage"] or "{}")
        except ValueError:
            continue
        n += int(u.get("in") or 0) + int(u.get("out") or 0)
    return n


def line(g: dict | None) -> str:
    """/goal 看的那一行。"""
    if g is None:
        return L("这个对话没有目标。/goal <目标> 定一个。", "This chat has no goal. /goal <goal> sets one.")
    status = {"active": L("在做", "active"), "paused": L("停着", "paused"), "done": L("做到了", "done"), "blocked": L("做不了", "blocked"),
              "limited": L("到上限了", "limit reached")}.get(g["status"], g["status"])
    used = tokens(g["conversation"], g["since"])
    out = L(f"**目标**：{g['goal']}\n- 状态：{status} · 第 {g['turns']}/{g['max_turns']} 轮 · token {used}/{g['max_tokens']}",
            f"**Goal**: {g['goal']}\n- Status: {status} · turn {g['turns']}/{g['max_turns']} · tokens {used}/{g['max_tokens']}")
    if g.get("reason"):
        out += L(f"\n- {g['reason']}", f"\n- {g['reason']}")
    return out


def judge_model() -> str | None:
    from . import models
    m = (cfg() or {}).get("judge")
    return m if isinstance(m, str) and models.known(m) else models.default_model()


async def judge(goal: str, response: str, conversation: str) -> tuple[str, str]:
    """→ (done / blocked / continue, 一句为什么)。出错抛 EngineError（调用方算 continue）。"""
    from . import oneshot
    model = judge_model()
    if not model:
        from .errors import EngineError
        raise EngineError("no model for the goal judge", kind="config")
    v, _ = await oneshot.ask_json(JUDGE, {"goal": goal, "response": (response or "")[-RESPONSE_MAX:]}, model=model, purpose="goal", need="verdict",
                                  timeout=60, max_tokens=4096, conversation=conversation, kind="owner", origin="auto")
    verdict = str(v.get("verdict") or "").strip().lower()
    return (verdict if verdict in ("done", "blocked", "continue") else "continue"), str(v.get("reason") or "").strip()[:300]


def shown(g: dict) -> str:
    return AUTO_TRIGGER + L(f"接着做目标（第 {g['turns'] + 1} 轮）：{g['goal'][:80]}", f"Continuing the goal (turn {g['turns'] + 1}): {g['goal'][:80]}")


def kick(conversation: str) -> bool:
    """开一轮「接着做」（这个对话闲着、没有排着的话才开）→ 开了没有。"""
    import chat
    g = get(conversation)
    if g is None or g["status"] != "active" or chat.busy(conversation) or chat.QUEUED.get(conversation):
        return False
    chat.start_run(conversation, shown(g), None, origin="auto", say=CONTINUE.format(goal=g["goal"]))
    return True


def after_run(run: Any) -> None:
    """../chat.py 每一轮收尾时调（只在 builtin）：没开、这个对话没有在做的目标 → 什么都不做；否则在后台判、接着做。"""
    if cfg() is None:
        return
    try:
        g = get(run.thread)
    except Exception:  # noqa: BLE001 — 库读不了不拖累收尾
        return
    if g is None or g["status"] != "active":
        return
    task = asyncio.ensure_future(_after(run.thread, run.text or "", run.status))
    _bg.add(task)
    task.add_done_callback(_bg.discard)


async def _after(conversation: str, text: str, status: str) -> None:
    from . import compact, estop
    try:
        if status == "stopped":
            pause(conversation, L("你点了停：目标先停着，/goal resume 接着做。", "You stopped the reply: the goal is paused; /goal resume picks it up."))
            compact.notice(conversation, L("目标先停着（你点了停）。/goal resume 接着做。", "Goal paused (you stopped the reply). /goal resume picks it up."))
            return
        if status == "error":
            pause(conversation, L("上一轮出错了，目标先停着。", "The last turn failed, so the goal is paused."))
            compact.notice(conversation, L("上一轮出错了，目标先停着。/goal resume 接着做。", "The last turn failed, so the goal is paused. /goal resume "
                                           "picks it up."))
            return
        g = _save(conversation, turns=(get(conversation) or {}).get("turns", 0) + 1)
        if g is None or g["status"] != "active":
            return
        if estop.paused():  # 第 4 批：急停着不再判（那也是一次模型调用）、不再「接着做」
            pause(conversation, L("急停着，目标先停着。", "The emergency stop is on, so the goal is paused."))
            compact.notice(conversation, L("急停着，目标先停着。恢复以后 /goal resume 接着做。", "The emergency stop is on, so the goal is paused. "
                                           "After you resume, /goal resume picks it up."))
            return
        used = tokens(conversation, g["since"])
        if g["turns"] >= g["max_turns"] or used >= g["max_tokens"]:
            why = L(f"到了上限（{g['turns']}/{g['max_turns']} 轮，token {used}/{g['max_tokens']}）", f"hit its limit ({g['turns']}/{g['max_turns']} "
                    f"turns, {used}/{g['max_tokens']} tokens)")
            _save(conversation, status="limited", reason=why)
            compact.notice(conversation, L(f"目标{why}，先停下。/goal resume 再做一段。", f"The goal {why}, so it stopped. /goal resume runs another stretch."))
            return
        try:
            verdict, reason = await judge(g["goal"], text, conversation)
            fails = 0
        except Exception as e:  # noqa: BLE001 — 判不出来照 Hermes 算 continue；连着几次判不出来就停
            verdict, reason, fails = "continue", f"{type(e).__name__}: {str(e)[:120]}", g["fails"] + 1
        if fails >= JUDGE_FAILS:
            _save(conversation, status="paused", fails=fails, reason=L(f"连着 {fails} 次判不出来：{reason}", f"The judge failed {fails} times in a row: {reason}"))
            compact.notice(conversation, L("判断目标做到没有的那一步连着出错，目标先停着。", "Checking the goal kept failing, so the goal is paused."))
            return
        if verdict == "done":
            _save(conversation, status="done", fails=0, reason=reason)
            compact.notice(conversation, L(f"目标做到了：{reason}", f"Goal done: {reason}"))
            return
        if verdict == "blocked":
            _save(conversation, status="blocked", fails=0, reason=reason)
            compact.notice(conversation, L(f"目标做不下去了：{reason}（/goal resume 接着试，/goal stop 不做了）",
                                           f"The goal is blocked: {reason} (/goal resume tries again, /goal stop drops it)"))
            return
        _save(conversation, fails=fails, reason=reason or None)
        kick(conversation)
    except Exception as e:  # noqa: BLE001 — 目标这一层出错不拖累对话
        print(f"[engine] {conversation}：目标没接上：{type(e).__name__}: {str(e)[:160]}")
