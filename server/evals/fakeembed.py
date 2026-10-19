"""假的向量服务（2026-10-16）：本机一个 HTTP 服务，Gemini 和 OpenAI 兼容两种说法都会，不连网、不花钱（engine/embeddings.py、semantic.py 的测试用）。
  POST /v1beta/models/<型号>:embedContent        {content: {parts: [{text}]}, taskType?, outputDimensionality?} → {embedding: {values}, usageMetadata}
  POST /v1beta/models/<型号>:batchEmbedContents  {requests: [{model, content, taskType?, outputDimensionality?}]} → {embeddings: [{values}, …], usageMetadata}
  POST /v1/embeddings（也认 /embeddings）          {model, input: 字 | [字…], dimensions?} → {data: [{index, embedding}], usage}
用量（usageMetadata.promptTokenCount / usage.prompt_tokens）= 收到的字按 words() 切出来的词数；usage = False 时两种都不带（测按字数估）。
engine.recall.embeddings 指过来：{"backend": "gemini", "url": fe.gemini_url, "key_env": "…"} 或 {"backend": "openai", "url": fe.openai_url}。

向量按词算（vector()），固定、不随机：认得的词归到几个「意思」（CONCEPTS：旅行 / 度假 / trip / vacation 都是 travel……），一个意思一维、
  分量大；别的词（英文一个词、中日韩两个字一个词）按型号名哈希到后面那些维、分量小。说法不一样、意思一样的两句余弦就高 —— 测「按意思
  找得到关键词找不到的」；型号名不一样，向量也不一样。回的向量没归一（真的 Gemini 截短了维度也不归一），归一是客户端的事。
剧本：fail(状态码, 几次, retry_after, only) 接下来几个请求回错（429 带 Retry-After，Gemini 的还带 RetryInfo；Gemini 密钥不对回 400
  API_KEY_INVALID，OpenAI 回 401 并把收到的密钥原样写进错误里，测报错里抹掉密钥）；reject(词) 建索引的请求里只要有一段带着这个词，
  整个请求回 400（对方不收某一段字：太长、内容审核……）；delay / doc_delay / query_delay 每个请求先等几秒；
  requests 录下每个请求（不含密钥：auth 只记带没带、对不对）。
命令行（手动试）：python -m evals.fakeembed --port 8766 [--key 密钥]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DIMS = 64
CONCEPT_WEIGHT, WORD_WEIGHT, SCALE = 2.0, 0.35, 3.0
# 一个意思一维（下标就是维）；词全是虚构测试里会用到的常见说法
CONCEPTS: dict[str, tuple[str, ...]] = {
    "travel": ("旅行", "旅游", "度假", "出游", "出行", "trip", "travel", "vacation", "holiday", "journey"),
    "pet": ("猫", "猫咪", "狗狗", "宠物", "cat", "kitten", "dog", "pet"),
    "allergy": ("过敏", "allergy", "allergic"),
    "food": ("花生", "坚果", "早餐", "饮食", "peanut", "breakfast", "diet"),
    "sleep": ("睡眠", "睡觉", "失眠", "熬夜", "sleep", "insomnia"),
    "fitness": ("健身", "训练", "深蹲", "跑步", "workout", "gym", "squat", "running", "exercise"),
    "music": ("音乐", "吉他", "钢琴", "music", "guitar", "piano"),
    "work": ("工作", "面试", "简历", "job", "interview", "resume", "career"),
    "family": ("妈妈", "爸爸", "家人", "mother", "father", "family", "parents"),
    "money": ("预算", "存钱", "花钱", "budget", "savings", "money"),
}
_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯]+")
_WORD = re.compile(r"[a-z0-9]+")


def words(text: str) -> list[str]:
    low = text.lower()
    out = _WORD.findall(_CJK.sub(" ", low))
    for run in _CJK.findall(low):
        out += [run] if len(run) == 1 else [run[i:i + 2] for i in range(len(run) - 1)]
    return out


def vector(text: str, model: str = "fake-embed", dims: int = DIMS) -> list[float]:
    """一段字的向量（没归一，乘了 SCALE）：认得的意思各占一维，别的词哈希到 len(CONCEPTS) 以后的维。"""
    n = max(dims, len(CONCEPTS) + 8)
    v = [0.0] * n
    low = text.lower()
    for i, ws in enumerate(CONCEPTS.values()):
        if any((w in low) if not w.isascii() else re.search(rf"\b{re.escape(w)}\b", low) for w in ws):
            v[i] += CONCEPT_WEIGHT
    rest = n - len(CONCEPTS)
    for w in words(text):
        h = int(hashlib.sha1(f"{model}:{w}".encode()).hexdigest(), 16)
        v[len(CONCEPTS) + h % rest] += WORD_WEIGHT * (1 if h & 1 else -1)
    if not any(v):
        v[-1] = 1.0
    return [x * SCALE for x in v[:dims]]


def cosine(a: list[float], b: list[float]) -> float:
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b, strict=False)) / (na * nb) if na and nb else 0.0


@dataclass
class Failure:
    status: int
    times: int = 1
    retry_after: float | None = None
    only: str | None = None   # "docs"（建索引的）| "query"（搜的那一句）| None（都算）
    message: str = "scripted failure"


class FakeEmbedServer:
    """with FakeEmbedServer() as fe: …；fe.gemini_url / fe.openai_url 给 engine.recall.embeddings 的 url。"""

    def __init__(self, *, key: str | None = None, host: str = "127.0.0.1", port: int = 0) -> None:
        self.key = key
        self.requests: list[dict] = []
        self.failures: list[Failure] = []
        self.rejects: list[tuple[str, int, str]] = []
        self.delay = self.doc_delay = self.query_delay = 0.0
        self.usage = True  # 回答里带不带用量
        self._addr = (host, port)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._httpd: ThreadingHTTPServer | None = None

    def __enter__(self) -> FakeEmbedServer:
        return self.start()

    def __exit__(self, *_exc) -> None:
        self.stop()

    def start(self) -> FakeEmbedServer:
        self._stop.clear()
        self._httpd = ThreadingHTTPServer(self._addr, _handler(self))
        self._httpd.daemon_threads = True
        threading.Thread(target=self._httpd.serve_forever, kwargs={"poll_interval": 0.05}, name="fake-embed", daemon=True).start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None

    @property
    def url(self) -> str:
        assert self._httpd is not None, "the fake embedding server isn't running"
        host, port = self._httpd.server_address[:2]
        return f"http://{host}:{port}"

    @property
    def gemini_url(self) -> str:
        return self.url + "/v1beta"

    @property
    def openai_url(self) -> str:
        return self.url + "/v1"

    def fail(self, status: int, times: int = 1, retry_after: float | None = None, only: str | None = None, message: str = "scripted failure") -> None:
        with self._lock:
            self.failures.append(Failure(status, times, retry_after, only, message))

    def reject(self, word: str, status: int = 400, message: str = "rejected") -> None:
        with self._lock:
            self.rejects.append((word, status, message))

    def _rejected(self, kind: str, texts: list[str]) -> tuple[int, str] | None:
        with self._lock:
            for word, status, message in self.rejects:
                if kind == "docs" and any(word in t for t in texts):
                    return status, message
        return None

    def texts(self, kind: str | None = None) -> list[str]:
        """收到过的所有字（kind = docs / query 只看一种）。"""
        with self._lock:
            return [t for r in self.requests if kind in (None, r["kind"]) for t in r["texts"]]

    def count(self, kind: str | None = None) -> int:
        with self._lock:
            return sum(1 for r in self.requests if kind in (None, r["kind"]))

    def reset(self) -> None:
        with self._lock:
            self.requests.clear()
            self.failures.clear()
            self.rejects.clear()

    def _next_failure(self, kind: str) -> Failure | None:
        with self._lock:
            for f in self.failures:
                if f.only in (None, kind) and f.times > 0:
                    f.times -= 1
                    return f
        return None

    def pause(self, seconds: float) -> None:
        if seconds > 0:
            self._stop.wait(seconds)


def _handler(fe: FakeEmbedServer) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_a) -> None:
            pass

        def _send(self, status: int, payload: dict, headers: dict | None = None) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self) -> None:
            raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            path = self.path.split("?", 1)[0].rstrip("/")
            headers = {k.lower(): v for k, v in self.headers.items()}
            try:
                body = json.loads(raw or b"{}")
            except ValueError:
                self._send(400, {"error": {"message": "invalid json"}})
                return
            m = re.search(r"/models/([^/:]+):(embedContent|batchEmbedContents)$", path)
            if m:
                self._gemini(m.group(1), m.group(2), body, headers)
            elif path.endswith("/embeddings"):
                self._openai(body, headers)
            else:
                self._send(404, {"error": {"message": f"no route {self.path}"}})

        def _record(self, api: str, kind: str, model: str, texts: list[str], dims, task, auth: bool | None) -> None:
            with fe._lock:
                fe.requests.append({"api": api, "kind": kind, "model": model, "texts": texts, "dims": dims, "task": task, "auth": auth,
                                    "t": time.time()})

        def _wait(self, kind: str) -> None:
            fe.pause(fe.delay + (fe.doc_delay if kind == "docs" else fe.query_delay))

        # —— Gemini ——
        def _gemini(self, model: str, op: str, body: dict, headers: dict) -> None:
            reqs = [body] if op == "embedContent" else body.get("requests") or []
            texts = ["".join(str(p.get("text") or "") for p in ((r.get("content") or {}).get("parts") or [])) for r in reqs]
            kind = "query" if op == "embedContent" else "docs"
            key = headers.get("x-goog-api-key")
            ok = fe.key is None or key == fe.key
            self._record("gemini", kind, model, texts, (reqs[0] if reqs else {}).get("outputDimensionality"),
                         (reqs[0] if reqs else {}).get("taskType"), None if fe.key is None else ok)
            self._wait(kind)
            if not ok:
                self._send(400, {"error": {"code": 400, "message": "API key not valid. Please pass a valid API key.", "status": "INVALID_ARGUMENT",
                                           "details": [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "API_KEY_INVALID"}]}})
                return
            if (f := fe._next_failure(kind)) is not None:
                details = [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": f"{f.retry_after:g}s"}] if f.retry_after is not None else []
                self._send(f.status, {"error": {"code": f.status, "message": f.message, "status": "RESOURCE_EXHAUSTED" if f.status == 429 else "ERROR",
                                                "details": details}},
                           {"Retry-After": f"{f.retry_after:g}"} if f.retry_after is not None and f.status == 429 else None)
                return
            if (rj := fe._rejected(kind, texts)) is not None:
                self._send(rj[0], {"error": {"code": rj[0], "message": rj[1], "status": "INVALID_ARGUMENT"}})
                return
            vecs = [vector(t, model, int(r.get("outputDimensionality") or DIMS)) for t, r in zip(texts, reqs, strict=True)]
            used = {"usageMetadata": {"promptTokenCount": sum(len(words(t)) for t in texts)}} if fe.usage else {}
            if op == "embedContent":
                self._send(200, {"embedding": {"values": vecs[0]}, **used})
            else:
                self._send(200, {"embeddings": [{"values": v} for v in vecs], **used})

        # —— OpenAI 兼容 ——
        def _openai(self, body: dict, headers: dict) -> None:
            inp = body.get("input")
            texts = [inp] if isinstance(inp, str) else [str(x) for x in inp or []]
            model = str(body.get("model") or "")
            auth = headers.get("authorization") or ""
            ok = fe.key is None or auth == f"Bearer {fe.key}"
            kind = "query" if isinstance(inp, str) else "docs"  # 内核搜的那一句发字符串、建索引的发列表（embeddings._openai）
            self._record("openai", kind, model, texts, body.get("dimensions"), None, None if fe.key is None else ok)
            self._wait(kind)
            if not ok:
                got = auth.removeprefix("Bearer ").strip()
                self._send(401, {"error": {"message": f"Incorrect API key provided: {got}. You can find your API key at …",
                                           "type": "invalid_request_error", "code": "invalid_api_key"}})
                return
            if (f := fe._next_failure(kind)) is not None:
                self._send(f.status, {"error": {"message": f.message, "type": "rate_limit" if f.status == 429 else "server_error"}},
                           {"Retry-After": f"{f.retry_after:g}"} if f.retry_after is not None else None)
                return
            if (rj := fe._rejected(kind, texts)) is not None:
                self._send(rj[0], {"error": {"message": rj[1], "type": "invalid_request_error"}})
                return
            dims = int(body.get("dimensions") or DIMS)
            data = [{"object": "embedding", "index": i, "embedding": vector(t, model, dims)} for i, t in enumerate(texts)]
            n = sum(len(words(t)) for t in texts)
            used = {"usage": {"prompt_tokens": n, "total_tokens": n}} if fe.usage else {}
            self._send(200, {"object": "list", "data": list(reversed(data)), "model": model, **used})

    return Handler


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m evals.fakeembed", description="假的向量服务（Gemini + OpenAI 兼容）")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--key", help="要求带的密钥（不写就不查）")
    a = ap.parse_args(argv)
    fe = FakeEmbedServer(key=a.key, host=a.host, port=a.port).start()
    print(f"fake embeddings on {fe.url}  Gemini: {fe.gemini_url}  OpenAI: {fe.openai_url}", flush=True)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        fe.stop()


if __name__ == "__main__":
    main()
