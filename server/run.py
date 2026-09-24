#!/usr/bin/env python3
"""按 server.json 的 bind 启动服务：python3 run.py"""
import uvicorn

from config import settings

if __name__ == "__main__":
    uvicorn.run("main:app", host=settings.host, port=settings.port, access_log=False, server_header=False)
