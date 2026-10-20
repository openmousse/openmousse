"""第 3 批第 7 项：看视频（engine/video.py）。不配 engine.use.video：没有这个工具、上传的视频照旧只存盘；配了 Gemini：视频放进 inline_data，
大的先传 Files API、等处理好、问完删掉；别的会看图的型号：ffmpeg 均匀抽帧交给它；start / end 只看一段、太长的只看开头那一段（ffmpeg）；
记花费；答的话包成资料；用户发来的视频开了 describe_video 才先看一遍；没人在看的那几轮、子 Agent 不给。服务商全是假的（evals/fakemedia.py、
evals/fakemodel.py）；要真 ffmpeg 的几条没有 ffmpeg 就跳过。"""
from __future__ import annotations

import asyncio
import base64
import json
import shutil
import subprocess

import pytest

import engine
from engine import media, provider_openai, providers, store, tools, video
from engine.loop import ToolFailure
from evals.fakemedia import FakeMedia
from evals.fakemodel import FakeModelServer, Reply

KEY = "sk-fake-video-0123456789abcdef"
FAKE_MP4 = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + b"\x00" * 200
FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def run(coro):
    return asyncio.run(coro)


def ctx() -> engine.ToolCtx:
    return engine.ToolCtx(turn="t", kind="owner", agent="main", conversation="t-video", call_id="c1", control=engine.Control())


def real_mp4(path, seconds: int = 6) -> bytes:
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size=160x120:rate=10",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True)
    return path.read_bytes()


@pytest.fixture
def fx(request):
    f = FakeMedia(key=KEY).start()
    request.addfinalizer(f.stop)
    return f


@pytest.fixture
def fm(request, monkeypatch):
    from engine import loop
    f = FakeModelServer([]).start()
    request.addfinalizer(f.stop)
    monkeypatch.setattr(loop, "MODEL_RETRIES", (0, 0))
    monkeypatch.setattr(provider_openai, "RETRY_DELAYS", (0, 0))
    monkeypatch.setattr(providers, "_no_images", {})
    return f


@pytest.fixture
def env(tmp_path, monkeypatch, fx):
    from config import settings
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setattr(settings, "workspace", ws)
    monkeypatch.setenv("GEMINI_API_KEY", KEY)
    (ws / "clip.mp4").write_bytes(FAKE_MP4)
    return ws


@pytest.fixture
def conf(server_json, fx, fm):
    def write(video_use=None, **extra) -> None:
        eng = {"model": "deepseek/deepseek-flash", "providers": {"google": {"url": fx.url + "/v1beta/openai"},
                                                                 "fake": {"api": "openai", "url": fm.openai_url, "key_env": ""}}, **extra}
        if video_use is not None:
            eng["use"] = {"video": video_use}
        server_json({"engine": eng})
    return write


def offered() -> engine.Tool | None:
    return next((t for t in tools.for_agent("main") if t.name == "video_analyze"), None)


def last_row() -> dict:
    with store.read() as conn:
        return dict(conn.execute("SELECT * FROM engine_media_usage ORDER BY id DESC LIMIT 1").fetchone())


def test_off_by_default(env, conf):
    import files
    conf()
    assert offered() is None and not video.on() and not files._engine_video()
    conf("google/gemini-2.5-flash-lite")
    assert offered() is not None and not files._engine_video()  # 上传的视频要另外开 describe_video
    conf("google/gemini-2.5-flash-lite", media={"describe_video": True})
    assert files._engine_video()


def test_gemini_watches_inline_or_through_the_files_api(env, conf, fx, monkeypatch):
    conf("google/gemini-2.5-flash-lite")
    t = offered()
    assert t.level({"path": "clip.mp4"})[0] == "read"
    out = run(t.run({"path": "clip.mp4", "question": "what happens at the start?"}, ctx()))
    assert "FAKE-VIDEO" in out and "what happens at the start?" in out and "<untrusted_tool_result" in out
    parts = fx.of(":generateContent")[-1].json["contents"][0]["parts"]
    assert parts[0]["inline_data"]["mime_type"] == "video/mp4" and base64.b64decode(parts[0]["inline_data"]["data"]) == FAKE_MP4
    assert "text" in parts[1]  # 视频在前、问题在后
    u = last_row()
    assert (u["purpose"], u["provider"], u["ok"]) == ("video", "google", 1) and u["cost"] == pytest.approx((1200 * 0.10 + 80 * 0.40) / 1e6)
    monkeypatch.setattr(media, "INLINE_MAX", 100)
    run(t.run({"path": "clip.mp4"}, ctx()))
    part = fx.of(":generateContent")[-1].json["contents"][0]["parts"][0]
    assert part["file_data"]["mime_type"] == "video/mp4" and fx.uploads["f1"]["bytes"] == len(FAKE_MP4)
    assert [r.method for r in fx.of("/v1beta/files/f1")][-1] == "DELETE"
    (env / "notes.txt").write_text("not a video")
    with pytest.raises(ToolFailure, match="isn't a video"):
        run(t.run({"path": "notes.txt"}, ctx()))
    with pytest.raises(ToolFailure, match="mm:ss"):
        run(t.run({"path": "clip.mp4", "start": "soon"}, ctx()))


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg isn't installed")
def test_other_vision_models_get_frames(env, conf, fm):
    real_mp4(env / "real.mp4")
    conf("fake/vision-1")
    fm.push(Reply(text="A test pattern with moving colour bars, the same throughout."))
    out = run(offered().run({"path": "real.mp4", "question": "what is shown?", "frames": 4}, ctx()))
    assert "watched as frames, no sound" in out and "moving colour bars" in out
    content = fm.of("openai")[-1]["body"]["messages"][-1]["content"]
    pics = [p for p in content if p["type"] == "image_url"]
    words = " ".join(p["text"] for p in content if p["type"] == "text")
    assert len(pics) == 4 and "4 frames taken evenly from the video" in words and "There is no sound" in words


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg isn't installed")
def test_trimming_and_the_length_limit(env, conf, fx, monkeypatch):
    real_mp4(env / "long.mp4", seconds=6)
    conf("google/gemini-2.5-flash-lite")
    run(offered().run({"path": "long.mp4", "start": "0:02", "end": "4"}, ctx()))
    sent = base64.b64decode(fx.of(":generateContent")[-1].json["contents"][0]["parts"][0]["inline_data"]["data"])
    assert sent != (env / "long.mp4").read_bytes() and 1.5 < run(media.duration(sent, "x.mp4")) < 2.6  # 只发了 2–4 秒那一段
    monkeypatch.setattr(video, "MAX_SECONDS", 3)
    out = run(offered().run({"path": "long.mp4"}, ctx()))
    assert "Only 0:00–0:03 of 0:06 was watched" in out
    with pytest.raises(ToolFailure, match="only 0:06 long"):
        run(offered().run({"path": "long.mp4", "start": "10"}, ctx()))


def test_uploaded_videos_are_described_only_when_switched_on(env, conf, fx):
    conf("google/gemini-2.5-flash-lite", media={"describe_video": True})
    text, note, status = run(video.describe_upload(env / "clip.mp4", "video/mp4"))
    assert status == "ok" and "FAKE-VIDEO" in text and "<untrusted_tool_result" in text and "gemini-2.5-flash-lite" in note
    fx.fail(500, 1, "down")
    text, note, status = run(video.describe_upload(env / "clip.mp4", "video/mp4"))
    assert (text, status) == ("", "warn") and "Couldn't watch it" in note


def test_not_for_unattended_turns_or_children_and_listed_in_setup(env, conf):
    from engine import owner, setup_api, tasks, unattended
    assert "video_analyze" in unattended.NO_TOOLS and "video_analyze" in tasks.CHILD_NEVER
    conf("google/gemini-2.5-flash-lite")
    assert "video_analyze watches a video" in owner.rules("main", tools.for_agent("main"))
    rows = {r["id"]: r for r in setup_api.media_rows(True)}
    assert rows["video"]["current"] == "google/gemini-2.5-flash-lite" and rows["video"]["providers"][0] == "google"
    assert json.dumps(rows["video"]["providers"]).count("google") == 1
