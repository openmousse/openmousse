"""通用的选择层（内核第 9 步，2026-10-17）：第一次打开的引导和设置中心用的接口。用户自己选用哪家模型、哪种记忆、哪家搜索，不绑任何一家。
设计借 OpenHuman（tinyhumansai/openhuman，GPL-3.0）第一次打开的三步（选模型、搜网页、记忆）和设置里「连接」「密钥」的分法，只借设计，自己写。

每一层都有几种选法：云端（填密钥）/ 本机跑的 / 复用这台电脑上已经登录的工具 / 自己填地址 / 不用。默认不写死成哪一家：没配就引导用户配；
多家能同时连，按用途挑（主对话、后台和定时、写作、外人回合、向量）。服务商目录在 engine/catalog.py，测一下和本机探测在 engine/probe.py，
用量在 engine/usage.py，密钥存在 secretstore.py。

规矩：
  - 只认主人的设备（owner）：app 的令牌（auth.tokens 里给人用的那些；mcp、api、sentinel 这些给程序的，main.guard 早挡了）或者
    Tailscale 白名单里的设备。只靠「本机免令牌」（trust_loopback）进来的、带 X-Mousse-Via 的（脚本、/mcp 替 Agent 发的）一律 403。
  - 密钥存进 secrets.env（0600、原子写），永远不回给 app：只回有没有、从哪来、末四位、哪天存的。不写进 server.json、日志、报错（测一下的
    报错过 errors.scrub）。请求体不用 pydantic 模型：校验没过时 FastAPI 的 422 会把原样的输入（连密钥）回给调用方；这里自己查，报错不带值。
  - 只有用户点「保存」（PUT / DELETE）才写；GET 什么都不改（老实例照实反映：claw = openclaw、已有的 engine 段、env_file 里的密钥）。
    写 server.json 之前先在同一目录备份一份（server.json.bak-<时间>，0600，留最近 20 份）；server.json 读不了（坏了）就不写。
  - 只有一家能用、主对话还没选模型（claw 是 builtin）时，存这一家的时候顺手用它（auto）。老实例（claw = openclaw）不自动改。

接口（都在 /api/setup 下；出错一律 {"ok": false, "error": "一句话", …}；给人看的字按 Accept-Language）：
  GET    /api/setup                         总览：每一层（模型、搜索、记忆、向量、连接、手）配好没有、缺什么；firstRun、noModel
  GET    /api/setup/models                  服务商：目录 + 连没连 + 本机探测到的 + 命令行登录（?probe=0 不探本机，?check=1 现查 Claude Code / Codex）
  PUT    /api/setup/models/{id}             存：{"key"?, "region"?, "url"?}；目录里没有的 id = 自定义的地址 {"name"?, "url", "api"?, "key"?}
  POST   /api/setup/models/{id}/test        测一下：存下的，或者请求里还没存的 {"key"?, "url"?, "region"?, "model"?}
  DELETE /api/setup/models/{id}             删掉 app 里存的密钥（自定义的连同 engine.providers 那一项）
  GET    /api/setup/purposes                按用途选模型（能选的只列连上的；推荐 = 理由 + 代价；只有一家时的 auto）
  PUT    /api/setup/purposes                {"chat"?, "background"?, "writing"?, "outsider"?, "card"?, "doorman"?, "embeddings"?, "models"?}
  GET    /api/setup/search                  搜索：开没开、用哪家、每家连没连
  PUT    /api/setup/search                  {"enabled"?, "provider"?}：开关、默认的一家（null = 按顺序挑有密钥的）
  PUT    /api/setup/search/{id}             {"key"}（SearXNG 是 {"url"}）
  POST   /api/setup/search/{id}/test        搜一句（存下的，或者 {"key"?, "url"?}）
  DELETE /api/setup/search/{id}             删掉密钥 / 地址
  GET    /api/setup/memory                  记忆：世界树放哪、有多少、按意思搜开没开
  PUT    /api/setup/memory                  {"storage": "sqlite" | "markdown", "path"?}（先只支持第一次选）和 / 或 {"embeddings": …}
  POST   /api/setup/embeddings/test         {"id", "model"?, "dims"?}：算一个向量
  GET    /api/setup/usage                   用量（?days=30）；也挂在 /api/engine/usage
请求和返回的样子在 PR 的接口说明里，也写在下面每个接口函数上。
"""
from __future__ import annotations

import asyncio
import contextlib
import copy
import json
import os
import re
import shutil
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

import claw
import config
import secretstore
from config import settings
from i18n import L

from . import catalog, models, probe
from . import usage as usage_mod
from .errors import EngineError

BACKUPS_KEPT = 20
ID = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,31}$")
URL = re.compile(r"^https?://[A-Za-z0-9.\-\[\]:]+(?:/[^\s?#]*)?$")
MODEL = re.compile(r"^[A-Za-z0-9][\w.:/\-\[\]@+]{0,199}$")
MAX_CACHED = 2000
LOCAL_TTL = 20.0
PURPOSES = ("chat", "background", "writing", "outsider", "embeddings")


# —— 错、主人的设备 ————————————————————————————————————————————————————————————————

class SetupError(Exception):
    """接口出错：状态码 + 一句给人看的话 + 别的字段（main.py 挂的处理器回 {"ok": false, "error", …}）。"""

    def __init__(self, status: int, message: str, **extra) -> None:
        super().__init__(message)
        self.status, self.extra = status, extra


async def on_error(_request: Request, exc: SetupError) -> JSONResponse:
    return JSONResponse({"ok": False, "error": str(exc), **exc.extra}, status_code=exc.status)


def owner(request: Request) -> str:
    """只认主人的设备（见文件开头）。"""
    import pairing
    who = str(getattr(request.state, "principal", "") or "")
    if request.headers.get("x-mousse-via") is None:
        if who.startswith("tailscale:"):
            return who
        if who.startswith("token:") and not pairing.program_token(who[6:]):
            return who
    raise SetupError(403, L("设置只能在你自己的设备上改：用 app 的令牌，或者 Tailscale 里你自己的设备",
                            "Setup can only be changed from your own devices: the app's token, or one of your devices on Tailscale"))


router = APIRouter(dependencies=[Depends(owner)])


async def body_of(request: Request) -> dict:
    """请求体：空的 = {}；不是 JSON 对象 → 400（不回原样的输入）。"""
    raw_ = await request.body()
    if not raw_.strip():
        return {}
    try:
        b = json.loads(raw_)
    except ValueError:
        b = None
    if not isinstance(b, dict):
        raise SetupError(400, L("请求体要是一个 JSON 对象", "The body must be a JSON object"))
    return b


def _str(b: dict, key: str, limit: int = 300) -> str | None:
    """请求体里的一个字符串字段：没给 / null → None；不是字符串、太长 → 400（不带值）。"""
    v = b.get(key)
    if v is None:
        return None
    if not isinstance(v, str) or len(v) > limit:
        raise SetupError(400, L(f"{key} 要是一个不太长的字符串", f"{key} must be a short string"), field=key)
    return v.strip()


def _key(b: dict) -> str | None:
    """请求体里的密钥：不像样 → 400（不带值，secretstore 的报错本来就不带）。"""
    k = _str(b, "key", 4096)
    if not k:
        return None
    try:
        return secretstore.check_value("key", k)
    except secretstore.SecretError:
        raise SetupError(400, L("密钥里有它不该有的字（空格、换行、引号、中文……）", "The key has characters a key can't have (spaces, line breaks, quotes, non-ASCII)"),
                         field="key") from None


def _url(b: dict, key: str = "url") -> str | None:
    u = _str(b, key, 300)
    if not u:
        return None
    u = u.rstrip("/")
    if not URL.match(u) or "@" in u:
        raise SetupError(400, L("地址要写成 http(s)://主机[:端口]/路径（不带用户名密码、问号后面的）",
                                "The address must look like http(s)://host[:port]/path (no user:password, no query)"), field=key)
    return u


# —— server.json：现读、备份、写 ——————————————————————————————————————————————————————

_wlock = threading.Lock()


def read_config() -> dict:
    """写之前现读 server.json（不走缓存）。读不了（JSON 坏了）→ 409：不能拿空的写回去，把令牌和别的设置都冲掉。"""
    p = config.CONFIG_PATH
    try:
        text = p.read_text(encoding="utf8")
    except FileNotFoundError:
        return {}
    except OSError:
        raise SetupError(409, L("server.json 读不了，先看看它的权限", "server.json can't be read; check its permissions")) from None
    try:
        data = json.loads(text) if text.strip() else {}
    except ValueError:
        data = None
    if not isinstance(data, dict):
        raise SetupError(409, L("server.json 写坏了（不是一个 JSON 对象），先修好再保存", "server.json is broken (not a JSON object); fix it before saving"))
    return data


def backup() -> str | None:
    """server.json 在同一目录备份一份（server.json.bak-<年月日-时分秒>，0600），只留最近 BACKUPS_KEPT 份。→ 备份的文件名。"""
    p = config.CONFIG_PATH
    if not p.exists():
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dst, n = p.with_name(f"{p.name}.bak-{stamp}"), 1
    while dst.exists():
        dst, n = p.with_name(f"{p.name}.bak-{stamp}-{n}"), n + 1
    fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f, open(p, "rb") as src:
        shutil.copyfileobj(src, f)
    olds = sorted(p.parent.glob(f"{p.name}.bak-*"), key=lambda x: (x.stat().st_mtime, x.name))
    for old in olds[:-BACKUPS_KEPT]:
        with contextlib.suppress(OSError):
            old.unlink()
    return dst.name


def edit(mutate: Callable[[dict], None]) -> dict:
    """改 server.json：现读 → mutate(一份拷贝) → 变了才备份 + 写（config.save：0600、先写临时文件再换名）。→ {"changed", "backup"}。"""
    with _wlock:
        data = read_config()
        new = copy.deepcopy(data)
        mutate(new)
        if new == data:
            return {"changed": False, "backup": None}
        name = backup()
        config.save(new)
        return {"changed": True, "backup": name}


def section(d: dict, *path: str) -> dict:
    """d[path…]，一路没有就建成 {}（不是 dict 的换成 {}）。"""
    for k in path:
        v = d.get(k)
        if not isinstance(v, dict):
            v = d[k] = {}
        d = v
    return d


def prune(d: dict, *path: str) -> None:
    """把 path 上变空了的 dict 一层层去掉（engine.providers.x = {} 这种不留）。"""
    chain = [d]
    for k in path:
        v = chain[-1].get(k)
        if not isinstance(v, dict):
            return
        chain.append(v)
    for i in range(len(path), 0, -1):
        if chain[i] == {}:
            chain[i - 1].pop(path[i - 1], None)


# —— 一家模型服务现在的样子 ————————————————————————————————————————————————————————————

def over(pid: str) -> dict:
    """server.json 的 engine.providers.<pid>（没有 = {}）。"""
    p = models.cfg().get("providers")
    v = p.get(pid) if isinstance(p, dict) else None
    return v if isinstance(v, dict) else {}


def effective(pid: str) -> dict | None:
    """叠完三处以后的这一家（models.provider）；认不出、没地址 → None。"""
    try:
        return models.provider(pid)
    except EngineError:
        return None


def key_env(pid: str, p: dict | None = None) -> str:
    """这一家的密钥存在哪个变量名下：配置里写成列表的取第一个；没写就是目录里的；都没有按 models.provider 的默认（<名字>_API_KEY）。"""
    p = p if p is not None else (effective(pid) or {})
    ke = p.get("key_env", None)
    if ke is None:
        e = catalog.entry(pid)
        ke = e.get("key_env") if e else f"{pid.upper()}_API_KEY"
    if isinstance(ke, list):
        ke = next((str(x).strip() for x in ke if str(x).strip()), "")
    return str(ke or "")


def key_info(pid: str, p: dict | None = None) -> dict | None:
    """这一家的密钥给 app 看的那一面（secretstore.info）；server.json 里直接写了 key 的算 config；不要密钥的 → None。"""
    p = p if p is not None else (effective(pid) or {})
    literal = str(p.get("key") or "").strip() if p else ""
    name = key_env(pid, p)
    if literal and p.get("keys") and p["keys"][0][0] == "key":
        return {"name": name or None, "present": True, "source": "config", "hint": secretstore.hint(literal), "savedAt": None}
    if not name:
        return None
    return secretstore.info(name)


# —— 列过的模型（测一下列出来的，存在 <data_dir>/setup/models.json，不含密钥）——————————————————————

_mcache: tuple[float, dict] | None = None


def _mcache_path() -> Path:
    return settings.data_dir / "setup" / "models.json"


def model_cache() -> dict:
    """{家: {"at", "models"}}（按 mtime 缓存）。"""
    global _mcache
    p = _mcache_path()
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return {}
    if _mcache and _mcache[0] == mtime:
        return _mcache[1]
    try:
        d = json.loads(p.read_text(encoding="utf8"))
    except (OSError, ValueError):
        d = {}
    _mcache = (mtime, d if isinstance(d, dict) else {})
    return _mcache[1]


def url_now(pid: str) -> str:
    """这一家现在用的地址：列过的型号认这个（换了地区 / 地址，别处列的不算）。"""
    if pid in catalog.LOCAL:
        return local_url(pid)
    p = effective(pid) or over(pid)
    return str(p.get("url") or (catalog.entry(pid) or {}).get("url") or "").rstrip("/")


def listed_of(pid: str) -> dict:
    """列过的这一家 {"at", "models", "listed", "url"}；是在别的地址上列的 → {}。型号名是规范的写法（以前列下来带 models/ 前缀的也去掉）。"""
    c = model_cache().get(pid) or {}
    if c.get("url") and c["url"] != url_now(pid):
        return {}
    ms = c.get("models")
    return {**c, "models": list(dict.fromkeys(catalog.bare(pid, str(m)) for m in ms))} if isinstance(ms, list) else c


def remember_models(pid: str, ids: list[str], url: str, listed: bool = True) -> None:
    """url = 在哪个地址上列的 / 测的；listed = 这一家列出来的全部，False = 列不了模型的那几家里测通过的型号（推荐的照样能选）。"""
    d = dict(model_cache())
    ids = list(dict.fromkeys(catalog.bare(pid, str(x)) for x in ids))
    d[pid] = {"at": datetime.now(settings.tz).isoformat(timespec="seconds"), "models": ids[:MAX_CACHED], "listed": listed,
              "url": str(url or "").rstrip("/")}
    p = _mcache_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf8")
    tmp.replace(p)
    global _mcache
    _mcache = None


def forget_models(pid: str) -> None:
    if pid in model_cache():
        d = dict(model_cache())
        d.pop(pid, None)
        p = _mcache_path()
        tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf8")
        tmp.replace(p)
        global _mcache
        _mcache = None


# —— 本机探测（并发、短超时，结果留 LOCAL_TTL 秒）————————————————————————————————————————

_local: tuple[float, dict] | None = None


def local_url(pid: str) -> str:
    """本机这一家现在的地址：server.json 改过就用它，没改用目录里的默认。"""
    return str(over(pid).get("url") or catalog.LOCAL[pid]["url"]).rstrip("/")


async def detect_locals(fresh: bool = False) -> dict[str, dict]:
    """{家: probe.detect_local 的结果}。默认端口没探到、目录里写了别的常见端口（alt）的再探那个。"""
    global _local
    if not fresh and _local and time.monotonic() - _local[0] < LOCAL_TTL:
        return _local[1]
    targets = [(pid, e, local_url(pid)) for pid, e in catalog.LOCAL.items()]
    found = await probe.detect_all(targets)
    alts = [(pid, {**catalog.LOCAL[pid], "detect": d}, u) for pid, e in catalog.LOCAL.items()
            if not found[pid]["detected"] and not over(pid).get("url") for u, d in e.get("alt") or []]
    if alts:
        more = await probe.detect_all(alts)
        for pid, r in more.items():
            if r["detected"]:
                found[pid] = r
    _local = (time.monotonic(), found)
    return found


def forget_locals() -> None:
    """本机的地址改了：下一次重新探。"""
    global _local
    _local = None


def reset() -> None:
    """测试用：进程里的缓存清掉。"""
    global _local, _mcache
    _local = _mcache = None


# —— 服务商一行行 ————————————————————————————————————————————————————————————————————

def cached_count(pid: str) -> dict:
    c = listed_of(pid)
    return {"count": len(c.get("models") or []), "at": c.get("at")}


def cloud_row(pid: str, e: dict) -> dict:
    p = effective(pid) or {}
    url = str(p.get("url") or e["url"]).rstrip("/")
    rid = catalog.region_of(e, url)
    ki = key_info(pid, p) or {"present": False, "source": None, "hint": None, "savedAt": None}
    reg = catalog.region(e, rid if rid != "custom" else None)
    return {"id": pid, "kind": "cloud", "name": catalog.text(e["name"]), "api": p.get("api") or e["api"], "url": url, "defaultUrl": e["url"],
            "region": rid, "regions": [{"id": r, "label": catalog.text(v["label"]), "url": v["url"]} for r, v in (e.get("regions") or {}).items()],
            "keyEnv": key_env(pid, p), "key": ki, "keyUrl": reg.get("key_url"), "docs": e.get("docs"), "caps": e["caps"],
            "market": e.get("market"), "note": catalog.text(e.get("note")), "connected": bool(ki.get("present")), "models": cached_count(pid),
            "canList": e["list"] != "none", "suggest": suggestions(e, p), "cost": catalog.text(catalog.KIND_NOTES["cloud"])}


def local_row(pid: str, e: dict, found: dict | None) -> dict:
    f = found or {"detected": False, "url": local_url(pid), "version": None, "models": []}
    return {"id": pid, "kind": "local", "name": catalog.text(e["name"]), "api": e["api"], "url": f["url"], "defaultUrl": e["url"],
            "detected": bool(f["detected"]), "version": f.get("version"), "models": f.get("models") or [], "connected": bool(f["detected"]),
            "configured": bool(over(pid)), "start": e.get("start"), "install": e.get("install"), "docs": e.get("docs"), "mac": bool(e.get("mac")),
            "caps": e["caps"], "cost": catalog.text(catalog.KIND_NOTES["local"])}


def login_rows() -> list[dict]:
    """命令行登录：Claude Code（runners 的检测）、ChatGPT 套餐（chatgpt_plan）、Codex（后台任务的外援）。不碰任何令牌。"""
    from . import chatgpt_plan, claude_brain, runners
    if not runners._warm:  # noqa: SLF001 — 服务器不是 builtin 时没人查过：在后台查一次，下一次来就有了
        runners.warm()
    out = []
    cc = claude_brain.status()
    out.append({"id": "claude-code", "kind": "login", "role": "chat", "name": "Claude Code", "ready": bool(cc["available"]),
                "auth": cc["auth"], "billing": cc["billing"], "plan": cc["plan"], "models": cc["models"], "enabled": cc["enabled"],
                "checking": bool(runners._checking), "how": catalog.text(catalog.LOGINS["claude-code"]["how"]),  # noqa: SLF001
                "docs": catalog.LOGINS["claude-code"]["docs"], "cost": catalog.text(catalog.KIND_NOTES["plan"])})
    st = chatgpt_plan.status()
    out.append({"id": "chatgpt", "kind": "login", "role": "chat", "name": catalog.text(catalog.LOGINS["chatgpt"]["name"]),
                "ready": bool(st["ready"]), "connected": st["connected"], "planEnabled": st["planEnabled"], "plan": st["plan"],
                "models": st["models"], "needsReconnect": st["needsReconnect"], "background": st["background"], "enabled": st["enabled"],
                "lastError": st["lastError"], "login": {"start": "POST /api/engine/chatgpt/login", "status": "GET /api/engine/chatgpt/login",
                                                        "callback": "POST /api/engine/chatgpt/callback", "disconnect": "POST /api/engine/chatgpt/disconnect"},
                "how": catalog.text(catalog.LOGINS["chatgpt"]["how"]), "docs": catalog.LOGINS["chatgpt"]["docs"],
                "cost": catalog.text(catalog.KIND_NOTES["plan"])})
    cx = runners.available().get("codex")
    out.append({"id": "codex", "kind": "login", "role": "helper", "name": "Codex", "ready": cx is not None, "auth": (cx or {}).get("auth"),
                "billing": (cx or {}).get("billing"), "viaPlan": bool((cx or {}).get("chatgpt_plan")), "enabled": runners.enabled("codex"),
                "how": catalog.text(catalog.LOGINS["codex"]["how"]), "docs": catalog.LOGINS["codex"]["docs"]})
    return out


def custom_ids() -> list[str]:
    """engine.providers 里目录没有的（自定义的地址）；chatgpt、claude-code 这些名字不算。"""
    p = models.cfg().get("providers")
    names = [str(k) for k, v in (p.items() if isinstance(p, dict) else []) if isinstance(v, dict)]
    return [n for n in names if catalog.entry(n) is None and n not in models.SUBSCRIPTIONS]


def custom_row(pid: str) -> dict:
    o, p = over(pid), effective(pid)
    ki = key_info(pid, p or o) if (p or o).get("key_env", None) != "" else None
    keyless = (p or o).get("key_env", None) == ""
    return {"id": pid, "kind": "custom", "name": str(o.get("name") or pid), "api": (p or o).get("api") or catalog.OPENAI,
            "url": str((p or o).get("url") or "").rstrip("/"), "keyEnv": key_env(pid, p or o) or None, "key": ki, "keyless": keyless,
            "valid": p is not None, "connected": p is not None and (keyless or bool(ki and ki.get("present"))), "models": cached_count(pid),
            "cost": catalog.text(catalog.KIND_NOTES["custom"])}


def openclaw_rows() -> list[dict]:
    """openclaw.json 里配的、目录和 engine.providers 都没有的（只读：在 OpenClaw 里改）。"""
    out = []
    for pid in sorted(models._openclaw_providers()):  # noqa: SLF001
        if catalog.entry(pid) or pid in over_names() or pid in models.SUBSCRIPTIONS:
            continue
        p = effective(pid)
        if p is None:
            continue
        ki = key_info(pid, p)
        out.append({"id": pid, "kind": "openclaw", "name": pid, "api": p["api"], "url": p.get("url"), "readOnly": True,
                    "key": ki, "connected": bool(p.get("key")) or p.get("key_env", "x") == "", "models": cached_count(pid)})
    return out


def over_names() -> set[str]:
    p = models.cfg().get("providers")
    return {str(k) for k in p} if isinstance(p, dict) else set()


async def providers(probe_local: bool = True, check: bool = False) -> list[dict]:
    if check:
        from . import runners
        await asyncio.to_thread(runners.refresh)
    found = await detect_locals(fresh=True) if probe_local else (_local[1] if _local else {})
    rows = [cloud_row(pid, e) for pid, e in catalog.CLOUD.items()]
    rows += [local_row(pid, e, found.get(pid)) for pid, e in catalog.LOCAL.items()]
    rows += login_rows()
    rows += [custom_row(pid) for pid in custom_ids()]
    rows += openclaw_rows()
    return rows


# —— 连上了没有、能选哪些模型 ————————————————————————————————————————————————————————

def usable(ref: str | None) -> bool:
    """这个模型现在跑得了吗（不连网）：套餐 → 这台电脑上登录着；别的 → 认得这一家、要密钥的有密钥。"""
    if not ref:
        return False
    if models.subscription(ref):
        return models.subscription_ready(ref)
    return models.valid_ref(ref) and models.has_key(ref)


def suggestions(e: dict, p: dict) -> dict:
    """这一家按现在选的地区推荐的型号 {"chat"?, "cheap"}：地区有自己的测试型号的（智谱、硅基流动的国内站），推荐的就是它（国际站的
    型号国内站不一定有）。"""
    rid = catalog.region_of(e, p.get("url"))
    reg = catalog.region(e, None if rid in (None, "custom") else rid)
    sug = dict(e.get("suggest") or {})
    if reg.get("test_model") and reg["test_model"] != e.get("test_model"):
        sug = {"cheap": reg["test_model"]}
    if not sug.get("cheap") and (reg.get("test_model") or e.get("test_model")):
        sug["cheap"] = reg.get("test_model") or e.get("test_model")
    return {k: v for k, v in sug.items() if v}


def _refs_in_config(pid: str) -> list[str]:
    """配置里写着的这一家的模型（engine.model、models、use、fallbacks）：没列过模型时也能选回现在用的那个。规范的写法（models.canonical）。"""
    c = models.cfg()
    vals: list = [c.get("model"), *(c.get("models") or []), *(c.get("fallbacks") or [])]
    use = c.get("use")
    vals += list(use.values()) if isinstance(use, dict) else []
    out: list[str] = []
    for v in vals:
        for r in (models.canonical(x) for x in (v if isinstance(v, list) else [v]) if isinstance(x, str)):
            if r.startswith(pid + "/") and r not in out:
                out.append(r)
    return out


async def connected(probe_local: bool = True) -> dict[str, dict]:
    """连上的每一家 → {"kind", "name", "models": [引用], "listed"（模型是不是列出来的）, "plan"}。只有这些能选。"""
    out: dict[str, dict] = {}
    for pid, e in catalog.CLOUD.items():
        p = effective(pid)
        if not p or not p.get("key"):
            continue
        c = listed_of(pid)
        cached = c.get("models") or []
        full = bool(cached) and c.get("listed", True)
        sug = suggestions(e, p)
        ids = cached if full else list(dict.fromkeys([*sug.values(), *picks(pid), *cached]))
        refs = list(dict.fromkeys([*(f"{pid}/{m}" for m in ids), *_refs_in_config(pid)]))
        out[pid] = {"kind": "cloud", "name": catalog.text(e["name"]), "models": refs, "listed": full, "plan": None, "suggest": sug}
    found = await detect_locals() if probe_local else (_local[1] if _local else {})
    for pid, e in catalog.LOCAL.items():
        f = found.get(pid) or {}
        if f.get("detected"):
            refs = list(dict.fromkeys([*(f"{pid}/{m}" for m in f.get("models") or []), *_refs_in_config(pid)]))
            out[pid] = {"kind": "local", "name": catalog.text(e["name"]), "models": refs, "listed": True, "plan": None}
    for pid in custom_ids():
        row = custom_row(pid)
        if row["connected"]:
            c = listed_of(pid)
            cached = c.get("models") or []
            refs = list(dict.fromkeys([*(f"{pid}/{m}" for m in cached), *_refs_in_config(pid)]))
            out[pid] = {"kind": "custom", "name": row["name"], "models": refs, "listed": bool(cached) and c.get("listed", True), "plan": None}
    for row in openclaw_rows():
        if row["connected"]:
            out[row["id"]] = {"kind": "openclaw", "name": row["id"], "models": _refs_in_config(row["id"]), "listed": False, "plan": None}
    for ref in models.subscription_models():
        sub = models.subscription(ref) or ""
        d = out.setdefault(sub, {"kind": "login", "name": catalog.text(catalog.LOGINS[sub]["name"]), "models": [], "listed": True, "plan": sub})
        if ref not in d["models"]:
            d["models"].append(ref)
    return out


def picks(pid: str) -> list[str]:
    """推荐里点了名的这一家的型号（catalog.RECOMMEND 的 prefer）：这一家连上了、还没列过模型时也放进能选的，推荐了就选得上。"""
    return list(dict.fromkeys(r.split("/", 1)[1] for x in catalog.RECOMMEND.values() for r in x.get("prefer") or () if r.startswith(pid + "/")))


def choices_of(conn: dict[str, dict]) -> list[dict]:
    rows = []
    for pid, d in conn.items():
        for ref in d["models"]:
            rows.append({"ref": ref, "provider": pid, "providerName": d["name"], "model": ref.split("/", 1)[1], "kind": d["kind"],
                         "plan": d["plan"], "listed": d["listed"]})
    return rows


def embedding_choices(conn: dict[str, dict]) -> list[dict]:
    """能选的向量：用的那一家连着（本机的探到了）。地区不对的（智谱的向量只有国内站有）不列。"""
    out = []
    for eid, e in catalog.EMBEDDINGS.items():
        pid = e["provider"]
        if pid not in conn:
            continue
        if e.get("region"):
            ent = catalog.entry(pid) or {}
            if catalog.region_of(ent, (effective(pid) or {}).get("url")) != e["region"]:
                continue
        out.append({"id": eid, "name": catalog.text(e["name"]), "provider": pid, "backend": e["backend"], "model": e.get("model"),
                    "dims": e.get("dims"), "local": bool(e.get("local")), "note": catalog.text(e.get("note")),
                    "models": [m.split("/", 1)[1] for m in conn[pid]["models"]] if e.get("local") else None})
    return out


def embed_url(e: dict) -> str:
    """向量的地址：本机的跟着 server.json 改过的端口；follow_url 的跟着那一家现在选的地区；别的照目录。"""
    if e.get("local"):
        return local_url(e["provider"])
    if e.get("follow_url"):
        return str((effective(e["provider"]) or {}).get("url") or e["url"]).rstrip("/")
    return str(e.get("url") or "").rstrip("/")


def embeddings_conf(eid: str, model: str | None = None, dims: int | None = None) -> dict:
    """目录里的一项 → engine.recall.embeddings 的写法（本机的地址跟着 server.json 改过的走）。"""
    e = catalog.EMBEDDINGS[eid]
    m = model or e.get("model")
    if not m:
        raise SetupError(400, L("这一家要你挑一个向量模型（model）", "Pick an embedding model (model) for this one"), field="model")
    conf: dict = {"backend": e["backend"], "model": m, "key_env": e.get("key_env", "")}
    if e["backend"] == "openai":
        conf["url"] = embed_url(e)
    d = dims if dims is not None else e.get("dims")
    if d:
        conf["dims"] = int(d)
    return conf


def current_embeddings() -> dict | None:
    """现在 engine.recall.embeddings 写的 → {"id"（目录里对得上的那一项，对不上 = custom）, "backend", "model", "dims"}；没开 → None。"""
    from . import embeddings as E
    s = E.spec()
    if s is None:
        return None
    eid = "custom"
    for k, e in catalog.EMBEDDINGS.items():
        url = embed_url(e) if e["backend"] == "openai" else E.BACKENDS["gemini"]["url"]
        if e["backend"] == s.backend and url.rstrip("/") == s.url.rstrip("/") and (e.get("model") in (None, s.model) or e.get("local")):
            eid = k
            break
    return {"id": eid, "backend": s.backend, "model": s.model, "dims": s.dims, "label": s.label}


# —— 推荐、只有一家时自动用它 ————————————————————————————————————————————————————————————

def _first_model(pid: str, d: dict, cheap: bool) -> str | None:
    """这一家推荐哪个：目录的 suggest（按地区，见 suggestions：cheap / chat），本机和自定义的挑第一个不是向量的，再不然第一个。"""
    sug = d.get("suggest") or {}
    want = sug.get("cheap" if cheap else "chat") or sug.get("chat" if cheap else "cheap")
    if want and (f"{pid}/{want}" in d["models"] or not d["listed"]):
        return f"{pid}/{want}"
    plain = [r for r in d["models"] if not re.search(r"embed|bge|e5-|gte-", r, re.I)]
    return (plain or d["models"] or [None])[0]


def _listed(pid: str, d: dict, model: str) -> str | None:
    """这一家选不选得到这个型号 → 引用；选不到 → None。"""
    ref = f"{pid}/{model}"
    return ref if ref in d["models"] or not d["listed"] else None


def recommend(purpose: str, conn: dict[str, dict]) -> dict | None:
    """一个用途推荐用哪个（只是建议：理由 + 代价，见 catalog.RECOMMEND）。"""
    why = catalog.RECOMMEND[purpose]
    pick = None
    if purpose == "embeddings":
        ch = embedding_choices(conn)
        best = next((c for c in ch if not c["local"]), None) or next((c for c in ch if c["model"]), None)
        return {"value": {"id": best["id"], "model": best["model"]}, "why": catalog.text(why["why"]), "cost": catalog.text(why["cost"])} if best else None
    if why.get("prefer"):  # 评测里好的那几个（名片 Agent、Doorman）：按顺序挑第一个连上、选得到的，不推荐套餐
        for ref in why["prefer"]:
            pid, model = ref.split("/", 1)
            d = conn.get(pid)
            if d and d["kind"] != "login" and _listed(pid, d, model):
                note = (why.get("models") or {}).get(ref) or why
                return {"value": ref, "why": catalog.text(note["why"]), "cost": catalog.text(note["cost"])}
    if purpose == "writing":  # 连着的哪一家目录里写了写作推荐的型号（suggest.writing）就推荐它，理由和代价有它自己的用它自己的
        for pid, d in conn.items():
            want = (d.get("suggest") or {}).get("writing")
            ref = _listed(pid, d, want) if want else None
            if ref:
                note = (why.get("models") or {}).get(f"{pid}/{want}") or why
                return {"value": ref, "why": catalog.text(note["why"]), "cost": catalog.text(note["cost"])}
    order = sorted(conn.items(), key=lambda kv: {"login": 0, "cloud": 1, "custom": 2, "openclaw": 3, "local": 4}[kv[1]["kind"]])
    if purpose not in ("chat", "writing"):  # 后台、外人：便宜的；外人回合不能花套餐（后台的也先挑不花套餐的）。写作照主对话挑（看文笔）
        order = [kv for kv in order if kv[1]["kind"] != "login"] + ([kv for kv in order if kv[1]["kind"] == "login"] if purpose == "background" else [])
    for pid, d in order:
        if d["kind"] == "login":
            pick = d["models"][0] if d["models"] else None
        else:
            pick = _first_model(pid, d, cheap=purpose not in ("chat", "writing"))
        if pick:
            break
    return {"value": pick, "why": catalog.text(why["why"]), "cost": catalog.text(why["cost"])} if pick else None


def auto_choice(conn: dict[str, dict]) -> dict | None:
    """只有一家连着、主对话还没选模型 → 用它（主对话用推荐的那个，外人回合用这一家不花套餐的）；否则 None。"""
    if models.default_model() or len(conn) != 1:
        return None
    pid, d = next(iter(conn.items()))
    chat = d["models"][0] if d["kind"] == "login" and d["models"] else _first_model(pid, d, cheap=False)
    if not chat:
        return None
    return {"provider": pid, "chat": chat, "outsider": None if d["kind"] == "login" else True}


def apply_auto(choice: dict) -> dict:
    """把 auto_choice 写进 server.json（claw 是 builtin 才写；主对话的模型已经有了就不动）。→ edit 的结果。"""
    def go(data: dict) -> None:
        eng = section(data, "engine")
        if eng.get("model"):
            return
        eng["model"] = choice["chat"]
        ms = eng.get("models")
        if isinstance(ms, list) and choice["chat"] not in [models._first(x) for x in ms]:  # noqa: SLF001
            eng["models"] = [choice["chat"], *ms]
        if choice.get("outsider"):
            use = section(eng, "use")
            for k in ("card", "doorman", "json"):
                use.setdefault(k, True)
    return edit(go)


def maybe_auto(conn: dict[str, dict]) -> dict | None:
    """存完一家以后：claw 是 builtin、只有这一家能用、主对话还没选 → 顺手用它。老实例不动。"""
    if not claw.is_builtin():
        return None
    choice = auto_choice(conn)
    if not choice:
        return None
    res = apply_auto(choice)
    return {**choice, "backup": res["backup"]} if res["changed"] else None


# —— 接口：总览 ——————————————————————————————————————————————————————————————————————

def no_model() -> bool:
    """还没有能用的模型（/api/health 的 no_model、总览的 noModel）：builtin 的主对话没选模型、选的那家没密钥 / 套餐没登录，而它的备用链
    （models.chain_after：它自己写的链，没写用 engine.fallbacks）上也没有一个能用的（主对话的模型暂时用不了、链上有能用的，出了错会换过去，
    不算「没有模型」）；OpenClaw 的这台电脑上没有 openclaw.json；别的 claw 没写地址。不连网，出错按 False（宁可不出引导，也别让连接探测失败）。
    刚起来那几秒 Claude Code 的登录还在后台查：models.subscription_ready 先当它在（runners.checking）。"""
    try:
        if claw.is_builtin():
            d = models.default_model()
            return not d or (not usable(d) and not any(usable(r) for r in models.chain_after(d)))
        if claw.is_openclaw():
            return not settings.openclaw_json.exists()
        return not claw.base_url()
    except Exception:  # noqa: BLE001
        return False


def _home(p: Path | str | None) -> str | None:
    """路径给人看：家目录写成 ~。"""
    if p is None:
        return None
    s, home = str(p), str(Path.home())
    return "~" + s[len(home):] if s == home or s.startswith(home + os.sep) else s


def _layer(fn) -> dict:
    """一层的状态：自己出错也不让整个总览出错（那一层写「读不到」）。"""
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        return {"status": "warn", "line": L(f"读不到这一层的状态（{type(e).__name__}）", f"Couldn't read this layer ({type(e).__name__})"), "missing": []}


def models_layer() -> dict:
    kind = claw.kind()
    if kind == "openclaw":
        found = settings.openclaw_json.exists()
        return {"status": "external" if found else "missing", "missing": [] if found else ["claw"],
                "line": L("主对话的模型由 OpenClaw 管（openclaw.json）", "OpenClaw picks the main chat's model (openclaw.json)") if found else
                L("这台电脑上没有 OpenClaw：重跑安装器选自带的内核（builtin），再选模型",
                  "OpenClaw isn't on this computer: rerun the installer, pick the built-in engine, then choose a model"),
                "outsider": {k: models.route(k) for k in ("card", "doorman")}}
    if kind == "openai":
        return {"status": "external" if claw.base_url() else "missing", "missing": [] if claw.base_url() else ["claw"],
                "line": L(f"模型由你的 claw（{claw.name()}）管", f"Your claw ({claw.name()}) picks the models"), "outsider": {k: models.route(k) for k in ("card", "doorman")}}
    d = models.default_model()
    ok = usable(d)
    missing = [] if ok else ["chat"] if not d else [f"key:{d.split('/', 1)[0]}"] if not models.subscription(d) else [f"login:{models.subscription(d)}"]
    line = (L(f"主对话用 {d}", f"The main chat uses {d}") if ok else L("还没选主对话的模型", "No model chosen for the main chat yet") if not d
            else L(f"主对话选了 {d}，但它现在用不了（没密钥或没登录）", f"The main chat is set to {d}, but it can't run now (no key or not signed in)"))
    return {"status": "ok" if ok else "missing", "missing": missing, "line": line, "chat": d or None,
            "background": models.background_model(), "outsider": {k: models.route(k) for k in ("card", "doorman")}}


def search_layer() -> dict:
    from . import web
    want = web._sub("search").get("provider")  # noqa: SLF001
    p = web.provider()
    off = want is False or str(want or "").strip().lower() in ("off", "none")
    if p:
        return {"status": "ok", "missing": [], "provider": p, "line": L(f"上网搜用 {catalog.text(catalog.SEARCH[p]['name'])}", f"Web search uses {catalog.text(catalog.SEARCH[p]['name'])}")}
    if off:
        return {"status": "off", "missing": [], "provider": None, "line": L("上网搜关着", "Web search is off")}
    return {"status": "optional", "missing": ["search"], "provider": None,
            "line": L("还没配搜索（web_fetch 照样能打开网址）", "No web search yet (web_fetch still opens links)")}


def tree_state() -> dict:
    """世界树：装没装、init 没、选中的是哪一份、存在哪、有多少条（档案要点不算）。"""
    import sources
    import treelib
    selected = sources.memory_tree
    out: dict = {"package": treelib.C is not None, "initialized": treelib.available(), "storage": None, "path": None, "memories": None,
                 "selected": None if selected is None else "package" if getattr(selected, "PACKAGE", False) else "script"}
    if out["selected"] == "script":
        out["path"] = _home(sources.tree_script())
        return out
    if not out["initialized"]:
        return out
    st = treelib.storage()
    out["storage"], out["path"] = st["kind"], _home(st["path"])
    out["memories"] = tree_count()
    return out


def tree_count() -> int | None:
    import sqlite3

    import treelib
    try:
        with contextlib.closing(treelib.connect(sync=False)) as conn:
            return int(conn.execute("SELECT COUNT(*) FROM tree WHERE source != 'profile'").fetchone()[0])
    except (sqlite3.Error, OSError, RuntimeError, ValueError, TypeError, AttributeError):
        return None


def memory_layer() -> dict:
    t = tree_state()
    if t["selected"] == "script":
        return {"status": "ok", "missing": [], "tree": t, "line": L("世界树用你自己的 memory_tree.py", "The memory tree uses your own memory_tree.py")}
    if not t["initialized"]:
        return {"status": "missing", "missing": ["tree"], "tree": t,
                "line": L("世界树还没建：选放在本机（SQLite）还是一个 Markdown 文件夹（可以是 Obsidian 库）",
                          "The memory tree isn't set up: keep it on this machine (SQLite) or in a Markdown folder (an Obsidian vault works)")}
    where = L("本机 SQLite", "SQLite on this machine") if t["storage"] == "sqlite" else L(f"Markdown 文件夹 {t['path']}", f"Markdown folder {t['path']}")
    n = t["memories"] or 0
    return {"status": "ok", "missing": [], "tree": t, "line": L(f"世界树在{where}，{n} 条", f"The memory tree is in {where}, {n} memor{'y' if n == 1 else 'ies'}")}


def embeddings_layer() -> dict:
    from . import embeddings as E
    cur = current_embeddings()
    prob = E.problem()
    if prob:
        return {"status": "warn", "missing": ["embeddings"], "line": prob}
    if cur is None:
        return {"status": "off", "missing": [], "line": L("按意思搜没开（只按关键词，什么都不发出去）", "Search by meaning is off (keywords only; nothing is sent out)")}
    s = E.spec()
    if s and s.key_env and not s.key():
        return {"status": "warn", "missing": [f"key:{s.key_env}"], "current": cur,
                "line": L(f"按意思搜选了 {cur['label']}，但没有密钥", f"Search by meaning uses {cur['label']}, but there's no key")}
    return {"status": "ok", "missing": [], "current": cur, "line": L(f"按意思搜用 {cur['label']}", f"Search by meaning uses {cur['label']}")}


def connections_layer() -> dict:
    from . import chatgpt_plan, claude_brain, mcp_client
    apps_on = 0
    with contextlib.suppress(Exception):
        import apps
        apps_on = sum(1 for a in apps.all_apps().values() if isinstance(a, dict) and a.get("status") == "connected")
    mcp = mcp_client.configs()
    skills = 0
    with contextlib.suppress(Exception):
        import bridges

        from . import tools
        skills = len(tools.list_skills("main", set(bridges.BRIDGES)))
    out = {"apps": apps_on, "mcp": len(mcp), "mcpErrors": sum(1 for c in mcp.values() if c.error), "skills": skills,
           "claudeCode": bool(claude_brain.helper()), "chatgpt": bool(chatgpt_plan.usable())}
    return {"status": "ok", "missing": [], **out,
            "line": L(f"连接器 {apps_on} 个，MCP 服务 {len(mcp)} 个，技能 {skills} 个", f"{apps_on} connectors, {len(mcp)} MCP servers, {skills} skills")}


def hands_layer() -> dict:
    from . import host_exec, host_files, sandbox, web
    files = bool(host_files.enabled("main") and host_files.root_of("main"))
    a = host_exec.cfg().get("agents")
    computer = sorted(str(k) for k, v in (a.items() if isinstance(a, dict) else []) if v is True or isinstance(v, dict))
    out = {"files": files, "webFetch": web.allowed("main", "fetch"), "webSearch": web.provider() is not None and web.allowed("main", "search"),
           "sandbox": sandbox.hands_on(), "hostCommands": computer}
    on = [L(n_zh, n_en) for k, n_zh, n_en in (("files", "文件", "files"), ("webFetch", "打开网页", "web pages"), ("webSearch", "搜索", "search"),
                                               ("sandbox", "沙箱", "sandbox")) if out[k]]
    if computer:
        on.append(L("主机命令", "host commands"))
    return {"status": "ok", "missing": [], **out, "line": L("、".join(on) or "都关着", ", ".join(on) or "all off")}


def config_facts() -> dict:
    """老实例照实反映：claw、有没有 engine 段、env_file、secrets.env 和它的权限。只读。"""
    sf = secretstore.path()
    m = secretstore.mode()
    ef = secretstore.env_file()
    return {"claw": claw.kind(), "engineSection": bool(models.cfg()), "openclawJson": settings.openclaw_json.exists(),
            "envFile": {"path": _home(ef), "exists": ef.exists()},
            "secretsFile": {"path": _home(sf), "exists": m is not None, "mode": f"{m:o}" if m is not None else None,
                            "tooOpen": bool(m is not None and m & 0o077)}}


@router.get("/api/setup")
async def overview():
    """总览。→ {"ok", "firstRun", "noModel", "needsSetup", "claw": {"kind", "name"}, "layers": {models, search, memory, embeddings,
    connections, hands: {"status": ok | missing | optional | off | external | warn, "line", "missing": […], …}}, "config": {…}}"""
    import data
    layers = {"models": _layer(models_layer), "search": _layer(search_layer), "memory": _layer(memory_layer),
              "embeddings": _layer(embeddings_layer), "connections": _layer(connections_layer), "hands": _layer(hands_layer)}
    nm = no_model()
    first = await asyncio.to_thread(data.first_run)
    return {"ok": True, "firstRun": first, "noModel": nm, "needsSetup": nm, "claw": {"kind": claw.kind(), "name": claw.name()},
            "layers": layers, "config": config_facts(),
            "steps": [{"id": "models", "done": layers["models"]["status"] in ("ok", "external")},
                      {"id": "search", "done": layers["search"]["status"] in ("ok", "off"), "optional": True},
                      {"id": "memory", "done": layers["memory"]["status"] == "ok"}]}


# —— 接口：模型服务 ————————————————————————————————————————————————————————————————————

@router.get("/api/setup/models")
async def list_providers(probe: int = 1, check: int = 0):
    """服务商。→ {"ok", "providers": [云端 / 本机 / 命令行登录 / 自定义 / OpenClaw 里配的，每一行见 *_row], "custom": {"hint", "apis"}}"""
    rows = await providers(bool(probe), bool(check))
    return {"ok": True, "providers": rows, "custom": {"hint": catalog.text(catalog.CUSTOM["hint"]), "apis": list(catalog.CUSTOM["apis"])}}


def row_of(pid: str) -> dict:
    """存完、删完回给 app 的那一行（不探本机）。"""
    if pid in catalog.CLOUD:
        return cloud_row(pid, catalog.CLOUD[pid])
    if pid in catalog.LOCAL:
        return local_row(pid, catalog.LOCAL[pid], (_local[1] if _local else {}).get(pid))
    return custom_row(pid)


@router.put("/api/setup/models/{pid}")
async def save_provider(pid: str, request: Request):
    """存一家。云端 {"key"?, "region"?, "url"?}；本机 {"url"?}（默认端口不用存）；目录里没有的 = 自定义的 OpenAI 兼容地址
    {"name"?, "url"（第一次要）, "api"?（openai / anthropic）, "key"?}。→ {"ok", "provider": 那一行, "saved": {"key", "config"}, "backup", "auto"}"""
    b = await body_of(request)
    if pid in catalog.LOGINS or pid in models.SUBSCRIPTIONS:
        raise SetupError(400, L("这是命令行登录：照它自己的那条路连（Claude Code 在服务器上登录，ChatGPT 用 Continue with ChatGPT）",
                                "This one is a sign-in: connect it its own way (sign in to Claude Code on the server, Continue with ChatGPT for ChatGPT)"))
    if not ID.match(pid):
        raise SetupError(400, L("名字只能用小写字母、数字和 . _ -", "The id may only use lowercase letters, digits and . _ -"), field="id")
    key, url = _key(b), _url(b)
    rid = _str(b, "region", 40)
    e = catalog.entry(pid)
    saved = {"key": False, "config": False}
    res = {"backup": None}
    if e is not None and pid in catalog.CLOUD:
        if rid:
            if rid not in (e.get("regions") or {}):
                raise SetupError(400, L("没有这个地区", "No such region"), field="region")
            url = url or catalog.region(e, rid)["url"]
        if url is not None:
            def go(data: dict, u: str = url) -> None:
                prov = section(data, "engine", "providers", pid)
                if u.rstrip("/") == e["url"].rstrip("/"):
                    prov.pop("url", None)
                else:
                    prov["url"] = u
                prune(data, "engine", "providers", pid)
            res = edit(go)
            saved["config"] = res["changed"]
        if key:
            secretstore.put(key_env(pid) or e["key_env"], key)
            saved["key"] = True
        if not key and url is None:
            raise SetupError(400, L("没有要存的（key、region 或 url）", "Nothing to save (key, region or url)"))
    elif e is not None:  # 本机
        if url is None and not key:
            return {"ok": True, "provider": row_of(pid), "saved": saved, "backup": None, "auto": None}

        def go(data: dict) -> None:
            prov = section(data, "engine", "providers", pid)
            if url is not None:
                if url == e["url"].rstrip("/"):
                    prov.pop("url", None)
                else:
                    prov["url"] = url
            if key:
                prov["key_env"] = f"{pid.upper()}_API_KEY"
            prune(data, "engine", "providers", pid)
        res = edit(go)
        saved["config"] = res["changed"]
        if key:
            secretstore.put(f"{pid.upper()}_API_KEY", key)
            saved["key"] = True
        forget_locals()
    else:  # 自定义的
        cur = over(pid)
        api = _str(b, "api", 40) or cur.get("api") or catalog.OPENAI
        if api not in catalog.CUSTOM["apis"]:
            raise SetupError(400, L("api 只能是 openai 或 anthropic", "api must be openai or anthropic"), field="api")
        name = _str(b, "name", 60)
        if not (url or cur.get("url")):
            raise SetupError(400, L("自定义的要写地址（url）", "A custom provider needs its address (url)"), field="url")
        env = re.sub(r"[^A-Z0-9]", "_", pid.upper()) + "_API_KEY"

        def go(data: dict) -> None:
            prov = section(data, "engine", "providers", pid)
            prov["api"] = api
            if url:
                prov["url"] = url
            if name:
                prov["name"] = name
            if key:
                prov["key_env"] = env
            elif "key_env" not in prov:
                prov["key_env"] = ""
        res = edit(go)
        saved["config"] = res["changed"]
        if key:
            secretstore.put(env, key)
            saved["key"] = True
        if not cached_count(pid)["count"]:  # 自定义的没有推荐的型号：存的时候顺手列一次（列不了也不碍事），只有它一家时才挑得出来用哪个
            with contextlib.suppress(SetupError):
                t, k = target(pid, {})
                listed, _err = await probe.list_models(t, k, timeout=8)
                if listed:
                    remember_models(pid, listed, t["url"])
    auto = maybe_auto(await connected(probe_local=pid in catalog.LOCAL)) if saved["key"] or saved["config"] else None
    return {"ok": True, "provider": row_of(pid), "saved": saved, "backup": res["backup"], "auto": auto}


def target(pid: str, b: dict) -> tuple[dict, str]:
    """测一下用的那一份（probe.test_model 要的 p）和密钥：请求里给了就用请求里的（还没存），没给用存下的。"""
    key, url, rid = _key(b), _url(b), _str(b, "region", 40)
    e = catalog.entry(pid)
    p = effective(pid) or {}
    if e is None and not p and not url:
        raise SetupError(404, L("没有这一家（自定义的先存地址，或者测的时候带上 url）", "No such provider (save a custom one first, or pass url)"))
    if e is not None and rid and rid not in (e.get("regions") or {}):
        raise SetupError(400, L("没有这个地区", "No such region"), field="region")
    base = url or (catalog.region(e, rid)["url"] if e and rid else None) or p.get("url") or (e or {}).get("url") or ""
    base = str(base).rstrip("/")
    api = p.get("api") or (e or {}).get("api") or _str(b, "api", 40) or catalog.OPENAI
    list_url = test_model = None
    if e is not None:
        eff = rid or catalog.region_of(e, base)
        reg = catalog.region(e, None if eff in (None, "custom") else eff)
        if str(reg.get("url") or "").rstrip("/") == base:  # 官方地址才用文档写的列模型地址；改成代理的照 <url>/models 列
            list_url = reg.get("list_url")
        test_model = reg.get("test_model") or e.get("test_model")
    needs = (e or {}).get("key_env", None) != "" if e else p.get("key_env", "x") != "" if p else bool(key)
    t = {"api": api, "url": base, "list": (e or {}).get("list") or ("anthropic" if api == catalog.ANTHROPIC else "openai"),
         "list_url": list_url, "strip": (e or {}).get("list_prefix"), "needs_key": needs and pid not in catalog.LOCAL, "test_model": test_model,
         "headers": p.get("headers") if isinstance(p.get("headers"), dict) else {}, "max_tokens_field": p.get("max_tokens_field")}
    return t, key or str(p.get("key") or "")


@router.post("/api/setup/models/{pid}/test")
async def test_provider(pid: str, request: Request):
    """测一下。→ {"ok", "via": list | request | detect | login, "models": [型号], "count", "listed", "ms", "error": {"kind", "message"} | null}
    （命令行登录：现查一遍；本机的：探一遍再列模型）。成功列出来的模型记下来，按用途选的时候能挑；列不了模型的那几家，带 model
    测通的那个型号也记下来。"""
    b = await body_of(request)
    if pid in ("claude-code", "codex"):
        from . import runners
        found = await asyncio.to_thread(runners.refresh)
        info = found.get(pid)
        ok = bool(info) and (pid != "claude-code" or runners.own_login(info))
        ms = models.claude_code_models() if pid == "claude-code" else []
        return {"ok": ok, "via": "login", "models": ms, "count": len(ms), "listed": True, "ms": 0,
                "error": None if ok else probe.error("auth", L("这台电脑上没装，或者还没登录", "Not installed on this computer, or not signed in"))}
    if pid == "chatgpt":
        from . import chatgpt_plan
        st = chatgpt_plan.status()
        return {"ok": bool(st["ready"]), "via": "login", "models": st["models"], "count": len(st["models"]), "listed": True, "ms": 0,
                "error": None if st["ready"] else probe.error("auth", L("ChatGPT 套餐没连上，或者要重连", "The ChatGPT plan isn't connected, or needs reconnecting"))}
    if pid in catalog.LOCAL:
        e = catalog.LOCAL[pid]
        url = _url(b) or local_url(pid)
        t0 = time.monotonic()
        f = await probe.detect_local(e, url, timeout=3.0)
        if f["detected"] and f["models"]:
            remember_models(pid, f["models"], url)
        return {"ok": f["detected"], "via": "detect", "models": f["models"], "count": len(f["models"]), "listed": True,
                "ms": int((time.monotonic() - t0) * 1000), "version": f.get("version"), "error": None if f["detected"] else probe.error("network", url)}
    t, key = target(pid, b)
    res = await probe.test_model(t, key, _str(b, "model", 200))
    if res["ok"] and res["models"]:
        if res["listed"]:
            remember_models(pid, res["models"], t["url"])
        else:  # 列不了模型的那几家：测通的这个型号记下来（同一个地址上以前测通的留着），按用途选的时候就能挑它
            old = model_cache().get(pid) or {}
            keep = (old.get("models") or []) if old.get("listed") is False and old.get("url") == t["url"] else []
            remember_models(pid, list(dict.fromkeys([*keep, *res["models"]])), t["url"], listed=False)
    return res


@router.delete("/api/setup/models/{pid}")
async def delete_provider(pid: str):
    """删掉：app 里存的密钥；自定义的连同 engine.providers.<id>；本机的去掉改过的地址。进程环境、env_file 里的这里删不了（409，说在哪）。
    → {"ok", "removed": {"key", "config"}, "affected": [还指着这一家的用途], "backup"}"""
    e = catalog.entry(pid)
    custom = e is None and pid in custom_ids()
    if e is None and not custom:
        raise SetupError(404, L("没有这一家", "No such provider"))
    removed = {"key": False, "config": False}
    res = {"backup": None}
    p = effective(pid) or over(pid)
    name = key_env(pid, p) if p.get("key_env", None) != "" or pid in catalog.CLOUD else ""
    if name:
        info = secretstore.info(name)
        if info["source"] in ("env", "env_file") and not secretstore.get(name):
            where = L("服务的进程环境", "the server's environment") if info["source"] == "env" else _home(secretstore.env_file())
            raise SetupError(409, L(f"这把密钥不是在 app 里存的（在 {where}），这里删不了", f"This key wasn't saved in the app (it's in {where}), so it can't be removed here"),
                             source=info["source"])
        removed["key"] = secretstore.delete(name)
    if custom or (e is not None and pid in catalog.LOCAL and over(pid)):
        def go(data: dict) -> None:
            prov = data.get("engine", {}).get("providers") if isinstance(data.get("engine"), dict) else None
            if isinstance(prov, dict):
                prov.pop(pid, None)
                prune(data, "engine", "providers")
        res = edit(go)
        removed["config"] = res["changed"]
        forget_models(pid)
    affected = [k for k, v in purposes_now().items() if isinstance(v, str) and v.startswith(pid + "/")]
    return {"ok": True, "removed": removed, "affected": affected, "backup": res["backup"]}


# —— 接口：按用途选模型 ————————————————————————————————————————————————————————————————

def _use(key: str):
    use = models.cfg().get("use")
    return use.get(key) if isinstance(use, dict) else None


def purposes_now() -> dict:
    """现在每个用途写的（给 affected 判断）：引用是规范的写法（models.canonical），true / 没写照原样。"""
    cur = current_embeddings()

    def one(v):
        return models._first(v) if isinstance(v, (list, str)) else v  # noqa: SLF001
    return {"chat": models.default_model() or None, "background": models._first(_use("background")) or None,  # noqa: SLF001
            "writing": models._first(_use("writing")) or None,  # noqa: SLF001
            "card": one(_use("card")), "doorman": one(_use("doorman")), "embeddings": cur["id"] if cur else None}


async def purposes_view(probe_local: bool = True) -> dict:
    conn = await connected(probe_local)
    builtin = claw.is_builtin()
    now = purposes_now()
    card, door = now["card"], now["doorman"]
    outsider = card if card == door else "mixed"
    picked = {"card": recommend("card", conn), "doorman": recommend("doorman", conn)}  # 外人回合的两件分开推荐（评测结果）
    rows = [
        {"id": "chat", "label": L("主对话（默认）", "Main chat (default)"), "applies": builtin, "accepts": {"plans": True},
         "current": now["chat"], "effective": now["chat"], "usable": usable(now["chat"]), "recommended": recommend("chat", conn)},
        {"id": "background", "label": L("后台和定时", "Background and scheduled"), "applies": builtin, "accepts": {"plans": True},
         "current": now["background"], "effective": models.background_model() or now["chat"], "follows": "chat" if not now["background"] else None,
         "recommended": recommend("background", conn)},
        {"id": "writing", "label": L("写作（长文、文书）", "Writing (long pieces, documents)"), "applies": builtin, "accepts": {"plans": True},
         "current": now["writing"], "effective": models.route("writing", kind="owner", origin="user") or now["chat"],
         "follows": "chat" if not now["writing"] else None, "recommended": recommend("writing", conn)},
        {"id": "outsider", "label": L("外人回合（名片 Agent、Doorman）", "Other people's turns (card agent, Doorman)"), "applies": True,
         "accepts": {"plans": False}, "current": outsider if outsider is not True else "default",
         "parts": {"card": {"value": "default" if card is True else card, "effective": models.route("card"), "recommended": picked["card"]},
                   "doorman": {"value": "default" if door is True else door, "effective": models.route("doorman"), "recommended": picked["doorman"]}},
         "recommended": picked["card"]},
        {"id": "embeddings", "label": L("向量（按意思搜记忆）", "Embeddings (search memory by meaning)"), "applies": builtin,
         "current": current_embeddings(), "recommended": recommend("embeddings", conn)},
    ]
    if not builtin:
        why = L("主对话、后台、写作和向量由你的 claw 管；这里只能选外人回合用的模型",
                "Your claw handles the main chat, background work, writing and memory; only other people's turns can be chosen here")
        for r in rows:
            if not r["applies"]:
                r["why"] = why
    return {"ok": True, "claw": claw.kind(), "purposes": rows, "choices": {"models": choices_of(conn), "embeddings": embedding_choices(conn)},
            "providers": {pid: {"kind": d["kind"], "name": d["name"], "listed": d["listed"]} for pid, d in conn.items()},
            "auto": auto_choice(conn) if builtin else None}


@router.get("/api/setup/purposes")
async def get_purposes(probe: int = 1):
    """按用途选模型。→ {"ok", "claw", "purposes": [chat、background、writing、outsider、embeddings 各一行 {"id", "label", "applies", "accepts",
    "current", "effective", "follows"?, "recommended": {"value", "why", "cost"} | null, …}]（outsider 那一行的 parts.card / parts.doorman
    各有自己的 recommended，评测结果；这一行的 recommended 照名片 Agent 的）, "choices": {"models": [{"ref", "provider", "providerName", "model", "kind", "plan", "listed"}],
    "embeddings": [{"id", "name", "provider", "model", "dims", "local", "models"?}]}, "providers", "auto": {"provider", "chat", "outsider"} | null}"""
    return await purposes_view(bool(probe))


def _need_ref(v, allowed: set[str], plans: bool, field: str) -> str:
    if not isinstance(v, str) or not v.strip():
        raise SetupError(400, L(f"{field} 要写成 provider/model", f"{field} must look like provider/model"), field=field)
    ref = models.canonical(v.strip())  # google/models/… 也认，写进去的是 google/…
    if ref not in allowed:
        raise SetupError(400, L(f"{ref} 不在能选的里面（只列连上的；先连上那一家，或者测一下把模型列出来）",
                                f"{ref} isn't one of the choices (only connected providers are listed; connect it first, or test it to list its models)"),
                         field=field)
    if not plans and models.subscription(ref):
        raise SetupError(400, L("外人回合不能花你自己的套餐，换一个按量付费或本机的模型", "Other people's turns can't use your own plan; pick a pay-as-you-go or local model"),
                         field=field)
    return ref


@router.put("/api/setup/purposes")
async def put_purposes(request: Request):
    """{"chat"?: 引用, "background"?: 引用 | "default" | null（都是跟主对话）, "writing"?: 引用 | "default" | null（都是跟主对话；能选套餐，
    用的时候照 may_spend）, "outsider"?: 引用 | "default" | null（不走内核）, "card"? / "doorman"?（单独改）,
    "embeddings"?: {"id", "model"?, "dims"?} | "off" | null, "models"?: [app 里能切换的引用]}。只改给了的。→ 新的 purposes_view + backup"""
    b = await body_of(request)
    conn = await connected()
    allowed = {r["ref"] for r in choices_of(conn)} | {v for v in purposes_now().values() if isinstance(v, str)}
    builtin = claw.is_builtin()
    for k in ("chat", "background", "writing", "embeddings", "models"):
        if k in b and not builtin:
            raise SetupError(409, L("主对话、后台、写作和向量由你的 claw 管（OpenClaw / 别的 claw），这里不改",
                                    "Your claw handles the main chat, background work, writing and memory; they aren't changed here"), field=k)
    plan: list[Callable[[dict], None]] = []
    if "chat" in b:
        ref = _need_ref(b["chat"], allowed, True, "chat")

        def chat(data: dict, r: str = ref) -> None:
            eng = section(data, "engine")
            eng["model"] = r
            ms = eng.get("models")
            if isinstance(ms, list) and r not in [models._first(x) for x in ms]:  # noqa: SLF001
                eng["models"] = [r, *ms]
        plan.append(chat)
    if "models" in b:
        if not isinstance(b["models"], list) or not b["models"]:
            raise SetupError(400, L("models 要是一个引用的列表", "models must be a list of refs"), field="models")
        refs = list(dict.fromkeys(_need_ref(x, allowed, True, "models") for x in b["models"]))

        def switcher(data: dict) -> None:
            eng = section(data, "engine")
            d = models._first(eng.get("model"))  # noqa: SLF001
            eng["models"] = refs if not d or d in refs else [d, *refs]
        plan.append(switcher)
    if "background" in b:
        v = b["background"]
        ref = None if v in (None, "default") else _need_ref(v, allowed, True, "background")

        def bg(data: dict, r: str | None = ref) -> None:
            use = section(data, "engine", "use")
            if r is None:
                old = use.pop("background", None)
                if old is not None and use.get("json") == old:
                    use["json"] = True  # 一次性 JSON 是跟着后台一起改的：一起回到默认模型
            else:
                use["background"] = r
                use["json"] = r  # 一次性 JSON（llmjson：会议纪要、学习页、朋友画像……）也是没人在看的后台活
            prune(data, "engine", "use")
        plan.append(bg)
    if "writing" in b:
        v = b["writing"]
        ref = None if v in (None, "default") else _need_ref(v, allowed, True, "writing")

        def wr(data: dict, r: str | None = ref) -> None:
            use = section(data, "engine", "use")
            if r is None:
                use.pop("writing", None)
            else:
                use["writing"] = r
            prune(data, "engine", "use")
        plan.append(wr)
    parts = {"card": b["outsider"], "doorman": b["outsider"]} if "outsider" in b else {}
    parts.update({k: b[k] for k in ("card", "doorman") if k in b})  # 单独改的盖过 outsider
    for part, v in parts.items():
        val = None if v in (None, "off") else True if v == "default" else _need_ref(v, allowed, False, part if part in b else "outsider")

        def out(data: dict, k: str = part, x=val) -> None:
            use = section(data, "engine", "use")
            if x is None:
                use.pop(k, None)
            else:
                use[k] = x
            prune(data, "engine", "use")
        plan.append(out)
    if "embeddings" in b:
        v = b["embeddings"]
        if v in (None, "off", False):
            conf = None
        else:
            if not isinstance(v, dict) or v.get("id") not in catalog.EMBEDDINGS:
                raise SetupError(400, L("embeddings 要写 {\"id\": 目录里的一项}，或者 \"off\"", "embeddings must be {\"id\": a catalog entry} or \"off\""), field="embeddings")
            if v["id"] not in {c["id"] for c in embedding_choices(conn)}:
                raise SetupError(400, L("这一家还没连上", "That provider isn't connected yet"), field="embeddings")
            dims = v.get("dims")
            if dims is not None and (not isinstance(dims, int) or isinstance(dims, bool) or not 1 <= dims <= 65536):
                raise SetupError(400, L("dims 要是一个正整数", "dims must be a positive integer"), field="dims")
            model = v.get("model")
            if model is not None and (not isinstance(model, str) or not MODEL.match(model)):
                raise SetupError(400, L("model 写得不对", "model isn't valid"), field="model")
            conf = embeddings_conf(v["id"], model, dims)

        def emb(data: dict, c: dict | None = conf) -> None:
            rec = section(data, "engine", "recall")
            if c is None:
                rec.pop("embeddings", None)
            else:
                rec["embeddings"] = c
            prune(data, "engine", "recall")
        plan.append(emb)
    if not plan:
        raise SetupError(400, L("没有要改的", "Nothing to change"))

    def go(data: dict) -> None:
        for fn in plan:
            fn(data)
    res = edit(go)
    out_ = await purposes_view(probe_local=False)
    return {**out_, "backup": res["backup"], "changed": res["changed"]}


# —— 接口：搜索 ——————————————————————————————————————————————————————————————————————

def search_rows() -> list[dict]:
    from . import web
    rows = []
    for sid, s in catalog.SEARCH.items():
        info = secretstore.info(s["key_env"]) if s.get("key_env") else None
        rows.append({"id": sid, "name": catalog.text(s["name"]), "keyless": bool(s.get("keyless")), "selfHosted": bool(s.get("self_hosted")),
                     "unofficial": bool(s.get("unofficial")), "keyEnv": s.get("key_env"), "key": info,
                     "url": web.url_of(sid) or None if s.get("self_hosted") else None, "connected": web.usable(sid),
                     "keyUrl": s.get("key_url"), "docs": s.get("docs"), "free": catalog.text(s.get("free")), "note": catalog.text(s.get("note"))})
    return rows


def search_view() -> dict:
    from . import web
    want = web._sub("search").get("provider")  # noqa: SLF001
    off = want is False or str(want or "").strip().lower() in ("off", "none")
    return {"ok": True, "enabled": web.provider() is not None, "provider": web.provider(), "chosen": None if off else (str(want).strip().lower() if want else None),
            "off": off, "auto": not want and not off, "providers": search_rows()}


@router.get("/api/setup/search")
async def get_search():
    """搜索。→ {"ok", "enabled", "provider"（现在真用的那家）, "chosen"（写死的那家，null = 按顺序挑）, "off", "auto", "providers": [{"id", "name",
    "keyless", "selfHosted", "unofficial", "keyEnv", "key", "url", "connected", "keyUrl", "docs", "free", "note"}]}"""
    return search_view()


@router.put("/api/setup/search")
async def put_search(request: Request):
    """{"enabled": false} = 关；{"enabled": true, "provider": "brave"} = 开、用它；{"enabled": true, "provider": null} = 按顺序挑有密钥的。"""
    from . import web
    b = await body_of(request)
    enabled, prov = b.get("enabled", True), b.get("provider", ...)
    if not isinstance(enabled, bool):
        raise SetupError(400, L("enabled 要是 true / false", "enabled must be true or false"), field="enabled")
    if prov is not ... and prov is not None:
        if prov not in catalog.SEARCH:
            raise SetupError(400, L("没有这一家", "No such provider"), field="provider")
        if enabled and not web.usable(prov):
            raise SetupError(409, L("这一家还没连上（先存密钥或地址）", "That provider isn't connected yet (save its key or address first)"), field="provider")

    def go(data: dict) -> None:
        s = section(data, "engine", "web", "search")
        if not enabled:
            s["provider"] = "off"
        elif prov is None or prov is ...:
            if prov is None or str(s.get("provider") or "").lower() in ("off", "none") or s.get("provider") is False:
                s.pop("provider", None)
        else:
            s["provider"] = prov
        prune(data, "engine", "web", "search")
    res = edit(go)
    if enabled and web.provider() is None:
        return {**search_view(), "backup": res["backup"], "warning": L("开了，但还没有一家连上：先存一把密钥", "On, but no provider is connected yet: save a key first")}
    return {**search_view(), "backup": res["backup"]}


@router.put("/api/setup/search/{sid}")
async def save_search(sid: str, request: Request):
    """{"key"}（SearXNG 是 {"url"}）。存了密钥、搜索又没写死哪一家，就按顺序用上了（只有一家时自动用它）。"""
    if sid not in catalog.SEARCH:
        raise SetupError(404, L("没有这一家", "No such provider"))
    s = catalog.SEARCH[sid]
    b = await body_of(request)
    res = {"backup": None}
    if s.get("self_hosted"):
        url = _url(b)
        if not url:
            raise SetupError(400, L("要写你的 SearXNG 地址（url）", "Enter your SearXNG address (url)"), field="url")

        def go(data: dict) -> None:
            section(data, "engine", "web", "search", "providers", sid)["url"] = url
        res = edit(go)
    elif s.get("keyless"):
        raise SetupError(400, L("这一家不要密钥：直接在开关里选它", "This one needs no key: just pick it"))
    else:
        key = _key(b)
        if not key:
            raise SetupError(400, L("要写密钥（key）", "Enter the key (key)"), field="key")
        secretstore.put(s["key_env"], key)
    return {**search_view(), "backup": res["backup"]}


@router.post("/api/setup/search/{sid}/test")
async def test_search(sid: str, request: Request):
    """搜一句「OpenMousse」。→ {"ok", "results", "ms", "error"}"""
    if sid not in catalog.SEARCH:
        raise SetupError(404, L("没有这一家", "No such provider"))
    b = await body_of(request)
    return await probe.test_search(sid, _key(b), _url(b))


@router.delete("/api/setup/search/{sid}")
async def delete_search(sid: str):
    if sid not in catalog.SEARCH:
        raise SetupError(404, L("没有这一家", "No such provider"))
    s = catalog.SEARCH[sid]
    res = {"backup": None}
    removed = False
    if s.get("key_env"):
        info = secretstore.info(s["key_env"])
        if info["source"] in ("env", "env_file") and not secretstore.get(s["key_env"]):
            raise SetupError(409, L("这把密钥不是在 app 里存的，这里删不了", "This key wasn't saved in the app, so it can't be removed here"), source=info["source"])
        removed = secretstore.delete(s["key_env"])
    if s.get("self_hosted"):
        def go(data: dict) -> None:
            p = section(data, "engine", "web", "search", "providers")
            p.pop(sid, None)
            prune(data, "engine", "web", "search", "providers")
        res = edit(go)
        removed = removed or res["changed"]
    return {**search_view(), "removed": removed, "backup": res["backup"]}


# —— 接口：记忆 ——————————————————————————————————————————————————————————————————————

def embeddings_state() -> dict:
    from . import semantic
    try:
        st = semantic.status()
    except Exception as e:  # noqa: BLE001
        return {"enabled": False, "error": type(e).__name__}
    keep = ("enabled", "problem", "backend", "model", "dims", "key", "keyEnv", "built", "vectors", "pending", "embeddedAt", "lastError", "stale")
    return {k: st.get(k) for k in keep if k in st}


@router.get("/api/setup/memory")
async def get_memory():
    """记忆。→ {"ok", "tree": {"package", "initialized", "selected": package | script | null, "storage": sqlite | markdown | null, "path",
    "memories", "canChoose", "why"?}, "embeddings": {…状态}, "current": 现在选的向量 | null}"""
    t = await asyncio.to_thread(tree_state)
    can, why = choosable(t)
    return {"ok": True, "tree": {**t, "canChoose": can, "why": why}, "embeddings": embeddings_state(), "current": current_embeddings()}


def choosable(t: dict) -> tuple[bool, str | None]:
    if t["selected"] == "script":
        return False, L("用的是你自己的 memory_tree.py", "Your own memory_tree.py is in use")
    if not t["package"]:
        return False, L("世界树的包没装（pip install ./tree）", "The memory tree package isn't installed (pip install ./tree)")
    if t["initialized"] and t["memories"]:
        return False, L("已经在用，换存法要迁移（以后做）", "Already in use; switching storage needs a migration (later)")
    return True, None


def _notes_dir(raw_: str) -> Path:
    p = Path(raw_).expanduser()
    if not p.is_absolute():
        raise SetupError(400, L("文件夹要写完整的路径（/… 或 ~/…）", "The folder needs a full path (/… or ~/…)"), field="path")
    p = Path(os.path.abspath(p))
    home = Path.home()
    if p == Path(p.anchor) or p == home or home.is_relative_to(p):
        raise SetupError(400, L("不能直接用根目录或家目录：写里面的一个文件夹", "Not the root or your home folder itself: use a folder inside it"), field="path")
    if not p.parent.is_dir():
        raise SetupError(400, L(f"{_home(p.parent)} 不存在", f"{_home(p.parent)} doesn't exist"), field="path")
    if p.exists() and not p.is_dir():
        raise SetupError(400, L("那不是一个文件夹", "That isn't a folder"), field="path")
    return p


def init_tree(storage: str, notes: Path | None) -> None:
    """建世界树（第一次）或者换存法（还没有记忆的时候）：和 mousse-tree init 同一套（openmousse_tree.cli）。在线程里跑。"""
    import argparse

    import treelib
    from openmousse_tree import cli
    C, S = treelib.C, cli.S
    first = not C.CONFIG.is_file()
    switch = None
    if not first and storage == "sqlite" and C.markdown():
        switch = {"storage": "sqlite"}  # Markdown → SQLite（没有记忆）：init 不肯（要 migrate），直接改存法
    elif not first and storage == "markdown" and C.markdown() and notes is not None and Path(C.notes_dir()).resolve() != notes.resolve():
        switch = {}  # 换一个笔记文件夹（没有记忆）
    if switch is not None:
        cfg = {**C.load(), **switch}
        if storage == "markdown":
            cli._set_markdown(cfg, str(notes), None)  # noqa: SLF001
        C.save(cfg)
        conn = S.connect()
        S.sync_profile(conn)
        S.export(conn)
        return
    # 第一次建（或者 SQLite → Markdown，init 自己会查有没有记忆）：和 mousse-tree init 同一条路。建过的不改它的语言、档案位置、时区
    a = argparse.Namespace(lang=settings.language if first else None, name=None, profile=str(settings.profile) if first else None,
                           tz=settings.timezone if first else None, host=None, storage=storage, notes=str(notes) if notes else None, profile_note=None)
    try:
        cli.cmd_init(a)
    except SystemExit as e:
        raise SetupError(409, str(e.code) if isinstance(e.code, str) else L("世界树没建成", "The memory tree couldn't be set up")) from None


def attach_tree() -> None:
    """刚建好的世界树：这个进程里就接上（sources 在启动时选的，不用重启）。"""
    import sources
    import treelib
    if sources.memory_tree is None and treelib.available():
        sources.memory_tree = treelib
        sources.AVAILABLE["tree"] = True


@router.put("/api/setup/memory")
async def put_memory(request: Request):
    """{"storage": "sqlite"} / {"storage": "markdown", "path": "~/vault/Memory"}（先只支持第一次选：已经有记忆、要换存法的回 409
    {"migrate": true}）和 / 或 {"embeddings": 同 PUT /api/setup/purposes}。→ GET /api/setup/memory 的样子 + backup"""
    b = await body_of(request)
    if "storage" not in b and "embeddings" not in b:
        raise SetupError(400, L("没有要改的（storage 或 embeddings）", "Nothing to change (storage or embeddings)"))
    backup_name = None
    if "embeddings" in b:
        if not claw.is_builtin():
            raise SetupError(409, L("按意思搜是自带内核的记忆检索：你的 claw 有它自己的", "Search by meaning belongs to the built-in engine; your claw has its own"), field="embeddings")
        sub = await put_purposes_body({"embeddings": b["embeddings"]})
        backup_name = sub["backup"]
    if "storage" in b:
        storage = b["storage"]
        if storage not in ("sqlite", "markdown"):
            raise SetupError(400, L("storage 只能是 sqlite 或 markdown", "storage must be sqlite or markdown"), field="storage")
        notes = _notes_dir(_str(b, "path", 400) or "") if storage == "markdown" else None
        if storage == "markdown" and notes is None:
            raise SetupError(400, L("Markdown 要写文件夹（path）", "Markdown needs a folder (path)"), field="path")
        t = await asyncio.to_thread(tree_state)
        can, why = choosable(t)
        same = t["initialized"] and t["storage"] == storage and (storage == "sqlite" or (t["path"] and Path(os.path.expanduser(t["path"])).resolve() == notes.resolve()))
        if not same:
            if not can:
                raise SetupError(409, why or "", migrate=bool(t["initialized"] and t["memories"]), storage=t["storage"])
            await asyncio.to_thread(init_tree, storage, notes)
        attach_tree()
    out = await get_memory()
    return {**out, "backup": backup_name}


class Body:
    """不经 HTTP 调这些接口（命令行的三步引导 setup_ctl.py、记忆页存向量）：只有 body()。主人的设备那一关是 HTTP 才有的，命令行
    本来就在服务器上、由能读 server.json 的人跑。"""

    def __init__(self, data: dict | None = None) -> None:
        self._raw = json.dumps(data or {}).encode()

    async def body(self) -> bytes:
        return self._raw


async def put_purposes_body(b: dict) -> dict:
    """PUT /api/setup/purposes 的那一套，给别的接口用（记忆页存向量）。"""
    return await put_purposes(Body(b))  # type: ignore[arg-type]


@router.post("/api/setup/embeddings/test")
async def test_embeddings(request: Request):
    """{"id", "model"?, "dims"?}：用存下的密钥算一个向量。→ {"ok", "dims", "ms", "error"}"""
    b = await body_of(request)
    eid = b.get("id")
    if eid not in catalog.EMBEDDINGS:
        raise SetupError(400, L("id 要是目录里的一项", "id must be a catalog entry"), field="id")
    model = _str(b, "model", 200)
    dims = b.get("dims")
    if dims is not None and (not isinstance(dims, int) or isinstance(dims, bool) or not 1 <= dims <= 65536):
        raise SetupError(400, L("dims 要是一个正整数", "dims must be a positive integer"), field="dims")
    return await probe.test_embeddings(embeddings_conf(eid, model, dims))


# —— 接口：用量 ——————————————————————————————————————————————————————————————————————

@router.get("/api/setup/usage")
@router.get("/api/engine/usage")
async def get_usage(days: int = 30):
    """用量（engine/usage.py）。→ {"ok", "since", "until", "days", "currency", "total", "byDay", "byModel", "byPurpose", "embeddings": {"total",
    "byDay", "byModel", "byKind", "recordedSince"}, "notCounted"}"""
    return {"ok": True, **await asyncio.to_thread(usage_mod.summary, days)}
