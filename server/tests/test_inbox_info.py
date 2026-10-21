"""收件箱的只读卡（kind info，10/20）：看完就行、不要你做决定（每周的工程周报这类）。点同意 / 拒绝 / 修改都只是收起：卡记成 done、
结果「已读」，不往任何对话发话、不起一轮；默认不推送、不发到聊天渠道；「已处理」里点「跟进」照旧是你自己要聊，但不把它改回「在做」。
别的 kind 照旧。数据都是编的。"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import chat
import inbox


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def runs(monkeypatch):
    """真起一轮就是 chat.start_run：换成只记一笔；推送、聊天渠道（NEW_HOOKS）也只记。"""
    import push
    st = SimpleNamespace(started=[], pushed=[], channels=[])
    monkeypatch.setattr(chat, "start_run", lambda thread, text, *a, **kw: st.started.append((thread, text)))

    async def fake_send(*a, **kw):
        st.pushed.append(kw)
        return {"ok": True}
    monkeypatch.setattr(push, "send_push", fake_send)

    async def channel(it, revised=False):
        st.channels.append(it["id"])
    monkeypatch.setattr(inbox, "NEW_HOOKS", [channel])
    return st


def notice(**kw) -> str:
    body = {"kind": "info", "title": "Weekly engineering report (fictional)", "why": "Every Monday at 08:00",
            "detail": "- 12 commits\n- one flaky test", "approveLabel": "Got it", **kw}
    return run(inbox.add(inbox.ItemIn(**body)))["id"]


@pytest.mark.parametrize("action,note", [("approve", ""), ("reject", ""), ("revise", ""), ("revise", "Make it shorter")])
def test_any_tap_on_a_notice_just_files_it_away(runs, action, note):
    iid = notice()
    it = inbox.item(iid)
    assert it["kind"] == "info" and it["level"] == "none" and it["status"] == "pending"
    assert runs.pushed == [] and runs.channels == []  # 默认不推送，也不发到聊天渠道（那里的卡是「要你点头」、带按钮的）
    res = run(inbox.act(iid, inbox.ActIn(action=action, note=note)))
    it = res["item"]
    assert it["status"] == "done" and it["result"] == "Read" and it["decidedAt"]
    assert "run" not in res and runs.started == []  # 没往任何对话发话，没起一轮
    with pytest.raises(HTTPException) as e:  # 收起了就定下来了
        run(inbox.act(iid, inbox.ActIn(action="approve")))
    assert e.value.status_code == 409


def test_a_notice_with_a_level_pushes_as_a_notice_and_the_agent_can_withdraw_it(runs):
    iid = notice(level="quiet")
    assert runs.pushed[-1]["subtitle"] == "Notice" and runs.pushed[-1]["level"] == "quiet"  # 不写「要你点头」
    it = run(inbox.withdraw(iid))["item"]
    assert it["status"] == "withdrawn" and runs.started == []
    assert inbox.default_level("info") == "none" and inbox.kind_label("info") == "Notice"


def test_following_up_on_a_notice_is_your_own_chat_and_does_not_reopen_it(runs):
    iid = notice()
    run(inbox.act(iid, inbox.ActIn(action="approve")))
    it, context = inbox.follow_context(iid)
    assert "there is nothing to do" in context and "no need to report done" in context and iid in context
    inbox.mark_followed(it, "Which test was flaky?")
    it = inbox.item(iid)
    assert it["status"] == "done" and it["followNote"] == "Which test was flaky?" and it["followedAt"]  # 还是收起的，不改回「在做」


def test_other_kinds_still_hand_the_card_to_the_agent(runs):
    iid = run(inbox.add(inbox.ItemIn(kind="write", title="Log today's lunch (fictional)", level="none")))["id"]
    res = run(inbox.act(iid, inbox.ActIn(action="approve")))
    assert res["item"]["status"] == "approved" and res["run"] == "started"
    assert runs.started[-1][0] == "main" and runs.started[-1][1].startswith(inbox.MARK)  # 照旧：发「【收件箱】已同意…」让它去做
    other = run(inbox.add(inbox.ItemIn(kind="code", title="Rename a config key (fictional)", level="none")))["id"]
    assert run(inbox.act(other, inbox.ActIn(action="reject")))["item"]["status"] == "rejected"  # 拒绝照旧是拒绝
    third = run(inbox.add(inbox.ItemIn(kind="skill", title="Add a skill (fictional)", level="none")))["id"]
    run(inbox.act(third, inbox.ActIn(action="revise", note="Narrower")))
    assert inbox.item(third)["status"] == "revising" and "needs changes: Narrower" in runs.started[-1][1]  # 改一下照旧叫它去改
    assert len(runs.started) == 2 and runs.channels  # 别的 kind 照旧发到聊天渠道


def test_the_command_line_and_the_feature_name_know_it():
    import inbox_ctl
    import version
    assert "info" in inbox_ctl.KINDS and "info" in inbox.KINDS and "inbox.info" in version.FEATURES
