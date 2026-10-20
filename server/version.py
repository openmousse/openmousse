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

VERSION = "2026.10.13-3"
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
    "pages",            # /api/pages…：Agent 写的页面（盒子里的网页、读数据要你同意、钉到看板）；对话卡 kind page，看板积木 type page
    "pages.native",     # 页面的桥也认手机 app 的 WebView（window.ReactNativeWebView 发、injectJavaScript 派回结果）；更早的服务器手机上只给说明
    "think.home",       # Zen 重排：GET /api/think/home（在想 + 记下的）、POST /api/think/done（完成 / 恢复，不调模型）、GET /api/think/finished、GET /api/think/heat；收藏带 done
    "podcast.stage",    # /api/podcast 的每一期带 stage（进度四格走到第几格）/ state（一句状态）/ waiting（等你处理）
    "chat.commands",    # 第 2 批：GET /api/engine/commands（这个对话能用的斜杠命令，输入框打 / 时列出来）；/api/chat/send 认整句以 / 开头的命令
    "today.categories",  # 「今天」的类别和待办（todocat.py）：/api/today 带 categories、pending.stale，每行 category / overdueDays / canDismiss；
                         # POST /api/remember/done 收 how=dismissed（不用管）、POST /api/remember/category；/api/schedule 收 kind todo（可以没日子）、category；
                         # GET /api/today/eng（工程看板，engboard.py）。开没开看 /api/today 的 categories.on（server.json today.categories）
    "setup",  # 第一次打开的三步和设置中心（2026-10-19）：/api/setup/…（engine/setup_api.py，只认主人的设备）、/api/health 的 no_model；
              # 没有它的老服务器 app 照旧走「好友怎么称呼你 → 连接常用服务」
    "naming",  # 第一次打开时给助手起名字（2026-10-19）：GET / PUT /api/naming（naming.py：app_name、IDENTITY.md 里一行性格，只认主人的设备）

    "nodes",            # 设备节点（第 8 批，nodes.py）：/api/nodes…（节点的 hello / poll / result / bye，主人的列表、开关、批准、移除、出节点配对码）；
                        # 开没开看 GET /api/nodes：没开（engine.nodes，claw 要是 builtin）是 404
    "talk",             # 对讲（第 8 批，app 的对讲页）：GET /api/chat/transcribe → {ready, why}，语音转写配好了没有（不转写、不花钱）
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
