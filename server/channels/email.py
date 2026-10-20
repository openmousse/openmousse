"""邮件（第 6 批，10.8）：imaplib 收、smtplib 发，都是标准库，不加依赖。和第 4 批的分工：邮件的收发（IMAP + SMTP、串线程、回信）归这里；
第 4 批的邮件触发（engine/mailwatch.py）用它自己的只读读取器，这里不 import 它。同一个邮箱（主机 + 账号 + 文件夹）别同时配成渠道和触发：
渠道照收，状态里提醒一句（accounts.email_overlap），留哪一边由主人定。

收：IMAP 轮询（poll 秒，默认 60，最少 15）。只读打开文件夹（EXAMINE：不改已读、不挪信），按 UIDVALIDITY + UID 记水位：第一次只建基线
  （现在最大的 UID 记下来，之前的一封都不处理），UIDVALIDITY 变了重新建基线。每一封先只取头（From、Authentication-Results、自动回复那几个头，
  最多 64 KB，照 Hermes 的预检）认人，再按是谁取正文：主人取整封（附件按 max_media_mb 收，先放进 spool，处理时存成附件）；联系人、要配对的
  陌生人只取前面一段（附件只记一个占位：名字、类型、大小）；不理的（陌生人默认、自动回复、邮件列表、退信、自己发的）不取正文，只记一行。
  正文取纯文字（只有 HTML 的转成字），字符集按信里写的解，解不了替换（gb2312 / gbk 按 gb18030 解）；回信里引用的上一封（> 开头、「On … wrote:」
  以下）去掉。一次最多取 BATCH 封，剩下的下一圈。IDLE 这一版没做（轮询就够，最少 15 秒一次）。
认人：发件人地址（parseaddr，照 Hermes 防「一个 From 里塞两个地址」）+ Authentication-Results：只认配置的 authserv_id 写的那一行
  （头里第一个 authserv-id 对上的；收信的服务器要把外面带进来的、冒充它的那一行去掉，RFC 8601），dmarc=pass（header.from 对得上），
  或者 dkim=pass 而且 header.d 和发件人的域名对齐，才信这个地址是真的。没配 authserv_id、没有那一行、没过的，一律当陌生人。
  主人：发件人在账号的 owners 里、而且验过；联系人：channel_people 里有这个地址、而且验过；陌生人默认不理、不发配对码（Hermes 的默认），
  dm 配成 pairing 的才回配对码（也只回验过的地址：配对码不发给冒充的地址）。
发：SMTP（starttls / tls / plain），纯文字（Markdown 转成纯文字），回信带 In-Reply-To、References、「Re: 主题」，同一串邮件同一个会话
  （联系人的名片 Agent 按那一串的第一封分会话；主人照样进绑定的那个 Agent 的对话）。回信带 Auto-Submitted: auto-replied（RFC 3834，免得和别人的
  自动回复来回打）。每个地址每小时最多回 replies_per_hour 封（默认 5；主人 60），多了进死信（能看见）。审批：邮件里点不了卡，回信末尾说一句去 app 看。
防回环：Auto-Submitted 不是 no 的、Precedence 是 bulk / list / junk 的、有 List-Id / List-Unsubscribe 的、退信（mailer-daemon、postmaster……）、
  自己发出去的（From 是自己、或者 Message-ID 是自己生成的），一律不回。
解码、防伪造的几段改写自 Hermes（NousResearch/hermes-agent，MIT，Copyright (c) 2025 Nous Research）commit cc6b1f29 的
plugins/platforms/email/adapter.py（_safe_decode、_decode_header_value、_extract_text_body、_strip_html、_extract_email_address、_ar_clauses、
_auth_props、_domains_aligned、_verify_sender_authentication、_is_automated_sender、_close_imap），许可全文见仓库根目录的 THIRD_PARTY_NOTICES。
"""
from __future__ import annotations

import asyncio
import email as email_lib
import hashlib
import imaplib
import logging
import re
import smtplib
import ssl
import time
from contextlib import contextmanager, suppress
from datetime import timezone
from email.header import decode_header
from email.message import EmailMessage, Message
from email.parser import BytesHeaderParser
from email.utils import formataddr, make_msgid, parseaddr, parsedate_to_datetime
from pathlib import Path

from config import settings
from i18n import L

from . import format as fmt
from . import people, store
from .base import Channel, ChannelError, Inbound, Outbound

log = logging.getLogger("mousse.channels")
POLL, POLL_MIN = 60, 15
BATCH = 25                         # 一圈最多取几封
HEADERS_MAX = 64 * 1024            # 预检时头最多取多少字节
PEEK_MAX = 256 * 1024              # 联系人、要配对的陌生人：正文最多取多少字节（附件只记占位）
REPLIES_PER_HOUR, OWNER_REPLIES_PER_HOUR = 5, 60
SMTP_TIMEOUT = 30
NOREPLY = ("noreply", "no-reply", "no_reply", "donotreply", "do-not-reply", "mailer-daemon", "postmaster", "bounce", "notifications@",
           "automated@", "auto-confirm", "auto-reply", "automailer")
AUTOMATED = {"Auto-Submitted": lambda v: v.strip().lower() != "no", "Precedence": lambda v: v.strip().lower() in {"bulk", "list", "junk"},
             "List-Id": lambda v: bool(v.strip()), "List-Unsubscribe": lambda v: bool(v.strip()), "X-Auto-Response-Suppress": lambda v: bool(v.strip())}
PREFETCH = "(RFC822.SIZE BODY.PEEK[HEADER.FIELDS (FROM AUTHENTICATION-RESULTS MESSAGE-ID IN-REPLY-TO REFERENCES SUBJECT DATE TO CC " \
           + " ".join(h.upper() for h in AUTOMATED) + f")]<0.{HEADERS_MAX + 1}>)"
CHARSETS = {"unknown-8bit": "utf-8", "unknown": "utf-8", "x-unknown": "utf-8", "default": "utf-8", "gb2312": "gb18030", "gbk": "gb18030",
            "ks_c_5601-1987": "cp949"}
# HTML → 字：只用不会回头重扫的正则（标签不跨「<」），<script>、<style> 一路往前找。以前的 <[^>]+>、[ \t]+\n、.*?</script>
# 在一长串「<」、空格、没收尾的 <script> 上是平方级：一封几百 KB 的信能卡住整个服务几十秒。
HTML_RAW = re.compile(r"<(script|style)\b", re.I)
HTML_RAW_END = {"script": re.compile(r"</script", re.I), "style": re.compile(r"</style", re.I)}
HTML_BR = re.compile(r"<br\b[^<>]*>", re.I)
HTML_BLOCK = re.compile(r"</?(?:p|div|tr|li|h[1-6]|hr|blockquote|pre|table|ul|ol)\b[^<>]*>", re.I)
HTML_TAG = re.compile(r"<[^<>]*>")
ENTITIES = (("&nbsp;", " "), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'"), ("&amp;", "&"))
QUOTE_HEAD = re.compile(r"^(On .{3,200} wrote:|在 .{2,200} 写道：?|-{2,} ?Original Message ?-{2,}|-{2,} ?原始邮件 ?-{2,}|From: .+ Sent: .+|"
                        r"-{2,} ?Forwarded message ?-{2,}|-{2,} ?转发的邮件 ?-{2,}|Begin forwarded message:|开始转发的邮件[:：]?)\s*$", re.I)
RULE = re.compile(r"^_{10,}$")                                     # Outlook 引用上一封前的那条横线（下面接 From:）
FROM_LINE = re.compile(r"^(From|发件人)\s*[:：]", re.I)
HEAD_LINE = re.compile(r"^(Sent|Date|To|发送时间|时间|日期|收件人)\s*[:：]", re.I)
SINGLE_BRACKET = re.compile(r'([^"<>]*)<([^<>\s]+)>\s*')
COMMENT = re.compile(r"\([^()]*\)")
AUTH_METHOD = re.compile(r"\s*(dmarc|dkim|spf)\s*=\s*([a-z]+)", re.I)
QUOTED = r'"(?:[^"\\]|\\.)*"'
AUTH_PROP = re.compile(rf'(header\.from|header\.d|smtp\.mailfrom|smtp\.from|envelope-from)\s*=\s*((?:{QUOTED}|[^\s";])+)|(?:{QUOTED}|[^\s"])+', re.I)
FROM_MAX = 2048


# —— 解码（改写自 Hermes） ——————————————————————————————————————————————————————————————

def _text(s: str) -> str:
    """单个的代理码位换成「?」：unicode_escape、utf-7 这类字符集解得出来，存不进库（UTF-8），会让同一封信一直卡住这个邮箱。"""
    return s.encode("utf-8", "replace").decode("utf-8")


def safe_decode(payload: bytes, charset: str | None) -> str:
    """按信里写的字符集解，从不抛：认不得的字符集按别名 → UTF-8 → latin-1（errors="replace" 管不到「没有这个编码」）。"""
    label = (charset or "utf-8").strip().strip("\"'").lower() or "utf-8"
    for cand in (CHARSETS.get(label, label), "utf-8"):
        try:
            return _text(payload.decode(cand, errors="replace"))
        except (LookupError, ValueError):
            continue
    return payload.decode("latin-1", errors="replace")


def header_text(raw) -> str:
    """RFC 2047 编码的头 → 一行字：折行、编码进去的换行都并成空格（回信的 Subject 不能带换行）。直接写 8 位字节的头按 UTF-8 解。
    坏了的照原样，从不抛。"""
    try:
        parts = decode_header(raw if hasattr(raw, "_chunks") else str(raw or ""))  # email 把 8 位字节的头给成 Header：直接解它，别先 str()
    except Exception:  # noqa: BLE001
        parts = [(str(raw or ""), None)]
    return " ".join(_text("".join(safe_decode(p, cs) if isinstance(p, bytes) else p for p, cs in parts)).split())


def strip_html(html: str) -> str:
    out, i = [], 0
    while m := HTML_RAW.search(html, i):  # <script>、<style> 连里面的字去掉；没收尾 → 后面的全不要
        out.append(html[i:m.start()])
        end = HTML_RAW_END[m.group(1).lower()].search(html, m.end())
        gt = html.find(">", end.end()) if end else -1
        i = len(html) if gt < 0 else gt + 1
    out.append(html[i:])
    text = HTML_TAG.sub("", HTML_BLOCK.sub("\n", HTML_BR.sub("\n", "".join(out))))
    for ent, ch in ENTITIES:
        text = text.replace(ent, ch)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(ln.rstrip(" \t") for ln in text.split("\n"))).strip()


def _parts(msg: Message):
    """信的各部分，不进 message/*：附上的另一封信（.eml、转发成附件的）是别人的字，不当正文。"""
    stack = [msg]
    while stack:
        part = stack.pop()
        yield part
        if part.is_multipart() and (part is msg or part.get_content_maintype() != "message"):
            stack.extend(reversed(part.get_payload()))


def _first_part(msg: Message, ctype: str) -> str:
    for part in _parts(msg):
        if "attachment" in str(part.get("Content-Disposition", "")).lower() or part.get_content_type() != ctype:
            continue
        payload = part.get_payload(decode=True)
        if payload:
            return safe_decode(payload, part.get_content_charset())
    return ""


def body_text(msg: Message) -> str:
    """正文的纯文字：有 text/plain 用它，只有 HTML 的转成字。"""
    if msg.is_multipart():
        return _first_part(msg, "text/plain") or strip_html(_first_part(msg, "text/html"))
    payload = msg.get_payload(decode=True)
    text = safe_decode(payload, msg.get_content_charset()) if isinstance(payload, bytes) else str(msg.get_payload() or "")
    return strip_html(text) if msg.get_content_type() == "text/html" else text


def split_quoted(text: str) -> tuple[str, str]:
    """自己写的和引用、转发的别人的字分开 → (自己写的, 别人的)。别人的从这几处起：「On … wrote:」（中文客户端的「在 … 写道：」）、
    Outlook 的 Original Message、横线下面接 From: 的那一块、From: 下面接 Sent: / Date: 的那一块、Gmail / Thunderbird / Apple Mail
    的转发；> 开头的行在哪都算别人的。"""
    lines = text.replace("\r\n", "\n").split("\n")
    for i, ln in enumerate(lines):
        s = ln.strip()
        nxt = next((x.strip() for x in lines[i + 1:i + 4] if x.strip()), "")
        if QUOTE_HEAD.match(s) or (RULE.match(s) and FROM_LINE.match(nxt)) or (FROM_LINE.match(s) and HEAD_LINE.match(nxt)):
            own, quoted = lines[:i], lines[i:]
            break
    else:
        own, quoted = lines, []
    inline = [ln for ln in own if ln.lstrip().startswith(">")]
    own = [ln for ln in own if not ln.lstrip().startswith(">")]
    return "\n".join(own).strip(), "\n".join([*inline, *quoted]).strip()


def strip_quoted(text: str) -> str:
    """只留自己写的（见 split_quoted）。"""
    return split_quoted(text)[0]


def address_of(raw) -> str:
    """From 里的地址（小写）。parseaddr 认不出的「Name <a@x>」只在显示名里没有别的地址时才取尖括号里的；太长、括号太多、没有 @ 的 → ""（不处理）。"""
    value = re.sub(r"\r?\n[ \t]+", " ", str(raw or ""))
    if len(value) > FROM_MAX or value.count("(") > 64:
        return ""
    _, addr = parseaddr(value)
    if not addr and (m := SINGLE_BRACKET.fullmatch(value)):
        display, bracketed = _strip_comments(m.group(1)).strip(), m.group(2)
        if "@" in bracketed[1:-1] and not any(c in display for c in ";:()") and ("@" not in display or display.lower() == bracketed.lower()):
            addr = bracketed
    addr = addr.strip().lower()
    return addr if "@" in addr else ""


def _strip_comments(text: str) -> str:
    while (s := COMMENT.sub(" ", text)) != text:
        text = s
    return text


def display_name(raw) -> str:
    name, _ = parseaddr(header_text(raw))
    return name.strip().strip('"')[:80]


# —— 防伪造（改写自 Hermes） ————————————————————————————————————————————————————————————

def _clauses(text: str) -> list[str] | None:
    """Authentication-Results 按 ; 切开（引号、括号里的 ; 不算），括号里的注释去掉。引号、括号没配对 → None。"""
    out, cur, depth, quoted, i = [], [], 0, False, 0
    while i < len(text):
        c = text[i]
        if c == "\\" and (quoted or depth):
            if quoted:
                cur.append(text[i:i + 2])
            i += 2
            continue
        if quoted:
            quoted = c != '"'
            cur.append(c)
        elif depth:
            depth += {"(": 1, ")": -1}.get(c, 0)
            if not depth:
                cur.append(" ")
        elif c == "(":
            depth = 1
        elif c == '"':
            quoted = True
            cur.append(c)
        elif c == ")":
            return None
        elif c == ";":
            out.append("".join(cur))
            cur = []
        else:
            cur.append(c)
        i += 1
    return None if quoted or depth else out + ["".join(cur)]


def _props(clause: str) -> list[tuple[str, str]]:
    return [(p.lower(), v.strip('"')) for p, v in AUTH_PROP.findall(clause) if p]


def _domain(addr: str) -> str:
    return addr.rpartition("@")[2].strip().lower()


def aligned(a: str, b: str) -> bool:
    """宽松对齐（DMARC relaxed）：一样，或者一个是另一个的子域。"""
    a, b = (a or "").strip().lower().rstrip("."), (b or "").strip().lower().rstrip(".")
    return bool(a and b) and (a == b or a.endswith("." + b) or b.endswith("." + a))


def verify(msg: Message, sender: str, authserv_id: str) -> tuple[bool, str]:
    """发件人的域名验过没有：只认 authserv_id 写的那一行 Authentication-Results（头里第一个对上的），dmarc=pass 或者 dkim=pass 且对齐。
    → (过没过, 一句原因)。"""
    dom = _domain(sender)
    if not dom:
        return False, "no From domain"
    if len(msg.get_all("From") or []) > 1:
        return False, "more than one From"
    pin = (authserv_id or "").strip().lower()
    if not pin:
        return False, "authserv_id is not configured"
    for raw in msg.get_all("Authentication-Results") or []:
        clauses = _clauses(" ".join(str(raw).split()))
        if not clauses or clauses[0].strip().lower().split()[0:1] != [pin]:
            continue
        res: dict[str, list] = {"dmarc": [], "dkim": [], "spf": []}
        for c in clauses[1:]:
            if m := AUTH_METHOD.match(c):
                res[m.group(1).lower()].append((m.group(2).lower(), _props(c)))
        if len(res["dmarc"]) > 1:
            return False, "ambiguous dmarc result"
        for verdict, props in res["dmarc"]:
            froms = [_domain(v) if "@" in v else v.lower() for p, v in props if p == "header.from"]
            if verdict == "pass" and froms and all(aligned(d, dom) for d in froms):
                return True, "dmarc=pass"
        for verdict, props in res["dkim"]:
            ds = [v for p, v in props if p == "header.d"]
            if verdict == "pass" and ds and all(aligned(d, dom) for d in ds):
                return True, "dkim=pass aligned"
        return False, "authentication failed"
    return False, f"no Authentication-Results from {pin}"


def automated(sender: str, msg: Message) -> str:
    """自动发的（不回）：→ 原因；不是 → ""。"""
    low = sender.lower()
    if any(p in low for p in NOREPLY):
        return "automated_sender"
    for h, check in AUTOMATED.items():
        v = msg.get(h)
        if v is not None and check(str(v)):
            return f"header:{h.lower()}"
    return ""


def _close(imap: imaplib.IMAP4) -> None:
    """logout 遇到断了的连接会抛 IMAP4.abort（不是 OSError）、跳过关 socket：保证关上（照 Hermes 的 _close_imap）。"""
    try:
        imap.logout()
    except Exception:  # noqa: BLE001
        with suppress(Exception):
            imap.shutdown()


def thread_key(msg: Message) -> str:
    """这一串邮件的键：第一封的 Message-ID（References 的第一个，没有就 In-Reply-To，再没有就自己）的指纹。"""
    refs = str(msg.get("References") or "").split()
    root = refs[0] if refs else str(msg.get("In-Reply-To") or "").strip() or str(msg.get("Message-ID") or "").strip()
    return hashlib.sha1(root.encode()).hexdigest()[:10] if root else ""


def _security(v, default: str) -> str:
    s = str(v or "").strip().lower().replace("-", "").replace("_", "")
    return {"tls": "tls", "ssl": "tls", "starttls": "starttls", "plain": "plain", "none": "plain"}.get(s, default)


class Email(Channel):
    kind = "email"
    label = "Email"
    max_len = 50_000
    buttons = False
    edits = False
    typing_every = 0.0
    formats = ("plain",)
    groups = False

    def __init__(self, acc, hub) -> None:
        super().__init__(acc, hub)
        self.address = str(acc.get("address") or "").strip().lower()
        self.me = {"id": self.address, "username": self.address}
        imap = acc.get("imap") if isinstance(acc.get("imap"), dict) else {}
        smtp = acc.get("smtp") if isinstance(acc.get("smtp"), dict) else {}
        self.imap_cfg, self.smtp_cfg = imap, smtp
        self.folder = str(imap.get("folder") or "INBOX")
        self.spool = settings.data_dir / "channels" / "spool" / f"email-{acc.id}"

    # —— 连 ——
    def _imap_password(self) -> str:
        return self.acc.secret(self.imap_cfg.get("password"))

    def _ctx(self, part: dict) -> ssl.SSLContext:
        return ssl.create_default_context()

    @contextmanager
    def _mailbox(self):
        """登录、只读打开文件夹（EXAMINE）。出错抛 ChannelError（话里不带密码）。"""
        c = self.imap_cfg
        host = str(c.get("host") or "")
        if not host or not self.address:
            raise ChannelError(L("邮箱没配好：imap.host 和 address 都要写", "The mailbox isn't set up: imap.host and address are required"), permanent=True,
                               kind="config")
        sec = _security(c.get("security"), "tls")
        port = int(c.get("port") or (993 if sec == "tls" else 143))
        try:
            if sec == "tls":
                imap = imaplib.IMAP4_SSL(host, port, ssl_context=self._ctx(c), timeout=30)
            else:
                imap = imaplib.IMAP4(host, port, timeout=30)
                if sec == "starttls":
                    imap.starttls(ssl_context=self._ctx(c))
        except (OSError, imaplib.IMAP4.error) as e:
            raise ChannelError(f"IMAP {host}:{port}: {type(e).__name__}: {str(e)[:160]}", kind="network") from None
        try:
            try:
                imap.login(str(c.get("user") or self.address), self._imap_password())
            except imaplib.IMAP4.error as e:
                raise ChannelError(L(f"IMAP 登录没过：{str(e)[:120]}", f"IMAP login failed: {str(e)[:120]}"), permanent=True, kind="auth") from None
            typ, _ = imap.select(self._quote(self.folder), readonly=True)
            if typ != "OK":
                raise ChannelError(L(f"打不开文件夹 {self.folder}", f"Can't open the folder {self.folder}"), permanent=True, kind="config")
            yield imap
        except (OSError, imaplib.IMAP4.error) as e:  # IMAP4.abort 是 IMAP4.error 的子类
            raise ChannelError(f"IMAP: {type(e).__name__}: {str(e)[:160]}", kind="network") from None
        finally:
            _close(imap)

    @staticmethod
    def _quote(folder: str) -> str:
        return folder if re.fullmatch(r"[A-Za-z0-9._/-]+", folder) else '"' + folder.replace("\\", "\\\\").replace('"', '\\"') + '"'

    # —— 收 ——
    async def run(self) -> None:
        backoff = 5.0
        poll = max(POLL_MIN, float(self.acc.get("poll") or POLL))
        while True:
            try:
                items, state = await asyncio.to_thread(self.fetch_new)
                if items or state:
                    self.hub.ingest(items, state)
                if self.hub.status.get("state") != "ok":
                    self.hub.note("ok", L(f"在收 {self.address} 的信", f"Reading {self.address}"))
                backoff = 5.0
                await asyncio.sleep(poll)
            except ChannelError as e:
                if e.kind in ("auth", "config"):
                    self.hub.note("auth" if e.kind == "auth" else "error", str(e), error=str(e))
                    await asyncio.sleep(600)
                else:
                    self.hub.note("error", L("连不上邮箱，过一会儿再试", "Can't reach the mailbox; retrying"), error=str(e))
                    await asyncio.sleep(min(backoff, 300))
                    backoff *= 2
            self._prune_spool()

    def fetch_new(self) -> tuple[list[Inbound], dict]:
        """一圈（在线程里跑）：→ (新进来的, 要写的水位)。第一次 / UIDVALIDITY 变了只建基线。"""
        kind, account = self.acc.kind, self.acc.id
        with self._mailbox() as imap:
            uv = str((imap.response("UIDVALIDITY")[1] or [b""])[0] or b"", "ascii", "replace").strip()
            last_uv, last_uid = store.state(kind, account, "uidvalidity"), int(store.state(kind, account, "uid") or 0)
            if not uv:
                raise ChannelError("IMAP: no UIDVALIDITY", kind="network")
            if uv != last_uv:
                typ, data = imap.uid("SEARCH", None, "ALL")
                top = max((int(x) for x in (data[0] or b"").split()), default=0) if typ == "OK" and data else 0
                log.info("email %s: baseline at UID %s (UIDVALIDITY %s)", account, top, uv)
                return [], {"uidvalidity": uv, "uid": str(top), "baseline_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
            typ, data = imap.uid("SEARCH", None, f"UID {last_uid + 1}:*")
            uids = sorted(int(x) for x in (data[0] or b"").split() if int(x) > last_uid) if typ == "OK" and data else []
            items: list[Inbound] = []
            top = last_uid
            for uid in uids[:BATCH]:
                try:
                    ib = self._one(imap, uv, uid)
                except ChannelError as e:
                    if e.kind != "fetch":
                        raise  # 连接断了：这一批都不算，水位不挪，下一圈再来
                    log.warning("email %s: UID %s: %s", account, uid, e)
                    ib = Inbound(channel=kind, account=account, ext_id=f"{uv}:{uid}", skip="fetch_failed")
                except (OSError, imaplib.IMAP4.error):
                    raise  # 连接断了：同上
                except Exception as e:  # noqa: BLE001 — 这一封读不了（坏的日期、嵌套太深……）：跳过它，别让一封信卡住整个邮箱
                    log.warning("email %s: UID %s unreadable: %s", account, uid, type(e).__name__)
                    ib = Inbound(channel=kind, account=account, ext_id=f"{uv}:{uid}", skip="unreadable")
                if ib is not None:
                    items.append(ib)
                top = uid
            return items, ({"uidvalidity": uv, "uid": str(top)} if top != last_uid else {})

    def _fetch(self, imap: imaplib.IMAP4, uid: int, what: str) -> tuple[bytes, bytes]:
        """UID FETCH 一项 → (描述：内容以外的那几段连起来, 内容)。RFC822.SIZE 这类在内容前在内容后都有可能（看服务器）。
        服务器说这一封取不了（NO / BAD）→ ChannelError kind fetch（这一封跳过，别的照收）。"""
        typ, data = imap.uid("FETCH", str(uid), what)
        if typ != "OK":
            raise ChannelError(f"IMAP FETCH {uid}: {typ}", kind="fetch")
        desc, body = b"", None
        for item in data or []:
            if isinstance(item, tuple) and len(item) >= 2:
                desc += bytes(item[0] or b"") + b" "
                if body is None:
                    body = bytes(item[1] or b"")
            elif isinstance(item, (bytes, bytearray)):
                desc += bytes(item) + b" "
        return desc, body or b""

    def _one(self, imap: imaplib.IMAP4, uv: str, uid: int) -> Inbound | None:
        """一封：先看头认人，再按是谁取正文。"""
        acc = self.acc
        desc, raw_head = self._fetch(imap, uid, PREFETCH)
        size_m = re.search(rb"RFC822\.SIZE (\d+)", desc)
        size = int(size_m.group(1)) if size_m else 0
        head = BytesHeaderParser().parsebytes(raw_head[:HEADERS_MAX])
        sender = address_of(head.get("From"))
        base = {"channel": acc.kind, "account": acc.id, "ext_id": f"{uv}:{uid}", "sender": sender, "chat": sender}
        if not sender or len(raw_head) > HEADERS_MAX:
            return Inbound(**base, skip="bad_from")
        mid = str(head.get("Message-ID") or "").strip()
        if sender == self.address or (mid and store.sent_id(acc.kind, acc.id, mid)):
            return Inbound(**base, skip="self")
        if why := automated(sender, head):
            return Inbound(**base, skip=why)
        ok, reason = verify(head, sender, str(acc.get("authserv_id") or ""))
        p = people.person(acc.kind, sender)
        owner = ok and people.is_owner(acc, sender)
        contact = ok and p is not None and p["role"] == "contact" and p["status"] == "active"
        listed = ok and sender in acc.allow_from()
        pair = ok and acc.dm == "pairing"
        if not (owner or contact or listed or pair):
            return Inbound(**base, skip="unverified" if not ok else "stranger", auth={"verified": ok, "reason": reason},
                           meta={"subject": header_text(head.get("Subject"))[:200]})
        cap = int(acc.get("max_media_mb") or 20) * 1024 * 1024
        if owner and size <= cap + PEEK_MAX:
            _, raw = self._fetch(imap, uid, "(BODY.PEEK[])")
        else:
            _, raw = self._fetch(imap, uid, f"(BODY.PEEK[]<0.{PEEK_MAX}>)")
        msg = email_lib.message_from_bytes(raw)
        text, quoted = split_quoted(body_text(msg))
        media = self._attachments(msg, f"{uv}-{uid}", keep=owner and size <= cap + PEEK_MAX, cap=cap)
        if owner and size > cap + PEEK_MAX:
            media.append({"kind": "file", "name": L("（附件太大，没收）", "(attachments too large; not taken)"), "size": size, "ref": ""})
        refs = str(msg.get("References") or "").split()
        subject = header_text(msg.get("Subject"))[:300]
        meta = {"name": display_name(msg.get("From")), "subject": subject, "references": refs[-20:], "message_id": mid,
                "to": header_text(msg.get("To"))[:300], "cc": header_text(msg.get("Cc"))[:300]}
        try:
            ts = parsedate_to_datetime(str(msg.get("Date"))).astimezone(timezone.utc).isoformat(timespec="seconds")
        except Exception:  # noqa: BLE001 — 9999 年、-2359 这种日期会 OverflowError
            ts = ""
        if quoted and not text.strip():  # 只有转发、引用的：正文是别人的字（主人回合里正文换成一句说明，见 owner.prepare）
            meta["forwarded"], text = quoted[:4000], ""
        elif quoted:
            meta["quote"] = quoted[:4000]  # 只当资料（owner.envelope）
        elif not text.strip() and not media and subject:
            text = subject
        return Inbound(**base, chat_type="dm", topic=thread_key(msg), message_id=mid, reply_to=str(msg.get("In-Reply-To") or "").strip(), ts=ts,
                       text=text[:20000], media=media, meta={k: v for k, v in meta.items() if v}, auth={"verified": ok, "reason": reason})

    def _attachments(self, msg: Message, key: str, *, keep: bool, cap: int) -> list[dict]:
        """附件：主人的存进 spool（处理时 fetch 读回来、存成附件），别人的只记占位。"""
        out: list[dict] = []
        if not msg.is_multipart():
            return out
        total = 0
        for n, part in enumerate(msg.walk()):
            disp = str(part.get("Content-Disposition", "")).lower()
            ctype = part.get_content_type()
            if "attachment" not in disp and ("inline" not in disp or ctype in ("text/plain", "text/html")):
                continue
            name = header_text(part.get_filename() or f"attachment-{n}")[:120] or f"attachment-{n}"
            payload = part.get_payload(decode=True)
            size = len(payload) if isinstance(payload, bytes) else 0
            kind = "image" if ctype.startswith("image/") else "audio" if ctype.startswith("audio/") else "video" if ctype.startswith("video/") else "file"
            item = {"kind": kind, "name": name, "mime": ctype, "size": size, "ref": ""}
            if keep and isinstance(payload, bytes) and payload and total + size <= cap:
                total += size
                self.spool.mkdir(parents=True, exist_ok=True)
                path = self.spool / f"{key}-{n}-{re.sub(r'[^A-Za-z0-9._-]', '_', name)[:60]}"
                path.write_bytes(payload)
                path.chmod(0o600)
                item["ref"] = str(path)
            out.append(item)
        return out

    async def fetch(self, item: dict) -> bytes:
        ref = str(item.get("ref") or "")
        if not ref:
            raise ChannelError(L("附件没收（太大，或者不是主人发的）", "The attachment wasn't taken (too large, or not from the owner)"), permanent=True)
        p = Path(ref)
        if not p.is_file() or self.spool.resolve() not in p.resolve().parents:
            raise ChannelError(L("附件不在了", "The attachment is gone"), permanent=True)
        return p.read_bytes()

    def _prune_spool(self) -> None:
        """spool 里两天以前的附件清掉（处理完的、没人要的）。"""
        try:
            cutoff = time.time() - 2 * 86400
            for f in self.spool.glob("*"):
                if f.stat().st_mtime < cutoff:
                    f.unlink(missing_ok=True)
        except OSError:
            pass

    # —— 发 ——
    def reply_ref(self, ib: Inbound) -> str:
        return ib.message_id

    def reply_extra(self, ib: Inbound) -> dict:
        subject = str(ib.meta.get("subject") or "").strip() or L(f"来自 {settings.app_name}", f"From {settings.app_name}")
        if not re.match(r"^\s*(re|回复|答复)\s*[:：]", subject, re.I):
            subject = f"Re: {subject}"
        refs = [*[str(x) for x in ib.meta.get("references") or []], *([ib.message_id] if ib.message_id else [])]
        return {"subject": subject[:300], "references": refs[-20:]}

    def _smtp(self) -> smtplib.SMTP:
        c = {**self.imap_cfg, **self.smtp_cfg} if self.smtp_cfg else self.imap_cfg
        host = str(self.smtp_cfg.get("host") or "")
        if not host:
            raise ChannelError(L("没配 smtp.host", "smtp.host isn't set"), permanent=True, kind="config")
        port = int(self.smtp_cfg.get("port") or 587)
        sec = _security(self.smtp_cfg.get("security"), "tls" if port == 465 else "starttls")
        try:
            if sec == "tls":
                s = smtplib.SMTP_SSL(host, port, context=self._ctx(c), timeout=SMTP_TIMEOUT)
            else:
                s = smtplib.SMTP(host, port, timeout=SMTP_TIMEOUT)
                if sec == "starttls":
                    s.starttls(context=self._ctx(c))
        except (OSError, smtplib.SMTPException) as e:
            raise ChannelError(f"SMTP {host}:{port}: {type(e).__name__}: {str(e)[:160]}", kind="network") from None
        user = str(self.smtp_cfg.get("user") or self.imap_cfg.get("user") or self.address)
        password = self.acc.secret(self.smtp_cfg.get("password") or self.imap_cfg.get("password"))
        try:
            if password:
                s.login(user, password)
        except smtplib.SMTPAuthenticationError as e:
            with suppress(Exception):
                s.quit()
            raise ChannelError(L(f"SMTP 登录没过：{e.smtp_code}", f"SMTP login failed: {e.smtp_code}"), permanent=True, kind="auth") from None
        except (OSError, smtplib.SMTPException) as e:
            with suppress(Exception):
                s.close()
            raise ChannelError(f"SMTP: {type(e).__name__}: {str(e)[:160]}", kind="network") from None
        return s

    def build(self, out: Outbound) -> EmailMessage:
        msg = EmailMessage()
        msg["From"] = formataddr((str(self.acc.get("name") or settings.app_name), self.address))
        msg["To"] = out.chat
        msg["Subject"] = str(out.extra.get("subject") or L(f"来自 {settings.app_name}", f"From {settings.app_name}"))
        if out.reply_to:
            msg["In-Reply-To"] = out.reply_to
            refs = [str(x) for x in out.extra.get("references") or []]
            if out.reply_to not in refs:
                refs.append(out.reply_to)
            msg["References"] = " ".join(refs[-20:])
        msg["Message-ID"] = make_msgid("mousse", domain=self.address.rpartition("@")[2] or "localhost")
        msg["Auto-Submitted"] = "auto-replied" if out.reply_to else "auto-generated"
        text = fmt.plain(out.text, bullet="- ") if out.format == "markdown" else out.text
        msg.set_content(text)
        return msg

    async def send(self, out: Outbound) -> list[str]:
        mine = people.is_owner(self.acc, out.chat)
        cap = OWNER_REPLIES_PER_HOUR if mine else int(self.acc.get("replies_per_hour") or REPLIES_PER_HOUR)
        recent = [r for r in store.outbox(self.acc.kind, self.acc.id, "sent", 200) if r["chat"] == out.chat and r["t"] >= time.time() - 3600]
        if len(recent) >= cap:
            raise ChannelError(L(f"一小时最多给 {out.chat} 回 {cap} 封，这封没发",
                                 f"At most {cap} email{'s' if cap != 1 else ''} an hour to {out.chat}; this one wasn't sent"), permanent=True, kind="limit")
        try:
            msg = self.build(out)
        except ValueError as e:  # 头里有换行这类：重试也一样，别挡着这个聊天后面的回信
            raise ChannelError(f"email: {str(e)[:160]}", permanent=True, kind="bad") from None

        def go() -> None:
            s = self._smtp()
            try:
                s.send_message(msg)
            except smtplib.SMTPRecipientsRefused as e:
                raise ChannelError(f"SMTP refused {out.chat}: {str(e)[:160]}", permanent=True, kind="bad") from None
            except (OSError, smtplib.SMTPException) as e:
                raise ChannelError(f"SMTP: {type(e).__name__}: {str(e)[:160]}", kind="network") from None
            finally:
                with suppress(Exception):
                    s.quit()
        await asyncio.to_thread(go)
        return [str(msg["Message-ID"])]  # 出去的队列把它记进 channel_sent：抄送、退回来的那封认得出是自己发的，不再回

    async def test(self) -> dict:
        def go() -> dict:
            with self._mailbox() as imap:
                uv = str((imap.response("UIDVALIDITY")[1] or [b""])[0] or b"", "ascii", "replace")
            s = self._smtp()
            with suppress(Exception):
                s.quit()
            return {"detail": L(f"IMAP（{self.folder}）和 SMTP 都登录上了", f"Logged in to IMAP ({self.folder}) and SMTP"), "uidvalidity": uv}
        out = await asyncio.to_thread(go)
        if not str(self.acc.get("authserv_id") or "").strip():
            out["warning"] = L("没配 authserv_id：谁的信都验不过，主人的信也会被当成陌生人（不理）",
                               "authserv_id isn't set: no sender can be verified, so even the owner's mail is treated as a stranger's (ignored)")
        return out
