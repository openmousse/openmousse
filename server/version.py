"""这台服务器是哪一版、能做什么（/api/health 的 server 段，2026-10-05）。

app 走热更新，几乎总比服务器新；服务器要用户自己重跑安装命令才更新。所以 app 不再靠「这个接口 404」「这个字段没回来」去猜，
而是看这里报的：
  version   发版日期，和 OpenClaw 一样写成 年.月.日（同一天再发一版加 -2、-3）。只给人看：设置里显示、排查问题时问对方是哪一版
  commit    正在跑的代码的提交号（启动时读一次；拉了新代码没重启，这里还是旧的，和真在跑的一致）
  api       基线：app 依赖的服务器接口每多一样就加 1。app 里写着它期望的 api（app/src/api/version.ts 的 SERVER_API），
            服务器低于它就在设置里提示更新；等大家都升上来，app 把最低要求提上去，低于这一级的兜底代码就可以删掉
  features  按名字查的能力：新加一样 app 要用的接口，在这里加一个名字，app 用 supports('<名字>') 决定显不显示入口，
            不用先请求一次看是不是 404。跟配置有关的（数据源、claw 能做什么、播客开没开）照旧在各自的地方报，不放这里
  openclaw  接的是 OpenClaw 时它的版本（读安装目录的 package.json；读不到用 openclaw.json 里 meta.lastTouchedVersion）

发版时：改 VERSION；这一版加了 app 要依赖的接口就把 API 加 1、在 FEATURES 里加名字，app 那边同步改 SERVER_API；打 tag v<VERSION>。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

from config import REPO, settings

VERSION = "2026.10.5"
API = 1
FEATURES = (
    "card.name",  # PATCH /api/card {name}：在 app 里设好友看到的名字
)


def _commit() -> str | None:
    try:
        r = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short=7", "HEAD"], capture_output=True, text=True, timeout=3)
        return (r.stdout.strip() or None) if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


COMMIT = _commit()

_oc: tuple[float, str | None] | None = None  # (读的时间, 版本)


def _openclaw_version() -> str | None:
    """OpenClaw 的版本：openclaw 命令所在的 npm 包的 package.json；读不到就用 openclaw.json 最后写它的版本。"""
    exe = shutil.which(settings.openclaw_bin)
    if exe:
        d = Path(exe).resolve().parent
        for _ in range(3):
            try:
                p = json.loads((d / "package.json").read_text(encoding="utf8"))
                if p.get("name") == "openclaw" and p.get("version"):
                    return str(p["version"])
            except (OSError, ValueError):
                pass
            d = d.parent
    try:
        v = (json.loads(settings.openclaw_json.read_text(encoding="utf8")).get("meta") or {}).get("lastTouchedVersion")
        return str(v) if v else None
    except (OSError, ValueError, AttributeError):
        return None


def openclaw_version() -> str | None:
    """升级 OpenClaw 不会重启这台服务器：十分钟重读一次。"""
    global _oc
    if _oc is None or time.monotonic() - _oc[0] > 600:
        _oc = (time.monotonic(), _openclaw_version())
    return _oc[1]


def info(claw_kind: str = "openclaw") -> dict:
    out = {"version": VERSION, "commit": COMMIT, "api": API, "features": list(FEATURES)}
    if claw_kind == "openclaw":
        out["openclaw"] = openclaw_version()
    return out
