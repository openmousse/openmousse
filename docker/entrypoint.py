#!/usr/bin/env python3
"""OpenMousse 的 Docker 入口（第 9 批 15.2，2026-10-19）：docker/Dockerfile 的 ENTRYPOINT。说明见 docs/docker.md。

数据都在卷 /data（MOUSSE_DATA 能换），容器删了重建也还在：
  /data/server.json     配置（镜像里 MOUSSE_SERVER_CONFIG 指这里）
  /data/secrets.env     app 里存的密钥（server.json 旁边，0600，见 server/secretstore.py）
  /data/data/           data_dir：数据库、上传的文件、备份
  /data/workspace/      主对话的工作区（档案 USER.md、MEMORY.md、memory/、skills/）；新建的 Agent 在它旁边的 workspace-<id>/
  /data/tree/           世界树（MOUSSE_TREE_HOME）
  /data/home/           HOME：别的东西要往家目录写的，也留在卷里
第一次（/data 里没有 server.json）：写一份（claw builtin、容器里听 0.0.0.0:8080、令牌随机、名字 / 语言 / 时区从环境变量读），建好世界树，
  打印配对链接（server/tokens.py pair，地址照 MOUSSE_PUBLIC_URL）。再起来：server.json 一个字不动（改设置在 app 里改，或者改卷里的文件）。
然后换成 server/run.py（exec：信号直接给服务器）。
环境变量：MOUSSE_NAME（助手的名字，默认 Mousse）、MOUSSE_LANG（zh / en；不写看 LANG）、MOUSSE_TZ（IANA 时区；不写看 TZ，再不写 UTC）、
  MOUSSE_PUBLIC_URL（手机连服务器用的地址，比如宿主上 Tailscale Serve 的 https://<机器>.<tailnet>.ts.net；不写就只打印怎么配）。
  这几样只在第一次用；MOUSSE_INSTALL=docker 是镜像里写死的（mousse update 看到它就让你拉新镜像）。
python3 entrypoint.py health：给 HEALTHCHECK 用，带 server.json 里本机脚本那把令牌打 /api/health，答了 ok 才算活着。
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SERVER = REPO / "server"
PORT = 8080
SKILLS = ["journal", "memory-tree", "inbox", "board", "goals", "page"]   # 新 Agent 默认的技能（同 packs/core/setup.py）
NOT_API = ("mcp", "sentinel", "api")


def data_dir(env=os.environ) -> Path:
    return Path(env.get("MOUSSE_DATA") or "/data")


def config_path(env=os.environ) -> Path:
    return Path(env.get("MOUSSE_SERVER_CONFIG") or data_dir(env) / "server.json")


def lang_of(env) -> str:
    v = str(env.get("MOUSSE_LANG") or env.get("LANG") or "").strip().lower()
    return "zh" if v.startswith(("zh", "cn")) else "en"


def tz_of(env) -> str:
    tz = str(env.get("MOUSSE_TZ") or env.get("TZ") or "").strip() or "UTC"
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(tz)
    except Exception:  # noqa: BLE001 — 打错了、镜像里没有这个时区：用 UTC，打一句
        print(f"MOUSSE_TZ={tz!r} isn't a known timezone, using UTC", file=sys.stderr)
        return "UTC"
    return tz


def first_config(env, data: Path) -> dict:
    """第一次的 server.json（照 packs/core/setup.py 给自带内核写的那一份，路径都在卷里）。"""
    lang = lang_of(env)
    ws = data / "workspace"
    return {
        "app_name": str(env.get("MOUSSE_NAME") or "").strip()[:40] or "Mousse",
        "owner_id": "owner",                          # 新装：你自己改的东西在库里记成 owner（见 server/config.py 的 owner_id）
        "timezone": tz_of(env),
        "language": lang,
        "bind": {"host": "0.0.0.0", "port": PORT},   # 容器里听所有地址；宿主那边 compose 只绑 127.0.0.1
        "claw": {"kind": "builtin"},
        "openclaw_home": str(data / "openclaw"),      # 自带内核用不着 OpenClaw：只是几样默认路径的根（归档的 Agent 放这里的 archive/）
        "workspace": str(ws),
        "profile": str(ws / "USER.md"),
        "data_dir": str(data / "data"),
        "dist": str(REPO / "app" / "dist"),
        "agent_default_skills": SKILLS,
        "study": {"materials": str(data / "data" / "study" / "materials"), "pages": str(data / "data" / "study" / "pages")},
        "auth": {"tokens": {name: secrets.token_urlsafe(24) for name in ("phone", "local", "mcp")}, "tailscale_nodes": [], "trust_loopback": False},
    }


def write_private(path: Path, text: str) -> None:
    """0600 写一个文件：同一目录先写临时文件再换上去（写到一半不会留下半个 server.json）。"""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf8") as f:
        f.write(text)
    os.replace(tmp, path)


def first_run(env, data: Path) -> tuple[bool, dict]:
    """建好卷里的目录；没有 server.json 就写一份。→ (这次是不是第一次, server.json 的内容)。已经有了的一个字不动。"""
    for d in (data / "data", data / "workspace", data / "tree", data / "home"):
        d.mkdir(parents=True, exist_ok=True)
    path = config_path(env)
    if path.exists():
        try:
            cfg = json.loads(path.read_text(encoding="utf8"))
        except ValueError:
            cfg = {}
        return False, cfg if isinstance(cfg, dict) else {}
    cfg = first_config(env, data)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_private(path, json.dumps(cfg, ensure_ascii=False, indent=2) + "\n")
    user = Path(cfg["profile"])
    if not user.exists():
        user.write_text("# USER.md\n\n## 基本信息\n" if cfg["language"] == "zh" else "# USER.md\n\n## Basics\n", encoding="utf8")
    for k in ("materials", "pages"):
        Path(cfg["study"][k]).mkdir(parents=True, exist_ok=True)
    return True, cfg


def init_tree(cfg: dict, env) -> str:
    """建世界树（mousse-tree init，同安装器的 setup_tree）；已经建过（tree/config.json 在）就不动。→ 一行给人看的话。"""
    home = Path(env.get("MOUSSE_TREE_HOME") or data_dir(env) / "tree")
    if (home / "config.json").exists():
        return ""
    exe = shutil.which("mousse-tree")
    if not exe:
        return "mousse-tree isn't installed in this image: skipped the memory tree"
    r = subprocess.run([exe, "init", "--tz", cfg["timezone"], "--profile", cfg["profile"], "--lang", cfg["language"]],
                       capture_output=True, text=True, timeout=120, env={**env, "MOUSSE_TREE_HOME": str(home)}, check=False)
    out = (r.stdout or r.stderr).strip().splitlines()
    return out[0] if out else ""


def pair(cfg: dict, env) -> str:
    """配对链接：MOUSSE_PUBLIC_URL 给了就出一个（30 分钟内有效、只能用一次）；没给就说怎么配。"""
    zh = cfg.get("language") == "zh"
    url = str(env.get("MOUSSE_PUBLIC_URL") or "").strip().rstrip("/")
    again = "docker compose -f docker/compose.yaml exec openmousse python3 tokens.py pair --server <address>"
    if not url:
        return ("手机连这台：在宿主上用 Tailscale 把 127.0.0.1:8080 开给你自己的设备（tailscale serve --bg 8080），"
                f"把它的地址写进 MOUSSE_PUBLIC_URL 再起一次；或者现在就出一个配对码：\n  {again}" if zh else
                "To connect your phone: on the host, give your own devices 127.0.0.1:8080 through Tailscale (tailscale serve --bg 8080), set "
                f"MOUSSE_PUBLIC_URL to that address and start again; or make a pairing code now:\n  {again}")
    r = subprocess.run([sys.executable, str(SERVER / "tokens.py"), "pair", "--name", "phone", "--minutes", "30", "--server", url],
                       capture_output=True, text=True, timeout=60, cwd=str(SERVER), env=dict(env), check=False)
    return (r.stdout or r.stderr).rstrip()


def summary(cfg: dict) -> str:
    zh = cfg.get("language") == "zh"
    lines = [("第一次起来：写好了 /data/server.json（自带内核、令牌随机生成，只存在卷里）" if zh else
              "First start: wrote /data/server.json (built-in engine, random tokens, kept only in the volume)"),
             (f"  名字 {cfg['app_name']}，时区 {cfg['timezone']}，语言 中文" if zh else
              f"  name {cfg['app_name']}, timezone {cfg['timezone']}, language English"),
             ("  浏览器（宿主上）：http://127.0.0.1:8080（compose 里改了 MOUSSE_PORT 就是那个端口；没构建网页版的话只有一行说明，见 docs/docker.zh-CN.md）"
              if zh else "  Browser on the host: http://127.0.0.1:8080 (or the MOUSSE_PORT you set in compose; without a web build it only shows a note, "
              "see docs/docker.md)")]
    return "\n".join(lines)


def health(env=os.environ) -> int:
    """HEALTHCHECK：带本机脚本那把令牌（第一把不是 mcp / sentinel / api 的，同 server/config.py 的 pick_api_token）打 /api/health。"""
    try:
        cfg = json.loads(config_path(env).read_text(encoding="utf8"))
    except (OSError, ValueError):
        return 1
    tokens = ((cfg.get("auth") or {}).get("tokens") or {}) if isinstance(cfg, dict) else {}
    tok = next((str(v) for k, v in tokens.items() if v and k not in NOT_API and not str(k).startswith(("mcp-", "api-"))), "")
    port = int((cfg.get("bind") or {}).get("port") or PORT)
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/health", headers={"Authorization": f"Bearer {tok}"} if tok else {})
    try:
        with urllib.request.urlopen(req, timeout=4) as r:  # noqa: S310 — 容器里自己的服务
            return 0 if json.loads(r.read().decode("utf8")).get("ok") else 1
    except (urllib.error.URLError, OSError, ValueError):
        return 1


def main(argv: list[str]) -> int:
    if argv[1:2] == ["health"]:
        return health()
    env = dict(os.environ)
    data = data_dir(env)
    fresh, cfg = first_run(env, data)
    if fresh:
        print(summary(cfg), flush=True)
        if line := init_tree(cfg, env):
            print(("  世界树：" if cfg.get("language") == "zh" else "  Memory tree: ") + line, flush=True)
        print(pair(cfg, env), flush=True)
    else:
        print("/data/server.json 已经有了：照它起，不改" if cfg.get("language") == "zh" else
              "/data/server.json is already there: starting with it, unchanged", flush=True)
    os.chdir(SERVER)
    os.execv(sys.executable, [sys.executable, str(SERVER / "run.py")])
    return 0  # 到不了这里


if __name__ == "__main__":
    sys.exit(main(sys.argv))
