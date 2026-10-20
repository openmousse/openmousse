"""假的邮箱：一个最小的 IMAP 服务（imaplib 真连得上：CAPABILITY、LOGIN、SELECT / EXAMINE、UID SEARCH、UID FETCH、NOOP、LOGOUT）和一个
SMTP 收件桶（EHLO、AUTH PLAIN / LOGIN、MAIL、RCPT、DATA、RSET、NOOP、QUIT）。都在本机、明文（测试里 security 写 plain），不连网。

FakeIMAP
  add(raw_bytes) → UID          往文件夹里放一封（UID 按顺序编）
  uidvalidity                   改它 = 文件夹重建过（客户端要重新建基线）
  commands                      收到的每一条命令（看客户端是不是只读打开、取了哪几封的正文）
  UID SEARCH UID n:* 照真服务器的样子：n 比最大的 UID 还大时也回最大的那一个（客户端要自己滤掉）
FakeSMTP
  messages                      收到的每一封：{"from", "to", "data", "msg"（email.message.Message）}
  refuse                        这些收件人回 550
"""
from __future__ import annotations

import base64
import email
import re
import socketserver
import threading


def _tokens(line: str) -> list[str]:
    """一行 IMAP 命令按空格切开，引号里的算一个（去掉引号、反斜杠转义）。"""
    out, cur, i, quoted = [], "", 0, False
    while i < len(line):
        c = line[i]
        if quoted:
            if c == "\\" and i + 1 < len(line):
                cur += line[i + 1]
                i += 2
                continue
            if c == '"':
                quoted = False
                out.append(cur)
                cur = ""
            else:
                cur += c
        elif c == '"':
            quoted = True
        elif c == " ":
            if cur:
                out.append(cur)
                cur = ""
        else:
            cur += c
        i += 1
    if cur:
        out.append(cur)
    return out


def _header_fields(raw: bytes, names: list[str]) -> bytes:
    """一封信的头里只留这几个字段（连着折行），后面一个空行（照 RFC 3501 的 HEADER.FIELDS）。"""
    head = raw.split(b"\r\n\r\n", 1)[0] if b"\r\n\r\n" in raw else raw.split(b"\n\n", 1)[0]
    want = {n.lower() for n in names}
    out, keep = [], False
    for line in re.split(rb"\r?\n", head):
        if line[:1] in (b" ", b"\t"):
            if keep:
                out.append(line)
            continue
        name = line.split(b":", 1)[0].decode("latin-1").strip().lower()
        keep = name in want
        if keep:
            out.append(line)
    return b"\r\n".join(out) + b"\r\n\r\n"


class _TCP(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def handle_error(self, request, client_address) -> None:  # noqa: ARG002 — 客户端断开：不打栈
        pass


class FakeIMAP:
    def __init__(self, user: str = "agent@example.org", password: str = "imap-secret", folder: str = "INBOX") -> None:
        self.user, self.password, self.folder = user, password, folder
        self.uidvalidity = 1001
        self.messages: list[tuple[int, bytes]] = []
        self.commands: list[str] = []
        self._next = 1
        self._lock = threading.Lock()
        self._srv: _TCP | None = None

    def add(self, raw: bytes) -> int:
        raw = raw.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
        with self._lock:
            uid = self._next
            self._next += 1
            self.messages.append((uid, raw))
        return uid

    def start(self) -> FakeIMAP:
        self._srv = _TCP(("127.0.0.1", 0), _imap_handler(self))
        threading.Thread(target=self._srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True, name="fake-imap").start()
        return self

    def stop(self) -> None:
        if self._srv is not None:
            self._srv.shutdown()
            self._srv.server_close()
            self._srv = None

    @property
    def port(self) -> int:
        assert self._srv is not None
        return self._srv.server_address[1]

    def fetched_bodies(self) -> list[int]:
        """取过正文（不只是头）的 UID。"""
        out = []
        for c in self.commands:
            m = re.match(r"\S+ UID FETCH (\d+) \((?:BODY\.PEEK\[\]|RFC822\))", c, re.I)
            if m:
                out.append(int(m.group(1)))
        return out


def _imap_handler(box: FakeIMAP):
    class H(socketserver.StreamRequestHandler):
        def w(self, s: str | bytes) -> None:
            self.wfile.write(s.encode() if isinstance(s, str) else s)

        def handle(self) -> None:
            self.w("* OK [CAPABILITY IMAP4rev1 AUTH=PLAIN] FakeIMAP ready\r\n")
            selected = False
            while True:
                line = self.rfile.readline()
                if not line:
                    return
                text = line.decode("utf8", "replace").rstrip("\r\n")
                with box._lock:
                    box.commands.append(text)
                parts = _tokens(text)
                if len(parts) < 2:
                    continue
                tag, cmd, args = parts[0], parts[1].upper(), parts[2:]
                if cmd == "CAPABILITY":
                    self.w(f"* CAPABILITY IMAP4rev1 AUTH=PLAIN\r\n{tag} OK CAPABILITY completed\r\n")
                elif cmd == "LOGIN":
                    if len(args) >= 2 and args[0] == box.user and args[1] == box.password:
                        self.w(f"{tag} OK LOGIN completed\r\n")
                    else:
                        self.w(f"{tag} NO [AUTHENTICATIONFAILED] Invalid credentials\r\n")
                elif cmd in ("SELECT", "EXAMINE"):
                    if not args or args[0] != box.folder:
                        self.w(f"{tag} NO no such mailbox\r\n")
                        continue
                    selected = True
                    with box._lock:
                        n, nxt, uv = len(box.messages), box._next, box.uidvalidity
                    ro = "READ-ONLY" if cmd == "EXAMINE" else "READ-WRITE"
                    self.w(f"* {n} EXISTS\r\n* 0 RECENT\r\n* OK [UIDVALIDITY {uv}] UIDs valid\r\n* OK [UIDNEXT {nxt}] next\r\n"
                           f"{tag} OK [{ro}] {cmd} completed\r\n")
                elif cmd == "UID" and args and selected:
                    self.uid(tag, args[0].upper(), args[1:], text)
                elif cmd == "NOOP":
                    self.w(f"{tag} OK NOOP completed\r\n")
                elif cmd == "LOGOUT":
                    self.w(f"* BYE logging out\r\n{tag} OK LOGOUT completed\r\n")
                    return
                else:
                    self.w(f"{tag} BAD unknown command\r\n")

        def uid(self, tag: str, sub: str, args: list[str], text: str) -> None:
            with box._lock:
                msgs = list(box.messages)
            if sub == "SEARCH":
                crit = " ".join(args).upper()
                uids = [u for u, _ in msgs]
                m = re.search(r"UID (\d+):\*", crit)
                if m:
                    lo = int(m.group(1))
                    hit = [u for u in uids if u >= lo]
                    if not hit and uids:
                        hit = [max(uids)]  # 真服务器的样子：n:* 里 * 是最大的那个，n 比它大也会回它
                    uids = hit
                self.w(f"* SEARCH {' '.join(map(str, uids))}\r\n{tag} OK SEARCH completed\r\n".replace("SEARCH \r\n", "SEARCH\r\n"))
                return
            if sub == "FETCH" and args:
                uid = int(args[0])
                found = [(i, raw) for i, (u, raw) in enumerate(msgs, start=1) if u == uid]
                if not found:
                    self.w(f"{tag} OK FETCH completed\r\n")
                    return
                seq, raw = found[0]
                spec = text.split(args[0], 1)[1].strip().upper()
                items = [f"UID {uid}"]
                literal: bytes | None = None
                key = ""
                if "RFC822.SIZE" in spec:
                    items.append(f"RFC822.SIZE {len(raw)}")
                hm = re.search(r"BODY\.PEEK\[HEADER\.FIELDS \(([^)]*)\)\](?:<(\d+)\.(\d+)>)?", spec)
                bm = re.search(r"(BODY\.PEEK\[\]|RFC822)(?:<(\d+)\.(\d+)>)?(?![.\w])", spec)
                if hm:
                    literal = _header_fields(raw, hm.group(1).split())
                    if hm.group(2):
                        literal = literal[int(hm.group(2)): int(hm.group(2)) + int(hm.group(3))]
                    key = f"BODY[HEADER.FIELDS ({hm.group(1)})]" + (f"<{hm.group(2)}>" if hm.group(2) else "")
                elif bm:
                    literal = raw
                    if bm.group(2):
                        literal = raw[int(bm.group(2)): int(bm.group(2)) + int(bm.group(3))]
                    key = "BODY[]" + (f"<{bm.group(2)}>" if bm.group(2) else "") if bm.group(1).startswith("BODY") else "RFC822"
                head = f"* {seq} FETCH ({' '.join(items)}"
                if literal is not None:
                    self.w(f"{head} {key} {{{len(literal)}}}\r\n".encode() + literal + b")\r\n")
                else:
                    self.w(head + ")\r\n")
                self.w(f"{tag} OK FETCH completed\r\n")
                return
            self.w(f"{tag} BAD unsupported UID command\r\n")
    return H


class FakeSMTP:
    def __init__(self, user: str = "agent@example.org", password: str = "imap-secret") -> None:
        self.user, self.password = user, password
        self.messages: list[dict] = []
        self.refuse: set[str] = set()
        self.logins: list[str] = []
        self._lock = threading.Lock()
        self._srv: _TCP | None = None

    def start(self) -> FakeSMTP:
        self._srv = _TCP(("127.0.0.1", 0), _smtp_handler(self))
        threading.Thread(target=self._srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True, name="fake-smtp").start()
        return self

    def stop(self) -> None:
        if self._srv is not None:
            self._srv.shutdown()
            self._srv.server_close()
            self._srv = None

    @property
    def port(self) -> int:
        assert self._srv is not None
        return self._srv.server_address[1]

    def to(self, addr: str) -> list[dict]:
        with self._lock:
            return [m for m in self.messages if addr in m["to"]]


def _smtp_handler(sink: FakeSMTP):
    class H(socketserver.StreamRequestHandler):
        def w(self, s: str) -> None:
            self.wfile.write((s + "\r\n").encode())

        def handle(self) -> None:
            self.w("220 fake.smtp ESMTP ready")
            frm, rcpt, authed = "", [], False
            while True:
                line = self.rfile.readline()
                if not line:
                    return
                text = line.decode("utf8", "replace").rstrip("\r\n")
                up = text.upper()
                if up.startswith(("EHLO", "HELO")):
                    self.wfile.write(b"250-fake.smtp\r\n250-AUTH PLAIN LOGIN\r\n250 8BITMIME\r\n")
                elif up.startswith("AUTH PLAIN"):
                    rest = text[10:].strip()
                    if not rest:
                        self.w("334 ")
                        rest = self.rfile.readline().decode().strip()
                    parts = base64.b64decode(rest).split(b"\0")
                    ok = len(parts) == 3 and parts[1].decode() == sink.user and parts[2].decode() == sink.password
                    authed = ok
                    sink.logins.append(parts[1].decode() if len(parts) == 3 else "?")
                    self.w("235 2.7.0 Authentication successful" if ok else "535 5.7.8 Authentication credentials invalid")
                elif up.startswith("AUTH LOGIN"):
                    self.w("334 VXNlcm5hbWU6")
                    user = base64.b64decode(self.rfile.readline().strip()).decode()
                    self.w("334 UGFzc3dvcmQ6")
                    pw = base64.b64decode(self.rfile.readline().strip()).decode()
                    authed = user == sink.user and pw == sink.password
                    sink.logins.append(user)
                    self.w("235 2.7.0 Authentication successful" if authed else "535 5.7.8 Authentication credentials invalid")
                elif up.startswith("MAIL FROM:"):
                    if not authed:
                        self.w("530 5.7.0 Authentication required")
                        continue
                    frm, rcpt = re.sub(r"^MAIL FROM:\s*<?([^>\s]*)>?.*$", r"\1", text, flags=re.I), []
                    self.w("250 2.1.0 OK")
                elif up.startswith("RCPT TO:"):
                    addr = re.sub(r"^RCPT TO:\s*<?([^>\s]*)>?.*$", r"\1", text, flags=re.I)
                    if addr.lower() in sink.refuse:
                        self.w("550 5.1.1 No such user")
                    else:
                        rcpt.append(addr)
                        self.w("250 2.1.5 OK")
                elif up == "DATA":
                    self.w("354 End data with <CR><LF>.<CR><LF>")
                    chunks = []
                    while True:
                        ln = self.rfile.readline()
                        if ln in (b".\r\n", b".\n", b""):
                            break
                        chunks.append(ln[1:] if ln.startswith(b"..") else ln)
                    data = b"".join(chunks)
                    with sink._lock:
                        sink.messages.append({"from": frm, "to": list(rcpt), "data": data, "msg": email.message_from_bytes(data)})
                    self.w("250 2.0.0 OK queued")
                elif up in ("RSET", "NOOP"):
                    self.w("250 2.0.0 OK")
                elif up == "QUIT":
                    self.w("221 2.0.0 Bye")
                    return
                else:
                    self.w("502 5.5.2 Command not recognized")
    return H
