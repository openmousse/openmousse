"""学习台连 Canvas（个人访问令牌，2026-09-30）：列出在读的课，把一门课的课件和作业同步进课程档案。

- 令牌：Canvas 里 Account → Settings → Approved Integrations → New Access Token，贴进学习台的「连上 Canvas」。
  存在 ~/.openmousse/canvas-tokens.json（和 server.json 一个目录，0600），按 Canvas 网址一把；只读用：课程、模块、文件、作业。
  令牌只发给那个 Canvas 网址；下载文件时重定向到别的主机（Canvas 的文件存储）不带令牌。
- 同步（点一次同步一次，不定时）：模块里的文件按「模块名 + 文件名」归到每一节（归不了的进待归节），下过的不再下；
  作业 → 课程档案的截止（交了的标交了）；课程的 Syllabus 页 → 没读过大纲就读一遍。
- 学校关了学生建令牌（很多学校会关），就用大纲和课件。
测试：server.json 的 study.canvas_allow_local = true 时允许 http://127.0.0.1（假的 Canvas）。
"""
from __future__ import annotations

import asyncio
import copy
import json
import os
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import coursefile as cf
import study
import webfetch
from config import CONFIG_PATH, settings
from i18n import L

router = APIRouter()
TOKENS = CONFIG_PATH.parent / "canvas-tokens.json"
JOBS: dict[str, dict] = {}
MAX_FILE = 200 * 1024 * 1024
_tasks: set[asyncio.Task] = set()


class CanvasError(RuntimeError):
    pass


def read_tokens() -> dict:
    try:
        d = json.loads(TOKENS.read_text(encoding="utf8"))
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def write_tokens(d: dict) -> None:
    TOKENS.parent.mkdir(parents=True, exist_ok=True)
    tmp = TOKENS.with_name(f".{TOKENS.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    tmp.replace(TOKENS)
    os.chmod(TOKENS, 0o600)


def norm_base(url: str) -> str:
    u = urlsplit((url or "").strip())
    if u.scheme.lower() not in ("https", "http") or not u.hostname:
        raise CanvasError(L("Canvas 网址写成 https://canvas.你们学校.edu", "Write the Canvas address like https://canvas.your-school.edu"))
    return f"{u.scheme.lower()}://{u.hostname.lower()}" + (f":{u.port}" if u.port else "")


async def check_base(base: str) -> None:
    """只连公网上的 Canvas（https）；测试时 study.canvas_allow_local 放行本机。"""
    u = urlsplit(base)
    if study.cfg().get("canvas_allow_local") and u.hostname in ("127.0.0.1", "localhost"):
        return
    if u.scheme != "https":
        raise CanvasError(L("Canvas 网址要 https://", "The Canvas address must be https://"))
    try:
        await webfetch.public(u.hostname or "", u.port or 443)
    except webfetch.FetchError as e:
        raise CanvasError(str(e)) from e


class Client:
    def __init__(self, base: str, token: str):
        self.base, self.token = base, token
        self.http = httpx.AsyncClient(timeout=httpx.Timeout(60, connect=15), follow_redirects=False)

    async def close(self) -> None:
        await self.http.aclose()

    async def get(self, path: str, params: dict | None = None):
        """GET 一页或跟着 Link: rel=next 翻完（最多 20 页）。"""
        url = path if path.startswith("http") else f"{self.base}/api/v1{path}"
        out: list = []
        for _ in range(20):
            if urlsplit(url).netloc != urlsplit(self.base).netloc:
                raise CanvasError("unexpected host")
            r = await self.http.get(url, params=params, headers={"Authorization": f"Bearer {self.token}"})
            params = None
            if r.status_code == 401:
                raise CanvasError(L("令牌不对或者过期了：在 Canvas 里重新建一个", "The token is wrong or expired: make a new one in Canvas"))
            if r.status_code == 403:
                raise CanvasError(L("Canvas 不让看这一项（学校可能关了）", "Canvas won't show this (your school may have turned it off)"))
            if r.status_code >= 400:
                raise CanvasError(f"Canvas HTTP {r.status_code}")
            data = r.json()
            if not isinstance(data, list):
                return data
            out += data
            nxt = re.search(r'<([^>]+)>;\s*rel="next"', r.headers.get("link") or "")
            if not nxt:
                break
            url = nxt.group(1)
        return out

    async def download(self, url: str, dest: Path) -> int:
        """下载一个文件：Canvas 自己的地址带令牌，重定向到别的主机（文件存储）不带；最多 200 MB。→ 字节数"""
        cur = url
        for _ in range(6):
            same = urlsplit(cur).netloc == urlsplit(self.base).netloc
            if not same:
                u = urlsplit(cur)
                if u.scheme != "https" and not study.cfg().get("canvas_allow_local"):
                    raise CanvasError("insecure file host")
                if not study.cfg().get("canvas_allow_local"):
                    await webfetch.public(u.hostname or "", u.port or 443)
            async with self.http.stream("GET", cur, headers={"Authorization": f"Bearer {self.token}"} if same else {}) as r:
                if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                    cur = str(httpx.URL(cur).join(r.headers["location"]))
                    continue
                if r.status_code >= 400:
                    raise CanvasError(f"HTTP {r.status_code}")
                n = 0
                tmp = dest.with_name(f".{dest.name}.part")
                with tmp.open("wb") as f:
                    async for chunk in r.aiter_bytes():
                        n += len(chunk)
                        if n > MAX_FILE:
                            raise CanvasError(L("文件太大（超过 200 MB）", "File too big (over 200 MB)"))
                        f.write(chunk)
                tmp.replace(dest)
                return n
        raise CanvasError("too many redirects")


def token_for(base: str) -> str:
    t = (read_tokens().get(base) or {}).get("token")
    if not t:
        raise HTTPException(400, L("还没连上这个 Canvas：先在「连上 Canvas」里贴令牌", "Not connected to this Canvas yet: paste a token under Connect Canvas first"))
    return t


def slim_course(x: dict) -> dict:
    term = (x.get("term") or {}).get("name") if isinstance(x.get("term"), dict) else None
    return {"id": x.get("id"), "name": x.get("name") or x.get("course_code") or str(x.get("id")), "code": x.get("course_code"), "term": term}


async def list_courses(base: str, token: str) -> list[dict]:
    cl = Client(base, token)
    try:
        items = await cl.get("/courses", {"enrollment_state": "active", "per_page": 50, "include[]": "term"})
    finally:
        await cl.close()
    return [slim_course(x) for x in items if isinstance(x, dict) and x.get("id") and not x.get("access_restricted_by_date")]


class ConnectIn(BaseModel):
    base: str
    token: str


@router.post("/api/study/canvas/connect")
async def connect(body: ConnectIn):
    """贴令牌：认一下是谁（users/self），存下来，列出在读的课。"""
    try:
        base = norm_base(body.base)
        await check_base(base)
        tok = body.token.strip()
        if len(tok) < 20 or any(c.isspace() for c in tok):
            raise CanvasError(L("令牌看着不对：从 Canvas 里整串复制过来", "That doesn't look like a token: copy the whole string from Canvas"))
        cl = Client(base, tok)
        try:
            me = await cl.get("/users/self")
        finally:
            await cl.close()
        d = read_tokens()
        d[base] = {"token": tok, "user": (me or {}).get("name") if isinstance(me, dict) else None, "added": cf.now_iso()}
        write_tokens(d)
        courses = await list_courses(base, tok)
    except CanvasError as e:
        raise HTTPException(400, str(e)) from None
    except httpx.HTTPError as e:
        raise HTTPException(502, L(f"连不上 Canvas：{type(e).__name__}", f"Can't reach Canvas: {type(e).__name__}")) from None
    return {"ok": True, "base": base, "user": d[base]["user"], "courses": courses}


@router.get("/api/study/canvas")
async def status():
    """连着哪些 Canvas（不给令牌）。"""
    return {"ok": True, "sites": [{"base": b, "user": v.get("user"), "added": v.get("added")} for b, v in read_tokens().items()]}


@router.delete("/api/study/canvas")
async def disconnect(base: str):
    d = read_tokens()
    d.pop(norm_base(base), None)
    write_tokens(d)
    return {"ok": True}


class LinkIn(BaseModel):
    course_id: int
    base: str | None = None


@router.post("/api/study/courses/{course}/canvas")
async def link(course: str, body: LinkIn):
    """这门课对应 Canvas 上的哪一门，开始同步（后台）。"""
    c = cf.load(course)
    if not c:
        raise HTTPException(404, L("没有这门课", "No such course"))
    sites = read_tokens()
    base = norm_base(body.base) if body.base else ((c.get("canvas") or {}).get("base") or (next(iter(sites)) if len(sites) == 1 else None))
    if not base or base not in sites:
        raise HTTPException(400, L("先连上 Canvas（贴令牌）", "Connect Canvas first (paste a token)"))
    if (JOBS.get(course) or {}).get("status") == "running":
        return {"ok": True, "job": JOBS[course]}
    JOBS[course] = {"status": "running", "started": cf.now_iso(), "stage": L("在读 Canvas 上的课…", "Reading the course on Canvas…")}
    t = asyncio.create_task(sync(course, base, body.course_id))
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)
    return {"ok": True, "job": JOBS[course], "note": L("在同步课件和作业（大的课要几分钟）", "Syncing files and assignments (a few minutes for big courses)")}


def kind_of(name: str, types: list | None) -> str:
    t = name.lower()
    if "quiz" in t or "online_quiz" in (types or []):
        return "quiz"
    if "group" in t:
        return "group"
    if "presentation" in t or "video" in t:
        return "presentation"
    if "exam" in t or "test" in t:
        return "exam"
    return "assignment"


async def sync(course: str, base: str, cid: int) -> None:
    """同步一次：课程信息和 Syllabus → 作业 → 模块里的文件。"""
    import courses as courses_mod
    cl = Client(base, token_for(base))
    try:
        info = await cl.get(f"/courses/{cid}", {"include[]": "syllabus_body"})
        name = (info or {}).get("name") if isinstance(info, dict) else None
        JOBS[course] |= {"stage": L("在读作业和截止…", "Reading assignments…")}
        items = await cl.get(f"/courses/{cid}/assignments", {"per_page": 100, "include[]": "submission"})
        with cf._lock:
            before = cf.load(course)
            assert before is not None
            c = copy.deepcopy(before)
            c["canvas"] = {**(c.get("canvas") or {}), "base": base, "course_id": cid, "name": name}
            have = {d["id"]: d for d in c["deadlines"]}
            by_url = {d["url"]: d for d in c["deadlines"] if d.get("url")}
            by_n = {s["n"]: s["id"] for s in cf.live_sessions(c)}
            for a in items if isinstance(items, list) else []:
                if not isinstance(a, dict) or not a.get("id") or not a.get("name"):
                    continue
                did = f"d-cv{a['id']}"
                sub = a.get("submission") or {}
                done = bool(a.get("has_submitted_submissions")) or sub.get("workflow_state") in ("submitted", "graded", "pending_review")
                due = None
                if a.get("due_at"):
                    try:
                        due = datetime.fromisoformat(str(a["due_at"]).replace("Z", "+00:00")).astimezone(settings.tz).strftime("%Y-%m-%d %H:%M")
                    except ValueError:
                        due = None
                m = re.search(r"session\s*(\d+)|week\s*(\d+)|lecture\s*(\d+)", a["name"], re.I)
                sess = by_n.get(int(next(g for g in m.groups() if g))) if m else None
                row = {"id": did, "title": a["name"], "due": due, "kind": kind_of(a["name"], a.get("submission_types")), "session": sess, "done": done,
                       "url": a.get("html_url")}
                # 大纲里已经读到的同一个作业（截止时间一样、还没挂 Canvas 链接）：挂上链接、交没交跟着 Canvas，不另加一条
                same = have.get(did) or by_url.get(row["url"] or "") or next(
                    (d for d in cf.live_deadlines(c) if not d.get("url") and d["due"] and due and d["due"][:16] == due[:16]), None)
                if same:
                    same.update({"url": row["url"], "done": done, "due": due or same["due"], **({"session": sess} if sess and not same.get("session") else {})})
                else:
                    c["deadlines"].append(row)
            c = cf.normalize(c, course)
            saved = cf.save(course, c)
        courses_mod.sync_agent(course)
        syl_html = (info or {}).get("syllabus_body") if isinstance(info, dict) else None
        if syl_html and not (saved.get("syllabus") or {}).get("read_at"):
            import coursegen
            from preview import _html_text
            text = _html_text(syl_html)
            if len(text) > 200:
                courses_mod.set_syllabus(course, {"file": None, "url": f"{base}/courses/{cid}/assignments/syllabus"})
                coursegen.start_syllabus(course, text, "Canvas Syllabus", "user")
        JOBS[course] |= {"stage": L("在下课件…", "Downloading files…")}
        mods = await cl.get(f"/courses/{cid}/modules", {"per_page": 100, "include[]": "items"})
        got, filed = await pull_files(course, cl, mods if isinstance(mods, list) else [])
        with cf._lock:
            c = cf.load(course)
            assert c is not None
            c["canvas"] = {**(c.get("canvas") or {}), "synced_at": cf.now_iso(), "files": {**((c.get("canvas") or {}).get("files") or {}), **got}}
            cf.save(course, c)
        JOBS[course] = {"status": "done", "finished": cf.now_iso(), "files": len(got), "filed": filed,
                        "stage": L(f"同步好了：新下了 {len(got)} 个文件", f"Synced: {len(got)} new file(s)")}
    except (CanvasError, httpx.HTTPError, HTTPException, OSError, ValueError) as e:
        JOBS[course] = {"status": "error", "finished": cf.now_iso(), "error": str(getattr(e, "detail", None) or e)[:300],
                        "stage": L("没同步成：", "Sync failed: ") + str(getattr(e, "detail", None) or e)[:200]}
    finally:
        await cl.close()


async def pull_files(course: str, cl: Client, mods: list) -> tuple[dict, int]:
    """模块里的文件：没下过的下到待归节，再按「模块名 + 文件名」归到每一节。→ ({Canvas 文件 id: 相对路径}, 归好的个数)"""
    import courses as courses_mod
    c = cf.load(course)
    assert c is not None
    known = set(((c.get("canvas") or {}).get("files") or {}).keys())
    base = cf.course_root(course)
    assert base is not None
    inc = base / cf.INCOMING
    inc.mkdir(parents=True, exist_ok=True)
    got: dict[str, str] = {}
    todo = []
    for m in mods:
        mname = str(m.get("name") or "")
        for it in m.get("items") or []:
            if not isinstance(it, dict) or it.get("type") != "File" or not it.get("content_id"):
                continue
            fid = str(it["content_id"])
            if fid in known or fid in got:
                continue
            meta = await cl.get(f"/files/{fid}")
            if not isinstance(meta, dict) or not meta.get("url"):
                continue
            fname = cf.BAD_CHARS.sub(" ", str(meta.get("display_name") or meta.get("filename") or f"file-{fid}")).strip() or f"file-{fid}"
            dest = cf.unique(inc / fname)
            JOBS[course] |= {"stage": L(f"在下 {fname}…", f"Downloading {fname}…")}
            await cl.download(meta["url"], dest)
            todo.append((dest, mname, fid))
    filed = 0
    for dest, mname, fid in todo:
        with cf._lock:
            before = cf.load(course)
            assert before is not None
            cc = copy.deepcopy(before)
            cls = cf.classify(cc, f"{mname} {dest.name}")
            forced = cls.get("session") or ("course" if cls.get("course_info") else None)
            res = courses_mod.file_into(course, cc, dest, forced, "user")
            cf.save(course, cf.normalize(cc, course))
        for f in res.get("files") or []:
            got[fid] = f["file"]
            filed += f["status"] in ("filed", "info")
    return got, filed


def job_of(course: str) -> dict | None:
    return JOBS.get(course)
