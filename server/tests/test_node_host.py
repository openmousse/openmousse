"""第 8 批：节点主机（node_host.py）。对着真跑的测试服务器（uvicorn，在线程里，端口随便挑）起一个真的 node_host.py 子进程：配对、报命令面、
等活、干活；PATH 上放假的 grim / notify-send（Linux 那条路）、screencapture / osascript / sips / launchctl / systemctl（macOS、装服务那条路，
在进程里换掉 system() 测）；文件夹都是临时目录。也查它能在 macOS 自带的 python3（3.9）上跑、不导入服务器的模块。不碰真的设备和系统服务。"""
from __future__ import annotations

import ast
import asyncio
import base64
import io
import json
import os
import socket
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

import nodes
import pairing

SERVER = Path(__file__).resolve().parent.parent
HOST = SERVER / "node_host.py"
OWNER = {"Authorization": "Bearer tok-phone"}


def png(w: int = 5, h: int = 3) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (10, 120, 200)).save(buf, "PNG")
    return buf.getvalue()


def script(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)
    return path


def wait_for(cond, timeout: float = 10.0, what: str = "condition"):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        v = cond()
        if v:
            return v
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {what}")


# —— macOS 自带的 python3（3.9）、只用标准库 ———————————————————————————————————————————————————


def test_parses_as_python_3_9_and_imports_only_the_standard_library():
    src = HOST.read_text(encoding="utf-8")
    tree = ast.parse(src, feature_version=(3, 9))
    mods = {(n.module or "").split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    mods |= {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    server_modules = {p.stem for p in SERVER.glob("*.py")} | {"engine"}
    assert not (mods - {"__future__"}) & server_modules, mods & server_modules
    assert mods - {"__future__"} <= set(sys.stdlib_module_names), mods - set(sys.stdlib_module_names)
    assert "from __future__ import annotations" in src


# —— 真跑的测试服务器 ————————————————————————————————————————————————————————————————


@pytest.fixture
def live(server_json, monkeypatch, tmp_path):
    """main.app 在一个线程里由 uvicorn 跑（不跑 lifespan）；engine.nodes 开着；不推送。call(协程) 在服务器的事件循环里跑并等结果。"""
    import uvicorn

    import main
    import push

    async def no_push(*a, **k):
        return {"ok": True, "sent": 0}
    monkeypatch.setattr(push, "send_push", no_push)
    monkeypatch.setattr(pairing, "_fails", {})
    monkeypatch.setattr(nodes, "LINKS", {})
    monkeypatch.setattr(nodes, "INVOKES", {})
    server_json({"claw": {"kind": "builtin"}, "timezone": "UTC", "language": "en", "auth": {"tokens": {"phone": "tok-phone"}},
                 "engine": {"model": "deepseek/deepseek-flash", "nodes": {}}})
    with nodes._lock(), nodes.ndb() as conn:
        conn.execute("DELETE FROM nodes")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    loop = asyncio.new_event_loop()
    srv = uvicorn.Server(uvicorn.Config(main.app, host="127.0.0.1", port=port, lifespan="off", log_level="warning"))

    def serve():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(srv.serve())
    t = threading.Thread(target=serve, daemon=True)
    t.start()
    wait_for(lambda: srv.started, what="the test server")

    class Live:
        url = f"http://127.0.0.1:{port}"

        @staticmethod
        def call(coro, timeout: float = 30):
            return asyncio.run_coroutine_threadsafe(coro, loop).result(timeout)
    yield Live
    srv.should_exit = True
    t.join(10)


@pytest.fixture
def box(tmp_path):
    """节点那台「电脑」：自己的家目录、节点目录、假程序（PATH 最前面）、给出来的文件夹。"""
    class Box:
        pass
    b = Box()
    b.home, b.node, b.bin = tmp_path / "home", tmp_path / "node-home", tmp_path / "bin"
    b.docs, b.ro = tmp_path / "docs", tmp_path / "readonly"
    for d in (b.home, b.bin, b.docs, b.ro):
        d.mkdir()
    (b.bin / "shot.png").write_bytes(png())
    b.notified = tmp_path / "notified.txt"
    script(b.bin / "grim", f'cp "{b.bin / "shot.png"}" "$1"\n')
    script(b.bin / "notify-send", f'for a in "$@"; do printf "%s\\n" "$a" >> "{b.notified}"; done\n')
    script(b.bin / "hello.sh", 'echo "hello $1 from $(pwd)"\n')
    script(b.bin / "env.sh", "env\n")
    script(b.bin / "spawn.sh", 'sleep 60 &\necho $! > "$1"\nwait\n')
    b.env = {**os.environ, "OPENMOUSSE_NODE_HOME": str(b.node), "HOME": str(b.home), "PATH": f"{b.bin}:{os.environ.get('PATH', '')}",
             "FAKE_SERVICE_TOKEN": "fake-token-node-123", "PLAIN_SETTING": "visible"}
    return b


def pair(live, box, name: str = "Test Laptop") -> str:
    code, _ = pairing.new_code(name, 10, node=True)
    link = pairing.link(live.url, code, node=True)
    r = subprocess.run([sys.executable, str(HOST), "pair", link], env=box.env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr + r.stdout
    return json.loads((box.node / "node.json").read_text())["id"]


def test_pair_keeps_the_token_private_and_writes_a_default_config(live, box):
    nid = pair(live, box)
    assert nid == "node-test-laptop"
    nf = box.node / "node.json"
    assert stat.S_IMODE(nf.stat().st_mode) == 0o600 and stat.S_IMODE(box.node.stat().st_mode) == 0o700
    data = json.loads(nf.read_text())
    import config
    assert config.raw(fresh=True)["auth"]["tokens"][nid] == data["token"] and data["server"] == live.url
    cfg = json.loads((box.node / "config.json").read_text())
    assert cfg["allow"] == [] and cfg["folders"] == {} and cfg["screen"] is False  # 先什么都不给
    phone_code, _ = pairing.new_code("iPhone", 10)  # 拿手机的码：说清楚，不换
    r = subprocess.run([sys.executable, str(HOST), "pair", pairing.link(live.url, phone_code)], env=box.env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 1 and "for a phone" in r.stderr


@pytest.fixture
def host(live, box):
    """配好、写好本机的配置、起一个 node_host.py run；主人批了它报的、开了几项。→ (节点 id, 进程)。"""
    nid = pair(live, box)
    (box.docs / "a.txt").write_text("first line\nsecond line\n")
    (box.docs / ".env").write_text("FAKE_KEY=nope\n")
    (box.docs / "out").mkdir()
    (box.docs / "link.txt").symlink_to(box.ro / "target.txt")
    (box.ro / "target.txt").write_text("outside\n")
    (box.docs / "linkdir").symlink_to(box.ro)
    cfg = {"allow": [f"{box.bin}/hello.sh ...", f"{box.bin}/env.sh", f"{box.bin}/spawn.sh ...", "sleep ..."],
           "folders": {"Docs": {"path": str(box.docs), "write": True}, "Ro": str(box.ro)}, "notify": True, "screen": True}
    (box.node / "config.json").write_text(json.dumps(cfg))
    logf = open(box.node / "run.log", "w")
    proc = subprocess.Popen([sys.executable, str(HOST), "run"], env=box.env, stdout=logf, stderr=subprocess.STDOUT)
    try:
        n = wait_for(lambda: (x := nodes.get(nid)) is not None and "screen.snapshot" in x.commands and x, what="the node's hello")
        assert set(n.commands) == {"system.which", "system.run", "fs.list", "fs.read", "fs.write", "screen.snapshot", "system.notify"}
        import httpx
        with httpx.Client(base_url=live.url, headers=OWNER, timeout=10) as c:
            assert c.post(f"/api/nodes/{nid}/approve", json={}).status_code == 200
            for cmd in ("system.run", "fs.write", "screen.snapshot"):
                assert c.post(f"/api/nodes/{nid}/allow", json={"command": cmd, "level": "allow"}).status_code == 200
        wait_for(lambda: nodes.connected(nid), what="the node to wait for work")
        yield nid, proc
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                proc.kill()
        logf.close()


def invoke(live, nid: str, command: str, params: dict, timeout: float = 20):
    return live.call(nodes.invoke(nid, command, params, timeout), timeout + 10)


def failure(live, nid: str, command: str, params: dict, timeout: float = 20) -> nodes.NodeError:
    with pytest.raises(nodes.NodeError) as e:
        invoke(live, nid, command, params, timeout)
    return e.value


def test_the_node_host_does_its_work(live, box, host):
    nid, proc = host
    # 通知：标题、正文原样当参数（Linux 的 notify-send）
    assert invoke(live, nid, "system.notify", {"title": 'He said "hi"', "body": "Water the fictional basil"}) == {"shown": True}
    assert box.notified.read_text().splitlines()[-2:] == ['He said "hi"', "Water the fictional basil"]
    # 截屏（假的 grim）
    shot = invoke(live, nid, "screen.snapshot", {"maxWidth": 1600})
    assert shot["format"] == "png" and (shot["width"], shot["height"]) == (5, 3) and base64.b64decode(shot["base64"]) == png()
    # 跑命令：不经 shell、在家目录、名单里的才跑；环境里没有像密钥的
    out = invoke(live, nid, "system.run", {"argv": [str(box.bin / "hello.sh"), "Sam"], "timeoutMs": 10_000})
    assert out["exitCode"] == 0 and out["output"].strip() == f"hello Sam from {box.home}" and not out["timedOut"]
    env = invoke(live, nid, "system.run", {"argv": [str(box.bin / "env.sh")]})["output"]
    assert "fake-token-node-123" not in env and "PLAIN_SETTING=visible" in env and "OPENMOUSSE_NODE_HOME" not in env
    in_docs = invoke(live, nid, "system.run", {"argv": [str(box.bin / "hello.sh"), "Kim"], "cwd": "Docs/out"})
    assert in_docs["output"].strip() == f"hello Kim from {box.docs / 'out'}"
    # 第二道门：服务器批了、本机名单里没有 → 不跑
    denied = failure(live, nid, "system.run", {"argv": ["ls", "/"]})
    assert denied.code == "SYSTEM_RUN_DENIED" and "own allowlist" in str(denied)
    assert failure(live, nid, "system.run", {"argv": [str(box.bin / "hello.sh")], "cwd": "Docs/linkdir"}).code == "PATH_DENIED"
    # 文件夹：列、读、写；..、符号链接、像密钥的、只读的都挡
    roots = invoke(live, nid, "fs.list", {})
    assert [(e["name"], e.get("write", False)) for e in roots["entries"]] == [("Docs", True), ("Ro", False)]
    listed = {e["name"]: e["kind"] for e in invoke(live, nid, "fs.list", {"path": "Docs"})["entries"]}
    assert listed["a.txt"] == "f" and listed["out"] == "d" and listed["link.txt"] == "l" and listed[".env"] == "x"
    read = invoke(live, nid, "fs.read", {"path": "Docs/a.txt"})
    assert read["text"] == "1\tfirst line\n2\tsecond line" and read["size"] == 23
    for path, code in (("Docs/../readonly/target.txt", "PATH_DENIED"), ("Docs/link.txt", "PATH_DENIED"), ("Docs/.env", "PATH_DENIED"),
                       ("Docs/linkdir/target.txt", "PATH_DENIED"), ("Elsewhere/x", "PATH_DENIED"), ("Docs/missing.txt", "NOT_FOUND")):
        assert failure(live, nid, "fs.read", {"path": path}).code == code, path
    wrote = invoke(live, nid, "fs.write", {"path": "Docs/new/plan.md", "content": "# Plan\n"})
    assert wrote == {"path": "Docs/new/plan.md", "bytes": 7, "created": True} and (box.docs / "new" / "plan.md").read_text() == "# Plan\n"
    assert invoke(live, nid, "fs.write", {"path": "Docs/new/plan.md", "content": "v2"})["created"] is False
    for path in ("Ro/x.txt", "Docs/link.txt", "Docs/linkdir/x.txt", "Docs/.git/config", "Docs/secrets/x"):
        assert failure(live, nid, "fs.write", {"path": path, "content": "x"}).code == "PATH_DENIED", path
    assert (box.ro / "target.txt").read_text() == "outside\n" and not (box.ro / "x.txt").exists()
    which = invoke(live, nid, "system.which", {"bins": ["hello.sh", "definitely-not-here-xyz"]})
    assert which["bins"] == {"hello.sh": str(box.bin / "hello.sh"), "definitely-not-here-xyz": None}


def _gone(pid: int) -> bool:
    try:
        st = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return True
    return st.split()[2] == "Z"


def test_timeouts_and_cancels_kill_the_whole_process_group(live, box, host):
    nid, proc = host
    pidf = box.home / "child.pid"
    t0 = time.monotonic()
    out = invoke(live, nid, "system.run", {"argv": [str(box.bin / "spawn.sh"), str(pidf)], "timeoutMs": 1000})
    assert out["timedOut"] and time.monotonic() - t0 < 10
    child = int(pidf.read_text())
    wait_for(lambda: _gone(child), what="the timed-out command's background child to be killed")
    pidf.unlink()

    async def stopped():
        task = asyncio.ensure_future(nodes.invoke(nid, "system.run", {"argv": [str(box.bin / "spawn.sh"), str(pidf)]}, 60))
        for _ in range(200):
            await asyncio.sleep(0.05)
            if pidf.exists() and pidf.read_text().strip():
                break
        task.cancel()  # 这一轮被停了
        try:
            await task
        except asyncio.CancelledError:
            pass
    live.call(stopped())
    child = int(pidf.read_text())
    wait_for(lambda: _gone(child), what="the cancelled command's process group to be killed")


def test_config_changes_are_reported_and_stopping_says_bye(live, box, host):
    nid, proc = host
    cfg = json.loads((box.node / "config.json").read_text())
    cfg["screen"] = False
    (box.node / "config.json").write_text(json.dumps(cfg))
    os.utime(box.node / "config.json", (time.time() + 5, time.time() + 5))
    wait_for(lambda: "screen.snapshot" not in nodes.get(nid).commands, what="the node to report without screenshots")
    assert failure(live, nid, "screen.snapshot", {}).code == "NODE_COMMAND_NOT_DECLARED"
    proc.terminate()  # 服务停了：先说 bye，退出码 0
    assert proc.wait(10) == 0
    wait_for(lambda: not nodes.connected(nid), what="the node to be offline")
    t0 = time.monotonic()
    assert failure(live, nid, "system.notify", {"title": "x"}).code == "NODE_OFFLINE" and time.monotonic() - t0 < 1


def test_a_removed_node_stops_cleanly(live, box, host):
    nid, proc = host
    import httpx
    with httpx.Client(base_url=live.url, headers=OWNER, timeout=10) as c:
        assert c.delete(f"/api/nodes/{nid}").status_code == 200
    assert proc.wait(60) == 0  # 令牌收回了：停下、退出码 0（服务不会一直重启）
    assert "doesn't accept this node's token" in (box.node / "run.log").read_text()


# —— macOS 那条路、装服务（在进程里换掉 system()）———————————————————————————————————————————————————


@pytest.fixture
def mac(tmp_path, monkeypatch):
    import node_host
    b = tmp_path / "bin"
    log = tmp_path / "calls.txt"
    for name in ("osascript", "launchctl", "systemctl", "sips"):
        script(b / name, f'printf "%s" "{name}" >> "{log}"; for a in "$@"; do printf " [%s]" "$a" >> "{log}"; done; echo >> "{log}"\n')
    (b / "shot.png").write_bytes(png(7, 4))
    script(b / "screencapture", f'for a in "$@"; do last="$a"; done; cp "{b / "shot.png"}" "$last"; echo "screencapture $*" >> "{log}"\n')
    monkeypatch.setenv("PATH", f"{b}:{os.environ.get('PATH', '')}")
    monkeypatch.setenv("OPENMOUSSE_NODE_HOME", str(tmp_path / "node-home"))
    monkeypatch.setattr(node_host, "system", lambda: "macos")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    (tmp_path / "home").mkdir()
    return node_host, log


def test_macos_screenshot_and_notification(mac):
    node_host, log = mac
    cfg = node_host.Config({"screen": True, "notify": True})
    assert node_host.surface(cfg) == ["system.which", "screen.snapshot", "system.notify"]
    shot = node_host.handle(cfg, {"id": "i1", "command": "screen.snapshot", "params": {"screen": 1}})
    assert shot["format"] == "png" and (shot["width"], shot["height"]) == (7, 4)
    assert "screencapture -x -t jpg -D 2 " in log.read_text()
    title = 'Done" & do shell script "touch /tmp/pwned'  # 标题里的引号不会变成 AppleScript
    node_host.handle(cfg, {"id": "i2", "command": "system.notify", "params": {"title": title, "body": "All set"}})
    line = next(x for x in log.read_text().splitlines() if x.startswith("osascript"))
    assert line == ("osascript [-e] [on run argv] [-e] [display notification (item 2 of argv) with title (item 1 of argv)] [-e] [end run] "
                    f"[{title}] [All set]")
    with pytest.raises(node_host.Failure) as off:
        node_host.handle(node_host.Config({}), {"id": "i3", "command": "screen.snapshot", "params": {}})
    assert off.value.code == "NOT_ENABLED"


def test_install_and_uninstall_launchd_and_systemd(mac, monkeypatch, tmp_path):
    node_host, log = mac
    assert node_host.install() == 2  # 还没配对
    node_host.write_json(node_host.home() / "node.json", {"server": "http://127.0.0.1:9", "token": "t", "id": "node-x"})
    assert node_host.install() == 0
    plist = tmp_path / "home" / "Library" / "LaunchAgents" / "ai.openmousse.node.plist"
    text = plist.read_text()
    assert "<string>ai.openmousse.node</string>" in text and f"<string>{HOST}</string>" in text and "<string>run</string>" in text
    assert "<key>SuccessfulExit</key>" in text and "OPENMOUSSE_NODE_HOME" in text
    assert f"launchctl [bootstrap] [gui/{os.getuid()}] [{plist}]" in log.read_text()
    assert node_host.uninstall() == 0 and not plist.exists()
    assert f"launchctl [bootout] [gui/{os.getuid()}/ai.openmousse.node]" in log.read_text()
    monkeypatch.setattr(node_host, "system", lambda: "linux")
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert node_host.install() == 0
    unit = tmp_path / "home" / ".config" / "systemd" / "user" / "openmousse-node.service"
    body = unit.read_text()
    assert f"ExecStart={sys.executable} {HOST} run" in body and "Restart=on-failure" in body and "WantedBy=default.target" in body
    assert "systemctl [--user] [enable] [--now] [openmousse-node.service]" in log.read_text()
    assert node_host.uninstall() == 0 and not unit.exists()
    assert "systemctl [--user] [disable] [--now] [openmousse-node.service]" in log.read_text()


def test_mousse_node_hands_over_to_the_node_host(capsys):
    import mousse
    assert mousse.main(["node", "--help"]) == 0
    assert "pair '<pairing link>'" in capsys.readouterr().out


# —— 操作电脑（computer.act，macOS）：假的 osascript（认 JXA 的那几个 op）、pbcopy、pbpaste ——————————————————————————————


@pytest.fixture
def pc(mac, tmp_path, monkeypatch):
    """在 mac 上再换掉 osascript（JXA：points 回屏幕大小，别的回 ok；UNTRUSTED 文件在就回 untrusted）、pbcopy / pbpaste（剪贴板是个文件）。
    截屏是 1600×1000 的图，屏幕 1512×982 点。"""
    node_host, log = mac
    b = tmp_path / "bin"
    clip, untrusted = tmp_path / "clipboard.txt", tmp_path / "UNTRUSTED"
    clip.write_text("old clipboard text")
    (b / "osascript").write_text(f"""#!{sys.executable}
import sys
args = sys.argv[1:]
if args[:2] == ["-l", "JavaScript"]:
    op = args[4:]
    with open({str(log)!r}, "a") as f:
        f.write("jxa " + " ".join(op) + ("  [script ok]" if args[2] == "-e" and "CGEventPost" in args[3] else "  [no script]") + "\\n")
    if op[0] == "points":
        print("1512 982")
    elif __import__("os").path.exists({str(untrusted)!r}):
        print("untrusted")
    else:
        print("ok")
""")
    (b / "osascript").chmod(0o755)
    script(b / "pbcopy", f'cat > "{clip}"; printf "pbcopy %s\\n" "$(cat "{clip}")" >> "{log}"\n')
    script(b / "pbpaste", f'cat "{clip}"\n')
    (b / "shot.png").write_bytes(png(1600, 1000))
    monkeypatch.setattr(node_host, "_shot", None)
    return node_host, log, clip, untrusted


def jxa_calls(log: Path) -> list[str]:
    return [x for x in log.read_text().splitlines() if x.startswith("jxa ")] if log.exists() else []


def test_computer_act_needs_screen_too_and_a_fresh_screenshot(pc):
    node_host, log, clip, untrusted = pc
    assert "computer.act" not in node_host.surface(node_host.Config({"computer": True}))  # 截屏没开：看不见就不让动手
    cfg = node_host.Config({"computer": True, "screen": True})
    assert node_host.surface(cfg)[-1] == "computer.act"
    with pytest.raises(node_host.Failure) as no_shot:
        node_host.handle(cfg, {"id": "c1", "command": "computer.act", "params": {"act": "click", "x": 800, "y": 500}})
    assert no_shot.value.code == "COMPUTER_NEEDS_SCREENSHOT" and not any("click" in x for x in jxa_calls(log))
    shot = node_host.handle(cfg, {"id": "s1", "command": "screen.snapshot", "params": {}})
    assert (shot["width"], shot["height"]) == (1600, 1000)
    out = node_host.handle(cfg, {"id": "c2", "command": "computer.act", "params": {"act": "click", "x": 800, "y": 500}})
    assert out == {"act": "click", "at": [756, 491]}  # 1600×1000 的图 → 1512×982 点
    assert jxa_calls(log)[-2:] == ["jxa points  [script ok]", "jxa click 756 491  [script ok]"]  # 坐标当参数传，脚本是固定的
    node_host.handle(cfg, {"id": "c3", "command": "computer.act", "params": {"act": "double_click", "x": 0, "y": 999}})
    node_host.handle(cfg, {"id": "c4", "command": "computer.act", "params": {"act": "scroll", "x": 1599, "y": 0, "direction": "down", "amount": 5}})
    assert jxa_calls(log)[-2:] == ["jxa double_click 0 981  [script ok]", "jxa scroll 1511 0 -5 0  [script ok]"]
    with pytest.raises(node_host.Failure) as outside:
        node_host.computer_act({"act": "click", "x": 1600, "y": 10})
    assert outside.value.code == "INVALID_REQUEST" and "1600×1000" in str(outside.value)
    node_host._shot["at"] -= node_host.SHOT_TTL + 1  # 截屏太旧了：屏幕可能已经变了
    with pytest.raises(node_host.Failure) as stale:
        node_host.computer_act({"act": "move", "x": 10, "y": 10})
    assert stale.value.code == "COMPUTER_NEEDS_SCREENSHOT"
    other = node_host.handle(cfg, {"id": "s2", "command": "screen.snapshot", "params": {"screen": 1}})  # 别的屏幕：不当成坐标的底
    assert other["width"] == 1600 and node_host._shot["at"] < time.monotonic() - node_host.SHOT_TTL


def test_computer_keys_typing_and_accessibility(pc):
    node_host, log, clip, untrusted = pc
    assert node_host.computer_act({"act": "key", "keys": "cmd+shift+s"}) == {"act": "key", "keys": "cmd+shift+s"}
    assert node_host.computer_act({"act": "key", "keys": "enter"})["keys"] == "enter"
    assert jxa_calls(log)[-2:] == [f"jxa key 1 {(1 << 20) | (1 << 17)}  [script ok]", "jxa key 36 0  [script ok]"]
    for bad in ("cmd+", "hyper+s", "cmd+foo", ""):
        with pytest.raises(node_host.Failure) as e:
            node_host.computer_act({"act": "key", "keys": bad})
        assert e.value.code == "INVALID_REQUEST"
    assert node_host.computer_act({"act": "type", "text": "你好，Wren Bakery"}) == {"act": "type", "chars": 14}
    lines = log.read_text().splitlines()
    assert lines[-3:] == ["pbcopy 你好，Wren Bakery", f"jxa key 9 {1 << 20}  [script ok]", "pbcopy old clipboard text"]  # 贴完放回原来的字
    assert clip.read_text() == "old clipboard text"
    untrusted.write_text("")
    with pytest.raises(node_host.Failure) as no_ax:
        node_host.computer_act({"act": "key", "keys": "esc"})
    assert no_ax.value.code == "PERMISSION_MISSING" and "Accessibility" in str(no_ax.value)
    with pytest.raises(node_host.Failure) as bad_act:
        node_host.computer_act({"act": "drag"})
    assert bad_act.value.code == "INVALID_REQUEST"


# —— 审查补的：.git 不分大小写、排着等位子时被叫停的不起 ——————————————————————————————————————————————————


def test_dot_git_is_off_limits_in_any_case(tmp_path):
    """macOS 默认不分大小写：.GIT/hooks、.Git/config 就是 .git 里的（审查：原来只认小写，钩子写得进去、config 读得到）。"""
    import node_host
    cfg = node_host.Config({"folders": {"Code": {"path": str(tmp_path), "write": True}}})
    for path in ("Code/.GIT/hooks/pre-commit", "Code/.Git/config", "Code/repo/.git/HEAD"):
        with pytest.raises(node_host.Failure) as e:
            node_host.place(cfg, path, write=True)
        assert e.value.code == "PATH_DENIED", path
    for path in ("Code/.Git/config", "Code/repo/.GiT/CONFIG"):
        with pytest.raises(node_host.Failure) as e:
            node_host.place(cfg, path)
        assert e.value.code == "PATH_DENIED", path
    assert node_host.place(cfg, "Code/repo/src/main.py", write=True)[1] == ["repo", "src", "main.py"]


def test_fs_write_leaves_allowlisted_scripts_and_their_folders_alone(tmp_path):
    """名单里会跑到的脚本、它所在的子文件夹：fs.write 不改、不新建（审查：先改写名单里的脚本、再 system.run 就是任意代码）。
    共享文件夹的根不整个护住；macOS 不分大小写，换了大小写也挡。"""
    import node_host
    code = tmp_path / "code"
    check = script(code / "tools" / "check.sh", "echo original\n")
    top = script(code / "run.sh", "echo top\n")
    cfg = node_host.Config({"allow": [str(check), f"/bin/sh {top}"], "folders": {"Code": {"path": str(code), "write": True}}})
    for path in ("Code/tools/check.sh", "Code/tools/helper.sh", "Code/Tools/Check.sh", "Code/tools/new/x.sh", "Code/run.sh"):
        with pytest.raises(node_host.Failure) as e:
            node_host.fs_write(cfg, {"path": path, "content": "echo replaced-by-agent\n"})
        assert e.value.code == "PATH_DENIED", path
    assert node_host.system_run(cfg, {"argv": [str(check)]}, "iid-check")["output"].strip() == "original"
    assert node_host.fs_write(cfg, {"path": "Code/notes.txt", "content": "hi"})["created"] and (code / "notes.txt").read_text() == "hi"


def test_a_command_cancelled_while_waiting_for_a_slot_never_starts(tmp_path):
    """同时最多跑 4 个：排着等位子时服务器说不用了（到点、这一轮被停），位子空出来也不起（审查：原来照样跑完再报 CANCELLED）。"""
    import node_host
    marker = tmp_path / "ran"
    prog = script(tmp_path / "bin" / "touch.sh", f'touch "{marker}"\n')
    cfg = node_host.Config({"allow": [str(prog)]})
    got: list = []

    def go():
        try:
            got.append(node_host.system_run(cfg, {"argv": [str(prog)], "timeoutMs": 10_000}, "iid-waiting"))
        except node_host.Failure as e:
            got.append(e)
    for _ in range(4):
        node_host.SLOTS.acquire()
    try:
        t = threading.Thread(target=go, daemon=True)
        t.start()
        time.sleep(0.3)  # 在等位子
        node_host.cancel("iid-waiting")
    finally:
        for _ in range(4):
            node_host.SLOTS.release()
    t.join(10)
    node_host.CANCELLED.discard("iid-waiting")
    assert len(got) == 1 and isinstance(got[0], node_host.Failure) and got[0].code == "CANCELLED"
    time.sleep(0.3)
    assert not marker.exists()
    assert node_host.system_run(cfg, {"argv": [str(prog)]}, "iid-later")["exitCode"] == 0 and marker.exists()  # 不叫停的照常跑
