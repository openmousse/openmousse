"""服务器替你打开一个网页链接（2026-09-30，学习台：大纲给的是链接、Canvas 的地址）。

只去公网：http(s)、解析出来的每个地址都是公网地址（不碰本机、内网、Tailscale、云的元数据地址），每一跳重定向都重新查；
最多 20 MB、30 秒。网页只留文字；PDF / Word / PPT 下载下来抽文字。不带任何令牌、cookie。
"""
from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
import tempfile
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx

from i18n import L

MAX_BYTES = 20 * 1024 * 1024
UA = "Mozilla/5.0 (compatible; OpenMousse study desk)"
DOC_TYPES = {"application/pdf": ".pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
             "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx"}


class FetchError(RuntimeError):
    pass


async def public(host: str, port: int) -> None:
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError) as e:
        raise FetchError(L(f"找不到 {host}", f"Can't resolve {host}")) from e
    for *_, sa in infos:
        ip = ipaddress.ip_address(str(sa[0]).split("%")[0])
        if ip.version == 6 and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if not ip.is_global:
            raise FetchError(L("只能打开公网上的地址", "Only public internet addresses can be opened"))


def check_url(url: str) -> tuple[str, str, int]:
    u = urlsplit((url or "").strip())
    scheme = u.scheme.lower()
    if scheme not in ("http", "https") or not u.hostname or u.username or u.password or len(url) > 2000:
        raise FetchError(L("链接要写成 https://…", "The link must look like https://…"))
    try:
        port = u.port or (443 if scheme == "https" else 80)
    except ValueError as e:
        raise FetchError(L("链接的端口不对", "Bad port in the link")) from e
    if port not in (80, 443):
        raise FetchError(L("只能打开 80 / 443 端口的网页", "Only ports 80 and 443 are allowed"))
    return scheme, u.hostname, port


async def fetch(url: str, headers: dict | None = None, max_bytes: int = MAX_BYTES) -> tuple[bytes, str, str]:
    """→ (内容, content-type, 最后的地址)。重定向自己跟，每一跳都查是不是公网。"""
    cur = url.strip()
    async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=10), follow_redirects=False, headers={"User-Agent": UA, **(headers or {})}) as client:
        for _ in range(6):
            _, host, port = check_url(cur)
            await public(host, port)
            async with client.stream("GET", cur) as r:
                if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                    cur = urljoin(cur, r.headers["location"])
                    continue
                if r.status_code >= 400:
                    raise FetchError(L(f"网页回了 HTTP {r.status_code}", f"The site answered HTTP {r.status_code}"))
                buf = bytearray()
                async for chunk in r.aiter_bytes():
                    buf += chunk
                    if len(buf) > max_bytes:
                        raise FetchError(L("文件太大了（超过 20 MB）", "The file is too big (over 20 MB)"))
                return bytes(buf), (r.headers.get("content-type") or "").split(";")[0].strip().lower(), cur
    raise FetchError(L("重定向太多次", "Too many redirects"))


async def page_text(url: str) -> tuple[str, str]:
    """→ (文字, 标题)。网页去掉标签；PDF / Word / PPT 抽文字。"""
    data, ctype, final = await fetch(url)
    name = Path(urlsplit(final).path).name or "page"
    ext = DOC_TYPES.get(ctype) or (Path(name).suffix.lower() if Path(name).suffix.lower() in (".pdf", ".docx", ".pptx") else "")
    if ext:
        import files as files_mod
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / f"doc{ext}"
            p.write_bytes(data)
            text, _ = await asyncio.to_thread(files_mod.extract_text, p, "doc", ctype)
        return text, name
    raw = data.decode("utf-8", "replace")
    m = re.search(r"<title[^>]*>(.*?)</title>", raw, re.I | re.S)
    title = re.sub(r"\s+", " ", m.group(1)).strip()[:120] if m else name
    if "html" in ctype or raw.lstrip()[:15].lower().startswith(("<!doctype", "<html")):
        from preview import _html_text
        return _html_text(raw), title
    return raw, title
