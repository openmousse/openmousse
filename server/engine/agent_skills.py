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

第 5 批（engine/skill_gate.py）：skills_for 再过门槛（要的程序、环境变量、配置、系统不满足的，engine.skills.entries.<名字>.enabled = false 的
不列；缺了不藏的环境变量在说明后面写 (setup needed: …)）；blocked() 给 skill 工具说缺什么。每个技能的设置（只认主人的设备、先备份）：
  GET  /api/setup/skills/config/{名字} → {"name", "found", "source", "enabled", "env": [{"name", "declared", "configured", "present", "source",
       "hint", "savedAt"}]（不带值）, "config", "configVars", "gate"}
  PUT  /api/setup/skills/config/{名字} {"enabled"?, "env"?: {"变量": "值" | null}, "config"?: {…} | null}：值只进 secrets.env，
       entries.<名字>.env 只写 ${变量}；claw 不是 builtin → 409
第 5 批第 6 项：技能当斜杠命令用（slash_skill：「/技能名 剩下的话」→ 技能全文 + 剩下的话；内置命令赢，user-invocable: false 的不认）。
"""
from __future__ import annotations

import contextlib
import json
import re
from dataclasses import dataclass
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


SETUP_MARK = " (setup needed: "  # 缺了不藏的环境变量（Hermes 的 setup_needed，engine/skill_gate.py）：说明后面带上，目录、skill 工具列的时候看得到


def skills_for(agent: str, offered: set[str] | None = None) -> dict[str, tuple[str, Path]]:
    """这个 Agent 能用的技能：名字 → (一句说明, SKILL.md)。tools.list_skills（工作区的、用户级的和 core 的，core 里要某个工具、这个 Agent
    没有那个工具的不列）再按名单筛，再过门槛（第 5 批，engine/skill_gate.py：要的程序、环境变量、配置、系统不满足的，engine.skills.entries
    里关掉的不列；缺了不藏的环境变量在说明后面写 (setup needed: …)）。offered：这一轮它有的工具名（不给 = 按它能有的命令行工具和后台任务算）。"""
    from . import tools as tools_mod
    me = agent or "main"
    found = tools_mod.list_skills(me, _offered(me) if offered is None else offered)
    allowed = names_for(me)
    return _gated(me, found if allowed is None else {n: v for n, v in found.items() if n in allowed}, offered)


def _gated(me: str, skills: dict[str, tuple[str, Path]], offered: set[str] | None) -> dict[str, tuple[str, Path]]:
    from . import skill_gate
    out: dict[str, tuple[str, Path]] = {}
    for n, (d, p) in skills.items():
        st = skill_gate.status_of(n, p, agent=me, offered=offered)
        if not st.ok:
            continue
        out[n] = (d + SETUP_MARK + ", ".join(s["name"] for s in st.setup) + ")" if st.setup else d, p)
    return out


def withheld(agent: str, name: str, offered: set[str] | None = None) -> bool:
    """有这个技能、只是这个 Agent 用不了：名单上没有它，或者没过门槛、关掉了（skill 工具据此回「这个 Agent 没有这个技能」，和「根本没有」
    分开说；为什么用不了见 blocked）。"""
    from . import tools as tools_mod
    me = agent or "main"
    found = tools_mod.list_skills(me, _offered(me) if offered is None else offered)
    if name not in found:
        return False
    allowed = names_for(me)
    return (allowed is not None and name not in allowed) or name not in _gated(me, {name: found[name]}, offered)


def blocked(agent: str, name: str, offered: set[str] | None = None) -> list[str]:
    """这个技能在名单上（或者没写名单）、只是没过门槛或者关掉了 → 缺什么（「要 ffmpeg」「在设置里关掉了」）；不是这种情况 → []。"""
    from . import skill_gate
    from . import tools as tools_mod
    me = agent or "main"
    hit = tools_mod.list_skills(me, _offered(me) if offered is None else offered).get(name)
    allowed = names_for(me)
    if hit is None or (allowed is not None and name not in allowed):
        return []
    return skill_gate.status_of(name, hit[1], agent=me, offered=offered).labels()


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


def source_of(path: Path) -> str:
    """一个 SKILL.md 在哪：core（packs/core/skills）/ user（数据目录下所有 Agent 共用的 skills/，第 5 批）/ plugin（载上的插件自带的）/ workspace。"""
    import bridges

    from config import settings
    real = path.resolve()
    if real.is_relative_to((bridges.REPO / "packs" / "core" / "skills").resolve()):
        return "core"
    if real.is_relative_to((settings.data_dir / "plugins").resolve()):
        return "plugin"
    return "user" if real.is_relative_to((settings.data_dir / "skills").resolve()) else "workspace"


def view(agent: str) -> dict:
    """GET 的样子（见文件开头）。available 照 tools.list_skills：这个 Agent 在这台电脑上能有的（core 里要别的工具、它没有那个工具的不列），
    不按名单筛；on = 名单上有（没写名单 = 都有）。第 5 批起每一项多 gate（engine/skill_gate.Status.public：eligible、enabled、
    missing [{"kind", "what", "label"}]（没过门槛的原因：「要 ffmpeg」「要 FOO_API_KEY」）、setupNeeded、env、primaryEnv…）。"""
    from . import skill_gate
    from . import tools as tools_mod
    found = tools_mod.list_skills(agent, _offered(agent))
    names = listed(agent)
    items = [{"name": n, "description": d, "source": source_of(p), "on": names is None or n in names,
              "gate": skill_gate.status_of(n, p, agent=agent).public()}
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


# —— 接口：一个技能的开关、环境变量、设置（engine.skills.entries.<名字>，第 5 批，engine/skill_gate.py）——————————————————

CONFIG_KEYS_MAX, CONFIG_BYTES_MAX = 40, 8000


def find(name: str) -> Path | None:
    """按名字找这台电脑上的一个技能（主对话、用户级、core，再看每个 Agent 的工作区）：设置是按技能存的，不分 Agent。"""
    from . import tools as tools_mod
    from config import settings
    every = _offered("main") | {"tasks"}
    for agent in ["main", *sorted(settings.agent_workspaces)]:
        hit = tools_mod.list_skills(agent, every).get(name)
        if hit is not None:
            return hit[1]
    return None


def _env_refs(e: dict) -> dict[str, str]:
    env = e.get("env")
    return {str(k): str(v) for k, v in env.items()} if isinstance(env, dict) else {}


def config_view(name: str) -> dict:
    """GET 的样子：{"name", "found", "source", "enabled", "env": [{"name", "declared", "configured", 密钥的 info（有没有、从哪来、末四位，不带值）}],
    "config", "configVars"（技能自己声明的设置）, "gate"}。"""
    import secretstore

    from . import skill_gate
    path = find(name)
    fm = skill_gate.front(path) if path is not None else {}
    n = skill_gate.needs(fm)
    e = skill_gate.entry(name)
    refs = _env_refs(e)
    declared = n.declared_env()
    env = []
    for var in dict.fromkeys([*declared, *refs]):
        ref = refs.get(var, "")
        m = skill_gate.VAR.fullmatch(ref.strip()) if ref else None
        info = secretstore.info(m.group(1) if m else var) if secretstore.NAME.match(m.group(1) if m else var) else {"present": False}
        env.append({"name": var, "declared": var in declared, "configured": bool(ref), "ref": ref if (m or not ref) else "(value in server.json)",
                    **{k: info.get(k) for k in ("present", "source", "hint", "savedAt")}})
    cfg = e.get("config") if isinstance(e.get("config"), dict) else {}
    return {"name": name, "found": path is not None, "source": source_of(path) if path is not None else None, "enabled": e.get("enabled") is not False,
            "env": env, "config": cfg, "configVars": skill_gate.config_vars(fm),
            "gate": skill_gate.check(name, fm).public() if path is not None else None}


def _still_used(d: dict, var: str, skip: str) -> bool:
    """别的技能的 entries 里还有没有用 ${var} 的（删密钥之前看一眼）。"""
    eng = d.get("engine") if isinstance(d.get("engine"), dict) else {}
    sk = eng.get("skills") if isinstance(eng.get("skills"), dict) else {}
    ents = sk.get("entries") if isinstance(sk.get("entries"), dict) else {}
    return any(f"${{{var}}}" in json.dumps(v) for k, v in ents.items() if k != skip)


@router.get("/api/setup/skills/config/{name}")
def get_skill_config(name: str):
    """一个技能的开关、环境变量（只说有没有、从哪来、末四位）、设置、门槛。"""
    if not NAME.fullmatch(name):
        raise SetupError(400, L("技能名只能是字母、数字、. _ -", "A skill name is letters, digits, . _ -"), field="name")
    return {"ok": True, **config_view(name)}


@router.put("/api/setup/skills/config/{name}")
async def put_skill_config(name: str, request: Request):
    """{"enabled"?: true | false, "env"?: {"FOO_API_KEY": "值" | null}, "config"?: {…} | null}：只改给了的。env 的值只进 secrets.env，
    server.json 的 entries.<名字>.env 只写 ${变量名}；null = 去掉这一项（别的技能没在用的，secrets.env 里那一行也删）。先备份 server.json。"""
    import secretstore

    from . import skill_gate
    b = await setup_api.body_of(request)
    if not claw.is_builtin():
        raise SetupError(409, L("技能的设置只管自带的内核：OpenClaw 的在 openclaw.json 的 skills.entries", "Skill settings only apply to the built-in "
                                "engine; OpenClaw's live in skills.entries in openclaw.json"))
    if not NAME.fullmatch(name):
        raise SetupError(400, L("技能名只能是字母、数字、. _ -", "A skill name is letters, digits, . _ -"), field="name")
    if find(name) is None and name not in skill_gate.entries():
        raise SetupError(404, L("这台电脑上没有这个技能", "There's no such skill on this computer"))
    on = b.get("enabled")
    if "enabled" in b and on is not None and not isinstance(on, bool):
        raise SetupError(400, L("enabled 要是 true 或 false", "enabled must be true or false"), field="enabled")
    env = b.get("env")
    if "env" in b and not isinstance(env, dict):
        raise SetupError(400, L("env 要写成 {\"变量名\": \"值\" 或 null}", "env must be an object of name → value or null"), field="env")
    keep, drop = {}, []
    for var, val in (env or {}).items():
        try:
            var = secretstore.check_name(var)
            if val is None:
                drop.append(var)
            elif not isinstance(val, str):
                raise secretstore.SecretError(var)
            else:
                keep[var] = secretstore.check_value(var, val)
        except secretstore.SecretError:
            raise SetupError(400, L(f"env 里的 {str(var)[:64]} 不像样：变量名是大写字母、数字、_，值不能有空格、引号、中文",
                                    f"env {str(var)[:64]}: names are A-Z, 0-9 and _; values can't have spaces, quotes or non-ASCII"),
                             field="env") from None
        if not skill_gate.allowed_env(var):
            raise SetupError(400, L(f"{var} 不能给技能设（会改掉程序的行为）", f"{var} can't be set for a skill (it changes how programs behave)"),
                             field="env")
    cfg = b.get("config")
    if "config" in b and cfg is not None:
        if (not isinstance(cfg, dict) or len(cfg) > CONFIG_KEYS_MAX or len(json.dumps(cfg, ensure_ascii=False)) > CONFIG_BYTES_MAX
                or not all(isinstance(k, str) and k.strip() for k in cfg)):
            raise SetupError(400, L(f"config 要是一个不太大的对象（最多 {CONFIG_KEYS_MAX} 项）", f"config must be a small object (at most "
                                    f"{CONFIG_KEYS_MAX} keys)"), field="config")
        if bad := [k for k in cfg if skill_gate.SECRETISH.search(k)]:
            raise SetupError(400, L(f"config 只放不是密钥的设置；{'、'.join(bad)} 看着像密钥，放进 env", f"config is for non-secret settings; "
                                    f"{', '.join(bad)} look like secrets, put them in env"), field="config")
    for var, val in keep.items():  # 值先进 secrets.env（server.json 只写引用）
        secretstore.put(var, val)
    gone: list[str] = []

    def go(d: dict) -> None:
        gone.clear()
        ent = setup_api.section(d, "engine", "skills", "entries", name)
        if "enabled" in b:
            if on is False:
                ent["enabled"] = False
            else:
                ent.pop("enabled", None)
        if keep or drop:
            refs = ent.get("env") if isinstance(ent.get("env"), dict) else {}
            for var in keep:
                refs[var] = f"${{{var}}}"
            for var in drop:
                if refs.pop(var, None) is not None and not _still_used(d, var, name):
                    gone.append(var)
            if refs:
                ent["env"] = refs
            else:
                ent.pop("env", None)
        if "config" in b:
            if cfg:
                ent["config"] = cfg
            else:
                ent.pop("config", None)
        setup_api.prune(d, "engine", "skills", "entries", name)
    res = setup_api.edit(go)
    for var in gone:
        try:
            secretstore.delete(var)
        except secretstore.SecretError:
            pass
    if res["changed"] or keep:
        import chat
        chat.log_activity(L(f"改了技能「{name}」的设置", f'Changed the settings of the "{name}" skill'), "edit")
    return {"ok": True, **config_view(name), "backup": res["backup"], "changed": res["changed"] or bool(keep)}


# —— 技能当斜杠命令用（第 5 批第 6 项）——————————————————————————————————————————————————————————————————
# 「/技能名 剩下的话」→ 这个技能的全文 + 剩下的话，交给模型（照 Hermes 的 agent/skill_commands.py：/<skill> 把全文装进这一句；OpenClaw 也认
# /skill <名字> 这种写法）。第 2 批的命令框架（engine/commands.py）还没合进来：这里只是一个独立的函数，谁在交给模型之前调它见 slash_skill。
# 那一句的开头、结尾两段原话（INVOKED、INSTRUCTION）和 slug 改写自 Hermes 的 agent/skill_commands.py（_build_skill_message、slugify_skill_name），
# NousResearch/hermes-agent commit cc6b1f29，MIT，Copyright (c) 2025 Nous Research；许可全文见仓库根目录的 THIRD_PARTY_NOTICES。

RESERVED = frozenset({"new", "reset", "stop", "help", "status", "model", "compact"})  # 内置命令常用的名字：命令框架接上之前也不让技能占
SLASH = re.compile(r"^\s*/([^\s/]+)(?:\s+([\s\S]*))?$")  # /名字 剩下的话；/usr/bin/x 这种路径不算
INVOKED = ('[IMPORTANT: The user has invoked the "{name}" skill, indicating they want you to follow its instructions. '
           "The full skill content is loaded below.]")  # Hermes 的原话（它的记忆、预览按这几个字认出这种消息）
INSTRUCTION = "The user has provided the following instruction alongside the skill invocation: "
FILES_SHOWN = 20


def slug(name: str) -> str:
    """技能名、命令名 → 比较用的样子（照 Hermes 的 slugify_skill_name：小写，空格和 _ 当 -，别的符号去掉）：/web_search 认 web-search。"""
    s = re.sub(r"[^\w-]", "", str(name).lower().replace(" ", "-").replace("_", "-"))
    return re.sub(r"-{2,}", "-", s).strip("-")


def invocable(path: Path) -> bool:
    """frontmatter 写了 user-invocable: false（OpenClaw 的写法，也认 user_invocable）= 不当斜杠命令用（模型照旧能读）。"""
    from . import skill_gate
    fm = skill_gate.front(path)
    v = fm.get("user-invocable", fm.get("user_invocable", True))
    return not (v is False or str(v).strip().lower() in ("false", "no", "off", "0"))


@dataclass
class SlashSkill:
    name: str        # 技能名
    path: Path       # SKILL.md
    rest: str        # 「/名字」后面剩下的话（没有 = ""）
    source: str      # workspace / user / core
    message: str     # 交给模型的那一句：技能全文（带设置、缺的环境变量）+ 剩下的话

    def note(self, turn: str | None, *, conversation: str, agent: str | None) -> None:
        """这一轮读了这个技能，记一笔（engine/used.py，via slash；和 skill 工具读的一样算）。"""
        from . import used
        used.note(turn, conversation=conversation, agent=agent, kind="skill", names=[self.name], via="slash", source=self.source)


def slash_skill(text: str, agent: str, *, builtin=(), offered: set[str] | None = None) -> SlashSkill | None:
    """这句话是不是「/技能名 剩下的话」（也认「/skill 技能名 剩下的话」）→ SlashSkill；不是 → None（照常当一句话）。
    认的：这个 Agent 能用的技能（skills_for：名单、门槛都过了），frontmatter 没写 user-invocable: false 的。内置命令赢：builtin（命令框架
    报上来的命令名和别名）和 RESERVED 里的一概不认，留给内置命令。名字比较照 slug（大小写、_ 和 - 不分）；两个技能撞了，名字一字不差的先。
    接法（命令框架先认内置命令，没认出来再调这里）：认出来了就把 message 当这一轮进模型的那句话（对话里照旧显示用户打的原话），
    这一轮有了 id 以后 note() 记一笔；手册的逐句搜（engine/manuals.py）用 rest，不用 message。"""
    m = SLASH.match(text or "")
    if not m:
        return None
    taken = {slug(b) for b in (*RESERVED, *builtin)} - {""}
    head, rest = m.group(1), (m.group(2) or "").strip()
    if slug(head) in taken:
        return None
    if slug(head) == "skill":
        m = re.match(r"^(\S+)(?:\s+([\s\S]*))?$", rest)
        if not m:
            return None
        head, rest = m.group(1), (m.group(2) or "").strip()
    want = slug(head)
    skills = skills_for(agent, offered) if want else {}
    hits = sorted(n for n in skills if slug(n) == want)
    if not hits:
        return None
    name = head if head in hits else hits[0]
    path = skills[name][1]
    if not invocable(path):
        return None
    return SlashSkill(name, path, rest, source_of(path), invocation(name, path, rest, agent=agent, offered=offered))


def invocation(name: str, path: Path, rest: str, *, agent: str, offered: set[str] | None = None) -> str:
    """交给模型的那一句（Hermes 的 _build_skill_message 的样子）：开头一行说明用户点了这个技能，SKILL.md 全文（和 skill 工具读的一样截长、
    接上设置和缺的环境变量），同一个文件夹里还有哪些文件（用 skill 工具的 file 读），最后是用户跟着说的话。"""
    import bridges

    from . import skill_gate
    try:
        body = bridges.clip(path.read_text(encoding="utf8", errors="replace"))
    except OSError:
        body = ""
    parts = [INVOKED.format(name=name), "", (body + skill_gate.extra(name, path, agent=agent, offered=offered)).strip()]
    root = path.parent
    files = []
    with contextlib.suppress(OSError):
        files = sorted(p.relative_to(root).as_posix() for p in root.rglob("*")
                       if p.is_file() and p != path and not any(x.startswith(".") for x in p.relative_to(root).parts))
    if files:
        shown = ", ".join(files[:FILES_SHOWN]) + (f" (+{len(files) - FILES_SHOWN} more)" if len(files) > FILES_SHOWN else "")
        parts += ["", f'[Other files in this skill\'s folder (read one with the skill tool: name "{name}", file "<path>"): {shown}]']
    if rest:
        parts += ["", INSTRUCTION + rest]
    return "\n".join(parts)
