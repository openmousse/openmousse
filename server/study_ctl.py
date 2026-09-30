#!/usr/bin/env python3
"""学习台的课：学习 Agent 在命令行里读、改（2026-09-30）。走服务的 HTTP 接口（/api/study/courses/*）。

读
  python3 study_ctl.py courses                                   # 有哪些课（课名、缩写、几节、核对完没有）
  python3 study_ctl.py show <课> [--json]                         # 每一节：节号、日期、主题、要读的、材料齐没齐；截止；要问的；待归节的文件
  python3 study_ctl.py check <课>                                 # 每一节缺什么
  python3 study_ctl.py log <课>                                   # 最近的改动（改动号，撤销用）
改（能撤销的直接改；Agent 改的回复下面出一张「改了课程结构」的卡，用户点得了撤销）
  python3 study_ctl.py session add <课> --topic "嘉宾讲座 Guest lecture" [--date 2026-10-27] [--time 10:00] [--after 7] [--reading "…"]…
  python3 study_ctl.py session update <课> <节> [--topic …] [--date YYYY-MM-DD] [--time HH:MM] [--note …]
  python3 study_ctl.py session move <课> <节> --after <节>        # 挪到某一节后面（节号跟着顺延）
  python3 study_ctl.py session remove <课> <节>                   # 去掉一节，后面的顺延（文件夹和学习页留着，撤销就回来）
  python3 study_ctl.py reading set <课> <节> "教材第 6 章" ["另一篇"]…  # 整个换掉这一节要读的
  python3 study_ctl.py reading add <课> <节> "Kahneman & Tversky (1979)" [--optional] [--url …]
  python3 study_ctl.py reading skip <课> <节> <阅读 id> [--undo]  # 找不到的先跳过（学习页会写明没读到）
  python3 study_ctl.py ddl add <课> --title "期末考试" --due "2026-12-08 09:00" [--kind exam] [--session 9]
  python3 study_ctl.py ddl update <课> <截止 id> [--title …] [--due …] [--kind …] [--done | --not-done]
  python3 study_ctl.py ddl remove <课> <截止 id>
  python3 study_ctl.py file <课> --upload <附件 id> [--upload …] [--session <节> | --session course]   # 对话里发来的课件放进这门课
  python3 study_ctl.py file <课> --file /服务器上的/文件.pdf [--session <节>]                       # 自己下载到的文件（经 MCP 不行，用 --upload）
  python3 study_ctl.py assign <课> <文件的相对路径> <节 | course | incoming>                        # 归到某一节 / 整门课的资料 / 挪回待归节
  python3 study_ctl.py syllabus <课> (--upload <附件 id> | --url https://… | --text "…" | --stdin)  # 读大纲，重新建每一节和截止
  python3 study_ctl.py answer <课> <问题 id> "第 6 章"             # 回答读大纲时拿不准的一处（空字符串 = 先不管）
  python3 study_ctl.py style <课> "更短、多举例、先讲直觉"          # 以后写学习页都照这个写法
  python3 study_ctl.py generate <课> <节> [<节>…] [--no-cards] [--no-quiz] [--video] [--rewrite] [--force]
  python3 study_ctl.py undo <课> [<改动号>] [--redo]               # 不给改动号 = 撤销这门课最近一次改动
  python3 study_ctl.py create "课名" [--term …] [--exam closed,essay] [--learn zh,intuition] [--notes …] [--adopt]
撤不回的
  python3 study_ctl.py delete <课>                                # 出收件箱卡，用户点了同意服务器才删

<课> 写文件夹名、课名或缩写都行；<节> 写节号（3 或 S3）或节的 id。
不给 --source：当前目录在哪个 Agent 的工作区里就算它的，否则是 main。
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from config import settings
from i18n import L, lang
from inbox_ctl import call, guess_source

STATUS = {"ready": ("齐了", "ready"), "missing": ("缺阅读", "readings missing"), "noslides": ("缺课件", "no slides"), "later": ("还没上", "not taught yet"),
          "empty": ("还没有材料", "no materials yet")}


def description() -> str:
    return L(__doc__, """Study desk courses: read and change them from the command line (the study Agent). Uses the server's HTTP API (/api/study/courses/*).

Read
  python3 study_ctl.py courses                                   # the courses (name, code, sessions, confirmed or not)
  python3 study_ctl.py show <course> [--json]                    # each session: number, date, topic, readings, materials; deadlines; open questions; unfiled files
  python3 study_ctl.py check <course>                            # what each session is missing
  python3 study_ctl.py log <course>                              # recent changes (change numbers, for undo)
Change (undoable changes are made directly; a change you make shows a "course changed" card under your reply with Undo)
  python3 study_ctl.py session add <course> --topic "Guest lecture" [--date 2026-10-27] [--time 10:00] [--after 7] [--reading "…"]…
  python3 study_ctl.py session update <course> <session> [--topic …] [--date YYYY-MM-DD] [--time HH:MM] [--note …]
  python3 study_ctl.py session move <course> <session> --after <session>
  python3 study_ctl.py session remove <course> <session>        # later sessions renumber; folder and notes stay (undo brings it back)
  python3 study_ctl.py reading set <course> <session> "Textbook ch. 6" ["another"]…
  python3 study_ctl.py reading add <course> <session> "Kahneman & Tversky (1979)" [--optional] [--url …]
  python3 study_ctl.py reading skip <course> <session> <reading id> [--undo]
  python3 study_ctl.py ddl add <course> --title "Final exam" --due "2026-12-08 09:00" [--kind exam] [--session 9]
  python3 study_ctl.py ddl update <course> <deadline id> [--title …] [--due …] [--kind …] [--done | --not-done]
  python3 study_ctl.py ddl remove <course> <deadline id>
  python3 study_ctl.py file <course> --upload <attachment id> [--upload …] [--session <session> | --session course]
  python3 study_ctl.py file <course> --file /path/on/server.pdf [--session <session>]   # not over MCP; use --upload
  python3 study_ctl.py assign <course> <relative file path> <session | course | incoming>
  python3 study_ctl.py syllabus <course> (--upload <attachment id> | --url https://… | --text "…" | --stdin)
  python3 study_ctl.py answer <course> <question id> "chapter 6"
  python3 study_ctl.py style <course> "shorter, more examples, intuition first"
  python3 study_ctl.py generate <course> <session> [<session>…] [--no-cards] [--no-quiz] [--video] [--rewrite] [--force]
  python3 study_ctl.py undo <course> [<change number>] [--redo]
  python3 study_ctl.py create "Course name" [--term …] [--exam closed,essay] [--learn zh,intuition] [--notes …] [--adopt]
Can't be undone
  python3 study_ctl.py delete <course>                           # an inbox card; the server deletes only after the user approves

<course>: folder name, title or code. <session>: number (3 or S3) or id.
Without --source: the Agent whose workspace contains the current directory, otherwise main.
""")


def q(s: str) -> str:
    return urllib.parse.quote(s, safe="")


def resolve(name: str) -> str:
    """课：文件夹名、课名或缩写（不分大小写）→ 文件夹名。"""
    d = call("GET", "/api/study/courses")
    low = name.strip().lower()
    for c in d["courses"]:
        if low in (c["name"].lower(), str(c.get("title") or "").lower()):
            return c["name"]
    coded = [c["name"] for c in d["courses"] if low == str(c.get("code") or "").lower()]
    if len(coded) == 1:
        return coded[0]
    if len(coded) > 1:
        sys.exit(L(f"缩写 {name} 对上了好几门课：{'、'.join(coded)}。写全名。", f"The code {name} matches several courses: {', '.join(coded)}. Use the full name."))
    hits = [c["name"] for c in d["courses"] if low in c["name"].lower() or low in str(c.get("title") or "").lower()]
    if len(hits) == 1:
        return hits[0]
    names = "、".join(f"{c['name']}（{c.get('code')}）" for c in d["courses"]) or L("（还没有课）", "(no courses yet)")
    sys.exit(L(f"没有这门课：{name}。有的是：{names}", f"No such course: {name}. There are: {names}"))


def course_of(name: str) -> tuple[str, dict]:
    c = resolve(name)
    return c, call("GET", f"/api/study/courses/{q(c)}")["course"]


def sid_of(course: dict, ref: str) -> str:
    ref = str(ref).strip()
    for s in course["sessions"]:
        if s["id"] == ref or str(s["n"]) == ref.lstrip("Ss"):
            return s["id"]
    sys.exit(L(f"这门课没有第 {ref} 节", f"This course has no session {ref}"))


def changed(r: dict) -> None:
    card = r.get("card")
    if not r.get("changed", True) and not card:
        print(L("没有变化", "No change"))
        return
    if card:
        print(L(f"改好了（改动号 {card['changeId']}）：{card['title']}", f"Done (change {card['changeId']}): {card['title']}"))
        for line in card.get("lines") or []:
            print(f"  · {line}")
    else:
        print(L("改好了", "Done"))


def show(c: dict) -> None:
    title = c.get("title") or c["name"]
    print(f"{title}（{c['name']}，{c.get('code')}）" + (f" · {c['term']}" if c.get("term") else "") +
          ("" if c["setup"]["confirmed"] else L("  ·  还没核对完（向导第 3 步）", "  ·  not confirmed yet (wizard step 3)")))
    for s in c["sessions"]:
        ch = s["check"]
        st = STATUS.get(ch["status"], (ch["status"], ch["status"]))
        when = (s["date"] or "") + (f" {s['time']}" if s.get("time") else "")
        page = L(" · 有学习页", " · has notes") if s.get("has_page") else ""
        print(f"S{s['n']:<3} {when:<17} {s['topic']}  [{L(st[0], st[1])}{page}]  id={s['id']}")
        for r in s["readings"]:
            state = next((x["state"] for x in ch["readings"] if x["id"] == r["id"]), "")
            mark = {"have": "✓", "skipped": L("跳过", "skipped"), "missing": "✗"}.get(state, "")
            print(f"      {L('读', 'read')}：{r['title']}{'' if r['required'] else L('（选读）', ' (optional)')} {mark}  id={r['id']}")
    if c["deadlines"]:
        print(L("截止：", "Deadlines:"))
        for d in c["deadlines"]:
            print(f"  {d.get('due') or L('日期待定', 'date TBC'):<17} {d['title']}（{d['kind']}）{L(' 交了', ' done') if d['done'] else ''}  id={d['id']}")
    if c.get("questions"):
        print(L("要问用户的：", "Open questions:"))
        for x in c["questions"]:
            print(f"  {x['text']}" + (f"（{' / '.join(x['options'])}）" if x.get("options") else "") + f"  id={x['id']}")
    if c.get("incoming"):
        print(L("待归节的文件：", "Unfiled files:"))
        for f in c["incoming"]:
            print(f"  {f}")


def multipart(path: str, fields: dict, files: list[Path]) -> dict:
    """传文件（multipart/form-data）：服务器上的文件放进课件。"""
    boundary = uuid.uuid4().hex
    body = bytearray()
    for k, v in fields.items():
        if v is None:
            continue
        body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
    for f in files:
        mime = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"files\"; filename=\"{f.name}\"\r\n"
                 f"Content-Type: {mime}\r\n\r\n").encode()
        body += f.read_bytes() + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}", "Accept-Language": "zh-CN" if lang() == "zh" else "en",
               "X-Mousse-Client": "ctl"}
    token = settings.api_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"http://{settings.host}:{settings.port}{path}", data=bytes(body), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=300) as r:  # noqa: S310 — 本机服务
            return json.loads(r.read().decode("utf8"))
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode("utf8")).get("detail")
        except ValueError:
            msg = str(e)
        sys.exit(L(f"失败（HTTP {e.code}）：{msg}", f"Failed (HTTP {e.code}): {msg}"))


def filed(r: dict) -> None:
    for f in r.get("files") or []:
        if f["status"] == "filed":
            print(L(f"{f['name']} → S{f['n']}（{f['reason']}）", f"{f['name']} → S{f['n']} ({f['reason']})"))
        elif f["status"] == "info":
            print(L(f"{f['name']} → 整门课的资料", f"{f['name']} → course info"))
        else:
            print(L(f"{f['name']} 没归：{f['reason']}（文件在 {f['file']}，用 assign 归到某一节）", f"{f['name']} not filed: {f['reason']} (at {f['file']}; use assign)"))
    for e in r.get("errors") or []:
        print(L(f"{e['name']}：{e['reason']}", f"{e['name']}: {e['reason']}"))
    if r.get("card"):
        print(L(f"改动号 {r['card']['changeId']}", f"change {r['card']['changeId']}"))


def main() -> None:  # noqa: C901 — 一条条子命令，拆开反而难找
    p = argparse.ArgumentParser(description=description(), formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--source", help=L("你的 Agent id（默认按当前目录猜）", "your Agent id (guessed from the current directory)"))
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--source", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    sub = p.add_subparsers(dest="cmd", required=True)

    def parser(name: str, parent=sub) -> argparse.ArgumentParser:
        return parent.add_parser(name, parents=[common])
    parser("courses")
    s = parser("show")
    s.add_argument("course")
    s.add_argument("--json", action="store_true")
    parser("check").add_argument("course")
    parser("log").add_argument("course")
    ss = parser("session").add_subparsers(dest="sub", required=True)
    s = parser("add", ss)
    s.add_argument("course")
    s.add_argument("--topic", required=True)
    s.add_argument("--date")
    s.add_argument("--time")
    s.add_argument("--after")
    s.add_argument("--note")
    s.add_argument("--reading", action="append")
    s = parser("update", ss)
    s.add_argument("course")
    s.add_argument("session")
    for f in ("--topic", "--date", "--time", "--note"):
        s.add_argument(f)
    s = parser("move", ss)
    s.add_argument("course")
    s.add_argument("session")
    s.add_argument("--after", required=True)
    s = parser("remove", ss)
    s.add_argument("course")
    s.add_argument("session")
    rs = parser("reading").add_subparsers(dest="sub", required=True)
    s = parser("set", rs)
    s.add_argument("course")
    s.add_argument("session")
    s.add_argument("titles", nargs="*")
    s = parser("add", rs)
    s.add_argument("course")
    s.add_argument("session")
    s.add_argument("title")
    s.add_argument("--optional", action="store_true")
    s.add_argument("--url")
    s = parser("skip", rs)
    s.add_argument("course")
    s.add_argument("session")
    s.add_argument("reading")
    s.add_argument("--undo", action="store_true")
    ds = parser("ddl").add_subparsers(dest="sub", required=True)
    s = parser("add", ds)
    s.add_argument("course")
    s.add_argument("--title", required=True)
    s.add_argument("--due")
    s.add_argument("--kind", default="assignment")
    s.add_argument("--session")
    s = parser("update", ds)
    s.add_argument("course")
    s.add_argument("id")
    for f in ("--title", "--due", "--kind", "--session"):
        s.add_argument(f)
    s.add_argument("--done", action="store_true")
    s.add_argument("--not-done", action="store_true")
    s = parser("remove", ds)
    s.add_argument("course")
    s.add_argument("id")
    s = parser("file")
    s.add_argument("course")
    s.add_argument("--upload", action="append")
    s.add_argument("--file", action="append")
    s.add_argument("--session")
    s = parser("assign")
    s.add_argument("course")
    s.add_argument("file")
    s.add_argument("session")
    s = parser("syllabus")
    s.add_argument("course")
    s.add_argument("--upload")
    s.add_argument("--url")
    s.add_argument("--text")
    s.add_argument("--stdin", action="store_true")
    s = parser("answer")
    s.add_argument("course")
    s.add_argument("id")
    s.add_argument("answer")
    s = parser("style")
    s.add_argument("course")
    s.add_argument("style")
    s = parser("generate")
    s.add_argument("course")
    s.add_argument("sessions", nargs="+")
    s.add_argument("--no-cards", action="store_true")
    s.add_argument("--no-quiz", action="store_true")
    s.add_argument("--video", action="store_true")
    s.add_argument("--rewrite", action="store_true")
    s.add_argument("--force", action="store_true")
    s = parser("undo")
    s.add_argument("course")
    s.add_argument("change", nargs="?", type=int)
    s.add_argument("--redo", action="store_true")
    s = parser("create")
    s.add_argument("name")
    s.add_argument("--term", default="")
    s.add_argument("--exam", default="")
    s.add_argument("--learn", default="")
    s.add_argument("--notes", default="")
    s.add_argument("--adopt", action="store_true")
    parser("delete").add_argument("course")
    a = p.parse_args()
    src = a.source or guess_source()
    base = "/api/study/courses"

    if a.cmd == "courses":
        d = call("GET", base)
        for c in d["courses"]:
            extra = (L(f"{c['sessions']} 节", f"{c['sessions']} sessions") + ("" if c.get("confirmed") else L("，还没核对完", ", not confirmed"))) if c.get("profile") \
                else L("没有课程档案（老的课）", "no course profile (older course)")
            print(f"{c['name']}（{c.get('code')}）· {extra}")
        if d.get("folders"):
            print(L("课件目录里还没当成课的文件夹：", "Folders not yet used as courses: ") + "、".join(d["folders"]))
        return
    if a.cmd == "create":
        r = call("POST", base, {"name": a.name, "term": a.term, "exam": [x for x in a.exam.split(",") if x], "learn": [x for x in a.learn.split(",") if x],
                                "notes": a.notes, "adopt": a.adopt, "source": src})
        print(L(f"建好了：{r['name']}（{len(r['course']['sessions'])} 节）", f"Created: {r['name']} ({len(r['course']['sessions'])} sessions)"))
        return
    course, c = course_of(a.course)
    cq = f"{base}/{q(course)}"
    if a.cmd == "show":
        print(json.dumps(c, ensure_ascii=False, indent=1)) if a.json else show(c)
    elif a.cmd == "check":
        for s in call("GET", f"{cq}/check")["sessions"]:
            st = STATUS.get(s["status"], (s["status"], s["status"]))
            miss = "；".join(m["title"] for m in s["missing"])
            print(f"S{s['n']:<3} {s['topic'][:40]:<40} {L(st[0], st[1])}" + (L(f"：缺 {miss}", f": missing {miss}") if miss else "")
                  + L(f"（课件 {len(s['slides'])} 个，阅读 {s['have']}/{s['total']}）", f" (files {len(s['slides'])}, readings {s['have']}/{s['total']})"))
    elif a.cmd == "log":
        for x in call("GET", f"{cq}/changes")["changes"]:
            print(f"{x['changeId']:>5}  {x['createdAt'][5:16].replace('T', ' ')}  {x['actor']:<10} {x['title']} · {x['summary']}"
                  + (L("（已撤销）", " (undone)") if x["status"] == "undone" else ""))
    elif a.cmd == "session":
        if a.sub == "add":
            changed(call("POST", f"{cq}/sessions", {"topic": a.topic, "date": a.date, "time": a.time, "after": a.after, "note": a.note,
                                                    "readings": a.reading or [], "source": src}))
        elif a.sub == "update":
            body = {k: getattr(a, k) for k in ("topic", "date", "time", "note") if getattr(a, k) is not None}
            changed(call("PATCH", f"{cq}/sessions/{q(sid_of(c, a.session))}", {**body, "source": src}))
        elif a.sub == "move":
            changed(call("PATCH", f"{cq}/sessions/{q(sid_of(c, a.session))}", {"after": a.after, "source": src}))
        else:
            changed(call("DELETE", f"{cq}/sessions/{q(sid_of(c, a.session))}?source={q(src)}"))
    elif a.cmd == "reading":
        sid = sid_of(c, a.session)
        if a.sub == "set":
            changed(call("PATCH", f"{cq}/sessions/{q(sid)}", {"readings": a.titles, "source": src}))
        elif a.sub == "add":
            changed(call("POST", f"{cq}/sessions/{q(sid)}/readings", {"title": a.title, "required": not a.optional, "url": a.url, "source": src}))
        else:
            changed(call("PATCH", f"{cq}/sessions/{q(sid)}/readings/{q(a.reading)}", {"skip": not a.undo, "source": src}))
    elif a.cmd == "ddl":
        if a.sub == "add":
            changed(call("POST", f"{cq}/deadlines", {"title": a.title, "due": a.due, "kind": a.kind,
                                                     "session": sid_of(c, a.session) if a.session else None, "source": src}))
        elif a.sub == "update":
            body = {k: getattr(a, k) for k in ("title", "due", "kind") if getattr(a, k) is not None}
            if a.session:
                body["session"] = sid_of(c, a.session)
            if a.done or a.not_done:
                body["done"] = bool(a.done)
            changed(call("PATCH", f"{cq}/deadlines/{q(a.id)}", {**body, "source": src}))
        else:
            changed(call("DELETE", f"{cq}/deadlines/{q(a.id)}?source={q(src)}"))
    elif a.cmd == "file":
        session = a.session if a.session in (None, "course", "info") else sid_of(c, a.session)
        if a.upload:
            filed(call("POST", f"{cq}/files/from-uploads", {"ids": a.upload, "session": session, "source": src}))
        if a.file:
            if any(f == "-" for f in a.file):
                sys.exit(L("经 MCP 放文件用 --upload（对话附件的 id）", "Over MCP, file documents with --upload (the chat attachment id)"))
            paths = [Path(f).expanduser() for f in a.file]
            missing = [str(x) for x in paths if not x.is_file()]
            if missing:
                sys.exit(L(f"找不到：{'、'.join(missing)}", f"Not found: {', '.join(missing)}"))
            filed(multipart(f"{cq}/files", {"session": session, "source": src}, paths))
        if not a.upload and not a.file:
            sys.exit(L("要给 --upload <附件 id> 或 --file <路径>", "Give --upload <attachment id> or --file <path>"))
    elif a.cmd == "assign":
        target = a.session if a.session in ("course", "info", "incoming") else sid_of(c, a.session)
        r = call("POST", f"{cq}/files/assign", {"file": a.file, "session": target, "source": src})
        print(L(f"挪好了：{r['file']}", f"Moved: {r['file']}") + (L(f"（改动号 {r['change']}）", f" (change {r['change']})") if r.get("change") else ""))
    elif a.cmd == "syllabus":
        text = sys.stdin.read() if a.stdin else a.text
        r = call("POST", f"{cq}/syllabus/from", {"upload": a.upload, "url": a.url, "text": text, "source": src})
        job = r.get("job") or {}
        if job.get("status") == "error":
            sys.exit(job.get("error"))
        print(L("在读大纲（一两分钟）。读完用 show 看每一节；拿不准的会列在「要问用户的」。", "Reading the syllabus (a minute or two). Then use show; unclear points are listed under open questions."))
    elif a.cmd == "answer":
        changed(call("POST", f"{cq}/questions/{q(a.id)}", {"answer": a.answer, "source": src}))
    elif a.cmd == "style":
        changed(call("PATCH", cq, {"style": a.style, "source": src}))
    elif a.cmd == "generate":
        r = call("POST", f"{cq}/generate", {"sessions": [sid_of(c, x) for x in a.sessions], "cards": not a.no_cards, "quiz": not a.no_quiz,
                                            "video": a.video, "rewrite": a.rewrite, "force": a.force, "source": src})
        for b in r.get("blocked") or []:
            miss = "；".join(m["title"] for m in b.get("missing") or [])
            print(L(f"S{b['n']} 先不写：{STATUS.get(b['status'], (b['status'],))[0]}" + (f"（缺 {miss}）" if miss else "") + "。补齐了再写，或者 --force 照写",
                    f"S{b['n']} held back: {b['status']}" + (f" (missing {miss})" if miss else "") + ". Add the materials first, or --force"))
        if r.get("queued"):
            print(L(f"排上了 {len(r['queued'])} 节。", f"Queued {len(r['queued'])} session(s). ") + str(r.get("note") or ""))
    elif a.cmd == "undo":
        cid = a.change
        if cid is None:
            items = [x for x in call("GET", f"{cq}/changes?limit=30")["changes"] if (x["status"] == "undone") == a.redo]
            if not items:
                sys.exit(L("没有能撤销的改动", "Nothing to undo"))
            cid = items[0]["changeId"]
        r = call("POST", f"{cq}/undo/{cid}?redo={1 if a.redo else 0}")
        print(L(f"{'重做' if a.redo else '撤销'}了改动 {cid}：{r['card']['title']}", f"{'Redid' if a.redo else 'Undid'} change {cid}: {r['card']['title']}"))
    elif a.cmd == "delete":
        r = call("DELETE", f"{cq}?source={q(src)}")
        print(L(f"出了收件箱卡（{r.get('inbox')}），用户点了同意才会删。", f"Inbox card created ({r.get('inbox')}); it's deleted only after the user approves."))


if __name__ == "__main__":
    main()
