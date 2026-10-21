"""工程看板（2026-10-15，「今天」页报告里一张卡 + 点进去的看板页）：工程的事不进待处理，手机上只看做了哪些、没做哪些、进度到哪。

两种来源，都只读（拍板、动手、改状态都在电脑上的 Claude Code 里）：交接本 todos.md，或 GitHub 的看板（2026-10-19，见后面）。

交接本：数据从 Claude Code 的交接本 todos.md 单向同步过来，
记进 mousse.db 的 eng_lines / eng_items：整节挪进 archive 以后，记过的「做完了」照样看得到。
- 一条线 = todos.md 的一节（## 标题）。名字取标题里括号和冒号前面那段；主线（标题以「主线」开头）按节里的粗体小标题分格。
- 一条事 = 一个勾选框（- [ ] / - [x]）。状态：[x] = 做完；没打勾、写着要你的（你 = server.json 的 user_name：等你、<名字> 定 / 选 / 看、
  拍板 ·、你做 ·……）= 等你；
  其余没打勾 = 没做；写着「→ 搬到…」的（挪到别处了）和「不做了」的不算。
- 线的状态：节里写了「状态：在做 / 等你 / 排队 / 做完」就按它；否则全做完 = 做完，写着「从这里开始」= 在做，有等你的 = 等你，其余 = 排队。
- 做完的日子：第一次看到它打了勾时，条目里写的最近的日子（「10/15 核对：…」），没写就用那一节标题里的日子，再没有用文件的修改日子；
  上次同步时还没打勾的，不早于上次同步那天。「这周做完」= 周一以来做完的。
- 什么时候同步：读看板或「今天」页时看文件的修改时间，变了才重读（不加定时器）。

GitHub 看板（GitHub Projects）：一条线 = 看板上「线」字段的一个选项（照选项的顺序），一条事 = 看板上的一件（标题、Status、
下一步、证据、更新）。起子进程跑 `gh api graphql`（用这台机器上 gh 登录的账号，令牌不经过这里），读一次管 GH_TTL_S 秒；
读不了就用上一次读到的（重启以后用库里存的 eng_gh），error 里写明给的是几点的。Status 六格对上 app 认的几种：
- 等你 → 等你。做完 → 做完：做完的日子 = 「更新」那天（没填用 GitHub 记的改动时间），「这周做完」= 周一以来的。
- 在做、等上线、排队 → 没做（三个数里的「没做」）。等上线算在做（线的状态、看板的「在做」列），标题前加「等上线 · 」。
- 以后 → 不算进「没做」的数，看板上单独一列，放在排队后面；一条线只剩以后的（和早于 7 天做完的），收进「更早的线」
  （app 本来就折起来的那一段），状态写排队。以后是先放着、不在手上的，算进「没做」数就虚高；app 的看板列是服务器给几列画几列，
  多一列不用改 app。
- 线的状态：有在做 / 等上线的 = 在做，否则有等你的 = 等你，有排队 / 以后的 = 排队，全做完 = 做完；做完 / 总数把以后的也算上。
  点开一条线，没做的那一组照 在做 → 等上线 → 排队 → 以后 排，标题前写着是哪一格。
- 认不出的 Status（自己加的选项）、没填的，算排队；GitHub 新建看板默认的 Todo / In Progress / Done 也认。
- 主线：看板上没有，可以另给一个 JSON 文件（mainline）：[{"name", "state", "note", "line"（可选）}]。每一步对上一条线
  （按 line；不写就按名字，一头是另一头的开头也算），格子 = 那条线的每一件，做完的在前；对不上的一步只有一格（state 是做完才算做完）。
  mainNote = 第一个 state 写着在做的那一步的 note。

server.json：
  "today": {"engineering": {"todos": "交接本的路径", "archive": "归档文件的 glob（可选，整节挪走的也算进做完）"}}
  "today": {"engineering": {"source": "github", "owner": "看板的主人（GitHub 用户或组织）", "number": 看板编号,
            "mainline": "主线 JSON 的路径（可选）", "gh": "gh 命令的路径（可选，默认在 PATH 里找）",
            "fields": {"line": "线", "who": "谁做", "next": "下一步", "evidence": "证据", "updated": "更新", "status": "Status"}（字段名不一样才写）,
            "statuses": {"看板上的选项": "wait / doing / ship / queue / later / done"}（选项名不一样才写）}}
不写 = 没有工程看板（OpenMousse 默认）。写了 source = github 就不读 todos（todos 留着也行：删掉 source 就回到交接本）。

GET /api/today/eng             看板页：三个数、主线分格、各条线、几列（交接本：没做 / 等你 / 这周做完；GitHub：等你 / 在做 / 排队 / 以后 / 这周做完）
GET /api/today/eng/line/{id}   点开一条线：没做的、等你的、做完的
"""
from __future__ import annotations

import difflib
import functools
import glob
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, HTTPException

from chat import _lock, db, now_iso
from config import TZ, raw
from i18n import L

router = APIRouter()

RECENT_DAYS = 7          # 「各条线」里直接列哪些：标题里的日子在 7 天内的、在做的；其余收进「更早的线」
CARD_LIMIT = 200         # 每一列最多给多少张
STATES = ("done", "todo", "wait", "moved", "dropped", "archived")

CHECK = re.compile(r"^(\s*)[-*+]\s+\[([ xX])\]\s+(.*)$")
HEADING = re.compile(r"^(#{2,3})\s+(.+?)\s*#*\s*$")
BOLD_HEAD = re.compile(r"^\*\*(.+?)\*\*")
STATUS_LINE = re.compile(r"^>?\s*\**状态\**\s*[:：]\s*\**(在做|等你|排队|做完|暂停)")
START_HERE = re.compile(r"(下一个|下个) ?session 从这里开始|在做|进行中")
START_HERE_BODY = re.compile(r"(下一个|下个) ?session 从这里开始")
# 「→ 已搬到日程（item:…）」「→ 剩下的已搬到…」「→ 已在日程里」「→ 不搬：…」= 挪到别处或别处已经有了；写在括号里的（「（提醒这一半 → 已搬到…）」）
# 只说了一半，这一条还算开着
MOVED = re.compile(r"→[^→（(]{0,8}?(已在|不搬|已?(搬|挪|移|并)(到|进))")
DROPPED_NOTE = re.compile(r"[（(]\s*(\d{1,2}/\d{1,2}\s*核对\s*[:：]\s*)?不做了")  # 「（不做了：…）」「（10/15 核对：不做了，…）」
# 「写着要你」（名字是 server.json 的 user_name）：等 <名字> / 等你 / <名字> 定、选、看、点、拍板、决定、确认、动手的 / 要 <名字> / 问 <名字> /
# 拍板 · / 你做 ·。括号里的补充、「」里引的原话不算；「上线前先问 <名字>」这种做事的规矩也不算
WAIT_TPL = (r"等\s*({who}|你)|(要|问|让|请)\s*({who})|({who})\s*(来|自己|亲自)?\s*(定|选|看|点|拍板|决定|确认|真机|手动|手机|登录|装|开|批|同意|"
            r"回复|答|填|签|在|用)(?![了过])|^({who})\s*[:：]|^(拍板|你做)\s*·|({who}) 的事|只有 ({who})")
# 已经定过的不算：「10/8 Alex 定「…」」「10/15 03:5x Alex 选了…」（Alex 是 server.json 的 user_name）
DECIDED_TPL = r"\d{{1,2}}/\d{{1,2}}(\s*\d{{1,2}}:\d{{1,2}}x?)?\s*({who})\s*(定|选|说|点|看|同意|拍板)"
PROCEDURE_TPL = r"(之前|以前|前|先)\s*(用选项)?(问|告诉)\s*({who})"
QUOTE = re.compile(r"「[^「」]*」")
DATE_MD = re.compile(r"(?<![\d/.])(1[0-2]|0?[1-9])/(3[01]|[12]\d|0?[1-9])(?![\d/])")
DATE_ISO = re.compile(r"(20\d\d)-(1[0-2]|0[1-9])-(3[01]|[12]\d|0[1-9])")
PARENS = re.compile(r"[（(][^（）()]*[）)]")
CJK = re.compile(r"[\u4e00-\u9fff]")
MD = re.compile(r"\*\*|__|`|~~")
LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
URL = re.compile(r"https?://\S+")

_sync_lock = threading.Lock()


# —— 配置 ——————————————————————————————————————————————————————————————

def cfg() -> dict:
    """配好了的 today.engineering（没配、少了要写的 = {}）。"""
    t = raw().get("today")
    c = t.get("engineering") if isinstance(t, dict) else None
    if not isinstance(c, dict):
        return {}
    if is_github(c):
        return c if c.get("owner") and c.get("number") else {}
    return c if c.get("todos") else {}


def is_github(c: dict | None = None) -> bool:
    """来源是 GitHub 看板（source = github）。"""
    c = cfg() if c is None else c
    return str(c.get("source") or "").strip().lower() == "github"


def source_files() -> list[tuple[str, Path]]:
    """[(kind, 路径)]：交接本在前（todos），归档的在后（archive）。来源是 GitHub 看板时没有文件。"""
    c = cfg()
    if not c or is_github(c):
        return []
    out = [("todos", Path(str(c["todos"])).expanduser())]
    pat = c.get("archive")
    if pat:
        out += [("archive", Path(p)) for p in sorted(glob.glob(str(Path(str(pat)).expanduser())))]
    return out


# —— 解析 ——————————————————————————————————————————————————————————————

@dataclass
class PItem:
    text: str
    state: str
    group: str
    pos: int


@dataclass
class PLine:
    heading: str
    name: str
    pos: int
    main: bool
    status: str | None = None     # 节里写明的状态（在做 / 等你 / 排队 / 做完 / 暂停）
    start_here: bool = False
    day: date | None = None
    items: list[PItem] = field(default_factory=list)


def plain(s: str) -> str:
    """去掉 markdown 的强调、代码、链接，空白并成一个。"""
    s = LINK.sub(r"\1", s)
    s = MD.sub("", s)
    return " ".join(s.split())


def line_name(heading: str) -> str:
    """节标题 → 线的名字：去掉「下一个 session 从这里开始：」这类前缀，取括号前面那段；冒号前面够长（4 个字以上）就只取冒号前面。"""
    h = plain(heading)
    h = re.sub(r"^(下一个|下个)\s*session\s*从这里开始[:：]\s*", "", h)
    h = re.sub(r"^\d{4}-\d{2}-\d{2}\s*新增[:：]\s*", "", h)
    h = re.split(r"[（(]", h, maxsplit=1)[0].strip()
    m = re.search(r"[:：]", h)
    if m and len(CJK.findall(h[:m.start()])) >= 4:  # 冒号前是一个够说明白的中文名（「测试设备分工：平板、…」）；「上架：Acme」「Zen：…」整个留着
        h = h[:m.start()].strip()
    return h[:40] or plain(heading)[:40]


def to_day(m: int, d: int, today: date) -> date | None:
    """「10/15」→ 今年的那天；比今天晚半年以上的算去年的（1 月看到「12/6」是去年的；10 月看到「12/6」是今年的）。"""
    try:
        x = date(today.year, m, d)
    except ValueError:
        return None
    return x.replace(year=x.year - 1) if x > today + timedelta(days=183) else x


def days_in(text: str, today: date) -> list[date]:
    out = []
    for m in DATE_ISO.finditer(text):
        try:
            out.append(date(int(m.group(1)), int(m.group(2)), int(m.group(3))))
        except ValueError:
            pass
    for m in DATE_MD.finditer(DATE_ISO.sub(" ", text)):
        x = to_day(int(m.group(1)), int(m.group(2)), today)
        if x:
            out.append(x)
    return out


def owner_names() -> tuple[str, ...]:
    """交接本里怎么称呼你：server.json 的 user_name（没设就只认「你」）。"""
    n = str(raw().get("user_name") or "").strip()
    return (n,) if n else ()


@functools.lru_cache(maxsize=8)
def patterns(names: tuple[str, ...]) -> tuple[re.Pattern, re.Pattern, re.Pattern, re.Pattern]:
    """(写着要你, 做事的规矩, 开头的「<名字>：」, 带日子的「已经定过」)。"""
    who = "|".join(re.escape(n) for n in names) or r"(?!)"
    return (re.compile(WAIT_TPL.format(who=who)), re.compile(PROCEDURE_TPL.format(who=who)), re.compile(rf"^({who})\s*[:：]\s*"),
            re.compile(DECIDED_TPL.format(who=who)))


def strip_parens(t: str) -> str:
    while True:
        t2 = PARENS.sub("", t)
        if t2 == t:
            return t
        t = t2


def item_state(checked: bool, text: str, names: tuple[str, ...] | None = None) -> str:
    """「」里引的字不算（「搬完写『→ 已搬到…』」说的是做法，不是它自己搬走了）。"""
    if checked:
        return "done"
    t = QUOTE.sub("", plain(text))
    if MOVED.search(strip_parens(t)):
        return "moved"
    if text.strip().startswith("~~") or "不做了" in strip_parens(t) or DROPPED_NOTE.search(t):
        return "dropped"
    wait, procedure, _, decided = patterns(owner_names() if names is None else names)
    core = decided.sub("", procedure.sub("", strip_parens(t)))
    return "wait" if wait.search(core) else "todo"


def parse(text: str, today: date, names: tuple[str, ...] | None = None) -> list[PLine]:
    """一份 markdown → 各节和里面的勾选框。代码块里的不算；### 小标题和行首的粗体小标题当分格（主线用）。names = 交接本里怎么称呼你。"""
    names = owner_names() if names is None else names
    lines: list[PLine] = []
    cur: PLine | None = None
    group = ""
    fenced = False
    n = 0
    for raw_line in text.splitlines():
        if raw_line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            continue
        h = HEADING.match(raw_line)
        if h and len(h.group(1)) == 2:
            heading = h.group(2).strip()
            name = line_name(heading)
            ds = [x for x in days_in(heading, today) if x <= today]
            cur = PLine(heading=heading, name=name, pos=len(lines), main=name.startswith("主线"),
                        start_here=bool(START_HERE.search(plain(heading))), day=max(ds) if ds else None)
            lines.append(cur)
            group = ""
            continue
        if cur is None:
            continue
        if h:  # ### 小标题
            group = plain(h.group(2))
            continue
        s = raw_line.strip()
        st = STATUS_LINE.match(s)
        if st and cur.status is None:
            cur.status = st.group(1)
            continue
        if START_HERE_BODY.search(s) and ("**" in s or s.startswith(">")):
            cur.start_here = True
        b = BOLD_HEAD.match(s)
        if b and not CHECK.match(raw_line) and raw_line == raw_line.lstrip():
            group = plain(b.group(1))
            continue
        c = CHECK.match(raw_line)
        if c:
            body = c.group(3).strip()
            cur.items.append(PItem(text=body, state=item_state(c.group(2) != " ", body, names), group=group, pos=n))
            n += 1
    return lines


def short_title(text: str) -> str:
    """一条事在卡片上的标题：去掉 markdown、括号里的补充、「拍板 ·」这类前缀，取第一句。"""
    t = strip_parens(URL.sub("", plain(text)))
    t = re.sub(r"^\d{1,2}\s+(?=\S)", "", t.strip())                 # 主线的「1 第 0 步…」
    t = re.sub(r"^(拍板|你做|CC)\s*·\s*", "", t)
    t = patterns(owner_names())[2].sub("", t)
    t = re.sub(r"^✅\s*", "", t)
    t = re.split(r"[；;。→]", t, maxsplit=1)[0]
    head, sep, _ = t.partition("：")
    if sep and (len(head.strip()) >= 6 or len(t) > 36 and len(head.strip()) >= 4):  # 「① 服务器：待办带类别…」这种冒号前太短的留着后面
        t = head
    if len(t) > 40:
        cut = re.search(r"[，,]", t[12:])
        if cut:
            t = t[:12 + cut.start()]
    t = " ".join(t.split()).strip(" ·:：,，")
    return t if len(t) <= 44 else t[:42] + "…"


def norm(text: str) -> str:
    return re.sub(r"\s+", "", plain(text)).lower()


# —— 同步 ——————————————————————————————————————————————————————————————

SCHEMA = """
CREATE TABLE IF NOT EXISTS eng_lines (id TEXT PRIMARY KEY, name TEXT NOT NULL, heading TEXT NOT NULL, file TEXT NOT NULL, pos INTEGER NOT NULL,
    main INTEGER NOT NULL DEFAULT 0, status TEXT, start_here INTEGER NOT NULL DEFAULT 0, day TEXT, first_seen TEXT NOT NULL,
    updated_at TEXT NOT NULL, gone_at TEXT);
CREATE TABLE IF NOT EXISTS eng_items (id TEXT PRIMARY KEY, line TEXT NOT NULL, grp TEXT NOT NULL DEFAULT '', text TEXT NOT NULL,
    state TEXT NOT NULL, done_at TEXT, pos INTEGER NOT NULL, first_seen TEXT NOT NULL, updated_at TEXT NOT NULL, gone_at TEXT);
CREATE INDEX IF NOT EXISTS eng_items_line ON eng_items(line);
CREATE TABLE IF NOT EXISTS eng_sync (file TEXT PRIMARY KEY, sig TEXT NOT NULL, synced_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS eng_gh (key TEXT PRIMARY KEY, data TEXT NOT NULL, fetched_at TEXT NOT NULL);
"""


_ready = False


def edb() -> sqlite3.Connection:
    global _ready
    conn = db()
    if not _ready:
        conn.executescript(SCHEMA)
        _ready = True
    return conn


def line_id(name: str, taken: set[str]) -> str:
    base = hashlib.sha1(name.encode("utf8")).hexdigest()[:12]
    lid, i = base, 2
    while lid in taken:
        lid = f"{base}-{i}"
        i += 1
    taken.add(lid)
    return lid


def match_items(old: list[sqlite3.Row], new: list[PItem]) -> dict[int, sqlite3.Row]:
    """新解析出来的条目对上库里原来的（同一条线里）。打勾时常在后面补一句证据（多在括号里），所以依次认：原文一样 → 去掉括号一样 →
    一个是另一个的开头 → 长得像（相似度 0.72 以上）。"""
    out: dict[int, sqlite3.Row] = {}
    free = {r["id"]: r for r in old}

    def core(t: str) -> str:
        return norm(strip_parens(plain(t)))

    stages = (
        lambda a, b: norm(a) == norm(b),
        lambda a, b: core(a) == core(b),
        lambda a, b: min(len(core(a)), len(core(b))) >= 6 and (core(a).startswith(core(b)) or core(b).startswith(core(a))),
    )
    for same in stages:
        for i, p in enumerate(new):
            if i in out:
                continue
            hit = next((r for r in free.values() if same(r["text"], p.text)), None)
            if hit is not None:
                out[i] = free.pop(hit["id"])
    for i, p in enumerate(new):
        if i in out or not free:
            continue
        k = core(p.text)[:120]
        best, score = None, 0.0
        for r in free.values():
            sc = difflib.SequenceMatcher(None, core(r["text"])[:120], k).ratio()
            if sc > score:
                best, score = r, sc
        if best is not None and score >= 0.72:
            out[i] = free.pop(best["id"])
    return out


def file_sig(p: Path) -> str | None:
    try:
        st = p.stat()
    except OSError:
        return None
    return f"{st.st_mtime_ns}:{st.st_size}"


def sync(force: bool = False) -> str | None:
    """文件改了就重读（没改什么都不做）。返回出错原因（None = 好的）。一次只跑一个。"""
    files = source_files()
    if not files:
        return None
    with _sync_lock:
        sigs = {str(p): file_sig(p) for _, p in files}
        if sigs.get(str(files[0][1])) is None:
            return L(f"读不到 {files[0][1]}", f"Can't read {files[0][1]}")
        current = {f: s for f, s in sigs.items() if s is not None}
        with _lock, edb() as conn:
            seen = {r["file"]: r["sig"] for r in conn.execute("SELECT file, sig FROM eng_sync")}
        if not force and seen == current:
            return None
        today = datetime.now(TZ).date()
        parsed: list[tuple[str, Path, list[PLine], date]] = []
        for kind, p in files:
            try:
                text = p.read_text(encoding="utf8")
            except (OSError, UnicodeError) as e:
                if kind == "todos":  # 交接本读不了：这次什么都不改（不然整块看板会被当成删光了）
                    return L(f"读不到 {p}：{e}", f"Can't read {p}: {e}")[:200]
                continue
            mday = datetime.fromtimestamp(p.stat().st_mtime, TZ).date()
            parsed.append((kind, p, parse(text, today), mday))
        with _lock, edb() as conn:
            apply_parse(conn, parsed, today)
            ts = now_iso()
            conn.execute("DELETE FROM eng_sync")
            for f, s in current.items():
                conn.execute("INSERT INTO eng_sync(file, sig, synced_at) VALUES(?,?,?)", (f, s, ts))
    return None


def last_sync_day(conn: sqlite3.Connection) -> date | None:
    r = conn.execute("SELECT MAX(synced_at) FROM eng_sync").fetchone()
    try:
        return datetime.fromisoformat(r[0]).astimezone(TZ).date() if r and r[0] else None
    except ValueError:
        return None


def apply_parse(conn: sqlite3.Connection, parsed: list[tuple[str, Path, list[PLine], date]], today: date) -> None:
    ts = now_iso()
    prev_day = last_sync_day(conn)
    old_lines = {r["id"]: r for r in conn.execute("SELECT * FROM eng_lines")}
    by_name: dict[str, list[str]] = {}
    for lid, r in old_lines.items():
        by_name.setdefault(r["name"], []).append(lid)
    taken: set[str] = set()
    present: set[str] = set()
    names_now = {pl.name for _, _, plines, _ in parsed for pl in plines}
    # 这次一个名字都对不上的老线：标题改了名的一节，按里面的条目认回来（不然改个名，做完的就算了两遍）
    vanished = {lid: conn.execute("SELECT * FROM eng_items WHERE line=?", (lid,)).fetchall()
                for lid, r in old_lines.items() if r["name"] not in names_now}
    for kind, path, plines, mday in parsed:
        for pl in plines:
            lid = next((x for x in sorted(by_name.get(pl.name, [])) if x not in taken), None) or renamed(vanished, pl, taken) \
                or line_id(pl.name, taken | set(old_lines))
            taken.add(lid)
            present.add(lid)
            archived = kind == "archive"
            conn.execute("INSERT INTO eng_lines(id, name, heading, file, pos, main, status, start_here, day, first_seen, updated_at, gone_at) "
                         "VALUES(?,?,?,?,?,?,?,?,?,?,?,NULL) ON CONFLICT(id) DO UPDATE SET name=excluded.name, heading=excluded.heading, "
                         "file=excluded.file, pos=excluded.pos, main=excluded.main, status=excluded.status, start_here=excluded.start_here, "
                         "day=excluded.day, updated_at=excluded.updated_at, gone_at=NULL",
                         (lid, pl.name, pl.heading, kind if not archived else f"archive:{path.name}", pl.pos if not archived else 100000 + pl.pos,
                          int(pl.main), pl.status, int(pl.start_here), pl.day.isoformat() if pl.day else None, ts, ts))
            olds = conn.execute("SELECT * FROM eng_items WHERE line=?", (lid,)).fetchall()
            hits = match_items(olds, pl.items)
            kept = set()
            for i, it in enumerate(pl.items):
                state = "archived" if archived and it.state in ("todo", "wait") else it.state
                old = hits.get(i)
                done_at = old["done_at"] if old is not None and old["state"] == "done" and state == "done" else None
                if state == "done" and not done_at:
                    ds = [x for x in days_in(it.text, today) if x <= today]
                    d = max(ds) if ds else (pl.day or mday)
                    if old is not None and prev_day and old["state"] in ("todo", "wait"):
                        d = max(d, prev_day)  # 上次同步时还没打勾：是那之后做完的
                    done_at = d.isoformat()
                if old is not None:
                    iid = old["id"]
                    conn.execute("UPDATE eng_items SET grp=?, text=?, state=?, done_at=?, pos=?, updated_at=?, gone_at=NULL WHERE id=?",
                                 (it.group, it.text, state, done_at, it.pos, ts, iid))
                else:
                    iid = "ei-" + hashlib.sha1(f"{lid}|{it.pos}|{it.text}|{ts}".encode("utf8")).hexdigest()[:12]
                    conn.execute("INSERT INTO eng_items(id, line, grp, text, state, done_at, pos, first_seen, updated_at, gone_at) "
                                 "VALUES(?,?,?,?,?,?,?,?,?,NULL)", (iid, lid, it.group, it.text, state, done_at, it.pos, ts, ts))
                kept.add(iid)
            for r in olds:
                if r["id"] not in kept and not r["gone_at"]:
                    conn.execute("UPDATE eng_items SET gone_at=? WHERE id=?", (ts, r["id"]))
    for lid, r in old_lines.items():
        if lid not in present and not r["gone_at"]:
            conn.execute("UPDATE eng_lines SET gone_at=? WHERE id=?", (ts, lid))
            conn.execute("UPDATE eng_items SET gone_at=? WHERE line=? AND gone_at IS NULL", (ts, lid))


def renamed(vanished: dict[str, list[sqlite3.Row]], pl: PLine, taken: set[str]) -> str | None:
    """新名字的一节是不是哪条消失的老线改了名：一半以上的条目对得上就算同一条。"""
    best, score = None, 0
    for lid, olds in vanished.items():
        if lid in taken or not olds or not pl.items:
            continue
        n = len(match_items(olds, pl.items))
        if n * 2 >= min(len(olds), len(pl.items)) and n > score:
            best, score = lid, n
    return best


# —— 算 ————————————————————————————————————————————————————————————————

STATUS_WORD = {"在做": "doing", "等你": "wait", "排队": "queue", "做完": "done", "暂停": "queue"}
STATUS_LABEL = {"doing": ("在做", "Doing"), "wait": ("等你", "Waiting on you"), "queue": ("排队", "Queued"), "done": ("做完", "Done")}


def week_start(today: date) -> date:
    return today - timedelta(days=today.weekday())


def counted(r: sqlite3.Row) -> bool:
    return r["state"] in ("done", "todo", "wait")


def line_status(line: sqlite3.Row, items: list[sqlite3.Row], today: date) -> str:
    """节里写明的为准；全做完 = 做完；最近两天写着「从这里开始」、还有 Claude Code 的活 = 在做；有等你的 = 等你；其余 = 排队。
    （不按「最近做完过一条」算在做：整理交接本时补勾的旧条目会把老线都算成在做。）"""
    if line["status"] in STATUS_WORD:
        return STATUS_WORD[line["status"]]
    live = [r for r in items if counted(r)]
    if live and all(r["state"] == "done" for r in live):
        return "done"
    if line["file"] != "todos" or line["gone_at"]:
        return "done"
    recent = (today - timedelta(days=1)).isoformat()
    has_todo = any(r["state"] == "todo" for r in live)
    if line["start_here"] and (line["day"] or "") >= recent and has_todo:
        return "doing"
    if any(r["state"] == "wait" for r in live):
        return "wait"
    return "queue"


def main_name(group: str) -> str:
    """「第一步：内核全部做完（约 10–12 个 session）」→「内核」。"""
    g = re.split(r"[（(]", group, maxsplit=1)[0]
    g = re.sub(r"^第[一二三四五六七八九十\d]+步\s*[:：]\s*", "", g)
    g = re.sub(r"(全部)?做完$", "", g.strip())
    return g.strip() or group[:12]


def load(conn: sqlite3.Connection) -> tuple[list[sqlite3.Row], dict[str, list[sqlite3.Row]]]:
    """线和条目。还在的线里被删掉 / 改得认不出的条目不算（改写过的那条以新的为准）；整节挪走了的线，做完的照样算。"""
    lines = conn.execute("SELECT * FROM eng_lines ORDER BY pos").fetchall()
    gone_line = {r["id"] for r in lines if r["gone_at"]}
    items: dict[str, list[sqlite3.Row]] = {}
    for r in conn.execute("SELECT * FROM eng_items ORDER BY pos"):
        if r["gone_at"] and not (r["line"] in gone_line and r["state"] == "done"):
            continue
        items.setdefault(r["line"], []).append(r)
    return lines, items


def board(full: bool = True) -> dict:
    """看板页要的全部：三个数、主线、各条线、三列。full=False 只要卡片上那几样（「今天」报告卡）。"""
    today = datetime.now(TZ).date()
    monday = week_start(today).isoformat()
    with _lock, edb() as conn:
        lines, items = load(conn)
        synced = conn.execute("SELECT MAX(synced_at) FROM eng_sync").fetchone()[0]
    names = {r["id"]: r["name"] for r in lines}
    live_lines = {r["id"] for r in lines if not r["gone_at"]}
    todo, wait, done_week = [], [], []
    for lid, rs in items.items():
        for r in rs:
            if r["state"] == "done":
                if (r["done_at"] or "") >= monday:
                    done_week.append(r)
            elif lid in live_lines and not r["gone_at"] and r["state"] in ("todo", "wait"):
                (wait if r["state"] == "wait" else todo).append(r)
    main_line = next((r for r in lines if r["main"] and not r["gone_at"]), None)
    main = []
    note = ""
    if main_line is not None:
        groups: dict[str, list[sqlite3.Row]] = {}
        for r in items.get(main_line["id"], []):
            if counted(r) and not r["gone_at"]:
                groups.setdefault(r["grp"] or "", []).append(r)
        for g, rs in groups.items():
            if not g:
                continue
            d = sum(1 for r in rs if r["state"] == "done")
            main.append({"name": main_name(g), "done": d, "total": len(rs), "cells": [r["state"] == "done" for r in rs]})
            nxt = next((r for r in rs if r["state"] != "done"), None)
            if nxt is not None and not note:
                note = L(f"{main_name(g)}下一步：{short_title(nxt['text'])}", f"Next in {main_name(g)}: {short_title(nxt['text'])}")
    summary = {"weekDone": len(done_week), "todo": len(todo), "wait": len(wait), "main": [{k: m[k] for k in ("name", "done", "total")} for m in main],
               "syncedAt": synced, "syncedLabel": synced_label(synced, today)}
    if not full:
        return summary
    recent_from = (today - timedelta(days=RECENT_DAYS)).isoformat()
    out_lines, older = [], []
    for r in lines:
        if r["main"]:
            continue
        rs = [x for x in items.get(r["id"], []) if counted(x) and (not x["gone_at"] or x["state"] == "done")]
        if not rs:
            continue
        st = line_status(r, rs, today)
        d = sum(1 for x in rs if x["state"] == "done")
        one = {"id": r["id"], "name": r["name"], "heading": r["heading"], "done": d, "total": len(rs), "status": st,
               "statusLabel": L(*STATUS_LABEL[st]), "day": r["day"], "archived": r["file"] != "todos" or bool(r["gone_at"])}
        recent = not one["archived"] and (st == "doing" or (r["day"] or "") >= recent_from)
        (out_lines if recent else older).append(one)
    older.sort(key=lambda x: x["day"] or "", reverse=True)

    def card(r: sqlite3.Row) -> dict:
        return {"id": r["id"], "title": short_title(r["text"]), "tags": [{"text": names.get(r["line"], ""), "tone": "neutral"}],
                "data": {"text": plain(r["text"]), "line": names.get(r["line"], ""), "doneAt": r["done_at"]}, "display": {}}

    def column(key: str, label: str, color: int | None, rows: list[sqlite3.Row]) -> dict:
        return {"key": key, "id": key, "label": label, "count": len(rows), "color": color, "drop": False,
                "cards": [card(r) for r in rows[:CARD_LIMIT]], "more": max(0, len(rows) - CARD_LIMIT)}

    pos = {r["id"]: r["pos"] for r in lines}
    todo.sort(key=lambda r: (pos.get(r["line"], 0), r["pos"]))
    wait.sort(key=lambda r: (pos.get(r["line"], 0), r["pos"]))
    done_week.sort(key=lambda r: (r["done_at"] or "", -pos.get(r["line"], 0)), reverse=True)
    return {**summary, "mainNote": note, "mainCells": main, "lines": out_lines, "older": older,
            "columns": [column("todo", L("没做", "Not started"), None, todo), column("wait", L("等你", "Waiting on you"), 1, wait),
                        column("done", L("这周做完", "Done this week"), 0, done_week)]}


def synced_label(synced: str | None, today: date) -> str:
    """交接本最后一次改动后同步的时间：今天的写几点，更早的写日子。"""
    try:
        at = datetime.fromisoformat(synced).astimezone(TZ) if synced else None
    except ValueError:
        return ""
    if at is None:
        return ""
    return at.strftime("%H:%M") if at.date() == today else f"{at.month}/{at.day}"


def report_card() -> tuple[dict | None, str | None]:
    """「今天」报告里的工程看板卡（没配 = (None, None)）。GitHub 看板读不了时给上一次读到的卡，再说一声。"""
    c = cfg()
    if not c:
        return None, None
    if is_github(c):
        s, err = board_gh(c, full=False)
        return (eng_card(s) if s else None), err
    try:
        err = sync()
        if err:
            return None, err
        s = board(full=False)
    except (OSError, sqlite3.Error, ValueError) as e:
        return None, L(f"工程看板读不出来：{e}", f"Couldn't read the engineering board: {e}")[:200]
    return eng_card(s), None


def eng_card(s: dict) -> dict:
    return {"type": "eng", "id": "eng-board", "kind": "eng_board", "title": L("工程看板", "Engineering"), "time": s["syncedLabel"],
            "weekDone": s["weekDone"], "todo": s["todo"], "wait": s["wait"], "main": s["main"],
            "body": "", "agent": None, "unseen": False, "cta": "", "data": None, "createdAt": s.get("syncedAt") or ""}


def line_items(lid: str) -> dict:
    """一条线里的事：没做 / 等你 / 做完（做完的新的在前），给看板页点开一条线用。"""
    with _lock, edb() as conn:
        line = conn.execute("SELECT * FROM eng_lines WHERE id=?", (lid,)).fetchone()
        if line is None:
            raise HTTPException(404, L("没有这条线", "No such line"))
        rows = conn.execute("SELECT * FROM eng_items WHERE line=? AND (gone_at IS NULL OR (? AND state='done')) ORDER BY pos",
                            (lid, int(bool(line["gone_at"])))).fetchall()
    rows = [r for r in rows if counted(r)]

    def one(r: sqlite3.Row) -> dict:
        return {"id": r["id"], "title": short_title(r["text"]), "text": plain(r["text"]), "state": r["state"], "doneAt": r["done_at"],
                "group": r["grp"] or None}

    done = sorted((r for r in rows if r["state"] == "done"), key=lambda r: r["done_at"] or "", reverse=True)
    st = line_status(line, rows, datetime.now(TZ).date())
    return {"id": line["id"], "name": line["name"], "heading": plain(line["heading"]), "status": st, "statusLabel": L(*STATUS_LABEL[st]),
            "todo": [one(r) for r in rows if r["state"] == "todo"], "wait": [one(r) for r in rows if r["state"] == "wait"],
            "done": [one(r) for r in done]}


# —— GitHub 看板（source = github）—————————————————————————————————————————————

GH_TTL_S = 300        # 「今天」页的卡：读一次 GitHub 管 5 分钟（每次打开「今天」都要这张卡）
GH_PAGE_TTL_S = 60    # 点进看板页、下拉刷新：读过 1 分钟就重读
GH_RETRY_S = 60       # 读失败以后 1 分钟里不再试，先给上一次的
GH_TIMEOUT_S = 20
GH_PAGES = 20         # 一页 100 件，最多读 20 页
GH_FIELDS = {"status": "Status", "line": "线", "who": "谁做", "next": "下一步", "evidence": "证据", "updated": "更新"}
# Status 的选项 → wait 等你 / doing 在做 / ship 等上线 / queue 排队 / later 以后 / done 做完（不分大小写；后三个是 GitHub 新建看板默认的）
GH_STATUS = {"等你": "wait", "在做": "doing", "等上线": "ship", "排队": "queue", "以后": "later", "做完": "done",
             "todo": "queue", "in progress": "doing", "done": "done"}
GH_KEYS = ("doing", "ship", "wait", "queue", "later", "done")  # 一条线里、一列里怎么排
GH_LABEL = {"wait": ("等你", "Waiting on you"), "doing": ("在做", "Doing"), "ship": ("等上线", "Ready to ship"), "queue": ("排队", "Queued"),
            "later": ("以后", "Later"), "done": ("做完", "Done")}
GH_QUERY = """query($owner: String!, $number: Int!, $lineField: String!, $after: String) {
  repositoryOwner(login: $owner) { ... on ProjectV2Owner { projectV2(number: $number) {
    title url
    line: field(name: $lineField) { ... on ProjectV2SingleSelectField { options { name } } }
    items(first: 100, after: $after) {
      pageInfo { hasNextPage endCursor }
      nodes { id isArchived updatedAt
        content { ... on DraftIssue { title } ... on Issue { title url } ... on PullRequest { title url } }
        fieldValues(first: 30) { nodes {
          ... on ProjectV2ItemFieldSingleSelectValue { name field { ... on ProjectV2FieldCommon { name } } }
          ... on ProjectV2ItemFieldTextValue { text field { ... on ProjectV2FieldCommon { name } } }
          ... on ProjectV2ItemFieldDateValue { date field { ... on ProjectV2FieldCommon { name } } } } } } } } } } }"""

_gh_lock = threading.Lock()
_gh: dict[str, dict] = {}  # 看板 → {"board": 上一次读到的, "at": 那次的时候（clock）, "tried": 上次试的时候, "error": 上次没读成的原因}
clock = time.monotonic     # 测试里换成假的钟


class GhError(Exception):
    """GitHub 看板没读成（一句给人看的原因）。"""


def gh_names(c: dict) -> dict[str, str]:
    """看板上的字段名（server.json 的 fields 盖过默认的中文名）。"""
    f = c.get("fields")
    mine = {k: str(v).strip() for k, v in f.items() if k in GH_FIELDS and str(v).strip()} if isinstance(f, dict) else {}
    return {**GH_FIELDS, **mine}


def gh_statuses(c: dict) -> dict[str, str]:
    s = c.get("statuses")
    mine = {str(k).strip().lower(): v for k, v in s.items() if v in GH_LABEL} if isinstance(s, dict) else {}
    return {**{k.lower(): v for k, v in GH_STATUS.items()}, **mine}


def run_gh(args: list[str]) -> str:
    """跑一次 gh，给标准输出。没装、超时、出错都抛 GhError（gh 报错的第一行）。"""
    env = {**os.environ, "GH_PROMPT_DISABLED": "1", "GH_NO_UPDATE_NOTIFIER": "1", "NO_COLOR": "1"}
    try:
        r = subprocess.run(args, capture_output=True, encoding="utf-8", errors="replace", timeout=GH_TIMEOUT_S, env=env, check=False)
    except FileNotFoundError:
        raise GhError(L("这台机器上没有 gh 命令", "The gh command isn't installed on this machine")) from None
    except subprocess.TimeoutExpired:
        raise GhError(L(f"gh {GH_TIMEOUT_S} 秒没回话", f"gh didn't answer within {GH_TIMEOUT_S}s")) from None
    if r.returncode != 0:
        first = next((x.strip() for x in (r.stderr or r.stdout or "").splitlines() if x.strip()), "")
        raise GhError((first or f"gh exit {r.returncode}")[:200])
    return r.stdout


def gh_day(v) -> str:
    """GitHub 记的时间（UTC 的 ISO）→ 按 server.json 时区的那天。"""
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).astimezone(TZ).date().isoformat() if v else ""
    except ValueError:
        return ""


def gh_item(n, names: dict[str, str], statuses: dict[str, str]) -> dict | None:
    """看板上的一件 → {id, title, url, status（看板上的字）, key, line, who, next, evidence, updated}。存档了的、读不到标题的不要。"""
    if not isinstance(n, dict) or n.get("isArchived"):
        return None
    content = n.get("content") or {}
    title = " ".join(str(content.get("title") or "").split())
    if not title:
        return None
    vals: dict[str, str] = {}
    for v in (n.get("fieldValues") or {}).get("nodes") or []:
        name = ((v or {}).get("field") or {}).get("name")
        val = (v or {}).get("name") or (v or {}).get("text") or (v or {}).get("date")
        if name and val:
            vals[name] = " ".join(str(val).split())
    status = vals.get(names["status"], "")
    return {"id": str(n.get("id") or ""), "title": title, "url": content.get("url") or None, "status": status,
            "key": statuses.get(status.lower(), "queue"), "line": vals.get(names["line"], ""), "who": vals.get(names["who"], ""),
            "next": vals.get(names["next"], ""), "evidence": vals.get(names["evidence"], ""),
            "updated": vals.get(names["updated"], "") or gh_day(n.get("updatedAt"))}


def gh_fetch(c: dict) -> dict:
    """读一遍看板 → {"title", "url", "lineOrder": [「线」的选项], "items": [gh_item…]}。"""
    names, statuses = gh_names(c), gh_statuses(c)
    try:
        number = int(c["number"])
    except (TypeError, ValueError):
        raise GhError(L(f"看板编号不对：{c.get('number')}", f"Bad board number: {c.get('number')}")) from None
    owner = str(c["owner"]).strip()
    base = [str(c.get("gh") or "").strip() or shutil.which("gh") or "gh", "api", "graphql", "-f", f"query={GH_QUERY}", "-f", f"owner={owner}",
            "-F", f"number={number}", "-f", f"lineField={names['line']}"]
    out: dict = {"title": "", "url": "", "lineOrder": [], "items": []}
    after = None
    for _ in range(GH_PAGES):
        text = run_gh(base + (["-f", f"after={after}"] if after else []))
        try:
            p = json.loads(text)["data"]["repositoryOwner"]["projectV2"]
        except (ValueError, KeyError, TypeError):
            p = None
        if not isinstance(p, dict):
            raise GhError(L(f"找不到看板 {owner}/{number}", f"No board {owner}/{number}"))
        out.update(title=p.get("title") or "", url=p.get("url") or "",
                   lineOrder=[o["name"] for o in ((p.get("line") or {}).get("options") or []) if isinstance(o, dict) and o.get("name")])
        out["items"] += [x for x in (gh_item(n, names, statuses) for n in (p.get("items") or {}).get("nodes") or []) if x]
        page = (p.get("items") or {}).get("pageInfo") or {}
        if not page.get("hasNextPage") or not page.get("endCursor"):
            break
        after = page["endCursor"]
    return out


def gh_saved(key: str) -> dict | None:
    """库里存的上一次（服务重启以后 GitHub 又读不了时用）。"""
    try:
        with _lock, edb() as conn:
            r = conn.execute("SELECT data FROM eng_gh WHERE key=?", (key,)).fetchone()
        return json.loads(r["data"]) if r else None
    except (sqlite3.Error, ValueError):
        return None


def gh_save(key: str, b: dict) -> None:
    try:
        with _lock, edb() as conn:
            conn.execute("INSERT INTO eng_gh(key, data, fetched_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET data=excluded.data, "
                         "fetched_at=excluded.fetched_at", (key, json.dumps(b, ensure_ascii=False), b["fetchedAt"]))
    except sqlite3.Error as e:
        print(f"[engboard] GitHub 看板存不进库：{e}")


def gh_board(c: dict, max_age: float = GH_TTL_S) -> tuple[dict | None, str | None]:
    """(读到的看板, 出错原因)：max_age 秒内读过就给那次的；过了重读（同一时间只读一份，别的请求等它读完）；
    读不了给上一次的（内存里没有就用库里的），原因里写给的是几点读的。一次都没读到过 = (None, 原因)。"""
    key = f"{str(c['owner']).strip()}/{c['number']}"
    with _gh_lock:
        now = clock()
        e = _gh.get(key) or {}
        if e.get("board") is not None and not e.get("error") and now - e["at"] < max_age:
            return e["board"], None
        if e.get("error") and now - e["tried"] < GH_RETRY_S:
            return e.get("board"), e["error"]
        try:
            b = gh_fetch(c)
        except GhError as err:
            prev = e.get("board") or gh_saved(key)
            when = synced_label(prev.get("fetchedAt"), datetime.now(TZ).date()) if prev else ""
            msg = (L(f"没能读 GitHub 看板：{err}", f"Couldn't read the GitHub board: {err}")
                   + (L(f"（给的是 {when} 读到的）", f" (showing what was read at {when})") if when else ""))[:300]
            _gh[key] = {"board": prev, "at": e.get("at", 0.0), "tried": now, "error": msg}
            return prev, msg
        b["fetchedAt"] = now_iso()
        _gh[key] = {"board": b, "at": now, "tried": now, "error": None}
    gh_save(key, b)
    return b, None


def gh_short(title: str) -> str:
    """卡片上的标题：长了（36 个字以上）取冒号、分号前面那段（够 6 个字的话），还长就截到 42 个字。"""
    t = title
    if len(t) > 36:
        head = re.split(r"[：:；;]", t, maxsplit=1)[0].strip()
        if len(head) >= 6:
            t = head
    return t if len(t) <= 44 else t[:42] + "…"


def md_day(iso: str) -> str:
    """2026-10-19 → 10/19。"""
    try:
        d = date.fromisoformat(iso)
    except ValueError:
        return iso
    return f"{d.month}/{d.day}"


def gh_text(x: dict) -> str:
    """点开一件看到的：标题全文、下一步、证据、链接，最后一行谁做 · 哪一格 · 更新。"""
    meta = " · ".join(v for v in (x["who"], x["status"], L(f"更新 {md_day(x['updated'])}", f"updated {md_day(x['updated'])}") if x["updated"] else "") if v)
    rows = (x["title"], L(f"下一步：{x['next']}", f"Next: {x['next']}") if x["next"] else "",
            L(f"证据：{x['evidence']}", f"Evidence: {x['evidence']}") if x["evidence"] else "", x.get("url") or "", meta)
    return "\n".join(v for v in rows if v)


def gh_heading(rs: list[dict]) -> str:
    """点开一条线时顶上那行：各格几件（在做 3 · 排队 6 · 以后 4 · 做完 5）。"""
    return " · ".join(f"{L(*GH_LABEL[k])} {n}" for k in GH_KEYS if (n := sum(1 for x in rs if x["key"] == k)))


def gh_view(b: dict, today: date) -> list[dict]:
    """读到的看板 → 各条线（照「线」选项的顺序，没写线的在最后）：{id, name, items（照 GH_KEYS 排）, status, day, recent}。"""
    order = {n: i for i, n in enumerate(b.get("lineOrder") or [])}
    none = L("没分线", "No line")
    groups: dict[str, list[dict]] = {}
    for i, it in enumerate(b.get("items") or []):
        name = it.get("line") or none
        groups.setdefault(name, []).append({**it, "pos": i, "lineName": name, "state": {"wait": "wait", "done": "done"}.get(it["key"], "todo")})
    recent_from = (today - timedelta(days=RECENT_DAYS)).isoformat()
    out = []
    for name in sorted(groups, key=lambda n: (n == none, order.get(n, len(order)), n)):
        rs = sorted(groups[name], key=lambda x: (GH_KEYS.index(x["key"]), x["pos"]))
        keys = {x["key"] for x in rs}
        status = "doing" if keys & {"doing", "ship"} else "wait" if "wait" in keys else "queue" if keys & {"queue", "later"} else "done"
        recent = bool(keys & {"doing", "ship", "wait", "queue"}) or any(x["key"] == "done" and x["updated"] >= recent_from for x in rs)
        out.append({"id": line_id(name, set()), "name": name, "items": rs, "status": status,
                    "day": max((x["updated"] for x in rs if x["updated"]), default=None), "recent": recent})
    return out


def gh_main(c: dict, lines: list[dict]) -> tuple[list[dict], str]:
    """主线（server.json 的 mainline 指的 JSON）→ 看板页的分格和一句话。没配、读不了 = ([], "")。"""
    p = str(c.get("mainline") or "").strip()
    if not p:
        return [], ""
    try:
        steps = json.loads(Path(p).expanduser().read_text(encoding="utf8"))
    except (OSError, ValueError):
        return [], ""
    by = {x["name"]: x for x in lines}
    out, note = [], ""
    for s in steps if isinstance(steps, list) else []:
        name = str(s.get("name") or "").strip() if isinstance(s, dict) else ""
        if not name:
            continue
        ln = by.get(str(s.get("line") or "").strip()) or by.get(name) or max(
            (x for x in lines if len(x["name"]) >= 2 and (name.startswith(x["name"]) or x["name"].startswith(name))), key=lambda x: len(x["name"]),
            default=None)
        state = str(s.get("state") or "").strip()
        cells = sorted((x["key"] == "done" for x in ln["items"]), reverse=True) if ln else [state in ("做完", "done")]
        out.append({"name": name, "done": sum(cells), "total": len(cells), "cells": cells})
        if not note and state in ("在做", "doing") and str(s.get("note") or "").strip():
            note = L(f"{name}：{str(s['note']).strip()}", f"{name}: {str(s['note']).strip()}")
    return out, note


EMPTY_BOARD = {"weekDone": 0, "todo": 0, "wait": 0, "main": [], "syncedAt": None, "syncedLabel": "", "mainNote": "", "mainCells": [],
               "lines": [], "older": [], "columns": []}


def board_gh(c: dict, full: bool = True, max_age: float = GH_TTL_S) -> tuple[dict | None, str | None]:
    """GitHub 看板的看板页（和 board() 一样的样子）。(看板, 出错原因)；一次都没读到过 = (None, 原因)。"""
    b, err = gh_board(c, max_age)
    if b is None:
        return None, err
    today = datetime.now(TZ).date()
    monday = week_start(today).isoformat()
    lines = gh_view(b, today)
    every = [x for ln in lines for x in ln["items"]]
    by = {k: sorted((x for x in every if x["key"] == k), key=lambda x: x["pos"]) for k in GH_KEYS}
    week = sorted((x for x in by["done"] if x["updated"] >= monday), key=lambda x: x["updated"], reverse=True)
    main, note = gh_main(c, lines)
    summary = {"weekDone": len(week), "todo": len(by["doing"]) + len(by["ship"]) + len(by["queue"]), "wait": len(by["wait"]),
               "main": [{k: m[k] for k in ("name", "done", "total")} for m in main], "syncedAt": b.get("fetchedAt"),
               "syncedLabel": synced_label(b.get("fetchedAt"), today)}
    if not full:
        return summary, err

    def card(x: dict) -> dict:
        title = gh_short(x["title"])
        if x["key"] == "ship":  # 等上线的放在「在做」那列，标题前写明
            title = f"{L(*GH_LABEL['ship'])} · {title}"
        return {"id": x["id"], "title": title, "tags": [{"text": t, "tone": "neutral"} for t in (x["lineName"], x["who"]) if t],
                "data": {"text": gh_text(x), "line": x["lineName"], "doneAt": x["updated"] if x["key"] == "done" else None}, "display": {}}

    def column(key: str, color: int | None, rows: list[dict], label: str | None = None) -> dict:
        return {"key": key, "id": key, "label": label or L(*GH_LABEL[key]), "count": len(rows), "color": color, "drop": False,
                "cards": [card(x) for x in rows[:CARD_LIMIT]], "more": max(0, len(rows) - CARD_LIMIT)}

    out_lines, older = [], []
    for ln in lines:
        rs = ln["items"]
        one = {"id": ln["id"], "name": ln["name"], "heading": gh_heading(rs), "done": sum(1 for x in rs if x["key"] == "done"), "total": len(rs),
               "status": ln["status"], "statusLabel": L(*STATUS_LABEL[ln["status"]]), "day": ln["day"], "archived": False}
        (out_lines if ln["recent"] else older).append(one)
    older.sort(key=lambda x: x["day"] or "", reverse=True)
    return {**summary, "mainNote": note, "mainCells": main, "lines": out_lines, "older": older,
            "columns": [column("wait", 1, by["wait"]), column("doing", 0, by["doing"] + by["ship"]), column("queue", None, by["queue"]),
                        column("later", None, by["later"]), column("done", 0, week, L("这周做完", "Done this week"))]}, err


def line_items_gh(c: dict, lid: str) -> dict:
    """GitHub 看板点开一条线：没做的（在做 → 等上线 → 排队 → 以后，标题前写着是哪一格）、等你的、做完的（新的在前）。"""
    b, _ = gh_board(c, GH_PAGE_TTL_S)
    ln = next((x for x in gh_view(b, datetime.now(TZ).date()) if x["id"] == lid), None) if b else None
    if ln is None:
        raise HTTPException(404, L("没有这条线", "No such line"))

    def one(x: dict) -> dict:
        title = gh_short(x["title"])
        if x["key"] in ("doing", "ship", "later"):
            title = f"{L(*GH_LABEL[x['key']])} · {title}"
        return {"id": x["id"], "title": title, "text": gh_text(x), "state": x["state"], "doneAt": x["updated"] if x["key"] == "done" else None,
                "group": x["status"] or None}

    rs = ln["items"]
    done = sorted((x for x in rs if x["key"] == "done"), key=lambda x: x["updated"], reverse=True)
    return {"id": ln["id"], "name": ln["name"], "heading": gh_heading(rs), "status": ln["status"], "statusLabel": L(*STATUS_LABEL[ln["status"]]),
            "todo": [one(x) for x in rs if x["state"] == "todo"], "wait": [one(x) for x in rs if x["state"] == "wait"], "done": [one(x) for x in done]}


@router.get("/api/today/eng/line/{lid}")
def get_eng_line(lid: str):
    """看板页点开一条线：这条线里没做的、等你的、做完的。"""
    c = cfg()
    if not c:
        raise HTTPException(404, L("没有配工程看板（server.json 的 today.engineering）", "No engineering board (today.engineering in server.json)"))
    if is_github(c):
        return {"ok": True, **line_items_gh(c, lid)}
    sync()  # 读不了就用库里上次同步的
    return {"ok": True, **line_items(lid)}


@router.get("/api/today/eng")
def get_eng():
    """工程看板页（只读）：交接本改过就先重读；GitHub 看板读过 1 分钟就重读。"""
    c = cfg()
    if not c:
        raise HTTPException(404, L("没有配工程看板（server.json 的 today.engineering）", "No engineering board (today.engineering in server.json)"))
    if is_github(c):
        b, err = board_gh(c, max_age=GH_PAGE_TTL_S)  # 读不了就给上一次读到的，error 里说是几点的
        return {"ok": True, **(b or EMPTY_BOARD), "error": err}
    err = sync()  # 读不了就给库里上次同步的，error 里说一声
    return {"ok": True, **board(), "error": err}
