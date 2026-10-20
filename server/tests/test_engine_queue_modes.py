"""第 2 批：排队模式（engine/queueing.py，命令 /queue 在 engine/commands.py，接在 ../chat.py 的 send / drain）：回复在跑的时候又说一句，
followup 一句一轮、collect 合成一轮（都不插话）、interrupt 停掉这一轮马上回；不写 = 照旧（插话）。模型、命令行都是假的。"""
from __future__ import annotations

import asyncio

import pytest

from engine import commands, queueing
from engine.session import text_of
from test_engine_events import BUILTIN, collect, msg_row, replies, run, running_step, until

ON = {**BUILTIN, "engine": {**BUILTIN["engine"], "commands": True}}


@pytest.fixture
def model(request):
    return request.getfixturevalue("fake")


@pytest.fixture
def cli(request):
    return request.getfixturevalue("bridge")


async def busy_with(chat, thread: str):
    r = chat.start_run(thread, "check my day", None)
    first = asyncio.create_task(collect(chat.attach(r)))
    await until(lambda: running_step(r))
    return r, first


def said(req) -> list[str]:
    """这一次发给模型的用户那几句（备料的手册说明之类也是 user，所以按内容找）。"""
    return [text_of(m) for m in req.messages if m["role"] == "user"]


async def send(chat, thread: str, text: str):
    resp = await chat.send(chat.SendBody(thread=thread, text=text))
    return asyncio.create_task(collect(resp.body_iterator))


def test_followup_answers_each_message_on_its_own(server_json, model, cli):
    import chat
    server_json(ON)
    queueing.set_mode("t-q-follow", "followup")
    cli.delays["schedule"] = 0.4
    f = model([{"calls": [("c1", "schedule", {"args": ["day"]})]}, {"text": "Your day is clear."}, {"text": "About one."}, {"text": "About two."}])

    async def go():
        r, first = await busy_with(chat, "t-q-follow")
        one = await send(chat, "t-q-follow", "one")
        two = await send(chat, "t-q-follow", "two")
        return r, await first, await one, await two
    r, first, one, two = run(go())
    assert one[0][0] == "queued" and not one[0][1].get("steer") and two[0][0] == "queued"  # 不插话
    assert dict(first)["done"]["text"] == "Your day is clear." and r.status == "ok"
    assert dict(one)["done"]["text"] == "About one." and dict(two)["done"]["text"] == "About two."  # 一句一轮
    assert said(f.requests[2][1])[-1] == "one" and "two" not in said(f.requests[2][1]) and said(f.requests[3][1])[-1] == "two"
    assert "one" not in said(f.requests[1][1])  # 正在跑的那一轮没收到
    assert len(replies(chat, "t-q-follow")) == 3


def test_collect_waits_and_answers_together(server_json, model, cli):
    import chat
    server_json(ON)
    queueing.set_mode("t-q-collect", "collect")
    cli.delays["schedule"] = 0.4
    f = model([{"calls": [("c1", "schedule", {"args": ["day"]})]}, {"text": "Your day is clear."}, {"text": "Both noted."}])

    async def go():
        _r, first = await busy_with(chat, "t-q-collect")
        one = await send(chat, "t-q-collect", "one")
        two = await send(chat, "t-q-collect", "two")
        return await first, await one, await two
    first, one, two = run(go())
    assert not one[0][1].get("steer") and dict(one)["done"]["id"] == dict(two)["done"]["id"] and dict(two)["done"]["text"] == "Both noted."
    assert any("[1] one" in t and "[2] two" in t for t in said(f.requests[2][1])) and len(f.requests) == 3


def test_interrupt_stops_the_running_reply_and_answers_the_new_one(server_json, model, cli):
    import chat
    server_json(ON)
    queueing.set_mode("t-q-int", "interrupt")
    cli.delays["schedule"] = 5
    f = model([{"calls": [("c1", "schedule", {"args": ["day"]})]}, {"text": "Sure, the gym at six."}])

    async def go():
        r, first = await busy_with(chat, "t-q-int")
        await until(lambda: any(n == "schedule" for n, _ in cli.calls))  # 等 schedule 真的跑起来再打断（忙的时候「有一步在跑」时它可能还没开始，就没东西可取消）
        new = await send(chat, "t-q-int", "never mind, book the gym")
        return r, await first, await new
    r, first, new = run(go())
    assert r.status == "stopped" and dict(first)["done"]["text"] == "(Stopped)" and cli.cancelled == ["schedule"]
    done = dict(new)["done"]
    assert done["text"] == "Sure, the gym at six." and done["id"] != dict(first)["done"]["id"]
    assert any(t.endswith("never mind, book the gym") for t in said(f.requests[1][1]))
    assert msg_row(chat, int(new[0][1]["userId"][2:]))["status"] == "ok"


def test_not_set_still_steers(server_json, model, cli):
    import chat
    server_json(ON)
    cli.delays["schedule"] = 0.4
    model([{"calls": [("c1", "schedule", {"args": ["day"]})]}, {"text": "Done, and noted."}])

    async def go():
        _r, first = await busy_with(chat, "t-q-default")
        late = await send(chat, "t-q-default", "also the dentist")
        return await first, await late
    first, late = run(go())
    assert late[0][1]["steer"] is True and dict(first)["done"]["id"] == dict(late)["done"]["id"]


def test_the_queue_command(server_json):
    server_json(ON)
    assert "the default" in run(commands.handle("t-q-cmd", "/queue")).text
    bad = run(commands.handle("t-q-cmd", "/queue sometimes"))
    assert not bad.ok and "sometimes" in bad.text and queueing.mode_of("t-q-cmd") is None
    assert run(commands.handle("t-q-cmd", "/queue Followup")).text.startswith("Done: followup")
    assert queueing.mode_of("t-q-cmd") == "followup" and "followup" in run(commands.handle("t-q-cmd", "/queue")).text
    assert run(commands.handle("t-q-cmd", "/queue default")).text.startswith("Back to the default")
    assert queueing.mode_of("t-q-cmd") is None
    queueing.set_mode("t-q-cmd", "collect")
    server_json({**ON, "claw": {"kind": "openclaw"}})
    assert queueing.mode_of("t-q-cmd") is None  # OpenClaw 那条路照旧


from test_engine_events import bridge, fake  # noqa: E402,F401 — 假模型、假命令行（上面经 request 拿）
