"""工程看板（engboard.py）：解析交接本、同步进库（做完的日子、改了字的条目认得出）、算三个数和三列。"""
from __future__ import annotations

import os
import time
from datetime import date, datetime, timedelta

import pytest

import engboard as eb
from chat import _lock
from config import TZ

TODAY = datetime.now(TZ).date()
MONDAY = TODAY - timedelta(days=TODAY.weekday())


def md(d: date) -> str:
    return f"{d.month}/{d.day}"


SAMPLE = f"""# 待办事项

> 顶上的说明，不算任何一节

## 主线（{md(TODAY)} Leo 定）：第一步把内核全部做完 → 第二步群聊和 Agents

**第一步：内核全部做完（约 10–12 个 session）**
- [x] 1 第 0 步 · 原型 + 定合同：分支 `engine-builtin`（{md(TODAY)} 合 main）
- [ ] 2 三种回合 + 工具表 + 基本关卡：主人回合也上内核
- [ ] 3 事件流接 app

**第二步：群聊和 Agents（约 4 个 session）**，细节见下面
- [ ] 赛博和通讯录（新建流程）

## 待办合进 Grava「今天」：三类 + 工程看板（{md(TODAY)} 伦敦 · CC）

- **下一个 session 从这里开始**：开分支
- [x] 三件有期限的已加进 Grava
- [ ] ① 服务器（Leo 改过的记在 schedule_marks，以他为准）：待办带类别；重启前先问 Leo
- [ ] ③ 搬那 29 件：搬完在 todos.md 原地写「→ 已搬到 Grava（item:…）」，不打勾
- [ ] 等 Leo 定：A / B / C
- [ ] 拍板 · 正式 CLA、注册商标 （出处：下一个 session（9/28 16:35））
- [ ] 旧条目 → 已搬到 Grava（item:ev-1）
- [ ] ~~原来的想法~~（不做了：换了做法）

```
- [ ] 代码块里的不算
```

## 设计系统（{md(TODAY - timedelta(days=30))} 起）

- [x] 盘点现状
- [ ] Leo 看样张
"""


@pytest.fixture
def board_env(tmp_path, server_json):
    todos = tmp_path / "todos.md"
    todos.write_text(SAMPLE, encoding="utf8")
    server_json({"timezone": str(TZ), "user_name": "Leo", "today": {"engineering": {"todos": str(todos)}}})
    with _lock, eb.edb() as conn:
        conn.executescript("DELETE FROM eng_lines; DELETE FROM eng_items; DELETE FROM eng_sync;")
    return todos


def test_parse_lines_and_states():
    lines = eb.parse(SAMPLE, TODAY, names=("Leo",))
    names = [x.name for x in lines]
    assert names == ["主线", "待办合进 Grava「今天」", "设计系统"]
    main = lines[0]
    assert main.main and [i.group for i in main.items][:1] == ["第一步：内核全部做完（约 10–12 个 session）"]
    assert [i.state for i in main.items] == ["done", "todo", "todo", "todo"]
    todo = lines[1]
    assert todo.start_here and todo.day == TODAY
    states = [i.state for i in todo.items]
    # 括号里的「Leo 改过的」、做事规矩「重启前先问 Leo」不算等你；「」里引的「→ 已搬到」不算搬走
    assert states == ["done", "todo", "todo", "wait", "wait", "moved", "dropped"]
    assert len(todo.items) == 7  # 代码块里的不算


def test_moved_notes():
    assert eb.item_state(False, "SOP：等 Jay 改完 → 已搬到 Grava（item:ev-1），以后状态看 Grava") == "moved"
    assert eb.item_state(False, "杰哥推荐信 → 剩下的已搬到 Grava（item:ev-2）") == "moved"
    assert eb.item_state(False, "QDA 小组展示 → 已在 Grava（Canvas：QDA Group assignment）") == "moved"
    assert eb.item_state(False, "批阅完出下一版 CV → 不搬：还没投的几家在 Grava 求职表里") == "moved"
    # 括号里说的是一半搬走了，这一条还开着
    assert eb.item_state(False, "Notion 没接（体脂这一半 → 已搬到 Grava（item:ev-3）；Notion 那一半是工程，留在这里）") == "todo"


def test_line_names():
    assert eb.line_name("开发设备分工：Mac、服务器、GitHub（10/15 · CC）") == "开发设备分工"
    assert eb.line_name("OpenMousse：第一个朋友试用后的四项优化（10/5）") == "OpenMousse：第一个朋友试用后的四项优化"
    assert eb.line_name("求职：Dymon（10/3 · CC）") == "求职：Dymon"
    assert eb.line_name("下一个 session 从这里开始：OpenMousse 界面重做（10/13）") == "OpenMousse 界面重做"
    assert eb.line_name("2026-09-26 新增：学习台 + Canvas（Claude Code）") == "学习台 + Canvas"


def test_owner_name_comes_from_config():
    # 没设 user_name：只认「等你」「拍板 ·」这类，不认名字
    assert eb.item_state(False, "等 Leo 定：A / B", names=()) == "todo"
    assert eb.item_state(False, "等你定：A / B", names=()) == "wait"
    assert eb.item_state(False, "等 Ada 定：A / B", names=("Ada",)) == "wait"


def test_short_title(server_json):
    server_json({"user_name": "Leo"})
    assert eb.short_title("1 第 0 步 · 原型 + 定合同：分支 `engine-builtin`，`server/engine/`（循环、会话树、插话 / 中止）+ Anthropic 和 OpenAI 兼容两家接口；名片 Agent") \
        == "第 0 步 · 原型 + 定合同"
    assert eb.short_title("拍板 · 正式 CLA、律师看商业授权、注册商标 （出处：下一个 session（9/28 16:35））") == "正式 CLA、律师看商业授权、注册商标"
    assert eb.short_title("**Leo：轮换 Notion token**（他说自己来）：Notion → Settings") == "轮换 Notion token"
    assert eb.short_title("后台任务：派活、台账、做完回到原对话") == "后台任务：派活、台账、做完回到原对话"


def test_sync_and_board(board_env):
    assert eb.sync() is None
    b = eb.board()
    assert b["main"] == [{"name": "内核", "done": 1, "total": 3}, {"name": "群聊和 Agents", "done": 0, "total": 1}]
    assert "三种回合 + 工具表 + 基本关卡" in b["mainNote"]
    # 这周做完：主线第 0 步（条目里写了今天）+ 三件有期限的（标题里的日子是今天）；设计系统那条是 30 天前的节
    assert b["weekDone"] == 2
    assert b["todo"] == 5 and b["wait"] == 3  # 主线的 3 条 + ① ③；等 Leo 定、拍板、Leo 看样张
    by = {x["name"]: x for x in b["lines"]}
    assert by["待办合进 Grava「今天」"]["status"] == "doing"
    assert (by["待办合进 Grava「今天」"]["done"], by["待办合进 Grava「今天」"]["total"]) == (1, 5)
    assert "设计系统" in {x["name"] for x in b["older"]}  # 30 天前的线收进「更早的线」
    cols = {c["key"]: c for c in b["columns"]}
    assert cols["wait"]["count"] == 3 and cols["done"]["count"] == 2
    assert cols["wait"]["cards"][0]["tags"][0]["text"] == "待办合进 Grava「今天」"


def bump(p, text: str) -> None:
    p.write_text(text, encoding="utf8")
    st = p.stat()
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))  # 同一秒里改两次也要认得出


def test_tick_keeps_identity_and_dates(board_env):
    eb.sync()
    with _lock, eb.edb() as conn:
        before = {r["text"]: r["id"] for r in conn.execute("SELECT id, text FROM eng_items")}
    text = SAMPLE.replace("- [ ] 3 事件流接 app", "- [x] 3 事件流接 app（打勾补了一句证据：提交 abc1234）")
    bump(board_env, text)
    assert eb.sync() is None
    with _lock, eb.edb() as conn:
        r = conn.execute("SELECT * FROM eng_items WHERE text LIKE '3 事件流接 app%'").fetchone()
    assert r["id"] == before["3 事件流接 app"]  # 补了证据还是同一条
    assert r["state"] == "done" and r["done_at"] == TODAY.isoformat()  # 上次同步时还没勾：不早于上次同步那天
    assert eb.board()["main"][0]["done"] == 2


def test_removed_section_keeps_done(board_env):
    eb.sync()
    text = SAMPLE.split("## 待办合进")[0] + "## 设计系统" + SAMPLE.split("## 设计系统")[1]
    bump(board_env, text)
    assert eb.sync() is None
    b = eb.board()
    assert b["weekDone"] == 2  # 整节挪走了，那节做完的照样算
    assert b["wait"] == 1      # 那节没做完的不再算
    assert "待办合进 Grava「今天」" in {x["name"] for x in b["older"]}


def test_unchanged_file_is_not_reparsed(board_env, monkeypatch):
    eb.sync()
    calls = []
    monkeypatch.setattr(eb, "parse", lambda *a: calls.append(a) or [])
    time.sleep(0.01)
    assert eb.sync() is None
    assert calls == []
