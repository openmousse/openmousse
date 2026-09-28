"""实时活动（锁屏 + 灵动岛，app 1.0.5 起）：服务器说现在该有哪些，app 在前台时照着开、改、关（src/api/native.ts 的 syncLive）。

现在有两种：
- focus：冥想时间的倒计时（从 think.py 的 think_focus 推出来，不存表）
- meal：练后餐倒计时。一次回复写了三餐建议卡（kind meal_plan）、里面有还没到点的「练后」一餐，就开一个倒到那一餐的时刻；
  新的卡里没有练后餐了（吃过了、记了）就关掉（push.notify_run 调 on_run）
别的（Agent、脚本）用 POST /api/live 开，POST /api/live/{key}/end 关。

app 没开时：iOS 17.2 起能用 push-to-start 令牌从服务器开活动，每个活动也有自己的令牌用来改和关。两种令牌 app 都交到 /api/live/token；
服务器 server.json 配了 apns（APNs 的 .p8 密钥，Expo 的推送服务不转发实时活动）就直接推给苹果，没配就只等 app 下次打开时自己开。
  "apns": {"key_file": "~/.openmousse/apns/AuthKey_XXXX.p8", "key_id": "XXXX", "team_id": "YYYY", "topic": "<app 的 bundle id>", "sandbox": false}
字段见 app 的 modules/mousse-native/ios/LiveActivities.swift（ContentState）：title、subtitle、icon（SF Symbol）、accent、startAt / endAt（Unix 秒）、
progress、lines、done。
"""
from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from chat import _lock, db, now_iso
from config import TZ, raw
from i18n import L

router = APIRouter()
ATTRIBUTES_TYPE = "MousseActivityAttributes"
KEY_RE = re.compile(r"^[A-Za-z0-9:_.\-]{1,64}$")
STATE_FIELDS = {"title", "subtitle", "icon", "accent", "startAt", "endAt", "progress", "lines", "done"}
POST_WORKOUT = ("练后", "post-workout", "postworkout", "after workout")
_ready = False


def ldb() -> sqlite3.Connection:
    global _ready
    conn = db()
    if not _ready:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS live_items (key TEXT PRIMARY KEY, kind TEXT NOT NULL, state TEXT NOT NULL, stale_at REAL, ends_at REAL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, ended_at TEXT, dismissed_at TEXT, pushed_at TEXT);
        CREATE TABLE IF NOT EXISTS live_tokens (token TEXT PRIMARY KEY, type TEXT NOT NULL, key TEXT, activity_id TEXT, platform TEXT,
            updated_at TEXT NOT NULL, disabled INTEGER NOT NULL DEFAULT 0, note TEXT);
        """)
        _ready = True
    return conn


def clean_state(state: dict) -> dict:
    s = {k: v for k, v in (state or {}).items() if k in STATE_FIELDS and v is not None}
    s["title"] = str(s.get("title") or "")[:80]
    if "subtitle" in s:
        s["subtitle"] = str(s["subtitle"])[:120]
    if "lines" in s:
        s["lines"] = [str(x)[:120] for x in (s["lines"] or [])][:3]
    for k in ("startAt", "endAt", "progress"):
        if k in s:
            try:
                s[k] = float(s[k])
            except (TypeError, ValueError):
                s.pop(k)
    return s


def item_json(key: str, kind: str, state: dict, stale_at: float | None) -> dict:
    return {"key": key, "kind": kind, "state": state, "staleAt": stale_at}


# —— 现在该有哪些 ——————————————————————————————————————————————————————

def focus_item() -> dict | None:
    try:
        import think  # 延迟导入：think 依赖 chat
        r = think.focus_active()
    except Exception:  # noqa: BLE001
        return None
    if not r:
        return None
    key = f"focus:{r['id']}"
    with _lock, ldb() as conn:
        d = conn.execute("SELECT dismissed_at FROM live_items WHERE key=?", (key,)).fetchone()
    if d and d["dismissed_at"]:
        return None
    start = datetime.fromisoformat(r["started_at"]).timestamp()
    end = datetime.fromisoformat(r["ends_at"]).timestamp()
    state = {"title": L("冥想时间", "Focus time"), "subtitle": L("推送都压着，结束时一起给你", "Notifications held until the end"),
             "icon": "moon.stars.fill", "accent": "purple", "startAt": start, "endAt": end}
    return item_json(key, "focus", state, end)


def current() -> list[dict]:
    now = time.time()
    out = []
    f = focus_item()
    if f:
        out.append(f)
    with _lock, ldb() as conn:
        rows = conn.execute("SELECT * FROM live_items WHERE ended_at IS NULL AND dismissed_at IS NULL AND kind != 'focus'").fetchall()
        for r in rows:
            if r["ends_at"] and r["ends_at"] < now:
                conn.execute("UPDATE live_items SET ended_at=? WHERE key=?", (now_iso(), r["key"]))
                continue
            try:
                state = json.loads(r["state"])
            except ValueError:
                continue
            out.append(item_json(r["key"], r["kind"], state, r["stale_at"]))
    return out


# —— 开、改、关 ————————————————————————————————————————————————————————

def upsert(key: str, kind: str, state: dict, stale_at: float | None = None, ends_at: float | None = None, alert: tuple[str, str] | None = None) -> dict:
    """开或改一个。服务器配了 APNs：之前没推过开始就用 push-to-start 令牌开，推过了就用活动的令牌改。"""
    st = clean_state(state)
    ts = now_iso()
    with _lock, ldb() as conn:
        old = conn.execute("SELECT * FROM live_items WHERE key=?", (key,)).fetchone()
        # 上一个同 key 的已经结束或过了时候：这是新的一个（被划掉的记号也清掉）；还开着的就是改内容，被划掉的保持不开
        fresh = not old or bool(old["ended_at"]) or bool(old["ends_at"] and old["ends_at"] < time.time())
        dismissed = None if fresh else old["dismissed_at"]
        conn.execute("""INSERT INTO live_items(key, kind, state, stale_at, ends_at, created_at, updated_at, ended_at, dismissed_at)
            VALUES(?,?,?,?,?,?,?,NULL,?)
            ON CONFLICT(key) DO UPDATE SET kind=excluded.kind, state=excluded.state, stale_at=excluded.stale_at, ends_at=excluded.ends_at,
            updated_at=excluded.updated_at, ended_at=NULL, dismissed_at=excluded.dismissed_at""",
                     (key, kind, json.dumps(st, ensure_ascii=False), stale_at, ends_at, ts, ts, dismissed))
    if not dismissed:
        apns_async("start" if fresh else "update", key, kind, st, stale_at, alert)
    return item_json(key, kind, st, stale_at)


def end(key: str, state: dict | None = None) -> bool:
    with _lock, ldb() as conn:
        r = conn.execute("SELECT * FROM live_items WHERE key=? AND ended_at IS NULL", (key,)).fetchone()
        if not r:
            return False
        conn.execute("UPDATE live_items SET ended_at=? WHERE key=?", (now_iso(), key))
        st = clean_state(state) if state else json.loads(r["state"])
    apns_async("end", key, r["kind"], st, None, None)
    return True


# —— 练后餐（push.notify_run 在每次回复结束时调） ————————————————————————————

def meal_time(t: str, now: datetime) -> datetime | None:
    m = re.match(r"^\s*(\d{1,2}):(\d{2})", t or "")
    if not m:
        return None
    return now.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0)


def on_card(card: dict | None) -> None:
    """一次回复写了三餐建议卡：有还没到点的练后餐 → 倒计时到那一餐；卡里没有练后餐了 → 关掉。"""
    if not card or card.get("kind") != "meal_plan":
        return
    data = card.get("data") if isinstance(card.get("data"), dict) else {}
    now = datetime.now(TZ)
    meal = None
    for m in data.get("meals") or []:
        if isinstance(m, dict) and any(w in str(m.get("label") or "").lower() for w in POST_WORKOUT):
            meal = m
            break
    if not meal:
        end("meal:post")
        return
    when = meal_time(str(meal.get("time") or ""), now)
    if not when or when < now - timedelta(minutes=10) or when > now + timedelta(hours=4):
        end("meal:post")
        return
    names = L("、", ", ").join(str(i.get("name")) for i in (meal.get("items") or [])[:3] if isinstance(i, dict) and i.get("name"))
    facts = []
    if meal.get("protein"):
        facts.append(L(f"蛋白质 {meal['protein']} g", f"Protein {meal['protein']} g"))
    if meal.get("kcal"):
        facts.append(f"{meal['kcal']} kcal")
    state = {"title": L(f"练后餐 {when:%H:%M}", f"Post-workout meal {when:%H:%M}"), "subtitle": names or str(meal.get("note") or "") or None,
             "icon": "fork.knife", "accent": "orange", "startAt": now.timestamp(), "endAt": when.timestamp(),
             "lines": [" · ".join(facts)] if facts else None}
    # 到点后留 45 分钟（吃饭的工夫），之后自己消失
    upsert("meal:post", "meal", state, stale_at=when.timestamp(), ends_at=(when + timedelta(minutes=45)).timestamp(),
           alert=(state["title"], names or L("练完了，该吃了", "Workout done — time to eat")))


def on_run(new_card) -> None:
    try:
        on_card(new_card)
    except Exception:  # noqa: BLE001 — 实时活动出错不影响推送和回复
        pass


# —— APNs（配了才推） ——————————————————————————————————————————————————

_jwt: tuple[float, str] | None = None
_jwt_lock = threading.Lock()


def apns_cfg() -> dict | None:
    c = raw().get("apns")
    if not isinstance(c, dict) or not all(c.get(k) for k in ("key_file", "key_id", "team_id", "topic")):
        return None
    return c


def apns_token(c: dict) -> str | None:
    global _jwt
    with _jwt_lock:
        if _jwt and time.time() - _jwt[0] < 45 * 60:
            return _jwt[1]
        try:
            import jwt  # pyjwt + cryptography
            key = Path(str(c["key_file"])).expanduser().read_text()
            tok = jwt.encode({"iss": c["team_id"], "iat": int(time.time())}, key, algorithm="ES256", headers={"kid": c["key_id"]})
        except Exception:  # noqa: BLE001 — 密钥读不了 / 库不在：不推
            return None
        _jwt = (time.time(), tok)
        return tok


def tokens(kind: str, key: str | None = None) -> list[str]:
    with _lock, ldb() as conn:
        if kind == "start":
            rows = conn.execute("SELECT token FROM live_tokens WHERE type='start' AND disabled=0 ORDER BY updated_at DESC LIMIT 3").fetchall()
        else:
            rows = conn.execute("SELECT token FROM live_tokens WHERE type='activity' AND key=? AND disabled=0 ORDER BY updated_at DESC LIMIT 3", (key,)).fetchall()
    return [r["token"] for r in rows]


def payload(event: str, key: str, kind: str, state: dict, stale_at: float | None, alert: tuple[str, str] | None) -> dict:
    aps: dict = {"timestamp": int(time.time()), "event": event, "content-state": state}
    if event == "start":
        aps["attributes-type"] = ATTRIBUTES_TYPE
        aps["attributes"] = {"kind": kind, "key": key}
        if alert:
            aps["alert"] = {"title": alert[0], "body": alert[1]}
    if stale_at:
        aps["stale-date"] = int(stale_at)
    if event == "end":
        aps["dismissal-date"] = int(time.time()) + 15 * 60
    return {"aps": aps}


def send_apns(c: dict, token: str, body: dict) -> tuple[int, str]:
    jwt_tok = apns_token(c)
    if not jwt_tok:
        return 0, "no key"
    host = "api.sandbox.push.apple.com" if c.get("sandbox") else "api.push.apple.com"
    cmd = ["curl", "--http2", "-sS", "-o", "-", "-w", "\n%{http_code}", "--max-time", "15",
           "-H", f"authorization: bearer {jwt_tok}", "-H", f"apns-topic: {c['topic']}.push-type.liveactivity",
           "-H", "apns-push-type: liveactivity", "-H", "apns-priority: 10", "--data-binary", "@-", f"https://{host}/3/device/{token}"]
    try:
        p = subprocess.run(cmd, input=json.dumps(body).encode(), capture_output=True, timeout=20, check=False)  # noqa: S603 — 固定参数
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 0, str(exc)[:200]
    out = p.stdout.decode(errors="replace").rsplit("\n", 1)
    try:
        return int(out[-1]), out[0][:300]
    except ValueError:
        return 0, p.stderr.decode(errors="replace")[:300]


def apns(event: str, key: str, kind: str, state: dict, stale_at: float | None, alert: tuple[str, str] | None) -> None:
    c = apns_cfg()
    if not c:
        return
    targets = tokens("start") if event == "start" else tokens("activity", key)
    body = payload(event, key, kind, state, stale_at, alert)
    for tok in targets:
        code, text = send_apns(c, tok, body)
        if code == 410 or "BadDeviceToken" in text or "Unregistered" in text:
            with _lock, ldb() as conn:
                conn.execute("UPDATE live_tokens SET disabled=1, note=? WHERE token=?", (text[:200], tok))
        elif code == 200 and event == "start":
            with _lock, ldb() as conn:
                conn.execute("UPDATE live_items SET pushed_at=? WHERE key=?", (now_iso(), key))


def apns_async(*args) -> None:
    if apns_cfg():
        threading.Thread(target=apns, args=args, daemon=True).start()


# —— 接口 ————————————————————————————————————————————————————————————

@router.get("/api/live")
def get_live():
    return {"ok": True, "items": current(), "apns": apns_cfg() is not None}


class LiveIn(BaseModel):
    key: str
    kind: str = "custom"
    state: dict
    staleAt: float | None = None
    endsAt: float | None = None
    minutes: float | None = None  # 给了就是倒计时 minutes 分钟（endAt = 现在 + minutes），到点后留 30 分钟


@router.post("/api/live")
def post_live(body: LiveIn):
    if not KEY_RE.match(body.key):
        raise HTTPException(400, L("key 只能是字母、数字和 :_.-，最长 64", "key: letters, digits and :_.- only, up to 64"))
    if not str(body.state.get("title") or "").strip():
        raise HTTPException(400, L("state.title 不能空", "state.title is required"))
    state = dict(body.state)
    ends_at = body.endsAt
    if body.minutes:
        now = time.time()
        state.setdefault("startAt", now)
        state["endAt"] = now + body.minutes * 60
        ends_at = ends_at or state["endAt"] + 30 * 60
    return {"ok": True, "item": upsert(body.key, body.kind[:24], state, body.staleAt or state.get("endAt"), ends_at)}


class EndIn(BaseModel):
    state: dict | None = None


@router.post("/api/live/{key}/end")
def post_end(key: str, body: EndIn | None = None):
    return {"ok": True, "ended": end(key, body.state if body else None)}


class TokenIn(BaseModel):
    type: str
    token: str
    key: str | None = None
    id: str | None = None
    platform: str = "ios"


@router.post("/api/live/token")
def post_token(body: TokenIn):
    if body.type not in ("start", "activity") or not re.fullmatch(r"[0-9a-fA-F]{32,256}", body.token or ""):
        raise HTTPException(400, L("令牌不对", "Bad token"))
    with _lock, ldb() as conn:
        conn.execute("""INSERT INTO live_tokens(token, type, key, activity_id, platform, updated_at, disabled) VALUES(?,?,?,?,?,?,0)
            ON CONFLICT(token) DO UPDATE SET type=excluded.type, key=excluded.key, activity_id=excluded.activity_id, platform=excluded.platform,
            updated_at=excluded.updated_at, disabled=0""", (body.token.lower(), body.type, body.key, body.id, body.platform, now_iso()))
    return {"ok": True}


class DismissIn(BaseModel):
    key: str


@router.post("/api/live/dismissed")
def post_dismissed(body: DismissIn):
    """在锁屏上被划掉了：这一个别再开（下一个同 key 的新开始，比如下一次练后餐，会重新开）。"""
    ts = now_iso()
    with _lock, ldb() as conn:
        conn.execute("""INSERT INTO live_items(key, kind, state, created_at, updated_at, dismissed_at) VALUES(?, 'focus', '{}', ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET dismissed_at=excluded.dismissed_at""", (body.key[:64], ts, ts, ts))
    return {"ok": True}
