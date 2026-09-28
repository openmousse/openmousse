"""推送通知（Expo Push → APNs / FCM）：回复、建议卡、收件箱、起床报告都从这里推，锁屏也能看到，点开直接到对应的地方。

- 手机端 app 启动并连上后把 Expo push token 交到 /api/push/register，存 grava.db 的 push_tokens。
- 三个档位 level：ring 响铃（有声音、高优先级、interruptionLevel active）；quiet 静默（不出声，进通知中心，passive）；none 不推。
  server.json 的 push.quiet_hours（默认 ["23:00", "07:30"]，按 timezone）里 ring 自动降成 quiet。
- 谁用哪档：用户发的消息回完 → ring；主对话转给 Agent（relay）→ none；系统触发（/api/chat/trigger）→ 触发方给的 level，默认 quiet；
  学习台 → none；收件箱新条目 → 条目的 level；收件箱做完 / 没做成 → quiet；从 app 派的后台任务做完 / 没做成 / 到点停了、
  「改一下」的一轮做完 → quiet（cards.py）。
- 回复结束（chat.py run_gateway 末尾）→ notify_run()：这次回复里写了建议卡（feed_items 新行、group_id 是这个线程）就推卡片
  （标题 = Agent 名，副标题 = 卡的类型，正文 = 卡标题 · 第一条要点），否则推回复的开头（preview：去掉 Markdown，按句子截断）。
- data 永远带 thread（老版本 app 只认它）和 target（新版按它跳）：{type: thread, thread} | {type: card, id, thread} | {type: inbox, id} | {type: today}；
  还带 level（实际用的档位，算过静默时段：ring / quiet；app 只在 ring 时弹应用内横幅）和 kind（reply 回复 / card 卡片 / inbox 收件箱新条目 /
  done 收件箱做完或没做成 / report 系统主动推的，比如起床报告、ddl 提醒，即 /api/push/send）。
- badge（app 图标角标）= 收件箱待你点头 + 给你的未读回复（见 unread.py）；算不出来就不带，推送照发。
- collapseId（≤ 64 字节）：同一件事的新通知替换旧的（同一线程的回复、同一张卡、同一条收件箱）。
- app 在前台且开着这个对话时，自己把通知压掉（见 src/api/push.ts）。Expo 那边的 DeviceNotRegistered 会把 token 标为失效。
消息字段见 https://docs.expo.dev/push-notifications/sending-notifications/ 的 message request format。
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from chat import LEVELS, Run, _lock, db, now_iso
from config import TZ, raw, settings
from i18n import L

router = APIRouter()
EXPO_PUSH = "https://exp.host/--/api/v2/push/send"
TARGETS = ("thread", "card", "inbox", "today", "board")  # 点开去哪（board：某个 Agent 的看板，Agent 的提醒用；老版本 app 不认，按 thread 进它的对话）
QUIET_DEFAULT = ("23:00", "07:30")
BODY_MAX = 180  # 通知正文的字数上限（整条推送的 payload 限 4 KB）
PREVIEW_WIDTH = 110  # 回复预览的显示宽度（汉字算 2）：锁屏上两三行


def pdb() -> sqlite3.Connection:
    conn = db()
    conn.execute("""CREATE TABLE IF NOT EXISTS push_tokens (token TEXT PRIMARY KEY, platform TEXT, created_at TEXT NOT NULL,
        last_seen TEXT NOT NULL, disabled INTEGER NOT NULL DEFAULT 0, note TEXT)""")
    return conn


class Register(BaseModel):
    token: str
    platform: str = "ios"


@router.post("/api/push/register")
def register(body: Register):
    tok = body.token.strip()
    if not (tok.startswith("ExponentPushToken[") or tok.startswith("ExpoPushToken[")):
        return {"ok": False, "error": L("不是 Expo push token", "Not an Expo push token")}
    ts = now_iso()
    with _lock, pdb() as conn:
        conn.execute("""INSERT INTO push_tokens(token, platform, created_at, last_seen, disabled) VALUES(?,?,?,?,0)
            ON CONFLICT(token) DO UPDATE SET last_seen=excluded.last_seen, disabled=0, platform=excluded.platform""", (tok, body.platform, ts, ts))
    return {"ok": True}


@router.get("/api/push/status")
def status():
    with _lock, pdb() as conn:
        rows = conn.execute("SELECT platform, last_seen, disabled, note FROM push_tokens ORDER BY last_seen DESC").fetchall()
    return {"ok": True, "devices": [{"platform": r["platform"], "lastSeen": r["last_seen"], "disabled": bool(r["disabled"]), "note": r["note"]} for r in rows],
            "quietHours": list(quiet_hours_text() or []), "quietNow": in_quiet_hours()}


class TestBody(BaseModel):
    title: str = ""
    body: str = ""  # 空 = 默认的测试文字（按请求语言）


@router.post("/api/push/test")
async def test(body: TestBody):
    text = body.body or L("测试推送：收到就说明通了。", "Test notification: if you can see this, push works.")
    return await send_push(body.title or settings.app_name, text, {"thread": "main"}, level="ring", kind="report")


class SendBody(BaseModel):
    title: str
    body: str
    thread: str = "today"          # 点开去哪（老版本 app 只认它）：某个线程 id，或 today（「今天」页）
    thread_id: str | None = None   # iOS 通知分组，不给 = thread
    subtitle: str | None = None
    level: str = "ring"            # ring 响铃 / quiet 静默 / none 不推（默认 ring，和以前一样；静默时段里自动降成 quiet）
    category: str | None = None    # 通知类别（app 注册的 categoryId）
    collapse: str | None = None    # 同一个 collapse 的新通知替换旧的
    target: dict | None = None     # 新版 app 按它跳：{type: thread|card|inbox|today, …}；不给按 thread 推


@router.post("/api/push/send")
async def send(body: SendBody):
    """系统主动推一条（起床报告、ddl 提醒等，suggestion_watcher 用）。"""
    if body.level not in LEVELS:
        raise HTTPException(400, L("level 只能是 ring / quiet / none", "level must be ring, quiet or none"))
    if body.target is not None and body.target.get("type") not in TARGETS:
        raise HTTPException(400, L(f"target.type 只能是 {' / '.join(TARGETS)}", f"target.type must be one of {', '.join(TARGETS)}"))
    data = {"thread": body.thread, **({"target": body.target} if body.target else {})}
    return await send_push(body.title, body.body, data, thread_id=body.thread_id or body.thread, subtitle=body.subtitle, level=body.level,
                           category=body.category, collapse=body.collapse, kind="report")


def active_tokens() -> list[str]:
    with _lock, pdb() as conn:
        return [r["token"] for r in conn.execute("SELECT token FROM push_tokens WHERE disabled=0")]


# —— 档位与静默时段 ——————————————————————————————————————————————

def _hm(value) -> tuple[int, int] | None:
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", str(value).strip())
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        return None
    return int(m.group(1)), int(m.group(2))


def quiet_hours() -> tuple[tuple[int, int], tuple[int, int]] | None:
    """server.json 的 push.quiet_hours：[开始, 结束]（HH:MM，按 timezone，可以跨午夜）。null 或 [] = 不设；写错了按默认。每次读文件，不用重启。"""
    p = raw().get("push")
    q = (p if isinstance(p, dict) else {}).get("quiet_hours", list(QUIET_DEFAULT))
    if not q:
        return None
    a = _hm(q[0]) if isinstance(q, (list, tuple)) and len(q) == 2 else None
    b = _hm(q[1]) if a else None
    if not a or not b:
        a, b = _hm(QUIET_DEFAULT[0]), _hm(QUIET_DEFAULT[1])
    return (a, b) if a != b else None


def quiet_hours_text() -> tuple[str, str] | None:
    q = quiet_hours()
    return (f"{q[0][0]:02d}:{q[0][1]:02d}", f"{q[1][0]:02d}:{q[1][1]:02d}") if q else None


def in_quiet_hours(now: datetime | None = None) -> bool:
    q = quiet_hours()
    if not q:
        return False
    now = now or datetime.now(TZ)
    t, (start, end) = (now.hour, now.minute), q
    return (t >= start or t < end) if start > end else (start <= t < end)


def effective_level(level: str | None, now: datetime | None = None) -> str:
    """实际用的档位：不认识的按 ring；静默时段里 ring → quiet。"""
    lv = level if level in LEVELS else "ring"
    return "quiet" if lv == "ring" and in_quiet_hours(now) else lv


# —— 拼消息、发 ——————————————————————————————————————————————————

def clip(text: str, n: int) -> str:
    text = text or ""
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def collapse_id(key: str) -> str:
    """Expo 的 collapseId 最长 64 字节（APNs 的 apns-collapse-id）；太长就截短再加哈希，同一个 key 永远得到同一个 id。"""
    b = key.encode("utf8")
    if len(b) <= 64:
        return key
    return b[:47].decode("utf8", "ignore") + "~" + hashlib.sha1(b).hexdigest()[:16]


def with_target(data: dict | None) -> dict:
    """data 里总要有 thread（老版本 app 只认它）和 target（新版按它跳），缺哪个用另一个补。收件箱、「今天」的 thread 是 today。"""
    d = dict(data or {})
    thread = d.get("thread") if isinstance(d.get("thread"), str) and d.get("thread") else None
    target = d.get("target") if isinstance(d.get("target"), dict) and d["target"].get("type") in TARGETS else None
    if target is None:
        target = {"type": "today"} if thread in (None, "today") else {"type": "thread", "thread": thread}
    if thread is None:
        thread = target.get("thread") if target["type"] in ("thread", "card") and target.get("thread") else "today"
    d["thread"], d["target"] = thread, target
    return d


def build_message(token: str, title: str, body: str, data: dict, *, level: str, subtitle: str | None = None, thread_id: str | None = None,
                  category: str | None = None, collapse: str | None = None, badge: int | None = None) -> dict:
    """一条 Expo 推送消息。level 是算过静默时段之后的：ring 带 sound、high、active；quiet 不带 sound、normal、passive（不亮屏不出声）。"""
    msg: dict = {"to": token, "title": title, "body": clip(body, BODY_MAX), "data": data}
    if subtitle:
        msg["subtitle"] = clip(subtitle, 60)
    if level == "ring":
        msg.update(sound="default", priority="high", interruptionLevel="active")
    else:
        msg.update(priority="normal", interruptionLevel="passive")
    if thread_id:
        msg["threadId"] = thread_id
    if category:
        msg["categoryId"] = category
    if collapse:
        msg["collapseId"] = collapse_id(collapse)
    if badge is not None:
        msg["badge"] = max(0, int(badge))
    return msg


async def badge_count() -> int | None:
    """app 图标上的数：收件箱待你点头 + 给你的未读回复。算不出来就不带角标，不能让推送失败。"""
    try:
        import unread  # 延迟导入：unread.py 依赖本模块
        return await unread.badge()
    except Exception:  # noqa: BLE001
        return None


async def post_expo(msgs: list[dict]) -> dict:
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(EXPO_PUSH, json=msgs, headers={"Accept": "application/json", "Content-Type": "application/json"})
    return r.json()


async def send_push(title: str, body: str, data: dict | None = None, thread_id: str | None = None, *, subtitle: str | None = None,
                    level: str = "ring", category: str | None = None, collapse: str | None = None, badge: int | None = None,
                    kind: str = "report") -> dict:
    """推给所有注册过的设备。level：ring / quiet / none（静默时段里 ring 自动降成 quiet）。
    data 会补上 thread、target、level（实际档位）、kind（reply / card / inbox / done / report）；badge 不给就按未读算。"""
    lv = effective_level(level)
    if lv == "none":
        return {"ok": True, "sent": 0, "level": "none", "skipped": True}
    try:  # 冥想时间：一律不推，记下来，结束时给小结（见 think.py）
        import think  # 延迟导入：think.py 依赖 chat
        if think.hold_push(title, body, data, level=lv, kind=kind, subtitle=subtitle):
            return {"ok": True, "sent": 0, "level": lv, "held": True}
    except Exception:  # noqa: BLE001 — 查不了就照常推
        pass
    tokens = active_tokens()
    if not tokens:
        return {"ok": False, "sent": 0, "level": lv, "error": L("没有注册的设备", "No registered devices")}
    if badge is None:
        badge = await badge_count()
    payload = {**with_target(data), "level": lv, "kind": kind}
    msgs = [build_message(t, title or settings.app_name, body, payload, level=lv, subtitle=subtitle, thread_id=thread_id,
                          category=category, collapse=collapse, badge=badge) for t in tokens]
    try:
        res = await post_expo(msgs)
    except (httpx.HTTPError, ValueError) as exc:
        return {"ok": False, "sent": 0, "level": lv, "error": str(exc)[:200]}
    tickets = (res.get("data") or []) if isinstance(res, dict) else []
    tickets = [t for t in (tickets if isinstance(tickets, list) else [tickets]) if isinstance(t, dict)]
    bad = []
    for tok, tk in zip(tokens, tickets):
        if tk.get("status") == "error":
            err = (tk.get("details") or {}).get("error") or tk.get("message")
            if err == "DeviceNotRegistered":
                bad.append((tok, err))
    if bad:
        with _lock, pdb() as conn:
            for tok, err in bad:
                conn.execute("UPDATE push_tokens SET disabled=1, note=? WHERE token=?", (err, tok))
    return {"ok": True, "sent": sum(1 for t in tickets if t.get("status") == "ok"), "level": lv,
            "errors": [t for t in tickets if t.get("status") == "error"][:3]}


def thread_title(thread: str) -> str:
    if thread == "main":
        return settings.app_name
    with _lock, db() as conn:
        try:
            r = conn.execute("SELECT name FROM groups WHERE id=?", (thread,)).fetchone()
            if r:
                return f"{settings.app_name} · {r['name']}"
            r = conn.execute("SELECT title FROM side_chats WHERE id=?", (thread,)).fetchone()
            if r:
                return f"{settings.app_name} · {r['title']}"
        except sqlite3.Error:
            pass
    if thread.startswith("tp-"):  # 思考主题
        import think  # 延迟导入
        name = think.topic_title(thread)
        if name:
            return f"{settings.app_name} · {name}"
    return settings.app_name


# —— 正文：回复预览（去 Markdown、按句子截断） ——————————————————————————————

CJK = re.compile(r"[\u1100-\u115f\u2e80-\u303e\u3041-\u33ff\u3400-\u4dbf\u4e00-\u9fff\ua000-\ua4cf\uac00-\ud7a3\uf900-\ufaff"
                 r"\ufe30-\ufe4f\uff00-\uff60\uffe0-\uffe6\U00020000-\U0003fffd]")
SENTENCE_END = re.compile(r"[。！？!?…]+[”’」』）)]*|\.(?=\s)")
CLAUSE_STOPS = (re.compile(r"[；;]"), re.compile(r"[，,、]"), re.compile(r"[：:]|\s"))  # 句末之后，按这个顺序找断点
PUNCT_END = "。！？!?；;：:，,、…）)」』”"


def width(text: str) -> int:
    """显示宽度：中日韩文字和全角符号算 2，其余算 1。"""
    return sum(2 if CJK.match(ch) else 1 for ch in text)


def _inline(s: str) -> str:
    """一行里的 Markdown 记号去掉，留文字：图片 / 链接留文字，行内代码、粗体、斜体、删除线留内容。"""
    s = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", s)
    s = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", s)
    s = re.sub(r"\[([^\]]+)\]\[[^\]]*\]", r"\1", s)
    s = re.sub(r"<(https?://[^>\s]+)>", r"\1", s)
    s = re.sub(r"<br\s*/?>", " ", s, flags=re.I)
    s = re.sub(r"`+([^`]*)`+", r"\1", s)
    s = re.sub(r"(\*\*|__)(.+?)\1", r"\2", s)
    s = re.sub(r"(?<!\*)\*(?![\s*])(.+?)(?<![\s*])\*(?!\*)", r"\1", s)  # *斜体*（中文里常贴着字写）
    s = re.sub(r"(?<![\w])_(?!\s)(.+?)(?<!\s)_(?![\w])", r"\1", s)  # _斜体_：两边不能是字母数字（snake_case 不动）
    s = re.sub(r"~~(.+?)~~", r"\1", s)
    return s.strip()


def md_lines(text: str, headings: bool = True) -> list[str]:
    """Markdown → 纯文字的行，空行（""）表示分段：代码块整段去掉，标题单独成段（headings=False 就去掉），引用 > / 列表符号 / 分隔线去掉，
    表格变成「格 · 格」（表头行和 |---| 去掉），链接、图片、粗体、斜体留文字。"""
    t = re.sub(r"```.*?(```|$)", "\n\n", (text or "").replace("\r\n", "\n"), flags=re.S)
    lines = t.split("\n")
    out: list[str] = []
    for i, ln in enumerate(lines):
        s = ln.strip()
        if re.fullmatch(r"\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?", s):  # 表格分隔线：上一行是表头，一起去掉
            if out and i > 0 and lines[i - 1].strip().startswith("|"):
                out.pop()
            continue
        if re.fullmatch(r"([-*_]\s*){3,}", s):  # 分隔线 = 分段
            out.append("")
            continue
        if re.match(r"#{1,6}\s", s):  # 标题：单独一段
            out += ["", _inline(re.sub(r"^#{1,6}\s+", "", s)), ""] if headings else [""]
            continue
        if s.startswith("|") and s.endswith("|") and len(s) > 1:
            s = " · ".join(c.strip() for c in s.strip("|").split("|") if c.strip())
        s = re.sub(r"^(>\s?)+", "", s)
        s = re.sub(r"^([-*+•]|\d{1,3}[.)])\s+(\[[ xX]\]\s+)?", "", s)
        out.append(_inline(s))
    return out


def _join(a: str, b: str, sep_cjk: str = "；", sep: str = "; ") -> str:
    """两段文字接起来：前一段以标点结尾就直接接（中文不加空格），否则加分隔（同一段里的列表项用分号，标题和下一段用 · ）。"""
    if not a:
        return b
    cjk = bool(CJK.match(a[-1]) or CJK.match(b[0]))
    if a[-1] in PUNCT_END:
        return a + ("" if cjk else " ") + b
    return a + (sep_cjk if cjk else sep) + b


def paragraphs(text: str) -> list[str]:
    paras, cur = [], ""
    for ln in md_lines(text) + [""]:
        ln = re.sub(r"\s+", " ", ln).strip()
        if ln:
            cur = _join(cur, ln)
        elif cur:
            paras.append(cur)
            cur = ""
    return paras


def _prefix(text: str, limit: int) -> str:
    w = 0
    for i, ch in enumerate(text):
        w += 2 if CJK.match(ch) else 1
        if w > limit:
            return text[:i]
    return text


def cut(text: str, limit: int = PREVIEW_WIDTH) -> str:
    """截到显示宽度 limit 以内：优先在句末（。！？. ）断，其次在逗号、分号、空格断（加 …），都不合适就硬截（加 …）。"""
    if width(text) <= limit:
        return text
    head = _prefix(text, limit - 1)
    ends = [m.end() for m in SENTENCE_END.finditer(text) if m.end() <= len(head)]  # 在全文上找（英文句号要看后面是不是空格）
    if ends and ends[-1] >= len(head) * 0.5:
        return head[: ends[-1]].rstrip()
    for stop in CLAUSE_STOPS:  # 分号 > 逗号顿号 > 冒号空格
        at = [m.start() for m in stop.finditer(head)]
        if at and at[-1] >= len(head) * 0.5:
            return head[: at[-1]].rstrip(" ；;，,、：:") + "…"
    return head.rstrip() + "…"


def preview(text: str, limit: int = PREVIEW_WIDTH) -> str:
    """推送和未读里显示的回复开头：去掉 Markdown，合并空白，优先第一段（第一段太短就接上后面的），在句子边界截到约 110 个英文字符宽
    （汉字算 2，约 55 个字）。永远不返回空字符串。"""
    paras = paragraphs(text)
    if not paras:  # 只有代码块之类：退回原文去掉反引号
        paras = [p for p in (re.sub(r"\s+", " ", x).strip() for x in re.sub(r"`{3}[^\n]*", "", text or "").split("\n\n")) if p]
    if not paras:
        return L("回复好了。", "Reply ready.")
    out = paras[0]
    for p in paras[1:]:
        if width(out) >= 30:
            break
        out = _join(out, p, " · ", " · ")
    return cut(out, limit) or L("回复好了。", "Reply ready.")


# —— 回复结束：推卡片还是推回复 ———————————————————————————————————————————

def run_level(run: Run) -> str:
    """回完这次推送的档位：旧开关 notify=False 或学习台 → none；指定了 level 按它；否则 user → ring，relay → none，auto → quiet。"""
    if not run.notify or run.thread.startswith("study-"):
        return "none"
    if run.level in LEVELS:
        return run.level
    return {"user": "ring", "relay": "none"}.get(run.origin, "quiet")


def card_label(kind: str | None) -> str:
    """卡片推送的副标题。"""
    return {"meal_plan": L("三餐建议", "Meal plan"), "training_plan": L("训练建议", "Training plan"),
            "training_review": L("练后总结", "Workout review"), "sleep_report": L("睡眠", "Sleep"),
            "reminder": L("提醒", "Reminder")}.get(kind or "", L("新卡片", "New card"))


def first_line(body: str) -> str:
    """正文里第一行有内容的文字（去掉 Markdown；标题只在没有别的内容时才用）。"""
    for headings in (False, True):
        for ln in md_lines(body or "", headings=headings):
            ln = re.sub(r"\s+", " ", ln).strip()
            if width(ln) >= 4:
                return ln
    return ""


def card_line(kind: str | None, body: str, data) -> str:
    """卡片的第一条要点：三餐建议 → 下一餐吃什么；训练建议 → 时间和强度；其余用正文第一行，再不行用 data.why。"""
    d = data if isinstance(data, dict) else {}
    if kind == "meal_plan":
        meals = [m for m in d.get("meals") or [] if isinstance(m, dict)]
        if meals:
            m = meals[0]  # 卡里只有还没吃的餐，第一顿就是下一顿
            names = L("、", ", ").join(str(i["name"]) for i in (m.get("items") or [])[:3] if isinstance(i, dict) and i.get("name"))
            head = " ".join(str(x) for x in (m.get("label"), m.get("time")) if x)
            line = L("：", ": ").join(x for x in (head, names or str(m.get("note") or "")) if x)
            if line and m.get("kcal"):
                line += L(f"（{m['kcal']} kcal）", f" ({m['kcal']} kcal)")
            if line:
                return line
    if kind == "training_plan":
        parts = [str(d[k]) for k in ("time", "intensity") if d.get(k)]
        if parts and not str(d.get("decision") or "").startswith(("休息", "Rest")):
            return " · ".join(parts)
    return first_line(body) or str(d.get("why") or d.get("summary") or "")


# —— 通知展开成卡片（app 1.0.5 的通知内容扩展，targets/notify）———————————————————————
# data.card 只放画卡片要的几样，键名很短（整条推送 4 KB）：k 类型、t 标题、s 数字 [[标签, 值], …]（最多 3 个）、
# r 进度环 [值, 满值, 标签]、l 要点（最多 5 条）、f 脚注、c 颜色（Agent 的颜色名或 #RRGGBB）。老版本 app 不认这个字段，照旧。

def _num(v) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return str(int(f)) if f == int(f) else f"{f:.1f}"


def rich_card(card: dict, tint: str | None = None) -> dict:
    """一张建议卡 → 通知展开时画的卡。认得三餐建议和训练建议，其余用正文的前几行。"""
    kind = card.get("kind")
    d = card.get("data") if isinstance(card.get("data"), dict) else {}
    out: dict = {"k": card_label(kind), "t": clip(str(card.get("title") or ""), 60)}
    lines: list[str] = []
    stats: list[list[str]] = []
    if kind == "meal_plan":
        for m in (d.get("meals") or [])[:4]:
            if not isinstance(m, dict):
                continue
            names = L("、", ", ").join(str(i["name"]) for i in (m.get("items") or [])[:3] if isinstance(i, dict) and i.get("name"))
            head = " ".join(str(x) for x in (m.get("label"), m.get("time")) if x)
            tail = L(f"（{m['kcal']} kcal）", f" ({m['kcal']} kcal)") if m.get("kcal") else ""
            lines.append(cut(f"{head} · {names or m.get('note') or ''}".strip(" ·") + tail, 60))
        tot = d.get("totals") if isinstance(d.get("totals"), dict) else {}
        if tot.get("kcal"):
            stats.append([L("合计", "Total"), f"{_num(tot['kcal'])} kcal"])
        if tot.get("protein"):
            stats.append([L("蛋白质", "Protein"), f"{_num(tot['protein'])} g"])
        if d.get("vs_target"):
            out["f"] = cut(str(d["vs_target"]), 70)
    elif kind == "training_plan":
        for label, key in ((L("练什么", "Session"), "session"), (L("时间", "Time"), "time"), (L("决定", "Call"), "decision")):
            if d.get(key):
                stats.append([label, clip(str(d[key]), 16)])
        lines += [cut(str(x), 60) for x in (d.get("focus") or [])[:3]]
        if d.get("why"):
            lines.append(cut(str(d["why"]), 70))
    if not lines:
        paras = [ln for ln in (re.sub(r"\s+", " ", x).strip() for x in md_lines(card.get("body") or "", headings=False)) if width(ln) >= 4]
        lines = [cut(x, 70) for x in paras[:4]]
    if stats:
        out["s"] = stats[:3]
    if lines:
        out["l"] = lines[:5]
    if tint:
        out["c"] = tint
    return out


def group_tint(thread: str) -> str | None:
    if thread == "main":
        return None
    try:
        with _lock, db() as conn:
            r = conn.execute("SELECT color FROM groups WHERE id=?", (thread,)).fetchone()
        return r["color"] if r and r["color"] else "cyan"
    except sqlite3.Error:
        return None


def new_card(run: Run) -> dict | None:
    """这次回复期间这个线程新写的最新一张卡：feed_items 里 rowid 比开跑时大、没被划掉、group_id 是这个线程（main 认没挂 Group 的卡）。"""
    main = run.thread == "main"
    where = "(group_id IS NULL OR group_id='' OR group_id='main')" if main else "group_id=?"
    try:
        with _lock, db() as conn:
            r = conn.execute(f"SELECT * FROM feed_items WHERE rowid>? AND dismissed=0 AND {where} ORDER BY rowid DESC LIMIT 1",
                             (run.feed_mark,) if main else (run.feed_mark, run.thread)).fetchone()
    except sqlite3.Error:
        return None
    if not r:
        return None
    keys = r.keys()
    try:
        data = json.loads(r["data"]) if "data" in keys and r["data"] else None
    except ValueError:
        data = None
    return {"id": r["id"], "title": r["title"], "body": r["body"] or "", "kind": r["kind"] if "kind" in keys else None, "data": data}


async def notify_run(run: Run) -> None:
    """回复结束后推一条（按 run_level 的档位）：出错 → 原来的出错提示；这次写了卡 → 推卡片；否则推回复开头。推送失败不影响回复本身。"""
    level = run_level(run)
    card = new_card(run) if run.status == "ok" else None
    try:  # 实时活动：练后餐倒计时（不管推不推送，见 live.py）
        import live  # 延迟导入：live 依赖 chat
        live.on_run(card)
    except Exception:  # noqa: BLE001
        pass
    if level == "none":
        return
    try:
        title = thread_title(run.thread)
        to_thread = {"thread": run.thread, "target": {"type": "thread", "thread": run.thread}}
        if run.status != "ok":
            await send_push(title, L("这条没回成，点开看看。", "This reply didn't go through. Tap to take a look."), to_thread,
                            thread_id=run.thread, level=level, collapse=f"reply:{run.thread}", kind="reply")
            return
        if card:
            line = cut(card_line(card["kind"], card["body"], card["data"]), 90)
            await send_push(title, f"{card['title']} · {line}" if line else card["title"],
                            {"thread": run.thread, "target": {"type": "card", "id": card["id"], "thread": run.thread},
                             "card": rich_card(card, group_tint(run.thread))},
                            thread_id=run.thread, subtitle=card_label(card["kind"]), level=level, category="card",
                            collapse=f"card:{run.thread}:{card['kind'] or 'card'}", kind="card")
            return
        await send_push(title, preview(run.text), to_thread, thread_id=run.thread, level=level, collapse=f"reply:{run.thread}", kind="reply")
    except Exception:  # noqa: BLE001 — 推送失败不影响回复本身
        pass
