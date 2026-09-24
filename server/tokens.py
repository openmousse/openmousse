#!/usr/bin/env python3
"""管理 app 的接入令牌（写在 server.json 的 auth.tokens 里，服务不用重启）。

  python3 tokens.py add <名字>       生成一个新令牌并打印（只显示这一次）
  python3 tokens.py list             列出名字（不显示令牌）
  python3 tokens.py remove <名字>
"""
from __future__ import annotations

import secrets
import sys

from config import CONFIG_PATH, raw, save


def main(argv: list[str]) -> int:
    if len(argv) < 1 or argv[0] not in ("add", "list", "remove"):
        print(__doc__)
        return 2
    data = dict(raw(fresh=True))
    auth = dict(data.get("auth") or {})
    tokens = dict(auth.get("tokens") or {})
    if argv[0] == "list":
        for name in tokens:
            print(name)
        if not tokens:
            print("（还没有令牌）")
        return 0
    if len(argv) < 2:
        print("要给个名字，比如：tokens.py add 手机")
        return 2
    name = argv[1]
    if argv[0] == "add":
        tok = secrets.token_urlsafe(24)
        tokens[name] = tok
        auth["tokens"] = tokens
        data["auth"] = auth
        save(data)
        print(f"{name} 的令牌（只显示这一次，填进 app 的连接页）：\n{tok}\n已写入 {CONFIG_PATH}")
        return 0
    if name not in tokens:
        print(f"没有叫 {name} 的令牌")
        return 1
    del tokens[name]
    auth["tokens"] = tokens
    data["auth"] = auth
    save(data)
    print(f"已删除 {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
