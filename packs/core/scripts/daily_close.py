#!/usr/bin/env python3
"""日结：每天 03:45（用户时区）给今天有过对话的线程发「【自动触发】日结」，让各 agent 在 04:00 会话重置前把结论写进记忆。

- 线程：main + 现在所有的 Agent（服务的 /api/groups）+ 没归档的项目（/api/projects，发「日结（项目）」，按 project skill 更新项目卡）。
  当天（逻辑日 04:00 起）没有消息的线程跳过。最后调一次 /api/projects/review：截止都过了 3 天的项目问一次「归档？」（收件箱，静音）。
- 日结里顺带：今天新知道的关于用户的事按世界树规则写进树（memory-tree skill）。
- 都发完以后，今天只要有一个线程有过对话，就给 main 再发一条「日结提案」：它按 proposals skill 回看这一周的对话，
  值得固定下来的做法提成 skill / Agent 交进收件箱（每晚最多 2 条，多数时候没有）。main 还在回就每 30 秒再试，
  最多等 12 分钟，夜里那次最晚等到 03:58（04:00 会话重置，别挤进新的一天）。
- 走服务端 /api/chat/trigger（origin=auto，level=none：回完不推送）。上一条还没回完（409）→ 等 60 秒再试一次。
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
        "日结。按 AGENTS.md 的日结规则：把今天的结论写进 memory/今天.md 的「## 日结」和共享 digest，值得长期记住的进 MEMORY.md；"
        "今天新知道的关于用户本人的事实、偏好、决定、近况，按世界树规则写进树（memory-tree skill，先 recall 查重，没有就不写）。回一行「日结好了」。",
        "Daily digest. Follow the daily digest rules in AGENTS.md: write today's conclusions under \"## Daily digest\" in today's "
        "memory/YYYY-MM-DD.md and in the shared digest, and put anything worth keeping long-term into MEMORY.md; new facts, preferences, "
        "decisions and life updates about the user go into the memory tree per its rules (memory-tree skill; recall first, skip if nothing new). "
        "Reply with one line: \"Daily digest done\".",
    )


def project_text() -> str:
    return MARK + L(
        "日结（项目）。按 project skill 的「日结」：用 project_ctl.py 更新这个项目的进度、下一步和今天定的事，今天的要点追加到 "
        "memory/projects/<项目 id>.md（别写 memory/今天.md）。回一行「日结好了」。",
        "Daily digest (project). Follow the project skill's daily digest: update this project's progress, next steps and today's "
        "decisions with project_ctl.py, and append today's notes to memory/projects/<project id>.md (not today's memory file). "
        "Reply with one line: \"Daily digest done\".",
    )


def proposal_text() -> str:
    return MARK + L(
        "日结提案。按 proposals skill：跑 `python3 ~/.openmousse/repo/server/proposals_ctl.py context` 回看这一周的对话，"
        "有值得做成 skill 或 Agent 的就提（每晚最多 2 条，多数时候没有，不要硬凑）。回一行：提了什么，或者「今天没有要提的」。",
        "Nightly proposals. Follow the proposals skill: run `python3 ~/.openmousse/repo/server/proposals_ctl.py context` to look back over "
        "this week's conversations and propose what is worth turning into a skill or an Agent (at most 2 a night; most nights there is nothing, "
        "don't force it). Reply with one line: what you proposed, or \"Nothing to propose today\".",
    )


PROPOSAL_DEADLINE = (3, 58)  # 用户时区：夜里那次过了这个点还没发出去就今天不提（04:00 会话重置）


def propose_when_free() -> str:
    """给 main 发「日结提案」：它还在回（多半是刚才的日结），每 30 秒再试；最多等 12 分钟，夜里那次最晚等到 PROPOSAL_DEADLINE。"""
    start = user_now()
    limit = start + timedelta(minutes=12)
    reset = start.replace(hour=PROPOSAL_DEADLINE[0], minute=PROPOSAL_DEADLINE[1], second=0, microsecond=0)
    if start < reset:
        limit = min(limit, reset)
    while True:
        r = trigger("main", proposal_text())
        if r != "busy":
            return r
        if user_now() >= limit:
            return "skipped (main busy)"
        time.sleep(30)


def projects() -> list[str]:
    """没归档的项目。读不到（老服务端）就没有。"""
    try:
        return [p["id"] for p in api("/api/projects", timeout=20)["projects"]]
    except Exception:  # noqa: BLE001
        return []


def review() -> list[dict]:
    """截止都过了几天的项目：服务端问一次「归档？」。返回问了哪些。"""
    try:
        return api("/api/projects/review", {}, timeout=60).get("asked") or []
    except Exception as exc:  # noqa: BLE001
        return [{"error": str(exc)}]


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


def trigger(thread: str, text: str) -> str:
    try:  # 日结是给 agent 的，回完不推送（level none；notify false 给还不认识 level 的旧服务）
        api("/api/chat/trigger", {"thread": thread, "text": text, "origin": "auto", "level": "none", "notify": False}, timeout=20)
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
    projs = [] if a.thread else projects()
    for th in ([a.thread] if a.thread else threads() + projs):
        n = active_today(th, since)
        if not n:
            results.append({"thread": th, "result": "idle"})
            continue
        if a.dry_run:
            results.append({"thread": th, "result": f"would trigger ({n} msgs)"})
            continue
        text = project_text() if th in projs or th.startswith("sc-") else trigger_text()
        r = trigger(th, text)
        if r == "busy":
            time.sleep(60)
            r = trigger(th, text)
        results.append({"thread": th, "result": r, "msgs": n})
        time.sleep(5)
    if not a.dry_run and not a.thread:
        results.append({"review": review()})
        if any(r.get("msgs") for r in results):
            results.append({"proposals": propose_when_free()})
    line = f"{now.strftime('%Y-%m-%d %H:%M')} daily_close {json.dumps(results, ensure_ascii=False)}"
    if not a.dry_run:
        log = db_path().parent / "daily_close.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.open("a", encoding="utf8").write(line + "\n")
    print(line)


if __name__ == "__main__":
    main()
