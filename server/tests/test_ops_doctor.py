"""第 7 批：mousse doctor / status / logs（server/ops/）。临时的家目录、PATH 上的假命令、假的 /api/health；不碰这台机器上真的服务。"""
from __future__ import annotations

import json
import os
import socket
import sqlite3
import stat
from datetime import datetime, timedelta, timezone

import pytest
from ops_helpers import REPO, FakeServer, make_home, ns, run_mousse

import config
from ops import common as C
from ops import doctor, logs, status

KEY = "sk-test-0123456789abcdWXYZ"
KEYS = {"id", "section", "status", "title", "detail", "fix", "fixable", "needsYes", "fixed", "note"}


@pytest.fixture
def h(tmp_path, monkeypatch):
    return make_home(tmp_path, monkeypatch)


def closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def builtin(h, port: int | None = None, **extra) -> dict:
    """一份能用的 builtin 配置：主对话的模型是本机一个不要密钥的假服务商（layer.models = ok）。"""
    cfg = {"claw": {"kind": "builtin"}, "language": "en", "timezone": "UTC", "bind": {"host": "127.0.0.1", "port": port or closed_port()},
           "data_dir": str(h.mousse / "data"), "auth": {"tokens": {"local": "tok-local", "phone": "tok-phone-0123456789abcdef"}},
           "engine": {"model": "box/tiny", "providers": {"box": {"api": "openai", "url": "http://127.0.0.1:9/v1", "key_env": ""}}}, **extra}
    h.write(cfg)
    return cfg


def tidy(h) -> None:
    """装好的样子：仓库软链、venv、0700 的数据目录、一个好的库、世界树的配置。"""
    (h.mousse / "repo").symlink_to(REPO)
    (h.mousse / "venv/bin").mkdir(parents=True)
    (h.mousse / "venv/bin/python").write_text("", encoding="utf8")
    d = h.mousse / "data"
    d.mkdir(mode=0o700)
    with sqlite3.connect(d / "mousse.db") as conn:
        conn.execute("CREATE TABLE t (x)")
    os.chmod(d / "mousse.db", 0o600)
    tree = h.home / ".mousse-tree"
    tree.mkdir()
    (tree / "config.json").write_text('{"storage": "sqlite"}', encoding="utf8")
    os.chmod(tree / "config.json", 0o600)


def run_doctor(capsys, **kw) -> tuple[int, dict]:
    code = doctor.main(ns(json=True, fix=kw.get("fix", False), yes=kw.get("yes", False), strict=kw.get("strict", False)))
    return code, json.loads(capsys.readouterr().out)


def by_id(rep: dict) -> dict[str, dict]:
    return {c["id"]: c for c in rep["checks"]}


def test_doctor_json_contract_and_exit_codes(h, capsys):
    srv = FakeServer(health={"ok": True, "server": {"version": "test", "commit": C.head_commit()}})
    try:
        builtin(h, srv.port)
        tidy(h)
        code, rep = run_doctor(capsys)
        checks = by_id(rep)
        assert all(set(c) == KEYS and c["status"] in ("ok", "warn", "error", "info") for c in rep["checks"])
        for cid in ("system.python", "system.deps", "config.file", "config.claw", "layer.models", "install.repo", "data.dir", "data.db",
                    "service.health", "tree.package"):
            assert checks[cid]["status"] == "ok", checks[cid]
        assert rep["summary"]["error"] == 0 and code == 0 and rep["ok"] is True
        assert rep["version"] and rep["commit"]
        warned = rep["summary"]["warn"]
        code, rep = run_doctor(capsys, strict=True)  # --strict：有 ! 也算没过
        assert code == (1 if warned else 0) and rep["strict"] is True
        # 服务停了：✗、退出码 1，修法是自己起的那一行（没有服务管理器）
        srv.close()
        srv = None
        code, rep = run_doctor(capsys)
        health = by_id(rep)["service.health"]
        assert code == 1 and health["status"] == "error" and "run.py" in health["fix"] and not health["fixable"]
    finally:
        if srv:
            srv.close()


def test_doctor_says_when_the_server_runs_old_code(h, capsys):
    srv = FakeServer(health={"ok": True, "server": {"version": "2026.1.1", "commit": "0000000"}})
    try:
        builtin(h, srv.port)
        code, rep = run_doctor(capsys)
        c = by_id(rep)["service.health"]
        assert c["status"] == "warn" and "old code" in c["title"] and "0000000" in c["detail"] and "pkill" in c["fix"]  # 自己起的：照安装器那一行
    finally:
        srv.close()


def test_doctor_fix_repairs_only_safe_reversible_things(h, capsys):
    now = datetime.now(timezone.utc)
    builtin(h, auth={"tokens": {"local": "tok-local"}, "pairing": [
        {"hash": "old", "expires": (now - timedelta(minutes=5)).isoformat(timespec="seconds"), "name": "gone"},
        {"hash": "new", "expires": (now + timedelta(minutes=5)).isoformat(timespec="seconds"), "name": "still"}]})
    os.chmod(h.config, 0o644)
    (h.mousse / "secrets.env").write_text(f"DEEPSEEK_API_KEY={KEY}\n", encoding="utf8")
    os.chmod(h.mousse / "secrets.env", 0o644)
    # 记忆检索的索引：FTS 坏了（正文还在）
    rdb = h.mousse / "data" / "recall.db"
    rdb.parent.mkdir()
    os.chmod(rdb.parent, 0o755)
    with sqlite3.connect(rdb) as conn:
        conn.execute("CREATE VIRTUAL TABLE chunks_fts USING fts5(body, title)")
        conn.executemany("INSERT INTO chunks_fts(rowid, body, title) VALUES(?,?,?)", [(i, f"note number {i} about tea", "") for i in range(1, 60)])
        conn.execute("UPDATE chunks_fts_data SET block = zeroblob(length(block)) WHERE id > 10")
    os.chmod(rdb, 0o600)
    before = by_id(run_doctor(capsys)[1])
    for cid in ("perm.config", "perm.secrets", "data.dir", "install.repo", "data.pairing", "data.fts"):
        assert before[cid]["status"] in ("warn", "error") and before[cid]["fixable"], before[cid]
    assert before["perm.secrets"]["status"] == "error"  # 别人读得到的密钥文件：✗
    assert KEY not in json.dumps(before)

    code, rep = run_doctor(capsys, fix=True)
    after = by_id(rep)
    for cid in ("perm.config", "perm.secrets", "data.dir", "install.repo", "data.pairing", "data.fts"):
        assert after[cid]["fixed"] and after[cid]["status"] == "ok", after[cid]
    assert stat.S_IMODE(h.config.stat().st_mode) == 0o600 and stat.S_IMODE((h.mousse / "secrets.env").stat().st_mode) == 0o600
    assert stat.S_IMODE((h.mousse / "data").stat().st_mode) == 0o700
    assert (h.mousse / "repo").resolve() == REPO.resolve()
    assert [p["name"] for p in h.read()["auth"]["pairing"]] == ["still"]  # 只清过期的
    baks = list(h.mousse.glob("server.json.bak-*"))
    assert len(baks) == 1 and stat.S_IMODE(baks[0].stat().st_mode) == 0o600 and len(json.loads(baks[0].read_text())["auth"]["pairing"]) == 2
    assert doctor.fts_problem(rdb) is None
    with sqlite3.connect(rdb) as conn:
        assert conn.execute("SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH 'tea'").fetchone()[0] == 59
    assert (h.mousse / "secrets.env").read_text() == f"DEEPSEEK_API_KEY={KEY}\n"  # 只改权限，内容一个字不动
    # 再跑一遍 --fix：没什么可修的，不再备份
    run_doctor(capsys, fix=True)
    assert len(list(h.mousse.glob("server.json.bak-*"))) == 1


def test_doctor_fix_needs_yes_before_touching_a_service(h, capsys):
    log = h.systemd()
    h.unit("openmousse-daily-close.timer", "active")
    srv = FakeServer(health={"ok": True, "server": {"version": "test", "commit": C.head_commit()}})
    try:
        builtin(h, srv.port)
        code, rep = run_doctor(capsys, fix=True)
        c = by_id(rep)["service.server"]
        assert c["status"] == "error" and not c["fixed"] and c["needsYes"] and "--yes" in c["note"]
        assert "restart" not in log.read_text() and code == 1
        code, rep = run_doctor(capsys, fix=True, yes=True)
        c = by_id(rep)["service.server"]
        assert c["fixed"] and c["status"] == "ok"
        assert "--user restart openmousse-server.service" in log.read_text()
        assert "/api/chat/busy" in srv.hits  # 先看有没有回复在跑
    finally:
        srv.close()


def test_doctor_reports_a_broken_config_and_database(h, capsys):
    builtin(h)
    good = h.read()
    (h.mousse / "server.json.bak-20261018-120000").write_text(json.dumps(good), encoding="utf8")
    h.write('{"claw": {"kind": "builtin"}, oops')
    code, rep = run_doctor(capsys, fix=True)
    checks = by_id(rep)
    assert code == 1 and checks["config.file"]["status"] == "error" and "server.json.bak-20261018-120000" in checks["config.file"]["fix"]
    assert checks["layers"]["status"] == "warn" and "config.claw" not in checks
    assert h.config.read_text().endswith("oops")  # 写坏的配置 --fix 不替你换回去：拿不准的不做
    h.write(good)
    db = h.mousse / "data" / "mousse.db"
    db.parent.mkdir()
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE t (x)")
        conn.executemany("INSERT INTO t VALUES (?)", [("x" * 500,)] * 200)
    raw = bytearray(db.read_bytes())
    raw[4096:8192] = b"\xff" * 4096  # 第二页写花
    db.write_bytes(bytes(raw))
    checks = by_id(run_doctor(capsys)[1])
    assert checks["data.db"]["status"] == "error" and "mousse restore" in checks["data.db"]["fix"]


def test_doctor_asks_claude_code_and_codex_themselves(h, capsys, monkeypatch):
    """登没登录照 engine/runners.py：起它们自己的命令查（不读令牌），像密钥的环境变量不给它们。"""
    seen = h.root / "claude-env"
    claude = h.fake("claude", f"""
env > "{seen}"
[ "$1 $2" = "auth status" ] && echo '{{"loggedIn": true, "authMethod": "claude.ai", "subscriptionType": "max"}}' && exit 0
exit 1
""")
    codex = h.fake("codex", 'echo "Not logged in" >&2\nexit 1\n')
    monkeypatch.setenv("SOMETHING_API_KEY", KEY)
    builtin(h, tasks={"runners": {"claude-code": {"bin": str(claude)}, "codex": {"bin": str(codex)}}})
    cfg = h.read()
    cfg["engine"]["model"] = "claude-code/sonnet"
    h.write(cfg)
    checks = {c.id: c for c in doctor.check_logins(doctor.ctx())}
    assert checks["login.claude-code"].status == "ok" and "plan" in checks["login.claude-code"].detail
    assert checks["login.codex"].status == "warn" and checks["login.codex"].fix == "codex login"
    assert KEY not in seen.read_text() and "SOMETHING_API_KEY" not in seen.read_text()
    # 没登录，而主对话用的就是它：✗
    h.fake("claude", 'echo \'{"loggedIn": false}\'\n')
    checks = {c.id: c for c in doctor.check_logins(doctor.ctx())}
    assert checks["login.claude-code"].status == "error"
    # copilot/… 已不再支持（10/21）：配过的给一条提示，不查装了没有，也不报「没开」
    assert "login.copilot" not in checks
    cfg["engine"]["model"] = "copilot/auto"
    h.write(cfg)
    checks = {c.id: c for c in doctor.check_logins(doctor.ctx())}
    assert "login.copilot.off" not in checks and checks["login.copilot"].status == "warn"
    assert "不再支持" in checks["login.copilot"].detail or "no longer supported" in checks["login.copilot"].detail
    cfg["engine"]["copilot"] = {"bin": str(h.fake("copilot", "exit 0\n"))}
    h.write(cfg)
    checks = {c.id: c for c in doctor.check_logins(doctor.ctx())}
    assert checks["login.copilot"].status == "warn" and "login.copilot.off" not in checks


def test_status_one_screen(h, capsys, monkeypatch):
    srv = FakeServer(health={"ok": True, "server": {"version": "2026.10.13-3", "commit": C.head_commit()}})
    try:
        builtin(h, srv.port)
        db = h.mousse / "data" / "mousse.db"
        monkeypatch.setattr(config.settings, "db", db)
        from engine import store
        with store.tx() as conn:  # 内核的表（engine_turns 今天一轮、算了钱）
            now = datetime.now(timezone.utc).isoformat()
            conn.execute("INSERT INTO engine_turns(id, conversation, kind, model, origin, status, usage, started_at) VALUES(?,?,?,?,?,?,?,?)",
                         ("t1", "main", "owner", "box/tiny", "user", "done", json.dumps({"in": 10, "out": 5, "cost": 0.0125}), now))
        with sqlite3.connect(db) as conn:
            conn.execute("CREATE TABLE engine_jobs (id TEXT, state TEXT)")
            conn.executemany("INSERT INTO engine_jobs VALUES (?, ?)", [("a", "on"), ("b", "on"), ("c", "paused"), ("d", "draft"), ("e", "done")])
            conn.execute("CREATE TABLE inbox (id TEXT, status TEXT, expires_at TEXT)")
            past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
            conn.executemany("INSERT INTO inbox VALUES (?,?,?)", [("1", "pending", None), ("2", "pending", ""), ("3", "pending", past), ("4", "approved", None)])
        (h.mousse / "update").mkdir()
        (h.mousse / "update/check.json").write_text(json.dumps({"available": True, "target": "v2026.10.20", "checkedAt": "2026-10-19T08:00:00+00:00"}))
        s = status.gather()
        assert s["server"]["up"] and s["server"]["version"] == "2026.10.13-3" and s["chatModel"] == "box/tiny"
        assert s["today"]["turns"] == 1 and s["today"]["cost"] == pytest.approx(0.0125)
        assert s["jobs"] == {"on": 2, "paused": 1, "draft": 1} and s["inbox"] == 2 and s["update"]["target"] == "v2026.10.20"
        assert status.main(ns(json=False)) == 0
        out = capsys.readouterr().out
        assert "running" in out and "box/tiny" in out and "1 turns · $0.0125" in out and "4 (1 paused), 1 waiting" in out
        assert "2 waiting for your OK" in out and "v2026.10.20 available: mousse update" in out
    finally:
        srv.close()
    assert status.main(ns(json=True)) == 1  # 服务停了
    assert json.loads(capsys.readouterr().out)["server"]["up"] is False


def test_logs_from_files_are_scrubbed(h, capsys):
    builtin(h)
    (h.mousse / "secrets.env").write_text("TAVILY_API_KEY=tvly-abcdefghijklmnop\n", encoding="utf8")
    d = h.mousse / "logs"
    d.mkdir()
    (d / "server.log").write_text("".join(f"line {i}\n" for i in range(50)) + f"ERROR boom {KEY}\nWARN token tok-phone-0123456789abcdef\n"
                                  "error again tvly-abcdefghijklmnop\nINFO done\n", encoding="utf8")
    assert logs.main(ns(follow=False, lines=3, since=None, grep=None, unit="server")) == 0
    out = capsys.readouterr().out.splitlines()
    assert out == ["WARN token ***", "error again ***", "INFO done"]
    assert logs.main(ns(follow=False, lines=5, since="1h", grep="ERROR", unit="server")) == 0
    got = capsys.readouterr()
    assert got.out.splitlines() == ["ERROR boom ***", "error again ***"] and "no timestamps" in got.err
    assert logs.main(ns(follow=False, lines=5, since=None, grep=None, unit="daily-close")) == 1  # 没有这个文件
    assert logs.main(ns(follow=False, lines=-1, since=None, grep=None, unit="server")) == 2


def test_logs_on_systemd_read_the_journal(h, capsys):
    builtin(h)
    h.manager("systemd")
    args = h.root / "journal-args"
    h.fake("journalctl", f'echo "$@" > "{args}"\necho "2026-10-19T08:00:00 host python[1]: started"\n'
                         f'echo "2026-10-19T08:00:01 host python[1]: key {KEY} oops"\n')
    assert logs.main(ns(follow=False, lines=50, since="2h", grep=None, unit="server")) == 0
    out = capsys.readouterr().out
    assert "started" in out and KEY not in out and "key *** oops" in out
    a = args.read_text()
    assert "--user -u openmousse-server.service" in a and "-n 50" in a and "--since -2h" in a
    assert logs.journal_since("30m") == "-30min" and logs.journal_since("2026-10-19 08:00") == "2026-10-19 08:00"


def test_mousse_command_line_runs_the_ops_commands(h):
    builtin(h)
    r = run_mousse(h, "doctor", "--json")
    assert r.returncode == 1, r.stderr  # 服务没开着：✗
    rep = json.loads(r.stdout)
    assert by_id(rep)["service.health"]["status"] == "error" and "Traceback" not in r.stderr
    r = run_mousse(h, "logs")
    assert r.returncode == 1 and "No log file" in r.stderr
    r = run_mousse(h, "--help")
    assert r.returncode == 0 and "mousse doctor" in r.stdout


def test_check_sh_points_at_mousse_doctor():
    text = (REPO / "check.sh").read_text(encoding="utf8")
    assert "mousse doctor" in text
