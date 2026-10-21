"""工程看板（engboard.py）：解析交接本、同步进库（做完的日子、改了字的条目认得出）、算三个数和三列。"""
from __future__ import annotations

import os
import time
from datetime import date, datetime, timedelta

import pytest
from fastapi import HTTPException

import engboard as eb
from chat import _lock
from config import TZ

TODAY = datetime.now(TZ).date()
MONDAY = TODAY - timedelta(days=TODAY.weekday())


def md(d: date) -> str:
    return f"{d.month}/{d.day}"


SAMPLE = f"""# 待办事项

> 顶上的说明，不算任何一节

## 主线（{md(TODAY)} Mia 定）：第一步把同步层全部做完 → 第二步分享和协作

**第一步：同步层全部做完（约 10–12 个 session）**
- [x] 1 第 0 步 · 原型 + 定接口：分支 `sync-layer`（{md(TODAY)} 合 main）
- [ ] 2 三种冲突 + 合并表 + 基本校验：离线编辑也走同步层
- [ ] 3 事件流接 app

**第二步：分享和协作（约 4 个 session）**，细节见下面
- [ ] 共享清单和成员（新建流程）

## 离线模式合进主线：三类 + 冲突提示（{md(TODAY)} · CC）

- **下一个 session 从这里开始**：开分支
- [x] 三件有期限的已加进日程
- [ ] ① 服务器（Mia 改过的记在 overrides 表，以她为准）：清单带分类；上线前先问 Mia
- [ ] ③ 搬那 29 条旧菜谱：搬完在 todos.md 原地写「→ 已搬到新库（rec:…）」，不打勾
- [ ] 等 Mia 定：A / B / C
- [ ] 拍板 · 换图标、定配色 （出处：下一个 session（9/28 16:35））
- [ ] 旧条目 → 已搬到新库（rec:ev-1）
- [ ] ~~原来的想法~~（不做了：换了做法）

```
- [ ] 代码块里的不算
```

## 设计系统（{md(TODAY - timedelta(days=30))} 起）

- [x] 盘点现状
- [ ] Mia 看样张
"""


@pytest.fixture
def board_env(tmp_path, server_json):
    todos = tmp_path / "todos.md"
    todos.write_text(SAMPLE, encoding="utf8")
    server_json({"timezone": str(TZ), "user_name": "Mia", "today": {"engineering": {"todos": str(todos)}}})
    with _lock, eb.edb() as conn:
        conn.executescript("DELETE FROM eng_lines; DELETE FROM eng_items; DELETE FROM eng_sync;")
    return todos


def test_parse_lines_and_states():
    lines = eb.parse(SAMPLE, TODAY, names=("Mia",))
    names = [x.name for x in lines]
    assert names == ["主线", "离线模式合进主线", "设计系统"]
    main = lines[0]
    assert main.main and [i.group for i in main.items][:1] == ["第一步：同步层全部做完（约 10–12 个 session）"]
    assert [i.state for i in main.items] == ["done", "todo", "todo", "todo"]
    todo = lines[1]
    assert todo.start_here and todo.day == TODAY
    states = [i.state for i in todo.items]
    # 括号里的「Mia 改过的」、做事规矩「上线前先问 Mia」不算等你；「」里引的「→ 已搬到」不算搬走
    assert states == ["done", "todo", "todo", "wait", "wait", "moved", "dropped"]
    assert len(todo.items) == 7  # 代码块里的不算


def test_moved_notes():
    assert eb.item_state(False, "菜谱导入：等接口定了 → 已搬到新库（rec:ev-1），以后状态看那边") == "moved"
    assert eb.item_state(False, "图标素材 → 剩下的已搬到新库（rec:ev-2）") == "moved"
    assert eb.item_state(False, "购物清单 → 已在日程里（提醒：周六买菜）") == "moved"
    assert eb.item_state(False, "改完出下一版设计稿 → 不搬：还没做的几页在设计看板里") == "moved"
    # 括号里说的是一半搬走了，这一条还开着
    assert eb.item_state(False, "日历没接（提醒这一半 → 已搬到新库（rec:ev-3）；日历那一半是工程，留在这里）") == "todo"


def test_line_names():
    assert eb.line_name("测试设备分工：平板、旧手机、模拟器（10/15 · CC）") == "测试设备分工"
    assert eb.line_name("Larder：第一轮试用后的四项优化（10/5）") == "Larder：第一轮试用后的四项优化"
    assert eb.line_name("上架：Acme（10/3 · CC）") == "上架：Acme"
    assert eb.line_name("下一个 session 从这里开始：Larder 界面重做（10/13）") == "Larder 界面重做"
    assert eb.line_name("2026-09-26 新增：购物清单 + 扫码（Claude Code）") == "购物清单 + 扫码"


def test_owner_name_comes_from_config():
    # 没设 user_name：只认「等你」「拍板 ·」这类，不认名字
    assert eb.item_state(False, "等 Mia 定：A / B", names=()) == "todo"
    assert eb.item_state(False, "等你定：A / B", names=()) == "wait"
    assert eb.item_state(False, "等 Ada 定：A / B", names=("Ada",)) == "wait"


def test_short_title(server_json):
    server_json({"user_name": "Mia"})
    assert eb.short_title("1 第 0 步 · 原型 + 定接口：分支 `sync-layer`，`app/sync/`（队列、版本号、重试 / 回滚）+ iOS 和 Android 两端；分享页") \
        == "第 0 步 · 原型 + 定接口"
    assert eb.short_title("拍板 · 换图标、定配色、改应用名 （出处：下一个 session（9/28 16:35））") == "换图标、定配色、改应用名"
    assert eb.short_title("**Mia：轮换测试服务器的密钥**（她说自己来）：控制台 → 设置") == "轮换测试服务器的密钥"
    assert eb.short_title("离线队列：排队、重试、做完回到原页面") == "离线队列：排队、重试、做完回到原页面"


def test_sync_and_board(board_env):
    assert eb.sync() is None
    b = eb.board()
    assert b["main"] == [{"name": "同步层", "done": 1, "total": 3}, {"name": "分享和协作", "done": 0, "total": 1}]
    assert "三种冲突 + 合并表 + 基本校验" in b["mainNote"]
    # 这周做完：主线第 0 步（条目里写了今天）+ 三件有期限的（标题里的日子是今天）；设计系统那条是 30 天前的节
    assert b["weekDone"] == 2
    assert b["todo"] == 5 and b["wait"] == 3  # 主线的 3 条 + ① ③；等 Mia 定、拍板、Mia 看样张
    by = {x["name"]: x for x in b["lines"]}
    assert by["离线模式合进主线"]["status"] == "doing"
    assert (by["离线模式合进主线"]["done"], by["离线模式合进主线"]["total"]) == (1, 5)
    assert "设计系统" in {x["name"] for x in b["older"]}  # 30 天前的线收进「更早的线」
    cols = {c["key"]: c for c in b["columns"]}
    assert cols["wait"]["count"] == 3 and cols["done"]["count"] == 2
    assert cols["wait"]["cards"][0]["tags"][0]["text"] == "离线模式合进主线"


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
    text = SAMPLE.split("## 离线模式")[0] + "## 设计系统" + SAMPLE.split("## 设计系统")[1]
    bump(board_env, text)
    assert eb.sync() is None
    b = eb.board()
    assert b["weekDone"] == 2  # 整节挪走了，那节做完的照样算
    assert b["wait"] == 1      # 那节没做完的不再算
    assert "离线模式合进主线" in {x["name"] for x in b["older"]}


def test_unchanged_file_is_not_reparsed(board_env, monkeypatch):
    eb.sync()
    calls = []
    monkeypatch.setattr(eb, "parse", lambda *a: calls.append(a) or [])
    time.sleep(0.01)
    assert eb.sync() is None
    assert calls == []


def test_line_items(board_env):
    eb.sync()
    b = eb.board()
    lid = next(x["id"] for x in b["lines"] if x["name"] == "离线模式合进主线")
    d = eb.line_items(lid)
    assert d["status"] == "doing"
    assert [x["title"] for x in d["done"]] == ["三件有期限的已加进日程"]
    assert {x["state"] for x in d["wait"]} == {"wait"} and len(d["wait"]) == 2
    assert len(d["todo"]) == 2  # 搬走的、不做了的不列
    with pytest.raises(HTTPException):
        eb.line_items("no-such-line")


def test_renamed_section_is_the_same_line(board_env):
    eb.sync()
    before = eb.board()
    bump(board_env, SAMPLE.replace("## 离线模式合进主线：三类 + 冲突提示", "## 离线模式合进主线改名了：三类 + 冲突提示"))
    eb.sync()
    after = eb.board()
    assert after["weekDone"] == before["weekDone"]
    assert "离线模式合进主线改名了" in {x["name"] for x in after["lines"]}
    assert "离线模式合进主线" not in {x["name"] for x in after["lines"] + after["older"]}


def test_reworded_done_item_counts_once(board_env):
    eb.sync()
    bump(board_env, SAMPLE.replace("- [x] 三件有期限的已加进日程", "- [x] 把三件有截止日的事写进了日程层，改动号 115 到 117，能撤销"))
    eb.sync()
    assert eb.board()["weekDone"] == 2


def test_unreadable_handoff_keeps_the_board(board_env):
    eb.sync()
    before = eb.board()
    board_env.write_bytes(b"\xff\xfe broken")
    assert eb.sync()  # 说读不了
    after = eb.board()
    assert (after["weekDone"], after["todo"], after["wait"]) == (before["weekDone"], before["todo"], before["wait"])
