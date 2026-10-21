"""从命令行跟一个 Agent 说一句（第 1 批 b，2026-10-19）：mousse agent（../mousse.py）的服务器那一半，POST /api/engine/agent。

为什么有它：用户的编码助手会话里原来用 `openclaw agent --agent <id> --message …` 找另一个模型商量；claw 选 builtin 以后没有 OpenClaw 的
Gateway 了，换成这个。claw 不是 builtin → 409（OpenClaw 那条路照旧用 openclaw agent）。

一次 = 一个新会话里的一轮主人回合（命令行是用户自己发起的，算主人：origin user，models.may_spend 照它认）：
  会话   不落库（MemorySession，同 engine/unattended.fresh）：事件表照记，对话记成 cli:<Agent>:<随机 12 位>；不进对话结束时的写回
         （engine/writeback.py 只看库里的会话）、不进 sessions_list（session_status 按这个对话名照样查得到）。
  开头   这个 Agent 平时的主人回合那一份（unattended.system_for，备料按它自己的对话：档案、记忆、技能、上次聊到哪……）+ 一节 NOTE：
         这一轮从命令行来、没人在 app 里看、回答原样交回去、工具只读。
  工具   这个 Agent 平时的那些（unattended.tools_for：去掉 cron、tasks、skill_manage、问用户、待办、计划模式），你配的 MCP 服务先起好
         （mcp_client.ready，同 owner.respond）。关卡 gate 只放只读的（tools.classify / Tool.level 是 read）：撤得回的写、要点头的一律
         不做，回一句「只读」给模型，不交卡（命令行那头没人点头）；冒充别人的照旧挡（tools.problem）。
  模型   不写 model：这个 Agent 在 app 里的对话选的那个（openai_api.chosen；没选 = engine.model）。写了：
           provider/model        我们的接口能跑的（valid_ref），不认识的 400
           chatgpt/<型号>         用户自己的 ChatGPT 套餐：may_spend 认主人（经 /mcp、后台来的 X-Mousse-Via 更严，照样不放 → 403）；没连上、
                                 没开用套餐、要重连 → 403，说法照 chatgpt_plan（「在设置里点 Continue with ChatGPT」……）；型号要在能列的里面：
                                 engine.chatgpt.models 写了照它，不写用这个账号自己的模型目录（chatgpt_plan.model_ids；目录还没取过就当场取
                                 一次），不在里面 400 并列出能用的
           claude-code/<型号>     这台电脑上用户自己登录的 Claude Code（engine/claude_brain.once：一次性的新会话，同一道只读的关卡；跑不了
                                 就换备用链里的，同定时任务）。我们不读、不存、不传它的令牌，照 claude_brain 的规矩
  回     {"ok", "status", "agent", "answer", "model", "requested", "fallbackFrom"?, "plan", "conversation", "turn",
          "tools": [{"name", "title", "status"}], "used": 13c 用到的手册 / 技能 / 叶子（engine/used.summary）,
          "usage": {"input", "output", "cacheRead", "cacheWrite", "total", "cost"}, "ms", "error"?}
         answer = 这一轮说的全部字（几次开口之间隔一个空行，同 app 和 /v1）；套餐的 cost 是 null（不计钱）。模型出错 502、到点 504（照样带
         已经说的字）；还没开跑就不行的 {"ok": false, "code", "error"}：400 / 403 / 404 / 409 / 413 / 503。
  令牌   /api 那一套（main.py 的 guard）：app 的令牌、本机脚本的那一把（config.pick_api_token）都行；mcp / api / sentinel 那几把在 /api 上 403。
  断开   调用方断了（Ctrl-C）= 停这一轮（同 /v1 的一次性请求）；到 timeout（默认 600 秒，最多 1800）也停，status timeout。
参考（10/19 现拉到 scratchpad 只读）：OpenClaw 2d9033c6 的 src/cli/program/register.agent-turn.ts（--agent、--model、--message-file、
--timeout 默认 600）、src/commands/agent-via-gateway.ts（--json 回 payloads 和 meta：用了哪个模型、用量、工具）；Hermes 49320887 的
tools/delegate_tool_child_run.py（交回去的那一份：状态、模型、token、工具清单）。只借设计，没拿代码。
"""
from __future__ import annotations

import asyncio
import dataclasses
import time
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from i18n import L

from . import models
from .errors import EngineError
from .loop import Control, Result, Spec, run_turn
from .session import MemorySession, time_stamp, user

ORIGIN = "user"                # 命令行是用户自己发起的：主人回合（may_spend：claude-code 在 OWNER_ORIGINS、chatgpt 在 PLAN_FOREGROUND）
PREFIX = "cli:"                # 对话名的开头：cli:<Agent>:<随机>
TIMEOUT, TIMEOUT_MIN, TIMEOUT_MAX = 600, 10, 1800
MESSAGE_MAX = 400_000
STATUS = {"error": 502, "timeout": 504}
NOTE = ("## This turn\n"
        "This message comes from the user's own command line (`mousse agent`), often their coding assistant asking you something on "
        "their behalf. It is a fresh session of its own: nobody is watching it in the app, and your reply goes back to the command line "
        "exactly as you write it, so answer in full in this one reply and don't end on a question you need answered first. Your tools "
        "are read-only here: look up whatever helps, but nothing gets changed (calls that would change something come back \"Not "
        "run\"); if something should change, say what and how in your answer.")
NOT_HERE = ("Not run: this turn comes from the command line (mousse agent) and its tools are read-only, so nothing can be changed here. "
            "Don't retry it or look for another way; say in your answer what you would change and why.")
router = APIRouter()


class Refused(Exception):
    """还没开跑就不行（参数、Agent、模型、套餐）：HTTP 状态 + 一个代号 + 一句给人看的。"""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message

    def response(self) -> JSONResponse:
        return JSONResponse({"ok": False, "code": self.code, "error": self.message}, status_code=self.status)


async def gate(tool, args: dict, ctx) -> str | None:
    """只读的关卡（Spec.gate 的样子）：只放 read 的；冒充别人的照 tools.problem 挡；关掉的照 tools.gate 那句。"""
    import bridges

    from . import tools as tools_mod
    if ctx.kind != "owner":
        return "Not run: tools only run in the owner's own turns."
    if tool.name in bridges.BRIDGES and (why := tools_mod.problem(tool.name, [str(x) for x in args.get("args") or []], ctx.agent or "main")):
        return why
    level, _ = tool.level(args)
    if level == tools_mod.READ:
        return None
    if level == tools_mod.OFF:
        return L("没跑：用户把这个关掉了（server.json 的 engine.tools.levels）。别再试，跟用户说一声。",
                 "Not run: the user turned this off (engine.tools.levels in server.json). Don't retry; tell the user.")
    return NOT_HERE


# —— 用哪个模型 ——————————————————————————————————————————————————————————————————————

async def plan_models(wait: float = 15) -> list[str]:
    """套餐能用的型号（chatgpt_plan.model_ids）：engine.chatgpt.models 没写、账号的模型目录又还没取过 → 当场取一次（最多等 wait 秒；
    后台正在取的就等它取完），不像 app 那样先回空的、在后台慢慢取。"""
    from . import chatgpt_plan
    if not isinstance(chatgpt_plan.cfg().get("models"), list) and not (chatgpt_plan.state().get("catalog") or {}).get("models"):
        end = time.monotonic() + wait
        try:
            await asyncio.wait_for(chatgpt_plan.refresh_catalog(), wait)
        except asyncio.TimeoutError:
            pass
        while chatgpt_plan._catalog_busy and time.monotonic() < end:  # 别处刚起了一次（refresh_catalog 见它在取就直接回了）
            await asyncio.sleep(0.1)
    return chatgpt_plan.model_ids()


async def plan_ready(ref: str, via: str | None, *, mine: bool) -> None:
    """chatgpt/<型号>：may_spend 认这一轮、套餐能用、型号在能列的里面；不行抛 Refused（见文件开头）。mine：是那个 Agent 自己选的（不是 --model）。"""
    from . import chatgpt_plan
    hint = L(f"（这个 Agent 在 app 里选的是 {ref}；也可以用 --model 换一个用 API 密钥的模型）",
             f" (this Agent's chat uses {ref}; you can also pick a model with an API key with --model)") if mine else ""
    if not models.may_spend(ref, kind="owner", origin=ORIGIN, via=via):
        raise Refused(403, "plan_not_allowed", L("这一次不能花你的 ChatGPT 套餐：请求是经 MCP 或者后台引出来的，不算你自己在命令行里说的",
                                                 "This request can't use your ChatGPT plan: it came through MCP or a background run, not from "
                                                 "you on the command line") + hint)
    if not chatgpt_plan.usable():
        raise Refused(403, "plan_not_connected", str(chatgpt_plan._not_ready()) + hint)
    ids = await plan_models()
    if ref not in ids:
        raise Refused(400, "plan_model_unknown", L(f"你的 ChatGPT 套餐里没有 {ref}。能用的：{'、'.join(ids) or '（模型目录是空的）'}",
                                                   f"{ref} isn't in your ChatGPT plan. Available: {', '.join(ids) or '(the model catalog is empty)'}"))


async def pick(agent: str, requested: str | None, via: str | None) -> str:
    """这一轮用哪个模型（见文件开头「模型」）。不行抛 Refused。"""
    ref = requested
    if ref is None:
        from . import openai_api
        m = openai_api.chosen(agent)
        ref = m if m and (models.valid_ref(m) or models.subscription(m)) else models.default_model() or None
        if not ref:
            raise Refused(503, "no_model", L("内核还没配模型：server.json 的 engine.model，或者用 --model 指定一个",
                                             "The engine has no model yet: set engine.model in server.json, or pick one with --model"))
    ref = models.resolve(ref)  # 第 7 批：别名（engine.aliases）先换成真的引用，下面照真的那个判（套餐照样过 may_spend）
    sub = models.subscription(ref)
    if sub == "chatgpt":
        await plan_ready(ref, via, mine=requested is None)
        return ref
    if sub == "grok":  # 10/20：用户自己的 Grok 订阅（engine/grok_plan.py）：may_spend 认这一轮（经 /mcp、后台来的不放）、登录着
        from . import grok_plan
        if not models.may_spend(ref, kind="owner", origin=ORIGIN, via=via):
            raise Refused(403, "plan_not_allowed", L("这一次不能花你的 Grok 订阅：请求是经 MCP 或者后台引出来的，不算你自己在命令行里说的",
                                                     "This request can't use your Grok subscription: it came through MCP or a background run, "
                                                     "not from you on the command line"))
        if not models.subscription_ready(ref):
            raise Refused(403, "plan_not_connected", str(grok_plan._not_ready()))
        return ref
    if sub == "kimi":  # 10/21：Kimi 会员（engine/kimi_plan.py）：may_spend 认这一轮（经 /mcp、后台来的不放）、贴了密钥
        from . import kimi_plan
        if not models.may_spend(ref, kind="owner", origin=ORIGIN, via=via):
            raise Refused(403, "plan_not_allowed", L("这一次不能花你的 Kimi 会员：请求是经 MCP 或者后台引出来的，不算你自己在命令行里说的",
                                                     "This request can't use your Kimi membership: it came through MCP or a background run, "
                                                     "not from you on the command line"))
        if not models.subscription_ready(ref):
            raise Refused(403, "plan_not_connected", kimi_plan.unavailable_text())
        bare = ref.split("/", 1)[1]
        if bare not in kimi_plan.MODELS:
            raise Refused(400, "plan_model_unknown", L(f"Kimi 会员没有 {ref}。能用的：{'、'.join(kimi_plan.MODELS)}",
                                                       f"{ref} isn't a Kimi membership model. Available: {', '.join(kimi_plan.MODELS)}"))
        return ref
    if sub == "claude-code":
        if not models.may_spend(ref, kind="owner", origin=ORIGIN, via=via) or not models.subscription_ready(ref):
            raise Refused(403, "claude_code_unavailable", L(f"这台电脑上没有你自己登录好的 Claude Code，跑不了 {ref}（或者 server.json 关掉了 "
                                                            "engine.claude_code）：用 --model 换一个",
                                                            f"There's no Claude Code signed in to your own account on this computer, so {ref} "
                                                            "can't run (or engine.claude_code is off in server.json): pick another with --model"))
        return ref
    if not models.valid_ref(ref):
        raise Refused(400, "unknown_model", L(f"不认识 {ref}：写 provider/model（engine.models 里的、能拿到密钥的），或者 chatgpt/<型号>",
                                              f"Unknown model {ref}: use provider/model (one from engine.models with a key), or chatgpt/<model>"))
    return models.canonical(ref)


# —— 一轮 ————————————————————————————————————————————————————————————————————————

def usage_of(u: dict) -> dict:
    i, o, cr, cw = (int(u.get(k) or 0) for k in ("in", "out", "cache_read", "cache_write"))
    return {"input": i, "output": o, "cacheRead": cr, "cacheWrite": cw, "total": i + o + cr + cw, "cost": u.get("cost")}


async def ask(agent: str, message: str, model: str | None = None, *, via: str | None = None, timeout: float | None = None,
              request: Request | None = None) -> dict:
    """跑一次（见文件开头）→ 回给命令行的那一份。还没开跑就不行抛 Refused。"""
    import bridges
    import claw

    from . import mcp_client, unattended, used
    if not claw.is_builtin():
        raise Refused(409, "not_builtin", L("这台服务器的 Agent 不在自己的内核上跑（claw 不是 builtin）：OpenClaw 用 openclaw agent",
                                            "This server's Agents don't run on OpenMousse's own engine (the claw isn't builtin): with "
                                            "OpenClaw, use openclaw agent"))
    agent = (agent or "").strip() or "main"
    if agent != "main" and not bridges.known_agent(agent):
        raise Refused(404, "unknown_agent", L(f"没有叫 {agent} 的 Agent（python3 agent_ctl.py list 看有哪些）",
                                              f"There's no Agent called {agent} (python3 agent_ctl.py list shows them)"))
    if not (message or "").strip():
        raise Refused(400, "empty_message", L("要说的话是空的", "The message is empty"))
    if len(message) > MESSAGE_MAX:
        raise Refused(413, "message_too_long", L(f"太长了：最多 {MESSAGE_MAX} 字", f"Too long: at most {MESSAGE_MAX} characters"))
    secs = max(float(TIMEOUT_MIN), min(float(timeout or TIMEOUT), float(TIMEOUT_MAX)))
    requested = (model or "").strip() or None
    ref = await pick(agent, requested, via)
    conv = f"{PREFIX}{agent}:{uuid.uuid4().hex[:12]}"
    control = Control()
    try:
        await mcp_client.ready(agent, control)  # 你配的 MCP 服务（第一次用到的 stdio 服务这时起；没配就什么都不做）
        tools = unattended.tools_for(agent)
        system = "\n\n".join([await unattended.system_for(agent, agent, tools, False), NOTE])
    except Exception as e:  # noqa: BLE001 — 配置不对、备料出错：还没开跑
        raise Refused(503, "engine_config", f"{type(e).__name__}: {str(e)[:200]}") from None
    spec = Spec(kind="owner", model=ref, system=system, tools=tools, conversation=conv, agent=agent, origin=ORIGIN, via=via, gate=gate,
                display="summarized", timeout=min(secs, 600.0))
    asked = {**user(message), "at": time_stamp()}  # 和 app 里说的一句一样带上当地时间
    parts: list[str] = []
    spoke = {"any": False, "gap": False}
    trace: dict[str, dict] = {}

    def on_event(ev: dict) -> None:
        t = ev.get("type")
        if t == "model":  # 又问一次模型：再开口就和前面那段隔开（同 owner.respond）
            spoke["gap"] = spoke["any"]
        elif t == "text" and ev.get("text"):
            if spoke["gap"]:
                parts.append("\n\n")
                spoke["gap"] = False
            spoke["any"] = True
            parts.append(str(ev["text"]))
        elif t == "rewrite":  # Claude Code 流断了整条重来（engine/claude_brain）：整段换掉
            parts[:] = [str(ev.get("text") or "")]
        elif t == "tool_run":
            trace[str(ev.get("id"))] = {"name": ev.get("name"), "title": ev.get("detail") or ev.get("name"), "status": "running"}
        elif t == "tool_end" and str(ev.get("id")) in trace:
            trace[str(ev.get("id"))]["status"] = ev.get("status") or ("ok" if ev.get("ok") else "error")

    timed_out = [False]

    async def watchdog() -> None:
        await asyncio.sleep(secs)
        timed_out[0] = True
        control.abort()

    async def hung_up() -> None:  # 调用方断开（Ctrl-C）= 停这一轮（同 /v1 的一次性请求）
        while request is not None and not control.aborted.is_set():
            if await request.is_disconnected():
                control.abort()
                return
            await asyncio.sleep(1)

    t0 = time.monotonic()
    dog, watch = asyncio.create_task(watchdog()), asyncio.create_task(hung_up())
    try:
        if models.subscription(ref) == "claude-code":
            from . import claude_brain

            async def switch(chain: list[str], failed: Result) -> Result:
                """Claude Code 跑不了（登录掉了、命令坏了、额度用完，还没出字）：换上备用链里的跑这一次（同 unattended.fresh）。"""
                res = await run_turn(dataclasses.replace(spec, model=chain[0], fallbacks=chain[1:]), MemorySession(), asked, on_event=on_event,
                                     control=control)
                res.models = [ref, *res.models]
                return res
            res = await claude_brain.once(agent, conv, system, tools, message, model=ref, origin=ORIGIN, on_event=on_event, control=control,
                                          switch=switch, gate=gate)
        else:
            res = await run_turn(spec, MemorySession(), asked, on_event=on_event, control=control)
    except EngineError as e:  # 回合的规矩不对（loop.run_turn 再挡一道套餐）
        raise Refused(403 if e.kind == "rule" else 503, "engine_" + (e.kind or "error"), str(e)) from None
    finally:
        dog.cancel()
        watch.cancel()
    answer = "".join(parts).strip() or (res.text or "").strip()
    status = "timeout" if timed_out[0] else res.status
    served = res.model or ref
    ok = status in ("done", "length", "max_steps", "refused") and bool(answer)  # 拒答但说了话的同 app：算回答了
    out = {"ok": ok, "status": status, "agent": agent, "answer": answer, "model": served, "requested": requested,
           **({"fallbackFrom": ref} if served != ref else {}), "plan": models.subscription(served) is not None, "conversation": conv,
           "turn": res.turn, "tools": list(trace.values()), "used": used.summary(res.turn), "usage": usage_of(res.usage or {}),
           "ms": int((time.monotonic() - t0) * 1000)}
    if not ok:
        out["error"] = (L(f"{int(secs)} 秒还没答完，停了", f"Not finished within {int(secs)} seconds; stopped") if status == "timeout"
                        else L("调用方断开了，这一轮停了", "The caller hung up, so the turn was stopped") if status == "aborted"
                        else res.error or (L("模型拒绝回答", "The model declined to answer") if status == "refused"
                                           else L("模型什么都没说", "The model said nothing")))
    return out


class AgentIn(BaseModel):
    agent: str = "main"
    message: str
    model: str | None = None
    timeout: float | None = None


@router.post("/api/engine/agent")
async def post_agent(body: AgentIn, request: Request):
    import chat
    try:
        out = await ask(body.agent, body.message, body.model, via=chat.REQUEST_VIA.get(), timeout=body.timeout, request=request)
    except Refused as e:
        return e.response()
    return JSONResponse(out, status_code=STATUS.get(out["status"], 200))
