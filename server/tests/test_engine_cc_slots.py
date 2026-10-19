"""10/19 彩排补的第 5 件：同时跑的 Claude Code 回合有上限（engine.claude_code.max_concurrent，默认 2；engine/claude_brain.py 的
_acquire / _release）。多出来的排队，事件里给 phase queued（app 照「在想」显示）；按量模型的回合不受影响；排着时点了停就不起它；
转交过来的那一轮不排。假的 claude 是 tests/fake_claude.py，按量的是 FakeModel，不连真的模型。"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import engine
from engine import claude_brain, events, owner
from test_engine_claude_code import BUILTIN, cc, collect, finish, serving, until  # noqa: F401 — cc 是那边的 fixture，这里经 claude 拿

DAY = "2026-10-19"


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def claude(request):
    return request.getfixturevalue("cc")


@pytest.fixture
def busy(monkeypatch):
    """数同时有几个 claude 进程在跑：peak = 最多的时候；order = 起、停的先后（按对话）。"""
    st = SimpleNamespace(now=0, peak=0, order=[])
    real = claude_brain.drive

    async def drive(turn, cmd, cwd, env, first):
        st.now += 1
        st.peak = max(st.peak, st.now)
        st.order.append(("start", turn.conversation))
        try:
            await real(turn, cmd, cwd, env, first)
        finally:
            st.now -= 1
            st.order.append(("end", turn.conversation))
    monkeypatch.setattr(claude_brain, "drive", drive)
    return st


def cap(server_json, n: int) -> None:
    server_json({**BUILTIN, "engine": {**BUILTIN["engine"], "claude_code": {"max_concurrent": n}}})


def test_the_limit_reads_the_config(server_json):
    server_json(BUILTIN)
    assert claude_brain.max_concurrent() == 2  # 默认
    for v, want in ((3, 3), ("4", 4), (0, 2), (-1, 1), ("lots", 2)):
        cap(server_json, v)
        assert claude_brain.max_concurrent() == want, v


def test_the_nightly_rush_runs_two_at_a_time_and_the_rest_wait(claude, busy):
    claude.scenario_([[{"say": "Digest written."}, {"sleep": 0.6}]])
    threads = ["t-slot-a", "t-slot-b", "t-slot-c"]
    seen: dict[str, list] = {t: [] for t in threads}

    async def go():
        async with serving(claude):
            return await asyncio.gather(*(owner.respond(t, DAY, "write the daily digest", "claude-code/opus", lambda x: None,
                                                        on_event=seen[t].append, origin="auto") for t in threads))
    res = run(go())
    assert [r.status for r in res] == ["done"] * 3 and [r.text for r in res] == ["Digest written."] * 3
    assert busy.peak == 2 and len(claude.records("start")) == 3  # 一个一个进程：最多两个同时在
    waited = [t for t in threads if any(e.get("type") == "phase" and e.get("phase") == "queued" for e in seen[t])]
    assert len(waited) == 1  # 第三个排了队
    step = next(s for s in events.steps(events.turns(waited[0])[0]["id"]) if s["type"] == "queue")
    assert step["status"] == "ok" and step["data"]["waited_ms"] > 0  # 事件表里看得到排了多久


def test_api_model_turns_do_not_wait_for_a_slot(claude, busy, server_json):
    cap(server_json, 1)
    claude.scenario_([[{"say": "slow one"}, {"sleep": 1.2}], [{"say": "second"}]])
    model = claude.fake_model([{"text": "from the API model"}])
    finished: list[str] = []

    async def go():
        async with serving(claude):
            async def one(thread: str, m: str):
                r = await owner.respond(thread, DAY, "hi", m, lambda x: None)
                finished.append(thread)
                return r
            a = asyncio.create_task(one("t-slot-cc1", "claude-code/opus"))
            await until(lambda: busy.now == 1)
            b = asyncio.create_task(one("t-slot-cc2", "claude-code/opus"))  # 排着
            c = await one("t-slot-api", "deepseek/deepseek-flash")  # 按量的：不排
            return await a, await b, c
    a, b, c = run(go())
    assert (a.status, b.status, c.status) == ("done", "done", "done") and c.text == "from the API model" and len(model.requests) == 1
    assert finished[0] == "t-slot-api" and busy.peak == 1  # 按量的先回完；claude 进程始终只有一个
    assert busy.order == [("start", "t-slot-cc1"), ("end", "t-slot-cc1"), ("start", "t-slot-cc2"), ("end", "t-slot-cc2")]


def test_stop_while_queued_never_starts_it_and_frees_nothing_it_did_not_hold(claude, busy, server_json):
    cap(server_json, 1)
    claude.scenario_([[{"say": "long one"}, {"sleep": 1.0}], [{"say": "after"}]])
    stopped = engine.Control()

    async def go():
        async with serving(claude):
            a = asyncio.create_task(owner.respond("t-slot-run", DAY, "hi", "claude-code/opus", lambda x: None))
            await until(lambda: busy.now == 1)
            seen: list = []
            b = asyncio.create_task(owner.respond("t-slot-stop", DAY, "hi", "claude-code/opus", lambda x: None, control=stopped,
                                                  on_event=seen.append))
            await until(lambda: any(e.get("phase") == "queued" for e in seen))
            stopped.abort()
            rb = await asyncio.wait_for(b, 3)  # 马上回来，不等前面那个
            ra = await a
            rc = await owner.respond("t-slot-next", DAY, "hi", "claude-code/opus", lambda x: None)  # 位子没算错：还能接着跑
            return ra, rb, rc
    ra, rb, rc = run(go())
    assert rb.status == "aborted" and ra.status == "done" and rc.status == "done" and rc.text == "after"
    assert [c for k, c in busy.order if k == "start"] == ["t-slot-run", "t-slot-next"]  # 停了的那个从没起过
    step = next(s for s in events.steps(events.turns("t-slot-stop")[0]["id"]) if s["type"] == "queue")
    assert step["status"] == "aborted"


def test_a_relayed_turn_does_not_queue_behind_the_turn_that_asked_for_it(claude, busy, server_json):
    """转交（origin relay）：发起它的那一轮正占着位子等它的回答，它再排队就互相等死了。"""
    cap(server_json, 1)
    claude.scenario_([[{"say": "asking an Agent"}, {"sleep": 1.0}], [{"say": "the Agent's answer"}]])

    async def go():
        async with serving(claude):
            a = asyncio.create_task(owner.respond("t-slot-main", DAY, "ask the diet Agent", "claude-code/opus", lambda x: None))
            await until(lambda: busy.now == 1)
            b = await owner.respond("t-slot-agent", DAY, "question from the main chat", "claude-code/opus", lambda x: None, origin="relay")
            return await a, b
    a, b = run(go())
    assert a.status == b.status == "done" and busy.peak == 2


def test_the_app_shows_a_queued_turn_as_thinking(claude, busy, server_json, monkeypatch):
    """排着的那一轮：SSE 的 progress 里 phase 是 queued（app 认不得的 phase 一律显示「正在思考」）。"""
    import chat
    monkeypatch.setattr(chat, "PROGRESS_EVERY", 0)
    cap(server_json, 1)
    claude.scenario_([[{"say": "busy"}, {"sleep": 1.2}], [{"say": "queued reply"}]])

    async def go():
        async with serving(claude):
            chat.start_run("t-slot-first", "long job", "claude-code/opus")
            await until(lambda: busy.now == 1)
            r = chat.start_run("t-slot-second", "hello", "claude-code/opus")
            evs = await collect(chat.attach(r))
            await finish(chat, "t-slot-second")
            return r, evs
    r, evs = run(go())
    phases = [d.get("phase") for e, d in evs if e == "progress"]
    assert "queued" in phases and phases.index("queued") < len(phases) - 1  # 排着的时候是 queued，轮到了接着往下走
    assert r.status == "ok" and r.text == "queued reply" and busy.peak == 1


def test_a_scheduled_chat_run_that_waits_for_a_slot_is_not_timed_out(claude, busy, server_json):
    from engine import unattended
    cap(server_json, 1)
    claude.scenario_([[{"say": "busy"}, {"sleep": 1.8}], [{"say": "the check is done"}]])

    async def go():
        async with serving(claude):
            first = asyncio.create_task(owner.respond("t-slot-hold", DAY, "long job", "claude-code/opus", lambda x: None))
            await until(lambda: busy.now == 1)
            job = await unattended.in_chat("t-slot-job", "Check", "do the check", level="none", idle=0.6, model="claude-code/opus")
            return await first, job
    first, job = run(go())
    assert first.status == "done" and job.status == "done" and job.text == "the check is done"  # 排了比 idle 还久，照样跑完
