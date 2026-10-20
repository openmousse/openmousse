"""提示缓存保温（第 7 批，2026-10-19）：主人在 app 里和 Claude 聊（Anthropic 的接口，开头和记录有 1 小时的提示缓存）以后走开了，
缓存快过期时把这个对话最后那次请求原样再发一次（max_tokens 1：读一遍缓存、续上 1 小时），他回来再说话不用重新写一遍缓存
（写 1 小时的缓存是输入价的 2 倍，读是 0.1 倍）。server.json 的 engine.cache_warming，不写 = 关：
  "cache_warming": {"hours": 2, "every_minutes": 55}     true = 照默认：最后一次说话以后最多保温 2 小时，每 55 分钟一次
只保这几种：主人回合（fallback.current 的 Spec：kind owner）、用户自己在 app 里说的（origin user / relay）、Anthropic 的接口、缓存是 1 小时的
  （owner.respond 给主人回合的都是）、不是要 JSON 的。别家（OpenAI 兼容的这些自己按前缀缓存、过期时间也不归我们管）不做。
怎么记：每次调完（providers.stream 里出 done 的那一次）记下这个对话的这一次请求（同一个对话只记最后一次，最多 MAX_SLOTS 个对话）；
  下一次真的说话就换成新的。
保温那一次：照原样（开头、记录、工具、缓存、思考的设置都一样，只把 max_tokens 改成 1：思考设置一变缓存就对不上）经 providers.stream 发，
  不进会话；在事件表里记一轮（对话 warm:<原来的对话>，kind owner，origin warming，记着是哪个 Agent 的）：用量和花费照实记，用途算 warming
  （engine/usage.py）。
  发不出去（出错、被拒）：这个对话就不再保温（下次真的说话再记）。保温的任务从一个空的 contextvars 起：不带着那一轮的 Picker
  （不然 engine/auto.py 会以为还在那一轮里）。
停：关掉（engine.cache_warming 不写了）、过了 hours。跳过这一次（对话留着，下一次再看）：急停着（engine/estop.py 的 paused()）、
  到了花费上限（engine/spend.py：今天的总数、这个 Agent 的、engine.spend.purposes.warming）。服务重启 = 都忘了。
"""
from __future__ import annotations

import asyncio
import contextlib
import contextvars
import dataclasses
import time
from dataclasses import dataclass

from . import fallback, models

HOURS, EVERY_MINUTES = 2.0, 55.0
ORIGINS = ("user", "relay")
MAX_SLOTS = 50                      # 最多同时保几个对话（最近说过话的）


@dataclass
class Slot:
    ref: str
    req: object                     # providers.Request（记下的那一次，原样）
    last: float                     # 真的说话（调完）的时候（time.monotonic）
    due: float                      # 下一次保温的时候
    agent: str | None = None        # 哪个 Agent 的对话（花费上限按 Agent 算、事件表里记着）


_slots: dict[str, Slot] = {}
_task: asyncio.Task | None = None


def cfg() -> dict | None:
    """开着 → 设置（hours、every_minutes）；关着 → None。"""
    v = models.cfg().get("cache_warming")
    if v is True:
        return {}
    return v if isinstance(v, dict) and v.get("enabled", True) is not False else None


def _num(c: dict, key: str, default: float, lo: float, hi: float) -> float:
    try:
        return max(lo, min(hi, float(c.get(key) or default)))
    except (TypeError, ValueError):
        return default


def paused() -> bool:
    """急停（engine/estop.py，第 4 批；还没有它就当没停）。"""
    try:
        from . import estop  # type: ignore[attr-defined]
    except ImportError:
        return False
    try:
        return bool(estop.paused())
    except Exception:  # noqa: BLE001 — 急停自己出错：宁可不保温
        return True


def capped(s: Slot) -> bool:
    """花费上限（engine/spend.py）：今天的总数、这个 Agent 的、保温这一项（engine.spend.purposes.warming）到了 → 这一次不保温。"""
    try:
        from . import spend
        return spend.blocked(purpose="warming", agent=s.agent, model=s.ref) is not None
    except Exception:  # noqa: BLE001 — 上限自己出错：宁可不保温
        return True


def remember(ref: str, req, api: str | None) -> None:
    """providers.stream 每次调完（出 done）都告诉这里（见文件开头「只保这几种」）。"""
    c = cfg()
    pick = fallback.current()
    spec = pick.spec if pick is not None else None
    if c is None or spec is None or spec.kind != "owner" or spec.origin not in ORIGINS or api != models.ANTHROPIC:
        return
    if getattr(req, "cache_ttl", None) != "1h" or getattr(req, "json", False) or not spec.conversation:
        return
    now = time.monotonic()
    every = _num(c, "every_minutes", EVERY_MINUTES, 5, 59) * 60
    _slots.pop(spec.conversation, None)  # 挪到最后（最近说过话的）
    _slots[spec.conversation] = Slot(ref, dataclasses.replace(req, messages=list(req.messages), quick=True), now, now + every, spec.agent)
    while len(_slots) > MAX_SLOTS:
        _slots.pop(next(iter(_slots)))
    _start()


def _start() -> None:
    global _task
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    if _task is None or _task.done() or _task.get_loop() is not loop:
        _task = loop.create_task(_run(), name="engine-cache-warming", context=contextvars.Context())


async def _run() -> None:
    while _slots:
        c = cfg()
        if c is None:
            _slots.clear()
            return
        now = time.monotonic()
        hours, every = _num(c, "hours", HOURS, 0.1, 24) * 3600, _num(c, "every_minutes", EVERY_MINUTES, 5, 59) * 60
        for conv, s in list(_slots.items()):
            if now - s.last > hours:
                _slots.pop(conv, None)
            elif s.due <= now:
                s.due = now + every
                if paused() or capped(s):  # 急停着、到了花费上限：这一次跳过，对话留着
                    continue
                if not await warm(conv, s):
                    _slots.pop(conv, None)
        if not _slots:
            return
        await asyncio.sleep(max(1.0, min(60.0, min(s.due for s in _slots.values()) - time.monotonic())))


async def warm(conv: str, s: Slot) -> bool:
    """保温一次（见文件开头）。→ 成了没有。"""
    from . import events, providers
    tid = events.turn_open(conversation=f"warm:{conv}", kind="owner", model=s.ref, origin="warming", agent=s.agent)
    usage: dict = {"in": 0, "out": 0, "cache_read": 0, "cache_write": 0, "cost": 0.0}
    try:
        async for ev in providers.stream(s.ref, dataclasses.replace(s.req, max_tokens=1)):
            if ev.get("type") == "done":
                m = ev["message"]
                u = m.get("usage") or {}
                for k in ("in", "out", "cache_read", "cache_write"):
                    usage[k] = int(u.get(k) or 0)
                usage["cost"] = models.cost(f"{m.get('provider')}/{m.get('served') or m.get('model')}", u)
    except Exception as e:  # noqa: BLE001 — 保温出错不拖累别的：这个对话不再保温
        print(f"[engine] 保温 {conv} 没成，不再保温它：{str(e)[:160]}")
        with contextlib.suppress(Exception):
            events.turn_close(tid, conversation=f"warm:{conv}", status="error", usage=usage, error=str(e)[:200], models=[s.ref])
        return False
    events.turn_close(tid, conversation=f"warm:{conv}", status="done", usage=usage, models=[s.ref])
    return True


def status() -> list[dict]:
    """正在保温的（给人看）：[{"conversation", "model", "next_in"（秒）}]。"""
    now = time.monotonic()
    return [{"conversation": c, "model": s.ref, "next_in": max(0, int(s.due - now))} for c, s in _slots.items()]
