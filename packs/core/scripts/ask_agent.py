#!/usr/bin/env python3
"""主对话把问题转给某个 Agent，等它答完把答案带回来。

走服务端 /api/chat/relay：问题记进那个 Agent 的线程（app 里显示成一行「主对话转来」灰字），
Agent 用自己的 skills 和记忆回答，回复入库、不推送，这里拿到全文。

用法：
  ask_agent.py <agent id> "问题"  [--timeout 180]
  ask_agent.py --list                 # 现在有哪些 Agent（id、名字、职责）
退出码：0 有答案；2 超时（Agent 还在答，稍后在它的线程里能看到）；1 其它错误。
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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("agent", nargs="?")
    ap.add_argument("question", nargs="?")
    ap.add_argument("--timeout", type=int, default=150)
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
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
