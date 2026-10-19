"""工程看板读 GitHub 看板（engboard.py，today.engineering.source = github）：Status 六格怎么对上 app 认的几种、缓存、gh 读不了时的退路。
gh 换成假的（eb.run_gh），给的是 GraphQL 的样子；看板上的人和事都是编的。"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException

import engboard as eb
from chat import _lock
from config import TZ, settings

TODAY = datetime.now(TZ).date()
MONDAY = TODAY - timedelta(days=TODAY.weekday())
LAST_WEEK = (MONDAY - timedelta(days=1)).isoformat()
OLD = (TODAY - timedelta(days=30)).isoformat()
LINES = ("内核", "设计", "App", "学习台")


def node(title: str, status: str | None = None, line: str | None = None, who: str | None = None, nxt: str | None = None,
         evidence: str | None = None, updated: str | None = None, archived: bool = False, at: str = "2026-01-05T10:00:00Z") -> dict:
    vals = [{"text": title, "field": {"name": "Title"}}]
    for name, key, v in (("Status", "name", status), ("线", "name", line), ("谁做", "name", who), ("下一步", "text", nxt),
                         ("证据", "text", evidence), ("更新", "date", updated)):
        if v:
            vals.append({key: v, "field": {"name": name}})
    return {"id": f"PVTI_{abs(hash(title)) % 10**9}", "isArchived": archived, "updatedAt": at, "content": {"title": title},
            "fieldValues": {"nodes": vals}}


def page(nodes: list[dict], cursor: str | None = None, lines=LINES) -> str:
    return json.dumps({"data": {"repositoryOwner": {"projectV2": {
        "title": "Mia 的工程", "url": "https://github.com/users/mia-lab/projects/3",
        "line": {"options": [{"name": n} for n in lines]},
        "items": {"pageInfo": {"hasNextPage": cursor is not None, "endCursor": cursor}, "nodes": nodes}}}}}, ensure_ascii=False)


BOARD = [
    node("派活的命令行：开、停、看状态", "在做", "内核", "云端", "交 PR", "云端会话 crew-cli", TODAY.isoformat()),
    node("手册上线：规矩只存一份、按这句话搜、用了哪条留记号", "等上线", "内核", "服务器", "下次上线时一起", "PR #7", TODAY.isoformat()),
    node("第 2 批：斜杠命令、新会话、手动压缩", "排队", "内核", "云端"),
    node("技能市场接不接", "以后", "内核", "你"),
    node("换内核前必须补的", "做完", "内核", "云端", evidence="PR #5", updated=MONDAY.isoformat()),
    node("上周做完的那件", "做完", "内核", "云端", updated=LAST_WEEK),
    node("设计要你批的：首页排法、图标方向", "等你", "设计", "你", "第 20 张首页排法，你点一个"),
    node("App 小修 8 条", "以后", "App", "云端"),
    node("学习台 5 条", "以后", "学习台", "服务器"),
    node("很早以前做完的", "做完", "学习台", "服务器", updated=OLD),
    node("没写线也没写状态的一件"),
    node("存档了的一件", "在做", "内核", archived=True),
]


@pytest.fixture
def gh(server_json, monkeypatch, tmp_path):
    """配好 GitHub 来源，gh 换成假的：fake.pages = 按 after 给的页（None 是第一页），fake.calls = 每次的参数，fake.fail = 报错。"""
    main = tmp_path / "mainline.json"
    main.write_text(json.dumps([{"name": "设计系统", "state": "在做", "note": "第一批等你批"},
                                {"name": "内核", "state": "在做", "note": "第 1 批在做"},
                                {"name": "桌面工作台", "state": "以后", "note": "没开工"},
                                {"name": "学习", "state": "排队", "line": "学习台"}], ensure_ascii=False), encoding="utf8")
    server_json({"timezone": str(TZ), "language": "zh", "user_name": "Mia",
                 "today": {"engineering": {"source": "github", "owner": "mia-lab", "number": 3, "mainline": str(main),
                                           "todos": str(tmp_path / "todos.md")}}})
    with _lock, eb.edb() as conn:
        conn.execute("DELETE FROM eng_gh")
    eb._gh.clear()
    monkeypatch.setattr(settings, "language", "zh")  # server.json 的 language 在导入时就读了：直接换
    now = [1000.0]
    monkeypatch.setattr(eb, "clock", lambda: now[0])

    class Fake:
        pages = {None: page(BOARD)}
        calls: list[list[str]] = []
        fail: str | None = None
        clock = now

        def __call__(self, args):
            self.calls.append(args)
            if self.fail:
                raise eb.GhError(self.fail)
            after = next((a[len("after="):] for a in args if a.startswith("after=")), None)
            return self.pages[after]
    fake = Fake()
    fake.calls = []
    monkeypatch.setattr(eb, "run_gh", fake)
    yield fake
    eb._gh.clear()


def cols(b: dict) -> dict:
    return {c["key"]: c for c in b["columns"]}


def test_statuses_map_onto_what_the_app_knows(gh):
    b = eb.get_eng()
    assert b["error"] is None and b["syncedLabel"]
    # 三个数：这周做完 = 周一以来做完的；没做 = 在做 + 等上线 + 排队（以后的不算，没写状态的算排队）；等你
    assert (b["weekDone"], b["todo"], b["wait"]) == (1, 4, 1)
    c = cols(b)
    assert [x["key"] for x in b["columns"]] == ["wait", "doing", "queue", "later", "done"]
    assert [x["label"] for x in b["columns"]] == ["等你", "在做", "排队", "以后", "这周做完"]
    # 等上线放在「在做」那列，标题前写明；存档了的不要
    assert [x["title"] for x in c["doing"]["cards"]] == ["派活的命令行：开、停、看状态", "等上线 · 手册上线：规矩只存一份、按这句话搜、用了哪条留记号"]
    assert c["later"]["count"] == 3 and c["queue"]["count"] == 2
    assert [x["title"] for x in c["done"]["cards"]] == ["换内核前必须补的"]
    assert c["done"]["cards"][0]["data"]["doneAt"] == MONDAY.isoformat()
    assert c["wait"]["cards"][0]["tags"] == [{"text": "设计", "tone": "neutral"}, {"text": "你", "tone": "neutral"}]
    text = c["doing"]["cards"][0]["data"]["text"]
    assert "下一步：交 PR" in text and "证据：云端会话 crew-cli" in text and text.splitlines()[-1].startswith("云端 · 在做 · 更新 ")


def test_lines_follow_the_board(gh):
    b = eb.get_eng()
    by = {x["name"]: x for x in b["lines"] + b["older"]}
    assert [x["name"] for x in b["lines"]] == ["内核", "设计", "没分线"]  # 照「线」选项的顺序，没写线的在最后
    assert (by["内核"]["status"], by["内核"]["done"], by["内核"]["total"]) == ("doing", 2, 6)  # 以后的也算进总数
    assert by["设计"]["status"] == "wait"
    assert by["没分线"]["status"] == "queue"
    # 只剩以后的（和早就做完的）线收进「更早的线」，状态写排队，最近动过的在前
    assert [x["name"] for x in b["older"]] == ["学习台", "App"]
    assert by["App"]["status"] == by["学习台"]["status"] == "queue"
    assert by["内核"]["heading"] == "在做 1 · 等上线 1 · 排队 1 · 以后 1 · 做完 2"


def test_app_line_with_only_later_items_is_folded(gh):
    gh.pages = {None: page([node("App 小修 8 条", "以后", "App", "云端"), node("老的", "做完", "App", updated=OLD)])}
    b = eb.get_eng()
    assert b["lines"] == [] and [x["name"] for x in b["older"]] == ["App"]
    assert (b["todo"], b["wait"], b["weekDone"]) == (0, 0, 0)


def test_line_detail(gh):
    b = eb.get_eng()
    lid = next(x["id"] for x in b["lines"] if x["name"] == "内核")
    d = eb.get_eng_line(lid)
    assert d["ok"] and d["status"] == "doing" and d["statusLabel"] == "在做"
    # 没做的那组照 在做 → 等上线 → 排队 → 以后，标题前写着是哪一格（排队的不写）
    assert [x["title"] for x in d["todo"]] == ["在做 · 派活的命令行：开、停、看状态", "等上线 · 手册上线：规矩只存一份、按这句话搜、用了哪条留记号",
                                              "第 2 批：斜杠命令、新会话、手动压缩", "以后 · 技能市场接不接"]
    assert [x["title"] for x in d["done"]] == ["换内核前必须补的", "上周做完的那件"]  # 新的在前
    assert d["done"][0]["doneAt"] == MONDAY.isoformat() and d["todo"][0]["doneAt"] is None
    with pytest.raises(HTTPException):
        eb.get_eng_line("no-such-line")


def test_report_card_has_the_same_shape(gh):
    card, err = eb.report_card()
    assert err is None
    assert {k: card[k] for k in ("type", "id", "kind", "weekDone", "todo", "wait")} == \
        {"type": "eng", "id": "eng-board", "kind": "eng_board", "weekDone": 1, "todo": 4, "wait": 1}
    assert card["time"] and card["createdAt"]


def test_mainline_steps_match_lines(gh):
    b = eb.get_eng()
    main = {m["name"]: m for m in b["mainCells"]}
    assert list(main) == ["设计系统", "内核", "桌面工作台", "学习"]
    assert (main["设计系统"]["done"], main["设计系统"]["total"]) == (0, 1)  # 「设计」是「设计系统」的开头，对上那条线
    assert main["内核"]["cells"] == [True, True, False, False, False, False]  # 做完的在前
    assert (main["桌面工作台"]["done"], main["桌面工作台"]["total"]) == (0, 1)  # 看板上没这条线：一格
    assert (main["学习"]["done"], main["学习"]["total"]) == (1, 2)  # 写了 line 就按它
    assert b["mainNote"] == "设计系统：第一批等你批"
    assert b["main"][1] == {"name": "内核", "done": 2, "total": 6}


def test_cache_then_refresh(gh):
    eb.get_eng()
    eb.report_card()
    assert len(gh.calls) == 1  # 1 分钟内看板页和「今天」的卡都用读过的
    gh.clock[0] += eb.GH_PAGE_TTL_S + 1
    eb.report_card()
    assert len(gh.calls) == 1  # 「今天」的卡管 5 分钟
    eb.get_eng()
    assert len(gh.calls) == 2  # 看板页过了 1 分钟就重读
    args = gh.calls[0]
    assert "owner=mia-lab" in args and "number=3" in args and "lineField=线" in args
    assert not any(a.startswith("after=") for a in args)


def test_pages_are_all_read(gh):
    gh.pages = {None: page(BOARD[:3], cursor="c1"), "c1": page(BOARD[3:])}
    b = eb.get_eng()
    assert len(gh.calls) == 2 and "after=c1" in gh.calls[1]
    assert (b["weekDone"], b["todo"], b["wait"]) == (1, 4, 1)


def test_gh_failure_keeps_the_last_board(gh):
    before = eb.get_eng()
    gh.fail = "HTTP 502: Bad Gateway"
    gh.clock[0] += eb.GH_TTL_S + 1
    after = eb.get_eng()
    assert "HTTP 502" in after["error"] and before["syncedLabel"] in after["error"]  # 说是几点读到的
    assert (after["weekDone"], after["todo"], after["wait"]) == (before["weekDone"], before["todo"], before["wait"])
    card, err = eb.report_card()
    assert card["todo"] == 4 and "HTTP 502" in err
    n = len(gh.calls)
    gh.clock[0] += 5
    eb.get_eng()
    assert len(gh.calls) == n  # 刚失败过：1 分钟里不再试
    gh.fail = None
    gh.clock[0] += eb.GH_RETRY_S
    assert eb.get_eng()["error"] is None and len(gh.calls) == n + 1


def test_after_restart_the_saved_copy_is_used(gh):
    eb.get_eng()
    eb._gh.clear()  # 服务重启：内存里的没了，库里还有
    gh.fail = "gh: Could not resolve to a ProjectV2"
    b = eb.get_eng()
    assert b["todo"] == 4 and "Could not resolve" in b["error"]


def test_never_read_says_why(gh):
    gh.fail = "这台机器上没有 gh 命令"
    b = eb.get_eng()
    assert b["error"].startswith("没能读 GitHub 看板：这台机器上没有 gh 命令")
    assert (b["columns"], b["lines"], b["todo"]) == ([], [], 0)
    assert eb.report_card() == (None, b["error"])
    with pytest.raises(HTTPException):
        eb.get_eng_line("x")


def test_missing_board_is_an_error(gh):
    gh.pages = {None: json.dumps({"data": {"repositoryOwner": None}})}
    b = eb.get_eng()
    assert "找不到看板 mia-lab/3" in b["error"]


def test_default_github_statuses_and_own_field_names(gh, server_json, monkeypatch):
    monkeypatch.setattr(settings, "language", "en")
    server_json({"timezone": str(TZ), "language": "en",
                 "today": {"engineering": {"source": "GitHub", "owner": "mia-lab", "number": "3",
                                           "fields": {"line": "Area", "updated": "Touched"}, "statuses": {"Blocked on me": "wait"}}}})
    nodes = []
    for title, status in (("A", "Todo"), ("B", "In Progress"), ("C", "Done"), ("D", "Blocked on me"), ("E", "Someday")):
        n = node(title, status)
        n["fieldValues"]["nodes"] += [{"name": "Kernel", "field": {"name": "Area"}}, {"date": MONDAY.isoformat(), "field": {"name": "Touched"}}]
        nodes.append(n)
    gh.pages = {None: page(nodes, lines=("Kernel",))}
    b = eb.get_eng()
    assert "lineField=Area" in gh.calls[0]
    assert (b["weekDone"], b["todo"], b["wait"]) == (1, 3, 1)  # 认不出的 Someday 算排队
    assert [x["label"] for x in b["columns"]] == ["Waiting on you", "Doing", "Queued", "Later", "Done this week"]
    assert b["lines"][0]["name"] == "Kernel" and b["mainCells"] == []


def test_github_needs_owner_and_number(server_json, tmp_path):
    server_json({"today": {"engineering": {"source": "github", "owner": "mia-lab", "todos": str(tmp_path / "todos.md")}}})
    assert eb.cfg() == {}
    assert eb.report_card() == (None, None)
    with pytest.raises(HTTPException):
        eb.get_eng()


def test_github_source_does_not_read_the_handoff(gh, monkeypatch):
    monkeypatch.setattr(eb, "sync", lambda *a, **k: pytest.fail("GitHub 来源不该读交接本"))
    eb.get_eng()
    eb.report_card()
    assert eb.source_files() == []


def test_run_gh_errors(monkeypatch):
    def missing(*a, **k):
        raise FileNotFoundError("gh")
    monkeypatch.setattr(eb.subprocess, "run", missing)
    with pytest.raises(eb.GhError):
        eb.run_gh(["gh", "api", "graphql"])

    class R:
        returncode, stdout, stderr = 1, "", "\nHTTP 401: Bad credentials (https://api.github.com/graphql)\nTry authenticating with:  gh auth login\n"
    monkeypatch.setattr(eb.subprocess, "run", lambda *a, **k: R())
    with pytest.raises(eb.GhError, match="^HTTP 401: Bad credentials"):
        eb.run_gh(["gh", "api", "graphql"])
