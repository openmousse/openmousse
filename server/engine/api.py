"""内核的 HTTP 入口（都在 /api 下，令牌规矩和别的接口一样）：

  GET  /api/engine/status          内核配了什么：默认模型、哪些事改走内核、各家接口（只说有没有密钥）、最近几轮
  GET  /api/engine/turns           最近的一轮轮（?conversation= 只看一个对话）
  GET  /api/engine/turns/{tid}     一轮的每一步
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
from . import events, models
from .errors import EngineError

router = APIRouter()


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
    return {"configured": bool(c), "model": models.default_model() or None, "models": models.models(),
            "use": {k: models.route(k) for k in ("card", "doorman", "json", "cyber")} | {k: v for k, v in use.items() if k not in ("card", "doorman", "json", "cyber")},
            "fallback": models.fallback_allowed(), "providers": providers, "turns": events.turns(limit=10)}


@router.get("/api/engine/turns")
def turns(conversation: str | None = None, limit: int = 30):
    return {"turns": events.turns(conversation, max(1, min(200, limit)))}


@router.get("/api/engine/turns/{tid}")
def turn_steps(tid: str):
    return {"steps": events.steps(tid)}


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
