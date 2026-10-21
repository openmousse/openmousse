"""内核第 7 项：记忆检索（engine/recall.py）。索引在 tmp 里；对话记录写进测试共用的 mousse.db（每个测试用自己的对话和词）。"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

import bridges
import chat
import engine
from engine import recall, tools
from engine.session import MemorySession, user
from stored_values import ASSISTANT_ROLE
from test_engine import FakeModel

SERVER = Path(__file__).resolve().parent.parent


def run(coro):
    return asyncio.run(coro)


def write(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf8")
    return p


def say(thread: str, role: str, text: str, ts: str = "2026-10-10T10:00:00+00:00") -> int:
    with chat._lock, chat.db() as conn:
        return conn.execute("INSERT INTO messages(thread, role, text, ts) VALUES(?,?,?,?)", (thread, role, text, ts)).lastrowid


def unsay(mid: int) -> None:
    with chat._lock, chat.db() as conn:
        conn.execute("DELETE FROM messages WHERE id=?", (mid,))


@pytest.fixture
def env(tmp_path, monkeypatch, server_json):
    """主对话的工作区 + 两个 Agent（rcfit、rcdiet）各自的工作区；索引在 tmp_path/recall.db。cfg(**recall) 改 engine.recall。"""
    from config import settings
    ws, fit, diet = tmp_path / "ws", tmp_path / "fit", tmp_path / "diet"
    for d in (ws, fit, diet):
        (d / "memory").mkdir(parents=True)
    monkeypatch.setattr(settings, "workspace", ws)
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")

    def cfg(levels: dict | None = None, **rc):
        rc = {"db": str(tmp_path / "recall.db"), "tree": False, **rc}  # 世界树默认不叫（要的测试自己打开、换成假的）
        server_json({"agent_workspaces": {"rcfit": str(fit), "rcdiet": str(diet)},
                     "engine": {"recall": rc, **({"tools": {"levels": levels}} if levels else {})}})
    cfg()
    return SimpleNamespace(ws=ws, fit=fit, diet=diet, tmp=tmp_path, cfg=cfg, settings=settings)


def found_text(q: str, agent: str = "main", **kw) -> str:
    return recall.render(recall.search(q, agent, **kw))


# —— 切字、拆查询、切段 ——————————————————————————————————————————————————————————

def test_segment_cuts_cjk_into_overlapping_pairs():
    assert recall.segment("修改youer服务端").split() == ["修改", "youer", "服务", "务端"]
    assert recall.segment("猫").split() == ["猫"]
    assert recall.segment("メモリ検索").split() == ["メモ", "モリ", "リ検", "検索"]
    assert recall.segment("ＰＤＦ v2.4，第１版").split() == ["PDF", "v2.4,", "第", "1", "版"]  # NFKC：全角变半角


def test_parse_groups_quotes_or_not_and_lone_chars():
    groups = recall.parse('"饮食 计划" 训练 OR 猫 NOT 狗 deploy*')
    assert [[(t.text, t.neg, t.prefix) for t in g] for g in groups] == [
        [("饮食 计划", False, False), ("训练", False, False)],
        [("猫", False, False), ("狗", True, False), ("deploy", False, True)]]
    assert groups[1][0].like and not groups[0][1].like and recall.Term("第3次").like
    assert recall.parse("NOT 狗") == [] and recall.parse("_ ？") == []
    assert [[t.text for t in g] for g in recall.relaxed(recall.parse("简历 版本"))] == [["简历"], ["版本"]]
    assert recall.relaxed(recall.parse("简历 NOT 版本")) is None


def test_chunks_keep_line_numbers_entries_and_overlap():
    curated = "# 记忆\n\n- 第一条\n  - 子条目\n- 第二条\n\n## 别的\n- 第三条\n"
    got = recall.chunk_lines(curated, curated=True)
    assert [(a, b, h) for a, b, h, _ in got] == [(1, 1, "记忆"), (3, 4, "记忆"), (5, 5, "记忆"), (7, 7, "别的"), (8, 8, "别的")]
    long = "\n".join(f"line {i} " + "x" * 90 for i in range(1, 60))
    parts = recall.chunk_lines(long)
    assert len(parts) > 2 and all(len(t.encode()) <= recall.CHUNK + 200 for *_, t in parts)
    assert parts[1][0] <= parts[0][1]  # 第二段带着第一段末尾的几行
    assert recall.chunk_lines("一行" * 2000)[0][:2] == (1, 1)  # 一行太长：切成几块，行号不变


# —— 搜 ——————————————————————————————————————————————————————————————————————

def test_search_finds_chinese_english_and_mixed_with_sources(env):
    write(env.ws / "MEMORY.md", "# 长期记忆\n\n- 训练日 1800 kcal，休息日 1500 kcal\n- Mira 养了一只龟，叫团子\n")
    write(env.ws / "memory" / "2026-10-01.md", "# 2026-10-01\n\n## 上午\nWent running with Noor in Wrenford Common.\n\n## 日结\n修改youer服务端的配置\n")
    write(env.ws / "memory" / "context" / "sleep-notes.md", "# 睡眠\n恢复分 = HRV 40 / 静息心率 25 / 睡眠 25\n")
    mid = say("main", ASSISTANT_ROLE, "海报用 v2.4 的蓝色版，Quokkafund 那份已经交了。", "2026-10-09T19:42:00+00:00")
    out = found_text("训练日")
    assert "[1] MEMORY.md · line 3 ·" in out and "3: - 训练日 1800 kcal，休息日 1500 kcal" in out  # 原文的全角标点照原样
    assert "memory/2026-10-01.md · lines 3–4 · 2026-10-01 · # 上午" in found_text("run")  # porter：run 找到 running
    assert "7: 修改youer服务端的配置" in found_text("youer")  # 字母和汉字挨着也分得开
    assert "MEMORY.md · line 4" in found_text("龟")  # 单个字：退回 LIKE
    assert "memory/context/sleep-notes.md" in found_text("恢复分 睡眠")
    chat_hit = found_text("蓝色版 Quokkafund")
    assert f"chat:main#{mid} · Main chat · 2026-10-09 19:42 · TestMousse" in chat_hit and "Quokkafund 那份已经交了" in chat_hit
    assert "Nothing found" in found_text("完全没有提过的词组合")
    loose = found_text("Quokkafund 完全没提过")  # 都要有的没有：放宽成任一个
    assert "Nothing matched every word" in loose and "Quokkafund" in loose


def test_index_is_incremental_and_forgets_deleted_sources(env):
    write(env.ws / "MEMORY.md", "- 鲸歌岛旅行 计划在十二月\n")
    note = write(env.ws / "memory" / "trip.md", "# trip\nBook the Brindlewick hostel zqxjvelociraptor\n")
    mid = say("main", "user", "提醒我买 Glacierwalk 的票")
    first = recall.sync("main")
    assert first["files"] == 2 and first["messages"] >= 1
    again = recall.sync("main")
    assert again["files"] == 0 and again["messages"] == 0 and again["removed"] == 0  # 没变的不重切
    os.utime(env.ws / "MEMORY.md", (1, 1))  # 只是 mtime 变了：内容指纹一样，不重切
    assert recall.sync("main")["chunks"] == 0
    write(env.ws / "MEMORY.md", "- 鲸歌岛旅行 计划在十二月\n- 极光团订在 12/20\n")
    assert recall.sync("main")["files"] == 1 and "极光团" in found_text("极光团")
    note.unlink()
    unsay(mid)
    gone = recall.sync("main")
    assert gone["removed"] == 2 and gone["messages"] == 0  # 删的是最新一条：不当成库倒回去、不把对话全重读一遍
    assert "Nothing found" in found_text("Brindlewick") and "Nothing found" in found_text("Glacierwalk")
    path = recall.db_path()
    blob = b"".join(Path(f"{path}{s}").read_bytes() for s in ("", "-wal") if Path(f"{path}{s}").exists())
    for word in (b"zqxjvelociraptor", b"Brindlewick", b"brindlewick", b"Glacierwalk", b"glacierwalk"):
        assert word not in blob, word  # 忘掉的字不留在库文件里（原文和索引里的词都没有）
    new = say("main", "user", "Glacierwalk 改到周日了")
    assert recall.sync("main")["messages"] == 1 and f"chat:main#{new}" in found_text("Glacierwalk")
    for suffix in ("-wal", "-shm"):
        Path(f"{path}{suffix}").unlink(missing_ok=True)
    path.write_bytes(b"not a database" * 100)  # 索引文件坏了：删掉重建，照样搜得到
    assert f"chat:main#{new}" in found_text("Glacierwalk")


def test_each_agent_only_sees_its_own_memory_and_chats(env):
    write(env.ws / "MEMORY.md", "- 主对话记着：Marzipanplan 下周开始\n")
    write(env.fit / "MEMORY.md", "- 深蹲最好成绩 100kg，Squatling 计划\n")
    write(env.diet / "memory" / "2026-10-02.md", "# 2026-10-02\n早餐 Oatberry 燕麦 60 g\n")
    f_msg = say("rcfit", "user", "Squatling 今天练腿")
    m_msg = say("main", "user", "Marzipanplan 的预算多少")
    write(env.tmp / "data" / "digest" / "rcfit" / "2026-10-03.md", "今天 Squatling 练完了，状态好\n")
    assert "Squatling" in found_text("Squatling", "rcfit") and "Nothing found" in found_text("Marzipanplan", "rcfit")
    assert "Nothing found" in found_text("Squatling", "main") and "Nothing found" in found_text("Oatberry", "rcfit")
    assert "Oatberry" in found_text("Oatberry", "rcdiet") and "Nothing found" in found_text("Oatberry", "main")
    fit_all = found_text("Squatling", "rcfit", limit=10)
    assert "digest:rcfit/2026-10-03.md" in fit_all and f"chat:rcfit#{f_msg}" in fit_all
    # 读原文也卡范围
    with pytest.raises(recall.RecallError):
        recall.get(f"chat:main#{m_msg}", "rcfit")
    with pytest.raises(recall.RecallError):
        recall.get("digest:rcfit/2026-10-03.md", "main")
    assert "Squatling" in recall.get("MEMORY.md", "rcfit") and "Marzipanplan" in recall.get("MEMORY.md", "main")
    # 主对话放开成全部：别人的工作区出处带 @<id>
    env.cfg(main="all")
    wide = found_text("Squatling", "main", limit=10)
    assert "@rcfit:MEMORY.md" in wide and f"chat:rcfit#{f_msg}" in wide
    assert "Squatling" in recall.get("@rcfit:MEMORY.md", "main")
    with pytest.raises(recall.RecallError):
        recall.get("@main:MEMORY.md", "rcfit")


def test_shared_extras_and_who_gets_them(env):
    prof = write(env.tmp / "shared" / "profile" / "USER.md", "# 档案\n- 时区：Europe/Lisbon\n- 早上喝 Flatwhite\n")
    notes = env.tmp / "vault" / "笔记"
    write(notes / "2026-09-30 读书.md", "《纸船旅馆》读到第三章，Owlchapter\n")
    write(notes / ".hidden.md", "Owlchapter 藏起来的\n")
    write(notes / "data.json", '{"x": "Owlchapter"}')
    env.cfg(extra=[str(prof), {"path": str(notes), "agents": ["main"]}])
    assert "profile:USER.md · line 3" in found_text("Flatwhite", "rcfit")  # 档案大家都有
    hit = found_text("Owlchapter", "main", limit=10)
    assert "笔记:2026-09-30 读书.md" in hit and ".hidden" not in hit and "data.json" not in hit
    assert "Nothing found" in found_text("Owlchapter", "rcfit")  # 限定了只给 main
    assert "纸船旅馆" in recall.get("笔记:2026-09-30 读书.md", "main")
    with pytest.raises(recall.RecallError):
        recall.get("笔记:2026-09-30 读书.md", "rcfit")
    env.cfg()  # 不写 extra：默认只有 settings.profile（这里不存在）；拿掉的共享项下次同步整个清掉
    assert recall.sync("main")["removed"] >= 2 and "Nothing found" in found_text("Owlchapter", "main")


def test_dates_after_before_decay_and_this_conversation_today(env):
    old = (date.fromisoformat(recall.today()) - timedelta(days=90)).isoformat()
    new = (date.fromisoformat(recall.today()) - timedelta(days=2)).isoformat()
    write(env.ws / "memory" / f"{old}.md", f"# {old}\nPelicanroute 第一次提\n")
    write(env.ws / "memory" / f"{new}.md", f"# {new}\nPelicanroute 又提了一次\n")
    write(env.ws / "MEMORY.md", "- Pelicanroute 是常青的记忆\n")
    both = recall.search("Pelicanroute", "main", limit=10)
    days = [h.day for h in both.hits]
    assert days.index(new) < days.index(old)  # 一样相关：新的排前面（老的最多减一半）
    recent = recall.search("Pelicanroute", "main", after="7d")
    assert [h.day for h in recent.hits] == [new]  # 限定时间：常青的、太早的都不要
    assert [h.day for h in recall.search("Pelicanroute", "main", before=new).hits] == [old]
    with pytest.raises(recall.RecallError):
        recall.search("Pelicanroute", "main", after="上周")
    today_ts = recall.datetime.now(env.settings.tz).isoformat()
    say("rc-today", "user", "Pelicanroute 今天在这个对话里说过", today_ts)
    assert "rc-today" in found_text("Pelicanroute", "main", limit=10)
    hidden = found_text("Pelicanroute", "main", limit=10, exclude=("rc-today", recall.today()))
    assert "rc-today" not in hidden and "Pelicanroute" in hidden  # 已经在上下文里的不搜


def test_search_skips_only_what_is_still_in_context(env):
    """这一轮的会话压缩过：压掉的那几句照样能搜，还在上下文里的不搜。"""
    from engine import events
    from engine.session import DbSession, day_key
    now = recall.datetime.now(env.settings.tz).isoformat()
    m1 = say("rc-ctx", "user", "Lynxwatch 第一次说起", now)
    m2 = say("rc-ctx", "user", "Lynxwatch 第二次说起", now)
    m3 = say("rc-ctx", "user", "Lynxwatch 现在问", now)
    s = DbSession.create(day_key("rc-ctx", recall.today()), kind="owner", agent="main")
    s.append({**user("Lynxwatch 第一次说起"), "ref": m1})
    s.append({"role": "assistant", "content": [{"type": "text", "text": "好"}], "stop": "end"})
    kept = s.append({**user("Lynxwatch 第二次说起"), "ref": m2})
    s.append({"summary": "说过一次 Lynxwatch", "first_kept": kept}, kind="compaction")
    s.append({**user("Lynxwatch 现在问"), "ref": m3})
    tid = events.turn_open(conversation="rc-ctx", kind="owner", model="deepseek/x", session=s.id, agent="main")
    ctx = engine.ToolCtx(turn=tid, kind="owner", agent="main", conversation="rc-ctx", call_id="1", control=engine.Control())
    assert recall._in_context(ctx) == ("rc-ctx", m2)
    ms = next(t for t in tools.for_agent("main") if t.name == "memory_search")
    out = run(ms.run({"query": "Lynxwatch"}, ctx))
    assert f"chat:rc-ctx#{m1}" in out and f"#{m2}" not in out and f"#{m3}" not in out
    other = engine.ToolCtx(turn="t-none", kind="owner", agent="main", conversation="rc-ctx", call_id="1", control=engine.Control())
    assert recall._in_context(other) == ("rc-ctx", recall.today())  # 找不到这一轮的会话：这个对话今天的都不搜
    assert "Nothing found" in run(ms.run({"query": "Lynxwatch"}, other))


def test_render_respects_the_budget_and_clips_long_lines(env):
    for i in range(30):
        own = " ".join(f"w{i}x{j}" for j in range(6))
        write(env.ws / "memory" / f"topic-{i:02d}.md", f"# topic {i}\nThe Wombatcase item {i} {own} " + "padding words " * 40 + "\n")
    found = recall.search("Wombatcase", "main", limit=20)
    out = recall.render(found, max_chars=1500)
    assert len(out) < 1500 + 400 and "more not shown" in out
    assert "…" in out  # 长行只留命中附近
    assert len(found.hits) == 20 and found.more >= 10


def test_get_reads_lines_with_continuation_and_refuses_outside_paths(env):
    write(env.ws / "memory" / "long.md", "\n".join(f"line {i}" for i in range(1, 151)) + "\n")
    first = recall.get("memory/long.md", "main")
    assert first.startswith("memory/long.md · lines 1–60 of 150") and "60: line 60" in first and "from=61" in first
    assert "100: line 100" in recall.get("memory/long.md#L100", "main", lines=5)
    assert recall.get("memory/long.md", "main", start=148).endswith("150: line 150")
    write(env.ws / "AGENTS.md", "secret rules\n")
    write(env.tmp / "outside.md", "outside\n")
    (env.ws / "memory" / "link.md").symlink_to(env.tmp / "outside.md")
    (env.ws / "memory" / "linkdir").symlink_to(env.tmp)
    for bad in ("AGENTS.md", "../outside.md", "memory/../../outside.md", "/etc/passwd", "memory/link.md", "memory/linkdir/outside.md",
                "memory/.secret.md", "memory/x\x00.md", "chat:nope", "digest:../x.md"):
        with pytest.raises(recall.RecallError):
            recall.get(bad, "main")
    assert "outside" not in found_text("outside", "main")  # 软链也不进索引
    mids = [say("rc-get", r, f"消息 {i} Hummingbirdtalk", f"2026-10-0{i}T09:00:00+00:00") for i, r in
            enumerate(("user", ASSISTANT_ROLE, "user", ASSISTANT_ROLE, "user"), 1)]
    around = recall.get(f"chat:rc-get#{mids[2]}", "main", lines=1)
    assert f"#{mids[1]}" in around and f"#{mids[3]}" in around and f"#{mids[0]}" not in around and "(this one)" in around


# —— 工具 ————————————————————————————————————————————————————————————————————

@pytest.fixture
def fake(monkeypatch):
    from engine import providers

    def make(script):
        f = FakeModel(script)
        monkeypatch.setattr(providers, "stream", f.stream)
        return f
    return make


def test_tools_are_offered_read_only_owner_only_and_run(env, fake, monkeypatch):
    env.cfg(tree=True)
    cmd = bridges.command  # 先当这台机器上没有 mousse-tree（CI 装了 ./tree，venv 里就有这个命令）
    monkeypatch.setattr(bridges, "command", lambda n: None if n == "tree" else cmd(n))
    write(env.ws / "MEMORY.md", "- Mia 的 Toucanplan 定在 11 月\n")
    names = {t.name for t in tools.for_agent("main")}
    assert {"memory_search", "memory_get"} <= names and "memory_search" in {t.name for t in tools.for_agent("rcfit")}
    ms = next(t for t in tools.for_agent("main") if t.name == "memory_search")
    assert ms.kinds == ("owner",) and ms.level({"query": "x"})[0] == "read" and "memory-tree" not in ms.description  # 没有 tree 工具
    ctx = engine.ToolCtx(turn="t", kind="owner", agent="main", conversation="t-recall", call_id="1", control=engine.Control())
    assert run(tools.gate(ms, {"query": "x"}, ctx)) is None  # 只读：关卡直接放
    # 世界树：有 tree 工具就顺手 recall，附在后面
    real_cmd, real_run = bridges.command, bridges.run
    monkeypatch.setattr(bridges, "command", lambda n: ["mousse-tree"] if n == "tree" else real_cmd(n))
    calls = []

    async def fake_run(name, args, stdin, agent, *, kill_on_cancel=False):
        calls.append((name, list(args), agent))
        if name == "tree":
            return "- [t1] Mia 喜欢 Toucanplan 这种安排 (claude, 2026-09-30)"
        return await real_run(name, args, stdin, agent, kill_on_cancel=kill_on_cancel)
    monkeypatch.setattr(bridges, "run", fake_run)
    f = fake([{"calls": [("c1", "memory_search", {"query": "Toucanplan"})]},
              {"calls": [("c2", "memory_get", {"ref": "MEMORY.md"})]}, {"text": "11 月。"}])
    spec = engine.Spec(kind="owner", model="deepseek/x", tools=tools.for_agent("main"), gate=tools.gate, agent="main", conversation="t-recall")
    assert "memory-tree entries" in next(t for t in spec.tools if t.name == "memory_search").description
    res = run(engine.run_turn(spec, MemorySession(), user("Toucanplan 什么时候？")))
    outs = [m for m in res.messages if m["role"] == "tool"]
    assert res.status == "done" and not outs[0]["error"] and not outs[1]["error"]
    text = outs[0]["content"][0]["text"]
    assert "[1] MEMORY.md · line 1" in text and "From the memory tree" in text and "[t1] Mia 喜欢 Toucanplan" in text
    assert ("tree", ["recall", "--q", "Toucanplan", "--limit", "5"], "main") in calls
    assert "1: - Mia 的 Toucanplan 定在 11 月" in outs[1]["content"][0]["text"]
    assert any(t["name"] == "memory_search" for t in f.requests[0][1].tools)
    # 赛博回合看不到；server.json 能关
    f2 = fake([{"text": "hi"}])
    run(engine.run_turn(engine.Spec(kind="cyber", model="deepseek/x", tools=tools.for_agent("main"), gate=tools.gate), MemorySession(), user("hi")))
    assert not any(t["name"].startswith("memory_") for t in f2.requests[0][1].tools)
    env.cfg(levels={"memory_get": "off"})
    names = {t.name for t in tools.for_agent("main")}
    assert "memory_search" in names and "memory_get" not in names


def test_tool_refusals_come_back_as_tool_errors(env, fake):
    f = fake([{"calls": [("c1", "memory_get", {"ref": "../../etc/passwd"}), ("c2", "memory_search", {"query": "x", "after": "昨天"})]},
              {"text": "ok"}])
    spec = engine.Spec(kind="owner", model="deepseek/x", tools=tools.for_agent("rcfit"), gate=tools.gate, agent="rcfit", conversation="rcfit")
    res = run(engine.run_turn(spec, MemorySession(), user("x")))
    outs = [m for m in res.messages if m["role"] == "tool"]
    assert all(m["error"] for m in outs) and "Can't read" in outs[0]["content"][0]["text"] and "Can't read this time" in outs[1]["content"][0]["text"]
    assert f.requests


def test_cli_search_and_get(tmp_path):
    ws = tmp_path / "ws"
    write(ws / "MEMORY.md", "- 命令行也能搜：Narwhalnote 在这里\n")
    cfgp = tmp_path / "server.json"
    cfgp.write_text('{"app_name":"T","timezone":"UTC","language":"zh","openclaw_home":"%s","workspace":"%s","data_dir":"%s"}'
                    % (tmp_path / "oc", ws, tmp_path / "data"), encoding="utf8")
    env = {**os.environ, "MOUSSE_SERVER_CONFIG": str(cfgp)}
    out = subprocess.run([sys.executable, str(SERVER / "engine" / "recall.py"), "search", "Narwhalnote"], capture_output=True, text=True,
                         env=env, cwd=tmp_path, timeout=60)
    assert out.returncode == 0, out.stderr
    assert "[1] MEMORY.md · 第 1 行" in out.stdout and "Narwhalnote" in out.stdout and str(tmp_path / "data" / "recall.db") in out.stderr
    got = subprocess.run([sys.executable, str(SERVER / "engine" / "recall.py"), "get", "MEMORY.md"], capture_output=True, text=True,
                         env=env, cwd=tmp_path, timeout=60)
    assert got.returncode == 0 and "1: - 命令行也能搜" in got.stdout
    st = subprocess.run([sys.executable, str(SERVER / "engine" / "recall.py"), "reindex", "--full"], capture_output=True, text=True,
                        env=env, cwd=tmp_path, timeout=60)
    assert st.returncode == 0 and "files=1" in st.stdout


def test_truncated_memory_in_the_opening_points_to_memory_get(env):
    from engine import context
    write(env.ws / "MEMORY.md", "- HEAD\n" + "长期记忆 " * 8000 + "\n- TAIL\n")
    mem = next(p for p in context.file_parts(env.ws) if p.key == "file:MEMORY.md").text
    assert "read the rest with memory_get (ref MEMORY.md)" in mem and "- HEAD" in mem and "- TAIL" in mem
    env.cfg(levels={"memory_get": "off"})  # 关了 memory_get 就不提
    assert "memory_get" not in next(p for p in context.file_parts(env.ws) if p.key == "file:MEMORY.md").text
