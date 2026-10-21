"""档案（USER.md，L0）一条的格式：`- 内容 [标签]… YYYY-MM(-DD)`，标签说这一条从哪来（2026-10-20 起新写的用中性的标签）。

  data.py          /api/profile 读（split_tags：标签翻成名字给 app 看）；PUT /api/profile/{id} 改一条，写成「[U] 当天」
  settings_ctl.py  user-name 写的称呼那一行，也是「[U] 当天」

新写的只有 CONFIRMED（你确认过）。以前写的档案里还有 [L]（你确认过）、[Gr]（对话）：照样认，翻成和新标签一样的名字；
已有的档案文件不改写。[G] / [C] / [P] 是 Gemini / Claude / ChatGPT 的导出（只读，代码里没人写）。只用标准库（命令行也导入它）。
"""
from __future__ import annotations

import re

from i18n import L

CONFIRMED = "U"   # 你确认过（app 里改过的、命令行写的称呼）
CHAT = "Ch"       # 对话里说的
LEGACY = {"L": CONFIRMED, "Gr": CHAT}  # 以前的写法 → 现在的标签：读的时候一样对待，文件里的不改

TAIL = re.compile(r"\s*((?:\[[A-Za-z]+\])+)\s*(\d{4}-\d{2}(?:-\d{2})?)?\s*$")  # 末尾的标签和日子


def names() -> dict[str, str]:
    """标签 → 显示名（以前的写法翻成一样的名字）。按请求的语言，所以每次现取。"""
    out = {"G": L("Gemini 导出", "Gemini export"), "C": L("Claude 导出", "Claude export"), "P": L("ChatGPT 导出", "ChatGPT export"),
           CHAT: L("对话", "Chat"), CONFIRMED: L("你确认过", "Confirmed by you")}
    return {**out, **{old: out[new] for old, new in LEGACY.items()}}


def confirmed(day: str) -> str:
    """一条末尾的「你这一天确认过」：[U] YYYY-MM-DD。"""
    return f"[{CONFIRMED}] {day}"


def strip_tail(line: str) -> str:
    """去掉一行末尾的标签和日子（新旧写法都认）。"""
    m = TAIL.search(line)
    return line[: m.start()] if m else line
