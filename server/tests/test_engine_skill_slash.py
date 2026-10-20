"""第 5 批第 6 项：技能当斜杠命令用（engine/agent_skills.slash_skill）。「/技能名 剩下的话」→ 技能全文 + 剩下的话；内置命令赢；
user-invocable: false 的、这个 Agent 用不了的（名单外、没过门槛）不认；不像命令的话（路径、没有这个技能）照常当一句话。"""
from __future__ import annotations

import pytest

import config
from engine import agent_skills, used


def write(root, name: str, body: str = "", front: str = "") -> None:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: The {name} skill, for tests.\n{front}---\n\n# {name}\n{body}\n", encoding="utf8")


@pytest.fixture
def home(tmp_path, monkeypatch, server_json):
    import sources
    ws = tmp_path / "ws-main"
    (ws / "memory").mkdir(parents=True)
    skills = ws / "skills"
    skills.mkdir()
    monkeypatch.setattr(config.settings, "workspace", ws)
    monkeypatch.setattr(config.settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(config.settings, "profile", tmp_path / "no-profile.md")
    monkeypatch.setattr(sources, "memory_tree", None)
    base = {"claw": {"kind": "builtin"}, "engine": {"model": "deepseek/deepseek-flash"}}
    server_json(base)
    write(skills, "weather", "Look up the forecast with the weather API. FORECAST-STEPS.")
    (skills / "weather" / "references").mkdir()
    (skills / "weather" / "references" / "cities.md").write_text("Paris, Lyon\n", encoding="utf8")
    (skills / "weather" / ".cache").mkdir()
    (skills / "weather" / ".cache" / "x").write_text("hidden\n", encoding="utf8")
    write(skills, "web_search", "Search the web.")
    write(skills, "secret-ops", "Internal only.", front="user-invocable: false\n")
    write(skills, "needs-bin", "Needs a program.", front='metadata: {"openclaw": {"requires": {"bins": ["no-such-program-x1"]}}}\n')
    write(skills, "status", "A skill whose name a built-in command uses.")
    write(skills, "deploy", "Ship it.")
    return type("Home", (), {"ws": ws, "skills": skills, "base": base, "cfg": staticmethod(server_json)})


def test_a_skill_name_after_a_slash_loads_the_whole_skill_and_the_rest(home):
    s = agent_skills.slash_skill("/weather tomorrow in Paris", "main")
    assert s is not None and s.name == "weather" and s.rest == "tomorrow in Paris" and s.source == "workspace"
    assert s.message.startswith('[IMPORTANT: The user has invoked the "weather" skill')
    assert "FORECAST-STEPS." in s.message and "name: weather" in s.message  # SKILL.md 全文
    assert "references/cities.md" in s.message and ".cache" not in s.message  # 文件夹里的别的文件（藏起来的不列）
    assert s.message.rstrip().endswith("alongside the skill invocation: tomorrow in Paris")
    bare = agent_skills.slash_skill("  /weather", "main")
    assert bare is not None and bare.rest == "" and "alongside the skill invocation" not in bare.message
    multi = agent_skills.slash_skill("/weather\nParis\nand Lyon", "main")
    assert multi is not None and multi.rest == "Paris\nand Lyon"
    via = agent_skills.slash_skill("/skill weather this weekend", "main")  # OpenClaw、Hermes 也认 /skill 名字
    assert via is not None and via.name == "weather" and via.rest == "this weekend"


def test_names_compare_without_case_or_dash_underscore(home):
    for typed in ("/web-search cats", "/WEB_SEARCH cats", "/Web_Search cats"):
        s = agent_skills.slash_skill(typed, "main")
        assert s is not None and s.name == "web_search" and s.rest == "cats", typed


def test_built_in_commands_win(home):
    assert agent_skills.slash_skill("/status", "main") is None  # RESERVED
    assert agent_skills.slash_skill("/deploy now", "main") is not None
    assert agent_skills.slash_skill("/deploy now", "main", builtin=["deploy"]) is None  # 命令框架报上来的名字
    assert agent_skills.slash_skill("/deploy now", "main", builtin=["/Deploy"]) is None
    assert agent_skills.slash_skill("/skill weather x", "main", builtin=["skill"]) is None  # /skill 本身也让给内置的


def test_not_user_invocable_gated_or_not_on_the_list_are_not_commands(home):
    assert agent_skills.slash_skill("/secret-ops go", "main") is None  # user-invocable: false
    assert "secret-ops" in agent_skills.skills_for("main")  # 模型照旧能读
    assert agent_skills.slash_skill("/needs-bin x", "main") is None  # 没过门槛
    home.cfg({**home.base, "engine": {**home.base["engine"], "skills": {"agents": {"main": ["deploy"]}}}})
    assert agent_skills.slash_skill("/weather x", "main") is None  # 名单上没有
    assert agent_skills.slash_skill("/deploy x", "main") is not None
    home.cfg({**home.base, "engine": {**home.base["engine"], "skills": {"entries": {"deploy": {"enabled": False}}}}})
    assert agent_skills.slash_skill("/deploy x", "main") is None  # 关掉了


def test_ordinary_sentences_are_left_alone(home):
    for text in ("hello", "/usr/bin/env is broken", "/ weather", "/nosuchskill do it", "", "check /weather later", "/"):
        assert agent_skills.slash_skill(text, "main") is None, text


def test_the_skill_settings_ride_along_and_the_use_is_noted(home):
    home.cfg({**home.base, "engine": {**home.base["engine"], "skills": {"entries": {"weather": {"config": {"units": "metric"}}}}}})
    s = agent_skills.slash_skill("/weather", "main")
    assert s is not None and "[Skill config" in s.message and "units = metric" in s.message
    s.note("t-slash", conversation="main", agent="main")
    assert any(r["name"] == "weather" and r["via"] == "slash" and r["kind"] == "skill" for r in used.of_turn("t-slash"))
