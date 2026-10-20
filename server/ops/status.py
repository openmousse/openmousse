"""mousse status：一屏摘要（第 7 批，14.3，2026-10-19）。只读、不联网（服务器自己的 /api/health 除外）。

  mousse status [--json]

服务器在不在、版本和 commit、claw、主对话的模型、今天几轮花了多少（engine/usage.py 的汇总，按 server.json 的时区算日历日）、
定时任务几条（engine_jobs 里还在的：等你点开 / 开着 / 停着）、收件箱几张在等你点头、有没有新版本（只读上一次 mousse update --check
存下的结果）。库只读地打开，不建表。
"""
from __future__ import annotations

import contextlib
import json
import sqlite3

from i18n import L

from . import common as C


def _count(sql: str, args: tuple = ()) -> list[tuple]:
    """在 mousse.db 上只读地跑一句；没有库、没有表 → []。"""
    p = C.db_path()
    if not p.exists():
        return []
    try:
        conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error:
        return []
    try:
        return conn.execute(sql, args).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def jobs() -> dict[str, int]:
    rows = _count("SELECT state, COUNT(*) FROM engine_jobs WHERE state IN ('draft','on','paused') GROUP BY state")
    return {str(s): int(n) for s, n in rows}


def inbox_waiting() -> int:
    rows = _count("SELECT COUNT(*) FROM inbox WHERE status='pending' AND (IFNULL(expires_at, '') = '' OR julianday(expires_at) > julianday('now'))")
    return int(rows[0][0]) if rows else 0


def today() -> dict | None:
    """今天（按 server.json 的时区）几轮、花了多少：engine/usage.py 的汇总。读不了（依赖缺了）→ None。"""
    try:
        from engine import usage
        return usage.summary(1)["total"]
    except Exception:  # noqa: BLE001
        return None


def chat_model(cfg: dict) -> str:
    claw = cfg.get("claw") if isinstance(cfg.get("claw"), dict) else {}
    kind = str(claw.get("kind") or "openclaw").lower()
    if kind == "openclaw":
        return L("OpenClaw 管（openclaw.json）", "managed by OpenClaw (openclaw.json)")
    if kind == "openai":
        return L(f"你的 claw（{claw.get('name') or claw.get('url') or '?'}）管", f"managed by your claw ({claw.get('name') or claw.get('url') or '?'})")
    with contextlib.suppress(Exception):
        from engine import models
        return models.default_model() or L("还没选", "none chosen yet")
    e = cfg.get("engine") if isinstance(cfg.get("engine"), dict) else {}
    m = e.get("model")
    return str(m[0] if isinstance(m, list) and m else m or L("还没选", "none chosen yet"))


def gather() -> dict:
    cfg, problem = C.load_config()
    cfg = cfg or {}
    h = C.health(cfg)
    srv = h["data"].get("server") if h["up"] and isinstance(h["data"].get("server"), dict) else {}
    claw = cfg.get("claw") if isinstance(cfg.get("claw"), dict) else {}
    return {"server": {"up": h["up"], "url": h["url"], "status": h["status"], "version": srv.get("version"), "commit": srv.get("commit")},
            "repo": {"version": C.repo_version() or None, "commit": C.head_commit()},
            "config": problem, "claw": str(claw.get("kind") or "openclaw").lower(), "chatModel": chat_model(cfg),
            "today": today(), "jobs": jobs(), "inbox": inbox_waiting(), "update": C.update_state()}


def row(label: str, value: str) -> None:
    print(f"  {label}{' ' * max(1, 12 - width(label))}{value}")


def width(s: str) -> int:
    """终端里占几格（中文两格）。"""
    return sum(2 if ord(ch) > 0x2E7F else 1 for ch in s)


def show(s: dict) -> None:
    srv, repo = s["server"], s["repo"]
    print(f"OpenMousse {repo['version'] or '?'} ({repo['commit'] or '?'}) · claw {s['claw']}")
    if srv["up"]:
        stale = srv["commit"] and repo["commit"] and not (repo["commit"].startswith(srv["commit"]) or srv["commit"].startswith(repo["commit"]))
        line = L(f"在跑 · {srv['url']} · {srv['version'] or '?'}（{srv['commit'] or '?'}）", f"running · {srv['url']} · {srv['version'] or '?'} ({srv['commit'] or '?'})")
        if stale:
            line += L(" · 跑的是旧代码，重启一下", " · old code, restart it")
    elif srv["status"] in (401, 403):
        line = L(f"在，但令牌对不上（HTTP {srv['status']}）", f"up, but the token doesn't match (HTTP {srv['status']})")
    else:
        line = L(f"没在回话（{srv['url']}）：mousse doctor 看为什么", f"not answering ({srv['url']}): mousse doctor says why")
    row(L("服务器", "Server"), line)
    if s["config"] in ("broken", "unreadable"):
        row("server.json", L("读不了：mousse doctor", "unreadable: mousse doctor"))
    row(L("主对话", "Main chat"), s["chatModel"])
    t = s["today"]
    if t is None:
        tl = "?"
    else:
        tl = L(f"{t['turns']} 轮 · ${t['cost']:.4f}", f"{t['turns']} turns · ${t['cost']:.4f}")
        if t.get("planTurns"):
            tl += L(f"（{t['planTurns']} 轮走套餐）", f" ({t['planTurns']} on plans)")
    row(L("今天", "Today"), tl)
    j = s["jobs"]
    n = sum(j.values())
    jl = L(f"{n} 条", f"{n}") + (L(f"（{j.get('paused', 0)} 条停着）", f" ({j.get('paused', 0)} paused)") if j.get("paused") else "")
    if j.get("draft"):
        jl += L(f"，{j['draft']} 条等你点开", f", {j['draft']} waiting for your OK")
    row(L("定时任务", "Scheduled"), jl)
    row(L("收件箱", "Inbox"), L(f"{s['inbox']} 张等你点头", f"{s['inbox']} waiting for your OK"))
    u = s["update"]
    if not u:
        ul = L("没查过：mousse update --check", "not checked: mousse update --check")
    elif u.get("available"):
        ul = L(f"有新的 {u.get('target') or '?'}：mousse update", f"{u.get('target') or '?'} available: mousse update")
    else:
        ul = L(f"已经是最新的（{str(u.get('checkedAt') or '')[:10]} 查的）", f"up to date (checked {str(u.get('checkedAt') or '')[:10]})")
    row(L("更新", "Update"), ul)


def main(a) -> int:
    s = gather()
    if a.json:
        print(json.dumps(s, ensure_ascii=False, indent=2))
    else:
        show(s)
    return 0 if s["server"]["up"] else 1
