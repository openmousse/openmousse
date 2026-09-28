"""对外的小服务：server.json 的 share.public_port 配了，run.py 就在 127.0.0.1:<端口> 上起它，给 Tailscale Funnel / 反向代理指过去。

这里只有 /s/<令牌>（分享的链接页和预览图，见 share.py）和 robots.txt：没有 /api，也不认任何令牌或 Tailscale 设备，
所以从外网进来的请求碰不到主服务。以后朋友之间的接口（社交第二层）也挂在这里。
"""
from __future__ import annotations

import threading

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse

import share

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(share.public_router)


@app.middleware("http")
async def mark_public(request: Request, call_next):
    request.state.public = True  # 链接页据此只数外网来的浏览
    return await call_next(request)


@app.get("/robots.txt")
async def robots():
    return PlainTextResponse("User-agent: *\nDisallow: /\n")


def start() -> threading.Thread | None:
    """在后台线程里起这个小服务（主服务退出它就跟着退）。没配 share.public_port 就什么都不做。"""
    port = share.public_port()
    if not port:
        return None
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, access_log=False, server_header=False, log_level="warning"))
    t = threading.Thread(target=server.run, name="share-public", daemon=True)
    t.start()
    return t
