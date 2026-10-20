"""迁移（mousse migrate）共用的：计划和报告的样子、只读地读源目录、合进 server.json 的规矩、模型引用怎么换。说明在 migrate/__init__.py 开头。"""
from __future__ import annotations

import contextlib
import copy
import json
import re
import shutil
import sqlite3
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from i18n import L

ENV_REF = re.compile(r"^\$\{(?:env:)?([A-Za-z_][A-Za-z0-9_]*)(?::-[^}]*)?\}$")   # ${VAR}、${env:VAR}（Hermes）、${VAR:-默认}（OpenClaw）
ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]{0,63}$")
SENSITIVE = re.compile(r"KEY|TOKEN|SECRET|PASSW|AUTH|CREDENTIAL|COOKIE|SESSION", re.I)
OWN_WORKSPACE = Path("~/.openmousse/workspace").expanduser()   # 安装器给自带内核建的工作区（packs/core/setup.py）


@dataclass
class Line:
    """报告里的一行：what = 源里的哪一项（键、文件），text = 转成了什么 / 要做什么 / 为什么转不了。"""
    what: str
    text: str


@dataclass
class Plan:
    """一次迁移要做的事。只看（不加 --apply）和真写用的是同一份：先算好，--apply 再照它写。值（密钥）只在 held 里，不进报告、不写盘。"""
    source: str                                   # openclaw | hermes
    home: Path
    auto: list[Line] = field(default_factory=list)    # 自动转的
    todo: list[Line] = field(default_factory=list)    # 要人做的
    cant: list[Line] = field(default_factory=list)    # 转不了的
    patch: dict = field(default_factory=dict)         # 合进 server.json 的（只加没有的，见 merge）
    replace: dict = field(default_factory=dict)       # 顶层的几个键照写、盖掉现在的（只有 workspace / profile：照 packs/core/setup.py 换工作区的规矩）
    drop: list[str] = field(default_factory=list)     # 顶层要去掉的键（profile 指着安装器的档案时：去掉，让它跟着新的工作区）
    jobs: list[dict] = field(default_factory=list)    # 定时任务：{"agent", "given"（jobs.clean 认的写法）, "from", "was"}，一律先停着
    agents: list[dict] = field(default_factory=list)  # 要建的 Agent：{"id", "name", "workspace", "from"}（OpenClaw：指到原工作区；Hermes：新建）
    files: list[dict] = field(default_factory=list)   # 要写的文件（Hermes 的 SOUL.md、记忆、技能），见 apply.files
    secrets: dict[str, Path] = field(default_factory=dict)   # 密钥的变量名 → 值在哪个 .env（只记名字）
    held: dict[str, str] = field(default_factory=dict)       # 直接写在配置里的密钥：变量名 → 值（只在 --copy-secrets 时写进 secrets.env）
    threads: dict | None = None                   # threads.model 怎么换：{"from": "anthropic/", "to": "claude-code/"}（只在 --apply）
    done: list[str] = field(default_factory=list)     # --apply 真做了的（备份在哪、建了什么）

    def ok(self, what: str, text: str) -> None:
        self.auto.append(Line(what, text))

    def ask(self, what: str, text: str) -> None:
        self.todo.append(Line(what, text))

    def no(self, what: str, text: str) -> None:
        self.cant.append(Line(what, text))


class SourceError(Exception):
    """源目录读不了（不在、不是这一家的家、配置坏了）：一句给人看的话。"""


# —— 只读地读 ——————————————————————————————————————————————————————————————————

def read_text(p: Path, limit: int = 2_000_000) -> str | None:
    """读一个文本文件（只读打开；不在、读不了、太大 → None）。符号链接照读，不跟着写任何东西。"""
    try:
        if not p.is_file() or p.stat().st_size > limit:
            return None
        return p.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return None


def _strip_json5(text: str) -> str:
    """JSON5 → JSON：去掉 // 和 /* */ 注释、对象和数组末尾多的逗号；单引号字符串换成双引号；没加引号的键加上引号。
    只认 OpenClaw 写配置会用到的那几样（它自己的 openclaw config set 写出来的是普通 JSON），认不出来的交给 json.loads 报错。"""
    out: list[str] = []
    last, at = "", -1                             # 上一个不是空白的输出字符和它在 out 里的位置（没加引号的键、末尾多的逗号）
    ident = re.compile(r"[A-Za-z_$][\w$]*")
    colon = re.compile(r"\s*:")
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in "\"'":
            q, j, buf = c, i + 1, []
            while j < n and text[j] != q:
                if text[j] == "\\" and j + 1 < n:
                    buf.append("'" if q == "'" and text[j + 1] == "'" else text[j:j + 2])
                    j += 2
                    continue
                buf.append('\\"' if text[j] == '"' else text[j])
                j += 1
            out.append('"' + "".join(buf) + '"')
            last, at, i = '"', len(out) - 1, j + 1
            continue
        if text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
            continue
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
            continue
        if last in ("{", ",") and (m := ident.match(text, i)) and colon.match(text, m.end()):
            out.append(f'"{m.group(0)}"')
            last, at, i = '"', len(out) - 1, m.end()
            continue
        if c in "}]" and last == ",":
            out[at] = ""
        out.append(c)
        if not c.isspace():
            last, at = c, len(out) - 1
        i += 1
    return "".join(out)


def read_json5(p: Path) -> dict:
    """读 openclaw.json（JSON5）。读不了、不是一个对象 → SourceError。"""
    text = read_text(p)
    if text is None:
        raise SourceError(L(f"读不了 {p}", f"Can't read {p}"))
    try:
        data = json.loads(text)
    except ValueError:
        try:
            data = json.loads(_strip_json5(text))
        except ValueError as e:
            raise SourceError(L(f"{p} 不是能读的 JSON / JSON5：{e}", f"{p} isn't readable JSON / JSON5: {e}")) from None
    if not isinstance(data, dict):
        raise SourceError(L(f"{p} 不是一个 JSON 对象", f"{p} isn't a JSON object"))
    return data


def read_env(p: Path) -> dict[str, str]:
    """一个 .env 里的 名字 → 值（KEY=VALUE、export KEY=VALUE、引号去掉；照 secretstore 的读法）。值只留在内存里。"""
    out: dict[str, str] = {}
    for line in (read_text(p) or "").splitlines():
        k, sep, v = line.strip().removeprefix("export ").partition("=")
        k = k.strip()
        if sep and k and not k.startswith("#"):
            out[k] = v.strip().strip("'\"")
    return out


@contextlib.contextmanager
def sqlite_copy(p: Path) -> Iterator[sqlite3.Connection | None]:
    """只读地看一个 SQLite 库：库和它的 -wal、-shm 先拷到一个临时目录，再用只读连接打开拷贝（源目录里连 -shm 都不会多一个，
    正在跑的 OpenClaw 写到一半也不会被我们锁住）。没有这个库、打不开 → None。用完删掉拷贝。"""
    if not p.is_file():
        yield None
        return
    tmp = Path(tempfile.mkdtemp(prefix="mousse-migrate-"))
    conn = None
    try:
        for suffix in ("", "-wal", "-shm"):
            src = Path(f"{p}{suffix}")
            if src.is_file():
                shutil.copyfile(src, tmp / f"src.db{suffix}")
        try:
            conn = sqlite3.connect(f"file:{tmp / 'src.db'}?mode=ro", uri=True, timeout=5)
            conn.row_factory = sqlite3.Row
            conn.execute("SELECT 1 FROM sqlite_master LIMIT 1")
        except sqlite3.Error:
            if conn is not None:
                conn.close()
            conn = None
        yield conn
    finally:
        if conn is not None:
            conn.close()
        shutil.rmtree(tmp, ignore_errors=True)


def table(conn: sqlite3.Connection | None, name: str) -> bool:
    if conn is None:
        return False
    try:
        return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None
    except sqlite3.Error:
        return False


def loads(s, default=None):
    """JSON 字段：解不开就 default（OpenClaw 的 job_json、state_json）。"""
    try:
        v = json.loads(s) if isinstance(s, str) else s
    except ValueError:
        return default
    return v if v is not None else default


# —— 密钥：只记名字 ——————————————————————————————————————————————————————————————

def env_ref(v) -> str | None:
    """"${VAR}" 这种写法 → VAR；OpenClaw 的 SecretRef {"source": "env", "id": "VAR"} 也认。直接写的值、别的来源 → None。"""
    if isinstance(v, dict) and str(v.get("source") or "env") == "env" and isinstance(v.get("id"), str) and ENV_NAME.match(v["id"]):
        return v["id"]
    m = ENV_REF.match(str(v or "").strip()) if isinstance(v, str) else None
    return m.group(1) if m else None


def literal(v) -> str | None:
    """直接写在配置里的密钥（不是 ${VAR}、不是 SecretRef、不是空的）→ 值；否则 None。"""
    if not isinstance(v, str) or not v.strip() or env_ref(v) or v.strip().startswith("$"):
        return None
    return v.strip()


def env_name_for(pid: str) -> str:
    """自定义的一家存密钥用的变量名（同 engine/setup_api.save_provider）。"""
    return re.sub(r"[^A-Z0-9]", "_", pid.upper()) + "_API_KEY"


# —— 合进 server.json ————————————————————————————————————————————————————————————

ATOMIC = (("engine", "providers", "*"), ("engine", "heartbeat", "*"), ("engine", "sandbox", "agents", "*"), ("engine", "host_exec", "agents", "*"),
          ("engine", "web", "search"), ("engine", "recall", "embeddings"), ("mcp_servers", "*"), ("engine", "skills", "agents", "*"))
SHOW = (("engine", "model"), ("engine", "fallbacks"), ("engine", "models"), ("engine", "use", "*"), ("workspace",), ("agent_workspaces", "*"),
        ("env_file",), ("engine", "skills", "agents", "*"), ("engine", "chatgpt", "models"), ("profile",))


def _match(path: tuple[str, ...], pats) -> bool:
    return any(len(p) == len(path) and all(a in ("*", b) for a, b in zip(p, path, strict=True)) for p in pats)


def merge(dst: dict, patch: dict, path: tuple[str, ...] = ()) -> tuple[list[str], list[tuple[str, object, object]]]:
    """把 patch 合进 dst（原地改）：dst 没有的键加上；两边都是对象的往里合；已经有、又不一样的不动，记成冲突。
    ATOMIC 里那几层整块算一个（已经有一家 engine.providers.x、一个 MCP 服务、一个 Agent 的心跳：不往里加零碎的键，免得改掉它原来的意思）。
    → (加了的键, [(键, 现在的, 想写的)])。"""
    added: list[str] = []
    kept: list[tuple[str, object, object]] = []
    for k, v in patch.items():
        p = (*path, str(k))
        if k not in dst:
            dst[k] = copy.deepcopy(v)
            added.append(".".join(p))
        elif isinstance(dst[k], dict) and isinstance(v, dict) and not _match(p, ATOMIC):
            a, c = merge(dst[k], v, p)
            added += a
            kept += c
        elif dst[k] != v:
            kept.append((".".join(p), dst[k], v))
    return added, kept


def brief(path: str, v) -> str:
    """冲突里给人看的值：模型、路径这类照写（截短），别的（可能带着令牌的 MCP 服务、服务商）只说「已经有一份」。"""
    if not _match(tuple(path.split(".")), SHOW):
        return L("已经有一份", "one is already there")
    s = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
    return s if len(s) <= 80 else s[:77] + "…"


def put(d: dict, *path_and_value) -> None:
    """put(d, "engine", "model", "x")：一路建好对象再写。"""
    *path, last, value = path_and_value
    for k in path:
        nxt = d.get(k)
        if not isinstance(nxt, dict):
            nxt = d[k] = {}
        d = nxt
    d[last] = value


def get(d, *path, default=None):
    for k in path:
        if not isinstance(d, dict):
            return default
        d = d.get(k)
    return default if d is None else d


def same_dir(a: Path, b: Path) -> bool:
    try:
        return a.expanduser().resolve() == b.expanduser().resolve()
    except OSError:
        return str(a) == str(b)


def tilde(p: Path | str) -> str:
    """报告里的路径：家目录写成 ~。"""
    s, home = str(p), str(Path.home())
    return "~" + s[len(home):] if s == home or s.startswith(home + "/") else s


# —— 模型引用 ——————————————————————————————————————————————————————————————————————

def known_provider(pid: str, patch: dict) -> bool:
    """内核认得这一家吗：目录里的（云端、本机）、这次要写进 engine.providers 的、server.json 已经有的、用户自己的套餐。"""
    import config
    from engine import catalog, models
    if pid in catalog.CLOUD or pid in catalog.LOCAL or pid in models.SUBSCRIPTIONS:
        return True
    if pid in (get(patch, "engine", "providers") or {}):
        return True
    return pid in (get(config.raw(), "engine", "providers") or {})


def canonical(ref: str) -> str:
    """规范的写法（google/models/x → google/x，照 engine/models.canonical）。"""
    from engine import models
    return models.canonical(ref)
