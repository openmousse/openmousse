"""第 7 批 12.2：/v1/responses（engine/openai_api.py 里加的那一节）。模型是 evals/fakemodel.py 的假模型服务器（真的走 HTTP），请求经 main.app
打（/v1 的开关、令牌都是真的）；不连真的模型、不花钱。"""
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
import data
from engine import loop, openai_api, provider_openai, store
from evals.fakemodel import Fail, FakeModelServer, Refusal, Reply

TOKENS = {"api": "tok-api", "api-bookclub": "tok-books", "phone": "tok-phone"}
STAMP = re.compile(r"^\[(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) \d{4}-\d\d-\d\d \d\d:\d\d [^\]]+\] ")
ORDER = ["response.created", "response.in_progress", "response.output_item.added", "response.content_part.added", "response.output_text.delta",
         "response.output_text.done", "response.content_part.done", "response.output_item.done"]


def H(tok: str, **extra: str) -> dict:
    return {"Authorization": f"Bearer {tok}", **extra}


def run(coro):
    return asyncio.run(coro)


def sse(text: str) -> list[tuple[str, dict]]:
    """SSE 的正文 → [(事件名, data 解析出来的)]；注释行跳过。"""
    out = []
    for block in text.split("\n\n"):
        name, lines = "message", []
        for line in block.split("\n"):
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                lines.append(line[5:].strip())
        if lines:
            out.append((name, json.loads("\n".join(lines))))
    return out


def texts(msgs: list[dict], role: str) -> list[str]:
    out = []
    for m in msgs:
        if m.get("role") != role:
            continue
        c = m.get("content")
        out.append(c if isinstance(c, str) else "".join(p.get("text") or "" for p in c or [] if isinstance(p, dict)))
    return [STAMP.sub("", t) for t in out] if role == "user" else out


@pytest.fixture
def api(server_json, monkeypatch, request):
    """假模型；server.json：claw builtin、模型指到假模型、engine.api 开着而且 responses = true；几把令牌；Agent bookclub。"""
    import main
    fm = FakeModelServer([]).start()
    request.addfinalizer(fm.stop)
    monkeypatch.setattr(loop, "MODEL_RETRIES", (0, 0))
    monkeypatch.setattr(provider_openai, "RETRY_DELAYS", (0, 0))

    async def fake_run(name, args, stdin, agent, *, kill_on_cancel=False):
        return f"ok: {name} {' '.join(args)}"
    monkeypatch.setattr(bridges, "run", fake_run)
    with chat._lock, data.ddb() as conn:
        conn.execute("INSERT OR REPLACE INTO groups(id, name, icon, purpose, created_at) VALUES('bookclub', 'Book club', 'book', "
                     "'Reading notes.', ?)", (chat.now_iso(),))

    def cfg(api_cfg=None, model="fake/m1"):
        server_json({"claw": {"kind": "builtin"}, "auth": {"tokens": TOKENS},
                     "engine": {"model": model, "providers": {"fake": {"api": "openai", "url": fm.openai_url, "key_env": ""}},
                                "api": {"responses": True} if api_cfg is None else api_cfg}})
    cfg()

    def client() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test", timeout=30)
    return SimpleNamespace(fm=fm, cfg=cfg, client=client)


def ask(inp, **extra) -> dict:
    return {"model": "openmousse/main", "input": inp, **extra}


def said(obj: dict) -> str:
    return "".join(p["text"] for item in obj["output"] for p in item["content"])


def test_off_unless_engine_api_responses_is_true(api):
    async def go():
        async with api.client() as c:
            out = {}
            for name, conf in (("plain", True), ("dict", {"agents": ["main"]}), ("false", {"responses": False}),
                               ("string", {"responses": "yes"})):
                api.cfg(api_cfg=conf)
                out[name] = (await c.post("/v1/responses", headers=H("tok-api"), json=ask("hi")),
                             await c.get("/v1/responses/resp_x", headers=H("tok-api")),
                             await c.delete("/v1/responses/resp_x", headers=H("tok-api")))
            out["anon"] = await c.post("/v1/responses", json=ask("hi"))  # 没令牌：先 401（关着的也不告诉它）
            out["chat"] = await c.post("/v1/chat/completions", headers=H("tok-api"),  # /v1 其余照旧
                                       json={"model": "openmousse/main", "messages": [{"role": "user", "content": "hi"}]})
            return out
    api.fm.default = Reply(text="still here")
    r = run(go())
    for name in ("plain", "dict", "false", "string"):
        for resp in r[name]:
            assert resp.status_code == 404 and resp.json()["error"]["code"] == "not_found", name
            assert "engine.api.responses" in resp.json()["error"]["message"]
    assert r["anon"].status_code == 401 and r["chat"].status_code == 200


def test_a_reply_can_be_fetched_and_deleted_only_by_its_own_token(api):
    api.fm.push(Reply(text="Hello there.", thinking="Short answer."), Reply(text="Books here."))

    async def go():
        async with api.client() as c:
            a = await c.post("/v1/responses", headers=H("tok-api", **{"X-Session-Id": "r-one"}), json=ask("hi there"))
            rid = a.json()["id"]
            books = await c.post("/v1/responses", headers=H("tok-books"), json={"input": "what are we reading?"})  # model 不写 = 它自己
            got = await c.get(f"/v1/responses/{rid}", headers=H("tok-api"))
            other = await c.get(f"/v1/responses/{rid}", headers=H("tok-books"))
            other_del = await c.delete(f"/v1/responses/{rid}", headers=H("tok-books"))
            openai_api._resp_ready.clear()  # 「重启」：表在库里，照样拿得回  # noqa: SLF001
            again = await c.get(f"/v1/responses/{rid}", headers=H("tok-api"))
            gone = await c.delete(f"/v1/responses/{rid}", headers=H("tok-api"))
            after = (await c.get(f"/v1/responses/{rid}", headers=H("tok-api")), await c.delete(f"/v1/responses/{rid}", headers=H("tok-api")))
            return a, books, got, other, other_del, again, gone, after
    a, books, got, other, other_del, again, gone, after = run(go())
    body = a.json()
    assert a.status_code == 200 and a.headers["x-session-id"] == "r-one" and a.headers["cache-control"] == "no-store"
    assert body["object"] == "response" and body["id"].startswith("resp_") and body["status"] == "completed"
    assert body["model"] == "openmousse/main" and body["previous_response_id"] is None and body["store"] is True
    assert body["error"] is None and body["incomplete_details"] is None and isinstance(body["created_at"], int)
    [msg] = body["output"]  # 只有一条 assistant 的话；思考摘要不放进来
    assert msg["type"] == "message" and msg["role"] == "assistant" and msg["status"] == "completed" and msg["id"].startswith("msg_")
    assert msg["content"] == [{"type": "output_text", "text": "Hello there.", "annotations": []}]
    u = body["usage"]
    assert u["input_tokens"] > 0 and u["output_tokens"] > 0 and u["total_tokens"] == u["input_tokens"] + u["output_tokens"]
    assert "cached_tokens" in u["input_tokens_details"]
    assert body["openmousse"]["session"] == "r-one" and body["openmousse"]["status"] == "done" and body["openmousse"]["pending"] == []
    assert books.status_code == 200 and books.json()["model"] == "openmousse/bookclub" and said(books.json()) == "Books here."
    assert got.status_code == 200 and got.json() == body and again.json() == body
    for r in (other, other_del):  # 别的令牌：看不到、删不了
        assert r.status_code == 404 and r.json()["error"]["code"] == "response_not_found"
    assert gone.status_code == 200 and gone.json() == {"id": body["id"], "object": "response", "deleted": True}
    assert [r.status_code for r in after] == [404, 404]


def test_previous_response_id_carries_on_the_same_session(api):
    api.fm.push(Reply(text="Noted."), Reply(text="Pixel."), Reply(text="Fresh."))

    async def go():
        async with api.client() as c:
            a = await c.post("/v1/responses", headers=H("tok-api"), json=ask("My cat is called Pixel."))
            rid = a.json()["id"]
            b = await c.post("/v1/responses", headers=H("tok-api", **{"X-Session-Id": "ignored"}),
                             json={"input": "What's my cat called?", "previous_response_id": rid})
            other = await c.post("/v1/responses", headers=H("tok-books"), json={"input": "hi", "previous_response_id": rid})
            wrong = await c.post("/v1/responses", headers=H("tok-api"), json=ask("hi", model="openmousse/bookclub", previous_response_id=rid))
            missing = await c.post("/v1/responses", headers=H("tok-api"), json=ask("hi", previous_response_id="resp_nope"))
            fresh = await c.post("/v1/responses", headers=H("tok-api"), json=ask("Do you know my cat?"))
            return a, b, other, wrong, missing, fresh
    a, b, other, wrong, missing, fresh = run(go())
    assert a.status_code == b.status_code == fresh.status_code == 200
    assert b.json()["previous_response_id"] == a.json()["id"] and said(b.json()) == "Pixel."
    assert b.json()["openmousse"]["session"] == a.json()["openmousse"]["session"] == a.headers["x-session-id"] == b.headers["x-session-id"]
    first, second, third = (r["body"]["messages"] for r in api.fm.of("openai")[-3:])
    assert texts(second, "user") == ["My cat is called Pixel.", "What's my cat called?"] and texts(second, "assistant") == ["Noted."]
    assert texts(third, "user") == ["Do you know my cat?"]  # 不带：新会话
    assert other.status_code == 404 and other.json()["error"]["param"] == "previous_response_id"  # 别的令牌的：当没有
    assert wrong.status_code == 400 and wrong.json()["error"]["param"] == "model"
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "response_not_found"


def test_input_items_and_instructions(api):
    api.fm.push(Reply(text="Pixel, en bref."), Reply(text="Yes."))
    items = [{"role": "developer", "content": "Answer in one line."},
             {"role": "user", "content": [{"type": "input_text", "text": "My cat is called Pixel."}]},
             {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Noted.", "annotations": []}]},
             {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "What's my cat called?"}]}]

    async def go():
        async with api.client() as c:
            a = await c.post("/v1/responses", headers=H("tok-api"), json=ask(items, instructions="Reply in French.", temperature=0.2,
                                                                              tools=[{"type": "function", "name": "x"}]))
            b = await c.post("/v1/responses", headers=H("tok-api"), json={"input": [{"type": "input_text", "text": "Sure?"}],
                                                                          "previous_response_id": a.json()["id"]})
            return a, b
    a, b = run(go())
    assert a.status_code == b.status_code == 200 and a.json()["instructions"] == "Reply in French." and b.json()["instructions"] is None
    first, second = (r["body"]["messages"] for r in api.fm.of("openai")[-2:])
    users = texts(first, "user")
    assert any("My cat is called Pixel." in t for t in users[:-1]) and texts(first, "assistant") == ["Noted."]  # 前情放进了新会话
    assert "Answer in one line." in json.dumps(first)
    last = users[-1]
    assert last.startswith(openai_api.RESP_NOTE) and "Reply in French.\n\n---\n\nWhat's my cat called?" in last  # 后面可能还跟着内核配的说明书
    assert texts(second, "user")[-1].startswith("Sure?")  # 说明只管那一轮，不带到下一轮
    sent_tools = [(t.get("function") or {}).get("name") for t in api.fm.of("openai")[-2]["body"].get("tools") or []]
    assert "x" not in sent_tools and "journal" in sent_tools  # 调用方的 tools 不管：Agent 用自己的


def test_content_parts_map_onto_the_chat_shape():
    parts = [{"type": "input_text", "text": "look"}, {"type": "input_image", "image_url": "https://example.test/cat.png"},
             {"type": "input_image", "image_url": {"url": "data:image/png;base64,AAAA"}}, {"type": "output_text", "text": "said"}]
    assert openai_api.resp_parts(parts, "x") == [{"type": "text", "text": "look"},
                                                 {"type": "image_url", "image_url": {"url": "https://example.test/cat.png"}},
                                                 {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
                                                 {"type": "text", "text": "said"}]
    assert openai_api.resp_parts(None, "x") == "" and openai_api.resp_parts("hi", "x") == "hi"
    for bad, code in (([{"type": "input_image", "file_id": "file-1"}], "invalid_content"), ([{"type": "input_file", "file_id": "f"}],
                                                                                           "unsupported_content_type"), (5, "invalid_content")):
        with pytest.raises(openai_api.ApiError) as e:
            openai_api.resp_parts(bad, "x")
        assert e.value.status == 400 and e.value.code == code


def test_bad_requests_are_openai_shaped(api):
    async def go():
        async with api.client() as c:
            post = c.post
            hd = H("tok-api")
            return {
                "missing": await post("/v1/responses", headers=hd, json={"model": "openmousse/main"}),
                "empty": await post("/v1/responses", headers=hd, json=ask("  ")),
                "call": await post("/v1/responses", headers=hd, json=ask([{"type": "function_call_output", "call_id": "c1", "output": "x"}])),
                "file": await post("/v1/responses", headers=hd, json=ask([{"role": "user", "content": [{"type": "input_file", "file_id": "f"}]}])),
                "role": await post("/v1/responses", headers=hd, json=ask([{"role": "tool", "content": "x"}])),
                "no_user": await post("/v1/responses", headers=hd, json=ask([{"role": "assistant", "content": "hello"}])),
                "prev": await post("/v1/responses", headers=hd, json=ask("hi", previous_response_id=5)),
                "instr": await post("/v1/responses", headers=hd, json=ask("hi", instructions=["x"])),
                "model": await post("/v1/responses", headers=hd, json=ask("hi", model="gpt-5")),
                "json": await post("/v1/responses", headers={**hd, "Content-Type": "application/json"}, content=b"{nope"),
            }
    r = run(go())
    want = {"missing": (400, "invalid_input"), "empty": (400, "missing_input"), "call": (400, "unsupported_input_type"),
            "file": (400, "unsupported_content_type"), "role": (400, None), "no_user": (400, "missing_user_message"), "prev": (400, None),
            "instr": (400, None), "model": (404, "model_not_found"), "json": (400, "invalid_json")}
    for k, (status, code) in want.items():
        e = r[k].json()["error"]
        assert r[k].status_code == status and e["code"] == code and set(e) == {"message", "type", "param", "code"} and e["message"], k
    assert r["call"].json()["error"]["param"] == "input[0]" and r["role"].json()["error"]["param"] == "input[0].role"
    assert r["file"].json()["error"]["param"] == "input[0].content[0]" and r["prev"].json()["error"]["param"] == "previous_response_id"
    assert not api.fm.of("openai")  # 一个都没跑到模型


def test_stream_events_in_order(api):
    api.fm.push(Reply(text="Streaming works fine.", thinking="Think first.", chunk=5), Reply(text="Again."))

    async def go():
        async with api.client() as c:
            a = await c.post("/v1/responses", headers=H("tok-api", **{"X-Session-Id": "r-stream"}), json=ask("stream please", stream=True))
            b = await c.post("/v1/responses", headers=H("tok-api", **{"X-OpenMousse-Events": "1"}), json=ask("with events", stream=True))
            evs = sse(a.text)
            got = await c.get(f"/v1/responses/{evs[0][1]['response']['id']}", headers=H("tok-api"))
            return a, b, got
    a, b, got = run(go())
    assert a.status_code == 200 and a.headers["content-type"].startswith("text/event-stream") and a.headers["x-session-id"] == "r-stream"
    evs = sse(a.text)
    names = [n for n, _ in evs]
    assert names[-1] == "response.completed" and "[DONE]" not in a.text
    assert [n for i, n in enumerate(names[:-1]) if i == 0 or n != names[i - 1]] == ORDER  # 每种依次来（增量连着好几条）
    assert all(d["type"] == n for n, d in evs) and [d["sequence_number"] for _, d in evs] == list(range(len(evs)))
    created = evs[0][1]["response"]
    assert created["status"] == "in_progress" and created["output"] == [] and created["model"] == "openmousse/main"
    added = dict(evs)["response.output_item.added"]["item"]
    assert added["status"] == "in_progress" and added["content"] == [] and added["role"] == "assistant"
    deltas = [d["delta"] for n, d in evs if n == "response.output_text.delta"]
    assert len(deltas) > 1 and "".join(deltas) == "Streaming works fine."
    assert len({d["item_id"] for n, d in evs if "item_id" in d}) == 1 and dict(evs)["response.output_text.done"]["text"] == "Streaming works fine."
    done = evs[-1][1]["response"]
    assert done["id"] == created["id"] and done["status"] == "completed" and said(done) == "Streaming works fine." and done["usage"]["total_tokens"] > 0
    assert done["openmousse"]["session"] == "r-stream" and got.status_code == 200 and got.json() == done
    evs_b = sse(b.text)  # 说了要事件：最后多一条 openmousse.done
    assert evs_b[-1][0] == "openmousse.done" and evs_b[-2][0] == "response.completed" and evs_b[-1][1]["status"] == "done"


def test_incomplete_and_failed_replies(api):
    api.fm.push(Reply(text="This gets cut", stop="length"), Refusal("I can't help with that."))

    async def go():
        async with api.client() as c:
            cut = await c.post("/v1/responses", headers=H("tok-api"), json=ask("write a lot"))
            no = await c.post("/v1/responses", headers=H("tok-api"), json=ask("something odd"))
            api.fm.default = Fail(500, "boom")
            failed = await c.post("/v1/responses", headers=H("tok-api"), json=ask("hi"))
            failed_stream = await c.post("/v1/responses", headers=H("tok-api"), json=ask("hi", stream=True))
            rid = sse(failed_stream.text)[0][1]["response"]["id"]
            stored = await c.get(f"/v1/responses/{rid}", headers=H("tok-api"))
            return cut, no, failed, failed_stream, stored
    cut, no, failed, failed_stream, stored = run(go())
    assert cut.json()["status"] == "incomplete" and cut.json()["incomplete_details"] == {"reason": "max_output_tokens"}
    assert cut.json()["output"][0]["status"] == "incomplete" and said(cut.json()) == "This gets cut"
    assert no.json()["status"] == "incomplete" and no.json()["incomplete_details"] == {"reason": "content_filter"}
    e = failed.json()["error"]  # 一次性的：HTTP 的错，同 chat/completions
    assert failed.status_code == 502 and e["code"] == "upstream_error" and "boom" in e["message"]
    evs = sse(failed_stream.text)
    assert failed_stream.status_code == 200 and evs[-1][0] == "response.failed"
    resp = evs[-1][1]["response"]
    assert resp["status"] == "failed" and resp["error"]["code"] == "upstream_error" and "boom" in resp["error"]["message"]
    assert stored.status_code == 200 and stored.json()["status"] == "failed"


def test_store_false_and_the_per_token_cap(api, monkeypatch):
    api.fm.default = Reply(text="ok")
    monkeypatch.setattr(openai_api, "RESP_KEEP", 3)

    async def go():
        async with api.client() as c:
            off = await c.post("/v1/responses", headers=H("tok-books"), json={"input": "not kept", "store": False})
            off_get = await c.get(f"/v1/responses/{off.json()['id']}", headers=H("tok-books"))
            off_prev = await c.post("/v1/responses", headers=H("tok-books"), json={"input": "x", "previous_response_id": off.json()["id"]})
            ids = [(await c.post("/v1/responses", headers=H("tok-books"), json={"input": f"n{i}"})).json()["id"] for i in range(4)]
            kept = [(await c.get(f"/v1/responses/{i}", headers=H("tok-books"))).status_code for i in ids]
            mine = await c.post("/v1/responses", headers=H("tok-api"), json=ask("separate"))
            return off, off_get, off_prev, kept, mine
    off, off_get, off_prev, kept, mine = run(go())
    assert off.status_code == 200 and off.json()["store"] is False and off_get.status_code == 404 and off_prev.status_code == 404
    assert kept == [404, 200, 200, 200]  # 每把令牌最多 RESP_KEEP 条：最旧的删掉
    with store.read() as conn:
        n = conn.execute("SELECT COUNT(*) FROM engine_api_responses WHERE token='api-bookclub'").fetchone()[0]
        assert n == 3 and conn.execute("SELECT COUNT(*) FROM engine_api_responses WHERE id=?", (mine.json()["id"],)).fetchone()[0] == 1


def test_shutdown_mid_stream_ends_incomplete(api, monkeypatch):
    monkeypatch.setattr(chat, "SHUTTING_DOWN", False)
    api.fm.push(Reply(text="a slow and long answer " * 6, chunk=4, delay=0.05))

    async def go():
        async with api.client() as c:
            task = asyncio.create_task(c.post("/v1/responses", headers=H("tok-api", **{"X-Session-Id": "r-down"}), json=ask("talk", stream=True)))
            t0 = time.monotonic()
            # 等第一段字到了再停（同 test_engine_acp）：只等登记、再固定等 0.2 秒，CI 的机器慢时一个字都还没出，就没有半截回复可存
            while not any(r.text for r in openai_api.RUNS.values()) and time.monotonic() - t0 < 10:
                await asyncio.sleep(0.01)
            assert chat.stop_engine_runs() == 1
            return await task
    r = run(go())
    evs = sse(r.text)
    assert evs[-1][0] == "response.incomplete" and not openai_api.RUNS
    resp = evs[-1][1]["response"]
    assert resp["status"] == "incomplete" and resp["incomplete_details"] == {"reason": "interrupted"} and said(resp)
