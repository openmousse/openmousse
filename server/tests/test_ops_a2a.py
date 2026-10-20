"""第 7 批 17：主人自己的 A2A（engine/a2a_api.py，挂在 /v1 上）。默认关；名片；SendMessage / GetTask / ListTasks / CancelTask；同一条消息
不跑第二次；别的令牌看不到；等点头的卡 → AUTH_REQUIRED，点了以后 COMPLETED；returnImmediately；各种错误码。模型是 evals/fakemodel.py 的
假模型服务器（真的走 HTTP），请求经 main.app 打（/v1 的开关、令牌都是真的）；不连真的模型、不花钱。"""
from __future__ import annotations

import asyncio
import base64
import time
import uuid
from types import SimpleNamespace

import httpx
import pytest

import bridges
import chat
import data
from config import settings
from engine import a2a_api, loop, provider_openai
from evals.fakemodel import FakeModelServer, Reply

TOKENS = {"api": "tok-api", "api-bookclub": "tok-books", "phone": "tok-phone"}


def H(tok: str, **extra: str) -> dict:
    return {"Authorization": f"Bearer {tok}", "A2A-Version": "1.0", **extra}


def run(coro):
    return asyncio.run(coro)


def rpc(method: str, params: dict | None = None, id_="r1") -> dict:
    return {"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}}


def msg(text: str, **kw) -> dict:
    return {"message": {"messageId": kw.pop("mid", None) or uuid.uuid4().hex, "role": "ROLE_USER", "parts": [{"text": text}], **kw}}


@pytest.fixture
def api(server_json, monkeypatch, request):
    import main
    fm = FakeModelServer([]).start()
    request.addfinalizer(fm.stop)
    monkeypatch.setattr(loop, "MODEL_RETRIES", (0, 0))
    monkeypatch.setattr(provider_openai, "RETRY_DELAYS", (0, 0))
    calls: list = []

    async def fake_run(name, args, stdin, agent, *, kill_on_cancel=False):
        calls.append((name, list(args), agent))
        return f"ok: {name} {' '.join(args)}"
    monkeypatch.setattr(bridges, "run", fake_run)
    with chat._lock, data.ddb() as conn:
        conn.execute("INSERT OR REPLACE INTO groups(id, name, icon, purpose, created_at) VALUES('bookclub', 'Book club', 'book', "
                     "'Reading notes.', ?)", (chat.now_iso(),))

    def cfg(api_cfg=None):
        server_json({"claw": {"kind": "builtin"}, "auth": {"tokens": TOKENS},
                     "engine": {"model": "fake/m1", "providers": {"fake": {"api": "openai", "url": fm.openai_url, "key_env": ""}},
                                "api": {"a2a": True} if api_cfg is None else api_cfg}})
    cfg()

    def client() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test", timeout=30)
    return SimpleNamespace(fm=fm, cfg=cfg, client=client, calls=calls)


def test_off_unless_engine_api_a2a_is_true(api):
    async def go():
        async with api.client() as c:
            out = {}
            for name, conf in (("plain", True), ("dict", {"agents": ["main"]}), ("false", {"a2a": False}), ("string", {"a2a": "yes"})):
                api.cfg(api_cfg=conf)
                out[name] = (await c.get("/v1/a2a", headers=H("tok-api")), await c.get("/v1/a2a/main/agent-card.json", headers=H("tok-api")),
                             await c.post("/v1/a2a/main", headers=H("tok-api"), json=rpc("SendMessage", msg("hi"))))
            api.cfg()
            out["anon"] = await c.post("/v1/a2a/main", json=rpc("SendMessage", msg("hi")))
            out["phone"] = await c.post("/v1/a2a/main", headers=H("tok-phone"), json=rpc("SendMessage", msg("hi")))
            return out
    r = run(go())
    for name in ("plain", "dict", "false", "string"):
        for resp in r[name]:
            assert resp.status_code == 404 and "engine.api.a2a" in resp.json()["error"]["message"], name
    assert r["anon"].status_code == 401 and r["phone"].status_code == 401  # 只认 api / api-<id> 的令牌


def test_the_card_and_the_index(api):
    async def go():
        async with api.client() as c:
            return (await c.get("/v1/a2a/main/agent-card.json", headers=H("tok-api")),
                    await c.get("/v1/a2a/bookclub/agent-card.json", headers=H("tok-books", **{"X-Forwarded-Proto": "https",
                                                                                               "X-Forwarded-Host": "mousse.example.org"})),
                    await c.get("/v1/a2a/main/agent-card.json", headers=H("tok-books")),
                    await c.get("/v1/a2a/nobody/agent-card.json", headers=H("tok-api")),
                    await c.get("/v1/a2a", headers=H("tok-books")))
    main_card, books, not_mine, nobody, index = run(go())
    card = main_card.json()
    assert main_card.status_code == 200 and card["name"] == settings.app_name and card["version"] == "1.0.0" and card["skills"][0]["tags"]
    assert card["supportedInterfaces"] == [{"url": "http://test/v1/a2a/main", "protocolBinding": "JSONRPC", "protocolVersion": "1.0"}]
    assert card["capabilities"] == {"streaming": False, "pushNotifications": False} and card["defaultOutputModes"] == ["text/plain"]
    assert card["securitySchemes"]["bearer"]["httpAuthSecurityScheme"]["scheme"] == "Bearer"
    assert card["securityRequirements"] == [{"schemes": {"bearer": {"list": []}}}]
    assert books.json()["name"] == "Book club" and books.json()["supportedInterfaces"][0]["url"] == "https://mousse.example.org/v1/a2a/bookclub"
    assert not_mine.status_code == 403 and nobody.status_code == 404
    assert [d["agent"] for d in index.json()["data"]] == ["bookclub"]


def test_send_get_and_carry_on_in_the_same_context(api):
    api.fm.push(Reply(text="Hello from Mousse."), Reply(text="Still me."))

    async def go():
        async with api.client() as c:
            first = (await c.post("/v1/a2a/main", headers=H("tok-api"), json=rpc("SendMessage", msg("hi there", mid="m-1")))).json()
            t = first["result"]["task"]
            again = (await c.post("/v1/a2a/main", headers=H("tok-api"), json=rpc("SendMessage", msg("hi there", mid="m-1")))).json()
            second = (await c.post("/v1/a2a/main", headers=H("tok-api"), json=rpc("SendMessage", msg("and now?", contextId=t["contextId"])))).json()
            got = (await c.post("/v1/a2a/main", headers=H("tok-api"), json=rpc("GetTask", {"id": t["id"], "historyLength": 1}))).json()
            other = (await c.post("/v1/a2a/bookclub", headers=H("tok-books"), json=rpc("GetTask", {"id": t["id"]}))).json()
            listed = (await c.post("/v1/a2a/main", headers=H("tok-api"), json=rpc("ListTasks", {"contextId": t["contextId"]}))).json()
            sessions = (await c.get("/v1/openmousse/sessions", headers=H("tok-api"))).json()
            return first, again, second, got, other, listed, sessions
    first, again, second, got, other, listed, sessions = run(go())
    t = first["result"]["task"]
    assert first["jsonrpc"] == "2.0" and first["id"] == "r1" and t["status"]["state"] == "TASK_STATE_COMPLETED"
    assert t["status"]["message"]["role"] == "ROLE_AGENT" and t["status"]["message"]["parts"] == [{"text": "Hello from Mousse.", "mediaType": "text/plain"}]
    assert t["artifacts"][0]["parts"][0]["text"] == "Hello from Mousse." and t["artifacts"][0]["artifactId"]
    assert [m["role"] for m in t["history"]] == ["ROLE_USER", "ROLE_AGENT"] and t["history"][0]["messageId"] == "m-1"
    meta = t["metadata"]["openmousse"]
    assert meta["session"] == "a2a:" + t["contextId"] and meta["status"] == "done" and meta["pending"] == [] and meta["usage"]["total_tokens"] > 0
    assert again["result"]["task"]["id"] == t["id"] and len(api.fm.of("openai")) == 2  # 同一条消息没跑第二次
    t2 = second["result"]["task"]
    assert t2["contextId"] == t["contextId"] and t2["id"] != t["id"] and t2["artifacts"][0]["parts"][0]["text"] == "Still me."
    convo = api.fm.of("openai")[-1]["body"]["messages"]
    assert any("Hello from Mousse." in str(m.get("content")) for m in convo)  # 同一个 contextId = 同一个会话
    assert got["result"]["id"] == t["id"] and len(got["result"]["history"]) == 1
    assert other["error"]["code"] == -32001 and other["error"]["data"][0]["reason"] == "TASK_NOT_FOUND"
    assert {x["id"] for x in listed["result"]["tasks"]} == {t["id"], t2["id"]} and listed["result"]["totalSize"] == 2
    assert any(s["id"] == "a2a:" + t["contextId"] for s in sessions["data"])


def test_a_card_waiting_for_the_owner_then_decided(api):
    import inbox
    api.fm.push(Reply(text="I'll delete it.", calls=[("journal", {"args": ["delete", "j-a2a-1"]})]), Reply(text="It's waiting for your OK."))

    async def go():
        async with api.client() as c:
            a = (await c.post("/v1/a2a/main", headers=H("tok-api"), json=rpc("SendMessage", msg("delete journal entry j-a2a-1")))).json()
            t = a["result"]["task"]
            iid = t["metadata"]["openmousse"]["pending"][0]["inbox"]
            before = (await c.post("/v1/a2a/main", headers=H("tok-api"), json=rpc("GetTask", {"id": t["id"]}))).json()
            await inbox.act(iid, inbox.ActIn(action="approve", note=""))
            after = (await c.post("/v1/a2a/main", headers=H("tok-api"), json=rpc("GetTask", {"id": t["id"]}))).json()
            again = (await c.post("/v1/a2a/main", headers=H("tok-api"), json=rpc("SendMessage", msg("ok", taskId=t["id"])))).json()
            return t, iid, before, after, again
    t, iid, before, after, again = run(go())
    assert t["status"]["state"] == "TASK_STATE_AUTH_REQUIRED" and "Waiting for your OK" in t["status"]["message"]["parts"][0]["text"]
    assert before["result"]["status"]["state"] == "TASK_STATE_AUTH_REQUIRED"
    done = after["result"]
    assert done["status"]["state"] == "TASK_STATE_COMPLETED" and "approved" in done["status"]["message"]["parts"][0]["text"]
    assert api.calls == [("journal", ["delete", "j-a2a-1"], "main")]  # 点了同意才跑
    assert again["error"]["code"] == -32004  # 结束了的任务不能再往下说（带 contextId 发新的）


def test_return_immediately_then_poll_and_cancel(api):
    api.fm.push(Reply(text="Quick."), Reply(text="a slow and long answer " * 30, chunk=4, delay=0.05))

    async def go():
        async with api.client() as c:
            w = (await c.post("/v1/a2a/main", headers=H("tok-api"),
                              json=rpc("SendMessage", {**msg("hi"), "configuration": {"returnImmediately": True}}))).json()["result"]["task"]
            t0 = time.monotonic()
            while True:
                g = (await c.post("/v1/a2a/main", headers=H("tok-api"), json=rpc("GetTask", {"id": w["id"]}))).json()["result"]
                if g["status"]["state"] != "TASK_STATE_WORKING" or time.monotonic() - t0 > 10:
                    break
                await asyncio.sleep(0.05)
            slow = (await c.post("/v1/a2a/main", headers=H("tok-api"),
                                 json=rpc("SendMessage", {**msg("talk"), "configuration": {"returnImmediately": True}}))).json()["result"]["task"]
            await asyncio.sleep(0.3)
            cancel = (await c.post("/v1/a2a/main", headers=H("tok-api"), json=rpc("CancelTask", {"id": slow["id"]}))).json()
            twice = (await c.post("/v1/a2a/main", headers=H("tok-api"), json=rpc("CancelTask", {"id": slow["id"]}))).json()
            return w, g, slow, cancel, twice
    w, g, slow, cancel, twice = run(go())
    assert w["status"]["state"] == "TASK_STATE_WORKING" and g["status"]["state"] == "TASK_STATE_COMPLETED" and g["artifacts"][0]["parts"][0]["text"] == "Quick."
    assert slow["status"]["state"] == "TASK_STATE_WORKING" and cancel["result"]["status"]["state"] == "TASK_STATE_CANCELED"
    assert twice["error"]["code"] == -32002 and not a2a_api.RUNNING


def test_protocol_errors(api):
    png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 32).decode()
    api.fm.default = Reply(text="A picture.")

    async def go():
        async with api.client() as c:
            post = c.post
            out = {
                "parse": await post("/v1/a2a/main", headers=H("tok-api"), content=b"{nope"),
                "request": await post("/v1/a2a/main", headers=H("tok-api"), json={"id": 1, "method": "SendMessage"}),
                "method": await post("/v1/a2a/main", headers=H("tok-api"), json=rpc("Nope")),
                "old": await post("/v1/a2a/main", headers=H("tok-api"), json=rpc("message/send", msg("hi"))),
                "version": await post("/v1/a2a/main", headers=H("tok-api", **{"A2A-Version": "0.3"}), json=rpc("SendMessage", msg("hi"))),
                "stream": await post("/v1/a2a/main", headers=H("tok-api"), json=rpc("SendStreamingMessage", msg("hi"))),
                "data": await post("/v1/a2a/main", headers=H("tok-api"), json=rpc("SendMessage", {"message": {
                    "messageId": "m-data", "role": "ROLE_USER", "parts": [{"data": {"x": 1}}]}})),
                "modes": await post("/v1/a2a/main", headers=H("tok-api"), json=rpc("SendMessage", {**msg("hi"), "configuration": {
                    "acceptedOutputModes": ["application/json"]}})),
                "params": await post("/v1/a2a/main", headers=H("tok-api"), json=rpc("SendMessage", {"message": {"role": "ROLE_USER", "parts": []}})),
                "missing": await post("/v1/a2a/main", headers=H("tok-api"), json=rpc("GetTask", {"id": "t-none"})),
                "image": await post("/v1/a2a/main", headers=H("tok-api"), json=rpc("SendMessage", {"message": {
                    "messageId": "m-img", "role": "ROLE_USER", "parts": [{"text": "what is this?"}, {"raw": png, "mediaType": "image/png"}]},
                    "configuration": {"historyLength": 0}})),
            }
            return {k: v.json() for k, v in out.items()}
    r = run(go())
    codes = {k: (v.get("error") or {}).get("code") for k, v in r.items()}
    assert codes == {"parse": -32700, "request": -32600, "method": -32601, "old": -32009, "version": -32009, "stream": -32004, "data": -32005,
                     "modes": -32005, "params": -32602, "missing": -32001, "image": None}
    task = r["image"]["result"]["task"]
    assert task["status"]["state"] == "TASK_STATE_COMPLETED" and "history" not in task  # historyLength 0：一条记录都不回
    sent = api.fm.of("openai")[-1]["body"]["messages"][-1]["content"]
    assert any(p.get("type") == "image_url" for p in sent)  # 图照样交给模型
