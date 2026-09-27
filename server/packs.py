"""功能包（pack）：一个包 = 几张表 + 一组积木 + 一份用法（GUIDE.md），也可以带几条提醒建议。装到某个 Agent 上，它就会这一套。

- 装的时候：表没有就建，已经有同名的就只补缺的字段、choice 缺的选项（已有的字段、数据一概不动）；积木加到它的看板上（同 id 的块已经有了就跳过，
  只标上是这个包的）；用法 Agent 按需要读（board_ctl.py pack guide <包名>，board skill 里写了什么时候读）。装包不改 Agent 的 skills，不碰配置。
- 两种装法（和看板一样）：用户在对话里让装的 → apply 直接装（看板顶上有撤回）；Agent 自己想到的 → propose，收件箱 kind block 的卡，
  预览是装好以后的看板，点了「装上」才装（草稿表转正、补字段、记下装了），点「不要」草稿表归档。
- 提醒是新推送：装包时不直接开，每条另外出一张收件箱卡（alerts.py），用户点了同意才开。
- 卸载只把这个包的积木从看板上拿掉（新的一版，能回去），表和数据都留着。
- 包放在仓库的 packs/<名字>/pack.json（server.json 的 packs_dir 能换地方）。文字可以写成 {"zh": …, "en": …}，装的时候按 server.json 的语言挑。
  pack.json：{name, version, title, summary, for（适合哪种 Agent，只是提示）, tables: [{name, title, fields}], blocks: [积木…],
  alerts: [提醒…], guide: "GUIDE.md"（英文版 GUIDE.en.md）}。
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

import boards
import data
from boards import bad, bdb
from chat import _lock, log_activity, now_iso
from config import REPO, raw, settings
from i18n import L

router = APIRouter()
NAME = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")


def pdb() -> sqlite3.Connection:
    conn = bdb()
    conn.execute("""CREATE TABLE IF NOT EXISTS pack_installs (agent TEXT NOT NULL, pack TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
        installed_at TEXT NOT NULL, by TEXT NOT NULL DEFAULT 'agent', PRIMARY KEY(agent, pack))""")
    return conn


def packs_dir() -> Path:
    return Path(raw().get("packs_dir") or REPO / "packs").expanduser()


def loc(x: Any) -> Any:
    """{"zh": …, "en": …} → 按 server.json 的语言挑一个（包是装给 Agent 用的，跟服务器的语言走）；别的原样往下找。"""
    if isinstance(x, dict):
        if x and set(x) <= {"zh", "en"} and (all(isinstance(v, str) for v in x.values()) or all(isinstance(v, list) for v in x.values())):
            return x.get(settings.language) or x.get("en") or x.get("zh")
        return {k: loc(v) for k, v in x.items()}
    if isinstance(x, list):
        return [loc(v) for v in x]
    return x


def load(name: str) -> dict:
    """读一个包（已经按语言挑好文字）。"""
    if not NAME.match(name or ""):
        raise bad(f"包名「{name}」不对", f'Bad pack name "{name}"')
    f = packs_dir() / name / "pack.json"
    try:
        p = json.loads(f.read_text(encoding="utf8"))
    except OSError:
        raise bad(f"没有「{name}」这个功能包（board_ctl.py pack list 能看到有哪些）", f'No pack called "{name}" (see board_ctl.py pack list)', 404) from None
    except ValueError as e:
        raise bad(f"功能包「{name}」的 pack.json 坏了：{e}", f'Pack "{name}" has a broken pack.json: {e}', 500) from None
    if not isinstance(p, dict) or p.get("name") != name:
        raise bad(f"功能包「{name}」的 pack.json 里 name 要写 {name}", f'Pack "{name}": pack.json must say "name": "{name}"', 500)
    p = loc(p)
    p.setdefault("version", 1)
    for k in ("tables", "blocks", "alerts"):
        if not isinstance(p.get(k) or [], list):
            raise bad(f"功能包「{name}」的 {k} 要是列表", f'Pack "{name}": {k} must be a list', 500)
        p[k] = p.get(k) or []
    return p


def guide(p: dict) -> str:
    base = packs_dir() / p["name"]
    names = ["GUIDE.en.md", str(p.get("guide") or "GUIDE.md")] if settings.language == "en" else [str(p.get("guide") or "GUIDE.md"), "GUIDE.en.md"]
    for n in names:
        f = base / n
        if f.is_file() and f.resolve().parent == base.resolve():
            return f.read_text(encoding="utf8")
    return ""


def all_packs() -> list[dict]:
    out = []
    d = packs_dir()
    if not d.is_dir():
        return out
    for sub in sorted(d.iterdir()):
        if (sub / "pack.json").is_file():
            try:
                out.append(load(sub.name))
            except Exception:  # noqa: BLE001 — 坏的包不影响列出别的
                continue
    return out


def installed(conn: sqlite3.Connection, pack: str | None = None, agent: str | None = None) -> list[sqlite3.Row]:
    q, params = "SELECT * FROM pack_installs WHERE 1=1", []
    if pack:
        q += " AND pack=?"
        params.append(pack)
    if agent:
        q += " AND agent=?"
        params.append(agent)
    return conn.execute(q + " ORDER BY installed_at", params).fetchall()


def merge_fields(have: list[dict], want: list[dict]) -> tuple[list[dict], list[str], list[str]]:
    """已有的表补上包里要的字段：缺的字段接在后面，choice 缺的选项接在后面。已有字段的类型对不上就留着原来的（记下来告诉 Agent）。
    返回 (新的字段表, 改了什么（给人看）, 类型对不上的字段)。"""
    out = [dict(f) for f in have]
    by = {f["key"]: f for f in out}
    changes, clash = [], []
    for f in want:
        cur = by.get(f["key"])
        if not cur:
            out.append(dict(f))
            by[f["key"]] = out[-1]
            changes.append(L(f"加字段「{f['label']}」", f'add field "{f["label"]}"'))
        elif cur["type"] != f["type"]:
            clash.append(f["key"])
        elif f["type"] == "choice":
            extra = [o for o in f.get("options", []) if o not in cur.get("options", [])]
            if extra:
                opts = list(cur.get("options", []))
                tail = [o for o in opts[-1:] if o in ("其他", "其它", "Other", "Others")]  # 新选项插在「其他」前面
                cur["options"] = [*opts[: len(opts) - len(tail)], *extra, *tail]
                changes.append(L(f"「{cur['label']}」多几个选项：{'、'.join(extra)}", f'"{cur["label"]}" gets options: {", ".join(extra)}'))
    return out, changes, clash


def plan(conn: sqlite3.Connection, agent: str, dashboard: str | None, p: dict) -> dict:
    """装这个包会做什么（先不动）：新建哪些表、哪些表补字段、看板加哪几块、哪几块已经有了。"""
    new_tables, merges, clashes, lines = [], {}, {}, []
    for t in p["tables"]:
        name = str(t.get("name") or "")
        if not boards.KEY.match(name):
            raise bad(f"功能包里的表名「{name}」不对", f'Bad table name "{name}" in the pack', 500)
        fields = boards.clean_fields(t.get("fields"))
        cur = conn.execute("SELECT * FROM collections WHERE agent=? AND name=? AND status IN ('active','draft')", (agent, name)).fetchone()
        if not cur:
            new_tables.append({"name": name, "title": str(t.get("title") or name)[:40], "fields": fields})
            lines.append(L(f"建一张表「{t.get('title') or name}」（{len(fields)} 个字段）", f'Create a table "{t.get("title") or name}" ({len(fields)} fields)'))
            continue
        merged, changes, clash = merge_fields(json.loads(cur["fields"]), fields)
        if changes:
            merges[name] = merged
            lines.append(L(f"「{cur['title']}」表：", f'"{cur["title"]}" table: ') + L("，", ", ").join(changes))
        if clash:
            clashes[name] = clash
    live = boards.blocks_of(boards.live_row(conn, agent))
    have = {b["id"]: b for b in live}
    anchors = boards.anchors_of(dashboard)
    pack_ids = {str(b.get("id")) for b in p["blocks"] if isinstance(b, dict)}
    add, adopt = [], []
    for b in p["blocks"]:
        if not isinstance(b, dict):
            continue
        if b.get("id") in have:
            if have[b["id"]].get("type") == b.get("type") and not have[b["id"]].get("pack"):
                adopt.append(b["id"])
            continue
        nb = {**b, "pack": p["name"]}
        after = nb.get("after")
        if after and after not in anchors and after not in pack_ids and after not in have:
            nb.pop("after")  # 包是按饮食看板写的，装到别的 Agent 上那一节不存在：放最后
        add.append(nb)
    blocks = [({**b, "pack": p["name"]} if b.get("id") in adopt else b) for b in live]
    order = [str(b.get("id")) for b in p["blocks"] if isinstance(b, dict)]
    for nb in add:  # 按包里的先后插：放在包里排在它后面、看板上已经有的那一块前面（同一节里先后就对了），没有就接在最后
        later = order[order.index(nb["id"]) + 1:]
        at = next((i for i, b in enumerate(blocks) if b.get("id") in later), len(blocks))
        blocks.insert(at, nb)
    titles = [b.get("title") or " / ".join(a.get("label", "") for a in b.get("actions", [])) or b["id"] for b in add]
    if titles:
        lines.append(L(f"看板加 {len(titles)} 块：{'、'.join(titles)}", f"Add {len(titles)} block(s) to the board: {', '.join(titles)}"))
    if adopt:
        lines.append(L(f"看板上已经有 {len(adopt)} 块，不重复加", f"{len(adopt)} block(s) already on the board, not added twice"))
    if p["alerts"]:
        lines.append(L(f"{len(p['alerts'])} 条提醒另外出卡，你点了同意才开", f"{len(p['alerts'])} reminder(s) come as separate cards; none is on until you say yes"))
    return {"newTables": new_tables, "merges": merges, "clashes": clashes, "add": [b["id"] for b in add], "adopt": adopt, "blocks": blocks,
            "changes": lines}


def create_tables(conn: sqlite3.Connection, agent: str, tables: list[dict], status: str) -> None:
    n = conn.execute("SELECT COUNT(*) FROM collections WHERE agent=? AND status IN ('active','draft')", (agent,)).fetchone()[0]
    if n + len(tables) > boards.MAX_COLLECTIONS:
        raise bad(f"一个 Agent 最多 {boards.MAX_COLLECTIONS} 张表", f"At most {boards.MAX_COLLECTIONS} tables per Agent")
    ts = now_iso()
    for t in tables:
        conn.execute("INSERT OR REPLACE INTO collections(agent, name, title, fields, status, created_at, updated_at) VALUES(?,?,?,?,?,?,?)",
                     (agent, t["name"], t["title"], json.dumps(t["fields"], ensure_ascii=False), status, ts, ts))


def apply_merges(conn: sqlite3.Connection, agent: str, merges: dict[str, list[dict]]) -> dict[str, dict]:
    """已有的表补字段、补选项（提案也先补上：预览要用新字段算；被拒了 undo 再退回去）。返回 {表: {"fields": [新加的字段], "options": {字段: [新加的选项]}}}。"""
    added: dict[str, dict] = {}
    for name, fields in merges.items():
        cur = conn.execute("SELECT fields FROM collections WHERE agent=? AND name=?", (agent, name)).fetchone()
        if not cur:
            continue
        old = {f["key"]: f for f in json.loads(cur["fields"])}
        merged, _, _ = merge_fields(list(old.values()), fields)
        conn.execute("UPDATE collections SET fields=?, updated_at=? WHERE agent=? AND name=?", (json.dumps(merged, ensure_ascii=False), now_iso(), agent, name))
        added[name] = {"fields": [f["key"] for f in merged if f["key"] not in old],
                       "options": {f["key"]: [o for o in f.get("options", []) if o not in old[f["key"]].get("options", [])]
                                   for f in merged if f["key"] in old and f["type"] == "choice" and f.get("options") != old[f["key"]].get("options")}}
    return added


def undo(conn: sqlite3.Connection, agent: str, meta: dict) -> None:
    """装包的提案没成：草稿表归档；补上的字段和选项还没人用的退回去（有数据了就留着，免得丢东西）。"""
    ts = now_iso()
    for name in meta.get("tables", []):
        conn.execute("UPDATE collections SET status='archived', updated_at=? WHERE agent=? AND name=? AND status='draft'", (ts, agent, name))
    for name, a in (meta.get("added") or {}).items():
        cur = conn.execute("SELECT fields FROM collections WHERE agent=? AND name=?", (agent, name)).fetchone()
        if not cur:
            continue
        used = set()
        for r in conn.execute("SELECT data FROM records WHERE agent=? AND collection=? AND deleted_at IS NULL", (agent, name)):
            d = json.loads(r["data"])
            used |= {k for k in a.get("fields", []) if d.get(k) not in (None, "")}
            used |= {f"{k}={d.get(k)}" for k in a.get("options", {}) if d.get(k) in a["options"][k]}
        fields = []
        for f in json.loads(cur["fields"]):
            if f["key"] in a.get("fields", []) and f["key"] not in used:
                continue
            if f["key"] in a.get("options", {}):
                f = {**f, "options": [o for o in f.get("options", []) if o not in a["options"][f["key"]] or f"{f['key']}={o}" in used]}
            fields.append(f)
        conn.execute("UPDATE collections SET fields=?, updated_at=? WHERE agent=? AND name=?", (json.dumps(fields, ensure_ascii=False), ts, agent, name))


def finish(conn: sqlite3.Connection, agent: str, meta: dict, by: str) -> None:
    """表转正、记下装了（直接装和提案被同意都走这里；字段在 apply_merges 时已经补上了）。"""
    ts = now_iso()
    for name in meta.get("tables", []):
        conn.execute("UPDATE collections SET status='active', updated_at=? WHERE agent=? AND name=? AND status='draft'", (ts, agent, name))
    conn.execute("INSERT OR REPLACE INTO pack_installs(agent, pack, version, installed_at, by) VALUES(?,?,?,?,?)",
                 (agent, meta["pack"], int(meta.get("version") or 1), ts, by))


def on_decided(conn: sqlite3.Connection, agent: str, meta: dict, action: str) -> None:
    """装包的提案被点了（boards.on_block_decided 里、同一个事务）：同意 → 收尾；不要 / 撤回 → 草稿表归档、没用上的新字段退回去。"""
    if not meta.get("pack"):
        return
    if action == "approve":
        finish(conn, agent, meta, "proposal")
    else:
        undo(conn, agent, meta)


async def after_decided(agent: str, meta: dict, action: str) -> None:
    if meta.get("pack") and action == "approve":
        import alerts  # noqa: PLC0415 — alerts 引用 packs，放这里免得互相引用
        await alerts.propose_from_pack(agent, meta["pack"])


boards.DECIDED.append(on_decided)
boards.AFTER_DECIDED.append(after_decided)


def summary(p: dict, conn: sqlite3.Connection | None = None) -> dict:
    out = {"name": p["name"], "version": p["version"], "title": p.get("title") or p["name"], "summary": p.get("summary") or "",
           "for": p.get("for") or [], "tables": [{"name": t.get("name"), "title": t.get("title") or t.get("name")} for t in p["tables"]],
           "blocks": [{"id": b.get("id"), "type": b.get("type"), "title": b.get("title") or ""} for b in p["blocks"] if isinstance(b, dict)],
           "alerts": [{"id": a.get("id"), "title": a.get("title") or a.get("id")} for a in p["alerts"] if isinstance(a, dict)]}
    if conn is not None:
        out["installedOn"] = [r["agent"] for r in installed(conn, p["name"])]
    return out


# —— 接口 ————————————————————————————————————————————————————————

@router.get("/api/packs")
def list_packs():
    with _lock, pdb() as conn:
        return {"ok": True, "packs": [summary(p, conn) for p in all_packs()]}


@router.get("/api/packs/{name}")
def get_pack(name: str):
    p = load(name)
    with _lock, pdb() as conn:
        return {"ok": True, **summary(p, conn), "guide": guide(p)}


class InstallIn(BaseModel):
    agent: str
    mode: str = "apply"     # apply 直接装（用户明确让装的）/ propose 出卡等用户点头 / check 只看会做什么
    why: str = ""
    title: str = ""
    dedupe: str = ""


@router.post("/api/packs/{name}/install")
async def install(name: str, body: InstallIn):
    """装一个包。表和补的字段先落下（提案的预览要用它们算），积木：apply 直接换上新的一版，propose 存成草稿交收件箱，check 只报会做什么、什么都不留。"""
    p = load(name)
    g = boards.group_row(body.agent)
    agent = g["id"]
    if body.mode not in ("apply", "propose", "check"):
        raise bad("mode 只能是 apply / propose / check", "mode must be apply, propose or check")
    title = p.get("title") or name
    with _lock, pdb() as conn:
        pl = plan(conn, agent, g["dashboard"], p)
        create_tables(conn, agent, pl["newTables"], "active" if body.mode == "apply" else "draft")
        added = apply_merges(conn, agent, pl["merges"])
        blocks = boards.clean_blocks(conn, agent, {"blocks": pl["blocks"]}, g["dashboard"])  # 包写错了在这里就报，表和字段跟着回滚
        if body.mode == "check":
            was = bool(installed(conn, name, agent))
            conn.rollback()
            return {"ok": True, "check": True, "changes": pl["changes"], "clashes": pl["clashes"], "add": pl["add"], "adopt": pl["adopt"], "installed": was}
        meta = {"pack": name, "version": p["version"], "tables": [t["name"] for t in pl["newTables"]], "added": added}
        v = None
        if body.mode == "apply":
            finish(conn, agent, meta, "agent")
            if pl["add"] or pl["adopt"]:
                v = boards.put_live(conn, agent, blocks, L(f"装上了功能包「{title}」", f'Installed the "{title}" pack'), "agent")
    if body.mode == "apply":
        log_activity(L(f"给{g['name']}装上了功能包「{title}」", f'Installed the "{title}" pack for {g["name"]}'), "board", actor=data.agent_label(agent))
        import alerts  # noqa: PLC0415 — alerts 引用 packs，放这里免得互相引用
        cards = await alerts.propose_from_pack(agent, name)
        return {"ok": True, "version": v, "changes": pl["changes"], "clashes": pl["clashes"], "alertCards": cards}
    res = await boards.propose(agent, g, blocks, note=L(f"装上功能包「{title}」", f'Install the "{title}" pack'),
                               title=body.title.strip() or L(f"给「{g['name']}」装上「{title}」", f'Add the "{title}" pack to {g["name"]}'),
                               why=body.why or str(p.get("summary") or ""), changes=pl["changes"], dedupe=body.dedupe or f"pack:{agent}:{name}",
                               approve=L("装上", "Install"), meta=meta)
    if not isinstance(res, dict):  # 30 天内被拒过：刚建的草稿表、刚补的字段都退回去
        with _lock, pdb() as conn:
            undo(conn, agent, meta)
        return res
    return {**res, "changes": pl["changes"], "clashes": pl["clashes"]}


class RemoveIn(BaseModel):
    agent: str


@router.post("/api/packs/{name}/remove")
def remove(name: str, body: RemoveIn):
    """卸载：看板上这个包的积木拿掉（新的一版，能回去），表和数据都留着。"""
    g = boards.group_row(body.agent)
    with _lock, pdb() as conn:
        live = boards.blocks_of(boards.live_row(conn, g["id"]))
        keep = [b for b in live if b.get("pack") != name]
        conn.execute("DELETE FROM pack_installs WHERE agent=? AND pack=?", (g["id"], name))
        v = boards.put_live(conn, g["id"], keep, L(f"拿掉了功能包「{name}」的积木（数据都在）", f'Removed the "{name}" pack\'s blocks (data kept)'), "agent") \
            if len(keep) != len(live) else None
    return {"ok": True, "version": v, "removed": len(live) - len(keep)}
