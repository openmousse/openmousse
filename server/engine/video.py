"""看视频（第 3 批第 7 项，2026-10-19）：video_analyze 工具、「看视频」用途（engine.use.video）、用户发来的视频可选先看一遍。

用途 engine.use.video（不写 = 没有这一样）：
  google/<型号>     收视频的（Gemini）：20 MB 以内放进 inline_data，更大的先传 Files API、等它处理好（state = ACTIVE）再问，问完删掉；
                    视频在前、问题在后（Google 文档的建议）
  别的会看图的型号  （OpenAI、Claude、Kimi……）不收视频：用 ffmpeg 均匀抽 frames 张（默认 FRAMES，最多 FRAMES_MAX）缩到 768 像素的图，连同每张在
                    第几秒一起交给它（听不到声音：要听写配 engine.use.stt 再 transcribe）；没有 ffmpeg 就说看不了
  对象里还能写 frames、max_seconds、url、timeout。
video_analyze（主人回合；配了 engine.use.video 才有）：一段视频（path / url / attachment，engine/media.load，最多 LOAD_MAX）+ question；
  start / end（秒或 mm:ss）只看一段。太长（超过 MAX_SECONDS）的只看开头那么长、太大（超过 SEND_MAX）的先压小（360p、每秒 2 帧）：都要 ffmpeg，
  没有就照原样发（太大的说清楚发不了）。答的话包成资料（视频里的字可能是指令）。read。
用户发来的视频（files.py 现在只存盘）：engine.media.describe_video = true（还要配了 engine.use.video）时，上传的时候先看一遍，描述放进消息
  （和文档抽出来的字一样），不配一个字不变。
花费：每次记一行 engine_media_usage（purpose video；token 数照接口回的，价钱照 media.PRICES，没有就照对话模型的价目）。
没人在看的那几轮、后台任务的子 Agent：不给（unattended.NO_TOOLS、tasks.CHILD_NEVER）。
参考（只读）：Hermes tools/vision_tools.py（video_analyze：Gemini 先上传、等处理好再问；不收视频的抽帧）、OpenClaw docs/nodes/media-understanding.md
  （按能力挑模型、视频太大先截）。只借设计。
"""
from __future__ import annotations

import base64
import re
import tempfile
from pathlib import Path

from config import raw
from i18n import L

from . import media
from .loop import Tool, ToolCtx, ToolFailure

NAME = "video_analyze"
READ = "read"
LOAD_MAX = 200 * 1024 * 1024
SEND_MAX = 100 * 1024 * 1024      # 再大的先压小（Files API 收 2 GB，但传、等都慢，也费 token）
MAX_SECONDS = 40 * 60             # 一次最多看多长（更长的只看开头这一段，或者用 start / end 挑）
FRAMES, FRAMES_MAX = 16, 32
TIMEOUT = 600.0
ANSWER = ("Watch the video and answer the question. Refer to moments by their time (mm:ss). Copy any on-screen text you rely on exactly. "
          "If the video doesn't show what's asked, say so.")
DESCRIBE = ("Describe this video for someone who can't watch it: what happens, in order, with times (mm:ss); people and their actions (don't "
            "identify real people by face); on-screen text copied exactly; what's said, summarised. Be complete but don't pad.")
MAGIC = ((4, b"ftypqt", "video/quicktime"), (4, b"ftyp", "video/mp4"), (0, b"\x1aE\xdf\xa3", "video/webm"))


def on() -> bool:
    return media.choice("video") is not None


def describe_uploads() -> bool:
    """用户发来的视频先看一遍吗（engine.media.describe_video，还要配了看视频）。"""
    e = raw().get("engine")
    m = e.get("media") if isinstance(e, dict) else None
    return on() and isinstance(m, dict) and m.get("describe_video") is True


def kind_of(data: bytes, name: str = "") -> str | None:
    """看文件头认视频：MP4 / MOV / WebM（MKV）/ AVI；认不出 → None。"""
    for off, sig, mime in MAGIC:
        if data[off:off + len(sig)] == sig:
            return "video/x-matroska" if mime == "video/webm" and name.lower().endswith(".mkv") else mime
    if data[:4] == b"RIFF" and data[8:12] == b"AVI ":
        return "video/x-msvideo"
    return None


def seconds_of(v) -> float | None:
    """"90" / "1:30" / "01:02:03" → 秒；空 → None；写错 → ToolFailure。"""
    s = str(v or "").strip()
    if not s:
        return None
    m = re.fullmatch(r"(?:(\d+):)?(?:(\d+):)?(\d+(?:\.\d+)?)", s)
    if not m:
        raise ToolFailure(L(f"时间写成秒或 mm:ss（写的是 {s}）", f"Write times as seconds or mm:ss (got {s})"))
    a, b, c = m.groups()
    if a is not None and b is not None:
        return int(a) * 3600 + int(b) * 60 + float(c)
    return (int(a) * 60 if a is not None else 0) + float(c)


def stamp(t: float) -> str:
    t = int(t)
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60}:{t % 60:02d}"


async def _ffmpeg(args: list[str], *, data: bytes, ext: str, out_ext: str, timeout: float = 300) -> bytes | list[bytes]:
    """用 ffmpeg 处理一段视频（字节进、字节出）。out_ext = "jpg-frames" 时回一组帧。没有 ffmpeg → MediaError(config)。"""
    exe = media.local_command("ffmpeg")
    if exe is None:
        raise media.MediaError("config", L("这台服务器上没有 ffmpeg", "ffmpeg isn't installed on this server"))
    with tempfile.TemporaryDirectory(prefix="mousse-video-") as d:
        src = Path(d) / f"in{ext}"
        src.write_bytes(data)
        out = Path(d) / ("frame-%03d.jpg" if out_ext == "jpg-frames" else f"out.{out_ext}")
        rc, _, err = await media.run_local([exe, "-nostdin", "-y", "-i", str(src), *args, str(out)], timeout=timeout, cwd=d)
        if rc != 0:
            raise media.MediaError("bad", f"ffmpeg couldn't process the video: {err[-300:]}")
        if out_ext == "jpg-frames":
            return [p.read_bytes() for p in sorted(Path(d).glob("frame-*.jpg"))]
        if not out.is_file():
            raise media.MediaError("bad", "ffmpeg wrote nothing")
        return out.read_bytes()


async def prepare(data: bytes, name: str, start: float | None, end: float | None, limit: int) -> tuple[bytes, float | None, list[str]]:
    """截、压：→ (要发的字节, 原来多长, 给模型的几句说明)。"""
    notes: list[str] = []
    secs = await media.duration(data, name)
    ext = Path(name).suffix[:10] or ".mp4"
    lo = start or 0.0
    hi = end if end is not None else None
    if hi is not None and hi <= lo:
        raise ToolFailure(L("end 要在 start 后面", "end must come after start"))
    if secs is not None and lo >= secs:
        raise ToolFailure(L(f"视频只有 {stamp(secs)} 长", f"The video is only {stamp(secs)} long"))
    if hi is None and secs is not None and secs - lo > limit:
        hi = lo + limit
        notes.append(f"[Only {stamp(lo)}–{stamp(hi)} of {stamp(secs)} was watched (at most {stamp(limit)} at a time); pick another part with start / end.]")
    cut = start is not None or hi is not None
    if cut:
        span = ["-ss", f"{lo:.3f}", *(["-t", f"{hi - lo:.3f}"] if hi is not None else [])]
        try:
            data = await _ffmpeg([*span, "-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-c:a", "aac", "-b:a", "64k", "-movflags", "+faststart"],
                                 data=data, ext=ext, out_ext="mp4")
            ext = ".mp4"
        except media.MediaError as e:
            if e.kind == "config":
                raise ToolFailure(L("只看一段要 ffmpeg（这台服务器上没有）", "Watching part of a video needs ffmpeg, which isn't installed")) from None
            raise
    if len(data) > SEND_MAX:
        try:
            data = await _ffmpeg(["-vf", "scale=-2:360", "-r", "2", "-c:v", "libx264", "-preset", "veryfast", "-crf", "32", "-c:a", "aac", "-b:a", "48k"],
                                 data=data, ext=ext, out_ext="mp4")
            notes.append("[The video was shrunk to 360p at 2 frames a second before watching.]")
        except media.MediaError as e:
            if e.kind == "config":
                raise ToolFailure(L(f"视频太大（{media.human_bytes(len(data))}），压小要 ffmpeg（这台服务器上没有）：用 start / end 挑一段",
                                    f"The video is too large ({media.human_bytes(len(data))}) and shrinking it needs ffmpeg, which isn't installed")) from None
            raise
    return data, secs, notes


async def ask(c: media.Choice, data: bytes, mime: str, name: str, question: str, *, frames: int, offset: float = 0.0,
              conversation: str | None = None, agent: str | None = None) -> tuple[str, str]:
    """看视频的模型看这一段、答 question（空 = 描述一遍）→ (它的话, 怎么看的：whole / frames)。出错抛 MediaError。"""
    prompt = f"{ANSWER}\n\nQuestion: {question}" if question else str(c.opts.get("prompt") or DESCRIBE)
    limit = int(c.opts.get("max_tokens") or 4096)
    with media.Meter("video", c, conversation, agent) as m:
        m.unit = "token"
        if c.provider == "google":
            uploaded = None
            if len(data) > media.INLINE_MAX:
                uploaded = await media.gemini_upload(c, data, mime, name)
                part = {"file_data": {"mime_type": uploaded["mime_type"], "file_uri": uploaded["uri"]}}
            else:
                part = media.inline(mime, data)
            try:
                got = await media.gemini(c, [part, {"text": prompt}], config={"maxOutputTokens": limit}, timeout=float(c.opts.get("timeout") or TIMEOUT))
            finally:
                if uploaded:
                    await media.gemini_forget(c, uploaded["name"])
            text = media.gemini_text(got).strip()
            m.tokens_in, m.tokens_out = media.gemini_usage(got)
            mods = media.gemini_modalities(got)
            if mods.get("AUDIO"):
                m.extra = {"audio_tokens_in": mods["AUDIO"]}
            how = "whole"
        else:
            secs = await media.duration(data, name)
            n = max(1, min(FRAMES_MAX, frames))
            fps = f"fps={n}/{max(secs or n, 1.0):.3f}"  # 均匀抽 n 张
            try:
                shots = await _ffmpeg(["-vf", f"{fps},scale='min(768,iw)':-2", "-frames:v", str(n), "-q:v", "4"], data=data,
                                      ext=Path(name).suffix[:10] or ".mp4", out_ext="jpg-frames")
            except media.MediaError as e:
                if e.kind == "config":
                    raise media.MediaError("config", L(f"{c.ref} 不收视频，抽帧要 ffmpeg（这台服务器上没有）",
                                                       f"{c.ref} can't take video and taking frames needs ffmpeg, which isn't installed")) from None
                raise
            if not shots:
                raise media.MediaError("bad", "ffmpeg took no frames")
            step = (secs or len(shots)) / len(shots)
            times = ", ".join(stamp(offset + (i + 0.5) * step) for i in range(len(shots)))
            blocks = [{"type": "image", "mime": "image/jpeg", "data": base64.b64encode(s).decode()} for s in shots]
            text, usage, _ = await media.ask(c, f"{prompt}\n\nThese are {len(shots)} frames taken evenly from the video, at {times}. There is no "
                                             "sound.", blocks, max_tokens=limit)
            m.tokens_in, m.tokens_out = usage["in"], usage["out"]
            how = "frames"
        m.units = m.tokens_in + m.tokens_out
    if not text:
        raise media.MediaError("shape", f"{c.ref}: empty answer")
    return text, how


async def describe_upload(path: Path, mime: str | None) -> tuple[str, str, str]:
    """files.py 的上传（开了 describe_video）：先看一遍 → (描述（包成资料）, 备注, 状态)。看不了：("", 一句为什么, "warn")。"""
    c = media.choice("video")
    if c is None:
        return "", "", "ok"
    try:
        size = path.stat().st_size
        if size > LOAD_MAX:
            return "", L("视频太大，没有先看", "Too large to watch first"), "warn"
        data = path.read_bytes()
        mime = kind_of(data, path.name) or mime or "video/mp4"
        data, secs, _ = await prepare(data, path.name, None, None, int(c.opts.get("max_seconds") or MAX_SECONDS))
        text, _ = await ask(c, data, mime, path.name, "", frames=int(c.opts.get("frames") or FRAMES))
    except (media.MediaError, ToolFailure, OSError) as e:
        return "", L(f"没看成：{str(e)[:120]}", f"Couldn't watch it: {str(e)[:120]}"), "warn"
    note = L(f"视频{f'，{stamp(secs)}' if secs else ''}，{c.ref} 看过", f"Video{f', {stamp(secs)}' if secs else ''}, watched by {c.ref}")
    return media.wrap(f"video {path.name}"[:200].replace('"', "%22"), text), note, "ok"


# —— video_analyze ————————————————————————————————————————————————————————————————————

def tool(agent: str) -> Tool | None:
    if not on():
        return None
    from .handkit import levelled
    c0 = media.choice("video")

    async def go(args: dict, ctx: ToolCtx) -> str:
        c = media.choice("video")
        if c is None:
            raise ToolFailure(L("看视频没配（engine.use.video）", "Video isn't set up (engine.use.video)"))
        question = " ".join(str(args.get("question") or "").split())[:2000]
        start, end = seconds_of(args.get("start")), seconds_of(args.get("end"))
        src = await media.load(args, ctx, max_bytes=LOAD_MAX, what=("视频", "video"))
        mime = kind_of(src.data, src.name)
        if mime is None:
            raise ToolFailure(L(f"{src.label} 不是认得的视频（MP4、MOV、WebM、MKV、AVI）", f"{src.label} isn't a video format this knows (MP4, MOV, WebM, MKV or AVI)"))
        limit = int(c.opts.get("max_seconds") or MAX_SECONDS)
        data, secs, notes = await prepare(src.data, src.name, start, end, limit)
        if data is not src.data:
            mime = "video/mp4"
        try:
            text, how = await ask(c, data, mime, src.name, question, frames=int(args.get("frames") or c.opts.get("frames") or FRAMES),
                                  offset=start or 0.0, conversation=ctx.conversation, agent=ctx.agent)
        except media.MediaError as e:
            raise ToolFailure(L(f"看视频的模型（{c.ref}）没答上来：{e}", f"The video model ({c.ref}) couldn't answer: {e}")) from None
        head = (f"{src.label}: video" + (f", {stamp(secs)}" if secs else "") + f", {media.human_bytes(len(src.data))}"
                + (" (watched as frames, no sound)" if how == "frames" else ""))
        what = "Answer" if question else "Description"
        return (f"{head}\n" + ("\n".join(notes) + "\n" if notes else "") + f"{what} from {c.ref}:\n"
                + media.wrap(f"video_analyze {src.label}"[:200].replace('"', "%22").replace("\n", " "), text))

    return Tool(
        name=NAME, kinds=("owner",), run=go, timeout=TIMEOUT + 120, max_output=20_000,
        title=lambda a: str(a.get("path") or a.get("url") or a.get("attachment") or "")[:80],
        classify=lambda a: levelled(NAME, (READ, L("只看一段视频（网址只去公网）；按量花一点钱，记在用量里",
                                                   "Only watches a video (web addresses: public internet only); costs a little and is logged"))),
        description=L(
            f"看一段视频、回答问题（{c0.ref if c0 else ''}）：工作区里的视频文件、这个对话的附件（路径或 attachment id）、公网上的（url）。"
            f"start / end（秒或 mm:ss）只看一段；太长的一次只看 {MAX_SECONDS // 60} 分钟。不写 question = 讲一遍内容。",
            f"Watch a video and answer a question about it ({c0.ref if c0 else ''}): a video file in your workspace, an attachment of this "
            f"conversation (its path or attachment id) or one on the public web (url). start / end (seconds or mm:ss) pick a part; long videos "
            f"are watched {MAX_SECONDS // 60} minutes at a time. Without question it describes the video."),
        schema={"type": "object", "properties": {
            "path": {"type": "string", "description": L("工作区里的路径，或者消息里写着的附件路径", "A workspace path, or an attachment path a message gave")},
            "url": {"type": "string", "description": L("公网上一段视频的网址", "Address of a video on the public web")},
            "attachment": {"type": "string", "description": L("这个对话的附件 id", "An attachment id from this conversation")},
            "question": {"type": "string", "description": L("想从视频里知道什么", "What you want to know from the video")},
            "start": {"type": "string", "description": L("从哪儿看起（秒或 mm:ss）", "Where to start (seconds or mm:ss)")},
            "end": {"type": "string", "description": L("看到哪儿（秒或 mm:ss）", "Where to stop (seconds or mm:ss)")},
            "frames": {"type": "integer", "description": L(f"不收视频的模型抽几张（默认 {FRAMES}，最多 {FRAMES_MAX}）",
                                                         f"Frames for models that can't take video (default {FRAMES}, max {FRAMES_MAX})")}}})
