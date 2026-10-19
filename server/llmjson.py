"""一次性的结构化模型调用：规矩（prompt）+ 资料（input，JSON）+ JSON Schema → 一个 JSON。播客的提纲、主持人追问、整理、费曼对照都走它。

走哪条路：
  1. OpenClaw 开了 llm-task 插件（安装器「开公网」时会开；也可以 openclaw.json 的 plugins.entries.llm-task）：Gateway 的 /tools/invoke，
     零工具、每次新会话、回来的 JSON 按 schema 校验过。快（Opus 5.5 一个追问 2–3 秒），不进任何对话，也碰不到任何工具。
  2. 没开（/tools/invoke 回 404）或者用的是别的 claw：think.complete 一问一答（OpenClaw 是一个用完就删的临时会话，别的 claw 走它的
     OpenAI 兼容接口），从回来的文字里抠出 JSON。llm-task 报过「没开」之后一小时内直接走这条。
schema 只卡最外层（是个对象、有哪几个键）：llm-task 严格校验，模型多一个键、一个数字写成字符串，整句就作废（名片 agent 和播客都踩过）。
字段的样子写在提示词里，调用方自己清理。校验还是没过（或者这一次模型出错）就不带 schema 再问一次。
llm-task 不许覆盖模型（Plugin LLM completion cannot override the target model）：model 只给别的路子用。
  0. server.json 的 engine.use.json 开了：先走自己的内核（engine/oneshot.py，外人回合：零工具、能换模型，model 也认）；
     出错时照旧往下走（engine.fallback 是 false 就不往下走，直接报错）。
谁引出来的（kind / origin，10/19）：主人自己的（会议纪要、播客、学习页……报 kind="owner", origin="user"）engine.use.json 写的是用户自己的
  Claude Code（claude-code/…）时交给它一次性地跑（零工具、不存会话，走他的套餐；跑不了有按量的备用就换）；builtin 没配
  engine.use.json、退回 think.complete 的那条路也带上来路（engine/owner.stream：模型是他的 Claude Code 就走套餐）。不报的当外人：
  一律不花用户的套餐（route() 换成按量的，owner.stream 也换）。
失败抛 LLMError（带一句原因，不带令牌）。
"""
from __future__ import annotations

import json
import re
import time
import uuid

import httpx
from fastapi import HTTPException

import chat
import claw
import engine
from config import settings

_no_task_until = 0.0


class LLMError(RuntimeError):
    pass


def parse_json(text: str):
    """回复里的 JSON（去掉 ```json 围栏和前后的话）。"""
    s = (text or "").strip()
    m = re.search(r"```(?:json)?\s*([\[{].*[\]}])\s*```", s, re.S)
    if m:
        s = m.group(1)
    else:
        starts = [i for i in (s.find("{"), s.find("[")) if i >= 0]
        a, b = (min(starts) if starts else -1), max(s.rfind("}"), s.rfind("]"))
        s = s[a:b + 1] if a >= 0 and b > a else s
    try:
        return json.loads(s)
    except ValueError as e:
        raise LLMError("the reply is not JSON") from e


async def via_llm_task(prompt: str, input_: dict, schema: dict | None, timeout: float, thinking: str, model: str | None,
                       max_tokens: int | None = None):
    global _no_task_until
    args: dict = {"prompt": prompt, "input": input_, "timeoutMs": int(timeout * 1000), "thinking": thinking}
    if schema:
        args["schema"] = schema
    if model:
        args["model"] = model
    if max_tokens:  # 长输出（学习台写学习页）：不给的话按模型默认，长的会被截断成坏 JSON
        args["maxTokens"] = int(max_tokens)
    try:
        token = chat.gateway_token()
    except HTTPException as e:
        raise LLMError("no Gateway token") from e
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout + 15, connect=10)) as client:
            r = await client.post(f"{settings.gateway}/tools/invoke", headers={"Authorization": f"Bearer {token}"},
                                  json={"tool": "llm-task", "args": args, "agentId": "main"})
    except httpx.HTTPError as e:
        raise LLMError(type(e).__name__) from e
    if r.status_code == 404:
        _no_task_until = time.time() + 3600
        raise LLMError("llm-task is not enabled")
    try:
        j = r.json()
    except ValueError as e:
        raise LLMError(f"HTTP {r.status_code}") from e
    if r.status_code != 200 or not j.get("ok"):
        err = j.get("error") if isinstance(j, dict) else None
        raise LLMError(str((err or {}).get("message") if isinstance(err, dict) else err or f"HTTP {r.status_code}")[:200])
    res = j.get("result") or {}
    det = res.get("details") if isinstance(res, dict) else None
    if isinstance(det, dict) and det.get("json") is not None:
        return det["json"], f"llm-task:{det.get('provider') or ''}/{det.get('model') or ''}"
    content = res.get("content") if isinstance(res, dict) else None
    text = next((x.get("text") for x in content or [] if isinstance(x, dict) and x.get("type") == "text"), "")
    return parse_json(text or ""), "llm-task"


async def via_complete(prompt: str, input_: dict, schema: dict | None, timeout: float, origin: str | None = None):
    """origin：主人自己引出来的（见文件开头），一路带给 think.complete；None = 当外人。"""
    import think  # 放这里：think 也会用到这个模块，免得互相导入
    text = (prompt + "\n\nINPUT_JSON:\n" + json.dumps(input_, ensure_ascii=False, indent=1)
            + "\n\nReply with ONE JSON value only, no other text, no tools." + (f"\nJSON Schema:\n{json.dumps(schema, ensure_ascii=False)}" if schema else ""))
    key = f"agent:main:grava:json-{uuid.uuid4().hex[:10]}"
    try:
        out = await think.complete(text, key, chat.DEFAULT_MODEL if claw.is_openclaw() else claw.model(), timeout, origin=origin)
    except Exception as e:  # noqa: BLE001 — 连不上、Gateway 报错都算这一次没成
        raise LLMError(str(e)[:200]) from e
    finally:
        if claw.is_openclaw():
            try:  # 临时会话用完就删
                await chat.gateway_call("sessions.delete", {"key": key}, timeout=20)
            except Exception:  # noqa: BLE001
                pass
    return parse_json(out), "complete"


async def ask(prompt: str, input_: dict, schema: dict | None = None, *, timeout: float = 90, thinking: str = "low",
              model: str | None = None, fallback_input: dict | None = None, tool_free_only: bool = False,
              max_tokens: int | None = None, kind: str = "outsider", origin: str | None = None) -> tuple[object, str]:
    """→ (JSON, 走的哪条路)。thinking：llm-task 的思考档位（Opus 5.5 不能关，最低 low）。
    fallback_input：退回 think.complete（带工具的对话回合）时换用的资料：input 里有别人的话（朋友的聊天、约朋友录的逐字稿、朋友画像）
    就给一份不带它的，那种回合里模型能用工具，别人的一句话不能跟着进去。
    tool_free_only：只许走 llm-task（整个 input 都是别人的话，比如给朋友记画像）；用不了就抛 LLMError，不退回。内核那条路（engine.use.json，
    含交给用户自己的 Claude Code 的）也零工具，算数。
    max_tokens：这一次最多输出多少 token（llm-task 和内核；长的输出要给够）。
    kind / origin：这一问是谁引出来的（见文件开头）；主人自己的报 kind="owner", origin="user"，不报 = 外人。"""
    owner = origin if kind == "owner" else None
    if ref := engine.route("json", model, kind=kind, origin=origin):
        required = tuple(schema.get("required") or ()) if isinstance(schema, dict) and schema.get("type") == "object" else ()
        try:
            return await engine.ask_json(prompt, input_, model=ref, purpose="json", required=required, timeout=timeout,
                                         effort=thinking if thinking in ("low", "medium", "high") else None, max_tokens=max_tokens,
                                         kind=kind, origin=origin)
        except engine.EngineError as e:
            if not engine.fallback_allowed():
                raise LLMError(str(e)[:200]) from e
    if claw.is_openclaw() and time.time() >= _no_task_until:
        try:
            return await via_llm_task(prompt, input_, schema, timeout, thinking, None, max_tokens)
        except LLMError as e:
            if "not enabled" not in str(e):
                # 校验没过（「did not match schema」，Gateway 只回一句 tool execution failed）或者这一次模型出错：不带 schema 再来一次
                try:
                    return await via_llm_task(prompt, input_, None, timeout, thinking, None, max_tokens)
                except LLMError as e2:
                    if "not enabled" not in str(e2):
                        raise
    if tool_free_only:
        raise LLMError("needs llm-task (no tools); it isn't available")
    return await via_complete(prompt, input_ if fallback_input is None else fallback_input, schema, timeout, origin=owner)
