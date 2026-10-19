"""待办的类别（todocat.py）：谁说了算的顺序、按字面猜、校验。"""
from __future__ import annotations

import pytest

import todocat


@pytest.mark.parametrize("title, want", [
    ("寄回宽带路由器，不然扣 £60", "life"),
    ("云主机：关掉旧机器 vps-demo-01 的自动续费", "life"),
    ("买演唱会门票（10/19、10/20、11/2）", "life"),
    ("递交高才通申请材料（月底截止）", "work"),
    ("办申根旅行签证", "life"),
    ("Group Assignment 2", "study"),
    ("推荐信：给导师发邮件，再回 5/23 那封", "study"),
    ("Acme quant internship interview", "work"),
    ("写 OpenMousse BP", "work"),
    ("Book a dentist appointment", "life"),
])
def test_guess(title, want):
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
