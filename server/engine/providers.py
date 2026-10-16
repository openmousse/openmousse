"""按模型引用找到哪一家、怎么接：anthropic → provider_anthropic（官方 SDK），openai → provider_openai（兼容接口）。

每家都是一个异步生成器，吐统一的事件：
  {"type": "text", "text"}       回复的字，一段段来
  {"type": "thinking", "text"}   思考摘要 / 工具之间的进度话（Claude 的 display=summarized / updates；DeepSeek、Kimi 的 reasoning）
  {"type": "tool_start", "id", "name"}   模型开始写一个工具调用
  {"type": "done", "message"}    最后一条：完整的 assistant 消息（中立格式，见 session.py）
出错抛 EngineError（errors.py）。
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from . import models, provider_anthropic, provider_openai


@dataclass
class Request:
    system: str = ""
    messages: list[dict] = field(default_factory=list)
    tools: list[dict] = field(default_factory=list)  # [{"name", "description", "schema"}]
    max_tokens: int | None = None   # None = 各家默认（Claude 给 32000，兼容接口不带）
    effort: str | None = None       # low / medium / high / xhigh / max（Claude 的 output_config.effort；兼容接口的 reasoning_effort）
    display: str | None = None      # Claude 的 thinking.display：summarized / omitted / updates
    json: bool = False              # 要一个 JSON（兼容接口带 response_format json_object；Claude 靠提示词，不许预填）
    timeout: float = 600


async def stream(ref: str, req: Request) -> AsyncIterator[dict]:
    name, model_id = models.parse_ref(ref)
    p = models.provider(name)
    mod = provider_anthropic if p["api"] == models.ANTHROPIC else provider_openai
    async for ev in mod.stream(p, model_id, req):
        yield ev
