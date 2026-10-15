"""i18n.py：Accept-Language 解析、按请求 / 按配置选语言。"""
from __future__ import annotations

import pytest

import i18n
from config import settings


@pytest.mark.parametrize("header,expected", [
    ("zh-CN,zh;q=0.9,en;q=0.8", "zh"),
    ("zh", "zh"),
    ("ZH-tw", "zh"),
    ("zh-Hans;q=1", "zh"),
    ("  zh-CN  ", "zh"),
    ("en-US,en;q=0.9", "en"),
    ("fr-FR,zh;q=0.9", "en"),  # 只看第一个
    ("ja", "en"),
])
def test_parse(header, expected):
    assert i18n.parse(header) == expected


@pytest.mark.parametrize("header", [None, "", "   ", "*", ",zh", ";q=0.5"])
def test_parse_empty_or_wildcard(header):
    assert i18n.parse(header) is None


def test_lang_falls_back_to_settings(monkeypatch):
    monkeypatch.setattr(settings, "language", "zh")
    assert i18n.lang() == "zh"
    assert i18n.L("中", "en") == "中"
    monkeypatch.setattr(settings, "language", "en")
    assert i18n.L("中", "en") == "en"


def test_use_and_reset(monkeypatch):
    monkeypatch.setattr(settings, "language", "en")
    tok = i18n.use("zh-CN")
    try:
        assert i18n.lang() == "zh"
        assert i18n.L("中", "en") == "中"
    finally:
        i18n.reset(tok)
    assert i18n.lang() == "en"


def test_use_wildcard_header_uses_settings(monkeypatch):
    monkeypatch.setattr(settings, "language", "zh")
    tok = i18n.use("*")
    try:
        assert i18n.lang() == "zh"
    finally:
        i18n.reset(tok)


def test_nested_use_restores_outer(monkeypatch):
    monkeypatch.setattr(settings, "language", "en")
    outer = i18n.use("zh")
    inner = i18n.use("en-GB")
    assert i18n.lang() == "en"
    i18n.reset(inner)
    assert i18n.lang() == "zh"
    i18n.reset(outer)


def test_LS_ignores_request_language(monkeypatch):
    monkeypatch.setattr(settings, "language", "en")
    tok = i18n.use("zh")
    try:
        assert i18n.L("中", "en") == "中"
        assert i18n.LS("中", "en") == "en"
    finally:
        i18n.reset(tok)
    monkeypatch.setattr(settings, "language", "zh")
    assert i18n.LS("中", "en") == "中"
