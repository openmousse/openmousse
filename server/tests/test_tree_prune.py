"""开源版世界树的每周修剪（openmousse_tree.prune）：context 列出可剪的、plan 校验后存下、apply 照做（幂等、记来源），
忘掉一片叶子时列出档案里从它来的句子。全在临时目录里，数据都是编的。"""
from __future__ import annotations

import json
import sys
from datetime import date, timedelta

import pytest
from openmousse_tree import branches as B
from openmousse_tree import cli
from openmousse_tree import origins as O
from openmousse_tree import prune as P
from openmousse_tree import store as S

BOTH = pytest.mark.parametrize("storage", ["sqlite", "markdown"])
LATER = date.today() + timedelta(days=45)  # 「一个半月以后」看这棵树


def leaf(conn, mid: str) -> dict:
    return dict(conn.execute("SELECT * FROM tree WHERE id=?", (mid,)).fetchone())


def add(conn, text: str, **kw) -> str:
    r = S.add(conn, text=text, source=kw.pop("source", "claude"), **kw)
    assert not r["duplicate"], r
    return r["id"]


def current(conn) -> set[str]:
    return {r["id"] for r in conn.execute("SELECT id FROM tree WHERE source != 'profile' AND status IN ('active','pending')")}


def test_context_lists_what_could_be_pruned(mousse_tree):
    mousse_tree.make()
    (mousse_tree.home / "USER.md").write_text("## Preferences\n- Alex likes tea\n\n## Work\n- Alex is a nurse\n", encoding="utf8")
    conn = S.connect()
    S.sync_profile(conn)
    stable = add(conn, "Alex prefers window seats on trains", kind="preference")
    event = add(conn, "Alex is moving to a new flat this month", kind="event", observed_at="2026-09-01")
    sim_a = add(conn, "Alex takes the 8:10 bus to work every weekday")
    sim_b = add(conn, "Every weekday Alex takes the 8:10 bus to work")
    early = add(conn, "Alex squats three times a week", source="g-fit")  # Agent 有枝之前写的：挂在主干上
    B.create(conn, "Fitness", about="Training", agents=["g-fit"])
    gone = add(conn, "Alex's aunt Rosa grows tomatoes", branch="People")
    conn.execute("UPDATE tree SET branch='Garden' WHERE id=?", (gone,))  # 挂的那根枝后来没了
    conn.commit()
    words = "amber birch cedar delta ember fjord grove harbor island juniper kestrel lagoon meadow nectar orchard prairie quartz " \
            "river summit tundra upland valley willow xylem yarrow zephyr alpine bramble canyon dune estuary".split()
    for i, w in enumerate(words):  # 31 片挂在「人」上（直接写进库，不过查重）
        S._insert(conn, text=f"{w.title()} {i} is a friend of Alex from the {w} club", source="claude", kind="fact", tags="",
                  observed_at=None, status="active", supersedes=None, branch="People")
    conn.commit()
    ctx = P.context(conn, today=LATER)
    assert ctx["week"] == P.week_of(LATER) and date.fromisoformat(ctx["week"]).weekday() == 6  # 那周的周日
    assert stable in {x["id"] for x in ctx["stable"]} and event not in {x["id"] for x in ctx["stable"]}
    assert [x["id"] for x in ctx["recent"]] == [event]
    assert any({sim_a, sim_b} <= {x["id"] for x in g} for g in ctx["similar"])
    mis = {x["id"]: x for x in ctx["misplaced"]}
    assert mis[early]["why"] == "trunk" and mis[early]["suggest"] == "Fitness"
    assert mis[gone]["why"] == "gone" and mis[gone]["branch"] == "Garden"
    assert [(c["branch"], c["leaves"]) for c in ctx["crowded"]] == [("People", 31)]
    assert ctx["sections"] == ["Preferences", "Work"] and not ctx["empty"] and ctx["counts"]["crowded"] == 1
    text = P.context_text(ctx)
    assert f"[{stable}] Alex prefers window seats" in text and 'suggested "Fitness"' in text and "Preferences, Work" in text
    assert P.context(S.connect(), today=date.today())["counts"]["stable"] == 0  # 今天看：还没稳定两周


def test_an_empty_week_says_so(mousse_tree):
    mousse_tree.make()
    conn = S.connect()
    add(conn, "Alex likes jazz")
    ctx = P.context(conn)
    assert ctx["empty"] and "Nothing to prune this week" in P.context_text(ctx)


def test_plan_is_checked_and_every_problem_listed(mousse_tree):
    mousse_tree.make()
    conn = S.connect()
    a, b = add(conn, "Alex cycles to work"), add(conn, "Alex owns a red road bike")
    with pytest.raises(P.PlanError) as e:
        P.plan(conn, {"steps": [
            {"op": "merge", "ids": [a], "text": ""},                  # 一片不够、没写 text
            {"op": "archive", "id": a},                               # a 已经在第 1 步了
            {"op": "archive", "id": "nope00000000"},                  # 没有这片
            {"op": "branch", "id": b, "to": "Nowhere"},               # 没有这根枝
            {"op": "promote", "id": "nope11111111"},                  # 没 section、没这片
            {"op": "sprout", "name": "family", "about": ""},          # 和别名撞了、没写 about
            {"op": "prune", "id": b},                                 # 不认识的 op
        ]})
    problems = e.value.problems
    assert len(problems) >= 8 and problems[0].startswith("Step 1:") and any("already in step 1" in p for p in problems)
    assert any("Nowhere" in p for p in problems) and any("op must be one of" in p for p in problems)
    with pytest.raises(P.PlanError):
        P.plan(conn, "{not json")
    ok = P.plan(conn, {"summary": "Sort out cycling", "steps": [
        {"op": "sprout", "name": "Cycling", "about": "Bikes and rides", "aliases": ["bike"]},
        {"op": "branch", "id": a, "to": "bike", "why": "it is about cycling"}]}, source="main")  # 用前面一步长出来的枝（别名也行）
    doc = P.load(ok["week"])
    assert ok["steps"] == 2 and doc["digest"] == ok["digest"] and doc["status"] == "planned" and doc["source"] == "main"
    assert doc["steps"][1]["to"] == "Cycling" and doc["steps"][1]["was"] == "Alex cycles to work" and not doc["steps"][1]["done"]
    assert P.lines(doc) == ['New branch "Cycling"', 'Move "Alex cycles to work" to "Cycling"'] and P.summary(doc) == "1 new branches, 1 moved"


@BOTH
def test_apply_does_every_kind_of_step_once_and_records_where_things_came_from(mousse_tree, storage):
    mousse_tree.make(storage)
    (mousse_tree.home / "USER.md").write_text("## Preferences\n- Alex likes tea\n\n## Work\n- Alex is a nurse\n", encoding="utf8")
    conn = S.connect()
    m1 = add(conn, "Alex takes the 8:10 bus to work every weekday", tags="commute")
    m2 = add(conn, "Every weekday Alex takes the 8:10 bus to work", tags="bus", source="chatgpt")
    rw = add(conn, "Alex is training for a 10k in November", kind="event")
    ar = add(conn, "Alex has an exam on Friday", kind="event")
    pr = add(conn, "Alex prefers aisle seats on planes", kind="preference")
    mv = add(conn, "Alex's neighbour Ivo lends him tools")
    sp = add(conn, "Alex plays five-a-side football on Thursdays")
    plan = {"summary": "Weekly tidy", "steps": [
        {"op": "merge", "ids": [m1, m2], "text": "Alex takes the 8:10 bus to work on weekdays", "why": "same fact"},
        {"op": "rewrite", "id": rw, "text": "Alex ran a 10k in November"},
        {"op": "archive", "id": ar, "why": "the exam is over"},
        {"op": "promote", "id": pr, "section": "Preferences", "text": "Prefers aisle seats on planes"},
        {"op": "branch", "id": mv, "to": "People"},
        {"op": "sprout", "name": "Sport", "parent": "People", "about": "Games Alex plays", "move": [sp]},
    ]}
    week = P.plan(conn, plan)["week"]
    res = P.apply(conn)
    assert (res["week"], res["done"], res["skipped"], res["failed"]) == (week, 6, [], [])
    assert res["summary"] == "1 merged, 1 rewritten, 1 archived, 1 into the profile, 1 moved, 1 new branches"
    doc = P.load(week)
    merged = doc["steps"][0]["result"]["id"]
    row = leaf(conn, merged)
    assert row["source"] == "prune" and row["supersedes"] == m1 and row["status"] == "active" and row["text"] == "Alex takes the 8:10 bus to work on weekdays"
    assert set(row["tags"].split(",")) == {"commute", "bus"}
    assert leaf(conn, m1)["status"] == leaf(conn, m2)["status"] == "superseded"
    rewritten = doc["steps"][1]["result"]["id"]
    assert leaf(conn, rewritten)["supersedes"] == rw and leaf(conn, rewritten)["kind"] == "event" and leaf(conn, rw)["status"] == "superseded"
    assert leaf(conn, ar)["status"] == "archived" and leaf(conn, pr)["status"] == "archived"
    profile = S.profile_text()
    assert "## Preferences\n- Alex likes tea\n- Prefers aisle seats on planes\n\n## Work" in profile  # 并进那一节的末尾
    assert leaf(conn, mv)["branch"] == "People"
    tree = {b["name"]: b for b in B.tree(conn)}
    assert tree["Sport"]["parent"] == "People" and tree["Sport"]["depth"] == 2 and leaf(conn, sp)["branch"] == "Sport"
    origins = O.load()
    assert origins["leaves"][merged]["from"] == [m1, m2] and origins["leaves"][rewritten]["from"] == [rw]
    pid = doc["steps"][3]["result"]["profile"]
    assert origins["profile"][pid]["from"] == [pr] and pid == S.profile_id("Preferences", "Prefers aisle seats on planes")
    if storage == "markdown":
        assert (mousse_tree.vault / "branches" / "Sport.md").is_file() and (mousse_tree.vault / "Archive").is_dir()
    # 再跑一遍：做过的都跳过，什么都不多出来
    before = (len(current(conn)), conn.execute("SELECT COUNT(*) FROM tree").fetchone()[0], S.profile_text())
    again = P.apply(conn)
    assert again["done"] == 6 and not again["failed"]
    assert (len(current(conn)), conn.execute("SELECT COUNT(*) FROM tree").fetchone()[0], S.profile_text()) == before


def test_apply_skips_what_changed_resumes_and_refuses_the_wrong_plan(mousse_tree):
    mousse_tree.make()
    conn = S.connect()
    a, b, c = add(conn, "Alex knits scarves"), add(conn, "Alex's sister Mia is a dentist"), add(conn, "Alex hates cold coffee")
    ok = P.plan(conn, {"steps": [{"op": "archive", "id": a}, {"op": "branch", "id": b, "to": "People"},
                                 {"op": "rewrite", "id": c, "text": "Alex only drinks hot coffee"}]})
    with pytest.raises(P.PlanError):
        P.apply(conn, digest="0000000000000000")  # 卡上那份不是这份
    S.set_status(conn, b, "retracted", "owner")  # 定计划以后忘掉了一片
    doc = P.load(ok["week"])
    doc["steps"][0].update(done=True)  # 上次跑到一半：第一步做过了（标了），第三步做了没来得及标
    P._save(doc)
    S.set_status(conn, a, "archived", "owner")
    newer = S.write(conn, text="Alex only drinks hot coffee", source="prune", replaces=[c])
    res = P.apply(conn, ok["week"], digest=ok["digest"])
    assert res["done"] == 2 and [n for n, _ in res["skipped"]] == [2] and not res["failed"]
    assert P.load(ok["week"])["steps"][2]["result"] == {"id": newer}  # 认出是做过的，没再写一片
    assert conn.execute("SELECT COUNT(*) FROM tree WHERE source='prune'").fetchone()[0] == 1
    later = P.plan(conn, {"steps": []}, source="main")  # 同一周再交一份：做过的那份改名留着
    assert later["steps"] == 0 and len(list(P.plan_dir().glob(f"{ok['week']}.*.json"))) == 1
    assert P.discard(ok["week"], "rejected") and P.load(ok["week"])["status"] == "rejected"
    with pytest.raises(P.PlanError):
        P.apply(conn, ok["week"])


@BOTH
def test_forgetting_a_leaf_lists_profile_lines_that_came_from_it(mousse_tree, storage, monkeypatch, capsys):
    mousse_tree.make(storage)
    (mousse_tree.home / "USER.md").write_text("## Food\n- Alex is vegetarian\n", encoding="utf8")
    conn = S.connect()
    a = add(conn, "Alex drinks oat milk in coffee")
    b = add(conn, "Alex uses oat milk in his coffee every morning", source="chatgpt")
    P.plan(conn, {"steps": [{"op": "merge", "ids": [a, b], "text": "Alex takes oat milk in his coffee"}]})
    merged = P.apply(conn)["done"] and P.load()["steps"][0]["result"]["id"]
    P.plan(conn, {"steps": [{"op": "promote", "id": merged, "section": "Food", "text": "Takes oat milk in coffee"}]})
    P.apply(conn)
    assert "- Takes oat milk in coffee" in S.profile_text()
    found = S.derived_profile(conn, a)  # 忘的是合并前的那片：合并、并进档案连着算
    assert [(x["section"], x["text"]) for x in found] == [("Food", "Takes oat milk in coffee")]
    monkeypatch.setattr(sys, "argv", ["mousse-tree", "forget", a])
    cli.main()
    out = capsys.readouterr().out
    assert out.startswith("ok\n") and "- [Food] Takes oat milk in coffee" in out and f"mousse-tree forget {a} --with-profile" in out
    assert "Takes oat milk in coffee" in S.profile_text()  # 没说要删就不动
    monkeypatch.setattr(sys, "argv", ["mousse-tree", "forget", a, "--with-profile"])
    cli.main()
    assert "Also removed 1 line(s) from the profile" in capsys.readouterr().out
    conn = S.connect()
    assert "oat milk" not in S.profile_text() and "Alex is vegetarian" in S.profile_text() and S.derived_profile(conn, a) == []
    assert S.derived_profile(conn, add(conn, "Alex goes to bed at eleven")) == []


def test_admin_page_forget_offers_to_drop_profile_lines(mousse_tree):
    import asyncio

    import httpx

    from openmousse_tree import server
    mousse_tree.make()
    (mousse_tree.home / "USER.md").write_text("## Habits\n- Alex journals at night\n", encoding="utf8")
    conn = S.connect()
    a = add(conn, "Alex keeps a paper diary")
    P.plan(conn, {"steps": [{"op": "promote", "id": a, "section": "Habits", "text": "Keeps a paper diary"}]})
    P.apply(conn)

    async def go() -> tuple[dict, dict]:
        h = {"authorization": "Bearer test"}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.build_app()), base_url="http://127.0.0.1:8787") as client:
            forgot = (await client.post("/ui/api/action", json={"action": "forget", "id": a}, headers=h)).json()
            dropped = (await client.post("/ui/api/action", json={"action": "drop-profile", "id": a}, headers=h)).json()
        return forgot, dropped
    forgot, dropped = asyncio.run(go())
    assert forgot["ok"] and [x["text"] for x in forgot["profile"]] == ["Keeps a paper diary"]
    assert dropped["dropped"] == ["Keeps a paper diary"]
    assert "paper diary" not in S.profile_text() and "journals at night" in S.profile_text()
    json.loads((mousse_tree.home / "origins.json").read_text())  # 来源文件还是好的 JSON
