"""内核 ⑫b B：ask_user（engine/ask.py）。问题是这一轮回复里的一段字 + SSE 的 ask_user 事件（重连补发）；用户插的下一句就是回答
（数字、选项的字、自己的话），记在工具结果上（ref：撤回、重启认得出）；POST /api/engine/answer 和插话只算先到的那一次；只认选项时
别的话照常当插话；带附件的回话收起问题、照常排成下一轮；等不到；点停；哪些回合没有这个工具。Claude Code 跑的回合经 /mcp 也能问
（test_engine_claude_code.py）。模型是假的（FakeModel 按剧本回放）。"""
from __future__ import annotations

import asyncio
import json
import time

import pytest
from fastapi import HTTPException

import engine
from engine import ask, owner, tools, unattended
from engine.session import DbSession, day_key, text_of
from test_engine import FakeModel
from test_engine_events import collect, msg_row, replies, until

BUILTIN = {"claw": {"kind": "builtin"}, "engine": {"model": "deepseek/deepseek-flash"}}
Q = "Which color should the logo for the fictional Wren Bakery be?"
OPTS = ["Blue (Recommended)", "Red"]


@pytest.fixture
def fake(monkeypatch):
    from engine import providers

    def make(script):
        f = FakeModel(script)
        monkeypatch.setattr(providers, "stream", f.stream)
        return f
    return make


def run(coro):
    return asyncio.run(coro)


def asks(**args) -> dict:
    return {"calls": [("c1", "ask_user", {"question": Q, **args})]}


def shown(options: list[str], free: bool = True) -> str:
    rows = "\n".join(f"{i}. {o}" for i, o in enumerate(options, 1))
    hint = "Reply with the number, the option text, or your own answer." if free else "Reply with the number or the option text."
    return f"{Q}\n\n{rows}\n\n{hint}"


# —— 问、等、插话当回答 ————————————————————————————————————————————————————————————————

def test_the_question_streams_as_text_and_the_next_message_answers_it(server_json, fake):
    import chat
    server_json(BUILTIN)
    f = fake([{"text": "Let me check one thing.", **asks(options=OPTS)}, {"text": "Blue it is."}])

    async def go():
        r = chat.start_run("t-ask", "make me a logo", None)
        first = asyncio.create_task(collect(chat.attach(r)))
        await until(lambda: r.asking is not None)
        again = await collect(chat.attach(r), until_event="ask_user")  # 断线重连：补发正在等的问题
        resp = await chat.send(chat.SendBody(thread="t-ask", text="1"))
        second = await collect(resp.body_iterator)
        return r, await first, again, second
    r, first, again, second = run(go())
    asked = next(d for e, d in first if e == "ask_user")
    assert asked == {"id": asked["id"], "question": Q, "options": OPTS, "free": True, "timeout": 300} and asked["id"].startswith("q_")
    assert again[-1] == ("ask_user", asked)
    assert r.text == f"Let me check one thing.\n\n{shown(OPTS)}\n\nBlue it is." and dict(first)["done"]["text"] == r.text  # 问题单独成段
    assert [d["status"] for e, d in first if e == "ask_user_done"] == ["answered"] and r.asking is None
    kind, data = second[0]
    assert kind == "queued" and data["steer"] is True and dict(second)["done"]["id"] == dict(first)["done"]["id"]  # 还是同一条回复
    sid = int(data["userId"][2:])
    msgs = f.requests[1][1].messages
    assert [m["role"] for m in msgs[-2:]] == ["assistant", "tool"] and msgs[-1]["ref"] == sid  # 回答没有另起一条 user，记在工具结果上
    assert json.loads(text_of(msgs[-1])) == {"status": "answered", "question": Q, "answer": "1", "choice": 1, "option": "Blue (Recommended)",
                                             "message": "The user answered your question. Carry on with their answer in mind."}
    assert msg_row(chat, sid)["status"] == "steered" and len(replies(chat, "t-ask")) == 1
    s = DbSession.latest(day_key("t-ask", chat.day_of(r.started)))
    assert sid in owner.refs(s.id) and ask._open == {}  # 重启以后认得出这一句已经用掉了
    assert "## Questions, to-do lists and plans" in f.requests[0][1].system and "ask_user" in {t["name"] for t in f.requests[0][1].tools}
    assert owner.rewind("t-ask", chat.day_of(r.started), sid)  # 撤回这一句回答：退到提问的那个调用（它没了结果）
    after = s.messages()
    assert after[-1]["role"] == "assistant" and after[-1]["content"][-1]["name"] == "ask_user"


def test_answers_match_by_number_or_text_and_strict_questions_let_other_words_through(server_json, fake):
    import chat
    got = [ask.match(t, OPTS) for t in ("2", "2.", "选 2", "option 1", "#2", "red", "Blue", "BLUE (recommended)", "Red!", "3", "maybe red", "")]
    assert got == [2, 2, 2, 1, 2, 2, 1, 1, 2, None, None, None]
    assert ask.clean_options(["  A  ", "A", {"label": "B"}, "", "C", "D", "E"]) == ["A", "B", "C", "D"]  # 去重、最多 4 个
    server_json(BUILTIN)
    f = fake([asks(options=OPTS, allow_free_text=False), {"text": "Okay, no logo then."}])

    async def go():
        r = chat.start_run("t-ask-strict", "make me a logo", None)
        await until(lambda: r.asking is not None)
        resp = await chat.send(chat.SendBody(thread="t-ask-strict", text="actually, skip the logo"))
        return r, await collect(resp.body_iterator)
    r, second = run(go())
    sid = int(second[0][1]["userId"][2:])
    msgs = f.requests[1][1].messages
    assert json.loads(text_of(msgs[-2]))["status"] == "not_answered" and "ref" not in msgs[-2]
    assert text_of(msgs[-1]) == "actually, skip the logo" and msgs[-1]["ref"] == sid  # 不算回答：照常当插话
    assert dict(second)["done"]["text"] == f"{shown(OPTS, free=False)}\n\nOkay, no logo then." and r.asking is None


def test_the_answer_endpoint_and_the_chat_count_only_once(server_json, fake):
    import chat
    server_json(BUILTIN)
    f = fake([asks(options=["Blue", "Red"]), {"text": "Red it is."}, {"text": "And noted."}])

    async def go():
        r = chat.start_run("t-ask-api", "make me a logo", None)
        await until(lambda: r.asking is not None)
        qid = r.asking["id"]
        errors = []
        for body in (ask.AnswerIn(conversation="t-other", id=qid, choice=2), ask.AnswerIn(conversation="t-ask-api", id="q_nope", choice=2),
                     ask.AnswerIn(conversation="t-ask-api", id=qid, choice=5), ask.AnswerIn(conversation="t-ask-api", id=qid)):
            with pytest.raises(HTTPException) as e:
                await ask.answer(body)
            errors.append(e.value.status_code)
        got = await ask.answer(ask.AnswerIn(conversation="t-ask-api", id=qid, choice="red"))
        with pytest.raises(HTTPException) as twice:
            await ask.answer(ask.AnswerIn(conversation="t-ask-api", id=qid, choice=1))
        resp = await chat.send(chat.SendBody(thread="t-ask-api", text="1"))  # 已经答过：这一句不再算回答，照常是插话
        late = await collect(resp.body_iterator)
        await until(lambda: r.done)
        return r, qid, got, errors, twice.value.status_code, late
    r, qid, got, errors, twice, late = run(go())
    assert errors == [404, 404, 400, 400] and twice == 409
    mid = int(got["messageId"][2:])
    assert got == {"ok": True, "id": qid, "messageId": f"db{mid}", "answer": "Red", "choice": 2}
    row = msg_row(chat, mid)
    assert (row["role"], row["text"], row["status"]) == ("user", "Red", "ok")  # 按钮的回答也记进对话（是用户说的）
    result = next(m for m in f.requests[1][1].messages if m["role"] == "tool")
    assert result["ref"] == mid and json.loads(text_of(result))["option"] == "Red"
    assert late[0][0] == "queued" and late[0][1]["steer"] is True
    later = [text_of(m) for _, req in f.requests[1:] for m in req.messages if m["role"] == "user"]
    assert "1" in later  # 晚到的那句照常并进这一轮


def test_free_answers_and_strict_questions_on_the_endpoint(server_json):
    import chat

    async def go():
        c = engine.Control()
        fut = c.expect_answer()
        ask._open["q_strict"] = ask.Pending("q_strict", "t-ask-direct", "turn-x", Q, ["Blue", "Red"], False, c)
        try:
            with pytest.raises(HTTPException) as e:
                await ask.answer(ask.AnswerIn(conversation="t-ask-direct", id="q_strict", text="Teal, please"))
            ask._open["q_strict"].free = True
            got = await ask.answer(ask.AnswerIn(conversation="t-ask-direct", id="q_strict", text="  Teal,\n please "))
            return e.value.status_code, got, fut.result()
        finally:
            ask._open.pop("q_strict", None)
    status, got, msg = run(go())
    assert status == 400 and got["answer"] == "Teal, please" and got["choice"] is None
    assert text_of(msg) == "Teal, please" and msg["via"] == "button" and msg["ref"] == int(got["messageId"][2:])
    assert msg_row(chat, msg["ref"])["thread"] == "t-ask-direct"


def test_a_reply_with_attachments_puts_the_question_away(server_json, fake, tmp_path):
    import chat
    import files as files_mod
    server_json(BUILTIN)
    sketch = tmp_path / "sketch.txt"
    sketch.write_text("a round blue logo", encoding="utf8")
    with files_mod._lock, files_mod.adb() as conn:
        conn.execute("INSERT INTO attachments(id, thread, name, mime, size, kind, path, text, chars, note, status, created_at) "
                     "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", ("att-ask-1", "t-ask-att", "sketch.txt", "text/plain", 17, "doc", str(sketch),
                                                          "a round blue logo", 17, None, "ok", chat.now_iso()))
    f = fake([asks(options=OPTS), {"text": "I'll look at your sketch next."}, {"text": "Going with the round blue one."}])

    async def go():
        r = chat.start_run("t-ask-att", "make me a logo", None)
        first = asyncio.create_task(collect(chat.attach(r)))
        await until(lambda: r.asking is not None)
        resp = await chat.send(chat.SendBody(thread="t-ask-att", text="here's my sketch", attachments=["att-ask-1"]))
        return r, await first, await collect(resp.body_iterator)
    r, first, second = run(go())
    out = json.loads(text_of(f.requests[1][1].messages[-1]))
    assert out["status"] == "not_answered" and "attachments" in out["message"]
    assert [d["status"] for e, d in first if e == "ask_user_done"] == ["other"]
    assert "steer" not in second[0][1] and dict(second)["done"]["text"] == "Going with the round blue one."  # 照常排成下一轮
    assert "a round blue logo" in text_of(f.requests[2][1].messages[-1])


def test_no_answer_in_time_tells_the_model_to_ask_in_its_reply(server_json, fake):
    import chat
    server_json({**BUILTIN, "engine": {**BUILTIN["engine"], "ask_user": {"timeout": 1}}})
    f = fake([asks(), {"text": "Which color would you like? Tell me when you're back."}])

    async def go():
        r = chat.start_run("t-ask-late", "make me a logo", None)
        return r, await collect(chat.attach(r))
    r, evs = run(go())
    asked = next(d for e, d in evs if e == "ask_user")
    assert asked["timeout"] == 1 and asked["options"] == [] and asked["free"] is True
    out = json.loads(text_of(f.requests[1][1].messages[-1]))
    assert out["status"] == "no_answer" and "end this turn" in out["message"] and "Don't take the silence as a yes" in out["message"]
    assert [d["status"] for e, d in evs if e == "ask_user_done"] == ["timeout"] and r.status == "ok"
    assert r.text == f"{Q}\n\nWhich color would you like? Tell me when you're back."  # 没有选项：只有问题那一句
    assert ask.timeout() == 1
    server_json({**BUILTIN, "engine": {**BUILTIN["engine"], "ask_user": {"timeout": 99999}}})
    assert ask.timeout() == ask.TIMEOUT_MAX


def test_stop_while_waiting_ends_the_turn_and_the_question(server_json, fake):
    import chat
    server_json(BUILTIN)
    f = fake([{"text": "One question first.", **asks(options=OPTS)}])

    async def go():
        r = chat.start_run("t-ask-stop", "make me a logo", None)
        first = asyncio.create_task(collect(chat.attach(r)))
        await until(lambda: r.asking is not None)
        qid = r.asking["id"]
        t0 = time.monotonic()
        await chat.stop(chat.StopBody(thread="t-ask-stop"))
        await until(lambda: r.done, 3)
        took = time.monotonic() - t0
        with pytest.raises(HTTPException) as e:
            await ask.answer(ask.AnswerIn(conversation="t-ask-stop", id=qid, choice=1))
        return r, await first, took, e.value.status_code
    r, evs, took, status = run(go())
    assert r.status == "stopped" and took < 2 and status == 404 and len(f.requests) == 1
    assert [d["status"] for e, d in evs if e == "ask_user_done"] == ["stopped"] and r.asking is None
    s = DbSession.latest(day_key("t-ask-stop", chat.day_of(r.started)))
    assert text_of(next(m for m in s.messages() if m["role"] == "tool")) == "Stopped by the user."


# —— 谁有这个工具 ————————————————————————————————————————————————————————————————————

def test_only_turns_someone_is_watching_get_ask_user(server_json, fake):
    import chat
    from engine import cyber
    server_json(BUILTIN)

    def names(ts) -> set[str]:
        return {t["name"] if isinstance(t, dict) else t.name for t in ts}
    assert {"ask_user", "todo", "enter_plan_mode", "propose_plan"} <= names(tools.chat_tools("main", watched=True))
    assert names(tools.chat_tools("main", watched=False)) == {"todo", "enter_plan_mode", "propose_plan"}
    assert tools.chat_tools("errand", watched=True) == []  # 代办只有沙箱那一套
    assert not names(unattended.tools_for("main")) & owner.CHAT_TOOLS  # 定时任务、心跳
    f = fake([{"text": "a"}, {"text": "b"}, {"text": "c"}, {"text": "d"}, {"text": "e"}])

    async def go():
        for thread, origin in (("t-w-user", "user"), ("t-w-auto", "auto")):
            r = chat.start_run(thread, "hello", None, origin=origin)
            await until(lambda: r.done)
        await owner.respond("t-w-back", "2026-10-16", "results are in", None, lambda t: None)  # 交回结果开的那一轮（watched 默认 False）
        await owner.respond("api:main:x", "2026-10-16", "hi", None, lambda t: None, origin="api", agent="main")  # 对外接口（⑭）
        await cyber.chat({"id": "pip", "name": "Pip", "identity": "A fictional pen pal."}, "hi")
    run(go())
    got = [names(req.tools) for _, req in f.requests]
    assert "ask_user" in got[0] and {"todo", "propose_plan"} <= got[0]
    assert "ask_user" not in got[1] and "todo" in got[1]  # 定时器之类发的（origin auto）：没人在看
    assert "ask_user" not in got[2] and "todo" in got[2]
    assert not got[3] & owner.CHAT_TOOLS and got[4] == set()  # 对外接口、赛博：这几样都没有
    server_json({**BUILTIN, "engine": {**BUILTIN["engine"], "tools": {"levels": {"ask_user": "off", "todo": "off"}}}})
    assert names(tools.chat_tools("main", watched=True)) == {"enter_plan_mode", "propose_plan"}  # 写 off 的不给
    r = chat.Run(thread="t-x", model="m", user_id=1, started=chat.now_iso(), watched=True)
    assert r.watched and not chat.Run(thread="t-x", model="m", user_id=1, started=chat.now_iso()).watched
