#!/usr/bin/env python3
"""发版前的隐私扫描（2026-10-20）：扫 `git ls-files` 的每一个文件（图片只看元数据），报像密钥、像私人信息的东西。只用标准库，能反复跑。

  python3 tools/release/privacy_scan.py [--root 仓库] [--words 名单] [--secrets .env 或 server.json]… [--json]

报什么（每一条：文件:行、哪一类、一句为什么；假值一眼看得出的放过，见 FAKE）：
  key       像密钥的串：sk-、ghp_ / github_pat_、AKIA / ASIA、xox?-、AIza、JWT、私钥块、Stripe、Telegram 机器人令牌
  path      本机的路径：/home/<名字>、/Users/<名字>、C:\\Users\\<名字>、~/openmousse-wt 这类开发者本机的工作目录
  tailnet   Tailscale 的地址：100.64.0.0/10 里的 IP、*.ts.net 主机名
  email     邮箱：example.* / .invalid / .test / openmousse.ai 这些以外的
  phone     电话号码：+ 开头的国际写法、中国大陆的手机号、英国的 07 手机号
  word      名单里的词（真名、学校、住址……）：名单从仓库外的本机文件读（--words，或环境变量 MOUSSE_PRIVACY_WORDS 指过去），
            一行一个词或短语（不分大小写，空行和 # 开头的不算）；没给就跳过这一项。只比指纹（sha256），命中只说「名单第几个词」
  secret    真的密钥的值：从给的 .env / server.json（或世界树的 config.json）读（--secrets，可以给几次，或环境变量 MOUSSE_PRIVACY_SECRETS，
            路径用 : 隔开）；键名像密钥的值、当键用的令牌都算，环境变量的名字不算。只比指纹，命中只说是哪个键，值永远不打印
  image     图片里带的文字块（PNG 的 tEXt / zTXt / iTXt / eXIf，JPEG 的 EXIF / XMP）：有就报一条，只说是哪一块、几个字（再按上面几类扫里面的字）
每一条只说哪一类、为什么，不打印碰到的那段字（邮箱不打域名，密钥只说多长）。
不扫：.git 里的东西、不在 git 里的文件；别的二进制文件只报一行「没扫」。
退出码：0 没有发现；1 有发现（一条一行打出来，--json 打成一个 JSON）；2 参数不对、读不了名单或密钥文件。
"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import struct
import subprocess
import sys
import zlib
from dataclasses import dataclass
from pathlib import Path

KEYS = (
    ("OpenAI / Anthropic 风格的密钥（sk-）", re.compile(r"\bsk-(?:ant-|proj-|live-)?[A-Za-z0-9_-]{20,}")),
    ("GitHub 令牌", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})")),
    ("AWS 访问密钥", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("Slack 令牌", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("Google API 密钥", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
    ("私钥块", re.compile(r"-----BEGIN (?:[A-Z]+ )*PRIVATE KEY-----")),
    ("Stripe 密钥", re.compile(r"\b(?:sk|rk)_live_[A-Za-z0-9]{16,}")),
    ("Telegram 机器人令牌", re.compile(r"\b\d{8,10}:AA[A-Za-z0-9_-]{33}\b")),
)
# 一眼看得出是假的：写着 fake / test / example、全是同一个字、一串顺子、带占位的尖括号或省略号……
FAKE = re.compile(r"(?i)fake|test|dummy|example|sample|placeholder|your|demo|mock|redacted|changeme|secret|wrong|xxxx|abcdef|abc123|123456|"
                  r"9876543|(.)\1{5,}|\.\.\.|…|<[^>]*>|\*{3,}|not[-_]?real")
HOME_PATH = re.compile(r"(?:/home/|/Users/|[A-Za-z]:\\\\?Users\\\\?)([A-Za-z0-9._-]+)")
HOME_OK = {"user", "username", "you", "me", "runner", "ubuntu", "root", "pi", "node", "app", "mousse", "openmousse", "example", "name", "someone",
           "test", "tester", "demo", "sandbox", "alex", "bob", "carol", "mira", "mia", "noor", "lina", "sam", "dev", "shared", "linuxbrew"}
LOCAL_PATH = re.compile(r"openmousse-wt\b|~/Desktop/|~/Documents/[A-Za-z]")
TS_NET = re.compile(r"\b(?:[A-Za-z0-9-]+\.)+ts\.net\b", re.I)
IPV4 = re.compile(r"(?<![\d.])(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?![\d.])")
TAILNET = ipaddress.ip_network("100.64.0.0/10")
EMAIL = re.compile(r"(?<![\w.+-])([A-Za-z0-9][A-Za-z0-9._%+-]*)@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,})(?![\w.-]*\d)")  # pkg@1.2.3 不算
EMAIL_OK = re.compile(r"(?i)(?:^|\.)(?:example\.(?:com|org|net)|example|invalid|test|localhost|local|openmousse\.ai)$")
NOT_EMAIL_DOMAIN = re.compile(r"(?i)\.(?:png|jpe?g|gif|webp|svg|ico|js|ts|tsx|json|md|py|css)$")
PHONE = re.compile(r"(?<![\w+])(?:\+\d{1,3}[ -]?\(?\d{1,4}\)?(?:[ -]?\d{2,4}){2,4}|1[3-9]\d{9}|07\d{3} ?\d{6})(?![\w])")
PHONE_OK = re.compile(r"\+?1[ -]?\(?555|\+44 ?7700 ?900|\+?44 ?20 ?7946 ?0|13800138000|(\d)\1{5}|1234567|0123456")
CJK = "\u3400-\u9fff\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af"
UNITS = re.compile(rf"[a-z0-9]+|[{CJK}]")
TEXT_EXT = {".png": "png", ".jpg": "jpeg", ".jpeg": "jpeg"}
ENV_NAME = re.compile(r"[A-Z][A-Z0-9_]*")  # 环境变量的名字（key_env 这类）：不是密钥，仓库里本来就写着
# 看过、留着的（文件, 哪一类, 为什么）：照样数，不当发现
ALLOW = (
    ("app/package-lock.json", "email", "npm 包自带的弃用说明里写的维护者邮箱（npm 生成的文件）"),
    ("tools/release/privacy_scan.py", "path", "这个脚本自己的规则"),
    *(("server/tests/test_privacy_scan.py", k, "这个脚本的测试：编的值，故意让它报") for k in ("path", "tailnet", "email", "phone")),
    ("server/tests/test_mcp_security.py", "tailnet", "Host 检查的测试：编的 Tailscale 网段地址，测的就是这个网段"),
    ("tree/tests/test_secrets.py", "key", "世界树隐去密钥的测试：编的密钥，要长得像真的才认得出"),
)


@dataclass
class Hit:
    file: str
    line: int
    kind: str
    why: str

    def show(self) -> str:
        return f"{self.file}:{self.line}: [{self.kind}] {self.why}"


# —— 名单和密钥：只留指纹 ———————————————————————————————————————————————————————

def fp(s: str) -> str:
    return hashlib.sha256(s.encode("utf8")).hexdigest()


def units(text: str) -> list[str]:
    """一行字拆成「单位」：一串拉丁字母和数字算一个，一个汉字（假名、谚文）算一个。名单里的词按同样拆，比的是单位连起来的指纹。"""
    return UNITS.findall(text.casefold())


class Words:
    """名单：读进来就换成指纹（sha256），原文不留。first 是每个词第一个单位的指纹（先筛一下，不用每个位置都算）。"""

    def __init__(self, lines: list[str]) -> None:
        self.fps: dict[str, int] = {}
        self.first: dict[str, set[int]] = {}
        n = 0
        for raw in lines:
            s = raw.strip()
            if not s or s.startswith("#"):
                continue
            n += 1
            us = units(s)
            if not us:
                continue
            self.fps.setdefault(fp(" ".join(us)), n)
            self.first.setdefault(fp(us[0]), set()).add(len(us))
        self.count = n

    def find(self, text: str) -> list[int]:
        us = units(text)
        out = []
        for i, u in enumerate(us):
            sizes = self.first.get(fp(u))
            if not sizes:
                continue
            for k in sizes:
                hit = self.fps.get(fp(" ".join(us[i:i + k]))) if i + k <= len(us) else None
                if hit is not None:
                    out.append(hit)
        return out


def secret_values(path: Path) -> list[tuple[str, str]]:
    """一个 .env 或 server.json 里的密钥：[(键, 值)]。值只在内存里，马上换成指纹。"""
    text = path.read_text(encoding="utf8")
    out: list[tuple[str, str]] = []
    if path.suffix == ".json":
        def walk(v, key: str) -> None:
            secretish = re.search(r"(?i)token|key|secret|password|auth|pass", key)
            if isinstance(v, dict):
                for k, x in v.items():
                    if secretish and re.fullmatch(r"[A-Za-z0-9_-]{16,}", str(k)):
                        out.append((f"{key}.<键>", str(k)))  # 令牌当键用的（世界树 config.json 的 tokens：令牌 → 平台名）
                    walk(x, f"{key}.{k}" if key else str(k))
            elif isinstance(v, list):
                for i, x in enumerate(v):
                    walk(x, f"{key}[{i}]")
            elif isinstance(v, str) and secretish and len(v) >= 8 and not v.startswith("${") and not ENV_NAME.fullmatch(v):
                out.append((key, v))
        walk(json.loads(text), "")
    else:
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.removeprefix("export ").partition("=")
            v = v.strip().strip("'\"")
            if len(v) >= 8 and not ENV_NAME.fullmatch(v):
                out.append((k.strip(), v))
    return out


class Secrets:
    def __init__(self, paths: list[Path]) -> None:
        self.fps: dict[str, str] = {}
        for p in paths:
            for key, v in secret_values(p):
                for piece in {v, *re.findall(r"[A-Za-z0-9_-]{12,}", v)}:
                    self.fps.setdefault(fp(piece), f"{key}（{p.name}）")

    def find(self, text: str) -> list[str]:
        return [self.fps[h] for h in (fp(r) for r in re.findall(r"[A-Za-z0-9_-]{12,}", text)) if h in self.fps] if self.fps else []


# —— 图片的元数据 ————————————————————————————————————————————————————————————————

def label(key: bytes) -> str:
    """文字块的键（Software、Comment……）：只在是普通的英文词时打出来，别的写「?」。"""
    k = key.decode("latin-1", "replace")
    return k if re.fullmatch(r"[A-Za-z][A-Za-z :._-]{0,30}", k) else "?"


def png_text(data: bytes) -> list[tuple[str, str]]:
    """PNG 里的文字块（tEXt / zTXt / iTXt）和 eXIf：[(哪一块, 里面的字)]。"""
    out, i = [], 8
    while i + 8 <= len(data):
        n, kind = struct.unpack(">I4s", data[i:i + 8])
        body = data[i + 8:i + 8 + n]
        i += 12 + n
        k, _, rest = body.partition(b"\0")
        if kind == b"tEXt":
            out.append((f"tEXt {label(k)}", rest.decode("latin-1", "replace")))
        elif kind == b"zTXt":
            try:
                out.append((f"zTXt {label(k)}", zlib.decompress(rest[1:]).decode("latin-1", "replace")))
            except zlib.error:
                out.append((f"zTXt {label(k)}", ""))
        elif kind == b"iTXt":
            out.append((f"iTXt {label(k)}", rest.replace(b"\0", b" ").decode("utf8", "replace")))
        elif kind == b"eXIf":
            out.append(("eXIf", body.decode("latin-1", "replace")))
        elif kind == b"IEND":
            break
    return out


def jpeg_text(data: bytes) -> list[tuple[str, str]]:
    """JPEG 的 APP1（EXIF / XMP）和 COM 段：[(哪一段, 里面的字)]。"""
    out, i = [], 2
    while i + 4 <= len(data) and data[i] == 0xFF:
        marker = data[i + 1]
        if marker in (0xD9, 0xDA):
            break
        n = struct.unpack(">H", data[i + 2:i + 4])[0]
        body = data[i + 4:i + 2 + n]
        if marker in (0xE1, 0xFE):
            out.append(("EXIF / XMP" if marker == 0xE1 else "COM", body.decode("latin-1", "replace")))
        i += 2 + n
    return out


# —— 扫 ——————————————————————————————————————————————————————————————————————————

def line_hits(rel: str, no: int, line: str, words: Words | None, secrets: Secrets) -> list[Hit]:
    out: list[Hit] = []
    for what, rx in KEYS:
        for m in rx.finditer(line):
            if not FAKE.search(m.group(0)):
                out.append(Hit(rel, no, "key", f"{what}（{len(m.group(0))} 个字）"))
    for m in HOME_PATH.finditer(line):
        name = m.group(1).casefold()
        if name not in HOME_OK and len(name) > 1 and not name.startswith(("$", "<", "{", "%")):
            out.append(Hit(rel, no, "path", "家目录路径里有一个用户名"))
    if LOCAL_PATH.search(line):
        out.append(Hit(rel, no, "path", "本机的工作目录"))
    for m in TS_NET.finditer(line):
        if not re.search(r"(?i)example|<|tailnet|machine|xxx|\btail(?:0|1234)\.", m.group(0)):
            out.append(Hit(rel, no, "tailnet", "*.ts.net 主机名"))
    for m in IPV4.finditer(line):
        try:
            ip = ipaddress.ip_address(m.group(0))
        except ValueError:
            continue
        octets = [int(x) for x in m.groups()]
        fake = octets[2] == 0 or octets[1] == octets[2] or all(b - a == 1 for a, b in zip(octets[1:], octets[2:]))  # 100.64.0.1、100.100.100.200、100.101.102.103
        if ip in TAILNET and not fake and not line[m.end():m.end() + 1] == "/":
            out.append(Hit(rel, no, "tailnet", "Tailscale 网段里的 IP"))
    for m in EMAIL.finditer(line):
        dom = m.group(2)
        if m.group(1).lower() == "git" or EMAIL_OK.search(dom) or NOT_EMAIL_DOMAIN.search(dom) or FAKE.search(m.group(0)):
            continue
        out.append(Hit(rel, no, "email", "邮箱（不是 example 这类域名）"))
    for m in PHONE.finditer(line):
        if not PHONE_OK.search(m.group(0)):
            out.append(Hit(rel, no, "phone", "像电话号码"))
    if words is not None:
        for n in words.find(line):
            out.append(Hit(rel, no, "word", f"名单第 {n} 个词"))
    for key in secrets.find(line):
        out.append(Hit(rel, no, "secret", f"真的密钥：{key}"))
    return out


def scan(root: Path, words: Words | None, secrets: Secrets) -> tuple[list[Hit], list[str], int]:
    files = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True, check=True).stdout.decode("utf8").split("\0")
    hits: list[Hit] = []
    skipped: list[str] = []
    n = 0
    for rel in filter(None, files):
        p = root / rel
        if not p.is_file():
            continue
        n += 1
        data = p.read_bytes()
        kind = TEXT_EXT.get(p.suffix.lower())
        if kind:
            texts = png_text(data) if kind == "png" else jpeg_text(data)
            for where, t in texts:
                hits.append(Hit(rel, 0, "image", f"图片里带着文字（{where}，{len(t)} 个字）"))
                for line in t.splitlines():
                    hits += line_hits(rel, 0, line, words, secrets)
            continue
        if b"\0" in data[:8192]:
            skipped.append(rel)
            continue
        text = data.decode("utf8", "replace")
        for no, line in enumerate(text.splitlines(), 1):
            hits += line_hits(rel, no, line, words, secrets)
    return hits, skipped, n


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=".", help="仓库的根（默认当前目录）")
    ap.add_argument("--words", help="名单（默认读环境变量 MOUSSE_PRIVACY_WORDS）")
    ap.add_argument("--secrets", action="append", default=[], help=".env 或 server.json（可以给几次；也读 MOUSSE_PRIVACY_SECRETS）")
    ap.add_argument("--json", action="store_true", help="打成一个 JSON")
    a = ap.parse_args(argv)
    root = Path(a.root).resolve()
    words = None
    wpath = a.words or os.environ.get("MOUSSE_PRIVACY_WORDS", "").strip()
    try:
        if wpath:
            words = Words(Path(wpath).expanduser().read_text(encoding="utf8").splitlines())
        spaths = [Path(x).expanduser() for x in [*a.secrets, *filter(None, os.environ.get("MOUSSE_PRIVACY_SECRETS", "").split(os.pathsep))]]
        secrets = Secrets(spaths)
    except (OSError, ValueError, UnicodeDecodeError) as e:
        print(f"读不了名单或密钥文件：{type(e).__name__}", file=sys.stderr)
        return 2
    hits, skipped, n = scan(root, words, secrets)
    allowed = [h for h in hits if any(h.file == f and h.kind == k for f, k, _ in ALLOW)]
    hits = [h for h in hits if h not in allowed]
    notes = [("名单" + (f"：{words.count} 个词" if words else "：没给（MOUSSE_PRIVACY_WORDS），这一项跳过")),
             ("密钥" + (f"：{len(secrets.fps)} 个指纹" if secrets.fps else "：没给（--secrets / MOUSSE_PRIVACY_SECRETS），这一项跳过"))]
    if a.json:
        print(json.dumps({"files": n, "hits": [h.__dict__ for h in hits], "allowed": len(allowed), "skipped": skipped, "notes": notes},
                         ensure_ascii=False, indent=2))
    else:
        for h in hits:
            print(h.show())
        for s in skipped:
            print(f"{s}: [skip] 二进制文件，没扫")
        print(f"扫了 {n} 个文件，{len(hits)} 条发现（另有 {len(allowed)} 条看过、留着，见 ALLOW）；" + "；".join(notes))
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
