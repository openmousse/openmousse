"""运维命令（第 7 批，server/ops/）的测试共用：临时的家目录、PATH 上的假命令（systemctl、launchctl、journalctl、claude、codex……）、
假的 /api/health。测试文件里自己写一个小 fixture：`def h(tmp_path, monkeypatch): return make_home(tmp_path, monkeypatch)`。

不碰这台机器上真的东西：HOME 换成临时的，PATH 只剩假命令的目录和 /usr/bin:/bin（开发机上真的 claude 在别处），服务管理器用
MOUSSE_SERVICE_MANAGER 指定（默认 none：一条 systemctl 都不跑）。"""
from __future__ import annotations

import argparse
import json
import os
import stat
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


import config

SERVER = Path(__file__).resolve().parent.parent
REPO = SERVER.parent


class Home:
    def __init__(self, root: Path, monkeypatch) -> None:
        self.root, self.mp = root, monkeypatch
        self.home = root / "home"
        self.mousse = self.home / ".openmousse"
        self.bin = root / "bin"
        self.config = self.mousse / "server.json"
        self.mousse.mkdir(parents=True)
        self.bin.mkdir()

    def write(self, data: dict | str, mode: int = 0o600) -> Path:
        """写 server.json（默认 0600），清掉 config 的缓存。"""
        self.config.write_text(data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, indent=2), encoding="utf8")
        os.chmod(self.config, mode)
        config._cache = None
        return self.config

    def read(self) -> dict:
        return json.loads(self.config.read_text(encoding="utf8"))

    def fake(self, name: str, body: str) -> Path:
        """PATH 上的一个假命令（sh 脚本）。"""
        p = self.bin / name
        p.write_text("#!/bin/sh\n" + body.lstrip("\n"), encoding="utf8")
        p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        return p

    def manager(self, name: str) -> None:
        self.mp.setenv("MOUSSE_SERVICE_MANAGER", name)

    def env(self, **extra: str) -> dict[str, str]:
        """给子进程的环境（跑 mousse.py 用）。"""
        base = {k: v for k, v in os.environ.items() if not k.startswith("MOUSSE_")}
        return {**base, "HOME": str(self.home), "MOUSSE_TREE_HOME": str(self.home / ".mousse-tree"), "MOUSSE_SERVICE_MANAGER":
                os.environ.get("MOUSSE_SERVICE_MANAGER", "none"), "PATH": os.environ["PATH"], "PYTHONPATH": str(SERVER), **extra}

    def systemd(self) -> Path:
        """假的 systemctl：每个单元的状态记在 <root>/units/<单元>（没有 = inactive），start / restart 把它改成 active，stop 改成
        inactive；每次调用记进 <root>/systemctl.log。再给一个说 linger 开着的 loginctl。"""
        units, log = self.root / "units", self.root / "systemctl.log"
        units.mkdir(exist_ok=True)
        self.fake("systemctl", f"""
echo "$@" >> "{log}"
unit=""
for a in "$@"; do unit="$a"; done
case "$*" in
  *is-active*) s=$(cat "{units}/$unit" 2>/dev/null || echo inactive); echo "$s"; [ "$s" = active ] ;;
  *restart*|*" start "*|*"start "*) echo active > "{units}/$unit" ;;
  *stop*) echo inactive > "{units}/$unit" ;;
  *) exit 0 ;;
esac
""")
        self.fake("loginctl", "echo yes\n")
        self.manager("systemd")
        unit_dir = self.home / ".config/systemd/user"  # 安装器写的单元文件（内容不要紧）
        unit_dir.mkdir(parents=True, exist_ok=True)
        for u in ("openmousse-server.service", "openmousse-daily-close.service", "openmousse-daily-close.timer"):
            (unit_dir / u).write_text("[Unit]\n", encoding="utf8")
        return log

    def unit(self, name: str, state: str) -> None:
        (self.root / "units").mkdir(exist_ok=True)
        (self.root / "units" / name).write_text(state + "\n", encoding="utf8")


def make_home(tmp_path, monkeypatch) -> Home:
    """临时的家目录（见文件开头）。"""
    from ops import common
    h = Home(tmp_path, monkeypatch)
    monkeypatch.setenv("HOME", str(h.home))
    monkeypatch.setenv("MOUSSE_TREE_HOME", str(h.home / ".mousse-tree"))
    monkeypatch.setenv("MOUSSE_SERVICE_MANAGER", "none")
    monkeypatch.setenv("PATH", f"{h.bin}:/usr/bin:/bin")
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(config, "CONFIG_PATH", h.config)
    monkeypatch.setattr(config, "_cache", None)
    monkeypatch.setattr(common, "_known", None)
    h.fake("tailscale", "echo '{}'\n")  # 什么都没开的 Tailscale：不读这台机器上真的那个（要别的样子的测试自己再 fake 一次）
    return h


class FakeServer:
    """假的 OpenMousse 服务器：/api/health（要令牌）、/api/chat/busy。health = 回什么（dict，或者每次现算的函数；None / 函数回 None = 500）。"""

    _default = object()

    def __init__(self, token: str = "tok-local", health=_default) -> None:
        self.token = token
        self.health_body = {"ok": True, "server": {"version": "test", "commit": None}} if health is FakeServer._default else health
        self.busy = False
        self.hits: list[str] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a) -> None:
                pass

            def do_GET(self) -> None:  # noqa: N802
                outer.hits.append(self.path)
                if self.headers.get("Authorization") != f"Bearer {outer.token}":
                    return self._send(401, {"ok": False})
                if self.path == "/api/health":
                    body = outer.health_body() if callable(outer.health_body) else outer.health_body
                    return self._send(200 if body is not None else 500, body or {})
                if self.path == "/api/chat/busy":
                    return self._send(200, {"idle": not outer.busy})
                return self._send(404, {})

            def _send(self, code: int, body: dict) -> None:
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


def ns(**kw) -> argparse.Namespace:
    return argparse.Namespace(**kw)


def run_mousse(h: Home, *args: str, **env: str):
    """在子进程里跑 server/mousse.py（真的命令行那条路）。"""
    import subprocess
    return subprocess.run([sys.executable, str(SERVER / "mousse.py"), *args], capture_output=True, text=True, env=h.env(**env), timeout=120,
                          cwd=str(SERVER))
