"""世界树（「我 → 世界树」，2026-09-27）：你在 Claude、ChatGPT、Gemini 和各个 Agent 那里说过的关于你的事，一条一片叶子，挂在枝上。

真身是 Obsidian 库里的笔记（世界树/，一条一篇）。索引、写入、遗忘全部交给 workspace 的 scripts/memory_tree.py
（可选数据源 tree，见 sources.py；没有它，这两个接口回 ok=false + missing_source=tree）。这里只替 app 读，外加三件事：
确认（pending → active）、忘记（retracted：笔记掏空成只剩属性的空壳挪进归档，索引里删掉）、挪到别的枝。
memory_tree 的函数都拿跨进程锁、会先按库的最新状态重建索引，所以放进线程里跑；sqlite 连接在同一个线程里开、关。

  GET  /api/tree        枝（先序：大枝后面跟着它的小枝；leaves = 直接挂在这根枝上的叶子数，total = 连小枝一起的）
                        + 当前的叶子（active / pending，不含档案要点；挂的枝不存在的算主干「档案」）+ 档案条数 + 各来源条数 + 格式有问题的笔记数。
                        叶子的 source 是笔记里写的；origin 是最早记下它的平台：每周修剪改写 / 合并出来的（source=prune）顺着 supersedes 找回去，
                        「按来源」和 counts.bySource 都按 origin 算
  POST /api/tree/{id}   {action: confirm | forget | move, branch?}：确认 / 忘记 / 挪枝（branch 写枝名，写「档案」= 直接挂主干）
  GET  /api/tree/connect             接到你的 AI：真身放在哪、公网地址、每个平台的接入地址（含令牌）、怎么接、要贴的那句指令
  POST /api/tree/platforms {name}    加一个平台（DeepSeek、通义千问、Kimi……）：发一个新令牌，重启世界树服务
  DELETE /api/tree/platforms/{id}    删掉一个平台：它的地址立即作废，重启世界树服务

两种世界树都能接：作者实例的 workspace scripts/memory_tree.py（有枝），或安装器装的开源版（treelib.py，没有枝，挪枝不支持）。

活动记录由 memory_tree 自己写，只写做了什么、不写记忆内容；actor 是「你 · 世界树」。这里不打印、不记录叶子的内容。
"""
from __future__ import annotations

import asyncio
import contextlib
import re
import secrets
import sqlite3
import subprocess
import time
import urllib.request
from types import ModuleType

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import sources
from chat import _lock, db
from config import settings
from i18n import L

router = APIRouter()
ID_RE = re.compile(r"[\w-]{1,64}")
ACTIONS = ("confirm", "forget", "move")
# 各 AI 平台（世界树 MCP 的接入名 → 显示名）；Grava 自己的 Agent 写的是 grava-<agent id>
PLATFORMS = {"claude": "Claude", "chatgpt": "ChatGPT", "gemini": "Gemini", "claude-code": "Claude Code", "notion": "Notion",
             "deepseek": "DeepSeek", "qwen": "Qwen", "kimi": "Kimi"}
PLATFORM_ID = re.compile(r"[a-z][a-z0-9-]{1,23}")


def tree() -> ModuleType:
    """memory_tree 脚本；没接就抛 NoSource（main.py 变成 200 + ok=false + missing_source=tree）。"""
    sources.require("tree")
    return sources.memory_tree


def agent_names() -> dict[str, str]:
    """Agent id → 名字（groups 表）；main 是助手自己。"""
    try:
        with _lock, db() as conn:
            names = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM groups")}
    except sqlite3.Error:  # 新实例还没有 groups 表
        names = {}
    return {**names, "main": settings.app_name}


AGENT_PREFIXES = ("grava-", "openclaw-")  # 作者的实例写 grava-<id>；开源版的 memory-tree skill 写 openclaw-<id>


def agent_of(source: str) -> str | None:
    """grava-<id> / openclaw-<id> → 那个 Agent 的 id（grava / grava-main / openclaw-main → main）；别的来源不是 Agent。"""
    if source == "grava":
        return "main"
    prefix = next((p for p in AGENT_PREFIXES if source.startswith(p)), None)
    return (source.removeprefix(prefix) or "main") if prefix else None


def source_name(source: str, agents: dict[str, str]) -> str:
    """来源的显示名：AI 平台、Agent 名、你自己（手机上直接写的笔记）、每周修剪（修剪时合并 / 改写出来的）。"""
    if source in PLATFORMS:
        return PLATFORMS[source]
    if source in ("leo", "owner"):  # 手机上直接写的笔记（作者的实例）/ mousse-tree 管理页、命令行（开源版）
        return L("你自己", "You")
    if source == "prune":
        return L("每周修剪", "Weekly pruning")
    aid = agent_of(source)
    if aid:
        return agents.get(aid) or aid
    return source


def origin_of(mid: str, links: dict[str, tuple[str, str | None]]) -> str:
    """每周修剪改写 / 合并出来的叶子（source=prune）顺着 supersedes 找回最早是哪个平台记下的；找不到就还是 prune。"""
    source, seen = links[mid][0], {mid}
    cur = links[mid][1]
    while source == "prune" and cur and cur in links and cur not in seen:
        seen.add(cur)
        source, cur = links[cur]
    return source


def snapshot() -> dict:
    """整棵树（在线程里跑）。"""
    mt = sources.memory_tree
    agents = agent_names()
    branch_col = "branch" if getattr(mt, "HAS_BRANCHES", True) else "NULL AS branch"  # 开源版世界树没有枝
    with contextlib.closing(mt.connect()) as conn:  # connect() 先按库的最新状态刷新索引
        branches = mt.branch_tree(conn)
        rows = conn.execute(
            f"SELECT id, text, kind, source, status, {branch_col}, tags, observed_at, created_at FROM tree "  # noqa: S608 — 列名是常量
            "WHERE source != 'profile' AND status IN ('active','pending') ORDER BY observed_at DESC, created_at DESC").fetchall()
        links = {r["id"]: (r["source"], r["supersedes"]) for r in conn.execute("SELECT id, source, supersedes FROM tree WHERE source != 'profile'")}
        trunk = conn.execute("SELECT COUNT(*) FROM tree WHERE source = 'profile'").fetchone()[0]
        issues = 0
        with contextlib.suppress(sqlite3.Error):  # issue 表只有笔记存储才有
            issues = conn.execute("SELECT COUNT(*) FROM issue").fetchone()[0]
    names = {b["name"] for b in branches}
    total = {b["name"]: b["leaves"] for b in branches}
    for b in reversed(branches):  # 先序倒过来走：小枝先算完，再加到上一级
        if b["parent"] in total:
            total[b["parent"]] += total[b["name"]]
    leaves = []
    for r in rows:
        origin = origin_of(r["id"], links)
        leaves.append({"id": r["id"], "text": r["text"], "kind": r["kind"], "source": r["source"], "sourceName": source_name(r["source"], agents),
                       "origin": origin, "originName": source_name(origin, agents), "agent": agent_of(origin),
                       "status": r["status"], "branch": r["branch"] if r["branch"] in names else mt.TRUNK,
                       "tags": [t for t in (r["tags"] or "").split(",") if t], "observedAt": r["observed_at"], "createdAt": r["created_at"]})
    per: dict[str, int] = {}
    for x in leaves:  # 按最早记下它的平台数（修剪过的算回原来的平台）
        per[x["origin"]] = per.get(x["origin"], 0) + 1
    by_source = [{"source": s, "name": source_name(s, agents), "agent": agent_of(s), "count": n}
                 for s, n in sorted(per.items(), key=lambda kv: -kv[1])]
    return {
        "ok": True,
        "trunk": {"name": mt.TRUNK, "count": trunk},
        "branches": [{"name": b["name"], "parent": b["parent"], "about": b["about"], "depth": b["depth"], "leaves": b["leaves"],
                      "total": total[b["name"]], "agents": [{"id": a, "name": agents.get(a) or a} for a in b["agents"].split(",") if a]}
                     for b in branches],
        "leaves": leaves,
        "counts": {"total": len(leaves), "pending": sum(1 for x in leaves if x["status"] == "pending"), "bySource": by_source},
        "issues": issues,
        "branchable": getattr(mt, "HAS_BRANCHES", True),
        "storage": storage_of(mt),
    }


@router.get("/api/tree")
async def get_tree():
    tree()
    try:
        return await asyncio.to_thread(snapshot)
    except (OSError, RuntimeError, sqlite3.Error) as exc:
        raise HTTPException(503, L(f"世界树读不了：{exc}", f"Couldn't read the memory tree: {exc}")) from exc


# —— 接到你的 AI（「我 → 世界树」最上面那块，2026-09-28；路由要排在 POST /api/tree/{id} 前面，不然 platforms 会被当成叶子 id）——————————————————————————————
# 世界树给每个平台一个令牌：令牌在地址里（/t/<令牌>/mcp，Claude.ai、ChatGPT 这类不带认证的连接器），或者放请求头（/m/mcp，Notion 这类）。
# 令牌就是平台名的钥匙：树按令牌知道是谁写的，叶子的 source 就是平台名。加 / 删平台要重启世界树服务（路由按令牌在启动时建好）。

INSTRUCTION = ("对话开始先调 profile 和 recall 了解我；我说出关于自己的新事实、偏好、决定、近况时调 remember。",
               "Call profile and recall at the start of a conversation to know me; when I state a new fact, preference, decision or update about myself, call remember.")
PRESETS = ("claude", "chatgpt", "gemini", "notion", "claude-code", "deepseek", "qwen", "kimi")


def platform_name(pid: str) -> str:
    return L("通义千问", "Qwen") if pid == "qwen" else PLATFORMS.get(pid, pid)


def guide(pid: str) -> tuple[str, list[str]]:
    """(auth, 步骤)：auth = path（地址里带令牌）| header（地址 /m/mcp，令牌放请求头）。步骤里的 {url} 由 app 换成地址。"""
    paste = L("把下面那句指令贴进它的自定义指令，关掉它自带的记忆，免得两边记的不一样。",
              "Paste the instruction below into its custom instructions and turn off its own memory, so the two don't drift apart.")
    known = {
        "claude": ("path", [L("打开 claude.ai → 设置 → 连接器（Connectors）→ 添加自定义连接器。", "Open claude.ai → Settings → Connectors → Add custom connector."),
                            L("名字写「世界树」，地址粘贴上面这个，认证留空。", "Name it Memory tree, paste the address above, leave authentication empty."), paste]),
        "chatgpt": ("path", [L("打开 ChatGPT → 设置 → Apps & Connectors → 高级，打开 Developer mode（要 Plus 以上）。",
                               "Open ChatGPT → Settings → Apps & Connectors → Advanced and turn on Developer mode (Plus or above)."),
                             L("点「创建」，地址粘贴上面这个，认证选 None。", "Tap Create, paste the address above, authentication None."), paste]),
        "gemini": ("path", [L("打开 gemini.google.com → 设置 → Connected Apps → 添加自定义 app（官方目前要求人在美国）。",
                              "Open gemini.google.com → Settings → Connected Apps → Add a custom app (Google currently requires you to be in the US)."),
                            L("地址粘贴上面这个。", "Paste the address above."), paste]),
        "notion": ("header", [L("Notion 的 Custom Agent → Tools & Access → Custom MCP server（要 Business 版以上）。",
                                "In Notion, open a Custom Agent → Tools & Access → Custom MCP server (Business plan or above)."),
                              L("地址填上面这个，认证选 Bearer token，值填下面的令牌。", "Paste the address above, pick Bearer token authentication and paste the token below."),
                              L("把下面那句指令写进这个 Agent 的说明。", "Put the instruction below into the Agent's instructions.")]),
        "claude-code": ("path", [L("在终端运行：claude mcp add --transport http tree {url}", "In a terminal run: claude mcp add --transport http tree {url}"),
                                 L("把下面那句指令加进 CLAUDE.md。", "Add the instruction below to CLAUDE.md.")]),
        "qwen": ("path", [L("通义千问的网页版还不能加自定义 MCP，用它的命令行工具 Qwen Code：在 ~/.qwen/settings.json 的 mcpServers 里加一项 \"tree\": {\"httpUrl\": \"{url}\"}。",
                            "Qwen's web chat can't add custom MCP servers yet, so use Qwen Code: in ~/.qwen/settings.json add \"tree\": {\"httpUrl\": \"{url}\"} under mcpServers."),
                          L("在 Qwen Code 里输入 /mcp，看到 tree 就接上了。", "Type /mcp in Qwen Code; when tree shows up it's connected."),
                          L("把下面那句指令加进 QWEN.md。", "Add the instruction below to QWEN.md.")]),
        "kimi": ("path", [L("Kimi 的网页版还不能加自定义 MCP，用它的命令行工具 Kimi Code：在终端运行 kimi mcp add --transport http tree {url}",
                            "Kimi's web chat can't add custom MCP servers yet, so use Kimi Code: run kimi mcp add --transport http tree {url}"),
                          L("在 Kimi Code 里用 /mcp 看一眼，tree 在就接上了。", "Check with /mcp in Kimi Code; if tree is listed it's connected."), paste]),
    }
    if pid in known:
        return known[pid]
    return ("path", [L("在这个 AI 里找「MCP」「连接器」或「工具」的设置，添加一个远程 MCP 服务（Streamable HTTP），地址粘贴上面这个，不用认证。",
                       "In that AI, find the MCP / connectors / tools settings and add a remote MCP server (Streamable HTTP) with the address above, no authentication."),
                     L("它的网页版不支持自定义 MCP 的话，用它家的命令行工具，或者任何支持 MCP 的客户端配上它的模型。",
                       "If its web app can't add custom MCP servers, use its command-line tool, or any MCP-capable client set up with its model."), paste])


def tree_unit() -> str | None:
    """世界树的 systemd 用户服务：作者的实例叫 grava-tree，安装器装的叫 mousse-tree。"""
    for unit in ("grava-tree", "mousse-tree"):
        try:
            r = subprocess.run(["systemctl", "--user", "show", "-p", "LoadState", "--value", f"{unit}.service"], capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            return None
        if r.stdout.strip() == "loaded":
            return unit
    return None


def restart_tree(port: int) -> bool:
    """重启世界树服务，等它的 /health 回来（最多 12 秒）。没有 systemd 服务 = False，app 提示手动重启。"""
    unit = tree_unit()
    if not unit:
        return False
    try:
        subprocess.run(["systemctl", "--user", "restart", f"{unit}.service"], capture_output=True, timeout=20, check=True)
    except (OSError, subprocess.SubprocessError):
        return False
    for _ in range(24):
        time.sleep(0.5)
        with contextlib.suppress(OSError):
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as r:  # noqa: S310 — 本机
                if r.status == 200:
                    return True
    return False


def storage_of(mt: ModuleType) -> dict:
    """真身放在哪：开源版自己报；作者的实例是 Obsidian 库里的笔记（memory_tree.NOTES）。"""
    if hasattr(mt, "storage"):
        return mt.storage()
    notes = getattr(mt, "NOTES", None)
    return {"kind": "markdown", "path": str(notes)} if notes else {"kind": "unknown", "path": ""}


def connect_info() -> dict:
    """「接到你的 AI」要的一切（在线程里跑）。含令牌，只给带了访问令牌的 app。"""
    mt = sources.memory_tree
    cfg = mt.load_config()
    port = int(cfg.get("port") or 8787)
    hosts = [h for h in cfg.get("public_hosts") or [] if isinstance(h, str) and h]
    base = f"https://{hosts[0]}" if hosts else None
    last: dict[str, str] = {}
    with contextlib.suppress(Exception):  # noqa: BLE001 — 读不到就不显示「最近写过」
        with contextlib.closing(mt.connect(sync=False)) as conn:  # 不刷新：MCP 服务每 5 秒刷一次
            last = {r[0]: r[1] for r in conn.execute("SELECT source, MAX(created_at) FROM tree WHERE source != 'profile' GROUP BY source")}
    by_platform: dict[str, str] = {}
    for tok, pid in (cfg.get("tokens") or {}).items():
        if isinstance(pid, str) and isinstance(tok, str):
            by_platform.setdefault(pid, tok)
    platforms = []
    for pid in sorted(by_platform, key=lambda p: (PRESETS.index(p) if p in PRESETS else len(PRESETS), p)):
        auth, steps = guide(pid)
        tok = by_platform[pid]
        platforms.append({
            "id": pid, "name": platform_name(pid), "auth": auth, "steps": steps, "lastWrote": last.get(pid),
            "url": (f"{base}/t/{tok}/mcp" if auth == "path" else f"{base}/m/mcp") if base else None,
            "token": tok if auth == "header" else None,
        })
    return {
        "ok": True,
        "storage": storage_of(mt),
        "public": base,
        "funnel": None if base else [f"tailscale funnel --bg --set-path=/t http://127.0.0.1:{port}/t",
                                     f"tailscale funnel --bg --set-path=/m http://127.0.0.1:{port}/m"],
        "restartable": tree_unit() is not None,
        "instruction": L(*INSTRUCTION),
        "platforms": platforms,
        "presets": [{"id": p, "name": platform_name(p)} for p in PRESETS if p not in by_platform],
    }


@router.get("/api/tree/connect")
async def get_connect():
    tree()
    try:
        return await asyncio.to_thread(connect_info)
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
        raise HTTPException(503, L(f"世界树的配置读不了：{exc}", f"Couldn't read the memory tree's settings: {exc}")) from exc


class PlatformIn(BaseModel):
    name: str


def change_platform(pid: str, add: bool) -> dict:
    """加（已经有就原样返回，不重启）/ 删一个平台的令牌，然后重启世界树服务（在线程里跑）。"""
    mt = sources.memory_tree
    cfg = mt.load_config()
    tokens: dict[str, str] = cfg.setdefault("tokens", {})
    have = [t for t, name in tokens.items() if name == pid]
    if add and have:
        return {"changed": False}
    if not add and not have:
        raise LookupError(pid)
    for t in have:
        del tokens[t]
    if add:
        tokens[secrets.token_urlsafe(24)] = pid
    mt.save_config(cfg)
    return {"changed": True, "restarted": restart_tree(int(cfg.get("port") or 8787))}


@router.post("/api/tree/platforms")
async def add_platform(body: PlatformIn):
    tree()
    pid = re.sub(r"[\s_]+", "-", body.name.strip().lower())
    if not PLATFORM_ID.fullmatch(pid):
        raise HTTPException(400, L("平台名用英文字母、数字和连字符，2 到 24 个字，比如 deepseek", "Use letters, digits and hyphens, 2–24 characters, e.g. deepseek"))
    try:
        res = await asyncio.to_thread(change_platform, pid, True)
    except (OSError, ValueError) as exc:
        raise HTTPException(503, L(f"没加上：{exc}", f"Couldn't add it: {exc}")) from exc
    return {"ok": True, "id": pid, **res}


@router.delete("/api/tree/platforms/{pid}")
async def remove_platform(pid: str):
    tree()
    if not PLATFORM_ID.fullmatch(pid):
        raise HTTPException(404, L("没有这个平台", "No such platform"))
    try:
        res = await asyncio.to_thread(change_platform, pid, False)
    except LookupError as exc:
        raise HTTPException(404, L("没有这个平台", "No such platform")) from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(503, L(f"没删掉：{exc}", f"Couldn't remove it: {exc}")) from exc
    return {"ok": True, **res}


class TreeAction(BaseModel):
    action: str
    branch: str | None = None  # move：枝名（或别名）；「档案」= 直接挂主干


def act(mid: str, action: str, branch: str, actor: str) -> dict | None:
    """确认 / 忘记 / 挪枝（在线程里跑）。叶子不存在、是档案要点、已经不是当前的 → None。"""
    mt = sources.memory_tree
    with contextlib.closing(mt.connect()) as conn:
        branch_col = "branch" if getattr(mt, "HAS_BRANCHES", True) else "NULL AS branch"
        row = conn.execute(f"SELECT status, source, {branch_col} FROM tree WHERE id=?", (mid,)).fetchone()  # noqa: S608 — 列名是常量
        if not row or row["source"] == "profile" or row["status"] not in mt.CURRENT:
            return None
        if action == "confirm":
            if row["status"] == "active":
                return {"changed": False}
            return {"changed": True} if mt.set_status(conn, mid, "active", actor) else None
        if action == "forget":
            return {"changed": True} if mt.set_status(conn, mid, "retracted", actor) else None
        where = mt.set_branch(conn, mid, branch, actor)  # 枝对不上抛 ValueError
        return {"changed": where != (row["branch"] or mt.TRUNK), "branch": where}


@router.post("/api/tree/{mid}")
async def tree_action(mid: str, body: TreeAction):
    tree()
    if body.action not in ACTIONS:
        raise HTTPException(400, L("action 只能是 confirm、forget 或 move", "action must be confirm, forget or move"))
    branch = " ".join((body.branch or "").split())
    if body.action == "move" and not branch:
        raise HTTPException(400, L("挪到哪根枝？branch 不能空", "Move it where? branch can't be empty"))
    if not ID_RE.fullmatch(mid):
        raise HTTPException(404, L("没有这片叶子", "No such leaf"))
    try:
        res = await asyncio.to_thread(act, mid, body.action, branch, L("你 · 世界树", "You · Memory tree"))
    except ValueError as exc:  # 枝对不上：刚在 Obsidian 里改了名或删了
        raise HTTPException(409, L("没有这根枝了，刷新再挪", "That branch is gone. Refresh and try again.")) from exc
    except (OSError, RuntimeError, sqlite3.Error) as exc:
        raise HTTPException(503, L(f"世界树没改成：{exc}", f"Couldn't change the memory tree: {exc}")) from exc
    if res is None:
        raise HTTPException(404, L("这片叶子已经不在了，可能刚在别处改过。刷新看看。", "That leaf is gone; it may have just changed elsewhere. Refresh."))
    return {"ok": True, **res}
