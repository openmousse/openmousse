"""死循环检测（第 2 批，10/19，engine.loop.guard，默认关）：一轮里同一个工具反复失败、只读的调用反复拿到一样的结果，在那个结果后面补一句
提醒；没人在看的那几轮（定时任务、心跳、收件箱接着做、后台任务）到阈值硬停，有人在看的只提醒。

三种（阈值照 Hermes 的默认）：
  exact        同一个工具、同样的参数失败了：第 2 次提醒；硬停时第 5 次以后不再跑
  same_tool    同一个工具这一轮连着失败（中间没成功过）：第 3 次提醒（带一段怎么排查）；硬停时第 8 次停下这一轮。跑命令、开网页的那几个
               （exec、host_exec、browser……）失败本来就常见（测试没过、网页超时），这一种不硬停它们
  no_progress  只读的调用、同样的参数、同样的结果：第 2 次提醒；硬停时第 5 次以后不再跑
  有进展：一个会改东西的调用成功了，前面的失败和重复都清零（下一次同样的调用算新的尝试，不算重放）。
  失败 = 工具自己报错、超时（结果带 error）；关卡没放、交给用户点头的不算。
硬停：before() 拦下的那个调用不跑（结果写明为什么）、after() 判停的那一个照常有结果；这一轮跑完这一批工具就停（engine/loop.py：
  status halted，最后补一段话告诉用户停在哪、怎么接着来）。
谁算有人在看：这一轮的来路（Spec.origin）是 user / relay / api / cli（用户自己在 app、命令行、对外接口里说的，或者转过来有人等着回答的）；
  别的（auto、task……）都算没人在看。engine.loop.guard.hard_stop = "always" / "never" 能改。
Claude Code 跑的回合：它调我们的工具走 /mcp（engine/mcp_engine.py），每把令牌（每一轮）也有一个 Guard：提醒照样补进结果；硬停的那几种
  不再跑那个调用，这一轮停不停由它自己定（我们停不了它这一轮，只能不再替它跑）。
server.json：engine.loop.guard = true，或 {"warn_after": {"exact": 2, "same_tool": 3, "no_progress": 2},
  "stop_after": {"exact": 5, "same_tool": 8, "no_progress": 5}, "hard_stop": "unattended" | "always" | "never"}。

阈值、三种检测和提醒的说法改写自 Hermes Agent（NousResearch/hermes-agent，commit f4ac0bea）的 agent/tool_guardrails.py（ToolCallGuardrails
的 before_call / after_call、_DECISION_MESSAGES、_tool_failure_recovery_hint）和 run_agent.py 停下时的那句话，MIT，
Copyright (c) 2025 Nous Research；许可全文见仓库根目录的 THIRD_PARTY_NOTICES。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from i18n import L

WARN = {"exact": 2, "same_tool": 3, "no_progress": 2}
STOP = {"exact": 5, "same_tool": 8, "no_progress": 5}
TOLERANT = frozenset({"exec", "sandbox_exec", "host_exec", "process", "sandbox_process", "browser", "sandbox_browser", "web_fetch"})
ATTENDED = frozenset({"user", "relay", "api", "cli"})

MESSAGES = {
    "exact_warn": ("{tool} has failed {count} times with identical arguments. This looks like a loop; inspect the error and change "
                   "strategy instead of retrying it unchanged."),
    "no_progress_warn": ("{tool} returned the same result {count} times. Use the result already provided or change the query instead "
                         "of repeating it unchanged."),
    "same_tool_warn": ("{tool} has failed {count} times this turn. This looks like a loop. Don't give up on tools, but diagnose before "
                       "retrying: inspect the latest error and check your assumptions first. Try different arguments, a narrower query or "
                       "path, or a different tool that can make progress. If the blocker is outside your control, say what it is after one "
                       "diagnostic attempt instead of repeating the same failing call."),
    "exact_block": ("Blocked {tool}: the same tool call failed {count} times with identical arguments. Stop retrying it unchanged; change "
                    "strategy or explain the blocker."),
    "no_progress_block": ("Blocked {tool}: this read-only call returned the same result {count} times. Stop repeating it unchanged; use "
                          "the result already provided or try a different query."),
    "same_tool_halt": ("Stopped {tool}: it failed {count} times this turn. Stop retrying the same failing tool path and choose a different "
                       "approach."),
}


@dataclass
class Decision:
    action: str          # warn / block（这一次不跑）/ halt（这一轮跑完这一批就停）
    code: str            # MESSAGES 的键
    tool: str
    count: int

    @property
    def message(self) -> str:
        return MESSAGES[self.code].format(tool=self.tool, count=self.count)

    def note(self) -> str:
        """补在工具结果后面的那一句（Hermes 的 append_toolguard_guidance）。"""
        label = "Tool loop hard stop" if self.action in ("halt", "block") else "Tool loop warning"
        return f"\n\n[{label}: {self.code}; count={self.count}; {self.message}]"


def cfg() -> dict | None:
    """engine.loop.guard：true 或 {…}；不写 / false = 不查（None）。"""
    from . import models
    lp = models.cfg().get("loop")
    v = lp.get("guard") if isinstance(lp, dict) else None
    if v is True:
        return {}
    return v if isinstance(v, dict) and v.get("enabled", True) is not False else None


def _n(v: Any, dflt: int) -> int:
    return v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else dflt


@dataclass
class Guard:
    """一轮一个（counters 一轮一清）。before / after 都是同步的：一起跑的几个调用共用它也不会乱。"""
    warn: dict = field(default_factory=lambda: dict(WARN))
    stop: dict = field(default_factory=lambda: dict(STOP))
    hard: bool = False                                   # 到阈值硬停（没人在看的那几轮）
    halted: Decision | None = None                       # 判过硬停：这一轮跑完这一批就停
    _exact: dict[str, int] = field(default_factory=dict)        # 签名 → 失败几次
    _same: dict[str, int] = field(default_factory=dict)         # 工具 → 连着失败几次
    _repeat: dict[str, tuple[str, int]] = field(default_factory=dict)  # 签名 → (结果指纹, 一样了几次)

    @classmethod
    def make(cls, origin: str | None) -> Guard | None:
        """按 engine.loop.guard 配一个；没开 → None。origin = 这一轮的来路（判断有没有人在看）。"""
        c = cfg()
        if c is None:
            return None
        w, s = c.get("warn_after") or {}, c.get("stop_after") or {}
        mode = str(c.get("hard_stop") or "unattended").lower()
        hard = mode == "always" or (mode != "never" and (origin or "") not in ATTENDED)
        return cls(warn={k: _n(w.get(k), v) for k, v in WARN.items()} if isinstance(w, dict) else dict(WARN),
                   stop={k: _n(s.get(k), v) for k, v in STOP.items()} if isinstance(s, dict) else dict(STOP), hard=hard)

    @staticmethod
    def signature(name: str, args: dict) -> str:
        return hashlib.sha256((name + "\0" + json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)).encode()).hexdigest()[:24]

    def before(self, name: str, args: dict) -> Decision | None:
        """跑之前：硬停时，同样失败够了次数的、只读没进展够了次数的，这一次不跑（→ block，这一轮跑完这一批就停）。"""
        if not self.hard:
            return None
        sig = self.signature(name, args)
        if self._exact.get(sig, 0) >= self.stop["exact"]:
            return self._halt(Decision("block", "exact_block", name, self._exact[sig]))
        rep = self._repeat.get(sig)
        if rep and rep[1] >= self.stop["no_progress"]:
            return self._halt(Decision("block", "no_progress_block", name, rep[1]))
        return None

    def after(self, name: str, args: dict, *, ok: bool, text: str, read: bool) -> Decision | None:
        """跑完：记下这一次 → 要补的提醒 / 判硬停（没有 → None）。read = 这一次分类是只读的。"""
        sig = self.signature(name, args)
        if not ok:
            exact = self._exact[sig] = self._exact.get(sig, 0) + 1
            same = self._same[name] = self._same.get(name, 0) + 1
            self._repeat.pop(sig, None)
            if self.hard and name not in TOLERANT and same >= self.stop["same_tool"]:
                return self._halt(Decision("halt", "same_tool_halt", name, same))
            if exact >= self.warn["exact"]:
                return Decision("warn", "exact_warn", name, exact)
            if same >= self.warn["same_tool"]:
                return Decision("warn", "same_tool_warn", name, same)
            return None
        self._exact.pop(sig, None)
        self._same.pop(name, None)
        if not read:  # 改了东西、成功了：算有进展，前面的失败和重复都清零
            self._exact.clear()
            self._same.clear()
            self._repeat.clear()
            return None
        h = hashlib.sha256(text.encode("utf8", "replace")).hexdigest()[:24]
        last = self._repeat.get(sig)
        n = last[1] + 1 if last and last[0] == h else 1
        self._repeat[sig] = (h, n)
        return Decision("warn", "no_progress_warn", name, n) if n >= self.warn["no_progress"] else None

    def _halt(self, d: Decision) -> Decision:
        if self.halted is None:
            self.halted = d
        return d


def stop_reply(d: Decision) -> str:
    """硬停以后这一轮最后补的那段话（给用户看；Hermes run_agent.py 停下时的说法）。"""
    return L(f"我停下了，没有再试：{d.tool} 已经试了 {d.count} 次都没有进展。上面最后一个结果写着卡在哪。告诉我接下来怎么办，或者回一句「继续」，"
             "我换个办法再试。",
             f"I stopped retrying because I kept running {d.tool} {d.count} times without making progress. The last result above shows "
             "what blocked it. Tell me how you'd like to proceed, or send \"continue\" and I'll try a different approach.")
