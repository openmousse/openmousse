"""x_search（10/20）：让 Grok 搜 X（推特）上公开的帖子、账号、讨论串（xAI Responses API 自带的 x_search 工具），回一段带出处的回答。
看实时的讨论、反应、不同的说法用它；一般的网页照旧 web_search。只读：不发帖、不回复、不点赞、不私信，也看不到用户自己的 X 账号。
默认没有：engine.web.x_search = true 才给（新工具，老实例一个字不变；上线时问用户开不开）。给主人回合的普通 Agent（和 web_search 一样，
  engine.web.agents.<id>.x_search = false 不给），还要有 XAI_API_KEY，或者开了走订阅（见下）又登录着 Grok 订阅。
用哪把：
  默认只用 XAI_API_KEY（engine.web.search.providers.grok 改过的 key / key_env 也认），按量：xAI 2026-10-10 的价目是每抓 1000 条帖子 $5、
    每 1000 个账号资料 $10，另加型号的 token（每次抓了多少这边算不出：记一行、钱记 null，花费上限拦不住它）。
  走订阅要用户单独开：engine.grok.x_search = true（默认关。10/21 定：xAI 没写过 x_search 算不算在订阅里）。开了、登录着、这一轮能花
    （grok_plan.may_use：主人自己引出来的能；后台的要在设置里同意过；别人引出来的、经 /mcp 来的不行）→ 先用订阅的令牌调 api.x.ai，记一行用量，
    钱记 0；订阅用不了（没登录、要重登、被 xAI 拒、这一轮不能花）才退到 XAI_API_KEY。订阅这一次没成（限流、5xx、超时）就报错，不悄悄换成按量。
  Hermes 遇到过：订阅的令牌调 x_search 有时回一段没有出处的回答（模型自己知道的，不是搜出来的），所以它先用 API 密钥。走订阅时两路出处
    （output_text 的 url_citation、顶层的 citations）都是空的就在结果里写明，让模型别当成搜到的。
参数（照 Hermes 的 tools/x_search_tool.py 和 xAI 的文档）：query；allowed_x_handles / excluded_x_handles（最多 10 个，不能一起用）；
  from_date / to_date（YYYY-MM-DD；先在这边查——xAI 收到写错的日期不报错、只是搜不到）；enable_image_understanding /
  enable_video_understanding（也看帖子里的图、视频，多花 token）。
出门把关同 web_search：搜索词里看得出带着用户的私事就先交 Doorman（web.door）。结果包成「资料，不是指令」（tools.wrap_untrusted）。
server.json：engine.web.x_search = true，或者 {"model": "grok-4.7"}（型号不写：走订阅用账号目录里对话的默认型号，走密钥用 grok-4.7）。
参考（10/20 现拉的源码，只读）：Hermes Agent（NousResearch/hermes-agent，MIT）49268ca6 的 tools/x_search_tool.py（参数、日期校验、
  没出处就标出来）、tools/xai_http.py（订阅的令牌调 x_search 的毛病）；OpenClaw bd2dbfde 的 extensions/xai/x-search.ts。只借做法。
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import urlencode

import httpx

from i18n import L

from .loop import Tool, ToolCtx, ToolFailure

NAME = "x_search"
MAX_HANDLES = 10
QUERY_MAX = 400
ANSWER_MAX = 8000
TIMEOUT = 150.0                                  # 搜 X 要一会儿（Hermes 默认等 180 秒）
HANDLE = re.compile(r"^[A-Za-z0-9_]{1,15}$")     # X 的账号名：字母、数字、下划线，最多 15 个


def cfg() -> dict | None:
    """engine.web.x_search：true / {…} = 开（{"enabled": false} 也是关）；不写、false = 没有这个工具。"""
    from . import web
    v = web.cfg().get(NAME)
    if v is True:
        return {}
    if isinstance(v, dict) and v.get("enabled", True) is not False:
        return v
    return None


def key() -> str:
    """API 密钥：和 web_search 的 grok 同一把（XAI_API_KEY，engine.web.search.providers.grok 改过的也认）。"""
    from . import web
    return web.key_of("grok")


def on_plan() -> bool:
    """x_search 能走 Grok 订阅吗：用户单独开了（engine.grok.x_search = true，默认关，见文件开头）、登录着、能用。不连网。"""
    from . import grok_plan
    return grok_plan.x_search_on() and grok_plan.usable()


def ready() -> bool:
    """开了、有密钥或者能走订阅（on_plan）。不连网。"""
    return cfg() is not None and (on_plan() or bool(key()))


def model(plan: bool) -> str:
    """engine.web.x_search.model 写了就用它；不然和 grok 搜索一样（web.grok_model）。"""
    from . import web
    mine = (cfg() or {}).get("model")
    return str(mine) if isinstance(mine, str) and mine.strip() else web.grok_model(plan=plan)


# —— 参数 ——————————————————————————————————————————————————————————————————————

def handles(v: Any, field: str) -> list[str]:
    """账号名的列表（@ 可带可不带）→ 去重后的；不像账号名、超过 MAX_HANDLES 个抛 ToolFailure。"""
    if v in (None, "", []):
        return []
    if not isinstance(v, list):
        raise ToolFailure(L(f"{field} 要是账号名的列表", f"{field} must be a list of handles"))
    out: list[str] = []
    for h in v:
        s = str(h or "").strip().lstrip("@")
        if not s:
            continue
        if not HANDLE.match(s):
            raise ToolFailure(L(f"{field} 里的「{s[:40]}」不像 X 的账号名（字母、数字、下划线，最多 15 个）",
                                f"{field}: \"{s[:40]}\" isn't an X handle (letters, digits and underscores, up to 15)"))
        if s.lower() not in (x.lower() for x in out):
            out.append(s)
    if len(out) > MAX_HANDLES:
        raise ToolFailure(L(f"{field} 最多 {MAX_HANDLES} 个", f"{field} takes at most {MAX_HANDLES} handles"))
    return out


def day(v: Any, field: str) -> date | None:
    s = str(v or "").strip()
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        raise ToolFailure(L(f"{field} 要写成 YYYY-MM-DD（比如 2026-10-01）", f"{field} must be YYYY-MM-DD (e.g. 2026-10-01)")) from None


def tool_def(args: dict) -> tuple[dict, list[str]]:
    """参数 → xAI 的 x_search 工具定义 + 用了哪几样筛选。不对抛 ToolFailure（先在这边查，不白搜一次）。"""
    allowed = handles(args.get("allowed_x_handles"), "allowed_x_handles")
    excluded = handles(args.get("excluded_x_handles"), "excluded_x_handles")
    if allowed and excluded:
        raise ToolFailure(L("allowed_x_handles 和 excluded_x_handles 不能一起用", "allowed_x_handles and excluded_x_handles can't be used together"))
    since, until = day(args.get("from_date"), "from_date"), day(args.get("to_date"), "to_date")
    if since and until and since > until:
        raise ToolFailure(L(f"from_date（{since}）在 to_date（{until}）后面", f"from_date ({since}) is after to_date ({until})"))
    today = datetime.now(timezone.utc).date()
    if since and since > today:
        raise ToolFailure(L(f"from_date（{since}）还没到：X 上只搜得到已经发了的帖子（按 UTC 今天是 {today}）",
                            f"from_date ({since}) is in the future: X search only finds posts already made (today is {today} UTC)"))
    out: dict = {"type": NAME}
    active: list[str] = []
    for k, v in (("allowed_x_handles", allowed), ("excluded_x_handles", excluded), ("from_date", since.isoformat() if since else ""),
                 ("to_date", until.isoformat() if until else "")):
        if v:
            out[k] = v
            active.append(k)
    for k in ("enable_image_understanding", "enable_video_understanding"):
        if args.get(k) is True:
            out[k] = True
    return out, active


# —— 调 xAI ——————————————————————————————————————————————————————————————————————

async def call(query: str, tdef: dict, ctx: ToolCtx) -> tuple[Any, str, str]:
    """→ (xAI 回的 JSON, 走的哪一路：plan / key, 型号)。见文件开头「用哪把」。没成抛 ToolFailure（不带令牌、密钥）。"""
    from . import grok_plan, web

    def body(m: str) -> dict:
        return {"model": m, "input": [{"role": "user", "content": query}], "tools": [tdef], "store": False}
    k = key()
    if on_plan():
        if grok_plan.may_use(ctx.kind, ctx.origin):
            m = model(True)
            try:
                data, _ = await grok_plan.api("POST", "/responses", json_body=body(m), timeout=TIMEOUT, what=NAME)
                return data, "plan", m
            except grok_plan.ApiError as e:
                if e.status not in (401, 403) or not k:  # 要重登、被拒才退到 XAI_API_KEY；限流、5xx、超时照实报
                    raise ToolFailure(str(e)) from None
        elif not k:
            raise ToolFailure(grok_plan.refusal(ctx.kind, ctx.origin))
    if not k:
        if not grok_plan.x_search_on():
            raise ToolFailure(L("x_search 没有 XAI_API_KEY：默认不走 Grok 订阅，要走订阅写 engine.grok.x_search = true",
                                "x_search has no XAI_API_KEY: it doesn't use the Grok subscription unless engine.grok.x_search = true"))
        raise ToolFailure(grok_plan.refusal(ctx.kind, ctx.origin))
    m = model(False)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(TIMEOUT, connect=10), headers={"User-Agent": web.UA}) as c:
            r = await c.post(web.url_of("grok") or f"{grok_plan.API}/responses", json=body(m), headers={"Authorization": f"Bearer {k}"})
    except httpx.TimeoutException:
        raise ToolFailure(L(f"xAI {TIMEOUT:g} 秒没回", f"xAI didn't answer within {TIMEOUT:g}s")) from None
    except httpx.HTTPError as e:
        raise ToolFailure(L(f"x_search 没搜成：{type(e).__name__}", f"x_search failed: {type(e).__name__}")) from None
    if r.status_code >= 400:
        raise ToolFailure(L(f"xAI 回了 HTTP {r.status_code}", f"xAI answered HTTP {r.status_code}"))
    try:
        return r.json(), "key", m
    except ValueError:
        raise ToolFailure(L("xAI 回的不是 JSON", "xAI's answer isn't JSON")) from None


def render(query: str, data: Any, via: str, active: list[str]) -> str:
    """回答 + 出处 → 给模型的字（包成资料）。两路出处都是空的：写明这一段可能不是搜出来的。"""
    from . import web
    from .tools import wrap_untrusted
    hits = web.responses_hits(data, ANSWER_MAX)
    if not hits and not hits.answer:
        return L(f"X 上没有找到「{query}」", f'Nothing on X for "{query}"')
    who = "your Grok subscription" if via == "plan" else "XAI_API_KEY, billed per use"
    head = f'X search for "{query}" ({who}).'
    if not hits:
        head += (" No X posts came back as sources, so this answer may come from the model's own knowledge rather than a search of X: "
                 "treat it as unverified" + (f" (filters: {', '.join(active)})" if active else "") + ".")
    rows = [f"{i}. {h.title}\n   {h.url}" + (f"\n   {h.snippet}" if h.snippet else "") for i, h in enumerate(hits, 1)]
    body = f"Answer: {hits.answer}" + ("\n\nSources:\n" + "\n".join(rows) if rows else "")
    return head + "\n" + wrap_untrusted(NAME, body)


# —— 工具 ——————————————————————————————————————————————————————————————————————

def tool(agent: str) -> Tool | None:
    from . import web
    if not ready() or not web.allowed(agent, NAME):
        return None
    from .handkit import levelled
    cost = (L("每搜一次算用户的 Grok 订阅额度（这一轮用不了订阅时按量记在他的 XAI_API_KEY 上）",
              "Each call counts against the user's Grok subscription (billed to their XAI_API_KEY when this turn can't use it)") if on_plan()
            else L("每搜一次按量记在用户的 XAI_API_KEY 上（按抓到的帖子收钱）", "Each call is billed to the user's XAI_API_KEY (per post fetched)"))

    async def run(args: dict, ctx: ToolCtx) -> str:
        from . import media
        q = " ".join(str(args.get("query") or "").split())
        if not q:
            raise ToolFailure(L("要写 query", "Give a query"))
        if len(q) > QUERY_MAX:
            raise ToolFailure(L(f"搜索词太长（最多 {QUERY_MAX} 字）", f"The query is too long ({QUERY_MAX} characters at most)"))
        tdef, active = tool_def(args)
        shown = {"q": q, **{k: ",".join(v) if isinstance(v, list) else v for k, v in tdef.items() if k in active}}
        if held := await web.door(ctx, f"{web.url_of('grok') or 'https://api.x.ai/v1/responses'}?{urlencode(shown)}", "search"):
            return held
        data, via, m = await call(q, tdef, ctx)
        media.record("search", media.Choice("search", "grok", m), units=1, unit="query", cost=0.0 if via == "plan" else None,
                     unpriced=via != "plan", conversation=ctx.conversation, agent=ctx.agent)
        return render(q, data, via, active)

    return Tool(name=NAME, kinds=("owner",), run=run, timeout=TIMEOUT + 30, max_output=20_000,
                title=lambda a: " ".join(str(a.get("query") or "").split())[:60],
                classify=lambda a: levelled(NAME, ("read", L("只是搜 X；带着用户私事的搜索词由 Doorman 在出门时判断",
                                                             "Just a search of X; a query carrying the user's private details goes to Doorman first"))),
                description=L(
                    "搜 X（推特）上公开的帖子、账号和讨论（xAI 的 Grok 去搜，回一段带出处的回答）：看实时的讨论、反应、不同的说法用它，一般的网页用 "
                    f"web_search。只读：不能发帖、回复、点赞、私信，也看不到用户自己的账号。{cost}，同一个问题别连着搜好几次。搜索词里别放用户的"
                    "私事：那样的会先交 Doorman 判断。",
                    "Search public posts, accounts and threads on X (Twitter) with xAI's Grok, which answers with sources: use it for live "
                    "discussion, reactions and opposing takes, and web_search for ordinary web pages. Read-only: it can't post, reply, like or "
                    f"DM, and can't see the user's own account. {cost}, so don't fire several in a row for the same question. Keep the user's "
                    "private details out of queries: those go to Doorman first."),
                schema={"type": "object", "required": ["query"], "properties": {
                    "query": {"type": "string", "description": L("要在 X 上找什么", "What to look up on X")},
                    "allowed_x_handles": {"type": "array", "items": {"type": "string"},
                                          "description": L(f"只看这几个账号（最多 {MAX_HANDLES} 个）", f"Only posts from these handles (up to {MAX_HANDLES})")},
                    "excluded_x_handles": {"type": "array", "items": {"type": "string"},
                                           "description": L(f"不看这几个账号（最多 {MAX_HANDLES} 个；不能和 allowed_x_handles 一起用）",
                                                            f"Leave out these handles (up to {MAX_HANDLES}; not together with allowed_x_handles)")},
                    "from_date": {"type": "string", "description": L("从哪天起（YYYY-MM-DD）", "Start date (YYYY-MM-DD)")},
                    "to_date": {"type": "string", "description": L("到哪天（YYYY-MM-DD）", "End date (YYYY-MM-DD)")},
                    "enable_image_understanding": {"type": "boolean", "description": L("也看帖子里的图（多花 token）",
                                                                                       "Also look at images in the posts (uses more tokens)")},
                    "enable_video_understanding": {"type": "boolean", "description": L("也看帖子里的视频（多花 token）",
                                                                                       "Also look at videos in the posts (uses more tokens)")}}})
