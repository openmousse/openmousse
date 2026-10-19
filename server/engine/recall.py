"""记忆检索（内核第 7 项，2026-10-16）：Agent 搜自己的记忆和以前的对话。默认是 SQLite FTS5 关键词检索，不用嵌入模型、不花钱。
配了 engine.recall.embeddings（engine/embeddings.py）才按意思搜：每段多存一串向量，关键词和意思合在一起排，世界树的叶子也进索引
（engine/semantic.py、engine/recall_tree.py）。没配的时候下面写的一切照旧，一个字都不会发出去。

搜什么（都按 Agent 卡范围，见 scope()）：
  工作区的记忆  这个 Agent 工作区里的 MEMORY.md、USER.md、memory/ 下的 *.md（日记 YYYY-MM-DD.md 和主题笔记），和 OpenClaw memory-core 的
               memory 来源一样。主对话的是 settings.workspace；没有自己工作区的 Agent（别的 claw、内核建的）没有这一块，不借主对话的
  日结          <data_dir>/digest/<对话>/<逻辑日>.md（chat.save_digest 给别的 claw 和内核存的），归那个对话的 Agent
  对话          mousse.db 的 messages：用户和助手说过的话（不含 auto 灰字、没拿到回复的出错行），归那个对话的 Agent（owner.agent_of）。
               这一轮所在的对话里还在上下文里的那段不搜（Hermes 的 session_search 也跳过当前会话）；压缩成摘要的照样能搜
  共享的        server.json engine.recall.extra 列的文件或文件夹；不写 = 只有档案 USER.md（settings.profile，所有 Agent 都能看的 L0）。
               每项可以用 agents 限定给哪几个 Agent
  世界树        不进这里的索引：memory_search 顺手跑一次 tree 工具的 recall，附在结果后面（这个 Agent 有 tree 工具才有）。
               按意思搜开着时一片叶子一段进索引（按枝的范围，engine/recall_tree.py），出处写 tree:<叶子 id>，不再另外附
范围：一个 Agent 只搜得到自己工作区、自己的对话和日结、给它的共享项。主对话默认也一样（Grava 的 OpenClaw 就是这么配的：memory.search
  每个 agent 一份索引，extraPaths 是大家共用的档案、各组日结、世界树导出、Obsidian 几个文件夹）；engine.recall.main = "all" 放开成全部。
  赛博、外人回合没有这两个工具（kinds = owner）。
不索引内核自己的 engine_entries：主人回合说的话 messages 里都有（app 里删掉的、撤回掉的它还留着，搜出来就不对了）；工具的原样输出大多是
  机器噪音（Hermes 默认也不搜）；赛博、外人回合的不是主人的记忆。

索引：<data_dir>/recall.db（单独一个库文件：能整个删掉重建、不占 mousse.db 的写锁、每日备份可以不带它；engine.recall.db 能改位置）。
  sources 一行一个文件或一条消息：文件按 mtime + 大小（再看内容指纹）增量；消息按 id 增量，删掉的靠 count / sum(id) 对出来再删。
  chunks 一段：文件照 OpenClaw 的切法（约 400 token 一段、带约 80 token 的重叠、按标题分节，MEMORY.md / USER.md 一条一段），记下行号和标题。
  chunks_fts 是 FTS5（porter unicode61）。中日韩连续的字先在 Python 里切成重叠的两字词再写进去（Hermes 的 cjk_unicode61 是同样的切法，
  它要编译一个扩展，我们在写进去之前切）：两个字以上的词走索引、按 BM25 排；带单独一个字的词（「猫」「第3次」）退回 LIKE。
  搜之前把这个 Agent 范围里的来源增量同步一遍（第一次是全量）。库的格式或 Python 的 Unicode 版本变了就整个重建。
  开着 FTS5 的 secure-delete 和 PRAGMA secure_delete：删掉的内容不留在库文件里（来源里忘掉的，下次同步后这里也真忘了）。

排序：BM25 × 时间（带日期的：日记、日结、对话，30 天分量减半、最少留一半；MEMORY.md 这类常青的不减，照 OpenClaw 的 temporalDecay），
  每个文件最多两段、同一个对话同一天最多三条、几乎一样的只留一条。所有词都要有（Hermes、OpenClaw 都是先 AND）；一条都没有再放宽成任一个词。
  按意思搜开着时：0.7 × 余弦 + 0.3 × 关键词分，再 × 时间 × 信几分，MMR 去掉差不多的（_hybrid，细节在 engine/semantic.py 开头）；
  搜的那一句算不出向量（没密钥、限流、断网）就照上面只按关键词，状态里写明。

命令行（测试和排查用；读 MOUSSE_SERVER_CONFIG 指的那份 server.json，没设就是 ~/.openmousse/server.json，别对着线上的跑重建）：
  python3 server/engine/recall.py search "<词>" [--agent id] [--after 7d] [--before 2026-10-01] [--source memory|chats] [--limit 6]
  python3 server/engine/recall.py get <出处> [--agent id] [--from N] [--lines N]
  python3 server/engine/recall.py reindex [--full]      全部来源增量同步；--full 删掉索引从头建（向量也删了，要重新发去算）
  python3 server/engine/recall.py embed [--max N]       在前台补向量（配了 engine.recall.embeddings 才有；服务器里后台也会补）
  python3 server/engine/recall.py stats

部分改写自 Hermes（NousResearch/hermes-agent commit 848fc0a，MIT，Copyright (c) 2025 Nous Research；许可全文见仓库根目录的
THIRD_PARTY_NOTICES）：hermes_state_search.py 的中日韩字判断（_CJK_RANGES、_has_lone_cjk_run）、查询拆词和 AND / OR / NOT
（_LIKE_TOKEN_RE、_compile_like_boolean_query、_or_relaxed_query）、hermes_state_common.py 的 escape_like、
tools/session_search_tool.py 的往回数的时间（7d / 24h / 2w，_RELATIVE_BOUND_RE、_parse_iso_bound）。
切段、时间衰减、读原文的写法照 OpenClaw memory-core（markdown-chunks.ts、temporal-decay.ts、read-file-shared.ts），只借设计。
"""
from __future__ import annotations

import sys
from pathlib import Path

if __name__ == "__main__" and not __package__:  # python3 server/engine/recall.py …：当脚本跑，换成包里的这个模块再跑
    _HERE = Path(__file__).resolve().parent
    sys.path[:] = [p for p in sys.path if Path(p or ".").resolve() != _HERE]  # engine/ 里的文件别顶替同名的模块
    sys.path.insert(0, str(_HERE.parent))
    from engine.recall import main
    raise SystemExit(main())

import argparse
import asyncio
import contextlib
import hashlib
import os
import re
import sqlite3
import stat
import threading
import time
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from config import raw, settings
from i18n import L

try:
    import fcntl
except ImportError:  # Windows：只有进程里的锁
    fcntl = None  # type: ignore[assignment]

SCHEMA = 1                  # 库的格式；改了（或者 Python 的 Unicode 版本变了，切字的结果跟着变）就整个重建
TOKENIZE = "porter unicode61 remove_diacritics 2"
MAX_FILE = 2_000_000        # 一个文件超过这么多字节不收（日志转储之类）
MAX_FILES = 5000            # 一个共享文件夹最多收多少个文件
CHUNK = 1600                # 一段多大：按 UTF-8 字节估（英文约 400 token、中文约 530 字；OpenClaw 默认 400 token 一段）
OVERLAP = 320               # 相邻两段重叠多少（约 80 token）
LIMIT, LIMIT_MAX = 6, 20    # 默认回几条（OpenClaw 默认 6）、最多几条
MAX_CHARS = 6000            # 一次搜索回给模型最多多少字（engine.recall.max_chars 能改）
TREE_CHARS = 1500           # 附在后面的世界树最多多少字
HALF_LIFE, FLOOR = 30, 0.5  # 带日期的东西 30 天分量减半，最少留一半（engine.recall.half_life_days；0 = 不减）
READ_LINES, READ_MAX = 60, 12000  # memory_get 读文件：默认几行、最多回多少字（OpenClaw 是 120 行 / 12000 字）
AROUND, MSG_MAX = 3, 1500   # memory_get 读对话：前后各几条、每条最多多少字
SHOW_LINES, LINE_MAX = 4, 220  # 每条结果最多列几行命中的、每行最多多少字
MAX_QUERY = 2048            # 查询最多多少字（Hermes 的 MAX_FTS5_QUERY_CHARS）
DAY_START_HOUR = 4          # 逻辑日 04:00 换（同 chat.DAY_START_HOUR；命令行不想为它导入整个 chat）
TEXT_EXT = (".md", ".markdown", ".txt")
CHAT_ROLES = ("user", "grava")
TOOLS = ("memory_search", "memory_get")  # 给模型的两个工具（owner.rules 拿它把这两个和命令行工具分开说）

_lock = threading.Lock()


class RecallError(Exception):
    """给模型看的一句（读不了、不在你的范围里、参数不对）。"""


# —— 切字 ——————————————————————————————————————————————————————————————————
# 中日韩的「字」（标点不算，unicode61 本来就把它们当分隔）。范围参照 Hermes 的 _CJK_RANGES，去掉了 3000–303F 里的标点、
# 加上兼容汉字和扩展 B 以后的几个区
_CJK_CHARS = ("\u3005-\u3007\u3041-\u3096\u309d-\u309f\u30a1-\u30fa\u30fc-\u30ff\u3400-\u4dbf\u4e00-\u9fff"
              "\uac00-\ud7af\uf900-\ufaff\U00020000-\U0002ebef\U00030000-\U0003134f")
CJK_RUN = re.compile(f"[{_CJK_CHARS}]+")
WORDISH = re.compile(r"[^\W_]")  # 有字母、数字或字（只有下划线、标点的词搜不了）


def norm(text: str) -> str:
    """NFKC：全角字母数字变半角、兼容汉字变统一汉字……索引和查询两边都过一遍，对得上。存的原文不改（中文的全角标点照原样给模型看）。"""
    return unicodedata.normalize("NFKC", text)


def _bigrams(m: re.Match) -> str:
    run = m.group(0)
    return " " + (run if len(run) == 1 else " ".join(run[i:i + 2] for i in range(len(run) - 1))) + " "


def segment(text: str) -> str:
    """写进 FTS5 的文字：中日韩连续的字切成重叠的两字词（单独一个字就是它自己），别的原样交给 unicode61 按词切。
    「修改youer服务端」→「修改 youer 服务 务端」：拉丁字母和汉字挨着也分得开（Hermes 说 unicode61 自己在这里切不开）。"""
    return CJK_RUN.sub(_bigrams, norm(text))


def escape_like(text: str) -> str:
    """LIKE 的 % 和 _ 照字面匹配（Hermes 的 escape_like），配 ESCAPE '\\'。"""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# —— 查询 ——————————————————————————————————————————————————————————————————
_TOKEN = re.compile(r'"[^"]+"|\S+')  # Hermes 的 _LIKE_TOKEN_RE：带引号的整句，或者一个词


@dataclass
class Term:
    text: str                # NFKC 过的词（不带引号）
    neg: bool = False        # NOT：不能有
    prefix: bool = False     # 英文词后面写了 *：前缀

    @property
    def like(self) -> bool:
        """只能走 LIKE 的：里面有单独一个中日韩字。两字词的索引里只有前后都不挨着字的那种单字（Hermes 的 _has_lone_cjk_run）。"""
        return any(len(r) == 1 for r in CJK_RUN.findall(self.text))

    def fts(self) -> str:
        p = '"' + segment(self.text).strip().replace('"', '""') + '"'
        return p + "*" if self.prefix else p


def parse(query: str, upper_ops: bool = False) -> list[list[Term]]:
    """查询 → 几组：组之间是「或」，组里的词都要有、neg 的都不能有（Hermes 的 _compile_like_boolean_query）。
    写法同 Hermes 的 session_search：空格隔开的词都要有，"带引号" = 整句，OR = 或，NOT 词 = 不要，英文词* = 前缀。没有要的词的组扔掉。
    upper_ops：只有大写的 OR / NOT / AND 算写法（按意思搜时 query 常是一句话，「what I did not like」里的 not 是话里的词）。"""
    groups: list[list[Term]] = [[]]
    neg = False
    for tok in _TOKEN.findall(norm(query)[:MAX_QUERY]):
        up = tok if upper_ops else tok.upper()
        if up == "OR":
            if groups[-1]:
                groups.append([])
            neg = False
            continue
        if up in ("AND", "NEAR"):
            continue
        if up == "NOT":
            neg = True
            continue
        quoted = len(tok) > 1 and tok.startswith('"') and tok.endswith('"')
        word = tok[1:-1].strip() if quoted else tok.strip('"')
        prefix = False
        if not quoted and word.endswith("*"):
            word = word.rstrip("*")
            prefix = word.isascii()  # 中日韩的词用不了前缀（Hermes 的 _strip_cjk_wildcards）
        if WORDISH.search(word):
            groups[-1].append(Term(word, neg, prefix))
        neg = False
    return [g for g in groups if any(not t.neg for t in g)]


def relaxed(groups: list[list[Term]]) -> list[list[Term]] | None:
    """全都要有的没对上：放宽成「任一个词」（Hermes 的 _or_relaxed_query、OpenClaw 的 matchAny）。写了 OR / NOT 的不放宽。"""
    if len(groups) != 1 or any(t.neg for t in groups[0]) or len(groups[0]) < 2:
        return None
    return [[t] for t in groups[0]]


LOOSE_MAX = 32


def loose(groups: list[list[Term]]) -> list[list[Term]] | None:
    """按意思搜时关键词那一路再宽一点：要的词任一个都行，中文的长词拆成两字词（「上次说想去哪儿旅行」→ 上次、想去、旅行……，一句话的查询
    也有一点关键词分），单独一个字的不要（太泛），NOT 的照样不能有。和原来一样 → None。"""
    negs = [t for g in groups for t in g if t.neg]
    seen: set[str] = set()
    out: list[list[Term]] = []
    for g in groups:
        for t in g:
            if t.neg:
                continue
            for tok in [t.text] if t.prefix else segment(t.text).split():
                key = tok.lower()
                if key in seen or not WORDISH.search(tok) or (len(tok) == 1 and CJK_RUN.fullmatch(tok)):
                    continue
                seen.add(key)
                out.append([Term(tok, prefix=t.prefix), *negs])
    sig = [[(x.text.lower(), x.neg) for x in g] for g in out]
    if not out or sig == [[(x.text.lower(), x.neg) for x in g] for g in groups]:
        return None
    return out[:LOOSE_MAX]


# —— 时间 ——————————————————————————————————————————————————————————————————
_REL = re.compile(r"^(\d+)\s*(h|d|w)$", re.IGNORECASE)  # Hermes 的 _RELATIVE_BOUND_RE：7d / 24h / 2w = 往回数


def day_of(dt: datetime) -> str:
    """一个时刻属于哪个逻辑日（server.json 的时区，04:00 为界，同 chat.day_of）。"""
    return (dt.astimezone(settings.tz) - timedelta(hours=DAY_START_HOUR)).strftime("%Y-%m-%d")


def today() -> str:
    return day_of(datetime.now(settings.tz))


def day_of_ts(ts: str | None) -> str | None:
    if not ts:
        return None
    try:
        return day_of(datetime.fromisoformat(ts))
    except ValueError:
        return ts[:10] if re.match(r"\d{4}-\d{2}-\d{2}", ts) else None


def day_bound(value) -> str | None:
    """after / before 的值 → 逻辑日：2026-10-01（带时间的只看日期），或往回数的 7d、24h、2w（Hermes 的 _parse_iso_bound）。"""
    v = str(value or "").strip()
    if not v:
        return None
    if m := _REL.match(v):
        hours = int(m.group(1)) * {"h": 1, "d": 24, "w": 168}[m.group(2).lower()]
        return day_of(datetime.now(settings.tz) - timedelta(hours=hours))
    try:
        return date.fromisoformat(v[:10]).isoformat()
    except ValueError:
        raise RecallError(L(f"看不懂这个时间：{v}（写 2026-10-01，或者往回数的 7d、24h、2w）",
                            f"Can't read this time: {v} (write 2026-10-01, or count back with 7d, 24h, 2w)")) from None


_DATED = re.compile(r"^(\d{4}-\d{2}-\d{2})")


def day_in_name(rel: str) -> str | None:
    """文件名以日期开头的（日记 2026-10-01.md、日结、带日期的笔记）→ 那一天；别的是常青的（MEMORY.md、主题笔记），不衰减。"""
    m = _DATED.match(Path(rel).name)
    if not m:
        return None
    try:
        return date.fromisoformat(m.group(1)).isoformat()
    except ValueError:
        return None


# —— 来源和范围 ——————————————————————————————————————————————————————————————

def config() -> dict:
    e = raw().get("engine")
    r = e.get("recall") if isinstance(e, dict) else None
    return r if isinstance(r, dict) else {}


@dataclass(frozen=True)
class Root:
    key: str                              # sources.root：ws.<agent> / digest / x.<名字>
    path: Path                            # 目录
    kind: str                             # ws / digest / extra
    only: str | None = None               # extra 写的是一个文件：只收它（文件名）
    agents: frozenset[str] | None = None  # extra 给哪几个 Agent；None = 都给
    name: str = ""                        # extra 的名字（出处的前缀）；ws 是 Agent id

    def label(self, me: str) -> str:
        """出处的前缀：自己的工作区不写；别人的工作区（主对话放开时）@<id>；日结 digest；共享项它的名字。"""
        if self.kind == "ws":
            return "" if self.name == me else f"@{self.name}"
        return "digest" if self.kind == "digest" else self.name


def ws_roots() -> dict[str, Root]:
    """每个 Agent 的工作区：主对话是 settings.workspace，别的是 agent_workspaces 里有的（没有自己工作区的 Agent 没有这一块）。"""
    out = {"main": Root("ws.main", settings.workspace, "ws", name="main")}
    for aid, p in settings.agent_workspaces.items():
        if aid != "main":
            out[aid] = Root(f"ws.{aid}", p, "ws", name=aid)
    return out


def digest_root() -> Root:
    return Root("digest", settings.data_dir / "digest", "digest", name="digest")


RESERVED = {"chat", "digest"}


def extras() -> list[Root]:
    """engine.recall.extra：["路径", {"path", "name"?, "agents"?: [...]}]。不写 = 档案 USER.md（有的话）。路径不存在的跳过。"""
    items = config().get("extra")
    if items is None:
        items = [str(settings.profile)] if settings.profile.is_file() else []
    out: list[Root] = []
    used = set(RESERVED) | ({"tree"} if _tree_on() else set())  # 世界树的叶子进了索引时 tree:<id> 是叶子
    for it in items if isinstance(items, list) else []:
        d = {"path": it} if isinstance(it, str) else it if isinstance(it, dict) else None
        if not d or not d.get("path"):
            continue
        p = Path(str(d["path"])).expanduser()
        is_file = p.is_file()
        if not is_file and not p.is_dir():
            continue
        base = str(d.get("name") or (p.parent.name if is_file else p.name) or "extra")
        name = re.sub(r"[\s:#@/\\]+", "-", base).strip("-") or "extra"
        n, i = name, 2
        while n in used:
            n, i = f"{name}-{i}", i + 1
        used.add(n)
        ag = d.get("agents")
        agents = frozenset(str(a) for a in ag) if isinstance(ag, list) else None
        out.append(Root(f"x.{n}", p.parent if is_file else p, "extra", only=p.name if is_file else None, agents=agents, name=n))
    return out


def all_roots() -> list[Root]:
    return [*ws_roots().values(), digest_root(), *extras()]


def _semantic_on() -> bool:
    """配了 engine.recall.embeddings（按意思搜）。没配的时候这个文件里的一切和以前一样。"""
    from . import embeddings
    return embeddings.spec() is not None


def _tree_on() -> bool:
    """世界树的叶子进不进索引：按意思搜开着、engine.recall.tree 没关。"""
    return _semantic_on() and config().get("tree") is not False


@dataclass
class Scope:
    agent: str
    roots: list[Root]      # 文件来源：自己的工作区（主对话放开时：所有工作区）、给它的共享项；日结按对话另算
    all_threads: bool      # 主对话放开时：所有对话和日结

    def owns(self, thread: str, cache: dict[str, str]) -> bool:
        return self.all_threads or owner_of(thread, cache) == self.agent


def scope(agent: str | None) -> Scope:
    me = agent or "main"
    everything = me == "main" and str(config().get("main") or "own").strip().lower() == "all"
    wss = ws_roots()
    roots = list(wss.values()) if everything else [wss[me]] if me in wss else []
    roots += [x for x in extras() if x.agents is None or me in x.agents]
    return Scope(me, roots, everything)


def owner_of(thread: str, cache: dict[str, str]) -> str:
    """这个对话归哪个 Agent：和内核跑主人回合时一样（owner.agent_of：main、Agent 自己的对话、学习台 → study.agent，别的归 main）。
    不加兜底：导入不了宁可报错，也不能把别人的对话算成谁的。"""
    if thread not in cache:
        from .owner import agent_of
        cache[thread] = agent_of(thread)
    return cache[thread]


# —— 库 ——————————————————————————————————————————————————————————————————————

def db_path() -> Path:
    return Path(str(config().get("db") or settings.data_dir / "recall.db")).expanduser()


def _version() -> str:
    return f"{SCHEMA}/{unicodedata.unidata_version}/{TOKENIZE}"


DDL = f"""
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS roots (key TEXT PRIMARY KEY, path TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sources (
  id INTEGER PRIMARY KEY AUTOINCREMENT, root TEXT NOT NULL, path TEXT NOT NULL, thread TEXT, day TEXT, ts TEXT, role TEXT,
  mtime INTEGER, size INTEGER, sha TEXT, UNIQUE(root, path));
CREATE INDEX IF NOT EXISTS sources_thread ON sources(thread);
CREATE TABLE IF NOT EXISTS chunks (
  id INTEGER PRIMARY KEY AUTOINCREMENT, source INTEGER NOT NULL, start_line INTEGER NOT NULL, end_line INTEGER NOT NULL,
  heading TEXT NOT NULL DEFAULT '', text TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS chunks_source ON chunks(source);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(body, title, tokenize='{TOKENIZE}');
CREATE TABLE IF NOT EXISTS leaves (source INTEGER PRIMARY KEY, branch TEXT, author TEXT NOT NULL DEFAULT '', kind TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT '', trust REAL, quote TEXT NOT NULL DEFAULT '', platform TEXT NOT NULL DEFAULT '', thread TEXT NOT NULL DEFAULT '',
  message TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS leaves_branch ON leaves(branch);
CREATE TABLE IF NOT EXISTS vec (chunk INTEGER PRIMARY KEY, sha TEXT NOT NULL, v BLOB NOT NULL);
CREATE INDEX IF NOT EXISTS vec_sha ON vec(sha);
CREATE TABLE IF NOT EXISTS vec_bits (chunk INTEGER PRIMARY KEY, source INTEGER NOT NULL, bits BLOB NOT NULL);
"""


def _open(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    fresh = not path.exists()
    conn = sqlite3.connect(path, timeout=15, check_same_thread=False)
    if fresh:
        with contextlib.suppress(OSError):
            os.chmod(path, 0o600)  # 里面是记忆和对话的原文：和 mousse.db 一样只给自己读
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA secure_delete=ON")
    with contextlib.suppress(sqlite3.Error):
        conn.execute("PRAGMA journal_mode=WAL")  # 重建、同步时别的进程照样能搜（读旧的那一版）
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _schema(conn: sqlite3.Connection) -> None:
    """建表；格式版本对不上就全删了重建（只在写锁里调）。"""
    conn.executescript("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);")
    row = conn.execute("SELECT v FROM meta WHERE k='version'").fetchone()
    if row and row[0] == _version():
        conn.executescript(DDL)
        have = {r[1] for r in conn.execute("PRAGMA table_info(leaves)")}
        for col in ("quote", "platform", "thread", "message"):  # 叶子的出处（10/17）：老库补列就行，不用整个重建（向量不重算）
            if col not in have:
                conn.execute(f"ALTER TABLE leaves ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")
        return
    conn.executescript("DROP TABLE IF EXISTS chunks_fts; DROP TABLE IF EXISTS chunks; DROP TABLE IF EXISTS sources; "
                       "DROP TABLE IF EXISTS roots; DROP TABLE IF EXISTS vec; DROP TABLE IF EXISTS vec_bits; DROP TABLE IF EXISTS leaves; "
                       "DELETE FROM meta;")
    conn.executescript(DDL)
    with contextlib.suppress(sqlite3.OperationalError):  # SQLite 3.42 起：删掉的词条马上从索引里清掉
        conn.execute("INSERT INTO chunks_fts(chunks_fts, rank) VALUES('secure-delete', 1)")
    conn.execute("INSERT OR REPLACE INTO meta(k, v) VALUES('version', ?)", (_version(),))
    conn.commit()


@contextlib.contextmanager
def _writer() -> Iterator[sqlite3.Connection]:
    """同步 / 重建：进程里一把锁 + 跨进程的文件锁（命令行和服务同时跑也只有一个在写）。"""
    path = db_path()
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(f"{path}.lock", "a+") as lk:
            if fcntl is not None:
                fcntl.flock(lk, fcntl.LOCK_EX)
            conn = _open(path)
            try:
                _schema(conn)
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
            finally:
                conn.close()


@contextlib.contextmanager
def _reader() -> Iterator[sqlite3.Connection]:
    conn = _open(db_path())
    try:
        yield conn
    finally:
        conn.close()


def _meta(conn: sqlite3.Connection, k: str) -> str | None:
    row = conn.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
    return row[0] if row else None


def _set_meta(conn: sqlite3.Connection, k: str, v: str) -> None:
    conn.execute("INSERT OR REPLACE INTO meta(k, v) VALUES(?,?)", (k, v))


# —— 切段 ——————————————————————————————————————————————————————————————————
_HEAD = re.compile(r"^#{1,6}\s+(.*)$")
_ENTRY = re.compile(r"^(?:[-*+]\s|#{1,6}\s)")


def _pieces(line: str) -> list[str]:
    """一行比一段还长：按字节切成几块（同一个行号），不切开一个字。"""
    if len(line.encode()) <= CHUNK:
        return [line]
    out, cur, n = [], [], 0
    for ch in line:
        b = len(ch.encode())
        if n + b > CHUNK and cur:
            out.append("".join(cur))
            cur, n = [], 0
        cur.append(ch)
        n += b
    out.append("".join(cur))
    return out


def chunk_lines(text: str, curated: bool = False) -> list[tuple[int, int, str, str]]:
    """→ [(起始行, 结束行, 所在的标题, 这一段的字)]。照 OpenClaw 的 chunkMarkdown：一行行攒到约 400 token 一段，下一段带上前一段末尾
    约 80 token 的几行；遇到标题另起一段。curated（MEMORY.md、USER.md，OpenClaw 的 perEntry）每条「- 」、每个标题一段，不重叠。"""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list[tuple[int, int, str, str]] = []
    cur: list[tuple[int, str]] = []
    size, heading, head = 0, "", ""

    def flush() -> None:
        keep = [i for i, (_, t) in enumerate(cur) if t.strip()]
        if keep:  # 两头的空行不算（行号只框住有字的）
            part = cur[keep[0]:keep[-1] + 1]
            out.append((part[0][0], part[-1][0], head, "\n".join(t for _, t in part)))

    for no, line in enumerate(lines, 1):
        m = _HEAD.match(line)
        if cur and (m or (curated and _ENTRY.match(line))):
            flush()
            cur, size = [], 0
        if m:
            heading = m.group(1).strip()[:120]
        if not cur:
            head = heading
        for piece in _pieces(line):
            n = len(piece.encode()) + 1
            if cur and size + n > CHUNK:
                flush()
                keep: list[tuple[int, str]] = []
                k = 0
                if not curated:
                    for item in reversed(cur):
                        k += len(item[1].encode()) + 1
                        if k > OVERLAP:
                            break
                        keep.insert(0, item)
                cur, size, head = keep, sum(len(t.encode()) + 1 for _, t in keep), heading
            cur.append((no, piece))
            size += n
    flush()
    return out


# —— 同步 ——————————————————————————————————————————————————————————————————

def _ok_name(name: str) -> bool:
    return not name.startswith(".")


def allowed(r: Root, rel: str) -> bool:
    """这个相对路径是不是这个来源会收的文件（同步和 memory_get 用同一套规矩：memory_get 不能变成读任意文件）。"""
    parts = rel.split("/")
    if not rel or rel.startswith("/") or "\x00" in rel or any(p in ("", ".", "..") or not _ok_name(p) for p in parts):
        return False
    if r.kind == "ws":
        return rel in ("MEMORY.md", "USER.md") or (parts[0] == "memory" and len(parts) > 1 and rel.lower().endswith(".md"))
    if r.kind == "digest":
        return len(parts) == 2 and rel.lower().endswith(".md")
    return rel == r.only if r.only else rel.lower().endswith(TEXT_EXT)


def _regular(p: Path) -> os.stat_result | None:
    """普通文件、不是软链、不太大 → lstat；否则 None（OpenClaw 也跳过软链：别借软链读到工作区外面的文件）。"""
    try:
        st = p.lstat()
    except OSError:
        return None
    return st if stat.S_ISREG(st.st_mode) and st.st_size <= MAX_FILE else None


def walk(r: Root) -> Iterator[tuple[str, Path, os.stat_result]]:
    """一个来源里要收的文件 → (相对路径, 路径, lstat)。隐藏的、软链（文件和文件夹）、太大的不收。"""
    base = r.path
    if not base.is_dir():
        return
    if r.only:
        st = _regular(base / r.only)
        if st:
            yield r.only, base / r.only, st
        return
    tops = [base / "memory"] if r.kind == "ws" else [base]
    if r.kind == "ws":
        for name in ("MEMORY.md", "USER.md"):
            st = _regular(base / name)
            if st:
                yield name, base / name, st
    count = 0
    for top in tops:
        if top.is_symlink() or not top.is_dir():
            continue
        for d, dirs, files in os.walk(top, followlinks=False):
            dirs[:] = sorted(x for x in dirs if _ok_name(x) and not os.path.islink(os.path.join(d, x)))
            for f in sorted(files):
                p = Path(d) / f
                rel = p.relative_to(base).as_posix()
                if not allowed(r, rel):
                    continue
                st = _regular(p)
                if st is None:
                    continue
                yield rel, p, st
                count += 1
                if count >= MAX_FILES:
                    return


def _drop_source(conn: sqlite3.Connection, sid: int) -> None:
    conn.execute("DELETE FROM chunks_fts WHERE rowid IN (SELECT id FROM chunks WHERE source=?)", (sid,))
    conn.execute("DELETE FROM vec WHERE chunk IN (SELECT id FROM chunks WHERE source=?)", (sid,))  # 按意思搜的向量（engine/semantic.py）
    conn.execute("DELETE FROM vec_bits WHERE chunk IN (SELECT id FROM chunks WHERE source=?)", (sid,))  # 按主键删：source 上没索引
    conn.execute("DELETE FROM leaves WHERE source=?", (sid,))
    conn.execute("DELETE FROM chunks WHERE source=?", (sid,))
    conn.execute("DELETE FROM sources WHERE id=?", (sid,))


def _drop_root(conn: sqlite3.Connection, key: str) -> int:
    n = conn.execute("SELECT count(*) FROM sources WHERE root=?", (key,)).fetchone()[0]
    conn.execute("DELETE FROM chunks_fts WHERE rowid IN (SELECT c.id FROM chunks c JOIN sources s ON s.id = c.source WHERE s.root=?)", (key,))
    conn.execute("DELETE FROM vec WHERE chunk IN (SELECT c.id FROM chunks c JOIN sources s ON s.id = c.source WHERE s.root=?)", (key,))
    conn.execute("DELETE FROM vec_bits WHERE chunk IN (SELECT c.id FROM chunks c JOIN sources s ON s.id = c.source WHERE s.root=?)", (key,))
    conn.execute("DELETE FROM leaves WHERE source IN (SELECT id FROM sources WHERE root=?)", (key,))
    conn.execute("DELETE FROM chunks WHERE source IN (SELECT id FROM sources WHERE root=?)", (key,))
    conn.execute("DELETE FROM sources WHERE root=?", (key,))
    conn.execute("DELETE FROM roots WHERE key=?", (key,))
    return n


_GENERIC = {"memory", "user", "readme", "index"}


def _title(rel: str, heading: str) -> str:
    """FTS 的 title 栏：文件名（日期、MEMORY / USER 这种不算）+ 所在的标题。对话没有 title（不然整个对话的每一条都对上对话名）。"""
    stem = Path(rel).stem
    stem = "" if _DATED.match(stem) or stem.lower() in _GENERIC else stem
    return segment(f"{stem} {heading}".strip())


def _add_chunks(conn: sqlite3.Connection, sid: int, pieces: list[tuple[int, int, str, str]], rel: str | None, root: str = "chat",
                keep: dict | None = None) -> int:
    """keep：这个来源重切之前的向量（semantic.stash）；字没变的段直接接着用，不再发去算。"""
    for a, b, heading, body in pieces:
        cid = conn.execute("INSERT INTO chunks(source, start_line, end_line, heading, text) VALUES(?,?,?,?,?)",
                           (sid, a, b, heading, body)).lastrowid
        conn.execute("INSERT INTO chunks_fts(rowid, body, title) VALUES(?,?,?)", (cid, segment(body), _title(rel, heading) if rel else ""))
        if keep:
            from . import semantic
            semantic.reuse(conn, cid, sid, semantic.embed_input(root, rel, heading, body), keep)
    return len(pieces)


def _sync_root(conn: sqlite3.Connection, r: Root, stats: dict) -> None:
    want = f"{r.path}|{r.only or ''}"
    prev = conn.execute("SELECT path FROM roots WHERE key=?", (r.key,)).fetchone()
    if prev and prev[0] != want:  # 工作区或共享项换了地方：旧的整个清掉
        stats["removed"] += _drop_root(conn, r.key)
    conn.execute("INSERT OR REPLACE INTO roots(key, path) VALUES(?,?)", (r.key, want))
    known = {row["path"]: row for row in conn.execute("SELECT id, path, mtime, size, sha FROM sources WHERE root=?", (r.key,))}
    seen: set[str] = set()
    for rel, p, st in walk(r):
        seen.add(rel)
        k = known.get(rel)
        if k is not None and k["mtime"] == st.st_mtime_ns and k["size"] == st.st_size:
            continue
        try:
            text = p.read_text(encoding="utf8", errors="replace")
        except OSError:
            continue  # 这次读不了：旧的先留着
        sha = hashlib.sha1(text.encode(), usedforsecurity=False).hexdigest()
        if k is not None and k["sha"] == sha:  # 只是 mtime 变了
            conn.execute("UPDATE sources SET mtime=?, size=? WHERE id=?", (st.st_mtime_ns, st.st_size, k["id"]))
            continue
        keep = None
        if k is not None:
            from . import semantic
            keep = semantic.stash(conn, k["id"])  # 按意思搜开着时：没改的那几段的向量接着用
            _drop_source(conn, k["id"])
        thread = rel.split("/", 1)[0] if r.kind == "digest" else None
        ts = datetime.fromtimestamp(st.st_mtime, settings.tz).isoformat(timespec="seconds")
        sid = conn.execute("INSERT INTO sources(root, path, thread, day, ts, role, mtime, size, sha) VALUES(?,?,?,?,?,?,?,?,?)",
                           (r.key, rel, thread, day_in_name(rel), ts, None, st.st_mtime_ns, st.st_size, sha)).lastrowid
        curated = r.kind != "digest" and Path(rel).name in ("MEMORY.md", "USER.md")
        stats["chunks"] += _add_chunks(conn, sid, chunk_lines(text, curated), rel, r.key, keep)
        stats["files"] += 1
    for rel, k in known.items():
        if rel not in seen:
            _drop_source(conn, k["id"])
            stats["removed"] += 1


BATCH = 2000


def _sync_chat(conn: sqlite3.Connection, stats: dict) -> None:
    """mousse.db 的 messages → 索引。按 id 往后接；删掉的（app 里删除、撤回、删项目）靠 count / sum(id) 发现，再对一遍 id 清掉。
    每次只读一小批（不在 mousse.db 上长时间占着读锁：老的 rollback 日志模式下会挡住 app 写）。"""
    path = settings.db
    if not path.exists():
        return
    try:
        src = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    except sqlite3.Error:
        return
    try:
        if not src.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='messages'").fetchone():
            return
        if _meta(conn, "chat_db") != str(path):  # 换了一个库：从头来
            stats["removed"] += _drop_root(conn, "chat")
            for k in ("chat_last", "chat_fp"):
                conn.execute("DELETE FROM meta WHERE k=?", (k,))
            _set_meta(conn, "chat_db", str(path))
        last = int(_meta(conn, "chat_last") or 0)
        try:  # messages 是 AUTOINCREMENT：sqlite_sequence 记着用过的最大 id（删掉最新一条不会让它变小）
            seq = src.execute("SELECT seq FROM sqlite_sequence WHERE name='messages'").fetchone()
        except sqlite3.Error:
            seq = None
        top = seq[0] if seq else src.execute("SELECT coalesce(max(id), 0) FROM messages").fetchone()[0]
        if top < last:  # 库倒回去了（从备份恢复）：id 可能被重新用过，从头来。恢复成内容不同的同一批 id 时用 reindex --full
            stats["removed"] += _drop_root(conn, "chat")
            last = 0
        cnt, total = src.execute("SELECT count(*), coalesce(sum(id), 0) FROM messages WHERE id<=?", (last,)).fetchone()
        if f"{cnt}:{total}" != (_meta(conn, "chat_fp") or "0:0"):
            alive = {r[0] for r in src.execute("SELECT id FROM messages WHERE id<=?", (last,))}
            for sid, mid in conn.execute("SELECT id, path FROM sources WHERE root='chat'").fetchall():
                if int(mid) not in alive:
                    _drop_source(conn, sid)
                    stats["removed"] += 1
            cnt, total = len(alive), sum(alive)
        while True:
            rows = src.execute("SELECT id, thread, role, text, ts, status FROM messages WHERE id>? ORDER BY id LIMIT ?",
                               (last, BATCH)).fetchall()
            if not rows:
                break
            for mid, thread, role, text, ts, status in rows:
                cnt, total, last = cnt + 1, total + mid, mid  # 指纹按读到的每一行算（读完以后再被删的，下次就对不上）
                if role not in CHAT_ROLES or (role == "grava" and status == "error") or not (text or "").strip():
                    continue
                cur = conn.execute("INSERT OR IGNORE INTO sources(root, path, thread, day, ts, role, size) VALUES('chat',?,?,?,?,?,?)",
                                   (str(mid), thread, day_of_ts(ts), ts, role, len(text)))
                if cur.rowcount == 1:  # 已经有了的不再加（lastrowid 在没插进去时是上一次的，不能用）
                    stats["chunks"] += _add_chunks(conn, cur.lastrowid, chunk_lines(text), None)
                    stats["messages"] += 1
        _set_meta(conn, "chat_last", str(last))
        _set_meta(conn, "chat_fp", f"{cnt}:{total}")
    finally:
        src.close()


def _corrupt(e: sqlite3.Error) -> bool:
    """库文件坏了（不是锁住、不是写法不对）：索引是从来源算出来的，删了重建就好。"""
    return isinstance(e, sqlite3.DatabaseError) and not isinstance(e, sqlite3.OperationalError)


def sync(agent: str | None = None) -> dict:
    """把来源增量同步进索引 → 这次的统计。agent 给了只同步它范围里的文件来源；日结和对话每次都全同步（便宜，归属在搜的时候才算）。
    配置里已经没有的来源（拿掉的共享项、删掉的 Agent 的工作区）整个清掉。索引文件坏了：删掉，从头建一次。"""
    try:
        return _sync(agent)
    except sqlite3.DatabaseError as e:
        if not _corrupt(e):
            raise
        _wipe()
        return _sync(agent)


def _sync(agent: str | None) -> dict:
    stats = {"files": 0, "messages": 0, "chunks": 0, "removed": 0}
    roots = all_roots() if agent is None else [*scope(agent).roots, digest_root()]
    keys = {r.key for r in all_roots()} | {"chat"}
    from . import embeddings
    spec = embeddings.spec()
    snap = why = None
    if spec is not None and config().get("tree") is not False:  # 按意思搜开着：世界树的叶子也进来（在写锁外面先读好）
        from . import recall_tree
        snap, why = recall_tree.read()
        if snap is not None or why:  # 读不了：旧的叶子先留着；没接世界树：清掉
            keys.add(recall_tree.ROOT)
    t0 = time.monotonic()
    with _writer() as conn:
        for (key,) in conn.execute("SELECT key FROM roots").fetchall():
            if key not in keys:
                stats["removed"] += _drop_root(conn, key)
        for r in roots:
            _sync_root(conn, r, stats)
        _sync_chat(conn, stats)
        if snap is not None:
            from . import recall_tree
            recall_tree.apply(conn, snap, stats)
        elif why:
            from . import recall_tree
            recall_tree.note_error(conn, why)
        from . import semantic
        stats["removed"] += semantic.check(conn, spec)  # 换了型号：向量整个重算；配置拿掉了：留着不用
        _set_meta(conn, "synced_at", datetime.now(settings.tz).isoformat(timespec="seconds"))
        conn.commit()
        if stats["removed"]:  # 删掉的旧页别留在 WAL 里（和 secure_delete 一起：忘掉的字不留在磁盘上）
            with contextlib.suppress(sqlite3.Error):
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    stats["ms"] = int((time.monotonic() - t0) * 1000)
    return stats


def _wipe() -> None:
    """删掉索引文件（拿着写锁）。"""
    path = db_path()
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(f"{path}.lock", "a+") as lk:
            if fcntl is not None:
                fcntl.flock(lk, fcntl.LOCK_EX)
            for suffix in ("", "-wal", "-shm"):
                with contextlib.suppress(FileNotFoundError):
                    os.remove(f"{path}{suffix}")


def rebuild() -> dict:
    """删掉索引从头建（忘记一切旧的：连 FTS 的段和空闲页都不留）。"""
    _wipe()
    return sync()


# —— 搜 ——————————————————————————————————————————————————————————————————————

@dataclass
class Hit:
    chunk: int
    source: int
    root: str
    path: str
    thread: str | None
    day: str | None
    ts: str | None
    role: str | None
    start: int
    end: int
    heading: str
    text: str
    score: float = 0.0
    # 下面的只有按意思搜开着时才有（engine/semantic.py）
    match: str = ""                # words = 所有词都对上了；meaning = 按意思（和部分词）找到的；some = 只对上部分词（还没有向量）
    branch: str | None = None      # 世界树的叶子：挂在哪一枝（存的名字）
    place: str = ""                # 给人看的枝名（不认识的枝、没挂枝的算主干）
    author: str = ""               # 叶子是谁记的（世界树的 source）
    kind: str = ""
    status: str = ""
    trust: float | None = None     # 叶子自己记着的 trust（世界树的 tree 表有 trust 列才有；乘数，盖过按档算的）
    tier: str = ""                 # 13d 的「信几分」：quoted（用户自己写的、带用户原话的）/ inferred（engine/memgate.py）


_COLS = ("c.id, c.source, c.start_line, c.end_line, c.heading, c.text, s.root, s.path, s.thread, s.day, s.ts, s.role")


def _hit(r: sqlite3.Row, score: float, leaves: dict | None = None, places: tuple[list, str] | None = None) -> Hit:
    h = Hit(r["id"], r["source"], r["root"], r["path"], r["thread"], r["day"], r["ts"], r["role"], r["start_line"], r["end_line"],
            r["heading"], r["text"], score)
    m = (leaves or {}).get(r["source"])
    if m is not None:
        from . import recall_tree
        h.branch, h.author, h.kind, h.status, h.trust = m["branch"], m["author"], m["kind"], m["status"], m["trust"]
        h.place = recall_tree.branch_label(h.branch, *(places or ([], "")))
        h.tier = recall_tree.tier_of(h.author or "", m["quote"] or "", m["platform"] or "")
    return h


def _decay(h: Hit, now: date, half: float) -> float:
    """时间：照 _weight；世界树的叶子只有 event 类（发生在哪天的事）按日子减，别的（偏好、决定、事实）常青。"""
    return 1.0 if h.root == "tree" and h.kind != "event" else _weight(h.day, now, half)


def _rows(conn: sqlite3.Connection, match: str | None, likes: list[str], unlikes: list[str], where: list[str], params: list,
          n: int) -> list[tuple[sqlite3.Row, float]]:
    """一组查询 → [(行, 相关度)]。有 MATCH 的按 BM25（body 1.0、title 0.5）；只有 LIKE 的按出现几次。"""
    w, p = list(where), list(params)
    for t in likes:  # 存的是原文：中日韩的字 NFKC 前后一样，对得上（全角数字字母混在单字词里的少见，对不上就算了）
        w.append("c.text LIKE ? ESCAPE '\\'")
        p.append(f"%{escape_like(t)}%")
    for t in unlikes:
        w.append("c.text NOT LIKE ? ESCAPE '\\'")
        p.append(f"%{escape_like(t)}%")
    cond = " AND ".join(w) or "1"
    if match:
        sql = (f"SELECT {_COLS}, bm25(chunks_fts, 1.0, 0.5) AS bm FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid "
               f"JOIN sources s ON s.id = c.source WHERE chunks_fts MATCH ? AND {cond} ORDER BY bm LIMIT ?")
        try:
            rows = conn.execute(sql, [match, *p, n]).fetchall()
        except sqlite3.OperationalError:  # 查询写法 FTS5 不认（拼出来的词都加了引号，按说不会）：当没对上
            return []
        return [(r, max(0.0, -float(r["bm"]))) for r in rows]
    sql = (f"SELECT {_COLS} FROM chunks c JOIN sources s ON s.id = c.source WHERE {cond} "
           "ORDER BY s.day IS NULL, s.day DESC, c.id DESC LIMIT ?")
    out = []
    for r in conn.execute(sql, [*p, n]).fetchall():
        low = norm(r["text"]).lower()
        hits = sum(low.count(t.lower()) for t in likes)
        out.append((r, 0.5 * min(hits, 5)))
    return out


def _plans(groups: list[list[Term]]) -> list[tuple[str | None, list[str], list[str]]]:
    """几组 → 要跑的查询 [(MATCH, LIKE 要有的, LIKE 不能有的)]。都能走索引的合成一个 MATCH（组之间 OR，FTS5 自己按 BM25 加）；
    带单字词的组各跑各的（SQLite 的 MATCH 不能和别的条件放进同一个 OR）。"""
    def expr(g: list[Term]) -> str | None:
        pos = [t.fts() for t in g if not t.neg and not t.like]
        if not pos:
            return None
        e = " AND ".join(pos)
        for t in g:
            if t.neg and not t.like:
                e = f"({e}) NOT {t.fts()}"
        return e

    if all(not t.like for g in groups for t in g):
        exprs = [e for e in (expr(g) for g in groups) if e]
        return [(" OR ".join(f"({e})" for e in exprs), [], [])] if exprs else []
    out = []
    for g in groups:
        m = expr(g)
        likes = [t.text for t in g if not t.neg and (t.like or m is None)]
        unlikes = [t.text for t in g if t.neg and (t.like or m is None)]
        if m or likes:
            out.append((m, likes, unlikes))
    return out


def _weight(day: str | None, now: date, half: float) -> float:
    """时间：带日期的 half 天减半、最少留 FLOOR；常青的不减（OpenClaw 的 temporalDecay，加了下限：关键词检索靠相关度说话）。"""
    if not day or half <= 0:
        return 1.0
    try:
        age = max(0, (now - date.fromisoformat(day)).days)
    except ValueError:
        return 1.0
    return FLOOR + (1 - FLOOR) * 0.5 ** (age / half)


def _toks(text: str) -> set[str]:
    return set(segment(text).lower().split())


def _same(a: set[str], b: set[str]) -> bool:
    """几乎一样：词的 Jaccard ≥ 0.8（同一件事日记里一份、日结里一份；相邻两段只重叠两成，不算）。"""
    return bool(a or b) and len(a & b) >= 0.8 * len(a | b)


def _ord(day: str | None) -> int:
    try:
        return date.fromisoformat(day).toordinal() if day else 0
    except ValueError:
        return 0


def _pick(hits: list[Hit], limit: int, keep_order: bool = False) -> tuple[list[Hit], int]:
    """按分数挑出 limit 条，先求广（OpenClaw 用 MMR 防一样的挤满）：第一遍每个文件、每条消息只要最好的一段，同一个对话同一天最多三条，
    和已挑的几乎一样的不要；没挑满再给文件补第二段（和已挑的那段行号不重叠）。一条消息只出一次（长消息切成几段也是）。
    → (挑出的（按分数；keep_order = 按传进来的顺序，MMR 排过的）, 没列出的还有几个出处)。"""
    order = {id(h): i for i, h in enumerate(hits)}
    out: list[Hit] = []
    spans: dict[int, list[tuple[int, int]]] = {}
    per_day: dict[tuple, int] = {}
    sigs: list[set[str]] = []
    for cap in (1, 2):
        for h in hits:
            if len(out) >= limit:
                break
            got = spans.get(h.source, [])
            if len(got) >= cap or (got and h.root == "chat") or any(h.start <= b and a <= h.end for a, b in got):
                continue
            if h.root == "chat" and per_day.get((h.thread, h.day), 0) >= 3:
                continue
            sig = _toks(h.text)
            if any(_same(sig, s) for s in sigs):
                continue
            out.append(h)
            sigs.append(sig)
            spans.setdefault(h.source, []).append((h.start, h.end))
            if h.root == "chat":
                per_day[(h.thread, h.day)] = per_day.get((h.thread, h.day), 0) + 1
    out.sort(key=(lambda h: order[id(h)]) if keep_order else (lambda h: (-h.score, -_ord(h.day), -h.chunk)))
    return out, len({h.source for h in hits} - set(spans))


@dataclass
class Found:
    hits: list[Hit]
    more: int          # 符合但没列出的
    relaxed: bool      # 是放宽成「任一个词」以后才有的
    terms: list[str]   # 要有的词（标命中的行用）
    scope: Scope
    mode: str = "keyword"   # keyword = 只按关键词（没开按意思搜）；hybrid = 意思和关键词一起；fallback = 开了，但这一次没算成向量
    why: str | None = None  # fallback 的原因（给状态和日志，不给模型）


def search(query: str, agent: str | None = "main", *, limit: int = LIMIT, after=None, before=None, source: str = "all",
           exclude: tuple[str, str | int] | None = None, refresh: bool = True) -> Found:
    """搜这个 Agent 范围里的记忆和对话。exclude = 已经在上下文里的那段对话，不搜：(对话, 第一条还在上下文里的 app 消息 id)，
    或者 (对话, 逻辑日) = 这个对话那一天的都不搜（找不到这一轮的会话时）。
    refresh：先把范围里的来源增量同步一遍。配了 engine.recall.embeddings：搜的那一句先算向量，意思和关键词一起排（_hybrid）；
    算不出来就和没配时一样只按关键词（mode = fallback）。"""
    sc = scope(agent)
    from . import embeddings
    spec = embeddings.spec()
    groups = parse(query, upper_ops=spec is not None)
    if not groups:
        raise RecallError(L("query 里没有能搜的词", "There's nothing to search for in query"))
    if source not in ("all", "memory", "chats"):
        raise RecallError(L("source 只能是 all、memory、chats", "source must be all, memory or chats"))
    a, b = day_bound(after), day_bound(before)
    limit = max(1, min(int(limit or LIMIT), LIMIT_MAX))
    synced = sync(sc.agent) if refresh else {}
    qv = why = None
    if spec is not None:
        from . import semantic
        if synced.get("chunks"):
            semantic.kick()  # 刚同步进来了新的段：后台去补向量（这一次搜不等它；没有新的就等它自己的钟点）
        qv, why = semantic.query(spec, " ".join(t.text for g in groups for t in g if not t.neg))  # 去掉 OR / NOT 和不要的词
    half = float(config().get("half_life_days", HALF_LIFE) or 0)
    now = date.fromisoformat(today())
    terms = [t.text for g in groups for t in g if not t.neg]
    try:
        with _reader() as conn:
            _schema_ok(conn)
            cache: dict[str, str] = {}
            threads = None if sc.all_threads else [t for (t,) in conn.execute("SELECT DISTINCT thread FROM sources WHERE thread IS NOT NULL")
                                                    if sc.owns(t, cache)]
            where, params = _scope_sql(sc, threads, source, conn if spec is not None and config().get("tree") is not False else None)
            if a:
                where.append("s.day >= ?")
                params.append(a)
            if b:
                where.append("s.day < ?")
                params.append(b)
            if exclude and isinstance(exclude[1], int):
                where.append("NOT (s.root = 'chat' AND s.thread = ? AND CAST(s.path AS INTEGER) >= ?)")
                params += list(exclude)
            elif exclude:
                where.append("NOT (s.root = 'chat' AND s.thread = ? AND s.day = ?)")
                params += list(exclude)
            if qv is not None:
                picked, more = _hybrid(conn, groups, where, params, limit, qv, spec, sc, now, half)
                return Found(picked, more, False, terms, sc, "hybrid")
            found, was_relaxed = _collect(conn, groups, where, params, limit), False
            if not found and (loose_ := relaxed(groups)):
                found, was_relaxed = _collect(conn, loose_, where, params, limit), True
            leaves, places = _leaves(conn, [r for r, _ in found.values()]) if spec is not None else ({}, None)
    except sqlite3.DatabaseError as e:
        if not _corrupt(e):
            raise
        _wipe()
        raise RecallError(L("记忆索引坏了，已经删掉；再搜一次会从头建", "The memory index was damaged and has been removed; search again to rebuild it")) from None
    hits = []
    for r, rel in found.values():
        h = _hit(r, rel, leaves, places)
        h.score = rel * _decay(h, now, half)
        hits.append(h)
    hits.sort(key=lambda h: (-h.score, -_ord(h.day), -h.chunk))
    picked, more = _pick(hits, limit)
    return Found(picked, more, was_relaxed, terms, sc, "keyword" if spec is None else "fallback", why)


def _leaves(conn: sqlite3.Connection, rows: list[sqlite3.Row]) -> tuple[dict, tuple[list, str] | None]:
    """这几行里世界树的叶子：(来源 → leaves 的那一行, (枝, 主干的名字))。"""
    sids = [r["source"] for r in rows if r["root"] == "tree"]
    if not sids:
        return {}, None
    from . import recall_tree
    return recall_tree.meta_of(conn, sids), recall_tree.branches(conn)


def _rows_of(conn: sqlite3.Connection, ids: list[int]) -> dict[int, sqlite3.Row]:
    out: dict[int, sqlite3.Row] = {}
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        for r in conn.execute(f"SELECT {_COLS} FROM chunks c JOIN sources s ON s.id = c.source WHERE c.id IN ({','.join('?' * len(part))})", part):
            out[r["id"]] = r
    return out


def _profile_roots(sc: Scope) -> set[str]:
    """哪几个共享项就是档案（settings.profile）：它们的段「进了档案」，按意思搜时往前排。"""
    try:
        prof = os.path.realpath(settings.profile)
    except (OSError, ValueError):
        return set()
    return {r.key for r in sc.roots if r.kind == "extra" and r.only and os.path.realpath(r.path / r.only) == prof}


def _hybrid(conn: sqlite3.Connection, groups: list[list[Term]], where: list[str], params: list, limit: int, qv: list[float], spec,
            sc: Scope, now: date, half: float) -> tuple[list[Hit], int]:
    """按意思和关键词一起排（engine/semantic.py 开头的 2–6）→ (挑出的, 没列出的还有几个出处)。"""
    from . import semantic
    n = max(limit * 6, 40)
    strict = _collect(conn, groups, where, params, limit)
    wide = _collect(conn, lg, where, params, limit) if (lg := loose(groups)) else {}
    if not wide and (rx := relaxed(groups)):
        wide = _collect(conn, rx, where, params, limit)
    kw = set(strict) | set(wide)
    vs = semantic.vector_scores(conn, qv, where, params, n, kw)
    rows = {cid: got[0] for cid, got in (*wide.items(), *strict.items())}
    rows |= _rows_of(conn, [c for c in vs if c not in rows])
    leaves, places = _leaves(conn, list(rows.values()))
    negs = [t.text.lower() for g in groups for t in g if t.neg]
    prof = _profile_roots(sc)
    w = spec.vector_weight
    hits: list[Hit] = []
    for cid, r in rows.items():
        rel = strict[cid][1] if cid in strict else wide[cid][1] if cid in wide else 0.0
        t = rel / (1 + rel) if rel > 0 else 0.0
        v = vs.get(cid)
        content = w * max(0.0, v) + (1 - w) * t if v is not None else t  # 还没有向量的段只看关键词分
        if cid not in strict:
            if content < spec.min_score:
                continue
            if negs and any(x in norm(r["text"]).lower() for x in negs):
                continue
        h = _hit(r, content, leaves, places)
        h.match = "words" if cid in strict else "meaning" if v is not None else "some"
        h.score = content * _decay(h, now, half) * semantic.trust(h, prof)
        hits.append(h)
    hits.sort(key=lambda h: (-h.score, -_ord(h.day), -h.chunk))
    return _pick(semantic.mmr(hits), limit, keep_order=True)


def _schema_ok(conn: sqlite3.Connection) -> None:
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='chunks_fts'").fetchone():
        raise RecallError(L("记忆索引还没建", "The memory index hasn't been built yet"))


def _scope_sql(sc: Scope, threads: list[str] | None, source: str, tree: sqlite3.Connection | None = None) -> tuple[list[str], list]:
    """tree：世界树的叶子在索引里（按意思搜开着）时传读的那个连接，按枝的范围加上（engine/recall_tree.py）。"""
    parts: list[str] = []
    params: list = []

    def among(col: str, values: list[str]) -> str:
        params.extend(values)
        return f"{col} IN ({','.join('?' * len(values))})" if values else "0"

    if source in ("all", "memory"):
        if sc.roots:
            parts.append(among("s.root", [r.key for r in sc.roots]))
        parts.append("(s.root = 'digest' AND " + ("1" if threads is None else among("s.thread", threads)) + ")")
        if tree is not None:
            from . import recall_tree
            if (t := recall_tree.scope_sql(tree, sc.agent, params)) is not None:
                parts.append(t)
    if source in ("all", "chats"):
        parts.append("(s.root = 'chat' AND " + ("1" if threads is None else among("s.thread", threads)) + ")")
    return [f"({' OR '.join(parts)})"], params


def _collect(conn, groups: list[list[Term]], where: list[str], params: list, limit: int) -> dict[int, tuple[sqlite3.Row, float]]:
    n = max(limit * 6, 40)  # 多拿一些再按时间、去重挑（OpenClaw 的 candidateMultiplier 是 4）
    found: dict[int, tuple[sqlite3.Row, float]] = {}
    for match, likes, unlikes in _plans(groups):
        for r, rel in _rows(conn, match, likes, unlikes, where, params, n):
            if r["id"] not in found or found[r["id"]][1] < rel:
                found[r["id"]] = (r, rel)
    return found


# —— 给模型看的 ——————————————————————————————————————————————————————————————

def _names() -> dict[str, str]:
    """对话 / Agent id → 名字（groups 的名字、项目的标题），只读；读不到就空。"""
    out: dict[str, str] = {}
    try:
        with contextlib.closing(sqlite3.connect(f"file:{settings.db}?mode=ro", uri=True, timeout=5)) as c:
            for sql in ("SELECT id, name FROM groups", "SELECT id, title FROM side_chats"):
                with contextlib.suppress(sqlite3.Error):
                    out.update({str(a): str(b) for a, b in c.execute(sql)})
    except sqlite3.Error:
        pass
    return out


def thread_label(thread: str, names: dict[str, str]) -> str:
    if thread == "main":
        return L("主对话", "Main chat")
    return names.get(thread) or thread


def speaker(role: str | None, thread: str, names: dict[str, str], cache: dict[str, str]) -> str:
    if role == "user":
        return settings.user_name or L("用户", "the user")
    if role == "grava":  # 助手的话：那个对话的 Agent 的名字，主对话就是助手自己
        who = owner_of(thread, cache)
        return settings.app_name if who == "main" else names.get(who) or settings.app_name
    return L("（系统）", "(system)")


def ref_of(h: Hit, sc: Scope) -> str:
    if h.root == "chat":
        return f"chat:{h.thread}#{h.path}"
    if h.root == "digest":
        return f"digest:{h.path}"
    if h.root == "tree":  # 世界树的叶子（按意思搜开着时才有）
        return f"tree:{h.path}"
    r = next((x for x in sc.roots if x.key == h.root), None)
    label = r.label(sc.agent) if r else h.root
    return f"{label}:{h.path}" if label else h.path


def _variants(terms: list[str]) -> list[str]:
    """标命中的行用的几种写法：原词；英文长词再加去掉词尾的（running ↔ run、studies ↔ study，索引用 porter 词干）。"""
    out = []
    for t in terms:
        low = t.lower()
        out.append(low)
        if low.isascii() and low.isalpha() and len(low) > 4:
            out.append(low[:max(4, len(low) - 3)])
    return out


def _clip_line(line: str, keys: list[str]) -> str:
    s = line.strip()
    if len(s) <= LINE_MAX:
        return s
    low = norm(s).lower()
    pos = min((i for i in (low.find(k) for k in keys) if i >= 0), default=0)
    a = max(0, pos - 60)
    return ("…" if a else "") + s[a:a + LINE_MAX] + ("…" if a + LINE_MAX < len(s) else "")


def shown_lines(h: Hit, terms: list[str]) -> tuple[list[tuple[int, str]], int]:
    """这一段里命中的行（最多 SHOW_LINES 行）→ ([(行号, 字)], 还有几行也命中了)。一行都对不上（词干对上的）就给开头两行。"""
    keys = _variants(terms)
    lines = [(h.start + i, ln) for i, ln in enumerate(h.text.split("\n"))]
    hit = [(n, ln) for n, ln in lines if ln.strip() and any(k in norm(ln).lower() for k in keys)]
    if not hit:
        hit = [(n, ln) for n, ln in lines if ln.strip()][:2]
        return [(n, _clip_line(ln, keys)) for n, ln in hit], 0
    return [(n, _clip_line(ln, keys)) for n, ln in hit[:SHOW_LINES]], max(0, len(hit) - SHOW_LINES)


def render(found: Found, max_chars: int | None = None) -> str:
    """搜到的 → 给模型的字：每条 [n] 出处 · 行号 / 时间 · 谁说的，下面是命中的那几行；总长不超过 max_chars。"""
    budget = int(max_chars or config().get("max_chars") or MAX_CHARS)
    if not found.hits and found.mode == "hybrid":
        return L("没找到相关的记忆或对话（按意思和关键词都搜过了）。可以换个说法再搜一次；也可能就是没记过，照实跟用户说。",
                 "Nothing found in memory or past chats (searched by meaning and by keywords). Try other wording once more; it may also "
                 "just never have been recorded, so tell the user that.")
    if not found.hits:
        return L("没找到相关的记忆或对话。所有词默认都要有：换个说法、少写几个词，或者用 OR 连几个说法再搜一次；"
                 "也可能就是没记过，照实跟用户说。",
                 "Nothing found in memory or past chats. All words must match by default: try other wording, fewer words, or join "
                 "alternatives with OR; it may also just never have been recorded, so tell the user that.")
    names, cache = _names(), {}
    out = [L("（所有词都在的没有；下面是只对上一部分词的。）", "(Nothing matched every word; these match some of them.)")] if found.relaxed else []
    if found.mode == "fallback":
        out.insert(0, L("（这一次按意思搜没连上，下面只按关键词。）", "(Searching by meaning didn't work this time; these are keyword matches only.)"))
    used = sum(len(x) + 1 for x in out)
    shown = 0
    for i, h in enumerate(found.hits, 1):
        ref = ref_of(h, found.scope)
        if h.root == "chat":
            when = (h.ts or "")[:16].replace("T", " ")
            head = f"[{i}] {ref} · {thread_label(h.thread or '', names)} · {when} · {speaker(h.role, h.thread or '', names, cache)}"
        elif h.root == "tree":
            from . import recall_tree
            bits = [f"[{i}] {ref}", L(f"世界树「{h.place}」", f"memory tree · {h.place}")]
            if h.day:
                bits.append(h.day)
            if h.author:
                bits.append(L(f"{recall_tree.author_label(h.author, names)} 记的", f"recorded by {recall_tree.author_label(h.author, names)}"))
            if h.tier:
                from . import memgate
                bits.append(L(f"信几分：{memgate.tier_label(h.tier)}", f"trust: {memgate.tier_label(h.tier)}"))
            if h.status == "pending":
                bits.append(L("还没确认", "not confirmed yet"))
            head = " · ".join(bits)
        else:
            span = L(f"第 {h.start} 行", f"line {h.start}") if h.start == h.end else L(f"第 {h.start}–{h.end} 行", f"lines {h.start}–{h.end}")
            bits = [f"[{i}] {ref}", span]
            if h.root == "digest":
                bits.append(L(f"「{thread_label(h.thread or '', names)}」的日结", f"daily digest of {thread_label(h.thread or '', names)}"))
            if h.day:
                bits.append(h.day)
            if h.heading:
                bits.append(f"# {h.heading}")
            head = " · ".join(bits)
        if h.match == "meaning":
            head += L(" · 按意思", " · by meaning")
        lines, extra = shown_lines(h, found.terms)
        body = [f"    {n}: {t}" if h.root not in ("chat", "tree") else f"    {t}" for n, t in lines]
        if extra:
            body.append(L(f"    …（这一段还有 {extra} 行提到）", f"    …({extra} more matching lines here)"))
        block = "\n".join([head, *body])
        if used + len(block) + 1 > budget and shown:
            break
        out.append(block)
        used += len(block) + 1
        shown += 1
    left = len(found.hits) - shown + found.more
    tail = L("（按相关度排。读原文：memory_get，ref 写 [n] 后面那段出处；文件可以加 from / lines，对话会带上前后几条。）",
             "(Most relevant first. To read the original, call memory_get with the source after [n] as ref; for files add from / lines, "
             "for chats you also get the messages around it.)")
    if found.mode == "hybrid":
        tail = L("（按相关度排：意思和关键词一起算，标「按意思」的是没把所有词都对上、意思相近的，用之前看看是不是一回事。"
                 "读原文：memory_get，ref 写 [n] 后面那段出处；文件可以加 from / lines，对话会带上前后几条。）",
                 "(Most relevant first, by meaning and keywords together; \"by meaning\" ones don't contain every word but are close in "
                 "meaning, so check they're really about the same thing. To read the original, call memory_get with the source after [n] "
                 "as ref; for files add from / lines, for chats you also get the messages around it.)")
    if left > 0:
        tail = L(f"（还有 {left} 条没列出：加 after / before 或换个更具体的说法缩小。）", f"({left} more not shown: narrow it with after / before "
                 "or more specific words.)") + "\n" + tail
    return "\n\n".join(out) + "\n\n" + tail


# —— 读原文 ——————————————————————————————————————————————————————————————————
_LINE_REF = re.compile(r"#L(\d+)(?:-L?\d+)?$")


def _resolve(ref: str, sc: Scope) -> tuple[Root, str, int | None]:
    """出处 → (来源, 相对路径, 出处里带的行号)。不在这个 Agent 范围里的、对不上来源规矩的一律不给。"""
    hint = None
    if m := _LINE_REF.search(ref):
        hint, ref = int(m.group(1)), ref[:m.start()]
    label, sep, rest = ref.partition(":")
    by_label = {r.label(sc.agent): r for r in sc.roots if r.label(sc.agent)}
    if sep and label == "digest":
        root, rel = digest_root(), rest
        if not sc.owns(rel.split("/", 1)[0], {}):
            raise RecallError(L("这份日结不在你的范围里", "That digest isn't in your scope"))
    elif sep and label in by_label:
        root, rel = by_label[label], rest
    else:
        own = next((r for r in sc.roots if r.kind == "ws" and r.name == sc.agent), None)
        if own is None or (sep and (label.startswith("@") or label in RESERVED)):
            raise RecallError(L(f"读不了 {ref}：不在你的范围里", f"Can't read {ref}: it isn't in your scope"))
        root, rel = own, ref
    rel = rel.strip().replace("\\", "/")
    if not allowed(root, rel):
        raise RecallError(L(f"读不了 {ref}：只能读 memory_search 给出的那种出处（记忆文件、日结、共享资料、对话）",
                            f"Can't read {ref}: only the kinds of sources memory_search returns (memory files, digests, shared files, chats)"))
    return root, rel, hint


def _safe_file(root: Root, rel: str) -> Path:
    """路径一节节走下去：哪一节是软链都不行（借软链读到工作区外面）；要是普通文件、不太大。"""
    base = root.path
    p = base
    for part in rel.split("/"):
        p = p / part
        if p.is_symlink():
            raise RecallError(L("读不了：路径里有软链", "Can't read it: the path goes through a symlink"))
    if _regular(p) is None:
        raise RecallError(L(f"没有这个文件：{rel}", f"No such file: {rel}"))
    return p


def get(ref: str, agent: str | None = "main", *, start: int | None = None, lines: int | None = None) -> str:
    """按出处读原文（memory_get）。文件：from 行起读 lines 行（默认 READ_LINES），后面还有就说下一次的 from；对话：那条和前后各 lines 条。"""
    sc = scope(agent)
    ref = (ref or "").strip()
    if not ref:
        raise RecallError(L("ref 不能空：写 memory_search 结果里 [n] 后面那段出处", "ref is empty: use the source after [n] in a memory_search result"))
    if ref.startswith("chat:"):
        return _get_chat(ref, sc, lines)
    if ref.startswith("tree:") and _tree_on():  # 世界树的叶子（按意思搜开着时才进索引；没开时 tree: 可能是叫这个名字的共享项）
        from . import recall_tree
        return recall_tree.get(ref, sc.agent)
    root, rel, hint = _resolve(ref, sc)
    p = _safe_file(root, rel)
    try:
        text = p.read_text(encoding="utf8", errors="replace")
    except OSError as e:
        raise RecallError(L(f"读不了：{e.strerror}", f"Can't read it: {e.strerror}")) from None
    all_lines = text.replace("\r\n", "\n").split("\n")
    if all_lines and all_lines[-1] == "":
        all_lines.pop()
    first = max(1, int(start)) if start else max(1, hint - 3) if hint else 1
    count = max(1, min(int(lines or READ_LINES), 400))
    picked = all_lines[first - 1:first - 1 + count]
    shown, used = [], 0
    for i, ln in enumerate(picked):
        row = f"{first + i}: {ln}"
        if used + len(row) + 1 > READ_MAX and shown:
            break
        shown.append(row[:READ_MAX])
        used += len(row) + 1
    label = root.label(sc.agent)
    name = f"{label}:{rel}" if label else rel
    if not shown:
        return L(f"{name} 一共 {len(all_lines)} 行，第 {first} 行以后没有了。", f"{name} has {len(all_lines)} lines; nothing from line {first} on.")
    last = first + len(shown) - 1
    head = L(f"{name} · 第 {first}–{last} 行（共 {len(all_lines)} 行）", f"{name} · lines {first}–{last} of {len(all_lines)}")
    body = "\n".join([head, *shown])
    if last < len(all_lines):
        body += "\n" + L(f"[后面还有：from={last + 1} 接着读]", f"[More content available. Use from={last + 1} to continue.]")
    return body


_CHAT_REF = re.compile(r"^chat:(.+)#(\d+)$")


def _get_chat(ref: str, sc: Scope, around: int | None) -> str:
    m = _CHAT_REF.match(ref)
    if not m:
        raise RecallError(L("对话的出处写成 chat:<对话>#<消息号>", "A chat source looks like chat:<chat>#<message id>"))
    thread, mid = m.group(1), int(m.group(2))
    cache: dict[str, str] = {}
    if not sc.owns(thread, cache):
        raise RecallError(L(f"读不了 {ref}：不是你的对话", f"Can't read {ref}: it isn't one of your chats"))
    k = max(0, min(int(around if around is not None else AROUND), 20))
    try:
        with contextlib.closing(sqlite3.connect(f"file:{settings.db}?mode=ro", uri=True, timeout=10)) as src:
            src.row_factory = sqlite3.Row
            me = src.execute("SELECT id, role, text, ts FROM messages WHERE id=? AND thread=?", (mid, thread)).fetchone()
            if me is None:
                raise RecallError(L(f"没有这条消息了：{ref}（可能被删了）", f"That message is gone: {ref} (it may have been deleted)"))
            roles = ("user", "grava", "auto")
            before = src.execute("SELECT id, role, text, ts FROM messages WHERE thread=? AND id<? AND role IN (?,?,?) ORDER BY id DESC LIMIT ?",
                                 (thread, mid, *roles, k)).fetchall()[::-1]
            after = src.execute("SELECT id, role, text, ts FROM messages WHERE thread=? AND id>? AND role IN (?,?,?) ORDER BY id LIMIT ?",
                                (thread, mid, *roles, k)).fetchall()
    except sqlite3.Error as e:
        raise RecallError(L(f"读不了对话记录：{e}", f"Can't read the chat history: {e}")) from None
    names = _names()
    out = [L(f"chat:{thread} · {thread_label(thread, names)} · 第 {mid} 条和前后各 {k} 条", f"chat:{thread} · {thread_label(thread, names)} · "
             f"message {mid} and {k} on each side")]
    used = len(out[0])
    for r in [*before, me, *after]:
        text = (r["text"] or "").strip()
        text = text if len(text) <= MSG_MAX else text[:MSG_MAX] + L(f"…（还有 {len(text) - MSG_MAX} 字）", f"…({len(text) - MSG_MAX} more chars)")
        mark = L("（就是这条）", " (this one)") if r["id"] == mid else ""
        row = f"— #{r['id']} {(r['ts'] or '')[:16].replace('T', ' ')} · {speaker(r['role'], thread, names, cache)}{mark}：\n{text}"
        if used + len(row) > READ_MAX and r["id"] != mid:
            continue
        out.append(row)
        used += len(row)
    return "\n\n".join(out)


# —— 工具（主人回合；engine/tools.py 的 for_agent 接进去）——————————————————————————————

def _in_context(ctx) -> tuple[str, str | int] | None:
    """这一轮所在对话里、现在还在模型上下文里的那段（memory_search 不搜它，Hermes 的 session_search 也跳过当前会话）：
    (对话, 第一条还在上下文里的 app 消息 id)。按这一轮的会话算（engine_turns.session → DbSession.view()）：压缩掉、只剩摘要的部分，
    和当天没抄进会话的早先几句照样能搜（Hermes 也让压缩掉的行能搜到）。找不到会话：(对话, 今天) = 这个对话今天的都不搜。"""
    c = getattr(ctx, "conversation", "") or ""
    if not c or c == "-":
        return None
    try:
        from . import store
        from .session import DbSession
        with store.read() as conn:
            row = conn.execute("SELECT session FROM engine_turns WHERE id=?", (getattr(ctx, "turn", ""),)).fetchone()
        s = DbSession.get(row["session"]) if row and row["session"] else None
        refs = [m["ref"] for _, m in s.view().rows if m.get("role") == "user" and isinstance(m.get("ref"), int)] if s else []
        if refs:
            return c, min(refs)
    except Exception:  # noqa: BLE001 — 算不出来：今天的都不搜（宁可少搜，不重复给已经在眼前的）
        pass
    return c, today()


def has_tree(who: str) -> bool:
    """这个 Agent 有 tree 工具、engine.recall.tree 没关：memory_search 顺手查世界树。"""
    if config().get("tree") is False:
        return False
    from . import tools as tools_mod
    return "tree" in tools_mod.offered(who)


def tree_inline() -> bool:
    """世界树的叶子就在索引里（按意思搜开着、接了世界树）：搜出来的直接带着，不再另外附 tree 工具的 recall。"""
    if not _tree_on():
        return False
    from . import recall_tree
    return recall_tree.available()


async def _tree(query: str, who: str) -> str | None:
    """世界树里相关的几条（tree 工具的 recall，和模型自己调一样），附在搜索结果后面。没有 tree 工具、关掉了、出错、叶子已经在索引里 → None。"""
    try:
        if not has_tree(who) or tree_inline():
            return None
        import bridges
        out = await asyncio.wait_for(bridges.run("tree", ["recall", "--q", query[:200], "--limit", "5"], None, who, kill_on_cancel=True), 20)
    except Exception:  # noqa: BLE001 — 世界树没回来：只是少附几条
        return None
    rows = [ln for ln in out.splitlines() if ln.startswith("- ")]
    text = ""
    for ln in rows:
        if len(text) + len(ln) + 1 > TREE_CHARS:
            break
        text += ln + "\n"
    return text.rstrip() or None


def search_tool(me: str, tree: bool = False, semantic: bool = False, leaves: bool = False):
    """semantic：按意思搜开着（说明换成「按意思和关键词」）；leaves：世界树的叶子在索引里（说明里提一句，不再另外附）。"""
    from .loop import Tool, ToolFailure

    async def run(args: dict, ctx) -> str:
        who = ctx.agent or "main"
        q = str(args.get("query") or "").strip()
        if not parse(q, upper_ops=_semantic_on()):
            raise ToolFailure(L("query 里没有能搜的词：写几个关键词", "There's nothing to search for in query: give a few keywords"))
        try:  # 参数不对就别去叫世界树了
            day_bound(args.get("after")), day_bound(args.get("before"))
        except RecallError as e:
            raise ToolFailure(str(e)) from None
        tree = asyncio.ensure_future(_tree(q, who))
        try:
            found = await asyncio.to_thread(search, q, who, limit=args.get("limit") or LIMIT, after=args.get("after"),
                                            before=args.get("before"), source=str(args.get("source") or "all"), exclude=_in_context(ctx))
            text = render(found)
        except RecallError as e:
            tree.cancel()
            raise ToolFailure(str(e)) from None
        except BaseException:
            tree.cancel()
            raise
        t = await tree
        if t:
            text += "\n\n" + L("世界树里相关的（用户跨平台的记忆，tree 工具能看更多）：", "From the memory tree (the user's memory across platforms; "
                               "the tree tool shows more):") + "\n" + t
        return text

    if semantic:
        description = L(
            "搜你自己的记忆和以前的对话（按意思和关键词一起搜，只读）：你工作区的 MEMORY.md、memory/ 里的日记和笔记、日结、给你的共享资料"
            "（比如用户档案）、" + ("世界树里给你看的那几枝的叶子（标着在哪一枝）、" if leaves else "") + "还有你的对话里以前说过的话；"
            "这个对话里还在你眼前的部分不搜（压缩成摘要的能搜）。" + ("世界树里相关的几条附在最后。" if tree and not leaves else "") +
            "回答要用到以前的事、决定、日期、人、偏好、待办，而眼前的上下文里没有时，先搜再答；搜过没找到就照实说。"
            "query 写一句话或几个关键词（中英文都行；说法不一样、意思相近的也找得到；\"带引号\" = 整句，大写的 OR = 任一个，NOT 词 = 不要）；"
            "after / before 限定日期（2026-10-01，或往回数的 7d、2w、24h）；source 只搜 memory（记忆文件" + ("和世界树" if leaves else "") +
            "）或 chats（对话）。结果里 [n] 后面是出处，要看原文用 memory_get。",
            "Search your own memory and past conversations (by meaning and keywords together, read-only): MEMORY.md and the diaries and "
            "notes under memory/ in your workspace, daily digests, shared material given to you (such as the user's profile), " +
            ("leaves of the memory tree on the branches you're given (with their branch), " if leaves else "") +
            "and what was said earlier in your chats; the part of this chat still in your context isn't searched (parts compacted into a "
            "summary are). " + ("Related memory-tree entries are added at the end. " if tree and not leaves else "") +
            "When an answer depends on earlier events, decisions, dates, people, preferences or to-dos that aren't in the current "
            "context, search before answering; if nothing turns up, say you checked. query: a short phrase or a few keywords (Chinese "
            "or English; different wording with the same meaning is found too; \"quotes\" match an exact phrase, uppercase OR means "
            "either, NOT word excludes it); after / before limit the dates (2026-10-01, or counting back: 7d, 2w, 24h); source: memory "
            "(memory files" + (" and the memory tree" if leaves else "") + ") or chats only. Each result starts with [n] and its source; "
            "read the original with memory_get.")
    else:
        description = L(
            "搜你自己的记忆和以前的对话（关键词检索，只读）：你工作区的 MEMORY.md、memory/ 里的日记和笔记、日结、给你的共享资料"
            "（比如用户档案），还有你的对话里以前说过的话；这个对话里还在你眼前的部分不搜（压缩成摘要的能搜）。" + ("世界树里相关的几条附在最后。" if tree else "") +
            "回答要用到以前的事、决定、日期、人、偏好、待办，而眼前的上下文里没有时，先搜再答；搜过没找到就照实说。"
            "query 写几个关键词（中英文都行；词默认都要有，\"带引号\" = 整句，OR = 任一个，NOT 词 = 不要）；after / before 限定日期"
            "（2026-10-01，或往回数的 7d、2w、24h）；source 只搜 memory（记忆文件）或 chats（对话）。结果里 [n] 后面是出处，"
            "要看原文用 memory_get。",
            "Search your own memory and past conversations (keyword search, read-only): MEMORY.md and the diaries and notes under memory/ "
            "in your workspace, daily digests, shared material given to you (such as the user's profile), and what was said earlier in "
            "your chats; the part of this chat still in your context isn't searched (parts compacted into a summary are). " +
            ("Related memory-tree entries are added at the end. " if tree else "") +
            "When an answer depends on earlier events, decisions, dates, people, preferences or to-dos that aren't in the "
            "current context, search before answering; if nothing turns up, say you checked. query: a few keywords (Chinese or English; "
            "all words must match by default, \"quotes\" match an exact phrase, OR means either, NOT word excludes it); after / before "
            "limit the dates (2026-10-01, or counting back: 7d, 2w, 24h); source: memory (memory files) or chats only. Each result "
            "starts with [n] and its source; read the original with memory_get.")
    return Tool(
        name="memory_search", kinds=("owner",), run=run, timeout=180, title=lambda args: "memory_search",
        classify=lambda args: ("read", "只读：搜自己范围里的记忆和对话"),
        description=description,
        schema={"type": "object", "required": ["query"], "properties": {
            "query": {"type": "string", "description": L("一句话或几个关键词", "A short phrase or a few keywords") if semantic else
                      L("几个关键词", "A few keywords")},
            "after": {"type": "string", "description": L("只要这一天及以后的：2026-10-01 或 7d / 2w / 24h", "Only from this day on: 2026-10-01 or 7d / 2w / 24h")},
            "before": {"type": "string", "description": L("只要这一天以前的（不含这一天）", "Only before this day (not including it)")},
            "source": {"type": "string", "enum": ["all", "memory", "chats"], "description": L("默认 all", "Default all")},
            "limit": {"type": "integer", "description": L(f"回几条，默认 {LIMIT}，最多 {LIMIT_MAX}", f"How many results, default {LIMIT}, at most {LIMIT_MAX}")}}})


def get_tool(me: str, leaves: bool = False):
    from .loop import Tool, ToolFailure

    async def run(args: dict, ctx) -> str:
        who = ctx.agent or "main"
        try:
            return await asyncio.to_thread(get, str(args.get("ref") or ""), who, start=args.get("from"), lines=args.get("lines"))
        except RecallError as e:
            raise ToolFailure(str(e)) from None

    return Tool(
        name="memory_get", kinds=("owner",), run=run, timeout=60, title=lambda args: "memory_get",
        classify=lambda args: ("read", "只读：读自己范围里的记忆原文"),
        description=L(
            "按 memory_search 结果里的出处读原文（只读，只能读你自己范围里的）。文件：ref 写出处（比如 memory/2026-10-12.md），"
            f"from 从第几行、lines 读几行（默认 {READ_LINES} 行；后面还有会告诉你下一次的 from）；对话：ref 写 chat:<对话>#<消息号>，"
            f"读那条和前后各 lines 条（默认 {AROUND} 条）。" + ("世界树的叶子：ref 写 tree:<叶子 id>。" if leaves else ""),
            "Read the original behind a memory_search result (read-only, only within your own scope). Files: ref is the source (e.g. "
            f"memory/2026-10-12.md); from is the first line and lines how many (default {READ_LINES}; if there's more you're told the next "
            f"from). Chats: ref is chat:<chat>#<message id>; you get that message and lines messages on each side (default {AROUND})."
            + (" Memory-tree leaves: ref is tree:<leaf id>." if leaves else "")),
        schema={"type": "object", "required": ["ref"], "properties": {
            "ref": {"type": "string", "description": L("memory_search 结果里 [n] 后面的出处", "The source after [n] in a memory_search result")},
            "from": {"type": "integer", "description": L("文件从第几行读起", "For files: the first line to read")},
            "lines": {"type": "integer", "description": L("文件读几行；对话是前后各几条", "For files: how many lines; for chats: how many messages on each side")}}})


def tools_for(me: str) -> list:
    """给这个 Agent 的两个只读工具；server.json 的 engine.tools.levels 写 {"memory_search": "off"} 能关掉。"""
    from . import tools as tools_mod
    lv = tools_mod.levels()
    out = []
    sem = _semantic_on()
    leaves = sem and tree_inline()
    if lv.get("memory_search") != "off":
        out.append(search_tool(me, has_tree(me), sem, leaves))
    if lv.get("memory_get") != "off":
        out.append(get_tool(me, leaves))
    return out


def query_hint() -> str:
    """owner.rules 的「## Memory」那一节怎么说 query：按意思搜开着时一句话也行。"""
    return "a short phrase or a few keywords" if _semantic_on() else "a few keywords"


# —— 命令行 ——————————————————————————————————————————————————————————————————

def stats() -> dict:
    path = db_path()
    if not path.exists():
        return {"db": str(path), "built": False}
    with _reader() as conn:
        try:
            roots = {k: n for k, n in conn.execute("SELECT root, count(*) FROM sources GROUP BY root")}
            chunks = conn.execute("SELECT count(*) FROM chunks").fetchone()[0]
        except sqlite3.Error:
            return {"db": str(path), "built": False}
        size = sum(Path(f"{path}{s}").stat().st_size for s in ("", "-wal") if Path(f"{path}{s}").exists())
        out = {"db": str(path), "built": True, "sources": roots, "chunks": chunks, "bytes": size,
               "chat_last": _meta(conn, "chat_last"), "synced_at": _meta(conn, "synced_at")}
    if _semantic_on():  # 按意思搜：向量补了多少、最近一次错误（engine/semantic.py 的 status）
        from . import semantic
        st = semantic.status()
        out["vectors"] = {k: st.get(k) for k in ("indexedWith", "vectors", "pending", "skipped", "leaves", "embeddedAt", "lastError")}
    return out


def _embed(max_requests: int | None) -> int:
    """命令行 embed：先同步，再在前台把向量补完（按 rpm / tpm 限速），一批打一行。"""
    from . import semantic
    if not _semantic_on():
        print(L("没配 engine.recall.embeddings：不按意思搜，没有向量要补", "engine.recall.embeddings isn't set: no search by meaning, nothing to embed"),
              file=sys.stderr)
        return 1
    sync()
    t0 = time.monotonic()

    def show(st: dict) -> None:
        print(f"# requests={st['requests']} embedded={st['embedded']} reused={st['reused']} skipped={st['skipped']} "
              f"{int(time.monotonic() - t0)}s", file=sys.stderr)
    res = semantic.backfill(max_requests=max_requests, progress=show)
    st = semantic.status()
    print(" ".join(f"{k}={v}" for k, v in res.items()) + f" vectors={st.get('vectors')} pending={st.get('pending')}")
    if res.get("busy"):
        print(L("别的进程（服务器的后台）正在补，这里就不补了", "Another process (the server's background job) is embedding right now; not doing it here"),
              file=sys.stderr)
    if res.get("error"):
        print(res["error"], file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="recall.py", description="记忆检索的索引（内核第 7 项）：搜、读、重建")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("search", help="按某个 Agent 的范围搜")
    s.add_argument("query")
    s.add_argument("--agent", default="main")
    s.add_argument("--limit", type=int, default=LIMIT)
    s.add_argument("--after")
    s.add_argument("--before")
    s.add_argument("--source", default="all", choices=("all", "memory", "chats"))
    s.add_argument("--no-sync", action="store_true", help="不先同步，直接搜现在的索引")
    g = sub.add_parser("get", help="按出处读原文")
    g.add_argument("ref")
    g.add_argument("--agent", default="main")
    g.add_argument("--from", dest="start", type=int)
    g.add_argument("--lines", type=int)
    r = sub.add_parser("reindex", help="全部来源增量同步")
    r.add_argument("--full", action="store_true", help="删掉索引从头建（向量也删了）")
    e = sub.add_parser("embed", help="在前台补向量（配了 engine.recall.embeddings 才有）")
    e.add_argument("--max", type=int, help="最多发几个请求")
    sub.add_parser("stats", help="索引里有什么")
    a = ap.parse_args(argv)
    print(f"# index: {db_path()}  config: {os.environ.get('MOUSSE_SERVER_CONFIG') or '~/.openmousse/server.json'}", file=sys.stderr)
    try:
        if a.cmd == "search":
            t0 = time.monotonic()
            found = search(a.query, a.agent, limit=a.limit, after=a.after, before=a.before, source=a.source, refresh=not a.no_sync)
            print(render(found))
            print(f"# {len(found.hits)} hits, {int((time.monotonic() - t0) * 1000)} ms" + ("" if found.mode == "keyword" else f", {found.mode}")
                  + (f" ({found.why})" if found.why else ""), file=sys.stderr)
        elif a.cmd == "get":
            print(get(a.ref, a.agent, start=a.start, lines=a.lines))
        elif a.cmd == "reindex":
            res = rebuild() if a.full else sync()
            print(" ".join(f"{k}={v}" for k, v in res.items()))
        elif a.cmd == "embed":
            return _embed(a.max)
        else:
            for k, v in stats().items():
                print(f"{k}: {v}")
    except RecallError as e:
        print(str(e), file=sys.stderr)
        return 1
    return 0
