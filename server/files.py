"""对话附件：上传、存盘、抽文字 / 转写、给模型拼内容、回放给 app。

- 上限对齐主流 LLM 产品：一条消息最多 10 个附件、每个 30 MB；图片最多 8 张随消息直接给模型（Gateway 的上限）。
- 图片：原图存盘，给模型的是缩到 1600px 的 JPEG（省 token），app 里的缩略图 512px 按需生成。
- 文档：PDF / Word / Excel / PowerPoint / 文本 / 代码在服务端抽成文字，每个最多 60k 字、一条消息合计 150k 字，塞进发给 Grava 的消息里。
- 音频：OpenAI 转写（gpt-4o-transcribe，失败退 whisper-1），转写文字塞进消息；语音输入也走这条路（/api/chat/transcribe）。
- 其它类型：只存盘，消息里给路径，Grava 需要时用工具读。
- 文件存 ~/.openclaw/grava/uploads/<thread>/，记录在 grava.db 的 attachments 表；消息行的 attachments 列存给 app 显示的摘要。
"""
from __future__ import annotations

import base64
import io
import mimetypes
import os
import re
import sqlite3
import uuid
from pathlib import Path

import httpx
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from chat import _lock, db, now_iso

router = APIRouter()

from config import settings  # noqa: E402
from i18n import L  # noqa: E402

UPLOAD_DIR = settings.uploads
ENV_PATH = settings.env_file
MAX_FILES = 10
MAX_BYTES = 30 * 1024 * 1024
MAX_IMAGES = 8
IMAGE_MAX_SIDE = 1600
THUMB_SIDE = 512
TEXT_CHARS_PER_FILE = 60_000
TEXT_CHARS_TOTAL = 150_000
TRANSCRIBE_MODELS = ("gpt-4o-transcribe", "whisper-1")
# 给转写模型的词表提示（server.json 的 transcribe_prompt：你常说的专有名词，不提示的话"肌酸"会被听成"计算"）。
TRANSCRIBE_PROMPT = settings.transcribe_prompt
TRANSCRIBE_URL = settings.transcribe_url

DOC_EXT = {".pdf", ".docx", ".xlsx", ".xlsm", ".pptx", ".csv", ".tsv", ".txt", ".md", ".markdown", ".json", ".yaml", ".yml", ".xml", ".html",
           ".htm", ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".c", ".cpp", ".h", ".cs", ".go", ".rs", ".rb", ".php", ".sh", ".sql", ".r",
           ".ipynb", ".tex", ".rtf", ".log", ".ini", ".toml", ".cfg", ".env"}
AUDIO_EXT = {".m4a", ".mp3", ".wav", ".aac", ".ogg", ".oga", ".opus", ".flac", ".webm", ".caf", ".aiff", ".mp4a"}


def adb() -> sqlite3.Connection:
    conn = db()
    conn.execute("""CREATE TABLE IF NOT EXISTS attachments (
        id TEXT PRIMARY KEY, thread TEXT NOT NULL, message_id INTEGER, name TEXT NOT NULL, mime TEXT, size INTEGER NOT NULL,
        kind TEXT NOT NULL, path TEXT NOT NULL, text TEXT, chars INTEGER, note TEXT, status TEXT NOT NULL DEFAULT 'ok', created_at TEXT NOT NULL)""")
    return conn


def env_key(name: str) -> str:
    val = os.environ.get(name, "")
    if not val and ENV_PATH.is_file():
        for line in ENV_PATH.read_text(encoding="utf8").splitlines():
            if line.strip().startswith(name + "="):
                val = line.strip().split("=", 1)[1].strip().strip('"').strip("'")
    if not val:
        raise RuntimeError(L(f"缺少 {name}", f"Missing {name}"))
    return val


def safe_name(name: str) -> str:
    name = re.sub(r"[\\/\x00-\x1f]", "_", (name or "file").strip())[:120]
    return name or "file"


def kind_of(name: str, mime: str) -> str:
    ext = Path(name).suffix.lower()
    mime = (mime or "").lower()
    if mime.startswith("image/") or ext in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".heif", ".bmp", ".tiff"}:
        return "image"
    if mime.startswith("audio/") or ext in AUDIO_EXT:
        return "audio"
    if mime.startswith("video/") or ext in {".mov", ".mp4", ".m4v", ".avi", ".mkv"}:
        return "video"
    if ext in DOC_EXT or mime.startswith("text/") or mime in {"application/pdf", "application/json"}:
        return "doc"
    return "file"


def human(n: int) -> str:
    return f"{n / 1024 / 1024:.1f} MB" if n >= 1024 * 1024 else f"{max(1, n // 1024)} KB"


def count(n: int, word: str) -> str:
    """英文计数：1 page / 3 pages。"""
    return f"{n} {word}{'' if n == 1 else 's'}"


# —— 抽文字 ——

def extract_text(path: Path, kind: str, mime: str) -> tuple[str, str]:
    """返回 (文字, 备注)。抽不了就返回空串。"""
    ext = path.suffix.lower()
    try:
        if ext == ".pdf" or mime == "application/pdf":
            from pypdf import PdfReader
            reader = PdfReader(str(path))
            parts, n = [], len(reader.pages)
            for i, page in enumerate(reader.pages[:300]):
                parts.append(L(f"[第 {i + 1} 页]", f"[Page {i + 1}]") + f"\n{(page.extract_text() or '').strip()}")
                if sum(len(x) for x in parts) > TEXT_CHARS_PER_FILE:
                    break
            text = "\n\n".join(parts)
            note = L(f"PDF，{n} 页", f"PDF, {count(n, 'page')}")
            if len(text.replace(L("[第", "[Page"), "").strip()) < 50 * min(n, 3):
                note += L("，几乎没有可抽取的文字（可能是扫描件）", ", almost no extractable text (probably a scan)")
            return text, note
        if ext == ".docx":
            import docx
            d = docx.Document(str(path))
            parts = [p.text for p in d.paragraphs if p.text.strip()]
            for tbl in d.tables:
                for row in tbl.rows:
                    parts.append("\t".join(c.text.strip() for c in row.cells))
            return "\n".join(parts), L(f"Word，{len(d.paragraphs)} 段", f"Word, {count(len(d.paragraphs), 'paragraph')}")
        if ext in {".xlsx", ".xlsm"}:
            import openpyxl
            wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
            parts = []
            for ws in wb.worksheets:
                parts.append(L(f"## 工作表 {ws.title}", f"## Sheet {ws.title}"))
                for r, row in enumerate(ws.iter_rows(values_only=True)):
                    if r >= 500:
                        parts.append(L("…（只取前 500 行）", "…(first 500 rows only)"))
                        break
                    parts.append("\t".join("" if v is None else str(v) for v in row))
            return "\n".join(parts), L(f"Excel，{len(wb.worksheets)} 个工作表", f"Excel, {count(len(wb.worksheets), 'sheet')}")
        if ext == ".pptx":
            from pptx import Presentation
            prs = Presentation(str(path))
            parts = []
            for i, slide in enumerate(prs.slides):
                texts = [sh.text_frame.text for sh in slide.shapes if getattr(sh, "has_text_frame", False) and sh.text_frame.text.strip()]
                parts.append(L(f"[第 {i + 1} 页]", f"[Slide {i + 1}]") + "\n" + "\n".join(texts))
            return "\n\n".join(parts), L(f"PowerPoint，{len(prs.slides)} 页", f"PowerPoint, {count(len(prs.slides), 'slide')}")
        if kind == "doc":
            raw = path.read_bytes()[: TEXT_CHARS_PER_FILE * 4]
            return raw.decode("utf-8", "replace"), L("文本", "Text")
    except Exception as exc:  # noqa: BLE001 — 抽不出来不算失败，原文件还在
        return "", L(f"抽取失败：{str(exc)[:120]}", f"Couldn't extract text: {str(exc)[:120]}")
    return "", ""


def transcribe(path: Path, mime: str | None = None) -> str:
    """OpenAI 转写。中英混说没问题；失败抛 RuntimeError。"""
    key = env_key("OPENAI_API_KEY")
    last = None
    for model in TRANSCRIBE_MODELS:
        try:
            with path.open("rb") as fh:
                r = httpx.post(TRANSCRIBE_URL, headers={"Authorization": f"Bearer {key}"},
                               files={"file": (path.name, fh, mime or mimetypes.guess_type(path.name)[0] or "application/octet-stream")},
                               data={"model": model, "response_format": "json", "prompt": TRANSCRIBE_PROMPT}, timeout=180)
            if r.status_code == 200:
                return (r.json().get("text") or "").strip()
            last = f"HTTP {r.status_code}: {r.text[:200]}"
        except httpx.HTTPError as exc:
            last = str(exc)
    raise RuntimeError(L(f"转写失败：{last}", f"Transcription failed: {last}"))


# —— 图片 ——

def _open_image(path: Path):
    from PIL import Image
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
    except ImportError:
        pass
    img = Image.open(path)
    img.load()
    return img


def image_for_model(path: Path) -> tuple[str, str] | None:
    """缩到 1600px 的 JPEG data URL；打不开就 None。"""
    try:
        from PIL import ImageOps
        img = ImageOps.exif_transpose(_open_image(path))
        img.thumbnail((IMAGE_MAX_SIDE, IMAGE_MAX_SIDE))
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=85, optimize=True)
        return "image/jpeg", base64.b64encode(buf.getvalue()).decode()
    except Exception:  # noqa: BLE001
        return None


def thumbnail(path: Path) -> Path | None:
    out = path.with_name(path.name + ".thumb.jpg")
    if out.is_file():
        return out
    try:
        from PIL import ImageOps
        img = ImageOps.exif_transpose(_open_image(path))
        img.thumbnail((THUMB_SIDE, THUMB_SIDE))
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        img.save(out, "JPEG", quality=80)
        return out
    except Exception:  # noqa: BLE001
        return None


# —— 上传 ——

def summary(r: sqlite3.Row | dict) -> dict:
    return {"id": r["id"], "name": r["name"], "mime": r["mime"], "size": r["size"], "kind": r["kind"], "chars": r["chars"], "note": r["note"],
            "status": r["status"], "url": f"/api/files/{r['id']}"}


@router.post("/api/chat/upload")
async def upload(thread: str = Form("main"), files: list[UploadFile] = File(...)):
    if len(files) > MAX_FILES:
        raise HTTPException(400, L(f"一条消息最多 {MAX_FILES} 个附件", f"Up to {MAX_FILES} attachments per message"))
    out = []
    for f in files:
        name = safe_name(f.filename or "file")
        mime = (f.content_type or mimetypes.guess_type(name)[0] or "application/octet-stream").lower()
        fid = uuid.uuid4().hex[:12]
        folder = UPLOAD_DIR / re.sub(r"[^A-Za-z0-9_-]", "_", thread)[:40]
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{fid}-{name}"
        size = 0
        with path.open("wb") as fh:
            while chunk := await f.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_BYTES:
                    fh.close()
                    path.unlink(missing_ok=True)
                    raise HTTPException(413, L(f"{name} 超过 {MAX_BYTES // 1024 // 1024} MB", f"{name} is over {MAX_BYTES // 1024 // 1024} MB"))
                fh.write(chunk)
        kind = kind_of(name, mime)
        text, note, status = "", "", "ok"
        if kind == "doc":
            text, note = extract_text(path, kind, mime)
        elif kind == "audio":
            try:
                text = transcribe(path, mime)
                note = L("已转写", "Transcribed")
            except RuntimeError as exc:
                note, status = str(exc)[:160], "warn"
        elif kind == "image":
            note = L("图片", "Image") if image_for_model(path) else L("图片（打不开，只存了文件）", "Image (couldn't open it, file saved only)")
        text = text[:TEXT_CHARS_PER_FILE]
        ts = now_iso()
        with _lock, adb() as conn:
            conn.execute("INSERT INTO attachments(id, thread, name, mime, size, kind, path, text, chars, note, status, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                         (fid, thread, name, mime, size, kind, str(path), text or None, len(text) if text else None, note or None, status, ts))
        out.append({"id": fid, "name": name, "mime": mime, "size": size, "kind": kind, "chars": len(text) if text else None, "note": note or None,
                    "status": status, "url": f"/api/files/{fid}", "text": text if kind == "audio" else None})
    return {"ok": True, "attachments": out}


@router.post("/api/chat/transcribe")
async def transcribe_endpoint(file: UploadFile = File(...)):
    """语音输入：录音传上来，回文字，不存盘。"""
    name = safe_name(file.filename or "voice.m4a")
    tmp = UPLOAD_DIR / "_voice"
    tmp.mkdir(parents=True, exist_ok=True)
    path = tmp / f"{uuid.uuid4().hex[:8]}-{name}"
    size = 0
    try:
        with path.open("wb") as fh:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise HTTPException(413, L("录音太大", "Recording is too large"))
                fh.write(chunk)
        try:
            text = transcribe(path, file.content_type)
        except RuntimeError as exc:
            raise HTTPException(502, str(exc)) from exc
    finally:
        path.unlink(missing_ok=True)
    return {"ok": True, "text": text, "seconds_hint": round(size / 16000, 1)}


@router.get("/api/files/{fid}")
def get_file(fid: str, thumb: int = 0):
    with _lock, adb() as conn:
        r = conn.execute("SELECT * FROM attachments WHERE id=?", (fid,)).fetchone()
    if not r or not Path(r["path"]).is_file():
        raise HTTPException(404, L("文件不在了", "File no longer exists"))
    path = Path(r["path"])
    if thumb and r["kind"] == "image":
        t = thumbnail(path)
        if t:
            return FileResponse(str(t), media_type="image/jpeg")
    return FileResponse(str(path), media_type=r["mime"] or "application/octet-stream", filename=r["name"])


# —— 给模型拼消息 ——

def load_pending(thread: str, ids: list[str]) -> list[sqlite3.Row]:
    if not ids:
        return []
    if len(ids) > MAX_FILES:
        raise HTTPException(400, L(f"一条消息最多 {MAX_FILES} 个附件", f"Up to {MAX_FILES} attachments per message"))
    with _lock, adb() as conn:
        rows = conn.execute(f"SELECT * FROM attachments WHERE id IN ({','.join('?' * len(ids))}) AND thread=? AND message_id IS NULL", (*ids, thread)).fetchall()
    found = {r["id"]: r for r in rows}
    missing = [i for i in ids if i not in found]
    if missing:
        raise HTTPException(400, L(f"附件已失效，请重新添加：{', '.join(missing)}", f"Attachment expired, please add it again: {', '.join(missing)}"))
    return [found[i] for i in ids]


def build_content(text: str, rows: list[sqlite3.Row]) -> tuple[str | list, str]:
    """返回 (发给 Gateway 的 content, 纯文字版)。有图片就是 OpenAI 的 content 数组，否则是字符串。"""
    if not rows:
        return text, text
    first = text.strip()
    if first == "（见附件）":  # chat.py 只有附件时存的占位：app 按原文比较，存库的不动；发给模型的这份按语言给
        first = L(first, "(see attachments)")
    lines = [first] if first else []
    images: list[tuple[str, str]] = []
    budget = TEXT_CHARS_TOTAL
    for i, r in enumerate(rows, 1):
        path = Path(r["path"])
        head = L(f"[附件 {i}] {r['name']}（{r['note'] or r['kind']}，{human(r['size'])}）", f"[Attachment {i}] {r['name']} ({r['note'] or r['kind']}, {human(r['size'])})")
        if r["kind"] == "image" and len(images) < MAX_IMAGES and (im := image_for_model(path)):
            images.append(im)
            lines.append(L(f"{head} 已随消息附上，第 {len(images)} 张图。", f"{head} is attached to this message as image {len(images)}."))
        elif r["text"]:
            body = r["text"][: max(0, budget)]
            budget -= len(body)
            cut = "" if len(body) == len(r["text"]) else L(f"\n…（只截了前 {len(body)} 字，全文在 {path}）", f"\n…(only the first {len(body)} characters; full text at {path})")
            label = L("转写", "transcript") if r["kind"] == "audio" else L("内容", "content")
            lines.append(L(f"{head} {label}如下：\n<<<\n{body}{cut}\n>>>", f"{head} {label} below:\n<<<\n{body}{cut}\n>>>"))
        else:
            lines.append(L(f"{head} 存在 {path}，需要时用工具读取。", f"{head} is saved at {path}; read it with your tools if needed."))
    plain = "\n\n".join(lines)
    if not images:
        return plain, plain
    return [{"type": "text", "text": plain}, *({"type": "image_url", "image_url": {"url": f"data:{m};base64,{b}"}} for m, b in images)], plain


def bind(rows: list[sqlite3.Row], message_id: int) -> list[dict]:
    with _lock, adb() as conn:
        for r in rows:
            conn.execute("UPDATE attachments SET message_id=? WHERE id=?", (message_id, r["id"]))
    return [summary(r) for r in rows]
