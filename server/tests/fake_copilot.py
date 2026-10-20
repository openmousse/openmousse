"""假的 copilot 命令（测试 engine/provider_cli.py 用；不碰真的 Copilot，不连真的模型）。照 1.0.100 的样子（10/19 拿真的核对过参数、
它自带的 session-events.schema.json 里的事件格式）：

  没登录（FAKE_COPILOT_LOGIN=none）：stderr 打 Error: No authentication information found.，退出 1（同 1.0.100，一个事件都不出）
  别的：从标准输入读这一次的话（没给 -p 时），--output-format json：一行一个会话事件 {"type", "data", …}，照 FAKE_COPILOT_SCENARIO
  （一个 JSON 列表）一步一步：
      {"say": "字"}                         assistant.message（之后一条 assistant.usage）
      {"think": "字"}                       assistant.reasoning
      {"tool": "schedule", "input": {...}}  tool.execution_start / tool.execution_complete；工具不在 --available-tools 里（openmousse-<名>）的
                                            不调（同真的：模型看不见它）；在的真的 POST tools/call 到 --additional-mcp-config 那个文件里
                                            openmousse 的地址（headers 照原样带上，${VAR} 照它的规矩从环境变量展开）
      {"error": "quota", "message": "字", "status": 429}   session.error，之后退出 1
      {"sleep": 秒}                         （被 SIGTERM 打断就记一笔、退出）
  最后 session.shutdown。照 1.0.100：每次都在 $COPILOT_HOME（没给 = ~/.copilot）/session-state/<--session-id>/ 记一份会话。
  FAKE_COPILOT_LOG  每次起都往这里追加几行 JSON：命令行、要紧的环境变量在不在（不记值）、工作目录、标准输入、工作目录里的设置、MCP 配置文件
  的权限、附件在不在、MCP 的往返、收到的信号。
"""
import json
import os
import re
import signal
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ARGV = sys.argv[1:]
LOG = os.environ.get("FAKE_COPILOT_LOG") or os.devnull
WATCH = ("GH_TOKEN", "GITHUB_TOKEN", "COPILOT_GITHUB_TOKEN", "COPILOT_PROVIDER_BASE_URL", "COPILOT_PROVIDER_API_KEY", "COPILOT_ALLOW_ALL",
         "COPILOT_MODEL", "GITHUB_COPILOT_PROMPT_MODE_REPO_HOOKS", "OTEL_EXPORTER_OTLP_ENDPOINT", "OPENAI_API_KEY", "COPILOT_HOME",
         "COPILOT_AUTO_UPDATE", "MOUSSE_TASK_MCP_TOKEN")


def log(rec: dict) -> None:
    with open(LOG, "a", encoding="utf8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def emit(t: str, data: dict) -> None:
    print(json.dumps({"id": str(uuid.uuid4()), "timestamp": "2026-10-19T08:00:00.000Z", "parentId": None, "ephemeral": False, "type": t,
                      "data": data}, ensure_ascii=False), flush=True)


def opt(name: str) -> list[str]:
    """--name v 或 --name=v 的所有值。"""
    out = []
    for i, a in enumerate(ARGV):
        if a == name and i + 1 < len(ARGV):
            out.append(ARGV[i + 1])
        elif a.startswith(name + "="):
            out.append(a[len(name) + 1:])
    return out


if os.environ.get("FAKE_COPILOT_LOGIN") == "none":
    print("Error: No authentication information found.\n\nCopilot can be authenticated with GitHub using an OAuth Token or a Fine-Grained "
          "Personal Access Token.", file=sys.stderr)
    sys.exit(1)


def on_term(*_a) -> None:
    log({"kind": "signal", "signal": "TERM"})
    sys.exit(143)


signal.signal(signal.SIGTERM, on_term)
prompt = (opt("-p") or [None])[0] or sys.stdin.read()
cfg_path = (opt("--additional-mcp-config") or [""])[0]
mcp_file = Path(cfg_path[1:]) if cfg_path.startswith("@") else None
settings = Path.cwd() / ".github" / "copilot" / "settings.json"
log({"kind": "start", "argv": ARGV, "cwd": os.getcwd(), "stdin": prompt, "env": {k: (k in os.environ) for k in WATCH},
     "settings": json.loads(settings.read_text(encoding="utf8")) if settings.exists() else None,
     "mcp_mode": oct(mcp_file.stat().st_mode & 0o777) if mcp_file and mcp_file.exists() else None,
     "attachments": {a: Path(a).exists() for a in opt("--attachment")}})
servers = json.loads(mcp_file.read_text(encoding="utf8")).get("mcpServers", {}) if mcp_file else {}
home = Path(os.environ.get("COPILOT_HOME") or Path.home() / ".copilot")
sid = (opt("--session-id") or [str(uuid.uuid4())])[0]
state = home / "session-state" / sid
state.mkdir(parents=True, exist_ok=True)
(state / "events.jsonl").write_text(json.dumps({"type": "user.message", "data": {"content": prompt}}) + "\n", encoding="utf8")
(home / "session-state" / ".session-operation-locks").mkdir(parents=True, exist_ok=True)
(home / "session-state" / ".session-operation-locks" / f"{sid}.lock").write_text("", encoding="utf8")
available = {t for v in opt("--available-tools") for t in v.split(",") if t}


def expand(v: str) -> str:
    return re.sub(r"\$\{(\w+)\}", lambda m: os.environ.get(m.group(1), ""), v)


def mcp(server: dict, method: str, params: dict) -> dict:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
               **{k: expand(str(v)) for k, v in (server.get("headers") or {}).items()}}
    req = urllib.request.Request(server["url"], data=body, headers=headers)
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=60) as r:
            return {"status": r.status, **json.loads(r.read() or b"{}")}
    except urllib.error.HTTPError as e:
        return {"status": e.code, "error": e.read().decode("utf8", "replace")}


emit("session.start", {"sessionId": sid})
emit("assistant.turn_start", {"turnId": "t1"})
n = 0
for step in json.loads(os.environ.get("FAKE_COPILOT_SCENARIO") or '[{"say": "ok"}]'):
    n += 1
    if "say" in step:
        emit("assistant.message", {"messageId": f"m{n}", "content": step["say"], "model": "fake-model"})
        emit("assistant.usage", {"model": "fake-model", "inputTokens": 1500, "cacheReadTokens": 1000, "outputTokens": 30, "cost": 1})
    elif "think" in step:
        emit("assistant.reasoning", {"reasoningId": f"r{n}", "content": step["think"]})
    elif "tool" in step:
        full = f"openmousse-{step['tool']}"
        emit("assistant.message", {"messageId": f"m{n}", "content": "", "toolRequests": [{"toolCallId": f"c{n}", "name": full}]})
        if full not in available:  # 模型看不见它：不调
            log({"kind": "hidden", "name": full})
            continue
        emit("tool.execution_start", {"toolCallId": f"c{n}", "toolName": full, "mcpServerName": "openmousse", "mcpToolName": step["tool"],
                                      "arguments": step.get("input") or {}})
        res = mcp(servers["openmousse"], "tools/call", {"name": step["tool"], "arguments": step.get("input") or {}})
        log({"kind": "mcp", "name": step["tool"], "response": res})
        result = res.get("result") or {}
        bad = bool(result.get("isError") or res.get("error"))
        emit("tool.execution_complete", {"toolCallId": f"c{n}", "success": not bad, **({"error": {"message": str(res.get("error"))}} if bad else
                                                                                        {"result": {"content": json.dumps(result.get("content"))}})})
    elif "error" in step:
        emit("session.error", {"errorType": step["error"], "message": step.get("message") or "error",
                               **({"statusCode": step["status"]} if step.get("status") else {})})
        emit("session.shutdown", {"shutdownType": "error", "errorReason": step.get("message") or "error"})
        sys.exit(1)
    elif "sleep" in step:
        time.sleep(float(step["sleep"]))
emit("assistant.turn_end", {"turnId": "t1"})
emit("session.shutdown", {"shutdownType": "routine", "totalApiDurationMs": 10, "sessionStartTime": 0, "codeChanges": {}, "modelMetrics": {}})
