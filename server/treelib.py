"""开源版世界树（仓库 tree/ 的 openmousse_tree 包，安装器用 `mousse-tree` 装的那一套）包成 memtree.py / connectors.py 要的样子。

sources.py 先找 workspace 的 scripts/memory_tree.py（作者自己的实例：Obsidian 库里的笔记，有「枝」）；没有它、而 openmousse_tree
已经 init 过（有 ~/.mousse-tree/config.json 或 MOUSSE_TREE_HOME 下的配置），就用这里。两边的配置格式一样（tokens: 令牌 → 平台名、
public_hosts、port），所以「接到你的 AI」两边通用。

和作者那份的区别：没有枝（branch_tree 空、挪枝不支持，HAS_BRANCHES = False）；存储可能是 SQLite（tree.db）或 Markdown 笔记文件夹；
只有 Markdown 存储才有 issue 表（格式有问题的笔记）。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from i18n import L

try:
    from openmousse_tree import config as C
    from openmousse_tree import store as S
except ImportError:  # 服务的 venv 里没装世界树
    C = S = None  # type: ignore[assignment]

HAS_BRANCHES = False
CURRENT = ("active", "pending")
TREE_UNIT = "mousse-tree"  # 安装器（mousse-tree install-service）装的 systemd 用户服务


def available() -> bool:
    """装了、也 init 过（有配置文件）。"""
    return C is not None and C.CONFIG.is_file()


def trunk() -> str:
    return L("档案", "Profile")


def __getattr__(name: str):
    # memtree.py 读 mt.TRUNK：按请求的语言给
    if name == "TRUNK":
        return trunk()
    raise AttributeError(name)


def connect(sync: bool = True) -> sqlite3.Connection:
    """store.connect() 对 Markdown 存储会先看一眼笔记文件夹；sync=False（连接页只数条数）也照样走它，开销很小。"""
    del sync
    return S.connect()


def branch_tree(conn: sqlite3.Connection) -> list[dict]:
    del conn
    return []


def set_status(conn: sqlite3.Connection, mid: str, status: str, actor: str) -> bool:
    return S.set_status(conn, mid, status, actor)


def set_branch(conn: sqlite3.Connection, mid: str, branch: str, actor: str) -> str:
    raise ValueError(L("这棵树没有枝", "This tree has no branches"))


def load_config() -> dict:
    return C.load()


def save_config(cfg: dict) -> None:
    C.save(cfg)


def notes_dir() -> Path | None:
    """Markdown 存储的笔记文件夹；SQLite 存储没有（connectors 的「Obsidian 库」据此判断，None = 不是库）。"""
    cfg = C.load()
    return C.notes_dir(cfg) if C.markdown(cfg) else None


def storage() -> dict:
    """{kind: markdown | sqlite, path}：世界树的真身放在哪。"""
    cfg = C.load()
    if C.markdown(cfg):
        return {"kind": "markdown", "path": str(C.notes_dir(cfg))}
    return {"kind": "sqlite", "path": str(C.DB)}
