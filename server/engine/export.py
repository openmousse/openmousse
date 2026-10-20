"""导出一条会话（第 2 批，10/19）：/export [md|json|html] [tools]，接口 GET /api/engine/chats/{thread}/export。

导出什么：这个对话现在这条会话（接口可以给 session）现在这一枝上的全部往来（压缩过的也全导：库里原文都在；压缩的地方写一笔摘要），
  用户说的、助手说的；tools = 带上工具调用（名字、参数）和结果（截短）。不导：开头的 system、补的规矩和背景、思考块、图的原图（写成「[图]」）。
打码：每一段字、每个参数都过一遍 engine/errors.py 的 scrub（像密钥的串、Bearer 令牌换成 ***）。只打码像密钥的，别的原样：这是主人自己
  的记录，导出给他自己。
三种格式：Markdown（一句一节，带时间）；JSON（{"conversation", "session", "day", "exported", "messages": [{"role", "text", "time"?,
  "calls"?, "name"?, "error"?}]}）；HTML（一页、不连外面的东西、所有字都转义；当附件发，浏览器里打开也在沙箱里：../files.py 的
  untrusted_headers）。
/export：存成这个对话的一个附件（../files.py 的 attachments 表，和用户上传的一样能在 app 里点开、下载），挂在回答那一行上。
借：Hermes hermes_cli/session_export.py（一条会话一个标题、下面是元数据、再一句一节；只借写法）。
"""
from __future__ import annotations

import json
import mimetypes
import re
import uuid
from datetime import datetime
from html import escape

from config import settings
from i18n import L

from .errors import scrub
from .session import DbSession, tool_calls

FORMATS = {"md": "md", "markdown": "md", "json": "json", "html": "html", "htm": "html"}
MIME = {"md": "text/markdown", "json": "application/json", "html": "text/html"}
RESULT_MAX = 2_000   # 带工具时，一个结果最多留多少字


def _clock(ts: str | None) -> str:
    try:
        return datetime.fromisoformat(str(ts)).astimezone(settings.tz).strftime("%H:%M")
    except (TypeError, ValueError):
        return ""


def _text(m: dict) -> str:
    """一条消息给人看的字：文字块照抄，图写成「[图]」。"""
    parts = []
    for b in m.get("content") or []:
        if b.get("type") == "text" and b.get("text"):
            parts.append(str(b["text"]))
        elif b.get("type") in ("image", "image_url"):
            parts.append(L("[图]", "[image]"))
    return scrub("\n".join(parts).strip())


def collect(session: DbSession, tools: bool = False) -> list[dict]:
    """会话现在这一枝 → 一句一项（见文件开头）。"""
    out: list[dict] = []
    for e in session.path():
        if e["kind"] == "compaction":
            d = json.loads(e["data"])
            if d.get("summary"):
                out.append({"role": "summary", "text": scrub(str(d["summary"])), "time": _clock(e["ts"])})
            continue
        if e["kind"] != "message":
            continue
        m = json.loads(e["data"])
        role = m.get("role")
        if role == "user":
            if t := _text(m):
                out.append({"role": "user", "text": t, "time": _clock(e["ts"])})
        elif role == "assistant":
            item: dict = {"role": "assistant", "text": _text(m), "time": _clock(e["ts"])}
            if tools and (calls := tool_calls(m)):
                item["calls"] = [{"name": str(c.get("name") or ""), "args": json.loads(scrub(json.dumps(c.get("args") or {}, ensure_ascii=False)))}
                                 for c in calls]
            if item["text"] or item.get("calls"):
                out.append(item)
        elif role == "tool" and tools:
            t = _text(m)
            out.append({"role": "tool", "name": str(m.get("name") or ""), "text": t if len(t) <= RESULT_MAX else t[:RESULT_MAX] + " …",
                        "error": bool(m.get("error")), "time": _clock(e["ts"])})
    return out


def who(role: str) -> str:
    return {"user": L("你", "You"), "assistant": settings.app_name, "tool": L("工具结果", "Tool result"),
            "summary": L("前面压缩成的摘要", "Summary of the earlier part")}.get(role, role)


def title_of(conversation: str) -> str:
    from . import commands
    return commands.title_of(conversation)


def markdown(conversation: str, session: DbSession, items: list[dict]) -> str:
    day = session.key.rsplit(":", 1)[-1]
    lines = [f"# {title_of(conversation)} · {day}", "",
             L(f"_导出于 {datetime.now(settings.tz):%Y-%m-%d %H:%M} · {len(items)} 条 · 会话 {session.id}_",
               f"_Exported {datetime.now(settings.tz):%Y-%m-%d %H:%M} · {len(items)} entries · session {session.id}_"), ""]
    for it in items:
        lines.append(f"## {who(it['role'])}" + (f" · {it['name']}" if it.get("name") else "") + (f" · {it['time']}" if it.get("time") else ""))
        lines.append("")
        if it.get("text"):
            lines += [it["text"], ""]
        for c in it.get("calls") or []:
            lines += [L(f"调用 `{c['name']}`：", f"Called `{c['name']}`:"), "", "```json", json.dumps(c["args"], ensure_ascii=False, indent=1), "```", ""]
    return "\n".join(lines).rstrip() + "\n"


def as_json(conversation: str, session: DbSession, items: list[dict]) -> str:
    return json.dumps({"conversation": conversation, "session": session.id, "day": session.key.rsplit(":", 1)[-1],
                       "exported": datetime.now(settings.tz).isoformat(timespec="seconds"), "messages": items}, ensure_ascii=False, indent=1) + "\n"


def html(conversation: str, session: DbSession, items: list[dict]) -> str:
    day = session.key.rsplit(":", 1)[-1]
    rows = []
    for it in items:
        head = escape(who(it["role"]) + (f" · {it['name']}" if it.get("name") else "") + (f" · {it['time']}" if it.get("time") else ""))
        body = f"<pre>{escape(it['text'])}</pre>" if it.get("text") else ""
        for c in it.get("calls") or []:
            body += f"<p class=call>{escape(L('调用', 'Called'))} <code>{escape(c['name'])}</code></p>" \
                    f"<pre class=args>{escape(json.dumps(c['args'], ensure_ascii=False, indent=1))}</pre>"
        rows.append(f"<section class=\"{escape(it['role'])}\"><h2>{head}</h2>{body}</section>")
    title = escape(f"{title_of(conversation)} · {day}")
    return ("<!doctype html><html><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            f"<title>{title}</title><style>body{{font:15px/1.5 system-ui,sans-serif;max-width:46rem;margin:2rem auto;padding:0 1rem;color:#1b1d21}}"
            "h1{font-size:1.4rem}h2{font-size:.85rem;color:#666;margin:1.2rem 0 .3rem}pre{white-space:pre-wrap;font:inherit;margin:0}"
            ".tool pre,.args{font:13px/1.4 ui-monospace,monospace;background:#f4f5f7;padding:.5rem;border-radius:6px}"
            ".summary{border-left:3px solid #ccc;padding-left:.8rem;color:#444}"
            "@media (prefers-color-scheme: dark){body{background:#14171c;color:#e6e8eb}.tool pre,.args{background:#20242b}}</style>"
            f"</head><body><h1>{title}</h1><p>{escape(L(f'会话 {session.id} · {len(items)} 条', f'Session {session.id} · {len(items)} entries'))}</p>"
            + "".join(rows) + "</body></html>\n")


def render(conversation: str, session: DbSession, fmt: str = "md", tools: bool = False) -> tuple[str, str, str]:
    """→ (内容, 文件名, MIME)。fmt 不认识 → ValueError。"""
    kind = FORMATS.get((fmt or "md").strip().lower())
    if kind is None:
        raise ValueError(fmt)
    items = collect(session, tools)
    body = {"md": markdown, "json": as_json, "html": html}[kind](conversation, session, items)
    day = session.key.rsplit(":", 1)[-1]
    name = re.sub(r"[^A-Za-z0-9_.-]", "_", f"{conversation}-{day}")[:60] + f".{kind}"
    return body, name, MIME[kind]


def attach(thread: str, name: str, body: str, mime: str) -> dict:
    """存成这个对话的一个附件（同上传的，../files.py 的 attachments 表；还没挂到哪条消息上）→ files.summary 那样的一份。"""
    import files
    data = body.encode("utf8")
    fid = uuid.uuid4().hex[:12]
    folder = files.UPLOAD_DIR / re.sub(r"[^A-Za-z0-9_-]", "_", thread)[:40]
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{fid}-{files.safe_name(name)}"
    path.write_bytes(data)
    mime = mime or mimetypes.guess_type(name)[0] or "application/octet-stream"
    with files._lock, files.adb() as conn:
        conn.execute("INSERT INTO attachments(id, thread, name, mime, size, kind, path, text, chars, note, status, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                     (fid, thread, files.safe_name(name), mime, len(data), "doc", str(path), None, None, L("导出", "Export"), "ok", files.now_iso()))
        r = conn.execute("SELECT * FROM attachments WHERE id=?", (fid,)).fetchone()
    return dict(r)
