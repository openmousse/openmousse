"""packs/core 脚本共用的一点配置：都从 ~/.openmousse/server.json 读（安装器写的），代码里不写死地址和路径。

  server_url()   服务的地址，如 http://127.0.0.1:8080（bind 是 0.0.0.0 时用 127.0.0.1）
  token()        名为 local 的接入令牌（安装器生成，给本机脚本调 /api 用）；没有就空字符串
  db_path()      服务的 SQLite（journal 表在里面）
  user_tz()      用户时区（server.json 的 timezone）
  api(path, body=None, timeout=30)   带令牌的 JSON 请求
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

CONFIG_PATH = Path(os.environ.get("MOUSSE_SERVER_CONFIG") or "~/.openmousse/server.json").expanduser()


def config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf8"))
    except (OSError, ValueError):
        return {}


def server_url() -> str:
    b = config().get("bind") or {}
    host = b.get("host") or "127.0.0.1"
    if host in ("0.0.0.0", "::", ""):
        host = "127.0.0.1"
    return f"http://{host}:{int(b.get('port') or 8080)}"


def token() -> str:
    return str(((config().get("auth") or {}).get("tokens") or {}).get("local") or "")


def db_path() -> Path:
    c = config()
    if c.get("db"):
        return Path(c["db"]).expanduser()
    return Path(c.get("data_dir") or "~/.openmousse/data").expanduser() / "mousse.db"


def user_tz() -> ZoneInfo:
    try:
        return ZoneInfo(config().get("timezone") or "UTC")
    except Exception:  # noqa: BLE001
        return ZoneInfo("UTC")


def user_now() -> datetime:
    return datetime.now(user_tz())


def api(path: str, body: dict | None = None, timeout: float = 30) -> dict:
    """调服务的 /api。失败抛 urllib 的异常（HTTPError 带状态码）。"""
    headers = {"Accept": "application/json"}
    if token():
        headers["Authorization"] = f"Bearer {token()}"
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(server_url() + path, data=data, headers=headers, method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — 本机服务
        return json.loads(resp.read())
