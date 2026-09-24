"""Agent 的创建与删除。一个 Agent = OpenClaw 的一个独立 agent（自己的 workspace、MEMORY.md、skills 允许列表）+ app 里 groups 表的一行。

新建（provision）做四件事，任何一步失败就回滚前面的：
  1. 建 workspace：<openclaw_home>/workspace-<id>/，AGENTS.md（职责 + 通用规则）、IDENTITY.md、MEMORY.md、memory/；
     SOUL.md / USER.md 从主 workspace 复制（同一个人格、同一个用户）；skills 软链到主 workspace 的 skills。
  2. 备份 openclaw.json，直接写 agents.entries.<id>，再 `openclaw config validate`（不过就恢复备份）。Gateway 监视这个文件，agents.* 热加载，不用重启。
  3. server.json 的 agent_workspaces 加一项（对话路由、记忆页靠它）。
删除（remove）反过来：条目去掉、agent_workspaces 去掉、workspace 整个移到 <openclaw_home>/archive/（记忆永远不删）。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

from config import settings

ICON_EMOJI = {"dumbbell": "🏋️", "utensils": "🥗", "book": "📚", "wallet": "💷", "moon": "🌙", "briefcase": "💼", "heart": "❤️‍🩹", "plane": "✈️"}


class ProvisionError(Exception):
    pass


def openclaw_bin() -> str:
    return shutil.which(settings.openclaw_bin) or settings.openclaw_bin


def clean_output(p: subprocess.CompletedProcess) -> str:
    lines = [ln for ln in ((p.stdout or "") + "\n" + (p.stderr or "")).splitlines() if ln.strip() and not ln.startswith("[agents/harness]")]
    return "\n".join(lines)[-400:]


def validate_config() -> None:
    try:
        p = subprocess.run([openclaw_bin(), "config", "validate"], capture_output=True, text=True, timeout=60, check=False)  # noqa: S603
    except (OSError, subprocess.SubprocessError) as e:
        raise ProvisionError(f"跑不了 openclaw config validate：{e}") from e
    if p.returncode != 0:
        raise ProvisionError(f"openclaw config validate 不通过：{clean_output(p)}")


def write_entry(agent_id: str, entry: dict | None, tag: str) -> None:
    """改 openclaw.json 的 agents.entries.<id>（None = 删）：先备份，原子写入，再 openclaw config validate；不通过就恢复备份。
    不用 `openclaw config patch`：它的 dry-run 会对整份配置做模型引用解析，环境稍有不顺就整个拒绝。Gateway 会自己热加载这个文件。"""
    path = settings.openclaw_json
    try:
        data = json.loads(path.read_text(encoding="utf8"))
    except (OSError, ValueError) as e:
        raise ProvisionError(f"读不了 {path}：{e}") from e
    entries = data.setdefault("agents", {}).setdefault("entries", {})
    if entry is None:
        if agent_id not in entries:
            return
        del entries[agent_id]
    else:
        entries[agent_id] = entry
    backup = backup_openclaw_json(tag)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
    shutil.copymode(path, tmp)
    tmp.replace(path)
    try:
        validate_config()
    except ProvisionError:
        if backup:
            shutil.copy2(backup, path)
        raise


def backup_openclaw_json(tag: str) -> Path | None:
    src = settings.openclaw_json
    if not src.is_file():
        return None
    settings.backup_dir.mkdir(parents=True, exist_ok=True)
    dst = settings.backup_dir / f"openclaw.json.pre-{tag}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    shutil.copy2(src, dst)
    return dst


def agents_md(agent_id: str, name: str, purpose: str) -> str:
    app = settings.app_name
    digest = settings.openclaw_home / "shared/digest" / agent_id
    return f"""# AGENTS.md — {app} · {name}（Agent）

> {datetime.now(settings.tz).strftime('%Y-%m-%d')} 在 app 里建。你是 {app} 在「{name}」这一块的分身：独立工作区、独立记忆。主对话（main）是接待台，会把属于这一块的问题转给你。

## 职责

{purpose or '（还没写。第一次对话时问清楚这一块要管什么，然后把结论写进 MEMORY.md。）'}

不归你管的事：一句话告诉用户去主对话或对应的 Agent 说。

## 每次会话

- `SOUL.md`、`USER.md`、`MEMORY.md` 已注入。用户的完整档案在 `{settings.profile}`，`memory_search` 可查。
- 今天的流水在 `memory/YYYY-MM-DD.md`；上一天的日结在前一天文件末尾，开会话时看一眼。
- **要问用户的问题写在回复里，不要用 ask_user 之类等待输入的工具**：app 的通道没人能回答工具里的提问，会一直卡住。
- app 里这些分身叫 Agent。跟用户说话用「Agent」。

## 自动触发

消息以「【自动触发】」开头的，不是用户在说话，是系统按时间点发的。不要提问，直接做该做的事，回复两行以内。

## 日结（收到「【自动触发】日结」时）

1. 把今天的结论写到 `memory/YYYY-MM-DD.md` 末尾一节 `## 日结`：要点、用户说过的事、明天要盯的。5–10 行。
2. 同一段再写一份到 `{digest}/YYYY-MM-DD.md`。
3. 值得长期记住的写进 `MEMORY.md`，就地改，不追加矛盾条目。
4. 回复一行"日结好了"。

## 记忆 / 安全 / 时间

- 流水进 `memory/YYYY-MM-DD.md`，结论进 `MEMORY.md`；结构化数据走工具脚本。
- 不外泄私人数据；用户的数据只出现在给用户的回复里。
- 服务器时间可能不是用户所在地，报时按 `USER.md` 的时区换算。
"""


def identity_md(name: str, icon: str) -> str:
    app = settings.app_name
    return f"""# IDENTITY.md - Who Am I?

- **Name:** {app} · {name}
- **Creature:** {app} 的一个分身，专管「{name}」这一块。同一个 {app}，同一个脾气，只是范围小、记得深。
- **Vibe:** 说人话，不废话，直接。
- **Emoji:** {ICON_EMOJI.get(icon, '✨')}
- **Avatar:** 与主 {app} 相同。
"""


def memory_md(name: str) -> str:
    return f"""# MEMORY.md — {settings.app_name} · {name}

> {datetime.now(settings.tz).strftime('%Y-%m-%d')} 建。只放提炼后的结论，日结时维护。

## 观察到的规律

- （待积累）

## 规则与偏好（本块）

- （待积累）

## 当前状态

- {datetime.now(settings.tz).strftime('%Y-%m-%d')}：Agent 新建。
"""


def workspace_path(agent_id: str) -> Path:
    return settings.openclaw_home / f"workspace-{agent_id}"


def build_workspace(agent_id: str, name: str, purpose: str, icon: str) -> Path:
    ws = workspace_path(agent_id)
    if ws.exists() and any(ws.iterdir()):
        raise ProvisionError(f"{ws} 已经存在且不为空")
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "AGENTS.md").write_text(agents_md(agent_id, name, purpose), encoding="utf8")
    (ws / "IDENTITY.md").write_text(identity_md(name, icon), encoding="utf8")
    (ws / "MEMORY.md").write_text(memory_md(name), encoding="utf8")
    (ws / "memory").mkdir(exist_ok=True)
    for fn in ("SOUL.md", "USER.md"):
        src = settings.workspace / fn
        if src.is_file():
            shutil.copy2(src, ws / fn)
    skills = settings.workspace / "skills"
    if skills.is_dir():
        os.symlink(skills, ws / "skills")
    (settings.openclaw_home / "shared/digest" / agent_id).mkdir(parents=True, exist_ok=True)
    return ws


def provision(agent_id: str, name: str, purpose: str, icon: str = "moon", skills: list[str] | None = None) -> Path:
    if not agent_id.replace("-", "").isalnum() or not agent_id.islower():
        raise ProvisionError("agent id 只能是小写字母、数字和连字符")
    entry: dict = {"workspace": str(workspace_path(agent_id))}
    allow = skills if skills is not None else settings.agent_default_skills
    if allow:
        entry["skills"] = list(allow)
    ws = build_workspace(agent_id, name, purpose, icon)
    try:
        write_entry(agent_id, entry, f"agent-{agent_id}")
        settings.set_agent_workspace(agent_id, ws)
    except Exception:
        shutil.rmtree(ws, ignore_errors=True)
        raise
    time.sleep(2)  # 给 Gateway 热加载一点时间
    return ws


def remove(agent_id: str) -> Path | None:
    """去掉 OpenClaw 条目和路由；workspace 移到 archive/，返回归档路径。没 workspace 的（只在 groups 表里的旧 Group）只删路由。"""
    if agent_id == "main":
        raise ProvisionError("main 不能删")
    if agent_id in settings.agent_workspaces:
        write_entry(agent_id, None, f"remove-{agent_id}")
        settings.set_agent_workspace(agent_id, None)
    ws = workspace_path(agent_id)
    if not ws.exists():
        return None
    archive = settings.openclaw_home / "archive"
    archive.mkdir(parents=True, exist_ok=True)
    dst = archive / f"workspace-{agent_id}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    shutil.move(str(ws), str(dst))
    return dst
