"""测一下、本机探测（第 9 步「通用的选择层」，2026-10-17；/api/setup/… 和 setup_ctl.py 用）。只问不改：不碰配置、不存密钥；回的报错
不带密钥（errors.scrub 抹掉这一把和像密钥的串，再截短）。

  test_model(p, key, model)      一家模型服务：先列模型（openai 兼容 GET <url>/models、Claude GET <根>/v1/models、Ollama GET <根>/api/tags）；
                                 列不了（这家没有、回 404 / 405 / 501）或者指定了型号，就发一句最小的请求（「ping」，最多 16 个 token：
                                 只有列不了模型的那几家会花一点点钱）
                                 → {"ok", "via": "list" | "request", "models", "count", "listed"（models 是不是列出来的全部）, "ms",
                                    "error": {"kind", "message"} | None}
  detect_local(entry, url)       本机跑的（Ollama、LM Studio、vLLM、llama.cpp、MLX）：GET 它不加载模型也会回的那个地址，等 LOCAL_TIMEOUT 秒；
                                 认出来了再列它装着的模型 → {"detected", "url", "version", "models"}。指着这台服务器自己的端口的不探
  test_search(name, key, url)    搜一句（web.search，同一套请求）→ {"ok", "results", "error"}
  test_embeddings(conf)          算一个向量（embeddings.embed）→ {"ok", "dims", "error"}
error.kind：no_key（要密钥但没给）、no_model（列不了模型又没给型号）、auth（401 / 403：密钥不对）、billing（402：欠费 / 额度）、
  not_found（404：地址或型号不对）、rate（429）、server（5xx）、bad（别的 4xx）、network（连不上、超时）、shape（回的不像样）。
"""
from __future__ import annotations

import asyncio
import re
import time
from urllib.parse import urlsplit

import httpx

from config import settings
from i18n import L

from .errors import scrub

TRANSPORT: httpx.AsyncBaseTransport | None = None  # 测试可以换成 httpx.MockTransport（现在的测试用本机的假服务）
LIST_TIMEOUT = 15.0
REQUEST_TIMEOUT = 30.0
LOCAL_TIMEOUT = 0.8
ANTHROPIC_VERSION = "2023-06-01"
ANTHROPIC_URL = "https://api.anthropic.com"
PING = "ping"
PING_TOKENS = 16
MAX_MODELS = 2000
ERR_MAX = 240
NO_LIST = {404, 405, 501}


def _client(timeout: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=min(10.0, timeout)), transport=TRANSPORT, follow_redirects=False)


def _why(kind: str) -> str:
    return {
        "no_key": L("要先填密钥", "A key is needed first"),
        "no_model": L("这一家列不了模型：写一个型号名再测", "This provider can't list its models: give a model name and test again"),
        "auth": L("密钥不对，或者这把密钥没有权限", "The key was rejected, or it lacks permission"),
        "billing": L("余额或额度不够", "Out of credit or quota"),
        "not_found": L("地址或型号不对", "Wrong address or model"),
        "rate": L("被限流了，过一会儿再试", "Rate-limited; try again in a bit"),
        "server": L("对方服务出错了", "The provider's service had an error"),
        "bad": L("请求被拒了", "The request was refused"),
        "network": L("连不上", "Couldn't connect"),
        "shape": L("回的不像这种服务", "The answer doesn't look like this kind of service"),
    }.get(kind, kind)


def error(kind: str, detail: str = "", *secrets: str) -> dict:
    """{"kind", "message"}：一句给人看的话，后面跟对方的原话（抹掉密钥、截短）。"""
    d = " ".join(scrub(str(detail or ""), *secrets).split())
    d = d if len(d) <= ERR_MAX else d[:ERR_MAX - 1] + "…"
    why = _why(kind)
    return {"kind": kind, "message": L(f"{why}（{d}）", f"{why} ({d})") if d else why}


def kind_of(status: int) -> str:
    return ("auth" if status in (401, 403) else "billing" if status == 402 else "not_found" if status == 404 else "rate" if status == 429
            else "server" if status >= 500 else "bad")


def _detail(r: httpx.Response) -> str:
    """对方报错里的那句话（JSON 的 error.message / message / detail，不然开头一段正文）。"""
    try:
        j = r.json()
    except ValueError:
        return f"HTTP {r.status_code}: {r.text[:200]}"
    err = j.get("error") if isinstance(j, dict) else None
    msg = (err.get("message") if isinstance(err, dict) else err if isinstance(err, str) else None) if err is not None else None
    if msg is None and isinstance(j, dict):
        msg = j.get("message") or j.get("detail")
    return f"HTTP {r.status_code}" + (f": {str(msg)[:200]}" if msg else "")


def root_of(url: str) -> str:
    """…/v1 → 去掉 /v1（Ollama 的 /api/tags、llama.cpp 的 /health、Claude 的 /v1/models 都在根上）。"""
    u = str(url or "").rstrip("/")
    return u[:-3] if u.endswith("/v1") else u


def ids_of(j) -> list[str]:
    """各家「列模型」的回答 → 型号名：OpenAI 的 data[].id、Ollama 的 models[].model、Fireworks 的 models[].name、ChatGPT 套餐那种
    models[].slug、阿里云百炼原生接口的 output.models[].model、直接一个列表（Together）。"""
    if isinstance(j, dict) and isinstance(j.get("output"), dict):
        j = j["output"]
    rows = j.get("data") if isinstance(j, dict) and isinstance(j.get("data"), list) else \
        j.get("models") if isinstance(j, dict) and isinstance(j.get("models"), list) else j if isinstance(j, list) else None
    if rows is None:
        raise ValueError("no model list")
    out: list[str] = []
    for m in rows:
        v = m if isinstance(m, str) else next((m.get(k) for k in ("id", "model", "name", "slug") if isinstance(m, dict) and isinstance(m.get(k), str)), None)
        v = str(v or "").strip()
        if v and len(v) <= 200 and not re.search(r"\s", v) and v not in out:
            out.append(v)
        if len(out) >= MAX_MODELS:
            break
    return out


def _headers(p: dict, key: str) -> dict:
    h = {"Accept": "application/json", **{str(k): str(v) for k, v in (p.get("headers") or {}).items()}}
    if p.get("api") == "anthropic":
        h["anthropic-version"] = ANTHROPIC_VERSION
        if key:
            h["x-api-key"] = key
    elif key:
        h["Authorization"] = f"Bearer {key}"
    return h


async def list_models(p: dict, key: str, timeout: float = LIST_TIMEOUT) -> tuple[list[str] | None, dict | None]:
    """→ (型号, None)；这家列不了 → (None, None)；出错 → (None, error)。"""
    how = p.get("list") or "openai"
    if how == "none":
        return None, None
    url = str(p.get("url") or "").rstrip("/")
    if p.get("api") == "anthropic":
        target = (url or ANTHROPIC_URL) + "/v1/models?limit=1000"
    elif p.get("list_url"):  # 文档写的列模型地址不在 <url>/models 的（Fireworks、DeepInfra、阿里云百炼）
        target = str(p["list_url"])
    elif how == "ollama":
        target = root_of(url) + "/api/tags"
    else:
        target = url + "/models"
    try:
        async with _client(timeout) as c:
            r = await c.get(target, headers=_headers(p, key))
    except httpx.HTTPError as e:
        return None, error("network", f"{type(e).__name__}: {e}", key)
    if r.status_code in NO_LIST:
        return None, None
    if r.status_code >= 400:
        return None, error(kind_of(r.status_code), _detail(r), key)
    try:
        ids = ids_of(r.json())
    except ValueError:
        return None, error("shape", f"HTTP {r.status_code}: {r.text[:120]}", key)
    pre = str(p.get("strip") or "")  # 列出来的型号名前面带的前缀（目录的 list_prefix，Google 回 models/<型号>）：去掉
    return (list(dict.fromkeys(i[len(pre):] if i.startswith(pre) and len(i) > len(pre) else i for i in ids)) if pre else ids), None


async def ping(p: dict, key: str, model: str, timeout: float = REQUEST_TIMEOUT) -> dict | None:
    """发一句最小的请求 → None = 成了；否则 error。"""
    url = str(p.get("url") or "").rstrip("/")
    api = p.get("api") or "openai"
    if api == "anthropic":
        target, body = (url or ANTHROPIC_URL) + "/v1/messages", {"model": model, "max_tokens": PING_TOKENS,
                                                                    "messages": [{"role": "user", "content": PING}]}
    elif api == "openai-responses":
        target, body = url + "/responses", {"model": model, "input": PING, "max_output_tokens": PING_TOKENS}
    else:
        target, body = url + "/chat/completions", {"model": model, "messages": [{"role": "user", "content": PING}],
                                                   str(p.get("max_tokens_field") or "max_tokens"): PING_TOKENS, "stream": False}
    try:
        async with _client(timeout) as c:
            r = await c.post(target, json=body, headers={**_headers(p, key), "Content-Type": "application/json"})
    except httpx.HTTPError as e:
        return error("network", f"{type(e).__name__}: {e}", key)
    if r.status_code >= 400:
        return error(kind_of(r.status_code), _detail(r), key)
    try:
        j = r.json()
    except ValueError:
        return error("shape", f"HTTP {r.status_code}: {r.text[:120]}", key)
    if not isinstance(j, dict) or not any(k in j for k in ("choices", "content", "output", "id")):
        return error("shape", f"HTTP {r.status_code}", key)
    return None


async def test_model(p: dict, key: str = "", model: str | None = None) -> dict:
    """一家模型服务能不能用（见文件开头）。p 要有 api、url，可以有 list（openai / anthropic / ollama / none）、needs_key、headers、
    max_tokens_field、strip（列出来的型号名要去掉的前缀）。回的 listed = models 是这一家列出来的全部（不是：列不了，models 只是测通的那一个）。"""
    t0 = time.monotonic()
    key = str(key or "").strip()

    def done(ok: bool, via: str | None, models: list[str] | None = None, err: dict | None = None, full: bool = False) -> dict:
        ms = int((time.monotonic() - t0) * 1000)
        return {"ok": ok, "via": via, "models": models or [], "count": len(models or []), "listed": full, "ms": ms, "error": err}
    if p.get("needs_key", True) and not key:
        return done(False, None, err=error("no_key"))
    listed, err = await list_models(p, key)
    if err is not None:
        return done(False, "list", err=err)
    if listed is not None and not model:
        return done(True, "list", listed, full=True)
    want = model or p.get("test_model")
    if not want:
        return done(False, None, listed, error("no_model"), full=listed is not None)
    err = await ping(p, key, str(want))
    return done(err is None, "request", listed if listed is not None else [str(want)] if err is None else [], err, full=listed is not None)


# —— 本机跑的 ——————————————————————————————————————————————————————————————————

LOCAL_NAMES = ("127.0.0.1", "localhost", "::1", "0.0.0.0", "::", "")


def own_port(url: str) -> bool:
    """这个地址是不是这台服务器自己（OpenMousse 默认也听 8080，和 llama.cpp、mlx-lm 撞）：我们的端口，主机是我们听的那个地址
    （听的是本机 / 所有地址时，本机的名字都算）。"""
    try:
        u = urlsplit(url)
        host, port = (u.hostname or "").lower(), u.port or (443 if u.scheme == "https" else 80)
    except ValueError:
        return False
    ours = str(settings.host or "").lower()
    return port == settings.port and (host == ours or (ours in LOCAL_NAMES and host in LOCAL_NAMES))


async def detect_local(entry: dict, url: str, timeout: float = LOCAL_TIMEOUT) -> dict:
    """{"detected", "url", "version", "models"}。entry["detect"] = {"path": 根上的路径（root: true）或接口下的路径, "expect": 回答里
    要有的键}；认不出（连不上、超时、回的不像它）→ detected false。"""
    out: dict = {"detected": False, "url": url, "version": None, "models": []}
    if not url or own_port(url):
        return out
    d = entry.get("detect") or {}
    base = root_of(url) if d.get("root") else url.rstrip("/")
    try:
        async with _client(timeout) as c:
            r = await c.get(base + str(d.get("path") or "/models"), headers={"Accept": "application/json"})
            j = r.json() if r.status_code == 200 else None
            want = d.get("expect") or "data"
            if not isinstance(j, dict) or want not in j:
                return out
            if want == "status" and str(j.get("status")).lower() not in ("ok", "healthy"):
                return out
            if d.get("unless"):  # 这个地址也通 = 是别家（mlx-lm 和 Docker 版的 llama.cpp 都在 8080、/health 都回 ok：/props 只有 llama.cpp 有）
                u = await c.get(root_of(url) + str(d["unless"]), headers={"Accept": "application/json"})
                if u.status_code == 200:
                    return out
            out["detected"] = True
            if isinstance(j.get("version"), str):
                out["version"] = j["version"][:40]
            if want == "data":
                out["models"] = ids_of(j)
            else:
                lr = await c.get((root_of(url) + "/api/tags") if entry.get("list") == "ollama" else url.rstrip("/") + "/models",
                                 headers={"Accept": "application/json"})
                if lr.status_code == 200:
                    out["models"] = ids_of(lr.json())
    except (httpx.HTTPError, ValueError):
        return out
    return out


async def detect_all(targets: list[tuple[str, dict, str]], timeout: float = LOCAL_TIMEOUT) -> dict[str, dict]:
    """[(id, 目录里的那一项, 地址)] → {id: detect_local 的结果}，一起探（最慢的那一个说了算）。"""
    res = await asyncio.gather(*(detect_local(e, u, timeout) for _, e, u in targets))
    return {pid: r for (pid, _, _), r in zip(targets, res)}


# —— 搜索、向量 ——————————————————————————————————————————————————————————————————

async def test_search(name: str, key: str | None = None, url: str | None = None) -> dict:
    """搜一句「OpenMousse」→ {"ok", "results", "ms", "error"}。key / url 不给 = 用存下的。"""
    from . import web
    from .loop import ToolFailure
    t0 = time.monotonic()
    try:
        hits = await web.search(name, "OpenMousse", 3, key=key, url=url)
    except ToolFailure as e:
        return {"ok": False, "results": 0, "ms": int((time.monotonic() - t0) * 1000), "error": error(_search_kind(str(e)), str(e), key or "")}
    return {"ok": True, "results": len(hits), "ms": int((time.monotonic() - t0) * 1000), "error": None}


def _search_kind(msg: str) -> str:
    m = re.search(r"HTTP (\d{3})", msg)
    return kind_of(int(m.group(1))) if m else "network"


async def test_embeddings(conf: dict) -> dict:
    """按 conf（engine.recall.embeddings 的写法）算一个向量 → {"ok", "dims", "ms", "error"}。密钥用存下的（key_env）。"""
    from . import embeddings as E
    t0 = time.monotonic()
    spec = E.spec_from(conf)
    if spec is None:
        return {"ok": False, "dims": None, "ms": 0, "error": error("bad", E.problem_of(conf) or "")}
    try:
        vecs = await asyncio.to_thread(E.embed, spec, ["hello"], query=True, kind="test")
    except E.EmbedError as e:
        kind = {"auth": "auth", "rate": "rate", "server": "server", "net": "network", "shape": "shape"}.get(e.kind, "bad")
        if kind == "auth" and spec.key_env and not spec.key():
            kind = "no_key"
        return {"ok": False, "dims": None, "ms": int((time.monotonic() - t0) * 1000), "error": error(kind, str(e), *spec.secrets())}
    return {"ok": True, "dims": len(vecs[0]) if vecs else None, "ms": int((time.monotonic() - t0) * 1000), "error": None}
