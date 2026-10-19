"""不是命令行脚本、也要用户点头的调用（内核第 13 步：MCP 服务里设成先问的工具、server.json 改成先问的写 skill）。

和命令行工具同一种卡（收件箱 kind tool，app 不用改）、同一张表（engine_calls，store.py），交卡、点了以后都走 engine/tools.py 那一套：
  ask()        关卡（engine/tools.py 的 gate）分到 ask：记下原样的调用（engine_calls.tool = 「前缀:名字」），交一张卡（tools.submit_card），
               回 approval-pending（同一次调用还在等 → 还是那张）
  同意         tools.on_decided 认出带前缀的调用，交给 run()：按前缀找回 register() 登记的跑法，照卡上的原样跑一次；结果、没做成，
               和命令行工具一样发回那个对话（对外接口的会话交回那个会话）。卡被改过不跑、点两下只跑一次，也是那边管
  拒绝、撤回、改一下  和命令行工具一模一样
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from i18n import L

from .loop import Tool, ToolCtx, ToolFailure

RUNNERS: dict[str, Callable[[str, dict, Any], Awaitable[str]]] = {}  # 前缀 → 照原样跑一次（名字, 参数, engine_calls 那一行）→ 输出；出错抛异常


@dataclass
class AskTool(Tool):
    """自己会交卡的工具：关卡分到 ask 时交给 ask_user（一般是 calls.ask），回 approval-pending 或一句没交成的原因。"""
    ask_user: Callable[[dict, ToolCtx], Awaitable[dict | str]] | None = None


def register(prefix: str, run: Callable[[str, dict, Any], Awaitable[str]]) -> None:
    RUNNERS[prefix] = run


async def ask(prefix: str, name: str, args: dict, ctx: ToolCtx, card: dict) -> dict | str:
    """记下这一次调用（engine_calls.tool = 「前缀:名字」，args = 原样的参数），交一张 kind tool 的卡。→ approval-pending 或没交成的原因。"""
    from . import tools
    who = ctx.agent or "main"
    tool = f"{prefix}:{name}"
    dedupe = "tool:" + hashlib.sha256(json.dumps([who, tool, args], ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:32]
    return await tools.submit_card(tool, json.dumps(args, ensure_ascii=False), None, card, dedupe, ctx, who=who)


async def run(row) -> str:
    """同意了（engine/tools.py 的 on_decided 调）：照 engine_calls 那一行原样跑一次 → 输出；跑不了、跑了没成功抛 ToolFailure。"""
    prefix, _, name = str(row["tool"]).partition(":")
    fn = RUNNERS.get(prefix)
    if fn is None:
        raise ToolFailure(L("这台服务器现在跑不了这一类调用", "this server can't run this kind of call now"))
    try:
        return await fn(name, json.loads(row["args"]), row)
    except ToolFailure:
        raise
    except Exception as e:  # noqa: BLE001 — 跑法自己的错（连不上、McpError……）：原话给对话
        raise ToolFailure(str(e) or type(e).__name__) from e
