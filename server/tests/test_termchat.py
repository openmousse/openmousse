"""第 8 批：终端里的对话（mousse chat，termchat.py）。对着真跑的测试服务器（uvicorn，在线程里）、自己的内核、假的模型（FakeModel 按剧本回放）：
发一句、回复边来边打、工具一步一行；这一轮新交的要你点头的事请你去 app 里确认；回复进行中按 Ctrl-C 停下这一轮、接回来把结尾读完；
选择题在终端里直接答；没有这个 Agent、Agent 自己的命令里用，都说清楚。不连真模型、不推送。"""
from __future__ import annotations

import asyncio
import io
import json
import socket
import threading
import time

import pytest

import mousse
import termchat
from engine.session import text_of
from test_engine import FakeModel


def wait_for(cond, timeout: float = 10.0, what: str = "condition"):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {what}")


@pytest.fixture
def live(server_json, monkeypatch):
    """main.app 在一个线程里由 uvicorn 跑（不跑 lifespan）；自己的内核；不推送；主对话里没有等你点头的卡。model(剧本) 换上假模型。"""
    import uvicorn

    import chat
    import inbox
    import main
    import push
    from engine import providers

    async def no_push(*a, **k):
        return {"ok": True, "sent": 0}
    monkeypatch.setattr(push, "send_push", no_push)
    server_json({"claw": {"kind": "builtin"}, "timezone": "UTC", "language": "en", "auth": {"tokens": {"phone": "tok-phone"}},
                 "engine": {"model": "deepseek/deepseek-flash"}})
    with chat._lock, inbox.idb() as conn:
        conn.execute("DELETE FROM inbox WHERE thread='main'")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    loop = asyncio.new_event_loop()
    srv = uvicorn.Server(uvicorn.Config(main.app, host="127.0.0.1", port=port, lifespan="off", log_level="warning"))

    def serve():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(srv.serve())
    t = threading.Thread(target=serve, daemon=True)
    t.start()
    wait_for(lambda: srv.started, what="the test server")

    class Live:
        url = f"http://127.0.0.1:{port}"
        api = termchat.Api(f"http://127.0.0.1:{port}", "tok-phone")

        @staticmethod
        def model(script, before=None):
            """假模型；before(第几次调用) 在回放那一次之前跑（在服务器的事件循环里）。"""
            f = FakeModel(script)

            async def stream(ref, req):
                if before is not None:
                    await before(len(f.requests))
                async for ev in f.stream(ref, req):
                    yield ev
            monkeypatch.setattr(providers, "stream", stream)
            return f
    yield Live
    wait_for(lambda: not chat.RUNS.get("main") or chat.RUNS["main"].done, what="the turn to finish")
    srv.should_exit = True
    t.join(10)


def lines(*said: str):
    """照着念的输入：说完了 = Ctrl-D。记下每次的提示。"""
    it = iter(said)
    prompts: list[str] = []

    def read(prompt: str) -> str:
        prompts.append(prompt)
        try:
            return next(it)
        except StopIteration:
            raise EOFError from None
    read.prompts = prompts  # type: ignore[attr-defined]
    return read


def test_a_turn_prints_the_reply_tool_steps_and_cards_to_confirm_in_the_app(live):
    import inbox

    async def card_on_second_call(n: int) -> None:
        if n == 1:  # 第二次调模型之前：这一轮交了一张要主人点头的卡
            await inbox.add(inbox.ItemIn(kind="other", title="Order the cake for the fictional Wren Bakery party", source="main", thread="main"))
    f = live.model([{"chunks": ["Let me ", "look."], "calls": [("c1", "skill", {"name": "no-such-skill"})]},
                    {"chunks": ["Nothing ", "there yet."]}], before=card_on_second_call)
    out = io.StringIO()
    code = termchat.chat(live.api, "main", out, lines("what's on for the party?", "/quit"))
    text = out.getvalue()
    assert code == 0
    assert text.startswith("Talking to TestMousse (main).")
    assert "Let me look.\n  · skill no-such-skill\n" in text  # 回复打到一半，工具另起一行
    assert "Nothing there yet." in text
    assert "1 item(s) need your OK. Confirm in the app (Inbox):\n  • Order the cake for the fictional Wren Bakery party\n" in text
    assert [m["role"] for m in f.requests[0][1].messages][-1] == "user" and text_of(f.requests[0][1].messages[-1]).endswith("what's on for the party?")


def test_ctrl_c_stops_the_turn_and_reads_the_end(live):
    live.model([{"chunks": ["Part one. ", "Part two. ", "Part three."], "delay": 0.4}])

    class Out(io.StringIO):
        interrupted = False

        def write(self, s: str) -> int:
            if "Part one" in s and not self.interrupted:  # 第一段字一出来就按 Ctrl-C
                self.interrupted = True
                raise KeyboardInterrupt
            return super().write(s)
    out = Out()
    code = termchat.chat(live.api, "main", out, lines("tell me a long story", "/quit"))
    text = out.getvalue()
    assert code == 0 and out.interrupted
    assert text.count("Part one.") == 1 and "Part three." not in text  # 接回来时已经打过的不再打
    assert text.count("(Stopped)") == 1  # 回复的结尾带着，不另说一遍
    import chat
    assert chat.RUNS["main"].status == "stopped"


def test_a_question_is_answered_in_the_terminal(live):
    q = "Which color for the fictional Wren Bakery logo?"
    f = live.model([{"text": "One question first.", "calls": [("c1", "ask_user", {"question": q, "options": ["Blue", "Red"]})]},
                    {"text": "Red it is."}])
    read = lines("make me a logo", "2", "/quit")
    out = io.StringIO()
    assert termchat.chat(live.api, "main", out, read) == 0
    text = out.getvalue()
    assert q in text and "Red it is." in text
    assert read.prompts[1].startswith("Answer (number, option or your own words")
    result = [m for m in f.requests[1][1].messages if m["role"] == "tool"][-1]  # 主对话今天的会话里还有前面几个测试的
    assert json.loads(text_of(result))["option"] == "Red"


def test_slash_stop_with_nothing_running_and_an_unknown_agent(live):
    out = io.StringIO()
    assert termchat.chat(live.api, "main", out, lines("/stop")) == 0
    assert "(No reply in progress)" in out.getvalue()
    out = io.StringIO()
    assert termchat.chat(live.api, "no-such-agent", out, lines()) == 1
    assert out.getvalue().startswith("No such Agent: no-such-agent (there are: main")


def test_wrong_token_and_no_server_say_so(live):
    out = io.StringIO()
    assert termchat.chat(termchat.Api(live.url, "wrong"), "main", out, lines()) == 1
    assert out.getvalue().strip() == "Wrong token: pass one that works with --token or MOUSSE_TOKEN (python3 tokens.py add <name>)"
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        dead = s.getsockname()[1]
    out = io.StringIO()
    assert termchat.chat(termchat.Api(f"http://127.0.0.1:{dead}", "tok-phone"), "main", out, lines()) == 1
    assert out.getvalue().startswith(f"Can't reach the server at http://127.0.0.1:{dead}")


def test_not_from_inside_an_agents_own_command(monkeypatch, capsys):
    monkeypatch.setenv("MOUSSE_AGENT", "main")
    assert termchat.main("http://127.0.0.1:9", "t", "main") == 2
    assert "can't run inside an Agent's own command" in capsys.readouterr().err


def test_mousse_chat_passes_the_server_token_and_agent(monkeypatch):
    got: list = []
    monkeypatch.setattr(termchat, "main", lambda base, token, agent: got.append((base, token, agent)) or 0)
    monkeypatch.delenv("MOUSSE_TOKEN", raising=False)
    monkeypatch.delenv("MOUSSE_SERVER", raising=False)
    assert mousse.main(["chat", "--agent", "diet", "--server", "mac.example:8080", "--token", "tok-x"]) == 0
    monkeypatch.setenv("MOUSSE_TOKEN", "tok-env")
    assert mousse.main(["chat"]) == 0
    assert got[0] == ("http://mac.example:8080", "tok-x", "diet")
    assert got[1][1:] == ("tok-env", "main") and got[1][0].startswith("http://")
