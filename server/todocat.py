"""待办的类别：学习 study / 生活 life / 工作 work（2026-10-15，「今天」页的三类）。

每一条待办、截止、日程都有一类，圆圈和色条按它上色，顶上能按它筛。谁说了算（先到先得）：
1. 你改过的：schedule_marks.category（app 里点开一条改类别，或 schedule_ctl.py category），以你为准
2. 写入时给的：自己的日程 schedule_items.category（schedule_ctl.py add --category），邮件条目抽取时分的（items.json 的 category）
3. 按来源：课表、课程网站的作业、学习台课程档案 → 学习；求职 / 申请表：申请学校 → 学习，其余 → 工作；
   Agent 排的按它的看板：健身、饮食、睡眠 → 生活，求职 → 工作，申请学校、学习台 → 学习（Agent 的幂等键前缀也算，比如 apply:…）
4. 按字面：标题和说明里的词（账单、退货、预约、旅行签证 → 生活；求职、面试、工签、融资 → 工作；作业、考试、推荐信 → 学习）
5. 都认不出：生活
工程（写代码、改系统、发版）不进这里，在 Claude Code 的交接本里，「今天」只给一块只读的工程看板（engboard.py）。

server.json（可选）："today": {"categories": true}：「今天」页按三类显示，过期的不再 3 天后消失（超过 7 天收成一行），能「不用管」。
不写 = 老样子（类别照样算，app 不显示）。
"""
from __future__ import annotations

import re
import sqlite3

from config import raw
from i18n import L

CATS = ("study", "life", "work")
LABEL = {"study": ("学习", "Study"), "life": ("生活", "Life"), "work": ("工作", "Work")}
DEFAULT = "life"
DASHBOARD_CAT = {"fitness": "life", "diet": "life", "health": "life", "apply": "work", "masters": "study", "study": "study"}
ORIGIN_CAT = {"calendar": "study", "canvas": "study", "course": "study"}

# 字面上的词：命中最多的那一类。签证单独看（求职签证 → 工作，其余 → 生活）。词都按小写比
WORDS = {
    "study": ("作业", "考试", "期中", "期末", "小测", "测验", "论文", "自习", "复习", "预习", "课程", "上课", "讲座", "学习台", "选课", "学分",
              "成绩", "成绩单", "推荐信", "申请学校", "读研", "硕士", "博士", "文书", "小组作业", "课件", "教材", "学校",
              "assignment*", "coursework", "homework", "problem set", "quiz*", "exam*", "midterm*", "final exam", "lecture*", "seminar*", "tutorial*",
              "office hour*", "canvas", "syllabus", "module*", "transcript*", "recommendation*", "reference letter*", "writing sample*",
              "personal statement", "statement of purpose", "master", "masters", "msc", "mfin", "meng", "mphil", "phd", "mba", "admission*", "university", "college",
              "gre", "gmat", "toefl", "ielts", "essay*",
              "reading*", "course*", "class*", "study", "school*"),
    "work": ("求职", "实习", "全职", "岗位", "职位", "面试", "笔试", "测评", "网申", "简历", "招聘", "校招", "内推", "雇主", "职业", "猎头",
             "薪资", "融资", "投资人", "路演", "商业计划", "商标", "律师", "授权", "发帖", "宣传", "高才通", "工签", "人才",
             "offer*", "cv", "cover letter*", "career*", "job*", "intern*", "graduate scheme*", "graduate programme*", "interview*", "assessment*",
             "recruit*", "networking", "coffee chat*", "linkedin", "fellowship*", "investor*", "pitch*", "fundrais*", "vc", "bp", "openmousse",
             "trademark*", "cla", "launch*", "hiring"),
    "life": ("账单", "扣费", "扣款", "付款", "缴费", "续费", "自动续费", "订阅", "退款", "退货", "寄回", "快递", "包裹", "预约", "体检", "医生",
             "看病", "牙", "银行", "房租", "租房", "水电", "保险", "机票", "航班", "酒店", "火车", "行程", "旅行", "演唱会", "门票", "演出",
             "电影", "健身", "训练", "饮食", "体重", "体脂", "睡眠", "购物", "买", "路由器", "手机", "话费", "生日", "父母", "家人", "理发",
             "搬家", "驾照", "护照", "居留", "里程", "会员", "账户", "账号", "密码", "邮箱",
             "bill*", "invoice*", "charge*", "payment*", "renew*", "subscription*", "refund*", "return*", "parcel*", "delivery", "appointment*",
             "doctor*", "dentist*", "gp", "nhs", "bank*", "rent", "council tax", "insurance", "flight*", "hotel*", "train", "trip*", "travel*",
             "concert*", "ticket*", "gym", "workout*", "passport*", "brp", "router*", "phone*", "birthday*", "account*", "password*"),
}
WORK_VISA = re.compile(r"高才通|工签|人才签|求职签|工作签|work visa|graduate visa|hpi|skilled worker|j-?find", re.I)
VISA = re.compile(r"签证|visa", re.I)
ASCII_WORD = re.compile(r"[a-z]")


def enabled() -> bool:
    """server.json 的 today.categories：「今天」页按三类显示、过期的留着、能「不用管」。"""
    t = raw().get("today")
    return isinstance(t, dict) and bool(t.get("categories"))


def label(cat: str | None) -> str:
    return L(*LABEL[cat]) if cat in LABEL else ""


def check(v: str | None) -> str | None:
    """校验写进来的类别：study / life / work；空 = 清掉（回到自动分）。中文名也认。"""
    if v is None:
        return None
    s = str(v).strip().lower()
    if not s or s in ("auto", "none", "自动"):
        return None
    for k, (zh, en) in LABEL.items():
        if s in (k, zh, en.lower()):
            return k
    raise ValueError(s)


def has_word(text: str, w: str) -> bool:
    """中文按子串；英文按整词（可以带复数 s：master 不命中 Mastercard，cla 不命中 class），后面带 * 的是词根（intern* 命中 internship）。"""
    if not ASCII_WORD.search(w):
        return w in text
    stem = w.endswith("*")
    w = w.rstrip("*")
    tail = "" if stem else r"(?:s|es)?(?![a-z])"
    return re.search(rf"(?<![a-z]){re.escape(w)}{tail}", text) is not None


def guess(*texts: str | None) -> str | None:
    """按字面猜：命中词最多的一类；一样多按 工作 > 学习 > 生活（工作和学习的词更专门）。签证：求职签证算工作，其余算生活。"""
    text = " ".join(t for t in texts if t).lower()
    if not text:
        return None
    if WORK_VISA.search(text):
        return "work"
    if VISA.search(text):
        return "life"
    score = {c: sum(1 for w in ws if has_word(text, w)) for c, ws in WORDS.items()}
    best = max(score.values())
    if not best:
        return None
    return next(c for c in ("work", "study", "life") if score[c] == best)


def agent_cats(conn: sqlite3.Connection) -> dict[str, str]:
    """Agent id → 类别（按它的看板）。没有 groups 表的新实例：空。"""
    try:
        rows = conn.execute("SELECT id, dashboard FROM groups").fetchall()
    except sqlite3.Error:
        return {}
    out = {}
    for r in rows:
        c = DASHBOARD_CAT.get(r["dashboard"] or "") or DASHBOARD_CAT.get(r["id"])
        if c:
            out[r["id"]] = c
    return out


def of_entry(e: dict, override: str | None, agents: dict[str, str]) -> str:
    """一条（schedule.entry 的样子）属于哪一类。e["category"] 是写入时给的（可能没有）。"""
    if override in CATS:
        return override
    if e.get("category") in CATS:
        return e["category"]
    o = e.get("origin")
    if o in ORIGIN_CAT:
        return ORIGIN_CAT[o]
    if o == "apply":
        return "study" if e.get("by") == "masters" else "work"
    if o in ("own", "inbox"):
        key = str(e.get("key") or "")
        prefix = key.split(":", 1)[0] if ":" in key else ""
        for who in (prefix, e.get("by")):
            if who and who in agents:
                return agents[who]
    return guess(e.get("title"), e.get("detail"), e.get("note")) or DEFAULT


def annotate(entries: list[dict], marks: dict[str, dict], conn: sqlite3.Connection | None = None, agents: dict[str, str] | None = None) -> None:
    """给每一条写上 category（和 categoryAuto：没被你改过时自动分的那一类，app 详情页「改回自动」用）。在锁里调（要读 groups）。"""
    if agents is None:
        agents = agent_cats(conn) if conn is not None else {}
    for e in entries:
        auto = of_entry(e, None, agents)
        mine = (marks.get(e.get("id") or "") or {}).get("category")
        e["category"] = mine if mine in CATS else auto
        e["categoryAuto"] = auto
        e["categorySet"] = mine in CATS
