"""假的 codex 命令（测试 engine/provider_cli.py 用；不碰真的 Codex，不连真的模型）。照 0.163.0 的样子（10/19 拿真的核对过）：

  codex login status   往 stderr 打 FAKE_CODEX_LOGIN（默认 "Logged in using ChatGPT"）；说 Not logged in 的退出 1（同 0.163.0）
  codex exec …         读标准输入，照 FAKE_CODEX_SCENARIO（一个 JSON 列表）吐 --json 的事件，每一步：
      {"say": "字"}                         item.completed agent_message
      {"think": "字"}                       item.completed reasoning
      {"tool": "schedule", "input": {...}}  真的 POST tools/call 到 -c mcp_servers.openmousse.url 的地址（令牌是环境变量
                                            mcp_servers.openmousse.bearer_token_env_var 指的那个），前后各一个 mcp_tool_call 事件
      {"fail": "字"}                        turn.failed（之后退出 1）
      {"sleep": 秒}                         （被 SIGTERM 打断就记一笔、退出）
  最后 turn.completed（带 usage）。照 0.163.0：exec 的 approval_policy 是 never，没写 mcp_servers.openmousse.default_tools_approval_mode="approve"
  的话 MCP 调用一律被它自己拒掉（不发到服务器）；写了 mcp_servers.openmousse.required=true 而服务器连不上：一个事件都不出，stderr 一行
  Error、退出 1。
  FAKE_CODEX_LOG  每次起都往这里追加几行 JSON：命令行、要紧的环境变量在不在（不记值）、工作目录、标准输入、MCP 的往返、收到的信号。
"""
import json
import os
import re
import signal
import sys
import time
import urllib.error
import urllib.request

ARGV = sys.argv[1:]
LOG = os.environ.get("FAKE_CODEX_LOG") or os.devnull
WATCH = ("OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL", "MOUSSE_TASK_MCP_TOKEN", "DEEPSEEK_API_KEY", "CODEX_HOME")


def log(rec: dict) -> None:
    with open(LOG, "a", encoding="utf8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def emit(e: dict) -> None:
    print(json.dumps(e, ensure_ascii=False), flush=True)


if ARGV[:2] == ["login", "status"]:
    said = os.environ.get("FAKE_CODEX_LOGIN", "Logged in using ChatGPT")
    print(said, file=sys.stderr)
    sys.exit(1 if "not logged in" in said.lower() else 0)

if not ARGV or ARGV[0] != "exec":
    print(f"fake codex: unexpected arguments {ARGV}", file=sys.stderr)
    sys.exit(2)


def conf(key: str) -> str | None:
    """-c key=value 里 key 的值（去掉 TOML 的引号）。"""
    for i, a in enumerate(ARGV):
        if a == "-c" and i + 1 < len(ARGV) and ARGV[i + 1].startswith(key + "="):
            v = ARGV[i + 1][len(key) + 1:]
            return v[1:-1] if v.startswith('"') and v.endswith('"') else v
    return None


def on_term(*_a) -> None:
    log({"kind": "signal", "signal": "TERM"})
    sys.exit(143)


signal.signal(signal.SIGTERM, on_term)
stdin = sys.stdin.read()
log({"kind": "start", "argv": ARGV, "cwd": os.getcwd(), "stdin": stdin, "env": {k: (k in os.environ) for k in WATCH}})
APPROVE = conf("mcp_servers.openmousse.default_tools_approval_mode") == "approve"
if conf("mcp_servers.openmousse.required") == "true":
    try:
        urllib.request.build_opener(urllib.request.ProxyHandler({})).open(urllib.request.Request(conf("mcp_servers.openmousse.url") or "http://127.0.0.1:9/",
                                                                                                 data=b"{}", method="POST"), timeout=5)
    except urllib.error.HTTPError:
        pass  # 连得上（回什么都行）
    except OSError:
        print("Error: thread/start failed: required MCP servers failed to initialize: openmousse: handshaking with MCP server failed",
              file=sys.stderr)
        sys.exit(1)
emit({"type": "thread.started", "thread_id": "fake-thread"})
emit({"type": "turn.started"})
n = 0


def mcp(method: str, params: dict) -> dict:
    url = conf("mcp_servers.openmousse.url")
    env = conf("mcp_servers.openmousse.bearer_token_env_var") or ""
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(url, data=body, headers={"Authorization": f"Bearer {os.environ.get(env, '')}", "Content-Type": "application/json",
                                                         "Accept": "application/json, text/event-stream"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=60) as r:
            return {"status": r.status, **json.loads(r.read() or b"{}")}
    except urllib.error.HTTPError as e:
        return {"status": e.code, "error": e.read().decode("utf8", "replace")}


for step in json.loads(os.environ.get("FAKE_CODEX_SCENARIO") or '[{"say": "ok"}]'):
    n += 1
    if "say" in step:
        emit({"type": "item.completed", "item": {"id": f"item_{n}", "type": "agent_message", "text": step["say"]}})
    elif "think" in step:
        emit({"type": "item.completed", "item": {"id": f"item_{n}", "type": "reasoning", "text": step["think"]}})
    elif "tool" in step:
        item = {"id": f"item_{n}", "type": "mcp_tool_call", "server": "openmousse", "tool": step["tool"], "arguments": step.get("input") or {}}
        emit({"type": "item.started", "item": {**item, "status": "in_progress"}})
        if not APPROVE:  # 0.163.0：approval_policy never，要点头的 MCP 调用它自己拒掉
            log({"kind": "denied", "name": step["tool"]})
            emit({"type": "item.completed", "item": {**item, "status": "failed",
                                                     "error": {"message": "MCP tool call requires approval, but approval policy is never"}}})
            continue
        res = mcp("tools/call", {"name": step["tool"], "arguments": step.get("input") or {}})
        log({"kind": "mcp", "name": step["tool"], "response": res})
        result = res.get("result") or {}
        bad = bool(result.get("isError") or res.get("error"))
        emit({"type": "item.completed", "item": {**item, "status": "failed" if bad else "completed",
                                                 "result": {"content": result.get("content") or []}, "error": res.get("error")}})
    elif "fail" in step:
        emit({"type": "error", "message": "Reconnecting... 1/5 (" + step["fail"] + ")"})
        emit({"type": "turn.failed", "error": {"message": step["fail"]}})
        sys.exit(1)
    elif "sleep" in step:
        time.sleep(float(step["sleep"]))
emit({"type": "turn.completed", "usage": {"input_tokens": 1200, "cached_input_tokens": 1000, "output_tokens": 40}})
if re.search(r"\bexit-2\b", stdin):
    sys.exit(2)
