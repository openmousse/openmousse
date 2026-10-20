"""Agent 的 message 工具和 send_to_owner（第 6 批，server/channels/tool.py）：发给主人直接发（和推送一样，不用点头）；发给联系人一律交一张
kind tool 的卡，主人点了同意服务器才照原样发一次；没开渠道（或 claw 不是 builtin）就没有这个工具；别的 Agent 只看得到、发得到绑给自己的联系人。
渠道是假的（test_channels_layer 的 FakeChannel），模型是假的（test_engine.FakeModel），不连网。"""
from __future__ import annotations

import asyncio
import json
import time
import uuid

import pytest

import channels
import chat
import inbox
import main  # noqa: F401 — 导入时挂好钩子：kind tool 的卡同意后照做（engine/tools.install_hooks）、渠道的（channels.install）
from channels import people, store
from channels import tool as chtool
from engine import events, loop, providers
from engine import tools as engine_tools
from test_channels_layer import config  # 导入时登记了假渠道（hub.KINDS["fake"]）
from test_engine import FakeModel


def run(coro):
    return asyncio.run(coro)


async def until(cond, timeout: float = 6.0) -> None:
    t0 = time.monotonic()
    while not cond():
        if time.monotonic() - t0 > timeout:
            raise AssertionError("timed out waiting")
        await asyncio.sleep(0.02)


@pytest.fixture
def fake(monkeypatch):
    def make(script):
        f = FakeModel(script)
        monkeypatch.setattr(providers, "stream", f.stream)
        return f
    return make


@pytest.fixture
def acct(server_json):
    """一个开着的假渠道账号，主人是 owner-1（写在配置里）。→ (账号 id, 改配置的函数)。"""
    acc_id = f"f{uuid.uuid4().hex[:8]}"

    def write(**acc) -> None:
        server_json(config(acc_id, **{"owners": ["owner-1"], **acc}))
    write()
    return acc_id, write


def ctx(agent: str = "main") -> loop.ToolCtx:
    tid = events.turn_open(conversation=agent, kind="owner", model="deepseek/deepseek-flash", agent=agent, origin="user")
    return loop.ToolCtx(turn=tid, kind="owner", agent=agent, conversation=agent, call_id=f"c{uuid.uuid4().hex[:6]}", control=loop.Control())


def message_tool(agent: str = "main"):
    return next((t for t in engine_tools.for_agent(agent) if t.name == "message"), None)


def out_to(acc_id: str, chat_: str) -> list:
    return [r for r in store.outbox("fake", acc_id) if r["chat"] == chat_]


def test_no_tool_without_an_enabled_channel(server_json):
    server_json({"claw": {"kind": "builtin"}})
    assert message_tool() is None
    server_json({**config("off-1"), "claw": {"kind": "openclaw"}})  # OpenClaw 那条路：渠道归它自己，这里什么都不给
    assert channels.tools_for("main") == []
    server_json(config("on-1"))
    t = message_tool()
    assert t is not None and t.kinds == ("owner",)
    server_json({**config("on-1"), "engine": {"tools": {"levels": {"message": "off"}}}})  # 主人能关
    assert message_tool() is None


def test_a_message_to_the_owner_goes_straight_out(acct):
    acc_id, _ = acct
    t = message_tool()
    args = {"to": "owner", "text": "The **report** is ready."}
    assert t.level(args)[0] == "undo" and run(engine_tools.gate(t, args, ctx())) is None  # 和推送一样：不用点头
    assert run(t.run(args, ctx())) == "Sent to the owner (fake)."
    row = out_to(acc_id, "owner-1")[0]
    assert json.loads(row["body"])["text"] == "The **report** is ready." and row["why"] == "message"
    run(chtool.tool("coach").run({"text": "Done with the plan."}, ctx("coach")))  # 别的 Agent 发的：带上是谁
    assert json.loads(out_to(acc_id, "owner-1")[0]["body"])["text"].startswith("**coach**\nDone with the plan.")


def test_a_message_to_a_contact_waits_for_the_owners_ok(acct, fake):
    acc_id, _ = acct
    p = people.add_contact("fake", f"c-{uuid.uuid4().hex[:6]}", name="Sam", tier="friend", account=acc_id)
    t = message_tool()
    args = {"to": p["id"], "text": "Running 10 minutes late."}
    assert t.level(args)[0] == "ask"
    with pytest.raises(loop.ToolFailure):  # 绕过关卡直接跑也发不出去
        run(t.run(args, ctx()))
    card = run(engine_tools.gate(t, args, ctx()))
    assert isinstance(card, dict) and out_to(acc_id, p["sender"]) == []  # 交了卡，还没发
    it = inbox.item(card["inbox"])
    assert it["kind"] == "tool" and "Sam" in it["title"] and "Running 10 minutes late." in it["detail"] and it["approveLabel"] == "Send"
    fake([{"text": "Sent it to Sam."}])  # 同意以后结果发回对话（那一轮的回话）

    async def approve():
        await inbox.act(card["inbox"], inbox.ActIn(action="approve"))
        await until(lambda: not chat.busy("main"))
    run(approve())
    rows = out_to(acc_id, p["sender"])
    body = json.loads(rows[0]["body"]) if len(rows) == 1 else {}
    assert body.get("text") == "Running 10 minutes late." and body["format"] == "plain" and rows[0]["ref"] == card["inbox"]  # 照卡上的原话发一次
    assert inbox.item(card["inbox"])["status"] == "done"


def test_other_agents_only_reach_their_own_contacts(acct):
    acc_id, _ = acct
    mine = people.add_contact("fake", f"c-{uuid.uuid4().hex[:6]}", name="Robin", tier="friend", agent="coach", account=acc_id)
    theirs = people.add_contact("fake", f"c-{uuid.uuid4().hex[:6]}", name="Kim", tier="close", account=acc_id)
    t = chtool.tool("coach")
    listed = run(t.run({"action": "contacts"}, ctx("coach")))
    assert mine["id"] in listed and theirs["id"] not in listed and "Robin" in listed
    assert '<untrusted_tool_result source="channels.contacts">' in listed  # 名字是对方自己设的：包成资料
    said = run(t.ask_user({"to": theirs["id"], "text": "hi"}, ctx("coach")))
    assert isinstance(said, str) and said.startswith("Not run: there's no contact")
    assert theirs["id"] in run(chtool.tool("main").run({"action": "contacts"}, ctx()))  # main 看全部


def test_send_to_owner_for_the_server_itself(acct, monkeypatch):
    acc_id, _ = acct
    res = channels.send_to_owner("Backup finished.")
    assert res["ok"] and res["channel"] == "fake" and res["account"] == acc_id
    assert out_to(acc_id, "owner-1")[0]["why"] == "notify"
    assert channels.send_to_owner("x", channel="email")["ok"] is False  # 指定的渠道没开
    assert channels.send_to_owner("   ")["ok"] is False
    monkeypatch.setattr(chtool, "owner_targets", lambda channel=None: [])  # 渠道开着、但还没认过主人
    assert "No enabled channel knows the owner yet" in channels.send_to_owner("y")["error"]
