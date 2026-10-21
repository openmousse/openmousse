"""Grok 订阅生图、生视频、朗读（10/20）：第 3 批的媒体那几样（engine/imagegen.py、mediagen.py、tts.py）各多一个服务商「Grok 订阅」：
用户自己的 SuperGrok / X Premium+ 登录（engine/grok_plan.py）调 api.x.ai，算订阅的额度、不另付钱（每次照旧记一行用量，钱记 0）。

默认都关：engine.use.image / video_gen / tts 写成 grok/<型号> 才用（登录了设置里才列出来能选；老实例一个字不变，上线时问用户开不开）：
  image      grok/grok-imagine-image（还有 grok-imagine-image-quality……看账号）
             POST /v1/images/generations {model, prompt, n, response_format: b64_json, aspect_ratio?, resolution?（1k / 2k）}；
             有参考图走 POST /v1/images/edits（JSON，不是 multipart：image / images 是 {url: data:…, type: image_url}，最多 REF_MAX 张）。
             grok-imagine-image 不收图（Hermes：xAI 文档里改图的基准是 -quality），改图就换 grok-imagine-image-quality；写了 edit_model 用它
  video_gen  grok/grok-imagine-video（给了起始图换 grok-imagine-video-1.5：图生视频用它，它不收只有字的；写了 image_model 用它）
             POST /v1/videos/generations {model, prompt, duration（1–15 秒，默认 8）, aspect_ratio?, resolution?（480p / 720p）, image?}
             （带 x-idempotency-key：重发不重复扣）→ request_id；每 POLL 秒 GET /v1/videos/<id> 到 done → video.url（xAI 给的临时地址），
             照 web_fetch 只去公网地下载，不带令牌（OpenClaw 也是空手 GET）。没有声音
  tts        grok/<随便一个名字>（xAI 的朗读只有一个接口；型号记在用量里）POST /v1/tts {text, voice_id（eve / ara / leo / rex / sal，
             默认 eve）, language（auto，或者 en、zh-cn 这种）, output_format: {codec: mp3 | wav}, speed?（0.7–1.5）} → 音频的字节
  对象里还能写 aspect_ratio、resolution、edit_model、image_model、voice、language、speed、format 的默认值。
谁能花（照 models.may_spend，grok_plan.may_use）：主人自己引出来的能；后台的要在设置里同意过（python3 setup_ctl.py grok background on）；
  经 /mcp、/v1 来的不行。不能花就照实说，不换别家（用户选的就是订阅）。这几样工具本来就只给主人回合（没人在看的那几轮、后台任务的子 Agent
  也不给）；「用语音回」（tts.after_reply，不知道那一轮的来路）算后台：同意过后台才念。
出错：401 刷新一次再试，还是不行标要重登；403 = 订阅里可能没有这一样（Hermes 遇到过订阅的令牌调朗读回 403）：说清楚、提示在设置里给这一样换
  一家；429 = 额度可能用完了。都换成 media.MediaError（报错里不带令牌）。
参考（10/20 现拉的源码，只读）：Hermes Agent（NousResearch/hermes-agent，MIT）49268ca6 的 plugins/image_gen/xai、plugins/video_gen/xai、
  tools/tts_tool_providers.py；OpenClaw bd2dbfde 的 extensions/xai/video-generation-provider.ts、tts.ts、speech-provider-metadata-factory.ts。
  只借做法。
"""
from __future__ import annotations

import asyncio
import base64
import re
import uuid
from typing import Any

from i18n import L

from . import grok_plan, media

PROVIDER = "grok"
PURPOSES = ("image", "video_gen", "tts")
IMAGE_MODEL, EDIT_MODEL = "grok-imagine-image", "grok-imagine-image-quality"
VIDEO_MODEL, IMAGE_VIDEO_MODEL = "grok-imagine-video", "grok-imagine-video-1.5"
REF_MAX = 3
RATIOS = ("1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3")
IMAGE_RESOLUTIONS = ("1k", "2k")
VIDEO_RESOLUTIONS = ("480p", "720p")
SECONDS, DEFAULT_SECONDS = (1, 15), 8
VOICES = ("eve", "ara", "leo", "rex", "sal")
SPEED = (0.7, 1.5)
FORMATS = ("mp3", "wav")
POLL = 5.0                                  # 视频多久问一次（OpenClaw 也是 5 秒）
TIMEOUT = 180.0
TERMINAL = ("done", "failed", "error", "expired", "cancelled")
REQUEST_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


def hint() -> str:
    return L("在设置的「用途」里给这一样换一家", "pick another provider for it under Settings → Purposes")


def check(kind: str | None, origin: str | None) -> None:
    """这一次能不能花订阅：登录着、这一轮能花（grok_plan.may_use）。不行抛 MediaError，一句为什么。"""
    if (why := grok_plan.why_not()) is not None:
        raise media.MediaError("auth", why)
    if not grok_plan.may_use(kind, origin):
        raise media.MediaError("config", grok_plan.refusal(kind, origin))


def _kind(status: int | None) -> str:
    if status is None:
        return "net"
    if status in (401, 403):
        return "auth"
    if status == 429:
        return "rate"
    return "server" if status >= 500 else "bad"


async def _call(method: str, path: str, *, what: str, json_body: Any = None, want: str = "json", timeout: float = TIMEOUT,
                headers: dict | None = None) -> tuple[Any, Any]:
    try:
        return await grok_plan.api(method, path, json_body=json_body, want=want, timeout=timeout, what=what, hint=hint(), headers=headers)
    except grok_plan.ApiError as e:
        raise media.MediaError(_kind(e.status), str(e)) from None


def _b64(s: object) -> bytes:
    try:
        return base64.b64decode(str(s or ""), validate=True)
    except ValueError:
        return b""


def _data_url(src: media.Source) -> str:
    from . import images
    return f"data:{images.sniff(src.data) or src.mime or 'image/png'};base64,{base64.b64encode(src.data).decode()}"


def _pick(v: object, ok: tuple[str, ...], field: str) -> str | None:
    s = str(v or "").strip().lower()
    if not s:
        return None
    if s not in ok:
        raise media.MediaError("bad", L(f"Grok 的 {field} 只能是 {' / '.join(ok)}（写的是 {s}）", f"Grok's {field} must be one of {', '.join(ok)} (got {s})"))
    return s


# —— 生图 ————————————————————————————————————————————————————————————————————————

async def image(c: media.Choice, prompt: str, refs: list[media.Source], count: int, ratio: str | None) -> list[tuple[bytes, str]]:
    """生 count 张（refs = 参考图）→ [(字节, 类型)]。"""
    from . import images
    if len(refs) > REF_MAX:
        raise media.MediaError("bad", L(f"Grok 改图最多收 {REF_MAX} 张参考图", f"Grok takes at most {REF_MAX} reference images"))
    base = {"prompt": prompt, "n": count, "response_format": "b64_json"}
    if refs:
        pics = [{"url": _data_url(r), "type": "image_url"} for r in refs]
        model = str(c.opts.get("edit_model") or (EDIT_MODEL if c.model == IMAGE_MODEL else c.model))
        body, path = {"model": model, **base, **({"image": pics[0]} if len(pics) == 1 else {"images": pics})}, "/images/edits"
    else:
        r = _pick(ratio or c.opts.get("aspect_ratio"), RATIOS, "aspect_ratio")
        res = _pick(c.opts.get("resolution"), IMAGE_RESOLUTIONS, "resolution")
        body, path = {"model": c.model, **base, **({"aspect_ratio": r} if r else {}), **({"resolution": res} if res else {})}, "/images/generations"
    data, _ = await _call("POST", path, json_body=body, what="image_generate", timeout=float(c.opts.get("timeout") or TIMEOUT))
    out: list[tuple[bytes, str]] = []
    for row in (data.get("data") if isinstance(data, dict) else None) or []:
        if not isinstance(row, dict):
            continue
        raw = _b64(row.get("b64_json"))
        if not raw and str(row.get("url") or "").startswith("https://"):  # 要的是 b64_json；万一回的是临时网址（几分钟就失效）：照公网的规矩下载
            from . import imagegen
            raw = await imagegen._download(str(row["url"]))  # noqa: SLF001
        if raw and images.sniff(raw):
            out.append((raw, images.sniff(raw) or "image/png"))
    return out


# —— 生视频 ——————————————————————————————————————————————————————————————————————

def video_opts(ratio: str | None, resolution: str | None, seconds: float | None) -> None:
    """video_generate 的参数先在这边查（起活之前，不白交一次）。不对抛 ToolFailure。"""
    from .loop import ToolFailure
    try:
        _pick(ratio, RATIOS, "aspect_ratio")
        _pick(resolution, VIDEO_RESOLUTIONS, "resolution")
    except media.MediaError as e:
        raise ToolFailure(str(e)) from None
    if seconds is not None and not SECONDS[0] <= seconds <= SECONDS[1]:
        raise ToolFailure(L(f"Grok 的视频一段 {SECONDS[0]}–{SECONDS[1]} 秒", f"Grok makes {SECONDS[0]}–{SECONDS[1]} s clips"))


async def video(c: media.Choice, prompt: str, o: dict, ref: media.Source | None) -> tuple[bytes, float | None]:
    """交一段视频、等它做好、下载 → (MP4 的字节, 多少秒)。等多久由外面定（mediagen 的 TIMEOUT）。"""
    import webfetch

    from . import mediagen, web
    model = str(c.opts.get("image_model") or (IMAGE_VIDEO_MODEL if c.model == VIDEO_MODEL else c.model)) if ref is not None else c.model
    secs = o.get("seconds") or c.opts.get("seconds") or DEFAULT_SECONDS
    body: dict = {"model": model, "prompt": prompt, "duration": int(min(SECONDS[1], max(SECONDS[0], round(float(secs)))))}
    for k, ok in (("aspect_ratio", RATIOS), ("resolution", VIDEO_RESOLUTIONS)):
        if v := _pick(o.get(k) or c.opts.get(k), ok, k):
            body[k] = v
    if ref is not None:
        body["image"] = {"url": _data_url(ref)}
    sub, _ = await _call("POST", "/videos/generations", json_body=body, what="video_generate", timeout=60,
                         headers={"x-idempotency-key": uuid.uuid4().hex})
    rid = str(sub.get("request_id") or "") if isinstance(sub, dict) else ""
    if not REQUEST_ID.match(rid):
        raise media.MediaError("shape", "grok: no request id came back")
    while True:
        st, _ = await _call("GET", f"/videos/{rid}", what="video_generate", timeout=60)
        status = str(st.get("status") or "").lower() if isinstance(st, dict) else ""
        if status in TERMINAL:
            break
        await asyncio.sleep(POLL)
    if status != "done":
        err = st.get("error") if isinstance(st.get("error"), dict) else {}
        why = str(err.get("message") or st.get("message") or "")[:200]
        raise media.MediaError("bad" if status == "failed" else "server", f"grok: the video request {status}" + (f": {why}" if why else ""))
    v = st.get("video") if isinstance(st.get("video"), dict) else {}
    url = str(v.get("url") or "")
    if not url.startswith(("https://", "http://")):
        raise media.MediaError("shape", "grok: the finished video has no address")
    try:
        raw, _ctype, _ = await web.fetch(url, max_bytes=mediagen.MAX_BYTES[mediagen.VIDEO])
    except webfetch.FetchError as e:
        raise media.MediaError("net", f"couldn't download the video: {e}") from None
    if not raw:
        raise media.MediaError("shape", "grok: the video was empty")
    d = v.get("duration")
    return raw, float(d) if isinstance(d, (int, float)) and not isinstance(d, bool) and d > 0 else await media.duration(raw, "video.mp4")


# —— 朗读 ————————————————————————————————————————————————————————————————————————

async def speech(c: media.Choice, text: str, voice: str | None, want: str | None) -> tuple[bytes, str]:
    """一段字 → (音频的字节, 扩展名)。"""
    f = str(want or c.opts.get("format") or "mp3").lower().strip(".")
    ext = f if f in FORMATS else "mp3"
    body: dict = {"text": text, "voice_id": str(voice or c.opts.get("voice") or VOICES[0]).strip().lower(),
                  "language": str(c.opts.get("language") or "auto").strip().lower(), "output_format": {"codec": ext}}
    if c.opts.get("speed"):
        try:
            body["speed"] = max(SPEED[0], min(SPEED[1], float(c.opts["speed"])))
        except (TypeError, ValueError):
            raise media.MediaError("config", L("Grok 朗读的 speed 要是 0.7–1.5 的数", "Grok speech speed must be a number from 0.7 to 1.5")) from None
    data, r = await _call("POST", "/tts", json_body=body, want="bytes", what="speak", timeout=float(c.opts.get("timeout") or TIMEOUT))
    ctype = (r.headers.get("content-type") or "").lower()
    if not data or "json" in ctype or ctype.startswith("text/"):
        raise media.MediaError("shape", f"grok: the answer isn't audio ({ctype or 'empty'})")
    return data, ext
