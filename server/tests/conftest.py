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
    "openclaw_bin": "/bin/false",  # 跑 OpenClaw 那条路的测试别碰到这台机器上真的 openclaw 命令（它连的是真 Gateway）
    "data_dir": str(_BOOT / "data"),
    "dist": str(_BOOT / "dist"),
}), encoding="utf8")
os.environ["MOUSSE_SERVER_CONFIG"] = str(_BOOT / "server.json")
os.environ["MOUSSE_TREE_HOME"] = str(_BOOT / "mousse-tree")  # 世界树（openmousse_tree）导入时就定好家目录：测试不碰真的那棵树
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

import config  # noqa: E402 — 要在上面设好环境变量之后再导入


@pytest.fixture
def mousse_tree(tmp_path, monkeypatch):
    """一棵新的开源版世界树（openmousse_tree）放在 tmp_path：配置、库、规则、修剪计划都在这里，子进程（mousse-tree 命令行）也用它。
    make(storage="sqlite" | "markdown", lang="en", name="Alex", **配置) 写好 config.json → 配置；home、vault（Markdown 存储的笔记文件夹）。"""
    from openmousse_tree import config as C
    from openmousse_tree import store as S
    home = tmp_path / "mousse-tree"
    for attr, val in (("HOME", home), ("CONFIG", home / "config.json"), ("DB", home / "tree.db"), ("INDEX", home / "index.db"),
                      ("LOCK", home / "tree.lock")):
        monkeypatch.setattr(C, attr, val)
    monkeypatch.setenv("MOUSSE_TREE_HOME", str(home))
    monkeypatch.delenv("MOUSSE_AGENT", raising=False)
    monkeypatch.setattr(S, "_ready", set())

    class Tree:
        pass
    t = Tree()
    t.home, t.vault = home, tmp_path / "vault" / "Memory"

    def make(storage: str = "sqlite", lang: str = "en", name: str = "Alex", **extra) -> dict:
        home.mkdir(parents=True, exist_ok=True)
        t.vault.parent.mkdir(parents=True, exist_ok=True)
        cfg = {"language": lang, "owner_name": name, "timezone": "UTC", "profile_path": str(home / "USER.md"),
               "export_path": str(home / "export" / "TREE.md"), "storage": storage, "tokens": {}, "ui_token": "test", **extra}
        if storage == "markdown":
            cfg.setdefault("notes_dir", str(t.vault))
            t.vault.mkdir(parents=True, exist_ok=True)
        C.save(cfg)
        return cfg
    t.make = make
    return t


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
