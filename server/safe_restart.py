#!/usr/bin/env python3
"""等进行中的回复都结束再重启服务（2026-09-28）。

重启会掐断正在进行的回复（连接一断，Gateway 就中止那一轮），回复进行中排着的消息也要等重启后才发。
改完服务端代码要重启时用它，不要直接 systemctl restart——尤其是后台子任务：主对话可能正在回你。

用法：python3 safe_restart.py [--unit 服务名] [--wait 秒] [--dry-run]
  --unit     systemd user 服务名，默认环境变量 MOUSSE_SERVICE，没有就 openmousse-server
  --wait     最多等多久（默认 600 秒）；到点还有回复在跑就不重启，退出码 3
  --dry-run  只看现在忙不忙，不重启
服务地址和令牌从 server.json（MOUSSE_SERVER_CONFIG，默认 ~/.openmousse/server.json）读，不打印。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from config import pick_api_token


def status(base: str, token: str | None) -> dict:
    req = urllib.request.Request(f"{base}/api/chat/busy", headers={"Authorization": f"Bearer {token}"} if token else {})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


def main() -> None:
    ap = argparse.ArgumentParser(description="等进行中的回复结束再重启服务 / Restart the server once no reply is in progress")
    ap.add_argument("--unit", default=os.environ.get("MOUSSE_SERVICE", "openmousse-server"))
    ap.add_argument("--wait", type=int, default=600)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    cfg = json.loads(Path(os.environ.get("MOUSSE_SERVER_CONFIG", "~/.openmousse/server.json")).expanduser().read_text(encoding="utf8"))
    bind = cfg.get("bind") or {}
    host = bind.get("host") or "127.0.0.1"
    base = f"http://{'127.0.0.1' if host in ('0.0.0.0', '::') else host}:{bind.get('port') or 8080}"
    tokens = (cfg.get("auth") or {}).get("tokens") or {}
    token = pick_api_token(tokens) if isinstance(tokens, dict) else None  # mcp、sentinel 那几把在 /api 上不通
    deadline = time.time() + a.wait
    while True:
        try:
            s = status(base, token)
        except OSError as e:  # 服务没在跑（或者太老、没有这个接口）：直接重启
            print(f"读不到忙闲（{e}），直接重启 / can't read busy state, restarting anyway")
            break
        if s.get("idle"):
            break
        if a.dry_run:
            print(json.dumps(s, ensure_ascii=False))
            return
        if time.time() > deadline:
            print(f"等了 {a.wait} 秒还有回复在跑，没重启 / still busy after {a.wait}s, not restarting: {json.dumps(s, ensure_ascii=False)}")
            sys.exit(3)
        time.sleep(3)
    if a.dry_run:
        print("空闲 / idle")
        return
    subprocess.run(["systemctl", "--user", "restart", a.unit], check=True)
    print(f"重启了 {a.unit} / restarted {a.unit}")


if __name__ == "__main__":
    main()
