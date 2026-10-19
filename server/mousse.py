#!/usr/bin/env python3
"""mousse：OpenMousse 的命令行（2026-10-19）。现在只有一个子命令：

  mousse agent --agent <id> [--model provider/model] --message-file <文件或 -> [--timeout 秒] [--server 地址] [--token 令牌]

跟某个 Agent 说一句、拿回结果：从外面（比如另一个编码助手的会话里）找它商量，原来用 `openclaw agent` 的地方换成这个。
每次一个新会话、只读的工具（查得到、改不了），回答、用了什么、多少 token 打成一个 JSON 打到标准输出（出错也是 JSON，带 error）。
  --agent         main 或某个 Agent 的 id（app 里 Agent 的设置页、python3 agent_ctl.py list 看得到）
  --model         不写 = 那个 Agent 在 app 里选的模型（没选 = engine.model）；chatgpt/<型号> 走你自己的 ChatGPT 套餐（命令行是你自己发起的，
                  算你自己的回合；没连上、没开用套餐就报错说清楚，型号照 engine.chatgpt.models，不写用你账号自己的模型目录）
  --message-file  要说的话从这个文件读；- = 从标准输入读
走服务器的接口 POST /api/engine/agent（engine/agent_cli.py），claw 要是 builtin（自己的内核）。令牌照 *_ctl.py：本机用 server.json 里
第一把能走 /api 的；--token 或环境变量 MOUSSE_TOKEN 换成 app 的令牌（python3 tokens.py add <名字>）。地址照 server.json 的 bind；
--server 或 MOUSSE_SERVER 换成别的（比如在另一台电脑上：http://<服务器>:8080）。
退出码：0 答了；1 没答成（JSON 的 error 说为什么）；2 参数不对、读不了要说的话。
Agent 自己跑的命令里（环境里有 MOUSSE_AGENT，主机命令 engine/host_exec.py 会给）不跑：照 OpenClaw 的 openclaw agent，那样会丢掉是谁转的、
绕过转交的跳数；Agent 之间转话用 handoff（engine/sessions.py）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

from config import settings
from i18n import L, lang

TIMEOUT = 600  # 默认最多等这一轮多少秒（服务器那边最多 1800）


def description() -> str:
    """--help 的说明，按 server.json 的 language。"""
    return L(__doc__, """mousse: OpenMousse's command line (2026-10-19). One command for now:

  mousse agent --agent <id> [--model provider/model] --message-file <file or -> [--timeout seconds] [--server URL] [--token TOKEN]

Say one thing to an Agent and get the result back: ask it from outside (from another coding assistant's session, say), where you used
`openclaw agent` before. Each call is a fresh session with read-only tools (it can look things up, not change anything); the answer, what
it used and how many tokens are printed as one JSON object on standard output (errors too, with error).
  --agent         main or an Agent's id (shown on the Agent's settings page in the app, or by python3 agent_ctl.py list)
  --model         left out = the model picked for that Agent's chat in the app (none picked = engine.model); chatgpt/<model> runs on your
                  own ChatGPT plan (the command line is you, so it counts as your own turn; if the plan isn't connected or plan use isn't
                  allowed it says so; models follow engine.chatgpt.models, or your account's own catalog when that isn't set)
  --message-file  read what to say from this file; - = standard input
Goes through the server's POST /api/engine/agent (engine/agent_cli.py); the claw must be builtin (OpenMousse's own engine). The token
works like the *_ctl.py scripts: on the server itself the first token in server.json that may use /api; --token or MOUSSE_TOKEN for an
app token (python3 tokens.py add <name>). The address is server.json's bind; --server or MOUSSE_SERVER for another one (from another
computer: http://<server>:8080).
Exit codes: 0 answered; 1 not answered (the JSON's error says why); 2 bad arguments or the message couldn't be read.
It doesn't run inside an Agent's own command (MOUSSE_AGENT in the environment, which host commands get): like OpenClaw's openclaw agent,
that would lose who handed what over and skip the handoff hop limit. Agents hand over to each other with handoff.
""")


def read_message(path: str) -> str:
    """--message-file：- = 标准输入。读不了、空的 → 退出码 2（照样打一个 JSON）。"""
    try:
        text = sys.stdin.read() if path == "-" else Path(path).expanduser().read_text(encoding="utf8")
    except (OSError, UnicodeDecodeError) as e:
        fail(L(f"读不了 {path}：{e}", f"Can't read {path}: {e}"), "message_unreadable", 2)
    if not text.strip():
        fail(L("要说的话是空的", "The message is empty"), "empty_message", 2)
    return text


def fail(error: str, code: str, exit_code: int = 1) -> None:
    print(json.dumps({"ok": False, "code": code, "error": error}, ensure_ascii=False, indent=2))
    sys.exit(exit_code)


def server_url(given: str | None) -> str:
    url = (given or os.environ.get("MOUSSE_SERVER") or "").strip().rstrip("/")
    if url:
        return url if "://" in url else f"http://{url}"
    host = settings.host if settings.host not in ("0.0.0.0", "::", "") else "127.0.0.1"
    return f"http://{host}:{settings.port}"


def ask(base: str, token: str | None, body: dict, timeout: float) -> dict:
    """POST /api/engine/agent → 回的 JSON。HTTP 出错的照样读出 JSON（服务器说了为什么）；连不上 → {"ok": false, …}。"""
    headers = {"Accept": "application/json", "Content-Type": "application/json", "Accept-Language": "zh-CN" if lang() == "zh" else "en"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if os.environ.get("MOUSSE_VIA"):  # 经 /mcp、内核的工具跑的：服务器据此认能不能花 ChatGPT 套餐（bridges.py）
        headers["X-Mousse-Via"] = os.environ["MOUSSE_VIA"]
    req = urllib.request.Request(f"{base}/api/engine/agent", data=json.dumps(body, ensure_ascii=False).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 — 用户自己的服务器
            return json.loads(r.read().decode("utf8"))
    except urllib.error.HTTPError as e:
        try:
            out = json.loads(e.read().decode("utf8"))
        except ValueError:
            out = {}
        if not isinstance(out, dict):
            out = {}
        if "ok" not in out:  # FastAPI 自己的错（401 令牌不对、422 参数）：{"detail": …} 或 {"error": …}
            detail = out.get("detail") if out.get("detail") is not None else out.get("error")
            out = {"ok": False, "code": f"http_{e.code}", "error": detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False)
                   if detail else f"HTTP {e.code}"}
        return {**out, "httpStatus": e.code}
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        why = getattr(e, "reason", None) or e
        return {"ok": False, "code": "unreachable", "error": L(f"连不上服务器 {base}：{why}", f"Can't reach the server at {base}: {why}")}
    except ValueError:
        return {"ok": False, "code": "bad_reply", "error": L("服务器回的不是 JSON", "The server's reply wasn't JSON")}


def agent(a: argparse.Namespace) -> int:
    if os.environ.get("MOUSSE_AGENT"):  # 照 OpenClaw：Agent 自己跑的命令里（主机命令带着 MOUSSE_AGENT）调它，会丢掉是谁转的、绕过转交的跳数
        fail(L("mousse agent 是给你自己（或者你的编码助手）用的；在 Agent 自己跑的命令里用，会丢掉是谁转的、绕过转交的跳数。Agent 之间转话用 handoff。",
               "mousse agent is for you (or your coding assistant). From inside an Agent's own command it would lose who handed what over "
               "and skip the handoff hop limit; Agents hand over with handoff."), "inside_agent", 2)
    message = read_message(a.message_file)
    body = {"agent": a.agent, "message": message, "timeout": a.timeout}
    if a.model:
        body["model"] = a.model
    token = (a.token or os.environ.get("MOUSSE_TOKEN") or "").strip() or settings.api_token()
    out = ask(server_url(a.server), token, body, a.timeout + 60)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out.get("ok") else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="mousse", description=description(), formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("agent", help=L("跟某个 Agent 说一句、拿回结果（JSON）", "Say one thing to an Agent and get the result (JSON)"))
    p.add_argument("--agent", required=True, help=L("main 或 Agent 的 id", "main or an Agent's id"))
    p.add_argument("--model", help=L("provider/model；不写 = 那个 Agent 的", "provider/model; left out = the Agent's own"))
    p.add_argument("--message-file", required=True, help=L("要说的话在哪个文件；- = 标准输入", "File with the message; - = standard input"))
    p.add_argument("--timeout", type=int, default=TIMEOUT, help=L("最多等多少秒", "Seconds to wait at most"))
    p.add_argument("--server", help=L("服务器地址（不写照 server.json）", "Server address (default: from server.json)"))
    p.add_argument("--token", help=L("接入令牌（不写用本机脚本那一把）", "Access token (default: the local scripts' one)"))
    a = ap.parse_args(argv)
    if a.timeout < 10:
        ap.error(L("--timeout 至少 10 秒", "--timeout must be at least 10 seconds"))
    return agent(a)


if __name__ == "__main__":
    sys.exit(main())
