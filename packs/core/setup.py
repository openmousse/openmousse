#!/usr/bin/env python3
"""OpenMousse 安装器的第二段（第一段是仓库根目录的 install.sh：装依赖、建 venv、问四个问题 + 两个可以跳过的，然后调这里）。

把一台已经跑着 claw 的机器配成能跑 OpenMousse。默认是 OpenClaw（下面 1–6）；--claw-url 给了别的 claw / agent 的 OpenAI 兼容接口，
就不碰 openclaw.json（没有 4），server.json 写 claw 段（见 server/claw.py），skills 和规矩装到 --claw-skills / --claw-rules 给的位置（没给就跳过）。
--builtin（内核第 9 步）：不要 OpenClaw，用 OpenMousse 自带的内核（server.json 的 claw = {"kind": "builtin"}）：不碰 openclaw.json、
不链 skill（内核自己读 packs/core/skills）、不追加规矩（内核自己备开头），档案放 ~/.openmousse/workspace；模型、搜索、记忆装完以后
在终端里选（server/setup_ctl.py 的三步，有终端时最后问一句要不要现在就走）。从 builtin 或别的 claw 换回 OpenClaw：server.json 的
workspace 还是安装器当初写的 ~/.openmousse/workspace 的话，换成 OpenClaw 主 agent 的工作区（那边原来的文件不搬，最后说一句在哪）：
  1. ~/.openmousse/repo → 仓库（skills 里的命令都走这个固定路径）
  2. ~/.openmousse/server.json：名字、时区、语言、监听地址、OpenClaw 的位置、令牌（phone 给手机，local 给本机脚本）；
     --vault（服务器上已经在同步的 Obsidian 库）：think.vault = 库的文件夹、think.obsidian_vault = 库名（文件夹名），没设过才写
  3. 主 agent 工作区的 skills/ 里软链 packs/core 的 skill（handoff / agent-builder / journal / memory-tree / inbox / dispatch / project / board / proposals / goals / onboarding / study / page）；AGENTS.md 末尾追加 OpenMousse 的规则（按所选语言写）；
     刚 onboard 完的新工作区：BOOTSTRAP.md（OpenClaw 第一次对话的起名仪式，会抢走 app 的「带我走一遍」）挪进备份，IDENTITY.md 空着的名字填上，
     USER.md 还是模板就换成档案的空架子（都先备份到 ~/.openmousse/backups/）
  4. openclaw.json（先备份，改完 openclaw config validate，不过就恢复）：
     - agents.defaults.skills 是列表的话追加主对话用的十个 skill（board 只给 Agent，写在 server.json 的 agent_default_skills；没有这个键 = 不限制，不动）
     - gateway.http.endpoints.chatCompletions.enabled = true（app 的对话走它）
     - session.reset = daily 04:00（对话页按天，日结在 03:45）
     - tools.deny 加 ask_user（app 通道没人能回答工具里的提问，会卡死）
     - memory.search.extraPaths 加 shared/digest（主对话能查各 Agent 的日结）
     - skills.load.allowSymlinkTargets 加 packs/core/skills（OpenClaw 不加载指到工作区外面的 skill 软链；单独 validate，老版本不认就不加）
  5. 世界树：mousse-tree init（同一种语言）+ install-openclaw（+ systemd 服务）
     - --vault：还是 SQLite 存储就换成 Markdown，一条记忆一篇笔记放进 <库>/世界树（英文 Memory tree），档案放一份 档案.md / Profile.md
       （和 USER.md 双向同步）；已有的记忆导成笔记，一条不丢。已经是 Markdown 的不动
     - --tree-public（「开公网」那一问）：Tailscale Funnel 只把 /t、/m 开到公网（Claude.ai、ChatGPT、Gemini、Notion 这些从它们的云上来连），
       这台机器的 MagicDNS 名字加进 Host 白名单；Funnel 没开成（或 443 上已有只在 tailnet 里的 serve，开了会连带公开）
       就在最后打印要手动跑的命令，照样装完。同一问也开分享和朋友：server.json 的 share.public_port（127.0.0.1 上只有 /s、/f 的小服务）
       + share.public_url，Funnel 开 /s、/f（见 share_public）；OpenClaw 还开 llm-task 插件（名片 agent 用，见 enable_llm_task）
     - 世界树服务已经在跑（再跑一遍安装器）就重启它：pip 刚装了新代码，配置也可能改了（换存储、加 Host）
  6. 开机自启：Linux（含 WSL）用 systemd user 服务 openmousse-server、openmousse-daily-close.timer + loginctl enable-linger；
     macOS 用 launchd：~/Library/LaunchAgents 里的 ai.openmousse.server、ai.openmousse.daily-close（日志在 ~/.openmousse/logs）。
     两样都没有的机器跳过，最后说怎么手动跑
装自带内核时（--builtin，不是试装）这台机器上有 OpenClaw（--openclaw-home 的 openclaw.json）或 Hermes（$HERMES_HOME，默认 ~/.hermes 的
  config.yaml）：装完、选模型之前先问一句要不要搬过来（照 hermes setup：先问迁移）。先只看报告（mousse migrate，什么都不写），
  说要才 --apply（先备份 server.json 和数据库；源目录一个字节不改）。MOUSSE_NONINTERACTIVE 时只打印怎么跑（offer_migration）。
--trial（或环境变量 MOUSSE_TRIAL=1，第 9 批 15.1）：试装。自带的内核、只听 127.0.0.1、不开公网、一个字不碰 OpenClaw；服务可选
  （install.sh 问一句，不装就给 --no-systemd：这里在后台先起一次，装完就能在浏览器里打开）。装完说网页版在哪、手机怎么连（同一个
  局域网或 Tailscale）。已经正式装过的机器（server.json 在、安装清单不是试装）不动，退出码 2。
每次安装把放了什么（服务、软链、目录、改过的文件、后台起的进程）记进 ~/.openmousse/install-manifest.json（record()）：
  卸载（mousse uninstall）照它删干净。一样东西一条，再装一遍不重复；每放一样就写一次，装到一半停了也记得。
再跑一遍是安全的：已有的不动，只补缺的。
语言（这里的输出、server.json 的 language、AGENTS.md 规则、世界树）：--lang；没给就用 server.json 里已有的，
再没有就看环境变量 LC_ALL / LANG（zh 开头 → 中文，其它 → English）。

用法：setup.py --repo PATH --venv PATH [--openclaw-home ~/.openclaw] [--tz Asia/Shanghai] [--name Mousse] [--lang zh|en] [--bind auto|127.0.0.1|<ip>]
               [--vault PATH] [--tree-public] [--no-systemd] [--no-tree]
               [--claw-url http://…/v1 --claw-name NAME --claw-model MODEL [--claw-skills DIR] [--claw-rules FILE]]（令牌走环境变量 MOUSSE_CLAW_TOKEN）
               [--builtin]（自带的内核，不要 OpenClaw） [--trial]（试装，见上）
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import secrets
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
MOUSSE_HOME = Path("~/.openmousse").expanduser()
SERVER_JSON = MOUSSE_HOME / "server.json"
TREE_HOME = Path(os.environ.get("MOUSSE_TREE_HOME", Path.home() / ".mousse-tree"))  # 世界树的配置和库，和 tree/openmousse_tree/config.py 同一个位置
IS_MAC = sys.platform == "darwin"
LAUNCH_AGENTS = Path("~/Library/LaunchAgents").expanduser()
LOG_DIR = MOUSSE_HOME / "logs"  # macOS：launchd 服务的输出（Linux 走 journald）
MANIFEST = MOUSSE_HOME / "install-manifest.json"  # 这台机器上装了什么（record()；卸载照它删）
# 我们的服务在两种系统上的名字：systemd 单元名 → launchd 标签（世界树的标签和 tree/openmousse_tree/cli.py 里的一致）
LABELS = {"openmousse-server": "ai.openmousse.server", "openmousse-daily-close": "ai.openmousse.daily-close", "mousse-tree": "ai.openmousse.tree"}
FUNNEL_TIMEOUT = 30  # 秒：tailnet 还没开 Funnel 时 tailscale funnel 会一直等你去后台点开，安装不能卡在那
PUBLIC_PATHS = ("/t", "/m", "/s", "/f")  # Funnel 只开这几条：世界树的 MCP（/t /m）、分享页（/s）、朋友（/f）；app 的 /api 永远不上公网
SHARE_PORT = 8089  # 对外小服务（server/public.py：只有 /s 和 /f）默认的本机端口，被占了就往后找
SKILLS = ("handoff", "agent-builder", "journal", "memory-tree", "inbox", "dispatch", "project", "board", "proposals", "goals", "onboarding", "study", "page")
GENERIC_SKILLS = tuple(s for s in SKILLS if s not in ("dispatch", "proposals"))  # 别的 claw：派活（sessions_spawn）和日结提案是 OpenClaw 那边的
MAIN_SKILLS = tuple(s for s in SKILLS if s not in ("board", "study"))  # 主对话用的；board（Agent 自己的表和看板）、study（改课）只给 Agent；proposals（日结提案）只有主对话用
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

GENERIC_RULES_ZH = """

## OpenMousse

> 由 OpenMousse 安装器追加（{date}）。你的消息多半来自用户手机上的 OpenMousse app。

- 以「【自动触发】」开头的消息不是用户在说话，是系统按时间点发的：不要提问，照它说的做，回复两行以内。
- 消息前面带「（… 给你的说明，用户看不到）」的一段是 app 给你的前情：这个对话是哪个 Agent、它管什么、上一次的日结、项目卡。照着接上，不用复述。
- 要问用户的问题写在回复里，不要用等待输入的工具：app 那边没人能回答工具里的提问。
- 装了 OpenMousse 的 skills 的话：记日志、写世界树、看板和表、收件箱（要用户点头的事）、目标、项目、新建 Agent，都按对应的 skill 跑
  `python3 ~/.openmousse/repo/server/…_ctl.py`。
"""
GENERIC_RULES_EN = """

## OpenMousse

> Appended by the OpenMousse installer ({date}). Your messages mostly come from the OpenMousse app on the user's phone.

- A message that starts with "【自动触发】" is not the user talking; the system sends it at a set time. Don't ask questions, do what it says, and keep the reply to two lines or less.
- A block at the start of a message marked as a note to you that the user doesn't see is context from the app: which Agent this conversation is, what it covers, its last daily digest, the project card. Pick up from it; no need to repeat it.
- Put questions for the user in your reply; don't use tools that wait for input: nobody can answer them through the app.
- If OpenMousse's skills are installed: journaling, the memory tree, boards and tables, the inbox (things that need the user's OK), goals, projects and new Agents
  all run `python3 ~/.openmousse/repo/server/…_ctl.py` per the matching skill.
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


def load_manifest() -> dict:
    try:
        data = load_json(MANIFEST)
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("version", 1)
    data["items"] = [x for x in data.get("items") or [] if isinstance(x, dict) and x.get("kind")]
    data["installs"] = [x for x in data.get("installs") or [] if isinstance(x, dict)]
    return data


def _key(item: dict) -> tuple:
    return item.get("kind"), item.get("path") or item.get("name")


def save_manifest(data: dict) -> None:
    MOUSSE_HOME.mkdir(parents=True, exist_ok=True)
    data["updated"] = datetime.now().astimezone().isoformat(timespec="seconds")
    dump_json(MANIFEST, data, mode=0o600)


def record(kind: str, path: Path | str | None = None, **extra) -> None:
    """记进 install-manifest.json（见文件开头）。kind：dir（建的目录）、file（写的文件）、symlink（软链，target 指到哪）、
    service（manager launchd / systemd，name 是标签 / 单元名，path 是 plist / 单元文件）、edited（改过的别人的文件，backup 是备份）、
    process（后台起的进程，stop 是停它的命令）。一样东西（kind + path，没有 path 看 name）只有一条：再记一次换成新的。"""
    item: dict = {"kind": kind}
    if path is not None:
        item["path"] = str(Path(path).expanduser())
    item.update({k: str(v) if isinstance(v, Path) else v for k, v in extra.items() if v is not None})
    with contextlib.suppress(OSError):
        data = load_manifest()
        data["items"] = [x for x in data["items"] if _key(x) != _key(item)] + [item]
        save_manifest(data)


def begin_manifest(mode: str, repo: Path, venv: Path) -> None:
    """这次安装开头：记下模式（trial / builtin / openclaw / claw）、从哪个仓库装的（checkout）、venv、什么时候装的。
    顶层的 repo 只在 install.sh 这次 clone 了仓库时写：mousse uninstall --full 把它当成安装器放的那份（干净时会删）；自己的检出不写。"""
    with contextlib.suppress(OSError):
        data = load_manifest()
        vm = re.search(r'^VERSION = "([^"]+)"', (repo / "server/version.py").read_text(encoding="utf8"), re.M) if (repo / "server/version.py").exists() else None
        data.update(mode=mode, checkout=str(repo), venv=str(venv))
        if os.environ.get("MOUSSE_CLONED") == "1":
            data["repo"] = str(repo)
        data["installs"] = [*data["installs"], {"at": datetime.now().astimezone().isoformat(timespec="seconds"), "mode": mode,
                                                 **({"version": vm.group(1)} if vm else {})}][-20:]
        save_manifest(data)


def openclaw_env(home: Path) -> dict[str, str]:
    """跑 openclaw 命令时让它认 --openclaw-home 这份配置：OpenClaw 不在默认的 ~/.openclaw 时，不带这两个环境变量它会去验默认那份。"""
    return {**os.environ, "OPENCLAW_STATE_DIR": str(home), "OPENCLAW_CONFIG_PATH": str(home / "openclaw.json")}


SYSTEMD_RUN = Path("/run/systemd/system")  # 有它 = systemd 在跑（sd_booted() 也看它）


def has_systemd() -> bool:
    """systemd 真在跑：没开 systemd 的 WSL、容器里常有 systemctl 这个命令，可它连不上 systemd（Failed to connect to bus）。"""
    return shutil.which("systemctl") is not None and SYSTEMD_RUN.is_dir()


def has_launchd() -> bool:
    return IS_MAC and shutil.which("launchctl") is not None


def has_services() -> bool:
    """这台机器有我们会用的服务管理器吗（Linux / WSL 的 systemd，或 macOS 的 launchd）。"""
    return has_systemd() or has_launchd()


MANAGED = True  # 这次安装的服务交给 systemd / launchd 管了吗（main() 里定：--no-systemd、两样都没有、服务没起来 → False，提示里给自己起的命令）


def tilde(p: Path) -> str:
    return str(p).replace(str(Path.home()), "~", 1)


def log_file(name: str) -> Path:
    """服务的输出写到哪个文件：macOS 的 launchd、自己起的（没有 systemd）都写文件；Linux 的 systemd 走 journald。"""
    return TREE_HOME / "tree.log" if name == "mousse-tree" else LOG_DIR / f"{name.removeprefix('openmousse-')}.log"


def start_cmd(name: str) -> str:
    """没有 systemd / launchd 时自己起服务（放后台，输出进 log_file）。run.py 写全路径：macOS 上 python.org、Homebrew 的 Python
    起来以后把命令行开头换成 …/Python.app/Contents/MacOS/Python，stop_cmd 只能靠后面这个路径认出是我们的。"""
    if name == "mousse-tree":
        return f"nohup ~/.openmousse/venv/bin/mousse-tree serve >> {tilde(log_file(name))} 2>&1 &"
    return f"cd ~/.openmousse/repo/server && nohup ~/.openmousse/venv/bin/python ~/.openmousse/repo/server/run.py >> {tilde(log_file(name))} 2>&1 &"


def stop_cmd(name: str) -> str:
    """停掉自己起的那个（start_cmd 起的命令行）：只认自己的进程，而且是 python 跑 ~/.openmousse 里的那个 run.py / mousse-tree（全路径，
    命令行到它为止）；「|」前面是 10/22 以前的起法（venv 的 python 跑相对路径的 run.py）。跑这一行的 shell（脚本里、bash -c 里）
    命令行里也带着这串字，但它以 bash / sh 开头、后面还跟着别的，不会把自己停掉。"""
    if name == "mousse-tree":
        pat = "[Pp]ython[0-9.]* $HOME/.openmousse/venv/bin/mousse-tree serve$"
    else:
        pat = "^$HOME/.openmousse/venv/bin/python[0-9.]* run.py$|[Pp]ython[0-9.]* $HOME/.openmousse/repo/server/run.py$"
    return f'pkill -u "$(id -u)" -f "{pat}"'


def stop_old_mac_server(server_dir: Path) -> bool:
    """10/22 以前的安装器起服务用相对路径 `python run.py`：macOS 上 python.org、Homebrew 的 Python 起来以后把命令行开头换成
    …/Python.app/Contents/MacOS/Python，stop_cmd 认不出是我们的（重跑安装器停不掉旧的：新起的抢不到端口，跑的还是旧代码；10/22 Mac 上试装撞到）。
    按工作目录认：Python.app 跑相对路径的 run.py、工作目录是这个仓库的 server/ 的，停掉（同 server/ops/common.py）。→ 停了没有。"""
    if not IS_MAC or not shutil.which("pgrep") or not shutil.which("lsof"):
        return False
    stopped = False
    for pid in run(["pgrep", "-u", str(os.getuid()), "-f", r"/Python\.app/Contents/MacOS/Python run\.py$"]).stdout.split():
        out = run(["lsof", "-a", "-p", pid, "-d", "cwd", "-Fn"]).stdout
        cwd = next((x[1:] for x in out.splitlines() if x.startswith("n")), "")
        if cwd and Path(cwd).resolve() == server_dir.resolve():
            with contextlib.suppress(OSError, ValueError):
                os.kill(int(pid), signal.SIGTERM)
                stopped = True
    return stopped


def restart_cmd(name: str) -> str:
    """给人看的：在这台机器上怎么重启我们的某个服务。"""
    if not MANAGED:
        return f"{stop_cmd(name)}; {start_cmd(name)}"
    if IS_MAC:
        return f"launchctl kickstart -k gui/$(id -u)/{LABELS.get(name, name)}"
    return f"systemctl --user restart {name}"


def logs_cmd(name: str, n: int = 50) -> str:
    """给人看的：怎么看我们某个服务的日志。"""
    if IS_MAC or not MANAGED:
        return f"tail -n {n} {tilde(log_file(name))}"
    return f"journalctl --user -u {name} -n {n}"


def status_cmd(name: str) -> str:
    if IS_MAC:
        return f"launchctl print gui/$(id -u)/{LABELS.get(name, name)}"
    return f"systemctl --user status {name}"


def tailscale_bin() -> str | None:
    """tailscale 命令在哪：PATH 里的；macOS 上 App Store / brew --cask 装的是 app，命令行在 app 包里，不在 PATH。"""
    found = shutil.which("tailscale")
    if found:
        return found
    app = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"
    return app if IS_MAC and os.access(app, os.X_OK) else None


def tailscale_install_hint() -> str:
    if IS_MAC:
        return L("从 App Store 装 Tailscale（或 brew install --cask tailscale）并登录", "install Tailscale from the App Store (or brew install --cask tailscale) and sign in")
    return "curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up"


def launch_agent(label: str, args: list[str], *, workdir: Path | None = None, log: Path | None = None,
                 calendar: dict | None = None, restart: bool = True) -> tuple[bool, str]:
    """macOS：写 ~/Library/LaunchAgents/<label>.plist 并交给 launchd。常驻服务（没有 calendar）开机就起、挂了就拉起来；
    calendar（如 {"Hour": 3, "Minute": 45}）= 按本机时钟定时跑一次。定义没变、已经在跑的常驻服务 kickstart -k 重启，换上新代码。
    → (成功, 出错时的原文)。先试登录用户的 gui 域，没有图形会话（只 SSH 进来）就用 user 域。"""
    import plistlib
    import time
    LAUNCH_AGENTS.mkdir(parents=True, exist_ok=True)
    plist: dict = {"Label": label, "ProgramArguments": [str(x) for x in args],
                   # launchd 给的 PATH 只有系统目录：带上安装时的 PATH，claw（npm 全局）、brew 装的 git / python 才找得到
                   "EnvironmentVariables": {"PATH": os.environ.get("PATH") or "/usr/bin:/bin:/usr/sbin:/sbin", "LANG": os.environ.get("LANG") or "en_US.UTF-8"}}
    if calendar:
        plist["StartCalendarInterval"] = calendar
    else:
        plist.update(RunAtLoad=True, KeepAlive=True, ThrottleInterval=5)
    if workdir:
        plist["WorkingDirectory"] = str(workdir)
    if log:
        log.parent.mkdir(parents=True, exist_ok=True)
        plist["StandardOutPath"] = plist["StandardErrorPath"] = str(log)
    path = LAUNCH_AGENTS / f"{label}.plist"
    data = plistlib.dumps(plist)
    changed = not path.exists() or path.read_bytes() != data
    path.write_bytes(data)
    record("service", path, manager="launchd", name=label)
    if log:
        record("dir", log.parent)
    uid = os.getuid()
    domains = [f"gui/{uid}", f"user/{uid}"]
    loaded = next((d for d in domains if run(["launchctl", "print", f"{d}/{label}"]).returncode == 0), None)
    if loaded and changed:  # 定义改了：卸掉重装才读新的 plist
        run(["launchctl", "bootout", f"{loaded}/{label}"])
        time.sleep(1)
        loaded = None
    if loaded:
        run(["launchctl", "enable", f"{loaded}/{label}"])
        if restart and not calendar:
            r = run(["launchctl", "kickstart", "-k", f"{loaded}/{label}"])
            if r.returncode != 0:
                return False, (r.stderr or r.stdout).strip()[-200:]
        return True, ""
    err = ""
    for d in domains:
        for _ in range(3):  # 刚 bootout 的服务 launchd 还在收尾时，bootstrap 会报 5: Input/output error：等一下再试
            r = run(["launchctl", "bootstrap", d, str(path)])
            if r.returncode == 0:
                run(["launchctl", "enable", f"{d}/{label}"])
                return True, ""
            err = (r.stderr or r.stdout).strip()[-200:]
            time.sleep(1)
    return False, err


def tailscale_ip() -> str | None:
    ts = tailscale_bin()
    if not ts:
        return None
    r = run([ts, "ip", "-4"])
    ip = (r.stdout or "").strip().splitlines()
    return ip[0] if r.returncode == 0 and ip else None


def tailscale(*args: str) -> subprocess.CompletedProcess:
    """跑 tailscale，最多等 FUNNEL_TIMEOUT 秒；超时或跑不起来都算失败（returncode -1），不抛。"""
    cmd = [tailscale_bin() or "tailscale", *args]
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
        return sorted(p for p in (((sc.get("Web") or {}).get(hp) or {}).get("Handlers") or {}) if p not in PUBLIC_PATHS)
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


def write_server_json(a: argparse.Namespace, oc: dict, home: Path, workspace: Path, repo: Path, vault: Path | None = None,
                      claw: dict | None = None) -> tuple[dict, dict[str, str]]:
    MOUSSE_HOME.mkdir(parents=True, exist_ok=True)
    record("dir", MOUSSE_HOME)
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
    if link.is_symlink():
        record("symlink", link, target=repo)

    cfg = load_json(SERVER_JSON) if SERVER_JSON.exists() else {}
    fresh = not cfg
    before = json.dumps(cfg, sort_keys=True)
    if fresh:  # 新装：你自己改的东西在库里记成 owner。已有的 server.json 不加（没有这个键 = leo，见 server/config.py 的 owner_id）
        cfg["owner_id"] = "owner"
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
    old_claw = cfg.get("claw") if isinstance(cfg.get("claw"), dict) else {}
    if claw:  # 别的 claw：令牌这次没给就沿用上次的（重装时回车）
        keep = {k: v for k, v in old_claw.items() if k in ("token", "token_env", "models", "session", "headers")} if old_claw.get("kind") == "openai" else {}
        cfg["claw"] = {**keep, **{k: v for k, v in claw.items() if v or k == "model"}}  # model 可以是 ""（不带 model）
    elif old_claw.get("kind") not in (None, "openclaw"):
        cfg.pop("claw", None)  # 换回了 OpenClaw
        say(L("server.json 的 claw 段去掉了：换回 OpenClaw", "Removed the claw section from server.json: back to OpenClaw"))
        # 自带的内核 / 别的 claw 那时安装器写的是 OpenMousse 自己的工作区：换成 OpenClaw 主 agent 的（skills、规则、它记的都在那边），
        # 档案跟着换（下面按那边的 USER.md 重新定）。原来的文件不搬：OpenClaw 那边可能已经有自己的 USER.md / MEMORY.md
        own = MOUSSE_HOME / "workspace"
        if same_dir(Path(str(cfg.get("workspace") or own)).expanduser(), own) and not same_dir(workspace, own):
            cfg["workspace"], cfg["openclaw_home"] = str(workspace), str(home)
            if cfg.get("profile") and same_dir(Path(str(cfg["profile"])).expanduser(), own / "USER.md"):
                cfg.pop("profile")
            say(L(f"主 agent 的工作区换成 OpenClaw 的 {workspace}。之前的档案和记忆（USER.md、MEMORY.md、memory/）还在 {own}，想接着用就挑着拷过去",
                  f"The main agent's workspace is now OpenClaw's {workspace}. The earlier profile and memory (USER.md, MEMORY.md, memory/) "
                  f"are still in {own}; copy over what you want to keep"))
    dm = default_model(oc) if not claw else claw.get("model")
    if dm:
        setdefault("default_model", dm)
    setdefault("agent_default_skills", ["journal", "memory-tree", "inbox", "board", "goals", "page"])
    # 学习台：课件和学习页默认放数据目录下（新装的；已经配过 study 的不动）。建好目录，学习台打开就能「加一门课」
    if not isinstance(cfg.get("study"), dict) or not cfg["study"].get("materials"):
        st = dict(cfg.get("study") or {})
        data_dir = Path(cfg["data_dir"]).expanduser()
        st.setdefault("materials", str(data_dir / "study" / "materials"))
        st.setdefault("pages", str(data_dir / "study" / "pages"))
        cfg["study"] = st
    for k in ("materials", "pages"):
        try:
            Path(cfg["study"][k]).expanduser().mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
    shared_profile = home / "shared/profile/USER.md"
    if not cfg.get("profile") and not shared_profile.exists() and (workspace / "USER.md").exists():
        cfg["profile"] = str(workspace / "USER.md")
    auth = cfg.setdefault("auth", {})
    tokens = auth.setdefault("tokens", {})
    for name in ("phone", "local", "mcp"):  # mcp：claw 经 MCP 连 /mcp 用的（见 server/mcp_bridge.py），不给人看
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
    record("file", SERVER_JSON)
    return cfg, new_tokens


# —— 3：skills 与 AGENTS.md ——

def link_skills(workspace: Path, repo: Path, home: Path) -> None:
    """OpenClaw：主 agent 工作区的 skills/ 和 AGENTS.md。"""
    install_skills(workspace / "skills", workspace / "AGENTS.md", repo, home, SKILLS, (AGENTS_RULES_ZH, AGENTS_RULES_EN))


# OpenClaw 新工作区里的模板（2026.9 的 onboard 放的）：认出来才动，模板换了样子就原样不碰
IDENTITY_EMPTY_NAME = re.compile(r"^(- \*\*Name:\*\*)[ \t]*\n[ \t]*_\(pick something you like\)_[ \t]*$", re.M)
USER_TEMPLATE_MARKS = ("Replace the example below with a real directive", "- Prefer ...")


def adopt_fresh_workspace(workspace: Path, name: str) -> None:
    """OpenClaw 新工作区的三份模板交给 OpenMousse（原文件都先备份到 ~/.openmousse/backups/）：
    - BOOTSTRAP.md 是 OpenClaw 的「出生仪式」：第一次对话先让用户给助手起名、选性格和表情。它会接走 app 里的「带我走一遍」，
      可名字安装时已经问过了。挪走以后 OpenClaw 就当这个工作区配好了、不会再放回来，第一次对话由 onboarding skill 带。
    - IDENTITY.md 的 Name 还空着：填上助手的名字（其余几项留着，以后聊出来再填）。
    - USER.md 还是模板（一条示例指令「- Prefer ...」和一个文档链接）：换成档案的空架子，不然世界树和档案页会把示例当成你的信息。"""
    backups = MOUSSE_HOME / "backups"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")

    def backup(p: Path) -> Path:
        backups.mkdir(parents=True, exist_ok=True)
        dst = backups / f"{p.name}.{stamp}"
        shutil.copy2(p, dst)
        record("edited", p, backup=dst)
        return dst

    boot = workspace / "BOOTSTRAP.md"
    if boot.is_file() and not boot.is_symlink():
        dst = backup(boot)
        boot.unlink()
        say(L(f"BOOTSTRAP.md（OpenClaw 第一次对话的起名仪式）挪到了 {dst}：名字已经定了（{name}），第一次对话由 app 的「带我走一遍」来带",
              f"Moved BOOTSTRAP.md (OpenClaw's first-conversation naming ritual) to {dst}: the name is already set ({name}), "
              "and the app's \"Walk me through it\" leads the first conversation"))
    ident = workspace / "IDENTITY.md"
    text = ident.read_text(encoding="utf8") if ident.is_file() else ""
    if IDENTITY_EMPTY_NAME.search(text):
        backup(ident)
        ident.write_text(IDENTITY_EMPTY_NAME.sub(lambda m: f"{m.group(1)} {name}", text, count=1), encoding="utf8")
        say(L(f"IDENTITY.md：名字填了 {name}", f"IDENTITY.md: name set to {name}"))
    user = workspace / "USER.md"
    text = user.read_text(encoding="utf8") if user.is_file() else ""
    if all(m in text for m in USER_TEMPLATE_MARKS):
        dst = backup(user)
        user.write_text(L("# USER.md\n\n## 基本信息\n", "# USER.md\n\n## Basics\n"), encoding="utf8")
        say(L(f"USER.md 还是 OpenClaw 的模板，换成了档案的空架子（原文件 {dst}）",
              f"USER.md was still OpenClaw's template; replaced it with an empty profile (original at {dst})"))


def install_skills(sk: Path | None, agents_md: Path | None, repo: Path, home: Path, names: tuple[str, ...], rules: tuple[str, str]) -> None:
    """把 names 这些 skill 软链进 sk，规矩追加到 agents_md 末尾（已有「## OpenMousse」就不动）。别的 claw 没给文件夹 / 规则文件的那一样跳过。"""
    changed = False
    if sk is None:
        say(L("没给这个 claw 的 skills 文件夹，OpenMousse 的 skill 没装（Agent 用不了看板、收件箱这些命令；想装就重跑安装器填上）",
              "No skills folder given for this claw, so OpenMousse's skills aren't installed (Agents can't use the board, inbox and similar commands; rerun the installer to add it)"))
        names = ()
    else:
        sk.mkdir(parents=True, exist_ok=True)
    for name in names:
        src = repo / "packs/core/skills" / name
        dst = sk / name
        if dst.is_symlink():
            if dst.resolve() == src.resolve():
                record("symlink", dst, target=src)
                continue
            say(L(f"skills/{name} 已是别的软链（{os.readlink(dst)}），没动",
                  f"skills/{name} is already a symlink to something else ({os.readlink(dst)}); left alone"))
            continue
        if dst.exists():
            say(L(f"skills/{name} 已存在（不是软链），没动；想用仓库这份就把它移走再跑一遍",
                  f"skills/{name} already exists (not a symlink); left alone. To use the repository's copy, move it away and run this again"))
            continue
        dst.symlink_to(src)
        record("symlink", dst, target=src)
        changed = True
        say(f"skills/{name} → packs/core")
    text = agents_md.read_text(encoding="utf8") if agents_md and agents_md.exists() else "# AGENTS.md\n"
    if agents_md is None:
        say(L("没给这个 claw 的规则文件，OpenMousse 的规矩没加（app 发来的前情它照样看得到）",
              "No rules file given for this claw, so OpenMousse's rules weren't added (it still sees the context the app sends)"))
    elif AGENTS_MARK not in text:  # 已有这一节（不管哪种语言）就不动：只补缺的，不覆盖用户改过的规则
        digest_root = home / "shared/digest"
        text = text.rstrip("\n") + L(*rules).format(date=datetime.now().strftime("%Y-%m-%d"), digest=digest_root / "main", digest_root=digest_root)
        agents_md.parent.mkdir(parents=True, exist_ok=True)
        agents_md.write_text(text, encoding="utf8")
        record("edited", agents_md, note=f"appended the {AGENTS_MARK} section")
        changed = True
        say(L(f"{agents_md.name} 末尾追加了「## OpenMousse」规则", f'Appended the "## OpenMousse" rules to the end of {agents_md.name}'))
    if rules[0] == AGENTS_RULES_ZH:
        (home / "shared/digest/main").mkdir(parents=True, exist_ok=True)
    if not changed and (sk is not None or agents_md is not None):
        say(L("skills 和规则文件不用改", "skills and the rules file: nothing to change"))


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
    r = run([openclaw_bin, "config", "validate"], env=openclaw_env(home))
    if r.returncode != 0:
        shutil.copy2(backup, oc_path)
        say(L("openclaw config validate 没通过，已恢复原配置。输出：", "openclaw config validate failed; the original config is restored. Output:"))
        print((r.stdout + r.stderr).strip()[-800:])
        return False
    for c in changes:
        say(f"openclaw.json{L('：', ': ')}{c}")
    record("edited", oc_path, backup=backup)
    say(L(f"备份 {backup}；validate 通过", f"Backup {backup}; validate passed"))
    return restart


def allow_skill_links(oc_path: Path, skills_dir: Path, home: Path, openclaw_bin: str) -> bool:
    """OpenClaw（2026.9 起）不加载指到工作区外面的 skill 软链（Gateway 日志 reason=symlink-escape），除非目标在
    skills.load.allowSymlinkTargets 里：把 packs/core/skills 的真实路径加进去。单独备份、单独 validate，不认这个键的老版本就只撤这一项。
    返回是否要重启 Gateway。"""
    oc = load_json(oc_path)
    real = str(skills_dir.resolve())
    targets = oc.setdefault("skills", {}).setdefault("load", {}).setdefault("allowSymlinkTargets", [])
    if not isinstance(targets, list) or real in targets:
        return False
    targets.append(real)
    backups = MOUSSE_HOME / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    backup = backups / f"openclaw.json.{datetime.now():%Y%m%d-%H%M%S}.skill-links"
    shutil.copy2(oc_path, backup)
    dump_json(oc_path, oc, mode=0o600)
    if run([openclaw_bin, "config", "validate"], env=openclaw_env(home)).returncode != 0:
        shutil.copy2(backup, oc_path)
        say(L("这个版本的 OpenClaw 不认 skills.load.allowSymlinkTargets，没加（它也不拦 skill 软链）",
              "This OpenClaw doesn't know skills.load.allowSymlinkTargets; left out (it doesn't block skill symlinks either)"))
        return False
    record("edited", oc_path, backup=backup)
    say(L(f"openclaw.json：skills.load.allowSymlinkTargets + {real}（OpenClaw 才肯加载软链进来的 skill）",
          f"openclaw.json: skills.load.allowSymlinkTargets + {real} (so OpenClaw loads the linked skills)"))
    return True


def mcp_url(cfg: dict) -> str:
    """claw 连 OpenMousse 的 MCP 地址（令牌放在路径里：不用请求头、不用环境变量）。没有 mcp 令牌 = 空。"""
    tok = ((cfg.get("auth") or {}).get("tokens") or {}).get("mcp") or ""
    if not tok:
        return ""
    b = cfg.get("bind") or {}
    host = b.get("host") or "127.0.0.1"
    host = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    return f"http://{host}:{int(b.get('port') or 8080)}/mcp/{tok}"


def print_mcp_hint(url: str, preset: str) -> None:
    """别的 claw：告诉用户怎么把 OpenMousse 的 MCP 加进它的 claw（各家配置格式不一样，安装器不去改它们的配置文件）。"""
    print(L("让你的 claw 经 MCP 用看板、收件箱这些（不用 shell）。地址里带令牌，只在自己终端看：",
            "Let your claw use boards, the inbox and the rest over MCP (no shell needed). The address carries a token; only look at it in your own terminal:"))
    print(f"  {url}")
    hints = {
        "hermes": L("  Hermes：hermes mcp add openmousse --url \"<上面的地址>\"；网关重启以后生效（聊天里发 /restart，或者 hermes gateway restart）",
                    "  Hermes: hermes mcp add openmousse --url \"<the address above>\"; it takes effect once the gateway restarts (/restart in a chat, or hermes gateway restart)"),
        "nanobot": L("  nanobot：~/.nanobot/config.json 的 tools.mcpServers 加 `\"openmousse\": {\"url\": \"<上面的地址>\"}`，重启 nanobot",
                     "  nanobot: in ~/.nanobot/config.json add `\"openmousse\": {\"url\": \"<the address above>\"}` under tools.mcpServers, then restart nanobot"),
        "letta": L("  Letta Code：在对话里 /mcp add --transport http openmousse <上面的地址>",
                   "  Letta Code: in a chat, /mcp add --transport http openmousse <the address above>"),
    }
    print(hints.get(preset) or L("  在它的 MCP 设置里加一个 Streamable HTTP 服务器，名字写 openmousse",
                                 "  add a Streamable HTTP server named openmousse in its MCP settings"))


def add_mcp_server(oc_path: Path, url: str, home: Path, openclaw_bin: str) -> None:
    """OpenClaw：mcp.servers 里加 openmousse（Streamable HTTP）。Agent 就能经 MCP 用看板、收件箱这些，不用 shell（沙箱里的 Agent 也行）。
    Gateway 热加载，不用重启。单独备份、单独 validate：不认 mcp.servers 的老版本就只撤这一项。直接改文件，令牌不上命令行。"""
    if not url:
        return
    oc = load_json(oc_path)
    servers = oc.setdefault("mcp", {}).setdefault("servers", {})
    want = {"url": url, "transport": "streamable-http", "requestTimeoutMs": 320000}  # handoff 等 Agent 回话最长 300 秒，OpenClaw 默认 60 秒
    cur = servers.get("openmousse")
    if isinstance(cur, dict) and all(cur.get(k) == v for k, v in want.items()):
        return
    servers["openmousse"] = {**(cur if isinstance(cur, dict) else {}), **want}
    backups = MOUSSE_HOME / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    backup = backups / f"openclaw.json.{datetime.now():%Y%m%d-%H%M%S}.mcp"
    shutil.copy2(oc_path, backup)
    dump_json(oc_path, oc, mode=0o600)
    if run([openclaw_bin, "config", "validate"], env=openclaw_env(home)).returncode != 0:
        shutil.copy2(backup, oc_path)
        say(L("这个版本的 OpenClaw 不认 mcp.servers，没加（Agent 照旧用 skills 里的命令）",
              "This OpenClaw doesn't know mcp.servers; left out (Agents keep using the skills' commands)"))
        return
    record("edited", oc_path, backup=backup)
    say(L("openclaw.json：mcp.servers + openmousse（Agent 经 MCP 用看板、收件箱这些；Gateway 自己热加载）",
          "openclaw.json: mcp.servers + openmousse (Agents reach boards, the inbox and the rest over MCP; the Gateway hot-reloads it)"))


def enable_llm_task(oc_path: Path, home: Path, openclaw_bin: str) -> bool:
    """好友默认能加（开公网直连，或者经中继）：名片 agent 替你回朋友和别家的 agent，要一次零工具的模型调用，走 OpenClaw 自带的 llm-task 插件
    （Gateway 的 /tools/invoke 按名字调，不会进任何 agent 的对话工具表）。plugins.entries.llm-task.enabled = true；
    单独备份、单独 validate，不认的版本就撤回（名片 agent 退回固定句子）；你自己关掉的（enabled: false）不动。返回是否要重启 Gateway。"""
    oc = load_json(oc_path)
    plugins = oc.setdefault("plugins", {})
    entries = plugins.setdefault("entries", {}) if isinstance(plugins, dict) else None
    if not isinstance(entries, dict):
        return False
    cur = entries.get("llm-task") if isinstance(entries.get("llm-task"), dict) else {}
    if cur.get("enabled") is True:
        return False
    if cur.get("enabled") is False:
        say(L("openclaw.json 里 llm-task 是关着的，不动：名片 agent 只用固定句子回朋友",
              "llm-task is turned off in openclaw.json; left alone, so the card agent answers friends with fixed lines only"))
        return False
    entries["llm-task"] = {**cur, "enabled": True}
    backups = MOUSSE_HOME / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    backup = backups / f"openclaw.json.{datetime.now():%Y%m%d-%H%M%S}.llm-task"
    shutil.copy2(oc_path, backup)
    dump_json(oc_path, oc, mode=0o600)
    if run([openclaw_bin, "config", "validate"], env=openclaw_env(home)).returncode != 0:
        shutil.copy2(backup, oc_path)
        say(L("这个版本的 OpenClaw 不认 llm-task 插件，没开：名片 agent 只用固定句子回朋友",
              "This OpenClaw doesn't know the llm-task plugin; not turned on, so the card agent answers friends with fixed lines only"))
        return False
    record("edited", oc_path, backup=backup)
    say(L("openclaw.json：开了 llm-task 插件（名片 agent 用它替你回朋友，零工具）",
          "openclaw.json: turned on the llm-task plugin (the card agent answers friends through it, with no tools)"))
    return True


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
    again = L("# 然后再跑一遍安装器，「开公网」答 y", "# then run the installer again and answer y to going public")
    if not tailscale_bin():
        say(tag + L("这台机器没有 tailscale 命令，跳过", "no tailscale command on this machine; skipped"))
        return [tailscale_install_hint(), again], False
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
        return [*todo, f"{exe} init --host {dns} && {restart_cmd('mousse-tree')}"], False
    say(tag + L(f"{dns} 加进了世界树的 Host 白名单", f"added {dns} to the memory tree's Host allowlist"))
    return todo, True


def tailscale_dns() -> str:
    """这台机器在 Tailscale 里的 MagicDNS 名字；没装、没登录、读不到都是 ""。"""
    if not tailscale_bin():
        return ""
    r = tailscale("status", "--json")
    try:
        return str((json.loads(r.stdout).get("Self") or {}).get("DNSName") or "").rstrip(".") if r.returncode == 0 else ""
    except (ValueError, AttributeError):
        return ""


def free_port(start: int, tries: int = 20) -> int | None:
    """127.0.0.1 上从 start 往后第一个没人占的端口（都占了 = None）。"""
    import socket
    for p in range(start, start + tries):
        with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
            try:
                s.bind(("127.0.0.1", p))
            except OSError:
                continue
            return p
    return None


def social_defaults() -> None:
    """好友的推送没写过就按默认：好友发来的话响铃，名片 agent 代答了、有人用了邀请码静音。已有的设置不改。"""
    cfg = load_json(SERVER_JSON) if SERVER_JSON.exists() else {}
    social = dict(cfg["social"]) if isinstance(cfg.get("social"), dict) else {}
    if "push" in social:
        return
    social["push"] = {"message": "ring", "answered": "quiet", "friend": "quiet"}
    cfg["social"] = social
    dump_json(SERVER_JSON, cfg, mode=0o600)
    say(L("server.json 补上了 social.push（好友发来的话响铃，其余静音）", "server.json now has social.push (a friend's message rings, the rest is quiet)"))


def funnel_link(text: str) -> str:
    """tailscale 输出里让你去后台授权 Funnel 的那个链接（没有就空）。"""
    m = re.search(r"https://login\.tailscale\.com/\S+", text)
    return m.group(0).rstrip(".,)") if m else ""


def funnel_serving(dns: str) -> bool | None:
    """Funnel 真的把 /f 开到公网了吗（funnel status --json 里 AllowFunnel 和 /f 的处理都在）；读不出来是 None。"""
    r = tailscale("funnel", "status", "--json")
    try:
        d = json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else None
    except ValueError:
        d = None
    if not isinstance(d, dict):
        return None
    hp = f"{dns}:443"
    handlers = ((d.get("Web") or {}).get(hp) or {}).get("Handlers") or {}
    return bool((d.get("AllowFunnel") or {}).get(hp) and any(str(k).rstrip("/") == "/f" for k in handlers))


def share_public() -> tuple[list[str], str]:
    """「开公网」答 y：分享链接和朋友也要公网 HTTPS。server.json 补上 share.public_port（服务在 127.0.0.1 上另起的小服务，
    server/public.py，只有 /s 分享页和 /f 朋友，没有 /api）和 share.public_url（https://<MagicDNS 名字>），Funnel 只开 /s、/f 指过去。
    好友的推送默认值在 social_defaults()（不开公网也写）。
    public_url 已经是别的地址（自己的域名 / 反向代理）就只补端口，Funnel 不动，告诉用户转哪两条。已有的设置一律不改。
    → (还要用户在服务器上跑的命令，[] = 都好了；对外地址，"" = 没配成)。服务要重启才起小服务：安装器最后一步本来就重启。"""
    tag = L("分享和朋友：", "Sharing and friends: ")
    again = L("# 然后再跑一遍安装器，「开公网」答 y", "# then run the installer again and answer y to going public")
    if not tailscale_bin():
        say(tag + L("这台机器没有 tailscale 命令，跳过", "no tailscale command on this machine; skipped"))
        return [tailscale_install_hint(), again], ""
    dns = tailscale_dns()
    if not dns:
        say(tag + L("拿不到这台机器在 Tailscale 里的名字（还没登录？），跳过", "couldn't get this machine's Tailscale name (not logged in?); skipped"))
        return ["sudo tailscale up", again], ""
    cfg = load_json(SERVER_JSON) if SERVER_JSON.exists() else {}
    share = dict(cfg["share"]) if isinstance(cfg.get("share"), dict) else {}
    social = dict(cfg["social"]) if isinstance(cfg.get("social"), dict) else {}
    own = f"https://{dns}"
    try:
        port = int(share.get("public_port") or 0) or None
    except (TypeError, ValueError):
        port = None
    changed = []
    if not port:
        port = free_port(SHARE_PORT)
        if not port:
            say(tag + L(f"127.0.0.1 上 {SHARE_PORT} 往后 20 个端口都被占了，没配", f"ports {SHARE_PORT} and the next 19 on 127.0.0.1 are all taken; not set up"))
            return [], ""
        share["public_port"] = port
        changed.append(f"share.public_port = {port}")
    url = str(share.get("public_url") or "").strip().rstrip("/")
    if not url:
        url = share["public_url"] = own
        changed.append(f"share.public_url = {own}")
    if changed:
        cfg["share"], cfg["social"] = share, social
        dump_json(SERVER_JSON, cfg, mode=0o600)
        say(tag + L("server.json 补上了 ", "server.json now has ") + L("、", ", ").join(changed))
    if url != own:
        say(tag + L(f"对外地址是你自己配的 {url}，Funnel 不动：把 {url}/s 和 {url}/f 转到 http://127.0.0.1:{port}（同样的路径）",
                    f"the public address is your own {url}, so Funnel is left alone: forward {url}/s and {url}/f to http://127.0.0.1:{port} (same paths)"))
        return [], url
    funnel = [["funnel", "--bg", f"--set-path={p}", f"http://127.0.0.1:{port}{p}"] for p in ("/s", "/f")]
    shown = [" ".join(["tailscale", *args]) for args in funnel]
    private = funnel_would_expose(dns)
    if private:
        paths = L("、", ", ").join(private)
        say(tag + L(f"443 端口上已经有只在 tailnet 里的服务（{paths}），Funnel 会把它们一起公开，所以没替你开",
                    f"port 443 already serves {paths} inside your tailnet only; Funnel would make those public too, so it wasn't turned on"))
        return [L(f"# 先把 {paths} 挪到别的端口（tailscale serve --https=8443 …），或者确认它们可以公开，再跑：",
                  f"# first move {paths} to another port (tailscale serve --https=8443 …), or make sure they may be public, then run:"), *shown], url
    for args in funnel:
        r = tailscale(*args)
        if r.returncode == 0:
            continue
        out = r.stdout + "\n" + r.stderr
        link = funnel_link(out)
        say(tag + L("Funnel 没开成，tailscale 说：", "Funnel didn't come up; tailscale says:"))
        for x in [x.strip() for x in out.splitlines() if x.strip()][-8:]:
            say("  " + x)
        say(tag + L("在此之前好友经 OpenMousse 中继找到这台服务器，照样能加", "until then friends reach this server through the OpenMousse relay, so adding friends still works"))
        steps = [L(f"# 先在浏览器里打开这个链接，给你的 tailnet 开 Funnel：{link}", f"# first open this link in a browser to enable Funnel for your tailnet: {link}")] if link else []
        return steps + [L("sudo tailscale set --operator=$USER   # 只要一次：以后不用 sudo 就能配 Funnel", "sudo tailscale set --operator=$USER   # once, so Funnel can be set up without sudo"),
                        shown[0] + ("" if link else L("   # tailnet 还没开 Funnel 的话它会给一个链接，点开照做", "   # if Funnel isn't enabled for your tailnet yet, it prints a link: open it and follow it")),
                        shown[1]], url
    serving = funnel_serving(dns)
    if serving is False:  # 命令说成功了，但状态里 /f 没在公网上（2026-10-05 第一个朋友就是这样）
        say(tag + L("Funnel 命令跑完了，但 /f 没有出现在公网上；好友先经 OpenMousse 中继找到这台服务器", "Funnel ran, but /f isn't public; friends reach this server through the OpenMousse relay for now"))
        return [L("tailscale funnel status   # 看 /f 在不在、有没有标 (Funnel on)", "tailscale funnel status   # check that /f is listed with (Funnel on)"), *shown], url
    say(tag + L(f"Funnel 开好了：{own}/s/…（分享链接）和 {own}/f/…（好友）", f"Funnel is on: {own}/s/… (share links) and {own}/f/… (friends)"))
    return [], url


def probe_claw(c: dict) -> tuple[bool, str]:
    """别的 claw 的接口连不连得上：GET <url>/models（多数 OpenAI 兼容接口都有；404 也算连得上，说明服务在）。不打印令牌。"""
    import urllib.error
    import urllib.request
    token = c.get("token") or ""
    if not token and c.get("token_env"):  # Hermes 之类：令牌在它自己的 .env（claw 段的 env_file）或环境变量里
        token = os.environ.get(c["token_env"], "")
        env_file = Path(str(c.get("env_file") or "")).expanduser() if c.get("env_file") else None
        if not token and env_file and env_file.is_file():
            for line in env_file.read_text(encoding="utf8").splitlines():
                k, sep, v = line.strip().removeprefix("export ").partition("=")
                if sep and k.strip() == c["token_env"]:
                    token = v.strip().strip("'\"")
    req = urllib.request.Request(str(c.get("url") or "").rstrip("/") + "/models", headers={"Authorization": f"Bearer {token}"} if token else {})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:  # noqa: S310 — 用户自己填的地址
            return True, f"HTTP {r.status}"
    except urllib.error.HTTPError as e:
        return e.code not in (401, 403) and e.code < 500, f"HTTP {e.code}"
    except (urllib.error.URLError, OSError, ValueError) as e:
        return False, type(e).__name__


def wait_server(cfg: dict, wait: float = 20) -> None:
    """刚重启的 OpenMousse 服务要一两秒才回话：等它回话再往下（小结、check.sh 紧接着跑不会看到「服务连不上」）。
    任何 HTTP 回答都算起来了（没带令牌是 401）。"""
    import time
    import urllib.error
    import urllib.request
    b = cfg.get("bind") or {}
    host = b.get("host") or "127.0.0.1"
    host = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    url = f"http://{host}:{int(b.get('port') or 8080)}/api/health"
    deadline = time.time() + wait
    while True:
        try:
            urllib.request.urlopen(url, timeout=2).close()  # noqa: S310 — 本机的服务
            return
        except urllib.error.HTTPError:
            return
        except (urllib.error.URLError, OSError):
            if time.time() >= deadline:
                say(L(f"openmousse-server 过了 {int(wait)} 秒还没回话：`{logs_cmd('openmousse-server')}` 看为什么",
                      f"openmousse-server isn't answering after {int(wait)} seconds: see why with `{logs_cmd('openmousse-server')}`"))
                return
            time.sleep(0.5)


def gateway_applies_config(oc_path: Path) -> bool:
    """Gateway 自己套用 openclaw.json 的改动吗：gateway.reload.mode 默认 hybrid（2026.8 起只剩 hybrid / off）= 能热加载的马上生效，
    要重启的（比如 memory.search）等手头的对话做完它自己重启；off 和早先的 hot 要人重启。
    自己套用的时候安装器不去重启它：claw 在自己的一轮里跑安装器时，同步的 `systemctl restart` 会互相等住（Gateway 重启前先排空，
    等这一轮做完；这一轮在等安装器；安装器在等 systemctl），最多 5 分钟后 Gateway 退出，systemd（KillMode=mixed）把同一组里
    还在跑的安装器一起杀掉：小结和配对码都出不来。要重启时也用 --no-block，不等。"""
    mode = ((load_json(oc_path).get("gateway") or {}).get("reload") or {}).get("mode") if oc_path.exists() else None
    return str(mode or "hybrid") not in ("off", "hot")


def gateway_token(oc_path: Path) -> str:
    """openclaw.json 的 gateway.auth.token，照 OpenClaw 的写法认环境变量引用："${VAR}"（$${VAR} = 字面的）、
    SecretRef {"source": "env", "id": "VAR"}；没写就用 OPENCLAW_GATEWAY_TOKEN。变量先看环境，再看 openclaw.json 旁边的 .env
    （OpenClaw 自己也读它）。缺了 = 空。"""
    def var(name: str) -> str:
        if os.environ.get(name):
            return os.environ[name]
        with contextlib.suppress(OSError, UnicodeDecodeError):
            for line in (oc_path.parent / ".env").read_text(encoding="utf8").splitlines():
                k, sep, v = line.strip().removeprefix("export ").partition("=")
                if sep and k.strip() == name:
                    return v.strip().strip("'\"")
        return ""

    auth = ((load_json(oc_path).get("gateway") or {}).get("auth") or {}) if oc_path.exists() else {}
    tok = auth.get("token") if isinstance(auth, dict) else None
    if isinstance(tok, dict):
        return var(str(tok.get("id") or "")) if tok.get("source") == "env" else ""
    if not isinstance(tok, str):
        return var("OPENCLAW_GATEWAY_TOKEN")
    ref = re.compile(r"\$(\$?)\{([A-Z_][A-Z0-9_]*)\}")
    if any(not m[1] and not var(m[2]) for m in ref.finditer(tok)):
        return ""
    return ref.sub(lambda m: "${" + m[2] + "}" if m[1] else var(m[2]), tok)


def probe_gateway(url: str, oc_path: Path, wait: float = 0) -> tuple[bool, str]:
    """OpenClaw Gateway 连不连得上（app 的对话走它）：GET <gateway>/v1/models，带 openclaw.json 里 Gateway 的令牌，不打印。
    刚重启过的 Gateway 要二三十秒才起来：wait 秒内连不上就接着等。"""
    import time
    import urllib.error
    import urllib.request
    token = gateway_token(oc_path)
    req = urllib.request.Request(url.rstrip("/") + "/v1/models", headers={"Authorization": f"Bearer {token}"} if token else {})
    deadline = time.time() + wait
    while True:
        try:
            with urllib.request.urlopen(req, timeout=5) as r:  # noqa: S310 — 本机的 Gateway
                return True, f"HTTP {r.status}"
        except urllib.error.HTTPError as e:
            return e.code not in (401, 403) and e.code < 500, f"HTTP {e.code}"
        except (urllib.error.URLError, OSError, ValueError) as e:
            if time.time() >= deadline:
                return False, str(getattr(e, "reason", "") or type(e).__name__)[:120]
            time.sleep(2)


def setup_tree(venv: Path, cfg: dict, home: Path, no_systemd: bool, vault: Path | None = None, public: bool = False, openclaw: bool = True,
               builtin: bool = False) -> list[str] | None:
    """→ --tree-public 还要用户手动跑的命令（[] = 都好了；None = 没要公网，或世界树没装）。builtin：自带的内核直接用世界树，不用另外接。"""
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
    record("dir", TREE_HOME)
    changed = tree_to_vault(exe, vault, lang) if vault else False
    todo = None
    if public:
        todo, added = tree_public(exe)
        changed = changed or added
    if openclaw:
        r = run([str(exe), "install-openclaw", "--openclaw-home", str(home)])
        if r.returncode == 0:  # 只要第一行：它第二行叫你重启 Gateway，openclaw.json 改了的话安装器最后自己重启
            say(tag + (r.stdout.strip().splitlines() or [""])[0].strip())
        else:
            say(L("世界树 install-openclaw 失败：", "Memory tree: install-openclaw failed: ") + (r.stderr or r.stdout).strip()[-300:])
    elif builtin:  # 自带的内核：tree 工具、记忆检索直接用这棵树（engine/recall_tree.py），不用另外接
        say(tag + L("自带的内核直接用这棵树（tree 工具、记忆检索），不用另外接；想放进 Markdown 文件夹（Obsidian 库），趁还没有记忆时换："
                    "`~/.openmousse/venv/bin/python ~/.openmousse/repo/server/setup_ctl.py memory markdown <文件夹>`",
                    "the built-in engine uses this tree directly (the tree tool, memory search), nothing else to connect; to keep it in a Markdown "
                    "folder (an Obsidian vault) instead, switch while it's still empty: "
                    "`~/.openmousse/venv/bin/python ~/.openmousse/repo/server/setup_ctl.py memory markdown <folder>`"))
    else:  # 别的 claw：它能连远程 MCP 的话，自己接上世界树（和 Claude.ai、ChatGPT 一样一个平台一个令牌）
        say(tag + L("你的 claw 能连 MCP 服务的话，让它也接上世界树：`~/.openmousse/venv/bin/mousse-tree rotate myclaw` 给它一个令牌，"
                    "地址见 `mousse-tree urls` 里 myclaw 那一行（本机用 http://127.0.0.1 开头的就行）",
                    "if your claw can connect to MCP servers, connect it to the memory tree too: `~/.openmousse/venv/bin/mousse-tree rotate myclaw` "
                    "gives it a token, and its address is the myclaw line of `mousse-tree urls` (the http://127.0.0.1 one is fine locally)"))
    if not no_systemd and has_systemd():
        # install-service 只 enable --now：已经在跑的服务（再跑一遍安装器）不会换上 pip 刚装的代码，也不读新配置，所以在跑的就重启
        running = run(["systemctl", "--user", "is-active", "mousse-tree.service"]).stdout.strip() == "active"
        r = run([str(exe), "install-service"])
        say(tag + ((r.stdout or r.stderr).strip().splitlines() or [L("install-service 没输出", "install-service printed nothing")])[-1])
        if r.returncode == 0:
            record("service", Path("~/.config/systemd/user/mousse-tree.service"), manager="systemd", name="mousse-tree.service")
        if running:
            r = run(["systemctl", "--user", "restart", "mousse-tree.service"])
            say(tag + (L("服务已重启（换上这次的代码和配置）", "restarted the service (picks up this run's code and config)") if r.returncode == 0 else
                       L("重启服务失败 ", "restarting the service failed ") + (r.stderr or r.stdout).strip()[-200:]))
    elif not no_systemd and has_launchd():
        # macOS：install-service 写 LaunchAgent 并交给 launchd；已经在跑的它会 kickstart -k，换上这次的代码和配置
        r = run([str(exe), "install-service"])
        say(tag + ((r.stdout or r.stderr).strip().splitlines() or [L("install-service 没输出", "install-service printed nothing")])[-1])
        if r.returncode == 0:
            record("service", LAUNCH_AGENTS / f"{LABELS['mousse-tree']}.plist", manager="launchd", name=LABELS["mousse-tree"])
    elif not no_systemd:
        say(tag + L("这台机器没有 systemd 也没有 launchd：世界树的服务要自己起（命令在最后）",
                    "no systemd or launchd on this machine: start the memory tree service yourself (the command is at the end)"))
    elif changed:
        say(tag + L(f"配置改了，重启世界树服务生效（{restart_cmd('mousse-tree')}）",
                    f"its config changed; restart the memory tree service to apply it ({restart_cmd('mousse-tree')})"))
    return todo


# —— 6：开机自启（systemd / launchd） ——

def install_services(repo: Path, venv: Path, tz: str) -> bool:
    """→ 服务起来了没有（没有 = 最后给自己起的命令）。"""
    if has_systemd():
        return install_systemd(repo, venv, tz)
    if has_launchd():
        return install_launchd(repo, venv, tz)
    return False


def print_manual_start(tree: bool) -> None:
    """服务没交给 systemd / launchd（--no-systemd、两样都没有、没起来）：自己起的命令，一个服务一行（先停掉在跑的，换上新代码和配置）。"""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    print()
    print(L("服务要自己起（没交给 systemd / launchd）：每行先停掉在跑的那个，再放到后台起一个新的；机器重启以后、再跑安装器以后都再跑一遍：",
            "Start the services yourself (they aren't under systemd / launchd): each line stops the running one, then starts a new one in the "
            "background; run them again after a reboot and after rerunning the installer:"))
    for name in ("openmousse-server", *(("mousse-tree",) if tree else ())):
        print(f"  {stop_cmd(name)}; {start_cmd(name)}")
    print(L("  每晚的日结：crontab -e 加一行（按这台机器的时钟；没有 crontab 命令的先装 cron）：",
            "  The nightly digest: add this line with crontab -e (it runs on this machine's clock; install cron first if there's no crontab command):"))
    print("  45 3 * * * ~/.openmousse/venv/bin/python ~/.openmousse/repo/packs/core/scripts/daily_close.py")


def local_tz() -> str:
    """这台机器自己的时区名（/etc/localtime 指向的 zoneinfo），拿不到就空。"""
    with contextlib.suppress(OSError):
        target = os.path.realpath("/etc/localtime")
        if "zoneinfo/" in target:
            return target.split("zoneinfo/", 1)[1]
    return ""


def install_launchd(repo: Path, venv: Path, tz: str) -> bool:
    """macOS：服务常驻（登录后起、挂了拉起来），日结按本机时钟每天 03:45 跑一次。日志在 ~/.openmousse/logs。→ 服务起来了没有。"""
    py = venv / "bin/python"
    jobs = ((LABELS["openmousse-server"], [py, "run.py"], dict(workdir=repo / "server", log=LOG_DIR / "server.log")),
            (LABELS["openmousse-daily-close"], [py, repo / "packs/core/scripts/daily_close.py"],
             dict(workdir=repo, log=LOG_DIR / "daily-close.log", calendar={"Hour": 3, "Minute": 45}, restart=False)))
    started = False
    for label, args, kw in jobs:
        ok, err = launch_agent(label, args, **kw)
        say(L(f"{label}：{'已启动' if ok else '启动失败 ' + err}", f"{label}: {'started' if ok else 'failed to start ' + err}"))
        started = started or (ok and label == LABELS["openmousse-server"])
    here = local_tz()
    if tz and here and here != tz:  # launchd 只认本机时钟：时区不一样时，日结的钟点跟着这台 Mac 走
        say(L(f"日结按这台 Mac 的时钟（{here}）每天 03:45 跑；你设的时区是 {tz}",
              f"The daily digest runs at 03:45 on this Mac's clock ({here}); your time zone is set to {tz}"))
    return started


def install_systemd(repo: Path, venv: Path, tz: str) -> bool:
    unit_dir = Path("~/.config/systemd/user").expanduser()
    unit_dir.mkdir(parents=True, exist_ok=True)
    for fn in ("openmousse-server.service", "openmousse-daily-close.service", "openmousse-daily-close.timer"):
        text = (HERE / "systemd" / fn).read_text(encoding="utf8").replace("{repo}", str(repo)).replace("{venv}", str(venv)).replace("{tz}", tz)
        (unit_dir / fn).write_text(text, encoding="utf8")
        record("service", unit_dir / fn, manager="systemd", name=fn)
    run(["systemctl", "--user", "daemon-reload"])
    # 服务用 restart：再跑一遍安装器（更新代码、换监听地址）要重启才生效；定时器 enable --now 就够
    started = False
    for unit, cmd in (("openmousse-server.service", "restart"), ("openmousse-daily-close.timer", "start")):
        run(["systemctl", "--user", "enable", unit])
        r = run(["systemctl", "--user", cmd, unit])
        ok = r.returncode == 0
        say(L(f"{unit}：{'已启动' if ok else '启动失败 ' + (r.stderr or r.stdout).strip()[-200:]}",
              f"{unit}: {'started' if ok else 'failed to start ' + (r.stderr or r.stdout).strip()[-200:]}"))
        started = started or (ok and unit == "openmousse-server.service")
    r = run(["loginctl", "enable-linger", os.environ.get("USER") or ""]) if shutil.which("loginctl") else None
    if r is None or r.returncode != 0:
        say(L("loginctl enable-linger 没成功（登出后服务会停）：用 sudo 跑一次 `loginctl enable-linger $USER`",
              "loginctl enable-linger failed (the services stop when you log out): run `sudo loginctl enable-linger $USER` once"))
    return started


def start_background(repo: Path, venv: Path) -> bool:
    """试装、没装成服务（--no-systemd，或者这台没有 launchd / systemd）：在后台先起一次，装完就能打开（先停掉上次自己起的那个）。
    输出进 ~/.openmousse/logs/server.log。记进安装清单：卸载时照 stop 停掉它。→ 起了没有。"""
    name = "openmousse-server"
    stopped = run(["sh", "-c", stop_cmd(name)]).returncode == 0
    if stop_old_mac_server(repo / "server") or stopped:
        time.sleep(1)  # 等旧的放开端口（同 mousse restart）
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    record("dir", LOG_DIR)
    server = MOUSSE_HOME / "repo/server"  # 全路径、走 ~/.openmousse/repo：stop_cmd 照这个认（见 start_cmd）
    if not (server / "run.py").is_file() or server.resolve() != (repo / "server").resolve():
        server = repo / "server"  # ~/.openmousse/repo 不是指向这个仓库的软链（write_server_json 没动它）：起这个仓库的
    try:
        with open(log_file(name), "ab") as log:
            subprocess.Popen([str(venv / "bin/python"), str(server / "run.py")], cwd=str(server), stdout=log, stderr=log,  # noqa: S603
                             stdin=subprocess.DEVNULL, start_new_session=True)
    except OSError as e:
        say(L(f"服务没起来：{e}", f"The server didn't start: {e}"))
        return False
    record("process", name=name, stop=stop_cmd(name), start=start_cmd(name))
    say(L("服务在后台起了一次（没装成开机自启）", "Started the server once in the background (not as a login service)"))
    return True


def lan_ip() -> str | None:
    """这台机器在局域网里的地址：macOS 看 ipconfig getifaddr en0 / en1，别的看 hostname -I 里的第一个私网地址。"""
    if IS_MAC:
        for ifc in ("en0", "en1"):
            r = run(["ipconfig", "getifaddr", ifc]) if shutil.which("ipconfig") else None
            if r is not None and r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip()
        return None
    r = run(["hostname", "-I"]) if shutil.which("hostname") else None
    ips = r.stdout.split() if r is not None and r.returncode == 0 else []
    for pat in (r"192\.168\.[\d.]+", r"10\.[\d.]+", r"172\.(?:1[6-9]|2\d|3[01])\.[\d.]+"):  # 家里的路由器多半是 192.168；172.17 常是 Docker 的网桥
        if hit := next((x for x in ips if re.fullmatch(pat, x)), None):
            return hit
    return None


def print_trial(cfg: dict, repo: Path, managed: bool, started: bool, tree: bool = False) -> None:
    """试装装完：网页版在哪、服务怎么停和起（tree = 装了世界树：它的服务试装不起，说一句怎么起）、手机怎么连（同一个局域网或 Tailscale）、
    装了什么记在哪。"""
    port = (cfg.get("bind") or {}).get("port") or 8080
    dist = Path(str(cfg.get("dist") or repo / "app" / "dist")).expanduser()
    print(L("试装：只在这台电脑上（127.0.0.1），没开公网，没碰 OpenClaw。", "Trial install: this computer only (127.0.0.1), nothing public, OpenClaw untouched."))
    if dist.is_dir():
        print(L(f"  网页版：在浏览器里打开 http://127.0.0.1:{port}", f"  Web version: open http://127.0.0.1:{port} in a browser"))
    else:
        print(L(f"  网页版还没构建：装了 Node.js 的话 `cd {tilde(repo)}/app && npm ci && npm run web:build`，之后在浏览器里打开 http://127.0.0.1:{port}",
                f"  The web version isn't built yet: with Node.js, `cd {tilde(repo)}/app && npm ci && npm run web:build`, then open "
                f"http://127.0.0.1:{port} in a browser"))
    if not managed and started:
        print(L(f"  服务在后台跑着（没装成开机自启）。停：`{stop_cmd('openmousse-server')}`；电脑重启以后再起：`{start_cmd('openmousse-server')}`",
                f"  The server runs in the background (not as a login service). Stop it: `{stop_cmd('openmousse-server')}`; after a reboot, start it "
                f"again: `{start_cmd('openmousse-server')}`"))
        if tree:
            print(L(f"  世界树的服务没起（给 AI 平台经 MCP 连的；自带的内核直接读树，用不到它）。要起：`{stop_cmd('mousse-tree')}; {start_cmd('mousse-tree')}`",
                    f"  The memory tree service isn't started (it's for AI apps over MCP; the built-in engine reads the tree directly). To start it: "
                    f"`{stop_cmd('mousse-tree')}; {start_cmd('mousse-tree')}`"))
    elif not managed:
        print_manual_start(False)
    ip = lan_ip()
    print(L("  手机怎么连：", "  Connecting your phone:"))
    print(L(f"  · Tailscale（推荐）：这台电脑和手机都装 Tailscale、登同一个账号（{tailscale_install_hint()}），这台上跑 `tailscale serve --bg {port}`，"
            "app 的连接页填它给的 https://<机器>.<tailnet>.ts.net",
            f"  · Tailscale (recommended): install Tailscale on this computer and the phone with the same account ({tailscale_install_hint()}), run "
            f"`tailscale serve --bg {port}` here, and enter the https://<machine>.<tailnet>.ts.net address it gives in the app"))
    print(L(f"  · 同一个 Wi-Fi：试装只听这台电脑。要让局域网里的手机连，不用试装再装一次：`MOUSSE_BIND={ip or '<这台的局域网地址>'} bash {tilde(repo)}/install.sh`"
            "（同一个网里的设备都碰得到这个端口，令牌照样要）",
            f"  · Same Wi-Fi: the trial only listens on this computer. For a phone on your network, install again without the trial: "
            f"`MOUSSE_BIND={ip or '<this computer LAN address>'} bash {tilde(repo)}/install.sh` (every device on the network can reach that port; "
            "it still needs a token)"))
    print(L("  · 配对码（app 连接页用它换一把自己的令牌）：`~/.openmousse/venv/bin/python ~/.openmousse/repo/server/tokens.py pair --server <上面那个地址>`",
            "  · A pairing code (the app trades it for a token of its own): "
            "`~/.openmousse/venv/bin/python ~/.openmousse/repo/server/tokens.py pair --server <that address>`"))
    print(L(f"  装了什么都记在 {tilde(MANIFEST)}（卸载照它删干净）。", f"  Everything this installed is listed in {tilde(MANIFEST)} (uninstalling follows it)."))


def main() -> None:
    global UI_LANG, MANAGED
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
    ap.add_argument("--claw-url", help="another claw / agent's OpenAI-compatible API, up to /v1 (not OpenClaw)")
    ap.add_argument("--claw-preset", help="hermes | nanobot | letta: start from server/claw_presets.py")
    ap.add_argument("--claw-name")
    ap.add_argument("--claw-model")
    ap.add_argument("--claw-skills", help="that claw's skills folder: OpenMousse's skills get linked in")
    ap.add_argument("--claw-rules", help="the rules file that claw reads every turn: OpenMousse's rules get appended")
    ap.add_argument("--builtin", action="store_true", help="OpenMousse's own engine, no OpenClaw: choose models in the app (or setup_ctl.py) afterwards")
    ap.add_argument("--trial", action="store_true", help="a trial install: the built-in engine, this computer only (127.0.0.1), nothing public, "
                    "OpenClaw untouched (also MOUSSE_TRIAL=1)")
    a = ap.parse_args()
    trial = a.trial or os.environ.get("MOUSSE_TRIAL", "").strip().lower() in ("1", "true", "yes", "y")
    if trial:  # 试装（见文件开头）：自带的内核、只听本机、不开公网、不碰 OpenClaw
        a.builtin, a.claw_url, a.bind, a.tree_public = True, None, "127.0.0.1", False
    try:
        saved_lang = load_json(SERVER_JSON).get("language")
    except (OSError, ValueError, AttributeError):
        saved_lang = None
    UI_LANG = a.lang or (norm_lang(saved_lang) if saved_lang else env_lang())
    if trial and SERVER_JSON.exists() and load_manifest().get("mode") != "trial":
        # 已经正式装过的（不是试装）：试装会把它换成自带的内核、只听本机，还会停掉后台的服务。一样不动
        say(L("这台已经装过 OpenMousse（不是试装），没动：试装会把它改成自带的内核、只在这台电脑上用。要重装就去掉 --trial；"
              "想另外试一下，换一个系统用户装",
              "OpenMousse is already installed here (not as a trial), so nothing was changed: a trial would switch it to the built-in "
              "engine on this computer only. To reinstall, drop --trial; to try it separately, install as another system user"))
        sys.exit(2)
    MANAGED = not a.no_systemd and has_services()
    repo, venv = Path(a.repo).expanduser().resolve(), Path(a.venv).expanduser().resolve()
    home = Path(a.openclaw_home).expanduser()
    oc_path = home / "openclaw.json"
    generic = bool(a.claw_url)  # 别的 claw（OpenAI 兼容接口）：不碰 openclaw.json
    builtin = bool(a.builtin) and not generic  # 自带的内核（第 9 步）：也不碰 openclaw.json
    begin_manifest("trial" if trial else "builtin" if builtin else "claw" if generic else "openclaw", repo, venv)
    record("dir", venv)
    if os.environ.get("MOUSSE_CLONED") == "1":  # install.sh 这次 clone 的仓库（自己的检出不算：卸载不删它）
        record("dir", repo, what="clone")
    claw = None
    if builtin:
        claw = {"kind": "builtin"}
        oc = {}
        workspace = MOUSSE_HOME / "workspace"  # 档案 USER.md、记忆页读的 MEMORY.md、主对话的文件工具都在这里
        workspace.mkdir(parents=True, exist_ok=True)
        record("dir", workspace)
        if not (workspace / "USER.md").exists():
            (workspace / "USER.md").write_text(L("# USER.md\n\n## 基本信息\n", "# USER.md\n\n## Basics\n"), encoding="utf8")
    elif generic:
        sys.path.insert(0, str(repo / "server"))
        import claw_presets  # 只用标准库
        claw = {**claw_presets.preset(a.claw_preset or ""), "kind": "openai", "url": a.claw_url.strip().rstrip("/")}
        claw["name"] = (a.claw_name or "").strip() or claw.get("name") or "My claw"
        if a.claw_model is not None:  # 没给就用预设的（nanobot 的 "" = 请求里不带 model）；都没有 = default
            claw["model"] = a.claw_model.strip() or "default"
        claw.setdefault("model", "default")
        claw["token"] = os.environ.get("MOUSSE_CLAW_TOKEN", "").strip()
        for k, v in (("skills", a.claw_skills), ("rules", a.claw_rules)):
            if v:
                claw[k] = str(Path(v).expanduser())
            elif claw.get(k):
                claw[k] = str(Path(claw[k]).expanduser())
        if claw.get("token_env") and not claw["token"]:
            claw.pop("token")  # 令牌按 token_env 从环境 / 它自己的 .env 读，server.json 里不存
        oc = {}
        workspace = MOUSSE_HOME / "workspace"  # OpenMousse 自己的：档案 USER.md、记忆页读的 MEMORY.md
        workspace.mkdir(parents=True, exist_ok=True)
        record("dir", workspace)
        if not (workspace / "USER.md").exists():
            (workspace / "USER.md").write_text(L("# USER.md\n\n## 基本信息\n", "# USER.md\n\n## Basics\n"), encoding="utf8")
    else:
        if not oc_path.exists():
            sys.exit(L(f"没找到 {oc_path}。先装好 OpenClaw、配好模型（openclaw onboard），再跑这个；用别的 claw 就给 --claw-url。",
                       f"{oc_path} not found. Install OpenClaw and set up a model first (openclaw onboard), then run this again; for another claw, pass --claw-url."))
        oc = load_json(oc_path)
        workspace = main_workspace(oc, home)
        workspace.mkdir(parents=True, exist_ok=True)
    print(L("配置", "Config"))
    vault = vault_dir(a.vault)
    cfg, new_tokens = write_server_json(a, oc, home, workspace, repo, vault, claw)
    record("dir", Path(str(cfg["data_dir"])).expanduser())
    print("skills")
    restart = False
    if builtin:
        say(L("自带的内核直接读 packs/core/skills、自己备每一轮的规矩，不用链 skill、不用改规则文件",
              "The built-in engine reads packs/core/skills directly and writes its own rules: no skill links, no rules file to change"))
    elif generic:
        install_skills(Path(claw["skills"]) if claw.get("skills") else None, Path(claw["rules"]) if claw.get("rules") else None, repo, home,
                       GENERIC_SKILLS, (GENERIC_RULES_ZH, GENERIC_RULES_EN))
    else:
        link_skills(workspace, repo, home)
        adopt_fresh_workspace(workspace, cfg["app_name"])
        print("openclaw.json")
        restart = patch_openclaw(oc_path, home, cfg.get("openclaw_bin") or "openclaw")
        restart = allow_skill_links(oc_path, repo / "packs/core/skills", home, cfg.get("openclaw_bin") or "openclaw") or restart
        add_mcp_server(oc_path, mcp_url(cfg), home, cfg.get("openclaw_bin") or "openclaw")
        # 好友默认就能加（不开公网也经 OpenMousse 中继，2026-10-05）：名片 agent 走 llm-task
        restart = enable_llm_task(oc_path, home, cfg.get("openclaw_bin") or "openclaw") or restart
    public = None
    if not a.no_tree:
        print(L("世界树", "Memory tree"))
        uses_openclaw = not generic and not builtin
        oc_before = oc_path.read_bytes() if uses_openclaw and oc_path.exists() else b""
        public = setup_tree(venv, cfg, home, a.no_systemd, vault, a.tree_public, openclaw=uses_openclaw, builtin=builtin)
        if uses_openclaw and oc_path.exists() and oc_path.read_bytes() != oc_before:
            restart = True  # 世界树往 extraPaths 里加了导出的文件夹
    social = None
    print(L("分享和好友", "Sharing and friends"))
    social_defaults()  # 好友的推送档位：不管开不开公网都要（好友默认经中继能加）
    if a.tree_public:  # 同一问：分享链接和好友直连（server.json 要在下面重启服务之前写好，小服务是服务启动时起的）
        social = share_public()
    else:
        say(L("好友经 OpenMousse 中继（relay.openmousse.ai）找到这台服务器，不需要公网；消息端到端加密。server.json 写 \"relay\": false 可关",
              "Friends reach this server through the OpenMousse relay (relay.openmousse.ai), no public address needed; messages are end-to-end encrypted. Set \"relay\": false in server.json to turn it off"))
    restarted = False
    self_apply = restart and gateway_applies_config(oc_path)
    started = False
    if MANAGED:
        print("launchd" if has_launchd() and not has_systemd() else "systemd")
        MANAGED = install_services(repo, venv, cfg["timezone"])  # 服务没起来：最后给自己起的命令
        if MANAGED:
            wait_server(cfg)
    if trial and not MANAGED:  # 试装不装服务：先在后台起一次，装完就能打开
        started = start_background(repo, venv)
        if started:
            wait_server(cfg)
    if builtin and not trial and offer_migration(venv, repo, home):  # 搬过来写了：工作区、模型可能换了，服务重启一下才用上
        if MANAGED:
            run(["sh", "-c", restart_cmd("openmousse-server")])
            wait_server(cfg)
        else:
            say(L(f"重启服务让搬过来的设置生效：{restart_cmd('openmousse-server')}", f"Restart the server so the moved settings apply: {restart_cmd('openmousse-server')}"))
    elif trial and found_homes(home):
        say(L("试装不碰 OpenClaw、Hermes。想把它们的设置搬过来，正式装以后跑 `~/.openmousse/repo/bin/mousse migrate openclaw`（或 hermes）",
              "A trial leaves OpenClaw and Hermes alone. To move their settings over, install for real and run "
              "`~/.openmousse/repo/bin/mousse migrate openclaw` (or hermes)"))
    if self_apply:
        say(L("openclaw.json 改了，Gateway 自己会套用：能热加载的马上生效，要重启的它等手头的对话做完自己重启",
              "openclaw.json changed; the Gateway applies it by itself: hot where it can, otherwise it restarts on its own once the turns in progress finish"))
    elif restart and not a.no_systemd and has_systemd() and run(["systemctl", "--user", "is-active", "openclaw-gateway"]).stdout.strip() == "active":
        run(["systemctl", "--user", "restart", "--no-block", "openclaw-gateway"])  # 不等：原因见 gateway_applies_config
        restarted = True
        say(L("openclaw-gateway 在重启（配置改了；手头的对话做完才重启）", "Restarting openclaw-gateway (its config changed; it finishes the turns in progress first)"))
    elif restart and not a.no_systemd and has_launchd() and shutil.which(cfg.get("openclaw_bin") or "openclaw"):
        # macOS 上 Gateway 是 OpenClaw 自己装的 LaunchAgent：让 openclaw 去重启，放到后台不等（同 --no-block，原因见 gateway_applies_config）
        subprocess.Popen([cfg.get("openclaw_bin") or "openclaw", "gateway", "restart"], env=openclaw_env(home), stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        restarted = True
        say(L("Gateway 在重启（配置改了；手头的对话做完才重启）", "Restarting the Gateway (its config changed; it finishes the turns in progress first)"))
    elif restart:
        say(L("openclaw.json 改了，重启你的 Gateway 生效", "openclaw.json changed: restart your Gateway to apply it"))
    gw_line = ""
    if not generic and not builtin:  # app 的对话走 Gateway：这里就试一下连不连得上，别等手机上才发现
        sys.path.insert(0, str(repo / "server"))
        from config import gateway_default  # 只用标准库；和服务端同一个算法（server.json 没写 gateway 就按 openclaw.json 的端口）
        gw = str(cfg.get("gateway") or gateway_default(oc_path))
        if restarted:
            say(L("等 Gateway 重启好（最多一分钟）…", "Waiting for the Gateway to come back (up to a minute)…"))
        ok, detail = probe_gateway(gw, oc_path, wait=60 if restarted or self_apply else 5)
        if not ok and (restarted or self_apply):  # 可能正在套用刚才的改动、自己重启：别让人（或 claw）去折腾它
            gw_line = L(f"OpenClaw Gateway：{gw}，现在连不上（{detail}）：它可能正在套用刚才的改动、自己重启，过一两分钟跑 check.sh 再看",
                        f"OpenClaw Gateway: {gw}, not reachable right now ({detail}): it may be applying the change and restarting itself; run check.sh again in a minute or two")
        else:
            gw_line = L(f"OpenClaw Gateway：{gw}" + ("，连得上 ✓" if ok else f"，现在连不上 ✗（{detail}）：`openclaw gateway status` 看它在不在跑；"
                                                            "它不在这个地址的话，server.json 里写 \"gateway\": \"http://127.0.0.1:<端口>\" 再重启服务"),
                        f"OpenClaw Gateway: {gw}" + (", reachable ✓" if ok else f", not reachable right now ✗ ({detail}): check `openclaw gateway status`; "
                                                            "if it listens somewhere else, set \"gateway\": \"http://127.0.0.1:<port>\" in server.json and restart the server"))

    b = cfg["bind"]
    url = f"http://{b['host']}:{b['port']}"
    print()
    print("=" * 60)
    print(L(f"装好了。服务地址：{url}", f"Done. Server: {url}"))
    vm = re.search(r'^VERSION = "([^"]+)"', (repo / "server/version.py").read_text(encoding="utf8"), re.M) if (repo / "server/version.py").exists() else None
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "--short=7", "HEAD"], capture_output=True, text=True).stdout.strip()
    if vm:
        print(f"OpenMousse {vm.group(1)}" + (L(f"（{head}）", f" ({head})") if head else ""))
    no_model = False
    if builtin:
        no_model = builtin_status(venv, repo)
    elif generic:
        ok, detail = probe_claw(cfg.get("claw") or {})
        print(L(f"你的 claw：{cfg['claw'].get('name')}（{cfg['claw'].get('url')}）" + ("，接口连得上 ✓" if ok else f"，接口现在连不上 ✗（{detail}）：看它在不在跑、地址和令牌对不对，改 server.json 的 claw 段不用重启"),
                f"Your claw: {cfg['claw'].get('name')} ({cfg['claw'].get('url')})" + (", API reachable ✓" if ok else f", API not reachable right now ✗ ({detail}): check it's running and the URL / token are right; editing the claw section of server.json needs no restart")))
        if mcp_url(cfg):
            print_mcp_hint(mcp_url(cfg), a.claw_preset or "")
    else:
        print(gw_line)
    if "phone" in new_tokens and os.environ.get("MOUSSE_NONINTERACTIVE"):  # claw 替人装：输出会进它的上下文，令牌不打出来；手机用配对码连
        print(L("手机令牌已生成，存在 ~/.openmousse/server.json（用配对码连就用不到它，别发到聊天里）",
                "Phone token created and kept in ~/.openmousse/server.json (pairing doesn't need it; don't send it to the chat)"))
    elif "phone" in new_tokens:
        print(L(f"手机令牌，只显示这一次：{new_tokens['phone']}", f"Phone token, shown only this once: {new_tokens['phone']}"))
    else:
        print(L("手机令牌之前已生成；要新的：`~/.openmousse/venv/bin/python ~/.openmousse/repo/server/tokens.py add phone2`",
                "The phone token was generated earlier; for a new one: `~/.openmousse/venv/bin/python ~/.openmousse/repo/server/tokens.py add phone2`"))
    if not MANAGED and not trial:
        print_manual_start(not a.no_tree and (venv / "bin/mousse-tree").exists())
    print()
    if trial:
        print_trial(cfg, repo, MANAGED, started, tree=not a.no_tree and (venv / "bin/mousse-tree").exists())
    else:
        print(L("手机怎么连：", "Connecting the phone:"))
    tok_zh, tok_en = ("上面的令牌", "the token above") if "phone" in new_tokens and not os.environ.get("MOUSSE_NONINTERACTIVE") else ("手机令牌", "your phone token")
    if trial:
        pass
    elif b["host"].startswith("100."):
        print(L("  这台机器在 Tailscale 里：手机也装 Tailscale、登同一个账号，装好 OpenMousse app，再用手机相机扫下面的码（或者点码下面的链接）：",
                "  This machine is on Tailscale: install Tailscale on the phone (same account) and the OpenMousse app, then scan the code below with the phone's camera (or tap the link under it):"))
        r = run([str(venv / "bin/python"), str(repo / "server/tokens.py"), "pair", "--name", "phone", "--minutes", "30"], cwd=str(repo / "server"))
        if r.returncode == 0 and r.stdout.strip():
            print(r.stdout.rstrip())
        print(L(f"  扫不了的话，app 连接页手填 {url} 和{tok_zh}。", f"  If scanning doesn't work, enter {url} and {tok_en} on the app's connect page."))
    elif b["host"] in ("127.0.0.1", "localhost"):
        print(L("  现在只监听本机。装 Tailscale（`curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up`）后再跑一遍安装器，",
                "  Listening on this machine only. Install Tailscale (`curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up`) and run the installer again,"))
        print(L(f"  或者用 `tailscale serve` / Caddy / nginx 把 127.0.0.1:{b['port']} 反代成 HTTPS，app 里填那个地址。",
                f"  or reverse-proxy 127.0.0.1:{b['port']} as HTTPS with `tailscale serve` / Caddy / nginx and enter that address in the app."))
        print(L("  配对码（app 连接页用它换自己的令牌，不用抄手机令牌）：`~/.openmousse/venv/bin/python ~/.openmousse/repo/server/tokens.py pair --server <那个地址>`",
                "  A pairing code (the app trades it for a token of its own, no token to copy): "
                "`~/.openmousse/venv/bin/python ~/.openmousse/repo/server/tokens.py pair --server <that address>`"))
    else:
        print(L(f"  app 连接页填 {url} 和令牌", f"  Enter {url} and the token in the app"))
    print(L("  app：iPhone 用 OpenMousse 的 TestFlight，安卓下载 APK（https://github.com/openmousse/openmousse/releases），或自己构建 app/（见 app/README.zh-CN.md）",
            "  The app: OpenMousse on TestFlight for iPhone, the APK for Android (https://github.com/openmousse/openmousse/releases), or build app/ yourself (see app/README.md)."))
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
    if social is not None:
        print()
        print(L("分享和朋友：", "Sharing and friends:"))
        todo, purl = social
        if todo:
            print(L("  还没弄完，在服务器上跑：", "  Not done yet; on the server run:"))
            for x in todo:
                print(f"    {x}")
        if purl:
            print(L(f"  对外地址 {purl}（只有 /s 分享页和 /f 朋友）。加朋友：app「对话」顶上切到「朋友」→ 加朋友，把邀请码发给对方",
                    f"  Public address {purl} (only /s for shares and /f for friends). To add a friend: in the app, Chat → Friends → Add a friend, and send them the invite"))
    print()
    print(L("检查：", "Check:"))
    print(f"  curl -H 'Authorization: Bearer <token>' {url}/api/health")
    follow = logs_cmd("openmousse-server").replace("tail -n 50", "tail -f").replace(" -n 50", " -f")
    if MANAGED:
        print(L(f"  {status_cmd('openmousse-server')}   # 日志：{follow}", f"  {status_cmd('openmousse-server')}   # logs: {follow}"))
    else:
        print(L(f"  {follow}   # 服务的日志", f"  {follow}   # the server's log"))
    if public is None:
        exposed = bool(tree_config().get("public_hosts"))
        print(L("  ~/.openmousse/venv/bin/mousse-tree urls      # 世界树接各平台的地址" + ("" if exposed else "（先开公网：再跑一遍安装器，「开公网」答 y）"),
                "  ~/.openmousse/venv/bin/mousse-tree urls      # memory tree URLs for AI platforms"
                + ("" if exposed else " (open it up first: run the installer again and answer y to going public)")))
    if (repo / "check.sh").exists():
        print(L(f"  bash {repo}/check.sh   # 自检：每一块通不通、怎么修；输出里没有令牌，可以整段发给帮你的人",
                f"  bash {repo}/check.sh   # self-check: what works, what doesn't and how to fix it; no tokens in the output, so you can send all of it to whoever helps"))
    print(L("再跑一遍安装器是安全的，只补缺的。", "Rerunning the installer is safe; it only fills in what is missing."))
    if builtin:
        offer_wizard(venv, repo, no_model, running=MANAGED or started)


# —— 自带的内核（第 9 步）：模型、搜索、记忆装完以后选 ——

def setup_ctl(venv: Path, repo: Path, *args: str, capture: bool = True) -> subprocess.CompletedProcess:
    """跑 server/setup_ctl.py（三步引导 / status）。用这个 venv 的 python：它要 server 的依赖。"""
    cmd = [str(venv / "bin/python"), str(repo / "server/setup_ctl.py"), *args]
    if capture:
        return run(cmd, cwd=str(repo / "server"))
    try:
        return subprocess.run(cmd, cwd=str(repo / "server"), check=False)  # noqa: S603 — 问答要接着这个终端
    except KeyboardInterrupt:  # Ctrl-C 停三步：setup_ctl 自己说了「停了」，这边别再吐一屏 traceback（10/22 Mac 上试装看到的）
        return subprocess.CompletedProcess(cmd, 130)


def builtin_status(venv: Path, repo: Path) -> bool:
    """装完的小结里那几行：每一层现在怎么样（setup_ctl.py status）。→ 是不是还没有能用的模型（它的退出码 3）。"""
    r = setup_ctl(venv, repo, "status")
    lines = [x for x in (r.stdout or "").splitlines() if x.strip()]
    print(L("自带的内核（不用 OpenClaw）：", "The built-in engine (no OpenClaw):"))
    for x in lines:
        print(x)
    if r.returncode not in (0, 3) and not lines:
        print("  " + (r.stderr or "").strip()[-300:])
    return r.returncode == 3


def tty_yes(prompt: str, default: bool = True) -> bool:
    """有终端、不是替人装（MOUSSE_NONINTERACTIVE）才问；管道里跑（curl | bash）从 /dev/tty 读。回车 = default（默认是）。"""
    if os.environ.get("MOUSSE_NONINTERACTIVE"):
        return False
    try:
        with open("/dev/tty", encoding="utf8") as t:
            try:  # 先清掉装依赖时按下的键，不然那几下回车就把这一题答了（同安装器）
                import termios
                termios.tcflush(t, termios.TCIFLUSH)
            except Exception:  # Windows 没有 termios
                pass
            print(f"{prompt} {'[Y/n]' if default else '[y/N]'}: ", end="", flush=True)
            ans = t.readline().strip().lower()
    except OSError:
        return False
    return ans in ("y", "yes", "是", "1") or (default and ans == "")


def found_homes(home: Path) -> list[tuple[str, Path]]:
    """这台机器上能搬的：OpenClaw（--openclaw-home 下有 openclaw.json）、Hermes（$HERMES_HOME 或 ~/.hermes 下有 config.yaml）。"""
    hermes = Path(os.environ.get("HERMES_HOME") or "~/.hermes").expanduser()
    return [(src, h) for src, h, f in (("openclaw", home, "openclaw.json"), ("hermes", hermes, "config.yaml")) if (h / f).is_file()]


def offer_migration(venv: Path, repo: Path, home: Path) -> bool:
    """装自带内核（不是试装）时，这台机器上有 OpenClaw / Hermes：先问要不要搬（见文件开头）。一家一家来：先只看报告（mousse migrate
    <家>，什么都不写），用户说要才 --apply。替人装（MOUSSE_NONINTERACTIVE）只打印怎么跑。→ 真的写了没有（写了要重启服务：工作区换了）。"""
    homes = found_homes(home)
    if not homes:
        return False
    names = {"openclaw": "OpenClaw", "hermes": "Hermes"}
    mousse = [str(venv / "bin/python"), str(repo / "server/mousse.py"), "migrate"]
    applied = False
    print()
    print(L("搬过来", "Moving over"))
    for src, h in homes:
        flag = [f"--{src}-home", str(h)]
        cmd = f"~/.openmousse/repo/bin/mousse migrate {src} --{src}-home {tilde(h)}"
        if os.environ.get("MOUSSE_NONINTERACTIVE"):
            say(L(f"这台机器上有 {names[src]}（{tilde(h)}）。要把它的设置搬过来：`{cmd}` 先看报告（什么都不写），看过再加 --apply",
                  f"{names[src]} is on this machine ({tilde(h)}). To move its settings over: `{cmd}` shows a report first (nothing is written); add "
                  "--apply once you've read it"))
            continue
        if not tty_yes(L(f"这台机器上有 {names[src]}（{tilde(h)}）。要不要把它的模型、定时任务、记忆这些搬过来？先只看一份报告，什么都不写",
                         f"{names[src]} is on this machine ({tilde(h)}). Move its models, scheduled jobs, memory and the rest over? First you only see a "
                         "report; nothing is written")):
            say(L(f"不搬。以后想搬：`{cmd}`", f"Not moving anything. To do it later: `{cmd}`"))
            continue
        r = subprocess.run([*mousse, src, *flag], cwd=str(repo / "server"), check=False)  # noqa: S603 — 报告直接打到终端
        if r.returncode != 0:
            say(L(f"报告没出来（退出码 {r.returncode}）：以后自己跑 `{cmd}`", f"The report didn't come out (exit code {r.returncode}): run `{cmd}` later"))
            continue
        if not tty_yes(L(f"照这份报告写进来吗？先备份 server.json 和数据库，{names[src]} 那边一个字不改", f"Write it as the report says? server.json and the "
                         f"database are backed up first, and nothing changes on {names[src]}'s side"), default=False):
            say(L(f"没写。想写的时候：`{cmd} --apply`", f"Nothing written. When you want it: `{cmd} --apply`"))
            continue
        r = subprocess.run([*mousse, src, *flag, "--apply"], cwd=str(repo / "server"), check=False)  # noqa: S603
        if r.returncode == 0:
            applied = True
            record("edited", SERVER_JSON, note=f"mousse migrate {src} --apply")
        else:
            say(L(f"没写完（退出码 {r.returncode}）：上面写着做了什么、备份在哪", f"Not finished (exit code {r.returncode}): what was done and where the backups are is above"))
    return applied


def offer_wizard(venv: Path, repo: Path, no_model: bool, running: bool = True) -> None:
    """装完了：模型还没选的，问一句要不要现在就在终端里走三步；不走就给那条命令。选过的只说以后怎么改。
    running = 服务交给 systemd / launchd 起来了；没有的话，走完三步再提醒一句把服务起起来（那几行早滚上去了）。"""
    cmd = "~/.openmousse/venv/bin/python ~/.openmousse/repo/server/setup_ctl.py"
    print()
    if not no_model:
        print(L(f"模型、搜索、记忆随时能换（加 status 看现在的样子）：{cmd}", f"Models, web search and memory can be changed any time (add status to see where things stand): {cmd}"))
        return
    if tty_yes(L("现在就在终端里选模型、搜索和记忆吗？", "Choose the model, web search and memory in this terminal now?")):
        setup_ctl(venv, repo, capture=False)
        if not running:
            print()
            print(L("下一步：用上面「服务要自己起」那几行把服务起起来，再连手机。", "Next: start the services with the lines under \"Start the services yourself\" above, then connect the phone."))
        return
    print(L("还没选模型：在服务器上跑这一条，三步走完（模型 → 搜网页 → 记忆），选好了不用重启服务：",
            "No model chosen yet: run this on the server for the three steps (model → web search → memory); no restart needed afterwards:"))
    print(f"  {cmd}")


if __name__ == "__main__":
    main()
