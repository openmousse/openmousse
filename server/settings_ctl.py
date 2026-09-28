#!/usr/bin/env python3
"""改 server.json 里的单项设置（服务不用重启：用到它的地方每次按 mtime 重读）。

  python3 settings_ctl.py user-name <称呼>   你的称呼：给模型的说明里用它指你（新手带路时主对话问了就写进来）
  python3 settings_ctl.py user-name          看现在的称呼
  python3 settings_ctl.py user-name ""       清掉，回到默认的「用户」

只改这一项，别的设置原样留着；先写临时文件再换名，写到一半断了也不会留下半个文件。
"""
from __future__ import annotations

import json
import sys

from config import CONFIG_PATH, save
from i18n import L

MAX_NAME = 40  # 一个称呼，不是一段话：它会原样写进给模型的说明里


def usage() -> str:
    """用法说明，按 server.json 的 language。"""
    return L(__doc__, """Change a single setting in server.json (no server restart needed: whatever uses it re-reads the file when it changes).

  python3 settings_ctl.py user-name <name>   what to call you: prompts to the model refer to you by it (the main chat sets it while showing a new user around)
  python3 settings_ctl.py user-name          show the current name
  python3 settings_ctl.py user-name ""       clear it, back to the default "the user"

Only this setting changes and everything else stays as it is; the file is written to a temporary file and renamed, so an interrupted write never leaves half a file.
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
