#!/usr/bin/env python3
"""日结：每天 03:45（用户时区）给今天有过对话的线程发「【自动触发】日结」，让各 agent 在 04:00 会话重置前把结论写进记忆。

- 线程：main + 现在所有的 Agent（服务的 /api/groups）。当天（逻辑日 04:00 起）没有消息的线程跳过。
- 走服务端 /api/chat/trigger（origin=auto）。上一条还没回完（409）→ 等 60 秒再试一次。
- 日志：~/.openmousse/data/daily_close.log
用法：daily_close.py [--dry-run] [--thread <id>]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
import urllib.error
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mousse_common import L, api, db_path, user_now  # noqa: E402

MARK = "【自动触发】"  # 协议标记：app 靠这个前缀认出系统消息，AGENTS.md 的规则也按它判断，不翻译；后面的说明跟 server.json 的 language


def trigger_text() -> str:
    return MARK + L(
        "日结。按 AGENTS.md 的日结规则：把今天的结论写进 memory/今天.md 的「## 日结」和共享 digest，值得长期记住的进 MEMORY.md。回一行「日结好了」。",
        "Daily digest. Follow the daily digest rules in AGENTS.md: write today's conclusions under \"## Daily digest\" in today's "
        "memory/YYYY-MM-DD.md and in the shared digest, and put anything worth keeping long-term into MEMORY.md. "
        "Reply with one line: \"Daily digest done\".",
    )


def threads() -> list[str]:
    try:
        return ["main"] + [g["id"] for g in api("/api/groups", timeout=10)["groups"]]
    except Exception:  # noqa: BLE001
        return ["main"]


def active_today(thread: str, since_iso: str) -> int:
    db = db_path()
    if not db.exists():
        return 0
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return c.execute("SELECT count(*) FROM messages WHERE thread=? AND ts>=? AND role!='auto'", (thread, since_iso)).fetchone()[0]
    except sqlite3.OperationalError:
        return 0
    finally:
        c.close()


def trigger(thread: str) -> str:
    try:
        api("/api/chat/trigger", {"thread": thread, "text": trigger_text(), "origin": "auto"}, timeout=20)
        return "ok"
    except urllib.error.HTTPError as exc:
        return "busy" if exc.code == 409 else f"error {exc.code}"
    except (urllib.error.URLError, TimeoutError) as exc:
        return f"error {exc}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--thread", default="")
    a = ap.parse_args()
    now = user_now()
    since = (now - timedelta(hours=4)).replace(hour=4, minute=0, second=0, microsecond=0).isoformat()  # 当前逻辑日的 04:00
    results = []
    for th in ([a.thread] if a.thread else threads()):
        n = active_today(th, since)
        if not n:
            results.append({"thread": th, "result": "idle"})
            continue
        if a.dry_run:
            results.append({"thread": th, "result": f"would trigger ({n} msgs)"})
            continue
        r = trigger(th)
        if r == "busy":
            time.sleep(60)
            r = trigger(th)
        results.append({"thread": th, "result": r, "msgs": n})
        time.sleep(5)
    line = f"{now.strftime('%Y-%m-%d %H:%M')} daily_close {json.dumps(results, ensure_ascii=False)}"
    if not a.dry_run:
        log = db_path().parent / "daily_close.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.open("a", encoding="utf8").write(line + "\n")
    print(line)


if __name__ == "__main__":
    main()
