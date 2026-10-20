"""自动选模型（第 7 批，2026-10-19）：主人自己说的一句简单的，交给便宜的模型答；别的照旧用主模型。server.json 的 engine.auto，不写 = 关：
  "auto": {"cheap": "deepseek/deepseek-flash", "max_chars": 160, "max_words": 28, "keywords": ["…"]}
    cheap      便宜的那个：我们的接口拿密钥跑得了的（用户自己的套餐不算：那几样另有规矩），正在冷却的这一轮不用。不写、写错、没密钥 = 关
    max_chars  这一句最多多少字（默认 160）；max_words 最多多少个词（默认 28；中文这类不空格的一整句算一个词，靠 max_chars 管）
    keywords   再加几个「一看就不简单」的词（默认那一份见 HARD）
    enabled    false = 关（设置留着）
什么时候换：这一轮是主人回合（fallback.current 的 Spec：kind owner），是用户自己说的（origin user / relay；定时任务、心跳、收件箱、
  对外接口、ACP 这些不换），用的是默认的主模型（engine.model；主模型是用户自己的套餐的不换：套餐不按量花钱，换了反倒花钱），这一次调的
  就是它（不是备用链换上的），而且这一轮进来的那句话「简单」：不长、最多一个换行、没有代码（`）、没有网址、没有图、没有 HARD 里的词
  （调试、实现、分析、比较、写一篇……），也不是要 JSON 的。用户在 app 里给这个对话另选了别的模型：那就是他要的，不换。
同一轮粘住：这一轮第一次调模型时定下来（记在这一轮的 Picker.notes），之后调完工具再调模型都照它，不会一会儿换一会儿不换。
  便宜的那个出错（还没出字）：照 fallback 的规矩记一次冷却，这一次接着用主模型答，这一轮剩下的也用主模型。
记账：这一轮真正回答的是哪个照实记（Result.model：Picker.ok 认 notes 里的 answered；engine_turns.models 只记真调过的），钱照它的价算
  （loop 照回复里的 provider / model 算）。app 照 fallbackFrom 标出「换成了 …」。
接在 providers.stream 最前面（choose → run）：loop、fallback 的流程一点不动。
参考：Hermes 的 smart_model_routing 那个想法（简单的一句交给便宜的模型）；只借想法，没拿代码。
"""
from __future__ import annotations

import re
from collections.abc import AsyncIterator

from . import fallback, models
from .errors import EngineError

MAX_CHARS, MAX_WORDS = 160, 28
ORIGINS = ("user", "relay")
HARD = ("debug", "error", "traceback", "exception", "stack trace", "implement", "refactor", "analy", "architect", "design", "compare", "benchmark",
        "optimi", "review", "plan ", "planning", "research", "strategy", "step by step", "explain why", "essay", "report", "article", "document",
        "code", "script", "function", "bug", "proof", "derive", "translate",
        "调试", "报错", "错误", "实现", "重构", "分析", "架构", "设计", "比较", "对比", "优化", "审阅", "规划", "计划", "研究", "策略", "方案",
        "一步步", "为什么", "写一篇", "文章", "报告", "文档", "代码", "脚本", "函数", "证明", "推导", "翻译")
STAMP = re.compile(r"^\[(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) \d{4}-\d\d-\d\d \d\d:\d\d[^\]]*\]\s*")   # session.with_time 加的当地时间
URL = re.compile(r"https?://|www\.", re.I)


def cfg() -> dict:
    v = models.cfg().get("auto")
    return v if isinstance(v, dict) and v.get("enabled", True) is not False else {}


def cheap() -> str | None:
    """便宜的那个（见文件开头）；关着、跑不了 → None。"""
    r = cfg().get("cheap")
    if not isinstance(r, str) or not r.strip():
        return None
    r = models.canonical(r.strip())
    return r if models.valid_ref(r) and not models.subscription(r) and models.has_key(r) else None


def _int(key: str, default: int) -> int:
    try:
        return max(1, int(cfg().get(key) or default))
    except (TypeError, ValueError):
        return default


def simple(text: str, *, images: bool = False) -> bool:
    """这一句够不够简单（见文件开头）。"""
    t = STAMP.sub("", str(text or "")).strip()
    if not t or images or len(t) > _int("max_chars", MAX_CHARS) or len(t.split()) > _int("max_words", MAX_WORDS) or t.count("\n") > 1:
        return False
    if "`" in t or URL.search(t):
        return False
    low = t.lower()
    extra = [str(k).lower() for k in cfg().get("keywords") or [] if isinstance(k, str) and k.strip()]
    return not any(k in low for k in (*HARD, *extra))


def _said(messages: list[dict]) -> tuple[str, bool] | None:
    """这一轮进来的那句话（最后一条用户的话，后面只跟着 system 的说明：手册这类）→ (字, 带不带图)；后面已经有回复、工具结果 → None。"""
    for m in reversed(messages or []):
        role = m.get("role")
        if role == "system":
            continue
        if role != "user":
            return None
        blocks = m.get("content") or []
        text = "".join(str(b.get("text") or "") for b in blocks if isinstance(b, dict) and b.get("type") == "text")
        return text, any(isinstance(b, dict) and b.get("type") == "image" for b in blocks)
    return None


def _decide(spec, ref: str, req) -> str | None:
    target = cheap()
    if target is None or spec is None or spec.kind != "owner" or spec.origin not in ORIGINS or getattr(req, "json", False):
        return None
    if target == ref or models.subscription(ref) or getattr(spec, "model", None) != ref:
        return None
    if models.canonical(ref) != models.canonical(models.default_model() or "") or fallback.cooling(target) > 0:
        return None
    said = _said(req.messages)
    return target if said is not None and simple(said[0], images=said[1]) else None


def choose(ref: str, req) -> str | None:
    """这一次调用改用哪个：便宜的那个；不换 → None（见文件开头）。这一轮第一次调模型时定下，之后照它。"""
    pick = fallback.current()
    if pick is None:
        return None
    n = pick.notes
    if "auto" not in n:
        n["auto"], n["auto_for"] = _decide(pick.spec, ref, req), ref
    target = n["auto"]
    if not target or ref != n["auto_for"]:
        return None
    if ref in pick.used and not n.get("auto_main_ran"):  # Picker.calling 刚记上的主模型其实没调：拿掉（engine_turns.models 只记真调过的）
        pick.used.remove(ref)
    if target not in pick.used:
        pick.used.append(target)
    return target


async def run(target: str, ref: str, req) -> AsyncIterator[dict]:
    """便宜的那个答这一次；还没出字就出错：记冷却、这一轮回到主模型，这一次接着用主模型答（见文件开头）。"""
    from . import providers
    pick = fallback.current()
    quick, req.quick = req.quick, True  # 没成就换主模型：它自己那一层不重试
    said = False
    try:
        async for ev in providers.stream(target, req):
            if ev.get("type") == "done":
                if pick is not None:
                    pick.notes["answered"] = target
                yield ev
                return
            said = said or ev.get("type") == "text"
            yield ev
    except EngineError as e:
        if said or pick is None:
            raise
        fallback.noted(target, e)
        print(f"[engine] auto：{target} 没成（{e.kind}），这一轮回到 {ref}")
        pick.notes["auto"] = None
    finally:
        req.quick = quick
    pick.notes["auto_main_ran"] = True
    if ref not in pick.used:
        pick.used.append(ref)
    async for ev in providers.stream(ref, req):
        yield ev
