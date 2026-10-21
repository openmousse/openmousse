"""记性的闸（13b）和读写表、三档信（13d）：engine/memgate.py 的梯子（经历自动、叶子直接长、档案点头、做法先提议、别人的话进待定）、
三种回合的读写表在工具层卡死（Tool.kinds 之后再 screen 一遍）、skill 和规矩文件默认先提议、每一次决定进事件流（engine_memory）、
读叶子的地方都带上信几分（备料、memory_search、memory_get、/api/tree、tree 工具的 recall）、开源包默认规则写上闸和读写表。
模型是假的，数据都是编的。"""
from __future__ import annotations

import asyncio
import json

import pytest
from openmousse_tree import rules as R
from openmousse_tree import store as S

import config
import engine
import memtree
import sources
import treelib
from engine import context, host_files, memgate, recall_tree, semantic, skill_write, store, tools
from engine.session import MemorySession, user
from test_engine import FakeModel
from test_engine_skills import GOOD

MODEL = "deepseek/deepseek-flash"


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def builtin(tmp_path, monkeypatch, server_json):
    ws = tmp_path / "ws"
    (ws / "memory").mkdir(parents=True)
    monkeypatch.setattr(config.settings, "workspace", ws)
    monkeypatch.setattr(config.settings, "profile", tmp_path / "no-profile.md")
    monkeypatch.setattr(sources, "memory_tree", None)
    server_json({"claw": {"kind": "builtin"}, "engine": {"model": MODEL}})
    with store.tx() as conn:
        conn.execute("DELETE FROM engine_memory")
    return ws


def ctx(kind="owner", agent="main", conversation="main", turn="t1") -> engine.ToolCtx:
    return engine.ToolCtx(turn=turn, kind=kind, agent=agent, conversation=conversation, call_id="c1", control=engine.Control())


# —— 闸：一个函数一张表 ————————————————————————————————————————————————————————————

def test_the_ladder_gets_tighter_from_experience_to_procedure():
    d = memgate.decide
    assert d("experience", "add").action == "write"
    assert d("leaf", "add").action == "write" and d("leaf", "edit").action == "write"
    assert d("leaf", "forget").action == "propose"  # 掏空了回不来
    assert d("profile", "edit").action == "propose" and d("profile", "promote").action == "propose"
    assert all(d("procedure", op).action == "propose" for op in ("add", "edit", "forget"))
    assert d("leaf", "add", turn="background").action == "write"  # 写回替主人长叶子
    # 别人的话：叶子进待定，档案、做法不许
    assert d("leaf", "add", outside=True).action == "hold"
    assert d("experience", "add", outside=True).action == "write"
    assert d("profile", "edit", outside=True).action == "deny" and d("procedure", "edit", outside=True).action == "deny"
    # 赛博有自己的记忆和知识库：长它自己的那份；主人的叶子它觉得该记的先进待定，主人的档案、做法不改；外人回合什么都不写
    assert d("experience", "add", turn="cyber", own=True).action == "write" and d("leaf", "add", turn="cyber", own=True).action == "write"
    assert d("procedure", "edit", turn="cyber", own=True).action == "propose"  # 它自己的技能也先提议
    assert d("leaf", "add", turn="cyber").action == "hold"
    assert all(d(k, "add", turn="cyber").action == "deny" for k in ("profile", "procedure"))
    assert d("experience", "add", turn="cyber").action == "deny" and d("leaf", "forget", turn="cyber").action == "deny"
    assert all(d(k, "add", turn="outsider").action == "deny" for k in memgate.KINDS)
    assert d("nonsense", "add").action == "deny" and d("leaf", "explode").action == "deny"
    assert d("leaf", "add").level == "undo" and d("procedure", "edit").level == "ask" and d("leaf", "add", outside=True).level == "ask"


def test_tree_tool_policy_matches_the_gate():
    """tree 工具的分类（tools.POLICY）和闸是一个意思：add 直接做、forget 先问。"""
    assert tools.classify("tree", ["add", "--text", "x"])[0] == memgate.decide("leaf", "add").level
    assert tools.classify("tree", ["forget", "abc"])[0] == memgate.decide("leaf", "forget").level == "ask"


def test_workspace_files_map_to_memory_kinds_and_rule_files_ask():
    k = memgate.file_kind
    assert k("MEMORY.md") == "leaf" and k("memory/2026-10-19.md") == "experience" and k("./HEARTBEAT.md") == "experience"
    assert k("USER.md") == "profile" and k("AGENTS.md") == "procedure" and k("skills/weekly/SKILL.md") == "procedure"
    assert k("notes/todo.md") is None and k("memoryless.md") is None
    assert set(memgate.ask_files()) == {"USER.md", "AGENTS.md", "SOUL.md", "IDENTITY.md", "TOOLS.md", "skills/**"} == set(host_files.ASK_FILES)


def test_table_is_served_for_the_memory_page(builtin):
    t = memgate.table()
    assert [x["id"] for x in t["kinds"]] == list(memgate.KINDS) and {x["turn"] for x in t["table"]} == {"owner", "cyber", "outsider"}
    outsider = next(x for x in t["table"] if x["turn"] == "outsider")
    assert {c["kind"]: c["write"] for c in outsider["cells"]}["leaf"].startswith("no")
    assert [x["id"] for x in t["tiers"]] == ["profile", "quoted", "inferred"]
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(memgate.router)
    c = TestClient(app)
    assert c.get("/api/engine/memory/table").json()["ok"]
    memgate.record(memgate.decide("leaf", "add"), target="tree:abc", conversation="main", author="main")
    items = c.get("/api/engine/memory", params={"conversation": "main"}).json()["items"]
    assert items[0]["kind"] == "leaf" and items[0]["action"] == "write" and items[0]["target"] == "tree:abc"


def test_nothing_is_recorded_on_the_openclaw_path(tmp_path, monkeypatch, server_json):
    server_json({"claw": {"kind": "openclaw"}})
    monkeypatch.setattr(store, "exists", lambda: False)
    assert memgate.record(memgate.decide("leaf", "add"), target="x") is None


# —— 读写表在工具层卡死 ——————————————————————————————————————————————————————————————

def _tool(name: str, kinds=("owner", "cyber"), **extra) -> engine.Tool:
    async def go(args, c):
        return "ran"
    t = engine.Tool(name=name, description=name, schema={"type": "object", "properties": {}}, run=go, kinds=kinds)
    for k, v in extra.items():
        setattr(t, k, v)
    return t


def test_screen_drops_memory_tools_outside_owner_turns():
    tree, search, other = _tool("tree"), _tool("memory_search"), _tool("ask_loaded_agent")
    assert memgate.screen("owner", [tree, search, other]) == [tree, search, other]
    assert memgate.screen("cyber", [tree, search, other]) == [other]  # 以后给赛博的「找装载的功能 Agent」不是记忆工具，留着
    scoped = _tool("memory_search", memory_scope="cyber")
    assert memgate.screen("cyber", [scoped]) == [scoped]  # 按赛博自己的范围读的工具自己标出来才给


def test_a_cyber_turn_never_sees_a_memory_tool_even_if_someone_marks_it(monkeypatch):
    """哪怕有人把 tree 工具标成赛博也能用（Tool.kinds 写错了），loop 挑工具时照读写表再筛掉：模型根本看不到它。"""
    f = FakeModel([{"text": "hi"}])
    from engine import providers
    monkeypatch.setattr(providers, "stream", f.stream)
    spec = engine.Spec(kind="cyber", model=MODEL, tools=[_tool("tree"), _tool("ask_loaded_agent")], conversation="cyber:x")
    res = run(engine.run_turn(spec, MemorySession(), user("hello")))
    assert res.status == "done" and [t["name"] for t in f.requests[0][1].tools] == ["ask_loaded_agent"]


def test_outsider_turns_still_take_no_tools_at_all():
    with pytest.raises(engine.EngineError):
        run(engine.run_turn(engine.Spec(kind="outsider", model=MODEL, tools=[_tool("ask_loaded_agent", kinds=("outsider",))]), MemorySession(), user("x")))


def test_every_memory_tool_offered_today_is_owner_only(builtin):
    offered = tools.for_agent("main")
    mem = [t for t in offered if t.name in memgate.MEMORY_TOOLS]
    assert mem and all(t.kinds == ("owner",) for t in mem)


# —— skill 和规矩文件默认先提议 ——————————————————————————————————————————————————————————

@pytest.fixture
def skills_home(builtin, monkeypatch):
    (builtin / "skills").mkdir()
    skill_write._saved.clear()
    tools.install_hooks()
    return builtin


def test_creating_a_skill_asks_first_and_runs_once_approved(skills_home, monkeypatch):
    import chat
    import inbox
    t = next(x for x in tools.for_agent("main") if x.name == "skill_manage")
    assert t.level({"action": "create"})[0] == tools.ASK and t.level({"action": "history"})[0] == tools.READ

    async def go():
        out = await tools.gate(t, {"action": "create", "name": "weekly-review", "content": GOOD}, ctx())
        assert out["status"] == "approval-pending" and not (skills_home / "skills" / "weekly-review").exists()
        it = inbox.item(out["inbox"])
        assert it["kind"] == "tool" and "改之前先问你" in it["why"] or "ask you first" in it["why"]
        monkeypatch.setattr(inbox, "kick", lambda thread, text: "started")
        res = await inbox.act(out["inbox"], inbox.ActIn(action="approve"))
        assert res["item"]["status"] == "done"
        assert (skills_home / "skills" / "weekly-review" / "SKILL.md").read_text(encoding="utf8") == GOOD
        for _ in range(50):
            r = chat.RUNS.get("main")
            if not r or r.done:
                break
            await asyncio.sleep(0.05)
    run(go())
    log = memgate.recent(kind="procedure")
    assert [x["action"] for x in reversed(log)] == ["propose", "write"] and all(x["target"] == "skill:weekly-review" for x in log)


def test_levels_auto_still_lets_the_user_loosen_it(skills_home, server_json):
    server_json({"claw": {"kind": "builtin"}, "engine": {"model": MODEL, "tools": {"levels": {"skill_manage": "auto"}}}})
    t = next(x for x in tools.for_agent("main") if x.name == "skill_manage")
    assert t.level({"action": "create"})[0] == tools.UNDO


def test_background_review_proposes_instead_of_saving(skills_home, monkeypatch, server_json):
    server_json({"claw": {"kind": "builtin"}, "engine": {"model": MODEL, "skills": {"review": True}}})
    review = {"save": True, "name": "week-planning", "description": "Use when the user asks to plan their week.",
              "body": "1. Read each day of the week.\n2. Point out clashes."}
    f = FakeModel([{"text": json.dumps(review)}])
    from engine import providers
    monkeypatch.setattr(providers, "stream", f.stream)

    class S_:
        id = "s-review"
    run(skill_write._review("main", S_(), [user("plan my week")], MODEL))
    assert not (skills_home / "skills" / "week-planning").exists()  # 没直接存
    import chat
    import inbox
    with chat._lock, inbox.idb() as conn:
        row = conn.execute("SELECT * FROM inbox WHERE kind='tool' AND title LIKE '%week-planning%' AND status='pending'").fetchone()
    assert row is not None and "后台回看" in row["why"] or "background review" in (row["why"] if row else "")
    assert skill_write._saved["s-review"] == ["?week-planning"]
    notes = skill_write.nudge(S_(), {"skill_manage"})
    assert any(n.get("nudge") == "skill-proposed" for n in notes)


def test_rule_files_ask_and_memory_files_are_recorded(builtin):
    wf = next(t for t in host_files.tools("main") if t.name == "write_file")
    assert wf.level({"path": "AGENTS.md"})[0] == "ask" and wf.level({"path": "USER.md"})[0] == "ask"
    assert wf.level({"path": "memory/2026-10-19.md"})[0] == "undo" and wf.level({"path": "MEMORY.md"})[0] == "undo"
    run(wf.run({"path": "memory/2026-10-19.md", "content": "- went running"}, ctx()))
    run(wf.run({"path": "notes.txt", "content": "not memory"}, ctx()))
    got = memgate.recent()
    assert [(x["kind"], x["action"], x["target"]) for x in got] == [("experience", "write", "file:memory/2026-10-19.md")]


def test_owner_rules_tell_the_model_about_the_gate(builtin):
    from engine import owner
    text = owner.rules("main", tools.for_agent("main"))
    assert "## What you may remember" in text and "held for the user to approve" in text and "three tiers" in text


# —— 信几分（13d） ——————————————————————————————————————————————————————————————————

def test_tiers_follow_who_wrote_it_and_whose_words_they_are():
    t = memgate.tier
    assert t("profile") == "profile"
    assert t("owner") == "quoted" and t(config.OWNER_ID_LEGACY, owner_sources=memtree.OWNER_SOURCES) == "quoted"  # 自己写的
    assert t("claude", "I only drink oat milk", "claude") == "quoted"  # 带原话
    assert t("claude") == "inferred"  # 平台总结的，没原话
    assert t("main", "Sam said she is vegan now", "friend") == "inferred"  # 别人说的：原话不是用户的
    assert memgate.TRUST["profile"] > memgate.TRUST["quoted"] > memgate.TRUST["inferred"]


@pytest.fixture
def pkg_tree(mousse_tree, monkeypatch):
    mousse_tree.make(name="Alex")
    monkeypatch.setattr(sources, "memory_tree", treelib)
    monkeypatch.setitem(sources.AVAILABLE, "tree", True)
    conn = S.connect()
    q = S.add(conn, text="Alex only drinks oat milk in coffee", source="claude", kind="preference", quote="I only drink oat milk in my coffee",
              thread="chat-1")["id"]
    i = S.add(conn, text="Alex probably likes jazz", source="chatgpt")["id"]
    o = S.add(conn, text="Alex runs on Tuesdays", source="owner")["id"]
    f = S.add(conn, text="Sam is vegetarian now", source="main", quote="I'm vegetarian now", platform="friend", thread="fr-1")["id"]
    conn.close()
    return {"quoted": q, "inferred": i, "owner": o, "friend": f}


def test_context_lines_carry_the_tier(builtin, pkg_tree):
    p = context.tree_part("main")
    lines = {x["id"]: x["line"] for x in p.leaves}
    assert lines[pkg_tree["quoted"]].endswith("· quoted)") and lines[pkg_tree["inferred"]].endswith("· inferred)")
    assert lines[pkg_tree["owner"]].endswith("· quoted)") and lines[pkg_tree["friend"]].endswith("· inferred)")
    assert "quoted (in the user's own words" in p.text


def test_api_tree_and_tree_tool_recall_show_the_tier(builtin, pkg_tree):
    snap = memtree.snapshot()
    tiers = {x["id"]: x["tier"] for x in snap["leaves"]}
    assert tiers[pkg_tree["quoted"]] == "quoted" and tiers[pkg_tree["inferred"]] == "inferred" and tiers[pkg_tree["friend"]] == "inferred"
    conn = S.connect()
    out = S.fmt(S.recall(conn, "oat milk"))
    assert ", quoted)" in out and "Trust: profile > quoted" in out
    assert S.trust_tier({"source": "profile"}) == "profile"


def test_semantic_ranking_uses_the_tiers():
    from engine.recall import Hit
    base = dict(chunk=1, source=1, root="tree", path="x", thread=None, day=None, ts=None, role=None, start=1, end=1, heading="", text="t")
    quoted = Hit(**base, author="claude", tier="quoted")
    inferred = Hit(**base, author="chatgpt", tier="inferred")
    pending = Hit(**base, author="claude", tier="quoted", status="pending")
    assert semantic.trust(quoted, set()) == memgate.TRUST["quoted"] > semantic.trust(inferred, set()) == 1.0
    assert semantic.trust(pending, set()) == pytest.approx(memgate.TRUST["quoted"] * 0.9)
    assert semantic.trust(Hit(**{**base, "root": "ws.main", "path": "USER.md"}), set()) == memgate.TRUST["profile"]
    assert recall_tree.tier_of("claude", "", "") == "inferred" and recall_tree.tier_of("owner", "", "") == "quoted"


def test_default_rules_carry_the_gate_and_the_table(mousse_tree):
    cfg = mousse_tree.make(lang="zh", name="小林")
    zh = R.default(cfg)
    assert "## 谁能写什么（记性的闸）" in zh and "## 谁读得到什么" in zh and "待定" in zh and "三档信" in zh and "小林" in zh
    assert "有自己的记忆和知识库" in zh and "转述" in zh
    cfg = mousse_tree.make(lang="en", name="Alex")
    en = R.default(cfg)
    assert "## Who may write what (the memory gate)" in en and "## Who can read what" in en and '"On hold"' in en
