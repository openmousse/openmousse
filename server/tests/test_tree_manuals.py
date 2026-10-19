"""开源版世界树 0.5 的手册（openmousse_tree.manuals / match / versions）：两种存法读写一样、手册不是叶子、改之前留版本、找（名字和
「什么时候用」比正文要紧）、四行指针、命令行、MCP 的 find_manual / read_manual。全在临时目录里（conftest 的 mousse_tree），手册都是编的。"""
from __future__ import annotations

import asyncio
import json
import sys

import pytest
from openmousse_tree import cli
from openmousse_tree import config as C
from openmousse_tree import manuals as MA
from openmousse_tree import match as M
from openmousse_tree import store as S
from openmousse_tree import versions as V

BOTH = pytest.mark.parametrize("storage", ["sqlite", "markdown"])
WRITING = {"name": "写文书", "when": "丰富文书素材、改 SOP、准备推荐信材料", "about": "按用户的经历和口吻写、改各类文书",
           "body": "1. 先问清楚是哪份文书、给谁看\n2. 读用户给的素材，列提纲\n3. 按用户的口吻写，改完标出改了哪里"}
TRAINING = {"name": "记录训练", "when": "练完了记一下、记录今天的训练、深蹲卧推做了几组、log a workout", "about": "把一次训练按动作、组数、重量记进训练表",
            "body": "每个动作一行：动作、组数、次数、重量。"}
MEETING = {"name": "准备会议", "when": "明天开会、准备会议材料、prepare for a meeting", "about": "开会前把议程、材料和要问的问题整理好",
           "body": "列出议程，每一项写要问的问题。"}


def put(conn, *ms: dict) -> None:
    for m in ms:
        MA.add(conn, actor="owner", **m)


def run_cli(monkeypatch, capsys, *argv: str, stdin: str | None = None, agent: str | None = None) -> str:
    if agent is None:
        monkeypatch.delenv("MOUSSE_AGENT", raising=False)
    else:
        monkeypatch.setenv("MOUSSE_AGENT", agent)
    if stdin is not None:
        import io
        monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    monkeypatch.setattr(sys, "argv", ["mousse-tree", *argv])
    cli.main()
    return capsys.readouterr().out


# —— 两种存法 ——————————————————————————————————————————————————————————————————

@BOTH
def test_two_storages_read_and_write_the_same(mousse_tree, storage):
    mousse_tree.make(storage, lang="zh")
    conn = S.connect()
    put(conn, WRITING, TRAINING)
    got = MA.get(conn, "写文书")
    assert got["when"] == ["丰富文书素材", "改 SOP", "准备推荐信材料"] and got["about"].startswith("按用户的经历") and "列提纲" in got["body"]
    assert got["status"] == "active" and got["agents"] == []
    assert [m["name"] for m in MA.all_manuals(conn)] == ["写文书", "记录训练"] or [m["name"] for m in MA.all_manuals(conn)] == ["记录训练", "写文书"]
    with pytest.raises(MA.ManualError):
        MA.add(conn, actor="owner", **{**WRITING, "body": "again"})  # 重名（不分大小写、空白）
    with pytest.raises(MA.ManualError):
        MA.add(conn, actor="owner", **{**WRITING, "name": "写/文书"})  # 名字要能当文件名
    with pytest.raises(MA.ManualError):
        MA.add(conn, actor="owner", **{**MEETING, "body": "  "})  # 没有正文
    r = MA.edit(conn, "写文书", when="改 SOP、润色简历", actor="owner")
    assert r["version"] == "v1" and r["manual"]["when"] == ["改 SOP", "润色简历"]
    r = MA.edit(conn, "写文书", rename="改文书", actor="owner")
    assert MA.get(conn, "写文书") is None and MA.get(conn, "改文书")["when"] == ["改 SOP", "润色简历"]
    assert [v["id"] for v in MA.history("改文书")] == ["v2", "v1"]  # 版本跟着新名字走
    MA.set_status(conn, "改文书", "disabled", "owner")
    assert MA.get(conn, "改文书", disabled=False) is None and MA.get(conn, "改文书")["status"] == "disabled"
    assert "改文书" not in [m["name"] for m in MA.all_manuals(conn)] and MA.read(conn, "改文书", platform="claude") is None
    vid = MA.remove(conn, "改文书", "owner")
    assert MA.get(conn, "改文书") is None
    back = MA.restore(conn, "改文书", "v1", "owner")["manual"]  # v1 = 第一次改之前的样子
    assert back["name"] == "写文书" and back["when"] == ["丰富文书素材", "改 SOP", "准备推荐信材料"] and back["status"] == "active"
    assert MA.parse(V.read("manuals", "改文书", vid) or "")["status"] == "disabled"  # 删之前那一版是停用着的


@BOTH
def test_manuals_are_not_leaves(mousse_tree, storage):
    mousse_tree.make(storage)
    conn = S.connect()
    S.add(conn, text="Alex rows on Sundays", source="owner")
    put(conn, TRAINING)
    conn = S.connect()  # Markdown 存储：再看一眼笔记文件夹
    rows = conn.execute("SELECT text FROM tree WHERE source != 'profile'").fetchall()
    assert [r["text"] for r in rows] == ["Alex rows on Sundays"]
    from openmousse_tree import prune as P
    assert P.context(conn)["leaves"] == 1
    assert "每个动作一行" not in S.export(conn).read_text(encoding="utf8")
    if storage == "markdown":
        f = C.notes_dir() / C.MANUALS / "记录训练.md"
        assert f.is_file() and "when:" in f.read_text(encoding="utf8")
        from openmousse_tree import notes as N
        assert N.issues(conn) == []


def test_markdown_edit_keeps_the_users_own_properties(mousse_tree):
    mousse_tree.make("markdown")
    conn = S.connect()
    put(conn, TRAINING)
    f = C.notes_dir() / C.MANUALS / "记录训练.md"
    f.write_text(f.read_text(encoding="utf8").replace("status: active", "status: active\ncolor: blue"), encoding="utf8")
    MA.edit(conn, "记录训练", about="把训练记进表", actor="owner")
    text = f.read_text(encoding="utf8")
    assert "color: blue" in text and '"把训练记进表"' in text


def test_a_note_written_by_hand_in_obsidian_counts(mousse_tree):
    mousse_tree.make("markdown")
    conn = S.connect()
    d = C.notes_dir() / C.MANUALS
    d.mkdir(parents=True)
    (d / "Weekly review.md").write_text("---\nwhen: [plan my week, weekly review]\ndescription: Sunday planning\n---\nLook back, then plan.\n",
                                        encoding="utf8")
    (d / "broken.md").write_text("---\nwhen: [unclosed\n", encoding="utf8")
    got = MA.get(conn, "weekly review")
    assert got["name"] == "Weekly review" and got["when"] == ["plan my week", "weekly review"] and got["about"] == "Sunday planning"
    assert [m["name"] for m in MA.all_manuals(conn)] == ["Weekly review"]  # 格式坏的跳过


def test_versions_skip_identical_copies_and_keep_the_last_few(mousse_tree, monkeypatch):
    mousse_tree.make()
    monkeypatch.setattr(V, "KEEP", 3)
    assert V.keep("manuals", "x", "a", by="t", note="edit") == "v1"
    assert V.keep("manuals", "x", "a", by="t", note="edit") == "v1"  # 和上一版一样：不再存
    for t in "bcde":
        V.keep("manuals", "x", t, by="t", note="edit")
    assert [v["id"] for v in V.history("manuals", "X")] == ["v5", "v4", "v3"] and V.read("manuals", "x", "v5") == "e"
    assert V.read("manuals", "x", "v1") is None and V.read("manuals", "x", "../x") is None


# —— 找 ——————————————————————————————————————————————————————————————————————

def test_find_puts_names_and_when_above_the_body(mousse_tree):
    mousse_tree.make(lang="zh")
    conn = S.connect()
    put(conn, WRITING, TRAINING, MEETING, {"name": "整理书架", "when": "书太乱了", "about": "按类别排书",
                                           "body": "SOP 推荐信 文书 改 SOP 再改一版 SOP SOP"})  # 正文里堆满了词
    got = MA.find(conn, "帮我把 SOP 再改一版")
    assert got[0]["name"] == "写文书" and got[0]["score"] >= 0.8
    assert all(x["score"] < got[0]["score"] - 0.3 for x in got[1:])
    assert MA.find(conn, "log my workout from today")[0]["name"] == "记录训练"
    assert MA.find(conn, "明天下午和导师开会，帮我准备一下")[0]["name"] == "准备会议"
    assert MA.find(conn, "今天天气怎么样") == []
    MA.edit(conn, "准备会议", agents=["g-work"], actor="owner")
    mine = {x["name"]: x["score"] for x in MA.find(conn, "准备材料", agent="g-work")}
    theirs = {x["name"]: x["score"] for x in MA.find(conn, "准备材料")}
    assert mine["准备会议"] > theirs["准备会议"]  # 这个 Agent 自己的优先


def test_terms_and_stems():
    t = M.terms("帮我把 SOP 再改一版")
    assert "sop" in t and "再改" in t and "帮我" not in t and "我" not in t  # 两个字都是虚字的两字词、虚字不要
    assert M.terms("改 SOP")["改"] == M.LONE  # 单独成段的一个字分量大一点
    assert {M.stem(w) for w in ("revise", "revising", "revised")} == {"revis"} and M.stem("logged") == M.stem("logs") == "log"
    assert M.stem("meetings") == "meet" and M.stem("status") == "status"
    assert M.phrases(["改 SOP、润色简历", "改 SOP"]) == ["改 SOP", "润色简历"]


def test_reads_are_counted_by_platform_and_day(mousse_tree):
    mousse_tree.make()
    conn = S.connect()
    put(conn, TRAINING)
    assert MA.last_reads(conn) == {}
    MA.read(conn, "记录训练", platform="claude")
    MA.read(conn, "记录训练", platform="claude")
    MA.read(conn, "记录训练", platform="chatgpt")
    rows = conn.execute("SELECT platform, n FROM manual_read ORDER BY platform").fetchall()
    assert [(r["platform"], r["n"]) for r in rows] == [("chatgpt", 1), ("claude", 2)] and "记录训练" in MA.last_reads(conn)
    MA.edit(conn, "记录训练", rename="Record training", actor="owner")
    assert "record training" in MA.last_reads(conn)  # 改了名字，读过的账跟着走


# —— 指针 ——————————————————————————————————————————————————————————————————

def test_pointer_is_the_four_lines_from_the_canvas(mousse_tree):
    mousse_tree.make(lang="zh")
    conn = S.connect()
    put(conn, WRITING)
    assert MA.pointer(MA.get(conn, "写文书")) == ("## 写文书\n什么时候用：丰富文书素材、改 SOP、准备推荐信材料\n正文：世界树 › 手册 › 写文书\n"
                                                "先取正文再动手；取不到就停下说。")
    text = MA.pointers(MA.all_manuals(conn))
    assert "不许凭那一行说明自己编" in text and text.endswith("先取正文再动手；取不到就停下说。\n")
    cfg = C.load()
    cfg["language"] = "en"
    C.save(cfg)
    en = MA.pointer(MA.get(conn, "写文书")).splitlines()
    assert en[0] == "## 写文书" and en[1].startswith("When to use: 丰富文书素材; 改 SOP") and en[2] == "Body: Memory tree › Manuals › 写文书"
    assert en[3] == "Fetch the body before you start; if you can't, stop and say so."


# —— 命令行 ——————————————————————————————————————————————————————————————————

@BOTH
def test_cli_add_edit_list_show_find_pointers_rm(mousse_tree, storage, monkeypatch, capsys):
    mousse_tree.make(storage, lang="zh")
    out = run_cli(monkeypatch, capsys, "manual", "add", "写文书", "--when", "丰富文书素材、改 SOP", "--when", "准备推荐信材料", "--about",
                  "按用户的口吻写文书", "--file", "-", stdin="先列提纲，再写。")
    assert json.loads(out)["manual"]["when"] == ["丰富文书素材", "改 SOP", "准备推荐信材料"]
    run_cli(monkeypatch, capsys, "manual", "add", "记录训练", "--when", "log a workout", "--about", "记训练", "--body", "一个动作一行。", agent="g-fit")
    assert "按用户的口吻写文书" in run_cli(monkeypatch, capsys, "manual", "list")
    shown = run_cli(monkeypatch, capsys, "manual", "show", "写文书", agent="g-docs")
    assert shown.startswith("# 写文书\n") and "先列提纲，再写。" in shown
    conn = S.connect()
    assert conn.execute("SELECT platform FROM manual_read").fetchone()["platform"] == "openmousse"
    found = json.loads(run_cli(monkeypatch, capsys, "manual", "find", "帮我把 SOP 再改一版", "--json"))
    assert found[0]["name"] == "写文书"
    out = json.loads(run_cli(monkeypatch, capsys, "manual", "edit", "写文书", "--about", "按用户的经历和口吻写、改文书", "--disable"))
    assert out["version"] == "v1" and out["manual"]["status"] == "disabled"
    assert "写文书" not in run_cli(monkeypatch, capsys, "manual", "list") and "（停用）" in run_cli(monkeypatch, capsys, "manual", "list", "--all")
    with pytest.raises(SystemExit) as e:
        run_cli(monkeypatch, capsys, "manual", "show", "写文书")
    assert "没有「写文书」这份手册" in str(e.value) and "不要凭那一行说明自己编" in str(e.value)
    run_cli(monkeypatch, capsys, "manual", "edit", "写文书", "--enable")
    ptr = run_cli(monkeypatch, capsys, "manual", "pointers", "写文书")
    assert "## 写文书\n什么时候用：丰富文书素材、改 SOP、准备推荐信材料\n正文：世界树 › 手册 › 写文书\n先取正文再动手；取不到就停下说。" in ptr
    assert "记录训练" not in ptr and "## 记录训练" in run_cli(monkeypatch, capsys, "manual", "pointers")
    assert "v2" in run_cli(monkeypatch, capsys, "manual", "history", "写文书")
    assert json.loads(run_cli(monkeypatch, capsys, "manual", "rm", "记录训练"))["ok"]
    assert "记录训练" not in run_cli(monkeypatch, capsys, "manual", "list", "--all")
    run_cli(monkeypatch, capsys, "manual", "restore", "记录训练", "v1")
    assert "记录训练" in run_cli(monkeypatch, capsys, "manual", "list")
    activity = [r["actor"] for r in S.connect().execute("SELECT actor FROM activity")]
    assert "g-fit" in activity  # 是哪个 Agent 写的记成它（MOUSSE_AGENT）


# —— MCP ——————————————————————————————————————————————————————————————————

def test_mcp_find_manual_and_read_manual(mousse_tree):
    from openmousse_tree import server
    cfg = mousse_tree.make(lang="en")
    conn = S.connect()
    put(conn, WRITING, TRAINING)
    srv = server.platform_server("claude", C.load())

    def call(tool: str, **args) -> str:
        out = asyncio.run(srv.call_tool(tool, args))
        blocks = out[0] if isinstance(out, tuple) else out
        return "".join(getattr(b, "text", "") for b in blocks)
    tools = {t.name: t for t in asyncio.run(srv.list_tools())}
    for name in ("find_manual", "read_manual"):
        assert "Fetch the body before you act; if you can't get it, stop and say so" in tools[name].description
    assert "find_manual" in server.instructions(cfg) and "never improvise" in server.instructions(cfg)
    found = call("find_manual", query="help me revise my SOP")
    assert found.startswith("- 写文书 (") and "when to use: 丰富文书素材" in found
    body = call("read_manual", name="写文书")
    assert body.startswith("# 写文书\n") and "列提纲" in body
    assert conn.execute("SELECT platform FROM manual_read").fetchone()["platform"] == "claude"
    missing = call("read_manual", name="Write a novel")
    assert missing.startswith('There\'s no manual called "Write a novel"') and "stop and say so" in missing
    assert "No matching manual" in call("find_manual", query="what's the weather")
