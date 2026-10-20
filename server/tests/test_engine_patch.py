"""第 3 批第 1 项：多文件补丁（engine/patch.py）。默认没有、开了才有；增、改、改名、删一次做完；全有或全无（对不上、写到一半出错都不落）；
路径守卫和 write_file 一样；每个文件留旧版本、restore_file 能换回；宽松地找（行尾空白、弯引号）但上下文照原文留着；BOM、CRLF、末尾没换行照旧；
规矩文件、删留不了版本的大文件要点头（卡上是整个补丁，同意了照做）。全在临时目录里。"""
from __future__ import annotations

import asyncio

import pytest

import engine
from engine import safefs, tools, versions


def run(coro):
    return asyncio.run(coro)


def ctx(agent: str = "main") -> engine.ToolCtx:
    return engine.ToolCtx(turn="t", kind="owner", agent=agent, conversation=agent, call_id="c1", control=engine.Control())


def tool(name: str = "apply_patch", agent: str = "main") -> engine.Tool | None:
    return next((t for t in tools.for_agent(agent) if t.name == name), None)


def apply(text: str, agent: str = "main") -> str:
    t = tool(agent=agent)
    return run(t.run(t.prepare({"input": text}), ctx(agent)))


@pytest.fixture
def ws(tmp_path, monkeypatch, server_json):
    from config import settings
    w = tmp_path / "main-ws"
    w.mkdir()
    monkeypatch.setattr(settings, "workspace", w)
    server_json({"engine": {"files": {"patch": True}}})
    return w


def P(*body: str) -> str:
    return "\n".join(["*** Begin Patch", *body, "*** End Patch"])


def test_off_unless_switched_on(ws, server_json, tmp_path):
    from engine import handkit, owner
    server_json({})
    assert tool() is None  # 老实例：不写 engine.files.patch 就没有
    server_json({"engine": {"files": {"patch": True}}})
    assert tool() is not None and "apply_patch" in handkit.names()
    assert "**Patches**" in owner.rules("main", tools.for_agent("main")) and "apply_patch" not in owner.rules("main", tools.for_agent("main")).split("## Tools")[1].split("\n\n")[0]
    server_json({"engine": {"files": {"patch": True, "agents": {"main": False}}}})
    assert tool() is None  # 不给文件工具的 Agent 也没有补丁
    server_json({"engine": {"files": {"patch": True}, "tools": {"levels": {"apply_patch": "off"}}}})
    assert tool() is None
    server_json({"engine": {"files": {"patch": True}}})
    assert tool(agent="errand") is None  # 只在沙箱里的 Agent 没有主机上的东西


def test_add_update_move_delete_in_one_patch_and_each_can_be_restored(ws):
    (ws / "src").mkdir()
    (ws / "src" / "app.py").write_text("def main():\n    old()\n    return 1\n")
    (ws / "src" / "util.py").write_text("X = 1\nY = 2\n")
    (ws / "obsolete.txt").write_text("bye\n")
    out = apply(P("*** Add File: notes/new.md", "+# New", "+", "+body",
                  "*** Update File: src/app.py", "@@ def main():", "-    old()", "+    new()",
                  "*** Update File: src/util.py", "*** Move to: src/consts.py", "@@", " X = 1", "-Y = 2", "+Y = 3",
                  "*** Delete File: obsolete.txt"))
    assert (ws / "notes" / "new.md").read_text() == "# New\n\nbody\n"
    assert (ws / "src" / "app.py").read_text() == "def main():\n    new()\n    return 1\n"
    assert not (ws / "src" / "util.py").exists() and (ws / "src" / "consts.py").read_text() == "X = 1\nY = 3\n"
    assert not (ws / "obsolete.txt").exists()
    assert "A notes/new.md" in out and "M src/app.py" in out and "R src/util.py -> src/consts.py" in out and "D obsolete.txt" in out
    assert "restore_file" in out and "-    old()" in out and "+    new()" in out
    rt = tool("restore_file")
    for path in ("obsolete.txt", "src/util.py", "src/app.py", "src/consts.py", "notes/new.md"):
        run(rt.run({"path": path}, ctx()))
    assert (ws / "obsolete.txt").read_text() == "bye\n" and (ws / "src" / "util.py").read_text() == "X = 1\nY = 2\n"
    assert (ws / "src" / "app.py").read_text() == "def main():\n    old()\n    return 1\n"
    assert not (ws / "src" / "consts.py").exists() and not (ws / "notes" / "new.md").exists()


def test_all_or_nothing_when_a_later_file_does_not_match(ws):
    (ws / "a.txt").write_text("one\ntwo\n")
    (ws / "b.txt").write_text("alpha\nbeta\n")
    with pytest.raises(engine.ToolFailure, match="Failed to find the expected lines in b.txt"):
        apply(P("*** Update File: a.txt", "@@", "-one", "+ONE", "*** Add File: c.txt", "+new",
                "*** Update File: b.txt", "@@", "-gamma", "+GAMMA"))
    assert (ws / "a.txt").read_text() == "one\ntwo\n" and not (ws / "c.txt").exists() and (ws / "b.txt").read_text() == "alpha\nbeta\n"
    assert not versions.history(ws, "a.txt"), "nothing was kept either: the patch never got that far"
    for bad, why in ((P("*** Add File: a.txt", "+x"), "already exists"), (P("*** Delete File: missing.txt"), "no such file"),
                     (P("*** Update File: a.txt", "*** Move to: b.txt", "@@", "-one", "+1"), "already exists"),
                     ("*** Update File: a.txt\n@@\n-one\n+1", "first line"), (P("*** Frobnicate: a.txt"), "isn't a file header"),
                     (P("*** Update File: a.txt", "@@", " one", " two"), "No changes")):
        with pytest.raises(engine.ToolFailure, match=why):
            apply(bad)
    assert (ws / "a.txt").read_text() == "one\ntwo\n"


def test_a_write_that_fails_halfway_puts_back_what_was_written(ws, monkeypatch):
    (ws / "a.txt").write_text("one\n")
    (ws / "b.txt").write_text("two\n")
    real = safefs.Root.write
    calls = {"n": 0}

    def flaky(self, parts, data, **kw):
        if parts and parts[0] != versions.DIR:
            calls["n"] += 1
            if calls["n"] == 3:  # a.txt、c.txt 都写了，第三个出错
                raise OSError(28, "No space left on device")
        return real(self, parts, data, **kw)
    monkeypatch.setattr(safefs.Root, "write", flaky)
    with pytest.raises(engine.ToolFailure, match="put back"):
        apply(P("*** Update File: a.txt", "-one", "+ONE", "*** Add File: c.txt", "+new", "*** Update File: b.txt", "-two", "+TWO"))
    assert (ws / "a.txt").read_text() == "one\n" and not (ws / "c.txt").exists() and (ws / "b.txt").read_text() == "two\n"


def test_the_path_guard_is_the_same_as_write_file(ws, server_json, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (ws / "link").symlink_to(outside)
    (ws / "ok.txt").write_text("fine\n")
    nested = ws / "agents" / "diet"
    nested.mkdir(parents=True)
    (ws / "scripts").mkdir()
    (ws / "scripts" / "run.sh").write_text("#!/bin/sh\necho hi\n")
    server_json({"agent_workspaces": {"diet": str(nested)}, "engine": {"files": {"patch": True},
                                                                         "host_exec": {"agents": {"main": {"allow": ["<workspace>/scripts/run.sh"]}}}}})
    for path in ("../outside/x.txt", str(outside / "x.txt"), "link/x.txt", ".env", "secrets/k.txt", "agents/diet/AGENTS.md", ".git/config",
                 ".mousse-versions/x", "scripts/run.sh", "scripts/new.sh"):
        with pytest.raises(engine.ToolFailure):
            apply(P("*** Update File: ok.txt", "-fine", "+changed", f"*** Add File: {path}", "+boom"))
    assert (ws / "ok.txt").read_text() == "fine\n" and not list(outside.iterdir()) and not (ws / ".env").exists()
    with pytest.raises(engine.ToolFailure, match="symbolic link"):
        apply(P("*** Delete File: link"))
    assert (ws / "link").is_symlink()


def test_loose_matching_keeps_the_original_bytes_and_line_endings(ws):
    (ws / "q.md").write_bytes("﻿Title\r\n“Quoted” line   \r\nkeep\r\nlast".encode())  # BOM、CRLF、弯引号、行尾空格、末尾没换行
    apply(P("*** Update File: q.md", "@@", ' "Quoted" line', "-keep", "+kept", "+added"))
    assert (ws / "q.md").read_bytes() == "﻿Title\r\n“Quoted” line   \r\nkept\r\nadded\r\nlast".encode()
    (ws / "eof.txt").write_text("a\nb")
    apply(P("*** Update File: eof.txt", "@@", "+c", "*** End of File"))
    assert (ws / "eof.txt").read_text() == "a\nb\nc"  # 末尾本来没有换行：还是没有
    (ws / "dup.txt").write_text("x = 1\ny = 2\nx = 1\n")
    with pytest.raises(engine.ToolFailure, match="must be unique"):
        apply(P("*** Update File: dup.txt", "@@", "-x = 1", "+x = 9"))
    (ws / "code.py").write_text("def apply_edits(text, edits):\n    pass\n\ndef other():\n    pass\n")
    apply(P("*** Update File: code.py", "@@ def apply_edits", "-    pass", "+    return text"))  # 半行的 @@ 上下文也认（只许一行含着它）
    assert (ws / "code.py").read_text() == "def apply_edits(text, edits):\n    return text\n\ndef other():\n    pass\n"
    apply("<<'EOF'\n" + P("*** Move File: code.py -> lib/code.py") + "\nEOF")  # 外面套了 heredoc、Hermes 的只改名
    assert (ws / "lib" / "code.py").exists() and not (ws / "code.py").exists()
    apply(P("*** Update File: lib/code.py", "@@ -1,2 +1,2 @@", " def apply_edits(text, edits):", "-    return text", "+    return edits"))
    assert "return edits" in (ws / "lib" / "code.py").read_text()  # 统一 diff 的段头不当上下文


def test_rules_files_and_big_deletes_ask_and_approval_applies_the_patch(ws, monkeypatch):
    import inbox
    tools.install_hooks()
    (ws / "AGENTS.md").write_text("rules\n")
    (ws / "notes.txt").write_text("n\n")
    t = tool()
    assert t.level({"input": P("*** Update File: notes.txt", "-n", "+m")})[0] == "undo"
    level, why = t.level({"input": P("*** Update File: AGENTS.md", "-rules", "+new rules")})
    assert level == "ask" and "rules file" in why
    monkeypatch.setattr(versions, "MAX_BYTES", 4)
    (ws / "big.bin").write_text("0123456789")
    assert t.level({"input": P("*** Delete File: big.bin")})[0] == "ask"
    with pytest.raises(engine.ToolFailure, match="too big"):
        apply(P("*** Update File: big.bin", "-0123456789", "+x"))  # 要改的大文件不交卡，直接不改（同 edit_file）
    monkeypatch.setattr(versions, "MAX_BYTES", 10 * 1024 * 1024)
    args = {"input": P("*** Update File: AGENTS.md", "-rules", "+new rules"), "why": "Tidy the fictional rules."}

    async def go():
        verdict = await tools.gate(t, args, ctx())
        assert verdict["status"] == "approval-pending"
        it = inbox.item(verdict["inbox"])
        assert "+new rules" in it["detail"] and "```diff" in it["detail"] and (ws / "AGENTS.md").read_text() == "rules\n"
        res = await tools.on_decided(it, "approve")
        return res
    res = run(go())
    assert "Success" in res["result"] and (ws / "AGENTS.md").read_text() == "new rules\n"
    assert versions.history(ws, "AGENTS.md")[0]["note"] == "before apply_patch"
