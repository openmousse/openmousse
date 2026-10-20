"""看 PDF（第 3 批第 3 项，2026-10-19）：read_pdf 工具、「看文档」用途（engine.use.pdf）、工具结果里的 PDF 怎么发给各家。

read_pdf（主人回合；配了 engine.use.pdf，或者 engine.media.read_pdf = true 才有）：一个 PDF（path / url / attachment，同 view_image，
  engine/media.load）+ question，pages 可选（"1-5,8"），mode = text 只抽字（不花钱）。按顺序：
  1. 主模型原样读 PDF（Claude 的官方接口、OpenAI 自己的 /chat/completions：media.reads_pdf）、选的页不超过 MAIN_PAGES、不超过 MAIN_BYTES：
     PDF 放进工具结果（document 块：存在 <data_dir>/engine/docs/<sha256>.pdf，会话里只记引用，发的时候再读回来，同图），它自己读、自己答。
  2. 配了看文档（engine.use.pdf）：交给那个模型答 question（Google 的走它自己的接口 inline_data；Claude、OpenAI 的走 document 块；
     别的型号给抽出来的字），回文字（包成资料）。
  3. 都没有：照旧抽字（pypdf，同附件、read_file），一页一页标好；抽不出字的页（扫描件）有 pdftoppm 就转成图：主模型收图 → 图放进工具结果；
     不收图、配了看图（engine.use.vision）→ 看图模型逐页把字读出来；都没有 → 说清楚是扫描件、这里读不了。
发给模型（providers.stream 调 for_model）：工具结果里的 PDF 照这一家收不收 PDF 读回 base64，或者换成一句话（是什么、几页、存在哪，要再看就
  重新 read_pdf）；最近 KEEP 份总是带着，更早的攒够 BATCH 份才一起换成字（同图：缓存的前缀成批地断）。
  注意：compact.py 估 token 时不算 document 块（第 2 批的地盘），一份 PDF 进来以后要等下一次模型调用回了用量才算准：所以 MAIN_PAGES 卡得紧。
花费：问看文档模型、看图模型读扫描页，各记一行 engine_media_usage（purpose pdf / vision）。
没人在看的那几轮、后台任务的子 Agent：不给（unattended.NO_TOOLS、tasks.CHILD_NEVER）。
server.json（都可以不写）：
  "engine": {"use": {"pdf": "google/gemini-3.5-flash-lite"}, "media": {"read_pdf": true, "pdf_pages": 20}}
参考（只读）：OpenClaw docs/tools/pdf.md（原生读 PDF 的交给 Anthropic / Google，别的抽字退路；页的范围）、Hermes 的文档处理（pdftoppm 转页）。只借设计。
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import os
import re
import tempfile
import time
from pathlib import Path

from config import settings
from i18n import L

from . import images, media, models
from .loop import Tool, ToolCtx, ToolFailure, ToolOutput

NAME = "read_pdf"
READ = "read"
MAX_BYTES = 32 * 1024 * 1024        # 一份 PDF 最多读进来多大（Claude 的 PDF 上限 32 MB）
MAIN_BYTES = 20 * 1024 * 1024       # 放进工具结果交给主模型的最多多大
MAIN_PAGES = 20                     # 交给主模型的最多几页（engine.media.pdf_pages 可改，1–100）
INLINE_MAX = 20 * 1024 * 1024       # Gemini 的 inline_data：整个请求 20 MB 以内
TEXT_CHARS = 40_000                 # 抽出来的字一次最多给多少
SCAN_PAGES = 8                      # 扫描件一次最多转几页图
SCAN_CHARS = 20                     # 一页抽出来不到这么多字 = 当它是扫描的
DPI = 110
KEEP, BATCH = 2, 2                  # 发给模型时总带着最近几份 PDF；更早的攒够几份才一起换成字
KEEP_DAYS = 30
STORE_MAX = 1024 * 1024 * 1024
PRUNE_EVERY = 3600
_pruned = [0.0]
ANSWER = ("Read the attached PDF and answer the question. Quote the passages you rely on with their page numbers. If the document "
          "doesn't answer it, say so plainly.")
SUMMARY = ("Read the attached PDF and report what it says: its structure, the key points, and every table and chart with its values, "
           "with page numbers. Copy figures and names exactly.")
SCAN = "Transcribe all the text on this page exactly as written (keep its language and layout as far as plain text allows). Describe figures briefly."


def on() -> bool:
    return media.choice("pdf") is not None


def tool_on() -> bool:
    from .vision import cfg
    return on() or cfg().get("read_pdf") is True


def main_pages() -> int:
    from .vision import cfg
    v = cfg().get("pdf_pages")
    return v if isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= 100 else MAIN_PAGES


# —— 存 PDF（同图库：按内容取名，会话里只记引用）——————————————————————————————————————————————

def store_dir() -> Path:
    return settings.data_dir / "engine" / "docs"


def save(data: bytes) -> tuple[str, str]:
    sha = hashlib.sha256(data).hexdigest()
    name = f"{sha}.pdf"
    d = store_dir()
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    if p.exists():
        os.utime(p)
    else:
        tmp = d / f".{name}.{os.getpid()}.tmp"
        tmp.write_bytes(data)
        os.replace(tmp, p)
    if time.monotonic() - _pruned[0] > PRUNE_EVERY:
        _pruned[0] = time.monotonic()
        prune()
    return name, sha


def prune() -> int:
    d = store_dir()
    try:
        files = sorted(((p.stat().st_mtime, p.stat().st_size, p) for p in d.iterdir() if p.is_file() and not p.name.startswith(".")), key=lambda x: x[0])
    except OSError:
        return 0
    cutoff, total, n = time.time() - KEEP_DAYS * 86400, sum(s for _, s, _ in files), 0
    for mtime, size, p in files:
        if mtime >= cutoff and total <= STORE_MAX:
            break
        try:
            p.unlink()
            total -= size
            n += 1
        except OSError:
            pass
    return n


def path_of(block: dict) -> Path | None:
    f = str(block.get("file") or "")
    return store_dir() / f if re.fullmatch(r"[0-9a-f]{64}\.pdf", f) else None


def load(block: dict) -> str | None:
    p = path_of(block)
    try:
        return base64.b64encode(p.read_bytes()).decode() if p else None
    except OSError:
        return None


def describe(block: dict) -> str:
    bits = ", ".join(x for x in (f"{block['pages']} page{'s' if block.get('pages') != 1 else ''}" if block.get("pages") else "",
                                 media.human_bytes(int(block["bytes"])) if block.get("bytes") else "") if x)
    label = str(block.get("label") or block.get("title") or "PDF")
    where = path_of(block)
    return f"{label} ({bits})" + (f", saved on the server at {where}" if where else "")


# —— 发给模型（providers.stream 调）——————————————————————————————————————————————————————

def tool_docs(messages: list[dict]) -> int:
    return sum(1 for m in messages if m.get("role") == "tool" for b in m.get("content") or [] if b.get("type") == "document")


def reads(p: dict) -> bool:
    """这一家（models.provider 的那一份）收不收中立格式的 document 块。"""
    return p.get("pdf") is not False and (p.get("api") == models.ANTHROPIC or media.pdf_chat(p))


def for_model(messages: list[dict], accepts: bool) -> list[dict]:
    """工具结果里的 PDF：读回 base64，或者换成一句话（不收 PDF 的、出错的结果里的、太早的、文件没了的）。不改传进来的消息。"""
    total = tool_docs(messages)
    if not total:
        return messages
    old = images.dropped(total, KEEP, BATCH)
    seen = 0
    out: list[dict] = []
    for m in messages:
        if m.get("role") != "tool" or not any(b.get("type") == "document" for b in m.get("content") or []):
            out.append(m)
            continue
        blocks: list[dict] = []
        for b in m.get("content") or []:
            if b.get("type") != "document":
                blocks.append(b)
                continue
            seen += 1
            if not accepts:
                text = f"[PDF omitted: this model can't read PDFs directly. It was {describe(b)}. Use read_pdf with mode text to read its text.]"
            elif m.get("error"):
                text = f"[PDF omitted from an error result: {describe(b)}.]"
            elif seen <= old:
                text = f"[Earlier PDF no longer attached, to save room: {describe(b)}. Read it again with read_pdf if you need it.]"
            elif (data := load(b)) is None:
                text = f"[PDF no longer available: {describe(b)}.]"
            else:
                blocks.append({"type": "document", "mime": "application/pdf", "data": data, "title": str(b.get("title") or "document.pdf")})
                continue
            blocks.append({"type": "text", "text": "\n" + text})
        out.append({**m, "content": blocks})
    return out


# —— 读 PDF（pypdf；requirements 里本来就有）————————————————————————————————————————————————

def is_pdf(data: bytes) -> bool:
    return b"%PDF-" in data[:1024]


def open_pdf(data: bytes):
    from pypdf import PdfReader
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise ToolFailure(L("这个 PDF 加了密码，打不开", "This PDF is password-protected, so it can't be opened"))
        n = len(reader.pages)
    except ToolFailure:
        raise
    except Exception as e:  # noqa: BLE001 — 坏的 PDF
        raise ToolFailure(L(f"读不了这个 PDF（{type(e).__name__}）", f"This PDF can't be read ({type(e).__name__})")) from None
    return reader, n


def page_list(spec: str | None, n: int) -> list[int]:
    """"1-5,8" → [1, 2, 3, 4, 5, 8]（从 1 起的页码）；不写 = 全部。写错、超出 → ToolFailure。"""
    s = str(spec or "").replace(" ", "")
    if not s:
        return list(range(1, n + 1))
    out: list[int] = []
    for part in s.split(","):
        m = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if not m:
            raise ToolFailure(L(f"pages 写成 1-5,8 这样（写的是 {spec}）", f"Write pages like 1-5,8 (got {spec})"))
        a, b = int(m.group(1)), int(m.group(2) or m.group(1))
        if not 1 <= a <= b <= n:
            raise ToolFailure(L(f"这个 PDF 只有 {n} 页（pages 写的是 {spec}）", f"This PDF has {n} pages (pages was {spec})"))
        out += [i for i in range(a, b + 1) if i not in out]
        if len(out) > 2000:
            break
    return out


def subset(reader, pages: list[int]) -> bytes:
    from pypdf import PdfWriter
    w = PdfWriter()
    for i in pages:
        w.add_page(reader.pages[i - 1])
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def texts(reader, pages: list[int]) -> list[tuple[int, str]]:
    out = []
    for i in pages:
        try:
            t = (reader.pages[i - 1].extract_text() or "").strip()
        except Exception:  # noqa: BLE001 — 某一页坏了：当它没有字
            t = ""
        out.append((i, t))
    return out


def joined(rows: list[tuple[int, str]], limit: int = TEXT_CHARS) -> tuple[str, int]:
    """[Page n] 字……拼起来，最多 limit 字。→ (字, 拼到第几页)。"""
    parts, used, last = [], 0, 0
    for i, t in rows:
        piece = f"[Page {i}]\n{t}"
        if parts and used + len(piece) > limit:
            break
        parts.append(piece[: max(0, limit - used)] if not parts else piece)
        used += len(piece)
        last = i
    return "\n\n".join(parts), last


async def render(data: bytes, pages: list[int]) -> list[tuple[int, bytes]]:
    """pdftoppm（有才行）把这几页转成 PNG。→ [(页码, PNG 字节)]；没有 pdftoppm / 转不了 → []。"""
    exe = media.local_command("pdftoppm")
    if exe is None or not pages:
        return []
    out = []
    with tempfile.TemporaryDirectory(prefix="mousse-pdf-") as d:
        src = Path(d) / "in.pdf"
        src.write_bytes(data)
        for i in pages:
            try:
                rc, _, _ = await media.run_local([exe, "-f", str(i), "-l", str(i), "-r", str(DPI), "-png", "-singlefile", str(src), str(Path(d) / f"p{i}")],
                                              timeout=60, cwd=d)
            except media.MediaError:
                break
            png = Path(d) / f"p{i}.png"
            if rc == 0 and png.is_file():
                out.append((i, png.read_bytes()))
    return out


# —— 交给看文档用途的模型 ————————————————————————————————————————————————————————————————

async def ask(c: media.Choice, data: bytes, name: str, rows: list[tuple[int, str]], question: str, *, conversation: str | None,
              agent: str | None) -> str:
    """看文档模型读这份 PDF、答 question（空 = 讲一遍内容）。→ 它的话（没包）。出错抛 MediaError。"""
    prompt = f"{ANSWER}\n\nQuestion: {question}" if question else SUMMARY
    limit = int(c.opts.get("max_tokens") or 4096)
    with media.Meter("pdf", c, conversation, agent) as m:
        m.unit = "token"
        if c.provider == "google":
            if len(data) > INLINE_MAX:
                raise media.MediaError("bad", L(f"PDF 太大，Google 一次只收 {INLINE_MAX // 1024 // 1024} MB 以内：用 pages 选几页",
                                                f"The PDF is too large for Google ({INLINE_MAX // 1024 // 1024} MB at most): pick pages"))
            got = await media.gemini(c, [{"text": prompt}, media.inline("application/pdf", data)], config={"maxOutputTokens": limit})
            text = media.gemini_text(got).strip()
            m.tokens_in, m.tokens_out = media.gemini_usage(got)
        elif media.reads_pdf(c.ref):
            text, usage, _ = await media.ask(c, prompt, [{"type": "document", "mime": "application/pdf", "data": base64.b64encode(data).decode(),
                                                          "title": name}], max_tokens=limit)
            m.tokens_in, m.tokens_out = usage["in"], usage["out"]
        else:  # 不收 PDF 的型号：给抽出来的字
            body, _ = joined(rows)
            text, usage, _ = await media.ask(c, prompt.replace("the attached PDF", "the PDF text below") + "\n\n"
                                             + media.wrap(f"read_pdf {name}"[:200].replace('"', "%22"), body or "(no extractable text)"), [], max_tokens=limit)
            m.tokens_in, m.tokens_out = usage["in"], usage["out"]
        m.units = m.tokens_in + m.tokens_out
    if not text:
        raise media.MediaError("shape", f"{c.ref}: empty answer")
    return text


# —— read_pdf ——————————————————————————————————————————————————————————————————————

def _src(label: str) -> str:
    return f"read_pdf {label}"[:200].replace('"', "%22").replace("\n", " ")


def tool(agent: str) -> Tool | None:
    if not tool_on():
        return None
    from .handkit import levelled

    async def run(args: dict, ctx: ToolCtx):
        question = " ".join(str(args.get("question") or "").split())[:2000]
        mode = str(args.get("mode") or "auto").strip().lower()
        if mode not in ("auto", "text"):
            raise ToolFailure(L("mode 只能是 auto 或 text", "mode is auto or text"))
        src = await media.load(args, ctx, max_bytes=MAX_BYTES, what=("PDF", "PDF"))
        if not is_pdf(src.data):
            raise ToolFailure(L(f"{src.label} 不是 PDF", f"{src.label} isn't a PDF"))
        reader, n = await asyncio.to_thread(open_pdf, src.data)
        pages = page_list(args.get("pages"), n)
        part = len(pages) != n
        data = await asyncio.to_thread(subset, reader, pages) if part else src.data
        span = f", pages {args.get('pages')}" if part else ""
        head = f"{src.label}: PDF, {n} page{'s' if n != 1 else ''}{span} · {media.human_bytes(len(data))}"
        main = media.turn_model(ctx.turn) or models.default_model() or None
        if mode == "auto" and media.reads_pdf(main) and len(pages) <= main_pages() and len(data) <= MAIN_BYTES:
            name, sha = await asyncio.to_thread(save, data)
            block = {"type": "document", "mime": "application/pdf", "file": name, "sha": sha, "bytes": len(data), "pages": len(pages),
                     "label": " ".join(f"{src.label}{span}".split())[:160], "title": src.name}
            return ToolOutput(head + ("\nThe PDF is attached: read it and answer the question yourself." if question else "\nThe PDF is attached."), [block])
        rows = await asyncio.to_thread(texts, reader, pages)
        c = media.choice("pdf")
        if mode == "auto" and c is not None:
            try:
                text = await ask(c, data, src.name, rows, question, conversation=ctx.conversation, agent=ctx.agent)
            except media.MediaError as e:
                raise ToolFailure(L(f"看文档的模型（{c.ref}）没答上来：{e}。mode = text 可以只抽字", f"The document model ({c.ref}) couldn't answer: {e}. "
                                    "mode text extracts the text only")) from None
            return f"{head}\nAnswer from the document model {c.ref}:\n" + media.wrap(_src(src.label), text)
        return await extracted(ctx, src, data, rows, head)

    big = main_pages()
    return Tool(
        name=NAME, kinds=("owner",), run=run, timeout=300, max_output=TEXT_CHARS + 6000,
        title=lambda a: str(a.get("path") or a.get("url") or a.get("attachment") or "")[:80],
        classify=lambda a: levelled(NAME, (READ, L("只读一份 PDF（网址只去公网）；问看文档模型按量花一点钱，记在用量里",
                                                   "Only reads a PDF (web addresses: public internet only); asking the document model costs a little and is logged"))),
        description=L(
            "读一份 PDF：工作区里的、这个对话的附件（路径或 attachment id）、公网上的（url）。你能原样读 PDF 时（表格、图表、版面都在）"
            f"PDF 放进结果里（最多 {big} 页，多的用 pages 选）；不能时" + ("交给看文档的模型回答 question；" if on() else "抽出文字；")
            + "扫描件按页转成图来读。mode = text 只抽字（不花钱）。",
            "Read a PDF: from your workspace, an attachment of this conversation (its path or attachment id) or the public web (url). If you "
            f"can read PDFs as they are (tables, charts and layout intact), the PDF comes back in the result (up to {big} pages; pick more "
            "with pages); otherwise " + ("the document model answers question for you; " if on() else "its text is extracted; ")
            + "scanned pages are read as images. mode text only extracts the text (free)."),
        schema={"type": "object", "properties": {
            "path": {"type": "string", "description": L("工作区里的路径，或者消息里写着的附件路径", "A workspace path, or an attachment path a message gave")},
            "url": {"type": "string", "description": L("公网上一份 PDF 的网址", "Address of a PDF on the public web")},
            "attachment": {"type": "string", "description": L("这个对话的附件 id", "An attachment id from this conversation")},
            "question": {"type": "string", "description": L("想从里面知道什么（不写 = 讲一遍内容）", "What you want to know (empty = go through what it says)")},
            "pages": {"type": "string", "description": L("哪几页，比如 1-5,8（不写 = 全部）", "Which pages, like 1-5,8 (empty = all)")},
            "mode": {"type": "string", "enum": ["auto", "text"], "description": L("text = 只抽字", "text = extract the text only")}}})


async def extracted(ctx: ToolCtx, src: media.Source, data: bytes, rows: list[tuple[int, str]], head: str):
    """抽字那条路；扫描的页（字太少）有 pdftoppm 就转成图：主模型收图 → 图进结果；配了看图 → 看图模型读出来；都没有 → 说读不了。
    rows = [(原来的页码, 字)]，和 data（选过页的那一份）里的页一一对应。"""
    from . import vision
    scanned = [i for i, t in rows if len(t) < SCAN_CHARS]
    texty = [(i, t) for i, t in rows if t]  # 字少的页也照给（下面再转成图）；一个字都没有的不列
    body, last = joined(texty)
    notes: list[str] = []
    pics: list[dict] = []
    if texty and last < texty[-1][0]:
        notes.append(f"[Text cut after page {last} to fit; read on with pages={last + 1}-{rows[-1][0]}.]")
    if scanned:
        pos = {i: k + 1 for k, (i, _) in enumerate(rows)}  # 原来的页码 → 在 data 里是第几页
        want = scanned[:SCAN_PAGES]
        back = {pos[i]: i for i in want}
        shots = [(back[k], png) for k, png in await render(data, [pos[i] for i in want])]
        listed = ", ".join(map(str, scanned[:20])) + ("…" if len(scanned) > 20 else "")
        if not shots:
            notes.append(f"[Pages {listed} have no extractable text (probably scanned), and this server can't turn pages into images "
                         "(pdftoppm isn't installed).]")
        elif vision.main_sees(ctx):
            for i, png in shots:
                block, _ = await asyncio.to_thread(images.make, png, label=f"{src.label} page {i}", mime="image/png")
                if block is not None:
                    pics.append(block)
            notes.append(f"[Pages {', '.join(str(i) for i, _ in shots)} have no extractable text (probably scanned): they're attached as images.]")
        elif (vc := media.choice("vision")) is not None:
            for i, png in shots:
                try:
                    t = await vision.ask(vc, base64.b64encode(png).decode(), "image/png", SCAN, conversation=ctx.conversation, agent=ctx.agent)
                except media.MediaError as e:
                    t = f"(the vision model couldn't read this page: {e})"
                body += f"\n\n[Page {i}, read by the vision model {vc.ref}]\n{t}"
        else:
            notes.append(f"[Pages {listed} have no extractable text (probably scanned), and neither this model nor a vision model "
                         "(engine.use.vision) can read them as images.]")
        if len(scanned) > SCAN_PAGES:
            notes.append(f"[Only the first {SCAN_PAGES} scanned pages were turned into images; pick others with pages.]")
    text = f"{head} (extracted text)\n" + (media.wrap(_src(src.label), body.strip()) if body.strip() else "(no extractable text)")
    if notes:
        text += "\n" + "\n".join(notes)
    return ToolOutput(text, pics) if pics else text
