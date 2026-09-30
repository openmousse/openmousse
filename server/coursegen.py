"""学习台的两件要模型做的事（2026-09-30）：读大纲抽出每一节和截止；按一节的材料写学习页，再排学习路线、出闪卡小测。

都走 llmjson（OpenClaw 的 llm-task：零工具、每次新会话、只回 JSON）：大纲和课件是别人写的字，不进带工具的回合；
别的 claw 没有 llm-task 时退回一问一答。一次只写一节（排队），写好一节在学习 Agent 的对话里记一行灰字。
状态存在 pages/<课>/.gen/jobs.json：服务重启后没写完的标成「中断了」，再点一次就行。
"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from pathlib import Path

import chat
import coursefile as cf
import llmjson
import study
from config import settings
from i18n import L, LS

SYLLABUS_CHARS = 60_000
PAGE_TIMEOUT, PAGE_TOKENS = 900, 32_000
SYL: dict[str, dict] = {}          # 课 → 读大纲的进度 {status, started, error, summary}
_queue: asyncio.Queue | None = None
_worker: asyncio.Task | None = None
_tasks: set[asyncio.Task] = set()


def spawn(coro) -> None:
    t = asyncio.create_task(coro)
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)


# —— 读大纲 ——

def syllabus_prompt(today: str) -> str:
    return L(
        "INPUT 里是一门课的大纲（syllabus / course outline / module guide）原文。把它整理成一个 JSON 对象，用来在学习台上建好这门课的每一节和截止。\n"
        f"今天是 {today}。只写大纲里真有的，不要编；拿不准的留空，并在 questions 里问一句。\n"
        "字段：\n"
        "- title：课名（大纲原文）；code：课程代码或常用缩写（没有就空）；term：学期（没有就空）\n"
        "- exam：考核方式一句话（比如「期末闭卷 2 小时 60%，小组作业 40%」），没写就空\n"
        "- sessions：每一节课一项，按顺序：n（节号，大纲自己编了号就照它）、date（YYYY-MM-DD；大纲只写了第几周和星期几、又写了开学日期的就算出来，"
        "年份按今天推断；算不出来就空）、time（HH:MM，没有就空）、week（第几周，没写就空）、topic（主题，保留原文）、"
        "kind（lecture / seminar / case / guest / review / workshop / other）、readings（这一节要读的：title 原文、required（必读 true / 选读 false）、"
        "kind（textbook / article / case / chapter / web / video / other）、chapter（教材第几章，没有就空）、note（页码、要求）、url（大纲里给了链接就写））。"
        "放假、阅读周这类没课的周不算一节。\n"
        "- deadlines：作业、展示、小测、考试：title、due（YYYY-MM-DD HH:MM 或只有日期；没写就空）、kind（assignment / group / presentation / exam / "
        "classwork / quiz / other）、session（跟哪一节有关的节号，没有就空）、weight（占比，比如 20%）\n"
        "- questions：大纲里看不清、要用户定的地方（比如「Ch. 5/6」到底读哪章、两个日期对不上），每个一项：session（节号或空）、field（readings / date / "
        "topic / deadline / other）、text（问用户的一句话，中文）、options（给 2–4 个选项，可以空）。没有就空数组。\n"
        "- summary：一句话，读到了什么（比如「8 节、2 个作业、期末考试」）。\n"
        "只输出这个 JSON 对象。",
        "INPUT holds the text of a course syllabus (course outline / module guide). Turn it into one JSON object used to set up the course's sessions "
        f"and deadlines on a study desk.\nToday is {today}. Only write what the syllabus says; don't invent. Leave unclear things empty and ask about them in questions.\n"
        "Fields:\n"
        "- title: the course name as written; code: course code or usual abbreviation (or empty); term (or empty)\n"
        "- exam: assessment in one sentence (e.g. \"2-hour closed-book final 60%, group project 40%\"), or empty\n"
        "- sessions: one item per class, in order: n (session number; keep the syllabus's own numbering), date (YYYY-MM-DD; if only the week and "
        "weekday are given plus a term start date, work it out, inferring the year from today; otherwise empty), time (HH:MM or empty), week "
        "(week number or empty), topic (as written), kind (lecture / seminar / case / guest / review / workshop / other), readings (for this session: "
        "title as written, required (true for required, false for optional), kind (textbook / article / case / chapter / web / video / other), chapter "
        "(textbook chapter or empty), note (pages, instructions), url (if the syllabus gives a link)). Breaks and reading weeks are not sessions.\n"
        "- deadlines: assignments, presentations, quizzes, exams: title, due (YYYY-MM-DD HH:MM or just the date; empty if not given), kind (assignment / "
        "group / presentation / exam / classwork / quiz / other), session (related session number or empty), weight (e.g. 20%)\n"
        "- questions: things that are unclear and need the user (e.g. which of \"Ch. 5/6\" to read, two dates that disagree): session (number or empty), "
        "field (readings / date / topic / deadline / other), text (one question for the user), options (2–4 choices, may be empty). Empty array if none.\n"
        "- summary: one sentence on what was found (e.g. \"8 sessions, 2 assignments and a final exam\").\n"
        "Output only this JSON object.")


SYL_SCHEMA = {"type": "object", "required": ["sessions"], "properties": {"sessions": {"type": "array"}, "deadlines": {"type": "array"},
                                                                           "questions": {"type": "array"}}}


def num(v) -> int | None:
    try:
        return int(str(v).strip().lstrip("Ss"))
    except (TypeError, ValueError):
        return None


def merge_syllabus(c: dict, data: dict) -> tuple[dict, str]:
    """读出来的结果 → 课程档案的每一节、截止、要问的。已经核对过（建好文件夹）的课：按节号更新、不丢已有的文件夹和学习页。→ (新档案, 一句话)"""
    old = {s["n"]: s for s in cf.live_sessions(c)}
    sessions = []
    for s in data.get("sessions") or []:
        if not isinstance(s, dict) or not str(s.get("topic") or "").strip():
            continue
        n = num(s.get("n"))
        prev = old.get(n) if n else None
        item = {"n": n or 0, "date": s.get("date"), "time": s.get("time"), "week": s.get("week"), "topic": s.get("topic"),
                "kind": s.get("kind") or "lecture", "readings": s.get("readings") or []}
        if prev:  # 同一节：留着 id、文件夹、学习页和已经到手的阅读
            have = {r["title"]: r for r in prev["readings"] if r.get("file") or r.get("skip")}
            item["readings"] = [{**r, **({"file": have[r.get("title")]["file"], "skip": have[r.get("title")]["skip"]} if isinstance(r, dict) and r.get("title") in have else {})}
                                for r in item["readings"]]
            item = {**prev, **{k: v for k, v in item.items() if v not in (None, "", [])}, "id": prev["id"]}
        sessions.append(item)
    kept = [s for s in c["sessions"] if s["removed"] or (s["n"] not in {num(x.get("n")) for x in data.get("sessions") or [] if isinstance(x, dict)} and (s.get("folder") or s.get("page")))]
    c = {**c, "sessions": sessions + kept}
    c = cf.normalize(c, c["name"])
    by_n = {s["n"]: s["id"] for s in cf.live_sessions(c)}
    ddls = [d for d in c["deadlines"] if d["removed"] or d["done"]]
    for d in data.get("deadlines") or []:
        if isinstance(d, dict) and str(d.get("title") or "").strip():
            ddls.append({"title": d["title"], "due": d.get("due"), "kind": d.get("kind") or "assignment", "session": by_n.get(num(d.get("session")) or -1),
                         "weight": d.get("weight") or ""})
    c["deadlines"] = ddls
    c["questions"] = [{"session": by_n.get(num(q.get("session")) or -1), "field": q.get("field") or "", "text": q.get("text"), "options": q.get("options") or []}
                      for q in data.get("questions") or [] if isinstance(q, dict) and str(q.get("text") or "").strip()]
    for k in ("term", "code"):
        if data.get(k) and not c.get(k):
            c[k] = str(data[k])[:40]
    if data.get("exam") and not c.get("notes"):
        c["notes"] = L("考核：", "Assessment: ") + str(data["exam"] if not isinstance(data["exam"], dict) else data["exam"].get("format") or "")[:300]
    c = cf.normalize(c, c["name"])
    n_s, n_d = len(cf.live_sessions(c)), len(cf.live_deadlines(c))
    summary = str(data.get("summary") or "").strip()[:120] or L(f"{n_s} 节、{n_d} 个作业和考试", f"{n_s} sessions, {n_d} assignments and exams")
    return c, summary


async def read_syllabus(course: str, text: str, label: str, actor: str) -> None:
    """后台：大纲文字 → 模型整理 → 写进档案（一次改动，能撤销）。"""
    import courses  # 延迟导入：courses 也 import 本模块
    SYL[course] = {"status": "running", "started": cf.now_iso(), "label": label}
    try:
        data, route = await llmjson.ask(syllabus_prompt(datetime.now(settings.tz).strftime("%Y-%m-%d")), {"syllabus": text[:SYLLABUS_CHARS]},
                                        SYL_SCHEMA, timeout=300, thinking="low", max_tokens=16_000)
        if not isinstance(data, dict):
            raise llmjson.LLMError("not an object")
        summary = await asyncio.to_thread(courses.apply_syllabus, course, data, label, actor)
        SYL[course] = {"status": "done", "finished": cf.now_iso(), "label": label, "summary": summary, "route": route}
    except (llmjson.LLMError, OSError, ValueError, KeyError) as e:
        SYL[course] = {"status": "error", "finished": cf.now_iso(), "label": label, "error": str(e)[:300]}


def start_syllabus(course: str, text: str, label: str, actor: str) -> dict:
    if (SYL.get(course) or {}).get("status") == "running":
        return SYL[course]
    if len(text.strip()) < 80:
        SYL[course] = {"status": "error", "label": label, "error": L("这份大纲几乎读不出字（可能是扫描件）。换一份能选中文字的，或者直接在表里填。",
                                                                    "Almost no text could be read from this syllabus (probably a scan). Try one with selectable text, or fill in the table.")}
        return SYL[course]
    SYL[course] = {"status": "running", "started": cf.now_iso(), "label": label}
    spawn(read_syllabus(course, text, label, actor))
    return SYL[course]


# —— 写学习页 ——

def jobs_path(course: str) -> Path:
    return study.gen_dir(course) / "jobs.json"


def read_jobs(course: str) -> dict:
    try:
        d = json.loads(jobs_path(course).read_text(encoding="utf8"))
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def write_jobs(course: str, jobs: dict) -> None:
    p = jobs_path(course)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.tmp")
    tmp.write_text(json.dumps(jobs, ensure_ascii=False, indent=1), encoding="utf8")
    tmp.replace(p)


_live: dict[tuple[str, str], dict] = {}   # 这次服务启动后排进来的（区分「中断了」）


def job_view(course: str) -> dict:
    """每一节生成到哪了：{sid: {status: queued / running / done / error / interrupted, stage, steps, error, …}}。"""
    out = {}
    for sid, j in read_jobs(course).items():
        if not isinstance(j, dict):
            continue
        if j.get("status") in ("queued", "running") and (course, sid) not in _live:
            j = {**j, "status": "interrupted"}
        out[sid] = j
    return out


def set_job(course: str, sid: str, **kw) -> dict:
    jobs = read_jobs(course)
    j = {**(jobs.get(sid) or {}), **kw}
    jobs[sid] = j
    write_jobs(course, jobs)
    if j.get("status") in ("queued", "running"):
        _live[(course, sid)] = j
    else:
        _live.pop((course, sid), None)
    return j


def lang_of(c: dict) -> str:
    return "zh" if "zh" in c.get("learn") or settings.language == "zh" else "en"


def profile_brief(c: dict) -> dict:
    exam = {"closed": L("闭卷笔试", "closed-book exam"), "open": L("开卷考试", "open-book exam"), "essay": L("书面作业", "written coursework"),
            "group": L("小组作业", "group work"), "present": L("展示", "presentation"), "unknown": L("还不知道", "not known yet")}
    learn = {"zh": L("中文讲，术语留英文（第一次出现写成「中文 English」）", "explain in Chinese, keep terms in English"),
             "intuition": L("先讲直觉再上公式", "intuition before formulas"), "examples": L("多举例子和案例", "lots of examples and cases"),
             "video": L("多想想哪些概念适合做成讲解视频", "point out concepts that suit an explainer video"),
             "practice": L("多做题：题型和自测题多一些", "more practice: more worked problems and self-test questions")}
    return {"course": c["title"], "term": c["term"], "exam": [exam[k] for k in c["exam"]], "how_i_learn": [learn[k] for k in c["learn"]],
            "notes": c["notes"], "style": c["style"]}


def page_prompt(c: dict, s: dict, has_captions: bool, skipped: list[str]) -> str:
    zh = lang_of(c) == "zh"
    fmt = ("**Q1.** 题目\n\n<details><summary>看答案</summary>\n\n参考答案\n\n</details>" if zh else
           "**Q1.** Question\n\n<details><summary>Answer</summary>\n\nReference answer\n\n</details>")
    head = f"# S{s['n']} · {s['topic']}"
    if zh:
        return (
            "你是这门课的助教，给学生写这一节的学习页（Markdown）。INPUT 里有：course（课程档案：考试怎么考、学生喜欢怎么学、还想说的话、学习页的写法）、"
            "session（这一节）、materials（这一节的课件原文，PDF 按页标了页码；有录播就有字幕，[时:分:秒] 是时间点；还有阅读材料）。\n"
            "只根据这些材料写；材料里没有、你补充的要标「（补充）」。引用写出处：文件名简称 + 页码（p.12），或录播时间点。\n"
            f"结构（二级标题照写，编号跟着顺延；没有的节整节不写）：\n{head}\n"
            "## 1. 这一节在回答什么问题（2–4 句）\n"
            + ("## 2. 课上重点（录播）（老师课上强调的、说考不考的、举的例子，每条带时间点）\n" if has_captions else "")
            + "## 核心概念（表格：术语 English | 中文解释 | 出处）\n"
            "## 框架 / 模型 / 公式（按知识块分 ### 小节，每块配课上的例子、标页码；公式用 LaTeX $…$）\n"
            "## 阅读材料要点（有阅读材料才写：每篇 3–5 条，和课件怎么连）\n"
            "## 题型（有练习、problem set、case 讨论题才写：怎么做、容易错在哪）\n"
            "## 易混点和考点\n"
            f"## 自测题（6–10 题，从概念到应用；每题一定照这个格式，题号写成 **Q1.**、**Q2.**…）：\n{fmt}\n"
            "## 🎬 可以做成视频的概念（2–3 个：一句话 + 3–4 步画面）\n"
            + (f"这些阅读学生跳过了、没有材料：{'；'.join(skipped)}。在「阅读材料要点」里写一句「没读到：…」，别编它的内容。\n" if skipped else "")
            + "语言：中文为主，专有名词第一次出现写成「中文 English」；按 course.how_i_learn 和 course.style 调整写法。长度：抓住要点，一般 8,000–20,000 字。\n"
            '只输出一个 JSON 对象：{"markdown": "整页 Markdown（从 # 标题开始）", "video_candidates": ["概念：一句话说清画面", …], "title": "这一节的短标题"}')
    return (
        "You are this course's teaching assistant writing the study notes (Markdown) for one session. INPUT has: course (the course profile: how it's "
        "assessed, how the student likes to learn, their notes, the writing style they asked for), session (this session) and materials (the full text "
        "of the session's materials, PDF pages marked; lecture captions with [h:mm:ss] times if there's a recording; the readings).\n"
        "Base everything on these materials; mark anything you add from outside them with \"(added)\". Cite sources: short file name + page (p.12), or recording time.\n"
        f"Structure (use these level-2 headings, numbered in order; leave out a section that has nothing):\n{head}\n"
        "## 1. What question this session answers (2–4 sentences)\n"
        + ("## 2. In class (recording) (what the lecturer stressed, said is or isn't examined, the examples used; each with a time)\n" if has_captions else "")
        + "## Key concepts (table: term | explanation | source)\n"
        "## Frameworks / models / formulas (### subsections per block, each with the class example and pages; LaTeX $…$ for formulas)\n"
        "## Readings (only if there are readings: 3–5 points each, and how they connect to the slides)\n"
        "## Question types (only with exercises, problem sets or case questions: how to solve them, common mistakes)\n"
        "## Common confusions and exam points\n"
        f"## Self-test (6–10 questions, from concepts to application; always this format, numbered **Q1.**, **Q2.**…):\n{fmt}\n"
        "## 🎬 Concepts for an explainer video (2–3: one sentence plus 3–4 visual steps)\n"
        + (f"The student skipped these readings, which have no material: {'; '.join(skipped)}. Say \"Not read: …\" under Readings; don't invent their content.\n" if skipped else "")
        + "Follow course.how_i_learn and course.style. Length: cover what matters, usually 5,000–12,000 words.\n"
        'Output one JSON object only: {"markdown": "the whole page in Markdown (starting with the # title)", "video_candidates": ["concept: one line on the visuals", …], "title": "a short title for the session"}')


SELF_H = re.compile(r"^##\s+(?:\d+[.、．]\s*)?(?:自测题|自测|self[- ]?test|check yourself)", re.I | re.M)


def self_test_ok(md: str) -> bool:
    """自测题那一节在、至少三题、每题是 **Q1.** + <details> 的样子（学习台的「自测」标签靠它拆题）。"""
    m = SELF_H.search(md)
    if not m:
        return False
    rest = md[m.end():]
    nxt = re.search(r"^##\s", rest, re.M)
    body = rest[:nxt.start()] if nxt else rest
    return len(re.findall(r"\*\*\s*Q?\s*\d+\s*[.、．:：]?\s*\*\*", body)) >= 3 and body.count("<details>") >= 3


def page_file(c: dict, s: dict) -> str:
    stem = cf.folder_part(s["topic"], 60).replace("_ ", " - ") or f"Session {s['n']}"
    return f"S{s['n']:02d} {stem}.md"


def front_matter(meta: dict) -> str:
    import yaml
    return "---\n" + yaml.safe_dump(meta, allow_unicode=True, sort_keys=False, width=1000).strip() + "\n---\n\n"


async def write_page(course: str, sid: str) -> dict:
    """写这一节的学习页：材料全文 + 课程档案 → 模型 → pages/<课>/SNN 主题.md，档案里记下 page。→ 这一节（带 page）。"""
    c = cf.load(course)
    s = cf.session_by(c, sid) if c else None
    if not c or not s:
        raise ValueError(L("没有这一节", "No such session"))
    base = cf.course_root(course)
    check = cf.session_check(course, c, s)
    sources = [base / p for p in check["slides"]] if base else []
    name = s.get("page") or page_file(c, s)
    unit = {"course": course, "kind": "page", "path": name, "title": f"S{s['n']} {s['topic']}", "body": None, "sources": sources,
            "thread": study.thread_of(course, "page", name), "session": s["n"]}
    budget = int(study.cfg().get("gen_context_chars") or study.GEN_CONTEXT_CHARS)
    materials = await asyncio.to_thread(study.context_for, unit, budget)
    skipped = [r["title"] for r in check["readings"] if r["state"] == "skipped"]
    has_caps = bool(check["captions"]) or bool(study.recordings_of(course, s["n"]))
    prompt = page_prompt(c, s, has_caps, skipped)
    inp = {"course": profile_brief(c), "session": {"n": s["n"], "topic": s["topic"], "date": s["date"],
                                                   "readings": [{"title": r["title"], "required": r["required"], "state": r["state"]} for r in check["readings"]]},
           "materials": materials}
    data, route = await llmjson.ask(prompt, inp, {"type": "object", "required": ["markdown"]}, timeout=PAGE_TIMEOUT, thinking="low", max_tokens=PAGE_TOKENS)
    md = str((data or {}).get("markdown") or "").strip() if isinstance(data, dict) else ""
    if len(md) < 800:
        raise ValueError(L("写出来的学习页太短，没存", "The page came back too short; not saved"))
    if not self_test_ok(md):  # 自测题格式不对：只让它把那一节按格式重写一遍
        fix = L("下面这页学习页的「自测题」一节格式不对。把整页原样输出，只把自测题改成：每题 **Q1.** 题目，下面空一行接 <details><summary>看答案</summary>，"
                "空一行写答案，空一行 </details>；6–10 题。只输出 JSON：{\"markdown\": \"…\"}",
                "The Self-test section of this page has the wrong format. Output the whole page unchanged except the self-test: each question as **Q1.** "
                "question, a blank line, <details><summary>Answer</summary>, a blank line, the answer, a blank line, </details>; 6–10 questions. "
                "Output JSON only: {\"markdown\": \"…\"}")
        try:
            fixed, _ = await llmjson.ask(fix, {"page": md}, {"type": "object", "required": ["markdown"]}, timeout=PAGE_TIMEOUT, max_tokens=PAGE_TOKENS)
            f2 = str((fixed or {}).get("markdown") or "").strip() if isinstance(fixed, dict) else ""
            if len(f2) > len(md) * 0.6 and self_test_ok(f2):
                md = f2
        except llmjson.LLMError:
            pass
    if not md.startswith("#"):
        md = f"# S{s['n']} · {s['topic']}\n\n{md}"
    rels = [str(p.relative_to(base)) for p in sources] if base else []
    vids = [str(v).strip()[:200] for v in (data.get("video_candidates") or []) if str(v).strip()][:4] if isinstance(data, dict) else []
    meta = {"course": course, "session": s["n"], "title": str((data or {}).get("title") or s["topic"]).strip()[:120] or s["topic"],
            "sources": rels, "generated": cf.now_iso(), "model": route, "video_candidates": vids}
    pdir = cf.profile_dir(course)
    pdir.mkdir(parents=True, exist_ok=True)
    target = pdir / name
    tmp = target.with_name(f".{target.name}.tmp")
    tmp.write_text(front_matter(meta) + md + "\n", encoding="utf8")
    tmp.replace(target)
    import courses  # 延迟导入
    await asyncio.to_thread(courses.bind_page, course, sid, name)
    return {"page": name, "title": meta["title"], "videos": vids}


async def gen_session(course: str, sid: str, opts: dict) -> None:
    """一节：学习页 → 学习路线 → （闪卡、小测）→ （视频）。每步的结果记进 jobs.json。"""
    steps = {"page": "queued", "path": "queued"} | {k: "queued" for k in ("cards", "quiz", "video") if opts.get(k)}
    set_job(course, sid, status="running", stage="page", steps=steps, started=cf.now_iso(), error=None)
    try:
        c = cf.load(course)
        s = cf.session_by(c, sid) if c else None
        if not s:
            raise ValueError(L("没有这一节", "No such session"))
        if opts.get("rewrite") or not s.get("page") or not (cf.profile_dir(course) / s["page"]).is_file():
            steps["page"] = "running"
            set_job(course, sid, steps=steps)
            got = await write_page(course, sid)
        else:
            got = {"page": s["page"], "videos": []}
        steps["page"] = "done"
        unit = study.resolve_unit(course, got["page"], None)
        for kind in ("path", "cards", "quiz"):
            if kind not in steps:
                continue
            steps[kind] = "running"
            set_job(course, sid, stage=kind, steps=steps)
            key = f"{unit['thread']}:{kind}"
            study.JOBS[key] = {"status": "running", "started": chat.now_iso()}
            await study.gen_job(key, unit, kind)
            steps[kind] = "done" if study.JOBS.get(key, {}).get("status") == "done" else "error"
            set_job(course, sid, steps=steps)
        if "video" in steps:
            cands = (unit.get("video_candidates") or got.get("videos") or [])
            meta, _ = study.read_page(cf.profile_dir(course) / got["page"])
            cands = [str(x) for x in meta.get("video_candidates") or []] or cands
            if study.cfg().get("video_cmd") and cands:
                steps["video"] = "running"
                set_job(course, sid, stage="video", steps=steps)
                key = f"{unit['thread']}:video"
                study.JOBS[key] = {"status": "running", "stage": "script", "concept": cands[0], "started": chat.now_iso()}
                await study.video_job(key, unit, cands[0])
                steps["video"] = "done" if study.JOBS.get(key, {}).get("status") == "done" else "error"
            else:
                steps["video"] = "skipped"
        bad = [k for k, v in steps.items() if v == "error"]
        # 先记那行灰字再标写好：学习台一看到写好就重读对话，要能读到它
        try:
            await asyncio.to_thread(announce_done, course, sid)
        except Exception as e:  # noqa: BLE001 — 没记上不影响这一节算写好
            print(f"[study] 写好一节的那行没记上：{type(e).__name__}: {e}")
        set_job(course, sid, status="done", stage=None, steps=steps, finished=cf.now_iso(),
                error=L(f"{'、'.join(bad)} 没生成成，可以在学习台里再点一次", f"{', '.join(bad)} failed; try again from the desk") if bad else None)
    except (llmjson.LLMError, ValueError, OSError, RuntimeError) as e:
        steps = {k: ("error" if v in ("queued", "running") and k == "page" else v) for k, v in steps.items()}
        set_job(course, sid, status="error", stage=None, steps=steps, finished=cf.now_iso(), error=str(getattr(e, "detail", None) or e)[:300])
    except Exception as e:  # noqa: BLE001 — 这一节失败不影响队列里的下一节
        set_job(course, sid, status="error", stage=None, steps=steps, finished=cf.now_iso(), error=str(e)[:300])


def announce_done(course: str, sid: str) -> None:
    """写好一节：学习 Agent 的对话里记一行灰字；server.json 的 study.notify_generated 开着才静音推一条。"""
    c = cf.load(course)
    s = cf.session_by(c, sid) if c else None
    agent = chat.study_agent()
    if not c or not s:
        return
    text = LS(f"学习台：「{c['title']}」S{s['n']} {s['topic']} 的学习页和学习路线写好了", f"Study desk: notes and study path ready for {c['title']} S{s['n']} {s['topic']}")
    if agent:
        with chat._lock, chat.db() as conn:
            conn.execute("INSERT INTO messages(thread, role, text, model, ts, status, origin) VALUES(?,?,?,?,?,?,?)",
                         (agent, "auto", text, None, chat.now_iso(), "ok", "auto"))
    if study.cfg().get("notify_generated"):
        import push
        target = {"type": "study", "course": course, "page": s.get("page"), "session": sid}
        spawn_sync(push.send_push(settings.app_name, text, {"target": target}, thread_id=agent, level="quiet", collapse=f"study:{course}:{sid}", kind="done"))


def spawn_sync(coro) -> None:
    """从线程里（asyncio.to_thread）排一个协程到服务的事件循环。"""
    loop = _loop
    if loop and loop.is_running():
        asyncio.run_coroutine_threadsafe(coro, loop)
    else:
        coro.close()


_loop: asyncio.AbstractEventLoop | None = None


async def worker() -> None:
    global _loop
    _loop = asyncio.get_running_loop()
    assert _queue is not None
    while True:
        course, sid, opts = await _queue.get()
        try:
            await gen_session(course, sid, opts)
        finally:
            _queue.task_done()


def start() -> None:
    """服务启动时（main.py 的 lifespan）：记下事件循环，线程里要推送时用。"""
    global _loop
    _loop = asyncio.get_running_loop()


def enqueue(course: str, sids: list[str], opts: dict) -> dict:
    """排进生成队列（一次一节）。已经在排或在写的跳过。"""
    global _queue, _worker, _loop
    if _queue is None:
        _queue = asyncio.Queue()
    if _worker is None or _worker.done():
        _loop = asyncio.get_running_loop()
        _worker = asyncio.create_task(worker())
    jobs = job_view(course)
    added = []
    for sid in sids:
        if (jobs.get(sid) or {}).get("status") in ("queued", "running"):
            continue
        steps = {"page": "queued", "path": "queued"} | {k: "queued" for k in ("cards", "quiz", "video") if opts.get(k)}
        set_job(course, sid, status="queued", stage=None, steps=steps, queued=cf.now_iso(), error=None, opts=opts)
        _queue.put_nowait((course, sid, dict(opts)))
        added.append(sid)
    return {"queued": added, "jobs": job_view(course)}


def running() -> bool:
    return bool(_live)


def eta_note() -> str:
    return L("一节大约 3–6 分钟；写好一节在学习 Agent 的对话里说一声。关掉这个页面也会接着写。",
             "About 3–6 minutes per session; the study Agent's chat gets a line when each is done. It keeps going if you close this page.")
