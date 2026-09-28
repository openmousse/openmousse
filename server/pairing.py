"""配对码（2026-09-29）：手机扫码或点链接就连上服务器，长期令牌不用抄、也不经过任何聊天渠道。

给新用户：claw 替用户装好 OpenMousse 以后跑一次 `tokens.py pair --json`，把链接发给用户；用户在手机上点它（或用相机扫终端里的二维码），
app 拿配对码换一个自己的接入令牌。

  python3 tokens.py pair [--name 设备名] [--minutes 10] [--server http://…] [--json]
      出一个一次性配对码：链接 openmousse://pair?s=<服务地址>&c=<码>，终端里再画一个二维码；--json 给 claw 读
  POST /api/pair {"code": "…", "device": "iPhone"}   不要令牌：码对、没过期、没用过 → 新建一个接入令牌回给 app，码当场作废

存：server.json 的 auth.pairing = [{"hash": 码的 sha256, "expires": ISO 时间, "name": 名字}]，只存哈希，过期的顺手清掉。
防猜：码 8 位（去掉 0 O 1 I L 之后的 31 个字母数字，约 40 位），默认 10 分钟过期、只能用一次；/api/pair 每个来源 10 分钟最多错 5 次、
全部加起来 10 分钟最多错 30 次，超了一律 429。/api/pair 在 /api 下面：只在 Tailscale 私网里（公网 Funnel 不开 /api），手机要先进同一个 tailnet。
新令牌叫 device-<设备>-<月日时分>，和手填的令牌一样能在服务器上 tokens.py remove 掉。
"""
from __future__ import annotations

import hashlib
import re
import secrets
import shutil
import subprocess
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

from config import raw, save, settings
from i18n import L

ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
CODE_LEN = 8
WINDOW, PER_SOURCE, TOTAL = 600, 5, 30  # 10 分钟里每个来源最多错 5 次，全部最多错 30 次
_fails: dict[str, list[float]] = {}


def _hash(code: str) -> str:
    return hashlib.sha256(normalize(code).encode()).hexdigest()


def normalize(code: str) -> str:
    """用户抄错的常见样子也认：小写、中间的空格和横杠。"""
    return re.sub(r"[\s-]", "", str(code or "")).upper()


def server_url() -> str:
    """手机连服务器用的地址：server.json 的 bind。监听 0.0.0.0 / 本机时换成 Tailscale 地址（手机经 Tailscale 来）。"""
    host, port = settings.host, settings.port
    if host in ("0.0.0.0", "::", "", "127.0.0.1", "localhost") and shutil.which("tailscale"):
        try:
            ip = subprocess.run(["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=5).stdout.split()
            host = ip[0] if ip else host
        except (OSError, subprocess.SubprocessError):
            pass
    return f"http://{host}:{port}"


def link(server: str, code: str) -> str:
    return "openmousse://pair?" + urllib.parse.urlencode({"s": server, "c": code})


def new_code(name: str = "", minutes: int = 10) -> tuple[str, str]:
    """出一个新配对码，写进 server.json（只存哈希）。→ (码, 过期时间 ISO)。"""
    code = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LEN))
    now = datetime.now(timezone.utc)
    expires = (now + timedelta(minutes=max(1, min(60, int(minutes))))).isoformat(timespec="seconds")
    data = dict(raw(fresh=True))
    auth = dict(data.get("auth") or {})
    live = [p for p in auth.get("pairing") or [] if isinstance(p, dict) and str(p.get("expires") or "") > now.isoformat(timespec="seconds")]
    live.append({"hash": _hash(code), "expires": expires, "name": str(name or "")[:40]})
    auth["pairing"] = live[-20:]
    data["auth"] = auth
    save(data)
    return code, expires


def _slug(device: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(device or "").lower()).strip("-")[:20]
    return s or "phone"


def limited(source: str) -> bool:
    now = time.monotonic()
    for k in list(_fails):
        _fails[k] = [t for t in _fails[k] if now - t < WINDOW]
        if not _fails[k]:
            del _fails[k]
    return len(_fails.get(source, [])) >= PER_SOURCE or sum(len(v) for v in _fails.values()) >= TOTAL


def redeem(code: str, device: str, source: str) -> dict:
    """换令牌。→ {"ok": True, "token", "name", "appName"} 或 {"ok": False, "error", "status"}。"""
    if limited(source):
        return {"ok": False, "status": 429, "error": L("试错太多次了，过 10 分钟再试", "Too many wrong tries; wait 10 minutes and try again")}
    h = _hash(code)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    data = dict(raw(fresh=True))
    auth = dict(data.get("auth") or {})
    pairing = [p for p in auth.get("pairing") or [] if isinstance(p, dict)]
    hit = next((p for p in pairing if secrets.compare_digest(str(p.get("hash") or ""), h)), None)
    if not hit or str(hit.get("expires") or "") <= now or len(normalize(code)) != CODE_LEN:
        _fails.setdefault(source, []).append(time.monotonic())
        return {"ok": False, "status": 403, "error": L("配对码不对，或者已经过期 / 用过了。在服务器上重新出一个：tokens.py pair",
                                                      "Wrong pairing code, or it expired / was already used. Make a new one on the server: tokens.py pair")}
    tokens = dict(auth.get("tokens") or {})
    name = f"device-{_slug(hit.get('name') or device)}-{datetime.now():%m%d%H%M}"
    while name in tokens:
        name += "x"
    tok = secrets.token_urlsafe(24)
    tokens[name] = tok
    auth["tokens"] = tokens
    auth["pairing"] = [p for p in pairing if p is not hit and str(p.get("expires") or "") > now]  # 用过就删，过期的一起清
    data["auth"] = auth
    save(data)
    return {"ok": True, "token": tok, "name": name, "appName": settings.app_name}


def terminal_qr(text: str) -> str:
    """终端里画二维码：两行格子并成一行（▀ ▄ █），黑字白底，带两格白边（深色终端里相机也扫得出）。"""
    import qr  # 同目录，只用标准库

    rows = qr.matrix(text)
    n = len(rows[0])
    quiet = 2
    grid = [[False] * (n + 2 * quiet) for _ in range(quiet)]
    grid += [[False] * quiet + [c == "1" for c in r] + [False] * quiet for r in rows]
    grid += [[False] * (n + 2 * quiet) for _ in range(quiet + 1)]
    out = []
    for y in range(0, len(grid) - 1, 2):
        top, bottom = grid[y], grid[y + 1]
        line = "".join("█" if a and b else "▀" if a else "▄" if b else " " for a, b in zip(top, bottom))
        out.append(f"\033[30;47m{line}\033[0m")
    return "\n".join(out)


# —— 接口 ————————————————————————————————————————————————————————————————

from fastapi import APIRouter, Request  # noqa: E402  放在后面：tokens.py 只用上面那些
from fastapi.responses import JSONResponse  # noqa: E402

router = APIRouter()


@router.post("/api/pair")
async def pair(request: Request):
    """不要令牌（main.py 的 guard 放行这一个路径）：拿配对码换接入令牌。"""
    try:
        body = await request.json()
    except ValueError:
        body = {}
    body = body if isinstance(body, dict) else {}
    r = redeem(str(body.get("code") or ""), str(body.get("device") or ""), request.client.host if request.client else "?")
    if r.get("ok"):
        return r
    return JSONResponse({"ok": False, "error": r["error"]}, status_code=r["status"])
