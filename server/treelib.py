"""开源版世界树（仓库 tree/ 的 openmousse_tree 包，安装器用 `mousse-tree` 装的那一套）包成 memtree.py / connectors.py / 内核要的样子。

sources.py 先找 workspace 的 scripts/memory_tree.py（作者自己的实例：Obsidian 库里的笔记）；没有它、而 openmousse_tree
已经 init 过（有 ~/.mousse-tree/config.json 或 MOUSSE_TREE_HOME 下的配置），就用这里。两边的配置格式一样（tokens: 令牌 → 平台名、
public_hosts、port），所以「接到你的 AI」两边通用。

0.3 起（2026-10-16）开源版也有枝、规则和每周修剪，所有用户都有：
- branch_tree / set_branch 是真的（HAS_BRANCHES），主干的名字是存下来的那个（config 的 trunk，「档案」/ Profile），和请求的语言无关。
  内核按枝给功能 Agent 看叶子（engine/context.py 的 tree_part）照旧看枝的 agents。
- 新建 Agent 时（data.create_group）给它长一根小枝（agents 填它的 id）；服务器起来时给还没有枝的老 Agent 补上（sync_agents，
  补过的记下来，主人删了那根枝也不再长回来）。
- think.py 不起子进程，直接调这里（branch_names、remember）；tree 工具跑同一个 venv 里的 mousse-tree（bridges.tree_command）。
- 每周修剪的收件箱卡、点了以后照计划做在 treeprune.py。
存储可能是 SQLite（tree.db）或 Markdown 笔记文件夹；只有 Markdown 存储才有 issue 表（格式有问题的笔记）。
"""
from __future__ import annotations

import contextlib
import json
import sqlite3
import sys
from pathlib import Path

from i18n import L

try:
    from openmousse_tree import branches as B
    from openmousse_tree import config as C
    from openmousse_tree import store as S
except ImportError:  # 服务的 venv 里没装世界树
    B = C = S = None  # type: ignore[assignment]

PACKAGE = True  # sources.memory_tree 是这里 = 选中的是开源版（不是 workspace 的脚本）
HAS_BRANCHES = S is not None and hasattr(S, "set_branch")  # 0.2 的包没有枝
HAS_PROVENANCE = S is not None and getattr(S, "HAS_PROVENANCE", False)  # 0.4 起叶子带出处和「用得上的时候」（memtree.py 据此读这几列）
CURRENT = ("active", "pending")
TREE_UNIT = "mousse-tree"  # 安装器（mousse-tree install-service）装的 systemd 用户服务


def available() -> bool:
    """装了、也 init 过（有配置文件）。"""
    return C is not None and C.CONFIG.is_file()


def selected() -> bool:
    """sources.py 选中的就是这里（不是作者实例的脚本）。"""
    import sources
    return sources.memory_tree is sys.modules[__name__]


def trunk() -> str:
    return S.trunk() if HAS_BRANCHES else L("档案", "Profile")


def __getattr__(name: str):
    # memtree.py、engine/context.py 读 mt.TRUNK
    if name == "TRUNK":
        return trunk()
    raise AttributeError(name)


def connect(sync: bool = True) -> sqlite3.Connection:
    """store.connect()：Markdown 存储默认先看一眼笔记文件夹；sync=False 只读索引（内核备料、连接页只数条数）。"""
    return S.connect(sync=sync) if HAS_BRANCHES else S.connect()


def branch_tree(conn: sqlite3.Connection) -> list[dict]:
    """先序：name、parent、about、agents（逗号连起来的 Agent id）、depth（大枝 1）、leaves（直接挂在上面的当前叶子数）。"""
    return B.tree(conn) if HAS_BRANCHES else []


def set_status(conn: sqlite3.Connection, mid: str, status: str, actor: str) -> bool:
    return S.set_status(conn, mid, status, actor)


def set_branch(conn: sqlite3.Connection, mid: str, branch: str, actor: str) -> str:
    """挪枝 → 挪到的那根的名字（主干就是主干的名字）。枝对不上抛 ValueError（memtree 回 409）。"""
    if not HAS_BRANCHES:
        raise ValueError(L("这棵树没有枝", "This tree has no branches"))
    return S.set_branch(conn, mid, branch, actor)


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


# —— think.py（想完了：要记的写一片叶子）——————————————————————————————————————————

def branch_names() -> list[str]:
    """主干和所有的枝（先序），给模型挑 branch 用。"""
    with contextlib.closing(connect()) as conn:
        return [trunk(), *(b["name"] for b in branch_tree(conn))]


def remember(text: str, *, source: str, kind: str = "fact", tags: str = "", branch: str | None = None, quote: str = "", platform: str = "",
             thread: str = "", message: str = "", cues=None, supersedes: str | None = None) -> dict:
    """写一片叶子（查重、自动挂枝都在包里）→ {"id", "duplicate", "branch", …}。出处（quote / platform / thread / message）、cues、
    supersedes 可选（0.4 起的包才有出处：老包不认就不带）。"""
    extra = {"quote": quote, "platform": platform, "thread": thread, "message": message, "cues": cues} if HAS_PROVENANCE else {}
    with contextlib.closing(connect()) as conn:
        status = "pending" if C.load().get("require_confirm") and source != "owner" else "active"
        r = S.add(conn, text=text, source=source, kind=kind, tags=tags, status=status, branch=branch or None, agent=source,
                  supersedes=supersedes or None, **extra)
        S.export(conn)
    return r


# —— Agent 的枝 ——————————————————————————————————————————————————————————————

def agent_created(gid: str, name: str, purpose: str = "") -> str | None:
    """新建了一个 Agent（data.create_group）：选中的是开源版就给它长一根小枝 → 枝名。出错不影响建 Agent。"""
    if not HAS_BRANCHES or not available() or not selected():
        return None
    try:
        with contextlib.closing(connect()) as conn:
            got = B.ensure_agent(conn, gid, name, purpose, actor="openmousse")
            _seen(conn, {gid})
            return got
    except Exception as e:  # noqa: BLE001
        print(f"[tree] 没给 Agent {gid} 长出枝：{type(e).__name__}: {e}", file=sys.stderr)
        return None


def _seen(conn: sqlite3.Connection, add: set[str] | None = None) -> set[str]:
    """长过枝的 Agent（记在世界树的 meta 里）：主人删了那根枝也不再长回来。"""
    r = conn.execute("SELECT v FROM meta WHERE k='openmousse_agents'").fetchone()
    try:
        have = set(json.loads(r[0])) if r else set()
    except ValueError:
        have = set()
    if add and not add <= have:
        have |= add
        conn.execute("INSERT OR REPLACE INTO meta(k, v) VALUES ('openmousse_agents', ?)", (json.dumps(sorted(have)),))
        conn.commit()
    return have


def sync_agents() -> list[str]:
    """服务器起来时：groups 表里还没长过枝的 Agent 补上一根（老用户升级的第一次）→ 这次补了哪几个。"""
    if not HAS_BRANCHES or not available() or not selected():
        return []
    import chat
    try:
        with chat._lock, chat.db() as db:
            groups = [(r["id"], r["name"], r["purpose"] or "") for r in db.execute("SELECT id, name, purpose FROM groups ORDER BY created_at")]
    except sqlite3.Error:  # 新实例还没有 groups 表
        return []
    out = []
    with contextlib.closing(connect()) as conn:
        seen = _seen(conn)
        for gid, name, purpose in groups:
            if gid in seen:
                continue
            try:
                B.ensure_agent(conn, gid, name, purpose, actor="openmousse")
                out.append(gid)
            except (ValueError, RuntimeError, OSError) as e:
                print(f"[tree] 没给 Agent {gid} 长出枝：{e}", file=sys.stderr)
                continue
            _seen(conn, {gid})
    return out


# —— 忘掉一片叶子：档案里从它来的句子 ——————————————————————————————————————————

def derived_profile(conn: sqlite3.Connection, mid: str) -> list[dict]:
    return S.derived_profile(conn, mid) if HAS_BRANCHES else []


def drop_derived(conn: sqlite3.Connection, mid: str, actor: str) -> list[str]:
    return S.drop_derived(conn, mid, actor) if HAS_BRANCHES else []


def prune():
    """每周修剪的那一套（openmousse_tree.prune）；包太旧没有就 None。"""
    if not HAS_BRANCHES:
        return None
    from openmousse_tree import prune as P
    return P
