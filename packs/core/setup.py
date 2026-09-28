#!/usr/bin/env python3
"""OpenMousse 安装器的第二段（第一段是仓库根目录的 install.sh：装依赖、建 venv、问四个问题 + 两个可以跳过的，然后调这里）。

把一台已经装好 OpenClaw 的机器配成能跑 OpenMousse：
  1. ~/.openmousse/repo → 仓库（skills 里的命令都走这个固定路径）
  2. ~/.openmousse/server.json：名字、时区、语言、监听地址、OpenClaw 的位置、令牌（phone 给手机，local 给本机脚本）；
     --vault（服务器上已经在同步的 Obsidian 库）：think.vault = 库的文件夹、think.obsidian_vault = 库名（文件夹名），没设过才写
  3. 主 agent 工作区的 skills/ 里软链 packs/core 的十个 skill（handoff / agent-builder / journal / memory-tree / inbox / dispatch / project / board / proposals / goals）；AGENTS.md 末尾追加 OpenMousse 的规则（按所选语言写）
  4. openclaw.json（先备份，改完 openclaw config validate，不过就恢复）：
     - agents.defaults.skills 是列表的话追加主对话用的九个 skill（board 只给 Agent，写在 server.json 的 agent_default_skills；没有这个键 = 不限制，不动）
     - gateway.http.endpoints.chatCompletions.enabled = true（app 的对话走它）
     - session.reset = daily 04:00（对话页按天，日结在 03:45）
     - tools.deny 加 ask_user（app 通道没人能回答工具里的提问，会卡死）
     - memory.search.extraPaths 加 shared/digest（主对话能查各 Agent 的日结）
  5. 世界树：mousse-tree init（同一种语言）+ install-openclaw（+ systemd 服务）
     - --vault：还是 SQLite 存储就换成 Markdown，一条记忆一篇笔记放进 <库>/世界树（英文 Memory tree），档案放一份 档案.md / Profile.md
       （和 USER.md 双向同步）；已有的记忆导成笔记，一条不丢。已经是 Markdown 的不动
     - --tree-public：Tailscale Funnel 只把 /t、/m 开到公网（Claude.ai、ChatGPT、Gemini、Notion 这些从它们的云上来连），
       这台机器的 MagicDNS 名字加进 Host 白名单；Funnel 没开成（或 443 上已有只在 tailnet 里的 serve，开了会连带公开）
       就在最后打印要手动跑的命令，照样装完
     - 配置改了（换存储、加 Host）就重启 mousse-tree 服务
  6. systemd user 服务：openmousse-server、openmousse-daily-close.timer；loginctl enable-linger
再跑一遍是安全的：已有的不动，只补缺的。
语言（这里的输出、server.json 的 language、AGENTS.md 规则、世界树）：--lang；没给就用 server.json 里已有的，
再没有就看环境变量 LC_ALL / LANG（zh 开头 → 中文，其它 → English）。

用法：setup.py --repo PATH --venv PATH [--openclaw-home ~/.openclaw] [--tz Asia/Shanghai] [--name Mousse] [--lang zh|en] [--bind auto|127.0.0.1|<ip>]
               [--vault PATH] [--tree-public] [--no-systemd] [--no-tree]
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import secrets
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
MOUSSE_HOME = Path("~/.openmousse").expanduser()
SERVER_JSON = MOUSSE_HOME / "server.json"
TREE_HOME = Path(os.environ.get("MOUSSE_TREE_HOME", Path.home() / ".mousse-tree"))  # 世界树的配置和库，和 tree/openmousse_tree/config.py 同一个位置
FUNNEL_TIMEOUT = 30  # 秒：tailnet 还没开 Funnel 时 tailscale funnel 会一直等你去后台点开，安装不能卡在那
SKILLS = ("handoff", "agent-builder", "journal", "memory-tree", "inbox", "dispatch", "project", "board", "proposals", "goals", "onboarding")
MAIN_SKILLS = tuple(s for s in SKILLS if s != "board")  # 主对话用的；board（Agent 自己的表和看板）只给 Agent；proposals（日结提案）只有主对话用
AGENTS_MARK = "## OpenMousse"
AGENTS_RULES_ZH = """

## OpenMousse

> 由 OpenMousse 安装器追加（{date}）。app 和这些规则配套；删掉这一节 app 的对话仍能用，但 Agent 之间就不协作了。

- **一天的边界**：会话每天 04:00 自动重置成新的一天，app 的对话页只显示当天，之前的在历史页按天翻。03:45 会收到「【自动触发】日结」：把今天的结论写到 `memory/YYYY-MM-DD.md` 末尾 `## 日结`（做了什么、用户定了什么、明天要做的，5–10 行），同一段再写一份到 `{digest}/YYYY-MM-DD.md`，值得长期记住的进 `MEMORY.md`（就地改，不追加矛盾条目），回一行"日结好了"。
- **自动触发**：消息以「【自动触发】」开头的不是用户在说话，是系统按时间点发的。不要提问，直接做该做的事，回复两行以内。
- **Agent**：用户在 app 里可以建多个 Agent，每个是独立的 OpenClaw agent（自己的工作区、记忆、skills），app 里叫「Agent」。用户说"帮我做个 XX agent"用 `skills/agent-builder/` 直接建；属于某个 Agent 那一块的事（要建议、要计划、要记录）用 `skills/handoff/` 转给它，把答案带回来并标明来源。各 Agent 的日结在 `{digest_root}/<agent id>/`，`memory_search` 可查。
- **派活**：超过一两轮的重活（深读长文档、整理资料、写长稿、查一堆网页）用 `sessions_spawn` 派成后台任务，主对话只放人话。派之前按 `skills/dispatch/` 看额度、按「目标 / 要交 / 约束」写任务：app 对话里会出一张任务卡跟着它。
- **项目**：持续几天到几周、有目标和截止的事放项目（app 侧栏「项目」），按 `skills/project/`：用户说开就开，你发现一件事要做好几天就提议；项目的事转进项目。消息前面带「【项目空间】」项目卡时你在项目里：先看卡（每天重置后靠它接上），聊的过程中随手更新。
- **要问用户的问题写在回复里，不要用 ask_user 之类等待输入的工具**：app 的通道没人能回答工具里的提问，会一直卡住。
- **日志**：用户说的感受、想法、决定用 `skills/journal/` 记进日志，只回一句确认。关于用户本人的新事实、偏好、决定、近况写进世界树（`skills/memory-tree/`）。
- **先问再做**：用户明确让你做、能撤回的直接做，说清怎么撤回；只读的事直接做。你自己的主意（新技能、新 Agent、他没开口的补记录）、会发给别人或撤不回的（发送、花钱、超出后台额度）、新的定时任务或推送、改代码 / 配置 / 凭证，先用 `skills/inbox/` 交到 app 的「等你点头」，用户同意了再做。消息以「【收件箱】」开头、或用户引用收件箱的卡回复时，按那个 skill 接着做。
- app 发来的附件：图片随消息直接可见；PDF / Word / 表格 / 代码抽出的文字和录音转写的文字就在消息里的 `[附件 N]` 块中，不用让用户再发一遍。
"""
AGENTS_RULES_EN = """

## OpenMousse

> Appended by the OpenMousse installer ({date}). The app is built around these rules; if you delete this section, chat in the app still works, but the Agents stop working together.

- **Day boundary**: the session resets to a new day at 04:00 every day. The app's chat page only shows today; earlier days are in the history page, one day at a time. At 03:45 you get "【自动触发】Daily digest": write today's conclusions at the end of `memory/YYYY-MM-DD.md` under `## Daily digest` (what got done, what the user decided, what's next tomorrow; 5–10 lines), write the same section to `{digest}/YYYY-MM-DD.md`, put anything worth remembering long-term into `MEMORY.md` (edit in place, don't append contradicting entries), and reply with one line: "Daily digest done".
- **Automatic triggers**: a message that starts with "【自动触发】" is not the user talking; the system sends it at a set time. Don't ask questions, just do what it asks, and keep the reply to two lines or less.
- **Agents**: in the app the user can create several Agents. Each one is a separate OpenClaw agent (its own workspace, memory and skills), called an "Agent" in the app. When the user says "make me an XX agent", create it right away with `skills/agent-builder/`. Anything that belongs to an Agent's area (advice, plans, records) goes to that Agent through `skills/handoff/`; bring its answer back and say where it came from. Each Agent's daily digests are in `{digest_root}/<agent id>/`, searchable with `memory_search`.
- **Background tasks**: heavy work that takes more than a turn or two (reading a long document, gathering material, drafting something long, going through many web pages) goes out as a background task with `sessions_spawn`; the main chat keeps to plain conversation. Before you start one, check the allowance and write the task as goal / deliverable / constraints, per `skills/dispatch/`: a task card follows it in the app's chat.
- **Projects**: things that run for days or weeks with a goal and deadlines live in a project ("Projects" in the app's sidebar), per `skills/project/`: open one when the user asks, suggest one when something clearly spans several days, and send project matters into the project. When a message starts with a "【项目空间】" project card you are inside that project: read the card first (it carries you across the daily reset) and keep it updated as you go.
- **Put questions for the user in your reply; don't use ask_user or any other tool that waits for input**: nobody can answer a tool's prompt through the app channel, so the session would hang.
- **Journal**: feelings, thoughts and decisions the user mentions go into the journal with `skills/journal/`; reply with a single line of confirmation. New facts, preferences, decisions and life updates about the user go into the memory tree (`skills/memory-tree/`).
- **Ask before you act**: things the user explicitly asked for that can be undone, just do, and say how to undo them; read-only work, just do. Your own ideas (a new skill, a new Agent, filling in a record they didn't ask about), anything that reaches other people or can't be undone (sending, spending money, going over the background budget), new scheduled jobs or notifications, and code / config / credential changes go to the app's "Needs your OK" first with `skills/inbox/`; do them only once the user approves. A message that starts with "【收件箱】", or a reply that quotes an inbox card, continues from there, per that skill.
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


def tailscale(*args: str) -> subprocess.CompletedProcess:
    """跑 tailscale，最多等 FUNNEL_TIMEOUT 秒；超时或跑不起来都算失败（returncode -1），不抛。"""
    cmd = ["tailscale", *args]
    try:
        return run(cmd, timeout=FUNNEL_TIMEOUT)
    except subprocess.TimeoutExpired as e:  # 超时的时候拿到的输出是 bytes
        out, err = (x.decode("utf8", "replace") if isinstance(x, bytes) else (x or "") for x in (e.stdout, e.stderr))
        return subprocess.CompletedProcess(cmd, -1, out, err + "\n" + L(f"（等了 {FUNNEL_TIMEOUT} 秒没回，停了）", f"(no answer after {FUNNEL_TIMEOUT} seconds; stopped)"))
    except OSError as e:
        return subprocess.CompletedProcess(cmd, -1, "", str(e))


def funnel_would_expose(dns: str) -> list[str]:
    """Funnel 是按「机器名:端口」整个开的：443 上已经有只在 tailnet 里的 tailscale serve（比如 app 的服务），一开它们也跟着上公网。
    → 会被连带公开的路径。读不到 serve 配置就当没有（那样 funnel 命令多半也会失败，失败有提示）。"""
    r = tailscale("serve", "status", "--json")
    hp = f"{dns}:443"
    try:
        sc = json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else {}
        if (sc.get("AllowFunnel") or {}).get(hp):
            return []  # 已经开着 Funnel：上面的东西本来就是公开的
        return sorted(p for p in (((sc.get("Web") or {}).get(hp) or {}).get("Handlers") or {}) if p not in ("/t", "/m"))
    except (ValueError, AttributeError):
        return []


def vault_dir(raw: str | None) -> Path | None:
    """--vault：服务器上已经在同步的 Obsidian 库文件夹。转成绝对路径（不解软链）；不存在就提示一句、跳过。"""
    if not raw:
        return None
    p = Path(os.path.abspath(Path(raw).expanduser()))
    if p.is_dir():
        return p
    say(L(f"Obsidian 库 {p} 不存在（或不是文件夹），跳过。先把库同步到这台服务器（Obsidian Sync / Syncthing / git），再跑一遍安装器",
          f"Obsidian vault {p} does not exist (or is not a folder); skipped. Sync the vault to this server first (Obsidian Sync / Syncthing / git), then run the installer again"))
    return None


def same_dir(a: Path, b: Path) -> bool:
    """同一个文件夹（~、软链、写法不同都算）；有一边不存在就比绝对路径。"""
    try:
        return a.samefile(b)
    except OSError:
        return os.path.abspath(a) == os.path.abspath(b)


def tree_config() -> dict:
    """世界树的 config.json（没有或读不了 = {}）。只读：要改一律走 mousse-tree 命令。"""
    try:
        c = load_json(TREE_HOME / "config.json")
    except (OSError, ValueError):
        return {}
    return c if isinstance(c, dict) else {}


def tree_has_memories() -> bool:
    """tree.db 里有没有记忆（档案要点不算）。和 cli.py 的 _sqlite_has_memories 同一个判断：有的话 init --storage markdown 会拒绝，要用 migrate。"""
    db = TREE_HOME / "tree.db"
    if not db.exists():
        return False
    try:
        with contextlib.closing(sqlite3.connect(db.absolute().as_uri() + "?mode=ro", uri=True)) as c:
            return bool(c.execute("SELECT 1 FROM tree WHERE source != 'profile' LIMIT 1").fetchone())
    except sqlite3.Error:
        return False


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

def think_vault(cfg: dict, vault: Path) -> None:
    """--vault → server.json 的 think：思考空间的笔记放进库（think.vault），app 里「在 Obsidian 里打开」的 obsidian:// 链接用库名
    （think.obsidian_vault，默认文件夹名）。只在没设过时写：用户自己配的（哪怕是别的库）不动。"""
    think = cfg.setdefault("think", {})
    if not isinstance(think, dict):
        return
    if think.get("vault") and not same_dir(Path(str(think["vault"])).expanduser(), vault):
        say(L(f"server.json 的 think.vault 已经是 {think['vault']}，没改（思考空间继续放那里）",
              f"server.json already has think.vault = {think['vault']}; left alone (the thinking space stays there)"))
        return
    if not think.get("vault"):
        think["vault"] = str(vault)
        say(L(f"思考空间放进 Obsidian 库：{vault}", f"The thinking space goes into the Obsidian vault: {vault}"))
    if not think.get("obsidian_vault") and vault.name:
        think["obsidian_vault"] = vault.name
        say(L(f"app 里「在 Obsidian 里打开」用库名「{vault.name}」（手机上 Obsidian 里的库名不一样就改 server.json 的 think.obsidian_vault）",
              f'"Open in Obsidian" in the app uses the vault name "{vault.name}" (if the vault has another name in Obsidian on your phone, change think.obsidian_vault in server.json)'))


def write_server_json(a: argparse.Namespace, oc: dict, home: Path, workspace: Path, repo: Path, vault: Path | None = None) -> tuple[dict, dict[str, str]]:
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
    b0 = cfg.get("bind") or {}
    if a.bind and a.bind != "auto":
        cfg["bind"] = {"host": a.bind, "port": int(b0.get("port") or 8080)}
    elif not b0:
        cfg["bind"] = {"host": tailscale_ip() or "127.0.0.1", "port": 8080, "auto": True}
    elif b0.get("auto"):
        # 安装器自己选的地址：第一次装时还没有 Tailscale 就只听本机，装好 Tailscale 再跑一遍改听 Tailscale 地址（最后的提示就是这么说的）。
        # 手动指定过的（--bind 或自己改的 server.json，没有 auto）不动。
        ts = tailscale_ip()
        if ts and b0.get("host") != ts:
            cfg["bind"] = {**b0, "host": ts}
    setdefault("openclaw_home", str(home))
    setdefault("workspace", str(workspace))
    setdefault("data_dir", str(MOUSSE_HOME / "data"))
    setdefault("dist", str(repo / "app" / "dist"))
    setdefault("openclaw_bin", shutil.which("openclaw") or "openclaw")
    dm = default_model(oc)
    if dm:
        setdefault("default_model", dm)
    setdefault("agent_default_skills", ["journal", "memory-tree", "inbox", "board", "goals"])
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
    if vault:
        think_vault(cfg, vault)
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
        add = [s for s in MAIN_SKILLS if s not in skills]
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

def tree_to_vault(exe: Path, vault: Path, lang: str) -> bool:
    """--vault：世界树还是 SQLite 存储就换成 Markdown，一条记忆一篇笔记放进库里的「世界树」文件夹，档案也放一份（和 USER.md 双向同步）。
    已有的记忆一条不丢：tree.db 里有记忆用 migrate 导成笔记（tree.db 原样留着），没有就 init --storage markdown。
    已经是 Markdown 的不动（笔记放哪用户定过了）。返回换没换（换了要重启服务）。"""
    tag = L("世界树：", "Memory tree: ")
    tc = tree_config()
    if tc.get("storage") == "markdown":
        where = tc.get("notes_dir") or TREE_HOME / "notes"
        say(tag + L(f"已经是 Markdown 存储（{where}），没动", f"already on Markdown storage ({where}); left alone"))
        return False
    notes = vault / ("世界树" if lang == "zh" else "Memory tree")
    profile_note = "档案.md" if lang == "zh" else "Profile.md"
    cmd = ["migrate", "markdown"] if tree_has_memories() else ["init", "--storage", "markdown"]
    r = run([str(exe), *cmd, "--notes", str(notes), "--profile-note", profile_note])
    if r.returncode != 0:
        say(tag + L("换成 Markdown 存储没成功：", "switching to Markdown storage failed: ") + (r.stderr or r.stdout).strip()[-300:])
        return False
    say(tag + L(f"换成 Markdown 存储：一条记忆一篇笔记，放在 {notes}；档案是里面的 {profile_note}（和 USER.md 双向同步）",
                f"switched to Markdown storage: one note per memory in {notes}; the profile is {profile_note} in there (synced both ways with USER.md)"))
    lines = r.stdout.strip().splitlines()
    if cmd[0] == "migrate" and lines:
        say(tag + lines[0])  # 写了几篇笔记、核对不一致几处
    return True


def tree_public(exe: Path) -> tuple[list[str], bool]:
    """--tree-public：Claude.ai、ChatGPT、Gemini、Notion 这些从它们的云上来连，世界树要有公网 HTTPS。用 Tailscale Funnel 只开 /t 和 /m
    （管理页 /ui 不开），这台机器的 MagicDNS 名字加进世界树的 Host 白名单（不加 MCP SDK 回 421）；Funnel 没开成白名单也照加，不碍事。
    443 上已经有只在 tailnet 里的 serve 就不开（Funnel 会把它们一起公开），命令打印出来让用户自己定。
    → (还要用户在服务器上跑的命令，[] = 都好了；世界树的配置改没改)。令牌一个都不打印。"""
    tag = L("世界树公网：", "Memory tree, public: ")
    again = L("# 然后再跑一遍安装器，「让 AI 平台连世界树」答 y", "# then run the installer again and answer y to letting AI platforms connect")
    if not shutil.which("tailscale"):
        say(tag + L("这台机器没有 tailscale 命令，跳过", "no tailscale command on this machine; skipped"))
        return ["curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up", again], False
    r = tailscale("status", "--json")
    try:
        dns = str((json.loads(r.stdout).get("Self") or {}).get("DNSName") or "").rstrip(".") if r.returncode == 0 else ""
    except (ValueError, AttributeError):
        dns = ""
    if not dns:
        say(tag + L("拿不到这台机器在 Tailscale 里的名字（还没登录？），跳过", "couldn't get this machine's Tailscale name (not logged in?); skipped"))
        return ["sudo tailscale up", again], False
    port = int(tree_config().get("port") or 8787)
    funnel = [["funnel", "--bg", f"--set-path={p}", f"http://127.0.0.1:{port}{p}"] for p in ("/t", "/m")]
    shown = [" ".join(["tailscale", *args]) for args in funnel]
    todo: list[str] = []
    private = funnel_would_expose(dns)
    if private:
        paths = L("、", ", ").join(private)
        say(tag + L(f"443 端口上已经有只在 tailnet 里的服务（{paths}）。Funnel 是整个端口一起开，它们会跟着上公网，所以没替你开",
                    f"port 443 already serves {paths} inside your tailnet only. Funnel opens the whole port, so those would go public too; not turned on"))
        todo = [L(f"# 先把 {paths} 挪到别的端口（tailscale serve --https=8443 …），或者确认它们可以公开，再跑：",
                  f"# first move {paths} to another port (tailscale serve --https=8443 …), or make sure they may be public, then run:"), *shown]
    else:
        for args in funnel:
            r = tailscale(*args)
            if r.returncode == 0:
                continue
            say(tag + L("Funnel 没开成，tailscale 说：", "Funnel didn't come up; tailscale says:"))
            for x in [x.strip() for x in (r.stdout + "\n" + r.stderr).splitlines() if x.strip()][-8:]:
                say("  " + x)
            # 常见两种：还没 set --operator（不是 root 就配不了 Funnel）；tailnet 还没开 Funnel（手动跑时它给一个链接，等你点开）
            todo = [L("sudo tailscale set --operator=$USER   # 只要一次：以后不用 sudo 就能配 Funnel", "sudo tailscale set --operator=$USER   # once, so Funnel can be set up without sudo"),
                    shown[0] + L("   # tailnet 还没开 Funnel 的话它会给一个链接，点开照做", "   # if Funnel isn't enabled for your tailnet yet, it prints a link: open it and follow it"),
                    shown[1]]
            break
        if not todo:
            say(tag + L(f"Funnel 开好了：https://{dns}/t/… 和 https://{dns}/m/…（管理页 /ui 没开出去）",
                        f"Funnel is on: https://{dns}/t/… and https://{dns}/m/… (the admin page /ui stays private)"))
    if dns in (tree_config().get("public_hosts") or []):
        return todo, False
    r = run([str(exe), "init", "--host", dns])
    if r.returncode != 0:
        say(tag + L("加 Host 白名单没成功：", "adding it to the Host allowlist failed: ") + (r.stderr or r.stdout).strip()[-300:])
        return [*todo, f"{exe} init --host {dns} && systemctl --user restart mousse-tree"], False
    say(tag + L(f"{dns} 加进了世界树的 Host 白名单", f"added {dns} to the memory tree's Host allowlist"))
    return todo, True


def setup_tree(venv: Path, cfg: dict, no_systemd: bool, vault: Path | None = None, public: bool = False) -> list[str] | None:
    """→ --tree-public 还要用户手动跑的命令（[] = 都好了；None = 没要公网，或世界树没装）。"""
    exe = venv / "bin/mousse-tree"
    tag = L("世界树：", "Memory tree: ")
    if not exe.exists():
        say(L("venv 里没有 mousse-tree，跳过世界树（install.sh 会装；手动：pip install ./tree）",
              "mousse-tree is not in the venv, skipping the memory tree (install.sh installs it; by hand: pip install ./tree)"))
        return None
    lang = norm_lang(cfg.get("language"))
    profile = cfg.get("profile") or str(Path(cfg["openclaw_home"]) / "shared/profile/USER.md")
    # 不传 --name：树的 owner_name 是「用户」的称呼（写进给模型的说明："这是 X 的个人记忆树"），app_name 是助手的名字，不能混用
    r = run([str(exe), "init", "--tz", cfg["timezone"], "--profile", profile, "--lang", lang])
    say((tag + (r.stdout or r.stderr).strip().splitlines()[0]) if (r.stdout or r.stderr) else L("世界树 init 没输出", "Memory tree: init printed nothing"))
    changed = tree_to_vault(exe, vault, lang) if vault else False
    todo = None
    if public:
        todo, added = tree_public(exe)
        changed = changed or added
    r = run([str(exe), "install-openclaw"])
    if r.returncode == 0:
        say(tag + " / ".join(x.strip() for x in r.stdout.strip().splitlines()[:2]))
    else:
        say(L("世界树 install-openclaw 失败：", "Memory tree: install-openclaw failed: ") + (r.stderr or r.stdout).strip()[-300:])
    if not no_systemd:
        r = run([str(exe), "install-service"])
        say(tag + ((r.stdout or r.stderr).strip().splitlines() or [L("install-service 没输出", "install-service printed nothing")])[-1])
        if changed and shutil.which("systemctl"):  # install-service 只 enable --now，已经在跑的服务不会读新配置
            r = run(["systemctl", "--user", "restart", "mousse-tree.service"])
            say(tag + (L("配置改了，服务已重启", "its config changed; restarted the service") if r.returncode == 0 else
                       L("配置改了，重启服务失败 ", "its config changed; restarting the service failed ") + (r.stderr or r.stdout).strip()[-200:]))
    elif changed:
        say(tag + L("配置改了，重启世界树服务生效（systemctl --user restart mousse-tree）",
                    "its config changed; restart the memory tree service to apply it (systemctl --user restart mousse-tree)"))
    return todo


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
    # 服务用 restart：再跑一遍安装器（更新代码、换监听地址）要重启才生效；定时器 enable --now 就够
    for unit, cmd in (("openmousse-server.service", "restart"), ("openmousse-daily-close.timer", "start")):
        run(["systemctl", "--user", "enable", unit])
        r = run(["systemctl", "--user", cmd, unit])
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
    ap.add_argument("--vault")
    ap.add_argument("--tree-public", action="store_true")
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
    vault = vault_dir(a.vault)
    cfg, new_tokens = write_server_json(a, oc, home, workspace, repo, vault)
    print("skills")
    link_skills(workspace, repo, home)
    print("openclaw.json")
    restart = patch_openclaw(oc_path, home, cfg.get("openclaw_bin") or "openclaw")
    public = None
    if not a.no_tree:
        print(L("世界树", "Memory tree"))
        public = setup_tree(venv, cfg, a.no_systemd, vault, a.tree_public)
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
    if public is not None:
        print()
        print(L("AI 平台怎么连世界树：", "Connecting AI platforms to the memory tree:"))
        if public:
            print(L("  还没弄完，在服务器上跑：", "  Not done yet; on the server run:"))
            for x in public:
                print(f"    {x}")
        print(L("  ~/.openmousse/venv/bin/mousse-tree urls   # 每个平台的接入地址（带令牌，只在自己终端看）；app 的「我 → 世界树」也能看到",
                "  ~/.openmousse/venv/bin/mousse-tree urls   # each platform's address (it carries a token: only look at it in your own terminal); the app's Me → Memory tree shows them too"))
        print(L("  各平台在哪加见 tree/README.zh-CN.md 的「接平台」；别的 MCP 客户端先 `~/.openmousse/venv/bin/mousse-tree rotate <名字>` 给它一个自己的令牌",
                "  Where to add it on each platform: \"Connecting platforms\" in tree/README.md. Any other MCP client: `~/.openmousse/venv/bin/mousse-tree rotate <name>` gives it its own token"))
    print()
    print(L("检查：", "Check:"))
    print(f"  curl -H 'Authorization: Bearer <token>' {url}/api/health")
    print(L("  systemctl --user status openmousse-server   # 日志：journalctl --user -u openmousse-server -f",
            "  systemctl --user status openmousse-server   # logs: journalctl --user -u openmousse-server -f"))
    if public is None:
        exposed = bool(tree_config().get("public_hosts"))
        print(L("  ~/.openmousse/venv/bin/mousse-tree urls      # 世界树接各平台的地址" + ("" if exposed else "（先开公网：再跑一遍安装器，「让 AI 平台连世界树」答 y）"),
                "  ~/.openmousse/venv/bin/mousse-tree urls      # memory tree URLs for AI platforms"
                + ("" if exposed else " (open it up first: run the installer again and answer y to letting AI platforms connect)")))
    print(L("再跑一遍安装器是安全的，只补缺的。", "Rerunning the installer is safe; it only fills in what is missing."))


if __name__ == "__main__":
    main()
