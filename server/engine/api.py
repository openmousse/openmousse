"""内核的 HTTP 入口（都在 /api 下，令牌规矩和别的接口一样）：

  …/api/setup…                     第 9 步「通用的选择层」：选模型（云端 / 本机 / 命令行登录 / 自定义）、按用途选、搜索、记忆、用量
                                   （engine/setup_api.py；GET /api/engine/usage 是同一个用量接口）
  GET / PUT /api/setup/skills/{agent}  10/19：一个 Agent 的技能名单（engine.skills.agents.<id>，engine/agent_skills.py）：看、换；只认主人的设备，
                                   写 server.json 之前先备份；第 5 批起每个技能带门槛（gate），GET / PUT /api/setup/skills/config/{名字} 是一个技能的
                                   开关、环境变量、设置
  …/api/setup/skills/market/…      第 5 批：技能市场（engine/skill_market.py）：search、preview、install、installed、update；只认主人的设备
  GET  /api/engine/status          内核配了什么：默认模型、哪些事改走内核、各家接口（只说有没有密钥）、最近几轮；
                                   claudeCode = 本机用户自己登录的 Claude Code 能不能跑主人回合、登录的是套餐还是密钥（第 11 项）；
                                   chatgpt = 用户自己的 ChatGPT 套餐：连没连、哪个套餐、令牌快过期没有、最近一次的错、后台同意没有
                                   （第 11b 项，engine/chatgpt_plan.py；不带令牌）；helpers = 后台任务能用的外援和它们登录的是什么（第 5 项）
  …/api/engine/chatgpt…            ChatGPT 套餐的授权、回调、断开、后台同意（engine/chatgpt_plan.py 的接口）
  …/api/engine/grok…               Grok 订阅（10/20）：xAI 的设备码登录、退出、状态（engine/grok_plan.py；只认主人的设备）；/api/engine/status 的
                                   grok 是同一份状态（不带令牌）
  GET  /api/engine/turns           最近的一轮轮（?conversation= 只看一个对话）
  GET  /api/engine/turns/{tid}     一轮的每一步
  GET  /api/engine/heartbeat       心跳（第 6 项）：哪些 Agent 开了、下一次、上一次怎样
  GET  /api/engine/sandbox         沙箱（第 8 项）：哪些 Agent 在沙箱里、开着哪些容器、闲了多久；?check=1 再把起之前要查的都查一遍
  POST /api/engine/sandbox/reset   {"agent": "errand"}：收掉这个 Agent 的沙箱（草稿区、开着的网页、后台命令都没了；下次用到时重新起；
                                   ⑫ 起普通 Agent 自己的沙箱也行）
  GET  /api/engine/mcp             你配的 MCP 服务（第 13 步，engine/mcp_client.py）：在跑 / 停了 / 出错、工具和档位、最近一次错误
  …/api/setup/mcp…                 第 5 批：加、改、删、试连你配的 MCP 服务（engine/mcp_client.py 的 setup_router；只认主人的设备，先备份）
  GET  /api/engine/hooks           第 5 批：用户钩子（engine/user_hooks.py）：有哪些、同意了没有、最近几次跑的结果
  GET  /api/engine/intents         第 5 批：要记得的事（engine/intents.py）：?agent= / ?all=1；DELETE /api/engine/intents/{id} 撤掉一条
  …/api/setup/plugins…             第 5 批：带代码的插件（engine/plugins.py，默认关）：列、预览、装、同意、开关、删；只认主人的设备
  GET  /api/engine/recall          记忆检索（engine/recall.py、semantic.py）：按意思搜开没开、用哪一家哪个型号（不带密钥，只说有没有）、
                                   索引了多少段、有向量的多少、还没补的多少、世界树几片叶子、上次同步 / 补向量的时间、最近一次错误、
                                   是不是正在退回只按关键词；/api/engine/status 的 recall 是它的摘要
  …/api/engine/jobs…               定时任务（第 6 项，engine/jobs.py 的接口）
  POST /api/engine/answer          ⑫b：回答正在等的 ask_user（engine/ask.py）：{"conversation", "id", "choice"? | "text"?}；
                                   和在对话里直接回一句二选一，只算先到的那一次
  GET  /api/engine/plan_mode       ⑫b：?conversation= 这个对话今天在不在计划模式（engine/plan.py）
  GET  /api/engine/memory          13b：记性的闸记下的决定（engine/memgate.py）：?conversation= / ?kind= / ?limit=，新的在前
  GET  /api/engine/memory/table    13b / 13d：四种记忆、闸、三种回合的读写表、三档信（给记忆页的「待确认」「待定」用）
  GET  /api/engine/writeback       13b：对话结束时的写回记录（?conversation=）：这一段长了哪几片叶子、没选的和为什么、改做法的卡
  POST /api/engine/plan_mode       ⑫b：{"conversation", "on"}：开 / 关计划模式（以后 app 的开关）
  GET  /api/engine/used/{turn}     13c：这一轮用到的手册、技能、功能包的用法、叶子（engine/used.py；回复卡片底下那一行点开的详情）；
                                   ?message=db123 按回复的 id 找那一轮
  GET  /api/engine/skills          13c：?agent= 这个 Agent 的技能、手册、用法，带最后一次用的时间、多久没用、标旧（engine.manuals.stale_days）
  POST /api/engine/agent           第 1 批 b：命令行 mousse agent（../mousse.py、engine/agent_cli.py）：{"agent", "message", "model"?, "timeout"?}
                                   → 一个新会话里只读地跑一轮，回 {"ok", "answer", "model", "tools", "usage", …}；claw 要是 builtin
  POST /api/engine/oneshot         一次性 JSON 给本机脚本（10/19，替 OpenClaw 的 llm-task：岗位打分、检查脚本这类）：
                                   {"prompt", "input"?: {…}, "required"?: [键], "timeout"?, "model"?} → {"ok", "json", "via"}；走 llmjson.ask，
                                   算主人自己的（/api 只有主人的设备和本机脚本进得来）：engine.use.json 是他自己的 Claude Code 就走他的套餐。
                                   带 X-Mousse-Via 的（经 /mcp、内核的工具跑的脚本）来路照它：外面的 MCP 客户端（mcp）不花套餐
  GET  /api/engine/commands        第 2 批：?thread= 这个对话能用的斜杠命令（名字、别名、参数写法、一句说明；engine/commands.py），
                                   app 输入框打 / 时列出来；engine.commands 没开 → 空的
  GET / POST /api/engine/chats/{thread}/sessions | resume | fork | branches | branch | export
                                   第 2 批：这个对话的旧会话、挑一条接着聊、分叉、切枝、导出（engine/history.py 开头写了每个的样子）
  GET / POST /api/engine/pause     第 4 批：急停（engine/estop.py）：看停没停 / 停（{"reason"?, "stop_running"?}）；POST /api/engine/resume 恢复；
                                   只认主人的设备；/api/engine/status 的 paused 是它的摘要
  GET / PUT /api/engine/spend      第 4 批：花费上限（engine/spend.py）：今天各项花了多少、离上限多远、算不出钱的；改上限（只认主人的设备、先备份）
  GET /api/engine/triggers         第 4 批：从外面进来的触发（engine/triggers.py）：配了哪些、最近的事件；PUT / DELETE …/webhooks/{名字} 改 webhook
  POST /webhooks/{名字}            第 4 批：webhook（engine/webhooks.py）：不在 /api 下，每个自己验签；public: true 的也挂在对外的小服务上
  POST /api/engine/bg              第 4 批：后台跑这句（engine/bg.py）：{thread, text}，派成后台任务，做完结果回到这个对话
  GET /api/engine/flows            第 4 批：轻量的多步工作流（engine/flows.py）：POST …/{名字}/start、…/runs/{id}/stop | resume
  POST /api/engine/cyber           跟一个赛博说一句（测试入口，界面在主线第三步）：
                                   {"cyber": {"id", "name", "identity", "model"?, "sources"?}, "text": "…", "stream": false}
                                   stream = true 回 SSE：event delta / thinking / tool，最后 event done
"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from . import cyber as cyber_mod
from . import agent_cli, agent_skills, ask, chatgpt_plan, events, fallback, heartbeat, jobs, mcp_client, memgate, models, plan, sandbox, setup_api, used, writeback
from . import commands, history
from . import intents, plugins, skill_market, user_hooks  # 第 5 批
from . import grok_plan  # 10/20：用户自己的 Grok 订阅
from . import bg, estop, flows, mailwatch, spend, triggers, webhooks
from .errors import EngineError

router = APIRouter()
router.include_router(setup_api.router)  # 第 9 步：/api/setup/…（第一次打开的引导、设置中心：选模型、搜索、记忆，只认主人的设备）和 /api/engine/usage
router.include_router(agent_skills.router)  # 10/19：GET / PUT /api/setup/skills/{agent}（一个 Agent 的技能名单，只认主人的设备）
router.include_router(skill_market.router)  # 第 5 批：/api/setup/skills/market/…（技能市场：搜、预览、装、更新、卸载，只认主人的设备）
router.include_router(user_hooks.router)  # 第 5 批：GET /api/engine/hooks（用户钩子：有哪些、同意了没有、最近跑的结果）
router.include_router(intents.router)  # 第 5 批：GET /api/engine/intents、DELETE /api/engine/intents/{id}（要记得的事）
router.include_router(plugins.router)  # 第 5 批：/api/setup/plugins…（带代码的插件：列、预览、装、同意、开关、删；只认主人的设备）
router.include_router(jobs.router)  # 定时任务（第 6 项）
router.include_router(chatgpt_plan.router)  # 第 11b 项：/api/engine/chatgpt…（用户自己的 ChatGPT 套餐）
router.include_router(grok_plan.router)  # 10/20：/api/engine/grok…（用户自己的 Grok 订阅，只认主人的设备）
router.include_router(mcp_client.router)  # 第 13 步：GET /api/engine/mcp（你配的 MCP 服务）
router.include_router(mcp_client.setup_router)  # 第 5 批：/api/setup/mcp…（加、改、删、试连你配的 MCP 服务，只认主人的设备）
router.include_router(ask.router)  # ⑫b：POST /api/engine/answer
router.include_router(plan.router)  # ⑫b：GET / POST /api/engine/plan_mode
router.include_router(memgate.router)  # 13b / 13d：GET /api/engine/memory（记性的闸记下的每一次决定）、/api/engine/memory/table（闸和读写表）
router.include_router(writeback.router)  # 13b：GET /api/engine/writeback（对话结束时的写回：长了哪几片、没选的和为什么）
router.include_router(used.router)  # 13c：GET /api/engine/used/{turn}、/api/engine/used?message=、/api/engine/skills
router.include_router(agent_cli.router)  # 第 1 批 b：POST /api/engine/agent（命令行 mousse agent：跟一个 Agent 说一句、拿回结果）
router.include_router(commands.router)  # 第 2 批：GET /api/engine/commands（斜杠命令的列表，app 打 / 时用）
router.include_router(history.router)  # 第 2 批：/api/engine/chats/{thread}/…（旧会话、接着聊、分叉、切枝、导出）
router.include_router(estop.router)  # 第 4 批：GET / POST /api/engine/pause、POST /api/engine/resume（急停，只认主人的设备）
router.include_router(spend.router)  # 第 4 批：GET / PUT /api/engine/spend（花费上限，只认主人的设备）
router.include_router(triggers.router)  # 第 4 批：GET /api/engine/triggers（从外面进来的触发：配了哪些、最近的事件，只认主人的设备）
router.include_router(webhooks.api)  # 第 4 批：PUT / DELETE /api/engine/triggers/webhooks/{名字}（只认主人的设备、先备份）
router.include_router(webhooks.router)  # 第 4 批：POST /webhooks/{名字}（不在 /api 下、不认令牌：每个 webhook 自己验签）
router.include_router(mailwatch.api)  # 第 4 批：PUT / DELETE /api/engine/triggers/mail/{名字}（盯邮箱，只认主人的设备、先备份）
router.include_router(bg.router)  # 第 4 批：POST /api/engine/bg（后台跑这句：派成后台任务、不问额度，只认主人的设备）
router.include_router(flows.router)  # 第 4 批：/api/engine/flows…（轻量的多步工作流：看、开、停、接着走，只认主人的设备）


@router.get("/api/engine/heartbeat")
def heartbeat_status():
    return {"ok": True, "agents": heartbeat.status()}


@router.get("/api/engine/sandbox")
async def sandbox_status(check: bool = False):
    out = await sandbox.status()
    if check:
        out["checks"] = {}
        boxes = {**sandbox.agents(), **({"main": m} if (m := sandbox.hands_of("main")) else {})}  # ⑫：主对话也有自己的沙箱
        for agent, a in boxes.items():
            for kind in ("shell", "browser") if a["browser"] else ("shell",):
                try:
                    await sandbox.preflight(agent, kind)
                    out["checks"][f"{agent}/{kind}"] = {"ok": True}
                except sandbox.SandboxError as e:
                    out["checks"][f"{agent}/{kind}"] = {"ok": False, "error": str(e)}
    return {"ok": True, **out}


class ResetIn(BaseModel):
    agent: str


@router.post("/api/engine/sandbox/reset")
async def sandbox_reset(body: ResetIn):
    if sandbox.box(body.agent) is None:  # ⑫：普通 Agent 自己的沙箱也能收掉重来
        raise HTTPException(404, "this Agent has no sandbox")
    return {"ok": True, "removed": await sandbox.reset(body.agent)}


@router.get("/api/engine/recall")
def recall_status():
    from . import recall, semantic
    out = semantic.status()
    try:
        out["index"] = {k: v for k, v in recall.stats().items() if k in ("db", "built", "sources", "chunks", "bytes", "synced_at")}
    except Exception as e:  # noqa: BLE001 — 索引坏了也给看状态（下一次搜会重建它）
        out["index"] = {"error": f"{type(e).__name__}: {str(e)[:120]}"}
    return {"ok": True, **out}


def _recall_brief() -> dict:
    """/api/engine/status 里的那一小段（读不了索引也不让整个接口出错）。"""
    from . import semantic
    try:
        st = semantic.status()
    except Exception as e:  # noqa: BLE001
        return {"enabled": False, "error": f"{type(e).__name__}: {str(e)[:120]}"}
    return {k: st.get(k) for k in ("enabled", "problem", "backend", "model", "vectors", "pending", "leaves", "embeddedAt", "lastError", "fallback")}


@router.get("/api/engine/status")
def status():
    c = models.cfg()
    names = sorted(set(models.PROVIDERS) | set(models._openclaw_providers()) | set(c.get("providers") or {}))
    providers = []
    for n in names:
        try:
            p = models.provider(n)
        except EngineError:
            continue
        providers.append({"name": n, "api": p["api"], "url": p.get("url") or None, "key": bool(p["key"]) or p.get("key_env", "x") == ""})
    use = c.get("use") if isinstance(c.get("use"), dict) else {}
    from . import claude_brain, runners
    found = runners.available()
    return {"configured": bool(c), "model": models.default_model() or None, "models": models.models(),
            "use": {k: models.route(k) for k in ("card", "doorman", "json", "cyber")} | {k: v for k, v in use.items() if k not in ("card", "doorman", "json", "cyber")},
            "fallback": models.fallback_allowed(), "providers": providers, "claudeCode": claude_brain.status(), "chatgpt": chatgpt_plan.status(),
            "grok": grok_plan.status(),  # 10/20：Grok 订阅（不带令牌）
            "helpers": {n: {k: (found.get(n) or {}).get(k) for k in ("auth", "billing", "plan")} for n in runners.offered()},
            "turns": events.turns(limit=10),
            "fallbacks": models.fallbacks(), "cooldowns": fallback.status(),  # 第 13 步：默认备用链、正在冷却的密钥 / 型号
            "recall": _recall_brief(),  # 记忆检索：按意思搜开没开、向量补了多少、最近一次错误（全的在 /api/engine/recall）
            "paused": estop.paused()}  # 第 4 批：急停着没有（全的在 /api/engine/pause）


@router.get("/api/engine/turns")
def turns(conversation: str | None = None, limit: int = 30):
    return {"turns": events.turns(conversation, max(1, min(200, limit)))}


@router.get("/api/engine/turns/{tid}")
def turn_steps(tid: str):
    return {"steps": events.steps(tid)}


class OneshotIn(BaseModel):
    prompt: str
    input: dict = {}
    required: list[str] = []
    timeout: float = 90
    model: str | None = None


@router.post("/api/engine/oneshot")
async def oneshot(body: OneshotIn):
    """一次性 JSON 给本机脚本（见文件开头）。模型出错、拿不到 JSON → 502（一句原因）。只在 builtin：别的 claw 上会退回带工具的回合。"""
    import chat
    import claw
    import llmjson
    if not claw.is_builtin():
        raise HTTPException(404, "only with the built-in engine")
    prompt = body.prompt.strip()
    if not prompt:
        raise HTTPException(400, "empty prompt")
    origin = chat.REQUEST_VIA.get() or "user"  # 经谁来的（main.guard 按 X-Mousse-Via 设）：认不出的、外面的 MCP 客户端都不花套餐
    schema = {"type": "object", "required": [str(k) for k in body.required]} if body.required else None
    try:
        out, via = await llmjson.ask(prompt, body.input, schema, timeout=max(5.0, min(float(body.timeout), 900.0)), model=body.model,
                                     kind="owner", origin=origin)
    except llmjson.LLMError as e:
        raise HTTPException(502, str(e)[:300]) from e
    return {"ok": True, "json": out, "via": via}


class CyberBody(BaseModel):
    cyber: dict
    text: str
    stream: bool = False


def _sse(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode()


@router.post("/api/engine/cyber")
async def cyber_chat(body: CyberBody):
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "empty message")
    try:
        cyber_mod.cyber_id(body.cyber)
        cyber_mod.model_of(body.cyber)
    except EngineError as e:
        raise HTTPException(400, str(e)) from e
    if not body.stream:
        res = await cyber_mod.chat(body.cyber, text)
        return {"reply": res.text, "status": res.status, "error": res.error, "turn": res.turn, "model": res.model, "usage": res.usage,
                "firstMs": res.first_ms}
    q: asyncio.Queue = asyncio.Queue()

    def on_event(ev: dict) -> None:
        kind = {"text": "delta", "thinking": "thinking", "tool_run": "tool", "tool_end": "tool"}.get(ev.get("type", ""))
        if kind:
            q.put_nowait((kind, ev))

    async def run() -> None:
        try:
            res = await cyber_mod.chat(body.cyber, text, on_event=on_event)
            q.put_nowait(("done", {"reply": res.text, "status": res.status, "error": res.error, "turn": res.turn, "usage": res.usage}))
        except Exception as e:  # noqa: BLE001
            q.put_nowait(("done", {"status": "error", "error": str(e)[:200]}))

    async def gen():
        task = asyncio.create_task(run())
        try:
            while True:
                kind, data = await q.get()
                yield _sse(kind, data)
                if kind == "done":
                    break
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(gen(), media_type="text/event-stream")
