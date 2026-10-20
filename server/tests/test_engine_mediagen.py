"""第 3 批第 14 项：生成视频、音乐（engine/mediagen.py）。不配 engine.use.video_gen / music：没有这两个工具；配了：Gemini Omni Flash 和 Lyria
（Interactions API）、fal 的队列、ElevenLabs Music；存成对话附件；记花费；没马上好就转后台、做好了往对话补一句（先交那一句、再存附件）；
同一件还在做时再调回它的状态；video_generate 每次先交卡（写着估计的钱），同意了不等、直接后台；报错不带密钥；点了停、重启时收掉。
服务商全是假的（evals/fakemedia.py），往对话补一句的 host_process.deliver 换成记下来。数据都是编的。"""
from __future__ import annotations

import asyncio
import json
import time

import pytest

import engine
from engine import host_process, mediagen, setup_api, store, tasks, tools, unattended
from engine.loop import ToolFailure
from evals.fakemedia import FAKE_MP3, FAKE_MP4, LYRICS, OMNI_TOKENS, FakeMedia, png

KEY = "sk-fake-mediagen-0123456789abcdef"
ENVS = ("GEMINI_API_KEY", "FAL_KEY", "ELEVENLABS_API_KEY")
VIDEO, MUSIC = mediagen.VIDEO, mediagen.MUSIC


def run(coro):
    return asyncio.run(coro)


def ctx(conversation: str = "t-gen") -> engine.ToolCtx:
    return engine.ToolCtx(turn="t", kind="owner", agent="main", conversation=conversation, call_id="c1", control=engine.Control(), origin="user")


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
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setattr(settings, "workspace", ws)
    monkeypatch.setattr(files, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(mediagen, "POLL", 0.01)
    monkeypatch.setattr(mediagen, "FILE_POLL", 0.01)
    for k in ENVS:
        monkeypatch.setenv(k, KEY)
    mediagen._jobs.clear()
    yield ws
    mediagen._jobs.clear()


@pytest.fixture
def conf(server_json, fx):
    def write(video=None, music=None, **extra) -> None:
        eng = {"model": "deepseek/deepseek-flash", "providers": {"google": {"url": fx.url + "/v1beta/openai"}, "fal": {"url": fx.url + "/queue"},
                                                                 "elevenlabs": {"url": fx.url + "/v1"}}, **extra}
        eng["use"] = {k: v for k, v in (("video_gen", video), ("music", music)) if v is not None}
        server_json({"claw": {"kind": "builtin"}, "engine": eng})
    return write


@pytest.fixture
def told(monkeypatch):
    """host_process.deliver / note 换成记下来：(对话, 灰字, 正文, 那时这个对话有几个附件)。"""
    got: list[tuple] = []

    async def deliver(thread, line, text):
        got.append((thread, line, text, len(attachments(thread))))

    monkeypatch.setattr(host_process, "deliver", deliver)
    monkeypatch.setattr(host_process, "note", lambda thread, line, said: got.append((thread, line, said, None)))
    return got


def tool(name: str) -> engine.Tool | None:
    return next((t for t in tools.for_agent("main") if t.name == name), None)


def attachments(thread: str) -> list[dict]:
    import files
    with files._lock, files.adb() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM attachments WHERE thread=? ORDER BY created_at", (thread,))]


def last_rows(n: int = 1) -> list[dict]:
    with store.read() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM engine_media_usage ORDER BY id DESC LIMIT ?", (n,))][::-1]


def slow(fx: FakeMedia, seconds: float):
    """Interactions API 慢一点回（转后台、点了停、重启那几条用）。"""
    def go(rec):
        time.sleep(seconds)
        return fx.interaction(rec)
    fx.on(r"^/v1beta/interactions$", go, method="POST")


def test_off_by_default_classified_and_listed(env, conf):
    conf()
    assert tool(VIDEO) is None and tool(MUSIC) is None
    conf(video="google/gemini-omni-1.1-flash", music="google/lyria-3-clip-preview")
    v, m = tool(VIDEO), tool(MUSIC)
    level, why = v.level({"prompt": "x"})
    assert level == "ask" and "$0.30" in why and v.level({"action": "status"})[0] == "read" and m.level({"prompt": "x"})[0] == "undo"
    assert v.kinds == ("owner",) and m.kinds == ("owner",)
    conf(video="google/gemini-omni-1.1-flash", tools={"levels": {"video_generate": "auto", "music_generate": "ask"}})
    assert tool(VIDEO).level({"prompt": "x"})[0] == "undo"
    for n in (VIDEO, MUSIC):
        assert n in unattended.NO_TOOLS and n in tasks.CHILD_NEVER
    rows = {r["id"]: r for r in setup_api.media_rows(True)}
    assert rows["video_gen"]["current"] == "google/gemini-omni-1.1-flash" and rows["video_gen"]["providers"] == ["google", "fal"]
    assert rows["music"]["providers"] == ["google", "elevenlabs", "fal"]


def test_omni_video_comes_back_in_the_turn(env, conf, fx):
    conf(video="google/gemini-omni-1.1-flash")
    out = run(tool(VIDEO).run({"prompt": "A fictional red kite over a quiet harbour", "aspect_ratio": "9:16", "resolution": "720p", "name": "kite"},
                              ctx()))
    assert out.startswith("[Video ready] vg_") and "kite.mp4" in out and "shows under your reply" in out
    body = fx.of(r"^/v1beta/interactions$")[-1].json
    assert body["model"] == "gemini-omni-1.1-flash" and body["input"] == "A fictional red kite over a quiet harbour"
    assert body["response_format"] == {"type": "video", "aspect_ratio": "9:16", "resolution": "720p"} and body["store"] is False
    a = attachments("t-gen")[-1]
    assert a["kind"] == "video" and a["status"] == "generated" and a["size"] == len(FAKE_MP4) and a["mime"] == "video/mp4"
    row = last_rows()[0]
    assert row["purpose"] == "video_gen" and row["tokens_out"] == OMNI_TOKENS and row["cost"] == pytest.approx((20 * 1.50 + OMNI_TOKENS * 17.50) / 1e6)
    (env / "start.png").write_bytes(png(12, 8))
    run(tool(VIDEO).run({"prompt": "Start from this fictional sketch", "image": "start.png"}, ctx()))
    parts = fx.of(r"^/v1beta/interactions$")[-1].json["input"]
    assert [p["type"] for p in parts] == ["text", "image"] and parts[1]["mime_type"] == "image/png"
    with pytest.raises(ToolFailure, match="aspect_ratio"):
        run(tool(VIDEO).run({"prompt": "x", "aspect_ratio": "4:3"}, ctx()))


def test_big_videos_can_come_through_the_files_api(env, conf, fx):
    conf(video={"model": "google/gemini-omni-1.1-flash", "delivery": "uri"})
    out = run(tool(VIDEO).run({"prompt": "A fictional tram at night"}, ctx()))
    assert "[Video ready]" in out and fx.of(r"^/v1beta/interactions$")[-1].json["response_format"]["delivery"] == "uri"
    paths = [r.path for r in fx.requests if r.path.startswith("/v1beta/files/")]
    assert paths[-1].endswith(":download") and len(paths) >= 3  # 问到 ACTIVE 再下载
    assert attachments("t-gen")[-1]["size"] == len(FAKE_MP4)


def test_slow_ones_go_to_the_background_and_come_back_into_the_chat(env, conf, fx, told, monkeypatch):
    conf(video="google/gemini-omni-1.1-flash", tools={"levels": {"video_generate": "auto"}})
    monkeypatch.setitem(mediagen.WAIT, VIDEO, 0.05)
    slow(fx, 0.6)

    base = len(attachments("main"))  # 附件表是测试共用的：看多了几个

    async def go():
        t = tool(VIDEO)
        out = await t.run({"prompt": "A fictional ferry leaving at dawn"}, ctx("main"))
        again = await t.run({"prompt": "A fictional ferry leaving at dawn"}, ctx("main"))
        status = await t.run({"action": "status"}, ctx("main"))
        job = next(iter(mediagen._jobs.values()))
        await asyncio.wait_for(job.done.wait(), 10)
        return out, again, status, job, await t.run({"action": "status"}, ctx("main"))
    out, again, status, job, after = run(go())
    assert out.startswith("Started vg_") and "comes into this chat" in out and "Already working on this" in again and len(mediagen._jobs) == 1
    assert "running" in status and "done" in after and job.status == "done"
    (thread, line, text, had), = told
    assert thread == "main" and line.startswith("Video ready") and "[Video ready]" in text and "This isn't from the user" in text
    assert had == base and len(attachments("main")) == base + 1  # 先交那一句、再存附件（挂在那一轮的回复下面）
    assert "id " in job.text and attachments("main")[-1]["id"] in job.text


def test_fal_queue_for_video_and_music(env, conf, fx):
    conf(video="fal/fal-ai/kling-video/v2/master/text-to-video", music="fal/fal-ai/minimax-music/v2.6")
    out = run(tool(VIDEO).run({"prompt": "A fictional paper boat in the rain", "seconds": 5, "aspect_ratio": "16:9"}, ctx()))
    assert "[Video ready]" in out
    sub = fx.of(r"^/queue/fal-ai/kling-video/v2/master/text-to-video$")[-1]
    assert sub.auth == KEY and sub.headers["authorization"] == f"Key {KEY}"
    assert sub.json == {"prompt": "A fictional paper boat in the rain", "aspect_ratio": "16:9", "duration": "5"}
    assert len(fx.of(r"^/queue/fal-ai/kling-video/requests/req1/status$")) == 3 and fx.of(r"^/queue/fal-ai/kling-video/requests/req1$")
    assert attachments("t-gen")[-1]["kind"] == "video" and last_rows()[0]["cost"] is None  # fal 的价钱没核对：只记次数
    out = run(tool(MUSIC).run({"prompt": "A calm fictional sea shanty", "lyrics": "Row, row, fictional crew", "seconds": 30}, ctx()))
    assert "[Music ready]" in out and attachments("t-gen")[-1]["kind"] == "audio" and attachments("t-gen")[-1]["size"] == len(FAKE_MP3)
    body = fx.of(r"^/queue/fal-ai/minimax-music/v2.6$")[-1].json
    assert body["lyrics"] == "Row, row, fictional crew" and body["duration"] == 30 and body["prompt"] == "A calm fictional sea shanty"


def test_fal_status_urls_on_another_host_are_not_used(env, conf, fx):
    conf(video="fal/fal-ai/kling-video/v2/master/text-to-video")

    def submit(rec):
        status, headers, out = fx.fal_submit(rec)
        return status, headers, {**out, "status_url": "https://elsewhere.example/steal/status", "response_url": "https://elsewhere.example/steal"}
    fx.on(r"^/queue/", submit, method="POST")
    out = run(tool(VIDEO).run({"prompt": "A fictional kite"}, ctx()))
    assert "[Video ready]" in out and fx.of(r"^/queue/fal-ai/kling-video/requests/req1/status$")  # 自己拼的地址：密钥不发给别的主机


def test_music_from_lyria_and_elevenlabs(env, conf, fx):
    conf(music="google/lyria-3-clip-preview")
    out = run(tool(MUSIC).run({"prompt": "Warm fictional lo-fi for a rainy afternoon", "instrumental": True}, ctx()))
    assert "[Music ready]" in out and LYRICS.splitlines()[1] in out and "<untrusted_tool_result" in out  # 歌词包成资料
    body = fx.of(r"^/v1beta/interactions$")[-1].json
    assert body["model"] == "lyria-3-clip-preview" and body["response_format"] == {"type": "audio"} and "Instrumental only" in body["input"]
    row = last_rows()[0]
    assert row["purpose"] == "music" and row["unit"] == "song" and row["cost"] == pytest.approx(0.04)
    conf(music="elevenlabs/music_v1")
    out = run(tool(MUSIC).run({"prompt": "A short fictional jingle", "seconds": 30, "instrumental": True}, ctx()))
    rec = fx.of(r"^/v1/music$")[-1]
    assert rec.json == {"prompt": "A short fictional jingle", "model_id": "music_v1", "music_length_ms": 30000, "force_instrumental": True}
    assert rec.query["output_format"] == ["mp3_44100_128"] and rec.headers["xi-api-key"] == KEY and "[Music ready]" in out
    row = last_rows()[0]
    assert row["unit"] == "second" and row["units"] > 0 and row["cost"] == pytest.approx(row["units"] * 0.15 / 60)


def test_video_asks_first_and_an_approved_one_runs_in_the_background(env, conf, fx, told, monkeypatch):
    import chat
    import inbox
    from engine import providers
    from test_engine import FakeModel
    conf(video="google/gemini-omni-1.1-flash")
    tools.install_hooks()
    monkeypatch.setattr(providers, "stream", FakeModel([{"text": "It's on its way."}] * 4).stream)  # 点了同意以后，结果交回 main 那一轮

    async def go():
        t = tool(VIDEO)
        args = {"prompt": "A fictional lighthouse at dawn", "why": "The user asked for a short clip"}
        out = await tools.gate(t, args, ctx("main"))
        assert isinstance(out, dict) and out["status"] == "approval-pending" and not mediagen._jobs
        with inbox.idb() as conn:
            card = dict(conn.execute("SELECT * FROM inbox WHERE id=?", (out["inbox"],)).fetchone())
        res = await inbox.act(out["inbox"], inbox.ActIn(action="approve"))
        job = next(iter(mediagen._jobs.values()))
        await asyncio.wait_for(job.done.wait(), 10)
        for _ in range(200):  # 交回 main 的那一轮回完再走：没回完的一轮留下来，后面的测试在 main 里发的话会一直排着
            r = chat.RUNS.get("main")
            if r is None or r.done:
                break
            await asyncio.sleep(0.05)
        return card, res, job
    card, res, job = run(go())
    assert card["kind"] == "tool" and card["title"] == "Generate a video: A fictional lighthouse at dawn"
    changes = json.loads(card["changes"]) if isinstance(card["changes"], str) else card["changes"]
    assert any("google/gemini-omni-1.1-flash" in c for c in changes) and any("$0.30" in c for c in changes)
    assert "Started vg_" in json.dumps(res, ensure_ascii=False)  # 同意了不等：直接后台
    assert job.status == "done" and len(told) == 1 and told[0][0] == "main" and "[Video ready]" in told[0][2]


def test_errors_never_carry_the_key(env, conf, fx, told, monkeypatch):
    wrong = "sk-wrong-mediagen-9876543210zyxw"
    monkeypatch.setenv("GEMINI_API_KEY", wrong)
    conf(video="google/gemini-omni-1.1-flash", music="google/lyria-3-clip-preview")
    with pytest.raises(ToolFailure) as e:
        run(tool(MUSIC).run({"prompt": "x"}, ctx()))
    assert "HTTP 400" in str(e.value) and wrong not in str(e.value)
    monkeypatch.setitem(mediagen.WAIT, VIDEO, 0.0)

    async def go():
        from engine import handkit
        tok = handkit._approved.set(True)  # 同意过的：直接后台
        try:
            await tool(VIDEO).run({"prompt": "A fictional bridge"}, ctx("main"))
        finally:
            handkit._approved.reset(tok)
        job = next(j for j in mediagen._jobs.values() if j.tool == VIDEO)
        await asyncio.wait_for(job.done.wait(), 10)
        return job
    job = run(go())
    assert job.status == "failed" and len(told) == 1 and "generation failed" in told[0][2] and wrong not in told[0][2]
    assert last_rows()[0]["ok"] == 0


def test_stop_and_restart_stop_the_job(env, conf, fx, told, monkeypatch):
    conf(video="google/gemini-omni-1.1-flash", tools={"levels": {"video_generate": "auto"}})
    slow(fx, 0.8)

    async def stopped_by_the_user():
        task = asyncio.ensure_future(tool(VIDEO).run({"prompt": "A fictional crane lifting crates"}, ctx()))
        await asyncio.sleep(0.2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        job = next(iter(mediagen._jobs.values()))
        await asyncio.wait_for(job.done.wait(), 5)
        return job
    job = run(stopped_by_the_user())
    assert job.status == "stopped"
    mediagen._jobs.clear()
    monkeypatch.setitem(mediagen.WAIT, VIDEO, 0.05)

    async def restart():
        await tool(VIDEO).run({"prompt": "A fictional night market"}, ctx("main"))
        return await mediagen.shutdown(), next(iter(mediagen._jobs.values()))
    n, job = run(restart())
    assert n == 1 and job.status == "stopped" and told and "server restart" in told[-1][1] and "may already have been paid for" in told[-1][2]
