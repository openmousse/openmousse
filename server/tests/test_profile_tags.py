"""档案（USER.md）一条末尾的来源标签（profile_tags.py，2026-10-20）：新写的是 [U]（你确认过）；以前的 [L]、[Gr] 照样认、翻成一样的名字，
已有的文件不改写。解析（标签、日子、子行）+ 两处写入（app 里改一条、settings_ctl.py user-name）。"""
from __future__ import annotations

from datetime import date, datetime

import pytest

import config
import data
import profile_tags
import settings_ctl

PROFILE = """# USER.md

## 基本信息

- 称呼：Mira [L] 2026-09-02
- 住在 Wrenford，养了一只龟 [Gr][C] 2026-09
- 喜欢慢跑 [U] 2026-10-18
- 家人
  - 妹妹 Noor
  - 表哥 Alex
- 没有来源的一条

## 饮食

- 早上只喝燕麦奶 [P]
"""


@pytest.fixture
def profile(tmp_path, monkeypatch):
    p, hist = tmp_path / "USER.md", tmp_path / "profile-history.md"
    p.write_text(PROFILE, encoding="utf8")
    monkeypatch.setattr(data, "PROFILE", p)
    monkeypatch.setattr(data, "PROFILE_HISTORY", hist)
    monkeypatch.setattr(config.settings, "profile", p)
    monkeypatch.setattr(config.settings, "profile_history", hist)
    return p, hist


def test_split_tags_reads_old_and_new_tags_dates_and_sub_lines(profile):
    _, items = data.parse_bullets(profile[0])
    got = [data.split_tags(it["raw"]) for it in items]
    assert got[0] == ("称呼：Mira", ["L"], "2026-09-02")
    assert got[1] == ("住在 Wrenford，养了一只龟", ["Gr", "C"], "2026-09")
    assert got[2] == ("喜欢慢跑", ["U"], "2026-10-18")
    assert got[3] == ("家人\n妹妹 Noor\n表哥 Alex", [], None)  # 子行算同一条
    assert got[4] == ("没有来源的一条", [], None) and got[5] == ("早上只喝燕麦奶", ["P"], None)


def test_old_tags_read_as_the_same_names(profile):
    items = {x["text"].split("\n")[0]: x for x in data.profile()["items"]}
    assert items["称呼：Mira"]["sources"] == ["Confirmed by you"] == items["喜欢慢跑"]["sources"]  # [L] 和 [U]
    assert items["住在 Wrenford，养了一只龟"]["sources"] == ["Chat", "Claude export"]               # [Gr] 和 [Ch] 一样
    names = profile_tags.names()
    assert names["Gr"] == names["Ch"] and names["L"] == names["U"]


def test_editing_an_item_writes_the_new_tag_and_leaves_the_rest(profile):
    p, hist = profile
    _, items = data.parse_bullets(p)
    target = next(it for it in items if it["raw"].startswith("- 喜欢慢跑"))
    data.edit_profile(target["id"], data.ProfileEdit(text="喜欢慢跑，每周三次"))
    text = p.read_text(encoding="utf8")
    assert f"- 喜欢慢跑，每周三次 [U] {date.today().isoformat()}" in text
    assert "- 称呼：Mira [L] 2026-09-02" in text and "- 住在 Wrenford，养了一只龟 [Gr][C] 2026-09" in text  # 别的条目一个字不改
    assert "[L]" not in text.split("喜欢慢跑，每周三次")[1].split("\n")[0]
    assert "- 喜欢慢跑 [U] 2026-10-18" in hist.read_text(encoding="utf8")  # 旧的一版留底


def test_user_name_line_uses_the_new_tag_and_recognises_the_old_one(profile):
    p, hist = profile
    assert settings_ctl.write_profile("Mira") is None  # 已经是这个称呼（老标签 [L]）：不改
    assert "- 称呼：Mira [L] 2026-09-02" in p.read_text(encoding="utf8") and not hist.exists()
    settings_ctl.write_profile("Mimi")
    today = datetime.now(config.TZ).date().isoformat()
    lines = p.read_text(encoding="utf8").splitlines()
    new = [ln for ln in lines if settings_ctl.NAME_LINE.match(ln)]  # 写的时候按 server.json 的语言：「称呼：」或「Preferred name: 」
    assert len(new) == 1 and new[0].endswith(f"Mimi [U] {today}")
    assert "- 称呼：Mira [L] 2026-09-02" in hist.read_text(encoding="utf8")
    assert settings_ctl.write_profile("Mimi") is None  # 新标签也认得出
