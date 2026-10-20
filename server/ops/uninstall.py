"""mousse uninstall（第 7 批，2026-10-19）：把 OpenMousse 从这台机器上拿掉。

  mousse uninstall [--full] [--keep-tree] [--no-backup] [--backup-to DIR] [--dry-run] [--yes] [--json]

不加 --full（默认）：只拿掉跑起来的那几样，数据一样不动——
  服务   停下、关掉开机自启、删掉服务定义：systemd 的 openmousse-server、openmousse-daily-close（.timer + .service）、mousse-tree、
         openmousse-node（~/.config/systemd/user 里的单元文件，删完 daemon-reload）；launchd 的 ai.openmousse.*（先 bootout，再删
         ~/Library/LaunchAgents 里的 plist）；没有服务管理器的照 common._manual 只停自己起的进程。停之前等正在跑的回复说完（最多 IDLE_WAIT 秒）
  定时   crontab 里跑 packs/core/scripts/daily_close.py 的那几行（没有 systemd / launchd 的机器手动加的；别的行不碰）
  软链   ~/.openmousse/repo、~/.local/bin/mousse（指到仓库的 bin/mousse 才删）、各工作区 skills/ 里指到仓库 packs/core/skills 的
  留着：~/.openmousse（server.json、secrets.env、数据、venv、日志、备份）、世界树（~/.mousse-tree）、仓库的代码；最后说一句怎么全删。
--full：上面这些，再删掉 ~/.openmousse 整个、世界树的家（--keep-tree：世界树的服务和数据都留着），server.json 指到别处、但还在家目录底下
  而且路径里带 mousse 的 data_dir / db / uploads / server.json 本身；仓库只在是安装器放的那份（~/openmousse，或者 install-manifest.json
  写的）、而且干净（没有没提交的改动、没有藏着的、没有没推上去的提交）时才删，不然留着说一声。
  先警告（列出每一样、多大），默认先打一个备份（同 mousse backup create，连 secrets.env；放在家目录或者 --backup-to 给的目录，
  不能在要删的里面；--no-backup 不打），备份没打成就什么都不删。问的时候要打出 delete（中文界面也认「删除」）；没有终端就要 --yes。
  ~/.openmousse/backups/（安装器改 OpenClaw 那边之前的备份、mousse backup 打的包）不跟着删：整个挪到备份旁边（家目录或者 --backup-to，
  名字 openmousse-backups-<日期-时间>；--no-backup 也挪），挪不出来就什么都不删。
不碰（只说一声）：OpenClaw 那边（openclaw.json 里安装器加的设置、AGENTS.md 末尾的规矩、世界树加的检索路径；安装器改之前的备份在
  ~/.openmousse/backups/，--full 时挪到备份旁边）；Obsidian 库（think.vault）和世界树放在库里的笔记（它们在要删的目录里面的话，
  那个目录整个不删）；Tailscale 上开着的 /t /m /s /f（只打印关掉它们的命令）。Docker 装的（MOUSSE_INSTALL=docker）不在这里卸：删容器和卷。
install-manifest.json（~/.openmousse 里，安装器写了的话）：照它列的来，和上面默认的那份合在一起。认这几样，都可以不写：
  {"services": ["openmousse-server.service" | "ai.openmousse.server" | {"unit": …} | {"label": …, "plist": …}], "links": ["~/…"],
   "paths": ["~/…"]（只有 --full 才删）, "repo": "~/openmousse", "cron": ["那一行里带的字"], "funnel": ["/t", …],
   "items": [{"kind": "process", "name": "openmousse-server" | "mousse-tree", …}]（第 9 批的安装器在后台起的：有服务管理器也照 common._manual 停）}
  第 9 批的安装器只在 install.sh 自己 clone 了仓库时写 repo（自己的检出记成 checkout，这里不认）。
  安全栏一样有：只删家目录底下、路径里带 mousse 的（家目录本身、它上面的一律不删）；软链只删软链本身，不跟着软链删目录。
退出码：0 卸好了 / --dry-run；1 有一步没做成（别的照做，最后列出来；备份没打成 = 什么都没删）；2 拒绝（没终端又没 --yes、备份放在要删的
  里面、Docker 装的、参数不对）。
参考：OpenClaw 的 openclaw uninstall（docs/cli/uninstall.md：分服务 / 状态 / 工作区几块、--dry-run、先停服务再删）、Hermes 的
  hermes uninstall（问要不要留数据、关掉开机自启）；只借设计，没拿代码。
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from i18n import L

from . import backup as B
from . import common as C

IDLE_WAIT = 60
MANIFEST = "install-manifest.json"
CRON_MARK = "packs/core/scripts/daily_close.py"
FUNNEL_PATHS = ("/t", "/m", "/s", "/f")
UNITS = {"server": ("openmousse-server.service",), "daily-close": ("openmousse-daily-close.timer", "openmousse-daily-close.service"),
         "tree": ("mousse-tree.service",), "node": ("openmousse-node.service",)}
CONFIRM = ("delete", "删除")


class Refused(Exception):
    pass


@dataclass
class Step:
    kind: str                     # service | cron | link | path | repo
    target: str                   # 给人看的（~ 开头）
    action: str
    run: Callable[[], str] | None = None
    size: int | None = None
    done: bool = False
    error: str | None = None
    note: str = ""

    def to_json(self) -> dict:
        return {"kind": self.kind, "target": self.target, "action": self.action, "bytes": self.size, "done": self.done, "error": self.error,
                "note": self.note}


@dataclass
class Plan:
    full: bool
    steps: list[Step]
    left: list[str]               # 留着的（给人看的一句一样）
    notes: list[str]              # 要自己做的、不碰的
    delete: list[Path]            # --full 要删的目录 / 文件（备份不能放在这里面）
    manifest: bool


# —— 安全栏 ——————————————————————————————————————————————————————————————————————

def _plain(p: Path) -> Path:
    """绝对路径，前面的软链解开、最后一段不跟（删软链就是删软链本身）。"""
    p = Path(os.path.abspath(p.expanduser()))
    return p.parent.resolve() / p.name


def deletable(p: Path) -> bool:
    """能删的：家目录底下（不是家目录本身），相对家目录的路径里带 mousse。"""
    try:
        rel = _plain(p).relative_to(C.home().resolve())
    except ValueError:
        return False
    return bool(rel.parts) and "mousse" in str(rel).lower()


def _inside(child: Path, parent: Path) -> bool:
    try:
        _plain(child).relative_to(_plain(parent))
        return True
    except ValueError:
        return False


def _size(p: Path) -> int:
    if p.is_symlink() or p.is_file():
        with contextlib.suppress(OSError):
            return p.lstat().st_size
        return 0
    total = 0
    for root, _dirs, files in os.walk(p, followlinks=False):
        for f in files:
            with contextlib.suppress(OSError):
                total += os.lstat(os.path.join(root, f)).st_size
    return total


def _remove(p: Path) -> Callable[[], str]:
    def go() -> str:
        if p.is_symlink() or p.is_file():
            p.unlink()
        elif p.is_dir():
            shutil.rmtree(p)
        return L("删了", "deleted")
    return go


def manifest() -> dict:
    """~/.openmousse/install-manifest.json（安装器写了的话；读不了 = 没有）。"""
    try:
        data = json.loads((C.mousse_home() / MANIFEST).read_text(encoding="utf8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _strs(v) -> list[str]:
    return [str(x).strip() for x in v if isinstance(x, (str, int)) and str(x).strip()] if isinstance(v, list) else []


# —— 服务 ——————————————————————————————————————————————————————————————————————

def _systemd_remove(units: tuple[str, ...], files: list[Path]) -> Callable[[], str]:
    def go() -> str:
        for u in units:
            C.run(["systemctl", "--user", "disable", "--now", u], timeout=120)  # 没装上的会报错：不要紧，下面照删文件
        for f in files:
            if f.exists() or f.is_symlink():
                f.unlink()
        return L("停了、关了开机自启、删了单元文件", "stopped, disabled, unit files deleted")
    return go


def _reload() -> str:
    r = C.run(["systemctl", "--user", "daemon-reload"], timeout=60)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip()[-200:] or "daemon-reload failed")
    C.run(["systemctl", "--user", "reset-failed"], timeout=30)
    return "daemon-reload"


def _launchd_remove(svc: C.Service | None, label: str, plist: Path) -> Callable[[], str]:
    def go() -> str:
        if svc is not None:
            ok, err = C.control(svc, "stop")
            if not ok:
                raise RuntimeError(err or "launchctl bootout failed")
        else:
            C.run(["launchctl", "bootout", f"gui/{os.getuid()}/{label}"], timeout=60)
        if plist.exists():
            plist.unlink()
        return L("bootout、删了 plist", "booted out, plist deleted")
    return go


def _manual_stop(svc: C.Service, direct: bool = False) -> Callable[[], str]:
    """direct：不经服务管理器，照 common._manual 停（安装器在后台起的那个，systemd / launchd 管不到）。"""
    def go() -> str:
        ok, err = C._manual(svc, "stop") if direct else C.control(svc, "stop")
        if not ok:
            raise RuntimeError(err or "pkill failed")
        return L("停了（只停自己起的那个进程）", "stopped (only its own process)")
    return go


def service_steps(man: dict, keep_tree: bool) -> list[Step]:
    m = C.manager()
    svcs = [s for s in C.SERVICES.values() if not (keep_tree and s is C.TREE)]
    extra = man.get("services") if isinstance(man.get("services"), list) else []
    out: list[Step] = []
    if m == "systemd":
        d = C.unit_dir()
        groups = [UNITS[s.name] for s in svcs]
        for x in extra:
            u = x.get("unit") if isinstance(x, dict) else x
            if isinstance(u, str) and u.endswith((".service", ".timer")) and not any(u in g for g in groups):
                if not (keep_tree and u == "mousse-tree.service"):
                    groups.append((u,))
        for g in groups:
            files = [d / u for u in g if (d / u).exists() or (d / u).is_symlink()]
            if files:
                out.append(Step("service", ", ".join(C.tilde(f) for f in files), L("停下、关掉开机自启、删掉单元文件",
                                "stop, disable, delete the unit files"), _systemd_remove(g, files)))
        if out:
            out.append(Step("service", "systemctl --user daemon-reload", L("让 systemd 忘掉它们", "make systemd forget them"), _reload))
    elif m == "launchd":
        seen: set[str] = set()
        for s in svcs:
            p = C.plist(s)
            seen.add(s.label)
            if p.exists() or C.state(s).get("installed"):
                out.append(Step("service", C.tilde(p), L("bootout、删掉 plist", "boot out, delete the plist"), _launchd_remove(s, s.label, p)))
        for x in extra:
            label = x.get("label") if isinstance(x, dict) else x
            if isinstance(label, str) and label.startswith("ai.openmousse.") and label not in seen and not (keep_tree and label == C.TREE.label):
                p = Path(str(x.get("plist"))).expanduser() if isinstance(x, dict) and x.get("plist") else C.home() / "Library/LaunchAgents" / f"{label}.plist"
                if p.exists() and deletable(p):
                    out.append(Step("service", C.tilde(p), L("bootout、删掉 plist", "boot out, delete the plist"), _launchd_remove(None, label, p)))
    else:
        for s in (x for x in svcs if x in (C.SERVER, C.TREE)):
            out.append(Step("service", s.name, L("停下自己起的进程（没在跑就没事）", "stop the hand-started process (fine if it isn't running)"),
                            _manual_stop(s)))
        return out
    # 有服务管理器、可安装器在后台起过一次（试装没装服务：清单 items 里 kind = process）：管理器管不到它，照 common._manual 停
    items = man.get("items") if isinstance(man.get("items"), list) else []
    started = {str(x.get("name")) for x in items if isinstance(x, dict) and x.get("kind") == "process"}
    for s, name in ((C.SERVER, "openmousse-server"), (C.TREE, "mousse-tree")):
        if name in started and not (keep_tree and s is C.TREE):
            out.append(Step("service", s.name, L("停下安装器在后台起的进程（没在跑就没事）", "stop the process the installer started in the background "
                            "(fine if it isn't running)"), _manual_stop(s, direct=True)))
    return out


# —— 定时、软链 ——————————————————————————————————————————————————————————————————

def cron_steps(man: dict) -> list[Step]:
    if not shutil.which("crontab"):
        return []
    r = C.run(["crontab", "-l"], timeout=15)
    if r.returncode != 0:
        return []
    marks = [CRON_MARK, *_strs(man.get("cron"))]
    hits = [x for x in r.stdout.splitlines() if any(mk in x for mk in marks) and not x.lstrip().startswith("#")]
    if not hits:
        return []

    def go() -> str:
        cur = C.run(["crontab", "-l"], timeout=15).stdout.splitlines()
        keep = [x for x in cur if x.lstrip().startswith("#") or not any(mk in x for mk in marks)]
        w = C.run(["crontab", "-"], timeout=15, input="\n".join(keep) + ("\n" if keep else ""))
        if w.returncode != 0:
            raise RuntimeError((w.stderr or w.stdout).strip()[-200:] or "crontab failed")
        return L(f"删了 {len(cur) - len(keep)} 行", f"removed {len(cur) - len(keep)} lines")
    return [Step("cron", L(f"crontab 里 {len(hits)} 行（日结）", f"{len(hits)} crontab lines (the nightly digest)"),
                 L("删掉这几行（别的不碰）", "remove those lines (nothing else)"), go)]


def _points_into(link: Path, roots: list[Path], marker: str) -> bool:
    try:
        raw = os.readlink(link)
    except OSError:
        return False
    target = (link.parent / raw).resolve() if not os.path.isabs(raw) else Path(raw).resolve()
    return marker in raw or any(_inside(target, r) for r in roots)


def link_steps(cfg: dict, man: dict, repo: Path | None) -> list[Step]:
    roots = [r for r in (repo, C.REPO) if r is not None]
    cands: list[tuple[Path, str]] = [(C.repo_link(), ""), (C.home() / ".local/bin/mousse", "bin/mousse")]
    cands += [(Path(x).expanduser(), "") for x in _strs(man.get("links"))]
    for ws in C.workspaces(cfg).values():
        sk = ws / "skills"
        with contextlib.suppress(OSError):
            for child in sorted(sk.iterdir()) if sk.is_dir() else []:
                if child.is_symlink():
                    cands.append((child, "packs/core/skills/"))
    out, seen = [], set()
    for p, marker in cands:
        key = str(_plain(p))
        if key in seen or not p.is_symlink():
            continue
        seen.add(key)
        ok = p == C.repo_link() or (marker and _points_into(p, roots, marker)) or (not marker and (deletable(p) or _points_into(p, roots, "mousse")))
        if ok:
            out.append(Step("link", C.tilde(p), L(f"删掉这个软链（→ {C.tilde(os.readlink(p))}）", f"remove the link (→ {C.tilde(os.readlink(p))})"),
                            _remove(p)))
    return out


# —— 仓库、--full 要删的 ——————————————————————————————————————————————————————————

def repo_dir(man: dict) -> tuple[Path | None, bool]:
    """仓库在哪 → (路径, 是不是安装器放的那份：manifest 写的，或者 ~/openmousse)。"""
    if man.get("repo"):
        return Path(str(man["repo"])).expanduser(), True
    link = C.repo_link()
    target = link.resolve() if link.is_symlink() else None
    default = C.home() / "openmousse"
    if target is not None and target.is_dir():
        return target, _plain(target) == _plain(default)
    return (default, True) if default.is_dir() else (None, False)


def repo_clean(p: Path) -> str | None:
    """能删吗：→ None（能）或者为什么不能。"""
    if not (p / ".git").exists():
        return L("不是 git 仓库", "not a git checkout")
    url = C.git("remote", "get-url", "origin", repo=p).stdout.strip()
    if "openmousse" not in url.lower():
        return L("origin 不是 openmousse 的", "its origin isn't openmousse")
    st = C.git("status", "--porcelain", repo=p)
    if st.returncode != 0 or st.stdout.strip():
        return L("有没提交的改动", "it has uncommitted changes")
    if C.git("stash", "list", repo=p).stdout.strip():
        return L("git stash 里藏着改动", "it has stashed changes")
    ahead = C.git("log", "--branches", "--not", "--remotes", "--oneline", repo=p)
    if ahead.returncode != 0 or ahead.stdout.strip():
        return L("有没推上去的提交", "it has commits that aren't pushed")
    return None


def protected(cfg: dict) -> list[Path]:
    """--full 也不删的：Obsidian 库、世界树放在库里的笔记。"""
    out = []
    think = cfg.get("think") if isinstance(cfg.get("think"), dict) else {}
    if think.get("vault"):
        out.append(Path(str(think["vault"])).expanduser())
    with contextlib.suppress(OSError, ValueError):
        tc = json.loads((C.tree_home() / "config.json").read_text(encoding="utf8"))
        if isinstance(tc, dict) and tc.get("notes_dir") and not _inside(Path(str(tc["notes_dir"])).expanduser(), C.tree_home()):
            out.append(Path(str(tc["notes_dir"])).expanduser())
    return [p for p in out if not any(q != p and _inside(p, q) for q in out)]  # 笔记在库里面：说库就够了


def full_steps(cfg: dict, man: dict, keep_tree: bool, repo: Path | None, installer_repo: bool) -> tuple[list[Step], list[Path], list[str]]:
    keep = protected(cfg)
    targets: list[Path] = []
    left: list[str] = []
    if not keep_tree:
        targets.append(C.tree_home())
    for key in ("data_dir", "db", "uploads"):
        if cfg.get(key):
            targets.append(Path(str(cfg[key])).expanduser())
    targets.append(C.config_path())
    targets += [Path(x).expanduser() for x in _strs(man.get("paths"))]
    targets.append(C.mousse_home())
    picked: list[Path] = []
    for t in targets:
        if not (t.exists() or t.is_symlink()) or any(_inside(t, p) for p in picked if p != t):
            continue
        if any(_plain(t) == _plain(p) for p in picked):
            continue
        if not deletable(t):
            left.append(L(f"{C.tilde(t)}（不在家目录底下、或者路径里没有 mousse：自己删）", f"{C.tilde(t)} (outside your home or without mousse in "
                          "its path: delete it yourself)"))
            continue
        if hold := next((k for k in keep if _inside(k, t)), None):
            left.append(L(f"{C.tilde(t)}（{C.tilde(hold)} 在里面：Obsidian 库、笔记不删）", f"{C.tilde(t)} ({C.tilde(hold)} is inside: vaults and "
                          "notes are never deleted)"))
            continue
        picked = [p for p in picked if not _inside(p, t)] + [t]
    repo_step = None
    if repo is not None and repo.is_dir():
        why = None if installer_repo else L("不是安装器放的那份", "not the copy the installer made")
        why = why or (None if deletable(repo) else L("不在家目录底下、或者路径里没有 mousse", "outside your home or without mousse in its path"))
        why = why or repo_clean(repo)
        if why:
            left.append(L(f"仓库 {C.tilde(repo)}（{why}）", f"the repository {C.tilde(repo)} ({why})"))
            for t in [t for t in picked if _inside(repo, t)]:  # 留着的仓库在要删的目录里面：那个目录整个不删
                picked.remove(t)
                left.append(L(f"{C.tilde(t)}（留着的仓库在里面）", f"{C.tilde(t)} (the repository that stays is inside)"))
        elif not any(_inside(repo, t) for t in picked):
            repo_step = Step("repo", C.tilde(repo), L("删掉（安装器放的、干净的那份）", "delete (the installer's clean copy)"), _remove(repo), size=_size(repo))
    steps = [Step("path", C.tilde(t), L("删掉", "delete"), _remove(t), size=_size(t)) for t in picked]
    if repo_step is not None:  # 仓库最后删（这个命令自己可能就是从它跑起来的）
        steps.append(repo_step)
        picked.append(repo)
    return steps, picked, left


def funnel_notes(cfg: dict, man: dict) -> list[str]:
    """Tailscale 上还开着的、安装器开的那几条：只打印关掉的命令。"""
    exe = shutil.which("tailscale")
    if not exe:
        return []
    r = C.run([exe, "serve", "status", "--json"], timeout=20)
    try:
        st = json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else {}
    except ValueError:
        st = {}
    paths = set(FUNNEL_PATHS) | set(_strs(man.get("funnel")))
    out = []
    for hostport, web in ((st.get("Web") or {}) if isinstance(st, dict) else {}).items():
        for path in sorted(((web or {}).get("Handlers") or {})):
            if path.rstrip("/") in paths and re.search(r"//(127\.0\.0\.1|localhost):\d+", str((web["Handlers"][path] or {}).get("Proxy") or "")):
                mode = "funnel" if (st.get("AllowFunnel") or {}).get(hostport) else "serve"
                out.append(f"tailscale {mode} --https={hostport.rsplit(':', 1)[-1]} --set-path={path.rstrip('/')} off")
    return out


def openclaw_notes(cfg: dict, full: bool = False) -> list[str]:
    claw = cfg.get("claw") if isinstance(cfg.get("claw"), dict) else {}
    oc = Path(str(cfg.get("openclaw_home") or C.home() / ".openclaw")).expanduser()
    if str(claw.get("kind") or "openclaw").lower() != "openclaw" and not (oc / "openclaw.json").exists():
        return []
    where = (L("~/.openmousse/backups/，会整个挪到备份旁边", "~/.openmousse/backups/, which moves next to the backup") if full
             else "~/.openmousse/backups/")
    return [L(f"OpenClaw（{C.tilde(oc)}）：openclaw.json 里安装器加的设置、AGENTS.md 末尾 OpenMousse 的规矩、世界树加的检索路径都还在（安装器改之前的备份在 "
              f"{where}）", f"OpenClaw ({C.tilde(oc)}): the settings the installer added to openclaw.json, OpenMousse's rules at the end "
              f"of AGENTS.md and the memory tree's search path are still there (the installer's copies from before are in {where})")]


def keep_backups(a: argparse.Namespace, delete: list[Path]) -> Path | None:
    """--full：~/.openmousse/backups（安装器改 OpenClaw 那边之前的备份、mousse backup 打的包）整个挪出来，放到备份旁边（家目录或者
    --backup-to），不跟着删。没有、空的、不在要删的里面 → None。挪不出来抛（main：什么都不删）。"""
    src = C.mousse_home() / "backups"
    if not src.is_dir() or src.is_symlink() or not any(src.iterdir()) or not any(_inside(src, t) for t in delete):
        return None
    d = _backup_dir(a, delete)
    d.mkdir(parents=True, exist_ok=True)
    dst = d / f"openmousse-backups-{datetime.now():%Y%m%d-%H%M%S}"
    while dst.exists():
        dst = dst.with_name(dst.name + "x")
    shutil.move(str(src), str(dst))
    return dst


def plan(full: bool, keep_tree: bool) -> Plan:
    cfg = C.cfg()
    man = manifest()
    repo, installer_repo = repo_dir(man)
    steps = service_steps(man, keep_tree) + cron_steps(man) + link_steps(cfg, man, repo)
    left: list[str] = []
    delete: list[Path] = []
    if full:
        more, delete, left = full_steps(cfg, man, keep_tree, repo, installer_repo)
        steps += more
    else:
        left.append(L(f"{C.tilde(C.mousse_home())}：server.json、secrets.env、数据、venv、日志、备份", f"{C.tilde(C.mousse_home())}: server.json, "
                      "secrets.env, data, venv, logs, backups"))
        if C.tree_home().exists():
            left.append(L(f"世界树：{C.tilde(C.tree_home())}", f"the memory tree: {C.tilde(C.tree_home())}"))
        if repo is not None:
            left.append(L(f"代码：{C.tilde(repo)}", f"the code: {C.tilde(repo)}"))
    if keep_tree and C.tree_home().exists() and full:
        left.append(L(f"世界树（--keep-tree）：{C.tilde(C.tree_home())}", f"the memory tree (--keep-tree): {C.tilde(C.tree_home())}"))
    notes = openclaw_notes(cfg, full) + [L(f"Tailscale 上还开着：{c}", f"still on Tailscale: {c}") for c in funnel_notes(cfg, man)]
    notes += [L(f"不删：{C.tilde(p)}", f"not deleted: {C.tilde(p)}") for p in protected(cfg) if p.exists()]
    return Plan(full, steps, left, notes, delete, bool(man))


# —— 命令行 ——————————————————————————————————————————————————————————————————

def show(p: Plan) -> None:
    if p.full:
        C.say(L("⚠ mousse uninstall --full：下面这些会被删掉，删了就没了（除非你有备份）。", "⚠ mousse uninstall --full: everything below gets deleted "
                "and can't come back without a backup."))
    C.say(L("要做的：", "To do:") + (L("（照 install-manifest.json 合在一起）", " (merged with install-manifest.json)") if p.manifest else ""))
    if not p.steps:
        C.say(L("  （没什么要拿掉的）", "  (nothing to remove)"))
    for s in p.steps:
        size = f" · {C.human_size(s.size)}" if s.size is not None else ""
        C.say(f"  - {s.target}：{s.action}{size}" if L("zh", "en") == "zh" else f"  - {s.target}: {s.action}{size}")
    if p.left:
        C.say(L("留着：", "Left alone:"))
        for x in p.left:
            C.say(f"  · {x}")
    if p.notes:
        C.say(L("不碰（要的话自己做）：", "Not touched (do it yourself if you want):"))
        for x in p.notes:
            C.say(f"  · {x}")


def _ask_full() -> bool:
    if not sys.stdin.isatty():
        return False
    try:
        a = input(L("确定全删就打 delete（或者「删除」）：", "Type delete to go ahead: ")).strip().lower()
    except EOFError:
        return False
    return a in CONFIRM


def _backup_dir(a: argparse.Namespace, delete: list[Path]) -> Path:
    d = Path(a.backup_to).expanduser() if a.backup_to else C.home()
    if any(_inside(d, t) for t in delete):
        raise Refused(L(f"备份不能放在要删的 {C.tilde(d)} 里面：--backup-to 换个地方", f"the backup can't go in {C.tilde(d)}, which is being deleted: "
                        "pick another --backup-to"))
    return d


def main(a: argparse.Namespace) -> int:
    if os.environ.get("MOUSSE_INSTALL") == "docker":
        C.say(L("Docker 装的不在这里卸：删掉容器和它的卷（docker rm、docker volume rm）", "A Docker install isn't removed from here: delete the "
                "container and its volume (docker rm, docker volume rm)"))
        return 2
    full, keep_tree = bool(a.full), bool(a.keep_tree)
    p = plan(full, keep_tree)
    if not a.json:
        show(p)
    try:
        dest = _backup_dir(a, p.delete) if full and not a.no_backup else None
    except Refused as e:
        C.say(f"✗ {e}")
        return 2
    if full and not a.no_backup and not a.json:
        C.say(L(f"先打一个备份放到 {C.tilde(dest)}（--no-backup 不打）", f"A backup goes to {C.tilde(dest)} first (--no-backup skips it)"))
    if a.dry_run:
        if a.json:
            print(json.dumps({"full": full, "dryRun": True, "steps": [s.to_json() for s in p.steps], "left": p.left, "notes": p.notes,
                              "backupTo": C.tilde(dest) if dest else None}, ensure_ascii=False, indent=2))
        return 0
    if not p.steps:
        if a.json:
            print(json.dumps({"full": full, "dryRun": False, "steps": [], "left": p.left, "notes": p.notes, "backup": None}, ensure_ascii=False, indent=2))
        return 0
    if not a.yes:
        ok = (not a.json) and (_ask_full() if full else C.ask_yes(L("拿掉 OpenMousse 的服务和软链？数据留着", "Remove OpenMousse's services and "
                                                                     "links? Your data stays")))
        if not ok:
            C.say(L("什么都没动。不问就做：加 --yes", "Nothing changed. To skip the question: add --yes"))
            return 2
    made = None
    if dest is not None:
        try:
            made = B.create(str(dest), keep=None, reason="uninstall")["path"]
        except Exception as e:  # noqa: BLE001 — 备份没打成：什么都不删
            C.say(L(f"✗ 备份没打成，什么都没删：{C.scrub(str(e))[:300]}（不要备份：--no-backup）", f"✗ The backup failed, nothing was deleted: "
                    f"{C.scrub(str(e))[:300]} (to go without one: --no-backup)"))
            return 1
        if not a.json:
            C.say(L(f"✓ 备份：{C.tilde(made)}", f"✓ Backup: {C.tilde(made)}"))
    kept = None
    if full:
        try:
            kept = keep_backups(a, p.delete)
        except Exception as e:  # noqa: BLE001 — 挪不出来：什么都不删（不然以前的备份跟着没了）
            C.say(L(f"✗ 以前的备份（~/.openmousse/backups）挪不出来，什么都没删：{C.scrub(str(e))[:300]}",
                    f"✗ Couldn't move the earlier backups (~/.openmousse/backups) out, nothing was deleted: {C.scrub(str(e))[:300]}"))
            return 1
        if kept and not a.json:
            C.say(L(f"✓ 以前的备份挪到了 {C.tilde(kept)}（安装器改 OpenClaw 之前的那几份也在里面）",
                    f"✓ Earlier backups moved to {C.tilde(kept)} (the installer's copies from before it changed OpenClaw are in there too)"))
    if any(s.kind == "service" for s in p.steps) and C.busy():
        C.wait_idle(seconds=IDLE_WAIT)
    for s in p.steps:
        try:
            s.note = s.run() if s.run else ""
            s.done = True
        except Exception as e:  # noqa: BLE001 — 一步没做成：记下，接着做别的
            s.error = C.scrub(str(e))[:300] or type(e).__name__
    failed = [s for s in p.steps if s.error]
    if a.json:
        print(json.dumps({"full": full, "dryRun": False, "steps": [s.to_json() for s in p.steps], "left": p.left, "notes": p.notes,
                          "backup": C.tilde(made) if made else None, "keptBackups": C.tilde(kept) if kept else None}, ensure_ascii=False, indent=2))
        return 1 if failed else 0
    for s in p.steps:
        C.say(f"  {'✓' if s.done else '✗'} {s.target}" + (f"：{s.error}" if s.error and L('zh', 'en') == 'zh' else f": {s.error}" if s.error else ""))
    if made:
        C.say(L(f"装回来以后恢复：mousse restore {C.tilde(made)}", f"To get it back after reinstalling: mousse restore {C.tilde(made)}"))
    if not full:
        C.say(L("数据都还在。全删（先备份）：mousse uninstall --full", "Your data is still there. To delete everything (after a backup): mousse uninstall --full"))
    return 1 if failed else 0
