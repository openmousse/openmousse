"""学习台接到 app 上的几件事（2026-09-30）：

- /api/study/home     学习 Agent 看板顶上那张「学习台」卡：复习几条、接着学哪一节第几步、下一个截止、每门课学完几节。
- /api/study/outline  一门课每一节的状态（手机原生学习屏按课列节用；有档案、没档案的课一个样子）。
- /api/study/unit     一节课在手机上要的全部：课件、阅读、录播、视频、学习路线和打勾、复习。
- /api/study/agent    设学习 Agent：server.json 的 study.agent + 它的看板换成学习看板（dashboard = study）。
- /api/study/login-link  电脑上打开学习台的一次性链接：<服务器>/study#pair=<配对码>（10 分钟、一次，换一个这台浏览器自己的令牌）。
- /api/study/self     自测写的答案、看没看、自己判的对错存服务器（手机和电脑共用）：pages/<课>/.gen/self.json。
- /api/study/review/add  加复习（自测「没答上」、闪卡「没记住」、小测答错的）。
- /api/study/file/preview、/file/page  手机上看课件：和对话附件同一套预览，页图缓存在课程的 .gen/preview 里。
"""
from __future__ import annotations

import hashlib
import json
import mimetypes
import re
import threading
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

import chat
import coursefile as cf
import study
from chat import _lock
from config import raw, save as save_config, settings
from i18n import L

router = APIRouter()
_self_lock = threading.Lock()


# —— 一门课每一节的状态 ——

def page_status(course: str, entry: dict) -> str:
    """一篇学习页学到哪：done 路线走完 / doing 走了一半 / todo 有学习页没开始。"""
    pr = entry.get("progress")
    if pr and pr.get("total") and pr["done"] >= pr["total"]:
        return "done"
    if pr and pr.get("done"):
        return "doing"
    return "todo"


def outline_of(course: str) -> dict:
    """→ {name, title, code, profile, sessions: [{id, n, title, date, page, status, progress, missing}], taught, done}。
    status：done 学完 / doing 在学 / todo 没开始 / ready 材料齐了还没生成 / missing 材料没齐 / later 还没上 / empty 什么都没有 / info 课前准备页。"""
    tree = study.course_tree(course)
    today = cf.today().isoformat()
    out = []
    pages = {p["path"]: p for m in tree["modules"] for p in m["pages"]}
    pages |= {p["path"]: p for p in tree["pages"]}
    if tree.get("profile"):
        for m in tree["modules"]:
            s = m.get("session")
            if not s:
                continue
            p = m["pages"][0] if m["pages"] else None
            st = page_status(course, p) if p else {"ready": "ready", "missing": "missing", "noslides": "missing", "later": "later", "empty": "empty"}[s["status"]]
            out.append({"id": s["id"], "n": s["n"], "title": m["title"].split(": ", 1)[-1], "date": s["date"], "page": p["path"] if p else None,
                        "status": st, "progress": p.get("progress") if p else None, "missing": s["missing"]})
        for p in tree["pages"]:
            out.append({"id": None, "n": p["session"], "title": p["title"], "date": None, "page": p["path"], "status": "info" if p["session"] is None else page_status(course, p),
                        "progress": p.get("progress"), "missing": p["readings"]["missing"]})
    else:
        for p in pages.values():
            out.append({"id": None, "n": p["session"], "title": p["title"], "date": None, "page": p["path"],
                        "status": page_status(course, p) if p["session"] is not None else "info", "progress": p.get("progress"),
                        "missing": p["readings"]["missing"]})
        # 还没有学习页、但名字像一节课的模块（课件到了、还没写）
        have = {p["session"] for p in pages.values() if p["session"] is not None}
        for m in tree["modules"]:
            mm = re.match(r"(?:session|lecture)\s*0*(\d{1,2})\b[\s:：_-]*(.*)", m["title"], re.I)
            if mm and int(mm.group(1)) not in have and not m["pages"]:
                out.append({"id": None, "n": int(mm.group(1)), "title": mm.group(2).strip() or m["title"], "date": None, "page": None,
                            "status": "ready" if m["files"] else "later", "progress": None, "missing": 0})
        out.sort(key=lambda x: (x["n"] is None, x["n"] or 0, x["title"]))
    done = sum(1 for x in out if x["status"] == "done")
    taught = sum(1 for x in out if (x["date"] and x["date"] <= today) or (not x["date"] and x["status"] in ("done", "doing", "todo", "ready")))
    return {"name": course, "title": tree.get("title") or course, "code": tree.get("code") or cf.short_code(tree.get("title") or course),
            "profile": bool(tree.get("profile")), "sessions": out, "done": done, "taught": taught, "total": len([x for x in out if x["n"] is not None])}


@router.get("/api/study/outline")
def outline(course: str):
    study.course_dir(course)
    return {"ok": True, **outline_of(course)}


# —— 学习台卡片 ——

def next_step(courses: list[dict]) -> dict | None:
    """接着学：最近打过勾、还没走完的那一节；都没有就挑最早一节有学习页没走完的。"""
    best, best_t = None, ""
    for c in courses:
        prog = study.read_progress(c["name"])
        for s in c["sessions"]:
            if not s["page"] or s["status"] not in ("doing", "todo"):
                continue
            t = str((prog.get(s["page"]) or {}).get("updated") or "")
            key = t or "0"
            if best is None or key > best_t:
                best, best_t = (c, s), key
    if not best:
        return None
    c, s = best
    unit = {"course": c["name"], "thread": study.thread_of(c["name"], "page", s["page"])}
    route = study.load_json(study.gen_path(unit, "path"))
    items = (route or {}).get("items") or [] if isinstance(route, dict) else []
    done = set(study.done_steps(c["name"], s["page"], route if isinstance(route, dict) else None))
    i = next((k for k in range(len(items)) if k not in done), None)
    step = items[i] if i is not None else None
    return {"course": c["name"], "courseTitle": c["title"], "code": c["code"], "page": s["page"], "n": s["n"], "title": s["title"],
            "step": (i + 1) if i is not None else None, "steps": len(items), "stepTitle": (step or {}).get("title"), "minutes": (step or {}).get("minutes")}


def review_summary(courses: list[dict]) -> dict | None:
    """没复习的：一共几条，最早的那条在哪门课哪一节、从哪来。"""
    total, first = 0, None
    for c in courses:
        items = [x for x in study.read_review(c["name"]) if not x.get("done_at")]
        total += len(items)
        for x in sorted(items, key=lambda x: x.get("created_at") or ""):
            if first is None or (x.get("created_at") or "") < (first[1].get("created_at") or ""):
                first = (c, x)
    if not total or not first:
        return None
    c, x = first
    s = next((y for y in c["sessions"] if y["page"] and y["page"] == x.get("page")), None)
    src = (x.get("from") or {}).get("title") or x.get("source") or ""
    return {"count": total, "course": c["name"], "code": c["code"], "page": x.get("page"), "n": s["n"] if s else None, "source": src,
            "kind": x.get("kind") or ""}


@router.get("/api/study/home")
def home():
    """看板顶上的学习台卡：复习、接着学、下一个截止、每门课的进度。"""
    names = study.courses()
    courses = []
    for n in names:
        try:
            courses.append(outline_of(n))
        except HTTPException:
            continue
    items, _err = study.deadline_rows()
    now = datetime.now(settings.tz).strftime("%Y-%m-%d %H:%M")
    upcoming = sorted([x for x in items if str(x.get("due") or "") >= now], key=lambda x: x["due"])[:5]
    return {"ok": True, "agent": chat.study_agent(), "configured": bool(study.root("materials")),
            "courses": [{k: c[k] for k in ("name", "title", "code", "profile", "done", "taught", "total")} for c in courses],
            "review": review_summary(courses), "next": next_step(courses),
            "deadlines": [{"due": x["due"], "course": x.get("course"), "code": x.get("code"), "title": x.get("title"), "url": x.get("url"),
                           "courseId": x.get("course_id"), "sessionId": x.get("session_id"), "kind": x.get("kind")} for x in upcoming]}


# —— 一节课（手机）——

@router.get("/api/study/unit")
def unit(course: str, page: str):
    """手机上的一节：学习页的元数据、课件文件、阅读、录播、视频、学习路线（带打勾）、复习、生成过什么。"""
    u = study.resolve_unit(course, page, None)
    cdir = study.course_dir(course)
    meta, _ = study.read_page(study.inside(study.pages_dir(course), page))
    mats = study.materials_of(u)
    route = study.load_json(study.gen_path(u, "path"))
    route = route if isinstance(route, dict) else None
    files = [study.file_entry(cdir, p) for p in u["sources"]]
    readings = [r for r in mats["readings"]]
    for r in readings:
        if r.get("file") and (cdir / r["file"]).is_file():
            r["size"] = (cdir / r["file"]).stat().st_size
    vids = [v for v in study.videos_of(course) if u["session"] is not None and v["session"] == u["session"]]
    review = [x for x in study.read_review(course) if x.get("page") in (page, None) and not x.get("done_at")]
    return {"ok": True, "course": course, "page": page, "title": u["title"], "session": u["session"], "meta": json.loads(json.dumps(meta, default=str)),
            "files": files, "readings": readings, "recordings": [study.slim_rec(r) for r in mats["recordings"]], "videos": vids,
            "missing": [study.slim_reading(r) for r in mats["missing"]], "route": route,
            "done": study.done_steps(course, page, route) if route else [],
            "generated": {k: study.gen_path(u, k).is_file() for k in study.KINDS}, "review": review, "thread": u["thread"]}


# —— 学习 Agent ——

class AgentIn(BaseModel):
    agent: str


def make_study_agent(agent: str) -> None:
    """这个 Agent 当学习 Agent：server.json 的 study.agent 指它，它的看板换成学习看板（别的 Agent 原来是学习看板的换回普通的）。"""
    import data
    with _lock, data.ddb() as conn:
        if not conn.execute("SELECT 1 FROM groups WHERE id=?", (agent,)).fetchone():
            raise HTTPException(404, L("没有这个 Agent", "No such agent"))
        conn.execute("UPDATE groups SET dashboard='none' WHERE dashboard='study' AND id!=?", (agent,))
        conn.execute("UPDATE groups SET dashboard='study' WHERE id=?", (agent,))
    d = dict(raw(fresh=True))
    st = dict(d.get("study") or {})
    if st.get("agent") != agent:
        st["agent"] = agent
        d["study"] = st
        save_config(d)


def adopt_template(agent: str) -> None:
    """新建 Agent 选了「学习」：看板换成学习看板、装上学习功能包（截止表、学习记录）；还没有学习 Agent 就让它当。"""
    import data
    with _lock, data.ddb() as conn:
        conn.execute("UPDATE groups SET dashboard='study' WHERE id=?", (agent,))
    if not chat.study_agent():
        make_study_agent(agent)
    try:
        install_pack(agent)
    except Exception as e:  # noqa: BLE001 — 包没装上，Agent 照样能用，看板上少几块
        print(f"[study] 学习功能包没装上：{type(e).__name__}: {e}")
    try:
        import coursefile  # noqa: F401 — 课件、学习页目录建好（没配就是数据目录下的默认位置）
        for k in ("materials", "pages"):
            d = study.root(k)
            if d:
                d.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass


def install_pack(agent: str) -> None:
    """装 packs/study（和 packs.install 的 apply 一样，只是同步做；这个包没有提醒）。"""
    import boards
    import packs
    p = packs.load("study")
    g = boards.group_row(agent)
    with _lock, packs.pdb() as conn:
        pl = packs.plan(conn, agent, g["dashboard"], p)
        packs.create_tables(conn, agent, pl["newTables"], "active")
        added = packs.apply_merges(conn, agent, pl["merges"])
        blocks = boards.clean_blocks(conn, agent, {"blocks": pl["blocks"]}, g["dashboard"])
        meta = {"pack": "study", "version": p["version"], "tables": [t["name"] for t in pl["newTables"]], "added": added}
        packs.finish(conn, agent, meta, "agent")
        if pl["add"] or pl["adopt"]:
            boards.put_live(conn, agent, blocks, L("装上了学习功能包", "Installed the study pack"), "agent")


@router.post("/api/study/agent")
def set_agent(body: AgentIn):
    make_study_agent(body.agent.strip())
    chat.log_activity(L("换了学习 Agent", "Changed the study Agent"), "edit")
    return {"ok": True, "agent": chat.study_agent()}


# —— 电脑上打开：一次性登录链接 ——

class LinkIn(BaseModel):
    server: str | None = None     # app 自己连的地址（手机上连的就是它）


@router.post("/api/study/login-link")
def login_link(body: LinkIn):
    """「电脑上打开」：出一个 10 分钟、只能用一次的配对码，拼成 <服务器>/study#pair=<码>。浏览器打开它，学习台自己换一个这台浏览器的令牌。"""
    import pairing
    server = str(body.server or "").strip().rstrip("/")
    if not re.match(r"^https?://[^\s/]+$", server) or len(server) > 200:
        server = pairing.server_url()
    code, expires = pairing.new_code(L("浏览器（学习台）", "Browser (study desk)"), minutes=10)
    return {"ok": True, "url": f"{server}/study#pair={code}", "expires": expires, "code": code}


# —— 自测：答案存服务器 ——

def self_path(course: str) -> Path:
    return study.gen_dir(course) / "self.json"


def read_self(course: str) -> dict:
    try:
        d = json.loads(self_path(course).read_text(encoding="utf8"))
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


@router.get("/api/study/self")
def get_self(course: str, page: str):
    study.course_dir(course)
    return {"ok": True, "state": read_self(course).get(page)}


class SelfIn(BaseModel):
    course: str
    page: str
    state: dict      # {"n": 题数, "v": {"0": {"a": 写的答案, "open": 看过, "g": ok / close / miss}}}


def clean_self(st: dict) -> dict:
    out: dict = {"n": int(st.get("n") or 0) if str(st.get("n") or "0").isdigit() else 0, "v": {}, "updated": chat.now_iso()}
    for k, v in (st.get("v") or {}).items() if isinstance(st.get("v"), dict) else []:
        if not str(k).isdigit() or not isinstance(v, dict):
            continue
        g = v.get("g") if v.get("g") in ("ok", "close", "miss") else None
        out["v"][str(k)] = {"a": str(v.get("a") or "")[:4000], "open": bool(v.get("open")), **({"g": g} if g else {}), **({"rv": 1} if v.get("rv") else {})}
    return out


@router.put("/api/study/self")
def put_self(body: SelfIn):
    study.inside(study.pages_dir(body.course), body.page)
    with _self_lock:
        d = read_self(body.course)
        d[body.page] = clean_self(body.state)
        p = self_path(body.course)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f".{p.name}.tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf8")
        tmp.replace(p)
    return {"ok": True, "state": d[body.page]}


# —— 加复习 ——

class ReviewItem(BaseModel):
    text: str
    kind: str = "missed"      # missed 没答上 / card 没记住 / quiz 答错 / wrong 讲错
    source: str = ""


class ReviewAdd(BaseModel):
    course: str
    page: str | None = None
    items: list[ReviewItem]


@router.post("/api/study/review/add")
def review_add(body: ReviewAdd):
    """加几条复习（挂在这一节上）：学习台和手机上「没答上」「没记住」「答错了」的一键加进来。同一节里一样的字不重复加。"""
    if body.page:
        study.inside(study.pages_dir(body.course), body.page)
    n = study.add_review(body.course, body.page, [i.model_dump() for i in body.items])
    return {"ok": True, "added": n}


# —— 手机上看课件：和对话附件同一套预览 ——

def study_file(course: str, path: str, where: str) -> Path:
    base = study.pages_dir(course) if where == "pages" else study.course_dir(course)
    if not base:
        raise HTTPException(404, L("找不到这个文件", "File not found"))
    study.course_dir(course)
    return study.inside(base, path)


@router.get("/api/study/file/preview")
def file_preview(course: str, path: str, where: str = "materials"):
    import files as files_mod
    import preview
    p = study_file(course, path, where)
    mime = mimetypes.guess_type(p.name)[0] or ""
    kind = files_mod.kind_of(p.name, mime)
    info = {"id": "study:" + hashlib.sha1(f"{course}\n{where}\n{path}".encode()).hexdigest()[:12], "name": p.name, "mime": mime, "size": p.stat().st_size,
            "kind": kind, "chars": None, "note": None, "status": "ok"}
    out: dict = {"ok": True, "file": info}
    try:
        text = study.text_of(p) if kind == "doc" and p.suffix.lower() not in (".pdf",) else None
        out.update(preview.build(p, p.name, mime, kind, text))
    except Exception as exc:  # noqa: BLE001 — 预览坏了不影响原件
        out.update({"view": "none", "note": L(f"预览出错：{str(exc)[:120]}", f"Preview failed: {str(exc)[:120]}")})
    return out


@router.get("/api/study/file/page")
def file_page(course: str, path: str, n: int, w: int = 1200, where: str = "materials"):
    import preview
    p = study_file(course, path, where)
    w = preview.page_width(w)
    key = hashlib.sha1(f"{where}\n{path}\n{p.stat().st_mtime}".encode()).hexdigest()[:16]
    out = study.gen_dir(course) / "preview" / f"{key}.p{n}-{w}.jpg"
    return FileResponse(str(preview.render_page(p, n, w, out)), media_type="image/jpeg", headers=preview.CACHE)
