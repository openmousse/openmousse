"""内核第 4 项：备料（engine/context.py）、每天换新（engine/owner.py）、压缩（engine/compact.py）、会话从压缩条目重建（session.py）。
模型是假的：按剧本回放，报的用量按请求的大小估（压缩靠它判断）；命令行脚本换成假的跑法。"""
from __future__ import annotations

import asyncio
import json
import sqlite3

import pytest

import bridges
import config
from engine import compact, context, events, owner, provider_anthropic, providers
from engine.errors import EngineError
from engine.session import NO_RESULT, SUMMARY_PREFIX, DbSession, View, day_key, repair, text_of, user, with_time
from stored_values import ASSISTANT_ROLE
from test_engine import FakeModel

MODEL = "deepseek/deepseek-flash"


class SizedFake(FakeModel):
    """FakeModel，但用量按这次请求的大小报（开头 + 历史 + 回复），和真模型一样能拿来判断要不要压缩。"""

    async def stream(self, ref, req):
        async for ev in super().stream(ref, req):
            if ev["type"] == "done":
                size = compact.tokens(req.system) + sum(compact.msg_tokens(m) for m in req.messages)
                ev["message"]["usage"] = {"in": size, "out": compact.msg_tokens(ev["message"]), "cache_read": 0, "cache_write": 0}
            yield ev


@pytest.fixture
def fake(monkeypatch):
    def make(script):
        f = SizedFake(script)
        monkeypatch.setattr(providers, "stream", f.stream)
        return f
    return make


@pytest.fixture
def runner(monkeypatch):
    calls: list[tuple] = []

    async def fake_run(name, args, stdin, agent, *, kill_on_cancel=False):
        calls.append((name, list(args), stdin, agent))
        return f"ok: {name} {' '.join(args[:1])}"
    monkeypatch.setattr(bridges, "run", fake_run)
    return calls


@pytest.fixture
def home(tmp_path, monkeypatch, server_json):
    """一台假机器：主工作区、一个有工作区的 Agent（g-fit）、一个没有的（g-solo）、档案；server.json 选 builtin。"""
    main_ws, fit_ws = tmp_path / "ws-main", tmp_path / "ws-fit"
    for ws in (main_ws, fit_ws):
        (ws / "memory").mkdir(parents=True)
    (main_ws / "AGENTS.md").write_text("MAIN RULES: you are the front desk.", encoding="utf8")
    (main_ws / "MEMORY.md").write_text("MAIN MEMORY: likes long walks.", encoding="utf8")
    (fit_ws / "AGENTS.md").write_text("FIT RULES: plan training only.", encoding="utf8")
    (fit_ws / "SOUL.md").write_text("FIT SOUL: blunt coach.", encoding="utf8")
    (fit_ws / "IDENTITY.md").write_text("FIT IDENTITY: Coach.", encoding="utf8")
    (fit_ws / "MEMORY.md").write_text("FIT MEMORY: squat 100kg.", encoding="utf8")
    profile = tmp_path / "profile.md"
    profile.write_text("PROFILE: the user rows on Sundays.", encoding="utf8")
    monkeypatch.setattr(config.settings, "workspace", main_ws)
    monkeypatch.setattr(config.settings, "profile", profile)
    import sources
    monkeypatch.setattr(sources, "memory_tree", None)  # 世界树只在要它的测试里接
    cfg = {"claw": {"kind": "builtin"}, "engine": {"model": MODEL}, "agent_workspaces": {"g-fit": str(fit_ws)}}
    server_json(cfg)
    import chat
    import data
    with chat._lock, data.ddb() as conn:
        for gid, name, purpose in (("g-fit", "Fitness", "Training plans."), ("g-solo", "Solo", "Odd jobs.")):
            conn.execute("INSERT OR REPLACE INTO groups(id, name, icon, purpose, created_at) VALUES(?,?,?,?,?)", (gid, name, "x", purpose, chat.now_iso()))

    class Home:
        pass
    h = Home()
    h.main, h.fit, h.profile, h.cfg = main_ws, fit_ws, profile, cfg
    h.write = lambda extra: server_json({**cfg, **extra})
    return h


def run(coro):
    return asyncio.run(coro)


def say(thread: str, day: str, text: str, ref: int | None = None) -> str:
    out: list[str] = []
    return run(owner.turn(thread, day, text, None, out.append, ref=ref))


def session_of(thread: str, day: str) -> DbSession:
    s = DbSession.latest(day_key(thread, day))
    assert s is not None
    return s


@pytest.fixture
def lean(home, monkeypatch):
    """压缩的测试：开头小一点（只有 schedule 一个工具、没有技能目录），按第一轮实际的开头大小定阈值。
    → set(高出开头多少 token 就压, 留多少)"""
    from engine import tools as tools_mod
    monkeypatch.setattr(owner.tools_mod, "for_agent", lambda agent: [tools_mod.bridge_tool("schedule", agent or "main")])
    monkeypatch.setattr(owner.tools_mod, "chat_tools", lambda agent, watched=False: [])  # ⑫b 的 todo / 计划模式也不要
    monkeypatch.setattr(context, "skills_part", lambda agent, offered: context.Part("skills", "Skills"))

    def set_(above: int, keep: int, first) -> int:
        base = compact.tokens(first.system)
        home.write({"engine": {"model": MODEL, "compaction": {"threshold": base + above, "keep": keep}}})
        return base
    return set_


# —— 备料 ——————————————————————————————————————————————————————————————————

def test_prep_reads_the_agents_own_files_in_openclaw_order(home, fake, runner):
    f = fake([{"text": "hi"}])
    say("g-fit", "2026-10-10", "hello")
    system = f.requests[0][1].system
    assert 'You are the Agent "Fitness"' in system and "Training plans." in system and "Your Agent id is g-fit" in system
    order = [system.index(x) for x in ("FIT RULES", "FIT SOUL", "FIT IDENTITY", "PROFILE: the user rows", "FIT MEMORY")]
    assert order == sorted(order)  # AGENTS → SOUL → IDENTITY → USER（档案）→ MEMORY，和 OpenClaw 一样
    assert "MAIN MEMORY" not in system and "MAIN RULES" not in system  # 不拿主对话的
    assert "# Project Context" in system and "<available_skills>" in system and "Today is" in system
    assert system.index("Today is") < system.index("# Project Context")


def test_agent_without_a_workspace_gets_its_role_not_mains_files(home, fake, runner):
    f = fake([{"text": "hi"}])
    say("g-solo", "2026-10-10", "hello")
    system = f.requests[0][1].system
    assert 'You are the Agent "Solo"' in system and "Odd jobs." in system
    assert "MAIN MEMORY" not in system and "MAIN RULES" not in system
    assert "PROFILE: the user rows" in system  # 档案是你的，所有 Agent 都有


def test_long_files_are_clipped_head_and_tail(home, fake, runner):
    (home.fit / "MEMORY.md").write_text("HEAD-MARK " + "x" * 30_000 + " TAIL-MARK", encoding="utf8")
    home.profile.write_text("P-HEAD " + "y" * 9_000 + " P-TAIL", encoding="utf8")
    f = fake([{"text": "hi"}])
    say("g-fit", "2026-10-11", "hello")
    system = f.requests[0][1].system
    mem = system[system.index("## MEMORY.md"):]
    assert "HEAD-MARK" in mem and "TAIL-MARK" in mem and "truncated" in mem and len(mem) < context.FILE_MAX + 2_000
    prof = system[system.index("## USER.md (profile)"):system.index("## MEMORY.md")]
    assert "P-HEAD" in prof and "P-TAIL" in prof and len(prof) < context.PROFILE_MAX + 500
    assert context.clip("abc", 10, "x") == "abc"


def test_files_share_one_budget(home, monkeypatch):
    monkeypatch.setattr(context, "FILES_MAX", 1_000)
    (home.fit / "AGENTS.md").write_text("A" * 900, encoding="utf8")
    parts = {p.key: p for p in context.file_parts(home.fit)}
    assert len(parts["file:AGENTS.md"].text) == 900
    assert len(parts["file:SOUL.md"].text) < 200 or "left out" in parts["file:SOUL.md"].text
    assert "left out" in parts["file:MEMORY.md"].text


# —— 当天变了：往后补，不改开头 ———————————————————————————————————————————————————

def test_changes_are_appended_after_the_users_message(home, fake, runner):
    f = fake([{"text": "one"}, {"text": "two"}, {"text": "three"}, {"text": "again"}])
    day = "2026-10-12"
    say("g-fit", day, "first")
    (home.fit / "MEMORY.md").write_text("FIT MEMORY: squat 110kg now.", encoding="utf8")
    say("g-fit", day, "second")
    r1, r2 = f.requests[0][1], f.requests[1][1]
    assert r2.system == r1.system and "squat 110kg" not in r2.system  # 开头一个字没动
    last_two = r2.messages[-2:]
    assert [m["role"] for m in last_two] == ["user", "system"] and text_of(last_two[0]) == "second"
    assert "squat 110kg" in text_of(last_two[1]) and "MEMORY.md" in text_of(last_two[1]) and "FIT SOUL" not in text_of(last_two[1])
    say("g-fit", day, "third")  # 没再变：不补
    assert sum(1 for m in f.requests[2][1].messages if m["role"] == "system") == 1
    # 撤回第二句：补的那条也一起撤掉，指纹回到之前，下一句再补一次
    s = session_of("g-fit", day)
    second = next(r for r in s.path() if r["kind"] == "message" and r["role"] == "user" and "second" in r["data"])
    s.rewind(second["id"])
    say("g-fit", day, "second again")
    msgs = f.requests[3][1].messages
    assert [text_of(m) for m in msgs if m["role"] == "user"] == ["first", "second again"]
    assert "squat 110kg" in text_of(msgs[-1]) and msgs[-1]["role"] == "system"


def test_session_from_before_step_4_gets_the_whole_background_once(home, fake, runner):
    day = "2026-10-12"
    s = DbSession.create(day_key("g-fit", day), kind="owner", agent="g-fit", model=MODEL, system="old rules")  # 没有备料指纹
    f = fake([{"text": "a"}, {"text": "b"}])
    say("g-fit", day, "hi")
    say("g-fit", day, "and?")
    first, second = f.requests[0][1], f.requests[1][1]
    assert first.system == "old rules" and first.messages[-1]["role"] == "system" and "FIT RULES" in text_of(first.messages[-1])
    assert sum(1 for m in second.messages if m["role"] == "system") == 1
    assert DbSession.get(s.id).system == "old rules"


def test_project_card_changed_by_the_project_itself_is_not_repeated():
    ctx = context.Context({"project": context.Part("project", "Project card", "card v2", fp={"rev": 2}, own=True)})
    note, state = context.changes({"project": {"rev": 1}}, ctx)
    assert note is None and state == {"project": {"rev": 2}}  # 只换指纹
    ctx.parts["project"].own = False
    note, state = context.changes({"project": {"rev": 1}}, ctx)
    assert "card v2" in note and "Project card (updated)" in note


class FakeTree:
    """假的世界树：sqlite 里的 tree / branch 两张表（列和 memory_tree.py 的索引一样）。"""
    TRUNK = "档案"
    HAS_BRANCHES = True

    def __init__(self, path):
        self.path = path
        with sqlite3.connect(path) as conn:
            conn.execute("CREATE TABLE tree (id TEXT PRIMARY KEY, text TEXT, kind TEXT, source TEXT, status TEXT, branch TEXT, tags TEXT, "
                         "observed_at TEXT, created_at TEXT)")
            conn.execute("CREATE TABLE branch (name TEXT PRIMARY KEY, parent TEXT, agents TEXT, about TEXT)")
            conn.executemany("INSERT INTO branch VALUES(?,?,?,?)", [("身体", "档案", "", ""), ("健身", "身体", "g-fit", ""),
                                                                     ("力量", "健身", "", ""), ("饮食", "身体", "diet", "")])

    def add(self, mid, text, branch, day="2026-10-01", status="active"):
        with sqlite3.connect(self.path) as conn:
            conn.execute("INSERT OR REPLACE INTO tree VALUES(?,?,?,?,?,?,?,?,?)", (mid, text, "fact", "claude", status, branch, "", day, day))

    def connect(self, sync=True):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def branch_tree(self, conn):
        return [{**dict(r), "depth": 1, "leaves": 0} for r in conn.execute("SELECT name, parent, agents, about FROM branch")]


def test_tree_gives_each_agent_its_branches_and_appends_new_leaves(home, fake, runner, monkeypatch, tmp_path):
    import sources
    tree = FakeTree(tmp_path / "tree.db")
    tree.add("m-1", "Deadlift PR 140kg", "健身")
    tree.add("m-2", "Bench form cue: elbows in", "力量", status="pending")
    tree.add("m-3", "Eats oats every morning", "饮食")
    tree.add("m-4", "Lives in Lisbon", "档案")
    monkeypatch.setattr(sources, "memory_tree", tree)
    f = fake([{"text": "a"}, {"text": "b"}, {"text": "c"}])
    day = "2026-10-13"
    say("g-fit", day, "hi")
    system = f.requests[0][1].system
    assert "[m-1] Deadlift PR 140kg" in system and "[m-2] Bench form cue" in system and "not confirmed yet" in system
    assert "oats" not in system and "Lives in Lisbon" not in system  # 别的枝不给
    say("t-ctx-main", day, "hi")  # 主对话看全部
    assert all(x in f.requests[1][1].system for x in ("Deadlift", "oats", "Lives in Lisbon"))
    tree.add("m-5", "Squat goal 120kg by December", "健身", day="2026-10-13")
    say("g-fit", day, "anything new?")
    note = f.requests[2][1].messages[-1]
    assert note["role"] == "system" and "[m-5] Squat goal" in text_of(note) and "Deadlift" not in text_of(note)


# —— 每天换新 ——————————————————————————————————————————————————————————————————

def test_new_day_brings_yesterdays_digest_and_where_it_ended(home, fake, runner):
    import chat
    thread = "g-fit"
    with chat._lock, chat.db() as conn:
        for role, text, status in (("user", "Plan my week please", "ok"), (ASSISTANT_ROLE, "Mon push, Wed pull, Fri legs.", "ok"),
                                   ("auto", context.AUTO_TRIGGER + "日结", "ok"), (ASSISTANT_ROLE, "digest reply", "ok"),
                                   ("user", "Move Friday to Saturday?", "ok")):
            conn.execute("INSERT INTO messages(thread, role, text, ts, status) VALUES(?,?,?,?,?)", (thread, role, text, "2026-10-14T20:00:00+00:00",
                                                                                                status))
    chat.save_digest(thread, "2026-10-14", "DIGEST: planned the week; Friday may move.")
    (home.fit / "memory" / "2026-10-14.md").write_text("NOTE-14: knee felt fine.", encoding="utf8")
    f = fake([{"text": "Sure, Saturday works."}])
    say(thread, "2026-10-15", "good morning")
    system = f.requests[0][1].system
    assert "Last daily digest (2026-10-14)" in system and "DIGEST: planned the week" in system
    assert "Where the last conversation ended (2026-10-14)" in system
    assert "User: Plan my week please" in system and "Move Friday to Saturday?" in system and "never got a reply" in system
    assert "日结" not in system.split("Where the last conversation ended")[1].split("##")[0]  # 定时脚本那一轮跳过
    assert "NOTE-14: knee felt fine." in system  # 昨天的日记


def test_moving_time_zones_keeps_where_it_ended_and_todays_messages(home, fake, runner, monkeypatch):
    """冒烟时的样子：在里斯本说了几句 → server.json 改成东八区、重启 → 再说一句。会话开头的「上次聊到哪」是上海的昨天那段（不是刚发的
    这句、也不说它没被回）；上海的今天从里斯本 21:00 起，那之后在里斯本说的几句抄进今天的会话（见 chat.day_bounds）。"""
    from zoneinfo import ZoneInfo

    import chat
    thread = "tz-smoke"
    for tz in (ZoneInfo("Europe/Lisbon"), ZoneInfo("Asia/Shanghai")):
        monkeypatch.setattr(chat, "TZ", tz)
        monkeypatch.setattr(config.settings, "tz", tz)
        with chat._lock, chat.db() as conn:
            rows = ([("user", "Plan my week please", "2026-10-16T20:30:00+01:00"), (ASSISTANT_ROLE, "Mon push, Wed pull.", "2026-10-16T20:31:00+01:00"),
                     ("user", "And Friday?", "2026-10-16T21:40:00+01:00"), (ASSISTANT_ROLE, "Friday legs.", "2026-10-16T21:41:00+01:00")]
                    if tz.key == "Europe/Lisbon" else [("user", "Friday again?", "2026-10-17T04:45:00+08:00")])
            ids = [conn.execute("INSERT INTO messages(thread, role, text, ts, status) VALUES(?,?,?,?,'ok')", (thread, *r)).lastrowid for r in rows]
    f = fake([{"text": "Still legs."}])
    say(thread, "2026-10-17", "Friday again?", ref=ids[-1])
    req = f.requests[0][1]
    tail = req.system.split("## Where the last conversation ended")[1].split("##")[0]
    assert tail.startswith(" (2026-10-16)") and "User: Plan my week please" in tail and "You: Mon push, Wed pull." in tail
    assert "Friday again?" not in tail and "never got a reply" not in tail
    assert [text_of(m) for m in req.messages] == ["And Friday?", "Friday legs.", "Friday again?"]


def test_a_turn_running_across_4am_finishes_before_the_new_day_starts(home, monkeypatch, runner):
    order: list[str] = []
    talking: list[asyncio.Event] = []

    async def stream(ref, req):
        first = req.messages[-1]
        order.append(f"request:{text_of(first)}")
        if text_of(first) == "late question":
            talking[0].set()
            for chunk in ("still ", "answering"):
                await asyncio.sleep(0.15)
                order.append(f"chunk:{chunk}")
                yield {"type": "text", "text": chunk}
            msg_text = "still answering"
        else:
            msg_text = "morning!"
            yield {"type": "text", "text": msg_text}
        yield {"type": "done", "message": {"role": "assistant", "content": [{"type": "text", "text": msg_text}], "api": "openai",
                                           "provider": "deepseek", "model": "deepseek-flash", "stop": "end", "usage": {}, "raw": {"content": ""}}}
    monkeypatch.setattr(providers, "stream", stream)

    async def go():
        out: list[str] = []
        talking.append(asyncio.Event())
        late = asyncio.create_task(owner.turn("g-solo", "2026-10-16", "late question", None, out.append))
        await asyncio.wait_for(talking[0].wait(), 10)  # 03:59 开始的那一轮已经在说了（等它真开口：备料在忙的机器上要一两百毫秒，固定睡 0.05 秒会抢在它前面）
        await owner.turn("g-solo", "2026-10-17", "early question", None, out.append)  # 04:01 新的一天
        await late
    run(go())
    assert order == ["request:late question", "chunk:still ", "chunk:answering", "request:early question"]
    assert [text_of(m) for m in session_of("g-solo", "2026-10-16").messages()] == ["late question", "still answering"]


# —— 压缩 ——————————————————————————————————————————————————————————————————

def summary_reply(text: str) -> dict:
    return {"text": f"<summary>\n{text}\n</summary>"}


def long(word: str, n: int = 900) -> str:
    return (word + " ") * (n // (len(word) + 1))


def test_compaction_cuts_at_a_turn_start_keeps_tool_pairs_and_logs_a_step(lean, fake, runner):
    day, thread = "2026-10-18", "t-compact"
    f = fake([{"text": long("alpha"), "calls": [("c1", "schedule", {"args": ["day"]})]}, {"text": long("after-tool")},
              {"text": long("bravo")},
              {"text": long("charlie")},
              summary_reply("S1: alpha bravo charlie happened. Open request: none"), {"text": "fresh answer"}])
    say(thread, day, "q1 " + long("one", 600))
    lean(1280, 300, f.requests[0][1])  # 每轮约 380–630 token：第四轮开头超过「开头 + 1280」，留最近约 300 token（第三轮）
    for q in ("q2 " + long("two", 600), "q3 " + long("three", 600), "q4 short"):
        say(thread, day, q)
    summarize_req = next(r for _, r in f.requests if r.system == compact.SUMMARIZER)
    assert summarize_req.tools == [] and "<conversation>" in text_of(summarize_req.messages[0])
    assert "[Assistant tool calls]: schedule(" in text_of(summarize_req.messages[0])
    final = f.requests[-1][1]
    first = final.messages[0]
    assert first["role"] == "user" and text_of(first).startswith(SUMMARY_PREFIX) and "S1: alpha bravo" in text_of(first)
    assert text_of(final.messages[-1]) == "q4 short"
    s = session_of(thread, day)
    path = s.path()
    comp = next(r for r in path if r["kind"] == "compaction")
    data = json.loads(comp["data"])
    assert data["reason"] == "threshold" and data["summary"].startswith("S1") and data["system"] and data["ctx"]
    kept = next(r for r in path if r["id"] == data["first_kept"])
    assert kept["role"] == "user" and kept["kind"] == "message" and '"q3 three' in kept["data"]  # 只在一轮的开头切
    assert [text_of(m)[:2] for m in final.messages[1:]] == ["q3", "ch", "q4"]
    view = s.view()
    # 工具调用和结果没拆开：repair 除了在用户的话前面加上当时的时间（with_time），什么都不用补
    assert repair(view.messages) == [with_time(m) for m in view.messages] and not any(NO_RESULT in text_of(m) for m in repair(view.messages))
    steps = [st for t in events.turns(thread) for st in events.steps(t["id"]) if st["type"] == "compact"]
    assert len(steps) == 1 and steps[0]["status"] == "ok" and steps[0]["data"]["before"] > steps[0]["data"]["after"]


def test_turn_starts_ignore_steers_and_keep_zero_compacts_everything(home):
    s = DbSession.create("test:cuts", kind="owner")
    s.append(user("u1"), turn="t1")
    s.append({"role": "assistant", "content": [{"type": "text", "text": long("a", 400)}]}, turn="t1")
    s.append(user("steer"), turn="t1")  # 同一轮里插的话：不是一轮的开头
    s.append({"role": "assistant", "content": [{"type": "text", "text": "ok"}]}, turn="t1")
    s.append(user("u2"), turn="t2")
    s.append({"role": "assistant", "content": [{"type": "text", "text": long("b", 400)}]}, turn="t2")
    view = s.view()
    assert compact.turn_starts(view.rows) == [0, 4]
    assert compact.cut_for(view, 0) == 6  # 一条不留：整段压（simple compaction）
    assert compact.cut_for(view, 50) == 6 or compact.cut_for(view, 50) == 4
    assert compact.cut_for(view, 10_000) is None  # 攒不到要留的量：没东西可压
    assert compact.cut_at_turn(view, "t2") == 4 and compact.cut_at_turn(view, "t1") is None


def test_after_compaction_claude_thinking_is_stripped_from_kept_turns_only():
    def claude(text: str, sig: str) -> dict:
        raw = [{"type": "thinking", "thinking": "hmm " + text, "signature": sig}, {"type": "text", "text": text}]
        return {"role": "assistant", "content": [{"type": "thinking", "text": "hmm " + text, "sig": sig}, {"type": "text", "text": text}],
                "api": "anthropic", "provider": "anthropic", "model": "claude-opus-5-5", "stop": "end", "usage": {}, "raw": raw}
    s = DbSession.create("test:strip", kind="owner")
    s.append(user("one"), turn="t1")
    s.append(claude("first", "sig-1"), turn="t1")
    keep = s.append(user("two"), turn="t2")
    s.append(claude("second", "sig-2"), turn="t2")
    s.append({"summary": "S", "first_kept": keep, "system": "rewritten rules", "ctx": {}}, turn="t3", kind="compaction")
    s.append(user("three"), turn="t3")
    s.append(claude("third", "sig-3"), turn="t3")
    view = s.view()
    assert view.system == "rewritten rules" and s.effective_system() == "rewritten rules" and view.fresh == 2
    wire = provider_anthropic.to_anthropic(view.messages, {"mid_system": True})
    assert wire[0]["role"] == "user" and "<summary>\nS\n</summary>" in wire[0]["content"][0]["text"]
    blocks = [(m["role"], b.get("type"), b.get("signature")) for m in wire for b in m["content"]]
    assert ("assistant", "thinking", "sig-2") not in blocks  # 保留的那一轮：思考去掉（前缀变了）
    assert ("assistant", "thinking", "sig-3") in blocks      # 压缩以后才有的：原样发回去
    assert ("assistant", "text", None) in blocks and not any(b == ("assistant", "thinking", "sig-1") for b in blocks)
    assert [text_of(m) for m in view.messages[1:]] == ["two", "second", "three", "third"]


def test_second_compaction_updates_the_previous_summary(lean, fake, runner):
    day, thread = "2026-10-19", "t-compact2"
    f = fake([{"text": long("a")}, {"text": long("b")}, summary_reply("S1 first part"), {"text": long("c")},
              summary_reply("S2 both parts"), {"text": "done"}])
    say(thread, day, "q1 " + long("x", 500))
    lean(700, 300, f.requests[0][1])  # 每轮约 360 token：第三、第四轮开头各压一次
    for q in ("q2 " + long("y", 500), "q3 " + long("z", 500), "q4 " + long("w", 500)):
        say(thread, day, q)
    summaries = [r for _, r in f.requests if r.system == compact.SUMMARIZER]
    assert len(summaries) == 2
    assert "<previous-summary>\nS1 first part\n</previous-summary>" in text_of(summaries[1].messages[0])
    assert "S2 both parts" in text_of(f.requests[-1][1].messages[0])
    comps = [json.loads(r["data"]) for r in session_of(thread, day).path() if r["kind"] == "compaction"]
    assert [c["summary"] for c in comps] == ["S1 first part", "S2 both parts"]


def test_context_overflow_compacts_once_and_retries(home, fake, runner):
    day, thread = "2026-10-20", "t-overflow"
    f = fake([{"text": long("first", 1200)},
              EngineError("Claude HTTP 400: prompt is too long: 1200000 tokens > 1000000 maximum", status=400),
              summary_reply("S: the first exchange"),
              {"text": "answered after compacting"}])
    say(thread, day, "q1 " + long("x", 1200))
    assert say(thread, day, "q2") == "answered after compacting"
    s = session_of(thread, day)
    comp = json.loads(next(r for r in s.path() if r["kind"] == "compaction")["data"])
    second = next(r for r in s.path() if r["kind"] == "message" and r["role"] == "user" and '"q2"' in r["data"])
    assert comp["reason"] == "overflow" and comp["first_kept"] == second["id"]  # 这一轮留着，前面的压掉
    retry = f.requests[-1][1]
    assert text_of(retry.messages[0]).startswith(SUMMARY_PREFIX) and text_of(retry.messages[-1]) == "q2"


def test_other_errors_and_a_second_overflow_are_not_retried(home, fake, runner):
    day, thread = "2026-10-21", "t-overflow2"
    fake([{"text": "hi"}, EngineError("deepseek HTTP 429: rate limit", status=429)])
    say(thread, day, "q1")
    with pytest.raises(EngineError):
        say(thread, day, "q2")
    assert not any(r["kind"] == "compaction" for r in session_of(thread, day).path())
    assert compact.is_overflow(EngineError("x HTTP 400: maximum context length is 131072 tokens", status=400))
    assert not compact.is_overflow(EngineError("x HTTP 400: Too many tokens per minute, rate limit", status=400))
    assert not compact.is_overflow(EngineError("x HTTP 500: prompt is too long", status=500))


def test_failed_summary_leaves_the_session_alone_and_cools_down(lean, fake, runner):
    day, thread = "2026-10-22", "t-fail"
    f = fake([{"text": long("a")}, EngineError("deepseek HTTP 500: boom", status=500), {"text": "still fine"}, {"text": "and again"}])
    say(thread, day, "q1 " + long("x"))
    lean(400, 100, f.requests[0][1])
    for q in ("q2 " + long("y"), "q3"):
        say(thread, day, q)
    s = session_of(thread, day)
    assert not any(r["kind"] == "compaction" for r in s.path())
    assert sum(1 for _, r in f.requests if r.system == compact.SUMMARIZER) == 1  # 冷却：q3 不再试
    steps = [st for t in events.turns(thread) for st in events.steps(t["id"]) if st["type"] == "compact"]
    assert [st["status"] for st in steps] == ["error"]


def test_ineffective_compaction_waits_for_the_conversation_to_grow(lean, fake, runner):
    day, thread = "2026-10-23", "t-thrash"
    f = fake([{"text": long("a")}, summary_reply("S1"), {"text": long("b")}, {"text": long("c")}, {"text": long("d")}])
    say(thread, day, "q1 " + long("x", 500))
    lean(100, 0, f.requests[0][1])  # 开头本身就快占满阈值：压完还是超
    for q in ("q2 " + long("y", 500), "q3 " + long("z", 500), "q4 " + long("w", 500)):
        say(thread, day, q)
    assert sum(1 for _, r in f.requests if r.system == compact.SUMMARIZER) == 1  # 压过一次没用：不每一轮都再压
    comp = json.loads(next(r for r in session_of(thread, day).path() if r["kind"] == "compaction")["data"])
    assert comp["after"] > 0 and comp["kept"] == 0


def test_summary_budget_and_prompt():
    assert compact.budget(["short"], None) == compact.SUMMARY_BUDGET[0]
    assert compact.budget(["x" * 400_000], None) == compact.SUMMARY_BUDGET[1]
    assert compact.budget(["x" * 40_000], "y" * 4_000) == (10_000 + 1_000) // 4
    p = compact.prompt_for(["[User]: hi"], "old summary", True, "2026-10-16")
    assert p.index("<conversation>") < p.index("<previous-summary>\nold summary") < p.index("Summarize the transcript")
    assert compact.OMITTED in p and "Today is 2026-10-16" in p and "[REDACTED]" in p and "within about 1500 tokens" in p


def test_settings_and_estimates():
    assert compact.window("anthropic/claude-opus-5-5") == 1_000_000 and compact.window("anthropic/claude-haiku-4-5") == 200_000
    assert compact.window("deepseek/deepseek-flash") == 1_000_000 and compact.window("moonshot/kimi-k3") == compact.DEFAULT_WINDOW
    assert compact.threshold("deepseek/deepseek-flash") == 1_000_000 - compact.RESERVE and compact.keep("deepseek/deepseek-flash") == compact.KEEP
    assert compact.tokens("abcd" * 10) == 10 and compact.tokens("你好世界") == 4
    view = View(messages=[user("x" * 400)], rows=[({"turn": None}, user("x" * 400))])
    assert compact.estimate(view, "s" * 40, [], [user("y" * 40)]) == 10 + 104 + 14
    used = {"role": "assistant", "content": [{"type": "text", "text": "ok"}], "provider": "deepseek", "stop": "end",
            "usage": {"in": 500, "out": 20, "cache_read": 1000, "cache_write": 0}}
    view = View(messages=[user("q"), used, user("z" * 40)], rows=[({"turn": "a"}, user("q")), ({"turn": "a"}, used), ({"turn": "b"}, user("z" * 40))])
    assert compact.estimate(view, "ignored", [], []) == 1520 + 14  # 最近一次的用量 + 之后新加的
    assert compact.extract("blah <summary>\n S \n</summary> tail") == "S" and compact.extract("plain") == "plain"
