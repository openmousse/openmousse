"""按意思搜（内核第 7 项的下一步，2026-10-16）：recall.db 里每一段（chunk）存一串向量，memory_search 把关键词和意思合在一起排。

只有配了 engine.recall.embeddings（engine/embeddings.py）才有这一切；不配 = 向量表一行都不写、一个请求都不发、世界树不进索引，和以前一模一样。
存（recall.db 里三张表，删段的时候一起删；engine/recall.py 的 DDL）：
  vec       一段一行：sha（发出去算的那段字的指纹）、v（float32 小端打包，已经归一：余弦 = 点积）。v 是空的 = 故意不算（太短的段）
  vec_bits  一段一行：每一维的正负号拼成的一串位（768 维 = 96 字节），粗筛用；和 vec 分开放，扫的时候只读这张窄表
  leaves    世界树的叶子（engine/recall_tree.py）：在哪一枝、谁写的、什么类、确认没有、trust（tree 表有这一列才有）、出处（原话、平台……，
            13d 的三档按它算）
  meta      embed_model（向量是哪一套配置算的，embeddings.Spec.identity）、embed_label、embed_at（上次写进向量）、embed_error（最近一次错误）
  换了型号 / 地址 / 维度（指纹对不上）：向量整个删掉重算。配置拿掉了（或者 server.json 这会儿读不了，读出来是空的）：不再发、
  不再用，算好的向量留在本机的 recall.db 里（跟着段一起删），再打开、还是这一套就不用重算；recall.py reindex --full 全删。
补（后台，不卡主人回合）：claw 是 builtin 时服务器起一个线程（main.py 的 lifespan 调 start()），每 10 分钟、搜的时候同步进来了新的段时，把来源同步一遍，
  给还没有向量的段补上：先世界树的叶子和常青的文件，再带日期的从新到旧，对话最后（也是从新到旧）。一批 batch 段一个请求，按 rpm / tpm 限速；
  文件改了，没变的那几段按指纹接着用旧的向量（照 OpenClaw 的 embedding cache，只是不另存：忘掉的段的向量当场删掉）；别处一模一样的一段
  也不再算。HTTP 请求不拿任何锁，写的时候才短暂拿 recall.db 的写锁。出错：限流照对方说的秒数等了再试，连不上 / 5xx 退避着再试，
  密钥 / 型号不对记下来等下一轮；都写进状态（status()，/api/engine/recall）。同一时间只有一个进程在补（recall.db.embed.lock）；
  命令行 recall.py embed 在前台补完。
搜（recall.search → recall._hybrid，用这里的 query、vector_scores、trust、mmr）：
  1. 搜的那一句先算向量（一次一个请求，最多 query_timeout 秒；最近几句记在内存里）。算不出来（没密钥、限流、断网……）：这一次只按关键词
     （和不开时一样的那一套），状态里写明，之后 60 秒不再试
  2. 关键词：所有词都要有的（和以前一样）+ 任一个词都行、中文长词拆成两字词的（一句话的查询也有点关键词分）
  3. 向量：范围里有向量的段，按符号位的汉明距离粗筛出几百段，再用 float32 精算余弦，留最像的几十段；关键词找到的也都精算
     （几万段时 Python 一段段硬算余弦要几百毫秒；粗筛 + 精算几十毫秒，PR 里有量的数）
  4. 相关度 = 0.7 × 余弦 + 0.3 × 关键词分（BM25 的 r → r / (1 + r)），照 OpenClaw 的 hybrid；还没有向量的段只看关键词分。
     所有词都对上的总能进；别的要相关度 ≥ min_score（0.35，OpenClaw 的 minScore）
  5. × 新近（和关键词那一路同一个 half_life_days：带日期的 30 天减半、最少留一半；世界树只有 event 类按日子减，别的叶子常青）
     × 信几分（trust()，13d 的三档：档案 1.2、带用户原话（或用户自己写）的叶子 1.1、平台推断的叶子 1；还没确认的再 × 0.9；
     叶子自己记着 trust 的用它）
  6. MMR（λ = 0.7，OpenClaw 的默认）重排：和已经挑出来的越像越往后；再照关键词那一路的规矩挑（每个文件最多两段、同一个对话同一天最多
     三条、几乎一样的只留一条）。按意思找到的（不是所有词都对上）在结果里标出来
mmr() 改写自 OpenClaw（openclaw/openclaw commit 6552b30，MIT，Copyright (c) 2026 OpenClaw Foundation；许可全文见仓库根目录的
THIRD_PARTY_NOTICES）：extensions/memory-core/src/memory/mmr.ts 的 applyMMRToHybridResults（分数压到 0–1、每段记着和已挑的最像的
相似度、分一样看原分）和 tokenize.ts 的 jaccardSimilarity。
别的排序和存法借 OpenClaw memory-core（hybrid.ts、temporal-decay.ts、manager-search-orchestration.ts、manager-embedding-*.ts）和
Hermes 的记忆后端 holographic（NousResearch/hermes-plugin-holographic commit e0106f2：FTS 候选 → 混合分 × trust × 时间衰减；向量当
BLOB 存在同一个 SQLite 里）的设计，只借设计。
"""
from __future__ import annotations

import array
import contextlib
import hashlib
import heapq
import json
import math
import operator
import sqlite3
import sys
import threading
import time
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

from config import settings
from i18n import L

from . import embeddings as E

try:
    import fcntl
except ImportError:  # Windows：只有进程里的锁
    fcntl = None  # type: ignore[assignment]

MIN_EMBED = 4          # 少于这么多个字的段不算向量（「好的」「ok」），照样能按关键词搜到
INTERVAL = 600         # 后台多久同步、补一次（秒）
FIRST_DELAY = 20       # 服务器起来以后先等这么久再开始（不和启动抢）
COOLDOWN = 60          # 搜的那一句没算成：这么多秒里不再试，只按关键词
LAMBDA = 0.7           # MMR 的 λ（OpenClaw 默认）
PREFILTER = 300        # 汉明距离粗筛留多少段再精算余弦
PREFILTER_MAX = 3000
FETCH = 8              # 一次从库里取几批要补的段
QUERY_CACHE = 128      # 记住最近几句查询的向量
RATE_WAIT_MAX = 900    # 限流时最多等多久再试（秒）
RETRIES, BACKOFF = 4, 5.0  # 连不上 / 5xx 最多再试几次（这一轮），第 n 次等 BACKOFF × 2^n 秒
HOLD, HOLD_MAX = 300, 3600  # 一轮出错停下以后，后台先歇多久（每错一次翻倍）
CANARY = "hello"       # 对方说这一批不收（400 之类）时先拿这句不相干的试一下：它也不收 = 配置的错（型号、参数、额度），不是那几段字的
TRUST = {"profile": 1.2, "owner": 1.1, "pending": 0.9}
_GENERIC = {"memory", "user", "readme", "index"}

_dot = getattr(math, "sumprod", None) or (lambda a, b: sum(map(operator.mul, a, b)))  # 3.12 起有 math.sumprod（快一倍）


# —— 打包 ————————————————————————————————————————————————————————————————————

def pack(v: array.array) -> bytes:
    """float32 小端（大端机器上先翻过来）。"""
    if sys.byteorder == "big":
        v = array.array("f", v)
        v.byteswap()
    return v.tobytes()


def unpack(blob: bytes) -> array.array:
    a = array.array("f")
    a.frombytes(blob)
    if sys.byteorder == "big":
        a.byteswap()
    return a


def bits(v) -> bytes:
    """每一维的正负号（> 0 记 1）拼成一串位，小端字节。"""
    n = int("".join("1" if x > 0 else "0" for x in reversed(v)), 2) if len(v) else 0
    return n.to_bytes((len(v) + 7) // 8, "little")


def sha(text: str) -> str:
    return hashlib.sha1(text.encode("utf8", "replace"), usedforsecurity=False).hexdigest()


def embed_input(root: str, rel: str | None, heading: str, text: str) -> str:
    """一段拿去算向量的字：文件带上文件名（日期、MEMORY / USER 这种不算）和所在的标题（「睡眠」「上午」），对话和世界树的叶子就是原文。"""
    if root in ("chat", "tree") or not rel:
        return text
    from .recall import _DATED
    stem = _DATED.sub("", Path(rel).stem).strip(" -_")  # 「2026-10-01 读书」→ 读书；日记本身（只有日期）→ 空
    stem = "" if stem.lower() in _GENERIC else stem
    head = " · ".join(x for x in (stem, heading) if x)
    return f"{head}\n{text}" if head else text


# —— 状态 ————————————————————————————————————————————————————————————————————
_state: dict = {"running": False, "pass": None, "cooldown_until": 0.0, "query_error": None}
_qcache: OrderedDict[tuple[str, str], list[float]] = OrderedDict()
_qlock = threading.Lock()


def spec() -> E.Spec | None:
    return E.spec()


def enabled() -> bool:
    return E.spec() is not None


def _now() -> str:
    return datetime.now(settings.tz).isoformat(timespec="seconds")


def _attempt(s: E.Spec) -> str:
    """出错以后歇着、冷却记在谁身上：这一套配置 + 密钥的指纹（改了密钥马上再试）。"""
    return f"{s.identity}:{hashlib.sha1(s.key().encode(), usedforsecurity=False).hexdigest()[:8]}"


def _record(kind: str, message: str, where: str) -> None:
    """记下最近一次错误（库里一份，给命令行和别的进程看；进程里一份）。写不进库就算了。
    搜的时候出的错只记在进程里：写库要等写锁，后台正在同步的话这一次搜就跟着等了。"""
    s = E.spec()
    err = {"at": _now(), "kind": kind, "message": E.scrub(message, *(s.secrets() if s else ())), "where": where}
    if where == "query":
        _state["query_error"] = err
        return
    from . import recall
    with contextlib.suppress(sqlite3.Error, OSError):
        with recall._writer() as conn:
            recall._set_meta(conn, "embed_error", json.dumps(err, ensure_ascii=False))


def _clear_error() -> None:
    from . import recall
    with contextlib.suppress(sqlite3.Error, OSError):
        with recall._writer() as conn:
            if recall._meta(conn, "embed_error"):
                recall._set_meta(conn, "embed_error", "")


# —— 和同步一起（recall._sync 在写锁里调）——————————————————————————————————————

def wipe(conn: sqlite3.Connection) -> int:
    """删掉所有向量 → 删了几段的。"""
    n = conn.execute("SELECT count(*) FROM vec").fetchone()[0]
    conn.execute("DELETE FROM vec")
    conn.execute("DELETE FROM vec_bits")
    return n


def check(conn: sqlite3.Connection, s: E.Spec | None) -> int:
    """库里的向量是不是这一套配置算的：不是就整个删掉、记下新的 → 删了几段。
    没配（s 是 None）：什么都不动。server.json 写错了一个逗号也读成「没配」，那时删了，改好以后就得把几万段重新发去算一遍。"""
    from . import recall
    if s is None:
        return 0
    have = recall._meta(conn, "embed_model")
    if have == s.identity:
        return 0
    n = wipe(conn)
    recall._set_meta(conn, "embed_model", s.identity)
    recall._set_meta(conn, "embed_label", s.label)
    recall._set_meta(conn, "embed_error", "")
    return n


def stash(conn: sqlite3.Connection, sid: int) -> dict[str, tuple[bytes, bytes]]:
    """这个来源要整个重切之前：它现有的向量（sha → (位, 向量)），新切出来字一样的段接着用。没有向量（没开）→ {}。"""
    return {r[0]: (r[1], r[2]) for r in conn.execute(
        "SELECT v.sha, b.bits, v.v FROM chunks c JOIN vec v ON v.chunk = c.id JOIN vec_bits b ON b.chunk = c.id WHERE c.source=? AND length(v.v) > 0",
        (sid,))}


def reuse(conn: sqlite3.Connection, cid: int, sid: int, text: str, keep: dict[str, tuple[bytes, bytes]]) -> bool:
    """新切出来的一段：字和以前的哪一段一样，就把那一段的向量接过来。"""
    if not keep:
        return False
    got = keep.get(sha(text))
    if got is None:
        return False
    conn.execute("INSERT OR REPLACE INTO vec(chunk, sha, v) VALUES(?,?,?)", (cid, sha(text), got[1]))
    conn.execute("INSERT OR REPLACE INTO vec_bits(chunk, source, bits) VALUES(?,?,?)", (cid, sid, got[0]))
    return True


# —— 补向量 ——————————————————————————————————————————————————————————————————
_limiter = E.Limiter()
_busy = threading.Lock()
PENDING = ("SELECT c.id, c.source, c.heading, c.text, s.root, s.path FROM chunks c JOIN sources s ON s.id = c.source "
           "LEFT JOIN vec v ON v.chunk = c.id WHERE v.chunk IS NULL "
           "ORDER BY (s.root = 'chat'), (s.root != 'tree'), (s.day IS NOT NULL), s.day DESC, c.id DESC LIMIT ?")


@contextlib.contextmanager
def _embed_lock():
    """跨进程：同一时间只有一个在补（服务器的后台线程、命令行 embed）。拿不到 → False。"""
    from . import recall
    path = recall.db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(f"{path}.embed.lock", "a+") as lk:
        if fcntl is not None:
            try:
                fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                yield False
                return
        yield True


def counts(conn: sqlite3.Connection) -> dict:
    total = conn.execute("SELECT count(*) FROM chunks").fetchone()[0]
    done = conn.execute("SELECT count(*), coalesce(sum(length(v) = 0), 0) FROM vec").fetchone()
    return {"chunks": total, "vectors": done[0] - done[1], "skipped": done[1], "pending": max(0, total - done[0])}


def _write(s: E.Spec, rows: list[tuple[int, int, str, array.array | None]]) -> int:
    """写进一批向量（段还在、配置还是这一套才写）→ 写了几段。"""
    from . import recall
    n = 0
    with recall._writer() as conn:
        if recall._meta(conn, "embed_model") != s.identity:
            return 0
        for cid, sid, h, v in rows:
            if not conn.execute("SELECT 1 FROM chunks WHERE id=?", (cid,)).fetchone():
                continue  # 补的时候这一段已经删了（文件改了、消息删了）
            conn.execute("INSERT OR REPLACE INTO vec(chunk, sha, v) VALUES(?,?,?)", (cid, h, pack(v) if v is not None else b""))
            if v is not None:
                conn.execute("INSERT OR REPLACE INTO vec_bits(chunk, source, bits) VALUES(?,?,?)", (cid, sid, bits(v)))
            n += 1
        recall._set_meta(conn, "embed_at", _now())
    return n


def _known(shas: list[str]) -> dict[str, bytes]:
    """别处一模一样的一段算过没有：sha → 向量。"""
    from . import recall
    out: dict[str, bytes] = {}
    with recall._reader() as conn:
        for i in range(0, len(shas), 500):
            part = shas[i:i + 500]
            for h, v in conn.execute(f"SELECT sha, v FROM vec WHERE sha IN ({','.join('?' * len(part))}) AND length(v) > 0", part):
                out.setdefault(h, v)
    return out


class _Pass:
    """一轮补向量：统计，和这一轮有没有成过一个请求（成过 = 配置没问题，对方不收的是那几段字）。"""

    def __init__(self, stats: dict) -> None:
        self.stats = stats
        self.ok = False


def _send(s: E.Spec, texts: list[str], stop: threading.Event | None, run: _Pass) -> list[array.array] | E.EmbedError | None:
    """发一个请求（限速；限流照对方说的等，连不上 / 5xx 退避着再试）→ 向量；对方不收（或者别的错）→ 那个错；这一轮该停了 → None。"""
    tries = waits = 0
    while True:
        if not _limiter.wait(s.rpm, s.tpm, sum(E.tokens(t) for t in texts), stop):
            return None
        if stop is not None and stop.is_set():
            return None
        run.stats["requests"] += 1
        try:
            out = list(E.embed(s, texts))
            run.ok = True
            return out
        except E.EmbedError as e:
            if e.kind == "rate" and waits < 6:
                delay = min(RATE_WAIT_MAX, e.retry_after if e.retry_after is not None else 30 * 2 ** waits)
                waits += 1
                _record("rate", f"{e} (waiting {delay:g}s)", "index")
                if _sleep(delay, stop):
                    return None
                continue
            if e.kind in ("net", "server") and tries < RETRIES:
                tries += 1
                if _sleep(min(300, BACKOFF * 2 ** tries), stop):
                    return None
                continue
            return e
        except Exception as e:  # noqa: BLE001 — 认不出的错：这一轮停下，记下来
            return E.EmbedError("internal", f"{type(e).__name__}: {e}")


def _call(s: E.Spec, texts: list[str], stop: threading.Event | None, run: _Pass) -> list[array.array | None] | None:
    """一批 → 每段的向量（单独发也不收的那段是 None = 不算它，照样能按关键词搜到）；这一轮该停了 → None（错误记下了）。
    对方说这一批不收（400 / 413 / 422 之类，不是限流、密钥、连不上）：这一轮还没成过一个请求的话，先拿 CANARY 试一下 ——
    它也不收 = 配置的错（型号、参数、额度……），这一轮停下，一段都不标成「不算」，修好了照样补；它收 = 是这批里哪几段（太长、
    对方不收）或者这一批太大：对半拆开再发，拆到单独一段还不收的才不算。"""
    got = _send(s, texts, stop, run)
    if got is None or isinstance(got, list):
        return got
    e = got
    if e.kind != "bad" or (not run.ok and not isinstance(_send(s, [CANARY], stop, run), list)):
        _record(e.kind, str(e), "index")
        run.stats["error"] = str(e)
        return None
    if len(texts) == 1:
        run.stats["refused"] += 1
        _record(e.kind, f"{e} — " + L("这一段不算向量（照样能按关键词搜到）", "this chunk gets no vector (keyword search still finds it)"), "index")
        return [None]
    half = len(texts) // 2
    a = _call(s, texts[:half], stop, run)
    b = _call(s, texts[half:], stop, run) if a is not None else None
    return None if a is None or b is None else a + b


def _sleep(seconds: float, stop: threading.Event | None) -> bool:
    if stop is not None:
        return stop.wait(seconds)
    time.sleep(seconds)
    return False


def backfill(*, stop: threading.Event | None = None, max_requests: int | None = None, progress=None) -> dict:
    """给还没有向量的段补上（后台线程、命令行 embed 都调它）→ 这一轮的统计。stop 置位就停；max_requests 发够这么多个请求就停。"""
    from . import recall
    s = E.spec()
    stats = {"embedded": 0, "reused": 0, "skipped": 0, "refused": 0, "requests": 0, "error": None, "busy": False}
    if s is None:
        return stats
    run = _Pass(stats)
    if not _busy.acquire(blocking=False):
        stats["busy"] = True
        return stats
    _state["running"] = True
    try:
        with _embed_lock() as got:
            if not got:
                stats["busy"] = True
                return stats
            with recall._writer() as conn:
                check(conn, s)
            while stop is None or not stop.is_set():
                cur = E.spec()
                if cur is None or cur.identity != s.identity:
                    break  # 配置刚改了：这一轮到此为止，下一轮按新的来
                with recall._reader() as conn:
                    rows = conn.execute(PENDING, (s.batch * FETCH,)).fetchall()
                if not rows:
                    break
                ready: list[tuple[int, int, str, array.array | None]] = []
                todo: list[tuple[int, int, str, str]] = []  # (段, 来源, sha, 要算的字)
                for r in rows:
                    text = embed_input(r["root"], r["path"], r["heading"], r["text"])
                    if len("".join(r["text"].split())) < MIN_EMBED:
                        ready.append((r["id"], r["source"], sha(text), None))
                        stats["skipped"] += 1
                    else:
                        todo.append((r["id"], r["source"], sha(text), text))
                known = _known([t[2] for t in todo])
                fresh = [t for t in todo if t[2] not in known]
                for cid, sid, h, _ in todo:
                    if h in known:
                        ready.append((cid, sid, h, unpack(known[h])))
                        stats["reused"] += 1
                if ready and not _write(s, ready):
                    break
                uniq: dict[str, str] = {}
                for _, _, h, text in fresh:
                    uniq.setdefault(h, text)
                hs = list(uniq)
                for idx in E.batches([uniq[h] for h in hs], s.batch):
                    if max_requests is not None and stats["requests"] >= max_requests:
                        return stats
                    part = [hs[i] for i in idx]
                    vecs = _call(s, [uniq[h] for h in part], stop, run)
                    if vecs is None:
                        return stats
                    got = dict(zip(part, vecs, strict=True))
                    out = [(cid, sid, h, got[h]) for cid, sid, h, _ in fresh if h in got]
                    if not _write(s, out):
                        return stats  # 写不进去（配置在这中间换了）：这一轮停下
                    stats["embedded"] += sum(1 for o in out if o[3] is not None)
                    if progress:
                        progress(stats)
            if not stats["error"] and not stats["refused"]:  # 有不收的段：留着那句话，状态里看得到
                _clear_error()
            return stats
    finally:
        _state["running"] = False
        _state["pass"] = {"at": _now(), **{k: v for k, v in stats.items() if k != "busy"}}
        _busy.release()


# —— 后台线程 ——————————————————————————————————————————————————————————————————
_thread: threading.Thread | None = None
_tlock = threading.Lock()  # 两次搜同时叫醒它：只起一个线程
_wake = threading.Event()
_halt = threading.Event()
_started = False


def start() -> None:
    """服务器起来时（main.py 的 lifespan，claw 是 builtin）：之后 kick() 才会起后台线程。配了向量的话现在就起。"""
    global _started
    _started = True
    _halt.clear()
    if enabled():
        _ensure()


def _ensure() -> None:
    global _thread
    with _tlock:
        if _thread is None or not _thread.is_alive():
            _thread = threading.Thread(target=_loop, name="recall-embed", daemon=True)
            _thread.start()


def kick() -> None:
    """有新的段了（搜之前同步过）：叫后台线程起来补。没 start 过（测试、命令行）、没配向量 → 什么都不做。"""
    if _started and enabled():
        _ensure()
        _wake.set()


def stop(timeout: float = 5.0) -> None:
    global _started
    _started = False
    _halt.set()
    _wake.set()
    t = _thread
    if t is not None and t.is_alive() and t is not threading.current_thread():
        t.join(timeout)


def reset() -> None:
    """清掉进程里记着的：查询的向量、冷却、出错以后歇着、限速的账（测试换配置时用）。"""
    with _qlock:
        _qcache.clear()
    _state.update({"cooldown_until": 0.0, "cooldown_for": None, "query_error": None, "hold": 0, "hold_until": 0, "hold_for": None, "pass": None})
    with _limiter.lock:
        _limiter.log.clear()


def _allowed() -> bool:
    """claw 换成别的了（不是 builtin）：后台什么都不做（OpenClaw 那条路不受影响）。"""
    import claw
    return claw.is_builtin()


def _held() -> bool:
    """第 4 批：急停着（engine/estop.py）、今天的花费到了上限（engine/spend.py：总数或 background）：这一轮不补向量。"""
    from . import estop, spend
    return estop.paused() or not spend.embed_ok()


def _loop() -> None:
    from . import recall
    first = True
    while not _halt.is_set():
        _wake.wait(FIRST_DELAY if first else INTERVAL)
        _wake.clear()
        first = False
        if _halt.is_set():
            break
        s = E.spec()
        if s is None or not _allowed() or _held():
            continue
        if _attempt(s) == _state.get("hold_for") and time.monotonic() < _state.get("hold_until", 0):
            continue  # 上一轮出错了（密钥、型号、连不上）：先歇一会儿，别每搜一次就撞一次；配置、密钥改了马上再试
        try:
            recall.sync()
            st = backfill(stop=_halt)
        except Exception as e:  # noqa: BLE001 — 后台线程不能死：记下来，下一轮再来
            _record("internal", f"{type(e).__name__}: {e}", "index")
            st = {"error": str(e)}
        if st.get("error"):
            _state["hold"] = min(HOLD_MAX, max(HOLD, 2 * _state.get("hold", 0)))
            _state["hold_until"], _state["hold_for"] = time.monotonic() + _state["hold"], _attempt(s)
        else:
            _state["hold"] = 0
            _state["hold_until"] = 0


# —— 搜 ——————————————————————————————————————————————————————————————————————

def query(s: E.Spec, text: str) -> tuple[list[float] | None, str | None]:
    """搜的那一句的向量（最近几句记在内存里）→ (向量, None)；算不出来 → (None, 原因)，之后 COOLDOWN 秒里不再试。"""
    key = (s.identity, text)
    attempt = _attempt(s)
    with _qlock:
        if key in _qcache:
            _qcache.move_to_end(key)
            return _qcache[key], None
        if time.monotonic() < _state["cooldown_until"] and _state.get("cooldown_for") == attempt:  # 改了配置、密钥就马上再试
            err = _state.get("query_error") or {}
            return None, err.get("message") or L("刚才没算成，等一会儿再试", "It failed a moment ago; trying again shortly")
    try:
        v = list(E.embed(s, [text], query=True)[0])
    except Exception as e:  # noqa: BLE001 — 什么错都退回只按关键词（EmbedError 是认得的那几种）
        why = str(e) if isinstance(e, E.EmbedError) else E.scrub(f"{type(e).__name__}: {e}", *s.secrets())
        with _qlock:
            _state["cooldown_until"], _state["cooldown_for"] = time.monotonic() + COOLDOWN, attempt
        _record(e.kind if isinstance(e, E.EmbedError) else "internal", why, "query")
        return None, why
    with _qlock:
        _qcache[key] = v
        while len(_qcache) > QUERY_CACHE:
            _qcache.popitem(last=False)
        if _state.get("query_error"):
            _state["query_error"] = None
    return v, None


def vector_scores(conn: sqlite3.Connection, qv: list[float], where: list[str], params: list, n: int,
                  extra: set[int]) -> dict[int, float]:
    """范围里最像的 n 段 + extra（关键词找到的）各自的余弦 → {段: 余弦}。先按汉明距离粗筛，再精算。"""
    if not qv:
        return {}
    qbits = int.from_bytes(bits(qv), "little")
    cond = " AND ".join(where) or "1"
    fb = int.from_bytes
    rows = conn.execute(f"SELECT b.chunk, b.bits FROM vec_bits b JOIN sources s ON s.id = b.source WHERE {cond}", params).fetchall()  # noqa: S608
    k = min(PREFILTER_MAX, max(PREFILTER, n * 8, len(rows) // 100))
    near = heapq.nsmallest(k, (((fb(b, "little") ^ qbits).bit_count(), cid) for cid, b in rows))
    ids = {cid for _, cid in near} | extra
    width = len(qv) * 4
    exact: dict[int, float] = {}
    todo = list(ids)
    for i in range(0, len(todo), 500):
        part = todo[i:i + 500]
        for cid, blob in conn.execute(f"SELECT chunk, v FROM vec WHERE chunk IN ({','.join('?' * len(part))})", part):
            if blob and len(blob) == width:
                exact[cid] = _dot(qv, unpack(blob))
    top = heapq.nlargest(n, ((v, c) for c, v in exact.items() if c not in extra))
    return {c: v for v, c in top} | {c: exact[c] for c in extra if c in exact}


def trust(h, profile_roots: set[str]) -> float:
    """信几分（13d，engine/memgate.py 的三档）：叶子自己记着 trust（世界树的 tree 表有 trust 列）就用它；不然按档 —— 档案（档案文件、
    工作区的 USER.md）× 1.2，带用户原话的叶子（或用户自己写的）× 1.1，平台推断的叶子 × 1；还没确认的叶子再 × 0.9。别的段 × 1。"""
    if getattr(h, "trust", None) is not None:
        return float(h.trust)
    if h.root in profile_roots or (h.root.startswith("ws.") and h.path == "USER.md"):
        return TRUST["profile"]
    if h.root == "tree":
        from . import memgate, recall_tree
        tier = getattr(h, "tier", "") or ("quoted" if h.author in recall_tree.owner_sources() else "inferred")
        return memgate.TRUST.get(tier, 1.0) * (TRUST["pending"] if h.status == "pending" else 1.0)
    return 1.0


def mmr(hits: list, lam: float = LAMBDA) -> list:
    """MMR 重排（Carbonell & Goldstein 1998，改写自 OpenClaw 的 mmr.ts，见文件头）：分数先压到 0–1，每次挑 λ × 分数 − (1 − λ) × 和已挑的
    最像的那段的相似度（词的 Jaccard）最大的。"""
    if len(hits) < 2:
        return list(hits)
    from .recall import _toks
    hi, lo = max(h.score for h in hits), min(h.score for h in hits)
    rel = {id(h): 1.0 if hi == lo else (h.score - lo) / (hi - lo) for h in hits}
    toks = {id(h): _toks(h.text) for h in hits}
    left = list(hits)
    worst = {id(h): 0.0 for h in hits}
    out = []
    while left:
        best = max(left, key=lambda h: (lam * rel[id(h)] - (1 - lam) * worst[id(h)], h.score))
        out.append(best)
        left.remove(best)
        bt = toks[id(best)]
        for h in left:
            t = toks[id(h)]
            sim = len(t & bt) / len(t | bt) if (t or bt) else 1.0
            if sim > worst[id(h)]:
                worst[id(h)] = sim
    return out


def _bare(url: str) -> str:
    """地址给人看：去掉里面万一带着的用户名密码和查询串。"""
    from urllib.parse import urlsplit, urlunsplit
    try:
        p = urlsplit(url)
        return urlunsplit((p.scheme, p.hostname + (f":{p.port}" if p.port else "") if p.hostname else p.netloc, p.path, "", ""))
    except ValueError:
        return ""


def status() -> dict:
    """/api/engine/recall 和命令行 stats 用：开没开、哪一家、索引了多少段、上次更新、最近一次错误（不带密钥）。"""
    from . import recall
    s = E.spec()
    out: dict = {"enabled": s is not None, "problem": E.problem()}
    if s is not None:
        key = s.key()
        out.update(backend=s.backend, model=s.model, dims=s.dims, url=_bare(s.url), keyEnv=s.key_env or None, key=bool(key) or not s.key_env,
                   batch=s.batch, rpm=s.rpm, tpm=s.tpm, minScore=s.min_score, vectorWeight=s.vector_weight)
    out["running"] = bool(_state["running"])  # 这个进程里正在补（一轮还没完）
    out["worker"] = bool(_thread is not None and _thread.is_alive() and _started)  # 后台线程在（服务器里、claw 是 builtin）
    out["lastPass"] = _state.get("pass")
    path = recall.db_path()
    if not path.exists():
        return {**out, "built": False}
    try:
        with recall._reader() as conn:
            c = counts(conn)
            leaves = conn.execute("SELECT count(*) FROM sources WHERE root='tree'").fetchone()[0]
            err = recall._meta(conn, "embed_error")
            out.update(built=True, **c, leaves=leaves, syncedAt=recall._meta(conn, "synced_at"), embeddedAt=recall._meta(conn, "embed_at"),
                       indexedWith=recall._meta(conn, "embed_label"), treeNote=recall._meta(conn, "tree_note") or None,
                       stale=bool(s and recall._meta(conn, "embed_model") not in (None, s.identity)))
            if s is None:
                out["pending"] = 0  # 没开：哪一段都不等着补
    except sqlite3.Error:
        return {**out, "built": False}
    try:
        e = json.loads(err) if err else None
    except ValueError:
        e = None
    q = _state.get("query_error")
    if q and (not e or q.get("at", "") >= e.get("at", "")):
        e = q
    out["lastError"] = e
    out["fallback"] = bool(s is not None and time.monotonic() < _state["cooldown_until"] and _state.get("cooldown_for") == _attempt(s))
    return out
