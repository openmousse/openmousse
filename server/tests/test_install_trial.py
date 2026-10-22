"""macOS 试装和安装清单（第 9 批 15.1，2026-10-19）：packs/core/setup.py 的 launchd plist、install-manifest.json、--trial 的几条规矩，
install.sh 过 bash -n。不起 launchd（launchctl 换成假的），不装东西、不连网。"""
from __future__ import annotations

import importlib.util
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def setup(tmp_path, monkeypatch):
    """packs/core/setup.py 当模块导进来，家目录那几样都指到 tmp_path，launchctl 换成假的（记下调用、回 0；print 回 1 = 还没加载）。"""
    spec = importlib.util.spec_from_file_location("mousse_setup_under_test", REPO / "packs" / "core" / "setup.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    home = tmp_path / "home"
    for attr, val in (("MOUSSE_HOME", home / ".openmousse"), ("SERVER_JSON", home / ".openmousse" / "server.json"),
                      ("MANIFEST", home / ".openmousse" / "install-manifest.json"), ("LAUNCH_AGENTS", home / "Library" / "LaunchAgents"),
                      ("LOG_DIR", home / ".openmousse" / "logs"), ("TREE_HOME", home / ".mousse-tree")):
        monkeypatch.setattr(mod, attr, val)
    monkeypatch.setattr(mod, "IS_MAC", True)
    calls: list[list[str]] = []

    def fake_run(cmd, **kw):
        calls.append([str(c) for c in cmd])
        code = 1 if cmd[:2] == ["launchctl", "print"] else 0
        return subprocess.CompletedProcess(cmd, code, "", "")
    monkeypatch.setattr(mod, "run", fake_run)
    mod.calls = calls
    return mod


def test_server_launch_agent_keeps_running_and_logs_to_a_file(setup, tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "/opt/homebrew/bin:/usr/bin:/bin")
    py, repo = tmp_path / "venv" / "bin" / "python", tmp_path / "repo"
    assert setup.install_launchd(repo, tmp_path / "venv", "Europe/Lisbon")
    plist = plistlib.loads((setup.LAUNCH_AGENTS / "ai.openmousse.server.plist").read_bytes())
    assert plist["Label"] == "ai.openmousse.server" and plist["ProgramArguments"] == [str(py), "run.py"]
    assert plist["RunAtLoad"] is True and plist["KeepAlive"] is True and plist["ThrottleInterval"] == 5
    assert plist["WorkingDirectory"] == str(repo / "server")
    assert plist["StandardOutPath"] == plist["StandardErrorPath"] == str(setup.LOG_DIR / "server.log")
    assert plist["EnvironmentVariables"]["PATH"].startswith("/opt/homebrew/bin")   # launchd 自己的 PATH 找不到 brew 装的东西
    daily = plistlib.loads((setup.LAUNCH_AGENTS / "ai.openmousse.daily-close.plist").read_bytes())
    assert daily["StartCalendarInterval"] == {"Hour": 3, "Minute": 45} and "KeepAlive" not in daily
    assert daily["ProgramArguments"] == [str(py), str(repo / "packs/core/scripts/daily_close.py")]
    assert ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(setup.LAUNCH_AGENTS / "ai.openmousse.server.plist")] in setup.calls


def test_manifest_lists_what_was_installed_once_each(setup, tmp_path):
    setup.begin_manifest("trial", tmp_path / "repo", tmp_path / "venv")
    setup.install_launchd(tmp_path / "repo", tmp_path / "venv", "UTC")
    setup.install_launchd(tmp_path / "repo", tmp_path / "venv", "UTC")             # 再装一遍不重复
    setup.record("symlink", setup.MOUSSE_HOME / "repo", target=tmp_path / "repo")
    setup.record("process", name="openmousse-server", stop="pkill …")
    setup.begin_manifest("trial", tmp_path / "repo", tmp_path / "venv")
    m = json.loads(setup.MANIFEST.read_text())
    assert m["version"] == 1 and m["mode"] == "trial" and m["checkout"] == str(tmp_path / "repo") and len(m["installs"]) == 2
    assert "repo" not in m                                                        # 自己的检出：mousse uninstall --full 不当安装器的那份删
    services = [x for x in m["items"] if x["kind"] == "service"]
    assert sorted(x["name"] for x in services) == ["ai.openmousse.daily-close", "ai.openmousse.server"]
    assert all(x["manager"] == "launchd" and x["path"].endswith(".plist") for x in services)
    assert {"kind": "dir", "path": str(setup.LOG_DIR)} in m["items"]
    assert {"kind": "symlink", "path": str(setup.MOUSSE_HOME / "repo"), "target": str(tmp_path / "repo")} in m["items"]
    assert {"kind": "process", "name": "openmousse-server", "stop": "pkill …"} in m["items"]
    keys = [(x["kind"], x.get("path") or x.get("name")) for x in m["items"]]
    assert len(keys) == len(set(keys))
    assert oct(setup.MANIFEST.stat().st_mode & 0o777) == "0o600"


def test_manifest_names_the_repo_only_when_install_sh_cloned_it(setup, tmp_path, monkeypatch):
    monkeypatch.setenv("MOUSSE_CLONED", "1")
    setup.begin_manifest("builtin", tmp_path / "openmousse", tmp_path / "venv")
    m = json.loads(setup.MANIFEST.read_text())
    assert m["repo"] == m["checkout"] == str(tmp_path / "openmousse")


def test_trial_is_builtin_local_only_and_never_public(setup, tmp_path, monkeypatch):
    """--trial（或 MOUSSE_TRIAL=1）：main() 一开头就改成自带内核、127.0.0.1、不开公网；server.json 照这个写。"""
    seen = {}

    def stop(a, *_args, **_kw):
        seen.update(builtin=a.builtin, bind=a.bind, tree_public=a.tree_public, claw_url=a.claw_url)
        raise SystemExit(0)
    monkeypatch.setattr(setup, "vault_dir", lambda raw: None)
    monkeypatch.setattr(setup, "write_server_json", stop)
    monkeypatch.setattr(sys, "argv", ["setup.py", "--repo", str(REPO), "--venv", str(tmp_path / "venv"), "--trial", "--bind", "0.0.0.0",
                                      "--tree-public", "--claw-url", "http://127.0.0.1:9/v1"])
    with pytest.raises(SystemExit):
        setup.main()
    assert seen == {"builtin": True, "bind": "127.0.0.1", "tree_public": False, "claw_url": None}
    seen.clear()
    monkeypatch.setenv("MOUSSE_TRIAL", "1")
    monkeypatch.setattr(sys, "argv", ["setup.py", "--repo", str(REPO), "--venv", str(tmp_path / "venv")])
    with pytest.raises(SystemExit):
        setup.main()
    assert seen["builtin"] and seen["bind"] == "127.0.0.1"
    m = json.loads(setup.MANIFEST.read_text())
    assert m["installs"][-1]["mode"] == "trial" and not (tmp_path / "home" / ".openclaw").exists()   # 一点不碰 OpenClaw


def test_trial_leaves_a_real_install_alone(setup, tmp_path, monkeypatch, capsys):
    """已经正式装过的（server.json 在、清单不是试装）：--trial 一样不动，退出码 2；上次就是试装的照常再装一遍。"""
    setup.SERVER_JSON.parent.mkdir(parents=True, exist_ok=True)
    setup.SERVER_JSON.write_text(json.dumps({"claw": {"kind": "openclaw"}, "bind": {"host": "100.64.0.1", "port": 8080}}), encoding="utf8")

    def stop(*_a, **_kw):
        raise SystemExit(0)
    monkeypatch.setattr(setup, "vault_dir", lambda raw: None)
    monkeypatch.setattr(setup, "write_server_json", stop)
    monkeypatch.setattr(sys, "argv", ["setup.py", "--repo", str(REPO), "--venv", str(tmp_path / "venv"), "--trial", "--lang", "en"])
    with pytest.raises(SystemExit) as e:
        setup.main()
    assert e.value.code == 2 and "already installed" in capsys.readouterr().out
    assert json.loads(setup.SERVER_JSON.read_text())["claw"]["kind"] == "openclaw" and not setup.MANIFEST.exists()
    setup.begin_manifest("trial", REPO, tmp_path / "venv")
    with pytest.raises(SystemExit) as e:
        setup.main()
    assert e.value.code == 0


def test_trial_server_json_listens_on_this_computer_only(setup, tmp_path):
    import argparse
    a = argparse.Namespace(name="Pebble", tz="UTC", lang="en", bind="127.0.0.1", vault=None)
    cfg, tokens = setup.write_server_json(a, {}, tmp_path / ".openclaw", setup.MOUSSE_HOME / "workspace", REPO, None, {"kind": "builtin"})
    assert cfg["bind"] == {"host": "127.0.0.1", "port": 8080} and cfg["claw"] == {"kind": "builtin"} and set(tokens) == {"phone", "local", "mcp"}
    m = json.loads(setup.MANIFEST.read_text())
    assert {"kind": "file", "path": str(setup.SERVER_JSON)} in m["items"] and {"kind": "dir", "path": str(setup.MOUSSE_HOME)} in m["items"]


def test_trial_without_a_service_starts_it_once_and_records_how_to_stop(setup, tmp_path, monkeypatch):
    started = []

    class P:
        def __init__(self, args, **kw):
            started.append((args, kw["cwd"], kw["start_new_session"]))
    monkeypatch.setattr(setup.subprocess, "Popen", P)
    venv, repo = tmp_path / "venv", tmp_path / "repo"
    assert setup.start_background(repo, venv)
    assert started == [([str(venv / "bin/python"), "run.py"], str(repo / "server"), True)]
    assert setup.calls[0][:2] == ["sh", "-c"] and "pkill" in setup.calls[0][2]          # 先停掉上次自己起的
    m = json.loads(setup.MANIFEST.read_text())
    proc = next(x for x in m["items"] if x["kind"] == "process")
    assert proc["name"] == "openmousse-server" and "pkill" in proc["stop"] and "run.py" in proc["start"]


def test_trial_summary_says_where_the_web_version_is_and_how_phones_connect(setup, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(setup, "lan_ip", lambda: "192.168.1.23")
    cfg = {"bind": {"host": "127.0.0.1", "port": 8080}, "dist": str(tmp_path / "dist")}
    setup.print_trial(cfg, tmp_path / "repo", managed=False, started=True)
    out = capsys.readouterr().out
    assert "npm run web:build" in out and "http://127.0.0.1:8080" in out           # 还没构建：先说怎么构建
    assert "tailscale serve --bg 8080" in out and "MOUSSE_BIND=192.168.1.23" in out and "tokens.py pair" in out
    assert "install-manifest.json" in out and "pkill" in out and "mousse-tree serve" not in out
    setup.print_trial(cfg, tmp_path / "repo", managed=False, started=True, tree=True)
    assert "mousse-tree serve" in capsys.readouterr().out                            # 世界树的服务试装不起：说怎么起
    (tmp_path / "dist").mkdir()
    setup.print_trial(cfg, tmp_path / "repo", managed=True, started=False)
    out = capsys.readouterr().out
    assert "open http://127.0.0.1:8080 in a browser" in out and "pkill" not in out


@pytest.mark.skipif(shutil.which("bash") is None, reason="no bash")
def test_install_sh_parses_and_knows_the_mac_and_trial_paths():
    assert subprocess.run(["bash", "-n", str(REPO / "install.sh")], check=False).returncode == 0
    text = (REPO / "install.sh").read_text()
    for needle in ("/opt/homebrew/bin/python3", "xcode-select --install", "MOUSSE_TRIAL", "--no-systemd", "MOUSSE_BIND=127.0.0.1", "MOUSSE_CLONED=1"):
        assert needle in text, needle


def test_install_sh_braces_variables_before_non_ascii_text():
    """macOS 自带的 bash 3.2 把紧跟在变量名后的中文字节也算进名字：「$MOUSSE_TZ」」在 set -u 下报 unbound variable（10/22 Mac 上试装撞到）。要写 ${MOUSSE_TZ}」。"""
    text = (REPO / "install.sh").read_text(encoding="utf8")
    bad = [line.strip() for line in text.splitlines() if re.search(r"\$[A-Za-z_][A-Za-z0-9_]*[^\x00-\x7f]", line)]
    assert not bad, bad


# —— 装自带内核时问要不要搬（第 9 批 15.8）——————————————————————————————————————————————————

@pytest.fixture
def homes(tmp_path, monkeypatch):
    oc, hermes = tmp_path / "oc", tmp_path / "hermes"
    oc.mkdir()
    hermes.mkdir()
    (oc / "openclaw.json").write_text("{}", encoding="utf8")
    (hermes / "config.yaml").write_text("model: {}\n", encoding="utf8")
    monkeypatch.setenv("HERMES_HOME", str(hermes))
    return oc, hermes


def test_migration_is_only_suggested_when_nobody_can_answer(setup, homes, tmp_path, monkeypatch, capsys):
    oc, hermes = homes
    monkeypatch.setenv("MOUSSE_NONINTERACTIVE", "1")
    ran = []
    monkeypatch.setattr(setup.subprocess, "run", lambda *a, **kw: ran.append(a) or subprocess.CompletedProcess(a, 0))
    assert setup.found_homes(oc) == [("openclaw", oc), ("hermes", hermes)]
    assert setup.offer_migration(tmp_path / "venv", REPO, oc) is False
    out = capsys.readouterr().out
    assert ran == [] and f"mousse migrate openclaw --openclaw-home {oc}" in out and f"mousse migrate hermes --hermes-home {hermes}" in out


def test_the_report_comes_first_and_writing_needs_a_second_yes(setup, homes, tmp_path, monkeypatch):
    oc, _hermes = homes
    answers = iter([True, False, False])            # OpenClaw：看报告、不写；Hermes：不看
    monkeypatch.setattr(setup, "tty_yes", lambda prompt, default=True: next(answers))
    ran = []
    monkeypatch.setattr(setup.subprocess, "run", lambda cmd, **kw: ran.append(cmd) or subprocess.CompletedProcess(cmd, 0))
    assert setup.offer_migration(tmp_path / "venv", REPO, oc) is False
    assert ran == [[str(tmp_path / "venv/bin/python"), str(REPO / "server/mousse.py"), "migrate", "openclaw", "--openclaw-home", str(oc)]]   # 只看了
    answers = iter([True, True, False])
    ran.clear()
    assert setup.offer_migration(tmp_path / "venv", REPO, oc) is True
    assert [c[-1] for c in ran] == [str(oc), "--apply"]
    m = json.loads(setup.MANIFEST.read_text())
    assert any(x["kind"] == "edited" and x["path"] == str(setup.SERVER_JSON) and "migrate openclaw" in x["note"] for x in m["items"])


def test_writing_defaults_to_no(setup, monkeypatch, tmp_path):
    tty = tmp_path / "tty"
    tty.write_text("\n", encoding="utf8")
    real_open = open
    monkeypatch.setattr("builtins.open", lambda f, *a, **kw: real_open(tty, *a, **kw) if f == "/dev/tty" else real_open(f, *a, **kw))
    monkeypatch.delenv("MOUSSE_NONINTERACTIVE", raising=False)
    assert setup.tty_yes("Look first?") is True                          # 回车 = 是
    assert setup.tty_yes("Write it?", default=False) is False             # 回车 = 不写
