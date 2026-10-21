"""认密钥：写入和导出之前把认出来的换成占位符。只记隐去了几处，不留原值。

认这些（都要带至少一个数字，免得长单词被当成密钥）：
- 各家前缀：Notion ntn_ / secret_、Perplexity pplx-、OpenAI sk- / sk-proj-、Anthropic sk-ant-、
  GitHub ghp_ / github_pat_ / gho_、xAI xai-、Google AIza、Slack xox[abpr]-、AWS AKIA、Stripe sk_live_ / rk_live_
- 形状：JWT（三段 base64 用点连起来）、PEM 私钥块、网址里的 token= / key= 参数、32 位以上的高熵串
  （路径、slug、变量名这种按 / _ - 切开每段都像词的不算）
- UUID 和 32 / 40 / 64 位十六进制（uuid4().hex、git 的 SHA-1 / SHA-256 提交号、Notion 页面 id）只有旁边（左右各约 40 字，
  不含它自己）出现 key / token / secret / password / bearer（或密钥、秘钥、令牌、口令、密码）才算；高熵那条看的是去掉这些 id 以后的样子

同一处被好几条规则认到只算一处。占位符按当时的语言：‹已隐去› / ‹redacted›。
"""
from __future__ import annotations

import math
import re
from collections import Counter

from . import config as C

# 正文最多 8192 字（4096 位的 RSA 私钥约 3.3 KB）：没有 END 的 BEGIN 不会一路扫到文末
_PEM = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.{0,8192}?-----END [A-Z0-9 ]*PRIVATE KEY-----", re.DOTALL)
# 参数名就是 token / key，或者以 _token / -token / _key / -key 结尾（access_token、api_key）。值至少 8 位。
_URL = re.compile(r"(?i)(?:[?&#](?:[A-Za-z0-9_-]*[-_])?(?:token|key))=([A-Za-z0-9._~%+/=-]{8,})")
# 前面也不能贴着 _ / -：不然一长串 base64url 里每个 - 后面都会再试一次、每次扫到串尾（二次方）
_JWT = re.compile(r"(?<![A-Za-z0-9_-])([A-Za-z0-9_-]{8,})\.([A-Za-z0-9_-]{8,})\.([A-Za-z0-9_-]{8,})(?![A-Za-z0-9])")
# 长的前缀放前面，sk-proj- / sk-ant- 才不会被 sk- 先吞掉半截。
_PREFIX = re.compile(
    r"(?<![A-Za-z0-9])(?:"
    r"sk-proj-[A-Za-z0-9_-]{20,}|sk-ant-[A-Za-z0-9_-]{20,}|sk-[A-Za-z0-9_-]{20,}|"
    r"github_pat_[A-Za-z0-9_]{20,}|ghp_[A-Za-z0-9]{20,}|gho_[A-Za-z0-9]{20,}|"
    r"ntn_[A-Za-z0-9]{20,}|secret_[A-Za-z0-9]{20,}|pplx-[A-Za-z0-9]{20,}|xai-[A-Za-z0-9]{20,}|"
    r"AIza[A-Za-z0-9_-]{30,}|xox[abpr]-[A-Za-z0-9-]{10,}|AKIA[A-Z0-9]{16}|"
    r"sk_live_[A-Za-z0-9]{10,}|rk_live_[A-Za-z0-9]{10,}"
    r")(?![A-Za-z0-9])"
)
_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_HEXID = re.compile(r"(?<![0-9A-Za-z])(?:[0-9a-fA-F]{64}|[0-9a-fA-F]{40}|[0-9a-fA-F]{32})(?![0-9A-Za-z])")
_IDS = re.compile(_UUID.pattern + "|" + _HEXID.pattern)  # 长串里夹着的 id：高熵那条先把它们拿掉再算
_NEAR = re.compile(r"(?i)(?<![A-Za-z0-9])(?:key|token|secret|password|passwd|bearer)s?(?![A-Za-z0-9])|密钥|秘钥|令牌|口令|密码")
_ENT = re.compile(r"(?<![A-Za-z0-9+/_=-])[A-Za-z0-9+/_=-]{32,}(?![A-Za-z0-9+/_=-])")
_SEP = re.compile(r"[/_-]+")
# 像词的一段：字母开头的词（可以驼峰）、末尾最多 4 位数字；全大写加几位数字；纯数字；v2、x86 这种短的。
# 驼峰按大写字母分段，别写成 (?:[A-Z]?[a-z]+)+：一长串小写字母后面对不上时那样会指数级回溯
_WORD = re.compile(r"[A-Z]?[a-z]+(?:[A-Z][a-z]+)*[0-9]{0,4}|[A-Z]+[0-9]{0,4}|[0-9]+|[A-Za-z]{1,4}[0-9]{1,4}")
_NEAR_WINDOW = 40
_ENTROPY_MIN = 3.5


def note(n: int) -> str:
    """给人看的那一句（Markdown 笔记的属性）。按调用时的语言，别在导入时调用。"""
    return C.L(f"隐去过 {n} 处", f"redacted {n}")


def count_of(v) -> int:
    """库里的整数，或笔记属性里的「隐去过 N 处」/ redacted N → N。没有就是 0。"""
    if isinstance(v, bool) or v is None:
        return 0
    if isinstance(v, int):
        return v if v > 0 else 0
    m = re.search(r"\d+", str(v))
    return int(m.group()) if m else 0


def _entropy(s: str) -> float:
    n = len(s)
    if n == 0:
        return 0.0
    return -sum((c / n) * math.log2(c / n) for c in Counter(s).values())


def _digit(s: str) -> bool:
    return any(ch.isdigit() for ch in s)


def _wordy(s: str) -> bool:
    """路径、slug、变量名（files/data/2026-10、snake_case_name_v2）：按 / _ - 切开，每段要么不到 8 个字、要么像词。随机的密钥几乎不会这样。"""
    return all(len(p) < 8 or _WORD.fullmatch(p) for p in _SEP.split(s) if p)


def _coalesce(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """叠在一起的只留一段：起点早的优先，一样长就留更长的。挨着但没重叠的仍算两处。"""
    if not spans:
        return []
    spans.sort(key=lambda x: (x[0], x[0] - x[1]))
    merged: list[list[int]] = [[spans[0][0], spans[0][1]]]
    for a, b in spans[1:]:
        if a >= merged[-1][1]:
            merged.append([a, b])
        elif b > merged[-1][1]:
            merged[-1][1] = b
    return [(a, b) for a, b in merged]


def redact(text: str) -> tuple[str, int]:
    """→ (换过的文字, 隐去了几处)。空的原样返回。再过一遍不会把占位符再认成密钥。"""
    if not isinstance(text, str):
        text = "" if text is None else str(text)
    if not text:
        return text, 0
    spans: list[tuple[int, int]] = [(m.start(), m.end()) for m in _PEM.finditer(text)]
    spans += [(m.start(1), m.end(1)) for m in _URL.finditer(text)]
    for m in _JWT.finditer(text):
        whole = m.group(0)
        a, b, c = m.group(1), m.group(2), m.group(3)
        if a.startswith("eyJ") or (min(len(a), len(b), len(c)) >= 16 and _digit(whole) and _entropy(whole) >= _ENTROPY_MIN):
            spans.append((m.start(), m.end()))
    spans += [(m.start(), m.end()) for m in _PREFIX.finditer(text) if _digit(m.group(0))]
    for rx in (_UUID, _HEXID):
        for m in rx.finditer(text):
            window = text[max(0, m.start() - _NEAR_WINDOW):m.start()] + text[m.end():m.end() + _NEAR_WINDOW]
            if _NEAR.search(window):
                spans.append((m.start(), m.end()))
    for m in _ENT.finditer(text):
        core = _IDS.sub("-", m.group(0))  # 提交网址、/items/<uuid>：id 归上面那条管，剩下的才看熵
        if not _digit(core) or _entropy(core) < _ENTROPY_MIN or _wordy(core):
            continue
        spans.append((m.start(), m.end()))
    kept = _coalesce(spans)
    if not kept:
        return text, 0
    placeholder = C.L("‹已隐去›", "‹redacted›")
    out: list[str] = []
    last = 0
    for a, b in kept:
        out.append(text[last:a])
        out.append(placeholder)
        last = b
    out.append(text[last:])
    return "".join(out), len(kept)
