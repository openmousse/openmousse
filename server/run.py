#!/usr/bin/env python3
"""按 server.json 的 bind 启动服务：python3 run.py"""
import uvicorn

from config import settings

if __name__ == "__main__":
    import public
    public.start()  # server.json share.public_port 配了才起：127.0.0.1 上只有 /s/ 的小服务（分享链接页，给 Funnel 指过去）
    uvicorn.run("main:app", host=settings.host, port=settings.port, access_log=False, server_header=False)
