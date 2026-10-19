"""备用链（内核第 13 步 A）：模型出了能换的错，换链里的下一个；一家几把密钥先换密钥；限流、计费错之后冷却一阵。

照 OpenClaw（packages/gateway-protocol/src/failover-reasons.ts、src/agents/failover/classify-core.ts 和 classification-rules.ts 的
分类，model-fallback-runner.ts 和 model-fallback-candidates.ts 的换法，embedded-agent-runner/run-entry.ts 的「出了字就不换」，
auth-profiles/usage-failure-state.ts 的冷却，commit c5c2b7fd）和 Hermes（agent/credential_pool.py 一家多把密钥，commit cf71b1aa）
的做法自己写的，没有拿代码。

链：[主模型, 备用…]。备用按这个顺序找，第一个有的算：
  Spec.fallbacks（调用方给的：engine.use.<这件事> 写成列表、赛博的 model 写成列表）
  → 这个模型自己的链（engine.model / engine.models 里写成列表、第一个是它的那一项）
  → engine.fallbacks（默认链）
不认识的 provider 去掉。没写备用 = 和以前一样，只用主模型。
用户自己的 Claude Code（claude-code/…，engine/claude_brain.py）不经 Picker：它那一轮出了换模型才好的错（登录掉了、命令坏了、套餐额度
用完），而且还没出字、没调工具，backup() 给往下换的链（同上：它自己写成列表的链，没写用 engine.fallbacks；只要不是套餐的），换上的
模型从头答这一句。临时的错（过载、5xx、超时、连不上、普通的限流）不换，照旧报错。

什么时候换（loop._model_step 在重试 MODEL_RETRIES 用完、或者本来就不能重试时问 Picker.failed）：
  - 这一步还没给用户看到字（OpenClaw：已经出了字再换，用户会看到两段不一样的回答）；外人回合的字没人看，不算；
  - 不是用户点了停（停了根本不问）；
  - 原因认得出来（reason_of）：rate_limit 限流 / overloaded 过载 / server 5xx / timeout 超时 / network 连不上、流断了 /
    auth 密钥不认、没配密钥 / billing 额度、余额、计费 / model_not_found 型号不存在 / context_overflow 上下文太长。
    别的不换：我们自己写错的请求（400、422，OpenClaw 的 format）、回的不像样（工具参数的 JSON 坏了）、拒答（拒答不是出错，
    Claude 服务端的 fallbacks 已经管了）、内容审核拦下的（OpenRouter 的 403 flagged 这类，和拒答一样）。
  context_overflow 只换到窗口比现在这个大的（compact.window）；没有就照旧交给压缩（loop 里的 Spec.recover）。
  换过以后这一轮剩下的步接着用新的；下一轮从主模型重新来（它在冷却就直接用下一个：reason = cooldown）。
  链里真有别的模型能换时（Picker.can_switch：有密钥、没在冷却 → providers.Request.quick）：各家自己那一层不再重试（Claude SDK 的
  max_retries、兼容接口的 RETRY_DELAYS），loop 的 MODEL_RETRIES 照旧重试完才换；只有外人回合的一次性调用（oneshot.ask_json 有
  总时限）超时不再等第二遍，直接换。没配备用链、链里的都用不了，照旧。

冷却（记在进程里，重启就清了）：只记换一把密钥 / 换个型号能好的那几种（OpenClaw 不记过载、5xx、超时、上下文太长）：
  rate_limit       这把密钥 + 这个型号（Anthropic、OpenAI 的限流按型号算）：30 秒起，连着再来翻倍，最多 1 小时
  billing          这把密钥（这一家的所有型号）：10 分钟起，翻倍，最多 6 小时
  auth             这把密钥：10 分钟起，翻倍，最多 1 小时（.env 里换了值就算另一把，马上能用）
  model_not_found  这个型号（不管哪把密钥）：10 分钟起，翻倍，最多 1 小时
  成功一次就清掉那个型号、那把密钥的记录；隔了一天的不算「连着」。链里剩下的都在冷却：2 分钟内冷却完的那个先拿来试一次
  （OpenClaw 的 probe）。
一家几把密钥（models.py：key_env 写成列表）：按顺序用第一把没在冷却的（Hermes 的 fill_first）；这一把限流、计费、不认了，
同一个型号换下一把再来；这一家的密钥都不行了才换下一个模型。
"""
from __future__ import annotations

import hashlib
import re
import time

from . import models
from .errors import EngineError

COOL = {"rate_limit": (30, 3600), "billing": (600, 6 * 3600), "auth": (600, 3600), "model_not_found": (600, 3600)}  # 原因 → (起步秒数, 最多)
KEY_REASONS = ("rate_limit", "billing", "auth")  # 换一把密钥可能就好的
STREAK = 24 * 3600          # 隔了这么久再错，不算「连着」
PROBE = 120                 # 链里剩下的都在冷却：这么多秒内冷却完的拿来试一次
_cool: dict[str, dict] = {}  # 记号 → {"until", "at"（monotonic）, "reason", "n", "label"（给人看的，不含密钥）}

BILLING = re.compile(r"billing|credit balance|insufficient[_ ](?:quota|balance|funds|credits?)|payment required|out of credits|"
                     r"exceeded your current quota|balance (?:is )?(?:too low|insufficient|not enough)|account (?:is )?(?:suspended|deactivated|"
                     r"disabled|inactive)|余额不足|欠费|额度不足|账户余额", re.I)
RATE = re.compile(r"rate[ _-]?limit|too many requests|throttl|requests per (?:minute|second|hour|day)|tokens per (?:minute|day)|"
                  r"\btpm\b|\brpm\b|resource[_ ]exhausted|请求过于频繁|请求太频繁|速率限制", re.I)
OVERLOAD = re.compile(r"overload|server[_ ]busy|(?:at|over) capacity|temporarily unavailable|service unavailable|过载|服务繁忙|系统繁忙", re.I)
AUTH = re.compile(r"api[ _-]?key|unauthori[sz]ed|authentication|invalid[_ ](?:token|credentials)|permission[_ ]denied|forbidden|"
                  r"not authori[sz]ed|密钥", re.I)
MODERATION = re.compile(r"flagged|moderation|content[ _-]?(?:policy|filter|management)|safety[ _-](?:system|filter|policy)|"
                        r"violat\w*\s+(?:our|the|its)\s+(?:usage\s+)?(?:policy|policies|terms)|inappropriate|high[ _-]risk|content exists risk|"
                        r"内容(?:审核|安全|风险)|敏感(?:内容|词|信息)|违规", re.I)
GONE = re.compile(r"model\b[^\n]{0,80}?(?:not[ _]found|does not exist|doesn't exist|not exist|is not available|not supported|"
                  r"deprecated|decommissioned|has been removed|no access)|no such model|unknown model|invalid model|"
                  r"模型不存在|不支持(?:该|这个)?模型", re.I)


# —— 为什么出错 ——————————————————————————————————————————————————————————————

def reason_of(e: EngineError) -> str | None:
    """这个错换个模型（或换一把密钥）可能就好吗：→ 原因（见文件开头），不换 → None。按 OpenClaw 的先后：
    先认上下文太长，再看状态码，状态码说不清的再看字眼。"""
    if e.kind in ("aborted", "refusal", "rule", "max_steps"):
        return None
    from . import compact  # compact 要导入 loop，loop 又导入这里：用到时再导
    msg, st = str(e), e.status
    if compact.is_overflow(e):
        return "context_overflow"
    if (st is None or 400 <= st < 500) and MODERATION.search(msg):
        return None  # 内容审核拦下的（OpenRouter 的 403 flagged、DeepSeek 的 Content Exists Risk……）：和拒答一样，不换、不冷却密钥
    if e.kind == "config":  # 没配密钥、对方不认这把密钥（provider 把 401 / 403 记成 config）
        if st in (401, 403) or "api key" in msg.lower() or AUTH.search(msg):
            return "billing" if BILLING.search(msg) else "auth"
        return None
    if st == 402:
        return "rate_limit" if RATE.search(msg) else "billing"
    if st == 429:
        return "billing" if BILLING.search(msg) else "rate_limit"
    if st in (401, 403):
        return "billing" if BILLING.search(msg) else "auth"
    if st == 404:
        return "model_not_found"
    if st in (400, 422):  # 多半是我们自己写错了：换谁都一样（OpenClaw 的 format）；字眼说清楚了是别的才换
        if GONE.search(msg):
            return "model_not_found"
        if BILLING.search(msg):
            return "billing"  # Anthropic 余额不够回的就是 400
        if RATE.search(msg):
            return "rate_limit"
        if AUTH.search(msg) and re.search(r"api[ _-]?key", msg, re.I):
            return "auth"  # Gemini 的密钥不对回 400
        return None
    if st == 413:
        return "context_overflow"
    if st in (408, 499, 504, 522, 524):
        return "timeout"
    if st in (503, 529):
        return "overloaded"
    if st is not None and (st == 409 or st >= 500):
        return "server"
    if e.kind == "timeout":
        return "timeout"
    if RATE.search(msg):
        return "rate_limit"
    if OVERLOAD.search(msg):
        return "overloaded"
    low = msg.lower()
    if "stream ended" in low or "can't reach" in low:
        return "network"
    if e.kind == "http":
        return "server"  # 流里来的错、模型带着 error 收尾
    return None


# —— 冷却 ——————————————————————————————————————————————————————————————————

def key_id(name: str, value: str) -> str:
    """一把密钥的记号：变量名 + 值的短指纹（不是密钥本身；值换了就是另一把）。"""
    return f"{name or '-'}#{hashlib.sha256(value.encode()).hexdigest()[:8]}" if value else (name or "-")


def _keys(p: dict) -> list[tuple[str, str, str]]:
    """[(记号, 变量名, 值)…]；不要密钥的本机模型也给一个空的，限流照样能记。"""
    return [(key_id(n, v), n, v) for n, v in p.get("keys") or []] or [(key_id("", ""), "", "")]


def _left(slot: str, now: float) -> float:
    c = _cool.get(slot)
    return max(0.0, c["until"] - now) if c else 0.0


def _key_wait(name: str, kid: str, model_id: str, now: float) -> float:
    return max(_left(f"key:{name}:{kid}", now), _left(f"key:{name}:{kid}:{model_id}", now))


def choose_key(p: dict, model_id: str) -> dict:
    """这一次用哪一把密钥：按顺序第一把没在冷却的；都在冷却就用最快冷却完的。→ p 的副本（key、key_name、key_id 换好）。"""
    keys = _keys(p)
    now = time.monotonic()
    waits = [_key_wait(p["name"], kid, model_id, now) for kid, _, _ in keys]
    i = next((i for i, w in enumerate(waits) if w <= 0), None)
    if i is None:
        i = min(range(len(keys)), key=lambda k: waits[k])
    kid, n, v = keys[i]
    return {**p, "key": v or p.get("key") or "", "key_name": n or p.get("key_name") or "", "key_id": kid}


def cooling(ref: str) -> float:
    """这个模型现在还要冷却多少秒（0 = 能用）：型号在冷却，或者这一家对这个型号的每一把密钥都在冷却。认不出来的 → 0。"""
    try:
        name, model_id = models.parse_ref(ref)
        p = models.provider(name)
    except EngineError:
        return 0.0
    now = time.monotonic()
    keys = min(_key_wait(name, kid, model_id, now) for kid, _, _ in _keys(p))
    return max(_left(f"model:{ref}", now), keys)


def _default_key(ref: str) -> str:
    try:
        name, model_id = models.parse_ref(ref)
        return choose_key(models.provider(name), model_id)["key_id"]
    except EngineError:
        return "-"


def note(ref: str, kid: str | None, reason: str) -> None:
    """记一次冷却（只记 COOL 里那几种）。连着再错翻倍；冷却中又错了（拿来试的那一次）也算连着。"""
    if reason not in COOL:
        return
    name, model_id = models.parse_ref(ref)
    kid = kid or _default_key(ref)
    if reason == "model_not_found":
        slot, label = f"model:{ref}", ref
    elif reason == "rate_limit":
        slot, label = f"key:{name}:{kid}:{model_id}", f"{ref} · {kid.split('#')[0]}"
    else:
        slot, label = f"key:{name}:{kid}", f"{name} · {kid.split('#')[0]}"
    now = time.monotonic()
    cur = _cool.get(slot)
    n = cur["n"] + 1 if cur and cur["reason"] == reason and now - cur["at"] < STREAK else 1
    base, cap = COOL[reason]
    _cool[slot] = {"until": now + min(cap, base * 2 ** (n - 1)), "at": now, "reason": reason, "n": n, "label": label}


def clear(ref: str, kid: str | None) -> None:
    """成功了一次：这个型号、这把密钥的冷却记录都清掉。"""
    try:
        name, model_id = models.parse_ref(ref)
    except EngineError:
        return
    _cool.pop(f"model:{ref}", None)
    if kid:
        _cool.pop(f"key:{name}:{kid}:{model_id}", None)
        _cool.pop(f"key:{name}:{kid}", None)


def status() -> list[dict]:
    """正在冷却的（给 /api/engine/status）：[{"what", "reason", "seconds"}]，不含密钥。"""
    now = time.monotonic()
    return [{"what": c["label"], "reason": c["reason"], "seconds": int(c["until"] - now)}
            for c in sorted(_cool.values(), key=lambda c: c["until"]) if c["until"] > now]


def reset() -> None:
    """清掉所有冷却（测试用）。"""
    _cool.clear()


def forget(provider: str) -> None:
    """一家的冷却全清掉（ChatGPT 套餐重新连上时：以前因为没连上、令牌失效记的冷却作废）。"""
    for slot in [s for s in _cool if s.startswith((f"key:{provider}:", f"model:{provider}/"))]:
        _cool.pop(slot, None)


# —— 一轮里用哪个 ——————————————————————————————————————————————————————————————

def chain(spec) -> list[str]:
    """[主模型, 备用…]（见文件开头）。"""
    rest = getattr(spec, "fallbacks", None)
    if rest is None:
        rest = models.own_chain(spec.model)
    if rest is None:
        rest = models.fallbacks()
    out = [spec.model]
    for r in rest:
        if isinstance(r, str) and r not in out and models.valid_ref(r):
            out.append(r)
    return out


class Picker:
    """一轮里用哪个模型（loop._run 一轮建一个）。ref = 现在用的；换过就一直用新的，直到这一轮结束。
    used = 这一轮真的调过的（按先后，不重复，engine_turns.models 记它）；final = 出了回复的那个（Result.model）。"""

    def __init__(self, spec) -> None:
        self.chain = chain(spec)
        self.i = 0
        self.used: list[str] = []
        self.final: str | None = None
        self.bad: set[str] = set()          # 这一轮里已经换走的模型
        self.bad_keys: set[tuple[str, str]] = set()
        self.skipped: dict | None = None
        if len(self.chain) > 1:
            waits = [cooling(r) for r in self.chain]
            if waits[0] > 0:  # 主模型在冷却：直接用下一个能用的（有密钥的）；都在冷却就用比主模型先冷却完的，没有就还是主模型
                usable = [k for k in range(1, len(waits)) if models.has_key(self.chain[k])]
                k = next((k for k in usable if waits[k] <= 0), None)
                if k is None:
                    soon = min(usable, key=lambda k: waits[k], default=None)
                    k = soon if soon is not None and waits[soon] < waits[0] else 0
                if k:
                    self.i = k
                    self.skipped = {"from": self.chain[0], "to": self.chain[k], "reason": "cooldown"}

    @property
    def ref(self) -> str:
        return self.chain[self.i]

    def take_skipped(self) -> dict | None:
        """一开始就跳过了主模型（它在冷却）：第一次调模型时报一次 model_switch。"""
        out, self.skipped = self.skipped, None
        return out

    def calling(self, ref: str) -> None:
        if ref not in self.used:
            self.used.append(ref)

    def can_switch(self) -> bool:
        """真有别的模型能换吗（有密钥、没换走过、没在冷却或者马上冷却完，同 _next）：有的话各家那一层不重试（providers.Request.quick）。
        只多几把密钥不算：换密钥只管限流、计费、不认密钥。"""
        return self._next(self.ref, "server") is not None

    def ok(self, ref: str, kid: str | None) -> None:
        self.final = ref
        clear(ref, kid)

    def failed(self, ref: str, e: EngineError, visible: bool) -> dict | None:
        """这一次调用没成：→ {"from", "to", "reason"}（to 和 from 一样 = 同一个型号换一把密钥再来），不换 → None。"""
        reason = reason_of(e)
        if reason is None or visible:
            return None
        kid = e.key or _default_key(ref)
        note(ref, kid, reason)
        self.bad_keys.add((ref, kid))
        if reason in KEY_REASONS and self._other_key(ref):
            return {"from": ref, "to": ref, "reason": reason}
        self.bad.add(ref)
        k = self._next(ref, reason)
        if k is None:
            return None
        self.i = k
        return {"from": ref, "to": self.chain[k], "reason": reason}

    def _other_key(self, ref: str) -> bool:
        """这一家还有没试过、没在冷却的密钥吗（同一个型号）。"""
        try:
            name, model_id = models.parse_ref(ref)
            keys = _keys(models.provider(name))
        except EngineError:
            return False
        now = time.monotonic()
        return len(keys) > 1 and any((ref, kid) not in self.bad_keys and _key_wait(name, kid, model_id, now) <= 0 for kid, _, _ in keys)

    def _next(self, ref: str, reason: str) -> int | None:
        """链里下一个：按链的顺序，没换走过、有密钥、没在冷却；上下文太长的只要窗口更大的。都在冷却：PROBE 秒内冷却完的那个。"""
        from . import compact
        cands = [k for k in range(len(self.chain)) if self.chain[k] not in self.bad and models.has_key(self.chain[k])]
        cands = [k for k in cands if k > self.i] + [k for k in cands if k < self.i]
        if reason == "context_overflow":
            cands = [k for k in cands if compact.window(self.chain[k]) > compact.window(ref)]
        waits = {k: cooling(self.chain[k]) for k in cands}
        free = next((k for k in cands if waits[k] <= 0), None)
        if free is not None:
            return free
        soon = min(cands, key=lambda k: waits[k], default=None)
        return soon if soon is not None and waits[soon] <= PROBE else None

    def exhausted(self, e: EngineError) -> EngineError:
        """链走完了还是不行：原来的错，后面写上试过哪几个（同一个 kind / retryable / status）。"""
        if len(self.used) < 2:
            return e
        out = EngineError(f"{e} (also tried: {', '.join(self.used[:-1])})", kind=e.kind, retryable=e.retryable, status=e.status)
        out.provider, out.key = e.provider, e.key
        return out


def backup(ref: str, *, kind: str = "owner", origin: str | None = None, via: str | None = None) -> list[str]:
    """套餐模型（claude-code/…）这一轮跑不了（engine/claude_brain.py：登录掉了、命令坏了、额度用完）往下换哪几个：models.chain_after
    （它自己写的链，没写用 engine.fallbacks），只要不是套餐、这一轮能花（models.may_spend）、有密钥的。打头的是现在就能用的（没在冷却），
    都在冷却就用 PROBE 秒内冷却完的那个（同 Picker._next）。→ [换上的, 它后面的…]（后面的交给 Spec.fallbacks 接着当链）；没有 → []。"""
    cands = [r for r in models.chain_after(ref) if not models.subscription(r) and models.may_spend(r, kind=kind, origin=origin, via=via)
             and models.has_key(r)]
    waits = {r: cooling(r) for r in cands}
    first = next((r for r in cands if waits[r] <= 0), None)
    if first is None:
        soon = min(cands, key=lambda r: waits[r], default=None)
        if soon is None or waits[soon] > PROBE:
            return []
        first = soon
    return [first, *(r for r in cands if r != first)]
