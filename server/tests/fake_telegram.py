"""假的 Telegram Bot API：本机一个 HTTP 服务（照 evals/fakemodel.py 的写法：后台线程、剧本、记下每个请求），不连网、不用真令牌。

  POST /bot<令牌>/<方法>          getMe、getUpdates、sendMessage、editMessageText、editMessageReplyMarkup、sendChatAction、answerCallbackQuery、
                                 setMyCommands、getFile、getWebhookInfo、deleteWebhook；令牌不对回 401
  GET  /file/bot<令牌>/<路径>      getFile 给的路径下载文件
剧本：
  push(*updates)                  排进去几个 update（没写 update_id 的按顺序编号）；getUpdates 只回 update_id >= offset 的（Telegram 也是这样：
                                  offset 之前的算确认过、不再给）
  fail(method, code, description, times=1, retry_after=None)   接下来 times 次这个方法回这个错（409、429、400 can't parse entities……）
  reject_html = True              带 parse_mode 的 sendMessage 一律回 400 can't parse entities（测退回纯文字）
  files[file_id] = bytes          getFile / 下载用
记下的：requests = [(方法, 请求体)]；sent = sendMessage 发出去的（请求体）；of(方法) 取某一种。
getUpdates 没有新的时等一小会儿（最多 timeout 秒，最多 wait 秒）再回空的，像真的长轮询，测试别等太久。
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeTelegram:
    def __init__(self, token: str = "1234567:TEST-token-for-the-fake-bot-0001", *, bot_id: int = 7000001, username: str = "mousse_test_bot",
                 wait: float = 0.05) -> None:
        self.token = token
        self.bot = {"id": bot_id, "is_bot": True, "first_name": "Mousse Test", "username": username}
        self.wait = wait
        self.updates: list[dict] = []
        self.requests: list[tuple[str, dict]] = []
        self.failures: dict[str, list[dict]] = {}
        self.files: dict[str, bytes] = {}
        self.reject_html = False
        self.webhook = ""
        self._next_update = 1
        self._next_message = 500
        self._lock = threading.Lock()
        self._httpd: ThreadingHTTPServer | None = None

    # —— 剧本 ——
    def push(self, *updates: dict) -> list[int]:
        ids = []
        with self._lock:
            for u in updates:
                u = dict(u)
                if "update_id" not in u:
                    u["update_id"] = self._next_update
                self._next_update = max(self._next_update, u["update_id"]) + 1
                self.updates.append(u)
                ids.append(u["update_id"])
        return ids

    def fail(self, method: str, code: int, description: str, times: int = 1, retry_after: float | None = None) -> None:
        with self._lock:
            self.failures.setdefault(method, []).extend([{"code": code, "description": description, "retry_after": retry_after}] * times)

    def of(self, method: str) -> list[dict]:
        with self._lock:
            return [b for m, b in self.requests if m == method]

    @property
    def sent(self) -> list[dict]:
        return self.of("sendMessage")

    # —— 起停 ——
    def __enter__(self) -> FakeTelegram:
        return self.start()

    def __exit__(self, *_exc) -> None:
        self.stop()

    def start(self) -> FakeTelegram:
        self._httpd = _Server(("127.0.0.1", 0), _handler(self))
        self._httpd.daemon_threads = True
        threading.Thread(target=self._httpd.serve_forever, kwargs={"poll_interval": 0.05}, name="fake-telegram", daemon=True).start()
        return self

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None

    @property
    def url(self) -> str:
        assert self._httpd is not None
        host, port = self._httpd.server_address[:2]
        return f"http://{host}:{port}"

    # —— 方法 ——
    def handle(self, method: str, body: dict) -> tuple[int, dict]:
        with self._lock:
            self.requests.append((method, body))
            queue = self.failures.get(method)
            if queue:
                f = queue.pop(0)
                out = {"ok": False, "error_code": f["code"], "description": f["description"]}
                if f["retry_after"] is not None:
                    out["parameters"] = {"retry_after": f["retry_after"]}
                return f["code"], out
        if method == "getMe":
            return 200, {"ok": True, "result": self.bot}
        if method == "getUpdates":
            offset = int(body.get("offset") or 0)
            deadline = time.monotonic() + min(float(body.get("timeout") or 0), self.wait)
            while True:
                with self._lock:
                    got = [u for u in self.updates if u["update_id"] >= offset][: int(body.get("limit") or 100)]
                if got or time.monotonic() >= deadline:
                    return 200, {"ok": True, "result": got}
                time.sleep(0.01)
        if method == "sendMessage":
            if self.reject_html and body.get("parse_mode"):
                return 400, {"ok": False, "error_code": 400, "description": "Bad Request: can't parse entities: Unsupported start tag \"x\" at byte offset 0"}
            with self._lock:
                self._next_message += 1
                mid = self._next_message
            return 200, {"ok": True, "result": {"message_id": mid, "chat": {"id": body.get("chat_id")}, "date": int(time.time()), "text": body.get("text")}}
        if method in ("editMessageText", "editMessageReplyMarkup"):
            return 200, {"ok": True, "result": {"message_id": body.get("message_id"), "chat": {"id": body.get("chat_id")}}}
        if method in ("sendChatAction", "answerCallbackQuery", "setMyCommands", "deleteWebhook"):
            return 200, {"ok": True, "result": True}
        if method == "getWebhookInfo":
            return 200, {"ok": True, "result": {"url": self.webhook, "pending_update_count": 0}}
        if method == "getFile":
            fid = str(body.get("file_id") or "")
            if fid not in self.files:
                return 400, {"ok": False, "error_code": 400, "description": "Bad Request: invalid file_id"}
            return 200, {"ok": True, "result": {"file_id": fid, "file_size": len(self.files[fid]), "file_path": f"files/{fid}"}}
        return 404, {"ok": False, "error_code": 404, "description": "Not Found: method not found"}


class _Server(ThreadingHTTPServer):
    def handle_error(self, request, client_address) -> None:  # noqa: ARG002 — 客户端先断开（停的时候掐掉长轮询）：不打一堆栈
        pass


def _handler(fake: FakeTelegram) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_a) -> None:
            pass

        def _send(self, status: int, payload: dict | bytes, ctype: str = "application/json") -> None:
            data = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self) -> None:
            raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            parts = self.path.split("?", 1)[0].strip("/").split("/")
            if len(parts) != 2 or not parts[0].startswith("bot"):
                self._send(404, {"ok": False, "error_code": 404, "description": "Not Found"})
                return
            if parts[0][3:] != fake.token:
                self._send(401, {"ok": False, "error_code": 401, "description": "Unauthorized"})
                return
            try:
                body = json.loads(raw or b"{}")
            except ValueError:
                body = {}
            status, out = fake.handle(parts[1], body if isinstance(body, dict) else {})
            self._send(status, out)

        def do_GET(self) -> None:
            parts = self.path.split("?", 1)[0].strip("/").split("/", 2)
            if len(parts) == 3 and parts[0] == "file" and parts[1] == f"bot{fake.token}":
                fid = parts[2].split("/", 1)[-1]
                if fid in fake.files:
                    self._send(200, fake.files[fid], "application/octet-stream")
                    return
            self._send(404, {"ok": False, "error_code": 404, "description": "Not Found"})

    return Handler
