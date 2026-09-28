"""分享（2026-09-28，社交第一层）：把一条回复、一篇想完了的笔记，变成一个链接或一张卡片发给别人。

- 分享是一份快照：建的时候把正文抄一份存进 grava.db 的 shares 表，原文以后再改，已经发出去的不变。
- 发之前先挡私事：从档案（USER.md）认出住址、伴侣和家人的名字，再加上邮箱、电话、身体数字（体重、体脂、心率、睡眠……）
  和 server.json share.private_words 里你自己加的词，默认都挡住；app 里一处处「放出来」。挡住的地方在链接页和卡片上都是一块灰条，
  原文不出服务器。标题里出现挡住的词也一样挡。
- 两种样子：带链接（发微信、WhatsApp：/s/<令牌> 是一页干净的网页，点开就能看，不用装 app；链接预览图 /s/<令牌>/card.png）和
  干净版（发小红书：一张 3:4 竖图，图里没有网址、二维码和 app 名字）。图在服务器上用 Pillow 画，要有中文字体（Noto Sans CJK）。
- 外网打开链接：server.json 的 share.public_port 让 run.py 在 127.0.0.1 上另起一个只有 /s/ 的小服务（public.py），
  再用 Tailscale Funnel（或别的反向代理）把 /s 指过去；share.public_url 是外面看到的地址（https://<机器>.<tailnet>.ts.net）。
  没配 public_url 时 app 只给图，不给链接。主服务上也挂着 /s/（只在自己的设备上能开，不算浏览次数）。
- 收回：链接页变成「已经收回了」，快照正文清空（标题留着，你的列表里还看得到）；卡片缓存删掉。草稿 7 天没发就删。
- 状态：draft 草稿 / live 链接能打开 / friends 只发给了朋友（社交第二层，见 friends.py；/s/ 打不开）/ revoked 收回了。
- 不调模型，不花额度。
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import html
import json
import re
import secrets
import shutil
import sqlite3
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel

import chat
import think
from chat import _lock, db, log_activity, now_iso
from config import raw, settings
from i18n import L

router = APIRouter()          # 给自己的：/api/shares…（要令牌）
public_router = APIRouter()   # 给别人的：/s/<令牌>（不要令牌；主服务和 public.py 的小服务都挂它）
REVOKE_HOOKS: list = []       # 收回一条发出去过的分享时调 fn(分享 id)：朋友那边的也收回（friends.py 挂）
JSON_HOOKS: list = []         # share_json 补字段：fn(行) -> dict（friends.py 挂 sentTo：发给过哪些朋友）

SHARE_RE = re.compile(r"^sh-[0-9a-f]{8}$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,40}$")
BODY_CAP = 60_000
DRAFT_DAYS = 7
MASK = ""      # 挡住的地方在排版时的占位符（私用区字符，Markdown 不会动它）
BLOCK = "▇▇▇"        # 给人看的纯文字版里挡住的样子


# —— 配置 ——

def cfg() -> dict:
    c = raw().get("share")
    return c if isinstance(c, dict) else {}


def public_url() -> str | None:
    u = str(cfg().get("public_url") or "").strip().rstrip("/")
    return u if u.startswith(("https://", "http://")) else None


def public_port() -> int | None:
    try:
        p = int(cfg().get("public_port") or 0)
    except (TypeError, ValueError):
        return None
    return p if 0 < p < 65536 else None


def cache_dir() -> Path:
    return settings.data_dir / "shares"


# —— 表 ——

_ready = False


def sdb() -> sqlite3.Connection:
    global _ready
    conn = db()
    if not _ready:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS shares (id TEXT PRIMARY KEY, token TEXT NOT NULL UNIQUE, kind TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT '{}', title_src TEXT NOT NULL DEFAULT '', title TEXT, body TEXT NOT NULL DEFAULT '',
            masks TEXT NOT NULL DEFAULT '[]', released TEXT NOT NULL DEFAULT '[]', quote TEXT,
            status TEXT NOT NULL DEFAULT 'draft', views INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, published_at TEXT, revoked_at TEXT);
        CREATE INDEX IF NOT EXISTS shares_created ON shares(created_at);
        """)
        _ready = True
    return conn


def row(sid: str) -> sqlite3.Row:
    if not SHARE_RE.match(sid or ""):
        raise HTTPException(404, L("找不到这条分享", "Share not found"))
    with _lock, sdb() as conn:
        r = conn.execute("SELECT * FROM shares WHERE id=?", (sid,)).fetchone()
    if not r:
        raise HTTPException(404, L("找不到这条分享", "Share not found"))
    return r


def row_by_token(token: str) -> sqlite3.Row | None:
    if not TOKEN_RE.match(token or ""):
        return None
    with _lock, sdb() as conn:
        return conn.execute("SELECT * FROM shares WHERE token=?", (token,)).fetchone()


# —— 认出私事 ——

PARTNER_WORDS = ("伴侣", "女朋友", "男朋友", "女友", "男友", "对象", "妻子", "丈夫", "老婆", "老公", "爱人", "未婚妻", "未婚夫",
                 "partner", "wife", "husband", "girlfriend", "boyfriend", "fiancée", "fiancee", "fiancé", "fiance", "spouse")
FAMILY_WORDS = ("父亲", "母亲", "爸爸", "妈妈", "父母", "儿子", "女儿", "孩子", "哥哥", "姐姐", "弟弟", "妹妹", "爷爷", "奶奶",
                "外公", "外婆", "家人", "father", "mother", "dad", "mom", "mum", "parents", "son", "daughter", "brother", "sister",
                "grandfather", "grandmother", "grandma", "grandpa", "family")
LATIN_NAME = re.compile(r"\b[A-Z][a-z]+(?:[ -][A-Z][a-z]+){1,2}\b")
HAN_NAME = re.compile(r"[（(]([一-鿿]{2,4})[）)]|(?:叫|名叫|名字是)([一-鿿]{2,3})")
ADDR_LINE = re.compile(r"住址|地址|住在|居住|家住|住处|^[-*\s]*住|\baddress\b|\blives? (?:at|in)\b|\bhome\b", re.I)
UK_POSTCODE = re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]? ?\d[A-Z]{2}\b")
STREET_WORDS = (r"(?:Street|St|Road|Rd|Square|Sq|Place|Pl|Avenue|Ave|Lane|Ln|Close|Court|Ct|Gardens|Way|Drive|Dr|Terrace|"
                r"Crescent|Boulevard|Blvd|Row|Walk|Mews)")
STREET_EN = re.compile(rf"\b\d{{1,5}}[A-Za-z]?,? (?:[A-Z][a-z]+ ){{0,3}}{STREET_WORDS}\b\.?")
PLACE_EN = re.compile(rf"\b(?:[A-Z][a-z]+ ){{1,3}}{STREET_WORDS}\b")
ADDR_ZH = re.compile(r"[一-鿿]{2,12}(?:路|街|大道|巷|弄|胡同)\s*\d+\s*号(?:\s*[一-鿿\dA-Za-z]{0,6}?(?:栋|幢|座|号楼|单元|楼|层|室))*"
                     r"|[一-鿿]{2,10}(?:小区|花园|公寓|大厦|新村|家园)(?:\s*\d+\s*(?:栋|幢|号楼|单元|室))*")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
PHONE = re.compile(r"(?<![\d+])(?:\+\d{1,3}[\s-]?\d{2,4}[\s-]?\d{3,4}[\s-]?\d{3,4}|1[3-9]\d{9}|07\d{3}\s?\d{6}|\(\d{3}\)\s?\d{3}-\d{4}|\d{3}-\d{3}-\d{4})(?!\d)")
BODY_KW = (r"体重|体脂率|体脂|腰围|臀围|胸围|BMI|静息心率|心率变异性?|心率|HRV|血压|血糖|血氧|体温|恢复分|睡眠|睡了|深睡|浅睡|"
           r"热量缺口|热量|卡路里|摄入|基础代谢|蛋白质|碳水|脂肪|body ?fat|weight|resting heart rate|heart rate|blood pressure|"
           r"blood sugar|glucose|SpO2|calories|recovery score|sleep|protein|carbs?")
_NUM = r"\d+(?:\.\d+)?(?:\s*[-–~到至]\s*\d+(?:\.\d+)?)?"
_UNIT = (r"(?:\s*(?:%|％|kg|公斤|斤|lbs?|磅|bpm|次/分钟?|ms|毫秒|mmHg|mmol/L|kcal|千卡|大卡|卡|g|克|个?半?小时|小时|h|分钟|分|min|"
         r"cm|厘米|°C|度))?")
BODY = re.compile(rf"(?:{BODY_KW})[^\d\n。；;，,]{{0,8}}?(?P<num>{_NUM}{_UNIT}(?:\s*{_NUM}{_UNIT})?)", re.I)

_profile: tuple[float, list[tuple[str, str, str]]] | None = None


def _has_word(line: str, words: tuple[str, ...]) -> bool:
    low = line.lower()
    for w in words:
        if w.isascii():
            if re.search(rf"\b{re.escape(w)}\b", low):
                return True
        elif w in line:
            return True
    return False


def profile_terms() -> list[tuple[str, str, str]]:
    """档案里要挡的词：[(词, kind, 给你看的标签)]。按文件修改时间缓存。"""
    global _profile
    p = settings.profile
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return []
    if _profile and _profile[0] == mtime:
        return _profile[1]
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return []
    terms: list[tuple[str, str, str]] = []
    for line in text.splitlines():
        partner = _has_word(line, PARTNER_WORDS)
        if partner or _has_word(line, FAMILY_WORDS):
            label = L("伴侣的名字", "Partner's name") if partner else L("家人的名字", "Family member's name")
            for m in LATIN_NAME.finditer(line):
                name = m.group()
                terms.append((name, "name", label))
                first = name.split()[0].split("-")[0]
                if len(first) >= 3:
                    terms.append((first, "name", label))
            for m in HAN_NAME.finditer(line):
                name = m.group(1) or m.group(2)
                terms.append((name, "name", label))
                if len(name) == 3:
                    terms.append((name[1:], "name", label))  # 只叫名字的时候
        if ADDR_LINE.search(line):
            for rx in (STREET_EN, PLACE_EN, UK_POSTCODE, ADDR_ZH):
                for m in rx.finditer(line):
                    terms.append((m.group().strip(" ,."), "address", L("住址", "Address")))
    seen: set[str] = set()
    uniq = [t for t in terms if len(t[0]) >= 2 and not (t[0].lower() in seen or seen.add(t[0].lower()))]
    _profile = (mtime, uniq)
    return uniq


def find_private(text: str) -> list[dict]:
    """正文里要挡的地方：[{id, kind, label, start, end}]，按位置排好、不重叠。"""
    hits: list[tuple[int, int, str, str]] = []
    terms = list(profile_terms())
    for w in cfg().get("private_words") or []:
        if isinstance(w, str) and len(w.strip()) >= 2:
            terms.append((w.strip(), "custom", L("你设的词", "Your word")))
    for word, kind, label in terms:
        flags = re.I if word.isascii() else 0
        pat = rf"(?<![A-Za-z]){re.escape(word)}(?![A-Za-z])" if word.isascii() else re.escape(word)
        for m in re.finditer(pat, text, flags):
            hits.append((m.start(), m.end(), kind, label))
    for rx in (STREET_EN, UK_POSTCODE, ADDR_ZH):
        for m in rx.finditer(text):
            hits.append((m.start(), m.end(), "address", L("住址", "Address")))
    for m in EMAIL.finditer(text):
        hits.append((m.start(), m.end(), "contact", L("邮箱", "Email")))
    for m in PHONE.finditer(text):
        hits.append((m.start(), m.end(), "contact", L("电话", "Phone")))
    for m in BODY.finditer(text):
        s, e = m.span("num")
        if text[s:e].strip():
            hits.append((s, e, "body", L("身体数字", "Body numbers")))
    hits.sort(key=lambda h: (h[0], -(h[1] - h[0])))
    out: list[dict] = []
    end = -1
    for s, e, kind, label in hits:
        if s < end:
            continue  # 和前一处重叠：留先开始、更长的那个
        while e > s and text[e - 1].isspace():
            e -= 1
        if e <= s:
            continue
        out.append({"id": f"m{len(out) + 1}", "kind": kind, "label": label, "start": s, "end": e})
        end = e
    return out


def apply_masks(text: str, masks: list[dict], released: set[str], mark: str = BLOCK) -> str:
    out, pos = [], 0
    for m in sorted(masks, key=lambda m: m["start"]):
        if m["id"] in released or m["start"] < pos:
            continue
        out.append(text[pos:m["start"]])
        out.append(mark)
        pos = m["end"]
    out.append(text[pos:])
    return "".join(out)


def hidden_words(r: sqlite3.Row) -> list[str]:
    """还挡着的那些原文（标题里出现也要挡）。"""
    body = r["body"] or ""
    rel = set(json.loads(r["released"] or "[]"))
    return sorted({body[m["start"]:m["end"]] for m in json.loads(r["masks"] or "[]") if m["id"] not in rel}, key=len, reverse=True)


def mask_title(r: sqlite3.Row, mark: str = BLOCK) -> str:
    t = r["title"] or r["title_src"] or ""
    if r["title"]:
        return t if mark == BLOCK else t.replace(BLOCK, mark)  # 你自己改过的标题照原样（收回时存的是挡好的）
    for w in hidden_words(r):
        if w.strip():
            t = t.replace(w, mark)
    body = r["body"] or ""
    rel = set(json.loads(r["released"] or "[]"))
    shown = {body[m["start"]:m["end"]] for m in json.loads(r["masks"] or "[]") if m["id"] in rel}
    for m in reversed(find_private(t)):
        if t[m["start"]:m["end"]] not in shown:  # 正文里放出来的，标题里也放
            t = t[:m["start"]] + mark + t[m["end"]:]
    return t


# —— 纯文字、默认那句话 ——

MD_STRIP = [(re.compile(r"```.*?```", re.S), " "), (re.compile(r"!\[[^\]]*\]\([^)]*\)"), ""), (re.compile(r"\[([^\]]*)\]\([^)]*\)"), r"\1"),
            (re.compile(r"^\s{0,3}(?:#{1,6}\s+|>\s?|[-*+]\s+|\d+[.)]\s+)", re.M), ""), (re.compile(r"(\*\*|__|\*|_|`|~~)"), "")]


def plain(md: str) -> str:
    for rx, rep in MD_STRIP:
        md = rx.sub(rep, md)
    return md


def first_line(text: str, cap: int = 40) -> str:
    for ln in plain(text).splitlines():
        ln = ln.strip(" \t「」\"“”'‘’")
        if ln:
            return ln if len(ln) <= cap else ln[:cap - 1].rstrip() + "…"
    return ""


def default_quote(shown: str, title: str) -> str:
    """卡片上的那一句：正文里第一句像样的话（挡住的照样挡着），跳过「问：」那行。"""
    t = plain(shown)
    for part in re.split(r"(?<=[。！？!?])|\n+", t):
        s = part.strip(" \t「」\"“”'‘’")
        if s.startswith(("问：", "Q:", "Q：")):
            continue
        if 8 <= len(s) <= 90:
            return s
    t = " ".join(t.split())
    return (t[:80].rstrip() + "…") if len(t) > 80 else (t or title)


# —— 快照 ——

class ShareIn(BaseModel):
    kind: str                       # message | note | text
    thread: str | None = None       # message：哪个对话
    id: str | None = None           # message：哪条（db123 或 123）
    withQuestion: bool | None = None  # message：回复前面带上你问的那句（默认带）
    topic: str | None = None        # note：想完了的主题（用它存下的那篇笔记）
    path: str | None = None         # note：库里的笔记（相对库的路径）
    title: str | None = None        # text
    text: str | None = None         # text


def _note_file(rel_path: str) -> Path:
    base = think.base_dir().resolve()
    p = (base / rel_path).resolve()
    if not p.is_relative_to(base) or p.suffix.lower() != ".md" or not p.is_file():
        raise HTTPException(404, L("找不到这篇笔记", "Note not found"))
    return p


def snapshot(b: ShareIn) -> tuple[str, dict, str, str]:
    """(kind, 来源, 标题, 正文)。"""
    kind = (b.kind or "").strip()
    if kind == "message":
        if not b.thread or not b.id:
            raise HTTPException(400, L("要说是哪个对话的哪条", "Which message?"))
        mid = chat.row_id(b.id)
        with _lock, db() as conn:
            m = conn.execute("SELECT * FROM messages WHERE thread=? AND id=?", (b.thread, mid)).fetchone()
            q = None
            if m and m["role"] == "grava":
                q = conn.execute("SELECT text FROM messages WHERE thread=? AND id<? AND role='user' ORDER BY id DESC LIMIT 1",
                                 (b.thread, mid)).fetchone()
        if not m:
            raise HTTPException(404, L("找不到这条消息", "Message not found"))
        if m["role"] not in ("grava", "user") or not (m["text"] or "").strip():
            raise HTTPException(400, L("这条不能分享", "This message can't be shared"))
        text = m["text"].strip()
        with_q = bool(q and (q["text"] or "").strip()) and (b.withQuestion is not False) and m["role"] == "grava"
        title = ""
        head = next((ln for ln in text.splitlines() if ln.strip()), "")
        if re.match(r"^\s{0,3}#{1,3}\s", head):
            title = first_line(head)
        if with_q:
            qt = q["text"].strip()
            title = title or first_line(qt)
            lines = qt.splitlines()
            text = "\n".join(["> " + L("问：", "Q: ") + lines[0], *("> " + ln for ln in lines[1:])]) + "\n\n" + text
        title = title or first_line(text)
        has_q = bool(q and (q["text"] or "").strip())
        return "message", {"thread": b.thread, "message": mid, "withQuestion": with_q, "hasQuestion": has_q}, title, text
    if kind == "note":
        rel_path = (b.path or "").strip()
        if b.topic:
            r = think.load_topic(b.topic)
            rel_path = r["note_path"] or ""
            if not rel_path:
                raise HTTPException(409, L("这个主题还没存成笔记", "This topic hasn't been saved as a note yet"))
        if not rel_path:
            raise HTTPException(400, L("要说是哪篇笔记", "Which note?"))
        p = _note_file(rel_path)
        meta, body, _bad = think.parse_note(p.read_text(encoding="utf-8"))
        body = body.strip()
        h1 = re.match(r"^#\s+(.+)\n?", body)
        title = str(meta.get("title") or "").strip() or (h1.group(1).strip() if h1 else "") \
            or re.sub(r"^\d{4}-\d{2}-\d{2}\s*", "", p.stem).strip()
        if h1 and h1.group(1).strip() == title:
            body = body[h1.end():].lstrip()
        rel_note = str(p.relative_to(think.base_dir().resolve()))
        return "note", {"path": rel_note, **({"topic": b.topic} if b.topic else {})}, title, body
    if kind == "text":
        text = (b.text or "").strip()
        if not text:
            raise HTTPException(400, L("没有内容", "Nothing to share"))
        return "text", {}, (b.title or "").strip()[:80] or first_line(text), text
    raise HTTPException(400, L("不认识的分享类型", "Unknown kind"))


def _cap(text: str) -> str:
    return text if len(text) <= BODY_CAP else text[:BODY_CAP].rstrip() + "\n\n…"


def create(b: ShareIn) -> str:
    kind, source, title, body = snapshot(b)
    body = _cap(body)
    src = json.dumps(source, ensure_ascii=False, sort_keys=True)
    with _lock, sdb() as conn:
        old = conn.execute("SELECT id FROM shares WHERE kind=? AND source=? AND status IN ('draft','live') AND body=? "
                           "ORDER BY created_at DESC LIMIT 1", (kind, src, body)).fetchone()
        if old and kind != "text":
            return old["id"]  # 同一条再点分享：还是原来那份（链接不变，放出来的也还在）
    masks = find_private(body)
    sid = f"sh-{uuid.uuid4().hex[:8]}"
    ts = now_iso()
    with _lock, sdb() as conn:
        conn.execute("INSERT INTO shares(id, token, kind, source, title_src, body, masks, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                     (sid, secrets.token_urlsafe(16), kind, src, title[:120], body, json.dumps(masks, ensure_ascii=False), ts, ts))
    return sid


# —— 给 app 的样子 ——

def segments(body: str, masks: list[dict], rel: set[str]) -> list[dict]:
    out, pos = [], 0
    for m in sorted(masks, key=lambda m: m["start"]):
        if m["start"] < pos:
            continue
        if m["start"] > pos:
            out.append({"t": body[pos:m["start"]]})
        out.append({"m": m["id"], "t": body[m["start"]:m["end"]], "label": m["label"], "released": m["id"] in rel})
        pos = m["end"]
    if pos < len(body):
        out.append({"t": body[pos:]})
    return out


def card_sig(r: sqlite3.Row, style: str) -> str:
    h = hashlib.sha1(json.dumps([style, mask_title(r, MASK), quote_of(r, MASK), author(), r["released"], r["created_at"], 3],
                                ensure_ascii=False).encode()).hexdigest()
    return h[:10]


def quote_of(r: sqlite3.Row, mark: str = BLOCK) -> str:
    if r["quote"]:
        return r["quote"]
    rel = set(json.loads(r["released"] or "[]"))
    return default_quote(apply_masks(r["body"] or "", json.loads(r["masks"] or "[]"), rel, mark), mask_title(r, mark))


def author() -> str:
    return settings.user_name or ""


def local_day(ts: str | None) -> str:
    if not ts:
        return ""
    d = think.local(ts)
    return d.strftime("%Y-%m-%d") if L("zh", "en") == "zh" else d.strftime("%b %-d, %Y")


def link_of(r: sqlite3.Row) -> str | None:
    base = public_url()
    return f"{base}/s/{r['token']}" if base and r["status"] == "live" else None


def share_json(r: sqlite3.Row, full: bool = False) -> dict:
    masks = json.loads(r["masks"] or "[]")
    rel = set(json.loads(r["released"] or "[]"))
    body = r["body"] or ""
    d = think.local(r["created_at"])
    out = {"id": r["id"], "kind": r["kind"], "status": r["status"], "title": mask_title(r), "titleCustom": bool(r["title"]),
           "quote": quote_of(r), "quoteCustom": bool(r["quote"]), "views": r["views"], "createdAt": r["created_at"],
           "publishedAt": r["published_at"], "revokedAt": r["revoked_at"], "day": d.strftime("%Y-%m-%d"), "time": d.strftime("%H:%M"),
           "blocked": sum(1 for m in masks if m["id"] not in rel), "maskCount": len(masks),
           "url": link_of(r), "path": f"/s/{r['token']}" if r["status"] == "live" else None, "canLink": bool(public_url()),
           "source": json.loads(r["source"] or "{}")}
    for hook in JSON_HOOKS:
        try:
            out.update(hook(r) or {})
        except Exception:  # noqa: BLE001 — 补不上就不补
            pass
    if full:
        out["masks"] = [{"id": m["id"], "kind": m["kind"], "label": m["label"], "text": body[m["start"]:m["end"]],
                         "before": " ".join(body[max(0, m["start"] - 16):m["start"]].split()),
                         "after": " ".join(body[m["end"]:m["end"] + 16].split()), "released": m["id"] in rel} for m in masks]
        out["segments"] = segments(body, masks, rel)
    return out


# —— 画卡片 ——

_font_files: dict[str, tuple[str, int] | None] = {}
_fonts: dict[tuple[str, int], object] = {}
KNOWN_FONTS = {"bold": [("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", 2), ("/System/Library/Fonts/PingFang.ttc", 0)],
               "regular": [("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 2), ("/System/Library/Fonts/PingFang.ttc", 0)]}


def font_file(weight: str) -> tuple[str, int] | None:
    """中文字体：server.json share.font / share.font_bold > fc-list 找到的 Noto Sans CJK SC > 常见位置。"""
    if weight in _font_files:
        return _font_files[weight]
    found: tuple[str, int] | None = None
    own = cfg().get("font_bold" if weight == "bold" else "font")
    if own and Path(str(own)).expanduser().is_file():
        found = (str(Path(str(own)).expanduser()), 0)
    if not found and shutil.which("fc-list"):
        try:
            out = subprocess.run(["fc-list", ":lang=zh", "-f", "%{file}|%{index}|%{family}|%{style}\n"],  # noqa: S603,S607
                                 capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.SubprocessError):
            out = ""
        best = -1
        for line in out.splitlines():
            parts = line.split("|")
            if len(parts) != 4 or not parts[0]:
                continue
            fam, style = parts[2], parts[3].lower()
            score = (4 if "Sans CJK SC" in fam else 3 if "Sans CJK" in fam else 2 if "Sans" in fam and "Mono" not in fam else 1) * 10
            want = "bold" if weight == "bold" else "regular"
            score += 5 if want in style else 0
            if score > best:
                best, found = score, (parts[0], int(parts[1] or 0))
    if not found:
        found = next(((f, i) for f, i in KNOWN_FONTS[weight] if Path(f).is_file()), None)
    _font_files[weight] = found
    return found


def font(weight: str, size: int, cjk: bool):
    from PIL import ImageFont
    key = (weight, size)
    if key in _fonts:
        return _fonts[key]
    ff = font_file(weight)
    if not ff:
        if cjk:
            raise HTTPException(501, L("服务器上没有中文字体，画不了卡片（装一个 Noto Sans CJK，比如 apt install fonts-noto-cjk）",
                                       "No CJK font on the server, so the card can't be drawn (install Noto Sans CJK, e.g. apt install fonts-noto-cjk)"))
        return ImageFont.load_default(size)
    f = ImageFont.truetype(ff[0], size, index=ff[1])
    _fonts[key] = f
    return f


CLOSE_PUNCT = set("，。、；：！？」』）》〉】〕…,.;:!?)]}%’”")
UNIT_RE = re.compile(r"|[A-Za-z0-9’'\-_.:/@#&+=]+[ \t]*|\s|.")


def _w(f, size: int, u: str) -> float:
    return size * 2.3 if u == MASK else f.getlength(u)


def wrap(f, size: int, text: str, width: float, max_lines: int) -> tuple[list[list[str]], bool]:
    lines: list[list[str]] = []
    for para in text.split("\n"):
        para = para.strip()
        if not para:
            continue
        cur: list[str] = []
        cw = 0.0
        units: list[str] = []
        for u in UNIT_RE.findall(para):
            if u != MASK and len(u) > 1 and f.getlength(u) > width:
                units.extend(u)  # 一个词比一行还长（网址之类）：拆成字
            else:
                units.append(u)
        for u in units:
            w = _w(f, size, u)
            if cur and cw + w > width and not (u[0] in CLOSE_PUNCT and cw + w <= width + size):
                lines.append(cur)
                cur, cw = [], 0.0
                if u.isspace():
                    continue
            cur.append(u)
            cw += w
        if cur:
            lines.append(cur)
    cut = len(lines) > max_lines
    if cut:
        lines = lines[:max_lines]
        last = lines[-1]
        while last and sum(_w(f, size, u) for u in last) + f.getlength("…") > width:
            last.pop()
        while last and last[-1].isspace():
            last.pop()
        last.append("…")
    return lines, cut


OPEN_PUNCT = set("「『（《〈【")


def draw_lines(d, f, size: int, lines: list[list[str]], x: float, y: float, line_h: float, fill: str, mask_fill: str) -> float:
    top, bottom = f.getbbox("中")[1::2]  # 灰条和汉字一样高：按这个字号里一个汉字实际画在哪
    for ln in lines:
        lx = x - (size * 0.5 if ln and ln[0] in OPEN_PUNCT else 0)  # 行首的「 挂出去半个字，字才对得齐
        for u in ln:
            if u == MASK:
                d.rounded_rectangle([lx + size * 0.1, y + top, lx + size * 2.2, y + bottom], radius=size * 0.18, fill=mask_fill)
                lx += size * 2.3
            else:
                d.text((lx, y), u, font=f, fill=fill)
                lx += f.getlength(u)
        y += line_h
    return y


def has_cjk(s: str) -> bool:
    return bool(re.search(r"[　-鿿＀-￯]", s))


def render_card(style: str, title: str, quote: str, who: str, when: str) -> bytes:
    """style: clean（发小红书的 3:4 竖图，没有网址 / 二维码 / app 名字）| link（链接预览图 1200×630）。"""
    from PIL import Image, ImageDraw
    cjk = has_cjk(title + quote + who)
    zh = cjk or L("zh", "en") == "zh"
    q = f"「{quote}」" if zh else f"“{quote}”"
    if style == "clean":
        W, H, pad = 1080, 1440, 100
        img = Image.new("RGB", (W, H), "#101216")
        d = ImageDraw.Draw(img)
        size, lines = 68, []
        for size in (68, 62, 56, 50, 46, 42, 38):
            f = font("bold", size, cjk)
            lines, cut = wrap(f, size, q, W - 2 * pad, 11)
            if not cut:
                break
        f = font("bold", size, cjk)
        tf, mf = font("bold", 40, cjk), font("regular", 32, cjk)
        tl, _ = wrap(tf, 40, title, W - 2 * pad, 2) if title else ([], False)
        meta = " · ".join(x for x in (who, when) if x)
        qh = len(lines) * size * 1.55
        rest = 64 + len(tl) * 40 * 1.45 + (20 + 32 * 1.4 if meta else 0)
        y = max(pad, (H - qh - rest) / 2 - 30)
        y = draw_lines(d, f, size, lines, pad, y, size * 1.55, "#ECEEF0", "#3A3F47")
        y += 40
        d.rounded_rectangle([pad, y, pad + 56, y + 6], radius=3, fill="#DDB56A")
        y += 24
        y = draw_lines(d, tf, 40, tl, pad, y, 40 * 1.45, "#DDB56A", "#6B5A36")
        if meta:
            draw_lines(d, mf, 32, [[meta]], pad, y + 20, 32 * 1.4, "#8A929B", "#3A3F47")
    else:
        W, H, pad = 1200, 630, 72
        img = Image.new("RGB", (W, H), "#FFFFFF")
        d = ImageDraw.Draw(img)
        d.rectangle([0, 0, W, 14], fill="#D9AE62")
        mf, tf, qf = font("regular", 30, cjk), font("bold", 58, cjk), font("regular", 38, cjk)
        meta = " · ".join(x for x in ((L(f"{who} 的分享", f"Shared by {who}") if who else ""), when) if x)
        if meta:
            draw_lines(d, mf, 30, [[meta]], pad, 60, 30 * 1.5, "#5F6770", "#D5D9DD")
        tl, _ = wrap(tf, 58, title, W - 2 * pad, 2)
        ql, _ = wrap(qf, 38, q, W - 2 * pad, 3 if len(tl) < 2 else 2)
        top, bottom = 130, H - pad - 50  # 标题和那一句放在页眉和「点开看全文」之间，上下居中
        block = len(tl) * 58 * 1.35 + 22 + len(ql) * 38 * 1.6
        y = top + max(0, (bottom - top - block) / 2)
        y = draw_lines(d, tf, 58, tl, pad, y, 58 * 1.35, "#12151A", "#D5D9DD") + 22
        draw_lines(d, qf, 38, ql, pad, y, 38 * 1.6, "#4B535C", "#D5D9DD")
        draw_lines(d, mf, 30, [[L("点开看全文", "Tap to read")]], pad, H - pad - 30, 30 * 1.5, "#0C5F73", "#D5D9DD")
    buf = BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def card_png(r: sqlite3.Row, style: str) -> bytes:
    style = "link" if style == "link" else "clean"
    sig = card_sig(r, style)
    p = cache_dir() / f"{r['id']}-{style}-{sig}.png"
    if p.is_file():
        return p.read_bytes()
    png = render_card(style, mask_title(r, MASK), quote_of(r, MASK), author(), local_day(r["published_at"] or r["created_at"]))
    p.parent.mkdir(parents=True, exist_ok=True)
    for old in p.parent.glob(f"{r['id']}-{style}-*.png"):
        old.unlink(missing_ok=True)
    tmp = p.with_name(f".{p.name}.{uuid.uuid4().hex[:6]}")
    tmp.write_bytes(png)
    tmp.replace(p)
    return png


def drop_cards(sid: str) -> None:
    for old in cache_dir().glob(f"{sid}-*.png"):
        old.unlink(missing_ok=True)


# —— 链接页 ——

PAGE_CSS = """:root{color-scheme:light dark;--bg:#FAFAF8;--ink:#12151A;--ink2:#4B535C;--ink3:#6B737C;--line:#E7EAEC;--gold:#8A5E12;--q:#F1F3F4}
@media (prefers-color-scheme:dark){:root{--bg:#101216;--ink:#ECEEF0;--ink2:#C5CAD0;--ink3:#8A929B;--line:#262A30;--gold:#DDB56A;--q:#191C21}}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font:17px/1.75 -apple-system,BlinkMacSystemFont,"PingFang SC","Hiragino Sans GB","Noto Sans CJK SC","Noto Sans SC","Microsoft YaHei",sans-serif;-webkit-font-smoothing:antialiased;overflow-wrap:anywhere}
main{max-width:680px;margin:0 auto;padding:40px 20px 64px}
.by{font-size:14px;color:var(--ink3);margin:0 0 8px}
h1{font-size:26px;line-height:1.35;margin:0 0 24px;font-weight:800;letter-spacing:-.01em}
article h1,article h2,article h3,article h4{line-height:1.4;margin:1.6em 0 .6em}
article h1{font-size:22px}article h2{font-size:20px}article h3,article h4{font-size:18px}
article p{margin:0 0 1em}
article blockquote{margin:0 0 1.2em;padding:12px 16px;background:var(--q);border-radius:12px;color:var(--ink2)}
article blockquote p{margin:0}article blockquote p+p{margin-top:.6em}
article ul,article ol{padding-left:1.4em;margin:0 0 1em}article li{margin:.2em 0}
article code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.88em;background:var(--q);padding:.1em .35em;border-radius:6px}
article pre{background:var(--q);padding:14px;border-radius:12px;overflow:auto;line-height:1.5}article pre code{background:none;padding:0}
article table{border-collapse:collapse;display:block;overflow:auto;margin:0 0 1em}article th,article td{border:1px solid var(--line);padding:6px 10px;text-align:left}
article a{color:var(--gold)}article hr{border:none;border-top:1px solid var(--line);margin:1.6em 0}article img{max-width:100%}
.m{display:inline-block;width:2.2em;height:.95em;border-radius:.3em;background:var(--ink);opacity:.2;vertical-align:-.12em}
footer{margin-top:40px;padding-top:16px;border-top:1px solid var(--line);font-size:13px;color:var(--ink3)}
.gone{padding-top:18vh;text-align:center;color:var(--ink2)}.gone h1{font-size:22px;margin-bottom:8px}"""
HEADERS = {"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow", "Referrer-Policy": "no-referrer",
           "X-Content-Type-Options": "nosniff",
           "Content-Security-Policy": "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"}
BOTS = re.compile(r"bot|crawl|spider|slurp|facebookexternalhit|WhatsApp/|Slackbot|Discordbot|Twitterbot|LinkedInBot|Embedly|SkypeUriPreview|preview", re.I)
_md = None


def render_md(text: str) -> str:
    """Markdown → HTML：不放行原始 HTML；挡住的地方（占位符）换成灰条。"""
    global _md
    try:
        from markdown_it import MarkdownIt
        if _md is None:
            _md = MarkdownIt("commonmark", {"html": False, "breaks": True, "linkify": False, "typographer": False}).enable(["table", "strikethrough"])
        out = _md.render(text)
        out = out.replace("<a href=", '<a rel="nofollow noopener noreferrer" href=')
    except ImportError:  # 没装 markdown-it-py：按段落原样显示
        out = "".join(f"<p>{html.escape(p).replace(chr(10), '<br>')}</p>" for p in re.split(r"\n\s*\n", text) if p.strip())
    return out.replace(MASK, f'<span class="m" role="img" aria-label="{html.escape(L("已隐藏", "Hidden"))}"></span>')


def page(title: str, body_html: str, head: str = "", status: int = 200) -> HTMLResponse:
    lang = "zh-CN" if L("zh", "en") == "zh" else "en"
    doc = (f'<!doctype html><html lang="{lang}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
           f'<meta name="robots" content="noindex,nofollow"><title>{html.escape(title)}</title>{head}<style>{PAGE_CSS}</style></head>'
           f"<body><main>{body_html}</main></body></html>")
    return HTMLResponse(doc, status_code=status, headers=HEADERS)


def gone_page(revoked: bool) -> HTMLResponse:
    if revoked:
        t, s = L("这条分享已经收回了", "This share has been withdrawn"), L("发的人把它收回了。", "The person who shared it took it back.")
    else:
        t, s = L("找不到这条分享", "Share not found"), L("链接可能不完整，或者已经过期。", "The link may be incomplete or out of date.")
    return page(t, f'<div class="gone"><h1>{html.escape(t)}</h1><p>{html.escape(s)}</p></div>', status=410 if revoked else 404)


def public_page(r: sqlite3.Row, request: Request) -> HTMLResponse:
    masks = json.loads(r["masks"] or "[]")
    rel = set(json.loads(r["released"] or "[]"))
    shown = apply_masks(r["body"] or "", masks, rel, MASK)
    title = mask_title(r, MASK)
    who = author()
    when = local_day(r["published_at"] or r["created_at"])
    by = " · ".join(x for x in (html.escape(who), html.escape(when)) if x)
    base = public_url() or str(request.base_url).rstrip("/")
    desc = " ".join(plain(apply_masks(r["body"] or "", masks, rel)).split())[:110]
    plain_title = title.replace(MASK, BLOCK)
    head = (f'<meta property="og:type" content="article"><meta property="og:title" content="{html.escape(plain_title)}">'
            f'<meta property="og:description" content="{html.escape(desc)}">'
            f'<meta property="og:image" content="{html.escape(base)}/s/{r["token"]}/card.png">'
            f'<meta property="og:image:width" content="1200"><meta property="og:image:height" content="630">'
            f'<meta name="twitter:card" content="summary_large_image">')
    t_html = html.escape(title).replace(MASK, f'<span class="m" role="img" aria-label="{html.escape(L("已隐藏", "Hidden"))}"></span>')
    foot = L(f"{who} 用 {settings.app_name} 分享", f"Shared by {who} with {settings.app_name}") if who else \
        L(f"用 {settings.app_name} 分享", f"Shared with {settings.app_name}")
    body = (f'{f"<p class=by>{by}</p>" if by else ""}<h1>{t_html}</h1><article>{render_md(shown)}</article>'
            f"<footer>{html.escape(foot)}</footer>")
    return page(plain_title, body, head)


@public_router.get("/s/{token}")
async def open_share(token: str, request: Request):
    r = await asyncio.to_thread(row_by_token, token)
    if not r or r["status"] in ("draft", "friends"):  # 只发给了朋友的，链接不开
        return gone_page(False)
    if r["status"] == "revoked":
        return gone_page(True)
    if getattr(request.state, "public", False) and not BOTS.search(request.headers.get("user-agent", "")):
        with _lock, sdb() as conn:
            conn.execute("UPDATE shares SET views=views+1 WHERE id=?", (r["id"],))  # 从外网来的、不是预览机器人的才算
    return public_page(r, request)


@public_router.get("/s/{token}/card.png")
async def open_card(token: str):
    r = await asyncio.to_thread(row_by_token, token)
    if not r or r["status"] != "live":
        return Response(status_code=404, headers=HEADERS)
    png = await asyncio.to_thread(card_png, r, "link")
    return Response(png, media_type="image/png", headers={**HEADERS, "Cache-Control": "public, max-age=300"})


# —— 给自己的接口 ——

class SharePatch(BaseModel):
    release: list[str] | None = None   # 放出来
    hide: list[str] | None = None      # 挡回去
    quote: str | None = None           # 卡片上那一句；"" = 回到默认
    title: str | None = None           # "" = 回到默认
    withQuestion: bool | None = None   # 回复前面带不带你问的那句（重新取快照，挡的地方重新认）


def _prune_drafts() -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=DRAFT_DAYS)).isoformat()
    with _lock, sdb() as conn:
        old = [r["id"] for r in conn.execute("SELECT id FROM shares WHERE status='draft' AND created_at<?", (cutoff,))]
        if old:
            conn.executemany("DELETE FROM shares WHERE id=?", [(i,) for i in old])
    for sid in old:
        drop_cards(sid)


@router.get("/api/shares")
async def list_shares(status: str = "all"):
    await asyncio.to_thread(_prune_drafts)
    q = "SELECT * FROM shares" + {"live": " WHERE status IN ('live','friends')", "all": " WHERE status IN ('live','friends','revoked')"}.get(status, "") + \
        " ORDER BY COALESCE(published_at, created_at) DESC LIMIT 300"
    with _lock, sdb() as conn:
        rows = conn.execute(q).fetchall()
    return {"ok": True, "shares": [share_json(r) for r in rows], "canLink": bool(public_url())}


@router.post("/api/shares")
async def create_share(body: ShareIn):
    sid = await asyncio.to_thread(create, body)
    return {"ok": True, "share": share_json(row(sid), full=True)}


@router.get("/api/shares/{sid}")
async def get_share(sid: str):
    return {"ok": True, "share": share_json(row(sid), full=True)}


@router.patch("/api/shares/{sid}")
async def patch_share(sid: str, body: SharePatch):
    r = row(sid)
    if r["status"] == "revoked":
        raise HTTPException(409, L("这条已经收回了", "This share was withdrawn"))
    sets: dict[str, object] = {}
    if body.withQuestion is not None and r["kind"] == "message":
        src = json.loads(r["source"] or "{}")
        kind, source, title, text = await asyncio.to_thread(
            snapshot, ShareIn(kind="message", thread=src.get("thread"), id=f"db{src.get('message')}", withQuestion=body.withQuestion))
        text = _cap(text)
        sets.update(source=json.dumps(source, ensure_ascii=False, sort_keys=True), title_src=title[:120], body=text,
                    masks=json.dumps(find_private(text), ensure_ascii=False), released="[]")
    if body.release or body.hide:
        ids = {m["id"] for m in json.loads(str(sets.get("masks") or r["masks"] or "[]"))}
        rel = set(json.loads(str(sets.get("released") or r["released"] or "[]")))
        rel |= {i for i in body.release or [] if i in ids}
        rel -= set(body.hide or [])
        sets["released"] = json.dumps(sorted(rel, key=lambda i: int(i[1:]) if i[1:].isdigit() else 0))
    if body.quote is not None:
        sets["quote"] = " ".join(body.quote.split())[:140] or None
    if body.title is not None:
        sets["title"] = " ".join(body.title.split())[:80] or None
    if sets:
        sets["updated_at"] = now_iso()
        with _lock, sdb() as conn:
            conn.execute(f"UPDATE shares SET {', '.join(f'{k}=?' for k in sets)} WHERE id=?", (*sets.values(), sid))  # noqa: S608 — 列名是上面写死的
    return {"ok": True, "share": share_json(row(sid), full=True)}


@router.post("/api/shares/{sid}/publish")
async def publish_share(sid: str):
    """发出去：链接从这一刻起能打开（要配了 public_url 外面才打得开）。"""
    r = row(sid)
    if r["status"] == "revoked":
        raise HTTPException(409, L("这条已经收回了，再分享一次会是新的链接", "This share was withdrawn; share again for a new link"))
    if r["status"] in ("draft", "friends"):  # friends：先只发给了朋友，现在开链接
        ts = now_iso()
        with _lock, sdb() as conn:
            conn.execute("UPDATE shares SET status='live', published_at=COALESCE(published_at, ?), updated_at=? WHERE id=?", (ts, ts, sid))
        await asyncio.to_thread(log_activity, L(f"分享了「{mask_title(r)}」", f"Shared \"{mask_title(r)}\""), "share")
    return {"ok": True, "share": share_json(row(sid), full=True)}


@router.delete("/api/shares/{sid}")
async def revoke_share(sid: str):
    """收回：链接页变成「已经收回了」，快照正文清空（标题留着）。"""
    r = row(sid)
    if r["status"] != "revoked":
        title = mask_title(r)
        ts = now_iso()
        with _lock, sdb() as conn:
            if r["status"] == "draft":
                conn.execute("DELETE FROM shares WHERE id=?", (sid,))
            else:
                conn.execute("UPDATE shares SET status='revoked', revoked_at=?, updated_at=?, title=?, title_src='', body='', masks='[]', "
                             "released='[]', quote=NULL WHERE id=?", (ts, ts, title, sid))
        await asyncio.to_thread(drop_cards, sid)
        if r["status"] in ("live", "friends"):
            await asyncio.to_thread(log_activity, L(f"收回了分享「{title}」", f"Withdrew the share \"{title}\""), "share")
            for hook in REVOKE_HOOKS:  # 发给过朋友的：那边也收回
                await asyncio.to_thread(hook, sid)
    return {"ok": True}


@router.get("/api/shares/{sid}/card.png")
async def share_card_png(sid: str, style: str = "clean"):
    r = row(sid)
    if r["status"] == "revoked":
        raise HTTPException(409, L("这条已经收回了", "This share was withdrawn"))
    png = await asyncio.to_thread(card_png, r, style)
    return Response(png, media_type="image/png", headers={"Cache-Control": "no-store"})


@router.get("/api/shares/{sid}/card")
async def share_card_data(sid: str, style: str = "clean"):
    """同一张图，包成 data URI：app 直接拿去显示、交给系统分享面板（不用再带令牌下载文件）。"""
    r = row(sid)
    if r["status"] == "revoked":
        raise HTTPException(409, L("这条已经收回了", "This share was withdrawn"))
    png = await asyncio.to_thread(card_png, r, style)
    w, h = (1200, 630) if style == "link" else (1080, 1440)
    return {"ok": True, "dataUri": "data:image/png;base64," + base64.b64encode(png).decode(), "width": w, "height": h}
