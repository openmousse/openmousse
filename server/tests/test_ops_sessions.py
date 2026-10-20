"""第 7 批 2.10：mousse sessions cleanup（ops/sessions.py）。临时的家目录、自己建的 mousse.db（内核那几张表照 engine/store.py 等的 SCHEMA），
不连服务、不连网。"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from engine import jobs, openai_api, store
from ops import sessions as S
from tests.ops_helpers import make_home, ns, run_mousse

TZ = "Asia/Shanghai"


@pytest.fixture
def h(tmp_path, monkeypatch):
    return make_home(tmp_path, monkeypatch)


def ago(days: float) -> str:
    return (datetime.now(ZoneInfo(TZ)) - timedelta(days=days)).isoformat(timespec="seconds")


def day(days: int) -> str:
    """几天前的逻辑日（04:00 换天）。"""
    return (datetime.now(ZoneInfo(TZ)) - timedelta(hours=S.DAY_START_HOUR) - timedelta(days=days)).strftime("%Y-%m-%d")


class Db:
    """一个内核用过的 mousse.db：会话、条目、轮、卡、后台任务、定时任务、对外接口的会话。"""

    def __init__(self, h, **cfg) -> None:
        self.dir = h.home / "data"
        self.dir.mkdir(parents=True, exist_ok=True)
        h.write({"timezone": TZ, "data_dir": str(self.dir), "claw": {"kind": "builtin"}, "bind": {"port": 9}, **cfg})
        self.path = self.dir / "mousse.db"
        self.conn = sqlite3.connect(self.path, isolation_level=None)
        for schema in (store.SCHEMA, jobs.SCHEMA, openai_api.SCHEMA):
            self.conn.executescript(schema)

    def session(self, sid: str, key: str, *, days: float = 100, kind: str = "owner", agent: str = "main", entries: int = 3) -> str:
        at = ago(days)
        leaf = f"{sid}-e{entries - 1}" if entries else None
        self.conn.execute("INSERT INTO engine_sessions(id, key, kind, agent, model, system, leaf, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                          (sid, key, kind, agent, "fake/m1", "rules", leaf, at, at))
        for i in range(entries):
            self.conn.execute("INSERT INTO engine_entries(id, session, parent, kind, role, data, turn, ts) VALUES(?,?,?,?,?,?,?,?)",
                              (f"{sid}-e{i}", sid, f"{sid}-e{i - 1}" if i else None, "message", "user",
                               json.dumps({"role": "user", "content": [{"type": "text", "text": "fictional line " * 20}]}), f"{sid}-t", at))
        return sid

    def turn(self, tid: str, sid: str | None, status: str = "done", days: float = 100) -> str:
        self.conn.execute("INSERT INTO engine_turns(id, conversation, session, kind, agent, model, origin, status, started_at) VALUES(?,?,?,?,?,?,?,?,?)",
                          (tid, "main", sid, "owner", "main", "fake/m1", "user", status, ago(days)))
        return tid

    def count(self, table: str, where: str = "1", args: tuple = ()) -> int:
        return self.conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}", args).fetchone()[0]

    def ids(self) -> set[str]:
        return {r[0] for r in self.conn.execute("SELECT id FROM engine_sessions")}


def cleanup(**kw) -> int:
    base = {"action": "cleanup", "older_than": "90d", "agent": None, "dry_run": False, "yes": True, "no_vacuum": False, "json": False}
    return S.main(ns(**{**base, **kw}))


def world(h) -> Db:
    db = Db(h)
    db.session("old-chat", f"mousse:main:{day(100)}")
    db.session("old-task", "mousse:task:t-done", kind="task")
    db.conn.execute("INSERT INTO engine_tasks(id, thread, agent, task, timeout_s, status, created_at, session, delivery) VALUES(?,?,?,?,?,?,?,?,?)",
                    ("t-done", "main", "main", "summarise", 600, "done", 1, "old-task", "delivered"))
    db.session("old-cyber", f"cyber:c1:{day(100)}", kind="cyber", agent="c1")
    db.session("old-diet", f"mousse:diet:{day(120)}", agent="diet", entries=2)
    # 在用的：都够老，照样不清
    db.session("card", f"mousse:diet:{day(101)}", agent="diet")
    db.turn("t-card", "card")
    db.conn.execute("INSERT INTO engine_calls(id, inbox, dedupe, tool, args, agent, thread, turn, card, status, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    ("call-1", "ib-1", "d", "journal", "[]", "diet", "diet", "t-card", "{}", "pending", ago(101)))
    db.session("parent", f"mousse:main:{day(102)}")
    db.turn("t-parent", "parent")
    db.session("child", "mousse:task:t-live", kind="task")
    db.conn.execute("INSERT INTO engine_tasks(id, thread, agent, task, timeout_s, status, created_at, turn, session, delivery) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    ("t-live", "main", "main", "research", 600, "running", 1, "t-parent", "child", "none"))
    db.session("undelivered", "mousse:task:t-wait", kind="task")
    db.conn.execute("INSERT INTO engine_tasks(id, thread, agent, task, timeout_s, status, created_at, session, delivery) VALUES(?,?,?,?,?,?,?,?,?)",
                    ("t-wait", "main", "main", "draft", 600, "done", 1, "undelivered", "pending"))
    db.session("job", f"mousse:fitness:{day(103)}", agent="fitness")
    db.turn("t-job", "job")
    db.conn.execute("INSERT INTO engine_job_runs(id, job, agent, started_at, kind, status, turn) VALUES(?,?,?,?,?,?,?)",
                    ("run-1", "j1", "fitness", ago(103), "agent", "running", "t-job"))
    db.session("running", f"mousse:main:{day(104)}")
    db.turn("t-run", "running", status="running")
    db.session("today", f"mousse:main:{day(0)}")  # 键是今天的（时间写得怪）：照样不清
    db.session("recent", f"mousse:main:{day(9)}", days=9)
    # 对外接口：清光了的那个对话一起去掉；攒着点头结果的、还有新会话的留着
    for conv, notes, days in (("api:main:aaa", "[]", 100), ("api:main:bbb", '["Approved: fictional"]', 100), ("api:main:ccc", "[]", 100)):
        db.conn.execute("INSERT INTO engine_api_sessions(conversation, token, agent, key, title, notes, created_at, updated_at) "
                        "VALUES(?,?,?,?,?,?,?,?)", (conv, "api", "main", conv[-3:], "t", notes, ago(days), ago(days)))
    db.session("api-old", f"mousse:api:main:aaa:{day(100)}")
    db.session("api-notes", f"mousse:api:main:bbb:{day(100)}")
    db.session("api-new", f"mousse:api:main:ccc:{day(5)}", days=5)
    db.turn("t-old", "old-chat")
    return db


GONE = {"old-chat", "old-task", "old-cyber", "old-diet", "api-old", "api-notes"}


def test_dry_run_lists_and_changes_nothing(h, capsys):
    db = world(h)
    before = db.ids()
    assert cleanup(dry_run=True, yes=False) == 0
    out = capsys.readouterr().out
    assert db.ids() == before
    assert "remove  6 sessions · 17 entries" in out and "main 4" in out and "diet 1" in out and "c1 1" in out
    for why in ("an inbox card waiting 1", "a background task not finished 3", "a scheduled job running 1", "a turn still running 1",
                "today's 1"):
        assert why in out, why
    assert "also    1 API sessions" in out and "The app's chat history" in out
    assert cleanup(dry_run=True, yes=False, json=True) == 0
    j = json.loads(capsys.readouterr().out)
    assert j["sessions"] == 6 and j["entries"] == 17 and j["kept"] == {"card": 1, "task": 3, "job": 1, "running": 1, "today": 1}
    assert j["byKind"] == {"owner": 4, "task": 1, "cyber": 1} and j["apiSessions"] == 1 and j["dryRun"] is True and j["removed"] is None
    assert j["db"] == "~/data/mousse.db" and j["olderThanDays"] == 90


def test_cleanup_removes_old_transcripts_and_keeps_live_ones(h, capsys):
    db = world(h)
    entries_before = db.count("engine_entries")
    assert cleanup(json=True) == 0
    j = json.loads(capsys.readouterr().out)
    assert j["removed"]["sessions"] == 6 and j["removed"]["entries"] == 17 and j["removed"]["apiSessions"] == 1
    assert j["removed"]["vacuum"]["done"] is True and j["removed"]["vacuum"]["after"] <= j["removed"]["vacuum"]["before"]
    assert db.ids().isdisjoint(GONE) and {"card", "parent", "child", "undelivered", "job", "running", "today", "recent", "api-new"} <= db.ids()
    assert db.count("engine_entries") == entries_before - 17 and db.count("engine_entries", "session IN ('old-chat','old-task')") == 0
    assert db.count("engine_turns", "id='t-old'") == 1  # 事件流（活动页、用量）不动
    assert db.count("engine_tasks", "id='t-done'") == 1 and db.count("engine_calls") == 1
    assert [r[0] for r in db.conn.execute("SELECT conversation FROM engine_api_sessions ORDER BY conversation")] == ["api:main:bbb", "api:main:ccc"]
    assert cleanup() == 0  # 再来一次：没什么可清
    assert "nothing to remove" in capsys.readouterr().out


def test_agent_and_age(h, capsys):
    db = world(h)
    assert cleanup(agent="diet") == 0
    assert "old-diet" not in db.ids() and "old-chat" in db.ids() and "card" in db.ids()
    assert cleanup(older_than="110d") == 0  # 只剩 110 天以前的（old-diet 已经清了）：没有
    assert {"old-chat", "old-task"} <= db.ids()
    assert cleanup(older_than="3m") == 0  # 90 天
    assert "old-chat" not in db.ids()
    assert [S.parse_age(x) for x in ("90d", "12w", "6m", "1y", "7", " 2D ")] == [90, 84, 180, 365, 7, 2]
    for bad in ("0d", "-3d", "soon", "", "1h"):
        with pytest.raises(ValueError):
            S.parse_age(bad)
    assert cleanup(older_than="1h") == 2 and "Bad --older-than" in capsys.readouterr().out


def test_recent_sessions_wait_for_writeback(h):
    db = Db(h)
    db.session("chat", f"mousse:main:{day(2)}", days=2)  # 写回还没写过
    db.session("written", f"mousse:main:{day(2)}", days=2)
    db.conn.execute("INSERT INTO engine_writeback(session, conversation, agent, upto, status, created_at, updated_at) VALUES(?,?,?,?,?,?,?)",
                    ("written", "main", "main", "written-e2", "done", ago(2), ago(2)))
    db.session("api", f"mousse:api:main:zzz:{day(2)}", days=2)  # 对外接口的对话不写回
    db.session("old", f"mousse:main:{day(5)}", days=5)  # 写回只看最近 3 天：过了就不等
    p = S.plan(1)
    assert set(p.ids) == {"written", "api", "old"} and p.kept == {"writeback": 1}
    h.write({**h.read(), "engine": {"writeback": False}})
    assert set(S.plan(1).ids) == {"chat", "written", "api", "old"}  # 写回关着：不等
    h.write({**h.read(), "engine": {}, "claw": {"kind": "openclaw"}})
    assert set(S.plan(1).ids) == {"chat", "written", "api", "old"}


def test_the_write_checks_again(h):
    db = world(h)
    p = S.plan(90)
    assert "old-chat" in p.ids
    db.turn("t-late", "old-chat", status="running")  # 列完、动手之前又跑起来了
    res = S.apply(p, vacuum=False)
    assert res["sessions"] == 5 and "old-chat" in db.ids() and res["vacuum"] is None


def test_refused_without_yes_or_a_terminal(h, capsys):
    db = world(h)
    before = db.ids()
    assert cleanup(yes=False) == 2  # pytest 里没有终端：不问、不清
    assert db.ids() == before and "add --yes" in capsys.readouterr().out
    assert cleanup(yes=False, json=True) == 2 and json.loads(capsys.readouterr().out)["refused"] == "add --yes"


def test_no_engine_tables_means_nothing_and_no_schema_change(h, capsys):
    data = h.home / "data"
    h.write({"timezone": TZ, "data_dir": str(data)})
    assert cleanup() == 0 and "nothing to clean" in capsys.readouterr().out  # 没有库
    data.mkdir()
    conn = sqlite3.connect(data / "mousse.db")
    conn.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, thread TEXT)")
    conn.commit()
    schema = conn.execute("SELECT name, sql FROM sqlite_master ORDER BY name").fetchall()
    assert cleanup() == 0 and "nothing to clean" in capsys.readouterr().out
    assert conn.execute("SELECT name, sql FROM sqlite_master ORDER BY name").fetchall() == schema  # 一张表都没多


def test_constants_match_the_engine():
    import chat
    from engine import writeback
    assert S.DAY_START_HOUR == chat.DAY_START_HOUR and S.WRITEBACK_DAYS == writeback.RECENT_DAYS and S.WRITEBACK_SKIP == writeback.SKIP_CONVS


def test_the_command_line(h):
    world(h)
    r = run_mousse(h, "sessions", "cleanup", "--dry-run", "--json")
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["sessions"] == 6
    r = run_mousse(h, "sessions", "cleanup", "--older-than", "never")
    assert r.returncode == 2
    r = run_mousse(h, "sessions", "--help")
    assert r.returncode == 0 and "cleanup" in r.stdout
