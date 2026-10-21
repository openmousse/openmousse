"""一句话新建（主线第三步第 5 件，agentnew.py）：模板、补问、草稿、试聊都是零工具的一问；功能 Agent 照草稿建好，提醒先停着等你点「开」。
不连真模型、不花钱：模型是假的（test_engine.FakeModel 按剧本回放），密钥是假的；名字、人和事都是编的。"""
from __future__ import annotations

import json

import chat
import cybers
from test_cybers import BASE, H, agent, env, fake, groups_clean, make  # noqa: F401 — 同一套 server.json、假模型、功能 Agent


def reply(v) -> dict:
    return {"text": json.dumps(v, ensure_ascii=False)}


def asked(f) -> list:
    """每一次调模型：(模型, 有没有工具, 发过去的全部文字)。"""
    return [(ref, req.tools, req.system + json.dumps(req.messages, ensure_ascii=False)) for ref, req in f.requests]


def test_new_is_behind_the_cybers_switch_and_lists_three_classes(env):  # noqa: F811 — env、fake、groups_clean 是导入的 fixture
    env.cfg(cybers=False)
    assert env.client.get("/api/new/templates", headers=H).status_code == 404
    assert env.client.post("/api/new/draft", json={"kind": "agent", "text": "Log my runs"}, headers=H).status_code == 404
    env.cfg()
    t = env.client.get("/api/new/templates", headers=H).json()
    assert [g["class"] for g in t["groups"]] == ["pro", "friend", "machine"] and t["blank"]["id"] == "blank"
    ids = {g["class"]: [x["id"] for x in g["templates"]] for g in t["groups"]}
    assert ids == {"pro": ["rachel", "victor", "socrates", "mentor"], "friend": ["kobayashi", "zhe", "sam"],
                   "machine": ["quizzer", "contrarian", "retro"]}
    machines = {x["id"]: x["avatar"] for x in t["groups"][2]["templates"]}
    assert {k: v["machine"] for k, v in machines.items()} == {"quizzer": "timer", "contrarian": "sides", "retro": "rewind"}
    rachel = t["groups"][0]["templates"][0]
    assert rachel["name"] == "Rachel" and rachel["tag"] == "Case interviewer" and "hair" in rachel["avatar"]
    zh = env.client.get("/api/new/templates", headers={**H, "Accept-Language": "zh-CN"}).json()
    assert zh["groups"][0]["title"] == "专业" and zh["groups"][1]["templates"][1]["name"] == "阿哲"


def test_questions_are_a_zero_tool_owner_call_and_fall_back_to_fixed_ones(env, fake):  # noqa: F811 — env、fake、groups_clean 是导入的 fixture
    f = fake([reply({"questions": [{"q": "Which kind of firm?", "options": ["Strategy", "Big Four", "Tech", "All of these"]},
                                   {"q": "How strict?", "options": ["Gentle", "Normal", "Strict"]},
                                   {"q": "One option only", "options": ["Yes"]}]})])
    r = env.client.post("/api/new/questions", json={"kind": "cyber", "text": "Someone to practise case interviews with"}, headers=H).json()
    assert r["fallback"] is False and [q["id"] for q in r["questions"]] == ["q1", "q2"]  # 只有一个选项的那道不要
    assert r["questions"][0]["options"] == ["Strategy", "Big Four", "Tech", "All of these"]
    (ref, tools, sent), = asked(f)
    assert tools == [] and "multiple-choice questions" in sent and "practise case interviews" in sent
    fake([{"text": "Sure! Here are some questions."}, {"text": "still not JSON"}])
    r = env.client.post("/api/new/questions", json={"kind": "agent", "text": "Log my runs"}, headers=H).json()
    assert r["fallback"] is True and [q["id"] for q in r["questions"]] == ["board", "remind"]
    assert env.client.post("/api/new/questions", json={"kind": "cyber"}, headers=H).status_code == 400  # 没描述也没模板
    assert env.client.post("/api/new/questions", json={"kind": "robot", "text": "x"}, headers=H).status_code == 400


def test_a_cyber_draft_fills_everything_and_suggests_only_agents_it_may_load(env, fake, groups_clean):  # noqa: F811 — env、fake、groups_clean 是导入的 fixture
    make(env, "Rachel")
    notes = agent(env, "Job search", icon="briefcase", purpose="Tracks applications and job posts")
    sleep = agent(env, "Bedtime", icon="moon", purpose="Interprets last night's sleep")
    f = fake([reply({"name": "Rachel", "tag": "Case interviewer", "class": "pro", "identity": "A former strategy consultant.",
                     "tone": "Crisp.", "rules": "One timed case at a time.", "loadout": [{"id": notes, "why": "checks job posts"},
                                                                                          {"id": sleep, "why": "sleep"}, {"id": "g-nope"}]})])
    r = env.client.post("/api/new/draft", json={"kind": "cyber", "text": "A case interviewer who times me",
                                                "answers": [{"q": "How strict?", "a": "Strict"}]}, headers=H).json()
    d = r["draft"]
    assert r["fallback"] is False and d["name"] == "Rachel 2" and d["class"] == "pro" and d["tag"] == "Case interviewer"  # 重名加个数字
    assert d["identity"] == "A former strategy consultant." and d["rules"] == "One timed case at a time."
    assert [a["id"] for a in d["loadout"]] == [notes] and d["loadout"][0]["why"] == "checks job posts"  # 健康类的、没有的都不要
    assert sleep not in [a["id"] for a in d["agents"]] and notes in [a["id"] for a in d["agents"]]
    assert len({p["bg"] for p in d["presets"]}) == 3 and d["avatar"] == d["presets"][0]
    assert d["model"] == "moonshot/kimi-k2.6" and d["modelName"] == "Kimi K2.6" and "capable open-weight" in d["modelReason"]  # 专业的要强一些
    (ref, tools, sent), = asked(f)
    assert tools == [] and "Draft a Cyber" in sent and "Strict" in sent and "Interprets last night" not in sent  # 健康类的不给模型挑
    made = env.client.post("/api/cybers", json={k: d[k] for k in ("name", "tag", "identity", "tone", "rules", "avatar", "model")}
                           | {"kind": d["class"], "template": d["template"], "loadout": [a["id"] for a in d["loadout"]]}, headers=H).json()["cyber"]
    assert made["loadout"] == [notes] and made["name"] == "Rachel 2"
    bad = env.client.post("/api/cybers", json={"name": "Nora", "loadout": [sleep]}, headers=H)
    assert bad.status_code == 400 and "health" in bad.json()["detail"]
    with chat._lock, cybers.cydb() as conn:
        assert not any(x["name"] == "Nora" for x in cybers.active_rows(conn))  # 不行就什么都不建


def test_template_and_blank_drafts_need_no_model_call(env, fake):  # noqa: F811 — env、fake、groups_clean 是导入的 fixture
    f = fake([])
    d = env.client.post("/api/new/draft", json={"kind": "cyber", "template": "contrarian"}, headers=H).json()["draft"]
    assert d["name"] == "Contrarian" and d["class"] == "machine" and d["template"] == "contrarian" and d["avatar"]["machine"] == "sides"
    assert [p["machine"] for p in d["presets"]] == ["sides", "timer", "rewind"] and d["model"] == "deepseek/deepseek-flash"  # 机器类要便宜、快
    b = env.client.post("/api/new/draft", json={"kind": "cyber", "template": "blank"}, headers=H).json()["draft"]
    assert b["name"] == "" and b["class"] == "blank" and b["identity"] == "" and len(b["presets"]) == 3
    assert f.requests == []
    assert env.client.post("/api/new/draft", json={"kind": "cyber", "template": "nobody"}, headers=H).status_code == 400


def test_a_cyber_draft_falls_back_to_the_description_and_answers(env, fake):  # noqa: F811 — env、fake、groups_clean 是导入的 fixture
    fake([{"text": "I'd love to help!"}, {"text": "no JSON here either"}])
    r = env.client.post("/api/new/draft", json={"kind": "cyber", "text": "Someone who listens when work gets loud",
                                                "answers": [{"q": "How strict?", "a": "Gentle"}, {"q": "Language?", "a": "English"}]}, headers=H).json()
    d = r["draft"]
    assert r["fallback"] is True and d["name"] == "New Cyber" and d["identity"] == "Someone who listens when work gets loud"
    assert d["tone"] == "Gentle, English" and d["class"] == "blank" and d["model"] == "deepseek/deepseek-flash"


def test_an_agent_draft_builds_a_board_that_renders_and_reminders_that_parse(env, fake, groups_clean):  # noqa: F811 — env、fake、groups_clean 是导入的 fixture
    f = fake([reply({"name": "Running", "icon": "dumbbell", "purpose": "Logs my runs and keeps an eye on the weekly distance.",
                     "table": {"name": "Runs", "title": "Runs", "fields": [
                         {"key": "route", "label": "Route", "type": "text"}, {"key": "Distance (km)", "label": "Distance", "type": "number", "unit": "km"},
                         {"key": "date", "label": "Date", "type": "date"}, {"key": "feel", "label": "Feel", "type": "choice", "options": ["Easy", "Hard"]},
                         {"key": "route", "label": "Again", "type": "text"}, {"key": "x", "label": "Odd", "type": "weird"}]},
                     "board": [{"kind": "recent", "title": "Latest runs"}, {"kind": "week"}, {"kind": "todo"}, {"kind": "add"}, {"kind": "chart"}],
                     "reminders": [{"name": "Run reminder", "when": "every day 07:00", "task": "Remind me to go for a run."},
                                   {"name": "Odd", "when": "whenever you like", "task": "x"}]})])
    d = env.client.post("/api/new/draft", json={"kind": "agent", "text": "Track my running",
                                                "answers": [{"q": "Reminders?", "a": "Daily"}]}, headers=H).json()["draft"]
    assert d["name"] == "Running" and d["icon"] == "dumbbell" and d["model"] == "deepseek/deepseek-flash"  # 跟主对话一样
    assert d["table"]["name"] == "runs" and [x["key"] for x in d["table"]["fields"]] == ["route", "distance_km", "date", "feel", "x"]
    assert d["table"]["fields"][4]["type"] == "text"  # 不认识的类型当文字
    assert [b["id"] for b in d["blocks"]] == ["recent", "week", "add"]  # 没有 bool 字段的待办、不认识的种类都跳过
    assert d["board"] == [{"kind": "recent", "title": "Latest runs"}, {"kind": "week", "title": "This week"}, {"kind": "add", "title": "Add one"}]
    assert [b["id"] for b in d["preview"]["blocks"]] == ["recent", "week", "add"] and d["preview"]["plan"] is True  # 先画一遍给你看
    assert d["reminders"] == [{"name": "Run reminder", "when": "every day 07:00", "task": "Remind me to go for a run.", "describe": "every day 07:00"}]
    assert d["does"][0] == "Board: Latest runs, This week, Add one" and d["does"][2].endswith("(off until you turn them on)")
    (ref, tools, sent), = asked(f)
    assert tools == [] and "Draft a functional Agent" in sent and "lightbulb" in sent  # 24 个图标给模型挑


def test_creating_the_agent_sets_up_its_table_and_board_and_leaves_reminders_off(env, groups_clean):  # noqa: F811 — env、fake、groups_clean 是导入的 fixture
    from engine import jobs
    body = {"name": "Running", "icon": "dumbbell", "purpose": "Logs my runs.",
            "table": {"name": "runs", "title": "Runs", "fields": [{"key": "route", "label": "Route", "type": "text"},
                                                                 {"key": "km", "label": "Distance", "type": "number"},
                                                                 {"key": "date", "label": "Date", "type": "date"}]},
            "board": [{"kind": "recent", "title": "Latest runs"}, {"kind": "week"}, {"kind": "add"}],
            "reminders": [{"name": "Run reminder", "when": "every day 07:00", "task": "Remind me to go for a run."}]}
    bad = env.client.post("/api/new/agent", json={**body, "reminders": [{"name": "x", "when": "in 2 hours", "task": "y"}]}, headers=H)
    assert bad.status_code == 400  # 一次性的不算提醒：先全部校验，什么都不建
    assert env.client.get("/api/groups", headers=H).json()["groups"] == []
    r = env.client.post("/api/new/agent", json=body, headers=H).json()
    gid = r["id"]
    assert r["blocks"] == 3 and r["problems"] == [] and len(r["reminders"]) == 1
    g = next(x for x in env.client.get("/api/groups", headers=H).json()["groups"] if x["id"] == gid)
    assert g["name"] == "Running" and g["icon"] == "dumbbell"
    colls = env.client.get(f"/api/collections/{gid}", headers=H).json()["collections"]
    assert [c["name"] for c in colls] == ["runs"]
    board = env.client.get(f"/api/boards/{gid}", headers=H).json()
    assert [b["id"] for b in board["blocks"]] == ["recent", "week", "add"]
    job = jobs.get(r["reminders"][0]["job"])
    assert job["state"] == "draft" and job["agent"] == gid  # 先停着，你点「开」才开
    cards = env.client.get(f"/api/inbox?thread={gid}", headers=H).json()["items"]
    assert [c["kind"] for c in cards] == ["schedule"] and cards[0]["id"] == r["reminders"][0]["inbox"]
    assert env.client.post("/api/new/agent", json=body, headers=H).status_code == 409  # 重名


def test_trial_uses_the_cybers_own_model_with_no_tools(env, fake):  # noqa: F811 — env、fake、groups_clean 是导入的 fixture
    persona = {"name": "Victor", "identity": "An early-stage investor.", "tone": "Direct.", "rules": "Asks for numbers."}
    f = fake([reply({"line": "What's your 4-week retention?", "tone": "Direct.", "rules": "Asks for numbers."}),
              reply({"line": "Numbers first. What's retention?", "tone": "Blunt and demanding.", "rules": "Asks for numbers and calls out weak ones."})])
    r = env.client.post("/api/new/trial", json={**persona, "model": "moonshot/kimi-k3"}, headers=H).json()
    assert r["line"] == "What's your 4-week retention?" and r["model"] == "moonshot/kimi-k3" and r["tone"] == "Direct."
    r2 = env.client.post("/api/new/trial", json={**persona, "model": "moonshot/kimi-k3", "tweak": "stricter",
                                                 "previous": [r["line"]]}, headers=H).json()
    assert r2["tone"] == "Blunt and demanding." and r2["rules"] == "Asks for numbers and calls out weak ones." and r2["tweak"] == "stricter"
    (ref1, tools1, sent1), (ref2, tools2, sent2) = asked(f)
    assert ref1 == ref2 == "moonshot/kimi-k3" and tools1 == tools2 == []
    assert "stricter" in sent2 and "What's your 4-week retention?" in sent2 and "stricter" not in sent1  # 「换一句」不重样
    assert env.client.post("/api/new/trial", json={**persona, "tweak": "louder"}, headers=H).status_code == 400
    plan = env.client.post("/api/new/trial", json={**persona, "model": "claude-code/opus"}, headers=H)
    assert plan.status_code == 400 and "plan" in plan.json()["detail"]
    fake([{"text": "Hello!"}, {"text": "Hello again!"}])
    assert env.client.post("/api/new/trial", json=persona, headers=H).status_code == 502  # 回的不是 JSON：app 里「换一句」重试


def test_trial_never_spends_the_owners_plan(env, fake):  # noqa: F811 — env、fake、groups_clean 是导入的 fixture
    env.cfg(engine={**BASE["engine"], "use": {"cyber": "claude-code/opus", "json": True}})  # 赛博的默认是用户自己的 Claude Code
    f = fake([reply({"line": "Hi.", "tone": "", "rules": ""})])
    r = env.client.post("/api/new/trial", json={"name": "Kai", "identity": "A code advisor."}, headers=H).json()
    assert r["model"] == "deepseek/deepseek-flash" and f.requests[0][0] == "deepseek/deepseek-flash"
