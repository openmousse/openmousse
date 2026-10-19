"""Agent 会议（2026-10-13，界面重做第 2 排）：几个 Agent 围绕一个议题轮流发言，Grava 主持、写纪要；确认后待办写进日程和各 Agent 的看板。

线程和会话
- 会议是一个对话线程 grp-<8 位十六进制>，消息照旧存 messages，多两列：speaker（谁说的：Agent id；Grava 的系统行和纪要是 main；你说的是空）
  和 meta（JSON：第几轮、回复谁、@ 了谁、纪要内容、系统行是哪种事件）。role：你说的 = user；Agent 的发言和纪要 = grava（origin meeting，
  算未读）；「由 Grava 发起」「Grava 请 X、Y 回答」这类系统行 = auto（不算未读），文字在读的时候按 meta 重新拼（改了名、换了语言都跟着变）。
- 每个成员在会里有自己的 OpenClaw 会话 agent:<id>:grava:grp-<id>，用它自己的记忆、工具和模型。轮到它时把它还没看过的会议发言
  （标明谁说的）发给它；OpenClaw 每天 04:00 重置会话，隔了天的第一次重新带上议题、规矩和最近 RECAP 条发言。别的 claw 不记会话：每次都带全。
- 表 meetings：议题、成员（按发言顺序）、主持（默认 main）、状态 running 进行中 / pending 待确认 / ended 已结束 / group 保留为群聊、
  第几轮、最新的纪要、确认的结果、从哪个对话发起（主对话自动发起时挂在那条回复下面）；meeting_seen：每个成员看到第几条了、哪天。

规则（发起页和 docs/meetings.md 写的是同一套）
- 每轮每个 Agent 最多发言一次。第 1 轮全体成员按顺序发言；之后的轮次你 @ 了谁就请谁，没 @ 由 Grava 决定：你补充了一句就按这句挑人
  （不带工具的模型），没补充就请纪要里 next 写的，都没有就全体。
- 发言里可以 @ 另一位成员一次（只认第一个），只转一层：被 @ 的这一轮还没说就提到下一个、发言标「回复 X」，它的回复里再 @ 不算；
  这一轮已经说过的不再多给一次（问题留给纪要和下一轮）。所以一轮最多 = 成员数次回复 + 1 次纪要。
- 一轮说完主对话的助手写纪要（llmjson：不带工具的模型）：决定 + 待办（每条有负责的 Agent 和时间）+ 下一轮该请谁。会议变成待确认：
  确认 = 待办写进日程（schedule_items，source = 负责的 Agent，key 防重复）和那个 Agent 看板上的「会议待办」清单，会议结束
  （开了「会后保留为群聊」的变成群聊）；继续讨论或补充一句 = 开下一轮，新纪要取代上一轮的。
- 群聊（group）：你说一句，@ 谁谁答，否则 Grava 请人；不写纪要。
- 会议里只讨论：发给成员的规矩里写明不改日程、看板、不交收件箱卡；要做的事等纪要确认后由服务器写。

主对话自动发起（packs/core/skills/handoff 的「发起会议」，ask_agent.py --meet → POST /api/meetings origin=main）
- 发起时主对话正在回复：会议卡（kind meeting）当场经 SSE 发给那次回复，回复结束挂到它下面（link_run）。一轮的纪要写好后在主对话记一条
  「会议已结束，以下 N 项待办需要你确认：」（role grava，origin meeting），下面挂纪要卡（kind minutes：确认 / 查看会议）。卡片都从
  /api/chat/cards 出（cards_for）。主对话下一次说话时，没告诉过它的纪要和确认结果带在前情里（context_for），它知道会上定了什么。
- 不推送：纪要只记未读（会议和主对话各一条）。

接口
- GET /api/meetings?status=active|ended|all：对话列表的会议行（title、members、status、last、updatedAt、needsYou…；未读看 /api/unread 的 threads）
- POST /api/meetings {topic, members, title?, keep?, origin?}：发起并开第 1 轮；POST /api/meetings/suggest {topic}：按议题推荐成员和会议名
- GET /api/meetings/{id}：会议 + 全部消息 + 正在进行的一轮（live）；GET /api/meetings/{id}/stream：接上正在进行的一轮（SSE，按发言人分段：
  round / speaker / delta / said / system / phase / minutes / message / status / done）
- POST /api/meetings/{id}/send {text}：补充一句（待确认、群聊时开新一轮；进行中只记下，还没发言的成员会看到）
- POST /api/meetings/{id}/continue · /confirm · /stop · /end；PATCH /api/meetings/{id} {keep, title}；DELETE /api/meetings/{id}（删记录，
  已确认写进日程和看板的待办留着）
"""
from __future__ import annotations

import asyncio
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import AsyncIterator

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import chat
import claw
import llmjson
from chat import _lock, day_of, hhmm, log_activity, now_iso, sse
from config import TZ, settings, user_word
from i18n import L, LS

router = APIRouter()

MID_RE = re.compile(r"^grp-[0-9a-f]{8}$")
HOST = "main"            # 主持：现在总是主对话（Grava）；表里留着 host 一列
MIN_MEMBERS, MAX_MEMBERS = 2, 8
TOPIC_MAX, TITLE_MAX, NOTE_MAX = 500, 24, 2000
SPEECH_TIMEOUT = 300     # 一位成员一次发言最长多少秒（到点算没说完，接着请下一位）
RECAP = 30               # 新会话（第一次发言、隔了天、别的 claw）最多带上最近多少条发言
KEEP_LIVE = 15 * 60      # 一轮结束后实时状态留多久（app 断线重连还能拿到收尾）
TODO_MAX, DATES_MAX, DECISIONS_MAX, OPEN_MAX = 8, 7, 5, 5
TODO_COLL = "meeting_todos"   # 各 Agent 看板上「会议待办」那张表
TODO_BLOCK = "meeting-todos"  # 和那块清单积木
STATUS_LABEL = {"running": ("进行中", "In progress"), "pending": ("待确认", "To confirm"), "ended": ("已结束", "Ended"),
                "group": ("群聊", "Group chat")}
_bg: set[asyncio.Task] = set()


def spawn(coro) -> asyncio.Task:
    t = asyncio.create_task(coro)
    _bg.add(t)
    t.add_done_callback(_bg.discard)
    return t


# —— 表 ————————————————————————————————————————————————————————————

SCHEMA = """
CREATE TABLE IF NOT EXISTS meetings (id TEXT PRIMARY KEY, title TEXT NOT NULL, topic TEXT NOT NULL, host TEXT NOT NULL DEFAULT 'main',
    members TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'running', keep INTEGER NOT NULL DEFAULT 0, round INTEGER NOT NULL DEFAULT 0,
    origin TEXT NOT NULL DEFAULT 'manual', from_thread TEXT, message_id INTEGER, minutes TEXT, confirmed TEXT,
    news INTEGER NOT NULL DEFAULT 0, told INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, ended_at TEXT);
CREATE INDEX IF NOT EXISTS meetings_from ON meetings(from_thread, created_at);
CREATE TABLE IF NOT EXISTS meeting_seen (meeting TEXT NOT NULL, agent TEXT NOT NULL, last_id INTEGER NOT NULL DEFAULT 0, day TEXT,
    PRIMARY KEY (meeting, agent));
"""
_ready = False


def mdb():
    """grava.db 加上会议的两张表，messages 补 speaker / meta 两列（只查一次）。"""
    global _ready
    conn = chat.db()
    if not _ready:
        conn.executescript(SCHEMA)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(messages)")}
        if "speaker" not in cols:
            conn.execute("ALTER TABLE messages ADD COLUMN speaker TEXT")  # 会议里谁说的（Agent id；main = 主持）
        if "meta" not in cols:
            conn.execute("ALTER TABLE messages ADD COLUMN meta TEXT")  # 会议消息的附加信息（JSON）
        _ready = True
    return conn


def loads(s: str | None, default=None):
    if not s:
        return default
    try:
        return json.loads(s)
    except ValueError:
        return default


def dumps(v) -> str:
    return json.dumps(v, ensure_ascii=False)


def agent_rows(conn) -> dict[str, dict]:
    """现在有哪些 Agent（groups 表，按显示顺序）：id → {id, name, icon, color, purpose}。"""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='groups'").fetchone():
        return {}
    return {r["id"]: {"id": r["id"], "name": r["name"], "icon": r["icon"], "color": r["color"], "purpose": (r["purpose"] or "").strip()}
            for r in conn.execute("SELECT id, name, icon, color, purpose FROM groups ORDER BY position, created_at")}


def name_of(who: str | None, ag: dict[str, dict]) -> str:
    if not who:
        return L("你", "You")
    if who == HOST:
        return settings.app_name
    return (ag.get(who) or {}).get("name") or who


def member_json(a: str, ag: dict[str, dict]) -> dict:
    g = ag.get(a)
    return {"id": a, "name": g["name"] if g else a, "icon": g["icon"] if g else None, "color": g["color"] if g else None, "gone": g is None}


def joined(names: list[str]) -> str:
    return L("、", ", ").join(names)


def get_row(conn, mid: str):
    if not MID_RE.match(mid or ""):
        raise HTTPException(404, L("没有这个会议", "No such meeting"))
    r = conn.execute("SELECT * FROM meetings WHERE id=?", (mid,)).fetchone()
    if r is None:
        raise HTTPException(404, L("没有这个会议", "No such meeting"))
    return r


def touch(conn, mid: str, ts: str) -> None:
    conn.execute("UPDATE meetings SET updated_at=? WHERE id=?", (ts, mid))
    conn.execute("INSERT INTO threads(id, updated_at) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at", (mid, ts))


def add_message(conn, thread: str, role: str, text: str, speaker: str | None, meta: dict | None, *, status: str = "ok",
                model: str | None = None, origin: str = "meeting", ts: str | None = None) -> int:
    ts = ts or now_iso()
    cur = conn.execute("INSERT INTO messages(thread, role, text, model, ts, status, origin, speaker, meta) VALUES(?,?,?,?,?,?,?,?,?)",
                       (thread, role, text, model, ts, status, origin if role != "user" else "user", speaker, dumps(meta) if meta else None))
    return int(cur.lastrowid or 0)


def system_line(conn, mid: str, event: str, **meta) -> int:
    """一行系统消息（role auto，不算未读）。文字先按服务器语言存一份（搜索、旧版本 app 看得到），app 读的时候按 meta 重拼。"""
    text = system_text({"event": event, **meta}, agent_rows(conn), LS)
    return add_message(conn, mid, "auto", text, HOST, {"kind": "system", "event": event, **meta})


def system_text(meta: dict, ag: dict[str, dict], lx=L) -> str:
    host = settings.app_name
    names = lx("、", ", ").join(name_of(a, ag) for a in meta.get("agents") or [])
    ev, rnd = meta.get("event"), meta.get("round") or 0
    if ev == "start":
        if meta.get("origin") == "main":
            return lx(f"由 {host} 发起", f"Started by {host}")
        return lx(f"由你发起，{host} 主持", f"Started by you, hosted by {host}")
    head = lx(f"第 {rnd} 轮 · ", f"Round {rnd} · ") if rnd > 1 and not meta.get("group") else ""  # 群聊每句一轮，不写第几轮
    if ev == "invite":
        return head + lx(f"{host} 请 {names} 回答", f"{host} asked {names} to answer")
    if ev == "mention":  # 你 @ 了谁
        return head + lx(f"你请 {names} 回答", f"You asked {names} to answer")
    if ev == "skipped":
        return lx(f"{names} 已删除，本轮跳过", f"{names} was deleted and is skipped")
    if ev == "stopped":
        return lx("本轮已停止", "This round was stopped")
    if ev == "interrupted":
        return lx("服务重启，本轮未完成", "The server restarted; this round didn't finish")
    if ev == "nominutes":
        return lx(f"未能生成纪要：{meta.get('error') or ''}".rstrip("："), f"Couldn't write the minutes: {meta.get('error') or ''}".rstrip(": "))
    if ev == "confirmed":
        n, failed = int(meta.get("todos") or 0), int(meta.get("failed") or 0)
        tail = lx(f"，{failed} 项未能写入", f"; {failed} couldn't be added") if failed else ""
        return lx(f"已确认纪要 · {n} 项待办写入日程和看板{tail}", f"Minutes confirmed · {n} to-do{'' if n == 1 else 's'} added to the schedule and dashboards{tail}")
    if ev == "group":
        return lx(f"已保留为群聊：@ 谁谁回答，未 @ 时由 {host} 安排", f"Kept as a group chat: whoever you @ answers; otherwise {host} decides")
    if ev == "ended":
        return lx("会议已结束", "Meeting ended")
    if ev == "reopened":
        return lx("已重新开启为群聊", "Reopened as a group chat")
    return ""


# —— 实时：正在进行的一轮 ————————————————————————————————————————————————————

@dataclass
class Live:
    """一轮讨论，和 HTTP 连接无关：客户端断了照样说完、写完纪要。"""
    mid: str
    round: int
    minutes: bool                  # 这一轮说完写不写纪要（群聊不写）
    queue: list[str]               # 还没发言的（按顺序）
    phase: str = "speaking"        # speaking 有人在说 / minutes 在写纪要 / done
    speaker: str | None = None
    reply_to: str | None = None
    text: str = ""
    done: bool = False
    stopping: bool = False
    stream: asyncio.Task | None = None
    queues: list[asyncio.Queue] = field(default_factory=list)

    def publish(self, event: str, data: dict) -> None:
        for q in list(self.queues):
            q.put_nowait((event, data))


LIVE: dict[str, Live] = {}
_starting: set[str] = set()     # 正在开新一轮、还没进 LIVE 的会议（别当成服务重启丢掉的那种去修）
_confirming: set[str] = set()   # 正在确认的会议（连点两下不重复写看板）


def running(mid: str) -> Live | None:
    lv = LIVE.get(mid)
    return lv if lv and not lv.done else None


def busy_now(mid: str) -> bool:
    return mid in _starting or running(mid) is not None


def live_json(mid: str) -> dict | None:
    lv = running(mid)
    if not lv:
        return None
    return {"round": lv.round, "phase": lv.phase, "speaker": lv.speaker, "replyTo": lv.reply_to, "text": lv.text, "queue": list(lv.queue),
            "minutes": lv.minutes}


async def attach(lv: Live) -> AsyncIterator[bytes]:
    """把一个客户端接到正在进行的一轮上：先给现状（谁在说、说到哪），再转发后续事件，done 为止。"""
    q: asyncio.Queue = asyncio.Queue()
    lv.queues.append(q)
    try:
        yield sse("state", {"live": live_json(lv.mid)})
        if lv.done:
            yield sse("done", {"round": lv.round})
            return
        while True:
            event, data = await q.get()
            yield sse(event, data)
            if event == "done":
                return
    finally:
        if q in lv.queues:
            lv.queues.remove(q)


# —— 输出给 app ——————————————————————————————————————————————————————

def todo_json(td: dict, ag: dict[str, dict]) -> dict:
    return {**td, "agentName": name_of(td.get("agent"), ag), "icon": (ag.get(td.get("agent") or "") or {}).get("icon"),
            "color": (ag.get(td.get("agent") or "") or {}).get("color")}


def minutes_out(minutes: dict | None, ag: dict[str, dict]) -> dict | None:
    if not minutes:
        return None
    return {**minutes, "todos": [todo_json(td, ag) for td in minutes.get("todos") or []]}


def msg_json(r, ag: dict[str, dict]) -> dict:
    meta = loads(r["meta"], {}) or {}
    speaker = r["speaker"]
    if r["role"] == "user":
        kind = "user"
    elif r["role"] == "auto":
        kind = "system"
    else:
        kind = "minutes" if meta.get("kind") == "minutes" else "agent"
    out = {"id": f"db{r['id']}", "kind": kind, "speaker": speaker, "name": name_of(speaker, ag) if kind != "user" else L("你", "You"),
           "text": r["text"], "time": hhmm(r["ts"]), "ts": r["ts"], "status": r["status"], "round": meta.get("round")}
    if kind == "system":
        out["event"] = meta.get("event")
        out["text"] = system_text(meta, ag) or r["text"]
        if meta.get("agents"):
            out["agents"] = meta["agents"]
    if meta.get("replyTo"):
        out["replyTo"] = {"id": meta["replyTo"], "name": name_of(meta["replyTo"], ag)}
    if meta.get("at"):
        out["at"] = {"id": meta["at"], "name": name_of(meta["at"], ag), "deferred": bool(meta.get("deferred"))}
    if kind == "minutes":
        out["minutes"] = minutes_out(meta.get("minutes"), ag)
    return out


def needs_you(r) -> bool:
    """待确认、这一轮的纪要有待办或决定、还没确认：列表行上标出来。"""
    m = loads(r["minutes"])
    return r["status"] == "pending" and bool(m) and m.get("round") == r["round"] and not r["confirmed"] and bool(m.get("todos") or m.get("decisions"))


def meeting_json(r, ag: dict[str, dict], last=None) -> dict:
    status = r["status"]
    out = {"id": r["id"], "kind": "group" if status == "group" else "meeting", "title": r["title"], "topic": r["topic"],
           "host": r["host"], "hostName": settings.app_name, "members": [member_json(a, ag) for a in loads(r["members"], [])],
           "status": status, "statusLabel": L(*STATUS_LABEL.get(status, (status, status))), "keep": bool(r["keep"]), "round": r["round"],
           "origin": r["origin"], "fromThread": r["from_thread"], "messageId": f"db{r['message_id']}" if r["message_id"] else None,
           "minutes": minutes_out(loads(r["minutes"]), ag), "confirmed": loads(r["confirmed"]), "needsYou": needs_you(r),
           "createdAt": r["created_at"], "updatedAt": r["updated_at"], "endedAt": r["ended_at"], "live": live_json(r["id"])}
    if last is not None:
        meta = loads(last["meta"], {}) or {}
        text = system_text(meta, ag) if last["role"] == "auto" else last["text"]
        who = None if last["role"] in ("user", "auto") else name_of(last["speaker"], ag)
        if last["role"] == "user":
            who = L("你", "You")
        out["last"] = {"id": f"db{last['id']}", "speaker": last["speaker"], "name": who, "text": " ".join((text or "").split())[:120],
                       "time": hhmm(last["ts"]), "ts": last["ts"], "kind": "minutes" if meta.get("kind") == "minutes" else last["role"]}
    return out


def repair(conn, r):
    """表里写着进行中、却没有在跑的一轮（服务重启了）：记一行，改成待确认（群聊不变），让人能接着讨论或结束。"""
    if r["status"] != "running" or busy_now(r["id"]):
        return r
    ts = now_iso()
    system_line(conn, r["id"], "interrupted", round=r["round"])
    conn.execute("UPDATE meetings SET status='pending', updated_at=? WHERE id=?", (ts, r["id"]))
    return conn.execute("SELECT * FROM meetings WHERE id=?", (r["id"],)).fetchone()


# —— 列表、发起、推荐 ————————————————————————————————————————————————————

@router.get("/api/meetings")
def list_meetings(status: str = "active"):
    """active = 进行中 / 待确认 / 群聊（对话列表里的行），ended = 已结束的会议，all = 全部。新的（最近有动静的）在前。"""
    if status not in ("active", "ended", "all"):
        raise HTTPException(400, L("status 只能是 active / ended / all", "status must be active, ended or all"))
    cond = {"active": "status!='ended'", "ended": "status='ended'", "all": "1"}[status]
    with _lock, mdb() as conn:
        ag = agent_rows(conn)
        rows = [repair(conn, r) for r in conn.execute(f"SELECT * FROM meetings WHERE {cond} ORDER BY updated_at DESC LIMIT 200")]  # noqa: S608 — 条件是上面写死的三种
        out = []
        for r in rows:
            last = conn.execute("SELECT id, role, text, ts, speaker, meta FROM messages WHERE thread=? ORDER BY id DESC LIMIT 1", (r["id"],)).fetchone()
            out.append(meeting_json(r, ag, last))
        ended = conn.execute("SELECT COUNT(*) FROM meetings WHERE status='ended'").fetchone()[0]
    return {"ok": True, "meetings": out, "endedCount": ended}


class MeetingIn(BaseModel):
    topic: str
    members: list[str]
    title: str | None = None
    keep: bool = False       # 会后保留为群聊
    origin: str = "manual"   # manual：在 app 里发起；main：主对话发起（handoff skill → ask_agent.py --meet）


def clean_text(s: str | None, n: int) -> str:
    return re.sub(r"\s+", " ", s or "").strip()[:n]


def default_title(topic: str) -> str:
    """没给会议名：议题的第一句，中文 12 个字、英文 32 个字符以内。"""
    first = re.split(r"[。！？!?；;\n]", topic.strip(), maxsplit=1)[0].strip() or topic.strip()
    limit = 12 if re.search(r"[一-鿿]", first) else 32
    return first if len(first) <= limit else first[: limit - 1].rstrip("，, 、") + "…"


@router.post("/api/meetings")
async def create(body: MeetingIn):
    topic = clean_text(body.topic, TOPIC_MAX)
    if not topic:
        raise HTTPException(400, L("要写议题", "A topic is required"))
    origin = "main" if body.origin == "main" else "manual"
    members = list(dict.fromkeys(a.strip() for a in body.members if a and a.strip()))
    with _lock, mdb() as conn:
        ag = agent_rows(conn)
    unknown = [a for a in members if a not in ag]
    if unknown:
        raise HTTPException(400, L(f"没有这些 Agent：{', '.join(unknown)}。现在有：{', '.join(ag) or '（无）'}",
                                   f"No such Agent: {', '.join(unknown)}. Current Agents: {', '.join(ag) or '(none)'}"))
    if not MIN_MEMBERS <= len(members) <= MAX_MEMBERS:
        raise HTTPException(400, L(f"会议要 {MIN_MEMBERS}–{MAX_MEMBERS} 个 Agent", f"A meeting needs {MIN_MEMBERS}–{MAX_MEMBERS} Agents"))
    title = clean_text(body.title, TITLE_MAX) or default_title(topic)
    mid = f"grp-{uuid.uuid4().hex[:8]}"
    src = None
    if origin == "main":  # 主对话正在回复（exec 起的脚本不知道自己在哪个会话，和转交卡一样按「谁在回复」认；只认主 agent 的线程）
        import cards  # 延迟导入：cards 依赖 chat
        src = cards.source_run(mid)
        if src is not None and chat.agent_of(src.thread) != "main":  # 主对话在 Telegram 那边、app 里只有某个 Agent 在回复：不挂到它那里
            src = None
    from_thread = (src.thread if src else "main") if origin == "main" else None
    ts = now_iso()
    _starting.add(mid)
    try:
        with _lock, mdb() as conn:
            conn.execute("""INSERT INTO meetings(id, title, topic, host, members, status, keep, round, origin, from_thread, created_at, updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", (mid, title, topic, HOST, dumps(members), "running", int(body.keep), 0, origin, from_thread, ts, ts))
            system_line(conn, mid, "start", origin=origin)
            touch(conn, mid, ts)
        begin_round(mid, members, 1, minutes=True)
    finally:
        _starting.discard(mid)
    log_activity(L(f"发起了会议「{title}」（{joined([name_of(a, ag) for a in members])}）", f'Started the meeting "{title}" ({joined([name_of(a, ag) for a in members])})'),
                 "meeting", actor=settings.app_name if origin == "main" else None)
    if src is not None:  # 发起会议的那次回复当场出会议卡（回复结束挂到它下面，见 link_run）
        import cards
        with _lock, mdb() as conn:
            card = meeting_card(get_row(conn, mid), agent_rows(conn))
        cards.publish(src, card)
    with _lock, mdb() as conn:
        return {"ok": True, "meeting": meeting_json(get_row(conn, mid), agent_rows(conn))}


SUGGEST_RULES = (
    "你是 {app}，用户要发起一场 Agent 会议。根据 INPUT_JSON 的议题（topic），从 agents 里挑该参会的 Agent：2–5 位，只挑和议题直接相关的，"
    "按发言顺序排（约束最多、别人要参考的先说）。再给会议起一个不超过 10 个字的名字（名词短语，不带标点）。"
    "只回一个 JSON，样子：{{\"agents\": [\"Agent id\"], \"title\": \"会议名\"}}",
    "You are {app}. The user is starting a meeting of Agents. From INPUT_JSON's topic, pick the Agents from agents that should attend: "
    "2–5, only the ones directly involved, in speaking order (the one with the most constraints others depend on first). Then name the "
    "meeting in at most 5 words (a noun phrase, no punctuation). Reply with ONE JSON value, shaped like: {{\"agents\": [\"Agent id\"], \"title\": \"Name\"}}")


class SuggestIn(BaseModel):
    topic: str


@router.post("/api/meetings/suggest")
async def suggest(body: SuggestIn):
    """手动发起时 Grava 按议题推荐人选和会议名（不带工具的模型）。模型用不了：不推荐（agents 空），会议名按议题截一句。"""
    topic = clean_text(body.topic, TOPIC_MAX)
    with _lock, mdb() as conn:
        ag = agent_rows(conn)
    if not topic or len(ag) < MIN_MEMBERS:
        return {"ok": True, "agents": [], "title": default_title(topic) if topic else "", "via": None}
    inp = {"topic": topic, "agents": [{"id": a, "name": g["name"], "purpose": g["purpose"]} for a, g in ag.items()]}
    try:
        out, via = await llmjson.ask(LS(*SUGGEST_RULES).format(app=settings.app_name), inp,
                                     {"type": "object", "required": ["agents"]}, timeout=45, kind="owner", origin="user")
    except llmjson.LLMError as e:
        return {"ok": True, "agents": [], "title": default_title(topic), "via": None, "error": str(e)[:120]}
    picked = pick_ids((out or {}).get("agents") if isinstance(out, dict) else None, list(ag), ag)[:5]
    title = clean_text(str((out or {}).get("title") or ""), TITLE_MAX) if isinstance(out, dict) else ""
    return {"ok": True, "agents": picked, "title": title.strip("「」\"'。.") or default_title(topic), "via": via}


def pick_ids(raw, allowed: list[str], ag: dict[str, dict]) -> list[str]:
    """模型给的 Agent：认 id，也认名字；只留 allowed 里的，去重、保持顺序。"""
    by_name = {g["name"].casefold(): a for a, g in ag.items()}
    out: list[str] = []
    for x in raw if isinstance(raw, list) else []:
        s = str(x or "").strip()
        a = s if s in allowed else by_name.get(s.casefold())
        if a and a in allowed and a not in out:
            out.append(a)
    return out


# —— 一轮：成员轮流发言 ————————————————————————————————————————————————————

def begin_round(mid: str, invited: list[str], rnd: int, minutes: bool, event: str = "invite") -> Live:
    """开一轮：记「Grava 请 X、Y 回答」，后台按顺序请他们发言，说完（会议）写纪要。"""
    lv = Live(mid=mid, round=rnd, minutes=minutes, queue=list(invited))
    LIVE[mid] = lv
    ts = now_iso()
    with _lock, mdb() as conn:
        sid = system_line(conn, mid, event, round=rnd, agents=invited, **({} if minutes else {"group": True}))
        if minutes:
            conn.execute("UPDATE meetings SET status='running', round=?, updated_at=? WHERE id=?", (rnd, ts, mid))
        else:
            conn.execute("UPDATE meetings SET round=?, updated_at=? WHERE id=?", (rnd, ts, mid))
        touch(conn, mid, ts)
        row = conn.execute("SELECT * FROM messages WHERE id=?", (sid,)).fetchone()
        ag = agent_rows(conn)
    lv.publish("round", {"round": rnd, "invited": invited, "minutes": minutes})
    lv.publish("system", msg_json(row, ag))
    spawn(run_round(lv))
    return lv


def member_key(agent: str, mid: str) -> str:
    """成员在会里的会话。OpenClaw：有自己 agent 的用 agent:<id>:grava:<会议>；还借住在 main 的老 Agent 在 main 底下单开一个。别的 claw：mousse:<会议>:<id>。"""
    if not claw.is_openclaw():
        return f"mousse:{mid}:{agent}"
    if agent in settings.group_agents:
        return f"agent:{agent}:grava:{mid}"
    return f"agent:main:grava:{mid}-{agent}"


def member_model(conn, agent: str) -> str:
    """成员用它自己对话里选的模型（threads 表里那个 Agent 线程的 model），没选过用默认。"""
    return chat.thread_model(conn, agent)


def user_label() -> str:
    return (settings.user_name or "").strip() or LS("用户", "The user")


def rules_text(m, agent: str, ag: dict[str, dict]) -> str:
    """第一次发言（和隔了天、别的 claw 每一次）：会议是什么、谁在场、你是谁、规矩。"""
    app, who = settings.app_name, user_word()
    me = ag.get(agent) or {"name": agent, "purpose": ""}
    roster = LS("、", ", ").join(f"{name_of(a, ag)}（{a}）" if settings.language == "zh" else f"{name_of(a, ag)} ({a})" for a in loads(m["members"], []))
    return LS(
        f"（{app} 给你的说明，{who}看不到）这是一场会议：{who}请几个 Agent 一起商量一件事，{app} 主持。\n"
        f"议题：{m['topic']}\n参会：{roster}\n"
        f"你是「{me['name']}」，只代表你负责的这一块" + (f"：{me['purpose']}" if me.get("purpose") else "") + "。\n"
        "规矩：\n"
        "- 发言简短（3–6 句），直接给建议，具体到哪天、几点、多少；需要数据就用你自己的记忆和工具先查清楚。\n"
        f"- 会议里只讨论：不改日程、看板和记录，不交收件箱卡，不派后台任务。要做的事写成建议，{who}确认纪要后由 {app} 写进日程和你的看板。\n"
        "- 要问另一位参会的 Agent，就单独一行写「@名字 问题」，一次最多 @ 一位，它会在这一轮回答你。\n"
        "- 别人说过的不用重复；和别人的建议冲突就直接指出。",
        f"({app}'s note to you; {who.strip()} doesn't see it) This is a meeting: {who.strip()} asked several Agents to work something out "
        f"together, hosted by {app}.\nTopic: {m['topic']}\nAttending: {roster}\n"
        f"You are \"{me['name']}\" and speak only for your own area" + (f": {me['purpose']}" if me.get("purpose") else "") + ".\n"
        "Rules:\n"
        "- Keep it short (3–6 sentences) and concrete: which day, what time, how much. If you need data, check your own memory and tools first.\n"
        f"- Only discuss in the meeting: don't change the schedule, dashboards or records, don't file inbox cards, don't start background "
        f"tasks. Put what should happen as a suggestion; once {who.strip()} confirms the minutes, {app} adds it to the schedule and your dashboard.\n"
        "- To ask another Agent in the meeting, write \"@Name question\" on its own line, at most one @ per message; it answers you this round.\n"
        "- Don't repeat what others said; if you disagree with someone's suggestion, say so directly.")


def line_of(r, ag: dict[str, dict], agent: str) -> str:
    """一条会议消息写给成员看的样子：【谁】原话。"""
    meta = loads(r["meta"], {}) or {}
    if r["role"] == "user":
        return f"【{user_label()}】{r['text']}"
    if meta.get("kind") == "minutes":
        return LS(f"【{settings.app_name} · 第 {meta.get('round')} 轮纪要】{r['text']}", f"[{settings.app_name} · minutes of round {meta.get('round')}] {r['text']}")
    who = name_of(r["speaker"], ag)
    if r["speaker"] == agent:
        who = LS(f"{who}（你）", f"{who} (you)")
    return f"【{who}】{r['text']}"


def question_of(text: str, target: str, ag: dict[str, dict]) -> str:
    """发言里 @ 了 target 的那一句（@名字 所在的那一行；那行只有 @名字 就连下一行）。"""
    name = name_of(target, ag)
    lines = [ln.strip() for ln in (text or "").splitlines()]
    for i, ln in enumerate(lines):
        pos = max(ln.find(f"@{name}"), ln.find(f"@ {name}"), ln.find(f"@{target}"))
        if pos < 0:
            continue
        q = ln[pos:].strip()
        if len(re.sub(r"^@\s*\S+\s*", "", q)) < 2 and i + 1 < len(lines):
            q = f"{q} {lines[i + 1]}"
        return q[:240]
    return " ".join((text or "").split())[:240]


def build_prompt(conn, m, agent: str, ag: dict[str, dict], ask, upto: int) -> str:
    """轮到 agent 时发给它的：新会话 = 规矩 + 到目前的发言（最多 RECAP 条）；接着的会话 = 它上次之后的新发言（不含它自己的）。"""
    mid, rnd = m["id"], m["round"]
    seen = conn.execute("SELECT last_id, day FROM meeting_seen WHERE meeting=? AND agent=?", (mid, agent)).fetchone()
    fresh = seen is None or seen["day"] != day_of(now_iso()) or not claw.is_openclaw()
    rows = conn.execute("SELECT id, role, text, speaker, meta FROM messages WHERE thread=? AND id>? AND id<=? AND role IN ('user','grava') "
                        "AND status!='error' ORDER BY id", (mid, 0 if fresh else seen["last_id"], upto)).fetchall()
    lines = [line_of(r, ag, agent) for r in rows if fresh or not (r["role"] == "grava" and r["speaker"] == agent)]
    if fresh and len(lines) > RECAP:
        lines = [LS("（更早的发言略）", "(Earlier messages omitted)")] + lines[-RECAP:]
    parts = [rules_text(m, agent, ag) if fresh else LS(f"（会议「{m['title']}」· 第 {rnd} 轮）", f"(Meeting \"{m['title']}\" · round {rnd})")]
    if lines:
        parts.append((LS("到目前的发言：", "What has been said so far:") if fresh else LS("你上次发言之后：", "Since you last spoke:"))
                     + "\n\n" + "\n\n".join(lines))
    if ask is not None:
        asker = name_of(ask["speaker"], ag)
        parts.append(LS(f"「{asker}」在发言里 @ 了你：「{question_of(ask['text'], agent, ag)}」先回答它；这次发言不要再 @ 别人。",
                        f"\"{asker}\" @-mentioned you: \"{question_of(ask['text'], agent, ag)}\" Answer that first; don't @ anyone in this reply."))
    parts.append(LS(f"现在请你发言（第 {rnd} 轮）。", f"It's your turn to speak (round {rnd})."))
    return "\n\n".join(parts)


def mentions(text: str, members: list[str], ag: dict[str, dict], exclude: str | None = None) -> list[str]:
    """文字里 @ 到的成员（认名字和 id，长的先认；「@ 名字」中间带空格也算），按出现的先后，不含 exclude。"""
    names = sorted([(name_of(a, ag), a) for a in members] + [(a, a) for a in members], key=lambda x: -len(x[0]))
    found: list[str] = []
    for m in re.finditer(r"[@＠]", text or ""):
        rest = text[m.end():].lstrip()
        for nm, a in names:
            if nm and rest.casefold().startswith(nm.casefold()) and a != exclude and a not in found:
                found.append(a)
                break
    return found


async def stream_member(agent: str, mid: str, model: str, content: str, on_delta) -> None:
    """把这一轮发给那位成员、流式收回复。OpenClaw：Gateway 的 OpenAI 兼容接口，指明它自己的 agent 和会话；别的 claw：claw.stream
    （自己的内核：会议是你开的，成员的模型是你自己的 Claude Code 就走你的套餐，engine/owner.stream）。"""
    if not claw.is_openclaw():
        await claw.stream([{"role": "user", "content": content}], claw.day_key(member_key(agent, mid), day_of(now_iso())), model, on_delta,
                          timeout=SPEECH_TIMEOUT, origin="user")
        return
    oc_agent = agent if agent in settings.group_agents else "main"
    token = chat.gateway_token()
    async with httpx.AsyncClient(timeout=httpx.Timeout(SPEECH_TIMEOUT, connect=10)) as client:
        async with client.stream("POST", f"{settings.gateway}/v1/chat/completions",
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json", "x-openclaw-model": model,
                                          "x-openclaw-session-key": member_key(agent, mid), "x-openclaw-agent-id": oc_agent},
                                 json={"model": f"openclaw/{oc_agent}", "stream": True, "messages": [{"role": "user", "content": content}]}) as r:
            if r.status_code != 200:
                raw = (await r.aread()).decode("utf8", "replace")
                raise RuntimeError(f"Gateway HTTP {r.status_code}: {raw[:300]}")
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
                    delta = (ch.get("delta") or {}).get("content")
                    if delta:
                        on_delta(delta)


async def speak(lv: Live, agent: str, ask) -> dict | None:
    """请一位成员发言：拼好它该看的、流式收回复（实时发 speaker / delta），存成一条带 speaker 的消息（发 said）。会议没了返回 None。"""
    mid = lv.mid
    with _lock, mdb() as conn:
        m = conn.execute("SELECT * FROM meetings WHERE id=?", (mid,)).fetchone()
        if m is None:
            return None
        ag = agent_rows(conn)
        upto = conn.execute("SELECT IFNULL(MAX(id), 0) FROM messages WHERE thread=?", (mid,)).fetchone()[0]
        content = build_prompt(conn, m, agent, ag, ask, upto)
        model = member_model(conn, agent)
    asker = ask["speaker"] if ask is not None else None
    lv.speaker, lv.reply_to, lv.text, lv.phase = agent, asker, "", "speaking"
    lv.publish("speaker", {"agent": agent, "name": name_of(agent, ag), "replyTo": asker, "round": lv.round})

    def on_delta(d: str) -> None:
        lv.text += d
        lv.publish("delta", {"agent": agent, "text": d})

    status, error = "ok", None
    lv.stream = asyncio.create_task(asyncio.wait_for(stream_member(agent, mid, model, content, on_delta), SPEECH_TIMEOUT))
    try:
        await lv.stream
    except asyncio.CancelledError:
        if not lv.stopping:
            raise
        status = "stopped"
    except TimeoutError:
        status, error = "error", L("超时", "timed out")
    except (httpx.HTTPError, RuntimeError, OSError) as e:
        status, error = "error", str(e)[:200]
    except HTTPException as e:  # 读不到 Gateway 令牌之类
        status, error = "error", str(e.detail)[:200]
    finally:
        lv.stream = None
    text = lv.text.strip()
    if status == "stopped":
        text = (text + "\n\n" if text else "") + L("（已停止）", "(Stopped)")
    elif status == "error" and not text:
        text = L(f"（未发言：{error}）", f"(No reply: {error})")
    meta = {"round": lv.round, **({"replyTo": asker} if asker else {})}
    ts = now_iso()
    with _lock, mdb() as conn:
        if conn.execute("SELECT 1 FROM meetings WHERE id=?", (mid,)).fetchone() is None:
            return None  # 说着说着会议被删了
        rid = add_message(conn, mid, "grava", text, agent, meta, status=status if status != "stopped" else "ok", model=model, ts=ts)
        conn.execute("""INSERT INTO meeting_seen(meeting, agent, last_id, day) VALUES(?,?,?,?)
            ON CONFLICT(meeting, agent) DO UPDATE SET last_id=excluded.last_id, day=excluded.day""", (mid, agent, upto, day_of(ts)))
        touch(conn, mid, ts)
        row = conn.execute("SELECT * FROM messages WHERE id=?", (rid,)).fetchone()
    out = msg_json(row, ag)
    lv.speaker, lv.reply_to, lv.text = None, None, ""
    lv.publish("said", out)
    return {"row": row, "msg": out, "ok": status == "ok"}


def set_meta(mid: str, rid: int, **extra) -> dict | None:
    """给一条发言的 meta 补字段（@ 了谁），返回新的样子（发给 app 换掉旧的）。"""
    with _lock, mdb() as conn:
        r = conn.execute("SELECT * FROM messages WHERE id=? AND thread=?", (rid, mid)).fetchone()
        if r is None:
            return None
        meta = {**(loads(r["meta"], {}) or {}), **extra}
        conn.execute("UPDATE messages SET meta=? WHERE id=?", (dumps(meta), rid))
        return msg_json(conn.execute("SELECT * FROM messages WHERE id=?", (rid,)).fetchone(), agent_rows(conn))


async def run_round(lv: Live) -> None:
    """按队列请成员发言；@ 只转一层（规则见文件开头）；说完写纪要。出错、停了都要收尾（done），不能让会议一直「进行中」。"""
    mid = lv.mid
    spoken: set[str] = set()
    asked: dict[str, object] = {}  # 被 @ 的 Agent → @ 它的那条发言（行）
    try:
        while lv.queue and not lv.stopping:
            a = lv.queue.pop(0)
            if a in spoken:
                continue
            with _lock, mdb() as conn:
                ag = agent_rows(conn)
                m = conn.execute("SELECT members FROM meetings WHERE id=?", (mid,)).fetchone()
            if m is None:
                return
            if a not in ag:  # 开会期间这个 Agent 被删了
                with _lock, mdb() as conn:
                    sid = system_line(conn, mid, "skipped", agents=[a])
                    row = conn.execute("SELECT * FROM messages WHERE id=?", (sid,)).fetchone()
                lv.publish("system", msg_json(row, ag))
                continue
            ask = asked.get(a)
            got = await speak(lv, a, ask)
            spoken.add(a)
            if got is None:
                return
            if not got["ok"] or ask is not None or lv.stopping:  # 回答 @ 的那一条里再 @ 不算（只转一层）
                continue
            members = loads(m["members"], [])
            at = next(iter(mentions(got["row"]["text"], members, ag, exclude=a)), None)
            if not at:
                continue
            deferred = at in spoken  # 这一轮已经说过了：不再多给一次
            upd = set_meta(mid, got["row"]["id"], at=at, **({"deferred": True} if deferred else {}))
            if upd:
                lv.publish("said", upd)
            if not deferred:
                if at in lv.queue:
                    lv.queue.remove(at)
                lv.queue.insert(0, at)
                asked[at] = got["row"]
        if lv.stopping:
            with _lock, mdb() as conn:
                sid = system_line(conn, mid, "stopped", round=lv.round)
                if lv.minutes:
                    conn.execute("UPDATE meetings SET status='pending', updated_at=? WHERE id=? AND status='running'", (now_iso(), mid))
                row = conn.execute("SELECT * FROM messages WHERE id=?", (sid,)).fetchone()
                ag = agent_rows(conn)
            lv.publish("system", msg_json(row, ag))
            return
        if lv.minutes:
            lv.phase, lv.speaker = "minutes", None
            lv.publish("phase", {"phase": "minutes"})
            await write_minutes(lv)
    except Exception as e:  # noqa: BLE001 — 什么错都要收尾，会议不能一直停在「进行中」
        print(f"[meetings] {mid} 第 {lv.round} 轮出错：{type(e).__name__}: {e}")
        try:
            with _lock, mdb() as conn:
                system_line(conn, mid, "nominutes", error=str(e)[:120])
                conn.execute("UPDATE meetings SET status='pending', updated_at=? WHERE id=? AND status='running'", (now_iso(), mid))
        except Exception:  # noqa: BLE001
            pass
    finally:
        lv.done, lv.phase, lv.speaker, lv.text = True, "done", None, ""
        status = None
        try:
            with _lock, mdb() as conn:
                r = conn.execute("SELECT status FROM meetings WHERE id=?", (mid,)).fetchone()
                status = r["status"] if r else None
        except Exception:  # noqa: BLE001
            pass
        lv.publish("done", {"round": lv.round, "status": status})

        def _forget() -> None:
            if LIVE.get(mid) is lv:
                LIVE.pop(mid, None)
        asyncio.get_running_loop().call_later(KEEP_LIVE, _forget)


# —— 纪要 ————————————————————————————————————————————————————————————

MINUTES_RULES = (
    "你是 {app}，这场会议的主持。根据 INPUT_JSON 写第 {round} 轮的纪要：speeches 是这场会议到现在的发言（id 是 Agent id，user 是用户），"
    "previous 是上一轮的纪要（没有就是 null），这一轮的纪要要取代它（还成立的决定和待办照写）。\n"
    "规矩：\n"
    "- 只写发言里真的定下来的，不编、不加自己的建议。没定下来的放进 open。\n"
    "- 每条待办有且只有一个负责的 Agent（members 里的 id），并且有时间：按 today（{today}，{weekday}）和时区 {tz} 换算成具体日期；"
    "重复的写多个日期（最多 7 个）；只有截止日的 start 写 null。待办最多 8 条。\n"
    "- text 是列表里显示的一行：时间 + 事，简短，比如「周二 18:00 加餐提醒」；title 是写进日程的标题，不带时间，比如「加餐提醒」。\n"
    "- next：要继续讨论时该请谁（和 open 里的问题有关的 Agent id），没有就空。\n"
    "- 用中文，书面、简短。\n"
    "只回一个 JSON，样子：{{\"decisions\": [\"一句一条，最多 5 条\"], \"todos\": [{{\"agent\": \"Agent id\", \"text\": \"周二 18:00 加餐提醒\", "
    "\"title\": \"加餐提醒\", \"dates\": [\"YYYY-MM-DD\"], \"start\": \"HH:MM 或 null\", \"end\": \"HH:MM 或 null\"}}], "
    "\"next\": [\"Agent id\"], \"open\": [\"还没定的问题，最多 5 条\"]}}",
    "You are {app}, the host of this meeting. Write the minutes of round {round} from INPUT_JSON: speeches is everything said in the meeting "
    "so far (id is the Agent id; user is the user), previous is the last round's minutes (or null); these minutes replace them (keep "
    "decisions and to-dos that still stand).\n"
    "Rules:\n"
    "- Only write what was actually agreed. Don't invent anything or add your own advice. Anything not settled goes in open.\n"
    "- Every to-do has exactly one owner (an Agent id from members) and a time: resolve it to concrete dates from today ({today}, {weekday}) "
    "in the {tz} time zone; a repeating one lists several dates (at most 7); a deadline only has start null. At most 8 to-dos.\n"
    "- text is the line shown in the list: time + what, short, e.g. \"Tue 18:00 snack reminder\"; title is the schedule title without the "
    "time, e.g. \"Snack reminder\".\n"
    "- next: who to ask if the discussion continues (Agent ids tied to the open questions), or empty.\n"
    "- Write in English, plain and short.\n"
    "Reply with ONE JSON value, shaped like: {{\"decisions\": [\"one sentence each, at most 5\"], \"todos\": [{{\"agent\": \"Agent id\", "
    "\"text\": \"Tue 18:00 snack reminder\", \"title\": \"Snack reminder\", \"dates\": [\"YYYY-MM-DD\"], \"start\": \"HH:MM or null\", "
    "\"end\": \"HH:MM or null\"}}], \"next\": [\"Agent id\"], \"open\": [\"open questions, at most 5\"]}}")
TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
WEEKDAYS = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")
WEEKDAYS_EN = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def hm_of(v) -> str | None:
    m = TIME_RE.match(str(v or "").strip())
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else None


def clean_minutes(out, members: list[str], ag: dict[str, dict], rnd: int) -> dict:
    """模型交上来的纪要逐个字段清理：待办的负责人要是成员、日期在 -1…+120 天以内、时间是 HH:MM；数量都有上限。"""
    out = out if isinstance(out, dict) else {}
    today = datetime.now(TZ).date()

    def strs(v, n: int, width: int) -> list[str]:
        return [clean_text(str(x), width) for x in (v if isinstance(v, list) else []) if clean_text(str(x), width)][:n]

    todos = []
    for td in out.get("todos") if isinstance(out.get("todos"), list) else []:
        if not isinstance(td, dict):
            continue
        owner = next(iter(pick_ids([td.get("agent")], members, ag)), None)
        text = clean_text(str(td.get("text") or td.get("title") or ""), 80)
        if not owner or not text:
            continue
        dates = []
        for d in td.get("dates") if isinstance(td.get("dates"), list) else ([td.get("date")] if td.get("date") else []):
            try:
                dd = date.fromisoformat(str(d).strip()[:10])
            except ValueError:
                continue
            if -1 <= (dd - today).days <= 120 and dd.isoformat() not in dates:
                dates.append(dd.isoformat())
        start, end = hm_of(td.get("start")), hm_of(td.get("end"))
        if not start or (end and end <= start):
            end = None
        todos.append({"agent": owner, "text": text, "title": clean_text(str(td.get("title") or ""), 60) or text,
                      "dates": sorted(dates)[:DATES_MAX], "start": start, "end": end})
    return {"round": rnd, "decisions": strs(out.get("decisions"), DECISIONS_MAX, 140), "todos": todos[:TODO_MAX],
            "next": pick_ids(out.get("next"), members, ag), "open": strs(out.get("open"), OPEN_MAX, 140), "at": now_iso()}


def minutes_text(mn: dict, ag: dict[str, dict], lx=L) -> str:
    """纪要的纯文字版（存进消息正文：搜索、未读预览、转发、给成员看都用它）。"""
    parts = []
    if mn.get("decisions"):
        parts.append(lx("决定：", "Decisions:") + "\n" + "\n".join(f"- {d}" for d in mn["decisions"]))
    if mn.get("todos"):
        parts.append(lx("待办：", "To-dos:") + "\n" + "\n".join(
            lx(f"- {td['text']}（{name_of(td['agent'], ag)}）", f"- {td['text']} ({name_of(td['agent'], ag)})") for td in mn["todos"]))
    if mn.get("open"):
        parts.append(lx("未定：", "Open:") + "\n" + "\n".join(f"- {q}" for q in mn["open"]))
    return "\n\n".join(parts) or lx("本轮没有定下决定或待办。", "Nothing was decided this round.")


async def write_minutes(lv: Live) -> None:
    """一轮说完：Grava（不带工具的模型）写纪要 → 会议里记一条纪要、状态改成待确认；从主对话发起的，主对话里记一条通知（下面挂纪要卡）。"""
    mid = lv.mid
    with _lock, mdb() as conn:
        m = conn.execute("SELECT * FROM meetings WHERE id=?", (mid,)).fetchone()
        if m is None:
            return
        ag = agent_rows(conn)
        rows = conn.execute("SELECT role, text, speaker, meta FROM messages WHERE thread=? AND role IN ('user','grava') AND status!='error' ORDER BY id",
                            (mid,)).fetchall()
    members = loads(m["members"], [])
    now = datetime.now(TZ)
    speeches = [{"id": "user" if r["role"] == "user" else r["speaker"], "who": user_label() if r["role"] == "user" else name_of(r["speaker"], ag),
                 "text": r["text"][:3000]} for r in rows if (loads(r["meta"], {}) or {}).get("kind") != "minutes"][-80:]
    prev = loads(m["minutes"])
    inp = {"topic": m["topic"], "round": lv.round, "today": now.date().isoformat(), "weekday": WEEKDAYS_EN[now.weekday()], "timezone": settings.timezone,
           "members": [{"id": a, "name": name_of(a, ag), "purpose": (ag.get(a) or {}).get("purpose", "")} for a in members],
           "speeches": speeches, "previous": {k: prev.get(k) for k in ("decisions", "todos", "open")} if prev else None}
    rules = LS(*MINUTES_RULES).format(app=settings.app_name, round=lv.round, today=now.date().isoformat(),
                                      weekday=LS(WEEKDAYS[now.weekday()], WEEKDAYS_EN[now.weekday()]), tz=settings.timezone)
    try:
        out, _via = await llmjson.ask(rules, inp, {"type": "object", "required": ["todos"]}, timeout=120, kind="owner", origin="user")
    except llmjson.LLMError as e:
        with _lock, mdb() as conn:
            sid = system_line(conn, mid, "nominutes", error=str(e)[:120])
            conn.execute("UPDATE meetings SET status='pending', updated_at=? WHERE id=?", (now_iso(), mid))
            row = conn.execute("SELECT * FROM messages WHERE id=?", (sid,)).fetchone()
        lv.publish("system", msg_json(row, ag))
        lv.publish("status", {"status": "pending"})
        return
    mn = clean_minutes(out, members, ag, lv.round)
    ts = now_iso()
    with _lock, mdb() as conn:
        cur = conn.execute("SELECT status FROM meetings WHERE id=?", (mid,)).fetchone()
        if cur is None:
            return
        ag = agent_rows(conn)
        rid = add_message(conn, mid, "grava", minutes_text(mn, ag), HOST, {"kind": "minutes", "round": lv.round, "minutes": mn}, ts=ts)
        mn["messageId"] = f"db{rid}"
        conn.execute("UPDATE messages SET meta=? WHERE id=?", (dumps({"kind": "minutes", "round": lv.round, "minutes": mn}), rid))
        # 写纪要的时候你已经点了「结束」：纪要照记，状态不改回待确认，主对话也不再通知
        conn.execute("UPDATE meetings SET minutes=?, status=CASE WHEN status='running' THEN 'pending' ELSE status END, news=news+1, updated_at=? "
                     "WHERE id=?", (dumps(mn), ts, mid))
        touch(conn, mid, ts)
        if m["from_thread"] and cur["status"] != "ended":  # 主对话发起的：主对话里一条通知，下面挂纪要卡（cards_for）
            add_message(conn, m["from_thread"], "grava", notice_text(m["title"], lv.round, mn), HOST,
                        {"kind": "meeting", "meeting": mid, "round": lv.round}, ts=ts)
            conn.execute("INSERT INTO threads(id, updated_at) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at",
                         (m["from_thread"], ts))
        row = conn.execute("SELECT * FROM messages WHERE id=?", (rid,)).fetchone()
        status = conn.execute("SELECT status FROM meetings WHERE id=?", (mid,)).fetchone()["status"]
    lv.publish("minutes", msg_json(row, ag))
    lv.publish("status", {"status": status})


def notice_text(title: str, rnd: int, mn: dict) -> str:
    n = len(mn.get("todos") or [])
    head = L(f"会议「{title}」已结束", f'The meeting "{title}" is over') if rnd <= 1 else \
        L(f"会议「{title}」第 {rnd} 轮讨论已结束", f'Round {rnd} of the meeting "{title}" is over')
    if n:
        return head + L(f"，以下 {n} 项待办需要你确认：", f". {n} to-do{'' if n == 1 else 's'} need{'s' if n == 1 else ''} your confirmation:")
    return head + L("，纪要见会议。", ". The minutes are in the meeting.")


# —— 之后的轮次、补充、确认、结束 ——————————————————————————————————————————

PICK_RULES = (
    "你是 {app}，这场会议的主持。{user}刚补充了一句（INPUT_JSON 的 note）。从 members 里挑这次该请谁回答：只挑和这句话直接相关的 1–3 位，"
    "按回答顺序。只回一个 JSON，样子：{{\"agents\": [\"Agent id\"]}}",
    "You are {app}, the host of this meeting. {user} just added a note (INPUT_JSON's note). Pick who from members should answer it: only "
    "the 1–3 directly involved, in answering order. Reply with ONE JSON value, shaped like: {{\"agents\": [\"Agent id\"]}}")


async def pick_speakers(m, note: str | None, ag: dict[str, dict]) -> list[str]:
    """没 @ 时请谁：补充了一句就请模型按这句挑（失败了看这句里提到了谁的名字）；没补充就请纪要的 next；都没有就全体。"""
    members = [a for a in loads(m["members"], []) if a in ag]
    mn = loads(m["minutes"]) or {}
    if note:
        inp = {"topic": m["topic"], "note": note, "members": [{"id": a, "name": name_of(a, ag), "purpose": ag[a]["purpose"]} for a in members],
               "open": mn.get("open") or []}
        try:
            out, _ = await llmjson.ask(LS(*PICK_RULES).format(app=settings.app_name, user=user_word()), inp,
                                       {"type": "object", "required": ["agents"]}, timeout=40, kind="owner", origin="user")
            picked = pick_ids(out.get("agents") if isinstance(out, dict) else None, members, ag)[:3]
            if picked:
                return picked
        except llmjson.LLMError:
            pass
        named = [a for a in members if name_of(a, ag) in note]
        if named:
            return named
    nxt = [a for a in mn.get("next") or [] if a in members] if mn.get("round") == m["round"] else []
    return nxt or members


class SendIn(BaseModel):
    text: str = ""


async def next_round(mid: str, note: str | None) -> dict:
    """开下一轮（会议：待确认 → 进行中，写纪要；群聊：不写纪要）。note = 你补充的那句（已经记进会议了）。"""
    with _lock, mdb() as conn:
        m = get_row(conn, mid)
        ag = agent_rows(conn)
    if busy_now(mid):
        raise HTTPException(409, L("本轮还在进行", "This round is still running"))
    if m["status"] == "ended":
        raise HTTPException(409, L("会议已结束", "The meeting has ended"))
    members = [a for a in loads(m["members"], []) if a in ag]
    if not members:
        raise HTTPException(409, L("参会的 Agent 都已删除", "All the meeting's Agents have been deleted"))
    _starting.add(mid)  # 挑人要等模型：这几秒里再来一句不另开一轮
    try:
        at = mentions(note or "", members, ag)
        invited = at or await pick_speakers(m, note, ag)
        begin_round(mid, invited, m["round"] + 1, minutes=m["status"] != "group", event="mention" if at else "invite")
    finally:
        _starting.discard(mid)
    with _lock, mdb() as conn:
        return meeting_json(get_row(conn, mid), agent_rows(conn))


@router.post("/api/meetings/{mid}/send")
async def send(mid: str, body: SendIn):
    """你在会议里说一句。待确认 / 群聊：开新一轮（@ 谁谁答，否则 Grava 请人）；进行中：只记下，还没发言的成员会看到；已结束：409。"""
    text = (body.text or "").strip()[:NOTE_MAX]
    if not text:
        raise HTTPException(400, L("空消息", "Empty message"))
    ts = now_iso()
    with _lock, mdb() as conn:
        m = get_row(conn, mid)
        if m["status"] == "ended":
            raise HTTPException(409, L("会议已结束", "The meeting has ended"))
        rid = add_message(conn, mid, "user", text, None, None, ts=ts)
        touch(conn, mid, ts)
        row = conn.execute("SELECT * FROM messages WHERE id=?", (rid,)).fetchone()
        ag = agent_rows(conn)
    msg = msg_json(row, ag)
    if busy_now(mid):
        lv = running(mid)
        if lv:
            lv.publish("message", msg)
        with _lock, mdb() as conn:
            return {"ok": True, "message": msg, "meeting": meeting_json(get_row(conn, mid), agent_rows(conn))}
    return {"ok": True, "message": msg, "meeting": await next_round(mid, text)}


@router.post("/api/meetings/{mid}/continue")
async def continue_(mid: str):
    """继续讨论：不补充，开下一轮（请纪要里 next 写的，没有就全体）。"""
    return {"ok": True, "meeting": await next_round(mid, None)}


@router.post("/api/meetings/{mid}/stop")
async def stop(mid: str):
    """停掉正在进行的一轮：在说的那位到此为止（标「已停止」），后面的不再请，不写纪要；会议变成待确认。"""
    lv = running(mid)
    if not lv:
        return {"ok": True, "stopped": False}
    lv.stopping = True
    lv.queue.clear()
    if lv.stream and not lv.stream.done():
        lv.stream.cancel()
    return {"ok": True, "stopped": True}


TODO_FIELDS = [{"key": "title", "label": ("待办", "To-do"), "type": "text", "required": True}, {"key": "due", "label": ("时间", "When"), "type": "datetime"},
               {"key": "meeting", "label": ("会议", "Meeting"), "type": "text"}, {"key": "done", "label": ("完成", "Done"), "type": "bool"}]


def board_add(agent: str, todos: list[dict], title: str) -> int:
    """确认的待办记进这个 Agent 看板上的「会议待办」：表没有就建，看板上没有用它的积木就加一块清单（Agent 一版，看板顶上能撤回）。"""
    import boards  # 延迟导入：boards 依赖 data / inbox
    ts = now_iso()
    fields = boards.clean_fields([{**f, "label": L(*f["label"])} for f in TODO_FIELDS])
    with _lock, boards.bdb() as conn:
        g = conn.execute("SELECT dashboard FROM groups WHERE id=?", (agent,)).fetchone()
        if g is None:
            return 0
        coll = conn.execute("SELECT status FROM collections WHERE agent=? AND name=?", (agent, TODO_COLL)).fetchone()
        if coll is None or coll["status"] not in ("active", "draft"):
            conn.execute("INSERT OR REPLACE INTO collections(agent, name, title, fields, status, created_at, updated_at) VALUES(?,?,?,?,?,?,?)",
                         (agent, TODO_COLL, L("会议待办", "Meeting to-dos"), dumps(fields), "active", ts, ts))
        elif coll["status"] == "draft":
            conn.execute("UPDATE collections SET status='active', updated_at=? WHERE agent=? AND name=?", (ts, agent, TODO_COLL))
        for td in todos:
            due = None
            if td.get("dates"):
                due = f"{td['dates'][0]}T{td.get('start') or '00:00'}"
                try:
                    due = datetime.fromisoformat(due).replace(tzinfo=TZ).isoformat(timespec="minutes")
                except ValueError:
                    due = None
            data = {"title": td["text"], "meeting": title, "done": False, **({"due": due} if due else {})}
            conn.execute("INSERT INTO records(id, agent, collection, data, source, created_at, updated_at) VALUES(?,?,?,?,?,?,?)",
                         (f"r-{uuid.uuid4().hex[:10]}", agent, TODO_COLL, dumps(data), "agent", ts, ts))
        blocks = boards.blocks_of(boards.live_row(conn, agent))
        if not any(TODO_COLL in boards.froms(q) for b in blocks for q in boards.queries_of(b)) and not any(b.get("id") == TODO_BLOCK for b in blocks):
            raw = {"id": TODO_BLOCK, "type": "checklist", "title": L("会议待办", "Meeting to-dos"),
                   "source": {"from": TODO_COLL, "where": [["done", "!=", True]], "sort": ["due"], "limit": 20},
                   "row": {"title": "title", "sub": ["{due} · {meeting}"]}, "check": "done", "empty": L("暂无会议待办", "No meeting to-dos")}
            try:
                block = boards.clean_block(conn, agent, raw, boards.anchors_of(g["dashboard"]))
                boards.put_live(conn, agent, blocks + [block], L(f"加了「会议待办」（会议「{title}」）", f'Added "Meeting to-dos" (meeting "{title}")'), "agent")
            except HTTPException as e:  # 积木没加上：行已经记下了，表在「它在记的数据」里看得到
                print(f"[meetings] {agent} 的看板没加上会议待办：{e.detail}")
    return len(todos)


@router.post("/api/meetings/{mid}/confirm")
async def confirm(mid: str):
    """确认这一轮的纪要：每条待办按日期写进日程（source = 负责的 Agent；有开始时间是日程，没有是截止），再记进那个 Agent 看板的「会议待办」。
    会议结束；开了「会后保留为群聊」的变成群聊。同一份纪要再点一次不重复写（日程的 key 也防重复）。"""
    with _lock, mdb() as conn:
        m = get_row(conn, mid)
    if busy_now(mid):
        raise HTTPException(409, L("本轮还在进行，结束后再确认", "This round is still running; confirm when it ends"))
    mn = loads(m["minutes"])
    if not mn:
        raise HTTPException(409, L("还没有纪要", "There are no minutes yet"))
    if mid in _confirming or (m["confirmed"] and (loads(m["confirmed"]) or {}).get("round") == mn.get("round")):
        with _lock, mdb() as conn:
            return {"ok": True, "meeting": meeting_json(get_row(conn, mid), agent_rows(conn)), "already": True}
    _confirming.add(mid)
    try:
        return await write_todos(m, mn)
    finally:
        _confirming.discard(mid)


async def write_todos(m, mn: dict) -> dict:
    import schedule as schedule_mod  # 延迟导入：schedule 依赖 study / sources
    mid = m["id"]
    with _lock, mdb() as conn:
        ag = agent_rows(conn)
    written, failed, board = 0, [], {}
    note = L(f"会议「{m['title']}」", f'Meeting "{m["title"]}"')
    for i, td in enumerate(mn.get("todos") or []):
        if td["agent"] not in ag:
            failed.append({"text": td["text"], "error": L("Agent 已删除", "The Agent was deleted")})
            continue
        for d in td.get("dates") or []:
            try:
                await schedule_mod.add_item(schedule_mod.ItemIn(title=td["title"], date=d, start=td.get("start"), end=td.get("end"),
                                                                kind="event" if td.get("start") else "deadline", note=note,
                                                                key=f"meeting:{mid}:{mn['round']}:{i}:{d}", source=td["agent"]))
                written += 1
            except HTTPException as e:
                failed.append({"text": f"{td['text']} · {d}", "error": str(e.detail)[:120]})
    by_agent: dict[str, list[dict]] = {}
    for td in mn.get("todos") or []:
        if td["agent"] in ag:
            by_agent.setdefault(td["agent"], []).append(td)
    for a, tds in by_agent.items():
        try:
            board[a] = await asyncio.to_thread(board_add, a, tds, m["title"])
        except Exception as e:  # noqa: BLE001 — 看板没记上不影响日程
            failed.append({"text": name_of(a, ag), "error": str(e)[:120]})
    ts = now_iso()
    todos_n = len(mn.get("todos") or [])
    done = {"at": ts, "round": mn["round"], "todos": todos_n, "schedule": written, "board": board, "failed": failed}
    new_status = "group" if m["keep"] else "ended"
    with _lock, mdb() as conn:
        conn.execute("UPDATE meetings SET confirmed=?, status=?, ended_at=?, news=news+1, updated_at=? WHERE id=?",
                     (dumps(done), new_status, ts if new_status == "ended" else None, ts, mid))
        system_line(conn, mid, "confirmed", round=mn["round"], todos=todos_n, failed=len(failed))
        system_line(conn, mid, "group" if new_status == "group" else "ended")
        touch(conn, mid, ts)
    log_activity(L(f"确认了会议「{m['title']}」的纪要：{todos_n} 项待办写入日程和看板", f'Confirmed the minutes of "{m["title"]}": {todos_n} to-dos added to the schedule and dashboards'),
                 "meeting")
    with _lock, mdb() as conn:
        return {"ok": True, "meeting": meeting_json(get_row(conn, mid), agent_rows(conn)), "confirmed": done}


@router.post("/api/meetings/{mid}/end")
async def end(mid: str):
    """结束会议（群聊也是）：在进行的一轮先停，归入「已结束的会议」。没确认的纪要不写进日程。"""
    lv = running(mid)
    if lv:
        await stop(mid)
        for _ in range(100):  # 等它收尾（最多 10 秒）
            if lv.done:
                break
            await asyncio.sleep(0.1)
    ts = now_iso()
    with _lock, mdb() as conn:
        m = get_row(conn, mid)
        if m["status"] != "ended":
            conn.execute("UPDATE meetings SET status='ended', ended_at=?, news=news+1, updated_at=? WHERE id=?", (ts, ts, mid))
            system_line(conn, mid, "ended")
            touch(conn, mid, ts)
        out = meeting_json(get_row(conn, mid), agent_rows(conn))
    if m["status"] != "ended":
        log_activity(L(f"结束了会议「{m['title']}」", f'Ended the meeting "{m["title"]}"'), "meeting")
    return {"ok": True, "meeting": out}


class MeetingPatch(BaseModel):
    keep: bool | None = None
    title: str | None = None


@router.patch("/api/meetings/{mid}")
def patch(mid: str, body: MeetingPatch):
    """改会议名、「会后保留为群聊」。已结束的打开 keep = 重新开成群聊；群聊关掉 keep = 结束。"""
    ts = now_iso()
    with _lock, mdb() as conn:
        m = get_row(conn, mid)
        if body.title is not None:
            title = clean_text(body.title, TITLE_MAX)
            if not title:
                raise HTTPException(400, L("会议名不能空", "The name can't be empty"))
            conn.execute("UPDATE meetings SET title=?, updated_at=? WHERE id=?", (title, ts, mid))
        if body.keep is not None:
            conn.execute("UPDATE meetings SET keep=?, updated_at=? WHERE id=?", (int(body.keep), ts, mid))
            if body.keep and m["status"] == "ended":
                conn.execute("UPDATE meetings SET status='group', ended_at=NULL WHERE id=?", (mid,))
                system_line(conn, mid, "reopened")
            elif not body.keep and m["status"] == "group":
                conn.execute("UPDATE meetings SET status='ended', ended_at=? WHERE id=?", (ts, mid))
                system_line(conn, mid, "ended")
        touch(conn, mid, ts)
        return {"ok": True, "meeting": meeting_json(get_row(conn, mid), agent_rows(conn))}


@router.delete("/api/meetings/{mid}")
async def delete(mid: str):
    """删掉会议的记录（消息、纪要、成员看到哪了）和各成员在会里的会话。已确认写进日程和看板的待办留着。"""
    lv = running(mid)
    if lv:
        await stop(mid)
    with _lock, mdb() as conn:
        m = get_row(conn, mid)
        conn.execute("DELETE FROM messages WHERE thread=?", (mid,))
        if m["from_thread"]:  # 主对话里「会议已结束，以下 N 项待办……」那几条通知（没有纪要卡了，留着看不懂）
            gone = [r["id"] for r in conn.execute("SELECT id, meta FROM messages WHERE thread=? AND origin='meeting'", (m["from_thread"],))
                    if (loads(r["meta"], {}) or {}).get("meeting") == mid]
            if gone:
                conn.execute(f"DELETE FROM messages WHERE id IN ({','.join('?' * len(gone))})", gone)  # noqa: S608
        conn.execute("DELETE FROM meeting_seen WHERE meeting=?", (mid,))
        conn.execute("DELETE FROM threads WHERE id=?", (mid,))
        conn.execute("DELETE FROM meetings WHERE id=?", (mid,))
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='read_marks'").fetchone():
            conn.execute("DELETE FROM read_marks WHERE thread=?", (mid,))
    LIVE.pop(mid, None)
    if claw.is_openclaw():  # 各成员在会里的会话也删掉（OpenClaw 会压缩存档一份，不是彻底抹掉）：一个一个在后台删，每次要起一个 openclaw 进程
        spawn(drop_sessions([member_key(a, mid) for a in loads(m["members"], [])]))
    log_activity(L(f"删除了会议「{m['title']}」的记录", f'Deleted the records of the meeting "{m["title"]}"'), "deleted")
    return {"ok": True}


async def drop_sessions(keys: list[str]) -> None:
    for k in keys:
        try:
            await chat.gateway_call("sessions.delete", {"key": k}, timeout=20)
        except Exception:  # noqa: BLE001 — 没发过言的成员没有会话
            pass


@router.get("/api/meetings/{mid}")
def get_meeting(mid: str):
    """会议 + 全部消息（旧的在前，最多 500 条）+ 正在进行的一轮（live：谁在说、说到哪、后面还有谁）。"""
    with _lock, mdb() as conn:
        m = repair(conn, get_row(conn, mid))
        ag = agent_rows(conn)
        rows = conn.execute("SELECT * FROM messages WHERE thread=? ORDER BY id DESC LIMIT 500", (mid,)).fetchall()
        return {"ok": True, "meeting": meeting_json(m, ag), "messages": [msg_json(r, ag) for r in reversed(rows)]}


@router.get("/api/meetings/{mid}/stream")
async def stream(mid: str):
    """接上正在进行的一轮（SSE）；刚结束的（15 分钟内）也接得上，直接收到 done；没有就 204。"""
    lv = LIVE.get(mid)
    if not lv:
        return StreamingResponse(iter(()), status_code=204)
    return StreamingResponse(attach(lv), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


# —— 主对话：会议卡、纪要卡、前情 ————————————————————————————————————————————

def meeting_card(r, ag: dict[str, dict]) -> dict:
    """主对话里的会议卡（kind meeting）：发起它的那条回复下面，标题、成员、状态，点开进会议。"""
    return {"kind": "meeting", "id": f"mt-{r['id']}", "meeting": r["id"], "thread": r["from_thread"], "messageId": r["message_id"],
            "createdAt": r["created_at"], "title": r["title"], "members": [member_json(a, ag) for a in loads(r["members"], [])],
            "status": r["status"], "statusLabel": L(*STATUS_LABEL.get(r["status"], (r["status"], r["status"]))), "round": r["round"]}


def cards_for(thread: str, lo: str, hi: str) -> list[dict]:
    """给 /api/chat/cards：这个对话这一天发起的会议卡 + 纪要通知下面的纪要卡（kind minutes：待办、确认 / 查看会议；确认过的是回执）。"""
    with _lock, mdb() as conn:
        ag = agent_rows(conn)
        # 按真时间比（时区改过偏移就不一样，见 chat.day_bounds）
        out = [meeting_card(r, ag) for r in conn.execute("SELECT * FROM meetings WHERE from_thread=? AND julianday(created_at)>=julianday(?) "
                                                         "AND julianday(created_at)<julianday(?) ORDER BY julianday(created_at)", (thread, lo, hi))]
        notes = conn.execute("SELECT id, ts, meta FROM messages WHERE thread=? AND origin='meeting' AND julianday(ts)>=julianday(?) "
                             "AND julianday(ts)<julianday(?) ORDER BY id", (thread, lo, hi)).fetchall()
        for n in notes:
            meta = loads(n["meta"], {}) or {}
            mid, rnd = meta.get("meeting"), meta.get("round")
            m = conn.execute("SELECT * FROM meetings WHERE id=?", (mid,)).fetchone() if mid else None
            if m is None:
                continue
            mn = None
            for r in conn.execute("SELECT meta FROM messages WHERE thread=? AND speaker=? AND role='grava' ORDER BY id DESC", (mid, HOST)):
                mm = loads(r["meta"], {}) or {}
                if mm.get("kind") == "minutes" and mm.get("round") == rnd:
                    mn = mm.get("minutes")
                    break
            done = loads(m["confirmed"]) or {}
            state = "confirmed" if done.get("round") == rnd else "superseded" if rnd != m["round"] else \
                "ended" if m["status"] in ("ended", "group") else "pending"
            out.append({"kind": "minutes", "id": f"mn-{mid}-{rnd}", "meeting": mid, "thread": thread, "messageId": n["id"], "createdAt": n["ts"],
                        "title": m["title"], "round": rnd, "status": state, "decisions": (mn or {}).get("decisions") or [],
                        "todos": [todo_json(td, ag) for td in (mn or {}).get("todos") or []], "open": (mn or {}).get("open") or [],
                        "confirmed": done if state == "confirmed" else None})
    return out


def link_run(run: chat.Run) -> None:
    """一次回复结束（cards.on_run_end）：这次回复里从这个对话发起的会议挂到这条回复下面。"""
    if run.reply_id is None:
        return
    with _lock, mdb() as conn:
        conn.execute("UPDATE meetings SET message_id=? WHERE from_thread=? AND message_id IS NULL AND created_at>=?",
                     (run.reply_id, run.thread, run.started))


def context_for(thread: str) -> str | None:
    """主对话下一次说话前：它发起的会议有了新纪要、确认或结束，还没告诉过它的，带在前情里（只带一次）。"""
    try:
        with _lock, mdb() as conn:
            rows = conn.execute("SELECT * FROM meetings WHERE from_thread=? AND news>told ORDER BY updated_at", (thread,)).fetchall()
            if not rows:
                return None
            ag = agent_rows(conn)
            conn.execute("UPDATE meetings SET told=news WHERE from_thread=? AND news>told", (thread,))
    except Exception:  # noqa: BLE001 — 带不上只是它少知道一件事
        return None
    parts = []
    for r in rows:
        mn = loads(r["minutes"]) or {}
        done = loads(r["confirmed"]) or {}
        state = LS("已确认，待办已写进日程和各 Agent 的看板", "confirmed; the to-dos are in the schedule and the Agents' dashboards") if done.get("round") == mn.get("round") and done \
            else LS("会议已结束，纪要没有确认", "ended without confirming the minutes") if r["status"] == "ended" \
            else LS(f"等{user_word()}确认", "waiting for the user to confirm")
        parts.append(LS(f"会议「{r['title']}」（{r['id']}）第 {mn.get('round') or r['round']} 轮纪要，{state}：\n{minutes_text(mn, ag, LS)}",
                        f"Meeting \"{r['title']}\" ({r['id']}), minutes of round {mn.get('round') or r['round']}, {state}:\n{minutes_text(mn, ag, LS)}"))
    return LS(f"（{settings.app_name} 给你的说明，{user_word()}看不到）你发起的会议有了结果：\n\n", f"({settings.app_name}'s note to you; the user doesn't see it) "
              "Meetings you started have news:\n\n") + "\n\n".join(parts)


def tracked(conn) -> list[str]:
    """给 unread.tracked：没结束的会议和群聊（传进来的 conn 已经拿着锁）。"""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='meetings'").fetchone():
        return []
    return [r["id"] for r in conn.execute("SELECT id FROM meetings WHERE status!='ended' ORDER BY created_at")]


def busy() -> list[str]:
    """正在进行的一轮（safe_restart 等它们说完再重启）。"""
    return [mid for mid, lv in LIVE.items() if not lv.done]


# —— 给对话列表（chats.py，对话页那条线）——————————————————————————————————————————

def chat_rows() -> list[dict]:
    """对话列表里的会议行：{id, kind: meeting | room（保留下来的 Agent 群）, title, members, ended, state, line, lineRole, at, unread}。
    state：working = 一轮正在进行；waiting = 纪要等你确认（列表上是琥珀点）。line：待确认时写「纪要待确认 · N 项待办」，
    正在进行时写谁在发言，否则最后一条（「谁：说了什么」）。at（排序）和 unread 算你的话、成员的发言和纪要，系统行不算。"""
    with _lock, mdb() as conn:
        ag = agent_rows(conn)
        marks = {}
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='read_marks'").fetchone():
            marks = {r["thread"]: r["last_read"] for r in conn.execute("SELECT thread, last_read FROM read_marks WHERE thread LIKE 'grp-%'")}
        out = []
        for r in conn.execute("SELECT * FROM meetings ORDER BY created_at"):
            r = repair(conn, r)
            last = conn.execute("SELECT id, role, text, ts, speaker, meta FROM messages WHERE thread=? AND role IN ('user','grava') ORDER BY id DESC LIMIT 1",
                                (r["id"],)).fetchone()
            unread = conn.execute("SELECT COUNT(*) FROM messages WHERE thread=? AND role='grava' AND id>?", (r["id"], marks.get(r["id"]) or 0)).fetchone()[0]
            lv = running(r["id"])
            mn = loads(r["minutes"]) or {}
            if needs_you(r):
                n = len(mn.get("todos") or [])
                line = L(f"纪要待确认 · {n} 项待办", f"Minutes to confirm · {n} to-do{'' if n == 1 else 's'}") if n else L("纪要待确认", "Minutes to confirm")
            elif lv and lv.phase == "minutes":
                line = L(f"{settings.app_name} 正在整理纪要", f"{settings.app_name} is writing the minutes")
            elif lv and lv.speaker:
                line = L(f"{name_of(lv.speaker, ag)} 正在发言", f"{name_of(lv.speaker, ag)} is speaking")
            elif last is not None:
                meta = loads(last["meta"], {}) or {}
                who = L("你", "You") if last["role"] == "user" else name_of(last["speaker"], ag)
                text = L("纪要", "Minutes") if meta.get("kind") == "minutes" else " ".join((last["text"] or "").split())[:80]
                line = f"{who}{L('：', ': ')}{text}"
            else:
                line = r["topic"][:80]
            out.append({"id": r["id"], "kind": "room" if r["status"] == "group" else "meeting", "title": r["title"], "members": loads(r["members"], []),
                        "ended": r["status"] == "ended", "state": "working" if lv else "waiting" if needs_you(r) else None, "line": line,
                        "lineRole": last["role"] if last is not None else None, "at": last["ts"] if last is not None else r["created_at"],
                        "unread": 0 if r["status"] == "ended" else unread})  # 结束的不算未读（和 unread.tracked 一致）
    return out


async def delete_meeting(mid: str) -> None:
    """对话列表左滑「删除」（chats.py）：和 DELETE /api/meetings/{id} 一样，找不到抛 404。"""
    await delete(mid)
