"""第 7 批：mousse backup create / verify / list、mousse restore（server/ops/backup.py）。临时的家目录；服务用假的 systemctl、假的 /api/health。"""
from __future__ import annotations

import hashlib
import io
import json
import os
import sqlite3
import stat
import tarfile
from pathlib import Path

import pytest
from ops_helpers import FakeServer, make_home, ns, run_mousse

from ops import backup as B

KEY = "sk-test-0123456789abcdWXYZ"


@pytest.fixture
def h(tmp_path, monkeypatch):
    home = make_home(tmp_path, monkeypatch)
    n = iter(range(100000))
    monkeypatch.setattr(B, "stamp", lambda: f"20261019-{next(n):06d}")  # 一秒里打好几个包也不撞名
    return home


def setup_install(h, port: int = 9) -> dict:
    """一台装好、用过一阵的样子：库（WAL，还有没 checkpoint 的写）、上传的文件、密钥、ChatGPT 凭据、工作区、Agent 的工作区、世界树。"""
    m = h.mousse
    ws, agent_ws = m / "workspace", h.home / ".openclaw" / "workspace-fitness"
    cfg = {"claw": {"kind": "builtin"}, "language": "en", "bind": {"host": "127.0.0.1", "port": port}, "data_dir": str(m / "data"),
           "workspace": str(ws), "agent_workspaces": {"fitness": str(agent_ws)}, "auth": {"tokens": {"local": "tok-local", "phone": "tok-phone-xyz"}}}
    h.write(cfg)
    (m / "secrets.env").write_text(f"OPENAI_API_KEY={KEY}\n", encoding="utf8")
    os.chmod(m / "secrets.env", 0o600)
    data = m / "data"
    for d in ("uploads/2026-10", "chatgpt", "apps", "sentinel", "social"):
        (data / d).mkdir(parents=True)
    (data / "uploads/2026-10/photo.jpg").write_bytes(os.urandom(5000))
    (data / "chatgpt/state.json").write_text('{"access": "secret"}', encoding="utf8")
    (data / "apps/notion.json").write_text('{"token": "secret"}', encoding="utf8")
    (data / "sentinel/secrets.json").write_text("{}", encoding="utf8")
    (data / "social/identity.json").write_text('{"seed": "secret"}', encoding="utf8")
    (data / "gateway-device.json").write_text('{"key": "secret"}', encoding="utf8")
    (data / "mousse.db.lock").write_text("", encoding="utf8")
    db = sqlite3.connect(data / "mousse.db")
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, text TEXT)")
    db.executemany("INSERT INTO messages(text) VALUES (?)", [(f"hello {i}",) for i in range(300)])
    db.commit()
    h.db = db  # 开着不关：-wal 里还有没 checkpoint 的，备份要用 backup() 才拿得到
    ws.mkdir(parents=True)
    (ws / "MEMORY.md").write_text("# Memory\n- likes tea\n", encoding="utf8")
    (ws / "skills").symlink_to(h.root / "elsewhere")
    agent_ws.mkdir(parents=True)
    (agent_ws / "MEMORY.md").write_text("# Fitness\n", encoding="utf8")
    tree = h.home / ".mousse-tree"
    tree.mkdir()
    (tree / "config.json").write_text('{"storage": "sqlite", "tokens": {"tk": "claude"}}', encoding="utf8")
    with sqlite3.connect(tree / "tree.db") as t:
        t.execute("CREATE TABLE tree (id TEXT, text TEXT)")
        t.execute("INSERT INTO tree VALUES ('a', 'Alex likes tea')")
    (tree / "tree.log").write_text("noise\n", encoding="utf8")
    return cfg


def fingerprint(p: Path) -> str:
    """文件的样子：SQLite 比内容（backup() 拷出来的页和原文件不一定一字不差），别的比字节。"""
    raw = p.read_bytes()
    if raw.startswith(b"SQLite format 3\x00"):
        with sqlite3.connect(f"file:{p}?mode=ro", uri=True) as c:
            return "db:" + hashlib.sha256("\n".join(c.iterdump()).encode()).hexdigest()
    return hashlib.sha256(raw).hexdigest()


def digest(paths: list[Path]) -> dict[str, str]:
    out = {}
    for root in paths:
        if root.is_file():
            out[str(root)] = fingerprint(root)
            continue
        for p in sorted(root.rglob("*")):
            if p.is_file() and not p.is_symlink() and not p.name.endswith(("-wal", "-shm")):
                out[str(p)] = fingerprint(p)
    return out


def rows(db: Path) -> int:
    with sqlite3.connect(db) as c:
        return c.execute("SELECT COUNT(*) FROM messages").fetchone()[0]


def members(path: str) -> list[str]:
    with tarfile.open(path, "r:gz") as tf:
        return [m.name.split("/", 1)[1] for m in tf.getmembers() if m.isfile()]


def test_backup_then_restore_round_trip(h, capsys):
    setup_install(h)
    r = B.create(None)
    p = Path(r["path"])
    assert p.parent == h.mousse / "backups" and p.name == "openmousse-backup-20261019-000000.tar.gz"
    assert stat.S_IMODE(p.stat().st_mode) == 0o600 and stat.S_IMODE(p.parent.stat().st_mode) == 0o700
    names = members(r["path"])
    assert "files/data/mousse.db" in names and "files/secrets/secrets.env" in names and "files/data/chatgpt/state.json" in names
    assert "files/tree/tree.db" in names and "files/workspace-main/MEMORY.md" in names and "files/workspace-fitness/MEMORY.md" in names
    assert not any(n.endswith(("-wal", "-shm", ".lock", ".log")) for n in names)
    man = B.verify(r["path"])["manifest"]
    assert {a["name"]: a["path"] for a in man["areas"]}["data"] == "~/.openmousse/data"  # 相对家目录：换机器也能恢复
    assert {"area": "workspace-main", "path": "skills", "target": str(h.root / "elsewhere")} in man["links"]
    db = next(f for f in man["files"] if f["path"] == "mousse.db")
    assert db["sqlite"] and man["secrets"] is True and man["version"] and man["commit"]
    data = h.mousse / "data"
    watched = [h.config, h.mousse / "secrets.env", data, h.mousse / "workspace", h.home / ".mousse-tree", h.home / ".openclaw"]
    before, n0 = digest(watched), rows(data / "mousse.db")
    assert n0 == 300
    # 然后东西变了、坏了
    h.db.execute("DELETE FROM messages WHERE id > 10")
    h.db.commit()
    h.db.close()
    (h.mousse / "workspace/MEMORY.md").write_text("# oops\n", encoding="utf8")
    (data / "uploads/2026-10/photo.jpg").unlink()
    (h.mousse / "secrets.env").write_text("OPENAI_API_KEY=sk-test-other-000000000000\n", encoding="utf8")
    h.write({**h.read(), "app_name": "Changed"})
    changed = digest(watched)
    assert changed != before

    assert B.restore(r["path"], dry_run=True) == 0  # 只看：一个字节不动
    assert digest(watched) == changed
    assert B.restore(r["path"]) == 0
    out = capsys.readouterr().out
    assert not (data / "mousse.db-wal").exists()  # 旧的 -wal 先删了（不然 SQLite 会把它叠到换上的库上）
    assert digest(watched) == before
    assert rows(data / "mousse.db") == 300
    assert stat.S_IMODE((h.mousse / "secrets.env").stat().st_mode) == 0o600
    snap = next(p for p in (h.mousse / "backups").iterdir() if p.name.startswith("openmousse-snapshot-pre-restore-"))
    assert snap.name in out and "mousse restore" in out
    # 撤回：拿快照恢复，回到恢复之前（变过的样子）
    assert B.restore(str(snap)) == 0
    assert rows(data / "mousse.db") == 10 and (h.mousse / "workspace/MEMORY.md").read_text() == "# oops\n"


def test_restoring_the_oldest_snapshot_keeps_it(h, capsys):
    """已经有 SNAP_KEEP 份恢复前的快照，从最旧的那份恢复：恢复前那次快照清旧的时候不许把它删了（不然解包时没了，也撤不回）。"""
    setup_install(h)
    h.db.close()
    snaps = [Path(B.snapshot("pre-restore")["path"]) for _ in range(B.SNAP_KEEP)]
    oldest = snaps[0]
    (h.mousse / "workspace/MEMORY.md").write_text("# changed\n", encoding="utf8")
    assert B.restore(str(oldest)) == 0, capsys.readouterr().out
    assert oldest.exists() and (h.mousse / "workspace/MEMORY.md").read_text().startswith("# Memory")
    left = sorted(p.name for p in (h.mousse / "backups").iterdir() if p.name.startswith("openmousse-snapshot-pre-restore-"))
    assert oldest.name in left and len(left) == B.SNAP_KEEP + 1


def test_a_locked_database_fails_fast_and_browser_profiles_stay_out(h, monkeypatch):
    """别的程序独占锁着的库（Chromium 开着时的配置档就是这样）：等一阵拷不出来就报错、说是哪个文件，不会一直卡着；沙箱浏览器的配置档不进包。"""
    import subprocess
    import sys
    import time

    from ops import common as C
    setup_install(h)
    h.db.close()
    prof = h.mousse / "data/engine/browser-profiles/main/Default"
    prof.mkdir(parents=True)
    (prof / "Cookies.txt").write_text("session=secret", encoding="utf8")
    assert not any("browser-profiles" in n for n in members(B.create(None)["path"]))
    locked = h.mousse / "data/other.db"
    holder = subprocess.Popen([sys.executable, "-c", (
        "import sqlite3, sys, time\n"
        f"c = sqlite3.connect({str(locked)!r}, isolation_level=None)\n"
        "c.execute('PRAGMA locking_mode=EXCLUSIVE'); c.execute('CREATE TABLE t (x)'); c.execute('BEGIN EXCLUSIVE'); c.execute('INSERT INTO t VALUES (1)')\n"
        "print('locked', flush=True); time.sleep(60)\n")], stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "locked"
        monkeypatch.setattr(C, "SQLITE_BUSY_WAIT", 1.0)
        t0 = time.monotonic()
        with pytest.raises(B.BackupError) as e:
            B.create(None)
        assert time.monotonic() - t0 < 10 and "other.db" in str(e.value)
    finally:
        holder.kill()
        holder.wait()
    assert not [p for p in (h.mousse / "backups").iterdir() if p.name.endswith(".tmp")]  # 写了一半的包删了


def test_backup_flags_and_keep(h, capsys):
    setup_install(h)
    r = B.create(None, secrets=False, workspace=False, tree=False)
    names = members(r["path"])
    assert "files/config/server.json" in names and "files/data/mousse.db" in names and "files/data/uploads/2026-10/photo.jpg" in names
    for gone in ("secrets/", "data/chatgpt/", "data/apps/", "data/sentinel/", "data/social/identity.json", "data/gateway-device.json",
                 "tree/", "workspace-"):
        assert not any(n.startswith("files/" + gone) for n in names), gone
    man = B.verify(r["path"])["manifest"]
    assert man["secrets"] is False and man["include"] == {"secrets": False, "workspace": False, "tree": False}
    snap = B.snapshot("pre-update")
    for _ in range(3):
        B.create(None, keep=2)
    left = sorted(p.name for p in (h.mousse / "backups").iterdir())
    assert len([n for n in left if n.startswith("openmousse-backup-")]) == 2 and Path(snap["path"]).name in left  # 快照不算在 --keep 里
    with pytest.raises(B.BackupError, match="inside"):  # 包不能放进要备份的目录里
        B.create(str(h.mousse / "data" / "x.tar.gz"))
    assert B.main_backup(ns(action="create", out=str(h.root / "out"), no_secrets=False, no_workspace=False, no_tree=False, keep=None, json=False)) == 0
    assert "as safe as a key" in capsys.readouterr().out and len(list((h.root / "out").glob("*.tar.gz"))) == 1
    assert B.main_backup(ns(action="list")) == 0
    assert "openmousse-snapshot-pre-update-" in capsys.readouterr().out


def test_backup_into_a_folder_that_holds_the_data(h):
    """--out 是家目录这种包着数据的目录：照样整块备份（只跳过输出目录在某一块里面的情况）。"""
    setup_install(h)
    r = B.create(str(h.home), keep=None)
    names = members(r["path"])
    assert Path(r["path"]).parent == h.home
    assert "files/data/uploads/2026-10/photo.jpg" in names and "files/data/chatgpt/state.json" in names and "files/tree/tree.db" in names
    assert any(n.startswith("files/workspace-main/") for n in names) and B.verify(r["path"])["ok"]


def rebuild(src: str, dst: Path, change) -> None:
    """照原包重新打一个，中途 change(名字, 内容) → (名字, 内容) | None（None = 不要这一个）。"""
    with tarfile.open(src, "r:gz") as tin, tarfile.open(dst, "w:gz") as tout:
        for m in tin.getmembers():
            if not m.isfile():
                continue
            got = change(m.name, tin.extractfile(m).read())
            if got is None:
                continue
            name, data = got
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tout.addfile(info, io.BytesIO(data))


def test_verify_and_restore_refuse_a_damaged_archive(h, capsys):
    setup_install(h)
    good = B.create(None)["path"]
    assert B.verify(good)["ok"]
    bad = h.root / "bad"
    bad.mkdir()

    def flip(name, data):
        if name.endswith("workspace-main/MEMORY.md"):
            return name, data.replace(b"tea", b"tee")  # 一个字节
        return name, data
    rebuild(good, bad / "flip.tar.gz", flip)
    with tarfile.open(good, "r:gz") as tf:
        root = tf.getmembers()[0].name.split("/")[0]
    with tarfile.open(good, "r:gz") as tin, tarfile.open(bad / "extra.tar.gz", "w:gz") as tout:  # 多塞一个 manifest 里没有的文件
        for m in tin.getmembers():
            if m.isfile():
                tout.addfile(m, tin.extractfile(m))
        info = tarfile.TarInfo(f"{root}/files/data/planted.txt")
        info.size = 3
        tout.addfile(info, io.BytesIO(b"bad"))
    with tarfile.open(bad / "escape.tar.gz", "w:gz") as tout:
        info = tarfile.TarInfo(f"{root}/../../evil.txt")
        info.size = 3
        tout.addfile(info, io.BytesIO(b"bad"))
    with tarfile.open(good, "r:gz") as tin, tarfile.open(bad / "link.tar.gz", "w:gz") as tout:
        for m in tin.getmembers():
            if m.isfile():
                tout.addfile(m, tin.extractfile(m))
        info = tarfile.TarInfo(f"{root}/files/data/link")
        info.type, info.linkname = tarfile.SYMTYPE, "/etc/passwd"
        tout.addfile(info)

    def broken_db(name, data):
        if name.endswith("files/data/mousse.db"):
            data = data[:2048] + b"\xff" * (len(data) - 2048)
        return name, data
    rebuild(good, bad / "db.tar.gz", broken_db)
    # 改了库再把 manifest 的 sha256 也改对：大小、sha256 对上了，quick_check 照样拦住
    man_fix = {}

    def broken_db_consistent(name, data):
        if name.endswith("files/data/mousse.db"):
            data = data[:2048] + b"\xff" * (len(data) - 2048)
            man_fix["sha"] = hashlib.sha256(data).hexdigest()
        if name.endswith("manifest.json"):
            m = json.loads(data)
            for f in m["files"]:
                if f["path"] == "mousse.db":
                    f["sha256"] = man_fix["sha"]
            data = json.dumps(m).encode()
        return name, data
    rebuild(good, bad / "db2.tar.gz", broken_db_consistent)
    raw = Path(good).read_bytes()
    (bad / "truncated.tar.gz").write_bytes(raw[: len(raw) // 2])
    (bad / "notar.tar.gz").write_bytes(b"hello")
    expect = {"flip": "doesn't match", "extra": "unexpected file", "escape": "unsafe path", "link": "not a regular file", "db": "doesn't match",
              "db2": "quick_check", "truncated": "damaged", "notar": "can't open"}
    watched = [h.config, h.mousse / "secrets.env", h.mousse / "data", h.mousse / "workspace", h.home / ".mousse-tree"]
    before = digest(watched)
    for name, why in expect.items():
        rep = B.verify(bad / f"{name}.tar.gz")
        assert not rep["ok"] and any(why in e for e in rep["errors"]), (name, rep["errors"])
        assert B.restore(str(bad / f"{name}.tar.gz")) == 1
        assert "nothing was written" in capsys.readouterr().out
    assert digest(watched) == before  # 坏包：一个字节都没写
    assert not any(p.name.startswith("openmousse-snapshot-") for p in (h.mousse / "backups").iterdir())  # 连快照都没做


def test_restore_waits_for_the_server_to_stop(h, capsys):
    srv = FakeServer(token="tok-local")
    try:
        setup_install(h, port=srv.port)
        good = B.create(None)["path"]
        (h.mousse / "workspace/MEMORY.md").write_text("# changed\n", encoding="utf8")
        assert B.restore(good) == 2  # 服务在跑，没 --yes：不动，告诉你先停
        out = capsys.readouterr().out
        assert "Stop it first" in out and "pkill" in out and (h.mousse / "workspace/MEMORY.md").read_text() == "# changed\n"
        log = h.systemd()
        h.unit("openmousse-server.service", "active")
        assert B.restore(good, yes=True) == 0
        calls = log.read_text()
        assert calls.index("--user stop openmousse-server.service") < calls.index("--user start openmousse-server.service")
        assert (h.mousse / "workspace/MEMORY.md").read_text().startswith("# Memory")
    finally:
        srv.close()


def test_restore_never_writes_through_a_link_out_of_its_folder(h, capsys):
    setup_install(h)
    ws = h.mousse / "workspace"
    (ws / "notes").mkdir()
    (ws / "notes" / "a.md").write_text("mine\n", encoding="utf8")
    good = B.create(None)["path"]
    outside = h.root / "outside"
    outside.mkdir()
    (ws / "notes" / "a.md").unlink()
    (ws / "notes").rmdir()
    (ws / "notes").symlink_to(outside)  # 后来被换成了指到外面的软链
    assert B.restore(good) == 0
    assert not (outside / "a.md").exists() and "leads outside" in capsys.readouterr().out


def test_backup_and_restore_from_the_command_line(h):
    setup_install(h)
    h.db.close()
    r = run_mousse(h, "backup", "create", "--json")
    assert r.returncode == 0, r.stderr
    path = json.loads(r.stdout)["path"]
    r = run_mousse(h, "backup", "verify", path)
    assert r.returncode == 0 and "sound" in r.stdout
    r = run_mousse(h, "restore", path, "--dry-run")
    assert r.returncode == 0 and "nothing changed" in r.stdout
    r = run_mousse(h, "restore", str(h.root / "missing.tar.gz"))
    assert r.returncode == 2
