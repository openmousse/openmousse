"""收件箱一张卡多条（inbox.py，2026-10-17）：交卡带 items；老 app 看到的是普通卡（每条一行，点「同意」= 每条照默认）；
新 app 逐条同意 / 拒绝 / 修改（按一句话重出那一条，零工具的模型调用）、最后提交只做同意的；server.json 的 inbox.items 能关掉逐条。
模型都是假的，数据都是编的。"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

import claw
import inbox


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def kicks(monkeypatch):
    """同意以后发进 Agent 对话的那句：记下来，不真的开一轮；推送也不真发。"""
    import push
    said: list[tuple[str, str]] = []
    monkeypatch.setattr(inbox, "kick", lambda thread, text: said.append((thread, text)) or "started")

    async def fake_send(*a, **kw):
        return {"ok": True}
    monkeypatch.setattr(push, "send_push", fake_send)
    return said


ITEMS = [{"text": "Merge the two notes about the 8:10 bus", "detail": "- was: Alex takes the 8:10\n- was: Alex catches the 8:10 every day",
          "check": {"state": "ok", "text": "Matches the conversation"}},
         {"id": "b", "text": "Archive the note about the cracked phone"},
         {"text": "Add to the profile: trains four times a week", "check": {"state": "mismatch", "text": "Doesn't match the conversation"},
          "default": "reject"}]


def card(**kw) -> str:
    body = {"kind": "write", "title": "Tidy the memory tree", "why": "Weekly pruning", "detail": "Three changes this week.", "items": ITEMS,
            "level": "none", **kw}
    return run(inbox.add(inbox.ItemIn(**body)))["id"]


def test_a_card_with_items_has_the_new_shape_and_still_reads_as_an_old_card(kicks):
    it = inbox.item(card())
    assert [x["id"] for x in it["items"]] == ["1", "b", "3"]  # 没写 id 的按第几条
    assert it["caps"] == ["items", "revise"] and it["intro"] == "Three changes this week." and it["itemsKind"] == ""
    assert it["itemCounts"] == {"approve": 0, "reject": 0, "open": 3, "revised": 0}
    assert [x["default"] for x in it["items"]] == ["approve", "approve", "reject"] and it["items"][1]["check"] is None
    # 老 app：changes 每条一行（核对的结果、默认不做的标出来），detail 是交卡时写的那段加上每一条
    assert it["changes"] == ["Merge the two notes about the 8:10 bus (Matches the conversation)", "Archive the note about the cracked phone",
                             "Add to the profile: trains four times a week (Doesn't match the conversation) — not done by default"]
    assert it["detail"].startswith("Three changes this week.\n\n1. Merge the two notes") and "   - was: Alex takes the 8:10" in it["detail"]
    assert 'the ones marked "not done by default" are skipped' in it["detail"]
    plain = inbox.item(run(inbox.add(inbox.ItemIn(kind="write", title="Log lunch", level="none"))).get("id"))
    assert "items" not in plain and "caps" not in plain  # 普通卡和以前一模一样


def test_the_old_app_approving_goes_with_each_default(kicks):
    iid = card()
    res = run(inbox.act(iid, inbox.ActIn(action="approve")))
    it = res["item"]
    assert it["status"] == "approved" and [x["decision"] for x in it["items"]] == ["approve", "approve", "reject"]
    assert kicks[-1][0] == "main" and "do only items 1, b; items 3 were not approved, skip them" in kicks[-1][1]
    assert f"inbox_ctl.py get {iid}" in kicks[-1][1]


def test_deciding_item_by_item_and_submitting_does_only_the_approved_ones(kicks):
    iid = card()
    it = run(inbox.act_item(iid, "3", inbox.ItemActIn(action="approve")))["item"]  # 默认不做的，也能改选
    assert it["items"][2]["decision"] == "approve" and it["status"] == "pending" and it["itemCounts"]["approve"] == 1
    run(inbox.act_item(iid, "b", inbox.ItemActIn(action="reject")))
    it = run(inbox.act_item(iid, "b", inbox.ItemActIn(action="undo")))["item"]
    assert it["items"][1]["decision"] is None
    it = run(inbox.act(iid, inbox.ActIn(action="submit", decisions={"1": "reject"})))["item"]  # 提交时带来的盖过之前的
    assert it["status"] == "approved" and [x["decision"] for x in it["items"]] == ["reject", "approve", "approve"]
    assert "do only items b, 3; items 1 were not approved" in kicks[-1][1]
    with pytest.raises(HTTPException) as e:  # 定下来以后不能再逐条点
        run(inbox.act_item(iid, "1", inbox.ItemActIn(action="approve")))
    assert e.value.status_code == 409


def test_submitting_with_nothing_approved_is_a_rejection(kicks):
    iid = card()
    it = run(inbox.act(iid, inbox.ActIn(action="submit", decisions={"1": "reject", "b": "reject"})))["item"]
    assert it["status"] == "rejected" and it["note"] == "None of the items were approved" and not kicks
    iid = card(dedupe="tidy-again")
    it = run(inbox.act(iid, inbox.ActIn(action="reject", note="not this week")))["item"]
    assert it["status"] == "rejected" and {x["decision"] for x in it["items"]} == {"reject"}


def test_revise_redoes_one_item_and_it_waits_for_a_decision_again(kicks, monkeypatch):
    seen: list[tuple] = []

    async def fake_reviser(prompt, input_, required):
        seen.append((input_, required))
        return {"text": "Add to the profile: trains three times a week", "detail": "as Alex said on Monday"}
    monkeypatch.setattr(inbox, "ask_reviser", fake_reviser)
    iid = card()
    run(inbox.act_item(iid, "3", inbox.ItemActIn(action="reject")))
    it = run(inbox.act_item(iid, "3", inbox.ItemActIn(action="revise", note="it's three, not four")))["item"]
    x = it["items"][2]
    assert x["text"] == "Add to the profile: trains three times a week" and x["detail"] == "as Alex said on Monday"
    assert x["decision"] is None and x["note"] == "it's three, not four"  # 换掉以后回到没定
    assert x["was"][0]["text"] == "Add to the profile: trains four times a week" and it["itemCounts"]["revised"] == 1
    assert seen[0][0]["note"] == "it's three, not four" and seen[0][0]["item"]["text"].endswith("four times a week") and seen[0][1] == ("text",)
    with pytest.raises(HTTPException) as e:
        run(inbox.act_item(iid, "3", inbox.ItemActIn(action="revise")))
    assert e.value.status_code == 400  # 「修改」要写一句


def test_revise_uses_the_registered_reviser_and_keeps_the_card_when_it_fails(kicks, monkeypatch):
    calls: list[tuple] = []

    async def step_reviser(c, x, note):
        calls.append((c["itemsKind"], x["data"], note))
        if note == "nonsense":
            raise inbox.ReviseError("That change doesn't fit the plan")
        return {**x, "text": "Archive the cracked phone note and the old charger", "data": {"op": "archive", "ids": ["p1", "p2"]},
                "default": "approve"}
    monkeypatch.setitem(inbox.ITEM_REVISE, "test-steps", step_reviser)
    items = [{"text": "Archive the cracked phone note", "data": {"op": "archive", "ids": ["p1"]}}]
    iid = card(items=items, itemsKind="test-steps", dedupe="steps")
    assert inbox.item(iid)["caps"] == ["items", "revise"]
    it = run(inbox.act_item(iid, "1", inbox.ItemActIn(action="revise", note="the charger too")))["item"]
    assert it["items"][0]["data"] == {"op": "archive", "ids": ["p1", "p2"]} and calls[0] == ("test-steps", {"op": "archive", "ids": ["p1"]}, "the charger too")
    with pytest.raises(HTTPException) as e:
        run(inbox.act_item(iid, "1", inbox.ItemActIn(action="revise", note="nonsense")))
    assert e.value.status_code == 422 and "doesn't fit" in e.value.detail
    assert inbox.item(iid)["items"][0]["text"] == "Archive the cracked phone note and the old charger"  # 没改成：原样
    other = card(items=items, itemsKind="nobody-knows", dedupe="nobody")  # 带 data、没登记重出办法的：卡上改不了
    assert inbox.item(other)["caps"] == ["items"]
    with pytest.raises(HTTPException) as e:
        run(inbox.act_item(other, "1", inbox.ItemActIn(action="revise", note="x")))
    assert e.value.status_code == 400


def test_the_reviser_is_a_tool_free_call(monkeypatch, server_json):
    import engine
    import llmjson
    got: list[tuple] = []

    async def fake_ask_json(prompt, input_, *, model, purpose, required, timeout, kind, origin):
        got.append(("engine", model, purpose, kind, origin))
        return {"text": "ok"}, f"builtin:{model}"

    async def fake_llm(prompt, input_, schema, *, timeout, model, tool_free_only, kind, origin):
        got.append(("llmjson", model, tool_free_only, kind, origin))
        return {"text": "ok"}, "llm-task"
    monkeypatch.setattr(engine, "ask_json", fake_ask_json)
    monkeypatch.setattr(llmjson, "ask", fake_llm)
    server_json({"inbox": {"revise_model": "deepseek/deepseek-flash"}})
    monkeypatch.setattr(claw, "is_builtin", lambda: True)
    assert run(inbox.ask_reviser("p", {"x": 1}, ("text",))) == {"text": "ok"}
    monkeypatch.setattr(claw, "is_builtin", lambda: False)
    run(inbox.ask_reviser("p", {"x": 1}, ("text",)))
    assert got == [("engine", "deepseek/deepseek-flash", "json", "owner", "user"), ("llmjson", "deepseek/deepseek-flash", True, "owner", "user")]


def test_the_owner_s_own_click_can_use_the_plan_and_mcp_or_a_task_can_t(monkeypatch, server_json):
    """10/21：「修改」算主人自己的一问一答：app 里点的照 engine.use.json 走他的套餐；经 /mcp 来的换成按量的。
    来路 task（后台任务）claude-code 认。套餐没做成照一问一答的说法报，不悄悄换。"""
    import chat
    import engine
    got: list[tuple] = []

    async def fake_ask_json(prompt, input_, *, model, purpose, required, timeout, kind, origin):
        got.append((model, kind, origin))
        return {"text": "ok"}, f"builtin:{model}"
    monkeypatch.setattr(engine, "ask_json", fake_ask_json)
    server_json({"claw": {"kind": "builtin"}, "engine": {"model": "deepseek/deepseek-flash", "use": {"json": "claude-code/sonnet"}}})
    for via in (None, "mcp", "task"):
        tok = chat.REQUEST_VIA.set(via)
        try:
            assert run(inbox.ask_reviser("p", {"x": 1}, ("text",))) == {"text": "ok"}
        finally:
            chat.REQUEST_VIA.reset(tok)
    assert got == [("claude-code/sonnet", "owner", "user"), ("deepseek/deepseek-flash", "owner", "mcp"),
                   ("claude-code/sonnet", "owner", "task")]

    async def signed_out(prompt, input_, **kw):
        raise engine.EngineError("claude: not logged in", kind="auth")
    monkeypatch.setattr(engine, "ask_json", signed_out)
    with pytest.raises(inbox.ReviseError) as e:
        run(inbox.ask_reviser("p", {"x": 1}, ("text",)))
    assert "claude-code/sonnet" in str(e.value) and ("登录掉了" in str(e.value) or "signed out" in str(e.value))


def test_items_can_be_switched_off_and_the_old_way_still_works(kicks, server_json):
    server_json({"inbox": {"items": False}})
    iid = card()
    assert inbox.item(iid)["caps"] == [] and len(inbox.item(iid)["items"]) == 3
    for call in (lambda: inbox.act_item(iid, "1", inbox.ItemActIn(action="approve")), lambda: inbox.act(iid, inbox.ActIn(action="submit"))):
        with pytest.raises(HTTPException) as e:
            run(call())
        assert e.value.status_code == 409
    assert run(inbox.act(iid, inbox.ActIn(action="approve")))["item"]["status"] == "approved"


def test_resubmitting_replaces_the_items_and_bad_items_are_refused(kicks):
    iid = card(dedupe="tidy-resubmit")
    run(inbox.act_item(iid, "1", inbox.ItemActIn(action="reject")))
    again = run(inbox.add(inbox.ItemIn(kind="write", title="Tidy the memory tree", dedupe="tidy-resubmit", level="none",
                                       items=[{"text": "Only archive the phone note"}])))
    assert again == {"ok": True, "id": iid, "updated": True}
    it = inbox.item(iid)
    assert [(x["id"], x["text"], x["decision"]) for x in it["items"]] == [("1", "Only archive the phone note", None)]
    it = run(inbox.update(iid, inbox.ItemPatch(items=[{"text": "Archive both phone notes"}, {"text": "Keep the bus note"}])))["item"]
    assert [x["text"] for x in it["items"]] == ["Archive both phone notes", "Keep the bus note"] and it["status"] == "pending"
    for bad in ([], [{"text": ""}], [{"id": "a", "text": "x"}, {"id": "a", "text": "y"}], [{"text": "x"}] * (inbox.ITEMS_MAX + 1),
                [{"id": "has space", "text": "x"}], [{"text": "x", "data": [1, 2]}]):
        with pytest.raises(HTTPException) as e:
            run(inbox.add(inbox.ItemIn(kind="write", title="Bad", level="none", items=bad)))
        assert e.value.status_code == 400
    with pytest.raises(HTTPException):
        run(inbox.act(card(dedupe="plain"), inbox.ActIn(action="submit", decisions={"1": "maybe"})))
    plain = run(inbox.add(inbox.ItemIn(kind="write", title="Plain", level="none")))["id"]  # 普通卡不能 submit
    with pytest.raises(HTTPException) as e:
        run(inbox.act(plain, inbox.ActIn(action="submit")))
    assert e.value.status_code == 400
