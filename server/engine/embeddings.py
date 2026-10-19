"""记忆检索的向量（内核第 7 项的下一步，2026-10-16）：把一段字变成一串数，意思相近的两段，数也相近（余弦大）。engine/semantic.py 用它。

不配 = 不开：和以前一样只按关键词，一个请求都不发（开源用户的记忆、对话不会悄悄发给第三方；进程里有 GEMINI_API_KEY 也不算配了）。
配了才把记忆文件、日结、对话、世界树的字发给你配的那一家去算。两种接口：
  gemini   Google 的 models/<型号>:batchEmbedContents（建索引，taskType RETRIEVAL_DOCUMENT）和 :embedContent（搜的那一句，
           RETRIEVAL_QUERY）。默认型号 gemini-embedding-001、地址 https://generativelanguage.googleapis.com/v1beta、密钥 GEMINI_API_KEY
           （放请求头 x-goog-api-key，不进地址，日志、报错里就没有它）。dims 写进 outputDimensionality，默认 768：3072 维的四分之一大，
           几万段时库小、搜得快（这个型号截短了照样能用，Google 推荐 768 / 1536 / 3072；截短的不归一，归一在这里做）。
           gemini-embedding-2 不收 taskType：改成把「task: … | query:」写进字里（Google 的文档、OpenClaw 的做法）
  openai   OpenAI 兼容的 POST <url>/embeddings（OpenAI、本机的 Ollama / LM Studio / vLLM……）。默认 https://api.openai.com/v1、
           text-embedding-3-small、OPENAI_API_KEY（Bearer）；key_env 写 "" = 不要密钥（本机的）；dims 写了才发 dimensions
           （不认这个字段的服务就别写）
server.json（都可不写，只写 "embeddings": "gemini" 或 "openai" = 全用默认；false / 不写 = 不开）：
  "engine": {"recall": {"embeddings": {
      "backend": "gemini", "model": "gemini-embedding-001", "dims": 768, "key_env": "GEMINI_API_KEY", "url": "…", "headers": {…},
      "batch": 50,           一个请求最多几段（Gemini 一次最多 100）
      "rpm": 30, "tpm": 0,   后台补向量的限速：每分钟最多几个请求、几个 token（0 = 不限；Gemini 免费档大约 100 RPM、3 万 TPM）
      "timeout": 30,         后台一个请求最多等几秒
      "query_timeout": 8,    搜的时候算那一句最多等几秒（超了这一次只按关键词）
      "min_score": 0.35, "vector_weight": 0.7,   排序（engine/semantic.py）
      "query_prefix": "…", "doc_prefix": "…"}}}  有的本机模型要在字前面加一句（nomic-embed-text、embeddinggemma 认得出来，自动加）
  密钥照 models.env_value：先看进程环境，再看 app 里存的 secrets.env，再看 server.json 的 env_file。每次现读，改了不用重启。
出错分几类（EmbedError.kind）：rate（429，带 Retry-After / Google 的 RetryInfo）、auth（401 / 403 / 没密钥）、bad（400 / 404 / 422：型号、
  参数不对）、server（5xx）、net（连不上、超时）、shape（回来的不像样：条数、维度对不上）。报错的字里抹掉密钥和像密钥的串。
用量：算成了的每个请求都记进 engine/usage.py（token 用接口回的：Gemini 的 usageMetadata.promptTokenCount、OpenAI 兼容的 usage；没回的
  按字数估、标成估的）。只在内存里加一下，攒一批再写库，不拖慢建索引。
请求的写法和出错的分类照 OpenClaw（openclaw/openclaw commit 6552b30：extensions/google/embedding-provider.ts、
extensions/openai/embedding-provider.ts、memory-host-sdk 的 embeddings-remote-provider.ts / embedding-inputs.ts、memory-core 的
manager-embedding-policy.ts），只借设计。
"""
from __future__ import annotations

import array
import hashlib
import json
import math
import re
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from config import raw
from i18n import L

FORMAT = 1  # 发出去的字怎么拼（semantic.embed_input）、怎么归一；改了就是另一套向量，整个重建
BACKENDS: dict[str, dict] = {
    "gemini": {"url": "https://generativelanguage.googleapis.com/v1beta", "model": "gemini-embedding-001", "key_env": "GEMINI_API_KEY",
               "dims": 768, "batch": 50, "max_batch": 100},
    "openai": {"url": "https://api.openai.com/v1", "model": "text-embedding-3-small", "key_env": "OPENAI_API_KEY",
               "dims": None, "batch": 64, "max_batch": 2048},
}
RPM, TPM, TIMEOUT, QUERY_TIMEOUT = 30, 0, 30.0, 8.0
MIN_SCORE, VECTOR_WEIGHT = 0.35, 0.7      # OpenClaw 的 minScore、hybrid.vectorWeight
REQUEST_BYTES = 120_000                     # 一个请求最多带多少字节（再多拆成两个；OpenClaw 按 8000 token 一批）
ERROR_MAX = 300
# 有的模型要在字前面说一句「这是查询 / 这是文档」（模型卡上写的）；名字里认得出来就自动加，query_prefix / doc_prefix 能改
PREFIXES = (
    (re.compile(r"nomic-embed-text", re.I), "search_query: ", "search_document: "),
    (re.compile(r"(?:^|[/:])(?:text-embedding-)?embeddinggemma(?:[-:._]|$)", re.I), "task: search result | query: ", "title: none | text: "),
    (re.compile(r"^gemini-embedding-2$"), "task: search result | query: ", "title: none | text: "),  # 不收 taskType，要写进字里（Google 的文档）
)
NO_TASK_TYPE = re.compile(r"^gemini-embedding-2$")  # 照 OpenClaw：只有正式版的 gemini-embedding-2 不带 taskType（-preview 照带）


class EmbedError(Exception):
    """一次请求没算成。kind 见文件开头；retry_after = 对方说的「几秒后再来」。"""

    def __init__(self, kind: str, message: str, retry_after: float | None = None):
        self.kind, self.retry_after = kind, retry_after
        super().__init__(message)


@dataclass(frozen=True)
class Spec:
    backend: str
    model: str
    url: str
    key_env: str
    dims: int | None
    batch: int
    rpm: int
    tpm: int
    timeout: float
    query_timeout: float
    min_score: float
    vector_weight: float
    query_prefix: str
    doc_prefix: str
    headers: tuple[tuple[str, str], ...] = ()

    @property
    def identity(self) -> str:
        """这一套向量是谁算的（存在 recall.db 的 meta 里）：后端、地址、型号、维度、前缀和拼字的写法。换了哪一样，旧的向量就对不上了。
        批量、限速、超时不算。存的是指纹，不是原样（地址里万一带着什么也不落盘）。"""
        body = json.dumps([self.backend, self.url, self.model, self.dims, self.query_prefix, self.doc_prefix, FORMAT], ensure_ascii=False)
        return hashlib.sha1(body.encode(), usedforsecurity=False).hexdigest()[:16]

    @property
    def label(self) -> str:
        return f"{self.backend}/{self.model}" + (f" · {self.dims}" if self.dims else "")

    def key(self) -> str:
        if not self.key_env:
            return ""
        from .models import env_value
        return env_value(self.key_env).strip()  # Docker / k8s 的 secret 文件常带一个换行

    def secrets(self) -> list[str]:
        """报错的字里要抹掉的：密钥、额外请求头的值（里面也可能是密钥）。"""
        return [x for x in (self.key(), *(v for _, v in self.headers)) if x]


def config() -> dict | str | None:
    e = raw().get("engine")
    r = e.get("recall") if isinstance(e, dict) else None
    return r.get("embeddings") if isinstance(r, dict) else None


def _num(v, default, lo, hi, cast=float):
    try:
        x = cast(v)
    except (TypeError, ValueError):
        return default
    return default if isinstance(v, bool) or not (lo <= x <= hi) else x


def problem() -> str | None:
    """engine.recall.embeddings 写了但写错了（状态里给用户看）；没写、写对了 → None。"""
    return problem_of(config())


def problem_of(c) -> str | None:
    """同 problem()，看的是给的这一份（第 9 步的设置页先测再存）。"""
    if c is None or c is False:
        return None
    if isinstance(c, dict) and c.get("enabled") is False:
        return None
    name = c if isinstance(c, str) else c.get("backend") if isinstance(c, dict) else None
    if str(name or "").strip().lower() not in BACKENDS:
        return L(f"engine.recall.embeddings 的 backend 只能是 {' / '.join(BACKENDS)}", f"engine.recall.embeddings: backend must be one of {', '.join(BACKENDS)}")
    return None


def spec() -> Spec | None:
    """现在的配置 → Spec；没配、关了、写错了 → None（= 不开）。"""
    return spec_from(config())


def spec_from(c) -> Spec | None:
    """一份 engine.recall.embeddings 的写法 → Spec（spec() 用现在的配置；第 9 步的设置页拿还没存的那一份先测）。"""
    if c is None or c is False or problem_of(c):
        return None
    d = {"backend": c} if isinstance(c, str) else dict(c)
    if d.get("enabled") is False:
        return None
    backend = str(d.get("backend")).strip().lower()
    base = BACKENDS[backend]
    model = str(d.get("model") or base["model"]).strip()
    if backend == "gemini":
        model = model.removeprefix("models/")
    dims = d.get("dims", base["dims"])
    dims = _num(dims, base["dims"], 1, 65536, int) if dims is not None else None
    batch = _num(d.get("batch"), base["batch"], 1, base["max_batch"], int)
    qp = dp = ""
    for pat, q, p in PREFIXES:
        if pat.search(model):
            qp, dp = q, p
    qp = str(d["query_prefix"]) if isinstance(d.get("query_prefix"), str) else qp
    dp = str(d["doc_prefix"]) if isinstance(d.get("doc_prefix"), str) else dp
    headers = d.get("headers") if isinstance(d.get("headers"), dict) else {}
    key_env = d.get("key_env", base["key_env"])
    return Spec(backend=backend, model=model, url=str(d.get("url") or base["url"]).strip().rstrip("/"),
                key_env=str(key_env).strip() if isinstance(key_env, str) else base["key_env"], dims=dims, batch=batch,
                rpm=_num(d.get("rpm"), RPM, 0, 100_000, int), tpm=_num(d.get("tpm"), TPM, 0, 100_000_000, int),
                timeout=_num(d.get("timeout"), TIMEOUT, 1, 600), query_timeout=_num(d.get("query_timeout"), QUERY_TIMEOUT, 0.5, 120),
                min_score=_num(d.get("min_score"), MIN_SCORE, 0, 1), vector_weight=_num(d.get("vector_weight"), VECTOR_WEIGHT, 0, 1),
                query_prefix=qp, doc_prefix=dp, headers=tuple((str(k), str(v)) for k, v in headers.items()))


# —— 向量 ————————————————————————————————————————————————————————————————————

def normalize(values) -> array.array:
    """→ 归一过的 float32（余弦 = 点积）。不是一串有限的数、全是 0 → EmbedError(shape)。"""
    if not isinstance(values, list) or not values:
        raise EmbedError("shape", "the service returned no vector")
    try:
        xs = [float(x) for x in values]
    except (TypeError, ValueError):
        raise EmbedError("shape", "the vector isn't a list of numbers") from None
    if not all(math.isfinite(x) for x in xs):
        raise EmbedError("shape", "the vector has NaN or infinite values")
    n = math.sqrt(math.fsum(x * x for x in xs))
    if n < 1e-12:
        raise EmbedError("shape", "the vector is all zeros")
    return array.array("f", [x / n for x in xs])


def tokens(text: str) -> int:
    """粗估 token（限速用）：UTF-8 字节 / 3（中文一个字算一个，英文偏多一点，宁可多算）。"""
    return len(text.encode()) // 3 + 1


# —— 请求 ————————————————————————————————————————————————————————————————————
TRANSPORT: httpx.BaseTransport | None = None  # 测试可以换成 httpx.MockTransport（现在的测试用 evals/fakeembed.py 的真服务）
_KEYISH = re.compile(r"(?:sk-|AIza|ya29\.|gsk_|xai-)[A-Za-z0-9_\-.]{8,}")


def scrub(text: str, *secrets: str) -> str:
    """报错的字：抹掉密钥（原样的、转义过的，和像密钥的串），太长截掉。"""
    s = str(text or "")
    for sec in secrets:
        core = str(sec or "").strip()
        if len(core) >= 4:
            for form in {core, repr(core.encode())[2:-1], repr(core)[1:-1]}:
                s = s.replace(form, "***")
    s = _KEYISH.sub("***", s)
    s = " ".join(s.split())
    return s if len(s) <= ERROR_MAX else s[:ERROR_MAX] + "…"


def _retry_after(r: httpx.Response, body) -> float | None:
    """几秒后再来：Retry-After 头，或者 Google 错误里的 RetryInfo.retryDelay（"12s"、"0.5s"）。"""
    v = r.headers.get("retry-after")
    if v:
        try:
            return max(0.0, float(v))
        except ValueError:
            pass
    details = ((body or {}).get("error") or {}).get("details") if isinstance(body, dict) else None
    for d in details if isinstance(details, list) else []:
        m = re.fullmatch(r"(\d+(?:\.\d+)?)s", str((d or {}).get("retryDelay") or "")) if isinstance(d, dict) else None
        if m:
            return float(m.group(1))
    return None


_BAD_KEY = re.compile(r"api[ _-]?key|API_KEY_INVALID|unauthenticated|permission", re.I)
_KEY_CHARS = re.compile(r"[\x21-\x7e]+")              # 密钥：看得见的 ASCII（没有空格、换行、中文）
_HEADER_NAME = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+")
_HEADER_VALUE = re.compile(r"[\x20-\x7e\t]*")


def _fail(spec: Spec, r: httpx.Response) -> EmbedError:
    try:
        body = r.json()
    except ValueError:
        body = None
    err = body.get("error") if isinstance(body, dict) else None
    msg = (err.get("message") if isinstance(err, dict) else err if isinstance(err, str) else None) or r.text[:500] or r.reason_phrase
    s = r.status_code
    kind = "rate" if s == 429 else "auth" if s in (401, 403) else "server" if s >= 500 else "bad"
    if kind == "bad" and _BAD_KEY.search(f"{msg} {json.dumps(err, ensure_ascii=False) if err else ''}"):
        kind = "auth"  # Gemini 密钥不对回的是 400 INVALID_ARGUMENT（reason API_KEY_INVALID），不是 401
    return EmbedError(kind, scrub(f"{spec.backend} HTTP {s}: {msg}", *spec.secrets()), _retry_after(r, body) if s in (429, 503) else None)


def _post(spec: Spec, path: str, body: dict, timeout: float) -> dict:
    key = spec.key()
    if spec.key_env and not key:
        raise EmbedError("auth", L(f"没有密钥：{spec.key_env} 没设（进程环境、app 里存的密钥和 env_file 里都没有）",
                                   f"No key: {spec.key_env} isn't set (not in the environment, the keys saved in the app or the env_file)"))
    if key and not _KEY_CHARS.fullmatch(key):  # 发出去之前就拦下：httpx 拒收时的报错里带着整个请求头（连密钥）
        raise EmbedError("auth", L(f"{spec.key_env} 里的密钥有放不进请求头的字（空格、换行、中文……）",
                                   f"The key in {spec.key_env} has characters that can't go in a request header (spaces, line breaks, non-ASCII)"))
    for name, value in spec.headers:
        if not _HEADER_NAME.fullmatch(name) or not _HEADER_VALUE.fullmatch(value):
            raise EmbedError("bad", L(f"headers 里的「{name}」有放不进请求头的字", f"The header {name!r} in headers has characters that can't be sent"))
    headers = {"Content-Type": "application/json", **dict(spec.headers)}
    if key and spec.backend == "gemini":
        headers["x-goog-api-key"] = key
    elif key:
        headers["Authorization"] = f"Bearer {key}"
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout, connect=min(10.0, timeout)), transport=TRANSPORT) as c:
            r = c.post(f"{spec.url}/{path}", json=body, headers=headers)
    except httpx.TimeoutException:
        raise EmbedError("net", f"{spec.backend}: no answer within {timeout:g}s") from None
    except httpx.LocalProtocolError as e:  # 请求自己不合规矩：报错的字里可能带着请求头，不往外放
        raise EmbedError("bad", f"{spec.backend}: {type(e).__name__}") from None
    except httpx.HTTPError as e:
        raise EmbedError("net", scrub(f"{spec.backend}: {type(e).__name__}: {e}", *spec.secrets())) from None
    except (httpx.InvalidURL, ValueError) as e:  # 地址写错了（UnicodeError 也是 ValueError）
        raise EmbedError("bad", L(f"{spec.backend}：地址或请求头写得不对（{type(e).__name__}）",
                                  f"{spec.backend}: the url or a header isn't valid ({type(e).__name__})")) from None
    if r.status_code >= 400:
        raise _fail(spec, r)
    try:
        data = r.json()
    except ValueError:
        raise EmbedError("shape", f"{spec.backend}: the answer isn't JSON") from None
    if not isinstance(data, dict):
        raise EmbedError("shape", f"{spec.backend}: the answer isn't a JSON object")
    return data


def _check(spec: Spec, vecs: list[array.array], n: int) -> list[array.array]:
    if len(vecs) != n:
        raise EmbedError("shape", f"{spec.backend}: asked for {n} vectors, got {len(vecs)}")
    dims = {len(v) for v in vecs}
    if len(dims) > 1 or (spec.dims and dims and dims != {spec.dims}):
        raise EmbedError("shape", f"{spec.backend}: expected {spec.dims or 'equal'} dimensions, got {sorted(dims)}")
    return vecs


def _reported(data: dict) -> int | None:
    """这个请求接口说用了多少 token：Gemini 的 usageMetadata.promptTokenCount、OpenAI 兼容的 usage.prompt_tokens（没有再看 total_tokens）；
    没回、不像样 → None（记用量时按字数估）。"""
    u = data.get("usageMetadata") if isinstance(data.get("usageMetadata"), dict) else data.get("usage")
    for k in ("promptTokenCount", "prompt_tokens", "total_tokens"):
        v = u.get(k) if isinstance(u, dict) else None
        if isinstance(v, int) and not isinstance(v, bool) and v >= 0:
            return v
    return None


def _gemini(spec: Spec, texts: list[str], query: bool, timeout: float) -> tuple[list[array.array], int | None]:
    path = f"models/{spec.model}"
    extra: dict = {"outputDimensionality": spec.dims} if spec.dims else {}
    if not NO_TASK_TYPE.match(spec.model):
        extra["taskType"] = "RETRIEVAL_QUERY" if query else "RETRIEVAL_DOCUMENT"
    if query and len(texts) == 1:
        data = _post(spec, f"{path}:embedContent", {"content": {"parts": [{"text": texts[0]}]}, **extra}, timeout)
        e = data.get("embedding")
        return _check(spec, [normalize(e.get("values") if isinstance(e, dict) else None)], 1), _reported(data)
    reqs = [{"model": path, "content": {"parts": [{"text": t}]}, **extra} for t in texts]
    data = _post(spec, f"{path}:batchEmbedContents", {"requests": reqs}, timeout)
    es = data.get("embeddings")
    if not isinstance(es, list):
        raise EmbedError("shape", "gemini: no embeddings in the answer")
    return _check(spec, [normalize(e.get("values") if isinstance(e, dict) else None) for e in es], len(texts)), _reported(data)


def _openai(spec: Spec, texts: list[str], query: bool, timeout: float) -> tuple[list[array.array], int | None]:
    body: dict = {"model": spec.model, "input": texts[0] if query and len(texts) == 1 else texts}  # 搜的那一句发字符串，建索引的发列表
    if spec.dims:
        body["dimensions"] = spec.dims
    data = _post(spec, "embeddings", body, timeout)
    rows = data.get("data")
    if not isinstance(rows, list):
        raise EmbedError("shape", "openai: no data in the answer")
    try:
        rows = sorted(rows, key=lambda d: int(d.get("index", 0)) if isinstance(d, dict) else 0)
    except (TypeError, ValueError):
        raise EmbedError("shape", "openai: bad index in the answer") from None
    return _check(spec, [normalize(d.get("embedding") if isinstance(d, dict) else None) for d in rows], len(texts)), _reported(data)


def embed(spec: Spec, texts: list[str], *, query: bool = False, timeout: float | None = None, kind: str | None = None) -> list[array.array]:
    """一批字 → 一批归一过的向量（顺序和 texts 一样）。query = 搜的那一句（Gemini 的 taskType、本机模型的前缀不一样）。出错抛 EmbedError。
    算成了就记进用量（engine/usage.embedded）：kind = 记成哪一种（index 建索引 / query 搜的那一句 / test 设置页测一下；不给按 query 定）。"""
    if not texts:
        return []
    pre = spec.query_prefix if query else spec.doc_prefix
    texts = [pre + t for t in texts] if pre else list(texts)
    t = timeout or (spec.query_timeout if query else spec.timeout)
    vecs, used = _gemini(spec, texts, query, t) if spec.backend == "gemini" else _openai(spec, texts, query, t)
    from . import usage
    usage.embedded(spec, texts, used, kind=kind or ("query" if query else "index"))
    return vecs


# —— 限速 ————————————————————————————————————————————————————————————————————

class Limiter:
    """后台补向量的限速：往回数 60 秒，请求数不超过 rpm、token 数不超过 tpm（0 = 不限）。一个请求本身就超过 tpm 的照发（不然永远发不出去）。
    clock / sleep 能换（测试用）；sleep 回 True = 叫停了。"""

    def __init__(self, clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], bool] | None = None):
        self.clock = clock
        self.sleep = sleep
        self.log: deque[tuple[float, int]] = deque()
        self.lock = threading.Lock()

    def wait(self, rpm: int, tpm: int, cost: int, stop: threading.Event | None = None) -> bool:
        """等到能发 → True；等的时候被叫停（stop 置位）→ False。"""
        while True:
            with self.lock:
                now = self.clock()
                while self.log and now - self.log[0][0] >= 60:
                    self.log.popleft()
                used = sum(c for _, c in self.log)
                if (not rpm or len(self.log) < rpm) and (not tpm or not self.log or used + cost <= tpm):
                    self.log.append((now, cost))
                    return True
                delay = max(0.05, 60 - (now - self.log[0][0]) + 0.05)
            if self.sleep is not None:
                stopped = self.sleep(delay)
            elif stop is not None:
                stopped = stop.wait(delay)
            else:
                time.sleep(delay)
                stopped = False
            if stopped:
                return False


def batches(texts: list[str], size: int) -> list[list[int]]:
    """几段字 → 一批批的下标：一批最多 size 段、REQUEST_BYTES 字节。"""
    out: list[list[int]] = []
    cur: list[int] = []
    used = 0
    for i, t in enumerate(texts):
        n = len(t.encode())
        if cur and (len(cur) >= size or used + n > REQUEST_BYTES):
            out.append(cur)
            cur, used = [], 0
        cur.append(i)
        used += n
    if cur:
        out.append(cur)
    return out
