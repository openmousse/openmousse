"""第 7 批 2.13：mousse uninstall（ops/uninstall.py）。临时的家目录里装好一台的样子：假的 systemctl / launchctl / crontab / tailscale、
本地的 git 仓库和它的「远端」（一个裸库）、Obsidian 库。不碰这台机器上真的服务、真的 crontab。"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from pathlib import Path

import pytest

from ops import backup as B
from ops import uninstall as U
from tests.ops_helpers import make_home, ns, run_mousse

GIT = ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "-c", "init.defaultBranch=main", "-c", "commit.gpgsign=false"]


def git(*args: str, cwd: Path) -> str:
    return subprocess.run([*GIT, *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def h(tmp_path, monkeypatch):
    h = make_home(tmp_path, monkeypatch)
    home, m = h.home, h.mousse
    # 仓库：~/openmousse，origin 是一个本地的裸库（URL 里带 openmousse），推上去了 = 干净
    remote = tmp_path / "remote" / "openmousse.git"
    remote.parent.mkdir()
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    repo = home / "openmousse"
    (repo / "packs/core/skills/journal").mkdir(parents=True)
    (repo / "packs/core/skills/journal/SKILL.md").write_text("# journal\n", encoding="utf8")
    (repo / "bin").mkdir()
    (repo / "bin/mousse").write_text("#!/bin/sh\n", encoding="utf8")
    git("init", "-q", cwd=repo)
    git("add", "-A", cwd=repo)
    git("commit", "-qm", "init", cwd=repo)
    git("remote", "add", "origin", str(remote), cwd=repo)
    git("push", "-q", "origin", "main", cwd=repo)
    (m / "repo").symlink_to(repo)
    (home / ".local/bin").mkdir(parents=True)
    (home / ".local/bin/mousse").symlink_to(repo / "bin/mousse")
    # 数据、配置、venv、日志
    ws, fit = m / "workspace", home / ".openclaw" / "workspace-fitness"
    h.write({"claw": {"kind": "builtin"}, "language": "en", "bind": {"port": 9}, "data_dir": str(m / "data"), "workspace": str(ws),
             "agent_workspaces": {"fitness": str(fit)}, "think": {"vault": str(home / "Vault")}, "auth": {"tokens": {"local": "tok-local"}}})
    (m / "data").mkdir()
    with sqlite3.connect(m / "data" / "mousse.db") as c:
        c.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, text TEXT)")
        c.execute("INSERT INTO messages(text) VALUES ('fictional hello')")
    (m / "secrets.env").write_text("OPENAI_API_KEY=sk-test-0000000000000000\n", encoding="utf8")
    os.chmod(m / "secrets.env", 0o600)
    (m / "venv/bin").mkdir(parents=True)
    (m / "venv/bin/python").write_text("", encoding="utf8")
    (m / "logs").mkdir()
    for d in (ws / "skills", fit / "skills"):
        d.mkdir(parents=True)
        (d / "journal").symlink_to(repo / "packs/core/skills/journal")
    (fit / "skills" / "own").symlink_to(tmp_path / "someone-elses-skill")  # 不是我们的：留着
    (home / ".openclaw/openclaw.json").write_text("{}", encoding="utf8")  # 以前用过 OpenClaw：它那边的只说一声
    # 世界树：笔记放在 Obsidian 库里
    tree = home / ".mousse-tree"
    tree.mkdir()
    (tree / "config.json").write_text(json.dumps({"storage": "markdown", "notes_dir": str(home / "Vault/Memory")}), encoding="utf8")
    (home / "Vault/Memory").mkdir(parents=True)
    (home / "Vault/Memory/tea.md").write_text("Alex likes tea\n", encoding="utf8")
    # 服务、crontab、tailscale
    h.log = h.systemd()
    (home / ".config/systemd/user/mousse-tree.service").write_text("[Unit]\n", encoding="utf8")
    h.cron = tmp_path / "crontab.txt"
    h.cron.write_text("0 3 * * * echo other job\n45 3 * * * ~/.openmousse/venv/bin/python ~/openmousse/packs/core/scripts/daily_close.py\n"
                      "# old: packs/core/scripts/daily_close.py\n", encoding="utf8")
    h.fake("crontab", f'case "$1" in -l) cat "{h.cron}" 2>/dev/null || exit 1 ;; -) cat > "{h.cron}" ;; esac\n')
    status = {"Web": {"box.tail0.ts.net:443": {"Handlers": {"/t": {"Proxy": "http://127.0.0.1:8765/t"}, "/notes": {"Proxy": "http://127.0.0.1:3000"}}}},
              "AllowFunnel": {"box.tail0.ts.net:443": True}}
    h.fake("tailscale", f"echo '{json.dumps(status)}'\n")
    h.repo = repo
    return h


def go(**kw) -> int:
    base = {"full": False, "keep_tree": False, "no_backup": False, "backup_to": None, "dry_run": False, "yes": True, "json": False}
    return U.main(ns(**{**base, **kw}))


def test_dry_run_lists_everything_and_changes_nothing(h, capsys):
    before = sorted(str(p) for p in h.home.rglob("*"))
    assert go(dry_run=True, yes=False) == 0
    out = capsys.readouterr().out
    assert sorted(str(p) for p in h.home.rglob("*")) == before and not h.log.exists()
    for want in ("openmousse-server.service", "openmousse-daily-close.timer", "mousse-tree.service", "daemon-reload", "1 crontab lines",
                 "~/.openmousse/repo", "~/.local/bin/mousse", "~/.openmousse/workspace/skills/journal", "~/.openclaw/workspace-fitness/skills/journal",
                 "Left alone:", "tailscale funnel --https=443 --set-path=/t off", "OpenClaw"):
        assert want in out, want
    assert "skills/own" not in out and "/notes" not in out
    assert go(dry_run=True, yes=False, json=True) == 0
    j = json.loads(capsys.readouterr().out)
    assert j["dryRun"] is True and {s["kind"] for s in j["steps"]} == {"service", "cron", "link"} and j["backupTo"] is None


def test_default_removes_services_and_links_and_keeps_data(h, capsys):
    assert go() == 0
    log = h.log.read_text()
    for u in ("openmousse-server.service", "openmousse-daily-close.timer", "openmousse-daily-close.service", "mousse-tree.service"):
        assert f"--user disable --now {u}" in log, u
        assert not (h.home / ".config/systemd/user" / u).exists()
    assert "--user daemon-reload" in log
    assert h.cron.read_text() == "0 3 * * * echo other job\n# old: packs/core/scripts/daily_close.py\n"  # 别的行、注释不碰
    assert not (h.mousse / "repo").exists() and not (h.home / ".local/bin/mousse").is_symlink()
    assert not (h.mousse / "workspace/skills/journal").is_symlink() and (h.home / ".openclaw/workspace-fitness/skills/own").is_symlink()
    assert (h.mousse / "data/mousse.db").exists() and (h.home / ".mousse-tree/config.json").exists() and (h.repo / ".git").is_dir()
    assert "mousse uninstall --full" in capsys.readouterr().out
    assert go() == 0 and "(nothing to remove)" in capsys.readouterr().out  # 再来一次：没什么了


def test_full_backs_up_then_deletes(h, capsys):
    (h.mousse / "backups").mkdir(exist_ok=True)
    (h.mousse / "backups" / "openclaw.json.20261001-120000").write_text('{"before": true}', encoding="utf8")  # 安装器改之前的
    assert go(full=True) == 0
    out = capsys.readouterr().out
    backups = list(h.home.glob("openmousse-backup-uninstall-*.tar.gz"))
    assert len(backups) == 1 and B.verify(str(backups[0]))["ok"] and "mousse restore ~/openmousse-backup-uninstall-" in out
    kept = list(h.home.glob("openmousse-backups-*"))  # 以前的备份不跟着删：挪到备份旁边，说一声挪到哪了
    assert len(kept) == 1 and (kept[0] / "openclaw.json.20261001-120000").read_text() == '{"before": true}'
    assert f"Earlier backups moved to ~/{kept[0].name}" in out and "which moves next to the backup" in out
    assert not h.mousse.exists() and not (h.home / ".mousse-tree").exists() and not h.repo.exists()
    assert (h.home / "Vault/Memory/tea.md").read_text() == "Alex likes tea\n"  # Obsidian 库不删
    assert (h.home / ".openclaw/workspace-fitness/skills/own").is_symlink() and (h.home / ".openclaw/workspace-fitness").is_dir()  # OpenClaw 的不删


def test_full_keeps_a_dirty_repository_and_the_tree_when_asked(h, capsys):
    (h.repo / "notes.txt").write_text("my change\n", encoding="utf8")
    assert go(full=True, keep_tree=True, no_backup=True) == 0
    out = capsys.readouterr().out
    assert not h.mousse.exists() and (h.repo / "notes.txt").exists() and (h.home / ".mousse-tree/config.json").exists()
    assert "uncommitted changes" in out and "--keep-tree" in out
    assert (h.home / ".config/systemd/user/mousse-tree.service").exists() and "mousse-tree.service" not in h.log.read_text()
    assert not list(h.home.glob("openmousse-backup-*"))


def test_unpushed_commits_keep_the_repository(h):
    (h.repo / "x.txt").write_text("x\n", encoding="utf8")
    git("add", "x.txt", cwd=h.repo)
    git("commit", "-qm", "local only", cwd=h.repo)
    assert "pushed" in U.repo_clean(h.repo)
    git("push", "-q", "origin", "main", cwd=h.repo)
    assert U.repo_clean(h.repo) is None
    git("stash", "list", cwd=h.repo)
    (h.repo / "x.txt").write_text("y\n", encoding="utf8")
    git("stash", "-q", cwd=h.repo)
    assert "stash" in U.repo_clean(h.repo)


def test_earlier_backups_that_cant_move_stop_the_delete(h, capsys, monkeypatch):
    (h.mousse / "backups").mkdir(exist_ok=True)
    (h.mousse / "backups" / "AGENTS.md.20261001-120000").write_text("before\n", encoding="utf8")

    def stuck(*a, **kw):
        raise OSError("disk full")
    monkeypatch.setattr(U.shutil, "move", stuck)
    assert go(full=True, no_backup=True) == 1 and "nothing was deleted" in capsys.readouterr().out
    assert (h.mousse / "backups" / "AGENTS.md.20261001-120000").exists() and h.repo.exists() and h.mousse.exists()


def test_refusals_change_nothing(h, capsys, monkeypatch):
    before = sorted(str(p) for p in h.home.rglob("*"))
    assert go(yes=False) == 2  # pytest 里没有终端
    assert go(full=True, yes=False) == 2
    assert go(full=True, backup_to=str(h.mousse / "keep")) == 2 and "pick another --backup-to" in capsys.readouterr().out
    monkeypatch.setenv("MOUSSE_INSTALL", "docker")
    assert go(full=True) == 2
    monkeypatch.delenv("MOUSSE_INSTALL")
    h.write("{broken")  # 备份打不成：什么都不删
    assert go(full=True) == 1 and "nothing was deleted" in capsys.readouterr().out
    after = sorted(str(p) for p in h.home.rglob("*"))
    assert after == before and not h.log.exists()


def test_install_manifest_is_followed_with_the_same_guard(h):
    extra_link = h.home / "bin" / "mousse-dev"
    extra_link.parent.mkdir()
    extra_link.symlink_to(h.repo / "bin/mousse")
    cache = h.home / "mousse-cache"
    cache.mkdir()
    (cache / "a").write_text("x", encoding="utf8")
    docs = h.home / "Documents"
    docs.mkdir()
    (h.mousse / U.MANIFEST).write_text(json.dumps({"links": ["~/bin/mousse-dev"], "paths": ["~/mousse-cache", "~/Documents", "~", "/etc"],
                                                   "cron": ["echo other job"]}), encoding="utf8")
    p = U.plan(True, False)
    targets = {s.target for s in p.steps}
    assert {"~/bin/mousse-dev", "~/mousse-cache"} <= targets and "~/Documents" not in targets and "~" not in targets
    assert any("~/Documents" in x for x in p.left) and p.manifest
    assert go(full=True, no_backup=True) == 0
    assert not extra_link.is_symlink() and not cache.exists() and docs.is_dir() and h.cron.read_text() == "# old: packs/core/scripts/daily_close.py\n"


def test_a_background_server_the_installer_started_is_stopped_under_systemd(h):
    h.manager("systemd")
    log = h.root / "pkill.log"
    h.fake("pkill", f'echo "$@" >> "{log}"\nexit 0\n')
    (h.mousse / U.MANIFEST).write_text(json.dumps({"version": 1, "mode": "trial", "checkout": str(h.repo), "items": [
        {"kind": "process", "name": "openmousse-server", "stop": "pkill …"}]}), encoding="utf8")
    p = U.plan(False, False)
    assert any(s.kind == "service" and s.target == "server" for s in p.steps)
    assert go() == 0
    assert "run.py" in log.read_text() and U.C._ere(str(h.mousse / "venv")) in log.read_text()   # 只停 ~/.openmousse 里的那个


def test_deletable():
    home = U.C.home()
    assert U.deletable(home / ".openmousse") and U.deletable(home / ".mousse-tree") and U.deletable(home / "openmousse")
    assert not U.deletable(home) and not U.deletable(home / "Documents") and not U.deletable(Path("/etc/openmousse"))
    assert not U.deletable(home / ".openmousse" / ".." / "..") and not U.deletable(home / "..")


def test_launchd(h, monkeypatch):
    h.manager("launchd")
    agents = h.home / "Library/LaunchAgents"
    agents.mkdir(parents=True)
    for label in ("ai.openmousse.server", "ai.openmousse.daily-close", "ai.openmousse.tree"):
        (agents / f"{label}.plist").write_text("<plist/>", encoding="utf8")
    log = h.root / "launchctl.log"
    h.fake("launchctl", f'echo "$@" >> "{log}"\ncase "$1" in print) echo "state = running"; exit 0 ;; esac\nexit 0\n')
    assert go() == 0
    text = log.read_text()
    assert "bootout gui/" in text and "ai.openmousse.server" in text and "ai.openmousse.tree" in text
    assert not list(agents.iterdir())


def test_the_command_line(h):
    r = run_mousse(h, "uninstall", "--dry-run", "--json")
    assert r.returncode == 0, r.stderr
    assert {s["kind"] for s in json.loads(r.stdout)["steps"]} >= {"service", "link"}
    r = run_mousse(h, "uninstall", "--help")
    assert r.returncode == 0 and "--full" in r.stdout
