"""OpenMousse 服务器接上开源版世界树（treelib.py、memtree.py、treeprune.py、bridges.py、think.py、engine/context.py）：
枝的接口、新建 Agent 长枝、内核按枝给功能 Agent 看叶子、tree 工具和 think.py 跟着 sources.py 走、每周修剪出卡和点了以后照做、
每周的定时任务。世界树在临时目录里（conftest 的 mousse_tree），数据都是编的。"""
from __future__ import annotations

import asyncio
import json
import shutil
import sys
import types
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from openmousse_tree import branches as B
from openmousse_tree import prune as P
from openmousse_tree import store as S

import bridges
import chat
import data
import inbox
import memtree
import sources
import think
import treelib
import treeprune
from config import TZ


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def tree(mousse_tree, monkeypatch):
    """开源版世界树接上了（sources.py 选中的是 treelib）。收件箱是所有测试共用的：别的测试留下的修剪卡先清掉（同一周只有一张）。"""
    mousse_tree.make()
    monkeypatch.setattr(sources, "memory_tree", treelib)
    monkeypatch.setitem(sources.AVAILABLE, "tree", True)
    with chat._lock, inbox.idb() as db:
        db.execute("DELETE FROM inbox WHERE dedupe LIKE 'tree:prune:%' OR kind='tree'")
    with chat._lock, treeprune.tdb() as db:
        db.execute("DELETE FROM tree_prune")
    return mousse_tree


@pytest.fixture
def pushes(monkeypatch):
    import push
    sent: list[dict] = []

    async def fake_send(title, body, data=None, thread_id=None, **kw):
        sent.append({"title": title, "body": body, **kw})
        return {"ok": True}
    monkeypatch.setattr(push, "send_push", fake_send)
    return sent


@pytest.fixture
def kicks(monkeypatch):
    """收件箱往 Agent 的对话里发的话（同意以后让它去做）：记下来，不真的开一轮。"""
    said: list[tuple[str, str]] = []
    monkeypatch.setattr(inbox, "kick", lambda thread, text: said.append((thread, text)) or "started")
    return said


def group(gid: str, name: str, purpose: str = "") -> None:
    with chat._lock, data.ddb() as conn:
        conn.execute("INSERT OR REPLACE INTO groups(id, name, icon, purpose, created_at) VALUES(?,?,?,?,?)", (gid, name, "x", purpose, chat.now_iso()))


def drop_group(gid: str) -> None:
    with chat._lock, data.ddb() as conn:
        conn.execute("DELETE FROM groups WHERE id=?", (gid,))
        conn.execute("DELETE FROM threads WHERE id=?", (gid,))


# —— treelib：枝是真的 ——————————————————————————————————————————————————————

def test_treelib_has_real_branches_and_the_stored_trunk(tree):
    import i18n
    assert treelib.HAS_BRANCHES and treelib.selected()
    conn = treelib.connect()
    B.create(conn, "Fitness", about="Training", agents=["g-fit"])
    mid = S.add(conn, text="Alex lifts on Mondays", source="g-fit", agent="g-fit")["id"]
    got = treelib.branch_tree(conn)
    assert [{k: b[k] for k in ("name", "parent", "about", "agents", "depth", "leaves")} for b in got] == [
        {"name": "People", "parent": "Profile", "about": got[0]["about"], "agents": "", "depth": 1, "leaves": 0},
        {"name": "Fitness", "parent": "Profile", "about": "Training", "agents": "g-fit", "depth": 1, "leaves": 1}]
    token = i18n.use("zh-CN")  # 手机是中文的请求
    try:
        assert treelib.TRUNK == "Profile"  # 存下来的那个，和请求的语言无关
    finally:
        i18n.reset(token)
    assert treelib.set_branch(conn, mid, "partner", "owner") == "People"
    assert treelib.set_branch(conn, mid, "Profile", "owner") == "Profile"
    with pytest.raises(ValueError):
        treelib.set_branch(conn, mid, "Nowhere", "owner")
    assert treelib.branch_names() == ["Profile", "People", "Fitness"]


def test_api_tree_shows_branches_and_forget_lists_profile_lines(tree):
    (tree.home / "USER.md").write_text("## Food\n- Alex is vegetarian\n", encoding="utf8")
    group("g-fit", "Fitness", "Training plans")
    try:
        conn = treelib.connect()
        B.ensure_agent(conn, "g-fit", "Fitness", "Training plans")
        a = S.add(conn, text="Alex warms up for ten minutes", source="g-fit", agent="g-fit")["id"]
        b = S.add(conn, text="Alex's partner Sam is gluten-free", source="claude", tags="partner", status="pending")["id"]
        snap = memtree.snapshot()
        assert snap["branchable"] and snap["trunk"]["name"] == "Profile" and snap["trunk"]["count"] == 0
        names = {x["name"]: x for x in snap["branches"]}
        assert names["Fitness"]["agents"] == [{"id": "g-fit", "name": "Fitness"}] and names["Fitness"]["leaves"] == 1
        leaves = {x["id"]: x for x in snap["leaves"]}
        assert leaves[a]["branch"] == "Fitness" and leaves[a]["sourceName"] == "Fitness" and leaves[a]["agent"] == "g-fit"
        assert leaves[b]["branch"] == "People" and leaves[b]["status"] == "pending"
        assert memtree.act(b, "move", "Fitness", "You") == {"changed": True, "branch": "Fitness"}
        with pytest.raises(ValueError):
            memtree.act(b, "move", "Nowhere", "You")
        P.plan(conn, {"steps": [{"op": "promote", "id": a, "section": "Habits", "text": "Warms up for ten minutes"}]})
        P.apply(conn)
        fresh = S.add(conn, text="Alex stretches after every run", source="claude")["id"]
        assert memtree.act(fresh, "forget", "", "You") == {"changed": True, "profileLines": []}
        assert S.set_status(conn, a, "active", "owner")  # 归档的那片拿回来，好在 app 里忘掉它
        res = memtree.act(a, "forget", "", "You")
        assert res["changed"] and [x["text"] for x in res["profileLines"]] == ["Warms up for ten minutes"]
        assert "Warms up for ten minutes" in S.profile_text()  # 没说要一起删就不动
    finally:
        drop_group("g-fit")


def test_forget_with_also_profile_removes_the_lines(tree):
    (tree.home / "USER.md").write_text("## Habits\n- Alex reads before bed\n", encoding="utf8")
    conn = treelib.connect()
    a = S.add(conn, text="Alex walks to work", source="claude")["id"]
    P.plan(conn, {"steps": [{"op": "promote", "id": a, "section": "Habits", "text": "Walks to work"}]})
    P.apply(conn)
    S.set_status(conn, a, "active", "owner")
    assert memtree.act(a, "forget", "", "You", also_profile=True) == {"changed": True, "profileRemoved": ["Walks to work"]}
    assert "Walks to work" not in S.profile_text() and "reads before bed" in S.profile_text()


# —— 新建 Agent 长一根小枝；老 Agent 起服务器时补上 ————————————————————————————————————————

def test_new_agent_gets_a_branch_and_old_ones_get_theirs_once(tree, server_json):
    server_json({"claw": {"kind": "builtin"}})
    res = data.create_group(data.GroupIn(name="Sleep", purpose="Reads last night's sleep", model="deepseek/deepseek-flash"))
    gid = res["id"]
    group("g-old1", "Garden", "Plants and seasons")
    try:
        conn = treelib.connect()
        assert B.of_agent(conn, gid) == ["Sleep"] and {b["name"]: b for b in B.tree(conn)}["Sleep"]["about"] == "Reads last night's sleep"
        added = treelib.sync_agents()
        assert "g-old1" in added and gid not in added and B.of_agent(conn, "g-old1") == ["Garden"]
        conn.execute("DELETE FROM branch WHERE name='Garden'")  # 主人把那根枝删了
        conn.commit()
        assert "g-old1" not in treelib.sync_agents() and B.of_agent(conn, "g-old1") == []  # 不再长回来
    finally:
        drop_group(gid)
        drop_group("g-old1")


def test_agent_branch_needs_the_open_source_tree(tree, monkeypatch):
    monkeypatch.setattr(sources, "memory_tree", types.ModuleType("memory_tree"))  # 选中的是作者那份
    assert treelib.agent_created("g-x", "X") is None and treelib.sync_agents() == []
    assert B.of_agent(treelib.connect(), "g-x") == []


# —— 内核按枝给功能 Agent 看叶子 ——————————————————————————————————————————————————

def test_engine_context_gives_each_agent_the_leaves_on_its_branches(tree):
    from engine import context
    conn = treelib.connect()
    B.create(conn, "Body", about="Health")
    B.create(conn, "Fitness", parent="Body", about="Training", agents=["g-fit"])
    B.create(conn, "Strength", parent="Fitness", about="Lifting")
    B.create(conn, "Food", parent="Body", about="Meals", agents=["g-food"])
    a = S.add(conn, text="Alex deadlifts 120kg", source="claude", branch="Strength")["id"]
    b = S.add(conn, text="Alex rests on Wednesdays", source="g-fit", agent="g-fit", status="pending")["id"]
    S.add(conn, text="Alex eats oats every morning", source="g-food", agent="g-food")
    S.add(conn, text="Alex lives in Wrenford", source="claude")
    fit = context.tree_part("g-fit")
    assert f"[{a}] Alex deadlifts 120kg (Strength" in fit.text and f"[{b}] Alex rests on Wednesdays (Fitness" in fit.text
    assert "not confirmed yet" in fit.text and "oats" not in fit.text and "Wrenford" not in fit.text
    assert fit.title == "Memory tree: your branches Fitness"
    main = context.tree_part("main").text
    assert all(x in main for x in ("deadlifts", "oats", "Wrenford (Profile"))
    assert context.tree_part("g-nobranch").text == ""  # 没有枝的 Agent 什么都不给


# —— tree 工具和 think.py 跟着 sources.py 走 ——————————————————————————————————————————————

FAKE_SCRIPT = """import json, sys
from pathlib import Path
Path(sys.argv[0]).with_suffix(".log").open("a").write(json.dumps(sys.argv[1:]) + "\\n")
if sys.argv[1] == "branches":
    print("- 档案：主干\\n- 人：身边的人\\n  - 健身：训练")
elif sys.argv[1] == "add":
    print(json.dumps({"id": "s1", "duplicate": False}))
"""


@pytest.fixture
def script_tree(tmp_path, monkeypatch):
    """选中的是作者那份：workspace 的 scripts/memory_tree.py（这里是个假的，把参数记进 memory_tree.log）。"""
    path = tmp_path / "scripts" / "memory_tree.py"
    path.parent.mkdir()
    path.write_text(FAKE_SCRIPT, encoding="utf8")
    mod = types.ModuleType("memory_tree")
    mod.__file__ = str(path)
    monkeypatch.setattr(sources, "memory_tree", mod)
    return path


def test_tree_tool_runs_whichever_tree_sources_picked(tree, script_tree, monkeypatch):
    assert bridges.command("tree") == ["python3", str(script_tree)]  # 作者那份：和 think.py 一样用 python3 跑脚本
    monkeypatch.setattr(sources, "memory_tree", treelib)
    monkeypatch.setattr(shutil, "which", lambda name: f"/opt/bin/{name}")
    exe = Path(sys.executable).parent / "mousse-tree"
    assert bridges.command("tree") == [str(exe) if exe.exists() else "/opt/bin/mousse-tree"]  # 开源版：同一个 venv 里的命令行
    monkeypatch.setattr(sources, "memory_tree", None)
    assert bridges.command("tree") is None  # 两份都没接：不给这个工具


def test_think_writes_to_the_script_when_sources_picked_it(script_tree):
    assert think.branches() == ["档案", "人", "健身"]
    assert think.remember_tree("小林认为慢慢来比较快", "人", ["想法"]) == {"id": "s1", "duplicate": False}
    calls = [json.loads(x) for x in script_tree.with_suffix(".log").read_text().splitlines()]
    assert calls[0] == ["branches"]
    assert calls[1][:4] == ["add", "--source", "grava", "--kind"] and calls[1][-2:] == ["--branch", "人"]


def test_think_falls_back_to_the_package(tree):
    conn = treelib.connect()
    B.create(conn, "Ideas", about="How Alex thinks")
    assert think.branches() == ["Profile", "People", "Ideas"]
    r = think.remember_tree("Alex thinks slow is smooth", "Ideas", ["thinking"])
    assert not r["duplicate"] and r["branch"] == "Ideas"
    row = dict(conn.execute("SELECT * FROM tree WHERE id=?", (r["id"],)).fetchone())
    assert row["source"] == "main" and row["tags"] == "thinking"
    assert think.remember_tree("Alex thinks slow is smooth", "Ideas", [])["duplicate"]


def test_tree_tool_policy_and_children(tree):
    from engine import tasks, tools
    assert {"profile", "branches", "prune-context", "prune-plan"} <= bridges.TREE_OK and "prune-apply" not in bridges.TREE_OK
    assert tools.classify("tree", ["prune-context"])[0] == "read" and tools.classify("tree", ["prune-plan", "--file", "-"])[0] == "undo"
    assert tools.classify("tree", ["profile"])[0] == "read"
    with pytest.raises(bridges.BridgeError):
        bridges.guard("tree", ["prune-apply"])
    assert "tree prune-plan" in tasks.CHILD_ASK
    assert "tree-prune" in tools.list_skills("main", {"tree"}) and "tree-prune" not in tools.list_skills("main", set())


# —— 每周修剪：出卡、点了以后照做 ——————————————————————————————————————————————————————

def plan_for(conn) -> dict:
    a = S.add(conn, text="Alex has a dentist appointment next week", source="claude", kind="event")["id"]
    b = S.add(conn, text="Alex's colleague Ines speaks four languages", source="claude")["id"]
    return {"summary": "Two small things", "steps": [{"op": "archive", "id": a, "why": "it has passed"}, {"op": "branch", "id": b, "to": "People"}]}


def submit(conn, plan: dict, agent: str = "main") -> str:
    got = P.plan(conn, plan, source=agent)
    line = json.dumps({"ok": True, **got})
    return run(treeprune.after_tree(["prune-plan", "--file", "-"], line, agent))


def test_a_plan_becomes_one_quiet_card_and_approving_prunes(tree, pushes, kicks):
    conn = treelib.connect()
    plan = plan_for(conn)
    msg = submit(conn, plan)
    assert "inbox card ib-" in msg and "[SILENT]" in msg
    iid = msg.split("inbox card ")[1].split(" ")[0]
    it = inbox.item(iid)
    week = P.week_of()
    assert it["kind"] == "tree" and it["level"] == "quiet" and it["thread"] == "main" and it["approveLabel"] == "Prune as planned"
    assert it["title"] == "Weekly memory tree pruning: 2 changes" and it["why"] == "Two small things"
    assert it["changes"][0].startswith('Archive "Alex has a dentist') and 'to "People"' in it["changes"][1]
    exp = datetime.fromisoformat(it["expiresAt"])
    assert timedelta(days=6) < exp - datetime.now(TZ) <= timedelta(days=7)
    with chat._lock, inbox.idb() as db:
        assert db.execute("SELECT dedupe FROM inbox WHERE id=?", (iid,)).fetchone()[0] == f"tree:prune:{week}"
    assert it["tree"]["week"] == week and len(it["tree"]["steps"]) == 2  # 卡上多给 app 的
    assert pushes and pushes[-1]["level"] == "quiet"
    # 同一周再交一份：原地换掉这张卡
    plan["steps"] = plan["steps"][:1]
    again = submit(conn, plan)
    assert iid in again and inbox.item(iid)["title"] == "Weekly memory tree pruning: 1 change"
    res = run(inbox.act(iid, inbox.ActIn(action="approve")))
    done = res["item"]
    assert done["status"] == "done" and done["result"].startswith("Pruned: 1 archived") and "run" not in res
    assert kicks == []  # 服务端做完了：不往对话里发话
    archived = plan["steps"][0]["id"]
    assert dict(conn.execute("SELECT status FROM tree WHERE id=?", (archived,)).fetchone())["status"] == "archived"
    assert P.load(week)["status"] == "applied"
    status = treeprune.get_prune()
    assert status["plan"]["week"] == week and status["card"] == {"inboxId": iid, "status": "applied", "result": done["result"]}


def test_declined_or_edited_cards_change_nothing(tree, pushes, kicks):
    conn = treelib.connect()
    plan = plan_for(conn)
    iid = submit(conn, plan).split("inbox card ")[1].split(" ")[0]
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as e:  # 服务器照计划出的卡：Agent 不能改卡上的字（要改就重新交计划）
        run(inbox.update(iid, inbox.ItemPatch(title="Something else")))
    assert e.value.status_code == 409 and inbox.item(iid)["title"] == "Weekly memory tree pruning: 2 changes"
    with chat._lock, treeprune.tdb() as db:  # 10/17 以前交的卡（没有逐条）：卡被改过就不动，照以前
        db.execute("UPDATE tree_prune SET card='an-old-hash' WHERE inbox_id=?", (iid,))
    with chat._lock, inbox.idb() as db:
        db.execute("UPDATE inbox SET items=NULL WHERE id=?", (iid,))
    res = run(inbox.act(iid, inbox.ActIn(action="approve")))
    assert res["item"]["status"] == "failed" and "edited" in res["item"]["result"]
    assert dict(conn.execute("SELECT status FROM tree WHERE id=?", (plan["steps"][0]["id"],)).fetchone())["status"] == "active"
    P.discard(P.week_of(), "rejected")  # 下面换一周的卡：先把这一周作废
    with chat._lock, inbox.idb() as db:
        db.execute("UPDATE inbox SET dedupe='old' WHERE id=?", (iid,))
    iid2 = submit(conn, plan).split("inbox card ")[1].split(" ")[0]
    run(inbox.act(iid2, inbox.ActIn(action="reject", note="not now")))
    assert P.load(P.week_of())["status"] == "rejected"
    with pytest.raises(P.PlanError):
        P.apply(conn)
    assert "declined" in submit(conn, plan)  # 30 天内拒过这一周的：不再出卡
    assert kicks == []


def test_revising_the_card_asks_the_main_chat_to_resubmit(tree, pushes, kicks):
    conn = treelib.connect()
    iid = submit(conn, plan_for(conn)).split("inbox card ")[1].split(" ")[0]
    run(inbox.act(iid, inbox.ActIn(action="revise", note="keep the dentist one")))
    assert kicks and kicks[0][0] == "main" and "prune-plan" in kicks[0][1] and "keep the dentist one" in kicks[0][1]
    ctx = inbox.reply_context(iid)
    assert ctx is not None and "prune-plan" in ctx[1]


def test_no_card_when_pruning_is_off_or_the_plan_is_empty(tree, server_json, pushes):
    conn = treelib.connect()
    iid = submit(conn, plan_for(conn)).split("inbox card ")[1].split(" ")[0]
    assert "no changes" in submit(conn, {"steps": []})  # 这周不修了：之前那张卡撤掉
    assert inbox.item(iid)["status"] == "withdrawn" and P.load()["status"] == "withdrawn"
    server_json({"tree": {"prune": False}})
    assert "off in server.json" in submit(conn, plan_for(conn))
    assert run(treeprune.after_tree(["recall", "--q", "x"], "- [a] x", "main")) == "- [a] x"  # 别的子命令原样


# —— 每周的定时任务 ——————————————————————————————————————————————————————————

def test_weekly_job_is_created_moved_and_removed_by_server_json(tree, server_json):
    from engine import jobs
    server_json({"claw": {"kind": "builtin"}})
    jid = treeprune.ensure_job()
    try:
        j = jobs.get(jid)
        assert j["agent"] == "main" and j["state"] == "on" and j["approved"] and j["created_by"] == treeprune.SYSTEM
        assert j["schedule"] == {"kind": "daily", "at": "19:00", "days": [6], "text": "every sunday 19:00"} and j["level"] == "none"
        assert j["deliver"] == "main" and j["session"] == "isolated" and "tree-prune" in j["prompt"] and "prune-plan" in j["prompt"]
        assert datetime.fromisoformat(j["next_run"]).weekday() == 6
        assert treeprune.ensure_job() == jid  # 再起一次服务器：还是这一条
        server_json({"claw": {"kind": "builtin"}, "tree": {"prune": {"when": "every saturday 9am"}}})
        assert treeprune.ensure_job() == jid and jobs.get(jid)["schedule"]["days"] == [5]
        server_json({"claw": {"kind": "builtin"}, "tree": {"prune": False}})
        assert treeprune.ensure_job() is None and jobs.get(jid)["state"] == "deleted"
        server_json({"claw": {"kind": "openclaw"}})
        assert treeprune.ensure_job() is None  # OpenClaw 那条路：内核的定时任务不跑，什么都不建
    finally:
        jobs._write(jid, state="deleted", next_run=None)


def test_no_weekly_job_for_the_authors_tree(tree, server_json, monkeypatch):
    server_json({"claw": {"kind": "builtin"}})
    monkeypatch.setattr(sources, "memory_tree", types.ModuleType("memory_tree"))
    assert treeprune.ensure_job() is None and treeprune.tree() is None


# —— 一路走通：tree 工具起真的 mousse-tree ——————————————————————————————————————————————————

@pytest.mark.skipif(not (shutil.which("mousse-tree") or (Path(sys.executable).parent / "mousse-tree").exists()),
                    reason="mousse-tree isn't installed (pip install ./tree)")
def test_tree_tool_end_to_end_with_the_real_cli(tree, pushes, kicks):
    conn = treelib.connect()
    B.create(conn, "Fitness", about="Training", agents=["g-fit"])
    group("g-fit", "Fitness")
    try:
        out = json.loads(run(bridges.run("tree", ["add", "--source", "claude", "--text", "Alex swims on Fridays"], None, "g-fit")))
    finally:
        drop_group("g-fit")
    row = dict(treelib.connect().execute("SELECT * FROM tree WHERE id=?", (out["id"],)).fetchone())
    assert row["source"] == "g-fit" and row["branch"] == "Fitness"  # MOUSSE_AGENT：记成那个 Agent，挂到它的枝
    assert "Memory tree rules" in run(bridges.run("tree", ["profile"], None, "main"))
    plan = {"steps": [{"op": "branch", "id": out["id"], "to": "People"}]}
    said = run(bridges.run("tree", ["prune-plan", "--file", "-"], json.dumps(plan), "main"))
    assert "inbox card ib-" in said
    assert P.load()["source"] == "main"
    with pytest.raises(bridges.BridgeError) as e:
        run(bridges.run("tree", ["prune-plan", "--file", "-"], json.dumps({"steps": [{"op": "archive", "id": "nope00000000"}]}), "main"))
    assert "nope00000000" in str(e.value)


@pytest.mark.skipif(not (shutil.which("mousse-tree") or (Path(sys.executable).parent / "mousse-tree").exists()),
                    reason="mousse-tree isn't installed (pip install ./tree)")
def test_the_weekly_job_turn_reads_the_context_submits_a_plan_and_stays_silent(tree, server_json, pushes, kicks, monkeypatch):
    from engine import jobs, providers, tools, when
    from test_engine import FakeModel
    server_json({"claw": {"kind": "builtin"}, "engine": {"model": "deepseek/deepseek-flash"}})
    tools.install_hooks()
    conn = treelib.connect()
    old = S.add(conn, text="Alex has a job interview on Tuesday", source="claude", kind="event")["id"]
    plan = {"summary": "The interview is over", "steps": [{"op": "archive", "id": old, "why": "it has passed"}]}
    model = FakeModel([{"calls": [("c1", "skill", {"name": "tree-prune"})]},
                       {"calls": [("c2", "tree", {"args": ["prune-context"]})]},
                       {"calls": [("c3", "tree", {"args": ["prune-plan", "--file", "-"], "input": json.dumps(plan)})]},
                       {"text": "[SILENT]"}])
    monkeypatch.setattr(providers, "stream", model.stream)
    jid = treeprune.ensure_job()
    try:
        out = run(jobs.execute(jobs.get(jid), None, "manual", 0.0, when.now()))
    finally:
        jobs._write(jid, state="deleted", next_run=None)
    assert out.status == "silent"  # 卡就是结果：对话里什么都不发
    results = [m for m in model.requests[-1][1].messages if m["role"] == "tool"]
    texts = ["".join(c.get("text", "") for c in m["content"]) for m in results]
    assert "# 世界树每周修剪" in texts[0] and "Pruning material for the week" in texts[1] and "inbox card ib-" in texts[2]
    iid = texts[2].split("inbox card ")[1].split(" ")[0]
    assert inbox.item(iid)["kind"] == "tree" and inbox.item(iid)["source"] == "main" and P.load()["source"] == "main"
    first = "".join(c.get("text", "") for c in model.requests[0][1].messages[0]["content"])
    assert "[Scheduled job]" in first and "[Weekly memory tree pruning]" in first and "tree-prune skill" in first
    run(inbox.act(iid, inbox.ActIn(action="approve")))
    assert dict(conn.execute("SELECT status FROM tree WHERE id=?", (old,)).fetchone())["status"] == "archived" and kicks == []
