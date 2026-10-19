"""10/19 彩排补的第 6b 件：Claude Code 跑的主人回合里同一句出现两次（「早餐记好了」）。查下来两种来路，都修了（engine/claude_brain.py）：
  事件重复了  它流到一半断了就重问一次，整条回复换个 id 重来；流出来的那份它自己撤了（tombstone），stream-json 不输出，我们就把两份都
              给了 app。现在认出来（上一次没等到 message_stop 就又开了一次）：撤掉那份（Turn._abandon → app 那边整段换掉，会话里只有重来的）。
  拼起来了    工具前说了一句，做完的总结又整个说了一遍：前一句不留（Turn.drop_repeats，只比挨着的两段，前一段整个在后一段里才算）。
假的 claude 是 tests/fake_claude.py（broken 那一步照 Claude Code 2.1.42 吐），不连真的模型。"""
from __future__ import annotations

import asyncio

import pytest

from engine import events
from engine.session import DbSession, day_key, text_of
from test_engine_claude_code import cc, collect, finish, serving  # noqa: F401 — cc 是那边的 fixture，这里经 claude 拿


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def claude(request):
    return request.getfixturevalue("cc")


def chat_turn(claude, thread: str, text: str):
    """在 app 的对话里跑一轮（chat.start_run，跟 app 发一句一样）→ (Run, SSE 事件)。"""
    import chat

    async def go():
        async with serving(claude):
            r = chat.start_run(thread, text, "claude-code/opus")
            evs = await collect(chat.attach(r))
            await finish(chat, thread)
            return r, evs
    return run(go())


def said(chat, r) -> str:
    """写进我们会话的那条回复（下一轮换别的模型时它看到的）。"""
    s = DbSession.latest(day_key(r.thread, chat.day_of(r.started)))
    return next(text_of(m) for m in reversed(s.messages()) if m["role"] == "assistant")


def test_a_reply_resent_after_the_stream_broke_is_shown_once(claude):
    import chat
    claude.scenario_([[{"broken": "早餐记", "again": "早餐记好了：燕麦粥一碗。"}]])
    r, evs = chat_turn(claude, "t-cc-resend", "早餐吃了燕麦粥")
    assert r.status == "ok" and r.text == "早餐记好了：燕麦粥一碗。" and said(chat, r) == "早餐记好了：燕麦粥一碗。"
    deltas = "".join(d["text"] for e, d in evs if e == "delta")
    assert "早餐记早餐记好了" in deltas.replace("\n", "")  # 流出来的那半截确实先给过 app……
    assert ("text", {"text": ""}) in evs and dict(evs)["done"]["text"] == "早餐记好了：燕麦粥一碗。"  # ……然后整段换掉
    steps = [s for s in events.steps(events.turns("t-cc-resend")[0]["id"]) if s["type"] == "model"]
    assert [s["status"] for s in steps] == ["error", "ok"]  # 断了的那次记成没成


def test_the_sentence_before_a_tool_call_is_kept_once_when_it_is_resent(claude):
    """断在工具调用上：那句话和工具调用一起重来；工具只做一次，前面说过的话也只留一份。"""
    import chat
    claude.scenario_([[{"broken": "我来看看今天的安排。", "block": True, "lost_call": True, "again": "我来看看今天的安排。", "call": "schedule",
                        "input": {"args": ["day"]}}, {"say": "今天下午三点有牙医。"}]])
    r, evs = chat_turn(claude, "t-cc-resend-tool", "今天有什么安排")
    assert r.status == "ok" and r.text == "我来看看今天的安排。\n\n今天下午三点有牙医。" == said(chat, r)
    assert claude.calls == [("schedule", ["day"], "main")]  # 撤掉的那次的工具调用没再做
    tools = [s for s in events.steps(events.turns("t-cc-resend-tool")[0]["id"]) if s["type"] == "tool"]
    assert [s["status"] for s in tools] == ["aborted", "ok"]
    ends = [d for e, d in evs if e == "progress"][-1]["steps"]
    assert all(s["status"] != "running" for s in ends)  # app 上没有一直转着的那一步


def test_a_resend_that_streams_again_is_also_caught(claude):
    """换 --fallback-model 重问：重来的那次照常流（又一个 message_start），上一次还是没有 message_stop。"""
    import chat
    claude.scenario_([[{"broken": "Logged your", "again": "Logged your breakfast: oats.", "streamed": True}]])
    r, _ = chat_turn(claude, "t-cc-restream", "had oats")
    assert r.text == "Logged your breakfast: oats." == said(chat, r)


def test_a_remark_the_summary_repeats_is_not_shown_twice(claude):
    import chat
    claude.scenario_([[{"say": "早餐记好了！"}, {"tool": "schedule", "input": {"args": ["day"]}}, {"say": "早餐记好了：燕麦粥一碗，约 300 千卡。"}]])
    r, evs = chat_turn(claude, "t-cc-repeat", "早餐吃了燕麦粥")
    assert r.text == "早餐记好了：燕麦粥一碗，约 300 千卡。" == said(chat, r)
    assert ("text", {"text": "早餐记好了：燕麦粥一碗，约 300 千卡。"}) in evs


def test_different_sentences_before_and_after_a_tool_both_stay(claude):
    import chat
    claude.scenario_([[{"say": "我先看看今天的日程。"}, {"tool": "schedule", "input": {"args": ["day"]}}, {"say": "下午三点有牙医，别的都空着。"}]])
    r, evs = chat_turn(claude, "t-cc-two", "今天忙吗")
    assert r.text == "我先看看今天的日程。\n\n下午三点有牙医，别的都空着。" == said(chat, r)
    assert not [d for e, d in evs if e == "text"]  # 没有改过


def test_the_system_prompt_asks_not_to_say_it_again(claude):
    claude.scenario_([[{"say": "hi"}]])
    chat_turn(claude, "t-cc-note", "hello")
    assert "carry on from there instead of saying it again" in claude.records("start")[0]["system"]
