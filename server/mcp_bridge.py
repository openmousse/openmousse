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
连接器（apps.py）：你连上的第三方应用的工具也从这里给出去（<应用 id>__<工具名>），接在这 14 个后面；每次列工具时现查，连上、断开、
改权限都不用重启。它们经 EXTRA 挂进来，这 14 个工具本身不经过它。
内核发给外援的临时令牌（内核第 11 项，engine/mcp_engine.py；第 5 项后台任务的外援也改用它）：自己的内核把一轮交给本机用户自己登录的
Claude Code / Codex 时，发一把只给这一轮的令牌（只在内存里，这一轮完了就收回；不在 server.json 里）。Gate 认出是它，就交给
engine/mcp_engine.py 的那个 MCP 服务：工具是内核的那张表、关卡是内核的那一套，和这里的 14 个工具不混；server.json 的令牌照旧只到这里。
"""
import secrets
import shlex
from typing import Annotated, Awaitable, Callable

from mcp import types
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import Field
from starlette.responses import JSONResponse

import bridges
from bridges import BRIDGES, command
from config import settings
from i18n import L

# 工具表、起子进程那一套在 bridges.py（2026-10-16 拆出来，自己的内核 engine/tools.py 也用它）；这里只把它的错换成 MCP 的 ToolError。
# 别的模块加到 /mcp 上的工具（连接器 apps.py）：一项 = (tools, call)。tools(who) → [types.Tool]，每次列工具时现问；
# call(工具名, 参数, who) → types.CallToolResult，不是它的工具回 None。who = {"token": 令牌名, "agent": 令牌绑的 Agent 或 None}。
EXTRA: list[tuple[Callable[[dict], Awaitable[list[types.Tool]]], Callable[[str, dict, dict], Awaitable[types.CallToolResult | None]]]] = []


def identify(token: str) -> tuple[str, str | None] | None:
    """令牌 → (令牌名, 绑的 Agent 或 None)。只认 mcp、mcp-<id>。"""
    if not token:
        return None
    for name, tok in settings.tokens().items():
        if (name == "mcp" or name.startswith("mcp-")) and tok and secrets.compare_digest(tok, token):
            return name, (name[4:] or None) if name.startswith("mcp-") else None
    return None


def engine_app(token: str):
    """是内核发给外援的临时令牌（engine/mcp_engine.py，一轮一把、只在内存里）→ 那边的 ASGI 服务；不是 → None。
    内核没加载就不是（OpenClaw 那边没有这种令牌）。"""
    import sys
    mod = sys.modules.get("engine.mcp_engine")
    if mod is None or not token:
        return None
    try:
        return mod.app if mod.lookup(token) is not None else None
    except Exception:  # noqa: BLE001
        return None


async def run(name: str, args: list[str], stdin: str | None, agent: str | None) -> str:
    token = bridges.VIA.set("mcp")  # 外面的 MCP 客户端：脚本再请求服务器（转交、触发）时带上，自己的内核不让这样引出来的回合花 ChatGPT 套餐
    try:
        return await bridges.run(name, args, stdin, agent)
    except bridges.BridgeError as e:
        raise ToolError(str(e)) from None
    finally:
        bridges.VIA.reset(token)


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
    """EXTRA 的工具接在后面：包一层低层的 tools/list、tools/call。这 14 个照旧走 FastMCP 自己的处理器（参数校验、ToolError 都一样）。"""
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
            except Exception:  # noqa: BLE001 — 加上来的工具出了错，这 14 个照常
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
        if who is None and (engine := engine_app(token)) is not None:  # 内核发给外援的令牌：交给 engine/mcp_engine.py（这 14 个工具不经过它）
            state = dict(scope.get("state") or {}, mousse_engine=token)
            return await engine(dict(scope, path="/", raw_path=b"/", root_path="", state=state), receive, send)
        if who is None:
            return await JSONResponse({"error": "unauthorized"}, status_code=401, headers={"WWW-Authenticate": "Bearer"})(scope, receive, send)
        state = dict(scope.get("state") or {}, mousse_mcp={"token": who[0], "agent": who[1]})
        inner = dict(scope, path="/", raw_path=b"/", root_path="", state=state)
        await _app(inner, receive, send)


gate = Gate()
