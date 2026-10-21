"""按模型引用找到哪一家、怎么接：anthropic → provider_anthropic（官方 SDK），openai → provider_openai（兼容接口），
openai-responses → provider_responses（OpenAI 的 Responses API）。
codex/…、copilot/…（第 7 批）：用户自己登录的 Codex / Copilot 命令行，整次交给 engine/provider_cli.py（不经密钥、图那一套）。
chatgpt/…（用户自己的 ChatGPT 套餐，engine/chatgpt_plan.py）：也走 provider_responses；每次调之前现取访问令牌（快过期先刷新，排队），
回 401 就强制刷新一次再试，刷新过了还是 401 标「要重连」（用户在 ChatGPT 里断开不会通知我们）；别的错记成套餐最近一次的错（状态里给 app 看）。
谁能用它不在这里管：loop.run_turn 先问过 models.may_spend。
grok/…（10/20，用户自己的 Grok 订阅，engine/grok_plan.py）：同 chatgpt/…，也走 provider_responses；现取访问令牌，回 401 强制刷新一次再试，
还是 401 标「要重登」，403 换成说清楚的一句（账号拿不到 OAuth、改用 API 密钥）；走 Grok CLI 的订阅代理时多带它要的请求头。

每家都是一个异步生成器，吐统一的事件：
  {"type": "text", "text"}       回复的字，一段段来
  {"type": "thinking", "text"}   思考摘要 / 工具之间的进度话（Claude 的 display=summarized / updates；DeepSeek、Kimi 的 reasoning）
  {"type": "tool_start", "id", "name"}   模型开始写一个工具调用
  {"type": "done", "message"}    最后一条：完整的 assistant 消息（中立格式，见 session.py）
出错抛 EngineError（errors.py），带上是哪一家、哪一把密钥（e.provider / e.key，备用链按它冷却、换密钥）。
一家有好几把密钥时用哪一把由 engine/fallback.py 挑（第一把没在冷却的）。
工具结果里的图（⑫b）：发之前按这一家收不收图（PROVIDERS 的 images）现读回来、换成一句话、只留最近几张（engine/images.py 的 for_model）；
每家各算一次（备用链换到不收图的那家也对），会话里的记录不动。那一家因为图回 400 / 413（还没出字）：图都换成一句话再发一次，
这个模型 NO_IMAGES_FOR 秒内直接发字（自己加的兼容接口没写 images = false 时，不至于这个对话之后每一句都报错）。
第 3 批（2026-10-19，_more）：工具结果里的 PDF（engine/pdf.py）照这一家收不收 PDF 读回来或者换成一句话；不收图的模型、配了看图
（engine.use.vision）：用户发的图、工具结果里的图先交给看图模型描述，描述代替那句「这里有张图」（engine/vision.py）。都没有 = 原样。
"""
from __future__ import annotations

import asyncio
import dataclasses
import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from . import fallback, images, models, provider_anthropic, provider_openai, provider_responses
from .errors import EngineError, scrub

IMAGE_ERR = re.compile(r"image|vision|multimodal|multi-modal|media.?type|picture", re.I)
NO_IMAGES_FOR = 3600                 # 拒过图的模型多久内直接把图换成字（秒）
_no_images: dict[str, float] = {}    # 模型引用 → 上一次因为图被拒的时候（time.monotonic）


def refused_images(e: EngineError) -> bool:
    """这个错是不是因为请求里的图（这一次带着工具结果里的图时才看）。"""
    return e.status == 413 or (e.status in (400, 415, 422) and bool(IMAGE_ERR.search(str(e))))


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
    cache_ttl: str | None = None    # Claude 的缓存：None = 5 分钟；"1h" = 1 小时（隔几分钟才一句的聊天）
    cache_tail: bool = True         # Claude 的顶层自动缓存（对话尾巴）：一次性的问答（每次资料都不一样）关掉，只缓存开头的规矩
    key: str | None = None          # stream 填上：这一次用的是哪一把密钥的记号（fallback.key_id；成功了清它的冷却）
    quick: bool = False             # 备用链里还有能换的（loop 设）：各家自己那一层不再重试（SDK 的 max_retries、兼容接口的 RETRY_DELAYS），
                                    # 只剩 loop 的 MODEL_RETRIES；超时连那个也不等，直接换（同一个模型再等一遍多半还是等不到）


async def stream(ref: str, req: Request) -> AsyncIterator[dict]:
    from . import auto
    if (cheap := auto.choose(ref, req)) is not None:  # 第 7 批：自动选模型（engine/auto.py，engine.auto 不写 = 关）：这一句交给便宜的那个
        async for ev in auto.run(cheap, ref, req):
            yield ev
        return
    name, model_id = models.parse_ref(ref)
    if name in models.CLI_PLANS:  # 第 7 批：用户自己登录的命令行（codex/…、copilot/…，engine/provider_cli.py）：没有密钥、没有地址，整次交给它
        from . import provider_cli
        async for ev in provider_cli.stream(models.provider(name), model_id, req):
            yield ev
        return
    p = fallback.choose_key(models.provider(name), model_id)
    plan = models.subscription(ref) == "chatgpt"
    if plan:  # 用户自己的 ChatGPT 套餐：key 换成现在能用的访问令牌（choose_key 填的只是记号）
        from . import chatgpt_plan
        try:
            p["key"] = await chatgpt_plan.access_token()
        except EngineError as e:
            e.provider, e.key = name, p.get("key_id")
            raise
    grok = name in models.API_PLANS
    if grok:  # 10/20：用户自己的 Grok 订阅：key 换成现在能用的访问令牌（choose_key 填的只是记号）
        from . import grok_plan
        try:
            p["key"] = await grok_plan.access_token()
        except EngineError as e:
            e.provider, e.key = name, p.get("key_id")
            raise
        p["headers"] = {**(p.get("headers") or {}), **grok_plan.chat_headers(model_id)}
    req.key = p.get("key_id")
    mod = provider_anthropic if p["api"] == models.ANTHROPIC else provider_responses if p["api"] == models.RESPONSES else provider_openai
    sent = req
    pictures = False  # 这一次真带着图
    if images.tool_images(req.messages):  # 有工具结果里的图：这一家的样子另做一份（读文件放进线程；原来的 req 换模型时还要用）
        pictures = p.get("images", True) is not False and time.monotonic() - _no_images.get(ref, float("-inf")) > NO_IMAGES_FOR
        sent = dataclasses.replace(req, messages=await asyncio.to_thread(images.for_model, req.messages, pictures))
    sent = await _more(ref, p, req, sent, pictures)
    reauthed = False
    while True:
        started = False
        try:
            async for ev in mod.stream(p, model_id, sent):
                started = True
                if ev.get("type") == "done":  # 第 7 批：提示缓存保温（engine/warming.py，engine.cache_warming 不写 = 关）记下这一次
                    from . import warming
                    warming.remember(ref, req, p["api"])
                yield ev
            if plan:
                chatgpt_plan.note_ok()
            if grok:
                grok_plan.note_ok()
            return
        except EngineError as e:
            # 报错进库、进事件、进对话（还有套餐的 note_error）之前抹掉密钥和像密钥的串：这一次用的那把（套餐是访问令牌）和这一家配着的几把
            e.args = (scrub(str(e), str(p.get("key") or ""), *(v for _, v in p.get("keys") or [])),)
            if pictures and not started and refused_images(e):  # 因为图被拒：图都换成一句话再发一次
                print(f"[engine] {ref} 不收这一次的图，换成文字再发一次：{str(e)[:160]}")
                _no_images[ref] = time.monotonic()
                pictures = False
                sent = dataclasses.replace(req, messages=await asyncio.to_thread(images.for_model, req.messages, False))
                sent = await _more(ref, p, req, sent, False)
                continue
            if grok and not started and e.status == 401 and not reauthed:  # 10/20：Grok 订阅的令牌被拒：强制刷新一次再试
                reauthed = True
                try:
                    p = {**p, "key": await grok_plan.after_401(p["key"])}
                except EngineError as e2:
                    e2.provider, e2.key = name, p.get("key_id")
                    raise
                continue
            if grok:  # 刷新过了还是 401 → 要重登；403 → 说清楚（账号拿不到 OAuth、改用 API 密钥）；别的记成最近一次的错
                grok_plan.note_failure(e)
            if plan and not started and e.status == 401 and not reauthed:  # 套餐的令牌被拒：强制刷新一次再试
                reauthed = True
                try:
                    p = {**p, "key": await chatgpt_plan.after_401(p["key"])}
                except EngineError as e2:
                    e2.provider, e2.key = name, p.get("key_id")
                    raise
                continue
            if plan and e.status == 401:  # 刷新过了还是 401：这份令牌不能用了，等用户重新连
                chatgpt_plan.mark_reconnect(getattr(e, "code", None) or "unauthorized")
            elif plan:
                chatgpt_plan.note_error(getattr(e, "code", None), str(e))
            e.provider, e.key = name, p.get("key_id")
            raise


async def _more(ref: str, p: dict, req: Request, sent: Request, pictures: bool) -> Request:
    """第 3 批（见文件开头）。pictures = 这一次带着工具结果里的图（收图的模型）：那样不用描述。"""
    from . import pdf, vision
    if not pictures and vision.wanted(ref, p, req.messages):
        sent = dataclasses.replace(req, messages=await vision.substitute(req.messages))
    if pdf.tool_docs(sent.messages):
        sent = dataclasses.replace(sent, messages=await asyncio.to_thread(pdf.for_model, sent.messages, pdf.reads(p)))
    return sent
