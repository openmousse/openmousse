"""树包的测试：先把仓库里的 tree/ 放进导入路径。

venv 里装着一份旧的 openmousse_tree，不改它。子进程（mousse-tree / python -m）靠 PYTHONPATH 认到同一份。
MOUSSE_TREE_HOME 在导入前指到临时目录，config 在导入时就定好家目录，测试不碰真的那棵树。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

TREE = Path(__file__).resolve().parents[1]
if str(TREE) not in sys.path:
    sys.path.insert(0, str(TREE))
_py = os.environ.get("PYTHONPATH", "")
if str(TREE) not in _py.split(os.pathsep):
    os.environ["PYTHONPATH"] = str(TREE) + (os.pathsep + _py if _py else "")
os.environ.setdefault("MOUSSE_TREE_HOME", tempfile.mkdtemp(prefix="mousse-tree-"))

import pytest  # noqa: E402 — 要等上面把导入路径放好


@pytest.fixture
def tree_home(tmp_path, monkeypatch):
    """一棵新的树，放在 tmp_path。子进程读得到同一个家目录。"""
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

    def make(storage: str = "sqlite", lang: str = "en", name: str = "Mira", **extra) -> dict:
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
