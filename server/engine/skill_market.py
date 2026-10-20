"""技能市场：搜和装（第 5 批第 3 项，2026-10-19；对照表 6.7、6.8）。只做「搜」「装」「更新」「卸载」，不做「发布」。

来源（engine.skills.market.sources 挑搜哪几家，默认这四家都搜；装的时候按标识认是哪一家）：
  clawhub     ClawHub（照 OpenClaw 自己的客户端 src/infra/clawhub-*.ts 和 ClawHub 公开的 HTTP 接口，地址 engine.skills.market.clawhub，
              默认 https://clawhub.ai）：搜 GET /api/v1/search?q=&limit=；详情 GET /api/v1/skills/<slug>?ownerHandle=；装之前查它的信任结论
              POST /api/v1/skills/-/security-verdicts（拦下的、标成恶意的不装；查不到也不装：同 OpenClaw 的 fail closed）；
              下载 GET /api/v1/download?slug=&ownerHandle=&version=（zip；GitHub 托管的回一段 JSON，改从它说的那一次 GitHub 提交下）。
              标识 clawhub:@作者/slug（也认 @作者/slug；只写 slug 的要 ClawHub 认得出是谁的）
  github      github:owner/repo[/路径][@ref]：ref（分支、标签；不写 = 默认分支）先换成 40 位提交号再下（钉住，lock 里记提交号），
              GET /repos/…/git/trees/<提交号>?recursive=1 找出那个文件夹，文件从 raw 地址（engine.skills.market.github_raw）一个个下。
              搜：engine.skills.market.repos 列的几个仓库（默认 anthropics/skills）按文件夹名对；配了 GITHUB_TOKEN（或 GH_TOKEN）再加 GitHub
              的代码搜索（filename:SKILL.md）。不配令牌一小时只能问 60 次（GitHub 的规矩）
  skills.sh   GET <地址>/api/search?q=&limit=（engine.skills.market.skills_sh，默认 https://skills.sh）只用来搜；装的时候从它指的 GitHub 仓库下、
              钉到提交号（同 Hermes：skills.sh 自己不托管文件）。标识 skills.sh:owner/repo/skill
  well-known  照 agentskills.io 的格式发布技能的网站：<网站>/.well-known/agent-skills/index.json（v0.2：每份有 url 和 digest，摘要要对上；
              type 是 skill-md 或 archive（zip / tar.gz））和 /.well-known/skills/index.json（v0.1：files 列表）。engine.skills.market.sites
              列的网站会被搜；别的网站给地址也能装（只去公网，engine/web.py）。标识 well-known:https://网站#名字
  url         直接给 SKILL.md 的链接（url:https://…/SKILL.md，也认直接贴的链接）：只下这一个文件（只去公网）
  agentskills.io：开工时查过（2026-10-19）：它是 SKILL.md 的格式规范网站，没有公开的目录、索引或搜索接口（/api、/skills、/registry 都是 404）；
              它自己的 /.well-known/ 下只有一份讲它这套文档的技能。所以落成「照这个格式发布的网站都认」（上面的 well-known），不单独接一家。
搜（search，只读）：几家一起问，每家最多 engine.skills.market.timeout 秒（默认 8），出错、超时的跳过、在 failed 里说一句；合并、去重（同一个
  GitHub 文件夹从几家来的算一份）、按名字和说明对上这句话的程度排（同分按来源的先后）。
装（照 Hermes 的 skills_hub_install 和 OpenClaw 的 clawhub 安装的做法）：
  1. 下到内存（zip 最多 ARCHIVE_MAX；文件最多 FILES_MAX 个、一个最多 FILE_MAX、加起来最多 TOTAL_MAX），拒绝：往外跑的路径（zip-slip：绝对路径、..、
     反斜杠、盘符）、符号链接和硬链接、可执行的二进制（ELF / PE / Mach-O 的文件头，.exe / .so / .dll 这类后缀）。整份拒，不挑着装
  2. 放进隔离区 <data_dir>/skills-market/quarantine/<编号>/<名字>/（文件一律 0644，不留执行位），用 engine/skills_guard.py（照搬 Hermes 的
     skills_guard）扫：外传数据、提示注入、破坏性命令、改 Agent 配置、隐形 Unicode……结论 safe / caution / dangerous。市场来的（ClawHub、
     GitHub、skills.sh、网站、链接）一律按社区、不受信扫（2026 年 2 月 ClawHub 上出过 341 个恶意技能）
  3. dangerous 不装（不出卡，命令行也不装）；ClawHub 拦下的、标成恶意的不装。别的出一张卡（kind tool，engine/calls.py 的前缀 market）：来源、版本或
     提交号、扫描结论和原因、SKILL.md 开头一段、装到哪；主人点了才装：照卡上的指纹核对隔离区里那一份（被改过就不装），再扫一遍（dangerous 照样
     不装），放进用户级的技能目录（<data_dir>/skills，所有 Agent 共用）或者指定 Agent 的工作区（skills/），同名的已经有了不盖（市场装的请用更新）
  4. 记进 <data_dir>/skills-market/lock.json：来源、标识、版本或提交号、内容指纹、扫描结论、装的时间（照 Hermes 的 lock.json）
更新：照 lock 里的标识再下一次（GitHub 的照原来的 ref 换成现在的提交号），和 lock 的指纹比：一样 = 已经最新；本地改过（现在的文件对不上 lock 的指纹）
  的跳过、写明原因；不一样的照样隔离、扫描、出卡（命令行直接装），换之前先留一版（engine/versions.py，和 skill_manage 存的同一种）。
卸载：先留一版（同上，键 skills/<名字>，存在那个技能目录所在的根下面的 .mousse-versions/），再拿掉，lock 里去掉。
入口：
  Agent 的 skill_market 工具（engine.skills.market.enabled = true 才有，默认关：会联网）：search / list 只读；install / update / remove 交卡。
    说明里照 OpenClaw 自带的 clawhub 技能：用户要的事现有的技能、工具都做不了，说「做不到」之前先搜一下；搜到了不等于能装：用户说要装才装。
  设置接口（只认主人的设备，给 app 用；界面不在这一批）：GET /api/setup/skills/market/search?q=、POST …/market/preview {"id", "agent"?}（下载、隔离、
    扫描，回结论和一个 staged）、POST …/market/install {"staged"}（主人确认 = 装）、GET …/market/installed、POST …/market/update {"name", "agent"?}
    （→ staged，再 install）、DELETE …/market/installed/{名字}?agent=
  命令行 mousse skills search | install | update | list | remove（../skills_ctl.py：主人在自己机器上跑，直接装，但照样扫描；caution 要 --yes，
    dangerous 照样不装）
配置：server.json 的 engine.skills.market（都可以不写）：
  {"enabled": false, "sources": ["clawhub", "skills.sh", "github", "well-known"], "clawhub": "https://clawhub.ai",
   "github_api": "https://api.github.com", "github_raw": "https://raw.githubusercontent.com", "skills_sh": "https://skills.sh",
   "repos": ["anthropics/skills"], "sites": [], "timeout": 8}
  地址写错了、连不上：那一家这次搜不到，不影响别家。
出处：ClawHub 信任结论的判法（_verdict）照 OpenClaw 2d9033c6 的 src/infra/clawhub-install-trust.ts，工具说明照它的 skills/clawhub/SKILL.md；
  内容指纹（digest）照 Hermes cc6b1f29 的 tools/skills_guard.py（_content_digest），lock.json 照它的 tools/skills_hub.py（HubLockFile）。
  都是 MIT，许可全文见仓库根目录的 THIRD_PARTY_NOTICES。
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import tarfile
import time
import uuid
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote, urlencode, urljoin, urlsplit

from fastapi import APIRouter, Depends, Request

from config import raw, settings
from i18n import L

from . import calls, setup_api
from .loop import ToolCtx, ToolFailure
from .setup_api import SetupError

SOURCES = ("clawhub", "skills.sh", "github", "well-known")
DEFAULTS = {"enabled": False, "sources": list(SOURCES), "clawhub": "https://clawhub.ai", "github_api": "https://api.github.com",
            "github_raw": "https://raw.githubusercontent.com", "skills_sh": "https://skills.sh", "repos": ["anthropics/skills"], "sites": [],
            "timeout": 8}
TOOL = "skill_market"
PREFIX = "market"          # engine_calls.tool = market:<名字>（engine/calls.py）
ARCHIVE_MAX = 25 * 1024 * 1024
FILES_MAX = 100
FILE_MAX = 1024 * 1024
TOTAL_MAX = 10 * 1024 * 1024
INDEX_MAX = 2 * 1024 * 1024
SEARCH_LIMIT = 20
STAGED_DAYS = 7
SHA = re.compile(r"^[0-9a-f]{40}$")
SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
GH_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
SKILL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
BINARY_EXT = {".exe", ".dll", ".so", ".dylib", ".bin", ".com", ".msi", ".dmg", ".app", ".deb", ".rpm", ".jar", ".class", ".o", ".a", ".pyc",
              ".pyd", ".elf", ".scr", ".sys", ".apk", ".ipa"}
MAGIC = (b"\x7fELF", b"MZ", b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf", b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\x00asm")
SKIP_DIRS = {".git", ".github", "__pycache__", "node_modules", ".venv", "venv", ".mousse-versions", ".history", ".clawhub"}
IGNORE_FILES = (".skillignore", ".clawhubignore")  # skills_guard（照 Hermes）不扫这两个文件里写的：市场来的包不带它们，每个文件都扫
# 扫描器读不了也不要紧的（不会被当成程序跑）：别的读不了的（没后缀的脚本、.zsh、夹了坏字节的 .sh……）结论至少 caution
INERT_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".svg", ".pdf", ".woff", ".woff2", ".ttf", ".otf", ".mp3", ".wav",
             ".ogg", ".m4a", ".mp4", ".webm", ".mov", ".csv", ".tsv"}
TAR_MEMBERS_MAX = FILES_MAX * 20
UA = "OpenMousse-skill-market/1"
_cache: dict[str, tuple[float, Any]] = {}   # 搜的时候按地址记一会儿（仓库的文件树、网站的索引）
CACHE_TTL = 3600.0
router = APIRouter(dependencies=[Depends(setup_api.owner)])


class MarketError(Exception):
    """一句给人看的话（不带密钥）。"""


# —— 配置 ——————————————————————————————————————————————————————————————————————

def cfg() -> dict:
    e = raw().get("engine")
    s = e.get("skills") if isinstance(e, dict) else None
    m = s.get("market") if isinstance(s, dict) else None
    out = {k: list(v) if isinstance(v, list) else v for k, v in DEFAULTS.items()}
    m = m if isinstance(m, dict) else {}
    out["enabled"] = m.get("enabled") is True
    t = m.get("timeout")
    if isinstance(t, (int, float)) and not isinstance(t, bool):
        out["timeout"] = max(1.0, min(60.0, float(t)))
    for k in ("sources", "repos", "sites"):
        if isinstance(m.get(k), list):
            out[k] = m[k]
    for k in ("clawhub", "github_api", "github_raw", "skills_sh"):
        if isinstance(m.get(k), str) and m[k].strip().startswith(("https://", "http://")):
            out[k] = m[k].strip()
    out["sources"] = [s for s in out["sources"] if s in SOURCES]
    for k in ("clawhub", "github_api", "github_raw", "skills_sh"):
        out[k] = str(out[k]).rstrip("/")
    out["sites"] = [str(x).rstrip("/") for x in out["sites"] if isinstance(x, str) and x.startswith(("https://", "http://"))]
    out["repos"] = [str(x) for x in out["repos"] if isinstance(x, str) and re.fullmatch(r"[\w.-]+/[\w.-]+", x)]
    return out


def enabled() -> bool:
    """Agent 的 skill_market 工具开没开（默认关：会联网）。命令行、设置接口是主人自己发起的，不看它。"""
    return cfg()["enabled"] is True


def home() -> Path:
    return settings.data_dir / "skills-market"


def user_root() -> Path:
    return settings.data_dir / "skills"


# —— 网络 ——————————————————————————————————————————————————————————————————————

def _owned(url: str) -> bool:
    """主人自己配的地址（市场的几个地址、sites 里的网站）：直接连（测试、自建的镜像可以是本机）；别的（Agent 给的链接）只去公网。"""
    c = cfg()
    bases = [c["clawhub"], c["github_api"], c["github_raw"], c["skills_sh"], *c["sites"]]
    return any(url == b or url.startswith(b + "/") for b in bases if b)


async def _get(url: str, *, params: dict | None = None, max_bytes: int = INDEX_MAX, headers: dict | None = None,
               timeout: float | None = None, method: str = "GET", body: dict | None = None) -> tuple[bytes, str, int]:
    """→ (内容, content-type, 状态码)。主人配的地址直接连（跟最多 5 次重定向，都还得是主人配的地址）；别的只去公网（engine/web.py：每一跳都查）。
    超过 max_bytes 抛 MarketError。"""
    import httpx
    if params:
        url = url + ("&" if "?" in url else "?") + urlencode({k: v for k, v in params.items() if v not in (None, "")})
    t = timeout or float(cfg()["timeout"])
    if not _owned(url):
        if method != "GET":
            raise MarketError(L("只能向公网地址读", "Only reads go to outside addresses"))
        import webfetch

        from . import web
        try:
            data, ctype, _final = await asyncio.wait_for(web.fetch(url, max_bytes=max_bytes), t * 3)
        except webfetch.FetchError as e:
            raise MarketError(str(e)) from None
        except asyncio.TimeoutError:
            raise MarketError(L("太久没回", "No answer in time")) from None
        return data, ctype, 200
    hdrs = {"User-Agent": UA, **(headers or {})}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(t * 3, connect=t), follow_redirects=False) as client:
            cur = url
            for _ in range(6):
                req = client.build_request(method, cur, headers=hdrs, json=body)
                r = await client.send(req, stream=True)
                try:
                    if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                        nxt = urljoin(cur, r.headers["location"])
                        if not _owned(nxt):
                            raise MarketError(L("跳到了别的地址，没跟过去", "It redirected somewhere else; not followed"))
                        cur, method, body = nxt, "GET" if r.status_code == 303 else method, None if r.status_code == 303 else body
                        continue
                    declared = r.headers.get("content-length")
                    if declared and declared.isdigit() and int(declared) > max_bytes:
                        raise MarketError(L(f"太大了（超过 {max_bytes // 1024 // 1024 or 1} MB）", f"Too big (over {max_bytes // 1024 // 1024 or 1} MB)"))
                    buf = bytearray()
                    async for chunk in r.aiter_bytes():
                        buf += chunk
                        if len(buf) > max_bytes:
                            raise MarketError(L(f"太大了（超过 {max_bytes // 1024 // 1024 or 1} MB）", f"Too big (over {max_bytes // 1024 // 1024 or 1} MB)"))
                    return bytes(buf), (r.headers.get("content-type") or "").split(";")[0].strip().lower(), r.status_code
                finally:
                    await r.aclose()
    except httpx.TimeoutException:
        raise MarketError(L("太久没回", "No answer in time")) from None
    except httpx.HTTPError as e:
        raise MarketError(L(f"连不上：{type(e).__name__}", f"Couldn't connect: {type(e).__name__}")) from None
    raise MarketError(L("重定向太多次", "Too many redirects"))


async def _json(url: str, **kw) -> Any:
    data, _ctype, status = await _get(url, **kw)
    if status == 404:
        raise MarketError(L("没有这一项（404）", "Not found (404)"))
    if status == 429:
        raise MarketError(L("问得太频繁了，过一会儿再试（429）", "Rate limited; try again later (429)"))
    if status >= 400:
        raise MarketError(L(f"对方回了 HTTP {status}", f"The server answered HTTP {status}"))
    try:
        return json.loads(data.decode("utf8"))
    except (UnicodeDecodeError, ValueError):
        raise MarketError(L("回的不是 JSON", "The answer wasn't JSON")) from None


async def _cached(key: str, make) -> Any:
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_TTL:
        return hit[1]
    v = await make()
    if len(_cache) > 200:
        _cache.clear()
    _cache[key] = (time.monotonic(), v)
    return v


def _gh_headers() -> dict:
    from . import models
    tok = models.env_value("GITHUB_TOKEN") or models.env_value("GH_TOKEN")
    return {"Accept": "application/vnd.github+json", **({"Authorization": f"Bearer {tok}"} if tok else {})}


# —— 一份技能（下到内存里的）——————————————————————————————————————————————————————————

@dataclass
class Hit:
    id: str                 # 装的时候用的标识（带来源前缀）
    source: str
    name: str
    description: str = ""
    version: str = ""
    author: str = ""
    url: str = ""
    score: float = 0.0      # 来源自己给的分（排序时只当参考）
    key: str = ""           # 去重用：同一个 GitHub 文件夹从几家来的算一份
    extra: dict = field(default_factory=dict)


@dataclass
class Bundle:
    source: str
    id: str                 # 规范的标识（lock 里记它，更新时照它再下）
    name: str
    files: dict[str, bytes]
    version: str = ""       # ClawHub 的版本、GitHub 请求的 ref
    commit: str = ""        # GitHub 的 40 位提交号
    url: str = ""
    upstream: dict = field(default_factory=dict)   # 来源自己的结论（ClawHub 的信任结论）


def _bad_rel(rel: str) -> str | None:
    """压缩包、文件树里的一个路径往外跑不跑（zip-slip）→ 问题；没问题 → None。"""
    if not rel or "\\" in rel or "\x00" in rel or rel.startswith("/") or re.match(r"^[A-Za-z]:", rel):
        return L(f"路径不对：{rel[:80]}", f"Bad path: {rel[:80]}")
    parts = rel.split("/")
    if any(p in ("", ".", "..") or ":" in p for p in parts):
        return L(f"路径往外跑：{rel[:80]}", f"Path escapes the folder: {rel[:80]}")
    return None


def _executable(rel: str, data: bytes) -> bool:
    """可执行的二进制：ELF / PE / Mach-O / wasm 的文件头，或者那几种后缀。"""
    return PurePosixPath(rel).suffix.lower() in BINARY_EXT or data[:4].startswith(MAGIC) or data[:2] == b"MZ"


def check_files(files: dict[str, bytes], *, need: str = "SKILL.md") -> dict[str, bytes]:
    """一份技能的文件过一遍上限和路径（见文件开头的第 1 步）：不像样的整份拒（抛 MarketError），.git 这类目录里的丢掉。
    need：非有不可的那个文件（带代码的插件用它查 openmousse.plugin.json，engine/plugins.py）。"""
    out: dict[str, bytes] = {}
    total = 0
    for rel, data in files.items():
        if why := _bad_rel(rel):
            raise MarketError(why)
        if any(p in SKIP_DIRS for p in rel.split("/")[:-1]) or rel.rsplit("/", 1)[-1] in IGNORE_FILES:
            continue
        if len(data) > FILE_MAX:
            raise MarketError(L(f"{rel[:80]} 太大（{len(data) // 1024} KB，一个文件最多 {FILE_MAX // 1024} KB）",
                                f"{rel[:80]} is too big ({len(data) // 1024} KB; at most {FILE_MAX // 1024} KB per file)"))
        if _executable(rel, data):
            raise MarketError(L(f"{rel[:80]} 是可执行的二进制，不收", f"{rel[:80]} is an executable binary, which isn't accepted"))
        total += len(data)
        out[rel] = data
    if len(out) > FILES_MAX:
        raise MarketError(L(f"文件太多（{len(out)} 个，最多 {FILES_MAX}）", f"Too many files ({len(out)}; at most {FILES_MAX})"))
    if total > TOTAL_MAX:
        raise MarketError(L(f"加起来太大（{total // 1024} KB，最多 {TOTAL_MAX // 1024 // 1024} MB）", f"Too big in total ({total // 1024} KB; at most "
                            f"{TOTAL_MAX // 1024 // 1024} MB)"))
    if need not in out:
        raise MarketError(L(f"里面没有 {need}", f"There's no {need} in it"))
    return out


def subtree(files: dict[str, bytes], sub: str = "") -> dict[str, bytes]:
    """从一堆文件里取出技能那一个文件夹：sub 给了就是它；没给：根上有 SKILL.md 就是根，只有一处有 SKILL.md 就是那一处。"""
    sub = sub.strip("/")
    if not sub:
        if "SKILL.md" in files:
            return files
        homes = sorted({rel.rsplit("/", 1)[0] for rel in files if rel.endswith("/SKILL.md")}, key=len)
        if len(homes) != 1:
            raise MarketError(L("找不到唯一的 SKILL.md（一个包里只能装一个技能）", "Couldn't find exactly one SKILL.md (one skill per package)"))
        sub = homes[0]
    pre = sub + "/"
    return {rel[len(pre):]: b for rel, b in files.items() if rel.startswith(pre)}


def unzip(data: bytes) -> dict[str, bytes]:
    """zip → 文件（相对路径 → 字节）。拒绝：往外跑的路径、符号链接、加起来太大；都在同一个顶层文件夹里的（GitHub 的 zip）去掉那一层。"""
    if len(data) > ARCHIVE_MAX:
        raise MarketError(L("压缩包太大", "The archive is too big"))
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise MarketError(L("不是一个能读的 zip", "Not a readable zip")) from None
    out: dict[str, bytes] = {}
    total = 0
    with zf:
        infos = zf.infolist()
        if len(infos) > FILES_MAX * 20:
            raise MarketError(L("压缩包里文件太多", "Too many files in the archive"))
        for info in infos:
            name = info.filename
            if info.is_dir():
                if why := _bad_rel(name.rstrip("/")):
                    raise MarketError(why)
                continue
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise MarketError(L(f"压缩包里有符号链接：{name[:80]}", f"The archive contains a symlink: {name[:80]}"))
            if why := _bad_rel(name):
                raise MarketError(why)
            if info.file_size > FILE_MAX:
                raise MarketError(L(f"{name[:80]} 太大", f"{name[:80]} is too big"))
            total += info.file_size
            if total > TOTAL_MAX:
                raise MarketError(L("解开以后太大", "Too big once unpacked"))
            with zf.open(info) as f:
                b = f.read(FILE_MAX + 1)
            if len(b) > FILE_MAX:
                raise MarketError(L(f"{name[:80]} 太大", f"{name[:80]} is too big"))
            out[name] = b
    return _strip_top(out)


def untar(data: bytes) -> dict[str, bytes]:
    """tar.gz → 文件。拒绝：往外跑的路径、符号链接、硬链接、设备文件，太大的。"""
    if len(data) > ARCHIVE_MAX:
        raise MarketError(L("压缩包太大", "The archive is too big"))
    out: dict[str, bytes] = {}
    total = members = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tf:
            for m in tf:
                members += 1
                if members > TAR_MEMBERS_MAX:  # 同 unzip：几十万个空成员的包光数一遍就要几秒
                    raise MarketError(L("压缩包里文件太多", "Too many files in the archive"))
                if m.isdir():
                    if why := _bad_rel(m.name.rstrip("/")):
                        raise MarketError(why)
                    continue
                if m.issym() or m.islnk():
                    raise MarketError(L(f"压缩包里有链接：{m.name[:80]}", f"The archive contains a link: {m.name[:80]}"))
                if not m.isfile():
                    raise MarketError(L(f"压缩包里有特殊文件：{m.name[:80]}", f"The archive contains a special file: {m.name[:80]}"))
                if why := _bad_rel(m.name):
                    raise MarketError(why)
                if m.size > FILE_MAX:
                    raise MarketError(L(f"{m.name[:80]} 太大", f"{m.name[:80]} is too big"))
                total += m.size
                if total > TOTAL_MAX:
                    raise MarketError(L("解开以后太大", "Too big once unpacked"))
                f = tf.extractfile(m)
                out[m.name] = f.read() if f is not None else b""
    except tarfile.TarError:
        raise MarketError(L("不是一个能读的 tar 包", "Not a readable tar archive")) from None
    return _strip_top(out)


def _strip_top(files: dict[str, bytes]) -> dict[str, bytes]:
    tops = {rel.split("/", 1)[0] for rel in files}
    if len(tops) == 1 and all("/" in rel for rel in files):
        top = tops.pop() + "/"
        return {rel[len(top):]: b for rel, b in files.items()}
    return files


def digest(files: dict[str, bytes]) -> str:
    """内容指纹：按相对路径排好，每个文件 路径 + \\0 + 内容 一起算 sha256（和 Hermes 的 _content_digest 同一种）。"""
    h = hashlib.sha256()
    for rel in sorted(files):
        h.update(rel.encode("utf8") + b"\x00")
        h.update(files[rel])
    return "sha256:" + h.hexdigest()


def disk_files(d: Path) -> dict[str, bytes]:
    """装好的那个文件夹现在的文件（链接不算、不跟）。"""
    out: dict[str, bytes] = {}
    if not d.is_dir() or d.is_symlink():
        return out
    for base, dirs, names in os.walk(d):
        b = Path(base)
        dirs[:] = sorted(x for x in dirs if not (b / x).is_symlink())
        for n in sorted(names):
            p = b / n
            if p.is_file() and not p.is_symlink():
                out[p.relative_to(d).as_posix()] = p.read_bytes()
    return out


def front(files: dict[str, bytes]) -> dict:
    from .tools import _front
    return _front(files.get("SKILL.md", b"").decode("utf8", errors="replace"))


def name_of(files: dict[str, bytes], fallback: str) -> str:
    """装成什么名字（文件夹名）：SKILL.md 的 name，不像样就用标识里的那一截。"""
    for cand in (str(front(files).get("name") or "").strip(), fallback.strip()):
        cand = cand.split("/")[-1].split("@")[0]
        if SKILL_NAME.fullmatch(cand):
            return cand
    raise MarketError(L("认不出这个技能叫什么（SKILL.md 的 name 只能用字母、数字、. _ -）", "Can't tell this skill's name (the SKILL.md name must "
                        "be letters, digits, . _ -)"))


# —— 标识 ——————————————————————————————————————————————————————————————————————

def parse_id(text: str) -> tuple[str, str]:
    """「clawhub:@作者/slug」「github:owner/repo/路径@ref」「skills.sh:owner/repo/skill」「well-known:https://网站#名字」「url:https://…」，
    也认不带前缀的：@作者/slug → clawhub；https://github.com/owner/repo/tree/ref/路径 → github；https://…/SKILL.md（.md）→ url；
    https://网站#名字 → well-known；owner/repo[/路径][@ref] → github。→ (来源, 剩下的)。认不出抛 MarketError。"""
    s = " ".join(str(text or "").split())
    if not s:
        raise MarketError(L("要装哪一个：写它的标识（搜出来的 id）", "Which one: give its identifier (the id from search)"))
    low = s.lower()
    for pre, src in (("clawhub:", "clawhub"), ("github:", "github"), ("skills.sh:", "skills.sh"), ("skills-sh:", "skills.sh"),
                     ("well-known:", "well-known"), ("url:", "url")):
        if low.startswith(pre):
            return src, s[len(pre):].strip()
    if s.startswith("@"):
        return "clawhub", s
    if low.startswith(("https://", "http://")):
        u = urlsplit(s)
        if u.hostname in ("github.com", "www.github.com"):
            parts = [p for p in u.path.split("/") if p]
            if len(parts) >= 2:
                if len(parts) >= 4 and parts[2] in ("tree", "blob"):
                    path = "/".join(parts[4:])
                    path = path[:-len("/SKILL.md")] if path.endswith("/SKILL.md") else "" if path == "SKILL.md" else path
                    return "github", f"{parts[0]}/{parts[1]}" + (f"/{path}" if path else "") + f"@{parts[3]}"
                return "github", f"{parts[0]}/{parts[1]}"
        if u.fragment:
            return "well-known", s
        if u.path.lower().endswith(".md"):
            return "url", s
        raise MarketError(L("这个链接认不出：直接给 SKILL.md 的链接，或者 https://网站#技能名", "Unrecognized link: give a direct SKILL.md "
                            "link, or https://site#skill-name"))
    if re.fullmatch(r"[\w.-]+/[\w.-]+(?:/[^@\s]*)?(?:@[\w./-]+)?", s):
        return "github", s
    if SLUG.fullmatch(s):
        return "clawhub", s
    raise MarketError(L(f"认不出这个标识：{s[:80]}", f"Unrecognized identifier: {s[:80]}"))


def _gh_parts(rest: str) -> tuple[str, str, str, str]:
    """owner/repo[/路径][@ref] → (owner, repo, 路径, ref)。"""
    ref = ""
    if "@" in rest:
        rest, ref = rest.rsplit("@", 1)
    parts = [p for p in rest.strip("/").split("/") if p]
    if len(parts) < 2 or not GH_NAME.fullmatch(parts[0]) or not GH_NAME.fullmatch(parts[1].removesuffix(".git")):
        raise MarketError(L("GitHub 的标识写成 owner/repo[/路径][@分支、标签或提交号]", "A GitHub identifier is owner/repo[/path][@branch, tag "
                            "or commit]"))
    path = "/".join(parts[2:])
    if path and (why := _bad_rel(path)):
        raise MarketError(why)
    if ref and not re.fullmatch(r"[\w./-]{1,200}", ref):
        raise MarketError(L("ref 不像样", "Bad ref"))
    return parts[0], parts[1].removesuffix(".git"), path, ref


# —— 来源：ClawHub ——————————————————————————————————————————————————————————————————

def _clawhub_ident(rest: str) -> tuple[str, str]:
    """@作者/slug、作者/skills/slug、slug → (slug, 作者)。"""
    s = rest.strip().removeprefix("@")
    parts = [p for p in s.split("/") if p]
    if len(parts) == 1:
        owner, slug = "", parts[0]
    elif len(parts) == 2:
        owner, slug = parts
    elif len(parts) == 3 and parts[1].lower() == "skills":
        owner, slug = parts[0], parts[2]
    else:
        raise MarketError(L("ClawHub 的标识写成 @作者/slug", "A ClawHub identifier is @owner/slug"))
    if not SLUG.fullmatch(slug) or (owner and not SLUG.fullmatch(owner)):
        raise MarketError(L("ClawHub 的 slug 不像样", "Bad ClawHub slug"))
    return slug, owner


async def clawhub_search(q: str, limit: int) -> list[Hit]:
    base = cfg()["clawhub"]
    data = await _json(f"{base}/api/v1/search", params={"q": q, "limit": min(limit, 50)})
    rows = data.get("results") if isinstance(data, dict) else data
    out: list[Hit] = []
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict) or not isinstance(r.get("slug"), str):
            continue
        inst = r.get("install") if isinstance(r.get("install"), dict) else {}
        ref = str(inst.get("reference") or "")
        if r.get("source") == "skills-sh" or ref.startswith("skills-sh:"):  # ClawHub 列出来的 skills.sh 的：从 GitHub 装
            rest = ref.removeprefix("skills-sh:")
            if rest.count("/") != 2:
                continue
            ident = f"skills.sh:{rest}"
            key = "gh:" + rest.lower().rsplit("/", 1)[0] + "#" + rest.lower().rsplit("/", 1)[1]
        else:
            owner = str(r.get("ownerHandle") or "").strip()
            if not owner:
                continue
            ident, key = f"clawhub:@{owner}/{r['slug']}", f"clawhub:{owner.lower()}/{r['slug'].lower()}"
        out.append(Hit(id=ident, source="clawhub", name=str(r.get("displayName") or r["slug"]), description=str(r.get("summary") or ""),
                       version=str(r.get("version") or ""), author=str(r.get("ownerHandle") or ""), url=f"{base}/skills/{r['slug']}",
                       score=float(r.get("score") or 0), key=key, extra={"official": bool(r.get("official"))}))
    return out[:limit]


def _verdict(item: dict) -> dict:
    """ClawHub 的一条信任结论 → {"blocked", "status", "decision", "reasons", "summary"}（照 OpenClaw 的 clawhub-install-trust.ts）。"""
    sec = item.get("security") if isinstance(item.get("security"), dict) else {}
    reasons = [str(x) for x in item.get("reasons") or [] if isinstance(x, (str, int))]
    status = str(sec.get("status") or "").lower()
    decision = str(item.get("decision") or "").lower()
    blocked = (decision == "blocked" or status == "malicious" or any("malicious" in r.lower() or "malware" in r.lower() or r.endswith(("_blocked",
               ".blocked")) or r == "blocked" for r in reasons))
    return {"blocked": blocked, "status": status or "unknown", "decision": decision or "unknown", "reasons": reasons[:10],
            "summary": " ".join(str(sec.get("summary") or item.get("overview") or "").split())[:300]}


async def clawhub_verdict(slug: str, owner: str, version: str) -> dict:
    """装之前查 ClawHub 的信任结论（POST /api/v1/skills/-/security-verdicts）。查不到抛 MarketError（装不了：同 OpenClaw 的 fail closed）。"""
    base = cfg()["clawhub"]
    try:
        data = await _json(f"{base}/api/v1/skills/-/security-verdicts", method="POST",
                           body={"items": [{"slug": slug, **({"ownerHandle": owner} if owner else {}), "version": version}]})
    except MarketError as e:
        raise MarketError(L(f"查不到 ClawHub 对它的信任结论（{e}），没装", f"Couldn't get ClawHub's trust verdict for it ({e}); not installed")) from None
    items = data.get("items") if isinstance(data, dict) else None
    item = items[0] if isinstance(items, list) and items and isinstance(items[0], dict) else None
    if item is None or item.get("error") or str(item.get("slug") or slug) != slug:
        raise MarketError(L("ClawHub 没给出它的信任结论，没装", "ClawHub gave no trust verdict for it; not installed"))
    return _verdict(item)


async def clawhub_fetch(rest: str) -> Bundle:
    base = cfg()["clawhub"]
    slug, owner = _clawhub_ident(rest)
    detail = await _json(f"{base}/api/v1/skills/{quote(slug)}", params={"ownerHandle": owner})
    skill = detail.get("skill") if isinstance(detail, dict) and isinstance(detail.get("skill"), dict) else detail if isinstance(detail, dict) else {}
    own = detail.get("owner") if isinstance(detail, dict) else None
    real_owner = (own.get("handle") if isinstance(own, dict) else own) if own else ""
    if owner and real_owner and str(real_owner).lower() != owner.lower():
        raise MarketError(L("ClawHub 上这个 slug 不是这个作者的", "On ClawHub this slug belongs to someone else"))
    owner = owner or str(real_owner or "")
    latest = detail.get("latestVersion") if isinstance(detail, dict) else None
    version = str((latest or {}).get("version") or "") if isinstance(latest, dict) else ""
    if not version and isinstance(skill.get("tags"), dict):
        version = str(skill["tags"].get("latest") or "")
    if not version:
        raise MarketError(L("ClawHub 上找不到它的最新版本", "Couldn't find its latest version on ClawHub"))
    mod = detail.get("moderation") if isinstance(detail, dict) and isinstance(detail.get("moderation"), dict) else {}
    if mod.get("isMalwareBlocked") or str(mod.get("verdict") or "").lower() == "malicious":
        raise MarketError(L("ClawHub 把这个技能标成了恶意的、拦下了，不装", "ClawHub flagged this skill as malicious and blocked it; not installed"))
    verdict = await clawhub_verdict(slug, owner, version)
    if verdict["blocked"]:
        raise MarketError(L(f"ClawHub 拦下了这个版本（{verdict['status']}：{'、'.join(verdict['reasons']) or verdict['summary']}），不装",
                            f"ClawHub blocked this release ({verdict['status']}: {', '.join(verdict['reasons']) or verdict['summary']}); not installed"))
    data, ctype, status = await _get(f"{base}/api/v1/download", params={"slug": slug, "ownerHandle": owner, "version": version}, max_bytes=ARCHIVE_MAX)
    if status >= 400:
        raise MarketError(L(f"ClawHub 下载回了 HTTP {status}", f"ClawHub's download answered HTTP {status}"))
    ident = f"clawhub:@{owner}/{slug}" if owner else f"clawhub:{slug}"
    if ctype == "application/json" or data[:1] == b"{":  # GitHub 托管的：从那一次提交下
        try:
            j = json.loads(data.decode("utf8"))
        except (UnicodeDecodeError, ValueError):
            raise MarketError(L("ClawHub 回的下载信息看不懂", "ClawHub's download answer was unreadable")) from None
        commit = str(j.get("commit") or "")
        if not SHA.fullmatch(commit) or not isinstance(j.get("repo"), str):
            raise MarketError(L("ClawHub 给的 GitHub 来源没有 40 位提交号，不装", "ClawHub's GitHub source has no 40-character commit; not installed"))
        gb = await github_fetch(f"{j['repo']}/{str(j.get('path') or '').strip('/')}".rstrip("/") + f"@{commit}")
        return Bundle(source="clawhub", id=ident, name=gb.name, files=gb.files, version=version, commit=commit, url=f"{base}/skills/{slug}",
                      upstream={"clawhub": verdict, "github": j["repo"]})
    files = subtree(await asyncio.to_thread(unzip, data))
    files = check_files(files)
    return Bundle(source="clawhub", id=ident, name=name_of(files, slug), files=files, version=version, url=f"{base}/skills/{slug}",
                  upstream={"clawhub": verdict})


# —— 来源：GitHub ——————————————————————————————————————————————————————————————————

async def _gh_commit(owner: str, repo: str, ref: str) -> str:
    api = cfg()["github_api"]
    if SHA.fullmatch(ref.lower()):
        return ref.lower()
    if not ref:
        info = await _json(f"{api}/repos/{owner}/{repo}", headers=_gh_headers())
        ref = str(info.get("default_branch") or "") if isinstance(info, dict) else ""
        if not ref:
            raise MarketError(L("找不到这个仓库的默认分支", "Couldn't find the repository's default branch"))
    c = await _json(f"{api}/repos/{owner}/{repo}/commits/{quote(ref, safe='')}", headers=_gh_headers())
    sha = str(c.get("sha") or "").lower() if isinstance(c, dict) else ""
    if not SHA.fullmatch(sha):
        raise MarketError(L(f"{ref} 换不成提交号", f"Couldn't turn {ref} into a commit"))
    return sha


async def _gh_tree(owner: str, repo: str, sha: str) -> list[dict]:
    api = cfg()["github_api"]
    t = await _json(f"{api}/repos/{owner}/{repo}/git/trees/{sha}", params={"recursive": "1"}, headers=_gh_headers(), max_bytes=8 * 1024 * 1024)
    if not isinstance(t, dict) or not isinstance(t.get("tree"), list):
        raise MarketError(L("读不到仓库的文件树", "Couldn't read the repository's file tree"))
    if t.get("truncated"):
        raise MarketError(L("仓库太大，GitHub 的文件树给不全：把路径写到技能那个文件夹", "The repository is too big for GitHub's file tree; give the "
                            "path down to the skill's folder"))
    return [e for e in t["tree"] if isinstance(e, dict) and isinstance(e.get("path"), str)]


def _skill_dirs(tree: list[dict]) -> list[str]:
    """文件树里有 SKILL.md 的文件夹（"" = 根）。"""
    return sorted({e["path"][:-len("SKILL.md")].rstrip("/") for e in tree if e.get("type") == "blob" and (e["path"] == "SKILL.md"
                   or e["path"].endswith("/SKILL.md"))})


async def github_fetch(rest: str, *, find: str = "") -> Bundle:
    """GitHub 上的一个技能文件夹，钉在一次提交上。find：skills.sh 给的技能名（路径不知道：照 Hermes 先试几个常见的位置，再在文件树里找）。"""
    c = cfg()
    owner, repo, path, ref = _gh_parts(rest)
    sha = await _gh_commit(owner, repo, ref)
    tree = await _gh_tree(owner, repo, sha)
    if find:
        homes = _skill_dirs(tree)
        tries = [f"skills/{find}", find, f".agents/skills/{find}", f".claude/skills/{find}"]
        path = next((p for p in tries if p in homes), "") or next((h for h in homes if h.rsplit("/", 1)[-1] == find), "")
        if not path:
            raise MarketError(L(f"{owner}/{repo} 里找不到 {find} 这个技能", f"Couldn't find the {find} skill in {owner}/{repo}"))
    pre = f"{path}/" if path else ""
    wanted = [e for e in tree if e.get("type") == "blob" and (not pre or e["path"].startswith(pre))]
    if not any(e["path"] == f"{pre}SKILL.md" for e in wanted):
        raise MarketError(L(f"{owner}/{repo}/{path or '(根)'} 里没有 SKILL.md", f"No SKILL.md in {owner}/{repo}/{path or '(root)'}"))
    if any(str(e.get("mode")) == "120000" for e in wanted):
        raise MarketError(L("技能文件夹里有符号链接，不装", "The skill folder contains a symlink; not installed"))
    wanted = [e for e in wanted if str(e.get("mode")) != "160000" and not any(p in SKIP_DIRS for p in e["path"][len(pre):].split("/")[:-1])]
    if len(wanted) > FILES_MAX:
        raise MarketError(L(f"文件太多（{len(wanted)} 个，最多 {FILES_MAX}）", f"Too many files ({len(wanted)}; at most {FILES_MAX})"))
    if sum(int(e.get("size") or 0) for e in wanted) > TOTAL_MAX or any(int(e.get("size") or 0) > FILE_MAX for e in wanted):
        raise MarketError(L("文件太大", "Files too big"))
    files: dict[str, bytes] = {}

    async def one(e: dict) -> None:
        data, _ctype, status = await _get(f"{c['github_raw']}/{owner}/{repo}/{sha}/{quote(e['path'])}", max_bytes=FILE_MAX)
        if status >= 400:
            raise MarketError(L(f"{e['path']} 下不来（HTTP {status}）", f"Couldn't download {e['path']} (HTTP {status})"))
        files[e["path"][len(pre):]] = data
    sem = asyncio.Semaphore(6)

    async def bounded(e: dict) -> None:
        async with sem:
            await one(e)
    await asyncio.gather(*(bounded(e) for e in wanted))
    files = check_files(files)
    ident = f"github:{owner}/{repo}" + (f"/{path}" if path else "") + (f"@{ref}" if ref else "")
    return Bundle(source="github", id=ident, name=name_of(files, path.rsplit("/", 1)[-1] if path else repo), files=files, version=ref,
                  commit=sha, url=f"https://github.com/{owner}/{repo}/tree/{sha}/{path}".rstrip("/"))


async def github_search(q: str, limit: int) -> list[Hit]:
    c = cfg()
    terms = _terms(q)
    out: list[Hit] = []
    for full in c["repos"]:
        owner, repo = full.split("/", 1)

        async def listing(owner=owner, repo=repo) -> list[str]:
            sha = await _gh_commit(owner, repo, "")
            return _skill_dirs(await _gh_tree(owner, repo, sha))
        try:
            homes = await _cached(f"gh-tree:{c['github_api']}:{full}", listing)
        except MarketError:
            continue
        for h in homes:
            leaf = h.rsplit("/", 1)[-1] or repo
            if terms and not any(t in leaf.lower() for t in terms):  # 按文件夹名对（说明要再下一次 SKILL.md，搜的时候不下）
                continue
            out.append(Hit(id=f"github:{full}" + (f"/{h}" if h else ""), source="github", name=leaf, author=owner,
                           url=f"https://github.com/{full}/tree/HEAD/{h}".rstrip("/"), key=f"gh:{full.lower()}#{leaf.lower()}"))
    hdr = _gh_headers()
    if "Authorization" in hdr and q.strip():  # 代码搜索要令牌
        try:
            data = await _json(f"{c['github_api']}/search/code", params={"q": f"{q} filename:SKILL.md", "per_page": min(limit, 30)}, headers=hdr)
        except MarketError:
            data = {}
        for it in data.get("items") or [] if isinstance(data, dict) else []:
            repo_full = ((it.get("repository") or {}).get("full_name") or "") if isinstance(it, dict) else ""
            path = str(it.get("path") or "") if isinstance(it, dict) else ""
            if not repo_full or not path.endswith("SKILL.md"):
                continue
            d = path[:-len("SKILL.md")].rstrip("/")
            out.append(Hit(id=f"github:{repo_full}" + (f"/{d}" if d else ""), source="github", name=d.rsplit("/", 1)[-1] or repo_full.split("/")[1],
                           author=repo_full.split("/")[0], url=f"https://github.com/{repo_full}/tree/HEAD/{d}".rstrip("/"),
                           key=f"gh:{repo_full.lower()}#{(d.rsplit('/', 1)[-1] or repo_full.split('/')[1]).lower()}"))
    return out[:limit]


# —— 来源：skills.sh ——————————————————————————————————————————————————————————————

async def skillssh_search(q: str, limit: int) -> list[Hit]:
    base = cfg()["skills_sh"]
    data = await _json(f"{base}/api/search", params={"q": q, "limit": min(limit, 50)})
    rows = data.get("skills") if isinstance(data, dict) else None
    out = []
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict):
            continue
        src, sid = str(r.get("source") or ""), str(r.get("skillId") or r.get("name") or "")
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", src) or not SLUG.fullmatch(sid):
            continue
        out.append(Hit(id=f"skills.sh:{src}/{sid}", source="skills.sh", name=str(r.get("name") or sid), author=src.split("/")[0],
                       url=f"{base}/{src}/{sid}", score=float(r.get("installs") or 0), key=f"gh:{src.lower()}#{sid.lower()}",
                       extra={"installs": r.get("installs")}))
    return out[:limit]


async def skillssh_fetch(rest: str) -> Bundle:
    parts = [p for p in rest.strip("/").split("/") if p]
    if len(parts) != 3 or not all(GH_NAME.fullmatch(p) for p in parts):
        raise MarketError(L("skills.sh 的标识写成 owner/repo/skill", "A skills.sh identifier is owner/repo/skill"))
    b = await github_fetch(f"{parts[0]}/{parts[1]}", find=parts[2])
    return Bundle(source="skills.sh", id=f"skills.sh:{'/'.join(parts)}", name=b.name, files=b.files, version="", commit=b.commit, url=b.url,
                  upstream={"github": f"{parts[0]}/{parts[1]}"})


# —— 来源：照 agentskills.io 格式发布的网站（/.well-known/）和直接给的链接 ————————————————————————

async def _index(site: str) -> tuple[str, list[dict]]:
    """网站的技能索引：先找 v0.2 的 /.well-known/agent-skills/index.json，再找 v0.1 的 /.well-known/skills/index.json → (版本, 列表)。"""
    last: Exception | None = None
    for ver, path in (("0.2", "/.well-known/agent-skills/index.json"), ("0.1", "/.well-known/skills/index.json")):
        try:
            data = await _json(site + path)
        except MarketError as e:
            last = e
            continue
        items = data.get("skills") if isinstance(data, dict) else None
        if not isinstance(items, list):
            continue
        schema = str(data.get("$schema") or "")
        if ver == "0.2" and schema and "/discovery/0.2" not in schema:
            continue  # 认不出的版本：照 RFC 不处理
        return ver, [x for x in items if isinstance(x, dict) and isinstance(x.get("name"), str)]
    raise MarketError(L(f"{site} 没有技能索引（{last}）", f"{site} has no skills index ({last})"))


async def wellknown_search(q: str, limit: int) -> list[Hit]:
    terms = _terms(q)
    out: list[Hit] = []
    for site in cfg()["sites"]:
        try:
            _ver, items = await _cached(f"wk:{site}", lambda site=site: _index(site))
        except MarketError:
            continue
        for it in items:
            text = f"{it['name']} {it.get('description') or ''}".lower()
            if terms and not any(t in text for t in terms):
                continue
            out.append(Hit(id=f"well-known:{site}#{it['name']}", source="well-known", name=it["name"], description=str(it.get("description") or ""),
                           author=urlsplit(site).hostname or "", url=site, key=f"wk:{site.lower()}#{it['name'].lower()}"))
    return out[:limit]


async def wellknown_fetch(rest: str) -> Bundle:
    site, _, name = rest.partition("#")
    site = site.strip().rstrip("/")
    for tail in ("/.well-known/agent-skills/index.json", "/.well-known/skills/index.json"):
        site = site.removesuffix(tail)
    if not site.startswith(("https://", "http://")) or not name or not SKILL_NAME.fullmatch(name):
        raise MarketError(L("网站的标识写成 https://网站#技能名", "A site identifier is https://site#skill-name"))
    ver, items = await _index(site)
    it = next((x for x in items if x["name"] == name), None)
    if it is None:
        raise MarketError(L(f"{site} 的索引里没有 {name}", f"{site}'s index has no {name}"))
    if ver == "0.2":
        url = urljoin(f"{site}/.well-known/agent-skills/index.json", str(it.get("url") or ""))
        data, _ctype, status = await _get(url, max_bytes=ARCHIVE_MAX)
        if status >= 400:
            raise MarketError(L(f"下不来（HTTP {status}）", f"Couldn't download it (HTTP {status})"))
        want = str(it.get("digest") or "")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", want) or hashlib.sha256(data).hexdigest() != want[7:]:
            raise MarketError(L("下下来的内容和索引里的摘要对不上，不装", "The download doesn't match the index's digest; not installed"))
        kind = str(it.get("type") or "skill-md")
        if kind == "archive":
            files = subtree(await asyncio.to_thread(unzip if data[:2] == b"PK" else untar, data))
        elif kind == "skill-md":
            files = {"SKILL.md": data}
        else:
            raise MarketError(L(f"认不出这种技能：{kind}", f"Unknown skill type: {kind}"))
    else:
        files = {}
        for rel in [str(x) for x in it.get("files") or ["SKILL.md"]][:FILES_MAX + 1]:
            if why := _bad_rel(rel):
                raise MarketError(why)
            data, _ctype, status = await _get(f"{site}/.well-known/skills/{name}/{quote(rel)}", max_bytes=FILE_MAX)
            if status >= 400:
                raise MarketError(L(f"{rel} 下不来（HTTP {status}）", f"Couldn't download {rel} (HTTP {status})"))
            files[rel] = data
    files = check_files(files)
    digest_note = {"digest": str(it.get("digest"))} if ver == "0.2" else {}
    return Bundle(source="well-known", id=f"well-known:{site}#{name}", name=name_of(files, name), files=files, version=f"index v{ver}",
                  url=site, upstream=digest_note)


async def url_fetch(rest: str) -> Bundle:
    url = rest.strip()
    if not url.startswith(("https://", "http://")):
        raise MarketError(L("链接要写成 https://…/SKILL.md", "The link must look like https://…/SKILL.md"))
    data, _ctype, status = await _get(url, max_bytes=FILE_MAX)
    if status >= 400:
        raise MarketError(L(f"下不来（HTTP {status}）", f"Couldn't download it (HTTP {status})"))
    files = check_files({"SKILL.md": data})
    stem = PurePosixPath(urlsplit(url).path).parent.name
    return Bundle(source="url", id=f"url:{url}", name=name_of(files, stem), files=files, url=url)


FETCH = {"clawhub": clawhub_fetch, "github": github_fetch, "skills.sh": skillssh_fetch, "well-known": wellknown_fetch, "url": url_fetch}
SEARCH = {"clawhub": clawhub_search, "skills.sh": skillssh_search, "github": github_search, "well-known": wellknown_search}


async def fetch(ident: str) -> Bundle:
    """按标识下一份技能到内存（见文件开头的第 1 步）。出错抛 MarketError。"""
    src, rest = parse_id(ident)
    return await FETCH[src](rest)


# —— 搜 ——————————————————————————————————————————————————————————————————————

def _terms(q: str) -> list[str]:
    return [t for t in re.split(r"[\s,，、/]+", q.lower()) if t]


def relevance(q: str, h: Hit) -> float:
    """这一条和这句话对得上多少：名字一模一样 1，名字里有整句 0.8，每个词都在名字或说明里 0.6，只有一部分按比例。"""
    ql = " ".join(q.lower().split())
    name, desc = h.name.lower(), h.description.lower()
    if not ql:
        return 0.1
    if ql in (name, name.replace("-", " "), h.id.lower().rsplit("/", 1)[-1]):
        return 1.0
    if ql in name or ql.replace(" ", "-") in name:
        return 0.8
    terms = _terms(q)
    hits = sum(1 for t in terms if t in name or t in desc)
    return 0.6 if terms and hits == len(terms) else 0.5 * hits / max(1, len(terms))


async def search(q: str, *, sources: list[str] | None = None, limit: int = SEARCH_LIMIT) -> dict:
    """几家一起搜（每家最多 timeout 秒，出错的跳过），合并、去重、排序 → {"query", "hits": [Hit 的 dict…], "failed": [{"source", "error"}],
    "searched": [来源…]}。"""
    c = cfg()
    want = [s for s in (sources or c["sources"]) if s in SEARCH]
    limit = max(1, min(int(limit or SEARCH_LIMIT), 50))

    async def one(src: str) -> list[Hit]:
        return await asyncio.wait_for(SEARCH[src](q, limit), float(c["timeout"]) + 2)
    results = await asyncio.gather(*(one(s) for s in want), return_exceptions=True)
    hits: list[Hit] = []
    failed = []
    for src, res in zip(want, results):
        if isinstance(res, BaseException):
            why = L("超时", "timed out") if isinstance(res, asyncio.TimeoutError) else str(res) or type(res).__name__
            failed.append({"source": src, "error": why[:200]})
            continue
        hits += res
    order = {s: i for i, s in enumerate(SOURCES)}
    hits.sort(key=lambda h: (-relevance(q, h), order.get(h.source, 9), -h.score, h.name.lower()))
    seen: set[str] = set()
    out = []
    for h in hits:
        k = h.key or h.id.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append({**asdict(h), "relevance": round(relevance(q, h), 2)})
    return {"query": q, "hits": out[:limit], "failed": failed, "searched": want}


# —— 隔离区、扫描 ——————————————————————————————————————————————————————————————————

def _write_new(path: Path, data: bytes) -> None:
    """新文件（O_EXCL、不跟链接），0644：隔离区和装好的技能里都不留执行位。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o644)
    with os.fdopen(fd, "wb") as f:
        f.write(data)


def quarantine(b: Bundle) -> tuple[str, Path]:
    """放进隔离区 <data_dir>/skills-market/quarantine/<编号>/<名字>/ → (编号, 文件夹)。"""
    token = "q" + uuid.uuid4().hex[:16]
    d = home() / "quarantine" / token / b.name
    d.mkdir(parents=True, exist_ok=False)
    for rel, data in b.files.items():
        _write_new(d / rel, data)
    return token, d


def scan(d: Path):
    """engine/skills_guard（照搬 Hermes 的）按社区、不受信扫 → ScanResult。"""
    from . import skills_guard
    return skills_guard.scan_skill(d, source="community")


def findings_of(res, n: int = 12) -> list[dict]:
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    fs = sorted(res.findings, key=lambda f: (order.get(f.severity, 4), f.file, f.line))
    return [{"severity": f.severity, "category": f.category, "file": f.file, "line": f.line, "rule": f.pattern_id,
             "description": f.description} for f in fs[:n]]


def unscanned(files: dict[str, bytes]) -> list[str]:
    """skills_guard 读不到的文件（它只读那几种后缀的 UTF-8 文字）：没后缀的脚本、.zsh、夹了一个坏字节的 .sh……图片、字体这类（INERT_EXT）不算。"""
    from . import skills_guard
    out = []
    for rel, data in sorted(files.items()):
        pp = PurePosixPath(rel)
        if pp.name != "SKILL.md" and pp.suffix.lower() not in skills_guard.SCANNABLE_EXTENSIONS:
            if pp.suffix.lower() not in INERT_EXT:
                out.append(rel)
            continue
        try:
            data.decode("utf-8-sig")
        except UnicodeDecodeError:
            out.append(rel)
    return out


def with_unscanned(verdict: str, findings: list[dict], files: dict[str, bytes]) -> tuple[str, list[dict]]:
    """扫描器没读到的文件每个记一条 high（同 skills_guard：有 high 就是 caution），排在 critical 后面。→ (结论, 发现)。"""
    blind = unscanned(files)
    if not blind:
        return verdict, findings
    extra = [{"severity": "high", "category": "structural", "file": rel[:80], "line": 0, "rule": "not_scanned",
              "description": L("扫描器读不了这个文件（不认识的类型，或者不是 UTF-8 的字），没扫过：装之前自己看一眼",
                               "the scanner can't read this file (unknown type, or not UTF-8 text), so it wasn't checked; look at it before "
                               "installing")} for rel in blind]
    crit = [f for f in findings if f.get("severity") == "critical"]
    return ("dangerous" if verdict == "dangerous" else "caution"), crit + extra + [f for f in findings if f.get("severity") != "critical"]


def verdict_word(v: str) -> str:
    return {"safe": L("safe（没发现问题）", "safe (nothing found)"), "caution": L("caution（有要留意的地方）", "caution (worth a look)"),
            "dangerous": L("dangerous（危险）", "dangerous")}.get(v, v)


# —— 装在哪、lock ————————————————————————————————————————————————————————————————

def target_root(target: str) -> Path:
    """装到哪：user = 用户级的技能目录（所有 Agent 共用）；别的是 Agent 的 id = 它自己工作区的 skills/。"""
    if target in ("", "user"):
        return user_root()
    from . import agent_skills, skill_write
    if agent_skills.agent_name(target) is None:
        raise MarketError(L(f"没有 {target} 这个 Agent", f"No Agent called {target}"))
    root = skill_write.home(target)
    if root is None:
        raise MarketError(L(f"{target} 没有自己的 skills/（没有自己的工作区，或者链到了别人的）", f"{target} has no skills/ of its own"))
    return root


def _lock_path() -> Path:
    return home() / "lock.json"


@contextlib.contextmanager
def _locked():
    """lock.json 同一时间只有一个在写（进程里的锁 + 文件锁：命令行和服务器不打架）。"""
    p = _lock_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    fh = open(p.with_suffix(".json.lock"), "a+")  # noqa: SIM115
    try:
        try:
            import fcntl
            fcntl.flock(fh, fcntl.LOCK_EX)
        except ImportError:
            pass
        yield
    finally:
        fh.close()


def read_lock() -> dict:
    try:
        v = json.loads(_lock_path().read_text(encoding="utf8"))
    except (OSError, ValueError):
        v = {}
    inst = v.get("installed") if isinstance(v, dict) else None
    return {"version": 1, "installed": inst if isinstance(inst, dict) else {}}


def _write_lock(data: dict) -> None:
    p = _lock_path()
    tmp = p.with_name(f".lock.{os.getpid()}.{uuid.uuid4().hex[:6]}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf8")
    os.replace(tmp, p)


def lock_key(target: str, name: str) -> str:
    return f"{target or 'user'}/{name}"


def installed(target: str | None = None) -> list[dict]:
    """lock 里记着的（市场装的），每一项多 localChanges（现在的文件对不上 lock 的指纹）、missing（文件夹不在了）。"""
    out = []
    for key, e in sorted(read_lock()["installed"].items()):
        if not isinstance(e, dict) or (target is not None and e.get("target") != target):
            continue
        try:
            d = target_root(str(e.get("target") or "user")) / str(e.get("name"))
        except MarketError:
            d = None
        files = disk_files(d) if d is not None else {}
        out.append({**e, "key": key, "missing": not files, "localChanges": bool(files) and digest(files) != e.get("hash")})
    return out


# —— 准备一次安装 / 更新（下载、隔离、扫描，记一份 staged）——————————————————————————————————————

def _staged_path(token: str) -> Path:
    if not re.fullmatch(r"q[0-9a-f]{16}", token or ""):
        raise MarketError(L("没有这一份待装的", "No such staged install"))
    return home() / "staged" / f"{token}.json"


def _sweep() -> None:
    """过了 STAGED_DAYS 天还没装的：连同隔离区里那一份一起清掉。"""
    cutoff = time.time() - STAGED_DAYS * 86400
    for p in (home() / "staged").glob("q*.json") if (home() / "staged").is_dir() else []:
        try:
            if p.stat().st_mtime < cutoff:
                shutil.rmtree(home() / "quarantine" / p.stem, ignore_errors=True)
                p.unlink(missing_ok=True)
        except OSError:
            continue


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def first_lines(files: dict[str, bytes], n: int = 600) -> str:
    """SKILL.md 开头一段（frontmatter 之后，最多 n 字）。"""
    text = files.get("SKILL.md", b"").decode("utf8", errors="replace")
    if text.startswith("---"):
        end = text.find("\n---", 3)
        text = text[end + 4:] if end > 0 else text
    text = text.strip()
    return text if len(text) <= n else text[:n] + "…"


async def prepare(ident: str, *, target: str = "user", op: str = "install") -> dict:
    """下载 → 检查 → 隔离区 → 扫描，记一份 staged（<data_dir>/skills-market/staged/<编号>.json）。→ staged 的样子
    {"staged", "op", "id", "source", "name", "version", "commit", "url", "target", "hash", "verdict", "findings", "upstream", "preview", …}。
    出错（下不来、不像样、ClawHub 拦下的）抛 MarketError；dangerous 也照样回（调用方决定：不出卡、不装）。"""
    from . import skills_guard
    root = target_root(target)
    b = await fetch(ident)
    _sweep()
    from . import skill_gate
    token, d = await asyncio.to_thread(quarantine, b)
    res = await asyncio.to_thread(scan, d)
    verdict, found = with_unscanned(res.verdict, findings_of(res), b.files)
    rec = {"staged": token, "op": op, "id": b.id, "requested": ident, "source": b.source, "name": b.name, "version": b.version, "commit": b.commit,
           "url": b.url, "target": target or "user", "dest": str(root / b.name), "hash": digest(b.files), "files": sorted(b.files),
           "verdict": verdict, "findings": found[:20], "summary": res.summary, "scanner": skills_guard.SCANNER_VERSION,
           "env": skill_gate.needs(front(b.files)).declared_env(), "upstream": b.upstream, "preview": first_lines(b.files), "created_at": _now()}
    p = home() / "staged" / f"{token}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf8")
    return rec


def staged(token: str) -> dict:
    try:
        rec = json.loads(_staged_path(token).read_text(encoding="utf8"))
    except (OSError, ValueError):
        raise MarketError(L("没有这一份待装的（过期了，或者已经装过了）", "No such staged install (it expired, or was already installed)")) from None
    return rec


def _drop_staged(token: str) -> None:
    with contextlib.suppress(MarketError, OSError):
        _staged_path(token).unlink(missing_ok=True)
    shutil.rmtree(home() / "quarantine" / token, ignore_errors=True)


def _keep_version(d: Path, who: str, note: str) -> str | None:
    """换掉、拿掉之前整份留一版（engine/versions.py，和 skill_manage 存的同一种：键 skills/<名字>）。"""
    from . import skill_write
    if not d.is_dir():
        return None
    try:
        return skill_write.snapshot(d, note, who)
    except ToolFailure as e:
        raise MarketError(str(e)) from None


def install_staged(token: str, *, who: str = "owner", expect_hash: str | None = None) -> dict:
    """把隔离区里那一份装进去（主人点了卡、或者主人自己在命令行 / 设置里确认的）。核对指纹、再扫一遍（dangerous 照样不装），再放进去、记 lock。
    → {"name", "target", "dest", "version", "commit", "hash", "verdict", "op", "kept"?}。不行抛 MarketError。"""
    rec = staged(token)
    qdir = home() / "quarantine" / token / rec["name"]
    files = disk_files(qdir)
    if not files or digest(files) != rec["hash"] or (expect_hash and expect_hash != rec["hash"]):
        raise MarketError(L("隔离区里那一份和卡上的对不上（被改过、或者已经不在了），没装", "The quarantined copy doesn't match the card (changed or "
                            "gone); not installed"))
    res = scan(qdir)
    if res.verdict == "dangerous":
        raise MarketError(L("扫描结论是 dangerous，不装", "The scan verdict is dangerous; not installed"))
    verdict, found = with_unscanned(res.verdict, findings_of(res, 6), files)
    root = target_root(rec["target"])
    dest = root / rec["name"]
    key = lock_key(rec["target"], rec["name"])
    kept = None
    with _locked():
        lock = read_lock()
        mine = lock["installed"].get(key)
        if rec["op"] == "install" and (dest.exists() or dest.is_symlink()):
            raise MarketError(L(f"{dest} 已经有了：市场装的用 update 更新，自己的先换个名字", f"{dest} already exists: use update for market "
                                "installs; rename your own one first"))
        if rec["op"] == "update":
            if not isinstance(mine, dict):
                raise MarketError(L("lock 里没有它：不是市场装的，没更新", "It's not in the lock file (not a market install); not updated"))
            now = disk_files(dest)
            if now and digest(now) != mine.get("hash"):
                raise MarketError(L("本地改过（和装的时候不一样了），没更新", "It was changed locally since it was installed; not updated"))
        root.mkdir(parents=True, exist_ok=True)
        tmp = root / f".{rec['name']}.installing-{token}"
        shutil.rmtree(tmp, ignore_errors=True)
        for rel, data in files.items():
            _write_new(tmp / rel, data)
        if rec["op"] == "update" and dest.exists():
            kept = _keep_version(dest, who, "update (skill market)")
            old = root / f".{rec['name']}.old-{token}"
            os.replace(dest, old)
            os.replace(tmp, dest)
            shutil.rmtree(old, ignore_errors=True)
        else:
            os.replace(tmp, dest)
        entry = {"name": rec["name"], "target": rec["target"], "source": rec["source"], "id": rec["id"], "version": rec["version"],
                 "commit": rec["commit"], "url": rec["url"], "hash": rec["hash"], "files": rec["files"], "verdict": verdict,
                 "findings": found[:6], "scanner": rec.get("scanner") or "", "upstream": rec.get("upstream") or {},
                 "installed_at": (mine or {}).get("installed_at") or _now(), "updated_at": _now(), "by": who}
        lock["installed"][key] = entry
        _write_lock(lock)
    _drop_staged(token)
    from . import memgate
    memgate.approved("procedure", "edit" if rec["op"] == "update" else "add", target=rec["name"], author=who, agent=rec["target"],
                     result=f"skill market {rec['op']}: {rec['id']}")
    return {"name": rec["name"], "target": rec["target"], "dest": str(dest), "version": rec["version"], "commit": rec["commit"], "hash": rec["hash"],
            "verdict": verdict, "op": rec["op"], **({"kept": kept} if kept else {})}


def remove(name: str, *, target: str = "user", who: str = "owner") -> dict:
    """卸载市场装的一个技能：先留一版，再拿掉，lock 里去掉。不是市场装的（lock 里没有）不动。→ {"name", "target", "kept"}。"""
    key = lock_key(target, name)
    with _locked():
        lock = read_lock()
        if key not in lock["installed"]:
            raise MarketError(L(f"{name} 不是技能市场装的（lock 里没有），没动", f"{name} wasn't installed from the skill market (not in the lock); "
                                "left alone"))
        d = target_root(target) / name
        kept = _keep_version(d, who, "remove (skill market)") if d.is_dir() and not d.is_symlink() else None
        if d.is_dir() and not d.is_symlink():
            shutil.rmtree(d)
        lock["installed"].pop(key, None)
        _write_lock(lock)
    from . import memgate
    memgate.approved("procedure", "forget", target=name, author=who, agent=target, result="skill market remove")
    return {"name": name, "target": target, "kept": kept}


async def prepare_update(name: str, *, target: str = "user", force: bool = False) -> dict:
    """更新之前：照 lock 里的标识再下一次，和指纹比。→ {"status": "current" | "local-changes" | "staged", …}（staged 的带 prepare 的样子）。"""
    e = read_lock()["installed"].get(lock_key(target, name))
    if not isinstance(e, dict):
        raise MarketError(L(f"{name} 不是技能市场装的（lock 里没有）", f"{name} wasn't installed from the skill market (not in the lock)"))
    now = disk_files(target_root(target) / name)
    if now and digest(now) != e.get("hash") and not force:
        return {"status": "local-changes", "name": name, "target": target}
    b = await fetch(str(e.get("id") or ""))
    if digest(b.files) == e.get("hash"):
        return {"status": "current", "name": name, "target": target, "version": b.version, "commit": b.commit}
    rec = await prepare(str(e["id"]), target=target, op="update")
    if rec["name"] != name:
        _drop_staged(rec["staged"])
        raise MarketError(L(f"上游的技能改名成了 {rec['name']}，没更新", f"Upstream renamed the skill to {rec['name']}; not updated"))
    return {"status": "staged", **rec}


# —— 卡（kind tool，engine/calls.py）—————————————————————————————————————————————————————

def where(target: str) -> str:
    return L("所有 Agent 共用的技能目录", "the skills folder every Agent shares") if target in ("", "user") else L(f"{target} 的工作区", f"{target}'s workspace")


def card(rec: dict, who: str) -> dict:
    up = rec.get("upstream") or {}
    ch = up.get("clawhub") if isinstance(up.get("clawhub"), dict) else None
    ver = rec.get("commit") or rec.get("version") or "-"
    lines = [L(f"来源：{rec['source']} {rec['id']}", f"Source: {rec['source']} {rec['id']}"),
             L(f"版本：{rec.get('version') or '-'}" + (f"（提交 {rec['commit'][:12]}）" if rec.get("commit") else ""),
               f"Version: {rec.get('version') or '-'}" + (f" (commit {rec['commit'][:12]})" if rec.get("commit") else "")),
             L(f"装到：{where(rec['target'])}", f"Installs into: {where(rec['target'])}"),
             L(f"扫描：{verdict_word(rec['verdict'])}，{len(rec['findings'])} 条发现", f"Scan: {verdict_word(rec['verdict'])}, "
               f"{len(rec['findings'])} finding(s)")]
    if ch:
        lines.append(L(f"ClawHub 的结论：{ch['status']}" + (f"（{'、'.join(ch['reasons'])}）" if ch["reasons"] else ""),
                       f"ClawHub's verdict: {ch['status']}" + (f" ({', '.join(ch['reasons'])})" if ch["reasons"] else "")))
    for f in rec["findings"][:6]:
        lines.append(f"{f['severity']} · {f['file']}:{f['line']} · {f['description']}")
    files = rec.get("files") or []
    shown = "、".join(f[:60] for f in files[:15]) + ("…" if len(files) > 15 else "")
    lines.append(L(f"文件（{len(files)} 个）：{shown}", f"Files ({len(files)}): {shown.replace('、', ', ')}"))
    if env := rec.get("env"):
        from . import skill_gate
        keys = [v for v in env if skill_gate.secretish(v)]
        lines.append(L(f"它要的环境变量：{'、'.join(env[:10])}" + ("（像密钥的不会自动给它：要在这个技能的设置里给）" if keys else ""),
                       f"Environment variables it asks for: {', '.join(env[:10])}" + (" (secret-looking ones aren't handed over: give them "
                                                                                       "in this skill's settings)" if keys else "")))
    act = L("更新", "Update") if rec["op"] == "update" else L("装", "Install")
    fence = "```"
    detail = (L("同意后，服务器把隔离区里这一份（指纹 ", "If you approve, the server installs exactly the quarantined copy (fingerprint ")
              + rec["hash"][:23] + L("）原样装进去；装的时候再扫一遍。它的脚本不会在装的时候跑；技能和别的技能一样只是写给 Agent 看的做法。",
                                     "); it's scanned again first. None of its scripts run on install; like any skill it's instructions for "
                                     "the Agent.")
              + "\n\nSKILL.md\n" + fence + "\n" + rec.get("preview", "").replace(fence, "ʼʼʼ") + "\n" + fence)
    why = L(f"{who}想{act.lower()}技能「{rec['name']}」（{ver[:12]}）。市场来的技能按不受信的扫过：{verdict_word(rec['verdict'])}。",
            f"{who} wants to {act.lower()} the skill \"{rec['name']}\" ({ver[:12]}). Market skills are scanned as untrusted: "
            f"{verdict_word(rec['verdict'])}.")
    return {"title": L(f"{act}技能：{rec['name']}", f"{act} skill: {rec['name']}"), "why": why, "changes": lines, "detail": detail,
            "approveLabel": L("装上", "Install") if rec["op"] == "install" else L("更新", "Update")}


def remove_card(name: str, target: str, who: str) -> dict:
    return {"title": L(f"卸载技能：{name}", f"Remove skill: {name}"),
            "why": L(f"{who}想卸载技能市场装的「{name}」（{where(target)}）。卸载之前整份留一版，能恢复。",
                     f"{who} wants to remove \"{name}\" ({where(target)}), installed from the skill market. A copy is kept first, so it can be "
                     "restored."),
            "changes": [L(f"拿掉：{target_root(target) / name}", f"Removes: {target_root(target) / name}")],
            "detail": L("同意后，服务器先把它整份存一版（engine/versions.py），再拿掉、从 lock 里去掉。", "If you approve, the server keeps a full "
                        "copy first (engine/versions.py), then removes it and drops it from the lock."),
            "approveLabel": L("卸载", "Remove")}


async def _approved(name: str, args: dict, row) -> str:
    """kind tool 的卡点了同意（engine/calls.py）：装 / 更新照 staged 装（指纹对卡上的），卸载照做。"""
    op = str(args.get("op") or "")
    try:
        if op in ("install", "update"):
            out = await asyncio.to_thread(install_staged, str(args.get("staged") or ""), who=str(row["agent"] or "owner"),
                                          expect_hash=str(args.get("hash") or ""))
            return L(f"装好了：{out['name']}（{where(out['target'])}，{(out['commit'] or out['version'] or '-')[:12]}，扫描 {out['verdict']}）。"
                     "下一轮的技能目录里就有它。",
                     f"Installed {out['name']} ({where(out['target'])}, {(out['commit'] or out['version'] or '-')[:12]}, scan {out['verdict']}). "
                     "It's in the skills list from the next turn.")
        if op == "remove":
            out = await asyncio.to_thread(remove, name, target=str(args.get("target") or "user"), who=str(row["agent"] or "owner"))
            return L(f"卸载了 {name}" + (f"（留了一版 {out['kept']}）" if out["kept"] else ""), f"Removed {name}" + (f" (kept version {out['kept']})"
                     if out["kept"] else ""))
    except MarketError as e:
        raise ToolFailure(str(e)) from None
    raise ToolFailure(L("认不出这张卡要做什么", "Can't tell what this card does"))


calls.register(PREFIX, _approved)


# —— Agent 的工具 ——————————————————————————————————————————————————————————————————

def _lines(found: dict) -> str:
    if not found["hits"]:
        out = L("没搜到。", "Nothing found.")
    else:
        out = "\n".join(f"- {h['id']} — {h['name']}" + (f": {' '.join(h['description'].split())[:160]}" if h["description"] else "")
                        + (f" ({h['version']})" if h["version"] else "") for h in found["hits"])
    if found["failed"]:
        out += "\n" + L("这几家这次没搜成：", "These sources failed this time: ") + "; ".join(f"{f['source']} ({f['error']})" for f in found["failed"])
    return out + "\n" + L("搜到的是外面的人写的，不等于能装：用户说要装才用 install（会出一张卡，扫描结论写在上面，他点了才装）。",
                          "These are written by other people; finding one isn't permission: use install only when the user asks (it makes a "
                          "card with the scan verdict, and installs only when they approve).")


async def _door(ctx: ToolCtx, ident: str) -> str | None:
    """Agent 给的地址在出卡之前就会去下（url:、不在 engine.skills.market.sites 里的 well-known:）：先过 Doorman（同 web_fetch：网址里带着私事、
    像在往外带数据的扣下）。配置里的来源不过。→ None = 放行；字符串 = 没去的说明。"""
    from . import web
    try:
        src, rest = parse_id(ident)
    except MarketError:
        return None  # prepare 会说认不出
    if src == "url":
        return await web.door(ctx, rest.strip(), "request")
    if src == "well-known":
        site, _, name = rest.partition("#")
        site = site.strip().rstrip("/")
        if site in [str(x).rstrip("/") for x in cfg()["sites"]]:
            return None
        return await web.door(ctx, f"{site}/.well-known/skills/{name.strip()}", "request")
    return None


def tool(me: str):
    """skill_market 工具（engine.skills.market.enabled 才有）。"""
    if not enabled():
        return None
    from . import tools as tools_mod

    async def run(args: dict, ctx: ToolCtx) -> str:
        action = str(args.get("action") or "").strip()
        if action == "search":
            q = str(args.get("query") or "").strip()
            if not q:
                raise ToolFailure(L("search 要写 query", "search needs a query"))
            from . import web
            if held := await web.door(ctx, f"{cfg()['clawhub']}/api/v1/search?{urlencode({'q': q})}", "search"):  # 这句话会发给几家市场
                return held
            found = await search(q, sources=[s for s in args.get("sources") or [] if s in SEARCH] or None)
            return tools_mod.wrap_untrusted("skill_market", _lines(found))
        if action == "list":
            rows = installed()
            if not rows:
                return L("还没有从技能市场装过技能。", "Nothing installed from the skill market yet.")
            return tools_mod.wrap_untrusted("skill_market", "\n".join(  # 标识里有外面的网址
                f"- {r['name']} ({where(r['target'])}): {r['id']} {(r.get('commit') or r.get('version') or '')[:12]}"
                + (L("（本地改过）", " (changed locally)") if r["localChanges"] else "") for r in rows))
        raise ToolFailure(L("install / update / remove 要用户点头：这次调用会交一张卡", "install / update / remove need the user's OK"))

    async def ask_user(args: dict, ctx: ToolCtx) -> dict | str:
        import inbox
        action = str(args.get("action") or "").strip()
        who = inbox.source_name(ctx.agent or "main", inbox.names())
        target = str(args.get("agent") or "").strip() or "user"
        try:
            if action == "install":
                if held := await _door(ctx, str(args.get("id") or "")):
                    return held
                rec = await prepare(str(args.get("id") or ""), target=target)
            elif action == "update":
                res = await prepare_update(str(args.get("name") or ""), target=target)
                if res["status"] == "current":
                    return L(f"{res['name']} 已经是最新的了。", f"{res['name']} is already up to date.")
                if res["status"] == "local-changes":
                    return L(f"没更新：{res['name']} 本地改过（和装的时候不一样了），更新会盖掉这些改动。跟用户说一声。",
                             f"Not updated: {res['name']} was changed locally since it was installed, and updating would overwrite that. "
                             "Tell the user.")
                rec = res
            elif action == "remove":
                name = str(args.get("name") or "").strip()
                if lock_key(target, name) not in read_lock()["installed"]:
                    return L(f"没卸载：{name} 不是技能市场装的（lock 里没有）。", f"Not removed: {name} wasn't installed from the skill market.")
                return await calls.ask(PREFIX, name, {"op": "remove", "target": target}, ctx, remove_card(name, target, who))
            else:
                return L("action 只能是 search / list / install / update / remove", "action must be search, list, install, update or remove")
        except MarketError as e:  # 里面有包里的路径、市场回的字
            return L("没装：", "Not installed: ") + tools_mod.wrap_untrusted("skill_market", str(e)[:600])
        if rec["verdict"] == "dangerous":
            _drop_staged(rec["staged"])
            reasons = "; ".join(f"{f['file'][:80]}:{f['line']} {f['description']}" for f in rec["findings"][:4])
            return (L("没装：扫描结论是 dangerous。危险的技能不装，也不出卡。跟用户说一声，别换个办法装。扫出来的：",
                      "Not installed: the scan verdict is dangerous. Dangerous skills aren't installed and get no card. Tell the user; don't "
                      "try another way. What the scan found: ") + tools_mod.wrap_untrusted("skill_market", reasons))
        return await calls.ask(PREFIX, rec["name"], {"op": rec["op"], "staged": rec["staged"], "hash": rec["hash"], "target": rec["target"]},
                               ctx, card(rec, who))

    def classify(args: dict) -> tuple[str, str]:
        a = str(args.get("action") or "")
        if a in ("search", "list"):
            return tools_mod.READ, "只读：搜技能市场、列出装过的"
        return tools_mod.ASK, "装 / 更新 / 卸载外来的技能：交 kind tool 的卡，主人点了才做（engine/skill_market.py）"

    return calls.AskTool(
        name=TOOL, kinds=("owner",), run=run, ask_user=ask_user, classify=classify, timeout=120,
        title=lambda a: f"skill_market {a.get('action') or ''} {a.get('query') or a.get('id') or a.get('name') or ''}".strip(),
        description=L(
            "技能市场（ClawHub、GitHub、skills.sh、照 agentskills.io 格式发布的网站）：search 按一句话搜（只读）；list 列出装过的；install 装（id 写搜出来的"
            " id；agent 写某个 Agent 的 id 就装进它的工作区，不写 = 所有 Agent 共用）、update、remove 都会交一张卡给用户（扫描结论写在上面），他点了才做。"
            "用户要的事你现有的技能、工具都做不了时，说「做不到」之前先 search 一下；搜到了不等于能装：用户说要装才 install。危险的技能不装。",
            "The skill market (ClawHub, GitHub, skills.sh, sites publishing in the agentskills.io format): search by a sentence (read-only); "
            "list what's installed; install (id from search; agent = one Agent's id to put it in that workspace, leave out to share it with "
            "every Agent), update and remove each send the user a card with the scan verdict and only happen once they approve. When "
            "nothing you have can do what the user wants, search before saying you can't; finding a skill isn't permission: install "
            "only when the user asks. Dangerous skills are never installed."),
        schema={"type": "object", "required": ["action"], "properties": {
            "action": {"type": "string", "enum": ["search", "list", "install", "update", "remove"]},
            "query": {"type": "string", "description": L("search：要做的事（一句话）", "search: the job at hand, in a sentence")},
            "sources": {"type": "array", "items": {"type": "string", "enum": list(SOURCES)},
                        "description": L("search：只搜这几家（不写 = 全部）", "search: only these sources (default all)")},
            "id": {"type": "string", "description": L("install：搜出来的 id（clawhub:@作者/slug、github:owner/repo/路径、skills.sh:…、well-known:…、url:…）",
                                                      "install: the id from search (clawhub:@owner/slug, github:owner/repo/path, skills.sh:…, "
                                                      "well-known:…, url:…)")},
            "name": {"type": "string", "description": L("update / remove：技能的名字", "update / remove: the skill's name")},
            "agent": {"type": "string", "description": L("装进哪个 Agent 的工作区（不写 = 所有 Agent 共用的技能目录）",
                                                         "Which Agent's workspace (default: the folder every Agent shares)")}}})


# —— 设置接口（只认主人的设备）———————————————————————————————————————————————————————————

def _target(b: dict) -> str:
    t = str(b.get("agent") or "").strip()
    return t or "user"


def _err(e: MarketError, status: int = 400) -> SetupError:
    return SetupError(status, str(e))


@router.get("/api/setup/skills/market/search")
async def api_search(q: str = "", sources: str = "", limit: int = SEARCH_LIMIT):
    """几家一起搜：{"hits", "failed", "searched"}。"""
    if not q.strip():
        raise SetupError(400, L("写 q：要搜什么", "q is required: what to search for"), field="q")
    srcs = [s for s in sources.split(",") if s in SEARCH] or None
    return {"ok": True, **await search(q.strip()[:200], sources=srcs, limit=limit)}


@router.post("/api/setup/skills/market/preview")
async def api_preview(request: Request):
    """{"id", "agent"?}：下载、隔离、扫描 → staged（还没装）。dangerous 的照样回结论，但 install 不收。"""
    b = await setup_api.body_of(request)
    try:
        rec = await prepare(str(b.get("id") or ""), target=_target(b))
    except MarketError as e:
        raise _err(e) from None
    if rec["verdict"] == "dangerous":
        _drop_staged(rec["staged"])
        rec = {**rec, "staged": None}
    return {"ok": True, **rec, "installable": rec["staged"] is not None}


@router.post("/api/setup/skills/market/install")
async def api_install(request: Request):
    """{"staged"}：主人确认，装（照样再核对一遍指纹、再扫一遍）。"""
    b = await setup_api.body_of(request)
    try:
        out = await asyncio.to_thread(install_staged, str(b.get("staged") or ""), who="owner")
    except MarketError as e:
        raise _err(e, 409) from None
    import chat
    chat.log_activity(L(f"从技能市场装了「{out['name']}」", f'Installed "{out["name"]}" from the skill market'), "edit")
    return {"ok": True, **out}


@router.get("/api/setup/skills/market/installed")
def api_installed():
    return {"ok": True, "items": installed()}


@router.post("/api/setup/skills/market/update")
async def api_update(request: Request):
    """{"name", "agent"?}：比上游的指纹 → {"status": "current" | "local-changes" | "staged", …}；staged 的再 POST …/install 装。"""
    b = await setup_api.body_of(request)
    try:
        res = await prepare_update(str(b.get("name") or ""), target=_target(b))
    except MarketError as e:
        raise _err(e) from None
    if res.get("status") == "staged" and res["verdict"] == "dangerous":
        _drop_staged(res["staged"])
        res = {**res, "staged": None}
    return {"ok": True, **res}


@router.delete("/api/setup/skills/market/installed/{name}")
def api_remove(name: str, agent: str = ""):
    try:
        out = remove(name, target=agent.strip() or "user", who="owner")
    except MarketError as e:
        raise _err(e, 404) from None
    import chat
    chat.log_activity(L(f"卸载了技能市场装的「{name}」", f'Removed "{name}" (installed from the skill market)'), "deleted")
    return {"ok": True, **out}
