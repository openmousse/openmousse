"""生图（第 3 批第 6 项，2026-10-19）：image_generate 工具、「生图」用途（engine.use.image）。

用途 engine.use.image（不写 = 没有这个工具）：
  openai/<型号>     POST <url>/images/generations（{model, prompt, n, size?, quality?, background?, output_format?}）→ data[].b64_json、usage；
                    有参考图走 POST <url>/images/edits（multipart：image / image[]、prompt……）：gpt-image-2、gpt-image-1.5、gpt-image-1、gpt-image-1-mini
  google/<型号>     Gemini 自己的接口：responseModalities [TEXT, IMAGE]（imageConfig.aspectRatio），参考图放进 inline_data；一次一张，要几张调几次：
                    gemini-3.1-flash-image、gemini-3-pro-image……
  fal/<模型路径>    POST https://fal.run/<模型路径>（Authorization: Key <FAL_KEY>；{prompt, num_images, image_size?, image_url? / image_urls?,
                    sync_mode: true}）→ images[].url（sync_mode 回 data: 地址；回 https 的照 web_fetch 只去公网地下载）：fal-ai/flux/dev……
  comfyui/workflow  本机的 ComfyUI（默认 http://127.0.0.1:8188）：{"model": "comfyui/workflow", "workflow": "~/comfy/workflow_api.json",
                    "prompt_node": "6", "prompt_input": "text"}：把提示词写进那个节点，POST /prompt，等 /history/<id> 出结果，GET /view 拿图。
                    不花钱、不出门；参考图不收（要的话在工作流里自己接）
  对象里还能写 size、quality、background、format、aspect_ratio 的默认值，url、timeout。
image_generate（主人回合；配了 engine.use.image 才有）：prompt + 可选的参考图（image / images：path、url、attachment 都认，同 view_image，最多
  REF_MAX 张）+ 张数（count 1–4）、size、aspect_ratio、quality、background、format。生成好的每一张：进图库（engine/images.py，工具结果里带图，
  模型看得见）、存成这个对话的附件（files.save_generated），这一轮回完挂到回复下面（app 现在就显示缩略图、能点开看大图）。
分类 undo（自己定的，理由）：只在这台服务器上多几个文件（这个对话的附件、图库），除了发给用户自己配的那一家，什么都不往外发、不改外面的东西；
  花的钱每次记一行、一次最多 COUNT_MAX 张，按钱的上限归第 4 批的闸管；每张都先问会让这个工具没法用。要每次都问：engine.tools.levels 写
  {"image_generate": "ask"}。
不放后台（自己定的，理由）：图要在这一轮里交回去（模型看得见、回复里挂得上），各家一般十几秒到一分钟，工具的超时（TIMEOUT）够；慢的本机
  工作流照样在超时以内等，等不到就说没出来。
花费：每次记一行 engine_media_usage（purpose image；OpenAI 照接口回的 token 数和价目；Gemini 照 token；fal 价目里没有记 null；本机 0）。
没人在看的那几轮、后台任务的子 Agent：不给（unattended.NO_TOOLS、tasks.CHILD_NEVER）。
参考（只读）：OpenClaw docs/tools/image-generation.md（参数：prompt、image / images、size、aspectRatio、quality、background、outputFormat、count；
  OpenAI 的 edits 收几张图；fal 的 sync_mode；回的网址按公网的规矩下载、不带密钥）、Hermes tools/image_generation_tool.py（各家的请求和结果）。
  只借设计。
"""
from __future__ import annotations

import asyncio
import base64
import json
import re
import time
import uuid
from pathlib import Path

from i18n import L

from . import images, media
from .loop import Tool, ToolCtx, ToolFailure, ToolOutput

NAME = "image_generate"
UNDO = "undo"
COUNT_MAX = 4
REF_MAX = 4
REF_BYTES = 20 * 1024 * 1024
TIMEOUT = 300.0
COMFY_POLL = 1.0
FORMATS = {"png": "image/png", "jpeg": "image/jpeg", "jpg": "image/jpeg", "webp": "image/webp"}
SIZE = re.compile(r"^(?:\d{3,4}x\d{3,4}|auto)$")
RATIO = re.compile(r"^\d{1,2}(?:\.\d)?:\d{1,2}(?:\.\d)?$")


def on() -> bool:
    return media.choice("image") is not None


class Picture:
    def __init__(self, data: bytes, mime: str) -> None:
        self.data, self.mime = data, mime


def _opt(c: media.Choice, args: dict, key: str, ok: tuple[str, ...] | re.Pattern | None = None) -> str | None:
    v = str(args.get(key) or c.opts.get(key) or "").strip().lower()
    if not v:
        return None
    if isinstance(ok, tuple) and v not in ok or isinstance(ok, re.Pattern) and not ok.match(v):
        raise ToolFailure(L(f"{key} 写得不对（{v}）", f"{key} isn't valid ({v})"))
    return v


async def generate(c: media.Choice, prompt: str, refs: list[media.Source], *, count: int, size: str | None, ratio: str | None, quality: str | None,
                   background: str | None, fmt: str | None, conversation: str | None, agent: str | None) -> list[Picture]:
    """按 c 生成 count 张（refs = 参考图）。出错抛 media.MediaError（报错里不带密钥）。记一行花费。"""
    with media.Meter("image", c, conversation, agent) as m:
        m.unit = "image"
        if c.provider == "openai" or c.opts.get("api") == "openai":
            pics, usage = await _openai(c, prompt, refs, count, size, quality, background, fmt)
            m.unit, m.tokens_in, m.tokens_out = "token", usage.get("in", 0), usage.get("out", 0)
            m.extra = {"image_tokens_in": usage.get("image_in", 0)}
            m.units = m.tokens_in + m.tokens_out
            m.unpriced = not (m.tokens_in or m.tokens_out)
        elif c.provider == "google":
            pics, (i, o) = await _gemini(c, prompt, refs, count, ratio)
            m.unit, m.tokens_in, m.tokens_out, m.units = "token", i, o, i + o
        elif c.provider == "fal":
            pics = await _fal(c, prompt, refs, count, size, ratio)
            m.units, m.unpriced = float(len(pics)), True  # fal 按模型按张、按百万像素计价，价目表里没有：null
        elif c.provider == "comfyui":
            if refs:
                raise media.MediaError("bad", L("ComfyUI 这里不收参考图（要的话在工作流里接）", "ComfyUI takes no reference images here (wire them into the workflow)"))
            pics = await _comfy(c, prompt, count)
            m.units, m.cost = float(len(pics)), 0.0
        else:
            raise media.MediaError("config", L(f"{c.provider} 不会生图（能写的：openai、google、fal、comfyui）",
                                               f"{c.provider} can't make images (use openai, google, fal or comfyui)"))
        if c.provider not in ("openai", "google"):
            m.units = float(len(pics))
    if not pics:
        raise media.MediaError("shape", f"{c.provider}: no image came back")
    return pics


def _b64(s: str) -> bytes:
    try:
        return base64.b64decode(s or "", validate=True)
    except ValueError:
        return b""


async def _openai(c: media.Choice, prompt: str, refs: list[media.Source], count: int, size: str | None, quality: str | None,
                  background: str | None, fmt: str | None) -> tuple[list[Picture], dict]:
    base, key, key_env = media.endpoint(c)
    if key_env:
        media.need_key(c, key, key_env)
    extra = {k: v for k, v in (("size", size), ("quality", quality), ("background", background), ("output_format", fmt)) if v}
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    t = float(c.opts.get("timeout") or TIMEOUT)
    if refs:
        field = "image[]" if len(refs) > 1 else "image"
        files = [(field, (r.name or f"ref{i}.png", r.data, r.mime or images.sniff(r.data) or "image/png")) for i, r in enumerate(refs)]
        out, _ = await media.call(c, "POST", f"{base}/images/edits", key=key, headers=headers, timeout=t, files=files,
                                  data={"model": c.model, "prompt": prompt, "n": str(count), **extra})
    else:
        out, _ = await media.call(c, "POST", f"{base}/images/generations", key=key, headers=headers, timeout=t,
                                  json_body={"model": c.model, "prompt": prompt, "n": count, **extra})
    rows = out.get("data") if isinstance(out, dict) else None
    mime = FORMATS.get(fmt or "png", "image/png")
    pics = [Picture(d, images.sniff(d) or mime) for r in rows or [] if isinstance(r, dict) and (d := _b64(str(r.get("b64_json") or "")))]
    u = out.get("usage") if isinstance(out, dict) and isinstance(out.get("usage"), dict) else {}
    det = u.get("input_tokens_details") if isinstance(u.get("input_tokens_details"), dict) else {}
    return pics, {"in": int(u.get("input_tokens") or 0), "out": int(u.get("output_tokens") or 0), "image_in": int(det.get("image_tokens") or 0)}


async def _gemini(c: media.Choice, prompt: str, refs: list[media.Source], count: int, ratio: str | None) -> tuple[list[Picture], tuple[int, int]]:
    parts: list[dict] = [{"text": prompt}] + [media.inline(r.mime or images.sniff(r.data) or "image/png", r.data) for r in refs]
    config: dict = {"responseModalities": ["TEXT", "IMAGE"], **({"imageConfig": {"aspectRatio": ratio}} if ratio else {})}
    pics: list[Picture] = []
    tin = tout = 0
    for _ in range(count):
        got = await media.gemini(c, parts, config=config, timeout=float(c.opts.get("timeout") or TIMEOUT))
        i, o = media.gemini_usage(got)
        tin, tout = tin + i, tout + o
        for p in media.gemini_parts(got):
            blob = p.get("inlineData") or p.get("inline_data")
            if isinstance(blob, dict) and str(blob.get("mimeType") or blob.get("mime_type") or "").startswith("image/") and (d := _b64(str(blob.get("data") or ""))):
                pics.append(Picture(d, images.sniff(d) or str(blob.get("mimeType") or blob.get("mime_type"))))
    return pics, (tin, tout)


def _data_url(r: media.Source) -> str:
    return f"data:{r.mime or images.sniff(r.data) or 'image/png'};base64,{base64.b64encode(r.data).decode()}"


async def _fal(c: media.Choice, prompt: str, refs: list[media.Source], count: int, size: str | None, ratio: str | None) -> list[Picture]:
    base, key, key_env = media.endpoint(c)
    media.need_key(c, key, key_env)
    body: dict = {"prompt": prompt, "num_images": count, "sync_mode": True}
    if size and size != "auto":
        w, h = (int(x) for x in size.split("x"))
        body["image_size"] = {"width": w, "height": h}
    elif ratio:
        body["aspect_ratio"] = ratio
    if len(refs) == 1:
        body["image_url"] = _data_url(refs[0])
    elif refs:
        body["image_urls"] = [_data_url(r) for r in refs]
    out, _ = await media.call(c, "POST", f"{base}/{c.model.strip('/')}", key=key, headers={"Authorization": f"Key {key}"}, json_body=body,
                              timeout=float(c.opts.get("timeout") or TIMEOUT))
    pics: list[Picture] = []
    for im in (out.get("images") if isinstance(out, dict) else None) or []:
        url = str((im or {}).get("url") or "") if isinstance(im, dict) else ""
        if url.startswith("data:"):
            m = re.match(r"data:([^;,]+)?(;base64)?,(.*)$", url, re.S)
            d = _b64(m.group(3)) if m and m.group(2) else b""
        elif url.startswith("https://"):
            d = await _download(url)
        else:
            d = b""
        if d:
            pics.append(Picture(d, images.sniff(d) or "image/png"))
    return pics


async def _download(url: str) -> bytes:
    """服务商回的图片网址：照 web_fetch 只去公网、钉住地址，不带密钥。"""
    import webfetch

    from . import web
    try:
        data, ctype, _ = await web.fetch(url, max_bytes=images.READ_MAX)
    except webfetch.FetchError as e:
        raise media.MediaError("net", f"couldn't download the image: {e}") from None
    return data if images.sniff(data) or ctype.startswith("image/") else b""


async def _comfy(c: media.Choice, prompt: str, count: int) -> list[Picture]:
    base, _, _ = media.endpoint(c)
    wf_path = Path(str(c.opts.get("workflow") or "")).expanduser()
    node, field = str(c.opts.get("prompt_node") or ""), str(c.opts.get("prompt_input") or "text")
    if not c.opts.get("workflow") or not wf_path.is_file() or not node:
        raise media.MediaError("config", L("ComfyUI 要写 workflow（导出的 API 格式工作流）和 prompt_node（提示词写进哪个节点）",
                                           "ComfyUI needs workflow (an exported API-format workflow) and prompt_node (the node that takes the prompt)"))
    try:
        wf = json.loads(wf_path.read_text(encoding="utf-8"))
        wf[node]["inputs"][field] = prompt
    except (OSError, ValueError, KeyError, TypeError):
        raise media.MediaError("config", L(f"工作流里没有节点 {node} 的 {field}", f"The workflow has no input {field} on node {node}")) from None
    t = float(c.opts.get("timeout") or TIMEOUT)
    pics: list[Picture] = []
    for k in range(count):
        for n in wf.values():  # 每张换一个种子（不然几张一模一样）
            if isinstance(n, dict) and isinstance(n.get("inputs"), dict) and isinstance(n["inputs"].get("seed"), int):
                n["inputs"]["seed"] = (n["inputs"]["seed"] + 7919 * (k + 1)) % (2 ** 31) if k else n["inputs"]["seed"]
        out, _ = await media.call(c, "POST", f"{base}/prompt", json_body={"prompt": wf, "client_id": uuid.uuid4().hex}, timeout=60)
        pid = str((out or {}).get("prompt_id") or "") if isinstance(out, dict) else ""
        if not pid:
            raise media.MediaError("shape", "comfyui: no prompt_id came back")
        t0 = time.monotonic()
        while True:
            hist, _ = await media.call(c, "GET", f"{base}/history/{pid}", timeout=30)
            entry = hist.get(pid) if isinstance(hist, dict) else None
            if isinstance(entry, dict) and entry.get("outputs"):
                break
            if time.monotonic() - t0 > t:
                raise media.MediaError("net", f"comfyui: no image within {t:g}s")
            await asyncio.sleep(COMFY_POLL)
        for o in entry["outputs"].values():
            for im in (o or {}).get("images") or []:
                if isinstance(im, dict) and im.get("filename"):
                    d, _ = await media.call(c, "GET", f"{base}/view", params={"filename": im["filename"], "subfolder": im.get("subfolder") or "",
                                                                             "type": im.get("type") or "output"}, want="bytes", timeout=60)
                    if images.sniff(d):
                        pics.append(Picture(d, images.sniff(d) or "image/png"))
    return pics


# —— image_generate ———————————————————————————————————————————————————————————————————

def tool(agent: str) -> Tool | None:
    if not on():
        return None
    from .handkit import levelled
    c0 = media.choice("image")

    async def go(args: dict, ctx: ToolCtx):
        c = media.choice("image")
        if c is None:
            raise ToolFailure(L("生图没配（engine.use.image）", "Image generation isn't set up (engine.use.image)"))
        prompt = str(args.get("prompt") or "").strip()
        if not prompt:
            raise ToolFailure(L("要写 prompt（画什么）", "Give a prompt (what to draw)"))
        if len(prompt) > 8000:
            raise ToolFailure(L("prompt 太长了（最多 8000 字）", "The prompt is too long (8,000 characters at most)"))
        from . import spend
        spend.before_tool(ctx, c.provider)  # 第 4 批：今天的花费到了上限就不生（本机的不拦）
        count = max(1, min(COUNT_MAX, int(args.get("count") or 1)))
        size = _opt(c, args, "size", SIZE)
        ratio = _opt(c, args, "aspect_ratio", RATIO)
        quality = _opt(c, args, "quality", ("low", "medium", "high", "auto"))
        background = _opt(c, args, "background", ("transparent", "opaque", "auto"))
        fmt = _opt(c, args, "format", ("png", "jpeg", "webp"))
        wanted = [x for x in ([args.get("image")] if args.get("image") else []) + list(args.get("images") or []) if str(x or "").strip()]
        if len(wanted) > REF_MAX:
            raise ToolFailure(L(f"参考图最多 {REF_MAX} 张", f"At most {REF_MAX} reference images"))
        refs = []
        for w in wanted:
            src = await media.load({"url": w} if str(w).startswith(("http://", "https://")) else {"attachment": w} if re.fullmatch(r"[0-9a-f]{12}", str(w))
                                   else {"path": w}, ctx, max_bytes=REF_BYTES, what=("参考图", "reference image"))
            if images.sniff(src.data) is None:
                raise ToolFailure(L(f"{src.label} 不是图（PNG、JPEG、GIF、WebP）", f"{src.label} isn't an image (PNG, JPEG, GIF or WebP)"))
            refs.append(src)
        try:
            pics = await generate(c, prompt, refs, count=count, size=size, ratio=ratio, quality=quality, background=background, fmt=fmt,
                                  conversation=ctx.conversation, agent=ctx.agent)
        except media.MediaError as e:
            raise ToolFailure(L(f"没生成出来（{c.ref}）：{e}", f"Image generation failed ({c.ref}): {e}")) from None
        import files
        name = re.sub(r"[^\w-]+", "-", str(args.get("name") or "image").strip())[:40].strip("-") or "image"
        blocks, saved, notes = [], [], []
        for i, p in enumerate(pics, 1):
            ext = images.OK.get(p.mime, "png")
            a = await asyncio.to_thread(files.save_generated, ctx.conversation, p.data, f"{name}{f'-{i}' if len(pics) > 1 else ''}.{ext}", p.mime,
                                        kind="image", text=prompt, note=L("生成的图", "Generated image"))
            saved.append(a)
            block, said = await asyncio.to_thread(images.make, p.data, label=f"generated image {a['name']}", mime=p.mime)
            if block is not None:
                blocks.append(block)
            elif said:
                notes.append(said)
        what = ", ".join(f"{a['name']} (id {a['id']})" for a in saved)
        text = (f"Generated {len(saved)} image{'s' if len(saved) != 1 else ''} with {c.ref}" + (f" from {len(refs)} reference image(s)" if refs else "")
                + f": {what}. They're attachments of this conversation and show under your reply; the image{'s are' if len(saved) != 1 else ' is'} "
                "attached here so you can check it. Don't describe it at length unless asked.")
        return ToolOutput(text + ("\n" + "\n".join(notes) if notes else ""), blocks)

    return Tool(
        name=NAME, kinds=("owner",), run=go, timeout=TIMEOUT + 60, max_output=4000,
        title=lambda a: " ".join(str(a.get("prompt") or "").split())[:60],
        classify=lambda a: levelled(NAME, (UNDO, L("只在这个对话里多几张图（附件），不往外发、不改外面的东西；按张花钱，记在用量里",
                                                   "Only adds images (attachments) to this conversation, sends nothing out and changes nothing "
                                                   "elsewhere; it costs per image and is logged"))),
        description=L(
            f"生成图片（{c0.ref if c0 else ''}）：写清楚画什么（主体、风格、构图、文字）。可以给参考图改（image / images：工作区里的路径、附件 id、"
            f"公网网址，最多 {REF_MAX} 张）。count 几张（1–{COUNT_MAX}，每张都花钱），size（1024x1024 这种）或 aspect_ratio（16:9 这种）、quality、"
            "background（transparent = 透明背景）、format（png / jpeg / webp）看服务商认不认。生成的图存成这个对话的附件，回复下面看得到。",
            f"Generate images ({c0.ref if c0 else ''}): say what to draw (subject, style, composition, any text). Give reference images to edit "
            f"(image / images: workspace paths, attachment ids or public URLs, up to {REF_MAX}). count = how many (1–{COUNT_MAX}; each one costs), "
            "size (like 1024x1024) or aspect_ratio (like 16:9), quality, background (transparent for a see-through background) and format "
            "(png / jpeg / webp) where the provider supports them. The images are saved as attachments of this conversation and show under your reply."),
        schema={"type": "object", "required": ["prompt"], "properties": {
            "prompt": {"type": "string", "description": L("画什么", "What to draw")},
            "image": {"type": "string", "description": L("一张参考图（路径、附件 id、网址）", "One reference image (path, attachment id or URL)")},
            "images": {"type": "array", "items": {"type": "string"}, "description": L("几张参考图", "Several reference images")},
            "count": {"type": "integer", "description": L(f"几张（1–{COUNT_MAX}，默认 1）", f"How many (1–{COUNT_MAX}, default 1)")},
            "size": {"type": "string", "description": L("比如 1024x1024、1536x1024", "Like 1024x1024 or 1536x1024")},
            "aspect_ratio": {"type": "string", "description": L("比如 1:1、16:9", "Like 1:1 or 16:9")},
            "quality": {"type": "string", "enum": ["low", "medium", "high", "auto"]},
            "background": {"type": "string", "enum": ["transparent", "opaque", "auto"]},
            "format": {"type": "string", "enum": ["png", "jpeg", "webp"]},
            "name": {"type": "string", "description": L("文件名（不带扩展名）", "File name (without extension)")}}})
