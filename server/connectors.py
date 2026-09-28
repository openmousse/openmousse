"""连接（「我 → 连接」，2026-09-27）：助手接着的每一样东西现在怎么样，一眼看出哪些好好的、哪些要修。

GET /api/connectors[?fresh=1] → {groups: [{id, title, items}], counts: {ok, warn, off}, checkedAt}
每一项：{id, name, icon, status: ok | warn | off, line（一句话的现状）, facts: [{label, value}], uses（它拿来做什么）,
fix（不是 ok 时怎么修，否则 null）, open（可选：app 里能跳去的页 {screen, label}）}。
整份结果缓存 60 秒（按语言各一份，fresh=1 跳过）；聊天渠道的在线状态要起一个 openclaw 进程（1 秒多），单独缓存 2 分钟。

规矩：每一项都是尽力而为，检查本身出了错也不抛（那一项显示「读不到它的状态」，不影响别的）；不返回、不记录任何密钥或配置的值：
只看密钥的名字在不在、文件的修改时间、条数、服务的运行状态（rclone.conf、openclaw.json、tree.json 在代码里读，只取布尔值、时间和端口）。
这一页主要照着作者自己的实例写（训记、Canvas、邮件摘要、Google Drive、Obsidian 都是他的脚本和服务）：别的机器上脚本 / 服务 / 目录不在的，
那一项就不出现；OpenMousse 自带的（Apple 健康、iPhone 日历订阅、推送）没用上时显示「没接」。
"""
from __future__ import annotations

import asyncio
import configparser
import contextlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter

import sources
from chat import _lock, db
import claw
from config import TZ, raw, settings
from data import when
from i18n import L, lang

router = APIRouter()
TTL = 60                 # 整份结果
CHANNEL_TTL = 120        # 聊天渠道的在线状态（openclaw channels status，1 秒多）
UNITS = ("grava-browser.service", "grava-mail.timer", "obsidian-sync.service", "grava-tree.service", "mousse-tree.service")
PLATFORMS = {"claude": "Claude", "chatgpt": "ChatGPT", "gemini": "Gemini", "claude-code": "Claude Code", "notion": "Notion"}
_cache: dict[str, tuple[float, dict]] = {}
_channels: tuple[float, dict | None] | None = None
_building = asyncio.Lock()


# —— 小工具（都不抛） ——————————————————————————————————————————————————

def now() -> datetime:
    return datetime.now(TZ)


def mtime(p: Path | None) -> datetime | None:
    try:
        return datetime.fromtimestamp(p.stat().st_mtime, TZ) if p else None
    except OSError:
        return None


def parse_ts(v: Any) -> datetime | None:
    """ISO 时间（带不带时区都行）、「+0100」写法、毫秒时间戳 → 本地时区的 datetime。"""
    try:
        if isinstance(v, (int, float)) and v > 0:
            return datetime.fromtimestamp(v / 1000 if v > 1e11 else v, TZ)
        if isinstance(v, str) and v.strip():
            s = v.strip().replace("Z", "+00:00")
            try:
                dt = datetime.fromisoformat(s)
            except ValueError:
                dt = datetime.strptime(s, "%Y-%m-%dT%H:%M:%S%z")
            return (dt if dt.tzinfo else dt.replace(tzinfo=TZ)).astimezone(TZ)
    except (ValueError, OSError, OverflowError):
        return None
    return None


def latest(*ds: datetime | None) -> datetime | None:
    return max((d for d in ds if d), default=None)


def age_h(d: datetime | None) -> float:
    return (now() - d).total_seconds() / 3600 if d else float("inf")


def ago(d: datetime | None) -> str:
    """「今天 01:20」这类；没有就是「还没有」。"""
    return when(d) if d else L("还没有", "Not yet")


def at(d: datetime | None) -> str:
    """放在句子中间的时间：英文的 Today / Yesterday 小写（「Last read today 01:20」）。"""
    s = ago(d)
    return s if lang() == "zh" else s.replace("Today", "today").replace("Yesterday", "yesterday").replace("Not yet", "not yet")


def day_at(d: datetime) -> str:
    """句子里只说哪天：「今天」「昨天」「9 月 24 日」（英文小写 today / yesterday）。"""
    s = when(d, False)
    return s if lang() == "zh" else s.replace("Today", "today").replace("Yesterday", "yesterday")


def read_json(p: Path) -> Any:
    try:
        return json.loads(p.read_text(encoding="utf8"))
    except (OSError, ValueError):
        return None


def env_names() -> set[str]:
    """有值的环境变量的名字：服务进程自己的 + .env 里的。只要名字，值不出这个函数。"""
    names = {k for k, v in os.environ.items() if v}
    with contextlib.suppress(OSError, UnicodeDecodeError):
        for line in settings.env_file.read_text(encoding="utf8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].lstrip()
            k, sep, v = line.partition("=")
            if sep and v.strip().strip("'\""):
                names.add(k.strip())
    return names


def units(names: tuple[str, ...]) -> dict[str, dict]:
    """systemd user 单元的状态（一次 systemctl show 全拿到）：{单元: {LoadState, ActiveState, …}}。没有 systemd 就是 {}。"""
    if not shutil.which("systemctl"):
        return {}
    try:
        out = subprocess.run(["systemctl", "--user", "show", *names, "-p", "Id,LoadState,ActiveState,SubState"],  # noqa: S603,S607
                             capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    res: dict[str, dict] = {}
    for block in out.strip().split("\n\n"):
        kv = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
        if kv.get("Id"):
            res[kv["Id"]] = kv
    return res


def unit_loaded(u: dict | None) -> bool:
    return bool(u) and u.get("LoadState") == "loaded"


def unit_active(u: dict | None) -> bool:
    return bool(u) and u.get("ActiveState") == "active"


def openclaw_config() -> dict:
    try:
        c = json.loads(settings.openclaw_json.read_text(encoding="utf8"))
    except (OSError, ValueError):
        return {}
    return c if isinstance(c, dict) else {}


def find_bin(name: str) -> str | None:
    """PATH 里找；找不到再看 openclaw 所在的目录（npm 全局装的命令都在那儿）。"""
    hit = shutil.which(name)
    if hit:
        return hit
    oc = shutil.which(settings.openclaw_bin) or settings.openclaw_bin
    cand = Path(oc).parent / name
    return str(cand) if Path(oc).is_absolute() and cand.is_file() else None


def q1(conn: sqlite3.Connection, sql: str) -> sqlite3.Row | None:
    try:
        return conn.execute(sql).fetchone()
    except sqlite3.Error:  # 表还不存在（新实例）
        return None


def item(id_: str, name: str, icon: str, status: str, line: str, facts: list[tuple[str, str | None]], uses: str,
         fix: str | None = None, open_: dict | None = None) -> dict:
    return {"id": id_, "name": name, "icon": icon, "status": status, "line": line,
            "facts": [{"label": k, "value": v} for k, v in facts if v], "uses": uses,
            "fix": fix if status != "ok" else None, "open": open_}


def app() -> str:
    return settings.app_name


def date_word(d: datetime | None) -> str | None:
    return when(d, False) if d else None


# —— 数据来源 ————————————————————————————————————————————————————————

def xunji_label() -> str:
    x = sources.xunji
    name = getattr(x, "SOURCE_NAME", None)
    if isinstance(name, dict):
        return L(name.get("zh") or name.get("en") or "", name.get("en") or name.get("zh") or "")
    return name if isinstance(name, str) and name else L("训记", "Xunji")


def last_write(log: Path) -> datetime | None:
    """writes.log（每行一个 JSON：ts / endpoint / committed / request_id）里最后一次真正写进去的时间。只读最后 64 KB。"""
    try:
        with log.open("rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 65536))
            lines = f.read().decode("utf8", "replace").splitlines()
    except OSError:
        return None
    for ln in reversed(lines):
        with contextlib.suppress(ValueError):
            row = json.loads(ln)
            if isinstance(row, dict) and row.get("committed"):
                return parse_ts(row.get("ts"))
    return None


def check_xunji(env: set[str]) -> dict | None:
    x = sources.xunji
    if x is None:
        return None
    name = xunji_label()
    keys = sorted({v[2] for v in (getattr(x, "ENDPOINTS", None) or {}).values() if isinstance(v, tuple) and len(v) > 2 and isinstance(v[2], str)})
    missing = [k for k in keys if k not in env]
    cache = getattr(x, "CACHE_DIR", None)
    cache = Path(cache) if cache else None
    read = latest(*(mtime(p) for p in cache.joinpath("reads").glob("*.json"))) if cache and cache.joinpath("reads").is_dir() else None
    wrote = last_write(cache / "writes.log") if cache else None
    seen = latest(read, wrote)
    facts = [(L("最近读到", "Last read"), ago(read)), (L("最近替你记", "Last logged for you"), ago(wrote)),
             (L("密钥", "Keys"), (L(f"{len(keys)} 把都在", f"All {len(keys)} present") if not missing else L(f"缺 {'、'.join(missing)}", f"Missing {', '.join(missing)}")) if keys else None)]
    uses = L(f"看板上的训练、饮食和身体数据都从{name}读；你点了头，它也能替你往{name}里记。",
             f"Workouts, meals and body data on the boards come from {name}; with your OK it also logs things there for you.")
    if missing:
        return item("xunji", name, "dumbbell", "warn", L(f"少了 {len(missing)} 把密钥", f"{len(missing)} key(s) missing"), facts, uses,
                    L(f"服务器的 .env 里缺 {'、'.join(missing)}：在{name} App 里申请开放接口的 Key，放进去后重启服务。",
                      f"The server's .env is missing {', '.join(missing)}: get the open-API key in the {name} app, add it, then restart the service."))
    if age_h(seen) > 48:
        return item("xunji", name, "dumbbell", "warn", L("两天没读到新数据", "No new data in two days") if seen else L("还没读到过数据", "Hasn't read anything yet"),
                    facts, uses, L(f"{name}的 Key 可能失效了：在{name} App 里看看开放接口的 Key 还在不在，换了就更新服务器的 .env。",
                                   f"The {name} key may have expired: check the open-API key in the {name} app and update the server's .env if it changed."))
    return item("xunji", name, "dumbbell", "ok", L(f"最近读到 {at(read)}", f"Last read {at(read)}") if read else L(f"最近替你记 {at(wrote)}", f"Last logged {at(wrote)}"),
                facts, uses)


def check_health() -> dict:
    with _lock, db() as conn:
        daily = q1(conn, "SELECT MAX(date) d, MAX(updated_at) u FROM health_daily")
        metrics = q1(conn, "SELECT MAX(date) d, MAX(updated_at) u, COUNT(DISTINCT metric) k FROM health_metrics")
        sleep = q1(conn, "SELECT MAX(updated_at) u FROM health_sleep")
    synced = latest(*(parse_ts(r["u"]) for r in (daily, metrics, sleep) if r is not None))
    day = max((r["d"] for r in (daily, metrics) if r is not None and r["d"]), default=None)
    kinds = metrics["k"] if metrics is not None else 0
    facts = [(L("上次同步", "Last sync"), ago(synced)), (L("数据到", "Data up to"), date_word(parse_ts(day)) if day else None),
             (L("同步的指标", "Metrics synced"), L(f"{kinds} 种", f"{kinds}") if kinds else None)]
    uses = L("iPhone 把睡眠、心率、步数这些推上来；恢复分、睡眠报告、热量缺口和起床判断都从这里算。",
             "Your iPhone sends sleep, heart rate, steps and more; the recovery score, sleep report, energy balance and wake-up check all come from here.")
    name, icon = L("Apple 健康", "Apple Health"), "heart-pulse"
    if not synced:
        return item("health", name, icon, "off", L("还没同步过", "Never synced"), facts, uses,
                    L(f"在 iPhone 上装好 {app()}、允许它读取健康数据，打开一次就会同步。", f"Install {app()} on your iPhone and allow Health access; it syncs as soon as you open it."))
    if age_h(synced) > 48:
        days = int(age_h(synced) // 24)
        return item("health", name, icon, "warn", L(f"{days} 天没同步了", f"No sync for {days} days"), facts, uses,
                    L(f"在 iPhone 上打开一次 {app()} 就会同步；还不行就去 设置 → 健康 → 数据访问与设备 → {app()}，看看权限开着没有。",
                      f"Open {app()} on your iPhone and it syncs; if not, check Settings → Health → Data Access & Devices → {app()}."))
    return item("health", name, icon, "ok", L(f"上次同步 {at(synced)}", f"Last synced {at(synced)}"), facts, uses)


def check_canvas(env: set[str], state: dict[str, dict]) -> dict | None:
    if not (settings.scripts / "canvas.py").is_file():
        return None
    home = settings.openclaw_home
    tracked = read_json(home / "grava/canvas/tracked.json")
    courses = [str(v) for v in tracked.values()] if isinstance(tracked, dict) else []
    dl = home / "cache/canvas/deadlines.json"
    rows = read_json(dl)
    synced = mtime(dl)
    cutoff = now().strftime("%Y-%m-%d %H:%M")
    left = sum(1 for r in rows if isinstance(r, dict) and not r.get("submitted") and str(r.get("due") or "") >= cutoff) if isinstance(rows, list) else None
    token = "CANVAS_TOKEN" in env
    browser = state.get("grava-browser.service")
    facts = [(L("跟踪的课", "Courses"), "、".join(courses) if courses else L("还没选", "None yet")),
             (L("还没交的作业", "Due, not submitted"), L(f"{left} 份", f"{left}") if left is not None else None),
             (L("作业上次同步", "Coursework synced"), ago(synced)),
             (L("登录", "Sign-in"), L("用 token", "Token") if token else (L("服务器上的浏览器，在跑", "Browser on the server, running") if unit_active(browser)
                                                                           else L("服务器上的浏览器，没开", "Browser on the server, not running") if unit_loaded(browser) else None))]
    uses = L("读你跟踪的课的课件和作业；截止前提醒你，「要记得的」里也有。",
             "Reads slides and coursework for the courses you follow, and reminds you before deadlines (they're in To remember too).")
    name, icon = "Canvas", "graduation"
    if not courses:
        return item("canvas", name, icon, "warn", L("还没选要跟踪的课", "No courses picked yet"), facts, uses,
                    L(f"跟 {app()} 说要跟踪哪几门课就行。", f"Tell {app()} which courses to follow."))
    if not token and unit_loaded(browser) and not unit_active(browser):
        return item("canvas", name, icon, "warn", L("登录用的浏览器没开", "The sign-in browser isn't running"), facts, uses,
                    L("在服务器上运行 systemctl --user restart grava-browser；登录过期的话，用 noVNC 打开那个浏览器重新登录 Canvas。",
                      "On the server run systemctl --user restart grava-browser; if the login expired, open that browser via noVNC and sign in to Canvas again."))
    if age_h(synced) > 12:
        return item("canvas", name, icon, "warn", L("半天多没同步到作业", "Coursework not synced for over 12 hours"), facts, uses,
                    L("Canvas 的登录可能过期了：用 noVNC 打开服务器上的浏览器，重新登录一次（含两步验证）。",
                      "The Canvas login may have expired: open the server's browser via noVNC and sign in again (with 2FA)."))
    return item("canvas", name, icon, "ok", L(f"{len(courses)} 门课 · 作业同步于 {at(synced)}", f"{len(courses)} courses · coursework synced {at(synced)}"), facts, uses)


# —— 日程和邮件 ——————————————————————————————————————————————————————

def check_calendar(env: set[str]) -> dict | None:
    c = sources.calendar_ics
    if c is None:
        return None
    cals = getattr(c, "CALENDARS", None) or {}
    cache = getattr(c, "CACHE", None)
    missing = [v for v in cals.values() if isinstance(v, str) and v not in env]
    fetched = latest(*(mtime(Path(cache) / f"{k}.ics") for k in cals)) if cache else None
    facts = [(L("日历链接", "Calendar link"), L("已设置", "Set") if cals and not missing else L("还没设置", "Not set")),
             (L("上次拉到", "Last fetched"), ago(fetched))]
    uses = L("「今天」页的课表；起床报告、训练和吃饭的时间也照它排。", "Your classes on the Today page; wake-up reports, workouts and meals are planned around them.")
    name, icon = L("课表", "Timetable"), "calendar"
    if missing or not cals:
        return item("calendar", name, icon, "warn", L("日历链接还没设置", "Calendar link not set"), facts, uses,
                    L(f"把学校日历的 ICS 订阅链接放进服务器 .env 的 {'、'.join(missing) or 'ICS 变量'}。",
                      f"Put your school calendar's ICS link in the server's .env as {', '.join(missing) or 'the ICS variable'}."))
    if age_h(fetched) > 24:
        return item("calendar", name, icon, "warn", L("一天多没拉到新课表", "Timetable not refreshed for over a day") if fetched else L("还没拉到过课表", "Never fetched"),
                    facts, uses, L("链接可能失效了：在 Outlook 网页版里重新发布日历，把新的 ICS 链接换进服务器的 .env。",
                                   "The link may have expired: republish the calendar in Outlook on the web and put the new ICS link in the server's .env."))
    return item("calendar", name, icon, "ok", L(f"上次拉到 {at(fetched)}", f"Last fetched {at(fetched)}"), facts, uses)


def check_mail(state: dict[str, dict]) -> dict | None:
    conf = (raw().get("remember") or {}).get("mail") or {}
    items_path = Path(conf["items"]).expanduser() if isinstance(conf, dict) and conf.get("items") else None
    timer = state.get("grava-mail.timer")
    if not items_path:  # 服务器没配 remember.mail：邮件条目进不来，这一项不出现
        return None
    st = read_json(items_path.parent / "state.json") if items_path else None
    st = st if isinstance(st, dict) else {}
    ok_at = parse_ts(st.get("last_ok"))
    res = st.get("last_result") if isinstance(st.get("last_result"), dict) else {}
    j = read_json(items_path) if items_path else None
    items = j.get("items") if isinstance(j, dict) else None
    opened = [x for x in items.values() if isinstance(x, dict) and not x.get("done")] if isinstance(items, dict) else None
    todo = sum(1 for x in opened if x.get("type") == "todo") if opened is not None else None
    facts = [(L("上次看完", "Last run"), ago(ok_at)),
             (L("那次看了", "Threads read"), L(f"{res['scanned']} 封", f"{res['scanned']}") if isinstance(res.get("scanned"), int) else None),
             (L("挑出的新事", "New items"), L(f"{res['new']} 条", f"{res['new']}") if isinstance(res.get("new"), int) else None),
             (L("要办的事", "To-dos"), L(f"{todo} 件", f"{todo}") if todo is not None else None),
             (L("钱和动态", "Money & updates"), L(f"{len(opened) - todo} 条", f"{len(opened) - todo}") if opened is not None and todo is not None else None),
             (L("定时", "Schedule"), (L("开着", "On") if unit_active(timer) else L("关着", "Off")) if unit_loaded(timer) else None)]
    uses = L("每天早晚各看一遍主邮箱，把要办的事、钱、状态变化挑进「要记得的」；邮件原文不存。",
             "Reads your main inbox every morning and evening and picks out to-dos, money and status changes for To remember; it never keeps the emails.")
    name, icon = L("邮件摘要", "Mail digest"), "mail"
    if unit_loaded(timer) and not unit_active(timer):
        return item("mail", name, icon, "warn", L("定时没开", "The schedule is off"), facts, uses,
                    L("在服务器上运行 systemctl --user enable --now grava-mail.timer。", "On the server run systemctl --user enable --now grava-mail.timer."))
    if res.get("ok") is False or age_h(ok_at) > 26:
        return item("mail", name, icon, "warn", L("上一次没跑成", "The last run failed") if res.get("ok") is False else L("一天多没看过邮箱", "Inbox not read for over a day"),
                    facts, uses, L("多半是 Claude 的 Gmail 连接断了：在 claude.ai 的 设置 → 连接器 里重新连一下 Gmail。",
                                   "Most likely Claude's Gmail connection dropped: reconnect Gmail in claude.ai under Settings → Connectors."))
    line = L(f"{at(ok_at)} 看过一遍", f"Read {at(ok_at)}") + (L(f" · {todo} 件要办", f" · {todo} to-dos") if todo else "")
    return item("mail", name, icon, "ok", line, facts, uses)


FEED_NAMES = {"classes": ("课表", "Timetable"), "mine": ("你加的", "Yours"), "deadlines": ("截止", "Deadlines"), "mail": ("邮件里的事", "From email")}
FEED_CLIENTS = {"ios": ("iPhone", "iPhone"), "mac": ("Mac", "Mac"), "google": ("Google 日历", "Google Calendar"), "outlook": ("Outlook", "Outlook")}


def check_feed() -> dict:
    with _lock, db() as conn:
        rows = {}
        with contextlib.suppress(sqlite3.Error):
            rows = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings WHERE key IN ('schedule_feed', 'schedule_feed_seen')")}
    feed, seen = None, None
    with contextlib.suppress(ValueError, TypeError):
        feed = json.loads(rows["schedule_feed"]) if "schedule_feed" in rows else None
    with contextlib.suppress(ValueError, TypeError):
        seen = json.loads(rows["schedule_feed_seen"]) if "schedule_feed_seen" in rows else None
    seen = seen if isinstance(seen, dict) else {}
    seen_at = parse_ts(seen.get("at"))
    who = FEED_CLIENTS.get(str(seen.get("client") or ""))
    who_name = L(who[0], who[1]) if who else L("日历", "A calendar")
    inc = (feed or {}).get("include") if isinstance(feed, dict) else None
    parts = [L(*FEED_NAMES[k]) for k in FEED_NAMES if isinstance(inc, dict) and inc.get(k)]
    facts = [(L("里面有", "Includes"), "、".join(parts) if parts else None), (L("上次来取", "Last picked up"), f"{who_name} · {when(seen_at)}" if seen_at else L("还没有", "Not yet"))]
    uses = L("在 iPhone 自带的日历里也能看到日程和截止（只读）。", "See your schedule and deadlines in the iPhone's own Calendar (read-only).")
    name, icon = L("iPhone 日历订阅", "iPhone calendar feed"), "calendar-sync"
    go = {"screen": "ScheduleFeed", "label": L("去设置", "Set it up")}
    fix = L("在「我 → 日程」里点「添加到 iPhone 日历」。", "In Me → Schedule, tap Add to iPhone Calendar.")
    if not isinstance(feed, dict) or not feed.get("token"):
        return item("feed", name, icon, "off", L("还没生成订阅链接", "No feed link yet"), facts, uses, fix, go)
    if not seen_at:
        return item("feed", name, icon, "off", L("还没有日历来取过", "No calendar has picked it up yet"), facts, uses, fix, go)
    if age_h(seen_at) > 72:
        return item("feed", name, icon, "warn", L(f"{int(age_h(seen_at) // 24)} 天没来取了", f"Not picked up for {int(age_h(seen_at) // 24)} days"), facts, uses,
                    L("手机连不上服务器时取不到：看看 Tailscale 连着没有；订阅删掉了就在「我 → 日程」里重新添加。",
                      "It can't refresh while your phone can't reach the server: check Tailscale; if you removed the subscription, add it again in Me → Schedule."), go)
    return item("feed", name, icon, "ok", L(f"{who_name} 上次来取 {at(seen_at)}", f"{who_name} picked it up {at(seen_at)}"), facts, uses, None, go)


# —— 文件和笔记 ——————————————————————————————————————————————————————

def drive_auth() -> dict:
    """rclone.conf 里 Google Drive 那一段：只取有没有、有没有 refresh token、access token 上次换新的时间（到期减一小时）、权限范围。
    值本身（client、token）不出这个函数。"""
    conf = Path(os.environ.get("RCLONE_CONFIG") or Path.home() / ".config/rclone/rclone.conf")
    out: dict[str, Any] = {"remote": False, "refresh": False, "renewed": None, "scope": None, "changed": mtime(conf)}
    cp = configparser.RawConfigParser()
    try:
        cp.read(conf, encoding="utf8")
    except (configparser.Error, OSError, UnicodeDecodeError):
        return out
    secs = [s for s in cp.sections() if cp.get(s, "type", fallback="") == "drive"]
    if not secs:
        return out
    sec = "gdrive" if "gdrive" in secs else secs[0]
    out["remote"] = True
    out["scope"] = cp.get(sec, "scope", fallback="") or "drive"
    try:
        tok = json.loads(cp.get(sec, "token", fallback="") or "{}")
    except ValueError:
        tok = {}
    if isinstance(tok, dict):
        out["refresh"] = bool(tok.get("refresh_token"))
        exp = parse_ts(tok.get("expiry"))
        out["renewed"] = exp - timedelta(hours=1) if exp else None  # Google 的 access token 一小时有效：到期减一小时 = 上次换新
    return out


def check_drive() -> dict | None:
    if not (settings.scripts / "gdrive.py").is_file():
        return None
    a = drive_auth()
    rclone = os.environ.get("RCLONE") or (str(Path.home() / ".local/bin/rclone") if (Path.home() / ".local/bin/rclone").is_file() else shutil.which("rclone"))
    index = mtime(settings.openclaw_home / "cache/gdrive/index.json")
    used = latest(a["renewed"], index)
    scope = str(a["scope"] or "")
    rights = (L("只读", "Read-only") if "readonly" in scope else L("只碰它自己建的文件", "Only files it created") if "drive.file" in scope
              else L("能读能写", "Read and write")) if a["remote"] else None
    facts = [(L("权限", "Access"), rights), (L("最近连上", "Last connected"), ago(used)), (L("文件索引", "File index"), ago(index))]
    uses = L("找你的 CV、课件和表格；它做的东西放进 Drive 里它自己的文件夹，不覆盖你的文件。",
             "Finds your CV, slides and spreadsheets; what it makes goes into its own Drive folder and never overwrites your files.")
    name, icon = "Google Drive", "hard-drive"
    fix = L("让 Claude Code 按 gdrive.py 开头写的步骤重新授权一次（rclone authorize drive）。",
            "Ask Claude Code to authorize again, following the steps at the top of gdrive.py (rclone authorize drive).")
    if not rclone:
        return item("drive", name, icon, "warn", L("服务器上没有 rclone", "rclone isn't installed on the server"), facts, uses,
                    L("在服务器上装好 rclone（用户级就行）。", "Install rclone on the server (a user install is fine)."))
    if not a["remote"]:
        return item("drive", name, icon, "warn", L("还没授权", "Not authorized yet"), facts, uses, fix)
    if not a["refresh"]:
        return item("drive", name, icon, "warn", L("授权不完整，过一会儿就会失效", "The authorization is incomplete and will lapse soon"), facts, uses, fix)
    return item("drive", name, icon, "ok", L(f"已授权 · 最近连上 {at(used)}", f"Authorized · last connected {at(used)}"), facts, uses)


def vault_dir() -> Path | None:
    """Obsidian 库：世界树的笔记文件夹的上一级（世界树的配置决定库在哪）。"""
    mt = sources.memory_tree
    if mt is None:
        return None
    with contextlib.suppress(Exception):  # noqa: BLE001 — 配置读坏了就当没有库
        return Path(mt.notes_dir()).parent
    return None


def scan_vault(root: Path, limit: int = 20000) -> tuple[int, datetime | None]:
    """笔记篇数（.md）和最近一次改动：跳过隐藏的（.obsidian 这些）和软链。"""
    n, newest, seen = 0, 0.0, 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and not os.path.islink(os.path.join(dirpath, d))]
        for f in filenames:
            if f.startswith("."):
                continue
            p = os.path.join(dirpath, f)
            with contextlib.suppress(OSError):
                st = os.lstat(p)
                if os.path.islink(p):
                    continue
                newest = max(newest, st.st_mtime)
                n += f.endswith(".md")
            seen += 1
            if seen >= limit:
                return n, datetime.fromtimestamp(newest, TZ) if newest else None
    return n, datetime.fromtimestamp(newest, TZ) if newest else None


def check_obsidian(state: dict[str, dict]) -> dict | None:
    vault = vault_dir()
    if not vault or not vault.is_dir():
        return None
    n, changed = scan_vault(vault)
    unit = state.get("obsidian-sync.service")
    sync = (L("在跑", "Running") if unit_active(unit) else L("停了", "Stopped")) if unit_loaded(unit) else L("没开（只在服务器上）", "Off (server only)")
    facts = [(L("笔记", "Notes"), L(f"{n} 篇", f"{n}")), (L("最近改动", "Last change"), ago(changed)), (L("同步", "Sync"), sync)]
    uses = L("你的数据库：世界树的真身就在这里，你在手机上改了会同步回来。", "Your database: the memory tree lives here, and edits on your phone sync back.")
    name, icon = L("Obsidian 库", "Obsidian vault"), "notebook"
    if unit_loaded(unit) and not unit_active(unit):
        return item("obsidian", name, icon, "warn", L("同步停了", "Sync stopped"), facts, uses,
                    L("在服务器上运行 systemctl --user restart obsidian-sync；还不行就让 Claude Code 看一下它的日志。",
                      "On the server run systemctl --user restart obsidian-sync; if that doesn't help, ask Claude Code to check its log."))
    line = L(f"同步在跑 · {n} 篇笔记", f"Syncing · {n} notes") if unit_loaded(unit) else L(f"{n} 篇笔记", f"{n} notes")
    return item("obsidian", name, icon, "ok", line, facts, uses)


def check_notion(env: set[str], oc: dict) -> dict | None:
    skills = ((oc.get("agents") or {}).get("defaults") or {}).get("skills")
    on = isinstance(skills, list) and "notion" in skills
    entry = ((oc.get("skills") or {}).get("entries") or {}).get("notion")
    key = "NOTION_API_TOKEN" in env or bool(isinstance(entry, dict) and entry.get("apiKey"))
    cli = find_bin("ntn")
    if not (on or key or cli):
        return None
    facts = [(L("技能", "Skill"), L("开着", "On") if on else L("没开", "Off")),
             (L("登录", "Sign-in"), L("有", "Yes") if key else L("没有", "No")),
             (L("命令行 ntn", "ntn CLI"), L("装了", "Installed") if cli else L("没装", "Not installed"))]
    uses = L("你写文章的地方；接上以后它能读你指定的页面。", "Where you write; once connected it can read the pages you point it to.")
    name, icon = "Notion", "file-text"
    if key:
        return item("notion", name, icon, "ok", L("已接上", "Connected"), facts, uses)
    return item("notion", name, icon, "off", L("服务器上没有 Notion 的登录", "No Notion sign-in on the server"), facts, uses,
                L("要它读你指定的 Notion 页面：让 Claude Code 在服务器上装好 Notion 命令行（ntn）并登录。",
                  "To let it read the Notion pages you choose, ask Claude Code to install the Notion CLI (ntn) on the server and sign in."))


# —— 记忆 ——————————————————————————————————————————————————————————

def mcp_health(port: int) -> dict | None:
    """世界树 MCP 服务的 /health（只在本机）：{ok, memories, note_issues, platforms}。连不上就是 None。"""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as r:  # noqa: S310 — 固定的本机地址
            data = json.loads(r.read(65536))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def check_tree(state: dict[str, dict]) -> dict | None:
    mt = sources.memory_tree
    if mt is None:
        return None
    current, issues, last = None, 0, {}
    with contextlib.suppress(Exception):  # noqa: BLE001 — 索引读不了就只报服务状态
        with contextlib.closing(mt.connect(sync=False)) as conn:  # 不刷新：MCP 服务每 5 秒刷一次
            current = conn.execute("SELECT COUNT(*) FROM tree WHERE source != 'profile' AND status IN ('active','pending')").fetchone()[0]
            last = {r[0]: r[1] for r in conn.execute("SELECT source, MAX(created_at) FROM tree WHERE source != 'profile' GROUP BY source")}
            with contextlib.suppress(sqlite3.Error):  # issue 表只有 Markdown 存储才有（开源版用 SQLite 存时没有）
                issues = conn.execute("SELECT COUNT(*) FROM issue").fetchone()[0]
    port = 8787
    with contextlib.suppress(Exception):  # noqa: BLE001
        port = int((mt.load_config() or {}).get("port") or 8787)  # tree.json 里只取端口
    health = mcp_health(port)
    tree_unit = "grava-tree" if unit_loaded(state.get("grava-tree.service")) else "mousse-tree"  # 作者的实例叫 grava-tree，安装器装的叫 mousse-tree
    unit = state.get(f"{tree_unit}.service")
    platforms = [p for p in (health or {}).get("platforms") or [] if isinstance(p, str)]
    shown = [p for p in PLATFORMS if p in platforms or p in last] + [p for p in platforms if p not in PLATFORMS]
    facts: list[tuple[str, str | None]] = [(L("当前的叶子", "Current leaves"), L(f"{current} 片", f"{current}") if current is not None else None)]
    for p in shown:
        wrote = parse_ts(last.get(p))
        facts.append((PLATFORMS.get(p, p), L(f"最近写过 {at(wrote)}", f"Last wrote {at(wrote)}") if wrote else L("还没写过", "Hasn't written yet")))
    grava = latest(*(parse_ts(v) for k, v in last.items() if k == "grava" or k.startswith("grava-")))
    facts.append((L(f"{app()} 的 Agent", f"{app()}'s agents"), L(f"最近写过 {at(grava)}", f"Last wrote {at(grava)}") if grava else L("还没写过", "Haven't written yet")))
    uses = L(f"Claude、ChatGPT、Gemini、Claude Code 和 {app()} 共用的记忆：在一个平台说过的，别的平台也知道。",
             f"Memory shared by Claude, ChatGPT, Gemini, Claude Code and {app()}: what you tell one, the others know too.")
    name, icon = L("世界树", "Memory tree"), "tree"
    go = {"screen": "Tree", "label": L("打开世界树", "Open the memory tree")}
    if (unit_loaded(unit) and not unit_active(unit)) or health is None:
        return item("tree", name, icon, "warn", L("共享服务停了，别的 AI 平台暂时连不上", "The sharing service is down; other AI apps can't reach it"), facts, uses,
                    L(f"在服务器上运行 systemctl --user restart {tree_unit}。", f"On the server run systemctl --user restart {tree_unit}."), go)
    if issues:
        return item("tree", name, icon, "warn", L(f"有 {issues} 篇笔记格式不对，先跳过了", f"{issues} note(s) have a formatting problem and were skipped"), facts, uses,
                    L(f"跟 {app()} 说「检查世界树的笔记」，它会告诉你是哪篇、哪里不对。", f'Ask {app()} to "check the memory tree notes"; it will tell you which one and what\'s wrong.'), go)
    writers = {k: parse_ts(v) for k, v in last.items() if k in PLATFORMS or k in ("leo", "owner") or k == "grava" or k.startswith(("grava-", "openclaw-"))}
    newest = max((k for k in writers if writers[k]), key=lambda k: writers[k], default=None)  # 最近一次是谁写的（每周修剪不算）
    who = None
    if newest:
        who = PLATFORMS.get(newest) or (L("你自己", "You") if newest in ("leo", "owner") else L(f"{app()} 的 Agent", f"{app()}'s agent"))
    line = (L(f"{current} 片叶子", f"{current} leaves") if current is not None else L("共享服务在跑", "The sharing service is running")) + (
        L(f" · {who} {day_at(writers[newest])}写过", f" · {who} wrote {day_at(writers[newest])}") if who else "")
    return item("tree", name, icon, "ok", line, facts, uses, None, go)


# —— 渠道和通知 ——————————————————————————————————————————————————————

async def channel_runtime(fresh: bool) -> dict | None:
    """各聊天渠道在不在线（openclaw channels status --json，经 Gateway）。只留布尔值和时间；读不到是 None。别的 claw 不看。"""
    global _channels
    if not claw.is_openclaw():
        return None
    if not fresh and _channels and time.time() - _channels[0] < CHANNEL_TTL:
        return _channels[1]
    exe = shutil.which(settings.openclaw_bin) or settings.openclaw_bin
    data = None
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(exe, "channels", "status", "--json", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(proc.communicate(), 12)
        data = json.loads(out) if proc.returncode == 0 and out else None
    except (OSError, ValueError, asyncio.TimeoutError):
        if proc and proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
        data = None
    res = None
    if isinstance(data, dict) and isinstance(data.get("channels"), dict):
        res = {}
        for key, ch in data["channels"].items():
            if not isinstance(ch, dict):
                continue
            accs = [a for a in (data.get("channelAccounts") or {}).get(key) or [] if isinstance(a, dict)]
            res[key] = {
                "running": bool(ch.get("running")),
                "connected": all(a.get("connected") is not False for a in accs) if accs else bool(ch.get("running")),
                "error": ch.get("lastError") is not None or any(a.get("lastError") is not None for a in accs),
                "token_ok": all(a.get("tokenStatus") in (None, "available") for a in accs),
                "since": max((a.get("lastConnectedAt") or 0 for a in accs), default=0) or ch.get("lastStartAt"),
                "label": (data.get("channelLabels") or {}).get(key) if isinstance((data.get("channelLabels") or {}).get(key), str) else None,
            }
    _channels = (time.time(), res)
    return res


def dm_words(policy: Any) -> str | None:
    """私聊的规矩（openclaw.json 的 dmPolicy）说成人话。"""
    return {"allowlist": L("只认白名单", "Allowlist only"), "pairing": L("要先配对", "Pairing required"),
            "open": L("谁都能发", "Open to anyone"), "disabled": L("不收", "Off")}.get(str(policy)) if policy else None


def check_channels(oc: dict, rt: dict | None) -> list[dict]:
    out = []
    for key, c in (oc.get("channels") or {}).items():
        if not isinstance(c, dict):
            continue
        r = (rt or {}).get(key)
        name = (r or {}).get("label") or {"telegram": "Telegram", "discord": "Discord", "whatsapp": "WhatsApp", "slack": "Slack"}.get(key, key.capitalize())
        icon = "send" if key == "telegram" else "messages"
        facts = [(L("私聊", "DMs"), dm_words(c.get("dmPolicy"))),
                 (L("上次连上", "Last connected"), when(parse_ts(r.get("since"))) if r and parse_ts(r.get("since")) else None)]
        uses = L(f"在 {name} 里和 {app()} 聊，和 app 里的主对话是同一段。", f"Chat with {app()} in {name}; it's the same conversation as the main chat in the app.")
        restart = L("在服务器上运行 systemctl --user restart openclaw-gateway，半分钟后再看；还不行就跑 openclaw channels status --probe 看原因。",
                    "On the server run systemctl --user restart openclaw-gateway and check again in half a minute; if not, run openclaw channels status --probe to see why.")
        if not c.get("enabled"):
            out.append(item(key, name, icon, "off", L("关着", "Off"), facts, uses, L(f"要用的话在 OpenClaw 里打开 {name} 这个渠道。", f"To use it, turn on the {name} channel in OpenClaw.")))
        elif rt is None:
            out.append(item(key, name, icon, "warn", L("读不到在线状态，Gateway 可能没在跑", "Can't read its status; the Gateway may be down"), facts, uses, restart))
        elif not r or not r["running"] or not r["connected"]:
            out.append(item(key, name, icon, "warn", L("开着，但没连上", "On, but not connected"), facts, uses, restart))
        elif not r["token_ok"]:
            out.append(item(key, name, icon, "warn", L("机器人的令牌不对", "The bot token isn't valid"), facts, uses,
                            L(f"在 {name} 那边重新生成机器人的令牌，换进 OpenClaw 的配置。", f"Generate a new bot token in {name} and put it in OpenClaw's config.")))
        elif r["error"]:
            out.append(item(key, name, icon, "warn", L("最近连接出过错", "It had a connection error recently"), facts, uses,
                            L("跑 openclaw channels logs 看是什么错；多半重启 Gateway 就好。", "Run openclaw channels logs to see the error; restarting the Gateway usually fixes it.")))
        else:
            out.append(item(key, name, icon, "ok", L("在线", "Online"), facts, uses))
    return out


def check_push() -> dict:
    with _lock, db() as conn:
        rows = []
        with contextlib.suppress(sqlite3.Error):
            rows = conn.execute("SELECT platform, last_seen, disabled FROM push_tokens").fetchall()
    live = [r for r in rows if not r["disabled"]]
    seen = latest(*(parse_ts(r["last_seen"]) for r in rows))
    kinds: dict[str, int] = {}
    for r in live:
        k = {"ios": "iPhone", "android": "Android"}.get(str(r["platform"]), str(r["platform"] or L("手机", "phone")))
        kinds[k] = kinds.get(k, 0) + 1
    devices = L("、".join(f"{n} 台 {k}" for k, n in kinds.items()), ", ".join(f"{n} × {k}" for k, n in kinds.items())) if kinds else None
    push_cfg = raw().get("push")
    quiet = push_cfg.get("quiet_hours") if isinstance(push_cfg, dict) and "quiet_hours" in push_cfg else ["23:00", "07:30"]  # 同 push.py 的默认
    facts = [(L("手机", "Devices"), devices or L("还没有", "None yet")), (L("上次登记", "Last registered"), ago(seen)),
             (L("静音时段", "Quiet hours"), f"{quiet[0]}–{quiet[1]}" if isinstance(quiet, list) and len(quiet) == 2 else L("不设", "None"))]
    uses = L("回复、起床报告、截止提醒推到手机上；静音时段只进通知中心，不出声。",
             "Replies, wake-up reports and deadline reminders go to your phone; during quiet hours they land silently in Notification Center.")
    name, icon = L("推送", "Notifications"), "bell"
    if not rows:
        return item("push", name, icon, "off", L("还没有手机登记", "No phone registered yet"), facts, uses,
                    L(f"在 iPhone 上打开 {app()}、连上服务器并允许通知，就会自动登记。", f"Open {app()} on your iPhone, connect to the server and allow notifications; it registers on its own."))
    if not live:
        return item("push", name, icon, "warn", L("手机的推送失效了", "Your phone's push registration expired"), facts, uses,
                    L(f"在 iPhone 上打开一次 {app()}；还收不到就去 设置 → 通知 → {app()} 看看开着没有。",
                      f"Open {app()} on your iPhone once; if pushes still don't arrive, check Settings → Notifications → {app()}."))
    return item("push", name, icon, "ok", f"{devices} · " + L(f"上次登记 {at(seen)}", f"registered {at(seen)}"), facts, uses)


# —— 汇总 ————————————————————————————————————————————————————————————

FALLBACK = {"xunji": ("训记", "Xunji", "dumbbell"), "health": ("Apple 健康", "Apple Health", "heart-pulse"), "canvas": ("Canvas", "Canvas", "graduation"),
            "calendar": ("课表", "Timetable", "calendar"), "mail": ("邮件摘要", "Mail digest", "mail"), "feed": ("iPhone 日历订阅", "iPhone calendar feed", "calendar-sync"),
            "drive": ("Google Drive", "Google Drive", "hard-drive"), "obsidian": ("Obsidian 库", "Obsidian vault", "notebook"), "notion": ("Notion", "Notion", "file-text"),
            "tree": ("世界树", "Memory tree", "tree"), "push": ("推送", "Notifications", "bell")}


async def check_claw() -> dict | None:
    """别的 claw（claw.py）：它的对话接口连不连得上。OpenClaw 不在这里列（它的状态看各渠道和模型页）。只报地址、模型、会话方式，不报令牌。"""
    if claw.is_openclaw():
        return None
    try:
        ok, detail = await claw.probe()
    except Exception as exc:  # noqa: BLE001 — 地址写错之类：这一项显示连不上，别拖垮整页
        ok, detail = False, type(exc).__name__
    mode, field, turns = claw.session_mode()
    how = {"history": L(f"每次带上今天的记录（最多 {turns} 轮）", f"sends today's messages each time (up to {turns} turns)"),
           "header": L(f"它自己记，会话键放在请求头 {field}", f"it keeps sessions itself; key in the {field} header"),
           "body": L(f"它自己记，会话键放在请求体的 {field}", f"it keeps sessions itself; key in the request's {field} field"),
           "user": L("它自己记，会话键放在 user 字段", "it keeps sessions itself; key in the user field")}[mode]
    facts = [(L("接口", "API"), claw.base_url() or None), (L("模型", "Model"), ", ".join(claw.models())), (L("对话怎么接上", "Conversation"), how),
             (L("令牌", "Token"), L("有", "Set") if claw.token() else L("没有", "None"))]
    uses = L(f"主对话、各个 Agent、Zen 的「想完了」都由它回答；{app()} 只负责转发、存记录和推送。",
             f"The main chat, every Agent and Zen's wrap-ups are answered by it; {app()} relays, keeps the history and sends notifications.")
    if ok:
        return item("claw", claw.name(), "server", "ok", L("连得上", "Reachable"), facts, uses)
    return item("claw", claw.name(), "server", "warn", L("连不上", "Can't reach it") + f" · {detail}", facts, uses,
                L("看它在不在跑，server.json 里 claw 段的 url、token 对不对；改完不用重启。",
                  "Check that it's running and that url / token in the claw section of server.json are right; no restart needed after editing."))


def safe(fn, *args) -> Any:
    """跑一项检查。检查本身出了错也不拖累别的：日志里只记一行错误类型（不带内容），这一项显示「读不到它的状态」。"""
    try:
        return fn(*args)
    except Exception as exc:  # noqa: BLE001
        print(f"[connectors] {fn.__name__} 出错：{type(exc).__name__}", file=sys.stderr)
        key = fn.__name__.removeprefix("check_")
        zh, en, icon = FALLBACK.get(key, (key, key, "plug"))
        return item(key, L(zh, en), icon, "warn", L("读不到它的状态", "Couldn't check it"), [], L("这一项的检查出错了。", "The check for this one failed."),
                    L("让 Claude Code 看一下服务日志里 [connectors] 开头的那一行。", "Ask Claude Code to look at the [connectors] line in the server log."))


def local_part() -> tuple[dict[str, list], dict]:
    """除了聊天渠道的在线状态，其余都在这里（在线程里跑）。→ (各组的项, openclaw.json)。"""
    env = env_names()
    state = units(UNITS)
    oc = openclaw_config()
    return {
        "data": [safe(check_xunji, env), safe(check_health), safe(check_canvas, env, state)],
        "schedule": [safe(check_calendar, env), safe(check_mail, state), safe(check_feed)],
        "files": [safe(check_drive), safe(check_obsidian, state), safe(check_notion, env, oc)],
        "memory": [safe(check_tree, state)],
        "channels": [safe(check_push)],
    }, oc


def titles() -> dict[str, str]:
    return {"claw": L("你的 claw", "Your claw"), "data": L("数据来源", "Data sources"), "schedule": L("日程和邮件", "Schedule & mail"), "files": L("文件和笔记", "Files & notes"),
            "memory": L("记忆", "Memory"), "channels": L("渠道和通知", "Channels & notifications")}


async def build(fresh: bool) -> dict:
    (part, oc), rt, mine = await asyncio.gather(asyncio.to_thread(local_part), channel_runtime(fresh), check_claw())
    if mine:  # 别的 claw：放最上面一组
        part = {"claw": [mine], **part}
    try:
        chans = check_channels(oc, rt)
    except Exception as exc:  # noqa: BLE001
        print(f"[connectors] check_channels 出错：{type(exc).__name__}", file=sys.stderr)
        chans = []
    part["channels"] = [*chans, *part["channels"]]
    names = titles()
    groups = [{"id": g, "title": names[g], "items": [x for x in items if x]} for g, items in part.items()]
    groups = [g for g in groups if g["items"]]
    counts = {"ok": 0, "warn": 0, "off": 0}
    for g in groups:
        for x in g["items"]:
            counts[x["status"]] += 1
    return {"ok": True, "groups": groups, "counts": counts, "checkedAt": at(now())}


@router.get("/api/connectors")
async def connectors(fresh: int = 0):
    key = lang()
    hit = _cache.get(key)
    if not fresh and hit and time.time() - hit[0] < TTL:
        return hit[1]
    async with _building:  # 同时来的请求别各起一遍 openclaw
        hit = _cache.get(key)
        if not fresh and hit and time.time() - hit[0] < TTL:
            return hit[1]
        res = await build(bool(fresh))
        _cache[key] = (time.time(), res)
        return res
