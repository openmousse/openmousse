#!/usr/bin/env python3
"""mousse：OpenMousse 的命令行（2026-10-19）。子命令：mousse agent、mousse migrate、mousse node、mousse chat（下面）；mousse channels（渠道，见 channels_ctl.py）。

  mousse agent --agent <id> [--model provider/model] --message-file <文件或 -> [--timeout 秒] [--server 地址] [--token 令牌]
  mousse migrate openclaw|hermes [--openclaw-home / --hermes-home 路径] [--out 目录] [--apply [--copy-secrets]]；mousse migrate on [id …]
    把 OpenClaw 或 Hermes 的设置搬进自带的内核（身子在 migrate/ 包里，说明见 migrate/__init__.py 开头）。默认只打印报告

下面是 mousse agent：

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
另有 mousse pause [--stop-running] [--reason …] / mousse resume：急停（../estop_ctl.py、engine/estop.py）。
Agent 自己跑的命令里（环境里有 MOUSSE_AGENT，主机命令 engine/host_exec.py 会给）不跑：照 OpenClaw 的 openclaw agent，那样会丢掉是谁转的、
绕过转交的跳数；Agent 之间转话用 handoff（engine/sessions.py）。

运维命令（第 7 批，身子在 ops/，每个命令 mousse <命令> --help 看详细的）：
  mousse doctor [--json] [--fix] [--yes] [--strict]   体检，--fix 修常见问题
  mousse status [--json]                              一屏摘要
  mousse logs [-f] [-n 200] [--since 1h] [--grep 词]   看服务的日志（打过码）
  mousse backup create | verify | list、mousse restore   备份和恢复
  mousse update [--check] [--plan] [--channel stable|main] [--yes]   更新
  mousse config validate | get | set | unset           查 / 改 server.json（整份校验）
  mousse security audit [--json] [--fix]              安全体检
  mousse sessions cleanup [--older-than 90d] [--dry-run]   清旧的内核会话记录，再 VACUUM
  mousse diagnostics [--out FILE.zip] [--print]        打一个诊断包（不带对话、密钥、令牌、邮箱）
  mousse uninstall [--full] [--dry-run] [--yes]        卸载（默认只拿掉服务和软链；--full 先备份再全删）

  mousse node pair '<链接>' | run | install | uninstall | status
把这台电脑当成节点（第 8 批）：后面的原样交给 node_host.py（见那个文件开头）。

  mousse chat [--agent main|<id>] [--server 地址] [--token 令牌]
在终端里跟主对话（或某个 Agent）说话（第 8 批）：读一行当你自己的一句话发进那个对话（app 里看得到），回复边来边打，工具一步一行；
/stop 或 Ctrl-C 停下这一轮，/quit 或 Ctrl-D 退出；要你点头的事请去 app 里确认。见 termchat.py。令牌、地址同上。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

import estop_ctl  # 第 4 批：mousse pause / resume（急停）
import hooks_ctl  # 第 5 批：mousse hooks（用户钩子）
import mcp_ctl  # 第 5 批：mousse mcp（你配的 MCP 服务）
import plugins_ctl  # 第 5 批：mousse plugins（带代码的插件，默认关）
import skills_ctl  # 第 5 批：mousse skills（技能市场）
from config import settings
from i18n import L, lang
from ops import cli as ops_cli

TIMEOUT = 600  # 默认最多等这一轮多少秒（服务器那边最多 1800）


def description() -> str:
    """--help 的说明，按 server.json 的 language。"""
    return L(__doc__, """mousse: OpenMousse's command line (2026-10-19). Commands: mousse agent, mousse migrate, mousse node and mousse chat (below); mousse channels (channels, see channels_ctl.py).

  mousse agent --agent <id> [--model provider/model] --message-file <file or -> [--timeout seconds] [--server URL] [--token TOKEN]
  mousse migrate openclaw|hermes [--openclaw-home / --hermes-home path] [--out folder] [--apply [--copy-secrets]]; mousse migrate on [id …]
    Move OpenClaw's or Hermes's settings over to the built-in engine (the code is in the migrate/ package, described at the top of
    migrate/__init__.py). By default it only prints a report

The rest is about mousse agent:

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
Also mousse pause [--stop-running] [--reason …] / mousse resume: the emergency stop (estop_ctl.py, engine/estop.py).
It doesn't run inside an Agent's own command (MOUSSE_AGENT in the environment, which host commands get): like OpenClaw's openclaw agent,
that would lose who handed what over and skip the handoff hop limit. Agents hand over to each other with handoff.

Operations (batch 7; the code is in ops/, mousse <command> --help for the details):
  mousse doctor [--json] [--fix] [--yes] [--strict]   health check; --fix repairs common problems
  mousse status [--json]                              one-screen summary
  mousse logs [-f] [-n 200] [--since 1h] [--grep WORD]   the server's logs (scrubbed)
  mousse backup create | verify | list, mousse restore   back up and restore
  mousse update [--check] [--plan] [--channel stable|main] [--yes]   update
  mousse config validate | get | set | unset           check / change server.json (validated as a whole)
  mousse security audit [--json] [--fix]              security check
  mousse sessions cleanup [--older-than 90d] [--dry-run]   remove old engine session transcripts, then VACUUM
  mousse diagnostics [--out FILE.zip] [--print]        a diagnostics bundle (no conversations, keys, tokens or emails)
  mousse uninstall [--full] [--dry-run] [--yes]        uninstall (services and links; --full deletes everything after a backup)

  mousse node pair '<link>' | run | install | uninstall | status
Use this computer as a node: the rest goes to node_host.py as is (see the top of that file).

  mousse chat [--agent main|<id>] [--server URL] [--token TOKEN]
Talk to the main chat (or an Agent) in the terminal: each line goes to that conversation as your own message (the app shows it), the
reply prints as it streams, one line per tool step; /stop or Ctrl-C stops the reply, /quit or Ctrl-D quits; anything that needs your OK
is confirmed in the app. See termchat.py. Token and address as above.
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
    for ctl in (estop_ctl, skills_ctl, hooks_ctl, mcp_ctl, plugins_ctl):  # 第 4、5 批：各自的 *_ctl.py 登记子命令（set_defaults(run=…)）
        ctl.register(sub)
    ops_cli.add(sub)  # 第 7 批的运维命令（ops/cli.py）：doctor、status、logs……
    sub.add_parser("channels", help=L("渠道：Telegram、邮件（第 6 批，见 channels_ctl.py）", "Channels: Telegram, email (see channels_ctl.py)"))
    import migrate  # mousse migrate：参数和身子都在 migrate/ 包里
    m = sub.add_parser("migrate", help=L("把 OpenClaw / Hermes 的设置搬进自带内核（默认只打印报告）",
                                         "Move OpenClaw / Hermes settings to the built-in engine (prints a report by default)"),
                       description=L(migrate.__doc__, "Move OpenClaw or Hermes settings to the built-in engine. See server/README.md."),
                       formatter_class=argparse.RawDescriptionHelpFormatter)
    migrate.arguments(m)
    sub.add_parser("node", add_help=False, help=L("把这台电脑当成节点（node_host.py）", "Use this computer as a node (node_host.py)"))
    c = sub.add_parser("chat", help=L("在终端里跟主对话或某个 Agent 说话（termchat.py）", "Talk to the main chat or an Agent in the terminal (termchat.py)"))
    c.add_argument("--agent", default="main", help=L("main（默认）或 Agent 的 id", "main (default) or an Agent's id"))
    c.add_argument("--server", help=L("服务器地址（不写照 server.json）", "Server address (default: from server.json)"))
    c.add_argument("--token", help=L("接入令牌（不写用本机脚本那一把）", "Access token (default: the local scripts' one)"))
    args = sys.argv[1:] if argv is None else list(argv)
    if args[:1] == ["channels"]:  # 渠道的子命令都在 channels_ctl.py（它自己的参数、--help）
        import channels_ctl
        return channels_ctl.main(args[1:])
    if args[:1] == ["node"]:  # 第 8 批：后面的原样交给 node_host.py（只用标准库，拷到别的电脑上也能单独跑；--help 也是它的）
        import node_host
        return node_host.main(args[1:])
    a = ap.parse_args(argv)
    if a.cmd == "migrate":
        return migrate.run(a)
    if a.cmd == "chat":  # 第 8 批：终端里的对话（termchat.py）
        import termchat
        return termchat.main(server_url(a.server), (a.token or os.environ.get("MOUSSE_TOKEN") or "").strip() or settings.api_token(), a.agent)
    if getattr(a, "run", None) is not None:
        return a.run(a)
    if a.timeout < 10:
        ap.error(L("--timeout 至少 10 秒", "--timeout must be at least 10 seconds"))
    return agent(a)


if __name__ == "__main__":
    sys.exit(main())
