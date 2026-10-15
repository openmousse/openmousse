"""push.py 里不发网络请求的部分：静默时段（时间换算）、档位、通知文字的裁剪和 Markdown 去除、collapseId、跳转目标。"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import push
from config import settings

UTC = ZoneInfo("UTC")


def at(h: int, m: int = 0) -> datetime:
    return datetime(2026, 1, 15, h, m, tzinfo=UTC)


@pytest.fixture(autouse=True)
def english(monkeypatch):
    monkeypatch.setattr(settings, "language", "en")


# —— _hm ————————————————————————————————————————————————————————————


@pytest.mark.parametrize("value,expected", [("07:30", (7, 30)), ("7:05", (7, 5)), (" 23:59 ", (23, 59)), ("00:00", (0, 0))])
def test_hm_valid(value, expected):
    assert push._hm(value) == expected


@pytest.mark.parametrize("value", ["24:00", "12:60", "7:5", "0730", "", None, "ab:cd", "123:00", 730])
def test_hm_invalid(value):
    assert push._hm(value) is None


# —— quiet_hours —————————————————————————————————————————————————————


def test_quiet_hours_default(server_json):
    assert push.quiet_hours() == ((23, 0), (7, 30))
    assert push.quiet_hours_text() == ("23:00", "07:30")


def test_quiet_hours_configured(server_json):
    server_json({"push": {"quiet_hours": ["22:15", "6:00"]}})
    assert push.quiet_hours() == ((22, 15), (6, 0))
    assert push.quiet_hours_text() == ("22:15", "06:00")


@pytest.mark.parametrize("q", [None, [], ""])
def test_quiet_hours_disabled(server_json, q):
    server_json({"push": {"quiet_hours": q}})
    assert push.quiet_hours() is None
    assert push.quiet_hours_text() is None


def test_quiet_hours_same_start_and_end_means_off(server_json):
    server_json({"push": {"quiet_hours": ["08:00", "08:00"]}})
    assert push.quiet_hours() is None


@pytest.mark.parametrize("q", [["23:00"], ["25:00", "07:00"], ["22:00", "7am"], "22:00-07:00", ["a", "b", "c"]])
def test_quiet_hours_malformed_falls_back_to_default(server_json, q):
    server_json({"push": {"quiet_hours": q}})
    assert push.quiet_hours() == ((23, 0), (7, 30))


def test_quiet_hours_push_not_a_dict(server_json):
    server_json({"push": "yes"})
    assert push.quiet_hours() == ((23, 0), (7, 30))


# —— in_quiet_hours / effective_level ————————————————————————————————


@pytest.mark.parametrize("h,m,quiet", [
    (22, 59, False), (23, 0, True), (0, 0, True), (3, 0, True), (7, 29, True), (7, 30, False), (12, 0, False),
])
def test_in_quiet_hours_across_midnight(server_json, h, m, quiet):
    assert push.in_quiet_hours(at(h, m)) is quiet


@pytest.mark.parametrize("h,m,quiet", [(12, 59, False), (13, 0, True), (13, 59, True), (14, 0, False), (0, 0, False)])
def test_in_quiet_hours_same_day_window(server_json, h, m, quiet):
    server_json({"push": {"quiet_hours": ["13:00", "14:00"]}})
    assert push.in_quiet_hours(at(h, m)) is quiet


def test_in_quiet_hours_disabled(server_json):
    server_json({"push": {"quiet_hours": None}})
    assert push.in_quiet_hours(at(3)) is False


@pytest.mark.parametrize("level,h,expected", [
    ("ring", 12, "ring"), ("ring", 2, "quiet"),
    ("quiet", 12, "quiet"), ("quiet", 2, "quiet"),
    ("none", 2, "none"), ("none", 12, "none"),
    (None, 12, "ring"), ("loud", 2, "quiet"), ("", 12, "ring"),
])
def test_effective_level(server_json, level, h, expected):
    assert push.effective_level(level, at(h)) == expected


# —— clip / collapse_id ——————————————————————————————————————————————


def test_clip():
    assert push.clip("short", 10) == "short"
    assert push.clip("exactly10!", 10) == "exactly10!"
    assert push.clip("hello world, long", 8) == "hello w…"
    assert push.clip("abc   def", 5) == "abc…"  # 截断处的空格去掉
    assert push.clip(None, 5) == ""
    assert push.clip("", 5) == ""


def test_collapse_id_short_key_unchanged():
    assert push.collapse_id("thread:main") == "thread:main"
    assert push.collapse_id("x" * 64) == "x" * 64


@pytest.mark.parametrize("key", ["x" * 65, "card:" + "a" * 200, "线程" * 40, "é" * 40])
def test_collapse_id_long_key(key):
    cid = push.collapse_id(key)
    assert len(cid.encode("utf8")) <= 64
    assert cid == push.collapse_id(key)  # 同一个 key 永远同一个 id
    assert "~" in cid


def test_collapse_id_distinguishes_long_keys_with_same_prefix():
    a, b = "p" * 80 + "a", "p" * 80 + "b"
    assert push.collapse_id(a) != push.collapse_id(b)


# —— with_target ——————————————————————————————————————————————————————


def test_with_target_thread_only():
    assert push.with_target({"thread": "main"}) == {"thread": "main", "target": {"type": "thread", "thread": "main"}}


@pytest.mark.parametrize("data", [None, {}, {"thread": ""}, {"thread": 5}, {"thread": "today"}])
def test_with_target_defaults_to_today(data):
    out = push.with_target(data)
    assert out["target"] == {"type": "today"}
    assert out["thread"] == "today"


def test_with_target_card_fills_thread():
    out = push.with_target({"target": {"type": "card", "id": 3, "thread": "helper"}})
    assert out["thread"] == "helper"


def test_with_target_inbox_thread_is_today():
    out = push.with_target({"target": {"type": "inbox", "id": 9}})
    assert out["thread"] == "today"


def test_with_target_unknown_type_rebuilt_from_thread():
    out = push.with_target({"thread": "main", "target": {"type": "spaceship"}})
    assert out["target"] == {"type": "thread", "thread": "main"}


def test_with_target_does_not_mutate_input():
    data = {"thread": "main", "extra": 1}
    push.with_target(data)
    assert data == {"thread": "main", "extra": 1}


# —— width / md_lines / cut / preview ——————————————————————————————————


@pytest.mark.parametrize("text,w", [("", 0), ("abc", 3), ("汉字", 4), ("ab汉字", 6), ("ｆｕｌｌ", 8), ("한글", 4)])
def test_width(text, w):
    assert push.width(text) == w


def test_md_lines_strips_markup():
    md = "# Title\n\n- **bold** item\n> quoted `code`\n1. [link](http://example.test) and ![img](x.png)\n***\nafter"
    assert push.md_lines(md) == ["", "Title", "", "", "bold item", "quoted code", "link and img", "", "after"]


@pytest.mark.xfail(reason="「---」分隔线先被表格分隔行的正则吃掉（直接 continue），走不到分隔线 = 分段那一支："
                          "分隔线前后两段会被 paragraphs() 接成一段", strict=True)
def test_md_lines_dash_rule_breaks_paragraph():
    assert push.md_lines("before\n---\nafter") == ["before", "", "after"]


def test_md_lines_without_headings():
    assert push.md_lines("# Title\nbody", headings=False) == ["", "body"]


def test_md_lines_table_and_code_block():
    md = "| a | b |\n|---|---|\n| 1 | 2 |\n```py\nprint(1)\n```\nend"
    assert push.md_lines(md) == ["1 · 2", "", "", "", "end"]


@pytest.mark.xfail(reason="表格分隔行要求每格至少两个 -：GFM 合法的 |:-:| 认不出，表头和「--- · :-:」都留在了预览里", strict=True)
def test_md_lines_table_single_dash_alignment():
    assert push.md_lines("| a | b |\n|---|:-:|\n| 1 | 2 |") == ["1 · 2"]


def test_md_lines_keeps_snake_case_and_handles_italics():
    assert push.md_lines("use snake_case_name and *italic* and _also_") == ["use snake_case_name and italic and also"]


def test_md_lines_unclosed_code_block_dropped():
    assert push.md_lines("before\n```\nnever closed") == ["before", "", "", ""]


def test_cut_short_text_unchanged():
    assert push.cut("Hello there.", 110) == "Hello there."


def test_cut_prefers_sentence_end():
    text = "First sentence here. Second sentence is much longer and keeps going on and on."
    assert push.cut(text, 40) == "First sentence here."


def test_cut_cjk_counts_double_width():
    text = "一二三四五六七八九十。" + "一二三四五六七八九十" * 3
    out = push.cut(text, 30)
    assert out == "一二三四五六七八九十。"
    assert push.width(out) <= 30


def test_cut_falls_back_to_clause_then_hard_cut():
    assert push.cut("alpha beta gamma delta epsilon zeta eta theta", 20) == "alpha beta gamma…"
    hard = push.cut("a" * 50, 20)
    assert hard == "a" * 19 + "…"
    assert push.width(hard) <= 20


def test_preview_basic():
    assert push.preview("# Title\n\nSome **bold** text and [a link](http://example.test).") == "Title · Some bold text and a link."


def test_preview_empty_never_blank():
    assert push.preview("") == "Reply ready."
    assert push.preview(None) == "Reply ready."
    assert push.preview("   \n\n ") == "Reply ready."


def test_preview_code_only_falls_back_to_raw_text():
    assert push.preview("```\nls -la\n```") == "ls -la"


def test_preview_respects_limit():
    long = " ".join(["word"] * 100)
    out = push.preview(long, 50)
    assert 0 < push.width(out) <= 50


def test_preview_chinese_language(monkeypatch):
    monkeypatch.setattr(settings, "language", "zh")
    assert push.preview("") == "回复好了。"


# —— _num ————————————————————————————————————————————————————————————


@pytest.mark.parametrize("v,expected", [(3, "3"), (3.0, "3"), ("4.50", "4.5"), (0.26, "0.3"), (-1, "-1"), ("x", "x"), (None, "None")])
def test_num(v, expected):
    assert push._num(v) == expected
