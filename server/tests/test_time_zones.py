"""时区改过以后（server.json 的 timezone 从里斯本改成上海）：库里的时间带的是写下那一刻的偏移，新旧两种混在一起。
按天取记录、上次聊到哪、内核轮次的排序都要按真时间比（julianday），不能按字符串；常用的查询照样走索引。不连模型。"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from zoneinfo import ZoneInfo

import pytest

import chat
import claw
import config
from engine import context, events, jobs, owner, store
from engine.session import text_of

LISBON, SHANGHAI = ZoneInfo("Europe/Lisbon"), ZoneInfo("Asia/Shanghai")


@pytest.fixture
def move(monkeypatch):
    """改 server.json 的时区再重启：chat 的 TZ 和 settings.tz 一起换。"""
    def to(tz: ZoneInfo) -> None:
        monkeypatch.setattr(chat, "TZ", tz)
        monkeypatch.setattr(config.settings, "tz", tz)
    return to


def put(thread: str, rows: list[tuple]) -> list[int]:
    """直接写进 app 的对话记录：(role, text, ts)。→ 各自的 id。"""
    with chat._lock, chat.db() as conn:
        return [conn.execute("INSERT INTO messages(thread, role, text, ts, status) VALUES(?,?,?,?,?)", (thread, role, text, ts, "ok")).lastrowid
                for role, text, ts in rows]


def lisbon_evening(thread: str) -> None:
    """里斯本 16 日晚上的两段：20:30 那段是上海的 16 日（凌晨 03:30），21:40 那段已经是上海的 17 日（04:40，逻辑日 04:00 换天）。"""
    put(thread, [("user", "Plan my week please", "2026-10-16T20:30:00+01:00"), ("grava", "Mon push, Wed pull.", "2026-10-16T20:31:00+01:00"),
                 ("user", "And Friday?", "2026-10-16T21:40:00+01:00"), ("grava", "Friday legs.", "2026-10-16T21:41:00+01:00")])


def test_a_day_after_moving_from_lisbon_to_shanghai(move):
    thread = "tz-day"
    move(LISBON)
    lisbon_evening(thread)
    move(SHANGHAI)
    put(thread, [("user", "早", "2026-10-17T09:00:00+08:00")])
    day16 = chat.history(thread, day="2026-10-16")["messages"]
    day17 = chat.history(thread, day="2026-10-17")["messages"]
    assert [m["text"] for m in day16] == ["Plan my week please", "Mon push, Wed pull."]
    assert [m["text"] for m in day17] == ["And Friday?", "Friday legs.", "早"]
    assert [m["time"] for m in day17] == ["04:40", "04:41", "09:00"]  # 按现在的时区显示，和归到哪一天对得上
    assert {d["day"]: d["count"] for d in chat.days(thread)["days"]} == {"2026-10-16": 2, "2026-10-17": 3}  # 目录和每天的记录一致
    # 别的 claw 的 history 模式：今天在这一句之前的往来（里斯本 21:40 那段已经是上海的今天）
    with chat._lock, chat.db() as conn:
        last = conn.execute("SELECT MAX(id) FROM messages WHERE thread=?", (thread,)).fetchone()[0]
        sent = claw.history(conn, thread, last, chat.day_bounds("2026-10-17")[0], 20)
    assert [m["content"] for m in sent] == ["And Friday?", "Friday legs."]


def test_where_it_ended_and_todays_seed_after_moving(move):
    """冒烟时的样子：在里斯本说了几句 → 改成东八区、重启 → 再说一句。以前「上次聊到哪」只剩刚发的这句、还说它没被回，
    当天的会话也抄不到里斯本 21:00 以后的那几句。"""
    thread = "tz-tail"
    move(LISBON)
    lisbon_evening(thread)
    move(SHANGHAI)
    (now,) = put(thread, [("user", "Same question again?", "2026-10-17T04:45:00+08:00")])
    p = context.tail_part(thread, "2026-10-17")
    assert p.title == "Where the last conversation ended (2026-10-16)"
    assert "User: Plan my week please" in p.text and "You: Mon push, Wed pull." in p.text
    assert "Same question again?" not in p.text and "never got a reply" not in p.text
    assert [text_of(m) for m in owner.seed(thread, "2026-10-17", now)] == ["And Friday?", "Friday legs."]


def test_turns_sort_by_real_time_across_offsets(move):
    conv = "tz-turns"
    move(SHANGHAI)
    rows = [("t_tz_a", "2026-10-16T21:40:00.000+01:00"),   # 20:40 UTC
            ("t_tz_b", "2026-10-17T04:30:00.000+08:00"),   # 20:30 UTC：按字符串排在 a 后面，其实最早
            ("t_tz_c", "2026-10-17T05:00:00.000+08:00")]   # 21:00 UTC
    with store.tx() as conn:
        conn.executemany("INSERT INTO engine_turns(id, conversation, kind, model, status, started_at) VALUES(?,?,?,?,?,?)",
                         [(tid, conv, "owner", "deepseek/x", "done", at) for tid, at in rows])
    assert [t["id"] for t in events.turns(conv)] == ["t_tz_c", "t_tz_a", "t_tz_b"]
    # 定时任务跑过的记录也一样
    jobs.ready()
    with store.tx() as conn:
        conn.executemany("INSERT INTO engine_job_runs(id, job, agent, started_at, kind, status) VALUES(?,?,?,?,?,?)",
                         [(f"jr-{tid}", "j-tz", "main", at, "scheduled", "ok") for tid, at in rows])
    assert [r["id"] for r in jobs.runs_of("j-tz")] == ["jr-t_tz_c", "jr-t_tz_a", "jr-t_tz_b"]


# —— 照顾索引：按天取、上次聊到哪、一个对话的轮次都走索引，不扫整张表 ——————————————————————

class Spy:
    """包一层连接，记下执行过的 SQL 和参数（之后拿去 EXPLAIN QUERY PLAN）。"""

    def __init__(self, conn, log: list) -> None:
        self.conn, self.log = conn, log

    def execute(self, sql, params=()):
        self.log.append((sql, params))
        return self.conn.execute(sql, params)

    def __enter__(self):
        self.conn.__enter__()
        return self

    def __exit__(self, *exc):
        return self.conn.__exit__(*exc)

    def __getattr__(self, name):
        return getattr(self.conn, name)


def plan(sql: str, params) -> str:
    with chat.db() as conn:
        return " | ".join(r[3] for r in conn.execute("EXPLAIN QUERY PLAN " + sql, params))


def test_day_queries_and_turn_lists_use_indexes(move, monkeypatch):
    move(SHANGHAI)
    log: list = []
    real_db, real_read = chat.db, store.read
    monkeypatch.setattr(chat, "db", lambda: Spy(real_db(), log))

    @contextmanager
    def spy_read():
        with real_read() as conn:
            yield Spy(conn, log)
    monkeypatch.setattr(store, "read", spy_read)
    put("tz-plan", [("user", "hi", "2026-10-16T20:30:00+01:00")])
    chat.history("tz-plan", day="2026-10-17")
    context.tail_part("tz-plan", "2026-10-17")
    events.turns("tz-plan")
    day_sql = [(s, p) for s, p in log if "FROM messages" in s and "julianday(ts)" in s]
    assert len(day_sql) >= 3  # 按天取一次、上次聊到哪两次
    for s, p in day_sql:
        assert "USING INDEX messages_thread_at" in plan(s, p), s
    (turns_sql,) = [(s, p) for s, p in log if "FROM engine_turns" in s]
    got = plan(*turns_sql)
    assert "USING INDEX engine_turns_conv_at" in got and "TEMP B-TREE" not in got  # 索引就是排好的顺序，不用另外排


def test_a_ts_that_is_not_a_time_does_not_block_the_chat_db(tmp_path, monkeypatch, capsys):
    """哪一行的 ts 不像时间（'now' 这种，SQLite 不让它进表达式索引）：时间索引建不起来，库照样能用，只是按天取不走索引。"""
    path = tmp_path / "odd.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, thread TEXT NOT NULL, role TEXT NOT NULL, text TEXT NOT NULL, "
                     "model TEXT, ts TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'ok')")
        conn.execute("INSERT INTO messages(thread, role, text, ts) VALUES('tz-odd', 'user', 'hi', 'now')")
    monkeypatch.setattr(chat, "DB", path)
    with chat._lock, chat.db() as conn:
        assert conn.execute("SELECT text FROM messages WHERE thread='tz-odd'").fetchone()[0] == "hi"
    assert "messages 的时间索引没建上" in capsys.readouterr().out
