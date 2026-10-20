"""工具多了按需加载（第 3 批第 10 项，2026-10-19）：tool_search / tool_describe / tool_call（照 OpenClaw docs/tools/tool-search.md 的三件，
它叫 dispatch_action 的那件这里叫 tool_call）。

开关 engine.tools.search：true（门槛 THRESHOLD）或 {"threshold": 20}；不写 / false = 关（照旧全给，一字不变）。
开着、这个 Agent 这一轮的 MCP 工具（engine/mcp_client.py）和连接器工具（apps.py）加起来多过门槛：这些工具不直接交给模型，换成三个：
  tool_search(query, limit?)   按名字和说明找（全部的名字 + 一句也写在它的说明里：工具目录，按名字排，最多 DIR_MAX 字，放不下的只列名字）
  tool_describe(name)          这个工具的完整说明和参数（JSON Schema）
  tool_call(name, arguments)   调它：照样过那个工具自己的分类和关卡——分类照它的（read 直接做、ask 交它自己的卡，同意了照它自己的路做一次）；
                               engine.tools.levels 写 off 的本来就不在里面
别的工具照旧直接给（内核自己的、手、记忆……）。一轮里工具表不变：这一轮开始时（tools.for_agent）算好，目录也是那时定的，提示缓存不破；
loop.py 不动（tools.for_agent 末尾多一行 compact）。MCP 服务改了工具表，下一轮的目录跟着变。
参考（只读）：OpenClaw docs/tools/tool-search.md（三件、目录按名字排、18000 字上限、调到的工具照样过它自己的策略和审批）。只借设计。
"""
from __future__ import annotations

import json
import re

from i18n import L

from .loop import Tool, ToolCtx, ToolFailure, check_args

THRESHOLD = 30
DIR_MAX = 18_000           # 目录最多多少字（OpenClaw 的上限）
LINE_MAX = 140             # 目录里每个工具的那一句最多多少字
SEARCH_LIMIT, SEARCH_MAX = 8, 25
NAMES = ("tool_search", "tool_describe", "tool_call")
READ = "read"


def cfg() -> dict | None:
    """engine.tools.search 的设置：开着 → {"threshold": n}；关着 → None。"""
    from config import raw
    e = raw().get("engine")
    t = e.get("tools") if isinstance(e, dict) else None
    v = t.get("search") if isinstance(t, dict) else None
    if v is True:
        return {"threshold": THRESHOLD}
    if isinstance(v, dict) and v.get("enabled", True) is not False:
        t = v.get("threshold")
        return {"threshold": t if isinstance(t, int) and not isinstance(t, bool) and t >= 0 else THRESHOLD}
    return None


def hideable(me: str) -> set[str]:
    """这个 Agent 这一轮的 MCP 工具和连接器工具的名字（会被收起来的那几个）。"""
    out: set[str] = set()
    try:
        from . import mcp_client
        out |= set(mcp_client.exposed(me))
    except Exception:  # noqa: BLE001 — MCP 不可用：没有它们
        pass
    try:
        from .tools import connector_tools
        out |= {t.name for t in connector_tools(me)}
    except Exception:  # noqa: BLE001
        pass
    return out


def compact(me: str, tools: list[Tool]) -> list[Tool]:
    """tools.for_agent 的最后一步：开着、MCP 和连接器的工具多过门槛 → 它们换成三个；不然原样。"""
    c = cfg()
    if c is None:
        return tools
    names = hideable(me)
    hidden = [t for t in tools if t.name in names]
    if len(hidden) <= c["threshold"]:
        return tools
    keep = [t for t in tools if t.name not in names]
    return keep + make(hidden)


def one_line(t: Tool) -> str:
    first = next((ln.strip() for ln in str(t.description or "").splitlines() if ln.strip()), "")
    return first if len(first) <= LINE_MAX else first[: LINE_MAX - 1] + "…"


def directory(hidden: list[Tool]) -> str:
    """名字 + 一句，按名字排，最多 DIR_MAX 字；放不下的只列名字，再放不下就说还有几个（tool_search 照样找得到）。"""
    rows = sorted(hidden, key=lambda t: t.name)
    out, used, i = [], 0, 0
    for i, t in enumerate(rows):
        line = f"- {t.name}: {one_line(t)}"
        if used + len(line) + 1 > DIR_MAX * 3 // 4:
            break
        out.append(line)
        used += len(line) + 1
    else:
        i = len(rows)
    rest = rows[i:]
    names = []
    for t in rest:
        if used + len(t.name) + 2 > DIR_MAX:
            break
        names.append(t.name)
        used += len(t.name) + 2
    if names:
        out.append("More (search for their descriptions): " + ", ".join(names))
    if len(rest) > len(names):
        out.append(f"…and {len(rest) - len(names)} more: use tool_search.")
    return "\n".join(out)


def score(t: Tool, words: list[str]) -> int:
    name, desc = t.name.lower(), str(t.description or "").lower()
    s = 0
    for w in words:
        if w in name:
            s += 3
        if w in desc:
            s += 1
    return s


def make(hidden: list[Tool]) -> list[Tool]:
    """收起来的这些 → 三个工具（闭包里就是这一轮的这一份：一轮里不变）。"""
    by_name = {t.name: t for t in hidden}

    def target(name: object) -> Tool:
        t = by_name.get(str(name or "").strip())
        if t is None:
            raise ToolFailure(L(f"没有 {name} 这个工具：用 tool_search 找", f"There's no tool {name}: find it with tool_search"))
        return t

    async def search(args: dict, ctx: ToolCtx) -> str:
        q = " ".join(str(args.get("query") or "").split())
        words = [w for w in re.split(r"[\s_\-./]+", q.lower()) if w]
        if not words:
            raise ToolFailure(L("要写 query", "Give a query"))
        n = max(1, min(SEARCH_MAX, int(args.get("limit") or SEARCH_LIMIT)))
        hits = sorted(((score(t, words), t.name, t) for t in hidden), key=lambda x: (-x[0], x[1]))
        hits = [t for s, _, t in hits if s > 0][:n]
        if not hits:
            return L(f"没找到和「{q}」有关的工具（一共 {len(hidden)} 个，目录在 tool_search 的说明里）", f'No tool matches "{q}" ({len(hidden)} in all; '
                     "the directory is in tool_search's description)")
        return "\n".join(f"- {t.name}: {one_line(t)}" for t in hits) + "\nUse tool_describe for the parameters, then tool_call."

    async def describe(args: dict, ctx: ToolCtx) -> str:
        t = target(args.get("name"))
        return f"{t.name}\n\n{t.description}\n\nParameters (JSON Schema):\n" + json.dumps(t.schema, ensure_ascii=False, indent=1)[:20_000]

    def inner(a: dict) -> tuple[Tool, dict]:
        """被调的那个和它的参数：照 loop 给每个工具的那样先修（prepare）再按它的 schema 查；分类、交卡、跑都用这一份。"""
        t = target(a.get("name"))
        v = a.get("arguments")
        if isinstance(v, str):
            try:
                v = json.loads(v or "{}")
            except ValueError:
                raise ToolFailure(L("arguments 要是一个 JSON 对象", "arguments must be a JSON object")) from None
        v = v if isinstance(v, dict) else {}
        if t.prepare is not None:
            try:
                v = t.prepare(v)
            except Exception:  # noqa: BLE001 — 修不了就原样交给下面查
                pass
        fixed, problem = check_args(v, t.schema)
        if problem:
            raise ToolFailure(f"Not run: {problem} in arguments for {t.name}. Check tool_describe and call again.")
        return t, fixed

    def prepare(raw):
        """arguments 写成了一段 JSON 字符串（有的模型这样）：先拆开，不然 loop 按 schema 查就挡了。"""
        if isinstance(raw, dict) and isinstance(raw.get("arguments"), str):
            try:
                v = json.loads(raw["arguments"] or "{}")
            except ValueError:
                return raw
            if isinstance(v, dict):
                return {**raw, "arguments": v}
        return raw

    async def call(args: dict, ctx: ToolCtx):
        t, a = inner(args)
        return await t.run(a, ctx)

    def level(args: dict) -> tuple[str, str]:
        try:
            t, a = inner(args)
        except ToolFailure:
            return READ, "unknown tool or bad arguments: the call fails without doing anything"
        return t.level(a)

    def title(args: dict) -> str:
        try:
            t, a = inner(args)
            return (t.title(a) if t.title else t.name)[:80]
        except Exception:  # noqa: BLE001
            return str(args.get("name") or "")[:80]

    async def ask_user(args: dict, ctx: ToolCtx):
        """关卡说 ask（分类照的是被调的那个）：交那个工具自己的卡，和直接调它时一样；同意了照它自己的路做一次。"""
        from . import tools as tools_mod
        t, a = inner(args)
        if (hand := tools_mod.HANDS.get(t.name)) is not None:
            return await hand.ask(t, a, ctx)
        if (asker := getattr(t, "ask_user", None)) is not None:
            return await asker(a, ctx)
        return ("Not run: this can't be undone and there's no approval card for it yet. The user has NOT consented. Do not retry it or try to "
                "reach it another way; tell the user what you wanted to do.")

    timeout = max([t.timeout for t in hidden] + [120])
    out_max = max([t.max_output for t in hidden] + [20_000])
    dir_text = directory(hidden)
    call_tool = Tool(name="tool_call", kinds=("owner",), run=call, classify=level, title=title, prepare=prepare, timeout=timeout,
                     max_output=out_max,
                     description=L("调一个收起来的工具：name = 它的名字，arguments = 它的参数（照 tool_describe 的 Schema）。照样过那个工具自己的规矩："
                                   "要用户点头的会交卡。", "Run one of the tools kept behind tool_search: name = its name, arguments = its parameters (per "
                                   "tool_describe's schema). It goes through that tool's own rules: ones that need the user's OK send a card."),
                     schema={"type": "object", "required": ["name"], "properties": {
                         "name": {"type": "string"},
                         "arguments": {"type": "object", "description": L("那个工具的参数", "That tool's parameters")}}})
    call_tool.ask_user = ask_user  # type: ignore[attr-defined] — tools.gate 认这个（同 MCP 工具）
    return [
        Tool(name="tool_search", kinds=("owner",), run=search, classify=lambda a: (READ, "only searches the tool directory"),
             description=L(f"按名字和说明找工具（{len(hidden)} 个 MCP 和连接器的工具收在这里，没直接给你）。找到以后 tool_describe 看参数，tool_call 调。"
                           "下面是全部的目录（名字：一句）：\n", f"Find a tool by name or description ({len(hidden)} MCP and connector tools are kept "
                           "here instead of being given to you directly). Then tool_describe shows its parameters and tool_call runs it. The full "
                           "directory (name: one line):\n") + dir_text,
             schema={"type": "object", "required": ["query"], "properties": {
                 "query": {"type": "string", "description": L("要做的事、工具名的一部分", "What you want to do, or part of a tool name")},
                 "limit": {"type": "integer", "description": L(f"最多几个（默认 {SEARCH_LIMIT}）", f"At most this many (default {SEARCH_LIMIT})")}}}),
        Tool(name="tool_describe", kinds=("owner",), run=describe, classify=lambda a: (READ, "only shows a tool's description"),
             description=L("看一个工具（tool_search 找到的）的完整说明和参数。", "Show the full description and parameters of a tool (one tool_search found)."),
             schema={"type": "object", "required": ["name"], "properties": {"name": {"type": "string"}}}),
        call_tool,
    ]
