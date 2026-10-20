"""第 2 批第 14 项：会话的标题（engine/titles.py，列旧会话时用）：先是第一句截短的，engine.titles 开了在后台用一次一问一答起名。
模型是假的（SizedFake），不花钱；数据都是编的。"""
from __future__ import annotations

import asyncio
import json

import pytest

from engine import history, titles
from test_engine_commands import until
from test_engine_context import MODEL, run, say


@pytest.fixture
def machine(request):
    return request.getfixturevalue("home")


@pytest.fixture
def model(request):
    return request.getfixturevalue("fake")


@pytest.fixture
def cli(request):
    return request.getfixturevalue("runner")


def test_derived_and_clean():
    assert titles.derived("plan the trip") == "plan the trip"
    long = titles.derived("please plan a fictional three day trip along the coast with stops at every lighthouse")
    assert long.endswith("…") and len(long) <= titles.DERIVED_MAX + 1 and not long[:-1].endswith(" ")
    assert titles.derived("规" * 60) == "规" * titles.DERIVED_MAX + "…"
    assert titles.clean(' "Coastal trip planning". ') == "Coastal trip planning"
    assert titles.clean("Sure! Here is a detailed plan for your three day trip along the coast with all the lighthouses") is None  # 像在回答


def test_titles_are_named_in_the_background_when_on(machine, model, cli):
    machine.write({"engine": {"model": MODEL, "titles": True}})
    f = model([{"text": "A route along the coast."}, {"text": json.dumps({"title": "Coastal trip planning"})}])
    say("t-title-on", "2026-10-14", "plan the fictional trip to the coast", ref=401)

    async def go():
        first = (await history.sessions_endpoint("t-title-on"))["sessions"]
        await until(lambda: not titles._bg)
        return first, (await history.sessions_endpoint("t-title-on"))["sessions"]
    first, again = run(go())
    assert first[0]["titleFrom"] == "derived" and first[0]["title"] == "plan the fictional trip to the coast"
    assert again[0]["titleFrom"] == "llm" and again[0]["title"] == "Coastal trip planning"
    naming = f.requests[1][1]
    assert naming.tools == [] and naming.json and "plan the fictional trip" in naming.messages[-1]["content"][0]["text"]
    run(history.sessions_endpoint("t-title-on"))
    assert len(f.requests) == 2  # 一条会话只起一次


def test_a_title_that_did_not_work_is_not_retried_on_every_list(machine, model, cli):
    machine.write({"engine": {"model": MODEL, "titles": True}})
    f = model([{"text": "Noted."}, {"text": json.dumps({"title": "Sure! Here is a long answer to your message about the fictional garden plan today"})}])
    say("t-title-bad", "2026-10-14", "the fictional garden plan", ref=403)

    async def go():
        await history.sessions_endpoint("t-title-bad")
        await until(lambda: not titles._bg)
        out = (await history.sessions_endpoint("t-title-bad"))["sessions"]
        await asyncio.sleep(0.05)
        return out
    out = run(go())
    assert out[0]["titleFrom"] == "derived" and len(f.requests) == 2  # 起的名像在回答：不要，下次列也不再起


def test_off_by_default(machine, model, cli):
    machine.write({"engine": {"model": MODEL}})
    f = model([{"text": "Hi."}])
    say("t-title-off", "2026-10-14", "hello there", ref=402)

    async def go():
        out = (await history.sessions_endpoint("t-title-off"))["sessions"]
        await asyncio.sleep(0.05)
        return out
    out = run(go())
    assert out[0]["titleFrom"] == "derived" and len(f.requests) == 1


from test_engine_context import fake, home, runner  # noqa: E402,F401 — fixture，上面经 request.getfixturevalue 拿
