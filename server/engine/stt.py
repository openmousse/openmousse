"""转写（第 3 批第 4 项，2026-10-19）：transcribe 工具、「转写」用途（engine.use.stt）、给渠道和附件用的 transcribe_file。

用途 engine.use.stt（不写 = 没有这个工具；附件、语音输入照旧走 files.py 原来那条：OpenAI 的 gpt-4o-transcribe，不行再 whisper-1）：
  openai/<型号>       POST <url>/audio/transcriptions（multipart：file、model、prompt、language）：gpt-4o-transcribe、gpt-4o-mini-transcribe、
                      whisper-1（whisper-1 回 verbose_json：带时长）
  groq/<型号>         同一个接口（https://api.groq.com/openai/v1）：whisper-large-v3-turbo、whisper-large-v3，回 verbose_json
  google/<型号>       Gemini 自己的接口：音频放进 inline_data（20 MB 以内；更大的先传 Files API），让它逐字写下来
  deepgram/<型号>     POST https://api.deepgram.com/v1/listen?model=nova-3&smart_format=true（Authorization: Token 密钥，正文就是音频）
  elevenlabs/<型号>   POST https://api.elevenlabs.io/v1/speech-to-text（multipart：file、model_id = scribe_v1……；xi-api-key）
  local/whisper-cli   本机的 whisper.cpp（PATH 上找得到才有）：{"model": "local/whisper-cli", "model_path": "~/models/ggml-base.bin"}；
                      不是 WAV 的先用 ffmpeg 转成 16 kHz 单声道（没有 ffmpeg 只收 WAV）。不花钱、不出门
  对象里还能写 language（默认自动认）、prompt（词表：你常说的专有名词，不写就用 server.json 的 transcribe_prompt）、url、timeout。
transcribe（主人回合；配了 engine.use.stt 才有，local 的还要找得到命令）：一段音频（path / url / attachment，engine/media.load）→ 文字
  （包成资料：录音里的话是别人说的也说不定）。read。
transcribe_file（给渠道、附件、语音输入用，见下面的签名）：给音频文件 → Transcript；没配 engine.use.stt 就走 files.py 原来那条。
  files.transcribe 配了 engine.use.stt 时也改走这里（transcribe_sync），不配一个字不变。
花费：每次记一行 engine_media_usage（purpose stt；按秒的照接口回的时长，没回就用 ffprobe 量；Gemini 按 token；本机的记 0）。
没人在看的那几轮、后台任务的子 Agent：不给 transcribe（unattended.NO_TOOLS、tasks.CHILD_NEVER）。
参考（只读）：Hermes tools/transcription_cloud.py（OpenAI / Groq 的 SDK 写法、ElevenLabs Scribe 的 multipart、model_id、language_code）、
  transcription_whisper_cpp.py（whisper-cli 的参数：-m -f -l -otxt -of）、hermes_cli/tools_config.py（哪几家）；OpenClaw docs/nodes/audio.md
  （按顺序挑、OpenAI 默认 gpt-4o-transcribe、本机的 whisper-cli）。只借设计。
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import mimetypes
import tempfile
from dataclasses import dataclass
from pathlib import Path

from i18n import L

from . import media
from .loop import Tool, ToolCtx, ToolFailure

NAME = "transcribe"
READ = "read"
MAX_BYTES = 100 * 1024 * 1024
TIMEOUT = 300.0
PROMPT = ("Transcribe this audio exactly, word for word, in the language(s) spoken. Output only the transcript, with no comments. "
          "Mark unclear words as [inaudible].")
OPENAI_LIKE = ("openai", "groq")


@dataclass
class Transcript:
    text: str
    seconds: float | None   # 多长（接口回的、量出来的；不知道 = None）
    provider: str
    model: str


def on() -> bool:
    c = media.choice("stt")
    return c is not None and (c.provider != media.LOCAL or media.local_command(c.model) is not None)


def _mime(name: str, mime: str | None) -> str:
    return (mime or mimetypes.guess_type(name)[0] or "application/octet-stream").split(";")[0].strip().lower()


async def run(c: media.Choice, data: bytes, name: str, mime: str | None = None, *, language: str | None = None, prompt: str | None = None,
              conversation: str | None = None, agent: str | None = None) -> Transcript:
    """按 c 转写一段音频。出错抛 media.MediaError（报错里不带密钥）。记一行花费。"""
    mime = _mime(name, mime)
    language = language or (str(c.opts.get("language")) if c.opts.get("language") else None)
    prompt = prompt if prompt is not None else (str(c.opts.get("prompt")) if c.opts.get("prompt") else None)
    with media.Meter("stt", c, conversation, agent) as m:
        if c.provider in OPENAI_LIKE or c.opts.get("api") == "openai":
            text, secs = await _openai(c, data, name, mime, language, prompt)
        elif c.provider == "google":
            text, secs, toks = await _gemini(c, data, name, mime, language, prompt)
            m.unit, m.tokens_in, m.tokens_out = "token", toks[0], toks[1]
            m.extra = {"audio_tokens_in": toks[2]}
            m.units = toks[0] + toks[1]
        elif c.provider == "deepgram":
            text, secs = await _deepgram(c, data, mime, language)
        elif c.provider == "elevenlabs":
            text, secs = await _elevenlabs(c, data, name, mime, language)
        elif c.provider == media.LOCAL:
            text, secs = await _local(c, data, name, language)
            m.cost = 0.0
        else:
            raise media.MediaError("config", L(f"{c.provider} 不会转写（能写的：openai、groq、google、deepgram、elevenlabs、local/whisper-cli）",
                                               f"{c.provider} can't transcribe (use openai, groq, google, deepgram, elevenlabs or local/whisper-cli)"))
        if secs is None:
            secs = await media.duration(data, name)
        if c.provider != "google":
            m.unit, m.units = "second", float(secs or 0)
            m.unpriced = secs is None and m.cost is None  # 不知道多长：记一行，钱是 null（不按 0 算）
    return Transcript(text.strip(), secs, c.provider, c.model)


async def _openai(c: media.Choice, data: bytes, name: str, mime: str, language: str | None, prompt: str | None) -> tuple[str, float | None]:
    base, key, key_env = media.endpoint(c)
    if key_env:  # 写了 key_env = "" 的（自己本机的兼容服务）不要密钥
        media.need_key(c, key, key_env)
    verbose = c.model.startswith("whisper")  # whisper 系的回 verbose_json（带时长）；gpt-4o-*-transcribe 只回 json / text
    fields = {"model": c.model, "response_format": "verbose_json" if verbose else "json",
              **({"prompt": prompt} if prompt else {}), **({"language": language} if language else {})}
    out, _ = await media.call(c, "POST", f"{base}/audio/transcriptions", key=key, headers={"Authorization": f"Bearer {key}"} if key else {},
                              data=fields, files={"file": (name, data, mime)}, timeout=float(c.opts.get("timeout") or TIMEOUT))
    if not isinstance(out, dict) or not isinstance(out.get("text"), str):
        raise media.MediaError("shape", f"{c.provider}: the answer has no text")
    u = out.get("usage") if isinstance(out.get("usage"), dict) else {}
    secs = out.get("duration") if isinstance(out.get("duration"), (int, float)) else u.get("seconds") if u.get("type") == "duration" else None
    return out["text"], float(secs) if secs is not None else None


async def _gemini(c: media.Choice, data: bytes, name: str, mime: str, language: str | None, prompt: str | None) -> tuple[str, float | None, tuple[int, int, int]]:
    ask = PROMPT + (f" The language is {language}." if language else "") + (f" Words that may come up: {prompt}" if prompt else "")
    uploaded = None
    if len(data) > media.INLINE_MAX:
        uploaded = await media.gemini_upload(c, data, mime, name)
        part = {"file_data": {"mime_type": uploaded["mime_type"], "file_uri": uploaded["uri"]}}
    else:
        part = media.inline(mime, data)
    try:
        got = await media.gemini(c, [{"text": ask}, part], config={"temperature": 0}, timeout=float(c.opts.get("timeout") or TIMEOUT))
    finally:
        if uploaded:
            await media.gemini_forget(c, uploaded["name"])
    i, o = media.gemini_usage(got)
    mods = media.gemini_modalities(got)
    return media.gemini_text(got), None, (i, o, mods.get("AUDIO", 0) if mods else i)  # 没分开报：都按音频的价（不少算）


async def _deepgram(c: media.Choice, data: bytes, mime: str, language: str | None) -> tuple[str, float | None]:
    base, key, key_env = media.endpoint(c)
    media.need_key(c, key, key_env)
    params = {"model": c.model, "smart_format": "true", "punctuate": "true", **({"language": language} if language else {"detect_language": "true"})}
    out, _ = await media.call(c, "POST", f"{base}/listen", key=key, params=params, content=data,
                              headers={"Authorization": f"Token {key}", "Content-Type": mime}, timeout=float(c.opts.get("timeout") or TIMEOUT))
    try:
        alt = out["results"]["channels"][0]["alternatives"][0]
        text = str(alt.get("transcript") or "")
    except (KeyError, IndexError, TypeError):
        raise media.MediaError("shape", f"{c.provider}: the answer has no transcript") from None
    secs = ((out.get("metadata") or {}) if isinstance(out, dict) else {}).get("duration")
    return text, float(secs) if isinstance(secs, (int, float)) else None


async def _elevenlabs(c: media.Choice, data: bytes, name: str, mime: str, language: str | None) -> tuple[str, float | None]:
    base, key, key_env = media.endpoint(c)
    media.need_key(c, key, key_env)
    fields = {"model_id": c.model, **({"language_code": language} if language else {})}
    out, _ = await media.call(c, "POST", f"{base}/speech-to-text", key=key, headers={"xi-api-key": key}, data=fields,
                              files={"file": (name, data, mime)}, timeout=float(c.opts.get("timeout") or TIMEOUT))
    if not isinstance(out, dict) or not isinstance(out.get("text"), str):
        raise media.MediaError("shape", f"{c.provider}: the answer has no text")
    ends = [w.get("end") for w in out.get("words") or [] if isinstance(w, dict) and isinstance(w.get("end"), (int, float))]
    return out["text"], float(max(ends)) if ends else None


async def _local(c: media.Choice, data: bytes, name: str, language: str | None) -> tuple[str, float | None]:
    exe = media.local_command(c.model)
    if exe is None:
        raise media.MediaError("config", L(f"这台服务器上没有 {c.model}", f"{c.model} isn't installed on this server"))
    mp = Path(str(c.opts.get("model_path") or "")).expanduser()
    if not str(c.opts.get("model_path") or "") or not mp.is_file():
        raise media.MediaError("config", L("本机转写要写 model_path（whisper.cpp 的模型文件，比如 ggml-base.bin）",
                                           "Local transcription needs model_path (a whisper.cpp model file such as ggml-base.bin)"))
    with tempfile.TemporaryDirectory(prefix="mousse-stt-") as d:
        src = Path(d) / ("in" + (Path(name).suffix[:10] or ".bin"))
        src.write_bytes(data)
        wav = src
        if not (data[:4] == b"RIFF" and data[8:12] == b"WAVE"):
            ff = media.local_command("ffmpeg")
            if ff is None:
                raise media.MediaError("config", L("本机转写只收 WAV（装上 ffmpeg 别的格式也能转）", "Local transcription takes WAV only (install ffmpeg for other formats)"))
            wav = Path(d) / "in16k.wav"
            rc, _, err = await media.run_local([ff, "-nostdin", "-y", "-i", str(src), "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(wav)],
                                               timeout=120, cwd=d)
            if rc != 0 or not wav.is_file():
                raise media.MediaError("bad", f"ffmpeg couldn't read the audio: {err[-300:]}")
        out = Path(d) / "transcript"
        rc, _, err = await media.run_local([exe, "-m", str(mp), "-f", str(wav), "-l", language or "auto", "-otxt", "-of", str(out), "-np"],
                                           timeout=float(c.opts.get("timeout") or 1800), cwd=d)
        txt = out.with_suffix(".txt")
        if rc != 0 or not txt.is_file():
            raise media.MediaError("bad", f"{c.model} failed: {err[-300:]}")
        secs = await media.duration(wav.read_bytes(), "in.wav")
        return txt.read_text(encoding="utf-8", errors="replace"), secs


# —— 给渠道、附件、语音输入 —————————————————————————————————————————————————————————————

async def transcribe_file(path: str | Path, *, mime: str | None = None, language: str | None = None, prompt: str | None = None,
                          conversation: str | None = None, agent: str | None = None) -> Transcript:
    """给第 6 批（渠道收到的语音）和 files.py 用：一个音频文件 → Transcript(text, seconds, provider, model)。
    配了 engine.use.stt 用它；没配走 files.py 原来那条（OpenAI 的 gpt-4o-transcribe，不行再 whisper-1；要 OPENAI_API_KEY）。
    出错抛 media.MediaError（kind 见 engine/media.py；报错里不带密钥）。文件最多 MAX_BYTES。"""
    p = Path(path)
    c = media.choice("stt")
    if c is None:
        import files
        try:
            text = await asyncio.to_thread(files.transcribe, p, mime)
        except RuntimeError as e:
            raise media.MediaError("bad", str(e)) from None
        return Transcript(text, None, "openai", files.TRANSCRIBE_MODELS[0])
    try:
        size = p.stat().st_size
    except OSError:
        raise media.MediaError("bad", L(f"读不到 {p.name}", f"Can't read {p.name}")) from None
    if size > MAX_BYTES:
        raise media.MediaError("bad", L(f"{p.name} 太大了（最多 {MAX_BYTES // 1024 // 1024} MB）", f"{p.name} is too large ({MAX_BYTES // 1024 // 1024} MB at most)"))
    data = await asyncio.to_thread(p.read_bytes)
    return await run(c, data, p.name, mime, language=language, prompt=prompt, conversation=conversation, agent=agent)


def transcribe_sync(path: str | Path, *, mime: str | None = None, prompt: str | None = None) -> Transcript:
    """同步的 transcribe_file（files.py 的上传、语音输入在同步的代码里调；可能正在事件循环里，所以另开一个线程跑）。"""
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(asyncio.run, transcribe_file(path, mime=mime, prompt=prompt)).result()


# —— transcribe ————————————————————————————————————————————————————————————————————

def tool(agent: str) -> Tool | None:
    if not on():
        return None
    from .handkit import levelled
    c0 = media.choice("stt")

    async def go(args: dict, ctx: ToolCtx) -> str:
        c = media.choice("stt")
        if c is None:
            raise ToolFailure(L("转写没配（engine.use.stt）", "Transcription isn't set up (engine.use.stt)"))
        src = await media.load(args, ctx, max_bytes=MAX_BYTES, what=("音频", "audio"))
        lang = str(args.get("language") or "").strip()[:20] or None
        hint = " ".join(str(args.get("prompt") or "").split())[:500] or None
        try:
            t = await run(c, src.data, src.name, src.mime or None, language=lang, prompt=hint, conversation=ctx.conversation, agent=ctx.agent)
        except media.MediaError as e:
            raise ToolFailure(L(f"转写没成（{c.ref}）：{e}", f"Transcription failed ({c.ref}): {e}")) from None
        if not t.text:
            return L(f"{src.label}：没听出说了什么（{c.ref}）", f"{src.label}: no speech was recognised ({c.ref})")
        how = f"{t.seconds:.0f} s, " if t.seconds else ""
        return (f"{src.label}: transcript ({how}{c.ref})\n"
                + media.wrap(f"transcribe {src.label}"[:200].replace('"', "%22").replace("\n", " "), t.text))

    return Tool(
        name=NAME, kinds=("owner",), run=go, timeout=TIMEOUT + 60, max_output=60_000,
        title=lambda a: str(a.get("path") or a.get("url") or a.get("attachment") or "")[:80],
        classify=lambda a: levelled(NAME, (READ, L("只把一段录音转成字；按量花一点钱，记在用量里", "Only turns a recording into text; costs a little and is logged"))),
        description=L(
            f"把一段录音转成文字（{c0.ref if c0 else ''}）：工作区里的音频文件、这个对话的附件（路径或 attachment id）、公网上的（url）。"
            "language 可以写语言（不写自动认），prompt 写会出现的专有名词。",
            f"Turn a recording into text ({c0.ref if c0 else ''}): an audio file in your workspace, an attachment of this conversation (its path "
            "or attachment id) or one on the public web (url). language names the language (auto-detected otherwise); prompt lists names "
            "and terms that may come up."),
        schema={"type": "object", "properties": {
            "path": {"type": "string", "description": L("工作区里的路径，或者消息里写着的附件路径", "A workspace path, or an attachment path a message gave")},
            "url": {"type": "string", "description": L("公网上一段音频的网址", "Address of an audio file on the public web")},
            "attachment": {"type": "string", "description": L("这个对话的附件 id", "An attachment id from this conversation")},
            "language": {"type": "string", "description": L("语言代码，比如 zh、en（不写自动认）", "Language code such as en or zh (auto-detected if empty)")},
            "prompt": {"type": "string", "description": L("会出现的专有名词", "Names and terms that may come up")}}})
