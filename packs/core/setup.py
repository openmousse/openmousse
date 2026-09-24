#!/usr/bin/env python3
"""OpenMousse 安装器的第二段（第一段是仓库根目录的 install.sh：装依赖、建 venv、问三个问题，然后调这里）。

把一台已经装好 OpenClaw 的机器配成能跑 OpenMousse：
  1. ~/.openmousse/repo → 仓库（skills 里的命令都走这个固定路径）
  2. ~/.openmousse/server.json：名字、时区、监听地址、OpenClaw 的位置、令牌（phone 给手机，local 给本机脚本）
  3. 主 agent 工作区的 skills/ 里软链 packs/core 的四个 skill（handoff / agent-builder / journal / memory-tree）；AGENTS.md 末尾追加 OpenMousse 的规则
  4. openclaw.json（先备份，改完 openclaw config validate，不过就恢复）：
     - agents.defaults.skills 是列表的话追加四个 skill（没有这个键 = 不限制，不动）
     - gateway.http.endpoints.chatCompletions.enabled = true（app 的对话走它）
     - session.reset = daily 04:00（对话页按天，日结在 03:45）
     - tools.deny 加 ask_user（app 通道没人能回答工具里的提问，会卡死）
     - memory.search.extraPaths 加 shared/digest（主对话能查各 Agent 的日结）
  5. 世界树：mousse-tree init + install-openclaw（+ systemd 服务）
  6. systemd user 服务：openmousse-server、openmousse-daily-close.timer；loginctl enable-linger
再跑一遍是安全的：已有的不动，只补缺的。

用法：setup.py --repo PATH --venv PATH [--openclaw-home ~/.openclaw] [--tz Asia/Shanghai] [--name Mousse] [--bind auto|127.0.0.1|<ip>] [--no-systemd] [--no-tree]
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
AGENTS_RULES = """

## OpenMousse

> 由 OpenMousse 安装器追加（{date}）。app 和这些规则配套；删掉这一节 app 的对话仍能用，但 Agent 之间就不协作了。

- **一天的边界**：会话每天 04:00 自动重置成新的一天，app 的对话页只显示当天，之前的在历史页按天翻。03:45 会收到「【自动触发】日结」：把今天的结论写到 `memory/YYYY-MM-DD.md` 末尾 `## 日结`（做了什么、用户定了什么、明天要做的，5–10 行），同一段再写一份到 `{digest}/YYYY-MM-DD.md`，值得长期记住的进 `MEMORY.md`（就地改，不追加矛盾条目），回一行"日结好了"。
- **自动触发**：消息以「【自动触发】」开头的不是用户在说话，是系统按时间点发的。不要提问，直接做该做的事，回复两行以内。
- **Agent**：用户在 app 里可以建多个 Agent，每个是独立的 OpenClaw agent（自己的工作区、记忆、skills），app 里叫「Agent」。用户说"帮我做个 XX agent"用 `skills/agent-builder/` 直接建；属于某个 Agent 那一块的事（要建议、要计划、要记录）用 `skills/handoff/` 转给它，把答案带回来并标明来源。各 Agent 的日结在 `{digest_root}/<agent id>/`，`memory_search` 可查。
- **要问用户的问题写在回复里，不要用 ask_user 之类等待输入的工具**：app 的通道没人能回答工具里的提问，会一直卡住。
- **日志**：用户说的感受、想法、决定用 `skills/journal/` 记进日志，只回一句确认。关于用户本人的新事实、偏好、决定、近况写进世界树（`skills/memory-tree/`）。
- app 发来的附件：图片随消息直接可见；PDF / Word / 表格 / 代码抽出的文字和录音转写的文字就在消息里的 `[附件 N]` 块中，不用让用户再发一遍。
"""


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
    if link.is_symlink() or link.exists():
        if link.resolve() != repo.resolve():
            link.unlink() if link.is_symlink() else None
            if not link.exists():
                link.symlink_to(repo)
    else:
        link.symlink_to(repo)
    say(f"~/.openmousse/repo → {repo}")

    cfg = load_json(SERVER_JSON) if SERVER_JSON.exists() else {}
    fresh = not cfg
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
    dump_json(SERVER_JSON, cfg, mode=0o600)
    say(f"{'写了' if fresh else '补全了'} {SERVER_JSON}（名字 {cfg['app_name']}，时区 {cfg['timezone']}，监听 {cfg['bind']['host']}:{cfg['bind']['port']}）")
    return cfg, new_tokens


# —— 3：skills 与 AGENTS.md ——

def link_skills(workspace: Path, repo: Path, home: Path) -> None:
    sk = workspace / "skills"
    sk.mkdir(parents=True, exist_ok=True)
    for name in SKILLS:
        src = repo / "packs/core/skills" / name
        dst = sk / name
        if dst.is_symlink():
            if dst.resolve() == src.resolve():
                continue
            say(f"skills/{name} 已是别的软链（{os.readlink(dst)}），没动")
            continue
        if dst.exists():
            say(f"skills/{name} 已存在（不是软链），没动；想用仓库这份就把它移走再跑一遍")
            continue
        dst.symlink_to(src)
        say(f"skills/{name} → packs/core")
    agents_md = workspace / "AGENTS.md"
    text = agents_md.read_text(encoding="utf8") if agents_md.exists() else "# AGENTS.md\n"
    if AGENTS_MARK not in text:
        digest_root = home / "shared/digest"
        text = text.rstrip("\n") + AGENTS_RULES.format(date=datetime.now().strftime("%Y-%m-%d"), digest=digest_root / "main", digest_root=digest_root)
        agents_md.write_text(text, encoding="utf8")
        say("AGENTS.md 末尾追加了「## OpenMousse」规则")
    (home / "shared/digest/main").mkdir(parents=True, exist_ok=True)


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
        say("openclaw.json 不用改")
        return False
    backups = MOUSSE_HOME / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    backup = backups / f"openclaw.json.{datetime.now():%Y%m%d-%H%M%S}"
    shutil.copy2(oc_path, backup)
    dump_json(oc_path, oc, mode=0o600)
    r = run([openclaw_bin, "config", "validate"])
    if r.returncode != 0:
        shutil.copy2(backup, oc_path)
        say("openclaw config validate 没通过，已恢复原配置。输出：")
        print((r.stdout + r.stderr).strip()[-800:])
        return False
    for c in changes:
        say(f"openclaw.json：{c}")
    say(f"备份 {backup}；validate 通过")
    return restart


# —— 5：世界树 ——

def setup_tree(venv: Path, cfg: dict, no_systemd: bool) -> None:
    exe = venv / "bin/mousse-tree"
    if not exe.exists():
        say("venv 里没有 mousse-tree，跳过世界树（install.sh 会装；手动：pip install ./tree）")
        return
    profile = cfg.get("profile") or str(Path(cfg["openclaw_home"]) / "shared/profile/USER.md")
    r = run([str(exe), "init", "--name", cfg["app_name"], "--tz", cfg["timezone"], "--profile", profile])
    say(("世界树：" + (r.stdout or r.stderr).strip().splitlines()[0]) if (r.stdout or r.stderr) else "世界树 init 没输出")
    r = run([str(exe), "install-openclaw"])
    if r.returncode == 0:
        say("世界树：" + " / ".join(x.strip() for x in r.stdout.strip().splitlines()[:2]))
    else:
        say("世界树 install-openclaw 失败：" + (r.stderr or r.stdout).strip()[-300:])
    if not no_systemd:
        r = run([str(exe), "install-service"])
        say("世界树：" + ((r.stdout or r.stderr).strip().splitlines() or ["install-service 没输出"])[-1])


# —— 6：systemd ——

def install_systemd(repo: Path, venv: Path, tz: str) -> None:
    if not shutil.which("systemctl"):
        say("没有 systemctl：手动跑 `~/.openmousse/venv/bin/python ~/.openmousse/repo/server/run.py`，日结用 cron 每天 03:45 跑 packs/core/scripts/daily_close.py")
        return
    unit_dir = Path("~/.config/systemd/user").expanduser()
    unit_dir.mkdir(parents=True, exist_ok=True)
    for fn in ("openmousse-server.service", "openmousse-daily-close.service", "openmousse-daily-close.timer"):
        text = (HERE / "systemd" / fn).read_text(encoding="utf8").replace("{repo}", str(repo)).replace("{venv}", str(venv)).replace("{tz}", tz)
        (unit_dir / fn).write_text(text, encoding="utf8")
    run(["systemctl", "--user", "daemon-reload"])
    for unit in ("openmousse-server.service", "openmousse-daily-close.timer"):
        r = run(["systemctl", "--user", "enable", "--now", unit])
        say(f"{unit}：{'已启动' if r.returncode == 0 else '启动失败 ' + (r.stderr or r.stdout).strip()[-200:]}")
    r = run(["loginctl", "enable-linger", os.environ.get("USER") or ""])
    if r.returncode != 0:
        say("loginctl enable-linger 没成功（登出后服务会停）：用 sudo 跑一次 `loginctl enable-linger $USER`")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--venv", required=True)
    ap.add_argument("--openclaw-home", default="~/.openclaw")
    ap.add_argument("--tz")
    ap.add_argument("--name")
    ap.add_argument("--bind", default="auto")
    ap.add_argument("--no-systemd", action="store_true")
    ap.add_argument("--no-tree", action="store_true")
    a = ap.parse_args()
    repo, venv = Path(a.repo).expanduser().resolve(), Path(a.venv).expanduser().resolve()
    home = Path(a.openclaw_home).expanduser()
    oc_path = home / "openclaw.json"
    if not oc_path.exists():
        sys.exit(f"没找到 {oc_path}。先装好 OpenClaw、配好模型（openclaw onboard），再跑这个。")
    oc = load_json(oc_path)
    workspace = main_workspace(oc, home)
    workspace.mkdir(parents=True, exist_ok=True)
    print("配置")
    cfg, new_tokens = write_server_json(a, oc, home, workspace, repo)
    print("skills")
    link_skills(workspace, repo, home)
    print("openclaw.json")
    restart = patch_openclaw(oc_path, home, cfg.get("openclaw_bin") or "openclaw")
    if not a.no_tree:
        print("世界树")
        setup_tree(venv, cfg, a.no_systemd)
    if not a.no_systemd:
        print("systemd")
        install_systemd(repo, venv, cfg["timezone"])
        if restart:
            r = run(["systemctl", "--user", "is-active", "openclaw-gateway"])
            if r.stdout.strip() == "active":
                run(["systemctl", "--user", "restart", "openclaw-gateway"])
                say("openclaw-gateway 已重启（配置改了）")
            else:
                say("openclaw.json 改了，重启你的 Gateway 生效")
    elif restart:
        say("openclaw.json 改了，重启你的 Gateway 生效")

    b = cfg["bind"]
    url = f"http://{b['host']}:{b['port']}"
    print()
    print("=" * 60)
    print(f"装好了 / Done. 服务地址 / server: {url}")
    if "phone" in new_tokens:
        print(f"手机令牌，只显示这一次 / phone token, shown once: {new_tokens['phone']}")
    else:
        print("手机令牌之前已生成 / phone token was generated earlier; new one: `~/.openmousse/venv/bin/python ~/.openmousse/repo/server/tokens.py add phone2`")
    print()
    print("手机怎么连 / connecting the phone:")
    if b["host"].startswith("100."):
        print(f"  这台机器在 Tailscale 里：手机也装 Tailscale、登同一个账号，app 连接页填 {url} 和上面的令牌。")
        print(f"  This machine is on Tailscale: install Tailscale on the phone (same account), enter {url} and the token in the app.")
    elif b["host"] in ("127.0.0.1", "localhost"):
        print("  现在只监听本机。装 Tailscale（`curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up`）后再跑一遍安装器，")
        print("  或者用 `tailscale serve` / Caddy / nginx 把 127.0.0.1:8080 反代成 HTTPS，app 里填那个地址。")
        print("  Listening on localhost only. Install Tailscale and rerun the installer, or reverse-proxy 127.0.0.1:8080 as HTTPS and enter that address.")
    else:
        print(f"  app 连接页填 / enter in the app: {url} + token")
    print("  app：作者的 TestFlight 链接（仓库 README），或自己构建 app/ · the author's TestFlight link (see README) or build app/ yourself.")
    print()
    print("检查 / check:")
    print(f"  curl -H 'Authorization: Bearer <token>' {url}/api/health")
    print("  systemctl --user status openmousse-server   # logs: journalctl --user -u openmousse-server -f")
    print("  ~/.openmousse/venv/bin/mousse-tree urls      # 世界树接各平台的地址 / world-tree URLs for Claude / ChatGPT / Gemini (expose it first, see tree/README.md)")
    print("再跑一遍安装器是安全的，只补缺的 / rerunning the installer is safe, it only fills in what is missing.")


if __name__ == "__main__":
    main()
