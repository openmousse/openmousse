"""定时任务的时间写法和「下一次什么时候」（内核第 6 项）。一律按 server.json 的 timezone 算。

写法（parse；工具和 app 都用它）：
  every 2m / every 30 minutes / every 2h / every hour / every day / hourly   每隔多久（m / h / d；最短 1 分钟）
  30m / 2h                                                            光写多久也是每隔多久（Hermes 的约定）
  in 30m / in 2h                                                      从现在起多久以后，一次
  2026-10-17T09:00 / 2026-10-17 09:00 / tomorrow 9am / today 18:30   那个时刻，一次（不写时区按 server.json 的）
  every day 9am / daily 09:00 / every day at 21:30                    每天几点
  weekdays 9am / weekends at 10:00 / every monday 9am / mon,wed 18:00 哪几天的几点
  0 9 * * 1-5                                                         cron 的五段（分 时 日 月 周；周日 = 0 或 7；本地钟点）
  中文：每 2 分钟、每两小时、每小时、每天 9:00、每天早上 9 点、工作日 9:00、周末 10 点、每周一、三 18:00、2 分钟后、明天 9:00
存成 {"kind": "every", "minutes": N} / {"kind": "daily", "at": "HH:MM", "days": [0..6，周一 = 0；空 = 每天]} /
{"kind": "once", "at": ISO} / {"kind": "cron", "expr": "…"}，都带 "text"（原来的写法）。

下一次（next_after）：
  every   从上一次本该跑的时刻往后排整数个周期，取第一个在 after 后面的（跑晚了也不越漂越远）；没有上一次 = after + 一个周期（Hermes 一样）。
          按真实经过的时间加（换夏令时那天每隔两小时还是两小时）
  daily / cron  下一个对得上的本地钟点：在本地的钟面上往后找（重复的那一小时不会跑两次），再配上 fold，取严格在后面的那个时刻
          （Hermes 的 compute_next_run：croniter 按本地钟面走，两种 fold 里取最早一个严格在后面的）
  once    那个时刻；过了就没有下一次
补跑窗口（grace_seconds）：周期的一半，夹在 120 秒到 2 小时（Hermes 的 _compute_grace_seconds）；一次的 120 秒（Hermes 的
ONESHOT_GRACE_SECONDS），到点时服务器没开着的给 2 小时（Hermes 的「存储停摆时到点的不算错过」，这里推广到服务停着）。

改写自 Hermes 的 cron/jobs.py（parse_duration、_parse_clock_time、_natural_every_to_cron、parse_schedule、_compute_grace_seconds、
compute_next_run），NousResearch/hermes-agent commit 848fc0a，MIT，Copyright (c) 2025 Nous Research；许可全文见仓库根目录的
THIRD_PARTY_NOTICES。cron 的五段是自己写的（Hermes 用 croniter，这里不加依赖），中文写法是加的。
"""
from __future__ import annotations

import contextlib
import re
from datetime import date, datetime, timedelta
from datetime import timezone as dt_timezone

from config import settings
from i18n import L

MIN_MINUTES = 1
MIN_GRACE, MAX_GRACE = 120, 7200
ONCE_GRACE = 120
ZH_DAYS = "一二三四五六日"
EN_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


class WhenError(ValueError):
    """写法认不出来：原话（带用法）回给模型或 app。"""


def usage() -> str:
    return L("能用的写法：每隔多久「every 30m」「every 2h」「每 2 分钟」；多久以后一次「in 30m」「2 分钟后」；某个时刻一次「2026-10-17 09:00」"
             "「tomorrow 9am」「明天 9:00」；每天几点「every day 9am」「每天 9:00」；哪几天「weekdays 9am」「every monday 9am」「工作日 9:00」"
             "「每周一、三 18:00」；cron 五段「0 9 * * 1-5」。",
             "Forms: interval 'every 30m', 'every 2h'; once after a delay 'in 30m'; once at a time '2026-10-17 09:00', 'tomorrow 9am'; "
             "daily 'every day 9am', 'daily 21:30'; some days 'weekdays 9am', 'every monday 9am', 'mon,wed 18:00'; cron '0 9 * * 1-5'.")


def now() -> datetime:
    return datetime.now(settings.tz)


def local(dt: datetime) -> datetime:
    """不带时区的按 server.json 的时区算，带了的换到这个时区。"""
    return dt.replace(tzinfo=settings.tz) if dt.tzinfo is None else dt.astimezone(settings.tz)


def iso(dt: datetime | None) -> str | None:
    """存进库的样子：带时区偏移，到秒。"""
    return dt.astimezone(settings.tz).isoformat(timespec="seconds") if dt else None


def parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return local(datetime.fromisoformat(s))
    except ValueError:
        return None


# —— 中文数字、多久、几点 ——————————————————————————————————————————————————————

_ZH_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_ZH_RUN = re.compile(r"[一二两三四五六七八九]?十[一二三四五六七八九]?|[一二两三四五六七八九]")


def zh_digits(s: str) -> str:
    """「两小时」「十五分钟」「九点」里的中文数字换成阿拉伯数字（一百以内，够用了）。"""
    def num(m: re.Match) -> str:
        t = m.group(0)
        if "十" in t:
            a, _, b = t.partition("十")
            return str((_ZH_NUM[a] if a else 1) * 10 + (_ZH_NUM[b] if b else 0))
        return str(_ZH_NUM[t])
    return _ZH_RUN.sub(num, s)


_DURATION = re.compile(r"^(\d*)\s*(m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days|分钟|分|小时|个小时|钟头|个钟头|天|日)$")
_MULT = {"m": 1, "h": 60, "d": 1440, "分": 1, "小": 60, "个": 60, "钟": 60, "天": 1440, "日": 1440}


def parse_duration(s: str) -> int:
    """「30m」→ 30，「2h」→ 120，「1d」→ 1440，只写单位（hour）= 1 个；中文「2分钟」「两小时」「半小时」也认。→ 分钟。"""
    t = s.strip().lower()
    if t in ("半小时", "半个小时", "半个钟头"):
        return 30
    m = _DURATION.match(zh_digits(t))
    if not m:
        raise WhenError(L(f"认不出「{s}」是多久。", f"Can't read '{s}' as a duration.") + " " + usage())
    return (int(m.group(1)) if m.group(1) else 1) * _MULT[m.group(2)[0]]


_PARTS_OF_DAY = (("早上", False), ("上午", False), ("早晨", False), ("凌晨", False), ("中午", True), ("下午", True), ("晚上", True),
                 ("傍晚", True), ("夜里", True))


def parse_clock(text: str) -> tuple[int, int] | None:
    """9am / 9:30pm / 14:00 / 7（24 小时制的钟点）/ noon / midnight；中文 9 点、9 点半、早上 9 点、下午 3 点、晚上 9:30。认不出 → None。"""
    t = text.strip().lower().replace(" ", "")
    if t in ("noon", "midday", "中午"):
        return 12, 0
    if t in ("midnight", "半夜", "午夜"):
        return 0, 0
    pm = None
    for word, is_pm in _PARTS_OF_DAY:
        if t.startswith(word):
            t, pm = t[len(word):], is_pm
            break
    t = zh_digits(t).replace("：", ":")
    if not t:
        return None
    if m := re.match(r"^(\d{1,2})(?:点|時|时)(?:(半)|(\d{1,2})分?)?$", t):
        hour, minute = int(m.group(1)), 30 if m.group(2) else int(m.group(3) or 0)
    elif m := re.match(r"^(\d{1,2})(?::(\d{2}))?(am|pm)?$", t):
        hour, minute = int(m.group(1)), int(m.group(2) or 0)
        if m.group(3):
            if not 1 <= hour <= 12:
                return None
            hour = hour % 12 + (12 if m.group(3) == "pm" else 0)
    else:
        return None
    if pm is True and hour < 12:
        hour += 12
    elif pm is False and hour == 12 and text.strip().startswith(("凌晨",)):
        hour = 0
    if hour > 23 or minute > 59:
        return None
    return hour, minute


# —— 哪几天的几点 ————————————————————————————————————————————————————————————

_EN_DAY = {"monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1, "wednesday": 2, "wed": 2, "weds": 2, "thursday": 3, "thu": 3,
           "thur": 3, "thurs": 3, "friday": 4, "fri": 4, "saturday": 5, "sat": 5, "sunday": 6, "sun": 6}
_DAY_SPEC = {"day": [], "daily": [], "everyday": [], "weekday": [0, 1, 2, 3, 4], "weekdays": [0, 1, 2, 3, 4], "weekend": [5, 6],
             "weekends": [5, 6]}
_ZH_DAY = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}


def _days_then_clock(rest: str) -> tuple[list[int], tuple[int, int]] | None:
    """「monday 9am」「weekdays at 9am」「mon, wed 18:00」「day at 21:30」→ (哪几天，空 = 每天；钟点)。Hermes 的 _natural_every_to_cron。"""
    tokens = rest.lower().replace(",", " ").split()
    if not tokens:
        return None
    days = _DAY_SPEC.get(tokens[0])
    idx = 1
    if days is None:
        days, idx = [], len(tokens)
        for i, tok in enumerate(tokens):
            if tok == "and":
                continue
            d = _EN_DAY.get(tok)
            if d is None:
                idx = i
                break
            if d not in days:
                days.append(d)
        if not days:
            return None
    clock = tokens[idx:]
    if clock and clock[0] == "at":
        clock = clock[1:]
    hm = parse_clock("".join(clock)) if clock else None
    return (sorted(days), hm) if hm else None


def _zh_days_then_clock(s: str) -> tuple[list[int], tuple[int, int]] | None:
    """「每天 9:00」「每天早上 9 点」「工作日 9:00」「周末 10 点」「每周一、三 18:00」「星期五晚上 8 点」→ (哪几天，空 = 每天；钟点)。"""
    t = s.strip().replace(" ", "")
    if m := re.match(r"^(?:每天|每日|天天)(?:的)?(.+)$", t):
        days: list[int] = []
        rest = m.group(1)
    elif m := re.match(r"^(?:每个?)?(?:工作日|周一到周五|星期一到星期五)(.+)$", t):
        days, rest = [0, 1, 2, 3, 4], m.group(1)
    elif m := re.match(r"^(?:每个?)?(?:周末|双休日)(.+)$", t):
        days, rest = [5, 6], m.group(1)
    elif m := re.match(r"^每?(?:周|星期|礼拜)([一二三四五六日天](?:[、，,和及与]?(?:周|星期|礼拜)?[一二三四五六日天])*)(.+)$", t):
        days, rest = sorted({_ZH_DAY[c] for c in m.group(1) if c in _ZH_DAY}), m.group(2)
    else:
        return None
    hm = parse_clock(rest)
    return (days if len(days) < 7 else [], hm) if hm else None


# —— parse ————————————————————————————————————————————————————————————————

_CRON_TIME = re.compile(r"^[\d*,/\-]+$")      # 分、时两段只有数字和符号（「remind me at nine pm」不会被当成 cron）
_CRON_FIELD = re.compile(r"^[A-Za-z\d*,/\-?]+$")


def parse(text: str, at: datetime | None = None) -> dict:
    """一种写法 → 存起来的样子（见文件开头）。认不出来抛 WhenError（带用法）。at = 现在（测试用假时钟）。"""
    original = " ".join(str(text or "").split())
    if not original:
        raise WhenError(L("没写什么时候。", "No time given.") + " " + usage())
    t0 = local(at) if at else now()
    s = original.lower()
    is_every = s.startswith("every ")
    rest = s[6:].strip() if is_every else s
    natural = _days_then_clock(rest) or _zh_days_then_clock(original)
    if natural:  # 每天几点 / 哪几天的几点（带不带 every 都行）
        days, (h, m) = natural
        return {"kind": "daily", "at": f"{h:02d}:{m:02d}", "days": days, "text": original}
    if is_every:
        return _every(parse_duration(rest), original)
    if s in ("hourly", "每小时", "每个小时"):
        return _every(60, original)
    if s in ("every day", "每天", "每日", "天天"):
        return _every(1440, original)
    zh = re.match(r"^每\s*(?:隔\s*)?(.+)$", original)  # 每 2 分钟、每两小时、每隔 30 分钟
    if zh and not zh.group(1).startswith(("天", "日", "周", "星期", "礼拜")):
        return _every(parse_duration(zh.group(1)), original)
    parts = original.split()
    if len(parts) == 5 and all(_CRON_TIME.match(p) for p in parts[:2]) and all(_CRON_FIELD.match(p) for p in parts[2:]):
        expr = " ".join(parts)
        cron_fields(expr)  # 写错了在这里就报
        return {"kind": "cron", "expr": expr, "text": original}
    once = _once(original, t0)
    if once is not None:
        return once
    with contextlib.suppress(WhenError):
        return _every(parse_duration(original), original)
    raise WhenError(L(f"认不出「{original}」。", f"Can't read '{original}'.") + " " + usage())


def _every(minutes: int, text: str) -> dict:
    if minutes < MIN_MINUTES:
        raise WhenError(L(f"最短每 {MIN_MINUTES} 分钟一次。", f"The shortest interval is {MIN_MINUTES} minute."))
    return {"kind": "every", "minutes": minutes, "text": text}


def _once(original: str, t0: datetime) -> dict | None:
    """某个时刻一次：ISO 时间、「in 30m」「30 分钟后」、today / tomorrow / 今天 / 今晚 / 明天 / 明早 / 后天 + 钟点。不是这类 → None。"""
    s = original.strip()
    low = s.lower()
    if re.match(r"^\d{4}-\d{2}-\d{2}", s):
        try:
            dt = datetime.fromisoformat(s)
        except ValueError as e:
            raise WhenError(L(f"时间写错了：{s}", f"Bad timestamp: {s}") + f" ({e})") from None
        return _at(local(dt), original)
    if m := re.match(r"^in\s+(.+)$", low):
        return _at(_utc_add(t0, timedelta(minutes=parse_duration(m.group(1)))), original)
    if (m := re.match(r"^(.+?)\s*(?:以后|之后|后)$", s)) and not re.search(r"[点:：]", m.group(1)):
        with contextlib.suppress(WhenError):
            return _at(_utc_add(t0, timedelta(minutes=parse_duration(m.group(1)))), original)
    if m := re.match(r"^(today|tonight|tomorrow|今天|今晚|明天|明早|后天)\s*(?:at\s+)?(.*)$", low):
        word, rest = m.group(1), m.group(2).strip()
        if word in ("今晚", "tonight") and not rest.startswith(tuple(w for w, _ in _PARTS_OF_DAY)):
            hm = parse_clock("晚上" + rest) if word == "今晚" else parse_clock(rest)
            if word == "tonight" and hm and hm[0] < 12:
                hm = (hm[0] + 12, hm[1])
        else:
            hm = parse_clock(rest)
        if not hm:
            raise WhenError(L(f"「{original}」几点？", f"What time is '{original}'?") + " " + usage())
        plus = {"today": 0, "tonight": 0, "今天": 0, "今晚": 0, "tomorrow": 1, "明天": 1, "明早": 1, "后天": 2}[word]
        d = t0.date() + timedelta(days=plus)
        return _at(datetime(d.year, d.month, d.day, hm[0], hm[1], tzinfo=settings.tz), original)
    return None


def _at(dt: datetime, text: str) -> dict:
    return {"kind": "once", "at": dt.astimezone(settings.tz).isoformat(timespec="seconds"), "text": text}


# —— cron 的五段 ————————————————————————————————————————————————————————————

_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
_DOW = {d: i for i, d in enumerate(("sun", "mon", "tue", "wed", "thu", "fri", "sat"))}
_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))


def _num(x: str, names: dict, text: str) -> int:
    if x.isdigit():
        return int(x)
    if x[:3] in names:
        return names[x[:3]]
    raise WhenError(L(f"cron 这一段写错了：{text}", f"Bad cron field: {text}") + " " + usage())


def _field(text: str, i: int) -> tuple[set[int], bool]:
    """cron 的一段 → (取值, 是不是 *)。认 * / */n / a-b / a-b/n / a,b,c，月份和星期认英文缩写。"""
    lo, hi = _RANGES[i]
    names = _MONTHS if i == 3 else _DOW if i == 4 else {}
    out: set[int] = set()
    for part in text.lower().split(","):
        step = 1
        if "/" in part:
            part, _, st = part.partition("/")
            if not st.isdigit() or int(st) < 1:
                raise WhenError(L(f"cron 这一段写错了：{text}", f"Bad cron field: {text}") + " " + usage())
            step = int(st)
        if part in ("*", "?", ""):
            a, b = lo, hi
        elif "-" in part:
            x, _, y = part.partition("-")
            a, b = _num(x, names, text), _num(y, names, text)
        else:
            a = _num(part, names, text)
            b = hi if step > 1 else a
        if not (lo <= a <= hi and lo <= b <= hi and a <= b):
            raise WhenError(L(f"cron 这一段超出范围：{text}（{lo}–{hi}）", f"Cron field out of range: {text} ({lo}-{hi})"))
        out.update(range(a, b + 1, step))
    if i == 4 and 7 in out:  # 周日也能写成 7
        out.discard(7)
        out.add(0)
    return out, text in ("*", "?")


def cron_fields(expr: str) -> tuple:
    parts = expr.split()
    if len(parts) != 5:
        raise WhenError(L("cron 要写五段：分 时 日 月 周", "A cron expression has five fields: minute hour day month weekday"))
    return tuple(_field(p, i) for i, p in enumerate(parts))


def _day_ok(fields: tuple, d: date) -> bool:
    (dom, dom_star), (mon, _), (dow, dow_star) = fields[2], fields[3], fields[4]
    if d.month not in mon:
        return False
    cron_dow = (d.weekday() + 1) % 7  # cron 里周日 = 0
    if dom_star and dow_star:
        return True
    if dom_star:
        return cron_dow in dow
    if dow_star:
        return d.day in dom
    return d.day in dom or cron_dow in dow  # 日和周都限定了：对上哪个都行（Vixie cron 的规矩）


def _wall_next(fields: tuple, base: datetime) -> datetime | None:
    """本地钟面上严格在 base 之后的下一个（按分钟）；配 fold：先 fold 0，不在 base 后面再 fold 1（Hermes 的 strictly-after guard）。"""
    hours, mins = sorted(fields[1][0]), sorted(fields[0][0])
    base_ts = base.timestamp()
    wall = base.astimezone(settings.tz).replace(tzinfo=None, second=0, microsecond=0)
    d = wall.date()
    for _ in range(366 * 5):
        if _day_ok(fields, d):
            for h in hours:
                for m in mins:
                    cand = datetime(d.year, d.month, d.day, h, m)
                    if cand <= wall:
                        continue
                    for fold in (0, 1):
                        c = cand.replace(tzinfo=settings.tz, fold=fold)
                        if c.timestamp() > base_ts:
                            return c
        d += timedelta(days=1)
    return None


def _daily_fields(sched: dict) -> tuple:
    h, m = (int(x) for x in sched["at"].split(":"))
    days = sched.get("days") or []
    dow = {(i + 1) % 7 for i in days} if days else set(range(7))
    return ({m}, False), ({h}, False), (set(range(1, 32)), True), (set(range(1, 13)), True), (dow, not days)


def _utc_add(dt: datetime, delta: timedelta) -> datetime:
    """按真实经过的时间加（Hermes 也在 UTC 里加：换夏令时那天每隔两小时还是两小时）。"""
    return (dt.astimezone(dt_timezone.utc) + delta).astimezone(settings.tz)


def next_after(sched: dict, after: datetime, anchor: datetime | None = None) -> datetime | None:
    """after 之后的下一次（严格在后面）。every 的从 anchor（上一次本该跑的时刻）往后排整数个周期，没有 anchor = after + 一个周期。
    没有下一次（一次的已经过了）→ None。"""
    kind = sched.get("kind")
    after = local(after)
    if kind == "once":
        at = local(datetime.fromisoformat(sched["at"]))
        return at if at > after else None
    if kind == "every":
        step = timedelta(minutes=int(sched["minutes"]))
        if anchor is None:
            return _utc_add(after, step)
        anchor = local(anchor)
        if anchor > after:
            return anchor
        return _utc_add(anchor, step * (int((after - anchor) / step) + 1))
    if kind == "daily":
        return _wall_next(_daily_fields(sched), after)
    if kind == "cron":
        return _wall_next(cron_fields(sched["expr"]), after)
    return None


def period_seconds(sched: dict, at: datetime | None = None) -> float | None:
    """大约多久一次（补跑窗口、一天几次用）：every 就是周期；daily / cron 看 at 之后的两次隔多久；once 没有周期。"""
    kind = sched.get("kind")
    if kind == "every":
        return float(sched["minutes"]) * 60
    if kind in ("daily", "cron"):
        a = next_after(sched, at or now())
        b = next_after(sched, a) if a else None
        return (b - a).total_seconds() if a and b else None
    return None


def grace_seconds(sched: dict, *, outage: bool = False, at: datetime | None = None) -> int:
    """晚了多久以内还算按时（见文件开头）。"""
    if sched.get("kind") == "once":
        return MAX_GRACE if outage else ONCE_GRACE
    p = period_seconds(sched, at)
    if not p:
        return MIN_GRACE
    return max(MIN_GRACE, min(int(p) // 2, MAX_GRACE))


def runs_per_day(sched: dict, at: datetime | None = None) -> float | None:
    """一天大约跑几次（写在卡上给用户看）。once → None。"""
    p = period_seconds(sched, at)
    return 86400 / p if p else None


# —— 说给人听 ——————————————————————————————————————————————————————————————

def _days_text(days: list[int]) -> str:
    if not days or len(days) == 7:
        return L("每天", "every day")
    if days == [0, 1, 2, 3, 4]:
        return L("工作日", "weekdays")
    if days == [5, 6]:
        return L("周末", "weekends")
    return L("每周" + "、".join(ZH_DAYS[d] for d in days), ", ".join(EN_DAYS[d] for d in days))


def _mins_text(n: int) -> str:
    if n % 1440 == 0:
        d = n // 1440
        return L("每天" if d == 1 else f"每 {d} 天", "every day" if d == 1 else f"every {d} days")
    if n % 60 == 0:
        h = n // 60
        return L("每小时" if h == 1 else f"每 {h} 小时", "every hour" if h == 1 else f"every {h} hours")
    return L("每分钟" if n == 1 else f"每 {n} 分钟", "every minute" if n == 1 else f"every {n} minutes")


def span_text(n: int) -> str:
    """多久：「2 分钟」「1 小时」「3 天」。"""
    if n % 1440 == 0:
        return L(f"{n // 1440} 天", f"{n // 1440} day" + ("s" if n != 1440 else ""))
    if n % 60 == 0:
        return L(f"{n // 60} 小时", f"{n // 60} hour" + ("s" if n != 60 else ""))
    return L(f"{n} 分钟", f"{n} minute" + ("s" if n != 1 else ""))


def describe(sched: dict) -> str:
    """一句人话：「每 2 分钟」「工作日 09:00」「10月17日 15:00 一次」「按 cron「0 9 * * 1-5」」。"""
    kind = sched.get("kind")
    if kind == "every":
        return _mins_text(int(sched["minutes"]))
    if kind == "daily":
        return f"{_days_text(sched.get('days') or [])} {sched['at']}"
    if kind == "once":
        at = local(datetime.fromisoformat(sched["at"]))
        return L(f"{at.month}月{at.day}日 {at:%H:%M} 一次", f"once on {at:%d %b} at {at:%H:%M}")
    if kind == "cron":
        return L(f"按 cron「{sched['expr']}」", f"cron '{sched['expr']}'")
    return str(sched.get("text") or "?")


def clock_text(dt: datetime | None, ref: datetime | None = None) -> str:
    """「14:02」「明天 09:00」「10月20日 09:00」。"""
    if dt is None:
        return L("（没有下一次）", "(none)")
    dt = local(dt)
    ref = local(ref) if ref else now()
    if dt.date() == ref.date():
        return dt.strftime("%H:%M")
    if dt.date() == ref.date() + timedelta(days=1):
        return L(f"明天 {dt:%H:%M}", f"tomorrow {dt:%H:%M}")
    return L(f"{dt.month}月{dt.day}日 {dt:%H:%M}", f"{dt:%d %b} {dt:%H:%M}")
