#!/usr/bin/env python3
"""管理 app 的接入令牌（写在 server.json 的 auth.tokens 里，服务不用重启）。

  python3 tokens.py add <名字>       生成一个新令牌并打印（只显示这一次）
  python3 tokens.py list             列出名字（不显示令牌）
  python3 tokens.py remove <名字>
  python3 tokens.py pair [--name 设备名] [--minutes 10] [--json]   一次性配对码：手机扫码或点链接就连上（见 pairing.py）
"""
from __future__ import annotations

import secrets
import sys

from config import CONFIG_PATH, raw, save
from i18n import L


def usage() -> str:
    """用法说明，按 server.json 的 language。"""
    return L(__doc__, """Manage the app's access tokens (stored in server.json under auth.tokens; no server restart needed).

  python3 tokens.py add <name>       create a new token and print it (shown only this once)
  python3 tokens.py list             list the names (tokens are not shown)
  python3 tokens.py remove <name>
  python3 tokens.py pair [--name DEVICE] [--minutes 10] [--json]   one-time pairing code: the phone scans it or taps the link (see pairing.py)
""")


def pair(argv: list[str]) -> int:
    """tokens.py pair [--name 设备名] [--minutes 10] [--server http://…] [--json]：一次性配对码（见 pairing.py）。"""
    import json
    import pairing

    opts = {"--name": "", "--minutes": "10", "--server": ""}
    as_json, i = False, 0
    while i < len(argv):
        if argv[i] == "--json":
            as_json = True
        elif argv[i] in opts and i + 1 < len(argv):
            opts[argv[i]] = argv[i + 1]
            i += 1
        else:
            print(L(f"不认识的参数 {argv[i]}", f"Unknown option {argv[i]}"))
            return 2
        i += 1
    try:
        minutes = int(opts["--minutes"])
    except ValueError:
        minutes = 10
    code, expires = pairing.new_code(opts["--name"], minutes)
    server = (opts["--server"] or pairing.server_url()).rstrip("/")
    url = pairing.link(server, code)
    if as_json:
        print(json.dumps({"code": code, "link": url, "server": server, "expires": expires}, ensure_ascii=False))
        return 0
    print(pairing.terminal_qr(url))
    print(L(f"手机上用相机扫上面的码，或者点这个链接（装了 OpenMousse app 才打得开）：\n{url}",
            f"Scan the code above with the phone's camera, or tap this link (it opens the OpenMousse app):\n{url}"))
    print(L(f"配对码 {code}（也能在 app 的连接页手动填），{minutes} 分钟内有效，只能用一次。手机要先连上 Tailscale。",
            f"Pairing code {code} (you can also type it on the app's connect screen); valid for {minutes} minutes, once. The phone needs Tailscale first."))
    return 0


def main(argv: list[str]) -> int:
    if argv[:1] == ["pair"]:
        return pair(argv[1:])
    if len(argv) < 1 or argv[0] not in ("add", "list", "remove"):
        print(usage())
        return 2
    data = dict(raw(fresh=True))
    auth = dict(data.get("auth") or {})
    tokens = dict(auth.get("tokens") or {})
    if argv[0] == "list":
        for name in tokens:
            print(name)
        if not tokens:
            print(L("（还没有令牌）", "(no tokens yet)"))
        return 0
    if len(argv) < 2:
        print(L("要给个名字，比如：tokens.py add 手机", "Give it a name, e.g.: tokens.py add phone"))
        return 2
    name = argv[1]
    if argv[0] == "add":
        tok = secrets.token_urlsafe(24)
        tokens[name] = tok
        auth["tokens"] = tokens
        data["auth"] = auth
        save(data)
        print(L(f"{name} 的令牌（只显示这一次，填进 app 的连接页）：\n{tok}\n已写入 {CONFIG_PATH}",
                f"Token for {name} (shown only this once; enter it on the app's connect screen):\n{tok}\nSaved to {CONFIG_PATH}"))
        return 0
    if name not in tokens:
        print(L(f"没有叫 {name} 的令牌", f"No token named {name}"))
        return 1
    del tokens[name]
    auth["tokens"] = tokens
    data["auth"] = auth
    save(data)
    print(L(f"已删除 {name}", f"Deleted {name}"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
