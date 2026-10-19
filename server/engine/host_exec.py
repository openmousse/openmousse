"""主机上跑命令（内核 ⑫，2026-10-16，照 OpenClaw 的执行审批）：给已经在用主机脚本的用户（比如从 OpenClaw 切过来的），默认全关。

开：主人在 server.json 给某个 Agent 写一段（没写的 Agent 没有这个工具；公开版默认谁都没有）：
  "engine": {"host_exec": {"agents": {"main": {"allow": ["python3 <workspace>/scripts/*.py ...", "git status"],
                                               "ask": "on-miss", "security": "allowlist", "timeout": 120, "env": ["OPENAI_API_KEY"]}}}}
  allow     放行名单：一条一个命令模式，按词比（照 shell 的规矩切词）。<workspace>（或 <工作区>）= 这个 Agent 的工作区；
            * 不跨 /、** 跨 /、? 一个字；带 / 的位置是路径：命令里的相对路径照工作目录算成绝对路径再比（.. 先化掉；符号链接指到的
            真实路径也要对得上）；第一个词不带 / = PATH 上的那个程序（命令里写成 ./x 的不算）；最后写 ... = 后面还能跟任意参数，
            不写 = 参数个数一样才算（不让在后面接参数改掉命令的意思）
  ask       on-miss（默认）：名单里的直接跑、名单外的出卡；always：每条都出卡；off：名单外的直接不跑（不问）
  security  allowlist（默认）：按名单；full：不看名单、都算放行（照 OpenClaw；危险的照样问）
  timeout   默认几秒（120；模型能写到 max_timeout，默认 900）。env：照传给命令的环境变量（默认像密钥的都去掉，见下）
分类（关卡）：名单里的（或 full）= 主人事先点过头，直接跑（算 undo 那一档：撤不撤得回看脚本自己）；名单外的、ask always 的、
  危险的 = ask：交收件箱卡（tools.submit_card，kind tool）。卡有两张：「只这一次」（同意 = 服务器照这条跑一次，结果发回对话）和
  「以后都放行」（静默的一张；同意 = 把这条命令一模一样地写进 allow，再跑这一次）；点了其中一张、拒了「只这一次」，另一张跟着撤回。
  拒了「以后都放行」只是不进名单，「只这一次」还在等。危险的、带通配符的、ask always 的没有「以后都放行」（OpenClaw 也一样）。
  engine.tools.levels 的 host_exec：ask = 每条都问，off = 不给；auto 不会跳过名单（要跳过写 security: full）。
危险的（即使在名单里也出卡，也不给「以后都放行」）：
  - 包了一层、换了身份的：sudo / doas / su / env / nice / timeout / xargs / setsid……（真正跑的是后面那个程序）、find -exec / -delete
  - 内联代码：sh / bash -c、python -c、node -e、perl -e、ruby -e、awk……（代码就在参数里，名单管不住）
  - 照 Hermes 的危险命令识别（改写自 tools/approval_detection.py，见下）：rm -rf、chmod 777、mkfs、dd、关机、杀全部进程、
    下载即执行、解码了再交给 shell、上传凭据、改系统配置和 shell 配置、git reset --hard / push --force / clean -f、卸载……
  - 动服务器自己的：docker 删 / 停容器（沙箱在那里）、~/.openmousse
怎么跑：不经 shell（切词后 exec：管道、重定向、&&、$VAR 都不认，写了就不跑并说明）；程序先按（干净的）PATH 找成绝对路径，跑的就是
  比过名单的那一个；工作目录 = 这个 Agent 的工作区（cwd 只能是里面的子文件夹）；环境变量：名字里带 KEY / TOKEN / SECRET / PASSWORD /
  CREDENTIAL / AUTH……的、网址里带着密码的、LD_* / DYLD_* / NODE_OPTIONS / PYTHONPATH 这类能改程序行为的、我们自己的 MOUSSE_* 都去掉
  （env 里写了名字的照传）；PATH 只留绝对路径；另给 MOUSSE_AGENT。超时（默认 120 秒）、输出留头尾（同沙箱）、同时最多 4 个；
  点了停、到点：整个进程组 SIGTERM，2 秒后 SIGKILL（照 bridges.py）。
  卡交上去以后到用户点头之前，脚本可能被改：交卡时记下程序和脚本文件的指纹，同意时对一下，变了就不跑（OpenClaw 的 systemRunPlan）。
  名单里会跑到的脚本（带 / 的那几个词，和它们所在的子文件夹）主机上的文件工具不许改、不许新建（engine/host_files.py），能写工作区的
  沙箱也把它们只读挂上：不然改一下脚本就等于随便跑什么。脚本从别处读进来的代码管不到：放行的脚本单独放一个文件夹。

参考（10/16 现拉的源码，只读）：OpenClaw e38e3a42 的 src/infra/exec-approvals-core.ts / -policy.ts（deny / allowlist / full ×
off / on-miss / always，更严的赢）、exec-allowlist-pattern.ts（* 不跨 /、** 跨）、exec-approvals-allowlist.ts（裸名只认 PATH 上的、
不认 ./x）、exec-approvals-allow-always.ts（以后都放行 = 一模一样的参数；解释器、内联代码、ask always 不给）、
src/infra/host-env-security-policy.json（LD_* 这类）、src/agents/bash-tools.exec-host-shared.ts（进程组、5 秒宽限）；
Hermes（https://github.com/NousResearch/hermes-agent，commit f6e272304e7224da88ca6e70e1c6ce4a67154f8e，MIT）的
tools/approval_detection.py（DANGEROUS_PATTERNS、HARDLINE_PATTERNS、_normalize_command_for_detection、_lower_preserving_flags）和
tools/ansi_strip.py（strip_ansi）：下面「危险的命令」那一段正则是改写过来的（代码，不只是设计；许可全文见仓库根目录 THIRD_PARTY_NOTICES）；
tools/code_execution_env.py（_SECRET_SUBSTRINGS，只借做法）。
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import time
import unicodedata
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from config import raw
from i18n import L

from . import safefs, sandbox
from .loop import Tool, ToolCtx, ToolFailure

NAME = "host_exec"
READ, UNDO, ASK, OFF = "read", "undo", "ask", "off"
TIMEOUT, MAX_TIMEOUT = 120, 900
SLOTS = asyncio.Semaphore(4)
KILL_GRACE = 2.0
INPUT_MAX = 200_000
OPS = set("|&;<>()")


# —— 设置 ——————————————————————————————————————————————————————————————————

def cfg() -> dict:
    e = raw().get("engine")
    h = e.get("host_exec") if isinstance(e, dict) else None
    return h if isinstance(h, dict) else {}


@dataclass
class Policy:
    allow: list[str] = field(default_factory=list)
    ask: str = "on-miss"
    security: str = "allowlist"
    timeout: int = TIMEOUT
    max_timeout: int = MAX_TIMEOUT
    env: list[str] = field(default_factory=list)


def policy(agent: str) -> Policy | None:
    """这个 Agent 的主机命令设置；没开 → None（没有这个工具）。"""
    a = cfg().get("agents")
    v = a.get(agent) if isinstance(a, dict) else None
    if v is True:
        v = {}
    if not isinstance(v, dict):
        return None
    from . import tools as tools_mod
    if tools_mod.levels().get(NAME) == OFF:
        return None
    p = Policy()
    p.allow = [str(x) for x in v.get("allow") or [] if str(x).strip()] if isinstance(v.get("allow"), list) else []
    p.ask = str(v.get("ask") or "on-miss").strip().lower()
    p.ask = p.ask if p.ask in ("off", "on-miss", "always") else "on-miss"
    p.security = "full" if str(v.get("security") or "").strip().lower() == "full" else "allowlist"
    try:
        p.max_timeout = max(1, min(int(v.get("max_timeout") or MAX_TIMEOUT), 3600))
        p.timeout = max(1, min(int(v.get("timeout") or TIMEOUT), p.max_timeout))
    except (TypeError, ValueError):
        pass
    p.env = [str(x) for x in v.get("env") or []] if isinstance(v.get("env"), list) else []
    if tools_mod.levels().get(NAME) == ASK:
        p.ask = "always"
    return p


def workspace(agent: str) -> Path | None:
    from . import host_files
    return host_files.root_of(agent)


# —— 命令模式 ——————————————————————————————————————————————————————————————

def glob_rx(pat: str) -> re.Pattern:
    """* 不跨 /、** 跨 /、? 一个字（不是 /），别的照字面（OpenClaw 的 exec-allowlist-pattern）。"""
    out = []
    for tok in re.findall(r"\*\*|[*?]|[^*?]+", pat):
        out.append(".*" if tok == "**" else "[^/]*" if tok == "*" else "[^/]" if tok == "?" else re.escape(tok))
    return re.compile("".join(out), re.S)


def expand(tok: str, ws: Path) -> str:
    tok = tok.replace("<workspace>", str(ws)).replace("<工作区>", str(ws))
    return os.path.expanduser(tok) if tok.startswith("~") else tok


@dataclass
class Pattern:
    text: str
    tokens: list[str]
    rest: bool


def compile_pattern(text: str, ws: Path) -> Pattern | None:
    try:
        toks = shlex.split(text)
    except ValueError:
        return None
    rest = bool(toks) and toks[-1] in ("...", "…")
    toks = toks[:-1] if rest else toks
    return Pattern(text, [expand(t, ws) for t in toks], rest) if toks else None


def patterns(agent: str) -> list[Pattern]:
    p, ws = policy(agent), workspace(agent)
    if p is None or ws is None:
        return []
    return [c for c in (compile_pattern(t, ws) for t in p.allow) if c is not None]


def _abs(arg: str, cwd: Path) -> str:
    return os.path.normpath(os.path.join(str(cwd), os.path.expanduser(arg)))


@dataclass
class Cmd:
    raw: str
    argv: list[str]
    exe: str            # 要 exec 的那个程序（绝对路径）
    real: str           # 它的真实路径（比名单、记指纹用）
    cwd: Path
    cwd_rel: str


def matches(p: Pattern, c: Cmd) -> bool:
    n = len(p.tokens)
    if len(c.argv) < n or (not p.rest and len(c.argv) != n):
        return False
    t0 = p.tokens[0]
    if "/" in t0:
        if not (glob_rx(t0).fullmatch(c.exe) and glob_rx(t0).fullmatch(c.real)):
            return False
    elif "/" in c.argv[0] or not glob_rx(t0).fullmatch(c.argv[0]):
        return False
    for t, a in zip(p.tokens[1:], c.argv[1:n]):
        if "/" in t:  # 路径位：算成绝对路径；符号链接指到的真实路径也要在名单里
            a = _abs(a, c.cwd)
            if not (glob_rx(t).fullmatch(a) and glob_rx(t).fullmatch(os.path.realpath(a))):
                return False
        elif not glob_rx(t).fullmatch(a):
            return False
    return True


def allowed(agent: str, c: Cmd) -> Pattern | None:
    return next((p for p in patterns(agent) if matches(p, c)), None)


def protected(agent: str) -> tuple[list[re.Pattern], list[Path]]:
    """名单里会跑到的脚本：(路径的模式, 整个护住的子文件夹)。工作区根目录不整个护住（不然什么都写不了），只护住对得上的文件。"""
    ws = workspace(agent)
    if ws is None:
        return [], []
    rxs, dirs = [], []
    for p in patterns(agent):
        for t in p.tokens:
            if "/" not in t:
                continue
            rxs.append(glob_rx(t))  # 工作区外面起的通配也算（/**/x.py 这种也对得上工作区里的文件）
            if not (t == str(ws) or t.startswith(str(ws) + "/")):
                continue
            lit = re.split(r"[*?]", t, maxsplit=1)[0]
            d = Path(lit[: lit.rfind("/")]) if "/" in lit else None
            if d is not None and d != ws and d.is_relative_to(ws):
                dirs.append(d)
    return rxs, dirs


def mount_guards(agent: str, ws: Path) -> list[Path] | None:
    """能写工作区的沙箱（sandbox.workspace_mounts）要只读盖上的：名单里会跑到的脚本所在的子文件夹（根目录下的就盖那个文件）。
    盖不住（工作区根目录下的通配、文件夹或文件还没有）→ None：整个工作区只读挂。"""
    out: list[Path] = []
    for p in patterns(agent):
        for t in p.tokens:
            if "/" not in t:
                continue
            lit = re.split(r"[*?]", t, maxsplit=1)[0]
            if not t.startswith(str(ws) + "/"):
                if lit != t and (str(ws) + "/").startswith(lit):  # 通配从工作区外面起、伸得进工作区（/**/x.py 这种）：盖不住
                    return None
                continue
            d = Path(lit[: lit.rfind("/")])
            if d == ws:
                if lit != t or not os.path.isfile(t) or os.path.islink(t):
                    return None
                out.append(Path(t))
            elif d.is_relative_to(ws):
                if not d.is_dir() or d.is_symlink():
                    return None
                out.append(d)
    return sorted(set(out), key=lambda x: len(x.parts))


def protects(agent: str, target: Path) -> bool:
    """host_files 写之前问：这个文件是不是名单里会跑到的（或在它们的子文件夹里）。"""
    rxs, dirs = protected(agent)
    t = str(target)
    return any(r.fullmatch(t) for r in rxs) or any(target == d or target.is_relative_to(d) for d in dirs)


# —— 切词、找程序 ——————————————————————————————————————————————————————————————

def clean_path() -> str:
    return os.pathsep.join(p for p in os.environ.get("PATH", "").split(os.pathsep) if p.startswith("/"))


def shell_syntax(raw_cmd: str) -> str | None:
    """不经 shell：管道、重定向、&&、$( )、`…`、$VAR 都不会生效。看得出写了这些 → 一句说明。"""
    try:
        lx = shlex.shlex(raw_cmd, posix=False, punctuation_chars=True)
        lx.whitespace_split = True
        toks = list(lx)
    except ValueError:
        return L("引号没配对", "Unbalanced quotes")
    for t in toks:
        if t and set(t) <= OPS:
            return L(f"「{t}」是 shell 的写法", f"'{t}' is shell syntax")
        if not t.startswith("'") and re.search(r"`|\$\(|\$\{|\$[A-Za-z_]", t):
            return L(f"「{t[:30]}」要 shell 展开", f"'{t[:30]}' needs a shell to expand")
    return None


def parse(agent: str, command: Any, cwd: str | None = None) -> Cmd:
    """一条命令 → Cmd（程序找成绝对路径）。写法不对、程序找不到抛 ToolFailure。"""
    from . import host_files
    ws = workspace(agent)
    if ws is None:
        raise ToolFailure(L("你没有自己的工作区，主机上的命令跑不了", "You have no workspace of your own, so host commands can't run"))
    if isinstance(command, list):
        argv = [str(x) for x in command]
        raw_cmd = shlex.join(argv)
    else:
        raw_cmd = str(command or "").strip()
        if why := shell_syntax(raw_cmd):
            raise ToolFailure(L(f"没跑：{why}。host_exec 不经 shell：只跑一个程序、参数原样给它（管道、重定向、&&、$VAR、通配符都不认）。"
                                "要 shell 的写法用沙箱里的 sandbox_exec，或者请用户把一个脚本加进放行名单。",
                                f"Not run: {why}. host_exec doesn't use a shell: it runs one program with its arguments as given (no pipes, "
                                "redirects, &&, $VAR or globs). For shell features use sandbox_exec in your sandbox, or ask the user to "
                                "allowlist a script."))
        try:
            argv = shlex.split(raw_cmd)
        except ValueError:
            raise ToolFailure(L("没跑：引号没配对", "Not run: unbalanced quotes")) from None
    if not argv or not argv[0].strip() or any("\x00" in a for a in argv):
        raise ToolFailure(L("没跑：命令是空的", "Not run: the command is empty"))
    if sum(len(a) for a in argv) > 100_000:
        raise ToolFailure(L("没跑：命令太长", "Not run: the command is too long"))
    rel = str(cwd or "").strip()
    if rel and rel not in (".", "./"):
        pl = host_files.place(agent, rel, dir_ok=True)
        try:
            st = pl.fs.lstat(pl.parts)
        except (OSError, safefs.Refused) as e:  # 路上有一层是符号链接、不是文件夹……
            raise ToolFailure(L(f"没跑：工作目录 {rel} 不行：{e}", f"Not run: the working folder {rel} won't do: {e}")) from None
        if st is None or not os.path.isdir(pl.root.joinpath(*pl.parts)) or os.path.islink(pl.root.joinpath(*pl.parts)):
            raise ToolFailure(L(f"没跑：工作区里没有文件夹 {rel}", f"Not run: there's no folder {rel} in your workspace"))
        where, rel = pl.root.joinpath(*pl.parts), pl.rel
    else:
        where, rel = ws, "."
    a0 = argv[0]
    if "/" in a0:
        exe = _abs(a0, where)
        if not (os.path.isfile(exe) and os.access(exe, os.X_OK)):
            raise ToolFailure(L(f"没跑：{a0} 不是能跑的程序", f"Not run: {a0} isn't an executable program"))
    else:
        found = shutil.which(a0, path=clean_path())
        if not found:
            raise ToolFailure(L(f"没跑：服务器的 PATH 上没有 {a0}", f"Not run: {a0} isn't on the server's PATH"))
        exe = os.path.abspath(found)
    return Cmd(raw_cmd, argv, exe, os.path.realpath(exe), where, rel)


# —— 危险的命令 ———————————————————————————————————————————————————————————————
# 下面这一段正则改写自 Hermes（NousResearch/hermes-agent，commit f6e272304e7224da88ca6e70e1c6ce4a67154f8e，MIT，Copyright (c) 2025 Nous Research）的
# tools/approval_detection.py：HARDLINE_PATTERNS、DANGEROUS_PATTERNS 里和 Unix 命令有关的那些（去掉了 Windows、Hermes 自己的服务、
# 容器全局参数那几条），连同 _normalize_command_for_detection、_lower_preserving_flags 的做法和 tools/ansi_strip.py 的 strip_ansi；
# 许可全文见仓库根目录的 THIRD_PARTY_NOTICES。
_ANSI = re.compile(r"\x1b(?:\[[\x30-\x3f]*[\x20-\x2f]*[\x40-\x7e]|\][\s\S]*?(?:\x07|\x1b\\)|[PX^_][\s\S]*?(?:\x1b\\)|[\x20-\x2f]+[\x30-\x7e]|[\x30-\x7e])"
                   r"|\x9b[\x30-\x3f]*[\x20-\x2f]*[\x40-\x7e]|\x9d[\s\S]*?(?:\x07|\x9c)|[\x80-\x9f]", re.DOTALL)
_SSH_SENSITIVE_PATH = r'(?:~|\$home|\$\{home\})/\.ssh(?:/|$)'
_PROJECT_ENV_PATH = r'(?:(?:/|\.{1,2}/)?(?:[^\s/"\'`]+/)*\.env(?:\.[^/\s"\'`]+)*)'
_SHELL_RC_FILES = r'(?:~|\$home|\$\{home\})/\.' r'(?:bashrc|zshrc|profile|bash_profile|zprofile)\b'
_CREDENTIAL_FILES = r'(?:~|\$home|\$\{home\})/\.' r'(?:netrc|pgpass|npmrc|pypirc)\b'
_HOME_PREFIX = r'(?:~|\$home|\$\{home\})/'
_SECRET_FILE = (rf'(?:{_SSH_SENSITIVE_PATH}|{_CREDENTIAL_FILES}|{_PROJECT_ENV_PATH}(?![\w-])'
                rf'|{_HOME_PREFIX}\.(?:aws/credentials|git-credentials|config/gh/hosts\.yml|docker/config\.json|kube/config)\b'
                r'|/etc/(?:shadow|passwd)\b|\bid_(?:rsa|ed25519|ecdsa|dsa)\b)')
_SECRET_VAR = r'\$\{?\w*(?:key|token|secret|passwd|password|credential)\w*\}?'
_UPLOAD_FLAG = (r'(?<!\S)(?:(?-i:-[a-zA-Z]*[dFT])|--data(?:-binary|-raw|-urlencode|-ascii)?|--form(?:-string)?'
                r'|--upload-file|--json|--post-(?:data|file)|--body-(?:data|file))')
_EMOJI_RANGE = r'\U0001F000-\U0001FAFF\u2600-\u27BF'
_SYSTEM_CONFIG_PATH = r'(?:/etc/|/private/(?:etc|var|tmp|home)/)'
_SENSITIVE_WRITE_TARGET = rf'(?:{_SYSTEM_CONFIG_PATH}|/dev/sd|{_SSH_SENSITIVE_PATH}|{_SHELL_RC_FILES}|{_CREDENTIAL_FILES})'
_USER_SENSITIVE_WRITE_TARGET = rf'(?:{_SSH_SENSITIVE_PATH}|{_SHELL_RC_FILES}|{_CREDENTIAL_FILES})'
_COMMAND_TAIL = r'(?:\s*(?:&&|\|\||;).*)?$'
_WRITE_TARGET_BOUNDARY = r'(?=[\s;&|<>"\']|$)'
_CMDPOS = (r'(?:^|[\n`]|\$\()' r'\s*' r'(?:sudo\s+(?:-[^\s]+\s+)*)?' r'(?:env\s+(?:\w+=\S*\s+)*)?' r'(?:(?:exec|nohup|setsid|time)\s+)*' r'\s*')
_HARDLINE_SYSTEM_DIRS = r'/home|/home/\*|/root|/root/\*|/etc|/etc/\*|/usr|/usr/\*|/var|/var/\*|/bin|/bin/\*|/sbin|/sbin/\*|/boot|/boot/\*|/lib|/lib/\*'
_RM_FLAG_PREFIX = _CMDPOS + r'rm\s+(-[^\s]*\s+)*'
_PKG_OPTS = r'(?:-[^\s]+(?:\s+[^-\s][^\s]*)?\s+)*'
_SHELL_NAMES_RE = "bash|sh|zsh|ksh|dash"


def _hardline_rm_path(path_alt: str, tail: str = r'(?:\s|$|[)`;|&])') -> str:
    return rf'(?:["\'](?:{path_alt})["\']|(?:{path_alt}){tail})'


HARDLINE = [
    (_RM_FLAG_PREFIX + _hardline_rm_path(r'/(?:(?:\.\.?)?/)*(?:\.\.?)?\**|/ \*'), "recursive delete of root filesystem"),
    (_RM_FLAG_PREFIX + _hardline_rm_path(_HARDLINE_SYSTEM_DIRS), "recursive delete of system directory"),
    (_RM_FLAG_PREFIX + _hardline_rm_path(r'(?:~|\$\{?HOME\}?)(?:/?|/\*)?'), "recursive delete of home directory"),
    (_CMDPOS + r'mkfs(\.[a-z0-9]+)?\b', "format filesystem (mkfs)"),
    (_CMDPOS + r'dd\b[^\n]*\bof=/dev/(sd|nvme|hd|mmcblk|vd|xvd)[a-z0-9]*', "dd to raw block device"),
    (r'>\s*/dev/(sd|nvme|hd|mmcblk|vd|xvd)[a-z0-9]*\b', "redirect to raw block device"),
    (r':\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:', "fork bomb"),
    (_CMDPOS + r'kill\s+(-[^\s]+\s+)*-1\b', "kill all processes"),
    (_CMDPOS + r'(shutdown|reboot|halt|poweroff)\b', "system shutdown/reboot"),
    (_CMDPOS + r'init\s+[06]\b', "init 0/6 (shutdown/reboot)"),
    (_CMDPOS + r'systemctl\s+(poweroff|reboot|halt|kexec)\b', "systemctl poweroff/reboot"),
    (_CMDPOS + r'telinit\s+[06]\b', "telinit 0/6 (shutdown/reboot)"),
]
DANGEROUS = [
    (r'\brm\s+(-[^\s]*\s+)*/', "delete in root path"),
    (r'\brm\s+-[^\s]*r', "recursive delete"),
    (r'\brm\s+--recursive\b', "recursive delete (long flag)"),
    (r'\brm\s+(?!--(?:\s|$))(?:(?!\s--(?:\s|$))[^\n"\';|&])*\s' r'(?:-[a-z]*r[a-z]*\b|--recursive\b)', "recursive delete (flags after operands)"),
    (r'\bchmod\s+(-[^\s]*\s+)*(777|666|o\+[rwx]*w|a\+[rwx]*w)\b', "world/other-writable permissions"),
    (r'\bchmod\s+--recursive\b.*(777|666|o\+[rwx]*w|a\+[rwx]*w)', "recursive world/other-writable (long flag)"),
    (r'\bchown\s+(-[^\s]*)?R\s+root', "recursive chown to root"),
    (r'\bchown\s+--recur[a-z]*\b.*root', "recursive chown to root (long flag)"),
    (_CMDPOS + r'mkfs\b', "format filesystem"),
    (_CMDPOS + r'dd\s+.*if=', "disk copy"),
    (r'>\s*/dev/sd', "write to block device"),
    (r'\bDROP\s+(TABLE|DATABASE)\b', "SQL DROP"),
    (r'\bDELETE\s+FROM\b(?![^\n]*\bWHERE\b)', "SQL DELETE without WHERE"),
    (r'\bTRUNCATE\s+(TABLE)?\s*\w', "SQL TRUNCATE"),
    (rf'>\s*{_SYSTEM_CONFIG_PATH}', "overwrite system config"),
    (r'\bsystemctl\s+(-[^\s]+\s+)*(stop|restart|disable|mask)\b', "stop/restart system service"),
    (r'\bkill\s+-9\s+-1\b', "kill all processes"),
    (r'\bpkill\s+-9\b', "force kill processes"),
    (r'\bkillall\s+(-[^\s]*\s+)*-(9|KILL|SIGKILL)\b', "force kill processes (killall -KILL)"),
    (r'\bkillall\s+(-[^\s]*\s+)*-s\s+(KILL|SIGKILL|9)\b', "force kill processes (killall -s KILL)"),
    (r'\bkillall\s+(-[^\s]*\s+)*-r\b', "kill processes by regex (killall -r)"),
    (r':\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:', "fork bomb"),
    (rf'\b(curl|wget)\b.*\|\s*(?:[/\w]*/)?(?:{_SHELL_NAMES_RE})(?:\s|$|-c)', "pipe remote content to shell"),
    (rf'\b(?:{_SHELL_NAMES_RE})\s+<\s*<?\s*\(\s*(curl|wget)\b', "execute remote script via process substitution"),
    (r'(?:\beval\b|\bsource\b|\.)\s*(?:\$\(\s*|`\s*)(?:curl|wget)\b', "execute remote content via command substitution"),
    (r'(?<![\d.])(?:169\.254\.169\.254|100\.100\.100\.200)(?![\d.])|(?<![\w.-])metadata\.google\.internal(?![\w.-])|fd00:ec2::254',
     "cloud metadata endpoint access (instance credentials)"),
    (rf'\b(base64|base32|base16)\s+(?:-[dD]|--decode)\b.*\|\s*\b(?:{_SHELL_NAMES_RE})\b', "pipe decoded content to shell (possible command obfuscation)"),
    (rf'\bxxd\s+-r\b.*\|\s*\b(?:{_SHELL_NAMES_RE})\b', "pipe xxd-decoded content to shell (possible command obfuscation)"),
    (rf'\becho\b[^|]*\|\s*\btr\b[^|]*\|\s*\b(?:{_SHELL_NAMES_RE})\b', "pipe tr-transformed output to shell (possible command obfuscation)"),
    (rf'\bopenssl\b.*\b(?:base64|enc)\b[^|]*\s+-[dD]\b[^|]*\|\s*\b(?:{_SHELL_NAMES_RE})\b', "pipe openssl-decoded content to shell (possible command obfuscation)"),
    (rf'{_UPLOAD_FLAG}(?:[\s=]+|(?=[@"\'$]))(?:"[^"]*(?:{_SECRET_VAR}|{_SECRET_FILE})|\'[^\']*{_SECRET_FILE}'
     rf'|\$\([^)]*{_SECRET_FILE}|\S*?(?:{_SECRET_VAR}|{_SECRET_FILE}))', "upload a secret or credential file via curl/wget (possible exfiltration)"),
    (rf'{_SECRET_FILE}[^|;&\n]*\|[^;&|\n]*\b(?:curl|wget)\b[^;&|\n]*{_UPLOAD_FLAG}', "pipe a credential file into a curl/wget upload (possible exfiltration)"),
    (rf'[\u200b\u200c\u200e\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff\u180e\U000e0000-\U000e007f]'
     rf'|(?<![{_EMOJI_RANGE}\ufe0f])\u200d|\u200d(?![{_EMOJI_RANGE}])', "invisible or bidirectional Unicode control character (possible obfuscation)"),
    (rf'\btee\b.*["\']?{_SENSITIVE_WRITE_TARGET}', "overwrite system file via tee"),
    (rf'>>?\s*["\']?{_SENSITIVE_WRITE_TARGET}', "overwrite system file via redirection"),
    (rf'\btee\b.*["\']?{_PROJECT_ENV_PATH}["\']?{_WRITE_TARGET_BOUNDARY}', "overwrite project env via tee"),
    (rf'>>?\s*["\']?{_PROJECT_ENV_PATH}["\']?{_WRITE_TARGET_BOUNDARY}', "overwrite project env via redirection"),
    (r'\bxargs\s+.*\brm\b', "xargs with rm"),
    (r'\bfind\b.*-exec(?:dir)?\s+(/\S*/)?rm\b', "find -exec/-execdir rm"),
    (r'\bfind\b.*-delete\b', "find -delete"),
    (rf'\b(cp|mv|install)\b.*\s{_SYSTEM_CONFIG_PATH}', "copy/move file into system config path"),
    (rf'\b(cp|mv|install)\b.*\s["\']?{_PROJECT_ENV_PATH}["\']?{_COMMAND_TAIL}', "overwrite project env file"),
    (rf'\b(cp|mv|install)\b.*\s["\']?{_SENSITIVE_WRITE_TARGET}[^\s"\']*["\']?{_COMMAND_TAIL}', "copy/move file into sensitive credential/SSH/shell-rc path"),
    (rf'\bsed\s+-[^\s]*i.*(?:{_USER_SENSITIVE_WRITE_TARGET})[^\s"\']*', "in-place edit of sensitive credential/SSH/shell-rc path"),
    (rf'\bsed\s+--in-place\b.*(?:{_USER_SENSITIVE_WRITE_TARGET})[^\s"\']*', "in-place edit of sensitive credential/SSH/shell-rc path (long flag)"),
    (rf'\b(?:perl|ruby)\b.*(?:^|\s)-[^\s]*i\b.*(?:{_USER_SENSITIVE_WRITE_TARGET})[^\s"\']*', "in-place edit of sensitive credential/SSH/shell-rc path (perl/ruby)"),
    (rf'\bsed\s+-[^\s]*i.*\s{_SYSTEM_CONFIG_PATH}', "in-place edit of system config"),
    (rf'\bsed\s+--in-place\b.*\s{_SYSTEM_CONFIG_PATH}', "in-place edit of system config (long flag)"),
    (rf'\b(?:{_SHELL_NAMES_RE})\s+<<', "shell execution via heredoc"),
    (r'\bgit\s+reset\s+--h(?:a(?:r(?:d)?)?)?\b', "git reset --hard (destroys uncommitted changes)"),
    (r'\bgit\s+push\b.*--forc[a-z]*\b', "git force push (rewrites remote history)"),
    (r'\bgit\s+push\b.*-f\b', "git force push short flag (rewrites remote history)"),
    (r'\bgit\s+clean\s+-[^\s]*f', "git clean with force (deletes untracked files)"),
    (r'\bgit\s+branch\s+(?-i:-D)\b', "git branch force delete"),
    (r'\bgit\s+branch\b[^;|&\n]*?(?:-d\b|--delete\b)[^;|&\n]*?(?:-f\b|--force\b)', "git branch force delete (long flags)"),
    (r'\bgit\s+branch\b[^;|&\n]*?(?:-f\b|--force\b)[^;|&\n]*?(?:-d\b|--delete\b)', "git branch force delete (long flags, force-first)"),
    (r'\bchmod\s+\+x\b.*[;&|]+\s*\./', "chmod +x followed by immediate execution"),
    (r'\bsudo\b[^;|&\n]*?\s+(?:-s\b|--st[a-z]*\b|-a\b|--a[a-z]*\b)', "sudo with privilege flag (stdin/askpass/shell/list)"),
    (r'\bsudo\b[^;|&\n]*?\s+-[a-z]*[sa][a-z]*\b', "sudo with combined-flag privilege escalation"),
    (_CMDPOS + r'npm\s+' + _PKG_OPTS + r'(?:uninstall|unlink|remove|rm|r|un)\b', "package manager uninstall"),
    (_CMDPOS + r'pnpm\s+' + _PKG_OPTS + r'(?:uninstall|remove|rm|un)\b', "package manager uninstall"),
    (_CMDPOS + r'yarn\s+' + _PKG_OPTS + r'(?:global\s+)?(?:uninstall|remove)\b', "package manager uninstall"),
    (_CMDPOS + r'pip(?:3)?\s+' + _PKG_OPTS + r'uninstall\b', "package manager uninstall"),
    (_CMDPOS + r'brew\s+' + _PKG_OPTS + r'(?:uninstall|remove|rm)\b', "package manager uninstall"),
]
_FLAGS = re.IGNORECASE | re.DOTALL
_HARDLINE = [(re.compile(p, _FLAGS), d) for p, d in HARDLINE]
_DANGEROUS = [(re.compile(p, _FLAGS), d) for p, d in DANGEROUS]


def normalize(command: str) -> str:
    """去掉终端控制字符、NUL、续行、反斜杠和空引号的障眼法、$IFS（Hermes 的 _normalize_command_for_detection，不改写家目录那两步）。"""
    command = unicodedata.normalize("NFKC", _ANSI.sub("", command).replace("\x00", ""))
    command = re.sub(r"\\\r?\n", "", command)
    command = re.sub(r"\\([^\n])", r"\1", command)
    command = re.sub(r"''|\"\"", "", command)
    return re.sub(r"\$\{IFS\b[^}]*\}|\$IFS\b", " ", command)


def _lower_flags(command: str) -> str:
    return "".join(t if t.startswith("-") else t.lower() for t in re.split(r"(\s+)", command))
# —— 改写自 Hermes 的到这里为止 ——


SHELLS = {"sh", "bash", "zsh", "dash", "ksh", "fish", "csh", "tcsh", "ash", "busybox"}
INLINE = {"python": "c", "node": "ep", "nodejs": "ep", "bun": "e", "deno": "", "perl": "eE", "ruby": "e", "php": "r", "lua": "e",
          "osascript": "e", "pwsh": "c", "powershell": "c", "rscript": "e", "tclsh": ""}
INLINE_LONG = {"--eval", "--print", "-command", "-encodedcommand", "--command"}
AWKS = {"awk", "gawk", "mawk", "nawk"}
WRAPPERS = {"env", "nice", "nohup", "timeout", "stdbuf", "time", "ionice", "taskset", "setsid", "flock", "unbuffer", "chrt", "xargs",
            "watch", "strace", "ltrace", "gdb", "script", "expect", "parallel", "nsenter", "unshare", "capsh"}
PRIVILEGE = {"sudo", "doas", "su", "pkexec", "chroot", "runuser", "machinectl", "systemd-run", "firejail"}
OWN = [(re.compile(r"\bdocker\b.*\b(rm|rmi|stop|kill|restart|prune|down)\b", _FLAGS), "docker: removes or stops containers (the sandboxes run there)"),
       (re.compile(r"\.openmousse(?:/|\b)", _FLAGS), "touches OpenMousse's own config or data")]


def _base(argv0: str) -> str:
    b = os.path.basename(argv0).lower()
    return "python" if re.fullmatch(r"python[0-9.]*", b) else b


def danger(c: Cmd) -> str | None:
    """为什么危险（一句英文）；不危险 → None。先看结构（包了一层、内联代码、提权），再按 Hermes 的名单查。"""
    b = _base(c.argv[0])
    args = c.argv[1:]
    if b in PRIVILEGE:
        return "runs a command as another user or with more privileges"
    if b in WRAPPERS:
        return "runs another program (the command after it is what really runs)"
    if "--" in args:
        args = args[: args.index("--")]
    # 选项带不带值分不清：参数里哪儿出现 -c / -e 都算（宁可多问；脚本自己的 -c 也会被当成内联代码）
    if b in SHELLS:
        if any(a.startswith("-") and not a.startswith("--") and ("c" in a or "s" in a) for a in args) or not [a for a in args if not a.startswith("-")]:
            return "shell command via -c (or read from standard input)"
    if b in AWKS:
        return "awk program (code in the arguments)"
    letters = INLINE.get(b)
    if letters is not None:
        for a in args:
            low = a.lower()
            if low in INLINE_LONG or low.startswith(("--eval=", "--print=")) or (b == "deno" and a == "eval"):
                return "script execution via -e/-c flag"
            if letters and a.startswith("-") and not a.startswith("--") and any(ch in a[1:] for ch in letters):
                return "script execution via -e/-c flag"
        if not [a for a in args if not a.startswith("-")]:
            return "interpreter reading code from standard input"
    if b == "find" and any(a in ("-exec", "-execdir", "-ok", "-okdir", "-delete") for a in args):
        return "find that runs commands or deletes"
    text = " ".join(c.argv)
    for variant in {normalize(c.raw), normalize(text)}:
        low = _lower_flags(variant)
        for rx, desc in _HARDLINE + _DANGEROUS:
            if rx.search(low):
                return desc
        for rx, desc in OWN:
            if rx.search(variant):
                return desc
    return None


# —— 跑 ————————————————————————————————————————————————————————————————————

SECRET_WORDS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL", "CREDS", "AUTH", "BEARER", "DSN", "WEBHOOK", "COOKIE")
DROP_ENV = {"BASH_ENV", "ENV", "IFS", "PS4", "PROMPT_COMMAND", "NODE_OPTIONS", "PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONINSPECT",
            "PERL5OPT", "PERL5LIB", "RUBYOPT", "JAVA_TOOL_OPTIONS", "_JAVA_OPTIONS", "GLIBC_TUNABLES", "GIT_EXTERNAL_DIFF", "GIT_SSH_COMMAND",
            "GIT_ASKPASS", "SSH_ASKPASS", "SUDO_ASKPASS", "LESSOPEN", "LESSCLOSE"}
DROP_PREFIX = ("LD_", "DYLD_", "BASH_FUNC_", "MOUSSE_", "OPENCLAW_", "CLAUDECODE", "CLAUDE_CODE")
URL_CRED = re.compile(r"://[^/\s:@]+:[^/\s@]+@")


def child_env(agent: str, keep: list[str] | None = None) -> dict[str, str]:
    """给命令的环境变量：像密钥的、能改程序行为的、我们自己的都去掉；keep 里的照传；PATH 只留绝对路径。"""
    from . import models
    out = {}
    for k, v in os.environ.items():
        up = k.upper()
        if any(w in up for w in SECRET_WORDS) or up in DROP_ENV or up.startswith(DROP_PREFIX) or URL_CRED.search(v or ""):
            continue
        out[k] = v
    for k in keep or []:
        val = os.environ.get(k) or models.env_value(k)
        if val:
            out[k] = val
    out["PATH"] = clean_path() or "/usr/local/bin:/usr/bin:/bin"
    out["MOUSSE_AGENT"] = agent
    out.setdefault("PYTHONIOENCODING", "utf-8")
    return out


def _killpg(proc, sig: int) -> None:
    try:
        os.killpg(proc.pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


async def _stop(proc) -> None:
    _killpg(proc, signal.SIGTERM)
    try:
        await asyncio.wait_for(proc.wait(), KILL_GRACE)
    except asyncio.TimeoutError:
        _killpg(proc, signal.SIGKILL)
        try:
            await asyncio.wait_for(proc.wait(), 5)
        except asyncio.TimeoutError:
            pass


async def execute(agent: str, c: Cmd, stdin: str | None, timeout: float, keep_env: list[str] | None = None) -> sandbox.Ran:
    """跑一次（不经 shell，自己一个进程组）。到点：整组收掉；被取消（用户点了停）：整组收掉再往外抛。"""
    timeout = max(1.0, float(timeout))
    async with SLOTS:
        t0 = time.monotonic()
        proc = await asyncio.create_subprocess_exec(c.exe, *c.argv[1:], cwd=str(c.cwd), env=child_env(agent, keep_env), start_new_session=True,
                                                    stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        cap = sandbox.Capture()

        async def feed() -> None:
            try:
                proc.stdin.write((stdin or "").encode("utf8"))
                await proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                try:
                    proc.stdin.close()
                except Exception:  # noqa: BLE001
                    pass

        async def pump() -> int:
            if stdin is not None:
                asyncio.ensure_future(feed())
            while True:
                chunk = await proc.stdout.read(65536)
                if not chunk:
                    break
                cap.add(chunk)
            return await proc.wait()
        task = asyncio.ensure_future(pump())
        timed_out = False
        try:
            code: int | None = await asyncio.wait_for(asyncio.shield(task), timeout)
        except asyncio.TimeoutError:
            timed_out = True
            await _stop(proc)
            code = await sandbox._settle(task)
        except asyncio.CancelledError:
            await asyncio.shield(_stop(proc))
            task.cancel()
            raise
        return sandbox.Ran(code, cap.text(), cap.size, max(0, cap.cut), int((time.monotonic() - t0) * 1000), timed_out=timed_out)


def render(res: sandbox.Ran) -> str:
    from .tools import wrap_untrusted
    out = res.output.rstrip("\n")
    secs = f"{res.ms / 1000:.1f}s"
    status = (f"[stopped after {secs}: it didn't finish within the timeout; everything it started was stopped]" if res.timed_out
              else f"[exit code {res.code} · {secs}]")
    if res.cut:
        status += f" [output was {res.size} bytes; the middle {res.cut} bytes were cut]"
    return f"{wrap_untrusted('host exec', out) if out else '(no output)'}\n{status}"


# —— 交卡时记下的指纹：程序、脚本文件 ——————————————————————————————————————————————————

def _sha(path: str) -> str | None:
    try:
        with open(path, "rb") as f:
            h = hashlib.sha256()
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def pins(c: Cmd) -> dict[str, str]:
    """要对一下的文件：程序本身，和后面第一个是文件的参数（解释器跑的脚本）。"""
    out = {}
    if (h := _sha(c.real)) is not None:
        out[c.real] = h
    for a in c.argv[1:]:
        if a.startswith("-"):
            continue
        p = _abs(a, c.cwd)
        if os.path.isfile(p):
            if (h := _sha(p)) is not None:
                out[os.path.realpath(p)] = h
        break
    return out


def changed(saved: dict[str, str]) -> str | None:
    for p, h in (saved or {}).items():
        if _sha(p) != h:
            return p
    return None


# —— 名单外的：交卡（这一次 / 以后都放行）、同意了照做、写回名单 ——————————————————————————————————

def _show(c: Cmd, ws: Path) -> str:
    s = c.raw.replace(str(ws) + "/", "").replace(str(ws), ".")
    return s if len(s) <= 300 else s[:299] + "…"


SAFE_TOKEN = re.compile(r"[\w@%+=:,./<>~-]+")


def always_pattern(c: Cmd, ws: Path) -> str | None:
    """「以后都放行」写进名单的那一条：一模一样的命令（工作区换成 <workspace>）；带通配符、... 的不给（会比这一条宽）。"""
    toks = []
    for i, a in enumerate(c.argv):
        if any(ch in a for ch in "*?") or a in ("...", "…") or "<workspace>" in a or "<工作区>" in a:
            return None
        if i == 0 and "/" in a:
            a = "<workspace>" + c.exe[len(str(ws)):] if c.exe.startswith(str(ws) + "/") else c.exe
        elif i > 0 and ("/" in a or a.startswith((".", "~"))):  # 路径：写成绝对的（工作区里的写 <workspace>/…），比的时候照样算
            p = _abs(a, c.cwd)
            a = "<workspace>" + p[len(str(ws)):] if p == str(ws) or p.startswith(str(ws) + "/") else p
        toks.append(a if SAFE_TOKEN.fullmatch(a) else shlex.quote(a))
    return " ".join(toks)


def add_to_allowlist(agent: str, pattern: str) -> bool:
    """写回 server.json：engine.host_exec.agents.<id>.allow 加这一条（已经有了不重复）。→ 加了没有。"""
    import config
    data = copy.deepcopy(config.raw(fresh=True))
    e = data.get("engine") if isinstance(data.get("engine"), dict) else {}
    h = e.get("host_exec") if isinstance(e.get("host_exec"), dict) else {}
    ags = h.get("agents") if isinstance(h.get("agents"), dict) else {}
    a = ags.get(agent)
    a = dict(a) if isinstance(a, dict) else {}
    allow = [str(x) for x in a.get("allow") or []] if isinstance(a.get("allow"), list) else []
    if pattern in allow:
        return False
    a["allow"] = allow + [pattern]
    ags[agent], h["agents"], e["host_exec"], data["engine"] = a, ags, h, e
    config.save(data)
    return True


def _fence(text: str) -> str:
    run = max((len(m) for m in re.findall(r"`+", text)), default=0)
    return "`" * max(3, run + 1)


def card_body(c: Cmd, stdin: str | None) -> str:
    """卡上写死的那条命令（全部参数、整段输入，一个字不截：同意了跑的就是这些）。"""
    return json.dumps({"argv": c.argv, "program": c.exe, "cwd": c.cwd_rel, **({"input": stdin} if stdin else {})}, ensure_ascii=False, indent=2)


def cards(agent: str, who: str, c: Cmd, ws: Path, why: str, risk: str | None, pattern: str | None, stdin: str | None) -> tuple[dict, dict | None]:
    shown = _show(c, ws)
    body = card_body(c, stdin)
    fence = _fence(body)
    because = (L(f"这条有风险（{risk}），这种即使在放行名单里也要你点头。", f"It's risky ({risk}), which always needs your OK, even when allowlisted.")
               if risk else L("它不在放行名单里，所以先问你。", "It isn't on the allowlist, so it asks you first."))
    changes = [L(f"命令：{shown}", f"Command: {shown}"), L(f"在哪跑：工作区 {c.cwd_rel}（{c.cwd}）", f"Where: workspace {c.cwd_rel} ({c.cwd})")]
    if risk:
        changes.append(L(f"为什么要问：{risk}", f"Why it asks: {risk}"))
    once = {"title": L(f"在服务器上跑：{shown[:60]}", f"Run on your server: {shown[:60]}"),
            "why": why or L(f"{who}想在你的服务器上跑这条命令。{because}", f"{who} wants to run this command on your server. {because}"),
            "changes": changes,
            "detail": L("同意 = 服务器照下面这条跑一次（不经 shell，在这个 Agent 的工作区里），结果发回对话。主机上的命令不在沙箱里，"
                        "能碰到这台服务器上这个用户能碰的一切。", "Approve = the server runs exactly this once (no shell, in this Agent's "
                        "workspace) and posts the result into the chat. Host commands aren't sandboxed: they can reach anything this "
                        "user can on the server.") + f"\n\n{fence}json\n{body}\n{fence}",
            "approveLabel": L("只这一次", "Just this once")}
    if not pattern:
        return once, None
    always = {"title": L(f"以后都放行：{pattern[:60]}", f"Always allow: {pattern[:60]}"),
              "why": L(f"和「只这一次」那张是同一条命令。点这张 = 现在跑一次，并且把它写进 {who}的放行名单（server.json 的 engine.host_exec），"
                       "以后一模一样的命令直接跑、不再问。", f"Same command as the \"Just this once\" card. Approving this one runs it now and adds it "
                       f"to {who}'s allowlist (engine.host_exec in server.json): the exact same command then runs without asking."),
              "changes": [L(f"放行名单加一条：{pattern}", f"Adds to the allowlist: {pattern}")],
              "detail": L("只放行这一条（参数一模一样，<workspace> 是这个 Agent 的工作区）。要放宽（比如换参数也行），在 server.json 里自己改成带 * 或 ... 的模式。"
                          "名单里的脚本以后 Agent 改不了（主机上的文件工具不许写）。",
                          "This allows exactly this command (same arguments; <workspace> is this Agent's workspace). To allow more (other "
                          "arguments, say), edit server.json yourself with * or .... Scripts on the allowlist become read-only to the Agent."),
              "approveLabel": L("以后都放行", "Always allow"), "level": "quiet"}
    return once, always


async def ask(tool: Tool, args: dict, ctx: ToolCtx) -> dict | str:
    """关卡说 ask 时：交卡（tools.submit_card）。写法不对、没开的、ask off 的不交卡，回一句不跑的原因。"""
    import inbox

    from . import tools as tools_mod
    agent = ctx.agent or "main"
    p, ws = policy(agent), workspace(agent)
    if p is None or ws is None:
        return L("没跑：这个 Agent 没开主机上的命令（server.json 的 engine.host_exec）。", "Not run: host commands aren't enabled for this Agent (engine.host_exec in server.json).")
    try:
        c = parse(agent, args.get("command"), args.get("cwd"))
    except ToolFailure as e:
        return str(e)
    risk = danger(c)
    if p.ask == "off":
        return (L(f"没跑：这条命令要你点头（{risk}），但这个 Agent 设了不问（ask: off）。跟用户说一声。", f"Not run: this needs the user's OK ({risk}), but approvals are off for this Agent (ask: off). Tell the user.")
                if risk else L("没跑：不在放行名单里，这个 Agent 又设了不问（ask: off）。跟用户说一声，要的话请他把这条加进名单。",
                               "Not run: it isn't on the allowlist and approvals are off for this Agent (ask: off). Tell the user; they can add it to the allowlist."))
    stdin = args.get("input") if isinstance(args.get("input"), str) and args.get("input") else None
    from .handkit import too_long
    if (no := too_long(card_body(c, stdin))) is not None:
        return no
    timeout = min(int(args.get("timeout") or p.timeout), p.max_timeout)
    pattern = None if risk or p.ask == "always" or p.security == "full" else always_pattern(c, ws)
    who = inbox.source_name(agent, inbox.names())
    once, always = cards(agent, who, c, ws, str(args.get("why") or "").strip(), risk, pattern, stdin)
    pid = uuid.uuid4().hex[:12]
    base = {"argv": c.argv, "cwd": c.cwd_rel, "program": c.real, "input": stdin, "timeout": timeout, "pins": pins(c), "pair": pid}
    key = hashlib.sha256(json.dumps([agent, c.argv, c.cwd_rel, stdin or ""], ensure_ascii=False).encode()).hexdigest()[:32]
    res = await tools_mod.submit_card(NAME, json.dumps({**base, "mode": "once"}, ensure_ascii=False), stdin, once, f"hostexec:{key}", ctx, who=agent)
    if always is not None and isinstance(res, dict):
        akey = hashlib.sha256(json.dumps([agent, pattern], ensure_ascii=False).encode()).hexdigest()[:32]
        extra = await tools_mod.submit_card(NAME, json.dumps({**base, "mode": "always", "pattern": pattern}, ensure_ascii=False), stdin, always,
                                            f"hostexec-always:{akey}", ctx, who=agent)
        if isinstance(extra, dict) and extra.get("inbox"):
            res = {**res, "message": res["message"] + " " + L(f"（另有一张「以后都放行」的卡 {extra['inbox']}：用户可以选只这一次，或者以后都放行。）",
                                                            f"(There's also an \"always allow\" card {extra['inbox']}: the user can allow it just this once, or from now on.)")}
    return res


def _calls_of_pair(pid: str) -> list:
    from . import store
    with store.read() as conn:
        rows = conn.execute("SELECT * FROM engine_calls WHERE tool=? AND args LIKE ?", (NAME, f'%"pair": "{pid}"%')).fetchall()
    return list(rows)


def _others(row) -> list:
    """同一条命令的另一张卡背后的那次调用（engine_calls 的行）。"""
    try:
        pid = json.loads(row["args"]).get("pair")
    except ValueError:
        return []
    return [o for o in _calls_of_pair(pid) if o["id"] != row["id"]] if pid else []


def _take(others: list, status: str) -> list:
    """另一张卡的调用先定下来：只改还在等的（pending → status，原子的：两张卡点得再快也只跑一次）。→ 改到了的那几行。"""
    from . import store
    got = []
    with store.tx() as conn:
        for o in others:
            if conn.execute("UPDATE engine_calls SET status=?, decided_at=? WHERE id=? AND status='pending'",
                            (status, store.now_iso(), o["id"])).rowcount == 1:
                got.append(o)
    return got


def _ran(others: list) -> bool:
    """另一张卡已经在跑、跑过了这条命令。"""
    from . import tools as tools_mod
    return any((cur := tools_mod.call_of(o["inbox"])) is not None and cur["status"] in ("running", "done") for o in others if o["inbox"])


async def _withdraw(others: list) -> None:
    """另一张卡在收件箱里还开着的撤回（engine_calls 那边先定好了：tools.on_decided 不会再当成「用户没同意」去告诉对外接口的会话）。"""
    import inbox
    from fastapi import HTTPException
    for o in others:
        if not o["inbox"]:
            continue
        try:
            if inbox.item(o["inbox"]).get("status") in ("pending", "revising"):
                await inbox.withdraw(o["inbox"])
        except HTTPException:
            pass


async def approved(row, it: dict) -> str:
    """用户点了同意（tools.on_decided 交过来）：再查一遍、对指纹，「以后都放行」先写回名单，然后照这条跑一次。→ 给对话的结果。
    另一张卡：点的是「只这一次」→ 那张「以后都放行」作废（撤回）；点的是「以后都放行」→「只这一次」那张背后是同一次调用，跑的时候
    占住、跑完记上同样的结果再撤回（对外接口的关卡正等着它：engine/openai_api.py 的 decision 看到的就是真的结果）。"""
    a = json.loads(row["args"])
    agent = row["agent"]
    others = _others(row)
    if _ran(others):
        raise ToolFailure(L("这条命令已经从另一张卡照做了，没有再跑", "This command was already run from the other card, so it wasn't run again"))
    p = policy(agent)
    if p is None:
        raise ToolFailure(L("这个 Agent 的主机命令已经关了，没有跑", "Host commands are now off for this Agent, so nothing was run"))
    c = parse(agent, a.get("argv") or [], a.get("cwd"))
    if c.real != a.get("program"):
        raise ToolFailure(L(f"程序变了（现在是 {c.real}），和卡上的对不上，没有跑", f"The program changed (it's now {c.real}), so it no longer matches the card; nothing was run"))
    if (moved := changed(a.get("pins") or {})) is not None:
        raise ToolFailure(L(f"{moved} 在交卡以后被改过，没有跑：要跑请让它重新交一次", f"{moved} changed after the card was sent, so nothing was run; ask again to run it"))
    always = a.get("mode") == "always"
    held = _take(others, "running" if always else "withdrawn")
    if _ran([o for o in others if o not in held]):
        raise ToolFailure(L("这条命令已经从另一张卡照做了，没有再跑", "This command was already run from the other card, so it wasn't run again"))
    if not always:
        await _withdraw(held)
    from . import tools as tools_mod
    try:
        if always and a.get("pattern"):
            add_to_allowlist(agent, a["pattern"])
        res = await execute(agent, c, a.get("input"), float(a.get("timeout") or p.timeout), p.env)
    except BaseException as e:
        if always:
            for o in held:
                tools_mod._set(o["id"], "failed", (str(e) or type(e).__name__)[:tools_mod.RESULT_MAX])
            await _withdraw(held)
        raise
    added = L(f"\n（已写进放行名单：{a['pattern']}）", f"\n(Added to the allowlist: {a['pattern']})") if always else ""
    text = render(res) + added
    if always:
        for o in held:
            tools_mod._set(o["id"], "done", text if len(text) <= tools_mod.RESULT_MAX else text[:tools_mod.RESULT_MAX] + "…")
        await _withdraw(held)
    return text


async def closed(row, it: dict, action: str) -> None:
    """拒了「只这一次」：「以后都放行」那张也作废、撤回（拒了「以后都放行」不动另一张）。"""
    try:
        mode = json.loads(row["args"]).get("mode")
    except ValueError:
        return
    if action == "reject" and mode == "once":
        others = _others(row)
        _take(others, "withdrawn")
        await _withdraw(others)


# —— 工具 ——————————————————————————————————————————————————————————————————

def judge(agent: str, args: dict) -> tuple[str, str]:
    """→ (read / undo / ask / off, 一句理由)。写法不对的也算 ask：关卡交给 ask()，它不交卡、回原因。"""
    p = policy(agent)
    if p is None:
        return OFF, "host commands aren't enabled for this Agent"
    try:
        c = parse(agent, args.get("command"), args.get("cwd"))
    except ToolFailure as e:
        return ASK, str(e)
    if (risk := danger(c)) is not None:
        return ASK, L(f"危险的命令（{risk}）：即使在名单里也要用户点头", f"Risky command ({risk}): always needs the user's OK")
    if p.ask == "always":
        return ASK, L("这个 Agent 设了每条主机命令都先问", "This Agent asks before every host command")
    if p.security == "full":
        return UNDO, L("主人设了不看名单（security: full）；撤不撤得回看命令自己", "The owner set security: full (no allowlist); undoing depends on the command")
    if (hit := allowed(agent, c)) is not None:
        return UNDO, L(f"在主人写的放行名单里（{hit.text}），等于事先点过头；撤不撤得回看命令自己",
                       f"On the owner's allowlist ({hit.text}): approved in advance; undoing depends on the command")
    return ASK, L("不在放行名单里：先问用户（可以只这一次，也可以以后都放行）", "Not on the allowlist: asks the user (just this once, or from now on)")


def tool(agent: str) -> Tool | None:
    p = policy(agent)
    if p is None or workspace(agent) is None:
        return None

    async def run(args: dict, ctx: ToolCtx) -> str:
        who = ctx.agent or "main"
        q = policy(who)
        if q is None:
            raise ToolFailure(L("主机上的命令没开", "Host commands aren't enabled"))
        c = parse(who, args.get("command"), args.get("cwd"))
        stdin = args.get("input") if isinstance(args.get("input"), str) and args.get("input") else None
        if stdin and len(stdin) > INPUT_MAX:
            raise ToolFailure(L(f"标准输入最多 {INPUT_MAX} 字", f"Standard input is limited to {INPUT_MAX} characters"))
        timeout = min(int(args.get("timeout") or q.timeout), q.max_timeout)
        return render(await execute(who, c, stdin, timeout, q.env))

    allow = "; ".join(f"`{x}`" for x in p.allow) if p.allow else L("（空的）", "(empty)")
    return Tool(name=NAME, kinds=("owner",), run=run, timeout=p.max_timeout + 30, max_output=40_000,
                title=lambda a: " ".join(str(a.get("command") or "").split())[:60],
                classify=lambda a: judge(agent, a),
                description=L(
                    f"在用户的服务器上（不是沙箱）跑一个程序，工作目录是你的工作区。不经 shell：管道、重定向、&&、$VAR、通配符都不认，要这些用 "
                    f"sandbox_exec。放行名单里的直接跑：{allow}；别的会给用户发卡（他可以只这一次，也可以以后都放行），调用马上返回。"
                    "危险的（删一大片、改权限、下载了就跑、包了一层 sudo / env / bash -c 的……）一定要点头。超时默认 "
                    f"{p.timeout} 秒、最多 {p.max_timeout} 秒；输出太长会截掉中间。",
                    f"Run one program on the user's server (not the sandbox), in your workspace. No shell: pipes, redirects, &&, $VAR and "
                    f"globs don't work (use sandbox_exec for those). Allowlisted commands run right away: {allow}. Anything else sends the "
                    "user an approval card (just this once, or from now on) and returns at once. Risky commands (mass deletes, permission "
                    f"changes, download-and-run, anything wrapped in sudo / env / bash -c…) always need their OK. Timeout {p.timeout} s by "
                    f"default, {p.max_timeout} s at most; very long output has its middle cut."),
                schema={"type": "object", "required": ["command"], "properties": {
                    "command": {"type": "string", "description": L("一条命令：程序 + 参数（照 shell 的规矩加引号，但不会经 shell）",
                                                                   "One command: the program and its arguments (quote like a shell, but no shell runs it)")},
                    "cwd": {"type": "string", "description": L("在工作区的哪个子文件夹里跑（默认工作区）", "Subfolder of your workspace to run in (default: the workspace)")},
                    "input": {"type": "string", "description": L("给程序的标准输入（不写就没有）", "Standard input for the program (none if left out)")},
                    "timeout": {"type": "integer", "description": L("几秒后停掉", "Seconds before it's stopped")},
                    "why": {"type": "string", "description": L("要用户点头时写：一句话说这次做什么、为什么（写在卡上）",
                                                               "When it needs the user's OK: one sentence on what this does and why (shown on the card)")}}})
