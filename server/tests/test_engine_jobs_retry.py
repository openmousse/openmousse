"""第 4 批：定时任务失败按退避重试（engine/jobs.py）。调模型的默认不重试，engine.jobs.retry 写 true 才开（会多花额度和钱）；开了以后：临时错误（还没出字就失败、连不上、429 / 过载、超时）按 30 秒、1 分、5 分……重试，
不越过下一次正常到点；永久错误（规矩不许、配置不对、没登录、花费到了上限、急停）不重试；服务商说配额窗口关了（带重置时间）挂到窗口之后；
只跑命令的默认不重试，写了 retry 才重试；发进对话的不重试临时错误；连着 5 次停用照旧（重试那几次不重复算）。
模型是假的（FakeModel 按剧本回放）；时钟是假的：when.now 换成一个能拨的钟，tick(at=…) 也按它。库是这个测试自己的。"""
from __future__ import annotations

import os
import stat
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

import config
from engine import estop, jobs, loop, runners, spend, unattended, when
from engine.errors import EngineError
import test_engine_jobs as tj
from test_engine import echo_tool
from test_engine_jobs import fire, msgs, run, runs, user_job

builtin, fake, pushes = tj.builtin, tj.fake, tj.pushes
UTC = ZoneInfo("UTC")
T0 = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def own_world(tmp_path, monkeypatch):
    """自己的库、能拨的钟、急停的文件不在共用的地方；一次模型调用里不再重试（MODEL_RETRIES）：这里数的是任务那一层的重试。"""
    monkeypatch.setattr(config.settings, "db", tmp_path / "retry.db")
    monkeypatch.setattr(estop, "path", lambda: tmp_path / "ESTOP")
    monkeypatch.setattr(runners, "_found", {})
    monkeypatch.setattr(loop, "MODEL_RETRIES", ())
    clock = {"now": T0}
    monkeypatch.setattr(when, "now", lambda: clock["now"])
    jobs._running.clear()
    spend._blocked.clear()
    yield clock


@pytest.fixture
def retrying(builtin):
    """调模型的任务默认不重试（engine.jobs.retry 不写 = 关）：要看重试的测试先把它打开。"""
    builtin({"jobs": {"retry": True}})


def at_next(clock, j: dict, plus: float = 1.0) -> datetime:
    """把钟拨到这个任务的下一次（再晚 plus 秒），回那一刻。"""
    clock["now"] = when.parse_iso(jobs.get(j["id"])["next_run"]) + timedelta(seconds=plus)
    return clock["now"]


def rate_limited() -> EngineError:
    return EngineError("deepseek HTTP 429: rate limit reached for requests", kind="http", retryable=True, status=429)


def last_id() -> int:
    """主对话现在最后一条的 id（对话库是测试共用的：只看这之后的）。"""
    ms = msgs("main")
    return ms[-1]["id"] if ms else 0


def notes(since: int) -> list[str]:
    """since 之后对话里那几句「这次没跑成」。"""
    return [m["text"] for m in msgs("main") if m["id"] > since and "didn't run this time" in (m["text"] or "")]


# —— 默认关 ——————————————————————————————————————————————————————————————————————————————

def test_model_jobs_are_not_retried_unless_turned_on(builtin, fake, pushes, own_world):
    clock = own_world
    j = user_job("main", when="every 1h", task="Summarise the news.")
    reopen = T0 + timedelta(hours=5, minutes=20)
    fake([rate_limited(), EngineError(f"Your Claude plan's usage limit is used up (resets {reopen.isoformat()})", kind="quota")])
    t = at_next(clock, j)
    fire(t)
    k = jobs.get(j["id"])
    assert k["retry"] is None and when.parse_iso(k["next_run"]) == t - timedelta(seconds=1) + timedelta(hours=1) and k["fails"] == 1
    t = at_next(clock, j)
    fire(t)  # 配额窗口关着也照旧：下一次正常到点，不挂到窗口之后
    k = jobs.get(j["id"])
    assert k["retry"] is None and when.parse_iso(k["next_run"]) == t - timedelta(seconds=1) + timedelta(hours=1) and k["fails"] == 2
    assert [r["kind"] for r in runs(j["id"])].count("retry") == 0


# —— 临时错误：按表重试，不越过下一次正常到点 ——————————————————————————————————————————————————

def test_a_transient_error_is_retried_on_the_backoff_ladder(builtin, fake, pushes, own_world, retrying):
    clock = own_world
    j = user_job("main", when="every 1h", task="Remind the user to drink a glass of water.")
    natural = when.parse_iso(j["next_run"])
    fake([rate_limited(), rate_limited(), {"text": "Water time."}])
    since = last_id()
    t = at_next(clock, j)
    assert fire(t) == [j["id"]]
    k = jobs.get(j["id"])
    r = runs(j["id"])[-1]
    assert r["status"] == "error" and "429" in r["error"] and "retry" in r["error"].lower()
    assert when.parse_iso(k["next_run"]) == t + timedelta(seconds=30) and k["retry"]["n"] == 1 and k["fails"] == 1
    assert when.parse_iso(k["retry"]["back"]) == natural + timedelta(hours=1)  # 重试完回到原来的格子上
    assert notes(since) == []  # 要重试的先不说
    t = at_next(clock, j)
    assert fire(t) == [j["id"]]
    k = jobs.get(j["id"])
    assert runs(j["id"])[-1]["kind"] == "retry" and when.parse_iso(k["next_run"]) == t + timedelta(seconds=60) and k["retry"]["n"] == 2
    assert k["fails"] == 1 and k["runs"] == 1  # 同一次的重试：不重复算「连着几次」、不重复算「跑了几次」
    t = at_next(clock, j)
    assert fire(t) == [j["id"]]
    k = jobs.get(j["id"])
    assert runs(j["id"])[-1]["status"] == "ok" and k["fails"] == 0 and k["retry"] is None
    assert when.parse_iso(k["next_run"]) == natural + timedelta(hours=1) and msgs("main")[-1]["text"] == "Water time." and notes(since) == []


def test_retries_never_go_past_the_next_normal_run(builtin, fake, pushes, own_world, retrying):
    clock = own_world
    j = user_job("main", when="every 2m", task="Remind the user to stretch.")
    fake([rate_limited()] * 4)
    since = last_id()
    t = at_next(clock, j)
    natural = when.parse_iso(j["next_run"]) + timedelta(minutes=2)
    fire(t)  # +30 秒：在下一次（+2 分）之前
    assert when.parse_iso(jobs.get(j["id"])["next_run"]) == t + timedelta(seconds=30)
    t = at_next(clock, j)
    fire(t)  # 再 +1 分：还在之前
    assert when.parse_iso(jobs.get(j["id"])["next_run"]) == t + timedelta(seconds=60)
    t = at_next(clock, j)
    fire(t)  # 再 +5 分：越过了下一次正常到点，不重试，等那一次
    k = jobs.get(j["id"])
    assert when.parse_iso(k["next_run"]) == natural and k["retry"] is None and k["fails"] == 1
    assert len(notes(since)) == 1 and "429" in notes(since)[0]  # 重试完了还不行才说一声


def test_permanent_errors_and_runs_that_did_something_are_not_retried(builtin, fake, pushes, own_world, retrying, monkeypatch):
    clock = own_world
    did: list = []
    monkeypatch.setattr(unattended, "tools_for", lambda agent: [echo_tool(did)])
    j = user_job("main", when="every 1h", task="Check the board.")
    fake([EngineError("no API key for deepseek (put DEEPSEEK_API_KEY in the .env)", kind="config")])
    t = at_next(clock, j)
    fire(t)
    k = jobs.get(j["id"])
    assert k["retry"] is None and when.parse_iso(k["next_run"]) == t - timedelta(seconds=1) + timedelta(hours=1)
    fake([EngineError("deepseek HTTP 400: bad request", kind="http", status=400)])
    fire(at_next(clock, j))
    assert jobs.get(j["id"])["retry"] is None
    fake([{"text": "", "calls": [("c1", "echo", {"x": "a"})]}, rate_limited()])  # 调过工具以后才出错：再来一次可能重复做事
    fire(at_next(clock, j))
    k = jobs.get(j["id"])
    assert runs(j["id"])[-1]["status"] == "error" and k["retry"] is None and did
    clock["now"] = clock["now"] + timedelta(minutes=1)
    assert jobs.failure(jobs.get(j["id"]), jobs.Outcome(status="error", error="x", kind="budget"))[0] == "permanent"
    assert jobs.failure(jobs.get(j["id"]), jobs.Outcome(status="error", error="x", kind="rule"))[0] == "permanent"
    assert jobs.failure(jobs.get(j["id"]), jobs.Outcome(status="timeout", error="No activity for 600s"))[0] == "transient"
    assert jobs.failure(jobs.get(j["id"]), jobs.Outcome(status="error", error="can't reach deepseek: ConnectError"))[0] == "transient"
    assert jobs.failure(jobs.get(j["id"]), jobs.Outcome(status="error", error="deepseek HTTP 503: overloaded", text="half an answer"))[0] == "permanent"
    assert jobs.failure(jobs.get(j["id"]), jobs.Outcome(status="error", error="google HTTP 429: Resource has been exhausted"))[0] == "transient"
    assert jobs.failure(jobs.get(j["id"]), jobs.Outcome(status="error", error="the report has 503 lines, pid 511"))[0] == "permanent"  # 正文里的数不算
    assert jobs.failure(jobs.get(j["id"]), jobs.Outcome(status="error", error="openai HTTP 429: insufficient_quota"))[0] == "permanent"  # 没说什么时候恢复


def test_five_failed_runs_in_a_row_still_pause_the_job(builtin, fake, pushes, own_world, retrying):
    clock = own_world
    j = user_job("main", when="every 1h", task="Summarise the news.")
    fake([rate_limited()] * 40)
    for _ in range(200):
        if jobs.get(j["id"])["state"] != "on":
            break
        fire(at_next(clock, j))
    k = jobs.get(j["id"])
    assert k["state"] == "paused" and k["fails"] == jobs.FAILS_TO_PAUSE
    statuses = [r["kind"] for r in runs(j["id"])]
    assert statuses.count("retry") >= jobs.FAILS_TO_PAUSE  # 每一次都重试过，五次（不算重试）都没成才停


def test_a_closed_quota_window_holds_the_job_until_it_reopens(builtin, fake, pushes, own_world, retrying):
    clock = own_world
    j = user_job("main", when="every 1h", task="Write the morning brief.")
    reopen = T0 + timedelta(hours=5, minutes=20)
    fake([EngineError(f"Your Claude plan's usage limit is used up (You've hit your session limit · resets {reopen.isoformat()}): wait until it "
                      "resets", kind="quota")])
    since = last_id()
    t = at_next(clock, j)
    fire(t)
    k = jobs.get(j["id"])
    nxt = when.parse_iso(k["next_run"])
    assert nxt >= reopen and nxt - reopen < timedelta(hours=1) and k["retry"] is None  # 关着的那几次合成一次，挂到窗口之后的第一格
    assert "until" in runs(j["id"])[-1]["error"]
    o = user_job("main", when="in 30m", task="Call the dentist.")
    fake([EngineError("moonshot HTTP 429: You exceeded your current quota, retry after 7200s", kind="http", retryable=True, status=429)])
    t = at_next(clock, o)
    fire(t)
    k = jobs.get(o["id"])
    assert k["state"] == "on" and when.parse_iso(k["next_run"]) == t + timedelta(seconds=7200 + jobs.HOLD_SLACK)  # 一次的：窗口一过再来
    assert len(notes(since)) == 2 and all("until" in n for n in notes(since))  # 挂起来的现在就说一声（要等好几个小时）
    assert jobs.window_end("resets 3pm", datetime(2026, 10, 20, 9, 0, tzinfo=UTC)) == datetime(2026, 10, 20, 15, 0, tzinfo=UTC)
    assert jobs.window_end("resets 8am", datetime(2026, 10, 20, 9, 0, tzinfo=UTC)) == datetime(2026, 10, 21, 8, 0, tzinfo=UTC)
    assert jobs.window_end("Please try again in 20s", T0) == T0 + timedelta(seconds=20)
    assert jobs.window_end("nothing here", T0) is None


def test_a_sparse_job_gets_one_catch_up_run_after_the_window(builtin, fake, pushes, own_world, retrying):
    clock = own_world
    j = user_job("main", when="every day 9am", task="Write the morning brief.")
    since = last_id()
    t = at_next(clock, j)
    reopen = t + timedelta(hours=2)

    def closed() -> EngineError:
        return EngineError(f"Your Claude plan's usage limit is used up (You've hit your session limit · resets {reopen.isoformat()})", kind="quota")
    fake([closed(), closed()])
    fire(t)
    k = jobs.get(j["id"])
    back = k["retry"]["back"]
    assert k["retry"]["hold"] and when.parse_iso(k["next_run"]) == reopen + timedelta(seconds=jobs.HOLD_SLACK)  # 一天一次的：窗口一过补跑这一次
    assert when.parse_iso(back) - t > timedelta(hours=20) and len(notes(since)) == 1
    fire(at_next(clock, j))  # 补跑的那次还是关着：不再挂，等明天那一次，也不再说一遍
    k = jobs.get(j["id"])
    assert runs(j["id"])[-1]["kind"] == "retry" and k["retry"] is None and k["next_run"] == back and len(notes(since)) == 1


def test_chat_mode_jobs_are_not_retried_after_a_passing_problem(builtin, fake, pushes, own_world, retrying):
    clock = own_world
    j = user_job("main", when="every 1h", task="Ask the user how their day is going.", session="chat")
    fake([rate_limited(), {"text": "How's your day going?"}])
    t = at_next(clock, j)
    fire(t)
    k = jobs.get(j["id"])
    assert k["retry"] is None and when.parse_iso(k["next_run"]) == t - timedelta(seconds=1) + timedelta(hours=1)  # 对话里已经留了「自动触发」和没拿到回复：再来一遍就是两份
    fire(at_next(clock, j))
    assert jobs.get(j["id"])["retry"] is None and msgs("main")[-1]["text"] == "How's your day going?"


def test_one_shot_jobs_retry_a_few_times_then_finish(builtin, fake, pushes, own_world, retrying):
    clock = own_world
    o = user_job("main", when="in 30m", task="Remind the user to call the bank.")
    fake([rate_limited()] * 10)
    for _ in range(10):
        if jobs.get(o["id"])["state"] != "on":
            break
        fire(at_next(clock, o))
    k = jobs.get(o["id"])
    assert k["state"] == "done" and len(runs(o["id"])) == 1 + jobs.ONCE_RETRIES


# —— 只跑命令的：默认不重试，写了 retry 才重试 ————————————————————————————————————————————————

def test_command_jobs_retry_only_when_asked(builtin, server_json, own_world, tmp_path):
    clock = own_world
    script = tmp_path / "flaky.py"
    script.write_text("import sys\nsys.exit(3)\n", encoding="utf8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    server_json({"claw": {"kind": "builtin"}, "engine": {"model": "deepseek/deepseek-flash", "jobs": {"scripts": [str(script)]}}})
    plain = run(jobs.create("main", {"when": "every 1h", "command": {"script": str(script)}}, by_user=True))["job"]
    t = at_next(clock, plain)
    fire(t)
    k = jobs.get(plain["id"])
    assert runs(plain["id"])[-1]["status"] == "error" and k["retry"] is None and when.parse_iso(k["next_run"]) > t + timedelta(minutes=50)
    asked = run(jobs.create("main", {"when": "every 1h", "command": {"script": str(script), "retry": True}}, by_user=True))["job"]
    t = at_next(clock, asked)
    fire(t)
    k = jobs.get(asked["id"])
    assert k["command"]["retry"] is True and when.parse_iso(k["next_run"]) == t + timedelta(seconds=30) and k["retry"]["n"] == 1
    two = run(jobs.create("main", {"when": "every 1h", "command": {"script": str(script), "retry": 1}}, by_user=True))["job"]
    fire(at_next(clock, two))
    fire(at_next(clock, two))
    assert jobs.get(two["id"])["retry"] is None and len(runs(two["id"])) == 2  # 写了 1：只重试一次
    with pytest.raises(jobs.JobError):
        jobs.clean_command("main", {"script": str(script), "retry": "often"})
    assert os.access(script, os.X_OK)


def test_the_backoff_ladder_is_configurable(builtin, own_world):
    builtin({"jobs": {"retry": [10, "2m", "1h"]}})
    assert jobs.backoff() == [10, 120, 3600]
    builtin({"jobs": {"retry": False}})
    assert jobs.backoff() == []
    builtin()
    assert jobs.backoff() == []  # 不写 = 关
    builtin({"jobs": {"retry": True}})
    assert jobs.backoff() == [30, 60, 300, 900, 3600]
