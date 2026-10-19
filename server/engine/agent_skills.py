"""每个 Agent 能用哪些技能（10/19 彩排：每个 Agent 都看得到全部几十个技能）。

server.json 的 engine.skills.agents.<Agent id> = 技能名的列表（照 SKILL.md 的 name，没写 name 就是文件夹名）；没写的 Agent 照旧看全部，
写空列表 = 一个都不给。名单上有、这台电脑上没有的不报错（可能还没装）。
  "engine": {"skills": {"agents": {"main": ["inbox", "journal", "agent-builder"], "g-diet": ["board", "inbox", "diet-log"]}}}

skills_for(agent) 是唯一的入口，管住这几处：
  会话开头的技能目录（engine/context.py 的 skills_part；后台任务子会话开头的目录 engine/tasks.py 的 child_system 也是）
  skill 工具（engine/tools.py：不在名单上的回「这个 Agent 没有这个技能」）
  经 /mcp 给外援的技能（Claude Code 跑主人回合、后台任务的外援拿到的就是同一个 skill 工具，engine/mcp_engine.py）
以后 13c 的手册搜索合进来时也调它。写自己的 skill（engine/skill_write.py）查同名照旧看全部：名单只管「给它看、给它读」。
"""
from __future__ import annotations

from pathlib import Path

from . import models


def names_for(agent: str) -> set[str] | None:
    """engine.skills.agents.<agent> 写的名单；没写（或者写的不是列表）→ None = 全部。"""
    s = models.cfg().get("skills")
    a = s.get("agents") if isinstance(s, dict) else None
    v = a.get(agent) if isinstance(a, dict) else None
    return {str(x).strip() for x in v if str(x).strip()} if isinstance(v, list) else None


def _offered(agent: str) -> set[str]:
    """这个 Agent 能有的工具名（tools.list_skills 按它去掉要别的工具的 core 技能）：命令行工具 + 后台任务。"""
    from . import tools as tools_mod
    out = set(tools_mod.offered(agent))
    if tools_mod.levels().get("tasks") != tools_mod.OFF:
        out.add("tasks")
    return out


def skills_for(agent: str, offered: set[str] | None = None) -> dict[str, tuple[str, Path]]:
    """这个 Agent 能用的技能：名字 → (一句说明, SKILL.md)。tools.list_skills（工作区的和 core 的，core 里要某个工具、这个 Agent 没有
    那个工具的不列）再按名单筛。offered：这一轮它有的工具名（不给 = 按它能有的命令行工具和后台任务算）。"""
    from . import tools as tools_mod
    me = agent or "main"
    found = tools_mod.list_skills(me, _offered(me) if offered is None else offered)
    allowed = names_for(me)
    return found if allowed is None else {n: v for n, v in found.items() if n in allowed}


def withheld(agent: str, name: str, offered: set[str] | None = None) -> bool:
    """有这个技能、只是这个 Agent 的名单上没有它（skill 工具据此回「这个 Agent 没有这个技能」，和「根本没有」分开说）。"""
    from . import tools as tools_mod
    me = agent or "main"
    allowed = names_for(me)
    return allowed is not None and name not in allowed and name in tools_mod.list_skills(me, _offered(me) if offered is None else offered)
