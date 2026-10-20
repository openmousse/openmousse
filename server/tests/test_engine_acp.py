"""ACP 适配器（engine/acp.py）：真的起一个子进程，经 stdio 说 ACP（一行一个 JSON-RPC）；它经 HTTP 连一个真的服务（main.app 跑在
这个进程的一个线程里的 uvicorn 上），服务的模型是假模型服务器（evals/fakemodel.py），命令行工具是假的。不连真的模型、不花钱。"""
from __future__ import annotations

import json
import re
import os
import queue
import socket
import subprocess
import sys
import threading
import time
from itertools import count
from pathlib import Path

import pytest

from engine import openai_api
from evals.fakemodel import Reply
from test_engine_api import api  # noqa: F401 — 同一套假模型、令牌、Agent

ACP = Path(__file__).resolve().parent.parent / "engine" / "acp.py"


@pytest.fixture
def served(api):  # noqa: F811
    """main.app 跑在线程里的 uvicorn 上（不跑 lifespan），回 (地址, api fixture)。"""
    import uvicorn

    import main
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(main.app, lifespan="off", log_level="warning", access_log=False))
    t = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    t.start()
    t0 = time.monotonic()
    while not server.started and time.monotonic() - t0 < 10:
        time.sleep(0.02)
    yield f"http://127.0.0.1:{sock.getsockname()[1]}", api
    server.should_exit = True
    t.join(10)


class Editor:
    """装成编辑器：起 acp.py，发请求、收通知，回它的 session/request_permission。"""

    def __init__(self, url: str, token: str, *args: str) -> None:
        env = {**os.environ, "OPENMOUSSE_TOKEN": token}
        self.p = subprocess.Popen([sys.executable, str(ACP), "--url", url, *args], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, env=env)
        self.q: queue.Queue = queue.Queue()
        self.ids = count(1)
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        for line in self.p.stdout:
            self.q.put(json.loads(line))

    def send(self, msg: dict) -> None:
        self.p.stdin.write((json.dumps({"jsonrpc": "2.0", **msg}) + "\n").encode())
        self.p.stdin.flush()

    def request(self, method: str, params: dict) -> int:
        rid = next(self.ids)
        self.send({"id": rid, "method": method, "params": params})
        return rid

    def wait(self, rid: int, on_permission=None, timeout: float = 20) -> tuple[dict, list[dict]]:
        """等 rid 的回话；路上的通知都收下（→ 第二项），遇到 session/request_permission 交给 on_permission 回。"""
        seen: list[dict] = []
        end = time.monotonic() + timeout
        while True:
            msg = self.q.get(timeout=max(0.1, end - time.monotonic()))
            if msg.get("method") == "session/request_permission":
                seen.append(msg)
                self.send({"id": msg["id"], "result": on_permission(msg)})
            elif "method" in msg:
                seen.append(msg)
            elif msg.get("id") == rid:
                return msg, seen

    def call(self, method: str, params: dict, **kw) -> tuple[dict, list[dict]]:
        return self.wait(self.request(method, params), **kw)

    def close(self) -> str:
        self.p.stdin.close()
        try:
            self.p.wait(10)
        except subprocess.TimeoutExpired:
            self.p.kill()
        return self.p.stderr.read().decode()


def updates(seen: list[dict]) -> list[dict]:
    return [m["params"]["update"] for m in seen if m.get("method") == "session/update"]


def chosen(option: str):
    return lambda msg: {"outcome": {"outcome": "selected", "optionId": option}}


def test_initialize_new_session_prompt_updates_and_end(served):
    url, om = served
    om.fm.push(Reply(thinking="Looking at the day.", text="Let me check.", calls=[("schedule", {"args": ["day"]})]),
                Reply(text="You have a quiet day."))
    ed = Editor(url, "tok-api")
    try:
        init, _ = ed.call("initialize", {"protocolVersion": 1, "clientCapabilities": {}})
        r = init["result"]
        assert r["protocolVersion"] == 1 and r["authMethods"] == [] and r["agentInfo"]["name"] == "openmousse"
        assert r["agentCapabilities"]["sessionCapabilities"] == {"list": {}, "resume": {}, "close": {}}
        assert r["agentCapabilities"]["promptCapabilities"]["image"] is True and r["agentCapabilities"]["loadSession"] is True  # 第 7 批起有 session/load
        new, _ = ed.call("session/new", {"cwd": "/home/demo/notes", "mcpServers": []})
        sid = new["result"]["sessionId"]
        opt = new["result"]["configOptions"][0]
        assert opt["id"] == "agent" and opt["currentValue"] == "main" and {"value": "bookclub", "name": "Book club"} in opt["options"]
        done, seen = ed.call("session/prompt", {"sessionId": sid, "prompt": [{"type": "text", "text": "what's on today?"}]})
        assert done["result"] == {"stopReason": "end_turn"}
        assert {m["params"]["sessionId"] for m in seen} == {sid}
        ups = updates(seen)
        kinds = [u["sessionUpdate"] for u in ups]
        kinds = [k for i, k in enumerate(kinds) if i == 0 or kinds[i - 1] != k]  # 一段段来的：连着同一种的并成一个看
        assert kinds == ["agent_thought_chunk", "agent_message_chunk", "tool_call", "tool_call_update", "agent_message_chunk"]
        thought = "".join(u["content"]["text"] for u in ups if u["sessionUpdate"] == "agent_thought_chunk")
        text = "".join(u["content"]["text"] for u in ups if u["sessionUpdate"] == "agent_message_chunk")
        assert thought == "Looking at the day." and text == "Let me check.\n\nYou have a quiet day."
        assert len({u["messageId"] for u in ups if u["sessionUpdate"] == "agent_message_chunk"}) == 1
        call = next(u for u in ups if u["sessionUpdate"] == "tool_call")
        assert call["title"] == "schedule day" and call["status"] == "in_progress" and call["kind"] == "other"
        end = next(u for u in ups if u["sessionUpdate"] == "tool_call_update")
        assert end == {"sessionUpdate": "tool_call_update", "toolCallId": call["toolCallId"], "status": "completed"}
        assert om.calls == [("schedule", ["day"], "main")]
        sent = om.fm.of("openai")[-2]
        asked = sent["body"]["messages"][-1]["content"]  # 前面带上当地时间（10/19 起，engine/session.with_time）
        assert re.fullmatch(r"\[(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) \d{4}-\d\d-\d\d \d\d:\d\d \w+\] what's on today\?", asked)
        listed, _ = ed.call("session/list", {"cwd": "/home/demo/notes"})
        assert [(s["sessionId"], s["cwd"], s["title"]) for s in listed["result"]["sessions"]] == [(sid, "/home/demo/notes", "what's on today?")]
        assert "configOptions" in ed.call("session/load", {"sessionId": sid, "cwd": "/", "mcpServers": []})[0]["result"]  # 重放见 test_ops_acp.py
        bad, _ = ed.call("session/prompt", {"sessionId": "nope", "prompt": [{"type": "text", "text": "hi"}]})
        assert bad["error"]["code"] == -32602
        rel, _ = ed.call("session/new", {"cwd": "relative/dir", "mcpServers": []})
        assert rel["error"]["code"] == -32602
    finally:
        err = ed.close()
    assert "Traceback" not in err


def test_request_permission_goes_back_to_the_inbox_card(served):
    import inbox
    url, om = served
    om.fm.push(Reply(text="Deleting.", calls=[("journal", {"args": ["delete", "j-acp-1"]})]), Reply(text="Deleted."),
                Reply(calls=[("journal", {"args": ["delete", "j-acp-2"]})]), Reply(text="That one too."),
                Reply(calls=[("agents", {"args": ["delete", "old-helper"]})]), Reply(text="Left it alone."))
    ed = Editor(url, "tok-api")
    try:
        ed.call("initialize", {"protocolVersion": 1, "clientCapabilities": {}})
        sid = ed.call("session/new", {"cwd": "/home/demo", "mcpServers": []})[0]["result"]["sessionId"]
        done, seen = ed.call("session/prompt", {"sessionId": sid, "prompt": [{"type": "text", "text": "delete entry j-acp-1"}]},
                             on_permission=chosen("allow_session"))
        asks = [m for m in seen if m.get("method") == "session/request_permission"]
        assert done["result"] == {"stopReason": "end_turn"} and len(asks) == 1
        p = asks[0]["params"]
        assert p["sessionId"] == sid and [o["optionId"] for o in p["options"]] == ["allow_once", "allow_session", "deny"]
        assert [o["kind"] for o in p["options"]] == ["allow_once", "allow_always", "reject_once"]
        assert p["toolCall"]["status"] == "pending" and "j-acp-1" in p["toolCall"]["title"]
        iid = p["toolCall"]["rawInput"]["inbox"]
        assert inbox.item(iid)["status"] == "done" and om.calls == [("journal", ["delete", "j-acp-1"], "main")]  # 点的结果交回了那张卡
        ups = updates(seen)
        last = [u for u in ups if u["sessionUpdate"] == "tool_call_update"][-1]
        assert last["status"] == "completed" and last["content"][0]["content"]["text"] == "ok: journal delete j-acp-1"
        assert "".join(u["content"]["text"] for u in ups if u["sessionUpdate"] == "agent_message_chunk") == "Deleting.\n\nDeleted."
        # 「这个会话里这一类都同意」：同一类的下一张不再问
        done, seen = ed.call("session/prompt", {"sessionId": sid, "prompt": [{"type": "text", "text": "and j-acp-2"}]},
                             on_permission=chosen("deny"))
        assert done["result"] == {"stopReason": "end_turn"} and not [m for m in seen if m.get("method") == "session/request_permission"]
        assert om.calls[-1] == ("journal", ["delete", "j-acp-2"], "main")
        # 别的一类照样问；拒绝 = 那张卡拒了，模型拿到「用户没同意」
        done, seen = ed.call("session/prompt", {"sessionId": sid, "prompt": [{"type": "text", "text": "remove old-helper"}]},
                             on_permission=chosen("deny"))
        asks = [m for m in seen if m.get("method") == "session/request_permission"]
        assert done["result"] == {"stopReason": "end_turn"} and len(asks) == 1
        assert inbox.item(asks[0]["params"]["toolCall"]["rawInput"]["inbox"])["status"] == "rejected" and len(om.calls) == 2
        assert [u["status"] for u in updates(seen) if u["sessionUpdate"] == "tool_call_update"][-1] == "failed"
    finally:
        ed.close()


def test_a_cancelled_permission_leaves_the_card_in_the_app(served):
    import inbox
    url, om = served
    om.cfg(api_cfg={"wait": 1})  # 服务器只等 1 秒
    om.fm.push(Reply(calls=[("journal", {"args": ["delete", "j-acp-9"]})]), Reply(text="It's waiting for your OK in the app."))
    ed = Editor(url, "tok-api")
    try:
        ed.call("initialize", {"protocolVersion": 1, "clientCapabilities": {}})
        sid = ed.call("session/new", {"cwd": "/home/demo", "mcpServers": []})[0]["result"]["sessionId"]
        done, seen = ed.call("session/prompt", {"sessionId": sid, "prompt": [{"type": "text", "text": "delete j-acp-9"}]},
                             on_permission=lambda msg: {"outcome": {"outcome": "cancelled"}})
        asks = [m for m in seen if m.get("method") == "session/request_permission"]
        assert done["result"] == {"stopReason": "end_turn"} and len(asks) == 1
        assert inbox.item(asks[0]["params"]["toolCall"]["rawInput"]["inbox"])["status"] == "pending" and om.calls == []  # 没替用户决定
        ups = [u for u in updates(seen) if u["sessionUpdate"] == "tool_call_update"]
        assert ups[-1]["status"] == "pending"  # 还在等点头（在 app 里）
        text = "".join(u["content"]["text"] for u in updates(seen) if u["sessionUpdate"] == "agent_message_chunk")
        assert text.startswith("It's waiting for your OK in the app.") and "(Waiting for your OK:" in text
        # 换一个 Agent：同一个 sessionId，服务器上是那个 Agent 的对话
        opts, _ = ed.call("session/set_config_option", {"sessionId": sid, "configId": "agent", "value": "bookclub"})
        assert opts["result"]["configOptions"][0]["currentValue"] == "bookclub"
        bad, _ = ed.call("session/set_config_option", {"sessionId": sid, "configId": "agent", "value": "nobody"})
        assert bad["error"]["code"] == -32602
        om.fm.push(Reply(text="Book club here."))
        done, _ = ed.call("session/prompt", {"sessionId": sid, "prompt": [{"type": "text", "text": "which chapter?"}]})
        assert done["result"] == {"stopReason": "end_turn"} and om.fm.of("openai")[-1]["body"]["messages"][-1]["content"].endswith("] which chapter?")
        assert 'You are the Agent "Book club"' in om.fm.of("openai")[-1]["body"]["messages"][0]["content"]
        assert ed.call("session/close", {"sessionId": sid})[0]["result"] == {}
        gone, _ = ed.call("session/prompt", {"sessionId": sid, "prompt": [{"type": "text", "text": "still there?"}]})
        assert gone["error"]["code"] == -32602
    finally:
        ed.close()


def test_cancel_stops_the_turn_and_resume_reattaches(served):
    url, om = served
    om.fm.push(Reply(text="a long, slow answer " * 10, chunk=4, delay=0.05), Reply(text="Back again."))
    ed = Editor(url, "tok-books")
    try:
        ed.call("initialize", {"protocolVersion": 1, "clientCapabilities": {}})
        new = ed.call("session/new", {"cwd": "/home/demo", "mcpServers": []})[0]["result"]
        sid = new["sessionId"]
        assert new["configOptions"][0]["options"] == [{"value": "bookclub", "name": "Book club"}]  # api-<agent>：只有它自己
        rid = ed.request("session/prompt", {"sessionId": sid, "prompt": [{"type": "text", "text": "tell me a story"}]})
        t0 = time.monotonic()
        while not openai_api.RUNS and time.monotonic() - t0 < 10:
            time.sleep(0.01)
        early: list[dict] = []  # 等第一段字到了再点停：CI 的机器慢，固定等 0.2 秒时常常一个字都还没出（10/17 CI 上挂过）
        done = None
        while done is None and time.monotonic() - t0 < 10:
            try:
                msg = ed.q.get(timeout=0.05)
            except queue.Empty:
                continue
            if msg.get("id") == rid:
                done = msg
                break
            early.append(msg)
            if "a long" in "".join(u["content"]["text"] for u in updates(early) if u["sessionUpdate"] == "agent_message_chunk"):
                break  # 出来的字够认了（下面要核对停之前的字留在会话里）
        ed.send({"method": "session/cancel", "params": {"sessionId": sid}})
        seen: list[dict] = []
        if done is None:
            done, seen = ed.wait(rid)
        seen = early + seen
        assert done["result"] == {"stopReason": "cancelled"}
        t0 = time.monotonic()
        while openai_api.RUNS and time.monotonic() - t0 < 10:  # 服务器那边 = 点了停
            time.sleep(0.02)
        assert not openai_api.RUNS
        text = "".join(u["content"]["text"] for u in updates(seen) if u["sessionUpdate"] == "agent_message_chunk")
        assert 0 < len(text) < len("a long, slow answer " * 10)
        ed2 = Editor(url, "tok-books")  # 换一个进程（编辑器重开了）：resume 接上服务器上的会话
        try:
            ed2.call("initialize", {"protocolVersion": 1, "clientCapabilities": {}})
            assert ed2.call("session/resume", {"sessionId": sid, "cwd": "/home/demo", "mcpServers": []})[0]["result"]["configOptions"]
            done, _ = ed2.call("session/prompt", {"sessionId": sid, "prompt": [{"type": "text", "text": "go on"}]})
            assert done["result"] == {"stopReason": "end_turn"}
            msgs = om.fm.of("openai")[-1]["body"]["messages"]
            assert any("a long" in (m.get("content") or "") for m in msgs if m["role"] == "assistant")  # 停之前出来的字还在会话里
        finally:
            ed2.close()
    finally:
        ed.close()
