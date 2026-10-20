"""思考档位（第 2 批，10/19）：/think [档位] 按对话存，按 Agent 的默认写 server.json；主人回合用上。

档位：low / medium / high / xhigh / max（内核原来就认这五个：models.effort、claude_brain.EFFORTS；Claude 的 effort、各家的
  reasoning_effort）。另认几个别名（照 OpenClaw thinking.md：x-high / extra-high → xhigh，highest → high，maximum → max）；
  default / inherit / clear / reset = 清掉这个对话的、跟回默认。没有 off：内核没有统一的「关掉思考」（Opus 5.5 关不掉，兼容接口
  不带 reasoning_effort 就是各家自己的默认）。
用哪一档（resolve）：这个对话的（threads.think，/think 设的）→ 这个 Agent 的默认（engine.think）→ 不给（各家自己的默认；Claude Code
  的照旧用 engine.claude_code.effort）。都不写 = 和以前一样。
就近夹（clamp，照搬 Hermes 的 clamp_effort）：模型不支持的档换成比要的弱的里面最强的一档（夹了不会更贵）；没有更弱的就用它最弱的
  一档；不知道它支持哪几档的原样发。存着的还是用户要的那一档：换回支持的模型又照它发。各家支持哪几档（supported）：
  - Claude 接口（api anthropic）：adaptive 的新型号五档都行；4.6（opus / sonnet）只到 high（xhigh、max 从 Opus 4.7 起）；
    老型号不带 effort（不分档）
  - OpenAI 兼容（api openai）：provider 开了 reasoning_effort 才带；effort_map 换了名的档算不支持（默认 xhigh、max → high）
  - Responses（api openai-responses，含 ChatGPT 套餐）："reasoning": false 的不分档；effort_map 默认 max → xhigh
  - Claude Code（claude-code/…）：--effort 认的五档（写了完整型号的照 Claude 接口那一条）
  夹完的交给 Spec.effort（engine/loop.py → providers 的 Request.effort；owner.respond 填），Claude Code 的交给 --effort
  （claude_brain.command，Turn.effort）。正在跑的那一轮照旧，下一轮起用新的。外人回合、一次性的问答照旧用 engine.effort。
server.json：engine.think = {"main": "high", "<Agent id>": "low", "*": "medium"}（"*" = 没写的 Agent 都用它）。不写 = 只有 /think 设的。
借：EFFORT_LADDER、clamp_effort 照搬 Hermes Agent（NousResearch/hermes-agent，commit f4ac0bea）agent/reasoning_effort.py，MIT，
  Copyright (c) 2025 Nous Research，许可全文见仓库根目录的 THIRD_PARTY_NOTICES；别名、按对话存 / 按 Agent 默认 / default 清掉的规矩照
  OpenClaw docs/tools/thinking.md（只借设计）。
"""
from __future__ import annotations

from collections.abc import Sequence

from config import raw

from .errors import EngineError

LEVELS = ("low", "medium", "high", "xhigh", "max")
LADDER = ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")  # Hermes EFFORT_LADDER：低 → 高
ALIASES = {"x-high": "xhigh", "x_high": "xhigh", "extra-high": "xhigh", "extra high": "xhigh", "extra_high": "xhigh", "highest": "high",
           "maximum": "max", "med": "medium", "mid": "medium"}
CLEAR = frozenset({"default", "inherit", "clear", "reset", "unpin"})
OLD_ADAPTIVE = frozenset({"claude-opus-4-6", "claude-sonnet-4-6"})  # 4.6：effort 只到 high


def normalize(word: str | None) -> str | None:
    """用户写的 → 五档之一；不认识 → None。"""
    w = " ".join(str(word or "").strip().lower().split())
    w = ALIASES.get(w, w)
    return w if w in LEVELS else None


def clamp(effort: str | None, supported: Sequence[str] | None, overrides: dict[str, str] | None = None) -> str | None:
    """Hermes clamp_effort：支持就原样；不支持的换成比它弱的里面最强的一档（夹了不会更贵），没有更弱的用最弱的；supported 不知道（None /
    空）、不在梯子上的名字原样还回去。"""
    want = str(effort or "").strip().lower()
    if not want or not supported:
        return effort
    sup = [x for x in (str(s).strip().lower() for s in supported) if x in LADDER]
    if not sup or want in sup:
        return effort
    if overrides and overrides.get(want) in sup:
        return overrides[want]
    if want not in LADDER:
        return effort
    cands = [x for x in sup if x != "none"]  # none 是关掉思考，不当「弱一档」用
    if not cands:
        return effort
    i = LADDER.index(want)
    below = [x for x in cands if LADDER.index(x) < i]
    return max(below, key=LADDER.index) if below else min(cands, key=LADDER.index)


def cfg() -> dict[str, str]:
    """engine.think：Agent → 档位（认不出的档位不算）。"""
    e = raw().get("engine")
    v = e.get("think") if isinstance(e, dict) else None
    if not isinstance(v, dict):
        return {}
    return {str(k): lv for k, x in v.items() if (lv := normalize(x if isinstance(x, str) else None))}


def agent_default(agent: str | None) -> str | None:
    c = cfg()
    return c.get(agent or "main") or c.get("*")


def of_thread(conversation: str) -> str | None:
    """这个对话 /think 设的档位（threads.think；没设 = None）。"""
    import chat
    with chat._lock, chat.db() as conn:
        r = conn.execute("SELECT think FROM threads WHERE id=?", (conversation,)).fetchone()
    return normalize(r["think"]) if r and r["think"] else None


def set_thread(conversation: str, level: str | None) -> None:
    """存这个对话的档位（None = 清掉，跟回默认）。"""
    import chat
    with chat._lock, chat.db() as conn:
        conn.execute("INSERT INTO threads(id, think, updated_at) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET think=excluded.think, "
                     "updated_at=excluded.updated_at", (conversation, level, chat.now_iso()))


def _claude(model_id: str) -> tuple[str, ...]:
    from . import models
    if not models.anthropic_caps(model_id)["adaptive"]:
        return ()
    return LEVELS[:3] if model_id in OLD_ADAPTIVE else LEVELS


def supported(model: str | None) -> tuple[str, ...] | None:
    """这个模型认哪几档：() = 不分档（不带）；None = 不知道（原样发）。"""
    from . import models
    ref = str(model or "").strip()
    if models.subscription(ref) == "claude-code":
        name = ref.split("/", 1)[1]
        return _claude(name) if name.startswith("claude-") else LEVELS
    try:
        name, mid = models.parse_ref(ref)
        p = models.provider(name)
    except EngineError:
        return None
    if p.get("api") == models.ANTHROPIC:
        return _claude(mid)
    if p.get("api") == models.RESPONSES:
        if p.get("reasoning", True) is False:
            return ()
        emap = p.get("effort_map") if isinstance(p.get("effort_map"), dict) else {"max": "xhigh"}
    else:
        if not p.get("reasoning_effort"):
            return ()
        emap = p.get("effort_map") if isinstance(p.get("effort_map"), dict) else {"xhigh": "high", "max": "high"}
    return tuple(x for x in LEVELS if emap.get(x, x) == x)


def resolve(conversation: str | None, agent: str | None, model: str | None) -> dict:
    """→ {"want": 要的档（None = 没设）, "from": "chat" / "agent" / None, "send": 夹完真正发的（None = 不带，用各家的默认）,
    "levels": 这个模型认的几档（None = 不知道）}。"""
    want, src = (of_thread(conversation) if conversation else None), "chat"
    if want is None:
        want, src = agent_default(agent), "agent"
    if want is None:
        return {"want": None, "from": None, "send": None, "levels": supported(model)}
    levels = supported(model)
    send = None if levels == () else clamp(want, levels)
    return {"want": want, "from": src, "send": send, "levels": levels}


def effort_for(conversation: str | None, agent: str | None, model: str | None) -> str | None:
    """这一轮交给模型的思考档位（None = 不带：各家自己的默认；Claude Code 照旧 engine.claude_code.effort）。"""
    try:
        return resolve(conversation, agent, model)["send"]
    except Exception as e:  # noqa: BLE001 — 读不到设置不拖累这一轮：照旧不带
        print(f"[engine] 思考档位没读到：{type(e).__name__}: {str(e)[:120]}")
        return None
