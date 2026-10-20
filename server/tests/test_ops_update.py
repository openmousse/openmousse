"""第 7 批：mousse update（server/ops/update.py）。远端是本地的一个裸 git 仓库（带 v* tag），装的仓库是它的克隆；venv 的 python 是个替身
（pip 只记一笔，别的交给跑测试的 python），服务用假的 systemctl、假的 /api/health。不联网、不碰这台机器上真的东西。"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from ops_helpers import FakeServer, make_home, ns, run_mousse

from ops import common as C
from ops import update as U


def sh(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def commit(src: Path, files: dict[str, str], msg: str, tag: str | None = None) -> str:
    for name, text in files.items():
        p = src / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf8")
    sh(src, "add", "-A")
    sh(src, "commit", "-q", "-m", msg)
    if tag:
        sh(src, "tag", "-a", tag, "-m", tag)
    return sh(src, "rev-parse", "HEAD")


class World:
    pass


@pytest.fixture
def w(tmp_path, monkeypatch):
    h = make_home(tmp_path, monkeypatch)
    for k, v in (("GIT_AUTHOR_NAME", "Test"), ("GIT_AUTHOR_EMAIL", "test@example.invalid"), ("GIT_COMMITTER_NAME", "Test"),
                 ("GIT_COMMITTER_EMAIL", "test@example.invalid"), ("GIT_CONFIG_NOSYSTEM", "1")):
        monkeypatch.setenv(k, v)
    x = World()
    x.h = h
    x.src = tmp_path / "src"
    x.src.mkdir()
    sh(x.src, "init", "-q", "-b", "main")
    x.v1 = commit(x.src, {"server/version.py": 'VERSION = "2026.10.1"\n', "server/requirements.txt": "fastapi>=0.115\n", "server/app.py": "x = 1\n",
                          "tree/pyproject.toml": "[project]\nname = 't'\n"}, "first", "v2026.10.1")
    x.v2 = commit(x.src, {"server/version.py": 'VERSION = "2026.10.2"\n', "server/app.py": "x = 2\n",
                          "server/requirements.txt": "fastapi>=0.115\nrich>=13\n"}, "second", "v2026.10.2")
    x.main = commit(x.src, {"server/app.py": "x = 3\n"}, "third (main only)")
    x.remote = tmp_path / "remote.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(x.src), str(x.remote)], check=True)
    x.repo = tmp_path / "install"
    subprocess.run(["git", "clone", "-q", str(x.remote), str(x.repo)], check=True)
    sh(x.repo, "reset", "-q", "--hard", "v2026.10.1")  # 装的时候在 main 上，停在第一版
    monkeypatch.setenv("MOUSSE_REPO", str(x.repo))
    x.pip = tmp_path / "pip.log"
    (h.mousse / "venv/bin").mkdir(parents=True)
    py = h.mousse / "venv/bin/python"
    py.write_text(f'#!/bin/sh\nif [ "$1" = "-m" ] && [ "$2" = "pip" ]; then echo "$@" >> "{x.pip}"; exit 0; fi\nexec "{sys.executable}" "$@"\n',
                  encoding="utf8")
    py.chmod(0o755)
    x.server = None

    def serve(health="dynamic") -> FakeServer:
        body = (lambda: {"ok": True, "server": {"version": "test", "commit": sh(x.repo, "rev-parse", "--short=7", "HEAD")}}) if health == "dynamic" else health
        x.server = FakeServer(token="tok-local", health=body)
        cfg = h.read() if h.config.exists() else {}
        cfg.setdefault("bind", {})["port"] = x.server.port
        h.write(cfg)
        return x.server
    x.serve = serve
    h.write({"claw": {"kind": "builtin"}, "language": "en", "bind": {"host": "127.0.0.1", "port": 9}, "data_dir": str(h.mousse / "data"),
             "workspace": str(h.mousse / "workspace"), "auth": {"tokens": {"local": "tok-local"}}, "update": {"wait": 3}})
    yield x
    if x.server:
        x.server.close()


def run(w, capsys, **kw) -> tuple[int, str]:
    a = ns(check=kw.get("check", False), plan=kw.get("plan", False), channel=kw.get("channel"), yes=kw.get("yes", True), stash=kw.get("stash", False),
           json=kw.get("json", False))
    code = U.main(a)
    return code, capsys.readouterr().out


def test_check_and_plan_change_nothing(w, capsys):
    code, out = run(w, capsys, check=True)
    assert code == 0 and "v2026.10.2" in out and sh(w.repo, "rev-parse", "HEAD") == w.v1
    st = C.update_state()
    assert st["available"] and st["target"] == "v2026.10.2" and st["behind"] == 1 and st["channel"] == "stable"
    code, out = run(w, capsys, plan=True, json=True)
    p = json.loads(out)
    assert code == 0 and p["commitCount"] == 1 and "second" in p["commits"][0] and "+rich>=13" in p["dependencies"]
    assert p["restart"] == ["openmousse-server"] and sh(w.repo, "rev-parse", "HEAD") == w.v1 and not w.pip.exists()
    # main 通道：记进 server.json（先备份），比 stable 多一个提交
    code, out = run(w, capsys, check=True, channel="main")
    assert code == 0 and C.update_state()["behind"] == 2 and w.h.read()["update"]["channel"] == "main"
    assert list(w.h.mousse.glob("server.json.bak-*"))
    r = run_mousse(w.h, "update", "--check", MOUSSE_REPO=str(w.repo))
    assert r.returncode == 0 and "main" in r.stdout and "Traceback" not in r.stderr


def test_update_applies_restarts_and_verifies(w, capsys):
    log = w.h.systemd()
    w.h.unit("openmousse-server.service", "active")
    w.serve()
    code, out = run(w, capsys)
    assert code == 0, out
    assert sh(w.repo, "rev-parse", "HEAD") == w.v2 and "Updated:" in out
    pip = w.pip.read_text().splitlines()
    assert len(pip) == 2 and "requirements.txt" in pip[0] and pip[1].endswith("/tree")
    assert "--user restart openmousse-server.service" in log.read_text()
    assert "/api/chat/busy" in w.server.hits  # 先等进行中的回复
    snaps = list((w.h.mousse / "backups").glob("openmousse-snapshot-pre-update-*.tar.gz"))
    assert len(snaps) == 1
    last = json.loads((w.h.mousse / "update/last.json").read_text())
    assert last["ok"] and last["restarted"] and last["to"] == w.v2[:7] and Path(last["log"]).exists()
    assert "compileall" in Path(last["log"]).read_text()
    assert C.update_state()["available"] is False
    code, out = run(w, capsys)  # 再跑一遍：没有可更的
    assert code == 0 and "Up to date" in out


def test_update_refuses_what_it_should_not_touch(w, capsys, monkeypatch, tmp_path):
    (w.repo / "server/app.py").write_text("x = 'mine'\n", encoding="utf8")
    code, out = run(w, capsys)
    assert code == 2 and "uncommitted changes" in out and sh(w.repo, "rev-parse", "HEAD") == w.v1
    w.h.systemd()
    w.h.unit("openmousse-server.service", "active")
    w.serve()
    code, out = run(w, capsys, stash=True)  # --stash：替你藏起来再更新
    assert code == 0 and sh(w.repo, "rev-parse", "HEAD") == w.v2 and "mousse update" in sh(w.repo, "stash", "list")
    monkeypatch.setenv("MOUSSE_INSTALL", "docker")
    code, out = run(w, capsys)
    assert code == 2 and "docker compose pull" in out
    monkeypatch.delenv("MOUSSE_INSTALL")
    sh(w.repo, "checkout", "-q", "-b", "feature", w.v1)  # 在跑别的开发分支：不硬切
    (w.repo / "server/extra.py").write_text("y = 1\n", encoding="utf8")
    sh(w.repo, "add", "-A")
    sh(w.repo, "commit", "-q", "-m", "local work")
    mine = sh(w.repo, "rev-parse", "HEAD")
    code, out = run(w, capsys)
    assert code == 2 and "development branch" in out and sh(w.repo, "rev-parse", "HEAD") == mine
    plain = tmp_path / "plain"
    (plain / "server").mkdir(parents=True)
    monkeypatch.setenv("MOUSSE_REPO", str(plain))
    code, out = run(w, capsys)
    assert code == 2 and "isn't a git checkout" in out


def test_update_rolls_back_when_it_does_not_compile(w, capsys):
    log = w.h.systemd()
    w.h.unit("openmousse-server.service", "active")
    w.serve()
    assert run(w, capsys)[0] == 0  # 先到 v2
    broken = commit(w.src, {"server/broken.py": "def oops(:\n"}, "broken", "v2026.10.3")
    sh(w.src, "push", "-q", str(w.remote), "main", "v2026.10.3")
    restarts = log.read_text().count("restart")
    code, out = run(w, capsys)
    assert code == 1 and "doesn't compile" in out and "Rolled back" in out
    assert sh(w.repo, "rev-parse", "HEAD") == w.v2 != broken
    assert log.read_text().count("restart") == restarts  # 服务没动
    last = json.loads((w.h.mousse / "update/last.json").read_text())
    assert last["rolledBack"] and last["why"] == "compile"
    assert len(w.pip.read_text().splitlines()) == 2 + 4  # 新的装一遍、旧的再装回去


def test_update_rolls_back_when_the_server_does_not_answer(w, capsys):
    log = w.h.systemd()
    w.h.unit("openmousse-server.service", "active")
    w.serve(health=None)  # /api/health 一直 500
    code, out = run(w, capsys)
    assert code == 1 and "didn't answer" in out and sh(w.repo, "rev-parse", "HEAD") == w.v1
    assert log.read_text().count("--user restart openmousse-server.service") == 2  # 新的起一次、回滚后再起一次
    last = json.loads((w.h.mousse / "update/last.json").read_text())
    assert last["rolledBack"] and last["why"] == "health" and last["restored"] is False
    assert "mousse restore" in out  # 数据要退回去：快照在那儿


def test_update_from_a_shallow_clone_without_a_service_manager(w, capsys, monkeypatch, tmp_path):
    shallow = tmp_path / "shallow"
    subprocess.run(["git", "clone", "-q", "--depth", "1", "--branch", "v2026.10.1", f"file://{w.remote}", str(shallow)], check=True)
    assert sh(shallow, "rev-parse", "--is-shallow-repository") == "true"
    monkeypatch.setenv("MOUSSE_REPO", str(shallow))
    code, out = run(w, capsys)  # 没有服务管理器：代码换好，告诉你自己怎么重启
    assert code == 0 and "restart it yourself" in out and sh(shallow, "rev-parse", "HEAD") == w.v2
    assert sh(shallow, "rev-parse", "--is-shallow-repository") == "false"


def test_update_follows_main_and_never_goes_backwards(w, capsys):
    w.h.systemd()
    w.h.unit("openmousse-server.service", "active")
    w.serve()
    code, out = run(w, capsys, channel="main")
    assert code == 0 and sh(w.repo, "rev-parse", "HEAD") == w.main and sh(w.repo, "symbolic-ref", "--short", "HEAD") == "main"
    assert w.h.read()["update"]["channel"] == "main"
    code, out = run(w, capsys, channel="stable")  # main 比最新的发版新：stable 没有可更的，不往回退
    assert code == 0 and "nothing to update" in out and sh(w.repo, "rev-parse", "HEAD") == w.main
    code, out = run(w, capsys, yes=False)  # 没有终端也没有 --yes：不动
    assert code == 2 and "--yes" in out


def test_a_failed_switch_to_main_stays_where_it_was(w, capsys):
    """在发版的 tag 上、本地的 main 还很旧，切 main 的时候没成（没跟踪的文件挡着）：不许停在旧的本地 main 上（比在跑的还旧，下次重启就跑它）。"""
    w.h.systemd()
    w.h.unit("openmousse-server.service", "active")
    w.serve()
    assert run(w, capsys)[0] == 0 and sh(w.repo, "rev-parse", "HEAD") == w.v2  # stable：detached 在 v2，本地 main 还在 v1
    commit(w.src, {"server/extra.py": "y = 1\n"}, "fourth (main only)")
    sh(w.src, "push", "-q", str(w.remote), "main")
    (w.repo / "server/extra.py").write_text("mine = True\n", encoding="utf8")  # 没跟踪的：切到 main 会被覆盖，git 不肯
    code, out = run(w, capsys, channel="main")
    assert code == 1 and sh(w.repo, "rev-parse", "HEAD") == w.v2, out
    assert subprocess.run(["git", "symbolic-ref", "-q", "HEAD"], cwd=w.repo).returncode != 0  # 还是 detached，没切到 main 上
    assert (w.repo / "server/extra.py").read_text() == "mine = True\n"


def test_safe_restart_uses_launchd_on_a_mac(w, monkeypatch, capsys):
    """safe_restart.py：macOS 上没有 systemctl，重启走 launchctl kickstart -k（同 mousse update）。"""
    import safe_restart
    calls = w.h.root / "launchctl.log"
    w.h.fake("launchctl", f'echo "$@" >> "{calls}"\ncase "$1" in print) echo "state = running" ;; esac\n')
    w.h.manager("launchd")
    w.serve()
    monkeypatch.setenv("MOUSSE_SERVER_CONFIG", str(w.h.config))
    monkeypatch.setattr(sys, "argv", ["safe_restart.py", "--wait", "5"])
    safe_restart.main()
    assert "kickstart -k gui/" in calls.read_text() and "ai.openmousse.server" in calls.read_text()
    assert "restarted openmousse-server" in capsys.readouterr().out
