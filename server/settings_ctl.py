#!/usr/bin/env python3
"""改 server.json 里的单项设置（服务不用重启：用到它的地方每次按 mtime 重读）。

  python3 settings_ctl.py user-name <称呼>   你的称呼：给模型的说明里用它指你（新手带路时主对话问了就写进来）
  python3 settings_ctl.py user-name          看现在的称呼
  python3 settings_ctl.py user-name ""       清掉，回到默认的「用户」

只改这一项，别的设置原样留着；先写临时文件再换名，写到一半断了也不会留下半个文件。
称呼同时记进档案 USER.md（settings.profile，也是世界树的主干）：一行「- 称呼：小周 [L] 日期」，格式和 app 里改档案一样；
已有这一行就改它，没有就加在「基本信息」一类的小节（没有就第一个小节）末尾，一个小节都没有就新开一节；清掉称呼就删掉这一行。
改之前的那一行记进 profile-history.md（和 app 一样）。
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from config import CONFIG_PATH, TZ, save, settings
from i18n import L

MAX_NAME = 40  # 一个称呼，不是一段话：它会原样写进给模型的说明里
NAME_LINE = re.compile(r"^- (?:称呼[：:]|Preferred name:)")  # 档案里记称呼的那一行（两种语言都认）
HOME_SECTION = re.compile(r"基本|个人|关于|档案|basic|about|profile|identity", re.I)  # 称呼优先放进的小节


def usage() -> str:
    """用法说明，按 server.json 的 language。"""
    return L(__doc__, """Change a single setting in server.json (no server restart needed: whatever uses it re-reads the file when it changes).

  python3 settings_ctl.py user-name <name>   what to call you: prompts to the model refer to you by it (the main chat sets it while showing a new user around)
  python3 settings_ctl.py user-name          show the current name
  python3 settings_ctl.py user-name ""       clear it, back to the default "the user"

Only this setting changes and everything else stays as it is; the file is written to a temporary file and renamed, so an interrupted write never leaves half a file.
The name also goes into the profile USER.md (settings.profile, which is also the memory tree's trunk) as one line,
"- Preferred name: Alex [L] date", the same format the app uses when you edit the profile: that line is updated if it's
already there, otherwise added at the end of a "Basics"-like section (or the first section), or in a new section if the file has none;
clearing the name deletes the line. The line it replaces goes into profile-history.md, as in the app.
""")


def load() -> dict | None:
    """server.json 现在的内容（没有这个文件 = 空）。文件坏了返回 None：不能拿空的写回去，把别的设置都冲掉。"""
    if not CONFIG_PATH.exists():
        return {}
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def section_at(lines: list[str], i: int) -> str:
    return next((ln[3:].strip() for ln in reversed(lines[:i]) if ln.startswith("## ")), "")


def write_profile(name: str) -> str | None:
    """称呼写进档案（见文件开头）。→ 给输出用的一句话；None = 档案不用改。"""
    path = settings.profile
    try:
        lines = path.read_text(encoding="utf8").splitlines()
    except FileNotFoundError:
        lines = []
    today = datetime.now(TZ).date().isoformat()
    new = f"- {L('称呼：', 'Preferred name: ')}{name} [L] {today}" if name else None
    at = next((i for i, ln in enumerate(lines) if NAME_LINE.match(ln)), None)
    if at is not None:
        end = at  # 要点下面缩进的子行算同一条（和 app 读档案一样）
        while end + 1 < len(lines) and lines[end + 1].startswith(("  ", "\t")) and lines[end + 1].strip():
            end += 1
        old = "\n".join(lines[at:end + 1])
        if new and end == at and NAME_LINE.sub("", lines[at]).split(" [L]")[0].strip() == name:
            return None  # 已经是这个称呼
        section = section_at(lines, at)
        lines[at:end + 1] = [new] if new else []
        settings.profile_history.parent.mkdir(parents=True, exist_ok=True)
        verb = L("改写", "Edited") if new else L("删除", "Deleted")
        with settings.profile_history.open("a", encoding="utf8") as f:  # 旧的一版留底（和 app 改档案一样，不在检索路径里）
            f.write(f"\n## {datetime.now(TZ).isoformat(timespec='seconds')} · {verb} · {section}\n{old}\n")
    elif new:
        heads = [i for i, ln in enumerate(lines) if ln.startswith("## ")]
        if heads:
            first = next((i for i in heads if HOME_SECTION.search(lines[i])), heads[0])
            at = next((i for i in heads if i > first), len(lines))
            while at > first + 1 and not lines[at - 1].strip():  # 加在这一节最后一行字后面
                at -= 1
            in_list = lines[at - 1].startswith(("- ", "  ", "\t"))
            lines[at:at] = [new] if in_list else ["", new]
        else:
            lines = (lines or ["# USER.md"]) + ["", f"## {L('基本信息', 'Basics')}", "", new]
    else:
        return None  # 要清掉，档案里本来就没有
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf8")
    if path.exists():
        os.chmod(tmp, path.stat().st_mode & 0o777)
    tmp.replace(path)
    where = str(path).replace(str(Path.home()), "~")
    if not new:
        return L(f"档案 {where} 里「称呼」那一行删了", f'Removed the "Preferred name" line from the profile {where}')
    return L(f"档案 {where} 记了一行「称呼：{name}」", f'Saved "Preferred name: {name}" in the profile {where}')


def user_name(args: list[str]) -> int:
    data = load()
    if data is None:
        print(L(f"{CONFIG_PATH} 读不了（不是合法的 JSON？），没改。先修好这个文件再来。",
                f"Can't read {CONFIG_PATH} (not valid JSON?), nothing changed. Fix that file first."))
        return 1
    if not args:
        cur = str(data.get("user_name") or "").strip()
        print(cur or L("（还没设，给模型的说明里叫你「用户」）", '(not set yet; prompts to the model call you "the user")'))
        return 0
    name = " ".join(" ".join(args).split())  # 换行、连续空白并成一个空格
    if len(name) > MAX_NAME:
        print(L(f"太长了：称呼最多 {MAX_NAME} 个字", f"Too long: a name is at most {MAX_NAME} characters"))
        return 2
    data["user_name"] = name
    save(data)
    try:
        note = write_profile(name)
    except OSError as exc:  # 档案写不了不影响 server.json 那边：称呼已经生效
        note = L(f"档案没写进去：{exc}", f"Couldn't write the profile: {exc}")
    if note:
        print(note)
    if name:
        print(L(f"好了，以后叫你「{name}」（写进了 {CONFIG_PATH}，不用重启）", f'Done: you are now "{name}" (saved to {CONFIG_PATH}, no restart needed)'))
    else:
        print(L(f"清掉了，给模型的说明里又叫你「用户」（{CONFIG_PATH}）", f'Cleared; prompts to the model call you "the user" again ({CONFIG_PATH})'))
    return 0


def main(argv: list[str]) -> int:
    if not argv or argv[0] != "user-name":
        print(usage())
        return 2
    return user_name(argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
