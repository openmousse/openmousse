"""生成视频、音乐（第 3 批第 14 项，2026-10-20）：video_generate、music_generate 两个工具，「生成视频」「生成音乐」两个用途
（engine.use.video_gen、engine.use.music；不写 = 没有这个工具）。照第 6 项（engine/imagegen.py）的写法：按用途选服务商、记花费、存成
对话附件；照 OpenClaw docs/tools/video-generation.md、music-generation.md：交出去以后做好了叫醒这个对话，同一个对话里同一件还在做时
重复调用回它的状态，action status 看进度。只借设计。

用途（2026-10-20 核对各家官方文档）：
  engine.use.video_gen
    google/gemini-omni-1.1-flash   Gemini 的 Interactions API：POST <根>/interactions {model, input（字，或者 [text, image]）, response_format:
                                   {type: video, aspect_ratio?, resolution?, delivery?}, store: false, background: false, stream: false}（同步，
                                   照官方文档对一次性生成的建议）→ steps 里 model_output 的 content：{type: video, data（base64）| uri（Files API，
                                   等它 ACTIVE 再 :download）}；一段 3–10 秒（长短它自己定，没有参数）、带声音。
                                   OpenAI 的 Sora 2 接口 2026-09-24 关了，Veo 3.1 预览版 2026-10-22 关：都不接
    fal/<模型路径>                 fal 的队列：POST https://queue.fal.run/<模型路径>（Authorization: Key <FAL_KEY>）→ request_id、status_url、
                                   response_url；问 status 到 COMPLETED，再取结果里的 video.url 下载（data: 地址，或者照 web_fetch 只去公网）。
                                   status / 结果的地址不在队列那台主机上就不用它给的（密钥不发给别的主机），照 <owner>/<app>/requests/<id> 自己拼
    grok/grok-imagine-video        10/20：用户自己的 Grok 订阅（engine/grok_media.py：一段 1–15 秒、没有声音；算订阅的额度、不另付钱；
                                   登录了才能选）。这一轮能不能花照 models.may_spend（点了卡同意的照交卡那一轮的来路算），起活之前就查
  engine.use.music
    google/lyria-3-clip-preview（30 秒）、google/lyria-3.5（整首）  Interactions API，response_format {type: audio}：content 里的 audio（MP3）
                                   和歌词（text）；没有「纯音乐」的开关：写进提示词
    elevenlabs/music_v1 / music_v2 / music_v2_5   POST <根>/music?output_format=mp3_44100_128 {prompt, model_id, music_length_ms?,
                                   force_instrumental?}（xi-api-key）→ 音频的字节
    fal/<模型路径>                 同上（结果里的 audio.url / audio_file.url……）
  对象里还能写 url、timeout；Google 的 delivery（"uri" = 大文件经 Files API）、api_revision（Api-Revision 请求头）；fal 的 queue_url；
  ElevenLabs 的 output_format。
怎么跑：工具起一件活（Job），在这一轮里最多等 WAIT 秒：等到了就当场交（附件挂在这次回复下面）；没等到就转后台，做好了往这个对话补一句、
  开一轮（host_process.deliver，同后台命令），附件挂在那一轮的回复下面（先交那一句、再存附件：一轮回完挂的是这一轮开始以后存的）。
  点了卡同意以后跑的（handkit.approved_run）不等，直接后台；不是 app 的对话（交不回去）一直等到做完。同一个对话里同一件（同一个提示词）
  还在做：回它的状态，不再起一件。一个 Agent 同时最多 MAX_RUNNING 件。用户在等的这一轮里点了停：这件活跟着停。服务重启：正在做的收掉，
  往那个对话说一声（钱可能已经花了）。
分类（自己定的，理由）：
  video_generate  ask：一段几毛到一两美元（Omni Flash 720p 约 $0.10 一秒、一段 3–10 秒；fal 看模型），卡上写清模型、估计多少钱、提示词；
                  engine.tools.levels 写 {"video_generate": "auto"} 就不问直接做。action status = read
  music_generate  undo：同生图，一首几美分到几毛（Lyria $0.04–0.08 一首，ElevenLabs $0.15 一分钟），只在这台服务器上多一个附件；
                  要每次都问写 {"music_generate": "ask"}
花费：每次记一行 engine_media_usage（purpose video_gen / music；Omni 照接口回的 token 数，Lyria 按首，ElevenLabs 按秒，fal 价钱没核对记 null）。
没人在看的那几轮、后台任务的子 Agent：不给（unattended.NO_TOOLS、tasks.CHILD_NEVER）。
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from i18n import L

from . import images, media
from .loop import Tool, ToolCtx, ToolFailure

VIDEO, MUSIC = "video_generate", "music_generate"
NAMES = (VIDEO, MUSIC)
PURPOSE = {VIDEO: "video_gen", MUSIC: "music"}
READ, UNDO, ASK = "read", "undo", "ask"
WAIT = {VIDEO: 25.0, MUSIC: 60.0}         # 这一轮里最多等几秒，没好就转后台
TIMEOUT = {VIDEO: 1200.0, MUSIC: 600.0}   # 一件活最多做多久（交出去、等、下载）
POLL = 5.0                                # fal 的队列多久问一次
FILE_POLL, FILE_WAIT = 2.0, 120.0         # Files API 的结果多久问一次、最多等多久
MAX_RUNNING = 2                           # 一个 Agent 同时最多几件
KEEP = 20                                 # 做完的留几件给 status 看
PROMPT_MAX, LYRICS_MAX = 4000, 3000
REF_BYTES = 20 * 1024 * 1024
MAX_BYTES = {VIDEO: 200 * 1024 * 1024, MUSIC: 50 * 1024 * 1024}
FAL_QUEUE = "https://queue.fal.run"
OMNI_PER_SECOND = 5792 * 17.50 / 1e6      # Omni Flash：720p 每秒 5792 个输出 token × 每百万 $17.50（ai.google.dev/gemini-api/docs/pricing，2026-10-19）
OMNI_SECONDS = (3, 10)                    # 一段多长由它定
OMNI_RATIOS = ("16:9", "9:16")
OMNI_RESOLUTIONS = ("360p", "720p", "1080p", "4k")
RATIO = re.compile(r"^\d{1,2}:\d{1,2}$")
RESOLUTION = re.compile(r"^(?:\d{3,4}p|[248]k)$")


def on(tool: str) -> bool:
    return media.choice(PURPOSE[tool]) is not None


@dataclass
class Made:
    data: bytes
    mime: str
    ext: str
    seconds: float | None = None
    text: str = ""                 # 歌词、服务商附带的话


@dataclass
class Job:
    id: str
    tool: str
    agent: str
    conversation: str
    ref: str
    prompt: str
    started: float = field(default_factory=time.time)
    status: str = "running"        # running / done / failed / stopped
    background: bool = False       # 转后台了：做好了往对话里补一句
    inline: bool = False           # 结果当场交（这一轮还在等）
    text: str = ""                 # 做好了 / 没做成的那一句
    task: asyncio.Task | None = None
    done: asyncio.Event = field(default_factory=asyncio.Event)
    kind: str | None = None        # 10/20：起这件活的那一轮是谁的（Grok 订阅照它看能不能花）
    origin: str | None = None


_jobs: dict[str, Job] = {}


# —— 各家 ————————————————————————————————————————————————————————————————————————

def _b64(s: str) -> bytes:
    try:
        return base64.b64decode(s or "", validate=True)
    except ValueError:
        return b""


async def _interact(c: media.Choice, body: dict, *, timeout: float) -> dict:
    """POST <根>/interactions → 回的 JSON（没做成的抛 MediaError，报错里不带密钥）。"""
    base, key, key_env = media.endpoint(c)
    media.need_key(c, key, key_env)
    headers = {"x-goog-api-key": key, "Content-Type": "application/json"}
    if c.opts.get("api_revision"):
        headers["Api-Revision"] = str(c.opts["api_revision"])
    data, _ = await media.call(c, "POST", f"{media.gemini_base(base)}/interactions", key=key, headers=headers, json_body=body, timeout=timeout)
    if not isinstance(data, dict):
        raise media.MediaError("shape", f"{c.provider}: the answer isn't a JSON object")
    errs = [e for e in data.get("errors") or [] if isinstance(e, dict)]
    if str(data.get("status") or "") in ("failed", "cancelled", "incomplete") or (errs and not _outputs(data)):
        msg = "; ".join(str(e.get("message") or e.get("code") or "") for e in errs)[:300] or f"status {data.get('status')}"
        raise media.MediaError("bad", media.scrub(f"{c.provider}: {msg}", key))
    return data


def _outputs(data: dict) -> list[dict]:
    """模型回的那几块（steps 里 model_output 的 content；早一点的版本放在 outputs 里）。"""
    out: list[dict] = []
    for st in data.get("steps") or []:
        if isinstance(st, dict) and st.get("type") == "model_output":
            out += [x for x in st.get("content") or [] if isinstance(x, dict)]
    out += [x for x in data.get("outputs") or [] if isinstance(x, dict)]
    return out


def _texts(data: dict) -> str:
    return "\n".join(str(x.get("text") or "").strip() for x in _outputs(data) if x.get("type") == "text" and str(x.get("text") or "").strip())


async def _content(c: media.Choice, item: dict, max_bytes: int) -> bytes:
    """一块结果的字节：data（base64）直接解；uri（Files API）等它 ACTIVE 再下载。"""
    if item.get("data"):
        raw = _b64(str(item["data"]))
    elif m := re.search(r"files/[A-Za-z0-9_-]+", str(item.get("uri") or "")):
        base, key, _ = media.endpoint(c)
        root, head = media.gemini_base(base), {"x-goog-api-key": key}
        deadline = time.monotonic() + FILE_WAIT
        while True:
            meta, _ = await media.call(c, "GET", f"{root}/{m.group(0)}", key=key, headers=head)
            state = str((meta or {}).get("state") or "ACTIVE") if isinstance(meta, dict) else "ACTIVE"
            if state == "ACTIVE":
                break
            if state == "FAILED" or time.monotonic() > deadline:
                raise media.MediaError("server", f"{c.provider}: the file with the result never became ready ({state})")
            await asyncio.sleep(FILE_POLL)
        raw, _ = await media.call(c, "GET", f"{root}/{m.group(0)}:download", key=key, headers=head, params={"alt": "media"},
                                  timeout=float(c.opts.get("timeout") or 300), want="bytes")
    else:
        raw = b""
    if not raw:
        raise media.MediaError("shape", f"{c.provider}: the result was empty")
    if len(raw) > max_bytes:
        raise media.MediaError("shape", f"{c.provider}: the result is over {media.human_bytes(max_bytes)}")
    return raw


async def _omni(c: media.Choice, prompt: str, o: dict, ref: media.Source | None) -> tuple[Made, tuple[int, int]]:
    fmt = {"type": "video", **{k: o[k] for k in ("aspect_ratio", "resolution") if o.get(k)}}
    if str(c.opts.get("delivery") or "") == "uri":
        fmt["delivery"] = "uri"
    inp: str | list = prompt if ref is None else [{"type": "text", "text": prompt},
                                                   {"type": "image", "data": base64.b64encode(ref.data).decode(), "mime_type": ref.mime}]
    data = await _interact(c, {"model": c.model, "input": inp, "response_format": fmt, "store": False, "background": False, "stream": False},
                           timeout=float(c.opts.get("timeout") or TIMEOUT[VIDEO]))
    item = next((x for x in _outputs(data) if x.get("type") == "video"), None)
    if item is None:
        said = _texts(data)
        raise media.MediaError("shape", f"{c.provider}: no video came back" + (f" ({said[:200]})" if said else ""))
    raw = await _content(c, item, MAX_BYTES[VIDEO])
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    made = Made(raw, str(item.get("mime_type") or "video/mp4"), "mp4", await media.duration(raw, "video.mp4"), _texts(data))
    return made, (int(usage.get("total_input_tokens") or 0), int(usage.get("total_output_tokens") or 0))


async def _lyria(c: media.Choice, prompt: str, o: dict) -> tuple[Made, tuple[int, int]]:
    data = await _interact(c, {"model": c.model, "input": _song_prompt(prompt, o), "response_format": {"type": "audio"}, "store": False,
                               "background": False, "stream": False}, timeout=float(c.opts.get("timeout") or TIMEOUT[MUSIC]))
    item = next((x for x in _outputs(data) if x.get("type") == "audio"), None)
    if item is None:
        raise media.MediaError("shape", f"{c.provider}: no audio came back")
    raw = await _content(c, item, MAX_BYTES[MUSIC])
    mime = str(item.get("mime_type") or "audio/mpeg")
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    made = Made(raw, mime, "wav" if "wav" in mime else "mp3", await media.duration(raw, "music.mp3"), _texts(data))
    return made, (int(usage.get("total_input_tokens") or 0), int(usage.get("total_output_tokens") or 0))


def _song_prompt(prompt: str, o: dict) -> str:
    """歌词、纯音乐写进提示词（Lyria、ElevenLabs 只给提示词时都这样）。"""
    return (prompt + (f"\n\nLyrics:\n{o['lyrics']}" if o.get("lyrics") else "")
            + ("\n\nInstrumental only, no vocals." if o.get("instrumental") else ""))


async def _eleven(c: media.Choice, prompt: str, o: dict) -> Made:
    base, key, key_env = media.endpoint(c)
    media.need_key(c, key, key_env)
    body: dict = {"prompt": prompt + (f"\n\nLyrics:\n{o['lyrics']}" if o.get("lyrics") else ""), "model_id": c.model}
    if o.get("seconds"):
        body["music_length_ms"] = max(3000, min(600_000, int(o["seconds"] * 1000)))
    if o.get("instrumental"):
        body["force_instrumental"] = True
    fmt = str(c.opts.get("output_format") or "mp3_44100_128")
    raw, r = await media.call(c, "POST", f"{base}/music", key=key, headers={"xi-api-key": key, "Content-Type": "application/json"}, json_body=body,
                              params={"output_format": fmt}, timeout=float(c.opts.get("timeout") or TIMEOUT[MUSIC]), want="bytes")
    if not raw:
        raise media.MediaError("shape", f"{c.provider}: no audio came back")
    ext = "wav" if fmt.startswith("pcm") else "opus" if fmt.startswith("opus") else "mp3"
    mime = {"wav": "audio/wav", "opus": "audio/ogg"}.get(ext, "audio/mpeg")
    return Made(raw, mime, ext, await media.duration(raw, f"music.{ext}") or (float(o["seconds"]) if o.get("seconds") else None))


def _same_host(url: object, base: str) -> str | None:
    u = str(url or "")
    return u if u.startswith(("https://", "http://")) and urlsplit(u).netloc == urlsplit(base).netloc else None


def _find_url(res: object, kind: str) -> str | None:
    """fal 的结果里那个文件的地址：先找 video / audio 那几个键，再找头一个 url。"""
    keys = ("video", "videos", "output", "result") if kind == "video" else ("audio", "audio_file", "audio_url", "music", "output", "result")

    def walk(v: object, depth: int) -> str | None:
        if depth > 5:
            return None
        if isinstance(v, str):
            return v if v.startswith(("https://", "data:")) else None
        if isinstance(v, dict):
            if isinstance(v.get("url"), str):
                return walk(v["url"], depth + 1)
            for k in [*keys, *v]:
                if k in v and (got := walk(v[k], depth + 1)):
                    return got
        if isinstance(v, list):
            for x in v:
                if got := walk(x, depth + 1):
                    return got
        return None
    return walk(res, 0)


async def _fetch(url: str, max_bytes: int) -> bytes:
    """服务商回的文件地址：data: 直接解；https 照 web_fetch 只去公网、钉住地址，不带密钥。"""
    if url.startswith("data:"):
        m = re.match(r"data:([^;,]+)?(;base64)?,(.*)$", url, re.S)
        return _b64(m.group(3)) if m and m.group(2) else b""
    import webfetch

    from . import web
    try:
        data, _ctype, _ = await web.fetch(url, max_bytes=max_bytes)
    except webfetch.FetchError as e:
        raise media.MediaError("net", f"couldn't download the result: {e}") from None
    return data


async def _fal(c: media.Choice, body: dict, kind: str) -> Made:
    base, key, key_env = media.endpoint(c)
    media.need_key(c, key, key_env)
    q = str(c.opts.get("queue_url") or (FAL_QUEUE if base.rstrip("/") in ("https://fal.run", "") else base)).rstrip("/")
    model, auth = c.model.strip("/"), {"Authorization": f"Key {key}"}
    sub, _ = await media.call(c, "POST", f"{q}/{model}", key=key, headers=auth, json_body=body, timeout=60)
    rid = str((sub or {}).get("request_id") or "") if isinstance(sub, dict) else ""
    if not rid:
        raise media.MediaError("shape", f"{c.provider}: the queue gave no request id")
    root = f"{q}/{'/'.join(model.split('/')[:2])}/requests/{rid}"
    status_url, result_url = _same_host(sub.get("status_url"), q) or root + "/status", _same_host(sub.get("response_url"), q) or root
    while True:
        st, _ = await media.call(c, "GET", status_url, key=key, headers=auth, timeout=60)
        s = str((st or {}).get("status") or "").upper() if isinstance(st, dict) else ""
        if s == "COMPLETED":
            break
        if s in ("FAILED", "ERROR", "CANCELLED", "CANCELED"):
            raise media.MediaError("server", media.scrub(f"{c.provider}: the request {s.lower()}: {str(st.get('error') or '')[:200]}", key))
        await asyncio.sleep(POLL)
    res, _ = await media.call(c, "GET", result_url, key=key, headers=auth, timeout=120)
    url = _find_url(res, kind)
    if not url:
        raise media.MediaError("shape", f"{c.provider}: no {kind} in the result")
    raw = await _fetch(url, MAX_BYTES[VIDEO if kind == "video" else MUSIC])
    if not raw:
        raise media.MediaError("shape", f"{c.provider}: the {kind} was empty")
    is_video = kind == "video"
    ext = "mp4" if is_video else ("wav" if raw[:4] == b"RIFF" else "mp3")
    mime = "video/mp4" if is_video else {"wav": "audio/wav"}.get(ext, "audio/mpeg")
    return Made(raw, mime, ext, await media.duration(raw, f"out.{ext}"))


async def generate(tool: str, c: media.Choice, prompt: str, o: dict, ref: media.Source | None, *, conversation: str | None,
                   agent: str | None, kind: str | None = None, origin: str | None = None) -> Made:
    """照 c 做一件（见文件开头）。出错抛 media.MediaError（报错里不带密钥）。记一行花费。
    kind / origin（10/20）：起这件活的那一轮是谁的：grok 照它看能不能花订阅（不给 = 不能）。"""
    if c.provider == media.GROK:  # 10/20：Grok 订阅：不能花就不做（不记花费）
        from . import grok_media
        grok_media.check(kind, origin)
    with media.Meter(PURPOSE[tool], c, conversation, agent) as m:
        if tool == VIDEO and c.provider == "google":
            made, (i, out) = await _omni(c, prompt, o, ref)
            m.unit, m.tokens_in, m.tokens_out, m.units, m.unpriced = "token", i, out, i + out, not (i or out)
        elif tool == MUSIC and c.provider == "google":
            made, (i, out) = await _lyria(c, prompt, o)
            m.unit, m.units, m.tokens_in, m.tokens_out = "song", 1.0, i, out
        elif tool == MUSIC and c.provider == "elevenlabs":
            made = await _eleven(c, prompt, o)
            m.unit, m.units, m.unpriced = "second", made.seconds or 0.0, not made.seconds
        elif c.provider == "fal":
            body: dict = {"prompt": prompt if tool == VIDEO else _song_prompt(prompt, {"instrumental": o.get("instrumental")})}
            if tool == VIDEO:
                body.update({k: o[k] for k in ("aspect_ratio", "resolution") if o.get(k)})
                if ref is not None:
                    body["image_url"] = f"data:{ref.mime};base64,{base64.b64encode(ref.data).decode()}"
            else:
                body.update({k: v for k, v in (("lyrics", o.get("lyrics")), ("instrumental", o.get("instrumental"))) if v})
            if o.get("seconds"):
                body["duration"] = str(int(o["seconds"])) if tool == VIDEO else int(o["seconds"])
            made = await _fal(c, body, "video" if tool == VIDEO else "audio")
            m.unit, m.units, m.unpriced = "video" if tool == VIDEO else "song", 1.0, True  # fal 按模型计价，价目表里没有：null
        elif tool == VIDEO and c.provider == media.GROK:  # 10/20：Grok 订阅（engine/grok_media.py）：算订阅的额度，钱记 0
            from . import grok_media
            raw, secs = await grok_media.video(c, prompt, o, ref)
            made = Made(raw, "video/mp4", "mp4", secs)
            m.unit, m.units, m.cost = "video", 1.0, 0.0
        else:
            can = "google, fal" if tool == VIDEO else "google, elevenlabs, fal"
            raise media.MediaError("config", L(f"{c.provider} 做不了这一样（能写的：{can}）", f"{c.provider} can't do this (use {can})"))
    return made


# —— 一件活：起、等、交 ——————————————————————————————————————————————————————————————

def _what(tool: str) -> tuple[str, str]:
    return ("视频", "video") if tool == VIDEO else ("音乐", "music")


async def _save(job: Job, made: Made, name: str) -> dict:
    import files
    zh, en = _what(job.tool)
    secs = f", {made.seconds:.0f} s" if made.seconds else ""
    return await asyncio.to_thread(files.save_generated, job.conversation, made.data, f"{name}.{made.ext}", made.mime,
                                   kind="video" if job.tool == VIDEO else "audio", text=job.prompt,
                                   note=L(f"生成的{zh}{secs.replace(', ', '，')}", f"Generated {en}{secs}"))


def _done_text(job: Job, made: Made, a: dict, *, later: bool) -> str:
    _zh, en = _what(job.tool)
    secs = f"{made.seconds:.0f} s, " if made.seconds else ""
    head = (f"[{en.capitalize()} ready] {job.id} · {job.ref}: saved as {a['name']} ({secs}{media.human_bytes(len(made.data))}), an attachment of "
            f"this chat (id {a['id']}); it shows under your reply, where the user can play it.")
    lyrics = f"\n{media.wrap(f'{job.tool} {job.ref}', made.text[:3000])}" if made.text else ""
    if later:
        return (head + lyrics + f"\nThis isn't from the user: it's the result of the {job.tool} you started earlier. Tell the user it's ready "
                "in a sentence; don't make it again.")
    return head + lyrics + " Don't describe it at length unless asked."


async def _work(job: Job, c: media.Choice, o: dict, ref: media.Source | None, name: str) -> None:
    zh, en = _what(job.tool)
    try:
        made = await asyncio.wait_for(generate(job.tool, c, job.prompt, o, ref, conversation=job.conversation, agent=job.agent,
                                               kind=job.kind, origin=job.origin), TIMEOUT[job.tool])
    except asyncio.CancelledError:
        job.status, job.text = "stopped", f"Stopped: {job.id} didn't finish."
        job.done.set()
        raise
    except Exception as e:  # noqa: BLE001 — MediaError、超时、别的：都交回一句
        why = "it didn't finish within the time limit" if isinstance(e, asyncio.TimeoutError) else \
            str(e) if isinstance(e, media.MediaError) else f"{type(e).__name__}: {str(e)[:200]}"
        job.status, job.text = "failed", f"[{en.capitalize()} generation failed] {job.id} · {job.ref}: {why}"
        if job.background:
            await _tell(job, L(f"{zh}没做成：{job.prompt[:40]}", f"{en.capitalize()} generation failed: {job.prompt[:40]}"),
                        job.text + "\nThis isn't from the user: it's the result of something you started earlier. Tell the user what went wrong; "
                        "don't retry unless they ask.")
        job.done.set()
        return
    if not job.background:  # 这一轮还在等：当场交，附件挂在这次回复下面
        job.inline = True
        try:
            a = await _save(job, made, name)
        except Exception as e:  # noqa: BLE001 — 存不下来
            job.status, job.text = "failed", f"[{en.capitalize()} generation failed] {job.id}: couldn't save it ({type(e).__name__})"
        else:
            job.status, job.text = "done", _done_text(job, made, a, later=False)
        job.done.set()
        return
    # 转后台了：先往对话补一句（开一轮），再存附件（一轮回完挂的是这一轮开始以后存的）
    saved = await _tell(job, L(f"{zh}做好了：{job.prompt[:40]}", f"{en.capitalize()} ready: {job.prompt[:40]}"), None, made=made, name=name)
    job.status = "done" if saved else "failed"
    job.done.set()


async def _tell(job: Job, line: str, text: str | None, *, made: Made | None = None, name: str = "") -> bool:
    """往这个对话补一句（host_process.deliver）；made 给了：交完那一句再存附件，正文里写上附件。交不出去只打一行日志。
    → 附件存下来了没有（没给 made = True）。"""
    from . import host_process
    # 附件的 id 要存了才知道：正文先写好名字，存完再补进 job.text（status 看得到）
    body = (text or "") if made is None else _done_text(job, made, {"name": f"{name}.{made.ext}", "id": "see the attachment under your reply"},
                                                        later=True)
    try:
        await host_process.deliver(job.conversation, line, body)
    except Exception as e:  # noqa: BLE001 — 交不出去：附件照样存（status 看得到）
        print(f"[engine] {job.tool} {job.id} 的结果没交到 {job.conversation}：{type(e).__name__}: {str(e)[:120]}")
    if made is None:
        return True
    try:
        a = await _save(job, made, name)
    except Exception as e:  # noqa: BLE001
        job.text = f"[{_what(job.tool)[1].capitalize()} generation failed] {job.id}: couldn't save it ({type(e).__name__})"
        print(f"[engine] {job.tool} {job.id} 存不下来：{type(e).__name__}: {str(e)[:120]}")
        return False
    job.text = _done_text(job, made, a, later=True)
    return True


def _status(conversation: str, tool: str) -> str:
    rows = [j for j in _jobs.values() if j.conversation == conversation and j.tool == tool]
    if not rows:
        return L("这个对话里没有在做的、做完的。", "Nothing started in this chat.")
    out = []
    for j in rows[-8:]:
        secs = int(time.time() - j.started)
        out.append(f"- {j.id} · {j.status} · {secs}s · {j.ref} · {j.prompt[:80]}" + (f"\n  {j.text[:300]}" if j.status != "running" and j.text else ""))
    return "\n".join(out)


def _prune() -> None:
    finished = [k for k, j in _jobs.items() if j.status != "running"]
    for k in finished[: max(0, len(finished) - KEEP)]:
        _jobs.pop(k, None)


async def shutdown() -> int:
    """关服务（main.py）：正在做的收掉，往那个对话说一声（钱可能已经花了）。→ 收掉了几件。"""
    return await stop_all(restart=True)


async def stop_all(*, restart: bool = False) -> int:
    """收掉正在做的，往那个对话说一声（钱可能已经花了）。→ 收掉了几件。restart：关服务，全部收掉；不是：急停的 --stop-running
    （第 4 批，engine/estop.py），只收转了后台的——还在一轮里等着的跟着那一轮走（自动的那几轮急停会停，你自己在 app 里的那一轮不停）。"""
    from . import host_process
    by_zh, by_en = ("服务重启了", "a server restart") if restart else ("急停", "the emergency stop")
    n = 0
    for j in list(_jobs.values()):
        if j.status != "running" or j.task is None or not (restart or j.background):
            continue
        j.task.cancel()
        n += 1
        zh, en = _what(j.tool)
        try:
            host_process.note(j.conversation, L(f"{zh}没做完：{by_zh}", f"{en.capitalize()} generation stopped by {by_en}"),
                              f"The {j.tool} {j.id} ({j.ref}, prompt: {j.prompt[:200]}) was stopped by {by_en} before it finished; it may "
                              "already have been paid for. If the user still wants it, start it again.")
        except Exception:  # noqa: BLE001
            pass
    await asyncio.gather(*(j.task for j in _jobs.values() if j.task is not None and (restart or j.background)), return_exceptions=True)
    return n


# —— 工具 ——————————————————————————————————————————————————————————————————————————

def _opts(tool: str, c: media.Choice, args: dict) -> dict:
    o: dict = {}
    if tool == VIDEO:
        ratio = str(args.get("aspect_ratio") or c.opts.get("aspect_ratio") or "").strip()
        res = str(args.get("resolution") or c.opts.get("resolution") or "").strip().lower()
        omni = c.provider == "google"
        if ratio and not (ratio in OMNI_RATIOS if omni else RATIO.match(ratio)):
            raise ToolFailure(L(f"aspect_ratio 写得不对（{ratio}；能写 {' / '.join(OMNI_RATIOS)}）" if omni else f"aspect_ratio 写得不对（{ratio}）",
                                f"aspect_ratio isn't valid ({ratio}; use {' or '.join(OMNI_RATIOS)})" if omni else f"aspect_ratio isn't valid ({ratio})"))
        if res and not (res in OMNI_RESOLUTIONS if omni else RESOLUTION.match(res)):
            raise ToolFailure(L(f"resolution 写得不对（{res}）", f"resolution isn't valid ({res}; e.g. {' / '.join(OMNI_RESOLUTIONS)})"))
        if c.provider == media.GROK:  # 10/20：Grok 订阅的比例、清晰度、长短先在这边查（engine/grok_media.py）
            from . import grok_media
            secs = args.get("seconds")
            grok_media.video_opts(ratio, res, float(secs) if isinstance(secs, (int, float)) and not isinstance(secs, bool) else None)
        o.update({"aspect_ratio": ratio or None, "resolution": res or None})
    else:
        lyrics = str(args.get("lyrics") or "").strip()
        if len(lyrics) > LYRICS_MAX:
            raise ToolFailure(L(f"歌词太长（最多 {LYRICS_MAX} 字）", f"The lyrics are too long ({LYRICS_MAX} characters at most)"))
        o.update({"lyrics": lyrics or None, "instrumental": args.get("instrumental") is True})
    if args.get("seconds") not in (None, ""):
        try:
            secs = float(args["seconds"])
        except (TypeError, ValueError):
            raise ToolFailure(L("seconds 要是个数", "seconds must be a number")) from None
        top = 60 if tool == VIDEO else 600
        if not 1 <= secs <= top:
            raise ToolFailure(L(f"seconds 要在 1–{top} 之间", f"seconds must be between 1 and {top}"))
        o["seconds"] = secs
    return o


def estimate(c: media.Choice | None) -> str:
    """卡上、说明里那一句：一段视频大概多少钱。"""
    if c is not None and c.provider == media.GROK:  # 10/20：Grok 订阅
        return L("不另付钱：算你的 Grok 订阅的额度", "no extra charge: it counts against your Grok subscription's allowance")
    if c is not None and c.provider == "google" and c.model.startswith("gemini-omni"):
        lo, hi = (s * OMNI_PER_SECOND for s in OMNI_SECONDS)
        return L(f"约 ${lo:.2f}–{hi:.2f}（Omni Flash 一段 3–10 秒，720p 每秒约 ${OMNI_PER_SECOND:.2f}；1080p、4K 是放大的，可能更贵）",
                 f"about ${lo:.2f}–{hi:.2f} (Omni Flash makes 3–10 s, about ${OMNI_PER_SECOND:.2f} a second at 720p; 1080p and 4K are upscaled "
                 "and may cost more)")
    return L("价钱看那个模型在 fal 上的说明（这里没核对）", "priced by fal per model (not checked here; see the model's page)")


def title(a: dict) -> str:
    return " ".join(str(a.get("prompt") or a.get("action") or "").split())[:60]


async def run(tool: str, args: dict, ctx: ToolCtx) -> str:
    from . import host_process
    from .handkit import approved_run
    action = str(args.get("action") or "generate").strip().lower()
    if action == "status":
        return _status(ctx.conversation, tool)
    if action != "generate":
        raise ToolFailure(L("action 只能是 generate 或 status", "action must be generate or status"))
    zh, en = _what(tool)
    c = media.choice(PURPOSE[tool])
    if c is None:
        raise ToolFailure(L(f"生成{zh}没配（engine.use.{PURPOSE[tool]}）", f"{en.capitalize()} generation isn't set up (engine.use.{PURPOSE[tool]})"))
    prompt = " ".join(str(args.get("prompt") or "").split()) if tool == VIDEO else str(args.get("prompt") or "").strip()
    if not prompt:
        raise ToolFailure(L(f"要写 prompt（做什么样的{zh}）", f"Give a prompt (what the {en} should be)"))
    if len(prompt) > PROMPT_MAX:
        raise ToolFailure(L(f"prompt 太长了（最多 {PROMPT_MAX} 字）", f"The prompt is too long ({PROMPT_MAX} characters at most)"))
    who = ctx.agent or "main"
    same = next((j for j in _jobs.values() if j.conversation == ctx.conversation and j.tool == tool and j.status == "running" and j.prompt == prompt),
                None)
    if same is not None:
        return (f"Already working on this: {same.id} (started {int(time.time() - same.started)}s ago, {same.ref}). It comes into this chat when "
                "it's ready; don't call again.")
    from . import spend
    spend.before_tool(ctx, c.provider)  # 第 4 批：今天的花费到了上限就不做（本机的不拦）
    origin = ctx.origin  # 后台的那件活照这个算；点了卡同意的照交卡那一轮的来路（handkit.run_approved，10/21：不升成主人自己引出来的）
    if c.provider == media.GROK:  # 10/20：Grok 订阅：这一轮不能花就不起活
        from . import grok_media
        try:
            grok_media.check(ctx.kind, origin)
        except media.MediaError as e:
            raise ToolFailure(L(f"没生成（{c.ref}）：{e}", f"Not generated ({c.ref}): {e}")) from None
    if sum(1 for j in _jobs.values() if j.agent == who and j.status == "running") >= MAX_RUNNING:
        raise ToolFailure(L(f"已经有 {MAX_RUNNING} 件在做了：等它们做完再来", f"{MAX_RUNNING} are already being made; wait for them to finish"))
    o = _opts(tool, c, args)
    ref = None
    if tool == VIDEO and str(args.get("image") or "").strip():
        w = str(args["image"]).strip()
        ref = await media.load({"url": w} if w.startswith(("http://", "https://")) else {"attachment": w} if re.fullmatch(r"[0-9a-f]{12}", w)
                               else {"path": w}, ctx, max_bytes=REF_BYTES, what=("参考图", "reference image"))
        if images.sniff(ref.data) is None:
            raise ToolFailure(L(f"{ref.label} 不是图（PNG、JPEG、GIF、WebP）", f"{ref.label} isn't an image (PNG, JPEG, GIF or WebP)"))
        ref.mime = images.sniff(ref.data) or ref.mime
    name = re.sub(r"[^\w-]+", "-", str(args.get("name") or en).strip())[:40].strip("-") or en
    _prune()
    job = Job(("vg_" if tool == VIDEO else "mg_") + uuid.uuid4().hex[:8], tool, who, ctx.conversation, c.ref, prompt, kind=ctx.kind, origin=origin)
    _jobs[job.id] = job
    job.task = asyncio.ensure_future(_work(job, c, o, ref, name))
    can_tell = host_process.deliverable(ctx.conversation)
    wait = (0.0 if approved_run() else WAIT[tool]) if can_tell else TIMEOUT[tool] + 30
    try:
        if wait > 0:
            await asyncio.wait_for(asyncio.shield(job.done.wait()), wait)
    except asyncio.TimeoutError:
        pass
    except asyncio.CancelledError:  # 用户在等的这一轮里点了停：这件活跟着停
        if not job.background and job.task is not None:
            job.task.cancel()
        raise
    if job.done.is_set() or job.inline:
        await job.done.wait()
        if job.status != "done":
            raise ToolFailure(job.text)
        return job.text + ("\n(This model picks the length itself: seconds was ignored.)" if o.get("seconds") and c.provider == "google" else "")
    job.background = True
    return (f"Started {job.id} ({c.ref}): making the {en} takes a while, so it carries on in the background and comes into this chat when it's "
            f"ready (with the file under that reply). Don't call {tool} again for it; action status shows where it is. Tell the user it's on its way.")


async def ask(tool_: Tool, args: dict, ctx: ToolCtx) -> dict | str:
    """video_generate 要点头：交一张卡，写清模型、估计多少钱、提示词；同意了 handkit.run_approved 照原样起一次（不等，做好了交回对话）。"""
    import inbox

    from . import handkit
    from . import tools as tools_mod
    agent = ctx.agent or "main"
    c = media.choice(PURPOSE[VIDEO])
    body = json.dumps({k: v for k, v in args.items() if k != "why"}, ensure_ascii=False, indent=2, default=str)
    if (no := handkit.too_long(body)) is not None:
        return no
    who = inbox.source_name(agent, inbox.names())
    ref = c.ref if c else "?"
    fence = tools_mod._fence(body)
    card = {"title": L(f"生成视频：{title(args)[:40]}", f"Generate a video: {title(args)[:40]}"),
            "why": str(args.get("why") or "").strip() or L(f"{who}想生成一段视频（{ref}）。", f"{who} wants to generate a video ({ref})."),
            "changes": [L(f"模型：{ref}", f"Model: {ref}"), L(f"花费：{estimate(c)}", f"Cost: {estimate(c)}"),
                        L("做好了发进这个对话（要几分钟）", "It comes into this chat when it's ready (a few minutes)")],
            "detail": L("同意后，服务器照下面的参数生成一次，做好了发进对话。", "If you approve, the server makes exactly this once and posts it to the chat "
                        "when it's ready.") + f"\n\n{fence}json\n{body}\n{fence}",
            "approveLabel": L("生成", "Generate")}
    dedupe = "video:" + hashlib.sha256(json.dumps([agent, args], ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()[:32]
    return await tools_mod.submit_card(VIDEO, json.dumps(args, ensure_ascii=False, default=str), None, card, dedupe, ctx, who=agent)


def tool(tool_name: str, agent: str) -> Tool | None:
    if not on(tool_name):
        return None
    from .handkit import levelled
    c = media.choice(PURPOSE[tool_name])

    def cls(a: dict) -> tuple[str, str]:
        if str(a.get("action") or "").strip().lower() == "status":
            return READ, L("只看在做的那几件", "Only looks at the ones being made")
        if tool_name == VIDEO and c is not None and c.provider == media.GROK:  # 10/20：Grok 订阅：不另付钱，照旧每段先问
            return levelled(VIDEO, (ASK, L("算你的 Grok 订阅的额度（不另付钱）；每段先问你", "Uses your Grok subscription's allowance "
                                           "(no extra charge); each clip asks you first")))
        if tool_name == VIDEO:
            return levelled(VIDEO, (ASK, L(f"按量花钱：{estimate(c)}", f"It costs money: {estimate(c)}")))
        return levelled(MUSIC, (UNDO, L("只在这个对话里多一个附件；一首几美分到几毛，记在用量里",
                                        "Only adds an attachment to this chat; a few cents to a few dimes a song, logged in usage")))

    async def go(args: dict, ctx: ToolCtx) -> str:
        return await run(tool_name, args, ctx)

    common = {"action": {"type": "string", "enum": ["generate", "status"], "description": L("generate（默认）或 status（看在做的）",
                                                                                            "generate (default) or status (see the ones in progress)")},
              "prompt": {"type": "string"},
              "seconds": {"type": "number", "description": L("多长（秒；服务商不收的就忽略）", "Length in seconds (ignored where the provider doesn't take it)")},
              "name": {"type": "string", "description": L("文件名（不带扩展名）", "File name (without extension)")}}
    if tool_name == VIDEO:
        props = {**common, "prompt": {"type": "string", "description": L("拍什么：主体、动作、镜头、声音", "What to show: subject, action, camera, sound")},
                 "image": {"type": "string", "description": L("一张起始的参考图（路径、附件 id、网址）", "A starting reference image (path, attachment id or URL)")},
                 "aspect_ratio": {"type": "string", "description": L("16:9 或 9:16", "16:9 or 9:16")},
                 "resolution": {"type": "string", "description": L("720p（默认）、1080p……", "720p (default), 1080p…")},
                 "why": {"type": "string", "description": L("卡上给用户看的一句为什么", "One line for the user's approval card: why")}}
        desc = L(f"生成一段短视频（{c.ref if c else ''}，带声音）：写清拍什么（主体、动作、镜头、声音），可以给一张起始图（image）。按量花钱（{estimate(c)}），"
                 "所以每次先交卡等用户点头。要一会儿：没马上好就在后台接着做，做好了自己发进这个对话（附件挂在那条回复下面）；别为同一件再调，"
                 "action status 看进度。",
                 f"Generate a short video with sound ({c.ref if c else ''}): say what to show (subject, action, camera, sound); image gives a "
                 f"starting picture. It costs money ({estimate(c)}), so each one asks the user first. It takes a while: if it isn't ready right "
                 "away it carries on in the background and comes into this chat by itself (with the file under that reply); don't call again "
                 "for the same one, action status shows progress.")
        if c is not None and c.provider == media.GROK:  # 10/20：Grok 订阅：没有声音、1–15 秒、不另付钱
            desc = L(f"生成一段短视频（{c.ref}，Grok 订阅，没有声音）：写清拍什么（主体、动作、镜头），可以给一张起始图（image）；seconds 1–15（默认 8），"
                     "aspect_ratio 1:1、16:9、9:16、4:3、3:4、3:2、2:3，resolution 480p / 720p。算用户 Grok 订阅的额度（不另付钱），每次照样先交卡等他"
                     "点头。要一会儿：没马上好就在后台接着做，做好了自己发进这个对话；别为同一件再调，action status 看进度。",
                     f"Generate a short silent video ({c.ref}, the user's Grok subscription): say what to show (subject, action, camera); image "
                     "gives a starting picture; seconds 1–15 (default 8), aspect_ratio 1:1, 16:9, 9:16, 4:3, 3:4, 3:2 or 2:3, resolution 480p or "
                     "720p. It uses the user's Grok subscription allowance (no extra charge), and each one still asks the user first. It takes a "
                     "while: if it isn't ready right away it carries on in the background and comes into this chat by itself; don't call again "
                     "for the same one, action status shows progress.")
    else:
        props = {**common, "prompt": {"type": "string", "description": L("什么样的音乐：风格、情绪、乐器、主题", "What kind of music: style, mood, instruments, theme")},
                 "lyrics": {"type": "string", "description": L("歌词（可选）", "Lyrics (optional)")},
                 "instrumental": {"type": "boolean", "description": L("纯音乐，不要人声", "Instrumental only, no vocals")}}
        desc = L(f"生成一段音乐（{c.ref if c else ''}）：写清风格、情绪、乐器、主题；可以给歌词，或者 instrumental 要纯音乐。存成这个对话的附件，"
                 "回复下面能放。要一会儿：没马上好就在后台接着做，做好了自己发进这个对话；别为同一件再调，action status 看进度。",
                 f"Generate music ({c.ref if c else ''}): say the style, mood, instruments and theme; give lyrics, or instrumental for no "
                 "vocals. It's saved as an attachment of this chat and plays under the reply. It takes a while: if it isn't ready right away it "
                 "carries on in the background and comes into this chat by itself; don't call again for the same one, action status shows progress.")
    return Tool(name=tool_name, kinds=("owner",), run=go, classify=cls, title=title, timeout=TIMEOUT[tool_name] + 90, max_output=6000,
                description=desc, schema={"type": "object", "properties": props})
