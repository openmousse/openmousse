"""会话：一轮轮对话的记录。两种：

  MemorySession  不落库（外人回合、一次性的问答、测试）
  DbSession      存在 engine_sessions / engine_entries 里的一棵树：每条带 parent，messages() 从叶子走回根；
                 撤回 = 叶子换到那一条之前（rewind，只能撤到你说的一句话），旧的枝还在库里，以后能做「换一个回答」「回到那一枝」

规矩（Claude 的硬规矩，也省缓存）：只往后补，不回头改。开头的 system 在建会话时定死；规矩变了往后补一条 role=system；
旧消息一个字都不动，assistant 的 raw（模型原样回来的内容块，带思考的签名）原样回放给同一家。
库里的记录保持原样，发给模型之前由 repair() 修一遍（没结果的工具调用补一句、出错 / 拒答的回复不发……），每次修法一样，前缀稳定。

消息是中立格式（provider_*.py 各自翻译成自家的）：
  user       {"role": "user", "content": [{"type": "text", "text"} | {"type": "image", "mime", "data"(base64)}]}
  assistant  {"role": "assistant", "content": [{"type": "text", "text"} | {"type": "thinking", "text", "sig"?, "redacted"?}
             | {"type": "tool_call", "id", "name", "args"}], "api", "provider", "model"（请求的型号）, "served"?（实际回答的型号，
             服务端换过才有）, "stop", "usage", "raw"?}
  tool       {"role": "tool", "id": 对应的 tool_call id, "name", "content": [{"type": "text", "text"}], "error": bool}
  system     {"role": "system", "content": [{"type": "text", "text"}]}
"""
from __future__ import annotations

import json
import uuid

from . import store

ABORTED_NOTE = "\n\n(This reply was cut off: the user stopped it before it finished.)"
NO_RESULT = "No result: the turn was interrupted before this tool call finished."


def user(text: str, images: list[dict] | None = None) -> dict:
    return {"role": "user", "content": [{"type": "text", "text": text}, *(images or [])]}


def system_note(text: str) -> dict:
    return {"role": "system", "content": [{"type": "text", "text": text}]}


def tool_result(call_id: str, name: str, text: str, error: bool = False) -> dict:
    return {"role": "tool", "id": call_id, "name": name, "content": [{"type": "text", "text": text}], "error": error}


def text_of(msg: dict | None) -> str:
    """一条消息里给人看的字（只拼 text 块；思考、工具调用不算）。"""
    return "".join(b.get("text") or "" for b in (msg or {}).get("content") or [] if b.get("type") == "text")


def tool_calls(msg: dict | None) -> list[dict]:
    return [b for b in (msg or {}).get("content") or [] if b.get("type") == "tool_call"]


def day_key(thread: str, day: str) -> str:
    """会话键：mousse:<对话>:<逻辑日>（和 claw.py 给别的 claw 的写法一样；逻辑日 04:00 换）。"""
    return f"mousse:{thread}:{day}"


def repair(messages: list[dict]) -> list[dict]:
    """发给模型之前修一遍（不改库里的记录；同一份历史每次修出来一样）。借 pi / OpenClaw / Hermes 的做法：
    - 有工具调用没结果的（被停了、出错了）：在它后面补一条「没结果」，不然 Claude 和多数兼容接口都会整轮报错；
    - 对不上调用的工具结果丢掉；夹在工具调用和结果中间的 system 挪到结果后面；
    - 拒答、出错、空的 assistant 不发；被停下的半截回复后面写明「被停了」，免得模型以为自己说完了。"""
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
        with store.read() as conn:
            rows = conn.execute("SELECT id, parent, kind, role, data, turn, ts FROM engine_entries WHERE session=? ORDER BY rowid",
                                (self.id,)).fetchall()
        return {r["id"]: dict(r) for r in rows}

    def path(self) -> list[dict]:
        """叶子走回根的那一串（按先后），每条 {id, parent, kind, role, data(str), turn, ts}，含不是消息的条目。"""
        rows, out, cur, seen = self._rows(), [], self.leaf, set()
        while cur and cur in rows and cur not in seen:
            seen.add(cur)
            out.append(rows[cur])
            cur = rows[cur]["parent"]
        return out[::-1]

    def messages(self) -> list[dict]:
        """当前这一枝上的消息（中立格式，原样，没修过）。压缩摘要这类条目的翻法是第 4 项的事，现在跳过。"""
        return [json.loads(r["data"]) for r in self.path() if r["kind"] == "message"]

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
        只能撤到 user 消息上（Hermes 的规矩：撤到半中间会留下没有结果的工具调用）。"""
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
            out.append({"id": r["id"], "parent": r["parent"], "kind": r["kind"], "role": r["role"], "ts": r["ts"], "preview": text_of(m)[:80],
                        "on_path": r["id"] in on})
        return out
