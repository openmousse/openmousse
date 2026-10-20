"""mousse migrate：把 OpenClaw 或 Hermes 的设置搬进 OpenMousse 自带的内核（第 9 批 15.8，2026-10-19）。

  mousse migrate openclaw [--openclaw-home 路径] [--out 目录] [--apply [--copy-secrets]]
  mousse migrate hermes   [--hermes-home 路径]   [--out 目录] [--apply [--copy-secrets]]
  mousse migrate on [<定时任务 id> | skill:<名字> …]

默认只打印报告：自动转的、要人做的、转不了的（什么都不写）。
  --out 目录      再写成三个文件：server.patch.json（要合进 server.json 的）、jobs.json（要建的定时任务）、report.md（同一份报告）
  --apply         真写：先备份 server.json（同一目录 server.json.bak-<时刻>）和数据库（<backup_dir>/mousse.db.<时刻>.pre-migrate），再写；
                  只给自带内核（server.json 的 claw 是 builtin）用：别的 claw 下建 Agent、定时任务、换对话的模型都会碰到 OpenClaw 那条路，不做
  --copy-secrets  只在 --apply 时有用：把密钥的值抄进 secrets.env（0600）。不加 = 只记变量名，值留在原来的 .env（server.json 的
                  env_file 指过去）。订阅的登录令牌（Claude 的 setup-token、ChatGPT / Codex 的 OAuth）加了也不抄
  on              打开迁过来的定时任务（迁过来一律先停着）、迁过来的技能（先停着）；不写 id = 列出还停着的
源目录一个字节都不改：只读打开文件，SQLite 先拷到临时目录再用只读连接看；要用的文件拷过来，不在源目录里写任何东西。
写进 server.json 的只加没有的键（common.merge）：已经有了、又不一样的不动，列进「要人做的」。再跑一次 --apply 不会重复建。
OpenClaw 那边转什么见 migrate/openclaw.py 开头，Hermes 的见 migrate/hermes.py 开头；--apply 的顺序见 migrate/apply.py 开头。
退出码：0 照做了（或者只看了）；1 写到一半出错（写了的、备份在哪，报告的「做了的」里有）；2 参数不对、源目录读不了、claw 不是 builtin。
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import config
from i18n import L

from . import apply as apply_mod
from . import report
from .common import Plan, SourceError, tilde


def arguments(p: argparse.ArgumentParser) -> None:
    """mousse.py 的 migrate 子命令的参数（身子在这个包里）。"""
    sp = p.add_subparsers(dest="source", required=True)
    for name, flag, default in (("openclaw", "--openclaw-home", "~/.openclaw"), ("hermes", "--hermes-home", "~/.hermes")):
        s = sp.add_parser(name, help=L(f"从 {report.NAMES[name]} 搬过来", f"Move over from {report.NAMES[name]}"))
        s.add_argument(flag, dest="home", default=None, help=L(f"它的家目录（默认 {default}）", f"Its home directory (default {default})"))
        s.add_argument("--out", help=L("报告和要写的写成文件放进这个目录", "Write the report and the planned changes into this folder"))
        s.add_argument("--apply", action="store_true", help=L("真写（先备份 server.json 和数据库）", "Write it (server.json and the database are backed up first)"))
        s.add_argument("--copy-secrets", action="store_true", help=L("把密钥的值也抄进 secrets.env（只在 --apply 时）",
                                                                     "Also copy key values into secrets.env (with --apply only)"))
    on = sp.add_parser("on", help=L("打开迁过来的定时任务、技能", "Turn on migrated jobs and skills"))
    on.add_argument("ids", nargs="*", help=L("定时任务 id，或 skill:<名字>；不写 = 列出还停着的", "Job ids, or skill:<name>; none = list what's still off"))


def default_home(source: str) -> Path:
    import os
    if source == "hermes":
        return Path(os.environ.get("HERMES_HOME") or "~/.hermes").expanduser()
    return Path(os.environ.get("OPENCLAW_STATE_DIR") or "~/.openclaw").expanduser()


def builtin() -> bool:
    c = config.raw().get("claw")
    return isinstance(c, dict) and c.get("kind") == "builtin"


def make_plan(source: str, home: Path, detect=None) -> Plan:
    import copy

    from . import hermes, openclaw
    from .common import brief, merge
    p = (openclaw if source == "openclaw" else hermes).plan(home, detect)
    cur = copy.deepcopy(config.raw(fresh=True))
    for k in p.drop:
        cur.pop(k, None)
    cur.update(p.replace)
    for path, have, want in merge(cur, p.patch)[1]:  # 已经有、又不一样的：不动，列出来
        p.ask(path, L(f"server.json 里已经有了（{brief(path, have)}），没改；{report.NAMES[source]} 的是 {brief(path, want)}",
                      f"Already set in server.json ({brief(path, have)}) and left alone; {report.NAMES[source]} has {brief(path, want)}"))
    if not builtin():
        p.ask("claw", L("这台的 claw 不是自带内核（builtin）：迁移是给自带内核用的，--apply 不写。先换成自带内核（重新跑安装器选自带内核，或者 server.json 的 "
                        "claw 写 {\"kind\": \"builtin\"}），再 --apply",
                        "This server's claw isn't the built-in engine (builtin): migration is for the built-in engine, and --apply won't write. Switch "
                        "first (re-run the installer and pick the built-in engine, or set claw to {\"kind\": \"builtin\"} in server.json), then --apply"))
    return p


def next_steps(p: Plan, a: argparse.Namespace, applied: bool) -> list[str]:
    cmd = f"mousse migrate {p.source}" + (f" --{p.source}-home {tilde(p.home)}" if a.home else "")
    if not applied:
        out = [L(f"写进去：{cmd} --apply（先备份 server.json 和数据库；源目录不动）", f"To write it: {cmd} --apply (server.json and the database are backed up "
                 "first; the source isn't touched)")]
        if p.secrets or p.held:
            out.append(L("密钥的值也要抄进这台服务器的 secrets.env：再加 --copy-secrets", "To also copy the key values into this server's secrets.env: add --copy-secrets"))
        if not a.out:
            out.append(L("先存成文件看看：加 --out <目录>", "To look at it as files first: add --out <folder>"))
        return out
    out: list[str] = []
    ids = [j["id"] for j in p.jobs if j.get("id")]
    if ids:
        out.append(L(f"定时任务都停着：看过没问题，mousse migrate on {ids[0]}（一个个打开；mousse migrate on 列出全部）",
                     f"The jobs are all paused: once you've checked one, turn it on with mousse migrate on {ids[0]} (mousse migrate on lists them all)"))
    skills = [f["name"] for f in p.files if f["op"] == "skill"]
    if skills:
        out.append(L(f"技能先停着：看过 skills/{skills[0]}/SKILL.md 没问题，mousse migrate on skill:{skills[0]}",
                     f"The skills are off for now: once you've read skills/{skills[0]}/SKILL.md, mousse migrate on skill:{skills[0]}"))
    if "workspace" in p.replace:
        out.append(L("重启 OpenMousse 的服务：换了工作区要重启才用上", "Restart the OpenMousse service: a new workspace takes effect after a restart"))
    return out


def run(a: argparse.Namespace, detect=None) -> int:
    if a.source == "on":
        return turn_on(a.ids)
    home = Path(a.home).expanduser() if a.home else default_home(a.source)
    if not home.is_dir():
        print(L(f"{tilde(home)} 不在：用 --{a.source}-home 指到 {report.NAMES[a.source]} 的家目录",
                f"{tilde(home)} doesn't exist: point --{a.source}-home at {report.NAMES[a.source]}'s home"), file=sys.stderr)
        return 2
    try:
        p = make_plan(a.source, home, detect)
    except SourceError as e:
        print(str(e), file=sys.stderr)
        return 2
    applied = False
    if a.apply:
        if not builtin():
            print(report.text(p, False, []), end="")
            print(L("没写：claw 不是自带内核（见上面「要人做的」）", "Nothing written: the claw isn't the built-in engine (see Needs you above)"), file=sys.stderr)
            return 2
        from engine.setup_api import SetupError
        try:
            apply_mod.run(p, copy_secrets=a.copy_secrets)
        except (SetupError, OSError, sqlite3.Error) as e:
            print(report.text(p, False, []), end="")
            print(L(f"没写完：{getattr(e, 'detail', None) or e}（写了的见上面「做了的」；备份在那里）",
                    f"Not finished: {getattr(e, 'detail', None) or e} (what was written is under Done above, with the backups)"), file=sys.stderr)
            return 1
        applied = True
    steps = next_steps(p, a, applied)
    if a.out:
        files = report.write_out(p, Path(a.out).expanduser(), steps, applied)
        p.done.append(L(f"写了 {', '.join(tilde(f) for f in files)}", f"Wrote {', '.join(tilde(f) for f in files)}"))
    if applied:
        keep = config.settings.data_dir / "migrate" / f"{a.source}-{datetime.now():%Y%m%d-%H%M%S}"
        report.write_out(p, keep, steps, True)
        p.done.append(L(f"这份报告存在 {tilde(keep / 'report.md')}", f"This report is saved at {tilde(keep / 'report.md')}"))
    print(report.text(p, applied, steps), end="")
    return 0


def turn_on(ids: list[str]) -> int:
    """mousse migrate on：打开迁过来的（只认迁移建的定时任务、frontmatter 记着 migrated-from 的技能）。不给 id = 列出还停着的。"""
    from engine import jobs
    ws = Path(str(config.raw().get("workspace") or config.settings.workspace)).expanduser()
    if not ids:
        rows = [j for j in jobs.listing() if str(j.get("created_by") or "").startswith("migrate:") and j["state"] == "paused"] if jobs.exists() else []
        skills = [d.name for d in sorted((ws / "skills").glob("*")) if apply_mod.is_off(d / "SKILL.md")]
        if not rows and not skills:
            print(L("没有停着的、迁过来的定时任务和技能", "No migrated jobs or skills are off"))
            return 0
        for j in rows:
            print(f"{j['id']}  {j['name']}  ({jobs.when.describe(j['schedule'])})")
        for s in skills:
            print(f"skill:{s}")
        return 0
    bad = 0
    for x in ids:
        if x.startswith("skill:"):
            ok = apply_mod.turn_on_skill(ws, x.split(":", 1)[1])
            print(L(f"{x}：开了", f"{x}: on") if ok else L(f"{x}：不是迁过来、停着的技能", f"{x}: not a migrated skill that's off"))
            bad += not ok
            continue
        j = jobs.get(x) if jobs.exists() else None
        if not j or not str(j.get("created_by") or "").startswith("migrate:"):
            print(L(f"{x}：不是迁过来的定时任务", f"{x}: not a migrated job"))
            bad += 1
            continue
        try:
            jobs.resume(x, by_user=True)
            print(L(f"{x}「{j['name']}」：开了（服务开着的话 30 秒内排上）", f'{x} "{j["name"]}": on (picked up within 30 seconds if the service is running)'))
        except jobs.JobError as e:
            print(f"{x}: {e}")
            bad += 1
    return 1 if bad else 0
