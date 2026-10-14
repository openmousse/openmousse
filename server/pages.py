"""页面（2026-10-13，类 Artifacts 的页面层）：积木画不了的，Agent 写一份网页，app 放进盒子里显示。

和 Claude Artifacts / OpenClaw show_widget 同一个思路，接的是 OpenMousse 自己的数据和界面：
- 页面 = Agent 交的一份自包含网页（HTML 片段；样式、脚本写在里面，库只能从几个 CDN 加载）。服务器存成版本（pages / page_versions）：
  同一个 Agent 同一个 name 再发一次 = 新的一版，id 不变；内容和要读的数据都没变就不加新版。
- 盒子：app 拿到的文档由这里包好（wrap）：最前面一条 CSP（不能联网、不能提交表单、不能嵌别的页面，脚本和样式只认 CDN 白名单），
  后面是桥和主题色。网页版把它放进 sandbox="allow-scripts" 的 iframe（srcdoc，不带 allow-same-origin = 不透明源），
  手机 app 放进 WebView（about:blank，不许跳到别的地址、不存 cookie 和本地存储）；页面读不到 app 的令牌、登录和别的数据，也调不了 /api。
- 桥：页面里 mousse.data(名字) 读数据、mousse.prompt(文字) 把一句话放进这个 Agent 对话的输入框（你按发送才发）。
  经 postMessage（手机上是 window.ReactNativeWebView）交给 app，app 用自己的令牌调 POST /api/pages/{id}/data。能读什么，发布时写在 caps.data 里：
  名字 → 看板的查询 Q（from / where / sort / limit，见 boards.py），只能读这个 Agent 自己的表和只读的系统来源 health:*，只读。
- 授权：声明了 data 的页面第一次打开时 app 问你（列出要读的表和字段），决定记在 page_grants，绑这一版的 digest（HTML + caps 的哈希）：
  内容改了要重新确认（和 OpenClaw 的批准绑内容哈希一样）。没同意的页面照样显示，读数据回 not_granted。
- 对话里的页面卡片：Agent 在哪次回复里发的版本，挂在那条回复下面（page_versions.thread / message_id；cards.on_run_end → link_run，
  认这次回复开跑以后、这个线程的 Agent 发的版本）。/api/chat/cards 里是 kind page。
- 钉到看板：POST /api/pages/{id}/pin 在这个 Agent 的看板最后加一块 type page（boards.EXTRA），看板顶上照常出撤回。
- Agent 用 server/page_ctl.py（MCP 工具 page）：check / publish / list / show / get / data / rm。写法见 packs/core/skills/page。

手机 app 从 1.0.6 起用 WebView 显示（服务器报 pages.native；更早的服务器桥只认 iframe，手机上给说明）。

还没做的：页面不能写数据；页面里不能问模型（以后接 llm-task，零工具）。
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import shutil
import sqlite3
import subprocess
import tempfile
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import boards
import chat
import data
from chat import TZ, _lock, db, log_activity, now_iso
from i18n import L, lang

router = APIRouter()

PID = re.compile(r"^pg-[0-9a-f]{8}$")
NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
BIND = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
MAX_HTML = 256 * 1024
MAX_BINDINGS = 8
ROWS_DEFAULT, ROWS_MAX = 500, 1000
CDN = ("https://cdn.jsdelivr.net", "https://cdnjs.cloudflare.com", "https://unpkg.com")
FONT_CSS, FONT_FILES = "https://fonts.googleapis.com", "https://fonts.gstatic.com"
CSP = ("default-src 'none'; "
       f"script-src 'unsafe-inline' {' '.join(CDN)}; "
       f"style-src 'unsafe-inline' {' '.join(CDN)} {FONT_CSS}; "
       f"font-src {FONT_FILES} data:; img-src data: blob:; media-src data: blob:; "
       "connect-src 'none'; frame-src 'none'; worker-src 'none'; manifest-src 'none'; form-action 'none'; base-uri 'none'")

SCHEMA = """
CREATE TABLE IF NOT EXISTS pages (
  id TEXT PRIMARY KEY, agent TEXT NOT NULL, name TEXT NOT NULL, title TEXT NOT NULL, summary TEXT NOT NULL DEFAULT '',
  version INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT, UNIQUE(agent, name));
CREATE TABLE IF NOT EXISTS page_versions (
  page TEXT NOT NULL, version INTEGER NOT NULL, html TEXT NOT NULL, caps TEXT NOT NULL, digest TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '', by TEXT NOT NULL, thread TEXT, message_id INTEGER, created_at TEXT NOT NULL, PRIMARY KEY(page, version));
CREATE INDEX IF NOT EXISTS page_versions_thread ON page_versions(thread, created_at);
CREATE TABLE IF NOT EXISTS page_grants (
  page TEXT NOT NULL, digest TEXT NOT NULL, decision TEXT NOT NULL, at TEXT NOT NULL, PRIMARY KEY(page, digest));
"""
_ready = False

# app 的两套颜色（app/src/theme.tsx，改那边要同步这里）：页面用 CSS 变量，跟着 app 的深浅色。
THEMES: dict[str, dict[str, Any]] = {
    "dark": {"scheme": "dark", "bg": "#0B0D10", "card": "#14171C", "card2": "#1E2329", "text": "#ECEEF0", "text2": "#A6AEB7", "text3": "#737C86",
             "line": "#252A31", "accent": "#DDB56A", "accentFill": "#D9AE62", "onAccent": "#2A1E06", "data": "#5CCFE6", "dataSoft": "#10303A",
             "good": "#55C795", "goodSoft": "#12301F", "warn": "#E5AD4E", "warnSoft": "#33270F", "bad": "#EC7A70", "badSoft": "#3A1815",
             "grid": "#23272E", "series": ("#2A97B0", "#B5873A", "#8E6FD8", "#3E9E6E", "#5E86D6", "#C0679A")},
    "light": {"scheme": "light", "bg": "#F1F3F4", "card": "#FFFFFF", "card2": "#E7EAEC", "text": "#12151A", "text2": "#4B535C", "text3": "#7D858F",
              "line": "#DCE0E3", "accent": "#9A6B16", "accentFill": "#D9AE62", "onAccent": "#2A1E06", "data": "#0F7A93", "dataSoft": "#DAF0F5",
              "good": "#22794F", "goodSoft": "#DDF1E7", "warn": "#9A6408", "warnSoft": "#FAEBCF", "bad": "#B83A30", "badSoft": "#FBE3E0",
              "grid": "#E6E9EC", "series": ("#1488A3", "#B07D22", "#7B5CC7", "#2F8A5B", "#4A72C2", "#B24F86")},
}


def pdb() -> sqlite3.Connection:
    conn = db()
    ensure(conn)
    return conn


def ensure(conn: sqlite3.Connection) -> None:
    global _ready
    if not _ready:
        conn.executescript(SCHEMA)
        _ready = True


def bad(zh: str, en: str, code: int = 400) -> HTTPException:
    return HTTPException(code, L(zh, en))


# —— 检查：大小、外部地址、脚本语法、要读的数据 ——————————————————————————————

class _Scan(HTMLParser):
    """收集要检查的东西：外部脚本 / 样式表 / 图片的地址、内联脚本（连同开头在第几行）、用不了的标签。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.srcs: list[tuple[str, str]] = []           # (标签, 地址)
        self.scripts: list[tuple[int, str, str]] = []   # (行, type, 代码)
        self.blocked: list[str] = []
        self.forms = 0
        self._in: tuple[int, str] | None = None
        self._buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "script":
            if a.get("src"):
                self.srcs.append(("script", a["src"]))
            else:
                self._in, self._buf = (self.getpos()[0], a.get("type", "").strip().lower()), []
        elif tag == "link" and "stylesheet" in a.get("rel", "").lower() and a.get("href"):
            self.srcs.append(("link", a["href"]))
        elif tag in ("img", "video", "audio", "source") and a.get("src"):
            self.srcs.append((tag, a["src"]))
        elif tag in ("iframe", "frame", "object", "embed", "base", "portal"):
            self.blocked.append(tag)
        elif tag == "form":
            self.forms += 1
        elif tag == "meta" and a.get("http-equiv", "").lower() == "refresh":
            self.blocked.append("meta refresh")

    def handle_data(self, d: str) -> None:
        if self._in is not None:
            self._buf.append(d)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._in is not None:
            self.scripts.append((self._in[0], self._in[1], "".join(self._buf)))
            self._in = None


def allowed_url(kind: str, url: str) -> bool:
    u = url.strip()
    if u.startswith(("data:", "blob:")):
        return kind != "script"
    if kind == "link":
        return u.startswith(tuple(f"{c}/" for c in (*CDN, FONT_CSS)))
    if kind == "script":
        return u.startswith(tuple(f"{c}/" for c in CDN))
    return False  # 图片、音视频只能用 data: / blob:


def node_check(code: str, module: bool) -> str | None:
    """内联脚本的语法（node --check）：有错回第一条（含行号），没装 node 就不查。"""
    node = shutil.which("node")
    if not node or not code.strip():
        return None
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / ("s.mjs" if module else "s.js")
        p.write_text(code, encoding="utf8")
        try:
            r = subprocess.run([node, "--check", str(p)], capture_output=True, text=True, timeout=10)  # noqa: S603 — 自己的临时文件
        except (OSError, subprocess.SubprocessError):
            return None
    if r.returncode == 0:
        return None
    lines = [x for x in (r.stderr or "").splitlines() if x.strip()]
    where = next((x for x in lines if str(p) in x), "")
    m = re.search(r":(\d+)$", where)
    msg = next((x for x in lines if re.match(r"^\w*Error", x)), lines[-1] if lines else "SyntaxError")
    return f"{int(m.group(1)) if m else 0}|{msg}"


def scan(html: str) -> tuple[list[str], list[str]]:
    """回 (错误, 提醒)。有错误就不存。"""
    errors: list[str] = []
    warns: list[str] = []
    if len(html.encode("utf8")) > MAX_HTML:
        errors.append(L(f"页面太大：最多 {MAX_HTML // 1024} KB（数据别写进页面，用 caps.data 读）",
                        f"Page too large: {MAX_HTML // 1024} KB max (don't inline data; read it through caps.data)"))
        return errors, warns
    s = _Scan()
    try:
        s.feed(html)
        s.close()
    except Exception as e:  # noqa: BLE001 — HTMLParser 很宽容，真出错就照原样报
        errors.append(L(f"HTML 解析不了：{e}", f"Can't parse the HTML: {e}"))
        return errors, warns
    for tag, url in s.srcs:
        if not allowed_url(tag, url):
            if tag in ("script", "link"):
                errors.append(L(f"加载不了 {url}：脚本和样式表只能来自 {' / '.join(CDN)}（字体可以用 Google Fonts），写死版本号",
                                f"Can't load {url}: scripts and stylesheets only from {', '.join(CDN)} (fonts: Google Fonts), with a pinned version"))
            else:
                warns.append(L(f"{tag} 的地址 {url[:80]} 显示不了：图片和音视频只能用 data: / blob:，或者直接画 SVG",
                               f"The {tag} at {url[:80]} won't load: images and media only as data: / blob:, or draw SVG"))
    for tag in sorted(set(s.blocked)):
        errors.append(L(f"页面里不能有 <{tag}>（盒子不允许嵌别的页面、改地址）", f"<{tag}> isn't allowed (the sandbox can't embed or redirect)"))
    if s.forms:
        warns.append(L("<form> 提交不了（盒子不允许）：按钮用 onclick 处理", "<form> can't submit in the sandbox: handle buttons with onclick"))
    code = "\n".join(c for _, _, c in s.scripts)
    if re.search(r"\b(fetch|XMLHttpRequest|WebSocket|EventSource|sendBeacon)\b", code):
        warns.append(L("页面不能联网（fetch / XMLHttpRequest / WebSocket 都会被挡）：数据用 mousse.data(名字) 读",
                       "The page can't use the network (fetch / XMLHttpRequest / WebSocket are blocked): read data with mousse.data(name)"))
    if re.search(r"\b(localStorage|sessionStorage|indexedDB|document\.cookie)\b", code):
        warns.append(L("盒子里没有 localStorage / sessionStorage / cookie（不透明源，一用就报错）：状态放在变量里",
                       "No localStorage / sessionStorage / cookies in the sandbox (opaque origin, they throw): keep state in variables"))
    if re.search(r"\bwindow\.open\b|target=[\"']_blank", html):
        warns.append(L("弹不出新窗口（盒子不允许）", "Popups don't open in the sandbox"))
    for line, typ, c in s.scripts:
        if typ and typ not in ("text/javascript", "application/javascript", "module"):
            continue
        err = node_check(c, typ == "module")
        if err:
            n, msg = err.split("|", 1)
            at = line + max(int(n) - 1, 0)
            errors.append(L(f"脚本语法错误（HTML 第 {at} 行附近）：{msg}", f"Script syntax error (around HTML line {at}): {msg}"))
    return errors, warns


def clean_caps(conn: sqlite3.Connection, agent: str, raw: Any) -> dict:
    """caps：{"data": {名字: Q}}。Q 按看板的查询写（from / where / sort / limit），存之前跑一遍，表、字段写错当场报错。"""
    if raw in (None, "", {}):
        return {}
    if not isinstance(raw, dict):
        raise bad("caps 写成 {\"data\": {名字: 查询}}", 'caps is {"data": {name: query}}')
    unknown = set(raw) - {"data"}
    if unknown:
        raise bad(f"caps 只认 data（不认 {', '.join(sorted(unknown))}）", f"caps only takes data (not {', '.join(sorted(unknown))})")
    binds = raw.get("data") or {}
    if not isinstance(binds, dict):
        raise bad("caps.data 写成 {名字: 查询}", "caps.data is {name: query}")
    if len(binds) > MAX_BINDINGS:
        raise bad(f"一个页面最多读 {MAX_BINDINGS} 份数据", f"A page reads at most {MAX_BINDINGS} datasets")
    out: dict[str, dict] = {}
    for name, q in binds.items():
        if not BIND.match(str(name)):
            raise bad(f"数据名「{name}」不行：小写字母开头，只用 a-z 0-9 _", f'Bad data name "{name}": a-z 0-9 _ , starting with a letter')
        if not isinstance(q, dict) or not q.get("from"):
            raise bad(f"「{name}」要写成查询：{{\"from\": 表, \"where\": …, \"sort\": …, \"limit\": N}}",
                      f'"{name}" must be a query: {{"from": table, "where": …, "sort": …, "limit": N}}')
        extra = set(q) - {"from", "where", "sort", "limit"}
        if extra:
            raise bad(f"「{name}」的查询只认 from / where / sort / limit（不认 {', '.join(sorted(extra))}）",
                      f'"{name}": a query only takes from / where / sort / limit (not {", ".join(sorted(extra))})')
        try:
            limit = max(1, min(int(q.get("limit") or ROWS_DEFAULT), ROWS_MAX))
        except (TypeError, ValueError) as e:
            raise bad(f"「{name}」的 limit 要是数字", f'"{name}": limit must be a number') from e
        clean = {"from": str(q["from"]), **({"where": q["where"]} if q.get("where") else {}), **({"sort": q["sort"]} if q.get("sort") else {}),
                 "limit": limit}
        boards.check_query(conn, agent, {**clean, "limit": 1}, "rows")
        out[str(name)] = clean
    return {"data": out} if out else {}


def digest_of(html: str, caps: dict) -> str:
    return hashlib.sha256((html + "\0" + json.dumps(caps, ensure_ascii=False, sort_keys=True)).encode("utf8")).hexdigest()


# —— 读数据（只读，盒子外面算好给页面） ————————————————————————————————————

def run_binding(conn: sqlite3.Connection, agent: str, q: dict) -> dict:
    coll, frm, params = boards.base(conn, agent, q)
    total = conn.execute(f"SELECT COUNT(*) {frm}", params).fetchone()[0]  # noqa: S608 — 字段按表结构校验过，值全是参数（boards.base）
    limit = max(1, min(int(q.get("limit") or ROWS_DEFAULT), ROWS_MAX))
    rows = conn.execute(f"SELECT id, data, created_at, updated_at {frm}{boards.order_sql(coll, q.get('sort'))} LIMIT {limit}", params).fetchall()  # noqa: S608
    out = []
    for r in rows:
        d = json.loads(r["data"])
        out.append({"id": r["id"], **d, "_created": r["created_at"], "_updated": r["updated_at"]})
    fields = [{k: f[k] for k in ("key", "label", "type", "options", "unit", "currency") if f.get(k) is not None} for f in coll.get("fields") or []]
    return {"collection": coll["name"], "title": coll.get("title") or coll["name"], "fields": fields, "rows": out, "total": total,
            "truncated": total > len(out)}


def needs_of(conn: sqlite3.Connection, agent: str, caps: dict) -> list[dict]:
    """授权页上列的：每份数据读哪张表、哪些字段（表的标题、字段的名字），只读。"""
    out = []
    for name, q in (caps.get("data") or {}).items():
        try:
            v = boards.virtual(conn, q["from"])
            coll = v[0] if v else boards.get_coll(conn, agent, q["from"])
            out.append({"name": name, "from": q["from"], "title": coll.get("title") or q["from"],
                        "fields": [f.get("label") or f.get("key") for f in coll.get("fields") or []][:12],
                        "system": bool(v), "readonly": True})
        except HTTPException:
            out.append({"name": name, "from": q["from"], "title": q["from"], "fields": [], "system": False, "readonly": True, "missing": True})
    return out


# —— 包成盒子里的文档 ——————————————————————————————————————————————————

def base_css(t: dict) -> str:
    series = "".join(f"--c{i + 1}:{c};" for i, c in enumerate(t["series"]))
    return (":root{color-scheme:" + t["scheme"] + ";--bg:" + t["bg"] + ";--card:" + t["card"] + ";--card-2:" + t["card2"] + ";--text:" + t["text"]
            + ";--text-2:" + t["text2"] + ";--text-3:" + t["text3"] + ";--line:" + t["line"] + ";--accent:" + t["accent"] + ";--accent-fill:"
            + t["accentFill"] + ";--on-accent:" + t["onAccent"] + ";--data:" + t["data"] + ";--data-soft:" + t["dataSoft"] + ";--good:" + t["good"]
            + ";--good-soft:" + t["goodSoft"] + ";--warn:" + t["warn"] + ";--warn-soft:" + t["warnSoft"] + ";--bad:" + t["bad"] + ";--bad-soft:"
            + t["badSoft"] + ";--grid:" + t["grid"] + ";" + series
            + "--radius:14px;--font:-apple-system,BlinkMacSystemFont,'SF Pro Text','PingFang SC','Noto Sans SC',system-ui,sans-serif}"
            "*{box-sizing:border-box}html,body{margin:0}"
            "body{background:var(--bg);color:var(--text);font:15px/1.5 var(--font);padding:16px;-webkit-text-size-adjust:100%;overflow-wrap:anywhere}"
            "h1{font-size:22px;line-height:1.3;margin:0 0 12px}h2{font-size:17px;margin:20px 0 8px}h3{font-size:15px;margin:16px 0 6px}"
            "p{margin:0 0 10px}a{color:var(--data)}"
            "button{font:inherit;color:var(--text);background:var(--card-2);border:0;border-radius:12px;padding:10px 14px;min-height:44px;cursor:pointer}"
            "button.primary{background:var(--accent-fill);color:var(--on-accent);font-weight:700}"
            "input,select,textarea{font:inherit;color:var(--text);background:var(--card);border:1px solid var(--line);border-radius:10px;padding:8px 10px}"
            "table{width:100%;border-collapse:collapse;font-size:14px}th,td{text-align:left;padding:8px 6px;border-bottom:1px solid var(--line)}"
            "th{color:var(--text-3);font-weight:600;font-size:12px}"
            ".card{background:var(--card);border-radius:var(--radius);padding:14px}.muted{color:var(--text-2)}.faint{color:var(--text-3)}"
            ".row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.grid{display:grid;gap:10px}"
            ".chip{display:inline-block;font-size:12px;font-weight:600;padding:2px 8px;border-radius:8px;background:var(--card-2);color:var(--text-2)}"
            ".num{font-variant-numeric:tabular-nums}")


def bridge_js(meta: dict) -> str:
    """页面里的 window.mousse：data(名字) / prompt(文字) 交给 app；出错报给 app（最多 3 条）。
    同一份文档两种盒子都能用：网页版的 iframe 经 parent.postMessage 发、只收 parent 发来的结果；手机 app 的 WebView 里有
    window.ReactNativeWebView（在页面脚本之前就有），经它发 JSON 字符串，结果由 app 用 injectJavaScript 派一个 message 事件回来
    （没有 source，形状和网页版一样）。手机上再去掉摄像头 / 麦克风 / 定位和弹窗：网页版的 iframe 本来就用不了这些。"""
    return ("(function(){var seq=0,wait={},errs=0,RN=window.ReactNativeWebView,M=" + json.dumps(meta, ensure_ascii=False).replace("</", "<\\/") + ";"
            "if(RN){var N=window.Navigator&&Navigator.prototype;['mediaDevices','getUserMedia','webkitGetUserMedia','geolocation'].forEach(function(k){"
            "try{if(N)delete N[k];delete navigator[k]}catch(e){}});"
            "['alert','confirm','prompt','print'].forEach(function(k){try{window[k]=function(){return k==='confirm'?false:k==='prompt'?null:undefined}}catch(e){}})}"
            "function post(m){m.mousse=1;try{if(RN)RN.postMessage(JSON.stringify(m));else parent.postMessage(m,'*')}catch(e){}}"
            "function ask(type,payload){return new Promise(function(res,rej){var id=++seq;wait[id]={res:res,rej:rej};post({type:type,id:id,payload:payload});"
            "setTimeout(function(){if(wait[id]){delete wait[id];var e=new Error('timeout');e.code='timeout';rej(e)}},30000)})}"
            "window.addEventListener('message',function(e){if(e.source!==(RN?null:parent))return;var m=e.data;if(!m||m.mousse!==1||m.type!=='result')return;"
            "var w=wait[m.id];if(!w)return;delete wait[m.id];if(m.ok)w.res(m.data);else{var x=new Error((m.error&&m.error.message)||'error');"
            "x.code=(m.error&&m.error.code)||'error';w.rej(x)}});"
            "function report(msg,line){if(errs>=3)return;errs++;post({type:'error',payload:{message:String(msg).slice(0,300),line:line||0}})}"
            "window.addEventListener('error',function(e){report(e.message,e.lineno)});"
            "window.addEventListener('unhandledrejection',function(e){var r=e.reason;report(r&&r.message?r.message:r)});"
            "var api={page:Object.freeze(M),data:function(n){return ask('data',{name:String(n)})},"
            "prompt:function(t){return ask('prompt',{text:String(t||'').slice(0,2000)})}};"
            "Object.freeze(api);Object.defineProperty(window,'mousse',{value:api,writable:false,configurable:false})})();")


STRIP = re.compile(r"<!doctype[^>]*>|</?(?:html|head|body)(?:\s[^>]*)?>", re.I)


def wrap(html: str, meta: dict, theme: str) -> str:
    """Agent 的片段 → 盒子里的完整文档：CSP 第一个，然后主题、桥，最后是页面本身（去掉它自己写的 html / head / body 标签）。"""
    t = THEMES.get(theme) or THEMES["dark"]
    head = (f'<meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="{CSP}">'
            '<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="referrer" content="no-referrer">'
            f"<title>{escape(meta.get('title') or '')}</title><style>{base_css(t)}</style><script>{bridge_js(meta)}</script>")
    body = STRIP.sub("", html)
    return f'<!doctype html><html lang="{"zh-CN" if lang() == "zh" else "en"}"><head>{head}</head><body>{body}</body></html>'


def escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


# —— 读写 ——————————————————————————————————————————————————————————————

def find(conn: sqlite3.Connection, ref: str, agent: str | None = None, deleted: bool = False) -> sqlite3.Row:
    if PID.match(ref):
        p = conn.execute("SELECT * FROM pages WHERE id=?", (ref,)).fetchone()
    elif agent:
        p = conn.execute("SELECT * FROM pages WHERE agent=? AND name=?", (agent, ref)).fetchone()
    else:
        p = None
    if not p or (p["deleted_at"] and not deleted):
        raise bad(f"没有页面「{ref}」", f'No page "{ref}"', 404)
    return p


def version_row(conn: sqlite3.Connection, pid: str, version: int | None = None) -> sqlite3.Row:
    if version:
        v = conn.execute("SELECT * FROM page_versions WHERE page=? AND version=?", (pid, version)).fetchone()
    else:
        v = conn.execute("SELECT * FROM page_versions WHERE page=? ORDER BY version DESC LIMIT 1", (pid,)).fetchone()
    if not v:
        raise bad(f"页面没有第 {version} 版", f"The page has no version {version}", 404)
    return v


def grant_of(conn: sqlite3.Connection, pid: str, digest: str) -> str | None:
    r = conn.execute("SELECT decision FROM page_grants WHERE page=? AND digest=?", (pid, digest)).fetchone()
    return r["decision"] if r else None


def page_json(conn: sqlite3.Connection, p: sqlite3.Row, v: sqlite3.Row | None = None) -> dict:
    v = v or version_row(conn, p["id"])
    caps = json.loads(v["caps"] or "{}")
    needs = needs_of(conn, p["agent"], caps)
    return {"id": p["id"], "agent": p["agent"], "name": p["name"], "title": p["title"], "summary": p["summary"], "version": v["version"],
            "latest": p["version"], "digest": v["digest"], "note": v["note"], "createdAt": p["created_at"], "updatedAt": p["updated_at"],
            "deleted": bool(p["deleted_at"]), "needs": needs, "grant": grant_of(conn, p["id"], v["digest"]) if needs else "none",
            "thread": v["thread"], "messageId": v["message_id"]}


def today_local() -> str:
    return datetime.now(TZ).date().isoformat()


def agent_name(agent: str) -> str:
    try:
        return data.agent_label(agent)
    except Exception:  # noqa: BLE001
        return agent


class PageIn(BaseModel):
    agent: str
    name: str
    title: str
    summary: str = ""
    html: str
    caps: dict | None = None
    note: str = ""
    check: bool = False  # 只检查，不存


@router.post("/api/pages")
def publish(body: PageIn):
    """Agent 发布（或只检查）一个页面：同一个 Agent 同一个 name 再发 = 新的一版，内容和 caps 都没变就不加版本。"""
    agent, name = body.agent.strip(), body.name.strip()
    if agent != "main":
        boards.group_row(agent)  # 没有这个 Agent → 404（它自己拿锁，放在我们的锁外面）
    if not NAME.match(name):
        raise bad(f"name「{name}」不行：小写字母或数字开头，只用 a-z 0-9 _ -（同一个 name 再发就是新的一版）",
                  f'Bad name "{name}": a-z 0-9 _ - (publishing the same name again makes a new version)')
    title = body.title.strip()[:60]
    if not title:
        raise bad("要写 title（页面标题，比如「可投岗位」）", "A title is required")
    errors, warns = scan(body.html)
    with _lock, pdb() as conn:
        caps = clean_caps(conn, agent, body.caps) if not errors else {}
        needs = needs_of(conn, agent, caps)
    if body.check or errors:
        return {"ok": not errors, "check": True, "errors": errors, "warnings": warns, "needs": needs}
    digest = digest_of(body.html, caps)
    with _lock, pdb() as conn:
        p = conn.execute("SELECT * FROM pages WHERE agent=? AND name=?", (agent, name)).fetchone()
        now = now_iso()
        summary = body.summary.strip()[:200] or (p["summary"] if p else "")  # 没写就沿用上一版的
        if p and not p["deleted_at"]:
            cur = version_row(conn, p["id"])
            if cur["digest"] == digest:
                conn.execute("UPDATE pages SET title=?, summary=?, updated_at=? WHERE id=?", (title, summary, now, p["id"]))
                return {"ok": True, "id": p["id"], "version": cur["version"], "digest": digest, "unchanged": True, "warnings": warns, "needs": needs}
        if p:
            pid, ver = p["id"], p["version"] + 1
            conn.execute("UPDATE pages SET title=?, summary=?, version=?, updated_at=?, deleted_at=NULL WHERE id=?", (title, summary, ver, now, pid))
        else:
            pid, ver = f"pg-{secrets.token_hex(4)}", 1
            conn.execute("INSERT INTO pages(id, agent, name, title, summary, version, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?)",
                         (pid, agent, name, title, summary, ver, now, now))
        conn.execute("INSERT INTO page_versions(page, version, html, caps, digest, note, by, created_at) VALUES(?,?,?,?,?,?,?,?)",
                     (pid, ver, body.html, json.dumps(caps, ensure_ascii=False), digest, body.note.strip()[:200], "agent", now))
    log_activity(L(f"发布页面「{title}」第 {ver} 版", f'Published page "{title}" v{ver}'), "page", actor=agent_name(agent))
    return {"ok": True, "id": pid, "version": ver, "digest": digest, "warnings": warns, "needs": needs}


@router.get("/api/pages")
def list_pages(agent: str | None = None):
    with _lock, pdb() as conn:
        rows = conn.execute("SELECT * FROM pages WHERE deleted_at IS NULL" + (" AND agent=?" if agent else "") + " ORDER BY updated_at DESC",
                            (agent,) if agent else ()).fetchall()
        return {"ok": True, "pages": [page_json(conn, p) for p in rows]}


@router.get("/api/pages/{pid}")
def get_page(pid: str, version: int | None = None):
    with _lock, pdb() as conn:
        p = find(conn, pid, deleted=True)
        return {"ok": True, **page_json(conn, p, version_row(conn, p["id"], version))}


@router.get("/api/pages/{pid}/versions")
def versions(pid: str):
    with _lock, pdb() as conn:
        p = find(conn, pid, deleted=True)
        rows = conn.execute("SELECT version, note, by, digest, created_at FROM page_versions WHERE page=? ORDER BY version DESC", (p["id"],)).fetchall()
    return {"ok": True, "versions": [{"version": r["version"], "note": r["note"], "by": r["by"], "digest": r["digest"], "createdAt": r["created_at"]}
                                     for r in rows]}


@router.get("/api/pages/{pid}/doc")
def doc(pid: str, theme: str = "dark", version: int | None = None, raw: int = 0):
    """盒子里显示的文档（app 放进 srcdoc）。raw=1：Agent 发的原文（page_ctl get 用，改之前先取回来）。"""
    with _lock, pdb() as conn:
        p = find(conn, pid)
        v = version_row(conn, p["id"], version)
    if raw:
        return {"ok": True, "id": p["id"], "version": v["version"], "html": v["html"], "caps": json.loads(v["caps"] or "{}")}
    meta = {"id": p["id"], "title": p["title"], "version": v["version"], "agent": p["agent"], "today": today_local(), "lang": lang(),
            "theme": "light" if theme == "light" else "dark"}
    return {"ok": True, "id": p["id"], "version": v["version"], "digest": v["digest"], "html": wrap(v["html"], meta, theme)}


class GrantIn(BaseModel):
    digest: str
    decision: str  # allow / deny


@router.post("/api/pages/{pid}/grant")
def grant(pid: str, body: GrantIn):
    """你对这一版要读的数据的决定（app 第一次打开时问）。只认当前这一版的 digest：页面刚更新过就 409，app 重新读。"""
    if body.decision not in ("allow", "deny"):
        raise bad("decision 只能是 allow / deny", "decision must be allow or deny")
    with _lock, pdb() as conn:
        p = find(conn, pid)
        v = version_row(conn, p["id"])
        if v["digest"] != body.digest:
            raise HTTPException(409, {"error": "stale", "message": L("页面刚更新过，请重新打开", "The page just changed; open it again")})
        conn.execute("INSERT OR REPLACE INTO page_grants(page, digest, decision, at) VALUES(?,?,?,?)", (p["id"], v["digest"], body.decision, now_iso()))
        names = "、".join(n["title"] for n in needs_of(conn, p["agent"], json.loads(v["caps"] or "{}")))
    verb = L("允许", "Allowed") if body.decision == "allow" else L("拒绝", "Denied")
    log_activity(L(f"{verb}页面「{p['title']}」第 {v['version']} 版读取：{names}", f'{verb} page "{p["title"]}" v{v["version"]} to read: {names}'), "page")
    return {"ok": True, "grant": body.decision}


class DataIn(BaseModel):
    name: str
    digest: str


@router.post("/api/pages/{pid}/data")
def page_data(pid: str, body: DataIn):
    """页面经桥要的一份数据。只给当前这一版、你点过「允许」的；页面更新过（digest 对不上）回 409 stale。"""
    with _lock, pdb() as conn:
        p = find(conn, pid)
        v = version_row(conn, p["id"])
        if v["digest"] != body.digest:
            raise HTTPException(409, {"error": "stale", "message": L("页面已更新，请重新打开", "The page has changed; open it again")})
        caps = json.loads(v["caps"] or "{}")
        q = (caps.get("data") or {}).get(body.name)
        if q is None:
            raise HTTPException(404, {"error": "unknown", "message": L(f"这个页面没有声明数据「{body.name}」", f'The page declares no data "{body.name}"')})
        if grant_of(conn, p["id"], v["digest"]) != "allow":
            raise HTTPException(403, {"error": "not_granted", "message": L("你没有允许这个页面读取数据", "You haven't allowed this page to read data")})
        return {"ok": True, "name": body.name, **run_binding(conn, p["agent"], q)}


@router.get("/api/pages/{pid}/preview-data")
def preview_data(pid: str, name: str, version: int | None = None):
    """Agent 看自己的页面某份数据现在读出来什么（page_ctl data）：它自己的表，不经你的授权；app 不调这个。"""
    with _lock, pdb() as conn:
        p = find(conn, pid)
        v = version_row(conn, p["id"], version)
        q = (json.loads(v["caps"] or "{}").get("data") or {}).get(name)
        if q is None:
            raise bad(f"这个页面没有声明数据「{name}」", f'The page declares no data "{name}"', 404)
        return {"ok": True, "name": name, **run_binding(conn, p["agent"], q)}


@router.delete("/api/pages/{pid}")
def delete_page(pid: str, by: str = "user"):
    with _lock, pdb() as conn:
        p = find(conn, pid)
        conn.execute("UPDATE pages SET deleted_at=? WHERE id=?", (now_iso(), p["id"]))
    log_activity(L(f"删除页面「{p['title']}」", f'Deleted page "{p["title"]}"'), "page", actor=agent_name(p["agent"]) if by == "agent" else None)
    return {"ok": True}


@router.post("/api/pages/{pid}/pin")
def pin(pid: str):
    """钉到这个 Agent 的看板：最后加一块 type page（你点的，看板顶上照常出撤回）。已经钉过就不重复。"""
    with _lock, pdb() as conn:
        p = find(conn, pid)
    if p["agent"] == "main":
        raise bad("主对话没有看板：页面只能钉到 Agent 的看板上", "The main chat has no board; pin pages to an Agent's board")
    g = boards.group_row(p["agent"])
    with _lock, boards.bdb() as conn:
        ensure(conn)
        cur = boards.live_row(conn, p["agent"])
        live = boards.blocks_of(cur)
        if any(b.get("type") == "page" and b.get("page") == p["id"] for b in live):
            return {"ok": True, "already": True}
        ids = {b.get("id") for b in live}
        bid, n = f"page-{p['name']}"[:40], 2
        while bid in ids:
            bid, n = f"page-{p['name']}"[:37] + f"-{n}", n + 1
        blocks = boards.clean_blocks(conn, p["agent"], {"blocks": [*live, {"id": bid, "type": "page", "title": p["title"], "page": p["id"]}]},
                                     g["dashboard"])
        v = boards.put_live(conn, p["agent"], blocks, L(f"钉上页面「{p['title']}」", f'Pinned page "{p["title"]}"'), "user")
    log_activity(L(f"看板换成第 {v} 版：钉上页面「{p['title']}」", f'Board is now version {v}: pinned page "{p["title"]}"'), "board")
    return {"ok": True, "version": v, "block": bid, "undoTo": cur["version"] if cur else None}  # 撤销 = POST /api/boards/{agent}/revert 回到 undoTo


# —— 看板积木 type page ————————————————————————————————————————————————

def clean_page_block(conn: sqlite3.Connection, agent: str, raw: dict, b: dict) -> dict:
    ensure(conn)
    ref = str(raw.get("page") or "").strip()
    if not ref:
        raise bad(f"积木「{b['id']}」要写 page（页面 id 或 name）", f'Block "{b["id"]}" needs page (a page id or name)')
    p = find(conn, ref, agent)
    if p["agent"] != agent:
        raise bad(f"页面「{ref}」不是这个 Agent 的", f'Page "{ref}" belongs to another Agent')
    b["page"] = p["id"]
    if not b.get("title"):
        b["title"] = p["title"][:40]
    return b


def resolve_page_block(conn: sqlite3.Connection, agent: str, b: dict) -> dict:
    ensure(conn)
    p = conn.execute("SELECT * FROM pages WHERE id=?", (b.get("page"),)).fetchone()
    if not p or p["deleted_at"]:
        return {"page": b.get("page"), "deleted": True}
    v = version_row(conn, p["id"])
    caps = json.loads(v["caps"] or "{}")
    return {"page": p["id"], "title": p["title"], "summary": p["summary"], "version": p["version"], "updatedAt": p["updated_at"],
            "reads": [n["title"] for n in needs_of(conn, agent, caps)], "deleted": False}


boards.EXTRA["page"] = (clean_page_block, resolve_page_block)


# —— 对话里的页面卡片 ——————————————————————————————————————————————————

def link_run(run: chat.Run) -> None:
    """一次回复结束（cards.on_run_end 调）：这次回复开跑以后、这个线程的 Agent 发的版本，挂到这条回复下面。"""
    if run.reply_id is None:
        return
    agent = chat.agent_of(run.thread)
    with _lock, pdb() as conn:
        conn.execute("UPDATE page_versions SET thread=?, message_id=? WHERE message_id IS NULL AND by='agent' AND created_at>=? "
                     "AND page IN (SELECT id FROM pages WHERE agent=?)", (run.thread, run.reply_id, run.started, agent))


def changes_for(thread: str, lo: str, hi: str) -> list[dict]:
    with _lock, pdb() as conn:
        rows = conn.execute("SELECT v.page, v.version, v.note, v.caps, v.thread, v.message_id, v.created_at, p.title, p.summary, p.agent, "
                            "p.version AS latest, p.deleted_at FROM page_versions v JOIN pages p ON p.id=v.page "
                            "WHERE v.thread=? AND v.message_id IS NOT NULL AND v.created_at>=? AND v.created_at<? ORDER BY v.created_at",
                            (thread, lo, hi)).fetchall()
        out = []
        for r in rows:
            reads = [n["title"] for n in needs_of(conn, r["agent"], json.loads(r["caps"] or "{}"))]
            out.append({"kind": "page", "id": f"page:{r['page']}:{r['version']}", "page": r["page"], "version": r["version"], "latest": r["latest"],
                        "title": r["title"], "summary": r["summary"], "note": r["note"], "agent": r["agent"], "reads": reads,
                        "deleted": bool(r["deleted_at"]), "thread": r["thread"], "messageId": r["message_id"], "createdAt": r["created_at"]})
    return out
