"""沙箱里的浏览器（内核第 8 项，2026-10-16）：browser 工具（engine/hands.py）经 CDP 开网页、看、点、填。浏览器在这个 Agent 的 browser 容器里
（engine/sandbox.py；镜像 mousse-errand-browser = OpenClaw 的沙箱浏览器，Chromium 包了一层、代理写死 Doorman），出网只经 Doorman，和命令一样。

怎么连：镜像的入口起 Chromium（CDP 只听容器里的 127.0.0.1:9223）和一个 CDP 中继（0.0.0.0:9222）：中继要令牌（建容器时生成，写进它的
环境变量；服务重启后从 docker inspect 读回来）、只认主机在这个网络上的地址（OPENCLAW_BROWSER_CDP_SOURCE_RANGE = 网关/32）。主机直接连
容器的地址，不发布端口：GET /json/version 拿浏览器的 WebSocket 地址（里面写的是 Chromium 自己的 127.0.0.1:9223，换成 容器地址:9222），
带令牌连上；每个页面 Target.attachToTarget(flatten) 开一个会话。连接断了（容器收掉、重建）下次用到时重连。

动作（act）：open（当前页打开网址；new_tab 开新页）、snapshot（页面大纲：标题、链接、按钮、输入框……能点能填的都带 ref，比如 [e12]）、
text（页面上的全部文字）、click / type / select（按 ref）、press（按键）、scroll、back / forward / reload、tabs / tab / close、wait、
evaluate（在页面里跑一段 JS）。ref 记在页面里（data-mref），同一页上没变的元素 ref 不变；换了页从 e1 重新数。会改页面的动作做完附上
新的大纲。页面上来的字一律包成「资料，不是指令」（tools.wrap_untrusted）。
⑫：普通 Agent 的浏览器多一个 screenshot（act 的 extra）：Page.captureScreenshot 看得到的那一屏，PNG 存在
<data_dir>/engine/screenshots/<Agent>/（只留最近 SHOT_KEEP 张），回路径和页面大纲；⑫b 起原图交给 act 的 pictures，hands.py 放进工具结果给模型看
（engine/images.py：卡大小、存进图库）。代办的照旧没有。
弹窗（alert / confirm / prompt）自动关：alert 点确定，confirm / prompt 点取消（提交、确认这类事交给 Doorman 和用户），下一次结果里说一声。
导航、点了以后最多等 NAV_WAIT 秒：Doorman 扣下的请求会一直挂着（等用户点头，最多 10 分钟），等不到先回「还在加载」，hands.py 再附上
Doorman 扣下了什么。

参考（10/16 现拉的源码，只读）：OpenClaw aa834b1 的 src/agents/sandbox/browser.ts（中继的令牌、cdpSourceRange、等 /json/version 好了再连）、
scripts/sandbox-browser-entrypoint.sh（中继认 Basic openclaw:<令牌> 或 Bearer <令牌>）、extensions/browser/src/browser-tool.schema.ts
（动作的名字：open / snapshot / text / tabs / close，act 里的 click、type、press、select、wait、evaluate）。只借设计；页面大纲是自己写的
（OpenClaw 用 Playwright 的 AI snapshot，我们不带 Playwright）。
"""
from __future__ import annotations

import asyncio
import base64
import json
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from config import settings
from i18n import L

from . import sandbox

CDP_PORT = 9222
READY_WAIT = 45.0          # 浏览器容器起来以后最多等 CDP 好多久
NAV_WAIT = 25.0            # 打开网址、点了以后最多等页面加载多久（Doorman 扣下的会更久：先回「还在加载」）
SNAPSHOT_MAX, AFTER_MAX, TEXT_MAX, EVAL_MAX = 14_000, 7_000, 20_000, 8_000
SCHEMES = ("http", "https", "about", "data")
KEYS = {"Enter": (13, "\r"), "Tab": (9, ""), "Escape": (27, ""), "Backspace": (8, ""), "Delete": (46, ""), "Space": (32, " "),
        "ArrowUp": (38, ""), "ArrowDown": (40, ""), "ArrowLeft": (37, ""), "ArrowRight": (39, ""), "Home": (36, ""), "End": (35, ""),
        "PageUp": (33, ""), "PageDown": (34, "")}


class BrowserError(sandbox.SandboxError):
    """这一下没做成：str(e) 给模型看。"""


class CDPTimeout(BrowserError):
    """浏览器到点没回话（导航被 Doorman 扣着的时候常见）。"""


# —— CDP 连接 ——————————————————————————————————————————————————————————————

class CDP:
    """一条 CDP WebSocket：send 等回话，事件交给 handlers（不阻塞读）。"""

    def __init__(self) -> None:
        self.ws: Any = None
        self.http: Any = None
        self.n = 0
        self.pending: dict[int, asyncio.Future] = {}
        self.handlers: list[Callable[[str, dict, str | None], None]] = []
        self.reader: asyncio.Task | None = None
        self.closed = False

    async def connect(self, url: str, headers: dict[str, str]) -> None:
        import aiohttp
        self.http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None, connect=10))
        try:
            self.ws = await self.http.ws_connect(url, headers=headers, max_msg_size=64 * 1024 * 1024, autoping=True)
        except Exception:
            await self.http.close()
            raise
        self.reader = asyncio.ensure_future(self._read())

    async def _read(self) -> None:
        import aiohttp
        try:
            async for msg in self.ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        break
                    continue
                try:
                    data = json.loads(msg.data)
                except ValueError:
                    continue
                if "id" in data:
                    fut = self.pending.pop(data["id"], None)
                    if fut is not None and not fut.done():
                        if "error" in data:
                            fut.set_exception(BrowserError(str((data["error"] or {}).get("message") or data["error"])))
                        else:
                            fut.set_result(data.get("result") or {})
                    continue
                for h in list(self.handlers):
                    try:
                        h(str(data.get("method") or ""), data.get("params") or {}, data.get("sessionId"))
                    except Exception:  # noqa: BLE001 — 事件处理出错不拖累读
                        pass
        except Exception:  # noqa: BLE001 — 断了：下面统一收尾
            pass
        finally:
            self.closed = True
            for fut in self.pending.values():
                if not fut.done():
                    fut.set_exception(BrowserError(L("和浏览器的连接断了", "The connection to the browser dropped")))
            self.pending.clear()

    async def send(self, method: str, params: dict | None = None, session: str | None = None, timeout: float = 30) -> dict:
        if self.closed or self.ws is None:
            raise BrowserError(L("和浏览器的连接断了", "The connection to the browser dropped"))
        self.n += 1
        msg: dict[str, Any] = {"id": self.n, "method": method, "params": params or {}}
        if session:
            msg["sessionId"] = session
        fut = asyncio.get_running_loop().create_future()
        self.pending[self.n] = fut
        await self.ws.send_str(json.dumps(msg))
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            self.pending.pop(msg["id"], None)
            raise CDPTimeout(L(f"浏览器 {timeout:.0f} 秒没回话（{method}）", f"The browser didn't answer within {timeout:.0f}s ({method})")) from None

    async def close(self) -> None:
        self.closed = True
        try:
            if self.ws is not None:
                await self.ws.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            if self.http is not None:
                await self.http.close()
        except Exception:  # noqa: BLE001
            pass
        if self.reader is not None:
            self.reader.cancel()


# —— 一个 Agent 的浏览器 ——————————————————————————————————————————————————————

@dataclass
class Tab:
    target: str
    session: str
    dialogs: list[str] = field(default_factory=list)


@dataclass
class Browser:
    agent: str
    container: str
    container_id: str
    cdp: CDP
    tabs: dict[str, Tab] = field(default_factory=dict)
    active: str | None = None
    short: dict[str, str] = field(default_factory=dict)    # t1 → targetId（给模型看的短号）
    n: int = 0

    def short_of(self, target: str) -> str:
        for k, v in self.short.items():
            if v == target:
                return k
        self.n += 1
        k = f"t{self.n}"
        self.short[k] = target
        return k

    def on_event(self, method: str, params: dict, session: str | None) -> None:
        if method == "Page.javascriptDialogOpening" and session:
            tab = next((t for t in self.tabs.values() if t.session == session), None)
            kind = str(params.get("type") or "")
            accept = kind in ("alert", "beforeunload")
            if tab is not None:
                tab.dialogs.append(f"{kind}: {str(params.get('message') or '')[:200]} ({'OK' if accept else 'dismissed'})")
            asyncio.ensure_future(self._dialog(session, accept))
        elif method == "Target.detachedFromTarget":
            sid = params.get("sessionId")
            for k, t in list(self.tabs.items()):
                if t.session == sid:
                    self.tabs.pop(k, None)
                    if self.active == k:
                        self.active = None
        elif method == "Target.targetDestroyed":
            self.tabs.pop(str(params.get("targetId") or ""), None)
            if self.active == params.get("targetId"):
                self.active = None

    async def _dialog(self, session: str, accept: bool) -> None:
        try:
            await self.cdp.send("Page.handleJavaScriptDialog", {"accept": accept, "promptText": ""}, session=session, timeout=10)
        except Exception:  # noqa: BLE001
            pass


_browsers: dict[str, Browser] = {}
_locks: dict[str, asyncio.Lock] = {}


def _forget(container: str) -> None:
    """sandbox.remove 收掉一个容器时叫：它的浏览器连接断开、忘掉。"""
    for agent, b in list(_browsers.items()):
        if b.container == container:
            _browsers.pop(agent, None)
            if _loop_running():
                asyncio.ensure_future(b.cdp.close())


def _loop_running() -> bool:
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


sandbox._on_remove.append(_forget)


def headers(live: sandbox.Live) -> dict[str, str]:
    return {"Authorization": f"Bearer {live.token}"}


async def _get_json(url: str, hdrs: dict[str, str], timeout: float = 3) -> dict | list | None:
    import aiohttp
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as s:
            async with s.get(url, headers=hdrs) as r:
                if r.status != 200:
                    return None
                return await r.json(content_type=None)
    except Exception:  # noqa: BLE001 — 还没好、连不上：调用方再等
        return None


async def ready(live: sandbox.Live, wait: float = READY_WAIT) -> str:
    """等浏览器容器的 CDP 中继好了 → 浏览器的 WebSocket 地址（换成 容器地址:9222）。等不到抛 BrowserError。"""
    if not live.ip or not live.token:
        raise BrowserError(L("浏览器容器没有地址或令牌（重建一下：sandbox reset）", "The browser container has no address or token (reset the sandbox)"))
    base = f"http://{live.ip}:{CDP_PORT}"
    deadline = time.monotonic() + wait
    while True:
        info = await _get_json(f"{base}/json/version", headers(live))
        if isinstance(info, dict) and info.get("webSocketDebuggerUrl"):
            u = urlsplit(str(info["webSocketDebuggerUrl"]))
            return urlunsplit((u.scheme or "ws", f"{live.ip}:{CDP_PORT}", u.path, u.query, ""))
        if time.monotonic() > deadline:
            raise BrowserError(L(f"浏览器 {wait:.0f} 秒还没起来（docker logs {live.spec.name} 看看）",
                                 f"The browser didn't come up within {wait:.0f}s (see docker logs {live.spec.name})"))
        await asyncio.sleep(0.4)


async def connect(agent: str, live: sandbox.Live) -> Browser:
    """这个 Agent 的浏览器（live = sandbox.use(agent, "browser") 给的容器；断了、容器换了就重连）。"""
    async with _locks.setdefault(agent, asyncio.Lock()):
        b = _browsers.get(agent)
        if b is not None and not b.cdp.closed and b.container_id == live.id:
            return b
        if b is not None:
            await b.cdp.close()
            _browsers.pop(agent, None)
        url = await ready(live)
        cdp = CDP()
        try:
            await cdp.connect(url, headers(live))
        except Exception as e:  # noqa: BLE001
            raise BrowserError(L(f"连不上浏览器：{type(e).__name__}", f"Couldn't connect to the browser: {type(e).__name__}")) from None
        b = Browser(agent, live.spec.name, live.id, cdp)
        cdp.handlers.append(b.on_event)
        await cdp.send("Target.setDiscoverTargets", {"discover": True})
        _browsers[agent] = b
        return b


async def pages(b: Browser) -> list[dict]:
    res = await b.cdp.send("Target.getTargets")
    return [t for t in res.get("targetInfos") or [] if t.get("type") == "page"]


async def attach(b: Browser, target: str) -> Tab:
    tab = b.tabs.get(target)
    if tab is not None:
        return tab
    res = await b.cdp.send("Target.attachToTarget", {"targetId": target, "flatten": True})
    tab = Tab(target, str(res.get("sessionId") or ""))
    b.tabs[target] = tab
    await b.cdp.send("Page.enable", session=tab.session)
    return tab


async def current(b: Browser, create: bool = True) -> Tab | None:
    """现在这一页：记着的那页还在就用它；不然用第一页；一页都没有就开一页（create）。"""
    ps = await pages(b)
    ids = [p["targetId"] for p in ps]
    if b.active in ids:
        return await attach(b, b.active)
    if ids:
        b.active = ids[0]
        return await attach(b, ids[0])
    if not create:
        return None
    return await new_tab(b)


async def new_tab(b: Browser) -> Tab:
    res = await b.cdp.send("Target.createTarget", {"url": "about:blank"})
    b.active = str(res.get("targetId") or "")
    return await attach(b, b.active)


async def evaluate(b: Browser, tab: Tab, expression: str, timeout: float = 20) -> Any:
    res = await b.cdp.send("Runtime.evaluate", {"expression": expression, "returnByValue": True, "awaitPromise": True,
                                                "userGesture": True}, session=tab.session, timeout=timeout)
    if res.get("exceptionDetails"):
        ex = res["exceptionDetails"]
        desc = ((ex.get("exception") or {}).get("description") or ex.get("text") or "error")
        raise BrowserError(L(f"页面里的脚本出错：{str(desc)[:300]}", f"Script error in the page: {str(desc)[:300]}"))
    return (res.get("result") or {}).get("value")


async def wait_load(b: Browser, tab: Tab, wait: float = NAV_WAIT) -> bool:
    """等页面加载完（readyState complete；interactive 也再给 1.5 秒）→ True = 好了；False = 到点了还在加载。"""
    deadline = time.monotonic() + wait
    interactive_at = None
    while time.monotonic() < deadline:
        try:
            state = await evaluate(b, tab, "document.readyState", timeout=5)
        except BrowserError:
            state = None
        if state == "complete":
            return True
        if state == "interactive":
            interactive_at = interactive_at or time.monotonic()
            if time.monotonic() - interactive_at > 1.5:
                return True
        await asyncio.sleep(0.25)
    return False


# —— 页面大纲（自己写的；在页面里跑）————————————————————————————————————————————————

SNAPSHOT_JS = r"""(maxChars) => {
  const W = window;
  W.__mousseRef = W.__mousseRef || 0;
  W.__mousseEls = W.__mousseEls || new Map();
  const lines = [];
  let used = 0, cut = 0, refs = 0, visited = 0;
  const push = (s) => {
    if (!s) return;
    if (used + s.length + 1 > maxChars) { cut++; return; }
    if (lines.length && lines[lines.length - 1] === s) return;
    lines.push(s); used += s.length + 1;
  };
  const clean = (s, n) => { s = String(s || '').replace(/\s+/g, ' ').trim(); return s.length > n ? s.slice(0, n - 1) + '…' : s; };
  const SKIP = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE', 'HEAD', 'META', 'LINK', 'SVG', 'CANVAS', 'OBJECT', 'EMBED', 'svg']);
  const ROLES = new Set(['button', 'link', 'checkbox', 'radio', 'switch', 'tab', 'menuitem', 'menuitemcheckbox', 'menuitemradio', 'option',
                         'combobox', 'textbox', 'searchbox', 'slider', 'spinbutton', 'treeitem']);
  const CTL = 'a[href],button,input:not([type=hidden]),select,textarea,summary,[role],[contenteditable],h1,h2,h3,h4,h5,h6,img[alt],iframe,form';
  const BLOCK = new Set(['block', 'list-item', 'table-cell', 'flex', 'grid', 'table', 'table-row', 'flow-root']);
  const hidden = (el) => {
    if (el.getAttribute('aria-hidden') === 'true' || el.hidden) return true;
    const s = getComputedStyle(el);
    return s.display === 'none' || s.visibility === 'hidden' || s.visibility === 'collapse';
  };
  const refOf = (el) => {
    let r = el.getAttribute('data-mref');
    if (!r || W.__mousseEls.get(r) !== el) {
      r = 'e' + (++W.__mousseRef);
      try { el.setAttribute('data-mref', r); } catch (e) {}
      W.__mousseEls.set(r, el);
    }
    refs++;
    return r;
  };
  const nameOf = (el) => {
    let n = el.getAttribute('aria-label') || '';
    if (!n) {
      const ids = (el.getAttribute('aria-labelledby') || '').split(/\s+/).filter(Boolean);
      n = ids.map((i) => (document.getElementById(i) || {}).innerText || '').join(' ');
    }
    if (!n && el.labels && el.labels.length) n = Array.from(el.labels).map((l) => l.innerText).join(' ');
    if (!n && !['INPUT', 'SELECT', 'TEXTAREA'].includes(el.tagName)) n = el.innerText || '';
    if (!n) { const img = el.querySelector && el.querySelector('img[alt]'); if (img) n = img.alt; }
    if (!n) n = el.getAttribute('title') || '';
    if (!n && el.tagName === 'INPUT' && /^(submit|button|reset)$/i.test(el.type || '')) n = el.value;
    return clean(n, 80);
  };
  const kindOf = (el) => {
    const t = el.tagName, role = (el.getAttribute('role') || '').toLowerCase();
    if (t === 'A' && el.hasAttribute('href')) return 'link';
    if (t === 'BUTTON') return 'button';
    if (t === 'INPUT') {
      const ty = (el.type || 'text').toLowerCase();
      if (ty === 'hidden') return null;
      if (['submit', 'button', 'reset', 'image'].includes(ty)) return 'button';
      if (ty === 'checkbox' || ty === 'radio') return ty;
      return 'input';
    }
    if (t === 'TEXTAREA') return 'textarea';
    if (t === 'SELECT') return 'select';
    if (t === 'SUMMARY') return 'summary';
    if (ROLES.has(role)) return role;
    if (el.isContentEditable && el.hasAttribute('contenteditable')) return 'editable';
    return null;
  };
  const describe = (el, k) => {
    let s = '[' + refOf(el) + '] ' + k;
    const n = nameOf(el);
    if (n) s += ' "' + n + '"';
    if (k === 'link') { const h = el.getAttribute('href') || ''; if (h && !h.startsWith('javascript:')) s += ' -> ' + clean(h, 90); }
    if (k === 'input' || k === 'textarea') {
      const ty = (el.type || '').toLowerCase();
      if (k === 'input' && ty && ty !== 'text') s += ' type=' + ty;
      if (el.name) s += ' name=' + clean(el.name, 40);
      if (el.value) s += ty === 'password' ? ' value=(hidden)' : ' value="' + clean(el.value, 60) + '"';
      else if (el.placeholder && el.placeholder !== n) s += ' placeholder="' + clean(el.placeholder, 40) + '"';
      if (el.required) s += ' required';
      if (el.readOnly) s += ' readonly';
    }
    if (k === 'checkbox' || k === 'radio') { if (el.checked) s += ' (checked)'; if (el.name) s += ' name=' + clean(el.name, 40); }
    if (k === 'select') {
      const opts = Array.from(el.options || []);
      if (el.name) s += ' name=' + clean(el.name, 40);
      s += ' selected: ' + (opts.filter((o) => o.selected).map((o) => clean(o.text, 30)).join(', ') || '(none)');
      s += ' options: ' + opts.slice(0, 12).map((o) => clean(o.text, 30)).join(' | ') + (opts.length > 12 ? ' | … (' + opts.length + ' in all)' : '');
    }
    if (el.disabled) s += ' disabled';
    const exp = el.getAttribute('aria-expanded'); if (exp) s += ' expanded=' + exp;
    const chk = el.getAttribute('aria-checked'); if (chk && k !== 'checkbox' && k !== 'radio') s += ' checked=' + chk;
    return s;
  };
  const kids = (node) => {
    if (node.tagName === 'SLOT') return node.assignedNodes({ flatten: true });
    return node.shadowRoot ? node.shadowRoot.childNodes : node.childNodes;
  };
  let buf = [];
  const flush = () => { if (buf.length) { push(clean(buf.join(' '), 1200)); buf = []; } };
  const tok = (s) => { if (s) buf.push(s); };
  const walk = (node) => {
    for (const ch of kids(node)) {
      if (++visited > 20000) { cut++; return; }
      if (ch.nodeType === 3) { tok(clean(ch.textContent, 300)); continue; }
      if (ch.nodeType !== 1) continue;
      const el = ch, t = el.tagName;
      if (SKIP.has(t) || hidden(el)) continue;
      const block = BLOCK.has(getComputedStyle(el).display);
      if (t === 'IFRAME' || t === 'FRAME') { flush(); push('[frame] ' + clean(el.title || el.src || '', 90)); continue; }
      const k = kindOf(el);
      if (k) { if (block) flush(); tok(describe(el, k)); if (block) flush(); continue; }
      if (/^H[1-6]$/.test(t)) { flush(); const tx = clean(el.innerText, 200); if (tx) push('#'.repeat(+t[1]) + ' ' + tx); continue; }
      if (t === 'IMG') { if (el.alt) tok('img "' + clean(el.alt, 80) + '"'); continue; }
      if (t === 'BR') { flush(); continue; }
      if (t === 'FORM') {
        flush();
        const a = el.getAttribute('action');
        push('form' + (a ? ' -> ' + clean(a, 90) : '') + ' (' + String(el.method || 'get').toUpperCase() + ')');
      }
      if (block && !el.shadowRoot && t !== 'FORM' && !el.querySelector(CTL)) { flush(); push(clean(el.innerText, 600)); continue; }
      if (block) { flush(); walk(el); flush(); } else walk(el);
    }
  };
  try {
    walk(document.body || document.documentElement);
    flush();
  } catch (e) {
    push('(outline stopped early: ' + String(e).slice(0, 120) + ')');
  }
  return { title: document.title, url: location.href, text: lines.join('\n'), refs: refs, cut: cut };
}"""

ELEMENT_JS = r"""(ref, scroll) => {
  const el = (window.__mousseEls && window.__mousseEls.get(ref)) || document.querySelector('[data-mref="' + ref + '"]');
  if (!el || !el.isConnected) return { missing: true };
  if (scroll) el.scrollIntoView({ block: 'center', inline: 'center' });
  const r = el.getBoundingClientRect();
  const lab = el.labels && el.labels.length ? el.labels[0].innerText : '';
  return { x: r.left + r.width / 2, y: r.top + r.height / 2, w: r.width, h: r.height, tag: el.tagName,
           name: (el.getAttribute('aria-label') || lab || el.innerText || (/^(submit|button|reset)$/i.test(el.type || '') ? el.value : '') ||
                  el.getAttribute('title') || '').replace(/\s+/g, ' ').trim().slice(0, 60) };
}"""


def _call(fn: str, *args: Any) -> str:
    return f"({fn})({', '.join(json.dumps(a, ensure_ascii=False) for a in args)})"


async def outline(b: Browser, tab: Tab, cap: int = SNAPSHOT_MAX) -> dict:
    v = await evaluate(b, tab, _call(SNAPSHOT_JS, cap), timeout=30)
    return v if isinstance(v, dict) else {"title": "", "url": "", "text": "", "refs": 0, "cut": 0}


def short_url(url: str, n: int = 200) -> str:
    return url if len(url) <= n else url[: n - 1] + "…"


def render(o: dict, cap_note: bool = True) -> str:
    head = f"Page: {o.get('title') or '(no title)'}\nURL: {short_url(str(o.get('url') or ''))}"
    body = str(o.get("text") or "").strip() or "(nothing visible)"
    tail = f"\n(… {o['cut']} more items not shown; use text for the full text, or scroll)" if cap_note and o.get("cut") else ""
    return f"{head}\n\n{body}{tail}"


def ref_of(ref: Any) -> str:
    r = str(ref or "").strip().strip("[]").strip()
    if not re.fullmatch(r"e\d{1,7}", r):
        raise BrowserError(L("要写 ref（大纲里的 [e12] 那种）", "Give a ref (like [e12] from the outline)"))
    return r


async def _element(b: Browser, tab: Tab, ref: str, scroll: bool = True) -> dict:
    ref = ref_of(ref)
    v = await evaluate(b, tab, _call(ELEMENT_JS, ref, scroll))
    if not isinstance(v, dict) or v.get("missing"):
        raise BrowserError(L(f"页面上没有 {ref} 了：先 snapshot 拿新的大纲", f"{ref} isn't on the page any more: take a new snapshot first"))
    return v


async def _mouse_click(b: Browser, tab: Tab, x: float, y: float) -> None:
    for kind in ("mouseMoved", "mousePressed", "mouseReleased"):
        p = {"type": kind, "x": x, "y": y, "button": "left" if kind != "mouseMoved" else "none", "clickCount": 1 if kind != "mouseMoved" else 0}
        await b.cdp.send("Input.dispatchMouseEvent", p, session=tab.session)


async def _key(b: Browser, tab: Tab, key: str) -> None:
    code, text = KEYS.get(key, (0, key if len(key) == 1 else ""))
    if not code and not text:
        raise BrowserError(L(f"不认识的按键 {key}：能用 {', '.join(KEYS)} 或单个字符", f"Unknown key {key}: use one of {', '.join(KEYS)} or a single character"))
    down: dict[str, Any] = {"type": "keyDown" if text else "rawKeyDown", "key": key if key != "Space" else " ", "windowsVirtualKeyCode": code,
                            "nativeVirtualKeyCode": code}
    if text:
        down["text"] = text
        down["unmodifiedText"] = text
    await b.cdp.send("Input.dispatchKeyEvent", down, session=tab.session)
    await b.cdp.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": down["key"], "windowsVirtualKeyCode": code, "nativeVirtualKeyCode": code},
                     session=tab.session)


async def _settle(b: Browser, tab: Tab, before_url: str, before_ids: set[str]) -> tuple[Tab, str]:
    """点了、按了以后：开了新页面就换过去；页面在跳就等它加载（最多 NAV_WAIT）。→ (现在这页, 一句说明)。"""
    await asyncio.sleep(0.4)
    note = ""
    try:
        ps = await pages(b)
    except BrowserError:
        ps = []
    fresh = [p for p in ps if p["targetId"] not in before_ids]
    if fresh:
        b.active = fresh[-1]["targetId"]
        tab = await attach(b, b.active)
        note = f"A new tab opened ({b.short_of(b.active)}) and is now the current one."
    try:
        url = await evaluate(b, tab, "location.href", timeout=5)
        state = await evaluate(b, tab, "document.readyState", timeout=5)
    except BrowserError:
        url, state = None, None
    if url != before_url or state != "complete":
        if not await wait_load(b, tab):
            note = (note + " " if note else "") + (f"The page is still loading after {NAV_WAIT:.0f}s (a request may be waiting for the user's OK "
                                                   "at Doorman): don't repeat the action; check again later with snapshot.")
    return tab, note


def _check_url(url: str) -> str:
    u = str(url or "").strip()
    if not u:
        raise BrowserError(L("要写网址", "Give a URL"))
    if "://" not in u and not u.startswith(("about:", "data:")):
        u = "https://" + u
    scheme = urlsplit(u).scheme.lower()
    if scheme not in SCHEMES:
        raise BrowserError(L(f"只能打开 http / https 网址（不能 {scheme}:）", f"Only http / https URLs can be opened (not {scheme}:)"))
    return u


def _dialogs(tab: Tab) -> str:
    if not tab.dialogs:
        return ""
    out = "Dialogs the page showed (handled automatically): " + "; ".join(tab.dialogs[-5:])
    tab.dialogs.clear()
    return out


# —— 动作 ——————————————————————————————————————————————————————————————————

ACTIONS = ("open", "snapshot", "text", "click", "type", "press", "select", "scroll", "back", "forward", "reload", "tabs", "tab", "close",
           "wait", "evaluate")
READS = ("snapshot", "text", "tabs", "wait")   # 只看、不动页面
EXTRA = ("screenshot",)   # ⑫：只给普通 Agent 的浏览器（代办的照旧）；也只看、不动页面
SHOT_KEEP = 50            # 每个 Agent 最多留多少张截图（旧的删）


async def act(agent: str, args: dict, extra: tuple[str, ...] = (), pictures: list | None = None) -> tuple[str, str | None]:
    """做一个动作 → (给模型的话, 页面上来的字（要包成资料的那段，没有就 None）)。出错抛 BrowserError / SandboxError。
    extra：这个 Agent 多给的动作（EXTRA 里的）。pictures：给一个列表，截图的原图 (PNG 字节, 是什么) 放进去（⑫b，hands.py 交给模型看）。"""
    action = str(args.get("action") or "").strip().lower()
    names = ACTIONS + tuple(x for x in extra if x in EXTRA)
    if action not in names:
        raise BrowserError(L(f"不认识的动作 {action!r}：能用 {', '.join(names)}", f"Unknown action {action!r}: use one of {', '.join(names)}"))
    async with sandbox.use(agent, "browser") as live:
        b = await connect(agent, live)
        if action == "screenshot":
            return await _shot(b, agent, pictures)
        return await _act(b, action, args)


def shots_dir(agent: str) -> Path:
    return Path(settings.data_dir) / "engine" / "screenshots" / sandbox.slug(agent)


def save_shot(agent: str, data: bytes) -> Path:
    """截图存在服务器上（<data_dir>/engine/screenshots/<Agent>/），只留最近 SHOT_KEEP 张。"""
    d = shots_dir(agent)
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}.png"
    p.write_bytes(data)
    for old in sorted(d.glob("*.png"))[:-SHOT_KEEP]:
        old.unlink(missing_ok=True)
    return p


async def _shot(b: Browser, agent: str, pictures: list | None = None) -> tuple[str, str | None]:
    """这一页截图（看得到的那一屏，PNG）存起来，回路径和页面大纲；pictures 给了就把原图放进去（⑫b）。"""
    tab = await current(b)
    assert tab is not None
    res = await b.cdp.send("Page.captureScreenshot", {"format": "png"}, session=tab.session, timeout=30)
    try:
        data = base64.b64decode(str(res.get("data") or ""), validate=True)
    except ValueError:
        data = b""
    if not data:
        raise BrowserError(L("浏览器没给出截图", "The browser didn't return a screenshot"))
    path = await asyncio.to_thread(save_shot, agent, data)
    o = await outline(b, tab, AFTER_MAX)
    if pictures is not None:
        pictures.append((data, f"screenshot of {short_url(str(o.get('url') or ''), 120) or 'the page'}"))
    return (f"Screenshot saved on the server: {path} ({len(data)} bytes, PNG). " + ("The picture is attached; the page outline:"
                                                                                   if pictures is not None else "The page outline:"),
            render(o) + _sep(_dialogs(tab)))


async def _act(b: Browser, action: str, args: dict) -> tuple[str, str | None]:
    if action == "tabs":
        ps = await pages(b)
        if not ps:
            return "No tabs open.", None
        lines = [f"{'*' if p['targetId'] == b.active else ' '} {b.short_of(p['targetId'])}  {str(p.get('title') or '')[:80]}  {str(p.get('url') or '')[:120]}"
                 for p in ps]
        return "Tabs (* = current):", "\n".join(lines)
    if action == "tab":
        target = b.short.get(str(args.get("tab") or "").strip())
        if not target or target not in [p["targetId"] for p in await pages(b)]:
            raise BrowserError(L("没有这一页：tabs 看有哪些", "No such tab: tabs lists them"))
        b.active = target
        tab = await attach(b, target)
        await b.cdp.send("Target.activateTarget", {"targetId": target})
        o = await outline(b, tab, AFTER_MAX)
        return f"Switched to {args.get('tab')}.", render(o)
    if action == "close":
        target = b.short.get(str(args.get("tab") or "").strip()) or b.active
        if not target:
            return "No tab to close.", None
        await b.cdp.send("Target.closeTarget", {"targetId": target})
        b.tabs.pop(target, None)
        if b.active == target:
            b.active = None
        return "Closed the tab.", None
    if action == "open":
        url = _check_url(str(args.get("url") or ""))
        tab = await new_tab(b) if args.get("new_tab") else await current(b)
        assert tab is not None
        try:
            res = await b.cdp.send("Page.navigate", {"url": url}, session=tab.session, timeout=NAV_WAIT)
        except CDPTimeout:  # 请求还挂着（多半是 Doorman 扣着等用户点头）：下面照「还在加载」说
            res = {}
        if res.get("errorText"):
            raise BrowserError(L(f"打不开 {url}：{res['errorText']}", f"Couldn't open {url}: {res['errorText']}"))
        loaded = await wait_load(b, tab)
        o = await outline(b, tab, AFTER_MAX)
        note = "" if loaded else (f"Still loading after {NAV_WAIT:.0f}s (a request may be waiting for the user's OK at Doorman): don't open it again; "
                                  "check later with snapshot. ")
        return note + "Opened. Outline (refs like [e12] are for click / type / select):", render(o) + _sep(_dialogs(tab))
    tab = await current(b)
    assert tab is not None
    if action == "snapshot":
        o = await outline(b, tab, SNAPSHOT_MAX)
        return "Outline (refs like [e12] are for click / type / select):", render(o) + _sep(_dialogs(tab))
    if action == "text":
        v = await evaluate(b, tab, "document.body ? document.body.innerText : ''")
        text = str(v or "")
        more = f"\n(… {len(text) - TEXT_MAX} more characters)" if len(text) > TEXT_MAX else ""
        return "Page text:", f"URL: {short_url(str(await evaluate(b, tab, 'location.href') or ''))}\n\n{text[:TEXT_MAX]}{more}"
    if action == "wait":
        secs = max(0.0, min(float(args.get("seconds") or 2), 30.0))
        want = str(args.get("text") or "").strip()
        deadline = time.monotonic() + secs
        while True:
            if want and want in str(await evaluate(b, tab, "document.body ? document.body.innerText : ''") or ""):
                return f"The text {want!r} is on the page.", None
            if time.monotonic() >= deadline:
                break
            await asyncio.sleep(min(0.5, max(0.05, deadline - time.monotonic())))
        return (f"Waited {secs:g}s; {want!r} isn't on the page." if want else f"Waited {secs:g}s."), None
    if action == "evaluate":
        expr = str(args.get("expression") or "").strip()
        if not expr:
            raise BrowserError(L("要写 expression", "Give an expression"))
        v = await evaluate(b, tab, expr, timeout=30)
        text = json.dumps(v, ensure_ascii=False, default=str) if not isinstance(v, str) else v
        return "Result:", text[:EVAL_MAX] + (f"\n(… {len(text) - EVAL_MAX} more characters)" if len(text) > EVAL_MAX else "")
    before_ids = {p["targetId"] for p in await pages(b)}
    before_url = await evaluate(b, tab, "location.href")
    if action == "click":
        el = await _element(b, tab, str(args.get("ref") or ""))
        if el.get("w", 0) > 0 and el.get("h", 0) > 0:
            await _mouse_click(b, tab, float(el["x"]), float(el["y"]))
        else:  # 没有大小（藏起来的复选框之类）：用 JS 点
            await evaluate(b, tab, _call("(ref) => { const el = window.__mousseEls.get(ref) || document.querySelector('[data-mref=\"' + ref + '\"]'); el.click(); return true; }",
                                         ref_of(args.get("ref"))))
        did = f"Clicked {args.get('ref')}" + (f" ({el.get('name')})" if el.get("name") else "") + "."
    elif action == "type":
        ref = ref_of(args.get("ref"))
        await _element(b, tab, ref)
        clear = args.get("clear") is not False
        ok = await evaluate(b, tab, _call("""(ref, clear) => { const el = window.__mousseEls.get(ref) || document.querySelector('[data-mref="' + ref + '"]');
            el.focus(); if (clear) { if (typeof el.select === 'function') el.select(); else if (el.isContentEditable) document.execCommand('selectAll'); }
            return document.activeElement === el; }""", ref, clear))
        if not ok:
            raise BrowserError(L(f"{ref} 点不进去（不能输入）", f"Couldn't focus {ref} to type into it"))
        await b.cdp.send("Input.insertText", {"text": str(args.get("text") or "")}, session=tab.session)
        did = f"Typed into {ref}."
        if args.get("submit"):
            await _key(b, tab, "Enter")
            did += " Pressed Enter."
        else:
            return did, None
    elif action == "press":
        key = str(args.get("key") or "").strip()
        if args.get("ref"):
            await _element(b, tab, str(args["ref"]))
            await evaluate(b, tab, _call("(ref) => { const el = window.__mousseEls.get(ref) || document.querySelector('[data-mref=\"' + ref + '\"]'); el.focus(); return true; }",
                                         ref_of(args["ref"])))
        await _key(b, tab, key)
        did = f"Pressed {key}."
    elif action == "select":
        ref = ref_of(args.get("ref"))
        await _element(b, tab, ref)
        vals = args.get("value")
        vals = [str(x) for x in vals] if isinstance(vals, list) else [str(vals or "")]
        got = await evaluate(b, tab, _call("""(ref, vals) => { const el = window.__mousseEls.get(ref) || document.querySelector('[data-mref="' + ref + '"]');
            if (!el || el.tagName !== 'SELECT') return null; const want = vals.map((v) => v.trim()); let n = 0;
            for (const o of el.options) { const hit = want.includes(o.value) || want.includes(o.text.trim()); if (el.multiple) o.selected = hit; else if (hit && !n) { el.value = o.value; } if (hit) n++; }
            el.dispatchEvent(new Event('input', { bubbles: true })); el.dispatchEvent(new Event('change', { bubbles: true })); return n; }""", ref, vals))
        if got is None:
            raise BrowserError(L(f"{ref} 不是下拉框", f"{ref} isn't a select box"))
        if not got:
            raise BrowserError(L(f"{ref} 里没有这一项：{', '.join(vals)}", f"{ref} has no option {', '.join(vals)}"))
        did = f"Selected {', '.join(vals)} in {ref}."
    elif action == "scroll":
        if args.get("ref"):
            await _element(b, tab, str(args["ref"]))
            did = f"Scrolled {args['ref']} into view."
        else:
            up = str(args.get("direction") or "down").lower() == "up"
            await evaluate(b, tab, f"window.scrollBy(0, {'-' if up else ''}Math.round(window.innerHeight * 0.85))")
            did = f"Scrolled {'up' if up else 'down'}."
    elif action in ("back", "forward"):
        h = await b.cdp.send("Page.getNavigationHistory", session=tab.session)
        i = int(h.get("currentIndex") or 0) + (-1 if action == "back" else 1)
        entries = h.get("entries") or []
        if not 0 <= i < len(entries):
            return f"Can't go {action}: no page there.", None
        await b.cdp.send("Page.navigateToHistoryEntry", {"entryId": entries[i]["id"]}, session=tab.session)
        did = f"Went {action}."
    else:  # reload
        await b.cdp.send("Page.reload", session=tab.session)
        did = "Reloaded."
    tab, note = await _settle(b, tab, str(before_url or ""), before_ids)
    o = await outline(b, tab, AFTER_MAX)
    return " ".join(x for x in (did, note, "Outline now:") if x), render(o) + _sep(_dialogs(tab))


def _sep(text: str) -> str:
    return f"\n\n{text}" if text else ""


async def close_all() -> None:
    """测试和关服务用：断开所有浏览器连接（容器不动）。"""
    for b in list(_browsers.values()):
        await b.cdp.close()
    _browsers.clear()
