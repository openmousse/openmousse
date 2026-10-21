"""server.json 的整份校验和 get / set（第 7 批，14.6，2026-10-19）。照 OpenClaw 的 strict validation（docs/gateway/configuration.md：
写之前整份校验，不合法不落盘）和 Hermes 的 config check（hermes_cli/config.py：原子写、读不出来就不写回）。只借做法。

  mousse config validate [--json] [--file 路径]     整份校验：有 ✗ 退出码 1，没有 0
  mousse config get <点路径>                        比如 engine.model、bind.port；像密钥的值一律打码，secrets.env 里的永远不给
  mousse config set <点路径> <JSON 值>              比如 engine.fallbacks '["moonshot/kimi-k3"]'、bind.port 8080；不是 JSON 的当字符串
  mousse config unset <点路径>                      去掉这一项（回到默认）
set / unset：先现读（读不了不写）→ 改一份拷贝 → 整份校验，有 ✗ 就不落盘 → 先备份（server.json.bak-<时间>，同 engine/setup_api.py）→
  原子写（0600 的临时文件再改名）。密钥不往 server.json 里写：路径最后一段像 token / key / secret / password 的、要写的值里（整个
  对象、列表也一样往里看）有像密钥的（headers 里的 Authorization、Cookie、*-Key 这些也算；写成 ${变量} 的不算）不给 set；auth 整段、
  auth.tokens、auth.pairing 底下的不给 set / unset。密钥用 setup_ctl.py add（进 secrets.env），接入令牌用 tokens.py。
get：像密钥的值一律打码（同上那几种），再过一遍 common.scrub（这台机器上真的令牌、密钥）。
模型引用（Ref）认 engine.aliases 里起过的别名（engine/models.py）；没起过的不带斜杠的名字算错（先起别名再用）。

表（KEYS，下面）：照 config.py 开头、engine/models.py 开头、各 engine 模块开头的说明写，先覆盖顶层键和 engine 段的主要键。
  不认识的键只警告（!），不算错：9 个会话正同时在加新键，新版本的配置拿给老版本也要能起。类型不对、取值不在范围里才算错（✗）。
  写成「开放」的段（open）只查认识的几项，别的不警告（各家服务商的脾气、各模块细的设置）。
别的地方（doctor 的 config.valid、setup_ctl.py 写完以后）都调 validate()。
"""
from __future__ import annotations

import json
import re
import sys
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from i18n import L

from . import common as C

REF = re.compile(r"^[a-z0-9][a-z0-9_.-]*/\S+$")
ENV = re.compile(r"^[A-Z_][A-Z0-9_]{0,63}$")
HHMM = re.compile(r"^([01]?\d|2[0-3]):[0-5]\d$")  # 6:00 也认（push.py 的写法）
URL = re.compile(r"^https?://[^\s/]+(/\S*)?$")
SECRETISH = re.compile(r"token|key|secret|password", re.IGNORECASE)
NOT_SECRET = re.compile(r"(_env|_file|_url|_uri|_path|_id|_field)$|^(max|min|total|budget)_|^key(less|words)$", re.IGNORECASE)
SECRET_HEADER = re.compile(r"^(authorization|proxy-authorization|cookie)$|key|token|secret|password", re.IGNORECASE)
ALIAS = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")  # 别名（engine/models.py）：不带斜杠
_ALIASES: ContextVar[frozenset[str]] = ContextVar("confcheck_aliases", default=frozenset())  # validate() 现读的 engine.aliases 里的名字


@dataclass
class Finding:
    path: str
    level: str      # error | warn
    message: str

    def to_json(self) -> dict:
        return {"path": self.path, "level": self.level, "message": self.message}


def kind(v) -> str:
    return ("null" if v is None else "bool" if isinstance(v, bool) else "int" if isinstance(v, int) else "number" if isinstance(v, float)
            else "string" if isinstance(v, str) else "list" if isinstance(v, list) else "object" if isinstance(v, dict) else type(v).__name__)


# —— 规格：几个小构造，拼成下面那张表 ——————————————————————————————————————————————————————

class Spec:
    name = "any"

    def check(self, v, path: str, out: list[Finding]) -> bool:  # → 对不对（OneOf 用）；错的写进 out
        return True


class Any_(Spec):
    pass


class Str(Spec):
    name = "string"

    def __init__(self, nonempty: bool = False, pattern: re.Pattern | None = None, what: tuple[str, str] = ("", "")) -> None:
        self.nonempty, self.pattern, self.what = nonempty, pattern, what

    def check(self, v, path, out):
        if not isinstance(v, str):
            out.append(Finding(path, "error", L(f"要写成字符串，现在是 {kind(v)}", f"should be a string, not {kind(v)}")))
            return False
        if self.nonempty and not v.strip():
            out.append(Finding(path, "error", L("不能是空的", "can't be empty")))
            return False
        if self.pattern and v.strip() and not self.pattern.match(v.strip()):  # 空的 = 没写（照默认），不算写错
            out.append(Finding(path, "error", L(f"不像{self.what[0]}：{v[:60]!r}", f"doesn't look like {self.what[1]}: {v[:60]!r}")))
            return False
        return True


class _Ref(Str):
    """provider/model，或者 engine.aliases 里起过的别名（不带斜杠、没起过的名字算错：engine 解析不了。先起别名再用）。"""

    def check(self, v, path, out):
        if isinstance(v, str) and v.strip() in _ALIASES.get():
            return True
        if isinstance(v, str) and ALIAS.match(v.strip()):
            out.append(Finding(path, "error", L(f"{v.strip()!r} 既不是 provider/model，也不是 engine.aliases 里起过的别名",
                                                f"{v.strip()!r} is neither provider/model nor an alias in engine.aliases")))
            return False
        return super().check(v, path, out)


def Ref(alias: bool = True) -> Str:  # noqa: N802
    """模型引用；alias=False：只认 provider/model（别名指的那个：别名不能指别名）。"""
    s = (_Ref if alias else Str)(pattern=REF, what=("模型引用 provider/model", "a provider/model reference"))
    s.name = "provider/model"
    return s


def Url() -> Str:  # noqa: N802
    s = Str(pattern=URL, what=("一个 http(s) 地址", "an http(s) address"))
    s.name = "url"
    return s


def EnvName() -> Str:  # noqa: N802
    s = Str(pattern=ENV, what=("一个环境变量名（A-Z、0-9、_）", "an environment variable name (A-Z, 0-9, _)"))
    s.name = "env var"
    return s


def Time() -> Str:  # noqa: N802
    s = Str(pattern=HHMM, what=("时间 HH:MM", "a time HH:MM"))
    s.name = "HH:MM"
    return s


class Tz(Str):
    name = "time zone"

    def check(self, v, path, out):
        if not super().check(v, path, out):
            return False
        try:
            ZoneInfo(v)
        except (ValueError, KeyError, OSError):
            out.append(Finding(path, "error", L(f"不认识的时区 {v!r}（写 IANA 的名字，比如 Asia/Shanghai）", f"unknown time zone {v!r} (use an IANA name such as Europe/London)")))
            return False
        return True


class Bool(Spec):
    name = "true / false"

    def check(self, v, path, out):
        if not isinstance(v, bool):
            out.append(Finding(path, "error", L(f"要写成 true 或 false，现在是 {kind(v)}", f"should be true or false, not {kind(v)}")))
            return False
        return True


class Num(Spec):
    def __init__(self, lo: float | None = None, hi: float | None = None, integer: bool = False) -> None:
        self.lo, self.hi, self.integer = lo, hi, integer
        self.name = "integer" if integer else "number"

    def check(self, v, path, out):
        ok_type = isinstance(v, int) and not isinstance(v, bool) if self.integer else isinstance(v, (int, float)) and not isinstance(v, bool)
        if not ok_type:
            if isinstance(v, str) and re.fullmatch(r"-?\d+(\.\d+)?", v.strip()):
                out.append(Finding(path, "warn", L(f"写成了字符串 {v!r}：写成数字", f"written as the string {v!r}: write it as a number")))
                return True
            out.append(Finding(path, "error", L(f"要写成{'整数' if self.integer else '数字'}，现在是 {kind(v)}", f"should be {'an integer' if self.integer else 'a number'}, not {kind(v)}")))
            return False
        if (self.lo is not None and v < self.lo) or (self.hi is not None and v > self.hi):
            if self.hi is None:
                want = L(f"至少 {self.lo}", f"at least {self.lo}")
            elif self.lo is None:
                want = L(f"最多 {self.hi}", f"at most {self.hi}")
            else:
                want = f"{self.lo}–{self.hi}"
            out.append(Finding(path, "error", L(f"{v} 不行：要 {want}", f"{v} won't do: it must be {want}")))
            return False
        return True


def Int(lo: float | None = None, hi: float | None = None) -> Num:  # noqa: N802
    return Num(lo, hi, integer=True)


class Enum(Spec):
    def __init__(self, *values: str) -> None:
        self.values = values
        self.name = " | ".join(values)

    def check(self, v, path, out):
        if not isinstance(v, str) or v.strip().lower() not in self.values:
            out.append(Finding(path, "error", L(f"只能是 {' / '.join(self.values)}，现在是 {json.dumps(v, ensure_ascii=False)[:60]}",
                                                f"must be one of {' / '.join(self.values)}, not {json.dumps(v, ensure_ascii=False)[:60]}")))
            return False
        return True


class Null(Spec):
    name = "null"

    def check(self, v, path, out):
        return v is None


class ListOf(Spec):
    def __init__(self, item: Spec) -> None:
        self.item = item
        self.name = f"list of {item.name}"

    def check(self, v, path, out):
        if not isinstance(v, list):
            out.append(Finding(path, "error", L(f"要写成列表，现在是 {kind(v)}", f"should be a list, not {kind(v)}")))
            return False
        good = True
        for i, x in enumerate(v):
            good = self.item.check(x, f"{path}[{i}]", out) and good
        return good


class DictOf(Spec):
    """键随便（Agent 的 id、服务商的名字……），值照 value。"""

    def __init__(self, value: Spec) -> None:
        self.value = value
        self.name = "object"

    def check(self, v, path, out):
        if not isinstance(v, dict):
            out.append(Finding(path, "error", L(f"要写成一个对象 {{…}}，现在是 {kind(v)}", f"should be an object {{…}}, not {kind(v)}")))
            return False
        good = True
        for k, x in v.items():
            good = self.value.check(x, f"{path}.{k}", out) and good
        return good


class Obj(Spec):
    """认识的键各按各的查；不认识的：open = 不管，不然警告一声（不算错）。"""

    def __init__(self, keys: dict[str, Spec] | None = None, open: bool = False) -> None:  # noqa: A002
        self.keys, self.open = keys or {}, open
        self.name = "object"

    def check(self, v, path, out):
        if not isinstance(v, dict):
            out.append(Finding(path, "error", L(f"要写成一个对象 {{…}}，现在是 {kind(v)}", f"should be an object {{…}}, not {kind(v)}")))
            return False
        good = True
        for k, x in v.items():
            sub = f"{path}.{k}" if path else str(k)
            spec = self.keys.get(k)
            if x is None and spec is not None:
                continue  # null = 没写（读的地方都照默认值）
            if spec is None:
                if not self.open:
                    out.append(Finding(sub, "warn", L("不认识的键（拼错了？还是新版本加的）", "unknown key (a typo, or added by a newer version?)")))
                continue
            good = spec.check(x, sub, out) and good
        return good


class OneOf(Spec):
    """几种写法里对上一种就行；都不对：报对得最近的那一种的错（类型对上的那一种），没有就说要哪几种。"""

    def __init__(self, *options: Spec) -> None:
        self.options = options
        self.name = " or ".join(o.name for o in options)

    def check(self, v, path, out):
        tries = []
        for o in self.options:
            got: list[Finding] = []
            if o.check(v, path, got):
                out.extend(f for f in got if f.level == "warn")
                return True
            tries.append(got)
        same_type = [t for t, o in zip(tries, self.options, strict=True) if _type_matches(o, v)]
        if same_type:
            out.extend(same_type[0])
        else:
            out.append(Finding(path, "error", L(f"要写成 {self.name}，现在是 {kind(v)}", f"should be {self.name}, not {kind(v)}")))
        return False


def _type_matches(o: Spec, v) -> bool:
    if isinstance(o, (Str, Enum)):
        return isinstance(v, str)
    if isinstance(o, Bool):
        return isinstance(v, bool)
    if isinstance(o, Num):
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    if isinstance(o, ListOf):
        return isinstance(v, list)
    if isinstance(o, (Obj, DictOf)):
        return isinstance(v, dict)
    if isinstance(o, Null):
        return v is None
    return False


# —— 表 ————————————————————————————————————————————————————————————————————————————

PATH = Str(nonempty=True)
PATH.name = "path"
USE = OneOf(Bool(), Ref(), ListOf(Ref()))
EFFORT = Enum("low", "medium", "high", "xhigh", "max")
LEVEL = Enum("ring", "quiet", "none")

PROVIDER = Obj({"api": Enum("anthropic", "openai", "openai-responses"), "url": Url(), "key_env": OneOf(Str(), ListOf(Str())), "key": Str(),
                "headers": DictOf(Str()), "name": Str(), "region": Str(), "fallbacks": Bool(), "json_mode": Bool(), "reasoning_effort": Bool()},
               open=True)  # 各家的脾气（provider_openai.py 开头）还有一串：只查这几项

ENGINE = Obj({
    "model": OneOf(Ref(), ListOf(Ref())),
    "models": ListOf(OneOf(Ref(), ListOf(Ref()))),
    "fallbacks": ListOf(Ref()),
    "use": Obj({"card": USE, "doorman": USE, "json": USE, "background": USE, "writing": USE, "compact": USE, "cyber": USE}),
    "fallback": Bool(),
    "effort": EFFORT,
    "record_openclaw": Bool(),
    "providers": DictOf(PROVIDER),
    "aliases": DictOf(Ref(alias=False)),
    "recall": Obj({"extra": ListOf(OneOf(Str(), Obj({"path": PATH, "name": Str(), "agents": ListOf(Str())}, open=True))), "main": Enum("own", "all"),
                   "tree": Bool(), "max_chars": Int(0), "half_life_days": Num(0), "db": PATH, "embeddings": OneOf(Bool(), Str(), Obj(open=True)),
                   "tree_md": PATH}, open=True),
    "jobs": Obj({"catch_up_missed": Bool(), "idle_timeout": Num(1), "max_parallel": Int(1), "daily_limit": Int(1)}, open=True),
    "heartbeat": DictOf(OneOf(Bool(), Obj({"every": Str(nonempty=True), "active": ListOf(Time()), "level": LEVEL, "model": Ref(), "light": Bool(),
                                           "prompt": Str()}, open=True))),
    "sandbox": Obj({"network": Str(), "proxy": Url(), "idle_minutes": Num(1), "max_containers": Int(1), "agents": DictOf(OneOf(Bool(), Obj(open=True)))}, open=True),
    "skills": Obj({"nudge": Bool(), "nudge_tools": Int(0), "review": OneOf(Bool(), Ref()), "agents": DictOf(ListOf(Str()))}, open=True),
    "manuals": OneOf(Bool(), Obj({"max": Int(0, 8), "min_score": Num(0, 1), "catalog": Enum("names", "none", "full"), "rewrite": OneOf(Bool(), Ref()),
                                  "max_chars": Int(0), "stale_days": Int(0), "unused_days": Int(0), "enabled": Bool()}, open=True)),
    "claude_code": OneOf(Bool(), Obj({"models": ListOf(Ref()), "effort": EFFORT, "fallback_model": Str(), "idle": Num(0), "builtin_tools": ListOf(Str()),
                                      "max_concurrent": Int(1), "max_oneshot": Int(1), "enabled": Bool()}, open=True)),
    "chatgpt": OneOf(Bool(), Obj({"models": ListOf(Str(nonempty=True)), "headers": DictOf(Str()), "enabled": Bool()}, open=True)),  # 型号可以不带 chatgpt/
    # 10/20：用户自己的 Grok 订阅（engine/grok_plan.py）：型号可以不带 grok/；route = api / proxy；search = false：搜网页不走订阅；x_search = true：搜 X 才走订阅
    "grok": OneOf(Bool(), Obj({"models": ListOf(Str(nonempty=True)), "route": Enum("api", "proxy"), "headers": DictOf(Str()), "search": Bool(), "x_search": Bool(),
                               "enabled": Bool()}, open=True)),
    # 第 7 批：codex/…、copilot/… 当模型（engine/provider_cli.py，默认关）
    "codex": OneOf(Bool(), Obj({"models": ListOf(Ref()), "max_concurrent": Int(1), "enabled": Bool()}, open=True)),
    "copilot": OneOf(Bool(), Obj({"models": ListOf(Ref()), "max_concurrent": Int(1), "background": Bool(), "max_ai_credits": Int(30), "bin": PATH,
                                  "enabled": Bool()}, open=True)),
    "auto": Obj({"cheap": Ref(), "max_chars": Int(1), "max_words": Int(1), "keywords": ListOf(Str()), "enabled": Bool()}, open=True),  # 第 7 批：engine/auto.py
    "cache_warming": OneOf(Bool(), Obj({"hours": Num(0.1, 24), "every_minutes": Num(5, 59), "enabled": Bool()}, open=True)),  # 第 7 批：engine/warming.py
    "api": OneOf(Bool(), Obj({"agents": ListOf(Str()), "approve": Bool(), "wait": Num(0), "enabled": Bool(), "responses": Bool(), "a2a": Bool()})),
    "files": Obj(open=True),
    "web": Obj({"search": OneOf(Bool(), Str(), Obj(open=True)), "fetch": OneOf(Bool(), Obj(open=True)), "agents": Any_(),
                "x_search": OneOf(Bool(), Obj({"model": Str(nonempty=True), "enabled": Bool()}, open=True))}, open=True),  # 10/20：engine/xsearch.py
    "host_exec": Obj({"agents": DictOf(OneOf(Bool(), Obj({"allow": ListOf(Str()), "ask": Enum("on-miss", "always", "off"),
                                                          "security": Enum("allowlist", "full"), "timeout": Num(1), "env": ListOf(EnvName())},
                                                         open=True)))}, open=True),
    "tools": Obj({"levels": DictOf(Enum("auto", "ask", "off"))}, open=True),
    "sessions": Obj({"visibility": Enum("agent", "self", "all"), "agents": DictOf(OneOf(Str(), ListOf(Str())))}, open=True),
    "handoff": Obj({"agents": Bool(), "max_hops": Int(1, 4)}, open=True),
    "plan": Obj(open=True),
    "writeback": OneOf(Bool(), Obj(open=True)),
    "images": Obj(open=True),
    "compaction": Obj(open=True),
    "ask_user": Any_(),
})

KEYS = Obj({
    "app_name": Str(), "user_name": Str(),
    "owner_id": Str(pattern=re.compile(r"^[a-z0-9_-]{1,40}$"), what=("一个 id（小写字母、数字、- 和 _）", "an id (lowercase letters, digits, - and _)")), "timezone": Tz(), "language": Str(pattern=re.compile(r"^(zh|en)([-_]\w+)*$", re.IGNORECASE), what=("zh 或 en", "zh or en")),
    "bind": Obj({"host": Str(nonempty=True), "port": Int(1, 65535), "auto": Bool()}),
    "claw": Obj({"kind": Enum("builtin", "openclaw", "openai"), "name": Str(), "url": Url(), "token": Str(), "token_env": EnvName(), "model": Str(),
                 "models": ListOf(Str()), "session": Any_(), "headers": DictOf(Str()), "skills": PATH, "rules": PATH, "env_file": PATH}, open=True),
    "engine": ENGINE,
    "openclaw_home": PATH, "workspace": PATH, "agent_workspaces": DictOf(PATH), "scripts": PATH, "data_dir": PATH, "db": PATH, "uploads": PATH,
    "profile_history": PATH, "profile": PATH, "dist": PATH, "gateway": Url(), "openclaw_bin": Str(), "env_file": PATH, "secrets_file": PATH,
    "default_model": Str(), "backup_dir": PATH,
    "auth": Obj({"tokens": DictOf(Str(nonempty=True)), "tailscale_nodes": ListOf(Str()), "trust_loopback": Bool(),
                 "pairing": ListOf(Obj({"hash": Str(), "expires": Str(), "name": Str()}))}),
    "nutrition_targets": OneOf(Null(), Obj({"kcal": Num(0), "protein": Num(0), "carb": Num(0), "fat": Num(0)})),
    "agent_default_skills": ListOf(Str()), "transcribe_prompt": Str(), "transcribe_url": Url(),
    "push": Obj({"quiet_hours": OneOf(Null(), ListOf(Time()))}, open=True),
    "apns": Obj({"key_file": PATH, "key_id": Str(), "team_id": Str(), "topic": Str(), "sandbox": Bool()}),
    "tasks": Obj({"daily_limit": Int(0), "max_minutes": Num(1), "notify_done": Bool(), "max_external": Int(1), "runners": DictOf(OneOf(Bool(), Obj(open=True)))},
                 open=True),
    "study": Obj(open=True), "chat": Obj({"transport": Enum("http", "ws")}, open=True),
    "share": Obj({"public_url": Url(), "public_port": Int(1, 65535), "private_words": ListOf(Str()), "font": Str(), "font_bold": Str()}, open=True),
    "social": Obj(open=True), "podcast": Obj(open=True), "mcp": Obj(open=True), "apps": Obj(open=True),
    "mcp_servers": DictOf(Obj(open=True)), "mcpServers": DictOf(Obj(open=True)), "wake": Obj(open=True),
    "relay": OneOf(Bool(), Obj(open=True)), "think": Obj(open=True), "today": Obj(open=True), "tree": Obj(open=True),
    "update": Obj({"channel": Enum("stable", "main"), "remote": Str(nonempty=True), "wait": Num(1), "idle_wait": Num(0)}),
    # 「接下来会自动做的事」里起好认的名字（data.py）、连接页认的单元名和文件（connectors.py）：2026-10-20 从代码里挪出来的
    "upcoming": Obj({"titles": DictOf(OneOf(Str(), Obj({"zh": Str(), "en": Str(), "repeat": Enum("daily", "weekly")}))), "reminder_prefix": Str()}),
    "connectors": Obj({"units": Obj({"browser": Str(), "mail": Str(), "obsidian": Str(), "tree": Str()}), "canvas_courses": PATH}),
})


def validate(data) -> list[Finding]:
    """整份查一遍 → 找到的（✗ error、! warn）。不是一个 JSON 对象 = 一条 error。"""
    out: list[Finding] = []
    if not isinstance(data, dict):
        return [Finding("", "error", L("server.json 要是一个 JSON 对象", "server.json must be a JSON object"))]
    eng = data.get("engine") if isinstance(data.get("engine"), dict) else {}
    al = eng.get("aliases") if isinstance(eng.get("aliases"), dict) else {}
    token = _ALIASES.set(frozenset(str(k) for k, t in al.items() if ALIAS.match(str(k)) and isinstance(t, str) and REF.match(t.strip())))
    try:
        KEYS.check(data, "", out)
    finally:
        _ALIASES.reset(token)
    return out


def errors(found: list[Finding]) -> list[Finding]:
    return [f for f in found if f.level == "error"]


# —— 点路径 ————————————————————————————————————————————————————————————————————————

def split(path: str) -> list[str]:
    parts = [p for p in str(path or "").strip().split(".")]
    if not parts or any(not p for p in parts):
        raise ValueError(L(f"点路径不像样：{path!r}（比如 engine.model）", f"not a dotted path: {path!r} (e.g. engine.model)"))
    return parts


def lookup(data: dict, parts: list[str]):
    cur = data
    for p in parts:
        if isinstance(cur, dict) and p in cur:
            cur = cur[p]
        elif isinstance(cur, list) and p.isdigit() and int(p) < len(cur):
            cur = cur[int(p)]
        else:
            raise KeyError(".".join(parts))
    return cur


def secret_key(name: str) -> bool:
    """键名像密钥（token / key / secret / password）：变量名、文件、地址、编号那几种（key_env、key_file、key_id……）、数目（max_tokens……）、
    keyless、keywords 不算。"""
    return bool(SECRETISH.search(name)) and not NOT_SECRET.search(name)


def secret_value(key: str, v, header: bool = False) -> bool:
    """这个值算密钥：键名像（header 里的 Authorization、Cookie、*-Key……），而且不是 ${变量}、变量名。"""
    if not isinstance(v, str) or not v or v.startswith("${") or ENV.match(v):
        return False
    return bool(SECRET_HEADER.search(key)) if header else secret_key(key)


def masked(v, key: str = "", header: bool = False):
    """像密钥的值一律打码（secret_value；auth.tokens 底下的全是）。"""
    if isinstance(v, dict):
        return {k: ("***" if key == "tokens" and isinstance(x, str) else masked(x, str(k), key.lower() == "headers")) for k, x in v.items()}
    if isinstance(v, list):
        return [masked(x, key, header) for x in v]
    return "***" if secret_value(key, v, header) else v


def has_secret(v, key: str = "", header: bool = False) -> bool:
    """要写进去的值里有没有密钥（整个对象、列表往里看；同 masked 认的那几种）。"""
    if isinstance(v, dict):
        return (key == "tokens" and any(isinstance(x, str) for x in v.values())) or \
            any(has_secret(x, str(k), key.lower() == "headers") for k, x in v.items())
    if isinstance(v, list):
        return any(has_secret(x, key, header) for x in v)
    return secret_value(key, v, header)


def guarded(parts: list[str]) -> bool:
    """set / unset 都不给碰的：auth 整段（换掉、去掉就进不来了）、auth.tokens（tokens.py）、auth.pairing（配对）。"""
    return parts == ["auth"] or parts[:2] in (["auth", "tokens"], ["auth", "pairing"])


def parse_value(raw: str):
    """set 的值：先当 JSON（true、8080、"文字"、[…]、{…}），不是 JSON 就当字符串。"""
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def change(parts: list[str], value=None, *, unset: bool = False) -> tuple[dict, dict]:
    """现读 server.json → (原来的, 改完的拷贝)。读不了抛 RuntimeError（不拿空的写回去）。"""
    data, problem = C.load_config()
    if problem in ("broken", "unreadable") or data is None:
        raise RuntimeError(L("server.json 读不了：先修好（mousse doctor 看为什么），不拿空的写回去", "server.json can't be read: fix it first (see mousse doctor); "
                             "it won't be overwritten with an empty one"))
    new = json.loads(json.dumps(data))
    cur = new
    for p in parts[:-1]:
        nxt = cur.get(p)
        if nxt is None and not unset:
            nxt = cur[p] = {}
        if not isinstance(nxt, dict):
            if unset:
                return data, new
            raise RuntimeError(L(f"{'.'.join(parts[:parts.index(p) + 1])} 不是一个对象，往下写不了", f"{'.'.join(parts[:parts.index(p) + 1])} isn't an object"))
        cur = nxt
    if unset:
        cur.pop(parts[-1], None)
    else:
        cur[parts[-1]] = value
    return data, new


def write(new: dict) -> Path | None:
    bak = C.backup_config()
    C.write_config(new)
    return bak


# —— 命令行 ——————————————————————————————————————————————————————————————————————————

def show(found: list[Finding]) -> None:
    for f in found:
        print(f"  {'✗' if f.level == 'error' else '!'} {f.path or 'server.json'}{L('：', ': ')}{f.message}")


def main(a) -> int:
    if a.action == "validate":
        p = Path(a.file).expanduser() if a.file else C.config_path()
        try:
            data = json.loads(p.read_text(encoding="utf8"))
        except FileNotFoundError:
            found = [Finding("", "error", L(f"{C.tilde(p)} 不在", f"{C.tilde(p)} doesn't exist"))]
        except (OSError, UnicodeDecodeError) as e:
            found = [Finding("", "error", L(f"读不了：{e}", f"can't read it: {e}"))]
        except ValueError as e:
            found = [Finding("", "error", L(f"不是合法的 JSON：{e}", f"not valid JSON: {e}"))]
        else:
            found = validate(data)
        bad = errors(found)
        if a.json:
            print(json.dumps({"ok": not bad, "file": C.tilde(p), "errors": len(bad), "warnings": len(found) - len(bad),
                              "findings": [f.to_json() for f in found]}, ensure_ascii=False, indent=2))
        else:
            show(found)
            print(L(f"{C.tilde(p)}：{len(bad)} 处不对，{len(found) - len(bad)} 处要留意", f"{C.tilde(p)}: {len(bad)} errors, {len(found) - len(bad)} warnings")
                  + ("" if found else L("（都对）", " (all good)")))
        return 1 if bad else 0
    try:
        parts = split(a.path)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    if a.action == "get":
        data, problem = C.load_config()
        if problem in ("broken", "unreadable") or data is None:
            print(L("server.json 读不了（mousse doctor 看为什么）", "server.json can't be read (see mousse doctor)"), file=sys.stderr)
            return 1
        try:
            v = lookup(data, parts)
        except KeyError:
            print(L(f"没有 {a.path}（没写 = 用默认值）", f"{a.path} isn't set (the default applies)"), file=sys.stderr)
            return 1
        header = len(parts) > 1 and parts[-2].lower() == "headers"
        print(C.scrub(json.dumps(masked(v, parts[-1], header), ensure_ascii=False, indent=2)))
        return 0
    header = len(parts) > 1 and parts[-2].lower() == "headers"
    value = None if a.action == "unset" else parse_value(a.value)
    if guarded(parts) or secret_key(parts[-1]) or (header and SECRET_HEADER.search(parts[-1])) or has_secret(value, parts[-1], header):
        print(L("密钥、令牌不往 server.json 里写：API 密钥用 setup_ctl.py add <服务商>（存进 secrets.env），接入令牌用 tokens.py add <名字>",
                "Keys and tokens don't go into server.json: API keys with setup_ctl.py add <provider> (stored in secrets.env), access tokens with "
                "tokens.py add <name>"), file=sys.stderr)
        return 2
    try:
        before, new = change(parts, value, unset=a.action == "unset")
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 1
    if new == before:
        print(L("没变", "No change"))
        return 0
    found = validate(new)
    bad = errors(found)
    if bad:
        print(L("改完的样子没过校验，没写：", "The result doesn't validate, so nothing was written:"))
        show(bad)
        return 1
    bak = write(new)
    print(L(f"写好了（server.json 备份在 {C.tilde(bak)}）", f"Saved (server.json backed up as {C.tilde(bak)})") if bak else L("写好了", "Saved"))
    new_warn = [f for f in found if f.path.startswith(".".join(parts))]
    show(new_warn)
    return 0
