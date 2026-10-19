"""每个 Agent 能用哪些技能（10/19 彩排：每个 Agent 都看得到全部几十个技能）。

server.json 的 engine.skills.agents.<Agent id> = 技能名的列表（照 SKILL.md 的 name，没写 name 就是文件夹名）；没写的 Agent 照旧看全部，
写空列表 = 一个都不给。名单上有、这台电脑上没有的不报错（可能还没装）。
  "engine": {"skills": {"agents": {"main": ["inbox", "journal", "agent-builder"], "g-diet": ["board", "inbox", "diet-log"]}}}

skills_for(agent) 是唯一的入口，管住这几处：
  会话开头的技能目录（engine/context.py 的 skills_part；后台任务子会话开头的目录 engine/tasks.py 的 child_system 也是）
  skill 工具（engine/tools.py：不在名单上的回「这个 Agent 没有这个技能」）
  经 /mcp 给外援的技能（Claude Code 跑主人回合、后台任务的外援拿到的就是同一个 skill 工具，engine/mcp_engine.py）
以后 13c 的手册搜索合进来时也调它。写自己的 skill（engine/skill_write.py）查同名照旧看全部：名单只管「给它看、给它读」。

谁写这份名单（10/19 第 1 批，都只在 claw 选 builtin 时；写 server.json 之前先备份，engine/setup_api.edit）：
  新建 Agent（data.create_group → agents.provision_builtin → register）：建好工作区，同一次写进路由和名单（请求给的 skills，没给就是
    agent_default_skills；都是空的就不写 = 看全部，同 OpenClaw）
  装日结提案里的 skill（proposals.grant → add）：给的那几个 Agent 写了名单的，把它加进去；没写名单的本来就看得到全部，不动
  app 里改（GET / PUT /api/setup/skills/{agent}，只认主人的设备，照 engine/setup_api.py 的 owner）：
    GET  → {"ok", "agent", "name", "claw", "applies", "limited"（写了名单没有）, "skills"（名单，没写 = null）,
            "available": [{"name", "description", "source": workspace | core, "on"}]（这个 Agent 在这台电脑上能有的）, "missing"（名单上有、这里没有的）}
    PUT  {"skills": [名字…]}（空列表 = 一个都不给）或 {"skills": null}（去掉名单 = 看全部）→ GET 的样子 + backup、changed；
         名字要是这个 Agent 能有的，或者名单上本来就有的（还没装的那种），不然 400 unknown；claw 不是 builtin → 409
  删 Agent（data.delete_group → forget）：去掉它的名单
"""
from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, Depends, Request

import claw
from i18n import L

from . import models, setup_api
from .setup_api import SetupError


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


# —— 写名单（见文件开头「谁写这份名单」）——————————————————————————————————————————————————

NAME = re.compile(r"[\w.-]{1,64}")  # 技能名：SKILL.md 的 name 或者文件夹名
MAX_NAMES = 200


def listed(agent: str) -> list[str] | None:
    """engine.skills.agents.<agent> 照写的顺序（去重、去空）；没写 → None。"""
    s = models.cfg().get("skills")
    a = s.get("agents") if isinstance(s, dict) else None
    v = a.get(agent) if isinstance(a, dict) else None
    return list(dict.fromkeys(str(x).strip() for x in v if str(x).strip())) if isinstance(v, list) else None


def put(data: dict, agent: str, names: list[str] | None) -> None:
    """在一份 server.json（dict，就地改）上写 engine.skills.agents.<agent>：names = 名单（空列表 = 一个都不给）；None = 去掉这一项
    （看全部），变空了的那几层一起去掉。"""
    if names is not None:
        setup_api.section(data, "engine", "skills", "agents")[agent] = list(names)
        return
    eng = data.get("engine")
    sk = eng.get("skills") if isinstance(eng, dict) else None
    a = sk.get("agents") if isinstance(sk, dict) else None
    if isinstance(a, dict) and agent in a:
        a.pop(agent)
        setup_api.prune(data, "engine", "skills", "agents")


def save(agent: str, names: list[str] | None) -> dict:
    """写这个 Agent 的名单（先备份）。→ setup_api.edit 的 {"changed", "backup"}；server.json 坏了 → SetupError(409)。"""
    return setup_api.edit(lambda d: put(d, agent, names))


def register(agent: str, workspace: Path, names: list[str] | None) -> dict:
    """claw 选 builtin 时新建 Agent（agents.provision_builtin）：一次写好工作区的路由（agent_workspaces.<id>：对话、记忆页、内核的规矩文件
    都按它找）和技能名单（names 空 / None = 不写，看全部，同 OpenClaw 那条路的 agent_default_skills）。先备份。→ {"changed", "backup"}"""
    def go(d: dict) -> None:
        aw = d.get("agent_workspaces")
        if not isinstance(aw, dict):
            aw = d["agent_workspaces"] = {}
        aw[agent] = str(workspace)
        if names:
            put(d, agent, names)
    return setup_api.edit(go)


def add(agents: list[str], name: str) -> list[str]:
    """把一个技能加进这些 Agent 的名单（装日结提案里的 skill，proposals.grant）：写了名单、名单上还没有它的才加；没写名单的本来就
    看得到全部，不动。先备份（没有要加的就不写、不备份）。→ 真的加了的 Agent。"""
    changed: list[str] = []

    def go(d: dict) -> None:
        changed.clear()
        eng = d.get("engine")
        sk = eng.get("skills") if isinstance(eng, dict) else None
        a = sk.get("agents") if isinstance(sk, dict) else None
        for aid in agents:
            v = a.get(aid) if isinstance(a, dict) else None
            if isinstance(v, list) and name not in {str(x).strip() for x in v}:
                v.append(name)
                changed.append(aid)
    setup_api.edit(go)
    return changed


def forget(agent: str) -> None:
    """删 Agent 时去掉它的名单（没有就什么都不写）。不往外抛：删 Agent 不能因为这一步没成。"""
    try:
        save(agent, None)
    except (SetupError, OSError) as e:
        print(f"[engine] {agent} 的技能名单没去掉：{type(e).__name__}: {e}")


# —— 接口：app 里看、改一个 Agent 的名单（只认主人的设备）——————————————————————————————————————

router = APIRouter(dependencies=[Depends(setup_api.owner)])


def agent_name(agent: str) -> str | None:
    """main 或 groups 表里的 Agent → 名字；没有这个 Agent → None。"""
    import bridges
    from config import settings
    if agent == "main":
        return settings.app_name
    if not bridges.known_agent(agent):
        return None
    import data
    return data.thread_names().get(agent) or agent


def view(agent: str) -> dict:
    """GET 的样子（见文件开头）。available 照 tools.list_skills：这个 Agent 在这台电脑上能有的（core 里要别的工具、它没有那个工具的不列），
    不按名单筛；on = 名单上有（没写名单 = 都有）。"""
    import bridges

    from . import tools as tools_mod
    found = tools_mod.list_skills(agent, _offered(agent))
    names = listed(agent)
    core = (bridges.REPO / "packs" / "core" / "skills").resolve()
    items = [{"name": n, "description": d, "source": "core" if p.resolve().is_relative_to(core) else "workspace", "on": names is None or n in names}
             for n, (d, p) in sorted(found.items())]
    out = {"agent": agent, "name": agent_name(agent), "claw": claw.kind(), "applies": claw.is_builtin(), "limited": names is not None,
           "skills": names, "available": items, "missing": [n for n in names or [] if n not in found]}
    if not claw.is_builtin():
        out["why"] = L("技能名单只管自带的内核；OpenClaw 的在 openclaw.json 的 agents.entries.<id>.skills",
                       "Skill lists only apply to the built-in engine; OpenClaw's live in agents.entries.<id>.skills in openclaw.json")
    return out


def _agent(agent: str) -> str:
    if agent_name(agent) is None:
        raise SetupError(404, L("没有这个 Agent", "No such Agent"))
    return agent


@router.get("/api/setup/skills/{agent}")
def get_skills(agent: str):
    """这个 Agent 的技能名单和它能有的技能。"""
    return {"ok": True, **view(_agent(agent))}


@router.put("/api/setup/skills/{agent}")
async def put_skills(agent: str, request: Request):
    """{"skills": [名字…] | null}：换掉这个 Agent 的名单（null = 去掉，看全部）。先备份 server.json。"""
    b = await setup_api.body_of(request)
    if not claw.is_builtin():
        raise SetupError(409, L("技能名单只管自带的内核：你的 claw 用它自己的（OpenClaw 在 openclaw.json 里）",
                                "Skill lists only apply to the built-in engine; your claw keeps its own (OpenClaw's are in openclaw.json)"))
    _agent(agent)
    if "skills" not in b:
        raise SetupError(400, L("要写 skills：技能名的列表，或者 null（看全部）", "skills is required: a list of skill names, or null for all of them"),
                         field="skills")
    v = b["skills"]
    names: list[str] | None = None
    if v is not None:
        if not isinstance(v, list) or len(v) > MAX_NAMES or not all(isinstance(x, str) and NAME.fullmatch(x.strip()) for x in v):
            raise SetupError(400, L(f"skills 要是技能名的列表（最多 {MAX_NAMES} 个；字母、数字、. _ -）",
                                    f"skills must be a list of skill names (at most {MAX_NAMES}; letters, digits, . _ -)"), field="skills")
        names = list(dict.fromkeys(x.strip() for x in v))
        from . import tools as tools_mod
        found, cur = tools_mod.list_skills(agent, _offered(agent)), set(listed(agent) or [])
        unknown = [n for n in names if n not in found and n not in cur]
        if unknown:
            raise SetupError(400, L(f"这个 Agent 没有这些技能：{'、'.join(unknown)}", f"This Agent has no such skills: {', '.join(unknown)}"),
                             field="skills", unknown=unknown)
    res = save(agent, names)
    if res["changed"]:
        import chat
        who = agent_name(agent)
        chat.log_activity(L(f"改了「{who}」能用的技能", f'Changed which skills "{who}" can use'), "edit")
    return {"ok": True, **view(agent), "backup": res["backup"], "changed": res["changed"]}
