"""Grava API：只读的真实数据接口（第一档）+ 真实对话（第二档，见 chat.py）+ 托管 app 的网页版。

- 监听地址、路径、认证都在 server.json（见 config.py）。/api/* 要令牌（Authorization: Bearer），或来源是 Tailscale 白名单里的设备。
- 第一档只读：训练 / 饮食 / 身体数据、日历（可选数据源，见 sources.py）。第二档对话经 Gateway 的 OpenAI 兼容接口，只在 loopback。
- app 其余页面（Groups、独立空间、目标、审批、定时任务、任务、活动、档案、记忆、模型、安全）：见 data.py，全部是真实来源。
- Apple 健康：原生 app 读 HealthKit 后按天推上来，存 grava.db 的 health_daily（见 health.py）。
- 附件：/api/chat/upload 存盘 + 抽文字 / 转写，/api/chat/send 带附件 id，/api/files/{id} 回放，/api/chat/transcribe 语音输入（见 files.py）；/api/files/{id}/preview、/page/{n} 在 app 里预览（见 preview.py）。
- 推送：/api/push/register 存 Expo push token；回复完成后 push.notify_run 按档位（ring / quiet / none）推回复或这次写的卡（见 push.py）。
- 收件箱「等你点头」：/api/inbox，Agent 经 inbox_ctl.py 提交要你同意的事，OpenClaw 执行审批也合在里面（见 inbox.py）。
- 未读：/api/unread，各线程的未读回复、新卡片、角标（见 unread.py）。
- 对话里的转交卡、任务卡：/api/chat/cards；后台任务额度 /api/tasks/quota；后台任务做完静默推一条（见 cards.py）。
- 训记数据不落库：训记是真源，这里只有短时缓存（由 xunji.py / calendar_ics.py 管）。
- 学习台：/study 网页 + /api/study/*，课件和学习页按课程 / 模块浏览，问答走同一条对话通道（见 study.py）。
- 思考空间和收藏：/api/think/*（碎片是库里收件箱的笔记，AI 不碰，等你叫它；收藏存原件和抽出来的正文；冥想时间压住推送。见 think.py、saves.py）。
- 日程和「要记得的」：/api/schedule（课表 + 自己的日程 + 当天的截止，能改）、/api/remember（作业、邮件、求职的 ddl，打勾），iPhone 日历订阅 /cal/<令牌>.ics（见 schedule.py）。
- 目标：/api/goals（你和 Agent 都能改，每次改动能撤销）、/api/goals/trend（体重、体脂的读数：训记为主、Apple 健康对照）（见 goals.py）。
- 世界树：/api/tree（各 AI 平台共用的记忆：枝和叶子，确认 / 忘记 / 挪枝；真源是 workspace 的 memory_tree.py，见 memtree.py）。
- 连接：/api/connectors（助手接着的每样东西现在怎么样：数据来源、日程和邮件、文件和笔记、记忆、渠道和推送；见 connectors.py）。
- 连接器：/api/apps（你接进来的第三方应用：Notion、Linear……经 OAuth 连上，工具经 /mcp 给 claw，按工具设权限；见 apps.py）。
- 数据源可选（sources.py）：workspace 的 scripts/ 里没有对应脚本时，相关接口回 ok=false + missing_source，其它照常。
"""
from __future__ import annotations

import asyncio
import json
import re
import secrets
import shutil
import subprocess
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
from schedule import router as schedule_router  # noqa: E402
from boards import router as boards_router  # noqa: E402
from pairing import router as pairing_router  # noqa: E402
from projects import router as projects_router  # noqa: E402 — 顺带把 kind=project 的收件箱钩子挂上
from goals import router as goals_router  # noqa: E402
import alerts  # noqa: E402
from alerts import router as alerts_router  # noqa: E402
from packs import router as packs_router  # noqa: E402
from podcast import router as podcast_router  # noqa: E402
from proposals import router as proposals_router  # noqa: E402 — 顺带挂上 kind=skill / agent 的收件箱钩子（日结提案）
from memtree import router as tree_router  # noqa: E402
from connectors import router as connectors_router  # noqa: E402
from think import router as think_router  # noqa: E402 — 思考空间、冥想时间
from saves import router as saves_router  # noqa: E402 — 收藏
from live import router as live_router  # noqa: E402 — 实时活动（app 1.0.5 起）
from widget import router as widget_router  # noqa: E402 — 小组件（app 1.0.5 起）
from share import public_router as share_public_router, router as share_router  # noqa: E402 — 分享（社交第一层）
import relay  # noqa: E402 — 中继（relay.py）：没有公网入口时朋友经它连进来
import friends  # noqa: E402 — 朋友（社交第二层）：/api/friends…、/api/card；/f/… 只挂在公网小服务 public.py 上
from a2a import router as a2a_router  # noqa: E402 — agent 之间（社交第三层）：/api/a2a…、/api/card…，顺带挂上收件箱 social 类的钩子
from egress import router as egress_router  # noqa: E402 — Doorman 出口（第 9 步）：代办沙箱的出网判断 /api/egress…，顺带挂上收件箱 egress 类的钩子

DIST = settings.dist
settings.db.parent.mkdir(parents=True, exist_ok=True)  # 新实例第一次启动：数据目录还不存在
# 训记的 meal_type → 餐次键（排序、分组用）；显示名按请求的语言给（meal_label），app 只显示不比较。
MEALS = {"morning": "breakfast", "breakfast": "breakfast", "lunch": "lunch", "noon": "lunch", "dinner": "dinner", "evening": "dinner",
         "night": "dinner", "preworkout": "preworkout", "postworkout": "postworkout", "snack": "snack", "snacks": "snack", "extra": "snack"}
MEAL_ORDER = ["breakfast", "lunch", "preworkout", "postworkout", "dinner", "snack"]


def weekday_name(i: int) -> str:
    """周几（0 = 周一）：中文一个字（app 拼成「周一」），英文 Mon。"""
    return L("一二三四五六日"[i], ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")[i])


def meal_label(key: str) -> str:
    return {"breakfast": L("早餐", "Breakfast"), "lunch": L("午餐", "Lunch"), "dinner": L("晚餐", "Dinner"), "preworkout": L("练前", "Pre-workout"),
            "postworkout": L("练后", "Post-workout"), "snack": L("加餐", "Snack")}.get(key) or L("其他", "Other")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if claw.is_openclaw():  # 这两个读 OpenClaw 的任务台账和会话记录；别的 claw 没有
        cards.start()  # 盯 OpenClaw 的任务台账：后台任务做完了静默推一条
        settle.start()  # 从 app 派的后台任务做完后，派它的对话里那一轮回话接回 app（实时接管 + 从 chat.history 补漏）
    chat.resume_queued()  # 上次重启前还排着没发的消息：接着发
    asyncio.create_task(chat.resume_ws())  # 走对话通道的：重启前发出去、还没拿到回复的，接回来或补回回复
    alerts.start()  # Agent 的提醒：到点查表，有东西就推
    coursegen.start()  # 学习台：生成学习页的队列用服务的事件循环
    friends.start()  # 朋友聊天的投递循环：发出去的消息排队发、失败重试；名片变了告诉朋友
    relay.start()  # 往外连中继：没有公网入口时朋友经它找到这台服务器（server.json "relay": false 关掉）
    if mcp_bridge is None:
        yield
        return
    async with mcp_bridge.server.session_manager.run():  # /mcp 要它（无状态：每个请求一个传输）
        yield


app = FastAPI(title=f"{settings.app_name} API", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
if mcp_bridge is not None:  # 放最前面：/mcp 不走 /api 的令牌检查，它自己认 mcp 令牌；/mcp 不带斜杠也要接住（MCP 客户端不跟 POST 的重定向）
    app.router.routes[:0] = [Route("/mcp", endpoint=mcp_bridge.gate, methods=["GET", "POST", "DELETE"]), Mount("/mcp", app=mcp_bridge.gate)]
app.include_router(chat_router)
app.include_router(pairing_router)
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
app.include_router(schedule_router)  # 含 /cal/<令牌>.ics（在网页版的静态文件之前注册）
app.include_router(alerts_router)  # 在 boards 之前：/api/alerts/… 和看板的路由互不相干，放前面只是好找
app.include_router(packs_router)
app.include_router(boards_router)
app.include_router(projects_router)
app.include_router(proposals_router)
app.include_router(goals_router)
app.include_router(tree_router)
app.include_router(connectors_router)
app.include_router(saves_router)  # 在 think 之前：/api/think/saves/… 不能被 think 的路由先接走
app.include_router(live_router)
app.include_router(widget_router)
app.include_router(think_router)
app.include_router(podcast_router)
app.include_router(share_router)
app.include_router(share_public_router)  # /s/<令牌>：分享的链接页，公开（这里只在自己的设备上开得到；外网走 public.py）
app.include_router(friends.router)  # 在 share 之后注册也行：/api/shares/{sid}/send 和 share 的路由不重叠
app.include_router(a2a_router)
app.include_router(egress_router)
if apps is not None:
    app.include_router(apps.router)
app.add_exception_handler(sources.NoSource, sources.no_source_handler)
_whois: dict[str, tuple[float, str | None]] = {}
TOKEN_URL = re.compile(r"^/api/(?:files/|think/file/|think/saves/[^/]+/file$|podcast/episodes/pe-[0-9a-f]{8}/audio/\d+$|study/file(?:/page)?$)")
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
        resp = await call_next(request)
    finally:
        i18n.reset(lang_token)
    # index.html 和接口都不缓存，避免主屏幕 app 看到旧版；带哈希的静态资源照常缓存。
    # 接口自己标了 private 缓存的照它的：附件的缩略图、大图、页图（内容不会变），只在这台设备上缓存
    if (request.url.path.startswith("/api/") or request.url.path in ("/", "/index.html")) and not resp.headers.get("cache-control", "").startswith("private"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


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
            "sources": sources.AVAILABLE, "chat": "live", "claw": claw.info(),
            "first_run": first_run()}  # 新实例（还没有消息、没有 Agent）：app 在主对话顶上放「从这里开始」


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


def targets_of(day: dict | None) -> dict | None:
    """训记把当天的营养目标放在 foods.limits 里（蛋白质 / 碳水 / 脂肪，克）。
    训记不存热量目标，这里按 4/4/9 从三大营养素换算。配置文件里的 nutrition_targets 可以覆盖。"""
    override = settings.nutrition_targets()
    if override:
        return {**override, "source": L("手动设定", "Set manually")}
    lim = ((day or {}).get("foods") or {}).get("limits") or {}
    try:
        p, c, f = float(lim.get("protein") or 0), float(lim.get("carb") or 0), float(lim.get("fat") or 0)
    except (TypeError, ValueError):
        return None
    if not (p and c and f):
        return None
    return {"kcal": round(p * 4 + c * 4 + f * 9), "protein": round(p), "carb": round(c), "fat": round(f), "source": xunji_name(), "kcal_derived": True}


def grams_of(rec: dict) -> float | None:
    amount = rec.get("amount")
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return None
    unit = (rec.get("unit") or "g").strip()
    if unit in ("g", "克", "ml", "毫升"):
        return amount
    for u in (rec.get("ntr") or {}).get("foodUnit") or []:
        if u.get("unit") == unit and u.get("gram"):
            return amount * float(u["gram"])
    return None


@app.get("/api/diet/day")
def diet_day(date_: str | None = None):
    d = date.fromisoformat(date_) if date_ else today()
    sources.require("meals")
    try:
        with _lock:
            data = xunji.call("food_query", {"start_date": d.isoformat(), "end_date": d.isoformat(), "include_detail": True}, ttl=ttl_for(d))
    except xunji.XunjiError as exc:
        raise HTTPException(502, L(f"训记读取失败：{exc}", f"Couldn't read Xunji: {exc}")) from exc
    day = next((x for x in (data.get("res") or {}).get("days") or [] if x.get("datestr") == d.isoformat()), None)
    totals = (day or {}).get("totals") or {}
    meals: dict[str, dict] = {}
    for rec in ((day or {}).get("foods") or {}).get("records") or []:
        key = MEALS.get(str(rec.get("meal_type") or "").lower(), "other")
        g, ntr = grams_of(rec), rec.get("ntr") or {}
        kcal = round(float(ntr.get("cal") or 0) * g / 100) if g is not None else None
        protein = round(float(ntr.get("protein") or 0) * g / 100, 1) if g is not None else None
        meal = meals.setdefault(key, {"label": meal_label(key), "kcal": 0, "protein": 0.0, "items": []})
        meal["items"].append({"name": rec.get("name"), "amount": rec.get("amount"), "unit": rec.get("unit") or "g", "kcal": kcal})
        meal["kcal"] += kcal or 0
        meal["protein"] = round(meal["protein"] + (protein or 0), 1)
    ordered = [meals[k] for k in sorted(meals, key=lambda k: MEAL_ORDER.index(k) if k in MEAL_ORDER else 99)]
    return {"ok": True, "source": xunji_name(), "date": d.isoformat(),
            "totals": {"kcal": round(totals.get("totalCal") or 0), "protein": round(totals.get("totalProtein") or 0),
                       "carb": round(totals.get("totalCarb") or 0), "fat": round(totals.get("totalFat") or 0)},
            "targets": targets_of(day), "item_count": (day or {}).get("item_count") or 0, "meals": ordered}


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

