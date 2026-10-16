"""OpenMousse 自己的内核（builtin）。Leo 10/15 定：自己的内核，Python 写进服务器；借 pi、OpenClaw 的设计，三家都是 MIT。
交接和十步计划：~/openmousse-wt/engine/README.md；规矩（三种回合、关卡、事件流）：~/openmousse-wt/harness/README.md。

分工：
  models.py              模型引用 provider/model、各家接口和密钥、server.json 的 engine 段、价钱
  providers.py           按模型找到哪一家：provider_anthropic.py（官方 SDK）/ provider_openai.py（OpenAI 兼容接口）
  session.py             中立格式的消息、会话（内存 / 库里的一棵树，撤回 = 换叶子）
  loop.py                一轮：模型 → 工具 → 模型……；三种回合（owner / cyber / outsider）按回合卡工具；插话、后续、停
  events.py, store.py    事件流：对话 → 一轮 → 每一步（mousse.db 的 engine_* 四张表）+ 进程里的实时广播
  oneshot.py             外人回合的一次性 JSON（名片 Agent、Doorman、llmjson.ask 改走它，能换模型）
  cyber.py               赛博回合（后端 + 测试入口）
  owner.py               claw 选 builtin 时的主对话骨架
  api.py                 /api/engine/…

这一层只在用到时才导入 anthropic SDK；不配 server.json 的 engine 段，服务里一切照旧。
"""
from __future__ import annotations

from .errors import EngineError
from .loop import Control, Result, Spec, Tool, ToolCtx, run_turn
from .models import default_model, fallback_allowed, route, valid_ref
from .oneshot import ask_json
from .session import DbSession, MemorySession, system_note, text_of, tool_result, user

__all__ = ["EngineError", "Control", "Result", "Spec", "Tool", "ToolCtx", "run_turn", "default_model", "fallback_allowed", "route",
           "valid_ref", "ask_json", "DbSession", "MemorySession", "system_note", "text_of", "tool_result", "user"]
