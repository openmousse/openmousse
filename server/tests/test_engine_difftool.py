"""第 3 批第 12 项：diff（engine/difftool.py）。不写 engine.tools.diff 就没有；开了：两段文字比；工作区里的文件和它留着的旧版本比
（version、to）；照 host_files 的路径守卫；二进制的只说大小和指纹；太长留头尾；page: true 做成一页（分类变成 undo），同名再发是新的一版。
数据都是编的。"""
from __future__ import annotations

import asyncio

import pytest

import engine
from engine import difftool, host_files, tools
from engine.loop import ToolFailure


def run(coro):
    return asyncio.run(coro)


def ctx(agent: str = "main") -> engine.ToolCtx:
    return engine.ToolCtx(turn="t", kind="owner", agent=agent, conversation=agent, call_id="c1", control=engine.Control(), origin="user")


def tool() -> engine.Tool | None:
    return next((t for t in tools.for_agent("main") if t.name == "diff"), None)


def diff(**args) -> str:
    return run(tool().run(args, ctx()))


@pytest.fixture
def ws(tmp_path, monkeypatch, server_json):
    from config import settings
    w = tmp_path / "main-ws"
    w.mkdir()
    monkeypatch.setattr(settings, "workspace", w)
    server_json({"claw": {"kind": "builtin"}, "engine": {"tools": {"diff": True}}})
    return w


def test_off_by_default(server_json):
    server_json({"claw": {"kind": "builtin"}})
    assert tool() is None
    server_json({"claw": {"kind": "builtin"}, "engine": {"tools": {"diff": True}}})
    t = tool()
    assert t is not None and t.kinds == ("owner",)
    assert t.level({"before": "a", "after": "b"})[0] == "read" and t.level({"before": "a", "after": "b", "page": True})[0] == "undo"


def test_two_texts(ws):
    out = diff(before="milk\neggs\nbread\n", after="milk\noat milk\nbread\njam\n", path="shopping.txt")
    assert out.splitlines()[:2] == ["--- a/shopping.txt", "+++ b/shopping.txt"]
    assert "-eggs" in out and "+oat milk" in out and "+jam" in out and "(+2 −1 lines)" in out
    assert diff(before="same\n", after="same\n").startswith("No differences")
    with pytest.raises(ToolFailure, match="both before and after"):
        diff(before="only one")
    with pytest.raises(ToolFailure, match="Give before"):
        diff()


def test_long_diffs_keep_the_start_and_the_end(ws):
    a = "\n".join(f"line {i}" for i in range(3000))
    b = "\n".join(f"line {i} changed" for i in range(3000))
    out = diff(before=a, after=b)
    assert len(out) < difftool.OUT_MAX + 600 and "-line 0" in out and "+line 2999 changed" in out
    assert "diff lines not shown" in out and "page: true" in out


def test_a_file_against_its_kept_versions(ws):
    host_files.write("main", "notes/plan.md", "# Plan\n- walk the fictional dog\n")
    host_files.write("main", "notes/plan.md", "# Plan\n- walk the fictional dog\n- water the plants\n")
    host_files.edit("main", "notes/plan.md", [("walk the fictional dog", "walk the dog at 7")])
    out = diff(path="notes/plan.md")  # 最近留的那一版（改之前）比现在
    assert "--- notes/plan.md (v3, kept " in out and "+++ notes/plan.md (now)" in out
    assert "-- walk the fictional dog" in out and "+- walk the dog at 7" in out and "water the plants" in out
    first = diff(path="notes/plan.md", version="v1", to="v2")  # 两个旧版本：v1 那时还没有这个文件
    assert "--- /dev/null" in first and "+# Plan" in first
    with pytest.raises(ToolFailure, match=r"no version v9 .*kept: v3, v2, v1"):
        diff(path="notes/plan.md", version="v9")
    (ws / "fresh.txt").write_text("never kept\n")
    with pytest.raises(ToolFailure, match="No old versions"):
        diff(path="fresh.txt")
    with pytest.raises(ToolFailure, match="No .. in paths"):
        diff(path="../outside.txt")
    with pytest.raises(ToolFailure, match="looks like a secret"):
        diff(path=".env")
    (ws / "notes" / "plan.md").unlink()
    gone = diff(path="notes/plan.md")
    assert "+++ /dev/null" in gone and "-# Plan" in gone  # 现在删了


def test_binary_files_only_say_size_and_hash(ws):
    host_files.write("main", "pic.bin", "placeholder")
    (ws / "pic.bin").write_bytes(b"\x89PNG\x00\x01fictional")
    out = diff(path="pic.bin")
    assert out.startswith("Binary files differ: pic.bin (v1") and "sha256" in out


def test_page_for_the_user(ws):
    import pages
    out = diff(before="tea\ncake\n", after="tea\nscones\n", path="menu.txt", page=True, title="Menu changes")
    assert 'Page "Menu changes" v1' in out and "-cake" in out
    with pages._lock, pages.pdb() as conn:
        p = conn.execute("SELECT * FROM pages WHERE agent='main' AND name='diff-menu-txt' AND deleted_at IS NULL").fetchone()
        v = conn.execute("SELECT html FROM page_versions WHERE page=? ORDER BY version DESC LIMIT 1", (p["id"],)).fetchone()
    assert p["title"] == "Menu changes" and 'class="l del">-cake</span>' in v["html"] and 'class="l add">+scones</span>' in v["html"]
    again = diff(before="tea\ncake\n", after="tea\nscones\n", path="menu.txt", page=True, title="Menu changes")
    assert "v1 (same as the last version)" in again
    newer = diff(before="tea\ncake\n", after="tea\nscones & jam\n", path="menu.txt", page=True, title="Menu changes")
    assert 'Page "Menu changes" v2' in newer
    assert difftool.page_name("My Page!", "x") == "my-page" and difftool.page_name(None, "a/b.md") == "diff-a-b-md"
