"""回原话核对（2026-10-17）：每周修剪把叶子并进档案、合并、改写之前，拿叶子的出处（原话 + 来自哪次对话）回那次对话的记录里找。

找对话记录交给调用方（find）：OpenMousse 服务器查它自己的对话（messages 表，treeprune.py），单独用这个包、没有对话记录可查的，
带原话的叶子都算「没法核对」。find(叶子) →
  None                                          这里查不了（记录不在这台服务器上）
  {"record": False, "where": …}                 该查得到的记录查不到（对话不在了、那几天没有用户说的话）
  {"record": True, "found": bool, "where": …, "said": …, "thread": …, "day": …, "message": …}
                                                查到了记录；found = 原话在用户说的话里；said = 指定的那条消息原来说的（对不上时给人看）
一片叶子的结果（state）：
  ok            与原对话相符
  mismatch      那次对话找到了，原话对不上
  missing       找不到原对话的记录
  unverifiable  没法核对（Claude.ai、ChatGPT 这些平台的对话不在这台服务器上）：照以前的办法，默认照做
  none          没有出处（以前的叶子）：照以前的办法，默认照做
一步（合并、改写、并进档案）按它动到的叶子里最差的那片算；mismatch、missing 默认不做（收件箱卡上默认「拒绝」）。归档、挪枝、长新枝不核对。
原话比对：去掉空白、标点，不分大小写以后是那次对话里某句的一段；差一两个字（错字、末尾截断）也算。
"""
from __future__ import annotations

import difflib
import re
from collections.abc import Callable

from . import config as C
from . import store as S

ORDER = ("mismatch", "missing", "unverifiable", "none", "ok")  # 从差到好
BAD = ("mismatch", "missing")                                  # 默认不做
CHECKED = ("merge", "rewrite", "promote")                      # 这几种要核对原话
_PUNCT = re.compile(r"[\W_]+")

Finder = Callable[[dict], "dict | None"]


def normal(s: str) -> str:
    return _PUNCT.sub("", (s or "").casefold())


def _grams(s: str) -> set[str]:
    return {s[i:i + 2] for i in range(len(s) - 1)} or ({s} if s else set())


def found_in(quote: str, text: str) -> bool:
    """原话在不在这段话里（见文件开头）。"""
    q, t = normal(quote), normal(text)
    if not q or not t:
        return False
    if q in t:
        return True
    if len(q) < 6:
        return False
    g = _grams(q)
    if len(g & _grams(t)) < 0.6 * len(g):  # 先粗筛：原话里的两字词大多都得在
        return False
    blocks = [b for b in difflib.SequenceMatcher(None, q, t, autojunk=False).get_matching_blocks() if b.size]
    if not blocks:
        return False
    if max(b.size for b in blocks) >= 0.8 * len(q):
        return True
    # 几段连在一起、中间只差一两个字
    best = 0
    for i in range(len(blocks)):
        total = 0
        for j in range(i, len(blocks)):
            if blocks[j].b + blocks[j].size - blocks[i].b > 1.25 * len(q):
                break
            total += blocks[j].size
            best = max(best, total)
    return best >= 0.9 * len(q)


def leaf(row: dict, find: Finder | None) -> dict:
    """一片叶子 → {"id", "state", "text"}（再带上 find 给的 where / day / thread / message，app 用来跳回那次对话）。"""
    q, th = row.get("quote") or "", row.get("thread") or ""
    out = {"id": row.get("id")}
    if not q:
        return {**out, "state": "none", "text": C.L("没有出处", "No source") if not th else C.L("只记了来自哪次对话，没有原话",
                                                                                                "Only the conversation was noted, no quote")}
    got = find(row) if find else None
    plat = row.get("platform") or row.get("source") or ""
    if got is None:
        return {**out, "state": "unverifiable", "text": C.L(f"没法回原对话核对（{plat or '那个平台'}的对话不在这里）",
                                                             f"Can't check against the conversation ({plat or 'that platform'}'s chats aren't here)")}
    where = str(got.get("where") or "")
    link = {k: got[k] for k in ("thread", "day", "message") if got.get(k) not in (None, "")}
    if not got.get("record"):
        tail = ((f"（{where}）" if C.lang() == "zh" else f" ({where})") if where else "")
        return {**out, "state": "missing", "text": C.L("找不到原对话的记录", "Couldn't find the original conversation") + tail, **link}
    if got.get("found"):
        return {**out, "state": "ok", "text": C.L("与原对话相符", "Matches the conversation")
                + ((f"：{where}" if C.lang() == "zh" else f": {where}") if where else ""), **link}
    said = S.clip(got.get("said") or "", 60)
    return {**out, "state": "mismatch", "text": (C.L(f"与原对话不符：那条消息说的是「{said}」", f'Doesn\'t match: that message says "{said}"') if said
                                                 else C.L("与原对话不符：那次对话里找不到这句原话", "Doesn't match: that conversation never says this")), **link}


def step(conn, st: dict, find: Finder | None) -> dict | None:
    """一步要不要核对、核对的结果 → {"state", "text", "leaves": [每片的结果]}；归档、挪枝、长新枝 → None。"""
    if st.get("op") not in CHECKED:
        return None
    ids = [str(x) for x in st.get("ids") or []] if st["op"] == "merge" else [str(st.get("id") or "")]
    rows = [dict(r) for r in (conn.execute("SELECT * FROM tree WHERE id=?", (i,)).fetchone() for i in ids) if r]
    per = [leaf(r, find) for r in rows]
    if not per:
        return None
    worst = min(per, key=lambda x: ORDER.index(x["state"]))
    text = worst["text"]
    if len(per) > 1:
        ok = sum(1 for x in per if x["state"] == "ok")
        text += C.L(f"（{len(per)} 片里 {ok} 片相符）", f" ({ok} of {len(per)} match)")
    return {"state": worst["state"], "text": text, "leaves": per}


def default(check: dict | None) -> str:
    """卡上这一条默认选什么：对不上、找不到原话的默认「拒绝」（不并），别的默认「同意」。"""
    return "reject" if check and check.get("state") in BAD else "approve"
