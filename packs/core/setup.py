#!/usr/bin/env python3
"""OpenMousse 安装器的第二段（第一段是仓库根目录的 install.sh：装依赖、建 venv、问四个问题，然后调这里）。

把一台已经装好 OpenClaw 的机器配成能跑 OpenMousse：
  1. ~/.openmousse/repo → 仓库（skills 里的命令都走这个固定路径）
  2. ~/.openmousse/server.json：名字、时区、语言、监听地址、OpenClaw 的位置、令牌（phone 给手机，local 给本机脚本）
  3. 主 agent 工作区的 skills/ 里软链 packs/core 的四个 skill（handoff / agent-builder / journal / memory-tree）；AGENTS.md 末尾追加 OpenMousse 的规则（按所选语言写）
  4. openclaw.json（先备份，改完 openclaw config validate，不过就恢复）：
     - agents.defaults.skills 是列表的话追加四个 skill（没有这个键 = 不限制，不动）
     - gateway.http.endpoints.chatCompletions.enabled = true（app 的对话走它）
     - session.reset = daily 04:00（对话页按天，日结在 03:45）
     - tools.deny 加 ask_user（app 通道没人能回答工具里的提问，会卡死）
     - memory.search.extraPaths 加 shared/digest（主对话能查各 Agent 的日结）
  5. 世界树：mousse-tree init（同一种语言）+ install-openclaw（+ systemd 服务）
  6. systemd user 服务：openmousse-server、openmousse-daily-close.timer；loginctl enable-linger
再跑一遍是安全的：已有的不动，只补缺的。
语言（这里的输出、server.json 的 language、AGENTS.md 规则、世界树）：--lang；没给就用 server.json 里已有的，
再没有就看环境变量 LC_ALL / LANG（zh 开头 → 中文，其它 → English）。

用法：setup.py --repo PATH --venv PATH [--openclaw-home ~/.openclaw] [--tz Asia/Shanghai] [--name Mousse] [--lang zh|en] [--bind auto|127.0.0.1|<ip>] [--no-systemd] [--no-tree]
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
MOUSSE_HOME = Path("~/.openmousse").expanduser()
SERVER_JSON = MOUSSE_HOME / "server.json"
SKILLS = ("handoff", "agent-builder", "journal", "memory-tree")
AGENTS_MARK = "## OpenMousse"
AGENTS_RULES_ZH = """

## OpenMousse

> 由 OpenMousse 安装器追加（{date}）。app 和这些规则配套；删掉这一节 app 的对话仍能用，但 Agent 之间就不协作了。

- **一天的边界**：会话每天 04:00 自动重置成新的一天，app 的对话页只显示当天，之前的在历史页按天翻。03:45 会收到「【自动触发】日结」：把今天的结论写到 `memory/YYYY-MM-DD.md` 末尾 `## 日结`（做了什么、用户定了什么、明天要做的，5–10 行），同一段再写一份到 `{digest}/YYYY-MM-DD.md`，值得长期记住的进 `MEMORY.md`（就地改，不追加矛盾条目），回一行"日结好了"。
- **自动触发**：消息以「【自动触发】」开头的不是用户在说话，是系统按时间点发的。不要提问，直接做该做的事，回复两行以内。
- **Agent**：用户在 app 里可以建多个 Agent，每个是独立的 OpenClaw agent（自己的工作区、记忆、skills），app 里叫「Agent」。用户说"帮我做个 XX agent"用 `skills/agent-builder/` 直接建；属于某个 Agent 那一块的事（要建议、要计划、要记录）用 `skills/handoff/` 转给它，把答案带回来并标明来源。各 Agent 的日结在 `{digest_root}/<agent id>/`，`memory_search` 可查。
- **要问用户的问题写在回复里，不要用 ask_user 之类等待输入的工具**：app 的通道没人能回答工具里的提问，会一直卡住。
- **日志**：用户说的感受、想法、决定用 `skills/journal/` 记进日志，只回一句确认。关于用户本人的新事实、偏好、决定、近况写进世界树（`skills/memory-tree/`）。
- app 发来的附件：图片随消息直接可见；PDF / Word / 表格 / 代码抽出的文字和录音转写的文字就在消息里的 `[附件 N]` 块中，不用让用户再发一遍。
"""
AGENTS_RULES_EN = """

## OpenMousse

> Appended by the OpenMousse installer ({date}). The app is built around these rules; if you delete this section, chat in the app still works, but the Agents stop working together.

- **Day boundary**: the session resets to a new day at 04:00 every day. The app's chat page only shows today; earlier days are in the history page, one day at a time. At 03:45 you get "【自动触发】Daily digest": write today's conclusions at the end of `memory/YYYY-MM-DD.md` under `## Daily digest` (what got done, what the user decided, what's next tomorrow; 5–10 lines), write the same section to `{digest}/YYYY-MM-DD.md`, put anything worth remembering long-term into `MEMORY.md` (edit in place, don't append contradicting entries), and reply with one line: "Daily digest done".
- **Automatic triggers**: a message that starts with "【自动触发】" is not the user talking; the system sends it at a set time. Don't ask questions, just do what it asks, and keep the reply to two lines or less.
- **Agents**: in the app the user can create several Agents. Each one is a separate OpenClaw agent (its own workspace, memory and skills), called an "Agent" in the app. When the user says "make me an XX agent", create it right away with `skills/agent-builder/`. Anything that belongs to an Agent's area (advice, plans, records) goes to that Agent through `skills/handoff/`; bring its answer back and say where it came from. Each Agent's daily digests are in `{digest_root}/<agent id>/`, searchable with `memory_search`.
- **Put questions for the user in your reply; don't use ask_user or any other tool that waits for input**: nobody can answer a tool's prompt through the app channel, so the session would hang.
- **Journal**: feelings, thoughts and decisions the user mentions go into the journal with `skills/journal/`; reply with a single line of confirmation. New facts, preferences, decisions and life updates about the user go into the memory tree (`skills/memory-tree/`).
- Attachments from the app: images come with the message and you can see them directly; text extracted from PDF / Word / spreadsheets / code, and voice transcripts, are right in the message in `[Attachment N]` blocks, so don't ask the user to send them again.
"""

UI_LANG = "en"  # 这次安装用的语言（"zh" / "en"），main() 一开始就定下来；之后所有输出和写进文件的文字都按它


def L(zh: str, en: str) -> str:
    return zh if UI_LANG == "zh" else en


def norm_lang(value: object) -> str:
    return "zh" if str(value or "").strip().lower().startswith("zh") else "en"


def env_lang() -> str:
    """环境变量的语言（LC_ALL 优先，其次 LANG）：zh 开头 → zh，其它 → en。"""
    return norm_lang(os.environ.get("LC_ALL") or os.environ.get("LANG"))


def say(msg: str) -> None:
    print(f"  {msg}")


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=False, **kw)  # noqa: S603


def load_json(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf8"))


def dump_json(p: Path, data: dict, mode: int | None = None) -> None:
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
    if mode is not None:
        os.chmod(tmp, mode)
    tmp.replace(p)


def tailscale_ip() -> str | None:
    if not shutil.which("tailscale"):
        return None
    r = run(["tailscale", "ip", "-4"])
    ip = (r.stdout or "").strip().splitlines()
    return ip[0] if r.returncode == 0 and ip else None


def main_workspace(oc: dict, home: Path) -> Path:
    agents = oc.get("agents") or {}
    ws = ((agents.get("entries") or {}).get("main") or {}).get("workspace") or (agents.get("defaults") or {}).get("workspace")
    return Path(ws).expanduser() if ws else home / "workspace"


def default_model(oc: dict) -> str | None:
    m = ((oc.get("agents") or {}).get("defaults") or {}).get("model")
    if isinstance(m, str):
        return m
    if isinstance(m, dict) and m.get("primary"):
        return str(m["primary"])
    return None


# —— 1 + 2：repo 链接、server.json ——

def write_server_json(a: argparse.Namespace, oc: dict, home: Path, workspace: Path, repo: Path) -> tuple[dict, dict[str, str]]:
    MOUSSE_HOME.mkdir(parents=True, exist_ok=True)
    link = MOUSSE_HOME / "repo"
    if (link.is_symlink() or link.exists()) and link.resolve() == repo.resolve():
        pass  # 已经指向这个仓库
    elif link.is_symlink() or not link.exists():
        if link.is_symlink():
            link.unlink()
        link.symlink_to(repo)
        say(f"~/.openmousse/repo → {repo}")
    else:
        say(L("~/.openmousse/repo 已存在且不是软链，没动（skills 里的命令走这个路径，确认它就是这个仓库）",
              "~/.openmousse/repo exists and is not a symlink; left alone (the skills' commands use this path, make sure it is this repository)"))

    cfg = load_json(SERVER_JSON) if SERVER_JSON.exists() else {}
    fresh = not cfg
    before = json.dumps(cfg, sort_keys=True)
    new_tokens: dict[str, str] = {}

    def setdefault(key: str, value):
        if cfg.get(key) in (None, "", {}, []):
            cfg[key] = value

    if a.name:
        cfg["app_name"] = a.name
    setdefault("app_name", "Mousse")
    if a.tz:
        cfg["timezone"] = a.tz
    setdefault("timezone", "UTC")
    if a.lang:
        cfg["language"] = a.lang
    setdefault("language", UI_LANG)
    if a.bind and a.bind != "auto":
        cfg["bind"] = {"host": a.bind, "port": int(cfg.get("bind", {}).get("port") or 8080)}
    if not cfg.get("bind"):
        ts = tailscale_ip()
        cfg["bind"] = {"host": ts or "127.0.0.1", "port": 8080}
    setdefault("openclaw_home", str(home))
    setdefault("workspace", str(workspace))
    setdefault("data_dir", str(MOUSSE_HOME / "data"))
    setdefault("dist", str(repo / "app" / "dist"))
    setdefault("openclaw_bin", shutil.which("openclaw") or "openclaw")
    dm = default_model(oc)
    if dm:
        setdefault("default_model", dm)
    setdefault("agent_default_skills", ["journal", "memory-tree"])
    shared_profile = home / "shared/profile/USER.md"
    if not cfg.get("profile") and not shared_profile.exists() and (workspace / "USER.md").exists():
        cfg["profile"] = str(workspace / "USER.md")
    auth = cfg.setdefault("auth", {})
    tokens = auth.setdefault("tokens", {})
    for name in ("phone", "local"):
        if not tokens.get(name):
            tokens[name] = secrets.token_urlsafe(24)
            new_tokens[name] = tokens[name]
    auth.setdefault("tailscale_nodes", [])
    auth.setdefault("trust_loopback", False)
    b = cfg["bind"]
    lang_name = "中文" if norm_lang(cfg["language"]) == "zh" else "English"
    summary = L(f"（名字 {cfg['app_name']}，时区 {cfg['timezone']}，语言 {lang_name}，监听 {b['host']}:{b['port']}）",
                f" (name {cfg['app_name']}, timezone {cfg['timezone']}, language {lang_name}, listening on {b['host']}:{b['port']})")
    if json.dumps(cfg, sort_keys=True) == before:
        say(L(f"{SERVER_JSON} 不用改", f"{SERVER_JSON}: nothing to change") + summary)
    else:
        dump_json(SERVER_JSON, cfg, mode=0o600)
        say(L(f"{'写了' if fresh else '补全了'} {SERVER_JSON}", f"{'Wrote' if fresh else 'Updated'} {SERVER_JSON}") + summary)
    return cfg, new_tokens


# —— 3：skills 与 AGENTS.md ——

def link_skills(workspace: Path, repo: Path, home: Path) -> None:
    sk = workspace / "skills"
    sk.mkdir(parents=True, exist_ok=True)
    changed = False
    for name in SKILLS:
        src = repo / "packs/core/skills" / name
        dst = sk / name
        if dst.is_symlink():
            if dst.resolve() == src.resolve():
                continue
            say(L(f"skills/{name} 已是别的软链（{os.readlink(dst)}），没动",
                  f"skills/{name} is already a symlink to something else ({os.readlink(dst)}); left alone"))
            continue
        if dst.exists():
            say(L(f"skills/{name} 已存在（不是软链），没动；想用仓库这份就把它移走再跑一遍",
                  f"skills/{name} already exists (not a symlink); left alone. To use the repository's copy, move it away and run this again"))
            continue
        dst.symlink_to(src)
        changed = True
        say(f"skills/{name} → packs/core")
    agents_md = workspace / "AGENTS.md"
    text = agents_md.read_text(encoding="utf8") if agents_md.exists() else "# AGENTS.md\n"
    if AGENTS_MARK not in text:  # 已有这一节（不管哪种语言）就不动：只补缺的，不覆盖用户改过的规则
        digest_root = home / "shared/digest"
        rules = L(AGENTS_RULES_ZH, AGENTS_RULES_EN)
        text = text.rstrip("\n") + rules.format(date=datetime.now().strftime("%Y-%m-%d"), digest=digest_root / "main", digest_root=digest_root)
        agents_md.write_text(text, encoding="utf8")
        changed = True
        say(L("AGENTS.md 末尾追加了「## OpenMousse」规则", 'Appended the "## OpenMousse" rules to the end of AGENTS.md'))
    (home / "shared/digest/main").mkdir(parents=True, exist_ok=True)
    if not changed:
        say(L("skills 和 AGENTS.md 不用改", "skills and AGENTS.md: nothing to change"))


# —— 4：openclaw.json ——

def patch_openclaw(oc_path: Path, home: Path, openclaw_bin: str) -> bool:
    """改配置，返回是否需要重启 gateway。任何一步不过就恢复备份。"""
    oc = load_json(oc_path)
    before = json.dumps(oc, sort_keys=True)
    changes: list[str] = []
    restart = False

    skills = (oc.get("agents") or {}).get("defaults", {}).get("skills")
    if isinstance(skills, list):
        add = [s for s in SKILLS if s not in skills]
        if add:
            skills.extend(add)
            changes.append(f"agents.defaults.skills + {', '.join(add)}")

    ep = oc.setdefault("gateway", {}).setdefault("http", {}).setdefault("endpoints", {}).setdefault("chatCompletions", {})
    if not ep.get("enabled"):
        ep["enabled"] = True
        changes.append("gateway.http.endpoints.chatCompletions.enabled = true")
        restart = True

    if not (oc.get("session") or {}).get("reset"):
        oc.setdefault("session", {})["reset"] = {"mode": "daily", "atHour": 4}
        changes.append("session.reset = daily 04:00")
        restart = True

    deny = oc.setdefault("tools", {}).setdefault("deny", [])
    if isinstance(deny, list) and "ask_user" not in deny:
        deny.append("ask_user")
        changes.append("tools.deny + ask_user")
        restart = True

    paths = oc.setdefault("memory", {}).setdefault("search", {}).setdefault("extraPaths", [])
    digest = str(home / "shared/digest")
    if isinstance(paths, list) and not any(isinstance(p, dict) and p.get("path") == digest for p in paths):
        paths.append({"path": digest})
        changes.append(f"memory.search.extraPaths + {digest}")
        restart = True

    if json.dumps(oc, sort_keys=True) == before:
        say(L("openclaw.json 不用改", "openclaw.json: nothing to change"))
        return False
    backups = MOUSSE_HOME / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    backup = backups / f"openclaw.json.{datetime.now():%Y%m%d-%H%M%S}"
    shutil.copy2(oc_path, backup)
    dump_json(oc_path, oc, mode=0o600)
    r = run([openclaw_bin, "config", "validate"])
    if r.returncode != 0:
        shutil.copy2(backup, oc_path)
        say(L("openclaw config validate 没通过，已恢复原配置。输出：", "openclaw config validate failed; the original config is restored. Output:"))
        print((r.stdout + r.stderr).strip()[-800:])
        return False
    for c in changes:
        say(f"openclaw.json{L('：', ': ')}{c}")
    say(L(f"备份 {backup}；validate 通过", f"Backup {backup}; validate passed"))
    return restart


# —— 5：世界树 ——

def setup_tree(venv: Path, cfg: dict, no_systemd: bool) -> None:
    exe = venv / "bin/mousse-tree"
    tag = L("世界树：", "Memory tree: ")
    if not exe.exists():
        say(L("venv 里没有 mousse-tree，跳过世界树（install.sh 会装；手动：pip install ./tree）",
              "mousse-tree is not in the venv, skipping the memory tree (install.sh installs it; by hand: pip install ./tree)"))
        return
    profile = cfg.get("profile") or str(Path(cfg["openclaw_home"]) / "shared/profile/USER.md")
    # 不传 --name：树的 owner_name 是「用户」的称呼（写进给模型的说明："这是 X 的个人记忆树"），app_name 是助手的名字，不能混用
    r = run([str(exe), "init", "--tz", cfg["timezone"], "--profile", profile, "--lang", norm_lang(cfg.get("language"))])
    say((tag + (r.stdout or r.stderr).strip().splitlines()[0]) if (r.stdout or r.stderr) else L("世界树 init 没输出", "Memory tree: init printed nothing"))
    r = run([str(exe), "install-openclaw"])
    if r.returncode == 0:
        say(tag + " / ".join(x.strip() for x in r.stdout.strip().splitlines()[:2]))
    else:
        say(L("世界树 install-openclaw 失败：", "Memory tree: install-openclaw failed: ") + (r.stderr or r.stdout).strip()[-300:])
    if not no_systemd:
        r = run([str(exe), "install-service"])
        say(tag + ((r.stdout or r.stderr).strip().splitlines() or [L("install-service 没输出", "install-service printed nothing")])[-1])


# —— 6：systemd ——

def install_systemd(repo: Path, venv: Path, tz: str) -> None:
    if not shutil.which("systemctl"):
        say(L("没有 systemctl：手动跑 `~/.openmousse/venv/bin/python ~/.openmousse/repo/server/run.py`，日结用 cron 每天 03:45 跑 packs/core/scripts/daily_close.py",
              "No systemctl: run `~/.openmousse/venv/bin/python ~/.openmousse/repo/server/run.py` yourself, and run "
              "packs/core/scripts/daily_close.py from cron every day at 03:45 for the daily digest"))
        return
    unit_dir = Path("~/.config/systemd/user").expanduser()
    unit_dir.mkdir(parents=True, exist_ok=True)
    for fn in ("openmousse-server.service", "openmousse-daily-close.service", "openmousse-daily-close.timer"):
        text = (HERE / "systemd" / fn).read_text(encoding="utf8").replace("{repo}", str(repo)).replace("{venv}", str(venv)).replace("{tz}", tz)
        (unit_dir / fn).write_text(text, encoding="utf8")
    run(["systemctl", "--user", "daemon-reload"])
    for unit in ("openmousse-server.service", "openmousse-daily-close.timer"):
        r = run(["systemctl", "--user", "enable", "--now", unit])
        ok = r.returncode == 0
        say(L(f"{unit}：{'已启动' if ok else '启动失败 ' + (r.stderr or r.stdout).strip()[-200:]}",
              f"{unit}: {'started' if ok else 'failed to start ' + (r.stderr or r.stdout).strip()[-200:]}"))
    r = run(["loginctl", "enable-linger", os.environ.get("USER") or ""])
    if r.returncode != 0:
        say(L("loginctl enable-linger 没成功（登出后服务会停）：用 sudo 跑一次 `loginctl enable-linger $USER`",
              "loginctl enable-linger failed (the services stop when you log out): run `sudo loginctl enable-linger $USER` once"))


def main() -> None:
    global UI_LANG
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--venv", required=True)
    ap.add_argument("--openclaw-home", default="~/.openclaw")
    ap.add_argument("--tz")
    ap.add_argument("--name")
    ap.add_argument("--lang", choices=("zh", "en"))
    ap.add_argument("--bind", default="auto")
    ap.add_argument("--no-systemd", action="store_true")
    ap.add_argument("--no-tree", action="store_true")
    a = ap.parse_args()
    try:
        saved_lang = load_json(SERVER_JSON).get("language")
    except (OSError, ValueError, AttributeError):
        saved_lang = None
    UI_LANG = a.lang or (norm_lang(saved_lang) if saved_lang else env_lang())
    repo, venv = Path(a.repo).expanduser().resolve(), Path(a.venv).expanduser().resolve()
    home = Path(a.openclaw_home).expanduser()
    oc_path = home / "openclaw.json"
    if not oc_path.exists():
        sys.exit(L(f"没找到 {oc_path}。先装好 OpenClaw、配好模型（openclaw onboard），再跑这个。",
                   f"{oc_path} not found. Install OpenClaw and set up a model first (openclaw onboard), then run this again."))
    oc = load_json(oc_path)
    workspace = main_workspace(oc, home)
    workspace.mkdir(parents=True, exist_ok=True)
    print(L("配置", "Config"))
    cfg, new_tokens = write_server_json(a, oc, home, workspace, repo)
    print("skills")
    link_skills(workspace, repo, home)
    print("openclaw.json")
    restart = patch_openclaw(oc_path, home, cfg.get("openclaw_bin") or "openclaw")
    if not a.no_tree:
        print(L("世界树", "Memory tree"))
        setup_tree(venv, cfg, a.no_systemd)
    if not a.no_systemd:
        print("systemd")
        install_systemd(repo, venv, cfg["timezone"])
        if restart:
            r = run(["systemctl", "--user", "is-active", "openclaw-gateway"])
            if r.stdout.strip() == "active":
                run(["systemctl", "--user", "restart", "openclaw-gateway"])
                say(L("openclaw-gateway 已重启（配置改了）", "Restarted openclaw-gateway (its config changed)"))
            else:
                say(L("openclaw.json 改了，重启你的 Gateway 生效", "openclaw.json changed: restart your Gateway to apply it"))
    elif restart:
        say(L("openclaw.json 改了，重启你的 Gateway 生效", "openclaw.json changed: restart your Gateway to apply it"))

    b = cfg["bind"]
    url = f"http://{b['host']}:{b['port']}"
    print()
    print("=" * 60)
    print(L(f"装好了。服务地址：{url}", f"Done. Server: {url}"))
    if "phone" in new_tokens:
        print(L(f"手机令牌，只显示这一次：{new_tokens['phone']}", f"Phone token, shown only this once: {new_tokens['phone']}"))
    else:
        print(L("手机令牌之前已生成；要新的：`~/.openmousse/venv/bin/python ~/.openmousse/repo/server/tokens.py add phone2`",
                "The phone token was generated earlier; for a new one: `~/.openmousse/venv/bin/python ~/.openmousse/repo/server/tokens.py add phone2`"))
    print()
    print(L("手机怎么连：", "Connecting the phone:"))
    if b["host"].startswith("100."):
        print(L(f"  这台机器在 Tailscale 里：手机也装 Tailscale、登同一个账号，app 连接页填 {url} 和上面的令牌。",
                f"  This machine is on Tailscale: install Tailscale on the phone (same account), then enter {url} and the token above in the app."))
    elif b["host"] in ("127.0.0.1", "localhost"):
        print(L("  现在只监听本机。装 Tailscale（`curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up`）后再跑一遍安装器，",
                "  Listening on this machine only. Install Tailscale (`curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up`) and run the installer again,"))
        print(L("  或者用 `tailscale serve` / Caddy / nginx 把 127.0.0.1:8080 反代成 HTTPS，app 里填那个地址。",
                "  or reverse-proxy 127.0.0.1:8080 as HTTPS with `tailscale serve` / Caddy / nginx and enter that address in the app."))
    else:
        print(L(f"  app 连接页填 {url} 和令牌", f"  Enter {url} and the token in the app"))
    print(L("  app：作者的 TestFlight 链接（仓库 README），或自己构建 app/",
            "  The app: the author's TestFlight link (see the repository README), or build app/ yourself."))
    print()
    print(L("检查：", "Check:"))
    print(f"  curl -H 'Authorization: Bearer <token>' {url}/api/health")
    print(L("  systemctl --user status openmousse-server   # 日志：journalctl --user -u openmousse-server -f",
            "  systemctl --user status openmousse-server   # logs: journalctl --user -u openmousse-server -f"))
    print(L("  ~/.openmousse/venv/bin/mousse-tree urls      # 世界树接各平台的地址（先暴露到公网，见 tree/README.zh-CN.md）",
            "  ~/.openmousse/venv/bin/mousse-tree urls      # memory tree URLs for Claude / ChatGPT / Gemini (expose it first, see tree/README.md)"))
    print(L("再跑一遍安装器是安全的，只补缺的。", "Rerunning the installer is safe; it only fills in what is missing."))


if __name__ == "__main__":
    main()
