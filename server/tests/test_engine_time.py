"""10/19 彩排补的第 2 件：每句话带上当地时间（session.time_stamp / with_time，owner.user_message 写进 at）。模型问「现在几点」答得了；
只加在新来的那句上，前面的一个字不动，提示缓存的前缀不变。主人回合交给 Claude Code 跑时一样。模型是假的，不连网。"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

import config
from engine import models, owner, provider_anthropic, provider_openai, providers, session, unattended
from engine.session import DbSession, day_key, repair, text_of, user, with_time
from test_engine import FakeModel

BUILTIN = {"claw": {"kind": "builtin"}, "engine": {"model": "deepseek/deepseek-flash"}}
MON_0642_UTC = datetime(2026, 10, 19, 6, 42, tzinfo=timezone.utc)


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def clock(monkeypatch):
    """把「现在」定在某个 UTC 时刻：clock.set(datetime)；时区：clock.zone("Europe/London")。"""
    class Clock:
        now = MON_0642_UTC

        def set(self, t: datetime) -> None:
            self.now = t

        def zone(self, name: str) -> None:
            monkeypatch.setattr(config.settings, "tz", ZoneInfo(name))
            monkeypatch.setattr(config.settings, "timezone", name)
    c = Clock()
    monkeypatch.setattr(session, "_utcnow", lambda: c.now)
    return c


@pytest.fixture
def builtin(server_json, monkeypatch):
    import sources
    monkeypatch.setattr(sources, "memory_tree", None)
    server_json(BUILTIN)


def fake(monkeypatch, script: list) -> FakeModel:
    f = FakeModel(script)
    monkeypatch.setattr(providers, "stream", f.stream)
    return f


@pytest.mark.parametrize("zone,at,stamp", [
    ("Europe/London", MON_0642_UTC, "Mon 2026-10-19 07:42 BST"),        # 夏令时（10/25 才换回 GMT）
    ("Europe/London", datetime(2026, 11, 2, 6, 42, tzinfo=timezone.utc), "Mon 2026-11-02 06:42 GMT"),
    ("Asia/Shanghai", MON_0642_UTC, "Mon 2026-10-19 14:42 CST"),
    ("Asia/Tokyo", datetime(2026, 10, 19, 23, 30, tzinfo=timezone.utc), "Tue 2026-10-20 08:30 JST"),  # 那边已经是第二天
    ("America/New_York", MON_0642_UTC, "Mon 2026-10-19 02:42 EDT"),
    ("America/Sao_Paulo", MON_0642_UTC, "Mon 2026-10-19 03:42 UTC-03"),  # 没有字母缩写的时区
    ("UTC", MON_0642_UTC, "Mon 2026-10-19 06:42 UTC"),
])
def test_the_stamp_is_local_time_in_the_users_zone(clock, zone, at, stamp):
    clock.zone(zone)
    clock.set(at)
    assert session.time_stamp() == stamp
    m = owner.user_message("现在几点？", 7)
    assert m["at"] == stamp and m["ref"] == 7 and text_of(m) == "现在几点？"  # 存的是原话，时间另放一栏
    assert text_of(with_time(m)) == f"[{stamp}] 现在几点？"


def test_only_user_messages_with_a_time_get_it_and_pictures_come_after():
    pic = {"role": "user", "content": [{"type": "image", "mime": "image/png", "data": "AAAA"}], "at": "Mon 2026-10-19 07:42 BST"}
    assert with_time(pic)["content"][0] == {"type": "text", "text": "[Mon 2026-10-19 07:42 BST]"} and with_time(pic)["content"][1]["type"] == "image"
    old = user("an older message, before stamps")
    assert with_time(old) is old and repair([old]) == [old]  # 没有 at 的（以前的、抄进来的）一个字不动
    note = {"role": "system", "content": [{"type": "text", "text": "x"}], "at": "Mon 2026-10-19 07:42 BST"}
    assert with_time(note) is note


def test_each_turn_tells_the_model_the_time_and_the_cached_prefix_holds(builtin, clock, monkeypatch):
    clock.zone("Europe/London")
    f = fake(monkeypatch, [{"text": "It's 07:42."}, {"text": "Now it's 09:05."}])
    res1 = run(owner.respond("t-time", "2026-10-19", "what time is it?", None, lambda t: None))
    clock.set(datetime(2026, 10, 19, 8, 5, tzinfo=timezone.utc))
    res2 = run(owner.respond("t-time", "2026-10-19", "and now?", None, lambda t: None))
    assert res1.status == res2.status == "done"
    (_, r1), (_, r2) = f.requests
    assert r1.system == r2.system and "starts with the local time it was sent" in r1.system  # 开头一个字不动，规矩里写明那个时间是什么
    for convert in (lambda msgs: provider_anthropic.to_anthropic(msgs, models.anthropic_caps("claude-opus-5-5")),
                    lambda msgs: provider_openai.to_openai("", msgs, models.provider("moonshot"), "kimi-k3")):  # 换过模型也一样
        a, b = convert(r1.messages), convert(r2.messages)
        assert b[:len(a)] == a  # 第二轮发出去的，前面就是第一轮发出去的那几条：提示缓存的前缀不变
        first, last = a[0]["content"], b[-1]["content"]
        assert "[Mon 2026-10-19 07:42 BST] what time is it?" in str(first) and "[Mon 2026-10-19 09:05 BST] and now?" in str(last)
    s = DbSession.latest(day_key("t-time", "2026-10-19"))
    mine = [m for m in s.messages() if m["role"] == "user"]
    assert [(text_of(m), m["at"]) for m in mine] == [("what time is it?", "Mon 2026-10-19 07:42 BST"), ("and now?", "Mon 2026-10-19 09:05 BST")]


def test_compaction_summaries_see_when_things_were_said():
    from engine import compact
    msgs = [{**user("logged oats"), "at": "Mon 2026-10-19 07:42 BST"}, user("an old one")]
    assert compact.serialize(msgs) == ["[User, Mon 2026-10-19 07:42 BST]: logged oats", "[User]: an old one"]


def test_scheduled_runs_carry_the_time_too(builtin, clock, monkeypatch):
    clock.zone("Asia/Shanghai")
    f = fake(monkeypatch, [{"text": "Drink some water."}])
    out = run(unattended.fresh("main", "main", "cron:j-time", "remind the user to drink water", model="deepseek/deepseek-flash", light=True))
    assert out.status == "done"
    asked = f.requests[0][1].messages[-1]
    assert asked["at"] == "Mon 2026-10-19 14:42 CST" and text_of(with_time(asked)).startswith("[Mon 2026-10-19 14:42 CST] remind the user")


def test_claude_code_turns_get_the_time_in_front_of_the_message(request, clock):
    """主人回合交给 Claude Code 跑：它的标准输入里这句话前面一样带上当地时间；第二句（--resume）带的是第二句的时间。"""
    fake_cc = request.getfixturevalue("cc")
    from test_engine_claude_code import serving, text_of_cc
    clock.zone("Europe/London")
    fake_cc.scenario_([[{"say": "07:42 here."}], [{"say": "Still Monday."}]])

    async def go():
        async with serving(fake_cc):
            await owner.respond("t-time-cc", "2026-10-19", "what time is it?", "claude-code/opus", lambda t: None)
            clock.set(datetime(2026, 10, 19, 21, 0, tzinfo=timezone.utc))
            return await owner.respond("t-time-cc", "2026-10-19", "is it still Monday?", "claude-code/opus", lambda t: None)
    assert run(go()).status == "done"
    first, second = fake_cc.records("stdin")
    assert text_of_cc(first) == "[Mon 2026-10-19 07:42 BST] what time is it?"
    assert text_of_cc(second) == "[Mon 2026-10-19 22:00 BST] is it still Monday?"
    assert "starts with the local time it was sent" in fake_cc.records("start")[0]["system"]


from test_engine_claude_code import cc  # noqa: E402,F401 — fixture（假的 claude + /mcp），上面经 request.getfixturevalue 拿
