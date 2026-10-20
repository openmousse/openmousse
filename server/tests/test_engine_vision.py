"""第 3 批第 3 项：看图（engine/vision.py）、看 PDF（engine/pdf.py）、媒体共用的一层（engine/media.py）。
不配用途、不开开关：没有这两个工具，发给不收图的模型的照旧是那一句；目录的 caps 多了 pdf / video / audio。
view_image：主模型收图 → 图进工具结果（工作区的、图库的、附件、公网网址）；不收图 → 看图模型答（包成资料、记花费、按图存着不再花钱）；
报错里抹掉密钥。不收图的模型配了看图：用户发的图、工具结果里的图先换成描述（同一张图只描述一次）。
read_pdf：主模型读 PDF → document 块进工具结果，发给 Claude 时是 document、发给不收 PDF 的换成一句话；配了看文档 → 那个模型答；
都没有 → 抽字，扫描页转成图（有 pdftoppm 才测）。没人在看的那几轮、子 Agent 不给；设置中心多两行；用量多一栏 media。
服务商全是假的：evals/fakemedia.py（Gemini 的接口）、evals/fakemodel.py（对话模型），网址是本机的假网站。"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import shutil
import time

import pytest
from PIL import Image

import engine
from engine import catalog, images, media, models, pdf, provider_anthropic, provider_openai, providers, store, tools, vision, web
from engine.loop import ToolFailure, ToolOutput
from engine.session import MemorySession, text_of, tool_result, user
from evals.fakemedia import FakeMedia
from evals.fakemodel import FakeModelServer, Reply

KEY = "sk-fake-gemini-0123456789abcdef"
PUBLIC_IP = "93.184.216.34"


def run(coro):
    return asyncio.run(coro)


def ctx(agent: str = "main", conversation: str = "main", turn: str = "t") -> engine.ToolCtx:
    return engine.ToolCtx(turn=turn, kind="owner", agent=agent, conversation=conversation, call_id="c1", control=engine.Control())


def png(w: int = 40, h: int = 30, color=(200, 30, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "PNG")
    return buf.getvalue()


def make_pdf(pages: list[str]) -> bytes:
    """一份最简单的 PDF：每页一行字（Helvetica）；字是空的那页 = 扫描件（抽不出字）。"""
    objs: list[bytes] = []
    n = len(pages)
    font = 3 + 2 * n
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(n))
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode())
    for i, t in enumerate(pages):
        stream = (f"BT /F1 18 Tf 72 700 Td ({t}) Tj ET" if t else "0 0 1 rg 100 100 200 200 re f").encode()
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {4 + 2 * i} 0 R /Resources << /Font << /F1 {font} 0 R >> >> >>".encode())
        objs.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for k, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{k} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode() + b"".join(f"{x:010d} 00000 n \n".encode() for x in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


@pytest.fixture
def lib(tmp_path, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(images, "_pruned", [time.monotonic()])
    monkeypatch.setattr(pdf, "_pruned", [time.monotonic()])
    monkeypatch.setattr(media, "_ready", set())
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setattr(settings, "workspace", ws)
    monkeypatch.setattr(settings, "uploads", tmp_path / "uploads")
    monkeypatch.setattr(providers, "_no_images", {})
    return ws


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
    return f


@pytest.fixture
def conf(server_json, fm, fx, monkeypatch):
    """server.json：fake/* 收图的兼容接口，deepseek/* 也指过来（目录里写着不收图），anthropic/* 是 Claude；google 指到假的 Gemini。"""
    monkeypatch.setenv("GEMINI_API_KEY", KEY)

    def write(main: str = "fake/vision-1", **extra) -> None:
        eng = {"model": main, "providers": {"fake": {"api": "openai", "url": fm.openai_url, "key_env": ""},
                                            "deepseek": {"url": fm.openai_url, "key_env": ""},
                                            "anthropic": {"url": fm.anthropic_url, "key": "test-key"},
                                            "google": {"url": fx.url + "/v1beta/openai"}}}
        for k, v in extra.items():
            eng[k] = v
        server_json({"engine": eng})
    return write


def offered(name: str, agent: str = "main") -> engine.Tool | None:
    return next((t for t in tools.for_agent(agent) if t.name == name), None)


def media_rows() -> list[dict]:
    with store.read() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM engine_media_usage ORDER BY id")]


# —— 不配：一字不变 ——————————————————————————————————————————————————————————————

def test_off_by_default_and_the_catalog_gained_caps(lib, conf, fm):
    conf()
    assert offered("view_image") is None and offered("read_pdf") is None
    assert catalog.problems() == [] and set(catalog.CAP_KEYS) >= {"pdf", "video", "audio"}
    assert catalog.CLOUD["google"]["caps"]["video"] is True and catalog.CLOUD["anthropic"]["caps"]["pdf"] is True
    assert catalog.CLOUD["openai"]["caps"]["pdf"] is True and catalog.CLOUD["deepseek"]["caps"]["pdf"] is None  # 文档里没写的：None
    assert media.choice("vision") is None and not vision.on() and not vision.describe_on()
    # 不收图的模型照旧是那一句（没有看图模型，不描述）
    fm.push(Reply(text="ok"))
    res = run(engine.run_turn(engine.Spec(kind="owner", model="deepseek/deepseek-flash"), MemorySession(),
                              user("what is this?", [{"type": "image", "mime": "image/png", "data": base64.b64encode(png()).decode()}])))
    assert res.status == "done"
    sent = json.dumps(fm.of("openai")[-1]["body"]["messages"])
    assert "can't read images" in sent and "FAKE-VISION" not in sent


def test_purposes_parse_reject_plans_and_explain_mistakes(lib, conf):
    conf(use={"vision": "claude-code/opus", "pdf": {"model": "google/models/gemini-2.5-flash-lite", "max_tokens": 500}, "stt": "nonsense"})
    assert media.choice("vision") is None and "plan" in (media.problem("vision") or "")  # 套餐不能用在按量花钱的这几样
    c = media.choice("pdf")
    assert c.ref == "google/gemini-2.5-flash-lite" and c.opts == {"max_tokens": 500}  # 带前缀的型号规范成不带的
    assert media.problem("pdf") is None and "provider/model" in (media.problem("stt") or "")
    conf(use={"vision": "nosuch/model"})
    assert "isn't a known provider" in (media.problem("vision") or "")


# —— view_image ————————————————————————————————————————————————————————————————

def test_view_image_hands_the_picture_to_a_model_that_sees(lib, conf, fx):
    conf(main="fake/vision-1", media={"view_image": True})
    (lib / "shot.png").write_bytes(png(64, 48))
    t = offered("view_image")
    assert t is not None and t.level({"path": "shot.png"})[0] == "read"
    out = run(t.run({"path": "shot.png", "question": "what colour?"}, ctx()))
    assert isinstance(out, ToolOutput) and out.images[0]["type"] == "image" and out.images[0]["w"] == 64
    assert "answer the question yourself" in out.text and not fx.requests  # 自己看：不花钱
    stored = images.path_of(out.images[0])  # 图库里那张也能再看（工具结果里写着的路径）
    again = run(t.run({"path": str(stored)}, ctx()))
    assert isinstance(again, ToolOutput) and again.images[0]["sha"] == out.images[0]["sha"]
    with pytest.raises(ToolFailure, match="exactly one"):
        run(t.run({"path": "shot.png", "url": "https://site.example/x.png"}, ctx()))
    (lib / ".env").write_text("SECRET=1")
    with pytest.raises(ToolFailure, match="secret"):
        run(t.run({"path": ".env"}, ctx()))
    with pytest.raises(ToolFailure, match="isn't inside your workspace"):
        run(t.run({"path": "/etc/hostname"}, ctx()))
    (lib / "notes.txt").write_text("not a picture")
    with pytest.raises(ToolFailure, match="isn't an image"):
        run(t.run({"path": "notes.txt"}, ctx()))


def test_view_image_reads_this_conversations_attachments_only(lib, conf):
    import files
    from config import settings
    conf(main="fake/vision-1", media={"view_image": True})
    folder = settings.uploads / "main"
    folder.mkdir(parents=True)
    p = folder / "a1b2c3-photo.png"
    p.write_bytes(png(20, 20))
    with files._lock, files.adb() as conn:
        conn.execute("INSERT INTO attachments(id, thread, name, mime, size, kind, path, status, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                     ("a1b2c3", "main", "photo.png", "image/png", p.stat().st_size, "image", str(p), "ok", "2026-10-19T08:00:00+00:00"))
    t = offered("view_image")
    by_id = run(t.run({"attachment": "a1b2c3"}, ctx()))
    by_path = run(t.run({"path": str(p)}, ctx()))
    assert isinstance(by_id, ToolOutput) and isinstance(by_path, ToolOutput) and by_id.images[0]["sha"] == by_path.images[0]["sha"]
    with pytest.raises(ToolFailure, match="no attachment"):
        run(t.run({"attachment": "a1b2c3"}, ctx(conversation="other-thread")))  # 别的对话的附件不认
    with pytest.raises(ToolFailure, match="isn't an attachment of this conversation"):
        run(t.run({"path": str(p)}, ctx(conversation="other-thread")))


def test_view_image_asks_the_vision_model_for_a_blind_model_and_remembers(lib, conf, fx):
    conf(main="deepseek/deepseek-flash", use={"vision": "google/gemini-2.5-flash-lite"})
    (lib / "chart.png").write_bytes(png(30, 30, (10, 200, 10)))
    t = offered("view_image")
    assert t is not None and "gemini-2.5-flash-lite" in t.description
    out = run(t.run({"path": "chart.png", "question": "what is the trend?"}, ctx()))
    assert isinstance(out, str) and "FAKE-VISION: 1 image(s)" in out and "what is the trend?" in out
    assert "<untrusted_tool_result" in out and "vision model google/gemini-2.5-flash-lite" in out  # 描述包成资料
    req = fx.of(":generateContent")[0]
    assert req.auth == KEY and KEY not in req.path  # 密钥只在请求头里
    row = media_rows()[-1]
    assert (row["purpose"], row["provider"], row["model"], row["ok"], row["conversation"]) == ("vision", "google", "gemini-2.5-flash-lite", 1, "main")
    assert row["tokens_in"] == 1200 and row["cost"] == pytest.approx((1200 * 0.10 + 80 * 0.40) / 1e6)  # 价目：media.PRICES
    run(t.run({"path": "chart.png", "question": "what is the trend?"}, ctx()))
    assert len(fx.of(":generateContent")) == 1  # 同一张图同一个问题：存着，不再花钱
    run(t.run({"path": "chart.png", "question": "and the colour?"}, ctx()))
    assert len(fx.of(":generateContent")) == 2


def test_vision_errors_never_carry_the_key(lib, conf, fx, monkeypatch):
    wrong = "sk-wrong-key-9876543210zyxw"
    monkeypatch.setenv("GEMINI_API_KEY", wrong)
    conf(main="deepseek/deepseek-flash", use={"vision": "google/gemini-2.5-flash-lite"})
    (lib / "x.png").write_bytes(png())
    with pytest.raises(ToolFailure) as e:
        run(offered("view_image").run({"path": "x.png"}, ctx()))
    assert wrong not in str(e.value) and "couldn't answer" in str(e.value)
    assert wrong in fx.requests[-1].auth  # 真发出去了（假服务商在错误里回显了它）
    row = media_rows()[-1]
    assert row["ok"] == 0 and wrong not in (row["error"] or "") and row["cost"] is None
    monkeypatch.delenv("GEMINI_API_KEY")
    with pytest.raises(ToolFailure, match="No key for google"):
        run(offered("view_image").run({"path": "x.png"}, ctx()))


def test_view_image_fetches_public_urls_only(lib, conf, monkeypatch):
    from aiohttp import web as aw
    conf(main="fake/vision-1", media={"view_image": True})
    pic = png(12, 12)
    holder: dict = {}

    async def h(request):
        return aw.Response(body=pic, content_type="image/png")

    async def resolve(host: str, port: int) -> list[str]:
        return {"site.example": [PUBLIC_IP], "inside.example": ["10.0.0.9"]}.get(host, [host])

    def dial(ip: str, port: int) -> tuple[str, int]:
        assert ip == PUBLIC_IP
        return "127.0.0.1", holder["port"]
    monkeypatch.setattr(web, "resolve_host", resolve)
    monkeypatch.setattr(web, "dial", dial)
    monkeypatch.setattr(web, "proxied", lambda: False)

    async def go():
        app = aw.Application()
        app.router.add_get("/cat.png", h)
        runner = aw.AppRunner(app)
        await runner.setup()
        s = aw.TCPSite(runner, "127.0.0.1", 0)
        await s.start()
        holder["port"] = s._server.sockets[0].getsockname()[1]
        try:
            t = offered("view_image")
            ok = await t.run({"url": "http://site.example/cat.png"}, ctx())
            with pytest.raises(ToolFailure):
                await t.run({"url": "http://inside.example/cat.png"}, ctx())
            with pytest.raises(ToolFailure):
                await t.run({"url": "http://127.0.0.1/cat.png"}, ctx())
            return ok
        finally:
            await runner.cleanup()
    out = run(go())
    assert isinstance(out, ToolOutput) and out.images[0]["w"] == 12 and "site.example" in out.text


# —— 不收图的模型：图换成描述 ——————————————————————————————————————————————————————————

def test_a_blind_model_gets_descriptions_instead_of_pictures(lib, conf, fm, fx):
    conf(main="deepseek/deepseek-flash", use={"vision": "google/gemini-2.5-flash-lite"})
    shot = png(50, 40, (1, 2, 3))

    async def camera_run(args, ctx_):
        block, _ = images.make(shot, label="screenshot of the fake page")
        return ToolOutput("took it", [block])
    camera = engine.Tool(name="shoot", description="take a screenshot", schema={"type": "object", "properties": {}}, run=camera_run)
    fm.push(Reply(calls=[("shoot", {})]), Reply(text="I see it."))
    photo = {"type": "image", "mime": "image/png", "data": base64.b64encode(png(8, 8, (9, 9, 9))).decode()}
    s = MemorySession()
    res = run(engine.run_turn(engine.Spec(kind="owner", model="deepseek/deepseek-flash", tools=[camera]), s, user("look at my photo", [photo])))
    assert res.status == "done" and res.text == "I see it."
    first, second = (json.dumps(r["body"]["messages"]) for r in fm.of("openai")[-2:])
    assert "FAKE-VISION" in first and "can't read images" not in first and "image_url" not in first
    assert "described by the vision model google/gemini-2.5-flash-lite" in second and "screenshot of the fake page" in second
    assert "untrusted_tool_result" in second  # 描述也包成资料
    calls = len(fx.of(":generateContent"))
    assert calls == 2  # 用户的照片一张、截图一张；第二次请求里用户那张已经存着
    fm.push(Reply(text="Still the same."))
    run(engine.run_turn(engine.Spec(kind="owner", model="deepseek/deepseek-flash", tools=[camera]), s, user("and now?")))
    assert len(fx.of(":generateContent")) == calls  # 同一张图：按内容存着，不再花钱（前缀也一样）
    assert json.dumps(fm.of("openai")[-1]["body"]["messages"]).count("FAKE-VISION") == 2
    assert [r["purpose"] for r in media_rows()[-calls:]] == ["vision"] * calls  # 库是测试共用的：看最后这几行
    # 收图的模型：照旧发图，不描述
    fm.push(Reply(text="Red."))
    run(engine.run_turn(engine.Spec(kind="owner", model="fake/vision-1"), MemorySession(), user("colour?", [photo])))
    assert "image_url" in json.dumps(fm.of("openai")[-1]["body"]["messages"]) and len(fx.of(":generateContent")) == calls
    # 关掉描述：照旧那一句
    conf(main="deepseek/deepseek-flash", use={"vision": "google/gemini-2.5-flash-lite"}, media={"describe": False})
    fm.push(Reply(text="ok"))
    run(engine.run_turn(engine.Spec(kind="owner", model="deepseek/deepseek-flash"), MemorySession(), user("x", [photo])))
    assert "can't read images" in json.dumps(fm.of("openai")[-1]["body"]["messages"])


def test_a_failed_description_falls_back_to_a_sentence(lib, conf, fm, fx):
    conf(main="deepseek/deepseek-flash", use={"vision": "google/gemini-2.5-flash-lite"})
    fx.fail(500, 1, "vision is down")
    fm.push(Reply(text="ok"))
    photo = {"type": "image", "mime": "image/png", "data": base64.b64encode(png(7, 7)).decode()}
    res = run(engine.run_turn(engine.Spec(kind="owner", model="deepseek/deepseek-flash"), MemorySession(), user("x", [photo])))
    assert res.status == "done"
    assert "couldn't describe it" in json.dumps(fm.of("openai")[-1]["body"]["messages"])


# —— read_pdf ——————————————————————————————————————————————————————————————————

def test_read_pdf_attaches_the_document_for_claude(lib, conf, fm):
    conf(main="anthropic/claude-haiku-5-5", media={"read_pdf": True})
    (lib / "report.pdf").write_bytes(make_pdf(["Quarterly figures", "Second page text"]))
    t = offered("read_pdf")
    assert t is not None and t.level({"path": "report.pdf"})[0] == "read"
    fm.push(Reply(calls=[("read_pdf", {"path": "report.pdf", "question": "totals?"})]), Reply(text="Read it."))
    res = run(engine.run_turn(engine.Spec(kind="owner", model="anthropic/claude-haiku-5-5", tools=[t]), MemorySession(), user("read the report")))
    assert res.status == "done" and res.text == "Read it."
    body = fm.of("anthropic")[-1]["body"]
    result = next(b for m in body["messages"] for b in (m["content"] if isinstance(m["content"], list) else []) if b.get("type") == "tool_result")
    doc = next(b for b in result["content"] if b.get("type") == "document")
    assert doc["source"]["media_type"] == "application/pdf" and base64.b64decode(doc["source"]["data"]).startswith(b"%PDF-")
    assert list((lib.parent / "data" / "engine" / "docs").glob("*.pdf"))  # 存在文档库里，会话里只记引用
    # 同一条工具结果发给不收 PDF 的：换成一句话
    block = {"type": "document", "mime": "application/pdf", "file": doc_name(lib), "pages": 2, "bytes": 10, "label": "report.pdf"}
    msgs = [tool_result("c1", "read_pdf", "report.pdf: PDF", images=[block])]
    shown = pdf.for_model(msgs, False)
    assert "can't read PDFs directly" in text_of(shown[0]) and "report.pdf" in text_of(shown[0])
    loaded = pdf.for_model(msgs, True)
    assert loaded[0]["content"][1]["type"] == "document" and loaded[0]["content"][1]["data"]


def doc_name(ws) -> str:
    return next((ws.parent / "data" / "engine" / "docs").glob("*.pdf")).name


def test_documents_go_out_as_pdf_parts_only_where_taken(lib, conf):
    conf()
    data = base64.b64encode(make_pdf(["hello"])).decode()
    msgs = [user("read", [{"type": "document", "mime": "application/pdf", "data": data, "title": "a.pdf"}])]
    official = provider_openai.to_openai("", msgs, {"name": "openai", "api": "openai"}, "gpt-6-luna")
    assert official[-1]["content"][1]["type"] == "file" and official[-1]["content"][1]["file"]["file_data"].startswith("data:application/pdf;base64,")
    other = provider_openai.to_openai("", msgs, {"name": "fake", "api": "openai"}, "x")
    assert "can't read PDFs directly" in json.dumps(other)
    claude = provider_anthropic.to_anthropic(msgs, {"mid_system": True})
    assert claude[-1]["content"][1]["type"] == "document" and claude[-1]["content"][1]["title"] == "a.pdf"
    assert media.reads_pdf("anthropic/claude-haiku-5-5") and not media.reads_pdf("fake/x") and not media.reads_pdf("claude-code/opus")


def test_read_pdf_asks_the_document_model_or_extracts_text(lib, conf, fx):
    (lib / "memo.pdf").write_bytes(make_pdf(["Budget is fictional", "Second page"]))
    conf(main="deepseek/deepseek-flash", use={"pdf": "google/gemini-2.5-flash-lite"})
    t = offered("read_pdf")
    out = run(t.run({"path": "memo.pdf", "question": "what budget?"}, ctx()))
    assert "FAKE-PDF" in out and "document model google/gemini-2.5-flash-lite" in out and "untrusted_tool_result" in out
    assert media_rows()[-1]["purpose"] == "pdf"
    one = run(t.run({"path": "memo.pdf", "pages": "2"}, ctx()))
    assert "pages 2" in one
    with pytest.raises(ToolFailure, match="has 2 pages"):
        run(t.run({"path": "memo.pdf", "pages": "3-4"}, ctx()))
    text = run(t.run({"path": "memo.pdf", "mode": "text"}, ctx()))  # 只抽字：不花钱
    assert "[Page 1]" in text and "Budget is fictional" in text and "(extracted text)" in text
    assert len(fx.of(":generateContent")) == 2
    (lib / "fake.pdf").write_bytes(b"not a pdf at all")
    with pytest.raises(ToolFailure, match="isn't a PDF"):
        run(t.run({"path": "fake.pdf"}, ctx()))


@pytest.mark.skipif(shutil.which("pdftoppm") is None, reason="pdftoppm isn't installed")
def test_scanned_pages_become_images(lib, conf, fx):
    (lib / "scan.pdf").write_bytes(make_pdf(["A page with plenty of real text on it", ""]))
    conf(main="fake/vision-1", media={"read_pdf": True})
    out = run(offered("read_pdf").run({"path": "scan.pdf"}, ctx()))
    assert isinstance(out, ToolOutput) and len(out.images) == 1 and "page 2" in out.images[0]["label"]
    assert "attached as images" in out.text and "plenty of real text" in out.text
    conf(main="deepseek/deepseek-flash", use={"vision": "google/gemini-2.5-flash-lite"}, media={"read_pdf": True})
    said = run(offered("read_pdf").run({"path": "scan.pdf"}, ctx()))
    assert isinstance(said, str) and "[Page 2, read by the vision model google/gemini-2.5-flash-lite]" in said and "FAKE-VISION" in said
    conf(main="deepseek/deepseek-flash", media={"read_pdf": True})
    none = run(offered("read_pdf").run({"path": "scan.pdf"}, ctx()))
    assert "neither this model nor a vision model" in none


# —— 别处 ——————————————————————————————————————————————————————————————————————

def test_not_for_unattended_turns_or_children_and_shown_in_setup_and_usage(lib, conf):
    from datetime import datetime, timedelta

    from config import settings
    from engine import setup_api, tasks, unattended, usage
    assert {"view_image", "read_pdf"} <= unattended.NO_TOOLS and {"view_image", "read_pdf"} <= tasks.CHILD_NEVER
    conf(use={"vision": "google/gemini-2.5-flash-lite"}, media={"read_pdf": True})
    from engine import owner
    rules = owner.rules("main", tools.for_agent("main"))
    assert "**Images, PDFs, audio and video**" in rules and "view_image" not in rules.split("## Tools")[1].split("\n\n")[0]
    rows = {r["id"]: r for r in setup_api.media_rows(True)}
    assert rows["vision"]["current"] == "google/gemini-2.5-flash-lite" and rows["pdf"]["current"] is None and rows["vision"]["kind"] == "media"
    assert "deepseek" not in rows["vision"]["providers"] and set(rows["pdf"]["providers"]) == {"openai", "anthropic", "google"}
    data: dict = {}
    for fn in setup_api.media_plan({"pdf": {"model": "google/gemini-2.5-flash-lite", "max_tokens": 900, "key": "never-stored"}, "vision": None}, True):
        fn(data)
    assert data["engine"]["use"] == {"pdf": {"model": "google/gemini-2.5-flash-lite", "max_tokens": 900}}  # 密钥不进 server.json
    with pytest.raises(setup_api.SetupError):
        setup_api.media_plan({"pdf": "deepseek/deepseek-flash"}, True)
    later = datetime.now(settings.tz) + timedelta(minutes=1)
    before = usage.summary(7, now=later)["media"]["total"]  # 库是测试共用的：看多了几次
    media.record("vision", media.Choice("vision", "google", "gemini-2.5-flash-lite"), unit="token", tokens_in=1000, tokens_out=100)
    s = usage.summary(7, now=later)
    assert s["media"]["total"]["calls"] == before["calls"] + 1 and "vision" in {r["purpose"] for r in s["media"]["byPurpose"]}
    assert s["media"]["total"]["cost"] == pytest.approx(before["cost"] + (1000 * 0.10 + 100 * 0.40) / 1e6)
    assert models.MEDIA_PURPOSES == media.PURPOSES
