"""10/19 彩排补的第 3 件：每个 Agent 能用哪些技能（engine/agent_skills.py，server.json 的 engine.skills.agents.<Agent id>）。
名单管住三处：会话开头的技能目录、skill 工具、经 /mcp 给外援的技能。用的是仓库里 packs/core/skills 的真技能，模型是假的。"""
from __future__ import annotations

import asyncio

import pytest

from engine import agent_skills, context, mcp_engine, owner, providers, tasks
from engine import tools as tools_mod
from engine.loop import Control, ToolCtx, ToolFailure
from test_engine import FakeModel

MAIN_ONLY = ["inbox", "journal", "not-installed-yet"]
CFG = {"claw": {"kind": "builtin"}, "engine": {"model": "deepseek/deepseek-flash", "skills": {"agents": {"main": MAIN_ONLY, "g-none": []}}}}


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def listed(server_json, monkeypatch):
    import sources
    monkeypatch.setattr(sources, "memory_tree", None)
    server_json(CFG)


def has(block: str, name: str) -> bool:
    """目录里有没有这个技能：「- 名字」（13c 起会话开头默认只留名字，engine/manuals.py）或「- 名字: 说明」（以前那样、skill 工具列的）。"""
    return any(ln == f"- {name}" or ln.startswith(f"- {name}:") for ln in block.splitlines())


def skill_of(agent: str):
    return next(t for t in tools_mod.for_agent(agent) if t.name == "skill")


def ctx(agent: str = "main") -> ToolCtx:
    return ToolCtx(turn="t", kind="owner", agent=agent, conversation=agent, call_id="c", control=Control())


def test_skills_for_reads_the_list_and_agents_without_one_see_everything(listed):
    everything = tools_mod.list_skills("g-other", agent_skills._offered("g-other"))
    assert {"board", "inbox", "journal"} <= set(everything)
    assert set(agent_skills.skills_for("main")) == {"inbox", "journal"}  # 名单上没装的不报错
    assert agent_skills.skills_for("g-other") == everything  # 没写名单：照旧全部
    assert agent_skills.skills_for("g-none") == {}  # 空名单：一个都不给
    assert agent_skills.names_for("g-other") is None and agent_skills.withheld("main", "board") and not agent_skills.withheld("main", "nope")


def test_the_opening_lists_only_the_agents_skills(listed, monkeypatch):
    skills = context.collect("main", "t-skills", "2026-10-19", {t.name for t in tools_mod.for_agent("main")}).text("skills")
    assert has(skills, "inbox") and has(skills, "journal") and not has(skills, "board") and not has(skills, "project")
    f = FakeModel([{"text": "ok"}])
    monkeypatch.setattr(providers, "stream", f.stream)
    run(owner.respond("t-skills", "2026-10-19", "hi", None, lambda t: None))
    system = f.requests[0][1].system
    listed_block = system[system.index("<available_skills>"):system.index("</available_skills>")]
    assert has(listed_block, "inbox") and not has(listed_block, "board")
    row = {"id": "tk_sk", "agent": "main", "thread": "t-skills", "depth": 1, "timeout_s": 600, "level": "read"}
    sub = tasks.child_system(row, tasks.child_tools(row))  # 后台任务子会话开头的目录也照名单
    assert "- journal:" in sub and "- board:" not in sub


def test_the_skill_tool_says_the_agent_does_not_have_it(listed):
    tool = skill_of("main")
    index = run(tool.run({}, ctx()))
    assert "- inbox:" in index and "- board:" not in index
    assert run(tool.run({"name": "inbox"}, ctx())).startswith("---\nname: inbox")
    with pytest.raises(ToolFailure) as e:
        run(tool.run({"name": "board"}, ctx()))
    assert "This Agent doesn't have the board skill" in str(e.value) and "inbox" in str(e.value)
    with pytest.raises(ToolFailure) as e:
        run(tool.run({"name": "no-such-skill"}, ctx()))
    assert "called no-such-skill" in str(e.value) and "doesn't have" not in str(e.value)  # 根本没有的照旧说「没有」（13c 起连手册也找过）
    assert "board" in run(skill_of("g-other").run({}, ctx("g-other")))  # 没写名单的 Agent 照旧


def test_helpers_over_mcp_get_the_same_list(request, server_json):
    """经 /mcp 给外援的技能：Claude Code 跑主人回合拿到的（mcp_engine.grant + for_agent）、后台任务的外援拿到的（tasks.mint）都照名单。"""
    fake_cc = request.getfixturevalue("cc")
    server_json(CFG)  # cc 自己写了一份 server.json：名单写回去
    from test_engine_claude_code import post, serving
    owner_tok = mcp_engine.grant(agent="main", tools=tools_mod.for_agent("main"), gate=tools_mod.gate, conversation="main")
    row = {"id": "tk_sk_mcp", "agent": "main", "runner": "claude-code", "depth": 1, "thread": "main"}
    task_tok = tasks.mint(row, tasks.child_tools(row), "read")

    async def go():
        async with serving(fake_cc):
            out = {}
            for who, tok in (("owner", owner_tok), ("task", task_tok)):
                out[who] = [(await post(fake_cc.url, tok, "tools/call", {"name": "skill", "arguments": args}))[1]["result"]
                            for args in ({}, {"name": "inbox"}, {"name": "board"})]
            return out
    try:
        got = run(go())
    finally:
        mcp_engine.revoke(owner_tok)
        tasks.revoke(task_tok)
    def text(r: dict) -> str:
        return r["content"][0]["text"]
    for who in ("owner", "task"):
        index, inbox, board = got[who]
        assert "- inbox:" in text(index) and "- board:" not in text(index), who
        assert inbox["isError"] is False and "name: inbox" in text(inbox), who
        assert board["isError"] is True and "doesn't have the board skill" in text(board), who


from test_engine_claude_code import cc  # noqa: E402,F401 — fixture（假的 claude + /mcp），上面经 request.getfixturevalue 拿
