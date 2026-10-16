"""模型和接口：引用一律写成「provider/model」（和 OpenClaw 一样：anthropic/claude-opus-5-5、moonshot/kimi-k3、deepseek/deepseek-chat）。

provider = 谁家的接口、地址、密钥放哪。三处按顺序叠（后面的盖前面的）：
  1. 内置的常见几家（PROVIDERS）
  2. openclaw.json 的 models.providers（这台机器装着 OpenClaw 时：地址、接口类型、apiKey 写成 ${VAR} 就认这个变量名）
  3. server.json 的 engine.providers：{"名字": {"api": "openai" | "anthropic", "url": ".../v1", "key_env": "变量名", "headers": {…},
     "json_mode": true, "reasoning_effort": false, "fallbacks": true}}，可以加新的（本机的 Ollama、公司的代理）
api：anthropic = 官方 SDK（只认 API 密钥：Anthropic 条款不许第三方产品借 Claude 订阅登录）；openai = OpenAI 兼容的 /chat/completions
（Kimi、DeepSeek、Qwen、Gemini、OpenRouter、Ollama、LM Studio、vLLM…）。
密钥：key_env 指环境变量名（默认 <PROVIDER>_API_KEY），先看进程环境、再看 server.json 的 env_file（默认 <openclaw_home>/.env）。
每次现读，改了不用重启；不打印、不记日志。

server.json 的 engine 段（不写这一段 = 内核不接管任何事，全部照旧）：
  "engine": {
    "model": "deepseek/deepseek-chat",        默认模型
    "models": ["deepseek/deepseek-chat", …],  claw 选 builtin 时 app 里能切换的模型（不写就只有 model）
    "use": {"card": true, "doorman": "moonshot/kimi-k3", "json": false},
                                               哪些事改走内核：true = 用默认模型，写模型 = 用它；不写 / false = 照旧（llm-task 等）
                                               card = 名片 Agent（含 A2A）；doorman = Doorman 复查（名片回复 + 代办出网）；json = llmjson.ask
    "fallback": true,                          内核这条路出错时退回原来的路（默认 true；false = 不退，直接报错）
    "effort": "low",                           外人回合的思考档位（low / medium / high；只给支持的模型）
    "providers": {…}                           见上
  }
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from config import raw, settings

from .errors import EngineError

ANTHROPIC, OPENAI = "anthropic", "openai"

PROVIDERS: dict[str, dict] = {
    "anthropic": {"api": ANTHROPIC, "key_env": "ANTHROPIC_API_KEY"},
    "openai": {"api": OPENAI, "url": "https://api.openai.com/v1", "key_env": "OPENAI_API_KEY", "max_tokens_field": "max_completion_tokens",
               "reasoning_effort": True},
    "moonshot": {"api": OPENAI, "url": "https://api.moonshot.ai/v1", "key_env": "MOONSHOT_API_KEY"},
    "deepseek": {"api": OPENAI, "url": "https://api.deepseek.com", "key_env": "DEEPSEEK_API_KEY"},
    "dashscope": {"api": OPENAI, "url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1", "key_env": "DASHSCOPE_API_KEY"},
    "google": {"api": OPENAI, "url": "https://generativelanguage.googleapis.com/v1beta/openai", "key_env": "GEMINI_API_KEY"},
    "openrouter": {"api": OPENAI, "url": "https://openrouter.ai/api/v1", "key_env": "OPENROUTER_API_KEY"},
    "xai": {"api": OPENAI, "url": "https://api.x.ai/v1", "key_env": "XAI_API_KEY"},
    "mistral": {"api": OPENAI, "url": "https://api.mistral.ai/v1", "key_env": "MISTRAL_API_KEY"},
    "groq": {"api": OPENAI, "url": "https://api.groq.com/openai/v1", "key_env": "GROQ_API_KEY"},
    "ollama": {"api": OPENAI, "url": "http://127.0.0.1:11434/v1", "key_env": ""},
    "lmstudio": {"api": OPENAI, "url": "http://127.0.0.1:1234/v1", "key_env": ""},
    "vllm": {"api": OPENAI, "url": "http://127.0.0.1:8000/v1", "key_env": ""},
}
OPENCLAW_API = {"openai-completions": OPENAI, "openai-responses": OPENAI, "anthropic-messages": ANTHROPIC}
ENV_REF = re.compile(r"^\$\{([A-Z_][A-Z0-9_]*)\}$")
REF = re.compile(r"^([a-z0-9][a-z0-9_.-]*)/(\S+)$")

# 每百万 token 的美元价：(输入, 输出, 缓存读, 缓存写)。只列核对过的（Anthropic 2026-10-06 的价目）；没列的不算钱，界面上写「?」。
PRICES: dict[str, tuple[float, float, float, float]] = {
    "anthropic/claude-fable-5-1": (10, 50, 0.25, 12.5),
    "anthropic/claude-opus-5-5": (4, 20, 0.20, 5),
    "anthropic/claude-opus-5": (5, 25, 0.50, 6.25),
    "anthropic/claude-sonnet-5-5": (2, 10, 0.20, 2.5),
    "anthropic/claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125),
}

# Anthropic 各型号能用的参数（只认确切的型号名；不认识的按名字里的代数猜：5 代及以后当新的，更老的什么都不带）
ADAPTIVE = {"claude-fable-5-1", "claude-mythos-5-1", "claude-fable-5", "claude-mythos-5", "claude-opus-5-5", "claude-opus-5",
            "claude-sonnet-5-5", "claude-sonnet-5", "claude-haiku-5-5", "claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6",
            "claude-sonnet-4-6"}
MID_SYSTEM = {"claude-fable-5-1", "claude-mythos-5-1", "claude-fable-5", "claude-mythos-5", "claude-opus-5-5", "claude-opus-5",
              "claude-opus-4-8", "claude-sonnet-5-5", "claude-haiku-5-5"}  # 对话中间的 role: system（Sonnet 5 不行）
DISPLAY_UPDATES = {"claude-fable-5-1", "claude-mythos-5-1", "claude-fable-5", "claude-opus-5-5", "claude-sonnet-5-5"}
SERVER_FALLBACKS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}  # fallbacks: "default"
GEN = re.compile(r"^claude-(?:opus|sonnet|haiku|fable|mythos)-(\d+)")


def cfg() -> dict:
    e = raw().get("engine")
    return e if isinstance(e, dict) else {}


def parse_ref(ref: str) -> tuple[str, str]:
    """「provider/model」→ (provider, model)。model 里可以再有斜杠（openrouter/anthropic/claude-…）。不像样的抛 EngineError(config)。"""
    m = REF.match(str(ref or "").strip())
    if not m:
        raise EngineError(f"model should look like provider/model, got {str(ref)[:60]!r}", kind="config")
    return m.group(1), m.group(2)


def valid_ref(ref: str | None) -> bool:
    try:
        name, _ = parse_ref(ref or "")
    except EngineError:
        return False
    return name in PROVIDERS or name in _openclaw_providers() or name in (cfg().get("providers") or {})


def default_model() -> str:
    return str(cfg().get("model") or "").strip()


def models() -> list[str]:
    ms = [str(m).strip() for m in cfg().get("models") or [] if valid_ref(str(m))]
    d = default_model()
    return ms or ([d] if d else [])


def route(purpose: str, override: str | None = None) -> str | None:
    """这件事（card / doorman / json）走不走内核：走就回模型引用，不走回 None。override = 调用方自己指定的模型（合法才用）。"""
    use = cfg().get("use")
    v = use.get(purpose) if isinstance(use, dict) else None
    if v is None or v is False:
        return None
    if override and valid_ref(override):
        return override
    if isinstance(v, str) and valid_ref(v):
        return v
    d = default_model()
    return d if v is True and valid_ref(d) else None


def fallback_allowed() -> bool:
    return cfg().get("fallback", True) is not False


def effort(purpose: str = "") -> str | None:
    e = cfg().get("effort")
    return str(e) if e in ("low", "medium", "high", "xhigh", "max") else None


# —— provider ——————————————————————————————————————————————————————————

_oc_cache: tuple[float, dict] | None = None


def _openclaw_providers() -> dict:
    """openclaw.json 的 models.providers（按 mtime 缓存）。没装 OpenClaw、读不到 = 空。"""
    global _oc_cache
    path = Path(settings.openclaw_json)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    if _oc_cache and _oc_cache[0] == mtime:
        return _oc_cache[1]
    try:
        p = (json.loads(path.read_text(encoding="utf8")).get("models") or {}).get("providers") or {}
    except (OSError, ValueError, AttributeError):
        p = {}
    _oc_cache = (mtime, p if isinstance(p, dict) else {})
    return _oc_cache[1]


def provider(name: str) -> dict:
    """{"name", "api", "url", "key", "headers", …其余设置}。不认识的 provider 抛 EngineError(config)。key 可能是空的（本机模型不要密钥）。"""
    p: dict = dict(PROVIDERS.get(name) or {})
    oc = _openclaw_providers().get(name)
    if isinstance(oc, dict):
        if oc.get("baseUrl"):
            p["url"] = str(oc["baseUrl"])
        if not p.get("api") and OPENCLAW_API.get(str(oc.get("api") or "")):
            p["api"] = OPENCLAW_API[str(oc["api"])]
        if not p.get("api") and oc.get("baseUrl"):
            p["api"] = OPENAI
        m = ENV_REF.match(str(oc.get("apiKey") or ""))
        if m:
            p["key_env"] = m.group(1)
    over = (cfg().get("providers") or {}).get(name)
    if isinstance(over, dict):
        p.update(over)
    if p.get("api") not in (ANTHROPIC, OPENAI):
        raise EngineError(f"unknown model provider {name!r} (add it under engine.providers in server.json)", kind="config")
    p["name"] = name
    p["url"] = str(p.get("url") or "").rstrip("/")
    if p["api"] == OPENAI and not p["url"]:
        raise EngineError(f"provider {name!r} has no url", kind="config")
    key_env = p.get("key_env", f"{name.upper()}_API_KEY")
    p["key"] = str(p.get("key") or "").strip() or env_value(str(key_env or ""))
    return p


def env_value(name: str) -> str:
    """环境变量：先看进程环境，再看 server.json 的 env_file（没写就是 <openclaw_home>/.env）。读不到 = 空。"""
    if not name:
        return ""
    if os.environ.get(name):
        return os.environ[name]
    env_file = Path(str(raw().get("env_file"))).expanduser() if raw().get("env_file") else settings.env_file
    try:
        for line in env_file.read_text(encoding="utf8").splitlines():
            k, sep, v = line.strip().removeprefix("export ").partition("=")
            if sep and k.strip() == name:
                return v.strip().strip("'\"")
    except OSError:
        pass
    return ""


def has_key(ref: str) -> bool:
    """这个模型能不能用：provider 认得、要密钥的有密钥（本机的 Ollama 这类不要密钥）。"""
    try:
        name, _ = parse_ref(ref)
        p = provider(name)
    except EngineError:
        return False
    return bool(p["key"]) or p.get("key_env", "x") == ""


# —— 型号能力和价钱 ———————————————————————————————————————————————————————

def anthropic_caps(model_id: str) -> dict:
    """{"adaptive": 带 thinking adaptive + effort, "mid_system": 对话中间能放 role system, "updates": thinking.display 能用 updates,
    "fallbacks": 能用服务端的 fallbacks: "default"}。"""
    if model_id in ADAPTIVE:
        return {"adaptive": True, "mid_system": model_id in MID_SYSTEM, "updates": model_id in DISPLAY_UPDATES,
                "fallbacks": model_id in SERVER_FALLBACKS}
    m = GEN.match(model_id)
    new = bool(m and int(m.group(1)) >= 5)
    return {"adaptive": new, "mid_system": new, "updates": False, "fallbacks": False}


def cost(ref: str, usage: dict) -> float | None:
    """这次用量值多少美元；价目里没有的回 None。usage：in / out / cache_read / cache_write（token 数）。"""
    p = PRICES.get(ref)
    if not p:
        return None
    return round((usage.get("in", 0) * p[0] + usage.get("out", 0) * p[1] + usage.get("cache_read", 0) * p[2]
                  + usage.get("cache_write", 0) * p[3]) / 1e6, 6)
