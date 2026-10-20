"""第 5 批第 2 项：用户级的技能目录（数据目录下所有 Agent 共用的 skills/）。三层的优先级：Agent 工作区 > 用户级 > packs/core；
skill_manage 写不进用户级的（只有技能市场和主人放得进去）。"""
from __future__ import annotations

import asyncio

import pytest

import bridges
import config
from engine import agent_skills, manuals, skill_write
from engine import tools as tools_mod
from engine.loop import Control, ToolCtx, ToolFailure

CORE = bridges.REPO / "packs" / "core" / "skills"


def run(coro):
    return asyncio.run(coro)


def ctx(agent: str = "main") -> ToolCtx:
    return ToolCtx(turn="t", kind="owner", agent=agent, conversation=agent, call_id="c", control=Control())


def write(root, name: str, marker: str) -> None:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {marker} copy of {name}, for tests.\n---\n\n# {name}\n{marker}\n", encoding="utf8")


@pytest.fixture
def home(tmp_path, monkeypatch, server_json):
    import sources
    ws = tmp_path / "ws-main"
    (ws / "memory").mkdir(parents=True)
    (ws / "skills").mkdir()
    monkeypatch.setattr(config.settings, "workspace", ws)
    monkeypatch.setattr(config.settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(config.settings, "profile", tmp_path / "no-profile.md")
    monkeypatch.setattr(sources, "memory_tree", None)
    server_json({"claw": {"kind": "builtin"}, "engine": {"model": "deepseek/deepseek-flash", "tools": {"levels": {"skill_manage": "auto"}}}})
    user = tmp_path / "data" / "skills"
    user.mkdir(parents=True)
    return type("Home", (), {"ws": ws, "user": user})


def test_three_layers_workspace_then_user_then_core(home):
    assert (CORE / "journal" / "SKILL.md").is_file()
    offered = set(tools_mod.offered("main")) | {"tasks"}
    assert tools_mod.skill_dirs("main") == [home.ws / "skills", config.settings.data_dir / "skills", CORE]
    before = tools_mod.list_skills("main", offered)["journal"][1]
    assert agent_skills.source_of(before) == "core"
    write(home.user, "journal", "USER")  # 用户级的盖过 core 的
    write(home.user, "shared-one", "USER")
    got = tools_mod.list_skills("main", offered)
    assert agent_skills.source_of(got["journal"][1]) == "user" and "USER copy" in got["journal"][0] and "shared-one" in got
    write(home.ws / "skills", "journal", "WORKSPACE")  # 工作区的又盖过用户级的
    got = tools_mod.list_skills("main", offered)
    assert agent_skills.source_of(got["journal"][1]) == "workspace" and "WORKSPACE copy" in got["journal"][0]
    view = {i["name"]: i for i in agent_skills.view("main")["available"]}
    assert view["shared-one"]["source"] == "user" and view["journal"]["source"] == "workspace"
    tool = next(t for t in tools_mod.for_agent("main") if t.name == "skill")
    assert "USER" in run(tool.run({"name": "shared-one"}, ctx()))
    entries = {e["name"]: e for e in manuals.catalog("main", offered, kinds=("skill",))}
    assert entries["shared-one"]["source"] == "user" and entries["shared-one"]["own"] is False


def test_every_agent_sees_the_user_level_skills(home, tmp_path, monkeypatch):
    ws2 = tmp_path / "ws-g"
    (ws2 / "skills").mkdir(parents=True)
    monkeypatch.setattr(type(config.settings), "agent_workspaces", property(lambda self: {"g-two": ws2}))
    write(home.user, "shared-one", "USER")
    assert "shared-one" in agent_skills.skills_for("g-two") and "shared-one" in agent_skills.skills_for("main")


def test_skill_manage_cannot_write_into_the_user_level_folder(home):
    write(home.user, "shared-one", "USER")
    original = (home.user / "shared-one" / "SKILL.md").read_text(encoding="utf8")
    with pytest.raises(ToolFailure):  # 改一份不在自己工作区里的：没有这个 skill
        run(skill_write.apply("main", {"action": "patch", "name": "shared-one", "old": "USER", "new": "HACKED"}, ctx()))
    with pytest.raises(ToolFailure):
        run(skill_write.apply("main", {"action": "delete", "name": "shared-one"}, ctx()))
    # 同名新建的只进自己的工作区（盖过用户级那一份），用户级那一份一个字不动
    run(skill_write.apply("main", {"action": "create", "name": "shared-one", "content":
                                   "---\nname: shared-one\ndescription: My own version, for tests.\n---\n\n1. Mine.\n"}, ctx()))
    assert (home.ws / "skills" / "shared-one" / "SKILL.md").is_file()
    assert (home.user / "shared-one" / "SKILL.md").read_text(encoding="utf8") == original
    # 自己工作区的 skills/<名字> 是指到用户级的链接：改之前换成自己的拷贝，链接那边不动
    write(home.user, "linked-target", "USER")
    (home.ws / "skills" / "linked-target").symlink_to(home.user / "linked-target")
    run(skill_write.apply("main", {"action": "patch", "name": "linked-target", "old": "USER\n", "new": "MINE\n"}, ctx()))
    assert "USER" in (home.user / "linked-target" / "SKILL.md").read_text(encoding="utf8")
    assert not (home.ws / "skills" / "linked-target").is_symlink()
