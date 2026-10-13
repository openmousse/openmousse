#!/usr/bin/env python3
"""主对话把问题转给某个 Agent，等它答完把答案带回来。

走服务端 /api/chat/relay：问题记进那个 Agent 的线程（app 里显示成一行「主对话转来」灰字），
Agent 用自己的 skills 和记忆回答，回复入库、不推送，这里拿到全文。

用法：
  ask_agent.py <agent id> "问题"  [--timeout 180]
  ask_agent.py --list                 # 现在有哪些 Agent（id、名字、职责）
  ask_agent.py --meet "议题" <agent id> <agent id> … [--title 会议名] [--keep]
                                      # 发起会议（2026-10-13）：几个 Agent 一起商量，每轮每位发言一次，Grava 写纪要；
                                      # 立刻返回，不等开完。纪要写好后出现在主对话里，等用户确认（见 server/meetings.py）
退出码：0 有答案（会议：已发起）；2 超时或排队（Agent 还在答 / 在忙、问题排进了它的队列，稍后在它的线程里能看到）；1 其它错误。
输出给主对话的 agent 读，语言跟 server.json 的 language。
"""
from __future__ import annotations

import argparse
import sys
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mousse_common import L, api  # noqa: E402


def agents() -> dict[str, str] | None:
    """现在有哪些 Agent；服务连不上返回 None。"""
    try:
        sep = L("：", ": ")
        return {g["id"]: f"{g['name']}{sep}{g.get('purpose') or ''}" for g in api("/api/groups", timeout=10)["groups"]}
    except Exception:  # noqa: BLE001
        return None


def meet(topic: str, members: list[str], title: str | None, keep: bool) -> None:
    """发起会议：POST /api/meetings（origin main），马上返回。"""
    try:
        r = api("/api/meetings", {"topic": topic, "members": members, "title": title, "keep": keep, "origin": "main"}, timeout=30)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf8", "replace")[:300]
        if exc.code == 404:
            detail = L("服务器还没有会议功能（要更新 OpenMousse 服务器）", "The server doesn't have meetings yet (update the OpenMousse server)")
        print(L(f"会议没发起（HTTP {exc.code}）：{detail}", f"Couldn't start the meeting (HTTP {exc.code}): {detail}"))
        sys.exit(1)
    except (urllib.error.URLError, TimeoutError) as exc:
        print(L(f"会议没发起：{exc}", f"Couldn't start the meeting: {exc}"))
        sys.exit(1)
    m = r["meeting"]
    names = L("、", ", ").join(x["name"] for x in m["members"])
    print(L(f"已发起会议「{m['title']}」（{m['id']}）：{names}。每轮每个 Agent 发言一次，Grava 写纪要；纪要出来后会出现在主对话，等用户确认。"
            "不用等它开完，也不用转述每个 Agent 的话。",
            f"Started the meeting \"{m['title']}\" ({m['id']}): {names}. Each Agent speaks once per round and Grava writes the minutes; "
            "they will appear in the main chat for the user to confirm. Don't wait for it, and don't relay what each Agent says."))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("agent", nargs="?")
    ap.add_argument("question", nargs="?")
    ap.add_argument("--timeout", type=int, default=150)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--meet", metavar="TOPIC", help="发起会议：后面跟参会的 Agent id")
    ap.add_argument("--title")
    ap.add_argument("--keep", action="store_true", help="会后保留为群聊")
    a, rest = ap.parse_known_args()
    if a.meet:
        members = [x for x in [a.agent, a.question, *rest] if x and not x.startswith("-")]
        meet(a.meet, members, a.title, a.keep)
        return
    if rest:
        ap.error(f"unrecognized arguments: {' '.join(rest)}")
    known = agents()
    if a.list or not a.agent or not a.question:
        if known is None:
            print(L("服务没连上（~/.openmousse/server.json 的 bind 地址，systemctl --user status openmousse-server）",
                    "Can't reach the server (check the bind address in ~/.openmousse/server.json and systemctl --user status openmousse-server)"))
        elif not known:
            print(L("还没有 Agent。用户可以在 app 里新建，或者你用 agent-builder skill 建。",
                    "No Agents yet. The user can create one in the app, or you can create one with the agent-builder skill."))
        for k, v in (known or {}).items():
            print(f"{k}\t{v}")
        return
    if known and a.agent not in known:
        print(L(f"没有叫 {a.agent} 的 Agent。现在有：{', '.join(known) or '（无）'}",
                f"There is no Agent called {a.agent}. Current Agents: {', '.join(known) or '(none)'}"))
        sys.exit(1)
    try:
        r = api("/api/chat/relay", {"thread": a.agent, "text": a.question, "timeout": a.timeout}, timeout=a.timeout + 15)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf8", "replace")[:200]
        busy = exc.code == 409
        print(L(f"转不过去（HTTP {exc.code}）：{'这个 Agent 上一条还没回完，等一会再问' if busy else detail}",
                f"Couldn't hand it over (HTTP {exc.code}): {'this Agent is still answering its previous message, ask again in a moment' if busy else detail}"))
        sys.exit(1)
    except (urllib.error.URLError, TimeoutError) as exc:
        print(L(f"转不过去：{exc}", f"Couldn't hand it over: {exc}"))
        sys.exit(1)
    if r.get("status") == "queued":  # 那个 Agent 一直在忙（2026-10-05 起排队，不再 409）：轮到了自动转进去
        print(L(f"{a.agent} 正在回答别的问题，这个问题已排进它的队列（等了 {r.get('seconds')} 秒），轮到时自动转交；回答稍后在它的对话里。",
                f"{a.agent} is busy with another question. This one is queued (waited {r.get('seconds')} s so far) and goes in when it's free; "
                "the answer will show up in its thread."))
        sys.exit(2)
    if r.get("status") == "timeout":
        print(L(f"{a.agent} 还在答（{r.get('seconds')} 秒了）。已答的部分：\n{r.get('text') or '（还没有）'}\n完整回复稍后在它的线程里。",
                f"{a.agent} is still answering ({r.get('seconds')} s so far). What it has so far:\n{r.get('text') or '(nothing yet)'}\n"
                "The full reply will show up in its thread later."))
        sys.exit(2)
    if not r.get("ok"):
        print(L(f"{a.agent} 没答成：{r.get('error')}", f"{a.agent} couldn't answer: {r.get('error')}"))
        sys.exit(1)
    print(r["text"])


if __name__ == "__main__":
    main()
