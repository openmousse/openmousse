"""网页（内核 ⑫，2026-10-16）：web_search（搜索）和 web_fetch（打开一个公网网页或文档，转成好读的文字，长了分段）。
主人回合的普通 Agent 都有（主对话、用户建的 Agent）；代办这种只在沙箱里的 Agent 不给（它上网只经 Doorman 的代理）。

web_search：搜索后端（照 OpenClaw / Hermes 接的那几家）
  brave        GET  api.search.brave.com/res/v1/web/search（X-Subscription-Token），密钥 BRAVE_API_KEY
  tavily       POST api.tavily.com/search（Bearer），TAVILY_API_KEY
  exa          POST api.exa.ai/search（x-api-key），EXA_API_KEY
  perplexity   POST api.perplexity.ai/search（Perplexity 的搜索接口，Bearer），PERPLEXITY_API_KEY
  searxng      GET  <你自己搭的>/search?format=json：写 url（或环境变量 SEARXNG_URL），不要密钥
  duckduckgo   GET  html.duckduckgo.com/html（不要密钥，抽网页里的结果）：只在 provider 写明 duckduckgo 时用
  第 3 批（2026-10-19，照 OpenClaw extensions/google|xai|moonshot|firecrawl 的请求和 autoDetectOrder）：
  gemini       POST generativelanguage.googleapis.com/v1beta/models/<型号>:generateContent，tools [{google_search: {}}]（Google 搜索接地），
               GEMINI_API_KEY；回一段带出处的回答（groundingChunks 的 web.uri / title 当结果）。型号默认 gemini-3.6-flash（OpenClaw 的默认）
  grok         POST api.x.ai/v1/responses（{model, input, tools: [{type: web_search}], store: false}），XAI_API_KEY；回答 + url_citation 当结果，
               型号默认 grok-4.7（OpenClaw 的默认）
  kimi         POST api.moonshot.ai/v1/tools/search（{text_query, limit}），MOONSHOT_API_KEY（或 KIMI_API_KEY）；中国大陆的密钥写
               providers.kimi.url = https://api.moonshot.cn/v1/tools/search
  firecrawl    POST api.firecrawl.dev/v2/search（{query, limit}），FIRECRAWL_API_KEY
  这四家每搜一次记一行 engine_media_usage（purpose search，价钱没核对：记 null，只记次数）。它们的密钥多半本来就为模型存着
  （GEMINI_API_KEY、XAI_API_KEY、MOONSHOT_API_KEY），所以默认不自己选上：provider 写明了才用；engine.web.search.more = true 才按顺序参与挑选。
  选哪家：engine.web.search.provider 写了就用它（不行就是没有，不换别家）；没写按 brave → gemini → grok → kimi → perplexity → firecrawl → exa →
  tavily → searxng 的顺序（第 3 批加了四家，顺序照 OpenClaw），
  有密钥（searxng 有地址）的那一家。密钥照 models.env_value：key_env 指的环境变量，先看进程环境、再看 server.json 的 env_file。
  一家都没配：不出这个工具（会话开头说一句「搜索没配，web_fetch 照样能打开网址」）。不自动退到不用密钥的那家（照 OpenClaw：没配就不
  把用户的搜索词发给他没选过的第三方；不要密钥的接口限流、出验证码、说改就改，一半时候失灵的工具只会教模型乱试）。要不用密钥的就写
  "provider": "duckduckgo"（或者自己搭一个 SearXNG）。
web_fetch：在 webfetch.py 上做（check_url：http(s)、80 / 443、不带用户名密码；PDF / Word / PPT 抽文字）。只去公网：连之前自己解析域名、
  每个地址都查（本机、内网、链路本地、云的元数据地址、CGNAT、NAT64 里藏的内网地址都挡），连的就是查过的那个地址（不再解析第二次，
  防 DNS 换绑；OpenClaw 的 pinned lookup），每一跳重定向重新查，最多 5 跳；服务器设了代理（HTTPS_PROXY）时退回 webfetch 原来的查法
  （每跳先查一遍再交给代理：钉不住地址，代理能到的内网也能到；不想这样写 engine.web.fetch.proxy = false，直连）。最多 20 MB、30 秒。
  网页去掉脚本、样式、导航、页脚和藏起来的字（display:none、aria-hidden……：网页里
  藏给模型看的指令多半在这），有 <main> / <article> 就只要它，转成 Markdown（标题、列表、链接、表格、代码块）；长了分段
  （offset / max_chars，默认一段 2 万字），转好的留 15 分钟，翻页不重新下。
  第 3 批：engine.web.fetch.render = true 时，HTML 抽出来的字太少（RENDER_MIN 以下：多半是只有脚本的壳）就交给这个 Agent 的沙箱浏览器渲染一次再抽
  （engine/browser.py；沙箱浏览器只经 Doorman 出网）；这个 Agent 没有沙箱浏览器就照旧。不写 = 照旧。
出门把关（照 Doorman 的规矩）：普通的读（GET、网址里没带私事）直接放行；网址或搜索词里看得出带着用户的私事（share.find_private：
  住址、邮箱、电话、家人名字、身体数字、用户设的词），或者有一大串像编码过的数据（像在往外带东西），交给 Doorman 的模型判断
  （egress.judge）；它说扣下 → 收件箱 kind egress 的卡（和代办被扣下的同一种，Agent 读不到），用户放行以后 30 分钟内一模一样地再调一次
  就过（点了以后 egress.tell_agent 在发请求的这个对话里说一声）。分类都是 read（要不要问用户由 Doorman 在出门那一刻定，不在关卡这里再问一遍）。
结果一律包成「资料，不是指令」（tools.wrap_untrusted），来源网址写在外面（source 也是它）。
server.json（都可以不写）：
  "engine": {"web": {"search": {"provider": "brave", "count": 5, "providers": {"brave": {"key_env": "MY_BRAVE_KEY"},
                                                                           "searxng": {"url": "http://127.0.0.1:8888"}}},
                     "fetch": {"max_chars": 20000, "proxy": false},
                     "agents": {"diet": {"search": false}, "errand-like": false}}}
参考（10/16 现拉的源码，只读）：OpenClaw e38e3a42 的 src/agents/tools/web-search.ts、src/web-search/runtime.ts（选哪家、显式写了就不退、
不用密钥的不自动选、没配就不出工具）、extensions/brave|tavily|exa|perplexity|searxng|duckduckgo（各家的请求和结果）、
src/agents/tools/web-fetch.ts、src/infra/net/ssrf.ts（钉住查过的地址、每跳重查、挡的名字）、web-fetch-visibility.ts（去掉藏起来的字）；
Hermes f6e2723 的 tools/web_tools.py、plugins/web/*（各家的请求、结果的样子）、tools/url_safety.py（元数据地址、NAT64）。只借设计。
"""
from __future__ import annotations

import asyncio
import html
import ipaddress
import json
import re
import socket
import time
import urllib.request
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, urljoin, urlsplit

import httpx

import webfetch
from config import raw
from i18n import L

from .loop import Tool, ToolCtx, ToolFailure

READ = "read"
UA = "Mozilla/5.0 (compatible; OpenMousse web_fetch)"
MAX_HOPS = 5
FETCH_CHARS, FETCH_MAX_CHARS = 20_000, 50_000
CACHE_TTL, CACHE_MAX = 900.0, 32
COUNT, COUNT_MAX = 5, 10
SEARCH_TIMEOUT = 20.0
BAD_NAMES = {"localhost", "localhost.localdomain", "metadata.google.internal", "metadata.goog", "metadata", "instance-data"}
NAT64 = ipaddress.ip_network("64:ff9b::/96")
PROVIDERS: dict[str, dict] = {
    "brave": {"url": "https://api.search.brave.com/res/v1/web/search", "key_env": "BRAVE_API_KEY", "order": 10},
    "perplexity": {"url": "https://api.perplexity.ai/search", "key_env": "PERPLEXITY_API_KEY", "order": 50},
    "exa": {"url": "https://api.exa.ai/search", "key_env": "EXA_API_KEY", "order": 65},
    "tavily": {"url": "https://api.tavily.com/search", "key_env": "TAVILY_API_KEY", "order": 70},
    "searxng": {"url_env": "SEARXNG_URL", "order": 200, "keyless": True},
    # 第 3 批：照 OpenClaw 的 autoDetectOrder（gemini 20、grok 30、kimi 40、firecrawl 60）
    "gemini": {"url": "https://generativelanguage.googleapis.com/v1beta", "key_env": "GEMINI_API_KEY", "order": 20, "model": "gemini-3.6-flash",
               "opt_in": True},
    "grok": {"url": "https://api.x.ai/v1/responses", "key_env": "XAI_API_KEY", "order": 30, "model": "grok-4.7", "opt_in": True},
    "kimi": {"url": "https://api.moonshot.ai/v1/tools/search", "key_env": "MOONSHOT_API_KEY", "alt_key_env": "KIMI_API_KEY", "order": 40,
             "opt_in": True},
    "firecrawl": {"url": "https://api.firecrawl.dev/v2/search", "key_env": "FIRECRAWL_API_KEY", "order": 60, "opt_in": True},
    "duckduckgo": {"url": "https://html.duckduckgo.com/html/", "keyless": True, "explicit": True},
}


# —— 设置 ——————————————————————————————————————————————————————————————————

def cfg() -> dict:
    e = raw().get("engine")
    w = e.get("web") if isinstance(e, dict) else None
    return w if isinstance(w, dict) else {}


def _sub(key: str) -> dict:
    v = cfg().get(key)
    return v if isinstance(v, dict) else {}


def allowed(agent: str, what: str) -> bool:
    """engine.web.agents.<id>：false = 都不给；{"search": false} = 不给这一样。"""
    a = cfg().get("agents")
    if not isinstance(a, dict):
        return True
    v = a[agent] if agent in a else a.get("*")
    if v is False:
        return False
    return not (isinstance(v, dict) and v.get(what) is False)


def settings_of(name: str) -> dict:
    p = _sub("search").get("providers")
    mine = p.get(name) if isinstance(p, dict) and isinstance(p.get(name), dict) else {}
    return {**PROVIDERS.get(name, {}), **mine}


def key_of(name: str) -> str:
    from . import models
    s = settings_of(name)
    return (str(s.get("key") or "") or (models.env_value(str(s["key_env"])) if s.get("key_env") else "")
            or (models.env_value(str(s["alt_key_env"])) if s.get("alt_key_env") else ""))  # 第 3 批：Kimi 两个变量名都认


def url_of(name: str) -> str:
    from . import models
    s = settings_of(name)
    return str(s.get("url") or "") or (models.env_value(str(s["url_env"])) if s.get("url_env") else "")


def usable(name: str) -> bool:
    s = settings_of(name)
    if name not in PROVIDERS:
        return False
    return bool(url_of(name)) if s.get("keyless") else bool(key_of(name) and url_of(name))


def provider() -> str | None:
    """用哪家搜：写了 provider 就是它（不行就没有）；没写按顺序找第一家有密钥的（不用密钥的不自动选）。"""
    want = _sub("search").get("provider")
    if want is False or str(want or "").strip().lower() in ("off", "none"):
        return None
    if want:
        w = str(want).strip().lower()
        return w if usable(w) else None
    more = _sub("search").get("more") is True  # 第 3 批：新加的四家默认不自己选上（见文件开头）
    for name in sorted((n for n, s in PROVIDERS.items() if not s.get("explicit") and (more or not s.get("opt_in"))),
                       key=lambda n: PROVIDERS[n]["order"]):
        if not settings_of(name).get("keyless") and usable(name):
            return name
    return "searxng" if usable("searxng") else None


# —— 搜 ——————————————————————————————————————————————————————————————————————

@dataclass
class Hit:
    title: str
    url: str
    snippet: str = ""
    date: str = ""


class Hits(list):
    """search 回的结果（还是一个 list）；answer = 回答式的搜索（gemini、grok）那一段回答。"""
    answer: str = ""


PAID = ("gemini", "grok", "kimi", "firecrawl")  # 第 3 批加的几家：每搜一次记一行用量
RENDER_MIN = 300                                # web_fetch 抽出来的字少于这么多（HTML）：可能是只有脚本的壳

def _clean(s: object, n: int = 400) -> str:
    t = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", str(s or ""))).split())
    return t if len(t) <= n else t[: n - 1] + "…"


def _hits(items: list, title: str, url: str, snippet: str, date: str = "") -> list[Hit]:
    out = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        u = str(it.get(url) or "")
        if not u.startswith(("http://", "https://")) or len(u) > 2048:
            continue
        sn = it.get(snippet)
        if isinstance(sn, list):
            sn = " ".join(str(x) for x in sn)
        out.append(Hit(_clean(it.get(title), 200) or u, u, _clean(sn), str(it.get(date) or "")[:10]))
    return out


def ddg_parse(page: str) -> list[Hit]:
    """DuckDuckGo 的 HTML 结果页：class 有 result__a 的链接是标题（经它的跳转，uddg 里是真网址），result__snippet 是摘要（按先后配对）。"""
    links = []
    for m in re.finditer(r"<a\b([^>]*)>(.*?)</a>", page, re.S):
        attrs = m.group(1)
        href = re.search(r'href="([^"]+)"', attrs)
        if href and re.search(r'class="[^"]*\bresult__a\b', attrs):
            links.append((href.group(1), m.group(2)))
    snips = [m.group(2) for m in re.finditer(r'<(a|div|td)\b[^>]*class="[^"]*\bresult__snippet\b[^"]*"[^>]*>(.*?)</\1>', page, re.S)]
    out = []
    for i, (href, title) in enumerate(links):
        href = html.unescape(href)
        if href.startswith("//"):
            href = "https:" + href
        q = parse_qs(urlsplit(href).query)
        real = (q.get("uddg") or [href])[0]
        if not real.startswith(("http://", "https://")):
            continue
        out.append(Hit(_clean(title, 200) or real, real, _clean(snips[i]) if i < len(snips) else ""))
    return out


async def search(name: str, query: str, count: int, *, key: str | None = None, url: str | None = None) -> list[Hit]:
    """调一家搜索后端 → 结果（最多 count 条）。出错抛 ToolFailure（只说哪家、什么状态，不带密钥）。
    key / url：第 9 步的设置页「测一下」还没存的那一把（不给 = 用配好的）。"""
    url, key = url or url_of(name), key or key_of(name)
    n = max(1, min(int(count or COUNT), COUNT_MAX))
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(SEARCH_TIMEOUT, connect=10), headers={"User-Agent": UA}) as c:
            if name == "brave":
                r = await c.get(url, params={"q": query, "count": n}, headers={"Accept": "application/json", "X-Subscription-Token": key})
                r.raise_for_status()
                hits = _hits(((r.json().get("web") or {}).get("results")), "title", "url", "description", "page_age")
            elif name == "tavily":
                r = await c.post(url, json={"query": query, "max_results": n, "search_depth": "basic", "include_answer": False,
                                            "include_raw_content": False, "include_images": False}, headers={"Authorization": f"Bearer {key}"})
                r.raise_for_status()
                hits = _hits(r.json().get("results"), "title", "url", "content", "published_date")
            elif name == "exa":
                r = await c.post(url, json={"query": query, "numResults": n, "contents": {"highlights": True}}, headers={"x-api-key": key})
                r.raise_for_status()
                rows = r.json().get("results") or []
                for x in rows:
                    if isinstance(x, dict) and not x.get("highlights"):
                        x["highlights"] = x.get("summary") or (str(x.get("text") or "")[:400])
                hits = _hits(rows, "title", "url", "highlights", "publishedDate")
            elif name == "perplexity":
                r = await c.post(url, json={"query": query, "max_results": n}, headers={"Authorization": f"Bearer {key}"})
                r.raise_for_status()
                hits = _hits(r.json().get("results"), "title", "url", "snippet", "date")
            elif name == "searxng":
                r = await c.get(url.rstrip("/") + "/search", params={"q": query, "format": "json"}, headers={"Accept": "application/json"})
                r.raise_for_status()
                hits = _hits(r.json().get("results"), "title", "url", "content", "publishedDate")
            elif name == "gemini":  # 第 3 批：Google 搜索接地
                model = str(settings_of(name).get("model") or "gemini-3.6-flash")
                r = await c.post(f"{url.rstrip('/')}/models/{model}:generateContent", headers={"x-goog-api-key": key},
                                 json={"contents": [{"parts": [{"text": query}]}], "tools": [{"google_search": {}}]})
                r.raise_for_status()
                cand = ((r.json().get("candidates") or [{}])[0]) or {}
                answer = "".join(str(p.get("text") or "") for p in ((cand.get("content") or {}).get("parts") or []) if isinstance(p, dict))
                chunks = (cand.get("groundingMetadata") or {}).get("groundingChunks") or []
                hits = Hits(_hits([c_.get("web") for c_ in chunks if isinstance(c_, dict)], "title", "uri", "snippet"))
                hits.answer = _clean(answer, 4000)
            elif name == "grok":  # 第 3 批：xAI 的 Responses API + web_search
                model = str(settings_of(name).get("model") or "grok-4.7")
                r = await c.post(url, headers={"Authorization": f"Bearer {key}"},
                                 json={"model": model, "input": [{"role": "user", "content": query}], "tools": [{"type": "web_search"}], "store": False})
                r.raise_for_status()
                texts, cites = [], []
                for out in r.json().get("output") or []:
                    for blk in (out.get("content") or []) if isinstance(out, dict) else []:
                        if isinstance(blk, dict) and blk.get("type") == "output_text":
                            texts.append(str(blk.get("text") or ""))
                            cites += [a for a in blk.get("annotations") or [] if isinstance(a, dict) and a.get("type") == "url_citation"]
                hits = Hits(_hits(cites, "title", "url", "snippet"))
                hits.answer = _clean("".join(texts), 4000)
            elif name == "kimi":  # 第 3 批：Moonshot 的搜索接口
                r = await c.post(url, headers={"Authorization": f"Bearer {key}"}, json={"text_query": query, "limit": n})
                r.raise_for_status()
                hits = _hits(r.json().get("search_results"), "title", "url", "snippet", "date")
            elif name == "firecrawl":  # 第 3 批
                r = await c.post(url, headers={"Authorization": f"Bearer {key}"}, json={"query": query, "limit": n})
                r.raise_for_status()
                data = r.json().get("data")
                rows = data if isinstance(data, list) else (data.get("web") or []) if isinstance(data, dict) else []
                hits = _hits(rows, "title", "url", "description")
            elif name == "duckduckgo":
                r = await c.get(url, params={"q": query}, headers={"Accept": "text/html"})
                r.raise_for_status()
                hits = ddg_parse(r.text)
                if not hits and re.search(r"anomaly|challenge|captcha", r.text, re.I):
                    raise ToolFailure(L("DuckDuckGo 要求人机验证，这会儿搜不了", "DuckDuckGo asked for a bot check, so search isn't available right now"))
            else:
                raise ToolFailure(L(f"不认识的搜索后端 {name}", f"Unknown search provider {name}"))
    except httpx.HTTPStatusError as e:
        raise ToolFailure(L(f"{name} 回了 HTTP {e.response.status_code}", f"{name} answered HTTP {e.response.status_code}")) from None
    except (httpx.HTTPError, ValueError) as e:
        raise ToolFailure(L(f"{name} 没搜成：{type(e).__name__}", f"{name} search failed: {type(e).__name__}")) from None
    seen, out = set(), Hits()
    for h in hits:
        if h.url not in seen:
            seen.add(h.url)
            out.append(h)
    out.answer = getattr(hits, "answer", "")
    if name in PAID:  # 第 3 批：这几家按次花钱，记一行（价钱没核对：null）
        from . import media
        media.record("search", media.Choice("search", name, str(settings_of(name).get("model") or name)), units=1, unit="query", unpriced=True)
    res = Hits(out[:n])
    res.answer = out.answer
    return res


# —— 只去公网：查过的地址就是连的地址 ————————————————————————————————————————————————

class Blocked(webfetch.FetchError):
    """不许去的地址（本机、内网、元数据……）。"""


def blocked(ip_text: str) -> str | None:
    try:
        ip = ipaddress.ip_address(ip_text.split("%")[0])
    except ValueError:
        return "not an IP address"
    if ip.version == 6:
        if ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        elif ip in NAT64:
            ip = ipaddress.ip_address(int(ip) & 0xFFFFFFFF)
        elif ip.sixtofour or ip.teredo:
            return "tunnelled address"
    if not ip.is_global or ip.is_multicast:
        return "private, local or reserved address"
    return None


async def _resolve(host: str, port: int) -> list[str]:
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError):
        raise webfetch.FetchError(L(f"找不到 {host}", f"Can't resolve {host}")) from None
    return [str(sa[0]) for *_, sa in infos]


def _dial(ip: str, port: int) -> tuple[str, int]:
    return ip, port


resolve_host = _resolve   # 测试换掉：假的域名解析
dial = _dial              # 测试换掉：查过的地址 → 本机的假网站


def bad_name(host: str) -> bool:
    h = host.strip("[]").lower().rstrip(".")
    return h in BAD_NAMES or h.endswith((".localhost", ".local", ".internal"))


async def vet(host: str, port: int) -> str:
    """连之前：名字不是本机这类、解析出来的每个地址都是公网的 → 回第一个（就连它）。不行抛 Blocked。"""
    h = host.strip("[]").lower().rstrip(".")
    if bad_name(h):
        raise Blocked(L(f"{h} 是本机或内网的名字，不打开", f"{h} is a local or internal name, so it isn't opened"))
    ips = await resolve_host(h, port)
    if not ips:
        raise webfetch.FetchError(L(f"找不到 {h}", f"Can't resolve {h}"))
    for ip in ips:
        if why := blocked(ip):
            raise Blocked(L(f"只能打开公网上的地址（{h} 解析到 {ip}：{why}）", f"Only public internet addresses can be opened ({h} resolves to {ip}: {why})"))
    return ips[0]


class _Vetted:
    """httpcore 的网络后端：连之前自己解析、查，连的就是查过的那个地址；TLS 证书照样按域名验（httpcore 另传 SNI）。"""

    def __init__(self) -> None:
        import httpcore
        self._inner = httpcore.AnyIOBackend()

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        h, p = dial(await vet(host, port), port)
        return await self._inner.connect_tcp(h, p, timeout=timeout, local_address=local_address, socket_options=socket_options)

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):
        raise Blocked("unix sockets aren't allowed")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


def _client() -> httpx.AsyncClient:
    import httpcore
    t = httpx.AsyncHTTPTransport(trust_env=False)
    t._pool = httpcore.AsyncConnectionPool(ssl_context=httpx.create_ssl_context(), network_backend=_Vetted(), max_connections=8)  # noqa: SLF001
    return httpx.AsyncClient(transport=t, timeout=httpx.Timeout(30, connect=10), follow_redirects=False, trust_env=False,
                             headers={"User-Agent": UA, "Accept": "text/html, text/markdown;q=0.9, text/plain;q=0.8, application/pdf;q=0.7, */*;q=0.5"})


def proxied() -> bool:
    """服务器设了代理（HTTPS_PROXY 这些）→ fetch 经它走（钉不住地址：每一跳先查名字和解析出来的地址，连的时候由代理再解析一次；代理自己
    能到的内网也就能到）。engine.web.fetch.proxy = false：不管代理，直连、钉住查过的地址（服务器不经代理也出得去时选这个）。"""
    if _sub("fetch").get("proxy") is False:
        return False
    p = urllib.request.getproxies()
    return bool(p.get("https") or p.get("http") or p.get("all"))


async def fetch(url: str, max_bytes: int = webfetch.MAX_BYTES) -> tuple[bytes, str, str]:
    """→ (内容, content-type, 最后的地址)。重定向自己跟（最多 MAX_HOPS 跳），每一跳都查。"""
    if proxied():  # 服务器设了代理：照 webfetch 原来的（每跳先查一遍；DNS 由代理解析）
        return await webfetch.fetch(url, max_bytes=max_bytes)
    cur = url.strip()
    async with _client() as client:
        for _ in range(MAX_HOPS + 1):
            _, host, _ = webfetch.check_url(cur)
            if bad_name(host):
                raise Blocked(L(f"{host} 是本机或内网的名字，不打开", f"{host} is a local or internal name, so it isn't opened"))
            try:
                async with client.stream("GET", cur) as r:
                    if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                        cur = urljoin(cur, r.headers["location"])
                        continue
                    if r.status_code >= 400:
                        raise webfetch.FetchError(L(f"网页回了 HTTP {r.status_code}", f"The site answered HTTP {r.status_code}"))
                    buf = bytearray()
                    async for chunk in r.aiter_bytes():
                        buf += chunk
                        if len(buf) > max_bytes:
                            raise webfetch.FetchError(L(f"太大了（超过 {max_bytes // 1024 // 1024} MB）", f"Too big (over {max_bytes // 1024 // 1024} MB)"))
                    return bytes(buf), (r.headers.get("content-type") or "").split(";")[0].strip().lower(), cur
            except httpx.TimeoutException:
                raise webfetch.FetchError(L("网页太久没回", "The site took too long to answer")) from None
            except httpx.HTTPError as e:
                raise webfetch.FetchError(L(f"连不上：{type(e).__name__}", f"Couldn't connect: {type(e).__name__}")) from None
    raise webfetch.FetchError(L("重定向太多次", "Too many redirects"))


# —— 网页 → 好读的 Markdown ———————————————————————————————————————————————————————

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
DROP = {"script", "style", "noscript", "template", "svg", "canvas", "iframe", "object", "embed", "head", "nav", "aside", "footer", "form",
        "button", "select", "textarea", "dialog", "math", "video", "audio", "picture", "map"}
BLOCK = {"p", "div", "section", "article", "main", "header", "ul", "ol", "dl", "dt", "dd", "table", "thead", "tbody", "tfoot", "figure",
         "figcaption", "address", "details", "summary", "center", "fieldset", "body", "html"}
HIDE_STYLE = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden|opacity\s*:\s*0(?:\.0*)?\s*(?:;|$)|font-size\s*:\s*0(?:px|em|rem|%)?\s*(?:;|$)", re.I)
HIDE_CLASS = re.compile(r"(?:^|\s)(?:sr-only|visually-hidden|d-none|hidden|screen-reader-text)(?:\s|$)", re.I)


@dataclass
class Node:
    tag: str
    attrs: dict
    kids: list = field(default_factory=list)
    up: Node | None = None


class _Tree(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#root", {})
        self.cur = self.root
        self.title = ""
        self._title = False

    def handle_starttag(self, tag, attrs):
        node = Node(tag, {k: v or "" for k, v in attrs}, [], self.cur)
        self.cur.kids.append(node)
        if tag == "title":
            self._title = True
        if tag not in VOID:
            self.cur = node

    def handle_startendtag(self, tag, attrs):
        self.cur.kids.append(Node(tag, {k: v or "" for k, v in attrs}, [], self.cur))

    def handle_endtag(self, tag):
        n = self.cur
        while n is not self.root and n.tag != tag:
            n = n.up
        if n is not self.root and n.up is not None:
            self.cur = n.up
        if tag == "title":
            self._title = False

    def handle_data(self, data):
        if self._title:
            self.title += data
        self.cur.kids.append(data)


def hidden(n: Node) -> bool:
    a = n.attrs
    return ("hidden" in a or a.get("aria-hidden", "").lower() == "true" or bool(HIDE_STYLE.search(a.get("style", "")))
            or bool(HIDE_CLASS.search(a.get("class", ""))))


def _first(n: Node, tags: tuple[str, ...]) -> Node | None:
    stack = [n]
    while stack:
        cur = stack.pop()
        if isinstance(cur, Node):
            if cur.tag in tags and not hidden(cur):
                return cur
            stack.extend(reversed([k for k in cur.kids if isinstance(k, Node)]))
    return None


def _plain(n: Node) -> str:
    out, stack = [], [n]
    while stack:
        cur = stack.pop()
        if isinstance(cur, str):
            out.append(cur)
        elif cur.tag not in DROP and not hidden(cur):
            stack.extend(reversed(cur.kids))
    return "".join(out)


def _md(n: Node | str, base: str, depth: int = 0, pre: bool = False) -> str:
    if isinstance(n, str):
        return n if pre else re.sub(r"\s+", " ", n)
    tag = n.tag
    if tag in DROP or hidden(n):
        return ""
    if depth > 300:
        return " " + " ".join(_plain(n).split()) + " "
    if tag == "br":
        return "\n"
    if tag == "hr":
        return "\n\n---\n\n"
    if tag == "pre":
        body = _plain(n).strip("\n")
        return f"\n\n```\n{body}\n```\n\n" if body.strip() else ""
    inner = "".join(_md(k, base, depth + 1, pre) for k in n.kids)
    if tag == "code":
        t = inner.strip()
        return f"`{t}`" if t else ""
    if re.fullmatch(r"h[1-6]", tag):
        t = " ".join(inner.split())
        return f"\n\n{'#' * int(tag[1])} {t}\n\n" if t else ""
    if tag == "a":
        t = " ".join(inner.split())
        href = urljoin(base, n.attrs.get("href", "").strip())
        return f"[{t}]({href})" if t and href.startswith(("http://", "https://")) else inner
    if tag == "img":
        alt = " ".join(n.attrs.get("alt", "").split())
        return f"[image: {alt}]" if alt else ""
    if tag == "li":
        return "\n- " + " ".join(inner.split())
    if tag == "tr":
        cells = [" ".join(_md(k, base, depth + 1).split()).replace("|", "/") for k in n.kids if isinstance(k, Node) and k.tag in ("td", "th")]
        return "\n| " + " | ".join(cells) + " |" if any(cells) else ""
    if tag == "blockquote":
        t = inner.strip()
        return "\n\n> " + re.sub(r"\n+", "\n> ", t) + "\n\n" if t else ""
    if tag in ("p", "dt", "dd", "figcaption", "summary") or tag in BLOCK:
        return f"\n\n{inner}\n\n"
    return inner


def readable(page: str, base: str) -> tuple[str, str]:
    """HTML → (标题, Markdown)。有 <main> / <article> 就只要它（没有就 <body>），去掉脚本、样式、导航、页脚和藏起来的字。"""
    t = _Tree()
    try:
        t.feed(page)
        t.close()
    except Exception:  # noqa: BLE001 — 坏 HTML：能解析多少算多少
        pass
    root = _first(t.root, ("main",)) or _first(t.root, ("article",)) or _first(t.root, ("body",)) or t.root
    text = _md(root, base)
    lines = [ln.rstrip() for ln in text.split("\n")]
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(ln if ln.strip() else "" for ln in lines)).strip()
    return " ".join(html.unescape(t.title).split())[:200], text


async def page(url: str) -> dict:
    """打开 url → {"final", "title", "text", "ctype", "at"}（留 15 分钟，翻页不再下）。"""
    now = time.monotonic()
    hit = _cache.get(url)
    if hit and now - hit["at"] < CACHE_TTL:
        return hit
    data, ctype, final = await fetch(url)
    name = Path(urlsplit(final).path).name or "page"
    ext = webfetch.DOC_TYPES.get(ctype) or (Path(name).suffix.lower() if Path(name).suffix.lower() in (".pdf", ".docx", ".pptx") else "")
    if ext:
        import tempfile

        import files as files_mod
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / f"doc{ext}"
            p.write_bytes(data)
            text, _ = await asyncio.to_thread(files_mod.extract_text, p, "doc", ctype)
        title = name
    elif "html" in ctype or (not ctype and data.lstrip()[:15].lower().startswith((b"<!doctype", b"<html"))):
        title, text = await asyncio.to_thread(readable, data.decode("utf-8", "replace"), final)
    elif b"\x00" not in data[:8192] and (ctype.startswith("text/") or ctype in ("application/json", "application/xml", "application/javascript", "")
                                         or ctype.endswith(("+json", "+xml"))):
        text, title = data.decode("utf-8", "replace"), name
        if "json" in ctype:
            try:
                text = json.dumps(json.loads(text), ensure_ascii=False, indent=1)
            except ValueError:
                pass
    else:
        raise webfetch.FetchError(L(f"不是文字的网页（{ctype}，{len(data)} 字节）", f"Not a text page ({ctype}, {len(data)} bytes)"))
    out = {"final": final, "title": title, "text": text, "ctype": ctype, "at": now}
    _cache[url] = out
    while len(_cache) > CACHE_MAX:
        _cache.pop(next(iter(_cache)))
    return out


_cache: dict[str, dict] = {}


def chunk(text: str, offset: int, size: int) -> tuple[str, int]:
    """从 offset 起最多 size 字（尽量断在换行上）→ (这一段, 下一段从哪起；= len(text) 就是完了)。"""
    start = max(0, min(int(offset or 0), len(text)))
    end = min(len(text), start + size)
    if end < len(text):
        nl = text.rfind("\n", start + int(size * 0.8), end)
        end = nl + 1 if nl > start else end
    return text[start:end], end


# —— 出门把关：带着私事的交给 Doorman 判断 —————————————————————————————————————————————

def _approved() -> bool:
    from . import handkit
    return handkit.approved_run()


async def door(ctx: ToolCtx, url: str, what: str) -> str | None:
    """网址（或搜索词）里带着私事、像在往外带数据 → 让 Doorman 的模型看；扣下了 → kind egress 的卡。→ None = 放行；字符串 = 没去的说明。"""
    import egress
    if _approved():
        return None
    u = urlsplit(url)
    scheme = (u.scheme or "https").lower()
    req = egress.CheckIn(client="", method="GET", scheme=scheme, host=(u.hostname or "").lower(), port=u.port or (443 if scheme == "https" else 80),
                         path=(u.path or "/") + (f"?{u.query}" if u.query else ""), sha=egress.fingerprint("GET", url, b""))
    private = egress.private_hits(req, include_body=False)
    if not private and not egress.looks_encoded(req.path, req.host):
        return None
    if egress.granted(req.sha):
        return None
    if w := egress.waiting(req.sha):  # 已经扣下在等：同一张卡，不再请模型看
        return _held(what, w["inbox"])
    key = f"web:{req.sha}"  # 模型说过「放行」的同一个请求：一小时内不再问（翻页时每段都过这里）
    hit = egress.cached(key)
    agent = ctx.agent or "main"
    task = egress.errand_task(ctx.conversation or agent)
    verdict, reason = hit if hit else await egress.judge(req, task, agent_name=egress.name_of(agent))
    if verdict == "allow":
        if not hit:
            egress.remember(key, verdict, reason)
        return None
    out = await egress.make_hold(req, reason or L("网址里带着你的私事", "The link carries your private details"), private, agent=agent, task=task,
                                 thread=ctx.conversation)  # 用户点了以后，那句「放行了 / 没放行」发回这个对话
    if out.get("decision") == "hold":
        return _held(what, out.get("inbox"))
    return f"[Doorman didn't let this {what} through: {out.get('reason') or 'denied'}. Don't try another way; tell the user.]"


def _held(what: str, card: object) -> str:
    import egress
    return (f"[Doorman is holding this {what} for the user's OK (inbox card {card}): it carries their private details or "
            "looks like it sends data out. Don't retry and don't try another way: tell the user in one line what's waiting. Once they "
            f"let it through, call again with exactly the same input within {egress.grant_minutes()} minutes.]")


# —— 工具 ——————————————————————————————————————————————————————————————————

def _source(s: str) -> str:
    return s.replace('"', "%22").replace("\n", " ")[:300]


def search_tool(agent: str) -> Tool | None:
    name = provider()
    if name is None or not allowed(agent, "search"):
        return None
    from .handkit import levelled

    async def run(args: dict, ctx: ToolCtx) -> str:
        from .tools import wrap_untrusted
        q = " ".join(str(args.get("query") or "").split())
        if not q:
            raise ToolFailure(L("要写 query", "Give a query"))
        if len(q) > 400:
            raise ToolFailure(L("搜索词太长（最多 400 字）", "The query is too long (400 characters at most)"))
        now = provider()
        if now is None:
            raise ToolFailure(L("搜索没配好（engine.web.search）", "Web search isn't set up (engine.web.search)"))
        if held := await door(ctx, f"{url_of(now) or 'https://' + now}?q={quote_plus(q)}", "search"):
            return held
        hits = await search(now, q, int(args.get("count") or _sub("search").get("count") or COUNT))
        answer = getattr(hits, "answer", "")
        if not hits and not answer:
            return L(f"「{q}」没有搜到结果（{now}）", f'No results for "{q}" ({now})')
        rows = [f"{i}. {h.title}\n   {h.url}" + (f"\n   {h.date}" if h.date else "") + (f"\n   {h.snippet}" if h.snippet else "")
                for i, h in enumerate(hits, 1)]
        if answer:  # 第 3 批：回答式的搜索（gemini、grok）先给回答，出处在后面
            rows.insert(0, f"Answer: {answer}\n\nSources:" if hits else f"Answer: {answer}")
        return f'Results for "{q}" ({now}). Open one with web_fetch.\n' + wrap_untrusted(_source(f"web_search {now}"), "\n".join(rows))

    return Tool(name="web_search", kinds=("owner",), run=run, timeout=60, max_output=20_000,
                title=lambda a: " ".join(str(a.get("query") or "").split())[:60],
                classify=lambda a: levelled("web_search", (READ, L("只是搜；带着用户私事的搜索词由 Doorman 在出门时判断", "Just a search; a query carrying the user's private details goes to Doorman first"))),
                description=L(
                    f"上网搜（{name}）：回标题、网址、摘要，要细看用 web_fetch 打开。搜索词里别放用户的私事（住址、电话、邮箱……）：那样的会先交 Doorman 判断，多半要他点头。",
                    f"Search the web ({name}): returns titles, URLs and snippets; open one with web_fetch to read it. Keep the user's private "
                    "details (address, phone, email…) out of queries: those go to Doorman first and usually need their OK."),
                schema={"type": "object", "required": ["query"], "properties": {
                    "query": {"type": "string"},
                    "count": {"type": "integer", "description": L(f"几条（默认 {COUNT}，最多 {COUNT_MAX}）", f"How many results (default {COUNT}, max {COUNT_MAX})")}}})


def fetch_tool(agent: str) -> Tool | None:
    if not allowed(agent, "fetch"):
        return None
    from .handkit import levelled
    size = max(1000, min(int(_sub("fetch").get("max_chars") or FETCH_CHARS), FETCH_MAX_CHARS))

    async def run(args: dict, ctx: ToolCtx) -> str:
        from .tools import wrap_untrusted
        url = str(args.get("url") or "").strip()
        if url and "://" not in url:
            url = "https://" + url
        try:
            webfetch.check_url(url)
        except webfetch.FetchError as e:
            raise ToolFailure(str(e)) from None
        if held := await door(ctx, url, "request"):
            return held
        try:
            got = await page(url)
        except webfetch.FetchError as e:
            raise ToolFailure(L(f"打不开 {url}：{e}", f"Couldn't open {url}: {e}")) from None
        if render_on() and thin(got) and (r := await rendered(ctx.agent or "main", got["final"])) is not None:  # 第 3 批：只有脚本的壳
            got = r
        text = got["text"]
        if not text.strip():
            return L(f"{got['final']} 上没有能读的文字", f"There's no readable text on {got['final']}")
        want = max(500, min(int(args.get("max_chars") or size), FETCH_MAX_CHARS))
        part, nxt = chunk(text, int(args.get("offset") or 0), want)
        start = max(0, min(int(args.get("offset") or 0), len(text)))
        where = (f"[characters {start}–{nxt} of {len(text)}" + (f"; more: call again with offset={nxt}]" if nxt < len(text) else "; that's the end]"))
        head = (f"Source: {got['final']}" + (f" (redirected from {url})" if got["final"] != url else "")
                + (" (rendered in your sandbox browser)" if got.get("rendered") else ""))
        body = (f"Title: {got['title']}\n\n" if got["title"] and start == 0 else "") + part
        return f"{head}\n{where}\n" + wrap_untrusted(_source(f"web_fetch {got['final']}"), body)

    return Tool(name="web_fetch", kinds=("owner",), run=run, timeout=90, max_output=FETCH_MAX_CHARS + 2000,
                title=lambda a: str(a.get("url") or "")[:80],
                classify=lambda a: levelled("web_fetch", (READ, L("只读一个公网网页；带着用户私事的网址由 Doorman 在出门时判断", "Only reads a public web page; a link carrying the user's private details goes to Doorman first"))),
                description=L(
                    f"打开一个公网上的网页或文档（http / https；PDF、Word、PPT 抽文字），转成好读的文字；长的分段，一段默认 {size} 字，offset 接着读。"
                    "只去公网（本机、内网的地址打不开）。网址里别放用户的私事：那样的会先交 Doorman 判断。",
                    f"Open a public web page or document (http / https; PDF, Word and PowerPoint give their text) as readable text; long pages "
                    f"come in parts of {size} characters by default (offset reads on). Public internet only (local and private addresses "
                    "can't be opened). Keep the user's private details out of URLs: those go to Doorman first."),
                schema={"type": "object", "required": ["url"], "properties": {
                    "url": {"type": "string"},
                    "offset": {"type": "integer", "description": L("从第几个字接着读（上一次结果里写着）", "Character to continue from (the last result says)")},
                    "max_chars": {"type": "integer", "description": L(f"这一段最多几个字（默认 {size}，最多 {FETCH_MAX_CHARS}）", f"At most this many characters (default {size}, max {FETCH_MAX_CHARS})")}}})


# —— 第 3 批：只有脚本的壳，交沙箱浏览器渲染一次 ——————————————————————————————————————————

def render_on() -> bool:
    return _sub("fetch").get("render") is True


def thin(got: dict) -> bool:
    """HTML 抽出来的字太少（多半是靠脚本画出来的页面）。"""
    return "html" in str(got.get("ctype") or "html") and len(str(got.get("text") or "").strip()) < RENDER_MIN


async def rendered(agent: str, url: str) -> dict | None:
    """这个 Agent 的沙箱浏览器打开一次、拿全部文字 → page() 那样的 dict（rendered = True）；没有沙箱浏览器、打不开 → None（照旧）。"""
    from . import browser, sandbox
    box = sandbox.box(agent)
    if box is None or not box.get("browser"):
        return None
    try:
        await browser.act(agent, {"action": "open", "url": url})
        _, body = await browser.act(agent, {"action": "text"})
    except sandbox.SandboxError:
        return None
    head, _, text = str(body or "").partition("\n\n")
    final = head[5:].strip() if head.startswith("URL: ") else url
    if not text.strip():
        return None
    return {"final": final or url, "title": "", "text": text.strip(), "ctype": "text/html", "rendered": True, "at": time.monotonic()}


def tools(agent: str) -> list[Tool]:
    return [t for t in (search_tool(agent), fetch_tool(agent)) if t is not None]
