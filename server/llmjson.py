"""一次性的结构化模型调用：规矩（prompt）+ 资料（input，JSON）+ JSON Schema → 一个 JSON。播客的提纲、主持人追问、整理、费曼对照都走它。

走哪条路：
  1. OpenClaw 开了 llm-task 插件（安装器「开公网」时会开；也可以 openclaw.json 的 plugins.entries.llm-task）：Gateway 的 /tools/invoke，
     零工具、每次新会话、回来的 JSON 按 schema 校验过。快（Opus 5.5 一个追问 2–3 秒），不进任何对话，也碰不到任何工具。
  2. 没开（/tools/invoke 回 404）或者用的是别的 claw：think.complete 一问一答（OpenClaw 是一个用完就删的临时会话，别的 claw 走它的
     OpenAI 兼容接口），从回来的文字里抠出 JSON。llm-task 报过「没开」之后一小时内直接走这条。
schema 只卡最外层（是个对象、有哪几个键）：llm-task 严格校验，模型多一个键、一个数字写成字符串，整句就作废（名片 agent 和播客都踩过）。
字段的样子写在提示词里，调用方自己清理。校验还是没过（或者这一次模型出错）就不带 schema 再问一次。
llm-task 不许覆盖模型（Plugin LLM completion cannot override the target model）：model 只给别的路子用。
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


async def via_llm_task(prompt: str, input_: dict, schema: dict | None, timeout: float, thinking: str, model: str | None):
    global _no_task_until
    args: dict = {"prompt": prompt, "input": input_, "timeoutMs": int(timeout * 1000), "thinking": thinking}
    if schema:
        args["schema"] = schema
    if model:
        args["model"] = model
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


async def via_complete(prompt: str, input_: dict, schema: dict | None, timeout: float):
    import think  # 放这里：think 也会用到这个模块，免得互相导入
    text = (prompt + "\n\nINPUT_JSON:\n" + json.dumps(input_, ensure_ascii=False, indent=1)
            + "\n\nReply with ONE JSON value only, no other text, no tools." + (f"\nJSON Schema:\n{json.dumps(schema, ensure_ascii=False)}" if schema else ""))
    key = f"agent:main:grava:json-{uuid.uuid4().hex[:10]}"
    try:
        out = await think.complete(text, key, chat.DEFAULT_MODEL if claw.is_openclaw() else claw.model(), timeout)
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
              model: str | None = None, fallback_input: dict | None = None, tool_free_only: bool = False) -> tuple[object, str]:
    """→ (JSON, 走的哪条路)。thinking：llm-task 的思考档位（Opus 5.5 不能关，最低 low）。
    fallback_input：退回 think.complete（带工具的对话回合）时换用的资料：input 里有别人的话（朋友的聊天、约朋友录的逐字稿、朋友画像）
    就给一份不带它的，那种回合里模型能用工具，别人的一句话不能跟着进去。
    tool_free_only：只许走 llm-task（整个 input 都是别人的话，比如给朋友记画像）；用不了就抛 LLMError，不退回。"""
    if claw.is_openclaw() and time.time() >= _no_task_until:
        try:
            return await via_llm_task(prompt, input_, schema, timeout, thinking, None)
        except LLMError as e:
            if "not enabled" not in str(e):
                # 校验没过（「did not match schema」，Gateway 只回一句 tool execution failed）或者这一次模型出错：不带 schema 再来一次
                try:
                    return await via_llm_task(prompt, input_, None, timeout, thinking, None)
                except LLMError as e2:
                    if "not enabled" not in str(e2):
                        raise
    if tool_free_only:
        raise LLMError("needs llm-task (no tools); it isn't available")
    return await via_complete(prompt, input_ if fallback_input is None else fallback_input, schema, timeout)
