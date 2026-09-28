#!/usr/bin/env python3
"""从命令行（或让主 Agent 在对话里）建 / 改 / 删 / 列 Agent。走服务的 HTTP 接口，和 app 里「新建 Agent」「编辑 Agent」完全一样。

  python3 agent_ctl.py list
  python3 agent_ctl.py create --name 睡眠 --purpose "每天早上解读昨晚睡眠…" [--icon moon] [--color purple] [--model anthropic/claude-opus-5-5] [--skills a,b] [--board-file 看板.json]
  python3 agent_ctl.py update <id> [--name 睡眠] [--purpose "…"] [--icon moon] [--color purple] [--model anthropic/claude-opus-5-5]
  python3 agent_ctl.py delete <id>

图标：dumbbell 训练 / utensils 饮食 / book 学习 / wallet 财务 / moon 睡眠 / briefcase 求职 / heart 健康 / plane 出行 /
      coffee 咖啡 / music 音乐 / camera 摄影 / code 编程 / cart 购物 / home 家务 / car 开车 / paw 宠物 / leaf 植物 /
      gamepad 游戏 / palette 创作 / globe 语言 / graduation 升学 / lightbulb 点子 / trophy 目标 / pill 用药。
颜色：cyan 青 / gold 金 / green 绿 / purple 紫 / pink 粉 / orange 橙；不给 = 默认色（update 时 --color default 换回默认）。
--board-file：建好后顺手建它的表、换上起步看板（{"tables": [{name, title, fields}], "blocks": [积木…]}，写法见 skills/board）。
update 只改给了的字段：改名字或职责会写进这个 Agent 的 IDENTITY.md（只换 app 管的那一段），改模型会改 openclaw.json 里它的默认模型。
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

from agents import COLORS
from config import settings
from i18n import L, lang


def description() -> str:
    """--help 的说明，按 server.json 的 language。"""
    return L(__doc__, """Create / edit / delete / list Agents from the command line (or let the main Agent do it in chat). Goes through the server's HTTP API, exactly like "Add agent" and "Edit agent" in the app.

  python3 agent_ctl.py list
  python3 agent_ctl.py create --name Sleep --purpose "Every morning, read last night's sleep…" [--icon moon] [--color purple] [--model anthropic/claude-opus-5-5] [--skills a,b]
  python3 agent_ctl.py update <id> [--name Sleep] [--purpose "…"] [--icon moon] [--color purple] [--model anthropic/claude-opus-5-5]
  python3 agent_ctl.py delete <id>

Icons: dumbbell workouts / utensils meals / book study / wallet money / moon sleep / briefcase job search / heart health / plane travel /
       coffee coffee / music music / camera photos / code coding / cart shopping / home household / car driving / paw pets / leaf plants /
       gamepad games / palette creative / globe languages / graduation school / lightbulb ideas / trophy goals / pill medication.
Colors: cyan / gold / green / purple / pink / orange; leave it out for the default color (on update, --color default switches back to it).
update only changes the fields you give: a new name or role is written into the Agent's IDENTITY.md (only the app's own section), a new model becomes its default model in openclaw.json.
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
    c.add_argument("--color", choices=COLORS, default=None, help=L("不给 = 默认色", "leave out for the default color"))
    c.add_argument("--model", default=settings.default_model)
    c.add_argument("--skills", default=None, help=L("逗号分隔；不给用 server.json 的 agent_default_skills", "comma-separated; defaults to agent_default_skills in server.json"))
    c.add_argument("--board-file", default=None, help=L('建好后建表、换上起步看板：{"tables": [...], "blocks": [...]}', 'after creating: its tables and a starter board, {"tables": [...], "blocks": [...]}'))
    u = sub.add_parser("update")
    u.add_argument("id")
    u.add_argument("--name")
    u.add_argument("--purpose")
    u.add_argument("--icon")
    u.add_argument("--color", choices=[*COLORS, "default"], help=L("default = 换回默认色", "default = back to the default color"))
    u.add_argument("--model")
    d = sub.add_parser("delete")
    d.add_argument("id")
    a = ap.parse_args()
    if a.cmd == "list":
        for g in call("GET", "/api/groups")["groups"]:
            print(f"{g['id']}\t{g['name']}\t{g.get('purpose') or ''}")
    elif a.cmd == "create":
        body = {"name": a.name, "purpose": a.purpose, "icon": a.icon, "model": a.model}
        if a.color:
            body["color"] = a.color
        if a.skills is not None:
            body["skills"] = [s.strip() for s in a.skills.split(",") if s.strip()]
        plan = None
        if a.board_file:  # 先读好、校验个大概再建 Agent，免得建了一半才发现文件坏了
            try:
                plan = json.loads(sys.stdin.read() if a.board_file == "-" else open(a.board_file, encoding="utf8").read())
            except (OSError, ValueError) as e:
                sys.exit(L(f"--board-file 读不了：{e}", f"Can't read --board-file: {e}"))
            if not isinstance(plan, dict) or not isinstance(plan.get("blocks", []), list) or not isinstance(plan.get("tables", []), list):
                sys.exit(L('--board-file 写成 {"tables": [...], "blocks": [...]}', '--board-file must be {"tables": [...], "blocks": [...]}'))
        r = call("POST", "/api/groups", body)
        out = {"ok": True, "id": r["id"], "name": a.name}
        if plan:
            gid = urllib.parse.quote(r["id"], safe="")
            for tb in plan.get("tables", []):
                call("POST", f"/api/collections/{gid}", {"name": tb.get("name"), "title": tb.get("title") or tb.get("name"), "fields": tb.get("fields") or []})
            if plan.get("blocks"):
                v = call("PUT", f"/api/boards/{gid}", {"blocks": plan["blocks"], "mode": "apply", "note": plan.get("note") or L("按方案建好的看板", "The board from the plan")})
                out["board"] = v.get("version")
        print(json.dumps(out, ensure_ascii=False))
    elif a.cmd == "update":
        body = {k: getattr(a, k) for k in ("name", "purpose", "icon", "model") if getattr(a, k) is not None}
        if a.color is not None:
            body["color"] = None if a.color == "default" else a.color
        if not body:
            sys.exit(L("没说要改什么：至少给一个 --name / --purpose / --icon / --color / --model",
                       "Nothing to change: give at least one of --name / --purpose / --icon / --color / --model"))
        r = call("PATCH", f"/api/groups/{a.id}", body)
        print(json.dumps(r, ensure_ascii=False))
    else:
        r = call("DELETE", f"/api/groups/{a.id}")
        print(json.dumps(r, ensure_ascii=False))


if __name__ == "__main__":
    main()
