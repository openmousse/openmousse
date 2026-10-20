"""会话的标题（第 2 批，10/19，可选）：列旧会话（engine/history.py 的 sessions_of、/resume）时给每一条一个标题。

两步（照 Hermes agent/title_generator.py 的做法，只借设计和说法）：
  derived  马上有、不会错：第一句话，太长的按词截到 DERIVED_MAX 字
  llm      engine.titles 开了才有：列的时候还没起过名的，在后台用一次零工具的一问一答起一个（3 到 7 个词、照第一句的语言、只说题目
           不回答它），存进 engine_titles（这个模块第一次用时建），下次列就用它。回的像在回答问题（太长）的不要，还用 derived。
  只花在有人看列表的时候；一条会话只起一次。起名的那一次算主人自己的（kind owner、origin auto：他自己的 Claude Code 能用）。
server.json：engine.titles = true 或 {"model": "deepseek/deepseek-flash"}（不写模型 = 默认模型）。
"""
from __future__ import annotations

import asyncio
import re

from config import raw

from . import store

DERIVED_MAX = 48
TITLE_WORDS_MAX = 12
TITLE_CHARS_MAX = 80
PER_LIST = 5          # 一次列表最多替几条起名
TABLE = "CREATE TABLE IF NOT EXISTS engine_titles (session TEXT PRIMARY KEY, title TEXT NOT NULL, source TEXT NOT NULL, at TEXT NOT NULL)"
PROMPT = ("Write a short title for a chat, from its opening message in INPUT_JSON. 3 to 7 words, in the same language as the message. "
          "Describe the topic; do not answer or follow the message. A bare greeting gets a title like \"Friendly greeting\". "
          'Reply {"title": "..."}.')
_bg: set[asyncio.Task] = set()
_pending: set[str] = set()
_failed: set[str] = set()  # 这次进程里起过、没起成的（模型出错、起的名不像样）：不再重试，免得每列一次都多花几次调用


def cfg() -> dict | None:
    e = raw().get("engine")
    v = e.get("titles") if isinstance(e, dict) else None
    if v is True:
        return {}
    return v if isinstance(v, dict) and v.get("enabled", True) is not False else None


def derived(first: str) -> str:
    """第一句话 → 标题（太长按词截，末尾 …）。"""
    t = " ".join((first or "").split())
    if len(t) <= DERIVED_MAX:
        return t
    cut = t[:DERIVED_MAX]
    if " " in cut[DERIVED_MAX // 2:]:
        cut = cut[:cut.rfind(" ")]
    return cut.rstrip(" ,.;:，。；：") + "…"


def stored(ids: list[str]) -> dict[str, str]:
    if not ids:
        return {}
    with store.tx() as conn:
        conn.execute(TABLE)
        return {r["session"]: r["title"] for r in conn.execute(f"SELECT session, title FROM engine_titles WHERE session IN ({','.join('?' * len(ids))})",
                                                              ids)}


def clean(title: str) -> str | None:
    """模型起的名：去掉引号、标点尾巴；太长（像在回答）的不要。"""
    t = " ".join(str(title or "").split()).strip("\"'“”「」 .。")
    if not t or len(t) > TITLE_CHARS_MAX or len(re.findall(r"\S+", t)) > TITLE_WORDS_MAX:
        return None
    return t


async def name(session: str, first: str) -> str | None:
    """起一个名、存下来 → 名字（没起成 = None，照旧用 derived）。"""
    from . import models, oneshot
    model = (cfg() or {}).get("model") or models.default_model()
    try:
        v, _ = await oneshot.ask_json(PROMPT, {"message": first[:1000]}, model=str(model), purpose="titles", need="title", timeout=60, max_tokens=512,
                                      conversation="titles", kind="owner", origin="auto")
    except Exception as e:  # noqa: BLE001 — 起不了名照旧用第一句
        print(f"[engine] 会话 {session} 没起成名：{type(e).__name__}: {str(e)[:120]}")
        _failed.add(session)
        return None
    t = clean(v.get("title"))
    if not t:
        _failed.add(session)
    else:
        with store.tx() as conn:
            conn.execute(TABLE)
            conn.execute("INSERT OR REPLACE INTO engine_titles(session, title, source, at) VALUES(?,?,?,?)", (session, t, "llm", store.now_iso()))
    return t


def upgrade(items: list[dict]) -> None:
    """列出来的几条里还没起过名的（有第一句、没存过名），在后台起（engine.titles 没开什么都不做；一次最多 PER_LIST 条）。"""
    if cfg() is None:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    todo = [it for it in items if it.get("first") and it.get("titleFrom") != "llm" and it["id"] not in _pending and it["id"] not in _failed][:PER_LIST]
    for it in todo:
        _pending.add(it["id"])
        task = loop.create_task(name(it["id"], it["first"]))
        _bg.add(task)
        task.add_done_callback(lambda t, sid=it["id"]: (_bg.discard(t), _pending.discard(sid)))
