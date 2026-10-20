"""第 5 批第 1 项：技能的门槛和配置（engine/skill_gate.py）。OpenClaw 的 metadata.openclaw.requires（bins、anyBins、env、config、os、always）
和 Hermes 的 platforms、required_environment_variables、requires_toolsets 都认；不满足的不进 skills_for、不进每句搜索，设置接口里列出缺什么；
engine.skills.entries 的开关、env（只写 ${变量名}，值进 secrets.env）、config（读全文时附在后面）；主机命令只拿到声明了的变量，沙箱里没有真值。
技能都是测试里现写的，模型是假的。"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import config
from engine import agent_skills, context, host_exec, manuals, sandbox, skill_gate
from engine import tools as tools_mod
from engine.loop import Control, ToolCtx, ToolFailure

TOKENS = {"phone": "tok-phone", "mcp": "tok-mcp"}
H = {"Authorization": "Bearer tok-phone"}
KEY = "fake-skill-key-1234567890"


def run(coro):
    return asyncio.run(coro)


def ctx(agent: str = "main") -> ToolCtx:
    return ToolCtx(turn="t", kind="owner", agent=agent, conversation=agent, call_id="c", control=Control())


def skill(root: Path, name: str, front: str, body: str = "1. Do the thing.") -> Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    p = d / "SKILL.md"
    p.write_text(f"---\nname: {name}\ndescription: The {name} skill for tests.\n{front}---\n\n# {name}\n{body}\n", encoding="utf8")
    return p


@pytest.fixture
def home(tmp_path, monkeypatch, server_json):
    """主工作区里几个现写的技能；数据目录、secrets.env 都在 tmp；claw 选 builtin。cfg(**额外的 engine 段) 改 server.json。"""
    import sources
    ws = tmp_path / "ws-main"
    (ws / "memory").mkdir(parents=True)
    root = ws / "skills"
    monkeypatch.setattr(config.settings, "workspace", ws)
    monkeypatch.setattr(config.settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(config.settings, "profile", tmp_path / "no-profile.md")
    monkeypatch.setattr(sources, "memory_tree", None)
    for v in ("FAKE_SKILL_API_KEY", "FAKE_HERMES_TOKEN", "FAKE_OTHER_API_KEY", "FAKE_REGION"):
        monkeypatch.delenv(v, raising=False)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    monkeypatch.setattr(skill_gate, "_tools_seen", {})
    skill(root, "needs-tool", 'metadata: {"openclaw": {"requires": {"bins": ["mousse-fake-tool"]}}}\n')
    skill(root, "any-tool", 'metadata: {"openclaw": {"requires": {"anyBins": "mousse-nope-a, mousse-fake-tool"}}}\n')
    skill(root, "needs-key", 'metadata: {"openclaw": {"primaryEnv": "FAKE_SKILL_API_KEY", "requires": {"env": ["FAKE_SKILL_API_KEY"]}}}\n')
    skill(root, "needs-config", "metadata:\n  openclaw:\n    requires:\n      config: [features.fancy]\n")  # YAML 写的也认
    skill(root, "mac-only", 'metadata: {"openclaw": {"os": ["darwin"]}}\n')
    skill(root, "always-on", 'metadata: {"openclaw": {"always": true, "requires": {"bins": ["mousse-never-installed"]}}}\n')
    skill(root, "windows-hermes", "platforms: [windows]\n")
    skill(root, "linux-hermes", "platforms: [linux, macos]\n")
    skill(root, "hermes-setup", "required_environment_variables:\n  - name: FAKE_HERMES_TOKEN\n    prompt: Your token\n"
                                "    help: Get one at https://example.org/tokens\n")
    skill(root, "hermes-toolset", "metadata:\n  hermes:\n    requires_toolsets: [homeassistant]\n")
    skill(root, "hermes-config", "metadata:\n  hermes:\n    config:\n      - key: units\n        description: Units to use\n"
                                 "        default: metric\n      - key: city\n        description: Home city\n")
    skill(root, "our-own", 'metadata: {"openmousse": {"requires": {"env": ["FAKE_OTHER_API_KEY"]}}}\n')
    base = {"claw": {"kind": "builtin"}, "auth": {"tokens": TOKENS}, "engine": {"model": "deepseek/deepseek-flash"}}

    def cfg(**engine) -> None:
        server_json({**base, "engine": {**base["engine"], **engine}})
    cfg()
    return SimpleNamespace(ws=ws, root=root, bin=bindir, cfg=cfg, dir=tmp_path)


def names(agent: str = "main") -> set[str]:
    return set(agent_skills.skills_for(agent))


def put_bin(home, monkeypatch, name: str = "mousse-fake-tool") -> None:
    p = home.bin / name
    p.write_text("#!/bin/sh\necho hi\n", encoding="utf8")
    p.chmod(0o755)
    monkeypatch.setenv("PATH", f"{home.bin}{os.pathsep}{os.environ.get('PATH', '')}")


# —— 门槛 ——————————————————————————————————————————————————————————————————————

def test_bins_hide_the_skill_until_the_program_is_on_path(home, monkeypatch):
    got = names()
    assert "needs-tool" not in got and "any-tool" not in got
    put_bin(home, monkeypatch)
    got = names()
    assert "needs-tool" in got and "any-tool" in got  # anyBins：有一个就行


def test_env_config_os_and_always(home, monkeypatch):
    got = names()
    assert "needs-key" not in got and "needs-config" not in got and "our-own" not in got
    assert "mac-only" not in got if sys.platform.startswith("linux") else True
    assert "always-on" in got  # always：不看 requires
    monkeypatch.setenv("FAKE_SKILL_API_KEY", KEY)
    import secretstore
    secretstore.put("FAKE_OTHER_API_KEY", KEY)
    home.cfg()
    assert "needs-key" not in names() and "our-own" not in names()  # 像密钥的：服务器有这个值不算，要主人在这个技能的设置里给
    home.cfg(skills={"entries": {"needs-key": {"apiKey": "${FAKE_SKILL_API_KEY}"},
                                 "our-own": {"env": {"FAKE_OTHER_API_KEY": "${FAKE_OTHER_API_KEY}"}}}})  # 进程环境、app 里存的都能引用
    server = json.loads(config.CONFIG_PATH.read_text(encoding="utf8"))
    server["features"] = {"fancy": True}
    config.CONFIG_PATH.write_text(json.dumps(server), encoding="utf8")
    config._cache = None
    got = names()
    assert {"needs-key", "needs-config", "our-own"} <= got
    server["features"] = {"fancy": ""}  # 空字符串不算「真」
    config.CONFIG_PATH.write_text(json.dumps(server), encoding="utf8")
    config._cache = None
    assert "needs-config" not in names()


def test_hermes_platforms_toolsets_and_setup_needed(home):
    got = agent_skills.skills_for("main")
    if sys.platform.startswith("linux"):
        assert "windows-hermes" not in got and "linux-hermes" in got
    assert "hermes-toolset" not in got  # 这个 Agent 没有那一组工具
    desc, path = got["hermes-setup"]  # 缺了不藏：标 setup needed
    assert "(setup needed: FAKE_HERMES_TOKEN)" in desc
    catalog = manuals.catalog_text("main", set(tools_mod.offered("main")))
    assert "- hermes-setup (setup needed)" in catalog and "- needs-tool" not in catalog and "- windows-hermes" not in catalog
    text = run(next(t for t in tools_mod.for_agent("main") if t.name == "skill").run({"name": "hermes-setup"}, ctx()))
    assert "[Skill setup note: Setup needed before using this skill: missing FAKE_HERMES_TOKEN." in text
    assert "https://example.org/tokens" in text and "never ask them to paste a key" in text


def test_gated_skills_stay_out_of_the_tool_the_catalog_and_the_search(home):
    tool = next(t for t in tools_mod.for_agent("main") if t.name == "skill")
    listing = run(tool.run({}, ctx()))
    assert "- needs-tool" not in listing and "- always-on:" in listing
    with pytest.raises(ToolFailure) as e:
        run(tool.run({"name": "needs-tool"}, ctx()))
    assert "can't be used right now" in str(e.value) and "needs mousse-fake-tool" in str(e.value)
    entries = manuals.catalog("main", set(tools_mod.offered("main")), kinds=("skill",))
    assert "needs-tool" not in {e["name"] for e in entries} and "always-on" in {e["name"] for e in entries}
    found = run(manuals.search("needs-tool skill for tests", agent="main", offered=set(tools_mod.offered("main")), use_model=False))
    assert "needs-tool" not in {x["entry"]["name"] for x in found["ranked"]}
    opening = context.collect("main", "t-gate", "2026-10-19", set(tools_mod.offered("main"))).text("skills")
    assert "needs-tool" not in opening and "always-on" in opening


def test_turned_off_in_entries(home):
    assert "always-on" in names()
    home.cfg(skills={"entries": {"always-on": {"enabled": False}}})
    assert "always-on" not in names()
    assert agent_skills.blocked("main", "always-on") == ["turned off in settings"]


# —— 配置 ——————————————————————————————————————————————————————————————————————

def test_entry_env_takes_only_references_and_satisfies_requires(home, monkeypatch):
    monkeypatch.setenv("FAKE_REGION", "eu-1")
    home.cfg(skills={"entries": {"needs-key": {"env": {"FAKE_SKILL_API_KEY": "literal-not-allowed"}}}})
    assert "needs-key" not in names()  # 像密钥的名字、值没写成 ${…}：不认
    import secretstore
    secretstore.put("STORED_FOR_SKILL", KEY)
    home.cfg(skills={"entries": {"needs-key": {"env": {"FAKE_SKILL_API_KEY": "${STORED_FOR_SKILL}", "REGION": "${FAKE_REGION}"}}}})
    assert "needs-key" in names()
    assert skill_gate.entry_env("needs-key") == {"FAKE_SKILL_API_KEY": KEY, "REGION": "eu-1"}
    home.cfg(skills={"entries": {"needs-key": {"apiKey": "${STORED_FOR_SKILL}"}}})  # apiKey = primaryEnv 那一个
    assert "needs-key" in names() and skill_gate.entry_env("needs-key", skill_gate.needs(skill_gate.front(home.root / "needs-key" / "SKILL.md"))) == {
        "FAKE_SKILL_API_KEY": KEY}


def test_config_is_appended_to_the_full_text(home):
    home.cfg(skills={"entries": {"hermes-config": {"config": {"city": "Springfield", "theme": "dark"}}}})
    tool = next(t for t in tools_mod.for_agent("main") if t.name == "skill")
    text = run(tool.run({"name": "hermes-config"}, ctx()))
    assert "[Skill config (from server.json engine.skills.entries.hermes-config.config):" in text
    assert "  units = metric" in text and "  city = Springfield" in text and "  theme = dark" in text and text.rstrip().endswith("]")
    assert "[Skill config" not in run(tool.run({"name": "always-on"}, ctx()))  # 没有设置的不附


# —— 主机命令、沙箱 ——————————————————————————————————————————————————————————————————

def test_host_commands_get_only_declared_variables_of_skills_the_agent_has(home):
    import secretstore
    secretstore.put("FAKE_SKILL_API_KEY", KEY)
    secretstore.put("FAKE_OTHER_API_KEY", "other-secret-value-123")
    skill(home.root, "needs-region", 'metadata: {"openclaw": {"requires": {"env": ["FAKE_REGION"]}}}\n')
    secretstore.put("FAKE_REGION", "eu-1")
    env = host_exec.child_env("main")
    assert "FAKE_SKILL_API_KEY" not in env and "FAKE_OTHER_API_KEY" not in env  # 技能只是声明了要：app 里存的密钥不交出去
    assert env["FAKE_REGION"] == "eu-1"  # 名字不像密钥的：照旧从 app 里存的取
    secretstore.put("FAKE_REGION", "https://u:p4ss@db.example.org/x")
    assert "FAKE_REGION" not in host_exec.child_env("main")  # 值里带网址密码的不给
    home.cfg(skills={"entries": {"needs-key": {"env": {"LD_PRELOAD": "${FAKE_SKILL_API_KEY}"}, "apiKey": "${FAKE_SKILL_API_KEY}"},
                                 "our-own": {"env": {"FAKE_OTHER_API_KEY": "${FAKE_OTHER_API_KEY}"}}},
                     "agents": {"g-nokey": ["always-on"]}})
    env = host_exec.child_env("main")
    assert env["FAKE_SKILL_API_KEY"] == KEY  # needs-key 声明的、主人在设置里给了
    assert env["FAKE_OTHER_API_KEY"] == "other-secret-value-123"  # our-own 声明的（metadata.openmousse）
    assert "LD_PRELOAD" not in env and "STORED_FOR_SKILL" not in env
    assert "FAKE_SKILL_API_KEY" not in host_exec.child_env("g-nokey")  # 名单上没有那个技能的 Agent 拿不到
    home.cfg(skills={"entries": {"needs-key": {"enabled": False}, "our-own": {"enabled": False}}})
    assert "FAKE_SKILL_API_KEY" not in host_exec.child_env("main")  # 关掉了：也拿不到
    spec = sandbox.build("main", "shell", sandbox.Net("mousse-errand", "172.30.99.1"))
    assert KEY not in json.dumps(spec.env) and KEY not in json.dumps(spec.args)  # 沙箱里没有真值


# —— 设置接口 ————————————————————————————————————————————————————————————————————

def test_setup_api_lists_what_is_missing_and_saves_keys_only_in_secrets(home):
    import main
    c = TestClient(main.app)
    r = c.get("/api/setup/skills/main", headers=H).json()
    items = {i["name"]: i for i in r["available"]}
    assert items["needs-tool"]["gate"]["eligible"] is False
    assert [m["label"] for m in items["needs-tool"]["gate"]["missing"]] == ["needs mousse-fake-tool"]
    assert [m["label"] for m in items["needs-key"]["gate"]["missing"]] == ["needs FAKE_SKILL_API_KEY"]
    assert items["hermes-setup"]["gate"]["eligible"] is True and items["hermes-setup"]["gate"]["setupNeeded"] == ["FAKE_HERMES_TOKEN"]
    assert c.get("/api/setup/skills/config/needs-key", headers={"Authorization": "Bearer tok-mcp"}).status_code == 403  # 只认主人的设备
    got = c.get("/api/setup/skills/config/needs-key", headers=H).json()
    assert got["found"] and got["env"][0]["name"] == "FAKE_SKILL_API_KEY" and got["env"][0]["present"] is False
    r = c.put("/api/setup/skills/config/needs-key", headers=H, json={"env": {"FAKE_SKILL_API_KEY": KEY}, "config": {"region": "eu"}})
    out = r.json()
    assert r.status_code == 200 and out["changed"] and out["backup"] and out["gate"]["eligible"] is True
    assert out["env"][0]["present"] is True and out["env"][0]["source"] == "app" and KEY not in json.dumps(out)
    conf = json.loads(config.CONFIG_PATH.read_text(encoding="utf8"))
    assert conf["engine"]["skills"]["entries"]["needs-key"] == {"env": {"FAKE_SKILL_API_KEY": "${FAKE_SKILL_API_KEY}"}, "config": {"region": "eu"}}
    assert KEY not in config.CONFIG_PATH.read_text(encoding="utf8") and KEY in (home.dir / "secrets.env").read_text(encoding="utf8")
    assert "needs-key" in names()
    bad = c.put("/api/setup/skills/config/needs-key", headers=H, json={"config": {"api_token": "x"}})
    assert bad.status_code == 400 and "env" in bad.json()["error"]
    assert c.put("/api/setup/skills/config/needs-key", headers=H, json={"env": {"PATH": "/tmp"}}).status_code == 400
    off = c.put("/api/setup/skills/config/needs-key", headers=H, json={"enabled": False, "env": {"FAKE_SKILL_API_KEY": None}}).json()
    assert off["enabled"] is False and "needs-key" not in names()
    assert "FAKE_SKILL_API_KEY" not in (home.dir / "secrets.env").read_text(encoding="utf8")  # 别的技能没在用：那一行也删了
    assert c.put("/api/setup/skills/config/nope-not-here", headers=H, json={"enabled": False}).status_code == 404


def test_frontmatter_shapes(home):
    n = skill_gate.needs({"metadata": '{"openclaw": {"requires": {"bins": "a, b", "env": ["X_KEY", "bad name"]}, "os": "linux"}}'})
    assert n.bins == ["a", "b"] and n.env == ["X_KEY"] and n.os == ["linux"]
    n = skill_gate.needs({"metadata": {"clawdbot": {"requires": {"anyBins": ["rg"]}}, "openmousse": {"always": True}}, "user-invocable": False})
    assert n.any_bins == ["rg"] and n.always is True and n.user_invocable is False
    req = skill_gate.required_env({"required_environment_variables": ["A_TOKEN", {"name": "B_TOKEN", "optional": True}],
                                   "setup": {"collect_secrets": [{"env_var": "C_TOKEN", "provider_url": "https://example.org"}]},
                                   "prerequisites": {"env_vars": "D_TOKEN"}})
    assert [r["name"] for r in req] == ["A_TOKEN", "B_TOKEN", "C_TOKEN", "D_TOKEN"] and req[1]["optional"] and req[2]["help"] == "https://example.org"
    assert skill_gate.os_ok(["macos", "linux", "windows"]) and not skill_gate.os_ok(["plan9"])


def test_hermes_toolsets_know_the_batch_3_hands():
    from engine import skill_gate as G
    assert G._has_tool("toolset:vision", {"view_image"}) and G._has_tool("toolset:image_gen", {"image_generate"})
    assert G._has_tool("toolset:tts", {"speak"}) and G._has_tool("text_to_speech", {"speak"}) and G._has_tool("vision_analyze", {"view_image"})
    assert G._has_tool("toolset:code_execution", {"execute_code"}) and G._has_tool("patch", {"apply_patch"})
    assert G._has_tool("toolset:browser", {"chrome"}) and not G._has_tool("toolset:tts", {"view_image"})
