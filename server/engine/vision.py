"""看图（第 3 批第 3 项，2026-10-19）：view_image 工具、「看图」用途（engine.use.vision）、不收图的主模型配了看图时图先换成描述。

view_image（主人回合；配了 engine.use.vision，或者 engine.media.view_image = true 才有）：一张图 + 一个问题。图从哪来写一样（engine/media.load）：
  path（工作区里的文件、图库里存着的图、这个对话的附件的路径）/ url（公网上的图：照 web_fetch 只去公网、钉住地址）/ attachment（附件 id）。
  主模型收图（这一轮的模型：目录里那一家 images 不是 false、最近没因为图被拒过；套餐的都收）：图放进工具结果（engine/images.py 现成的：
  卡大小、存进图库，会话里只记引用），它自己看、自己答。不收图：交给看图用途的模型答 question，回文字（包成资料）。
  不收图又没配看图（只开了 engine.media.view_image）：说清楚看不了。
不收图的模型、配了看图（providers.stream 发之前调 substitute）：用户发的图、工具结果里的图先交给看图模型描述一遍，描述代替那句「这里有张图」
  （provider_openai 的「this model can't read images」、images.for_model 的「Image omitted」）。按图的内容（sha256）+ 看图模型存描述
  （engine_media_cache），同一张图不重复花钱、发出去的前缀也稳定（缓存吃得到）。一次请求最多描述 NEW_MAX 张新的，多的这一次照旧是一句话、
  下一次接着描述。描述也包成资料（图里的字可能是网页上的指令）。工具结果里的老图照 images.for_model 的规矩先换成字（不描述）。
  engine.media.describe = false 关掉这一样（view_image 照常）。没配看图：一字不变。
花费：每问一次看图模型记一行 engine_media_usage（purpose vision；token 数照接口回的；价钱照 media.PRICES，没有就照对话模型的价目）。
没人在看的那几轮、后台任务的子 Agent：不给 view_image（unattended.NO_TOOLS、tasks.CHILD_NEVER）。描述那一样跟着模型走（不收图的模型才有）。
server.json（都可以不写）：
  "engine": {"use": {"vision": "google/gemini-3.5-flash-lite"}, "media": {"view_image": true, "describe": true}}
  engine.use.vision 也能写成对象：{"model": "openai/gpt-6-luna", "prompt": "描述用的提示词", "max_tokens": 1024}
参考（只读）：OpenClaw src/agents/tools/image-tool.ts（单独的看图模型 + prompt；路径、网址都收）、Hermes tools/vision_tools.py（vision_analyze：
  辅助的看图模型回答问题，网址先下载、认是不是图）、agent/image_routing.py（主模型收图就原样给，不收就交给辅助模型描述）。只借设计。
"""
from __future__ import annotations

import asyncio
import base64
import contextvars
import hashlib
import time

from config import raw
from i18n import L

from . import images, media, models
from .loop import Tool, ToolCtx, ToolFailure, ToolOutput

NAME = "view_image"
READ = "read"
NEW_MAX = 8                  # 一次请求最多描述几张新的图（多的下一次接着描述）
DESCRIBE_TOKENS = 1024
ANSWER_TOKENS = 2048
READ_MAX = images.READ_MAX
DESCRIBE = ("Describe this image for someone who can't see it, so they can answer questions about it: what it shows, all visible text "
            "copied exactly (keep its language), numbers, tables and charts with their values, interface elements, and anything unusual. "
            "Don't identify real people by face. Be complete but don't pad.")
ANSWER = ("Look at the image and answer the question about it. Copy any text you rely on exactly. If the image doesn't show what's asked, "
          "say so plainly. Don't identify real people by face.")
_describing: contextvars.ContextVar[bool] = contextvars.ContextVar("vision_describing", default=False)  # 看图模型自己的那次调用：不再描述


def cfg() -> dict:
    e = raw().get("engine")
    m = e.get("media") if isinstance(e, dict) else None
    return m if isinstance(m, dict) else {}


def on() -> bool:
    """配了看图用途（engine.use.vision 写对了）。"""
    return media.choice("vision") is not None


def tool_on() -> bool:
    return on() or cfg().get("view_image") is True


def describe_on() -> bool:
    return on() and cfg().get("describe") is not False


# —— 不收图的模型：图换成描述 ———————————————————————————————————————————————————————————————

def blind(ref: str, p: dict) -> bool:
    """这一家现在不收图：目录 / engine.providers 里 images = false，或者 NO_IMAGES_FOR 秒内因为图被拒过（providers.stream 记的）。"""
    from . import providers
    return p.get("images", True) is False or time.monotonic() - providers._no_images.get(ref, float("-inf")) <= providers.NO_IMAGES_FOR  # noqa: SLF001


def has_images(messages: list[dict]) -> bool:
    return any(b.get("type") == "image" for m in messages if m.get("role") in ("user", "tool") for b in m.get("content") or [])


def wanted(ref: str, p: dict, messages: list[dict]) -> bool:
    """providers.stream 问：这一次要不要先把图换成描述。看图模型自己的那次调用、看图模型就是这一个：不换。"""
    if _describing.get() or not has_images(messages) or not describe_on() or not blind(ref, p):
        return False
    c = media.choice("vision")
    return c is not None and models.canonical(c.ref) != models.canonical(ref)


def _key(c: media.Choice, sha: str, question: str = "") -> str:
    q = hashlib.sha256(question.encode()).hexdigest()[:16] if question else "describe"
    return f"vision:{c.ref}:{sha}:{q}"


async def ask(c: media.Choice, b64: str, mime: str, question: str = "", *, conversation: str | None = None, agent: str | None = None,
              cache: bool = True) -> str:
    """看图模型看这张图：question 空 = 描述一遍。→ 它的话（没包；调用方包成资料）。按 (看图模型, 图的 sha256, 问题) 存着，同一张图同一个问题不再花钱。
    出错抛 media.MediaError。"""
    sha = hashlib.sha256(base64.b64decode(b64)).hexdigest()
    key = _key(c, sha, question)
    if cache and (hit := media.cached(key)) is not None:
        return hit
    prompt = str(c.opts.get("prompt") or DESCRIBE) if not question else f"{ANSWER}\n\nQuestion: {question}"
    limit = int(c.opts.get("max_tokens") or (ANSWER_TOKENS if question else DESCRIBE_TOKENS))
    token = _describing.set(True)
    try:
        with media.Meter("vision", c, conversation, agent) as m:
            text, usage, _ = await media.ask(c, prompt, [{"type": "image", "mime": mime or "image/png", "data": b64}], max_tokens=limit)
            m.unit, m.units, m.tokens_in, m.tokens_out = "token", usage["in"] + usage["out"], usage["in"], usage["out"]
    finally:
        _describing.reset(token)
    if not text.strip():
        raise media.MediaError("shape", f"{c.ref}: empty answer")
    if cache:
        media.remember(key, "vision", c.ref, text.strip())
    return text.strip()


def _said(c: media.Choice, label: str, text: str) -> str:
    head = f"[Image{f' ({label})' if label else ''}, described by the vision model {c.ref} because this model can't see images:]"
    return head + "\n" + media.wrap(f"vision {c.ref}", text)


async def substitute(messages: list[dict]) -> list[dict]:
    """发给不收图的模型之前：图都换成看图模型的描述（见文件开头）。不改传进来的消息。看图模型出错：那一张照旧换成一句话（不拖垮这一轮）。"""
    c = media.choice("vision")
    if c is None:
        return await asyncio.to_thread(images.for_model, messages, False)
    total = images.tool_images(messages)
    old = images.dropped(total)
    seen = 0
    plan: list[tuple[int, int, str, str, str]] = []  # (第几条, 第几块, base64, mime, 是什么)
    out: list[dict] = []
    for i, m in enumerate(messages):
        role = m.get("role")
        if role not in ("user", "tool") or not any(b.get("type") == "image" for b in m.get("content") or []):
            out.append(m)
            continue
        blocks: list[dict] = []
        for b in m.get("content") or []:
            if b.get("type") != "image":
                blocks.append(b)
                continue
            if role == "tool":
                seen += 1
                if m.get("error"):
                    blocks.append({"type": "text", "text": f"[Image omitted from an error result: {images.describe(b)}.]"})
                    continue
                if seen <= old:
                    blocks.append({"type": "text", "text": f"[Earlier image no longer shown, to save room: {images.describe(b)}. "
                                                            "Take it or read it again if you need to see it.]"})
                    continue
                data = await asyncio.to_thread(images.load, b)
                label = str(b.get("label") or "")
                if data is None:
                    blocks.append({"type": "text", "text": f"[Image no longer available: {images.describe(b)}.]"})
                    continue
            else:
                data, label = str(b.get("data") or ""), ""
                if not data:
                    blocks.append({"type": "text", "text": "[Image omitted: it couldn't be read.]"})
                    continue
            plan.append((i, len(blocks), data, str(b.get("mime") or "image/png"), label))
            blocks.append({"type": "text", "text": ""})  # 占位：下面填描述
        out.append({**m, "content": blocks})
    if not plan:
        return out
    want: dict[str, tuple[str, str]] = {}  # sha → (base64, mime)
    keys = []
    for _, _, data, mime, _ in plan:
        try:
            sha = hashlib.sha256(base64.b64decode(data)).hexdigest()
        except ValueError:
            sha = ""
        keys.append(sha)
        if sha and sha not in want:
            want[sha] = (data, mime)
    got: dict[str, str] = {}
    fresh = []
    for sha in want:
        hit = media.cached(_key(c, sha))
        if hit is not None:
            got[sha] = hit
        else:
            fresh.append(sha)
    results = await asyncio.gather(*(ask(c, want[sha][0], want[sha][1]) for sha in fresh[:NEW_MAX]), return_exceptions=True)
    why: dict[str, str] = {}
    for sha, r in zip(fresh[:NEW_MAX], results):
        if isinstance(r, str):
            got[sha] = r
        else:
            why[sha] = str(r)[:200] if isinstance(r, Exception) else "cancelled"
            if isinstance(r, BaseException) and not isinstance(r, Exception):
                raise r
    for (i, j, _, _, label), sha in zip(plan, keys):
        if sha in got:
            text = _said(c, label, got[sha])
        elif sha in why:
            text = f"[Image{f' ({label})' if label else ''}: this model can't see images and the vision model couldn't describe it ({why[sha]}).]"
        else:
            text = f"[Image{f' ({label})' if label else ''}: this model can't see images; it will be described on the next message.]"
        out[i]["content"][j] = {"type": "text", "text": text}
    return out


# —— view_image ————————————————————————————————————————————————————————————————————

def main_sees(ctx: ToolCtx) -> bool:
    """这一轮的主模型收不收图（找不到这一轮 = 默认模型）。"""
    return media.sees_images(media.turn_model(ctx.turn) or models.default_model() or None)


async def picture(src: media.Source) -> tuple[dict | None, str, str]:
    """拿到的文件 → (图块 | None, 给模型的一句, 认出来的类型)。不是图：抛 ToolFailure。HEIC 这种（手机照片）有 pillow_heif 时转成 JPEG。"""
    kind = images.sniff(src.data)
    data = src.data
    if kind is None and src.path is not None and (src.mime.startswith("image/") or src.name.lower().endswith((".heic", ".heif", ".tif", ".tiff"))):
        import files
        conv = await asyncio.to_thread(files.image_for_model, src.path)  # 附件里的 HEIC / TIFF：files.py 现成的转法
        if conv is not None:
            data, kind = base64.b64decode(conv[1]), conv[0]
    if kind is None:
        raise ToolFailure(L(f"{src.label} 不是能看的图（PNG、JPEG、GIF、WebP）", f"{src.label} isn't an image that can be viewed (PNG, JPEG, GIF or WebP)"))
    block, said = await asyncio.to_thread(images.make, data, label=src.label, mime=kind)
    return block, said, kind


def tool(agent: str) -> Tool | None:
    if not tool_on():
        return None
    from .handkit import levelled

    async def run(args: dict, ctx: ToolCtx):
        question = " ".join(str(args.get("question") or "").split())[:2000]
        src = await media.load(args, ctx, max_bytes=READ_MAX, what=("图", "image"))
        block, said, kind = await picture(src)
        if block is None:
            return f"{src.label}: {said}"
        head = f"{src.label}: image [{kind}] · {block['w']}x{block['h']}" + (f"\n{said}" if said else "")
        if main_sees(ctx):
            return ToolOutput(head + ("\nThe image is attached: look at it and answer the question yourself." if question else "\nThe image is attached."),
                              [block])
        c = media.choice("vision")
        if c is None:
            raise ToolFailure(L("这一轮的模型看不了图，也没配看图的模型（engine.use.vision）：换一个能看图的模型，或者请用户配一个看图模型",
                                "This turn's model can't see images and no vision model is set up (engine.use.vision): switch to a model that "
                                "can see images, or ask the user to set one up"))
        b64 = await asyncio.to_thread(images.load, block)
        if b64 is None:
            raise ToolFailure(L("图存不下来，看不了", "The image couldn't be stored, so it can't be viewed"))
        try:
            text = await ask(c, b64, block.get("mime") or kind, question, conversation=ctx.conversation, agent=ctx.agent)
        except media.MediaError as e:
            raise ToolFailure(L(f"看图模型（{c.ref}）没答上来：{e}", f"The vision model ({c.ref}) couldn't answer: {e}")) from None
        what = "Answer" if question else "Description"
        return (f"{head}\n{what} from the vision model {c.ref} (this turn's model can't see images itself):\n"
                + media.wrap(f"view_image {src.label}"[:200].replace('"', "%22"), text))

    vis = media.choice("vision")
    return Tool(
        name=NAME, kinds=("owner",), run=run, timeout=180, max_output=20_000,
        title=lambda a: str(a.get("path") or a.get("url") or a.get("attachment") or "")[:80],
        classify=lambda a: levelled(NAME, (READ, L("只看一张图（网址只去公网）；问看图模型按量花一点钱，记在用量里",
                                                   "Only looks at an image (web addresses: public internet only); asking the vision model costs a little and is logged"))),
        description=L(
            "看一张图：工作区里的图片文件、图库里存着的截图（工具结果里写着的 …/engine/images/… 路径）、这个对话的附件（路径或 attachment id）、"
            "公网上的图（url）。你自己看得见图时图会放进结果里；看不见时" + (f"交给看图模型 {vis.ref} 回答 question。" if vis else "说看不了。")
            + "工作区里的图 read_file 也能读：这个工具多了网址、附件、图库，还有替看不见图的模型回答。",
            "View one image: an image file in your workspace, a stored screenshot (the …/engine/images/… path a tool result gave), an "
            "attachment of this conversation (its path or attachment id), or an image on the public web (url). If you can see images, the "
            "image comes back in the result; if you can't, " + (f"the vision model {vis.ref} answers question for you. " if vis else "it says so. ")
            + "read_file also reads workspace images; this tool adds web addresses, attachments and the image store, and answers for models "
            "that can't see images."),
        schema={"type": "object", "properties": {
            "path": {"type": "string", "description": L("工作区里的路径，或者消息、工具结果里写着的服务器上的路径", "A workspace path, or the server path a message or tool result gave")},
            "url": {"type": "string", "description": L("公网上一张图的网址", "Address of an image on the public web")},
            "attachment": {"type": "string", "description": L("这个对话的附件 id", "An attachment id from this conversation")},
            "question": {"type": "string", "description": L("想从图里知道什么（不写 = 描述一遍）", "What you want to know from the image (empty = describe it)")}}})
