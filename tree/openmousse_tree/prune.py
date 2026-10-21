"""每周修剪（2026-10-16）：树一周修一次，计划由模型出，主人点了头才动。

三步，都在包里（OpenMousse 服务器负责出收件箱卡、点了以后调 apply；单独用的话命令行 prune-context / prune-plan / prune-apply）：
  context  列出可剪的：稳定两周以上的叶子（可以并进档案）、满 30 天的近况（过时的归档）、相近的几片（说同一件事的合成一片）、
           挂错枝的（挂的枝不在了、挂在主干上但标签对得上某根枝）、超过 30 片的枝（分成两根）。都没有就是 empty。
  plan     收一个 JSON 计划，校验（叶子在不在、是不是当前的、一片只出现在一步里、枝对不对得上……），存成 ~/.mousse-tree/prune/<那周周日>.json。
           同一周再交一份 = 换掉还没做的那份；做过的那份改名留着。
  apply    按存下的计划做：一步做完记一笔（done），再跑一遍跳过做过的（幂等）；叶子已经不在了的那步跳过，写明为什么。
           合并、改写出来的叶子 source = prune（supersedes 记第一片，「最早是谁记的」顺着它找回去）；合并和并进档案记下来源（origins.py）。
计划的样子（steps 按顺序做；why 可以每步都写，会出现在卡上）：
  {"summary": "一句话", "steps": [
    {"op": "merge",   "ids": ["a", "b"], "text": "合成的一句", "branch": "人"},     ← branch、kind 可不写：沿用第一片的
    {"op": "rewrite", "id": "a", "text": "改写后的一句"},
    {"op": "archive", "id": "a", "why": "过时了"},
    {"op": "promote", "id": "a", "section": "偏好", "text": "并进档案的一句"},       ← 并进档案那一节的末尾（没有这一节就加上），叶子归档
    {"op": "branch",  "id": "a", "to": "健身"},                                      ← 挪到别的枝（主干的名字 = 直接挂主干）
    {"op": "sprout",  "name": "健身", "parent": "身体", "about": "管什么", "aliases": [], "move": ["a", "b"]}]}  ← 长一根新枝，可以顺手挪几片过去
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from pathlib import Path

from . import branches as B
from . import config as C
from . import origins as O
from . import secrets as K
from . import store as S

STABLE_DAYS, RECENT_DAYS, CROWDED = 14, 30, 30
MAX_STEPS = 40
LIST_MAX = 40        # context 里每一类最多列几片
GROUP_MAX = 2000     # 叶子多过这么多就不两两比相似了（太慢）
OPS = ("merge", "rewrite", "archive", "promote", "branch", "sprout")
SOURCE = "prune"


class PlanError(ValueError):
    """计划不对：problems 一条一条写清楚（回给模型改）。"""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("\n".join(problems))


class _Skip(Exception):
    """这一步不做了（叶子已经不在了）：原因写在计划里。"""


def week_of(day: date | None = None) -> str:
    """那一周（周一到周日）的周日：修剪计划和收件箱卡都按它认。"""
    d = day or C.now().date()
    return (d + timedelta(days=6 - d.weekday())).isoformat()


def plan_dir() -> Path:
    return C.HOME / "prune"


def plan_path(week: str) -> Path:
    return plan_dir() / f"{week}.json"


def _clip(s, n: int) -> str:
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _age(s, today: date) -> int:
    try:
        return (today - date.fromisoformat(str(s or "")[:10])).days
    except ValueError:
        return 0


def _current(conn) -> dict[str, dict]:
    return {r["id"]: dict(r) for r in conn.execute(
        "SELECT * FROM tree WHERE source != 'profile' AND status IN ('active','pending') ORDER BY observed_at, created_at")}


# ---------- context ----------

def context(conn, today: date | None = None) -> dict:
    """可剪的（见文件开头）。每一类最多 LIST_MAX 片，counts 是全部的数。"""
    today = today or C.now().date()
    trunk = S.trunk()
    tree = B.tree(conn)
    names = {b["name"] for b in tree}
    cur = _current(conn)

    def leaf(r: dict) -> dict:
        return {"id": r["id"], "text": r["text"], "kind": r["kind"], "branch": r["branch"] or trunk, "source": r["source"],
                "status": r["status"], "observed_at": r["observed_at"], "tags": r["tags"] or ""}
    rows = list(cur.values())
    stable = [leaf(r) for r in rows if r["status"] == "active" and r["kind"] != "event" and _age(r["updated_at"], today) >= STABLE_DAYS]
    recent = [leaf(r) for r in rows if r["kind"] == "event" and _age(r["observed_at"], today) >= RECENT_DAYS]
    groups: list[list[dict]] = []
    if len(rows) <= GROUP_MAX:
        texts = [S.norm(r["text"]) for r in rows]
        parent = list(range(len(rows)))

        def find(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i
        for i in range(len(rows)):
            for j in range(i + 1, len(rows)):
                if S.ratio(texts[i], texts[j]) >= S.NEAR:
                    parent[find(j)] = find(i)
        by: dict[int, list[dict]] = {}
        for i, r in enumerate(rows):
            by.setdefault(find(i), []).append(leaf(r))
        groups = [g for g in by.values() if len(g) > 1]
    misplaced = []
    hints: dict[tuple[str, str], str | None] = {}
    for r in rows:
        if r["branch"] and r["branch"] in names:
            continue
        key = (r["source"], r["tags"] or "")
        if key not in hints:
            hints[key] = B.pick(conn, source=key[0], tags=key[1])[0]
        if r["branch"]:  # 挂的枝不在了（改了名、删了）
            misplaced.append({**leaf(r), "why": "gone", "suggest": hints[key]})
        elif hints[key]:  # 挂在主干上，但它该挂的那根枝有了（写它的 Agent 有自己的枝、标签对上了）
            misplaced.append({**leaf(r), "why": "trunk", "suggest": hints[key]})
    crowded = []
    for b in tree:
        if b["leaves"] > CROWDED:
            crowded.append({"branch": b["name"], "leaves": b["leaves"],
                            "items": [leaf(r) for r in rows if r["branch"] == b["name"]][:60]})
    out = {"week": week_of(today), "today": today.isoformat(), "trunk": trunk, "branches": tree, "sections": S.profile_sections(),
           "stable": stable[:LIST_MAX], "recent": recent[:LIST_MAX], "similar": groups[:LIST_MAX], "misplaced": misplaced[:LIST_MAX],
           "crowded": crowded, "leaves": len(rows),
           "counts": {"stable": len(stable), "recent": len(recent), "similar": len(groups), "misplaced": len(misplaced), "crowded": len(crowded)}}
    out["empty"] = not any(out["counts"].values())
    return out


def context_text(ctx: dict) -> str:
    """给模型看的（按配置的语言）。"""
    zh = C.lang() == "zh"
    sep = "，" if zh else ", "

    def line(x: dict, extra: str = "") -> str:
        meta = sep.join([x["branch"], x["kind"], x["observed_at"]] + ([C.L("待确认", "pending")] if x["status"] == "pending" else []))
        return f"- [{x['id']}] {x['text']}（{meta}）{extra}" if zh else f"- [{x['id']}] {x['text']} ({meta}){extra}"

    def more(key: str, shown: int) -> list[str]:
        n = ctx["counts"][key] - shown
        return [C.L(f"（还有 {n} 片没列出来，下周再看）", f"({n} more not listed; next week)")] if n > 0 else []
    out = [C.L(f"世界树这周（{ctx['week']} 那周）的修剪材料，今天 {ctx['today']}，当前叶子 {ctx['leaves']} 片。",
               f"Pruning material for the week ending {ctx['week']} (today {ctx['today']}); {ctx['leaves']} current leaves.")]
    if ctx["empty"]:
        out.append(C.L("这周没有可剪的：不用交计划。", "Nothing to prune this week: no plan needed."))
        return "\n".join(out)
    out += ["", C.L("## 枝（主干在最上面）", "## Branches (trunk first)"), f"- {ctx['trunk']}" + C.L("（主干：档案）", " (trunk: the profile)")]
    for b in ctx["branches"]:
        out.append("  " * b["depth"] + f"- {b['name']}" + C.L(f"（{b['leaves']} 片）", f" ({b['leaves']})") + (f"：{b['about']}" if zh and b["about"] else
                                                                                                          f": {b['about']}" if b["about"] else ""))
    out += ["", C.L("## 档案的小节（promote 的 section 从这里挑，也可以新开一节）", "## Profile sections (promote's section; a new one is fine too)"),
            ("、" if zh else ", ").join(ctx["sections"]) or C.L("（档案还没有小节）", "(no sections yet)")]
    sections = [
        ("stable", C.L("## 稳定两周以上的（以后一直用得上的 → promote 并进档案那一节；「人」那根枝上的不进档案）",
                       "## Held for two weeks or more (still useful for good → promote into that profile section; leaves about other people stay)")),
        ("recent", C.L("## 满 30 天的近况（过时了 → archive；还算数 → 不动或 rewrite）",
                       "## Recent-life leaves 30+ days old (outdated → archive; still true → leave or rewrite)")),
    ]
    for key, title in sections:
        if ctx[key]:
            out += ["", title, *(line(x) for x in ctx[key]), *more(key, len(ctx[key]))]
    if ctx["similar"]:
        out += ["", C.L("## 相近的几片（说的是同一件事 → merge 成一片；只是像 → 不动）", "## Similar leaves (same fact → merge into one; merely alike → leave)")]
        for i, g in enumerate(ctx["similar"], 1):
            out.append(C.L(f"第 {i} 组：", f"Group {i}:"))
            out += [line(x) for x in g]
        out += more("similar", len(ctx["similar"]))
    if ctx["misplaced"]:
        out += ["", C.L("## 挂错枝的（→ branch 挪过去）", "## Misplaced (→ branch to move it)")]
        for x in ctx["misplaced"]:
            why = (C.L(f"：挂的枝「{x['branch']}」不在了", f': its branch "{x["branch"]}" is gone') if x["why"] == "gone" else
                   C.L("：挂在主干上", ": on the trunk"))
            sug = C.L(f"，建议挂「{x['suggest']}」", f', suggested "{x["suggest"]}"') if x["suggest"] else ""
            out.append(line(x, why + sug))
        out += more("misplaced", len(ctx["misplaced"]))
    if ctx["crowded"]:
        out += ["", C.L(f"## 超过 {CROWDED} 片的枝（→ sprout 分出一根小枝，把一部分挪过去）",
                        f"## Branches with more than {CROWDED} leaves (→ sprout a small branch and move some leaves onto it)")]
        for c in ctx["crowded"]:
            out.append(C.L(f"「{c['branch']}」{c['leaves']} 片：", f'"{c["branch"]}" has {c["leaves"]}:'))
            out += [line(x) for x in c["items"]]
    out += ["", C.L("计划写法见 tree-prune skill；用 prune-plan --file - 交（JSON 放标准输入）。",
                    "How to write the plan: the tree-prune skill; submit it with prune-plan --file - (JSON on standard input).")]
    return "\n".join(out)


# ---------- plan ----------

def check(conn, data, *, others: dict[int, dict] | None = None, start: int = 1) -> dict:
    """校验、整理一份计划 → {"week", "summary", "steps"}；不对抛 PlanError（全部问题一起列出来）。
    others / start：只校验改过的一步（收件箱卡上「修改」重出的那一条，见 recheck）：others 是计划里别的几步（第几步 → 整理过的那步），
    它们用掉的叶子、长出的枝算数；start 是这一步是第几步（问题里这么写）。"""
    if isinstance(data, (str, bytes)):
        try:
            data = json.loads(data)
        except ValueError as e:
            raise PlanError([C.L(f"计划不是合法的 JSON：{e}", f"The plan isn't valid JSON: {e}")]) from None
    if isinstance(data, list):
        data = {"steps": data}
    if not isinstance(data, dict) or not isinstance(data.get("steps"), list):
        raise PlanError([C.L('计划要是一个对象：{"summary": "…", "steps": [ … ]}', 'The plan must be an object: {"summary": "…", "steps": [ … ]}')])
    week = str(data.get("week") or "").strip()
    try:
        week = week_of(date.fromisoformat(week)) if week else week_of()
    except ValueError:
        raise PlanError([C.L(f"week 写成 YYYY-MM-DD（{week}）", f"week must be YYYY-MM-DD ({week})")]) from None
    steps_in = data["steps"]
    problems: list[str] = []
    if len(steps_in) > MAX_STEPS:
        problems.append(C.L(f"一周最多 {MAX_STEPS} 步，这份有 {len(steps_in)} 步：挑最要紧的", f"At most {MAX_STEPS} steps a week; this has {len(steps_in)}"))
    cur = _current(conn)
    rs = B.rows(conn)
    look = B.lookup(conn, rs)
    sprouted: dict[str, str] = {}
    used: dict[str, int] = {}
    for k, o in (others or {}).items():
        for mid in step_ids(o):
            used.setdefault(mid, k)
        if o.get("op") == "sprout" and o.get("name"):
            for a in (o["name"], *(o.get("aliases") or [])):
                sprouted.setdefault(str(a).casefold(), o["name"])
    trunk = S.trunk()
    steps: list[dict] = []
    for n, st in enumerate(steps_in, start):
        bad = len(problems)

        def say(zh: str, en: str, n=n) -> None:
            problems.append(C.L(f"第 {n} 步：{zh}", f"Step {n}: {en}"))

        if not isinstance(st, dict):
            say("要是一个对象", "must be an object")
            continue
        op = str(st.get("op") or "").strip().lower()
        why = _clip(st.get("why"), 200)

        def one(x, n=n) -> dict | None:
            mid = str(x or "").strip()
            if mid not in cur:
                say(f"没有 {mid or '（空）'} 这片当前的叶子（档案要点、已经归档或忘掉的不算）",
                    f"{mid or '(empty)'} isn't a current leaf (profile points, archived and forgotten ones don't count)")
                return None
            if mid in used:
                say(f"{mid} 已经在第 {used[mid]} 步里了：一片叶子只能出现在一步里", f"{mid} is already in step {used[mid]}; a leaf can only be in one step")
                return None
            used[mid] = n
            return cur[mid]

        def target(name) -> str | None:
            s = B.link_name(name)
            hit = look.get(s.casefold(), sprouted.get(s.casefold()))
            if hit is None:
                say(f"没有「{s}」这根枝（可以先用 sprout 长出来）", f'there\'s no branch called "{s}" (sprout it first)')
            return hit

        def text_of(key: str = "text") -> str:
            t = " ".join(str(st.get(key) or "").split())
            if not t:
                say(f"要写 {key}（一句话）", f"{key} is required (one sentence)")
            elif len(t) > 500:
                say(f"{key} 太长了（最多 500 字）", f"{key} is too long (500 characters at most)")
            return t
        if op == "merge":
            ids = st.get("ids") if isinstance(st.get("ids"), list) else []
            if len(ids) < 2:
                say("merge 要 ids：至少两片", "merge needs ids: at least two leaves")
            got = [x for x in (one(i) for i in ids) if x]
            text = text_of()
            br = target(st["branch"]) if B.link_name(st.get("branch")) else None
            kind = str(st.get("kind") or "").strip()
            if kind and kind not in S.KINDS[:-1]:
                say(f"kind「{kind}」不认识", f'unknown kind "{kind}"')
            step = {"op": op, "ids": [x["id"] for x in got], "text": text, "branch": br, "kind": kind or None, "was": [x["text"] for x in got]}
        elif op in ("rewrite", "archive", "promote", "branch"):
            x = one(st.get("id"))
            step = {"op": op, "id": x["id"] if x else str(st.get("id") or ""), "was": x["text"] if x else ""}
            if op == "rewrite":
                step["text"] = text_of()
                if x and step["text"] == x["text"]:
                    say("改写后和原来一模一样", "the rewrite is identical to the original")
            elif op == "promote":
                step["section"] = " ".join(str(st.get("section") or "").split())
                if not step["section"]:
                    say("promote 要写 section：并进档案的哪一节", "promote needs section: which profile section")
                # 并进档案（USER.md、origins.json）的那句先隐去密钥：模型没写就用旧叶子的原文，那可能是 0.7.0 以前写的。
                # 在这里换，计划 JSON、查重用的 profile_id 和真写进去的是同一句
                step["text"] = K.redact(" ".join(str(st.get("text") or "").split()) or (x["text"] if x else ""))[0]
            elif op == "branch":
                to = target(st.get("to"))
                step["to"] = to
                step["from"] = (x["branch"] or trunk) if x else ""
                if x and to is not None and (x["branch"] or "") == to:
                    say("已经挂在这根枝上了", "it's already on that branch")
        elif op == "sprout":
            try:
                name = B.clean_name(st.get("name"))
            except ValueError as e:
                say(str(e), str(e))
                name = ""
            if name and (name.casefold() in look or name.casefold() in sprouted):
                say(f"已经有叫「{name}」的枝（或别名）了", f'there\'s already a branch (or alias) called "{name}"')
            up = target(st["parent"]) if B.link_name(st.get("parent")) else ""
            moves = st.get("move") if isinstance(st.get("move"), list) else []
            got = [x for x in (one(i) for i in moves) if x]
            aliases = [" ".join(str(a).split()) for a in (st.get("aliases") or []) if " ".join(str(a).split())] if isinstance(st.get("aliases"), list) else []
            agents = [str(a).strip() for a in (st.get("agents") or []) if str(a).strip()] if isinstance(st.get("agents"), list) else []
            about = _clip(st.get("about"), 200)
            if not about:
                say("sprout 要写 about：这根枝管什么", "sprout needs about: what the branch covers")
            if name:
                sprouted[name.casefold()] = name
                for a in aliases:
                    sprouted.setdefault(a.casefold(), name)
            step = {"op": op, "name": name, "parent": up or "", "about": about, "aliases": aliases, "agents": agents,
                    "move": [x["id"] for x in got], "was": [x["text"] for x in got]}
        else:
            say(f"op 只能是 {' / '.join(OPS)}（写的是「{op}」）", f"op must be one of {', '.join(OPS)} (got \"{op}\")")
            continue
        if why:
            step["why"] = why
        if len(problems) == bad:
            steps.append(step)
    if problems:
        raise PlanError(problems)
    return {"week": week, "summary": _clip(data.get("summary"), 300), "steps": steps}


def step_ids(st: dict) -> list[str]:
    """这一步动到的叶子。"""
    if st.get("op") == "merge":
        return [str(x) for x in st.get("ids") or []]
    if st.get("op") == "sprout":
        return [str(x) for x in st.get("move") or []]
    return [str(st["id"])] if st.get("id") else []


RAW = {"merge": ("ids", "text", "branch", "kind", "why"), "rewrite": ("id", "text", "why"), "archive": ("id", "why"),
       "promote": ("id", "section", "text", "why"), "branch": ("id", "to", "why"),
       "sprout": ("name", "parent", "about", "aliases", "agents", "move", "why")}


def raw_step(st: dict) -> dict:
    """存下的一步（check 整理过的，带 was / from / done…）→ 交计划时的写法：拿去给模型改、再校验一遍。"""
    out = {"op": st["op"], **{k: st[k] for k in RAW.get(st["op"], ()) if st.get(k) not in (None, "", [])}}
    if st["op"] == "branch" and not out.get("to"):
        out["to"] = S.trunk()
    return out


def recheck(conn, doc: dict, n: int, raw: dict) -> dict:
    """计划里第 n 步换成 raw（「修改」重出的那一条）：在别的几步的前提下校验这一步 → 整理好的这一步；不对抛 PlanError。"""
    others = {i: st for i, st in enumerate(doc["steps"], 1) if i != n}
    return check(conn, {"week": doc["week"], "steps": [raw]}, others=others, start=n)["steps"][0]


def digest_of(steps: list[dict]) -> str:
    return hashlib.sha256(json.dumps(steps, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]


def _save(plan: dict) -> None:
    from .notes import atomic_write
    plan_dir().mkdir(parents=True, exist_ok=True)
    atomic_write(plan_path(plan["week"]), json.dumps(plan, ensure_ascii=False, indent=1) + "\n")


def load(week: str | None = None) -> dict | None:
    """存下的那一周的计划；week 不给 = 最近的一份。"""
    if week is None:
        files = sorted(p for p in plan_dir().glob("????-??-??.json")) if plan_dir().is_dir() else []
        if not files:
            return None
        week = files[-1].stem
    try:
        d = json.loads(plan_path(week).read_text(encoding="utf8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def plan(conn, data, *, source: str = "owner") -> dict:
    """校验、存下一份计划 → {"week", "steps", "digest", "path"}。那一周已经有做过的一份：改名留着（<周日>.<时间>.json）。"""
    p = check(conn, data)
    with S.locked():
        old = load(p["week"])
        if old and old.get("status") in ("applied", "partial"):
            stamp = str(old.get("applied_at") or old.get("created_at") or S.now_iso()).replace(":", "")[:17]
            plan_path(p["week"]).replace(plan_dir() / f"{p['week']}.{stamp}.json")
        doc = {"week": p["week"], "summary": p["summary"], "steps": [{**s, "done": False} for s in p["steps"]],
               "digest": digest_of(p["steps"]), "source": source, "status": "planned", "created_at": S.now_iso()}
        _save(doc)
        S.log(conn, source, C.L(f"交了 {p['week']} 那周的修剪计划（{len(p['steps'])} 步）", f"Submitted the pruning plan for {p['week']} ({len(p['steps'])} steps)"))
        conn.commit()
    return {"week": doc["week"], "steps": len(doc["steps"]), "digest": doc["digest"], "path": str(plan_path(doc["week"]))}


def discard(week: str, status: str = "rejected") -> bool:
    """主人没要这份计划（拒了、卡过期了）：标上，不再做。做过的不动。"""
    with S.locked():
        doc = load(week)
        if not doc or doc.get("status") != "planned":
            return False
        doc.update(status=status, decided_at=S.now_iso())
        _save(doc)
    return True


# ---------- apply ----------

def apply(conn, week: str | None = None, *, digest: str | None = None, actor: str = "owner", only: set[int] | None = None) -> dict:
    """按存下的计划做（幂等：做过的步骤跳过）。digest 给了就要对得上（卡上那份就是要做的这份）。
    only：只做这几步（第几步，从 1 数；收件箱卡逐条点过、只同意了这几条），别的记成「没同意」跳过，以后也不做。
    → {"week", "done", "skipped": [(第几步, 为什么)], "failed": [(第几步, 出了什么错)], "summary"}"""
    doc = load(week)
    if not doc:
        raise PlanError([C.L("没有存下的修剪计划", "There's no stored pruning plan")])
    if digest and doc.get("digest") != digest:
        raise PlanError([C.L("存下的计划和卡上的不是同一份（后来又交过一份）", "The stored plan isn't the one on the card (a newer one was submitted)")])
    if doc.get("status") in ("rejected", "expired", "withdrawn"):
        raise PlanError([C.L("这份计划已经作废了", "This plan was discarded")])
    done = 0
    skipped: list[tuple[int, str]] = []
    failed: list[tuple[int, str]] = []
    with S.locked():
        for n, st in enumerate(doc["steps"], 1):
            if st.get("done"):
                done += 1
                continue
            if only is not None and n not in only and not st.get("skipped"):
                st["skipped"] = C.L("没同意", "not approved")
            if st.get("skipped"):
                skipped.append((n, st["skipped"]))
                continue
            try:
                st["result"] = _do(conn, st, actor)
            except _Skip as e:
                st["skipped"] = str(e)
                skipped.append((n, str(e)))
            except (ValueError, LookupError, RuntimeError, OSError) as e:
                st["error"] = _clip(e, 200)
                failed.append((n, st["error"]))
            else:
                st["done"] = True
                st.pop("error", None)
                done += 1
            _save(doc)  # 做一步记一步：中途停了，下次接着做
        doc.update(status="partial" if failed else "applied", applied_at=S.now_iso())
        _save(doc)
        S.log(conn, actor, C.L(f"照 {doc['week']} 那周的计划修剪了 {done} 步", f"Pruned {done} steps from the plan for {doc['week']}"))
        conn.commit()
    S.export(conn)
    return {"week": doc["week"], "done": done, "skipped": skipped, "failed": failed, "summary": summary(doc, done_only=True)}


def _leaf_now(conn, mid: str) -> dict | None:
    r = conn.execute("SELECT * FROM tree WHERE id=?", (mid,)).fetchone()
    return dict(r) if r else None


def _gone() -> _Skip:
    return _Skip(C.L("叶子已经不在了（改过、归档或忘掉了）", "the leaf is no longer current (changed, archived or forgotten)"))


def _made_by_prune(conn, first: str) -> str | None:
    """上一次跑到一半：这一步其实已经做了（有一片 source=prune、取代了第一片的当前叶子）。"""
    r = conn.execute("SELECT id FROM tree WHERE source=? AND supersedes=? AND status IN ('active','pending')", (SOURCE, first)).fetchone()
    return r["id"] if r else None


def _do(conn, st: dict, actor: str) -> dict:
    op = st["op"]
    if op in ("merge", "rewrite"):
        ids = st["ids"] if op == "merge" else [st["id"]]
        srcs = [x for x in (_leaf_now(conn, i) for i in ids) if x and x["status"] in S.CURRENT]
        if not srcs:
            if (mid := _made_by_prune(conn, ids[0])):
                return {"id": mid}
            raise _gone()
        first = srcs[0]
        if st.get("branch") is not None:
            br = B.resolve(conn, st["branch"]) if st["branch"] else ""
            if br is None:
                raise ValueError(C.L(f"没有「{st['branch']}」这根枝了", f'Branch "{st["branch"]}" is gone'))
        else:
            br = first["branch"] or ""
        tags = ",".join(dict.fromkeys(t for x in srcs for t in (x["tags"] or "").split(",") if t))
        said = next((x for x in srcs if x.get("quote") or x.get("thread")), None)  # 出处跟着走：原来那几片里第一片有出处的
        cues = S.cue_list([c for x in srcs for c in S.cue_list(x.get("cues") or "")])
        new = S.write(conn, text=st["text"], source=SOURCE, kind=st.get("kind") or first["kind"], tags=tags,
                      observed_at=max(x["observed_at"] for x in srcs), branch=br or None, replaces=[x["id"] for x in srcs], actor=actor,
                      prov={k: said.get(k) or "" for k in S.PROV} if said else None, cues=cues)
        O.add_leaf(new, ids, op)
        return {"id": new}
    if op == "archive":
        x = _leaf_now(conn, st["id"])
        if x and x["status"] == "archived":
            return {}
        if not x or x["status"] not in S.CURRENT:
            raise _gone()
        S.set_status(conn, st["id"], "archived", actor)
        return {}
    if op == "promote":
        x = _leaf_now(conn, st["id"])
        if not x or x["status"] not in S.CURRENT:
            if x and x["status"] == "archived" and S.profile_id(st["section"], st["text"]) in _profile_ids(conn):
                return {}
            raise _gone()
        pid = S.append_profile(st["section"], st["text"])
        S.set_status(conn, st["id"], "archived", actor)
        O.add_profile(pid, st["section"], st["text"], [st["id"]])
        S.sync_profile(conn)
        return {"profile": pid}
    if op == "branch":
        x = _leaf_now(conn, st["id"])
        if not x or x["status"] not in S.CURRENT:
            raise _gone()
        return {"branch": S.set_branch(conn, st["id"], st["to"] or S.trunk(), actor)}
    if op == "sprout":
        name = st["name"]
        if B.resolve(conn, name) is None:
            B.create(conn, name, parent=st.get("parent") or "", about=st.get("about") or "", aliases=st.get("aliases") or (),
                     agents=st.get("agents") or (), actor=actor)
        moved = []
        for mid in st.get("move") or []:
            x = _leaf_now(conn, mid)
            if x and x["status"] in S.CURRENT:
                S.set_branch(conn, mid, name, actor)
                moved.append(mid)
        return {"branch": name, "moved": moved}
    raise ValueError(op)


def _profile_ids(conn) -> set[str]:
    S.sync_profile(conn)
    return {r["id"] for r in conn.execute("SELECT id FROM tree WHERE source='profile'")}


# ---------- 给人看的 ----------

def lines(doc: dict) -> list[str]:
    """一步一行（卡上的「会改什么」），按配置的语言。"""
    out = []
    for st in doc["steps"]:
        op = st["op"]
        if op == "merge":
            out.append(C.L(f"合并 {len(st['ids'])} 片 →「{_clip(st['text'], 60)}」", f'Merge {len(st["ids"])} → "{_clip(st["text"], 60)}"'))
        elif op == "rewrite":
            out.append(C.L(f"改写「{_clip(st['was'], 30)}」→「{_clip(st['text'], 40)}」", f'Rewrite "{_clip(st["was"], 30)}" → "{_clip(st["text"], 40)}"'))
        elif op == "archive":
            out.append(C.L(f"归档「{_clip(st['was'], 60)}」", f'Archive "{_clip(st["was"], 60)}"'))
        elif op == "promote":
            out.append(C.L(f"并进档案「{st['section']}」：{_clip(st['text'], 60)}", f'Into the profile ("{st["section"]}"): {_clip(st["text"], 60)}'))
        elif op == "branch":
            out.append(C.L(f"「{_clip(st['was'], 40)}」挪到「{st['to'] or S.trunk()}」", f'Move "{_clip(st["was"], 40)}" to "{st["to"] or S.trunk()}"'))
        elif op == "sprout":
            moved = C.L(f"，挪过去 {len(st['move'])} 片", f", moving {len(st['move'])}") if st.get("move") else ""
            out.append(C.L(f"长一根新枝「{st['name']}」{moved}", f'New branch "{st["name"]}"{moved}'))
    return out


def summary(doc: dict, done_only: bool = False) -> str:
    """一句话：每种各几步（卡片标题；done_only = 做完的结果，只数做了的）。"""
    n: dict[str, int] = {}
    for st in doc["steps"]:
        if st.get("done") or not done_only:
            n[st["op"]] = n.get(st["op"], 0) + 1
    zh = {"merge": "合并", "rewrite": "改写", "archive": "归档", "promote": "并进档案", "branch": "挪枝", "sprout": "新枝"}
    en = {"merge": "merged", "rewrite": "rewritten", "archive": "archived", "promote": "into the profile", "branch": "moved", "sprout": "new branches"}
    return C.L("、".join(f"{zh[k]} {v}" for k, v in n.items()) or "没有改动", ", ".join(f"{v} {en[k]}" for k, v in n.items()) or "no changes")


def detail(doc: dict) -> str:
    """卡上的细节（Markdown）：每一步原来是什么、改成什么、为什么。"""
    out = []
    if doc.get("summary"):
        out += [doc["summary"], ""]
    for i, (st, ln) in enumerate(zip(doc["steps"], lines(doc)), 1):
        out.append(f"{i}. {ln}")
        was = st.get("was")
        for w in (was if isinstance(was, list) else [was] if was and st["op"] not in ("archive",) else []):
            if w:
                out.append(C.L(f"   - 原来：{_clip(w, 120)}", f"   - was: {_clip(w, 120)}"))
        if st.get("why"):
            out.append(C.L(f"   - 为什么：{st['why']}", f"   - why: {st['why']}"))
    return "\n".join(out)
