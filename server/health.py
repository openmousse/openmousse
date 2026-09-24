"""Apple 健康：手机上的原生 app 读 HealthKit，按天汇总后推到这里，存进 grava.db 的 health_daily。

- health_daily：恢复卡用的睡眠分期、HRV、静息心率等；health_metrics：全部类型按天汇总，一天一个指标一行。都不存原始样本。
- "哪一天"由手机按当地时区算：睡眠归到醒来的那天。同一天重复上传就覆盖，所以 app 每次都可以把最近两周整段重推。
- 训练数据不走这里（由训练数据源提供）。
- 派生指标（恢复分、热量缺口、体能趋势）的算法在 workspace 的 scripts/apple_health.py（可选数据源，见 sources.py），和对话里的 apple-health skill 用的是同一份。
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import date, timedelta

from fastapi import APIRouter
from pydantic import BaseModel

import sources
from chat import DB, _lock, now_iso
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
