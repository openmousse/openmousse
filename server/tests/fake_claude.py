"""假的 claude 命令（测试 engine/claude_brain.py、runners.py 用；不碰真的 Claude Code，不连真的模型）。

测试把它拷到 tmp_path、加上 #!<python> 当可执行文件，经 runners 的检测结果（bin）指过去。按场景吐 stream-json：
  FAKE_CLAUDE_SCENARIO  场景文件：[[第 1 次起它要做的几步], [第 2 次的], …]（次数多了用最后一份）。每一步：
      {"say": "字", "chunks": ["分", "段"], "think": "思考"}   开口说一段（一次「调模型」：message_start … message_stop）
      {"tool": "schedule", "input": {...}}                      调我们的工具：照临时 MCP 配置里的地址和令牌真的 POST tools/call
      {"list": true}                                            POST tools/list，记下看到的工具名
      {"read_steer": 秒, "echo": true}                          等标准输入的下一条（插话），读到了下一次开口报 user_message_uuid
                                                                （echo false：只在 result 的 user_message_uuids 里报）
      {"sleep": 秒}                                             （被 SIGINT 打断就记一笔、吐一个没做完的 result、退出）
      {"result": {...}}                                         最后那个 result 要改的栏
      {"stderr": "字", "exit": 退出码}                          直接退出
      {"api_error": "字", "error": "authentication_failed"}     它自己编一条回复（model <synthetic>，带 error：没登录、额度用完、
                                                                接口报错），这一轮就到这里（result 的 is_error true，照 Claude Code 2.1）
      {"die": "字", "exit": 退出码}                             放在第一步：读标准输入之前就退出（命令行参数不认得：版本不对）
      {"broken": "半截", "again": "整句", "call": "schedule", "input": {...}, "block": false, "lost_call": false, "streamed": false}
                                                                开口说到一半流断了，它重问一次、整条换个 id 重来（见 broken()）
  FAKE_CLAUDE_STATE     状态目录：第几次起、记得哪些会话（--resume 一个不认识的 → 「No conversation found…」退出 1）
  FAKE_CLAUDE_LOG       每次起都往这里追加几行 JSON：命令行、环境、工作目录、临时配置、开头、标准输入、MCP 的往返、收到的信号
  FAKE_CLAUDE_AUTH      claude auth status --json 回什么（默认套餐登录）
"""
import json
import os
import select
import signal
import stat
import sys
import time
import urllib.error
import urllib.request
import uuid

ARGV = sys.argv[1:]
LOG = os.environ.get("FAKE_CLAUDE_LOG") or os.devnull
STATE = os.environ.get("FAKE_CLAUDE_STATE") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake-claude-state")
PREFIX = "mcp__openmousse__"
WATCH = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_BASE_URL", "CLAUDE_CODE_DISABLE_AUTO_MEMORY",
         "CLAUDE_CODE_DISABLE_CLAUDE_MDS", "ENABLE_CLAUDEAI_MCP_SERVERS", "MCP_CONNECTION_NONBLOCKING", "ENABLE_TOOL_SEARCH",
         "MCP_TOOL_TIMEOUT", "CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST", "CLAUDECODE", "HOME", "CLAUDE_CODE_MAX_OUTPUT_TOKENS")


def log(rec: dict) -> None:
    with open(LOG, "a", encoding="utf8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def opt(name: str):
    return ARGV[ARGV.index(name) + 1] if name in ARGV else None


def emit(e: dict) -> None:
    print(json.dumps(e, ensure_ascii=False), flush=True)


if ARGV[:2] == ["auth", "status"]:
    print(os.environ.get("FAKE_CLAUDE_AUTH") or json.dumps({"loggedIn": True, "authMethod": "claude.ai", "subscriptionType": "max"}))
    sys.exit(0)

os.makedirs(STATE, exist_ok=True)
count_file = os.path.join(STATE, "count")
RUN = int(open(count_file).read()) if os.path.exists(count_file) else 0
with open(count_file, "w") as f:
    f.write(str(RUN + 1))
scenario = json.load(open(os.environ["FAKE_CLAUDE_SCENARIO"], encoding="utf8"))
STEPS = scenario[min(RUN, len(scenario) - 1)]
if STEPS and "die" in STEPS[0]:  # 认不得我们给的参数（版本不对）：什么都不读就退出
    sys.stderr.write(STEPS[0]["die"])
    sys.exit(int(STEPS[0].get("exit", 1)))
sessions_file = os.path.join(STATE, "sessions.json")
sessions = json.load(open(sessions_file)) if os.path.exists(sessions_file) else []
resume, new, conf_path, sys_path = opt("--resume"), opt("--session-id"), opt("--mcp-config"), opt("--system-prompt-file")
conf = json.load(open(conf_path)) if conf_path else {}
start = {"run": RUN, "kind": "start", "argv": ARGV, "env": {k: os.environ.get(k) for k in WATCH}, "env_keys": sorted(os.environ),
         "cwd": os.getcwd(), "cwd_mode": stat.S_IMODE(os.stat(".").st_mode), "conf": conf,
         "conf_mode": stat.S_IMODE(os.stat(conf_path).st_mode) if conf_path else None,
         "system": open(sys_path, encoding="utf8").read() if sys_path else None,
         "system_mode": stat.S_IMODE(os.stat(sys_path).st_mode) if sys_path else None}
if resume and resume not in sessions:
    log({**start, "kind": "missing"})
    sys.stderr.write(f"No conversation found with session ID: {resume}\n")
    sys.exit(1)
SID = resume or new or str(uuid.uuid4())
if new and "--no-session-persistence" not in ARGV:
    sessions.append(new)
    with open(sessions_file, "w") as f:
        json.dump(sessions, f)
log(start)
SERVED = {"opus": "claude-fake-opus", "sonnet": "claude-fake-sonnet", "haiku": "claude-fake-haiku"}.get(opt("--model") or "", opt("--model"))

buf = b""


def read_line(timeout: float):
    """标准输入的下一条（JSON）；等不到 → None；读到头 → "EOF"。"""
    global buf
    end = time.time() + timeout
    while b"\n" not in buf:
        left = end - time.time()
        if left <= 0:
            return None
        ready, _, _ = select.select([0], [], [], left)
        if not ready:
            return None
        chunk = os.read(0, 65536)
        if not chunk:
            return "EOF"
        buf += chunk
    raw, buf = buf.split(b"\n", 1)
    return json.loads(raw)


state = {"n": 0, "echo": None, "last": "", "taken": []}


def on_int(signum, frame):
    log({"run": RUN, "kind": "sigint"})
    emit({"type": "result", "subtype": "error_during_execution", "is_error": True, "result": "", "session_id": SID,
          "terminal_reason": "aborted_streaming", "usage": {"input_tokens": 5, "output_tokens": 1}, "total_cost_usd": 0.001})
    sys.exit(0)


signal.signal(signal.SIGINT, on_int)


def begin() -> str:
    state["n"] += 1
    mid = f"msg_{RUN}_{state['n']}"
    ev = {"type": "stream_event", "event": {"type": "message_start", "message": {"id": mid, "model": SERVED}}, "parent_tool_use_id": None,
          "session_id": SID}
    if state["echo"]:
        ev["user_message_uuid"] = state["echo"]
        state["echo"] = None
    emit(ev)
    return mid


def stream(ev: dict) -> None:
    emit({"type": "stream_event", "event": ev, "parent_tool_use_id": None, "session_id": SID})


def say(text: str, chunks=None, think: str | None = None) -> None:
    mid = begin()
    if think:
        stream({"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}})
        stream({"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": think}})
        stream({"type": "content_block_stop", "index": 0})
    if text:
        stream({"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}})
        for c in chunks or [text]:
            stream({"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": c}})
        emit({"type": "assistant", "message": {"id": mid, "model": SERVED, "content": [{"type": "text", "text": text}]}, "parent_tool_use_id": None,
              "session_id": SID})
        stream({"type": "content_block_stop", "index": 1})
        state["last"] = text
    stream({"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 7}})
    stream({"type": "message_stop"})


def mcp(method: str, params: dict) -> dict:
    srv = conf["mcpServers"]["openmousse"]
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(srv["url"], data=body, headers={**srv.get("headers", {}), "Content-Type": "application/json",
                                                                "Accept": "application/json, text/event-stream"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 本机的地址：不走代理
    try:
        with opener.open(req, timeout=60) as r:
            return {"status": r.status, **json.loads(r.read() or b"{}")}
    except urllib.error.HTTPError as e:
        return {"status": e.code, "error": e.read().decode("utf8", "replace")}


def tool(name: str, inp: dict) -> None:
    mid = begin()
    tid = f"toolu_{RUN}_{state['n']}"
    stream({"type": "content_block_start", "index": 0, "content_block": {"type": "tool_use", "id": tid, "name": PREFIX + name, "input": {}}})
    stream({"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": json.dumps(inp)}})
    emit({"type": "assistant", "message": {"id": mid, "model": SERVED, "content": [{"type": "tool_use", "id": tid, "name": PREFIX + name,
                                                                                    "input": inp}]}, "parent_tool_use_id": None})
    stream({"type": "content_block_stop", "index": 0})
    stream({"type": "message_stop"})
    call(name, inp, tid)


def broken(step: dict) -> None:
    """流到一半断了（Claude Code 2.1.42 的 queryModel：流里出错就不流式重问一次；--fallback-model 换模型也是重问）：这一次只有
    message_start 和几个字，没有 message_stop；流出来的那条它撤了（tombstone，stream-json 不输出），重来的那条换了 id。
    block：那段字说完了（整条到过），断在后面的工具调用上（lost_call：工具调用那一块也到过）。streamed：重来的那次照常流（换模型
    重问），不然整条一起到、不带流事件。"""
    mid = begin()
    stream({"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}})
    stream({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": step["broken"]}})
    if step.get("block"):
        emit({"type": "assistant", "message": {"id": mid, "model": SERVED, "content": [{"type": "text", "text": step["broken"]}]},
              "parent_tool_use_id": None, "session_id": SID})
        stream({"type": "content_block_stop", "index": 0})
        lost = {"type": "tool_use", "id": f"toolu_{RUN}_lost", "name": PREFIX + (step.get("call") or "schedule"), "input": {}}
        stream({"type": "content_block_start", "index": 1, "content_block": lost})
        stream({"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": "{\"args\": [\"da"}})
        if step.get("lost_call"):  # 工具调用那一块也说完了（整条到过），断在 message_stop 之前
            emit({"type": "assistant", "message": {"id": mid, "model": SERVED, "content": [{**lost, "input": step.get("input") or {}}]},
                  "parent_tool_use_id": None, "session_id": SID})
            stream({"type": "content_block_stop", "index": 1})
    if step.get("streamed"):
        say(step["again"])
        return
    state["n"] += 1
    mid, tid = f"msg_{RUN}_{state['n']}", f"toolu_{RUN}_{state['n']}"
    content = [{"type": "text", "text": step["again"]}]
    if step.get("call"):
        content.append({"type": "tool_use", "id": tid, "name": PREFIX + step["call"], "input": step.get("input") or {}})
    emit({"type": "assistant", "message": {"id": mid, "model": SERVED, "content": content}, "parent_tool_use_id": None, "session_id": SID})
    state["last"] = step["again"]
    if step.get("call"):
        call(step["call"], step.get("input") or {}, tid)


def call(name: str, inp: dict, tid: str) -> None:
    res = mcp("tools/call", {"name": name, "arguments": inp})
    log({"run": RUN, "kind": "mcp", "name": name, "response": res})
    result = res.get("result") or {}
    text = "".join(c.get("text", "") for c in result.get("content") or []) or res.get("error", "")
    emit({"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid, "content": text,
                                                                    "is_error": bool(result.get("isError") or res.get("error"))}]},
          "parent_tool_use_id": None, "session_id": SID})


def result(over: dict | None = None) -> None:
    emit({"type": "result", "subtype": "success", "is_error": False, "result": state["last"], "session_id": SID, "num_turns": state["n"],
          "usage": {"input_tokens": 120, "output_tokens": 30, "cache_read_input_tokens": 1000, "cache_creation_input_tokens": 50},
          "total_cost_usd": 0.02, "user_message_uuids": list(state["taken"]), "user_message_uuid": state["taken"][-1], **(over or {})})


first = read_line(15)
if not isinstance(first, dict):
    log({"run": RUN, "kind": "no-input"})
    sys.exit(2)
log({"run": RUN, "kind": "stdin", "msg": first})
state["taken"].append(first.get("uuid"))
emit({"type": "system", "subtype": "init", "session_id": SID, "model": SERVED, "tools": [], "mcp_servers": [{"name": "openmousse",
                                                                                                             "status": "connected"}]})
over = None
for step in STEPS:
    if "stderr" in step or "exit" in step:
        sys.stderr.write(step.get("stderr", ""))
        sys.exit(int(step.get("exit", 1)))
    if "broken" in step:
        broken(step)
    elif "say" in step or "think" in step:
        say(step.get("say", ""), step.get("chunks"), step.get("think"))
    elif "tool" in step:
        tool(step["tool"], step.get("input") or {})
    elif step.get("list"):
        res = mcp("tools/list", {})
        log({"run": RUN, "kind": "list", "names": [t["name"] for t in (res.get("result") or {}).get("tools") or []], "status": res.get("status")})
    elif "read_steer" in step:
        m = read_line(float(step["read_steer"]))
        if isinstance(m, dict):
            log({"run": RUN, "kind": "stdin", "msg": m})
            state["taken"].append(m.get("uuid"))
            if step.get("echo", True):  # false：回复上不报，只在 result 的 user_message_uuids 里（老一点的版本那样）
                state["echo"] = m.get("uuid")
    elif "sleep" in step:
        time.sleep(float(step["sleep"]))
    elif "result" in step:
        over = step["result"]
    elif "api_error" in step:  # 照 Claude Code 2.1：接口报错编成一条 <synthetic> 回复（带 error），result 是这句、is_error true
        emit({"type": "assistant", "message": {"id": str(uuid.uuid4()), "model": "<synthetic>", "role": "assistant",
                                               "content": [{"type": "text", "text": step["api_error"]}]},
              "parent_tool_use_id": None, "session_id": SID, "error": step.get("error", "unknown")})
        over = {**(over or {}), "is_error": True, "result": step["api_error"]}
        break
result(over)
while True:  # 标准输入关上之前又来的（插话没赶上这一轮）：一条一轮，处理完才退（和真的一样）
    m = read_line(15)
    if not isinstance(m, dict):
        break
    log({"run": RUN, "kind": "stdin", "msg": m})
    state["taken"] = [m.get("uuid")]
    state["echo"] = m.get("uuid")
    words = "".join(b.get("text", "") for b in (m.get("message") or {}).get("content") or [] if isinstance(b, dict))
    say(f"(later) {words}")
    result()
log({"run": RUN, "kind": "exit"})
