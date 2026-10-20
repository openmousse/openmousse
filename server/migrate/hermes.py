"""Hermes → OpenMousse（mousse migrate hermes）：照 Hermes 的家目录（NousResearch/hermes-agent commit cc6b1f2，只读地看过源码；布局以它为准）。

只读 $HERMES_HOME（默认 ~/.hermes）：
  config.yaml   model（default / model、provider、base_url）、providers:（还有老的 custom_providers 列表）、fallback_providers（+ 老的
                fallback_model）、auxiliary.<任务>、mcp_servers、command_allowlist、approvals、memory.provider
  .env          只看有哪些变量名（值只在 --copy-secrets 时抄进 secrets.env；订阅的登录令牌 CLAUDE_CODE_OAUTH_TOKEN、ANTHROPIC_TOKEN 从来不抄）
  SOUL.md       → 主工作区的 SOUL.md（没有才拷，有了不盖，写一句）
  memories/MEMORY.md、memories/USER.md（条目之间一行 §，hermes tools/memory_tool_store.py 的 ENTRY_DELIMITER）→ 主工作区的
                MEMORY.md、USER.md：没有的条目加在末尾的一节里，已经有的不动（改之前留版本）
  skills/       用户自己的技能（.bundled_manifest 里记着的 Hermes 自带的不搬）：先过 engine/skill_write.guard 的内容检查，过了的拷进
                主工作区 skills/<名字>/，frontmatter 写 disable-model-invocation: true 先停着，列给用户点头（mousse migrate on skill:<名字>）
  cron/jobs.json   定时任务（hermes cron/jobs.py 的样子：schedule kind cron / interval / once）→ 内核定时任务，先停着
  profiles/<名字>/  每个 profile 一个 Agent：照新建 Agent 那条路建工作区，它的 SOUL.md、记忆、模型、定时任务、MCP、放行名单跟过去
模型：provider 照 HERMES 换成目录里的名字（gemini → google、kimi-coding → moonshot……）；Anthropic 用的是订阅令牌（.env 里只有
  CLAUDE_CODE_OAUTH_TOKEN / ANTHROPIC_TOKEN、没有 ANTHROPIC_API_KEY）时同 OpenClaw：这台机器的 Claude Code 登录着就换成 claude-code/…，
  不然照旧 anthropic/… 按量，写一句；openai-codex（Codex 登录）→ chatgpt/…，放进 engine.chatgpt.models，备用链里只放按量的。
主机命令：command_allowlist 只转写明了的命令（危险操作那一类的「以后都放行」不转）；approvals.mode 是 off 的也不会因此变成全放开。
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

import yaml

import config
from i18n import L

from .apply import frontmatter
from .common import (Plan, SourceError, env_name_for, env_ref, get, known_provider, literal, put, read_env, read_text, tilde)
from .common import canonical as canon

DELIM = "\n§\n"
HERMES = {  # Hermes 的 provider id → (目录里的名字, 地区, Hermes 读的密钥变量)（hermes_cli/auth.py 的 PROVIDER_REGISTRY）
    "openrouter": ("openrouter", None, ("OPENROUTER_API_KEY",)),
    "openai-api": ("openai", None, ("OPENAI_API_KEY",)), "openai": ("openai", None, ("OPENAI_API_KEY",)),
    "anthropic": ("anthropic", None, ("ANTHROPIC_API_KEY",)),
    "gemini": ("google", None, ("GEMINI_API_KEY", "GOOGLE_API_KEY")), "google": ("google", None, ("GEMINI_API_KEY", "GOOGLE_API_KEY")),
    "zai": ("zai", None, ("ZAI_API_KEY", "GLM_API_KEY", "Z_AI_API_KEY")),
    "kimi-coding": ("moonshot", "global", ("KIMI_API_KEY", "KIMI_CODING_API_KEY")), "kimi-coding-cn": ("moonshot", "cn", ("KIMI_CN_API_KEY",)),
    "minimax": ("minimax", "global", ("MINIMAX_API_KEY",)), "minimax-cn": ("minimax", "cn", ("MINIMAX_CN_API_KEY",)),
    "alibaba": ("dashscope", None, ("DASHSCOPE_API_KEY",)), "deepseek": ("deepseek", None, ("DEEPSEEK_API_KEY",)),
    "xai": ("xai", None, ("XAI_API_KEY",)), "nvidia": ("nvidia", None, ("NVIDIA_API_KEY",)), "ai-gateway": ("vercel", None, ("AI_GATEWAY_API_KEY",)),
    "huggingface": ("huggingface", None, ("HF_TOKEN",)), "lmstudio": ("lmstudio", None, ()),
}
PLAN_TOKENS = ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_TOKEN")   # Claude 订阅的登录令牌（setup-token）：认出是订阅，从来不抄
API_MODE = {"chat_completions": "openai", "openai": "openai", "codex_responses": "openai-responses", "responses": "openai-responses",
            "openai-responses": "openai-responses", "anthropic_messages": "anthropic", "anthropic": "anthropic"}
AUX = {"compression": "compact", "title_generation": "json", "memory_query_rewrite": "json", "triage_specifier": "json"}
PROFILE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
LIVE = ("config.yaml", ".env", "SOUL.md", "profile.yaml", "auth.json", "state.db")
SKIP_DIRS = {".hub", ".archive", ".curator_backups", ".git"}


def read_yaml(p: Path) -> dict:
    text = read_text(p)
    if text is None:
        return {}
    try:
        d = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise SourceError(L(f"{tilde(p)} 不是能读的 YAML：{e}", f"{tilde(p)} isn't readable YAML: {e}")) from None
    return d if isinstance(d, dict) else {}


def entries(p: Path) -> list[str]:
    """MEMORY.md / USER.md 的条目：整篇按「一行 §」切开、去掉空的（hermes memory_tool_store 的读法）。"""
    text = read_text(p)
    if not text:
        return []
    return [e.strip() for e in text.split(DELIM) if e.strip()]


class Home:
    """一个 Hermes 的家（根目录或一个 profile）：读好的配置、.env 的变量名。"""

    def __init__(self, root: Path, name: str, parent: "Home | None" = None):
        self.root, self.name, self.parent = root, name, parent
        self.cfg = read_yaml(root / "config.yaml")
        self.env_path = root / ".env"
        self.env = read_env(self.env_path)

    def at(self, key: str) -> str:
        """报告里这一项叫什么：根目录的照写，profile 的前面带 profiles/<名字>/。"""
        return key if self.name == "default" else f"profiles/{self.name}/{key}"


class Planner:
    def __init__(self, plan: Plan, detect):
        self.p = plan
        self._detect = detect
        self._cc: dict | None | bool = False
        self.used: set[str] = set()

    def cc(self) -> dict | None:
        if self._cc is False:
            from engine import runners
            try:
                info = self._detect("claude-code")
            except Exception:  # noqa: BLE001
                info = None
            self._cc = info if runners.own_login(info) else None
        return self._cc  # type: ignore[return-value]

    # —— 模型 ——————————————————————————————————————————————————————————————————

    def convert(self, h: Home, provider: str, model: str, base_url: str = "") -> tuple[str | None, str]:
        """(Hermes 的 provider, 型号) → (我们的引用 或 None, plain / sub / sub-api / codex / custom / unknown)。"""
        prov = str(provider or "").strip().lower()
        model = str(model or "").strip()
        if not model:
            return None, "unknown"
        if prov.startswith("custom:"):
            prov = prov.split(":", 1)[1]
        if prov in ("", "auto"):
            if "/" in model and model.split("/", 1)[0] in HERMES:   # 写成了 provider/型号
                prov, model = model.split("/", 1)
            else:
                return None, "auto"
        if prov == "openai-codex":
            return f"chatgpt/{model}", "codex"
        if prov in self.custom.get(h.name, {}):
            return f"{self.custom[h.name][prov]}/{model}", "custom"
        if prov not in HERMES:
            return None, "unknown"
        ours, _region, _envs = HERMES[prov]
        if ours == "anthropic" and self.plan_login(h):
            return (f"claude-code/{model}", "sub") if self.cc() else (canon(f"anthropic/{model}"), "sub-api")
        if not known_provider(ours, self.p.patch):
            return None, "unknown"
        self.used.add(prov)
        return canon(f"{ours}/{model}"), "plain"

    def plan_login(self, h: Home) -> bool:
        """Anthropic 走的是订阅令牌：.env 里有 CLAUDE_CODE_OAUTH_TOKEN / ANTHROPIC_TOKEN、没有 ANTHROPIC_API_KEY。"""
        return any(k in h.env for k in PLAN_TOKENS) and "ANTHROPIC_API_KEY" not in h.env

    def providers(self, h: Home, aid: str) -> None:
        """providers:（和老的 custom_providers 列表）→ engine.providers.<名字>：自定义的 OpenAI 兼容 / Anthropic / Responses 地址。
        profile 里的名字前面加上 profile 名（一份 server.json 里不撞）。"""
        self.custom.setdefault(h.name, {})
        items = dict(h.cfg.get("providers") or {}) if isinstance(h.cfg.get("providers"), dict) else {}
        for c in h.cfg.get("custom_providers") or [] if isinstance(h.cfg.get("custom_providers"), list) else []:
            if isinstance(c, dict) and c.get("name"):
                items.setdefault(str(c["name"]), c)
        for name, c in items.items():
            if not isinstance(c, dict):
                continue
            url = str(c.get("base_url") or c.get("url") or c.get("api") or "").strip()
            what = h.at(f"providers.{name}")
            if not url:
                continue  # Hermes 也一样：没有地址的不算自定义的（只是给内置的那家调超时）
            pid = re.sub(r"[^a-z0-9_.-]+", "-", (name if h.name == "default" else f"{h.name}-{name}").lower()).strip("-")[:32] or "custom"
            api = API_MODE.get(str(c.get("api_mode") or c.get("transport") or "chat_completions"))
            if api is None:
                self.p.no(what, L(f"接口 {c.get('api_mode')} 内核不认", f"The kernel doesn't speak {c.get('api_mode')}"))
                continue
            spec = {"api": api, "url": url, "name": str(name)}
            key_env = str(c.get("key_env") or c.get("api_key_env") or "").strip()
            if key_env:
                self.secret(h, key_env)
            elif c.get("api_key"):
                key_env = self.key(h, what, c["api_key"], env_name_for(pid)) or ""
            spec["key_env"] = key_env
            put(self.p.patch, "engine", "providers", pid, spec)
            self.custom[h.name][str(name).lower()] = pid
            self.p.ok(what, L(f"→ engine.providers.{pid}（{api}，{url}）", f"→ engine.providers.{pid} ({api}, {url})"))

    def secret(self, h: Home, name: str) -> None:
        if name in h.env:
            if h.name == "default":
                self.p.secrets[name] = h.env_path
            else:
                self.p.secrets.setdefault(name, h.env_path)

    def key(self, h: Home, what: str, v, name: str) -> str | None:
        if ref := env_ref(v):
            self.secret(h, ref)
            return ref
        if val := literal(v):
            self.p.held[name] = val
            return name
        return None

    def models(self, h: Home, aid: str) -> str | None:
        """model / fallback_providers / auxiliary → engine.model、engine.fallbacks、engine.use.*（profile 的模型 → 那个 Agent 对话的模型）。"""
        m = h.cfg.get("model")
        m = {"default": m} if isinstance(m, str) else m if isinstance(m, dict) else {}
        model = m.get("default") or m.get("model")
        if isinstance(model, dict):
            m = {**m, **model}
            model = model.get("model")
        chosen = None
        if model:
            ref, how = self.convert(h, str(m.get("provider") or ""), str(model), str(m.get("base_url") or ""))
            what = h.at("model")
            if ref:
                chosen = ref
                if aid == "main":
                    put(self.p.patch, "engine", "model", ref)
                self.say(what, ref, how, "engine.model" if aid == "main" else L(f"Agent {aid} 对话的模型", f"Agent {aid}'s chat model"))
            else:
                self.p.no(what, self.why(f"{m.get('provider') or 'auto'}/{model}", how))
        if aid != "main":
            return chosen
        chain: list[str] = []
        fb = h.cfg.get("fallback_providers")
        fb = list(fb) if isinstance(fb, list) else [fb] if isinstance(fb, dict) else []
        old = h.cfg.get("fallback_model")
        fb += list(old) if isinstance(old, list) else [old] if isinstance(old, dict) else []
        for f in fb:
            if not isinstance(f, dict) or not f.get("provider") or not f.get("model"):
                continue
            ref, how = self.convert(h, str(f["provider"]), str(f["model"]), str(f.get("base_url") or ""))
            if ref and how in ("plain", "custom"):
                chain.append(ref)
            elif how in ("sub", "sub-api"):
                self.p.ok("fallback_providers", L(f"{f['provider']}/{f['model']} 不进备用链：Hermes 上它走你的 Claude 订阅，备用链只放按量的",
                                                  f"{f['provider']}/{f['model']} left out of the fallbacks: on Hermes it ran on your Claude plan; "
                                                  "fallbacks are pay-as-you-go only"))
            elif how == "codex" and ref:
                self.chatgpt(ref, "fallback_providers")
            else:
                self.p.no("fallback_providers", self.why(f"{f['provider']}/{f['model']}", how))
        if chain:
            put(self.p.patch, "engine", "fallbacks", list(dict.fromkeys(chain)))
            self.p.ok("fallback_providers", f"→ engine.fallbacks = {', '.join(chain)}")
        aux = h.cfg.get("auxiliary") if isinstance(h.cfg.get("auxiliary"), dict) else {}
        left: list[str] = []
        for task, c in aux.items():
            if not isinstance(c, dict) or not c.get("model") or str(c.get("provider") or "auto") == "auto":
                continue
            use = AUX.get(str(task))
            if not use:
                left.append(str(task))
                continue
            if get(self.p.patch, "engine", "use", use):
                continue
            ref, how = self.convert(h, str(c["provider"]), str(c["model"]), str(c.get("base_url") or ""))
            if ref and how in ("plain", "custom"):
                put(self.p.patch, "engine", "use", use, ref)
                self.p.ok(f"auxiliary.{task}", f"→ engine.use.{use} = {ref}")
            else:
                self.p.no(f"auxiliary.{task}", self.why(f"{c['provider']}/{c['model']}", how))
        if left:
            self.p.no("auxiliary", L(f"{', '.join(left)}：OpenMousse 没有对应的用途，没转", f"{', '.join(left)}: no matching purpose in OpenMousse, not converted"))
        return chosen

    def say(self, what: str, ref: str, how: str, key: str) -> None:
        if how == "sub":
            self.p.ok(what, L(f"→ {key} = {ref}（Hermes 上走你的 Claude 订阅；这台机器的 Claude Code 登录着，用它）",
                              f"→ {key} = {ref} (on Hermes this ran on your Claude plan; Claude Code is signed in here, so it uses that)"))
        elif how == "sub-api":
            self.p.ok(what, f"→ {key} = {ref}")
            self.p.ask(key, L(f"Hermes 上它走你的 Claude 订阅，这台机器没装或没登录 Claude Code：先照旧写 {ref}，按量计费（要 ANTHROPIC_API_KEY）。"
                              "装好、登录 Claude Code 以后在 app 的「设置 → 按用途选模型」换成 claude-code/…",
                              f"On Hermes this ran on your Claude plan, but Claude Code isn't installed or signed in here: it's written as {ref} for now, "
                              "billed per use (needs ANTHROPIC_API_KEY). Once Claude Code is signed in, switch to claude-code/… under Settings → Models by purpose"))
        elif how == "codex":
            self.chatgpt(ref, what, quiet=True)
            self.p.ok(what, L(f"→ {key} = {ref}（Hermes 上用 Codex 登录）", f"→ {key} = {ref} (Codex sign-in on Hermes)"))
        else:
            self.p.ok(what, f"→ {key} = {ref}")

    def chatgpt(self, ref: str, what: str, quiet: bool = False) -> None:
        cur = get(self.p.patch, "engine", "chatgpt", "models") or []
        if ref not in cur:
            put(self.p.patch, "engine", "chatgpt", "models", [*cur, ref])
        if not quiet:
            self.p.ok(what, L(f"{ref} 放进 engine.chatgpt.models，不进备用链（备用链只放按量的）", f"{ref} goes into engine.chatgpt.models, not the fallbacks "
                              "(fallbacks are pay-as-you-go only)"))
        if not any(x.what == "ChatGPT" for x in self.p.todo):
            self.p.ask("ChatGPT", L("要用 ChatGPT 套餐：在 app 的「设置 → 服务商」里 Continue with ChatGPT 登录一次（Hermes 的登录不搬：令牌不抄）",
                                    "To use your ChatGPT plan, sign in once with Continue with ChatGPT under Settings → Providers (Hermes's sign-in isn't "
                                    "moved: tokens aren't copied)"))

    def why(self, ref: str, how: str) -> str:
        if how == "auto":
            return L(f"{ref}：provider 是 auto，看不出用的是哪一家", f"{ref}: provider is auto, so there's no telling which service it used")
        return L(f"{ref}：OpenMousse 不认识这一家", f"{ref}: OpenMousse doesn't know this provider")

    def keys(self, h: Home) -> None:
        """用到的几家（Hermes 的名字）的密钥变量：目录里的名字和 Hermes 读的不一样时写 engine.providers.<家>.key_env；只记名字。"""
        import secretstore
        from engine import catalog
        for prov in sorted(self.used):
            ours, region, envs = HERMES[prov]
            e = catalog.entry(ours) or {}
            mine = str(e.get("key_env") or "")
            if region and region != next(iter(e.get("regions") or {"": None})):
                put(self.p.patch, "engine", "providers", ours, "url", catalog.region(e, region)["url"])
            have = next((n for n in envs if n in h.env), None)
            src = h
            if have is None and h.parent is not None:  # profile 自己的 .env 里没有：根目录那一份（env_file 指的就是它）
                have, src = next((n for n in envs if n in h.parent.env), None), h.parent
            if have:
                self.secret(src, have)
                if have != mine:
                    put(self.p.patch, "engine", "providers", ours, "key_env", have)
            elif mine and not secretstore.lookup(mine)[0] and not (ours == "anthropic" and self.plan_login(h)):
                self.p.ask(mine, L(f"{ours} 要密钥 {mine}：Hermes 的 .env 里没有，这台服务器也没有。在 app 的「设置 → 服务商」里填",
                                   f"{ours} needs the key {mine}: not in Hermes's .env or on this server. Enter it under Settings → Providers in the app"))
        if self.plan_login(h) and any(k in h.env for k in PLAN_TOKENS):
            self.p.no(h.at(".env"), L(f"{'、'.join(k for k in PLAN_TOKENS if k in h.env)} 是 Claude 订阅的登录令牌：不抄（OpenMousse 只用你自己登录的 Claude Code）",
                                          f"{', '.join(k for k in PLAN_TOKENS if k in h.env)} is a Claude plan sign-in token: not copied (OpenMousse only "
                                          "uses your own signed-in Claude Code)"))

    # —— 别的设置 ——————————————————————————————————————————————————————————————

    def mcp(self, h: Home, aid: str) -> None:
        servers = h.cfg.get("mcp_servers") if isinstance(h.cfg.get("mcp_servers"), dict) else {}
        for name, s in servers.items():
            if not isinstance(s, dict):
                continue
            what = h.at(f"mcp_servers.{name}")
            if str(s.get("auth") or "") == "oauth":
                self.p.no(what, L("要 OAuth 登录的：在 app 的连接器里接", "Needs an OAuth sign-in: connect it under Connectors in the app"))
                continue
            out: dict = {}
            if s.get("url"):
                out = {"type": "sse" if str(s.get("transport") or "") == "sse" else "http", "url": str(s["url"])}
                if hd := self.clean(h, what, s.get("headers")):
                    out["headers"] = hd
            elif s.get("command"):
                out = {"command": str(s["command"]), "args": [str(a) for a in s.get("args") or []]}
                if env := self.clean(h, what, s.get("env")):
                    out["env"] = env
                if s.get("cwd"):
                    out["cwd"] = str(s["cwd"])
            else:
                continue
            if s.get("enabled") is False or str(s.get("enabled")).lower() in ("false", "off", "no"):
                out["disabled"] = True
            tools = s.get("tools") if isinstance(s.get("tools"), dict) else {}
            if tools.get("exclude"):
                out["tools"] = {str(t): "off" for t in tools["exclude"]}
            if tools.get("include"):
                self.p.ask(what, L("tools.include 没转：OpenMousse 按工具写 auto / ask / off", "tools.include wasn't converted: OpenMousse sets auto / ask / off per tool"))
            key = str(name) if aid == "main" else f"{h.name}-{name}"
            out["agents"] = [aid]
            put(self.p.patch, "mcp_servers", key, out)
            self.p.ok(what, f"→ mcp_servers.{key}" + (L(f"（给 {aid}）", f" (for {aid})") if aid != "main" else ""))

    def clean(self, h: Home, what: str, d) -> dict:
        from .common import SENSITIVE
        out: dict = {}
        dropped: list[str] = []
        for k, v in d.items() if isinstance(d, dict) else []:
            if ref := env_ref(v):
                out[str(k)] = f"${{{ref}}}"
                self.secret(h, ref)
            elif SENSITIVE.search(str(k)):
                dropped.append(str(k))
            else:
                out[str(k)] = str(v)
        if dropped:
            self.p.ask(what, L(f"{', '.join(dropped)} 是直接写的密钥：没转（MCP 的密钥只能写成 ${{变量}}）",
                               f"{', '.join(dropped)} {'holds a key' if len(dropped) == 1 else 'hold keys'} written in plain: not converted (MCP keys must "
                               "be written as ${VARIABLE})"))
        return out

    def host_exec(self, h: Home, aid: str) -> None:
        from engine import host_exec
        allow: list[str] = []
        skipped: list[str] = []
        descs = {d.casefold() for _rx, d in [*host_exec._HARDLINE, *host_exec._DANGEROUS]}  # noqa: SLF001
        raw_list = h.cfg.get("command_allowlist")
        raw_list = [raw_list] if isinstance(raw_list, str) else raw_list if isinstance(raw_list, list) else []
        for x in raw_list:
            s = str(x or "").strip()
            if not s:
                continue
            first = s.split()[0]
            if s.casefold() in descs or (not first.startswith(("/", "~")) and not shutil.which(first.rstrip("*?"))) or "[" in s:
                skipped.append(s)
                continue
            words = s.split()
            words[0] = str(Path(first).expanduser()) if first.startswith("~") else first
            tail = s.endswith("*")  # fnmatch 的 * 跨词：结尾的 * 换成我们的 ...（后面还能跟参数）；单独的一个 * 去掉
            if words[-1] == "*":
                words.pop()
            allow.append(" ".join(words) + (" ..." if tail else ""))
        what = h.at("command_allowlist")
        if allow:
            put(self.p.patch, "engine", "host_exec", "agents", aid, {"allow": list(dict.fromkeys(allow)), "ask": "on-miss"})
            self.p.ok(what, L(f"→ engine.host_exec.agents.{aid}.allow（{len(allow)} 条；名单外的要你点头）",
                              f"→ engine.host_exec.agents.{aid}.allow ({len(allow)}; anything else needs your OK)"))
        if skipped:
            self.p.ask(what, L(f"这几条不像写明了的命令（Hermes「以后都放行」记下的一类危险操作，或者看不出跑的是哪个程序）：没转：{'；'.join(skipped)[:300]}",
                               f"These don't look like spelled-out commands (a class of dangerous operations Hermes was told to always allow, or no "
                               f"recognisable program): not converted: {'; '.join(skipped)[:300]}"))
        mode = get(h.cfg, "approvals", "mode")
        if mode is False or str(mode).lower() == "off":
            self.p.ask(h.at("approvals.mode"), L("Hermes 上 approvals.mode = off（不问就跑）；OpenMousse 的主机命令默认全关，只放写明了的",
                                                     "approvals.mode was off on Hermes (run without asking); OpenMousse keeps host commands off and "
                                                     "only allows spelled-out ones"))

    # —— 文件 ——————————————————————————————————————————————————————————————————

    def files(self, h: Home, aid: str, new: bool) -> None:
        soul = h.root / "SOUL.md"
        if (read_text(soul) or "").strip():
            self.p.files.append({"op": "soul", "agent": aid, "src": soul, "new": new, "from": h.at("SOUL.md")})
            where_zh = "主工作区" if aid == "main" else f"{aid} 的工作区"
            where_en = "the main workspace" if aid == "main" else f"{aid}'s workspace"
            self.p.ok(h.at("SOUL.md"), L(f"→ {where_zh}的 SOUL.md" + ("" if new else "（有了就不盖）"),
                                             f"→ SOUL.md in {where_en}" + ("" if new else " (kept if one is already there)")))
        for fn in ("MEMORY.md", "USER.md"):
            got = entries(h.root / "memories" / fn)
            if got:
                label = L("从 Hermes 迁过来的", "Moved over from Hermes") + ("" if h.name == "default" else f" · {h.name}")
                self.p.files.append({"op": "entries", "agent": aid, "rel": fn, "entries": got, "label": label, "from": h.at(f"memories/{fn}")})
                self.p.ok(h.at(f"memories/{fn}"), L(f"{len(got)} 条 → {fn}（没有的加在末尾，已有的不动）", f"{len(got)} entr{'y' if len(got) == 1 else 'ies'} → "
                                                       f"{fn} (new ones appended, existing ones left alone)"))
        mp = str(get(h.cfg, "memory", "provider") or "").strip()
        if mp:
            self.p.no(h.at("memory.provider"), L(f"外接的记忆服务 {mp}：不转（OpenMousse 用自己的记忆和世界树）",
                                                     f"External memory service {mp}: not converted (OpenMousse uses its own memory and memory tree)"))

    def skills(self, h: Home, taken: set[str]) -> None:
        from engine import skill_write
        root = h.root / "skills"
        if not root.is_dir():
            return
        bundled = {ln.split(":", 1)[0].strip() for ln in (read_text(root / ".bundled_manifest") or "").splitlines() if ln.strip()}
        skipped_bundled = 0
        for md in sorted(root.rglob("SKILL.md")):
            d = md.parent
            if any(part in SKIP_DIRS for part in d.relative_to(root).parts):
                continue
            text = read_text(md)
            if text is None:
                continue
            fm = _front(text)
            raw_name = str(fm.get("name") or d.name).strip()
            if raw_name in bundled or d.name in bundled:
                skipped_bundled += 1
                continue
            what = h.at(f"skills/{d.relative_to(root)}")
            base = re.sub(r"[^a-z0-9]+", "-", raw_name.lower()).strip("-")[:56] or "skill"
            name, n = base, 2
            while name in taken:
                name, n = f"hermes-{base}" if n == 2 else f"hermes-{base}-{n}", n + 1
            reasons = skill_write.guard(text, "SKILL.md")
            extra: list[str] = []
            for f in sorted(d.rglob("*")):
                rel = f.relative_to(d)
                # 软链不跟（references/x -> ~/.hermes/.env 这种会把技能外面的文件拷进工作区）
                if f.is_symlink() or not f.is_file() or f.name == "SKILL.md" and len(rel.parts) == 1:
                    continue
                if rel.parts[0] not in skill_write.SUBDIRS or len(extra) >= skill_write.FILES_MAX:
                    continue
                body = read_text(f, limit=skill_write.FILE_MAX)
                if body is None:
                    continue
                reasons += skill_write.guard(body, str(rel))
                extra.append(str(rel))
            if reasons:
                self.p.no(what, L(f"内容检查没过，没拷：{'；'.join(reasons[:3])}", f"Failed the content check, not copied: {'; '.join(reasons[:3])}"))
                continue
            new_text = frontmatter(text, name, "hermes")
            if new_text is None:
                self.p.no(what, L("SKILL.md 开头没有 frontmatter：没拷", "SKILL.md has no frontmatter: not copied"))
                continue
            taken.add(name)
            self.p.files.append({"op": "skill", "agent": "main", "name": name, "src": d, "files": extra, "text": new_text, "from": what})
            self.p.ask(what, L(f"→ skills/{name}，先停着：看过没问题，mousse migrate on skill:{name} 打开",
                               f"→ skills/{name}, off for now: once you've read it, turn it on with mousse migrate on skill:{name}"))
        if skipped_bundled:
            self.p.no(h.at("skills"), L(f"Hermes 自带的技能 {skipped_bundled} 个：不搬", f"{skipped_bundled} of Hermes's bundled skills: not moved"))

    # —— 定时任务 ——————————————————————————————————————————————————————————————

    def cron(self, h: Home, aid: str) -> None:
        from engine import when
        data = None
        text = read_text(h.root / "cron" / "jobs.json")
        if text:
            import json
            try:
                data = json.loads(text)
            except ValueError:
                self.p.no(h.at("cron/jobs.json"), L("读不了（不是 JSON）", "Unreadable (not JSON)"))
                return
        jobs = data.get("jobs") if isinstance(data, dict) and isinstance(data.get("jobs"), list) else data if isinstance(data, list) else \
            [dict(v, id=k) for k, v in data.items() if isinstance(v, dict)] if isinstance(data, dict) else []
        for j in jobs:
            if not isinstance(j, dict):
                continue
            name = str(j.get("name") or j.get("id") or "").strip()
            what = h.at(f"cron:{name}")
            if str(j.get("state") or "") == "completed":
                self.p.no(what, L("Hermes 上已经跑完了", "Already finished on Hermes"))
                continue
            sched = j.get("schedule")
            if isinstance(sched, dict):
                kind = str(sched.get("kind") or "")
                spec = sched.get("expr") if kind == "cron" else f"every {sched.get('minutes')}m" if kind == "interval" else sched.get("run_at") if kind == "once" else None
            else:
                spec = sched
            try:
                parsed = when.parse(str(spec or ""), when.now())
            except when.WhenError as e:
                self.p.no(what, str(e))
                continue
            if parsed["kind"] == "once" and when.next_after(parsed, when.now()) is None:
                self.p.no(what, L("那个时刻已经过了", "That time has already passed"))
                continue
            given: dict = {"name": name[:60] or None, "when": str(spec)}
            if j.get("no_agent") and j.get("script"):
                path = str(Path(str(j["script"])).expanduser())
                allowed = {str(Path(x).expanduser()) for x in get(config.raw(), "engine", "jobs", "scripts") or [] if isinstance(x, str)}
                if path not in allowed:
                    self.p.ask(what, L(f"「{name}」只跑脚本 {tilde(path)}：不在 engine.jobs.scripts 白名单里。要的话加进去，再跑一次 mousse migrate hermes --apply",
                                       f'"{name}" only runs the script {tilde(path)}, which isn\'t in the engine.jobs.scripts allowlist. Add it there and run '
                                       "mousse migrate hermes --apply again to keep it"))
                    continue
                given["command"] = {"script": path, "args": []}
            else:
                task = str(j.get("prompt") or "").strip()
                if not task:
                    self.p.no(what, L("没有要做的事（prompt 是空的）", "Nothing to do (empty prompt)"))
                    continue
                if j.get("script"):
                    self.p.ask(what, L("Hermes 先跑脚本、把输出交给它：OpenMousse 的定时任务不先跑脚本，只照提示词做", "Hermes ran a script first and handed "
                                       "its output over: OpenMousse's jobs don't, they just follow the prompt"))
                sk = [str(x) for x in (j.get("skills") or ([j["skill"]] if j.get("skill") else [])) if x]
                if sk:
                    task += L(f"\n（先读这几个技能再做：{', '.join(sk)}）", f"\n(Read these skills first: {', '.join(sk)})")
                given["task"] = task
                given["session"] = "isolated"
                if j.get("model"):
                    ref, how = self.convert(h, str(j.get("provider") or ""), str(j["model"]), str(j.get("base_url") or ""))
                    if ref and how in ("plain", "custom", "sub"):
                        given["model"] = ref
            rep = j.get("repeat") if isinstance(j.get("repeat"), dict) else {}
            if isinstance(rep.get("times"), int) and rep["times"] > 0:
                left = rep["times"] - int(rep.get("completed") or 0)
                if left <= 0:
                    self.p.no(what, L("次数已经跑满了", "It already ran its full count"))
                    continue
                given["times"] = left
            deliver = str(j.get("deliver") or "local")
            if deliver not in ("local", "origin", ""):
                self.p.ask(what, L(f"Hermes 投到 {deliver}：OpenMousse 投进这个 Agent 的对话", f"Hermes delivered to {deliver}: OpenMousse posts to the Agent's chat"))
            given["level"] = "quiet"
            was = L("Hermes 上开着", "on in Hermes") if j.get("enabled", True) and str(j.get("state") or "") != "paused" else L("Hermes 上停着", "paused in Hermes")
            self.p.jobs.append({"agent": aid, "given": {k: v for k, v in given.items() if v is not None}, "from": what, "was": was})

    # —— 合起来 ——————————————————————————————————————————————————————————————————

    def run(self, home: Path) -> None:
        self.custom: dict[str, dict[str, str]] = {}
        root = Home(home, "default")
        if not (home / "config.yaml").is_file() and not root.env and not (home / "SOUL.md").is_file():
            raise SourceError(L(f"{tilde(home)} 里没有 config.yaml：不是 Hermes 的家？用 --hermes-home 指到它",
                                f"There's no config.yaml in {tilde(home)}: is that Hermes's home? Point --hermes-home at it"))
        homes: list[tuple[Home, str, bool]] = [(root, "main", False)]
        prof_dir = home / "profiles"
        for d in sorted(prof_dir.iterdir()) if prof_dir.is_dir() else []:
            if not d.is_dir() or not PROFILE.match(d.name) or not any((d / f).exists() for f in LIVE):
                continue
            aid = "hermes-" + re.sub(r"[^a-z0-9]+", "-", d.name).strip("-")
            h = Home(d, d.name, root)
            purpose = L(f"从 Hermes 的 profile「{d.name}」迁过来", f'Moved over from the Hermes profile "{d.name}"')
            self.p.agents.append({"id": aid, "name": d.name, "purpose": purpose, "new": True, "from": f"profiles/{d.name}"})
            homes.append((h, aid, True))
            self.p.ok(f"profiles/{d.name}", L(f"→ Agent「{d.name}」（{aid}），自己的工作区（--apply 时建）", f'→ Agent "{d.name}" ({aid}) with its own workspace '
                                              "(created with --apply)"))
        taken = self.skill_names()
        for h, aid, new in homes:
            self.providers(h, aid)
            chosen = self.models(h, aid)
            if aid != "main" and chosen:
                next(a for a in self.p.agents if a["id"] == aid)["model"] = chosen
            self.keys(h)
            self.used.clear()
            self.mcp(h, aid)
            self.host_exec(h, aid)
            self.files(h, aid, new)
            self.skills(h, taken)
            self.cron(h, aid)
        self.env_file(root)

    def skill_names(self) -> set[str]:
        """已经有的技能名（主工作区的、packs/core 的）：拷过来的不撞（撞了就叫 hermes-<名字>），也不会把同名的核心技能停掉。"""
        import bridges
        out: set[str] = set()
        ws = Path(str(config.raw().get("workspace") or config.settings.workspace)).expanduser()
        for base in (ws / "skills", bridges.REPO / "packs" / "core" / "skills"):
            try:
                out |= {p.name for p in base.iterdir() if p.is_dir()}
            except OSError:
                continue
        return out

    def env_file(self, root: Home) -> None:
        if not any(p == root.env_path for p in self.p.secrets.values()):
            return
        import secretstore

        from .common import same_dir
        if same_dir(secretstore.env_file(), root.env_path):
            return
        cur = config.raw().get("env_file")
        others = sorted({tilde(p) for p in self.p.secrets.values() if p != root.env_path})
        if others:
            self.p.ask("profiles/*/.env", L(f"profile 自己的密钥在 {', '.join(others)}：server.json 只能指一个 .env；加 --copy-secrets 抄进 secrets.env",
                                            f"Profile keys are in {', '.join(others)}: server.json points at one .env only; add --copy-secrets to copy them "
                                            "into secrets.env"))
        if cur:
            self.p.ask("env_file", L(f"server.json 的 env_file 已经指着 {tilde(str(cur))}：Hermes 的密钥在 {tilde(root.env_path)}；加 --copy-secrets 抄进 "
                                     "secrets.env，或者自己改", f"server.json's env_file already points at {tilde(str(cur))}: Hermes's keys are in "
                                     f"{tilde(root.env_path)}; add --copy-secrets to copy them into secrets.env, or change it yourself"))
            return
        put(self.p.patch, "env_file", str(root.env_path))
        self.p.ok(".env", L(f"→ env_file = {tilde(root.env_path)}（密钥的值留在那里）", f"→ env_file = {tilde(root.env_path)} (the values stay there)"))


def _front(text: str) -> dict:
    m = re.match(r"^---\s*\n(.*?)\n---", text, re.S)
    if not m:
        return {}
    try:
        d = yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        return {}
    return d if isinstance(d, dict) else {}


def plan(home: Path, detect=None) -> Plan:
    from engine import runners
    p = Plan("hermes", home)
    Planner(p, detect or runners.detect).run(home)
    return p

