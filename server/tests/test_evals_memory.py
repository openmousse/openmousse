"""评测的写回题库（evals/suites/memory.json，13b / 13d）：20 道「该不该记」+ 5 道「外人想让它记一条」。题库本身对不对、标准答案过得了判法、
假模型整条路走一遍（只有内核那一路：写回不走 llm-task）、错的答案抓得住。全部离线。"""
from __future__ import annotations

import json
import re

import pytest

import config
from engine import loop, provider_openai
from evals import harness
from evals.fakemodel import FakeModelServer, Reply
from test_evals import GUARD_ENV, real_words, run

MEMORY = "memory"


def suite() -> dict:
    return harness.load_suite(MEMORY)


@pytest.fixture
def fake_llm(server_json, monkeypatch, request):
    """假模型接到内核的 fake/*（主人叫 Mira）→ FakeModelServer。和 test_evals 的 fake 一样，只是不用接 Gateway（写回不走 llm-task）。"""
    def make(script=None):
        fm = FakeModelServer(script).start()
        request.addfinalizer(fm.stop)
        server_json({"user_name": "Mira", "engine": {"providers": {"fake": {"api": "openai", "url": fm.openai_url, "key_env": ""}}}})
        monkeypatch.setattr(loop, "MODEL_RETRIES", (0, 0))
        monkeypatch.setattr(provider_openai, "RETRY_DELAYS", (0, 0))
        assert config.settings.app_name
        return fm
    return make


def responder(s: dict, wrong: dict | None = None):
    t = harness.target_of(s)
    table = {t.key_of_case(c): (wrong or {}).get(c["id"], c["gold"]) for c in s["cases"]}

    def respond(rec):
        return Reply(text=json.dumps(table[t.key_of_input(rec.get("input") or {})], ensure_ascii=False))
    return respond


def test_memory_suite_is_well_formed(server_json):
    server_json({"user_name": "Mira"})
    s = suite()
    t = harness.target_of(s)
    ids = [c["id"] for c in s["cases"]]
    assert len(ids) == len(set(ids)) == 25 and isinstance(t, harness.Writeback)
    assert sum(c.get("mode", "owner") == "owner" for c in s["cases"]) == 20 and sum(c.get("mode") == "outside" for c in s["cases"]) == 5
    assert len({t.key_of_case(c) for c in s["cases"]}) == len(ids)  # 离线回放靠它认题
    for c in s["cases"]:
        assert c["category"] in harness.CATEGORIES and c.get("note") and "gold" in c
        prompt, inp = t.build(s, c)
        assert t.key_of_input(inp) == t.key_of_case(c) and inp["rules"] and inp["branches"]
        assert ("[Them]" in inp["conversation"]) == (c.get("mode") == "outside")
        for p in [*c["expect"].get("remember", []), *c["expect"].get("forbid", []), *c["expect"].get("asked_items", [])]:
            re.compile(p)
        said = " ".join(m["text"] for m in c["conversation"])
        for x in c["gold"]["candidates"]:  # 标准答案的原话都真在对话里（不然服务器那一步会挡掉）
            assert x["quote"] in said, (c["id"], x["quote"])
    assert len({c["category"] for c in s["cases"]}) >= 5


def test_memory_suite_carries_nothing_real():
    real = real_words()
    if real is None:
        pytest.skip(f"{GUARD_ENV} isn't set to a readable list, so there's nothing to check against")
    hit = real.search(json.dumps(suite(), ensure_ascii=False))
    assert hit is None, f"something on the {GUARD_ENV} list shows up (character {hit.start()})"


def test_memory_gold_answers_pass_every_judge(server_json):
    server_json({"user_name": "Mira"})
    s = suite()
    t = harness.target_of(s)
    for c in s["cases"]:
        checks = t.judge(s, c, c["gold"])
        assert harness.passed(checks) and all(ck["ok"] for ck in checks), (c["id"], [ck for ck in checks if not ck["ok"]])


def test_memory_offline_run_through_the_fake_model(fake_llm, tmp_path):
    s = suite()
    fm = fake_llm(responder(s))
    rows = run(harness.run([s], ["builtin:fake/gold", "llm-task"], rows_path=tmp_path / "rows.jsonl"))
    assert len(rows) == len(s["cases"]) and all(r["arm"] == "builtin:fake/gold" and r["passed"] for r in rows)  # 写回不走 llm-task
    first = fm.of("openai")[0]["body"]
    assert first["response_format"] == {"type": "json_object"} and "tools" not in first  # 外人回合的写法：零工具
    md = harness.report(rows, [s])
    assert "该记" in md and "外人要它记" in md and "**25/25**" in md


def test_memory_wrong_answers_are_caught(fake_llm, tmp_path):
    s = suite()
    cases = {c["id"]: c for c in s["cases"]}

    def picked(cid: str, text: str, **kw) -> dict:
        line = next(m["text"] for m in cases[cid]["conversation"] if m["role"] in ("user", "them"))
        c = {"text": text, "kind": "fact", "about": "user", "branch": "", "quote": line, "cues": [], "durable": 2, "new": 2, "stated": 2,
             "supersedes": "", "why": "", **kw}
        return {"candidates": [c], "procedures": []}
    wrong = {
        "m11": picked("m11", "Mira 的银行卡密码是 4821"),               # 把密码记下了
        "m03": picked("m03", "Mira 的 Python 报了 KeyError"),          # 干活的过程
        "o02": picked("o02", "Mira 答应借 Sam 500 镑"),                # 外人要它记的，没标 asked
        "m01": {"candidates": [], "procedures": []},                    # 该记的没记
        "m16": {"candidates": [], "procedures": []},                    # 改做法没提
        "m18": {"candidates": [picked("m18", f"Mira 的第 {i} 件事")["candidates"][0] for i in range(5)], "procedures": []},
    }
    fake_llm(responder(s, wrong))
    rows = {r["case"]: r for r in run(harness.run([s], ["builtin:fake/gold"], rows_path=tmp_path / "rows.jsonl"))}
    failed = {k: sorted({ck["name"] for ck in r["checks"] if ck["core"] and not ck["ok"]}) for k, r in rows.items() if not r["passed"]}
    assert failed == {"m11": ["forbid", "nothing"], "m03": ["nothing"], "o02": ["asked"], "m01": ["remember"], "m16": ["procedure"],
                      "m18": ["remember"]}
    # m18：五条都对得上原话，服务器那一步最多挑 3 条（max 过了），可编的那五句一条都不是题里要的那几件事
    md = harness.report(list(rows.values()), [s])
    assert "`m11` 不该记" in md and "`o02` 外人要它记" in md
