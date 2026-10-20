"""Grava API：只读的真实数据接口（第一档）+ 真实对话（第二档，见 chat.py）+ 托管 app 的网页版。

- 监听地址、路径、认证都在 server.json（见 config.py）。/api/* 要令牌（Authorization: Bearer），或来源是 Tailscale 白名单里的设备。
- 第一档只读：训练 / 身体数据、日历（可选数据源，见 sources.py）；饮食记录在服务自己的库里（diet.py）。第二档对话经 Gateway 的 OpenAI 兼容接口，只在 loopback。
- app 其余页面（Groups、独立空间、目标、审批、定时任务、任务、活动、档案、记忆、模型、安全）：见 data.py，全部是真实来源。
- Apple 健康：原生 app 读 HealthKit 后按天推上来，存 grava.db 的 health_daily（见 health.py）。
- 附件：/api/chat/upload 存盘 + 抽文字 / 转写，/api/chat/send 带附件 id，/api/files/{id} 回放，/api/chat/transcribe 语音输入（见 files.py）；/api/files/{id}/preview、/page/{n} 在 app 里预览（见 preview.py）。
- 推送：/api/push/register 存 Expo push token；回复完成后 push.notify_run 按档位（ring / quiet / none）推回复或这次写的卡（见 push.py）。
- 收件箱「等你点头」：/api/inbox，Agent 经 inbox_ctl.py 提交要你同意的事，OpenClaw 执行审批也合在里面（见 inbox.py）。
- 未读：/api/unread，各线程的未读回复、新卡片、角标（见 unread.py）。
- 对话列表：/api/chats，Agent 栏和主对话、会议、项目各一行，排序只算你的话和给你的回复，置顶 / 免打扰（见 chats.py）。
- 对话里的转交卡、任务卡：/api/chat/cards；后台任务额度 /api/tasks/quota；后台任务做完静默推一条（见 cards.py）。
- 训记数据不落库：训练和身体数据以训记为真源，这里只有短时缓存（由 xunji.py / calendar_ics.py 管）。
- 饮食记录：/api/diet/*（2026-10-13 起记在服务自己的库里：吃了什么、个人食物库、营养目标；查营养 个人食物库 → Open Food Facts → CoFID → USDA。见 diet.py、nutrition.py）。
- Agent 会议：/api/meetings（几个 Agent 围绕一个议题轮流发言，各用自己的 OpenClaw 会话；Grava 主持、写纪要，确认后待办写进日程和各 Agent 的看板，见 meetings.py）。
- 学习台：/study 网页 + /api/study/*，课件和学习页按课程 / 模块浏览，问答走同一条对话通道（见 study.py）。
- 思考空间和收藏：/api/think/*（碎片是库里收件箱的笔记，AI 不碰，等你叫它；收藏存原件和抽出来的正文；冥想时间压住推送。见 think.py、saves.py）。
- 日程和「要记得的」：/api/schedule（课表 + 自己的日程 + 当天的截止，能改）、/api/remember（作业、邮件、求职的 ddl，打勾），iPhone 日历订阅 /cal/<令牌>.ics（见 schedule.py）。
- 目标：/api/goals（你和 Agent 都能改，每次改动能撤销）、/api/goals/trend（体重、体脂的读数：训记为主、Apple 健康对照）（见 goals.py）。
- 世界树：/api/tree（各 AI 平台共用的记忆：枝和叶子，确认 / 忘记 / 挪枝；真源是 workspace 的 memory_tree.py 或开源版世界树，见 memtree.py）；
  每周修剪的卡和执行：/api/tree/prune（见 treeprune.py）；待定（别人说的要记的先放这里，逐条同意才长成叶子）：/api/memory/held（memhold.py）。
- 连接：/api/connectors（助手接着的每样东西现在怎么样：数据来源、日程和邮件、文件和笔记、记忆、渠道和推送；见 connectors.py）。
- 连接器：/api/apps（你接进来的第三方应用：Notion、Linear……经 OAuth 连上，工具经 /mcp 给 claw，按工具设权限；见 apps.py）。
- 对外接口：/v1/models、/v1/chat/completions（OpenAI 兼容：别的程序把 Agent 当成一个模型用）和 ACP 适配器（engine/acp.py）用的 /v1/openmousse/…；
  server.json 的 engine.api 开了才挂（claw 要是 builtin），只认 api、api-<agent id> 令牌（见 engine/openai_api.py）。
- 数据源可选（sources.py）：workspace 的 scripts/ 里没有对应脚本时，相关接口回 ok=false + missing_source，其它照常。
"""
from __future__ import annotations

import asyncio
import json
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
from datetime import date, datetime, timedelta

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.routing import Mount, Route

import sources  # noqa: E402 — 先于 health / data：把 scripts/ 放进 sys.path、加载可选数据源
from config import TZ, settings  # noqa: E402
import i18n  # noqa: E402
from i18n import L  # noqa: E402
from sources import calendar_ics, xunji, xunji_name  # noqa: E402
import study  # noqa: E402
import cards  # noqa: E402
import claw  # noqa: E402
import version  # noqa: E402
import schedule  # noqa: E402
import chat  # noqa: E402
import settle  # noqa: E402
try:
    import mcp_bridge  # noqa: E402  /mcp：claw 经 MCP 用看板、收件箱这些（见 mcp_bridge.py）
except ImportError:  # 没装 mcp 包的老安装：没有 /mcp，别的照常
    mcp_bridge = None
apps = None
if mcp_bridge is not None:  # 连接器也要 mcp 包（连远端 MCP）：顺带挂上收件箱 app 类的钩子，把连上的应用的工具加进 /mcp
    import apps  # noqa: E402
from chat import router as chat_router  # noqa: E402
from cards import router as cards_router  # noqa: E402
from health import router as health_router  # noqa: E402
from data import first_run, router as data_router  # noqa: E402
from files import router as files_router  # noqa: E402
from preview import router as preview_router  # noqa: E402 — 附件在 app 里预览（2026-09-30）
from push import router as push_router  # noqa: E402
from study import router as study_router  # noqa: E402
from courses import router as courses_router  # noqa: E402
from studyapp import router as studyapp_router  # noqa: E402
from canvasapi import router as canvas_router  # noqa: E402
import coursegen  # noqa: E402
from inbox import router as inbox_router  # noqa: E402
from unread import router as unread_router  # noqa: E402
from chats import router as chats_router  # noqa: E402 — 对话列表（Grava 一边：Agent 栏、主对话、会议、项目；置顶 / 免打扰）
from schedule import router as schedule_router  # noqa: E402
import today as today_page  # noqa: E402 — 本文件有自己的 today()（日期），模块换个名字
import engboard  # noqa: E402
from boards import router as boards_router  # noqa: E402
from views import router as views_router  # noqa: E402  — 集合块（一张表、几种视图）：import 时把 collection 接进 boards.EXTRA
from pages import router as pages_router  # noqa: E402 — 页面（Agent 写的网页，app 放进盒子里显示）：import 时把 page 接进 boards.EXTRA
from pairing import router as pairing_router  # noqa: E402
from nodes import router as nodes_router  # noqa: E402 — 设备节点（第 8 批）：/api/nodes/…，顺带挂上收件箱 kind node 的钩子（nodes.py）
from projects import router as projects_router  # noqa: E402 — 顺带把 kind=project 的收件箱钩子挂上
from goals import router as goals_router  # noqa: E402
import alerts  # noqa: E402
from alerts import router as alerts_router  # noqa: E402
from packs import router as packs_router  # noqa: E402
from podcast import router as podcast_router  # noqa: E402
from proposals import router as proposals_router  # noqa: E402 — 顺带挂上 kind=skill / agent 的收件箱钩子（日结提案）
from memtree import router as tree_router  # noqa: E402
import treeprune  # noqa: E402 — 世界树的每周修剪：/api/tree/prune…，顺带挂上收件箱 kind tree 的钩子和 tree 工具交计划后出卡（bridges.AFTER）
import memhold  # noqa: E402 — 待定（别人说的要记的先放这里）：/api/memory/held…，顺带把待定接上每周修剪卡（inbox 的 EXTEND_ITEMS / AFTER_ITEMS）
import naming  # noqa: E402 — 第一次打开时给助手起名字：/api/naming（naming.py，自带的内核）
from connectors import router as connectors_router  # noqa: E402
from think import router as think_router  # noqa: E402 — 思考空间、冥想时间
from saves import router as saves_router  # noqa: E402 — 收藏
from live import router as live_router  # noqa: E402 — 实时活动（app 1.0.5 起）
from widget import router as widget_router  # noqa: E402 — 小组件（app 1.0.5 起）
from share import public_router as share_public_router, router as share_router  # noqa: E402 — 分享（社交第一层）
import relay  # noqa: E402 — 中继（relay.py）：没有公网入口时朋友经它连进来
import friends  # noqa: E402 — 朋友（社交第二层）：/api/friends…、/api/card；/f/… 只挂在公网小服务 public.py 上
from forward import router as forward_router  # noqa: E402 — 合并转发：几条消息合成一份，发给朋友或转给自己的 Agent
from a2a import router as a2a_router  # noqa: E402 — agent 之间（社交第三层）：/api/a2a…、/api/card…，顺带挂上收件箱 social 类的钩子
from egress import router as egress_router  # noqa: E402 — Doorman 出口（第 9 步）：代办沙箱的出网判断 /api/egress…，顺带挂上收件箱 egress 类的钩子
import diet  # noqa: E402
from diet import router as diet_router  # noqa: E402 — 饮食记录、个人食物库、营养目标、查营养（diet.py + nutrition.py）
from outputs import router as outputs_router  # noqa: E402 — Agent 的产出：浮动标签、小板子、对话里的产出卡片（2026-10-13）
from meetings import router as meetings_router  # noqa: E402 — Agent 会议：/api/meetings…（几个 Agent 轮流发言、主对话的助手写纪要，见 meetings.py）
from engine.api import router as engine_router  # noqa: E402 — 自己的内核：/api/engine/…（状态、一轮轮的记录、赛博的测试入口）、/api/setup/…
from engine import setup_api as engine_setup  # noqa: E402 — 第 9 步「通用的选择层」：出错的样子、/api/health 的 no_model
from engine import tools as engine_tools  # noqa: E402 — 内核的工具和关卡：收件箱 kind tool / schedule 的钩子（重启后点同意也照样跑）
from engine import jobs as engine_jobs  # noqa: E402 — 内核的定时任务和心跳（claw 选 builtin 时才跑，engine/jobs.py、heartbeat.py）
from engine import triggers as engine_triggers  # noqa: E402 — 第 4 批：从外面进来的触发（engine/triggers.py、webhooks.py）
from engine import openai_api as engine_api  # noqa: E402 — 对外接口 /v1（OpenAI 兼容；server.json 的 engine.api 开了才挂）
import channels  # noqa: E402 — 第 6 批的渠道层（server/channels）：Telegram、Discord、邮件；claw 选 builtin、server.json 的 channels 开了才起
from channels.api import router as channels_router  # noqa: E402 — /api/setup/channels…（只认主人的设备）
engine_tools.install_hooks()
channels.install()  # 收件箱新卡发到主人的渠道、名片 Agent 的 DELIVER（telegram / discord / email）、配对卡、message 工具同意后的跑法

DIST = settings.dist
settings.db.parent.mkdir(parents=True, exist_ok=True)  # 新实例第一次启动：数据目录还不存在


def weekday_name(i: int) -> str:
    """周几（0 = 周一）：中文一个字（app 拼成「周一」），英文 Mon。"""
    return L("一二三四五六日"[i], ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")[i])


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if claw.is_openclaw():  # 这两个读 OpenClaw 的任务台账和会话记录；别的 claw 没有（自己的内核的任务卡、回报从内核的事件出，第 5 项）
        cards.start()  # 盯 OpenClaw 的任务台账：后台任务做完了静默推一条
        settle.start()  # 从 app 派的后台任务做完后，派它的对话里那一轮回话接回 app（实时接管 + 从 chat.history 补漏）
    chat.resume_engine()  # 自己的内核上次停机时还在跑的回合：标成 aborted、会话补完整，app 那句补「没拿到回复」（没用过内核什么都不做）
    chat.resume_queued()  # 上次重启前还排着没发的消息：接着发
    if claw.is_builtin():  # 自己内核的后台任务（第 5 项）：上次没跑完的标成被重启打断、做完还没交的交回原对话；在后台查本机的 Claude Code / Codex
        from engine import runners as engine_runners
        from engine import tasks as engine_tasks
        engine_tasks.resume()
        engine_runners.warm()
    asyncio.create_task(chat.resume_ws())  # 走对话通道的：重启前发出去、还没拿到回复的，接回来或补回回复
    alerts.start()  # Agent 的提醒：到点查表，有东西就推
    engine_jobs.start()  # 内核的定时任务和心跳：到点跑一轮主人回合（claw 不是 builtin 时只转不跑）
    engine_triggers.start()  # 第 4 批：从外面进来的触发（webhook……）：记下服务的事件循环、挂上收件箱的钩子（claw 不是 builtin 时什么都不收）
    treeprune.start()  # 开源版世界树：老 Agent 补上枝；builtin 时建好每周修剪的定时任务（server.json tree.prune 能关）
    memhold.start()  # 待定：周日晚上这一周还没有带待定的修剪卡，就出一张只有待定的卡
    if claw.is_builtin():  # 13b：对话结束时的写回（闲了一阵、跨过 04:00、攒够了轮数）：挑几条长成叶子，别人说的进待定（engine/writeback.py）
        from engine import writeback as engine_writeback
        engine_writeback.start()
    if claw.is_builtin():  # 每周整理技能：每个 Agent 自己的技能当一整套看一遍，一张卡逐条定（engine/skill_review.py；engine.skills.weekly，默认关）
        from engine import skill_review as engine_skill_review
        engine_skill_review.start()
    coursegen.start()  # 学习台：生成学习页的队列用服务的事件循环
    friends.start()  # 朋友聊天的投递循环：发出去的消息排队发、失败重试；名片变了告诉朋友
    relay.start()  # 往外连中继：没有公网入口时朋友经它找到这台服务器（server.json "relay": false 关掉）
    if claw.is_builtin():  # 第 6 批的渠道层：开着的 Telegram、Discord、邮件账号收发（OpenClaw 有它自己的渠道，那条路不起这个）
        channels.start()
    if claw.is_builtin():  # 关服务时先停自己内核正在跑的回合（像点了停），别等 app 那条连接、别让工具对着关掉的端口一直连不上
        chat.watch_shutdown()
    if claw.is_builtin():  # 内核的沙箱（第 8 项）：认回 / 收掉上次留下的容器，之后闲了收掉
        from engine import sandbox as engine_sandbox
        engine_sandbox.start()
    if claw.is_builtin():  # 第 3 批：上次重启停下的主机后台命令（engine/host_process.py）：还活着的收掉，往起它们的对话里说一声
        from engine import host_process as engine_host_process
        engine_host_process.start()
    if claw.is_builtin():  # 记忆检索按意思搜（配了 engine.recall.embeddings 才有）：后台慢慢补向量，不卡主人回合
        from engine import semantic as engine_semantic
        engine_semantic.start()
    if claw.is_builtin():  # 第 5 批：先载开着的插件（默认没有；engine/plugins.py），再 startup 的钩子、自带的 boot-md（默认关；engine/user_hooks.py）
        from engine import plugins as engine_plugins, user_hooks as engine_hooks
        await engine_plugins.start()
        engine_hooks.start()
    if mcp_bridge is None:
        yield
    else:
        async with mcp_bridge.server.session_manager.run():  # /mcp 要它（无状态：每个请求一个传输）
            yield
    await chat.engine_shutdown()  # 关的时候还有内核的回合在跑：停下，等它们收完尾、回复入库（最多几秒；没有就马上过）
    await channels.stop()  # 渠道的收发停下（排着没发的、等回复的都在库里，下次启动接着发）
    if (engine_semantic := sys.modules.get("engine.semantic")) is not None:  # 补向量的后台线程：叫它停（没导入过就不导入）
        await asyncio.to_thread(engine_semantic.stop, 2.0)
    from engine import mcp_client as engine_mcp  # 第 13 步：你配的 stdio MCP 服务连进程组一起收（没起过就什么都不做）
    await engine_mcp.shutdown()
    if (engine_hp := sys.modules.get("engine.host_process")) is not None:  # 第 3 批：主机上的后台命令整组收掉，下次起来说一声
        await engine_hp.shutdown()
    if (engine_mg := sys.modules.get("engine.mediagen")) is not None:  # 第 3 批第 14 项：正在生成的视频、音乐收掉，往那个对话说一声
        await engine_mg.shutdown()


app = FastAPI(title=f"{settings.app_name} API", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
if mcp_bridge is not None:  # 放最前面：/mcp 不走 /api 的令牌检查，它自己认 mcp 令牌；/mcp 不带斜杠也要接住（MCP 客户端不跟 POST 的重定向）
    app.router.routes[:0] = [Route("/mcp", endpoint=mcp_bridge.gate, methods=["GET", "POST", "DELETE"]), Mount("/mcp", app=mcp_bridge.gate)]
app.include_router(chat_router)
app.include_router(pairing_router)
app.include_router(nodes_router)
app.include_router(cards_router)  # 在 data 之前：/api/tasks/quota 不能被 /api/tasks/{tid} 先接走
app.include_router(health_router)
app.include_router(data_router)
app.include_router(files_router)
app.include_router(preview_router)
app.include_router(push_router)
app.include_router(study_router)
app.include_router(courses_router)  # 学习台：从零加一门课、改课（courses.py）
app.include_router(studyapp_router)  # 学习台接到 app 上：看板卡、手机学习屏、一次性登录链接（studyapp.py）
app.include_router(canvas_router)  # 学习台连 Canvas（个人令牌，canvasapi.py）
app.include_router(inbox_router)
app.include_router(unread_router)
app.include_router(chats_router)
app.include_router(schedule_router)  # 含 /cal/<令牌>.ics（在网页版的静态文件之前注册）
app.include_router(today_page.router)  # 「今天」页按三块分好组（待处理 / 日程 / 报告），周 / 月视图，推迟（today.py）
app.include_router(engboard.router)  # 「今天」的工程看板：交接本 todos.md 只读同步过来（engboard.py，server.json today.engineering）
app.include_router(alerts_router)  # 在 boards 之前：/api/alerts/… 和看板的路由互不相干，放前面只是好找
app.include_router(packs_router)
app.include_router(boards_router)
app.include_router(views_router)
app.include_router(pages_router)
app.include_router(projects_router)
app.include_router(proposals_router)
app.include_router(goals_router)
app.include_router(treeprune.router)  # 在 tree_router 之前：/api/tree/prune… 是 GET，和 POST /api/tree/{id} 不打架，放前面好找
app.include_router(tree_router)
app.include_router(memhold.router)  # /api/memory/held…：待定（记忆页的「待定」）
app.include_router(naming.router)  # /api/naming：助手的名字和一句性格（naming.py）
app.include_router(connectors_router)
app.include_router(saves_router)  # 在 think 之前：/api/think/saves/… 不能被 think 的路由先接走
app.include_router(live_router)
app.include_router(widget_router)
app.include_router(think_router)
app.include_router(podcast_router)
app.include_router(share_router)
app.include_router(share_public_router)  # /s/<令牌>：分享的链接页，公开（这里只在自己的设备上开得到；外网走 public.py）
app.include_router(friends.router)  # 在 share 之后注册也行：/api/shares/{sid}/send 和 share 的路由不重叠
app.include_router(forward_router)  # /api/forward/basket 在 /api/forward/{fid} 之前定义（同一个 router 里按顺序）
app.include_router(a2a_router)
app.include_router(egress_router)
app.include_router(diet_router)
app.include_router(outputs_router)
app.include_router(meetings_router)  # /api/meetings/suggest 在 /api/meetings/{mid} 之前定义（同一个 router 里按顺序）
app.include_router(engine_router)
app.include_router(channels_router)  # 在网页版的静态文件之前（第 6 批）
if apps is not None:
    app.include_router(apps.router)
app.add_exception_handler(sources.NoSource, sources.no_source_handler)
app.add_exception_handler(engine_setup.SetupError, engine_setup.on_error)  # /api/setup/… 出错：{"ok": false, "error", …}
_whois: dict[str, tuple[float, str | None]] = {}
TOKEN_URL = re.compile(r"^/api/(?:files/|think/file/|think/saves/[^/]+/(?:file|page/\d+)$|podcast/episodes/pe-[0-9a-f]{8}/audio/\d+$|study/file(?:/page)?$)")
_lock = threading.Lock()


def node_of(ip: str) -> str | None:
    """来源 IP 对应的 Tailscale 设备名（5 分钟缓存）。本机没装 tailscale 就是 None。"""
    hit = _whois.get(ip)
    if hit and time.time() - hit[0] < 300:
        return hit[1]
    if not shutil.which("tailscale"):
        return None
    name = None
    try:
        out = subprocess.run(["tailscale", "whois", "--json", ip], capture_output=True, text=True, timeout=5, check=True)  # noqa: S603,S607
        node = json.loads(out.stdout).get("Node", {})
        name = node.get("ComputedName") or (node.get("Name") or "").split(".")[0] or None
    except (subprocess.SubprocessError, ValueError, OSError):
        name = None
    _whois[ip] = (time.time(), name)
    return name


def principal_of(request: Request) -> str | None:
    """这个请求是谁：token:<名字> / tailscale:<设备> / loopback，都不是就 None。"""
    auth = request.headers.get("authorization", "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else (request.headers.get("x-api-key") or "").strip()
    # ?token= 只给 <Image>、网页版的 <audio> 这类带不了请求头的地方用：只认 GET 文件（聊天附件、思考里的语音照片和收藏的文件、
    # 播客原声），别的接口不收 URL 里的令牌（会进日志和浏览记录）
    if not token and request.method == "GET" and TOKEN_URL.match(request.url.path):
        token = (request.query_params.get("token") or "").strip()
    if token:
        for name, tok in settings.tokens().items():
            if tok and secrets.compare_digest(tok, token):
                return f"token:{name}"
    ip = request.client.host if request.client else ""
    if settings.trust_loopback() and ip in ("127.0.0.1", "::1"):
        return "loopback"
    nodes = settings.tailscale_nodes()
    if nodes:
        node = node_of(ip)
        if node and node in nodes:
            return f"tailscale:{node}"
    return None


@app.middleware("http")
async def guard(request: Request, call_next):
    # 这个请求用什么语言回文字（app 带 Accept-Language；没有就用 server.json 的 language）。下游任务会继承它。
    lang_token = i18n.use(request.headers.get("accept-language"))
    # 经谁来的（脚本经 /mcp、内核的工具跑时带 X-Mousse-Via，见 bridges.py）：这个请求开的回合记下它，ChatGPT 套餐据此认 /mcp 和后台。
    # 只会更严：认不出的值当成「不能花」
    via = request.headers.get("x-mousse-via")
    via_token = chat.REQUEST_VIA.set(None if via is None else via if re.fullmatch(r"[a-z0-9_-]{1,24}", via) else "-")
    try:
        # 网页版的静态文件公开；/api/* 要认证。
        if request.url.path.startswith("/api/") and request.url.path != "/api/pair":  # 配对码换令牌：本来就没令牌（见 pairing.py）
            who = principal_of(request)
            if not who:
                return JSONResponse({"ok": False, "error": L("没有有效的接入令牌。在服务器上运行 python3 tokens.py add <名字> 生成一个，填进 app 的连接页。",
                                                             "No valid access token. On the server run python3 tokens.py add <name> and enter the token on the app's connect screen.")},
                                    status_code=401)
            request.state.principal = who
            # Doorman 代理的令牌只管出网判断：别的接口（对话、收件箱……）一律不给，免得它能替你点头
            if who == "token:sentinel" and not request.url.path.startswith("/api/egress/"):
                return JSONResponse({"ok": False, "error": "this token is only for /api/egress"}, status_code=403)
            # MCP 令牌（mcp、mcp-<agent id>）只给 /mcp：只拿着它的 claw（沙箱里的、云上的）不能经 /api 替你点头、配对新设备
            if who == "token:mcp" or who.startswith("token:mcp-"):
                return JSONResponse({"ok": False, "error": L("MCP 令牌只能用在 /mcp 上", "The MCP token only works on /mcp")}, status_code=403)
            # 对外接口的令牌（api、api-<agent id>）同理只给 /v1（engine/openai_api.py）
            if who == "token:api" or who.startswith("token:api-"):
                return JSONResponse({"ok": False, "error": L("API 令牌只能用在 /v1 上", "The API token only works on /v1")}, status_code=403)
            # 节点的令牌（node-<设备>，第 8 批）只给 /api/nodes/*（nodes.py）：当节点的那台电脑拿着它，读不了你的对话、替你点不了头
            if who.startswith("token:node-") and not request.url.path.startswith("/api/nodes/"):
                return JSONResponse({"ok": False, "error": L("节点令牌只能用在 /api/nodes/ 上", "The node token only works on /api/nodes/")},
                                    status_code=403)
        resp = await call_next(request)
    finally:
        i18n.reset(lang_token)
        chat.REQUEST_VIA.reset(via_token)
    # index.html 和接口都不缓存，避免主屏幕 app 看到旧版；带哈希的静态资源照常缓存。
    # 接口自己标了 private 缓存的照它的：附件的缩略图、大图、页图（内容不会变），只在这台设备上缓存
    if (request.url.path.startswith("/api/") or request.url.path in ("/", "/index.html")) and not resp.headers.get("cache-control", "").startswith("private"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


# 对外接口 /v1（engine/openai_api.py）：加在最外层。server.json 的 engine.api 开着（claw 是 builtin）才接 /v1/…，自己认 api 令牌；
# 关着就原样往下交，和没有它一样（每次现读，开、关都不用重启）
app.add_middleware(engine_api.Gate)


def today() -> date:
    return datetime.now(TZ).date()


def ttl_for(d: date) -> int:
    return 600 if d >= today() else 12 * 3600  # 过去的日子基本不变


def top_set(sets: list[dict]) -> str:
    best, best_w = "", -1.0
    for s in sets:
        if not s.get("done"):
            continue
        try:
            w = float(s.get("weight") or 0)
        except ValueError:
            w = 0.0
        if w > best_w:
            best_w = w
            reps = s.get("reps") or ""
            best = (f"{s.get('weight')} {s.get('unit') or 'kg'} × {reps}" if w else (L(f"自重 × {reps}", f"Bodyweight × {reps}") if reps else "")).strip()
    return best


def shape_train(t: dict) -> dict:
    start, end = t.get("start") or 0, t.get("end") or 0
    minutes = round((end - start) / 60000) if end > start else 0
    moves = []
    for m in t.get("movements") or []:
        sets = m.get("sets") or []
        moves.append({"name": m.get("name"), "type": m.get("type") or "", "sets_done": sum(1 for s in sets if s.get("done")),
                      "sets_total": len(sets), "top_set": top_set(sets)})
    kcal = None
    note = t.get("note")
    if isinstance(note, str) and "calorie:" in note:
        try:
            kcal = round(float(note.split("calorie:")[1].split()[0]))
        except (ValueError, IndexError):
            kcal = None
    return {"title": t.get("title") or L("训练", "Workout"), "minutes": minutes, "kcal": kcal,
            "start": datetime.fromtimestamp(start / 1000, TZ).strftime("%H:%M") if start else "",
            "movements": moves, "sets_done": sum(x["sets_done"] for x in moves)}


def trains_on(d: date) -> list[dict]:
    sources.require("workouts")
    body = {"schema_version": "train_open_api_v2", "datestr": d.isoformat(), "include_full_data": False}
    with _lock:  # 训记限频按天计，这里串行化避免并发打同一天
        data = xunji.call("train_get", body, scope=d.isoformat(), ttl=ttl_for(d))
    return [shape_train(t) for t in (data.get("res") or {}).get("trains") or []]


schedule.TRAINS_ON = trains_on  # 过去的训练日程用训练记录补实际时间


def meals_logged(d: date) -> dict:
    """某天记了几条饮食、多少千卡（「今天」页饮食卡上的「今日尚未记录饮食」）。走 diet_day：饮食记录换了真源也跟着换。"""
    day = diet_day(d.isoformat())
    return {"count": int(day.get("item_count") or 0), "kcal": int((day.get("totals") or {}).get("kcal") or 0)}


today_page.MEALS_ON = meals_logged


def shared_channels() -> list[str]:
    """主会话还接着哪些聊天渠道（openclaw.json 里 enabled 的 channels），app 用来说明"主对话和 X 共用"。别的 claw 不知道，当没有。"""
    if not claw.is_openclaw():
        return []
    try:
        ch = json.loads(settings.openclaw_json.read_text(encoding="utf8")).get("channels") or {}
    except (OSError, ValueError):
        return []
    return [claw.channel_name(k) for k, v in ch.items() if k not in claw.NOT_CHAT_CHANNELS and isinstance(v, dict) and v.get("enabled")]


@app.get("/api/health")
def health(request: Request):
    return {"ok": True, "app_name": settings.app_name, "time": datetime.now(TZ).strftime("%Y-%m-%d %H:%M"), "timezone": settings.timezone,
            "principal": getattr(request.state, "principal", None), "shared_channels": shared_channels(),
            "sources": sources.AVAILABLE, "chat": "live", "claw": claw.info(), "server": version.info(claw.kind()),
            "first_run": first_run(),  # 新实例（还没有消息、没有 Agent）：app 在主对话顶上放「从这里开始」
            "no_model": engine_setup.no_model()}  # 第 9 步：还没有能用的模型（自带内核没选、选的没密钥）→ app 走 /api/setup 的引导


@app.get("/api/fitness/week")
def fitness_week(offset: int = 0):
    if not -26 <= offset <= 0:
        raise HTTPException(400, L("offset 取 -26 到 0", "offset must be between -26 and 0"))
    sources.require("workouts")
    t = today()
    monday = t - timedelta(days=t.weekday()) + timedelta(weeks=offset)
    days = []
    for i in range(7):
        d = monday + timedelta(days=i)
        trains = []
        if d <= t:
            try:
                trains = trains_on(d)
            except xunji.XunjiError as exc:
                raise HTTPException(502, L(f"训记读取失败：{exc}", f"Couldn't read Xunji: {exc}")) from exc
        days.append({"date": d.isoformat(), "d": weekday_name(i), "minutes": sum(x["minutes"] for x in trains),
                     "label": " + ".join(x["title"] for x in trains) or (L("休息", "Rest") if d < t else (L("今天还没练", "No workout yet") if d == t else "")),
                     "future": d > t, "trains": trains})
    done = [x for x in days if x["trains"]]
    return {"ok": True, "source": xunji_name(), "week_start": monday.isoformat(), "today_index": t.weekday() if offset == 0 else 6,
            "sessions": sum(len(x["trains"]) for x in days), "active_days": len(done),
            "total_minutes": sum(x["minutes"] for x in days), "total_sets": sum(tr["sets_done"] for x in days for tr in x["trains"]),
            "days": days}


def diet_day(date_: str | None = None) -> dict:
    """饮食看板的一天（2026-10-13 搬到 diet.py 的 day_view）。留着这个名字给按老路径调它的模块（比如产出小板子读蛋白质目标）。"""
    return diet.day_view(date_)


@app.get("/api/body/latest")
def body_latest():
    sources.require("body")
    try:
        with _lock:
            data = xunji.call("body_query", {"include_latest": True, "include_records": False, "limit": 1, "offset": 0}, ttl=3600)
    except xunji.XunjiError as exc:
        raise HTTPException(502, L(f"训记读取失败：{exc}", f"Couldn't read Xunji: {exc}")) from exc
    latest = (data.get("res") or {}).get("latest") or {}
    return {"ok": True, "source": xunji_name(), "metrics": [
        {"type": k, "label": v.get("label"), "value": v.get("value"), "unit": v.get("unit"), "date": v.get("datestr")}
        for k, v in latest.items()]}


@app.get("/api/calendar")
def calendar(days: int = 1, from_: str | None = Query(None, alias="from")):
    """from 不给就从今天起；给了（YYYY-MM-DD）就从那天起，「今天」页翻到别的日子用。"""
    if not 1 <= days <= 14:
        raise HTTPException(400, L("days 取 1 到 14", "days must be between 1 and 14"))
    start = today()
    if from_:
        try:
            start = date.fromisoformat(from_)
        except ValueError as exc:
            raise HTTPException(400, L("from 要写成 YYYY-MM-DD", "from must be YYYY-MM-DD")) from exc
    lo = datetime.combine(start, datetime.min.time(), TZ)
    hi = lo + timedelta(days=days)
    now = datetime.now(TZ)
    # 学习台的 ddl（server.json 的 study.deadlines_cmd）按截止时间插进日程：「今天」页当天就看得到
    dues = [{"date": d.strftime("%Y-%m-%d"), "weekday": weekday_name(d.weekday()), "all_day": False, "start": d.strftime("%H:%M"), "end": "",
             "title": title, "location": "", "past": d < now, "tentative": False, "deadline": True} for d, title in study.deadlines_between(lo, hi)]
    if not sources.AVAILABLE["calendar"]:
        return {"ok": True, "source": None, "available": False, "timezone": settings.timezone, "events": dues}
    try:
        items = calendar_ics.expand(calendar_ics.parse_events(calendar_ics.fetch("ic", False), TZ), lo, hi)
    except SystemExit as exc:
        raise HTTPException(502, L("日历拉取失败，链接可能已失效", "Couldn't fetch the calendar; the link may have expired")) from exc
    out = []
    for x in items:
        s, e = x["start"].astimezone(TZ), x["end"].astimezone(TZ)
        out.append({"date": s.strftime("%Y-%m-%d"), "weekday": weekday_name(s.weekday()), "all_day": x["all_day"],
                    "start": L("全天", "All day") if x["all_day"] else s.strftime("%H:%M"), "end": "" if x["all_day"] else e.strftime("%H:%M"),
                    "title": x["title"], "location": x["location"], "past": e < now, "tentative": x["busy"] == "TENTATIVE"})
    out = sorted(out + dues, key=lambda e: (e["date"], not e["all_day"], e["start"]))
    return {"ok": True, "source": L("日历", "Calendar"), "timezone": settings.timezone, "events": out}


if DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(DIST), html=True), name="app")
else:
    @app.get("/")
    def no_web():
        return {"ok": True, "app_name": settings.app_name,
                "note": L(f"网页版还没构建（{DIST} 不存在）。用 iOS app 连这个地址，或在 app/ 目录 npm run web:build。",
                          f"The web app isn't built yet ({DIST} doesn't exist). Connect the iOS app to this address, or run npm run web:build in app/.")}

