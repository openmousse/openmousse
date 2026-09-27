"""收藏（2026-09-27）：别人的好东西，先存着，你决定怎么处理。另见 think.py（思考空间）。

- 原件不进 Obsidian 库（原始材料，库要精）：grava.db 的 think_saves 一行 + <data_dir>/saves/ 里的原件（server.json think.saves_dir 可改）。
- 存的时候不调模型，只把正文抽一份存成 <id>.txt：链接在后台抓网页正文（公众号文章被删了也还在），PDF / Word / 表格抽文字。
  抓不到（服务器 IP 被挡、要登录、要跑脚本的页面）就记下原因，原链接还在。
- 怎么进来：app 里粘贴链接或一段文字、选照片 / 文件、对话里长按一条消息「收藏」；以后的分享扩展（下次出包）也走这里（source 写来自哪个 App）。
- 处理：问 Grava（app 把这条带进主对话，chat.py 的 save 字段把正文给模型）、交给某个 Agent（在它的线程里开一轮，回完静默推）、
  放进思考（变成一条 kind=save 的碎片）、提炼进库（app：放进思考 → 主题 → 想完了）、删（软删，deleted_at，能恢复）。
- 关键词和想法共用（think.py 的 keyword 页一起列）。
"""
from __future__ import annotations

import asyncio
import json
import re
import shutil
import sqlite3
import uuid
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

import chat
import think
from chat import _lock, db, log_activity, now_iso
from config import settings
from i18n import L, LS

router = APIRouter()
SAVE_RE = re.compile(r"^sv-[0-9a-f]{8}$")
TEXT_CAP = 200_000
FETCH_MAX = 6 * 1024 * 1024
URL_RE = re.compile(r"https?://[^\s<>\"'，。、；！？)）\]】」]+", re.I)
UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 "
      "Mobile/15E148 Safari/604.1")
SOURCES = (("mp.weixin.qq.com", "微信公众号", "WeChat article"), ("weixin.qq.com", "微信", "WeChat"), ("xhslink.com", "小红书", "Xiaohongshu"),
           ("xiaohongshu.com", "小红书", "Xiaohongshu"), ("zhihu.com", "知乎", "Zhihu"), ("bilibili.com", "B 站", "Bilibili"),
           ("b23.tv", "B 站", "Bilibili"), ("douyin.com", "抖音", "Douyin"), ("weibo.", "微博", "Weibo"), ("x.com", "X", "X"),
           ("twitter.com", "X", "X"), ("youtube.com", "YouTube", "YouTube"), ("youtu.be", "YouTube", "YouTube"), ("github.com", "GitHub", "GitHub"),
           ("substack.com", "Substack", "Substack"), ("medium.com", "Medium", "Medium"))
_tasks: set[asyncio.Task] = set()
_text_cache: dict[str, tuple[float, str]] = {}


def saves_dir() -> Path:
    v = think.cfg().get("saves_dir")
    return Path(v).expanduser() if v else settings.data_dir / "saves"


_ready = False


def sdb() -> sqlite3.Connection:
    global _ready
    conn = db()
    if not _ready:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS think_saves (id TEXT PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL DEFAULT '', url TEXT,
            source TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '', file TEXT, name TEXT, mime TEXT, size INTEGER,
            text_status TEXT NOT NULL DEFAULT 'none', text_len INTEGER NOT NULL DEFAULT 0, text_note TEXT, keywords TEXT NOT NULL DEFAULT '[]',
            thread TEXT, message_id INTEGER, given_to TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, seen_at TEXT, deleted_at TEXT);
        CREATE INDEX IF NOT EXISTS think_saves_created ON think_saves(created_at);
        """)
        _ready = True
    return conn


def all_rows() -> list[sqlite3.Row]:
    try:
        with _lock, sdb() as conn:
            return conn.execute("SELECT * FROM think_saves WHERE deleted_at IS NULL ORDER BY created_at DESC").fetchall()
    except sqlite3.Error:
        return []


def new_count() -> int:
    try:
        with _lock, sdb() as conn:
            return conn.execute("SELECT COUNT(*) FROM think_saves WHERE deleted_at IS NULL AND seen_at IS NULL").fetchone()[0]
    except sqlite3.Error:
        return 0


def row(sid: str, deleted: bool = False) -> sqlite3.Row:
    if not SAVE_RE.match(sid or ""):
        raise HTTPException(404, L("没有这条收藏", "No such saved item"))
    with _lock, sdb() as conn:
        r = conn.execute("SELECT * FROM think_saves WHERE id=?" + ("" if deleted else " AND deleted_at IS NULL"), (sid,)).fetchone()
    if not r:
        raise HTTPException(404, L("没有这条收藏", "No such saved item"))
    return r


def text_path(sid: str) -> Path:
    return saves_dir() / f"{sid}.txt"


def text_of(sid: str) -> str:
    p = text_path(sid)
    try:
        mt = p.stat().st_mtime
    except OSError:
        return ""
    hit = _text_cache.get(sid)
    if hit and hit[0] == mt:
        return hit[1]
    t = p.read_text(encoding="utf8", errors="replace")
    _text_cache[sid] = (mt, t)
    return t


def put_text(sid: str, text: str) -> int:
    text = (text or "").strip()[:TEXT_CAP]
    saves_dir().mkdir(parents=True, exist_ok=True)
    think.write_atomic(text_path(sid), text)
    return len(text)


def source_of(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    for dom, zh_name, en_name in SOURCES:
        if dom in host:
            return L(zh_name, en_name)
    return host.removeprefix("www.")


def save_json(r: sqlite3.Row) -> dict:
    d = think.local(r["created_at"])
    has_file = bool(r["file"])
    kind = r["kind"]
    return {"id": r["id"], "kind": kind, "title": r["title"], "url": r["url"] or "", "source": r["source"], "note": r["note"],
            "name": r["name"], "mime": r["mime"], "size": r["size"], "textStatus": r["text_status"], "textLen": r["text_len"],
            "textNote": r["text_note"], "keywords": json.loads(r["keywords"] or "[]"), "createdAt": r["created_at"], "day": d.strftime("%Y-%m-%d"),
            "time": d.strftime("%H:%M"), "seen": bool(r["seen_at"]), "givenTo": r["given_to"], "thread": r["thread"],
            "fileUrl": f"/api/think/saves/{r['id']}/file" if has_file else None,
            "thumbUrl": f"/api/think/saves/{r['id']}/file?thumb=1" if has_file and kind == "image" else None}


def insert(kind: str, *, title: str = "", url: str = "", source: str = "", note: str = "", keywords: list[str] | None = None,
           file: str | None = None, name: str | None = None, mime: str | None = None, size: int | None = None,
           thread: str | None = None, message_id: int | None = None) -> str:
    sid = f"sv-{uuid.uuid4().hex[:8]}"
    ts = now_iso()
    kws = think.clean_keywords(list(keywords or []) + think.inline_tags(note))
    with _lock, sdb() as conn:
        conn.execute("""INSERT INTO think_saves(id, kind, title, url, source, note, file, name, mime, size, keywords, thread, message_id,
                        created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                     (sid, kind, title[:200], url or None, source[:40], note.strip()[:2000], file, name, mime, size, json.dumps(kws, ensure_ascii=False),
                      thread, message_id, ts, ts))
    return sid


def set_text(sid: str, status: str, text: str = "", note: str | None = None, title: str | None = None) -> None:
    n = put_text(sid, text) if text else 0
    with _lock, sdb() as conn:
        r = conn.execute("SELECT title, url FROM think_saves WHERE id=?", (sid,)).fetchone()
        new_title = r["title"] if r else ""
        if title and r and (not r["title"] or r["title"] == (urlparse(r["url"] or "").hostname or "")):
            new_title = title[:200]
        conn.execute("UPDATE think_saves SET text_status=?, text_len=?, text_note=?, title=?, updated_at=? WHERE id=?",
                     (status, n, note, new_title, now_iso(), sid))


def spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


# —— 网页正文 ——————————————————————————————————————————————————————————

class _Page(HTMLParser):
    """够用的网页正文：标题（og:title / <title> / 公众号的标题）、正文（公众号 #js_content > <article> > <main> > <body>）。"""
    SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "form", "button", "iframe", "template", "select", "canvas"}
    BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "tr", "blockquote", "pre", "figcaption", "ul", "ol", "table"}
    VOID = {"br", "img", "meta", "link", "input", "hr", "source", "wbr", "area", "base", "col", "embed", "param", "track"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.title = ""
        self._in_title = False
        self.stack: list[tuple[str, set[str]]] = []  # (标签, 这一层打开的区域)
        self.skip = 0
        self.buf: dict[str, list[str]] = {"wx": [], "article": [], "main": [], "body": [], "h1": []}

    def zones(self) -> set[str]:
        z: set[str] = set()
        for _, s in self.stack:
            z |= s
        return z

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "meta":
            k = (a.get("property") or a.get("name") or "").lower()
            if k in ("og:title", "og:description", "description", "author", "og:site_name", "twitter:title") and a.get("content"):
                self.meta.setdefault(k, a["content"].strip())
            return
        if tag == "title":
            self._in_title = True
        if tag in self.VOID:
            if tag == "br":
                self._add("\n")
            return
        opened: set[str] = set()
        if a.get("id") == "js_content":
            opened.add("wx")
        if tag == "article":
            opened.add("article")
        if tag == "main" or a.get("role") == "main":
            opened.add("main")
        if tag == "body":
            opened.add("body")
        if tag == "h1":
            opened.add("h1")
        if tag in self.SKIP:
            self.skip += 1
        self.stack.append((tag, opened))
        if tag in self.BLOCK:
            self._add("\n")

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag in self.VOID:
            return
        for i in range(len(self.stack) - 1, -1, -1):  # 容错：没闭合的标签一起弹掉
            if self.stack[i][0] == tag:
                for t, _ in self.stack[i:]:
                    if t in self.SKIP:
                        self.skip = max(0, self.skip - 1)
                del self.stack[i:]
                break
        if tag in self.BLOCK:
            self._add("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
            return
        if self.skip:
            return
        self._add(data)

    def _add(self, s: str) -> None:
        z = self.zones()
        for k in ("wx", "article", "main", "body", "h1"):
            if k in z:
                self.buf[k].append(s)

    def text(self) -> str:
        for k in ("wx", "article", "main", "body"):
            t = tidy("".join(self.buf[k]))
            if len(t) > 80 or (k == "body" and t):
                return t
        return ""


def tidy(t: str) -> str:
    t = re.sub(r"[ \t 　]+", " ", t)
    lines = [ln.strip() for ln in t.split("\n")]
    out, blank = [], 0
    for ln in lines:
        if not ln:
            blank += 1
            if blank == 1 and out:
                out.append("")
            continue
        blank = 0
        out.append(ln)
    return "\n".join(out).strip()


BLOCKED = ("环境异常", "访问过于频繁", "请完成验证", "Please verify", "Access Denied", "captcha", "此内容因违规无法查看", "该内容已被发布者删除")


async def fetch_link(sid: str, url: str) -> None:
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(25, connect=10),
                                     headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}) as client:
            async with client.stream("GET", url) as r:
                if r.status_code >= 400:
                    raise RuntimeError(f"HTTP {r.status_code}")
                ctype = (r.headers.get("content-type") or "").lower()
                body = b""
                async for chunk in r.aiter_bytes():
                    body += chunk
                    if len(body) > FETCH_MAX:
                        break
                final = str(r.url)
                enc = r.encoding or "utf-8"
        if "pdf" in ctype or final.lower().split("?")[0].endswith(".pdf"):
            path = saves_dir() / f"{sid}.pdf"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
            import files as files_mod
            text, note = await asyncio.to_thread(files_mod.extract_text, path, "doc", "application/pdf")
            with _lock, sdb() as conn:
                conn.execute("UPDATE think_saves SET file=?, name=?, mime=?, size=? WHERE id=?", (str(path), Path(urlparse(final).path).name or "file.pdf",
                                                                                             "application/pdf", len(body), sid))
            set_text(sid, "ok" if text.strip() else "empty", text, note)
            return
        html = body.decode(enc, "replace")
        page = _Page()
        page.feed(html)
        title = (page.meta.get("og:title") or page.meta.get("twitter:title") or tidy("".join(page.buf["h1"])).split("\n")[0]
                 or tidy(page.title)).strip()
        m = re.search(r"var msg_title\s*=\s*['\"](.+?)['\"]", html)
        if m and not title:
            title = m.group(1)
        text = page.text()
        author = page.meta.get("author") or ""
        head = "\n".join(x for x in [title, author, page.meta.get("og:description") or page.meta.get("description") or ""] if x)
        if any(b.lower() in (text[:400] + title).lower() for b in BLOCKED) and len(text) < 600:
            set_text(sid, "blocked", "", L("网站挡住了服务器（要验证或内容被删），原链接还在", "The site blocked the server (verification or removed); the link is still saved"), title or None)
            return
        if len(text) < 40:
            set_text(sid, "empty", head, L("这个页面要在手机上打开才看得到正文（要登录或跑脚本）", "This page needs a browser to show its text (login or scripts)"), title or None)
            return
        set_text(sid, "ok", (head + "\n\n" + text) if head and not text.startswith(title or "\x00") else text, None, title or None)
    except Exception as exc:  # noqa: BLE001 — 抓不到只记原因，收藏本身留着
        set_text(sid, "failed", "", L(f"没抓到正文：{str(exc)[:80]}", f"Couldn't fetch the text: {str(exc)[:80]}"))


async def extract_file(sid: str, path: Path, mime: str) -> None:
    import files as files_mod
    kind = files_mod.kind_of(path.name, mime)
    if kind not in ("doc",):
        set_text(sid, "none")
        return
    text, note = await asyncio.to_thread(files_mod.extract_text, path, kind, mime)
    set_text(sid, "ok" if text.strip() else "empty", text, note or None)


# —— 接口 ——————————————————————————————————————————————————————————

@router.get("/api/think/saves")
async def list_saves(filter: str = "all", limit: int = 200):  # noqa: A002 — 查询参数名
    rows = await asyncio.to_thread(all_rows)
    f = filter if filter in ("all", "new", "link", "file", "image", "text") else "all"
    if f == "new":
        rows = [r for r in rows if not r["seen_at"]]
    elif f == "file":
        rows = [r for r in rows if r["kind"] == "file"]
    elif f != "all":
        rows = [r for r in rows if r["kind"] == f or (f == "link" and r["kind"] == "chat")]
    return {"ok": True, "saves": [save_json(r) for r in rows[: max(1, min(limit, 500))]], "new": sum(1 for r in await asyncio.to_thread(all_rows) if not r["seen_at"])}


class SaveIn(BaseModel):
    url: str = ""
    text: str = ""
    title: str = ""
    note: str = ""
    source: str = ""
    keywords: list[str] = []


@router.post("/api/think/saves")
async def post_save(body: SaveIn):
    """粘贴进来的：一个链接（后台抓正文），或者一段文字（里面有链接就按链接存，其余当标题）。"""
    raw = (body.url or body.text).strip()
    if not raw:
        raise HTTPException(400, L("是空的", "It's empty"))
    m = URL_RE.search(raw)
    if m:
        url = m.group(0).rstrip(".,;:")
        rest = (raw[: m.start()] + " " + raw[m.end():]).strip()
        rest = re.sub(r"^【(.+?)】", r"\1", rest).strip(" -—|·")
        title = body.title.strip() or rest[:120] or (urlparse(url).hostname or url)
        sid = await asyncio.to_thread(insert, "link", title=title, url=url, source=body.source or source_of(url), note=body.note, keywords=body.keywords)
        with _lock, sdb() as conn:
            conn.execute("UPDATE think_saves SET text_status='fetching' WHERE id=?", (sid,))
        spawn(fetch_link(sid, url))
    else:
        title = body.title.strip() or raw.splitlines()[0][:60]
        sid = await asyncio.to_thread(insert, "text", title=title, source=body.source or L("粘贴", "Pasted"), note=body.note, keywords=body.keywords)
        await asyncio.to_thread(set_text, sid, "ok", raw)
    log_activity(L(f"收藏了「{title[:40]}」", f'Saved "{title[:40]}"'), "edit")
    return {"ok": True, "save": save_json(row(sid))}


@router.post("/api/think/saves/upload")
async def upload_saves(files: list[UploadFile] = File(...), note: str = Form(""), source: str = Form(""), keywords: str = Form("")):
    if not files or len(files) > 10:
        raise HTTPException(400, L("一次 1 到 10 个文件", "1 to 10 files at a time"))
    try:
        kws = json.loads(keywords) if keywords.strip() else []
    except ValueError:
        kws = []
    import files as files_mod
    out = []
    for f in files:
        tmp, name, size = think.save_upload(f)
        mime = f.content_type or ""
        kind = "image" if files_mod.kind_of(name, mime) == "image" else "file"
        sid = await asyncio.to_thread(insert, kind, title=Path(name).stem[:120] if kind == "file" else "", source=source or (L("相册", "Photos") if kind == "image" else L("文件", "Files")),
                                      note=note, keywords=kws, name=name, mime=mime, size=size)
        dst = saves_dir() / f"{sid}{Path(name).suffix.lower()}"
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(tmp), dst)
        with _lock, sdb() as conn:
            conn.execute("UPDATE think_saves SET file=? WHERE id=?", (str(dst), sid))
        if kind == "file":
            with _lock, sdb() as conn:
                conn.execute("UPDATE think_saves SET text_status='fetching' WHERE id=?", (sid,))
            spawn(extract_file(sid, dst, mime))
        out.append(sid)
    log_activity(L(f"收藏了 {len(out)} 个文件", f"Saved {len(out)} file{'' if len(out) == 1 else 's'}"), "edit")
    return {"ok": True, "saves": [save_json(row(s)) for s in out]}


class FromMessage(BaseModel):
    thread: str
    id: str
    note: str = ""


@router.post("/api/think/saves/from-message")
async def from_message(body: FromMessage):
    """对话里长按一条「收藏」：存下这条的文字（附件记着在哪个对话）。"""
    mid = chat.row_id(body.id)
    with _lock, db() as conn:
        m = conn.execute("SELECT * FROM messages WHERE thread=? AND id=?", (body.thread, mid)).fetchone()
    if not m:
        raise HTTPException(404, L("找不到这条消息", "Message not found"))
    text = m["text"] or ""
    first = next((ln.strip(" #*>-") for ln in text.splitlines() if ln.strip(" #*>-")), "")[:60]
    who = settings.app_name if m["role"] == "grava" else L("你", "You")
    sid = await asyncio.to_thread(insert, "chat", title=f"{who}：{first}" if first else who, source=L("对话", "Chat"), note=body.note,
                                  thread=body.thread, message_id=mid)
    await asyncio.to_thread(set_text, sid, "ok", text)
    return {"ok": True, "save": save_json(row(sid))}


@router.get("/api/think/saves/{sid}")
async def get_save(sid: str, full: int = 0):
    r = row(sid)
    text = await asyncio.to_thread(text_of, sid)
    return {"ok": True, "save": save_json(r), "text": text if full else text[:4000], "more": len(text) > 4000 and not full}


@router.get("/api/think/saves/{sid}/file")
async def save_file(sid: str, thumb: int = 0):
    r = row(sid)
    if not r["file"] or not Path(r["file"]).is_file():
        raise HTTPException(404, L("文件不在了", "File no longer exists"))
    path = Path(r["file"])
    if thumb and r["kind"] == "image":
        t = await asyncio.to_thread(think.thumb_of, path)
        if t:
            return FileResponse(str(t), media_type="image/jpeg")
    return FileResponse(str(path), media_type=r["mime"] or "application/octet-stream", filename=r["name"] or path.name)


class SavePatch(BaseModel):
    title: str | None = None
    note: str | None = None
    keywords: list[str] | None = None
    seen: bool | None = None


@router.patch("/api/think/saves/{sid}")
async def patch_save(sid: str, body: SavePatch):
    r = row(sid)
    title = body.title.strip()[:200] if body.title is not None and body.title.strip() else r["title"]
    note = body.note.strip()[:2000] if body.note is not None else r["note"]
    kws = think.clean_keywords((body.keywords if body.keywords is not None else json.loads(r["keywords"] or "[]"))
                               + (think.inline_tags(body.note) if body.note is not None else []))
    seen = (now_iso() if body.seen else None) if body.seen is not None else r["seen_at"]
    with _lock, sdb() as conn:
        conn.execute("UPDATE think_saves SET title=?, note=?, keywords=?, seen_at=?, updated_at=? WHERE id=?",
                     (title, note, json.dumps(kws, ensure_ascii=False), seen, now_iso(), sid))
    return {"ok": True, "save": save_json(row(sid))}


@router.delete("/api/think/saves/{sid}")
async def delete_save(sid: str):
    """软删：列表里不见了，原件和正文留着（能恢复）。"""
    r = row(sid)
    with _lock, sdb() as conn:
        conn.execute("UPDATE think_saves SET deleted_at=?, updated_at=? WHERE id=?", (now_iso(), now_iso(), sid))
    log_activity(L(f"删了收藏「{r['title'][:40]}」", f'Deleted the saved item "{r["title"][:40]}"'), "deleted")
    return {"ok": True}


@router.post("/api/think/saves/{sid}/restore")
async def restore_save(sid: str):
    row(sid, deleted=True)
    with _lock, sdb() as conn:
        conn.execute("UPDATE think_saves SET deleted_at=NULL, updated_at=? WHERE id=?", (now_iso(), sid))
    return {"ok": True, "save": save_json(row(sid))}


def save_context(sid: str, cap: int = 30_000) -> str:
    """把一条收藏给模型看（问 Grava、交给 Agent）：来源、链接、你的备注、抽出来的正文。"""
    r = row(sid)
    text = text_of(sid)
    lines = [LS(f"【收藏】Leo 让你看他收藏的一条（id {sid}）。", f"[Saved item] The user wants you to look at something they saved (id {sid})."),
             LS(f"标题：{r['title']}", f"Title: {r['title']}")]
    if r["source"]:
        lines.append(LS(f"来自：{r['source']}", f"From: {r['source']}"))
    if r["url"]:
        lines.append(LS(f"链接：{r['url']}", f"Link: {r['url']}"))
    if r["note"]:
        lines.append(LS(f"他写的备注：{r['note']}", f"Their note: {r['note']}"))
    kws = json.loads(r["keywords"] or "[]")
    if kws:
        lines.append(LS("关键词：", "Keywords: ") + " ".join("#" + k for k in kws))
    if r["file"]:
        lines.append(LS(f"原件（可以用工具打开）：{r['file']}", f"Original file (open it with your tools): {r['file']}"))
    if text:
        cut = text[:cap]
        lines.append(LS("正文（存的时候抽的）：", "Text (extracted when saved):") + "\n" + cut + ("\n……" if len(text) > cap else ""))
    elif r["text_note"]:
        lines.append(LS(f"（正文没存上：{r['text_note']}）", f"(No text saved: {r['text_note']})"))
    return "\n".join(lines)


class GiveIn(BaseModel):
    agent: str


@router.post("/api/think/saves/{sid}/give")
async def give(sid: str, body: GiveIn):
    """交给某个 Agent：在它的线程里开一轮（它按自己的规矩处理），回完静默推。"""
    r = row(sid)
    if body.agent not in settings.group_agents and body.agent != "main":
        raise HTTPException(404, L("没有这个 Agent", "No such agent"))
    ctx = await asyncio.to_thread(save_context, sid)
    chat.start_run(body.agent, LS(f"【收藏转来】{r['title']}", f"[From saved items] {r['title']}"), None, origin="auto", context=ctx, level="quiet")
    with _lock, sdb() as conn:
        conn.execute("UPDATE think_saves SET given_to=?, seen_at=COALESCE(seen_at, ?), updated_at=? WHERE id=?", (body.agent, now_iso(), now_iso(), sid))
    log_activity(L(f"把收藏「{r['title'][:40]}」交给了 {body.agent}", f'Handed "{r["title"][:40]}" to {body.agent}'), "edit")
    return {"ok": True, "save": save_json(row(sid))}


@router.post("/api/think/saves/{sid}/to-idea")
async def to_idea(sid: str):
    """放进思考：变成一条 kind=save 的碎片（标题、链接、你的备注、关键词），原件留在收藏里。"""
    r = row(sid)
    text = r["note"] or ""
    frag = await asyncio.to_thread(think.create_fragment, kind="save", text=text, title=r["title"][:think.TITLE_MAX], url=r["url"] or "",
                                   link_title=r["source"] or "", keywords=json.loads(r["keywords"] or "[]"), save=sid)
    with _lock, sdb() as conn:
        conn.execute("UPDATE think_saves SET seen_at=COALESCE(seen_at, ?), updated_at=? WHERE id=?", (now_iso(), now_iso(), sid))
    return {"ok": True, "fragment": frag}
