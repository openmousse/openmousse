"""双语：返回给 app 看的文字写成 L("中文", "English")。

每个请求的语言来自 app 发的 Accept-Language（zh 开头 → 中文，其他 → English）；没有这个头的调用（脚本、定时器、
网页版以外的工具）用 server.json 的 language。后台任务（对话回复、推送）继承发起请求时的语言。
给程序比较的枚举值（任务状态、餐次、tone 之类）不翻译，app 自己按语言显示。
"""
from __future__ import annotations

from contextvars import ContextVar, Token

from config import settings

_lang: ContextVar[str | None] = ContextVar("mousse_lang", default=None)


def parse(header: str | None) -> str | None:
    first = (header or "").split(",")[0].split(";")[0].strip().lower()
    if not first or first == "*":
        return None
    return "zh" if first.startswith("zh") else "en"


def use(header: str | None) -> Token:
    return _lang.set(parse(header))


def reset(token: Token) -> None:
    _lang.reset(token)


def lang() -> str:
    return _lang.get() or settings.language


def L(zh: str, en: str) -> str:
    return zh if lang() == "zh" else en
