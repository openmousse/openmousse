"""命令行工具的桥（2026-10-16 从 mcp_bridge.py 拆出来）：skills 里那些 *_ctl.py 脚本怎么起一个子进程跑一次。

两处用它：/mcp（mcp_bridge.py，给外面的 claw）和自己的内核（engine/tools.py，builtin 的主人回合）。只用标准库，
不依赖 mcp 包：没装 mcp 的老安装照样能让内核用这些工具。10/19 起还有只跑命令的定时任务（engine/jobs.py）：BRIDGES 里的工具和
server.json 的 engine.jobs.scripts 白名单里的脚本都经 execute 起（参数照 guard 查，时限是任务自己写的，不占上面那 4 个：定时任务
自己有同时跑几条的上限，几分钟长的命令不该挤着 Agent 调工具）。

一次调用 = 起一个子进程：参数一项一个词、不经 shell；本来要从标准输入给的放 stdin；在调用的 Agent 的工作区里跑，
是谁另外经环境变量 MOUSSE_AGENT 告诉脚本；单次最长 120 秒（handoff 300 秒）、输出最多 30000 字、同时最多 4 个（两处共用这 4 个）。
出错一律 BridgeError（一句给模型看的原因，按 server.json 的语言）。
tree 工具跟着 sources.py 选中的那一份世界树走（tree_command）：选中 workspace 的 scripts/memory_tree.py 就跑那个脚本（和 think.py 一样
用 python3），选中开源版就跑同一个 venv 里的 mousse-tree；都没接就不给这个工具。
跑完以后服务端还要接着做的挂在 AFTER（treeprune.py 注册 tree：交了每周修剪的计划就出一张收件箱卡）。
经谁跑的（VIA，调用方设）：/mcp 设 mcp，内核的工具设那一轮的来路；经环境变量 MOUSSE_VIA 交给脚本，脚本请求服务器时带 X-Mousse-Via，
服务器开的回合（转交、触发）记下它，自己的内核据此不让 /mcp 和后台引出来的回合花 ChatGPT 套餐（engine/models.may_spend）。
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import signal
import sqlite3
import sys
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from pathlib import Path

from config import raw, settings
from i18n import L

SERVER = Path(__file__).resolve().parent
REPO = SERVER.parent
MAX_OUT = 30000        # 回给模型的输出最多多少字（多了掐中间）
MAX_ARGS = 200000      # args + input 一共最多多少字
TIMEOUT, SLOW = 120, 300  # SLOW：handoff 等另一个 Agent 回话（ask_agent.py 默认等 150 秒）
PARALLEL = asyncio.Semaphore(4)
VIA: ContextVar[str | None] = ContextVar("mousse_bridge_via", default=None)  # 见文件开头：这一次是经谁跑的（mcp / 内核那一轮的来路）

# 工具名 → (脚本, 中文说明, English, 超时)。说明只写它管什么；子命令和写法在对应的 skill 里。
BRIDGES: dict[str, tuple[str, str, str, int]] = {
    "board": ("server/board_ctl.py", "你自己的数据表和看板（board_ctl.py）：show、table、rows、check、apply、propose、view（集合块的视图）、pack、alert、history、revert。写法见 board skill。",
              "Your own data tables and board (board_ctl.py): show, table, rows, check, apply, propose, view (a collection's views), pack, alert, history, revert. See the board skill.", TIMEOUT),
    "inbox": ("server/inbox_ctl.py", "收件箱，要用户点头的事（inbox_ctl.py）：add、update、done、fail、withdraw、list、get。什么时候交、怎么写见 inbox skill。",
              "The inbox, things that need the user's OK (inbox_ctl.py): add, update, done, fail, withdraw, list, get. When and how: the inbox skill.", TIMEOUT),
    "goals": ("server/goals_ctl.py", "用户的长期目标（goals_ctl.py）：list、trend、log、add、update、done、drop、reopen、undo。见 goals skill。",
              "The user's long-term goals (goals_ctl.py): list, trend, log, add, update, done, drop, reopen, undo. See the goals skill.", TIMEOUT),
    "project": ("server/project_ctl.py", "项目（project_ctl.py）：list、show、create、propose、goal、progress、add、done、ask、conclude、archive…。见 project skill。",
                "Projects (project_ctl.py): list, show, create, propose, goal, progress, add, done, ask, conclude, archive… See the project skill.", TIMEOUT),
    "schedule": ("server/schedule_ctl.py", "日程和「要记得的」（schedule_ctl.py）：day、remember、add、update、delete、skip、attend、place、done、undo…。",
                 "The schedule and things to remember (schedule_ctl.py): day, remember, add, update, delete, skip, attend, place, done, undo…", TIMEOUT),
    "study": ("server/study_ctl.py", "学习台的课（study_ctl.py）：courses、show、check、session、reading、ddl、file、assign、syllabus、answer、style、generate、undo、log、create、delete。见 study skill。",
              "Study desk courses (study_ctl.py): courses, show, check, session, reading, ddl, file, assign, syllabus, answer, style, generate, undo, log, create, delete. See the study skill.", TIMEOUT),
    "agents": ("server/agent_ctl.py", "新建 / 改 / 删 Agent（agent_ctl.py）：list、create、update、delete。见 agent-builder skill。",
               "Create, edit or delete Agents (agent_ctl.py): list, create, update, delete. See the agent-builder skill.", TIMEOUT),
    "handoff": ("packs/core/scripts/ask_agent.py", "把属于某个 Agent 的事转给它、等它回话（ask_agent.py）：--list 看有哪些 Agent。见 handoff skill。",
                "Hand something to the Agent it belongs to and wait for its answer (ask_agent.py); --list shows the Agents. See the handoff skill.", SLOW),
    "tasks": ("server/tasks_ctl.py", "派后台任务之前看额度（tasks_ctl.py）：quota、list。见 dispatch skill。",
              "Check the background-task allowance before dispatching (tasks_ctl.py): quota, list. See the dispatch skill.", TIMEOUT),
    "proposals": ("server/proposals_ctl.py", "日结提案（proposals_ctl.py）：context、skill、agent、list、show、withdraw。见 proposals skill。",
                  "Nightly proposals (proposals_ctl.py): context, skill, agent, list, show, withdraw. See the proposals skill.", TIMEOUT),
    "journal": ("packs/core/scripts/journal.py", "用户的日志：感受、想法、决定（journal.py）：add、list、search、delete。见 journal skill。",
                "The user's journal: feelings, thoughts, decisions (journal.py): add, list, search, delete. See the journal skill.", TIMEOUT),
    "diet": ("server/diet_ctl.py", "饮食记录（diet_ctl.py）：day、days、library、lookup、foods、food-save、food-add、food-update、add、log-plan、edit、rm、undo、targets。"
                                    "记录默认只预览，加 --commit 才写。用法：board 工具 pack guide diet。",
             "The meal log (diet_ctl.py): day, days, library, lookup, foods, food-save, food-add, food-update, add, log-plan, edit, rm, undo, targets. "
             "Logging previews unless you add --commit. How to use it: the board tool's pack guide diet.", TIMEOUT),
    "settings": ("server/settings_ctl.py", "助手的设置，比如用户的称呼（settings_ctl.py）：user-name。",
                 "Assistant settings such as what to call the user (settings_ctl.py): user-name.", TIMEOUT),
    "tree": ("mousse-tree", "世界树，用户跨平台共享的记忆（mousse-tree 命令行）：profile（档案、规则和枝）、recall、recent、add、forget、stats、"
                            "branches；每周修剪的 prune-context、prune-plan。见 memory-tree skill、tree-prune skill。",
             "The memory tree, the user's memory shared across AI platforms (the mousse-tree CLI): profile (the profile, the rules and the branches), "
             "recall, recent, add, forget, stats, branches; weekly pruning's prune-context and prune-plan. See the memory-tree and tree-prune skills.",
             TIMEOUT),
    "page": ("server/page_ctl.py", "页面（page_ctl.py）：积木画不了的互动和可视化，写一份网页，app 放进盒子里显示：check、publish、list、show、get、data、rm。"
                                   "HTML 放 input、args 里写 --file -，要读的数据用 --data 名字=查询。见 page skill。",
             "Pages (page_ctl.py): interactive views the board blocks can't draw; write one web page and the app shows it in a sandbox: check, publish, "
             "list, show, get, data, rm. Put the HTML in input with --file - in args, and the data it reads as --data NAME=query. See the page skill.", TIMEOUT),
}


# 读服务器上文件的参数：经桥只许写 -（内容放 input）。沙箱里的 Agent、云上的 claw 本来碰不到这台机器的文件，
# 不能借 --detail-file /某个文件 把它塞进收件箱卡再读回去。argparse 认缩写（--detail-f、--pla），所以前缀也算，
# 除了本身就是别的参数的这几个（--brief、--detail、--field 是正经参数，不读文件）。只在某个工具里是正经参数的按工具放（NOT_FILE_IN：
# diet 的 --item、page 的 --data）：别的工具里它们还是文件参数的缩写（inbox update 的 --item = --items-file），照样挡。
# test_bridges_guard 对着各脚本的 add_argument 查，再有撞上的会挂。
FILE_FLAGS = ("--file", "--detail-file", "--board-file", "--brief-file", "--fields-file", "--plan", "--data-file", "--items-file")
NOT_FILE = {"--brief", "--detail", "--field"}
NOT_FILE_IN = {"diet": {"--item"}, "page": {"--data"}}
# 世界树只给记忆本身的操作、每周修剪的看材料、交计划，和读手册（13c，只给自己的内核：claw 是 builtin；工具说明里那一句在 engine/tools.py）；
# init、migrate、urls（会打印令牌）不给，prune-apply（照计划改树）只在主人点了卡以后由服务端跑。手册是做法（engine/memgate.py：先提议）：
# 经桥一律只读；内核里改手册走 manual_manage（engine/manual_write.py，交卡、点了头服务端照卡改），用户自己在命令行、Obsidian 里改的照旧
TREE_OK = {"add", "recall", "recent", "forget", "stats", "profile", "branches", "prune-context", "prune-plan", "manual"}
TREE_MANUAL_OK = {"list", "show", "find", "pointers", "history"}
# 跑完以后服务端接着做的：工具名 → fn(args, 输出, Agent) → 给模型看的输出。treeprune.py 注册 tree（交了修剪计划就出卡）
AFTER: dict[str, Callable[[list[str], str, str | None], Awaitable[str]]] = {}
# 这一次调用是在哪个对话里、答的哪条用户消息：(对话, 消息 id)。内核的 tree 工具调之前设上（engine/tools.py），世界树的叶子据此记下出处
SAID: ContextVar[tuple[str, str] | None] = ContextVar("mousse_bridge_said", default=None)
# 内核的 handoff 是从哪个对话转出去的（第 1 批 b，engine/sessions.py）：经 MOUSSE_FROM 交给 ask_agent.py，它带进 /api/chat/relay，
# 服务器据此记转交卡、数跳数、给对方标「别的会话转来」。/mcp 不设（OpenClaw 那条路照旧）
FROM: ContextVar[str | None] = ContextVar("mousse_bridge_from", default=None)


class BridgeError(Exception):
    """调不成：没有这个工具、参数不许、超时、脚本退出码不是 0。str(e) 就是给模型看的原因。"""


def guard(name: str, args: list[str]) -> None:
    import claw
    ok = TREE_OK if claw.is_builtin() else TREE_OK - {"manual"}  # 13c：手册只给自己的内核；OpenClaw 那条路（/mcp）照旧
    if name == "tree" and (not args or args[0] not in ok):
        raise BridgeError(L(f"tree 只能用 {'、'.join(sorted(ok))}", f"tree only allows {', '.join(sorted(ok))}"))
    if name == "tree" and args[0] == "manual" and (len(args) < 2 or args[1] not in TREE_MANUAL_OK):
        ok = sorted(TREE_MANUAL_OK)
        raise BridgeError(L(f"tree manual 在这里只能读：{'、'.join(ok)}。手册是做法、各个平台共用，改之前要用户点头：内核里用 manual_manage 交卡，"
                            "别处交一张收件箱卡提议，或者请用户自己改（mousse-tree manual edit）",
                            f"tree manual is read-only here: {', '.join(ok)}. Manuals are how things get done and every platform shares them, so "
                            "changes need the user's OK: in the engine use manual_manage (it makes an approval card); elsewhere propose it with an "
                            "inbox card, or ask the user to change it (mousse-tree manual edit)"))
    for i, a in enumerate(args):
        flag, eq, val = a.partition("=")
        if not flag.startswith("--") or flag in NOT_FILE or flag in NOT_FILE_IN.get(name, ()) or not any(f.startswith(flag) for f in FILE_FLAGS) or len(flag) < 4:
            continue
        v = val if eq else (args[i + 1] if i + 1 < len(args) else "")
        if v != "-":
            raise BridgeError(L(f"经 MCP 不能读服务器上的文件：{flag} 写 -，内容放 input",
                                f"Over MCP you can't read files on the server: write {flag} -, and put the content in input"))


def overrides() -> dict:
    m = raw().get("mcp")
    s = m.get("scripts") if isinstance(m, dict) else None
    return s if isinstance(s, dict) else {}


def command(name: str) -> list[str] | None:
    """这个工具背后要跑的命令（不含 args）；找不到就 None（工具不提供）。server.json 的 mcp.scripts 能换掉或关掉（null）。"""
    o = overrides()
    if name in o:
        v = o[name]
        if v is None:
            return None
        v = [v] if isinstance(v, str) else v
        return [str(Path(x).expanduser()) if str(x).startswith("~") else str(x) for x in v] or None
    script = BRIDGES[name][0]
    if script == "mousse-tree":
        return tree_command()
    path = REPO / script
    return [sys.executable, str(path)] if path.exists() else None


def tree_command() -> list[str] | None:
    """世界树跟 sources.py 选中的那一份走：工作区的 scripts/memory_tree.py → python3 跑那个脚本（think.py 也这么调）；
    开源版 → 服务同一个 venv 里的 mousse-tree（安装器装的），没有就看 PATH；都没接 → None。"""
    import sources
    if sources.memory_tree is None:
        return None
    if (script := sources.tree_script()) is not None:
        return ["python3", str(script)]
    exe = Path(sys.executable).parent / "mousse-tree"
    found = str(exe) if exe.exists() else shutil.which("mousse-tree")
    return [found] if found else None


def known_agent(aid: str) -> bool:
    """有这个 Agent 吗：OpenClaw 那种有自己 workspace 的，或者 groups 表里的（别的 claw 的 Agent 只在表里）。"""
    if aid == "main" or aid in settings.agent_workspaces:
        return True
    try:
        with sqlite3.connect(f"file:{settings.db}?mode=ro", uri=True, timeout=5) as conn:
            return conn.execute("SELECT 1 FROM groups WHERE id=?", (aid,)).fetchone() is not None
    except sqlite3.Error:
        return False


def workdir(agent: str | None) -> Path:
    """命令在哪儿跑：是某个有 workspace 的 Agent 就在它的 workspace（和它自己在 shell 里跑一样）；否则在主 workspace。
    是谁另外经环境变量 MOUSSE_AGENT 告诉脚本（看板、收件箱先认它），别的 claw 的 Agent 没有 workspace 也认得出。"""
    if agent and agent != "main":
        if not known_agent(agent):
            raise BridgeError(L(f"没有「{agent}」这个 Agent（agents 工具的 list 能看到有哪些）",
                                f"There's no Agent called {agent} (the agents tool's list shows them)"))
        ws = settings.agent_workspaces.get(agent)
        if ws and ws.is_dir():
            return ws
    ws = settings.workspace
    return ws if ws.is_dir() else REPO


def clip(text: str) -> str:
    if len(text) <= MAX_OUT:
        return text
    head, tail = text[: MAX_OUT * 2 // 3], text[-MAX_OUT // 4:]
    return f"{head}\n…（{L('中间省略', 'omitted')} {len(text) - len(head) - len(tail)} {L('字', 'chars')}）…\n{tail}"


def _kill(proc: asyncio.subprocess.Process) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)  # 连它起的子进程一起
    except (ProcessLookupError, PermissionError):
        pass


class BridgeTimeout(BridgeError):
    """到了时限还没跑完（已经连它起的子进程一起停掉了）。"""


async def execute(name: str, cmd: list[str], args: list[str], stdin: str | None, agent: str | None, *, timeout: float,
                  kill_on_cancel: bool = False, shared: bool = True) -> tuple[int, str, str]:
    """起一个子进程跑一次（不经 shell，参数一项一个词）→ (退出码, 标准输出, 标准错误)，退出码由调用方看。参数不许抛 BridgeError，
    超时抛 BridgeTimeout。name：参数照哪个工具的规矩查（guard；只跑命令的定时任务跑白名单里的脚本时给 ""）。run 和定时任务共用。
    shared：占不占 /mcp 和内核共用的那 4 个（PARALLEL）；定时任务不占（见文件开头）。"""
    args = [str(a) for a in args or []]
    if sum(len(a) for a in args) + len(stdin or "") > MAX_ARGS or any("\x00" in a for a in args):
        raise BridgeError(L("参数太长或者有非法字符", "Arguments too long or contain invalid characters"))
    guard(name, args)
    cwd = workdir(agent)
    env = dict(os.environ, PYTHONIOENCODING="utf-8", MOUSSE_MCP="1")
    for k in ("MOUSSE_AGENT", "MOUSSE_VIA", "MOUSSE_THREAD", "MOUSSE_MESSAGE", "MOUSSE_FROM"):
        env.pop(k, None)
    if agent:
        env["MOUSSE_AGENT"] = agent
    if via := VIA.get():
        env["MOUSSE_VIA"] = via
    if name == "tree" and (said := SAID.get()) and said[0]:
        env["MOUSSE_THREAD"], env["MOUSSE_MESSAGE"] = str(said[0]), str(said[1] or "")
    if name == "handoff" and (src := FROM.get()):
        env["MOUSSE_FROM"] = src
    label = name or os.path.basename(cmd[-1])
    async with PARALLEL if shared else contextlib.nullcontext():
        proc = await asyncio.create_subprocess_exec(
            *cmd, *args, cwd=str(cwd), env=env, start_new_session=True,
            stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(proc.communicate(stdin.encode("utf8") if stdin is not None else None), timeout)
        except asyncio.TimeoutError:
            _kill(proc)
            await proc.wait()
            raise BridgeTimeout(L(f"{label} 超过 {timeout:g} 秒没跑完，停掉了", f"{label} didn't finish within {timeout:g} seconds and was stopped")) from None
        except asyncio.CancelledError:
            if kill_on_cancel:
                _kill(proc)
            raise
    return int(proc.returncode or 0), out.decode("utf8", "replace").strip(), err.decode("utf8", "replace").strip()


async def run(name: str, args: list[str], stdin: str | None, agent: str | None, *, kill_on_cancel: bool = False) -> str:
    """跑一次 → 脚本的输出（掐过长的）。退出码不是 0、超时、参数不许都抛 BridgeError。
    kill_on_cancel：调用方被取消时（内核里用户点了停）连子进程一起杀掉；/mcp 照旧不杀（让脚本跑完）。
    SAID 里有 (对话, 消息 id) 时（内核的 tree 工具设的）：经 MOUSSE_THREAD / MOUSSE_MESSAGE 告诉世界树这是哪次对话、哪条消息（叶子记下出处）。"""
    cmd = command(name)
    if not cmd:
        raise BridgeError(L(f"这台服务器上没有 {name} 这个功能", f"{name} isn't available on this server"))
    rc, text_out, text_err = await execute(name, cmd, args, stdin, agent, timeout=BRIDGES[name][3], kill_on_cancel=kill_on_cancel)
    if rc != 0:
        why = text_err or text_out or L("（没有输出）", "(no output)")
        raise BridgeError(clip(L(f"退出码 {rc}：", f"Exit code {rc}: ") + why))
    said = clip(text_err) if text_err and not text_out else clip(text_out or L("（完成，没有输出）", "(done, no output)"))
    if (after := AFTER.get(name)) is not None:
        said = await after(args, said, agent)
    return said
