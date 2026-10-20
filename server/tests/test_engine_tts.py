"""第 3 批第 5 项：朗读（engine/tts.py）。不配 engine.use.tts：没有 speak，回复后什么都不多做；配了：选哪家（OpenAI、ElevenLabs、Gemini 的
PCM 包成 WAV、Deepgram、本机的命令）、记花费（按字、按 token、本机 0、算不出记 null）、报错里抹掉密钥；念好的存成这个对话的附件，这一轮回完挂到
回复上（真的走一轮自己的内核：chat.py → files.after_reply）；「用语音回」按对话开、回完在后台合成一条；给渠道的 synthesize / speech_file；
没人在看的那几轮、子 Agent 不给。服务商全是假的（evals/fakemedia.py），模型是假的（test_engine.FakeModel）。"""
from __future__ import annotations

import asyncio
import json
import os
import stat
import wave

import pytest

import engine
from engine import media, store, tools, tts
from engine.loop import ToolFailure
from evals.fakemedia import FAKE_MP3, PCM_SECONDS, FakeMedia
from test_engine import FakeModel
from test_engine_events import collect, msg_row, until

KEY = "sk-fake-tts-0123456789abcdef"
ENVS = ("OPENAI_API_KEY", "GEMINI_API_KEY", "DEEPGRAM_API_KEY", "ELEVENLABS_API_KEY")


def run(coro):
    return asyncio.run(coro)


def ctx(conversation: str = "t-tts") -> engine.ToolCtx:
    return engine.ToolCtx(turn="t", kind="owner", agent="main", conversation=conversation, call_id="c1", control=engine.Control())


@pytest.fixture
def fx(request):
    f = FakeMedia(key=KEY).start()
    request.addfinalizer(f.stop)
    return f


@pytest.fixture
def env(tmp_path, monkeypatch, fx):
    import files
    from config import settings
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(files, "UPLOAD_DIR", tmp_path / "uploads")
    for k in ENVS:
        monkeypatch.setenv(k, KEY)
    return tmp_path


@pytest.fixture
def conf(server_json, fx):
    def write(tts_use=None, **extra) -> None:
        eng = {"model": "deepseek/deepseek-flash", "providers": {"openai": {"url": fx.url + "/v1"}, "google": {"url": fx.url + "/v1beta/openai"},
                                                                 "deepgram": {"url": fx.url + "/v1"}, "elevenlabs": {"url": fx.url + "/v1"}}, **extra}
        if tts_use is not None:
            eng["use"] = {"tts": tts_use}
        server_json({"claw": {"kind": "builtin"}, "engine": eng})
    return write


def offered() -> engine.Tool | None:
    return next((t for t in tools.for_agent("main") if t.name == "speak"), None)


def last_row() -> dict:
    with store.read() as conn:
        return dict(conn.execute("SELECT * FROM engine_media_usage ORDER BY id DESC LIMIT 1").fetchone())


def attachment(aid: str):
    import files
    with files._lock, files.adb() as conn:
        return conn.execute("SELECT * FROM attachments WHERE id=?", (aid,)).fetchone()


def test_off_by_default(env, conf):
    import files
    conf()
    assert offered() is None and not tts.on()
    tts.set_voice_reply("t-off", True)
    files.after_reply("t-off", "2026-10-19T08:00:00+00:00", 1, "hello there", "ok")  # 没配朗读：开着「用语音回」也什么都不做
    assert tts._tasks == set()
    with pytest.raises(media.MediaError, match="isn't set up"):
        run(tts.synthesize("hi"))


def test_each_provider_and_its_cost(env, conf, fx):
    text = "A short fictional line to read aloud."
    cases = [("openai/tts-1", None, "mp3", len(text) * 15 / 1e6),
             ("openai/gpt-4o-mini-tts", None, "mp3", None),  # 按 token、接口不回 token 数：null
             ({"model": "elevenlabs/eleven_multilingual_v2", "voice": "voice-fake-1"}, None, "mp3", len(text) * 0.08 / 1000),
             ("deepgram/aura-2-thalia-en", None, "mp3", len(text) * 0.030 / 1000),
             ("google/gemini-2.5-flash-preview-tts", None, "wav", (20 * 0.50 + 900 * 10.0) / 1e6)]
    for use, fmt, ext, cost in cases:
        conf(use)
        t = offered()
        assert t is not None and t.level({"text": text})[0] == "undo"
        out = run(t.run({"text": text, "name": "line", **({"format": fmt} if fmt else {})}, ctx()))
        assert f"line.{ext}" in out and "attachment of this conversation" in out
        aid = out.split("(id ")[1].split(",")[0]
        row = attachment(aid)
        assert row["status"] == "generated" and row["kind"] == "audio" and row["message_id"] is None and row["text"] == text
        data = open(row["path"], "rb").read()
        if ext == "mp3":
            assert data == FAKE_MP3
        else:
            with wave.open(row["path"]) as w:  # Gemini 的 PCM 包成了能放的 WAV
                assert w.getframerate() == 24000 and abs(w.getnframes() / 24000 - PCM_SECONDS) < 0.01
        u = last_row()
        ref = use if isinstance(use, str) else use["model"]
        assert (u["purpose"], f"{u['provider']}/{u['model']}", u["ok"]) == ("tts", ref, 1)
        assert (u["cost"] is None) if cost is None else u["cost"] == pytest.approx(cost, rel=1e-3)
    assert fx.of("/v1/audio/speech")[-1].json["voice"] == "alloy" and fx.of("/v1/text-to-speech/voice-fake-1")[-1].headers["xi-api-key"] == KEY
    speak = fx.of(":generateContent")[-1].json["generationConfig"]
    assert speak["responseModalities"] == ["AUDIO"] and speak["speechConfig"]["voiceConfig"]["prebuiltVoiceConfig"]["voiceName"] == "Kore"
    assert fx.of("/v1/speak")[-1].query["model"] == ["aura-2-thalia-en"]


def test_errors_and_limits(env, conf, fx, monkeypatch):
    wrong = "sk-wrong-tts-9876543210zyxwvu"
    monkeypatch.setenv("ELEVENLABS_API_KEY", wrong)
    conf({"model": "elevenlabs/eleven_flash_v2_5", "voice": "v1"})
    with pytest.raises(ToolFailure) as e:
        run(offered().run({"text": "hello"}, ctx()))
    assert wrong not in str(e.value) and "HTTP 401" in str(e.value) and fx.requests[-1].auth == wrong
    conf("elevenlabs/eleven_flash_v2_5")
    with pytest.raises(ToolFailure, match="voice"):
        run(offered().run({"text": "hello"}, ctx()))
    conf("openai/tts-1")
    with pytest.raises(ToolFailure, match="Too long"):
        run(offered().run({"text": "x" * (tts.TEXT_MAX + 1)}, ctx()))
    fx.on(r"^/v1/audio/speech$", lambda rec: (200, {"content-type": "application/json"}, {"oops": True}))
    with pytest.raises(ToolFailure, match="isn't audio"):
        run(offered().run({"text": "hello"}, ctx()))


def test_local_command(env, conf, tmp_path, monkeypatch):
    conf({"model": "local/fake-say", "args": ["--out", "{out}"], "stdin": True, "format": "wav"})
    assert offered() is None  # 命令找不到：没有
    bindir = tmp_path / "bin"
    bindir.mkdir()
    script = bindir / "fake-say"
    script.write_text('#!/bin/sh\n[ "$1" = "--out" ] || exit 2\nwords=$(cat)\nprintf "RIFF-%s" "$words" > "$2"\n')
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    out = run(offered().run({"text": "spoken locally"}, ctx()))
    aid = out.split("(id ")[1].split(",")[0]
    assert open(attachment(aid)["path"], "rb").read() == b"RIFF-spoken locally"  # 字从标准输入进去
    assert (last_row()["provider"], last_row()["cost"]) == ("local", 0.0)


def test_spoken_clip_hangs_on_the_reply_after_a_real_turn(env, conf, server_json, monkeypatch):
    import chat
    from engine import providers
    conf("openai/tts-1")
    f = FakeModel([{"text": "Here it is.", "calls": [("c1", "speak", {"text": "Good morning from the fictional bakery."})]},
                   {"text": "I read it aloud for you."}])
    monkeypatch.setattr(providers, "stream", f.stream)

    async def go():
        r = chat.start_run("t-tts-chat", "read me the greeting", None)
        events = await collect(chat.attach(r))
        return r, events
    r, events = run(go())
    assert r.status == "ok" and dict(events)["done"]["text"].endswith("I read it aloud for you.")
    shown = json.loads(msg_row(chat, r.reply_id)["attachments"])
    assert len(shown) == 1 and shown[0]["kind"] == "audio" and shown[0]["name"].endswith(".mp3") and shown[0]["status"] == "generated"
    assert attachment(shown[0]["id"])["message_id"] == r.reply_id  # app 在回复下面显示、点开能放
    h = chat.history("t-tts-chat")
    assert any(m.get("attachments") for m in h["messages"] if m["role"] == "grava")


def test_voice_replies_per_conversation(env, conf, server_json, fx, monkeypatch):
    import chat
    from engine import providers
    conf("openai/tts-1")
    assert tts.voice_reply("t-voice") is None
    tts.set_voice_reply("t-voice", True, voice="nova")
    assert tts.voice_reply("t-voice") == {"voice": "nova"}
    f = FakeModel([{"text": "**Bold** answer with `code` and a [link](https://example.org/x).\n\n```py\nprint(1)\n```\nDone."}])
    monkeypatch.setattr(providers, "stream", f.stream)

    async def go():
        r = chat.start_run("t-voice", "hello", None)
        await collect(chat.attach(r))
        await until(lambda: bool(msg_row(chat, r.reply_id)["attachments"]), timeout=5)
        return r
    r = run(go())
    shown = json.loads(msg_row(chat, r.reply_id)["attachments"])
    assert shown[0]["kind"] == "audio" and shown[0]["name"] == f"reply-{r.reply_id}.mp3"
    said = fx.of("/v1/audio/speech")[-1].json
    assert said["voice"] == "nova" and said["input"] == "Bold answer with code and a link. Done."  # 念的是去掉 Markdown 的字
    tts.set_voice_reply("t-voice", False)
    assert tts.voice_reply("t-voice") is None
    assert tts.spoken("| a | b |\n|---|---|\n# Title\n> quote") == "a b Title quote"  # 表格的分隔线、标题和引用的记号都不念


def test_channel_functions(env, conf, fx):
    conf("openai/tts-1")
    s = run(tts.synthesize("For a channel.", format="wav"))
    assert s.ext == "wav" and s.mime == "audio/wav" and s.seconds == pytest.approx(1.0) and s.path is None
    f = run(tts.speech_file("For a channel."))
    assert f.path is not None and f.path.read_bytes() == FAKE_MP3 and f.path.suffix == ".mp3" and (f.provider, f.model) == ("openai", "tts-1")


def test_not_for_unattended_turns_or_children_and_listed_in_setup(env, conf):
    from engine import owner, setup_api, tasks, unattended
    assert "speak" in unattended.NO_TOOLS and "speak" in tasks.CHILD_NEVER
    conf("openai/tts-1")
    assert "speak reads text aloud" in owner.rules("main", tools.for_agent("main"))
    rows = {r["id"]: r for r in setup_api.media_rows(True)}
    assert rows["tts"]["current"] == "openai/tts-1" and {"elevenlabs", "deepgram", "local"} <= set(rows["tts"]["providers"])
