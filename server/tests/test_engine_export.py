"""第 2 批：导出（engine/export.py，命令 /export 在 engine/commands.py，接口在 engine/history.py）：Markdown / JSON / HTML、带不带工具、
打码、挂成附件。会话是手写的（数据都是编的），不调模型。"""
from __future__ import annotations

import asyncio
import json

from engine import commands, export, history
from engine.session import DbSession, day_key, user

MODEL = "deepseek/deepseek-flash"
ON = {"claw": {"kind": "builtin"}, "engine": {"model": MODEL, "commands": True}}
KEY = "sk-abcdefghijklmnopqrstuvwx"


def run(coro):
    return asyncio.run(coro)


def today() -> str:
    import chat
    return chat.day_of(chat.now_iso())


def made(thread: str) -> DbSession:
    s = DbSession.create(day_key(thread, today()), kind="owner", agent="main", model=MODEL, system="SECRET SYSTEM PROMPT")
    s.append({**user(f"store my key {KEY} and check the <b>weather</b>"), "ref": 1, "at": "Mon 2026-10-19 09:00 UTC"})
    s.append({"role": "assistant", "content": [{"type": "thinking", "text": "private reasoning"}, {"type": "text", "text": "Checking."},
                                               {"type": "tool_call", "id": "c1", "name": "web_fetch", "args": {"url": "https://example.invalid", "token": KEY}}]})
    s.append({"role": "tool", "id": "c1", "name": "web_fetch", "content": [{"type": "text", "text": "Sunny, 21°C. " + "x" * 3000}], "error": False})
    s.append({"role": "system", "content": [{"type": "text", "text": "internal note"}]})
    s.append({"role": "assistant", "content": [{"type": "text", "text": "It's sunny. Your key is saved."}]})
    return s


def test_markdown_without_and_with_tools(server_json):
    server_json(ON)
    s = made("t-exp-md")
    body, name, mime = export.render("t-exp-md", s, "md")
    assert name == f"t-exp-md-{today()}.md" and mime == "text/markdown"
    assert "## You" in body and "## TestMousse" in body and "It's sunny. Your key is saved." in body
    assert KEY not in body and "***" in body  # 打码
    for hidden in ("SECRET SYSTEM PROMPT", "private reasoning", "internal note", "web_fetch", "Sunny"):
        assert hidden not in body  # 开头、思考、补的说明不导；不带工具时工具也不导
    full, _, _ = export.render("t-exp-md", s, "markdown", tools=True)
    assert "Called `web_fetch`:" in full and "## Tool result · web_fetch" in full and "Sunny, 21°C." in full
    assert KEY not in full and "x" * (export.RESULT_MAX + 10) not in full  # 参数里的也打码；结果截短


def test_json_and_html(server_json):
    server_json(ON)
    s = made("t-exp-js")
    data = json.loads(export.render("t-exp-js", s, "json", tools=True)[0])
    assert data["session"] == s.id and data["conversation"] == "t-exp-js" and [m["role"] for m in data["messages"]] == \
        ["user", "assistant", "tool", "assistant"]
    assert data["messages"][1]["calls"] == [{"name": "web_fetch", "args": {"url": "https://example.invalid", "token": "***"}}]
    page, name, mime = export.render("t-exp-js", s, "html")
    assert mime == "text/html" and name.endswith(".html") and page.startswith("<!doctype html>")
    assert "&lt;b&gt;weather&lt;/b&gt;" in page and "<b>weather</b>" not in page  # 都转义
    assert "http" not in page.split("<body>")[0].replace("https://example.invalid", "")  # 不连外面的东西


def test_the_command_attaches_a_file_and_the_endpoint_downloads(server_json):
    import chat
    server_json(ON)
    thread = "t-exp-cmd"
    made(thread)
    out = run(commands.handle(thread, "/export html tools"))
    assert out.ok and out.files and out.files[0]["name"].endswith(".html") and out.files[0]["mime"] == "text/html"
    bad = run(commands.handle(thread, "/export pdf"))
    assert not bad.ok and "pdf" in bad.text
    resp = history.export_endpoint(thread, format="json", tools=1)
    assert resp.media_type.startswith("application/json") and "attachment" in resp.headers["content-disposition"]
    assert json.loads(resp.body)["conversation"] == thread
    page = history.export_endpoint(thread, format="html")
    assert "sandbox" in page.headers["content-security-policy"]  # 浏览器里打开也在沙箱里
    # 走 app 的入口：回答那一行挂着附件
    from test_engine_commands import say
    done = run(say(thread, "/export"))[-1][1]
    assert done["attachments"][0]["name"].endswith(".md") and done["attachments"][0]["url"].startswith("/api/files/")
    hist = chat.history(thread)["messages"]
    assert hist[-1]["attachments"][0]["id"] == done["attachments"][0]["id"]
