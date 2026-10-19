"""会话：一轮轮对话的记录。两种：

  MemorySession  不落库（外人回合、一次性的问答、测试）
  DbSession      存在 engine_sessions / engine_entries 里的一棵树：每条带 parent，messages() 从叶子走回根；
                 撤回 = 叶子换到那一条之前（rewind，只能撤到你说的一句话），旧的枝还在库里，以后能做「换一个回答」「回到那一枝」

规矩（Claude 的硬规矩，也省缓存）：只往后补，不回头改。开头的 system 在建会话时定死；规矩变了往后补一条 role=system；
旧消息一个字都不动，assistant 的 raw（模型原样回来的内容块，带思考的签名）原样回放给同一家。
库里的记录保持原样，发给模型之前由 repair() 修一遍（没结果的工具调用补一句、出错 / 拒答的回复不发……），每次修法一样，前缀稳定。

条目（engine_entries.kind，借 pi 的会话格式，第 4 项）：
  message     一条消息（下面的中立格式）。备料变了往后补的那条 system 带 "ctx"（补完以后各样背景的指纹，engine/context.py）
  context     不是消息：会话开头备料的指纹 {"ctx": …}，或者「这一样变了但模型已经知道」时只更新指纹；不发给模型
  compaction  压缩（engine/compact.py）：{"summary", "first_kept": 从哪一条起原样保留（None = 全压了）, "system": 按压缩时的背景
              重写的开头, "ctx", "tokens_before", "model", "usage", "reason", …}
view() 照 pi 的 buildSessionContext 从叶子走回根：路上有压缩就是 [摘要（一条 user）] + 从 first_kept 起保留的 + 压缩以后的；
保留的那几轮去掉 Claude 的思考块（前缀变了，官方：keep-tail 压缩要去掉，不然 400），带 ctx 的补充不再发（重写的开头里已经是最新的）。
同一份库里的记录每次重建出来一模一样（压缩以后的前缀也是稳定的）。

消息是中立格式（provider_*.py 各自翻译成自家的）：
  user       {"role": "user", "content": [{"type": "text", "text"} | {"type": "image", "mime", "data"(base64)}], "at"?}
             at（10/19 起，owner.user_message 写）：用户说这句话时的当地时间，存成写好的字（time_stamp）；发给模型时 repair 把它放在
             这句话前面（with_time），模型就知道现在几点（以前问「现在几点」答不了）
  assistant  {"role": "assistant", "content": [{"type": "text", "text"} | {"type": "thinking", "text", "sig"?, "redacted"?}
             | {"type": "tool_call", "id", "name", "args"}], "api", "provider", "model"（请求的型号）, "served"?（实际回答的型号，
             服务端换过才有）, "stop", "usage", "raw"?}
  tool       {"role": "tool", "id": 对应的 tool_call id, "name", "content": [{"type": "text", "text"} | 图块…], "error": bool, "ref"?}
             图块（⑫b，engine/images.py）：会话里存 {"type": "image", "mime", "file": 图库里的文件名, "sha", "bytes", "w", "h", "label"?}，
             不存 base64；发给模型之前（providers.stream）按各家能不能看图读回来、换成一句话、只留最近几张。
             ref：ask_user 等到的回答是 app 里的哪一条消息（engine/ask.py；撤回、重启后认插话用）
  system     {"role": "system", "content": [{"type": "text", "text"}]}
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from config import settings

from . import store

ABORTED_NOTE = "\n\n(This reply was cut off: the user stopped it before it finished.)"
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
NO_RESULT = "No result: the turn was interrupted before this tool call finished."
# 压缩以后摘要那一条的开头（OpenClaw / pi 用 <summary> 包；说法借 Hermes 的 SUMMARY_PREFIX：只当背景、只回答摘要后面最新的那句、
# 工具照用。Hermes 记过的坑：说得太死模型会只说不做，不说清又会把摘要里早就办完的事再办一遍）
SUMMARY_PREFIX = (
    "[Earlier conversation, compacted] The earlier part of this conversation was summarized below to save space. It is background "
    "for continuity, not a new request: what was asked in it has been handled unless the summary says it is still open, and nothing "
    "in it should be redone or resumed on its own. Respond to the latest user message after this summary. Your instructions, profile "
    "and memory in the system prompt still apply in full, and your tools work as before.")
CLAUDE_THINKING = ("thinking", "redacted_thinking")


def user(text: str, images: list[dict] | None = None) -> dict:
    return {"role": "user", "content": [{"type": "text", "text": text}, *(images or [])]}


def system_note(text: str) -> dict:
    return {"role": "system", "content": [{"type": "text", "text": text}]}


def tool_result(call_id: str, name: str, text: str, error: bool = False, images: list[dict] | None = None) -> dict:
    return {"role": "tool", "id": call_id, "name": name, "content": [{"type": "text", "text": text}, *(images or [])], "error": error}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def time_stamp(at: datetime | None = None) -> str:
    """用户这句话是当地几点说的：Mon 2026-10-19 07:42 BST（照 OpenClaw 在每句话前面带的那个时间）。时区照 settings.tz（server.json 的
    timezone）。这么写：星期几省得模型自己推「明天」「下周一」是哪天（它算日历常出错）；日期用 ISO（哪个地方的人看都不会读错月和日）；
    24 小时制（没有上午下午之分）；带时区缩写（夏令时一换就看得出），没有字母缩写的（America/Sao_Paulo 是 -03）写成 UTC-03。
    英文的星期缩写不随语言变：这是给模型看的记号，不是给人看的字。"""
    t = (at or _utcnow()).astimezone(settings.tz)
    zone = t.tzname() or ""
    if zone[:1] in ("+", "-"):
        zone = "UTC" + zone
    return f"{WEEKDAYS[t.weekday()]} {t:%Y-%m-%d %H:%M} {zone}".strip()


def with_time(msg: dict) -> dict:
    """发给模型时：带 at 的 user 消息前面加上 [Mon 2026-10-19 07:42 BST]（第一段字的开头；第一块是图就单放一段在最前面）。
    只有存的时候就带了 at 的那几条（新的）才加，同一条每次加出来一样；老的一个字不动，提示缓存的前缀不变。"""
    at = msg.get("at")
    if msg.get("role") != "user" or not isinstance(at, str) or not at:
        return msg
    content = list(msg.get("content") or [])
    if content and content[0].get("type") == "text":
        content[0] = {**content[0], "text": f"[{at}] {content[0].get('text') or ''}"}
    else:
        content.insert(0, {"type": "text", "text": f"[{at}]"})
    return {**msg, "content": content}


def text_of(msg: dict | None) -> str:
    """一条消息里给人看的字（只拼 text 块；思考、工具调用不算）。"""
    return "".join(b.get("text") or "" for b in (msg or {}).get("content") or [] if b.get("type") == "text")


def tool_calls(msg: dict | None) -> list[dict]:
    return [b for b in (msg or {}).get("content") or [] if b.get("type") == "tool_call"]


def day_key(thread: str, day: str) -> str:
    """会话键：mousse:<对话>:<逻辑日>（和 claw.py 给别的 claw 的写法一样；逻辑日 04:00 换）。"""
    return f"mousse:{thread}:{day}"


def summary_message(summary: str) -> dict:
    """压缩成的摘要发给模型时的样子：一条 user（pi、OpenClaw、Claude 官方的 simple compaction 都这样放）。"""
    return {**user(f"{SUMMARY_PREFIX}\n\n<summary>\n{summary.strip()}\n</summary>"), "compaction": True}


def strip_thinking(msg: dict) -> dict:
    """压缩以后保留下来的 Claude 回复：去掉思考块（文字、工具调用照留）。Claude 的思考签名绑着生成它时的整个前缀，压缩以后前缀变了，
    原样发回去会 400（官方 keep-tail compaction 的说法）。别家的思考没有签名，原样留着（DeepSeek、Kimi 每一轮都带回去，见 provider_openai）。"""
    if msg.get("role") != "assistant" or msg.get("api") != "anthropic":
        return msg
    out = {**msg, "content": [b for b in msg.get("content") or [] if b.get("type") != "thinking"]}
    if isinstance(msg.get("raw"), list):
        out["raw"] = [b for b in msg["raw"] if b.get("type") not in CLAUDE_THINKING]
    return out


def repair(messages: list[dict]) -> list[dict]:
    """发给模型之前修一遍（不改库里的记录；同一份历史每次修出来一样）。借 pi / OpenClaw / Hermes 的做法：
    - 有工具调用没结果的（被停了、出错了）：在它后面补一条「没结果」，不然 Claude 和多数兼容接口都会整轮报错；
    - 对不上调用的工具结果丢掉；夹在工具调用和结果中间的 system 挪到结果后面；
    - 拒答、出错、空的 assistant 不发；被停下的半截回复后面写明「被停了」，免得模型以为自己说完了；
    - 带 at 的 user 消息前面加上那个时间（with_time）。"""
    out: list[dict] = []
    pending: dict[str, str] = {}  # 还没结果的工具调用 id → 工具名
    held: list[dict] = []         # 等工具结果补齐再放的 system

    def settle() -> None:
        for cid, name in pending.items():
            out.append(tool_result(cid, name, NO_RESULT, True))
        pending.clear()
        out.extend(held)
        held.clear()

    for m in messages:
        role = m.get("role")
        if role == "tool":
            cid = str(m.get("id") or "")
            if cid in pending:
                pending.pop(cid)
                out.append(m)
                if not pending:
                    out.extend(held)
                    held.clear()
            continue
        if role == "system" and pending:
            held.append(m)
            continue
        settle()
        if role == "assistant":
            if m.get("stop") in ("refusal", "error"):
                continue
            calls = tool_calls(m)
            text = text_of(m)
            if not calls and not text.strip():
                continue
            if m.get("stop") == "aborted" and text:
                m = {**m, "content": [*(b for b in m.get("content") or [] if b.get("type") != "text"),
                                      {"type": "text", "text": text + ABORTED_NOTE}]}
            for c in calls:
                pending[str(c.get("id") or "")] = str(c.get("name") or "")
        elif role == "user":
            m = with_time(m)
        out.append(m)
    settle()
    return out


class MemorySession:
    id: str | None = None

    def __init__(self, messages: list[dict] | None = None, system: str = "") -> None:
        self.system = system
        self._msgs: list[dict] = list(messages or [])

    def messages(self) -> list[dict]:
        return list(self._msgs)

    def append(self, msg: dict, turn: str | None = None) -> str:
        self._msgs.append(msg)
        return str(len(self._msgs))


@dataclass
class View:
    """当前这一枝发给模型的样子（DbSession.view()）。"""
    messages: list[dict]                       # 发给模型的（有压缩时第一条是摘要）
    rows: list[tuple[dict, dict]] = field(default_factory=list)  # 摘要后面的每条消息：(条目, 消息)，按先后；messages 去掉摘要就是它们
    fresh: int = 0                             # rows 里从第几条起是最近一次压缩以后才有的（没压缩过 = 0）
    summary: str | None = None                 # 最近一次压缩的摘要
    system: str | None = None                  # 最近一次压缩按当时背景重写的开头（没压缩过 = None，用会话开头的）
    ctx: dict | None = None                    # 这一枝上最新的备料指纹（engine/context.py）
    compaction: dict | None = None             # 最近一次压缩条目的全部内容


class DbSession:
    def __init__(self, row) -> None:
        self.id: str = row["id"]
        self.key: str = row["key"]
        self.kind: str = row["kind"]
        self.agent: str | None = row["agent"]
        self.model: str | None = row["model"]
        self.system: str = row["system"] or ""
        self.leaf: str | None = row["leaf"]

    @classmethod
    def get(cls, sid: str) -> DbSession | None:
        with store.read() as conn:
            r = conn.execute("SELECT * FROM engine_sessions WHERE id=?", (sid,)).fetchone()
        return cls(r) if r else None

    @classmethod
    def latest(cls, key: str) -> DbSession | None:
        with store.read() as conn:
            r = conn.execute("SELECT * FROM engine_sessions WHERE key=? ORDER BY created_at DESC, rowid DESC LIMIT 1", (key,)).fetchone()
        return cls(r) if r else None

    @classmethod
    def create(cls, key: str, *, kind: str, agent: str | None = None, model: str | None = None, system: str = "") -> DbSession:
        sid = "s_" + uuid.uuid4().hex[:16]
        now = store.now_iso()
        with store.tx() as conn:
            conn.execute("INSERT INTO engine_sessions(id, key, kind, agent, model, system, leaf, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                         (sid, key, kind, agent, model, system or "", None, now, now))
        s = cls.get(sid)
        assert s is not None
        return s

    @classmethod
    def open(cls, key: str, *, kind: str, agent: str | None = None, model: str | None = None, system: str = "") -> DbSession:
        """这个键最近的一条会话；没有就新建（system 只在新建时定下，已有的会话不改开头）。"""
        return cls.latest(key) or cls.create(key, kind=kind, agent=agent, model=model, system=system)

    def _rows(self) -> dict[str, dict]:
        """这条会话的全部条目；顺手从库里读一下现在的叶子（别处的 DbSession 可能往后补过、撤回过）。"""
        with store.read() as conn:
            rows = conn.execute("SELECT id, parent, kind, role, data, turn, ts FROM engine_entries WHERE session=? ORDER BY rowid",
                                (self.id,)).fetchall()
            r = conn.execute("SELECT leaf FROM engine_sessions WHERE id=?", (self.id,)).fetchone()
        if r is not None:
            self.leaf = r["leaf"]
        return {r["id"]: dict(r) for r in rows}

    def path(self) -> list[dict]:
        """叶子走回根的那一串（按先后），每条 {id, parent, kind, role, data(str), turn, ts}，含不是消息的条目。"""
        rows = self._rows()
        out, cur, seen = [], self.leaf, set()
        while cur and cur in rows and cur not in seen:
            seen.add(cur)
            out.append(rows[cur])
            cur = rows[cur]["parent"]
        return out[::-1]

    def view(self) -> View:
        """发给模型的样子（见文件开头）：压缩过就是摘要 + 保留的 + 之后的。"""
        path = self.path()
        ctx = None
        for r in reversed(path):  # 最新的备料指纹：context 条目、带 ctx 的补充、压缩条目，谁在后面算谁的
            if r["kind"] in ("context", "compaction") or (r["kind"] == "message" and r["role"] == "system"):
                d = json.loads(r["data"])
                if isinstance(d.get("ctx"), dict):
                    ctx = d["ctx"]
                    break
        ci = max((i for i, r in enumerate(path) if r["kind"] == "compaction"), default=-1)
        if ci < 0:
            rows = [(r, json.loads(r["data"])) for r in path if r["kind"] == "message"]
            return View(messages=[m for _, m in rows], rows=rows, ctx=ctx)
        comp = json.loads(path[ci]["data"])
        rewritten = isinstance(comp.get("system"), str) and bool(comp["system"])
        rows: list[tuple[dict, dict]] = []
        keep, on = comp.get("first_kept"), False
        for r in path[:ci]:
            on = on or (keep is not None and r["id"] == keep)
            if not on or r["kind"] != "message":
                continue
            m = json.loads(r["data"])
            if rewritten and m.get("role") == "system" and "ctx" in m:
                continue
            rows.append((r, strip_thinking(m)))
        fresh = len(rows)
        rows += [(r, json.loads(r["data"])) for r in path[ci + 1:] if r["kind"] == "message"]
        summary = str(comp.get("summary") or "")
        return View(messages=[summary_message(summary), *(m for _, m in rows)], rows=rows, fresh=fresh, summary=summary,
                    system=comp["system"] if rewritten else None, ctx=ctx, compaction=comp)

    def messages(self) -> list[dict]:
        """当前这一枝发给模型的消息（中立格式；没修过，repair() 在各家翻译时做）。"""
        return self.view().messages

    def effective_system(self) -> str:
        """现在用的开头：最近一次压缩重写过就用重写的，不然是建会话时定下的。"""
        return self.view().system or self.system

    def append(self, msg: dict, turn: str | None = None, kind: str = "message") -> str:
        eid = "e_" + uuid.uuid4().hex[:16]
        now = store.now_iso()
        with store.tx() as conn:
            leaf = conn.execute("SELECT leaf FROM engine_sessions WHERE id=?", (self.id,)).fetchone()["leaf"]
            conn.execute("INSERT INTO engine_entries(id, session, parent, kind, role, data, turn, ts) VALUES(?,?,?,?,?,?,?,?)",
                         (eid, self.id, leaf, kind, str(msg.get("role") or ""), json.dumps(msg, ensure_ascii=False), turn, now))
            conn.execute("UPDATE engine_sessions SET leaf=?, updated_at=? WHERE id=?", (eid, now, self.id))
        self.leaf = eid
        return eid

    def set_leaf(self, entry_id: str | None) -> None:
        with store.tx() as conn:
            if entry_id is not None and not conn.execute("SELECT 1 FROM engine_entries WHERE id=? AND session=?", (entry_id, self.id)).fetchone():
                raise KeyError(entry_id)
            conn.execute("UPDATE engine_sessions SET leaf=?, updated_at=? WHERE id=?", (entry_id, store.now_iso(), self.id))
        self.leaf = entry_id

    def rewind(self, entry_id: str) -> None:
        """撤回你说的那一句：叶子换成它前面那条（这一句和它后面的都离开当前这一枝，库里还留着）。
        只能撤到 user 消息上（Hermes 的规矩：撤到半中间会留下没有结果的工具调用）。撤到压缩之前，压缩也一起离开这一枝（又是完整的历史）。"""
        rows = self._rows()
        if entry_id not in rows:
            raise KeyError(entry_id)
        if rows[entry_id]["role"] != "user" or rows[entry_id]["kind"] != "message":
            raise ValueError("can only rewind to something the user said")
        self.set_leaf(rows[entry_id]["parent"])

    def tree(self) -> list[dict]:
        """整棵树（给以后的「换一枝」界面）：每条 {id, parent, kind, role, ts, preview, on_path}。"""
        on = {r["id"] for r in self.path()}
        out = []
        for r in self._rows().values():
            m = json.loads(r["data"])
            out.append({"id": r["id"], "parent": r["parent"], "kind": r["kind"], "role": r["role"], "ts": r["ts"],
                        "preview": (text_of(m) or str(m.get("summary") or ""))[:80], "on_path": r["id"] in on})
        return out
