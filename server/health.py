"""Apple 健康：手机上的原生 app 读 HealthKit，按天汇总后推到这里，存进 grava.db 的 health_daily。

- health_daily：恢复卡用的睡眠分期、HRV、静息心率等；health_metrics：全部类型按天汇总，一天一个指标一行。
- health_sleep：睡眠是唯一存原始样本的（一段一行），用来看早上几点醒、有没有睡回笼觉（算法在 apple_health.py 的 night_detail / wake）。
- wake_signals：起床信号。快捷指令（闹钟、睡眠模式关闭、拔充电器、打开某个 app）、app 回到前台、「我起来了」都 POST /api/health/signal。
  server.json 的 wake.notify_cmd（可选）：早上收到信号时跑一下这个命令（比如立刻跑一次出建议卡的定时脚本），不用等下一轮。
- "哪一天"由手机按当地时区算：睡眠归到醒来的那天。同一天重复上传就覆盖，所以 app 每次都可以把最近两周整段重推。
- 训练数据不走这里（由训练数据源提供）。
- 派生指标（恢复分、热量缺口、体能趋势）的算法在 workspace 的 scripts/apple_health.py（可选数据源，见 sources.py），和对话里的 apple-health skill 用的是同一份。
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import sources
from chat import DB, _lock, now_iso
from config import TZ, raw
from sources import apple_health  # 可选：scripts/apple_health.py 不在时为 None，三个派生指标接口回「还没接数据源」

router = APIRouter()

FIELDS = ["sleep_min", "deep_min", "rem_min", "core_min", "awake_min", "bed_start", "bed_end",
          "hrv_ms", "rhr_bpm", "resp_rate", "wrist_temp_c"]


def db() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS health_daily (
        date TEXT PRIMARY KEY, sleep_min REAL, deep_min REAL, rem_min REAL, core_min REAL, awake_min REAL,
        bed_start TEXT, bed_end TEXT, hrv_ms REAL, rhr_bpm REAL, resp_rate REAL, wrist_temp_c REAL,
        source TEXT, updated_at TEXT NOT NULL)""")
    return conn


class Day(BaseModel):
    date: str
    sleep_min: float | None = None
    deep_min: float | None = None
    rem_min: float | None = None
    core_min: float | None = None
    awake_min: float | None = None
    bed_start: str | None = None
    bed_end: str | None = None
    hrv_ms: float | None = None
    rhr_bpm: float | None = None
    resp_rate: float | None = None
    wrist_temp_c: float | None = None


class Upload(BaseModel):
    days: list[Day]
    source: str = "healthkit"


@router.post("/api/health/daily")
def upload(body: Upload):
    ts = now_iso()
    with _lock, db() as conn:
        for d in body.days:
            date.fromisoformat(d.date)
            vals = [getattr(d, f) for f in FIELDS]
            conn.execute(f"""INSERT INTO health_daily(date, {', '.join(FIELDS)}, source, updated_at)
                VALUES(?, {', '.join('?' * len(FIELDS))}, ?, ?)
                ON CONFLICT(date) DO UPDATE SET {', '.join(f'{f}=excluded.{f}' for f in FIELDS)},
                source=excluded.source, updated_at=excluded.updated_at""", (d.date, *vals, body.source, ts))
    return {"ok": True, "saved": len(body.days), "at": ts}


@router.get("/api/health/daily")
def daily(days: int = 14):
    since = (date.today() - timedelta(days=min(max(days, 1), 120))).isoformat()
    with _lock, db() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM health_daily WHERE date>=? ORDER BY date", (since,))]
    return {"ok": True, "days": rows, "synced_at": max((r["updated_at"] for r in rows), default=None)}


# —— 睡眠分段：HealthKit 的睡眠样本原样存，一段一行 ——

def sdb() -> sqlite3.Connection:
    conn = db()
    conn.execute("""CREATE TABLE IF NOT EXISTS health_sleep (
        uuid TEXT PRIMARY KEY, start TEXT NOT NULL, end TEXT NOT NULL, start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL,
        value INTEGER NOT NULL, source TEXT, updated_at TEXT NOT NULL)""")
    conn.execute("CREATE INDEX IF NOT EXISTS health_sleep_end ON health_sleep(end_ms)")
    return conn


def _ms(iso: str) -> int:
    t = datetime.fromisoformat(iso)
    return int((t if t.tzinfo else t.replace(tzinfo=TZ)).timestamp() * 1000)


class SleepSegment(BaseModel):
    uuid: str
    value: int          # HealthKit CategoryValueSleepAnalysis：0 在床上、1 睡着（没分期）、2 醒着、3 核心、4 深睡、5 REM
    start: str          # ISO 8601，带手机当地的时区
    end: str
    source: str | None = None   # 哪个设备 / app 记的（Apple Watch、iPhone、第三方）


class SleepUpload(BaseModel):
    segments: list[SleepSegment]
    start: str          # 这次查的范围：范围内服务器上有、这次没传的分段删掉（手机上删了或改了）
    end: str
    source: str = "healthkit"


@router.post("/api/health/sleep")
def upload_sleep(body: SleepUpload):
    ts = now_iso()
    lo, hi = _ms(body.start), _ms(body.end)
    if hi <= lo:
        raise HTTPException(400, "end must be after start")
    with _lock, sdb() as conn:
        # HealthKit 按「和范围有重叠」查，这里按同一个条件先删后写
        conn.execute("DELETE FROM health_sleep WHERE end_ms > ? AND start_ms < ?", (lo, hi))
        for x in body.segments:
            conn.execute("""INSERT OR REPLACE INTO health_sleep(uuid, start, end, start_ms, end_ms, value, source, updated_at)
                VALUES(?,?,?,?,?,?,?,?)""", (x.uuid, x.start, x.end, _ms(x.start), _ms(x.end), x.value, x.source, ts))
    return {"ok": True, "saved": len(body.segments), "at": ts}


# —— 起床信号 ——

SIGNAL_KINDS = {
    "alarm",        # 闹钟响了 / 停了 / 贪睡（快捷指令）
    "wake",         # 睡眠计划的「醒来」（快捷指令）
    "focus_off",    # 睡眠专注模式关了（快捷指令）
    "charger_off",  # 拔了充电器（快捷指令）
    "app",          # 打开了某个 app（快捷指令）
    "foreground",   # 这个 app 回到前台
    "up",           # 在 app 里点了「我起来了」
}
NOTIFY_HOURS = ("05:30", "13:00")   # wake.notify_cmd 只在这段时间里跑（按 timezone）
NOTIFY_GAP_S = 300                  # 同一个 5 分钟里最多跑一次；「我起来了」不限
_notified = 0.0


def wdb() -> sqlite3.Connection:
    conn = db()
    conn.execute("""CREATE TABLE IF NOT EXISTS wake_signals (
        id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, at_ms INTEGER NOT NULL, kind TEXT NOT NULL, source TEXT)""")
    conn.execute("CREATE INDEX IF NOT EXISTS wake_signals_at ON wake_signals(at_ms)")
    return conn


class Signal(BaseModel):
    kind: str
    at: str | None = None       # 不填 = 现在
    source: str | None = None   # shortcut / ios / web …


def _notify(kind: str, now: datetime) -> bool:
    """早上收到信号：跑 server.json 的 wake.notify_cmd（argv 列表），让判断起床的脚本马上看一眼。"""
    global _notified
    conf = raw().get("wake") or {}
    cmd = conf.get("notify_cmd")
    if not cmd:
        return False
    lo, hi = conf.get("notify_hours") or NOTIFY_HOURS
    if not lo <= now.strftime("%H:%M") < hi or (kind != "up" and time.time() - _notified < NOTIFY_GAP_S):
        return False
    _notified = time.time()
    argv = [str(Path(x).expanduser()) if str(x).startswith("~") else str(x) for x in (cmd if isinstance(cmd, list) else str(cmd).split())]
    try:
        subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)  # noqa: S603 — 命令来自本机配置文件
    except OSError:
        return False
    return True


@router.post("/api/health/signal")
def signal(body: Signal):
    kind = body.kind.strip().lower()
    if kind not in SIGNAL_KINDS:
        raise HTTPException(400, f"kind must be one of: {', '.join(sorted(SIGNAL_KINDS))}")
    now = datetime.now(TZ)
    at = now
    if body.at:
        try:
            t = datetime.fromisoformat(body.at)
        except ValueError:
            raise HTTPException(400, "at must be ISO 8601") from None
        at = min(now, (t if t.tzinfo else t.replace(tzinfo=TZ)).astimezone(TZ))
        if now - at > timedelta(days=1):
            raise HTTPException(400, "at is more than a day ago")
    at_ms = int(at.timestamp() * 1000)
    with _lock, wdb() as conn:
        # 同一种信号 60 秒内只记一次（快捷指令的「打开 app」会连着触发）；只留 30 天
        last = conn.execute("SELECT MAX(at_ms) FROM wake_signals WHERE kind=? AND source IS ?", (kind, body.source)).fetchone()[0]
        fresh = last is None or abs(at_ms - last) >= 60_000
        if fresh:
            conn.execute("INSERT INTO wake_signals(at, at_ms, kind, source) VALUES(?,?,?,?)",
                         (at.isoformat(timespec="seconds"), at_ms, kind, body.source))
            conn.execute("DELETE FROM wake_signals WHERE at_ms < ?", (at_ms - 30 * 86400_000,))
    return {"ok": True, "kind": kind, "at": at.strftime("%H:%M"), "recorded": fresh, "notified": fresh and _notify(kind, now)}


@router.get("/api/health/signal")
def signals(hours: int = 24):
    since = int(time.time() * 1000) - min(max(hours, 1), 24 * 30) * 3600_000
    with _lock, wdb() as conn:
        rows = [dict(r) for r in conn.execute("SELECT at, kind, source FROM wake_signals WHERE at_ms >= ? ORDER BY at_ms DESC", (since,))]
    return {"ok": True, "signals": rows}


# —— 全部指标：一天一个指标一行 ——

def mdb() -> sqlite3.Connection:
    conn = db()
    conn.execute("""CREATE TABLE IF NOT EXISTS health_metrics (
        date TEXT NOT NULL, metric TEXT NOT NULL, unit TEXT, sum REAL, avg REAL, min REAL, max REAL,
        count INTEGER, minutes REAL, extra TEXT, source TEXT, updated_at TEXT NOT NULL, PRIMARY KEY(date, metric))""")
    return conn


class Metric(BaseModel):
    date: str
    metric: str
    unit: str | None = None
    sum: float | None = None
    avg: float | None = None
    min: float | None = None
    max: float | None = None
    count: int | None = None
    minutes: float | None = None
    extra: dict | None = None


class MetricUpload(BaseModel):
    rows: list[Metric]
    source: str = "healthkit"


@router.post("/api/health/metrics")
def upload_metrics(body: MetricUpload):
    ts = now_iso()
    with _lock, mdb() as conn:
        for m in body.rows:
            date.fromisoformat(m.date)
            conn.execute("""INSERT INTO health_metrics(date, metric, unit, sum, avg, min, max, count, minutes, extra, source, updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(date, metric) DO UPDATE SET unit=excluded.unit, sum=excluded.sum,
                avg=excluded.avg, min=excluded.min, max=excluded.max, count=excluded.count, minutes=excluded.minutes,
                extra=excluded.extra, source=excluded.source, updated_at=excluded.updated_at""",
                         (m.date, m.metric, m.unit, m.sum, m.avg, m.min, m.max, m.count, m.minutes,
                          json.dumps(m.extra, ensure_ascii=False) if m.extra else None, body.source, ts))
    return {"ok": True, "saved": len(body.rows), "at": ts}


@router.get("/api/health/metrics/status")
def metrics_status():
    with _lock, mdb() as conn:
        r = conn.execute("SELECT COUNT(*) n, COUNT(DISTINCT metric) k, MIN(date) a, MAX(date) b, MAX(updated_at) u FROM health_metrics").fetchone()
    return {"ok": True, "rows": r["n"], "metrics": r["k"], "from": r["a"], "to": r["b"], "synced_at": r["u"]}


@router.get("/api/health/metrics")
def metrics(days: int = 30, metric: str | None = None):
    since = (date.today() - timedelta(days=min(max(days, 1), 400))).isoformat()
    q, args = "SELECT * FROM health_metrics WHERE date>=?", [since]
    if metric:
        q += " AND metric=?"; args.append(metric)
    with _lock, mdb() as conn:
        rows = [dict(r) for r in conn.execute(q + " ORDER BY date, metric", args)]
    for r in rows:
        r["extra"] = json.loads(r["extra"]) if r["extra"] else None
    return {"ok": True, "rows": rows}


# —— 派生指标：恢复分、热量缺口（进饮食看板）、体能趋势（进健身看板）——

_xunji_lock = threading.Lock()  # 热量缺口要查一次训记摄入，串行化避免和 main.py 的饮食查询撞限频


@router.get("/api/health/recovery")
def recovery(days: int = 14):
    sources.require("health")
    return {"ok": True, **apple_health.recovery_series(min(max(days, 1), 60))}


@router.get("/api/health/energy")
def energy(days: int = 7):
    sources.require("health")
    with _xunji_lock:
        return {"ok": True, **apple_health.energy(min(max(days, 1), 30))}


@router.get("/api/fitness/trend")
def fitness_trend(days: int = 90):
    sources.require("health")
    return {"ok": True, **apple_health.trend(min(max(days, 7), 365))}


@router.get("/api/health/wake")
def wake():
    """今天起没起、几点醒、有没有回笼觉（apple_health.wake）。数据源的脚本是旧版、还没有这个函数时当没接。"""
    sources.require("health")
    if not hasattr(apple_health, "wake"):
        raise sources.NoSource("health")
    return {"ok": True, **apple_health.wake()}
