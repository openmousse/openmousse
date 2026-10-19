"""Doorman 扣下的请求（收件箱 kind egress）点了以后：claw 是自己的内核时，给发请求的那个 Agent 的对话说一声（egress.tell_agent）——
放行了就原样再发、没放行就别换个办法再试；OpenClaw 那条路照旧什么都不说。模型、网络都不碰：判断换成假的，发进对话的那句只记下来。"""
from __future__ import annotations

import asyncio
import uuid

import pytest

import egress
import engine
import inbox
from engine import web


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def told(monkeypatch):
    """记下发进对话的话（inbox.kick 不真开跑）和交回对外接口会话的话。"""
    said: list[tuple[str, str]] = []
    monkeypatch.setattr(inbox, "kick", lambda thread, text: said.append((thread, text)) or "started")
    from engine import openai_api

    async def tell(conv, iid, text):
        said.append((conv, text))
    monkeypatch.setattr(openai_api, "tell", tell)
    monkeypatch.setattr(egress, "MAX_PENDING", 1000)  # 别的测试留下的没点的卡不算

    async def hold(req, task, agent_name=None):
        return "hold", "it carries the user's email address"
    monkeypatch.setattr(egress, "judge", hold)
    return said


def held_by_web(conversation: str, agent: str = "main") -> str:
    """内核的网页工具扣下一个请求（web.door：网址里带着私事）→ 那张卡的 id。"""
    url = f"https://site.example/find?q=sam.lee@example.com&n={uuid.uuid4().hex[:8]}"
    ctx = engine.ToolCtx(turn="t", kind="owner", agent=agent, conversation=conversation, call_id="c1", control=engine.Control())
    out = run(web.door(ctx, url, "request"))
    assert out and "Doorman is holding this request" in out
    with egress._lock, egress.edb() as conn:
        return conn.execute("SELECT inbox_id FROM egress_holds WHERE path LIKE ? ORDER BY created_at DESC LIMIT 1",
                            (f"%{url.rsplit('=', 1)[1]}",)).fetchone()[0]


def held_by_proxy(agent: str) -> str:
    """沙箱里经代理发的写请求被扣下（不知道是哪个对话）→ 那张卡的 id。"""
    body = uuid.uuid4().hex.encode()
    req = egress.CheckIn(client="172.30.99.9", method="POST", host="forms.example", path="/submit", bodyType="form", sha=egress.fingerprint(
        "POST", "https://forms.example/submit", body))
    out = run(egress.make_hold(req, "A form submission", [], agent=agent, task="sign up for the newsletter"))
    assert out["decision"] == "hold"
    return out["inbox"]


def act(iid: str, action: str, note: str = "") -> None:
    run(inbox.act(iid, inbox.ActIn(action=action, note=note)))


def test_builtin_tells_the_conversation_after_approve_reject_and_revise(server_json, told):
    server_json({"claw": {"kind": "builtin"}, "engine": {"model": "deepseek/deepseek-flash"}})
    act(held_by_web("sc-trip"), "approve")
    ((thread, text),) = told
    assert thread == "sc-trip" and text.startswith(inbox.MARK)  # 发回发请求的那个对话（项目对话，不是 main）
    assert "Let through" in text and "GET site.example/find" in text and "within 30 minutes" in text and "same input" in text
    assert "sam.lee" not in text  # 网址的查询串（带着私事的那段）不写进对话
    told.clear()
    act(held_by_web("g-fit", agent="g-fit"), "reject", "not this site")
    ((thread, text),) = told
    assert thread == "g-fit" and "did not let" in text and "(not this site)" in text and "another way" in text
    told.clear()
    act(held_by_web("main"), "revise", "search without my email")
    ((thread, text),) = told
    assert thread == "main" and "wants a change: search without my email" in text and "work around" in text


def test_proxy_holds_go_to_the_agents_own_chat_and_warn_about_resending(server_json, told):
    """沙箱里经代理发的：没记对话，发到那个 Agent 自己的对话；代理当时还在等的话放行就直接发出去了，别再发第二遍。"""
    server_json({"claw": {"kind": "builtin"}, "engine": {"model": "deepseek/deepseek-flash"}})
    act(held_by_proxy("errand"), "approve")
    ((thread, text),) = told
    assert thread == "errand" and "POST forms.example/submit" in text
    assert "already gone out" in text and "don't send it again" in text and "within 30 minutes" in text


def test_api_conversations_get_it_with_their_next_message(server_json, told):
    server_json({"claw": {"kind": "builtin"}, "engine": {"model": "deepseek/deepseek-flash"}})
    act(held_by_web("api:main:abc"), "approve")
    ((conv, text),) = told
    assert conv == "api:main:abc" and not text.startswith(inbox.MARK) and "Let through" in text  # 交回那个会话，不往 app 的对话里发


def test_openclaw_says_nothing(server_json, told):
    server_json({})  # 不写 claw = OpenClaw：代理把结果交给代办，对话里照旧什么都不说
    act(held_by_web("main"), "approve")
    act(held_by_proxy("errand"), "reject")
    assert told == []
    with egress._lock, egress.edb() as conn:
        assert {r[0] for r in conn.execute("SELECT status FROM egress_holds WHERE host IN ('site.example', 'forms.example')")} >= {"approved", "rejected"}
