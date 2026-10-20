"""朗读（第 3 批第 5 项，2026-10-19）：speak 工具、「朗读」用途（engine.use.tts）、「用语音回」按对话的开关、给渠道用的 synthesize。

用途 engine.use.tts（不写 = 没有这一样）：
  openai/<型号>       POST <url>/audio/speech（{model, input, voice, response_format, instructions?, speed?}）：gpt-4o-mini-tts、tts-1、tts-1-hd；
                      voice 默认 alloy；instructions（语气）只有 gpt-4o-mini-tts 认
  elevenlabs/<型号>   POST https://api.elevenlabs.io/v1/text-to-speech/<voice>?output_format=mp3_44100_128（{text, model_id}；xi-api-key）：
                      eleven_multilingual_v2、eleven_v3、eleven_flash_v2_5……；voice 写声音的 id（必填）
  google/<型号>       Gemini 自己的接口：responseModalities AUDIO + speechConfig.voiceConfig.prebuiltVoiceConfig.voiceName（默认 Kore）：
                      gemini-2.5-flash-preview-tts……；回的是 24 kHz 16 位单声道 PCM，这里包成 WAV
  deepgram/<型号>     POST https://api.deepgram.com/v1/speak?model=<型号>&encoding=mp3（{text}；Token 密钥）：aura-2-thalia-en……
  local/<命令>        本机的命令（PATH 上找得到才有），比如 piper、espeak-ng：{"model": "local/piper", "args": ["--model", "/…/voice.onnx",
                      "--output_file", "{out}"], "stdin": true, "format": "wav"}；args 里 {out} = 写到哪、{text_file} = 字存成的文件、{text} = 字本身、
                      {voice} = 声音；stdin = 字从标准输入给。不花钱、不出门
  对象里还能写 voice、format（mp3 / wav / opus，看那一家收不收）、instructions、speed、url、timeout。
  Hermes 默认的免费 edge-tts 不接：那是微软 Edge「大声朗读」的非公开接口（WebSocket + 会变的 Sec-MS-GEC 令牌），要多装 edge-tts 这个包、
  没有给服务器用的条款、微软一改就坏；要免费的用本机的 piper / espeak-ng（local/<命令>）。
speak（主人回合；配了 engine.use.tts 才有）：一段字 → 一个音频文件，存成这个对话的附件（files.save_generated：和上传的同一个目录、同一张表），
  这一轮回完挂到回复下面（app 的附件预览现在就能放）。只在这台服务器上多一个文件、花一点钱（每次记一行），不往外发东西：算 undo。
「用语音回」（按对话；只做了接口，app 里的开关以后接）：set_voice_reply(thread, True) 以后，这个对话每次回完（files.after_reply）在后台把
  回复的字合成一条语音、挂到那条回复上。没配 engine.use.tts：什么都不做。
给渠道（第 6 批）：synthesize(text, …) → Speech(data, mime, ext, seconds, provider, model)；speech_file(text, …) 另外存成一个文件（Speech.path）。
花费：每次记一行 engine_media_usage（purpose tts；按字的照字数、按 token 的照接口回的；本机的记 0；价目里没有的记 null）。
没人在看的那几轮、后台任务的子 Agent：不给 speak。
参考（只读）：Hermes tools/tts_tool.py、tts_tool_openai.py、tts_tool_providers.py（各家的请求、默认的声音、Gemini 的 PCM 包成 WAV）、
  tts_command_provider.py（本机命令的占位符）；OpenClaw docs/tools/tts/api.md。只借设计。
"""
from __future__ import annotations

import asyncio
import base64
import io
import re
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path

from config import settings
from i18n import L

from . import media
from .loop import Tool, ToolCtx, ToolFailure

NAME = "speak"
UNDO = "undo"
TEXT_MAX = 4000              # 一次最多念多少字（OpenAI 4096、Deepgram 2000 字以内分段的事这里不做：太长的让模型拆）
TIMEOUT = 180.0
FORMATS = {"mp3": "audio/mpeg", "wav": "audio/wav", "opus": "audio/ogg", "aac": "audio/aac", "flac": "audio/flac"}
DEFAULT_VOICE = {"openai": "alloy", "google": "Kore"}
VOICE_REPLY_MAX = 1500       # 「用语音回」最多念回复的前多少字


@dataclass
class Speech:
    data: bytes
    mime: str
    ext: str                 # mp3 / wav / opus……（存文件用的扩展名）
    seconds: float | None
    provider: str
    model: str
    path: Path | None = None


def on() -> bool:
    c = media.choice("tts")
    return c is not None and (c.provider != media.LOCAL or media.local_command(c.model) is not None)


def pcm_to_wav(pcm: bytes, rate: int = 24000, channels: int = 1, width: int = 2) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def _fmt(c: media.Choice, want: str | None, allowed: tuple[str, ...], default: str) -> str:
    f = str(want or c.opts.get("format") or default).lower().strip(".")
    return f if f in allowed else default


async def synthesize(text: str, *, voice: str | None = None, format: str | None = None, instructions: str | None = None,
                     conversation: str | None = None, agent: str | None = None) -> Speech:
    """给第 6 批（渠道发语音）和 speak 用：一段字 → Speech（音频的字节、类型、扩展名、多长）。按 engine.use.tts；没配抛 MediaError(config)。
    出错抛 media.MediaError（报错里不带密钥）。每次记一行花费。"""
    c = media.choice("tts")
    if c is None:
        raise media.MediaError("config", L("朗读没配（engine.use.tts）", "Speech isn't set up (engine.use.tts)"))
    text = (text or "").strip()
    if not text:
        raise media.MediaError("bad", L("没有要念的字", "There's no text to read"))
    if len(text) > TEXT_MAX:
        raise media.MediaError("bad", L(f"太长了：一次最多 {TEXT_MAX} 字，分成几段", f"Too long: {TEXT_MAX} characters at a time, split it up"))
    voice = voice or (str(c.opts["voice"]) if c.opts.get("voice") else None) or DEFAULT_VOICE.get(c.provider)
    with media.Meter("tts", c, conversation, agent) as m:
        m.unit, m.units = "char", float(len(text))
        if c.provider == "openai" or c.opts.get("api") == "openai":
            data, ext = await _openai(c, text, voice, format, instructions)
            if c.model == "gpt-4o-mini-tts":
                m.unpriced = True  # 按 token 计价、接口不回 token 数：不猜（记 null）
        elif c.provider == "elevenlabs":
            data, ext = await _elevenlabs(c, text, voice, format)
        elif c.provider == "google":
            data, ext, toks = await _gemini(c, text, voice, instructions)
            m.unit, m.tokens_in, m.tokens_out, m.units = "token", toks[0], toks[1], toks[0] + toks[1]
        elif c.provider == "deepgram":
            data, ext = await _deepgram(c, text, format)
        elif c.provider == media.LOCAL:
            data, ext = await _local(c, text, voice, format)
            m.cost = 0.0
        else:
            raise media.MediaError("config", L(f"{c.provider} 不会朗读（能写的：openai、elevenlabs、google、deepgram、local/<命令>）",
                                               f"{c.provider} can't read text aloud (use openai, elevenlabs, google, deepgram or local/<command>)"))
    secs = await media.duration(data, f"speech.{ext}")
    return Speech(data, FORMATS.get(ext, "application/octet-stream"), ext, secs, c.provider, c.model)


async def speech_file(text: str, *, voice: str | None = None, format: str | None = None, folder: Path | None = None,
                      conversation: str | None = None, agent: str | None = None) -> Speech:
    """synthesize 再存成一个文件（folder 不给 = <data_dir>/engine/speech/）：渠道要的是文件路径和格式。→ Speech（path 填好）。"""
    s = await synthesize(text, voice=voice, format=format, conversation=conversation, agent=agent)
    d = folder or settings.data_dir / "engine" / "speech"
    d.mkdir(parents=True, exist_ok=True)
    import hashlib
    p = d / f"{hashlib.sha256(s.data).hexdigest()[:16]}.{s.ext}"
    await asyncio.to_thread(p.write_bytes, s.data)
    s.path = p
    return s


async def _openai(c: media.Choice, text: str, voice: str | None, want: str | None, instructions: str | None) -> tuple[bytes, str]:
    base, key, key_env = media.endpoint(c)
    if key_env:
        media.need_key(c, key, key_env)
    ext = _fmt(c, want, ("mp3", "wav", "opus", "aac", "flac"), "mp3")
    body = {"model": c.model, "input": text, "voice": voice or "alloy", "response_format": ext,
            **({"instructions": instructions} if instructions and c.model == "gpt-4o-mini-tts" else {}),
            **({"speed": float(c.opts["speed"])} if c.opts.get("speed") else {})}
    data, r = await media.call(c, "POST", f"{base}/audio/speech", key=key, headers={"Authorization": f"Bearer {key}"} if key else {},
                               json_body=body, want="bytes", timeout=float(c.opts.get("timeout") or TIMEOUT))
    _audio(c, data, r)
    return data, ext


async def _elevenlabs(c: media.Choice, text: str, voice: str | None, want: str | None) -> tuple[bytes, str]:
    if not voice:
        raise media.MediaError("config", L("ElevenLabs 要写 voice（声音的 id）", "ElevenLabs needs voice (a voice id)"))
    base, key, key_env = media.endpoint(c)
    media.need_key(c, key, key_env)
    ext = _fmt(c, want, ("mp3",), "mp3")
    data, r = await media.call(c, "POST", f"{base}/text-to-speech/{voice}", key=key, params={"output_format": "mp3_44100_128"},
                               headers={"xi-api-key": key}, json_body={"text": text, "model_id": c.model}, want="bytes",
                               timeout=float(c.opts.get("timeout") or TIMEOUT))
    _audio(c, data, r)
    return data, ext


async def _deepgram(c: media.Choice, text: str, want: str | None) -> tuple[bytes, str]:
    base, key, key_env = media.endpoint(c)
    media.need_key(c, key, key_env)
    ext = _fmt(c, want, ("mp3", "wav", "opus"), "mp3")
    enc = {"mp3": "mp3", "wav": "linear16", "opus": "opus"}[ext]
    params = {"model": c.model, "encoding": enc, **({"container": "wav"} if ext == "wav" else {"container": "ogg"} if ext == "opus" else {})}
    data, r = await media.call(c, "POST", f"{base}/speak", key=key, params=params, headers={"Authorization": f"Token {key}"},
                               json_body={"text": text}, want="bytes", timeout=float(c.opts.get("timeout") or TIMEOUT))
    _audio(c, data, r)
    return data, ext


async def _gemini(c: media.Choice, text: str, voice: str | None, instructions: str | None) -> tuple[bytes, str, tuple[int, int]]:
    say = f"{instructions.strip()}: {text}" if instructions else text
    config = {"responseModalities": ["AUDIO"], "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice or "Kore"}}}}
    got = await media.gemini(c, [{"text": say}], config=config, timeout=float(c.opts.get("timeout") or TIMEOUT))
    part = next((p for p in media.gemini_parts(got) if isinstance(p.get("inlineData") or p.get("inline_data"), dict)), None)
    blob = (part or {}).get("inlineData") or (part or {}).get("inline_data") or {}
    try:
        pcm = base64.b64decode(blob.get("data") or "", validate=True)
    except ValueError:
        pcm = b""
    if not pcm:
        raise media.MediaError("shape", f"{c.provider}: no audio came back")
    mime = str(blob.get("mimeType") or blob.get("mime_type") or "")
    m = re.search(r"rate=(\d+)", mime)
    return pcm_to_wav(pcm, int(m.group(1)) if m else 24000), "wav", media.gemini_usage(got)


async def _local(c: media.Choice, text: str, voice: str | None, want: str | None) -> tuple[bytes, str]:
    exe = media.local_command(c.model)
    if exe is None:
        raise media.MediaError("config", L(f"这台服务器上没有 {c.model}", f"{c.model} isn't installed on this server"))
    args = c.opts.get("args")
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        raise media.MediaError("config", L("本机朗读要写 args（命令的参数，{out} 是写到哪）", "Local speech needs args (the command's arguments; {out} is the output file)"))
    ext = _fmt(c, want, tuple(FORMATS), "wav")
    with tempfile.TemporaryDirectory(prefix="mousse-tts-") as d:
        out, tf = Path(d) / f"out.{ext}", Path(d) / "text.txt"
        tf.write_text(text, encoding="utf-8")
        argv = [exe, *(a.replace("{out}", str(out)).replace("{text_file}", str(tf)).replace("{text}", text).replace("{voice}", voice or "")
                       for a in args)]
        rc, _, err = await media.run_local(argv, timeout=float(c.opts.get("timeout") or TIMEOUT), cwd=d,
                                           stdin=text.encode() if c.opts.get("stdin") else None)
        if rc != 0 or not out.is_file() or not out.stat().st_size:
            raise media.MediaError("bad", f"{c.model} failed: {err[-300:]}")
        return out.read_bytes(), ext


def _audio(c: media.Choice, data: bytes, r) -> None:
    ctype = (r.headers.get("content-type") or "").lower()
    if not data or "json" in ctype or ctype.startswith("text/"):
        raise media.MediaError("shape", f"{c.provider}: the answer isn't audio ({ctype or 'empty'})")


# —— 「用语音回」（按对话的开关：只做了接口）—————————————————————————————————————————————

SCHEMA = "CREATE TABLE IF NOT EXISTS engine_voice_reply (thread TEXT PRIMARY KEY, voice TEXT, at TEXT NOT NULL)"
_tasks: set[asyncio.Task] = set()


def _with_db(fn):
    """在 mousse.db 里做一件事（拿着 store 的锁；表第一次用时建）。"""
    from . import store
    with store._lock:  # noqa: SLF001
        conn = media._db()  # noqa: SLF001 — 和花费同一个库
        try:
            conn.execute(SCHEMA)
            return fn(conn)
        finally:
            conn.close()


def voice_reply(thread: str) -> dict | None:
    """这个对话开着「用语音回」吗：开着 → {"voice": 声音 | None}；关着 → None。"""
    try:
        r = _with_db(lambda conn: conn.execute("SELECT voice FROM engine_voice_reply WHERE thread=?", (thread,)).fetchone())
    except Exception:  # noqa: BLE001
        return None
    return {"voice": r["voice"]} if r else None


def set_voice_reply(thread: str, on: bool, voice: str | None = None) -> None:
    """开 / 关这个对话的「用语音回」（app 里的开关以后接这里）。"""
    from . import store

    def go(conn) -> None:
        with conn:
            if on:
                conn.execute("INSERT OR REPLACE INTO engine_voice_reply(thread, voice, at) VALUES(?,?,?)", (thread, voice, store.now_iso()))
            else:
                conn.execute("DELETE FROM engine_voice_reply WHERE thread=?", (thread,))
    _with_db(go)


def spoken(text: str) -> str:
    """回复的 Markdown → 念出来的字：去掉代码块、链接只留字、表格和符号去掉，最多 VOICE_REPLY_MAX 字。"""
    t = re.sub(r"```.*?```", " ", text or "", flags=re.S)
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", t)
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"`([^`]*)`", r"\1", t)
    t = re.sub(r"^\s*[|>#*\-+]+\s*", "", t, flags=re.M)
    t = re.sub(r"[*_~|]+", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:VOICE_REPLY_MAX]


async def after_reply(thread: str, message_id: int, text: str) -> dict | None:
    """开了「用语音回」的对话：把这条回复念成一段语音，存成附件挂到这条回复上。→ 附件的摘要；没开、没配、没字 → None。出错只打一行日志。"""
    want = voice_reply(thread)
    if want is None or not on():
        return None
    say = spoken(text)
    if not say:
        return None
    try:
        s = await synthesize(say, voice=want.get("voice"), conversation=thread)
    except media.MediaError as e:
        print(f"[engine] 语音回复没合成：{str(e)[:160]}")
        return None
    import files
    note = L(f"语音{f'，{s.seconds:.0f} 秒' if s.seconds else ''}", f"Voice{f', {s.seconds:.0f} s' if s.seconds else ''}")
    return await asyncio.to_thread(files.save_generated, thread, s.data, f"reply-{message_id}.{s.ext}", s.mime, kind="audio", text=say,
                                   note=note, message_id=message_id)


def after_reply_soon(thread: str, message_id: int, text: str, status: str) -> None:
    """files.after_reply 调（回复入库以后）：开着、配了、这条成了才在后台合成（不拖着「回完了」）。"""
    if status != "ok" or not (text or "").strip() or not on() or voice_reply(thread) is None:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    t = loop.create_task(after_reply(thread, message_id, text))
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)


# —— speak ————————————————————————————————————————————————————————————————————————

def tool(agent: str) -> Tool | None:
    if not on():
        return None
    from .handkit import levelled
    c0 = media.choice("tts")

    async def go(args: dict, ctx: ToolCtx) -> str:
        text = str(args.get("text") or "").strip()
        if not text:
            raise ToolFailure(L("要写 text（念什么）", "Give text (what to read aloud)"))
        try:
            s = await synthesize(text, voice=str(args.get("voice") or "").strip() or None, format=str(args.get("format") or "").strip() or None,
                                 instructions=" ".join(str(args.get("instructions") or "").split())[:300] or None,
                                 conversation=ctx.conversation, agent=ctx.agent)
        except media.MediaError as e:
            raise ToolFailure(L(f"没念成（{c0.ref if c0 else 'tts'}）：{e}", f"Speech failed ({c0.ref if c0 else 'tts'}): {e}")) from None
        import files
        name = re.sub(r"[^\w-]+", "-", str(args.get("name") or "speech").strip())[:40].strip("-") or "speech"
        note = L(f"朗读{f'，{s.seconds:.0f} 秒' if s.seconds else ''}", f"Read aloud{f', {s.seconds:.0f} s' if s.seconds else ''}")
        a = await asyncio.to_thread(files.save_generated, ctx.conversation, s.data, f"{name}.{s.ext}", s.mime, kind="audio", text=text, note=note)
        how = f"{s.seconds:.0f} s, " if s.seconds else ""
        return (f"Saved {a['name']} ({how}{s.ext}, {media.human_bytes(len(s.data))}, {s.provider}/{s.model}) as an attachment of this conversation "
                f"(id {a['id']}, on the server at {a['path']}); it shows under your reply, where the user can play it. Don't paste the text again.")

    return Tool(
        name=NAME, kinds=("owner",), run=go, timeout=TIMEOUT + 60, max_output=4000,
        title=lambda a: " ".join(str(a.get("text") or "").split())[:60],
        classify=lambda a: levelled(NAME, (UNDO, L("只在这个对话里多一个音频附件，不往外发；按量花一点钱，记在用量里",
                                                   "Only adds an audio attachment to this conversation and sends nothing out; costs a little and is logged"))),
        description=L(
            f"把一段字念出来（{c0.ref if c0 else ''}），存成这个对话的音频附件：回复下面会有一个能放的语音。一次最多 {TEXT_MAX} 字，长的分几次。"
            "voice 换声音，format 换格式（mp3、wav、opus……看服务商），instructions 写语气（只有 gpt-4o-mini-tts 认）。",
            f"Read text aloud ({c0.ref if c0 else ''}) and save it as an audio attachment of this conversation: a playable clip appears under your "
            f"reply. Up to {TEXT_MAX} characters at a time; split longer text. voice picks the voice, format the format (mp3, wav, opus… as the "
            "provider allows), instructions the tone (gpt-4o-mini-tts only)."),
        schema={"type": "object", "required": ["text"], "properties": {
            "text": {"type": "string", "description": L("要念的字", "The text to read aloud")},
            "voice": {"type": "string", "description": L("声音（不写用设置里的）", "Voice (empty = the configured one)")},
            "format": {"type": "string", "description": L("mp3 / wav / opus……", "mp3 / wav / opus…")},
            "instructions": {"type": "string", "description": L("语气，比如「平静、慢一点」", "Tone, like \"calm and a bit slow\"")},
            "name": {"type": "string", "description": L("文件名（不带扩展名）", "File name (without extension)")}}})
