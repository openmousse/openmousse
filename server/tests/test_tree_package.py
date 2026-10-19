"""开源版世界树（tree/ 的 openmousse_tree 0.3）：枝、规则、写入时自动挂枝、查重、老数据升级、Markdown 存储里用户加的属性原样留着。
全在临时目录里（conftest 的 mousse_tree），数据都是编的。"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time

import pytest
from openmousse_tree import branches as B
from openmousse_tree import cli
from openmousse_tree import config as C
from openmousse_tree import notes as N
from openmousse_tree import rules as R
from openmousse_tree import store as S

BOTH = pytest.mark.parametrize("storage", ["sqlite", "markdown"])

# 0.2 的表（tree 没有 branch 列，没有 branch 表）：照原样建，看升级以后照常能用
OLD_SCHEMA = """
CREATE TABLE IF NOT EXISTS tree (id TEXT PRIMARY KEY, text TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'fact', tags TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL, observed_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active', supersedes TEXT, created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS tree_status ON tree(status, observed_at);
CREATE VIRTUAL TABLE IF NOT EXISTS tree_fts USING fts5(text, tags, content='tree', content_rowid='rowid', tokenize='trigram');
CREATE TRIGGER IF NOT EXISTS tree_ai AFTER INSERT ON tree BEGIN INSERT INTO tree_fts(rowid, text, tags) VALUES (new.rowid, new.text, new.tags); END;
CREATE TRIGGER IF NOT EXISTS tree_ad AFTER DELETE ON tree BEGIN
  INSERT INTO tree_fts(tree_fts, rowid, text, tags) VALUES ('delete', old.rowid, old.text, old.tags); END;
CREATE TRIGGER IF NOT EXISTS tree_au AFTER UPDATE ON tree BEGIN
  INSERT INTO tree_fts(tree_fts, rowid, text, tags) VALUES ('delete', old.rowid, old.text, old.tags);
  INSERT INTO tree_fts(rowid, text, tags) VALUES (new.rowid, new.text, new.tags); END;
CREATE TABLE IF NOT EXISTS activity (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, actor TEXT NOT NULL, text TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""


def fresh() -> sqlite3.Connection:
    """新进程那样再连一次（每个进程第一次连上时做的事重新做）。"""
    S._ready.clear()
    return S.connect()


def leaf(conn, mid: str) -> dict:
    return dict(conn.execute("SELECT * FROM tree WHERE id=?", (mid,)).fetchone())


def note_of(conn, mid: str) -> str:
    return (N.root() / leaf(conn, mid)["path"]).read_text(encoding="utf8")


def run_cli(monkeypatch, capsys, *argv: str, agent: str | None = None) -> str:
    if agent is None:
        monkeypatch.delenv("MOUSSE_AGENT", raising=False)
    else:
        monkeypatch.setenv("MOUSSE_AGENT", agent)
    monkeypatch.setattr(sys, "argv", ["mousse-tree", *argv])
    cli.main()
    return capsys.readouterr().out


def backdate(path, seconds: float = 30) -> None:
    """装成是一会儿以前改的：笔记文件夹里有 1 秒内刚写的文件，索引会等它写完再收（同步可能还没写完）。给文件夹就整个往前拨。"""
    t = time.time() - seconds
    for p in ([path] if path.is_file() else [x for x in path.rglob("*") if x.is_file()]):
        os.utime(p, (t, t))


# —— 一棵新树：主干、「人」、规则 ————————————————————————————————————————————————

@BOTH
def test_fresh_tree_has_trunk_people_and_rules(mousse_tree, storage):
    mousse_tree.make(storage)
    conn = S.connect()
    assert json.loads(C.CONFIG.read_text())["trunk"] == "Profile"  # 主干的名字存进配置，以后换语言也不变
    tree = B.tree(conn)
    assert [b["name"] for b in tree] == ["People"]
    people = tree[0]
    assert people["parent"] == "Profile" and people["depth"] == 1 and people["leaves"] == 0 and people["agents"] == ""
    assert people["aliases"] == ["family", "partner", "friends", "classmates", "colleagues", "commitments"] and "Alex" in people["about"]
    rules = R.path().read_text(encoding="utf8")
    assert rules.startswith("# Memory tree rules") and "Alex can edit it at any time" in rules and "{name}" not in rules
    assert conn.execute("SELECT COUNT(*) FROM tree").fetchone()[0] == 0  # 枝的笔记、规则都不算记忆
    if storage == "markdown":
        assert R.path() == mousse_tree.vault / "rules.md"
        note = (mousse_tree.vault / "branches" / "People.md").read_text(encoding="utf8")
        assert 'parent: "[[Profile]]"' in note and '  - "family"' in note and "agents: []" in note
    else:
        assert R.path() == mousse_tree.home / "rules.md"
    assert B.resolve(conn, "Family") == "People" and B.resolve(conn, "[[People]]") == "People" and B.resolve(conn, "Profile") == ""
    assert B.resolve(conn, "nowhere") is None


def test_chinese_tree_uses_chinese_names_and_the_default_name(mousse_tree):
    mousse_tree.make("markdown", lang="zh", name="")
    conn = S.connect()
    assert S.trunk() == "档案" and [b["name"] for b in B.tree(conn)] == ["人"]
    assert B.resolve(conn, "家人") == "人" and B.resolve(conn, "约定") == "人" and B.resolve(conn, "主干") == ""
    rules = R.text()
    assert rules.startswith("# 世界树规则") and "用户 可以随时改这一页" in rules and "挂「人」这根枝" in rules
    assert (mousse_tree.vault / "branches" / "人.md").is_file()


def test_rules_written_once_then_left_alone(mousse_tree):
    mousse_tree.make()
    conn = S.connect()
    R.path().write_text("# My rules\n\n- Only remember food.\n", encoding="utf8")
    fresh()
    assert R.text() == "# My rules\n\n- Only remember food."  # 改过的不覆盖
    assert "Only remember food." in S.profile_bundle(conn)
    R.path().write_text("", encoding="utf8")
    fresh()
    assert R.path().read_text() == "" and "Memory tree rules" not in S.profile_bundle(conn)  # 清空 = 不要规则
    R.path().unlink()
    fresh()
    assert R.text().startswith("# Memory tree rules")  # 删掉了：下次换回默认的


def test_profile_bundle_has_profile_rules_and_branches(mousse_tree, monkeypatch, capsys):
    mousse_tree.make()
    (mousse_tree.home / "USER.md").write_text("## Basics\n- Alex lives in Wrenford\n", encoding="utf8")
    conn = S.connect()
    B.create(conn, "Fitness", about="Training and recovery", aliases=["gym"], agents=["g-fit"])
    out = run_cli(monkeypatch, capsys, "profile")
    prof, rules, branches = out.split("\n\n---\n\n")
    assert "Alex lives in Wrenford" in prof and rules.startswith("# Memory tree rules")
    assert "- Profile (the trunk: the profile)" in branches and "  - People (aliases: family" in branches
    assert "  - Fitness (aliases: gym): Training and recovery" in branches
    listing = run_cli(monkeypatch, capsys, "branches")
    assert listing.splitlines()[0] == "- Profile: the trunk" and "  - Fitness: Training and recovery" in listing


# —— 老用户升级 ————————————————————————————————————————————————————————————————

def test_upgrade_from_0_2_sqlite_keeps_every_leaf(mousse_tree):
    mousse_tree.make()
    cfg = json.loads(C.CONFIG.read_text())
    cfg.pop("trunk", None)
    C.CONFIG.write_text(json.dumps(cfg))
    with sqlite3.connect(C.DB) as old:
        old.executescript(OLD_SCHEMA)
        old.execute("INSERT INTO tree VALUES ('a1b2c3d4e5f6', 'Alex drinks green tea every morning', 'preference', 'drinks', 'claude', "
                    "'2026-09-01', 'active', NULL, '2026-09-01T08:00:00+00:00', '2026-09-01T08:00:00+00:00')")
        old.execute("INSERT INTO tree VALUES ('b1b2c3d4e5f6', 'Alex has a cat called Pixel', 'fact', '', 'chatgpt', "
                    "'2026-09-02', 'pending', NULL, '2026-09-02T08:00:00+00:00', '2026-09-02T08:00:00+00:00')")
    conn = S.connect()
    assert "branch" in {r[1] for r in conn.execute("PRAGMA table_info(tree)")}
    old_leaf = leaf(conn, "a1b2c3d4e5f6")
    assert old_leaf["branch"] is None and old_leaf["text"] == "Alex drinks green tea every morning" and old_leaf["status"] == "active"
    assert [b["name"] for b in B.tree(conn)] == ["People"]  # 第一次跑补上「人」
    assert [r["id"] for r in S.recall(conn, "green tea")] == ["a1b2c3d4e5f6"]  # 全文索引照常
    assert {r["id"] for r in S.recall(conn, "Pixel")} == {"b1b2c3d4e5f6"}  # 待确认的也查得到
    assert json.loads(C.CONFIG.read_text())["trunk"] == "Profile"
    r = S.add(conn, text="Alex's partner Sam loves hiking", source="claude", tags="partner")
    assert r["branch"] == "People"  # 升级以后新写的照常挂枝
    fresh()
    assert len(B.tree(conn)) == 1  # 再跑一次不会再长一根「人」


def test_upgrade_from_0_2_markdown_rebuilds_the_index_and_leaves_notes_alone(mousse_tree):
    mousse_tree.make("markdown")
    vault = mousse_tree.vault
    old_note = ("---\nid: c1d2e3f4a5b6\nkind: fact\nsource: claude\nobserved_at: 2026-09-03\nstatus: active\n"
                "created_at: 2026-09-03T09:00:00+00:00\nupdated_at: 2026-09-03T09:00:00+00:00\n---\nAlex plays the cello\n")
    (vault / "2026-09-03 Alex plays the cello.md").write_text(old_note, encoding="utf8")
    backdate(vault)
    with sqlite3.connect(C.INDEX) as old:  # 0.2 的索引：tree 没有 branch 列
        old.executescript("CREATE TABLE tree (id TEXT PRIMARY KEY, path TEXT NOT NULL, text TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'fact', "
                          "tags TEXT NOT NULL DEFAULT '', source TEXT NOT NULL, observed_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active', "
                          "supersedes TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);"
                          "CREATE TABLE issue (path TEXT NOT NULL, problem TEXT NOT NULL);" + OLD_SCHEMA)
        old.execute("INSERT INTO meta(k, v) VALUES ('sig', 'old-signature')")
    conn = S.connect()
    row = leaf(conn, "c1d2e3f4a5b6")
    assert row["branch"] is None and row["text"] == "Alex plays the cello"
    assert (vault / "2026-09-03 Alex plays the cello.md").read_text(encoding="utf8") == old_note  # 老笔记一个字不动
    assert (vault / "branches" / "People.md").is_file() and [b["name"] for b in B.tree(conn)] == ["People"]
    assert S.recall(conn, "cello")[0]["id"] == "c1d2e3f4a5b6"


# —— 自动挂枝 ————————————————————————————————————————————————————————————————

@BOTH
def test_auto_branch_order(mousse_tree, storage, monkeypatch, capsys):
    mousse_tree.make(storage)
    conn = S.connect()
    B.create(conn, "Fitness", about="Training", aliases=["gym"], agents=["g-fit"])
    B.create(conn, "Food", about="Meals", agents=["g-food"])
    # 写的人指定的 > 写的 Agent 自己那根
    r = S.add(conn, text="Alex's brother Ravi runs marathons", source="g-fit", agent="g-fit", branch="family")
    assert r["branch"] == "People"
    # 写的 Agent 自己那根 > 标签对上的
    r = S.add(conn, text="Alex squats on Mondays", source="g-fit", agent="g-fit", tags="family")
    assert r["branch"] == "Fitness"
    # 标签对上枝名或别名的
    assert S.add(conn, text="Alex's gym opens at six", source="claude", tags="gym")["branch"] == "Fitness"
    # 都对不上：主干；指定了一根不存在的：说一声，再按后面的规矩挂
    assert S.add(conn, text="Alex is learning Portuguese", source="claude")["branch"] == "Profile"
    r = S.add(conn, text="Alex wants to visit Whalesong Isle in spring", source="claude", branch="Travel")
    assert r["branch"] == "Profile" and "Travel" in r["note"]
    # 取代旧叶子：继承旧叶子的枝（就算写的 Agent 自己有别的枝）
    old = S.add(conn, text="Alex eats porridge before training", source="claude", branch="Food")
    r = S.add(conn, text="Alex eats eggs before training now", source="g-fit", agent="g-fit", supersedes=old["id"])
    assert r["branch"] == "Food" and leaf(conn, old["id"])["status"] == "superseded"
    # 命令行认 MOUSSE_AGENT：source 记成那个 Agent（--source 写别的也不算），自动挂到它的枝
    out = json.loads(run_cli(monkeypatch, capsys, "add", "--source", "claude", "--text", "Alex rows twice a week", agent="g-fit"))
    row = leaf(S.connect(), out["id"])
    assert row["source"] == "g-fit" and row["branch"] == "Fitness" and out["branch"] == "Fitness"
    out = json.loads(run_cli(monkeypatch, capsys, "add", "--source", "openclaw-g-food", "--text", "Alex skips lunch on Fridays"))
    assert leaf(S.connect(), out["id"])["branch"] == "Food"  # OpenClaw 那边的 skill 写的 openclaw-<id> 也认
    if storage == "markdown":
        assert 'branch: "[[Fitness]]"' in note_of(conn, out["id"]) or 'branch: "[[Food]]"' in note_of(conn, out["id"])
        assert 'branch: "[[Profile]]"' in note_of(S.connect(), S.recall(S.connect(), "Portuguese")[0]["id"])  # 新笔记写明直接挂主干


@BOTH
def test_set_branch_by_name_alias_or_trunk(mousse_tree, storage):
    mousse_tree.make(storage)
    conn = S.connect()
    mid = S.add(conn, text="Alex's friend Noor bakes sourdough", source="claude")["id"]
    assert S.set_branch(conn, mid, "friends", "owner") == "People" and leaf(conn, mid)["branch"] == "People"
    assert S.set_branch(conn, mid, "Profile", "owner") == "Profile" and leaf(conn, mid)["branch"] is None
    with pytest.raises(ValueError):
        S.set_branch(conn, mid, "Nowhere", "owner")
    with pytest.raises(LookupError):
        S.set_branch(conn, "missing0000", "People", "owner")


def test_agent_branches_and_people_alias_collision(mousse_tree):
    mousse_tree.make()
    conn = S.connect()
    assert B.ensure_agent(conn, "g-fit", "Fitness", "Training plans") == "Fitness"
    assert B.ensure_agent(conn, "g-fit", "Renamed", "") == "Fitness"  # 已经有它的枝了
    assert B.ensure_agent(conn, "g-kin", "Family", "") == "People"  # 名字是「人」的别名：加进那根的 agents
    assert B.ensure_agent(conn, "g-odd", "Plans: A/B", "") == "Plans A B"  # 当不了文件名的字换成空格
    assert B.ensure_agent(conn, "main", "Main", "") is None
    tree = {b["name"]: b for b in B.tree(conn)}
    assert tree["Fitness"]["agents"] == "g-fit" and tree["People"]["agents"] == "g-kin" and tree["Fitness"]["about"] == "Training plans"
    assert B.of_agent(conn, "g-kin") == ["People"]


def test_branch_tree_is_preorder_with_depth_and_counts(mousse_tree):
    mousse_tree.make()
    conn = S.connect()
    B.create(conn, "Body", about="Health")
    B.create(conn, "Fitness", parent="Body", about="Training", agents=["g-fit"])
    B.create(conn, "Strength", parent="Fitness", about="Lifting")
    S.add(conn, text="Alex deadlifts 120kg", source="claude", branch="Strength")
    S.add(conn, text="Alex sleeps eight hours", source="claude", branch="Body")
    tree = B.tree(conn)
    assert [(b["name"], b["parent"], b["depth"], b["leaves"]) for b in tree] == [
        ("People", "Profile", 1, 0), ("Body", "Profile", 1, 1), ("Fitness", "Body", 2, 0), ("Strength", "Fitness", 3, 1)]
    with pytest.raises(ValueError):
        B.create(conn, "family")  # 和别名撞了
    with pytest.raises(ValueError):
        B.create(conn, "Hobbies", parent="Nowhere")


# —— 查重 ————————————————————————————————————————————————————————————————

@BOTH
def test_near_duplicates_are_refused_and_similar_ones_listed(mousse_tree, storage):
    mousse_tree.make(storage)
    (mousse_tree.home / "USER.md").write_text("## Food\n- Alex is vegetarian\n", encoding="utf8")
    conn = S.connect()
    S.sync_profile(conn)
    first = S.add(conn, text="Alex switched breakfast to oats and eggs on 2026-10-01", source="claude")
    assert not first["duplicate"]
    same = S.add(conn, text="**Alex switched breakfast to oats and eggs**", source="chatgpt")  # 去掉标记和日期以后一样
    assert same["duplicate"] and same["id"] == first["id"] and same["ratio"] >= S.DUP
    near = S.add(conn, text="Alex switched his breakfast to oats with eggs", source="gemini")
    assert not near["duplicate"] and [x["id"] for x in near["similar"]] == [first["id"]] and S.NEAR <= near["similar"][0]["ratio"] < S.DUP
    other = S.add(conn, text="Alex's partner Sam plays chess", source="claude")
    assert not other["duplicate"] and other["similar"] == []
    prof = S.add(conn, text="Alex is vegetarian.", source="claude")  # 档案里已经有了
    assert prof["duplicate"] and prof["profile"]
    # 取代旧的那条：和被取代的那条像不算重复（和别的几乎一样的照样挡）
    assert S.add(conn, text="Alex's partner Sam plays chess well", source="claude")["duplicate"]
    newer = S.add(conn, text="Alex's partner Sam plays chess well", source="claude", supersedes=other["id"])
    assert not newer["duplicate"] and leaf(conn, other["id"])["status"] == "superseded"
    blocked = S.add(conn, text="Alex switched breakfast to oats and two eggs", source="claude", supersedes=near["id"])
    assert blocked["duplicate"] and blocked["id"] == first["id"] and leaf(conn, near["id"])["status"] == "active"
    count = conn.execute("SELECT COUNT(*) FROM tree WHERE source != 'profile' AND status IN ('active','pending')").fetchone()[0]
    assert count == 3


def test_mcp_remember_replies_say_why(mousse_tree):
    from openmousse_tree import server
    mousse_tree.make()
    conn = S.connect()
    a = S.add(conn, text="Alex walks the dog at seven", source="claude")
    same = S.add(conn, text="Alex walks the dog at seven!", source="gemini")
    assert "already exists" in server.reply(same, "active", "Alex") and a["id"] in server.reply(same, "active", "Alex")
    almost = server.reply({"id": a["id"], "duplicate": True, "ratio": 0.93, "profile": False, "text": "Alex walks the dog at seven"}, "active", "Alex")
    assert "almost identical" in almost and f"supersedes={a['id']}" in almost
    assert "profile already has" in server.reply({"id": "p1", "duplicate": True, "ratio": 1.0, "profile": True}, "active", "Alex")
    near = S.add(conn, text="Alex walks his dog at seven each evening", source="claude", status="pending")
    msg = server.reply(near, "pending", "Alex")
    assert msg.startswith(f'Remembered [{near["id"]}] on "Profile" (waiting for Alex to confirm)') and a["id"] in msg and "superseded" in msg


# —— recall / recent 和内核看到的一样 ——————————————————————————————————————————————

def test_recall_and_recent_include_pending_and_mark_it(mousse_tree):
    mousse_tree.make()
    conn = S.connect()
    S.add(conn, text="Alex is saving for a bike", source="claude", status="pending", branch="People")
    S.add(conn, text="Alex's bike is blue", source="claude")
    out = S.fmt(S.recall(conn, "bike"))
    assert "saving for a bike" in out and "[pending]" in out and "People" in out
    assert len(S.recent(conn, 3)) == 2


# —— Markdown 存储：用户加的属性原样留着 ——————————————————————————————————————————————

def test_rewriting_a_note_keeps_properties_the_user_added(mousse_tree):
    mousse_tree.make("markdown")
    conn = S.connect()
    mid = S.add(conn, text="Alex volunteers at the library on Saturdays", source="claude")["id"]
    path = N.root() / leaf(conn, mid)["path"]
    raw = path.read_text(encoding="utf8")
    raw = raw.replace("kind: fact\n", "kind: fact\nmood: proud   # my own note\nrelated:\n- \"[[Library]]\"\n", 1)
    raw = raw.replace("updated_at:", "cssclasses: [wide]\nupdated_at:", 1)
    path.write_text(raw, encoding="utf8")
    backdate(mousse_tree.vault)
    conn = S.connect()
    mine = ["mood: proud   # my own note", "related:", '- "[[Library]]"', "cssclasses: [wide]"]
    for step in (lambda: S.set_status(conn, mid, "pending", "owner"), lambda: S.set_branch(conn, mid, "People", "owner"),
                 lambda: S.edit(conn, mid, "Alex volunteers at the library on Sundays", "owner")):
        step()
        text = note_of(conn, mid)
        assert all(x in text for x in mine), text
        assert text.index("mood:") < text.index("source:") < text.index("cssclasses:") < text.index("updated_at:")  # 原地留着
    assert 'branch: "[[People]]"' in note_of(conn, mid) and "Sundays" in note_of(conn, mid)
    newer = S.add(conn, text="Alex volunteers at the library twice a month", source="claude", supersedes=mid)
    archived = note_of(conn, mid)
    assert "status: superseded" in archived and all(x in archived for x in mine) and newer["branch"] == "People"
    S.set_status(conn, mid, "retracted", "owner")
    shell = (N.root() / N.archive() / f"{mid}.md").read_text(encoding="utf8")
    assert "mood" not in shell and "volunteers" not in shell  # 遗忘：只留骨架


def test_branch_note_edited_in_obsidian_is_picked_up(mousse_tree):
    mousse_tree.make("markdown")
    conn = S.connect()
    note = mousse_tree.vault / "branches" / "People.md"
    note.write_text(note.read_text(encoding="utf8").replace('  - "family"', '  - "family"\n  - "relatives"'), encoding="utf8")
    backdate(mousse_tree.vault)
    conn = S.connect()
    assert B.resolve(conn, "relatives") == "People"
    B.add_agent(conn, "People", "g-kin")
    text = note.read_text(encoding="utf8")
    assert '"relatives"' in text and "agents:\n  - g-kin" in text and "order: 1" in text  # 只换 agents，别的原样
    (mousse_tree.vault / "branches" / "Hobbies.md").write_text('---\nparent: "[[People]]"\norder: 5\n---\nWhat Alex does for fun\n', encoding="utf8")
    backdate(mousse_tree.vault)
    tree = {b["name"]: b for b in B.tree(S.connect())}
    assert tree["Hobbies"]["parent"] == "People" and tree["Hobbies"]["depth"] == 2 and tree["Hobbies"]["about"] == "What Alex does for fun"


# —— 换存储：枝跟着走 ——————————————————————————————————————————————————————————

def test_switching_storage_carries_branches(mousse_tree, monkeypatch, capsys):
    mousse_tree.make()
    conn = S.connect()
    B.create(conn, "Fitness", about="Training", agents=["g-fit"])
    mid = S.add(conn, text="Alex prefers morning workouts", source="g-fit", agent="g-fit")["id"]
    run_cli(monkeypatch, capsys, "migrate", "markdown", "--notes", str(mousse_tree.vault))
    conn = fresh()
    assert leaf(conn, mid)["branch"] == "Fitness" and 'branch: "[[Fitness]]"' in note_of(conn, mid)
    assert {b["name"]: b["agents"] for b in B.tree(conn)} == {"People": "", "Fitness": "g-fit"}
    run_cli(monkeypatch, capsys, "migrate", "sqlite")
    conn = fresh()
    assert leaf(conn, mid)["branch"] == "Fitness" and B.of_agent(conn, "g-fit") == ["Fitness"]


# —— MCP 工具（各平台看到的）——————————————————————————————————————————————————————

def test_mcp_tools_give_rules_take_a_branch_and_offer_to_drop_profile_lines(mousse_tree):
    import asyncio

    from openmousse_tree import prune as P
    from openmousse_tree import server
    cfg = mousse_tree.make()
    (mousse_tree.home / "USER.md").write_text("## Food\n- Alex is vegetarian\n", encoding="utf8")
    srv = server.platform_server("claude", C.load())
    assert "call profile" in server.instructions(cfg) and "supersedes" in server.instructions(cfg) and "people around them" in server.instructions(cfg)

    def call(name: str, **args) -> str:
        out = asyncio.run(srv.call_tool(name, args))
        blocks = out[0] if isinstance(out, tuple) else out
        return "".join(getattr(b, "text", "") for b in blocks)
    tools = {t.name: t for t in asyncio.run(srv.list_tools())}
    assert "branch" in tools["remember"].inputSchema["properties"] and "also_profile" in tools["forget"].inputSchema["properties"]
    prof = call("profile")
    assert "Alex is vegetarian" in prof and "# Memory tree rules" in prof and "- People (aliases: family" in prof
    said = call("remember", text="Alex's friend Tom is allergic to cats", branch="friends")
    assert said.startswith("Remembered [") and 'on "People"' in said
    assert "already" in call("remember", text="Alex's friend Tom is allergic to cats.")
    mid = said.split("[")[1].split("]")[0]
    conn = S.connect()
    P.plan(conn, {"steps": [{"op": "promote", "id": mid, "section": "People", "text": "Friend Tom is allergic to cats"}]})
    P.apply(conn)
    S.set_status(conn, mid, "active", "owner")
    asked = call("forget", memory_id=mid)
    assert asked.startswith("Forgotten.") and "Friend Tom is allergic to cats" in asked and "also_profile" in asked
    assert "Friend Tom" in S.profile_text()
    assert "also removed 1 profile line" in call("forget", memory_id=mid, also_profile=True)
    assert "Friend Tom" not in S.profile_text()
