"""mousse-tree 命令行。

  mousse-tree init [--name Alice] [--profile PATH] [--tz Europe/London] [--lang zh|en]   建库、生成令牌、读档案；--lang 定说明和输出的语言
  mousse-tree serve                                                     前台跑服务（systemd 用这个）
  mousse-tree urls [--base https://xxx.ts.net]                          各平台接入地址（含令牌）
  mousse-tree install-openclaw                                          给 ~/.openclaw/openclaw.json 加导出目录的检索路径（先备份，后校验）
  mousse-tree install-service                                           装 systemd user 服务并启动
  mousse-tree add --source myclaw --text "..." [--kind ...] [--tags ...]
  mousse-tree recall --q 关键词 | recent [--days 7] | stats | export
  mousse-tree confirm <id> | forget <id>
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from . import config as C
from . import store as S

USAGE_EN = """mousse-tree command line.

  mousse-tree init [--name Alice] [--profile PATH] [--tz Europe/London] [--lang zh|en]   create the db, generate tokens, read the profile; --lang sets the language of instructions and output
  mousse-tree serve                                                     run the service in the foreground (systemd uses this)
  mousse-tree urls [--base https://xxx.ts.net]                          each platform's connection URL (with its token)
  mousse-tree install-openclaw                                          add the export directory to ~/.openclaw/openclaw.json search paths (backup first, validate after)
  mousse-tree install-service                                           install and start the systemd user service
  mousse-tree add --source myclaw --text "..." [--kind ...] [--tags ...]
  mousse-tree recall --q keyword | recent [--days 7] | stats | export
  mousse-tree confirm <id> | forget <id>
"""

SERVICE = """[Unit]
Description={desc}
After=network-online.target

[Service]
ExecStart={exe} serve
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
"""


def cmd_init(a: argparse.Namespace) -> None:
    cfg = C.load()
    # 语言：给了 --lang 就用它；第一次 init（还没有平台令牌，最多有 serve / urls 顺手存下的管理令牌）按环境变量 LANG 猜；
    # 已经 init 过的不动（老配置没有 language 时 load() 已按 zh 算），免得 `init --host …` 之类把语言悄悄换掉。
    if a.lang:
        cfg["language"] = a.lang
    elif not cfg.get("tokens"):
        cfg["language"] = C.env_lang()
    C.ensure_tokens(cfg)
    if a.name:
        cfg["owner_name"] = a.name
    if a.profile:
        cfg["profile_path"] = str(Path(a.profile).expanduser())
    if a.tz:
        cfg["timezone"] = a.tz
    if a.host:
        cfg.setdefault("public_hosts", [])
        if a.host not in cfg["public_hosts"]:
            cfg["public_hosts"].append(a.host)
    C.save(cfg)
    conn = S.connect()
    n = S.sync_profile(conn)
    S.export(conn)
    toks = ', '.join(sorted(cfg['tokens'].values()))
    print(C.L(f"ok · 配置 {C.CONFIG} · 库 {C.DB} · 档案 {cfg['profile_path']}（{n} 条要点）· 令牌 {toks}",
              f"ok · config {C.CONFIG} · db {C.DB} · profile {cfg['profile_path']} ({n} bullet points) · tokens {toks}"))
    if not Path(cfg["profile_path"]).expanduser().exists():
        print(C.L("提示：档案文件不存在。`mousse-tree serve` 后用 `mousse-tree urls` 打印的管理页链接打开「档案」页写一份，或用 --profile 指到你的 USER.md。",
                  "Note: the profile file doesn't exist yet. After `mousse-tree serve`, open the admin link printed by `mousse-tree urls` "
                  "and write one on the Profile tab, or point --profile at your USER.md."))


def cmd_serve(_: argparse.Namespace) -> None:
    from .server import serve

    serve()


def cmd_urls(a: argparse.Namespace) -> None:
    cfg = C.load()
    base = (a.base or (f"https://{cfg['public_hosts'][0]}" if cfg.get("public_hosts") else f"http://127.0.0.1:{cfg['port']}")).rstrip("/")
    toks = sorted(cfg.get("tokens", {}).items(), key=lambda kv: kv[1])
    print(C.L("无认证连接器（Claude.ai / ChatGPT / Gemini）：URL 里带令牌",
              "No-auth connectors (Claude.ai / ChatGPT / Gemini): the token is part of the URL"))
    for tok, name in toks:
        print(f"  {name:12} {base}/t/{tok}/mcp")
    print(C.L("\n必须带认证头的（Notion）：URL 用下面这个，认证选 API key / Bearer token，值填该平台的令牌",
              "\nConnectors that need an auth header (Notion): use this URL, pick API key / Bearer token auth, and paste that platform's token"))
    print(f"  {'URL':12} {base}/m/mcp")
    for tok, name in toks:
        print(f"  {name:12} token: {tok}")
    if C.ensure_ui_token(cfg):
        C.save(cfg)
    print(C.L("\n管理页（链接带管理令牌，只在本机或 tailnet 里打开，别公开）",
              "\nAdmin page (the link carries the admin key: open it only on this machine or inside your tailnet, never publicly)"))
    print(f"  http://127.0.0.1:{cfg['port']}/ui#key={cfg['ui_token']}")


def cmd_install_openclaw(_: argparse.Namespace) -> None:
    oc = Path.home() / ".openclaw/openclaw.json"
    if not oc.exists():
        sys.exit(C.L(f"没找到 {oc}", f"{oc} not found"))
    export_dir = str(Path(C.load()["export_path"]).expanduser().parent)
    cfg = json.loads(oc.read_text(encoding="utf8"))
    paths = cfg.setdefault("memory", {}).setdefault("search", {}).setdefault("extraPaths", [])
    if any(p.get("path") == export_dir for p in paths):
        print(C.L("extraPaths 已有，未改", "extraPaths already has it, nothing changed"))
        return
    backup = Path.home() / f"backups/openclaw.json.pre-mousse-tree-{datetime.now():%Y%m%d-%H%M}"  # 真要改才备份
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(oc, backup)
    paths.append({"path": export_dir})
    oc.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
    print(C.L(f"已加 extraPaths: {export_dir}（备份 {backup}）", f"Added to extraPaths: {export_dir} (backup {backup})"))
    if shutil.which("openclaw"):
        r = subprocess.run(["openclaw", "config", "validate"], capture_output=True, text=True)
        print((r.stdout or r.stderr).strip().splitlines()[-1] if (r.stdout or r.stderr) else "validate: no output")
        print(C.L("重启 gateway 生效：systemctl --user restart openclaw-gateway",
                  "Restart the gateway to apply it: systemctl --user restart openclaw-gateway"))


def cmd_install_service(_: argparse.Namespace) -> None:
    exe = shutil.which("mousse-tree") or f"{sys.executable} -m openmousse_tree.cli"
    unit = Path.home() / ".config/systemd/user/mousse-tree.service"
    unit.parent.mkdir(parents=True, exist_ok=True)
    unit.write_text(SERVICE.format(exe=exe, desc=C.L("mousse-tree 世界树 MCP 服务（loopback）", "mousse-tree memory tree MCP service (loopback)")),
                    encoding="utf8")
    for cmd in (["systemctl", "--user", "daemon-reload"], ["systemctl", "--user", "enable", "--now", "mousse-tree.service"]):
        subprocess.run(cmd, check=False)
    print(C.L(f"已装 {unit}；状态：systemctl --user status mousse-tree", f"Installed {unit}; status: systemctl --user status mousse-tree"))


def cmd_add(a: argparse.Namespace) -> None:
    text = sys.stdin.read() if a.stdin else (a.text or "")
    conn = S.connect()
    status = "pending" if C.load().get("require_confirm") and a.source != "owner" else "active"
    r = S.add(conn, text=text, source=a.source, kind=a.kind, tags=a.tags, observed_at=a.observed, status=status, supersedes=a.supersedes)
    S.export(conn)
    print(json.dumps(r, ensure_ascii=False))


def cmd_recall(a: argparse.Namespace) -> None:
    conn = S.connect()
    S.sync_profile(conn)
    print(S.fmt(S.recall(conn, a.q, a.limit)))


def cmd_recent(a: argparse.Namespace) -> None:
    print(S.fmt(S.recent(S.connect(), a.days)))


def cmd_status(status: str):
    def run(a: argparse.Namespace) -> None:
        conn = S.connect()
        ok = S.set_status(conn, a.id, status, "owner")
        S.export(conn)
        print("ok" if ok else "not found")
    return run


def cmd_export(_: argparse.Namespace) -> None:
    conn = S.connect()
    S.sync_profile(conn)
    print(S.export(conn))


def cmd_stats(_: argparse.Namespace) -> None:
    for r in S.stats(S.connect()):
        print(f"{r['source']:12} {r['status']:10} {r['n']}")


def main() -> None:
    p = argparse.ArgumentParser(prog="mousse-tree", description=C.L(__doc__, USAGE_EN), formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = p.add_subparsers(dest="cmd", required=True)
    q = sp.add_parser("init"); q.add_argument("--name"); q.add_argument("--profile"); q.add_argument("--tz"); q.add_argument("--host")
    q.add_argument("--lang", choices=("zh", "en")); q.set_defaults(fn=cmd_init)
    sp.add_parser("serve").set_defaults(fn=cmd_serve)
    q = sp.add_parser("urls"); q.add_argument("--base"); q.set_defaults(fn=cmd_urls)
    sp.add_parser("install-openclaw").set_defaults(fn=cmd_install_openclaw)
    sp.add_parser("install-service").set_defaults(fn=cmd_install_service)
    q = sp.add_parser("add")
    q.add_argument("--source", default="owner"); q.add_argument("--text"); q.add_argument("--stdin", action="store_true")
    q.add_argument("--kind", default="fact", choices=S.KINDS[:-1]); q.add_argument("--tags", default=""); q.add_argument("--observed"); q.add_argument("--supersedes")
    q.set_defaults(fn=cmd_add)
    q = sp.add_parser("recall"); q.add_argument("--q", required=True); q.add_argument("--limit", type=int, default=8); q.set_defaults(fn=cmd_recall)
    q = sp.add_parser("recent"); q.add_argument("--days", type=int, default=7); q.set_defaults(fn=cmd_recent)
    for name, st in (("confirm", "active"), ("forget", "retracted")):
        q = sp.add_parser(name); q.add_argument("id"); q.set_defaults(fn=cmd_status(st))
    sp.add_parser("export").set_defaults(fn=cmd_export)
    sp.add_parser("stats").set_defaults(fn=cmd_stats)
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
