"""Sentinel 出口代理：mitmproxy 插件。「代办」Agent 的沙箱（Docker 网络 mousse-errand）只能连到这里，规矩见 egress.py。

跑法（errand.py 写好的 user 服务 openmousse-sentinel）：sentinel_run.py 把这里的插件直接挂进 mitmproxy（别用 mitmdump -s：
  脚本一变它就重新加载，加载失败会不带规则接着当普通代理）。钩子里出任何错都按挡下处理（fail closed）。
  环境变量 MOUSSE_SENTINEL_DIR=<data_dir>/sentinel：里面的 proxy.json（{"server": 服务端地址, "token": sentinel 令牌, "hold_wait": 秒}，600）
  和 secrets.json（{"名字": {"value": 真值, "hosts": ["api.example.com"]}}，600，只有这个进程读）。

这里自己挡的（不问服务端）：
- 目标是私网 / 本机 / 链路本地 / 云元数据 / 保留地址：连接前自己解析域名、查每个地址，再把连接钉在查过的那个 IP 上（防 DNS 换绑）。
- 80 / 443 以外的端口、WebSocket（连上以后能发任何东西，不好一条条看）。
- 带着占位符 MOUSSE_SECRET_<名字>、目标却不在它绑定的网站里的（也包括 secrets.json 里没有的名字）。
其余每个请求 POST <server>/api/egress/check，回答：
- allow → 把占位符换成真值再发；drop → 204；deny → 403（带理由）；
- hold → 等（每秒问一次 /api/egress/holds/<id>，最多 hold_wait 秒）：你放行了就发，不放行 / 要改就 403 带你的话，等不到就 403「还在等你点头」
  （你之后放行的话，一模一样的请求 30 分钟内再发就过）。
- 服务端连不上、出错：403（fail closed）。
回来的内容里要是出现某个真值（被对方原样反射回来），换回占位符再交给沙箱。
不写日志正文：请求的记录由服务端记（egress_log），这里只打一行错误。
"""
from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import logging
import os
import re
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import parse_qsl

from mitmproxy import http

log = logging.getLogger("sentinel")

DIR = Path(os.environ.get("MOUSSE_SENTINEL_DIR") or "~/.openmousse/sentinel").expanduser()
PLACEHOLDER = re.compile(rb"MOUSSE_SECRET_([A-Z][A-Z0-9_]{1,63})")
PORTS = {80, 443}
TEXTY = ("text/", "application/json", "application/javascript", "application/x-javascript", "application/xml", "application/xhtml",
         "application/x-www-form-urlencoded", "application/graphql", "+json", "+xml")
MAX_FIELDS, MAX_VALUE, MAX_TEXT, MAX_PATH = 40, 400, 3000, 2000
# 不许连的地址：私网、本机、链路本地（含云元数据）、CGNAT（含 Tailscale）、组播、保留、文档用、基准测试、IPv6 本地 / 映射 / 6to4 / Teredo / NAT64
BAD_NETS = [ipaddress.ip_network(n) for n in (
    "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16", "172.16.0.0/12", "192.0.0.0/24", "192.0.2.0/24",
    "192.88.99.0/24", "192.168.0.0/16", "198.18.0.0/15", "198.51.100.0/24", "203.0.113.0/24", "224.0.0.0/4", "240.0.0.0/4",
    "255.255.255.255/32", "::/128", "::1/128", "::ffff:0:0/96", "::/96", "64:ff9b::/96", "64:ff9b:1::/48", "100::/64", "2001::/32",
    "2001:2::/48", "2001:db8::/32", "2001:10::/28", "2001:20::/28", "2002::/16", "fc00::/7", "fe80::/10", "fec0::/10", "ff00::/8")]
BAD_NAMES = {"localhost", "localhost.localdomain", "metadata.google.internal", "metadata", "instance-data"}


def own_ips() -> set[str]:
    """这台机器自己的地址（含公网 IP）：沙箱不许经代理连回主机上的任何服务。"""
    import subprocess
    try:
        out = subprocess.run(["ip", "-j", "addr"], capture_output=True, text=True, timeout=5).stdout
        return {a["local"] for i in json.loads(out or "[]") for a in i.get("addr_info") or [] if a.get("local")}
    except Exception:  # noqa: BLE001
        return set()


def load_json(name: str) -> dict:
    try:
        v = json.loads((DIR / name).read_text(encoding="utf8"))
        return v if isinstance(v, dict) else {}
    except (OSError, ValueError):
        return {}


def bad_ip(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip.split("%", 1)[0])
    except ValueError:
        return True
    if isinstance(a, ipaddress.IPv6Address) and a.ipv4_mapped:
        a = a.ipv4_mapped
    return any(a in n for n in BAD_NETS if n.version == a.version) or not a.is_global


def texty(ctype: str) -> bool:
    c = (ctype or "").lower()
    return any(t in c for t in TEXTY)


def reply(flow: http.HTTPFlow, code: int, kind: str, message: str, **extra) -> None:
    """给沙箱的回答：浏览器要网页就给一页字，脚本给 JSON。都带 X-Sentinel 头。"""
    body = {"sentinel": kind, "message": message, **extra}
    accept = flow.request.headers.get("accept", "")
    if "text/html" in accept and "json" not in accept:
        esc = message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        page = (f"<!doctype html><meta charset=utf-8><title>Sentinel</title><body style='font:16px system-ui;margin:2em'>"
                f"<h3>Sentinel · {kind}</h3><p>{esc}</p><pre>{json.dumps(extra, ensure_ascii=False)}</pre></body>")
        flow.response = http.Response.make(code, page.encode(), {"Content-Type": "text/html; charset=utf-8", "X-Sentinel": kind})
    else:
        flow.response = http.Response.make(code, json.dumps(body, ensure_ascii=False).encode(),
                                           {"Content-Type": "application/json; charset=utf-8", "X-Sentinel": kind})


class Sentinel:
    def __init__(self) -> None:
        self.cfg = load_json("proxy.json")
        self.secrets: dict = {}
        self.secrets_mtime = 0.0
        self.own = own_ips()
        self.own_at = time.monotonic()

    # —— 密钥 ——
    def load_secrets(self) -> dict:
        p = DIR / "secrets.json"
        try:
            m = p.stat().st_mtime
        except OSError:
            self.secrets = {}
            return self.secrets
        if m != self.secrets_mtime:
            raw = load_json("secrets.json")
            self.secrets = {k: v for k, v in raw.items() if isinstance(v, dict) and isinstance(v.get("value"), str) and v["value"]}
            self.secrets_mtime = m
        return self.secrets

    def bound(self, name: str, host: str) -> bool:
        s = self.load_secrets().get(name)
        hosts = [str(h).lower() for h in (s or {}).get("hosts") or []]
        return bool(s) and host in hosts  # 精确匹配，不认后缀

    # —— 连接：只许公网地址 ——
    async def server_connect(self, data) -> None:
        try:
            await self._server_connect(data)
        except Exception as e:  # noqa: BLE001 — 查不了就不连（fail closed）
            log.error("sentinel: server_connect failed: %s", type(e).__name__)
            data.server.error = "Sentinel: internal error, not connecting"

    async def _server_connect(self, data) -> None:
        host, port = data.server.address
        if port not in PORTS:
            data.server.error = f"Sentinel: port {port} is not allowed"
            return
        name = str(host).lower().rstrip(".")
        if name in BAD_NAMES:
            data.server.error = f"Sentinel: {name} is not allowed"
            return
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(name, port, type=socket.SOCK_STREAM)
        except OSError as e:
            data.server.error = f"Sentinel: can't resolve {name} ({e})"
            return
        ips = [i[4][0] for i in infos]
        if time.monotonic() - self.own_at > 600:
            self.own, self.own_at = own_ips(), time.monotonic()
        if not ips or any(bad_ip(ip) or ip in self.own for ip in ips):
            data.server.error = f"Sentinel: {name} resolves to a private or reserved address"
            return
        if not data.server.sni and not re.fullmatch(r"[0-9.:]+", name):
            data.server.sni = name
        data.server.address = (ips[0], port)  # 钉在查过的地址上

    # —— 请求 ——
    def summarize(self, req: http.Request) -> dict:
        ctype = req.headers.get("content-type", "").lower()
        raw = req.raw_content
        body_type, fields, text = "none", [], ""
        if raw is None:
            body_type = "streamed"
        elif raw:
            if "application/x-www-form-urlencoded" in ctype:
                body_type = "form"
                fields = [{"name": k[:80], "value": v[:MAX_VALUE]} for k, v in parse_qsl(req.get_text(strict=False) or "", keep_blank_values=True)]
            elif "json" in ctype:
                body_type = "json"
                try:
                    fields = flatten(json.loads(req.get_text(strict=False) or "null"))
                except ValueError:
                    text = (req.get_text(strict=False) or "")[:MAX_TEXT]
            elif "multipart/form-data" in ctype:
                body_type = "multipart"
                try:
                    for k, v in req.multipart_form.items(multi=True):
                        val = v.decode("utf8", "replace") if len(v) <= 4096 else f"<file {len(v)} bytes>"
                        fields.append({"name": k.decode("utf8", "replace")[:80], "value": val[:MAX_VALUE]})
                except Exception:  # noqa: BLE001
                    pass
            elif texty(ctype):
                body_type, text = "text", (req.get_text(strict=False) or "")[:MAX_TEXT]
            else:
                body_type = "binary"
        return {"bodyType": body_type, "bodyLength": len(raw or b""), "fields": fields[:MAX_FIELDS], "text": text}

    def ask(self, path: str, payload: dict | None = None, timeout: float = 60) -> dict:
        base = str(self.cfg.get("server") or "").rstrip("/")
        req = urllib.request.Request(base + path, data=json.dumps(payload).encode() if payload is not None else None,
                                     method="POST" if payload is not None else "GET",
                                     headers={"Authorization": f"Bearer {self.cfg.get('token') or ''}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 — 本机服务端
            return json.loads(r.read().decode("utf8"))

    async def request(self, flow: http.HTTPFlow) -> None:
        try:
            await self._request(flow)
        except Exception as e:  # noqa: BLE001 — 规则出错：这一个请求挡下（fail closed），不能让它原样出去
            log.error("sentinel: request hook failed: %s", type(e).__name__)
            reply(flow, 403, "denied", "Sentinel hit an internal error, so this request is blocked.")

    async def _request(self, flow: http.HTTPFlow) -> None:
        req = flow.request
        # 按真正要连的目标判断（CONNECT / 网址里的主机），不信 Host 头：两个对不上就挡，免得「连 A、头里写 B」骗过绑定检查
        host = (req.host or "").lower().rstrip(".")
        hh = (req.host_header or "").lower().rstrip(".")
        if hh and hh.rsplit(":", 1)[0].strip("[]") != host.strip("[]") and hh != host:
            return reply(flow, 403, "denied", f"Sentinel: the Host header ({hh}) doesn't match where this goes ({host}).")
        if req.port not in PORTS:
            return reply(flow, 403, "denied", f"Sentinel only lets errands reach ports 80 and 443 (not {req.port}).")
        if host in BAD_NAMES or (re.fullmatch(r"[0-9.:\[\]]+", host) and bad_ip(host.strip("[]"))):
            return reply(flow, 403, "denied", f"Sentinel doesn't let errands reach {host} (private or local address).")
        if req.headers.get("upgrade", "").lower() == "websocket":
            return reply(flow, 403, "denied", "Sentinel doesn't allow WebSocket connections from errands.")
        # 占位符：只许发往绑定的网站
        url_b = req.url.encode()
        blob = url_b + b"\n" + b"\n".join(v.encode("utf8", "replace") for v in req.headers.values()) + b"\n" + (req.raw_content or b"")
        names = sorted({m.decode() for m in PLACEHOLDER.findall(blob)})
        unbound = [n for n in names if not self.bound(n, host)]
        if unbound:
            return reply(flow, 403, "denied", f"Secret {', '.join(unbound)} may not be sent to {host}.", secrets=unbound)
        sha = hashlib.sha256(req.method.upper().encode() + b"\n" + url_b + b"\n" + (req.raw_content or b"")).hexdigest()
        pl = {"client": flow.client_conn.peername[0] if flow.client_conn.peername else "", "method": req.method, "scheme": req.scheme,
              "host": host, "port": req.port, "path": req.path[:MAX_PATH],
              "headers": {k: req.headers.get(k, "")[:300] for k in ("content-type", "sec-fetch-mode", "sec-fetch-dest", "sec-fetch-site",
                                                                  "origin", "referer") if req.headers.get(k)},
              "secrets": names, "sha": sha, **self.summarize(req)}
        try:
            out = await asyncio.to_thread(self.ask, "/api/egress/check", pl, 75)
        except Exception as e:  # noqa: BLE001 — 服务端不在：挡
            log.warning("sentinel: check failed: %s", type(e).__name__)
            return reply(flow, 403, "denied", "Sentinel couldn't reach its server, so nothing goes out right now.")
        d = out.get("decision")
        if d == "drop":
            flow.response = http.Response.make(204, b"", {"X-Sentinel": "dropped"})
            return
        if d == "hold":
            d, out = await self.wait(flow, out)
        if d != "allow":
            return reply(flow, 403, out.get("kind") or "denied", out.get("reason") or "Sentinel blocked this request.",
                         **{k: out[k] for k in ("inbox", "note") if out.get(k)})
        if names:
            self.substitute(req, names)

    async def wait(self, flow: http.HTTPFlow, out: dict) -> tuple[str, dict]:
        hid, iid = out.get("hold"), out.get("inbox")
        deadline = time.monotonic() + float(self.cfg.get("hold_wait") or 600)
        while time.monotonic() < deadline:
            await asyncio.sleep(1.0)
            try:
                st = await asyncio.to_thread(self.ask, f"/api/egress/holds/{hid}", None, 15)
            except Exception:  # noqa: BLE001
                continue
            s = st.get("status")
            if s == "approved":
                return "allow", out
            if s == "rejected":
                return "rejected", {"kind": "rejected", "inbox": iid, "note": st.get("note") or "",
                                    "reason": "The user didn't let this request through" + (f": {st['note']}" if st.get("note") else ".")}
            if s == "expired":
                return "expired", {"kind": "expired", "inbox": iid, "reason": "The approval card expired without an answer."}
        return "held", {"kind": "held", "inbox": iid,
                        "reason": "Held for the user's OK in the app. Once they let it through, send the exact same request again within 30 minutes."}

    def substitute(self, req: http.Request, names: list[str]) -> None:
        sec = self.load_secrets()
        def sub(b: bytes) -> bytes:
            return PLACEHOLDER.sub(lambda m: sec[m.group(1).decode()]["value"].encode() if m.group(1).decode() in sec else m.group(0), b)
        req.path = sub(req.path.encode()).decode()  # 只动路径和查询串，不动主机
        for k in list(req.headers.keys()):
            vals = req.headers.get_all(k)
            new = [sub(v.encode("utf8", "replace")).decode("utf8", "replace") for v in vals]
            if new != vals:
                req.headers.set_all(k, new)
        if req.raw_content:
            req.content = sub(req.content or b"")

    # —— 不是 HTTP 的一律掐断：隧道里讲别的协议（裸 TCP、UDP、WebSocket）就看不到内容，没法按规矩判 ——
    def tcp_start(self, flow) -> None:
        flow.kill()

    def udp_start(self, flow) -> None:
        flow.kill()

    def websocket_start(self, flow) -> None:
        flow.kill()

    # —— 回来的内容：真值换回占位符 ——
    def response(self, flow: http.HTTPFlow) -> None:
        try:
            self._response(flow)
        except Exception as e:  # noqa: BLE001 — 没法确认里面没有真值：不交给沙箱
            log.error("sentinel: response hook failed: %s", type(e).__name__)
            flow.response = http.Response.make(502, b"Sentinel couldn't check this response.", {"X-Sentinel": "denied"})

    def _response(self, flow: http.HTTPFlow) -> None:
        sec = self.load_secrets()
        if not sec or not flow.response or flow.response.headers.get("X-Sentinel"):
            return
        resp = flow.response
        for k in list(resp.headers.keys()):
            vals = resp.headers.get_all(k)
            new = []
            for v in vals:
                for n, s in sec.items():
                    if s["value"] and s["value"] in v:
                        v = v.replace(s["value"], f"MOUSSE_SECRET_{n}")
                new.append(v)
            if new != vals:
                resp.headers.set_all(k, new)
        if resp.raw_content and len(resp.raw_content) <= 8 * 1024 * 1024 and texty(resp.headers.get("content-type", "")):
            body = resp.content or b""
            changed = body
            for n, s in sec.items():
                val = s["value"].encode()
                if val and val in changed:
                    changed = changed.replace(val, f"MOUSSE_SECRET_{n}".encode())
            if changed is not body:
                resp.content = changed


def flatten(v, prefix: str = "") -> list[dict]:
    """JSON 平铺成字段：a.b[0].c = 值（字符串截短）。"""
    out: list[dict] = []
    if isinstance(v, dict):
        for k, x in list(v.items())[:MAX_FIELDS]:
            out += flatten(x, f"{prefix}.{k}" if prefix else str(k))
    elif isinstance(v, list):
        for i, x in enumerate(v[:10]):
            out += flatten(x, f"{prefix}[{i}]")
    else:
        out.append({"name": prefix[:80] or "(value)", "value": ("" if v is None else str(v))[:MAX_VALUE]})
    return out[:MAX_FIELDS]


addons = [Sentinel()]
