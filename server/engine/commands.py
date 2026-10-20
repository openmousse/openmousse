"""斜杠命令（第 2 批，10/19）：对话里打 /名字 …，在这句话交给内核之前就办掉；命令和回答记进 app 的对话，不进发给模型的会话。

两种对话都能用：内核自己跑的（各家接口的模型）和整轮交给用户自己的 Claude Code 跑的（claude-code/…，它自己的斜杠命令照旧关着，
  --disable-slash-commands）。命令在我们这边、话交给模型之前，跟这一轮谁在跑无关；每条命令写明哪种对话能用（Command.kinds：
  engine / claude-code）。
借的设计（10/19 读的源码，只借设计）：OpenClaw docs/tools/slash-commands.md（命令要整句以 / 开头、只认主人、命令和回答不进模型的
  会话、/stop /status 随时能用）、Hermes hermes_cli/commands.py 的 COMMAND_REGISTRY（一条命令 = 名字、别名、参数写法、一句说明、
  哪里能用；网关里改会话的那几条要等这一轮停）。

怎么算一条命令（parse）：整句以 /名字 开头（名字字母打头，字母、数字、- 和 _，不分大小写，最长 40），后面空白隔开的都是参数。
  /path/to/x、/12 这种不算，照常发给模型。
只认主人：handle(who="owner") 才执行；别人的话（朋友、名片 Agent 收到的、以后渠道进来的陌生人）里的 /xxx 只是文字（handle 回 None，
  调用方照常处理）。../chat.py 只在用户自己在 app 里打的那一句（/api/chat/send，origin user、没带附件）上问这里；定时任务、转交、
  收件箱发进来的都不问。
不认识的：回一句「没有这个命令，/help 看有哪些」，不发给模型。
要不要等这一轮停（Command.idle）：改会话本身的（/new /compact /resume …）这一轮还在跑就回一句「等它说完，或者先 /stop」；
  /status /stop /help … 随时能用。
记录（../chat.py 的 command_reply）：命令那一句和回答都记进 app 的对话（messages.origin = command，/btw 的回答是 btw），回 SSE
  start → delta → done（和一条普通回复一样，老 app 照样画）；owner.seed（抄进新会话的当天记录）、context.tail_part（上次聊到哪）
  跳过这两种。命令本身要模型做事的（/compact 压一次、/btw 一次性问一句……）各自照它自己的规矩。

登记表（别的模块往里加：第 4 批的 /bg、第 5 批技能变成 /技能名、第 6 批渠道共用这一套）：
  register(Command(...))      加一条（同名的换掉；名字、别名不能和别的命令撞）；unregister(名字) 拿掉
  add_source(名字, fn)        动态的一组：fn(agent) → [Command]（技能这种，每个 Agent 不一样）；列表和执行时现问，不能盖过登记表里的
  handle(对话, 原话, who=…, agent=…, channel=…) → Reply | None：渠道（第 6 批）直接调它；None = 不是给这里的（没开、不是命令、
                              不是主人），照常处理；Reply.text 是给用户看的回答（Markdown），Reply.then 是回答记下以后在后台接着做的
  一条命令的入口：async run(Call) → Reply | str；Call 里有对话、Agent、参数、谁、哪个渠道、这个对话的模型、这一轮在不在跑。
server.json：engine.commands = true 开；{"off": ["btw", "goal"]} = 开、但这几条不要。不写 / false = 关（app 的提示也没有）。
"""
from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config import raw, settings
from i18n import L

KINDS = ("engine", "claude-code")   # 内核自己跑的 / 交给用户自己的 Claude Code 跑的
NAME = re.compile(r"^[/／]([A-Za-z][A-Za-z0-9_-]{0,39})(?=$|\s)\s*(.*)$", re.S)
router = APIRouter()


# —— 登记表 ——————————————————————————————————————————————————————————————————————

@dataclass
class Call:
    conversation: str             # 对话（app 的线程 id）
    agent: str                    # 这个对话归哪个 Agent
    name: str                     # 命令名（别名换成了正名）
    args: str                     # 后面的字（去掉前后空白）
    who: str = "owner"
    channel: str = "app"
    model: str = ""               # 这个对话现在选的模型
    kind: str = "engine"          # engine / claude-code
    busy: bool = False            # 这个对话这一轮还在跑
    ref: int | None = None        # app 里这条命令的消息 id


@dataclass
class Reply:
    text: str                     # 给用户看的（Markdown）
    ok: bool = True               # False：没办成（参数不对、不认识、这时候用不了）
    origin: str = "command"       # 记在回答那一行的 origin（command / btw）
    then: Callable[[], Awaitable[Any]] | None = None  # 回答记下以后在后台接着做的（开一轮、补写回……）
    files: list[dict] = field(default_factory=list)  # 挂在回答那一行上的附件（../files.py 的 attachments 里还没挂的那几行；/export 用）


@dataclass(frozen=True)
class Command:
    name: str
    run: Callable[[Call], Awaitable[Reply | str]]
    help: tuple[str, str]                          # 一句说明 (中文, English)
    args: tuple[str, str] | None = None            # 参数的写法 (中文, English)
    aliases: tuple[str, ...] = ()
    kinds: tuple[str, ...] = KINDS                 # 哪种对话能用
    idle: bool = False                             # 要等这一轮停
    source: str = "core"                           # 谁登记的
    hidden: bool = False                           # 不列出来（还能用）
    extra: dict = field(default_factory=dict, compare=False)


_registry: dict[str, Command] = {}
_sources: dict[str, Callable[[str], list[Command]]] = {}


def register(cmd: Command) -> None:
    """加一条命令（同名的换掉）。名字、别名不能和别的命令的名字、别名撞（撞了抛 ValueError）。"""
    name = cmd.name.lower()
    if not NAME.match("/" + name):
        raise ValueError(f"bad command name {cmd.name!r}")
    taken = {a: c.name for c in _registry.values() if c.name != name for a in (c.name, *c.aliases)}
    for a in (name, *cmd.aliases):
        if a.lower() in taken:
            raise ValueError(f"/{a} is already /{taken[a.lower()]}")
    _registry[name] = cmd if cmd.name == name else Command(**{**cmd.__dict__, "name": name})


def unregister(name: str) -> None:
    _registry.pop(name.lower(), None)


def add_source(name: str, fn: Callable[[str], list[Command]]) -> None:
    """动态的一组命令：fn(agent) → [Command]（每次列表、执行时现问；出错当没有）。同名的换掉。"""
    _sources[name] = fn


def remove_source(name: str) -> None:
    _sources.pop(name, None)


def _dynamic(agent: str) -> list[Command]:
    out: list[Command] = []
    for src, fn in list(_sources.items()):
        try:
            out += [c for c in fn(agent) or [] if isinstance(c, Command)]
        except Exception as e:  # noqa: BLE001 — 一组命令列不出来不拖累别的
            print(f"[engine] 命令「{src}」列不出来：{type(e).__name__}: {str(e)[:120]}")
    return out


def find(name: str, agent: str = "main") -> Command | None:
    """名字或别名 → 命令（登记表优先，再看动态的那几组）。"""
    name = name.lower()
    for pool in (list(_registry.values()), _dynamic(agent)):
        for c in pool:
            if name == c.name or name in c.aliases:
                return c
    return None


def listed(agent: str = "main", kind: str | None = None) -> list[Command]:
    """列给人看的（/help、app 的提示）：没关的、没藏的、这种对话能用的；登记表的在前，动态的在后（盖不过登记表里的名字）。"""
    off = turned_off()
    seen: set[str] = set()
    out: list[Command] = []
    for c in [*_registry.values(), *_dynamic(agent)]:
        if c.name in seen or c.hidden or c.name in off or (kind and kind not in c.kinds):
            continue
        seen.update((c.name, *c.aliases))
        out.append(c)
    return out


# —— 设置 ————————————————————————————————————————————————————————————————————————

def cfg() -> Any:
    e = raw().get("engine")
    return e.get("commands") if isinstance(e, dict) else None


def enabled() -> bool:
    """engine.commands 写了 true 或一个 {…}（不是 {"enabled": false}），而且 claw 是 builtin。"""
    import claw
    c = cfg()
    return claw.is_builtin() and (c is True or (isinstance(c, dict) and c.get("enabled", True) is not False))


def turned_off() -> set[str]:
    c = cfg()
    off = c.get("off") if isinstance(c, dict) else None
    return {str(x).strip().lstrip("/").lower() for x in off} if isinstance(off, list) else set()


def parse(text: str) -> tuple[str, str] | None:
    """整句是不是一条命令 → (名字（小写）, 参数)；不是 → None。"""
    m = NAME.match((text or "").strip())
    return (m.group(1).lower(), m.group(2).strip()) if m else None


# —— 这个对话 ————————————————————————————————————————————————————————————————————

def current_model(conversation: str) -> str:
    """这个对话现在选的模型（threads.model，没选过用 claw 的默认）。"""
    import chat
    with chat._lock, chat.db() as conn:
        return chat.thread_model(conn, conversation)


def kind_of(model: str | None) -> str:
    from . import models
    return "claude-code" if models.subscription(model) == "claude-code" else "engine"


def busy(conversation: str) -> bool:
    try:
        import chat
        return chat.busy(conversation)
    except Exception:  # noqa: BLE001 — 命令行里导入不了 chat
        return False


def today(conversation: str) -> str:
    """这个对话现在是哪个逻辑日（会话按它分）。"""
    import chat
    return chat.day_of(chat.now_iso())


def session_now(conversation: str):
    """这个对话现在的会话（今天的那条；还没说过话 = None）。"""
    from .session import DbSession, day_key
    return DbSession.latest(day_key(conversation, today(conversation)))


def title_of(conversation: str) -> str:
    try:
        from . import sessions
        return sessions.names().get(conversation) or conversation
    except Exception:  # noqa: BLE001
        return conversation


async def handle(conversation: str, text: str, *, who: str = "owner", agent: str | None = None, channel: str = "app",
                 ref: int | None = None, is_busy: bool | None = None, model: str | None = None) -> Reply | None:
    """一句话是不是给这里的命令：是就办掉 → Reply；不是（没开、不是命令、不是主人说的）→ None，调用方照常处理。"""
    if who != "owner" or not enabled():
        return None
    p = parse(text)
    if p is None:
        return None
    name, args = p
    from . import owner
    agent = agent or owner.agent_of(conversation)
    cmd = find(name, agent)
    if cmd is None or cmd.name in turned_off():
        return Reply(L(f"没有 /{name} 这个命令，/help 看有哪些。", f"There's no /{name} command. /help lists the ones there are."), ok=False)
    model = model or current_model(conversation)
    kind = kind_of(model)
    if kind not in cmd.kinds:
        return Reply(L(f"/{cmd.name} 在这个对话里用不了（它现在由 Claude Code 跑）。", f"/{cmd.name} doesn't work in this chat (Claude Code runs it now)."),
                     ok=False)
    is_busy = busy(conversation) if is_busy is None else is_busy
    if cmd.idle and is_busy:
        return Reply(L("还在回复：等它说完，或者先 /stop。", "A reply is still running: wait for it to finish, or /stop it first."), ok=False)
    call = Call(conversation=conversation, agent=agent, name=cmd.name, args=args, who=who, channel=channel, model=model, kind=kind,
                busy=is_busy, ref=ref)
    out = await cmd.run(call)
    return out if isinstance(out, Reply) else Reply(str(out))


# —— 写成字 ——————————————————————————————————————————————————————————————————————

def k(n: int | float | None) -> str:
    """token 数：1234 → 1.2k，1234567 → 1.23M。"""
    n = int(n or 0)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def money(v: float | None) -> str:
    return "—" if v is None else f"${v:.4f}" if v < 1 else f"${v:.2f}"


def when(ts: str | None) -> str:
    try:
        return datetime.fromisoformat(str(ts)).astimezone(settings.tz).strftime("%H:%M")
    except (TypeError, ValueError):
        return ""


def first_line(text: str, limit: int = 90) -> str:
    """工具说明的第一句（到第一个句号），长了截断。"""
    t = " ".join((text or "").split())
    m = re.search(r"。|\.(?:\s|$)", t)
    t = t[: m.start() + 1] if m else t
    return t if len(t) <= limit else t[: limit - 1] + "…"


def usage_line(u: dict) -> str:
    parts = [L(f"进 {k(u.get('in'))}", f"{k(u.get('in'))} in"), L(f"出 {k(u.get('out'))}", f"{k(u.get('out'))} out")]
    if u.get("cache_read"):
        parts.append(L(f"缓存读 {k(u.get('cache_read'))}", f"{k(u.get('cache_read'))} cached"))
    cost = u.get("cost")
    if isinstance(cost, (int, float)) and not isinstance(cost, bool) and cost:
        parts.append(money(float(cost)))
    return " · ".join(parts)


# —— 第一批：不花钱的 ———————————————————————————————————————————————————————————————

async def _help(c: Call) -> Reply:
    lines = [L("**命令**（只有你自己在 app 里打的才算；别人发来的 /… 只是文字）",
               "**Commands** (only what you type yourself counts; a /… from anyone else is just text)")]
    for cmd in listed(c.agent, c.kind):
        usage = f"/{cmd.name}" + (f" {L(*cmd.args)}" if cmd.args else "")
        alias = L(f"（也可以 {'、'.join('/' + a for a in cmd.aliases)}）", f" (or {', '.join('/' + a for a in cmd.aliases)})") if cmd.aliases else ""
        lines.append(f"- `{usage}` — {L(*cmd.help)}{alias}")
    return Reply("\n".join(lines))


def _session_facts(conversation: str) -> dict:
    """这个对话今天这条会话：几轮、压缩过几次、什么时候开的。"""
    from . import store
    s = session_now(conversation)
    if s is None:
        return {}
    path = s.path()
    with store.read() as conn:
        n = conn.execute("SELECT COUNT(*) FROM engine_turns WHERE session=?", (s.id,)).fetchone()[0]
        r = conn.execute("SELECT created_at FROM engine_sessions WHERE id=?", (s.id,)).fetchone()
    return {"session": s, "turns": n, "compactions": sum(1 for x in path if x["kind"] == "compaction"),
            "since": r["created_at"] if r else None}


async def _status(c: Call) -> Reply:
    from . import sessions
    facts = await asyncio.to_thread(_session_facts, c.conversation)
    st = await asyncio.to_thread(sessions.status_of, c.conversation, facts.get("session"))
    head = f"**{title_of(c.conversation)}** · `{c.model}`" + (L("（Claude Code 跑）", " (on Claude Code)") if c.kind == "claude-code" else "")
    lines = [head]
    from . import think
    th = await asyncio.to_thread(think.resolve, c.conversation, c.agent, c.model)
    if th["want"]:  # 设了思考档位（/think、engine.think）才列
        lines.append(L("- 思考档位：", "- Thinking: ") + think_line(th, c.model, c.kind))
    if st.get("running"):
        lines.append(L(f"- 正在跑：{st.get('doing') or '开始了'}", f"- Running: {st.get('doing') or 'starting'}"))
    else:
        lines.append(L("- 没在跑", "- Not running"))
    waits = st.get("waitingFor") or []
    if waits:
        lines.append(L("- 在等：", "- Waiting for: ") + "; ".join(_waiting(w) for w in waits))
    if facts:
        bits = [L(f"{facts['turns']} 轮", f"{facts['turns']} turns")]
        if facts.get("since"):
            bits.insert(0, L(f"{when(facts['since'])} 开的", f"since {when(facts['since'])}"))
        if facts["compactions"]:
            bits.append(L(f"压缩过 {facts['compactions']} 次", f"compacted {facts['compactions']}×"))
        lines.append(L("- 今天这条会话：", "- Today's session: ") + " · ".join(bits))
    else:
        lines.append(L("- 今天还没有会话（下一句话开一条）", "- No session yet today (your next message starts one)"))
    last = st.get("lastTurn")
    if last:
        t = last.get("tokens") or {}
        u = {"in": t.get("input"), "out": t.get("output"), "cache_read": t.get("cacheRead")}
        lines.append(L(f"- 上一轮：{last.get('status')} · {last.get('model')} · ", f"- Last turn: {last.get('status')} · {last.get('model')} · ")
                     + usage_line(u) + (f" · {last['error']}" if last.get("error") else ""))
    return Reply("\n".join(lines))


def _waiting(w: dict) -> str:
    what = w.get("what")
    if what == "approval":
        return L(f"你点头（{w.get('title') or w.get('tool')}）", f"your OK ({w.get('title') or w.get('tool')})")
    if what == "background task":
        return L(f"后台任务「{w.get('label') or w.get('task')}」", f"background task \"{w.get('label') or w.get('task')}\"")
    if what == "the user's answer":
        return L(f"你的回答（{w.get('question')}）", f"your answer ({w.get('question')})")
    if what == "queued messages":
        return L(f"排着的 {w.get('count')} 句", f"{w.get('count')} queued messages")
    if what == "handoffs queued for it":
        return L(f"排队转进来的 {w.get('count')} 个问题", f"{w.get('count')} handoffs queued for it")
    if what == "an answer from another Agent":
        return L(f"{w.get('agent')} 的回答", f"an answer from {w.get('agent')}")
    return str(what)


def _usage_since(conversation: str, start: datetime) -> dict:
    """这个对话从 start 起的一轮轮（价钱照 usage.py 的规矩补）。"""
    from . import models, store, usage
    out = {"turns": 0, "in": 0, "out": 0, "cache_read": 0, "cost": 0.0, "plan": 0}
    if not store.exists():
        return out
    with store.read() as conn:
        rows = conn.execute("SELECT model, models, usage, started_at FROM engine_turns WHERE conversation=? AND julianday(started_at) >= julianday(?)",
                            (conversation, start.isoformat())).fetchall()
    for r in rows:
        try:
            u = json.loads(r["usage"] or "{}")
            called = json.loads(r["models"] or "[]")
        except ValueError:
            continue
        model = models.canonical(next((m for m in reversed(called) if isinstance(m, str) and m), None) or str(r["model"] or ""))
        u = usage.priced(model, u if isinstance(u, dict) else {}, r["started_at"])
        out["turns"] += 1
        for key in ("in", "out", "cache_read"):
            out[key] += int(u.get(key) or 0)
        if usage.billing_of(model, u) == "plan":
            out["plan"] += 1
        elif isinstance(u.get("cost"), (int, float)):
            out["cost"] += float(u["cost"])
    return out


async def _usage(c: Call) -> Reply:
    from . import usage
    start = datetime.now(settings.tz).replace(hour=0, minute=0, second=0, microsecond=0)
    mine = await asyncio.to_thread(_usage_since, c.conversation, start)
    day, week = await asyncio.to_thread(usage.summary, 1), await asyncio.to_thread(usage.summary, 7)

    def line(label: str, t: dict, plan: int) -> str:
        u = {"in": t.get("in"), "out": t.get("out"), "cache_read": t.get("cacheRead", t.get("cache_read")), "cost": t.get("cost")}
        return (L(f"- {label}：{t.get('turns', 0)} 轮 · ", f"- {label}: {t.get('turns', 0)} turns · ") + usage_line(u)
                + (L(f"（其中 {plan} 轮走套餐，不计钱）", f" ({plan} of them on a plan, not billed)") if plan else ""))
    lines = [L("**用量**（按日历日，和各家的账单对得上）", "**Usage** (calendar days, as the providers bill)"),
             line(L("这个对话今天", "This chat today"), mine, mine["plan"]),
             line(L("今天全部", "Everything today"), day["total"], day["total"].get("planTurns", 0)),
             line(L("最近 7 天", "Last 7 days"), week["total"], week["total"].get("planTurns", 0))]
    return Reply("\n".join(lines))


async def tools_of(agent: str) -> list:
    """这个 Agent 这一轮有哪些工具（和 owner.respond 给的一样：for_agent + app 对话的那几样，再照读写表筛）。"""
    from . import mcp_client, memgate
    from . import tools as tools_mod
    try:
        await mcp_client.ready(agent, None)
    except Exception:  # noqa: BLE001 — MCP 服务起不来：照旧列别的
        pass
    tools = await asyncio.to_thread(tools_mod.for_agent, agent)
    tools += tools_mod.chat_tools(agent, watched=True)
    return memgate.screen("owner", [t for t in tools if "owner" in t.kinds])


async def _tools(c: Call) -> Reply:
    tools = await tools_of(c.agent)
    lines = [L(f"**工具** · {title_of(c.agent)} 这一轮有 {len(tools)} 个", f"**Tools** · {len(tools)} for {title_of(c.agent)} this turn")]
    for t in tools:
        lines.append(f"- `{t.name}` — {first_line(t.description)}")
    if c.kind == "claude-code":
        lines.append(L("（Claude Code 经 MCP 用同一张表，同一套关卡）", "(Claude Code gets the same list over MCP, through the same approval gate)"))
    return Reply("\n".join(lines))


async def _context(c: Call) -> Reply:
    from . import compact, context, owner
    from .session import strip_thinking
    day = today(c.conversation)
    tools = await tools_of(c.agent)
    specs = [t.spec() for t in tools]
    ctx = await context.gather(c.agent, c.conversation, day, {t.name for t in tools})
    s = session_now(c.conversation)
    system = s.effective_system() if s is not None else owner.system_for(c.agent, tools, ctx)
    view = s.view() if s is not None else None
    window, limit = compact.window(c.model), compact.threshold(c.model)
    tool_t = compact.tokens(json.dumps(specs, ensure_ascii=False)) if specs else 0
    hist_t = sum(compact.msg_tokens(strip_thinking(m)) for m in view.messages) if view else 0
    summary_t = compact.tokens(view.summary or "") if view and view.summary else 0
    total = compact.estimate(view, system, specs, []) if view else compact.tokens(system) + tool_t

    def part(key: str) -> int:
        p = ctx.part(key)
        return compact.tokens(p.text) if p and p.text else 0
    files = [(p.title, compact.tokens(p.text)) for key in context.ORDER if (p := ctx.part(key)) and p.text]
    back = [(p.title, compact.tokens(p.text)) for key in context.BACKGROUND if (p := ctx.part(key)) and p.text]
    lines = [L(f"**上下文** · `{c.model}`", f"**Context** · `{c.model}`"),
             L(f"下一次发出去约 {k(total)} token，窗口 {k(window)}，超过 {k(limit)} 就压缩。",
               f"The next request is about {k(total)} tokens of a {k(window)} window; it compacts above {k(limit)}."),
             L(f"- 开头：{k(compact.tokens(system))}", f"- Opening: {k(compact.tokens(system))}"),
             L(f"  - 它是谁 {k(part('role'))} · 规矩 {k(compact.tokens(owner.rules(c.agent, tools)))} · 技能目录 {k(part('skills'))}",
               f"  - Who it is {k(part('role'))} · rules {k(compact.tokens(owner.rules(c.agent, tools)))} · skills {k(part('skills'))}")]
    if files:
        lines.append(L("  - 规矩文件和档案：", "  - Workspace files and profile: ") + ", ".join(f"{t} {k(n)}" for t, n in files))
    if back:
        lines.append(L("  - 今天的背景：", "  - Background: ") + ", ".join(f"{t} {k(n)}" for t, n in back))
    lines.append(L(f"- 工具：{len(specs)} 个，{k(tool_t)}", f"- Tools: {len(specs)}, {k(tool_t)}"))
    if view is not None:
        lines.append(L(f"- 对话：{len(view.messages)} 条，{k(hist_t)}", f"- Conversation: {len(view.messages)} messages, {k(hist_t)}")
                     + (L(f"（其中压缩成的摘要 {k(summary_t)}）", f" (the compaction summary is {k(summary_t)} of it)") if summary_t else ""))
    else:
        lines.append(L("- 对话：今天还没开始", "- Conversation: nothing yet today"))
    if c.kind == "claude-code":
        lines.append(L("（这个对话由 Claude Code 跑：它自己留着一份会话、自己压缩；这里是我们交给它的那份的估算）",
                       "(Claude Code runs this chat: it keeps its own copy of the session and compacts it itself; these are estimates of what "
                       "we hand it)"))
    return Reply("\n".join(lines))


async def _model(c: Call) -> Reply:
    import chat
    import claw
    choices = claw.models()
    if not c.args:
        return Reply(L(f"这个对话用 `{c.model}`。能换的：", f"This chat uses `{c.model}`. You can switch to: ")
                     + ", ".join(f"`{m}`" for m in choices) + L("。/model 名字 换。", ". /model <name> switches."))
    want = c.args.split()[0].strip("`")
    low = want.lower()
    hit = [m for m in choices if m.lower() == low] or [m for m in choices if m.lower().endswith("/" + low)] or \
        [m for m in choices if low in m.lower()]
    if len(hit) != 1:
        return Reply((L(f"`{want}` 对上了好几个：", f"`{want}` matches several: ") if hit else L(f"没有 `{want}` 这个模型。能换的：",
                                                                                           f"There's no model `{want}`. You can switch to: "))
                     + ", ".join(f"`{m}`" for m in (hit or choices)), ok=False)
    chosen = hit[0]
    if chosen == c.model:
        return Reply(L(f"已经在用 `{chosen}` 了。", f"Already using `{chosen}`."))
    chat.set_model(chat.ModelBody(thread=c.conversation, model=chosen))  # 和 /api/chat/model 一样：只换这个对话的
    return Reply(L(f"这个对话换成了 `{chosen}`（原来 `{c.model}`）", f"This chat now uses `{chosen}` (was `{c.model}`)")
                 + (L("，正在跑的这一轮照旧，下一轮起用它。", "; the reply that's running keeps its model, the next one uses it.") if c.busy else "."))


async def _stop(c: Call) -> Reply:
    import chat
    if not c.busy:
        return Reply(L("没有在跑的回复。", "Nothing is running."))
    got = await chat.stop(chat.StopBody(thread=c.conversation))
    return Reply(L("停了。", "Stopped.") if got.get("stopped") else L("没有在跑的回复。", "Nothing is running."))


async def _new(c: Call) -> Reply:
    from . import owner
    if session_now(c.conversation) is None and not c.args:
        return Reply(L("今天这个对话还没有会话：下一句话就开一条新的。", "There's no session in this chat today yet: your next message starts a new one."))
    _s, old = await owner.start_new(c.conversation, today(c.conversation), agent=c.agent, model=c.model, notes=c.args)
    said = [L("换了一条新会话。", "Started a new session.")]
    if c.args:
        said.append(L("开头带上了你写的要点。", "It starts with the notes you wrote."))
    elif old is not None:
        said.append(L("开头带上了刚才聊到哪，前面的话不再整段发给模型。", "It starts with where the last one left off; the earlier messages are no "
                      "longer sent in full."))
    if c.kind == "claude-code":
        said.append(L("Claude Code 下一句开一个新会话。", "Claude Code starts a new session with your next message."))
    return Reply(" ".join(said))


BEFORE = re.compile(r"^(?:#|db)(\d+)(?=$|\s)\s*(.*)$", re.S)  # /compact #123 关注点：从 app 里这条消息往前压


def compact_text(out: dict, kind: str, before: int | None = None) -> str:
    """手动压缩的结果 → 给用户看的一句。"""
    if out.get("ok"):
        said = L(f"压好了：前面 {out['turns']} 轮压成了一段摘要", f"Compacted: the {out['turns']} earlier turns are now a summary")
        if out.get("before") and out.get("after"):
            said += L(f"（约 {k(out['before'])} → {k(out['after'])} token）", f" (about {k(out['before'])} → {k(out['after'])} tokens)")
        said += L("。", ".")
        if kind == "claude-code":
            said += L(" Claude Code 下一句开一个新会话，开头带这份摘要。", " Claude Code starts a new session with your next message, opening with this "
                      "summary.")
        return said
    why = out.get("why")
    if why == "no_session":
        return L("今天这个对话还没有会话，没什么可压的。", "There's no session in this chat today, so there's nothing to compact.")
    if why == "nothing":
        return L("还没有可以压的：至少要两轮对话。", "Nothing to compact yet: it needs at least two exchanges.")
    if why == "not_found":
        return L(f"现在这条会话里没有那条消息（#{before}）。", f"That message (#{before}) isn't in the current session.")
    return L("没压成（摘要没写出来），对话原样不动；过一会儿再试。", "Compaction didn't work (the summary didn't come back); the conversation is "
             "unchanged. Try again in a while.")


async def _compact(c: Call) -> Reply:
    from . import owner
    before, focus = None, c.args
    if m := BEFORE.match(c.args):
        before, focus = int(m.group(1)), m.group(2).strip()
    out = await owner.compact_now(c.conversation, today(c.conversation), model=c.model, focus=focus, before=before)
    return Reply(compact_text(out, c.kind, before), ok=bool(out.get("ok")))


def think_line(r: dict, model: str, kind: str) -> str:
    """思考档位现在是什么样（/think、/status 用）。r = think.resolve(…)。"""
    if r["want"] is None:
        if kind == "claude-code":
            from . import claude_brain
            e = claude_brain.effort()
            return L(f"没设（Claude Code 照 engine.claude_code.effort：{e}）", f"not set (Claude Code uses engine.claude_code.effort: {e})") if e \
                else L("没设（用模型自己的默认）", "not set (the model's own default)")
        return L("没设（用模型自己的默认）", "not set (the model's own default)")
    src = L("这个对话设的", "set for this chat") if r["from"] == "chat" else L("这个 Agent 的默认", "this Agent's default")
    out = L(f"{r['want']}（{src}）", f"{r['want']} ({src})")
    if r["levels"] == ():
        out += L(f"；`{model}` 不分档，照它自己的默认", f"; `{model}` has no levels, so it uses its own default")
    elif r["send"] != r["want"]:
        out += L(f"；`{model}` 没有这一档，按 {r['send']} 发", f"; `{model}` doesn't have it, so it sends {r['send']}")
    return out


async def _think(c: Call) -> Reply:
    """/think [档位]（engine/think.py）：按对话存，下一轮起用；不写 = 看现在的；default = 清掉、跟回默认。"""
    from . import think
    word = c.args.strip()
    if word and word.lower() in think.CLEAR:
        think.set_thread(c.conversation, None)
        r = think.resolve(c.conversation, c.agent, c.model)
        return Reply(L("清掉了这个对话的思考档位，现在：", "Cleared this chat's thinking level. Now: ") + think_line(r, c.model, c.kind))
    if word:
        level = think.normalize(word)
        if level is None:
            return Reply(L(f"没有「{word}」这一档。能用的：{'、'.join(think.LEVELS)}；default = 跟回默认。",
                           f"There's no \"{word}\" level. Use one of {', '.join(think.LEVELS)}; default goes back to the default."), ok=False)
        think.set_thread(c.conversation, level)
    r = think.resolve(c.conversation, c.agent, c.model)
    head = L("这个对话的思考档位：", "This chat's thinking level: ") if not word else L("设好了，思考档位：", "Done. Thinking level: ")
    tail = L("正在跑的这一轮照旧，下一轮起用。", " The reply that's running keeps its level; the next one uses this.") if word and c.busy else ""
    return Reply(head + think_line(r, c.model, c.kind) + L("。", ".") + tail)


QUEUE_SAID = {
    None: ("插话：插进正在跑的这一轮，进不去的排着、完了合成一轮（默认）",
           "steer: joins the reply that's running; what can't get in waits and is answered together after it (the default)"),
    "steer": ("插话：插进正在跑的这一轮，进不去的排着、完了合成一轮", "steer: joins the reply that's running; what can't get in waits "
              "and is answered together after it"),
    "followup": ("排着：这一轮完了一句一句地回", "followup: waits, then each message gets its own reply after this one"),
    "collect": ("排着：这一轮完了合成一轮回", "collect: waits, then everything sent meanwhile is answered together after this one"),
    "interrupt": ("打断：停掉这一轮，马上回新的这一句", "interrupt: stops the reply that's running and answers the new message right away"),
}


async def _queue(c: Call) -> Reply:
    """/queue [steer|followup|collect|interrupt|default]（engine/queueing.py）：回复在跑的时候你又说一句，怎么办；按对话存。"""
    from . import queueing
    word = c.args.strip().lower()
    if not word:
        return Reply(L("这个对话回复在跑的时候你又说的话：", "While a reply is running, what you send in this chat: ")
                     + L(*QUEUE_SAID[queueing.mode_of(c.conversation)]) + L("。", "."))
    if word in queueing.CLEAR:
        queueing.set_mode(c.conversation, None)
        return Reply(L("回到默认：", "Back to the default: ") + L(*QUEUE_SAID[None]) + L("。", "."))
    if word not in queueing.MODES:
        return Reply(L(f"没有「{word}」这种。能用的：{'、'.join(queueing.MODES)}；default = 回到默认。",
                       f"There's no \"{word}\" mode. Use one of {', '.join(queueing.MODES)}; default goes back to the default."), ok=False)
    queueing.set_mode(c.conversation, word)
    return Reply(L("设好了：", "Done: ") + L(*QUEUE_SAID[word]) + L("。", "."))


async def _resume(c: Call) -> Reply:
    """/resume（engine/history.py）：不写 = 列这个对话之前的会话；/resume 编号（或会话 id）= 开一条新会话，开头带那一条聊到哪
    （同 /new 的写法；为什么不直接接到那一条后面见 history.py 开头）。列随时能列，换要等这一轮停。"""
    from . import history, owner, titles
    day = today(c.conversation)
    found = await asyncio.to_thread(history.sessions_of, c.conversation, day, 10)
    titles.upgrade(found)  # engine.titles 开了：还没起过名的在后台起
    old = [s for s in found if not s["current"]]
    if not c.args:
        if not old:
            return Reply(L("这个对话没有之前的会话。", "This chat has no earlier sessions."))
        lines = [L("**这个对话之前的会话**（/resume 编号 接着聊：开一条新会话，开头带那一段聊到哪）",
                   "**Earlier sessions in this chat** (/resume <number> picks one up: a new session that starts with where it left off)")]
        for i, s in enumerate(old, 1):
            lines.append(f"{i}. {s['day']} {when(s['created'])} · {s['title']}" + L(f"（{s['turns']} 轮）", f" ({s['turns']} turns)"))
        return Reply("\n".join(lines))
    if c.busy:
        return Reply(L("还在回复：等它说完，或者先 /stop。", "A reply is still running: wait for it to finish, or /stop it first."), ok=False)
    arg = c.args.split()[0].lstrip("#")
    pick = old[int(arg) - 1] if arg.isdigit() and 1 <= int(arg) <= len(old) else next((s for s in found if s["id"] == arg), None)
    if pick is None:
        return Reply(L(f"没有「{arg}」这一条。/resume 看有哪些。", f"There's no session \"{arg}\". /resume lists them."), ok=False)
    if pick["current"]:
        return Reply(L("这就是现在这条会话。", "That's the current session."), ok=False)
    src = await asyncio.to_thread(history.owned, c.conversation, pick["id"])
    await owner.resume_from(c.conversation, day, src, agent=c.agent, model=c.model)
    said = [L(f"开了一条新会话，开头带上了 {pick['day']} 那一段聊到哪（「{pick['title']}」）。",
              f"Started a new session that opens with where the {pick['day']} one left off (\"{pick['title']}\").")]
    if c.kind == "claude-code":
        said.append(L("Claude Code 下一句开一个新会话。", "Claude Code starts a new session with your next message."))
    return Reply(" ".join(said))


async def _export(c: Call) -> Reply:
    """/export [md|json|html] [tools]（engine/export.py）：这个对话现在这条会话导成一个文件，挂在回答上（app 里能点开、下载）。"""
    from . import export
    words = [w.lower() for w in c.args.split()]
    fmt = next((export.FORMATS[w] for w in words if w in export.FORMATS), "md")
    tools = any(w in ("tools", "tool", "工具") for w in words)
    bad = [w for w in words if w not in export.FORMATS and w not in ("tools", "tool", "工具")]
    if bad:
        return Reply(L(f"不认识「{bad[0]}」。写法：/export [md|json|html] [tools]", f"Didn't understand \"{bad[0]}\". Usage: /export [md|json|html] [tools]"),
                     ok=False)
    s = session_now(c.conversation)
    if s is None:
        return Reply(L("今天这个对话还没有会话，没什么可导的。", "There's no session in this chat today, so there's nothing to export."), ok=False)
    body, name, mime = await asyncio.to_thread(export.render, c.conversation, s, fmt, tools)
    row = await asyncio.to_thread(export.attach, c.conversation, name, body, mime)
    return Reply(L(f"导出好了：{name}（{fmt}{'，带工具调用' if tools else ''}；像密钥的字都换成了 ***）。",
                   f"Exported: {name} ({fmt}{', with tool calls' if tools else ''}; anything that looks like a key is replaced with ***)."),
                 files=[row])


async def _goal(c: Call) -> Reply:
    """/goal（engine/goal.py）：/goal <目标>（start / set 也行）定一个、/goal 看、/goal pause、/goal resume、/goal stop。"""
    from . import goal
    if goal.cfg() is None:
        return Reply(L("持续目标没开（server.json 的 engine.goal）：开了它会自己接着跑、接着花钱，所以要你自己开。",
                       "Standing goals are off (engine.goal in server.json): once on, it keeps running and spending by itself, so it's yours "
                       "to turn on."), ok=False)
    first, _, rest = c.args.partition(" ")
    word = first.strip().lower()

    async def kick() -> None:
        goal.kick(c.conversation)
    if not c.args or word == "status":
        return Reply(goal.line(await asyncio.to_thread(goal.get, c.conversation)))
    if word in ("stop", "clear", "cancel"):
        had = await asyncio.to_thread(goal.stop, c.conversation)
        return Reply(L("目标清掉了。", "Goal cleared.") if had else L("这个对话没有目标。", "This chat has no goal."))
    if word == "pause":
        g = await asyncio.to_thread(goal.pause, c.conversation, L("你让它先停着。", "You paused it."))
        return Reply(L("目标先停着，/goal resume 接着做。", "Goal paused; /goal resume picks it up.") if g
                     else L("这个对话没有目标。", "This chat has no goal."), ok=g is not None)
    if word == "resume":
        g = await asyncio.to_thread(goal.resume, c.conversation)
        if g is None:
            return Reply(L("这个对话没有目标。/goal <目标> 定一个。", "This chat has no goal. /goal <goal> sets one."), ok=False)
        return Reply(L(f"接着做目标：{g['goal']}（轮数、token 从现在重新算）", f"Back on the goal: {g['goal']} (turns and tokens count from now)"),
                     then=kick)
    text = rest.strip() if word in ("start", "set") else c.args.strip()
    if not text:
        return Reply(L("/goal 后面写目标，比如：/goal 把这周的收件箱清完", "Write the goal after /goal, like: /goal clear this week's inbox"), ok=False)
    g = await asyncio.to_thread(goal.start, c.conversation, text)
    return Reply(L(f"目标定好了：{text}\n每一轮完了判一次做到没有，没做到就接着做（最多 {g['max_turns']} 轮、{g['max_tokens']} token）。"
                   "你说话照常先回你；/goal 看进度，/goal stop 不做了。",
                   f"Goal set: {text}\nAfter each turn it checks whether the goal is met and keeps going if not (at most {g['max_turns']} turns, "
                   f"{g['max_tokens']} tokens). Your messages still come first; /goal shows progress, /goal stop ends it."), then=kick)


async def _btw(c: Call) -> Reply:
    """旁问（engine/side.py）：这一轮在跑也照样问，不等它、不打断它。回答开头标「（旁问）」（app 现在的气泡照画；以后的渠道照样看得出）。"""
    from . import side
    from .errors import EngineError, scrub
    if not c.args:
        return Reply(L("/btw 后面写你要问的，比如：/btw 刚才那个文件叫什么？", "Write your question after /btw, like: /btw what was that file called?"),
                     ok=False)
    try:
        text, _ = await side.answer(c.conversation, c.args, model=c.model)
    except EngineError as e:
        why = scrub(str(e))[:200]
        return Reply(L(f"（旁问）没问成：{why}", f"(Side question) It didn't go through: {why}"), ok=False, origin="btw")
    return Reply(L("（旁问）", "(Side question) ") + text, origin="btw")


async def _bg(c: Call) -> Reply:
    """后台跑这句（engine/bg.py，第 4 批）：/bg 后面的话派成一个后台任务，主对话照常聊；做完结果回到这个对话。这一轮在跑也照样派。
    急停着、今天的花费到了上限不派（bg.start 抛 ToolFailure，原话回给用户）。"""
    from . import bg
    from .loop import ToolFailure
    if not c.args:
        return Reply(L("/bg 后面写要在后台做的事，比如：/bg 把今天的笔记整理成一张清单",
                       "Write what to do after /bg, like: /bg turn today's notes into a short list"), ok=False)
    try:
        out = await bg.start(c.conversation, c.args)
    except ToolFailure as e:
        return Reply(str(e), ok=False)
    note = f"\n{out['note']}" if out.get("note") else ""
    return Reply(L(f"在后台做了：{out['label']}（{out['id']}，{out['runner']}，最长 {out['minutes']} 分钟）。做完结果回到这个对话，进度看任务卡。",
                   f"Running in the background: {out['label']} ({out['id']}, {out['runner']}, up to {out['minutes']} minutes). "
                   "The result comes back into this chat; the task card shows progress.") + note)


for _c in (
    Command("help", _help, ("看有哪些命令", "List the commands"), aliases=("commands",)),
    Command("compact", _compact, ("现在把前面的对话压成一段摘要；写了关注点就照它留细节（#消息号 = 从那条往前压）",
                                  "Compact the earlier conversation into a summary now; a focus keeps its details (#id = everything before that message)"),
            args=("[#消息号] [关注点]", "[#id] [focus]"), aliases=("compress",), idle=True),
    Command("new", _new, ("马上换一条新会话，开头带上刚才聊到哪（写了要点就带你写的）",
                          "Start a new session now, carrying over where this one left off (or the notes you write)"),
            args=("[要点]", "[notes]"), aliases=("reset",), idle=True),
    Command("status", _status, ("这个对话在不在跑、在等什么、模型、上一轮用了多少", "Whether this chat is running, what it waits for, the model, the last turn's tokens")),
    Command("usage", _usage, ("这个对话、今天、最近 7 天用了多少 token 和钱", "Tokens and cost for this chat, today and the last 7 days")),
    Command("context", _context, ("开头、工具、对话各占多少 token，离压缩还有多远", "How many tokens the opening, tools and conversation take, and how far from compacting")),
    Command("tools", _tools, ("这个 Agent 这一轮有哪些工具", "Which tools this Agent has this turn")),
    Command("model", _model, ("看或换这个对话的模型", "Show or switch this chat's model"), args=("[模型]", "[model]")),
    Command("stop", _stop, ("停掉正在跑的回复", "Stop the reply that's running")),
    Command("think", _think, ("看或设这个对话的思考档位（default = 跟回默认），下一轮起用",
                              "Show or set this chat's thinking level (default goes back to the default), from the next reply on"),
            args=("[low|medium|high|xhigh|max|default]", "[low|medium|high|xhigh|max|default]"), aliases=("thinking",)),
    Command("export", _export, ("把这个对话现在这条会话导成文件（Markdown / JSON / HTML；tools = 带上工具调用）",
                                "Export this chat's current session as a file (Markdown / JSON / HTML; tools = include tool calls)"),
            args=("[md|json|html] [tools]", "[md|json|html] [tools]")),
    Command("resume", _resume, ("看这个对话之前的会话；/resume 编号 接着聊（开一条新会话，开头带那一段聊到哪）",
                                "List this chat's earlier sessions; /resume <number> picks one up (a new session that starts with where it left off)"),
            args=("[编号]", "[number]")),
    Command("queue", _queue, ("回复在跑的时候你又说一句：插话、排着一句一句回、排着合成一轮、还是打断它",
                              "What a message sent while a reply is running does: steer into it, follow up one by one, collect, or interrupt"),
            args=("[steer|followup|collect|interrupt|default]", "[steer|followup|collect|interrupt|default]")),
    Command("goal", _goal, ("定一个持续目标：每一轮完了判一次做到没有，没做到就接着做（/goal、pause、resume、stop）",
                            "Set a standing goal: after each turn it checks whether it's met and keeps going if not (/goal, pause, resume, stop)"),
            args=("[目标|status|pause|resume|stop]", "[goal|status|pause|resume|stop]")),
    Command("btw", _btw, ("顺手问一句：拿这个对话当背景，不进对话、不用工具、不打断正在跑的回复",
                          "Ask a quick side question about this chat: kept out of the conversation, no tools, doesn't interrupt a running reply"),
            args=("问题", "question"), aliases=("side",)),
    Command("bg", _bg, ("这句放到后台做：派成一个后台任务，主对话照常聊，做完结果回到这个对话",
                        "Run this in the background as a task: the chat carries on, and the result comes back here when it's done"),
            args=("要做的事", "what to do")),
):
    register(_c)


# —— 接口 ————————————————————————————————————————————————————————————————————————

class CompactIn(BaseModel):
    thread: str
    before: str | None = None  # 从 app 里这条消息（"db123"）往前压：它和后面的原样留着
    focus: str = ""


@router.post("/api/engine/compact")
async def compact_endpoint(body: CompactIn):
    """「从这条往前总结」（第 2 批，以后 app 长按消息用）：这个对话现在的会话手动压一次，同 /compact。只在 builtin；这一轮还在跑 → 409。
    → {"ok", "turns"?, "before"?, "after"?, "why"?, "text"（给人看的一句）}。"""
    import claw
    from . import owner
    if not claw.is_builtin():
        raise HTTPException(404, "only with the built-in engine")
    if busy(body.thread):
        raise HTTPException(409, L("还在回复：等它说完，或者先停下。", "A reply is still running: wait for it to finish, or stop it first."))
    ref = None
    if body.before:
        m = re.match(r"^(?:db|#)?(\d+)$", body.before.strip())
        if not m:
            raise HTTPException(400, "before should look like db123")
        ref = int(m.group(1))
    model = current_model(body.thread)
    out = await owner.compact_now(body.thread, today(body.thread), model=model, focus=body.focus, before=ref)
    return {**out, "text": compact_text(out, kind_of(model), ref)}


@router.get("/api/engine/commands")
def commands_list(thread: str = "main"):
    """app 输入框打 / 时列出来的（第 2 批）：这个对话能用的命令、参数写法、一句说明。engine.commands 没开 → 空的。"""
    if not enabled():
        return {"ok": True, "commands": []}
    from . import owner
    try:
        kind = kind_of(current_model(thread))
    except Exception:  # noqa: BLE001 — 读不到这个对话的模型：按内核自己跑的列
        kind = "engine"
    return {"ok": True, "commands": [{"name": c.name, "aliases": list(c.aliases), "args": L(*c.args) if c.args else "", "description": L(*c.help),
                                      "idle": c.idle, "source": c.source} for c in listed(owner.agent_of(thread), kind)]}
