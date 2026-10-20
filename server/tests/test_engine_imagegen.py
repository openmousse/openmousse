"""第 3 批第 6 项：生图（engine/imagegen.py）。不配 engine.use.image：没有这个工具；配了：OpenAI（生成、带参考图走 edits）、Gemini、fal、
本机 ComfyUI（工作流里写提示词、等结果、拿图）；每张都进图库（工具结果里带图）、存成这个对话的附件（回完挂到回复上，真走一轮自己的内核）；
记花费（OpenAI 照 token、Gemini 照 token、fal 记 null、本机 0）；报错里抹掉密钥；分类 undo（levels 能改成 ask）；没人在看的那几轮、子 Agent 不给。
服务商全是假的（evals/fakemedia.py），模型是假的（test_engine.FakeModel）。"""
from __future__ import annotations

import asyncio
import json

import pytest

import engine
from engine import images, imagegen, store, tools
from engine.loop import ToolFailure, ToolOutput
from evals.fakemedia import FakeMedia, png
from test_engine import FakeModel
from test_engine_events import collect, msg_row

KEY = "sk-fake-image-0123456789abcdef"
ENVS = ("OPENAI_API_KEY", "GEMINI_API_KEY", "FAL_KEY")


def run(coro):
    return asyncio.run(coro)


def ctx(conversation: str = "t-img") -> engine.ToolCtx:
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
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setattr(settings, "workspace", ws)
    monkeypatch.setattr(files, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(imagegen, "COMFY_POLL", 0.01)
    for k in ENVS:
        monkeypatch.setenv(k, KEY)
    return ws


@pytest.fixture
def conf(server_json, fx):
    def write(image_use=None, **extra) -> None:
        eng = {"model": "deepseek/deepseek-flash", "providers": {"openai": {"url": fx.url + "/v1"}, "google": {"url": fx.url + "/v1beta/openai"},
                                                                 "fal": {"url": fx.url}, "comfyui": {"url": fx.url}}, **extra}
        if image_use is not None:
            eng["use"] = {"image": image_use}
        server_json({"claw": {"kind": "builtin"}, "engine": eng})
    return write


def offered() -> engine.Tool | None:
    return next((t for t in tools.for_agent("main") if t.name == "image_generate"), None)


def last_row() -> dict:
    with store.read() as conn:
        return dict(conn.execute("SELECT * FROM engine_media_usage ORDER BY id DESC LIMIT 1").fetchone())


def attachments(out: str) -> list:
    import files
    ids = [p.split(")")[0] for p in out.split("(id ")[1:]]
    with files._lock, files.adb() as conn:
        return [conn.execute("SELECT * FROM attachments WHERE id=?", (i,)).fetchone() for i in ids]


def test_off_by_default_and_classified_undo(env, conf):
    conf()
    assert offered() is None and not imagegen.on()
    conf("openai/gpt-image-1")
    t = offered()
    assert t is not None and t.level({"prompt": "x"})[0] == "undo"
    conf("openai/gpt-image-1", tools={"levels": {"image_generate": "ask"}})
    assert offered().level({"prompt": "x"})[0] == "ask"  # 要每次都问：levels 改成 ask


def test_openai_generates_and_edits(env, conf, fx):
    conf("openai/gpt-image-1")
    out = run(offered().run({"prompt": "A fictional lighthouse at dawn", "count": 2, "size": "1024x1024", "quality": "low", "name": "lighthouse"}, ctx()))
    assert isinstance(out, ToolOutput) and len(out.images) == 2 and all(b["type"] == "image" for b in out.images)
    rows = attachments(out.text)
    assert [r["name"] for r in rows] == ["lighthouse-1.png", "lighthouse-2.png"] and all(r["status"] == "generated" and r["kind"] == "image" for r in rows)
    assert all(r["message_id"] is None and r["text"] == "A fictional lighthouse at dawn" for r in rows)
    sent = fx.of("/v1/images/generations")[-1].json
    assert sent == {"model": "gpt-image-1", "prompt": "A fictional lighthouse at dawn", "n": 2, "size": "1024x1024", "quality": "low"}
    u = last_row()
    assert (u["purpose"], u["unit"], u["tokens_in"], u["tokens_out"]) == ("image", "token", 50, 1056)
    assert u["cost"] == pytest.approx((50 * 5.0 + 1056 * 40.0) / 1e6)  # 价目：media.PRICES（OpenAI 价目页）
    (env / "sketch.png").write_bytes(png(16, 16))
    out = run(offered().run({"prompt": "Make it night", "image": "sketch.png"}, ctx()))
    edit = fx.of("/v1/images/edits")[-1].extra
    assert edit["prompt"] == "Make it night" and edit["image"][1] == (env / "sketch.png").read_bytes() and edit["model"] == "gpt-image-1"
    assert last_row()["cost"] == pytest.approx((50 * 5.0 + 300 * 10.0 + 1056 * 40.0) / 1e6)  # 参考图按图的输入价
    with pytest.raises(ToolFailure, match="size"):
        run(offered().run({"prompt": "x", "size": "huge"}, ctx()))
    (env / "notes.txt").write_text("not an image")
    with pytest.raises(ToolFailure, match="isn't an image"):
        run(offered().run({"prompt": "x", "image": "notes.txt"}, ctx()))


def test_gemini_fal_and_comfyui(env, conf, fx, tmp_path):
    conf("google/gemini-3.1-flash-image")
    out = run(offered().run({"prompt": "A small fictional robot", "aspect_ratio": "16:9"}, ctx()))
    assert len(out.images) == 1 and images.sniff(open(attachments(out.text)[0]["path"], "rb").read()) == "image/png"
    cfg = fx.of(":generateContent")[-1].json["generationConfig"]
    assert cfg["responseModalities"] == ["TEXT", "IMAGE"] and cfg["imageConfig"] == {"aspectRatio": "16:9"}
    assert last_row()["cost"] == pytest.approx(1290 * 60.0 / 1e6)

    conf("fal/fal-ai/flux/dev")
    out = run(offered().run({"prompt": "Watercolor fox", "count": 3, "size": "768x512"}, ctx()))
    assert len(out.images) == 3 and fx.of("/fal-ai/flux/dev")[-1].headers["authorization"] == f"Key {KEY}"
    body = fx.of("/fal-ai/flux/dev")[-1].json
    assert body["num_images"] == 3 and body["image_size"] == {"width": 768, "height": 512} and body["sync_mode"] is True
    assert last_row()["cost"] is None and last_row()["units"] == 3  # fal 按模型计价，价目表里没有：null

    wf = tmp_path / "workflow_api.json"
    wf.write_text(json.dumps({"3": {"class_type": "KSampler", "inputs": {"seed": 42}}, "6": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}}}))
    conf({"model": "comfyui/workflow", "workflow": str(wf), "prompt_node": "6"})
    out = run(offered().run({"prompt": "A quiet fictional garden", "count": 2}, ctx()))
    assert len(out.images) == 2 and last_row()["cost"] == 0.0 and last_row()["provider"] == "comfyui"
    sent = [r.json["prompt"] for r in fx.of("^/prompt$")]
    assert sent[0]["6"]["inputs"]["text"] == "A quiet fictional garden" and sent[0]["3"]["inputs"]["seed"] != sent[1]["3"]["inputs"]["seed"]
    (env / "ref.png").write_bytes(png())
    with pytest.raises(ToolFailure, match="reference"):
        run(offered().run({"prompt": "x", "image": "ref.png"}, ctx()))


def test_errors_never_carry_the_key(env, conf, fx, monkeypatch):
    wrong = "sk-wrong-image-9876543210zyxw"
    monkeypatch.setenv("OPENAI_API_KEY", wrong)
    conf("openai/gpt-image-1-mini")
    with pytest.raises(ToolFailure) as e:
        run(offered().run({"prompt": "x"}, ctx()))
    assert wrong not in str(e.value) and "HTTP 401" in str(e.value) and fx.requests[-1].auth == wrong
    assert last_row()["ok"] == 0 and wrong not in (last_row()["error"] or "")


def test_generated_images_hang_on_the_reply(env, conf, monkeypatch):
    import chat
    from engine import providers
    conf("openai/gpt-image-1")
    f = FakeModel([{"text": "Drawing it.", "calls": [("c1", "image_generate", {"prompt": "A fictional teapot"})]}, {"text": "Here is your teapot."}])
    monkeypatch.setattr(providers, "stream", f.stream)

    async def go():
        r = chat.start_run("t-img-chat", "draw a teapot", None)
        await collect(chat.attach(r))
        return r
    r = run(go())
    shown = json.loads(msg_row(chat, r.reply_id)["attachments"])
    assert len(shown) == 1 and shown[0]["kind"] == "image" and shown[0]["url"].startswith("/api/files/")
    tool_msg = next(m for m in f.requests[1][1].messages if m["role"] == "tool")
    assert any(b.get("type") == "image" for b in tool_msg["content"])  # 模型这一轮里看得见生成的图


def test_not_for_unattended_turns_or_children_and_listed_in_setup(env, conf):
    from engine import owner, setup_api, tasks, unattended
    assert "image_generate" in unattended.NO_TOOLS and "image_generate" in tasks.CHILD_NEVER
    conf("openai/gpt-image-1")
    assert "image_generate draws images" in owner.rules("main", tools.for_agent("main"))
    rows = {r["id"]: r for r in setup_api.media_rows(True)}
    assert rows["image"]["current"] == "openai/gpt-image-1" and {"openai", "google", "fal", "comfyui"} <= set(rows["image"]["providers"])
