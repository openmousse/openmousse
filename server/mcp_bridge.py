"""OpenMousse 的 MCP 入口（2026-09-29）：claw 经 MCP 用 OpenMousse 的功能，不用 shell、不用和脚本在同一个沙箱里。

为什么：以前 claw 靠 skill 里的 `python3 ~/.openmousse/repo/server/*_ctl.py …` 用这些功能，要能跑 shell、能读到我们的脚本；
沙箱里的 Agent、换了 docker / ssh 终端后端的 Hermes、云上托管的 claw 都跑不了。MCP 只要能连上这个地址。

地址（Streamable HTTP，无状态，回 JSON）：
  <服务地址>/mcp            + Authorization: Bearer <令牌>（也认 X-API-Key）
  <服务地址>/mcp/<令牌>      令牌放在路径里，给带不了请求头的客户端
令牌：server.json 的 auth.tokens 里名字是 mcp 或 mcp-<agent id> 的（python3 tokens.py add mcp），别的令牌这里不认：
  mcp              整个 claw 共用：命令默认在主 workspace 里跑；某个 Agent 调时填工具的 agent 参数（它的 id），就在它的工作区里跑
  mcp-<agent id>   绑一个 Agent：命令在它的 workspace 里跑（和它自己在 shell 里跑一样，不用写 --agent）；mcp-main = 主 workspace
工具：每个是一个现成命令行脚本的桥，参数和 skills 里写的命令一模一样：
  args   命令里脚本名后面的那些词，一个词一项（不经 shell：引号、空格、中文都不用转义；JSON 整段放一项）
  input  本来要从标准输入给的内容（命令里写 --file -、--stdin 这类的时候）
  agent  （整个 claw 共用的令牌才用）你是哪个 Agent：命令在它的工作区里跑，脚本自己认出是它，不用管 --agent 放在哪
  回脚本的输出；退出码不是 0 = 工具出错，带上脚本说的原因。
每次调用起一个子进程跑脚本（约 0.1 秒），同时最多 4 个；单次最长 120 秒（handoff 转给 Agent 等回话，300 秒；客户端那边的超时要设得比它长）。

server.json 的 mcp 段（可选，每次读文件）：{"scripts": {"工具名": ["命令", "参数"…]}}：换掉或加一个工具背后的命令
（比如日志换成你自己 workspace 里的脚本）；写成 null = 不提供这个工具。改了工具列表要重启服务。
连接器（apps.py）：你连上的第三方应用的工具也从这里给出去（<应用 id>__<工具名>），接在这 13 个后面；每次列工具时现查，连上、断开、
改权限都不用重启。它们经 EXTRA 挂进来，这 13 个工具本身不经过它。
"""
import asyncio
import os
import secrets
import shlex
import shutil
import signal
import sqlite3
import sys
from pathlib import Path
from typing import Annotated, Awaitable, Callable

from mcp import types
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import Field
from starlette.responses import JSONResponse

from config import raw, settings
from i18n import L

SERVER = Path(__file__).resolve().parent
REPO = SERVER.parent
MAX_OUT = 30000        # 回给模型的输出最多多少字（多了掐中间）
MAX_ARGS = 200000      # args + input 一共最多多少字
TIMEOUT, SLOW = 120, 300  # SLOW：handoff 等另一个 Agent 回话（ask_agent.py 默认等 150 秒）
PARALLEL = asyncio.Semaphore(4)

# 工具名 → (脚本, 中文说明, English, 超时)。说明只写它管什么；子命令和写法在对应的 skill 里。
BRIDGES: dict[str, tuple[str, str, str, int]] = {
    "board": ("server/board_ctl.py", "你自己的数据表和看板（board_ctl.py）：show、table、rows、check、apply、propose、pack、alert、history、revert。写法见 board skill。",
              "Your own data tables and board (board_ctl.py): show, table, rows, check, apply, propose, pack, alert, history, revert. See the board skill.", TIMEOUT),
    "inbox": ("server/inbox_ctl.py", "收件箱，要用户点头的事（inbox_ctl.py）：add、update、done、fail、withdraw、list、get。什么时候交、怎么写见 inbox skill。",
              "The inbox, things that need the user's OK (inbox_ctl.py): add, update, done, fail, withdraw, list, get. When and how: the inbox skill.", TIMEOUT),
    "goals": ("server/goals_ctl.py", "用户的长期目标（goals_ctl.py）：list、trend、log、add、update、done、drop、reopen、undo。见 goals skill。",
              "The user's long-term goals (goals_ctl.py): list, trend, log, add, update, done, drop, reopen, undo. See the goals skill.", TIMEOUT),
    "project": ("server/project_ctl.py", "项目（project_ctl.py）：list、show、create、propose、goal、progress、add、done、ask、conclude、archive…。见 project skill。",
                "Projects (project_ctl.py): list, show, create, propose, goal, progress, add, done, ask, conclude, archive… See the project skill.", TIMEOUT),
    "schedule": ("server/schedule_ctl.py", "日程和「要记得的」（schedule_ctl.py）：day、remember、add、update、delete、skip、attend、place、done、undo…。",
                 "The schedule and things to remember (schedule_ctl.py): day, remember, add, update, delete, skip, attend, place, done, undo…", TIMEOUT),
    "study": ("server/study_ctl.py", "学习台的课（study_ctl.py）：courses、show、check、session、reading、ddl、file、assign、syllabus、answer、style、generate、undo、log、create、delete。见 study skill。",
              "Study desk courses (study_ctl.py): courses, show, check, session, reading, ddl, file, assign, syllabus, answer, style, generate, undo, log, create, delete. See the study skill.", TIMEOUT),
    "agents": ("server/agent_ctl.py", "新建 / 改 / 删 Agent（agent_ctl.py）：list、create、update、delete。见 agent-builder skill。",
               "Create, edit or delete Agents (agent_ctl.py): list, create, update, delete. See the agent-builder skill.", TIMEOUT),
    "handoff": ("packs/core/scripts/ask_agent.py", "把属于某个 Agent 的事转给它、等它回话（ask_agent.py）：--list 看有哪些 Agent。见 handoff skill。",
                "Hand something to the Agent it belongs to and wait for its answer (ask_agent.py); --list shows the Agents. See the handoff skill.", SLOW),
    "tasks": ("server/tasks_ctl.py", "派后台任务之前看额度（tasks_ctl.py）：quota、list。见 dispatch skill。",
              "Check the background-task allowance before dispatching (tasks_ctl.py): quota, list. See the dispatch skill.", TIMEOUT),
    "proposals": ("server/proposals_ctl.py", "日结提案（proposals_ctl.py）：context、skill、agent、list、show、withdraw。见 proposals skill。",
                  "Nightly proposals (proposals_ctl.py): context, skill, agent, list, show, withdraw. See the proposals skill.", TIMEOUT),
    "journal": ("packs/core/scripts/journal.py", "用户的日志：感受、想法、决定（journal.py）：add、list、search、delete。见 journal skill。",
                "The user's journal: feelings, thoughts, decisions (journal.py): add, list, search, delete. See the journal skill.", TIMEOUT),
    "settings": ("server/settings_ctl.py", "助手的设置，比如用户的称呼（settings_ctl.py）：user-name。",
                 "Assistant settings such as what to call the user (settings_ctl.py): user-name.", TIMEOUT),
    "tree": ("mousse-tree", "世界树，用户跨平台共享的记忆（mousse-tree 命令行）。见 memory-tree skill。",
             "The memory tree, the user's memory shared across AI platforms (the mousse-tree CLI). See the memory-tree skill.", TIMEOUT),
}


# 读服务器上文件的参数：经 MCP 只许写 -（内容放 input）。沙箱里的 Agent、云上的 claw 本来碰不到这台机器的文件，
# 不能借 --detail-file /某个文件 把它塞进收件箱卡再读回去。argparse 认缩写（--detail-f、--pla），所以前缀也算，
# 除了本身就是别的参数的这几个（--brief、--detail、--field 是正经参数，不读文件）。
FILE_FLAGS = ("--file", "--detail-file", "--board-file", "--brief-file", "--fields-file", "--plan")
NOT_FILE = {"--brief", "--detail", "--field"}
TREE_OK = {"add", "recall", "recent", "forget", "stats"}  # 世界树只给记忆本身的操作；init、migrate、urls（会打印令牌）这些不给
# 别的模块加到 /mcp 上的工具（连接器 apps.py）：一项 = (tools, call)。tools(who) → [types.Tool]，每次列工具时现问；
# call(工具名, 参数, who) → types.CallToolResult，不是它的工具回 None。who = {"token": 令牌名, "agent": 令牌绑的 Agent 或 None}。
EXTRA: list[tuple[Callable[[dict], Awaitable[list[types.Tool]]], Callable[[str, dict, dict], Awaitable[types.CallToolResult | None]]]] = []


def guard(name: str, args: list[str]) -> None:
    if name == "tree" and (not args or args[0] not in TREE_OK):
        raise ToolError(L(f"tree 只能用 {'、'.join(sorted(TREE_OK))}", f"tree only allows {', '.join(sorted(TREE_OK))}"))
    for i, a in enumerate(args):
        flag, eq, val = a.partition("=")
        if not flag.startswith("--") or flag in NOT_FILE or not any(f.startswith(flag) for f in FILE_FLAGS) or len(flag) < 4:
            continue
        v = val if eq else (args[i + 1] if i + 1 < len(args) else "")
        if v != "-":
            raise ToolError(L(f"经 MCP 不能读服务器上的文件：{flag} 写 -，内容放 input",
                              f"Over MCP you can't read files on the server: write {flag} -, and put the content in input"))


def overrides() -> dict:
    m = raw().get("mcp")
    s = m.get("scripts") if isinstance(m, dict) else None
    return s if isinstance(s, dict) else {}


def command(name: str) -> list[str] | None:
    """这个工具背后要跑的命令（不含 args）；找不到就 None（工具不提供）。"""
    o = overrides()
    if name in o:
        v = o[name]
        if v is None:
            return None
        v = [v] if isinstance(v, str) else v
        return [str(Path(x).expanduser()) if str(x).startswith("~") else str(x) for x in v] or None
    script = BRIDGES[name][0]
    if script == "mousse-tree":  # 世界树的命令行装在服务同一个 venv 里（安装器装的）；没有就看 PATH
        exe = Path(sys.executable).parent / "mousse-tree"
        found = str(exe) if exe.exists() else shutil.which("mousse-tree")
        return [found] if found else None
    path = REPO / script
    return [sys.executable, str(path)] if path.exists() else None


def identify(token: str) -> tuple[str, str | None] | None:
    """令牌 → (令牌名, 绑的 Agent 或 None)。只认 mcp、mcp-<id>。"""
    if not token:
        return None
    for name, tok in settings.tokens().items():
        if (name == "mcp" or name.startswith("mcp-")) and tok and secrets.compare_digest(tok, token):
            return name, (name[4:] or None) if name.startswith("mcp-") else None
    return None


def known_agent(aid: str) -> bool:
    """有这个 Agent 吗：OpenClaw 那种有自己 workspace 的，或者 groups 表里的（别的 claw 的 Agent 只在表里）。"""
    if aid == "main" or aid in settings.agent_workspaces:
        return True
    try:
        with sqlite3.connect(f"file:{settings.db}?mode=ro", uri=True, timeout=5) as conn:
            return conn.execute("SELECT 1 FROM groups WHERE id=?", (aid,)).fetchone() is not None
    except sqlite3.Error:
        return False


def workdir(agent: str | None) -> Path:
    """命令在哪儿跑：是某个有 workspace 的 Agent 就在它的 workspace（和它自己在 shell 里跑一样）；否则在主 workspace。
    是谁另外经环境变量 MOUSSE_AGENT 告诉脚本（看板、收件箱先认它），别的 claw 的 Agent 没有 workspace 也认得出。"""
    if agent and agent != "main":
        if not known_agent(agent):
            raise ToolError(L(f"没有「{agent}」这个 Agent（agents 工具的 list 能看到有哪些）",
                              f"There's no Agent called {agent} (the agents tool's list shows them)"))
        ws = settings.agent_workspaces.get(agent)
        if ws and ws.is_dir():
            return ws
    ws = settings.workspace
    return ws if ws.is_dir() else REPO


def clip(text: str) -> str:
    if len(text) <= MAX_OUT:
        return text
    head, tail = text[: MAX_OUT * 2 // 3], text[-MAX_OUT // 4:]
    return f"{head}\n…（{L('中间省略', 'omitted')} {len(text) - len(head) - len(tail)} {L('字', 'chars')}）…\n{tail}"


async def run(name: str, args: list[str], stdin: str | None, agent: str | None) -> str:
    cmd = command(name)
    if not cmd:
        raise ToolError(L(f"这台服务器上没有 {name} 这个功能", f"{name} isn't available on this server"))
    args = [str(a) for a in args or []]
    if sum(len(a) for a in args) + len(stdin or "") > MAX_ARGS or any("\x00" in a for a in args):
        raise ToolError(L("参数太长或者有非法字符", "Arguments too long or contain invalid characters"))
    guard(name, args)
    cwd = workdir(agent)
    env = dict(os.environ, PYTHONIOENCODING="utf-8", MOUSSE_MCP="1")
    env.pop("MOUSSE_AGENT", None)
    if agent:
        env["MOUSSE_AGENT"] = agent
    timeout = BRIDGES[name][3]
    async with PARALLEL:
        proc = await asyncio.create_subprocess_exec(
            *cmd, *args, cwd=str(cwd), env=env, start_new_session=True,
            stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(proc.communicate(stdin.encode("utf8") if stdin is not None else None), timeout)
        except asyncio.TimeoutError:
            try:
                os.killpg(proc.pid, signal.SIGKILL)  # 连它起的子进程一起
            except ProcessLookupError:
                pass
            await proc.wait()
            raise ToolError(L(f"{name} 超过 {timeout} 秒没跑完，停掉了", f"{name} didn't finish within {timeout} seconds and was stopped")) from None
    text_out = out.decode("utf8", "replace").strip()
    text_err = err.decode("utf8", "replace").strip()
    if proc.returncode != 0:
        why = text_err or text_out or L("（没有输出）", "(no output)")
        raise ToolError(clip(L(f"退出码 {proc.returncode}：", f"Exit code {proc.returncode}: ") + why))
    if text_err and not text_out:
        return clip(text_err)
    return clip(text_out or L("（完成，没有输出）", "(done, no output)"))


def build() -> FastMCP:
    mcp = FastMCP(
        "openmousse",
        instructions=L(
            f"{settings.app_name} 的功能：看板和数据表、收件箱（要用户点头的事）、目标、项目、日程、Agent、日志、世界树。"
            "每个工具就是 skills 里那条命令：args 放脚本名后面的词（一个词一项），标准输入的内容放 input。"
            "要用户点头的事先交 inbox；用户明确让做、能撤回的直接做。",
            f"{settings.app_name}'s features: boards and tables, the inbox (things that need the user's OK), goals, projects, the schedule, "
            "Agents, the journal and the memory tree. Each tool is the command from the skills: put the words after the script name in args "
            "(one word per item) and anything meant for standard input in input. Things that need the user's OK go to the inbox first; "
            "what the user explicitly asked for and can be undone, just do."),
        stateless_http=True, json_response=True, streamable_http_path="/",
        # 令牌在前面挡着，DNS 重绑定拿不到令牌；地址又有 loopback / Tailscale / Funnel 好几种，不按 Host 过滤
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))
    args_help = L("命令行参数：skills 里这条命令脚本名后面的词，一个词一项（不经 shell，不用加引号；JSON 整段放一项）",
                  "Command-line arguments: the words after the script name in the skill's command, one word per item "
                  "(no shell, no quoting; a whole JSON value is one item)")
    input_help = L("要从标准输入给脚本的内容：命令里要读文件的参数（--file、--detail-file、--board-file…）一律写 -，内容放这里；--stdin 也是。没有就不填",
                   "Content for the script's standard input: write - for any option that reads a file (--file, --detail-file, --board-file…) "
                   "and put the content here; same for --stdin. Leave out otherwise")
    agent_help = L("你是哪个 Agent（它的 id，比如 diet）：命令就在它的工作区里跑，和它自己在 shell 里跑一样，不用再写 --agent。主对话不填",
                   "Which Agent you are (its id, e.g. diet): the command runs in that Agent's workspace, as if it ran it in its own shell, "
                   "so no --agent is needed. Leave empty in the main chat")

    def add(name: str) -> None:
        _, zh, en, _ = BRIDGES[name]

        # 类型写成纯 str：FastMCP 会把「看着像 JSON」的字符串参数先解析掉，除非参数就是 str（input 常常就是一段 JSON）
        async def tool(args: Annotated[list[str] | str, Field(description=args_help)],
                       input: Annotated[str, Field(description=input_help)] = "",  # noqa: A002  MCP 参数名
                       agent: Annotated[str, Field(description=agent_help)] = "",
                       ctx: Context | None = None) -> str:
            req = ctx.request_context.request if ctx and ctx.request_context else None
            who = ((req.scope.get("state") or {}).get("mousse_mcp") if req is not None else None) or {}
            bound, asked = who.get("agent"), agent.strip()
            if bound and asked and asked != bound:
                raise ToolError(L(f"这把令牌是 {bound} 的，不能替 {asked} 做事", f"This token belongs to {bound}; it can't act for {asked}"))
            if isinstance(args, str):  # 有的模型把整条命令当一个字符串交上来：按 shell 的规矩切开（不执行 shell）
                try:
                    args = shlex.split(args)
                except ValueError as e:
                    raise ToolError(L(f"args 切不开：{e}；请给一个列表", f"Couldn't split args: {e}; pass a list")) from None
            return await run(name, args, input if input != "" else None, bound or asked or None)

        mcp.add_tool(tool, name=name, description=L(zh, en), structured_output=False)

    for name in BRIDGES:
        if command(name):
            add(name)
    # 只有工具：FastMCP 默认也挂着 prompts / resources 的处理器，于是宣称有这两样，OpenClaw 这类客户端会为此
    # 多生成 prompts_list、resources_read 等 4 个空工具，白占每轮的上下文。拿掉处理器，就不宣称了。
    for req in (types.ListPromptsRequest, types.GetPromptRequest, types.ListResourcesRequest, types.ReadResourceRequest,
                types.ListResourceTemplatesRequest):
        mcp._mcp_server.request_handlers.pop(req, None)
    extend(mcp._mcp_server)
    return mcp


def extend(low) -> None:
    """EXTRA 的工具接在后面：包一层低层的 tools/list、tools/call。这 13 个照旧走 FastMCP 自己的处理器（参数校验、ToolError 都一样）。"""
    base_list, base_call = low.request_handlers[types.ListToolsRequest], low.request_handlers[types.CallToolRequest]

    def who() -> dict:
        try:
            req = low.request_context.request
        except LookupError:
            return {}
        return ((req.scope.get("state") or {}).get("mousse_mcp") if req is not None else None) or {}

    async def list_tools(req):
        res = await base_list(req)
        for tools, _ in EXTRA:
            try:
                res.root.tools.extend(await tools(who()))
            except Exception:  # noqa: BLE001 — 加上来的工具出了错，这 13 个照常
                pass
        return res

    async def call_tool(req):
        if req.params.name not in BRIDGES:
            for _, call in EXTRA:
                try:
                    got = await call(req.params.name, dict(req.params.arguments or {}), who())
                except Exception as e:  # noqa: BLE001
                    got = types.CallToolResult(content=[types.TextContent(type="text", text=str(e) or type(e).__name__)], isError=True)
                if got is not None:
                    return types.ServerResult(got)
        return await base_call(req)

    low.request_handlers[types.ListToolsRequest] = list_tools
    low.request_handlers[types.CallToolRequest] = call_tool


server = build()
_app = server.streamable_http_app()


class Gate:
    """/mcp 和 /mcp/<令牌> 的入口：认令牌，把是谁放进 scope，转给 MCP。"""

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return
        rest = scope["path"][len("/mcp"):].strip("/") if scope["path"].startswith("/mcp") else ""
        token = rest
        if not token:
            headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers") or []}
            auth = headers.get("authorization", "")
            token = auth[7:].strip() if auth.lower().startswith("bearer ") else headers.get("x-api-key", "").strip()
        if "/" in token:
            return await JSONResponse({"error": "not found"}, status_code=404)(scope, receive, send)
        who = identify(token)
        if who is None:
            return await JSONResponse({"error": "unauthorized"}, status_code=401, headers={"WWW-Authenticate": "Bearer"})(scope, receive, send)
        state = dict(scope.get("state") or {}, mousse_mcp={"token": who[0], "agent": who[1]})
        inner = dict(scope, path="/", raw_path=b"/", root_path="", state=state)
        await _app(inner, receive, send)


gate = Gate()
