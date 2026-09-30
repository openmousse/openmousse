"""从零加一门课、改课（2026-09-30）：/api/study/courses/*。数据在 coursefile.py（course.json），要模型做的在 coursegen.py。

- 五步：建课（说说这门课）→ 你手上有什么（大纲 / 课件 / 课程网站 / 还没有）→ 核对每一节（读大纲抽出来的节、日期、阅读、截止，
  拿不准的问你）→ 放材料、查齐（按文件名归节，zip 拆开，缺什么先说）→ 生成（只生成齐了的节）。做到哪存到哪（setup.step）。
- 改课：每次改动记一行 course_changes（改之前 / 之后的整份档案 + 挪过的文件），能撤销（只撤后来没再改过的地方）。学习 Agent 在回复里改的，
  回复下面出一张「改了课程结构」的卡（kind course，和日程卡一样挂法：回复进行中当场发，回复结束挂到那条回复下）。
- 撤不回的（删课）：出收件箱卡（kind write），点了同意服务端自己做：课件、学习页、生成的东西整个挪进 <data_dir>/study-trash/。
- 核对完（confirm）以后，作业和考试进「今天」、要记得的、学习台顶栏的截止和学习 Agent 的截止表（study.py 合并）。
"""
from __future__ import annotations

import asyncio
import copy
import json
import re
import shutil
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

import chat
import coursefile as cf
import coursegen
import inbox
import study
from chat import _lock, db
from config import raw, save as save_config, settings
from i18n import L, LS

router = APIRouter()
MAX_UPLOAD = 200 * 1024 * 1024   # 一个文件（zip 可以大一点）
MAX_FILES = 60


def cdb() -> sqlite3.Connection:
    conn = db()
    conn.execute("""CREATE TABLE IF NOT EXISTS course_changes (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, actor TEXT NOT NULL,
        thread TEXT, message_id INTEGER, course TEXT NOT NULL, action TEXT NOT NULL, title TEXT NOT NULL, summary TEXT NOT NULL,
        lines TEXT NOT NULL DEFAULT '[]', before TEXT, after TEXT, ops TEXT NOT NULL DEFAULT '[]', undone_at TEXT)""")
    conn.execute("CREATE INDEX IF NOT EXISTS course_changes_thread ON course_changes(thread, id)")
    conn.execute("CREATE TABLE IF NOT EXISTS course_deletes (inbox_id TEXT PRIMARY KEY, course TEXT NOT NULL, created_at TEXT NOT NULL)")
    return conn


def bad(zh: str, en: str, code: int = 400) -> HTTPException:
    return HTTPException(code, L(zh, en))


def need(course: str) -> dict:
    c = cf.load(course)
    if not c:
        raise bad("这门课还没有课程档案", "This course has no course profile", 404)
    return c


def who(source: str | None) -> str:
    """谁改的：user（你在学习台 / app 里点的）或 Agent 的 id（study_ctl 经 --source / MCP）。"""
    s = (source or "").strip()
    return s if s and s != "user" and re.fullmatch(r"[A-Za-z0-9_-]{1,40}", s) else "user"


# —— 改动记录、撤销、对话里的卡 ——

def run_of(actor: str) -> chat.Run | None:
    """Agent 改的：它这会儿正在回复的那一次（卡片挂在那条回复下面）。你自己点的没有。"""
    if actor == "user":
        return None
    live = [r for t, r in chat.RUNS.items() if not r.done and not t.startswith(("task:", "study-")) and chat.agent_of(t) == actor]
    return max(live, key=lambda r: r.t0) if live else None


def change_json(r: sqlite3.Row) -> dict:
    c = cf.load(r["course"]) or {}
    return {"kind": "course", "id": f"cc-{r['id']}", "changeId": r["id"], "thread": r["thread"], "messageId": r["message_id"], "createdAt": r["at"],
            "status": "undone" if r["undone_at"] else "done", "action": r["action"], "actor": r["actor"], "course": r["course"],
            "courseTitle": c.get("title") or r["course"], "title": r["title"], "summary": r["summary"], "lines": json.loads(r["lines"] or "[]"),
            "session": session_ref(r)}


def session_ref(r: sqlite3.Row) -> dict | None:
    """卡上「打开这一节」：这次改动动到的第一节（有学习页的给页面）。"""
    try:
        marks = cf.diff_marks(json.loads(r["before"] or "{}"), json.loads(r["after"] or "{}"))
    except ValueError:
        return None
    sid = next(iter(marks["sessions"]), None)
    c = cf.load(r["course"]) if sid else None
    s = cf.session_by(c, sid) if c and sid else None
    return {"course": r["course"], "session": s["id"], "n": s["n"], "page": s.get("page")} if s else None


def record(course: str, actor: str, action: str, title: str, lines: list[str], before: dict, after: dict, ops: list | None = None) -> dict:
    """记一次改动；Agent 正在回复就当场给那次回复发卡。→ 卡片 JSON。"""
    run = run_of(actor)
    summary = "；".join(lines[:3]) if lines else title
    with _lock, cdb() as conn:
        cid = conn.execute("INSERT INTO course_changes(at, actor, thread, course, action, title, summary, lines, before, after, ops) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                           (cf.now_iso(), actor, run.thread if run else None, course, action, title[:120], summary[:300],
                            json.dumps(lines[:12], ensure_ascii=False), json.dumps(before, ensure_ascii=False), json.dumps(after, ensure_ascii=False),
                            json.dumps(ops or [], ensure_ascii=False))).lastrowid
        row = conn.execute("SELECT * FROM course_changes WHERE id=?", (cid,)).fetchone()
    card = change_json(row)
    if run is not None and not run.done:
        import cards  # 延迟导入：cards 在 /api/chat/cards 里也要用本模块
        cards.publish(run, card)
    if actor != "user":
        chat.log_activity(L(f"改了课程「{after.get('title') or course}」：{summary}", f'Changed the course "{after.get("title") or course}": {summary}'),
                          "study", actor)
    return card


def changes_for(thread: str, lo: str, hi: str) -> list[dict]:
    """这个线程这一天里 Agent 在回复中改的课（给 /api/chat/cards）。"""
    with _lock, cdb() as conn:
        rows = conn.execute("SELECT * FROM course_changes WHERE thread=? AND at>=? AND at<? ORDER BY id", (thread, lo, hi)).fetchall()
    return [change_json(r) for r in rows]


def link_run(run: chat.Run) -> None:
    """一次回复结束：这次回复里改的课挂到这条回复下面（cards.on_run_end 调）。"""
    if run.reply_id is None:
        return
    with _lock, cdb() as conn:
        conn.execute("UPDATE course_changes SET message_id=? WHERE thread=? AND message_id IS NULL AND at>=?", (run.reply_id, run.thread, run.started))


def reconcile(course: str, before: dict, after: dict) -> None:
    """节号变了：学习页的 session 和视频文件名开头的 SNN 跟着改（学习页文件名不改：问答记录和生成的东西按它认）。"""
    moved = cf.renumber_map(before, after)
    if not moved:
        return
    pdir = cf.profile_dir(course)
    by_id = {s["id"]: s for s in after["sessions"]}
    for sid, (_old, new) in moved.items():
        page = by_id[sid].get("page")
        p = pdir / page if page else None
        if p and p.is_file():
            text = p.read_text(encoding="utf8")
            fixed = re.sub(r"^(---\s*\n(?:.*\n)*?)session:\s*\d+\s*$", lambda m: f"{m.group(1)}session: {new}", text, count=1, flags=re.M)
            if fixed != text:
                tmp = p.with_name(f".{p.name}.tmp")
                tmp.write_text(fixed, encoding="utf8")
                tmp.replace(p)
    media = pdir / "media"
    if media.is_dir():
        staged = []
        for sid, (old, new) in moved.items():
            for f in media.iterdir():
                m = re.match(r"^S0*(\d+)(\b.*)$", f.name, re.I)
                if f.is_file() and m and int(m.group(1)) == old:
                    tmp = f.with_name(f".mv-{uuid.uuid4().hex[:6]}-{f.name}")
                    f.rename(tmp)
                    staged.append((tmp, media / f"S{new:02d}{m.group(2)}"))
        for tmp, dst in staged:
            tmp.rename(cf.unique(dst))


def drop_empty_folders(course: str, before: dict, after: dict) -> None:
    """撤销「加一节」：那一节不在了，它的文件夹是空的就收掉（有文件的留着，挪回来还在）。"""
    base = cf.course_root(course)
    keep = {s.get("folder") for s in after["sessions"] if not s["removed"]}
    for s in before["sessions"]:
        f = s.get("folder")
        if base and f and f not in keep and (base / f).is_dir() and not any((base / f).iterdir()):
            (base / f).rmdir()


def run_ops(course: str, ops: list, reverse: bool = False) -> None:
    """挪文件（放进某一节 / 挪回待归节）：撤销时反着挪。挪不了的（文件已经不在了）跳过。"""
    base = cf.course_root(course)
    if not base:
        return
    for op in reversed(ops) if reverse else ops:
        if op.get("op") != "move":
            continue
        src, dst = (op["to"], op["from"]) if reverse else (op["from"], op["to"])
        a, b = base / src, base / dst
        if a.is_file() and not b.exists():
            b.parent.mkdir(parents=True, exist_ok=True)
            a.rename(b)


def commit(course: str, before: dict, after: dict, actor: str, action: str, title: str, lines: list[str], ops: list | None = None,
           quiet: bool = False) -> dict:
    """存档案、跟着改学习页和视频的节号、记改动（quiet：向导里一格一格改的不记）。→ {"course": 新档案, "card": 卡片或 None}"""
    with cf._lock:
        saved = cf.save(course, after)
        reconcile(course, before, saved)
    card = None if quiet else record(course, actor, action, title, lines, before, saved, ops)
    sync_agent(course)
    return {"course": saved, "card": card}


def mutate(course: str, fn, actor: str, action: str, title: str, lines_fn=None, quiet: bool = False) -> dict:
    """读档案 → fn(副本) 改 → 存 + 记。fn 可以抛 KeyError（没有这一节）。lines_fn(before, after) 给卡片上的几行。"""
    with cf._lock:
        before = need(course)
        after = copy.deepcopy(before)
        try:
            extra = fn(after)
        except KeyError as e:
            raise bad(f"没有这一节 / 这一条：{e}", f"No such session or item: {e}", 404) from None
        after = cf.normalize(after, course)
        if json.dumps(before, sort_keys=True) == json.dumps(after, sort_keys=True):
            return {"ok": True, "changed": False, "course": view(course, before)}
        lines = lines_fn(before, after) if lines_fn else []
        res = commit(course, before, after, actor, action, title, lines, quiet=quiet)
    return {"ok": True, "changed": True, "course": view(course, res["course"]), "card": res["card"], **(extra if isinstance(extra, dict) else {})}


def describe(before: dict, after: dict) -> list[str]:
    """卡片上的几行：S5 读第 6 章；加了第 8 节「嘉宾讲座」，后面顺延；期末定在 12/8 09:00。"""
    marks = cf.diff_marks(before, after)
    b_s = {s["id"]: s for s in before["sessions"]}
    a_s = {s["id"]: s for s in after["sessions"]}
    out = []
    for sid, fields in marks["sessions"].items():
        s = a_s[sid]
        if fields == ["new"]:
            out.append(L(f"加了第 {s['n']} 节「{s['topic']}」" + (f"（{short_day(s['date'])}）" if s["date"] else ""),
                         f"Added session {s['n']} \"{s['topic']}\"" + (f" ({short_day(s['date'])})" if s["date"] else "")))
            continue
        o = b_s[sid]
        if "n" in fields and fields != ["n"]:
            fields = [f for f in fields if f != "n"]
        if fields == ["n"]:
            continue
        bits = []
        if "date" in fields or "time" in fields:
            bits.append(L(f"{short_day(o['date']) or '没日期'} → {short_day(s['date']) or '没日期'}", f"{short_day(o['date']) or 'no date'} → {short_day(s['date']) or 'no date'}"))
        if "topic" in fields:
            bits.append(L(f"主题改成「{s['topic']}」", f'topic now "{s["topic"]}"'))
        if "readings" in fields:
            rs = "；".join(r["title"] for r in s["readings"]) or L("不用读", "none")
            bits.append(L(f"读 {rs}", f"reading: {rs}"))
        if "folder" in fields and not bits:
            continue
        if "note" in fields and not bits:
            bits.append(L("改了备注", "note changed"))
        if bits:
            out.append(f"S{s['n']} " + "，".join(bits))
    shifted = [sid for sid, f in marks["sessions"].items() if f == ["n"]]
    if shifted:
        out.append(L(f"后面 {len(shifted)} 节的编号顺延了", f"{len(shifted)} later session{'s' if len(shifted) > 1 else ''} renumbered"))
    for sid in marks["removed"]:
        s = b_s.get(sid)
        if s:
            out.append(L(f"去掉了第 {s['n']} 节「{s['topic']}」", f"Removed session {s['n']} \"{s['topic']}\""))
    a_d = {d["id"]: d for d in after["deadlines"]}
    b_d = {d["id"]: d for d in before["deadlines"]}
    for did, fields in marks["deadlines"].items():
        d = a_d[did]
        when = short_due(d["due"]) or L("日期待定", "date TBC")
        if fields == ["new"]:
            out.append(L(f"加了「{d['title']}」· {when}", f'Added "{d["title"]}" · {when}'))
        elif "due" in fields:
            out.append(L(f"「{d['title']}」定在 {when}", f'"{d["title"]}" is now {when}'))
        elif "done" in fields:
            out.append(L(f"「{d['title']}」{'交了' if d['done'] else '还没交'}", f'"{d["title"]}" {"done" if d["done"] else "not done"}'))
        else:
            out.append(L(f"改了「{d['title']}」", f'Edited "{d["title"]}"'))
    for did, d in a_d.items():
        if d["removed"] and did in b_d and not b_d[did]["removed"]:
            out.append(L(f"去掉了「{d['title']}」", f'Removed "{d["title"]}"'))
    return out[:10]


def short_day(d: str | None) -> str:
    if not d:
        return ""
    try:
        x = datetime.strptime(d[:10], "%Y-%m-%d")
    except ValueError:
        return d
    wk = "一二三四五六日"[x.weekday()]
    return L(f"{x.month}/{x.day} 周{wk}", f"{x:%a} {x.day}/{x.month}")


def short_due(due: str | None) -> str:
    if not due:
        return ""
    return short_day(due[:10]) + (f" {due[11:16]}" if len(due) > 10 else "")


@router.post("/api/study/courses/{course}/undo/{cid}")
def undo(course: str, cid: int, redo: int = 0):
    """撤销一次改动（redo=1 再做回来）。只撤后来没再改过的地方；有冲突就 409，说清是哪几处。"""
    with _lock, cdb() as conn:
        r = conn.execute("SELECT * FROM course_changes WHERE id=? AND course=?", (cid, course)).fetchone()
    if not r:
        raise bad("没有这次改动", "No such change", 404)
    if bool(r["undone_at"]) != bool(redo):
        raise bad("已经撤销过了" if not redo else "没撤销过", "Already undone" if not redo else "Not undone", 409)
    before, after = json.loads(r["before"]), json.loads(r["after"])
    with cf._lock:
        cur = need(course)
        # 撤销 = 把 before→after 这次改动从现在的档案上拿掉；重做 = 把 after→before（撤销那一下）拿掉
        out, conflicts = cf.revert(cur, after, before) if redo else cf.revert(cur, before, after)
        if conflicts:
            raise bad("后来又改过这些地方，撤销不了；直接说要改成什么就行", "Those places were changed again later, so this can't be undone; just say what you want instead", 409)
        saved = cf.save(course, out)
        reconcile(course, cur, saved)
        run_ops(course, json.loads(r["ops"] or "[]"), reverse=not redo)
        drop_empty_folders(course, cur, saved)
    with _lock, cdb() as conn:
        conn.execute("UPDATE course_changes SET undone_at=? WHERE id=?", (None if redo else cf.now_iso(), cid))
        row = conn.execute("SELECT * FROM course_changes WHERE id=?", (cid,)).fetchone()
    sync_agent(course)
    return {"ok": True, "card": change_json(row), "course": view(course, saved)}


@router.get("/api/study/courses/{course}/changes")
def changes(course: str, limit: int = 20):
    with _lock, cdb() as conn:
        rows = conn.execute("SELECT * FROM course_changes WHERE course=? ORDER BY id DESC LIMIT ?", (course, max(1, min(limit, 100)))).fetchall()
    return {"ok": True, "changes": [change_json(r) for r in rows]}


def last_marks(course: str) -> dict:
    """标黄：最近一次（24 小时内、没撤销的）改动动到的地方。"""
    with _lock, cdb() as conn:
        r = conn.execute("SELECT * FROM course_changes WHERE course=? AND undone_at IS NULL AND action!='setup' ORDER BY id DESC LIMIT 1", (course,)).fetchone()
    if not r or (datetime.now(settings.tz) - datetime.fromisoformat(r["at"])).total_seconds() > 86400:
        return {"sessions": {}, "deadlines": {}, "removed": [], "change": None}
    try:
        m = cf.diff_marks(json.loads(r["before"]), json.loads(r["after"]))
    except ValueError:
        return {"sessions": {}, "deadlines": {}, "removed": [], "change": None}
    return {**m, "change": change_json(r)}


STEP_NAMES = {1: ("说说这门课", "about the course"), 2: ("你手上有什么", "what you have"), 3: ("核对每一节", "checking the sessions"),
              4: ("放材料、查齐", "adding materials"), 5: ("生成", "generating")}


def chat_context(value: str) -> str | None:
    """学习台旁边的对话（/api/chat/send 的 study：「课|第几步」）→ 只给模型看的一句：用户在学习台的哪门课、加课的哪一步。"""
    course, _, step = str(value or "").partition("|")
    course = course.strip()[:120]
    c = cf.load(course) if course else None
    try:
        n = int(step) if step.strip() else 0
    except ValueError:
        n = 0
    where = LS(f"加一门课的第 {n} 步「{STEP_NAMES[n][0]}」", f"step {n} of adding a course ({STEP_NAMES[n][1]})") if n in STEP_NAMES else LS("学习台", "the study desk")
    if not c:
        return LS(f"（学习台）用户在{where}，还没建课。要加课就按 study skill 带他走（study_ctl.py create …）。",
                  f"(Study desk) The user is on {where}; no course yet. To add one, follow the study skill (study_ctl.py create …).")
    return LS(f"（学习台）用户在{where}，课是「{c['title']}」（study_ctl.py 里写 {course}）。他说哪里不对就按 study skill 直接改（能撤销），改完一句话说清改了什么；"
              "先 study_ctl.py show 看现在的样子。",
              f"(Study desk) The user is on {where} for \"{c['title']}\" (write {course} in study_ctl.py). If something's wrong, fix it directly with the "
              "study skill (undoable) and say in one line what changed; start with study_ctl.py show.")


# —— 读：一门课的全貌 ——

def view(course: str, c: dict | None = None) -> dict:
    """给向导、学习台、app 的一门课：档案 + 每一节查齐的结果 + 标黄 + 生成进度 + 待归节的文件。"""
    c = c or need(course)
    t = cf.today()
    checks = {s["id"]: cf.session_check(course, c, s, t) for s in cf.live_sessions(c)}
    jobs = coursegen.job_view(course)
    prog = study.read_progress(course)
    sessions = []
    for s in cf.live_sessions(c):
        ch = checks[s["id"]]
        route = progress = None
        if s.get("page") and (cf.profile_dir(course) / s["page"]).is_file():
            unit = {"course": course, "thread": study.thread_of(course, "page", s["page"])}
            route = study.load_json(study.gen_path(unit, "path"))
            route = route if isinstance(route, dict) else None
            if route:
                progress = {"done": len(study.done_steps(course, s["page"], route, prog)), "total": len(route.get("items") or [])}
        sessions.append({**s, "check": ch, "job": jobs.get(s["id"]), "progress": progress,
                         "has_page": bool(s.get("page") and (cf.profile_dir(course) / s["page"]).is_file())})
    base = cf.course_root(course)
    incoming = [str(f.relative_to(base)) for f in cf.list_files(base / cf.INCOMING)] if base else []
    info = [str(f.relative_to(base)) for f in cf.list_files(base / cf.INFO_FOLDER)] if base else []
    counts = {k: sum(1 for x in checks.values() if x["status"] == k) for k in ("ready", "missing", "noslides", "later", "empty")}
    return {**c, "sessions": sessions, "deadlines": cf.live_deadlines(c), "marks": last_marks(course), "incoming": incoming, "info_files": info,
            "counts": counts, "syllabus_job": coursegen.SYL.get(course), "gen_note": coursegen.eta_note(), "canvas_job": canvas_job(course),
            "study_agent": chat.study_agent(), "materials_dir": str(base) if base else None}


def canvas_job(course: str) -> dict | None:
    import canvasapi  # 延迟导入：canvasapi 也 import 本模块
    return canvasapi.job_of(course)


@router.get("/api/study/courses")
def list_courses():
    """学习台里所有的课：有档案的（带摘要）+ 没档案的老课；folders = 课件目录里还没当成课的文件夹（加课时能直接用）。"""
    names = study.courses()
    out = []
    for name in names:
        c = cf.load(name)
        item = {"name": name, "title": (c or {}).get("title") or name, "code": (c or {}).get("code") or cf.short_code(name), "profile": bool(c)}
        if c:
            live = cf.live_sessions(c)
            item |= {"sessions": len(live), "confirmed": c["setup"]["confirmed"], "step": c["setup"]["step"], "term": c["term"],
                     "taught": sum(1 for s in live if s["date"] and s["date"] <= cf.today().isoformat())}
        out.append(item)
    mat = study.root("materials")
    folders = []
    if mat and mat.is_dir():
        taken = set(names)
        folders = [p.name for p in sorted(mat.iterdir()) if p.is_dir() and not p.name.startswith((".", "_")) and p.name not in taken
                   and any(x.is_dir() for x in p.iterdir())]
    return {"ok": True, "courses": out, "folders": folders, "agent": chat.study_agent(), "materials": str(mat) if mat else None}


@router.get("/api/study/courses/{course}")
def get_course(course: str):
    return {"ok": True, "course": view(course)}


# —— 建课 ——

class CourseIn(BaseModel):
    name: str
    title: str | None = None
    term: str = ""
    exam: list[str] = []
    learn: list[str] = []
    notes: str = ""
    platform: str = ""
    adopt: bool = False           # 用课件目录里已经有的文件夹（比如课程网站镜像下来的）
    source: str | None = None


def adopt_sessions(course: str) -> list[dict]:
    """已有文件夹里认出来的节：模块名里有 Session / Lecture / 第 N 讲 的一个模块一节，只有 Week 的一周一节。已有学习页按 session 号挂上。"""
    base = cf.course_root(course)
    if not base or not base.is_dir():
        return []
    found: dict[int, dict] = {}
    weeks: dict[int, dict] = {}
    for d in sorted(p for p in base.iterdir() if p.is_dir() and not p.name.startswith(".")):
        title = study.module_title(d.name)
        m = re.search(r"(?:session|lecture|seminar|topic|unit)\s*0*(\d{1,2})\b[\s:：_-]*(.*)|第\s*0*(\d{1,2})\s*[讲课节]\s*[:：]?\s*(.*)", title, re.I)
        if m:
            n = int(m.group(1) or m.group(3))
            topic = (m.group(2) or m.group(4) or "").strip(" :：-_") or title
            found.setdefault(n, {"n": n, "topic": topic, "folder": d.name})
            continue
        w = re.search(r"(?:week|wk)\s*0*(\d{1,2})\b[\s:：_-]*(.*)|第\s*0*(\d{1,2})\s*周\s*[:：]?\s*(.*)", title, re.I)
        if w:
            n = int(w.group(1) or w.group(3))
            weeks.setdefault(n, {"n": n, "week": n, "topic": (w.group(2) or w.group(4) or "").strip(" :：-_") or title, "folder": d.name})
    sessions = list(found.values()) if found else list(weeks.values())
    pdir = study.pages_dir(course)
    if pdir and pdir.is_dir():
        for f in sorted(pdir.glob("*.md")):
            meta, _ = study.read_page(f)
            n = study.session_of(meta.get("session"))
            s = next((x for x in sessions if x["n"] == n), None)
            if s and not s.get("page"):
                s["page"] = f.name
    return sorted(sessions, key=lambda s: s["n"])


@router.post("/api/study/courses")
def create_course(body: CourseIn):
    mat = study.root("materials")
    if not mat:
        raise bad("还没配置课件目录（server.json 的 study.materials）", "No materials directory configured (study.materials in server.json)")
    name = cf.clean_name(body.name)
    if not name:
        raise bad("课名不能是空的", "The course needs a name")
    folder = mat / name
    if cf.load(name):
        raise bad(f"「{name}」已经有了", f'"{name}" already exists', 409)
    if folder.exists() and not body.adopt:
        raise bad(f"课件目录里已经有「{name}」这个文件夹：要用它就选「用已有的文件夹」", f'A folder "{name}" already exists in the materials directory: choose to use it', 409)
    c = cf.blank(name, (body.title or body.name).strip() or name)
    c.update({"term": body.term, "exam": body.exam, "learn": body.learn, "notes": body.notes, "platform": body.platform,
              "setup": {"step": 2, "confirmed": False}, "code": unique_code(c["title"], name)})
    if body.adopt and folder.is_dir():
        c["sessions"] = adopt_sessions(name)
        c["have"] = {"files": True}
    folder.mkdir(parents=True, exist_ok=True)
    c = cf.save(name, c)
    actor = who(body.source)
    record(name, actor, "create", L(f"建了课程「{c['title']}」", f'Created the course "{c["title"]}"'),
           [L(f"用已有的文件夹，认出 {len(c['sessions'])} 节", f"Using the existing folder: {len(c['sessions'])} sessions found")] if body.adopt else [],
           cf.blank(name, c["title"]), c)
    chat.log_activity(L(f"学习台加了一门课「{c['title']}」", f'Added the course "{c["title"]}" to the study desk'), "study", actor if actor != "user" else None)
    return {"ok": True, "name": name, "course": view(name, c)}


def unique_code(title: str, name: str) -> str:
    """缩写别和学习台里别的课撞（「今天」页、截止表靠它分课）：Behavioural Economics 撞了 Business Economics 的 BE → BEE → BEH …"""
    taken = set()
    for other in study.courses():
        if other == name:
            continue
        o = cf.load(other)
        taken.add(((o or {}).get("code") or cf.short_code(other)).upper())
        from schedule import course_short  # 延迟导入：老的课在日程里按这个缩写
        taken.add(course_short(other).upper())
    base = cf.short_code(title)
    words = re.findall(r"[A-Za-z]+", title)
    cands = [base]
    if len(words) >= 2:
        cands += [(words[0][:2] + "".join(w[0] for w in words[1:])).upper(), (words[0][:3] + "".join(w[0] for w in words[1:])).upper(), words[0][:4].upper()]
    for x in cands:
        if x.upper() not in taken:
            return x
    k = 2
    while f"{base}{k}".upper() in taken:
        k += 1
    return f"{base}{k}"


class ProfilePatch(BaseModel):
    title: str | None = None
    code: str | None = None
    term: str | None = None
    exam: list[str] | None = None
    learn: list[str] | None = None
    notes: str | None = None
    style: str | None = None
    platform: str | None = None
    have: dict | None = None
    extras: list[dict] | None = None
    step: int | None = None
    remind_ready: bool | None = None
    source: str | None = None


@router.patch("/api/study/courses/{course}")
def patch_course(course: str, body: ProfilePatch):
    """改课程档案（向导第 1、2 步、以后在设置里改；学习 Agent 按你说的改写法）。你自己改的不出卡。"""
    fields = body.model_dump(exclude_none=True, exclude={"source", "step"})

    def fn(c: dict):
        c.update(fields)
        if body.step is not None:
            c["setup"]["step"] = min(5, max(1, body.step))
    actor = who(body.source)
    lines = []
    if body.style is not None:
        lines.append(L(f"学习页的写法：{body.style[:60]}", f"How notes are written: {body.style[:60]}"))
    return mutate(course, fn, actor, "profile", L("改了课程档案", "Updated the course profile"), (lambda b, a: lines or [L("改了课程档案", "Profile updated")]),
                  quiet=actor == "user")


# —— 大纲 ——

class SyllabusIn(BaseModel):
    url: str | None = None
    upload: str | None = None      # 对话附件的 id（在对话里发的大纲）
    text: str | None = None        # 直接贴的文字
    source: str | None = None


@router.post("/api/study/courses/{course}/syllabus")
async def syllabus_file(course: str, file: UploadFile = File(...), source: str = Form("user")):
    """传大纲文件：存进「课程资料」，抽文字，后台让模型读出每一节、截止和要问的。"""
    need(course)
    path = await save_upload(course, file, cf.INFO_FOLDER)
    text = await asyncio.to_thread(study.text_of, path)
    rel = str(path.relative_to(cf.course_root(course)))
    set_syllabus(course, {"file": rel, "url": None, "read_at": None, "summary": None})
    job = coursegen.start_syllabus(course, text, path.name, who(source))
    return {"ok": True, "file": rel, "job": job}


@router.post("/api/study/courses/{course}/syllabus/from")
async def syllabus_from(course: str, body: SyllabusIn):
    """大纲的另外几种来法：一个网页链接、对话里发过的附件、直接贴的文字。"""
    need(course)
    actor = who(body.source)
    if body.upload:
        path = attachment_path(body.upload)
        dst = cf.unique(cf.course_root(course) / cf.INFO_FOLDER / path.name.split("-", 1)[-1])
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dst)
        text = await asyncio.to_thread(study.text_of, dst)
        rel = str(dst.relative_to(cf.course_root(course)))
        set_syllabus(course, {"file": rel, "url": None})
        return {"ok": True, "file": rel, "job": coursegen.start_syllabus(course, text, dst.name, actor)}
    if body.url:
        import webfetch
        try:
            text, title = await webfetch.page_text(body.url)
        except webfetch.FetchError as e:
            raise bad(f"打不开这个链接：{e}", f"Couldn't open that link: {e}") from None
        set_syllabus(course, {"file": None, "url": body.url})
        return {"ok": True, "url": body.url, "job": coursegen.start_syllabus(course, text, title or body.url, actor)}
    if body.text and body.text.strip():
        set_syllabus(course, {"file": None, "url": None})
        return {"ok": True, "job": coursegen.start_syllabus(course, body.text, L("贴的文字", "pasted text"), actor)}
    raise bad("要给大纲文件、链接或文字", "Give a syllabus file, link or text")


def set_syllabus(course: str, info: dict) -> None:
    with cf._lock:
        c = need(course)
        c["syllabus"] = {**(c.get("syllabus") or {}), **info}
        c["have"] = {**c["have"], "syllabus": True}
        cf.save(course, c)


@router.get("/api/study/courses/{course}/syllabus")
def syllabus_status(course: str):
    return {"ok": True, "job": coursegen.SYL.get(course) or {"status": "none"}, "syllabus": need(course).get("syllabus")}


def apply_syllabus(course: str, data: dict, label: str, actor: str) -> str:
    """coursegen 读完大纲调：写进档案（一次改动，能撤销）。→ 一句话摘要。"""
    with cf._lock:
        before = need(course)
        after, summary = coursegen.merge_syllabus(copy.deepcopy(before), data)
        after["syllabus"] = {**(after.get("syllabus") or {}), "read_at": cf.now_iso(), "summary": summary}
        after["setup"]["step"] = max(after["setup"]["step"], 3)
        after = cf.normalize(after, course)
        commit(course, before, after, actor, "syllabus", L(f"读了大纲「{label}」", f'Read the syllabus "{label}"'), [summary], quiet=actor == "user")
    return summary


# —— 每一节、截止、要问的 ——

class SessionIn(BaseModel):
    topic: str | None = None
    date: str | None = None
    time: str | None = None
    week: int | None = None
    kind: str | None = None
    note: str | None = None
    readings: list | None = None    # 整个换掉：[{"title", "required"}…] 或字符串
    after: str | None = None        # 加一节：放在哪一节后面（id 或节号）
    source: str | None = None


@router.post("/api/study/courses/{course}/sessions")
def add_session(course: str, body: SessionIn):
    if not (body.topic or "").strip():
        raise bad("要写这一节的主题", "The session needs a topic")
    got: dict = {}

    def fn(c: dict):
        s = cf.insert_session(c, {"topic": body.topic, "date": body.date, "time": body.time, "week": body.week, "kind": body.kind or "lecture",
                                  "note": body.note or "", "readings": body.readings or []}, body.after)
        if c["setup"]["confirmed"]:
            cf.ensure_folder(course, s)
        got["session"] = s["id"]
        return {"session": s["id"]}
    return mutate(course, fn, who(body.source), "add", L("加了一节", "Added a session"), describe, quiet=who(body.source) == "user" and not need(course)["setup"]["confirmed"])


@router.patch("/api/study/courses/{course}/sessions/{sid}")
def patch_session(course: str, sid: str, body: SessionIn):
    def fn(c: dict):
        s = cf.session_by(c, sid)
        if not s:
            raise KeyError(sid)
        for k in ("topic", "date", "time", "week", "kind", "note"):
            v = getattr(body, k)
            if v is not None:
                s[k] = v if v != "" else (None if k in ("date", "week") else "")
        if body.readings is not None:
            old = {r["title"]: r for r in s["readings"]}
            s["readings"] = [({**old[r], "title": r} if isinstance(r, str) and r in old else r) for r in body.readings]
        if body.after is not None:  # 挪到某一节后面：先拿掉再插回去
            moved = copy.deepcopy(s)
            cf.remove_session(c, s["id"])
            c["sessions"] = [x for x in c["sessions"] if x["id"] != s["id"]]
            cf.insert_session(c, moved, body.after)
    actor = who(body.source)
    return mutate(course, fn, actor, "edit", L("改了一节", "Edited a session"), describe, quiet=actor == "user" and not need(course)["setup"]["confirmed"])


@router.delete("/api/study/courses/{course}/sessions/{sid}")
def delete_session(course: str, sid: str, source: str | None = None):
    """去掉一节：后面的顺延；这一节的文件夹和学习页留着（撤销就回来）。"""
    def fn(c: dict):
        cf.remove_session(c, sid)
    actor = who(source)
    return mutate(course, fn, actor, "remove", L("去掉了一节", "Removed a session"), describe)


class ReadingIn(BaseModel):
    title: str | None = None
    required: bool | None = None
    skip: bool | None = None
    note: str | None = None
    url: str | None = None
    kind: str | None = None
    source: str | None = None


@router.post("/api/study/courses/{course}/sessions/{sid}/readings")
def add_reading(course: str, sid: str, body: ReadingIn):
    if not (body.title or "").strip():
        raise bad("要写读什么", "The reading needs a title")

    def fn(c: dict):
        s = cf.session_by(c, sid)
        if not s:
            raise KeyError(sid)
        r = cf.norm_reading({"title": body.title, "required": body.required is not False, "note": body.note or "", "url": body.url, "kind": body.kind})
        s["readings"].append(r)
        return {"reading": r["id"] if r else None}
    actor = who(body.source)
    return mutate(course, fn, actor, "reading", L("加了一篇阅读", "Added a reading"), describe, quiet=actor == "user" and not need(course)["setup"]["confirmed"])


@router.patch("/api/study/courses/{course}/sessions/{sid}/readings/{rid}")
def patch_reading(course: str, sid: str, rid: str, body: ReadingIn):
    """改一篇阅读；skip=true 是「先跳过」（学习页会写明没读到），skip=false 撤销跳过。"""
    def fn(c: dict):
        s = cf.session_by(c, sid)
        r = next((x for x in (s or {}).get("readings") or [] if x["id"] == rid), None)
        if not r:
            raise KeyError(rid)
        for k in ("title", "required", "skip", "note", "url", "kind"):
            v = getattr(body, k)
            if v is not None:
                r[k] = v
    actor = who(body.source)
    return mutate(course, fn, actor, "reading", L("改了阅读", "Edited a reading"), describe, quiet=actor == "user")


@router.delete("/api/study/courses/{course}/sessions/{sid}/readings/{rid}")
def delete_reading(course: str, sid: str, rid: str, source: str | None = None):
    def fn(c: dict):
        s = cf.session_by(c, sid)
        if not s or not any(r["id"] == rid for r in s["readings"]):
            raise KeyError(rid)
        s["readings"] = [r for r in s["readings"] if r["id"] != rid]
    actor = who(source)
    return mutate(course, fn, actor, "reading", L("去掉了一篇阅读", "Removed a reading"), describe, quiet=actor == "user" and not need(course)["setup"]["confirmed"])


class DeadlineIn(BaseModel):
    title: str | None = None
    due: str | None = None
    kind: str | None = None
    session: str | None = None
    done: bool | None = None
    weight: str | None = None
    note: str | None = None
    source: str | None = None


@router.post("/api/study/courses/{course}/deadlines")
def add_deadline(course: str, body: DeadlineIn):
    if not (body.title or "").strip():
        raise bad("要写是什么作业 / 考试", "Say what is due")
    if body.due and not cf.norm_due(body.due):
        raise bad("截止写成 YYYY-MM-DD HH:MM", "Write the due date as YYYY-MM-DD HH:MM")

    def fn(c: dict):
        s = cf.session_by(c, body.session) if body.session else None
        d = cf.norm_deadline({"title": body.title, "due": body.due, "kind": body.kind or "assignment", "session": s["id"] if s else None,
                              "weight": body.weight or "", "note": body.note or ""})
        c["deadlines"].append(d)
        return {"deadline": d["id"] if d else None}
    actor = who(body.source)
    return mutate(course, fn, actor, "deadline", L("加了一个截止", "Added a deadline"), describe, quiet=actor == "user" and not need(course)["setup"]["confirmed"])


@router.patch("/api/study/courses/{course}/deadlines/{did}")
def patch_deadline(course: str, did: str, body: DeadlineIn):
    if body.due and not cf.norm_due(body.due):
        raise bad("截止写成 YYYY-MM-DD HH:MM", "Write the due date as YYYY-MM-DD HH:MM")

    def fn(c: dict):
        d = next((x for x in cf.live_deadlines(c) if x["id"] == did), None)
        if not d:
            raise KeyError(did)
        for k in ("title", "due", "kind", "done", "weight", "note"):
            v = getattr(body, k)
            if v is not None:
                d[k] = v
        if body.session is not None:
            s = cf.session_by(c, body.session)
            d["session"] = s["id"] if s else None
    actor = who(body.source)
    return mutate(course, fn, actor, "deadline", L("改了截止", "Edited a deadline"), describe, quiet=actor == "user" and not need(course)["setup"]["confirmed"])


@router.delete("/api/study/courses/{course}/deadlines/{did}")
def delete_deadline(course: str, did: str, source: str | None = None):
    def fn(c: dict):
        d = next((x for x in cf.live_deadlines(c) if x["id"] == did), None)
        if not d:
            raise KeyError(did)
        d["removed"] = True
    actor = who(source)
    return mutate(course, fn, actor, "deadline", L("去掉了截止", "Removed a deadline"), describe)


class AnswerIn(BaseModel):
    answer: str = ""
    source: str | None = None


@router.post("/api/study/courses/{course}/questions/{qid}")
def answer_question(course: str, qid: str, body: AnswerIn):
    """回答读大纲时拿不准的一处：读哪章（写进那一节的阅读）、日期、主题；空答案 = 先不管（问题去掉）。"""
    ans = body.answer.strip()

    def fn(c: dict):
        q = next((x for x in c["questions"] if x["id"] == qid), None)
        if not q:
            raise KeyError(qid)
        s = cf.session_by(c, q["session"]) if q["session"] else None
        if s and ans:
            if q["field"] == "readings":
                ch = re.findall(r"\d+", ans)
                if ch and len(ch) <= 2:  # 读哪章：没写清章节的那条教材换成这一章
                    new = cf.norm_reading({"title": L(f"教材第 {'–'.join(ch)} 章", f"Textbook ch. {'–'.join(ch)}"), "kind": "textbook", "chapter": "–".join(ch)})
                    vague = [r for r in s["readings"] if r["kind"] in ("textbook", "chapter") and (not r["chapter"] or "/" in str(r["chapter"]))]
                    s["readings"] = [r for r in s["readings"] if r not in vague] + [new]
                else:
                    s["readings"].append(cf.norm_reading({"title": ans[:200]}))
            elif q["field"] == "date" and cf.norm_date(ans):
                s["date"] = cf.norm_date(ans)
            elif q["field"] == "topic":
                s["topic"] = ans[:160]
            else:
                s["note"] = (s.get("note") + "；" if s.get("note") else "") + ans[:200]
        c["questions"] = [x for x in c["questions"] if x["id"] != qid]
    actor = who(body.source)
    return mutate(course, fn, actor, "answer", L("定了一处", "Settled a question"), describe, quiet=actor == "user" and not need(course)["setup"]["confirmed"])


@router.post("/api/study/courses/{course}/confirm")
def confirm(course: str, source: str | None = None):
    """核对完：每一节建好文件夹（已有的不动），作业和考试进「今天」和学习 Agent 的截止表。"""
    def fn(c: dict):
        for s in cf.live_sessions(c):
            cf.ensure_folder(course, s)
        c["setup"] = {"step": max(4, c["setup"]["step"]), "confirmed": True}
    actor = who(source)
    res = mutate(course, fn, actor, "setup", L("核对完每一节", "Confirmed the sessions"), lambda b, a: [L(f"{len(cf.live_sessions(a))} 节建好了文件夹", f"Folders ready for {len(cf.live_sessions(a))} sessions")],
                 quiet=actor == "user")
    study.forget_deadlines()
    return res


# —— 放文件、按文件名归节 ——

async def save_upload(course: str, f: UploadFile, folder: str) -> Path:
    base = cf.course_root(course)
    if not base:
        raise bad("还没配置课件目录", "No materials directory configured")
    name = cf.BAD_CHARS.sub(" ", Path(f.filename or "file").name).strip() or "file"
    if name.startswith("."):
        name = name.lstrip(".") or "file"
    d = base / folder
    d.mkdir(parents=True, exist_ok=True)
    path = cf.unique(d / name)
    size = 0
    with path.open("wb") as fh:
        while chunk := await f.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_UPLOAD:
                fh.close()
                path.unlink(missing_ok=True)
                raise bad(f"{name} 超过 {MAX_UPLOAD // 1024 // 1024} MB", f"{name} is over {MAX_UPLOAD // 1024 // 1024} MB", 413)
            fh.write(chunk)
    return path


def attachment_path(fid: str) -> Path:
    """对话里发过的附件 → 服务器上的文件（学习 Agent 把你在对话里发的课件放进某一节时用）。"""
    import files as files_mod
    with _lock, files_mod.adb() as conn:
        r = conn.execute("SELECT path FROM attachments WHERE id=?", (fid.strip(),)).fetchone()
    p = Path(r["path"]) if r else None
    if not p or not p.is_file():
        raise bad(f"找不到附件 {fid}", f"No attachment {fid}", 404)
    return p


def file_into(course: str, c: dict, path: Path, forced: str | None, actor: str) -> dict:
    """一个到了服务器上的文件（在 .incoming 里）：zip 拆开，每个按文件名归节 → [{name, file, session, n, reading, reason, options, status}]。
    归好的挪进那一节的文件夹（阅读对上了就记进那篇阅读）；拿不准的留在待归节，等你定。"""
    base = cf.course_root(course)
    assert base is not None
    items = [path]
    if path.suffix.lower() == ".zip":
        try:
            items = cf.safe_extract(path, path.parent)
        except (ValueError, OSError) as e:
            return {"name": path.name, "status": "error", "reason": str(e)[:200], "files": []}
        finally:
            if path.exists() and path.suffix.lower() == ".zip":
                path.unlink(missing_ok=True)
    out = []
    for p in items:
        if forced:
            s = cf.session_by(c, forced) if forced not in ("course", "info") else None
            cls = {"session": s["id"] if s else None, "reading": None, "course_info": forced in ("course", "info"), "options": [],
                   "reason": L("你指定的", "As you chose")}
            if s:
                rm = [x for x in cf.reading_match(c, p.name) if x[0]["id"] == s["id"]]
                if rm:
                    cls["reading"] = rm[0][1]["id"]
        else:
            cls = cf.classify(c, p.name)
        entry = {"name": p.name, "file": str(p.relative_to(base)), "reason": cls["reason"], "options": cls.get("options") or [], "session": None,
                 "n": None, "reading": None}
        if cls.get("course_info"):
            dst = cf.unique(base / cf.INFO_FOLDER / p.name)
            dst.parent.mkdir(parents=True, exist_ok=True)
            p.rename(dst)
            entry |= {"file": str(dst.relative_to(base)), "status": "info"}
        elif cls.get("session"):
            s = cf.session_by(c, cls["session"])
            assert s is not None
            dst = cf.unique(cf.ensure_folder(course, s) / p.name)
            p.rename(dst)
            rel = str(dst.relative_to(base))
            if cls.get("reading"):
                for r in s["readings"]:
                    if r["id"] == cls["reading"]:
                        r["file"] = rel
                        r["skip"] = False
            entry |= {"file": rel, "status": "filed", "session": s["id"], "n": s["n"], "reading": cls.get("reading")}
        else:
            entry |= {"status": "ask"}
        out.append(entry)
    return {"name": path.name, "status": "ok", "files": out}


def after_files(course: str, before: dict, after: dict, results: list[dict], actor: str) -> dict:
    """放完文件：存档案（阅读的 file、新建的文件夹），Agent 放的记一次改动（卡片上写放进了哪几节）。"""
    lines = []
    for r in results:
        for f in r.get("files") or []:
            if f["status"] == "filed":
                lines.append(L(f"{f['name']} → S{f['n']}", f"{f['name']} → S{f['n']}"))
    with cf._lock:
        saved = cf.save(course, after)
    card = None
    if actor != "user" and lines:
        card = record(course, actor, "files", L(f"放了 {len(lines)} 个文件", f"Filed {len(lines)} file{'s' if len(lines) > 1 else ''}"), lines, before, saved)
    return {"course": saved, "card": card}


@router.post("/api/study/courses/{course}/files")
async def upload_files(course: str, files: list[UploadFile] = File(...), session: str | None = Form(None), source: str = Form("user")):
    """传课件（文件或 zip）：先落到待归节，再按文件名归到每一节；session 给了就直接放进那一节（course = 整门课的资料）。"""
    if len(files) > MAX_FILES:
        raise bad(f"一次最多 {MAX_FILES} 个文件", f"At most {MAX_FILES} files at a time")
    need(course)
    paths = [await save_upload(course, f, cf.INCOMING) for f in files]
    return await asyncio.to_thread(file_paths, course, paths, session, who(source))


def file_paths(course: str, paths: list[Path], session: str | None, actor: str) -> dict:
    with cf._lock:
        before = need(course)
        c = copy.deepcopy(before)
        results = [file_into(course, c, p, session, actor) for p in paths]
        res = after_files(course, before, cf.normalize(c, course), results, actor)
    notify_ready(course, before, res["course"])
    flat = [f for r in results for f in r.get("files") or []]
    errors = [r for r in results if r.get("status") == "error"]
    return {"ok": True, "files": flat, "errors": errors, "card": res["card"], "course": view(course, res["course"])}


class FromUploads(BaseModel):
    ids: list[str]
    session: str | None = None
    source: str | None = None


@router.post("/api/study/courses/{course}/files/from-uploads")
async def files_from_uploads(course: str, body: FromUploads):
    """对话里发的附件（id）放进这门课：学习 Agent 在对话里收到你传的课件时用。复制一份，原附件留在对话里。"""
    need(course)
    base = cf.course_root(course)
    paths = []
    for fid in body.ids[:MAX_FILES]:
        src = attachment_path(fid)
        dst = cf.unique(base / cf.INCOMING / re.sub(r"^[0-9a-f]{12}-", "", src.name))
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        paths.append(dst)
    return await asyncio.to_thread(file_paths, course, paths, body.session, who(body.source))


class AssignIn(BaseModel):
    file: str                 # 课程目录下的相对路径（待归节里的，或者已经在某一节里的）
    session: str              # 节的 id / 节号；course = 整门课的资料；incoming = 挪回待归节
    reading: str | None = None
    source: str | None = None


@router.post("/api/study/courses/{course}/files/assign")
def assign_file(course: str, body: AssignIn):
    """把一个文件归到某一节（「归到…」）、挪到整门课的资料，或者挪回待归节。能撤销。"""
    base = cf.course_root(course)
    src = study.inside(base, body.file) if base else None
    if not src:
        raise bad("找不到这个文件", "File not found", 404)
    actor = who(body.source)
    with cf._lock:
        before = need(course)
        c = copy.deepcopy(before)
        if body.session in ("course", "info"):
            dst = cf.unique(base / cf.INFO_FOLDER / src.name)
            label = L("整门课的资料", "course info")
        elif body.session == "incoming":
            dst = cf.unique(base / cf.INCOMING / src.name)
            label = L("待归节", "unfiled")
        else:
            s = cf.session_by(c, body.session)
            if not s:
                raise bad("没有这一节", "No such session", 404)
            dst = cf.unique(cf.ensure_folder(course, s) / src.name)
            label = f"S{s['n']}"
        rel_src = str(src.relative_to(base))
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dst)
        rel_dst = str(dst.relative_to(base))
        for s in c["sessions"]:
            for r in s["readings"]:
                if r.get("file") == rel_src:
                    r["file"] = rel_dst if body.session not in ("incoming",) else None
                if body.reading and r["id"] == body.reading:
                    r["file"], r["skip"] = rel_dst, False
        after = cf.normalize(c, course)
        saved = cf.save(course, after)
        card = record(course, actor, "files", L(f"{src.name} → {label}", f"{src.name} → {label}"), [f"{src.name} → {label}"], before, saved,
                      [{"op": "move", "from": rel_src, "to": rel_dst}])
    notify_ready(course, before, saved)
    return {"ok": True, "file": rel_dst, "card": card if actor != "user" else None, "change": card["changeId"], "course": view(course, saved)}


@router.delete("/api/study/courses/{course}/files")
def remove_incoming(course: str, file: str):
    """删掉一个还在待归节里的文件（传错了）。已经归到某一节的用 assign 挪回待归节。"""
    base = cf.course_root(course)
    p = study.inside(base, file) if base else None
    if not p or p.parent.name != cf.INCOMING:
        raise bad("只能删待归节里的文件", "Only unfiled uploads can be deleted here")
    p.unlink()
    return {"ok": True}


@router.get("/api/study/courses/{course}/check")
def check(course: str):
    c = need(course)
    return {"ok": True, "sessions": [cf.session_check(course, c, s) for s in cf.live_sessions(c)]}


# —— 生成 ——

class GenIn(BaseModel):
    sessions: list[str]
    cards: bool = True
    quiz: bool = True
    video: bool = False
    rewrite: bool = False     # 已经有学习页也重写（比如材料补齐了）
    force: bool = False       # 材料没齐也写（学习页会写明缺了什么）
    source: str | None = None


@router.post("/api/study/courses/{course}/generate")
async def generate(course: str, body: GenIn):
    """生成学习页 + 学习路线（+ 闪卡、小测、视频）。材料没齐的先不写，除非 force（明着跳过的阅读不算没齐）。"""
    c = need(course)
    todo, blocked = [], []
    for ref in body.sessions:
        s = cf.session_by(c, ref)
        if not s:
            continue
        ch = cf.session_check(course, c, s)
        if ch["status"] not in ("ready",) and not body.force:
            blocked.append({"session": s["id"], "n": s["n"], "status": ch["status"], "missing": ch["missing"]})
            continue
        todo.append(s["id"])
    if blocked and not todo:
        return {"ok": False, "status": "needs_materials", "blocked": blocked}
    got = coursegen.enqueue(course, todo, {"cards": body.cards, "quiz": body.quiz, "video": body.video and bool(study.cfg().get("video_cmd")),
                                           "rewrite": body.rewrite})
    with cf._lock:
        cur = need(course)
        cur["setup"]["step"] = 5
        cf.save(course, cur)
    return {"ok": True, **got, "blocked": blocked, "note": coursegen.eta_note()}


@router.get("/api/study/courses/{course}/generate")
def gen_status(course: str):
    return {"ok": True, "jobs": coursegen.job_view(course)}


def bind_page(course: str, sid: str, page: str) -> None:
    """coursegen 写好学习页：记进那一节（不算一次改动：撤销课程结构不删学习页）。"""
    with cf._lock:
        c = need(course)
        s = cf.session_by(c, sid)
        if s and s.get("page") != page:
            s["page"] = page
            cf.save(course, c)


# —— 删课：撤不回，出收件箱卡 ——

@router.delete("/api/study/courses/{course}")
async def delete_course(course: str, source: str | None = None):
    """删一门课撤不回：只出一张收件箱卡，点了同意服务端把这门课的课件、学习页、生成的东西整个挪进 study-trash。"""
    c = cf.load(course)
    if not c and course not in study.courses():
        raise bad("没有这门课", "No such course", 404)
    title = (c or {}).get("title") or course
    base = cf.course_root(course)
    n_files = sum(1 for p in base.rglob("*") if p.is_file()) if base and base.is_dir() else 0
    pdir = study.pages_dir(course)
    n_pages = len(list(pdir.glob("*.md"))) if pdir and pdir.is_dir() else 0
    agent = who(source)
    src = agent if agent != "user" else (chat.study_agent() or "main")
    body = inbox.ItemIn(kind="write", title=L(f"删掉课程「{title}」", f'Delete the course "{title}"'), source=src,
                        why=L("删了就从学习台上去掉，撤不回。", "It disappears from the study desk and can't be undone."),
                        changes=[L(f"课件 {n_files} 个文件", f"{n_files} material files"), L(f"学习页 {n_pages} 篇、闪卡小测和学习路线", f"{n_pages} study pages, cards, quizzes and paths"),
                                 L("截止从「今天」和截止表里去掉", "Its deadlines leave Today and the deadline table")],
                        detail=L("文件不会立刻消失：整个挪进服务器上的 study-trash 文件夹，要找回得在服务器上手动挪回来。",
                                 "The files aren't wiped right away: they move to the server's study-trash folder, and getting them back means moving them by hand."),
                        approveLabel=L("删掉这门课", "Delete the course"), level="quiet", dedupe=f"study:delete:{course}")
    res = await inbox.add(body)
    if isinstance(res, dict) and res.get("id"):
        with _lock, cdb() as conn:
            conn.execute("INSERT OR REPLACE INTO course_deletes(inbox_id, course, created_at) VALUES(?,?,?)", (res["id"], course, cf.now_iso()))
        return {"ok": True, "inbox": res["id"]}
    return res


def trash(course: str) -> str:
    """整门课挪进 <data_dir>/study-trash/<课>-<时间>/（materials、pages）。"""
    stamp = datetime.now(settings.tz).strftime("%Y%m%d-%H%M%S")
    dst = settings.data_dir / "study-trash" / f"{cf.clean_name(course) or 'course'}-{stamp}"
    dst.mkdir(parents=True, exist_ok=True)
    moved = []
    for label, p in (("materials", cf.course_root(course)), ("pages", study.pages_dir(course)), ("pages", settings.data_dir / "study" / course)):
        if p and p.is_dir():
            target = dst / label
            if target.exists():
                target = cf.unique(target)
            shutil.move(str(p), str(target))
            moved.append(str(target))
    want = [x for x in study.cfg().get("courses") or [] if x != course]
    if want != (study.cfg().get("courses") or []):
        data = dict(raw(fresh=True))
        data["study"] = {**(data.get("study") or {}), "courses": want}
        save_config(data)
    study.forget_deadlines()
    return str(dst)


async def on_write_decided(it: dict, action: str) -> dict | None:
    with _lock, cdb() as conn:
        r = conn.execute("SELECT course FROM course_deletes WHERE inbox_id=?", (it["id"],)).fetchone()
    if not r:
        return await _prev_write(it, action) if _prev_write else None
    if action != "approve":
        return {"silent": True}
    try:
        where = await asyncio.to_thread(trash, r["course"])
    except OSError as e:
        return {"failed": L(f"没删成：{e}", f"Couldn't delete it: {e}")}
    chat.log_activity(L(f"删了课程「{r['course']}」（挪进了 {where}）", f'Deleted the course "{r["course"]}" (moved to {where})'), "deleted")
    return {"result": L(f"删了，文件挪进了 {where}", f"Deleted; files moved to {where}"), "silent": True}


_prev_write = inbox.HOOKS.get("write")
inbox.HOOKS["write"] = on_write_decided


# —— 学习 Agent 的截止表 ——

def sync_agent(course: str | None = None) -> None:
    """档案里的截止改了：学习台顶栏的缓存作废，学习 Agent 的截止表同步一次（在后台）。"""
    study.forget_deadlines()
    try:
        study.sync_course_deadlines()
    except Exception:  # noqa: BLE001 — 同步不上只是截止表少几行
        pass


def notify_ready(course: str, before: dict, after: dict) -> None:
    """有一节从「没齐」变成「齐了」、还没生成：这门课开了 remind_ready 而且服务器允许（study.notify_ready）就静音推一条。"""
    if not after.get("remind_ready") or not study.cfg().get("notify_ready"):
        return
    t = cf.today()
    was = {s["id"]: cf.session_check(course, before, s, t)["status"] for s in cf.live_sessions(before)}
    for s in cf.live_sessions(after):
        if s.get("page") or was.get(s["id"]) == "ready":
            continue
        if cf.session_check(course, after, s, t)["status"] == "ready":
            import push
            text = LS(f"「{after['title']}」S{s['n']} 的材料齐了，点一下开始生成", f"{after['title']} S{s['n']}: materials are in; tap to generate")
            coursegen.spawn_sync(push.send_push(settings.app_name, text, {"target": {"type": "study", "course": course, "session": s["id"]}},
                                                thread_id=chat.study_agent(), level="quiet", collapse=f"study-ready:{course}:{s['id']}", kind="card"))
