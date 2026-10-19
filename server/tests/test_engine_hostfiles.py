"""内核 ⑫：主机上的文件工具（engine/host_files.py）、不跟符号链接的读写（safefs.py）、留旧版本 / 恢复（versions.py）。
路径守卫（..、符号链接、像密钥的、别的 Agent 的工作区、服务器自己的东西）、写和改留旧版本能恢复、pi 式 edit 的唯一匹配、分类。
全在临时目录里，不碰真的工作区。"""
from __future__ import annotations

import asyncio
import json
import os

import pytest

import engine
from engine import host_files, safefs, tools, versions


def run(coro):
    return asyncio.run(coro)


def ctx(agent: str = "main") -> engine.ToolCtx:
    return engine.ToolCtx(turn="t", kind="owner", agent=agent, conversation=agent, call_id="c1", control=engine.Control())


def tool(name: str, agent: str = "main") -> engine.Tool:
    return next(t for t in tools.for_agent(agent) if t.name == name)


def call(name: str, args: dict, agent: str = "main") -> str:
    t = tool(name, agent)
    return run(t.run(t.prepare(args) if t.prepare else args, ctx(agent)))


@pytest.fixture
def ws(tmp_path, monkeypatch, server_json):
    from config import settings
    w = tmp_path / "main-ws"
    w.mkdir()
    monkeypatch.setattr(settings, "workspace", w)
    server_json({})
    return w


# —— 路径守卫 ——————————————————————————————————————————————————————————————————

def test_path_guard_refuses_escapes_links_secrets_and_other_agents(ws, server_json, tmp_path):
    nested = ws / "agents" / "diet"
    nested.mkdir(parents=True)
    (nested / "AGENTS.md").write_text("diet rules\n")
    server_json({"agent_workspaces": {"diet": str(nested)}})
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "notes.txt").write_text("outside the workspace\n")
    (ws / "notes.md").write_text("hello from the workspace\n")
    (ws / "link-dir").symlink_to(outside)
    (ws / "link.md").symlink_to(outside / "notes.txt")
    (ws / ".env").write_text("API_TOKEN=fake-not-real\n")
    (ws / "secrets").mkdir()
    (ws / "secrets" / "a.txt").write_text("fake secret\n")
    (ws / "api_key.txt").write_text("fake key\n")
    (ws / "credentials.json").write_text("{}\n")
    bad = ["../outside/notes.txt", str(outside / "notes.txt"), "notes/../../outside/notes.txt", "~/notes.txt", "link-dir/notes.txt", "link.md",
           ".env", "secrets/a.txt", "api_key.txt", "credentials.json", "agents/diet/AGENTS.md", ".mousse-versions/x"]
    for path in bad:
        with pytest.raises(engine.ToolFailure):
            call("read_file", {"path": path})
        with pytest.raises(engine.ToolFailure):
            call("write_file", {"path": path, "content": "x"})
    assert (outside / "notes.txt").read_text() == "outside the workspace\n" and (ws / ".env").read_text() == "API_TOKEN=fake-not-real\n"
    assert "hello from the workspace" in call("read_file", {"path": "notes.md"})
    assert "hello from the workspace" in call("read_file", {"path": str(ws / "notes.md")})  # 工作区里的绝对路径可以
    # 搜内容不搜像密钥的；列目录标出来、链接不跟
    assert "fake" not in call("grep_files", {"pattern": "fake"})
    listing = call("list_dir", {})
    assert ".env  (" in listing and "looks like a secret" in listing and "link-dir -> (symbolic link, not followed)" in listing
    assert "agents/" in listing
    found = call("find_files", {"pattern": "*.txt"})
    assert "api_key.txt" not in found and "secrets/a.txt" not in found and "outside" not in found
    # .git 里能读不能写
    (ws / ".git").mkdir()
    (ws / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    assert "refs/heads/main" in call("read_file", {"path": ".git/HEAD"})
    with pytest.raises(engine.ToolFailure, match=".git"):
        call("write_file", {"path": ".git/hooks/pre-commit", "content": "boom"})
    # .git 里的 config 当密钥：带令牌的远程地址、CI 写的 extraheader 在那（子模块的也是）
    (ws / ".git" / "modules" / "lib").mkdir(parents=True)
    (ws / ".git" / "config").write_text('[remote "origin"]\n\turl = https://someone:fake-token-123@git.example/repo.git\n')
    (ws / ".git" / "modules" / "lib" / "config").write_text("[http]\n\textraheader = AUTHORIZATION: basic fake-token-456\n")
    for path in (".git/config", ".git/modules/lib/config", str(ws / ".git" / "config")):
        with pytest.raises(engine.ToolFailure, match="secret"):
            call("read_file", {"path": path})
    assert "fake-token" not in call("grep_files", {"pattern": "fake-token", "path": ".git"})
    found = call("find_files", {"pattern": "config", "path": ".git"})
    assert ".git/config" not in found and "lib/config" not in found
    git_listing = call("list_dir", {"path": ".git"})
    assert "looks like a secret" in git_listing.split("config  (", 1)[1].split("\n", 1)[0]


def test_safefs_never_follows_a_link_swapped_into_the_path(tmp_path):
    root = tmp_path / "root"
    (root / "a").mkdir(parents=True)
    (root / "a" / "f.txt").write_text("inside")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "f.txt").write_text("outside")
    r = safefs.Root(root)
    assert r.read(["a", "f.txt"])[0] == b"inside"
    (root / "a" / "f.txt").unlink()
    (root / "a").rmdir()
    (root / "a").symlink_to(outside)  # 查完以后换成了链接：一层层走的时候照样挡住
    with pytest.raises(safefs.Refused):
        r.read(["a", "f.txt"])
    with pytest.raises(safefs.Refused):
        r.write(["a", "new.txt"], b"x")
    assert not (outside / "new.txt").exists()
    with pytest.raises(safefs.Refused):
        safefs.split("x/../y")


def test_agent_without_a_workspace_gets_no_file_tools_and_cannot_reach_main(ws, server_json, tmp_path):
    diet = tmp_path / "diet-ws"
    diet.mkdir()
    (diet / "log.md").write_text("meals\n")
    (ws / "main-only.md").write_text("main\n")
    server_json({"agent_workspaces": {"diet": str(diet)}})
    assert "meals" in call("read_file", {"path": "log.md"}, agent="diet")
    with pytest.raises(engine.ToolFailure):
        call("read_file", {"path": str(ws / "main-only.md")}, agent="diet")  # 主工作区不是它的
    assert not {t.name for t in tools.for_agent("ghost")} & set(host_files.NAMES)
    server_json({"agent_workspaces": {"diet": str(diet)}, "engine": {"files": {"agents": {"diet": False}}}})
    assert not {t.name for t in tools.for_agent("diet")} & set(host_files.NAMES) and "read_file" in {t.name for t in tools.for_agent("main")}


# —— 读、列、找、搜 ——————————————————————————————————————————————————————————————————

def test_read_numbers_lines_and_pages(ws):
    (ws / "long.txt").write_text("".join(f"line {i}\n" for i in range(1, 1001)))
    out = call("read_file", {"path": "long.txt"})
    assert out.startswith("long.txt · lines 1–400 of 1000\n     1\tline 1\n") and out.endswith("[Showing lines 1–400 of 1000. Use offset=401 to continue.]")
    out = call("read_file", {"path": "long.txt", "offset": 999, "limit": 5})
    assert out.endswith("   999\tline 999\n  1000\tline 1000") and "Use offset" not in out
    with pytest.raises(engine.ToolFailure, match="beyond the end"):
        call("read_file", {"path": "long.txt", "offset": 5000})
    (ws / "img.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00binary")
    assert "binary file (PNG image" in call("read_file", {"path": "img.png"})
    (ws / "empty.txt").write_text("")
    assert "is empty" in call("read_file", {"path": "empty.txt"})
    with pytest.raises(engine.ToolFailure, match="no such file"):
        call("read_file", {"path": "missing.txt"})


def test_read_extracts_text_from_documents(ws):
    import docx
    d = docx.Document()
    d.add_paragraph("A fictional meeting note about the garden plan.")
    d.save(str(ws / "note.docx"))
    out = call("read_file", {"path": "note.docx"})
    assert "(extracted text)" in out and "garden plan" in out


def test_find_and_grep(ws):
    (ws / "notes" / "2026").mkdir(parents=True)
    (ws / "notes" / "2026" / "trip.md").write_text("Pack the TENT\nbuy stove fuel\n")
    (ws / "notes" / "todo.md").write_text("- call the plumber\n")
    (ws / "top.md").write_text("tent poles\n")
    (ws / "node_modules").mkdir()
    (ws / "node_modules" / "x.md").write_text("tent\n")
    found = call("find_files", {"pattern": "*.md"})
    assert found.split("\n") == ["notes/2026/trip.md", "notes/todo.md", "top.md"]
    assert call("find_files", {"pattern": "notes/**/*.md"}).split("\n") == ["notes/2026/trip.md", "notes/todo.md"]
    hits = call("grep_files", {"pattern": "tent", "ignore_case": True})
    assert hits.split("\n") == ["notes/2026/trip.md:1: Pack the TENT", "top.md:1: tent poles"]
    assert call("grep_files", {"pattern": "tent", "path": "notes", "ignore_case": True}) == "notes/2026/trip.md:1: Pack the TENT"
    assert call("grep_files", {"pattern": "plumb", "glob": "todo.md"}) == "notes/todo.md:1: - call the plumber"
    assert call("grep_files", {"pattern": "(", "literal": True}) == "No matches found."
    with pytest.raises(engine.ToolFailure, match="Bad regular expression"):
        call("grep_files", {"pattern": "("})
    assert "limit reached" in call("grep_files", {"pattern": "e", "limit": 1})


# —— 写、改、旧版本 ——————————————————————————————————————————————————————————————————

def test_write_keeps_old_versions_and_restore_brings_them_back(ws):
    out = call("write_file", {"path": "plans/garden.md", "content": "one\n"})
    assert out.startswith("Created plans/garden.md") and "version v1 (removes it)" in out
    out = call("write_file", {"path": "plans/garden.md", "content": "two\n"})
    assert out.startswith("Wrote plans/garden.md") and "version v2" in out
    assert (ws / "plans" / "garden.md").read_text() == "two\n"
    listing = call("restore_file", {"path": "plans/garden.md", "list": True})
    assert listing.index("v2") < listing.index("v1") and "(didn't exist yet)" in listing
    out = call("restore_file", {"path": "plans/garden.md"})  # 最近留的那一版 = 写 two 之前的 one
    assert (ws / "plans" / "garden.md").read_text() == "one\n" and "Restored plans/garden.md to v2" in out and "version v3" in out
    call("restore_file", {"path": "plans/garden.md", "version": "v3"})  # 恢复也能撤
    assert (ws / "plans" / "garden.md").read_text() == "two\n"
    call("restore_file", {"path": "plans/garden.md", "version": "v1"})  # 那时还没有：删掉
    assert not (ws / "plans" / "garden.md").exists()
    with pytest.raises(engine.ToolFailure, match="no version v9"):
        call("restore_file", {"path": "plans/garden.md", "version": "v9"})
    assert ".mousse-versions" not in call("list_dir", {}) and (ws / ".mousse-versions").is_dir()


def test_versions_module_is_generic_capped_and_refuses_huge_files(tmp_path, monkeypatch):
    root = tmp_path / "skills-root"
    (root / "skills" / "x").mkdir(parents=True)
    f = root / "skills" / "x" / "SKILL.md"
    monkeypatch.setattr(versions, "KEEP", 3)
    for i in range(6):
        f.write_text(f"draft {i}\n")
        versions.keep(root, "skills/x/SKILL.md", by="tester")
    hist = versions.history(root, "skills/x/SKILL.md")
    assert [v["id"] for v in hist] == ["v6", "v5", "v4"] and hist[0]["by"] == "tester"
    assert versions.keep(root, "skills/x/SKILL.md")["id"] == "v6"  # 没变：不再存一份
    res = versions.restore(root, "skills/x/SKILL.md", "v4")
    assert f.read_text() == "draft 3\n" and res["kept"]["id"] == "v6"
    monkeypatch.setattr(versions, "MAX_BYTES", 10)
    f.write_text("x" * 100)
    with pytest.raises(versions.TooBig):
        versions.keep(root, "skills/x/SKILL.md")
    with pytest.raises(safefs.Refused):
        versions.keep(root, ".mousse-versions/whatever")


def test_edit_is_pi_style_unique_and_only_touches_the_match(ws):
    p = ws / "doc.md"
    p.write_text("alpha\nbeta\nalpha beta\n")
    with pytest.raises(engine.ToolFailure, match="Found 2 occurrences of the text in doc.md"):
        call("edit_file", {"path": "doc.md", "edits": [{"old": "alpha", "new": "A"}]})
    with pytest.raises(engine.ToolFailure, match="Could not find the text in doc.md"):
        call("edit_file", {"path": "doc.md", "edits": [{"old": "gamma", "new": "G"}]})
    out = call("edit_file", {"path": "doc.md", "edits": [{"old": "alpha\nbeta", "new": "ALPHA\nbeta"}]})
    assert p.read_text() == "ALPHA\nbeta\nalpha beta\n" and out.startswith("Replaced 1 block(s) in doc.md") and "version v1" in out
    assert "-alpha" in out and "+ALPHA" in out
    # 几处都对着原文找；重叠的不行
    call("edit_file", {"path": "doc.md", "edits": [{"old": "ALPHA", "new": "one"}, {"old": "alpha beta", "new": "two"}]})
    assert p.read_text() == "one\nbeta\ntwo\n"
    with pytest.raises(engine.ToolFailure, match="overlap"):
        call("edit_file", {"path": "doc.md", "edits": [{"old": "one\nbeta", "new": "x"}, {"old": "beta\ntwo", "new": "y"}]})
    # 写在外面的 old / new 也认（pi 的 prepareEditArguments）；带着行号抄过来的提示去掉
    call("edit_file", {"path": "doc.md", "old": "two", "new": "three"})
    assert p.read_text() == "one\nbeta\nthree\n"
    with pytest.raises(engine.ToolFailure, match="line-number prefixes"):
        call("edit_file", {"path": "doc.md", "edits": [{"old": "     1\tone\n     2\tbeta", "new": "x"}]})
    # 宽松的找法：弯引号、行尾空白；只换找到的那一段，同一行里中文的弯引号不动
    p.write_text("“你好”，世界  \nit’s fine  \n")
    call("edit_file", {"path": "doc.md", "edits": [{"old": "it's fine", "new": "it is fine"}]})
    assert p.read_text() == "“你好”，世界  \nit is fine  \n"
    # CRLF、BOM 照原样
    p.write_bytes("﻿line1\r\nline2\r\n".encode())
    call("edit_file", {"path": "doc.md", "edits": [{"old": "line2", "new": "LINE2"}]})
    assert p.read_bytes() == "﻿line1\r\nLINE2\r\n".encode()
    p.write_bytes(b"\xff\xfe broken")
    with pytest.raises(engine.ToolFailure, match="UTF-8"):
        call("edit_file", {"path": "doc.md", "edits": [{"old": "broken", "new": "x"}]})


# —— 分类 ——————————————————————————————————————————————————————————————————

def test_levels_reads_undoable_writes_and_rules_files(ws, server_json):
    lv = {t.name: t.level for t in tools.for_agent("main")}
    assert all(lv[n]({"path": "x", "pattern": "x"})[0] == "read" for n in ("read_file", "list_dir", "find_files", "grep_files"))
    assert lv["write_file"]({"path": "notes.md"})[0] == "undo" and "restore_file" in lv["write_file"]({"path": "notes.md"})[1]
    assert lv["edit_file"]({"path": "MEMORY.md"})[0] == "undo" and lv["write_file"]({"path": "memory/2026-10-16.md"})[0] == "undo"
    for rules_file in ("AGENTS.md", "agents.md", "SOUL.md", "USER.md", "skills/x/SKILL.md", str(ws / "TOOLS.md")):
        assert lv["write_file"]({"path": rules_file})[0] == "ask", rules_file
    assert lv["restore_file"]({"path": "AGENTS.md", "list": True})[0] == "read" and lv["restore_file"]({"path": "AGENTS.md"})[0] == "ask"
    server_json({"engine": {"tools": {"levels": {"write_file": "ask", "grep_files": "off", "edit_file": "auto"}}}})
    names = {t.name for t in tools.for_agent("main")}
    lv = {t.name: t.level for t in tools.for_agent("main")}
    assert "grep_files" not in names and lv["write_file"]({"path": "notes.md"})[0] == "ask" and lv["edit_file"]({"path": "AGENTS.md"})[0] == "undo"
    server_json({"engine": {"files": {"ask": ["notes/**"], "secrets": ["*.private"]}}})
    lv = {t.name: t.level for t in tools.for_agent("main")}
    assert lv["write_file"]({"path": "notes/a.md"})[0] == "ask" and lv["write_file"]({"path": "AGENTS.md"})[0] == "undo"
    (ws / ".env").write_text("X=1\n")
    (ws / "a.private").write_text("p\n")
    assert "X=1" in call("read_file", {"path": ".env"})  # 名单整个换掉了
    with pytest.raises(engine.ToolFailure):
        call("read_file", {"path": "a.private"})


def test_rules_file_goes_through_a_card_then_is_written_with_a_version(ws, server_json):
    import inbox
    tools.install_hooks()
    (ws / "AGENTS.md").write_text("# Rules\nBe brief.\n")
    t = tool("write_file")

    async def go():
        verdict = await tools.gate(t, {"path": "AGENTS.md", "content": "# Rules\nBe kind.\n"}, ctx())
        assert verdict["status"] == "approval-pending"
        it = inbox.item(verdict["inbox"])
        assert it["kind"] == "tool" and it["title"] == "Write a file: AGENTS.md" and "rules file" in it["why"] and "Be kind." in it["detail"]
        assert (ws / "AGENTS.md").read_text() == "# Rules\nBe brief.\n"  # 还没写
        # 同意 = 服务器照卡上的参数写一次，旧版本照样留着；kick 那一轮这里不管（不是内核 claw，起不来）
        return await inbox.act(verdict["inbox"], inbox.ActIn(action="approve"))
    out = run(go())
    assert out["item"]["status"] == "done" and "Wrote AGENTS.md" in out["item"]["result"]
    assert (ws / "AGENTS.md").read_text() == "# Rules\nBe kind.\n"
    assert versions.history(ws, "AGENTS.md")[0]["id"] == "v1"
    row = tools.call_of(out["item"]["id"])
    assert row["status"] == "done" and json.loads(row["args"])["path"] == "AGENTS.md" and row["tool"] == "write_file"


def test_the_card_shows_everything_that_would_be_written(ws):
    """同意了写的就是卡上的：内容一个字不截；长到卡上放不下的不交卡（让它拆小），免得后面藏着没给人看的。"""
    import inbox
    t = tool("write_file")
    long = "# Rules\n" + "Be kind to the plants.\n" * 300 + "FICTIONAL-TAIL: water on Sundays\n"
    verdict = run(tools.gate(t, {"path": "AGENTS.md", "content": long}, ctx()))
    assert verdict["status"] == "approval-pending" and "FICTIONAL-TAIL: water on Sundays" in inbox.item(verdict["inbox"])["detail"]
    refused = run(tools.gate(t, {"path": "AGENTS.md", "content": "Be kind.\n" * 3000}, ctx()))
    assert isinstance(refused, str) and "too long" in refused and "edit_file" in refused
    assert not (ws / "AGENTS.md").exists()


def test_cyber_and_outsider_turns_get_no_file_tools(ws):
    from engine import providers
    from engine.errors import EngineError
    from engine.session import MemorySession, user
    from test_engine import FakeModel
    f = FakeModel([{"text": "hi"}])
    real = providers.stream
    providers.stream = f.stream
    try:
        run(engine.run_turn(engine.Spec(kind="cyber", model="deepseek/x", tools=tools.for_agent("main"), gate=tools.gate), MemorySession(), user("hi")))
        with pytest.raises(EngineError):
            run(engine.run_turn(engine.Spec(kind="outsider", model="deepseek/x", tools=tools.for_agent("main")), MemorySession(), user("x")))
    finally:
        providers.stream = real
    assert f.requests[0][1].tools == []
    cyber = engine.ToolCtx(turn="t", kind="cyber", agent="main", conversation="c", call_id="1", control=engine.Control())
    assert "only run in the owner's" in run(tools.gate(tool("read_file"), {"path": "x"}, cyber))
    assert os.environ.get("MOUSSE_SERVER_CONFIG")  # conftest 的临时配置还在
