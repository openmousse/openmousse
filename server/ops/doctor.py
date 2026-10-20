"""mousse doctor：体检（第 7 批，14.2，2026-10-19）。服务器没开着也能跑。

  mousse doctor [--json] [--fix] [--yes] [--strict]

查什么（一节一节，每一项 ✓ 正常 / ! 要留意 / ✗ 不通 / · 只是说一声，带一句话和怎么修）：
  系统        Python 版本、server/requirements.txt 的依赖、磁盘剩余、沙箱的 Docker（只看在不在）
  安装        ~/.openmousse/repo 软链、venv、mousse 在不在 PATH 上
  配置        server.json 读不读得了（整份校验见 mousse config validate）、claw 段
  每一层      模型、搜索、记忆、向量、连接、手：直接调 engine/setup_api.py 的总览（和 app 的设置、setup_ctl.py status 同一处）
  命令行登录  Claude Code、Codex 在不在、登录了没（照 engine/runners.py：起它们自己的 auth status / login status，不读令牌）、ChatGPT 套餐
  数据        data_dir、密钥文件、server.json、ChatGPT 套餐的凭据目录、世界树配置的权限；SQLite 能打开、PRAGMA quick_check；
              记忆检索的 FTS 索引（integrity-check）；server.json 里过期的配对码
  世界树      包装了没、建了没
  服务        systemd / launchd 在不在、服务在不在跑、日结的定时器、/api/health 通不通（跑的是不是仓库里的代码）
  版本        上一次 mousse update --check 存下的结果（不联网）
--fix 只修撤得回、安全的：收紧权限（0600 / 0700）、补建缺的目录、重建 ~/.openmousse/repo 软链、重建 FTS 索引、清过期的配对码
  （改 server.json 先备份）；修完再查一遍那一项。要停、要重启服务的修法要再加 --yes，没有就只说要跑什么。
--json：照 OpenClaw 的 health contract（docs/cli/doctor/health-contract.md）给结构化结果：每一项 id、section、status（ok | warn | error |
  info）、title、detail、fix、fixable、needsYes、fixed、note。
退出码：没有 ✗ = 0（--strict 时有 ! 也算 1）；有 ✗ = 1。
仓库根目录的 check.sh 留着：它只用系统自带的 python3，venv 坏了也能跑；这里是 venv 里更全的一个，两边说法对齐。
"""
from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import os
import re
import shutil
import sqlite3
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from i18n import L

from . import common as C

INSTALL = "curl -fsSL https://openmousse.ai/install | bash"
SETUP = f"{C.PY} {C.REPO_TILDE}/server/setup_ctl.py"
PIP = f"{C.PY} -m pip install -r {C.REPO_TILDE}/server/requirements.txt"
STATUSES = ("ok", "warn", "error", "info")


@dataclass
class Check:
    id: str
    status: str                 # ok | warn | error | info
    title: str
    detail: str = ""
    fix: str = ""               # 怎么修：一条命令或一句话
    fixable: bool = False       # --fix 能替你修（撤得回、安全的）
    needs_yes: bool = False     # 这个修法要停 / 重启服务：--fix 还要 --yes
    repair: Callable[[], str] | None = field(default=None, repr=False, compare=False)  # 修 → 一句话（修了什么）；修不成抛错
    section: str = ""
    fixed: bool = False
    note: str = ""              # --fix 之后的一句：修了什么、为什么没修
    recheck: Callable[[Ctx], list[Check]] | None = field(default=None, repr=False, compare=False)  # 修完再查一遍用的（出这一项的那个函数）

    @property
    def bad(self) -> bool:
        return self.status in ("warn", "error")

    def to_json(self) -> dict:
        return {"id": self.id, "section": self.section, "status": self.status, "title": self.title, "detail": self.detail, "fix": self.fix,
                "fixable": self.fixable, "needsYes": self.needs_yes, "fixed": self.fixed, "note": self.note}


@dataclass
class Ctx:
    cfg: dict                   # 读得了的 server.json（读不了 = {}）
    problem: str | None         # common.load_config 的问题：None | missing | unreadable | broken


def ctx() -> Ctx:
    data, problem = C.load_config()
    return Ctx(data or {}, problem)


# —— 系统 ——————————————————————————————————————————————————————————————————————————

def requirements() -> list[tuple[str, str]]:
    """server/requirements.txt → [(包名, 最低版本或 "")]。"""
    out = []
    try:
        lines = (C.REPO / "server" / "requirements.txt").read_text(encoding="utf8").splitlines()
    except OSError:
        return out
    for line in lines:
        line = line.split("#", 1)[0].strip()
        m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?\s*(?:>=\s*([0-9][0-9.]*))?", line)
        if m:
            out.append((m.group(1), m.group(2) or ""))
    return out


def _vt(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v)[:4])


def check_python(_c: Ctx) -> list[Check]:
    v = sys.version.split()[0]
    good = sys.version_info >= (3, 11)
    return [Check("system.python", "ok" if good else "error", "Python", v, "" if good else L("要 3.11 以上（重跑安装命令会建新的 venv）",
                                                                                             "needs 3.11 or newer (rerunning the installer builds a new venv)"))]


def check_deps(_c: Ctx) -> list[Check]:
    missing, old = [], []
    for name, least in requirements():
        try:
            have = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            missing.append(name)
            continue
        if least and _vt(have) < _vt(least):
            old.append(f"{name} {have} < {least}")
    if missing or old:
        detail = L("缺：", "missing: ") + ", ".join(missing) if missing else ""
        detail += ("; " if detail and old else "") + (L("太旧：", "too old: ") + ", ".join(old) if old else "")
        return [Check("system.deps", "error", L("Python 依赖不全", "Python dependencies incomplete"), detail, PIP)]
    return [Check("system.deps", "ok", L("Python 依赖", "Python dependencies"), L(f"{len(requirements())} 个都在", f"all {len(requirements())} installed"))]


def check_disk(_c: Ctx) -> list[Check]:
    try:
        free = shutil.disk_usage(C.home()).free / 1e9
    except OSError as e:
        return [Check("system.disk", "warn", L("磁盘剩余", "Free disk"), str(e))]
    status = "ok" if free >= 5 else "warn" if free >= 1 else "error"
    return [Check("system.disk", status, L("磁盘剩余", "Free disk"), f"{free:.1f} GB",
                  "" if status == "ok" else L("清一清空间：数据库、上传的文件、备份都在家目录", "free some space: the database, uploads and backups live in your home directory"))]


def check_docker(c: Ctx) -> list[Check]:
    exe = shutil.which("docker")
    if exe:
        return [Check("system.docker", "ok", L("Docker（沙箱用）", "Docker (for the sandbox)"), C.tilde(exe))]
    e = c.cfg.get("engine") if isinstance(c.cfg.get("engine"), dict) else {}
    want = isinstance(e.get("sandbox"), dict)
    return [Check("system.docker", "warn" if want else "info", L("Docker（沙箱用）", "Docker (for the sandbox)"),
                  L("没装：沙箱用不了", "not installed: the sandbox can't run") + ("" if want else L("（不用沙箱可以不管）", " (fine if you don't use it)")),
                  L("装 Docker（docs.docker.com/engine/install），再起一次服务", "install Docker (docs.docker.com/engine/install), then restart the server") if want else "")]


# —— 安装 ——————————————————————————————————————————————————————————————————————————

def _relink() -> str:
    link = C.repo_link()
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.is_symlink():
        link.unlink()
    link.symlink_to(C.REPO)
    return f"{C.tilde(link)} → {C.tilde(C.REPO)}"


def check_repo(_c: Ctx) -> list[Check]:
    link = C.repo_link()
    title = L("仓库软链", "Repository link")
    fix = L(f"重建：ln -sfn {C.tilde(C.REPO)} {C.tilde(link)}", f"recreate it: ln -sfn {C.tilde(C.REPO)} {C.tilde(link)}")
    if link.is_symlink() or link.exists():
        target = link.resolve()
        if (target / "server" / "run.py").is_file():
            same = target == C.REPO.resolve()
            return [Check("install.repo", "ok", title, f"{C.tilde(link)} → {C.tilde(target)}" + ("" if same else L(
                f"（这个 mousse 跑的是 {C.tilde(C.REPO)}）", f" (this mousse runs from {C.tilde(C.REPO)})")))]
        if link.is_symlink():
            return [Check("install.repo", "warn", title, L(f"{C.tilde(link)} 指向的地方不在了", f"{C.tilde(link)} points nowhere"), fix,
                          fixable=True, repair=_relink)]
        return [Check("install.repo", "warn", title, L(f"{C.tilde(link)} 不是软链，也不是仓库", f"{C.tilde(link)} is neither a link nor the repository"),
                      L("挪开它，再跑一遍安装命令", "move it aside, then rerun the installer"))]
    return [Check("install.repo", "warn", title, L(f"{C.tilde(link)} 不在（skills 里的命令走这个路径）", f"{C.tilde(link)} is missing (the skills' commands use it)"),
                  fix, fixable=True, repair=_relink)]


def check_venv(_c: Ctx) -> list[Check]:
    py = C.venv() / "bin" / "python"
    if py.exists():
        return [Check("install.venv", "ok", "venv", C.tilde(C.venv()))]
    if sys.prefix != sys.base_prefix:
        return [Check("install.venv", "info", "venv", L(f"没有 {C.tilde(C.venv())}；现在用的是 {C.tilde(sys.prefix)}",
                                                        f"no {C.tilde(C.venv())}; this runs in {C.tilde(sys.prefix)}"))]
    return [Check("install.venv", "warn", "venv", L(f"{C.tilde(C.venv())} 不在", f"{C.tilde(C.venv())} is missing"),
                  L(f"再跑一遍安装命令：{INSTALL}", f"rerun the installer: {INSTALL}"))]


def check_cli(_c: Ctx) -> list[Check]:
    exe = shutil.which("mousse")
    if exe:
        return [Check("install.cli", "ok", L("mousse 命令", "mousse command"), C.tilde(exe))]
    return [Check("install.cli", "info", L("mousse 命令", "mousse command"), L("不在 PATH 上", "not on PATH"),
                  f"ln -s {C.REPO_TILDE}/bin/mousse ~/.local/bin/mousse")]


# —— 配置 ——————————————————————————————————————————————————————————————————————————

def check_config_file(c: Ctx) -> list[Check]:
    p = C.config_path()
    title = "server.json"
    if c.problem == "missing":
        return [Check("config.file", "error", title, L(f"{C.tilde(p)} 不在", f"{C.tilde(p)} is missing"), L(f"跑一遍安装命令：{INSTALL}", f"run the installer: {INSTALL}"))]
    if c.problem == "unreadable":
        return [Check("config.file", "error", title, L(f"{C.tilde(p)} 读不了", f"can't read {C.tilde(p)}"), f"ls -l {C.tilde(p)}")]
    if c.problem == "broken":
        baks = C.config_backups()
        fix = (L(f"拿最近的备份换回去：cp {C.tilde(baks[0])} {C.tilde(p)}", f"put the latest backup back: cp {C.tilde(baks[0])} {C.tilde(p)}") if baks
               else L(f"照 {C.REPO_TILDE}/server/server.example.json 改好", f"fix it, using {C.REPO_TILDE}/server/server.example.json as a guide"))
        return [Check("config.file", "error", title, L("写坏了（不是一个 JSON 对象）", "broken (not a JSON object)"), fix)]
    b = c.cfg.get("bind") if isinstance(c.cfg.get("bind"), dict) else {}
    a = c.cfg.get("auth") if isinstance(c.cfg.get("auth"), dict) else {}
    n = len(a.get("tokens") or {}) if isinstance(a.get("tokens"), dict) else 0
    return [Check("config.file", "ok", title, L(f"监听 {b.get('host') or '127.0.0.1'}:{b.get('port') or 8080} · 令牌 {n} 个",
                                                f"listening on {b.get('host') or '127.0.0.1'}:{b.get('port') or 8080} · {n} tokens"))]


def check_config_valid(c: Ctx) -> list[Check]:
    """整份校验（ops/confcheck.py）：类型不对、取值不在范围里 = ✗；不认识的键只说一声（新版本、别的会话加的键）。"""
    if c.problem:
        return []
    from . import confcheck
    found = confcheck.validate(c.cfg)
    bad, unknown = confcheck.errors(found), [f for f in found if f.level == "warn"]
    title = L("server.json 的内容", "server.json contents")
    if bad:
        detail = "; ".join(f"{f.path}: {f.message}" for f in bad[:3]) + (L(f"……共 {len(bad)} 处", f" … {len(bad)} in all") if len(bad) > 3 else "")
        return [Check("config.valid", "error", title, detail, "mousse config validate")]
    if unknown:
        return [Check("config.valid", "info", title, L(f"都对；{len(unknown)} 个不认识的键（{', '.join(f.path for f in unknown[:3])}）：新版本加的、或者拼错了",
                                                      f"valid; {len(unknown)} unknown keys ({', '.join(f.path for f in unknown[:3])}): added by a newer version, or a typo"),
                      "mousse config validate")]
    return [Check("config.valid", "ok", title, L("整份校验过了", "the whole file validates"))]


def check_claw(c: Ctx) -> list[Check]:
    if c.problem in ("broken", "unreadable"):
        return []
    raw = c.cfg.get("claw")
    title = "claw"
    if raw is None:
        oc_home = Path(str(c.cfg.get("openclaw_home") or C.home() / ".openclaw")).expanduser()
        if (oc_home / "openclaw.json").exists():
            return [Check("config.claw", "ok", title, "OpenClaw")]
        return [Check("config.claw", "error", title, L(f"OpenClaw（{C.tilde(oc_home / 'openclaw.json')} 不在）", f"OpenClaw ({C.tilde(oc_home / 'openclaw.json')} is missing)"),
                      L("装好 OpenClaw（openclaw onboard），或者重跑安装命令选自带的内核（builtin）",
                        "set up OpenClaw (openclaw onboard), or rerun the installer and pick the built-in engine (builtin)"))]
    if not isinstance(raw, dict):
        return [Check("config.claw", "error", title, L("claw 段不是一个对象", "the claw section isn't an object"), L('写成 {"kind": "builtin"}', 'write it as {"kind": "builtin"}'))]
    kind = str(raw.get("kind") or "openclaw").strip().lower()
    if kind == "builtin":
        return [Check("config.claw", "ok", title, L("自带的内核（builtin）", "the built-in engine (builtin)"))]
    if kind == "openai":
        if not str(raw.get("url") or "").strip():
            return [Check("config.claw", "error", title, L("别的 claw，但没写地址（claw.url）", "another claw, but claw.url is missing"),
                          L("在 server.json 的 claw 段写上 url（…/v1）", "add url (…/v1) to the claw section of server.json"))]
        return [Check("config.claw", "ok", title, f"{raw.get('name') or 'claw'} · {raw.get('url')}")]
    if kind == "openclaw":
        return check_claw(Ctx({k: v for k, v in c.cfg.items() if k != "claw"}, c.problem))
    return [Check("config.claw", "error", title, L(f"不认识的 kind：{kind}", f"unknown kind: {kind}"), L("写 builtin、openclaw 或 openai", "use builtin, openclaw or openai"))]


# —— 每一层（engine/setup_api.py 的总览）——————————————————————————————————————————————

LAYERS = (("models", ("模型", "Models")), ("search", ("搜索", "Search")), ("memory", ("记忆", "Memory")), ("embeddings", ("向量", "Embeddings")),
          ("connections", ("连接", "Connections")), ("hands", ("手", "Hands")))


def overview() -> dict:
    """setup_api 总览的那六层（直接调它每一层的函数，不经 HTTP；不调总览本身：它顺带看 first_run，会在新机器上把库建出来）。"""
    from engine import setup_api as sa
    fns = {"models": sa.models_layer, "search": sa.search_layer, "memory": sa.memory_layer, "embeddings": sa.embeddings_layer,
           "connections": sa.connections_layer, "hands": sa.hands_layer}
    return {"layers": {k: sa._layer(fn) for k, fn in fns.items()}}  # noqa: SLF001 — 一层出错只写那一层（同总览）


def check_layers(c: Ctx) -> list[Check]:
    if c.problem in ("broken", "unreadable"):
        return [Check("layers", "warn", L("每一层", "Layers"), L("server.json 读不了，先修它", "server.json can't be read; fix it first"))]
    try:
        o = overview()
    except Exception as e:  # noqa: BLE001 — 依赖缺了、配置写坏了：这一节说一声，别的照查
        return [Check("layers", "error", L("读不到每一层的状态", "Couldn't read the layers"), f"{type(e).__name__}: {C.scrub(str(e))[:160]}", PIP)]
    out = []
    for key, (zh, en) in LAYERS:
        layer = o.get("layers", {}).get(key)
        if not isinstance(layer, dict):
            continue
        s = str(layer.get("status") or "")
        status = {"ok": "ok", "external": "ok", "off": "info", "optional": "info", "warn": "warn",
                  "missing": "error" if key == "models" else "warn"}.get(s, "warn")
        fix = ""
        if status in ("warn", "error") and key in ("models", "search", "memory", "embeddings"):
            fix = f"{SETUP} status" if key != "models" else SETUP
        out.append(Check(f"layer.{key}", status, L(zh, en), str(layer.get("line") or ""), fix))
    return out


# —— 命令行登录（照 engine/runners.py 起它们自己的命令查，不读令牌）——————————————————————————

def check_logins(c: Ctx) -> list[Check]:
    if c.problem in ("broken", "unreadable"):
        return []
    try:
        from engine import catalog, models, runners
    except Exception as e:  # noqa: BLE001
        return [Check("login", "error", L("查不了命令行登录", "Couldn't check the sign-ins"), f"{type(e).__name__}", PIP)]
    chat = models.default_model()
    out = []
    for name, title, login in (("claude-code", "Claude Code", L("在终端里跑 claude，自己登录一次（/login）", "run claude in a terminal and sign in (/login)")),
                               ("codex", "Codex", "codex login")):
        exe = runners.bin_of(name)
        if not exe:
            out.append(Check(f"login.{name}", "info", title, L("没装（可选）", "not installed (optional)"), catalog.text(catalog.LOGINS[name]["how"]) or ""))
            continue
        if not runners.enabled(name):
            out.append(Check(f"login.{name}", "info", title, L("关着（tasks.runners）", "turned off (tasks.runners)")))
            continue
        info = runners.detect(name)  # 起它自己的 auth status / login status：登没登录、登录的是套餐还是密钥，别的一概不读
        needed = models.subscription(chat) == name
        if info:
            billing = {"subscription": L("套餐", "plan"), "api_key": L("API 密钥", "API key")}.get(str(info.get("billing")), str(info.get("billing") or ""))
            out.append(Check(f"login.{name}", "ok", title, L(f"登录了（{billing}）", f"signed in ({billing})")))
        else:
            out.append(Check(f"login.{name}", "error" if needed else "warn", title,
                             L("装了，没登录", "installed, not signed in") + (L("：主对话用的就是它", ": the main chat uses it") if needed else ""), login))
    try:
        from engine import chatgpt_plan
        st = chatgpt_plan.status()
    except Exception:  # noqa: BLE001
        st = None
    if st and st.get("enabled"):
        title = L("ChatGPT 套餐", "ChatGPT plan")
        needed = models.subscription(chat) == "chatgpt"
        if st.get("needsReconnect"):
            out.append(Check("login.chatgpt", "error" if needed else "warn", title, L("要重新连一次", "needs to be connected again"),
                             L("app 的设置里点 Continue with ChatGPT", "tap Continue with ChatGPT in the app's settings")))
        elif st.get("connected"):
            out.append(Check("login.chatgpt", "ok", title, L("连着", "connected") + ("" if st.get("planEnabled") else L("（没开用套餐）", " (plan use is off)"))))
        else:
            out.append(Check("login.chatgpt", "error" if needed else "info", title, L("没连（可选）", "not connected (optional)"),
                             L("app 的设置里点 Continue with ChatGPT", "tap Continue with ChatGPT in the app's settings")))
    return out + _cli_models(chat)


def _cli_models(chat: str | None) -> list[Check]:
    """codex/…、copilot/… 当对话模型（engine/provider_cli.py，默认关）：主对话用着它却没开；copilot 开了却没装（它没有查登录的命令，只看装了没有）。"""
    try:
        from engine import models, provider_cli
    except Exception:  # noqa: BLE001
        return []
    out = []
    sub = models.subscription(chat)
    if sub in provider_cli.NAMES and not provider_cli.enabled(sub):
        out.append(Check(f"login.{sub}.off", "error", provider_cli.NAMES[sub], L(f"主对话用的是 {chat}，可 engine.{sub} 没开",
                                                                              f"the main chat uses {chat}, but engine.{sub} is off"),
                         f"mousse config set engine.{sub} true"))
    if provider_cli.enabled("copilot"):
        title = provider_cli.NAMES["copilot"]
        if provider_cli.bin_of("copilot"):
            out.append(Check("login.copilot", "ok", title, L("装了（登录了没有它自己查不了：第一次回复就知道）",
                                                             "installed (it can't report whether you're signed in; the first reply will tell)")))
        else:
            out.append(Check("login.copilot", "error" if sub == "copilot" else "warn", title, L("开了，没装", "turned on, not installed"),
                             L("装 GitHub Copilot CLI（copilot），或在 engine.copilot.bin 写它在哪", "install the GitHub Copilot CLI (copilot), "
                               "or set engine.copilot.bin")))
    return out


# —— 数据：目录、权限、数据库、FTS、配对码 ——————————————————————————————————————————————

def _mkdir(p: Path) -> Callable[[], str]:
    def go() -> str:
        p.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(p, 0o700)
        return L(f"建了 {C.tilde(p)}（0700）", f"created {C.tilde(p)} (0700)")
    return go


def _chmod(p: Path, mode: int) -> Callable[[], str]:
    def go() -> str:
        os.chmod(p, mode)
        return f"chmod {mode:o} {C.tilde(p)}"
    return go


def unknown(c: Ctx) -> bool:
    """server.json 读不了：data_dir 这些在哪不知道（默认的位置不一定对），别在那儿建东西、查东西。"""
    return c.problem in ("broken", "unreadable")


def check_data_dir(c: Ctx) -> list[Check]:
    if unknown(c):
        return []
    d = C.data_dir(c.cfg)
    title = L("数据目录", "Data directory")
    if not d.exists():
        return [Check("data.dir", "warn", title, L(f"{C.tilde(d)} 不在（服务第一次起来时也会建）", f"{C.tilde(d)} is missing (the server also creates it on first start)"),
                      f"mkdir -m 700 -p {C.tilde(d)}", fixable=True, repair=_mkdir(d))]
    m = C.mode_of(d) or 0
    if m & 0o077:
        return [Check("data.dir", "warn", title, L(f"{C.tilde(d)} 别的用户也能进（{m:o}）：里面是你的对话和记忆", f"{C.tilde(d)} is open to other users ({m:o}): it holds your chats and memories"),
                      f"chmod 700 {C.tilde(d)}", fixable=True, repair=_chmod(d, 0o700))]
    return [Check("data.dir", "ok", title, f"{C.tilde(d)} ({m:o})")]


def perm_targets(cfg: dict) -> list[tuple[str, str, Path, int]]:
    """要收紧权限的：(id, 给人看的名字, 路径, 应该的权限)。目录 0700、文件 0600。"""
    return [("perm.secrets", L("密钥文件 secrets.env", "Key file secrets.env"), C.secrets_path(cfg), 0o600),
            ("perm.config", "server.json", C.config_path(), 0o600),
            ("perm.db", L("数据库 mousse.db", "Database mousse.db"), C.db_path(cfg), 0o600),
            ("perm.chatgpt", L("ChatGPT 套餐的凭据", "ChatGPT plan credentials"), C.chatgpt_dir(cfg), 0o700),
            ("perm.tree", L("世界树的配置（各平台的令牌）", "Memory tree config (platform tokens)"), C.tree_home() / "config.json", 0o600)]


def check_perms(c: Ctx) -> list[Check]:
    out = []
    for cid, title, p, want in perm_targets(c.cfg):
        if unknown(c) and cid != "perm.config":
            continue
        m = C.mode_of(p)
        if m is None:
            continue  # 没有这个文件：没用过，不算毛病
        if m & 0o077:
            world = bool(m & 0o004) and cid in ("perm.secrets", "perm.config", "perm.chatgpt", "perm.tree")
            out.append(Check(cid, "error" if world else "warn", title, L(f"{C.tilde(p)} 是 {m:o}，别的用户读得到", f"{C.tilde(p)} is {m:o}: other users can read it"),
                             f"chmod {want:o} {C.tilde(p)}", fixable=True, repair=_chmod(p, want)))
        else:
            out.append(Check(cid, "ok", title, f"{C.tilde(p)} ({m:o})"))
    return out


def sqlite_files(cfg: dict) -> list[Path]:
    """data_dir 第一层的 SQLite 库（mousse.db、nutrition.db……）和记忆检索的索引；按名字排。"""
    d = C.data_dir(cfg)
    found = {C.db_path(cfg), C.recall_db(cfg)}
    if d.is_dir():
        found |= {p for p in d.iterdir() if p.suffix == ".db" and p.is_file()}
    return sorted((p for p in found if p.is_file() and C.is_sqlite(p)), key=lambda p: p.name)


def check_databases(c: Ctx) -> list[Check]:
    if unknown(c):
        return []
    out = []
    main = C.db_path(c.cfg)
    if not main.exists():
        out.append(Check("data.db", "info", L("数据库", "Database"), L(f"{C.tilde(main)} 还没有（服务第一次起来时建）", f"no {C.tilde(main)} yet (the server creates it on first start)")))
    for p in sqlite_files(c.cfg):
        cid = "data.db" if p == main else f"data.db.{p.stem}"
        good, msg = C.quick_check(p)
        size = C.human_size(p.stat().st_size)
        if good:
            out.append(Check(cid, "ok", p.name, f"{size} · quick_check ok"))
        else:
            out.append(Check(cid, "error", p.name, f"quick_check: {msg[:160]}",
                             L(f"先停服务，用备份换回来：mousse restore <备份>（{C.tilde(p)} 的样子先拷一份留着）",
                               f"stop the server and restore a backup: mousse restore <backup> (copy {C.tilde(p)} aside first)")))
    return out


def fts_problem(p: Path) -> str | None:
    """记忆检索的 FTS5 索引对不对（integrity-check）→ 不对的原因；对 / 还没建 = None。
    integrity-check 是一句特别的 INSERT，只读的连接跑不了：开成能写的，放在一个事务里，查完回滚（什么都不改）。"""
    try:
        conn = sqlite3.connect(p, timeout=30, isolation_level=None)
    except sqlite3.Error as e:
        return str(e)
    try:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='chunks_fts'").fetchone():
            return None
        conn.execute("BEGIN")
        try:
            conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('integrity-check')")
        finally:
            conn.execute("ROLLBACK")
        return None
    except sqlite3.Error as e:
        return str(e) or type(e).__name__
    finally:
        conn.close()


def _rebuild_fts(p: Path) -> Callable[[], str]:
    def go() -> str:
        # 和 engine/recall.py 的写一样拿文件锁（服务器在同步的话等它写完）；FTS5 的 rebuild 照表里存的正文重建索引，正文不动
        lock = Path(f"{p}.lock")
        with open(lock, "a+") as lk:
            try:
                import fcntl
                fcntl.flock(lk, fcntl.LOCK_EX)
            except ImportError:
                pass
            conn = sqlite3.connect(p, timeout=60)
            try:
                conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
                conn.commit()
            finally:
                conn.close()
        return L("重建了记忆检索的 FTS 索引", "rebuilt the memory search FTS index")
    return go


def check_fts(c: Ctx) -> list[Check]:
    if unknown(c):
        return []
    p = C.recall_db(c.cfg)
    if not p.exists():
        return []
    why = fts_problem(p)
    title = L("记忆检索的索引（FTS）", "Memory search index (FTS)")
    if why:
        return [Check("data.fts", "warn", title, why[:160], L("mousse doctor --fix（重建索引，正文不动）", "mousse doctor --fix (rebuilds the index; the text stays)"),
                      fixable=True, repair=_rebuild_fts(p))]
    return [Check("data.fts", "ok", title, "integrity-check ok")]


def expired_pairing(cfg: dict, now: datetime | None = None) -> int:
    a = cfg.get("auth") if isinstance(cfg.get("auth"), dict) else {}
    now_s = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    return sum(1 for p in a.get("pairing") or [] if isinstance(p, dict) and str(p.get("expires") or "") <= now_s)


def drop_expired_pairing() -> str:
    """清掉 server.json 里过期的配对码（先备份；读不了不写）。→ 一句话。"""
    data, problem = C.load_config()
    if problem or data is None:
        raise RuntimeError(L("server.json 读不了，没动", "server.json can't be read; left alone"))
    a = data.get("auth") if isinstance(data.get("auth"), dict) else {}
    now_s = datetime.now(timezone.utc).isoformat(timespec="seconds")
    live = [p for p in a.get("pairing") or [] if isinstance(p, dict) and str(p.get("expires") or "") > now_s]
    n = len(a.get("pairing") or []) - len(live)
    if n <= 0:
        return L("没有过期的配对码", "no expired pairing codes")
    bak = C.backup_config()
    a["pairing"] = live
    data["auth"] = a
    C.write_config(data)
    return L(f"清掉 {n} 个过期的配对码（server.json 备份在 {C.tilde(bak)}）", f"removed {n} expired pairing codes (server.json backed up as {C.tilde(bak)})")


def check_pairing(c: Ctx) -> list[Check]:
    n = expired_pairing(c.cfg)
    if not n:
        return []
    return [Check("data.pairing", "warn", L("过期的配对码", "Expired pairing codes"), L(f"server.json 里还留着 {n} 个", f"{n} still in server.json"),
                  L("mousse doctor --fix（先备份 server.json）", "mousse doctor --fix (backs up server.json first)"), fixable=True, repair=drop_expired_pairing)]


# —— 世界树 ——————————————————————————————————————————————————————————————————————————

def check_tree(_c: Ctx) -> list[Check]:
    title = L("世界树", "Memory tree")
    if importlib.util.find_spec("openmousse_tree") is None:
        return [Check("tree.package", "warn", title, L("没装 openmousse_tree", "openmousse_tree isn't installed"),
                      f"{C.PY} -m pip install {C.REPO_TILDE}/tree")]
    try:
        ver = importlib.metadata.version("openmousse-tree")
    except importlib.metadata.PackageNotFoundError:
        ver = "?"
    out = [Check("tree.package", "ok", title, L(f"装了 {ver}", f"installed {ver}"))]
    conf = C.tree_home() / "config.json"
    try:
        tc = json.loads(conf.read_text(encoding="utf8"))
    except (OSError, ValueError):
        tc = None
    if not isinstance(tc, dict):
        out.append(Check("tree.init", "warn", L("世界树建了没", "Memory tree set up"), L(f"{C.tilde(conf)} 不在：还没建", f"no {C.tilde(conf)}: not set up yet"),
                         f"{SETUP} memory sqlite"))
    else:
        storage = str(tc.get("storage") or "sqlite")
        where = C.tilde(tc.get("notes_dir")) if storage == "markdown" and tc.get("notes_dir") else C.tilde(C.tree_home())
        out.append(Check("tree.init", "ok", L("世界树建了没", "Memory tree set up"), f"{storage} · {where}"))
    return out


# —— 服务 ——————————————————————————————————————————————————————————————————————————

def _control(svc: C.Service, action: str) -> Callable[[], str]:
    def go() -> str:
        ok, err = C.control(svc, action)
        if not ok:
            raise RuntimeError(err or action)
        return f"{action} {svc.unit if C.manager() == 'systemd' else svc.label}"
    return go


def _restart_server() -> str:
    if not C.wait_idle(seconds=120):  # 照 safe_restart.py：有回复在跑就不掐断它
        raise RuntimeError(L("还有回复在跑，等它们说完再修（或者 python3 safe_restart.py）", "replies are still in progress; try again when they finish (or python3 safe_restart.py)"))
    ok, err = C.control(C.SERVER, "restart")
    if not ok:
        raise RuntimeError(err or "restart")
    h = C.wait_health(seconds=30)
    return L("重启了服务", "restarted the server") + ("" if h["up"] else L("（还没回话，等一会儿再查）", " (not answering yet; check again in a moment)"))


def check_services(c: Ctx) -> list[Check]:
    m = C.manager()
    out = [Check("service.manager", "info", L("服务管理", "Service manager"),
                 {"systemd": "systemd (user)", "launchd": "launchd", "none": L("没有 systemd / launchd：服务要自己起", "no systemd / launchd: start the services yourself")}[m])]
    if m == "systemd":
        user = os.environ.get("USER") or ""
        r = C.run(["loginctl", "show-user", user, "-p", "Linger", "--value"], timeout=10)
        if r.returncode == 0 and r.stdout.strip() == "no":
            out.append(Check("service.linger", "warn", L("登出后服务会停（linger 没开）", "Services stop when you log out (linger is off)"),
                             fix="sudo loginctl enable-linger $USER"))
    if m == "none":
        out.append(Check("service.server", "info", L("服务器", "Server"), L("自己起的（下面那一项看它在不在回话）", "started by hand (see whether it answers below)")))
        return out
    s = C.state(C.SERVER)
    title = L("服务器（openmousse-server）", "Server (openmousse-server)")
    if s["active"]:
        out.append(Check("service.server", "ok", title, s["state"]))
    elif s["installed"] is False:
        out.append(Check("service.server", "error", title, L("没装成服务", "not installed as a service"), L(f"再跑一遍安装命令：{INSTALL}", f"rerun the installer: {INSTALL}")))
    else:
        out.append(Check("service.server", "error", title, s["state"], C.restart_cmd(C.SERVER), fixable=True, needs_yes=True, repair=_restart_server))
    d = C.state(C.DAILY_CLOSE)
    title = L("每晚日结", "Nightly digest")
    if d["active"]:
        out.append(Check("service.daily-close", "ok", title, d["state"]))
    elif d["installed"] is False:
        out.append(Check("service.daily-close", "warn", title, L("没装上", "not installed"), L("再跑一遍安装命令", "rerun the installer")))
    else:
        fix = "systemctl --user enable --now openmousse-daily-close.timer" if m == "systemd" else C.start_cmd(C.DAILY_CLOSE)
        out.append(Check("service.daily-close", "warn", title, d["state"], fix, fixable=True, needs_yes=True, repair=_control(C.DAILY_CLOSE, "start")))
    t = C.state(C.TREE)
    if t["installed"]:
        title = L("世界树服务（mousse-tree）", "Memory tree service (mousse-tree)")
        out.append(Check("service.tree", "ok", title, t["state"]) if t["active"] else
                   Check("service.tree", "warn", title, t["state"], C.restart_cmd(C.TREE), fixable=True, needs_yes=True, repair=_control(C.TREE, "restart")))
    return out


def check_health(c: Ctx) -> list[Check]:
    if c.problem in ("broken", "unreadable"):
        return []
    h = C.health(c.cfg)
    title = L("服务在回话（/api/health）", "Server answers (/api/health)")
    restart = C.restart_cmd(C.SERVER)
    managed = C.manager() != "none"
    if h["up"]:
        srv = h["data"].get("server") if isinstance(h["data"].get("server"), dict) else {}
        running, head = str(srv.get("commit") or ""), C.head_commit() or ""
        if running and head and not (head.startswith(running) or running.startswith(head)):
            return [Check("service.health", "warn", L("服务还在跑旧代码", "The server still runs old code"),
                          L(f"在跑 {srv.get('version')}（{running}），仓库是 {C.repo_version() or '?'}（{head}）",
                            f"running {srv.get('version')} ({running}), the repository has {C.repo_version() or '?'} ({head})"),
                          restart, fixable=managed, needs_yes=True, repair=_restart_server if managed else None)]
        return [Check("service.health", "ok", title, f"{h['url']} · {srv.get('version') or '?'} · {running or '?'}")]
    if h["status"] in (401, 403):
        return [Check("service.health", "error", title, L(f"服务在，但令牌对不上（HTTP {h['status']}）", f"the server is up but rejects the token (HTTP {h['status']})"),
                      L(f"令牌改过的话重启一下：{restart}", f"if you changed tokens, restart it: {restart}"))]
    detail = f"{h['url']} · " + (f"HTTP {h['status']}" if h["status"] else h["error"][:100] or L("连不上", "not reachable"))
    return [Check("service.health", "error", L("服务连不上（日志：mousse logs -n 50）", "Server not reachable (logs: mousse logs -n 50)"), detail,
                  restart, fixable=managed, needs_yes=True, repair=_restart_server if managed else None)]


# —— 版本 ——————————————————————————————————————————————————————————————————————————

def check_version(_c: Ctx) -> list[Check]:
    st = C.update_state()
    title = L("新版本", "Updates")
    if not st:
        return [Check("version.update", "info", title, L("还没查过", "not checked yet"), "mousse update --check")]
    at = str(st.get("checkedAt") or "")[:16].replace("T", " ")
    if st.get("available"):
        return [Check("version.update", "warn", title, L(f"有新的：{st.get('target') or '?'}（{at} 查的）", f"newer one available: {st.get('target') or '?'} (checked {at})"),
                      "mousse update")]
    return [Check("version.update", "ok", title, L(f"已经是最新的（{at} 查的，{st.get('channel') or 'stable'} 通道）",
                                                   f"up to date (checked {at}, {st.get('channel') or 'stable'} channel)"))]


SECTIONS: list[tuple[str, tuple[str, str], list[Callable[[Ctx], list[Check]]]]] = [
    ("system", ("系统", "System"), [check_python, check_deps, check_disk, check_docker]),
    ("install", ("安装", "Install"), [check_repo, check_venv, check_cli]),
    ("config", ("配置", "Configuration"), [check_config_file, check_config_valid, check_claw]),
    ("layers", ("每一层", "Layers"), [check_layers]),
    ("login", ("命令行登录", "Sign-ins"), [check_logins]),
    ("data", ("数据", "Data"), [check_data_dir, check_perms, check_databases, check_fts, check_pairing]),
    ("tree", ("世界树", "Memory tree"), [check_tree]),
    ("services", ("服务", "Services"), [check_services, check_health]),
    ("version", ("版本", "Version"), [check_version]),
]


def run_checks(only: set[str] | None = None, on_section: Callable[[str, str, list[Check]], None] | None = None) -> list[Check]:
    """全部查一遍（only = 只查这几节）。on_section(节, 节名, 这一节的结果)：边查边打。"""
    c = ctx()
    out: list[Check] = []
    for key, (zh, en), fns in SECTIONS:
        if only and key not in only:
            continue
        part: list[Check] = []
        for fn in fns:
            try:
                got = fn(c)
            except Exception as e:  # noqa: BLE001 — 一项出错不拦着别的
                got = [Check(f"{key}.{fn.__name__.removeprefix('check_')}", "warn", fn.__name__.removeprefix("check_"),
                             L(f"这一项没查成：{type(e).__name__}", f"this check failed to run: {type(e).__name__}"))]
            for ch in got:
                ch.section, ch.recheck = key, fn
            part += got
        out += part
        if on_section:
            on_section(key, L(zh, en), part)
    return out


def repair(checks: list[Check], yes: bool) -> list[Check]:
    """--fix：能修的修了、再查一遍那一项（OpenClaw：修完 re-run detect，还在就算没修好）。→ 动过的那几项。"""
    touched = []
    for ch in checks:
        if not (ch.bad and ch.fixable and ch.repair):
            continue
        touched.append(ch)
        if ch.needs_yes and not yes:
            ch.note = L(f"要停 / 重启服务，加 --yes 才替你做；或者自己跑：{ch.fix}", f"needs a service stop / restart: add --yes, or run it yourself: {ch.fix}")
            continue
        try:
            ch.note = ch.repair()
        except Exception as e:  # noqa: BLE001
            ch.note = L(f"没修成：{C.scrub(str(e))[:200]}", f"couldn't fix it: {C.scrub(str(e))[:200]}")
            continue
        again = next((x for x in ch.recheck(ctx()) if x.id == ch.id), None) if ch.recheck else None
        if again is None or not again.bad:
            ch.fixed, ch.status = True, "ok"
            if again is not None:
                ch.detail = again.detail
        else:
            ch.note += L("；再查一遍还是不对", "; still not right when checked again")
            ch.status, ch.detail = again.status, again.detail
    return touched


def summary(checks: list[Check]) -> dict:
    s = {k: sum(1 for c in checks if c.status == k) for k in STATUSES}
    s["fixed"] = sum(1 for c in checks if c.fixed)
    return s


def exit_code(checks: list[Check], strict: bool) -> int:
    s = summary(checks)
    return 1 if s["error"] or (strict and s["warn"]) else 0


def report(checks: list[Check], *, strict: bool = False, fixed: bool = False) -> dict:
    """--json 的样子。"""
    return {"ok": exit_code(checks, strict) == 0, "strict": strict, "fix": fixed, "version": C.repo_version() or None,
            "commit": C.head_commit(), "checkedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "checks": [c.to_json() for c in checks], "summary": summary(checks)}


def print_check(ch: Check) -> None:
    sep = L("：", ": ")
    print(f"  {C.MARK[ch.status]} {ch.title}" + (f"{sep}{ch.detail}" if ch.detail else ""))
    if ch.fix and ch.bad:
        print(f"      → {ch.fix}" + (L("（--fix 能修）", " (--fix can do this)") if ch.fixable and not ch.needs_yes else
                                   L("（--fix --yes 能做）", " (--fix --yes can do this)") if ch.fixable else ""))
    elif ch.fix and ch.status == "info":
        print(f"      {ch.fix}")


def main(a) -> int:
    as_json, fix, yes, strict = bool(a.json), bool(a.fix), bool(a.yes), bool(a.strict)

    def show(_key: str, title: str, part: list[Check]) -> None:
        if as_json or not part:
            return
        print()
        print(title)
        for ch in part:
            print_check(ch)

    if not as_json:
        ver = C.repo_version()
        print(L("OpenMousse 体检", "OpenMousse doctor") + f" · {ver or '?'} ({C.head_commit() or '?'}) · Python {sys.version.split()[0]}")
    checks = run_checks(on_section=show)
    if fix:
        touched = repair(checks, yes)
        if not as_json:
            print()
            print(L("修", "Fixes"))
            if not touched:
                print("  " + L("没有 --fix 能修的", "nothing --fix can repair"))
            for ch in touched:
                mark = "✓" if ch.fixed else "·" if ch.needs_yes and not yes else "✗"
                print(f"  {mark} {ch.title}{L('：', ': ')}{ch.note}")
    if as_json:
        print(json.dumps(report(checks, strict=strict, fixed=fix), ensure_ascii=False, indent=2))
        return exit_code(checks, strict)
    s = summary(checks)
    print()
    line = L(f"{s['ok']} 项正常 · {s['warn']} 项要留意 · {s['error']} 项不通", f"{s['ok']} ok · {s['warn']} to look at · {s['error']} failing")
    if fix:
        line += L(f" · 修好了 {s['fixed']} 项", f" · {s['fixed']} fixed")
    elif any(c.bad and c.fixable for c in checks):
        line += L("。mousse doctor --fix 能修标了的几项", ". mousse doctor --fix can repair the marked ones")
    print(line + L("。这段输出里没有令牌和密钥。", ". There are no tokens or keys in this output."))
    return exit_code(checks, strict)
