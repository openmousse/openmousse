"""10/19：主人自己的一次性调用交给他自己登录的 Claude Code（engine/claude_brain.ask，走他的套餐）：llmjson.ask / engine.ask_json（会议纪要、
播客、学习页……）、一问一答（owner.stream：Zen「想完了」、会议的发言）、压缩的摘要（engine/compact.py）、/api/engine/oneshot。
谁能用照 may_spend：报了主人来路的才花；不报的、名片 Agent、Doorman、外人回合、对外接口照旧按量。没做成换按量的备用，没配照旧报错。
和整轮对话共用同时跑的上限，排在它们后面，一次性的另有一个小上限。

假的 claude 是 tests/fake_claude.py，按量的是 FakeModel，不连真的模型、不花钱。数据都是编的。"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

import engine
from engine import claude_brain, compact, events, models, owner, runners
from engine.errors import EngineError
from engine.session import DbSession, user
from test_engine_claude_code import BUILTIN, cc, serving, until  # noqa: F401 — cc 是那边的 fixture（假的 claude + /mcp），这里经 claude 拿

pytest.importorskip("mcp")

DAY = "2026-10-19"
API = "deepseek/deepseek-flash"
SIGNED_OUT = {"api_error": "Not logged in · Please run /login", "error": "authentication_failed"}
LIMIT = {"api_error": "You've hit your session limit · resets 3pm", "error": "rate_limit"}
MINUTES = {"type": "object", "required": ["todos"]}


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def claude(request):
    return request.getfixturevalue("cc")


def use(server_json, extra: dict | None = None, **purposes) -> None:
    """builtin，默认模型按量（deepseek），engine.use 照给的写。"""
    server_json({"claw": {"kind": "builtin"}, "engine": {"model": API, "use": purposes, **(extra or {})}})


def text_in(rec: dict) -> str:
    """假的 claude 从标准输入读到的那一句（stream-json 的 user 消息）里的字。"""
    return "".join(b.get("text", "") for b in rec["msg"]["message"]["content"] if isinstance(b, dict))


@pytest.fixture
def procs(monkeypatch):
    """数同时有几个 claude 进程：now / peak（全部）、once / peak_once（一次性的），order = 起、停的先后（按对话）。"""
    st = SimpleNamespace(now=0, peak=0, once=0, peak_once=0, order=[])
    real = claude_brain.drive

    async def drive(turn, cmd, cwd, env, first):
        st.now += 1
        st.once += int(turn.oneshot)
        st.peak, st.peak_once = max(st.peak, st.now), max(st.peak_once, st.once)
        st.order.append(("start", turn.conversation))
        try:
            await real(turn, cmd, cwd, env, first)
        finally:
            st.now -= 1
            st.once -= int(turn.oneshot)
            st.order.append(("end", turn.conversation))
    monkeypatch.setattr(claude_brain, "drive", drive)
    return st


# —— 谁能花 ——————————————————————————————————————————————————————————————————————————

def test_only_the_owners_own_one_shots_may_spend_the_plan(claude, server_json):
    use(server_json, {"models": ["moonshot/kimi-k3"]}, json="claude-code/sonnet", compact=["claude-code/haiku", "moonshot/kimi-k3"],
        card="claude-code/opus", doorman=True)
    for origin in ("user", "auto", "relay"):  # 主人自己的：app 里点的、他自己的定时任务、主对话转过来的
        assert models.route("json", kind="owner", origin=origin) == "claude-code/sonnet"
        assert models.route("compact", kind="owner", origin=origin) == "claude-code/haiku"
    for kind, origin in (("owner", None), ("owner", "api"), ("owner", "acp"), ("owner", "mcp"), ("owner", "task"), ("cyber", "user"),
                         ("outsider", "card"), ("outsider", "a2a"), ("outsider", "doorman")):
        assert models.route("json", kind=kind, origin=origin) == API, (kind, origin)  # 换成 outside() 挑的按量模型
        assert models.route("compact", kind=kind, origin=origin) == "moonshot/kimi-k3", (kind, origin)  # 套餐打头的列表：后面那个按量的
    assert models.route("json") == API  # 不报来路 = 当外人
    assert models.route("card") == models.route("doorman") == API  # 名片 Agent、Doorman 照旧按量
    assert models.route("json", "claude-code/opus", kind="owner", origin="user") == "claude-code/opus"  # 调用方指定的套餐：主人的才认
    assert models.route("json", "claude-code/opus") == API
    assert models.purpose_chain("compact", "claude-code/haiku") == ["moonshot/kimi-k3"] and models.purpose_chain("json", "claude-code/sonnet") is None
    assert models.purpose_chain("compact", "moonshot/kimi-k3") == []


def test_the_one_shot_cap_reads_the_config(server_json):
    def cap(**cc_cfg):
        server_json({**BUILTIN, "engine": {**BUILTIN["engine"], "claude_code": cc_cfg}})
        return claude_brain.max_concurrent(), claude_brain.max_oneshot()
    assert cap() == (2, 1)  # 默认：留一个位子给整轮对话
    assert cap(max_concurrent=4) == (4, 3) and cap(max_concurrent=1) == (1, 1)  # 只有一个位子：没法留，一次性的也能用它
    assert cap(max_concurrent=3, max_oneshot=1) == (3, 1) and cap(max_concurrent=2, max_oneshot=5) == (2, 2)
    assert cap(max_oneshot="x") == (2, 1) and cap(max_oneshot=0) == (2, 1)


# —— 一次性 JSON（llmjson.ask → engine.ask_json）————————————————————————————————————————————

def test_an_owner_json_call_runs_on_claude_code_with_no_tools(claude, server_json):
    import llmjson
    use(server_json, json="claude-code/sonnet")
    model = claude.fake_model([])  # 按量的一次都不该调（调了 FakeModel 就报错）
    claude.scenario_([[{"say": '{"todos": [{"text": "Book the community hall", "owner": "g-events"}], "open": []}'}]])
    before = {t["id"] for t in events.turns("json", 200)}
    v, via = run(llmjson.ask("You write the minutes of a meeting.", {"speeches": ["We still need a venue for the fictional fair."]}, MINUTES,
                             timeout=60, thinking="medium", kind="owner", origin="user"))
    assert v["todos"][0]["text"] == "Book the community hall" and via == "builtin:claude-code/sonnet" and model.requests == []
    start = claude.records("start")[-1]
    argv = start["argv"]
    assert argv[0] == "-p" and argv[argv.index("--tools") + 1] == "" and "--strict-mcp-config" in argv and "--mcp-config" not in argv
    assert argv[argv.index("--setting-sources") + 1] == "" and "--disable-slash-commands" in argv and "--no-session-persistence" in argv
    assert argv[argv.index("--model") + 1] == "sonnet" and argv[argv.index("--effort") + 1] == "medium" and "--max-turns" not in argv
    assert not {"--resume", "--session-id", "--allowedTools", "--append-system-prompt"} & set(argv)
    assert start["system"].startswith("You write the minutes of a meeting.") and "Reply with ONE JSON value only" in start["system"]
    assert start["system_mode"] == 0o600 and start["cwd"].endswith("/claude-code/.oneshot") and start["cwd_mode"] == 0o700
    env = start["env"]
    assert env["ANTHROPIC_API_KEY"] is None and env["ANTHROPIC_BASE_URL"] is None and env["CLAUDECODE"] is None  # 只用它自己存的登录
    assert env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] == "1" and env["ENABLE_CLAUDEAI_MCP_SERVERS"] == "false"
    asked = text_in(claude.records("stdin")[-1])
    assert asked.startswith("INPUT_JSON:") and "fictional fair" in asked
    t = next(t for t in events.turns("json", 200) if t["id"] not in before)
    assert (t["kind"], t["model"], t["origin"], t["status"]) == ("owner", "claude-code/sonnet", "user", "done")
    assert t["usage"]["cost"] is None and t["usage"]["notional_cost_usd"] == 0.02 and t["usage"]["via"] == "claude-code"  # 套餐：不记钱
    assert not list(Path(claude_brain.base_dir()).glob(".system-*"))  # 开头的临时文件答完就删


def test_long_outputs_and_a_bad_reply_once(claude, server_json):
    """要长输出的（学习页）经 CLAUDE_CODE_MAX_OUTPUT_TOKENS 交给它；回的不是 JSON 再问一次（同一个模型）。"""
    import llmjson
    use(server_json, json="claude-code/sonnet")
    claude.fake_model([])
    claude.scenario_([[{"say": "Here is the page you asked for."}], [{"say": '{"markdown": "# Session 3"}'}]])
    v, _ = run(llmjson.ask("Write the study page.", {"session": 3}, {"type": "object", "required": ["markdown"]}, max_tokens=32_000,
                           kind="owner", origin="user"))
    assert v == {"markdown": "# Session 3"} and len(claude.records("start")) == 2
    assert {s["env"]["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] for s in claude.records("start")} == {"32000"}


def test_others_and_unreported_calls_never_start_claude_code(claude, server_json):
    import llmjson
    use(server_json, json="claude-code/sonnet", card="claude-code/opus", doorman=True)
    model = claude.fake_model([{"text": '{"todos": []}'}, {"text": '{"reply": "Hi, the owner is away."}'}, {"text": '{"verdict": "ok"}'},
                               {"text": "plain answer"}])
    _, via = run(llmjson.ask("Summarise.", {"x": 1}, MINUTES))  # 不报来路
    assert via == f"builtin:{API}"
    _, via = run(engine.ask_json("Answer the friend.", {"message": "hi"}, model=models.route("card"), purpose="card", need="reply"))
    assert via == f"builtin:{API}"  # 名片 Agent
    _, via = run(engine.ask_json("Judge the draft.", {"draft": "hi"}, model=models.route("doorman"), purpose="doorman", need="verdict"))
    assert via == f"builtin:{API}"  # Doorman
    for kind, origin in (("outsider", "card"), ("owner", "api"), ("owner", None)):  # 硬给套餐也不跑
        with pytest.raises(EngineError) as e:
            run(engine.ask_json("x", {}, model="claude-code/sonnet", kind=kind, origin=origin))
        assert e.value.kind == "rule"
    with pytest.raises(EngineError):
        run(claude_brain.ask("x", "y", model="claude-code/sonnet", origin="a2a", conversation="c"))
    assert run(owner.stream([{"role": "user", "content": "quick question"}], "agent:main:x", "claude-code/sonnet", lambda t: None)) == "plain answer"
    assert claude.records() == [] and [r[0] for r in model.requests] == [API] * 4  # 假的 claude 一次没起


@pytest.mark.parametrize("step,kind", [(SIGNED_OUT, "auth"), (LIMIT, "quota"), ({"sleep": 8}, "timeout")])
def test_a_failed_plan_call_switches_to_the_pay_per_use_backup(claude, server_json, monkeypatch, step, kind):
    import llmjson
    use(server_json, json=["claude-code/sonnet", API])
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-deepseek-key")
    model = claude.fake_model([{"text": '{"todos": ["from the backup"]}'}])
    claude.scenario_([[step]])
    before = {t["id"] for t in events.turns("json", 200)}
    v, via = run(llmjson.ask("Write the minutes.", {"x": 1}, MINUTES, timeout=7, kind="owner", origin="user"))
    assert v == {"todos": ["from the backup"]} and via == f"builtin:{API}" and [r[0] for r in model.requests] == [API]
    new = [t for t in events.turns("json", 200) if t["id"] not in before]
    assert [(t["model"], t["status"]) for t in reversed(new)] == [("claude-code/sonnet", "error"), (API, "done")]
    assert {"auth": "signed out", "quota": "usage limit", "timeout": "didn't answer within"}[kind] in new[-1]["error"]


def test_a_refusal_is_not_routed_around(claude, server_json, monkeypatch):
    """它拒答（stop_reason refusal）：不当成出错换按量的备用去答（同 loop.py：拒答不换）。"""
    import llmjson
    use(server_json, {"fallback": False}, json=["claude-code/sonnet", API])
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-deepseek-key")
    model = claude.fake_model([])
    claude.scenario_([[{"result": {"stop_reason": "refusal", "result": ""}}]])
    with pytest.raises(llmjson.LLMError) as e:
        run(llmjson.ask("Write the minutes.", {"x": 1}, MINUTES, kind="owner", origin="user"))
    assert "declined" in str(e.value) and model.requests == []


def test_a_missing_login_switches_too(claude, server_json, monkeypatch):
    """这台电脑上的 Claude Code 没登录（runners 查不到它自己存的登录）：还没起它就知道，照样换。"""
    import llmjson
    use(server_json, {"fallbacks": [API]}, json="claude-code/sonnet")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-deepseek-key")
    monkeypatch.setattr(runners, "_found", {})
    monkeypatch.setattr(runners, "refresh", lambda: {})
    monkeypatch.setattr(runners, "bin_of", lambda name: str(claude.exe))
    claude.fake_model([{"text": '{"todos": []}'}])
    _, via = run(llmjson.ask("Write the minutes.", {"x": 1}, MINUTES, kind="owner", origin="user"))
    assert via == f"builtin:{API}" and claude.records() == []


def test_without_a_backup_the_error_goes_where_it_went_before(claude, server_json, monkeypatch):
    import llmjson
    use(server_json, {"fallback": False}, json="claude-code/sonnet")
    claude.scenario_([[LIMIT]])
    model = claude.fake_model([])
    with pytest.raises(llmjson.LLMError) as e:  # engine.fallback = false：直接报，说清是哪一类
        run(llmjson.ask("Write the minutes.", {"x": 1}, MINUTES, kind="owner", origin="user"))
    assert "usage limit" in str(e.value) and model.requests == []
    use(server_json, json="claude-code/sonnet")  # 默认：照旧退回原来的路，fallback_input 照旧（不带别人的话）
    got: list = []

    async def via_complete(prompt, input_, schema, timeout, origin=None):
        got.append((input_, origin))
        return {"todos": ["from the old path"]}, "complete"
    monkeypatch.setattr(llmjson, "via_complete", via_complete)
    v, via = run(llmjson.ask("Write the minutes.", {"transcript": "a friend's words"}, MINUTES, fallback_input={"transcript": "mine only"},
                             kind="owner", origin="user"))
    assert via == "complete" and got == [({"transcript": "mine only"}, "user")]
    with pytest.raises(llmjson.LLMError):  # 只许零工具的：内核这条路没成，别的 claw 又没有 llm-task
        run(llmjson.ask("Notes about a friend.", {"transcript": "a friend's words"}, MINUTES, tool_free_only=True, kind="owner", origin="user"))


# —— 一问一答（owner.stream：Zen「想完了」、会议的发言）————————————————————————————————————————

def test_zen_done_drafts_on_the_plan(claude, server_json, monkeypatch, tmp_path):
    import chat
    import sources
    import think
    server_json({"claw": {"kind": "builtin"}, "engine": {"model": "claude-code/opus", "models": [API]}, "think": {"vault": str(tmp_path / "vault")}})
    monkeypatch.setattr(sources, "memory_tree", None)
    think._cache.clear()
    p = think.inbox_dir() / "2026-10-19 0800 th-0c0c0c0a.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    think.write_atomic(p, think.dump_note({"id": "th-0c0c0c0a", "kind": "text", "created_at": "2026-10-19T08:00:00+01:00", "source": "app"},
                                          "Rowing on Sundays clears my head more than running does."))
    with chat._lock, think.tdb() as conn:
        conn.execute("INSERT OR REPLACE INTO think_topics(id, title, fragments, status, created_at, updated_at) VALUES(?,?,?,?,?,?)",
                     ("tp-0c0c0c0a", "Sunday rowing", json.dumps(["th-0c0c0c0a"]), "open", chat.now_iso(), chat.now_iso()))
    model = claude.fake_model([])
    claude.scenario_([[{"say": json.dumps({"title": "Sunday rowing", "oneLine": "Rowing resets the week.",
                                           "points": [{"text": "Rowing clears the head", "from": ["th-0c0c0c0a"]}]})}]])
    run(think.make_draft("tp-0c0c0c0a"))
    r = think.load_topic("tp-0c0c0c0a")
    assert r["draft_status"] == "ready" and json.loads(r["draft"])["oneLine"] == "Rowing resets the week." and model.requests == []
    start = claude.records("start")[-1]
    assert start["argv"][start["argv"].index("--model") + 1] == "opus" and "--no-session-persistence" in start["argv"]
    assert "Sunday rowing" in text_in(claude.records("stdin")[-1])
    t = events.turns("agent:main:grava:tp-0c0c0c0a:draft")[0]  # 一问一答记在它的键下
    assert (t["kind"], t["model"], t["origin"]) == ("owner", "claude-code/opus", "user")


def test_a_one_question_on_the_plan_switches_or_says_why(claude, server_json, monkeypatch):
    server_json({"claw": {"kind": "builtin"}, "engine": {"model": "claude-code/opus", "fallbacks": [API]}})
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-deepseek-key")
    claude.scenario_([[SIGNED_OUT]])
    model = claude.fake_model([{"text": "answered by the backup"}])
    out: list[str] = []
    asked = [{"role": "user", "content": "earlier question"}, {"role": "assistant", "content": "earlier answer"},
             {"role": "user", "content": "what did the fair cost?"}]
    assert run(owner.stream(asked, "agent:main:meet-x", None, out.append, origin="user")) == "answered by the backup"
    assert "".join(out) == "answered by the backup" and [r[0] for r in model.requests] == [API]
    sent = text_in(claude.records("stdin")[-1])  # 带过来的记录接在这句话前面
    assert "User: earlier question" in sent and "Assistant: earlier answer" in sent and sent.endswith("what did the fair cost?")
    server_json({"claw": {"kind": "builtin"}, "engine": {"model": "claude-code/opus"}})  # 没配备用：照旧报错，说清是哪一类、该做什么
    with pytest.raises(EngineError) as e:
        run(owner.stream(asked, "agent:main:meet-x", None, out.append, origin="user"))
    assert e.value.kind == "auth" and "sign in again" in str(e.value)


def test_meeting_speeches_and_complete_carry_the_owner_origin(claude, server_json):
    import claw
    server_json({"claw": {"kind": "builtin"}, "engine": {"model": API}})
    claude.scenario_([[{"say": "I'll take the venue."}]])
    model = claude.fake_model([{"text": "not the plan"}])
    parts: list[str] = []
    run(claw.stream([{"role": "user", "content": "your turn"}], "mousse:g-events:meeting", "claude-code/haiku", parts.append, origin="user"))
    assert "".join(parts) == "I'll take the venue." and model.requests == []
    assert run(claw.complete("one more", "mousse:g-events:x", "claude-code/haiku")) == "not the plan"  # 不报来路：按量
    assert len(claude.records("start")) == 1


# —— 压缩的摘要 ————————————————————————————————————————————————————————————————————————

def chat_session(name: str) -> DbSession:
    s = DbSession.create(f"test:{name}", kind="owner")
    for i, (q, a) in enumerate((("We picked the hall on Elm Street for the fair.", "Noted: the hall on Elm Street."),
                                ("Budget is 300 for snacks.", "Noted: 300 for snacks."))):
        s.append(user(q), turn=f"t{i}")
        s.append({"role": "assistant", "content": [{"type": "text", "text": a}], "provider": "deepseek", "model": "deepseek-flash",
                  "stop": "end", "usage": {}}, turn=f"t{i}")
    return s


def test_owner_compaction_is_summarised_on_the_plan(claude, server_json):
    use(server_json, compact="claude-code/haiku")
    claude.scenario_([[{"say": "<summary>The fair is in the hall on Elm Street; snacks budget 300. Open request: none</summary>"}]])
    model = claude.fake_model([{"text": "<summary>From the pay-per-use model.</summary>"}])
    view = chat_session("cc-compact").view()
    spec = engine.Spec(kind="owner", model=API, conversation="t-cc-compact", agent="main", origin="user")
    summary, usage, turns = run(compact.summarize(view, len(view.rows), spec))
    assert summary.startswith("The fair is in the hall on Elm Street") and usage["cost"] is None and model.requests == []
    start = claude.records("start")[-1]
    assert start["system"] == compact.SUMMARIZER and start["argv"][start["argv"].index("--model") + 1] == "haiku"
    assert start["argv"][start["argv"].index("--effort") + 1] == "low" and "<conversation>" in text_in(claude.records("stdin")[-1])
    t = events.turns("compact:t-cc-compact")[0]
    assert t["id"] == turns[0] and (t["kind"], t["model"], t["origin"]) == ("owner", "claude-code/haiku", "user")
    # 对外接口的一轮（origin api）、后台任务：不花套餐，换成 outside() 挑的
    for origin in ("api", "task"):
        summary, _, _ = run(compact.summarize(view, len(view.rows), dataclasses.replace(spec, origin=origin)))
        assert summary == "From the pay-per-use model."
        model.script.append({"text": "<summary>From the pay-per-use model.</summary>"})
    assert len(claude.records("start")) == 1 and [r[0] for r in model.requests] == [API, API]


def test_a_failed_plan_summary_falls_back_to_the_turns_own_model(claude, server_json):
    use(server_json, compact="claude-code/haiku")  # 没写链、没有 engine.fallbacks：最后是这一轮自己的模型（以前就用它）
    claude.scenario_([[LIMIT]])
    model = claude.fake_model([{"text": "<summary>Summarised by the turn's own model.</summary>"}])
    view = chat_session("cc-compact-fail").view()
    spec = engine.Spec(kind="owner", model="moonshot/kimi-k3", conversation="t-cc-compact-fail", agent="main", origin="user")
    summary, usage, turns = run(compact.summarize(view, len(view.rows), spec))
    assert summary == "Summarised by the turn's own model." and len(turns) == 2 and [r[0] for r in model.requests] == ["moonshot/kimi-k3"]
    assert [t["model"] for t in events.turns("compact:t-cc-compact-fail")] == ["moonshot/kimi-k3", "claude-code/haiku"]


# —— 位子：和整轮对话共用、排在后面 ———————————————————————————————————————————————————————

def json_call(conv: str):
    return engine.ask_json("Score it.", {"x": conv}, model="claude-code/sonnet", kind="owner", origin="user", conversation=conv, timeout=30)


def test_one_shots_queue_behind_full_turns(claude, procs, server_json):
    """只有一个位子：整轮对话占着；一次性的先来排着，后来的整轮对话还是排在它前面。"""
    server_json({**BUILTIN, "engine": {**BUILTIN["engine"], "claude_code": {"max_concurrent": 1}}})
    claude.scenario_([[{"say": '{"ok": true}'}, {"sleep": 0.6}]])
    claude.fake_model([])

    async def go():
        async with serving(claude):
            a = asyncio.create_task(owner.respond("t-os-a", DAY, "hi", "claude-code/opus", lambda x: None))
            await until(lambda: procs.now == 1)
            x = asyncio.create_task(json_call("os-x"))
            await asyncio.sleep(0.2)
            b = asyncio.create_task(owner.respond("t-os-b", DAY, "hi", "claude-code/opus", lambda x: None))
            return await asyncio.gather(a, x, b)
    ra, (v, _), rb = run(go())
    assert ra.status == rb.status == "done" and v == {"ok": True} and procs.peak == 1
    assert [c for k, c in procs.order if k == "start"] == ["t-os-a", "t-os-b", "os-x"]  # 先来的一次性调用排在后来的整轮对话后面
    q = next(s for s in events.steps(events.turns("os-x")[0]["id"]) if s["type"] == "queue")
    assert q["status"] == "ok" and q["data"]["waited_ms"] > 0


def test_one_shots_never_take_the_last_slot(claude, procs, server_json):
    """两个位子（默认）：一次性的同时最多一个，剩下那个总留给整轮对话，主对话不用等它们。"""
    use(server_json)
    claude.scenario_([[{"say": '{"ok": true}'}, {"sleep": 0.6}]])
    claude.fake_model([])
    seen: list[dict] = []

    async def go():
        async with serving(claude):
            xs = [asyncio.create_task(json_call(f"os-n{i}")) for i in range(3)]
            await until(lambda: procs.once == 1)
            await asyncio.sleep(0.2)
            assert procs.now == 1  # 第二个一次性的没起：位子空着也不占
            a = await owner.respond("t-os-main", DAY, "hi", "claude-code/opus", lambda x: None, on_event=seen.append)
            return a, await asyncio.gather(*xs)
    a, outs = run(go())
    assert a.status == "done" and [v for v, _ in outs] == [{"ok": True}] * 3
    assert procs.peak_once == 1 and procs.peak == 2
    assert not any(e.get("phase") == "queued" for e in seen)  # 主对话没排队


def test_a_one_shot_that_waits_too_long_times_out_and_switches(claude, procs, server_json, monkeypatch):
    """排队的时间也算进 timeout：等不到位子就换按量的备用（不让后台的调用卡在队里）。"""
    server_json({**BUILTIN, "engine": {**BUILTIN["engine"], "claude_code": {"max_concurrent": 1}, "use": {"json": ["claude-code/sonnet", API]}}})
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-deepseek-key")
    claude.scenario_([[{"say": "busy"}, {"sleep": 4}]])
    model = claude.fake_model([{"text": '{"ok": "backup"}'}])

    async def go():
        async with serving(claude):
            a = asyncio.create_task(owner.respond("t-os-hold", DAY, "long job", "claude-code/opus", lambda x: None))
            await until(lambda: procs.now == 1)
            v = await engine.ask_json("Score it.", {}, model="claude-code/sonnet", kind="owner", origin="user", conversation="os-wait",
                                      timeout=7, fallbacks=[API])
            return await a, v
    a, (v, via) = run(go())
    assert a.status == "done" and v == {"ok": "backup"} and via == f"builtin:{API}" and [r[0] for r in model.requests] == [API]
    assert [c for k, c in procs.order if k == "start"] == ["t-os-hold"]  # 一次性的那个从没起过
    t = events.turns("os-wait")[-1]
    assert t["model"] == "claude-code/sonnet" and t["status"] == "error" and "didn't answer within" in t["error"]


# —— 给本机脚本的接口 ——————————————————————————————————————————————————————————————————

def test_the_oneshot_endpoint_counts_as_the_owner_unless_it_came_through_mcp(claude, server_json):
    import httpx

    import main
    server_json({"claw": {"kind": "builtin"}, "auth": {"tokens": {"local": "tok-local", "api": "tok-api"}},
                 "engine": {"model": API, "use": {"json": "claude-code/sonnet"}, "api": True}})
    claude.scenario_([[{"say": '{"score": 4, "why": "fits the brief"}'}]])
    model = claude.fake_model([{"text": '{"score": 2, "why": "pay-per-use"}'}])
    ask = {"prompt": "Score this role from 1 to 5.", "input": {"role": "Analyst, Northwind (fictional)"}, "required": ["score"]}

    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test", timeout=30) as c:
            h = {"Authorization": "Bearer tok-local"}
            mine = await c.post("/api/engine/oneshot", headers=h, json=ask)
            via_mcp = await c.post("/api/engine/oneshot", headers={**h, "X-Mousse-Via": "mcp"}, json=ask)
            outside = await c.post("/api/engine/oneshot", headers={"Authorization": "Bearer tok-api"}, json=ask)
            empty = await c.post("/api/engine/oneshot", headers=h, json={"prompt": " "})
            return mine, via_mcp, outside, empty
    mine, via_mcp, outside, empty = run(go())
    assert mine.status_code == 200 and mine.json()["json"]["score"] == 4 and mine.json()["via"] == "builtin:claude-code/sonnet"
    assert via_mcp.json()["json"]["score"] == 2 and via_mcp.json()["via"] == f"builtin:{API}"  # 外面的 MCP 客户端：不花套餐
    assert outside.status_code == 403 and empty.status_code == 400  # 对外接口的令牌进不来
    assert len(claude.records("start")) == 1 and [r[0] for r in model.requests] == [API]


def test_isolation_matches_the_full_turns(claude, tmp_path):
    """一次性调用和整轮对话一样不读用户的设置：同一份 --settings、同一套去掉的环境变量。"""
    sys_file = tmp_path / "s.md"
    cmd = claude_brain.oneshot_command("/x/claude", system_file=sys_file, model="opus")
    full = claude_brain.command("/x/claude", conf=tmp_path / "c.json", system_file=sys_file, tools=[], model="opus", session=None, resume=False)
    for flag in ("--setting-sources", "--settings", "--permission-mode", "--disallowedTools", "--system-prompt-file", "--model"):
        assert cmd[cmd.index(flag) + 1] == full[full.index(flag) + 1], flag
    assert stat.S_IMODE(claude_brain.oneshot_dir().stat().st_mode) == 0o700


def test_the_oneshot_endpoint_is_only_for_the_built_in_engine(server_json):
    import httpx

    import main
    server_json({"claw": {"kind": "openclaw"}, "auth": {"tokens": {"local": "tok-local"}}, "engine": {"model": API}})

    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test", timeout=30) as c:
            return await c.post("/api/engine/oneshot", headers={"Authorization": "Bearer tok-local"}, json={"prompt": "Score it."})
    assert run(go()).status_code == 404  # 别的 claw 上会退回带工具的回合，脚本带来的资料不能进去


def test_course_one_shots_keep_where_the_request_came_from(server_json, monkeypatch):
    import chat
    import llmjson
    import study
    use(server_json)
    seen = []

    async def fake_ask(prompt, input_, schema, **kw):
        seen.append(kw.get("origin"))
        return {"ok": 1}, "builtin:test"
    monkeypatch.setattr(llmjson, "ask", fake_ask)
    for via in (None, "mcp", "task"):
        tok = chat.REQUEST_VIA.set(via)
        try:
            assert run(study.gen_json({"thread": "t"}, "notes", "", "Write the notes.", "materials")) == ({"ok": 1}, "builtin:test")
        finally:
            chat.REQUEST_VIA.reset(tok)
    assert seen == ["user", "mcp", "task"]  # 经 /mcp、后台来的不当成主人自己问的，不花套餐
