"""mousse-tree 命令行。

  mousse-tree init [--name Alice] [--profile PATH] [--tz Europe/London] [--lang zh|en]   建库、生成令牌、读档案；--lang 定说明和输出的语言
  mousse-tree serve                                                     前台跑服务（systemd 用这个）
  mousse-tree urls [--base https://xxx.ts.net]                          各平台接入地址（含令牌）
  mousse-tree install-openclaw                                          给 ~/.openclaw/openclaw.json 加导出目录的检索路径（先备份，后校验）
  mousse-tree install-service                                           装 systemd user 服务并启动
  mousse-tree add --source myclaw --text "..." [--kind ...] [--tags ...]
  mousse-tree recall --q 关键词 | recent [--days 7] | stats | export
  mousse-tree confirm <id> | forget <id>
  mousse-tree rotate <平台> | revoke <平台>                               给某个平台换令牌 / 删令牌（令牌泄露时用）
  mousse-tree migrate markdown --notes DIR [--profile-note 档案.md]      换成 Markdown 存储：记忆导成一条一篇笔记（可以放进 Obsidian 库）
  mousse-tree migrate sqlite                                            换回 SQLite 存储（笔记写回 tree.db）
  mousse-tree check | rebuild                                           Markdown 存储：格式有问题的笔记 / 从笔记重建索引
"""
from __future__ import annotations

import argparse
import contextlib
import json
import secrets
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
  mousse-tree rotate <platform> | revoke <platform>                    issue a new token for a platform / delete its token (if one leaked)
  mousse-tree migrate markdown --notes DIR [--profile-note Profile.md]  switch to Markdown storage: one note per memory (can live in an Obsidian vault)
  mousse-tree migrate sqlite                                            switch back to SQLite storage (notes are written back to tree.db)
  mousse-tree check | rebuild                                           Markdown storage: notes with format problems / rebuild the index from the notes
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
    if a.storage == "markdown" and not C.markdown(cfg):
        if _sqlite_has_memories():
            sys.exit(C.L("tree.db 里已经有记忆：用 mousse-tree migrate markdown --notes DIR 把它们导成笔记",
                         "tree.db already has memories: use mousse-tree migrate markdown --notes DIR to turn them into notes"))
        _set_markdown(cfg, a.notes, a.profile_note)
    elif a.storage == "sqlite" and C.markdown(cfg):
        sys.exit(C.L("换回 SQLite 用 mousse-tree migrate sqlite", "To switch back to SQLite, use mousse-tree migrate sqlite"))
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


def _sqlite_has_memories() -> bool:
    if not C.DB.exists():
        return False
    import sqlite3
    with contextlib.closing(sqlite3.connect(f"file:{C.DB}?mode=ro", uri=True)) as c:
        try:
            return bool(c.execute("SELECT 1 FROM tree WHERE source != 'profile' LIMIT 1").fetchone())
        except sqlite3.Error:
            return False


def _set_markdown(cfg: dict, notes_dir: str | None, profile_note: str | None) -> None:
    cfg["storage"] = "markdown"
    if notes_dir:
        cfg["notes_dir"] = str(Path(notes_dir).expanduser())
    cfg["archive_dir"] = cfg.get("archive_dir") or C.archive_name(cfg)  # 定下来，以后换语言不会变
    if profile_note is not None:
        cfg["profile_note"] = profile_note
    base = C.notes_dir(cfg)
    if not base.parent.is_dir():
        sys.exit(C.L(f"{base.parent} 不存在", f"{base.parent} does not exist"))
    base.mkdir(mode=0o700, exist_ok=True)


def cmd_migrate(a: argparse.Namespace) -> None:
    cfg = C.load()
    if a.to == "markdown":
        if C.markdown(cfg) and not a.notes:
            sys.exit(C.L("已经是 Markdown 存储", "Already using Markdown storage"))
        if not a.notes and not cfg.get("notes_dir"):
            sys.exit(C.L("要给 --notes 笔记文件夹，比如 --notes ~/vault/世界树", "Pass the notes folder with --notes, e.g. --notes ~/vault/Memory"))
        _set_markdown(cfg, a.notes, a.profile_note)
        base = C.notes_dir(cfg)
        C.save(cfg)
        from . import notes
        if C.DB.exists():
            n, bad = notes.from_sqlite()
        else:
            notes.connect().close()
            n, bad = 0, []
        print(C.L(f"已换成 Markdown 存储：{base}，写了 {n} 篇笔记，核对不一致 {len(bad)} 处。tree.db 原样留着（换回用 mousse-tree migrate sqlite）。",
                  f"Switched to Markdown storage: {base}; wrote {n} notes; {len(bad)} mismatches. tree.db is kept as it was (mousse-tree migrate sqlite switches back)."))
        for b in bad:
            print("  " + b)
    else:
        if not C.markdown(cfg):
            sys.exit(C.L("已经是 SQLite 存储", "Already using SQLite storage"))
        from . import notes
        n = notes.to_sqlite()
        cfg["storage"] = "sqlite"
        C.save(cfg)
        S.sync_profile(S.connect())
        print(C.L(f"已换回 SQLite 存储：{n} 条写回 tree.db。笔记文件夹原样留着。", f"Switched back to SQLite storage: wrote {n} memories to tree.db. The notes folder is left as it is."))
    print(C.L("重启服务生效：systemctl --user restart mousse-tree", "Restart the service to apply: systemctl --user restart mousse-tree"))


def cmd_check(_: argparse.Namespace) -> None:
    if not C.markdown():
        sys.exit(C.L("只有 Markdown 存储才有笔记格式问题", "Only Markdown storage has note format problems"))
    from . import notes
    rows = notes.issues(S.connect())
    for r in rows:
        print(f"{r['path']}: {r['problem']}")
    print(C.L(f"{len(rows)} 处问题", f"{len(rows)} problem(s)") if rows else C.L("笔记格式都没问题", "All notes are fine"))


def cmd_rebuild(_: argparse.Namespace) -> None:
    if not C.markdown():
        sys.exit(C.L("只有 Markdown 存储才有索引要重建", "Only Markdown storage has an index to rebuild"))
    from . import notes
    conn = notes.connect(sync=False)
    notes.refresh(conn, force=True, actor=None)
    n = conn.execute("SELECT COUNT(*) FROM tree").fetchone()[0]
    print(C.L(f"索引已重建：{n} 条（含档案要点）", f"Index rebuilt: {n} entries (including profile points)"))


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
    conn = S.connect()
    for r in S.stats(conn):
        print(f"{r['source']:12} {r['status']:10} {r['n']}")
    if C.markdown():
        k = conn.execute("SELECT COUNT(*) FROM issue").fetchone()[0]
        print(C.L(f"\n笔记 {C.notes_dir()} · 格式问题 {k} 处", f"\nnotes {C.notes_dir()} · {k} format problem(s)"))


def cmd_token(action: str):
    """rotate：给某个平台换一个新令牌（旧的立即作废）；revoke：删掉它的令牌。改完要重启服务才生效。"""
    def run(a: argparse.Namespace) -> None:
        cfg = C.load()
        tokens: dict[str, str] = cfg.setdefault("tokens", {})
        old = [t for t, name in tokens.items() if name == a.platform]
        if not old and action == "revoke":
            sys.exit(C.L(f"没有 {a.platform} 的令牌", f"No token for {a.platform}"))
        for t in old:
            del tokens[t]
        if action == "rotate":
            tokens[secrets.token_urlsafe(24)] = a.platform
        C.save(cfg)
        print(C.L(f"{a.platform}：{'已换新令牌' if action == 'rotate' else '令牌已删除'}。重启服务生效：systemctl --user restart mousse-tree",
                  f"{a.platform}: {'new token issued' if action == 'rotate' else 'token revoked'}. Restart the service to apply: systemctl --user restart mousse-tree"))
        if action == "rotate":
            print(C.L("新地址见 mousse-tree urls，记得在该平台的连接器设置里换掉。",
                      "See mousse-tree urls for the new address, and update it in that platform's connector settings."))
    return run


def main() -> None:
    p = argparse.ArgumentParser(prog="mousse-tree", description=C.L(__doc__, USAGE_EN), formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = p.add_subparsers(dest="cmd", required=True)
    q = sp.add_parser("init"); q.add_argument("--name"); q.add_argument("--profile"); q.add_argument("--tz"); q.add_argument("--host")
    q.add_argument("--lang", choices=("zh", "en")); q.add_argument("--storage", choices=("sqlite", "markdown"))
    q.add_argument("--notes"); q.add_argument("--profile-note"); q.set_defaults(fn=cmd_init)
    q = sp.add_parser("migrate"); q.add_argument("to", choices=("markdown", "sqlite")); q.add_argument("--notes"); q.add_argument("--profile-note")
    q.set_defaults(fn=cmd_migrate)
    sp.add_parser("check").set_defaults(fn=cmd_check)
    sp.add_parser("rebuild").set_defaults(fn=cmd_rebuild)
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
    for action in ("rotate", "revoke"):
        q = sp.add_parser(action); q.add_argument("platform"); q.set_defaults(fn=cmd_token(action))
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
