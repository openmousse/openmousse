"""枝（2026-10-16）：主干是档案；枝按生活领域分大枝、按功能分小枝（一个功能 Agent 一根），叶子挂在枝上。

两种存储一个样子：
- SQLite 存储：tree.db 的 branch 表一枝一行（name、parent = 上一级的名字（空 = 主干）、about、aliases、agents、ord）。
- Markdown 存储：笔记文件夹 branches/ 里一枝一篇笔记，文件名就是枝名。属性 parent: "[[上一级]]"（"[[档案]]" = 直接长在主干上）、
  aliases 别名、agents 默认挂到这根枝的 Agent id、order 排序；正文第一行写它管什么。索引（index.db）里有同样一张 branch 表，扫笔记时重建。
  枝的笔记和规则文件（rules.md）不算记忆，扫笔记时跳过。
- 叶子的 branch：SQLite 是 tree 表的 branch 列（NULL = 直接挂主干）；笔记是属性 branch: "[[枝名]]"，"[[档案]]" = 直接挂主干。
  名字和别名都认（不分大小写）；对不上任何一根的照原样留着（算主干，每周修剪时列进「挂错枝的」）。
默认只长两样：主干（档案 / Profile）和「人 / People」。每个功能 Agent 建好时给它长一根（服务器那边调 ensure_agent），
别的枝靠每周修剪的 sprout 长出来。老用户升级：已有叶子不动（没有 branch 的算主干），第一次跑补上「人」。
写入时自动挂枝（pick）：写的人指定的 > 被取代的那片原来的枝 > 写的 Agent 自己那根（某根枝的 agents 里有它）> 标签对上枝名或别名的 > 主干。
"""
from __future__ import annotations

import json
import re
import sqlite3

from . import config as C

NAME_MAX = 40
PEOPLE = {
    "zh": ("人", ["家人", "伴侣", "朋友", "同学", "同事", "约定"],
           "身边的人和长期的约定：家人、伴侣、朋友、同学、同事是谁，和{name}什么关系、在意什么；答应别人的、别人答应的、说好定期做的。"),
    "en": ("People", ["family", "partner", "friends", "classmates", "colleagues", "commitments"],
           "The people around {name} and standing commitments: who family, partner, friends, classmates and colleagues are, "
           "how they relate to {name} and what matters to them; what was promised either way and what was agreed to do regularly."),
}
TRUNK_WORDS = ("档案", "主干", "profile", "trunk")    # 不论哪种语言都当主干（除非真有一根枝叫这个）
AGENT_PREFIXES = ("openclaw-", "grava-", "openmousse-")  # 来源的写法：openclaw-<Agent id>（memory-tree skill）、grava-<id>（作者的实例）
_BAD = re.compile(r'[\\/:*?"<>|#^\[\]\x00-\x1f]')
_LINK = re.compile(r"^\s*\[\[([^\]|#]*)(?:[#|][^\]]*)?\]\]\s*$")


def trunk() -> str:
    return C.trunk()


def link_name(v) -> str:
    """属性里写的枝或上一级：「[[人]]」「[[人|家里人]]」「人」，手写没加引号被 YAML 读成的 [["人"]] → 人。"""
    while isinstance(v, list):
        v = v[0] if v else ""
    s = str(v or "").strip() if isinstance(v, str) else ""
    m = _LINK.match(s)
    return (m.group(1) if m else s).strip()


def clean_name(name) -> str:
    """枝名：要能当文件名（Markdown 存储里枝就是 branches/<枝名>.md）。不行抛 ValueError。"""
    s = " ".join(str(name or "").split()).strip(" .")
    if not s or len(s) > NAME_MAX or _BAD.search(s):
        raise ValueError(C.L(f"枝名「{s}」不行：1 到 {NAME_MAX} 个字，不能有 \\ / : * ? \" < > | # ^ [ ]",
                             f'Branch name "{s}" won\'t do: 1–{NAME_MAX} characters, none of \\ / : * ? " < > | # ^ [ ]'))
    return s


def as_list(v) -> list[str]:
    """别名、Agent 这类属性：YAML 列表、JSON 文本、逗号隔开的一串都认 → 去重的字符串列表。"""
    if isinstance(v, str):
        try:
            v = json.loads(v) if v.strip().startswith("[") else [x for x in v.split(",")]
        except ValueError:
            v = [v]
    if not isinstance(v, list):
        return []
    out: list[str] = []
    for x in v:
        s = link_name(x) if isinstance(x, (str, list)) else ""
        if s and s not in out:
            out.append(s)
    return out


def rows(conn: sqlite3.Connection) -> list[dict]:
    """所有的枝（存下来的样子，不排先后）。"""
    try:
        rs = conn.execute("SELECT name, parent, about, aliases, agents, ord, path FROM branch ORDER BY ord, name").fetchall()
    except sqlite3.Error:  # 还没升级的库
        return []
    return [{"name": r["name"], "parent": r["parent"] or "", "about": r["about"] or "", "aliases": as_list(r["aliases"]),
             "agents": as_list(r["agents"]), "order": r["ord"] or 0, "path": r["path"]} for r in rs]


def lookup(conn: sqlite3.Connection, rs: list[dict] | None = None) -> dict[str, str]:
    """名字、别名（不分大小写）→ 枝名；主干的名字 → ""。枝名优先于别名，别名优先于主干的通用叫法。"""
    rs = rows(conn) if rs is None else rs
    out = {w: "" for w in TRUNK_WORDS}
    for b in rs:
        for a in b["aliases"]:
            out[a.casefold()] = b["name"]
    for b in rs:
        out[b["name"].casefold()] = b["name"]
    out[trunk().casefold()] = ""
    return out


def resolve(conn: sqlite3.Connection, name, rs: list[dict] | None = None) -> str | None:
    """枝名或别名 → 枝名；主干 → ""；对不上 → None。"""
    s = link_name(name)
    if not s:
        return None
    return lookup(conn, rs).get(s.casefold())


def tree(conn: sqlite3.Connection) -> list[dict]:
    """先序：大枝后面跟着它的小枝。每项 name、parent（大枝的是主干的名字）、about、agents（逗号连起来的 Agent id）、aliases、
    depth（大枝 1、小枝 2……）、leaves（直接挂在它上面的当前叶子数）、order。上一级对不上的、绕成圈的，都当大枝。"""
    rs = rows(conn)
    look = lookup(conn, rs)
    try:
        counts = {r[0]: r[1] for r in conn.execute(
            "SELECT branch, COUNT(*) FROM tree WHERE source != 'profile' AND status IN ('active','pending') AND branch IS NOT NULL GROUP BY branch")}
    except sqlite3.Error:
        counts = {}
    kids: dict[str, list[dict]] = {}
    for b in rs:
        p = look.get(b["parent"].casefold(), "") if b["parent"] else ""
        kids.setdefault("" if p == b["name"] else p, []).append(b)
    out: list[dict] = []
    seen: set[str] = set()
    top = trunk()

    def walk(parent: str, depth: int) -> None:
        for b in sorted(kids.get(parent, []), key=lambda x: (x["order"], x["name"].casefold())):
            if b["name"] in seen:
                continue
            seen.add(b["name"])
            out.append({"name": b["name"], "parent": parent or top, "about": b["about"], "agents": ",".join(b["agents"]),
                        "aliases": b["aliases"], "depth": depth, "leaves": counts.get(b["name"], 0), "order": b["order"]})
            walk(b["name"], depth + 1)
    walk("", 1)
    for b in sorted(rs, key=lambda x: (x["order"], x["name"].casefold())):  # 绕成圈的：从主干走不到，当大枝
        if b["name"] not in seen:
            kids.setdefault("#loose", []).append(b)
    walk("#loose", 1)
    for x in out:
        if x["parent"] == "#loose":
            x["parent"] = top
    return out


def of_agent(conn: sqlite3.Connection, agent: str) -> list[str]:
    """哪几根枝的 agents 里有它（先序）。"""
    if not agent:
        return []
    return [b["name"] for b in tree(conn) if agent in b["agents"].split(",")]


def agent_from(source: str) -> str:
    """来源里认出是哪个 Agent：openclaw-<id> / grava-<id> / openmousse-<id> → id（只有前缀 = main）；别的原样（可能就是 Agent id）。"""
    s = str(source or "").strip()
    for p in AGENT_PREFIXES:
        if s.startswith(p):
            return s[len(p):] or "main"
    return s


def pick(conn: sqlite3.Connection, *, explicit: str | None = None, old: dict | None = None, agent: str | None = None,
         source: str = "", tags: str = "") -> tuple[str | None, str | None]:
    """写入时挂哪根枝 → (枝名，None = 主干；给写的人看的一句说明，没有就 None)。顺序见文件开头。"""
    rs = rows(conn)
    note = None
    if explicit and link_name(explicit):
        hit = resolve(conn, explicit, rs)
        if hit is not None:
            return hit or None, None
        note = C.L(f"没有「{link_name(explicit)}」这根枝", f'There\'s no branch called "{link_name(explicit)}"')
    if old is not None and old.get("branch"):
        hit = resolve(conn, old["branch"], rs)
        if hit:
            return hit, note
    mine = of_agent(conn, agent or agent_from(source))
    if mine:
        return mine[0], note
    for t in re.split(r"[,，、]", tags or ""):
        hit = resolve(conn, t.strip().lstrip("#"), rs)
        if hit:
            return hit, note
    return None, note


# ---------- 写 ----------

def create(conn: sqlite3.Connection, name: str, *, parent: str = "", about: str = "", aliases=(), agents=(), actor: str = "tree") -> str:
    """长一根新枝 → 它的名字。重名（和别的枝名、别名、主干撞了）、上一级对不上抛 ValueError。"""
    from . import store as S
    name = clean_name(name)
    rs = rows(conn)
    look = lookup(conn, rs)
    if name.casefold() in look:
        raise ValueError(C.L(f"已经有叫「{name}」的枝（或别名）了", f'There\'s already a branch (or alias) called "{name}"'))
    up = resolve(conn, parent, rs) if link_name(parent) else ""
    if up is None:
        raise ValueError(C.L(f"没有「{link_name(parent)}」这根枝，长不上去", f'There\'s no branch called "{link_name(parent)}" to grow it on'))
    order = max((b["order"] for b in rs), default=0) + 1
    names = [a for a in dict.fromkeys(" ".join(str(a).split()) for a in aliases or ()) if a and a.casefold() not in look and a.casefold() != name.casefold()]
    who = [str(a).strip() for a in dict.fromkeys(agents or ()) if str(a).strip()]
    about = " ".join(str(about or "").split())
    if C.markdown():
        from . import notes
        notes.write_branch(conn, name, parent=up, about=about, aliases=names, agents=who, order=order)
    else:
        ts = S.now_iso()
        conn.execute("INSERT INTO branch(name, parent, about, aliases, agents, ord, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                     (name, up, about, json.dumps(names, ensure_ascii=False), json.dumps(who), order, ts, ts))
    S.log(conn, actor, C.L(f"长了一根枝「{name}」", f'Grew a branch "{name}"'))
    conn.commit()
    return name


def add_agent(conn: sqlite3.Connection, name: str, agent: str, actor: str = "tree") -> None:
    """把一个 Agent 加进这根枝的 agents（已经有就不动）。"""
    from . import store as S
    b = next((x for x in rows(conn) if x["name"] == name), None)
    if b is None or agent in b["agents"]:
        return
    agents = [*b["agents"], agent]
    if C.markdown():
        from . import notes
        notes.update_branch(conn, name, agents=agents)
    else:
        conn.execute("UPDATE branch SET agents=?, updated_at=? WHERE name=?", (json.dumps(agents), S.now_iso(), name))
    S.log(conn, actor, C.L(f"枝「{name}」多了一个 Agent", f'Branch "{name}" got another agent'))
    conn.commit()


def ensure_agent(conn: sqlite3.Connection, agent: str, name: str, about: str = "", actor: str = "tree") -> str | None:
    """给一个功能 Agent 长一根小枝（agents 填它的 id）→ 枝名。已经有一根枝的 agents 里有它就用那根；
    它的名字已经是某根枝的名字或别名 = 把它加进那根的 agents。名字不能当枝名的，用 Agent id。"""
    agent = str(agent or "").strip()
    if not agent or agent == "main":
        return None
    mine = of_agent(conn, agent)
    if mine:
        return mine[0]
    label = clean_name(" ".join(_BAD.sub(" ", str(name or "")).split()).strip(" .")[:NAME_MAX] or agent)
    hit = resolve(conn, label)
    if hit:
        add_agent(conn, hit, agent, actor)
        return hit
    if hit == "":  # Agent 的名字和主干撞了
        label = clean_name(f"{label} {agent}")
    return create(conn, label, about=about, agents=[agent], actor=actor)


def ensure_defaults(conn: sqlite3.Connection, actor: str = "tree") -> bool:
    """第一次跑：长出「人 / People」（已经有同名或同别名的就不长）。→ 这次长了没有。"""
    name, aliases, about = PEOPLE["zh" if C.lang() == "zh" else "en"]
    if resolve(conn, name) is not None:
        return False
    create(conn, name, about=about.replace("{name}", C.owner()), aliases=aliases, actor=actor)
    return True
