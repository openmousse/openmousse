"""迁移的报告：终端里打的、--out 写的三个文件（server.patch.json、jobs.json、report.md）。密钥的值一概不出现：只有变量名。"""
from __future__ import annotations

import json
import os
from pathlib import Path

from i18n import L, lang

from .common import Line, Plan, tilde

NAMES = {"openclaw": "OpenClaw", "hermes": "Hermes"}


def _title(plan: Plan, applied: bool) -> str:
    name = NAMES.get(plan.source, plan.source)
    state = L("已经写好", "written") if applied else L("只看了，什么都没写", "dry run: nothing was written")
    return f"{name} → OpenMousse · {state}"


def _job_line(j: dict) -> str:
    g = j["given"]
    what = g.get("task") or " ".join([str(g["command"].get("tool") or g["command"].get("script") or ""), *g["command"].get("args", [])])
    name = g.get("name") or what[:20]
    head = (f"{j['id']} " if j.get("id") else "") + (f"「{name}」" if lang() == "zh" else f'"{name}"')
    what = " ".join(what.split())
    return f"{head} {g['when']} · {what[:80]}" + (L(f"（{j['was']}）", f" ({j['was']})") if j.get("was") else "")


def sections(plan: Plan, applied: bool) -> list[tuple[str, list[str]]]:
    """(小标题, 行) 一段一段：终端和 report.md 共用。"""
    out: list[tuple[str, list[str]]] = []

    def lines(items: list[Line], mark: str) -> list[str]:
        sep = "：" if lang() == "zh" else ": "
        return [f"{mark} {x.what}{sep}{x.text}" for x in items]

    out.append((L(f"自动转的 · {len(plan.auto)}", f"Converted · {len(plan.auto)}"), lines(plan.auto, "✓")))
    out.append((L(f"要人做的 · {len(plan.todo)}", f"Needs you · {len(plan.todo)}"), lines(plan.todo, "•")))
    out.append((L(f"转不了的 · {len(plan.cant)}", f"Not converted · {len(plan.cant)}"), lines(plan.cant, "×")))
    if plan.jobs:
        hint = L("迁过来一律先停着", "all arrive paused")
        out.append((L(f"定时任务 · {len(plan.jobs)}（{hint}）", f"Scheduled jobs · {len(plan.jobs)} ({hint})"), [f"- {_job_line(j)}" for j in plan.jobs]))
    sec: list[str] = []
    if plan.secrets:
        names = ", ".join(sorted(plan.secrets))
        where = ", ".join(sorted({tilde(p) for p in plan.secrets.values()}))
        sec.append(L(f"只记了变量名（{names}），值留在 {where}。", f"Only the variable names were recorded ({names}); the values stay in {where}."))
    if plan.held:
        n = len(plan.held)
        sec.append(L(f"直接写在配置里的密钥 {n} 把（{', '.join(sorted(plan.held))}）：报告里不写值。",
                     f"{n} key{'' if n == 1 else 's'} written straight into the config ({', '.join(sorted(plan.held))}): "
                     f"{'its value isn' if n == 1 else 'their values aren'}'t shown here."))
    if sec:
        out.append((L("密钥", "Keys"), sec))
    if plan.done:
        out.append((L("做了的", "Done"), [f"- {d}" for d in plan.done]))
    return out


def text(plan: Plan, applied: bool, next_steps: list[str]) -> str:
    rows = [_title(plan, applied), L(f"从 {tilde(plan.home)} 读（只读：源目录一个字节都没改）", f"Read from {tilde(plan.home)} (read only: not a byte changed there)"), ""]
    for head, items in sections(plan, applied):
        if not items:
            continue
        rows.append(head)
        rows += [f"  {x}" for x in items]
        rows.append("")
    if next_steps:
        rows.append(L("接下来", "Next"))
        rows += [f"  {x}" for x in next_steps]
    return "\n".join(rows).rstrip() + "\n"


def markdown(plan: Plan, applied: bool, next_steps: list[str]) -> str:
    rows = [f"# {_title(plan, applied)}", "", L(f"来源：`{tilde(plan.home)}`（只读）", f"Source: `{tilde(plan.home)}` (read only)"), ""]
    for head, items in sections(plan, applied):
        if not items:
            continue
        rows += [f"## {head}", ""]
        rows += [x if x.startswith("- ") else f"- {x}" for x in items]
        rows.append("")
    if next_steps:
        rows += [f"## {L('接下来', 'Next')}", ""]
        rows += [f"- {x}" for x in next_steps]
    return "\n".join(rows).rstrip() + "\n"


def jobs_json(plan: Plan) -> list[dict]:
    """jobs.json：要建的定时任务（jobs.clean 认的写法，外加 from / was；--apply 建出来都是 paused）。"""
    return [{"agent": j["agent"], **j["given"], "state": "paused", "from": j.get("from"), "was": j.get("was")} for j in plan.jobs]


def write_out(plan: Plan, folder: Path, next_steps: list[str], applied: bool = False) -> list[Path]:
    """--out：server.patch.json、jobs.json、report.md 写进这个目录（没有就建；0600：报告里有路径、命令、你写的提示词）。"""
    folder.mkdir(parents=True, exist_ok=True)
    files = {"server.patch.json": json.dumps({**plan.patch, **plan.replace}, ensure_ascii=False, indent=2) + "\n",
             "jobs.json": json.dumps(jobs_json(plan), ensure_ascii=False, indent=2) + "\n",
             "report.md": markdown(plan, applied, next_steps)}
    out = []
    for name, body in files.items():
        p = folder / name
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf8") as f:
            f.write(body)
        out.append(p)
    return out
