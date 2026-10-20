"""第 3 批第 4 项：转写（engine/stt.py）。不配 engine.use.stt：没有 transcribe 工具，附件和语音输入照旧走 files.py 的 OpenAI 那两个型号；
配了：选哪家（OpenAI、Groq、Gemini 含 Files API、Deepgram、ElevenLabs、本机 whisper-cli）、记花费（按秒、按 token、本机 0、不知道多长记 null）、
报错里抹掉密钥、没人在看的那几轮和子 Agent 不给、给渠道的 transcribe_file、files.transcribe 配了才改道。服务商全是假的（evals/fakemedia.py）。"""
from __future__ import annotations

import asyncio
import io
import os
import stat
import wave

import pytest

import engine
from engine import media, store, stt, tools
from engine.loop import ToolFailure
from evals.fakemedia import DURATION, FakeMedia

KEY = "sk-fake-stt-0123456789abcdef"
ENVS = ("OPENAI_API_KEY", "GROQ_API_KEY", "GEMINI_API_KEY", "DEEPGRAM_API_KEY", "ELEVENLABS_API_KEY")


def run(coro):
    return asyncio.run(coro)


def ctx(agent: str = "main", conversation: str = "main") -> engine.ToolCtx:
    return engine.ToolCtx(turn="t", kind="owner", agent=agent, conversation=conversation, call_id="c1", control=engine.Control())


def wav(seconds: float = 2.0, rate: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(seconds * rate))
    return buf.getvalue()


@pytest.fixture
def fx(request):
    f = FakeMedia(key=KEY).start()
    request.addfinalizer(f.stop)
    return f


@pytest.fixture
def ws(tmp_path, monkeypatch, fx):
    from config import settings
    w = tmp_path / "ws"
    w.mkdir()
    monkeypatch.setattr(settings, "workspace", w)
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(settings, "uploads", tmp_path / "uploads")
    for k in ENVS:
        monkeypatch.setenv(k, KEY)
    (w / "memo.wav").write_bytes(wav(3.0))
    return w


@pytest.fixture
def conf(server_json, fx):
    def write(stt_use=None, **extra) -> None:
        eng = {"model": "fake/x", "providers": {"fake": {"api": "openai", "url": fx.url + "/v1", "key_env": ""},
                                                "openai": {"url": fx.url + "/v1"}, "groq": {"url": fx.url + "/openai/v1"},
                                                "google": {"url": fx.url + "/v1beta/openai"}, "deepgram": {"url": fx.url + "/v1"},
                                                "elevenlabs": {"url": fx.url + "/v1"}}, **extra}
        if stt_use is not None:
            eng["use"] = {"stt": stt_use}
        server_json({"engine": eng})
    return write


def offered(agent: str = "main") -> engine.Tool | None:
    return next((t for t in tools.for_agent(agent) if t.name == "transcribe"), None)


def last_row() -> dict:
    with store.read() as conn:
        return dict(conn.execute("SELECT * FROM engine_media_usage ORDER BY id DESC LIMIT 1").fetchone())


def test_off_by_default_and_files_keeps_its_two_openai_models(ws, conf, fx, monkeypatch):
    import files
    conf()
    assert offered() is None and not stt.on()
    monkeypatch.setattr(files, "TRANSCRIBE_URL", fx.url + "/v1/audio/transcriptions")
    text = files.transcribe(ws / "memo.wav", "audio/wav")
    assert text.startswith("FAKE-TRANSCRIPT model=gpt-4o-transcribe")  # 照旧：gpt-4o-transcribe 先
    assert fx.requests[-1].extra["model"] == "gpt-4o-transcribe" and not media.choice("stt")
    t = run(stt.transcribe_file(ws / "memo.wav"))  # 给渠道的：没配也能用，走的是同一条
    assert t.text.startswith("FAKE-TRANSCRIPT") and (t.provider, t.model) == ("openai", "gpt-4o-transcribe")


def test_each_provider_and_its_cost(ws, conf, fx):
    cases = [("openai/gpt-4o-transcribe", "FAKE-TRANSCRIPT model=gpt-4o-transcribe", "second", 3.0, 3.0 * 0.006 / 60),  # 时长：WAV 文件头
             ("groq/whisper-large-v3-turbo", "FAKE-TRANSCRIPT model=whisper-large-v3-turbo", "second", DURATION, DURATION * 0.04 / 3600),
             ("deepgram/nova-3", "FAKE-DEEPGRAM model=nova-3", "second", DURATION, DURATION * 0.0043 / 60),
             ("elevenlabs/scribe_v1", "FAKE-SCRIBE model=scribe_v1", "second", DURATION, None),  # 价目表里没有：null，不猜
             ("google/gemini-2.5-flash-lite", "FAKE-AUDIO", "token", None, (40 * 0.10 + 1160 * 0.30 + 80 * 0.40) / 1e6)]  # 音频按音频的价
    for ref, said, unit, secs, cost in cases:
        conf(ref)
        t = offered()
        assert t is not None and ref in t.description and t.level({"path": "memo.wav"})[0] == "read"
        out = run(t.run({"path": "memo.wav", "language": "en", "prompt": "Quillfeather"}, ctx()))
        assert said in out and "<untrusted_tool_result" in out and ref in out
        row = last_row()
        assert (row["purpose"], f"{row['provider']}/{row['model']}", row["unit"], row["ok"]) == ("stt", ref, unit, 1)
        if secs is not None:
            assert row["units"] == pytest.approx(secs)
        assert (row["cost"] is None) if cost is None else row["cost"] == pytest.approx(cost, rel=1e-3)
    gem = fx.of(":generateContent")[-1].json["contents"][0]["parts"]
    assert gem[1]["inline_data"]["mime_type"] == "audio/x-wav" or gem[1]["inline_data"]["mime_type"].startswith("audio/")
    assert "The language is en" in gem[0]["text"] and "Quillfeather" in gem[0]["text"]
    sent = fx.of("/openai/v1/audio/transcriptions")[-1].extra
    assert sent["language"] == "en" and sent["prompt"] == "Quillfeather" and sent["response_format"] == "verbose_json"
    assert fx.of("/v1/listen")[-1].query["model"] == ["nova-3"] and fx.of("/v1/listen")[-1].headers["authorization"] == f"Token {KEY}"
    assert fx.of("/v1/speech-to-text")[-1].headers["xi-api-key"] == KEY


def test_big_audio_goes_through_the_gemini_files_api(ws, conf, fx, monkeypatch):
    monkeypatch.setattr(media, "INLINE_MAX", 1000)
    conf("google/gemini-2.5-flash-lite")
    out = run(offered().run({"path": "memo.wav"}, ctx()))
    assert "FAKE-AUDIO" in out  # 传上去的文件按 file_data 交给它（假的服务商照样认出是音频）
    part = fx.of(":generateContent")[-1].json["contents"][0]["parts"][1]
    assert part["file_data"]["file_uri"].endswith("/v1beta/files/f1") and fx.uploads["f1"]["bytes"] == len(wav(3.0))
    assert [r.method for r in fx.of("/v1beta/files/f1")] == ["GET", "GET", "DELETE"]  # 等它处理好（假的第一次说还在处理）、用完删掉


def test_the_key_never_shows_in_errors(ws, conf, fx, monkeypatch):
    wrong = "sk-wrong-stt-9876543210zyxwvu"
    monkeypatch.setenv("GROQ_API_KEY", wrong)
    conf("groq/whisper-large-v3")
    with pytest.raises(ToolFailure) as e:
        run(offered().run({"path": "memo.wav"}, ctx()))
    assert wrong not in str(e.value) and "HTTP 401" in str(e.value) and fx.requests[-1].auth == wrong
    row = last_row()
    assert row["ok"] == 0 and wrong not in (row["error"] or "")
    monkeypatch.delenv("GROQ_API_KEY")
    with pytest.raises(ToolFailure, match="No key for groq"):
        run(offered().run({"path": "memo.wav"}, ctx()))


def test_local_whisper_cli_when_installed(ws, conf, tmp_path, monkeypatch):
    conf({"model": "local/whisper-cli", "model_path": str(tmp_path / "ggml-fake.bin")})
    assert offered() is None  # 命令找不到：没有这个工具
    bindir = tmp_path / "bin"
    bindir.mkdir()
    script = bindir / "whisper-cli"
    script.write_text('#!/bin/sh\nwhile [ $# -gt 0 ]; do case "$1" in -of) out="$2"; shift;; -l) lang="$2"; shift;; esac; shift; done\n'
                      'printf "local words in %s" "$lang" > "$out.txt"\n')
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    t = offered()
    assert t is not None
    with pytest.raises(ToolFailure, match="model_path"):
        run(t.run({"path": "memo.wav"}, ctx()))
    (tmp_path / "ggml-fake.bin").write_bytes(b"model")
    out = run(t.run({"path": "memo.wav", "language": "zh"}, ctx()))
    assert "local words in zh" in out
    row = last_row()
    assert (row["provider"], row["model"], row["cost"]) == ("local", "whisper-cli", 0.0) and row["units"] == pytest.approx(3.0)


def test_files_and_channels_use_the_choice_when_set(ws, conf, fx):
    import files
    conf("deepgram/nova-3")
    text = files.transcribe(ws / "memo.wav", "audio/wav")  # 附件上传、语音输入：改走配好的那一家
    assert text.startswith("FAKE-DEEPGRAM")
    t = run(stt.transcribe_file(ws / "memo.wav", language="de", conversation="main"))
    assert (t.provider, t.model, t.seconds) == ("deepgram", "nova-3", DURATION) and fx.of("/v1/listen")[-1].query["language"] == ["de"]
    with pytest.raises(media.MediaError):
        run(stt.transcribe_file(ws / "missing.wav"))
    fx.fail(503, 1, "busy")
    with pytest.raises(RuntimeError, match="Transcription failed"):
        files.transcribe(ws / "memo.wav", "audio/wav")


def test_not_for_unattended_turns_or_children_and_listed_in_setup(ws, conf):
    from engine import owner, setup_api, tasks, unattended
    assert "transcribe" in unattended.NO_TOOLS and "transcribe" in tasks.CHILD_NEVER
    conf("groq/whisper-large-v3-turbo")
    assert "transcribe turns a recording" in owner.rules("main", tools.for_agent("main"))
    rows = {r["id"]: r for r in setup_api.media_rows(True)}
    assert rows["stt"]["current"] == "groq/whisper-large-v3-turbo" and {"deepgram", "elevenlabs", "local"} <= set(rows["stt"]["providers"])
    assert offered(agent="errand") is None  # 只在沙箱里的 Agent 没有


def test_meter_does_not_record_a_cancelled_call(ws, conf):
    c = media.Choice("stt", "groq", "whisper-large-v3")
    with store.read() as conn:
        before = conn.execute("SELECT COUNT(*) FROM engine_media_usage").fetchone()[0] if conn.execute(
            "SELECT name FROM sqlite_master WHERE name='engine_media_usage'").fetchone() else 0
    with pytest.raises(asyncio.CancelledError):
        with media.Meter("stt", c):
            raise asyncio.CancelledError()
    with store.read() as conn:
        after = conn.execute("SELECT COUNT(*) FROM engine_media_usage").fetchone()[0] if conn.execute(
            "SELECT name FROM sqlite_master WHERE name='engine_media_usage'").fetchone() else 0
    assert after == before
