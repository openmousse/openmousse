"""「你自己」在库里记成什么（config.owner_id，2026-10-20）：server.json 没写 = leo（老的安装一个字节不变），新装写 owner；
读的时候两种都认（老库只有 leo、新库只有 owner、两种混着）；mousse owner migrate 默认只看，--apply 以后库里只剩一个值。"""
from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
from datetime import timedelta

import pytest

import chat
import config
import diet
import goals
import projects
import schedule as sch
from chat import _lock
from ops import owner as owner_cmd

OLD, NEW = config.OWNER_ID_LEGACY, config.OWNER_ID_NEW


def add(**kw) -> dict:
    return asyncio.run(sch.add_item(sch.ItemIn(**kw)))


def row_id(ref: str) -> str:
    return ref.removeprefix("item:")  # 接口回的是 item:<id>，库里的 id 不带前缀


@pytest.fixture
def clean():
    with _lock, sch.sdb() as conn:
        conn.executescript("DELETE FROM schedule_items; DELETE FROM schedule_marks; DELETE FROM schedule_log;")
    with _lock, goals.gdb() as conn:
        conn.executescript("DELETE FROM goals; DELETE FROM goal_log;")


def test_owner_id_comes_from_server_json(server_json):
    server_json({})
    assert config.owner_id() == OLD and config.owner_ids() == (OLD, NEW)
    server_json({"owner_id": NEW})
    assert config.owner_id() == NEW and config.owner_ids() == (NEW, OLD)
    for bad in ("main", "Main", "../x", "", 3):  # 写错了、写成 main：照老的值
        server_json({"owner_id": bad})
        assert config.owner_id() == OLD
    assert config.is_owner(OLD) and config.is_owner(NEW) and not config.is_owner("main") and not config.is_owner(None)


def test_writes_follow_server_json(server_json, clean):
    server_json({"timezone": "UTC"})
    a = row_id(add(title="Water the plants", kind="todo")["id"])  # 不给 source = 你自己
    server_json({"timezone": "UTC", "owner_id": NEW})
    b = row_id(add(title="Call the plumber", kind="todo")["id"])
    c = row_id(add(title="From an old script", kind="todo", source=OLD)["id"])  # 老脚本照样传 --source leo：记成现在的值
    d = row_id(add(title="Plan a run", kind="todo", source="fitness")["id"])
    with _lock, sch.sdb() as conn:
        got = {r["id"]: r["source"] for r in conn.execute("SELECT id, source FROM schedule_items")}
    assert got == {a: OLD, b: NEW, c: NEW, d: "fitness"}
    assert diet.actor_of(None) == NEW and diet.actor_of(OLD) == NEW and diet.actor_of("main") == "main"


def test_new_tables_default_to_the_configured_owner(server_json, tmp_path, monkeypatch):
    server_json({"owner_id": NEW})
    monkeypatch.setattr(chat, "DB", tmp_path / "fresh.db")
    monkeypatch.setattr(sch, "_ready", False)
    monkeypatch.setattr(projects, "_ready", False)
    conn = projects.pdb()  # 日程的表一起建
    try:
        dflt = {t: {r[1]: r[4] for r in conn.execute(f"PRAGMA table_info({t})")} for t in ("schedule_items", "project_items")}
    finally:
        conn.close()
    assert dflt["schedule_items"]["source"] == f"'{NEW}'" and dflt["project_items"]["source"] == f"'{NEW}'"


@pytest.mark.parametrize("moved_as", [(OLD,), (NEW,), (OLD, NEW)], ids=["old-db", "new-db", "mixed"])
def test_reads_count_both_values_as_you(server_json, clean, moved_as):
    """你挪过的训练，Agent 重排时不改时间：你那次挪动记成 leo、owner 还是两种都有，都认得出来。"""
    server_json({"timezone": "UTC", "owner_id": NEW})
    day = (sch.today() + timedelta(days=2)).isoformat()
    first = add(title="Run", kind="event", date=day, start="07:00", end="08:00", key="fitness:training:x", source="fitness")["id"]
    with _lock, sch.sdb() as conn:
        for v in moved_as:  # 你在 app 里挪过（老的服务器记成 leo）
            conn.execute("INSERT INTO schedule_log(at, actor, target, action, title, summary) VALUES(?,?,?,?,?,?)",
                         (chat.now_iso(), v, first, "move", "Run", "07:00 → 18:00"))
        conn.execute("UPDATE schedule_items SET start='18:00', end='19:00' WHERE id=?", (row_id(first),))
        names = sch.group_names(conn)
    again = add(title="Run (easy)", kind="event", date=day, start="09:00", end="10:00", key="fitness:training:x", source="fitness")
    assert again["id"] == first and "time" in again.get("kept", [])
    with _lock, sch.sdb() as conn:
        r = conn.execute("SELECT start, title FROM schedule_items WHERE id=?", (row_id(first),)).fetchone()
    assert (r["start"], r["title"]) == ("18:00", "Run (easy)")
    assert all(sch.who_name(v, names) == "" for v in moved_as) and sch.who_name("main", names)


def test_goal_changes_of_yours_stay_out_of_recent(server_json, clean):
    server_json({"timezone": "UTC"})
    goals.add_goal(goals.GoalIn(title="Read 12 books", category="学业"))  # 老的值
    server_json({"timezone": "UTC", "owner_id": NEW})
    goals.add_goal(goals.GoalIn(title="Swim 1 km", category="健康"))      # 新的值
    goals.add_goal(goals.GoalIn(title="Walk daily", category="健康", source="fitness"))
    recent = goals.goals()["recent"]
    assert [c["actor"] for c in recent] == ["fitness"]


def test_diet_target_set_by_you_reads_as_manual():
    for src in (OLD, NEW):
        row = {"kcal": None, "protein": 100, "carb": 200, "fat": 60, "water_ml": None, "kcal_derived": 1, "source": src, "effective_from": "2026-10-01"}
        assert diet.target_out(row)["source"] == "Set manually"


# —— mousse owner migrate ——————————————————————————————————————————————————————————————

SCHEMA = f"""
CREATE TABLE schedule_items (id TEXT PRIMARY KEY, source TEXT NOT NULL DEFAULT '{OLD}');
CREATE TABLE schedule_log (id INTEGER PRIMARY KEY, actor TEXT NOT NULL);
CREATE TABLE goals (id TEXT PRIMARY KEY, added_by TEXT);
CREATE TABLE goal_log (id INTEGER PRIMARY KEY, actor TEXT NOT NULL);
CREATE TABLE diet_targets (id INTEGER PRIMARY KEY, source TEXT NOT NULL, created_by TEXT);
"""


def ns(**kw) -> argparse.Namespace:
    return argparse.Namespace(**{"to": None, "apply": False, "db": None, "json": True, **kw})


def values(db) -> dict[str, list]:
    with sqlite3.connect(db) as conn:
        return {"items": [r[0] for r in conn.execute("SELECT source FROM schedule_items ORDER BY id")],
                "log": [r[0] for r in conn.execute("SELECT actor FROM schedule_log ORDER BY id")],
                "goals": [r[0] for r in conn.execute("SELECT added_by FROM goals ORDER BY id")],
                "targets": [tuple(r) for r in conn.execute("SELECT source, created_by FROM diet_targets ORDER BY id")]}


@pytest.fixture
def old_db(tmp_path, server_json):
    db = tmp_path / "mousse.db"
    with sqlite3.connect(db) as conn:
        conn.executescript(SCHEMA)
        conn.executemany("INSERT INTO schedule_items(id, source) VALUES(?, ?)", [("a", OLD), ("b", "main"), ("c", "fitness"), ("d", NEW)])
        conn.executemany("INSERT INTO schedule_log(actor) VALUES(?)", [(OLD,), ("fitness",), (OLD,)])
        conn.executemany("INSERT INTO goals(id, added_by) VALUES(?, ?)", [("g1", OLD), ("g2", None)])
        conn.executemany("INSERT INTO diet_targets(source, created_by) VALUES(?, ?)", [(OLD, OLD), ("agent", "diet")])
    path = server_json({"timezone": "UTC", "backup_dir": str(tmp_path / "backups")})
    return db, path


def test_migrate_only_reports_by_default(old_db, capsys):
    db, path = old_db
    before, conf = values(db), path.read_text(encoding="utf8")
    assert owner_cmd.main(ns(db=str(db))) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["applied"] is False and out["from"] == [OLD] and out["to"] == NEW
    assert out["total"] == 6 and {(c["table"], c["column"]): c["rows"] for c in out["changes"]} == {
        ("schedule_items", "source"): 1, ("schedule_log", "actor"): 2, ("goals", "added_by"): 1, ("diet_targets", "source"): 1,
        ("diet_targets", "created_by"): 1}
    assert values(db) == before and path.read_text(encoding="utf8") == conf  # 一个字都没动
    assert not (db.parent / "backups").exists()


def test_migrate_apply_backs_up_then_leaves_one_value(old_db, capsys):
    db, path = old_db
    assert owner_cmd.main(ns(db=str(db), apply=True)) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["applied"] is True and out["ownerId"] == NEW
    got = values(db)
    assert got == {"items": [NEW, "main", "fitness", NEW], "log": [NEW, "fitness", NEW], "goals": [NEW, None],
                   "targets": [(NEW, NEW), ("agent", "diet")]}  # main、Agent 的、空的都不动
    assert json.loads(path.read_text(encoding="utf8"))["owner_id"] == NEW and config.owner_id() == NEW
    backups = list((db.parent / "backups").glob("mousse-before-owner-*.db"))
    assert len(backups) == 1 and values(backups[0])["items"][0] == OLD  # 改之前的样子
    assert list(path.parent.glob("server.json.bak-*"))
    assert owner_cmd.main(ns(db=str(db), apply=True)) == 0  # 再跑一次：没什么要改
    assert json.loads(capsys.readouterr().out)["total"] == 0


def test_migrate_can_go_back_and_rejects_bad_ids(old_db, capsys):
    db, path = old_db
    assert owner_cmd.main(ns(db=str(db), apply=True)) == 0
    assert owner_cmd.main(ns(db=str(db), apply=True, to=OLD)) == 0  # 往回换
    assert values(db)["items"] == [OLD, "main", "fitness", OLD] and config.owner_id() == OLD
    capsys.readouterr()
    assert owner_cmd.main(ns(db=str(db), to="Main Chat")) == 2 and owner_cmd.main(ns(db=str(db), to="main")) == 2
