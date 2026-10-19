"""手册怎么找（2026-10-19，0.6）：一句话拆成词，和每份手册的名字、「什么时候用」、说明、正文比，打一个 0–1 的分。
世界树的 find_manual、mousse-tree manual find，和 OpenMousse 内核每轮备料前搜手册和技能（server/engine/manuals.py）用的是同一套。
只用标准库，不碰模型、不碰网络。

词（terms）：
  中日韩的字：连着的两个字一个词（「再改一版」→ 再改、改一、一版），两个字都是虚字的（帮我、一下）和「怎么、什么、这个」这类
  （STOP_BI）不要，只有一个字是虚字的（「问的、改一」）分量减半；再加单个的字，分量小（0.3，「的、我、把」这类虚字不要）；
  一个字单独成段的（「改 SOP」里的「改」）分量大一点（0.7）
  英文和数字：小写，去掉 the / my / please 这类词，再去词尾（revising、revised、revises → revis；logging → log）。去词尾的办法借
  OpenClaw（openclaw/openclaw commit 762750c0）src/agents/tool-search-ranking.ts 的思路：去几遍词尾、去掉因词尾重写的辅音，只借思路，没拿代码
分（score）：一个词在这一批里越少见越要紧（平滑过的 IDF：1 + ln((N+1)/(df+1))，一批里每份都有的词分量是 1）。
  名字、「什么时候用」的每一句（短的）：那一句有多少落在这句话里（盖住整句 = 1；只碰上一个单字的打折）
  说明、正文（长的）：这句话里落在里面的词加起来的分量 M，换成 1 - e^(-M/k)（说明 k = 2.5，正文 k = 4）：只看碰上了多少，
  不因为这句话长就被摊薄；比这一批的平均长的那一栏 k 再乘 1 - b + b × 长度/平均（BM25 的长度归一，b = 0.75，只罚长的）：
  一大段说明里碰上一两个常见词不算数（OpenMousse 自带的技能说明动辄几百字）
  合起来：1 - Π(1 - 权重 × 那一栏的分)；权重 名字 0.95、什么时候用 0.9、说明 0.6、正文 0.35：只在正文里碰上几个词的分不高
几种说法（rank 的 queries）：每种说法带一个分量（这句话本身 1.0，连上一句的 0.85……），取最高的；不止一种说法碰上（≥ 0.3）的，每多一种 +0.05。
"""
from __future__ import annotations

import math
import re
import threading
import unicodedata
from collections.abc import Iterable

CJK = re.compile("[々〇ぁ-ゖァ-ヺー㐀-䶿一-鿿가-힯豈-﫿"
                 "\U00020000-\U0002ebef]+")
LATIN = re.compile(r"[a-z0-9]+")
SPLIT = re.compile(r"[、,，;；|｜/\n]+")
STOP_EN = frozenset(
    "a an the and or of to in on at for with from by about as into than then my me i im i'm we our us you your it its this that these those "
    "is are was were be been am do does did done please pls can could would should will shall may might must let lets help want need "
    "get got just some any really also so very again one more bit how what which there here".split())
STOP_ZH = frozenset("的了我你他她它们把被给让请帮要想能去来说看用做还又很没不也都就再这那个下上中里吗呢吧啊呀哦嗯一和与及或是在有点些")
STOP_BI = frozenset("怎么 么样 怎样 什么 为什 这个 那个 一个 一些 一点 可以 我们 你们 他们 她们 自己 现在 时候 已经 还是 就是 因为 所以 如果 "
                    "但是 然后 而且 或者 不是 没有 这样 那样 这些 那些 哪里 哪个 这里 那里 多少 有点 的话 东西 事情".split())  # 两个字都不虚、可也说明不了要做什么的
NON_PLURAL = frozenset(("news", "status", "alias", "canvas", "focus", "bonus", "atlas", "lens", "axis", "basis", "analysis", "gas", "bus",
                        "plus", "always", "perhaps", "series"))
BIGRAM, HALF, UNIGRAM, LONE, WORD, NUMBER = 1.0, 0.5, 0.3, 0.7, 1.0, 0.5
FIELDS = {"name": 0.95, "when": 0.9, "about": 0.6, "body": 0.35}
SATURATE = {"about": 2.5, "body": 4.0}
LENGTH_B = 0.75
AGREE, AGREE_AT = 0.05, 0.3
_cache: dict[str, dict[str, float]] = {}
_cache_lock = threading.Lock()  # OpenMousse 的内核在几个线程里同时搜
CACHE_MAX = 512


def phrases(raw) -> list[str]:
    """「什么时候用」：列表、或者用 、，;；|/ 换行隔开的一串（列表里的每一项也照这样再分）→ 去掉空白、重复的几句。"""
    items = raw if isinstance(raw, (list, tuple)) else [raw] if isinstance(raw, str) else []
    out: list[str] = []
    for x in items:
        for part in SPLIT.split(x) if isinstance(x, str) else []:
            s = " ".join(part.split()).strip(" 。.")
            if s and s not in out:
                out.append(s)
    return out


def _undouble(w: str) -> str:
    """running → run：词尾去掉以后露出来的双写辅音（l、s、f、z 是词本身的，不动）。"""
    if len(w) > 3 and w[-1] == w[-2] and w[-1] not in "aeioulsfz":
        return w[:-1]
    return w


def _strip(w: str) -> str:
    if len(w) <= 3 or w in NON_PLURAL:
        return w
    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"
    if w.endswith("ing") and len(w) > 5:
        return _undouble(w[:-3])
    if w.endswith("ion") and len(w) > 6:
        return w[:-3]
    if w.endswith("ed") and len(w) > 4:
        return _undouble(w[:-2])
    if w.endswith(("ches", "shes", "sses", "xes", "zes")):
        return w[:-2]
    if w.endswith("s") and not w.endswith(("ss", "us", "is")):
        return w[:-1]
    if w.endswith("ly") and len(w) > 5:
        return w[:-2]
    if w.endswith("er") and len(w) > 5:
        return _undouble(w[:-2])
    if w.endswith("e") and len(w) > 4:
        return w[:-1]
    return w


def stem(word: str) -> str:
    """英文词去词尾（几遍，到不变为止）：revising / revised / revise → revis，meetings → meet，logged → log。数字不动。"""
    w = word
    if w.isdigit():
        return w
    for _ in range(3):
        nxt = _strip(w)
        if nxt == w:
            break
        w = nxt
    return w


def _add(out: dict[str, float], t: str, w: float) -> None:
    if w > out.get(t, 0.0):
        out[t] = w


def terms(text: str) -> dict[str, float]:
    """一段字 → {词: 分量}（见文件开头）。同一个词出现几次只算一次（取分量最大的那次）。"""
    text = text or ""
    if text in _cache:
        return _cache[text]
    low = unicodedata.normalize("NFKC", text).casefold()
    out: dict[str, float] = {}
    for run in CJK.findall(low):
        if len(run) == 1:
            if run not in STOP_ZH:
                _add(out, run, LONE)
            continue
        for i in range(len(run) - 1):
            a, b = run[i], run[i + 1]
            if not (a in STOP_ZH and b in STOP_ZH) and a + b not in STOP_BI:
                _add(out, a + b, HALF if a in STOP_ZH or b in STOP_ZH else BIGRAM)
        for ch in run:
            if ch not in STOP_ZH:
                _add(out, ch, UNIGRAM)
    for w in LATIN.findall(CJK.sub(" ", low)):
        if w in STOP_EN or (len(w) < 2 and not w.isdigit()):
            continue
        _add(out, stem(w), NUMBER if w.isdigit() else WORD)
    with _cache_lock:
        if len(_cache) >= CACHE_MAX:
            _cache.pop(next(iter(_cache)), None)
        _cache[text] = out
    return out


def short_score(q: dict[str, float], field: dict[str, float], idf: dict[str, float]) -> float:
    """名字、「什么时候用」的一句（短的）：它的词有多少落在这句话里（按分量 × IDF 算比例）；落进来的词加起来不到一个整词的（只碰上一个单字）打折。"""
    if not field:
        return 0.0
    total = sum(w * idf.get(t, 1.0) for t, w in field.items())
    got = [t for t in field if t in q]
    if not got or total <= 0:
        return 0.0
    share = sum(field[t] * idf.get(t, 1.0) for t in got) / total
    return share * min(1.0, sum(field[t] for t in got))


def long_score(q: dict[str, float], field: dict[str, float], idf: dict[str, float], k: float, norm: float = 1.0) -> float:
    """说明、正文（长的）：这句话里落在里面的词加起来的分量，饱和成 0–1（见文件开头）。长的一栏里两字词里的单字不算
    （两字词碰上了，它的两个字也一定碰上，算两遍就虚高了）。norm：这一栏比这一批的平均长多少（BM25 的长度归一），越长越难饱和。"""
    m = sum(w * idf.get(t, 1.0) for t, w in q.items() if t in field and w != UNIGRAM)
    return 1.0 - math.exp(-m / (k * norm)) if m > 0 else 0.0


def length(field: dict[str, float]) -> float:
    """一栏有多长（不算单字）：长度归一用。"""
    return sum(w for w in field.values() if w != UNIGRAM)


class Matcher:
    """一批要找的东西（手册、技能……）：每份 {"name", "when"（几句）, "about", "body"}，别的键原样带着。先把每份拆好词、算好 IDF。"""

    def __init__(self, entries: Iterable[dict]):
        self.entries = list(entries)
        self.fields: list[dict[str, object]] = []
        df: dict[str, int] = {}
        for e in self.entries:
            name = terms(str(e.get("name") or ""))
            whens = [terms(p) for p in phrases(e.get("when"))]
            about = terms(str(e.get("about") or ""))
            body = terms(str(e.get("body") or ""))
            self.fields.append({"name": name, "when": whens, "about": about, "body": body})
            seen = set(name) | set(about) | set(body) | {t for w in whens for t in w}
            for t in seen:
                df[t] = df.get(t, 0) + 1
        n = len(self.entries)
        self.idf = {t: 1.0 + math.log((n + 1) / (c + 1)) for t, c in df.items()}  # 碰得上的词都在这批里，这批里没有的词不用算
        self.norm: list[dict[str, float]] = [{} for _ in self.fields]
        for k in SATURATE:  # 长度归一（BM25 的 b）：说明、正文比这一批的平均长的难饱和；比平均短的照旧（不因为短就一碰就满）
            lens = [length(f[k]) for f in self.fields]
            have = [x for x in lens if x > 0]
            avg = sum(have) / len(have) if have else 1.0
            for i, x in enumerate(lens):
                self.norm[i][k] = max(1.0, 1.0 - LENGTH_B + LENGTH_B * x / avg)

    def score_one(self, i: int, q: dict[str, float]) -> tuple[float, dict]:
        """第 i 份对这一种说法（拆好的词）的分 → (分, 每一栏的分)。"""
        f, idf, norm = self.fields[i], self.idf, self.norm[i]
        parts = {"name": short_score(q, f["name"], idf),
                 "when": max((short_score(q, w, idf) for w in f["when"]), default=0.0),
                 "about": long_score(q, f["about"], idf, SATURATE["about"], norm["about"]),
                 "body": long_score(q, f["body"], idf, SATURATE["body"], norm["body"])}
        miss = 1.0
        for k, s in parts.items():
            miss *= 1.0 - FIELDS[k] * s
        return 1.0 - miss, parts

    def rank(self, queries: list[tuple[str, float]], *, boost: dict[int, float] | None = None, limit: int | None = None,
             floor: float = 0.0) -> list[dict]:
        """几种说法（字, 分量）→ 按分从高到低 [{"i", "entry", "score", "hits"（几种说法碰上了）, "fields"（最好的那种说法每一栏的分）,
        "by"（每种说法各自的分，乘过分量，顺序同 queries；空的说法是 0）}]。boost：第几份 → 再乘多少（当前 Agent 自己的、功能包带的优先）。
        分 < floor 的不要。"""
        qs = [(terms(t) if t and w > 0 else {}, w) for t, w in queries]
        out = []
        for i, e in enumerate(self.entries):
            best, fields, hits, by = 0.0, {}, 0, []
            for q, w in qs:
                s, parts = self.score_one(i, q) if q else (0.0, {})
                by.append(round(s * w, 4))
                if s >= AGREE_AT:
                    hits += 1
                if s * w > best:
                    best, fields = s * w, parts
            if best <= 0:
                continue
            score = min(1.0, (best + AGREE * max(0, hits - 1)) * (boost or {}).get(i, 1.0))
            if score >= floor:
                out.append({"i": i, "entry": e, "score": round(score, 4), "hits": hits, "fields": {k: round(v, 3) for k, v in fields.items()},
                            "by": by})
        out.sort(key=lambda x: (-x["score"], str(x["entry"].get("name") or "")))
        return out[:limit] if limit else out

    def covered(self, text: str, at_least: float = 0.5) -> list[tuple[int, str]]:
        """这句话盖住了哪几份的名字、哪几句「什么时候用」（≥ at_least）→ [(第几份, 那一句)]：内核不调模型改写时拿它们当另外几种说法。"""
        q = terms(text)
        if not q:
            return []
        out = []
        for i, (e, f) in enumerate(zip(self.entries, self.fields)):
            cands = [(str(e.get("name") or ""), f["name"]), *zip(phrases(e.get("when")), f["when"])]
            for text_, field in cands:
                if text_ and short_score(q, field, self.idf) >= at_least:
                    out.append((i, text_))
        return out
