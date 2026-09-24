"""Grava API：只读的真实数据接口（第一档）+ 真实对话（第二档，见 chat.py）+ 托管 app 的网页版。

- 监听地址、路径、认证都在 server.json（见 config.py）。/api/* 要令牌（Authorization: Bearer），或来源是 Tailscale 白名单里的设备。
- 第一档只读：训练 / 饮食 / 身体数据、日历（可选数据源，见 sources.py）。第二档对话经 Gateway 的 OpenAI 兼容接口，只在 loopback。
- app 其余页面（Groups、独立空间、目标、审批、定时任务、任务、活动、档案、记忆、模型、安全）：见 data.py，全部是真实来源。
- Apple 健康：原生 app 读 HealthKit 后按天推上来，存 grava.db 的 health_daily（见 health.py）。
- 附件：/api/chat/upload 存盘 + 抽文字 / 转写，/api/chat/send 带附件 id，/api/files/{id} 回放，/api/chat/transcribe 语音输入（见 files.py）。
- 推送：/api/push/register 存 Expo push token；回复完成后 push.notify_reply 推一条（见 push.py）。
- 训记数据不落库：训记是真源，这里只有短时缓存（由 xunji.py / calendar_ics.py 管）。
- 数据源可选（sources.py）：workspace 的 scripts/ 里没有对应脚本时，相关接口回 ok=false + missing_source，其它照常。
"""
from __future__ import annotations

import json
import secrets
import shutil
import subprocess
import threading
import time
from datetime import date, datetime, timedelta

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

import sources  # noqa: E402 — 先于 health / data：把 scripts/ 放进 sys.path、加载可选数据源
from config import TZ, settings  # noqa: E402
from sources import calendar_ics, xunji  # noqa: E402
from chat import router as chat_router  # noqa: E402
from health import router as health_router  # noqa: E402
from data import router as data_router  # noqa: E402
from files import router as files_router  # noqa: E402
from push import router as push_router  # noqa: E402

DIST = settings.dist
settings.db.parent.mkdir(parents=True, exist_ok=True)  # 新实例第一次启动：数据目录还不存在
WEEKDAYS = "一二三四五六日"
MEALS = {"morning": "早餐", "breakfast": "早餐", "lunch": "午餐", "noon": "午餐", "dinner": "晚餐", "evening": "晚餐",
         "night": "晚餐", "preworkout": "练前", "postworkout": "练后", "snack": "加餐", "snacks": "加餐", "extra": "加餐"}
MEAL_ORDER = ["早餐", "午餐", "练前", "练后", "晚餐", "加餐"]

app = FastAPI(title=f"{settings.app_name} API", docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(chat_router)
app.include_router(health_router)
app.include_router(data_router)
app.include_router(files_router)
app.include_router(push_router)
app.add_exception_handler(sources.NoSource, sources.no_source_handler)
_whois: dict[str, tuple[float, str | None]] = {}
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
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else (request.headers.get("x-api-key") or request.query_params.get("token") or "").strip()
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
    # 网页版的静态文件公开；/api/* 要认证。
    if request.url.path.startswith("/api/"):
        who = principal_of(request)
        if not who:
            return JSONResponse({"ok": False, "error": "没有有效的接入令牌。在服务器上运行 python3 tokens.py add <名字> 生成一个，填进 app 的连接页。"}, status_code=401)
        request.state.principal = who
    resp = await call_next(request)
    # index.html 和接口都不缓存，避免主屏幕 app 看到旧版；带哈希的静态资源照常缓存
    if request.url.path.startswith("/api/") or request.url.path in ("/", "/index.html"):
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
            best = (f"{s.get('weight')} {s.get('unit') or 'kg'} × {reps}" if w else (f"自重 × {reps}" if reps else "")).strip()
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
    return {"title": t.get("title") or "训练", "minutes": minutes, "kcal": kcal,
            "start": datetime.fromtimestamp(start / 1000, TZ).strftime("%H:%M") if start else "",
            "movements": moves, "sets_done": sum(x["sets_done"] for x in moves)}


def trains_on(d: date) -> list[dict]:
    sources.require("workouts")
    body = {"schema_version": "train_open_api_v2", "datestr": d.isoformat(), "include_full_data": False}
    with _lock:  # 训记限频按天计，这里串行化避免并发打同一天
        data = xunji.call("train_get", body, scope=d.isoformat(), ttl=ttl_for(d))
    return [shape_train(t) for t in (data.get("res") or {}).get("trains") or []]


def shared_channels() -> list[str]:
    """主会话还接着哪些聊天渠道（openclaw.json 里 enabled 的 channels），app 用来说明"主对话和 X 共用"。"""
    try:
        ch = json.loads(settings.openclaw_json.read_text(encoding="utf8")).get("channels") or {}
    except (OSError, ValueError):
        return []
    return [k.capitalize() for k, v in ch.items() if isinstance(v, dict) and v.get("enabled")]


@app.get("/api/health")
def health(request: Request):
    return {"ok": True, "app_name": settings.app_name, "time": datetime.now(TZ).strftime("%Y-%m-%d %H:%M"), "timezone": settings.timezone,
            "principal": getattr(request.state, "principal", None), "shared_channels": shared_channels(),
            "sources": sources.AVAILABLE, "chat": "live"}


@app.get("/api/fitness/week")
def fitness_week(offset: int = 0):
    if not -26 <= offset <= 0:
        raise HTTPException(400, "offset 取 -26 到 0")
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
                raise HTTPException(502, f"训记读取失败：{exc}") from exc
        days.append({"date": d.isoformat(), "d": WEEKDAYS[i], "minutes": sum(x["minutes"] for x in trains),
                     "label": " + ".join(x["title"] for x in trains) or ("休息" if d < t else ("今天还没练" if d == t else "")),
                     "future": d > t, "trains": trains})
    done = [x for x in days if x["trains"]]
    return {"ok": True, "source": "训记", "week_start": monday.isoformat(), "today_index": t.weekday() if offset == 0 else 6,
            "sessions": sum(len(x["trains"]) for x in days), "active_days": len(done),
            "total_minutes": sum(x["minutes"] for x in days), "total_sets": sum(tr["sets_done"] for x in days for tr in x["trains"]),
            "days": days}


def targets_of(day: dict | None) -> dict | None:
    """训记把当天的营养目标放在 foods.limits 里（蛋白质 / 碳水 / 脂肪，克）。
    训记不存热量目标，这里按 4/4/9 从三大营养素换算。配置文件里的 nutrition_targets 可以覆盖。"""
    override = settings.nutrition_targets()
    if override:
        return {**override, "source": "手动设定"}
    lim = ((day or {}).get("foods") or {}).get("limits") or {}
    try:
        p, c, f = float(lim.get("protein") or 0), float(lim.get("carb") or 0), float(lim.get("fat") or 0)
    except (TypeError, ValueError):
        return None
    if not (p and c and f):
        return None
    return {"kcal": round(p * 4 + c * 4 + f * 9), "protein": round(p), "carb": round(c), "fat": round(f), "source": "训记", "kcal_derived": True}


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
        raise HTTPException(502, f"训记读取失败：{exc}") from exc
    day = next((x for x in (data.get("res") or {}).get("days") or [] if x.get("datestr") == d.isoformat()), None)
    totals = (day or {}).get("totals") or {}
    meals: dict[str, dict] = {}
    for rec in ((day or {}).get("foods") or {}).get("records") or []:
        label = MEALS.get(str(rec.get("meal_type") or "").lower(), "其他")
        g, ntr = grams_of(rec), rec.get("ntr") or {}
        kcal = round(float(ntr.get("cal") or 0) * g / 100) if g is not None else None
        protein = round(float(ntr.get("protein") or 0) * g / 100, 1) if g is not None else None
        meal = meals.setdefault(label, {"label": label, "kcal": 0, "protein": 0.0, "items": []})
        meal["items"].append({"name": rec.get("name"), "amount": rec.get("amount"), "unit": rec.get("unit") or "g", "kcal": kcal})
        meal["kcal"] += kcal or 0
        meal["protein"] = round(meal["protein"] + (protein or 0), 1)
    ordered = sorted(meals.values(), key=lambda m: MEAL_ORDER.index(m["label"]) if m["label"] in MEAL_ORDER else 99)
    return {"ok": True, "source": "训记", "date": d.isoformat(),
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
        raise HTTPException(502, f"训记读取失败：{exc}") from exc
    latest = (data.get("res") or {}).get("latest") or {}
    return {"ok": True, "source": "训记", "metrics": [
        {"type": k, "label": v.get("label"), "value": v.get("value"), "unit": v.get("unit"), "date": v.get("datestr")}
        for k, v in latest.items()]}


@app.get("/api/calendar")
def calendar(days: int = 1, from_: str | None = Query(None, alias="from")):
    """from 不给就从今天起；给了（YYYY-MM-DD）就从那天起，「今天」页翻到别的日子用。"""
    if not 1 <= days <= 14:
        raise HTTPException(400, "days 取 1 到 14")
    start = today()
    if from_:
        try:
            start = date.fromisoformat(from_)
        except ValueError as exc:
            raise HTTPException(400, "from 要写成 YYYY-MM-DD") from exc
    lo = datetime.combine(start, datetime.min.time(), TZ)
    hi = lo + timedelta(days=days)
    if not sources.AVAILABLE["calendar"]:
        return {"ok": True, "source": None, "available": False, "timezone": settings.timezone, "events": []}
    try:
        items = calendar_ics.expand(calendar_ics.parse_events(calendar_ics.fetch("ic", False), TZ), lo, hi)
    except SystemExit as exc:
        raise HTTPException(502, "日历拉取失败，链接可能已失效") from exc
    now = datetime.now(TZ)
    out = []
    for x in items:
        s, e = x["start"].astimezone(TZ), x["end"].astimezone(TZ)
        out.append({"date": s.strftime("%Y-%m-%d"), "weekday": WEEKDAYS[s.weekday()], "all_day": x["all_day"],
                    "start": "全天" if x["all_day"] else s.strftime("%H:%M"), "end": "" if x["all_day"] else e.strftime("%H:%M"),
                    "title": x["title"], "location": x["location"], "past": e < now, "tentative": x["busy"] == "TENTATIVE"})
    return {"ok": True, "source": "日历", "timezone": settings.timezone, "events": out}


if DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(DIST), html=True), name="app")
else:
    @app.get("/")
    def no_web():
        return {"ok": True, "app_name": settings.app_name,
                "note": f"网页版还没构建（{DIST} 不存在）。用 iOS app 连这个地址，或在 app/ 目录 npm run web:build。"}

