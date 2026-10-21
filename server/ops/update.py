"""mousse update：更新（第 7 批，14.4，2026-10-19）。照 Hermes 的 hermes_cli/update_cmd.py（计划 → 快照 → 应用 → 重启 → 核验 → 回执；
编译不过自动回退；Docker、包管理装的拒绝并给对的命令）和 OpenClaw 的 docs/cli/update、install/development-channels.md（通道）。只借做法。

  mousse update [--check] [--plan] [--channel stable | main] [--yes] [--stash] [--json]

通道：stable = 远端最新的 v<VERSION> tag（发版时打的，server/version.py）；main = 远端的 main。--channel 选了就记进 server.json 的
  update.channel（默认 stable，先备份）；远端默认 origin，update.remote 能改；等服务回话最多 update.wait 秒（默认 90）。
先看能不能更，不能就停下、说清楚（退出码 2）：
  - MOUSSE_INSTALL=docker（第 9 批的镜像会设）：在容器里不更，拉新镜像、重建容器；
  - 不是 git 装的：重跑安装命令；
  - 仓库里有没提交的改动：--stash 才替你藏进 git stash（git stash pop 拿回来）；
  - 现在的 commit 不在通道的历史里（在跑别的开发分支）：不硬切。比通道还新（main 上发版之后的提交、通道是 stable）= 没有可更的，不往回退。
  装的时候是 git clone --depth 1（浅的）：先补全历史（git fetch --unshallow）再比。
--check：fetch 后比一比，结果存进 <server.json 旁边>/update/check.json（mousse status、doctor 只读它，不联网）。
--plan：从哪个 commit 到哪个、中间几个提交、依赖变了什么（server/requirements.txt、tree/pyproject.toml）、会重启哪些服务；不动手。
真更新（终端里问一句，或者 --yes）：
  1. 自动快照（mousse backup 那一套：openmousse-snapshot-pre-update-…，带密钥，回滚时 mousse restore 它就回到更新前）；
  2. 切到目标（stable：git checkout --detach <tag>；main：本地 main 快进到远端的），用 venv 的 python 装 server/requirements.txt 和 ./tree；
  3. python -m compileall -q server 过了才往下，不过就回到原来的 commit（依赖也装回去）；
  4. 重启服务：systemd / launchd 先等进行中的回复说完（同 safe_restart.py），世界树的代码变了也重启它；没有服务管理器的说怎么手动起；
  5. 等 /api/health 通；不通就回滚（原来的 commit、重装依赖、再重启）并说为什么。
  全程写一份日志：<server.json 旁边>/update/logs/update-<时间>.log（命令的输出过一遍打码），最后一次的回执在 update/last.json。
不自动更新、不在后台定时检查；不改 server/version.py 的 VERSION、不打 tag（发版是主人的事）。
退出码：0 更新好了 / 没有可更的 / --check、--plan 看完了；1 没更成（已经回滚，或者回滚也没成：日志里写着）；2 不能更（上面那几种）。
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from i18n import L

from . import common as C

CHANNELS = ("stable", "main")
TAG = re.compile(r"^v(\d{4})\.(\d{1,2})\.(\d{1,2})(?:-(\d+))?$")
WAIT = 90
DEP_FILES = ("server/requirements.txt", "tree/pyproject.toml")


class Refused(Exception):
    """不能更：一句话 + 该怎么办（退出码 2）。"""


class Failed(Exception):
    """更到一半没成。"""


def repo_dir() -> Path:
    """更新哪个仓库：这份代码自己所在的（bin/mousse 的 MOUSSE_REPO 也认）。"""
    return Path(os.environ.get("MOUSSE_REPO") or C.REPO).expanduser()


def settings() -> dict:
    u = C.cfg().get("update")
    return u if isinstance(u, dict) else {}


def channel(given: str | None = None) -> str:
    c = given or str(settings().get("channel") or "stable")
    return c if c in CHANNELS else "stable"


def remote() -> str:
    r = str(settings().get("remote") or "origin").strip()
    return r if re.fullmatch(r"[A-Za-z0-9._/-]{1,100}", r) else "origin"


def wait_seconds() -> float:
    try:
        return max(1.0, float(settings().get("wait") or WAIT))
    except (TypeError, ValueError):
        return float(WAIT)


class Log:
    """更新日志：每一步、每条命令的输出（打过码）。"""

    def __init__(self) -> None:
        d = C.state_dir() / "update" / "logs"
        d.mkdir(parents=True, exist_ok=True)
        self.path = d / f"update-{datetime.now().strftime('%Y%m%d-%H%M%S')}.log"
        self.f = open(self.path, "a", encoding="utf8")  # noqa: SIM115 — 跟着这一次更新一直开着

    def __call__(self, line: str) -> None:
        self.f.write(f"[{datetime.now().strftime('%H:%M:%S')}] {C.scrub(line)}\n")
        self.f.flush()

    def close(self) -> None:
        self.f.close()


def git(log: Log | None, *args: str, timeout: float = 300) -> str:
    """跑 git；出错抛 Failed（日志里写着命令和输出）。"""
    r = C.git(*args, repo=repo_dir(), timeout=timeout)
    if log:
        log(f"$ git {' '.join(args)} → {r.returncode}" + (f"\n{(r.stdout + r.stderr).strip()[-4000:]}" if (r.stdout + r.stderr).strip() else ""))
    if r.returncode != 0:
        raise Failed(f"git {' '.join(args[:3])}: {C.scrub((r.stderr or r.stdout).strip())[-300:]}")
    return r.stdout.strip()


def git_ok(*args: str) -> bool:
    return C.git(*args, repo=repo_dir(), timeout=60).returncode == 0


def tag_key(t: str) -> tuple[int, ...]:
    m = TAG.match(t)
    return tuple(int(x or 0) for x in m.groups()) if m else ()


def latest_tag() -> str | None:
    """远端最新的发版 tag（只看远端有的：本地自己打的不算）。"""
    r = C.git("ls-remote", "--tags", "--refs", remote(), repo=repo_dir(), timeout=120)
    if r.returncode != 0:
        raise Failed(L(f"连不上远端 {remote()}：{C.scrub(r.stderr.strip())[-200:]}", f"can't reach the remote {remote()}: {C.scrub(r.stderr.strip())[-200:]}"))
    tags = [line.split("refs/tags/", 1)[1] for line in r.stdout.splitlines() if "refs/tags/" in line]
    tags = [t for t in tags if TAG.match(t)]
    return max(tags, key=tag_key) if tags else None


def preflight(stash: bool) -> None:
    """不能更的几种（见文件开头）→ Refused。"""
    if (os.environ.get("MOUSSE_INSTALL") or "").strip().lower() == "docker":
        raise Refused(L("这是 Docker 装的：容器里不更新。在主机上的仓库里重建镜像、换掉容器：git pull && docker compose -f docker/compose.yaml up -d --build"
                        "（用的是别处拉来的镜像就拉新的；或者照你起它的那一条 docker run 重来）",
                        "This is a Docker install: it doesn't update inside the container. Rebuild the image and recreate the container from the "
                        "repository on the host: git pull && docker compose -f docker/compose.yaml up -d --build (with an image pulled from elsewhere, "
                        "pull the new one; or rerun the docker run you started it with)"))
    repo = repo_dir()
    r = C.git("rev-parse", "--show-toplevel", repo=repo, timeout=20)
    if r.returncode != 0 or Path(r.stdout.strip()).resolve() != repo.resolve():
        raise Refused(L(f"{C.tilde(repo)} 不是 git 装的：重跑安装命令更新（curl -fsSL https://openmousse.ai/install | bash）",
                        f"{C.tilde(repo)} isn't a git checkout: update by rerunning the installer (curl -fsSL https://openmousse.ai/install | bash)"))
    dirty = C.git("status", "--porcelain", "--untracked-files=no", repo=repo, timeout=60).stdout.strip()
    if dirty and not stash:
        files = ", ".join(line[3:] for line in dirty.splitlines()[:5])
        raise Refused(L(f"仓库里有没提交的改动（{files}）：先提交或者收起来；--stash 替你藏进 git stash（之后 git stash pop 拿回来）",
                        f"The repository has uncommitted changes ({files}): commit or put them away first; --stash puts them in git stash for you "
                        f"(git stash pop brings them back)"))


def fetch(log: Log | None, ch: str) -> None:
    rem = remote()
    if C.git("rev-parse", "--is-shallow-repository", repo=repo_dir(), timeout=20).stdout.strip() == "true":
        git(log, "fetch", "--unshallow", "--tags", rem)  # 安装器是浅克隆：补全历史才比得了谁在谁前面
    git(log, "fetch", "--tags", rem)
    if ch == "main":
        git(log, "fetch", rem, "+refs/heads/main:refs/remotes/" + rem + "/main")


def head() -> tuple[str, str | None]:
    """→ (现在的 commit, 在哪个分支上；detached = None)。"""
    sha = C.git("rev-parse", "HEAD", repo=repo_dir(), timeout=20).stdout.strip()
    br = C.git("symbolic-ref", "--short", "-q", "HEAD", repo=repo_dir(), timeout=20).stdout.strip() or None
    return sha, br


def target(ch: str) -> tuple[str, str]:
    """→ (目标的名字（tag 或 <remote>/main）, 它的 commit)。"""
    if ch == "main":
        ref = f"{remote()}/main"
    else:
        ref = latest_tag() or ""
        if not ref:
            raise Refused(L(f"远端（{remote()}）还没有发版的 tag（v<版本>）：先用 --channel main", f"The remote ({remote()}) has no release tags (v<version>) yet: use --channel main"))
    sha = C.git("rev-parse", f"{ref}^{{commit}}", repo=repo_dir(), timeout=20).stdout.strip()
    if not sha:
        raise Refused(L(f"找不到 {ref}", f"can't find {ref}"))
    return ref, sha


def compare(h: str, t: str) -> str:
    """same | behind（能快进过去）| ahead（比目标还新）| diverged（别的开发分支）。"""
    if h == t:
        return "same"
    if git_ok("merge-base", "--is-ancestor", h, t):
        return "behind"
    if git_ok("merge-base", "--is-ancestor", t, h):
        return "ahead"
    return "diverged"


def check(ch: str, log: Log | None = None, *, save: bool = True) -> dict:
    """fetch 后比：{"channel", "current", "target", "targetCommit", "relation", "available", "behind", "checkedAt"}；存进 check.json。"""
    fetch(log, ch)
    h, br = head()
    ref, t = target(ch)
    rel = compare(h, t)
    behind = int(C.git("rev-list", "--count", f"{h}..{t}", repo=repo_dir(), timeout=60).stdout.strip() or 0) if rel == "behind" else 0
    out = {"checkedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"), "channel": ch, "remote": remote(),
           "current": {"commit": h[:7], "branch": br, "version": C.repo_version(repo_dir()) or None},
           "target": ref.split("/", 1)[1] if ch == "main" else ref, "targetCommit": t[:7], "relation": rel, "available": rel == "behind",
           "behind": behind}
    if save:
        p = C.update_state_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf8")
        os.replace(tmp, p)
    return out


def plan(chk: dict) -> dict:
    """从哪到哪、中间的提交、依赖变了什么、会重启哪些服务（不动手）。"""
    h, t = C.git("rev-parse", "HEAD", repo=repo_dir()).stdout.strip(), chk["targetCommit"]
    commits = C.git("log", "--oneline", "--no-decorate", f"{h}..{t}", repo=repo_dir(), timeout=60).stdout.splitlines() if chk["available"] else []
    deps = C.git("diff", f"{h}", f"{t}", "--", *DEP_FILES, repo=repo_dir(), timeout=60).stdout if chk["available"] else ""
    changed = C.git("diff", "--name-only", f"{h}", f"{t}", repo=repo_dir(), timeout=60).stdout.split() if chk["available"] else []
    restart = ["openmousse-server"] + (["mousse-tree"] if any(f.startswith("tree/") for f in changed) and C.state(C.TREE)["installed"] else [])
    dep_lines = [ln for ln in deps.splitlines() if ln[:1] in "+-" and not ln.startswith(("+++", "---"))]
    return {**chk, "commits": commits[:50], "commitCount": len(commits), "dependencies": dep_lines, "restart": restart if chk["available"] else [],
            "manager": C.manager(), "snapshot": True}


# —— 真更新 ————————————————————————————————————————————————————————————————————————

def venv_python() -> str | None:
    """装依赖、编译用的 python：安装器的 venv；没有它就是现在这个（在 venv 里的话）；都不是 = None（不替你往系统 python 里装）。"""
    py = C.venv() / "bin" / "python"
    if py.exists():
        return str(py)
    return sys.executable if sys.prefix != sys.base_prefix else None


def pip(log: Log) -> str:
    py = venv_python()
    repo = repo_dir()
    if py is None:
        log("no venv: dependencies not installed")
        return L(f"没有 venv，依赖没装：{C.PY} -m pip install -r {C.REPO_TILDE}/server/requirements.txt", f"no venv, dependencies not installed: "
                 f"{C.PY} -m pip install -r {C.REPO_TILDE}/server/requirements.txt")
    for args in (["-m", "pip", "install", "-q", "-r", str(repo / "server" / "requirements.txt")], ["-m", "pip", "install", "-q", str(repo / "tree")]):
        r = C.run([py, *args], timeout=900, cwd=str(repo))
        log(f"$ python {' '.join(args)} → {r.returncode}\n{(r.stdout + r.stderr).strip()[-4000:]}")
        if r.returncode != 0:
            raise Failed(L(f"装依赖没成：{C.scrub((r.stderr or r.stdout).strip())[-300:]}", f"installing dependencies failed: {C.scrub((r.stderr or r.stdout).strip())[-300:]}"))
    return ""


def compiles(log: Log) -> str | None:
    """python -m compileall -q server → 没过的原文；过了 = None。"""
    py = venv_python() or sys.executable
    r = C.run([py, "-m", "compileall", "-q", "server"], timeout=600, cwd=str(repo_dir()))
    log(f"$ python -m compileall -q server → {r.returncode}\n{(r.stdout + r.stderr).strip()[-4000:]}")
    return None if r.returncode == 0 else C.scrub((r.stdout + r.stderr).strip())[-600:] or f"exit {r.returncode}"


def move(log: Log, ch: str, ref: str, t: str) -> None:
    """切到目标。main：本地的 main 有远端没有的提交就不切；一步切过去（git checkout -B：成了才动分支，没成什么都不变，不会停在一个
    旧的本地 main 上）。"""
    if ch == "main":
        local = C.git("rev-parse", "--verify", "-q", "refs/heads/main", repo=repo_dir(), timeout=20).stdout.strip()
        if local and not git_ok("merge-base", "--is-ancestor", local, t):
            raise Refused(L("本地的 main 有远端没有的提交：不硬切", "the local main has commits the remote doesn't: not forcing it"))
        git(log, "checkout", "-q", "-B", "main", t)  # 本地的 main 是 t 的祖先：等于快进
        if not local:
            C.git("branch", "-q", "--set-upstream-to", ref, "main", repo=repo_dir(), timeout=20)  # 新建的照原来的 --track（不成也不碍事）
    else:
        git(log, "checkout", "-q", "--detach", t)


def back(log: Log, h: str, br: str | None) -> None:
    """回到更新前的 commit（分支在的回到分支上）。"""
    if br:
        git(log, "checkout", "-q", br)
        if C.git("rev-parse", "HEAD", repo=repo_dir()).stdout.strip() != h:
            git(log, "reset", "-q", "--hard", h)  # 这个分支就是刚才快进的那个：退回去
    else:
        git(log, "checkout", "-q", "--detach", h)


def restart(log: Log, services: list[C.Service]) -> str:
    """重启：先等进行中的回复说完（同 safe_restart.py）。→ 没重启成的原因（空 = 好了）。"""
    if C.manager() == "none":
        return L(f"没有服务管理器：自己重启一下：{C.restart_cmd(C.SERVER)}", f"no service manager: restart it yourself: {C.restart_cmd(C.SERVER)}")
    if not C.wait_idle(seconds=float(settings().get("idle_wait") or 600)):
        return L("还有回复在跑，没重启：说完以后跑 python3 safe_restart.py", "replies are still in progress, so it wasn't restarted: run python3 safe_restart.py when they finish")
    for svc in services:
        ok, err = C.control(svc, "restart")
        log(f"restart {svc.name} → {'ok' if ok else err}")
        if not ok and svc is C.SERVER:
            return L(f"重启没成：{err}", f"the restart failed: {err}")
    return ""


def healthy(new: str, log: Log) -> tuple[bool, str]:
    h = C.wait_health(seconds=wait_seconds(), every=1.0)
    log(f"health → {h['status']} {h['data'].get('server') if h['up'] else h['error']}")
    if not h["up"]:
        return False, (f"HTTP {h['status']}" if h["status"] else h["error"]) or L("没回话", "no answer")
    srv = h["data"].get("server") if isinstance(h["data"].get("server"), dict) else {}
    running = str(srv.get("commit") or "")
    if running and not (new.startswith(running) or running.startswith(new[:7])):
        log(f"warning: the server reports commit {running}, the repository is at {new[:7]}")
    return True, running


def receipt(data: dict) -> None:
    p = C.state_dir() / "update" / "last.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf8")


def apply(ch: str, *, stash: bool, say=print) -> int:
    """真更新（见文件开头 1–5）。→ 退出码。"""
    from . import backup
    log = Log()
    say(L(f"更新日志：{C.tilde(log.path)}", f"Update log: {C.tilde(log.path)}"))
    lock = C.state_dir() / "update" / "lock"
    lock_f = open(lock, "a")  # noqa: SIM115
    try:
        import fcntl
        try:
            fcntl.flock(lock_f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            say(L("已经有一个 mousse update 在跑", "Another mousse update is already running"))
            return 2
    except ImportError:
        pass
    started = time.monotonic()
    try:
        preflight(stash)
        chk = check(ch, log)
        if not chk["available"]:
            say(describe(chk))
            return 0 if chk["relation"] in ("same", "ahead") else 2
        h, br = head()
        ref, t = target(ch)
        log(f"from {h} ({br or 'detached'}) to {ref} {t}")
        tree_changed = any(f.startswith("tree/") for f in C.git("diff", "--name-only", h, t, repo=repo_dir()).stdout.split())
        snap = backup.snapshot("pre-update")
        say(L(f"1/5 快照：{C.tilde(snap['path'])}", f"1/5 Snapshot: {C.tilde(snap['path'])}"))
        log(f"snapshot {snap['path']}")
        if stash and C.git("status", "--porcelain", "--untracked-files=no", repo=repo_dir()).stdout.strip():
            git(log, "stash", "push", "-m", f"mousse update {datetime.now().strftime('%Y-%m-%d %H:%M')}")
            say(L("   改动藏进了 git stash（git stash pop 拿回来）", "   your changes are in git stash (git stash pop brings them back)"))
        try:
            move(log, ch, ref, t)
        except Failed:
            with contextlib.suppress(Failed):
                back(log, h, br)  # 切的时候没成：回到原来的地方（下一次重启跑的还是现在这一版）
            raise
        say(L(f"2/5 切到 {chk['target']}（{t[:7]}），装依赖", f"2/5 Checked out {chk['target']} ({t[:7]}), installing dependencies"))
        note = ""
        try:
            note = pip(log)
            bad = compiles(log)
        except Failed as e:
            bad = str(e)
        if bad:
            say(L(f"3/5 编译没过：回到原来的 {h[:7]}", f"3/5 It doesn't compile: going back to {h[:7]}"))
            say("   " + bad.splitlines()[-1][:300] if bad.splitlines() else "")
            back(log, h, br)
            with contextlib.suppress(Failed):
                pip(log)
            receipt({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "ok": False, "from": h[:7], "to": t[:7], "rolledBack": True,
                     "why": "compile", "log": str(log.path)})
            say(L("回滚好了，服务没动。", "Rolled back; the server wasn't touched."))
            return 1
        say(L("3/5 编译过了", "3/5 It compiles") + (f"（{note}）" if note else ""))
        services = [C.SERVER] + ([C.TREE] if tree_changed and C.state(C.TREE)["installed"] else [])
        why = restart(log, services)
        if why:
            say(L(f"4/5 {why}", f"4/5 {why}"))
            receipt({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "ok": True, "restarted": False, "from": h[:7], "to": t[:7],
                     "log": str(log.path)})
            check(ch, log)
            say(L(f"代码更新到 {chk['target']} 了；重启以后才用上（mousse doctor 看跑的是不是新的）。",
                  f"The code is now {chk['target']}; it takes effect after the restart (mousse doctor tells which code runs)."))
            return 0
        say(L("4/5 重启了服务", "4/5 Restarted the server"))
        ok, running = healthy(t, log)
        if not ok:
            say(L(f"5/5 服务没回话（{running}）：回滚到 {h[:7]}", f"5/5 The server didn't answer ({running}): rolling back to {h[:7]}"))
            back(log, h, br)
            with contextlib.suppress(Failed):
                pip(log)
            again = restart(log, services)
            ok2, _ = healthy(h, log) if not again else (False, again)
            receipt({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "ok": False, "from": h[:7], "to": t[:7], "rolledBack": True,
                     "why": "health", "restored": ok2, "log": str(log.path)})
            say(L("回滚好了，服务又在回话了。", "Rolled back; the server answers again.") if ok2 else
                L(f"回滚了代码，服务还是没回话：mousse logs -n 100；数据要退回去的话 mousse restore {C.tilde(snap['path'])} --yes",
                  f"The code is rolled back but the server still doesn't answer: mousse logs -n 100; to put the data back: mousse restore {C.tilde(snap['path'])} --yes"))
            check(ch, log)
            return 1
        say(L(f"5/5 服务在回话了（{running or '?'}）", f"5/5 The server answers ({running or '?'})"))
        receipt({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "ok": True, "restarted": True, "from": h[:7], "to": t[:7],
                 "target": chk["target"], "seconds": round(time.monotonic() - started), "snapshot": snap["path"], "log": str(log.path)})
        check(ch, log)
        say(L(f"更新好了：{h[:7]} → {chk['target']}（{t[:7]}）。", f"Updated: {h[:7]} → {chk['target']} ({t[:7]})."))
        return 0
    except Refused as e:
        log(f"refused: {e}")
        say(str(e))
        return 2
    except Failed as e:
        log(f"failed: {e}")
        say(L(f"没更成：{e}（日志 {C.tilde(log.path)}）", f"Update failed: {e} (log: {C.tilde(log.path)})"))
        return 1
    finally:
        log.close()
        lock_f.close()


def describe(chk: dict) -> str:
    rel = chk["relation"]
    if rel == "behind":
        return L(f"有新的：{chk['target']}（{chk['targetCommit']}，比现在新 {chk['behind']} 个提交）。mousse update 更新；--plan 先看会做什么",
                 f"Newer: {chk['target']} ({chk['targetCommit']}, {chk['behind']} commits ahead). Run mousse update; --plan shows what it would do")
    if rel == "same":
        return L(f"已经是最新的（{chk['channel']} 通道：{chk['target']}）", f"Up to date ({chk['channel']} channel: {chk['target']})")
    if rel == "ahead":
        return L(f"现在的 {chk['current']['commit']} 比 {chk['channel']} 通道的 {chk['target']} 还新：没有可更的（跟 main 走：--channel main）",
                 f"{chk['current']['commit']} is newer than the {chk['channel']} channel's {chk['target']}: nothing to update (to follow main: --channel main)")
    return L(f"现在的 {chk['current']['commit']}（{chk['current']['branch'] or '没在分支上'}）不在 {chk['channel']} 通道的历史里：在跑别的开发分支，不硬切。"
             f"要换过去：git -C {C.tilde(repo_dir())} checkout <分支>，再 mousse update",
             f"{chk['current']['commit']} ({chk['current']['branch'] or 'detached'}) isn't in the {chk['channel']} channel's history: another development "
             f"branch is checked out, so it won't force a switch. To switch: git -C {C.tilde(repo_dir())} checkout <branch>, then mousse update")


def remember_channel(ch: str) -> None:
    """--channel 选了就记进 server.json 的 update.channel（变了才写，先备份；读不了不写）。"""
    data, problem = C.load_config()
    if problem in ("broken", "unreadable") or data is None:
        return
    u = data.get("update") if isinstance(data.get("update"), dict) else {}
    if u.get("channel", "stable") == ch and ("channel" in u or ch == "stable"):
        return
    C.backup_config()
    data["update"] = {**u, "channel": ch}
    C.write_config(data)


def main(a) -> int:
    if a.channel and a.channel not in CHANNELS:
        print(L("--channel 只认 stable、main", "--channel takes stable or main"))
        return 2
    ch = channel(a.channel)
    if a.check or a.plan:
        try:
            preflight(stash=True)  # 看一看不在乎改动；真更新时再拦
            chk = check(ch)
            out = plan(chk) if a.plan else chk
        except Refused as e:
            print(str(e))
            return 2
        except Failed as e:
            print(L(f"查不了：{e}", f"Couldn't check: {e}"))
            return 1
        if a.channel and a.check:
            remember_channel(ch)
        if a.json:
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0 if chk["relation"] != "diverged" else 2
        print(describe(chk))
        if a.plan and chk["available"]:
            print(L(f"计划（不动手）：{chk['current']['commit']} → {chk['target']}（{chk['targetCommit']}），{out['commitCount']} 个提交",
                    f"Plan (nothing changes): {chk['current']['commit']} → {chk['target']} ({chk['targetCommit']}), {out['commitCount']} commits"))
            for c in out["commits"][:20]:
                print(f"  {c}")
            if out["commitCount"] > 20:
                print(L(f"  ……还有 {out['commitCount'] - 20} 个", f"  …{out['commitCount'] - 20} more"))
            print(L("依赖：", "Dependencies: ") + (L("没变", "unchanged") if not out["dependencies"] else ""))
            for d in out["dependencies"]:
                print(f"  {d}")
            print(L("会先做一份快照（mousse backup 那一套），再重启：", "A snapshot comes first (as mousse backup does), then restarts: ")
                  + (", ".join(out["restart"]) if out["manager"] != "none" else L("没有服务管理器，要你自己重启", "nothing (no service manager: you restart it)")))
        return 0 if chk["relation"] != "diverged" else 2
    if not a.yes:
        if not sys.stdin.isatty():
            print(L("真更新要加 --yes（或者在终端里跑，会问一句）；先看计划：mousse update --plan", "To actually update add --yes (or run it in a terminal, it asks); "
                    "see the plan first: mousse update --plan"))
            return 2
        try:
            preflight(a.stash)
            chk = check(ch)
        except (Refused, Failed) as e:
            print(str(e))
            return 2 if isinstance(e, Refused) else 1
        print(describe(chk))
        if not chk["available"] or not C.ask_yes(L("现在更新吗", "Update now")):
            return 0 if chk["relation"] != "diverged" else 2
    if a.channel:
        remember_channel(ch)
    return apply(ch, stash=a.stash)
