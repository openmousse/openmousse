"""假的媒体服务商（第 3 批，2026-10-19）：本机一个 HTTP 服务，冒充看图、看文档、看视频、转写、朗读、生图那几家的接口，不连网、不花钱
（engine/media.py、vision.py、pdf.py……的测试用）。各家的说法：
  Google   POST /v1beta/models/<型号>:generateContent（x-goog-api-key）：看 parts 里有什么回一句（图 → FAKE-VISION、PDF → FAKE-PDF、
           视频 → FAKE-VIDEO、音频 → FAKE-AUDIO，后面跟着问的话的最后 120 字），usageMetadata 照 USAGE 回
           POST /upload/v1beta/files（Files API：start → x-goog-upload-url；再 upload, finalize → file，PROCESSING）、
           GET /v1beta/files/<id>（第一次问还在 PROCESSING，再问 ACTIVE）、DELETE /v1beta/files/<id>
  OpenAI / Groq  POST /v1/audio/transcriptions、/openai/v1/audio/transcriptions（multipart：file、model、prompt、language、response_format）
           → {"text": "FAKE-TRANSCRIPT …", "duration"?（verbose_json 才有）}；DURATION 秒
  Deepgram POST /v1/listen?model=…（Token 密钥，正文是音频）→ results.channels[0].alternatives[0].transcript、metadata.duration
  ElevenLabs POST /v1/speech-to-text（multipart：file、model_id、language_code）→ {"text", "words": [{start, end}]}
生图：OpenAI POST /v1/images/generations（JSON）、/v1/images/edits（multipart）→ data[].b64_json + usage；Google 的 generateContent 带
  responseModalities IMAGE → inlineData 是一张 PNG；fal POST /fal-ai/<路径>（Key 密钥）→ images[].url（data: 地址）；ComfyUI POST /prompt →
  prompt_id，GET /history/<id>（第一次问还没好）、GET /view?filename=… → PNG
生成视频、音乐：Google POST /v1beta/interactions（response_format type video → 假的 MP4，delivery uri 时给 Files API 的地址、
  GET /v1beta/files/<id>:download 拿字节；type audio → 歌词 + 假的 MP3）；fal 的队列 POST /queue/<模型路径> → request_id，
  GET …/requests/<id>/status（IN_QUEUE → IN_PROGRESS → COMPLETED）、GET …/requests/<id> → video.url / audio.url（data: 地址）；
  ElevenLabs POST /v1/music → 假的 MP3
朗读：OpenAI POST /v1/audio/speech（JSON）、ElevenLabs POST /v1/text-to-speech/<voice>、Deepgram POST /v1/speak → 一段假的音频（FAKE_MP3 /
  WAV 的字节）；Google 的 generateContent 带 responseModalities AUDIO → inlineData 是 24 kHz 的 PCM（audio/L16;codec=pcm;rate=24000）
密钥：key 设了就核对；不对回 400 API_KEY_INVALID / 401，错误里原样写着收到的密钥（测报错里抹掉密钥）。
剧本：on(路径的正则, 函数) 换掉某一条的回法：函数拿到 Rec，回 (状态码, 头, 字节 | dict)；fail(状态码, 几次) 接下来几个请求回错。
requests 录下每个请求（Rec：method、path、query、headers 里有没有密钥、json / 原样的字节）。
"""
from __future__ import annotations

import base64
import json
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

USAGE = {"promptTokenCount": 1200, "candidatesTokenCount": 80}
DURATION = 12.5
FAKE_MP3 = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\xff\xfb\x90\x00" * 64   # 像 MP3 的开头（只拿来对字节，不是真能放的）
FAKE_MP4 = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + b"\x00\x00\x00\x08free" + b"\x00" * 256   # 像 MP4 的开头（同上）
OMNI_TOKENS = 5792 * 8      # 假的 Omni Flash：8 秒 720p 的输出 token
LYRICS = "[Verse 1]\nFictional lyrics about a night drive past the harbour"
PCM_SECONDS = 1.5


def png(w: int = 8, h: int = 8, color: tuple = (40, 160, 90)) -> bytes:
    """一张小 PNG（标准库就能写：zlib + crc32）。"""
    import struct
    import zlib
    raw = b"".join(b"\x00" + bytes(color) * w for _ in range(h))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


IMAGE_USAGE = {"input_tokens": 50, "output_tokens": 1056, "input_tokens_details": {"text_tokens": 50, "image_tokens": 0}}


def form(rec: Rec) -> dict:
    """multipart/form-data → {名字: 字 | (文件名, 字节, 类型)}（测试里看发出去的是什么）。"""
    from email.parser import BytesParser
    from email.policy import HTTP
    ctype = rec.headers.get("content-type", "")
    if "multipart/form-data" not in ctype:
        return {}
    msg = BytesParser(policy=HTTP).parsebytes(b"Content-Type: " + ctype.encode() + b"\r\n\r\n" + rec.body)
    out: dict = {}
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition")
        data = part.get_payload(decode=True) or b""
        fname = part.get_filename()
        out[name] = (fname, data, part.get_content_type()) if fname else data.decode("utf-8", "replace")
    return out


@dataclass
class Rec:
    method: str
    path: str
    query: dict
    headers: dict
    body: bytes
    json: object = None
    auth: str = ""          # 收到的密钥（x-goog-api-key / Bearer / xi-api-key / Token / Key）
    extra: dict = field(default_factory=dict)


Reply = tuple[int, dict, object]


class FakeMedia:
    def __init__(self, *, key: str | None = None) -> None:
        self.key = key
        self.requests: list[Rec] = []
        self.routes: list[tuple[re.Pattern, str, Callable[[Rec], Reply]]] = []
        self._fails: list[tuple[int, str]] = []
        self._lock = threading.Lock()
        self._httpd: ThreadingHTTPServer | None = None
        self.on(r"^/v1beta/models/[^/:]+:generateContent$", self.gemini, method="POST")
        self.on(r"^/upload/v1beta/files$", self.upload_start, method="POST")
        self.on(r"^/upload/v1beta/files/session$", self.upload_finish, method="POST")
        self.on(r"^/v1beta/files/[a-z0-9]+$", self.file_state, method="GET")
        self.on(r"^/v1beta/files/[a-z0-9]+$", lambda rec: (200, {}, {}), method="DELETE")
        self.on(r"^/v1beta/files/[a-z0-9]+:download$", self.file_bytes, method="GET")
        self.on(r"^/v1beta/interactions$", self.interaction, method="POST")
        self.on(r"^/queue/", self.fal_submit, method="POST")
        self.on(r"^/queue/[^/]+/[^/]+/requests/[^/]+/status$", self.fal_status, method="GET")
        self.on(r"^/queue/[^/]+/[^/]+/requests/[^/]+$", self.fal_result, method="GET")
        self.on(r"^/v1/music$", self.music, method="POST")
        self.fal_jobs: dict[str, dict] = {}
        self.on(r"^(?:/openai)?/v1/audio/transcriptions$", self.transcriptions, method="POST")
        self.on(r"^/v1/listen$", self.listen, method="POST")
        self.on(r"^/v1/speech-to-text$", self.scribe, method="POST")
        self.on(r"^/v1/audio/speech$", self.speech, method="POST")
        self.on(r"^/v1/images/generations$", self.images_new, method="POST")
        self.on(r"^/v1/images/edits$", self.images_edit, method="POST")
        self.on(r"^/fal-ai/", self.fal, method="POST")
        self.on(r"^/prompt$", self.comfy_prompt, method="POST")
        self.on(r"^/history/[^/]+$", self.comfy_history, method="GET")
        self.on(r"^/view$", lambda rec: (200, {"content-type": "image/png"}, png(6, 6, (200, 0, 0))), method="GET")
        self.comfy: dict[str, int] = {}
        self.keyless = re.compile(r"^/(?:prompt|history/[^/]+|view)$")  # 本机的 ComfyUI 不要密钥
        self.on(r"^/v1/text-to-speech/[^/]+$", self.speech, method="POST")
        self.on(r"^/v1/speak$", self.speech, method="POST")
        self.uploads: dict[str, dict] = {}

    # —— 起停 ——
    def __enter__(self) -> FakeMedia:
        return self.start()

    def __exit__(self, *_exc) -> None:
        self.stop()

    def start(self) -> FakeMedia:
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), _handler(self))
        self._httpd.daemon_threads = True
        threading.Thread(target=self._httpd.serve_forever, kwargs={"poll_interval": 0.05}, name="fake-media", daemon=True).start()
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

    @property
    def gemini_url(self) -> str:
        return self.url + "/v1beta"

    # —— 剧本 ——
    def on(self, pattern: str, fn: Callable[[Rec], Reply], *, method: str = "POST") -> None:
        """换掉（或者加上）一条：后加的先比。"""
        with self._lock:
            self.routes.insert(0, (re.compile(pattern), method, fn))

    def fail(self, status: int, times: int = 1, message: str = "fake failure") -> None:
        with self._lock:
            self._fails += [(status, message)] * times

    def of(self, pattern: str) -> list[Rec]:
        rx = re.compile(pattern)
        with self._lock:
            return [r for r in self.requests if rx.search(r.path)]

    # —— 各家 ——
    def gemini(self, rec: Rec) -> Reply:
        body = rec.json if isinstance(rec.json, dict) else {}
        if "IMAGE" in ((body.get("generationConfig") or {}).get("responseModalities") or []):  # 生图：回一张 PNG
            return 200, {}, {"candidates": [{"content": {"role": "model", "parts": [{"text": "Here you go."}, {"inlineData": {
                "mimeType": "image/png", "data": base64.b64encode(png()).decode()}}]}, "finishReason": "STOP"}],
                "usageMetadata": {"promptTokenCount": 30, "candidatesTokenCount": 1290}}
        if "AUDIO" in ((body.get("generationConfig") or {}).get("responseModalities") or []):  # 朗读：回 PCM
            pcm = b"\x00\x01" * int(24000 * PCM_SECONDS)
            return 200, {}, {"candidates": [{"content": {"role": "model", "parts": [{"inlineData": {"mimeType": "audio/L16;codec=pcm;rate=24000",
                                                                                                  "data": base64.b64encode(pcm).decode()}}]},
                                             "finishReason": "STOP"}], "usageMetadata": {"promptTokenCount": 20, "candidatesTokenCount": 900}}
        parts = [p for c in body.get("contents") or [] for p in (c.get("parts") or [])]
        text = " ".join(str(p.get("text") or "") for p in parts if "text" in p)
        kinds = [str((p.get("inline_data") or p.get("inlineData") or {}).get("mime_type") or (p.get("file_data") or {}).get("mime_type") or "")
                 for p in parts if "inline_data" in p or "inlineData" in p or "file_data" in p]
        if any(k.startswith("image/") for k in kinds):
            answer = f"FAKE-VISION: {sum(k.startswith('image/') for k in kinds)} image(s). Asked: {text[-120:]}"
        elif "application/pdf" in kinds:
            size = sum(len(base64.b64decode((p.get("inline_data") or {}).get("data") or "")) for p in parts if "inline_data" in p)
            answer = f"FAKE-PDF: read a {size}-byte PDF. Asked: {text[-120:]}"
        elif any(k.startswith("video/") for k in kinds):
            answer = f"FAKE-VIDEO: watched it. Asked: {text[-120:]}"
        elif any(k.startswith("audio/") for k in kinds):
            answer = f"FAKE-AUDIO: heard it. Asked: {text[-120:]}"
        else:
            answer = f"FAKE-TEXT: {text[-120:]}"
        usage = dict(USAGE)
        if any(k.startswith("audio/") for k in kinds):  # 真的 Gemini 按模态分开报（音频的价钱不一样）
            usage["promptTokensDetails"] = [{"modality": "TEXT", "tokenCount": 40}, {"modality": "AUDIO", "tokenCount": USAGE["promptTokenCount"] - 40}]
        return 200, {}, {"candidates": [{"content": {"role": "model", "parts": [{"text": answer}]}, "finishReason": "STOP"}],
                         "usageMetadata": usage}


    def interaction(self, rec: Rec) -> Reply:
        """Interactions API（生成视频、音乐）：response_format 的 type video → 一段假的 MP4（delivery uri 时放进 Files API 给个地址），
        audio → 歌词 + 假的 MP3；usage 照 Interactions 的名字回。"""
        body = rec.json if isinstance(rec.json, dict) else {}
        fmt = body.get("response_format") if isinstance(body.get("response_format"), dict) else {}
        inp = body.get("input")
        parts = [inp] if isinstance(inp, str) else [p for p in inp or [] if isinstance(p, dict)]
        text = " ".join(p if isinstance(p, str) else str(p.get("text") or "") for p in parts)
        if not text.strip():
            return 400, {}, {"error": {"code": 400, "message": "input is required"}}
        pics = sum(1 for p in parts if isinstance(p, dict) and p.get("type") == "image")
        if fmt.get("type") == "video":
            if fmt.get("delivery") == "uri":
                fid = f"v{len(self.uploads) + 1}"
                self.uploads[fid] = {"bytes": len(FAKE_MP4), "mime": "video/mp4", "polls": 0, "data": FAKE_MP4}
                item = {"type": "video", "mime_type": "video/mp4", "uri": f"{self.url}/v1beta/files/{fid}"}
            else:
                item = {"type": "video", "mime_type": "video/mp4", "data": base64.b64encode(FAKE_MP4).decode()}
            content, out = [item], OMNI_TOKENS
        elif fmt.get("type") == "audio":
            content, out = [{"type": "text", "text": LYRICS}, {"type": "audio", "mime_type": "audio/mpeg",
                                                                "data": base64.b64encode(FAKE_MP3).decode()}], 1500
        else:
            content, out = [{"type": "text", "text": f"FAKE-INTERACTION: {text[-120:]}"}], 20
        usage = {"total_input_tokens": 20 + 258 * pics, "total_output_tokens": out, "total_tokens": 20 + 258 * pics + out}
        return 200, {}, {"object": "interaction", "status": "completed", "model": body.get("model"), "usage": usage,
                         "steps": [{"type": "user_input", "content": [{"type": "text", "text": text}]}, {"type": "model_output", "content": content}]}

    def file_bytes(self, rec: Rec) -> Reply:
        f = self.uploads.get(rec.path.rsplit("/", 1)[-1].split(":", 1)[0])
        if f is None or "data" not in f:
            return 404, {}, {"error": {"code": 404, "message": "no such file"}}
        return 200, {"content-type": f["mime"]}, f["data"]

    def fal_submit(self, rec: Rec) -> Reply:
        """fal 的队列：POST /queue/<模型路径> → request_id 和几个地址；status 第一次问 IN_QUEUE、第二次 IN_PROGRESS、再问 COMPLETED。"""
        model = rec.path[len("/queue/"):]
        rid = f"req{len(self.fal_jobs) + 1}"
        self.fal_jobs[rid] = {"model": model, "body": rec.json, "polls": 0}
        root = f"{self.url}/queue/{'/'.join(model.split('/')[:2])}/requests/{rid}"
        return 200, {}, {"request_id": rid, "status_url": root + "/status", "response_url": root, "cancel_url": root + "/cancel", "queue_position": 0}

    def fal_status(self, rec: Rec) -> Reply:
        j = self.fal_jobs.get(rec.path.split("/")[-2])
        if j is None:
            return 404, {}, {"detail": "no such request"}
        j["polls"] += 1
        return 200, {}, {"status": ("IN_QUEUE", "IN_PROGRESS")[j["polls"] - 1] if j["polls"] < 3 else "COMPLETED"}

    def fal_result(self, rec: Rec) -> Reply:
        j = self.fal_jobs.get(rec.path.rsplit("/", 1)[-1])
        if j is None:
            return 404, {}, {"detail": "no such request"}
        if re.search(r"music|audio", j["model"]):
            return 200, {}, {"audio": {"url": "data:audio/mpeg;base64," + base64.b64encode(FAKE_MP3).decode(), "content_type": "audio/mpeg"}}
        return 200, {}, {"video": {"url": "data:video/mp4;base64," + base64.b64encode(FAKE_MP4).decode(), "content_type": "video/mp4"}, "seed": 7}

    def music(self, rec: Rec) -> Reply:
        body = rec.json if isinstance(rec.json, dict) else {}
        if not (body.get("prompt") or body.get("composition_plan")):
            return 422, {}, {"detail": [{"loc": ["body", "prompt"], "msg": "prompt or composition_plan is required", "type": "value_error"}]}
        return 200, {"content-type": "audio/mpeg", "song-id": "song_fake1"}, FAKE_MP3

    def upload_start(self, rec: Rec) -> Reply:
        assert rec.headers.get("x-goog-upload-command") == "start", "the upload must start with X-Goog-Upload-Command: start"
        return 200, {"x-goog-upload-url": self.url + "/upload/v1beta/files/session?upload_id=u1"}, {}

    def upload_finish(self, rec: Rec) -> Reply:
        fid = f"f{len(self.uploads) + 1}"
        self.uploads[fid] = {"bytes": len(rec.body), "mime": rec.headers.get("content-type", ""), "polls": 0}
        return 200, {}, {"file": {"name": f"files/{fid}", "uri": f"{self.url}/v1beta/files/{fid}", "mimeType": rec.headers.get("content-type", ""),
                                  "state": "PROCESSING"}}

    def file_state(self, rec: Rec) -> Reply:
        fid = rec.path.rsplit("/", 1)[-1]
        f = self.uploads.get(fid)
        if f is None:
            return 404, {}, {"error": {"code": 404, "message": "no such file"}}
        f["polls"] += 1
        return 200, {}, {"name": f"files/{fid}", "uri": f"{self.url}/v1beta/files/{fid}", "mimeType": f["mime"],
                         "state": "ACTIVE" if f["polls"] > 1 else "PROCESSING"}

    def images_new(self, rec: Rec) -> Reply:
        body = rec.json if isinstance(rec.json, dict) else {}
        n = int(body.get("n") or 1)
        return 200, {}, {"data": [{"b64_json": base64.b64encode(png(8 + i, 8)).decode()} for i in range(n)], "usage": dict(IMAGE_USAGE)}

    def images_edit(self, rec: Rec) -> Reply:
        f = form(rec)
        rec.extra = f
        n = int(f.get("n") or 1)
        usage = {**IMAGE_USAGE, "input_tokens": 50 + 300, "input_tokens_details": {"text_tokens": 50, "image_tokens": 300}}
        return 200, {}, {"data": [{"b64_json": base64.b64encode(png(9, 9 + i)).decode()} for i in range(n)], "usage": usage}

    def fal(self, rec: Rec) -> Reply:
        body = rec.json if isinstance(rec.json, dict) else {}
        n = int(body.get("num_images") or 1)
        return 200, {}, {"images": [{"url": "data:image/png;base64," + base64.b64encode(png(7, 7 + i)).decode(), "content_type": "image/png"}
                                    for i in range(n)], "seed": 1}

    def comfy_prompt(self, rec: Rec) -> Reply:
        pid = f"p{len(self.comfy) + 1}"
        self.comfy[pid] = 0
        return 200, {}, {"prompt_id": pid, "number": 1}

    def comfy_history(self, rec: Rec) -> Reply:
        pid = rec.path.rsplit("/", 1)[-1]
        self.comfy[pid] = self.comfy.get(pid, 0) + 1
        if self.comfy[pid] < 2:
            return 200, {}, {}
        return 200, {}, {pid: {"outputs": {"9": {"images": [{"filename": f"{pid}.png", "subfolder": "", "type": "output"}]}}}}

    def speech(self, rec: Rec) -> Reply:
        body = rec.json if isinstance(rec.json, dict) else {}
        if not (body.get("input") or body.get("text")):
            return 400, {}, {"error": {"message": "text is required"}}
        if body.get("response_format") == "wav" or rec.query.get("container") == ["wav"]:
            import io
            import wave
            buf = io.BytesIO()
            with wave.open(buf, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(16000)
                w.writeframes(b"\x00\x00" * 16000)
            return 200, {"content-type": "audio/wav"}, buf.getvalue()
        return 200, {"content-type": "audio/mpeg"}, FAKE_MP3

    def transcriptions(self, rec: Rec) -> Reply:
        f = form(rec)
        rec.extra = f
        if not isinstance(f.get("file"), tuple):
            return 400, {}, {"error": {"message": "file is required"}}
        out = {"text": f"FAKE-TRANSCRIPT model={f.get('model')} lang={f.get('language', '')} prompt={f.get('prompt', '')}"}
        if f.get("response_format") == "verbose_json":
            out["duration"] = DURATION
        return 200, {}, out

    def listen(self, rec: Rec) -> Reply:
        return 200, {}, {"metadata": {"duration": DURATION},
                         "results": {"channels": [{"alternatives": [{"transcript": f"FAKE-DEEPGRAM model={rec.query.get('model', [''])[0]} "
                                                                                  f"bytes={len(rec.body)}"}]}]}}

    def scribe(self, rec: Rec) -> Reply:
        f = form(rec)
        rec.extra = f
        return 200, {}, {"text": f"FAKE-SCRIBE model={f.get('model_id')} heard a short fictional memo", "words": [{"text": "x", "start": 0.0, "end": DURATION}]}


def _handler(fm: FakeMedia) -> type[BaseHTTPRequestHandler]:
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a) -> None:  # 安静
            pass

        def _go(self, method: str) -> None:
            u = urlsplit(self.path)
            n = int(self.headers.get("content-length") or 0)
            raw = self.rfile.read(n) if n else b""
            heads = {k.lower(): v for k, v in self.headers.items()}
            auth = (heads.get("x-goog-api-key") or heads.get("xi-api-key") or re.sub(r"^(Bearer|Token|Key)\s+", "", heads.get("authorization") or "")
                    or "")
            rec = Rec(method, u.path, parse_qs(u.query), heads, raw, auth=auth)
            if "json" in heads.get("content-type", ""):
                try:
                    rec.json = json.loads(raw or b"null")
                except ValueError:
                    rec.json = None
            with fm._lock:
                fm.requests.append(rec)
                failing = fm._fails.pop(0) if fm._fails else None
                routes = list(fm.routes)
            if fm.key is not None and auth != fm.key and not fm.keyless.search(u.path):
                status = 400 if "x-goog-api-key" in heads else 401
                return self._send(status, {}, {"error": {"code": status, "message": f"API key not valid: got {auth!r}",
                                                         "status": "INVALID_ARGUMENT" if status == 400 else "UNAUTHENTICATED",
                                                         "details": [{"reason": "API_KEY_INVALID"}]}})
            if failing is not None:
                return self._send(failing[0], {}, {"error": {"code": failing[0], "message": failing[1]}})
            for rx, m, fn in routes:
                if m == method and rx.search(u.path):
                    status, headers, out = fn(rec)
                    return self._send(status, headers, out)
            return self._send(404, {}, {"error": {"code": 404, "message": f"no fake route for {method} {u.path}"}})

        def _send(self, status: int, headers: dict, out) -> None:
            data = out if isinstance(out, bytes) else json.dumps(out).encode()
            try:
                self.send_response(status)
                ctype = headers.pop("content-type", None) or ("application/octet-stream" if isinstance(out, bytes) else "application/json")
                self.send_header("content-type", ctype)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):  # 那边已经不等了（测「点了停」的时候）：不用回
                pass

        def do_POST(self) -> None:  # noqa: N802
            self._go("POST")

        def do_GET(self) -> None:  # noqa: N802
            self._go("GET")

        def do_PUT(self) -> None:  # noqa: N802
            self._go("PUT")

        def do_DELETE(self) -> None:  # noqa: N802
            self._go("DELETE")
    return H
