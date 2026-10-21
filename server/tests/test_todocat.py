"""待办的类别（todocat.py）：谁说了算的顺序、按字面猜、校验。"""
from __future__ import annotations

import pytest

import todocat


@pytest.mark.parametrize("title, want", [
    ("寄回宽带路由器，不然扣押金", "life"),
    ("云主机：关掉旧机器 vps-demo-01 的自动续费", "life"),
    ("买音乐节门票", "life"),
    ("递交工签申请材料（月底截止）", "work"),
    ("办旅行签证", "life"),
    ("Group Assignment 2", "study"),
    ("请导师写推荐信", "study"),
    ("Acme internship interview", "work"),
    ("写商业计划书初稿", "work"),
    ("Book a dentist appointment", "life"),
])
def test_guess(title, want):
    assert todocat.guess(title) == want


@pytest.mark.parametrize("title, want", [
    ("Renew my licence at the DMV", "life"),
    ("交社保和公积金", "life"),
    ("H-1B petition paperwork", "work"),
    ("考研复习：第三章", "study"),
])
def test_guess_other_regions(title, want):
    # 词表不只认一个地方的说法
    assert todocat.guess(title) == want


def test_guess_word_boundaries():
    # 三个字母以内的英文词要整词：class 不算 cla（商标那类），bpm 不算 bp
    assert todocat.guess("class notes") == "study"
    assert todocat.guess("resting bpm") is None
    assert todocat.guess("") is None


def test_check():
    assert todocat.check("学习") == "study"
    assert todocat.check("Work") == "work"
    assert todocat.check(" life ") == "life"
    assert todocat.check("auto") is None
    assert todocat.check("") is None
    assert todocat.check(None) is None
    with pytest.raises(ValueError):
        todocat.check("engineering")


def entry(**kw):
    base = {"id": "item:x", "origin": "own", "title": "", "detail": "", "note": "", "by": None, "key": None, "category": None}
    return {**base, **kw}


def test_precedence():
    agents = {"fitness": "life", "apply": "work", "masters": "study"}
    # 你改过的 > 写入时给的 > 来源 > 字面
    assert todocat.of_entry(entry(category="work", origin="canvas"), "life", agents) == "life"
    assert todocat.of_entry(entry(category="work", origin="canvas"), None, agents) == "work"
    assert todocat.of_entry(entry(origin="canvas", title="job interview prep"), None, agents) == "study"
    assert todocat.of_entry(entry(origin="calendar"), None, agents) == "study"
    assert todocat.of_entry(entry(origin="apply", by="masters"), None, agents) == "study"
    assert todocat.of_entry(entry(origin="apply", by="apply"), None, agents) == "work"
    # Agent 排的按它的看板；主对话加的按幂等键的前缀
    assert todocat.of_entry(entry(by="fitness", title="训练 · Push A"), None, agents) == "life"
    assert todocat.of_entry(entry(by="main", key="apply:acme-written:owner-deadline", title="Acme 书面答题"), None, agents) == "work"
    # 都认不出：生活
    assert todocat.of_entry(entry(by="owner", title="一件说不清的事"), None, agents) == "life"


def test_annotate_marks_override():
    es = [entry(id="mail:a", origin="mail", title="账单到期"), entry(id="mail:b", origin="mail", title="面试确认")]
    todocat.annotate(es, {"mail:a": {"category": "work"}}, agents={})
    assert es[0]["category"] == "work" and es[0]["categoryAuto"] == "life" and es[0]["categorySet"] is True
    assert es[1]["category"] == "work" and es[1]["categorySet"] is False
