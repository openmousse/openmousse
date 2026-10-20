"""第 7 批 12.3：会话记录（GET /v1/openmousse/history，engine/openai_api.py）和 ACP 的 session/load（engine/acp.py）。模型是假模型服务器，
命令行工具是假的；ACP 那条真的起 acp.py 子进程、经 HTTP 连跑在线程里的服务。不连真的模型、不花钱。"""
from __future__ import annotations

import asyncio

from engine import acp
from evals.fakemodel import Reply
from test_engine_acp import Editor, served, updates  # noqa: F401 — 同一套服务、假模型、令牌
from test_engine_api import H, api, ask  # noqa: F401


def run(coro):
    return asyncio.run(coro)


def test_history_is_only_the_tokens_own(api):  # noqa: F811
    api.fm.push(Reply(thinking="Looking.", text="Let me check.", calls=[("schedule", {"args": ["day"]})]), Reply(text="Quiet day."),
                Reply(text="You're welcome."), Reply(text="Books here."))

    async def go():
        async with api.client() as c:
            hd = H("tok-api", **{"X-Session-Id": "s-acp-history"})
            await c.post("/v1/chat/completions", headers=hd, json=ask("what's on today?"))
            await c.post("/v1/chat/completions", headers=hd, json=ask("thanks"))
            await c.post("/v1/chat/completions", headers=H("tok-books", **{"X-Session-Id": "s-acp-history"}),
                         json={"messages": [{"role": "user", "content": "chapter three"}]})  # 同一个键、别的令牌：另一个会话
            get = c.get
            return {"all": await get("/v1/openmousse/history", headers=H("tok-api"), params={"id": "s-acp-history"}),
                    "last2": await get("/v1/openmousse/history", headers=H("tok-api"), params={"id": "s-acp-history", "limit": 2}),
                    "books": await get("/v1/openmousse/history", headers=H("tok-books"), params={"id": "s-acp-history"}),
                    "books_main": await get("/v1/openmousse/history", headers=H("tok-books"), params={"id": "s-acp-history", "agent": "main"}),
                    "nope": await get("/v1/openmousse/history", headers=H("tok-api"), params={"id": "nope"}),
                    "bad": await get("/v1/openmousse/history", headers=H("tok-api"), params={"id": "a\x01b"}),
                    "anon": await get("/v1/openmousse/history", params={"id": "s-acp-history"})}
    r = run(go())
    body = r["all"].json()
    assert r["all"].status_code == 200 and body["agent"] == "main" and body["model"] == "openmousse/main" and body["truncated"] is False
    items = body["data"]
    assert [i["type"] for i in items] == ["user", "thinking", "assistant", "tool", "assistant", "user", "assistant"]
    assert items[0]["text"].endswith("what's on today?") and items[0]["images"] == 0 and items[0]["at"]
    assert items[1]["text"] == "Looking." and items[2]["text"] == "Let me check." and items[4]["text"] == "Quiet day."
    assert {k: items[3][k] for k in ("name", "status", "output")} == {"name": "schedule", "status": "completed", "output": "ok: schedule day"}
    assert items[3]["id"] and items[5]["text"].endswith("thanks") and items[6]["text"] == "You're welcome."
    last2 = r["last2"].json()
    assert [i["type"] for i in last2["data"]] == ["user", "assistant"] and last2["truncated"] is True
    books = r["books"].json()  # api-bookclub 的令牌：只看得到它自己那个会话
    assert books["agent"] == "bookclub" and [i["text"] for i in books["data"] if i["type"] == "assistant"] == ["Books here."]
    for k in ("books_main", "nope"):
        assert r[k].status_code == 404 and r[k].json()["error"]["code"] == "session_not_found", k
    assert r["bad"].status_code == 400 and r["anon"].status_code == 401


def test_session_load_replays_then_carries_on(served):  # noqa: F811
    url, om = served
    om.fm.push(Reply(thinking="Looking at the day.", text="Let me check.", calls=[("schedule", {"args": ["day"]})]),
               Reply(text="You have a quiet day."), Reply(text="Still quiet."))
    first = Editor(url, "tok-api")
    try:
        first.call("initialize", {"protocolVersion": 1, "clientCapabilities": {}})
        sid = first.call("session/new", {"cwd": "/home/demo/acp-load", "mcpServers": []})[0]["result"]["sessionId"]
        assert first.call("session/prompt", {"sessionId": sid, "prompt": [{"type": "text", "text": "what's on today?"}]})[0]["result"]
    finally:
        first.close()
    ed = Editor(url, "tok-api")  # 编辑器重开了：新的适配器进程，什么都不记得
    try:
        init, _ = ed.call("initialize", {"protocolVersion": 1, "clientCapabilities": {}})
        assert init["result"]["agentCapabilities"]["loadSession"] is True
        done, seen = ed.call("session/load", {"sessionId": sid, "cwd": "/home/demo/acp-load", "mcpServers": []})
        assert done["result"]["configOptions"][0]["currentValue"] == "main"
        ups = updates(seen)  # 回话之前全发完
        assert [u["sessionUpdate"] for u in ups] == ["user_message_chunk", "agent_thought_chunk", "agent_message_chunk", "tool_call",
                                                     "agent_message_chunk"]
        assert ups[0]["content"]["text"].endswith("what's on today?") and ups[1]["content"]["text"] == "Looking at the day."
        assert [u["content"]["text"] for u in ups if u["sessionUpdate"] == "agent_message_chunk"] == ["Let me check.", "You have a quiet day."]
        call = ups[3]
        assert call["title"] == "schedule" and call["status"] == "completed" and call["content"][0]["content"]["text"] == "ok: schedule day"
        more, _ = ed.call("session/prompt", {"sessionId": sid, "prompt": [{"type": "text", "text": "and tomorrow?"}]})
        assert more["result"] == {"stopReason": "end_turn"}
        sent = om.fm.of("openai")[-1]["body"]["messages"]
        assert any("You have a quiet day." in str(m.get("content")) for m in sent if m["role"] == "assistant")  # 接在同一个会话后面
        unknown, _ = ed.call("session/load", {"sessionId": "nope", "cwd": "/home/demo", "mcpServers": []})
        assert unknown["error"]["code"] == -32602
    finally:
        ed.close()
    other = Editor(url, "tok-books")  # 别的令牌：看不到这个会话
    try:
        other.call("initialize", {"protocolVersion": 1, "clientCapabilities": {}})
        assert other.call("session/load", {"sessionId": sid, "cwd": "/home/demo", "mcpServers": []})[0]["error"]["code"] == -32602
    finally:
        other.close()


class FakeServer:
    def __init__(self, hist: dict) -> None:
        self.hist, self.paths = hist, []

    def call(self, method: str, path: str, body=None) -> dict:
        self.paths.append(path)
        if path.startswith("/v1/models"):
            return {"data": [{"id": "openmousse/main", "name": "Main"}]}
        if path.startswith("/v1/openmousse/sessions"):
            return {"data": [{"id": "s1", "agent": "main", "cwd": "/w"}]}
        return self.hist


class FakeRpc:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    def notify(self, method: str, params: dict) -> None:
        self.sent.append(params["update"])


def test_replay_shapes():
    hist = {"truncated": True, "data": [{"type": "user", "text": "look", "images": 2}, {"type": "tool", "id": "c1", "name": "mail__send", "status": "pending"},
                                        {"type": "tool", "id": "c2", "name": "memory_search", "status": "failed", "output": "boom"},
                                        {"type": "odd"}, {"type": "tool", "name": "no-id"}]}
    srv, rpc = FakeServer(hist), FakeRpc()
    out = acp.Agent(srv, rpc, None).load({"sessionId": "s1", "cwd": ""})
    assert out["configOptions"][0]["currentValue"] == "main"
    assert any(p.startswith("/v1/openmousse/history?") and f"limit={acp.LOAD_LIMIT}" in p for p in srv.paths)
    first, user, pending, failed = rpc.sent
    assert first["sessionUpdate"] == "agent_message_chunk" and first["content"]["text"] == acp.EARLIER  # 更早的没发：说一句
    assert user["content"]["text"] == "look\n[image]\n[image]"
    assert pending == {"sessionUpdate": "tool_call", "toolCallId": "c1", "title": "mail__send", "status": "pending", "kind": "fetch"}
    assert failed["status"] == "failed" and failed["kind"] == "search" and failed["content"][0]["content"]["text"] == "boom"
