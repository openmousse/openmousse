"""对外接口（engine/openai_api.py）：OpenAI 兼容的 /v1。模型是 evals/fakemodel.py 的假模型服务器（真的走 HTTP），命令行工具换成假的跑法；
请求经 main.app 打（/v1 的开关、令牌、/api 的 guard 都是真的），不连真的模型、不花钱。"""
from __future__ import annotations

import asyncio
import json
import re
import time
from types import SimpleNamespace

import httpx
import pytest

import bridges
import chat
import config
import data
from engine import context, events, loop, openai_api, provider_openai, store
from engine import tools as engine_tools
from engine.session import DbSession, day_key, user
from evals.fakemodel import Fail, FakeModelServer, Reply

TOKENS = {"api": "tok-api", "api-bookclub": "tok-books", "phone": "tok-phone", "mcp": "tok-mcp"}


def H(tok: str, **extra: str) -> dict:
    return {"Authorization": f"Bearer {tok}", **extra}


def run(coro):
    return asyncio.run(coro)


def sse(text: str) -> list[tuple[str, str]]:
    """SSE 的正文 → [(事件名, data)]；注释行跳过。"""
    out = []
    for block in text.split("\n\n"):
        name, data_ = "message", []
        for line in block.split("\n"):
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                data_.append(line[5:].strip())
        if data_:
            out.append((name, "\n".join(data_)))
    return out


def chunks(evs: list[tuple[str, str]]) -> list[dict]:
    return [json.loads(d) for e, d in evs if e == "message" and d != "[DONE]"]


def said(evs: list[tuple[str, str]], field: str = "content") -> str:
    return "".join((c["choices"][0]["delta"].get(field) or "") for c in chunks(evs) if c.get("choices"))


STAMP = re.compile(r"^\[(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) \d{4}-\d\d-\d\d \d\d:\d\d [^\]]+\] ")


def texts(msgs: list[dict], role: str) -> list[str]:
    """假模型收到的 OpenAI 格式的 messages 里，某个角色说的字。用户的话前面带的当地时间（10/19 起，engine/session.with_time）去掉。"""
    out = []
    for m in msgs:
        if m.get("role") != role:
            continue
        c = m.get("content")
        out.append(c if isinstance(c, str) else "".join(p.get("text") or "" for p in c or [] if isinstance(p, dict)))
    return [STAMP.sub("", t) for t in out] if role == "user" else out


@pytest.fixture
def api(server_json, monkeypatch, request):
    """起一个假模型；server.json：claw builtin、内核的模型指到假模型、engine.api 开着、几把令牌；Agent bookclub；命令行工具是假的。"""
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

    def cfg(api_cfg=True, claw="builtin", model="fake/m1"):
        server_json({"claw": {"kind": claw}, "auth": {"tokens": TOKENS},
                     "engine": {"model": model, "providers": {"fake": {"api": "openai", "url": fm.openai_url, "key_env": ""}},
                                **({"api": api_cfg} if api_cfg is not None else {})}})
    cfg()

    def client() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test", timeout=30)
    return SimpleNamespace(fm=fm, calls=calls, cfg=cfg, client=client)


def ask(text: str, **extra) -> dict:
    return {"model": "openmousse/main", "messages": [{"role": "user", "content": text}], **extra}


# —— 开关、令牌 ————————————————————————————————————————————————————————————————

def test_v1_is_off_unless_engine_api_is_on_and_the_claw_is_builtin(api):
    async def go():
        async with api.client() as c:
            api.cfg(api_cfg=None)
            off = await c.get("/v1/models", headers=H("tok-api"))
            api.cfg(api_cfg={"enabled": False})
            off2 = await c.get("/v1/models", headers=H("tok-api"))
            api.cfg(claw="openclaw")
            openclaw = await c.get("/v1/models", headers=H("tok-api"))
            api.cfg()
            on = await c.get("/v1/models", headers=H("tok-api"))
            return off, off2, openclaw, on
    off, off2, openclaw, on = run(go())
    for r in (off, off2, openclaw):  # 没挂：和以前一样落到 app 自己的 404
        assert r.status_code == 404 and r.json() == {"detail": "Not Found"}
    assert on.status_code == 200 and on.json()["object"] == "list"


def test_tokens_and_which_agents_they_can_use(api):
    async def go():
        async with api.client() as c:
            out = {"none": await c.get("/v1/models"),
                   "phone": await c.get("/v1/models", headers=H("tok-phone")),
                   "mcp": await c.post("/v1/chat/completions", headers=H("tok-mcp"), json=ask("hi")),
                   "api": await c.get("/v1/models", headers=H("tok-api")),
                   "books": await c.get("/v1/models", headers={"X-API-Key": "tok-books"}),
                   "books_main": await c.post("/v1/chat/completions", headers=H("tok-books"), json=ask("hi")),
                   "books_one": await c.get("/v1/models/openmousse/main", headers=H("tok-books")),
                   "api_on_api": await c.get("/api/health", headers=H("tok-api")),
                   "books_on_api": await c.get("/api/health", headers=H("tok-books")),
                   "phone_on_api": await c.get("/api/health", headers=H("tok-phone"))}
            return out
    r = run(go())
    for k in ("none", "phone", "mcp"):  # 没令牌、别的令牌：一律 401，OpenAI 的样子
        assert r[k].status_code == 401 and r[k].json()["error"]["code"] == "invalid_api_key" and r[k].headers["www-authenticate"] == "Bearer"
        assert set(r[k].json()["error"]) == {"message", "type", "param", "code"}
    ids = [m["id"] for m in r["api"].json()["data"]]
    assert {"openmousse/main", "openmousse/bookclub"} <= set(ids) and ids[0] == "openmousse/main"
    card = r["api"].json()["data"][1]
    assert card["object"] == "model" and card["owned_by"] == "openmousse" and card["name"]
    assert [m["id"] for m in r["books"].json()["data"]] == ["openmousse/bookclub"]  # api-<agent>：只看得到自己
    e = r["books_main"].json()["error"]
    assert r["books_main"].status_code == 403 and e["code"] == "model_not_allowed" and e["param"] == "model"
    assert r["books_one"].status_code == 404
    assert r["api_on_api"].status_code == 403 and r["books_on_api"].status_code == 403  # 这几把在 /api 上不通
    assert r["phone_on_api"].status_code == 200
    assert config.pick_api_token({"api": "a", "api-x": "b", "phone": "p"}) == "p"
    import pairing
    assert pairing.program_token("api") and pairing.program_token("api-bookclub") and not pairing.program_token("apiary")


def test_a_bound_token_runs_its_own_agent(api):
    api.fm.push(Reply(text="Books here."))

    async def go():
        async with api.client() as c:
            return await c.post("/v1/chat/completions", headers=H("tok-books"),
                                json={"messages": [{"role": "user", "content": "what are we reading?"}]})  # model 不写 = 它自己
    r = run(go())
    assert r.status_code == 200 and r.json()["model"] == "openmousse/bookclub" and r.json()["choices"][0]["message"]["content"] == "Books here."
    sent = api.fm.of("openai")[-1]["body"]["messages"]
    assert 'You are the Agent "Book club"' in texts(sent, "system")[0]  # 照 owner.py 备料：是这个 Agent 的开头
    t = events.turns()[0]
    assert t["agent"] == "bookclub" and t["origin"] == "api" and t["conversation"].startswith("api:bookclub:")


# —— 一问一答、会话 ——————————————————————————————————————————————————————————————

def test_non_stream_reply_and_the_session_key(api):
    api.fm.push(Reply(text="Hello there.", thinking="Short answer."), Reply(text="Second answer."), Reply(text="Fresh start."))

    async def go():
        async with api.client() as c:
            a = await c.post("/v1/chat/completions", headers=H("tok-api", **{"X-Session-Id": "s-one"}), json=ask("hi there"))
            b = await c.post("/v1/chat/completions", headers=H("tok-api", **{"X-Session-Id": "s-one"}), json=ask("and again"))
            other = await c.post("/v1/chat/completions", headers=H("tok-api", **{"X-Session-Id": "s-two"}), json=ask("new topic"))
            return a, b, other
    a, b, other = run(go())
    body = a.json()
    assert a.status_code == 200 and a.headers["x-session-id"] == "s-one" and a.headers["cache-control"] == "no-store"
    assert body["object"] == "chat.completion" and body["model"] == "openmousse/main" and body["id"].startswith("chatcmpl-")
    ch = body["choices"][0]
    assert ch["message"] == {"role": "assistant", "content": "Hello there.", "reasoning_content": "Short answer."} and ch["finish_reason"] == "stop"
    u = body["usage"]
    assert u["prompt_tokens"] > 0 and u["completion_tokens"] > 0 and u["total_tokens"] == u["prompt_tokens"] + u["completion_tokens"]
    assert body["openmousse"]["session"] == "s-one" and body["openmousse"]["status"] == "done" and body["openmousse"]["pending"] == []
    reqs = api.fm.of("openai")[-3:]
    assert texts(reqs[1]["body"]["messages"], "user") == ["hi there", "and again"]  # 同一个键：接在会话后面
    assert texts(reqs[1]["body"]["messages"], "assistant") == ["Hello there."]
    assert texts(reqs[2]["body"]["messages"], "user") == ["new topic"]  # 别的键：另一个会话
    assert b.json()["choices"][0]["message"]["content"] == "Second answer."


def test_user_field_is_a_session_key_and_no_key_means_a_new_session(api):
    api.fm.push(*(Reply(text=f"answer {i}") for i in range(4)))

    async def go():
        async with api.client() as c:
            rs = [await c.post("/v1/chat/completions", headers=H("tok-api"), json=ask(t, user="reader-7")) for t in ("one", "two")]
            rs += [await c.post("/v1/chat/completions", headers=H("tok-api"), json=ask(t)) for t in ("three", "four")]
            return rs
    rs = run(go())
    reqs = api.fm.of("openai")[-4:]
    assert rs[0].headers["x-session-id"] == rs[1].headers["x-session-id"] == "reader-7"
    assert texts(reqs[1]["body"]["messages"], "user") == ["one", "two"]
    k3, k4 = rs[2].headers["x-session-id"], rs[3].headers["x-session-id"]
    assert k3 != k4 and len(k3) == 32 and texts(reqs[3]["body"]["messages"], "user") == ["four"]  # 没有键：每次新的


def test_a_non_ascii_user_key_stays_out_of_the_header(api):
    api.fm.push(Reply(text="hi"))

    async def go():
        async with api.client() as c:
            return await c.post("/v1/chat/completions", headers=H("tok-api"), json=ask("hello", user="读者七"))
    r = run(go())
    assert r.status_code == 200 and "x-session-id" not in r.headers and r.json()["openmousse"]["session"] == "读者七"


def test_a_non_stream_caller_that_hangs_up_stops_the_turn():
    class Gone:
        async def is_disconnected(self) -> bool:
            return True
    c = loop.Control()
    run(asyncio.wait_for(openai_api.hung_up(Gone(), c), 3))
    assert c.aborted.is_set()


def test_a_new_session_takes_the_callers_earlier_messages(api):
    api.fm.push(Reply(text="Pixel."), Reply(text="Still Pixel."))
    history = [{"role": "system", "content": "Answer in one line."}, {"role": "user", "content": "My cat is called Pixel."},
               {"role": "assistant", "content": "Noted."}]

    async def go():
        async with api.client() as c:
            hd = H("tok-api", **{"X-Session-Id": "s-hist"})
            a = await c.post("/v1/chat/completions", headers=hd,
                             json={"model": "openmousse/main", "messages": [*history, {"role": "user", "content": "What's my cat called?"}]})
            b = await c.post("/v1/chat/completions", headers=hd,  # 无状态的客户端每次都带全部：会话已经有了，不再放一遍
                             json={"model": "openmousse/main", "messages": [*history, {"role": "user", "content": "What's my cat called?"},
                                                                            {"role": "assistant", "content": "Pixel."},
                                                                            {"role": "user", "content": "Sure?"}]})
            return a, b
    a, b = run(go())
    assert a.status_code == b.status_code == 200
    first, second = (r["body"]["messages"] for r in api.fm.of("openai")[-2:])
    users = texts(first, "user")
    assert users[-1] == "What's my cat called?" and any("My cat is called Pixel." in t for t in users)
    assert texts(first, "assistant") == ["Noted."]
    assert "Answer in one line." in json.dumps(first) and "instructions" in json.dumps(first)
    assert sum("My cat is called Pixel." in t for t in texts(second, "user")) == 1 and texts(second, "user")[-1] == "Sure?"


def test_stream_chunks_reasoning_finish_usage_and_done(api):
    api.fm.push(Reply(text="Streaming works fine.", thinking="Think first.", chunk=5))

    async def go():
        async with api.client() as c:
            return await c.post("/v1/chat/completions", headers=H("tok-api", **{"X-Session-Id": "s-stream"}),
                                json=ask("stream please", stream=True, stream_options={"include_usage": True}))
    r = run(go())
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream") and r.headers["x-session-id"] == "s-stream"
    evs = sse(r.text)
    assert evs[-1] == ("message", "[DONE]") and {e for e, _ in evs} == {"message"}  # 没说要事件：只有标准的块
    cs = chunks(evs)
    assert cs[0]["choices"][0]["delta"] == {"role": "assistant", "content": ""} and all(c["object"] == "chat.completion.chunk" for c in cs)
    assert len({c["id"] for c in cs}) == 1 and all(c["model"] == "openmousse/main" for c in cs)
    assert said(evs) == "Streaming works fine." and said(evs, "reasoning_content") == "Think first."
    assert [c["choices"][0]["finish_reason"] for c in cs if c["choices"] and c["choices"][0]["finish_reason"]] == ["stop"]
    assert cs[-1]["choices"] == [] and cs[-1]["usage"]["total_tokens"] > 0
    assert len([c for c in cs if c["choices"] and c["choices"][0]["delta"].get("content")]) > 1  # 真的一段段来的


# —— 撤不回的：进收件箱、回的话里写明在等点头 ——————————————————————————————————————

def pending_cards(argv_part: str) -> list:
    with store.read() as conn:
        return conn.execute("SELECT * FROM engine_calls WHERE args LIKE ? ORDER BY created_at", (f"%{argv_part}%",)).fetchall()


def main_messages() -> int:
    with chat._lock, chat.db() as conn:
        return conn.execute("SELECT COUNT(*) FROM messages WHERE thread='main'").fetchone()[0]


def test_irreversible_call_waits_for_the_users_ok(api):
    import inbox
    api.fm.push(Reply(text="I'll delete it.", calls=[("journal", {"args": ["delete", "j-api-1"]})]), Reply(text="It's waiting for your OK."),
                Reply(text="Done, it's gone."))

    async def go():
        async with api.client() as c:
            hd = H("tok-api", **{"X-Session-Id": "s-del"})
            a = await c.post("/v1/chat/completions", headers=hd, json=ask("delete journal entry j-api-1"))
            iid = a.json()["openmousse"]["pending"][0]["inbox"]
            before = main_messages()
            ok = await c.post(f"/v1/openmousse/approvals/{iid}", headers=H("tok-api"), json={"action": "approve"})
            again = await c.post(f"/v1/openmousse/approvals/{iid}", headers=H("tok-api"), json={"action": "approve"})
            after = main_messages()
            b = await c.post("/v1/chat/completions", headers=hd, json=ask("thanks"))
            return a, iid, ok, again, before, after, b
    a, iid, ok, again, before, after, b = run(go())
    body = a.json()
    content = body["choices"][0]["message"]["content"]
    assert content.startswith("I'll delete it.\n\nIt's waiting for your OK.\n\n(Waiting for your OK: ") and iid in content
    pend = body["openmousse"]["pending"]
    assert pend == [{"inbox": iid, "tool": "journal", "title": pend[0]["title"], "approvable": True}] and "j-api-1" in pend[0]["title"]
    first_two = api.fm.of("openai")[-3:-1]
    tool_msg = [m for m in first_two[1]["body"]["messages"] if m["role"] == "tool"][-1]
    assert "approval-pending" in tool_msg["content"]  # 模型拿到的是「交给用户了」
    it = inbox.item(iid)
    assert it["kind"] == "tool" and it["thread"] == "main" and it["status"] == "done"
    assert ok.status_code == 200 and ok.json() == {"object": "openmousse.approval", "inbox": iid, "status": "done", "result": it["result"]}
    assert again.status_code == 409 and again.json()["error"]["code"] == "approval_not_pending"
    assert api.calls == [("journal", ["delete", "j-api-1"], "main")]  # 点了同意才跑，只跑一次
    assert after == before  # 结果没发进 app 的对话
    last_user = texts(api.fm.of("openai")[-1]["body"]["messages"], "user")[-1]
    assert last_user.startswith("【收件箱】Approved ") and "ok: journal delete j-api-1" in last_user and last_user.endswith("thanks")
    assert b.json()["choices"][0]["message"]["content"] == "Done, it's gone."


def test_stream_can_wait_for_the_ok_and_carry_on(api):
    api.fm.push(Reply(text="Deleting.", calls=[("journal", {"args": ["delete", "j-api-2"]})]), Reply(text="Deleted it."),
                Reply(text="Trying.", calls=[("journal", {"args": ["delete", "j-api-3"]})]), Reply(text="You said no, so I left it."))
    hd = H("tok-api", **{"X-Session-Id": "s-wait", "X-OpenMousse-Events": "1", "X-OpenMousse-Approvals": "wait"})

    async def decide(part: str, action: str) -> None:
        for _ in range(400):
            rows = [r for r in pending_cards(part) if r["status"] == "pending" and r["inbox"]]
            if rows and openai_api.WAITING.get(rows[0]["inbox"]):
                async with api.client() as c:
                    r = await c.post(f"/v1/openmousse/approvals/{rows[0]['inbox']}", headers=H("tok-api"), json={"action": action})
                    assert r.status_code == 200, r.text
                return
            await asyncio.sleep(0.02)
        raise AssertionError("no card")

    async def go(text: str, part: str, action: str):
        async with api.client() as c:
            r, _ = await asyncio.gather(c.post("/v1/chat/completions", headers=hd, json=ask(text, stream=True)), decide(part, action))
            return r
    yes = run(go("delete j-api-2", "j-api-2", "approve"))
    evs = sse(yes.text)
    names = [e for e, _ in evs]
    assert names.index("openmousse.tool") < names.index("openmousse.approval") < names.index("openmousse.approval_result")
    card = json.loads(dict(evs)["openmousse.approval"])
    assert card["waiting"] and card["approvable"] and card["key"] == "journal delete" and card["tool"] == "journal" and card["id"]
    res = json.loads(dict(evs)["openmousse.approval_result"])
    assert res["outcome"] == "approved" and res["output"] == "ok: journal delete j-api-2" and res["inbox"] == card["inbox"]
    tool_events = [json.loads(d) for e, d in evs if e == "openmousse.tool"]
    assert [(t["phase"], t.get("status")) for t in tool_events] == [("start", None), ("end", "ok")] and tool_events[0]["title"] == "journal delete"
    assert said(evs) == "Deleting.\n\nDeleted it."  # 当场拿到结果接着说，没有「在等点头」
    assert json.loads(dict(evs)["openmousse.done"])["pending"] == []
    tool_msg = [m for m in api.fm.of("openai")[-1]["body"]["messages"] if m["role"] == "tool"][-1]
    assert '"status": "approved"' in tool_msg["content"] and "ok: journal delete j-api-2" in tool_msg["content"]
    assert api.calls == [("journal", ["delete", "j-api-2"], "main")]
    no = run(go("delete j-api-3", "j-api-3", "reject"))
    evs = sse(no.text)
    assert json.loads(dict(evs)["openmousse.approval_result"])["outcome"] == "rejected"
    assert [json.loads(d)["status"] for e, d in evs if e == "openmousse.tool" and json.loads(d)["phase"] == "end"] == ["denied"]
    tool_msg = [m for m in api.fm.of("openai")[-1]["body"]["messages"] if m["role"] == "tool"][-1]
    assert "declined" in tool_msg["content"] and len(api.calls) == 1  # 拒了：没跑
    assert said(evs) == "Trying.\n\nYou said no, so I left it."


def test_approvals_only_for_the_tokens_own_cards(api):
    api.fm.push(Reply(calls=[("journal", {"args": ["delete", "j-api-4"]})]), Reply(text="Waiting."))

    async def go():
        async with api.client() as c:
            a = await c.post("/v1/chat/completions", headers=H("tok-api", **{"X-Session-Id": "s-own"}), json=ask("delete j-api-4"))
            iid = a.json()["openmousse"]["pending"][0]["inbox"]
            other = await c.post(f"/v1/openmousse/approvals/{iid}", headers=H("tok-books"), json={"action": "approve"})
            bad = await c.post(f"/v1/openmousse/approvals/{iid}", headers=H("tok-api"), json={"action": "revise"})
            api.cfg(api_cfg={"approve": False})
            off = await c.post(f"/v1/openmousse/approvals/{iid}", headers=H("tok-api"), json={"action": "approve"})
            return a, iid, other, bad, off
    a, iid, other, bad, off = run(go())
    import inbox
    assert a.json()["choices"][0]["message"]["content"].startswith("Waiting.\n\n(Waiting for your OK:")
    assert other.status_code == 404 and other.json()["error"]["code"] == "approval_not_found"  # 别的令牌的卡：看不到
    assert bad.status_code == 400 and bad.json()["error"]["param"] == "action"
    assert off.status_code == 403 and off.json()["error"]["code"] == "approvals_off"
    assert inbox.item(iid)["status"] == "pending" and api.calls == []
    # app 的对话里交的卡：API 点不了
    tid = events.turn_open(conversation="main", kind="owner", model="fake/m1", agent="main", origin="user")
    tool = next(t for t in engine_tools.for_agent("main") if t.name == "journal")
    ctx = loop.ToolCtx(turn=tid, kind="owner", agent="main", conversation="main", call_id="c-app", control=loop.Control())
    app_card = run(engine_tools.gate(tool, {"args": ["delete", "j-app-1"]}, ctx))
    api.cfg()

    async def try_app():
        async with api.client() as c:
            return await c.post(f"/v1/openmousse/approvals/{app_card['inbox']}", headers=H("tok-api"), json={"action": "approve"})
    assert run(try_app()).status_code == 404 and inbox.item(app_card["inbox"])["status"] == "pending"


def test_decisions_made_in_the_app_go_back_to_the_api_session(api):
    import inbox
    api.fm.push(Reply(calls=[("journal", {"args": ["delete", "j-api-5"]})]), Reply(text="Asked."), Reply(text="Okay, I won't."))

    async def go():
        async with api.client() as c:
            hd = H("tok-api", **{"X-Session-Id": "s-app"})
            a = await c.post("/v1/chat/completions", headers=hd, json=ask("delete j-api-5"))
            iid = a.json()["openmousse"]["pending"][0]["inbox"]
            before = main_messages()
            await inbox.act(iid, inbox.ActIn(action="reject", note="keep it"))  # 在 app 里点的
            after = main_messages()
            b = await c.post("/v1/chat/completions", headers=hd, json=ask("ok?"))
            return iid, before, after, b
    iid, before, after, b = run(go())
    last_user = texts(api.fm.of("openai")[-1]["body"]["messages"], "user")[-1]
    assert after == before and api.calls == []
    assert last_user.startswith("【收件箱】") and "declined it (keep it)" in last_user and iid in last_user and last_user.endswith("ok?")


# —— 出错：OpenAI 的样子 ——————————————————————————————————————————————————————————

def test_errors_are_openai_shaped(api):
    async def go():
        async with api.client() as c:
            post = c.post
            out = {
                "json": await post("/v1/chat/completions", headers={**H("tok-api"), "Content-Type": "application/json"}, content=b"{nope"),
                "no_messages": await post("/v1/chat/completions", headers=H("tok-api"), json={"model": "openmousse/main"}),
                "no_user": await post("/v1/chat/completions", headers=H("tok-api"),
                                      json={"model": "openmousse", "messages": [{"role": "system", "content": "x"}]}),
                "part": await post("/v1/chat/completions", headers=H("tok-api"),
                                   json={"messages": [{"role": "user", "content": [{"type": "input_audio", "input_audio": {}}]}]}),
                "model": await post("/v1/chat/completions", headers=H("tok-api"), json=ask("hi", model="gpt-4o")),
                "nobody": await post("/v1/chat/completions", headers=H("tok-api"), json=ask("hi", model="openmousse/nobody")),
                "route": await c.get("/v1/nothing-here", headers=H("tok-api")),
                "key": await post("/v1/chat/completions", headers=H("tok-api", **{"X-Session-Id": "x" * 300}), json=ask("hi")),
            }
            api.fm.default = Fail(500, "boom")
            out["upstream"] = await post("/v1/chat/completions", headers=H("tok-api"), json=ask("hi"))
            out["upstream_stream"] = await post("/v1/chat/completions", headers=H("tok-api"), json=ask("hi", stream=True))
            api.fm.default = Reply(text="ok")
            api.cfg(model="")
            out["config"] = await post("/v1/chat/completions", headers=H("tok-api"), json=ask("hi"))
            return out
    r = run(go())
    want = {"json": (400, "invalid_json"), "no_messages": (400, "missing_messages"), "no_user": (400, "missing_user_message"),
            "part": (400, "unsupported_content_type"), "model": (404, "model_not_found"), "nobody": (404, "model_not_found"),
            "route": (404, "not_found"), "key": (400, "invalid_session_id"), "upstream": (502, "upstream_error"), "config": (503, "engine_config")}
    for k, (status, code) in want.items():
        e = r[k].json()["error"]
        assert r[k].status_code == status and e["code"] == code and set(e) == {"message", "type", "param", "code"} and e["message"], k
    assert r["part"].json()["error"]["param"] == "messages[0].content[0]" and r["upstream"].json()["error"]["type"] == "server_error"
    assert "boom" in r["upstream"].json()["error"]["message"]
    assert "engine.model" in r["config"].json()["error"]["message"] and "subscription" not in r["config"].json()["error"]["message"]
    evs = sse(r["upstream_stream"].text)  # 流已经开始：一条 error，再 [DONE]
    assert r["upstream_stream"].status_code == 200 and evs[-1] == ("message", "[DONE]")
    assert json.loads(evs[-2][1])["error"]["code"] == "upstream_error" and not [c for c in chunks(evs) if c.get("choices") and
                                                                                 c["choices"][0]["finish_reason"]]


# —— 会话列表、前一天的最后几句、关服务 ————————————————————————————————————————————————————

def test_sessions_are_listed_per_token(api):
    api.fm.push(Reply(text="a"), Reply(text="b"))

    async def go():
        async with api.client() as c:
            await c.post("/v1/chat/completions", headers=H("tok-api", **{"X-Session-Id": "s-list", "X-OpenMousse-Cwd": "/home/demo/%E4%B9%A6"}),
                         json=ask("plan the reading list"))
            await c.post("/v1/chat/completions", headers=H("tok-books", **{"X-Session-Id": "s-list"}),
                         json={"messages": [{"role": "user", "content": "chapter three"}]})
            mine = await c.get("/v1/openmousse/sessions", headers=H("tok-api"), params={"cwd": "/home/demo/书"})
            books = await c.get("/v1/openmousse/sessions", headers=H("tok-books"))
            one = await c.get("/v1/openmousse/sessions", headers=H("tok-api"), params={"id": "s-list"})
            bad = await c.get("/v1/openmousse/sessions", headers=H("tok-api"), params={"limit": "lots"})
            return mine.json(), books.json(), one.json(), bad
    mine, books, one, bad = run(go())
    assert [(s["id"], s["agent"], s["title"], s["cwd"]) for s in mine["data"]] == [("s-list", "main", "plan the reading list", "/home/demo/书")]
    assert [s["agent"] for s in books["data"]] and {s["agent"] for s in books["data"]} == {"bookclub"}
    assert [s["model"] for s in one["data"]] == ["openmousse/main"]  # 同一个键、不同令牌：两个会话，各看各的
    assert bad.status_code == 400 and bad.json()["error"]["param"] == "limit"


def test_the_next_day_starts_with_the_last_lines_of_the_api_session(api):
    conv = openai_api.conversation("api", "main", "s-tail")
    s = DbSession.create(day_key(conv, "2026-01-09"), kind="owner", agent="main", model="fake/m1", system="sys")
    s.append(user("Remind me what we picked."))
    s.append({"role": "assistant", "content": [{"type": "text", "text": "The blue cover."}], "api": None, "provider": None, "model": None,
              "stop": "end", "usage": {}})
    p = context.tail_part(conv, "2026-01-10")
    assert p.title == "Where the last conversation ended (2026-01-09)" and p.text == "User: Remind me what we picked.\nYou: The blue cover."
    assert not p.live and context.tail_part(conv, "2026-02-10").text == ""  # 太久以前的不拿


def test_shutdown_stops_api_turns(api, monkeypatch):
    monkeypatch.setattr(chat, "SHUTTING_DOWN", False)
    api.fm.push(Reply(text="a slow and long answer " * 6, chunk=4, delay=0.05))

    async def go():
        async with api.client() as c:
            task = asyncio.create_task(c.post("/v1/chat/completions", headers=H("tok-api", **{"X-Session-Id": "s-down"}), json=ask("talk")))
            t0 = time.monotonic()
            # 等第一段字到了再停（同 test_engine_acp）：只等登记、再固定等 0.2 秒，CI 的机器慢时一个字都还没出，就没有半截回复可存
            while not any(r.text for r in openai_api.RUNS.values()) and time.monotonic() - t0 < 10:
                await asyncio.sleep(0.01)
            assert chat.stop_engine_runs() == 1
            return await task
    r = run(go())
    assert r.status_code == 503 and r.json()["error"]["code"] == "aborted" and not openai_api.RUNS
    conv = openai_api.conversation("api", "main", "s-down")
    msgs = DbSession.latest(day_key(conv, chat.day_of(chat.now_iso()))).messages()
    assert msgs[-2]["stop"] == "aborted" and msgs[-1]["role"] == "system" and "restarted" in msgs[-1]["content"][0]["text"]
