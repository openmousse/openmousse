"""播客（2026-09-28）：「思考」里的第三块，「聊聊」的语音版。说出来，录完帮你理成笔记。

一期（episode，grava.db pod_episodes，id pe-xxxxxxxx）
- 四种录法：solo 自己讲（它只听）/ host 有主持人（你停下它才问，一次一个）/ feynman 费曼（它扮聪明的外行追问，讲完对照学习台的课件）/
  friends 约朋友（坐一起用一台手机，录完按声音分人，第一次让你认谁是谁）。
  远程一起录还没做，接口先留着（2026-09-28 定：怎么实现还没定，也许 app 里直接打电话）：以后每人一条音轨，按 track（谁）和开录后的秒数
  （对齐）传进同一期，整理时按音轨分人、不用猜声音。现在 pod_segments 是一条时间线一段接一段，要加 track 列和按时间合并的逻辑。
- 今天聊点什么：从 Zen 里还没想完的、库里笔记的「还没想清的」、学习台最近几节、截止日期、世界树里挑（每条写明从哪来），
  模型挑 4 个写成具体的问题，按天缓存；「换一批」再挑。
- 录前先聊聊：它先问一句第一反应，你答（打字或说），它按你的原话排一张提纲卡（3–5 条，只出提纲不写稿子），录的时候一直在屏幕上。
- 录：app 每停一次就是一段（pod_segments，原声 m4a 存在 <data_dir>/podcast/<期>/NNN.m4a，不进库），传上来马上转写：
  gpt-transcribe 出文字（带词表 keywords、每个词的置信度，低的标「听不准」），whisper-1 出逐句时间（verbose_json），两边按字对齐 →
  每一句 {t0, t1, text, flag}，点一句播一句。词表 = 你改过的词（pod_vocab）+ server.json 的 transcribe_prompt 里的常见词 +
  学习台的课名 + Agent 名 + 世界树的枝。档案里的人名、住址不进词表。
- 主持人：你停下（或点「让它问」）→ 等这段转完 → llm-task 按提纲和你说过的问一个（跳过、换个问法都行），顺便标出提纲讲到哪了。
- 录完：后台整理（校对同音字和专有名词 → 标题、一句话、你的原话（带时间点）、还没想清的、关键词、要不要记世界树 → 跟库里以前的笔记比
  想法变没变 → 费曼：对照学习台这一节的课件和录播，讲对 / 讲错 / 漏了），app 轮询。你改完「存进库」= 写进 笔记/ 写作/ 或 学习/<课>/，
  世界树不点不记；费曼讲错和漏了的可以一键加进学习台复习（study.py 的复习）。原声和逐字稿留在服务器上，不进库。
模型一律走 llmjson.py（OpenClaw 的 llm-task：零工具、不进任何对话；没开就临时会话一问一答）。
- 素材（podmaterials.py）：一期可以放进主对话里说的话、和朋友的聊天、文件、Zen 的想法和收藏；录前聊天、主持人追问、录完整理、费曼对照都参考。
  朋友说的只在这一期里用：存进库的笔记只写「参考了和 X 的聊天」。
- 朋友画像（people.py）：约朋友录的，开录前可以选谁在，认人时对上人（已有的人 / 朋友 / 新建）；整理完给每个人记几条（只有你看得到）。
  和同一个人再录，主持人和录前聊天能接上以前说的；「今天聊点什么」也从「下次问问」里挑。

配置（server.json 的 podcast，全部可选）：dir 原声放哪（默认 <data_dir>/podcast）、text_model（默认 gpt-transcribe）、
time_model（默认 whisper-1，填 "" 不要逐句时间）、thinking（默认 low）、model（llm-task 的模型覆盖）。转写用 transcribe_url 和 OPENAI_API_KEY。
"""
from __future__ import annotations

import asyncio
import difflib
import json
import math
import mimetypes
import re
import shutil
import sqlite3
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import httpx
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

import config
import files as files_mod
import llmjson
import people
import podmaterials
from people import PersonPick
import think
from chat import _lock, db, log_activity, now_iso
from config import settings, user_word
from i18n import L, LS

router = APIRouter()
router.include_router(podmaterials.router)
router.include_router(people.router)

MODES = ("solo", "host", "feynman", "friends")
ID_RE = re.compile(r"^pe-[0-9a-f]{8}$")
SEG_MAX = 25 * 1024 * 1024        # OpenAI 转写一个文件最多 25 MB（32 kbps 大约 1.7 小时）
TITLE_MAX = 80
OUTLINE_MAX = 6
SUGGEST_N = 4
FLAG_P = 0.4                      # 一个词的置信度低于它就标「听不准」
_ready = False
_seg_tasks: dict[tuple[str, int], asyncio.Task] = {}
_jobs: dict[str, asyncio.Task] = {}
_vocab_cache: tuple[float, list[str]] | None = None


# —— 配置和表 ——————————————————————————————————————————————————————

def cfg() -> dict:
    c = config.raw().get("podcast")
    return c if isinstance(c, dict) else {}


def audio_root() -> Path:
    d = cfg().get("dir")
    return Path(d).expanduser() if d else settings.data_dir / "podcast"


def pdb() -> sqlite3.Connection:
    global _ready
    conn = db()
    if not _ready:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS pod_episodes (id TEXT PRIMARY KEY, title TEXT NOT NULL, mode TEXT NOT NULL, source TEXT,
            outline TEXT NOT NULL DEFAULT '[]', cur INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL, created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL, duration REAL NOT NULL DEFAULT 0, result TEXT, feynman TEXT, speakers TEXT, error TEXT,
            saved_path TEXT, saved_folder TEXT, saved_at TEXT, tree TEXT, review_at TEXT);
        CREATE TABLE IF NOT EXISTS pod_segments (episode TEXT NOT NULL, idx INTEGER NOT NULL, file TEXT NOT NULL, duration REAL NOT NULL DEFAULT 0,
            status TEXT NOT NULL, text TEXT, sentences TEXT, error TEXT, created_at TEXT NOT NULL, PRIMARY KEY (episode, idx));
        CREATE TABLE IF NOT EXISTS pod_turns (id INTEGER PRIMARY KEY AUTOINCREMENT, episode TEXT NOT NULL, phase TEXT NOT NULL,
            role TEXT NOT NULL, text TEXT NOT NULL, at REAL, voice REAL, status TEXT, created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS pod_turns_ep ON pod_turns (episode, id);
        CREATE TABLE IF NOT EXISTS pod_vocab (term TEXT PRIMARY KEY, source TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS pod_suggest (day TEXT PRIMARY KEY, items TEXT NOT NULL, created_at TEXT NOT NULL);
        """)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(pod_episodes)")}
        if "people" not in cols:  # 朋友画像（people.py）：people = 开录前选的谁在 [人的 id]，speaker_people = 认人时对上的 {声音: 人的 id}
            conn.execute("ALTER TABLE pod_episodes ADD COLUMN people TEXT")
            conn.execute("ALTER TABLE pod_episodes ADD COLUMN speaker_people TEXT")
        _ready = True
    return conn


def load(eid: str) -> sqlite3.Row:
    if not ID_RE.match(eid or ""):
        raise HTTPException(404, L("没有这一期", "No such episode"))
    with _lock, pdb() as conn:
        r = conn.execute("SELECT * FROM pod_episodes WHERE id=?", (eid,)).fetchone()
    if not r:
        raise HTTPException(404, L("没有这一期", "No such episode"))
    return r


def touch(eid: str, **cols) -> None:
    cols["updated_at"] = now_iso()
    sets = ", ".join(f"{k}=?" for k in cols)
    with _lock, pdb() as conn:
        conn.execute(f"UPDATE pod_episodes SET {sets} WHERE id=?", (*cols.values(), eid))  # noqa: S608 — 列名是这里写死的


def jload(v, default):
    try:
        x = json.loads(v) if v else default
    except ValueError:
        return default
    return x if isinstance(x, type(default)) else default


def clock(t: float | None) -> str:
    t = max(0, int(round(t or 0)))
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60}:{t % 60:02d}"


def uw() -> str:
    return user_word().strip()


def shape(example: str) -> str:
    """提示词末尾写明要回的 JSON 的样子（schema 只卡最外层，字段靠这句）。"""
    return "\n" + LS("只回一个 JSON，不要别的文字，样子：", "Reply with ONE JSON object only, shaped like: ") + example


# —— 断句、对齐、听不准 ————————————————————————————————————————————

ENDERS = set("。！？!?；;…")
CLOSERS = set("」』”\"'）)】")
KEEP = re.compile(r"[^0-9a-z㐀-鿿豈-﫿]")


def norm(s: str) -> str:
    return KEEP.sub("", s.lower())


def split_spans(text: str) -> list[tuple[int, int]]:
    """一段转写切成句子：[(起, 止)]（原文里的位置）。中文按句末标点，英文按句号 + 空格；太短的并进上一句，太长的在逗号处切开。"""
    spans, start, n = [], 0, len(text)
    for i, ch in enumerate(text):
        nxt = text[i + 1] if i + 1 < n else ""
        cut = False
        if ch in ENDERS and nxt not in ENDERS and nxt not in CLOSERS:
            cut = True
        elif ch in CLOSERS and i and text[i - 1] in ENDERS:
            cut = True
        elif ch == "." and (nxt in (" ", "\n", "")) and not re.search(r"\b(?:e\.g|i\.e|vs|etc|Mr|Ms|Dr|No)\.$", text[start:i + 1]):
            cut = True
        elif ch == "\n":
            cut = True
        if cut:
            if text[start:i + 1].strip():
                spans.append((start, i + 1))
            start = i + 1
    if text[start:].strip():
        spans.append((start, n))
    merged: list[list[int]] = []
    for a, b in spans:
        if merged and len(norm(text[a:b])) < 4:
            merged[-1][1] = b
        else:
            merged.append([a, b])
    out: list[tuple[int, int]] = []
    for a, b in merged:
        while b - a > 140:
            seg = text[a:a + 120]
            cut = max(seg.rfind("，"), seg.rfind(","), seg.rfind("、"))
            if cut < 30:
                break
            out.append((a, a + cut + 1))
            a += cut + 1
        out.append((a, b))
    return [(a, b) for a, b in out if text[a:b].strip()]


def low_spans(text: str, logprobs: list | None) -> list[tuple[int, int]]:
    """置信度低的词在原文里的位置（标点不算；挨着的并成一个词）。logprobs 的 token 按顺序拼起来就是原文。"""
    out: list[list[int]] = []
    pos = 0
    for tk in logprobs or []:
        tok = str((tk or {}).get("token") or "")
        if not tok:
            continue
        at = text.find(tok, pos)
        if at < 0:
            continue
        end = at + len(tok)
        pos = end
        try:
            p = math.exp(float(tk.get("logprob") or 0))
        except (TypeError, ValueError, OverflowError):
            continue
        if p >= FLAG_P or not norm(tok):
            continue
        a = at + (len(tok) - len(tok.lstrip()))
        while a > 0 and text[a - 1].isascii() and text[a - 1].isalnum():  # 英文 token 常是半个词：扩到整个词
            a -= 1
        while end < len(text) and text[end].isascii() and text[end].isalnum():
            end += 1
        if out and a - out[-1][1] <= 1:
            out[-1][1] = max(out[-1][1], end)
        else:
            out.append([a, end])
    return [(a, b) for a, b in out]


def align(text: str, spans: list[tuple[int, int]], wsegs: list[dict], dur: float) -> list[tuple[float, float]]:
    """每一句的 (起, 止) 秒。文字来自 gpt-transcribe，时间来自 whisper 的分段：两边都去掉标点空格按字对齐（difflib），
    对不上的字在前后对上的之间插值；没有 whisper 的时间就按字数平分这一段。"""
    A: list[str] = []
    bounds = []
    for a, b in spans:
        a0 = len(A)
        A.extend(norm(text[a:b]))
        bounds.append((a0, len(A)))
    B: list[str] = []
    times: list[float] = []
    for sg in wsegs or []:
        s = norm(str(sg.get("text") or ""))
        t0, t1 = float(sg.get("start") or 0), float(sg.get("end") or 0)
        for k, ch in enumerate(s):
            B.append(ch)
            times.append(t0 + (max(t1, t0) - t0) * (k + 0.5) / max(1, len(s)))
    n = len(A)
    at: list[float | None] = [None] * n
    if n and B:
        for a, b, size in difflib.SequenceMatcher(None, A, B, autojunk=False).get_matching_blocks():
            for k in range(size):
                at[a + k] = times[b + k]
    known = [(i, t) for i, t in enumerate(at) if t is not None]
    if len(known) < max(2, n // 10):  # 几乎对不上（换了语言、whisper 没转出来）：按字数平分
        total = dur or (times[-1] if times else 0) or n * 0.25
        at = [total * (i + 0.5) / max(1, n) for i in range(n)]
    else:
        first_i, first_t = known[0]
        last_i, last_t = known[-1]
        rate = (last_t - first_t) / max(1, last_i - first_i) if last_i > first_i else 0.25
        for i in range(first_i):
            at[i] = max(0.0, first_t - (first_i - i) * rate)
        for i in range(last_i + 1, n):
            at[i] = min(dur or 1e9, last_t + (i - last_i) * rate)
        for (i0, t0), (i1, t1) in zip(known, known[1:]):
            for i in range(i0 + 1, i1):
                at[i] = t0 + (t1 - t0) * (i - i0) / (i1 - i0)
    out: list[tuple[float, float]] = []
    prev = 0.0
    for a0, a1 in bounds:
        if a1 <= a0:
            out.append((prev, prev + 0.5))
            continue
        t0 = max(prev, float(at[a0] or 0) - 0.3)
        t1 = max(t0 + 0.4, float(at[a1 - 1] or 0) + 0.4)
        if dur:
            t1 = min(t1, dur)
            t0 = min(t0, max(0.0, t1 - 0.4))
        out.append((round(t0, 2), round(t1, 2)))
        prev = t0
    return out


# —— 词表 ——————————————————————————————————————————————————————————

def vocab_terms() -> list[str]:
    """转写的词表（给 gpt-transcribe 的 keywords、whisper 的 prompt）：你改过的词在前。缓存 5 分钟。"""
    global _vocab_cache
    if _vocab_cache and time.time() - _vocab_cache[0] < 300:
        return _vocab_cache[1]
    terms: list[str] = []
    with _lock, pdb() as conn:
        terms += [r["term"] for r in conn.execute("SELECT term FROM pod_vocab ORDER BY created_at DESC LIMIT 40")]
        for sql in ("SELECT name FROM groups ORDER BY position", "SELECT title AS name FROM side_chats WHERE archived=0 ORDER BY updated_at DESC LIMIT 10"):
            try:
                terms += [r["name"] for r in conn.execute(sql) if len(r["name"] or "") <= 20]
            except sqlite3.Error:
                pass
    terms += [settings.app_name, "OpenMousse"]
    m = re.search(r"(?:常见词|words?)\s*[：:]\s*(.+)", settings.transcribe_prompt or "", re.I | re.S)
    if m:
        terms += [x.strip() for x in re.split(r"[、，,;；。\n]", m.group(1)) if x.strip()]
    try:
        import study
        terms += study.courses()
    except Exception:  # noqa: BLE001 — 没配学习台
        pass
    try:
        terms += think.branches()
    except Exception:  # noqa: BLE001
        pass
    seen, out = set(), []
    for t in terms:
        t = str(t).strip()[:40]
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    _vocab_cache = (time.time(), out[:60])
    return _vocab_cache[1]


def add_vocab(terms: list[str], source: str = "fix") -> None:
    global _vocab_cache
    terms = [t.strip()[:40] for t in terms if t and t.strip() and len(norm(t)) >= 2]
    if not terms:
        return
    with _lock, pdb() as conn:
        conn.executemany("INSERT OR REPLACE INTO pod_vocab (term, source, created_at) VALUES (?,?,?)", [(t, source, now_iso()) for t in terms])
    _vocab_cache = None


# —— 转写 ——————————————————————————————————————————————————————————

async def transcribe(path: Path, *, timestamps: bool = True, diarize: bool = False) -> dict:
    """→ {text, sentences: [{t0, t1, text, flag}], duration, speakers?}。文字和时间两个请求同时发；文字那边失败就退回 files.py 的模型，
    时间那边失败就按字数平分。diarize：坐一起录的，另用 gpt-4o-transcribe-diarize 分出谁说的（按字对齐到句子上）。"""
    key = files_mod.env_key("OPENAI_API_KEY")
    url = files_mod.TRANSCRIBE_URL
    head = {"Authorization": f"Bearer {key}"}
    raw = path.read_bytes()
    mime = AUDIO_TYPES.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0] or "audio/mp4"
    terms = await asyncio.to_thread(vocab_terms)  # 要跑世界树的脚本：别卡住服务
    text_model = str(cfg().get("text_model") or "gpt-transcribe")
    time_model = str(cfg().get("time_model") if cfg().get("time_model") is not None else "whisper-1")

    async with httpx.AsyncClient(timeout=httpx.Timeout(240, connect=15)) as client:
        async def post(data: dict) -> httpx.Response:
            return await client.post(url, headers=head, files={"file": (path.name, raw, mime)}, data=data)

        async def text_call() -> dict:
            tries = [{"model": text_model, "response_format": "json", "include[]": ["logprobs"], "keywords[]": terms[:50],
                      **({"prompt": settings.transcribe_prompt} if settings.transcribe_prompt else {})}]
            tries += [{"model": m, "response_format": "json", **({"prompt": settings.transcribe_prompt} if settings.transcribe_prompt else {})}
                      for m in files_mod.TRANSCRIBE_MODELS if m != text_model]
            last = ""
            for d in tries:
                r = await post(d)
                if r.status_code == 200:
                    return r.json()
                last = f"{d['model']}: HTTP {r.status_code} {r.text[:160]}"
            raise RuntimeError(last)

        async def time_call() -> dict:
            prompt = "、".join(terms[:25])[:400]
            r = await post({"model": time_model, "response_format": "verbose_json", "timestamp_granularities[]": ["segment"],
                            **({"prompt": prompt} if prompt else {})})
            if r.status_code != 200:
                raise RuntimeError(f"{time_model}: HTTP {r.status_code}")
            return r.json()

        async def diarize_call() -> dict:
            r = await post({"model": "gpt-4o-transcribe-diarize", "response_format": "diarized_json", "chunking_strategy": "auto"})
            if r.status_code != 200:
                raise RuntimeError(f"diarize: HTTP {r.status_code} {r.text[:160]}")
            return r.json()

        calls = [text_call()] + ([time_call()] if timestamps and time_model else []) + ([diarize_call()] if diarize else [])
        got = await asyncio.gather(*calls, return_exceptions=True)
    tx = got[0]
    tm = got[1] if timestamps and time_model else None
    dz = got[-1] if diarize else None
    wsegs = (tm.get("segments") or []) if isinstance(tm, dict) else []
    dur = float((tm or {}).get("duration") or 0) if isinstance(tm, dict) else 0.0
    if isinstance(dz, dict):
        dur = dur or float(dz.get("duration") or 0)
    if isinstance(tx, dict) and str(tx.get("text") or "").strip():
        text = str(tx["text"]).strip()
        lows = low_spans(text, tx.get("logprobs"))
    elif wsegs:  # 文字那边没成：用 whisper 自己的
        text = "\n".join(str(s.get("text") or "").strip() for s in wsegs if str(s.get("text") or "").strip())
        lows = []
    elif isinstance(dz, dict) and str(dz.get("text") or "").strip():
        text = "\n".join(str(s.get("text") or "").strip() for s in dz.get("segments") or [])
        lows = []
    else:
        raise RuntimeError(str(tx) if isinstance(tx, Exception) else "empty transcript")
    spans = split_spans(text)
    times = align(text, spans, wsegs or ((dz or {}).get("segments") if isinstance(dz, dict) else []) or [], dur)
    sentences = []
    for (a, b), (t0, t1) in zip(spans, times):
        flag = [text[x:y].strip() for x, y in lows if a <= x < b and text[x:y].strip()]
        sentences.append({"t0": t0, "t1": t1, "text": text[a:b].strip(), **({"flag": "、".join(dict.fromkeys(flag))[:60]} if flag else {})})
    out = {"text": text, "sentences": sentences, "duration": dur or (times[-1][1] if times else 0)}
    if isinstance(dz, dict) and dz.get("segments"):
        out["speakers"] = speakers_for(text, spans, dz["segments"])
        for s, who in zip(sentences, out["speakers"]):
            s["speaker"] = who
        out.pop("speakers")
    return out


def speakers_for(text: str, spans: list[tuple[int, int]], dsegs: list[dict]) -> list[str]:
    """坐一起录的：每一句是谁说的（diarize 的分段按字对齐过来，一句里谁的字多算谁）。"""
    A = [(i, ch) for i, (a, b) in enumerate(spans) for ch in norm(text[a:b])]
    B, who = [], []
    for sg in dsegs:
        for ch in norm(str(sg.get("text") or "")):
            B.append(ch)
            who.append(str(sg.get("speaker") or "A"))
    votes: list[dict[str, int]] = [{} for _ in spans]
    for a, b, size in difflib.SequenceMatcher(None, [c for _, c in A], B, autojunk=False).get_matching_blocks():
        for k in range(size):
            v = votes[A[a + k][0]]
            v[who[b + k]] = v.get(who[b + k], 0) + 1
    out, last = [], "A"
    for v in votes:
        last = max(v, key=v.get) if v else last
        out.append(last)
    return out


# —— 一段录音 ————————————————————————————————————————————————————

AUDIO_TYPES = {".m4a": "audio/mp4", ".mp4": "audio/mp4", ".aac": "audio/aac", ".wav": "audio/wav", ".mp3": "audio/mpeg",
               ".webm": "audio/webm", ".ogg": "audio/ogg", ".oga": "audio/ogg", ".flac": "audio/flac"}  # OpenAI 转写收的格式


def audio_ext(f: UploadFile) -> str:
    """按上传的文件名 / 类型定扩展名（iPhone 录的是 m4a，网页版是 webm）；认不出就当 m4a。"""
    ext = Path(f.filename or "").suffix.lower()
    if ext in AUDIO_TYPES:
        return ext
    ct = (f.content_type or "").split(";")[0].strip().lower()
    return next((e for e, m in AUDIO_TYPES.items() if m == ct), ".m4a")


def seg_path(eid: str, idx: int, ext: str = ".m4a") -> Path:
    return audio_root() / eid / f"{idx:03d}{ext}"


async def run_segment(eid: str, idx: int) -> None:
    with _lock, pdb() as conn:
        r = conn.execute("SELECT * FROM pod_segments WHERE episode=? AND idx=?", (eid, idx)).fetchone()
        ep = conn.execute("SELECT mode FROM pod_episodes WHERE id=?", (eid,)).fetchone()
    if not r:
        return
    try:
        res = await transcribe(Path(r["file"]), diarize=bool(ep and ep["mode"] == "friends"))
        with _lock, pdb() as conn:
            conn.execute("UPDATE pod_segments SET status='done', text=?, sentences=?, duration=CASE WHEN ?>0 THEN ? ELSE duration END, error=NULL "
                         "WHERE episode=? AND idx=?", (res["text"], json.dumps(res["sentences"], ensure_ascii=False),
                                                       res["duration"], res["duration"], eid, idx))
    except Exception as exc:  # noqa: BLE001 — 这一段转不出来：标出来，整理时重试一次
        with _lock, pdb() as conn:
            conn.execute("UPDATE pod_segments SET status='failed', error=? WHERE episode=? AND idx=?", (str(exc)[:300], eid, idx))
    finally:
        _seg_tasks.pop((eid, idx), None)
    sync_duration(eid)


def start_segment(eid: str, idx: int) -> asyncio.Task:
    t = _seg_tasks.get((eid, idx))
    if t and not t.done():
        return t
    with _lock, pdb() as conn:
        conn.execute("UPDATE pod_segments SET status='transcribing', error=NULL WHERE episode=? AND idx=?", (eid, idx))
    t = asyncio.create_task(run_segment(eid, idx))
    _seg_tasks[(eid, idx)] = t
    return t


async def settle_segments(eid: str, timeout: float = 45, retry_failed: bool = False) -> None:
    """等这一期还在转的段转完（服务重启丢了任务的、要重试的失败段重新开始）。"""
    with _lock, pdb() as conn:
        rows = conn.execute("SELECT idx, status FROM pod_segments WHERE episode=?", (eid,)).fetchall()
    tasks = []
    for r in rows:
        if r["status"] == "transcribing" or (retry_failed and r["status"] == "failed"):
            tasks.append(start_segment(eid, r["idx"]))
    if tasks:
        await asyncio.wait(tasks, timeout=timeout)


def sync_duration(eid: str) -> None:
    with _lock, pdb() as conn:
        d = conn.execute("SELECT COALESCE(SUM(duration),0) FROM pod_segments WHERE episode=?", (eid,)).fetchone()[0]
        conn.execute("UPDATE pod_episodes SET duration=? WHERE id=?", (d, eid))


def segments_of(eid: str) -> list[dict]:
    """每段：{idx, offset, duration, status, sentences}（offset = 前面几段加起来的秒数，一期里的时间 = offset + 句子的 t0）。"""
    with _lock, pdb() as conn:
        rows = conn.execute("SELECT * FROM pod_segments WHERE episode=? ORDER BY idx", (eid,)).fetchall()
    out, off = [], 0.0
    for r in rows:
        sents = jload(r["sentences"], [])
        out.append({"idx": r["idx"], "offset": round(off, 2), "duration": round(r["duration"] or 0, 2), "status": r["status"],
                    "error": r["error"], "url": f"/api/podcast/episodes/{eid}/audio/{r['idx']}",
                    "sentences": [{"i": i, **s} for i, s in enumerate(sents)]})
        off += r["duration"] or 0
    return out


def speaker_name(label: str, who: dict | None) -> str:
    name = (who or {}).get(label) or label
    return (uw() or L("我", "me")) if name == "@me" else name


def flat_sentences(segs: list[dict], who: dict | None = None) -> list[dict]:
    """一期的所有句子：{id: "段.句", at: 一期里的秒, text, label?, speaker?}（坐一起录的：label 是分出来的 A / B，speaker 是认过的名字）。"""
    out = []
    for s in segs:
        for x in s["sentences"]:
            item = {"id": f"{s['idx']}.{x['i']}", "at": round(s["offset"] + x["t0"], 1), "text": x["text"]}
            if x.get("speaker"):
                item["label"] = x["speaker"]
                item["speaker"] = speaker_name(x["speaker"], who)
            out.append(item)
    return out


def transcript_lines(sents: list[dict], limit: int = 30000) -> list[str]:
    lines = [f"[{x['id']} · {clock(x['at'])}]{(' ' + x['speaker'] + '：') if x.get('speaker') else ' '}{x['text']}" for x in sents]
    total, keep = 0, []
    for line in reversed(lines):  # 太长就留后面的（主持人问的时候最近说的最要紧），开头再补几句
        total += len(line)
        if total > limit:
            break
        keep.append(line)
    keep.reverse()
    if len(keep) < len(lines):
        keep = lines[:5] + ["…"] + keep
    return keep


# —— 一期的 JSON ——————————————————————————————————————————————————

def chip_of(r: sqlite3.Row) -> dict | None:
    fy = jload(r["feynman"], {})
    if r["status"] == "recording":
        return {"text": L("录到一半", "Half recorded"), "tone": "gold"}
    if r["status"] == "processing":
        return {"text": L("在整理", "Organizing"), "tone": "cyan"}
    if r["status"] == "naming":
        return {"text": L("认一下谁是谁", "Who's who?"), "tone": "gold"}
    if r["status"] == "failed":
        return {"text": L("没整理成", "Didn't finish"), "tone": "red"}
    if fy and r["status"] in ("ready", "saved"):
        wrong, missed = len(fy.get("wrong") or []), len(fy.get("missed") or [])
        if wrong:
            return {"text": L(f"讲错 {wrong} 处", f"{wrong} wrong"), "tone": "red"}
        if missed:
            return {"text": L(f"漏了 {missed} 处", f"{missed} missed"), "tone": "gold"}
    if r["status"] == "saved":
        where = {"notes": L("笔记", "Notes"), "writing": L("写作", "Writing"), "study": L("学习", "Study")}.get(r["saved_folder"] or "", "")
        return {"text": L(f"存进了{where}", f"Saved to {where}") if where else L("存进了库", "Saved"), "tone": "gray"}
    if r["status"] == "ready":
        return {"text": L("还没想完", "Not done yet"), "tone": "gold"}
    return None


def brief(r: sqlite3.Row) -> dict:
    return {"id": r["id"], "title": r["title"], "mode": r["mode"], "status": r["status"], "duration": round(r["duration"] or 0, 1),
            "createdAt": r["created_at"], "updatedAt": r["updated_at"], "source": jload(r["source"], {}), "chip": chip_of(r)}


def present_people(r: sqlite3.Row) -> list[str]:
    """这一期有谁（人的 id）：开录前选的 + 认人时对上的。"""
    return list(dict.fromkeys(jload(r["people"], []) + list(jload(r["speaker_people"], {}).values())))


def episode_json(eid: str) -> dict:
    r = load(eid)
    with _lock, pdb() as conn:
        turns = conn.execute("SELECT * FROM pod_turns WHERE episode=? ORDER BY id", (eid,)).fetchall()
    who = people.names(present_people(r))
    return {**brief(r), "outline": jload(r["outline"], []), "cur": r["cur"], "error": r["error"],
            "people": [{"id": k, "name": v} for k, v in who.items()], "speakerPeople": jload(r["speaker_people"], {}),
            "segments": segments_of(eid), "speakers": jload(r["speakers"], {}),
            "turns": [{"id": t["id"], "phase": t["phase"], "role": t["role"], "text": t["text"], "at": t["at"], "voice": t["voice"],
                       "status": t["status"], "createdAt": t["created_at"]} for t in turns],
            "result": jload(r["result"], {}) or None, "feynman": jload(r["feynman"], {}) or None,
            "saved": {"path": r["saved_path"], "folder": r["saved_folder"], "at": r["saved_at"], "tree": r["tree"],
                      "obsidian": obsidian_url(r["saved_path"])} if r["saved_path"] else None,
            "reviewAt": r["review_at"], "materials": podmaterials.count(eid)}


def obsidian_url(rel_path: str | None) -> str | None:
    name = think.cfg().get("obsidian_vault")
    if not rel_path or not name:
        return None
    from urllib.parse import quote
    return f"obsidian://open?vault={quote(str(name))}&file={quote(rel_path[:-3] if rel_path.endswith('.md') else rel_path)}"


# —— 今天聊点什么 ——————————————————————————————————————————————————

def when_label(due: datetime, now: datetime) -> str:
    days = (due.date() - now.date()).days
    hm = due.strftime("%H:%M")
    if days <= 0:
        return L(f"今天 {hm} 截止", f"due today {hm}")
    if days == 1:
        return L(f"明天 {hm} 截止", f"due tomorrow {hm}")
    wd = L("一二三四五六日"[due.weekday()], ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][due.weekday()])
    return L(f"周{wd} {hm} 截止", f"due {wd} {hm}")


def open_questions() -> list[dict]:
    """库里最近 60 天的笔记里「还没想清的」那几条。"""
    out = []
    cutoff = time.time() - 60 * 86400
    for p in sorted(think.note_files(), key=lambda p: p.stat().st_mtime, reverse=True):
        if p.stat().st_mtime < cutoff or len(out) >= 12:
            break
        try:
            _, body, _ = think.parse_note(p.read_text(encoding="utf8", errors="replace"))
        except OSError:
            continue
        m = re.search(r"^##\s*(?:还没想清的|Still open)\s*\n(.*?)(?=^##\s|\Z)", body, re.M | re.S)
        if not m:
            continue
        for line in m.group(1).splitlines():
            q = re.sub(r"^\s*[-*]\s*", "", line).strip()
            if q and len(out) < 12:
                out.append({"kind": "open", "text": q[:200], "label": L(f"「{p.stem[:24]}」里你还没想清的", f"still open in “{p.stem[:24]}”")})
    return out


def study_units() -> list[dict]:
    try:
        import study
        cs = study.courses()
    except Exception:  # noqa: BLE001 — 没配学习台
        return []
    out = []
    for c in cs:
        try:
            tree = study.course_tree(c)
        except Exception:  # noqa: BLE001
            continue
        pages = [p for m in tree["modules"] for p in m["pages"]] + tree["pages"]
        pages = sorted([p for p in pages if p.get("session")], key=lambda p: -p["session"])[:2]
        for p in pages:
            out.append({"kind": "study", "text": p["title"], "course": c, "page": p["path"],
                        "label": L(f"学习台 · {c} 第 {p['session']} 节", f"Study desk · {c} session {p['session']}")})
    return out


def deadline_items(now: datetime) -> list[dict]:
    out = []
    try:
        import schedule
        items = schedule.build_remember().get("items") or []
    except Exception:  # noqa: BLE001
        items = []
    for e in items:
        if not e.get("date") or e.get("done") or e.get("kind") == "money":
            continue
        try:
            due = datetime.fromisoformat(f"{e['date']}T{(e.get('start') or '23:59')[:5]}").replace(tzinfo=settings.tz)
        except ValueError:
            continue
        if now <= due <= now + timedelta(days=6):
            out.append({"kind": "deadline", "text": str(e.get("title") or "")[:160], "label": when_label(due, now)})
    return out[:8]


def tree_items() -> list[dict]:
    try:
        import memtree
        snap = memtree.snapshot()
    except Exception:  # noqa: BLE001 — 没有世界树
        return []
    return [{"kind": "tree", "text": x["text"][:200], "label": L(f"世界树 · {x['branch']}", f"Memory tree · {x['branch']}")}
            for x in (snap.get("leaves") or [])[:12] if x.get("status") == "active"]


def candidates() -> list[dict]:
    now = datetime.now(settings.tz)
    cands: list[dict] = []
    for tp in think.topics_open()[:6]:
        cands.append({"kind": "open", "text": tp["title"], "label": L("Zen 里还没想完的", "unfinished in Zen")})
    cands += open_questions() + study_units() + deadline_items(now) + tree_items() + people.suggest_candidates()
    return cands


SUGGEST_SCHEMA = {"type": "object", "properties": {"items": {"type": "array"}}, "required": ["items"]}
async def make_suggestions(exclude: list[str]) -> list[dict]:
    cands = await asyncio.to_thread(candidates)
    if not cands:
        return []
    with _lock, pdb() as conn:
        recent = [r["title"] for r in conn.execute("SELECT title FROM pod_episodes ORDER BY created_at DESC LIMIT 12")]
    u = uw()
    prompt = LS(
        f"你是{u}的播客搭档。从候选里挑 {SUGGEST_N} 个今天值得录一期的话题（来源尽量不重样），每个写成一个具体的问题或说法（像一期节目的名字，"
        "20 字以内，用 TA 的语言），配一种录法：host 有主持人（想不清、想聊开的）、feynman 费曼（只给学习台来的：用自己的话把一个概念讲给外行听）、"
        "solo 自己讲（想法已经比较清楚、想说出来的）。ref 是候选的序号。别挑和最近录过的重复的，也别挑 exclude 里的。"
        "kind=person 的是朋友画像里的（在做的事、下次问问）：最多挑 1 个，写成约这个朋友聊（「约小林聊聊：实习找得怎么样了」），录法 friends；提到人写名字，不写「他 / 她」。",
        f"You're {u}'s podcast partner. Pick {SUGGEST_N} topics worth an episode today from the candidates (mix the sources). Write each as a "
        "concrete question or claim (an episode title, under 12 words, in their language) and give it a mode: host (the AI host asks follow-ups; "
        "for things still unclear), feynman (only for study-desk items: explain one concept to a smart layperson), solo (they already know what "
        "they think). ref is the candidate's index. Skip anything close to the recent episodes or in exclude. kind=person items come from "
        "notes about friends (what they're up to, what to ask next): pick at most 1, written as recording with that friend "
        "(\"Catch up with Lin: how's the internship search?\"), mode friends.")
    prompt += shape('{"items": [{"ref": 3, "title": "…", "mode": "host"}]}')
    items = [{"ref": i, "kind": c["kind"], "source": c["label"], "text": c["text"]} for i, c in enumerate(cands)]
    got, _ = await llmjson.ask(prompt, {"candidates": items, "recent": recent, "exclude": exclude[:20]}, SUGGEST_SCHEMA, timeout=60,
                               thinking=str(cfg().get("thinking") or "low"), model=cfg().get("model"),
                               # 退回带工具的对话回合时不带朋友画像来的候选（ref 照旧是原来的序号）
                               fallback_input={"candidates": [x for x in items if x["kind"] != "person"], "recent": recent, "exclude": exclude[:20]})
    if isinstance(got, list):  # 不带 schema 那次常直接回一个列表
        got = {"items": got}
    out, used = [], set()
    for it in (got or {}).get("items") or [] if isinstance(got, dict) else []:
        if not isinstance(it, dict):
            continue
        try:
            ref = int(it.get("ref"))
        except (TypeError, ValueError):
            continue
        if not 0 <= ref < len(cands) or ref in used:
            continue
        c = cands[ref]
        mode = str(it.get("mode") or "host")
        mode = mode if mode in ("solo", "host", "feynman") else "host"
        if mode == "feynman" and c["kind"] != "study":
            mode = "host"
        if c["kind"] == "person":  # 朋友画像来的：约他一起录（一期最多一个）
            if any(o["source"].get("kind") == "person" for o in out):
                continue
            mode = "friends"
        title = re.sub(r"\s+", " ", str(it.get("title") or c["text"])).strip()[:TITLE_MAX]
        used.add(ref)
        out.append({"title": title, "mode": mode, "source": {k: c[k] for k in ("kind", "label", "course", "page", "person", "name") if c.get(k)}})
    return out[:SUGGEST_N]


@router.get("/api/podcast")
async def home():
    day = datetime.now(settings.tz).date().isoformat()
    with _lock, pdb() as conn:
        s = conn.execute("SELECT items, created_at FROM pod_suggest WHERE day=?", (day,)).fetchone()
        eps = conn.execute("SELECT * FROM pod_episodes ORDER BY created_at DESC LIMIT 40").fetchall()
    import study
    return {"ok": True, "suggestions": jload(s["items"], []) if s else None, "suggestedAt": s["created_at"] if s else None,
            "episodes": [brief(r) for r in eps], "study": bool(study.courses()),
            "materials": True, "people": True}  # app 看这两个决定显不显示长按「放进播客」、我 → 朋友画像（老服务器没有）


class SuggestIn(BaseModel):
    exclude: list[str] = []


@router.post("/api/podcast/suggest")
async def suggest(body: SuggestIn):
    try:
        items = await make_suggestions(body.exclude)
    except llmjson.LLMError as e:
        raise HTTPException(502, L(f"没挑出来：{e}", f"Couldn't pick topics: {e}")) from e
    day = datetime.now(settings.tz).date().isoformat()
    with _lock, pdb() as conn:
        conn.execute("INSERT OR REPLACE INTO pod_suggest (day, items, created_at) VALUES (?,?,?)", (day, json.dumps(items, ensure_ascii=False), now_iso()))
    return {"ok": True, "suggestions": items}


# —— 建一期、改、删 ——————————————————————————————————————————————————

class EpisodeIn(BaseModel):
    title: str
    mode: str = "host"
    source: dict | None = None
    people: list[PersonPick] | None = None   # 约朋友：开录前选的谁在（主持人拿他们的画像）


@router.post("/api/podcast/episodes")
async def create(body: EpisodeIn):
    title = re.sub(r"\s+", " ", body.title).strip()[:TITLE_MAX]
    if not title:
        raise HTTPException(400, L("先说这期聊什么", "Say what this episode is about"))
    if body.mode not in MODES:
        raise HTTPException(400, L("没有这种录法", "Unknown mode"))
    src = {k: str(v)[:200] for k, v in (body.source or {}).items() if k in ("kind", "label", "course", "page", "person", "name") and v}
    present: list[str] = []
    for pick in (body.people or [])[:8] if body.mode == "friends" else []:
        try:
            pid, _ = people.resolve(pick)
        except HTTPException as e:
            if e.status_code == 404:  # 那个人删了（比如昨天挑的「约小林聊…」）：不算
                continue
            raise
        if pid:
            present.append(pid)
    eid = f"pe-{uuid.uuid4().hex[:8]}"
    with _lock, pdb() as conn:
        conn.execute("INSERT INTO pod_episodes (id, title, mode, source, status, created_at, updated_at, people) VALUES (?,?,?,?,?,?,?,?)",
                     (eid, title, body.mode, json.dumps(src, ensure_ascii=False), "prep", now_iso(), now_iso(), json.dumps(list(dict.fromkeys(present)))))
    return {"ok": True, "episode": episode_json(eid)}


@router.get("/api/podcast/episodes/{eid}")
async def get_episode(eid: str):
    r = load(eid)
    if r["status"] == "processing" and eid not in _jobs:  # 服务重启把整理的任务丢了：接着做
        start_job(eid)
    if r["status"] == "recording":
        with _lock, pdb() as conn:
            stale = conn.execute("SELECT idx FROM pod_segments WHERE episode=? AND status='transcribing'", (eid,)).fetchall()
        for s in stale:
            if (eid, s["idx"]) not in _seg_tasks:
                start_segment(eid, s["idx"])
    return {"ok": True, "episode": episode_json(eid)}


class EpisodePatch(BaseModel):
    title: str | None = None
    mode: str | None = None
    outline: list[str] | None = None
    done: list[int] | None = None      # 提纲里讲完了的（自己讲的时候点一下划掉）
    cur: int | None = None
    speakers: dict[str, str] | None = None  # 坐一起录的：{"A": "@me", "B": "小林"}（@me = 你自己）
    people: dict[str, PersonPick] | None = None  # 认人时对上的人 {"B": {id} | {friend} | {name} | {name, skip}}：名字跟着人走


@router.patch("/api/podcast/episodes/{eid}")
async def patch_episode(eid: str, body: EpisodePatch):
    r = load(eid)
    cols: dict = {}
    if body.people is not None:  # 对上人：名字用人的名字，记下 {声音: 人}（skip 的只留名字）
        spk = dict(body.speakers if body.speakers is not None else jload(r["speakers"], {}))
        sp = {}
        for label, pick in list(body.people.items())[:8]:
            if spk.get(label) == "@me":
                continue
            pid, name = people.resolve(pick)
            spk[label] = name
            if pid:
                sp[str(label)[:4]] = pid
        body.speakers = spk
        cols["speaker_people"] = json.dumps(sp)
    if body.title is not None and body.title.strip():
        cols["title"] = re.sub(r"\s+", " ", body.title).strip()[:TITLE_MAX]
    if body.mode is not None:
        if body.mode not in MODES:
            raise HTTPException(400, L("没有这种录法", "Unknown mode"))
        cols["mode"] = body.mode
    outline = jload(r["outline"], [])
    if body.outline is not None:
        old = {o["text"]: o.get("done", False) for o in outline}
        outline = [{"text": t.strip()[:120], "done": old.get(t.strip()[:120], False)} for t in body.outline if t.strip()][:OUTLINE_MAX]
    if body.done is not None:
        outline = [{**o, "done": i in body.done} for i, o in enumerate(outline)]
    if body.outline is not None or body.done is not None:
        cols["outline"] = json.dumps(outline, ensure_ascii=False)
    if body.cur is not None:
        cols["cur"] = max(0, min(body.cur, max(0, len(outline) - 1)))
    if body.speakers is not None:
        cols["speakers"] = json.dumps({str(k)[:4]: str(v).strip()[:20] for k, v in body.speakers.items() if str(v).strip()}, ensure_ascii=False)
    if cols:
        touch(eid, **cols)
    if body.speakers is not None and r["status"] == "naming":  # 认完了：接着整理
        touch(eid, status="processing")
        start_job(eid)
    return {"ok": True, "episode": episode_json(eid)}


@router.delete("/api/podcast/episodes/{eid}")
async def delete_episode(eid: str):
    r = load(eid)
    for (e, i), t in list(_seg_tasks.items()):
        if e == eid:
            t.cancel()
    job = _jobs.pop(eid, None)
    if job:
        job.cancel()
    podmaterials.drop_episode(eid)
    people.revert_episode(eid)  # 从这一期记的画像一起撤掉（你改过的留着）
    shutil.rmtree(audio_root() / eid, ignore_errors=True)
    with _lock, pdb() as conn:
        for tbl in ("pod_segments", "pod_turns"):
            conn.execute(f"DELETE FROM {tbl} WHERE episode=?", (eid,))  # noqa: S608
        conn.execute("DELETE FROM pod_episodes WHERE id=?", (eid,))
    log_activity(L(f"删了一期播客「{r['title']}」（原声、逐字稿、素材和从这期记的画像；存进库的笔记还在）",
                   f"Deleted the episode “{r['title']}” (audio, transcript, materials and the friend notes from it; any saved note stays)"), "edit")
    return {"ok": True}


# —— 录前先聊聊 ——————————————————————————————————————————————————

PREP_SCHEMA = {"type": "object", "properties": {"reply": {"type": "string"}}, "required": ["reply"]}
def source_note(src: dict) -> str:
    if not src:
        return ""
    return LS(f"（话题来自：{src.get('label') or src.get('kind')}）", f" (the topic comes from: {src.get('label') or src.get('kind')})")


async def prep_reply(r: sqlite3.Row, turns: list[sqlite3.Row], ask_outline: bool) -> dict:
    u = uw()
    outline = [o["text"] for o in jload(r["outline"], [])]
    mode = {"solo": LS("自己讲", "solo"), "host": LS("有主持人", "with a host"), "feynman": LS("费曼：讲给外行听", "Feynman: explain it to a layperson"),
            "friends": LS("和朋友一起录", "with friends")}[r["mode"]]
    prompt = LS(
        f"你是一档个人播客的制作人，录之前陪 {u} 聊几句，帮 TA 找到这期想说的主线，然后排一张提纲卡。这期：「{r['title']}」{source_note(jload(r['source'], {}))}，"
        f"录法：{mode}。规矩：\n"
        "- 一次只说一两句，口语，用 TA 的语言；不写稿子、不替 TA 回答、不讲道理。\n"
        "- 还没聊过：先问一句第一反应（「先说第一反应，不用想好：……」），问题要具体到这个话题。\n"
        "- TA 答过一两句以后：按 TA 的原话排提纲 outline（3–5 条，每条中文 16 字以内，是 TA 要讲的点，可以用「开头：」「收尾：」起头；"
        "第一条是开头，最后一条是收尾），"
        "reply 用一句话引出（比如「那这期就从「…」讲起。按你刚才的话，排了一张提纲：」）。TA 的话太少才再追问一次。\n"
        "- 已经有提纲、TA 又说了要改：按 TA 说的改，回改好的整张 outline。\n"
        "- 费曼：提纲是讲给外行听的顺序（先讲是什么、再讲为什么、举个例子、容易搞混的地方）。" + podmaterials.rules_line(),
        f"You're the producer of a personal podcast, chatting with {u} before recording to find the thread of this episode, then drafting "
        f"an outline card. Episode: “{r['title']}”{source_note(jload(r['source'], {}))}, mode: {mode}. Rules:\n"
        "- One or two sentences at a time, conversational, in their language; no script, don't answer for them, no lecturing.\n"
        "- Nothing said yet: ask for their first reaction (\"First reaction, no need to have it figured out: …\"), specific to this topic.\n"
        "- Once they've answered once or twice: draft outline (3–5 items, each under 10 words, the points THEY will make, in their words; "
        "the first opens, the last closes), with reply as one line introducing it. Only ask again if they said almost nothing.\n"
        "- If there is an outline and they ask for changes: return the whole revised outline.\n"
        "- Feynman: order the outline the way you'd teach a layperson (what it is, why, an example, what people confuse)." + podmaterials.rules_line())
    ppl = people.for_prompt(present_people(r)) if r["mode"] == "friends" else []
    if ppl:
        prompt += people.rules_line()
    prompt += shape('{"reply": "…", "outline": ["…", "…"]}  ' + LS("（还没到排提纲就不写 outline）", "(leave outline out until it's time)"))
    talk = [{"who": "you" if t["role"] == "host" else "them", "text": t["text"]} for t in turns]
    inp, fallback = podmaterials.inputs(r["id"], {"talk": talk, "outline": outline, "wantOutline": ask_outline, **({"people": ppl} if ppl else {})}, 8000)
    got, _ = await llmjson.ask(prompt, inp, PREP_SCHEMA, timeout=60, thinking=str(cfg().get("thinking") or "low"), model=cfg().get("model"),
                               fallback_input=fallback)
    if not isinstance(got, dict):
        raise llmjson.LLMError("unexpected JSON")
    return got


class PrepIn(BaseModel):
    text: str | None = None
    outline: bool = False    # 直接要提纲（「差不多了，排提纲吧」）


async def prep_turn(eid: str, text: str | None, voice: float | None, want_outline: bool) -> dict:
    r = load(eid)
    if text:
        with _lock, pdb() as conn:
            conn.execute("INSERT INTO pod_turns (episode, phase, role, text, voice, created_at) VALUES (?,?,?,?,?,?)",
                         (eid, "prep", "me", text[:4000], voice, now_iso()))
    with _lock, pdb() as conn:
        turns = conn.execute("SELECT * FROM pod_turns WHERE episode=? AND phase='prep' ORDER BY id", (eid,)).fetchall()
    if turns and turns[-1]["role"] == "host" and not want_outline:  # 它刚问过、你还没答：不再问
        return episode_json(eid)
    try:
        got = await prep_reply(r, turns, want_outline)
    except llmjson.LLMError as e:
        raise HTTPException(502, L(f"它没接上：{e}", f"No reply: {e}")) from e
    reply = str(got.get("reply") or "").strip()[:600]
    outline = [str(x).strip()[:120] for x in got.get("outline") or [] if str(x).strip()][:OUTLINE_MAX]
    with _lock, pdb() as conn:
        if reply:
            conn.execute("INSERT INTO pod_turns (episode, phase, role, text, created_at) VALUES (?,?,?,?,?)", (eid, "prep", "host", reply, now_iso()))
        if outline:
            conn.execute("UPDATE pod_episodes SET outline=?, cur=0, updated_at=? WHERE id=?",
                         (json.dumps([{"text": o, "done": False} for o in outline], ensure_ascii=False), now_iso(), eid))
    return episode_json(eid)


@router.post("/api/podcast/episodes/{eid}/prep")
async def prep(eid: str, body: PrepIn):
    return {"ok": True, "episode": await prep_turn(eid, (body.text or "").strip() or None, None, body.outline)}


@router.post("/api/podcast/episodes/{eid}/prep/voice")
async def prep_voice(eid: str, file: UploadFile = File(...), duration: float = Form(0)):
    """录前聊天里说的一段：转成文字当你的话（原声不留）。"""
    load(eid)
    tmp = audio_root() / eid / f".prep-{uuid.uuid4().hex[:6]}{audio_ext(file)}"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    try:
        await save_upload(file, tmp)
        try:
            res = await transcribe(tmp, timestamps=False)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(502, L(f"没转出文字：{exc}", f"Couldn't transcribe: {exc}")) from exc
    finally:
        tmp.unlink(missing_ok=True)
    text = res["text"].strip()
    if not text:
        raise HTTPException(400, L("没听到说话", "Didn't hear anything"))
    return {"ok": True, "text": text, "episode": await prep_turn(eid, text, duration or res["duration"], False)}


async def save_upload(f: UploadFile, dest: Path) -> int:
    size = 0
    with dest.open("wb") as out:
        while chunk := await f.read(1024 * 1024):
            size += len(chunk)
            if size > SEG_MAX:
                out.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(413, L("这一段太长了（超过 25 MB）", "This take is too long (over 25 MB)"))
            out.write(chunk)
    return size


# —— 录 ——————————————————————————————————————————————————————————

@router.post("/api/podcast/episodes/{eid}/segments")
async def upload_segment(eid: str, file: UploadFile = File(...), idx: int = Form(...), duration: float = Form(0)):
    """停一次传一段（idx 从 0 数；同一个 idx 再传 = 替换，网络断了重传用）。马上开始转写，不等。"""
    r = load(eid)
    if r["status"] in ("processing", "saved"):
        raise HTTPException(409, L("这一期已经录完了", "This episode is already finished"))
    if not 0 <= idx < 1000:
        raise HTTPException(400, "idx")
    dest = seg_path(eid, idx, audio_ext(file))
    dest.parent.mkdir(parents=True, exist_ok=True)
    for old in dest.parent.glob(f"{idx:03d}.*"):  # 同一段重传（换了格式）：旧的删掉
        if old != dest:
            old.unlink(missing_ok=True)
    await save_upload(file, dest)
    with _lock, pdb() as conn:
        conn.execute("INSERT OR REPLACE INTO pod_segments (episode, idx, file, duration, status, created_at) VALUES (?,?,?,?,?,?)",
                     (eid, idx, str(dest), max(0.0, float(duration or 0)), "transcribing", now_iso()))
    if r["status"] in ("prep", "ready", "failed"):
        touch(eid, status="recording", error=None)
    sync_duration(eid)
    start_segment(eid, idx)
    return {"ok": True, "idx": idx}


ASK_SCHEMA = {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]}
class AskIn(BaseModel):
    how: str = "next"   # next 问一个 / again 换个问法 / skip 跳过刚才那个


@router.post("/api/podcast/episodes/{eid}/ask")
async def ask(eid: str, body: AskIn):
    """你停下了：等这段转完，问一个（主持人或费曼的外行）。"""
    r = load(eid)
    with _lock, pdb() as conn:
        last = conn.execute("SELECT * FROM pod_turns WHERE episode=? AND phase='rec' ORDER BY id DESC LIMIT 1", (eid,)).fetchone()
    if body.how == "skip":
        if last and last["status"] == "asked":
            with _lock, pdb() as conn:
                conn.execute("UPDATE pod_turns SET status='skipped' WHERE id=?", (last["id"],))
        return {"ok": True, "episode": episode_json(eid)}
    await settle_segments(eid, timeout=45)
    segs = segments_of(eid)
    sents = flat_sentences(segs)
    with _lock, pdb() as conn:
        asked = conn.execute("SELECT text, status, at FROM pod_turns WHERE episode=? AND phase='rec' AND role='host' ORDER BY id", (eid,)).fetchall()
    outline = jload(r["outline"], [])
    u = uw()
    if r["mode"] == "feynman":
        role = LS(
            f"你扮一个聪明的外行：没学过这门课，但脑子快、爱较真。{u} 在给你讲「{r['title']}」，刚停下来。问一个外行真会问的问题：抓 TA 讲得含糊、"
            "跳了步、用了术语没解释、跟常识对不上的地方（比如「结果好，不就说明决策好吗？」）。不纠正、不给答案、不夸。",
            f"You play a smart layperson who never took this course but thinks fast and pushes back. {u} is explaining “{r['title']}” to you "
            "and just paused. Ask one question a layperson would really ask: where they were vague, skipped a step, used jargon without "
            "explaining it, or said something that clashes with common sense. Don't correct, don't answer, don't praise.")
    else:
        role = LS(
            f"你是这档个人播客的主持人。{u} 在录「{r['title']}」，刚停下来。顺着 TA 刚说的往深里问一个：要例子、要理由、问反面、问 TA 自己的经历。"
            "不评价、不总结、不给答案、不夸。提纲这一条讲透了，可以用问题把 TA 自然带到下一条（别说「下一条」）。",
            f"You host this personal podcast. {u} is recording “{r['title']}” and just paused. Ask one follow-up that goes deeper into what "
            "they just said: an example, a reason, the other side, their own experience. Don't judge, summarize, answer or praise. If the "
            "current outline point is done, a question can lead them naturally to the next (don't say \"next point\").")
    prompt = role + LS(
        "\n- 一次只问一个，一句话，中文 30 字以内（英文 20 词以内），用 TA 说话的语言。\n- 别重复问过的；TA 跳过的那类别再问。"
        + ("\n- how=again：把最后那个问题换个问法（更具体、换个角度），意思不变。" if body.how == "again" else "")
        + "\n- covered：提纲里 TA 已经讲过的条目（从 0 数）；cur：TA 现在在讲第几条。" + podmaterials.rules_line(),
        "\n- One question, one sentence, under 20 words, in their language.\n- Don't repeat earlier questions; avoid the kind they skipped."
        + ("\n- how=again: rephrase the last question (more concrete, another angle), same meaning." if body.how == "again" else "")
        + "\n- covered: outline items they've already covered (0-based); cur: the item they're on now." + podmaterials.rules_line())
    ppl = people.for_prompt(present_people(r)) if r["mode"] == "friends" else []
    if ppl:
        prompt += people.rules_line()
    prompt += shape('{"question": "…", "covered": [0, 1], "cur": 2}')
    inp = {"outline": [o["text"] for o in outline], "transcript": transcript_lines(sents, 12000),
           "asked": [{"q": a["text"], "status": a["status"]} for a in asked], "how": body.how, **({"people": ppl} if ppl else {})}
    if not sents:
        inp["note"] = "Nothing transcribed yet: ask an opening question about the topic."
    # 约朋友录的：退回带工具的对话回合时不带逐字稿（里面有朋友的原话），主持人只按提纲问
    fb = {**inp, "transcript": [], "note": "The transcript is withheld here: ask about the next outline point or the topic."} if r["mode"] == "friends" else None
    inp, fallback = podmaterials.inputs(eid, inp, 6000, fallback=fb)
    try:
        got, _ = await llmjson.ask(prompt, inp, ASK_SCHEMA, timeout=45, thinking=str(cfg().get("thinking") or "low"), model=cfg().get("model"),
                                   fallback_input=fallback)
    except llmjson.LLMError as e:
        raise HTTPException(502, L(f"它没问出来：{e}", f"No question: {e}")) from e
    q = str((got or {}).get("question") or "").strip()[:300] if isinstance(got, dict) else ""
    if not q:
        raise HTTPException(502, L("它没问出来", "No question"))
    covered = {int(i) for i in (got.get("covered") or []) if isinstance(got.get("covered"), list) and str(i).strip().isdigit()}
    at = sum(s["duration"] for s in segs)
    with _lock, pdb() as conn:
        if body.how == "again" and last and last["role"] == "host" and last["status"] == "asked":
            conn.execute("UPDATE pod_turns SET status='rephrased' WHERE id=?", (last["id"],))
        elif last and last["role"] == "host" and last["status"] == "asked":
            conn.execute("UPDATE pod_turns SET status='answered' WHERE id=?", (last["id"],))
        conn.execute("INSERT INTO pod_turns (episode, phase, role, text, at, status, created_at) VALUES (?,?,?,?,?,?,?)",
                     (eid, "rec", "host", q, round(at, 1), "asked", now_iso()))
    cols: dict = {}
    if covered and outline:
        cols["outline"] = json.dumps([{**o, "done": o.get("done") or i in covered} for i, o in enumerate(outline)], ensure_ascii=False)
    if str(got.get("cur", "")).strip().isdigit() and outline:
        cols["cur"] = max(0, min(int(str(got["cur"]).strip()), len(outline) - 1))
    if cols:
        touch(eid, **cols)
    return {"ok": True, "episode": episode_json(eid)}


# —— 录完：整理 ————————————————————————————————————————————————————

FIX_SCHEMA = {"type": "object", "properties": {"fixes": {"type": "array"}}, "required": ["fixes"]}
SUM_SCHEMA = {"type": "object", "properties": {"title": {"type": "string"}, "quotes": {"type": "array"}}, "required": ["title", "quotes"]}
REL_SCHEMA = {"type": "object", "properties": {"relates": {"type": "array"}}, "required": ["relates"]}
FY_SCHEMA = {"type": "object", "properties": {"wrong": {"type": "array"}, "missed": {"type": "array"}}, "required": ["wrong", "missed"]}
@router.post("/api/podcast/episodes/{eid}/finish")
async def finish(eid: str):
    """录完了：后台整理（app 轮询 GET 这一期的 status：processing → ready / failed）。整理过的再点 = 重新整理。"""
    r = load(eid)
    with _lock, pdb() as conn:
        n = conn.execute("SELECT COUNT(*) FROM pod_segments WHERE episode=?", (eid,)).fetchone()[0]
    if not n:
        raise HTTPException(400, L("还没录到东西", "Nothing recorded yet"))
    if r["status"] == "processing" and eid in _jobs:
        return {"ok": True, "episode": episode_json(eid)}
    touch(eid, status="processing", error=None)
    start_job(eid)
    return {"ok": True, "episode": episode_json(eid)}


def start_job(eid: str) -> None:
    t = asyncio.create_task(process(eid))
    _jobs[eid] = t
    t.add_done_callback(lambda _t, e=eid: _jobs.pop(e, None))


def related_notes(words: list[str], skip: str | None = None) -> list[dict]:
    """库里（笔记、写作）和这些词最沾边的几篇：按命中的词数排，给一段上下文。"""
    words = [w.lower() for w in words if len(norm(w)) >= 2][:12]
    if not words:
        return []
    scored = []
    for p in think.note_files():
        try:
            meta, body, _ = think.parse_note(p.read_text(encoding="utf8", errors="replace"))
        except OSError:
            continue
        if meta.get("episode") and meta.get("episode") == skip:
            continue
        hay = (p.stem + "\n" + body).lower()
        hits = [w for w in words if w in hay]
        if len(hits) >= (2 if len(words) > 3 else 1):
            i = hay.find(hits[0])
            scored.append((len(hits), p, body[max(0, i - 200): i + 400].strip()))
    scored.sort(key=lambda x: -x[0])
    return [{"path": think.rel(p), "title": p.stem, "snippet": snip} for _, p, snip in scored[:4]]


async def process(eid: str) -> None:
    r = load(eid)
    think_level = str(cfg().get("thinking") or "low")
    model = cfg().get("model")
    try:
        await settle_segments(eid, timeout=240, retry_failed=True)
        segs = segments_of(eid)
        who = jload(r["speakers"], {})
        sents = flat_sentences(segs, who)
        if not sents:
            raise RuntimeError(L("没听到说话（转写是空的）", "No speech found (the transcript is empty)"))
        labels = {x["label"] for x in sents if x.get("label")}
        if r["mode"] == "friends" and labels and not (labels <= set(who) and "@me" in who.values()):
            touch(eid, status="naming", error=None)  # 先让你认一下谁是谁（PATCH speakers 以后接着整理）
            return
        friends = r["mode"] == "friends"
        me_labels = {k for k, v in who.items() if v == "@me"}

        def only_mine(xs: list[dict]) -> list[dict]:
            """约朋友录的：我说的那几句（退回带工具的对话回合时只带这些，朋友的原话不进那种回合）。"""
            return [x for x in xs if x.get("label") in me_labels] if friends else xs

        # 1. 校对：同音字、专有名词（只改转错的，不改说法）
        try:
            got, _ = await llmjson.ask(LS(
                "下面是一期播客的逐字稿（语音转写），可能有同音字、专有名词写错。对照词表（TA 常说的专有名词、课名、项目名）和上下文，"
                "只改明显转错的：同音字、专有名词拼写、中英混说里被写成中文的英文词。不改说法、不润色、不删口头语。from 必须是那一句里原样有的字。没有就回空列表。",
                "Below is a podcast transcript (speech-to-text) that may contain misheard words and misspelled proper nouns. Using the vocabulary "
                "(names, courses, projects they often say) and context, fix only clear transcription errors. Don't rephrase, polish or remove "
                "fillers. `from` must appear verbatim in that sentence. Return an empty list if nothing needs fixing.")
                + shape('{"fixes": [{"id": "0.3", "from": "…", "to": "…"}]}'),
                {"vocabulary": vocab_terms(), "sentences": [{"id": x["id"], "text": x["text"]} for x in sents]},
                FIX_SCHEMA, timeout=120, thinking=think_level, model=model,
                fallback_input={"vocabulary": vocab_terms(), "sentences": [{"id": x["id"], "text": x["text"]} for x in only_mine(sents)]})
            apply_fixes(eid, (got or {}).get("fixes") or [] if isinstance(got, dict) else [])
            segs = segments_of(eid)
            sents = flat_sentences(segs, who)
        except llmjson.LLMError:
            pass
        with _lock, pdb() as conn:
            turns = conn.execute("SELECT text, status, at FROM pod_turns WHERE episode=? AND phase='rec' AND role='host' ORDER BY id", (eid,)).fetchall()
        outline = jload(r["outline"], [])
        br = await asyncio.to_thread(think.branches)
        u = uw()
        mine = only_mine(sents) if me_labels else sents
        # 2. 整理
        prompt = LS(
            f"{u} 录完了一期播客「{r['title']}」。按逐字稿整理成 TA 自己的笔记草稿。规矩：\n"
            "- 尽量用 TA 的原话，不替 TA 润色，不加 TA 没说过的观点；没想清的放进 open，别替 TA 下结论。\n"
            "- title：这期讲的东西（可以沿用原题，讲偏了就按实际讲的改），20 字以内。oneLine：TA 现在的结论，一两句，用 TA 的话。\n"
            "- quotes：3–6 句最能代表 TA 想法的原话，id 是逐字稿里那一句的编号，text 从那一句里原样摘（可以截一段，不改字）。\n"
            "- open：TA 还没想清的，0–4 条，写成 TA 会问自己的问题，不加括号注释、不写「提到了……」。keywords：3–6 个；suggest：你另外建议的，最多 2 个。\n"
            f"- tree：如果有一条三个月后换个 AI 也用得上的「TA 怎么想」，写成一句第三人称（「{u}{' ' if u.isascii() else ''}认为……」）；没有就空。"
            "branch：挂哪根枝" + (f"（从这些里选：{'、'.join(br)}）" if br else "") + "。\n"
            "- 主持人的问题只是帮你理解上下文，不算 TA 的话。"
            + ("\n- 这是和朋友一起录的：note 只写「我」说的；minutes 写每个人一份纪要 {说话人: [要点…]}（按各自说的）。"
               "title、oneLine、quotes、open、keywords、suggest、tree 都只按「我」说的写：朋友的近况、计划、私事不进标题和关键词（这些会存进 TA 的库）；"
               "title 按「我」说的重新起，不沿用原题。" if friends else "")
            + "\n- materials 是 TA 放进这一期的素材，只帮你理解背景：quotes 只能从逐字稿里摘；朋友说的不写进 title、oneLine、open、keywords、suggest、tree；tree 只写 TA 自己的想法。",
            f"{u} finished recording the episode “{r['title']}”. Turn the transcript into a draft of their own note. Rules:\n"
            "- Use their own words; don't polish or add views they didn't express; unresolved things go in open — don't conclude for them.\n"
            "- title: what the episode was actually about (keep the original unless they drifted), under 12 words. oneLine: their current "
            "conclusion in their words, one or two sentences.\n"
            "- quotes: 3–6 sentences that best carry their thinking; id is the transcript sentence id, text taken verbatim from it (may be a "
            "part of it, no word changes).\n- open: 0–4 things still unclear to them, as questions they'd ask themselves, no bracketed notes. keywords: 3–6; suggest: at most 2 more.\n"
            "- tree: one third-person sentence about how they think, worth remembering across AIs for months, or empty. branch: which branch"
            + (f" (one of: {', '.join(br)})" if br else "") + ".\n- The host's questions are context only, not their words."
            + ("\n- Recorded with friends: the note is only what 'me' said; minutes gives each speaker their own summary {speaker: [points…]}. "
               "title, oneLine, quotes, open, keywords, suggest and tree come only from what 'me' said: friends' news, plans and private matters "
               "stay out of the title and keywords (those go into their vault); write a new title from what 'me' said, don't keep the original." if friends else "")
            + "\n- materials are what they put into this episode, for background only: quotes come from the transcript; nothing a friend said goes into "
              "title, oneLine, open, keywords, suggest or tree; tree is only their own view.")
        prompt += shape('{"title": "…", "oneLine": "…", "quotes": [{"id": "0.2", "text": "…"}], "open": ["…"], "keywords": ["…"], '
                        '"suggest": ["…"], "tree": "", "branch": ""' + (', "minutes": {"…": ["…"]}' if friends else "") + "}")
        base = {"outline": [o["text"] for o in outline], "transcript": transcript_lines(sents if friends else mine, 60000),
                "hostQuestions": [f"[{clock(t['at'])}] {t['text']} ({t['status']})" for t in turns], **({"me": speaker_name("@me", who)} if friends else {})}
        inp, fallback = podmaterials.inputs(eid, base, 24000, fallback={**base, "transcript": transcript_lines(mine, 60000)})
        got, _ = await llmjson.ask(prompt, inp, SUM_SCHEMA, timeout=180, thinking="medium" if think_level == "low" else think_level, model=model,
                                   fallback_input=fallback)
        res = clean_result(got if isinstance(got, dict) else {}, mine, r["title"])  # 约朋友录的：「我的原话」只从我说的句子里摘
        if br and res["branch"] not in br:  # 编出来的枝不要：世界树写的时候自己挂
            res["branch"] = ""
        # 3. 跟以前的笔记比：想法变没变
        try:
            notes = await asyncio.to_thread(related_notes, res["keywords"] + res["suggest"] + [r["title"]], eid)
            notes = podmaterials.mine_for_relates(eid)[:3] + notes  # 你放进这一期的、你自己说过的话，也拿来比
            if notes:
                rel_got, _ = await llmjson.ask(LS(
                    f"这是 {u} 刚录的一期播客的要点，和库里 TA 以前写的几篇笔记。找出 TA 以前说过、这期又说到的想法（最多 2 条）：then = 以前笔记里的原话，"
                    "now = 这期里的原话（nowId 是那一句的编号），changed = 想法变了没有。真沾边才写，没有就回空列表。",
                    f"Here are the key points of {u}'s new episode and a few of their earlier notes. Find at most 2 ideas they wrote about before and "
                    "touched again now: then = the earlier note's words, now = this episode's words (nowId = that sentence id), changed = whether "
                    "their view changed. Only real overlaps; otherwise an empty list.")
                    + shape('{"relates": [{"path": "…", "then": "…", "now": "…", "nowId": "0.4", "changed": true}]}'),
                    {"episode": {"title": res["title"], "oneLine": res["oneLine"], "quotes": res["quotes"]}, "transcript": transcript_lines(mine, 15000),
                     "notes": notes}, REL_SCHEMA, timeout=90, thinking=think_level, model=model)
                paths = {n["path"]: n["title"] for n in notes}
                at = {x["id"]: x["at"] for x in sents}
                res["relates"] = [{"path": x["path"], "title": paths[x["path"]], "then": str(x.get("then") or "")[:300], "now": str(x.get("now") or "")[:300],
                                   "at": at.get(str(x.get("nowId") or "")), "changed": bool(x.get("changed"))}
                                  for x in ((rel_got or {}).get("relates") or [] if isinstance(rel_got, dict) else []) if x.get("path") in paths][:2]
        except llmjson.LLMError:
            pass
        # 3b. 朋友画像：认人时对上了人的，每人记几条（只有你看得到）；每人纪要末尾说一句会记进画像
        sp = jload(r["speaker_people"], {})
        if friends and sp:
            res["people"] = await profile_people(eid, res["title"], sents, who, sp)
            linked = {speaker_name(label, who) for label in sp}
            for k, pts in (res.get("minutes") or {}).items():
                if k in linked:
                    pts.append(people.minutes_note())
        touch(eid, result=json.dumps(res, ensure_ascii=False))
        # 4. 费曼：对照学习台这一节
        if r["mode"] == "feynman":
            fy = await feynman(r, sents, turns, think_level, model)
            touch(eid, feynman=json.dumps(fy, ensure_ascii=False))
        touch(eid, status="ready", error=None, title=res["title"] or r["title"])
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 — 页面上说，能重试
        touch(eid, status="failed", error=str(exc)[:300])


async def profile_people(eid: str, title: str, sents: list[dict], who: dict, sp: dict) -> list[dict]:
    """一期整理完记画像，每人一次 llm-task（同时发）：先存一份、撤掉这一期上次记的（重新整理不会记两遍），再记；记失败了原样放回去。
    这一期以前对上过、这次不再对上的人，他从这一期记的撤掉。
    → [{person, name, added, replaced, answered}] 或 [{person, name, error}]（录完那页顶上「小林的画像多了 3 条」）。"""
    by: dict[str, set[str]] = {}
    for label, pid in sp.items():
        by.setdefault(pid, set()).add(label)
    for pid in await asyncio.to_thread(people.people_in_episode, eid):
        if pid not in by:
            await asyncio.to_thread(people.revert_episode, eid, pid)
    nm = people.names(by)
    lines = transcript_lines(sents, 40000)

    async def one(pid: str, labels: set[str]) -> dict:
        snap = await asyncio.to_thread(people.snapshot, eid, pid)
        await asyncio.to_thread(people.revert_episode, eid, pid)
        try:
            return await people.extract(eid, title, pid, nm[pid], lines, {x["id"]: x for x in sents if x.get("label") in labels})
        except Exception as exc:  # noqa: BLE001 — 这一个人这次没记上：上次记的放回去，页面上说一句
            await asyncio.to_thread(people.restore, eid, pid, snap)
            return {"person": pid, "name": nm[pid], "error": str(exc)[:160]}

    return list(await asyncio.gather(*(one(pid, labels) for pid, labels in by.items() if pid in nm)))


def clean_result(j: dict, sents: list[dict], title: str) -> dict:
    by_id = {x["id"]: x for x in sents}

    def strs(v, n, cap):
        return [str(x).strip()[:cap] for x in (v or []) if str(x).strip()][:n] if isinstance(v, list) else []

    quotes = []
    for q in j.get("quotes") or []:
        if not isinstance(q, dict):
            continue
        s = by_id.get(str(q.get("id") or ""))
        text = str(q.get("text") or "").strip()
        if not s or not text:
            continue
        if norm(text) not in norm(s["text"]):  # 模型改了字：用原句
            text = s["text"]
        quotes.append({"id": s["id"], "at": s["at"], "text": text[:300]})
    kws = think.clean_keywords(strs(j.get("keywords"), 6, 30))
    suggest = [k for k in think.clean_keywords(strs(j.get("suggest"), 2, 30)) if k.lower() not in {x.lower() for x in kws}]
    mins = j.get("minutes") if isinstance(j.get("minutes"), dict) else {}
    return {"title": str(j.get("title") or title).strip()[:TITLE_MAX] or title, "oneLine": str(j.get("oneLine") or "").strip()[:600],
            "quotes": quotes[:6], "open": strs(j.get("open"), 4, 300), "keywords": kws, "suggest": suggest,
            "tree": str(j.get("tree") or "").strip()[:300], "branch": str(j.get("branch") or "").strip()[:30], "relates": [],
            **({"minutes": {str(k)[:20]: strs(v, 8, 300) for k, v in mins.items()}} if mins else {})}


def apply_fixes(eid: str, fixes: list) -> int:
    """校对的结果改进逐字稿：每句记下改了什么（fixed: [[原来, 改成]]），页面上能看出来。"""
    by: dict[tuple[int, int], list[tuple[str, str]]] = {}
    for f in fixes:
        if not isinstance(f, dict):
            continue
        m = re.match(r"^(\d+)\.(\d+)$", str(f.get("id") or ""))
        a, b = str(f.get("from") or ""), str(f.get("to") or "")
        if m and a and b and a != b and len(a) <= 40 and len(b) <= 40:
            by.setdefault((int(m.group(1)), int(m.group(2))), []).append((a, b))
    n = 0
    with _lock, pdb() as conn:
        for (idx, i), pairs in by.items():
            row = conn.execute("SELECT sentences FROM pod_segments WHERE episode=? AND idx=?", (eid, idx)).fetchone()
            sents = jload(row["sentences"], []) if row else []
            if not 0 <= i < len(sents):
                continue
            s = sents[i]
            for a, b in pairs:
                if a in s["text"]:
                    s["text"] = s["text"].replace(a, b, 1)
                    s.setdefault("fixed", []).append([a, b])
                    n += 1
            conn.execute("UPDATE pod_segments SET sentences=? WHERE episode=? AND idx=?", (json.dumps(sents, ensure_ascii=False), eid, idx))
    return n


async def feynman(r: sqlite3.Row, sents: list[dict], turns: list, think_level: str, model: str | None) -> dict:
    src = jload(r["source"], {})
    material, where = "", ""
    if src.get("course") and src.get("page"):
        try:
            import study
            unit = study.resolve_unit(src["course"], src["page"], None)
            material = await asyncio.to_thread(study.context_for, unit, 90000)
            where = f"{src['course']} · {unit['title']}"
        except Exception:  # noqa: BLE001 — 学习页挪了、删了：按常识对照
            material = ""
    extra = podmaterials.files_for_feynman(r["id"], 40000)
    if extra:  # 你放进这一期的文件（课件、讲义）也拿来对照，出处写文件名
        material = (material + "\n\n" if material else "") + LS("【你放进这一期的材料】\n", "[Materials you added to this episode]\n") + extra
        where = where or LS("你放进这一期的材料", "the materials you added")
    u = uw()
    prompt = LS(
        f"{u} 用费曼法讲了一遍「{r['title']}」（讲给外行听）。" + (f"对照下面这一节（{where}）的课件、学习页和录播字幕：" if material else
                                                               "这一节没有课件可对照，按这个领域公认的讲法对照：")
        + "列出 TA 讲对的（right，一句一条）、讲错的（wrong：said = TA 的原话，id = 那一句的编号，correct = 课上 / 公认的讲法，source = 出处：课件文件名和页码、"
          "或录播时间点" + ("" if material else "，没有课件就写「常识」") + "）、漏了的（missed：课上强调、TA 没讲到的要点，source 同上）。"
          "只挑要紧的：wrong 和 missed 各最多 4 条。explain：把 TA 的讲解整理成一段笔记，用 TA 的原话、去掉口头语、按讲的顺序。",
        f"{u} explained “{r['title']}” Feynman-style (to a layperson). " + (f"Compare it with this session's materials ({where}: slides, notes, lecture captions): "
                                                                           if material else "There are no course materials; compare it with the standard view of the field: ")
        + "list what they got right (right, one line each), what they got wrong (wrong: said = their words, id = that sentence id, correct = "
          "what the course / field says, source = file and page or recording time" + ("" if material else ", or \"general knowledge\"") + "), "
          "and what they missed (missed: points the course stresses that they didn't cover, source as above). Only what matters: at most 4 "
          "wrong and 4 missed. explain: their explanation as a note, in their words, fillers removed, in the order they told it.")
    prompt += shape('{"right": ["…"], "wrong": [{"id": "0.1", "said": "…", "correct": "…", "source": "…"}], '
                    '"missed": [{"text": "…", "source": "…"}], "explain": "…"}')
    got, _ = await llmjson.ask(prompt, {"transcript": transcript_lines(sents, 40000), "laypersonQuestions": [f"[{clock(t['at'])}] {t['text']}" for t in turns],
                                        **({"materials": material} if material else {})},
                               FY_SCHEMA, timeout=240, thinking="medium" if think_level == "low" else think_level, model=model)
    j = got if isinstance(got, dict) else {}
    at = {x["id"]: x["at"] for x in sents}
    wrong = [{"id": str(w.get("id") or ""), "at": at.get(str(w.get("id") or "")), "said": str(w.get("said") or "")[:300],
              "correct": str(w.get("correct") or "")[:500], "source": str(w.get("source") or "")[:120]}
             for w in j.get("wrong") or [] if isinstance(w, dict) and w.get("said")][:4]
    missed = [{"text": str(m.get("text") or "")[:300], "source": str(m.get("source") or "")[:120]}
              for m in j.get("missed") or [] if isinstance(m, dict) and m.get("text")][:4]
    return {"right": [str(x)[:300] for x in j.get("right") or [] if str(x).strip()][:6], "wrong": wrong, "missed": missed,
            "explain": str(j.get("explain") or "").strip()[:4000], "against": where or None,
            "questions": [{"at": t["at"], "text": t["text"]} for t in turns]}


# —— 逐字稿：改一句 ——————————————————————————————————————————————

def changed_words(old: str, new: str) -> list[str]:
    """改过的词：英文扩到整个词（OpenMoose → OpenMousse 记 OpenMousse，不是 us），中文把改动处往两边各带一个字（错的起 → 错得起）。"""
    out = []
    for tag, _a0, _a1, b0, b1 in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if tag not in ("replace", "insert"):
            continue
        a, b = b0, b1
        if (a < len(new) and new[a].isascii() and new[a].isalnum()) or (b > 0 and new[b - 1].isascii() and new[b - 1].isalnum()):
            while a > 0 and new[a - 1].isascii() and (new[a - 1].isalnum() or new[a - 1] in "&-'"):
                a -= 1
            while b < len(new) and new[b].isascii() and (new[b].isalnum() or new[b] in "&-'"):
                b += 1
        else:
            if a > 0 and "㐀" <= new[a - 1] <= "鿿":
                a -= 1
            if b < len(new) and "㐀" <= new[b] <= "鿿":
                b += 1
        w = new[a:b].strip(" ，。,.!?！？、：:；;")
        if 2 <= len(norm(w)) and len(w) <= 30 and w not in out:
            out.append(w)
    return out


class SentenceIn(BaseModel):
    idx: int
    i: int
    text: str


@router.patch("/api/podcast/episodes/{eid}/sentence")
async def edit_sentence(eid: str, body: SentenceIn):
    """点一句改一句：改掉的词进词表（下次转写就认得）。"""
    load(eid)
    new = body.text.strip()[:1000]
    if not new:
        raise HTTPException(400, L("不能改成空的", "Can't be empty"))
    with _lock, pdb() as conn:
        row = conn.execute("SELECT sentences FROM pod_segments WHERE episode=? AND idx=?", (eid, body.idx)).fetchone()
        sents = jload(row["sentences"], []) if row else []
        if not 0 <= body.i < len(sents):
            raise HTTPException(404, L("没有这一句", "No such sentence"))
        old = sents[body.i]["text"]
        sents[body.i] = {**sents[body.i], "text": new, "edited": True}
        sents[body.i].pop("flag", None)
        conn.execute("UPDATE pod_segments SET sentences=? WHERE episode=? AND idx=?", (json.dumps(sents, ensure_ascii=False), eid, body.idx))
    added = changed_words(old, new)
    add_vocab(added)
    return {"ok": True, "vocab": added, "episode": episode_json(eid)}


# —— 存进库 ——————————————————————————————————————————————————————

class SaveIn(BaseModel):
    folder: str = "notes"          # notes 笔记 / writing 写作 / study 学习（费曼：学习/<课>/）
    title: str
    oneLine: str = ""
    quotes: list[dict] = []        # [{text, at}]
    open: list[str] = []
    keywords: list[str] = []
    relates: list[dict] = []       # [{path, title, then, now, at, changed}]
    explain: str | None = None     # 费曼：你的讲解（整理过的）
    tree: str | None = None        # 要记进世界树的那一句（不点不记：不给就不记）
    branch: str | None = None


def study_dir() -> Path:
    return think._folder("study_dir", "学习", "Study")


def save_sync(eid: str, body: SaveIn) -> dict:
    r = load(eid)
    title = re.sub(r"\s+", " ", body.title).strip()[:TITLE_MAX]
    if not title:
        raise HTTPException(400, L("起个标题", "Give it a title"))
    src = jload(r["source"], {})
    fy = jload(r["feynman"], {})
    kws = think.clean_keywords(body.keywords)
    if body.folder == "writing":
        folder = think.writing_dir()
    elif body.folder == "study":
        folder = study_dir() / think.safe_file_part(src.get("course") or "", 40) if src.get("course") else study_dir()
    else:
        folder = think.notes_dir()
    folder.mkdir(parents=True, exist_ok=True)
    if r["saved_path"]:  # 存过一次再存：覆盖同一篇（改了再存），不另起一篇
        old = think.base_dir() / r["saved_path"]
        path = old if old.parent == folder and old.exists() else think.unique_path(folder, think.safe_file_part(title, 60) or eid)
    else:
        path = think.unique_path(folder, think.safe_file_part(title, 60) or eid)
    parts = []
    if body.oneLine.strip():
        parts.append("> " + body.oneLine.strip().replace("\n", "\n> "))
    if body.explain and body.explain.strip():
        parts.append(f"## {LS('我的讲解', 'My explanation')}\n" + body.explain.strip())
    quotes = [q for q in body.quotes if isinstance(q, dict) and str(q.get("text") or "").strip()]
    if quotes:
        parts.append(f"## {LS('我的原话', 'In my words')}\n" + "\n".join(
            f"- 「{str(q['text']).strip()}」" + (f"（{clock(q.get('at'))}）" if q.get("at") is not None else "") for q in quotes))
    rels = [x for x in body.relates if isinstance(x, dict) and x.get("path")]
    if rels:
        lines = []
        for x in rels:
            if str(x["path"]).startswith("material:"):  # 你放进这一期的素材：没有库里的笔记可以链
                link = str(x.get("title") or LS("素材", "material"))
            else:
                link = f"[[{str(x['path'])[:-3] if str(x['path']).endswith('.md') else x['path']}|{x.get('title') or Path(str(x['path'])).stem}]]"
            arrow = LS("想法变了：", "changed: ") if x.get("changed") else LS("还是这么想：", "same view: ")
            lines.append(f"- {link}：{LS('以前', 'before')}「{x.get('then') or ''}」→ {arrow}「{x.get('now') or ''}」")
        parts.append(f"## {LS('跟以前想的', 'Compared with before')}\n" + "\n".join(lines))
    if fy:
        if fy.get("wrong"):
            parts.append(f"## {LS('讲错的', 'Got wrong')}\n" + "\n".join(
                f"- {LS('我说', 'I said')}「{w['said']}」→ {w['correct']}" + (f"（{w['source']}）" if w.get("source") else "") for w in fy["wrong"]))
        if fy.get("missed"):
            parts.append(f"## {LS('漏了的', 'Missed')}\n" + "\n".join(f"- {m['text']}" + (f"（{m['source']}）" if m.get("source") else "") for m in fy["missed"]))
    opens = [x.strip() for x in body.open if x.strip()]
    if opens:
        parts.append(f"## {LS('还没想清的', 'Still open')}\n" + "\n".join(f"- {x}" for x in opens))
    refs = podmaterials.note_lines(eid)  # 和朋友的聊天只写「参考了和 X 的聊天」，不写朋友的原话
    if refs:
        parts.append(f"## {LS('参考了', 'Drew on')}\n" + "\n".join(f"- {x}" for x in refs))
    mode_name = {"solo": LS("自己讲", "solo"), "host": LS("有主持人", "with a host"), "feynman": LS("费曼", "Feynman"), "friends": LS("和朋友", "with friends")}
    parts.append("---\n" + LS(f"原声和逐字稿在服务器上：播客「{r['title']}」（{clock(r['duration'])}，{mode_name[r['mode']]}）",
                              f"Audio and transcript stay on the server: episode “{r['title']}” ({clock(r['duration'])}, {mode_name[r['mode']]})"))
    meta = {"created_at": r["saved_at"] or now_iso(), "updated_at": now_iso(), "source": "grava-podcast", "episode": eid, "mode": r["mode"],
            "duration": clock(r["duration"]), "keywords": kws, "tags": think.obsidian_tags(kws)}
    if src.get("course"):
        meta["course"] = src["course"]
    think.write_atomic(path, think.dump_note(meta, "\n\n".join(parts)))
    if r["saved_path"] and (think.base_dir() / r["saved_path"]) != path:
        (think.base_dir() / r["saved_path"]).unlink(missing_ok=True)  # 换了文件夹：旧的那篇挪走
    tree = None
    if body.tree and body.tree.strip():
        tree = think.remember_tree(body.tree.strip()[:300], body.branch, kws)
    rp = think.rel(path)
    touch(eid, status="saved", saved_path=rp, saved_folder=body.folder if body.folder in ("notes", "writing", "study") else "notes",
          saved_at=r["saved_at"] or now_iso(), title=title, tree=body.tree.strip()[:300] if body.tree and body.tree.strip() else r["tree"])
    log_activity(L(f"播客「{title}」存进了库（{rp}）", f"Saved the episode “{title}” to the vault ({rp})"), "edit")
    return {"ok": True, "path": rp, "obsidian": obsidian_url(rp), "tree": tree, "episode": episode_json(eid)}


@router.post("/api/podcast/episodes/{eid}/save")
async def save(eid: str, body: SaveIn):
    return await asyncio.to_thread(save_sync, eid, body)


@router.post("/api/podcast/episodes/{eid}/review")
async def to_review(eid: str):
    """费曼：讲错和漏了的加进学习台这门课的复习。"""
    r = load(eid)
    fy, src = jload(r["feynman"], {}), jload(r["source"], {})
    if not fy or not src.get("course"):
        raise HTTPException(400, L("这一期没有对照课件", "This episode wasn't compared with course materials"))
    import study
    items = [{"text": f"{w['said']} → {w['correct']}", "kind": "wrong", "source": w.get("source") or ""} for w in fy.get("wrong") or []]
    items += [{"text": m["text"], "kind": "missed", "source": m.get("source") or ""} for m in fy.get("missed") or []]
    if not items:
        raise HTTPException(400, L("没有讲错和漏了的", "Nothing wrong or missed"))
    n = study.add_review(src["course"], src.get("page"), items, {"episode": eid, "title": r["title"]})
    touch(eid, review_at=now_iso())
    return {"ok": True, "added": n, "episode": episode_json(eid)}


# —— 原声 ——————————————————————————————————————————————————————————

@router.get("/api/podcast/episodes/{eid}/audio/{idx}")
async def audio(eid: str, idx: int):
    load(eid)
    with _lock, pdb() as conn:
        row = conn.execute("SELECT file FROM pod_segments WHERE episode=? AND idx=?", (eid, idx)).fetchone()
    p = Path(row["file"]) if row else None
    if not p or not p.is_file() or p.parent.resolve() != (audio_root() / eid).resolve():
        raise HTTPException(404, L("这一段的原声不在了", "This take's audio is gone"))
    return FileResponse(p, media_type=AUDIO_TYPES.get(p.suffix.lower(), "audio/mp4"), filename=f"{eid}-{idx:03d}{p.suffix}")
