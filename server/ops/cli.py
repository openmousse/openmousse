"""把运维命令挂到 mousse 的参数上（第 7 批）。../mousse.py 里只多一行 ops_cli.add(sub) 和一处分派（a.run），别的命令照旧。
每个命令的 --help 按 server.json 的 language：中文用那个模块开头的说明，英文在这里。模块用到时才导入（mousse agent 不多导入什么）。
会改东西的（writes）在 Agent 自己跑的命令里（环境里有 MOUSSE_AGENT，主机命令会给）不跑，退出码 2：那是主人的运维（更新会把跑着它的服务
重启掉一半），只查的照跑。主机命令那边 engine/host_exec.py 的 OWN 也把这几条算危险（要你点头）。"""
from __future__ import annotations

import argparse
import importlib
import os
import sys

from i18n import L

EN = {
    "doctor": """mousse doctor: a health check (works with the server stopped).

  mousse doctor [--json] [--fix] [--yes] [--strict]

Checks, section by section (✓ fine / ! look at it / ✗ failing / · just so you know, each with one line and how to fix it): the system
(Python, server/requirements.txt, free disk, Docker for the sandbox), the install (~/.openmousse/repo link, venv, mousse on PATH), server.json
and its claw section, every layer (models, search, memory, embeddings, connections, hands: the same overview as the app's settings),
sign-ins (Claude Code and Codex are asked through their own commands; we never read their tokens), data (permissions of data_dir,
secrets.env, server.json, the ChatGPT plan's folder and the memory tree's config; SQLite quick_check; the memory search FTS index;
expired pairing codes), the memory tree, the services (systemd / launchd, the nightly digest, /api/health) and the last update check.
--fix only does safe, reversible repairs: tighten permissions (0600 / 0700), create missing folders, recreate the ~/.openmousse/repo link,
rebuild the FTS index, drop expired pairing codes (server.json is backed up first); each repaired item is checked again. Repairs that stop or
restart a service also need --yes; without it you get the command to run.
--json follows OpenClaw's health contract: id, section, status (ok | warn | error | info), title, detail, fix, fixable, needsYes, fixed, note.
Exit codes: 0 no ✗ (with --strict, any ! also gives 1); 1 something is failing.
check.sh at the repository root stays: it only needs the system python3, so it runs even when the venv is broken.
""",
    "status": """mousse status: one screen (read-only, offline apart from the server's own /api/health).

  mousse status [--json]

Is the server up, version and commit, the claw, the main chat's model, today's turns and spend, scheduled jobs, cards waiting in the
inbox, and whether a newer version is out (only what the last mousse update --check saved). Exit code 1 when the server isn't answering.
""",
    "logs": """mousse logs: the server's logs.

  mousse logs [-f] [-n 200] [--since 1h] [--grep WORD] [--unit server | daily-close | tree]

Linux with systemd: journalctl --user -u openmousse-server (--since goes to journalctl: 1h, 30m, 2d or "2026-10-19 08:00"). macOS (launchd) and
hand-started servers: ~/.openmousse/logs/<name>.log (the memory tree: ~/.mousse-tree/tree.log); those lines carry no time, so --since can't
apply there. Every line is scrubbed (key-like strings and this machine's real tokens and keys) before it's printed. --grep ignores case and
gives the last n matching lines. -f keeps following (Ctrl-C stops). Exit codes: 0 printed; 1 no logs; 2 bad arguments.
""",
}


EN["backup"] = """mousse backup / restore: back up and restore.

  mousse backup create [--out FILE or FOLDER] [--no-secrets] [--no-workspace] [--no-tree] [--keep N] [--json]
  mousse backup verify FILE [--json]
  mousse backup list
  mousse restore FILE [--dry-run] [--yes]

One tar.gz (0600), named openmousse-backup-<date-time>.tar.gz, in the backups/ folder next to server.json by default; --keep keeps the newest
N (default 5). Inside: server.json (it holds your access tokens); secrets.env (left out with --no-secrets, and so are the other key files in
the data folder: chatgpt/, apps/, sentinel/, gateway-device.json, social/identity.json); the whole data folder, with SQLite databases copied
live through sqlite3's backup() (never the file being written); the memory tree's home (--no-tree leaves it out); each Agent's workspace
(--no-workspace leaves them out; links are recorded, not followed). manifest.json records the format, time, version and commit, whether keys
are included, where each part lived (under your home as ~/..., so another machine or user name works) and every file's size, sha256 and mode.
verify: one root folder, regular files only, no absolute or .. paths, every listed file present with the right size and sha256 and nothing
extra; SQLite files open and pass PRAGMA quick_check.
restore verifies first and writes nothing if that fails; snapshots the current state (openmousse-snapshot-pre-restore-..., so a restore can be
undone with mousse restore <snapshot>); refuses while the server runs unless --yes, which stops and restarts it for you (exit code 2 otherwise);
extracts to a staging folder, checks again, then swaps files in one by one (old -wal / -shm removed first). Files not in the archive stay.
Exit codes: 0 done; 1 failed (bad archive, can't write); 2 refused (server running without --yes, bad arguments).
"""


EN["update"] = """mousse update: update OpenMousse.

  mousse update [--check] [--plan] [--channel stable | main] [--yes] [--stash] [--json]

Channels: stable = the remote's newest v<VERSION> tag (made at each release); main = the remote's main. --channel is remembered in
server.json's update.channel (default stable, backed up first); the remote is origin unless update.remote says otherwise; update.wait is
how long to wait for the server to answer (90 seconds).
It first checks whether it can update, and otherwise stops and says why (exit code 2): a Docker install (MOUSSE_INSTALL=docker: pull the
new image and recreate the container), not a git checkout (rerun the installer), uncommitted changes (--stash puts them in git stash), or
a commit outside the channel's history (another development branch: it won't force a switch). Being ahead of the channel isn't an update.
A shallow clone (the installer's) gets its history first.
--check fetches and compares, and saves the result for mousse status (which never goes online). --plan lists the commits, dependency changes
and services to restart, and changes nothing.
A real update (asked in a terminal, or --yes): 1. a snapshot, the same as mousse backup; 2. check out the target and install
server/requirements.txt and ./tree with the venv's python; 3. python -m compileall -q server must pass, or it goes back to the old commit;
4. restart the services (after replies in progress finish, like safe_restart.py; without a service manager it tells you the command);
5. wait for /api/health, and roll back (old commit, dependencies, restart) if it doesn't answer. Every step goes to a log under update/logs/
next to server.json; the last receipt is update/last.json. No automatic updates, no background checks; VERSION and tags are never touched.
Exit codes: 0 updated, nothing to update, or --check / --plan done; 1 the update failed (rolled back, see the log); 2 it can't update.
"""


EN["confcheck"] = """mousse config: check and change server.json.

  mousse config validate [--json] [--file PATH]     the whole file: exit code 1 with any ✗, otherwise 0
  mousse config get DOTTED.PATH                     e.g. engine.model, bind.port; key-like values are always masked
  mousse config set DOTTED.PATH JSON-VALUE          e.g. engine.fallbacks '["moonshot/kimi-k3"]', bind.port 8080; non-JSON is a string
  mousse config unset DOTTED.PATH                   remove it (back to the default)
set / unset read the file fresh (an unreadable one is never overwritten), change a copy, validate the whole result and write nothing if it
has a ✗, back up (server.json.bak-<time>) and write atomically (a 0600 temporary file, then a rename). Keys and tokens are refused: API keys
go into secrets.env with setup_ctl.py add, access tokens with tokens.py.
The table of known keys follows config.py, engine/models.py and the engine modules. An unknown key is only a warning (!), never an error:
several sessions add keys at the same time, and a newer config must still start an older version. Wrong types and out-of-range values are
errors (✗).
"""


EN["audit"] = """mousse security audit: a security check of the config and files (no server, no network).

  mousse security audit [--json] [--fix]

Each finding says what the danger is and how to change it: permissions of secrets.env, server.json, the data folder, the ChatGPT plan's
credentials, connector tokens, Doorman's keys, the device key, the friend identity key, the memory tree's config and the backups; keys
written right in server.json; tokens that are too short or shared (a program token equal to a person's token is critical); auth.trust_loopback;
listening on something other than loopback or Tailscale; Funnel exposing /api (only checked when the tailscale command exists); the external
API letting callers approve; host commands with security: full; engine.tools.levels set to auto for things that needed your OK; MCP write
tools on auto; the ChatGPT plan allowed in the background; a sandbox network that bypasses Doorman; expired pairing codes.
--fix only does the safe ones (tighten permissions, drop expired pairing codes with server.json backed up first); the rest get a command.
Exit code 1 when anything critical is left, otherwise 0.
"""


EN["sessions"] = """mousse sessions cleanup: remove old engine session transcripts (what the model saw), then VACUUM mousse.db.

  mousse sessions cleanup [--older-than 90d] [--agent ID] [--dry-run] [--yes] [--no-vacuum] [--json]

Removes the built-in engine's sessions (engine_sessions) and their entries (messages, tool results, compaction summaries) last touched
before --older-than (default 90d; at least 1d; 30d, 12w, 6m and 1y also work, a bare number is days); --agent only that Agent's. Rows of
engine_api_sessions whose transcripts are all gone, unused for as long and with no approval results waiting go too (ACP's session/list
stops listing them). The app's chat history, the activity page, usage and spend, memory, the memory tree and workspaces aren't touched.
Kept, and counted by reason: today's sessions (the day turns over at 04:00), ones with a turn still running, an inbox card waiting for
your OK, a background task not finished or not yet delivered (its own session and the session that started it), a scheduled job running,
and owner sessions from the last 3 days not yet written back to memory (only while writeback is on).
It lists what it would do first; --dry-run stops there. Without --yes it asks in the terminal (no terminal: refused, exit code 2). The
removal runs in one write transaction that checks everything again, so a session that came into use meanwhile stays. Then VACUUM (not with
--no-vacuum; it waits up to a minute for a running turn to finish, and skips with a note if the server stays busy or holds the database).
No tables are created or changed. Exit codes: 0 removed, nothing to remove, or --dry-run; 1 failed (nothing removed); 2 refused.
"""


EN["diagnostics"] = """mousse diagnostics: a bundle for whoever helps with a problem (attach it to a bug report).

  mousse diagnostics [--out FILE.zip or FOLDER] [--print] [--lines 2000]

One zip (0600), in diagnostics/ next to server.json by default, named openmousse-diagnostics-<date-time>.zip; --print writes nothing and
prints each JSON document instead (for pasting). Inside: README.txt; summary.json (version, commit, Python, OS without the host name,
service manager, Docker or not, which commands are installed); doctor.json and audit.json (checks only, nothing repaired); status.json;
config-shape.json (every key of server.json, but only safe values: model references, the claw kind, language, time zone, switches and
numbers; other strings become markers like <str:12>, <url>, <set>; auth.tokens keeps only the names); config-check.json (mousse config
validate); engine.json (main model, fallbacks, aliases, whether each model has a key, cooldowns); databases.json (size, quick_check and
rows per table of each SQLite file, no contents); usage.json (the last 7 days, summed); logs.json (signals from the last --lines log lines:
counts by [module] and level, HTTP status classes, error types and the code lines they came from; no log line is copied, since lines can
hold bits of conversations); update.json (the last update check and receipt).
Never included: conversations, messages, memory, log text, keys, tokens, email addresses. Every document is scrubbed (key-like strings
and this machine's real tokens and keys), emails become <email>, the home folder and user name in paths become ~; a last check over the
whole bundle leaves out any document that still holds a real token, key or email (and says so in README.txt).
Works with the server stopped. Exit codes: 0 written; 1 can't write it; 2 bad arguments.
"""


EN["uninstall"] = """mousse uninstall: take OpenMousse off this machine.

  mousse uninstall [--full] [--keep-tree] [--no-backup] [--backup-to DIR] [--dry-run] [--yes] [--json]

Without --full it only removes what runs, and keeps every bit of data: the services (systemd's openmousse-server, openmousse-daily-close
.timer and .service, mousse-tree and openmousse-node: stopped, disabled, unit files deleted, then daemon-reload; launchd's ai.openmousse.*:
booted out, plists deleted; hand-started servers: only their own processes stopped), the crontab lines that run the nightly digest
(nothing else in crontab), and the links: ~/.openmousse/repo, ~/.local/bin/mousse (only when it points at the repository's bin/mousse) and
the workspaces' skills/ links into the repository's packs/core/skills. ~/.openmousse, the memory tree and the code stay.
--full also deletes ~/.openmousse entirely, the memory tree's home (--keep-tree keeps the tree, its service and data), data_dir / db /
uploads / server.json when server.json puts them elsewhere under your home with mousse in the path, and the repository, but only the
installer's copy (~/openmousse, or what install-manifest.json names) and only when it's clean (no uncommitted, stashed or unpushed work).
It warns first with every path and size, makes a backup first (like mousse backup create, keys included; in your home folder or
--backup-to, never inside what gets deleted; --no-backup skips it; if the backup fails nothing is deleted), and asks you to type delete
(no terminal: needs --yes).
Never touched, only mentioned: OpenClaw (the settings the installer added to openclaw.json, the rules at the end of AGENTS.md, the memory
tree's search path; the installer's copies from before are in ~/.openmousse/backups/, copy them out before --full if you want them); your
Obsidian vault and the memory tree's notes in it (a folder that holds them isn't deleted); Tailscale paths /t /m /s /f still published
(it prints the commands to turn them off). A Docker install (MOUSSE_INSTALL=docker) isn't removed here: delete the container and volume.
~/.openmousse/install-manifest.json, if the installer wrote one, is followed too: services, links, paths (deleted with --full only), repo,
cron and funnel entries. The same guard applies to everything: only paths under your home with mousse in them, never your home itself;
links are removed as links, never followed.
Exit codes: 0 done or --dry-run; 1 a step failed (the rest still ran; a failed backup means nothing was deleted); 2 refused.
"""


def _doc(name: str) -> str:
    """--help 的说明。中文是那个模块开头的说明（导入出错也不拦着别的命令：只是没有说明）。"""
    if L("zh", "en") == "en":
        return EN.get(name, "")
    try:
        return importlib.import_module(f"ops.{name}").__doc__ or ""
    except Exception:  # noqa: BLE001
        return ""


def writes(name: str, fn: str, a: argparse.Namespace) -> bool:
    """会改东西的运维命令：update（--check、--plan 除外）、restore、uninstall、sessions cleanup（这三样 --dry-run 除外）、config set / unset、
    带 --fix 的（doctor、security audit）。"""
    if getattr(a, "fix", False):
        return True
    if name == "update":
        return not (getattr(a, "check", False) or getattr(a, "plan", False))
    if name == "confcheck":
        return getattr(a, "action", None) in ("set", "unset")
    if (name, fn) == ("backup", "main_restore") or name in ("uninstall", "sessions"):
        return not getattr(a, "dry_run", False)
    return False


def _run(name: str, fn: str = "main"):
    def go(a: argparse.Namespace) -> int:
        if os.environ.get("MOUSSE_AGENT") and writes(name, fn, a):
            print(L("这是给你自己用的运维命令，会改东西：Agent 自己跑的命令里不跑（MOUSSE_AGENT）。在终端里自己跑；只查的（不带 --fix 的 doctor、"
                    "status、logs、config get、update --check）Agent 也能跑。",
                    "This operator command changes things, so it doesn't run inside an Agent's own command (MOUSSE_AGENT). Run it yourself in a "
                    "terminal; the read-only ones (doctor without --fix, status, logs, config get, update --check) work for Agents too."), file=sys.stderr)
            return 2
        return getattr(importlib.import_module(f"ops.{name}"), fn)(a)
    return go


def _parser(sub, name: str, help_zh: str, help_en: str, module: str | None = None, fn: str = "main") -> argparse.ArgumentParser:
    p = sub.add_parser(name, help=L(help_zh, help_en), description=_doc(module or name), formatter_class=argparse.RawDescriptionHelpFormatter)
    p.set_defaults(run=_run(module or name, fn))
    return p


def add(sub) -> None:
    """mousse 的 subparsers 上挂运维命令。"""
    p = _parser(sub, "doctor", "体检（--fix 修常见问题）", "Health check (--fix repairs common problems)")
    p.add_argument("--json", action="store_true", help=L("结构化结果（health contract）", "structured result (health contract)"))
    p.add_argument("--fix", action="store_true", help=L("修撤得回、安全的", "apply safe, reversible repairs"))
    p.add_argument("--yes", action="store_true", help=L("要停 / 重启服务的修法也做", "also do repairs that stop / restart a service"))
    p.add_argument("--strict", action="store_true", help=L("有 ! 也算没过（退出码 1）", "warnings fail too (exit code 1)"))

    p = _parser(sub, "status", "一屏摘要", "One-screen summary")
    p.add_argument("--json", action="store_true", help=L("打成 JSON", "print JSON"))

    p = _parser(sub, "logs", "看服务的日志（打过码）", "Show the server's logs (scrubbed)")
    p.add_argument("-f", "--follow", action="store_true", help=L("跟着看新的", "keep following"))
    p.add_argument("-n", "--lines", type=int, default=200, help=L("最后几行（默认 200）", "how many lines (default 200)"))
    p.add_argument("--since", help=L("从多久以前（1h、30m、2d 或日期；只有 journalctl 认）", "since when (1h, 30m, 2d or a date; journalctl only)"))
    p.add_argument("--grep", help=L("只看带这个词的（不分大小写）", "only lines with this word (case-insensitive)"))
    p.add_argument("--unit", choices=("server", "daily-close", "tree"), default="server", help=L("哪个服务", "which service"))

    p = _parser(sub, "backup", "备份（create / verify / list）", "Back up (create / verify / list)", fn="main_backup")
    bs = p.add_subparsers(dest="action", required=True)
    c = bs.add_parser("create", help=L("打一个包", "make an archive"))
    c.add_argument("--out", help=L("放哪（文件或目录；默认 server.json 旁边的 backups/）", "where to put it (file or folder; default: backups/ next to server.json)"))
    c.add_argument("--no-secrets", action="store_true", help=L("不带 secrets.env 和别的密钥文件", "leave out secrets.env and the other key files"))
    c.add_argument("--no-workspace", action="store_true", help=L("不带各 Agent 的工作区", "leave out the Agents' workspaces"))
    c.add_argument("--no-tree", action="store_true", help=L("不带世界树", "leave out the memory tree"))
    c.add_argument("--keep", type=int, default=None, help=L("留最近几份（默认 5）", "how many to keep (default 5)"))
    c.add_argument("--json", action="store_true")
    v = bs.add_parser("verify", help=L("校验一个包", "check an archive"))
    v.add_argument("file")
    v.add_argument("--json", action="store_true")
    bs.add_parser("list", help=L("列出备份和快照", "list backups and snapshots"))

    p = _parser(sub, "config", "查 / 改 server.json（整份校验）", "Check / change server.json (validated as a whole)", module="confcheck")
    cs = p.add_subparsers(dest="action", required=True)
    v = cs.add_parser("validate", help=L("整份校验", "validate the whole file"))
    v.add_argument("--json", action="store_true")
    v.add_argument("--file", help=L("查别的文件（默认 server.json）", "check another file (default: server.json)"))
    g = cs.add_parser("get", help=L("看一项（密钥打码）", "show one value (keys masked)"))
    g.add_argument("path")
    s = cs.add_parser("set", help=L("改一项（先校验、先备份）", "change one value (validated and backed up first)"))
    s.add_argument("path")
    s.add_argument("value")
    u = cs.add_parser("unset", help=L("去掉一项", "remove one value"))
    u.add_argument("path")

    p = _parser(sub, "security", "安全体检（audit）", "Security check (audit)", module="audit")
    ss = p.add_subparsers(dest="action", required=True)
    au = ss.add_parser("audit", help=L("冷检查配置和文件", "check the config and files"))
    au.add_argument("--json", action="store_true")
    au.add_argument("--fix", action="store_true", help=L("做安全的那几样（权限、过期配对码）", "do the safe fixes (permissions, expired pairing codes)"))

    p = _parser(sub, "update", "更新（stable / main 通道）", "Update (stable / main channel)")
    p.add_argument("--check", action="store_true", help=L("只比较，存下结果给 status 看", "only compare, and save the result for status"))
    p.add_argument("--plan", action="store_true", help=L("列出会做什么，不动手", "list what it would do, change nothing"))
    p.add_argument("--channel", help=L("stable（最新的发版 tag，默认）或 main", "stable (the newest release tag, default) or main"))
    p.add_argument("--yes", action="store_true", help=L("不问，直接更新", "update without asking"))
    p.add_argument("--stash", action="store_true", help=L("有没提交的改动就藏进 git stash", "put uncommitted changes in git stash"))
    p.add_argument("--json", action="store_true", help=L("--check / --plan 打成 JSON", "--check / --plan as JSON"))

    p = _parser(sub, "restore", "从备份恢复（先校验、先快照）", "Restore from a backup (verified and snapshotted first)", module="backup", fn="main_restore")
    p.add_argument("file")
    p.add_argument("--dry-run", action="store_true", help=L("只看会恢复什么", "only show what would be restored"))
    p.add_argument("--yes", action="store_true", help=L("服务在跑就替你停、恢复完再起", "stop the server if it runs, start it again after"))

    p = _parser(sub, "sessions", "内核的会话记录（cleanup 清旧的）", "Engine session transcripts (cleanup removes old ones)")
    ss = p.add_subparsers(dest="action", required=True)
    cl = ss.add_parser("cleanup", help=L("清旧的会话记录，再 VACUUM", "remove old session transcripts, then VACUUM"))
    cl.add_argument("--older-than", default="90d", help=L("多久以前最后动过的（默认 90d；1d 起，12w、6m、1y 也认）",
                                                           "last touched how long ago (default 90d; at least 1d; 12w, 6m, 1y work too)"))
    cl.add_argument("--agent", help=L("只清这个 Agent 的", "only this Agent's"))
    cl.add_argument("--dry-run", action="store_true", help=L("只列出来，不动", "only list, change nothing"))
    cl.add_argument("--yes", action="store_true", help=L("不问，直接清", "remove without asking"))
    cl.add_argument("--no-vacuum", action="store_true", help=L("清完不 VACUUM", "don't VACUUM afterwards"))
    cl.add_argument("--json", action="store_true")

    p = _parser(sub, "diagnostics", "打一个诊断包（不带对话、密钥、令牌、邮箱）", "Make a diagnostics bundle (no conversations, keys, tokens or emails)")
    p.add_argument("--out", help=L("放哪（.zip 文件或目录；默认 server.json 旁边的 diagnostics/）",
                                   "where to put it (a .zip file or a folder; default: diagnostics/ next to server.json)"))
    p.add_argument("--print", action="store_true", help=L("不写文件，打到终端", "print it instead of writing a file"))
    p.add_argument("--lines", type=int, default=None, help=L("扫日志的最后几行（默认 2000）", "how many log lines to scan (default 2000)"))

    p = _parser(sub, "uninstall", "卸载（默认只拿掉服务和软链；--full 全删，先备份）",
                "Uninstall (services and links by default; --full deletes everything, after a backup)")
    p.add_argument("--full", action="store_true", help=L("连数据、venv、世界树、安装器放的仓库一起删", "also delete the data, venv, memory tree and the installer's repository"))
    p.add_argument("--keep-tree", action="store_true", help=L("世界树留着（服务和数据）", "keep the memory tree (service and data)"))
    p.add_argument("--no-backup", action="store_true", help=L("--full 之前不打备份", "no backup before --full"))
    p.add_argument("--backup-to", help=L("备份放哪（默认家目录）", "where the backup goes (default: your home folder)"))
    p.add_argument("--dry-run", action="store_true", help=L("只列出来，不动", "only list, change nothing"))
    p.add_argument("--yes", action="store_true", help=L("不问，直接做", "go ahead without asking"))
    p.add_argument("--json", action="store_true")
