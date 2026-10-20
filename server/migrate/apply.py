"""mousse migrate … --apply：照算好的 Plan 写进 OpenMousse。见 migrate/__init__.py 开头的规矩。

顺序：先备份 server.json（engine/setup_api.backup，同一目录 server.json.bak-<时刻>，0600）和数据库（<backup_dir>/mousse.db.<时刻>.pre-migrate，
SQLite 的备份接口拷一份，0600），两样都备好了才动手；再合进 server.json（只加没有的，common.merge）、抄密钥（只在 --copy-secrets）、
建 Agent（groups 表一行；Hermes 的 profile 照新建 Agent 那条路建工作区）、建定时任务（jobs.clean 查过、jobs._insert 存成 paused、
没同意过：只有你自己在 app 里或者 mousse migrate on 打开）、换对话的模型（threads.model）、写工作区的文件（改之前留版本）。
源目录只读：这里读源里的文件，拷进来的是拷贝。再跑一次不会重复：已经有的 Agent、一模一样的定时任务、已经有的记忆条目、技能都跳过。
"""
from __future__ import annotations

import copy
import os
import re
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

import config
from i18n import L

from .common import Plan, merge, read_env, read_text, tilde

MIGRATED = "migrated-from"            # 迁过来的技能 frontmatter 里记一笔（mousse migrate on skill:<名字> 只认这种）
OFF = "disable-model-invocation"      # 先停着：tools.list_skills 不列（engine/improve.py 停用技能也是这么写的）


def backup_db() -> Path | None:
    """数据库整个拷一份（SQLite 的备份接口：服务器开着也拷得到一致的一份）。没有库 → None。"""
    src = config.settings.db
    if not src.is_file():
        return None
    folder = config.settings.backup_dir
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dst, n = folder / f"{src.name}.{stamp}.pre-migrate", 1
    while dst.exists():
        dst, n = folder / f"{src.name}.{stamp}-{n}.pre-migrate", n + 1
    fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    s = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=30)
    d = sqlite3.connect(dst)
    try:
        with d:
            s.backup(d)
    finally:
        d.close()
        s.close()
    os.chmod(dst, 0o600)
    return dst


def run(plan: Plan, *, copy_secrets: bool) -> None:
    """写。哪一步出错就停在那一步（前面写了的留着，备份在 plan.done 里写着）：抛出去，调用方说一句。"""
    from engine import setup_api
    cur = setup_api.read_config()               # server.json 坏了 → SetupError：一个字都不写
    name = setup_api.backup()
    plan.done.append(L(f"server.json 备份：{name}", f"server.json backup: {name}") if name else L("还没有 server.json：新写一份", "No server.json yet: writing a new one"))
    db = backup_db()
    if db:
        plan.done.append(L(f"数据库备份：{tilde(db)}", f"Database backup: {tilde(db)}"))
    new = copy.deepcopy(cur)
    for k in plan.drop:
        new.pop(k, None)
    new.update(copy.deepcopy(plan.replace))
    added, _kept = merge(new, plan.patch)
    if new != cur:
        config.save(new)
        plan.done.append(L(f"server.json 加了 {len(added)} 项", f"server.json: {len(added)} setting(s) added") +
                         (L(f"，换了 {', '.join(plan.replace)}", f"; replaced {', '.join(plan.replace)}") if plan.replace else ""))
    if copy_secrets:
        secrets(plan)
    made = make_agents(plan)
    files(plan)
    make_jobs(plan, made)
    threads(plan)


# —— 密钥（只在 --copy-secrets）——————————————————————————————————————————————————

def secrets(plan: Plan) -> None:
    """把 .env 里的值、直接写在配置里的值抄进 secrets.env（0600，secretstore.put）。secrets.env 里已经有的不盖。报告只写名字。"""
    import secretstore
    copied: list[str] = []
    skipped: list[str] = []
    envs: dict[Path, dict[str, str]] = {}
    for name, src in sorted(plan.secrets.items()):
        value = envs.setdefault(src, read_env(src)).get(name) or plan.held.get(name, "")
        _put(secretstore, name, value, copied, skipped)
    for name, value in sorted(plan.held.items()):
        if name not in plan.secrets:
            _put(secretstore, name, value, copied, skipped)
    if copied:
        plan.done.append(L(f"抄进 secrets.env（0600）：{', '.join(copied)}", f"Copied into secrets.env (0600): {', '.join(copied)}"))
    if skipped:
        plan.done.append(L(f"没抄（secrets.env 里已经有、或者值不像样）：{', '.join(skipped)}",
                           f"Not copied (already in secrets.env, or not a usable value): {', '.join(skipped)}"))


def _put(secretstore, name: str, value: str, copied: list[str], skipped: list[str]) -> None:
    if not value or secretstore.get(name):
        skipped.append(name)
        return
    try:
        secretstore.put(name, value)
        copied.append(name)
    except secretstore.SecretError:
        skipped.append(name)


# —— Agent ————————————————————————————————————————————————————————————————————

def make_agents(plan: Plan) -> set[str]:
    """groups 表里建这几个 Agent（app 的列表照它）。OpenClaw 的：工作区已经由 agent_workspaces 指过去，只补表里一行；
    Hermes 的 profile：照新建 Agent 那条路建一个新工作区（agents.provision_builtin：AGENTS.md、IDENTITY.md、MEMORY.md、memory/，
    skills/ 链到主工作区的），再补表里一行。→ 现在有的 Agent id（已经有的也算）。"""
    import agents as agents_mod
    import chat
    import data
    have: set[str] = set()
    for a in plan.agents:
        aid = a["id"]
        with chat._lock, data.ddb() as conn:
            row = conn.execute("SELECT 1 FROM groups WHERE id=?", (aid,)).fetchone()
            taken = {r["name"].casefold() for r in conn.execute("SELECT name FROM groups")}
        if row:
            have.add(aid)
            continue
        if a.get("new"):
            try:
                ws = agents_mod.provision_builtin(aid, a["name"], a.get("purpose") or "", "moon", skills=None)
            except agents_mod.ProvisionError as e:
                plan.ask(a["from"], L(f"Agent「{a['name']}」没建成：{e}", f'Agent "{a["name"]}" wasn\'t created: {e}'))
                continue
            a["workspace"] = ws
        name, n = a["name"], 2
        while name.casefold() in taken:
            name, n = f"{a['name']} {n}", n + 1
        with chat._lock, data.ddb() as conn:
            conn.execute("INSERT INTO groups(id, name, icon, color, purpose, created_at) VALUES(?,?,?,?,?,?)",
                         (aid, name, "moon", None, a.get("purpose") or "", chat.now_iso()))
            conn.execute("INSERT INTO threads(id, model, updated_at) VALUES(?,?,?) ON CONFLICT(id) DO NOTHING", (aid, a.get("model"), chat.now_iso()))
        have.add(aid)
        plan.done.append(L(f"建了 Agent「{name}」（{aid}，工作区 {tilde(a['workspace'])}）", f'Created Agent "{name}" ({aid}, workspace {tilde(a["workspace"])})'))
    return have


# —— 定时任务 ——————————————————————————————————————————————————————————————————

def make_jobs(plan: Plan, made: set[str]) -> None:
    """一条条查（jobs.clean，和 app 里建的同一套规矩；用户自己的迁移 = 他自己定的，by_user）再存成 paused、没同意过（approved 0）：
    到点不跑，Agent 也开不了；你在 app 里（或 mousse migrate on <id>）打开才跑。一模一样的已经有了就跳过（再跑一次 --apply 不重复）。"""
    from engine import jobs, store, when
    at = when.now()
    for j in plan.jobs:
        agent = j["agent"]
        if agent != "main" and agent not in made:
            plan.ask(j["from"], L(f"定时任务「{j['given'].get('name')}」没建：它的 Agent {agent} 没建成", f'Job "{j["given"].get("name")}" wasn\'t created: '
                                  f"its Agent {agent} wasn't created"))
            continue
        try:
            spec = jobs.clean(agent, j["given"], at=at, by_user=True)
        except jobs.JobError as e:
            plan.ask(j["from"], L(f"定时任务「{j['given'].get('name')}」没建：{e}", f'Job "{j["given"].get("name")}" wasn\'t created: {e}'))
            continue
        jobs.ready()
        with store.read() as conn:
            dup = conn.execute("SELECT id FROM engine_jobs WHERE agent=? AND digest=? AND state IN ('draft','on','paused')",
                               (agent, jobs.digest(spec))).fetchone()
        if dup:
            j["id"] = dup["id"]
            continue
        try:
            row = jobs._insert(agent, spec, state="paused", approved=False, by=f"migrate:{plan.source}", next_run=None)  # noqa: SLF001
        except jobs.JobError as e:
            plan.ask(j["from"], L(f"定时任务「{spec['name']}」没建：{e}", f'Job "{spec["name"]}" wasn\'t created: {e}'))
            continue
        j["id"] = row["id"]
    ids = [j["id"] for j in plan.jobs if j.get("id")]
    if ids:
        plan.done.append(L(f"建了 {len(ids)} 个定时任务，都停着：{', '.join(ids)}", f"Created {len(ids)} scheduled job(s), all paused: {', '.join(ids)}"))


# —— 对话的模型 ————————————————————————————————————————————————————————————————

def threads(plan: Plan) -> None:
    """threads.model：订阅优先、这台机器的 Claude Code 登录着时，anthropic/… 换成 claude-code/…（只在 --apply，数据库先备份过了）。"""
    t = plan.threads
    if not t:
        return
    import chat
    import data
    with chat._lock, data.ddb() as conn:
        n = conn.execute("UPDATE threads SET model = ? || substr(model, ?) WHERE substr(model, 1, ?) = ?",
                         (t["to"], len(t["from"]) + 1, len(t["from"]), t["from"])).rowcount
    if n:
        plan.done.append(L(f"{n} 个对话的模型 {t['from']}… 换成 {t['to']}…", f"{n} chat(s) switched from {t['from']}… to {t['to']}…"))


# —— 工作区的文件（Hermes）——————————————————————————————————————————————————————————

def workspace_of(agent: str) -> Path | None:
    if agent == "main":
        return config.settings.workspace
    return config.settings.agent_workspaces.get(agent)


def files(plan: Plan) -> None:
    """plan.files 一样一样写：
    {"op": "soul", "agent", "src"}            SOUL.md：没有才拷；新建的 Agent（工作区里那份是从主工作区抄来的）换成它自己的
    {"op": "entries", "agent", "rel", "entries", "label"}   MEMORY.md / USER.md：没有的条目加在末尾的一节里，已经有的不动
    {"op": "skill", "name", "src", "files"}   技能：拷进主工作区 skills/<名字>/，先停着（frontmatter 写 disable-model-invocation: true）
    改已有的文件之前留一版（engine/versions.keep，主机上的文件工具也是这么留的）。"""
    from engine import versions
    for f in plan.files:
        ws = workspace_of(f.get("agent", "main"))
        if ws is None or not ws.is_dir():
            plan.ask(f["from"], L(f"没写：{f.get('agent')} 的工作区不在", f"Not written: {f.get('agent')}'s workspace isn't there"))
            continue
        try:
            if f["op"] == "soul":
                _soul(plan, f, ws, versions)
            elif f["op"] == "entries":
                _entries(plan, f, ws, versions)
            elif f["op"] == "skill":
                _skill(plan, f, ws)
        except (OSError, versions.TooBig) as e:
            plan.ask(f["from"], L(f"没写成：{e}", f"Couldn't write it: {e}"))


def _soul(plan: Plan, f: dict, ws: Path, versions) -> None:
    dst = ws / "SOUL.md"
    text = read_text(Path(f["src"]))
    if text is None or not text.strip():
        return
    if dst.exists():
        mine = read_text(dst) or ""
        if mine.strip() == text.strip():
            return
        if not f.get("new"):
            plan.ask(f["from"], L(f"{tilde(dst)} 已经有了，没盖掉：Hermes 的在 {tilde(f['src'])}，要换就自己拷过去",
                                  f"{tilde(dst)} already exists and wasn't replaced: Hermes's is at {tilde(f['src'])}; copy it over if you want it"))
            return
        versions.keep(ws, "SOUL.md", by="migrate", note=f"migrate {plan.source}")
    dst.write_text(text if text.endswith("\n") else text + "\n", encoding="utf8")
    plan.done.append(L(f"写了 {tilde(dst)}", f"Wrote {tilde(dst)}"))


def _norm(s: str) -> str:
    return " ".join(s.split()).casefold()


def _entries(plan: Plan, f: dict, ws: Path, versions) -> None:
    dst = ws / f["rel"]
    mine = read_text(dst) or ""
    have = _norm(mine)
    new = [e for e in f["entries"] if _norm(e) and _norm(e) not in have]
    if not new:
        return
    if dst.exists():
        versions.keep(ws, f["rel"], by="migrate", note=f"migrate {plan.source}")
    head = f["label"]
    body = "\n".join("- " + e.strip().replace("\n", "\n  ") for e in new)
    text = (mine.rstrip("\n") + "\n\n" if mine.strip() else "") + f"## {head}\n\n{body}\n"
    dst.write_text(text, encoding="utf8")
    plan.done.append(L(f"{tilde(dst)} 加了 {len(new)} 条", f"{tilde(dst)}: {len(new)} entr{'y' if len(new) == 1 else 'ies'} added"))


def _skill(plan: Plan, f: dict, ws: Path) -> None:
    """技能拷进 skills/<名字>/：SKILL.md 的 frontmatter 换上新名字、记上从哪来、先停着；附属文件只拷 references/ templates/ scripts/ assets/
    下的（Hermes 的 ALLOWED_SUBDIRS，engine/skill_write.SUBDIRS）。已经有这个文件夹 → 跳过（再跑一次不重复，也不盖掉你的）。"""
    root = ws / "skills"
    dst = root / f["name"]
    if dst.exists() or dst.is_symlink():
        return
    if root.is_symlink():  # skills/ 整个链到别处（新 Agent 链到主工作区的）：写进它真的那个地方
        root = root.resolve()
        dst = root / f["name"]
    root.mkdir(parents=True, exist_ok=True)
    tmp = root / f".{f['name']}.migrating"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir()
    try:
        (tmp / "SKILL.md").write_text(f["text"], encoding="utf8")
        for rel in f["files"]:
            src = Path(f["src"]) / rel
            if src.is_symlink():  # 列计划之后才变成软链的：不跟
                continue
            out = tmp / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, out)
        tmp.rename(dst)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    plan.done.append(L(f"技能 {f['name']} 拷进了 {tilde(dst)}（先停着）", f"Skill {f['name']} copied to {tilde(dst)} (off for now)"))


def frontmatter(text: str, name: str, source: str) -> str | None:
    """SKILL.md 的 frontmatter：name 换成 name、记上 migrated-from、加 disable-model-invocation: true。没有 frontmatter → None。"""
    m = re.match(r"^---\s*\n(.*?)\n---\s*(?:\n|$)", text, re.S)
    if not m:
        return None
    lines = [ln for ln in m.group(1).split("\n") if not re.match(rf"^(name|{MIGRATED}|{OFF})\s*:", ln)]
    head = "\n".join([f"name: {name}", *lines, f"{MIGRATED}: {source}", f"{OFF}: true"])
    return f"---\n{head}\n---\n" + text[m.end():]


def _head(text: str | None) -> re.Match | None:
    return re.match(r"^---\s*\n(.*?)\n---\s*(?:\n|$)", text, re.S) if text else None


def is_off(p: Path) -> bool:
    """这个 SKILL.md 是迁过来、还停着的吗（frontmatter 有 migrated-from、disable-model-invocation: true）。"""
    m = _head(read_text(p))
    return bool(m) and bool(re.search(rf"(?m)^{MIGRATED}\s*:", m.group(1))) and bool(re.search(rf"(?m)^{OFF}\s*:\s*true\s*$", m.group(1)))


def turn_on_skill(ws: Path, name: str) -> bool:
    """mousse migrate on skill:<名字>：迁过来的技能去掉 disable-model-invocation 那一行（先留一版）。不是迁过来的、已经开着 → False。"""
    from engine import versions
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", name):
        return False
    p = ws / "skills" / name / "SKILL.md"
    if not is_off(p):
        return False
    text = read_text(p) or ""
    m = _head(text)
    assert m is not None
    versions.keep(ws, f"skills/{name}/SKILL.md", by="migrate", note="turn on")
    head = "\n".join(ln for ln in m.group(1).split("\n") if not re.match(rf"^{OFF}\s*:", ln))
    p.write_text(f"---\n{head}\n---\n" + text[m.end():], encoding="utf8")
    return True
