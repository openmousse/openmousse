"""mousse owner migrate（2026-10-20）：把库里记成老值的「你自己」（'leo'）换成新的值（默认 'owner'），再把 server.json 的 owner_id 写上。

  mousse owner migrate [--to owner] [--apply] [--db 路径] [--json]

为什么：日程、目标、项目、饮食这几张表里，你自己改的东西记成一个 id（config.owner_id；别的值是 main 或 Agent 的 id）。老的安装记的是
  'leo'；读的时候 'leo'、'owner' 和 server.json 配的那个都算你自己（config.is_owner），所以不迁也照样能用。想让库里只剩一个值才跑它。
  --to 写别的（比如 leo）就是往回换：除了它以外算你自己的值都换成它。
默认只看：列出哪张表、哪一列、多少行会改，什么都不动。--apply 才改：
  1. 先查 server.json 读得了、写上 owner_id 以后整份校验没有 ✗（不然什么都不动）；
  2. 备份数据库（sqlite3 的 backup()，放 server.json 的 backup_dir，默认 <data_dir>/backups/，0600）；
  3. 一个事务里把 COLUMNS 这几列里算你自己的老值换成新值；
  4. server.json 先备份（server.json.bak-<时间>），再写 owner_id：以后新写的就是新值（服务每次读文件，不用重启）；
  5. 中间服务又写进来的老值，再扫一遍。
不碰：世界树的叶子（来源在你自己的笔记里，memtree 两种都认）、改动记录里存的改动前后的快照（before / after：撤销时写回去的老值照样认）、
  别的列。不在任何地方自动跑（安装器、更新、doctor 都不跑它）。
退出码：0 看完了 / 改好了 / 没什么要改；1 出错（库打不开、写不了、server.json 读不了或写上以后校验不过）；2 参数不对（--to 不像一个 id）。
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path

import config
from i18n import L

from . import common as C
from . import confcheck

# 存「谁改的」的地方：(表, 列)。schedule.py（日程、标记、改动记录）、projects.py、goals.py、diet.py（记录、食物库、营养目标）
COLUMNS = (("schedule_items", "source"), ("schedule_marks", "source"), ("schedule_log", "actor"),
           ("project_items", "source"), ("project_log", "actor"), ("goals", "added_by"), ("goal_log", "actor"),
           ("diet_entries", "created_by"), ("diet_foods", "created_by"), ("diet_targets", "source"), ("diet_targets", "created_by"))


def old_values(c: dict, new: str) -> tuple[str, ...]:
    """要换掉的值：除了 new 以外算你自己的（'owner'、'leo' 和 server.json 现在配的 owner_id）。"""
    cur = c.get("owner_id") if isinstance(c.get("owner_id"), str) else ""
    vals = [*config.OWNER_IDS, cur.strip()]
    return tuple(v for v in dict.fromkeys(vals) if v and v != new)


def plan(conn: sqlite3.Connection, olds: tuple[str, ...]) -> list[dict]:
    """每一处要改几行；表或列不在（新实例、没用过这个功能）的跳过。"""
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    marks = ",".join("?" * len(olds))
    out = []
    for table, col in COLUMNS:
        if table not in tables or col not in {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}:
            continue
        n = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {col} IN ({marks})", olds).fetchone()[0] if olds else 0  # noqa: S608 — 表名、列名是常量
        out.append({"table": table, "column": col, "rows": int(n)})
    return out


def rewrite(conn: sqlite3.Connection, where: list[dict], olds: tuple[str, ...], new: str) -> int:
    """一个事务里换掉；→ 改了几行。"""
    if not olds:
        return 0
    marks = ",".join("?" * len(olds))
    n = 0
    with conn:
        for w in where:
            n += conn.execute(f"UPDATE {w['table']} SET {w['column']}=? WHERE {w['column']} IN ({marks})", (new, *olds)).rowcount  # noqa: S608
    return n


def backup_db(db: Path, c: dict) -> Path:
    """sqlite3 的 backup()（在写的库也拷得对）→ backup_dir 里的 <库名>-before-owner-<时间>.db（0600）。"""
    folder = Path(str(c["backup_dir"])).expanduser() if c.get("backup_dir") else C.data_dir(c) / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dst, n = folder / f"{db.stem}-before-owner-{stamp}.db", 1
    while dst.exists():  # 同一秒里跑两次
        dst, n = folder / f"{db.stem}-before-owner-{stamp}-{n}.db", n + 1
    fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    src, out = sqlite3.connect(db), sqlite3.connect(dst)
    try:
        src.backup(out)
    finally:
        out.close()
        src.close()
    return dst


def report(where: list[dict], olds: tuple[str, ...], new: str, db: Path) -> None:
    C.say(L(f"{C.tilde(db)}：你自己记成的 {' / '.join(olds) or '（没有）'} → {new}", f"{C.tilde(db)}: 'you' recorded as {' / '.join(olds) or '(none)'} → {new}"))
    for w in where:
        n = w["rows"]
        if n:
            C.say(f"  {w['table'] + '.' + w['column']:<28} {L(f'{n} 行', f'{n} rows')}")
    total = sum(w["rows"] for w in where)
    C.say(L(f"一共 {total} 行。", f"{total} rows in all."))


def main(a: argparse.Namespace) -> int:
    new = str(a.to or config.OWNER_ID_NEW).strip()
    if not config.OWNER_RE.match(new) or new == "main":
        C.say(L(f"--to {new!r} 不行：写成小写字母、数字、- 或 _（不能是 main）", f"--to {new!r} won't do: use lowercase letters, digits, - or _ (not main)"))
        return 2
    c, problem = C.load_config()
    if problem in ("broken", "unreadable") or c is None:
        C.say(L("server.json 读不了：先修好（mousse doctor 看为什么）", "server.json can't be read: fix it first (see mousse doctor)"))
        return 1
    db = Path(a.db).expanduser() if a.db else C.db_path(c)
    olds = old_values(c, new)
    if not db.is_file():
        where: list[dict] = []
    else:
        try:
            with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
                where = plan(conn, olds)
        except sqlite3.Error as e:
            C.say(L(f"库打不开：{e}", f"can't open the database: {e}"))
            return 1
    total = sum(w["rows"] for w in where)
    out = {"ok": True, "db": C.tilde(db), "from": list(olds), "to": new, "changes": [w for w in where if w["rows"]], "total": total,
           "applied": False, "backup": None, "configBackup": None, "ownerId": c.get("owner_id")}
    if not a.json:
        report(where, olds, new, db)
    if not a.apply:
        if not a.json:
            C.say(L("这是预演，什么都没改。加 --apply 才改（先备份数据库和 server.json）。", "This was a dry run; nothing changed. Add --apply to change it "
                    "(the database and server.json are backed up first)."))
        else:
            print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    try:
        before, after = confcheck.change(["owner_id"], new)
    except RuntimeError as e:
        C.say(str(e))
        return 1
    bad = confcheck.errors(confcheck.validate(after))
    if bad:
        C.say(L("写上 owner_id 以后 server.json 校验不过，什么都没改：", "server.json wouldn't validate with owner_id set; nothing changed:"))
        confcheck.show(bad)
        return 1
    try:
        if total:
            out["backup"] = C.tilde(backup_db(db, c))
            with sqlite3.connect(db) as conn:
                out["applied"] = rewrite(conn, where, olds, new) > 0
        if before.get("owner_id") != new:
            bak = confcheck.write(after)
            out["configBackup"] = C.tilde(bak) if bak else None
        out["ownerId"] = new
        if total:
            with sqlite3.connect(db) as conn:  # 改库和写 server.json 中间服务写进来的老值
                rewrite(conn, plan(conn, olds), olds, new)
    except (OSError, sqlite3.Error) as e:
        C.say(L(f"没改完：{e}（备份在 {out['backup'] or '（没备份）'}）", f"didn't finish: {e} (backup: {out['backup'] or 'none'})"))
        return 1
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        C.say(L(f"改好了：库换成 {new}，server.json 的 owner_id = {new}。", f"Done: the database now says {new}, and server.json has owner_id = {new}.")
              + (L(f"数据库备份在 {out['backup']}。", f" Database backup: {out['backup']}.") if out["backup"] else ""))
    return 0
