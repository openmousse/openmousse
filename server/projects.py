"""项目空间（2026-09-27）：以前的「独立空间」，定位成有始有终的事——持续几天到几周、有目标和截止（小组作业、求职冲刺）。
Agent 管一整块领域、一直在；项目做完就归档；任务是一次性的活。

- 一个项目 = side_chats 的一行（线程 id 就是项目 id，sc-xxxxxxxx）+ 对话顶上一张项目卡：目标、截止、下一步、已定的、进度、在跑的任务、结论。
- 截止不另存：自己的截止是日程层的一条（schedule_items kind=deadline，key = project:<项目 id>:<随机>）；已有的
  （课程作业、邮件里的事、求职 ddl、别处加的截止）在 project_items 里挂一个 ref。打勾、推送、起床报告、「要记得的」都走日程层。
- 接得上：OpenClaw 按天重置会话（session.reset）。项目线程每天第一句话、项目卡改过以后的第一句话，前面带上项目卡
  （chat.start_run 调 context_for；只给模型看，对话记录里不显示）。日结（workspace 的 daily_close.py）让项目线程更新进度和下一步。
- 改动记 project_log（谁、改了什么、之前 / 之后）。Agent 在一次回复里改的，对话里那条回复下面出一张小卡（kind project），能撤销。
  截止的增删改和打勾是日程层的改动，出的是日程卡（schedule.py）。
- 开项目：app 里开（POST /api/sidechats 或 /api/projects）；用户让开的，Agent 用 project_ctl.py create；Agent 自己想到的用
  project_ctl.py propose → 收件箱 kind=project，同意了服务端开好，把要点（brief）转进新项目（和转交一样，出转交卡）。
- 归档：app 里「写结论并归档」= 马上收进已归档 + 在项目线程里发「【自动触发】项目归档」，它写结论（project_ctl.py conclude）、写记忆；
  最后一个截止过了 ARCHIVE_AFTER_DAYS 天，日结时问一次（收件箱，静音），同意 = 同样的流程。

表（grava.db）
- side_chats 补的列：goal 目标、progress / progress_at 进度（日结时更新）、summary / summary_at 结论（JSON：done 做成了 / decided 定过的 /
  learned 下次记得 / saved 存到了哪）、rev 卡片版本（改一次 +1）、fed_rev / fed_at 上次带给模型的版本和时间、archived_at、
  closing_at（在写结论）、inbox_id（从哪张提案开的）、archive_asked（问过「归档？」的时间）
- project_items：step 下一步（done_at 打勾）/ decision 已定的 / deadline 挂上的已有截止（ref；text / due_date / due_time 是挂上时的快照，
  源头没了（作业交了、邮件条目过期清掉）照快照显示成做完了）。软删（deleted_at）。
- project_log：每次改动一行。target = card（名字、目标、进度）/ pi:<条目 id> / archive；before / after 是快照。
- project_proposals：收件箱里 kind=project 的卡对应的提案（open 开项目 / archive 归档），同意时照它做。
"""
from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import chat
import inbox
import schedule
from chat import _lock, log_activity, now_iso
from config import TZ, settings
from i18n import L, LS

router = APIRouter()
CTL = Path(__file__).resolve().parent / "project_ctl.py"
KINDS = ("step", "decision", "deadline")
TEXT_MAX, GOAL_MAX, PROGRESS_MAX, TITLE_MAX = 200, 300, 300, 60
ARCHIVE_AFTER_DAYS = 3            # 最后一个截止过了几天，日结时问一次「归档？」
TASKS_SHOWN = 5                   # 项目卡上最多列几个任务
SIDE_COLS = {"goal": "TEXT NOT NULL DEFAULT ''", "progress": "TEXT NOT NULL DEFAULT ''", "progress_at": "TEXT",
             "summary": "TEXT NOT NULL DEFAULT ''", "summary_at": "TEXT", "rev": "INTEGER NOT NULL DEFAULT 0", "fed_rev": "INTEGER",
             "fed_at": "TEXT", "archived_at": "TEXT", "closing_at": "TEXT", "inbox_id": "TEXT", "archive_asked": "TEXT"}
ID_RE = re.compile(r"^sc-[0-9a-f]{8}$")
MARK_ARCHIVE = "【自动触发】项目归档"  # 协议标记，不翻译：project skill 按它认
_waiting: set[asyncio.Task] = set()  # 等项目线程回完再发「写结论」的后台任务（留个引用，免得被回收）


# —— 表 ————————————————————————————————————————————————————————————

_ready = False


def pdb() -> sqlite3.Connection:
    """grava.db（带日程层的表）+ 本模块的表。side_chats 缺的列补上，只查一次。"""
    global _ready
    conn = schedule.sdb()
    if not _ready:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS side_chats (id TEXT PRIMARY KEY, title TEXT NOT NULL, purpose TEXT,
            archived INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS project_items (id TEXT PRIMARY KEY, project TEXT NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL DEFAULT '',
            ref TEXT, due_date TEXT, due_time TEXT, done_at TEXT, source TEXT NOT NULL DEFAULT 'leo', position REAL NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT);
        CREATE INDEX IF NOT EXISTS project_items_project ON project_items(project, kind);
        CREATE TABLE IF NOT EXISTS project_log (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, actor TEXT NOT NULL,
            project TEXT NOT NULL, thread TEXT, message_id INTEGER, target TEXT NOT NULL, action TEXT NOT NULL, title TEXT NOT NULL,
            summary TEXT NOT NULL, before TEXT, after TEXT, undone_at TEXT);
        CREATE INDEX IF NOT EXISTS project_log_thread ON project_log(thread, id);
        CREATE TABLE IF NOT EXISTS project_proposals (inbox_id TEXT PRIMARY KEY, action TEXT NOT NULL, project TEXT, payload TEXT NOT NULL,
            created_at TEXT NOT NULL);
        """)
        have = {r[1] for r in conn.execute("PRAGMA table_info(side_chats)")}
        for col, decl in SIDE_COLS.items():
            if col not in have:
                conn.execute(f"ALTER TABLE side_chats ADD COLUMN {col} {decl}")
        conn.commit()
        _ready = True
    return conn


def row_dict(r: sqlite3.Row | None) -> dict | None:
    return dict(r) if r is not None else None


def project_row(conn: sqlite3.Connection, pid: str) -> sqlite3.Row:
    r = conn.execute("SELECT * FROM side_chats WHERE id=?", (pid,)).fetchone()
    if r is None:
        raise HTTPException(404, L("没有这个项目", "No such project"))
    return r


def is_project(thread: str) -> bool:
    if not thread or not thread.startswith("sc-"):
        return False
    with _lock, pdb() as conn:
        return conn.execute("SELECT 1 FROM side_chats WHERE id=?", (thread,)).fetchone() is not None


def key_prefix(pid: str) -> str:
    return f"project:{pid}:"


def own_rows(conn: sqlite3.Connection, pid: str) -> list[sqlite3.Row]:
    """这个项目自己的截止（日程层里 key 以 project:<id>: 开头的）。"""
    p = key_prefix(pid)
    return conn.execute("SELECT * FROM schedule_items WHERE deleted_at IS NULL AND kind='deadline' AND substr(key, 1, ?)=? ORDER BY date, start",
                        (len(p), p)).fetchall()


def item_rows(conn: sqlite3.Connection, pid: str) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM project_items WHERE project=? AND deleted_at IS NULL ORDER BY position, created_at", (pid,)).fetchall()


# —— 截止：解析成「要记得的」那样的一行 —————————————————————————————————————————

def entries_for(conn: sqlite3.Connection, pid: str) -> list[dict]:
    """这个项目的截止：自己的 + 挂上的，按日子排（勾过的在后）。每行多 own、linkId（挂上的那条 project_items id）、left（还剩几天）、gone（源头没了）。"""
    now_dt, t = schedule.now(), schedule.today()
    mk = schedule.all_marks(conn)
    names = schedule.group_names(conn)
    out = [dict(schedule.item_entry(r, mk, names, now_dt), own=True, linkId=None) for r in own_rows(conn, pid)]
    links = [r for r in item_rows(conn, pid) if r["kind"] == "deadline" and r["ref"]]
    if links:
        want = {r["ref"].split(":", 1)[0] for r in links}
        found: dict[str, dict] = {}
        if "canvas" in want:
            rows, _ = schedule.canvas_rows()
            found |= {schedule.canvas_ref(x): schedule.canvas_entry(x, mk, now_dt) for x in rows}
        if "mail" in want:
            found |= {f"mail:{x['id']}": schedule.mail_entry(x, mk, now_dt) for x in schedule.mail_items()}
        if "app" in want:
            found |= {f"app:{r['id']}": schedule.app_entry(r, mk, names, now_dt) for r in schedule.app_rows(conn)}
        for r in links:
            ref = r["ref"]
            e = found.get(ref)
            if e is None and ref.startswith("item:"):
                it = conn.execute("SELECT * FROM schedule_items WHERE id=? AND deleted_at IS NULL", (ref[5:],)).fetchone()
                e = schedule.item_entry(it, mk, names, now_dt) if it else None
            if e is None and ref in mk and mk[ref].get("date"):
                e = schedule.snapshot_entry(mk[ref])  # 交了、过期清掉了：照打勾时的快照
            if e is None:  # 源头没了、也没打过勾：作业交了、邮件条目过期清掉了——按挂上时的快照算做完了
                e = schedule.entry(id=ref, kind="deadline", origin=schedule.SNAP_ORIGIN.get(ref.split(":", 1)[0], "own"),
                                   title=r["text"] or ref, date=r["due_date"], start=r["due_time"] or "", allDay=not r["due_time"],
                                   done=True, past=True, gone=True)
            out.append(dict(e, own=False, linkId=r["id"]))
    for e in out:
        e.setdefault("gone", False)
        e["left"] = (date.fromisoformat(e["date"]) - t).days if e.get("date") else None
    out.sort(key=lambda e: (bool(e["done"]), e.get("date") or "9999-12-31", e.get("start") or "", e["title"]))
    return out


def next_of(entries: list[dict]) -> dict | None:
    """最近一个还没勾的截止（过了期还没勾的也算，排在最前）。"""
    open_ = [e for e in entries if not e["done"] and e.get("date")]
    if not open_:
        return None
    e = open_[0]
    return {"id": e["id"], "title": e["title"], "date": e["date"], "start": e.get("start") or "", "left": e["left"]}


# —— 任务 ——————————————————————————————————————————————————————————

def tasks_of(pid: str) -> dict:
    """在这个项目里派的后台任务（OpenClaw 台账里 owner 是这个项目的会话）。读不到台账 = available false。"""
    try:
        import cards  # 延迟导入：cards 依赖 chat
        rows = cards.ledger("owner_key=?", (chat.session_key(pid),), 30)
    except Exception:  # noqa: BLE001
        rows = None
    if rows is None:
        return {"available": False, "running": 0, "done": 0, "items": []}
    items = [{"id": r["task_id"], "title": cards.title_of(r), "status": cards.status_of(r)} for r in rows]
    running = sum(1 for x in items if x["status"] == "进行中")
    return {"available": True, "running": running, "done": len(items) - running,
            "items": sorted(items, key=lambda x: x["status"] != "进行中")[:TASKS_SHOWN]}


# —— 项目卡 ————————————————————————————————————————————————————————

def summary_of(raw: str | None) -> dict | None:
    if not raw:
        return None
    try:
        v = json.loads(raw)
    except ValueError:
        return {"done": raw, "decided": [], "learned": "", "saved": ""}
    return v if isinstance(v, dict) else None


def item_json(r: sqlite3.Row) -> dict:
    return {"id": r["id"], "kind": r["kind"], "text": r["text"], "done": bool(r["done_at"]), "doneAt": r["done_at"], "by": r["source"],
            "createdAt": r["created_at"]}


DEADLINE_KEYS = ("id", "title", "date", "start", "allDay", "done", "past", "badge", "origin", "link", "left", "own", "gone", "linkId")


def card_json(conn: sqlite3.Connection, r: sqlite3.Row, with_tasks: bool = True) -> dict:
    pid = r["id"]
    ents = entries_for(conn, pid)
    items = item_rows(conn, pid)
    steps = [item_json(x) for x in items if x["kind"] == "step"]
    steps.sort(key=lambda s: s["done"])  # 做完的沉到下面
    out = {"id": pid, "title": r["title"], "goal": r["goal"] or r["purpose"] or "", "progress": r["progress"] or "", "progressAt": r["progress_at"],
           "summary": summary_of(r["summary"]), "summaryAt": r["summary_at"], "archived": bool(r["archived"]), "archivedAt": r["archived_at"],
           "closing": bool(r["closing_at"]) and not r["summary"], "rev": r["rev"] or 0, "createdAt": r["created_at"],
           # link 是原文链接（Canvas 作业页、Gmail 那封）；linkId 是挂上来的那条 project_items（拿掉时用）
           "deadlines": [{k: e.get(k) for k in DEADLINE_KEYS} for e in ents],
           "steps": steps, "decisions": [item_json(x) for x in items if x["kind"] == "decision"],
           "next": next_of(ents), "stepsLeft": sum(1 for s in steps if not s["done"])}
    if with_tasks:
        out["tasks"] = tasks_of(pid)
    return out


def load_card(pid: str, with_tasks: bool = True) -> dict:
    with _lock, pdb() as conn:
        r = project_row(conn, pid)
        return card_json(conn, r, with_tasks)


def list_fields(conn: sqlite3.Connection, r: sqlite3.Row) -> dict:
    """/api/sidechats 里每个项目多给的：目标、最近的截止、还剩几件下一步、有没有结论。"""
    ents = entries_for(conn, r["id"])
    steps_left = conn.execute("SELECT COUNT(*) FROM project_items WHERE project=? AND kind='step' AND deleted_at IS NULL AND done_at IS NULL",
                              (r["id"],)).fetchone()[0]
    return {"goal": r["goal"] or r["purpose"] or "", "next": next_of(ents), "stepsLeft": steps_left, "hasSummary": bool(r["summary"]),
            "archivedAt": r["archived_at"], "closing": bool(r["closing_at"]) and not r["summary"]}


def side_extra() -> dict[str, dict]:
    """给 data.side_chats()：id → list_fields。出错就不给（列表照常）。"""
    try:
        with _lock, pdb() as conn:
            return {r["id"]: list_fields(conn, r) for r in conn.execute("SELECT * FROM side_chats")}
    except Exception as e:  # noqa: BLE001
        print(f"[projects] 列表字段没算出来：{e}")
        return {}


# —— 带给模型的项目卡 ————————————————————————————————————————————————————

def fmt_day(d: str | None, t: str | None = None) -> str:
    if not d:
        return LS("没定日子", "no date")
    x = date.fromisoformat(d)
    wd = "一二三四五六日"[x.weekday()]
    return LS(f"{x.month}/{x.day} 周{wd}{(' ' + t) if t else ''}", f"{x:%a} {x.day} {x:%b}{(' ' + t) if t else ''}")


def left_words(n: int | None) -> str:
    if n is None:
        return ""
    if n < 0:
        return LS(f"过了 {-n} 天", f"{-n} days ago")
    return LS("今天" if n == 0 else f"还剩 {n} 天", "today" if n == 0 else f"{n} days left")


def context_text(c: dict) -> str:
    """一张项目卡，写给模型看（server.json 的语言）。"""
    lines = [LS(f"【项目空间】这个对话是项目「{c['title']}」（id {c['id']}）。下面是它的项目卡：你和用户共用的进度板，每天第一句话、"
                "卡片改过以后自动带给你，对话记录里不显示。",
                f"[Project space] This chat is the project \"{c['title']}\" (id {c['id']}). Below is its project card, the progress board "
                "you share with the user. It is attached automatically to the first message each day and after the card changes; "
                "it doesn't show in the chat.")]
    if c["goal"]:
        lines.append(LS(f"目标：{c['goal']}", f"Goal: {c['goal']}"))
    if c["deadlines"]:
        parts = []
        for d in c["deadlines"]:
            tail = LS("已勾", "ticked") if d["done"] else left_words(d["left"])
            parts.append(f"{fmt_day(d['date'], d['start'] or None)} {d['title']}" + (f"（{tail}）" if tail else "") + f" [{d['id']}]")
        lines.append(LS("截止：", "Deadlines: ") + "；".join(parts))
    if c["steps"]:
        lines.append(LS("下一步：", "Next steps: ") + "；".join(("☑ " if s["done"] else "☐ ") + s["text"] + f" [{s['id']}]" for s in c["steps"]))
    if c["decisions"]:
        lines.append(LS("已定的：", "Decided: ") + "；".join(f"{d['text']} [{d['id']}]" for d in c["decisions"]))
    if c["progress"]:
        at = (c["progressAt"] or "")[:10]
        lines.append(LS(f"进度（{at}）：{c['progress']}", f"Progress ({at}): {c['progress']}"))
    tk = c.get("tasks") or {}
    if tk.get("running"):
        names = "、".join(x["title"] for x in tk["items"] if x["status"] == "进行中")
        lines.append(LS(f"在跑的任务：{tk['running']} 个（{names}）", f"Tasks running: {tk['running']} ({names})"))
    if c["archived"]:
        lines.append(LS("（这个项目已经归档了。）", "(This project is archived.)"))
    lines.append(LS(f"改项目卡用 python3 {CTL}（project skill）；方括号里是条目 id。",
                    f"Change the card with python3 {CTL} (project skill); ids are in brackets."))
    return "\n".join(lines)


def openclaw_reset() -> dict:
    try:
        return (json.loads(settings.openclaw_json.read_text(encoding="utf8")).get("session") or {}).get("reset") or {}
    except (OSError, ValueError):
        return {}


def last_reset(now_dt: datetime | None = None) -> datetime:
    """OpenClaw 上一次按天重置会话的时刻。按「本机时区」和「用户时区」各算一次取晚的（OpenClaw 按哪个算都不怕，最多多带一次）。
    idle 模式：idleMinutes 之前；不重置：最早。"""
    cfg = openclaw_reset()
    now_dt = now_dt or datetime.now(timezone.utc)
    mode = cfg.get("mode") or "none"
    if mode == "daily":
        hour = int(cfg.get("atHour", 4))
        best = None
        for tz in (datetime.now().astimezone().tzinfo, TZ):
            n = now_dt.astimezone(tz)
            b = n.replace(hour=hour, minute=0, second=0, microsecond=0)
            if b > n:
                b -= timedelta(days=1)
            best = b if best is None or b > best else best
        return best
    if mode == "idle" and cfg.get("idleMinutes"):
        return now_dt - timedelta(minutes=int(cfg["idleMinutes"]))
    return datetime.min.replace(tzinfo=timezone.utc)


def context_for(thread: str) -> str | None:
    """chat.start_run 发消息前调：是项目、而且今天（上次重置以后）还没带过或者卡片改过了 → 返回项目卡的文字，记下带过了。"""
    if not thread.startswith("sc-"):
        return None
    with _lock, pdb() as conn:
        r = conn.execute("SELECT * FROM side_chats WHERE id=?", (thread,)).fetchone()
        if r is None:
            return None
        try:
            fed = datetime.fromisoformat(r["fed_at"]) if r["fed_at"] else None
        except ValueError:
            fed = None
        if fed is not None and r["fed_rev"] == (r["rev"] or 0) and fed >= last_reset():
            return None
        c = card_json(conn, r, with_tasks=False)
    c["tasks"] = tasks_of(thread)
    text = context_text(c)
    with _lock, pdb() as conn:
        conn.execute("UPDATE side_chats SET fed_rev=?, fed_at=? WHERE id=?", (c["rev"], now_iso(), thread))
    return text


# —— 改动：日志、卡片、版本 ———————————————————————————————————————————————

def actor_of(source: str | None) -> str:
    return schedule.actor_of(source)


def run_for(pid: str, actor: str) -> chat.Run | None:
    """Agent 改的：优先这个项目自己正在回复的那一次，其次那个 Agent 最近开始的回复（main 在主对话里改项目卡）。"""
    if actor == "leo":
        return None
    cur = chat.RUNS.get(pid)
    if cur and not cur.done and chat.agent_of(pid) == actor:
        return cur
    return schedule.run_of(actor)


def bump(conn: sqlite3.Connection, pid: str, run: chat.Run | None) -> None:
    """卡片版本 +1。改的是这个项目自己的那次回复：它已经知道了，下一句话不用再带。"""
    conn.execute("UPDATE side_chats SET rev=IFNULL(rev, 0)+1, updated_at=? WHERE id=?", (now_iso(), pid))
    if run is not None and run.thread == pid:
        conn.execute("UPDATE side_chats SET fed_rev=rev WHERE id=?", (pid,))


def log(conn: sqlite3.Connection, actor: str, run: chat.Run | None, pid: str, target: str, action: str, title: str, summary: str,
        before: dict | None, after: dict | None) -> int:
    cur = conn.execute("INSERT INTO project_log(at, actor, project, thread, target, action, title, summary, before, after) VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (now_iso(), actor, pid, run.thread if run else None, target, action, title[:120], summary[:200],
                        json.dumps(before, ensure_ascii=False) if before is not None else None,
                        json.dumps(after, ensure_ascii=False) if after is not None else None))
    return int(cur.lastrowid or 0)


UNDOABLE = ("add", "edit", "done", "undone", "delete", "goal", "progress", "rename", "link", "unlink", "archive", "restore")


def change_json(r: sqlite3.Row, titles: dict[str, str]) -> dict:
    """对话里的项目小卡（kind project）：哪个项目、改了什么、能不能撤销。"""
    return {"kind": "project", "id": f"pj-{r['id']}", "logId": r["id"], "thread": r["thread"], "messageId": r["message_id"],
            "createdAt": r["at"], "status": "undone" if r["undone_at"] else "done", "action": r["action"], "actor": r["actor"],
            "project": r["project"], "projectTitle": titles.get(r["project"], ""), "title": r["title"], "summary": r["summary"],
            "undoable": r["action"] in UNDOABLE}


def titles(conn: sqlite3.Connection) -> dict[str, str]:
    return {r["id"]: r["title"] for r in conn.execute("SELECT id, title FROM side_chats")}


def announce(log_id: int, run: chat.Run | None, actor: str) -> dict | None:
    """回复进行中改的：当场给那次回复发一张小卡；Agent 改的记一行活动。"""
    with _lock, pdb() as conn:
        row = conn.execute("SELECT * FROM project_log WHERE id=?", (log_id,)).fetchone()
        tt = titles(conn)
        names = schedule.group_names(conn)
    if row is None:
        return None
    card = change_json(row, tt)
    if run is not None and not run.done:
        import cards  # 延迟导入
        cards.publish(run, card)
    if actor != "leo":
        log_activity(L(f"改了项目「{card['projectTitle']}」：{row['title']} · {row['summary']}",
                       f'Changed the project "{card["projectTitle"]}": {row["title"]} · {row["summary"]}'), "edit",
                     schedule.who_name(actor, names) or actor)
    return card


def changes_for(thread: str, lo: str, hi: str) -> list[dict]:
    """这个线程这一天（lo–hi）里 Agent 在回复中改的项目卡（给 /api/chat/cards）。"""
    with _lock, pdb() as conn:
        rows = conn.execute("SELECT * FROM project_log WHERE thread=? AND at>=? AND at<? ORDER BY id", (thread, lo, hi)).fetchall()
        tt = titles(conn)
    return [change_json(r, tt) for r in rows]


def link_run(run: chat.Run) -> None:
    """一次回复结束（cards.on_run_end 调）：这次回复里改的项目卡挂到这条回复下面；这次没回成（Gateway 出错），
    带过去的项目卡模型不一定看到了，下一句话重新带。"""
    if run.reply_id is None:
        return
    with _lock, pdb() as conn:
        conn.execute("UPDATE project_log SET message_id=? WHERE thread=? AND message_id IS NULL AND at>=?", (run.reply_id, run.thread, run.started))
        if run.status != "ok" and run.thread.startswith("sc-"):
            conn.execute("UPDATE side_chats SET fed_at=NULL WHERE id=?", (run.thread,))


# —— 接口：项目卡 ——————————————————————————————————————————————————————

@router.get("/api/projects/{pid}")
async def get_project(pid: str):
    return {"ok": True, "project": await asyncio.to_thread(load_card, pid)}


class CardPatch(BaseModel):
    title: str | None = None
    goal: str | None = None
    progress: str | None = None
    source: str | None = None


def clean(text: str | None, limit: int, what: str) -> str | None:
    if text is None:
        return None
    v = " ".join(text.split())[:limit]
    if not v and what:
        raise HTTPException(400, L(f"{what}不能空", f"{what} can't be empty"))
    return v


@router.patch("/api/projects/{pid}")
async def patch_project(pid: str, body: CardPatch):
    """改名字、目标、进度（给了哪个改哪个；目标、进度给空字符串 = 清掉）。"""
    actor = actor_of(body.source)
    run = run_for(pid, actor)
    title = clean(body.title, TITLE_MAX, L("名字", "Name"))
    goal, progress = clean(body.goal, GOAL_MAX, ""), clean(body.progress, PROGRESS_MAX, "")
    logs = []
    with _lock, pdb() as conn:
        r = project_row(conn, pid)
        for field, val, action, head in (("title", title, "rename", L("改了名字", "Renamed")), ("goal", goal, "goal", L("目标", "Goal")),
                                         ("progress", progress, "progress", L("进度", "Progress"))):
            if val is None or val == (r[field] or ""):
                continue
            before = {field: r[field] or "", **({"progress_at": r["progress_at"]} if field == "progress" else {})}
            after = {field: val, **({"progress_at": now_iso() if val else None} if field == "progress" else {})}
            sets = ", ".join(f"{k}=?" for k in after)
            conn.execute(f"UPDATE side_chats SET {sets} WHERE id=?", (*after.values(), pid))  # noqa: S608 — 列名是常量
            if field == "goal":  # 旧的「职责」一并换掉，别处（推送、侧栏）还在读 purpose
                conn.execute("UPDATE side_chats SET purpose=? WHERE id=?", (val, pid))
            summ = val or L("清掉了", "cleared")
            logs.append(log(conn, actor, run, pid, "card", action, head, summ if field != "title" else f"{r['title']} → {val}", before, after))
        if logs:
            bump(conn, pid, run)
    cards_out = [announce(i, run, actor) for i in logs]
    return {"ok": True, "changed": bool(logs), "cards": [c for c in cards_out if c]}


class ItemIn(BaseModel):
    kind: str                 # step 下一步 / decision 已定的 / deadline 截止
    text: str = ""            # 下一步、已定的：那句话；截止：交什么
    due: str | None = None    # 截止：YYYY-MM-DD 或 YYYY-MM-DD HH:MM（自己的截止，进日程层）
    ref: str | None = None    # 截止：挂一条已有的（canvas:… / mail:… / app:… / item:…，从 schedule_ctl.py remember 拿）
    source: str | None = None


def parse_due(due: str) -> tuple[str, str | None]:
    v = due.strip().replace("T", " ")
    if not schedule.DUE_RE.match(v):
        raise HTTPException(400, L("due 要写成 YYYY-MM-DD 或 YYYY-MM-DD HH:MM", "due must be YYYY-MM-DD or YYYY-MM-DD HH:MM"))
    return schedule.check_date(v[:10]) or v[:10], schedule.check_time(v[11:16] or None, "due")


def next_pos(conn: sqlite3.Connection, pid: str, kind: str) -> float:
    r = conn.execute("SELECT MAX(position) FROM project_items WHERE project=? AND kind=?", (pid, kind)).fetchone()
    return (r[0] or 0) + 1


def insert_deadline(conn: sqlite3.Connection, pid: str, title: str, d: str, t: str | None, actor: str) -> str:
    """直接写一条自己的截止进日程层（开项目时用，不出日程卡）。返回 item:<id>。"""
    iid, ts = f"ev-{uuid.uuid4().hex[:8]}", now_iso()
    schedule.write_item(conn, {"id": iid, "kind": "deadline", "title": title, "date": d, "start": t, "end": None, "location": "", "note": "",
                               "source": actor, "key": f"{key_prefix(pid)}{uuid.uuid4().hex[:6]}", "attended": None, "actual_start": None,
                               "actual_end": None, "created_at": ts, "updated_at": ts, "deleted_at": None})
    return f"item:{iid}"


def check_ref(ref: str) -> str:
    ref = ref.strip()
    if ref.split(":", 1)[0] not in schedule.SNAP_ORIGIN or len(ref) < 6:
        raise HTTPException(400, L("ref 要写要记得的那条 id（canvas:… / mail:… / app:… / item:…），schedule_ctl.py remember 能看到",
                                   "ref must be a reminder id (canvas:… / mail:… / app:… / item:…); see schedule_ctl.py remember"))
    return ref


KIND_WORD = {"step": ("下一步", "Next step"), "decision": ("已定的", "Decided"), "deadline": ("截止", "Deadline")}


def kind_word(kind: str) -> str:
    zh, en = KIND_WORD.get(kind, (kind, kind))
    return L(zh, en)


@router.post("/api/projects/{pid}/items")
async def add_item(pid: str, body: ItemIn):
    """加一条：下一步 / 已定的；截止给 due（新建一条自己的截止，出日程卡）或 ref（挂上一条已有的）。"""
    actor = actor_of(body.source)
    kind = body.kind.strip().lower()
    if kind not in KINDS:
        raise HTTPException(400, L("kind 只能是 step / decision / deadline", "kind must be step, decision or deadline"))
    run = run_for(pid, actor)
    with _lock, pdb() as conn:
        project_row(conn, pid)
    if kind == "deadline" and not body.ref:
        title = clean(body.text, TEXT_MAX, L("截止写交什么", "The deadline's title"))
        if not body.due:
            raise HTTPException(400, L("截止要给 due（YYYY-MM-DD 或 YYYY-MM-DD HH:MM），或者 ref 挂一条已有的",
                                       "A deadline needs due (YYYY-MM-DD or YYYY-MM-DD HH:MM), or ref to link an existing one"))
        d, t = parse_due(body.due)
        res = await schedule.add_item(schedule.ItemIn(title=title, date=d, start=t, kind="deadline", key=f"{key_prefix(pid)}{uuid.uuid4().hex[:6]}",
                                                      source=actor))
        with _lock, pdb() as conn:
            bump(conn, pid, run)
        return {"ok": True, "id": res["id"], "card": res.get("card")}
    ts = now_iso()
    with _lock, pdb() as conn:
        if kind == "deadline":
            ref = check_ref(body.ref or "")
            dup = conn.execute("SELECT id FROM project_items WHERE project=? AND ref=? AND deleted_at IS NULL", (pid, ref)).fetchone()
            if dup or (ref.startswith("item:") and conn.execute("SELECT 1 FROM schedule_items WHERE id=? AND substr(key, 1, ?)=?",
                                                                (ref[5:], len(key_prefix(pid)), key_prefix(pid))).fetchone()):
                return {"ok": True, "id": ref, "changed": False}
            hit = found_ref(conn, ref)
            text = (body.text or "").strip()[:TEXT_MAX] or hit["title"]
            snap = (hit.get("date"), hit.get("start") or None)
        else:
            text = clean(body.text, TEXT_MAX, kind_word(kind))
            ref, snap = None, (None, None)
        iid = f"pi-{uuid.uuid4().hex[:8]}"
        after = {"id": iid, "project": pid, "kind": kind, "text": text, "ref": ref, "due_date": snap[0], "due_time": snap[1], "done_at": None,
                 "source": actor, "position": next_pos(conn, pid, kind), "created_at": ts, "updated_at": ts, "deleted_at": None}
        write_row(conn, after)
        lid = log(conn, actor, run, pid, f"pi:{iid}", "link" if kind == "deadline" else "add", L(f"{kind_word(kind)} +1", f"{kind_word(kind)} +1"),
                  text, None, after)
        bump(conn, pid, run)
    card = announce(lid, run, actor)
    return {"ok": True, "id": iid, "card": card}


def found_ref(conn: sqlite3.Connection, ref: str) -> dict:
    """要挂上的那条「要记得的」：找得到（或者打过勾、有快照）才挂，找不到 400（id 要从 schedule_ctl.py remember 拿，别自己拼）。"""
    hit = entries_for_refs(conn, [ref])[0]
    if hit is None:
        m = conn.execute("SELECT * FROM schedule_marks WHERE ref=?", (ref,)).fetchone()
        if m is not None and m["date"]:
            hit = schedule.snapshot_entry(dict(m))
    if hit is None:
        raise HTTPException(404, L(f"找不到这一条：{ref}（id 从 schedule_ctl.py remember 的输出里整段复制）",
                                   f"Can't find this entry: {ref} (copy the whole id from schedule_ctl.py remember)"))
    return hit


def entries_for_refs(conn: sqlite3.Connection, refs: list[str]) -> list[dict | None]:
    """几条「要记得的」ref → 那一行（找不到 None）。挂截止时取标题用。"""
    now_dt = schedule.now()
    mk, names = schedule.all_marks(conn), schedule.group_names(conn)
    out: list[dict | None] = []
    for ref in refs:
        kind, _, key = ref.partition(":")
        e = None
        if kind == "canvas":
            e = next((schedule.canvas_entry(x, mk, now_dt) for x in schedule.canvas_rows()[0] if schedule.canvas_ref(x) == ref), None)
        elif kind == "mail":
            e = next((schedule.mail_entry(x, mk, now_dt) for x in schedule.mail_items() if f"mail:{x['id']}" == ref), None)
        elif kind == "app":
            e = next((schedule.app_entry(r, mk, names, now_dt) for r in schedule.app_rows(conn) if f"app:{r['id']}" == ref), None)
        elif kind == "item":
            it = conn.execute("SELECT * FROM schedule_items WHERE id=? AND deleted_at IS NULL", (key,)).fetchone()
            e = schedule.item_entry(it, mk, names, now_dt) if it else None
        out.append(e)
    return out


ITEM_COLS = ("id", "project", "kind", "text", "ref", "due_date", "due_time", "done_at", "source", "position", "created_at", "updated_at",
             "deleted_at")


def write_row(conn: sqlite3.Connection, state: dict) -> None:
    conn.execute(f"INSERT OR REPLACE INTO project_items({', '.join(ITEM_COLS)}) VALUES({', '.join('?' * len(ITEM_COLS))})",  # noqa: S608
                 tuple(state.get(c) for c in ITEM_COLS))


def item_row(conn: sqlite3.Connection, pid: str, iid: str) -> sqlite3.Row:
    r = conn.execute("SELECT * FROM project_items WHERE id=? AND project=?", (iid, pid)).fetchone()
    if r is None or r["deleted_at"]:
        raise HTTPException(404, L("项目卡上没有这一条", "No such item on the project card"))
    return r


def own_ref(conn: sqlite3.Connection, pid: str, ref: str) -> bool:
    return ref.startswith("item:") and conn.execute("SELECT 1 FROM schedule_items WHERE id=? AND substr(key, 1, ?)=?",
                                                    (ref[5:], len(key_prefix(pid)), key_prefix(pid))).fetchone() is not None


class ItemUpdate(BaseModel):
    id: str                    # 下一步 / 已定的：pi-…；截止：它的 id（item:… / canvas:… …，项目卡上的 deadlines[].id）
    text: str | None = None
    due: str | None = None     # 自己的截止改日子
    done: bool | None = None   # 下一步、截止：打勾 / 取消
    source: str | None = None


@router.post("/api/projects/{pid}/items/update")
async def update_item(pid: str, body: ItemUpdate):
    """改一条：文字、截止的日子、打勾。截止走日程层（出日程卡、能撤销）；挂上的截止源头改不了，只能打勾。"""
    actor = actor_of(body.source)
    run = run_for(pid, actor)
    iid = body.id.strip()
    if not iid.startswith("pi-"):  # 截止
        with _lock, pdb() as conn:
            project_row(conn, pid)
            mine = own_ref(conn, pid, iid)
            linked = conn.execute("SELECT 1 FROM project_items WHERE project=? AND ref=? AND deleted_at IS NULL", (pid, iid)).fetchone()
        if not mine and not linked:
            raise HTTPException(404, L("这个项目没有这一条截止", "This project has no such deadline"))
        out: dict = {"ok": True, "cards": []}
        if body.done is not None:
            res = await schedule.remember_done(schedule.DoneIn(ref=iid, done=body.done, source=body.source))
            out["cards"].append(res.get("card"))
        if body.text is not None or body.due is not None:
            if not mine:
                raise HTTPException(400, L("挂上来的截止源头改不了（作业、邮件、求职），只能打勾；要改就在它的来源改",
                                           "Linked deadlines can't be edited here (coursework, mail, applications); tick it, or change it at the source"))
            kw: dict = {"source": body.source}
            if body.text is not None:
                kw["title"] = clean(body.text, TEXT_MAX, L("截止写交什么", "The deadline's title"))
            if body.due is not None:
                d, t = parse_due(body.due)
                kw.update(date=d, start=t or "")
            res = await schedule.patch_item(iid, schedule.ItemPatch(**kw))
            out["cards"].append(res.get("card"))
        with _lock, pdb() as conn:
            bump(conn, pid, run)
        out["cards"] = [c for c in out["cards"] if c]
        return out
    logs = []
    with _lock, pdb() as conn:
        before = dict(item_row(conn, pid, iid))
        after = dict(before)
        if body.text is not None:
            after["text"] = clean(body.text, TEXT_MAX, kind_word(before["kind"]))
        if body.done is not None:
            if before["kind"] != "step":
                raise HTTPException(400, L("只有下一步能打勾", "Only next steps can be ticked"))
            after["done_at"] = (before["done_at"] or now_iso()) if body.done else None
        if after == before:
            return {"ok": True, "changed": False}
        after["updated_at"] = now_iso()
        write_row(conn, after)
        if after["text"] != before["text"]:
            logs.append(log(conn, actor, run, pid, f"pi:{iid}", "edit", L(f"{kind_word(before['kind'])} · 改了", f"{kind_word(before['kind'])} · edited"),
                            after["text"], before, after))
        elif bool(after["done_at"]) != bool(before["done_at"]):
            logs.append(log(conn, actor, run, pid, f"pi:{iid}", "done" if after["done_at"] else "undone",
                            L("下一步 · 做完了", "Next step · done") if after["done_at"] else L("下一步 · 放回去", "Next step · back on"),
                            after["text"], before, after))
        bump(conn, pid, run)
    return {"ok": True, "changed": True, "cards": [c for c in (announce(i, run, actor) for i in logs) if c]}


class ItemDelete(BaseModel):
    id: str
    source: str | None = None


@router.post("/api/projects/{pid}/items/delete")
async def delete_item(pid: str, body: ItemDelete):
    """删一条。自己的截止从日程里删掉（日程卡能撤销）；挂上的截止只是从项目卡上拿掉，源头不动。"""
    actor = actor_of(body.source)
    run = run_for(pid, actor)
    iid = body.id.strip()
    with _lock, pdb() as conn:
        project_row(conn, pid)
        mine = own_ref(conn, pid, iid)
    if mine:
        res = await schedule.delete_item(iid, source=body.source)
        with _lock, pdb() as conn:
            bump(conn, pid, run)
        return {"ok": True, "card": res.get("card")}
    with _lock, pdb() as conn:
        r = conn.execute("SELECT * FROM project_items WHERE project=? AND (id=? OR ref=?) AND deleted_at IS NULL", (pid, iid, iid)).fetchone()
        if r is None:
            raise HTTPException(404, L("项目卡上没有这一条", "No such item on the project card"))
        before = dict(r)
        after = {**before, "deleted_at": now_iso(), "updated_at": now_iso()}
        write_row(conn, after)
        lid = log(conn, actor, run, pid, f"pi:{before['id']}", "unlink" if before["kind"] == "deadline" else "delete",
                  L(f"{kind_word(before['kind'])} · 拿掉了", f"{kind_word(before['kind'])} · removed"), before["text"], before, after)
        bump(conn, pid, run)
    return {"ok": True, "card": announce(lid, run, actor)}


class UndoIn(BaseModel):
    redo: bool = False


@router.post("/api/projects/undo/{log_id}")
async def undo(log_id: int, body: UndoIn | None = None):
    """撤销项目卡的一次改动（redo=true 再做回来）。对话里项目小卡的「撤销」走这里。"""
    redo = bool(body and body.redo)
    with _lock, pdb() as conn:
        row = conn.execute("SELECT * FROM project_log WHERE id=?", (log_id,)).fetchone()
        if row is None:
            raise HTTPException(404, L("没有这条改动", "No such change"))
        if row["action"] not in UNDOABLE:
            raise HTTPException(400, L("这条改动撤销不了", "This change can't be undone"))
        if bool(row["undone_at"]) != redo:
            raise HTTPException(409, L("已经撤销过了" if not redo else "没撤销过", "Already undone" if not redo else "Not undone"))
        raw = row["after"] if redo else row["before"]
        state = json.loads(raw) if raw else None
        target, pid = row["target"], row["project"]
        if target.startswith("pi:"):
            if state is None:
                cur = conn.execute("SELECT * FROM project_items WHERE id=?", (target[3:],)).fetchone()
                if cur is not None:
                    write_row(conn, {**dict(cur), "deleted_at": now_iso()})
            else:
                write_row(conn, {**state, "updated_at": now_iso()})
        elif target in ("card", "archive"):
            if state:
                sets = ", ".join(f"{k}=?" for k in state)
                conn.execute(f"UPDATE side_chats SET {sets} WHERE id=?", (*state.values(), pid))  # noqa: S608 — 键来自自己写的快照
                if "goal" in state:
                    conn.execute("UPDATE side_chats SET purpose=? WHERE id=?", (state["goal"], pid))
        else:
            raise HTTPException(400, L("这条改动撤销不了", "This change can't be undone"))
        conn.execute("UPDATE project_log SET undone_at=? WHERE id=?", (None if redo else now_iso(), log_id))
        conn.execute("UPDATE side_chats SET rev=IFNULL(rev, 0)+1 WHERE id=?", (pid,))
        row = conn.execute("SELECT * FROM project_log WHERE id=?", (log_id,)).fetchone()
        tt = titles(conn)
    return {"ok": True, "card": change_json(row, tt)}


# —— 开项目 ————————————————————————————————————————————————————————

class DeadlineIn(BaseModel):
    title: str = ""
    due: str | None = None      # YYYY-MM-DD[ HH:MM]：新建一条自己的截止
    ref: str | None = None      # 或者挂一条已有的


class ProjectIn(BaseModel):
    title: str
    goal: str = ""
    purpose: str = ""           # 旧字段（独立空间的「职责」）：没给 goal 就当目标
    model: str | None = None
    deadlines: list[DeadlineIn] = []
    steps: list[str] = []
    decisions: list[str] = []
    brief: str = ""             # 开好以后转进新项目的要点（主对话里聊过的）；空 = 不转
    source: str | None = None


def default_model(conn: sqlite3.Connection) -> str:
    return chat.thread_model(conn, "main")


def create_sync(body: ProjectIn, actor: str, inbox_id: str | None = None) -> tuple[str, str]:
    """写库：side_chats + threads + 截止 + 下一步 + 已定的。返回 (项目 id, 一行摘要)。"""
    title = clean(body.title, TITLE_MAX, L("项目名字", "Project name"))
    goal = clean(body.goal or body.purpose, GOAL_MAX, "") or ""
    dls = []
    for d in body.deadlines:
        if d.ref:
            dls.append(("ref", check_ref(d.ref), (d.title or "").strip()[:TEXT_MAX]))
        elif d.due:
            dd, tt = parse_due(d.due)
            dls.append(("due", (dd, tt), clean(d.title, TEXT_MAX, L("截止写交什么", "The deadline's title"))))
    steps = [s for s in (clean(x, TEXT_MAX, "") for x in body.steps) if s]
    decisions = [s for s in (clean(x, TEXT_MAX, "") for x in body.decisions) if s]
    pid, ts = f"sc-{uuid.uuid4().hex[:8]}", now_iso()
    with _lock, pdb() as conn:  # 挂的截止先都找一遍，有一条找不到就整个不开（别开出半个项目）
        linked = {val: found_ref(conn, val) for kind, val, _ in dls if kind == "ref"}
    with _lock, pdb() as conn:
        model = (body.model or "").strip() or default_model(conn)
        conn.execute("INSERT INTO side_chats(id, title, purpose, goal, created_at, updated_at, rev, inbox_id) VALUES(?,?,?,?,?,?,1,?)",
                     (pid, title, goal, goal, ts, ts, inbox_id))
        conn.execute("INSERT INTO threads(id, model, updated_at) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET model=excluded.model", (pid, model, ts))
        pos = 0.0
        for kind, val, text in dls:
            if kind == "due":
                insert_deadline(conn, pid, text, val[0], val[1], actor)
            else:
                hit = linked[val]
                pos += 1
                write_row(conn, {"id": f"pi-{uuid.uuid4().hex[:8]}", "project": pid, "kind": "deadline", "text": text or hit["title"], "ref": val,
                                 "due_date": hit.get("date"), "due_time": hit.get("start") or None, "done_at": None, "source": actor,
                                 "position": pos, "created_at": ts, "updated_at": ts, "deleted_at": None})
        for kind, texts in (("step", steps), ("decision", decisions)):
            for i, text in enumerate(texts, 1):
                write_row(conn, {"id": f"pi-{uuid.uuid4().hex[:8]}", "project": pid, "kind": kind, "text": text, "ref": None, "due_date": None,
                                 "due_time": None, "done_at": None, "source": actor, "position": float(i), "created_at": ts, "updated_at": ts,
                                 "deleted_at": None})
    bits = []
    if dls:
        bits.append(L(f"{len(dls)} 个截止", f"{len(dls)} deadline{'s' if len(dls) > 1 else ''}"))
    if steps:
        bits.append(L(f"{len(steps)} 步", f"{len(steps)} step{'s' if len(steps) > 1 else ''}"))
    if decisions:
        bits.append(L(f"{len(decisions)} 条已定的", f"{len(decisions)} decided"))
    return pid, " · ".join(bits) or (goal or L("空的项目卡", "an empty card"))


def relay_brief(pid: str, brief: str) -> str:
    """把要点转进新项目（和主对话转给 Agent 一样：那边显示「主对话转来」，这边出转交卡），不等它回。返回 started / failed: …"""
    brief = brief.strip()
    if not brief:
        return ""
    import cards  # 延迟导入
    try:
        run = chat.start_run(pid, chat.PREFIX_RELAY + brief, None, origin="relay", level="none")
    except HTTPException as e:
        return f"failed: {e.detail}"
    cards.handoff_start(pid, brief, run)
    return "started"


async def create_project(body: ProjectIn, inbox_id: str | None = None) -> dict:
    actor = actor_of(body.source)
    # Agent 在回复里开的：开项目的小卡挂在那条回复下面。收件箱同意开的（inbox_id）没有哪次回复在等它
    run = schedule.run_of(actor) if not inbox_id else None
    pid, what = await asyncio.to_thread(create_sync, body, actor, inbox_id)
    title = clean(body.title, TITLE_MAX, "") or ""
    with _lock, pdb() as conn:
        lid = log(conn, actor, run, pid, "create", "create", L("开了项目", "Opened a project"), what, None, None)
        who = schedule.who_name(actor, schedule.group_names(conn))
    log_activity(L(f"开了项目「{title}」", f'Opened the project "{title}"'), "edit", who or None)
    card = announce(lid, run, "leo")  # 活动上面记过了，这里只发卡
    relayed = relay_brief(pid, body.brief)
    return {"ok": True, "id": pid, "card": card, "relay": relayed or None}


@router.post("/api/projects")
async def post_project(body: ProjectIn):
    """开一个项目（app 的「开一个项目」、Agent 的 project_ctl.py create）。"""
    return await create_project(body)


# —— 提案：开项目 / 归档（收件箱 kind=project）————————————————————————————————————

class ProposeIn(ProjectIn):
    why: str = ""               # 为什么提（证据：「这周第 3 次聊」「有两个截止」）
    dedupe: str = ""


def when_words(d: DeadlineIn, conn: sqlite3.Connection | None = None) -> str:
    """提案里的一个截止，给人看：「10/2 周五 09:00 交小组视频」。挂的已有截止（ref）找出它的名字和日子（conn 给了才找）。"""
    if d.due:
        dd, tt = parse_due(d.due)
        return f"{fmt_day_user(dd, tt)} {d.title}".strip()
    hit = entries_for_refs(conn, [d.ref])[0] if conn is not None and d.ref else None
    if hit:
        head = fmt_day_user(hit["date"], hit.get("start") or None) if hit.get("date") else ""
        tail = f"（{hit['badge']}）" if hit.get("badge") else ""
        return f"{head} {d.title or hit['title']}{tail}".strip()
    return d.title or d.ref or ""


def fmt_day_user(d: str, t: str | None) -> str:
    x = date.fromisoformat(d)
    return L(f"{x.month}/{x.day} 周{'一二三四五六日'[x.weekday()]}{(' ' + t) if t else ''}", f"{x:%a} {x.day} {x:%b}{(' ' + t) if t else ''}")


@router.post("/api/projects/propose")
async def propose(body: ProposeIn):
    """Agent 自己想到要开一个项目：进收件箱（kind project，静音），同意了服务端照这份开好、把 brief 转进去。"""
    actor = actor_of(body.source)
    title = clean(body.title, TITLE_MAX, L("项目名字", "Project name"))
    for d in body.deadlines:  # 先把格式检查了，别等同意时才报错
        if d.due:
            parse_due(d.due)
        elif d.ref:
            check_ref(d.ref)
    changes = [L(f"侧栏「项目」里多一个「{title}」", f'A new project "{title}" in the sidebar')]
    new_n, link_n = sum(1 for d in body.deadlines if d.due), sum(1 for d in body.deadlines if d.ref and not d.due)
    if new_n:
        changes.append(L(f"{new_n} 个截止放进日程和「要记得的」", f"{new_n} deadline{'s' if new_n > 1 else ''} go into the schedule and To remember"))
    if link_n:
        changes.append(L(f"已有的 {link_n} 个截止（作业、邮件里的事）挂到项目卡上", f"{link_n} existing deadline{'s' if link_n > 1 else ''} linked to the card"))
    if body.brief.strip():
        changes.append(L("把聊过的要点带过去", "Carry over what was discussed"))
    detail = []
    if body.goal:
        detail.append(L(f"**目标**：{body.goal}", f"**Goal**: {body.goal}"))
    if body.deadlines:
        with _lock, pdb() as conn:
            words = [when_words(d, conn) for d in body.deadlines]
        detail.append(L("**截止**：", "**Deadlines**: ") + L("；", "; ").join(words))
    if body.decisions:
        detail.append(L("**已定的**：", "**Decided**: ") + L("；", "; ").join(body.decisions))
    if body.steps:
        detail.append(L("**下一步**：", "**Next steps**: ") + L("；", "; ").join(body.steps))
    res = await inbox.add(inbox.ItemIn(kind="project", title=L(f"开一个项目：{title}", f"Open a project: {title}"), source=actor, why=body.why,
                                       changes=changes, detail="\n\n".join(detail), approveLabel=L("开这个项目", "Open it"),
                                       dedupe=body.dedupe or f"project:open:{title}"))
    if not isinstance(res, dict):  # 30 天内拒过（409 rejected_before）：原样交回
        return res
    payload = body.model_dump(exclude={"why", "dedupe"})
    with _lock, pdb() as conn:
        conn.execute("INSERT OR REPLACE INTO project_proposals(inbox_id, action, project, payload, created_at) VALUES(?,?,?,?,?)",
                     (res["id"], "open", None, json.dumps(payload, ensure_ascii=False), now_iso()))
    return {"ok": True, "inboxId": res["id"], **({"updated": True} if res.get("updated") else {})}


def proposal_extra(iid: str) -> dict | None:
    """收件箱卡上多给 app 的：提案内容（预览用）和开好的项目（「去看看」）。inbox.item_json 调。"""
    with _lock, pdb() as conn:
        p = conn.execute("SELECT * FROM project_proposals WHERE inbox_id=?", (iid,)).fetchone()
        opened = conn.execute("SELECT id, title FROM side_chats WHERE inbox_id=?", (iid,)).fetchone()
        target = conn.execute("SELECT id, title FROM side_chats WHERE id=?", (p["project"],)).fetchone() if p and p["project"] else None
        pay = json.loads(p["payload"]) if p is not None else {}
        words = [when_words(DeadlineIn(**d), conn) for d in pay.get("deadlines") or []] if p is not None and p["action"] == "open" else []
    if p is None:
        return None
    out: dict = {"action": p["action"]}
    if p["action"] == "open":
        out.update(goal=pay.get("goal") or pay.get("purpose") or "", decisions=pay.get("decisions") or [], steps=pay.get("steps") or [],
                   deadlines=words)
    project = opened or target
    if project:
        out["project"] = {"id": project["id"], "title": project["title"]}
    return out


async def on_project_decided(it: dict, action: str) -> dict | None:
    """收件箱里 kind=project 的卡被点了。开项目：同意 → 照提案开好、转要点，卡片直接标做完。归档：同意 → 收进已归档、让它写结论。"""
    with _lock, pdb() as conn:
        p = conn.execute("SELECT * FROM project_proposals WHERE inbox_id=?", (it["id"],)).fetchone()
    if p is None or action != "approve":
        return None
    if p["action"] == "open":
        body = ProjectIn(**json.loads(p["payload"]))
        res = await create_project(body, inbox_id=it["id"])
        return {"result": L(f"开好了：{body.title}", f"Opened: {body.title}"), "project": res["id"]}
    if p["action"] == "archive" and p["project"]:
        # 只收起来、标上「在写结论」；收件箱照常把「已同意」发进项目线程，它照 project skill 写结论、再报 done（只跑一轮）
        archive_state(p["project"], "leo", summarize=True)
    return None


inbox.HOOKS["project"] = on_project_decided
inbox.EXTRAS["project"] = proposal_extra


# —— 归档、结论 —————————————————————————————————————————————————————

class ArchiveIn(BaseModel):
    summarize: bool = True      # 先让它写结论（进记忆），再归档
    source: str | None = None


def close_text(title: str) -> str:
    """对话里那一行灰字。"""
    return MARK_ARCHIVE + LS(f"：「{title}」归档了，写一份结论", f': "{title}" is archived; write its summary')


def close_context(pid: str) -> str:
    """只给模型看的做法（对话记录里不显示）。"""
    return LS(f"（项目 {pid} 归档了。按 project skill 的「归档」写结论：python3 {CTL} conclude {pid} --done … --decided … --learned … --saved …；"
              "主记忆（MEMORY.md）加一条总结（范围、结论、状态），值得跨平台记住的进世界树。写完回一行「结论写好了」。）",
              f"(Project {pid} is archived. Write its summary as the project skill's \"Archive\" section says: python3 {CTL} conclude {pid} "
              "--done … --decided … --learned … --saved …; add a one-entry summary to MEMORY.md (scope, outcome, status) and put anything "
              "worth remembering across platforms into the memory tree. Then reply \"Summary written.\")")


async def kick_close(pid: str, title: str) -> str:
    """在项目线程里发「写结论」。线程正在回复就等它回完（最多 inbox.WAIT_BUSY_S）。返回 started / queued / failed: …"""
    def go() -> None:
        chat.start_run(pid, close_text(title), None, origin="auto", level="none", context=close_context(pid))
    try:
        go()
        return "started"
    except HTTPException as e:
        if e.status_code != 409:
            return f"failed: {e.detail}"

    async def later() -> None:
        deadline = asyncio.get_running_loop().time() + inbox.WAIT_BUSY_S
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(2)
            cur = chat.RUNS.get(pid)
            if cur and not cur.done:
                continue
            try:
                go()
                return
            except HTTPException as e:
                if e.status_code != 409:
                    break
        log_activity(L(f"「{title}」的结论没能开始写（项目一直在忙）", f'Could not start the summary for "{title}" (the project stayed busy)'), "failed")
    task = asyncio.create_task(later())
    _waiting.add(task)
    task.add_done_callback(_waiting.discard)
    return "queued"


def archive_state(pid: str, actor: str, summarize: bool) -> tuple[int, str, chat.Run | None, bool]:
    """收进「已归档」；summarize 且还没有结论就标上「在写结论」（closing_at）。返回 (改动号, 项目名, 发起的那次回复, 要不要写结论)。"""
    run = run_for(pid, actor)
    with _lock, pdb() as conn:
        r = project_row(conn, pid)
        before = {"archived": r["archived"], "archived_at": r["archived_at"], "closing_at": r["closing_at"]}
        ts = now_iso()
        after = {"archived": 1, "archived_at": r["archived_at"] if r["archived"] else ts,
                 "closing_at": ts if summarize and not r["summary"] else r["closing_at"]}
        conn.execute("UPDATE side_chats SET archived=1, archived_at=?, closing_at=?, updated_at=? WHERE id=?",
                     (after["archived_at"], after["closing_at"], ts, pid))
        lid = log(conn, actor, run, pid, "archive", "archive", L("归档了", "Archived"), r["title"], before, after)
        bump(conn, pid, run)
        title, write = r["title"], summarize and not r["summary"]
    log_activity(L(f"归档了项目「{title}」", f'Archived the project "{title}"'), "edit")
    return lid, title, run, write


@router.post("/api/projects/{pid}/archive")
async def archive(pid: str, body: ArchiveIn):
    """归档：马上收进「已归档」；summarize 就在项目线程里发「【自动触发】项目归档」让它写结论（线程正忙就等它回完）。"""
    actor = actor_of(body.source)
    lid, title, run, write = archive_state(pid, actor, body.summarize)
    started = await kick_close(pid, title) if write else ""  # 已经有结论了（先写过）就不再写
    return {"ok": True, "summarizing": write, "run": started or None, "card": announce(lid, run, "leo")}


def mark_archived(pid: str, archived: bool) -> None:
    """旧接口 PATCH /api/sidechats/{id} 改了 archived（只收起来、不写结论）：归档时间跟着记上 / 清掉。"""
    with _lock, pdb() as conn:
        if archived:
            conn.execute("UPDATE side_chats SET archived_at=IFNULL(archived_at, ?), rev=IFNULL(rev, 0)+1 WHERE id=?", (now_iso(), pid))
        else:
            conn.execute("UPDATE side_chats SET archived_at=NULL, closing_at=NULL, rev=IFNULL(rev, 0)+1 WHERE id=?", (pid,))


@router.post("/api/projects/{pid}/restore")
async def restore(pid: str, source: str | None = None):
    actor = actor_of(source)
    with _lock, pdb() as conn:
        r = project_row(conn, pid)
        if not r["archived"]:
            return {"ok": True, "changed": False}
        before = {"archived": 1, "archived_at": r["archived_at"], "closing_at": r["closing_at"]}
        after = {"archived": 0, "archived_at": None, "closing_at": None}
        conn.execute("UPDATE side_chats SET archived=0, archived_at=NULL, closing_at=NULL, updated_at=? WHERE id=?", (now_iso(), pid))
        log(conn, actor, None, pid, "archive", "restore", L("放回侧栏", "Restored"), r["title"], before, after)
        conn.execute("UPDATE side_chats SET rev=IFNULL(rev, 0)+1 WHERE id=?", (pid,))
    log_activity(L(f"恢复了项目「{r['title']}」", f'Restored the project "{r["title"]}"'), "edit")
    return {"ok": True, "changed": True}


class ConcludeIn(BaseModel):
    done: str = ""               # 做成了什么
    decided: list[str] = []      # 定过的
    learned: str = ""            # 下次记得的
    saved: str = ""              # 存到了哪（主记忆、世界树哪根枝）
    archive: bool = True         # 写完顺手归档（还没归档的话）
    source: str | None = None


@router.post("/api/projects/{pid}/conclude")
async def conclude(pid: str, body: ConcludeIn):
    """写结论（归档时它写；也可以没归档就先写一份）。"""
    actor = actor_of(body.source)
    run = run_for(pid, actor)
    summary = {"done": (body.done or "").strip()[:600], "decided": [d.strip()[:TEXT_MAX] for d in body.decided if d.strip()][:8],
               "learned": (body.learned or "").strip()[:600], "saved": (body.saved or "").strip()[:200]}
    if not summary["done"] and not summary["decided"] and not summary["learned"]:
        raise HTTPException(400, L("结论至少写一样：--done / --decided / --learned", "Write at least one of --done / --decided / --learned"))
    ts = now_iso()
    with _lock, pdb() as conn:
        r = project_row(conn, pid)
        conn.execute("UPDATE side_chats SET summary=?, summary_at=?, closing_at=NULL, updated_at=? WHERE id=?",
                     (json.dumps(summary, ensure_ascii=False), ts, ts, pid))
        if body.archive and not r["archived"]:
            conn.execute("UPDATE side_chats SET archived=1, archived_at=? WHERE id=?", (ts, pid))
        lid = log(conn, actor, run, pid, "summary", "summary", L("写了结论", "Wrote the summary"), summary["done"] or summary["learned"], None, summary)
        bump(conn, pid, run)
        title = r["title"]
    log_activity(L(f"项目「{title}」写了结论", f'Wrote the summary for "{title}"'), "edit")
    return {"ok": True, "card": announce(lid, run, "leo")}


# —— 日结时看一眼：该问「归档？」的 ————————————————————————————————————————————

def review_sync() -> list[dict]:
    """没归档、有截止、最后一个截止过了 ARCHIVE_AFTER_DAYS 天、还没问过的项目。"""
    t = schedule.today()
    out = []
    with _lock, pdb() as conn:
        for r in conn.execute("SELECT * FROM side_chats WHERE archived=0 AND archive_asked IS NULL").fetchall():
            ents = [e for e in entries_for(conn, r["id"]) if e.get("date")]
            if not ents:
                continue
            last = max(e["date"] for e in ents)
            if (t - date.fromisoformat(last)).days >= ARCHIVE_AFTER_DAYS:
                out.append({"id": r["id"], "title": r["title"], "last": last, "open": sum(1 for e in ents if not e["done"])})
    return out


@router.post("/api/projects/review")
async def review():
    """日结（daily_close.py）每晚调一次：截止都过了几天的项目，问一次「归档？」（收件箱 kind project，静音）。"""
    due = await asyncio.to_thread(review_sync)
    asked = []
    for p in due:
        last = date.fromisoformat(p["last"])
        why = L(f"最后一个截止（{last.month}/{last.day}）过了 {ARCHIVE_AFTER_DAYS} 天以上", f"The last deadline ({last:%-d %b}) was over {ARCHIVE_AFTER_DAYS} days ago")
        if p["open"]:
            why += L(f"，还有 {p['open']} 个没勾", f"; {p['open']} still unticked")
        res = await inbox.add(inbox.ItemIn(kind="project", title=L(f"归档「{p['title']}」？", f'Archive "{p["title"]}"?'), source="main", thread=p["id"],
                                           why=why, changes=[L("先写一份结论，存进记忆", "Write a summary into memory first"),
                                                             L("从侧栏收进「已归档」，对话都在", "Move it to Archived; the chat stays")],
                                           approveLabel=L("写结论并归档", "Summarize and archive"), dedupe=f"project:archive:{p['id']}"))
        with _lock, pdb() as conn:
            conn.execute("UPDATE side_chats SET archive_asked=? WHERE id=?", (now_iso(), p["id"]))
            if isinstance(res, dict) and res.get("id"):
                conn.execute("INSERT OR REPLACE INTO project_proposals(inbox_id, action, project, payload, created_at) VALUES(?,?,?,?,?)",
                             (res["id"], "archive", p["id"], "{}", now_iso()))
        asked.append({"id": p["id"], "title": p["title"], "inboxId": res.get("id") if isinstance(res, dict) else None})
    return {"ok": True, "asked": asked}


@router.get("/api/projects")
async def list_projects(all: int = 0):  # noqa: A002 — 查询参数名
    """项目列表（给 project_ctl.py list 和日结用）：没归档的，all=1 连归档的。"""
    def load() -> list[dict]:
        with _lock, pdb() as conn:
            rows = conn.execute("SELECT * FROM side_chats" + ("" if all else " WHERE archived=0") + " ORDER BY updated_at DESC").fetchall()
            return [{"id": r["id"], "title": r["title"], "archived": bool(r["archived"]), **list_fields(conn, r)} for r in rows]
    return {"ok": True, "projects": await asyncio.to_thread(load)}


# —— 删除、「要记得的」上的项目标记 ————————————————————————————————————————————

def on_delete(pid: str) -> None:
    """data.delete_side_chat 删项目时：自己的截止从日程里软删，卡上的条目软删，提案留着（收件箱历史）。"""
    ts = now_iso()
    with _lock, pdb() as conn:
        p = key_prefix(pid)
        conn.execute("UPDATE schedule_items SET deleted_at=?, updated_at=? WHERE deleted_at IS NULL AND substr(key, 1, ?)=?", (ts, ts, len(p), p))
        conn.execute("UPDATE project_items SET deleted_at=?, updated_at=? WHERE project=? AND deleted_at IS NULL", (ts, ts, pid))


def annotate(entries: list[dict]) -> None:
    """日程和「要记得的」里属于某个项目的截止：带上 project {id, title}（app 点一下进项目）；自己的截止小标写项目名。
    schedule.build_remember / build_timeline 在锁外调。"""
    try:
        with _lock, pdb() as conn:
            names = {r["id"]: r["title"] for r in conn.execute("SELECT id, title FROM side_chats")}
            links = {r["ref"]: r["project"] for r in conn.execute("SELECT ref, project FROM project_items WHERE kind='deadline' AND deleted_at IS NULL")}
    except sqlite3.Error:
        return
    for e in entries:
        pid = None
        key = e.get("key") or ""
        if key.startswith("project:"):
            pid = key.split(":")[1]
            if pid in names:
                e["badge"] = names[pid]
        pid = pid or links.get(e.get("id") or "")
        if pid and pid in names:
            e["project"] = {"id": pid, "title": names[pid]}
