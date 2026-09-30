"""课程档案（2026-09-30）：学习台里每门课一个 course.json，从零加一门课的五步和学习 Agent 改课都读写它。

放在 pages/<课>/course.json（没配 pages 就是 <data_dir>/study/<课>/course.json）。没有档案的课（手动整理的文件夹、以前的课）照旧能用，
有档案的课多出：每一节的日期、主题、要读的、作业和考试，按文件名归节、查齐、生成学习页。
  name        文件夹名（课的 id，建好不改）；title 显示的名字；code 缩写（截止表、日程里的小标）
  term / exam / learn / notes / style   学期、考试怎么考、喜欢怎么学、还想说的、学习页的写法（每次生成都带上）
  platform / have / extras               课程网站（canvas / moodle / blackboard / other）、手上有什么、还有别的材料
  syllabus    大纲：{file | url, read_at, summary}；canvas：{base, course_id, synced_at}（令牌不在这里，见 canvasapi.py）
  sessions    每一节 {id, n, date, time, week, topic, kind, readings: [{id, title, required, file, skip, note, url, kind, chapter}],
              folder（materials/<课>/ 下的文件夹）, page（学习页文件名）, removed（删掉的节：留着，能撤销）}
  deadlines   作业和考试 {id, title, due: "YYYY-MM-DD HH:MM" 或 "YYYY-MM-DD", kind, session, done, weight}
  questions   读大纲时拿不准、要你定的 {id, session, field, text, options}
  setup       向导做到哪 {step, confirmed}；remind_ready：新的一节材料齐了提醒（要服务器允许，见 courses.py）
每一节的编号 n 显式存着：加一节、删一节时后面的顺延（学习页的 session、视频文件名的 S08 跟着改，见 reconcile）。
这里只有数据和计算；接口在 courses.py，生成在 coursegen.py。
"""
from __future__ import annotations

import copy
import json
import re
import threading
import unicodedata
import uuid
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path

from config import settings
from i18n import L

COURSE_FILE = "course.json"
INFO_FOLDER = "00 Course info"      # 大纲、教材、作业说明这类整门课的资料
INCOMING = ".incoming"              # 传上来还没归到哪一节的
EXAM_KEYS = ("closed", "open", "essay", "group", "present", "unknown")
LEARN_KEYS = ("zh", "intuition", "examples", "video", "practice")
PLATFORMS = ("canvas", "moodle", "blackboard", "other")
SESSION_KINDS = ("lecture", "seminar", "case", "guest", "review", "workshop", "other")
DDL_KINDS = ("assignment", "group", "presentation", "exam", "classwork", "quiz", "other")
READING_KINDS = ("textbook", "article", "case", "chapter", "web", "video", "other")
CAPTION_EXT = {".vtt", ".srt"}
MATERIAL_EXT = {".pdf", ".pptx", ".ppt", ".docx", ".doc", ".key", ".xlsx", ".xls", ".csv", ".md", ".txt", ".rmd", ".r", ".py", ".ipynb",
                ".tex", ".html", ".htm", ".rtf", ".odt", ".odp", ".epub", ".json", ".zip", ".vtt", ".srt", ".png", ".jpg", ".jpeg", ".mp4"}
MAX_ZIP_FILES, MAX_ZIP_BYTES = 300, 800 * 1024 * 1024
_lock = threading.RLock()   # 整个 course.json 的读改写（按课分开没必要：改动都很小）


def now_iso() -> str:
    return datetime.now(settings.tz).isoformat(timespec="seconds")


def today() -> date:
    return datetime.now(settings.tz).date()


def new_id(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:6]}"


def ddl_label(kind: str | None) -> str:
    return {"assignment": L("作业", "Assignment"), "group": L("小组作业", "Group work"), "presentation": L("展示", "Presentation"),
            "exam": L("考试", "Exam"), "classwork": L("课堂练习", "Class exercise"), "quiz": L("小测", "Quiz")}.get(kind or "", L("截止", "Due"))


def ddl_label_zh(kind: str | None) -> str:
    """学习 Agent 截止表「类型」那一列的中文选项（以前的表是中文选项）。"""
    return {"assignment": "作业", "group": "小组作业", "presentation": "展示", "exam": "考试", "classwork": "课堂练习", "quiz": "小测"}.get(kind or "", "作业")


# —— 名字和路径 ——

BAD_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]')


def clean_name(raw: str | None, limit: int = 60) -> str:
    """课名 → 文件夹名：去掉路径和文件名里不能用的字符、首尾空白和点；不能以 . 或 _ 开头（那是藏起来的）。"""
    s = unicodedata.normalize("NFC", str(raw or ""))
    s = BAD_CHARS.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip().strip(".").strip()
    s = s.lstrip("._").strip()
    return s[:limit].strip()


def folder_part(raw: str | None, limit: int = 50) -> str:
    """一节的主题 → 文件夹名里的那一段：冒号写成「_ 」（和课程网站镜像的写法一样，学习台显示时换回冒号）。"""
    s = unicodedata.normalize("NFC", str(raw or "")).replace(":", "_ ").replace("：", "_ ")
    s = BAD_CHARS.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip().strip(".")[:limit].strip()


def session_folder_name(n: int, topic: str) -> str:
    part = folder_part(topic)
    return f"{n:02d} Session {n}" + (f"_ {part}" if part else "")


def course_root(course: str) -> Path | None:
    import study  # 延迟导入：study 也 import 本模块
    mat = study.root("materials")
    return mat / course if mat else None


def profile_dir(course: str) -> Path:
    import study
    return study.pages_dir(course) or settings.data_dir / "study" / course


def profile_path(course: str) -> Path:
    return profile_dir(course) / COURSE_FILE


def profiled() -> list[str]:
    """有档案的课（按建课时间排）。"""
    import study
    bases = [b for b in (study.root("pages"), settings.data_dir / "study") if b]
    found: dict[str, str] = {}
    for base in bases:
        if not base.is_dir():
            continue
        for d in base.iterdir():
            f = d / COURSE_FILE
            if d.is_dir() and f.is_file() and d.name not in found:
                try:
                    found[d.name] = str(json.loads(f.read_text(encoding="utf8")).get("created") or "")
                except (OSError, ValueError):
                    found[d.name] = ""
    return sorted(found, key=lambda n: (found[n], n))


def load(course: str) -> dict | None:
    try:
        data = json.loads(profile_path(course).read_text(encoding="utf8"))
    except (OSError, ValueError):
        return None
    return normalize(data, course) if isinstance(data, dict) else None


def save(course: str, data: dict) -> dict:
    """写回（临时文件 + 换名）。data 先规整一遍：编号、排序、缺的字段。"""
    data = normalize(data, course)
    data["updated"] = now_iso()
    p = profile_path(course)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{uuid.uuid4().hex[:6]}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf8")
    tmp.replace(p)
    return data


def blank(course: str, title: str | None = None) -> dict:
    ts = now_iso()
    return {"v": 1, "name": course, "title": title or course, "code": short_code(title or course), "term": "", "exam": [], "learn": [],
            "notes": "", "style": "", "platform": "", "have": {}, "extras": [], "syllabus": None, "canvas": None, "sessions": [],
            "deadlines": [], "questions": [], "setup": {"step": 1, "confirmed": False}, "remind_ready": False, "created": ts, "updated": ts}


def short_code(name: str) -> str:
    """课名的缩写：Behavioural Economics → BE；中文名取前四个字；本来就短的原样。"""
    name = (name or "").strip()
    words = re.findall(r"[A-Za-z]+", name)
    if len(words) >= 2:
        stop = {"and", "of", "the", "in", "for", "to", "a", "an", "&"}
        caps = "".join(w[0].upper() for w in words if w.lower() not in stop)
        return caps[:5] or name[:6]
    if words:
        return words[0][:8]
    return name[:4]


def str_list(v, allowed: tuple[str, ...] | None = None, limit: int = 20) -> list[str]:
    out = []
    for x in v if isinstance(v, list) else []:
        s = str(x or "").strip()
        if s and (allowed is None or s in allowed) and s not in out:
            out.append(s[:80])
    return out[:limit]


def norm_date(v) -> str | None:
    """「2026-10-05」「2026/10/5」→ YYYY-MM-DD；看不懂 = None。"""
    s = str(v or "").strip()
    m = re.match(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", s)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
    except ValueError:
        return None


def norm_time(v) -> str:
    m = re.match(r"^\s*(\d{1,2})[:：.](\d{2})", str(v or ""))
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        return ""
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def norm_due(v) -> str | None:
    """截止：「YYYY-MM-DD HH:MM」或只有日期「YYYY-MM-DD」。"""
    d = norm_date(v)
    if not d:
        return None
    t = norm_time(str(v)[10:]) if len(str(v)) > 10 else ""
    return f"{d} {t}" if t else d


def norm_reading(r, sid: str = "") -> dict | None:
    if isinstance(r, str):
        r = {"title": r}
    if not isinstance(r, dict) or not str(r.get("title") or "").strip():
        return None
    kind = str(r.get("kind") or "").strip()
    ch = r.get("chapter")
    return {"id": str(r.get("id") or new_id("r-")), "title": str(r["title"]).strip()[:300], "required": r.get("required") is not False,
            "file": str(r["file"]) if isinstance(r.get("file"), str) and r["file"] else None, "skip": bool(r.get("skip")),
            "note": str(r.get("note") or "").strip()[:300], "url": str(r["url"]).strip()[:500] if isinstance(r.get("url"), str) and r["url"].startswith(("http://", "https://")) else None,
            "kind": kind if kind in READING_KINDS else ("textbook" if re.search(r"教材|textbook|第\s*\d+\s*章|\bch(apter)?\.?\s*\d", str(r["title"]), re.I) else "other"),
            "chapter": ch if isinstance(ch, (int, str)) and str(ch).strip() else None}


def norm_session(s: dict) -> dict | None:
    if not isinstance(s, dict):
        return None
    try:
        n = int(s.get("n"))
    except (TypeError, ValueError):
        n = 0
    kind = str(s.get("kind") or "lecture")
    week = s.get("week")
    try:
        week = int(week) if week not in (None, "") else None
    except (TypeError, ValueError):
        week = None
    sid = str(s.get("id") or new_id("s-"))
    return {"id": sid, "n": n, "date": norm_date(s.get("date")), "time": norm_time(s.get("time")), "week": week,
            "topic": str(s.get("topic") or "").strip()[:160], "kind": kind if kind in SESSION_KINDS else "other",
            "readings": [x for x in (norm_reading(r, sid) for r in s.get("readings") or []) if x],
            "folder": str(s["folder"]) if isinstance(s.get("folder"), str) and s["folder"] else None,
            "page": str(s["page"]) if isinstance(s.get("page"), str) and s["page"] else None,
            "note": str(s.get("note") or "").strip()[:300], "removed": bool(s.get("removed"))}


def norm_deadline(d: dict) -> dict | None:
    if not isinstance(d, dict) or not str(d.get("title") or "").strip():
        return None
    kind = str(d.get("kind") or "assignment")
    return {"id": str(d.get("id") or new_id("d-")), "title": str(d["title"]).strip()[:160], "due": norm_due(d.get("due")),
            "kind": kind if kind in DDL_KINDS else "other", "session": str(d["session"]) if d.get("session") else None,
            "done": bool(d.get("done")), "weight": str(d.get("weight") or "").strip()[:30], "note": str(d.get("note") or "").strip()[:300],
            "url": str(d["url"])[:500] if isinstance(d.get("url"), str) and d["url"].startswith(("http://", "https://")) else None,
            "removed": bool(d.get("removed"))}


def normalize(data: dict, course: str) -> dict:
    out = blank(course)
    out.update({k: v for k, v in data.items() if k in out or k in ("v",)})
    out["name"] = course
    out["title"] = str(out.get("title") or course).strip()[:120] or course
    out["code"] = str(out.get("code") or short_code(out["title"])).strip()[:10]
    out["exam"] = str_list(out.get("exam"), EXAM_KEYS)
    out["learn"] = str_list(out.get("learn"), LEARN_KEYS)
    out["platform"] = out["platform"] if out.get("platform") in PLATFORMS else ""
    out["have"] = {k: bool(v) for k, v in (out.get("have") or {}).items() if k in ("syllabus", "files", "site", "none")} if isinstance(out.get("have"), dict) else {}
    ex = []
    for x in out.get("extras") or []:
        if isinstance(x, dict) and str(x.get("label") or "").strip():
            ex.append({"key": str(x.get("key") or "custom")[:20], "label": str(x["label"]).strip()[:60]})
    out["extras"] = ex[:20]
    for k in ("term", "notes", "style"):
        out[k] = str(out.get(k) or "").strip()[:2000]
    ss = [x for x in (norm_session(s) for s in out.get("sessions") or []) if x]
    seen = set()
    for s in ss:
        while s["id"] in seen:
            s["id"] = new_id("s-")
        seen.add(s["id"])
    # 没编号的（刚从大纲里读出来的、手动加的）按先后补号
    top = max([s["n"] for s in ss if s["n"] > 0] or [0])
    for s in ss:
        if s["n"] <= 0:
            top += 1
            s["n"] = top
    ss.sort(key=lambda s: (s["removed"], s["n"], s["date"] or "9999", s["id"]))
    out["sessions"] = ss
    dd = [x for x in (norm_deadline(d) for d in out.get("deadlines") or []) if x]
    dd.sort(key=lambda d: (d["removed"], d["due"] or "9999", d["title"]))
    out["deadlines"] = dd
    qq = []
    for q in out.get("questions") or []:
        if isinstance(q, dict) and str(q.get("text") or "").strip():
            qq.append({"id": str(q.get("id") or new_id("q-")), "session": str(q["session"]) if q.get("session") else None,
                       "field": str(q.get("field") or "")[:20], "text": str(q["text"]).strip()[:300],
                       "options": [str(o).strip()[:80] for o in q.get("options") or [] if str(o).strip()][:6]})
    out["questions"] = qq
    st = out.get("setup") if isinstance(out.get("setup"), dict) else {}
    out["setup"] = {"step": max(1, min(5, int(st.get("step") or 1))), "confirmed": bool(st.get("confirmed"))}
    out["remind_ready"] = bool(out.get("remind_ready"))
    return out


def live_sessions(c: dict) -> list[dict]:
    return [s for s in c["sessions"] if not s["removed"]]


def live_deadlines(c: dict) -> list[dict]:
    return [d for d in c["deadlines"] if not d["removed"]]


def session_by(c: dict, ref) -> dict | None:
    """按 id 或节号（3、S3、s3）找一节。"""
    ref = str(ref or "").strip()
    for s in live_sessions(c):
        if s["id"] == ref:
            return s
    m = re.fullmatch(r"[sS]?\s*(\d{1,3})", ref)
    if m:
        return next((s for s in live_sessions(c) if s["n"] == int(m.group(1))), None)
    return None


# —— 加一节、删一节（后面顺延）——

def insert_session(c: dict, sess: dict, after: str | None = None) -> dict:
    """插一节：给了 after（id 或节号）就放在它后面；有日期就按日期放；都没有放最后。后面的节号 +1。→ 新的这一节。"""
    live = live_sessions(c)
    s = norm_session({**sess, "n": 0})
    assert s is not None
    if after is not None and (a := session_by(c, after)):
        pos = a["n"] + 1
    elif s["date"]:
        later = [x for x in live if x["date"] and x["date"] > s["date"]]
        pos = min((x["n"] for x in later), default=(max((x["n"] for x in live), default=0) + 1))
    else:
        pos = max((x["n"] for x in live), default=0) + 1
    for x in live:
        if x["n"] >= pos:
            x["n"] += 1
    s["n"] = pos
    c["sessions"].append(s)
    c["sessions"].sort(key=lambda x: (x["removed"], x["n"]))
    return s


def remove_session(c: dict, sid: str) -> dict:
    """删一节：留在档案里（removed，能撤销），后面的节号 -1；挂在这一节的截止解开。"""
    s = session_by(c, sid)
    if not s:
        raise KeyError(sid)
    n = s["n"]
    s["removed"] = True
    for x in live_sessions(c):
        if x["n"] > n:
            x["n"] -= 1
    for d in c["deadlines"]:
        if d["session"] == s["id"]:
            d["session"] = None
    return s


def renumber_map(before: dict, after: dict) -> dict[str, tuple[int, int]]:
    b = {s["id"]: s["n"] for s in live_sessions(before)}
    return {s["id"]: (b[s["id"]], s["n"]) for s in live_sessions(after) if s["id"] in b and b[s["id"]] != s["n"]}


# —— 撤销：按「一样东西」三方比较（档案字段、每一节、每个截止、每个问题）——

PROFILE_KEYS = ("title", "code", "term", "exam", "learn", "notes", "style", "platform", "have", "extras", "syllabus", "canvas", "setup", "remind_ready")
_MISSING = object()


def entities(c: dict) -> dict:
    m: dict = {("f", k): c.get(k) for k in PROFILE_KEYS}
    for s in c.get("sessions") or []:
        m[("s", s["id"])] = s
    for d in c.get("deadlines") or []:
        m[("d", d["id"])] = d
    for q in c.get("questions") or []:
        m[("q", q["id"])] = q
    return m


def revert(cur: dict, before: dict, after: dict) -> tuple[dict, list]:
    """把 after 相对 before 的改动从 cur 上撤掉。只撤改动以后没再被改过的；有被改过的 → (原样, 冲突列表)，一样都不撤。"""
    B, A, C = entities(before), entities(after), entities(cur)
    todo, conflicts = [], []
    for key in set(B) | set(A):
        b, a = B.get(key, _MISSING), A.get(key, _MISSING)
        if b == a:
            continue
        if C.get(key, _MISSING) != a:
            conflicts.append(key)
        else:
            todo.append((key, b))
    if conflicts:
        return cur, conflicts
    out = copy.deepcopy(cur)
    for (kind, k), val in todo:
        if kind == "f":
            out[k] = copy.deepcopy(val) if val is not _MISSING else None
            continue
        field = {"s": "sessions", "d": "deadlines", "q": "questions"}[kind]
        rest = [x for x in out.get(field) or [] if x["id"] != k]
        if val is not _MISSING:
            rest.append(copy.deepcopy(val))
        out[field] = rest
    return out, []


def diff_marks(before: dict, after: dict) -> dict:
    """标黄用：这次改动动了哪些节的哪些字段、哪些截止。{"sessions": {id: [字段…] 或 ["new"]}, "deadlines": {id: [...]}, "removed": [节 id]}。"""
    out: dict = {"sessions": {}, "deadlines": {}, "removed": []}
    bs = {s["id"]: s for s in before.get("sessions") or []}
    for s in after.get("sessions") or []:
        o = bs.get(s["id"])
        if s["removed"]:
            if o and not o["removed"]:
                out["removed"].append(s["id"])
            continue
        if not o or o["removed"]:
            out["sessions"][s["id"]] = ["new"]
            continue
        f = [k for k in ("n", "date", "time", "topic", "readings", "kind", "folder", "note") if s.get(k) != o.get(k)]
        if f:
            out["sessions"][s["id"]] = f
    bd = {d["id"]: d for d in before.get("deadlines") or []}
    for d in after.get("deadlines") or []:
        o = bd.get(d["id"])
        if d["removed"]:
            continue
        if not o or o["removed"]:
            out["deadlines"][d["id"]] = ["new"]
            continue
        f = [k for k in ("title", "due", "kind", "session", "done") if d.get(k) != o.get(k)]
        if f:
            out["deadlines"][d["id"]] = f
    return out


# —— 文件：每一节的文件夹、按文件名归节 ——

def is_hidden(p: Path) -> bool:
    return p.name.startswith(".")


def list_files(folder: Path | None) -> list[Path]:
    if not folder or not folder.is_dir():
        return []
    return sorted(f for f in folder.iterdir() if f.is_file() and not is_hidden(f))


def session_dir(course: str, s: dict) -> Path | None:
    base = course_root(course)
    return base / s["folder"] if base and s.get("folder") else None


def ensure_folder(course: str, s: dict) -> Path:
    """这一节还没有文件夹就建一个（NN Session N_ 主题），记进 s["folder"]。"""
    base = course_root(course)
    if not base:
        raise RuntimeError(L("还没配置课件目录（server.json 的 study.materials）", "No materials directory configured (study.materials in server.json)"))
    if s.get("folder") and (base / s["folder"]).is_dir():
        return base / s["folder"]
    name = s.get("folder") or session_folder_name(s["n"], s["topic"])
    d = base / name
    k = 2
    while d.exists() and not d.is_dir():
        d = base / f"{name} ({k})"
        k += 1
    d.mkdir(parents=True, exist_ok=True)
    s["folder"] = d.name
    return d


WORD = re.compile(r"[A-Za-z][A-Za-z'’-]{2,}|\d{4}")
STOP = {"the", "and", "for", "with", "from", "into", "about", "chapter", "reading", "readings", "week", "session", "lecture", "slides", "slide",
        "notes", "note", "part", "print", "full", "copy", "final", "draft", "version", "pdf", "pptx", "docx", "class", "course", "module", "intro",
        "introduction", "theory", "analysis", "economics", "business", "management", "study", "paper", "article", "case", "review", "journal",
        "textbook", "book", "handout", "exercise", "exercises", "problem", "problems", "set", "sheet", "solutions", "solution", "answers"}


def words_of(text: str) -> set[str]:
    return {w.lower().strip("'’-") for w in WORD.findall(text or "") if w.lower() not in STOP}


NUM_RULES = (
    (re.compile(r"(?:^|[^a-z])(?:session|sess|lecture|lect|lec|seminar|topic|unit)[\s_\-.]*0*(\d{1,2})(?!\d)", re.I), "session"),
    (re.compile(r"(?:^|[^a-z])(?:week|wk)[\s_\-.]*0*(\d{1,2})(?!\d)", re.I), "week"),
    (re.compile(r"第\s*0*(\d{1,2})\s*[讲课节次]"), "session"),
    (re.compile(r"第\s*0*(\d{1,2})\s*周"), "week"),
    (re.compile(r"^(?:L|S)0*(\d{1,2})(?![\d])", re.I), "session"),
    (re.compile(r"^(?:W)0*(\d{1,2})(?![\d])", re.I), "week"),
)


def weeks_of(c: dict) -> dict[str, int]:
    """每一节是第几周：大纲写了 week 就用它；没写但有日期的，从第一节所在的那一周算起。"""
    live = live_sessions(c)
    out = {s["id"]: s["week"] for s in live if s["week"]}
    dated = [s for s in live if s["date"]]
    if dated:
        first = min(date.fromisoformat(s["date"]) for s in dated)
        monday = first - timedelta(days=first.weekday())
        for s in dated:
            out.setdefault(s["id"], (date.fromisoformat(s["date"]) - monday).days // 7 + 1)
    return out


def reading_match(c: dict, name: str) -> list[tuple[dict, dict]]:
    """文件名像哪篇阅读（作者、年份、标题里的词对上两个以上）→ [(节, 阅读)]，按对上的多少排。"""
    fw = words_of(Path(name).stem.replace("_", " "))
    if not fw:
        return []
    hits = []
    for s in live_sessions(c):
        for r in s["readings"]:
            rw = words_of(r["title"])
            k = len(fw & rw)
            years = {w for w in fw & rw if w.isdigit()}
            if k >= 2 or (k >= 1 and years and len(fw & rw) >= 2):
                hits.append((k, s, r))
    hits.sort(key=lambda x: -x[0])
    return [(s, r) for _, s, r in hits]


def topic_match(c: dict, name: str) -> list[dict]:
    fw = words_of(Path(name).stem.replace("_", " "))
    out = []
    for s in live_sessions(c):
        k = len(fw & words_of(s["topic"]))
        if k >= 2 or (k == 1 and any(len(w) >= 7 for w in fw & words_of(s["topic"]))):
            out.append((k, s))
    out.sort(key=lambda x: -x[0])
    return [s for k, s in out if k == out[0][0]] if out else []


def classify(c: dict, name: str) -> dict:
    """一个文件该归到哪一节。→ {"session": id | None, "reading": id | None, "reason": 说明, "options": [id…]（拿不准时的候选）, "course_info": bool}。
    规则：阅读清单对得上 → 那一节（记成这篇阅读）；文件名里有 Session / Lecture / 第 N 讲 → 那一节；Week N → 那一周（一周两节就问）；
    大纲、课程说明、作业说明这类 → 整门课的资料；都没有就看主题词；还是拿不准 → 问你。"""
    stem = Path(name).stem
    low = stem.lower()
    live = live_sessions(c)
    by_n = {s["n"]: s for s in live}
    res: dict = {"session": None, "reading": None, "reason": "", "options": [], "course_info": False}
    rm = reading_match(c, name)
    if rm and (len(rm) == 1 or rm[0][1]["id"] != rm[1][1]["id"]):
        s, r = rm[0]
        return {**res, "session": s["id"], "reading": r["id"], "reason": L(f"对上了阅读「{r['title'][:40]}」", f'Matches the reading "{r["title"][:40]}"')}
    if re.search(r"syllabus|course[\s_-]*(outline|guide|handbook|info)|module[\s_-]*(guide|handbook|outline)|assessment|brief|大纲|课程说明", low):
        return {**res, "course_info": True, "reason": L("像是整门课的资料", "Looks like course-wide material")}
    for rx, what in NUM_RULES:
        m = rx.search(stem)
        if not m:
            continue
        num = int(m.group(1))
        if what == "session":
            if num in by_n:
                return {**res, "session": by_n[num]["id"], "reason": L(f"文件名里是第 {num} 节", f"The name says session {num}")}
            res["reason"] = L(f"文件名写的是第 {num} 节，这门课没有这一节", f"The name says session {num}, which this course doesn't have")
            break
        wk = weeks_of(c)
        cands = [s for s in live if wk.get(s["id"]) == num]
        tm_all = topic_match(c, name)
        other = [s for s in tm_all if s not in cands]
        if len(cands) == 1 and not other:
            return {**res, "session": cands[0]["id"], "reason": L(f"文件名里是第 {num} 周", f"The name says week {num}")}
        if cands:
            tm = [s for s in tm_all if s in cands]
            if len(tm) == 1:
                return {**res, "session": tm[0]["id"], "reason": L(f"第 {num} 周，主题对得上", f"Week {num}, and the topic matches")}
            if len(other) == 1:  # 周数和主题说的不是同一节：主题对上的放在第一个，你来挑
                o = other[0]
                return {**res, "options": [o["id"]] + [s["id"] for s in cands],
                        "reason": L(f"文件名写的第 {num} 周，主题却像 S{o['n']}「{o['topic'][:24]}」，归哪一节？",
                                    f'The name says week {num}, but the topic looks like S{o["n"]} "{o["topic"][:24]}": which one?')}
            return {**res, "options": [s["id"] for s in cands], "reason": L(f"第 {num} 周有 {len(cands)} 节，归哪一节？", f"Week {num} has {len(cands)} sessions: which one?")}
        res["reason"] = L(f"文件名写的是第 {num} 周，对不上哪一节", f"The name says week {num}, which doesn't match a session")
        break
    tm = topic_match(c, name)
    if len(tm) == 1:
        return {**res, "session": tm[0]["id"], "reason": L(f"主题对得上「{tm[0]['topic'][:30]}」", f'Topic matches "{tm[0]["topic"][:30]}"')}
    if tm:
        return {**res, "options": [s["id"] for s in tm], "reason": L("主题对得上好几节，归哪一节？", "Several sessions match the topic: which one?")}
    if not res["reason"]:
        res["reason"] = L("认不出是哪一节", "Couldn't tell which session")
    return res


def safe_extract(zpath: Path, dest: Path) -> list[Path]:
    """解 zip：只要文件，不要 __MACOSX、隐藏文件、越界路径；最多 300 个、解开后最多 800 MB；GBK 文件名修好。"""
    out: list[Path] = []
    total = 0
    with zipfile.ZipFile(zpath) as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            name = info.filename
            if not info.flag_bits & 0x800:  # 没标 UTF-8 的：多半是 Windows 中文的 GBK
                try:
                    name = name.encode("cp437").decode("gbk")
                except (UnicodeEncodeError, UnicodeDecodeError):
                    pass
            parts = [p for p in re.split(r"[\\/]+", name) if p not in ("", ".", "..")]
            if not parts or parts[0] == "__MACOSX" or any(p.startswith(".") for p in parts):
                continue
            base = BAD_CHARS.sub(" ", parts[-1]).strip() or "file"
            if Path(base).suffix.lower() == ".zip":
                continue  # zip 里的 zip 不再拆
            total += info.file_size
            if len(out) >= MAX_ZIP_FILES or total > MAX_ZIP_BYTES:
                raise ValueError(L("zip 太大了：最多 300 个文件、解开后 800 MB", "The zip is too big: at most 300 files and 800 MB unpacked"))
            target = unique(dest / base)
            with z.open(info) as src, target.open("wb") as dst:
                while chunk := src.read(1024 * 1024):
                    dst.write(chunk)
            out.append(target)
    return out


def unique(p: Path) -> Path:
    """同名就加 (2)、(3)…"""
    if not p.exists():
        return p
    k = 2
    while True:
        q = p.with_name(f"{p.stem} ({k}){p.suffix}")
        if not q.exists():
            return q
        k += 1


# —— 查齐 ——

def reading_state(course: str, r: dict) -> str:
    """have 到手 / skipped 明着跳过 / missing 还没有。教材的章节：整本教材在课程资料里就算到手。"""
    base = course_root(course)
    if r.get("file") and base and (base / r["file"]).is_file():
        return "have"
    if r.get("skip"):
        return "skipped"
    return "missing"


def session_check(course: str, c: dict, s: dict, today_: date | None = None) -> dict:
    """一节的材料：课件（除了阅读、字幕以外的文件）、阅读几篇到手、有没有录播字幕 → 状态：
    ready 齐了 / missing 缺阅读 / noslides 过了上课时间还没有课件 / later 还没上课 / empty 什么都没有（没有日期）。"""
    t = today_ or today()
    base = course_root(course)
    files = list_files(session_dir(course, s))
    rel = [str(f.relative_to(base)) for f in files] if base else []
    reading_files = {r["file"] for r in s["readings"] if r.get("file")}
    caps = [p for p in rel if Path(p).suffix.lower() in CAPTION_EXT]
    slides = [p for p in rel if p not in reading_files and p not in caps]
    readings = [{**r, "state": reading_state(course, r)} for r in s["readings"]]
    req = [r for r in readings if r["required"]]
    missing = [r for r in req if r["state"] == "missing"]
    past = bool(s["date"]) and date.fromisoformat(s["date"]) <= t + timedelta(days=1)
    if missing and (slides or past):
        status = "missing"
    elif slides:
        status = "ready" if not missing else "missing"
    elif s["date"] and not past:
        status = "later"
    elif s["date"]:
        status = "noslides"
    else:
        status = "empty"
    return {"id": s["id"], "n": s["n"], "topic": s["topic"], "date": s["date"], "status": status, "slides": slides, "captions": caps,
            "readings": readings, "have": sum(r["state"] == "have" for r in readings), "total": len(readings),
            "missing": [{"id": r["id"], "title": r["title"], "url": r.get("url")} for r in missing],
            "skipped": sum(r["state"] == "skipped" for r in readings), "page": s.get("page")}


# —— 录播字幕（.vtt / .srt）——

def parse_captions(path: Path) -> list[dict]:
    """WebVTT / SRT → [{t: 秒, text}]。"""
    try:
        text = path.read_text(encoding="utf8", errors="replace")
    except OSError:
        return []
    out = []
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n")):
        m = re.search(r"(\d{1,2}):(\d{2}):(\d{2})[.,]\d{1,3}\s*-->|(\d{1,2}):(\d{2})[.,]\d{1,3}\s*-->", block)
        if not m:
            continue
        if m.group(1) is not None:
            t = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
        else:
            t = int(m.group(4)) * 60 + int(m.group(5))
        lines = block[m.end():].split("\n")[1:]
        words = " ".join(re.sub(r"<[^>]+>", "", ln).strip() for ln in lines if ln.strip())
        if words:
            out.append({"t": float(t), "text": words})
    return out


# —— 截止 ——

def course_deadlines(include_done: bool = False) -> list[dict]:
    """所有有档案的课的作业和考试（没删的、有日期的），和 deadlines_cmd 打印的一个样子：{due, course, title, url, id, session, kind, source}。"""
    out = []
    for course in profiled():
        c = load(course)
        if not c or not c["setup"]["confirmed"]:
            continue  # 核对完才进截止表和日程
        by_id = {s["id"]: s for s in c["sessions"]}
        for d in live_deadlines(c):
            if not d["due"] or (d["done"] and not include_done):
                continue
            due = d["due"] if len(d["due"]) > 10 else f"{d['due']} 23:59"
            s = by_id.get(d["session"] or "")
            out.append({"due": due, "course": c["title"], "course_id": course, "code": c["code"], "title": d["title"], "url": d.get("url"),
                        "id": d["id"], "session": s["n"] if s and not s["removed"] else None, "session_id": d["session"],
                        "kind": d["kind"], "done": d["done"], "source": "course"})
    return sorted(out, key=lambda x: x["due"])


def same_deadline(a: dict, b: dict) -> bool:
    """课程网站同步来的和大纲里写的是同一个：同一门课、截止时间一样（或者只写了日期、日子一样）。"""
    ca, cb = str(a.get("course") or "").lower(), str(b.get("course") or "").lower()
    if ca and cb and ca != cb and not (ca in cb or cb in ca):
        return False
    da, db = str(a.get("due") or ""), str(b.get("due") or "")
    return bool(da and db) and (da[:16] == db[:16] or (da[:10] == db[:10] and (da.endswith("23:59") or db.endswith("23:59"))))
