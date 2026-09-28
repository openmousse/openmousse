"""你的 claw：真正跑 agent 的那一个。OpenMousse 自己不带 agent，接在它旁边（2026-09-28 起不只 OpenClaw）。

server.json 的 claw 段（没有这一段 = OpenClaw，和以前完全一样）：
  kind      "openclaw"（默认）| "openai"：任何有 OpenAI 兼容对话接口（POST <url>/chat/completions，SSE 流式）的 claw 或 agent
  name      界面上怎么叫它（默认 OpenClaw / "your claw"）
  url       openai：接口的根，一直写到 /v1，比如 http://127.0.0.1:8642/v1
  token     openai：Bearer 令牌；也可以写 token_env = 环境变量名（先看进程环境，再看 claw 段的 env_file，再看 server.json 的 env_file）
  model     openai：请求里的 model（不写 = "default"；写成空字符串 = 请求里不带 model，nanobot 要这样）
  models    openai：app 里能切换的模型（不给就只有 model 一个，app 不显示切换）
  session   openai：一个对话怎么接上一句
              {"mode": "history", "turns": 40}  默认：每次把这个对话今天的记录（最多 turns 轮）一起发过去。给没有会话的接口用
                                                （比如直接接一个模型的 API）；撤回 = 删 app 这边的记录，下一轮就不带了
              {"mode": "header", "header": "X-Session-Id"}  claw 自己记会话：会话键放进这个请求头，每次只发新的一句（Hermes、Letta）
              {"mode": "body", "field": "session_id"}        会话键放进请求体的这个字段（nanobot）
              {"mode": "user"}                  会话键放进 OpenAI 的 user 字段
            后三种的会话键是 mousse:<对话>:<逻辑日>，每天 04:00 换新的（多数 claw 自己不按天重置；前一天靠日结接上）；
            "daily": false 就一直用 mousse:<对话>
  headers   openai：额外的请求头（有的 claw 用它选 agent、选工作区）
  常见 claw 的现成配置（Hermes、nanobot、Letta）在 claw_presets.py，安装时答它的名字就行
  skills / rules   只给安装器用：claw 的 skills 文件夹（OpenMousse 的 skill 软链进去）、它每轮都读的规则文件（AGENTS.md 之类，规矩追加进去）

OpenClaw 以外的 claw 能做的（caps()，/api/health 带给 app，做不到的入口 app 藏起来）：
  对话、停止、排队（回复进行中发的回完合成一轮）、附件、推送、看板 / 目标 / 日程 / Zen / 收藏 / 世界树（都是 OpenMousse 自己的）；
  Agent = 同一个 claw 的一个单独会话，每天第一句话前面带上它的名字和职责（见 role_context）。
  做不到：插话（steer）、Agent 各自独立的工作区和 skills、后台任务 / 执行审批 / 定时任务 / 模型计费这些读 OpenClaw 的页面。
"""
from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Callable
from pathlib import Path

import httpx

from config import raw, settings

KINDS = ("openclaw", "openai")
DEFAULT_TURNS = 40
# OpenClaw 的 channels 里不是人聊天的：a2a 是 agent 之间的协议（2026.9 的 onboard 默认就开着），不算「主对话和 X 共用」，也不进「连接」页
NOT_CHAT_CHANNELS = frozenset({"a2a"})
CHANNEL_NAMES = {"telegram": "Telegram", "discord": "Discord", "whatsapp": "WhatsApp", "slack": "Slack", "imessage": "iMessage",
                 "googlechat": "Google Chat", "msteams": "Microsoft Teams", "irc": "IRC", "sms": "SMS", "line": "LINE",
                 "nextcloud-talk": "Nextcloud Talk", "synology-chat": "Synology Chat"}


def channel_name(key: str) -> str:
    return CHANNEL_NAMES.get(key, key.capitalize())


class ClawError(RuntimeError):
    """claw 那边出的错（连不上、HTTP 不是 200、流里报错）：一轮回复记成没拿到。"""


def cfg() -> dict:
    c = raw().get("claw")
    return c if isinstance(c, dict) else {}


def kind() -> str:
    k = str(cfg().get("kind") or "openclaw").strip().lower()
    return k if k in KINDS else "openclaw"


def is_openclaw() -> bool:
    return kind() == "openclaw"


def name() -> str:
    return str(cfg().get("name") or "").strip() or ("OpenClaw" if is_openclaw() else "your claw")


def base_url() -> str:
    return str(cfg().get("url") or "").strip().rstrip("/")


def model() -> str:
    return str(cfg().get("model") or "").strip() or "default"


def send_model() -> bool:
    """请求里带不带 model：claw 段写了 "model": ""（空字符串）就不带（nanobot 只认它自己配的模型，不带最省事）。"""
    c = cfg()
    return not ("model" in c and not str(c.get("model") or "").strip())


def models() -> list[str]:
    """app 里能选的模型（通用 claw）：models 列表，没有就只有 model 一个。"""
    ms = [str(m).strip() for m in cfg().get("models") or [] if str(m).strip()]
    return ms or [model()]


def session_cfg() -> dict:
    s = cfg().get("session")
    return s if isinstance(s, dict) else {"mode": s} if isinstance(s, str) else {}


def session_mode() -> tuple[str, str, int]:
    """(mode, name, turns)。mode：history（默认）| header | body | user；name = 请求头名（header）或请求体字段名（body）。"""
    s = session_cfg()
    mode = str(s.get("mode") or "history").lower()
    if mode not in ("history", "header", "body", "user"):
        mode = "history"
    name_ = str(s.get("header") or "X-Session-Id") if mode == "header" else str(s.get("field") or "session_id") if mode == "body" else ""
    try:
        turns = max(1, min(200, int(s.get("turns") or DEFAULT_TURNS)))
    except (TypeError, ValueError):
        turns = DEFAULT_TURNS
    return mode, name_, turns


def day_key(key: str, day: str) -> str:
    """claw 自己记会话时（header / body / user）交给它的会话键：默认按逻辑日换新的（多数 claw 没有每天重置，app 一天一页）。"""
    mode, _, _ = session_mode()
    if mode == "history" or session_cfg().get("daily") is False:
        return key
    return f"{key}:{day}"


def env_value(name_: str) -> str:
    """环境变量的值：先看进程环境，再看 server.json 的 env_file（每次读，改了不用重启；没写就是 settings.env_file），KEY=VALUE 一行一个。
    读不到 = 空。不打印、不记日志。"""
    if not name_:
        return ""
    if os.environ.get(name_):
        return os.environ[name_]
    files = [Path(str(cfg()["env_file"])).expanduser()] if cfg().get("env_file") else []  # 比如 Hermes 的 ~/.hermes/.env
    files.append(Path(str(raw().get("env_file"))).expanduser() if raw().get("env_file") else settings.env_file)
    for env_file in files:
        try:
            for line in env_file.read_text(encoding="utf8").splitlines():
                k, sep, v = line.strip().removeprefix("export ").partition("=")
                if sep and k.strip() == name_:
                    return v.strip().strip("'\"")
        except OSError:
            continue
    return ""


def token() -> str:
    c = cfg()
    return str(c.get("token") or "").strip() or env_value(str(c.get("token_env") or "").strip())


def headers() -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    extra = cfg().get("headers")
    if isinstance(extra, dict):
        h.update({str(k): str(v) for k, v in extra.items()})
    if tok := token():
        h["Authorization"] = f"Bearer {tok}"
    return h


def caps() -> dict:
    """这个 claw 能做什么（/api/health 的 claw.caps）。app 按它藏做不到的入口。"""
    if is_openclaw():
        return {"steer": True, "agentWorkspaces": True, "tasks": True, "approvals": True, "cron": True, "billing": True,
                "channels": True, "rewind": True, "modelSwitch": True}
    mode, _, _ = session_mode()
    return {"steer": False, "agentWorkspaces": False, "tasks": False, "approvals": False, "cron": False, "billing": False,
            "channels": False, "rewind": mode == "history", "modelSwitch": len(models()) > 1}


def info() -> dict:
    return {"kind": kind(), "name": name(), "caps": caps()}


# —— 通用 claw：OpenAI 兼容接口 ——————————————————————————————————————————————

def history(conn: sqlite3.Connection, thread: str, before_id: int, since: str, turns: int) -> list[dict]:
    """history 模式要一起发过去的记录：这个对话里 before_id 之前、since（今天的逻辑日开始）之后的往来，最多 turns 轮。
    用户那边发给模型的原文（gw_text：带了附件抽出的字、前情）优先；回复里没拿到的、停了的照样带（模型知道上一轮怎么了）；
    自动触发的（role auto）算用户说的。"""
    rows = conn.execute(
        "SELECT role, text, gw_text FROM messages WHERE thread=? AND id<? AND ts>=? AND role IN ('user','auto','grava') ORDER BY id DESC LIMIT ?",
        (thread, before_id, since, turns * 2)).fetchall()
    out: list[dict] = []
    for r in reversed(rows):
        role = "assistant" if r["role"] == "grava" else "user"
        text = (r["gw_text"] if role == "user" and r["gw_text"] else r["text"]) or ""
        if out and out[-1]["role"] == role:  # 连着两条同一边的（排队合成过、没拿到回复）并成一条，接口不用处理奇怪的顺序
            out[-1]["content"] += "\n\n" + text
        else:
            out.append({"role": role, "content": text})
    while out and out[0]["role"] != "user":  # 从用户那句开始
        out.pop(0)
    return out


def request_body(messages: list[dict], key: str, model_: str | None, stream: bool) -> tuple[dict, dict[str, str]]:
    mode, field, _ = session_mode()
    body: dict = {"stream": stream, "messages": messages}
    if send_model():
        body["model"] = model_ or model()
    h = headers()
    if mode == "header":
        h[field] = key
    elif mode == "body":
        body[field] = key
    elif mode == "user":
        body["user"] = key
    return body, h


async def stream(messages: list[dict], key: str, model_: str | None, on_delta: Callable[[str], None], timeout: float = 600) -> None:
    """发给 claw、流式把回复一段段交给 on_delta。接口不支持流式、直接回整段 JSON 的也认。出错抛 ClawError / httpx.HTTPError。"""
    url = base_url()
    if not url:
        raise ClawError("server.json 的 claw.url 没填 / claw.url is not set in server.json")
    body, h = request_body(messages, key, model_, True)
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=10)) as client:
        async with client.stream("POST", f"{url}/chat/completions", headers=h, json=body) as r:
            if r.status_code != 200:
                raw_ = (await r.aread()).decode("utf8", "replace")
                raise ClawError(f"{name()} HTTP {r.status_code}: {raw_[:300]}")
            if "text/event-stream" not in r.headers.get("content-type", ""):
                j = json.loads((await r.aread()).decode("utf8", "replace") or "{}")
                if "error" in j:
                    raise ClawError(str((j["error"] or {}).get("message") if isinstance(j["error"], dict) else j["error"]))
                text = ((j.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
                if text:
                    on_delta(text)
                return
            async for line in r.aiter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    j = json.loads(payload)
                except ValueError:
                    continue
                if j.get("error"):
                    e = j["error"]
                    raise ClawError(str(e.get("message") if isinstance(e, dict) else e) or f"{name()} error")
                for ch in j.get("choices") or []:
                    delta = (ch.get("delta") or {}).get("content")
                    if isinstance(delta, str) and delta:
                        on_delta(delta)


async def complete(prompt: str, key: str, model_: str | None = None, timeout: float = 300) -> str:
    """一问一答（Zen 的「想完了」之类，不进任何对话）。"""
    parts: list[str] = []
    await stream([{"role": "user", "content": prompt}], key, model_, parts.append, timeout)
    return "".join(parts)


async def probe(timeout: float = 5) -> tuple[bool, str]:
    """连得上吗：GET <url>/models（多数 OpenAI 兼容接口都有；没有这个接口、回 404 也算连得上）。→ (ok, 一句说明)。"""
    url = base_url()
    if not url:
        return False, "claw.url is not set"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(f"{url}/models", headers=headers())
    except httpx.HTTPError as e:
        return False, f"{type(e).__name__}: {e}"[:200]
    if r.status_code in (401, 403):
        return False, f"HTTP {r.status_code} (token?)"
    return r.status_code < 500, f"HTTP {r.status_code}"
