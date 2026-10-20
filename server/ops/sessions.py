"""mousse sessions cleanup（第 7 批，2026-10-19）：清掉旧的内核会话记录（模型那边看的逐字记录），给 mousse.db 瘦身。

  mousse sessions cleanup [--older-than 90d] [--agent ID] [--dry-run] [--yes] [--no-vacuum] [--json]

清什么：mousse.db 里 engine_sessions 的会话和它的 engine_entries（每一条消息、工具结果、压缩摘要），最后一次动它（updated_at）在
  --older-than 以前的（默认 90 天；写 1d 起，30d、12w、6m、1y 都认，不写单位 = 天）；--agent 只清这个 Agent 的。对外接口的会话表
  （engine_api_sessions）里会话记录都清光了、也那么久没用过、没有攒着的点头结果的那几行一起去掉（ACP 的 session/list 不再列它们）。
  不动：app 里看得到的聊天记录（messages）、事件流（engine_turns / engine_steps：活动页、用量和花费照旧）、记忆、世界树、工作区。
不清（列出来，写明为什么）：
  today       今天（逻辑日，04:00 换天）的会话
  running     有一轮还在跑（engine_turns 里 running 的）
  card        有还在等点头的卡（engine_calls 是 pending / running）
  task        后台任务还没跑完、或者结果还没交回去（engine_tasks queued / running，delivery pending / sent / steered）：它的子会话和
              派它的那一轮所在的会话
  job         定时任务正在跑的那一轮（engine_job_runs 没收尾的）所在的会话
  writeback   最近 WRITEBACK_DAYS 天里还没写回记忆的主人会话（写回只看最近这几天动过的：等它写完；engine.writeback 关了、claw 不是 builtin 不管）
做法：先列出来（几条会话、多少条目、大约多大，按 Agent 和种类分；不清的按原因数），--dry-run 到此为止；没写 --yes 就在终端里问一句
  （没有终端 = 拒绝，退出码 2）。动手时在一个写事务里再算一遍（这中间变成在用的照样不清），删完 VACUUM（--no-vacuum 不做；服务在跑
  一轮就先等它停下最多 IDLE_WAIT 秒，等不到或者库被占着就跳过，说一声），报库前后的大小。
  不建表、不改表：直接用 sqlite3 打开（不经 engine/store.connect：那里会补老库缺的栏），没有内核的表就什么都不做。
退出码：0 清了 / 没什么可清 / --dry-run；1 出错（库打不开、删的时候出错）；2 拒绝（没终端又没 --yes、参数不对）。
参考：OpenClaw 的 sessions cleanup（docs/cli/sessions.md：先报再动、--dry-run、--json）、Hermes 的 hermes sessions prune（--older-than 天数、
  跳过没结束的会话、删完 VACUUM）；只借设计，没拿代码。
"""
from __future__ import annotations

import argparse
import contextlib
import json
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from i18n import L

from . import common as C

DAY_START_HOUR = 4     # 逻辑日 04:00 换（同 chat.DAY_START_HOUR；命令行不为它导入整个 chat）
DEFAULT_AGE = "90d"
WRITEBACK_DAYS = 3     # 同 engine/writeback.RECENT_DAYS（命令行不为它导入整个内核；测试里对着查）
WRITEBACK_SKIP = ("api:", "task:", "cron:", "heartbeat:", "compact:", "writeback:", "skills:", "eval:")  # 同 engine/writeback.SKIP_CONVS
IDLE_WAIT = 60         # VACUUM 之前服务正在跑一轮：最多等几秒
KEY = re.compile(r"^(?:mousse|cyber):(?P<conv>.+):(?P<day>\d{4}-\d\d-\d\d)$")
AGE = re.compile(r"^\s*(\d+)\s*([dwmy]?)\s*$", re.IGNORECASE)
UNIT = {"": 1, "d": 1, "w": 7, "m": 30, "y": 365}
TASK_LIVE = "{p}status IN ('queued','running') OR {p}delivery IN ('pending','sent','steered')"
WHY = {"today": ("今天的", "today's"), "running": ("一轮还在跑", "a turn still running"), "card": ("有卡在等点头", "an inbox card waiting"),
       "task": ("后台任务没收尾", "a background task not finished"), "job": ("定时任务在跑", "a scheduled job running"),
       "writeback": ("还没写回记忆", "not written back to memory yet")}


def parse_age(text: str) -> int:
    """90d / 12w / 6m / 1y / 90 → 天数（至少 1）。不认 → ValueError。"""
    m = AGE.match(str(text or ""))
    days = int(m.group(1)) * UNIT[m.group(2).lower()] if m else 0
    if days < 1:
        raise ValueError(text)
    return days


def _tz(c: dict) -> ZoneInfo:
    try:
        return ZoneInfo(str(c.get("timezone") or "UTC"))
    except Exception:  # noqa: BLE001 — 写错的时区：照 UTC（mousse config validate 会报）
        return ZoneInfo("UTC")


def _writeback_on(c: dict) -> bool:
    """写回开着没有（同 engine/writeback.enabled：claw 是 builtin，engine.writeback 不是 false）。"""
    claw = c.get("claw") if isinstance(c.get("claw"), dict) else {}
    e = c.get("engine") if isinstance(c.get("engine"), dict) else {}
    return str(claw.get("kind") or "").strip().lower() == "builtin" and e.get("writeback") is not False


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=30, isolation_level=None)  # 自己管事务（BEGIN IMMEDIATE / VACUUM 不能在事务里）
    conn.row_factory = sqlite3.Row
    return conn


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _db_size(path: Path) -> int:
    return sum(p.stat().st_size for p in (path, path.with_name(path.name + "-wal")) if p.exists())


@dataclass
class Plan:
    db: Path
    days: int
    cutoff: str                 # 这个时间以前动过的才清（ISO，带时区）
    today: str                  # 今天的逻辑日
    agent: str | None
    recent: str | None = None   # 写回的窗口从这个时间起（写回关着 = None）；apply 照同一个再算
    ids: list[str] = field(default_factory=list)
    entries: int = 0
    bytes: int = 0
    by_agent: Counter = field(default_factory=Counter)
    by_kind: Counter = field(default_factory=Counter)
    kept: Counter = field(default_factory=Counter)  # 为什么不清 → 几条
    api: list[str] = field(default_factory=list)    # engine_api_sessions 里一起去掉的对话
    size: int = 0               # 库现在多大（主文件 + -wal）

    def summary(self) -> dict:
        return {"db": C.tilde(self.db), "olderThanDays": self.days, "cutoff": self.cutoff, "today": self.today, "agent": self.agent,
                "sessions": len(self.ids), "entries": self.entries, "bytes": self.bytes, "byAgent": dict(self.by_agent.most_common()),
                "byKind": dict(self.by_kind.most_common()), "kept": dict(self.kept), "apiSessions": len(self.api), "dbBytes": self.size}


def _protected(conn: sqlite3.Connection, have: set[str], recent: str | None) -> dict[str, str]:
    """在用的会话 id → 为什么不清（running / card / task / job / writeback）。recent = None：不管写回。"""
    out: dict[str, str] = {}

    def mark(sql: str, why: str, args: tuple = ()) -> None:
        for r in conn.execute(sql, args):
            if r[0] and r[0] not in out:
                out[r[0]] = why
    if "engine_turns" in have:
        mark("SELECT DISTINCT session FROM engine_turns WHERE status='running'", "running")
        if "engine_calls" in have:
            mark("SELECT DISTINCT t.session FROM engine_calls c JOIN engine_turns t ON t.id = c.turn WHERE c.status IN ('pending','running')",
                 "card")
        if "engine_job_runs" in have:
            mark("SELECT DISTINCT t.session FROM engine_job_runs r JOIN engine_turns t ON t.id = r.turn WHERE r.ended_at IS NULL", "job")
    if "engine_tasks" in have:
        mark(f"SELECT session FROM engine_tasks WHERE {TASK_LIVE.format(p='')}", "task")
        if "engine_turns" in have:
            mark(f"SELECT t.session FROM engine_tasks k JOIN engine_turns t ON t.id IN (k.turn, k.child_turn) WHERE {TASK_LIVE.format(p='k.')}",
                 "task")
    if recent is not None and "engine_writeback" in have:
        rows = conn.execute("SELECT s.id, s.key FROM engine_sessions s LEFT JOIN engine_writeback w ON w.session = s.id WHERE s.kind='owner' "
                            "AND julianday(s.updated_at) >= julianday(?) AND (w.session IS NULL OR w.status NOT IN ('done','skipped') "
                            "OR COALESCE(s.leaf, '') != COALESCE(w.upto, ''))", (recent,))
        for r in rows:
            m = KEY.match(r["key"] or "")
            if m and r["key"].startswith("mousse:") and not m.group("conv").startswith(WRITEBACK_SKIP):
                out.setdefault(r["id"], "writeback")
    return out


def _scan(conn: sqlite3.Connection, p: Plan) -> tuple[list[sqlite3.Row], dict[str, str]]:
    """够老的会话里，哪些清（行）、哪些不清（id → 为什么）。"""
    have = _tables(conn)
    q, args = "SELECT id, key, kind, agent FROM engine_sessions WHERE julianday(updated_at) < julianday(?)", [p.cutoff]
    if p.agent:
        q += " AND agent=?"
        args.append(p.agent)
    old = conn.execute(q + " ORDER BY julianday(updated_at)", args).fetchall()
    busy = _protected(conn, have, p.recent)
    keep: dict[str, str] = {}
    go: list[sqlite3.Row] = []
    for r in old:
        m = KEY.match(r["key"] or "")
        if m and m.group("day") >= p.today:
            keep[r["id"]] = "today"
        elif r["id"] in busy:
            keep[r["id"]] = busy[r["id"]]
        else:
            go.append(r)
    return go, keep


def _fill(conn: sqlite3.Connection, ids: list[str]) -> None:
    """要清的 id 放进一张临时表（TEMP：只在这个连接里，不动库的结构）。"""
    conn.execute("CREATE TEMP TABLE IF NOT EXISTS doomed (id TEXT PRIMARY KEY)")
    conn.execute("DELETE FROM temp.doomed")
    conn.executemany("INSERT OR IGNORE INTO temp.doomed(id) VALUES(?)", [(i,) for i in ids])


def _api_rows(conn: sqlite3.Connection, have: set[str], cutoff: str, agent: str | None) -> list[str]:
    """engine_api_sessions 里能一起去掉的对话：会话记录（清完以后）一条不剩、那么久没用过、没有攒着的点头结果（notes）。
    在 _fill 之后调：清单里的会话当已经没了。"""
    if "engine_api_sessions" not in have:
        return []
    q = ("SELECT a.conversation FROM engine_api_sessions a WHERE julianday(a.updated_at) < julianday(?) AND COALESCE(a.notes, '[]') IN ('', '[]') "
         "AND NOT EXISTS (SELECT 1 FROM engine_sessions s WHERE substr(s.key, 1, length(a.conversation) + 8) = 'mousse:' || a.conversation || ':' "
         "AND s.id NOT IN (SELECT id FROM temp.doomed))")
    args: list = [cutoff]
    if agent:
        q += " AND a.agent=?"
        args.append(agent)
    return [r[0] for r in conn.execute(q, args)]


def plan(days: int, agent: str | None = None, *, now: datetime | None = None) -> Plan | None:
    """算出会清哪些（不动库）。库不在、没有内核的表 → None。"""
    c = C.cfg()
    path = C.db_path(c)
    tz = _tz(c)
    now = (now or datetime.now(tz)).astimezone(tz)
    p = Plan(db=path, days=days, cutoff=(now - timedelta(days=days)).isoformat(timespec="seconds"),
             today=(now - timedelta(hours=DAY_START_HOUR)).strftime("%Y-%m-%d"), agent=agent)
    if not path.is_file():
        return None
    p.size = _db_size(path)
    p.recent = (now - timedelta(days=WRITEBACK_DAYS)).isoformat(timespec="seconds") if _writeback_on(c) else None
    conn = _connect(path)
    try:
        if "engine_sessions" not in _tables(conn):
            return None
        conn.execute("BEGIN")  # 一个读事务里看完：前后一致
        go, keep = _scan(conn, p)
        p.ids = [r["id"] for r in go]
        p.by_agent.update(r["agent"] or "?" for r in go)
        p.by_kind.update(r["kind"] or "?" for r in go)
        p.kept.update(keep.values())
        _fill(conn, p.ids)
        if "engine_entries" in _tables(conn) and p.ids:
            r = conn.execute("SELECT COUNT(*), COALESCE(SUM(length(e.data)), 0) FROM engine_entries e JOIN temp.doomed d ON d.id = e.session").fetchone()
            p.entries, p.bytes = int(r[0]), int(r[1])
        p.api = _api_rows(conn, _tables(conn), p.cutoff, agent)
        conn.execute("ROLLBACK")
    finally:
        conn.close()
    return p


def apply(p: Plan, *, vacuum: bool = True) -> dict:
    """照 plan 清：在一个写事务里再算一遍（这中间变成在用的不清），只删两次都算出来的；然后 VACUUM。→ 结果。"""
    out: dict = {"sessions": 0, "entries": 0, "apiSessions": 0, "vacuum": None}
    conn = _connect(p.db)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            go, _keep = _scan(conn, p)
            planned, listed = set(p.ids), set(p.api)
            ids = [r["id"] for r in go if r["id"] in planned]
            _fill(conn, ids)
            api = [x for x in _api_rows(conn, _tables(conn), p.cutoff, p.agent) if x in listed]
            have = _tables(conn)
            if "engine_entries" in have:
                out["entries"] = conn.execute("DELETE FROM engine_entries WHERE session IN (SELECT id FROM temp.doomed)").rowcount
            out["sessions"] = conn.execute("DELETE FROM engine_sessions WHERE id IN (SELECT id FROM temp.doomed)").rowcount
            if api:
                out["apiSessions"] = conn.executemany("DELETE FROM engine_api_sessions WHERE conversation=?", [(x,) for x in api]).rowcount
            conn.execute("COMMIT")
        except BaseException:
            with contextlib.suppress(sqlite3.Error):
                conn.execute("ROLLBACK")
            raise
        if vacuum and (out["sessions"] or out["apiSessions"]):
            out["vacuum"] = _vacuum(conn, p.db)
    finally:
        conn.close()
    return out


def _vacuum(conn: sqlite3.Connection, path: Path) -> dict:
    """VACUUM（库要独占一会儿：服务在跑一轮就先等它停下）。→ {"done", "before", "after", "error"}。"""
    before = _db_size(path)
    if C.busy() and not C.wait_idle(seconds=IDLE_WAIT):
        return {"done": False, "before": before, "after": before, "error": L(f"服务一直在跑（等了 {IDLE_WAIT} 秒）", f"the server stayed busy "
                                                                              f"({IDLE_WAIT} s)")}
    try:
        conn.execute("VACUUM")
        with contextlib.suppress(sqlite3.Error):
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.OperationalError as e:
        return {"done": False, "before": before, "after": _db_size(path), "error": str(e)}
    return {"done": True, "before": before, "after": _db_size(path), "error": None}


# —— 命令行 ——————————————————————————————————————————————————————————————————

def show(p: Plan) -> None:
    C.say(L(f"库：{C.tilde(p.db)}（{C.human_size(p.size)}）", f"Database: {C.tilde(p.db)} ({C.human_size(p.size)})"))
    who = L(f"（只看 {p.agent}）", f" (only {p.agent})") if p.agent else ""
    C.say(L(f"{p.cutoff[:10]} 以前（{p.days} 天）最后动过的会话{who}：", f"Sessions last touched before {p.cutoff[:10]} ({p.days} days){who}:"))
    if p.ids:
        C.say(L(f"  清   {len(p.ids)} 条会话 · {p.entries} 条条目 · 大约 {C.human_size(p.bytes)}",
                f"  remove  {len(p.ids)} sessions · {p.entries} entries · about {C.human_size(p.bytes)}"))
        top = p.by_agent.most_common(8)
        more = len(p.by_agent) - len(top)
        C.say(L("        按 Agent：", "          by Agent: ") + ", ".join(f"{a} {n}" for a, n in top) + (L(f"，还有 {more} 个", f", {more} more") if more > 0 else ""))
        C.say(L("        按种类：", "          by kind: ") + ", ".join(f"{k} {n}" for k, n in p.by_kind.most_common()))
    else:
        C.say(L("  没有要清的", "  nothing to remove"))
    if p.kept:
        C.say(L(f"  不清 {sum(p.kept.values())} 条：", f"  keep    {sum(p.kept.values())}: ")
              + L("，", ", ").join(f"{L(*WHY.get(k, (k, k)))} {n}" for k, n in p.kept.most_common()))
    if p.api:
        C.say(L(f"  对外接口的会话表里一起去掉 {len(p.api)} 行（会话记录已经一条不剩）", f"  also    {len(p.api)} API sessions with nothing left"))
    C.say(L("app 里的聊天记录、活动页、用量和花费都不动。", "The app's chat history, the activity page and spend aren't touched."))


def main(a: argparse.Namespace) -> int:
    if getattr(a, "action", None) != "cleanup":
        return 2
    try:
        days = parse_age(a.older_than or DEFAULT_AGE)
    except ValueError:
        C.say(L(f"--older-than 不认：{a.older_than}（写 90d、12w、6m、1y，至少 1 天）", f"Bad --older-than: {a.older_than} (use 90d, 12w, 6m "
                "or 1y; at least 1 day)"))
        return 2
    agent = (a.agent or "").strip() or None
    try:
        p = plan(days, agent)
    except sqlite3.Error as e:
        C.say(L(f"✗ 读不了库：{e}", f"✗ Can't read the database: {e}"))
        return 1
    if p is None:
        if a.json:
            print(json.dumps({"db": C.tilde(C.db_path()), "sessions": 0, "note": "no engine tables"}, ensure_ascii=False))
        else:
            C.say(L("库里没有内核的会话（没用过自己的内核）：没什么可清。", "No engine sessions in the database (the built-in engine was never "
                    "used): nothing to clean."))
        return 0
    if not a.json:
        show(p)
    if a.dry_run or not (p.ids or p.api):
        if a.json:
            print(json.dumps({**p.summary(), "dryRun": bool(a.dry_run), "removed": None}, ensure_ascii=False, indent=2))
        return 0
    if not a.yes:
        if a.json or not C.ask_yes(L(f"清掉这 {len(p.ids)} 条会话？（要留一份的话先 mousse backup create）",
                                     f"Remove these {len(p.ids)} sessions? (run mousse backup create first if you might want them back)")):
            if a.json:
                print(json.dumps({**p.summary(), "dryRun": False, "removed": None, "refused": "add --yes"}, ensure_ascii=False, indent=2))
            else:
                C.say(L("没清。不问就清：加 --yes", "Nothing removed. To skip the question: add --yes"))
            return 2
    try:
        res = apply(p, vacuum=not a.no_vacuum)
    except sqlite3.Error as e:
        C.say(L(f"✗ 没清成（什么都没删）：{e}", f"✗ Cleanup failed (nothing was removed): {e}"))
        return 1
    if a.json:
        print(json.dumps({**p.summary(), "dryRun": False, "removed": res}, ensure_ascii=False, indent=2))
        return 0
    C.say(L(f"✓ 清掉 {res['sessions']} 条会话（{res['entries']} 条条目）", f"✓ Removed {res['sessions']} sessions ({res['entries']} entries)")
          + (L(f"、对外接口的会话 {res['apiSessions']} 行", f" and {res['apiSessions']} API sessions") if res["apiSessions"] else ""))
    v = res.get("vacuum")
    if v and v["done"]:
        C.say(f"✓ VACUUM：{C.human_size(v['before'])} → {C.human_size(v['after'])}")
    elif v:
        cmd = f"{C.PY} -c \"import sqlite3; sqlite3.connect({str(p.db)!r}).execute('VACUUM')\""
        C.say(L(f"! 没 VACUUM（{v['error']}）：记录已经删了，文件要 VACUUM 以后才变小。服务停着的时候跑：\n  {cmd}",
                f"! VACUUM skipped ({v['error']}): the rows are gone, but the file only shrinks after a VACUUM. With the server stopped, run:\n  {cmd}"))
    return 0
