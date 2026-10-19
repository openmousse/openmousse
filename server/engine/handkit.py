"""普通 Agent 的手（内核 ⑫，2026-10-16）：给谁什么、开关、分类怎么改、要点头的怎么交卡、会话开头讲手的那一节。
内核要能完整替代 OpenClaw 和 Hermes，命令、文件、网页、浏览器是这类 harness 最基本的手；这里把它们给普通 Agent（主对话和用户建的
各个 Agent），全部走内核现有的分类和关卡（engine/tools.py）。

默认给谁什么：
  主对话            主机上的文件（主工作区，engine/host_files.py）、web_search（配了搜索后端才有）和 web_fetch（engine/web.py）、
                    自己的沙箱里的命令、文件和浏览器（engine/hands.py 的 sandbox_tools；服务器装好了沙箱才有；看不到工作区：access none）
  用户建的 Agent    同样一套；文件限在它自己的工作区（没有工作区的没有文件工具）
  主机上的命令      全关（engine/host_exec.py：server.json 的 engine.host_exec 给某个 Agent 开、写放行名单）
  代办（只在沙箱里） 不变：只有沙箱那一套（engine/hands.py 的 tools_for），这里一个不给
  赛博、外人回合    一个都不给（Tool.kinds 只有 owner，关卡也只放主人回合）
开关（server.json，按 Agent；"*" = 别的 Agent 的默认）：
  engine.files.agents.<id> = false                         不给主机上的文件工具
  engine.web.agents.<id> = false / {"search": false}       不给网页 / 只不给搜索
  engine.sandbox.agents.<id> = false                       不给沙箱（主对话写 main；普通 Agent 要改设置写 {"only": false, "access": "ro"}）
  engine.host_exec.agents.<id> = {"allow": [...]}          开主机上的命令（不写 = 没有）
  engine.tools.levels                                      改分类，键 = 工具名：auto（直接做）/ ask（先问）/ off（不给）
要点头的（关卡说 ask）怎么办：tools.gate 交给这里挂的 tools.HANDS——主机命令有自己的两张卡（host_exec.ask：只这一次 / 以后都放行），
  别的（改规矩文件、rw 沙箱里的命令、levels 写了 ask 的）用通用的一张（ask）：卡上是工具和参数，同意 = 服务器照原样调一次那个工具
  （run_approved：现找这个 Agent 的那个工具，标成「已经点过头」，web 工具就不再交 Doorman），结果当「做完了」发回对话。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from contextvars import ContextVar

from i18n import L

from .loop import Control, Tool, ToolCtx, ToolFailure

READ, UNDO, ASK, OFF = "read", "undo", "ask", "off"
WEB = ("web_search", "web_fetch")
_approved: ContextVar[bool] = ContextVar("mousse_hand_approved", default=False)
LABELS = {"read_file": ("读文件", "Read a file"), "list_dir": ("看文件夹", "List a folder"), "find_files": ("找文件", "Find files"),
          "grep_files": ("搜文件内容", "Search files"), "write_file": ("写文件", "Write a file"), "edit_file": ("改文件", "Edit a file"),
          "restore_file": ("换回文件的旧版本", "Restore a file"), "web_search": ("上网搜", "Search the web"), "web_fetch": ("打开网页", "Open a web page"),
          "sandbox_exec": ("在沙箱里跑命令", "Run a command in the sandbox"), "sandbox_process": ("管沙箱里的后台命令", "Manage a background command"),
          "sandbox_read": ("读沙箱里的文件", "Read a sandbox file"), "sandbox_write": ("写沙箱里的文件", "Write a sandbox file"),
          "sandbox_edit": ("改沙箱里的文件", "Edit a sandbox file"), "sandbox_ls": ("看沙箱里的文件夹", "List a sandbox folder"),
          "browser": ("用沙箱里的浏览器", "Use the sandbox browser")}


def names() -> set[str]:
    """这里给的全部工具名（owner.rules 讲命令行工具那一行不列它们）。"""
    from . import hands, host_files
    return {*host_files.NAMES, *WEB, *hands.SANDBOX_NAMES, "host_exec"}


def approved_run() -> bool:
    """这一次调用是不是用户在卡上点过头的（run_approved 里）：web 工具就不再交 Doorman 判断。"""
    return _approved.get()


def levelled(name: str, base: tuple[str, str]) -> tuple[str, str]:
    """engine.tools.levels 改这个工具的档（键 = 工具名）：off = 不做；ask = 先问；auto = 直接做（本来要问的也直接做）。"""
    from .tools import levels
    v = levels().get(name)
    if v == OFF:
        return OFF, L("用户在设置里关掉了", "Turned off in the settings")
    if v == ASK:
        return ASK, L("用户在设置里定了这件事先问", "The settings say to ask first")
    if v == "auto" and base[0] == ASK:
        return UNDO, L("用户在设置里定了这件事直接做", "The settings say to just do it")
    return base


def tools_for(agent: str) -> list[Tool]:
    """普通 Agent 的手（只在沙箱里的 Agent 不给：它有自己的那一套）。engine.tools.levels 写 off 的不给。"""
    from . import hands, host_exec, host_files, web
    from .tools import levels
    if hands.confined(agent):
        return []
    out = host_files.tools(agent) + web.tools(agent) + hands.sandbox_tools(agent)
    if (t := host_exec.tool(agent)) is not None:
        out.append(t)
    lv = levels()
    return [t for t in out if lv.get(t.name) != OFF]


# —— 要点头的：通用的一张卡，同意了照原样做一次 ————————————————————————————————————————————

CARD_MAX = 20_000   # 卡上给用户看的参数最多多少字：同意了跑的就是卡上写的，一个字都不能藏（再长就不交卡，让它拆小）


def too_long(body: str) -> str | None:
    """卡上放不下（同意的人得看得到要跑的全部）→ 回给模型的一句；放得下 → None。"""
    if len(body) <= CARD_MAX:
        return None
    return L(f"没跑：这次调用的参数太长（{len(body)} 字），卡上放不下给用户看全（最多 {CARD_MAX} 字）。拆小一点再来（比如 edit_file 只改要改的那几段）。",
             f"Not run: this call's arguments are too long ({len(body)} characters) to show the user in full on an approval card "
             f"(at most {CARD_MAX}). Make it smaller (for example edit_file with just the parts that change) and try again.")


async def ask(tool: Tool, args: dict, ctx: ToolCtx) -> dict | str:
    """关卡说 ask：交一张 kind tool 的卡（tools.submit_card），卡上写死工具和全部参数（一个字不截：同意了跑的就是这些）。"""
    import inbox

    from . import host_exec
    from . import tools as tools_mod
    agent = ctx.agent or "main"
    who = inbox.source_name(agent, inbox.names())
    reason = tool.level(args)[1].rstrip("。. ")
    try:
        target = (tool.title(args) if tool.title else "")[:60]
    except Exception:  # noqa: BLE001
        target = ""
    zh, en = LABELS.get(tool.name, (tool.name, tool.name))
    body = json.dumps({"tool": tool.name, "args": {k: v for k, v in args.items() if k != "why"}}, ensure_ascii=False, indent=2, default=str)
    if (no := too_long(body)) is not None:
        return no
    fence = host_exec._fence(body)
    card = {"title": L(f"{zh}：{target}", f"{en}: {target}") if target else L(zh, en),
            "why": str(args.get("why") or "").strip() or L(f"{who}想{zh}。{reason}。", f"{who} wants to {en[0].lower() + en[1:]}. {reason}."),
            "changes": [L(f"工具：{tool.name}", f"Tool: {tool.name}")] + ([L(f"对象：{target}", f"Target: {target}")] if target else []),
            "detail": L("同意后，服务器照下面的参数做一次，结果发回对话。", "If you approve, the server does exactly this once and posts the result to the chat.")
            + f"\n\n{fence}json\n{body}\n{fence}",
            "approveLabel": L("照做", "Do it")}
    dedupe = "hand:" + hashlib.sha256(json.dumps([agent, tool.name, args], ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()[:32]
    return await tools_mod.submit_card(tool.name, json.dumps(args, ensure_ascii=False, default=str), None, card, dedupe, ctx, who=agent)


async def run_approved(row, it: dict) -> str:
    """用户点了同意（tools.on_decided 交过来）：现找这个 Agent 的那个工具，照卡上的参数调一次（算点过头的）。→ 结果的字。"""
    args = json.loads(row["args"] or "{}")
    agent = row["agent"]
    tool = next((t for t in tools_for(agent) if t.name == row["tool"]), None)
    if tool is None:
        raise ToolFailure(L(f"{agent} 现在没有 {row['tool']} 这个工具了，没有做", f"{agent} no longer has the {row['tool']} tool, so nothing was done"))
    ctx = ToolCtx(turn=row["turn"] or "", kind="owner", agent=agent, conversation=row["thread"] or agent, call_id=row["call"] or "", control=Control())
    token = _approved.set(True)
    try:
        res = await asyncio.wait_for(tool.run(args, ctx), tool.timeout)
    except asyncio.TimeoutError:
        raise ToolFailure(L(f"{tool.timeout:.0f} 秒没做完", f"It didn't finish within {tool.timeout:.0f}s")) from None
    except ToolFailure:
        raise
    except Exception as e:  # noqa: BLE001 — 给卡上、对话里一句原因
        raise ToolFailure(f"{type(e).__name__}: {str(e)[:300]}") from None
    finally:
        _approved.reset(token)
    return res if isinstance(res, str) else json.dumps(res, ensure_ascii=False, default=str)


def install() -> None:
    """把这几样挂进关卡（tools.HANDS）：主机命令用它自己的两张卡，别的用通用的。导入这个模块、服务启动（tools.install_hooks）时都挂。"""
    from . import host_exec
    from . import tools as tools_mod
    for n in names():
        tools_mod.HANDS[n] = tools_mod.Hand(ask, run_approved)
    tools_mod.HANDS["host_exec"] = tools_mod.Hand(host_exec.ask, host_exec.approved, host_exec.closed)


# —— 会话开头讲手的那一节（owner.rules 接）——————————————————————————————————————————

def rules(agent: str, tools: list) -> str:
    """有哪些手、哪些直接做、哪些要点头、东西在哪（主机和沙箱分开说）。一样都没有 → 空。"""
    import egress

    from . import hands, host_exec, host_files, sandbox, web
    have = {t.name for t in tools}
    root = host_files.root_of(agent)
    minutes = egress.grant_minutes()
    lines = []
    if have & set(host_files.NAMES):
        lines.append(
            f"- **Files on the user's server**: your workspace is {root} (relative paths start there). read_file shows numbered lines "
            "(offset / limit for long files) and images as the picture itself; list_dir, find_files and grep_files look around; write_file, edit_file (each old text must "
            "appear exactly once in the file) and restore_file change things. You can't leave the workspace, follow symbolic links, or open "
            "anything that looks like a secret (.env, keys, credentials…). Every change keeps the old version first (.mousse-versions), so "
            "it runs right away: say what you changed and that restore_file undoes it. Changing your rules files (AGENTS.md, SOUL.md, "
            "IDENTITY.md, TOOLS.md, USER.md, skills/) needs the user's OK.")
    if have & set(WEB):
        s = "- **The web**: "
        s += (f"web_search searches the web ({web.provider()}). " if "web_search" in have
              else "Web search isn't set up on this server, so you can't search; ")
        if "web_fetch" in have:
            s += "web_fetch opens a public http(s) page or document as readable text (long pages come in parts: call again with offset). "
        s += ("Reading is free. A link or query that carries the user's private details (address, email, phone, family names…) or looks "
              "like it sends data out goes to Doorman first; if it's held, tell the user it's waiting for their OK in the inbox, and once "
              f"they let it through, call again with exactly the same input within {minutes} minutes.")
        lines.append(s)
    if sandbox.box(agent) is not None and have & set(hands.SANDBOX_NAMES):
        where = {"none": "/workspace in it is an empty scratch folder of its own (kept between conversations, wiped when the sandbox is reset "
                         "after about two idle hours); none of the user's files are in the sandbox, and nothing you do there touches their server",
                 "ro": f"/workspace in it is your workspace ({root}), read-only (scratch files go under /tmp); files that look like secrets are hidden",
                 "rw": f"/workspace in it IS your workspace ({root}) on the user's server, writable: sandbox_write / sandbox_edit keep the old "
                       "version first like write_file, but every sandbox_exec command needs the user's OK, because a command can change any of "
                       "their files and resetting the sandbox doesn't undo that; files that look like secrets are hidden"}[sandbox.mounted(agent)]
        net = ("It has no network at all." if sandbox.setting("network") == "none" else
               "Everything it sends online goes through Doorman, the user's gatekeeper: reading is free; submitting, sending, logging in, "
               "uploading or anything carrying the user's private details waits for their OK (when a result says Doorman is holding "
               f"something, stop and tell the user; once they let it through, do exactly the same again within {minutes} minutes). Never "
               "ask the user for passwords.")
        browse = ("; browser drives a headless Chromium in it (screenshot shows you the visible part of the page as a picture)"
                  if "browser" in have else "")
        lines.append(f"- **Your sandbox**: a Linux container of your own (Debian, user sandbox; python3, curl, jq, git). sandbox_exec runs "
                     "bash commands in it (120 s by default; background for long ones, watched with sandbox_process); sandbox_read / "
                     f"sandbox_write / sandbox_edit / sandbox_ls handle files in it{browse}. {where}. {net}")
    if "host_exec" in have and (p := host_exec.policy(agent)) is not None:
        allow = "; ".join(f"`{x}`" for x in p.allow) or "(none yet)"
        lines.append("- **Commands on the user's server**: host_exec runs one program in your workspace, outside any sandbox (no shell: no "
                     f"pipes, redirects, && or $VAR). On the allowlist, so they run right away: {allow}. Anything else sends the user an "
                     "approval card (they can allow it just this once, or from now on); risky commands (mass deletes, permission changes, "
                     "download-and-run, anything wrapped in sudo / env / bash -c…) always ask. Use the sandbox for anything experimental.")
    elif have & set(hands.SANDBOX_NAMES):
        lines.append("- You can't run commands on the user's server itself: use your sandbox.")
    if not lines:
        return ""
    return ("## Your hands\nBesides the tools above you have these (each takes its own parameters, not args):\n" + "\n".join(lines)
            + "\n- **Right away or the user's OK**: looking (files, the web, the sandbox) and changes that can be undone (file writes keep "
            "the old version; the sandbox is your own scratch space) run right away. A call that needs their OK returns \"approval-pending\" "
            "at once with a card for them: don't call it again or try another way; tell them it's waiting.")


install()
