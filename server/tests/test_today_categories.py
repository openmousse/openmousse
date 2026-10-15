"""「今天」的类别和待办（today.categories）：没日子的待办、过期的留着（7 天以后收起来）、不用管、改类别、邮件待办的快照、老表的迁移。"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import timedelta

import pytest
from fastapi import HTTPException

import schedule as sch
import today as tp
from chat import _lock


@pytest.fixture
def env(tmp_path, server_json):
    """开着 today.categories 的配置 + 一份空的邮件条目；每个测试前清空日程的表。返回 (写配置, 写邮件条目)。"""
    mail = tmp_path / "items.json"

    def write_mail(items: list[dict]) -> None:
        mail.write_text(json.dumps({"items": {x["id"]: x for x in items}}, ensure_ascii=False), encoding="utf8")

    def config(cats: bool = True) -> None:
        server_json({"timezone": "UTC", "today": {"categories": cats}, "remember": {"mail": {"items": str(mail)}}})

    write_mail([])
    config()
    with _lock, sch.sdb() as conn:
        conn.executescript("DELETE FROM schedule_items; DELETE FROM schedule_marks; DELETE FROM schedule_log; DELETE FROM todo_keep;")
    return config, write_mail


def add(**kw) -> str:
    return asyncio.run(sch.add_item(sch.ItemIn(**kw)))["id"]


def day(d=None) -> dict:
    return tp.build_day(d or sch.today(), [], {})


def ago(n: int) -> str:
    return (sch.today() - timedelta(days=n)).isoformat()


def test_undated_todo_and_category(env):
    tid = add(title="量一次体脂", kind="todo", category="life", source="leo")
    with pytest.raises(HTTPException):
        add(title="没日子的日程", kind="event")
    with pytest.raises(HTTPException):
        add(title="没日子却有时刻", kind="todo", start="10:00")
    d = day()
    assert d["categories"]["on"] is True
    row = next(r for r in d["pending"]["nodate"] if r["id"] == tid)
    assert row["category"] == "life" and row["canDismiss"] is True and row["kind"] == "todo"
    # 待办改成有日子、再改回没日子
    asyncio.run(sch.patch_item(tid, sch.ItemPatch(date=sch.today().isoformat())))
    assert any(r["id"] == tid for r in day()["pending"]["items"])
    asyncio.run(sch.patch_item(tid, sch.ItemPatch(date=None)))
    assert any(r["id"] == tid for r in day()["pending"]["nodate"])


def test_overdue_stays_and_folds_after_a_week(env):
    a = add(title="Accenture application", kind="deadline", date=ago(3), source="leo")
    b = add(title="寄回 EE 路由器", kind="deadline", date=ago(10), source="leo")
    d = day()
    row = next(r for r in d["pending"]["items"] if r["id"] == a)
    assert row["overdueDays"] == 3 and row["tone"] == "bad" and row["category"] == "work"
    assert [r["id"] for r in d["pending"]["stale"]] == [b]
    assert d["pending"]["stale"][0]["category"] == "life"
    # 关掉 today.categories：老样子，过期 3 天以上的不列、没有 stale
    config, _ = env
    config(False)
    d = day()
    assert not d["pending"]["stale"] and a in {r["id"] for r in d["pending"]["items"]} and b not in {r["id"] for r in d["pending"]["items"]}


def test_dismiss_is_recorded_apart_from_done(env):
    a = add(title="写 OpenMousse 文章", kind="deadline", date=ago(1), source="main")
    r = asyncio.run(sch.remember_done(sch.DoneIn(ref=a, how="dismissed")))
    assert r["changed"] and r["card"]["action"] == "dismiss" and r["card"]["area"] == "remember"
    with _lock, sch.sdb() as conn:
        m = dict(conn.execute("SELECT * FROM schedule_marks WHERE ref=?", (a,)).fetchone())
    assert m["done_at"] and m["resolved"] == "dismissed"
    assert a not in {x["id"] for x in day()["pending"]["items"]}
    # 撤销 = 放回来
    asyncio.run(sch.undo(r["card"]["logId"]))
    assert a in {x["id"] for x in day()["pending"]["items"]}
    # 做完了：resolved = done
    asyncio.run(sch.remember_done(sch.DoneIn(ref=a)))
    with _lock, sch.sdb() as conn:
        assert conn.execute("SELECT resolved FROM schedule_marks WHERE ref=?", (a,)).fetchone()[0] == "done"


def test_category_override_wins_and_undoes(env):
    a = add(title="Book a dentist appointment", kind="todo", source="leo")
    assert next(r for r in day()["pending"]["nodate"] if r["id"] == a)["category"] == "life"
    r = asyncio.run(sch.remember_category(sch.CategoryIn(ref=a, category="work")))
    row = next(x for x in day()["pending"]["nodate"] if x["id"] == a)
    assert row["category"] == "work" and row["categorySet"] and row["categoryAuto"] == "life"
    asyncio.run(sch.undo(r["card"]["logId"]))
    assert next(x for x in day()["pending"]["nodate"] if x["id"] == a)["category"] == "life"
    with pytest.raises(HTTPException):
        asyncio.run(sch.remember_category(sch.CategoryIn(ref=a, category="engineering")))


def test_mail_todo_kept_after_source_drops_it(env):
    _, write_mail = env
    x = {"id": "t1:todo", "type": "todo", "title": "寄回 EE 路由器，不然扣 £85", "detail": "", "due": ago(2), "source": "zxy",
         "thread_id": "t1", "urgent": False, "first_seen": "2026-10-01T10:00:00+01:00"}
    write_mail([x])
    row = next(r for r in day()["pending"]["items"] if r["id"] == "mail:t1:todo")
    assert row["category"] == "life" and not row["kept"]
    write_mail([])  # 来源过了期删掉了
    row = next(r for r in day()["pending"]["items"] if r["id"] == "mail:t1:todo")
    assert row["kept"] and row["overdueDays"] == 2
    asyncio.run(sch.remember_done(sch.DoneIn(ref="mail:t1:todo", title=x["title"])))
    assert "mail:t1:todo" not in {r["id"] for r in day()["pending"]["items"]}


def test_remember_groups_stale(env):
    add(title="old thing", kind="deadline", date=ago(9), source="leo")
    add(title="undated", kind="todo", source="leo")
    got = {e["title"]: e for e in sch.build_remember()["items"]}
    assert got["old thing"]["group"] == "stale" and got["undated"]["group"] == "nodate"
    assert all(e["category"] in ("study", "life", "work") for e in got.values())


def test_migrate_old_table(tmp_path):
    conn = sqlite3.connect(tmp_path / "old.db")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
    CREATE TABLE schedule_items (id TEXT PRIMARY KEY, kind TEXT NOT NULL DEFAULT 'event', title TEXT NOT NULL,
        date TEXT NOT NULL, start TEXT, end TEXT, location TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '',
        source TEXT NOT NULL DEFAULT 'leo', key TEXT, attended INTEGER, actual_start TEXT, actual_end TEXT,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT);
    CREATE TABLE schedule_marks (ref TEXT PRIMARY KEY, skip INTEGER, location TEXT, note TEXT, done_at TEXT,
        attended INTEGER, actual_start TEXT, actual_end TEXT, title TEXT, date TEXT, time TEXT, source TEXT, updated_at TEXT NOT NULL);
    INSERT INTO schedule_items(id, title, date, created_at, updated_at) VALUES('ev-1', '旧的一条', '2026-10-01', 'x', 'x');
    INSERT INTO schedule_marks(ref, done_at, updated_at) VALUES('item:ev-1', 'y', 'y');
    """)
    sch.migrate(conn)
    cols = {r[1]: r for r in conn.execute("PRAGMA table_info(schedule_items)")}
    assert cols["date"][3] == 0 and "category" in cols
    assert {r[1] for r in conn.execute("PRAGMA table_info(schedule_marks)")} >= {"category", "resolved"}
    assert dict(conn.execute("SELECT id, title, date FROM schedule_items").fetchone()) == {"id": "ev-1", "title": "旧的一条", "date": "2026-10-01"}
    conn.execute("INSERT INTO schedule_items(id, title, date, created_at, updated_at) VALUES('ev-2', '没日子', NULL, 'x', 'x')")
    sch.migrate(conn)  # 再跑一次什么都不做
    assert conn.execute("SELECT COUNT(*) FROM schedule_items").fetchone()[0] == 2
