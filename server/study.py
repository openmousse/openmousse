"""学习台：按课程 / 模块看课件和学习页，就着这一节的材料问助手，生成闪卡和小测。网页在 /study（宽屏，给电脑用）。

配置在 server.json 的 study（没有就不启用，/study 会说明怎么配）：
  materials       课件目录：一门课一个文件夹，下面一层是模块（周 / session），模块里放文件。
                  按模块下载的课程平台镜像（比如 Canvas）正好是这个结构；手动整理的文件夹也行。
  pages           学习页目录：一门课一个文件夹（和 materials 里同名），Markdown，开头是 YAML front matter：
                    session: 3              第几节（排序；和 media/ 里的视频配对）
                    title: …
                    sources: [课件路径]      相对 materials/<课程>/。学习页挂在第一个来源所在的模块下；问答时这些文件的全文一起给模型
                  <课程>/media/S03 …mp4      这一节的视频（文件名以 S + 序号开头）
                  <课程>/.gen/               生成的闪卡 / 小测（JSON）
  courses         可选：显示哪几门、按什么顺序（文件夹名）。不写 = materials 下所有带模块的文件夹
  readings        可选：阅读清单目录，<课程>.json = {"items": [{title, kind, required, instructions, sessions: [3], file?, status, url?}]}；
                  file 相对 materials/<课程>/。每节显示「阅读」标签，必读材料不齐时生成前会先提醒
  recordings      可选：录播字幕目录，<课程>/index.json 列出录播（id, name, start, duration, sessions, file, has_captions），
                  file 指向 {viewer_url, segments: [{t: 秒, text}]}。每节显示「录播」标签，字幕进问答前情
  context_chars   可选：问答前情的字数上限（默认 150000）；gen_context_chars：生成学习路线等的上限（默认 320000）
  deadlines_cmd   可选：打印 ddl JSON 数组的命令（[{due: "YYYY-MM-DD HH:MM", course, title, url?}]），缓存 30 分钟
  video_cmd       可选：渲染视频的命令（argv 列表，{script} = 助手写的 Manim 脚本，{media_dir} = 工作目录），配了才出现「生成视频」。
                  这会在本机运行助手写的代码：只在已经信任助手能在这台机器上执行代码时打开。

问答走 chat.py 的同一条通道：每个学习页（或单个课件）一个线程 study-<hash>。每个逻辑日的第一条消息把学习页和课件全文
作为前情一起发给模型（会话每天 04:00 重置），对话记录里只显示提问本身。生成闪卡 / 小测 / 学习路线每次用一个新的 session key，互不累积。
学习路线的打勾进度存在 pages/<课程>/.gen/progress.json。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import mimetypes
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

import yaml
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

import chat
import files as files_mod
from config import raw, settings
from i18n import L

router = APIRouter()
PAGE_HTML = Path(__file__).resolve().parent / "static" / "study.html"
CONTEXT_CHARS = 150_000
GEN_CONTEXT_CHARS = 320_000
TRANSCRIPT_CHARS = 80_000
TEXT_EXT = {".md", ".markdown", ".txt", ".rmd", ".r", ".py", ".csv", ".tex", ".json"}
VIDEO_EXT = {".mp4", ".webm", ".mov", ".m4v"}
KINDS = ("cards", "quiz", "path")
KIND_ORDER = {"textbook": 0, "case": 1, "article": 2, "note": 3, "news": 4, "web": 5, "book": 6}
SESSION_RE = re.compile(r"\b(session|week|lecture|lesson|topic|unit|chapter)\s*\d+|第\s*\d+\s*[周讲课节章]", re.I)
_text_cache: dict[str, tuple[float, str]] = {}
_json_cache: dict[str, tuple[float, object]] = {}
_deadlines: tuple[float, dict] | None = None
_progress_lock = threading.Lock()
JOBS: dict[str, dict] = {}


def cfg() -> dict:
    return raw().get("study") or {}


def root(name: str) -> Path | None:
    value = cfg().get(name)
    return Path(value).expanduser() if value else None


def courses() -> list[str]:
    """materials 下的课（带模块子文件夹的才算）；配置了 courses 就按它筛选和排序。"""
    mat = root("materials")
    if not mat or not mat.is_dir():
        return []
    found = [p.name for p in sorted(mat.iterdir())
             if p.is_dir() and not p.name.startswith((".", "_")) and any(c.is_dir() for c in p.iterdir())]
    want = [str(x) for x in cfg().get("courses") or []]
    return [c for c in want if c in found] if want else found


def course_dir(course: str) -> Path:
    if course not in courses():
        raise HTTPException(404, L("没有这门课", "No such course"))
    return root("materials") / course  # type: ignore[operator]


def pages_dir(course: str) -> Path | None:
    base = root("pages")
    return base / course if base else None


def inside(base: Path, rel: str) -> Path:
    """base 下的文件；越界（../）或不存在一律 404。"""
    p = (base / rel).resolve()
    if not p.is_relative_to(base.resolve()) or not p.is_file():
        raise HTTPException(404, L("找不到这个文件", "File not found"))
    return p


def module_title(folder: str) -> str:
    """「04 Session 1_ Demand and Costs」→「Session 1: Demand and Costs」：去掉排序号，镜像时被替换掉的冒号换回来。"""
    return re.sub(r"^\d+\s+", "", folder).replace("_ ", ": ")


def kind_of(name: str) -> str:
    ext = Path(name).suffix.lower()
    if ext == ".pdf":
        return "pdf"
    if ext in (".md", ".markdown"):
        return "md"
    if ext in VIDEO_EXT:
        return "video"
    if ext in TEXT_EXT:
        return "text"
    return "other"


def read_page(path: Path) -> tuple[dict, str]:
    """学习页 → (front matter, 正文)。"""
    text = path.read_text(encoding="utf8", errors="replace")
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.S)
    if not m:
        return {}, text
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        meta = {}
    return (meta if isinstance(meta, dict) else {}), text[m.end():]


def session_of(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def thread_of(course: str, kind: str, path: str) -> str:
    return "study-" + hashlib.sha1(f"{course}\n{kind}\n{path}".encode()).hexdigest()[:12]


def videos_of(course: str) -> list[dict]:
    pdir = pages_dir(course)
    media = pdir / "media" if pdir else None
    if not media or not media.is_dir():
        return []
    out = []
    for f in sorted(media.iterdir()):
        if f.is_file() and f.suffix.lower() in VIDEO_EXT:
            m = re.match(r"^S(\d+)\s*[-_ ]?\s*(.*)$", f.stem, re.I)
            out.append({"path": f"media/{f.name}", "name": (m.group(2) if m and m.group(2) else f.stem), "session": int(m.group(1)) if m else None,
                        "size": f.stat().st_size})
    return out


def gen_dir(course: str) -> Path:
    return (pages_dir(course) or settings.data_dir / "study" / course) / ".gen"


def gen_path(unit: dict, kind: str) -> Path:
    return gen_dir(unit["course"]) / f"{unit['thread']}.{kind}.json"


def file_entry(cdir: Path, f: Path) -> dict:
    return {"path": str(f.relative_to(cdir)), "name": f.name, "size": f.stat().st_size, "kind": kind_of(f.name)}


def load_json(path: Path | None):
    """小 JSON 文件（阅读清单、录播索引、学习路线），按 mtime 缓存；没有或坏了返回 None。"""
    if not path:
        return None
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    hit = _json_cache.get(str(path))
    if hit and hit[0] == mtime:
        return hit[1]
    try:
        data = json.loads(path.read_text(encoding="utf8"))
    except (OSError, ValueError):
        data = None
    _json_cache[str(path)] = (mtime, data)
    return data


def sessions_in(value) -> set[int]:
    return {n for n in (session_of(x) for x in (value if isinstance(value, list) else [value])) if n is not None}


def readings_of(course: str, session: int | None) -> list[dict]:
    """阅读清单里属于这一节的条目：必读在前，教材、case、文章、新闻依次排。file 在 materials 里真有才算到手。"""
    base, mat = root("readings"), root("materials")
    if not base or not mat or session is None:
        return []
    data = load_json(base / f"{course}.json")
    items = data.get("items") if isinstance(data, dict) else data
    out = []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict) or session not in sessions_in(it.get("sessions")):
            continue
        f = it.get("file")
        have = isinstance(f, str) and (mat / course / f).is_file()
        status = "have" if have else ("missing" if it.get("status") in (None, "have") else str(it["status"]))
        out.append({k: it.get(k) for k in ("id", "title", "authors", "kind", "instructions", "url", "access", "error")}
                   | {"required": bool(it.get("required")), "file": f if have else None, "status": status})
    out.sort(key=lambda x: (not x["required"], KIND_ORDER.get(str(x.get("kind")), 9), str(x.get("title") or "")))
    return out


def recordings_of(course: str, session: int | None, every: bool = False) -> list[dict]:
    """录播索引里属于这一节的录播，按时间排。path = 带字幕的 JSON（没有字幕就是 None）。
    同一堂课常有几份（两个班各录一次、同一个 tutorial 连上几场）：按「覆盖哪几节 × 时长」分组，每组只留一份——
    有字幕的优先，其次字幕完整的、自己课表上那一场（in_timetable）、字幕条数多的。every=True 时全部返回。"""
    base = root("recordings")
    if not base or session is None:
        return []
    cdir = base / course
    data = load_json(cdir / "index.json")
    items = (data.get("recordings") or data.get("items")) if isinstance(data, dict) else data
    found = []
    for r in items if isinstance(items, list) else []:
        if not isinstance(r, dict) or session not in sessions_in(r.get("sessions")):
            continue
        f = str(r.get("file") or "")
        path = (Path(f).expanduser() if f.startswith(("/", "~")) else cdir / f) if f else None
        if path and path.suffix.lower() != ".json":
            path = path.with_suffix(".json")
        detail = load_json(path) if path and path.is_file() else None
        detail = detail if isinstance(detail, dict) else {}
        pick = lambda k: r.get(k) if r.get(k) is not None else detail.get(k)  # noqa: E731
        try:
            hours = round(float(pick("duration") or 0) / 3600)
        except (TypeError, ValueError):
            hours = 0
        covers = tuple(sorted(sessions_in(pick("sessions"))))
        found.append({"id": str(pick("id") or ""), "name": str(pick("name") or ""), "start": pick("start"), "duration": pick("duration"),
                      "viewer_url": pick("viewer_url"), "has_captions": bool(detail.get("segments")), "path": path if detail else None,
                      "mine": bool(r.get("in_timetable")), "coverage": float(r.get("caption_coverage") or (1 if detail.get("segments") else 0)),
                      "segments": int(r.get("segments") or len(detail.get("segments") or [])), "note": r.get("note"),
                      "kind": "class" if len(covers) > 1 else ("tutorial" if 0 < hours <= 1 else "lecture"), "group": (covers, hours)})
    if not every:
        best: dict = {}
        for r in found:
            score = (r["has_captions"], r["coverage"] >= 0.9, r["mine"], r["segments"])
            if r["group"] not in best or score > best[r["group"]][0]:
                best[r["group"]] = (score, r)
        found = [r for _, r in best.values()]
    found.sort(key=lambda x: ({"lecture": 0, "class": 1, "tutorial": 2}[x["kind"]], str(x.get("start") or "")))
    return found


def rec_title(r: dict) -> str:
    """「讲课 · 9/7 周一 11:00（你那一班）」：比 Panopto 的房间号名字好认。"""
    kind = {"lecture": L("讲课", "Lecture"), "class": L("助教做题课", "Class (TA)"), "tutorial": "Tutorial"}.get(r.get("kind") or "", L("录播", "Recording"))
    try:
        d = datetime.fromisoformat(str(r.get("start")))
        when = L(f"{d.month}/{d.day} 周{'一二三四五六日'[d.weekday()]} {d:%H:%M}", f"{d:%a} {d.day}/{d.month} {d:%H:%M}")
    except (TypeError, ValueError):
        when = str(r.get("start") or "")[:16]
    return f"{kind} · {when}" + (L("（你那一班）", " (your class)") if r.get("mine") else "")


def slim_rec(r: dict) -> dict:
    return {k: r.get(k) for k in ("id", "name", "start", "duration", "viewer_url", "has_captions", "mine", "kind", "note")} | {"title": rec_title(r)}


def segments_of(rec: dict) -> list[dict]:
    data = load_json(rec.get("path"))
    segs = data.get("segments") if isinstance(data, dict) else None
    out = []
    for s in segs if isinstance(segs, list) else []:
        if isinstance(s, dict) and str(s.get("text") or "").strip():
            try:
                out.append({"t": float(s.get("t") or 0), "text": str(s["text"]).strip()})
            except (TypeError, ValueError):
                continue
    return out


def slides_of(rec: dict) -> list[dict]:
    """录播里翻到第几页课件的时间点（Panopto 记下的幻灯片切换）：[{t, n, title}]。"""
    data = load_json(rec.get("path"))
    out = []
    for s in (data.get("slides") if isinstance(data, dict) else None) or []:
        try:
            out.append({"t": float(s["t"]), "n": int(s["n"]), "title": str(s.get("title") or "").strip()})
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(out, key=lambda s: s["t"])


def clock(t: float) -> str:
    t = int(t)
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}"


def transcript_chunks(segs: list[dict], slides: list[dict] | None = None, every: float = 30.0) -> list[dict]:
    """字幕并成大约 every 秒一段，读起来像段落：[{t, text, slide?}]。翻到新的一页课件就另起一段，并记下页码。"""
    out: list[dict] = []
    marks = list(slides or [])
    for s in segs:
        slide = None
        while marks and marks[0]["t"] <= s["t"] + 0.5:
            slide = marks.pop(0)
        if out and slide is None and s["t"] - out[-1]["t"] < every:
            out[-1]["text"] += " " + s["text"]
        else:
            out.append(dict(s) | ({"slide": slide["n"], "slide_title": slide["title"]} if slide else {}))
    return out


def transcript_text(rec: dict) -> str:
    lines = []
    for c in transcript_chunks(segments_of(rec), slides_of(rec)):
        page = L(f"〔课件 p.{c['slide']}" + (f" {c['slide_title']}" if c.get("slide_title") else "") + "〕 ", f"(slide p.{c['slide']}) ") if c.get("slide") else ""
        lines.append(f"[{clock(c['t'])}] {page}{c['text']}")
    return "\n".join(lines)[:TRANSCRIPT_CHARS]


def materials_of(unit: dict) -> dict:
    """这一节的阅读材料和录播；missing = 还没到手的必读（生成学习路线、闪卡、小测前要先补齐）。"""
    if unit["kind"] != "page":
        return {"readings": [], "recordings": [], "missing": [], "complete": True}
    readings = readings_of(unit["course"], unit.get("session"))
    missing = [r for r in readings if r["required"] and r["status"] != "have"]
    return {"readings": readings, "recordings": recordings_of(unit["course"], unit.get("session")), "missing": missing, "complete": not missing}


def headings_of(body: str) -> list[tuple[str, str]]:
    """学习页里的二、三级标题 → [(id, 标题)]；id 按出现顺序 s1、s2…，前端按同样顺序给标题加锚点。"""
    out, fence = [], False
    for line in body.splitlines():
        if line.lstrip().startswith("```"):
            fence = not fence
            continue
        m = None if fence else re.match(r"^#{2,3}\s+(.+?)\s*#*\s*$", line)
        if m:
            out.append((f"s{len(out) + 1}", m.group(1)))
    return out


def progress_path(course: str) -> Path:
    return gen_dir(course) / "progress.json"


def read_progress(course: str) -> dict:
    try:
        data = json.loads(progress_path(course).read_text(encoding="utf8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def done_steps(course: str, page: str, route: dict | None, progress: dict | None = None) -> list[int]:
    """学习路线里打过勾的步骤；路线重新生成过就从头算。"""
    if not route:
        return []
    entry = (progress if progress is not None else read_progress(course)).get(page) or {}
    if not isinstance(entry, dict) or entry.get("gen") != route.get("generated"):
        return []
    n = len(route.get("items") or [])
    return sorted({i for i in entry.get("done") or [] if isinstance(i, int) and 0 <= i < n})


def course_tree(course: str) -> dict:
    cdir = course_dir(course)
    modules, index = [], {}
    for m in sorted(p for p in cdir.iterdir() if p.is_dir() and not p.name.startswith(".")):
        mod = {"id": m.name, "title": module_title(m.name),
               "files": [file_entry(cdir, f) for f in sorted(m.iterdir()) if f.is_file() and not f.name.startswith(".")], "pages": []}
        modules.append(mod)
        index[m.name] = mod
    loose: list[dict] = []
    videos = videos_of(course)
    pdir = pages_dir(course)
    progress = read_progress(course)
    if pdir and pdir.is_dir():
        for f in sorted(pdir.glob("*.md")):
            meta, _ = read_page(f)
            session = session_of(meta.get("session"))
            readings = readings_of(course, session)
            in_readings = {r["file"] for r in readings if r["file"]}
            # 阅读材料在「阅读」标签里，不再各占一个标签，也不决定学习页挂在哪个模块下
            sources = [str(s) for s in meta.get("sources") or [] if isinstance(s, str) and (cdir / s).is_file() and s not in in_readings]
            unit = {"course": course, "thread": thread_of(course, "page", f.name)}
            route = load_json(gen_path(unit, "path"))
            route = route if isinstance(route, dict) else None
            entry = {"path": f.name, "title": str(meta.get("title") or f.stem), "session": session, "sources": sources,
                     "videos": [v for v in videos if session is not None and v["session"] == session],
                     "video_candidates": [str(x) for x in meta.get("video_candidates") or [] if isinstance(x, str)],
                     "generated": {k: gen_path(unit, k).is_file() for k in KINDS},
                     "readings": {"have": len(in_readings), "total": len(readings), "missing": sum(r["required"] and r["status"] != "have" for r in readings)},
                     "recordings": len(recordings_of(course, session)),
                     "progress": {"done": len(done_steps(course, f.name, route, progress)), "total": len(route.get("items") or [])} if route else None}
            target = next((index[s.split("/")[0]] for s in sources if s.split("/")[0] in index), None)
            (target["pages"] if target else loose).append(entry)
    # 左栏按「周 / 节」排：有学习页或名字像一节课的模块在上面，课程信息、作业说明、阅读清单这类资料模块收到下面；
    # 已经挂进某个学习页（标签或阅读）的文件标 used，左栏不再重复列
    used = {s for mod in modules for p in mod["pages"] for s in p["sources"]} | {s for p in loose for s in p["sources"]}
    base = root("readings")
    data = load_json(base / f"{course}.json") if base else None
    for it in (data.get("items") if isinstance(data, dict) else data) or []:
        if isinstance(it, dict) and isinstance(it.get("file"), str) and sessions_in(it.get("sessions")):
            used.add(it["file"])
    for mod in modules:
        mod["pages"].sort(key=lambda p: (p["session"] is None, p["session"] or 0, p["path"]))
        mod["kind"] = "session" if mod["pages"] or SESSION_RE.search(mod["title"]) else "resource"
        for f in mod["files"]:
            f["used"] = f["path"] in used
    loose.sort(key=lambda p: (p["session"] is not None, p["session"] or 0, p["path"]))  # 总览（没有 session）排前面
    return {"name": course, "modules": modules, "pages": loose,
            "videos": [v for v in videos if not any(p["session"] == v["session"] for mod in modules for p in mod["pages"])]}


def resolve_unit(course: str, page: str | None, file: str | None) -> dict:
    """一个学习单元：学习页（带它的课件）或单个课件。"""
    cdir = course_dir(course)
    if page:
        pdir = pages_dir(course)
        if not pdir:
            raise HTTPException(404, L("还没配置学习页目录（server.json 的 study.pages）", "No study pages directory configured (study.pages in server.json)"))
        path = inside(pdir, page)
        meta, body = read_page(path)
        sources = [cdir / s for s in meta.get("sources") or [] if isinstance(s, str) and (cdir / s).is_file()]
        return {"course": course, "kind": "page", "path": page, "title": str(meta.get("title") or path.stem), "body": body,
                "sources": sources, "thread": thread_of(course, "page", page), "session": session_of(meta.get("session"))}
    if file:
        path = inside(cdir, file)
        return {"course": course, "kind": "file", "path": file, "title": path.name, "body": None, "sources": [path],
                "thread": thread_of(course, "file", file), "session": None}
    raise HTTPException(400, L("要指定学习页或课件", "Specify a study page or a file"))


def text_of(path: Path) -> str:
    """课件全文（按 mtime 缓存）。PDF 带页码标记。"""
    key, mtime = str(path), path.stat().st_mtime
    hit = _text_cache.get(key)
    if hit and hit[0] == mtime:
        return hit[1]
    if path.suffix.lower() in TEXT_EXT:
        text = path.read_text(encoding="utf8", errors="replace")[:files_mod.TEXT_CHARS_PER_FILE]
    else:
        mime = mimetypes.guess_type(path.name)[0] or ""
        text, _ = files_mod.extract_text(path, files_mod.kind_of(path.name, mime), mime)
    _text_cache[key] = (mtime, text)
    return text


def context_for(unit: dict, budget: int | None = None) -> str:
    """发给模型的前情：学习页 → 课件全文 → 录播字幕 → 阅读材料，超出字数上限的只给路径。"""
    head = L(f"【学习台】我正在看《{unit['course']}》的「{unit['title']}」。下面是这一节的学习页、课件原文（PDF 按页标了页码）、"
             "录播字幕（老师课上的原话，[时:分:秒] 是录播里的时间点）和阅读材料。回答以这些材料为准，引用时写出处（文件名和页码，或录播时间点）；"
             "材料里没有、你补充的内容要标出来。用中文回答，专有名词第一次出现写成「中文 English」，比如「边际收益 Marginal Revenue (MR)」。"
             "这是学习讨论，不用改任何文件；课件是图为主、文字抽不全，或者材料太长没放进来时，可以按路径用工具读原文件。",
             f"[Study desk] I'm studying “{unit['title']}” in {unit['course']}. Below are this session's study notes, the full text of its "
             "course materials (PDF pages are marked), the lecture captions (what the lecturer said in class; [h:mm:ss] are recording times) and "
             "the readings. Base answers on these materials and cite them (file name and page, or recording time); flag anything you add "
             "from outside them. This is a study discussion, don't change any files; if slides are mostly images and the text is thin, or a "
             "material was too long to include, you can read the original file at its path with your tools.")
    parts, budget = [head], budget or int(cfg().get("context_chars") or CONTEXT_CHARS)
    mats = materials_of(unit)
    cdir = root("materials") / unit["course"]  # type: ignore[operator]
    reading_files = [(r, cdir / r["file"]) for r in mats["readings"] if r["file"]]
    in_readings = {p for _, p in reading_files}
    if unit.get("body"):
        body = unit["body"][:budget]
        budget -= len(body)
        parts.append(L("=== 学习页 ===", "=== Study notes ===") + f"\n{body}")

    def add(label: str, path: Path | None, text_fn) -> None:
        nonlocal budget
        if budget <= 0:
            parts.append(f"=== {label}" + (L(f"（没放进来，原文件在 {path}）", f" (not included; file at {path})") if path else L("（没放进来）", " (not included)")) + " ===")
            return
        text = text_fn()[:budget]
        budget -= len(text)
        parts.append(f"=== {label}" + (L(f"（原文件 {path}）", f" (file at {path})") if path else "") + f" ===\n{text or L('（抽不出文字）', '(no extractable text)')}")

    for src in unit["sources"]:
        if src not in in_readings:
            add(L(f"课件：{src.name}", f"Material: {src.name}"), src, lambda s=src: text_of(s))
    for rec in mats["recordings"]:
        if rec["has_captions"]:
            add(L(f"录播字幕：{rec_title(rec)}", f"Captions: {rec_title(rec)}"), None, lambda r=rec: transcript_text(r))
    for r, path in reading_files:
        need = L("必读", "required") if r["required"] else L("选读", "optional")
        how = L("；", "; ") + str(r["instructions"]) if r.get("instructions") else ""
        add(L(f"阅读材料（{need}{how}）：{r['title']}", f"Reading ({need}{how}): {r['title']}"), path, lambda p=path: text_of(p))
    parts.append(L("=== 以上是材料，下面是我的问题 ===", "=== End of materials. My question follows ==="))
    return "\n\n".join(parts)


def needs_context(thread: str) -> bool:
    """这个线程今天（逻辑日）还没说过话 → 会话是新的，要带前情。"""
    lo, _ = chat.day_bounds(chat.day_of(chat.now_iso()))
    with chat._lock, chat.db() as conn:
        return conn.execute("SELECT 1 FROM messages WHERE thread=? AND ts>=? LIMIT 1", (thread, lo)).fetchone() is None


# —— 页面与只读接口 ——

@router.get("/study", include_in_schema=False)
@router.get("/study/", include_in_schema=False)
def study_page():
    return FileResponse(PAGE_HTML, media_type="text/html", headers={"Cache-Control": "no-store"})


@router.get("/api/study/tree")
def tree():
    if not root("materials"):
        return {"ok": True, "configured": False, "app_name": settings.app_name, "courses": [],
                "hint": L("在 server.json 里加 study.materials（课件目录：一门课一个文件夹，下面一层是模块）和 study.pages（学习页目录）。",
                          "Add study.materials (one folder per course, one subfolder per module) and study.pages (study notes) to server.json.")}
    return {"ok": True, "configured": True, "app_name": settings.app_name, "language": settings.language, "video": bool(cfg().get("video_cmd")),
            "courses": [course_tree(c) for c in courses()]}


@router.get("/api/study/page")
def page(course: str, path: str):
    pdir = pages_dir(course)
    course_dir(course)
    if not pdir:
        raise HTTPException(404, L("还没配置学习页目录", "No study pages directory configured"))
    meta, body = read_page(inside(pdir, path))
    return {"ok": True, "meta": json.loads(json.dumps(meta, default=str)), "markdown": body}


@router.get("/api/study/file")
def file(course: str, path: str, where: str = "materials"):
    base = pages_dir(course) if where == "pages" else course_dir(course)
    if not base:
        raise HTTPException(404, L("找不到这个文件", "File not found"))
    course_dir(course)
    p = inside(base, path)
    mime = mimetypes.guess_type(p.name)[0] or ("text/markdown" if p.suffix.lower() in (".md", ".rmd") else "application/octet-stream")
    if mime.startswith("text/") or p.suffix.lower() in TEXT_EXT:
        mime = f"{mime.split(';')[0]}; charset=utf-8"
    return FileResponse(p, media_type=mime, filename=p.name, content_disposition_type="inline")


@router.get("/api/study/deadlines")
def deadlines(refresh: int = 0):
    """ddl 来自 deadlines_cmd 打印的 JSON（比如课程平台的待交作业），缓存 30 分钟。"""
    global _deadlines
    cmd = cfg().get("deadlines_cmd")
    if not cmd:
        return {"ok": True, "configured": False, "items": []}
    if not refresh and _deadlines and time.time() - _deadlines[0] < 1800:
        return _deadlines[1]
    argv = [str(Path(x).expanduser()) if str(x).startswith("~") else str(x) for x in (cmd if isinstance(cmd, list) else str(cmd).split())]
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=120, check=False)  # noqa: S603 — 命令来自本机配置文件
        raw_out = out.stdout.strip()
        try:
            data = json.loads(raw_out or "null")
        except ValueError:  # 前面混了日志：取最后一行
            data = json.loads(raw_out.splitlines()[-1])
    except (subprocess.SubprocessError, ValueError, OSError, IndexError) as e:
        return {"ok": False, "configured": True, "items": [], "error": str(e)[:200]}
    if isinstance(data, dict) and data.get("error"):
        res = {"ok": False, "configured": True, "items": [], "error": str(data["error"])[:300]}
    else:
        items = [{"due": str(x.get("due") or ""), "course": x.get("course"), "title": re.sub(r"_[A-Z]_$", "", str(x.get("title") or "")), "url": x.get("url")}
                 for x in (data if isinstance(data, list) else []) if isinstance(x, dict) and not x.get("submitted")]
        res = {"ok": True, "configured": True, "items": sorted(items, key=lambda x: x["due"]), "checked": chat.now_iso()}
        sync_agent_deadlines(res["items"])
    _deadlines = (time.time(), res)
    return res



# —— 学习秘书 Agent（server.json 的 study.agent，2026-09-27）：Canvas ddl 同步进它的「ddl」表，路线打勾记进「学习记录」 ——

COURSE_SHORT = {"Corporate Strategy": "CS", "Business Economics": "BE", "Quantitative Data Analysis": "QDA"}


def ddl_kind(title: str) -> str:
    t = title.lower()
    if "group" in t:
        return "小组作业"
    if "presentation" in t or "video" in t:
        return "展示"
    if "exam" in t:
        return "考试"
    return "作业"


def sync_agent_deadlines(items: list[dict]) -> None:
    """Canvas 未交作业 → 学习秘书的 ddl 表：新的加一行（按 Canvas 链接认），表里有、截止还没到、Canvas 上不再是未交的 → 标交了。"""
    agent = chat.study_agent()
    if not agent:
        return
    try:
        import boards  # 延迟导入：boards 依赖 chat / data
        have = {(r["data"].get("link") or ""): r for r in boards.list_rows(agent, "deadlines", limit=500)["rows"]}
        live = set()
        new = []
        for x in items:
            if "participation" in str(x.get("title") or "").lower() or not x.get("due"):
                continue
            url = x.get("url") or ""
            live.add(url)
            if url and url in have:
                continue
            title = str(x.get("title") or "").strip()
            new.append({"title": title, "course": COURSE_SHORT.get(str(x.get("course") or "")), "kind": ddl_kind(title),
                        "due": str(x["due"])[:16].replace(" ", "T"), "done": False, "link": url or None})
        if new:
            boards.add_rows(agent, "deadlines", boards.RowsIn(rows=new, by="agent"))
        now = datetime.now(settings.tz).strftime("%Y-%m-%dT%H:%M")
        for url, r in have.items():
            d = r["data"]
            if url.startswith("https://canvas.") and url not in live and not d.get("done") and str(d.get("due") or "") > now:
                boards.patch_row(r["id"], boards.RowPatch(data={"done": True}, by="agent"))
    except Exception:  # noqa: BLE001 — 同步不上只是看板少几行，学习台照常
        pass


def log_study_step(unit: dict, step: dict) -> None:
    """学习路线打了一个勾 → 学习秘书的「学习记录」记一行（哪天、哪门课、哪一步、多久）。"""
    agent = chat.study_agent()
    if not agent:
        return
    try:
        import boards
        what = f"S{unit['session']} " if unit.get("session") else ""
        boards.add_rows(agent, "study_log", boards.RowsIn(rows=[{
            "date": chat.day_of(chat.now_iso()), "course": COURSE_SHORT.get(unit["course"]),
            "what": (what + str(step.get("title") or ""))[:120], "minutes": int(step.get("minutes") or 0) or None}], by="agent"))
    except Exception:  # noqa: BLE001
        pass


_refreshing = threading.Lock()


def _refresh_deadlines() -> None:
    if _refreshing.acquire(blocking=False):
        try:
            deadlines(refresh=0)
        finally:
            _refreshing.release()


def deadlines_between(lo: datetime, hi: datetime) -> list[tuple[datetime, str]]:
    """落在 [lo, hi) 的 ddl：(截止时间, 日程里显示的标题)。给「今天」页的日程用：缓存过期就在后台刷新，这次先用旧的。"""
    if not cfg().get("deadlines_cmd"):
        return []
    if _deadlines is None:
        _refresh_deadlines()   # 第一次同步读（命令一般自带缓存，很快）
    elif time.time() - _deadlines[0] > 1800:
        threading.Thread(target=_refresh_deadlines, daemon=True).start()
    out = []
    for x in (_deadlines[1] if _deadlines else {}).get("items") or []:
        try:
            due = datetime.strptime(x["due"][:16], "%Y-%m-%d %H:%M").replace(tzinfo=settings.tz)
        except (KeyError, ValueError):
            continue
        if lo <= due < hi:
            out.append((due, L("截止 · ", "Due · ") + " · ".join(str(v) for v in (x.get("course"), x.get("title")) if v)))
    return out

# —— 问答 ——

class AskBody(BaseModel):
    course: str
    page: str | None = None
    file: str | None = None
    text: str
    model: str | None = None
    step: int | None = None  # 学习路线里正在做的那一步（从 0 数）
    quote: str | None = None  # 就学习页里的哪一节 / 哪一段提问（只给模型看）


def step_note(unit: dict, step: int | None) -> str | None:
    """「我现在在学习路线第几步」：只给模型看，让它知道我卡在哪。"""
    route = load_json(gen_path(unit, "path")) if step is not None and unit["kind"] == "page" else None
    items = route.get("items") if isinstance(route, dict) else None
    if not items or not 0 <= step < len(items):  # type: ignore[operator]
        return None
    s = items[step]
    return L(f"【学习路线】我现在在第 {step + 1} 步（共 {len(items)} 步）：{s.get('title')}。这一步要做的：{s.get('do')}",
             f"[Study path] I'm on step {step + 1} of {len(items)}: {s.get('title')}. What this step asks: {s.get('do')}")


@router.post("/api/study/ask")
async def ask(body: AskBody):
    text = body.text.strip()
    if not text:
        raise HTTPException(400, L("空消息", "Empty message"))
    unit = resolve_unit(body.course, body.page, body.file)
    context = await asyncio.to_thread(context_for, unit) if needs_context(unit["thread"]) else None
    notes = [n for n in (step_note(unit, body.step),
                         L("【我问的是这一段】\n", "[I'm asking about this passage]\n") + body.quote.strip()[:4000] if (body.quote or "").strip() else None) if n]
    if notes:
        context = "\n\n".join(([context] if context else []) + notes)
    run = chat.start_run(unit["thread"], text, body.model, context=context)
    run.notify = False  # 人就在电脑前看着，不推到手机
    return StreamingResponse(chat.attach(run), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@router.get("/api/study/history")
def history(course: str, page: str | None = None, file: str | None = None):
    unit = resolve_unit(course, page, file)
    return {**chat.history(thread=unit["thread"], limit=200, day=None, all=1), "thread": unit["thread"]}


# —— 生成闪卡 / 小测 ——

def gen_prompt(kind: str) -> tuple[str, str]:
    """(对话记录里显示的一句, 给模型的要求)。"""
    if kind == "cards":
        return L("生成闪卡", "Make flashcards"), L(
            "请根据上面的材料做 12–20 张闪卡，帮我复习这一节。一张卡只考一个点（概念、公式的含义、框架怎么用、易混点）；"
            "正面是简短的问题，背面 1–3 句，需要公式就用 LaTeX（$…$）；中文为主，专有名词写成「中文 English」（比如「无谓损失 Deadweight Loss (DWL)」）；"
            "ref 写出处（文件名简称 + 页码，或录播时间点）。"
            '只输出一个 JSON 对象，不要任何别的文字，也不要调用工具：{"cards": [{"q": "…", "a": "…", "tag": "概念", "ref": "…"}]}',
            "From the materials above, make 12–20 flashcards to review this session. One idea per card (a concept, what a formula means, "
            "how to use a framework, a common confusion); a short question on the front, 1–3 sentences on the back, LaTeX ($…$) for formulas; "
            "ref = source (short file name + page). Output one JSON object and nothing else, and don't call tools: "
            '{"cards": [{"q": "…", "a": "…", "tag": "concept", "ref": "…"}]}')
    return L("生成小测", "Make a quiz"), L(
        "请根据上面的材料出 8 道单选题考我这一节。每题 4 个选项、只有一个对；错误选项要是常见的误解，不要一眼能排除；"
        "解析说清为什么对、别的为什么错；需要公式用 LaTeX（$…$）；中文为主，专有名词写成「中文 English」；ref 写出处。"
        '只输出一个 JSON 对象，不要任何别的文字，也不要调用工具：{"questions": [{"q": "…", "options": ["…", "…", "…", "…"], "answer": 0, "explain": "…", "ref": "…"}]}'
        "（answer 是正确选项的下标，从 0 开始）",
        "From the materials above, write 8 multiple-choice questions on this session. 4 options each, exactly one correct; wrong options "
        "should be common misconceptions, not obviously wrong; the explanation says why the answer is right and the others wrong; LaTeX ($…$) "
        "for formulas; ref = source. Output one JSON object and nothing else, and don't call tools: "
        '{"questions": [{"q": "…", "options": ["…", "…", "…", "…"], "answer": 0, "explain": "…", "ref": "…"}]} (answer = 0-based index)')


def parse_json(text: str) -> dict:
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    start, end = t.find("{"), t.rfind("}")
    if start < 0 or end <= start:
        raise ValueError(L("回复里没有 JSON", "No JSON in the reply"))
    return json.loads(t[start:end + 1])


def check(kind: str, data: dict) -> list[dict]:
    if kind == "cards":
        items = [c for c in data.get("cards") or [] if isinstance(c, dict) and c.get("q") and c.get("a")]
    else:
        items = [q for q in data.get("questions") or [] if isinstance(q, dict) and q.get("q") and isinstance(q.get("options"), list)
                 and len(q["options"]) >= 2 and isinstance(q.get("answer"), int) and 0 <= q["answer"] < len(q["options"])]
    if not items:
        raise ValueError(L("生成的内容格式不对", "The generated content has the wrong format"))
    return items


def minutes_of(value) -> str:
    """录播时长（秒数或现成的文字）→「58 分钟」。"""
    try:
        return L(f"{round(float(value) / 60)} 分钟", f"{round(float(value) / 60)} min")
    except (TypeError, ValueError):
        return str(value or "")


def route_catalog(unit: dict, mats: dict) -> tuple[str, dict]:
    """学习路线能引用的资源：给模型看的清单文字 + 校验 refs 用的集合。"""
    cdir = root("materials") / unit["course"]  # type: ignore[operator]
    secs = headings_of(unit.get("body") or "")
    files = [str(s.relative_to(cdir)) for s in unit["sources"] if s.is_relative_to(cdir)]
    files += [r["file"] for r in mats["readings"] if r["file"] and r["file"] not in files]
    session = unit.get("session")
    vids = [v for v in videos_of(unit["course"]) if session is not None and v["session"] == session]
    recs = [r for r in mats["recordings"] if r["id"] and r["has_captions"]]
    lines = [L("可以引用的资源（refs 只能用下面这些）：", "Resources you may reference (refs must use only these):"),
             L("学习页小节（type=section，写 id）：", "Study-note sections (type=section, give the id):")]
    lines += [f"- {i} {t}" for i, t in secs] or ["- " + L("（没有）", "(none)")]
    lines.append(L("课件和阅读材料（type=file，写 path；page 用材料里标的页码）：", "Materials and readings (type=file, give the path; page = the page marker in the text):"))
    for p in files:
        r = next((x for x in mats["readings"] if x["file"] == p), None)
        note = ""
        if r:
            need = L("必读", "required") if r["required"] else L("选读", "optional")
            note = L(f"（阅读：{r['title']}，{need}", f" (reading: {r['title']}, {need}") + (L("；", "; ") + str(r["instructions"]) if r.get("instructions") else "") + L("）", ")")
        lines.append(f"- {p}{note}")
    if vids:
        lines.append(L("视频（type=video，写 path）：", "Videos (type=video, give the path):"))
        lines += [f"- {v['path']}（{v['name']}）" for v in vids]
    if recs:
        lines.append(L("录播（type=recording，写 id；t 是秒数，按字幕里的 [时:分:秒] 换算）：", "Lecture recordings (type=recording, give the id; t = seconds, from the [h:mm:ss] marks in the captions):"))
        lines += [f"- {r['id']}（{rec_title(r)}，{minutes_of(r.get('duration'))}）" for r in recs]
    lines.append(L("闪卡（type=cards）、小测（type=quiz）：点开就能用，还没有的会先生成。", "Flashcards (type=cards) and quiz (type=quiz): open them directly; missing ones get generated first."))
    allowed = {"section": dict(secs), "file": set(files), "video": {v["path"] for v in vids}, "recording": {r["id"]: (rec_title(r), r.get("viewer_url")) for r in recs}}
    return "\n".join(lines), allowed


def path_prompt(unit: dict, mats: dict) -> tuple[str, str, dict, str]:
    """(对话记录里显示的一句, 给模型的要求, 校验用的集合, 课前 pre / 课后 post)。有录播 = 已经上过课。"""
    catalog, allowed = route_catalog(unit, mats)
    phase = "post" if mats["recordings"] else "pre"
    today = datetime.now(settings.tz).strftime("%Y-%m-%d")
    shape = ('{"summary": "…", "steps": [{"title": "…", "minutes": 15, "do": "…", "refs": [{"type": "section", "id": "s3"}, '
             '{"type": "file", "path": "…", "page": 12}, {"type": "video", "path": "media/…"}, {"type": "recording", "id": "…", "t": 754}, '
             '{"type": "cards"}, {"type": "quiz"}]}]}')
    ask = L(
        "请根据上面的材料，给我设计这一节的学习路线：一步一步告诉我先学什么、再学什么，照着做完就能学会这一节。\n"
        f"今天是 {today}。" + ("这一节已经上过课，有录播字幕：这是课后复习路线。" if phase == "post" else "这一节还没上课：这是课前预习路线。") + f"\n\n{catalog}\n\n"
        "要求：\n"
        "1. 5–8 步，按学习顺序排：先弄清这一节要回答的问题和整体框架 → 按知识块逐块学（每块看课件哪几页、学习页哪一节、教材或阅读材料哪一部分；"
        "有录播就给出老师讲这一块的时间点）→ 做题（课上的题、problem sheet、case 讨论题）→ 用闪卡、小测自测 → 最后回顾易错点。"
        "预习路线：按 Canvas 页面的要求读 case 和阅读材料、想好课堂讨论题；要交的 pre-case 题只提醒我自己先写，不要替我写答案。\n"
        "2. 每一步：title（十个字左右）、minutes（大概要多少分钟）、do（1–3 句，具体说做什么、带着什么问题去看；中文，专有名词第一次出现写成「中文 English」）、"
        "refs（这一步要打开的资源，1–3 个，只能用上面列出的）。\n"
        "3. 页码和时间点必须来自材料里的标记，拿不准就不写 page 或 t。\n"
        "4. summary 用一句话说这条路线是怎么安排的。\n"
        f"只输出一个 JSON 对象，不要任何别的文字，也不要调用工具：\n{shape}",
        "From the materials above, design a study path for this session: tell me step by step what to learn first and what next, so that "
        f"following it I master the session.\nToday is {today}. " + ("This session has been taught and has lecture captions: this is a review path."
                                                                     if phase == "post" else "This session hasn't been taught yet: this is a preparation path.") + f"\n\n{catalog}\n\n"
        "Requirements:\n1. 5–8 steps in learning order: first the question the session answers and its overall framework → the content block by "
        "block (which slide pages, which note section, which part of the textbook or readings; with a recording, the time the lecturer covers it) → "
        "practice (class questions, problem sheets, case questions) → self-test with flashcards and the quiz → finally review common mistakes. "
        "For preparation: read the case and readings the course page asks for and think through the discussion questions; for assessed pre-case "
        "questions only remind me to write my own answer, don't write it for me.\n2. Each step: title (a few words), minutes (estimate), do (1–3 "
        "sentences: exactly what to do and what question to read with), refs (1–3 resources to open, only from the list above).\n3. Page numbers "
        "and times must come from the markers in the materials; if unsure, leave out page or t.\n4. summary: one sentence on how the path is organised.\n"
        f"Output one JSON object and nothing else, and don't call tools:\n{shape}")
    return L("生成学习路线", "Make a study path"), ask, allowed, phase


def check_route(data: dict, allowed: dict) -> list[dict]:
    """只留格式对、引用的资源真实存在的步骤和 refs。"""
    steps = []
    for s in data.get("steps") or []:
        if not isinstance(s, dict) or not str(s.get("title") or "").strip():
            continue
        refs: list[dict] = []
        for r in s.get("refs") or []:
            t = r.get("type") if isinstance(r, dict) else None
            if t in ("cards", "quiz"):
                refs.append({"type": t})
            elif t == "section" and r.get("id") in allowed["section"]:
                refs.append({"type": t, "id": r["id"], "label": allowed["section"][r["id"]]})
            elif t in ("file", "video") and r.get("path") in allowed[t]:
                ref = {"type": t, "path": r["path"]}
                page = r.get("page")
                page = int(page) if isinstance(page, str) and page.isdigit() else page
                if t == "file" and isinstance(page, int) and page > 0:
                    ref["page"] = page
                refs.append(ref)
            elif t == "recording" and str(r.get("id")) in allowed["recording"]:
                label, url = allowed["recording"][str(r["id"])]
                ref = {"type": t, "id": str(r["id"]), "label": label, "url": url}
                if isinstance(r.get("t"), (int, float)) and r["t"] >= 0:
                    ref["t"] = int(r["t"])
                refs.append(ref)
        try:
            minutes = max(0, min(600, int(s.get("minutes") or 0)))
        except (TypeError, ValueError):
            minutes = 0
        steps.append({"title": str(s["title"]).strip()[:80], "minutes": minutes, "do": str(s.get("do") or "").strip(), "refs": refs[:4]})
    if not steps:
        raise ValueError(L("生成的内容格式不对", "The generated content has the wrong format"))
    return steps


async def ask_agent(thread: str, shown: str, key: str, context: str) -> chat.Run:
    """在生成线程里问一次、等它答完。key 是这次生成专用的 session key。"""
    run = chat.start_run(thread, shown, None, key=key, context=context)
    run.notify = False
    deadline = time.time() + 600
    while not run.done and time.time() < deadline:
        await asyncio.sleep(1)
    if not run.done:
        raise RuntimeError(L("超过 10 分钟还没生成完", "Still not done after 10 minutes"))
    if run.status != "ok":
        raise RuntimeError(run.error or "error")
    return run


async def gen_job(key: str, unit: dict, kind: str) -> None:
    try:
        extra: dict = {}
        if kind == "path":
            mats = await asyncio.to_thread(materials_of, unit)
            shown, ask_text, allowed, extra["phase"] = path_prompt(unit, mats)
            budget = int(cfg().get("gen_context_chars") or GEN_CONTEXT_CHARS)
        else:
            (shown, ask_text), allowed, budget = gen_prompt(kind), None, None
        context = await asyncio.to_thread(context_for, unit, budget)
        thread = unit["thread"] + "-gen"
        # 每次一个新会话：前情很长，不能在同一个会话里越攒越多
        run = await ask_agent(thread, shown, f"{chat.session_key(thread)}-{int(time.time())}", f"{context}\n\n{ask_text}")
        data = parse_json(run.text)
        if kind == "path":
            items = check_route(data, allowed)  # type: ignore[arg-type]
            extra |= {"summary": str(data.get("summary") or "").strip()[:400], "total_minutes": sum(s["minutes"] for s in items)}
        else:
            items = check(kind, data)
        path = gen_path(unit, kind)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"kind": kind, "title": unit["title"], "generated": chat.now_iso(), "model": run.model, **extra, "items": items},
                                   ensure_ascii=False, indent=1), encoding="utf8")
        JOBS[key] = {"status": "done"}
    except (HTTPException, RuntimeError, ValueError, OSError) as e:
        JOBS[key] = {"status": "error", "error": str(getattr(e, "detail", None) or e)[:300]}


# —— 生成视频（配了 video_cmd 才有）：助手写 Manim 脚本 → 本机渲染 → 出错把报错交回去改一次 ——

def video_prompt(concept: str) -> str:
    no_latex = "" if shutil.which("latex") else L(
        "这台机器没有装 LaTeX：不要用 MathTex、Tex、Integer、DecimalNumber、Variable，也不要让坐标轴自动加数字或标签"
        "（include_numbers、add_coordinates、get_axis_labels 都依赖 LaTeX）；公式和数字一律用 Text 写，需要变化的数字用 always_redraw 重建 Text。",
        "This machine has no LaTeX: don't use MathTex, Tex, Integer, DecimalNumber or Variable, and don't let axes add numbers or labels "
        "(include_numbers, add_coordinates, get_axis_labels all need LaTeX); write formulas and numbers with Text, and rebuild Text with always_redraw for changing numbers.")
    return L(
        f"请为这个概念写一个 Manim Community 版的动画脚本，3Blue1Brown 风格，45–90 秒：{concept}\n"
        "要求：场景类名必须是 Main（class Main(Scene)）；深色背景，字少，靠图形的变化讲直觉，结尾一句话总结；"
        "中文用 Text(…, font=\"Noto Sans CJK SC\")；专有名词第一次出现写成「中文 English (缩写)」，比如「边际收益 Marginal Revenue (MR)」，"
        "后面再出现可以只写缩写或中文加缩写，标题和结尾总结里的关键术语也带英文；字多了就缩小字号或分行，不要超出画面；记号和例子尽量和上面的课件一致；"
        f"只用 manim 自带的对象，不读外部文件、不联网。{no_latex}\n"
        "只输出一个 python 代码块，不要任何别的文字，也不要调用工具。",
        f"Write a Manim Community animation script for this concept, 3Blue1Brown style, 45–90 seconds: {concept}\n"
        "Requirements: the scene class must be Main (class Main(Scene)); dark background, few words, build intuition through changing shapes, "
        "end with a one-sentence summary; match the notation and examples of the materials above; use only built-in manim objects, "
        f"no external files or network. {no_latex}\nOutput one python code block and nothing else, and don't call tools.")


def extract_code(text: str) -> str:
    m = re.search(r"```(?:python|py)?[ \t]*\n(.*?)```", text, re.S)
    code = (m.group(1) if m else text).strip()
    if "class Main" not in code:
        raise ValueError(L("回复里没有 class Main 的脚本", "No script with class Main in the reply"))
    return code


async def render(script: Path, work: Path) -> tuple[bool, str, Path | None]:
    cmd = cfg().get("video_cmd") or []
    argv = [str(x).replace("{script}", str(script)).replace("{media_dir}", str(work)) for x in (cmd if isinstance(cmd, list) else str(cmd).split())]
    argv = [str(Path(a).expanduser()) if a.startswith("~") else a for a in argv]
    proc = await asyncio.create_subprocess_exec(*argv, cwd=str(work), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), 900)
    except asyncio.TimeoutError:
        proc.kill()
        return False, L("渲染超过 15 分钟", "Rendering took over 15 minutes"), None
    log = out.decode("utf8", "replace")
    mp4s = sorted((p for p in work.rglob("*.mp4") if "partial_movie_files" not in p.parts), key=lambda p: p.stat().st_mtime)
    return proc.returncode == 0 and bool(mp4s), log[-4000:], (mp4s[-1] if mp4s else None)


async def video_job(key: str, unit: dict, concept: str) -> None:
    try:
        context = await asyncio.to_thread(context_for, unit)
        thread = unit["thread"] + "-gen"
        skey = f"{chat.session_key(thread)}-v{int(time.time())}"
        run = await ask_agent(thread, L(f"生成视频：{concept}", f"Make a video: {concept}"), skey, f"{context}\n\n{video_prompt(concept)}")
        code = extract_code(run.text)
        work = gen_dir(unit["course"]) / "video" / time.strftime("%Y%m%d-%H%M%S")
        work.mkdir(parents=True, exist_ok=True)
        script = work / "scene.py"
        ok, log, mp4 = False, "", None
        for attempt in range(2):
            script.write_text(code, encoding="utf8")
            JOBS[key] = {**JOBS[key], "stage": "render"}
            ok, log, mp4 = await render(script, work)
            if ok or attempt:
                break
            JOBS[key] = {**JOBS[key], "stage": "fix"}
            # 同一个会话：助手记得自己写的脚本，只把报错交回去
            run = await ask_agent(thread, L("视频渲染出错，修脚本", "Render failed, fix the script"), skey,
                                  L("渲染报错如下。请修好后只输出完整的新脚本（一个 python 代码块）：\n", "The render failed with the log below. Fix it and output only the full new script (one python code block):\n") + log[-2500:])
            code = extract_code(run.text)
        if not ok or not mp4:
            raise RuntimeError(L("渲染失败：", "Render failed: ") + log[-300:])
        media = (pages_dir(unit["course"]) or gen_dir(unit["course"]).parent) / "media"
        media.mkdir(parents=True, exist_ok=True)
        # 文件名用短标题：候选概念通常是「标题：说明」，取冒号前面；同名就加序号
        short = re.split(r"[：:]", concept, maxsplit=1)[0]
        title = re.sub(r'[\\/*?"<>|\n\r\t]+', " ", short).strip()[:40] or "video"
        prefix = f"S{unit['session']:02d} " if unit.get("session") is not None else ""
        name, n = f"{prefix}{title}.mp4", 2
        while (media / name).exists():
            name, n = f"{prefix}{title} {n}.mp4", n + 1
        shutil.move(str(mp4), media / name)
        JOBS[key] = {"status": "done", "concept": concept, "path": f"media/{name}"}
    except (HTTPException, RuntimeError, ValueError, OSError) as e:
        JOBS[key] = {"status": "error", "concept": concept, "error": str(getattr(e, "detail", None) or e)[:300]}


class GenBody(BaseModel):
    course: str
    page: str | None = None
    file: str | None = None
    kind: str
    concept: str | None = None  # 视频要讲的概念
    force: bool = False         # 必读材料不齐也照样生成


def slim_reading(r: dict) -> dict:
    return {k: r.get(k) for k in ("title", "kind", "instructions", "status", "url", "error")}


@router.post("/api/study/generate")
async def generate(body: GenBody):
    unit = resolve_unit(body.course, body.page, body.file)
    if body.kind == "video":
        concept = (body.concept or "").strip()[:200]
        if not cfg().get("video_cmd"):
            raise HTTPException(400, L("还没配置 study.video_cmd，不能生成视频", "study.video_cmd isn't configured, so videos can't be generated"))
        if not concept:
            raise HTTPException(400, L("要说明视频讲什么", "Say what the video should explain"))
        key = f"{unit['thread']}:video"
        if (JOBS.get(key) or {}).get("status") != "running":
            JOBS[key] = {"status": "running", "stage": "script", "concept": concept, "started": chat.now_iso()}
            asyncio.create_task(video_job(key, unit, concept))
        return {"ok": True, **JOBS[key]}
    if body.kind not in KINDS:
        raise HTTPException(400, L("kind 只能是 cards、quiz、path 或 video", "kind must be cards, quiz, path or video"))
    key = f"{unit['thread']}:{body.kind}"
    if (JOBS.get(key) or {}).get("status") != "running":
        # 先核对材料：必读还没到手就先提醒，补齐了再生成（或者明确说不等了）
        missing = materials_of(unit)["missing"]
        if missing and not body.force:
            return {"ok": False, "status": "needs_materials", "missing": [slim_reading(r) for r in missing]}
        JOBS[key] = {"status": "running", "started": chat.now_iso()}
        asyncio.create_task(gen_job(key, unit, body.kind))
    return {"ok": True, **JOBS[key]}


@router.get("/api/study/generated")
def generated(course: str, kind: str, page: str | None = None, file: str | None = None):
    unit = resolve_unit(course, page, file)
    if kind == "video":
        return {"ok": True, "status": "none", **(JOBS.get(f"{unit['thread']}:video") or {})}
    if kind not in KINDS:
        raise HTTPException(400, L("kind 只能是 cards、quiz、path 或 video", "kind must be cards, quiz, path or video"))
    path = gen_path(unit, kind)
    data = json.loads(path.read_text(encoding="utf8")) if path.is_file() else None
    job = JOBS.get(f"{unit['thread']}:{kind}") or {}
    res = {"ok": True, "status": job.get("status") or ("done" if data else "none"), "error": job.get("error"), "data": data}
    if kind == "path" and unit["kind"] == "page":
        res["done"] = done_steps(course, unit["path"], data)
    return res


# —— 材料清单、录播字幕、学习路线进度 ——

@router.get("/api/study/materials")
def materials(course: str, page: str):
    unit = resolve_unit(course, page, None)
    m = materials_of(unit)
    return {"ok": True, "readings": m["readings"], "recordings": [slim_rec(r) for r in m["recordings"]],
            "missing": [slim_reading(r) for r in m["missing"]], "complete": m["complete"]}


@router.get("/api/study/recordings")
def recordings(course: str, page: str):
    unit = resolve_unit(course, page, None)
    return {"ok": True, "items": [slim_rec(r) | {"chunks": transcript_chunks(segments_of(r), slides_of(r))} for r in recordings_of(course, unit.get("session"))]}


class ProgressBody(BaseModel):
    course: str
    page: str
    step: int
    done: bool


@router.post("/api/study/progress")
def set_progress(body: ProgressBody):
    unit = resolve_unit(body.course, body.page, None)
    path = gen_path(unit, "path")
    route = json.loads(path.read_text(encoding="utf8")) if path.is_file() else None
    n = len((route or {}).get("items") or [])
    if not 0 <= body.step < n:
        raise HTTPException(400, L("没有这一步", "No such step"))
    with _progress_lock:
        data = read_progress(body.course)
        done = set(done_steps(body.course, body.page, route, data))
        newly = body.done and body.step not in done
        (done.add if body.done else done.discard)(body.step)
        data[body.page] = {"gen": route["generated"], "done": sorted(done), "updated": chat.now_iso()}  # type: ignore[index]
        target = progress_path(body.course)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf8")
        tmp.replace(target)
    if newly:
        log_study_step(unit, route["items"][body.step])  # type: ignore[index]
    return {"ok": True, "done": sorted(done), "total": n}
