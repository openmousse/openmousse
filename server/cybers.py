"""赛博（Cybers，主线第三步「Agents 和群聊」第一段，2026-10-20）：陪你想的 Agent。

两类 Agent：功能 Agent 是做事的（groups 表：管一个领域、能动手）；赛博是陪你想的：有名字、形象、性格、模型、记忆，只说话。
赛博不进 groups 表、不建 OpenClaw agent：自己一张表（cybers），内核直接跑它的赛博回合（engine/cyber.py）。

开关（照 nodes.py 的做法）：server.json 的 "cybers": true（或 {…}，见下）；不写 / false、claw 不是 builtin → 整个功能关着，/api/cybers…
一律 404（app 据此不显示入口）。已有的安装默认关，上线时再问开不开。

表（mousse.db）
  cybers  一位一行：id（cy-<8 位十六进制>，也是它一对一的对话线程）、编号 no（No. 07：只增不回收，删了的也占着）、名字、标签
          （通讯录和群里写在名字后面的「早期投资人」）、哪一类 kind（pro 专业 / friend 朋友 / machine 机器 / blank 空白开始）、
          从哪个模板来、形象 avatar（JSON，见「形象」）、性格三行（identity 身份 / tone 语气 / rules 规矩）、声音、模型（provider/model，
          不写 = server.json 的 engine.use.cyber，再不行 engine.model）、装载名单、建立 / 改 / 删的时间。
  删除 = deleted_at 记下、记忆移进归档（不真删）；列表、通讯录里不再出现，对话记录留着。

形象（avatar，JSON；部件和颜色见 app 的头像组件，这里只认名字）
  捏的：{hair, hairColor, beard, collar, clothes, skin, bg, accColor, acc: {glasses, phones, earring, hat}}，缺的照默认补
  上传的：{"upload": <附件 id>}（先经 /api/chat/upload 存成附件，只存在这台服务器上；要是图片）
  机器类：{"machine": "timer" | "sides" | "rewind", "bg"?}（线条图标，不画人）
  random_avatar()：照设计稿的「随机一个」（胡子 60% 没有；眼镜 40%、耳机 25%、耳环 25%、帽子 15%；底色避开衣服的同色系，20 像素也分得开）

接口（都要 cybers 开着）
  GET    /api/cybers                 {cybers: [...]}：没删的，按最近对话排（没聊过的按建立的先后）
  POST   /api/cybers                 {name, tag?, kind?, template?, avatar?, identity?, tone?, rules?, voice?, model?, loadout?} → {ok, cyber}
                                     （一句话新建的补问、草稿、试聊在 agentnew.py）
  GET    /api/cybers/{id}            一位（资料页：编号、建于、一起几天、记下几条……）
  PATCH  /api/cybers/{id}            只改给了的字段（同 POST 的字段）
  DELETE /api/cybers/{id}            删除：记忆移进归档

记忆（三样）：名片（每位赛博都知道）、记下的（它自己长，只记和你聊过的，每条你点了头才记，能看、能删、能清空）、知识库（你给的资料、
  每天读的，写明来源）。备料见 memory_for。健康、饮食、睡眠不给赛博、不记（HEALTH）。
  GET /api/cybers/{id}/memory · /notes · /knowledge；DELETE /notes/{nid} · /notes（清空）· /knowledge/{kid}；POST /knowledge {title, text | file}
改法（先给改好的样子 → 点头才写进去 → 每次改记一个版本 → 能撤销到上一版）：propose / apply_change / undo，见那一节。
  GET /api/cybers/{id}/changes（等你点头的 + 最近的版本）· /versions；POST /changes {field?, value? | tweak? | ask?}（资料页：先给样子）；
  POST /changes/{pid} {action: apply | drop}；POST /undo {version?}。对话里说的走赛博回合的 propose_change（engine/cyber_tools.py）
回看（server.json 的 cybers.review，默认关）：每隔几轮零工具地回头看一次，想记的、想改的说话方式都先提给你（卡挂在它的对话上）。

一对一（线程 = 赛博的 id）：/api/chat/send 发到这里，chat.start_run 认出来（模型是这位赛博的，thread_model），chat.engine_stream 交给
  respond() 跑一轮赛博回合（engine/cyber.chat）；流的事件、messages、未读和别的对话一样。对话列表（chats.py）里聊过的才有一行
  （chat_rows，app 带 ?with=cyber 才给），未读算进 unread.tracked（tracked）。
"""
from __future__ import annotations

import json
import random
import re
import sqlite3
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import chat
import claw
from chat import _lock, log_activity, now_iso
from config import TZ, raw
from i18n import L
from stored_values import ASSISTANT_ROLE

ID_RE = re.compile(r"^cy-[0-9a-f]{8}$")
KINDS = ("pro", "friend", "machine", "blank")
NAME_MAX, TAG_MAX, LINE_MAX, VOICE_MAX = 24, 16, 400, 40
CTRL = re.compile(r"[\x00-\x1f\x7f​-‏‪-‮⁦-⁩]")
VOICE_RE = re.compile(r"^[\w.:-]{1,40}$")

# —— 形象的部件（名字和 app 的头像组件对得上；路径、颜色在 app 里）————————————————————————————————
HAIR = ("bun", "short", "buzz", "bob", "long", "pony", "curly", "bald")
HAIR_COLORS = ("black", "dark", "chestnut", "flax", "grey", "wine")
BEARDS = ("none", "stubble", "mustache", "full")
COLLARS = ("shirt", "v", "round", "turtle")
CLOTHES = ("cyan", "camel", "purple", "green", "rose", "rust")
SKINS = ("stone", "warm", "wheat", "tan", "deep")
BGS = ("cyan", "gold", "purple", "green", "pink", "orange")
ACC_COLORS = ("black", "gold", "silver", "cyan")
ACCS = ("glasses", "phones", "earring", "hat")
MACHINES = ("timer", "sides", "rewind")
# 衣服颜色和底色的同色系（随机时底色避开衣服那一系，20 像素时只剩轮廓和颜色也认得出）
FAMILY = {"cyan": "cyan", "camel": "gold", "purple": "purple", "green": "green", "rose": "pink", "rust": "orange"}
DEFAULT_AVATAR = {"hair": "short", "hairColor": "black", "beard": "none", "collar": "round", "clothes": "cyan", "skin": "warm", "bg": "gold",
                  "accColor": "black", "acc": {"glasses": False, "phones": False, "earring": False, "hat": False}}
PARTS = {"hair": HAIR, "hairColor": HAIR_COLORS, "beard": BEARDS, "collar": COLLARS, "clothes": CLOTHES, "skin": SKINS, "bg": BGS,
         "accColor": ACC_COLORS}


# —— 开关 ——————————————————————————————————————————————————————————————————————————————

def cfg() -> dict | None:
    """server.json 的 cybers：不写、false、{"enabled": false}，或者 claw 不是 builtin → None（关着）；true = {}。每次现读。"""
    if not claw.is_builtin():
        return None
    v = raw().get("cybers")
    if v is True:
        return {}
    return v if isinstance(v, dict) and v.get("enabled", True) is not False else None


def on() -> bool:
    return cfg() is not None


def off_text() -> str:
    return L("这台服务器没开赛博（server.json 的 cybers，要用自己的内核）", "Cybers are off on this server (cybers in server.json; needs the built-in engine)")


def enabled() -> None:
    """整个功能关着：/api/cybers… 一律 404（app 据此不显示入口）。"""
    if not on():
        raise HTTPException(404, off_text())


router = APIRouter(dependencies=[Depends(enabled)])


# —— 表 ————————————————————————————————————————————————————————————————————————————————

SCHEMA = """
CREATE TABLE IF NOT EXISTS cybers (id TEXT PRIMARY KEY, no INTEGER NOT NULL, name TEXT NOT NULL, tag TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT 'blank', template TEXT, avatar TEXT, identity TEXT NOT NULL DEFAULT '', tone TEXT NOT NULL DEFAULT '',
    rules TEXT NOT NULL DEFAULT '', voice TEXT, model TEXT, loadout TEXT NOT NULL DEFAULT '[]', builtin TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT);
CREATE TABLE IF NOT EXISTS cyber_notes (id TEXT PRIMARY KEY, cyber TEXT NOT NULL, text TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'chat',
    created_at TEXT NOT NULL, archived_at TEXT);
CREATE INDEX IF NOT EXISTS cyber_notes_by ON cyber_notes(cyber, created_at);
CREATE TABLE IF NOT EXISTS cyber_knowledge (id TEXT PRIMARY KEY, cyber TEXT NOT NULL, title TEXT NOT NULL, text TEXT NOT NULL,
    origin TEXT NOT NULL DEFAULT 'given', source TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, archived_at TEXT);
CREATE INDEX IF NOT EXISTS cyber_knowledge_by ON cyber_knowledge(cyber, created_at);
CREATE TABLE IF NOT EXISTS cyber_changes (id TEXT PRIMARY KEY, cyber TEXT NOT NULL, field TEXT NOT NULL, old TEXT, new TEXT, via TEXT NOT NULL,
    why TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'pending', inbox TEXT, created_at TEXT NOT NULL, decided_at TEXT, version INTEGER);
CREATE INDEX IF NOT EXISTS cyber_changes_by ON cyber_changes(cyber, created_at);
CREATE TABLE IF NOT EXISTS cyber_versions (id INTEGER PRIMARY KEY AUTOINCREMENT, cyber TEXT NOT NULL, field TEXT NOT NULL, old TEXT, new TEXT,
    via TEXT NOT NULL, change TEXT, at TEXT NOT NULL, undone_at TEXT);
CREATE INDEX IF NOT EXISTS cyber_versions_by ON cyber_versions(cyber, id);
CREATE TABLE IF NOT EXISTS cyber_state (cyber TEXT PRIMARY KEY, turns INTEGER NOT NULL DEFAULT 0, reviewed INTEGER NOT NULL DEFAULT 0,
    reviewed_at TEXT, daily_day TEXT);
CREATE TABLE IF NOT EXISTS cyber_meta (key TEXT PRIMARY KEY, value TEXT);
"""
_ready: set[str] = set()


def cydb() -> sqlite3.Connection:
    """应用的库（chat.db）加上赛博的表（每个库只建一次）。"""
    conn = chat.db()
    key = str(chat.DB)
    if key not in _ready:
        conn.executescript(SCHEMA)
        if "daily" not in {r[1] for r in conn.execute("PRAGMA table_info(cybers)")}:  # 「每天读」的开关（装载那一节）
            conn.execute("ALTER TABLE cybers ADD COLUMN daily INTEGER NOT NULL DEFAULT 0")
        _ready.add(key)
    return conn


def loads(s: str | None, default=None):
    if not s:
        return default
    try:
        return json.loads(s)
    except ValueError:
        return default


def dumps(v) -> str:
    return json.dumps(v, ensure_ascii=False)


def is_thread(thread: str | None) -> bool:
    """这个对话线程是不是一位赛博的一对一（线程 id 就是赛博的 id）。"""
    return bool(ID_RE.match(thread or ""))


def row_of(conn: sqlite3.Connection, cid: str, *, deleted: bool = False) -> sqlite3.Row | None:
    if not ID_RE.match(cid or ""):
        return None
    r = conn.execute("SELECT * FROM cybers WHERE id=?", (cid,)).fetchone()
    return r if r is not None and (deleted or not r["deleted_at"]) else None


def get_row(conn: sqlite3.Connection, cid: str) -> sqlite3.Row:
    r = row_of(conn, cid)
    if r is None:
        raise HTTPException(404, L("没有这位赛博", "No such Cyber"))
    return r


def active_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM cybers WHERE deleted_at IS NULL ORDER BY created_at, rowid").fetchall()


def names() -> dict[str, str]:
    """赛博的线程 → 名字（收件箱的卡挂在赛博的对话上时认它、写它的名字）。关着 → 空。"""
    if not on():
        return {}
    try:
        with _lock, cydb() as conn:
            return {r["id"]: r["name"] for r in active_rows(conn)}
    except sqlite3.Error:
        return {}


# —— 形象 ——————————————————————————————————————————————————————————————————————————————

def random_avatar(rng: random.Random | None = None) -> dict:
    """「随机一个」（见文件开头）：发型、发色、衣领、衣服、肤色、底色、配饰颜色各随机；底色避开衣服那一系。"""
    rng = rng or random.Random()
    clothes = rng.choice(CLOTHES)
    return {"hair": rng.choice(HAIR), "hairColor": rng.choice(HAIR_COLORS),
            "beard": "none" if rng.random() < 0.6 else rng.choice(BEARDS[1:]), "collar": rng.choice(COLLARS), "clothes": clothes,
            "skin": rng.choice(SKINS), "bg": rng.choice([b for b in BGS if b != FAMILY[clothes]]), "accColor": rng.choice(ACC_COLORS),
            "acc": {"glasses": rng.random() < 0.4, "phones": rng.random() < 0.25, "earring": rng.random() < 0.25, "hat": rng.random() < 0.15}}


def presets(seed: str, n: int = 3) -> list[dict]:
    """草稿页「换形象」那一排的预设：按名字定的几个（同一个名字每次一样），彼此底色不同。"""
    rng = random.Random(seed)
    out: list[dict] = []
    while len(out) < n:
        a = random_avatar(rng)
        if all(a["bg"] != b["bg"] for b in out):
            out.append(a)
    return out


def upload_ok(fid: str) -> bool:
    """上传的头像：这台服务器上存着、是图片的附件（files.py 的 attachments）。"""
    import files as files_mod  # 延迟导入：files.py 依赖 chat
    with _lock, files_mod.adb() as conn:
        r = conn.execute("SELECT kind FROM attachments WHERE id=?", (fid,)).fetchone()
    return r is not None and r["kind"] == "image"


def clean_avatar(v) -> dict:
    """形象（见文件开头）：上传的 {upload}、机器类的 {machine, bg?}，或者捏的部件（不认识的值 400，缺的照默认补）。"""
    if v is None:
        return dict(DEFAULT_AVATAR)
    if not isinstance(v, dict):
        raise HTTPException(400, L("avatar 要是一个对象", "avatar must be an object"))
    if "upload" in v:
        fid = str(v.get("upload") or "").strip()
        if not re.fullmatch(r"[0-9a-f]{12}", fid) or not upload_ok(fid):
            raise HTTPException(400, L("上传的头像要先经 /api/chat/upload 存成图片附件", "Upload the picture with /api/chat/upload first (it must be an image)"))
        return {"upload": fid}
    if "machine" in v:
        icon = str(v.get("machine") or "")
        if icon not in MACHINES:
            raise HTTPException(400, L(f"machine 只能是 {' / '.join(MACHINES)}", f"machine must be one of {', '.join(MACHINES)}"))
        bg = v.get("bg") if v.get("bg") in BGS else "cyan"
        return {"machine": icon, "bg": bg}
    out = dict(DEFAULT_AVATAR)
    for key, allowed in PARTS.items():
        if key in v:
            if v[key] not in allowed:
                raise HTTPException(400, L(f"{key} 只能是 {' / '.join(allowed)}", f"{key} must be one of {', '.join(allowed)}"))
            out[key] = v[key]
    acc = v.get("acc") if isinstance(v.get("acc"), dict) else {}
    out["acc"] = {a: bool(acc.get(a)) for a in ACCS}
    return out


# —— 一位赛博的样子 ——————————————————————————————————————————————————————————————————————————

FIX_CASE = {"deepseek": "DeepSeek", "gpt": "GPT", "glm": "GLM", "qwen": "Qwen", "llama": "Llama", "kimi": "Kimi", "gemini": "Gemini",
            "claude": "Claude", "mistral": "Mistral", "grok": "Grok", "minimax": "MiniMax"}


def pretty_model(ref: str | None) -> str:
    """给人看的模型名：moonshot/kimi-k3 → Kimi K3、anthropic/claude-sonnet-5-5 → Claude Sonnet 5.5。"""
    if not ref:
        return ""
    name = ref.split("/", 1)[-1].split("/")[-1]
    words = [w for w in re.split(r"[-_\s]+", name) if w]
    out: list[str] = []
    for w in words:
        if out and w.isdigit() and len(w) == 1 and re.fullmatch(r"\d+(?:\.\d+)*", out[-1]):
            out[-1] += "." + w  # 5-5 → 5.5
            continue
        low = w.lower()
        out.append(FIX_CASE.get(low) or (w.upper() if re.fullmatch(r"[a-z]\d[\w.]*", low) else w[:1].upper() + w[1:]))
    return " ".join(out)


def model_in_use(r: sqlite3.Row) -> str | None:
    """这位赛博实际用哪个模型（它自己的；没写 = engine.use.cyber → 主对话是套餐就用套餐，否则 engine.model）。内核还没配模型 → None。"""
    from engine import cyber as engine_cyber
    from engine.errors import EngineError
    try:
        return engine_cyber.model_of({"id": r["id"], "model": r["model"]})
    except EngineError:
        return None


def days_together(created_at: str) -> int:
    """一起几天：建立那天算第 1 天。"""
    try:
        start = datetime.fromisoformat(created_at).astimezone(TZ).date()
    except ValueError:
        return 1
    return max(1, (datetime.now(TZ).date() - start).days + 1)


def cyber_json(r: sqlite3.Row, conn: sqlite3.Connection | None = None) -> dict:
    model = model_in_use(r)
    out = {"id": r["id"], "thread": r["id"], "no": r["no"], "name": r["name"], "tag": r["tag"] or "", "kind": r["kind"],
           "template": r["template"], "avatar": loads(r["avatar"], None) or dict(DEFAULT_AVATAR), "identity": r["identity"] or "",
           "tone": r["tone"] or "", "rules": r["rules"] or "", "voice": r["voice"], "model": r["model"], "modelInUse": model,
           "modelName": pretty_model(model or r["model"]), "loadout": loads(r["loadout"], []) or [], "daily": bool(r["daily"]), "builtin": r["builtin"],
           "createdAt": r["created_at"], "updatedAt": r["updated_at"], "days": days_together(r["created_at"]),
           "deleted": bool(r["deleted_at"])}
    if conn is not None:
        last = conn.execute(f"SELECT id, role, text, ts FROM messages WHERE thread=? AND role IN ('user','{ASSISTANT_ROLE}') ORDER BY id DESC LIMIT 1",
                            (r["id"],)).fetchone()
        out["last"] = ({"id": f"db{last['id']}", "role": last["role"], "text": " ".join((last["text"] or "").split())[:120], "ts": last["ts"]}
                       if last else None)
    return out


def ordered(rows: list[sqlite3.Row], conn: sqlite3.Connection) -> list[dict]:
    """按最近对话排（最后一句的时间，新的在前）；没聊过的排后面，按建立的先后。"""
    items = [cyber_json(r, conn) for r in rows]
    talked = sorted((x for x in items if x["last"]), key=lambda x: chat.instant(x["last"]["ts"]), reverse=True)
    return talked + [x for x in items if not x["last"]]


# —— 校验 ————————————————————————————————————————————————————————————————————————————————

def clean_line(v, cap: int) -> str:
    return re.sub(r"\s+", " ", CTRL.sub("", str(v or ""))).strip()[:cap]


def clean_name(v, conn: sqlite3.Connection, cid: str | None = None) -> str:
    """名字：去掉控制字符，1–24 个字；和别的（没删的）赛博重名（不分大小写）→ 409，群里 @ 它才不会认错。"""
    name = clean_line(v, NAME_MAX)
    if not name:
        raise HTTPException(400, L("赛博要有名字", "A Cyber needs a name"))
    for r in active_rows(conn):
        if r["id"] != cid and r["name"].casefold() == name.casefold():
            raise HTTPException(409, L(f"已经有一位叫「{name}」的赛博了", f'There is already a Cyber called "{name}"'))
    return name


def clean_model(v) -> str | None:
    """模型：不写 = 跟 engine.use.cyber。要是我们的接口跑得了的 provider/model，或者用户自己的套餐（替他干活的回合才花，存的时候不要求已经登录）。"""
    from engine import models as engine_models
    ref = str(v or "").strip()
    if not ref:
        return None
    if engine_models.subscription(ref) or engine_models.valid_ref(ref):
        return engine_models.canonical(ref)
    raise HTTPException(400, L(f"不认识这个模型：{ref}（写成 provider/model）", f"Unknown model: {ref} (use provider/model)"))


def clean_voice(v) -> str | None:
    s = str(v or "").strip()
    if not s:
        return None
    if not VOICE_RE.match(s):
        raise HTTPException(400, L("voice 是一个声音的名字：字母、数字和 . : - _，最多 40 个字符", "voice is a voice name: letters, digits and . : - _, at most 40 characters"))
    return s


class CyberIn(BaseModel):
    name: str
    tag: str = ""
    kind: str = "blank"
    template: str | None = None
    avatar: dict | None = None
    identity: str = ""
    tone: str = ""
    rules: str = ""
    voice: str | None = None
    model: str | None = None
    loadout: list[str] = []  # 新建时一起装上的功能 Agent（一句话新建的草稿里建议的；照装载那一节的规矩查）


class CyberPatch(BaseModel):
    """只带要改的字段；给 null 等于没给（model / voice 例外：null 或 "" = 回到默认）。"""
    name: str | None = None
    tag: str | None = None
    avatar: dict | None = None
    identity: str | None = None
    tone: str | None = None
    rules: str | None = None
    voice: str | None = None
    model: str | None = None


def next_no(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT IFNULL(MAX(no), 0) FROM cybers").fetchone()[0]) + 1


def insert(conn: sqlite3.Connection, *, name: str, tag: str = "", kind: str = "blank", template: str | None = None, avatar: dict | None = None,
           identity: str = "", tone: str = "", rules: str = "", voice: str | None = None, model: str | None = None,
           builtin: str | None = None) -> str:
    """建一行（字段已经清理过），回 id。调用方拿着锁和连接。"""
    while True:
        cid = f"cy-{uuid.uuid4().hex[:8]}"
        if conn.execute("SELECT 1 FROM cybers WHERE id=?", (cid,)).fetchone() is None:
            break
    ts = now_iso()
    conn.execute("""INSERT INTO cybers(id, no, name, tag, kind, template, avatar, identity, tone, rules, voice, model, loadout, builtin,
        created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                 (cid, next_no(conn), name, tag, kind, template, dumps(avatar or DEFAULT_AVATAR), identity, tone, rules, voice, model, "[]",
                  builtin, ts, ts))
    return cid


# —— 默认的两位（第 9 件）：Iris 主持、阿哲外行 ———————————————————————————————————————————————————
# 赛博开着、这台服务器还没建过：建好这两位（第二步播客的录前讨论用它们：主持人模式 Iris、费曼模式阿哲）。只建一次（cyber_meta 记下），
# 删了不再建；你已经有一位同名的就不建那一位。没开赛博的安装一切照旧。存的是服务器的语言（LS），以后照样能改。
# 什么时候建：服务启动时（start）、app 第一次来列赛博或通讯录时（开关是现读的，开了不用重启）。builtin 列记着是哪一位（by_builtin 找它）。

DEFAULT_CYBERS: tuple[tuple[str, dict], ...] = (
    ("iris", {"name": ("Iris", "Iris"), "tag": ("主持", "Host"), "kind": "pro",
              "identity": ("播客和讨论的主持人：帮你把想法说清楚。", "A podcast and discussion host who helps you say what you mean."),
              "tone": ("温和、好奇，句子短。", "Warm and curious, in short sentences."),
              "rules": ("会追问，一次只问一个问题；不替你下结论；收尾时帮你理出两三点。",
                        "Follows up with one question at a time; never draws your conclusions for you; at the end, sums up two or three points."),
              "avatar": {"hair": "bob", "hairColor": "dark", "beard": "none", "collar": "v", "clothes": "purple", "skin": "wheat", "bg": "gold",
                         "accColor": "gold", "acc": {"glasses": False, "phones": True, "earring": True, "hat": False}}}),
    ("zhe", {"name": ("阿哲", "Zhe"), "tag": ("外行", "Layman"), "kind": "friend",
             "identity": ("不是这一行的朋友，好奇心重。", "A friend from outside your field, endlessly curious."),
             "tone": ("随意，像朋友聊天。", "Casual, like chatting with a friend."),
             "rules": ("听不懂就直说并追问；常要你举一个例子；不装懂。", "Says so when lost and asks; often asks for an example; never pretends to follow."),
             "avatar": {"hair": "curly", "hairColor": "black", "beard": "stubble", "collar": "round", "clothes": "green", "skin": "warm", "bg": "orange",
                        "accColor": "black", "acc": {"glasses": True, "phones": False, "earring": False, "hat": False}}}),
)


def seed_defaults() -> list[str]:
    """见上：→ 这一次建的 id（建过了、开关关着 → []）。"""
    from i18n import LS
    if not on():
        return []
    made: list[tuple[str, str]] = []
    with _lock, cydb() as conn:
        if conn.execute("SELECT 1 FROM cyber_meta WHERE key='defaults'").fetchone():
            return []
        for key, d in DEFAULT_CYBERS:
            name = LS(*d["name"])
            if conn.execute("SELECT 1 FROM cybers WHERE builtin=?", (key,)).fetchone() or any(
                    r["name"].casefold() == name.casefold() for r in active_rows(conn)):
                continue
            made.append((insert(conn, name=name, tag=LS(*d["tag"]), kind=d["kind"], avatar=dict(d["avatar"]), identity=LS(*d["identity"]),
                                tone=LS(*d["tone"]), rules=LS(*d["rules"]), builtin=key), name))
        conn.execute("INSERT INTO cyber_meta(key, value) VALUES('defaults', ?)", (now_iso(),))
    if made:
        names_ = LS("、", ", ").join(n for _, n in made)
        log_activity(LS(f"建好了默认的赛博：{names_}", f"Created the default Cybers: {names_}"), "edit")
    return [cid for cid, _ in made]


def by_builtin(key: str) -> sqlite3.Row | None:
    """默认的那一位（iris / zhe）还在的话 → 它那一行；删了、没建 → None。"""
    with _lock, cydb() as conn:
        return conn.execute("SELECT * FROM cybers WHERE builtin=? AND deleted_at IS NULL", (key,)).fetchone()


# —— 接口 ————————————————————————————————————————————————————————————————————————————————

@router.get("/api/cybers")
def list_cybers():
    """没删的赛博，按最近对话排（见 ordered）。第一次来时建好默认的两位（seed_defaults）。"""
    seed_defaults()
    with _lock, cydb() as conn:
        return {"ok": True, "cybers": ordered(active_rows(conn), conn)}


@router.post("/api/cybers")
def create(body: CyberIn):
    kind = body.kind if body.kind in KINDS else "blank"
    avatar = clean_avatar(body.avatar)
    tag, identity, tone, rules = (clean_line(body.tag, TAG_MAX), clean_line(body.identity, LINE_MAX), clean_line(body.tone, LINE_MAX),
                                  clean_line(body.rules, LINE_MAX))
    voice, model = clean_voice(body.voice), clean_model(body.model)
    template = clean_line(body.template, 40) or None
    with _lock, cydb() as conn:
        name = clean_name(body.name, conn)
        loadout = check_loadout(conn, body.loadout)
        cid = insert(conn, name=name, tag=tag, kind=kind, template=template, avatar=avatar, identity=identity, tone=tone, rules=rules,
                     voice=voice, model=model)
        if loadout:
            conn.execute("UPDATE cybers SET loadout=? WHERE id=?", (dumps(loadout), cid))
        out = cyber_json(get_row(conn, cid), conn)
    log_activity(L(f"新建赛博「{name}」", f'Created the Cyber "{name}"'), "edit")
    return {"ok": True, "cyber": out}


@router.get("/api/cybers/{cid}")
def get_cyber(cid: str):
    """一位（资料页）：外加记下几条、知识库几份、还在等你点头的改法几个、最近一次改动（「刚改 · 撤销」，一天以内、没撤过的）。"""
    with _lock, cydb() as conn:
        r = get_row(conn, cid)
        settle_cards(conn, cid)
        out = cyber_json(r, conn)
        out["notes"] = conn.execute("SELECT COUNT(*) FROM cyber_notes WHERE cyber=? AND archived_at IS NULL", (cid,)).fetchone()[0]
        out["knowledge"] = conn.execute("SELECT COUNT(*) FROM cyber_knowledge WHERE cyber=? AND archived_at IS NULL", (cid,)).fetchone()[0]
        out["pending"] = conn.execute("SELECT COUNT(*) FROM cyber_changes WHERE cyber=? AND status='pending'", (cid,)).fetchone()[0]
        v = conn.execute("SELECT * FROM cyber_versions WHERE cyber=? AND undone_at IS NULL AND julianday(at)>=julianday('now','-1 day') "
                         "ORDER BY id DESC LIMIT 1", (cid,)).fetchone()
        out["recent"] = version_json(v) if v is not None else None
        return {"ok": True, "cyber": out}


@router.patch("/api/cybers/{cid}")
def patch(cid: str, body: CyberPatch):
    """只改给了的、真变了的字段。"""
    sets: dict[str, object] = {}
    avatar = dumps(clean_avatar(body.avatar)) if body.avatar is not None else None  # 在锁外查（上传的要读附件表，同一把锁）
    voice = clean_voice(body.voice) if "voice" in body.model_fields_set else None
    model = clean_model(body.model) if "model" in body.model_fields_set else None
    with _lock, cydb() as conn:
        r = get_row(conn, cid)
        if body.name is not None and (name := clean_name(body.name, conn, cid)) != r["name"]:
            sets["name"] = name
        if body.tag is not None and (tag := clean_line(body.tag, TAG_MAX)) != (r["tag"] or ""):
            sets["tag"] = tag
        for key in ("identity", "tone", "rules"):
            v = getattr(body, key)
            if v is not None and (line := clean_line(v, LINE_MAX)) != (r[key] or ""):
                sets[key] = line
        if avatar is not None and avatar != r["avatar"]:
            sets["avatar"] = avatar
        if "voice" in body.model_fields_set and voice != r["voice"]:
            sets["voice"] = voice
        if "model" in body.model_fields_set and model != r["model"]:
            sets["model"] = model
        for key, val in sets.items():  # 每一项改动记一个版本（能撤销到上一版）
            write_field(conn, r, key, val, via="edit")
        out = cyber_json(get_row(conn, cid), conn)
    return {"ok": True, "cyber": out, "changed": [k for k in sets if k != "updated_at"]}


@router.delete("/api/cybers/{cid}")
async def delete(cid: str):
    """删除赛博：记下 deleted_at（不真删，对话记录留着），记下的、知识库移进归档，还在等你点头的改法撤回；列表、通讯录里不再出现。"""
    ts = now_iso()
    with _lock, cydb() as conn:
        r = get_row(conn, cid)
        conn.execute("UPDATE cybers SET deleted_at=?, updated_at=? WHERE id=?", (ts, ts, cid))
        conn.execute("UPDATE cyber_notes SET archived_at=? WHERE cyber=? AND archived_at IS NULL", (ts, cid))
        conn.execute("UPDATE cyber_knowledge SET archived_at=? WHERE cyber=? AND archived_at IS NULL", (ts, cid))
        open_cards = [x["inbox"] for x in conn.execute("SELECT inbox FROM cyber_changes WHERE cyber=? AND status='pending' AND inbox IS NOT NULL", (cid,))]
        conn.execute("UPDATE cyber_changes SET status='dropped', decided_at=? WHERE cyber=? AND status='pending'", (ts, cid))
    for iid in open_cards:
        await close_card(iid)
    log_activity(L(f"删除了赛博「{r['name']}」（记忆移进归档）", f'Deleted the Cyber "{r["name"]}" (its memory moved to the archive)'), "deleted")
    return {"ok": True}


# —— 一对一：赛博回合 ————————————————————————————————————————————————————————————————————
# app 的 /api/chat/send 发到线程 cy-…：chat.start_run 认出来（模型是这位赛博的），chat.engine_stream 交给 respond()，跑一轮赛博回合
# （engine/cyber.chat：它自己今天的会话，cyber:<id>:<逻辑日>）。流的事件、存进 messages、未读都和别的对话一样，app 的对话页不用改。
# 赛博回合替用户自己干活时能花他登录着的套餐（models.may_spend 看来路，不看 kind）。替外人答的换成按量的。

def persona(r: sqlite3.Row) -> str:
    """性格三行 → 赛博回合开头的「你是谁、怎么说话」（借 Hermes hermes_cli/personality.py 的 render_personality_prompt：
    一段身份，后面跟语气、规矩各一行；只借样子，没拿代码）。"""
    parts = [(r["identity"] or "").strip(), f"Tone: {r['tone'].strip()}" if (r["tone"] or "").strip() else "",
             f"Rules: {r['rules'].strip()}" if (r["rules"] or "").strip() else ""]
    return "\n".join(p for p in parts if p)


def engine_dict(r: sqlite3.Row) -> dict:
    """存起来的一位 → engine/cyber.py 认的 dict。每位赛博都知道你的名片（sources card）。"""
    return {"id": r["id"], "name": r["name"], "identity": persona(r), "model": r["model"], "sources": ["card"]}


def thread_model(thread: str) -> str:
    """这个赛博对话用哪个模型（chat.start_run 开跑前问）：功能关着、没有这位 → 404；内核还没配模型 → 503。"""
    if not on():
        raise HTTPException(404, off_text())
    with _lock, cydb() as conn:
        r = row_of(conn, thread)
    if r is None:
        raise HTTPException(404, L("没有这位赛博", "No such Cyber"))
    model = model_in_use(r)
    if not model:
        raise HTTPException(503, L("内核还没配模型：server.json 的 engine.model，或者给这位赛博选一个", "The engine has no model yet: set engine.model in server.json, or pick one for this Cyber"))
    return model


def split_content(content: str | list) -> tuple[str, list[dict]]:
    """chat.py 拼好的这一句（字符串，或者带图的 OpenAI 格式 content 数组）→ (字, 中立格式的图块)。"""
    if isinstance(content, str):
        return content, []
    from engine import owner as engine_owner
    msgs = engine_owner.neutral([{"role": "user", "content": content}])
    blocks = msgs[0]["content"] if msgs else []
    text = "\n\n".join(b["text"] for b in blocks if b.get("type") == "text")
    return text, [b for b in blocks if b.get("type") == "image"]


async def respond(thread: str, content: str | list, on_text, *, origin: str = "user", control=None, on_event=None, ref: int | None = None):
    """跑一轮赛博回合（chat.engine_stream 调）：回复的字一段段交给 on_text（两次开口之间隔一个空行，同 owner.respond），内核的事件交给
    on_event（「在想 / 在做」）。→ engine 的 Result。没有这位（刚删了）抛 RuntimeError（chat.py 记成没拿到回复）。"""
    from engine import cyber as engine_cyber
    from engine import cyber_tools
    text, images = split_content(content)
    with _lock, cydb() as conn:
        r = row_of(conn, thread)
        memory, note = memory_for(conn, r, text) if r is not None else ("", "")
        loaded = loadout_of(conn, r) if r is not None else []
    if r is None:
        raise RuntimeError(L("这位赛博已经删除", "This Cyber was deleted"))
    tools = [t for t in [cyber_tools.ask_agent_tool(engine_dict(r), loaded, thread=r["id"], ask=asker(r["id"])),
                         cyber_tools.propose_tool(propose=proposer(r["id"]))] if t is not None]
    spoke = {"any": False, "gap": False}

    def on_ev(ev: dict) -> None:
        if ev.get("type") == "model":  # 又问一次模型（调过工具）：这次再开口就和前面那段隔开
            spoke["gap"] = spoke["any"]
        elif ev.get("type") == "text" and ev.get("text"):
            if spoke["gap"]:
                on_text("\n\n")
                spoke["gap"] = False
            spoke["any"] = True
            on_text(ev["text"])
        if on_event is not None:
            on_event(ev)

    res = await engine_cyber.chat(engine_dict(r), text, on_event=on_ev, control=control, origin=origin, images=images, ref=ref, stamp=True,
                                  memory=memory, note=note, tools=tools)
    cyber_tools.forget(res.turn)
    after_turn(r["id"], res.status)
    return res


# —— 对话列表、未读 ——————————————————————————————————————————————————————————————————————

def chat_rows() -> list[dict]:
    """对话列表（chats.py）里赛博的一对一：聊过才出现（这个线程有消息）。关着 → []。"""
    if not on():
        return []
    with _lock, cydb() as conn:
        out = []
        for r in active_rows(conn):
            if conn.execute("SELECT 1 FROM messages WHERE thread=? LIMIT 1", (r["id"],)).fetchone() is None:
                continue
            c = cyber_json(r)
            out.append({"id": r["id"], "title": r["name"], "tag": c["tag"], "model": c["modelName"], "avatar": c["avatar"], "no": r["no"]})
    return out


def tracked(conn: sqlite3.Connection) -> list[str]:
    """给 unread.tracked：没删的赛博的对话（传进来的 conn 已经拿着锁；关着 → []）。"""
    if not on() or not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='cybers'").fetchone():
        return []
    return [r["id"] for r in conn.execute("SELECT id FROM cybers WHERE deleted_at IS NULL ORDER BY created_at")]


# —— 记忆：名片、记下的、知识库 ——————————————————————————————————————————————————————————————
# 名片：每位赛博都知道（engine/cyber.py 的 card_text：名片 Agent 给「朋友」这一档放出来的那部分：你的名字、在做的事、忙闲）。
# 记下的：它自己长的，只记和你聊过的；每一条都是你点了头才记下的（改法那一套：先给样子），能看、能删、能清空。
# 知识库：你给的资料、它每天读的（装载的功能 Agent 的日报），每一条写明来源。
# 备料（memory_for）：开头的最后一节放最近的几条记下的、知识库最近几份的全文和别的标题；这一句相关的旧笔记、资料片段跟在这句话后面
# （engine/cyber.chat 的 note），开头不跟着每句话变。健康、饮食、睡眠不给赛博：这几类的话不记（HEALTH）。

NOTE_MAX, NOTES_SHOWN, NOTES_RELEVANT = 300, 12, 5
KNOW_TITLE_MAX, KNOW_TEXT_MAX, KNOW_SHOWN, KNOW_FULL, KNOW_FULL_CHARS, KNOW_EXCERPT, KNOW_RELEVANT = 80, 60_000, 10, 2, 2_500, 1_200, 3
# 健康、饮食、睡眠（和名片 Agent 的 HEALTH 同一个意思，多了饮食、睡眠、身体数字）：赛博不记、管家也不说
HEALTH = re.compile(r"健康|饮食|睡眠|睡觉|失眠|熬夜|午睡|体重|体脂|减肥|减脂|节食|血压|血糖|心率|卡路里|热量|医生|医院|看病|吃药|用药|体检|"
                    r"生病|病情|症状|治疗|心理咨询|"
                    r"\b(?:health\w*|diet\w*|sleep\w*|insomnia|naps?|bmi|blood pressure|glucose|heart rate|hrv|calorie\w*|kcal|"
                    r"doctors?|hospital\w*|clinic\w*|medic\w*|symptom\w*|diagnos\w*|therap\w*|weight loss|lose weight|body fat)\b", re.I)
WORD = re.compile(r"[a-z0-9][a-z0-9_.+-]{2,}", re.I)
HAN = re.compile(r"[一-鿿]+")


def tokens(text: str) -> set[str]:
    """粗分词：英文三个字母以上的词（小写），中文切成两字词（同 engine/recall.py 的中文切法）。"""
    low = (text or "").lower()
    out = {w for w in WORD.findall(low)}
    for run in HAN.findall(low):
        out |= {run[i:i + 2] for i in range(len(run) - 1)} if len(run) > 1 else {run}
    return out


def relevant(rows: list, text: str, field: str, n: int, skip: set[str] | None = None) -> list:
    """按这句话挑相关的几条（词有重叠，重叠多的在前）；skip 里的 id 不要（开头已经有了）。"""
    want = tokens(text)
    if not want:
        return []
    scored = []
    for r in rows:
        if skip and r["id"] in skip:
            continue
        s = len(want & tokens(r[field] if field != "both" else f"{r['title']} {r['text']}"))
        if s:
            scored.append((s, r))
    scored.sort(key=lambda x: -x[0])
    return [r for _, r in scored[:n]]


def note_rows(conn: sqlite3.Connection, cid: str) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM cyber_notes WHERE cyber=? AND archived_at IS NULL ORDER BY created_at DESC, rowid DESC", (cid,)).fetchall()


def knowledge_rows(conn: sqlite3.Connection, cid: str) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM cyber_knowledge WHERE cyber=? AND archived_at IS NULL ORDER BY created_at DESC, rowid DESC", (cid,)).fetchall()


def day_label(ts: str) -> str:
    try:
        return datetime.fromisoformat(ts).astimezone(TZ).strftime("%m/%d")
    except ValueError:
        return ts[:10]


def memory_for(conn: sqlite3.Connection, r: sqlite3.Row, text: str) -> tuple[str, str]:
    """这一轮的记忆（见上）→ (开头最后一节, 跟在这句话后面的那条)。都没有 → ("", "")。"""
    from engine.tasks import data_block
    from config import settings
    owner = settings.user_name or "the user"
    notes, know = note_rows(conn, r["id"]), knowledge_rows(conn, r["id"])
    parts: list[str] = []
    shown = notes[:NOTES_SHOWN]
    if shown:
        parts.append(f"## What you've kept\nNotes from your talks with {owner}, each one approved by them (newest first):\n"
                     + "\n".join(f"- {n['text']}" for n in shown))
    if know:
        lines = [f"- {k['title']} ({k['source'] or k['origin']}, {day_label(k['created_at'])})" for k in know[:KNOW_SHOWN]]
        full = [data_block(f"{k['title']} ({k['source'] or k['origin']})", k["text"], KNOW_FULL_CHARS) for k in know[:KNOW_FULL]]
        parts.append(f"## Your knowledge base\nMaterial {owner} gave you or you read, each with where it came from:\n" + "\n".join(lines)
                     + ("\n\n" + "\n\n".join(x for x in full if x) if any(full) else ""))
    extra: list[str] = []
    older = relevant(notes[NOTES_SHOWN:], text, "text", NOTES_RELEVANT)
    if older:
        extra.append("Older notes that may be relevant:\n" + "\n".join(f"- {n['text']}" for n in older))
    hits = relevant(know, text, "both", KNOW_RELEVANT, {k["id"] for k in know[:KNOW_FULL]})
    for k in hits:
        extra.append(data_block(f"From your knowledge base: {k['title']} ({k['source'] or k['origin']})", k["text"], KNOW_EXCERPT))
    note = ("Possibly relevant from your memory for this message:\n\n" + "\n\n".join(x for x in extra if x)) if extra else ""
    return "\n\n".join(parts), note


def note_json(n: sqlite3.Row) -> dict:
    return {"id": n["id"], "text": n["text"], "source": n["source"], "createdAt": n["created_at"]}


def knowledge_json(k: sqlite3.Row, full: bool = False) -> dict:
    out = {"id": k["id"], "title": k["title"], "origin": k["origin"], "source": k["source"], "createdAt": k["created_at"], "chars": len(k["text"]),
           "excerpt": " ".join(k["text"].split())[:160]}
    if full:
        out["text"] = k["text"]
    return out


def add_note(conn: sqlite3.Connection, cid: str, text: str, source: str) -> str:
    """记下一条（调用方拿着锁；已经是你点过头的）。提交以后再调 noted() 在记性的闸里记一笔。"""
    nid = f"cn-{uuid.uuid4().hex[:10]}"
    conn.execute("INSERT INTO cyber_notes(id, cyber, text, source, created_at) VALUES(?,?,?,?,?)", (nid, cid, text, source, now_iso()))
    return nid


def noted(cid: str, nid: str) -> None:
    """记性的闸里记一笔（engine/memgate.py：赛博长它自己的那份，own）。要在写记下的那一笔提交以后调：事件流是同一个库，
    事务还开着时另起一个连接写会一直等锁。"""
    try:
        from engine import memgate
        memgate.record(memgate.decide("leaf", "add", turn="cyber", own=True), target=nid, author=cid, conversation=f"cyber:{cid}", agent=cid,
                       result=nid)
    except Exception:  # noqa: BLE001 — 事件流没记上不影响记下
        pass


def add_knowledge(conn: sqlite3.Connection, cid: str, title: str, text: str, *, origin: str, source: str) -> str:
    kid = f"ck-{uuid.uuid4().hex[:10]}"
    conn.execute("INSERT INTO cyber_knowledge(id, cyber, title, text, origin, source, created_at) VALUES(?,?,?,?,?,?,?)",
                 (kid, cid, title, text, origin, source, now_iso()))
    return kid


@router.get("/api/cybers/{cid}/memory")
def memory(cid: str):
    """资料页的「记忆」卡：名片（每位赛博都知道的那几行）、记下的（条数 + 最近几条）、知识库（条数 + 最近几份）。"""
    import cardagent  # 延迟导入：名片的资料按「朋友」这一档
    from config import settings
    with _lock, cydb() as conn:
        get_row(conn, cid)
        notes, know = note_rows(conn, cid), knowledge_rows(conn, cid)
    try:
        mats, _ = cardagent.build_material(cardagent.scopes("friend"), settings.language)
        card = [{"label": m["label"], "text": m["text"]} for m in mats if m.get("text")]
    except Exception:  # noqa: BLE001 — 名片读不了：这一块空着
        card = []
    return {"ok": True, "card": card, "notes": {"count": len(notes), "items": [note_json(n) for n in notes[:3]]},
            "knowledge": {"count": len(know), "items": [knowledge_json(k) for k in know[:5]]},
            "rule": L("健康、饮食、睡眠不给赛博", "Health, diet and sleep are never given to a Cyber")}


@router.get("/api/cybers/{cid}/notes")
def list_notes(cid: str):
    with _lock, cydb() as conn:
        get_row(conn, cid)
        rows = note_rows(conn, cid)
    return {"ok": True, "notes": [note_json(n) for n in rows], "count": len(rows)}


@router.delete("/api/cybers/{cid}/notes/{nid}")
def delete_note(cid: str, nid: str):
    with _lock, cydb() as conn:
        get_row(conn, cid)
        if conn.execute("DELETE FROM cyber_notes WHERE id=? AND cyber=?", (nid, cid)).rowcount == 0:
            raise HTTPException(404, L("没有这一条", "No such note"))
    return {"ok": True}


@router.delete("/api/cybers/{cid}/notes")
def clear_notes(cid: str):
    """清空记下的（只删这位赛博的；别的记忆、对话记录不动）。"""
    with _lock, cydb() as conn:
        r = get_row(conn, cid)
        n = conn.execute("DELETE FROM cyber_notes WHERE cyber=?", (cid,)).rowcount
    log_activity(L(f"清空了赛博「{r['name']}」记下的 {n} 条", f'Cleared {n} notes the Cyber "{r["name"]}" kept'), "deleted")
    return {"ok": True, "deleted": n}


class KnowledgeIn(BaseModel):
    title: str = ""
    text: str = ""
    file: str | None = None  # 先经 /api/chat/upload 存成附件的文档（抽好的字）
    source: str = ""


@router.get("/api/cybers/{cid}/knowledge")
def list_knowledge(cid: str):
    with _lock, cydb() as conn:
        get_row(conn, cid)
        rows = knowledge_rows(conn, cid)
    return {"ok": True, "items": [knowledge_json(k) for k in rows], "count": len(rows)}


@router.post("/api/cybers/{cid}/knowledge")
def give_knowledge(cid: str, body: KnowledgeIn):
    """你给的资料：一段字（title + text），或者一个上传过的文档（file = 附件 id，用抽好的字）。来源写明。"""
    title, text, source = clean_line(body.title, KNOW_TITLE_MAX), (body.text or "").strip(), clean_line(body.source, 80)
    if body.file:
        import files as files_mod  # 延迟导入
        with _lock, files_mod.adb() as conn:
            a = conn.execute("SELECT name, text FROM attachments WHERE id=?", (body.file,)).fetchone()
        if a is None or not (a["text"] or "").strip():
            raise HTTPException(400, L("这个文件没有抽出字（先经 /api/chat/upload 上传文档）", "That file has no extracted text (upload a document with /api/chat/upload first)"))
        title, text, source = title or clean_line(a["name"], KNOW_TITLE_MAX), a["text"], source or clean_line(a["name"], 80)
    if not text:
        raise HTTPException(400, L("资料是空的", "The material is empty"))
    with _lock, cydb() as conn:
        get_row(conn, cid)
        kid = add_knowledge(conn, cid, title or text.split("\n", 1)[0][:KNOW_TITLE_MAX], text[:KNOW_TEXT_MAX], origin="given",
                            source=source or L("你给的", "From you"))
        k = conn.execute("SELECT * FROM cyber_knowledge WHERE id=?", (kid,)).fetchone()
    return {"ok": True, "item": knowledge_json(k)}


@router.delete("/api/cybers/{cid}/knowledge/{kid}")
def delete_knowledge(cid: str, kid: str):
    with _lock, cydb() as conn:
        get_row(conn, cid)
        if conn.execute("DELETE FROM cyber_knowledge WHERE id=? AND cyber=?", (kid, cid)).rowcount == 0:
            raise HTTPException(404, L("没有这一份", "No such item"))
    return {"ok": True}


# —— 改法：先给改好的样子，点头才写进去，每次改记一个版本，能撤销 ————————————————————————————————————
# 三条路落到同一个接口（propose → apply / drop，undo）：
#   对话里说（赛博回合的 propose_change 工具，engine/cyber_tools.py）、它自己提（回看、每天读）→ 一张收件箱卡挂在这位赛博的对话上
#   （kind tool，engine/calls.py 的「前缀:名字」= cyber:change：卡上定死是哪一个改法；同意 = 照它改一次）；
#   资料页点一行（POST /api/cybers/{id}/changes，via profile）→ 回改好的样子，app 上「改 / 不改」直接调 apply / drop，不出卡。
# 改的是性格三行、标签、声音，或者记下一条（note）。直接改（PATCH）的名字、形象、模型也记版本，同样能撤销。

FIELDS = {"identity": ("身份", "Identity"), "tone": ("语气", "Tone"), "rules": ("规矩", "Rules"), "tag": ("标签", "Tag"), "voice": ("声音", "Voice"),
          "name": ("名字", "Name"), "avatar": ("形象", "Avatar"), "model": ("模型", "Model"), "note": ("记下的", "Notes")}
PROPOSABLE = ("identity", "tone", "rules", "tag", "voice", "note")  # 改法（propose）能改的；名字、形象、模型在资料页直接改
VIAS = ("chat", "profile", "review", "daily", "edit", "group")
CARD_VIAS = ("chat", "review", "daily", "group")  # 这几条路出收件箱卡（资料页上是你自己点的，不用卡）；group：群聊小结里它记下的（groupchat.py）
CHANGE_TTL_DAYS = 14  # 没点的改法多久以后不算数（卡也过期）
# 「调一调」的五个改法（新建时的试聊、资料页点一行都用）：给模型的话 + 给人看的名字
TWEAKS = {"stricter": "Make it stricter: higher standards, more direct criticism.",
          "gentler": "Make it gentler: warmer and more encouraging.",
          "shorter": "Make it more concise: shorter replies.",
          "english": "Make it talk in English from now on (write the new value in English).",
          "examples": "Make it give more concrete examples."}
TWEAK_LABELS = {"stricter": ("更严格", "Stricter"), "gentler": ("更温和", "Gentler"), "shorter": ("更简短", "Shorter"), "english": ("用英文", "In English"),
                "examples": ("多举例", "More examples")}


def field_label(field: str) -> str:
    return L(*FIELDS.get(field, (field, field)))


def check_value(field: str, v) -> str:
    """改好以后的值：性格三行 400 字、标签 16 字、声音照 clean_voice、记下的一条 300 字（健康、饮食、睡眠的不记）。"""
    if field not in PROPOSABLE:
        raise HTTPException(400, L(f"field 只能是 {' / '.join(PROPOSABLE)}", f"field must be one of {', '.join(PROPOSABLE)}"))
    if field == "voice":
        return clean_voice(v) or ""
    val = clean_line(v, {"tag": TAG_MAX, "note": NOTE_MAX}.get(field, LINE_MAX))
    if field == "note" and not val:
        raise HTTPException(400, L("要记下的那一条是空的", "The note is empty"))
    if field == "note" and HEALTH.search(val):
        raise HTTPException(400, L("健康、饮食、睡眠的事不给赛博，也不记", "Health, diet and sleep aren't given to a Cyber, so this isn't kept"))
    return val


def change_json(c: sqlite3.Row) -> dict:
    return {"id": c["id"], "cyber": c["cyber"], "field": c["field"], "label": field_label(c["field"]), "old": c["old"] or "", "new": c["new"] or "",
            "via": c["via"], "why": c["why"] or "", "status": c["status"], "inbox": c["inbox"], "createdAt": c["created_at"],
            "decidedAt": c["decided_at"], "version": c["version"]}


def version_json(v: sqlite3.Row) -> dict:
    """inbox：从卡上来的改法的那张卡（查询里 LEFT JOIN 了 cyber_changes 才有）：app 在对话里那张卡的回执上「撤销」认它。"""
    old, new = v["old"], v["new"]
    if v["field"] == "note":
        new = (loads(new, {}) or {}).get("text", new)
    return {"id": v["id"], "field": v["field"], "label": field_label(v["field"]), "old": old if v["field"] != "avatar" else loads(old, None),
            "new": new if v["field"] != "avatar" else loads(new, None), "via": v["via"], "change": v["change"], "at": v["at"],
            "undone": bool(v["undone_at"]), "inbox": v["inbox"] if "inbox" in v.keys() else None}


VERSIONS = "SELECT v.*, c.inbox AS inbox FROM cyber_versions v LEFT JOIN cyber_changes c ON c.id = v.change WHERE v.cyber=?"


def write_field(conn: sqlite3.Connection, r: sqlite3.Row, field: str, value, *, via: str, change: str | None = None) -> int:
    """写进去并记一个版本（调用方拿着锁、值已经清理过）。note = 记下一条（版本里存这一条的 id 和字，撤销 = 删掉它）。→ 版本 id。"""
    ts = now_iso()
    if field == "note":
        nid = add_note(conn, r["id"], str(value), via)
        old, new = None, dumps({"id": nid, "text": value})
    else:
        old, new = r[field], value
        conn.execute(f"UPDATE cybers SET {field}=?, updated_at=? WHERE id=?", (value, ts, r["id"]))  # noqa: S608 — field 是 FIELDS 里的
    cur = conn.execute("INSERT INTO cyber_versions(cyber, field, old, new, via, change, at) VALUES(?,?,?,?,?,?,?)",
                       (r["id"], field, old, new, via, change, ts))
    return int(cur.lastrowid or 0)


def settle_cards(conn: sqlite3.Connection, cid: str) -> None:
    """卡在收件箱里拒了、撤回了、过期了的改法：这边也不算数了（不另挂钩子，列的时候对一下）；太久没点的也不算数。"""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='inbox'").fetchone():
        return
    ts = now_iso()
    for c in conn.execute("SELECT id, inbox, created_at FROM cyber_changes WHERE cyber=? AND status='pending'", (cid,)).fetchall():
        st = conn.execute("SELECT status FROM inbox WHERE id=?", (c["inbox"],)).fetchone() if c["inbox"] else None
        old = (datetime.now(TZ) - datetime.fromisoformat(c["created_at"]).astimezone(TZ)).days >= CHANGE_TTL_DAYS
        if (st is not None and st["status"] in ("rejected", "withdrawn", "expired")) or old:
            conn.execute("UPDATE cyber_changes SET status='dropped', decided_at=? WHERE id=?", (ts, c["id"]))


def new_change(conn: sqlite3.Connection, r: sqlite3.Row, field: str, value: str, *, via: str, why: str) -> tuple[sqlite3.Row, bool]:
    """记下一个改法（还没写进去）。同一项、同样改好的样子还在等 → 还是那一个（第二个值 False）。"""
    settle_cards(conn, r["id"])
    same = conn.execute("SELECT * FROM cyber_changes WHERE cyber=? AND field=? AND new=? AND status='pending' ORDER BY created_at DESC LIMIT 1",
                        (r["id"], field, value)).fetchone()
    if same is not None:
        return same, False
    pid = f"cc-{uuid.uuid4().hex[:10]}"
    old = None if field == "note" else r[field]
    conn.execute("INSERT INTO cyber_changes(id, cyber, field, old, new, via, why, created_at) VALUES(?,?,?,?,?,?,?,?)",
                 (pid, r["id"], field, old, value, via, clean_line(why, 300), now_iso()))
    return conn.execute("SELECT * FROM cyber_changes WHERE id=?", (pid,)).fetchone(), True


def card_of(name: str, c: dict) -> dict:
    """「改好以后 · 等你点头」那张卡（旧 → 新）。"""
    if c["field"] == "note":
        return {"title": L(f"{name} 想记下一条", f"{name} wants to remember this"),
                "why": c["why"] or L("只记和你聊过的；点了才记下，记下以后能看、能删。", "It only keeps what you talked about; it's kept once you approve, and you can see or delete it later."),
                "changes": [L(f"记下：{c['new']}", f"Keep: {c['new']}")],
                "detail": L("记下的在资料页「记忆」里，能删、能清空。", "Notes are under Memory on the profile page, where you can delete them or clear them all."),
                "approveLabel": L("记下", "Keep")}
    return {"title": L(f"{name} · {c['label']}", f"{name} · {c['label']}"),
            "why": c["why"] or L("改好以后的样子在下面；点了才生效，改了能撤销。", "Here's how it would read; nothing changes until you approve, and it can be undone."),
            "changes": [L(f"旧：{c['old'] or '（空）'}", f"Before: {c['old'] or '(empty)'}"), L(f"新：{c['new'] or '（空）'}", f"After: {c['new'] or '(empty)'}")],
            "detail": L("也可以在资料页点那一行改；它自己想改时也会先这样问你。", "You can also change it from that row on the profile page; when it wants to change something itself, it asks like this first."),
            "approveLabel": L("改", "Change")}


async def propose(cid: str, field: str, value, *, via: str, why: str = "", ctx=None) -> dict:
    """提一个改法（见上）→ 改法的样子（多一个 card：交卡的结果，资料页那条路没有）。via chat / review / daily 出卡（ctx = 赛博回合
    工具的那一次调用；后台的回看、每天读没有，按这位赛博的对话交）。"""
    if via not in VIAS or via == "edit":
        raise HTTPException(400, L("via 不对", "bad via"))
    val = check_value(field, value)
    with _lock, cydb() as conn:
        r = get_row(conn, cid)
        if field != "note" and val == (r[field] or ""):
            raise HTTPException(409, L("和现在的一样，不用改", "That's what it already is"))
        c, fresh = new_change(conn, r, field, val, via=via, why=why)
        out = change_json(c)
    if via in CARD_VIAS and (fresh or not out["inbox"]):
        out["card"] = await submit(out, r["name"], ctx)
        if isinstance(out["card"], str):  # 卡没交成（拒过一模一样的、收件箱出错）：这个改法也不算数，免得一直挂着
            with _lock, cydb() as conn:
                conn.execute("UPDATE cyber_changes SET status='dropped', decided_at=? WHERE id=? AND status='pending'", (now_iso(), out["id"]))
            out["status"] = "dropped"
    return out


async def submit(c: dict, name: str, ctx=None) -> dict | str:
    """这个改法交一张收件箱卡（kind tool：engine/calls.py 记下「cyber:change」和它的 id；同意 = run_change 照它改一次），挂在这位赛博的对话上。
    → tools.submit_card 回的（approval-pending 和卡 id，或者一句没交成的原因）。"""
    from engine import calls
    from engine.loop import Control, ToolCtx
    ctx = ctx or ToolCtx(turn="", kind="cyber", agent=c["cyber"], conversation=c["cyber"], call_id=f"bg-{c['id']}", control=Control())
    res = await calls.ask("cyber", "change", {"change": c["id"]}, ctx, card_of(name, c))
    if isinstance(res, dict) and res.get("inbox"):
        with _lock, cydb() as conn:
            conn.execute("UPDATE cyber_changes SET inbox=? WHERE id=?", (res["inbox"], c["id"]))
        c["inbox"] = res["inbox"]
    return res


def apply_change(pid: str, *, by: str) -> dict:
    """照这个改法写进去（还在等的才写；同一项后来又改过就不写了，免得盖掉新的）→ {change, version}。"""
    stale = False
    with _lock, cydb() as conn:
        c = conn.execute("SELECT * FROM cyber_changes WHERE id=?", (pid,)).fetchone()
        if c is None:
            raise HTTPException(404, L("没有这个改法", "No such change"))
        if c["status"] != "pending":
            raise HTTPException(409, L(f"这个改法已经{'改了' if c['status'] == 'applied' else '不算数了'}", f"This change is already {c['status']}"))
        r = get_row(conn, c["cyber"])
        stale = c["field"] != "note" and (r[c["field"]] or "") != (c["old"] or "")
        if stale:  # 先记下不算数（出了 with 才抛：with 里抛会把这一笔回滚掉）
            conn.execute("UPDATE cyber_changes SET status='dropped', decided_at=? WHERE id=?", (now_iso(), pid))
    if stale:
        raise HTTPException(409, L("这一项在这之后又改过了，这个改法不再算数", "That item changed again since, so this change no longer applies"))
    with _lock, cydb() as conn:
        if conn.execute("UPDATE cyber_changes SET status='writing' WHERE id=? AND status='pending'", (pid,)).rowcount != 1:  # 点两下只写一次
            raise HTTPException(409, L("这个改法已经处理过了", "This change was already handled"))
        vid = write_field(conn, r, c["field"], c["new"], via=c["via"], change=pid)
        conn.execute("UPDATE cyber_changes SET status='applied', decided_at=?, version=? WHERE id=?", (now_iso(), vid, pid))
        out = change_json(conn.execute("SELECT * FROM cyber_changes WHERE id=?", (pid,)).fetchone())
        vrow = conn.execute("SELECT * FROM cyber_versions WHERE id=?", (vid,)).fetchone()
        v = version_json(vrow)
    if c["field"] == "note":
        noted(r["id"], (loads(vrow["new"], {}) or {}).get("id") or "")
    log_activity(L(f"赛博「{r['name']}」改了{out['label']}" if c["field"] != "note" else f"赛博「{r['name']}」记下了一条",
                   f'The Cyber "{r["name"]}" changed its {out["label"].lower()}' if c["field"] != "note" else f'The Cyber "{r["name"]}" kept a note'),
                 "edit", actor=L("你", "You") if by == "profile" else None)
    return {"change": out, "version": v}


async def close_card(iid: str | None) -> None:
    """这张改法卡用不着了（在资料页上定了、赛博删了）：还在等的就撤回（收件箱的 withdraw，engine_calls 跟着撤）。"""
    if not iid:
        return
    import inbox
    try:
        await inbox.withdraw(iid)
    except HTTPException:  # 已经定了
        pass


async def run_change(name: str, args: dict, row) -> str:
    """收件箱里点了同意（engine/tools.py 的 on_decided → engine/calls.run）：照卡上的那个改法改一次 → 一句结果（发回对话的「已同意…」里带上）。"""
    from engine.loop import ToolFailure
    if name != "change":
        raise ToolFailure("unknown call")
    try:
        out = apply_change(str(args.get("change") or ""), by="card")
    except HTTPException as e:
        raise ToolFailure(str(e.detail)) from e
    c = out["change"]
    if c["field"] == "note":
        return L(f"已记下：{c['new']}", f"Kept: {c['new']}")
    return L(f"已改：{c['label']} → {c['new']}（资料页能撤销）", f"Changed: {c['label']} → {c['new']} (can be undone on the profile page)")


def undo(cid: str, vid: int | None = None) -> dict:
    """撤销一个版本（不给 = 最近一个没撤过的）：回到上一版。只能撤这一项最近的那一次（后来又改过的先撤后来的）；记下的那一条 = 删掉它。"""
    with _lock, cydb() as conn:
        r = get_row(conn, cid)
        q = "SELECT * FROM cyber_versions WHERE cyber=? AND undone_at IS NULL" + (" AND id=?" if vid is not None else "") + " ORDER BY id DESC LIMIT 1"
        v = conn.execute(q, (cid, vid) if vid is not None else (cid,)).fetchone()
        if v is None:
            raise HTTPException(404, L("没有可以撤销的改动", "Nothing to undo"))
        later = conn.execute("SELECT 1 FROM cyber_versions WHERE cyber=? AND field=? AND id>? AND undone_at IS NULL", (cid, v["field"], v["id"])).fetchone()
        if later is not None and v["field"] != "note":
            raise HTTPException(409, L("这一项后来又改过：先撤销后来的那一次", "That item changed again later: undo the later change first"))
        ts = now_iso()
        if v["field"] == "note":
            conn.execute("DELETE FROM cyber_notes WHERE id=? AND cyber=?", ((loads(v["new"], {}) or {}).get("id"), cid))
        else:
            conn.execute(f"UPDATE cybers SET {v['field']}=?, updated_at=? WHERE id=?", (v["old"], ts, cid))  # noqa: S608 — field 是 FIELDS 里的
        conn.execute("UPDATE cyber_versions SET undone_at=? WHERE id=?", (ts, v["id"]))
        out = version_json(conn.execute("SELECT * FROM cyber_versions WHERE id=?", (v["id"],)).fetchone())
        cy = cyber_json(get_row(conn, cid), conn)
    log_activity(L(f"撤销了赛博「{r['name']}」的一次改动（{out['label']}）", f'Undid a change to the Cyber "{r["name"]}" ({out["label"]})'), "edit")
    return {"version": out, "cyber": cy}


class ChangeAct(BaseModel):
    action: str  # apply 改 / drop 不改


class UndoIn(BaseModel):
    version: int | None = None


@router.get("/api/cybers/{cid}/changes")
def list_changes(cid: str):
    """还在等你点头的改法（卡在收件箱里拒了、撤回了的已经不算）和最近的版本（资料页的「刚改 · 撤销」）。"""
    with _lock, cydb() as conn:
        get_row(conn, cid)
        settle_cards(conn, cid)
        pend = conn.execute("SELECT * FROM cyber_changes WHERE cyber=? AND status='pending' ORDER BY created_at DESC", (cid,)).fetchall()
        vers = conn.execute(VERSIONS + " ORDER BY v.id DESC LIMIT 20", (cid,)).fetchall()
    return {"ok": True, "pending": [change_json(c) for c in pend], "versions": [version_json(v) for v in vers]}


@router.post("/api/cybers/{cid}/changes/{pid}")
async def act_change(cid: str, pid: str, body: ChangeAct):
    """资料页上「改 / 不改」（也给卡上来的用：这边定了，收件箱那张撤回）。"""
    with _lock, cydb() as conn:
        c = conn.execute("SELECT * FROM cyber_changes WHERE id=? AND cyber=?", (pid, cid)).fetchone()
    if c is None:
        raise HTTPException(404, L("没有这个改法", "No such change"))
    if body.action == "apply":
        out = apply_change(pid, by="profile")
        await close_card(c["inbox"])
        return {"ok": True, **out}
    if body.action == "drop":
        with _lock, cydb() as conn:
            conn.execute("UPDATE cyber_changes SET status='dropped', decided_at=? WHERE id=? AND status='pending'", (now_iso(), pid))
        await close_card(c["inbox"])
        return {"ok": True}
    raise HTTPException(400, L("action 只能是 apply / drop", "action must be apply or drop"))


@router.post("/api/cybers/{cid}/undo")
def undo_endpoint(cid: str, body: UndoIn):
    return {"ok": True, **undo(cid, body.version)}


@router.get("/api/cybers/{cid}/versions")
def list_versions(cid: str):
    with _lock, cydb() as conn:
        get_row(conn, cid)
        vers = conn.execute(VERSIONS + " ORDER BY v.id DESC LIMIT 100", (cid,)).fetchall()
    return {"ok": True, "versions": [version_json(v) for v in vers]}


# —— 资料页的改法（第 6 件；三条路的另外两条：对话里说 = propose_change 工具，它自己提 = 回看、每天读）————————————————————
# 点一行（身份、语气、规矩、标签）：自己写好新的一行（value），或者点一个改法（tweak：同试聊的五个），或者一句话说怎么改（ask）；
# 资料页底下「跟 X 说要怎么改」：只有一句话（ask，不给 field），由模型挑改哪几项（最多两项，也可以是记下一条）。tweak、ask 先零工具地
# 改写一次（llmjson.ask：你自己引出来的，kind owner），都落到 propose(via profile)：回改好的样子，app 上「改 / 不改」调
# POST /changes/{id}（不出卡），改完「刚改 · 撤销」= POST /undo。

REWRITE_RULES = """You rewrite one of a Cyber's settings. A Cyber is a character the user talks with; it only talks and never acts.
INPUT_JSON.cyber has its current settings; INPUT_JSON.field is the one to rewrite (identity: who it is; tone: how it talks; rules: what it always
or never does; tag: a short label shown after its name) and INPUT_JSON.change says what to change. Write the whole new value for that field:
keep what wasn't asked to change, one or two sentences (a tag is at most 16 characters), in the same language as the current value unless asked
otherwise. Never add anything about health, diet or sleep.
Reply with {"value"}."""
FREE_RULES = """The user said how they want one of their Cybers changed (INPUT_JSON.ask). A Cyber is a character the user talks with; it only
talks and never acts. INPUT_JSON.cyber has its current settings. Decide which settings that touches: identity (who it is), tone (how it talks),
rules (what it always or never does), tag (a short label shown after its name), or note (a fact about the user to remember, if they told one).
Reply with {"changes": [{"field", "value"}]}: at most 2, each with the whole new value (keep what wasn't asked to change; same language as the
current settings unless asked otherwise), or [] if nothing fits. Never anything about health, diet or sleep."""
REWRITABLE = ("identity", "tone", "rules", "tag")
FREE_FIELDS = ("identity", "tone", "rules", "tag", "note")


class ChangeIn(BaseModel):
    field: str | None = None
    value: str | None = None
    tweak: str | None = None  # stricter / gentler / shorter / english / examples
    ask: str | None = None    # 一句话说怎么改
    why: str = ""


def settings_of(r: sqlite3.Row) -> dict:
    return {"name": r["name"], "tag": r["tag"] or "", "identity": r["identity"] or "", "tone": r["tone"] or "", "rules": r["rules"] or ""}


async def ask_json_owner(prompt: str, input_: dict, required: str):
    """资料页上的改写：你自己引出来的零工具的一问（llmjson.ask，kind owner）。没成 → 502。"""
    import llmjson
    try:
        out, _via = await llmjson.ask(prompt, input_, {"type": "object", "required": [required]}, timeout=60, kind="owner", origin="user")
    except Exception as e:  # noqa: BLE001 — 模型没回、回的不是 JSON
        raise HTTPException(502, L(f"没改写成：{str(e)[:160]}", f"Couldn't rewrite it: {str(e)[:160]}")) from None
    if not isinstance(out, dict):
        raise HTTPException(502, L("没改写成：回的不是一个对象", "Couldn't rewrite it: the reply wasn't an object"))
    return out


@router.post("/api/cybers/{cid}/changes")
async def profile_change(cid: str, body: ChangeIn):
    """资料页的改法（见上）→ {changes: [改法的样子（旧 → 新），还没写进去]}。"""
    with _lock, cydb() as conn:
        r = get_row(conn, cid)
    field = (body.field or "").strip() or None
    tweak = (body.tweak or "").strip() or None
    ask = clean_line(body.ask, 300) or None
    if field is not None and field not in PROPOSABLE:
        raise HTTPException(400, L(f"field 只能是 {' / '.join(PROPOSABLE)}", f"field must be one of {', '.join(PROPOSABLE)}"))
    if tweak is not None and tweak not in TWEAKS:
        raise HTTPException(400, L(f"tweak 只能是 {' / '.join(TWEAKS)}", f"tweak must be one of {', '.join(TWEAKS)}"))
    why = clean_line(body.why, 300) or (L(*TWEAK_LABELS[tweak]) if tweak else ask or "")
    if field and body.value is not None:
        pairs = [(field, body.value)]
    elif field and (tweak or ask):
        if field not in REWRITABLE:
            raise HTTPException(400, L("记下的一条、声音直接写新的值（value）", "For a note or a voice, write the new value itself"))
        out = await ask_json_owner(REWRITE_RULES, {"cyber": settings_of(r), "field": field, "change": TWEAKS[tweak] if tweak else f"The user asked: {ask}"},
                                   "value")
        pairs = [(field, out.get("value"))]
    elif ask:
        out = await ask_json_owner(FREE_RULES, {"cyber": settings_of(r), "ask": ask}, "changes")
        items = out.get("changes") if isinstance(out.get("changes"), list) else []
        pairs = [(str(x.get("field")), x.get("value")) for x in items if isinstance(x, dict) and x.get("field") in FREE_FIELDS][:2]
        if not pairs:
            raise HTTPException(409, L("没看出要改哪一项：点那一行改", "Couldn't tell what to change: tap the line to change it"))
    else:
        raise HTTPException(400, L("给新的值（value），或者一个改法（tweak）、一句话（ask）", "Give a new value, a tweak or a sentence (ask)"))
    changes, last = [], None
    for f, v in pairs:
        try:
            changes.append(await propose(cid, f, v, via="profile", why=why))
        except HTTPException as e:  # 和现在一样、写错了：只有这一项时照实报，好几项时跳过这一项
            last = e
    if not changes:
        raise last or HTTPException(409, L("没有要改的", "Nothing to change"))
    return {"ok": True, "changes": changes}


# —— 怎么长大：回看（默认关）————————————————————————————————————————————————————————————————————
# 每隔几轮回头看一次最近的对话（server.json 的 cybers.review：true = 每 6 轮；{"every": n}）：哪句该记、说话方式要不要调，
# 用这位赛博自己的模型做一次零工具的 JSON 调用（engine/oneshot.ask_json，来路 auto：你自己的后台回看，登录着的套餐能花；
# ChatGPT 这类后台要同意过），想记的、想改的都先提给你
# （propose via review，卡挂在它的对话上），点了才生效，能撤回。和内核「回合后复盘只提议」是同一套。新的后台花费：默认关；急停时不跑。

REVIEW_EVERY, REVIEW_TALK = 6, 30
_bg: set = set()


def review_every() -> int | None:
    v = (cfg() or {}).get("review")
    if v is True:
        return REVIEW_EVERY
    if isinstance(v, dict) and v.get("enabled", True) is not False:
        n = v.get("every")
        return max(2, min(50, n)) if isinstance(n, int) and not isinstance(n, bool) else REVIEW_EVERY
    return None


def spawn(coro):
    import asyncio
    t = asyncio.create_task(coro)
    _bg.add(t)
    t.add_done_callback(_bg.discard)
    return t


def after_turn(cid: str, status: str) -> None:
    """一轮说完（respond 调）：数一下；回看开着、攒够了 → 后台回看一次（先记下「看到第几轮了」，同时来的不重复看）。
    回看关着时也跟着记「看到这一轮了」：开的那一刻从零数起，不会一开就把关着时攒的轮数算上、马上回看（还和下一轮抢着说）。"""
    if status != "done":
        return
    every = review_every()
    with _lock, cydb() as conn:
        conn.execute("INSERT INTO cyber_state(cyber, turns) VALUES(?, 1) ON CONFLICT(cyber) DO UPDATE SET turns=turns+1", (cid,))
        if every is None:
            conn.execute("UPDATE cyber_state SET reviewed=turns WHERE cyber=?", (cid,))
            return
        st = conn.execute("SELECT turns, reviewed FROM cyber_state WHERE cyber=?", (cid,)).fetchone()
        due = st["turns"] - st["reviewed"] >= every
        if due:
            conn.execute("UPDATE cyber_state SET reviewed=turns, reviewed_at=? WHERE cyber=?", (now_iso(), cid))
    if due:
        spawn(review(cid))


REVIEW_RULES = (
    "你是赛博「{name}」，在回头看你和{user}最近的对话（INPUT_JSON 的 talk，who = user 是{user}，cyber 是你）。看两件事：\n"
    "1. 有没有值得记下的：{user}说的关于自己的事、偏好、在做的事、你们定下的做法。只记对话里真说过的，一句一条，最多 2 条；"
    "notes 里已经记过的别重复；健康、饮食、睡眠、住址、钱、别人的私事一律不记。\n"
    "2. 说话方式要不要调：{user}提过「以后……」「别……」这类要求，或者明显不满意你的说法，就提一处改动：tone（语气）或 rules（规矩），"
    "写改好以后的整句（不超过 120 字，在现在的基础上改）；没有就是 null。\n"
    "每一条带一句 why（为什么，引{user}的原话更好）。没有要记的、要改的就都空着。\n"
    "只回一个 JSON：{{\"notes\": [{{\"text\": \"…\", \"why\": \"…\"}}], \"change\": {{\"field\": \"tone\", \"new\": \"…\", \"why\": \"…\"}} 或 null}}",
    "You are the Cyber \"{name}\", looking back over your recent talk with {user} (INPUT_JSON's talk; who = user is {user}, cyber is you). "
    "Check two things:\n"
    "1. Anything worth keeping: what {user} said about themselves, their preferences, what they're working on, how you agreed to do "
    "things. Only what was actually said, one sentence each, at most 2; nothing already in notes; never health, diet, sleep, addresses, "
    "money or other people's private matters.\n"
    "2. Whether your way of talking should change: if {user} asked for something like \"from now on…\" or \"don't…\", or was clearly "
    "unhappy with how you said things, propose one change to tone or rules, written as the whole new line (at most 120 characters, "
    "starting from the current one); otherwise null.\n"
    "Give each a short why (quoting {user} is best). If there's nothing to keep or change, leave them empty.\n"
    "Reply with ONE JSON value: {{\"notes\": [{{\"text\": \"…\", \"why\": \"…\"}}], \"change\": {{\"field\": \"tone\", \"new\": \"…\", \"why\": \"…\"}} or null}}")


async def review(cid: str) -> list[dict]:
    """回看一次（见上）→ 这次提的改法。出错只记一行日志。"""
    from config import settings, user_word
    from engine import cyber as engine_cyber
    from engine import estop, oneshot
    from engine.errors import EngineError
    from i18n import LS
    if estop.note("cyber review"):
        return []
    with _lock, cydb() as conn:
        r = row_of(conn, cid)
        if r is None:
            return []
        rows = conn.execute(f"SELECT role, text FROM messages WHERE thread=? AND role IN ('user','{ASSISTANT_ROLE}') AND status='ok' ORDER BY id DESC LIMIT ?",
                            (cid, REVIEW_TALK)).fetchall()
        notes = [n["text"] for n in note_rows(conn, cid)[:40]]
    try:  # 来路 auto：回看是用户自己的后台。ChatGPT 这类没同意过后台的，这里就换成按量的，不让这一问直接失败
        model = engine_cyber.model_of({"id": r["id"], "model": r["model"]}, "auto")
    except EngineError:
        model = None
    if not model or not rows:
        return []
    talk = [{"who": "user" if x["role"] == "user" else "cyber", "text": x["text"][:1500]} for x in reversed(rows)]
    inp = {"cyber": {"name": r["name"], "identity": r["identity"], "tone": r["tone"], "rules": r["rules"]}, "notes": notes, "talk": talk}
    try:
        out, _ = await oneshot.ask_json(LS(*REVIEW_RULES).format(name=r["name"], user=user_word().strip() or settings.user_name or "the user"),
                                        inp, model=model, purpose="cyber", required=("notes",), timeout=90, conversation=f"cyber-review:{cid}",
                                        origin="auto")
    except Exception as e:  # noqa: BLE001 — 回看没成：下一回再看
        print(f"[cybers] {cid} 回看没成：{type(e).__name__}: {str(e)[:160]}")
        return []
    made: list[dict] = []
    for n in (out.get("notes") if isinstance(out, dict) and isinstance(out.get("notes"), list) else [])[:2]:
        text = clean_line((n or {}).get("text") if isinstance(n, dict) else n, NOTE_MAX)
        if not text or HEALTH.search(text) or text in notes:
            continue
        try:
            made.append(await propose(cid, "note", text, via="review", why=clean_line((n or {}).get("why") if isinstance(n, dict) else "", 300)))
        except HTTPException:
            continue
    ch = out.get("change") if isinstance(out, dict) else None
    if isinstance(ch, dict) and ch.get("field") in ("tone", "rules") and clean_line(ch.get("new"), LINE_MAX):
        try:
            made.append(await propose(cid, ch["field"], ch["new"], via="review", why=clean_line(ch.get("why"), 300)))
        except HTTPException:
            pass
    return made


# —— 装载：性格归赛博，本事归功能 Agent ——————————————————————————————————————————————————————————
# 装上就会、卸下就断开，数据一直留在功能 Agent 那里；一个功能 Agent 可以给几位赛博用。赛博在对话里经 ask_agent（engine/cyber_tools.py）
# 叫装载的功能 Agent 去查、去做：只读的结果回到对话，要改你的数据的由它出卡给你，点了才做。
# 健康类的功能 Agent 不能装载（资料页上写「健康数据不给赛博」）。认法（is_health，任何一条对上就算；宁可错拦）：
#   - 内建看板是 diet（饮食记录）、fitness（训练和身体：体重、恢复、睡眠来的恢复分）：这两块的数据就是身体和饮食
#   - 图标是 heart（健康）、pill（用药）、utensils（饮食）、dumbbell（训练）：新建时自己挑的图标说明了它管什么
#   - 名字或职责里有健康、饮食、睡眠的词（HEALTH，同记下的那一条）
#   - server.json 的 cybers.health_agents 里写了它（自己补；只能加不能减）
# 每天读（每位赛博一个开关，默认关：新的定时花费）：每天一次，读装载的功能 Agent 当天的产出（产出卡、定时任务发进它对话的回复），
# 存进知识库（来源写明「GitHub 雷达 · 10/20 日报」），再在赛博自己的对话里起一轮，和你聊读到的。

LOADOUT_MAX = 6
HEALTH_DASHBOARDS = ("diet", "fitness")
HEALTH_ICONS = ("heart", "pill", "utensils", "dumbbell")
DAILY_AT, DAILY_UNTIL, DAILY_EVERY = "09:00", "21:00", 15 * 60  # 几点以后读、过了几点今天就不读了、多久看一次
DAILY_CHARS = 12_000  # 一个 Agent 一天的产出最多存多少字


def group_rows(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    """功能 Agent（groups 表，按显示顺序）。"""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='groups'").fetchone():
        return {}
    return {g["id"]: g for g in conn.execute("SELECT id, name, icon, color, purpose, dashboard FROM groups ORDER BY position, created_at")}


def is_health(g) -> bool:
    """这个功能 Agent 是不是健康类（见上）：是就不能装载给赛博。"""
    extra = (cfg() or {}).get("health_agents")
    if isinstance(extra, list) and g["id"] in [str(x) for x in extra]:
        return True
    return (g["dashboard"] in HEALTH_DASHBOARDS or g["icon"] in HEALTH_ICONS
            or bool(HEALTH.search(f"{g['name']} {g['purpose'] or ''}")))


def agent_json(g, loaded: bool = False) -> dict:
    health = is_health(g)
    return {"id": g["id"], "name": g["name"], "icon": g["icon"], "color": g["color"], "purpose": (g["purpose"] or "").strip(),
            "loaded": loaded, "health": health, **({"why": L("健康数据不给赛博", "Health data isn't given to a Cyber")} if health else {})}


def loadout_of(conn: sqlite3.Connection, r: sqlite3.Row) -> list[dict]:
    """装着的（还在、不是健康类的；删了的 Agent、后来变成健康类的不算）：[{id, name, purpose}]，给 ask_agent。"""
    gs = group_rows(conn)
    return [{"id": a, "name": gs[a]["name"], "purpose": (gs[a]["purpose"] or "").strip()} for a in loads(r["loadout"], []) or []
            if a in gs and not is_health(gs[a])]


def check_loadout(conn: sqlite3.Connection, ids: list[str]) -> list[str]:
    """新建时一起装的（去重、照原来的先后）：没有这个 Agent 404、健康类的 400、多过 LOADOUT_MAX 409，和一个一个装一样。"""
    gs = group_rows(conn)
    out: list[str] = []
    for a in dict.fromkeys(str(x) for x in ids or []):
        g = gs.get(a)
        if g is None:
            raise HTTPException(404, L("没有这个功能 Agent", "No such Agent"))
        if is_health(g):
            raise HTTPException(400, L(f"「{g['name']}」是健康类的功能 Agent：健康数据不给赛博", f'"{g["name"]}" looks after health: health data isn\'t given to a Cyber'))
        out.append(a)
    if len(out) > LOADOUT_MAX:
        raise HTTPException(409, L(f"最多装 {LOADOUT_MAX} 个功能 Agent", f"At most {LOADOUT_MAX} Agents can be loaded"))
    return out


@router.get("/api/cybers/{cid}/loadout")
def get_loadout(cid: str):
    """装着的（每个一句它能做什么 = 职责）和能装的（健康类的标「不给」）；每天读开没开。"""
    with _lock, cydb() as conn:
        r = get_row(conn, cid)
        gs = group_rows(conn)
        ids = [a for a in loads(r["loadout"], []) or [] if a in gs]
    return {"ok": True, "loaded": [agent_json(gs[a], True) for a in ids], "available": [agent_json(g) for a, g in gs.items() if a not in ids],
            "daily": bool(r["daily"]), "max": LOADOUT_MAX}


class LoadIn(BaseModel):
    agent: str


@router.post("/api/cybers/{cid}/loadout")
def load(cid: str, body: LoadIn):
    """装上一个功能 Agent。健康类的 → 400（健康数据不给赛博）；最多 LOADOUT_MAX 个。"""
    with _lock, cydb() as conn:
        r = get_row(conn, cid)
        gs = group_rows(conn)
        g = gs.get(body.agent)
        if g is None:
            raise HTTPException(404, L("没有这个功能 Agent", "No such Agent"))
        if is_health(g):
            raise HTTPException(400, L(f"「{g['name']}」是健康类的功能 Agent：健康数据不给赛博", f'"{g["name"]}" looks after health: health data isn\'t given to a Cyber'))
        ids = [a for a in loads(r["loadout"], []) or [] if a in gs]
        if body.agent not in ids:
            if len(ids) >= LOADOUT_MAX:
                raise HTTPException(409, L(f"最多装 {LOADOUT_MAX} 个功能 Agent", f"At most {LOADOUT_MAX} Agents can be loaded"))
            ids.append(body.agent)
            conn.execute("UPDATE cybers SET loadout=?, updated_at=? WHERE id=?", (dumps(ids), now_iso(), cid))
    log_activity(L(f"给赛博「{r['name']}」装上了「{g['name']}」", f'Loaded "{g["name"]}" on the Cyber "{r["name"]}"'), "edit")
    return get_loadout(cid)


@router.delete("/api/cybers/{cid}/loadout/{agent}")
def unload(cid: str, agent: str):
    """卸下：断开（数据一直在功能 Agent 那里，什么都不删）。"""
    with _lock, cydb() as conn:
        r = get_row(conn, cid)
        ids = [a for a in loads(r["loadout"], []) or [] if a != agent]
        conn.execute("UPDATE cybers SET loadout=?, updated_at=? WHERE id=?", (dumps(ids), now_iso(), cid))
    return get_loadout(cid)


class DailyIn(BaseModel):
    on: bool


@router.post("/api/cybers/{cid}/daily")
def set_daily(cid: str, body: DailyIn):
    """每天读的开关（新的定时花费，默认关）。"""
    with _lock, cydb() as conn:
        get_row(conn, cid)
        conn.execute("UPDATE cybers SET daily=?, updated_at=? WHERE id=?", (int(body.on), now_iso(), cid))
    return get_loadout(cid)


def proposer(cid: str):
    """给 propose_change 的那一步（对话里说的那条路）：照改法的规矩提（via chat，交卡挂在这位赛博的对话上）→ 一句给模型的结果。"""
    async def go(field: str, value: str, why: str, ctx) -> str:
        from engine.loop import ToolFailure
        try:
            c = await propose(cid, field, value, via="chat", why=why, ctx=ctx)
        except HTTPException as e:
            raise ToolFailure(f"Not proposed: {e.detail}") from None
        owner = owner_name()
        if isinstance(c.get("card"), str):  # 卡没交成：原话给它
            raise ToolFailure(c["card"])
        if "card" not in c:
            return f"This exact change is already waiting for {owner}'s OK on a card; don't propose it again."
        what = f"keeping this note: {c['new']}" if field == "note" else f"{field}: {c['old'] or '(empty)'} -> {c['new']}"
        return (f"Proposed {what}. It's on a card under your reply, waiting for {owner}'s OK; nothing changes until they approve, and they "
                "can undo it. Tell them in a few words; don't say it's done.")
    return go


def asker(cid: str, sink: list | None = None):
    """给 ask_agent 的那一步：真去问之前再查一遍（还装着、不是健康类）。sink：交了的卡（{id, title}）也记一份到这里（群里那一轮用：
    卡照旧挂在它一对一的对话上，群里也列出来，groupchat.py）。"""
    async def ask(agent: str, question: str, ctx) -> str:
        return await ask_loaded(cid, agent, question, sink=sink, origin=getattr(ctx, "origin", None) or "user")
    return ask


async def ask_loaded(cid: str, agent: str, question: str, *, sink: list | None = None, origin: str = "user") -> str:
    """赛博问它装载的功能 Agent（engine/cyber_tools.py 的 ask_round）：两边留记录，回给赛博的是包成资料的回答和交了的卡。
    origin：这一轮替谁干活（赛博回合的来路）。用户自己的能花套餐；管家请教（butler）不能。"""
    from config import settings
    from engine import cyber_tools
    from engine.loop import ToolFailure
    with _lock, cydb() as conn:
        r = row_of(conn, cid)
        loaded = {a["id"]: a for a in loadout_of(conn, r)} if r is not None else {}
    if r is None or agent not in loaded:
        raise ToolFailure(f"Not asked: {agent} isn't loaded on you any more.")
    cards: list[dict] = []
    out = await cyber_tools.ask_round(agent, question, cyber={**engine_dict(r), "model": model_in_use(r)}, thread=cid, cards=cards,
                                     origin=origin)
    if sink is not None:
        sink.extend(cards)
    name = loaded[agent]["name"]
    record_ask(agent, r["name"], question, out)
    return cyber_tools.result_text(name, agent, out, cards, settings.user_name or "the user")


def record_ask(agent: str, cyber_name: str, question: str, out: dict) -> None:
    """功能 Agent 那边也留一笔（照 handoff 两边都有记录）：一行灰字（谁来问的、问了什么）+ 它的回答；origin relay，不算未读、不改排序。"""
    from engine.unattended import AUTO_TRIGGER
    from i18n import LS
    ts = now_iso()
    line = AUTO_TRIGGER + LS(f"赛博「{cyber_name}」来问（只读）：{question}", f'The Cyber "{cyber_name}" asked (read-only): {question}')
    try:
        with _lock, chat.db() as conn:
            conn.execute("INSERT INTO messages(thread, role, text, model, ts, status, origin) VALUES(?,?,?,?,?,?,?)",
                         (agent, "auto", line[:1000], None, ts, "ok", "relay"))
            if out.get("answer"):
                conn.execute("INSERT INTO messages(thread, role, text, model, ts, status, origin) VALUES(?,?,?,?,?,?,?)",
                             (agent, ASSISTANT_ROLE, out["answer"], out.get("model"), now_iso(), "ok", "relay"))
    except sqlite3.Error as e:
        print(f"[cybers] 没记进 {agent} 的对话：{e}")


# —— 每天读 ——————————————————————————————————————————————————————————————————————————————

def hm(v: str) -> tuple[int, int]:
    try:
        h, m = (int(x) for x in str(v).split(":", 1))
        return max(0, min(23, h)), max(0, min(59, m))
    except ValueError:
        return 9, 0


def reports_of(conn: sqlite3.Connection, agent: str, day: str) -> list[dict]:
    """这个功能 Agent 那一天（逻辑日）的产出：产出卡（feed_items）、定时任务发进它对话的回复（role ASSISTANT_ROLE、origin auto）。"""
    lo, hi = chat.day_bounds(day)
    out: list[dict] = []
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='feed_items'").fetchone():
        for f in conn.execute("SELECT title, body, created_at FROM feed_items WHERE group_id=? AND dismissed=0 AND julianday(created_at)>=julianday(?) "
                              "AND julianday(created_at)<julianday(?) ORDER BY created_at", (agent, lo, hi)):
            out.append({"at": f["created_at"], "text": f"{f['title']}\n{f['body'] or ''}".strip()})
    for m in conn.execute(f"SELECT text, ts FROM messages WHERE thread=? AND role='{ASSISTANT_ROLE}' AND origin='auto' AND status='ok' "
                          "AND julianday(ts)>=julianday(?) AND julianday(ts)<julianday(?) ORDER BY id", (agent, lo, hi)):
        out.append({"at": m["ts"], "text": m["text"]})
    return out


async def daily_check(now: datetime | None = None) -> list[str]:
    """每天读的那一次（循环每隔 DAILY_EVERY 调；测试直接调）：开着每天读、今天还没读、到了钟点的赛博，读装载的 Agent 今天的产出。
    → 这次读了的赛博。急停时不读。"""
    from engine import estop
    if not on() or estop.note("cyber daily read"):
        return []
    now = (now or datetime.now(TZ)).astimezone(TZ)
    day = chat.day_of(now.isoformat())
    start, until = hm((cfg() or {}).get("daily_at") or DAILY_AT), hm(DAILY_UNTIL)
    if (now.hour, now.minute) < start:
        return []
    done: list[str] = []
    with _lock, cydb() as conn:
        due = [r for r in active_rows(conn) if r["daily"]]
        states = {s["cyber"]: s for s in conn.execute("SELECT * FROM cyber_state")}
    for r in due:
        if (states.get(r["id"]) or {"daily_day": None})["daily_day"] == day:
            continue
        if await read_today(r, day, now, late=(now.hour, now.minute) >= until):
            done.append(r["id"])
    return done


async def read_today(r: sqlite3.Row, day: str, now: datetime, *, late: bool) -> bool:
    """读一位赛博装载的 Agent 今天的产出：有 → 存进知识库、记下今天读过、在它的对话里起一轮聊读到的；没有 → 太晚了就记今天不读了。"""
    from engine.unattended import AUTO_TRIGGER
    from i18n import LS
    with _lock, cydb() as conn:
        loaded = loadout_of(conn, r)
        found = [(a, reports_of(conn, a["id"], day)) for a in loaded]
        found = [(a, reps) for a, reps in found if reps]
        if not found and not late:
            return False
        conn.execute("INSERT INTO cyber_state(cyber, daily_day) VALUES(?,?) ON CONFLICT(cyber) DO UPDATE SET daily_day=excluded.daily_day",
                     (r["id"], day))
        for a, reps in found:
            text = "\n\n".join(x["text"] for x in reps)[:DAILY_CHARS]
            label = day_label(now.isoformat())
            add_knowledge(conn, r["id"], LS(f"{a['name']} · {label} 日报", f"{a['name']} · {label} report"), text, origin="daily",
                          source=LS(f"{a['name']} · {label}", f"{a['name']} · {label}"))
    if not found:
        return False
    names_ = LS("、", ", ").join(a["name"] for a, _ in found)
    n = sum(len(reps) for _, reps in found)
    say = LS(f"（每天读）你刚读了装载的功能 Agent 今天的产出：{names_}，共 {n} 份，已经在你的知识库里。挑对{owner_name()}有用的几点简短说说，"
             "说你自己的看法；想记下的用 propose_change 提给他（他点了头才记）。",
             f"(Daily read) You just read today's output from your loaded Agents: {names_}, {n} item(s), now in your knowledge base. Tell "
             f"{owner_name()} briefly the few points that matter to them, with your own view; propose anything worth keeping with "
             "propose_change (it's kept only once they approve).")
    try:
        chat.start_run(r["id"], AUTO_TRIGGER + LS(f"读了{names_}今天的产出 · {n} 份", f"Read today's output from {names_} · {n}"), None,
                       origin="auto", say=say)
    except HTTPException as e:  # 正在聊：这一轮不起了，资料已经在知识库里
        print(f"[cybers] {r['id']} 每天读完没能起一轮：{e.detail}")
    return True


def owner_name() -> str:
    """给模型的话里怎么称呼你（server.json 的 user_name；没写按服务器的语言写「用户」）。"""
    from config import settings
    from i18n import LS
    return settings.user_name or LS("用户", "the user")


async def daily_loop() -> None:
    import asyncio
    while True:
        try:
            await daily_check()
        except Exception as e:  # noqa: BLE001 — 这一次没读成，下一回再来
            print(f"[cybers] 每天读出错：{type(e).__name__}: {str(e)[:160]}")
        await asyncio.sleep(DAILY_EVERY)


def start() -> None:
    """服务启动时（main.py 的 lifespan，claw 是 builtin）：开着赛博就建好默认的两位（只建一次）；每天读的循环（开着每天读的赛博才真去读）。"""
    if claw.is_builtin():
        try:
            seed_defaults()
        except sqlite3.Error as e:  # 建不成不耽误启动：app 第一次来列赛博时再建
            print(f"[cybers] 没建成默认的赛博：{e}")
        spawn(daily_loop())


def install() -> None:
    """服务启动时（导入时）挂上：收件箱认得赛博的对话（卡挂在那里、写它的名字），改法卡点了同意照做（engine/calls.py 的 cyber 前缀）。"""
    import inbox
    from engine import calls
    calls.register("cyber", run_change)
    if names not in inbox.THREAD_NAMES:
        inbox.THREAD_NAMES.append(names)


install()
