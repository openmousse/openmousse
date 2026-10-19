"""待办清单（内核 ⑫b，2026-10-16）：todo 工具。多步的活先列一张清单，做着更新状态（待做 / 在做 / 做完 / 不做了），用户在 app 里看得到进度。

怎么写（照 Hermes 的 todo_list 和 Claude Code 的 TodoWrite）：
  todos 给整张清单 = 整张换掉（默认）；merge = true 按 id 只改给了的几项、新的 id 加在后面；什么都不给 = 看现在的清单。
  一项 {"id", "content", "status"}，status = pending / in_progress / completed / cancelled；同一时间只有一个 in_progress（说明里写，不硬挡；
  写了在做的那一项排到前面的待做之前，Hermes 的 _normalize_order）。最多 MAX_ITEMS 项、每项最多 MAX_CHARS 字。每次都回整张清单和计数。
存：engine_todos 表，一个对话一行（主人回合按 app 的对话；后台任务的子 Agent 按 task:<id>，管它自己的；Hermes 也是一个子 Agent 一张新清单）。
  压缩以后、第二天接着聊时带上：还没做完的写进会话开头的背景（engine/context.py 的 todo 那一节，live = False：清单是它自己改的，变了不往后补；
  压缩时开头按那时的清单重写，Hermes 是压缩完补一条「[Your active task list was preserved across context compression]」）。
  撤回（rewind）不退清单：清单记的是做到哪了，不跟着会话的枝走。
每次改了经 ToolCtx.emit 发 {"type": "todo", "items"}（chat.py 推成 SSE 的 todo 事件，以后 app 画；重连时补发）。
给谁：主人回合里 app 的对话（tools.chat_tools）和后台任务的子 Agent（tasks.child_tools）；定时任务、心跳、对外接口不给。
TodoStore 那几个函数（write 的整张换 / merge、_merge、_validate、_cap_content、_dedupe_by_id、_normalize_order、format_for_injection 的写法）
改写自 Hermes 的 tools/todo_tool.py，NousResearch/hermes-agent commit 17c691fd，MIT，Copyright (c) 2025 Nous Research；许可全文见仓库根目录的
THIRD_PARTY_NOTICES。没要它的子任务（parent）和 revision 的那一套界面对账。
"""
from __future__ import annotations

import json
from typing import Any

from i18n import L

from . import store
from .loop import Tool, ToolCtx, ToolFailure

TOOL = "todo"
STATUSES = ("pending", "in_progress", "completed", "cancelled")
ACTIVE = ("pending", "in_progress")
MAX_ITEMS = 100
MAX_CHARS = 500
MARKERS = {"completed": "[x]", "in_progress": "[>]", "pending": "[ ]", "cancelled": "[~]"}
CUT = "… [truncated]"


# —— 一张清单（改写自 Hermes 的 TodoStore）————————————————————————————————————————————

def _cap(content: str) -> str:
    return content if len(content) <= MAX_CHARS else content[:MAX_CHARS - len(CUT)] + CUT


def _validate(item: Any, index: int) -> dict:
    """→ 一项（没写 id 的 id 先空着，write 再补）。"""
    if not isinstance(item, dict):
        raise ValueError(f"todos[{index}] must be an object with id, content and status")
    iid = str(item.get("id") or "").strip()
    content = " ".join(str(item.get("content") or "").split())
    if not content:
        raise ValueError(f"todo item {iid or index + 1!r} has no content: each item needs a short description")
    status = str(item.get("status") or "pending").strip().lower()
    return {"id": iid[:40], "content": _cap(content), "status": status if status in STATUSES else "pending"}


def _fill_ids(items: list[dict]) -> list[dict]:
    """没写 id 的补一个还没用过的数字（Hermes 补「?」，几项都没写就分不清了）。"""
    used = {x["id"] for x in items if x["id"]}
    n = 0
    for x in items:
        if not x["id"]:
            n += 1
            while str(n) in used:
                n += 1
            x["id"] = str(n)
            used.add(x["id"])
    return items


def _dedupe(todos: list) -> list:
    """同一个 id 出现几次：留最后那一次（放在它自己的位置）。"""
    last: dict[str, int] = {}
    for i, t in enumerate(todos):
        key = str(t.get("id") or "").strip() if isinstance(t, dict) else f"__{i}"
        last[key or f"__{i}"] = i
    return [todos[i] for i in sorted(last.values())]


def _ordered(items: list[dict]) -> list[dict]:
    """在做的那一项排到它前面第一个待做的位置（Hermes：别让一个占位的待做挡在正在做的前面）。"""
    st = [x["status"] for x in items]
    if "in_progress" not in st:
        return items
    at = st.index("in_progress")
    if "pending" not in st[:at]:
        return items
    out = list(items)
    out.insert(st.index("pending"), out.pop(at))
    return out


def write(cur: list[dict], todos: list, merge: bool = False) -> list[dict]:
    """→ 写完以后的整张清单。不像样的一项抛 ValueError（原来的清单不动）。"""
    if not merge:
        items = _fill_ids([_validate(t, i) for i, t in enumerate(_dedupe(todos))])
    else:
        items = [dict(x) for x in cur]
        by_id = {x["id"]: x for x in items}
        for i, t in enumerate(_dedupe(todos)):
            iid = str(t.get("id") or "").strip() if isinstance(t, dict) else ""
            if not iid:
                raise ValueError(f"todos[{i}] has no id: with merge every item needs the id it has (or a new one)")
            if iid not in by_id:
                v = _validate(t, i)
                by_id[v["id"]] = v
                items.append(v)
                continue
            x = by_id[iid]
            if str(t.get("content") or "").strip():
                x["content"] = _cap(" ".join(str(t["content"]).split()))
            if str(t.get("status") or "").strip().lower() in STATUSES:
                x["status"] = str(t["status"]).strip().lower()
    return _ordered(items)[:MAX_ITEMS]


def counts(items: list[dict]) -> dict:
    return {"total": len(items), **{s: sum(1 for x in items if x["status"] == s) for s in STATUSES}}


def lines(items: list[dict], active_only: bool = False) -> list[str]:
    return [f"- {MARKERS.get(x['status'], '[?]')} {x['id']}. {x['content']} ({x['status']})" for x in items
            if not active_only or x["status"] in ACTIVE]


# —— 存（一个对话一行）————————————————————————————————————————————————————————————————

def load(conversation: str) -> list[dict]:
    try:
        with store.read() as conn:
            r = conn.execute("SELECT items FROM engine_todos WHERE conversation=?", (conversation,)).fetchone()
    except Exception:  # noqa: BLE001 — 读不了：当没有清单
        return []
    try:
        items = json.loads(r["items"]) if r else []
    except ValueError:
        return []
    return [x for x in items if isinstance(x, dict) and x.get("content")] if isinstance(items, list) else []


def save(conversation: str, items: list[dict], turn: str | None = None) -> None:
    with store.tx() as conn:
        conn.execute("""INSERT INTO engine_todos(conversation, items, revision, turn, updated_at) VALUES(?,?,1,?,?)
            ON CONFLICT(conversation) DO UPDATE SET items=excluded.items, revision=engine_todos.revision + 1, turn=excluded.turn,
            updated_at=excluded.updated_at""", (conversation, json.dumps(items, ensure_ascii=False), turn, store.now_iso()))


def block(conversation: str) -> str:
    """会话开头（建会话、压缩时重写）放的那一节：还没做完的那几项；都做完了 / 没有清单 → 空（engine/context.py、tasks.child_system）。"""
    items = load(conversation)
    active = lines(items, active_only=True)
    if not active:
        return ""
    done = sum(1 for x in items if x["status"] == "completed")
    return ("Your to-do list from earlier in this conversation (still open; " + (f"{done} item{'s' if done != 1 else ''} already done; " if done else "")
            + "keep it up to date with the todo tool, and drop items that no longer apply):\n" + "\n".join(active))


# —— 工具 ————————————————————————————————————————————————————————————————————

def result(items: list[dict]) -> dict:
    c = counts(items)
    return {"todos": items, "summary": c, "message": L(
        "（清单是空的。）" if not items else f"清单现在 {c['total']} 项：{c['in_progress']} 在做、{c['pending']} 待做、{c['completed']} 做完。"
        + ("同一时间只留一个在做；做完一项马上标 completed。" if c["in_progress"] != 1 and c["pending"] else ""),
        "(The list is empty.)" if not items else f"The list has {c['total']} items: {c['in_progress']} in progress, {c['pending']} pending, "
        f"{c['completed']} completed." + (" Keep exactly one item in_progress, and mark each completed as soon as it's done."
                                          if c["in_progress"] != 1 and c["pending"] else ""))}


def tool() -> Tool:
    async def run(args: dict, ctx: ToolCtx) -> dict:
        conv = ctx.conversation or "-"
        cur = load(conv)
        todos = args.get("todos")
        if todos is None:
            return result(cur)
        if not isinstance(todos, list):
            raise ToolFailure(L("todos 要是一个列表（整张清单），或者不给（看现在的清单）", "todos must be a list (the whole list), or left out to read it"))
        try:
            items = write(cur, todos, bool(args.get("merge")))
        except ValueError as e:
            raise ToolFailure(str(e)) from None
        if items != cur:
            save(conv, items, ctx.turn)
            if ctx.emit is not None:
                ctx.emit({"type": "todo", "items": items})
        return result(items)

    def title(args: dict) -> str:
        t = args.get("todos")
        if not isinstance(t, list):
            return L("看待办清单", "Read the to-do list")
        now = next((str(x.get("content") or "") for x in t if isinstance(x, dict) and x.get("status") == "in_progress"), "")
        return (L("待办：", "To-do: ") + now)[:80] if now else L(f"更新待办清单（{len(t)} 项）", f"Update the to-do list ({len(t)} items)")

    return Tool(name=TOOL, kinds=("owner",), run=run, timeout=30, title=title,
                classify=lambda args: ("read", "只记这个对话自己的待办清单，不动用户的东西"),
                description=L(
                    "待办清单：三步以上的活（或者用户一次交代了好几件事）先列一张清单再做，做着更新：同一时间只有一项 in_progress，做完一项马上标 completed，"
                    "不做了的标 cancelled、需要的话加一项新的。todos 给整张清单就整张换掉；merge = true 只按 id 改给了的几项、新 id 加在后面；什么都不给 = 看现在的清单。"
                    "清单跟着这个对话（压缩以后、第二天接着聊都在），用户在 app 里看得到。一两步就完的事别用它。",
                    "To-do list: for work with three or more steps (or several things the user asked for at once), write a list before you "
                    "start and keep it current: exactly one item in_progress at a time, mark each completed as soon as it's done, mark "
                    "dropped ones cancelled and add a new item if needed. todos with the whole list replaces it; merge = true changes only "
                    "the items you give (by id) and appends new ids; no todos reads the current list. The list stays with this conversation "
                    "(after compaction and on the next day too) and the user sees it in the app. Skip it for quick one- or two-step requests."),
                schema={"type": "object", "required": [], "properties": {
                    "todos": {"type": "array", "description": L("清单（整张，或者 merge 时要改的几项）", "The list (all of it, or the items to change with merge)"),
                              "items": {"type": "object", "required": ["id", "content", "status"], "properties": {
                                  "id": {"type": "string", "description": L("短 id：1、2、3……", "A short id: 1, 2, 3…")},
                                  "content": {"type": "string", "description": L("这一步做什么（一句）", "What this step is (one line)")},
                                  "status": {"type": "string", "enum": list(STATUSES)}}}},
                    "merge": {"type": "boolean", "description": L("true：只改给了的几项（按 id），新的加在后面；false（默认）：整张换掉",
                                                                  "true: update the given items by id and append new ones; false (default): replace the whole list")}}})
