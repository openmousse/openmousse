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

VERSION = "2026.10.13-2"
API = 1
FEATURES = (
    "card.name",  # PATCH /api/card {name}：在 app 里设好友看到的名字
    "friends.prefs",    # GET / PATCH /api/friends/prefs：好友通知开关、通知里显示内容、已读回执；PATCH /api/friends/{id} {pinned, muted}
    "friends.actions",  # POST /api/friends/messages/{id}/edit · /react · /hide；消息带 read、reactions、quote，GET …/messages 带 more、可 before 翻页
    "friends.search",   # GET /api/friends/search；/api/search 不限线程时带好友聊天（kind friend）
    "chats.list",       # GET /api/chats（Agent 栏 + 主对话、会议、项目，带未读、置顶、免打扰、状态）；PATCH / DELETE /api/chats/{thread}
    "friends.clear",    # POST /api/friends/{id}/clear：删除与这位好友的聊天记录（只在本机）
    "diet.log",       # 饮食记录在服务自己的库里：/api/diet/day 的 meals 带 key / time / carb / fat、每样带 id；/api/diet/entries · /days · /undo · /targets
    "diet.log_meal",  # POST /api/diet/log-plan：按饮食建议卡一键「记录这一餐」（份量可改，同一餐不重复记，返回 batch 可撤销）
    "diet.foods",     # 个人食物库 /api/diet/foods、查营养 /api/diet/lookup（个人食物库 → Open Food Facts → CoFID → USDA）
    "think.preview",    # Zen 里的文件在 app 里预览：/api/think/saves/{id}/preview · /page/{n}、/api/think/file/{id}/{i}/preview · /page/{n}；两个原件接口收 preview / inline
    "today",            # GET /api/today（待处理 / 日程 / 报告三块）、/api/today/range（周 / 月）、POST /api/today/snooze；/api/schedule/mark 收 mail:
    "boards.views",     # 看板积木 type collection（集合块：表格 / 看板 / 日历 / 时间线 / 图表）；POST /api/boards/{agent}/view 按排序、筛选、月份、翻页重算一个视图
    "outputs",  # GET /api/outputs（新产出 + 小板子）、/api/outputs/{id}；/api/chat/cards 带 kind output（对话里的产出卡片）
    "meetings",         # /api/meetings…：Agent 会议（发起、按发言人分段的流、纪要确认写进日程和看板、保留为群聊）；对话卡 kind meeting / minutes
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
