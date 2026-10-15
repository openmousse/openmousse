"""单元测试的公共设置：server/ 放进 sys.path，导入前把 MOUSSE_SERVER_CONFIG 指到一个临时的空白配置。

config.py 在导入时就读配置文件（默认 ~/.openmousse/server.json），所以这里必须在任何测试模块导入 server 代码之前设好环境变量，
测试永远不碰真实的家目录。单个测试要换配置时用 server_json 这个 fixture（写到 tmp_path，monkeypatch config.CONFIG_PATH）。
"""
from __future__ import annotations

import atexit
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

SERVER = Path(__file__).resolve().parent.parent
_BOOT = Path(tempfile.mkdtemp(prefix="mousse-test-"))
atexit.register(shutil.rmtree, _BOOT, ignore_errors=True)
(_BOOT / "server.json").write_text(json.dumps({
    "app_name": "TestMousse",
    "timezone": "UTC",
    "language": "en",
    "openclaw_home": str(_BOOT / "openclaw"),
    "data_dir": str(_BOOT / "data"),
    "dist": str(_BOOT / "dist"),
}), encoding="utf8")
os.environ["MOUSSE_SERVER_CONFIG"] = str(_BOOT / "server.json")
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

import config  # noqa: E402 — 要在上面设好环境变量之后再导入


@pytest.fixture
def server_json(tmp_path, monkeypatch):
    """换一份 server.json：server_json({...}) 写到 tmp_path 并让 config 读它（清掉 mtime 缓存）。返回文件路径。"""
    path = tmp_path / "server.json"
    monkeypatch.setattr(config, "CONFIG_PATH", path)
    monkeypatch.setattr(config, "_cache", None)

    def write(data) -> Path:
        path.write_text(data if isinstance(data, str) else json.dumps(data, ensure_ascii=False), encoding="utf8")
        config._cache = None
        return path

    return write
