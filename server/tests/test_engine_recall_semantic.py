"""记忆检索按意思搜（engine/embeddings.py、semantic.py、recall_tree.py）：向量服务用假的（evals/fakeembed.py，Gemini 和 OpenAI 兼容
两种说法都测），不连真的接口、不花钱。索引、对话库都在 tmp 里（chat.DB 换成这个测试自己的：别的测试写进共用 mousse.db 的对话，按意思
搜不到这里来）。数据都是编的。"""
from __future__ import annotations

import asyncio
import contextlib
import json
import math
import os
import random
import sqlite3
import subprocess
import sys
import time
import types
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

import chat
import engine
from engine import embeddings, owner, recall, semantic, tools
from engine.session import MemorySession, user
from evals.fakeembed import FakeEmbedServer, cosine, vector
from test_engine import FakeModel

SERVER = Path(__file__).resolve().parent.parent
KEY = "fk-test-0123456789abcdef"
MODEL, DIMS = "fake-1", 48


def run(coro):
    return asyncio.run(coro)


def write(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf8")
    return p


def say(thread: str, role: str, text: str, ts: str = "2026-10-10T10:00:00+00:00") -> int:
    with chat._lock, chat.db() as conn:
        return conn.execute("INSERT INTO messages(thread, role, text, ts) VALUES(?,?,?,?)", (thread, role, text, ts)).lastrowid


def counts() -> dict:
    with recall._reader() as conn:
        return semantic.counts(conn)


def until(cond, timeout: float = 15.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


@pytest.fixture
def fe():
    with FakeEmbedServer(key=KEY) as f:
        yield f


@pytest.fixture
def sem(tmp_path, monkeypatch, server_json, fe):
    """主对话的工作区 + Agent rcfit 的；索引、对话库、档案都在 tmp_path。cfg(向量的配置, **engine.recall)："default" = 指向假服务的
    Gemini（不限速），None / False = 不开；extra = server.json 里别的段。"""
    from config import settings
    ws, fit = tmp_path / "ws", tmp_path / "fit"
    for d in (ws, fit):
        (d / "memory").mkdir(parents=True)
    monkeypatch.setattr(settings, "workspace", ws)
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(settings, "db", tmp_path / "mousse.db")
    monkeypatch.setattr(chat, "DB", tmp_path / "mousse.db")
    monkeypatch.setattr(settings, "profile", tmp_path / "profile" / "USER.md")
    monkeypatch.setenv("FAKE_EMBED_KEY", KEY)
    semantic.reset()

    def emb(backend: str = "gemini", **kw) -> dict:
        return {"backend": backend, "url": fe.gemini_url if backend == "gemini" else fe.openai_url, "key_env": "FAKE_EMBED_KEY",
                "model": MODEL, "dims": DIMS, "rpm": 0, **kw}

    def cfg(e="default", extra: dict | None = None, **rc) -> None:
        e = emb() if e == "default" else e
        rc = {"db": str(tmp_path / "recall.db"), "tree": False, **rc}
        if e is not None:
            rc["embeddings"] = e
        server_json({"agent_workspaces": {"rcfit": str(fit)}, "engine": {"recall": rc}, **(extra or {})})
    cfg()
    yield SimpleNamespace(ws=ws, fit=fit, tmp=tmp_path, cfg=cfg, emb=emb, fe=fe, settings=settings)
    semantic.stop()
    semantic.reset()


@pytest.fixture
def fake(monkeypatch):
    from engine import providers

    def make(script):
        f = FakeModel(script)
        monkeypatch.setattr(providers, "stream", f.stream)
        return f
    return make


# —— 不配 = 和以前一样 ——————————————————————————————————————————————————————————

def test_off_unless_configured_and_nothing_is_sent(sem, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-not-a-real-key-000000000000")  # 进程里有密钥也不算配了
    monkeypatch.setenv("OPENAI_API_KEY", "sk-not-a-real-key-0000000000")
    sent = []
    monkeypatch.setattr(embeddings, "_post", lambda *a, **k: sent.append(a))
    write(sem.ws / "MEMORY.md", "- 我们打算十二月去鲸歌岛度假 Fjordplan\n")
    for off in (None, False, {"backend": "gemini", "enabled": False}):
        sem.cfg(off)
        assert embeddings.spec() is None and not semantic.enabled() and semantic.status()["enabled"] is False
        f = recall.search("Fjordplan", "main")
        assert f.mode == "keyword" and f.hits and f.hits[0].match == ""
        assert "Nothing found" in recall.render(recall.search("旅行计划", "main"))  # 只按关键词：意思一样、字不一样就找不到
        ms = next(t for t in tools.for_agent("main") if t.name == "memory_search")
        assert "(keyword search, read-only)" in ms.description and ms.schema["properties"]["query"]["description"] == "A few keywords"
        assert "memory_search first (a few keywords, Chinese or English)" in owner.rules("main", tools.for_agent("main"))
    with recall._reader() as conn:
        assert [conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ("vec", "vec_bits", "leaves")] == [0, 0, 0]
        assert recall._meta(conn, "embed_model") is None
    assert not sent and sem.fe.count() == 0
    semantic.start()  # 后台线程：没配就不起
    semantic.kick()
    assert semantic._thread is None or not semantic._thread.is_alive()


def test_config_defaults_identity_and_mistakes(sem):
    sem.cfg("gemini")
    s = embeddings.spec()
    assert (s.backend, s.model, s.dims, s.key_env, s.url) == ("gemini", "gemini-embedding-001", 768, "GEMINI_API_KEY",
                                                             "https://generativelanguage.googleapis.com/v1beta")
    assert (s.batch, s.rpm, s.min_score, s.vector_weight) == (50, 30, 0.35, 0.7)
    sem.cfg("openai")
    s = embeddings.spec()
    assert (s.model, s.dims, s.key_env, s.url) == ("text-embedding-3-small", None, "OPENAI_API_KEY", "https://api.openai.com/v1")
    sem.cfg({"backend": "openai", "url": "http://127.0.0.1:11434/v1/", "model": "nomic-embed-text", "key_env": ""})  # 本机的 Ollama
    s = embeddings.spec()
    assert s.url == "http://127.0.0.1:11434/v1" and s.key() == "" and (s.query_prefix, s.doc_prefix) == ("search_query: ", "search_document: ")
    ids = []
    for change in ({}, {"batch": 7, "rpm": 3, "tpm": 1000, "timeout": 5, "query_timeout": 2}, {"model": "fake-2"}, {"dims": 32},
                   {"url": "http://127.0.0.1:9/v1beta"}, {"backend": "openai", "url": sem.fe.gemini_url}):
        sem.cfg({**sem.emb(), **change})
        ids.append(embeddings.spec().identity)
    assert ids[0] == ids[1] and len(set(ids)) == 5  # 批量、限速、超时不算；型号、维度、地址、后端换了就是另一套
    sem.cfg({"backend": "cohere"})
    assert embeddings.spec() is None and "backend" in embeddings.problem() and semantic.status()["problem"]
    sem.cfg({**sem.emb(), "dims": "lots", "batch": 0, "rpm": -1})  # 写错的数字用默认
    s = embeddings.spec()
    assert (s.dims, s.batch, s.rpm) == (768, 50, 30)


# —— 按意思找 ——————————————————————————————————————————————————————————————————

@pytest.mark.parametrize("backend", ["gemini", "openai"])
def test_finds_by_meaning_what_keywords_cannot(sem, backend):
    sem.cfg(sem.emb(backend))
    write(sem.ws / "MEMORY.md", "# 长期记忆\n\n- 我们打算十二月去鲸歌岛度假\n- 早上喝 Flatwhite\n")
    write(sem.ws / "memory" / "2026-10-02.md", "晚上失眠，三点才睡着\n")
    write(sem.ws / "memory" / "notes.md", "# 书单\n读完《纸船旅馆》第一部\n")
    recall.sync("main")
    st = semantic.backfill()
    assert st["embedded"] == 5 and not st["error"] and counts()["pending"] == 0
    f = recall.search("旅行计划", "main")
    assert f.mode == "hybrid" and f.hits[0].path == "MEMORY.md" and "鲸歌岛" in f.hits[0].text and f.hits[0].match == "meaning"
    assert not any("Flatwhite" in h.text or "纸船旅馆" in h.text for h in f.hits)  # 不相干的够不上 min_score
    text = recall.render(f)
    assert text.startswith("[1] MEMORY.md · line 3 · # 长期记忆 · by meaning") and "by meaning and keywords together" in text
    en = recall.search("sleep problems at night", "main")  # 换一种语言说
    assert en.hits and "失眠" in en.hits[0].text
    kw = recall.search("Flatwhite", "main")  # 所有词都对上的照样找得到，不标「按意思」
    assert kw.hits[0].match == "words" and "Flatwhite" in kw.hits[0].text
    assert "Nothing found" in recall.render(recall.search("钢琴课", "main")) and "searched by meaning and by keywords" in recall.render(recall.search("钢琴课", "main"))
    docs = [r for r in sem.fe.requests if r["kind"] == "docs"]
    q = [r for r in sem.fe.requests if r["kind"] == "query"]
    assert docs and q and all(r["auth"] for r in sem.fe.requests) and all(r["dims"] == DIMS for r in sem.fe.requests)
    assert {r["model"] for r in sem.fe.requests} == {MODEL}
    if backend == "gemini":
        assert {r["task"] for r in docs} == {"RETRIEVAL_DOCUMENT"} and {r["task"] for r in q} == {"RETRIEVAL_QUERY"}
    assert "notes · 书单\n# 书单\n读完《纸船旅馆》第一部" in sem.fe.texts("docs")  # 文件名、标题一起算
    assert "旅行计划" in sem.fe.texts("query")
    n = sem.fe.count("query")
    recall.search("旅行计划", "main")  # 同一句：内存里记着，不再算
    assert sem.fe.count("query") == n
    ms = next(t for t in tools.for_agent("main") if t.name == "memory_search")
    assert "by meaning and keywords together" in ms.description and ms.schema["properties"]["query"]["description"] == "A short phrase or a few keywords"
    assert "memory_search first (a short phrase or a few keywords, Chinese or English)" in owner.rules("main", tools.for_agent("main"))


def test_gemini_embedding_2_puts_the_task_in_the_text(sem):
    sem.cfg(sem.emb(model="gemini-embedding-2"))  # 这个型号不收 taskType（Google 的文档）
    write(sem.ws / "MEMORY.md", "- 我们打算十二月去鲸歌岛度假\n")
    recall.sync("main")
    semantic.backfill()
    recall.search("旅行计划", "main")
    assert {r["task"] for r in sem.fe.requests} == {None}
    assert sem.fe.texts("docs") == ["title: none | text: - 我们打算十二月去鲸歌岛度假"]
    assert sem.fe.texts("query") == ["task: search result | query: 旅行计划"]


def test_scope_dates_not_and_chats_by_meaning(sem):
    write(sem.fit / "MEMORY.md", "- 每周三去健身房练腿\n")
    write(sem.ws / "MEMORY.md", "- 预算：每个月存三千\n")
    mid = say("main", "user", "下个月想去鲸歌岛度假，先看看机票")
    other = say("rcfit", "user", "今天的深蹲练完了")
    recall.sync()
    semantic.backfill()
    main = recall.search("travel", "main")
    assert [h.root for h in main.hits] == ["chat"] and main.hits[0].path == str(mid)  # 别的 Agent 的工作区、对话按意思也搜不到
    assert "chat:main#" in recall.render(main)
    fit = recall.search("workout routine", "rcfit")
    assert {h.path for h in fit.hits} == {"MEMORY.md", str(other)} and all(h.root in ("ws.rcfit", "chat") for h in fit.hits)
    assert not recall.search("travel", "main", source="memory").hits  # source 一样管用
    assert not recall.search("travel", "main", before="2026-10-01").hits  # 日期一样管用（那条是 10-10 说的）
    assert not recall.search("travel", "main", exclude=("main", mid)).hits  # 还在上下文里的不搜
    assert not recall.search("度假 NOT 机票", "main").hits  # 大写的 NOT：按意思找到的也不能有这个词
    assert recall.search("vacation not planned yet", "main").hits  # 小写的 not 是话里的词


# —— 增量、换型号重建、关掉 ————————————————————————————————————————————————————————

def test_vectors_are_incremental_and_rebuilt_when_the_model_changes(sem, server_json):
    sem.cfg(db=None)  # 索引放在默认的地方：server.json 写坏了的时候也找得到同一个
    mem = write(sem.ws / "MEMORY.md", "- 我们打算十二月去鲸歌岛度假\n- 养了一只猫叫豆豆\n")
    trip = write(sem.ws / "memory" / "trip.md", "# 行程\n去鲸歌岛前要办签证\n")
    write(sem.ws / "memory" / "2026-10-03.md", "晚上失眠，三点才睡着\n")
    recall.sync("main")
    assert semantic.backfill()["embedded"] == 4 and len(sem.fe.texts("docs")) == 4
    assert semantic.backfill()["requests"] == 0  # 没变：一个请求都不发
    write(mem, "- 我们打算十二月去鲸歌岛度假\n- 养了一只猫叫豆豆\n- 下周面试一家新公司\n")
    recall.sync("main")
    st = semantic.backfill()
    assert sem.fe.texts("docs")[4:] == ["- 下周面试一家新公司"] and st["embedded"] == 1  # 没改的两条接着用旧的向量
    write(sem.tmp / "data" / "digest" / "main" / "2026-10-03.md", "晚上失眠，三点才睡着\n")  # 日结里一模一样的一段：不再算
    recall.sync("main")
    st = semantic.backfill()
    assert st["reused"] == 1 and st["requests"] == 0 and counts()["pending"] == 0
    before = counts()
    trip.unlink()  # 删掉的文件：向量一起删
    recall.sync("main")
    after = counts()
    assert after["vectors"] == before["vectors"] - 1 and after["chunks"] == before["chunks"] - 1
    sem.cfg(sem.emb(model="fake-2"), db=None)  # 换了型号：旧的向量整个删掉，重新算
    assert recall.sync("main")["removed"] >= after["vectors"] and counts()["vectors"] == 0
    assert recall.search("旅行计划", "main").mode == "hybrid"  # 重建期间照样能搜（关键词那一路 + 已经补上的）
    sem.fe.reset()
    semantic.backfill()
    assert counts() == after and {r["model"] for r in sem.fe.requests if r["kind"] == "docs"} == {"fake-2"}
    sem.cfg(sem.emb(model="fake-2", dims=24), db=None)  # 换维度也一样
    recall.sync("main")
    assert counts()["vectors"] == 0
    semantic.backfill()
    with recall._reader() as conn:
        assert {len(v) for (v,) in conn.execute("SELECT v FROM vec WHERE length(v) > 0")} == {24 * 4}
    for off in (None, "{ not json"):  # 配置拿掉了，或者 server.json 写坏了（读出来是空的）：不再用、不再发，算好的向量留着
        if off is None:
            sem.cfg(None, db=None)
        else:
            server_json(off)
        recall.sync("main")
        assert counts()["vectors"] == after["vectors"]
        f = recall.search("旅行计划", "main")
        assert f.mode == "keyword" and not f.hits
    sem.fe.reset()
    sem.cfg(sem.emb(model="fake-2", dims=24), db=None)  # 改好了、还是这一套：一段都不用重算
    recall.sync("main")
    assert semantic.backfill()["requests"] == 0 and sem.fe.count("docs") == 0
    assert recall.search("旅行计划", "main").hits[0].text == "- 我们打算十二月去鲸歌岛度假"
    recall.rebuild()  # reindex --full：索引连向量一起删掉从头建
    assert counts()["vectors"] == 0 and counts()["chunks"] == after["chunks"]


# —— 出错退回关键词 ————————————————————————————————————————————————————————————————

def test_errors_fall_back_to_keywords_and_show_in_status(sem, monkeypatch):
    write(sem.ws / "MEMORY.md", "- 我们打算十二月去鲸歌岛度假 Fjordplan\n")
    recall.sync("main")
    semantic.backfill()
    sem.fe.fail(500, times=5, only="query")
    f = recall.search("Fjordplan 旅行", "main")
    assert f.mode == "fallback" and f.hits and "500" in f.why
    assert recall.render(f).startswith("(Searching by meaning didn't work this time; these are keyword matches only.)")
    n = sem.fe.count("query")
    assert recall.search("鲸歌岛", "main").mode == "fallback" and sem.fe.count("query") == n  # 60 秒里不再试
    st = semantic.status()
    assert st["fallback"] and st["lastError"]["where"] == "query" and st["lastError"]["kind"] == "server"
    # 密钥不对（Gemini 回 400 API_KEY_INVALID）：算密钥的错；补向量的那一轮停下，一段都不标成「不算」
    semantic.reset()
    monkeypatch.setenv("FAKE_EMBED_KEY", "wrong-" + KEY)
    write(sem.ws / "memory" / "new.md", "新写的一段，还没有向量\n")
    recall.sync("main")
    st = semantic.backfill()
    assert st["error"] and st["embedded"] == 0 and st["requests"] == 1
    assert counts()["pending"] == 1 and counts()["skipped"] == 0
    assert semantic.status()["lastError"]["kind"] == "auth"
    # OpenAI 回 401、把收到的密钥原样写进错误里：状态里抹掉
    semantic.reset()
    sem.cfg(sem.emb("openai"))
    f = recall.search("鲸歌岛", "main")
    blob = json.dumps(semantic.status(), ensure_ascii=False)
    assert f.mode == "fallback" and "401" in blob and "wrong-" not in blob and KEY not in blob and "***" in blob
    # 没设密钥
    semantic.reset()
    monkeypatch.delenv("FAKE_EMBED_KEY")
    n = sem.fe.count()
    f = recall.search("鲸歌岛", "main")
    assert f.mode == "fallback" and "FAKE_EMBED_KEY" in f.why and sem.fe.count() == n  # 没密钥：根本不发
    # 服务连不上
    semantic.reset()
    monkeypatch.setenv("FAKE_EMBED_KEY", KEY)
    sem.cfg({**sem.emb(), "url": "http://127.0.0.1:9/v1beta", "query_timeout": 2})
    assert recall.search("鲸歌岛", "main").mode == "fallback" and semantic.status()["lastError"]["kind"] == "net"


def test_rate_limits_and_server_errors_are_retried_while_indexing(sem, monkeypatch):
    monkeypatch.setattr(semantic, "BACKOFF", 0.01)
    for i in range(3):
        write(sem.ws / "memory" / f"topic-{i}.md", f"第 {i} 个话题：去鲸歌岛度假的准备 {i}\n")
    recall.sync("main")
    sem.fe.fail(429, times=1, retry_after=0.3, only="docs")
    sem.fe.fail(503, times=2, only="docs")
    t0 = time.monotonic()
    st = semantic.backfill()
    assert st["embedded"] == 3 and st["requests"] == 4 and time.monotonic() - t0 >= 0.3 and not st["error"]
    assert semantic.status()["lastError"] is None  # 补完了：错误清掉
    # 对方不收其中一段（太长、内容审核）：它排在最前面也一样 —— 先拿一句不相干的试出配置没问题，再拆开发，只有那一段不算
    for name, text in (("a", "一段对方不收的话 " * 5), ("b", "另一段 能算的\n"), ("c", "第三段 也能算\n")):
        write(sem.ws / "memory" / f"{name}.md", text)
    recall.sync("main")
    sem.fe.reset()
    sem.fe.reject("不收", message="Input data may contain inappropriate content")
    st = semantic.backfill()
    assert st["refused"] == 1 and st["embedded"] == 2 and not st["error"] and counts()["pending"] == 0 and counts()["skipped"] == 1
    assert semantic.CANARY in sem.fe.texts("docs") and "inappropriate" in semantic.status()["lastError"]["message"]  # 状态里留着那一句
    # 整批都不收、那一句不相干的也不收：配置 / 额度的错（不是哪一段的），这一轮停下，一段都不标成「不算」
    write(sem.ws / "memory" / "d.md", "又写了一段新的\n")
    write(sem.ws / "memory" / "e.md", "再写了一段新的\n")
    recall.sync("main")
    sem.fe.reset()
    sem.fe.fail(400, times=99, only="docs", message="You exceeded your current quota, please check your plan")
    st = semantic.backfill()
    assert "quota" in st["error"] and st["refused"] == 0 and st["requests"] == 2 and counts()["pending"] == 2
    assert semantic.status()["lastError"]["kind"] == "bad"
    sem.fe.reset()
    assert semantic.backfill()["embedded"] == 2 and counts()["pending"] == 0  # 好了以后照样补上


# —— 世界树 ——————————————————————————————————————————————————————————————————

def test_tree_leaves_from_the_package_by_meaning_and_by_branch(sem, mousse_tree, monkeypatch):
    import sources
    import treelib
    from openmousse_tree import branches as B
    from openmousse_tree import store as S
    mousse_tree.make()
    monkeypatch.setattr(sources, "memory_tree", treelib)
    sem.cfg(tree=True)
    conn = treelib.connect()
    B.create(conn, "Body", about="Health")
    B.create(conn, "Fitness", parent="Body", about="Training", agents=["rcfit"])
    B.create(conn, "Strength", parent="Fitness", about="Lifting")
    legs = S.add(conn, text="Alex trains legs at the gym on Wednesdays", source="claude", branch="Strength")["id"]
    trip = S.add(conn, text="Alex plans a vacation on Whalesong Isle in December", source="owner", kind="event")["id"]
    rest = S.add(conn, text="Alex takes a rest day after every workout", source="rcfit", agent="rcfit", status="pending")["id"]
    conn.close()
    st = recall.sync()
    assert st["leaves"] == 3
    semantic.backfill()
    main = recall.search("travel plans", "main")
    assert main.hits[0].root == "tree" and main.hits[0].path == trip and main.hits[0].place == "Profile"
    text = recall.render(main)
    assert f"[1] tree:{trip} · memory tree · Profile" in text and "recorded by the user" in text and "by meaning" in text
    fit = recall.search("workout schedule", "rcfit")
    got = {h.path: h for h in fit.hits if h.root == "tree"}
    assert set(got) == {legs, rest} and got[legs].place == "Strength" and got[rest].status == "pending"
    assert "not confirmed yet" in recall.render(fit) and "recorded by Claude" in recall.render(fit)
    assert not [h for h in recall.search("travel plans", "rcfit").hits if h.root == "tree"]  # 主干上的不给功能 Agent
    assert "vacation on Whalesong Isle" in recall.get(f"tree:{trip}", "main")
    with pytest.raises(recall.RecallError):
        recall.get(f"tree:{trip}", "rcfit")
    assert "trains legs" in recall.get(f"tree:{legs}", "rcfit")
    for t in tools.for_agent("main"):
        if t.name == "memory_search":
            assert "leaves of the memory tree" in t.description and "added at the end" not in t.description
        if t.name == "memory_get":
            assert "tree:<leaf id>" in t.description
    import bridges

    async def no_tree_tool(*a, **k):
        raise AssertionError("the tree tool shouldn't be called: the leaves are in the index")
    monkeypatch.setattr(bridges, "run", no_tree_tool)
    monkeypatch.setattr(bridges, "command", lambda n: ["mousse-tree"])
    assert recall.has_tree("main") and run(recall._tree("travel", "main")) is None  # 叶子已经在索引里：不再另外附
    # 改了字：重算这一片；挪了枝：范围跟着变，向量接着用；忘掉：从索引里删掉
    conn = treelib.connect()
    S.edit(conn, trip, "Alex plans a holiday trip to Whalesong Isle in December", "owner")
    S.set_branch(conn, legs, "Body", "owner")
    S.set_status(conn, rest, "retracted", "owner")
    conn.close()
    sem.fe.reset()
    st = recall.sync()
    semantic.backfill()
    assert sem.fe.texts("docs") == ["Alex plans a holiday trip to Whalesong Isle in December"] and st["removed"] == 1
    assert not [h for h in recall.search("workout schedule", "rcfit").hits if h.root == "tree"]
    with recall._reader() as c:
        assert c.execute("SELECT count(*) FROM sources WHERE root='tree'").fetchone()[0] == 2
    sem.cfg(tree=False)  # 关掉：叶子从索引里清掉，不再进来
    recall.sync()
    with recall._reader() as c:
        assert c.execute("SELECT count(*) FROM leaves").fetchone()[0] == 0


def test_tree_leaves_carry_their_cues_and_source_into_the_index(sem, mousse_tree, monkeypatch):
    """「用得上的时候」进这一段的字（关键词和向量两路都搜得到），出处进 leaves 表；没有这两样的老叶子指纹不变（升级不重算）。"""
    import hashlib

    import sources
    import treelib
    from engine import recall_tree
    from openmousse_tree import store as S
    mousse_tree.make()
    monkeypatch.setattr(sources, "memory_tree", treelib)
    sem.cfg(tree=True)
    conn = treelib.connect()
    pasta = S.add(conn, text="Alex weighs pasta dry before cooking", source="claude", quote="I always weigh it dry", thread="chat-1",
                  cues=["how much is a bowl of noodles", "noodle calories"])["id"]
    jazz = S.add(conn, text="Alex likes jazz", source="claude")["id"]
    conn.close()
    recall.sync()
    semantic.backfill()
    assert "Alex weighs pasta dry before cooking (useful when: how much is a bowl of noodles; noodle calories)" in sem.fe.texts("docs")
    found = recall.search("noodle calories", "main")
    assert found.hits[0].root == "tree" and found.hits[0].path == pasta
    with recall._reader() as c:
        assert [r[0] for r in c.execute("SELECT c.source FROM chunks_fts f JOIN chunks c ON c.id = f.rowid WHERE chunks_fts MATCH 'calories'")]
        row = c.execute("SELECT l.quote, l.platform, l.thread, s.sha FROM leaves l JOIN sources s ON s.id = l.source WHERE s.path=?", (pasta,)).fetchone()
        old = c.execute("SELECT s.sha FROM sources s WHERE s.path=?", (jazz,)).fetchone()[0]
    assert tuple(row)[:3] == ("I always weigh it dry", "claude", "chat-1")
    leaf = dict(treelib.connect().execute("SELECT * FROM tree WHERE id=?", (jazz,)).fetchone())
    was = json.dumps([leaf["text"], leaf["tags"], leaf["kind"], leaf["status"], leaf["branch"], leaf["source"], leaf["observed_at"], None],
                     ensure_ascii=False)
    assert old == hashlib.sha1(was.encode()).hexdigest()  # 和 10/17 以前一样的指纹：老叶子不重算
    assert 'In their words: "I always weigh it dry"' in recall.get(f"tree:{pasta}", "main")
    md = sem.tmp / "TREE.md"  # 导出里的那一行也认得出「用得上的时候」
    md.write_text("## preference\n- Alex weighs pasta dry #food [claude 2026-10-09] (useful when: noodle calories; a bowl of pasta)\n",
                  encoding="utf8")
    got = recall_tree._from_export(md, None).leaves
    assert len(got) == 1 and got[0].cues == ("noodle calories", "a bowl of pasta") and got[0].tags == "food"


def _script_tree(tmp_path: Path, leaves: list[tuple], branches: list[dict], broken: bool = False) -> types.ModuleType:
    """作者那份 memory_tree.py 的样子：connect(sync=False) 给它的索引（tree 表）、branch_tree、TRUNK；broken = 读不了。"""
    db = tmp_path / "author-tree-index.db"
    with contextlib.closing(sqlite3.connect(db)) as c:
        c.execute("CREATE TABLE tree (id TEXT PRIMARY KEY, text TEXT, kind TEXT, tags TEXT, source TEXT, status TEXT, branch TEXT, "
                  "observed_at TEXT, created_at TEXT, supersedes TEXT, trust REAL)")  # trust：13d 以后的「信几分」
        c.executemany("INSERT INTO tree(id, text, kind, tags, source, status, branch, observed_at, created_at, trust) VALUES(?,?,?,?,?,?,?,?,?,?)",
                      [(*x, None)[:10] for x in leaves])
        c.commit()
    mod = types.ModuleType("memory_tree")
    mod.TRUNK = "档案"
    mod.__file__ = str(tmp_path / "scripts" / "memory_tree.py")

    def connect(sync: bool = True):
        if broken:
            raise RuntimeError("index is being rebuilt")
        c = sqlite3.connect(db)
        c.row_factory = sqlite3.Row
        return c
    mod.connect = connect
    mod.branch_tree = lambda conn: branches
    return mod


def test_tree_leaves_from_the_authors_script_and_its_export(sem, monkeypatch):
    import sources
    sem.cfg(tree=True)
    leaves = [("n1", "周末想去海边度假", "preference", "旅行", "claude", "active", None, "2026-09-01", "2026-09-01T10:00:00"),
              ("n2", "每天早上跑步五公里", "fact", "", "grava-rcfit", "active", "健身", "2026-09-20", "2026-09-20T08:00:00", 1.5),
              ("n3", "十月去看了牙医", "event", "健康", "owner", "active", "身体", "2026-10-05", "2026-10-05T09:00:00"),
              ("p1", "档案要点不进来", "profile", "", "profile", "active", None, "2026-10-01", "2026-10-01")]
    branches = [{"name": "人", "parent": "档案", "agents": ""}, {"name": "身体", "parent": "档案", "agents": ""},
                {"name": "健身", "parent": "身体", "agents": "rcfit"}]
    monkeypatch.setattr(sources, "memory_tree", _script_tree(sem.tmp, leaves, branches))
    st = recall.sync()
    assert st["leaves"] == 3
    semantic.backfill()
    main = recall.search("旅行", "main")
    assert main.hits[0].path == "n1" and main.hits[0].place == "档案" and main.hits[0].match == "words"  # 标签里有「旅行」
    run_hit = recall.search("exercise", "rcfit").hits[0]
    assert run_hit.path == "n2" and run_hit.trust == 1.5 and semantic.trust(run_hit, set()) == 1.5  # 13d 的口子：树里记着的分盖过几档
    assert not [h for h in recall.search("度假", "rcfit").hits if h.root == "tree"]
    owner = next(h for h in recall.search("牙医", "main").hits if h.root == "tree")
    assert owner.author == "owner" and semantic.trust(owner, set()) == semantic.TRUST["owner"]
    with recall._reader() as c:
        assert recall._meta(c, "tree_trunk") == "档案" and c.execute("SELECT path FROM roots WHERE key='tree'").fetchone()[0] == "script"
    # 平时读得了的那一份这会儿读不了（库正在重建）：旧的叶子先留着，不换成没有枝的导出，也不重算
    export = write(sem.tmp / "export" / "TREE.md", "# 世界树 TREE.md（mousse-tree 自动导出，勿手改）\n\n> 资料\n\n## preference\n"
                   "- 周末想去海边度假 #旅行 [claude 2026-09-01]\n\n## event\n- 十月去看了牙医 #健康 [owner 2026-10-05]\n")
    for d in "bcd":
        (sem.tmp / d).mkdir()
    monkeypatch.setattr(sources, "memory_tree", _script_tree(sem.tmp / "b", leaves, branches, broken=True))
    sem.cfg(tree=True, tree_md=str(export))
    recall.sync()

    def tree_rows():
        with recall._reader() as c:
            return (c.execute("SELECT path FROM roots WHERE key='tree'").fetchone()[0],
                    c.execute("SELECT count(*) FROM sources WHERE root='tree'").fetchone()[0], recall._meta(c, "tree_note"))
    origin, n, note = tree_rows()
    assert origin == "script" and n == 3 and "index is being rebuilt" in note
    assert recall.search("exercise", "rcfit").hits[0].path == "n2" and semantic.backfill()["requests"] == 0
    # 从来没读成过（新建的索引）：退回读 TREE.md 导出（没有 id 和枝：只给主对话）
    assert recall.rebuild()["leaves"] == 2
    semantic.backfill()
    origin, n, note = tree_rows()
    assert origin == f"export:{export}" and n == 2 and "index is being rebuilt" in note
    hit = recall.search("vacation by the sea", "main").hits[0]
    assert hit.root == "tree" and hit.path.startswith("md") and hit.text == "周末想去海边度假 #旅行"
    assert not [h for h in recall.search("vacation by the sea", "rcfit").hits if h.root == "tree"]
    # 又读得了：换回那一份，字一样的叶子接着用导出那时的向量，只算导出里没有的那片
    monkeypatch.setattr(sources, "memory_tree", _script_tree(sem.tmp / "c", leaves, branches))
    sem.fe.reset()
    recall.sync()
    semantic.backfill()
    assert tree_rows()[:2] == ("script", 3) and sem.fe.texts("docs") == ["每天早上跑步五公里"]
    # 什么都读不了（模块坏了、导出也没了）：旧的叶子先留着
    export.unlink()
    monkeypatch.setattr(sources, "memory_tree", _script_tree(sem.tmp / "d", leaves, branches, broken=True))
    recall.sync()
    assert tree_rows()[:2] == ("script", 3)


# —— 排序：新近、档案、主人自己写的、MMR ————————————————————————————————————————————

def test_ranking_prefers_recent_profile_and_own_leaves(sem):
    q = vector("trip", MODEL, DIMS)
    old_t, new_t = "鲸歌岛 旅行", "鲸歌岛 旅行 计划 安排 一下"
    assert cosine(q, vector(old_t, MODEL, DIMS)) > cosine(q, vector(new_t, MODEL, DIMS))  # 旧的那段本身更像
    today = date.fromisoformat(recall.today())
    write(sem.ws / "memory" / f"{(today - timedelta(days=90)).isoformat()}.md", old_t + "\n")
    write(sem.ws / "memory" / f"{(today - timedelta(days=2)).isoformat()}.md", new_t + "\n")
    recall.sync("main")
    semantic.backfill()
    hits = recall.search("trip", "main").hits
    assert [h.text for h in hits[:2]] == [new_t, old_t]  # 新的排前面
    # 进了档案的往前：档案里的那句没有笔记里的像，照样排前面
    write(sem.settings.profile, "- 鲸歌岛 旅行 计划 安排 一下\n")
    write(sem.ws / "memory" / "ideas.md", "鲸歌岛 旅行\n")
    for p in sem.ws.glob("memory/2026-*.md"):
        p.unlink()
    recall.sync("main")
    semantic.backfill()
    assert cosine(q, vector("ideas\n鲸歌岛 旅行", MODEL, DIMS)) > cosine(q, vector("- 鲸歌岛 旅行 计划 安排 一下", MODEL, DIMS))
    hits = recall.search("trip", "main").hits
    assert hits[0].root.startswith("x.") and hits[0].path == "USER.md" and hits[1].path == "memory/ideas.md"


def test_mmr_pushes_near_copies_down_and_duplicates_out(sem):
    def hit(i: int, text: str, score: float) -> recall.Hit:
        return recall.Hit(i, i, "ws.main", f"n{i}.md", None, None, None, None, 1, 1, "", text, score)
    a = hit(1, "鲸歌岛 旅行 计划 十二月 出发", 1.0)
    a2 = hit(2, "鲸歌岛 旅行 计划 十二月 机票 酒店", 0.95)  # 和 a 很像（Jaccard 0.67），但不是一模一样
    b = hit(3, "棉花糖镇 旅行 樱花 三月", 0.9)
    z = hit(4, "别的事", 0.1)
    assert [h.chunk for h in semantic.mmr([a, a2, b, z])] == [1, 3, 2, 4]
    assert [h.chunk for h in semantic.mmr([a, a2, b, z], lam=1.0)] == [1, 2, 3, 4]  # λ = 1：只看分数
    # 日记里一份、日结里一份一模一样的：只留一条
    write(sem.ws / "memory" / "2026-10-04.md", "晚上失眠，三点才睡着\n")
    write(sem.tmp / "data" / "digest" / "main" / "2026-10-04.md", "晚上失眠，三点才睡着\n")
    write(sem.ws / "memory" / "sleep.md", "# 睡眠\n睡前别看手机，十一点前睡觉\n")
    recall.sync("main")
    semantic.backfill()
    hits = recall.search("insomnia", "main").hits
    assert sum("三点才睡着" in h.text for h in hits) == 1 and any("十一点前睡觉" in h.text for h in hits)


# —— 粗筛 + 精算 ————————————————————————————————————————————————————————————————

def test_vector_prefilter_finds_the_same_nearest_chunks_as_brute_force(sem):
    rnd = random.Random(11)
    dims, n = 64, 4000
    centres = [[rnd.gauss(0, 1) for _ in range(dims)] for _ in range(40)]

    def unit(xs):
        s = math.sqrt(sum(x * x for x in xs))
        return [x / s for x in xs]
    vecs = [unit([c + rnd.gauss(0, 0.9) for c in centres[i % 40]]) for i in range(n)]
    with recall._writer() as conn:
        for i, v in enumerate(vecs):
            sid = conn.execute("INSERT INTO sources(root, path) VALUES('ws.main', ?)", (f"memory/v{i}.md",)).lastrowid
            cid = conn.execute("INSERT INTO chunks(source, start_line, end_line, text) VALUES(?,1,1,?)", (sid, f"v{i}")).lastrowid
            arr = embeddings.normalize(v)
            conn.execute("INSERT INTO vec(chunk, sha, v) VALUES(?,?,?)", (cid, f"s{i}", semantic.pack(arr)))
            conn.execute("INSERT INTO vec_bits(chunk, source, bits) VALUES(?,?,?)", (cid, sid, semantic.bits(arr)))
    for trial in range(5):
        q = unit([c + rnd.gauss(0, 0.9) for c in centres[trial * 7]])
        with recall._reader() as conn:
            got = semantic.vector_scores(conn, q, ["s.root = ?"], ["ws.main"], 10, set())
            ids = {r[0]: r[1] for r in conn.execute("SELECT c.id, c.text FROM chunks c")}
        brute = sorted(range(n), key=lambda i: -sum(a * b for a, b in zip(q, vecs[i], strict=True)))[:10]
        assert {ids[c] for c in got} == {f"v{i}" for i in brute}
        best = max(got.values())
        assert abs(best - sum(a * b for a, b in zip(q, vecs[brute[0]], strict=True))) < 1e-4  # 精算的是 float32 的余弦


# —— 后台建索引不卡回合 ——————————————————————————————————————————————————————————

def test_background_build_does_not_block_the_owner_turn(sem, fake):
    sem.cfg(sem.emb(batch=2), extra={"claw": {"kind": "builtin"}})
    for i in range(13):
        write(sem.ws / "memory" / f"topic-{i:02d}.md", f"第 {i} 个话题 Orcaplan{i} 的细节\n")
    write(sem.ws / "MEMORY.md", "- 我们打算十二月去鲸歌岛度假 Orcaplan\n")
    sem.fe.doc_delay = 0.8  # 7 批，一批 0.8 秒：整个补完要 5.6 秒
    recall.sync("main")
    semantic.start()
    semantic.kick()
    assert until(lambda: sem.fe.count("docs") >= 1, 10)
    t0 = time.monotonic()
    fake([{"calls": [("c1", "memory_search", {"query": "Orcaplan"})]}, {"text": "十二月去鲸歌岛。"}])
    spec = engine.Spec(kind="owner", model="deepseek/x", tools=tools.for_agent("main"), gate=tools.gate, agent="main", conversation="t-bg")
    res = run(engine.run_turn(spec, MemorySession(), user("Orcaplan 是什么？")))
    took = time.monotonic() - t0
    out = next(m for m in res.messages if m["role"] == "tool")["content"][0]["text"]
    assert res.status == "done" and took < 2.5 and "Orcaplan" in out  # 没等后台（它还要好几秒）
    st = semantic.status()
    assert st["running"] and st["worker"] and counts()["pending"] > 0  # 这一轮结束时后台还在一批批补
    assert until(lambda: counts()["pending"] == 0, 20)
    semantic.stop()
    assert not semantic._thread.is_alive()


def test_background_does_nothing_unless_the_claw_is_builtin(sem):
    write(sem.ws / "MEMORY.md", "- 我们打算十二月去鲸歌岛度假\n")
    recall.sync("main")
    semantic.start()  # claw 还是 OpenClaw（默认）：线程起了也什么都不做
    semantic.kick()
    time.sleep(0.4)
    assert sem.fe.count() == 0 and counts()["pending"] == 1
    semantic.stop()


# —— 状态 ————————————————————————————————————————————————————————————————————

def test_status_routes(sem):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from engine import api
    app = FastAPI()
    app.include_router(api.router)
    c = TestClient(app)
    write(sem.ws / "MEMORY.md", "- 我们打算十二月去鲸歌岛度假\n- 早上喝 Flatwhite\n")
    recall.sync("main")
    semantic.backfill()
    r = c.get("/api/engine/recall").json()
    assert r["ok"] and r["enabled"] and (r["backend"], r["model"], r["dims"]) == ("gemini", MODEL, DIMS) and r["key"] is True
    assert (r["vectors"], r["pending"], r["lastError"]) == (2, 0, None) and r["embeddedAt"] and r["syncedAt"] and r["indexedWith"]
    assert r["index"]["chunks"] == 2 and KEY not in json.dumps(r)
    brief = c.get("/api/engine/status").json()["recall"]
    assert brief["enabled"] and brief["vectors"] == 2 and brief["backend"] == "gemini"
    sem.cfg(None)
    off = c.get("/api/engine/recall").json()
    assert off["ok"] and off["enabled"] is False and "backend" not in off and off["pending"] == 0
    (sem.tmp / "recall.db").write_bytes(b"not a database" * 100)  # 索引坏了：状态照样给看
    assert c.get("/api/engine/recall").json()["ok"]


# —— 用量 ——————————————————————————————————————————————————————————————————————

def _usage_rows(db: Path) -> list[tuple] | None:
    """mousse.db 里记下的向量用量，一份一行（还没有这张表 → None）。"""
    with contextlib.closing(sqlite3.connect(db)) as c:
        try:
            return c.execute("SELECT kind, model, provider, requests, texts, tokens, estimated FROM engine_embed_usage ORDER BY kind, id").fetchall()
        except sqlite3.OperationalError:
            return None


def test_embedding_calls_go_into_usage_written_in_batches(sem, monkeypatch):
    """建索引、搜的那一句都记进用量：token 用接口回的（OpenAI 兼容的 usage、Gemini 的 usageMetadata），没回的按字数估、标成估的；价目里
    有的才算钱。算向量的时候只在内存里加一下，攒一批再写：看用量之前写，定的时间到了在后台线程里写。"""
    from engine import models, usage
    from evals.fakeembed import words
    usage.flush()  # 别的测试攒下的先写回它们自己的库，定好的那一次也取消
    monkeypatch.setattr(usage, "FLUSH_SECONDS", 3600)
    db = sem.tmp / "mousse.db"
    sem.cfg(sem.emb("openai", batch=2))
    for i in range(5):
        write(sem.ws / "memory" / f"topic-{i}.md", f"第 {i} 个话题：周末去海边度假的准备 {i}\n")
    recall.sync("main")
    assert semantic.backfill()["requests"] == 3  # 五段，一批两段
    assert recall.search("旅行计划", "main").mode == "hybrid"
    assert _usage_rows(db) is None  # 还攒在内存里：一行都没写
    docs = sum(len(words(t)) for t in sem.fe.texts("docs"))
    query = sum(len(words(t)) for t in sem.fe.texts("query"))
    s = usage.summary(1)["embeddings"]
    ref = f"custom/{MODEL}"  # 地址是本机的假服务：认不出是目录里的哪一家
    assert _usage_rows(db) == [("index", ref, None, 3, 5, docs, 0), ("query", ref, None, 1, 1, query, 0)]  # 同一天、同一种的加成一行
    assert s["total"] == {"requests": 4, "texts": 6, "tokens": docs + query, "estimatedTokens": 0, "cost": 0.0, "unpricedTokens": docs + query}
    assert s["byModel"] == [{"model": ref, "provider": None, "billing": "unpriced", **s["total"]}]
    assert [k["kind"] for k in s["byKind"]] == ["index", "query"] and len(s["byDay"]) == 1 and s["recordedSince"] == s["byDay"][0]["day"]
    monkeypatch.setitem(models.PRICES, ref, (1000.0, 0, 0, 0))  # 价目里有了：看的时候按那一份的日子算钱
    s = usage.summary(1)["embeddings"]
    assert s["total"]["cost"] == pytest.approx((docs + query) * 1000 / 1e6) and s["total"]["unpricedTokens"] == 0
    assert s["byModel"][0]["billing"] == "metered"
    # Gemini 回了 usageMetadata：照它记；没回：按字数估（embeddings.tokens），标成估的
    sem.cfg(sem.emb("gemini", model="fake-2"))
    recall.sync("main")
    sem.fe.reset()
    assert semantic.backfill()["embedded"] == 5
    reported = sum(len(words(t)) for t in sem.fe.texts("docs"))
    sem.fe.usage = False
    assert recall.search("周末旅行", "main").mode == "hybrid"
    estimated = sum(embeddings.tokens(t) for t in sem.fe.texts("query"))
    g = next(m for m in usage.summary(1)["embeddings"]["byModel"] if m["model"] == "custom/fake-2")
    assert g["tokens"] == reported + estimated and g["estimatedTokens"] == estimated and g["billing"] == "unpriced"
    # 定的时间到了：后台线程写，不用等谁来看用量
    monkeypatch.setattr(usage, "FLUSH_SECONDS", 0.05)
    n = len(_usage_rows(db))
    assert recall.search("海边", "main").mode == "hybrid"
    assert until(lambda: len(_usage_rows(db)) == n + 1, 5) and not usage._ebuf


# —— 密钥、地址写坏了、格式版本换了、名字 ————————————————————————————————————————————————————

def test_bad_keys_urls_and_schema_changes_never_break_search(sem, monkeypatch):
    write(sem.ws / "MEMORY.md", "- 我们打算十二月去鲸歌岛度假 Fjordplan\n")
    monkeypatch.setenv("FAKE_EMBED_KEY", KEY + "\n")  # Docker / k8s 的 secret 常带一个换行：去掉照常用
    recall.sync("main")
    assert semantic.backfill()["embedded"] == 1 and recall.search("旅行计划", "main").mode == "hybrid"
    for bad, mark in (("fk-test\nleaked-0123456789", "leaked-0123"), ("fk-测试-0123456789", "测试-0123")):  # 放不进请求头的：发之前就拦下
        semantic.reset()
        monkeypatch.setenv("FAKE_EMBED_KEY", bad)
        f = recall.search("鲸歌岛", "main")
        assert f.mode == "fallback" and "FAKE_EMBED_KEY" in f.why
        assert mark not in json.dumps(semantic.status(), ensure_ascii=False) + f.why
    semantic.reset()
    monkeypatch.setenv("FAKE_EMBED_KEY", KEY)
    sem.cfg(sem.emb(headers={"X-Team": "team-secret-0123\n"}))  # 额外的请求头一样
    f = recall.search("鲸歌岛", "main")
    assert f.mode == "fallback" and "X-Team" in f.why and "team-secret" not in f.why
    assert "abc123secret" not in embeddings.scrub("Illegal header value b'Bearer abc123secret\\n'", "abc123secret\n")
    semantic.reset()
    sem.cfg({**sem.emb(), "url": "http://[::1/v1beta"})  # 地址写坏了：退回关键词、记下来，不是整个搜出错
    f = recall.search("鲸歌岛", "main")
    assert f.mode == "fallback" and f.hits and semantic.status()["lastError"]["kind"] == "bad"
    # 出错以后歇着记在「这一套配置 + 密钥」上：改了密钥马上再试
    s = embeddings.spec()
    before = semantic._attempt(s)
    monkeypatch.setenv("FAKE_EMBED_KEY", KEY + "-new")
    assert semantic._attempt(s) != before
    # 格式版本换了（升级了 Python 的 unicodedata……）：向量表、叶子表跟着删掉重建，不会和新的 id 撞上
    monkeypatch.setenv("FAKE_EMBED_KEY", KEY)
    semantic.reset()
    sem.cfg(tree=True)
    import sources
    (sem.tmp / "t").mkdir()
    monkeypatch.setattr(sources, "memory_tree", _script_tree(sem.tmp / "t", [("n1", "周末想去海边度假", "preference", "旅行", "claude", "active",
                                                                             None, "2026-09-01", "2026-09-01T10:00:00")], []))
    recall.sync()
    semantic.backfill()
    with recall._writer() as c:
        c.execute("UPDATE meta SET v='0/old' WHERE k='version'")
        c.commit()
    monkeypatch.setattr(recall, "_wipe", lambda: pytest.fail("the index shouldn't need wiping"))
    recall.sync()
    with recall._reader() as c:
        assert [c.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ("leaves", "vec")] == [1, 0]
    assert semantic.backfill()["embedded"] == 2 and any(h.root == "tree" for h in recall.search("vacation by the sea", "main").hits)
    # 名字：按意思搜开着、世界树没关时 tree:<id> 是叶子，叫 tree 的共享项改叫 tree-2；世界树关了就不占这个名字
    shared = sem.tmp / "shared"
    shared.mkdir()
    real = recall.config
    monkeypatch.setattr(recall, "config", lambda: {**real(), "extra": [{"name": "tree", "path": str(shared)}]})
    assert [x.name for x in recall.extras()] == ["tree-2"]
    sem.cfg(tree=False)
    assert [x.name for x in recall.extras()] == ["tree"]


# —— 限速、分批 ——————————————————————————————————————————————————————————————————

def test_limiter_and_batches():
    now = [0.0]
    slept: list[float] = []

    def sleep(s: float) -> bool:
        slept.append(s)
        now[0] += s
        return False
    lim = embeddings.Limiter(clock=lambda: now[0], sleep=sleep)
    assert lim.wait(2, 0, 10) and lim.wait(2, 0, 10) and not slept
    assert lim.wait(2, 0, 10) and 59 <= slept[0] <= 61  # 第三个请求：等到一分钟以前的那个过期
    lim2 = embeddings.Limiter(clock=lambda: now[0], sleep=sleep)
    slept.clear()
    assert lim2.wait(0, 100, 80) and lim2.wait(0, 100, 500) and slept  # token 超了也等；一个本身就超的照发
    stop = embeddings.Limiter(clock=lambda: now[0], sleep=lambda s: True)
    assert stop.wait(1, 0, 1) and not stop.wait(1, 0, 1)  # 等的时候叫停了
    texts = ["a" * 10] * 5 + ["b" * (embeddings.REQUEST_BYTES - 5)]
    assert embeddings.batches(texts, 2) == [[0, 1], [2, 3], [4], [5]]
    assert embeddings.scrub(f"bad key {KEY} and sk-abcdefghijklmnop and AIzaSyA0123456789", KEY) == "bad key *** and *** and ***"


# —— 命令行 ——————————————————————————————————————————————————————————————————

def test_cli_embed_search_and_stats(tmp_path, fe):
    ws = tmp_path / "ws"
    write(ws / "MEMORY.md", "- 我们打算十二月去鲸歌岛度假\n")
    cfgp = tmp_path / "server.json"
    cfgp.write_text(json.dumps({"app_name": "T", "timezone": "UTC", "language": "en", "openclaw_home": str(tmp_path / "oc"), "workspace": str(ws),
                                "data_dir": str(tmp_path / "data"), "engine": {"recall": {"tree": False, "embeddings": {
                                    "backend": "openai", "url": fe.openai_url, "key_env": "FAKE_EMBED_KEY", "model": MODEL, "rpm": 0}}}}),
                    encoding="utf8")
    env = {**os.environ, "MOUSSE_SERVER_CONFIG": str(cfgp), "FAKE_EMBED_KEY": KEY}

    def cli(*args):
        return subprocess.run([sys.executable, str(SERVER / "engine" / "recall.py"), *args], capture_output=True, text=True, env=env, cwd=tmp_path,
                              timeout=60)
    out = cli("embed")
    assert out.returncode == 0, out.stderr
    assert "embedded=1" in out.stdout and "pending=0" in out.stdout and fe.count("docs") == 1
    s = cli("search", "旅行计划")
    assert s.returncode == 0 and "鲸歌岛" in s.stdout and "hybrid" in s.stderr
    st = cli("stats")
    assert "'vectors': 1" in st.stdout and "openai/fake-1" in st.stdout
