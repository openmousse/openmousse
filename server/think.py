"""思考空间（2026-09-27）：想到什么先扔进来，AI 不碰，等你叫它。另见 saves.py（收藏）。

想法（碎片）
- 一句话、几个关键词、语音（转文字，原声留着）、照片、文件、链接、长文。只存，不发给模型：不花额度，也不进任何 Agent 的记忆。
- 一条碎片 = 一篇 Markdown 笔记。server.json 的 think.vault 配了库（Leo：~/vault，Obsidian 双向同步）就放库的收件箱，
  没配放 <data_dir>/think/。文件夹名按语言默认（中文：收件箱 / 收件箱/已想完 / 收件箱/附件 / 笔记 / 写作），think.* 可以改。
  属性：id / kind / created_at / source / keywords（原样）/ tags（去掉空格，给 Obsidian 的标签面板）/ topics / note / files / url …
  正文就是那段话；附件在正文末尾嵌成 ![[…]]，Obsidian 里看得到。Obsidian 里新建的（没有属性的）也收：id 按路径算。
- 读：每次按文件的 mtime + 大小看有没有变，变了才重读（内存缓存）。obsidian-headless 落盘不是原子写，1 秒内刚改的等下一轮。
  写：一律临时文件（. 开头）+ rename。别人的属性（Leo 在 Obsidian 里加的）原样保留。
- 想完的挪进「已想完」（不删）；删除 = 挪进库的 .trash/（Obsidian 自己的回收站）。
- 关键词：属性 keywords + 正文里的 #xxx。点一个关键词看所有带它的想法和收藏（saves.py），还有常一起出现的词。

主题（聊聊 / 想完了）
- 勾几条碎片点「聊聊」或「想完了」= 建一个主题（grava.db think_topics，id tp-xxxxxxxx 也是对话线程 id，会话 agent:main:grava:tp-…）。
- 聊聊：先发一句「【自动触发】聊聊」让它先问；每天第一句话、主题里的碎片变了以后，chat.start_run 调 context_for 把碎片和
  「陪你想」的规矩拼在消息前面（只给模型看，对话里不显示）。
- 只记下：聊的时候记一句不给它看 = 一条碎片（note: true，挂在这个主题上），对话里显示一行虚线气泡（messages 里 role=auto、
  【只记下】开头，不发给 Gateway），context_for 不带它；想完了时一起用。
- 想完了：后台让模型整理成笔记草稿（JSON），存 think_topics.draft；用户改完「存进库」= 写进 笔记/ 或 写作/，碎片挪进已想完，
  要记的写一片世界树叶子（workspace 的 memory_tree.py），要做的加进日程（app 直接调 /api/schedule）。

搜索和历史
- 想法、收藏、聊过的主题、存进库的笔记一起搜：内存里按字面找（量小；中文两个字也能搜），不调模型。结果里给高亮的分段。
- 按天：某个月每天记了几条想法、几条收藏；某一天的全部。

冥想时间
- think_focus：开始时记下到几点结束（25 / 45 / 90 分钟，不限 = 3 小时）。期间 push.send_push 一律不推，记进 think_focus_held；
  结束（点结束或到点）给一份小结：压住的推送、等你点头的、接下来的日程。开始前先看这段时间里有没有日程和截止。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import threading
import time
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx
import yaml
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

import chat
import config
from chat import _lock, db, log_activity, now_iso
from config import TZ, settings
from i18n import L, LS

router = APIRouter()

TEXT_MAX = 20_000          # 一条碎片正文最多多少字（长文也够）
TITLE_MAX = 80
KW_MAX, KW_LEN = 12, 30     # 一条最多几个关键词、每个多长
VAULT_FILE_MAX = 5 * 1024 * 1024   # 放进库的附件上限（Obsidian Sync 单文件 5 MB），大的留服务器
UPLOAD_MAX = 30 * 1024 * 1024
FRESH_SECONDS = 1.0        # 这么久之内刚改过的笔记先不读（同步可能还没写完）
FOCUS_MINUTES = (25, 45, 90, 0)
FOCUS_MAX = 180            # 不限 = 最长 3 小时
MARK_TALK = "【自动触发】聊聊"    # 协议标记：app 按「【自动触发】」显示成一行灰字
MARK_NOTE = "【只记下】"          # 对话里的虚线气泡（app 按它认，不翻译）
ID_RE = re.compile(r"^th-[0-9a-f]{8}$")
TOPIC_RE = re.compile(r"^tp-[0-9a-f]{8}$")
HASHTAG = re.compile(r"(?<![A-Za-z0-9/&=?._:~%+-])#([^\s#，。、；：！？,.;:!?()（）\[\]【】「」『』\"'`<>…～]+)")
EMBED = re.compile(r"^!\[\[([^\]|]+)(?:\|[^\]]*)?\]\]\s*$")
FM = re.compile(r"\A---[ \t]*\n(.*?)\n---[ \t]*(?:\n|\Z)", re.S)
KINDS = ("text", "keywords", "voice", "photo", "file", "link", "long", "save")


# —— 位置 ——————————————————————————————————————————————————————————

def cfg() -> dict:
    c = config.raw().get("think")
    return c if isinstance(c, dict) else {}


def zh() -> bool:
    return settings.language == "zh"


def vault() -> Path | None:
    v = cfg().get("vault")
    return Path(v).expanduser() if v else None


def base_dir() -> Path:
    return vault() or (settings.data_dir / "think")


def _folder(key: str, zh_name: str, en_name: str) -> Path:
    return base_dir() / (cfg().get(key) or (zh_name if zh() else en_name))


def inbox_dir() -> Path:
    return _folder("inbox_dir", "收件箱", "Inbox")


def done_dir() -> Path:
    return inbox_dir() / (cfg().get("done_dir") or ("已想完" if zh() else "Done"))


def attach_dir() -> Path:
    return inbox_dir() / (cfg().get("attach_dir") or ("附件" if zh() else "Attachments"))


def notes_dir() -> Path:
    return _folder("notes_dir", "笔记", "Notes")


def writing_dir() -> Path:
    return _folder("writing_dir", "写作", "Writing")


def big_dir() -> Path:
    """放不进库的大附件（> 5 MB）和缩略图、转写用的临时文件。"""
    return settings.data_dir / "think"


def trash_dir() -> Path:
    return base_dir() / ".trash"


def rel(p: Path) -> str:
    try:
        return str(p.relative_to(base_dir()))
    except ValueError:
        return str(p)


def inside(p: Path, root: Path) -> bool:
    try:
        p.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


# —— 笔记的读写 ——————————————————————————————————————————————————————

def parse_note(raw: str) -> tuple[dict, str, bool]:
    """(属性, 正文, 属性坏了没)。没有属性块就是 {}。"""
    m = FM.match(raw)
    if not m:
        return {}, raw, False
    try:
        meta = yaml.safe_load(m.group(1)) or {}
        if not isinstance(meta, dict):
            return {}, raw[m.end():], True
    except yaml.YAMLError:
        return {}, raw[m.end():], True
    return meta, raw[m.end():], False


def dump_note(meta: dict, body: str) -> str:
    clean = {k: v for k, v in meta.items() if v not in (None, "", [], {})}
    head = yaml.safe_dump(clean, allow_unicode=True, sort_keys=False, default_flow_style=False, width=4096).strip()
    return f"---\n{head}\n---\n{body.strip()}\n"


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:6]}.tmp")
    tmp.write_text(text, encoding="utf8")
    os.replace(tmp, path)


def safe_file_part(text: str, n: int = 24) -> str:
    s = re.sub(r"[\\/:*?\"<>|#^\[\]\n\r\t]+", " ", text or "").strip()
    s = re.sub(r"\s+", " ", s)[:n].strip(" .")
    return s


def unique_path(folder: Path, stem: str, ext: str = ".md") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / f"{stem}{ext}"
    i = 2
    while p.exists():
        p = folder / f"{stem} {i}{ext}"
        i += 1
    return p


def as_iso(v) -> str | None:
    if isinstance(v, datetime):
        return (v if v.tzinfo else v.replace(tzinfo=TZ)).isoformat(timespec="seconds")
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day, tzinfo=TZ).isoformat(timespec="seconds")
    if isinstance(v, str) and v.strip():
        try:
            d = datetime.fromisoformat(v.strip().replace("Z", "+00:00"))
            return (d if d.tzinfo else d.replace(tzinfo=TZ)).isoformat(timespec="seconds")
        except ValueError:
            return None
    return None


def local(ts: str) -> datetime:
    try:
        return datetime.fromisoformat(ts).astimezone(TZ)
    except (ValueError, TypeError):
        return datetime.now(TZ)


# —— 关键词 ————————————————————————————————————————————————————————

def kw_norm(k: str) -> str:
    return re.sub(r"\s+", "", k or "").lower()


def clean_keywords(items) -> list[str]:
    out, seen = [], set()
    for k in items or []:
        k = re.sub(r"\s+", " ", str(k or "")).strip().lstrip("#").strip()[:KW_LEN]
        if k and kw_norm(k) not in seen:
            seen.add(kw_norm(k))
            out.append(k)
    return out[:KW_MAX]


def inline_tags(text: str) -> list[str]:
    return [m.rstrip("-_/") for m in HASHTAG.findall(text or "") if m.strip("-_/") and not m.isdigit()]


def obsidian_tags(keywords: list[str]) -> list[str]:
    """Obsidian 的标签不能有空格和大多数标点，也不能全是数字。"""
    out = []
    for k in keywords:
        t = re.sub(r"[^\w\-/]", "", k.replace(" ", ""))
        if t and not t.isdigit() and t not in out:
            out.append(t)
    return out


# —— 碎片 ——————————————————————————————————————————————————————————

_cache: dict[str, tuple[int, int, dict]] = {}
_scan_lock = threading.Lock()


def kind_of_file(name: str, mime: str = "") -> str:
    import files as files_mod
    k = files_mod.kind_of(name, mime)
    return {"image": "photo", "audio": "voice"}.get(k, k)


def resolve_embed(target: str, near: Path) -> Path | None:
    """![[…]] 指向的文件：库里的相对路径，或者只有文件名（Obsidian 的最短路径）就在附件夹、这篇旁边、库根里找。"""
    target = target.strip()
    root = base_dir()
    cands = [root / target] if "/" in target else [attach_dir() / target, near.parent / target, root / target]
    for c in cands:
        if c.is_file() and inside(c, root):
            return c
    return None


def frag_from_file(path: Path, st: os.stat_result) -> dict:
    raw = path.read_text(encoding="utf8", errors="replace")
    meta, body, bad = parse_note(raw)
    fid = str(meta.get("id") or "")
    if not ID_RE.match(fid):
        fid = "th-" + hashlib.sha1(rel(path).encode("utf8")).hexdigest()[:8]
    files: list[dict] = []
    lines = []
    for line in body.splitlines():
        m = EMBED.match(line.strip())
        if m:
            p = resolve_embed(m.group(1), path)
            if p is not None:
                files.append({"path": rel(p), "name": p.name, "kind": kind_of_file(p.name), "size": p.stat().st_size})
                continue
        lines.append(line)
    for extra in meta.get("big_files") or []:  # 放不进库的大附件：在服务器上
        p = big_dir() / "files" / str(extra)
        if p.is_file():
            files.append({"path": f"@big/{extra}", "name": p.name.split("-", 1)[-1], "kind": kind_of_file(p.name), "size": p.stat().st_size})
    text = "\n".join(lines).strip()
    kind = str(meta.get("kind") or "")
    if kind not in KINDS:
        kind = "long" if len(text) > 280 else ("photo" if files and files[0]["kind"] == "photo" and not text else "text")
    keywords = clean_keywords(list(meta.get("keywords") or []) + inline_tags(text))
    if kind == "keywords" and not meta.get("keywords"):
        keywords = clean_keywords(re.split(r"\s*[·、,，]\s*|\s{2,}", text))
    created = as_iso(meta.get("created_at"))
    if not created:  # Obsidian 里新建的：文件名开头是日期就用它（「2026-09-27 1440 …」），不然用修改时间
        m = re.match(r"^(\d{4}-\d{2}-\d{2})(?:[ T_]?(\d{2})[:.]?(\d{2}))?", path.stem)
        created = (datetime.fromisoformat(f"{m.group(1)}T{m.group(2) or '00'}:{m.group(3) or '00'}").replace(tzinfo=TZ).isoformat(timespec="seconds")
                   if m else datetime.fromtimestamp(st.st_mtime, TZ).isoformat(timespec="seconds"))
    in_done = inside(path, done_dir())
    title = str(meta.get("title") or "")
    if kind == "long" and not title:
        title = re.sub(r"^\d{4}-\d{2}-\d{2} \d{4} ", "", path.stem)
    return {
        "id": fid, "path": rel(path), "kind": kind, "text": text, "title": title[:TITLE_MAX], "keywords": keywords,
        "createdAt": created, "status": "done" if in_done or meta.get("status") == "done" else "open",
        "topics": [str(t) for t in (meta.get("topics") or []) if TOPIC_RE.match(str(t))], "note": bool(meta.get("note")),
        "files": files, "url": str(meta.get("url") or ""), "linkTitle": str(meta.get("link_title") or ""),
        "duration": meta.get("duration") if isinstance(meta.get("duration"), (int, float)) else None,
        "source": str(meta.get("source") or ("app" if meta.get("id") else "obsidian")), "save": str(meta.get("save") or ""),
        "bad": bad, "mtime": st.st_mtime, "chars": len(text),
    }


def scan() -> list[dict]:
    """收件箱和已想完里的全部碎片（按 mtime + 大小缓存）。隐藏文件、子文件夹（附件）不收。"""
    now = time.time()
    seen: set[str] = set()
    out: list[dict] = []
    with _scan_lock:
        for folder in (inbox_dir(), done_dir()):
            if not folder.is_dir():
                continue
            for p in folder.iterdir():
                if p.name.startswith(".") or p.suffix.lower() != ".md" or not p.is_file() or p.is_symlink():
                    continue
                key = str(p)
                try:
                    st = p.stat()
                except OSError:
                    continue
                hit = _cache.get(key)
                if hit and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
                    frag = hit[2]
                elif now - st.st_mtime < FRESH_SECONDS and hit:
                    frag = hit[2]  # 同步可能还没写完：先用上一版
                else:
                    try:
                        frag = frag_from_file(p, st)
                    except OSError:
                        continue
                    _cache[key] = (st.st_mtime_ns, st.st_size, frag)
                seen.add(key)
                out.append(frag)
        for key in [k for k in _cache if k not in seen]:
            _cache.pop(key, None)
    out.sort(key=lambda f: f["createdAt"], reverse=True)
    return out


def frag_by_id(fid: str) -> dict:
    for f in scan():
        if f["id"] == fid:
            return f
    raise HTTPException(404, L("没有这条想法（可能在 Obsidian 里删了或改了名）", "No such thought (maybe deleted or renamed in Obsidian)"))


def frag_path(f: dict) -> Path:
    return base_dir() / f["path"]


def file_url(fid: str, i: int) -> str:
    return f"/api/think/file/{fid}/{i}"


def frag_json(f: dict, full: bool = True) -> dict:
    d = local(f["createdAt"])
    text = f["text"] if full or len(f["text"]) <= 400 else f["text"][:400] + "…"
    return {
        "id": f["id"], "kind": f["kind"], "text": text, "title": f["title"], "keywords": f["keywords"], "createdAt": f["createdAt"],
        "day": d.strftime("%Y-%m-%d"), "time": d.strftime("%H:%M"), "status": f["status"], "topics": f["topics"], "note": f["note"],
        "files": [{"name": x["name"], "kind": x["kind"], "size": x["size"], "url": file_url(f["id"], i)} for i, x in enumerate(f["files"])],
        "url": f["url"], "linkTitle": f["linkTitle"], "duration": f["duration"], "source": f["source"], "save": f["save"] or None,
        "chars": f["chars"], "bad": f["bad"], "path": f["path"],
    }


def stem_for(kind: str, text: str, title: str, keywords: list[str], when: datetime) -> str:
    head = title or (" ".join(keywords) if kind == "keywords" else text) or {"voice": LS("语音", "Voice"), "photo": LS("照片", "Photo"),
                                                                              "file": LS("文件", "File"), "link": LS("链接", "Link")}.get(kind, "")
    return f"{when:%Y-%m-%d %H%M} {safe_file_part(head) or kind}".strip()


def store_file(src: Path, name: str, when: datetime) -> tuple[str | None, str | None]:
    """附件落地：5 MB 以内放进库的附件夹（返回库里的相对路径），大的放服务器（返回 big 名字）。src 会被移走。"""
    name = safe_file_part(Path(name).stem, 40) + Path(name).suffix.lower()
    if src.stat().st_size <= VAULT_FILE_MAX:
        dst = unique_path(attach_dir(), f"{when:%Y-%m-%d %H%M%S} {Path(name).stem}", Path(name).suffix or ".bin")
        os.replace(src, dst)
        return rel(dst), None
    folder = big_dir() / "files"
    folder.mkdir(parents=True, exist_ok=True)
    big = f"{uuid.uuid4().hex[:8]}-{name}"
    os.replace(src, folder / big)
    return None, big


def create_fragment(*, kind: str, text: str = "", keywords: list[str] | None = None, title: str = "", url: str = "", link_title: str = "",
                    files: list[tuple[Path, str]] | None = None, duration: float | None = None, topics: list[str] | None = None,
                    note: bool = False, source: str = "app", save: str = "", when: datetime | None = None) -> dict:
    """新建一条碎片（一篇笔记）。files：[(临时文件, 原名)]，会被挪进库的附件夹。"""
    if kind not in KINDS:
        raise HTTPException(400, L("不认识的种类", "Unknown kind"))
    text = (text or "").strip()[:TEXT_MAX]
    title = re.sub(r"\s+", " ", title or "").strip()[:TITLE_MAX]
    kws = clean_keywords(list(keywords or []) + inline_tags(text))
    if kind == "keywords":
        if not kws:
            raise HTTPException(400, L("关键词是空的", "No keywords"))
        text = " · ".join(kws)
    if not (text or files or url or title):
        raise HTTPException(400, L("是空的", "It's empty"))
    when = when or datetime.now(TZ)
    fid = f"th-{uuid.uuid4().hex[:8]}"
    embeds, big = [], []
    for src, name in files or []:
        r, b = store_file(src, name, when)
        if r:
            embeds.append(f"![[{r}]]")
        if b:
            big.append(b)
    meta = {"id": fid, "kind": kind, "created_at": when.isoformat(timespec="seconds"), "source": source, "title": title,
            "keywords": kws, "tags": obsidian_tags(kws), "topics": topics or [], "note": True if note else None,
            "url": url, "link_title": link_title, "duration": round(duration, 1) if duration else None, "save": save,
            "big_files": big}
    body = "\n\n".join(x for x in [text, "\n".join(embeds)] if x)
    path = unique_path(inbox_dir(), stem_for(kind, text, title, kws, when))
    write_atomic(path, dump_note(meta, body))
    return frag_json(frag_from_file(path, path.stat()))


def rewrite(f: dict, change) -> dict:
    """改一篇碎片：change(meta, body) 就地改，返回新的碎片。Leo 自己加的属性不动。"""
    path = frag_path(f)
    raw = path.read_text(encoding="utf8", errors="replace")
    meta, body, bad = parse_note(raw)
    if bad:
        raise HTTPException(409, L("这篇笔记的属性格式坏了，先在 Obsidian 里修好", "This note's properties are malformed. Fix it in Obsidian first."))
    if not meta.get("id"):
        meta = {"id": f["id"], **meta}
    body = change(meta, body) or body
    write_atomic(path, dump_note(meta, body))
    return frag_from_file(path, path.stat())


def move_to(f: dict, folder: Path) -> Path:
    src = frag_path(f)
    if src.parent == folder:
        return src
    dst = unique_path(folder, src.stem)
    folder.mkdir(parents=True, exist_ok=True)
    os.replace(src, dst)
    return dst


def set_topics(f: dict, add: str | None = None, remove: str | None = None) -> None:
    def change(meta: dict, body: str) -> str:
        cur = [str(t) for t in meta.get("topics") or []]
        if add and add not in cur:
            cur.append(add)
        if remove:
            cur = [t for t in cur if t != remove]
        meta["topics"] = cur
        return body
    rewrite(f, change)


# —— 碎片的接口 ————————————————————————————————————————————————————————

def topics_open() -> list[dict]:
    with _lock, tdb() as conn:
        rows = conn.execute("SELECT * FROM think_topics WHERE status='open' ORDER BY updated_at DESC LIMIT 20").fetchall()
    return [topic_brief(r) for r in rows]


@router.get("/api/think/stream")
async def stream(before: str | None = None, limit: int = 80, status: str = "open"):
    """想法页：碎片（新的在前）+ 在想的主题 + 收藏里没看的数量。status=open（默认，只看没想完的）/ all。"""
    frags = await asyncio.to_thread(scan)
    if status != "all":
        frags = [f for f in frags if f["status"] == "open"]
    frags = [f for f in frags if not f["note"]]  # 只记下的在它的主题里显示
    if before:
        frags = [f for f in frags if f["createdAt"] < before]
    page = frags[: max(1, min(limit, 200))]
    import saves
    return {"ok": True, "fragments": [frag_json(f, full=False) for f in page], "more": len(frags) > len(page),
            "topics": await asyncio.to_thread(topics_open), "savesNew": await asyncio.to_thread(saves.new_count),
            "vault": vault() is not None, "folder": rel(inbox_dir())}


@router.get("/api/think/fragments/{fid}")
async def get_fragment(fid: str):
    return {"ok": True, "fragment": frag_json(await asyncio.to_thread(frag_by_id, fid))}


class FragmentIn(BaseModel):
    kind: str = "text"
    text: str = ""
    title: str = ""
    keywords: list[str] = []
    url: str = ""
    topic: str | None = None       # 只记下：挂到这个主题上（对话里出一行）


@router.post("/api/think/fragments")
async def post_fragment(body: FragmentIn):
    kind = body.kind if body.kind in KINDS else "text"
    if kind == "text" and len(body.text) > 280 and body.title:
        kind = "long"
    if body.url and kind == "text" and not body.text.strip():
        kind = "link"
    note = False
    topics: list[str] = []
    if body.topic:
        load_topic(body.topic)
        note, topics = True, [body.topic]
    frag = await asyncio.to_thread(create_fragment, kind=kind, text=body.text, title=body.title, keywords=body.keywords, url=body.url.strip(),
                                   topics=topics, note=note)
    if body.topic:
        await asyncio.to_thread(add_note_to_topic, body.topic, frag)
    return {"ok": True, "fragment": frag}


def save_upload(f: UploadFile) -> tuple[Path, str, int]:
    tmp_dir = big_dir() / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    name = Path(f.filename or "file").name or "file"
    tmp = tmp_dir / f"{uuid.uuid4().hex}{Path(name).suffix.lower()}"
    size = 0
    with tmp.open("wb") as out:
        while chunk := f.file.read(1024 * 1024):
            size += len(chunk)
            if size > UPLOAD_MAX:
                out.close()
                tmp.unlink(missing_ok=True)
                raise HTTPException(413, L(f"{name} 超过 30 MB", f"{name} is over 30 MB"))
            out.write(chunk)
    return tmp, name, size


@router.post("/api/think/fragments/upload")
async def upload_fragment(files: list[UploadFile] = File(...), text: str = Form(""), kind: str = Form(""), keywords: str = Form(""),
                          duration: float | None = Form(None)):
    """带附件的一条：照片、文件、语音（kind=voice：转成文字当正文，原声留着）。keywords 是 JSON 数组。"""
    if not files or len(files) > 10:
        raise HTTPException(400, L("一次 1 到 10 个文件", "1 to 10 files at a time"))
    saved = [save_upload(f) for f in files]
    try:
        kws = json.loads(keywords) if keywords.strip() else []
    except ValueError:
        kws = []
    if not kind:
        kind = kind_of_file(saved[0][1], files[0].content_type or "")
        kind = kind if kind in ("photo", "voice") else "file"
    body = text
    if kind == "voice":
        import files as files_mod
        try:
            said = await asyncio.to_thread(files_mod.transcribe, saved[0][0], files[0].content_type or "audio/m4a")
        except Exception as exc:  # noqa: BLE001 — 转写失败：原声照存，正文写一句
            said = L(f"（没转成文字：{str(exc)[:80]}）", f"(Couldn't transcribe: {str(exc)[:80]})")
        body = f"{text.strip()}\n\n{said}".strip() if text.strip() else said
    frag = await asyncio.to_thread(create_fragment, kind=kind, text=body, keywords=kws, files=[(p, n) for p, n, _ in saved], duration=duration)
    return {"ok": True, "fragment": frag}


class FragmentPatch(BaseModel):
    text: str | None = None
    title: str | None = None
    keywords: list[str] | None = None


@router.patch("/api/think/fragments/{fid}")
async def patch_fragment(fid: str, body: FragmentPatch):
    f = await asyncio.to_thread(frag_by_id, fid)

    def change(meta: dict, old: str) -> str:
        embeds = [ln for ln in old.splitlines() if EMBED.match(ln.strip())]
        text = old
        if body.text is not None:
            text = "\n\n".join(x for x in [body.text.strip()[:TEXT_MAX], "\n".join(embeds)] if x)
        if body.title is not None:
            meta["title"] = re.sub(r"\s+", " ", body.title).strip()[:TITLE_MAX]
        if body.keywords is not None or body.text is not None:
            base = body.keywords if body.keywords is not None else list(meta.get("keywords") or [])
            kws = clean_keywords(list(base) + inline_tags(body.text if body.text is not None else ""))
            meta["keywords"], meta["tags"] = kws, obsidian_tags(kws)
            if meta.get("kind") == "keywords" and body.text is None:
                text = "\n\n".join(x for x in [" · ".join(kws), "\n".join(embeds)] if x)
        meta["updated_at"] = now_iso()
        return text

    fresh = await asyncio.to_thread(rewrite, f, change)
    bump_topics(fresh["topics"])
    return {"ok": True, "fragment": frag_json(fresh)}


@router.delete("/api/think/fragments/{fid}")
async def delete_fragment(fid: str):
    """挪进库的 .trash/（Obsidian 的回收站），附件留着。"""
    f = await asyncio.to_thread(frag_by_id, fid)
    await asyncio.to_thread(move_to, f, trash_dir())
    for tid in f["topics"]:
        with _lock, tdb() as conn:
            r = conn.execute("SELECT fragments FROM think_topics WHERE id=?", (tid,)).fetchone()
            if r:
                ids = [x for x in json.loads(r["fragments"] or "[]") if x != fid]
                conn.execute("UPDATE think_topics SET fragments=?, rev=rev+1, updated_at=? WHERE id=?", (json.dumps(ids), now_iso(), tid))
    return {"ok": True}


@router.get("/api/think/file/{fid}/{index}")
async def get_file(fid: str, index: int, thumb: int = 0):
    f = await asyncio.to_thread(frag_by_id, fid)
    if index < 0 or index >= len(f["files"]):
        raise HTTPException(404, L("没有这个附件", "No such attachment"))
    x = f["files"][index]
    path = big_dir() / "files" / x["path"][5:] if x["path"].startswith("@big/") else base_dir() / x["path"]
    if not path.is_file():
        raise HTTPException(404, L("文件不在了", "File no longer exists"))
    if thumb and x["kind"] == "photo":
        t = await asyncio.to_thread(thumb_of, path)
        if t:
            return FileResponse(str(t), media_type="image/jpeg")
    import mimetypes
    return FileResponse(str(path), media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream", filename=path.name)


def thumb_of(path: Path) -> Path | None:
    """缩略图放服务器的缓存里，不往库里写（库里的每个文件都会同步到手机）。"""
    out = big_dir() / "thumbs" / (hashlib.sha1(f"{path}:{path.stat().st_mtime_ns}".encode()).hexdigest()[:16] + ".jpg")
    if out.is_file():
        return out
    try:
        import files as files_mod
        from PIL import ImageOps
        img = ImageOps.exif_transpose(files_mod._open_image(path))  # noqa: SLF001 — 同一个项目里的小工具
        img.thumbnail((files_mod.THUMB_SIDE, files_mod.THUMB_SIDE))
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        out.parent.mkdir(parents=True, exist_ok=True)
        img.save(out, "JPEG", quality=80)
        return out
    except Exception:  # noqa: BLE001
        return None


class WriteIn(BaseModel):
    title: str = ""
    text: str
    folder: str = "writing"        # writing：直接存进库的「写作」当素材


@router.post("/api/think/notes")
async def post_note(body: WriteIn):
    """全屏写字板的「存进写作」：直接写一篇笔记到库的 写作/（不进收件箱）。"""
    text = body.text.strip()
    if not text:
        raise HTTPException(400, L("是空的", "It's empty"))
    title = re.sub(r"\s+", " ", body.title).strip()[:TITLE_MAX] or text.splitlines()[0][:24]
    folder = writing_dir() if body.folder != "notes" else notes_dir()
    when = datetime.now(TZ)
    kws = clean_keywords(inline_tags(text))
    path = await asyncio.to_thread(unique_path, folder, safe_file_part(title, 60) or f"{when:%Y-%m-%d %H%M}")
    meta = {"created_at": when.isoformat(timespec="seconds"), "source": "grava-think", "keywords": kws, "tags": obsidian_tags(kws)}
    await asyncio.to_thread(write_atomic, path, dump_note(meta, text))
    log_activity(L(f"在库里写了一篇「{title}」（{rel(path)}）", f'Wrote "{title}" into the vault ({rel(path)})'), "edit")
    return {"ok": True, "path": rel(path)}


# —— 主题 ——————————————————————————————————————————————————————————

_ready = False


def tdb() -> sqlite3.Connection:
    global _ready
    conn = db()
    if not _ready:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS think_topics (id TEXT PRIMARY KEY, title TEXT NOT NULL, fragments TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'open', created_at TEXT NOT NULL, updated_at TEXT NOT NULL, rev INTEGER NOT NULL DEFAULT 0,
            fed_rev INTEGER, fed_at TEXT, draft TEXT, draft_status TEXT, draft_at TEXT, draft_error TEXT, note_path TEXT, done_at TEXT);
        CREATE TABLE IF NOT EXISTS think_focus (id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL, ends_at TEXT NOT NULL,
            minutes INTEGER NOT NULL, ended_at TEXT, seen_at TEXT, words INTEGER, notes INTEGER);
        CREATE TABLE IF NOT EXISTS think_focus_held (id INTEGER PRIMARY KEY AUTOINCREMENT, focus INTEGER NOT NULL, at TEXT NOT NULL,
            title TEXT, body TEXT, subtitle TEXT, data TEXT, level TEXT, kind TEXT);
        """)
        _ready = True
    return conn


def load_topic(tid: str) -> sqlite3.Row:
    if not TOPIC_RE.match(tid or ""):
        raise HTTPException(404, L("没有这个主题", "No such topic"))
    with _lock, tdb() as conn:
        r = conn.execute("SELECT * FROM think_topics WHERE id=?", (tid,)).fetchone()
    if not r:
        raise HTTPException(404, L("没有这个主题", "No such topic"))
    return r


def topic_brief(r: sqlite3.Row) -> dict:
    ids = json.loads(r["fragments"] or "[]")
    with _lock, db() as conn:
        last = conn.execute("SELECT text, ts FROM messages WHERE thread=? AND role='grava' ORDER BY id DESC LIMIT 1", (r["id"],)).fetchone()
        n = conn.execute("SELECT COUNT(*) FROM messages WHERE thread=? AND role='grava'", (r["id"],)).fetchone()[0]
    return {"id": r["id"], "title": r["title"], "count": len(ids), "status": r["status"], "createdAt": r["created_at"], "updatedAt": r["updated_at"],
            "talked": n, "lastLine": (last["text"].strip().replace("\n", " ")[:80] if last else ""), "lastAt": last["ts"] if last else None,
            "draft": r["draft_status"], "notePath": r["note_path"]}


def topic_title_for(frags: list[dict]) -> str:
    for f in frags:
        if f["kind"] == "long" and f["title"]:
            return f["title"][:24]
    for f in frags:
        if f["keywords"]:
            return f["keywords"][0]
    for f in frags:
        if f["text"]:
            return safe_file_part(f["text"], 16)
    return L("新主题", "New topic")


def topic_title(tid: str) -> str | None:
    try:
        with _lock, tdb() as conn:
            r = conn.execute("SELECT title FROM think_topics WHERE id=?", (tid,)).fetchone()
        return r["title"] if r else None
    except sqlite3.Error:
        return None


def topic_json(r: sqlite3.Row) -> dict:
    ids = json.loads(r["fragments"] or "[]")
    by_id = {f["id"]: f for f in scan()}
    frags = [frag_json(by_id[i]) for i in ids if i in by_id]
    out = topic_brief(r)
    try:
        draft = json.loads(r["draft"]) if r["draft"] else None
    except ValueError:
        draft = None
    out.update(fragments=frags, missing=len([i for i in ids if i not in by_id]), draft=draft, draftStatus=r["draft_status"],
               draftError=r["draft_error"], notePath=r["note_path"])
    return out


def bump_topics(tids: list[str]) -> None:
    if not tids:
        return
    with _lock, tdb() as conn:
        for tid in tids:
            conn.execute("UPDATE think_topics SET rev=rev+1, updated_at=? WHERE id=?", (now_iso(), tid))


class TopicIn(BaseModel):
    fragments: list[str]
    title: str | None = None


def create_topic_sync(ids: list[str], title: str | None) -> str:
    by_id = {f["id"]: f for f in scan()}
    frags = [by_id[i] for i in dict.fromkeys(ids) if i in by_id]
    if not frags:
        raise HTTPException(400, L("先勾几条想法", "Pick some thoughts first"))
    tid = f"tp-{uuid.uuid4().hex[:8]}"
    ts = now_iso()
    name = re.sub(r"\s+", " ", title or "").strip()[:40] or topic_title_for(frags)
    with _lock, tdb() as conn:
        conn.execute("INSERT INTO think_topics(id, title, fragments, created_at, updated_at) VALUES(?,?,?,?,?)",
                     (tid, name, json.dumps([f["id"] for f in frags]), ts, ts))
    for f in frags:
        try:
            set_topics(f, add=tid)
        except HTTPException:
            pass  # 属性坏了的笔记：主题照样记着它，笔记上不写
    log_activity(L(f"开了一个思考主题「{name}」（{len(frags)} 条想法）", f'Started a thinking topic "{name}" ({len(frags)} thoughts)'), "edit")
    return tid


@router.post("/api/think/topics")
async def post_topic(body: TopicIn):
    tid = await asyncio.to_thread(create_topic_sync, body.fragments, body.title)
    return {"ok": True, "id": tid, "topic": await asyncio.to_thread(topic_json, load_topic(tid))}


@router.get("/api/think/topics")
async def list_topics(status: str = "open"):
    with _lock, tdb() as conn:
        rows = conn.execute("SELECT * FROM think_topics" + ("" if status == "all" else " WHERE status=?") + " ORDER BY updated_at DESC LIMIT 200",
                            (() if status == "all" else (status,))).fetchall()
    return {"ok": True, "topics": [topic_brief(r) for r in rows]}


@router.get("/api/think/topics/{tid}")
async def get_topic(tid: str):
    return {"ok": True, "topic": await asyncio.to_thread(topic_json, load_topic(tid))}


class TopicPatch(BaseModel):
    title: str | None = None
    add: list[str] = []
    remove: list[str] = []
    status: str | None = None     # open：重新打开（想完了又想接着想）


@router.patch("/api/think/topics/{tid}")
async def patch_topic(tid: str, body: TopicPatch):
    r = load_topic(tid)
    ids = json.loads(r["fragments"] or "[]")
    by_id = {f["id"]: f for f in await asyncio.to_thread(scan)}
    for i in body.add:
        if i in by_id and i not in ids:
            ids.append(i)
            await asyncio.to_thread(set_topics, by_id[i], tid)
    for i in body.remove:
        if i in ids:
            ids.remove(i)
            if i in by_id:
                await asyncio.to_thread(set_topics, by_id[i], None, tid)
    title = re.sub(r"\s+", " ", body.title or "").strip()[:40] if body.title is not None else r["title"]
    status = body.status if body.status in ("open",) else r["status"]
    with _lock, tdb() as conn:
        conn.execute("UPDATE think_topics SET title=?, fragments=?, status=?, rev=rev+1, updated_at=? WHERE id=?",
                     (title or r["title"], json.dumps(ids), status, now_iso(), tid))
    return {"ok": True, "topic": await asyncio.to_thread(topic_json, load_topic(tid))}


def add_note_to_topic(tid: str, frag: dict) -> None:
    """只记下：记进主题的碎片（不给模型看），对话里出一行虚线气泡（不发给 Gateway）。"""
    with _lock, tdb() as conn:
        r = conn.execute("SELECT fragments FROM think_topics WHERE id=?", (tid,)).fetchone()
        ids = json.loads(r["fragments"] or "[]") if r else []
        if frag["id"] not in ids:
            ids.append(frag["id"])
        conn.execute("UPDATE think_topics SET fragments=?, updated_at=? WHERE id=?", (json.dumps(ids), now_iso(), tid))
        conn.execute("INSERT INTO messages(thread, role, text, model, ts, origin) VALUES(?,?,?,?,?,?)",
                     (tid, "auto", MARK_NOTE + frag["text"][:2000], None, now_iso(), "note"))


def frag_line(f: dict, full: bool = True) -> str:
    d = local(f["createdAt"])
    label = {"text": LS("一句话", "note"), "keywords": LS("关键词", "keywords"), "voice": LS("语音转的字", "voice (transcribed)"),
             "photo": LS("照片", "photo"), "file": LS("文件", "file"), "link": LS("链接", "link"), "long": LS("长文", "long piece"),
             "save": LS("收藏", "saved item")}.get(f["kind"], f["kind"])
    head = f"[{f['id']}] {d.month}/{d.day} {d:%H:%M} {label}" + (f"《{f['title']}》" if f["title"] else "")
    body = f["text"] if full else f["text"][:300]
    parts = [head + (LS("：", ": ") + body if body else "")]
    if f["url"]:
        parts.append(f"  {f['linkTitle'] or ''} {f['url']}".rstrip())
    for x in f["files"]:
        where = big_dir() / "files" / x["path"][5:] if x["path"].startswith("@big/") else base_dir() / x["path"]
        parts.append(LS(f"  附件（{x['kind']}）：{where}", f"  attachment ({x['kind']}): {where}"))
    if f["keywords"] and f["kind"] != "keywords":
        parts.append("  " + " ".join("#" + k for k in f["keywords"]))
    return "\n".join(parts)


def context_for(thread: str) -> str | None:
    """chat.start_run 发消息前调：思考主题的线程，今天（上次重置以后）还没带过、或者碎片变了 → 碎片和规矩拼在消息前面。"""
    if not thread.startswith("tp-"):
        return None
    with _lock, tdb() as conn:
        r = conn.execute("SELECT * FROM think_topics WHERE id=?", (thread,)).fetchone()
    if r is None:
        return None
    try:
        fed = datetime.fromisoformat(r["fed_at"]) if r["fed_at"] else None
    except ValueError:
        fed = None
    import projects
    if fed is not None and r["fed_rev"] == (r["rev"] or 0) and fed >= projects.last_reset():
        return None
    ids = json.loads(r["fragments"] or "[]")
    by_id = {f["id"]: f for f in scan()}
    frags = [by_id[i] for i in ids if i in by_id and not by_id[i]["note"]]
    lines = [LS(f"【思考空间】这个对话是 Leo 的一个思考主题「{r['title']}」（id {r['id']}）。下面是他扔进来的碎片：他自己想到的，"
                "以前没给你看过，现在叫你来一起想。每天第一句话、碎片变了以后自动带给你，对话里不显示。",
                f"[Thinking space] This chat is one of the user's thinking topics \"{r['title']}\" (id {r['id']}). Below are the thoughts "
                "they dropped in: their own, never shown to you before; now they want you to think along. Attached to the first message "
                "each day and after the thoughts change; not shown in the chat."),
             LS("规矩：先追问，不急着下结论，一次两三个问题；用他的原话，不替他润色；可以翻库里的笔记和世界树（recall）对照他以前想过的，"
                "引用时说出处；这里只聊，不派任务、不改日程、不写记忆，除非他明确让你做。他点「想完了」时 app 会另外让你整理成笔记。",
                "Rules: ask before concluding, two or three questions at a time; use their own words, don't polish them; you may check their "
                "notes and memory tree (recall) for what they thought before, and say where it came from; this is for thinking only — no "
                "tasks, schedule changes or memory writes unless they ask. When they tap \"done thinking\" the app asks you for a note separately."),
             LS("碎片：", "Thoughts:")]
    lines += [frag_line(f) for f in frags]
    with _lock, tdb() as conn:
        conn.execute("UPDATE think_topics SET fed_rev=?, fed_at=? WHERE id=?", (r["rev"] or 0, now_iso(), thread))
    return "\n".join(lines)


@router.post("/api/think/topics/{tid}/talk")
async def talk(tid: str):
    """聊聊：它先读一遍碎片、先问你几个问题。回复不推（你正看着）。"""
    r = load_topic(tid)
    if r["status"] != "open":
        with _lock, tdb() as conn:
            conn.execute("UPDATE think_topics SET status='open', updated_at=? WHERE id=?", (now_iso(), tid))
    run = chat.start_run(tid, MARK_TALK + LS("：读一下这几条，先问我几个问题", ": read these and ask me a few questions first"), None, origin="auto", level="none")
    return {"ok": True, "thread": tid, "userId": f"db{run.user_id}"}


# —— 想完了：草稿 ————————————————————————————————————————————————————————

DRAFT_KEYS = ("title", "oneLine", "points", "open", "next", "keywords", "suggest", "tree", "branch")


def branches() -> list[str]:
    script = settings.scripts / "memory_tree.py"
    if not script.is_file():
        return []
    try:
        out = subprocess.run(["python3", str(script), "branches"], capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    names = re.findall(r"^\s*-\s*([^：:\s]+)", out, re.M)
    return list(dict.fromkeys(names))[:30]


def transcript(tid: str, limit: int = 60) -> list[str]:
    with _lock, db() as conn:
        rows = conn.execute("SELECT role, text FROM messages WHERE thread=? ORDER BY id DESC LIMIT ?", (tid, limit)).fetchall()
    out = []
    for r in reversed(rows):
        if r["role"] == "user":
            out.append(LS("Leo：", "User: ") + r["text"][:1500])
        elif r["role"] == "grava":
            out.append(LS("你：", "You: ") + r["text"][:1500])
    return out


def draft_prompt(r: sqlite3.Row, frags: list[dict]) -> str:
    talk_lines = transcript(r["id"])
    br = branches()
    lines = [LS(f"【思考空间 · 想完了】Leo 想完了一个主题「{r['title']}」，请把下面的碎片（和你们聊过的）整理成一篇他自己的笔记草稿。",
                f"[Thinking space · done] The user finished thinking about \"{r['title']}\". Turn the thoughts below (and your talk, if any) "
                "into a draft of their own note."),
             LS("规矩：", "Rules:"),
             LS("- 尽量用他的原话，不替他润色，不加他没说过的观点；没想清的放进 open，别替他下结论。",
                "- Use their own words; don't polish or add views they didn't express; unresolved things go in open — don't conclude for them."),
             LS("- oneLine：他现在的结论，一两句。points：3–6 条要点，每条写 from（来自哪几条碎片的 id）。",
                "- oneLine: their current conclusion in one or two sentences. points: 3–6 points, each with from (ids of the thoughts it comes from)."),
             LS("- next：只写他说过要做的或明显该做的，0–3 条，有日子的写 date（YYYY-MM-DD）。",
                "- next: only what they said they'd do or clearly should, 0–3 items, with date (YYYY-MM-DD) when there is one."),
             LS("- keywords：碎片上已有的关键词都留着；suggest：你另外建议的，最多 3 个，碎片里真有这个意思才建议。",
                "- keywords: keep every keyword already on the thoughts; suggest: at most 3 more, only if the thoughts really say it."),
             LS("- tree：如果有一条三个月后换个 AI 也用得上的「他怎么想」，写成一句第三人称（「Leo 认为……」）；没有就空。branch：挂哪根枝"
                + (f"（从这些里选：{'、'.join(br)}）" if br else "") + "。",
                "- tree: if there is one 'how they think' worth remembering across AIs for months, one third-person sentence; else empty. "
                "branch: which branch" + (f" (one of: {', '.join(br)})" if br else "") + "."),
             LS("只回一个 JSON，不要别的文字：", "Reply with one JSON object only, nothing else:"),
             '{"title": "...", "oneLine": "...", "points": [{"text": "...", "from": ["th-..."]}], "open": ["..."], '
             '"next": [{"text": "...", "date": ""}], "keywords": ["..."], "suggest": ["..."], "tree": "", "branch": ""}',
             LS("碎片：", "Thoughts:")]
    lines += [frag_line(f) + (LS("（只记下的，聊的时候你没看过）", " (a private note you didn't see while talking)") if f["note"] else "")
              for f in frags]
    if talk_lines:
        lines += [LS("聊过的（早的在前）：", "Your talk (oldest first):")] + talk_lines
    return "\n".join(lines)


def parse_draft(text: str, frags: list[dict], title: str) -> dict:
    s = text.strip()
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", s, re.S)
    if m:
        s = m.group(1)
    else:
        a, b = s.find("{"), s.rfind("}")
        s = s[a:b + 1] if a >= 0 and b > a else s
    j = json.loads(s)
    if not isinstance(j, dict):
        raise ValueError("not an object")
    ids = {f["id"] for f in frags}
    kw_have = clean_keywords([k for f in frags for k in f["keywords"]])

    def strs(v, n=8, cap=500):
        return [str(x).strip()[:cap] for x in (v or []) if str(x).strip()][:n] if isinstance(v, list) else []

    points = []
    for p in j.get("points") or []:
        if isinstance(p, dict) and str(p.get("text") or "").strip():
            points.append({"text": str(p["text"]).strip()[:500], "from": [x for x in (p.get("from") or []) if x in ids][:4]})
        elif isinstance(p, str) and p.strip():
            points.append({"text": p.strip()[:500], "from": []})
    nxt = []
    for p in j.get("next") or []:
        if isinstance(p, dict) and str(p.get("text") or "").strip():
            d = str(p.get("date") or "").strip()
            nxt.append({"text": str(p["text"]).strip()[:200], "date": d if re.match(r"^\d{4}-\d{2}-\d{2}$", d) else ""})
        elif isinstance(p, str) and p.strip():
            nxt.append({"text": p.strip()[:200], "date": ""})
    keywords = clean_keywords(kw_have + strs(j.get("keywords"), KW_MAX, KW_LEN))
    suggest = [k for k in clean_keywords(strs(j.get("suggest"), 3, KW_LEN)) if kw_norm(k) not in {kw_norm(x) for x in keywords}]
    return {"title": str(j.get("title") or title).strip()[:TITLE_MAX] or title, "oneLine": str(j.get("oneLine") or "").strip()[:600],
            "points": points[:8], "open": strs(j.get("open"), 6, 300), "next": nxt[:3], "keywords": keywords, "suggest": suggest,
            "tree": str(j.get("tree") or "").strip()[:300], "branch": str(j.get("branch") or "").strip()[:30]}


async def complete(prompt: str, key: str, model: str, timeout: float = 300) -> str:
    """一问一答（不进任何 app 线程）：流式攒完整段回复。"""
    token = chat.gateway_token()
    out = ""
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=10)) as client:
        async with client.stream("POST", f"{chat.GATEWAY}/v1/chat/completions",
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json", "x-openclaw-model": model,
                                          "x-openclaw-session-key": key, "x-openclaw-agent-id": "main"},
                                 json={"model": "openclaw/main", "stream": True, "messages": [{"role": "user", "content": prompt}]}) as r:
            if r.status_code != 200:
                raw = (await r.aread()).decode("utf8", "replace")
                raise RuntimeError(f"Gateway HTTP {r.status_code}: {raw[:200]}")
            async for line in r.aiter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    j = json.loads(payload)
                except ValueError:
                    continue
                if "error" in j:
                    raise RuntimeError((j["error"] or {}).get("message") or "Gateway error")
                for ch in j.get("choices", []):
                    out += (ch.get("delta") or {}).get("content") or ""
    return out


_drafting: set[asyncio.Task] = set()


async def make_draft(tid: str) -> None:
    r = load_topic(tid)
    ids = json.loads(r["fragments"] or "[]")
    by_id = {f["id"]: f for f in await asyncio.to_thread(scan)}
    frags = [by_id[i] for i in ids if i in by_id]
    key = f"agent:main:grava:{tid}:draft"
    with _lock, db() as conn:
        model = chat.thread_model(conn, tid)
    try:
        text = await complete(draft_prompt(r, frags), key, model)
        draft = parse_draft(text, frags, r["title"])
        with _lock, tdb() as conn:
            conn.execute("UPDATE think_topics SET draft=?, draft_status='ready', draft_at=?, draft_error=NULL WHERE id=?",
                         (json.dumps(draft, ensure_ascii=False), now_iso(), tid))
    except Exception as exc:  # noqa: BLE001 — 失败了在页面上说，能重试
        with _lock, tdb() as conn:
            conn.execute("UPDATE think_topics SET draft_status='error', draft_error=?, draft_at=? WHERE id=?", (str(exc)[:300], now_iso(), tid))
    finally:
        try:  # 这一问的会话用完就删（OpenClaw 会压缩存档一份）
            await chat.gateway_call("sessions.delete", {"key": key}, timeout=20)
        except Exception:  # noqa: BLE001
            pass


@router.post("/api/think/topics/{tid}/done")
async def done(tid: str, fresh: int = 0):
    """想完了：后台整理成笔记草稿（已经有草稿、不要求重来就直接用）。app 轮询 GET /api/think/topics/{tid} 的 draftStatus。"""
    r = load_topic(tid)
    if r["draft_status"] == "running":
        return {"ok": True, "status": "running"}
    if r["draft_status"] == "ready" and not fresh:
        return {"ok": True, "status": "ready"}
    with _lock, tdb() as conn:
        conn.execute("UPDATE think_topics SET draft_status='running', draft_error=NULL, draft_at=? WHERE id=?", (now_iso(), tid))
    task = asyncio.create_task(make_draft(tid))
    _drafting.add(task)
    task.add_done_callback(_drafting.discard)
    return {"ok": True, "status": "running"}


class SaveIn(BaseModel):
    title: str
    oneLine: str = ""
    points: list[str] = []
    open: list[str] = []
    next: list[str] = []
    keywords: list[str] = []
    folder: str = "notes"          # notes 笔记 / writing 写作
    tree: str | None = None        # 要记进世界树的那一句（不记就不给）
    branch: str | None = None


def save_sync(tid: str, body: SaveIn) -> dict:
    r = load_topic(tid)
    title = re.sub(r"\s+", " ", body.title).strip()[:TITLE_MAX]
    if not title:
        raise HTTPException(400, L("起个标题", "Give it a title"))
    ids = json.loads(r["fragments"] or "[]")
    by_id = {f["id"]: f for f in scan()}
    frags = [by_id[i] for i in ids if i in by_id]
    kws = clean_keywords(body.keywords)
    folder = writing_dir() if body.folder == "writing" else notes_dir()
    path = unique_path(folder, safe_file_part(title, 60) or r["id"])
    parts = []
    if body.oneLine.strip():
        parts.append("> " + body.oneLine.strip().replace("\n", "\n> "))
    for head, items in ((LS("要点", "Points"), body.points), (LS("还没想清的", "Still open"), body.open), (LS("下一步", "Next"), body.next)):
        items = [x.strip() for x in items if x.strip()]
        if items:
            parts.append(f"## {head}\n" + "\n".join(f"- {x}" for x in items))
    # 碎片挪进已想完之后的位置：先算好链接
    moved: list[tuple[dict, Path]] = []
    for f in frags:
        try:
            moved.append((f, move_to(f, done_dir())))
        except OSError:
            moved.append((f, frag_path(f)))
    links = []
    for f, p in moved:
        d = local(f["createdAt"])
        label = f"{d.month}/{d.day} {d:%H:%M} " + (f["title"] or safe_file_part(f["text"], 18) or f["kind"])
        links.append(f"- [[{rel(p)[:-3]}|{label}]]")
    if links:
        parts.append("---\n" + LS("用到的碎片：", "From these thoughts:") + "\n" + "\n".join(links))
    meta = {"created_at": now_iso(), "source": "grava-think", "topic": tid, "keywords": kws, "tags": obsidian_tags(kws),
            "fragments": [f["id"] for f in frags]}
    write_atomic(path, dump_note(meta, "\n\n".join(parts)))
    for f, p in moved:  # 碎片标成想完了（在已想完文件夹里本来就算，属性上也写一笔）
        try:
            def change(m: dict, b: str, _p=p) -> str:
                m["status"] = "done"
                m["done_at"] = now_iso()
                m["note_path"] = rel(path)
                return b
            rewrite({**f, "path": rel(p)}, change)
        except (HTTPException, OSError):
            pass
    tree = None
    if body.tree and body.tree.strip():
        tree = remember_tree(body.tree.strip()[:300], body.branch, kws)
    with _lock, tdb() as conn:
        conn.execute("UPDATE think_topics SET status='done', note_path=?, done_at=?, updated_at=?, rev=rev+1 WHERE id=?",
                     (rel(path), now_iso(), now_iso(), tid))
    log_activity(L(f"想完了「{title}」，存进了库（{rel(path)}）", f'Finished thinking about "{title}", saved to the vault ({rel(path)})'), "edit")
    return {"ok": True, "path": rel(path), "tree": tree, "moved": len(moved)}


def remember_tree(text: str, branch: str | None, tags: list[str]) -> dict | None:
    script = settings.scripts / "memory_tree.py"
    if not script.is_file():
        return None
    cmd = ["python3", str(script), "add", "--source", "grava", "--kind", "fact", "--text", text, "--tags", ",".join(tags[:6])]
    if branch:
        cmd += ["--branch", branch]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return {"ok": False}
    try:
        return json.loads(p.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"ok": p.returncode == 0}


@router.post("/api/think/topics/{tid}/save")
async def save(tid: str, body: SaveIn):
    return await asyncio.to_thread(save_sync, tid, body)


# —— 搜索、关键词、按天 ——————————————————————————————————————————————————

def parts_of(text: str, words: list[str], width: int = 90) -> list[list]:
    """命中附近的一段，按命中切开：[[文字, 是不是命中], …]。"""
    flat = re.sub(r"\s+", " ", text or "").strip()
    low = flat.lower()
    pos = min((low.find(w) for w in words if low.find(w) >= 0), default=0)
    start = max(0, pos - width // 3)
    seg = flat[start:start + width]
    pre, post = ("…" if start > 0 else ""), ("…" if start + width < len(flat) else "")
    out: list[list] = []
    i, lseg = 0, seg.lower()
    while i < len(seg):
        hits = [(lseg.find(w, i), w) for w in words if w and lseg.find(w, i) >= 0]
        if not hits:
            out.append([seg[i:], False])
            break
        j, w = min(hits)
        if j > i:
            out.append([seg[i:j], False])
        out.append([seg[j:j + len(w)], True])
        i = j + len(w)
    if out and pre:
        out[0][0] = pre + out[0][0]
    if out and post:
        out[-1][0] = out[-1][0] + post
    return out


def all_words(hay: str, words: list[str]) -> bool:
    low = hay.lower()
    return all(w in low for w in words)


def note_files() -> list[Path]:
    out = []
    for folder in (notes_dir(), writing_dir()):
        if folder.is_dir():
            out += [p for p in folder.glob("*.md") if not p.name.startswith(".") and not p.is_symlink()]
    return out


def search_sync(q: str, scope: str) -> dict:
    words = [w.lower() for w in q.split() if w.strip()]
    if not words:
        return {"q": q, "keywords": [], "ideas": [], "saves": [], "topics": [], "notes": []}
    import saves
    out = {"q": q, "keywords": [], "ideas": [], "saves": [], "topics": [], "notes": []}
    frags = scan()
    if scope in ("all", "idea"):
        stats = keyword_stats(frags, saves.all_rows())
        out["keywords"] = [k for k in stats if all(w.replace(" ", "") in kw_norm(k["k"]) for w in words)][:5]
        for f in frags:
            hay = " ".join([f["title"], f["text"], " ".join(f["keywords"]), f["linkTitle"], f["url"]])
            if all_words(hay, words):
                src = f["text"] if all_words(f["text"], words) else hay
                out["ideas"].append({**frag_json(f, full=False), "parts": parts_of(src, words)})
    if scope in ("all", "save"):
        for s in saves.all_rows():
            hay = " ".join([s["title"], s["note"] or "", " ".join(json.loads(s["keywords"] or "[]")), s["url"] or "", s["source"] or ""])
            body = saves.text_of(s["id"])
            if all_words(hay, words) or all_words(hay + " " + body, words):
                in_body = bool(body) and all_words(body, words) and not all_words(s["title"], words)
                out["saves"].append({**saves.save_json(s), "parts": parts_of(body if in_body else hay, words), "inBody": in_body})
    if scope in ("all", "topic"):
        found = []
        with _lock, tdb() as conn:
            rows = conn.execute("SELECT * FROM think_topics ORDER BY updated_at DESC").fetchall()
            for r in rows:
                msgs = conn.execute("SELECT text FROM messages WHERE thread=? AND role IN ('user','grava') ORDER BY id DESC", (r["id"],)).fetchall()
                hit = next((m["text"] for m in msgs if all_words(m["text"], words)), None)
                if hit or all_words(r["title"], words):
                    found.append((r, hit))
        for r, hit in found:  # topic_brief 自己拿锁：出了 with 再调
            out["topics"].append({**topic_brief(r), "parts": parts_of(hit or r["title"], words)})
    if scope in ("all", "note"):
        for p in note_files():
            try:
                meta, body, _ = parse_note(p.read_text(encoding="utf8", errors="replace"))
            except OSError:
                continue
            if all_words(p.stem + " " + body, words):
                out["notes"].append({"path": rel(p), "title": p.stem, "folder": rel(p.parent), "createdAt": as_iso(meta.get("created_at")),
                                     "parts": parts_of(body if all_words(body, words) else p.stem, words)})
    return out


@router.get("/api/think/search")
async def search(q: str, scope: str = "all"):
    res = await asyncio.to_thread(search_sync, q, scope if scope in ("all", "idea", "save", "topic", "note") else "all")
    counts = {k: len(res[k]) for k in ("ideas", "saves", "topics", "notes")}
    return {"ok": True, **res, "counts": counts, "total": sum(counts.values())}


def keyword_stats(frags: list[dict], save_rows: list) -> list[dict]:
    stats: dict[str, dict] = {}
    for f in frags:
        for k in f["keywords"]:
            e = stats.setdefault(kw_norm(k), {"k": k, "n": 0, "ideas": 0, "saves": 0, "last": ""})
            e["n"] += 1
            e["ideas"] += 1
            e["last"] = max(e["last"], f["createdAt"])
    for s in save_rows:
        for k in json.loads(s["keywords"] or "[]"):
            e = stats.setdefault(kw_norm(k), {"k": k, "n": 0, "ideas": 0, "saves": 0, "last": ""})
            e["n"] += 1
            e["saves"] += 1
            e["last"] = max(e["last"], s["created_at"])
    out = sorted(stats.values(), key=lambda e: e["last"], reverse=True)  # 一样多的，最近用过的在前
    out.sort(key=lambda e: -e["n"])
    return out


@router.get("/api/think/keywords")
async def keywords():
    import saves
    frags = await asyncio.to_thread(scan)
    rows = await asyncio.to_thread(saves.all_rows)
    return {"ok": True, "keywords": keyword_stats(frags, rows)}


def keyword_sync(k: str) -> dict:
    import saves
    key = kw_norm(k)
    frags = [f for f in scan() if key in {kw_norm(x) for x in f["keywords"]}]
    rows = [s for s in saves.all_rows() if key in {kw_norm(x) for x in json.loads(s["keywords"] or "[]")}]
    co: dict[str, dict] = {}
    for kws in [f["keywords"] for f in frags] + [json.loads(s["keywords"] or "[]") for s in rows]:
        for x in kws:
            if kw_norm(x) != key:
                e = co.setdefault(kw_norm(x), {"k": x, "n": 0})
                e["n"] += 1
    items = [{"type": "idea", "at": f["createdAt"], "idea": frag_json(f, full=False)} for f in frags] + \
            [{"type": "save", "at": s["created_at"], "save": saves.save_json(s)} for s in rows]
    items.sort(key=lambda x: x["at"], reverse=True)
    topic_ids = {t for f in frags for t in f["topics"]}
    rows_t = []
    if topic_ids:
        with _lock, tdb() as conn:
            rows_t = [r for tid in topic_ids if (r := conn.execute("SELECT * FROM think_topics WHERE id=?", (tid,)).fetchone())]
    topics = [topic_brief(r) for r in rows_t]
    name = (frags[0]["keywords"] if frags else json.loads(rows[0]["keywords"]) if rows else [k])
    shown = next((x for x in name if kw_norm(x) == key), k)
    return {"k": shown, "ideas": len(frags), "saves": len(rows), "since": items[-1]["at"] if items else None, "items": items,
            "co": sorted(co.values(), key=lambda e: -e["n"])[:8], "topics": topics}


@router.get("/api/think/keyword")
async def keyword(k: str):
    return {"ok": True, **await asyncio.to_thread(keyword_sync, k)}


def days_sync(month: str) -> dict:
    import saves
    counts: dict[str, dict] = {}
    for f in scan():
        if f["note"]:
            continue
        d = local(f["createdAt"]).strftime("%Y-%m-%d")
        if d.startswith(month):
            counts.setdefault(d, {"day": d, "ideas": 0, "saves": 0})["ideas"] += 1
    for s in saves.all_rows():
        d = local(s["created_at"]).strftime("%Y-%m-%d")
        if d.startswith(month):
            counts.setdefault(d, {"day": d, "ideas": 0, "saves": 0})["saves"] += 1
    return {"month": month, "days": sorted(counts.values(), key=lambda x: x["day"]), "total": len(counts)}


@router.get("/api/think/days")
async def days(month: str | None = None):
    m = month or datetime.now(TZ).strftime("%Y-%m")
    if not re.match(r"^\d{4}-\d{2}$", m):
        raise HTTPException(400, L("month 要写成 YYYY-MM", "month must be YYYY-MM"))
    return {"ok": True, **await asyncio.to_thread(days_sync, m)}


def day_sync(day: str) -> list[dict]:
    import saves
    items = [{"type": "idea", "at": f["createdAt"], "idea": frag_json(f, full=False)} for f in scan()
             if not f["note"] and local(f["createdAt"]).strftime("%Y-%m-%d") == day]
    items += [{"type": "save", "at": s["created_at"], "save": saves.save_json(s)} for s in saves.all_rows()
              if local(s["created_at"]).strftime("%Y-%m-%d") == day]
    items.sort(key=lambda x: x["at"], reverse=True)
    return items


@router.get("/api/think/day")
async def day(day: str):
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", day or ""):
        raise HTTPException(400, L("day 要写成 YYYY-MM-DD", "day must be YYYY-MM-DD"))
    return {"ok": True, "day": day, "items": await asyncio.to_thread(day_sync, day)}


# —— 冥想时间 ————————————————————————————————————————————————————————

def focus_row(conn: sqlite3.Connection) -> sqlite3.Row | None:
    """正在进行的那一次；到点了还没结束的，按到点结束（ended_at = ends_at）。"""
    r = conn.execute("SELECT * FROM think_focus WHERE ended_at IS NULL ORDER BY id DESC LIMIT 1").fetchone()
    if r and datetime.fromisoformat(r["ends_at"]) <= datetime.now(TZ):
        conn.execute("UPDATE think_focus SET ended_at=? WHERE id=?", (r["ends_at"], r["id"]))
        return None
    return r


def focus_active() -> sqlite3.Row | None:
    try:
        with _lock, tdb() as conn:
            return focus_row(conn)
    except sqlite3.Error:
        return None


def hold_push(title: str, body: str, data: dict | None, *, level: str, kind: str, subtitle: str | None) -> bool:
    """push.send_push 发之前问一句：冥想中就压住、记下来，返回 True（这条不推）。"""
    try:
        with _lock, tdb() as conn:
            r = focus_row(conn)
            if not r:
                return False
            conn.execute("INSERT INTO think_focus_held(focus, at, title, body, subtitle, data, level, kind) VALUES(?,?,?,?,?,?,?,?)",
                         (r["id"], now_iso(), title, body, subtitle, json.dumps(data or {}, ensure_ascii=False), level, kind))
        return True
    except sqlite3.Error:
        return False


def focus_json(r: sqlite3.Row | None) -> dict | None:
    if not r:
        return None
    with _lock, tdb() as conn:
        held = conn.execute("SELECT COUNT(*) FROM think_focus_held WHERE focus=?", (r["id"],)).fetchone()[0]
    return {"id": r["id"], "startedAt": r["started_at"], "endsAt": r["ends_at"], "minutes": r["minutes"], "endedAt": r["ended_at"],
            "held": held, "until": local(r["ends_at"]).strftime("%H:%M")}


async def heads_up(start: datetime, end: datetime) -> list[dict]:
    """这段时间里（和结束后 15 分钟内）要开始的日程、到期的截止。"""
    out: list[dict] = []
    try:
        import schedule
        res = await schedule.get_schedule(days=2 if end.date() > start.date() else 1, from_=start.strftime("%Y-%m-%d"))
        events = res.get("events") or []
    except Exception:  # noqa: BLE001 — 看不到日程就不提醒，冥想照样开始
        return out
    limit = end + timedelta(minutes=15)
    for e in events:
        d, s = e.get("date"), e.get("start")
        if not d or not s or e.get("skip") or e.get("done"):
            continue
        try:
            at = datetime.fromisoformat(f"{d}T{s}").replace(tzinfo=TZ)
        except ValueError:
            continue
        if start <= at <= limit:
            out.append({"title": e.get("title") or "", "time": s, "kind": e.get("kind") or "event", "location": e.get("location") or ""})
    out.sort(key=lambda x: x["time"])
    return out[:4]


@router.get("/api/think/focus")
async def focus_state():
    """在冥想吗；刚结束、还没看过小结的那一次也给（app 回到前台时补看）。"""
    with _lock, tdb() as conn:
        r = focus_row(conn)
        last = conn.execute("SELECT * FROM think_focus WHERE ended_at IS NOT NULL AND seen_at IS NULL ORDER BY id DESC LIMIT 1").fetchone()
    return {"ok": True, "active": focus_json(r), "unseen": focus_json(last) if last and not r else None}


@router.get("/api/think/focus/preview")
async def focus_preview(minutes: int = 45):
    m = FOCUS_MAX if minutes <= 0 else min(minutes, FOCUS_MAX)
    now = datetime.now(TZ)
    end = now + timedelta(minutes=m)
    return {"ok": True, "now": now.strftime("%H:%M"), "until": end.strftime("%H:%M"), "minutes": m, "items": await heads_up(now, end)}


class FocusIn(BaseModel):
    minutes: int = 45


@router.post("/api/think/focus/start")
async def focus_start(body: FocusIn):
    m = FOCUS_MAX if body.minutes <= 0 else min(max(body.minutes, 5), FOCUS_MAX)
    now = datetime.now(TZ)
    with _lock, tdb() as conn:
        r = focus_row(conn)
        if not r:
            cur = conn.execute("INSERT INTO think_focus(started_at, ends_at, minutes) VALUES(?,?,?)",
                               (now.isoformat(timespec="seconds"), (now + timedelta(minutes=m)).isoformat(timespec="seconds"), m))
            r = conn.execute("SELECT * FROM think_focus WHERE id=?", (cur.lastrowid,)).fetchone()
    log_activity(L(f"进入冥想时间（{m} 分钟），推送先压住", f"Started focus time ({m} min); notifications held"), "edit")
    return {"ok": True, "active": focus_json(r)}


class FocusEnd(BaseModel):
    words: int | None = None
    notes: int | None = None


def summary_of(fid: int) -> dict:
    with _lock, tdb() as conn:
        r = conn.execute("SELECT * FROM think_focus WHERE id=?", (fid,)).fetchone()
        if not r:
            raise HTTPException(404, L("没有这次冥想", "No such focus session"))
        held = conn.execute("SELECT * FROM think_focus_held WHERE focus=? ORDER BY id", (fid,)).fetchall()
    items, seen = [], {}
    for h in held:  # 同一个去处（同一个对话、同一张卡）只留最新的一条
        try:
            data = json.loads(h["data"] or "{}")
        except ValueError:
            data = {}
        th = data.get("thread")
        tgt = data.get("target") or ({"type": "thread", "thread": th} if th and th != "today" else {"type": "today"})
        # 同一个对话 / 同一张卡只留最新的一条；推到「今天」的报告（起床报告、截止提醒）按标题分开
        k = json.dumps(tgt, sort_keys=True) + (f"|{h['title']}" if tgt.get("type") == "today" else "")
        item = {"title": h["title"] or "", "body": h["body"] or "", "subtitle": h["subtitle"] or "", "at": local(h["at"]).strftime("%H:%M"),
                "kind": h["kind"], "level": h["level"], "target": tgt, "thread": data.get("thread")}
        if k in seen:
            items[seen[k]] = item
        else:
            seen[k] = len(items)
            items.append(item)
    start, end = local(r["started_at"]), local(r["ended_at"] or now_iso())
    mins = max(1, round((end - start).total_seconds() / 60))
    return {"id": r["id"], "startedAt": r["started_at"], "endedAt": r["ended_at"], "from": start.strftime("%H:%M"), "to": end.strftime("%H:%M"),
            "minutes": mins, "planned": r["minutes"], "words": r["words"], "notes": r["notes"], "held": items}


@router.post("/api/think/focus/end")
async def focus_end(body: FocusEnd | None = None):
    now = now_iso()
    with _lock, tdb() as conn:
        r = conn.execute("SELECT * FROM think_focus ORDER BY id DESC LIMIT 1").fetchone()
        if not r:
            raise HTTPException(404, L("没有在冥想", "Not in focus time"))
        conn.execute("UPDATE think_focus SET ended_at=COALESCE(ended_at, ?), words=COALESCE(?, words), notes=COALESCE(?, notes), seen_at=? WHERE id=?",
                     (now, body.words if body else None, body.notes if body else None, now, r["id"]))
    s = await asyncio.to_thread(summary_of, r["id"])
    import inbox
    try:
        s["inbox"] = await inbox.pending_count()
    except Exception:  # noqa: BLE001
        s["inbox"] = 0
    s["next"] = await heads_up(datetime.now(TZ), datetime.now(TZ) + timedelta(minutes=60))
    return {"ok": True, "summary": s}


@router.post("/api/think/focus/seen/{fid}")
async def focus_seen(fid: int):
    with _lock, tdb() as conn:
        conn.execute("UPDATE think_focus SET seen_at=? WHERE id=? AND seen_at IS NULL", (now_iso(), fid))
    return {"ok": True}


@router.get("/api/think/focus/summary/{fid}")
async def focus_summary(fid: int):
    s = await asyncio.to_thread(summary_of, fid)
    import inbox
    try:
        s["inbox"] = await inbox.pending_count()
    except Exception:  # noqa: BLE001
        s["inbox"] = 0
    s["next"] = await heads_up(datetime.now(TZ), datetime.now(TZ) + timedelta(minutes=60))
    return {"ok": True, "summary": s}
