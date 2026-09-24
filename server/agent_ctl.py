#!/usr/bin/env python3
"""从命令行（或让主 Agent 在对话里）建 / 删 / 列 Agent。走服务的 HTTP 接口，和 app 里「新建 Agent」完全一样。

  python3 agent_ctl.py list
  python3 agent_ctl.py create --name 睡眠 --purpose "每天早上解读昨晚睡眠…" [--icon moon] [--model anthropic/claude-opus-5-5] [--skills a,b]
  python3 agent_ctl.py delete <id>

图标：dumbbell 训练 / utensils 饮食 / book 学习 / wallet 财务 / moon 睡眠 / briefcase 求职 / heart 健康 / plane 出行。
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

from config import settings
from i18n import L, lang


def description() -> str:
    """--help 的说明，按 server.json 的 language。"""
    return L(__doc__, """Create / delete / list Agents from the command line (or let the main Agent do it in chat). Goes through the server's HTTP API, exactly like "Add agent" in the app.

  python3 agent_ctl.py list
  python3 agent_ctl.py create --name Sleep --purpose "Every morning, read last night's sleep…" [--icon moon] [--model anthropic/claude-opus-5-5] [--skills a,b]
  python3 agent_ctl.py delete <id>

Icons: dumbbell workouts / utensils meals / book study / wallet money / moon sleep / briefcase job search / heart health / plane travel.
""")


def call(method: str, path: str, body: dict | None = None) -> dict:
    url = f"http://{settings.host}:{settings.port}{path}"
    data = json.dumps(body, ensure_ascii=False).encode("utf8") if body is not None else None
    # 服务回的文字（错误说明、新 Agent 的 AGENTS.md）和这个命令用同一种语言
    headers = {"Content-Type": "application/json", "Accept": "application/json", "Accept-Language": "zh-CN" if lang() == "zh" else "en"}
    tokens = settings.tokens()
    if tokens:  # 本机跑，用第一个令牌；没令牌时靠 Tailscale 白名单 / trust_loopback
        headers["Authorization"] = f"Bearer {next(iter(tokens.values()))}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:  # noqa: S310
            return json.loads(r.read().decode("utf8"))
    except urllib.error.HTTPError as e:
        try:
            j = json.loads(e.read().decode("utf8"))
            msg = j.get("detail") or j.get("error") or str(j)
        except ValueError:
            msg = str(e)
        sys.exit(L(f"失败（HTTP {e.code}）：{msg}", f"Failed (HTTP {e.code}): {msg}"))
    except urllib.error.URLError as e:
        sys.exit(L(f"连不上服务 {url}：{e.reason}", f"Can't reach the server at {url}: {e.reason}"))


def main() -> None:
    ap = argparse.ArgumentParser(description=description(), formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    c = sub.add_parser("create")
    c.add_argument("--name", required=True)
    c.add_argument("--purpose", default="")
    c.add_argument("--icon", default="moon")
    c.add_argument("--model", default=settings.default_model)
    c.add_argument("--skills", default=None, help=L("逗号分隔；不给用 server.json 的 agent_default_skills", "comma-separated; defaults to agent_default_skills in server.json"))
    d = sub.add_parser("delete")
    d.add_argument("id")
    a = ap.parse_args()
    if a.cmd == "list":
        for g in call("GET", "/api/groups")["groups"]:
            print(f"{g['id']}\t{g['name']}\t{g.get('purpose') or ''}")
    elif a.cmd == "create":
        body = {"name": a.name, "purpose": a.purpose, "icon": a.icon, "model": a.model}
        if a.skills is not None:
            body["skills"] = [s.strip() for s in a.skills.split(",") if s.strip()]
        r = call("POST", "/api/groups", body)
        print(json.dumps({"ok": True, "id": r["id"], "name": a.name}, ensure_ascii=False))
    else:
        r = call("DELETE", f"/api/groups/{a.id}")
        print(json.dumps(r, ensure_ascii=False))


if __name__ == "__main__":
    main()
