"""工具结果里的图（内核 ⑫b，2026-10-16）：图怎么进工具结果、卡多大、存在哪、发给各家时怎么带、老图怎么办。

谁会出图（都调 make(字节, label=…) 拿一个图块，放进 loop.ToolOutput 和字一起回给内核）：
  浏览器的 screenshot（engine/browser.py）、主机文件的 read_file（engine/host_files.py）、沙箱的 sandbox_read（engine/hands.py）读到图片、
  MCP 工具回的 image 内容（engine/mcp_client.py）。
卡大小（改写自 pi 的 packages/coding-agent/src/utils/image-resize-core.ts、image-resize.ts、image-process.ts，earendil-works/pi commit 8efd314f，
MIT，Copyright (c) 2025 Mario Zechner；许可全文见仓库根目录的 THIRD_PARTY_NOTICES）：
  PNG / JPEG / GIF / WebP 原样收；长、宽都不超过 MAX_SIDE（2000）、base64 以后不到 MAX_B64（4.5 MB，给 Claude 的 5 MB 留余量）的原样用。
  超了：先按比例缩进 2000 × 2000，依次试 PNG、JPEG 质量 80 / 85 / 70 / 55 / 40，第一个放得下的就用；都放不下就长宽各乘 0.75 再来，
  缩到 1 × 1 还放不下就不要这张图，换成一句话。缩过的在字里写明原来多大、现在多大、坐标乘几（pi 的原话）。别的格式（BMP……）转成 PNG。
  JPEG 带 EXIF 方向的先转正。缩放要 Pillow（requirements 里有；用到时才导入）：没有 Pillow 就只按字节数卡，超了退成文字。
  有 Pillow 时每张都先整张解一遍（pi 也是先解码再量）：坏图不交给模型（不然会话里留着一张坏图，这一家以后每次都报错）。
存（不把大图的 base64 塞进 SQLite 的每一条；照 DeepSeek Harness 的附件库：按内容取名、会话里只记引用）：
  <data_dir>/engine/images/<sha256>.<扩展名>，同一张图只存一份。会话里的图块：
    {"type": "image", "mime", "file": 图库里的文件名, "sha", "bytes", "w", "h", "label": 是什么（截的哪一页、哪个文件、哪个工具）}
  发给模型的时候再读回来。KEEP_DAYS 天以前存的、总量超过 STORE_MAX 的（旧的先删）顺手清掉；删掉了的图回放时换成一句「已经不在了」。
  也认 {"data": base64}（app 发来的附件、测试）和 {"path": 本机的绝对路径, "sha"?}（读的时候对一下指纹，变了就不发）。
发给模型（providers.stream 调 for_model，每家各算一次，不改会话里的记录）：
  - 老图换成一句：是什么图、多大、存在哪，要再看就重新截、重新读（DeepSeek Harness 的「image omitted to fit request image limits」）。
    留多少照 Hermes（context_compressor 的 _MAX_KEEP_TOOL_IMAGES = 3；它发送前按上限成批地清，image_eviction_policy 的 batch / floor）：
    最近 KEEP 张总是带着；更早的攒够 BATCH 张才一起换成字（OpenClaw 说得对：换掉已经发过的图会让缓存的前缀从那里断开，一张一张换就每一轮都断；
    成批换，BATCH 张才断一次）。pi、OpenClaw 不删老图，截图多了一轮比一轮贵。压缩时摘要里只写「[N image(s)]」（compact.serialize）。
  - 模型不收图（PROVIDERS 的 images = False，比如 DeepSeek）：每张都换成一句：有一张图、多大、存在哪（pi 的 placeholder 只说「省掉了」，
    这里多写存在哪，模型能跟用户说、能换个看图的模型）。
  - 出错的工具结果里的图也换成一句（Claude 不收 is_error 的 tool_result 里放图：pi 挪到后面，这里出错的结果本来就不带图，只是兜底）。
  - 其余读回 base64：Claude 放进 tool_result 的 image 块（provider_anthropic）；OpenAI 兼容接口的 tool 消息放不了图，照 pi 挪到这一批工具结果
    后面紧跟着的一条 user 里，写明是哪个工具的结果（provider_openai）。
  - 那一家因为图回 400 / 413（自己加的兼容接口没写 images = false、它不认的图）：图都换成一句话再发一次，这个模型一小时内直接发字
    （providers.stream；不然会话里的图会让这个对话之后的每一句都报错）。
server.json（都可以不写）：
  "engine": {"images": {"keep": 3, "batch": 5, "max_side": 2000, "max_bytes": 4718592, "days": 30, "store_max_mb": 1024}}
"""
from __future__ import annotations

import base64
import hashlib
import io
import math
import os
import time
from pathlib import Path

from config import raw, settings

MAX_SIDE = 2000                       # pi 的 DEFAULT_MAX_WIDTH / HEIGHT
MAX_B64 = int(4.5 * 1024 * 1024)      # base64 以后最多多少字节（pi：给 Claude 的 5 MB 留余量）
QUALITIES = (80, 85, 70, 55, 40)      # JPEG 依次试的质量（pi：jpegQuality 80，再 85、70、55、40）
READ_MAX = 25 * 1024 * 1024           # 一张图最多读进来多少字节再处理（再大的不收）
PIXELS_MAX = 64_000_000               # 解出来最多多少像素（DeepSeek Harness 的上限；防解压炸弹）
KEEP = 3                              # 发给模型时总是带着最近几张工具结果里的图（Hermes 的 _MAX_KEEP_TOOL_IMAGES）
BATCH = 5                             # 更早的攒够几张才一起换成字（缓存的前缀 BATCH 张图才断一次）
KEEP_DAYS = 30
STORE_MAX = 1024 * 1024 * 1024        # 图库最多多大（字节）
PRUNE_EVERY = 3600                    # 多久清一次图库（秒）
OK = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp"}
_pruned = [0.0]


def cfg() -> dict:
    e = raw().get("engine")
    c = e.get("images") if isinstance(e, dict) else None
    return c if isinstance(c, dict) else {}


def _int(key: str, default: int, low: int = 0) -> int:
    v = cfg().get(key)
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= low else default


def keep() -> int:
    return _int("keep", KEEP)


def dropped(total: int, keep_n: int | None = None, batch: int | None = None) -> int:
    """一共 total 张工具图：最早的几张换成字。最近 keep 张总是留着；更早的攒够 batch 张才一起换（中间这几轮前缀不变）。"""
    keep_n = keep() if keep_n is None else keep_n
    batch = max(1, _int("batch", BATCH, 1) if batch is None else batch)
    return 0 if total < keep_n + batch else (total - keep_n) // batch * batch


def max_side() -> int:
    return _int("max_side", MAX_SIDE, 16)


def max_b64() -> int:
    return _int("max_bytes", MAX_B64, 1024)


def store_dir() -> Path:
    return settings.data_dir / "engine" / "images"


def b64_len(n: int) -> int:
    return math.ceil(n / 3) * 4


def size_text(n: int) -> str:
    return f"{n} B" if n < 1024 else f"{n / 1024:.0f} KB" if n < 1024 * 1024 else f"{n / 1024 / 1024:.1f} MB"


# —— 认格式、量大小（只看文件头，不用 Pillow）——————————————————————————————————————————

def sniff(data: bytes) -> str | None:
    """看文件头认图（照 pi 的 utils/mime.ts，按魔数不按扩展名）：PNG / JPEG / GIF / WebP / BMP → 媒体类型；别的 → None。"""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff" and data[3:4] != b"\xf7":  # FF D8 FF F7 是 JPEG-LS，不收
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:2] == b"BM" and len(data) > 26 and int.from_bytes(data[14:18], "little") in (12, 40, 52, 56, 64, 108, 124):  # BMP 的 DIB 头长度
        return "image/bmp"
    return None


def dims(data: bytes, mime: str | None = None) -> tuple[int, int] | None:
    """宽、高（只读文件头）。认不出来 → None。"""
    mime = mime or sniff(data)
    try:
        if mime == "image/png":
            return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
        if mime == "image/gif":
            return int.from_bytes(data[6:8], "little"), int.from_bytes(data[8:10], "little")
        if mime == "image/bmp":
            return abs(int.from_bytes(data[18:22], "little", signed=True)), abs(int.from_bytes(data[22:26], "little", signed=True))
        if mime == "image/webp":
            kind = data[12:16]
            if kind == b"VP8 " and data[23:26] == b"\x9d\x01\x2a":
                return int.from_bytes(data[26:28], "little") & 0x3FFF, int.from_bytes(data[28:30], "little") & 0x3FFF
            if kind == b"VP8L" and data[20:21] == b"\x2f":
                b = int.from_bytes(data[21:25], "little")
                return (b & 0x3FFF) + 1, ((b >> 14) & 0x3FFF) + 1
            if kind == b"VP8X":
                return int.from_bytes(data[24:27], "little") + 1, int.from_bytes(data[27:30], "little") + 1
            return None
        if mime == "image/jpeg":
            i = 2
            while i + 9 < len(data):
                if data[i] != 0xFF:
                    i += 1
                    continue
                m = data[i + 1]
                if m == 0xFF:
                    i += 1
                    continue
                if m in (0xD8, 0x01) or 0xD0 <= m <= 0xD7:
                    i += 2
                    continue
                if m in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    return int.from_bytes(data[i + 7:i + 9], "big"), int.from_bytes(data[i + 5:i + 7], "big")
                i += 2 + int.from_bytes(data[i + 2:i + 4], "big")
    except (IndexError, ValueError):
        return None
    return None


# —— 卡大小、缩放（照 pi）——————————————————————————————————————————————————————————————

def _pil():
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return None
    return Image, ImageOps


def _encode(im, fmt: str, quality: int | None) -> bytes:
    buf = io.BytesIO()
    if fmt == "JPEG":
        if im.mode not in ("RGB", "L"):  # JPEG 没有透明：铺在白底上
            from PIL import Image
            rgba = im.convert("RGBA")
            bg = Image.new("RGB", rgba.size, (255, 255, 255))
            bg.paste(rgba, mask=rgba.split()[-1])
            im = bg
        im.save(buf, "JPEG", quality=quality)
    else:
        im.save(buf, "PNG")
    return buf.getvalue()


def fit(data: bytes, mime: str) -> tuple[bytes, str, int, int, str] | str:
    """→ (字节, 媒体类型, 宽, 高, 给模型的一句（缩过才有）)；放不下 / 读不了 → 一句为什么（给模型看）。
    mime 是看文件头认出来的（sniff）；认不出来的是 ""（MCP 说是图、文件头又不认识：有 Pillow 就试着转成 PNG）。"""
    side, cap = max_side(), max_b64()
    pil = _pil()
    if pil is None:  # 没有 Pillow：只按字节数卡（长宽超了也照发：各家自己会缩，超得太离谱的才不收）
        wh = dims(data, mime) if mime in OK else None
        if wh and min(wh) > 0 and max(wh) <= 8000 and b64_len(len(data)) < cap:
            return data, mime, wh[0], wh[1], ""
        if mime not in OK:
            return "[Image omitted: this image format can't be passed on.]"
        return ("[Image omitted: it's too large to pass on (" + size_text(len(data)) + ") and this server can't shrink images "
                "(Pillow isn't installed).]")
    Image, ImageOps = pil
    try:
        with Image.open(io.BytesIO(data)) as src:  # open 只读文件头：先看像素数再解（防解压炸弹；不动 Pillow 全局的上限）
            if src.size[0] * src.size[1] > PIXELS_MAX:
                return "[Image omitted: it has too many pixels to process.]"
            src.load()  # 整张解一遍：坏图在这里就认出来（原样交给模型的坏图，这一家每次都会报错）
            turned = mime == "image/jpeg" and int(src.getexif().get(0x0112, 1) or 1) != 1  # 手机照片的 EXIF 方向：要转正
            if mime in OK and max(src.size) <= side and b64_len(len(data)) < cap and not turned:
                return data, mime, src.size[0], src.size[1], ""
            im = ImageOps.exif_transpose(src)
        if im.mode not in ("RGB", "RGBA", "L"):  # 调色板（GIF）、带透明的灰度：转成 RGBA 再缩（P 模式只能最近邻缩）
            im = im.convert("RGBA")
        ow, oh = im.size
        scale = min(1.0, side / max(ow, oh))
        tw, th = max(1, round(ow * scale)), max(1, round(oh * scale))
        while True:
            cur = im if (tw, th) == im.size else im.resize((tw, th), Image.LANCZOS)
            for fmt, q in (("PNG", None), *(("JPEG", q) for q in dict.fromkeys(QUALITIES))):
                out = _encode(cur, fmt, q)
                if b64_len(len(out)) < cap:
                    note = "" if (tw, th) == (ow, oh) else (f"[Image: original {ow}x{oh}, displayed at {tw}x{th}. Multiply coordinates by "
                                                            f"{ow / tw:.2f} to map to original image.]")
                    return out, "image/png" if fmt == "PNG" else "image/jpeg", tw, th, note
            if tw == 1 and th == 1:
                break
            tw, th = max(1, math.floor(tw * 0.75)), max(1, math.floor(th * 0.75))
    except Exception:  # noqa: BLE001 — 坏图、Pillow 不认的格式
        return "[Image omitted: it couldn't be read as an image.]"
    return "[Image omitted: could not be resized below the inline image size limit.]"


# —— 存进图库 ——————————————————————————————————————————————————————————————————

def path_of(block: dict) -> Path | None:
    f = str(block.get("file") or "")
    if f and "/" not in f and not f.startswith("."):
        return store_dir() / f
    p = str(block.get("path") or "")
    return Path(p) if p.startswith("/") else None


def save(data: bytes, mime: str) -> tuple[str, str]:
    """按内容存一份（已经有了就不再写）→ (文件名, sha256)。写临时文件再换名，半截的文件不会被当成图。"""
    sha = hashlib.sha256(data).hexdigest()
    name = f"{sha}.{OK.get(mime, 'bin')}"
    d = store_dir()
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    if p.exists():
        os.utime(p)
    else:
        tmp = d / f".{name}.{os.getpid()}.tmp"
        tmp.write_bytes(data)
        os.replace(tmp, p)
    if time.monotonic() - _pruned[0] > PRUNE_EVERY:
        _pruned[0] = time.monotonic()
        prune()
    return name, sha


def prune() -> int:
    """清图库：KEEP_DAYS 天以前存的，再按总量从旧的删。→ 删了几张。"""
    d = store_dir()
    try:
        files = sorted(((p.stat().st_mtime, p.stat().st_size, p) for p in d.iterdir() if p.is_file() and not p.name.startswith(".")),
                       key=lambda x: x[0])
    except OSError:
        return 0
    cutoff = time.time() - _int("days", KEEP_DAYS, 1) * 86400
    total = sum(s for _, s, _ in files)
    limit = _int("store_max_mb", STORE_MAX // (1024 * 1024), 1) * 1024 * 1024
    n = 0
    for mtime, size, p in files:
        if mtime >= cutoff and total <= limit:
            break
        try:
            p.unlink()
            total -= size
            n += 1
        except OSError:
            pass
    return n


def make(data: bytes, *, label: str, mime: str | None = None) -> tuple[dict | None, str]:
    """一张图（工具读到、截到、MCP 回的）→ (图块, 给模型的一句)：卡大小、缩放、存进图库（同步：调用方放进线程里跑）。
    图块 None = 这张图不交给模型，那一句说为什么（照样写进工具结果）。"""
    if len(data) > READ_MAX:
        return None, f"[Image omitted: it's {size_text(len(data))}, too large to process.]"
    got = sniff(data)  # 按文件头认；说是图（MCP 的 mimeType）、文件头又不认识的，交给 Pillow 试（认不出来就不要）
    if got is None and not str(mime or "").startswith("image/"):
        return None, "[Image omitted: this isn't an image format that can be passed on (PNG, JPEG, GIF or WebP).]"
    res = fit(data, got or "")
    if isinstance(res, str):
        return None, res
    out, mime_, w, h, note = res
    name, sha = save(out, mime_)
    block = {"type": "image", "mime": mime_, "file": name, "sha": sha, "bytes": len(out), "w": w, "h": h, "label": " ".join(label.split())[:160]}
    if got not in OK:
        was = (got or str(mime)).split("/")[-1].upper()
        note = (note + " " if note else "") + f"[Image converted from {was} to {mime_.split('/')[1].upper()}.]"
    return block, note


def from_b64(b64: str, *, label: str, mime: str | None = None) -> tuple[dict | None, str]:
    """base64 的一张图（MCP 的 image 内容、CDP 的截图）→ make。坏的 base64 → (None, 一句)。"""
    try:
        data = base64.b64decode(b64 or "", validate=True)
    except ValueError:
        return None, "[Image omitted: the image data wasn't valid base64.]"
    return make(data, label=label, mime=mime)


# —— 发给模型 ——————————————————————————————————————————————————————————————————

def load(block: dict) -> str | None:
    """图块 → base64（读不到、指纹对不上 → None）。"""
    if block.get("data"):
        return str(block["data"])
    p = path_of(block)
    if p is None:
        return None
    try:
        data = p.read_bytes()
    except OSError:
        return None
    if block.get("path") and block.get("sha") and hashlib.sha256(data).hexdigest() != block["sha"]:
        return None  # 指向本机文件的：文件后来变了，不是当时那张
    return base64.b64encode(data).decode()


def describe(block: dict) -> str:
    """这张图是什么、多大、存在哪（一句，给换成文字的地方用）。"""
    kind = str(block.get("mime") or "image").split("/")[-1].upper()
    size = f"{block['w']}x{block['h']}" if block.get("w") and block.get("h") else ""
    bits = ", ".join(x for x in (kind, size, size_text(int(block["bytes"])) if block.get("bytes") else "") if x)
    label = str(block.get("label") or "").strip()
    where = path_of(block)
    return (f"{label} ({bits})" if label else bits) + (f", saved on the server at {where}" if where else "")


def tool_images(messages: list[dict]) -> int:
    return sum(1 for m in messages if m.get("role") == "tool" for b in m.get("content") or [] if b.get("type") == "image")


def for_model(messages: list[dict], accepts: bool = True, keep_n: int | None = None, batch: int | None = None) -> list[dict]:
    """发给这一家之前（见文件开头「发给模型」）：工具结果里的图读回 base64，或者换成一句话。不改传进来的消息；没有工具图就原样回。"""
    total = tool_images(messages)
    if not total:
        return messages
    old = dropped(total, keep_n, batch)
    seen = 0
    out: list[dict] = []
    for m in messages:
        if m.get("role") != "tool" or not any(b.get("type") == "image" for b in m.get("content") or []):
            out.append(m)
            continue
        blocks: list[dict] = []
        for b in m.get("content") or []:
            if b.get("type") != "image":
                blocks.append(b)
                continue
            seen += 1
            if not accepts:
                text = f"[Image omitted: this model can't view images. It was {describe(b)}.]"
            elif m.get("error"):
                text = f"[Image omitted from an error result: {describe(b)}.]"
            elif seen <= old:
                text = f"[Earlier image no longer shown, to save room: {describe(b)}. Take it or read it again if you need to see it.]"
            elif (data := load(b)) is None:
                text = f"[Image no longer available: {describe(b)}.]"
            else:
                blocks.append({"type": "image", "mime": b.get("mime") or "image/png", "data": data})
                continue
            blocks.append({"type": "text", "text": ("\n" if blocks else "") + text})
        out.append({**m, "content": blocks})
    return out
