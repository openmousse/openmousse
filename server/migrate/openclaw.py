"""OpenClaw → OpenMousse（mousse migrate openclaw）：照 OpenClaw 2026.10.5 的家目录（openclaw/openclaw commit e559b9e，只读地看过源码）。

只读：openclaw.json（JSON5）、state/openclaw.sqlite（拷到临时目录再用只读连接打开：定时任务 cron_jobs、心跳清单 cron_job_scratch、
主机命令的审批 exec_approvals_config）、各工作区的 HEARTBEAT.md、.env（只看有哪些变量名）。老版本（openclaw doctor --fix 之前）的
cron/jobs.json、exec-approvals.json、agents.list、memorySearch 也认。

转什么（plan 里一样一样写，报告分「自动转的 / 要人做的 / 转不了的」）：
  主模型 agents.defaults.model.primary → engine.model；auth.order.anthropic 第一个是订阅（以 :subscription 结尾，或者是 setup-token /
    Claude CLI 登录那种 token、oauth 档案）时 anthropic/<型号> → claude-code/<型号>：这台机器没装、没登录 Claude Code（runners.detect）
    就照旧写 anthropic/…（按量），报告里说。
  备用链 agents.defaults.model.fallbacks → engine.fallbacks：订阅优先时的 anthropic/* 去掉；Codex 登录来的 openai/*（auth.order.openai
    第一个是 oauth 档案、型号上写着 agentRuntime codex、老写法 openai-codex/*、codex/*）换成 chatgpt/*，放进 engine.chatgpt.models，
    不进备用链：备用链里只放按量的。
  agents.defaults.modelPolicy.allow（老的：没有 modelPolicy 时 agents.defaults.models 的键）→ engine.models；别名（opus、sonnet……、
    models.<引用>.alias）照 OpenClaw 的表换成引用；改过名的服务商照 catalog 换（RENAMED），型号照 models.canonical 规范。
  agents.defaults.heartbeat.model → engine.use.background。名片 Agent、Doorman、一次性 JSON：OpenClaw 上走 llm-task 用主模型（可能是订阅），
    只在「要人做的」里列出来让用户选，不替他定。
  models.providers.<名> → engine.providers（api：openai-completions → openai、anthropic-messages → anthropic、openai-responses →
    openai-responses；baseUrl → url；apiKey 写成 ${变量} 的 → key_env；直接写的值只在 --copy-secrets 时抄进 secrets.env）。
  memory.search → engine.recall（embeddings 同型号和密钥变量，extraPaths → extra）。
  agents.*.sandbox（mode 不是 off 的）→ engine.sandbox（资源、prune）和 engine.sandbox.agents.<id>（access、browser）：这台机器有 docker
    才写；镜像、网络不搬（我们的沙箱只用自己的镜像、只接不做 NAT 的网络），写进「要人做的」。
  tools.web.search → engine.web.search（brave / tavily / exa / perplexity / searxng；密钥只记变量名）。
  agents.defaults.skills、agents.entries.<id>.skills → engine.skills.agents。
  主机命令：OpenClaw 默认不审批（security full），我们默认全关：只有写明了的放行名单（exec approvals 的 allowlist，不带 argPattern 的）
    才转成 engine.host_exec.agents.<id>.allow，没有就关着，写进「要人做的」。security full 一律不转。
  定时任务：agentTurn / systemEvent（让 Agent 说一句的）→ 内核定时任务（时间照 engine/when.py 的写法）；command → 「只跑命令」的，命令得是
    BRIDGES 里的工具或 engine.jobs.scripts 白名单里的脚本，不然写进「要人做的」；heartbeat:* → 那个工作区的 HEARTBEAT.md（或 OpenClaw 新版
    放在 cron_job_scratch 里的清单）有内容才转成 engine.heartbeat；skill-collection-review:*、memory-core 的做梦：不转。转过来的一律先停着。
  session.reset.atHour 不是 4、channels.<x>.enabled、tools.media、hooks.internal.entries：不转，各写一句。
  mcp.servers → mcp_servers（指回 OpenMousse 自己 /mcp 的不转）。
  工作区的 AGENTS.md、SOUL.md、IDENTITY.md、USER.md、MEMORY.md、memory/ 不搬：server.json 的 workspace、agent_workspaces 指过去
    （workspace 还是安装器建的 ~/.openmousse/workspace 才换，同 packs/core/setup.py）；别的 Agent 在 groups 表里补一行（--apply）。
  threads.model：订阅优先、Claude Code 登录着时 anthropic/* 换成 claude-code/*，只在 --apply 时做，先备份数据库。
"""
from __future__ import annotations

import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

import config
from i18n import L

from .common import (OWN_WORKSPACE, SENSITIVE, Plan, SourceError, env_name_for, env_ref, get, known_provider, literal, loads, put, read_env,
                     read_json5, read_text, same_dir, sqlite_copy, table, tilde)
from .common import canonical as canon

ALIASES = {"opus": "anthropic/claude-opus-5-5", "sonnet": "anthropic/claude-sonnet-5-5", "gpt": "openai/gpt-5.4", "gpt-mini": "openai/gpt-5.4-mini",
           "gpt-nano": "openai/gpt-5.4-nano", "gemini": "google/gemini-4-argon", "gemini-flash": "google/gemini-3.8-flash",
           "gemini-flash-lite": "google/gemini-3.5-flash-lite"}   # OpenClaw 的 DEFAULT_MODEL_ALIASES（src/config/defaults.ts）
RENAMED = {"qwen": "dashscope", "qwencloud": "dashscope", "modelstudio": "dashscope", "vercel-ai-gateway": "vercel", "llama-cpp": "llamacpp"}
LEGACY_CODEX = ("openai-codex", "codex")
API = {"openai-completions": "openai", "anthropic-messages": "anthropic", "openai-responses": "openai-responses"}
WEB = {"brave": "BRAVE_API_KEY", "tavily": "TAVILY_API_KEY", "exa": "EXA_API_KEY", "perplexity": "PERPLEXITY_API_KEY", "searxng": ""}
EMBED = {"openai": ("openai", "OPENAI_API_KEY"), "gemini": ("gemini", "GEMINI_API_KEY"), "auto": ("openai", "OPENAI_API_KEY")}
ACCESS = ("none", "ro", "rw")
SKIP_DECL = ("skill-collection-review:", "memory-core:")


def dur(minutes: float) -> str:
    """分钟数 → when 认的写法：120 → 2h，45 → 45m（最少 1 分钟）。"""
    m = max(1, round(minutes))
    return f"{m // 60}h" if m % 60 == 0 else f"{m}m"


class Source:
    """读好的一个 OpenClaw 家：配置、库里的几张表、.env 的变量名。"""

    def __init__(self, home: Path):
        self.home = home
        p = home / "openclaw.json"
        if not p.is_file():
            raise SourceError(L(f"{tilde(home)} 里没有 openclaw.json：不是 OpenClaw 的家？用 --openclaw-home 指到它",
                                f"There's no openclaw.json in {tilde(home)}: is that OpenClaw's home? Point --openclaw-home at it"))
        self.cfg = read_json5(p)
        self.env_path = home / ".env"
        self.env = read_env(self.env_path)
        self.jobs: list[dict] = []
        self.scratch: dict[str, str] = {}
        self.approvals: dict | None = None
        with sqlite_copy(home / "state" / "openclaw.sqlite") as conn:
            if table(conn, "cron_jobs"):
                for r in conn.execute("SELECT * FROM cron_jobs ORDER BY sort_order, updated_at, job_id"):
                    job = loads(r["job_json"], {}) or {}
                    self.jobs.append({**job, "id": r["job_id"], "name": r["name"] or job.get("name"), "declarationKey": r["declaration_key"]
                                      or job.get("declarationKey"), "enabled": bool(r["enabled"]), "agentId": r["agent_id"] or job.get("agentId"),
                                      "payloadKind": r["payload_kind"], "state": loads(r["state_json"], {})})
            if table(conn, "cron_job_scratch"):
                for r in conn.execute("SELECT job_id, content FROM cron_job_scratch"):
                    if r["content"]:
                        self.scratch[r["job_id"]] = r["content"]
            if table(conn, "exec_approvals_config"):
                r = conn.execute("SELECT raw_json FROM exec_approvals_config ORDER BY rowid DESC LIMIT 1").fetchone()
                self.approvals = loads(r["raw_json"], None) if r else None
            conn_profiles = self._store(conn)
        self.auth = self._auth(conn_profiles)
        if not self.jobs:  # 2026.6.1 以前：cron/jobs.json
            old = loads(read_text(home / "cron" / "jobs.json"), {}) or {}
            for job in old.get("jobs") if isinstance(old, dict) and isinstance(old.get("jobs"), list) else []:
                if isinstance(job, dict):
                    self.jobs.append({**job, "payloadKind": (job.get("payload") or {}).get("kind")})
        if self.approvals is None:
            self.approvals = loads(read_text(home / "exec-approvals.json"), None)


    @staticmethod
    def _store(conn) -> dict:
        """auth_profile_store 里 primary 那一行的 profiles（OpenClaw 新版把模型的凭证放在库里）。"""
        if not table(conn, "auth_profile_store"):
            return {}
        r = conn.execute("SELECT store_json FROM auth_profile_store WHERE store_key='primary'").fetchone()
        d = loads(r["store_json"], {}) if r else {}
        return d.get("profiles") if isinstance(d, dict) and isinstance(d.get("profiles"), dict) else {}

    def _auth(self, shared: dict) -> dict[str, dict]:
        """模型的凭证档案（id → {type, provider, key | keyRef | token…}）：共用的库、主 Agent 自己的库、老的 auth-profiles.json。
        只为了两件事：按量的 API 密钥（--copy-secrets 时才抄）、keyRef 指的变量名。登录令牌（订阅、OAuth）一概不抄。"""
        out = dict(shared)
        agent = self.home / "agents" / "main" / "agent"
        with sqlite_copy(agent / "openclaw-agent.sqlite") as conn:
            out.update(self._store(conn))
        old = loads(read_text(agent / "auth-profiles.json"), {}) or {}
        if isinstance(old, dict) and isinstance(old.get("profiles"), dict):
            for k, v in old["profiles"].items():
                out.setdefault(k, v)
        return {str(k): v for k, v in out.items() if isinstance(v, dict)}


class Planner:
    def __init__(self, src: Source, plan: Plan, detect):
        self.s, self.p, self.cfg = src, plan, src.cfg
        self._detect = detect
        self._cc: dict | None | bool = False      # 还没查
        self.tz = str(config.raw().get("timezone") or "UTC")
        d = get(self.cfg, "agents", "defaults") or {}
        self.defaults = d if isinstance(d, dict) else {}
        self.aliases = dict(ALIASES)
        for ref, v in (self.defaults.get("models") or {}).items() if isinstance(self.defaults.get("models"), dict) else []:
            if isinstance(v, dict) and isinstance(v.get("alias"), str) and v["alias"].strip():
                self.aliases[v["alias"].strip()] = ref
        self.profiles = get(self.cfg, "auth", "profiles") or {}
        self.sub_first = self._sub_first()
        self.codex = self._codex()
        self.entries = self._entries()
        self.used: set[str] = set()               # 迁过来的模型用到的（按量的）几家：看它们的密钥从哪来
        self.ids: dict[str, str] = {}             # OpenClaw 的 Agent id → 我们的（建得成的）
        self.workspaces: dict[str, Path] = {}

    # —— 认人：订阅、Codex、Agent ——————————————————————————————————————————————

    def _mode(self, pid: str) -> str:
        p = self.profiles.get(pid) if isinstance(self.profiles, dict) else None
        return str(p.get("mode") or "") if isinstance(p, dict) else ""

    def _first(self, provider: str) -> str | None:
        order = get(self.cfg, "auth", "order", provider)
        if isinstance(order, list) and order:
            return str(order[0])
        mine = [k for k, v in (self.profiles.items() if isinstance(self.profiles, dict) else []) if isinstance(v, dict) and v.get("provider") == provider]
        return mine[0] if len(mine) == 1 else None

    def _sub_first(self) -> bool:
        first = self._first("anthropic")
        return bool(first) and (first.endswith((":subscription", ":claude-cli")) or self._mode(first) in ("token", "oauth"))

    def _codex(self) -> bool:
        first = self._first("openai") or self._first("openai-codex")
        return bool(first) and (self._mode(first) == "oauth" or first.startswith("openai-codex:"))

    def cc(self) -> dict | None:
        """这台机器上的 Claude Code（装了、登录着、是它自己存的登录）；查一次。"""
        if self._cc is False:
            from engine import runners
            try:
                info = self._detect("claude-code")
            except Exception:  # noqa: BLE001 — 查不了就当没有
                info = None
            self._cc = info if runners.own_login(info) else None
        return self._cc  # type: ignore[return-value]

    def _entries(self) -> dict[str, dict]:
        e = get(self.cfg, "agents", "entries")
        if isinstance(e, dict):
            return {str(k): v for k, v in e.items() if isinstance(v, dict)}
        out: dict[str, dict] = {}
        for item in get(self.cfg, "agents", "list") or []:  # 老写法 agents.list: [{id, default?, …}]
            if isinstance(item, dict) and item.get("id"):
                out["main" if item.get("default") else str(item["id"])] = item
        return out

    # —— 模型引用 ——————————————————————————————————————————————————————————————

    def convert(self, ref: str) -> tuple[str | None, str]:
        """OpenClaw 的一个模型引用 → (我们的 或 None, 怎么来的：plain / sub / sub-api / codex / unknown / wildcard)。"""
        r = str(ref or "").strip()
        r = self.aliases.get(r, r)
        if "/" not in r:
            return None, "unknown"
        pid, model = r.split("/", 1)
        if model == "*" or "*" in model:
            return None, "wildcard"
        runtime = str(get(self.defaults, "models", r, "agentRuntime", "id") or "")
        if pid in LEGACY_CODEX or (pid == "openai" and (self.codex or runtime == "codex")):
            return f"chatgpt/{model}", "codex"
        if pid == "anthropic" and (self.sub_first or runtime == "claude-cli"):
            return (f"claude-code/{model}", "sub") if self.cc() else (canon(r), "sub-api")
        pid = RENAMED.get(pid, pid)
        if not known_provider(pid, self.p.patch):
            return None, "unknown"
        self.used.add(pid)
        return canon(f"{pid}/{model}"), "plain"

    # —— 一样一样 ——————————————————————————————————————————————————————————————

    def run(self) -> None:
        if "$include" in self.cfg:
            self.p.ask("$include", L("openclaw.json 用了 $include：被它带进来的那几个文件没读，里面的设置要自己看",
                                     "openclaw.json uses $include: the files it pulls in weren't read; check those settings yourself"))
        self.agents()
        self.providers()       # 先于模型：自定义的服务商写进 patch 以后，用到它的模型才认得
        self.models()
        self.recall()
        self.sandbox()
        self.web()
        self.skills()
        self.host_exec()
        self.cron()
        self.mcp()
        self.misc()
        self.env_file()
        self.keys()
        if self.sub_first and self.cc():
            self.p.threads = {"from": "anthropic/", "to": "claude-code/"}
            self.p.ok("threads.model", L("对话里选过 anthropic/… 的换成 claude-code/…（只在 --apply 时做，先备份数据库）",
                                         "Chats set to anthropic/… switch to claude-code/… (only with --apply, after backing up the database)"))

    def ws_of(self, aid: str) -> Path:
        """照 OpenClaw 的 resolveAgentWorkspaceDir：自己写了的；main 用 defaults.workspace（没写 = <家>/workspace）；
        别的 Agent 用 <defaults.workspace>/<id>，没写 defaults.workspace 就是 <家>/workspace-<id>。"""
        own = str((self.entries.get(aid) or {}).get("workspace") or "").strip()
        if own:
            return Path(own).expanduser()
        base = str(self.defaults.get("workspace") or "").strip()
        if aid == "main":
            return Path(base).expanduser() if base else self.s.home / "workspace"
        return Path(base).expanduser() / aid if base else self.s.home / f"workspace-{aid}"

    def agents(self) -> None:
        main_ws = self.ws_of("main")
        self.ids["main"] = "main"
        if main_ws.is_dir():
            self.workspaces["main"] = main_ws
            cur = config.raw().get("workspace")
            if cur is None or same_dir(Path(str(cur)), OWN_WORKSPACE):
                self.p.replace["workspace"] = str(main_ws)
                self.p.ok("agents.defaults.workspace", L(f"主对话的工作区指到 {tilde(main_ws)}（AGENTS.md、SOUL.md、MEMORY.md、memory/ 不搬，原地用）",
                                                         f"The main chat's workspace points at {tilde(main_ws)} (AGENTS.md, SOUL.md, MEMORY.md, memory/ stay where they are)"))
                if cur is not None:
                    self.p.ask("workspace", L(f"之前的档案和记忆还在 {tilde(OWN_WORKSPACE)}（没搬）：想接着用就挑着拷过去",
                                              f"The earlier profile and memory are still in {tilde(OWN_WORKSPACE)} (not moved): copy over what you want to keep"))
                prof = config.raw().get("profile")
                if prof is None or same_dir(Path(str(prof)), OWN_WORKSPACE / "USER.md"):
                    shared = self.s.home / "shared" / "profile" / "USER.md"
                    target = shared if shared.is_file() else main_ws / "USER.md"
                    if target.is_file():
                        self.p.replace["profile"] = str(target)
                self.p.ask("workspace", L("从现在起 OpenMousse 和 OpenClaw 用同一个工作区：两边别同时开着，不然会一起改这些文件",
                                          "From now on OpenMousse and OpenClaw share this workspace: don't run both at once, or they'll both edit these files"))
            elif not same_dir(Path(str(cur)), main_ws):
                self.p.ask("workspace", L(f"server.json 的 workspace 是 {tilde(str(cur))}，没换：OpenClaw 的在 {tilde(main_ws)}；要用它就把 workspace 改过去",
                                          f"server.json's workspace is {tilde(str(cur))} and wasn't changed: OpenClaw's is {tilde(main_ws)}; point workspace there to use it"))
        else:
            self.p.no("agents.defaults.workspace", L(f"主对话的工作区 {tilde(main_ws)} 不在", f"The main chat's workspace {tilde(main_ws)} isn't there"))
        taken = {"main"}
        for oc_id, e in self.entries.items():
            if oc_id == "main":
                continue
            ws = self.ws_of(oc_id)
            if not ws.is_dir():
                self.p.no(f"agents.entries.{oc_id}", L(f"工作区 {tilde(ws)} 不在：没建这个 Agent", f"Workspace {tilde(ws)} isn't there: this Agent wasn't created"))
                continue
            aid = re.sub(r"[^a-z0-9-]+", "-", oc_id.lower()).strip("-")[:40] or "agent"
            n, base = 2, aid
            while aid in taken:
                aid, n = f"{base}-{n}", n + 1
            taken.add(aid)
            self.ids[oc_id], self.workspaces[aid] = aid, ws
            name = str(e.get("name") or get(e, "identity", "name") or oc_id).strip()[:40] or oc_id
            put(self.p.patch, "agent_workspaces", aid, str(ws))
            self.p.agents.append({"id": aid, "name": name, "workspace": ws, "from": f"agents.entries.{oc_id}"})
            self.p.ok(f"agents.entries.{oc_id}", L(f"Agent「{name}」（{aid}），工作区指到 {tilde(ws)}", f'Agent "{name}" ({aid}), workspace {tilde(ws)}'))

    def models(self) -> None:
        for oc_id, e in self.entries.items():  # 每个 Agent 自己的模型（agents.entries.<id>.model）→ 它对话的模型
            aid = self.ids.get(oc_id)
            own = e.get("model")
            own = own if isinstance(own, str) else (own or {}).get("primary") if isinstance(own, dict) else None
            if oc_id == "main" or not aid or not own:
                continue
            ref, how = self.convert(own)
            if ref:
                next(a for a in self.p.agents if a["id"] == aid)["model"] = ref
                self.p.ok(f"agents.entries.{oc_id}.model", L(f"→ Agent {aid} 对话的模型 = {ref}", f"→ Agent {aid}'s chat model = {ref}"))
            else:
                self.p.no(f"agents.entries.{oc_id}.model", self._why(own, how))
        main_own = (self.entries.get("main") or {}).get("model")
        m = main_own or (self.defaults.get("model") if "model" in self.defaults else self.cfg.get("defaultModel"))
        primary = m if isinstance(m, str) else (m or {}).get("primary") if isinstance(m, dict) else None
        if primary:
            ref, how = self.convert(primary)
            self._model("agents.defaults.model.primary", primary, ref, how, ("engine", "model"))
        fallbacks: list[str] = []
        for f in (m.get("fallbacks") or []) if isinstance(m, dict) else []:
            ref, how = self.convert(str(f))
            if how == "plain" and ref:
                fallbacks.append(ref)
            elif how in ("sub", "sub-api"):
                self.p.ok("agents.defaults.model.fallbacks", L(f"{f} 不进备用链：OpenClaw 上它走你的 Claude 订阅，备用链只放按量的",
                                                               f"{f} left out of the fallbacks: on OpenClaw it ran on your Claude plan, and fallbacks are pay-as-you-go only"))
            elif how == "codex" and ref:
                self._chatgpt(ref, "agents.defaults.model.fallbacks")
            else:
                self.p.no("agents.defaults.model.fallbacks", self._why(str(f), how))
        if fallbacks:
            put(self.p.patch, "engine", "fallbacks", list(dict.fromkeys(fallbacks)))
            self.p.ok("agents.defaults.model.fallbacks", L(f"→ engine.fallbacks = {', '.join(fallbacks)}", f"→ engine.fallbacks = {', '.join(fallbacks)}"))
        allow = get(self.defaults, "modelPolicy", "allow")
        if allow is None and not isinstance(self.defaults.get("modelPolicy"), dict) and not get(self.cfg, "meta", "migrations", "modelPolicyAllowlist"):
            allow = list((self.defaults.get("models") or {}).keys()) if isinstance(self.defaults.get("models"), dict) else None
        out: list[str] = []
        for a in allow or []:
            ref, how = self.convert(str(a))
            if ref:
                out.append(ref)
                if how == "codex":
                    self._chatgpt(ref, "agents.defaults.modelPolicy.allow", quiet=True)
            else:
                self.p.no("agents.defaults.modelPolicy.allow", self._why(str(a), how))
        if out:
            put(self.p.patch, "engine", "models", list(dict.fromkeys(out)))
            self.p.ok("agents.defaults.modelPolicy.allow", L(f"→ engine.models（app 里能换的）= {', '.join(out)}", f"→ engine.models (switchable in the app) = {', '.join(out)}"))
        hb = get(self.defaults, "heartbeat", "model")
        if isinstance(hb, str) and hb.strip():
            ref, how = self.convert(hb)
            self._model("agents.defaults.heartbeat.model", hb, ref, how, ("engine", "use", "background"))
        cur = get(config.raw(), "engine", "use") or {}
        if not any(k in cur for k in ("card", "doorman", "json")):
            self.p.ask(L("名片 Agent / Doorman / 一次性 JSON", "Card agent / Doorman / one-shot JSON"),
                       L("OpenClaw 上这三样走 llm-task，用的是主模型（可能是你的订阅）；OpenMousse 不替你定：在 app 的「设置 → 按用途选模型」里选"
                         "（engine.use.card / doorman / json），外人回合不花你的套餐",
                         "On OpenClaw these ran through llm-task on your main model (possibly your plan). OpenMousse doesn't pick for you: choose in the "
                         "app under Settings → Models by purpose (engine.use.card / doorman / json); outsider turns never spend your plan"))

    def _why(self, ref: str, how: str) -> str:
        if how == "wildcard":
            return L(f"{ref}：通配的不转（engine.models 要一个个写）", f"{ref}: wildcards aren't converted (engine.models lists models one by one)")
        return L(f"{ref}：OpenMousse 不认识这一家（目录里没有，openclaw.json 也没写地址）", f"{ref}: OpenMousse doesn't know this provider (not in the catalog, "
                 "and openclaw.json gives no address)")

    def _model(self, what: str, raw_ref: str, ref: str | None, how: str, path: tuple[str, ...]) -> None:
        key = ".".join(path)
        if not ref:
            self.p.no(what, self._why(raw_ref, how))
            return
        put(self.p.patch, *path, ref)
        if how == "sub":
            self.p.ok(what, L(f"→ {key} = {ref}（OpenClaw 上走你的 Claude 订阅；这台机器的 Claude Code 登录着，用它）",
                              f"→ {key} = {ref} (on OpenClaw this ran on your Claude plan; Claude Code is signed in on this machine, so it uses that)"))
        elif how == "sub-api":
            self.p.ok(what, f"→ {key} = {ref}")
            self.p.ask(key, L(f"OpenClaw 上 {raw_ref} 走你的 Claude 订阅，这台机器没装或没登录 Claude Code：先照旧写 {ref}，按量计费（要 ANTHROPIC_API_KEY）。"
                              "装好、登录 Claude Code（claude auth login）以后，在 app 的「设置 → 按用途选模型」换成 claude-code/…",
                              f"On OpenClaw {raw_ref} ran on your Claude plan, but Claude Code isn't installed or signed in here: it's written as {ref} for "
                              "now, billed per use (needs ANTHROPIC_API_KEY). Once Claude Code is installed and signed in (claude auth login), switch to "
                              "claude-code/… under Settings → Models by purpose"))
        elif how == "codex":
            self._chatgpt(ref, what, quiet=True)
            self.p.ok(what, L(f"→ {key} = {ref}（OpenClaw 上用 Codex / ChatGPT 登录）", f"→ {key} = {ref} (signed in with Codex / ChatGPT on OpenClaw)"))
        else:
            self.p.ok(what, f"→ {key} = {ref}")

    def _chatgpt(self, ref: str, what: str, quiet: bool = False) -> None:
        cur = get(self.p.patch, "engine", "chatgpt", "models") or []
        if ref not in cur:
            put(self.p.patch, "engine", "chatgpt", "models", [*cur, ref])
        if not quiet:
            self.p.ok(what, L(f"{ref} 放进 engine.chatgpt.models，不进备用链（OpenClaw 上用 Codex 登录；备用链只放按量的）",
                              f"{ref} goes into engine.chatgpt.models, not the fallbacks (Codex sign-in on OpenClaw; fallbacks are pay-as-you-go only)"))
        if not any(x.what == "ChatGPT" for x in self.p.todo):
            self.p.ask("ChatGPT", L("要用 ChatGPT 套餐：在 app 的「设置 → 服务商」里 Continue with ChatGPT 登录一次（OpenClaw 的登录不搬：令牌不抄）",
                                    "To use your ChatGPT plan, sign in once with Continue with ChatGPT under Settings → Providers in the app (OpenClaw's "
                                    "sign-in isn't moved: tokens aren't copied)"))

    def _key(self, what: str, v, name: str) -> str | None:
        """一处密钥 → 变量名（记进 plan.secrets / held）。${VAR} → VAR；直接写的 → name（值放 held）；别的来源 → None（写一句）。"""
        if v in (None, ""):
            return None
        if ref := env_ref(v):
            if ref in self.s.env:
                self.p.secrets[ref] = self.s.env_path
            return ref
        if val := literal(v):
            self.p.held[name] = val
            return name
        self.p.ask(what, L("密钥放在 OpenClaw 的密钥库 / 文件 / 命令里：没转，在 app 里重新填", "The key lives in OpenClaw's secret store, a file or a "
                           "command: not converted; enter it again in the app"))
        return None

    def providers(self) -> None:
        from engine import catalog
        for pid, p in (get(self.cfg, "models", "providers") or {}).items():
            if not isinstance(p, dict):
                continue
            new = RENAMED.get(pid, pid)
            what = f"models.providers.{pid}"
            e = catalog.entry(new)
            api = API.get(str(p.get("api") or "")) if p.get("api") else None
            if p.get("api") and api is None and e is None:
                self.p.no(what, L(f"接口 {p['api']} 内核不认（只认 OpenAI 兼容、Anthropic、Responses）", f"The kernel doesn't speak {p['api']} (only "
                                  "OpenAI-compatible, Anthropic and Responses)"))
                continue
            out: dict = {}
            url = str(p.get("baseUrl") or "").strip()
            if e is not None:
                if url and url.rstrip("/") != str(e["url"]).rstrip("/"):
                    out["url"] = url
                default_env = e.get("key_env") or ""
                name = self._key(what + ".apiKey", p.get("apiKey"), default_env or env_name_for(new))
                if name is not None and name != default_env:
                    out["key_env"] = name
            else:
                if not url:
                    self.p.no(what, L("目录里没有这一家，也没写地址（baseUrl）", "Not in the catalog and no address (baseUrl)"))
                    continue
                out = {"api": api or "openai", "url": url, "name": pid}
                out["key_env"] = "" if str(p.get("auth") or "") == "none" else (self._key(what + ".apiKey", p.get("apiKey"), env_name_for(new)) or "")
            if p.get("headers"):
                self.p.ask(what + ".headers", L("请求头没转（可能带着令牌）：要的话在 server.json 的 engine.providers 里自己写", "Headers weren't converted "
                                                "(they may carry tokens): add them under engine.providers in server.json if you need them"))
            if out:
                put(self.p.patch, "engine", "providers", new, out)
            label = f"engine.providers.{new}" + (L(f"（OpenClaw 叫 {pid}）", f" (called {pid} on OpenClaw)") if new != pid else "")
            self.p.ok(what, f"→ {label}" + (L("：", ": ") + ", ".join(out) if out else L("：目录里有，不用写", ": in the catalog, nothing to write")))

    def keys(self) -> None:
        """用到的几家按量的密钥从哪来：.env 里有那个变量 → 只记名字；OpenClaw 库里的 API 密钥档案 → 只在 --copy-secrets 时抄；
        登录令牌（订阅、OAuth）不抄。哪儿都没有、这台服务器也没有 → 写进「要人做的」。"""
        import secretstore
        from engine import catalog
        for pid in sorted(self.used | {"anthropic"} if any(r.startswith("anthropic/") for r in self._refs()) else self.used):
            e = catalog.entry(pid)
            name = str(get(self.p.patch, "engine", "providers", pid, "key_env") or (e or {}).get("key_env") or "")
            if not name:
                continue
            if name in self.s.env:
                self.p.secrets[name] = self.s.env_path
                continue
            found = False
            for prof_id, prof in self.s.auth.items():
                if str(prof.get("provider") or "") != pid:
                    continue
                kind = str(prof.get("type") or "")
                if kind == "api_key" and (ref := env_ref(prof.get("keyRef"))):
                    if ref in self.s.env:
                        self.p.secrets[ref] = self.s.env_path
                    if ref != name:
                        put(self.p.patch, "engine", "providers", pid, "key_env", ref)
                    found = True
                elif kind == "api_key" and (val := literal(prof.get("key"))):
                    self.p.held[name] = val
                    self.p.ask(f"auth:{prof_id}", L(f"{pid} 的 API 密钥存在 OpenClaw 的库里：加 --copy-secrets 抄进 secrets.env（{name}），或者在 app 里重新填",
                                                    f"{pid}'s API key is stored in OpenClaw's database: add --copy-secrets to copy it into secrets.env "
                                                    f"({name}), or enter it again in the app"))
                    found = True
                elif kind in ("token", "oauth"):
                    self.p.no(f"auth:{prof_id}", L("登录令牌（订阅 / OAuth）不抄：在 OpenMousse 里自己登录一次", "Sign-in tokens (plans / OAuth) aren't copied: "
                                                   "sign in once in OpenMousse"))
            if pid == "anthropic" and any(x.what.startswith("engine.") and "ANTHROPIC_API_KEY" in x.text for x in self.p.todo):
                continue  # 「先照旧写 anthropic/…（要 ANTHROPIC_API_KEY）」那一句已经说了
            if not found and not secretstore.lookup(name)[0]:
                self.p.ask(name, L(f"{pid} 要密钥 {name}：OpenClaw 那边没找到，这台服务器也没有。在 app 的「设置 → 服务商」里填",
                                   f"{pid} needs the key {name}: not found on OpenClaw's side or on this server. Enter it under Settings → Providers in the app"))

    def _refs(self) -> list[str]:
        e = self.p.patch.get("engine") or {}
        out = [e.get("model"), *(e.get("fallbacks") or []), *(e.get("models") or []), get(e, "use", "background")]
        return [str(x) for x in out if isinstance(x, str)]

    def recall(self) -> None:
        ms = get(self.cfg, "memory", "search") or self.cfg.get("memorySearch") or self.defaults.get("memorySearch")
        if not isinstance(ms, dict) or ms.get("enabled") is False:
            return
        prov = str(ms.get("provider") or "").strip().lower()
        if prov:
            spec: dict | None = None
            remote = ms.get("remote") if isinstance(ms.get("remote"), dict) else {}
            base = str(remote.get("baseUrl") or "").strip()
            if prov in EMBED:
                backend, default_env = EMBED[prov]
                spec = {"backend": backend}
                name = self._key("memory.search.remote.apiKey", remote.get("apiKey"), default_env)
                if name and name != default_env:
                    spec["key_env"] = name
                elif default_env in self.s.env:
                    self.p.secrets[default_env] = self.s.env_path
                if base:
                    spec["url"] = base
            elif prov == "ollama":
                spec = {"backend": "openai", "url": (base or "http://127.0.0.1:11434").rstrip("/").removesuffix("/v1") + "/v1", "key_env": ""}
            else:
                self.p.no("memory.search.provider", L(f"{prov}：内核按意思搜只接 Gemini 和 OpenAI 兼容的（含本机 Ollama）", f"{prov}: the kernel's "
                                                      "semantic search takes Gemini and OpenAI-compatible services (local Ollama included)"))
            if spec is not None:
                if ms.get("model"):
                    spec["model"] = str(ms["model"])
                put(self.p.patch, "engine", "recall", "embeddings", spec)
                self.p.ok("memory.search", L(f"→ engine.recall.embeddings：{spec['backend']} {spec.get('model', '')}（按意思搜照开着：记忆和对话会发给它算向量，同 OpenClaw）",
                                             f"→ engine.recall.embeddings: {spec['backend']} {spec.get('model', '')} (semantic search stays on: memory and "
                                             "chats are sent to it for vectors, as on OpenClaw)"))
        extra: list = []
        for x in ms.get("extraPaths") or []:
            path = x if isinstance(x, str) else x.get("path") if isinstance(x, dict) else None
            if path:
                extra.append(str(Path(str(path)).expanduser()))
                if isinstance(x, dict) and x.get("pattern"):
                    self.p.ask("memory.search.extraPaths", L(f"{path} 的 pattern（{x['pattern']}）没转：整个文件夹的 .md 都会进索引",
                                                             f"The pattern for {path} ({x['pattern']}) wasn't converted: every .md in the folder is indexed"))
        for oc_id, e in self.entries.items():
            aid = self.ids.get(oc_id)
            for x in get(e, "memory", "search", "extraPaths") or get(e, "memorySearch", "extraPaths") or []:
                path = x if isinstance(x, str) else x.get("path") if isinstance(x, dict) else None
                if path and aid:
                    extra.append({"path": str(Path(str(path)).expanduser()), "agents": [aid]})
        if extra:
            prof = config.settings.profile
            if prof.is_file() and str(prof) not in extra:
                extra.insert(0, str(prof))  # 不写 extra 时默认有档案：写了也带上，不少一样
            put(self.p.patch, "engine", "recall", "extra", extra)
            self.p.ok("memory.search.extraPaths", L(f"→ engine.recall.extra（{len(extra)} 项）", f"→ engine.recall.extra ({len(extra)} item{'' if len(extra) == 1 else 's'})"))

    def sandbox(self) -> None:
        conf: dict[str, dict] = {}
        d = self.defaults.get("sandbox") if isinstance(self.defaults.get("sandbox"), dict) else {}
        for oc_id in ["main", *[k for k in self.entries if k != "main"]]:
            s = (self.entries.get(oc_id) or {}).get("sandbox")
            s = {**d, **s} if isinstance(s, dict) else dict(d)
            mode = str(s.get("mode") or "off")
            if mode == "off" or (mode == "non-main" and oc_id == "main") or oc_id not in self.ids:
                continue
            conf[self.ids[oc_id]] = s
        if not conf:
            return
        top: dict = {}
        dock = d.get("docker") if isinstance(d.get("docker"), dict) else {}
        for k, ours in (("memory", "memory"), ("cpus", "cpus"), ("pidsLimit", "pids")):
            if dock.get(k) not in (None, ""):
                top[ours] = dock[k]
        prune = d.get("prune") if isinstance(d.get("prune"), dict) else {}
        if isinstance(prune.get("idleHours"), (int, float)):
            top["idle_minutes"] = int(prune["idleHours"] * 60)
        if isinstance(prune.get("maxAgeDays"), (int, float)):
            top["max_hours"] = int(prune["maxAgeDays"] * 24)
        agents: dict = {}
        for aid, s in conf.items():
            a = {"only": False}
            if str(s.get("workspaceAccess") or "") in ACCESS:
                a["access"] = s["workspaceAccess"]
            if get(s, "browser", "enabled") is True:
                a["browser"] = True
            agents[aid] = a
        if get(dock, "network") == "none":
            top["network"] = "none"
        elif get(dock, "network"):
            self.p.ask("agents.defaults.sandbox.docker.network", L(f"网络 {dock['network']} 没搬：OpenMousse 的沙箱只接不做 NAT 的网络（errand.py setup 建的）",
                                                                  f"Network {dock['network']} wasn't moved: OpenMousse's sandbox only joins a network "
                                                                  "without NAT (the one errand.py setup creates)"))
        if get(dock, "image"):
            self.p.ask("agents.defaults.sandbox.docker.image", L(f"镜像 {dock['image']} 没搬：OpenMousse 的沙箱用自己的镜像（只认 Doorman 的证书）；"
                                                                f"要自己的，照 sandbox/errand/Dockerfile 做一个再写 engine.sandbox.image",
                                                                f"Image {dock['image']} wasn't moved: OpenMousse's sandbox uses its own image (it only "
                                                                "trusts the Doorman's certificate); to use your own, build one from sandbox/errand/Dockerfile and "
                                                                "set engine.sandbox.image"))
        if not shutil.which("docker"):
            self.p.ask("agents.*.sandbox", L(f"OpenClaw 上 {', '.join(conf)} 在沙箱里跑；这台机器没有 docker，先没写 engine.sandbox。装好 Docker、跑过 "
                                             "python3 server/errand.py setup 以后再跑一次 mousse migrate openclaw --apply",
                                             f"On OpenClaw {', '.join(conf)} ran in a sandbox; this machine has no docker, so engine.sandbox wasn't written. "
                                             "Install Docker, run python3 server/errand.py setup, then run mousse migrate openclaw --apply again"))
            return
        put(self.p.patch, "engine", "sandbox", {**top, "agents": agents})
        self.p.ok("agents.*.sandbox", L(f"→ engine.sandbox（{', '.join(agents)} 各有自己的沙箱）", f"→ engine.sandbox (its own sandbox for {', '.join(agents)})"))

    def web(self) -> None:
        ws = get(self.cfg, "tools", "web", "search")
        if not isinstance(ws, dict) or ws.get("enabled") is False:
            return
        prov = str(ws.get("provider") or ("brave" if ws.get("apiKey") else "")).strip().lower()
        if not prov:
            return
        if prov not in WEB:
            self.p.no("tools.web.search.provider", L(f"{prov}：内核的搜索接 brave、tavily、exa、perplexity、searxng", f"{prov}: the kernel's search "
                                                     "takes brave, tavily, exa, perplexity and searxng"))
            return
        spec: dict = {"provider": prov}
        if isinstance(ws.get("maxResults"), int):
            spec["count"] = ws["maxResults"]
        key = get(self.cfg, "plugins", "entries", prov, "config", "webSearch", "apiKey") or ws.get("apiKey")
        if WEB[prov]:
            name = self._key("tools.web.search.apiKey", key, WEB[prov])
            if name and name != WEB[prov]:
                spec["providers"] = {prov: {"key_env": name}}
            elif WEB[prov] in self.s.env:
                self.p.secrets[WEB[prov]] = self.s.env_path
        else:
            self.p.ask("tools.web.search", L("searxng 的地址没搬：写进 engine.web.search.providers.searxng.url", "The searxng address wasn't moved: set "
                                             "engine.web.search.providers.searxng.url"))
        put(self.p.patch, "engine", "web", "search", spec)
        self.p.ok("tools.web.search", f"→ engine.web.search = {prov}")

    def skills(self) -> None:
        d = self.defaults.get("skills")
        if isinstance(d, list) and "main" in self.ids:
            put(self.p.patch, "engine", "skills", "agents", "main", [str(x) for x in d])
            self.p.ok("agents.defaults.skills", L(f"→ engine.skills.agents.main（{len(d)} 个；名单外的技能主对话看不到，同 OpenClaw）",
                                                  f"→ engine.skills.agents.main ({len(d)}; skills off the list are hidden from the main chat, as on OpenClaw)"))
        for oc_id, e in self.entries.items():
            aid = self.ids.get(oc_id)
            if oc_id == "main" or not aid:
                continue
            s = e.get("skills") if isinstance(e.get("skills"), list) else d if isinstance(d, list) else None
            if s is not None:
                put(self.p.patch, "engine", "skills", "agents", aid, [str(x) for x in s])
                self.p.ok(f"agents.entries.{oc_id}.skills", L(f"→ engine.skills.agents.{aid}（{len(s)} 个）", f"→ engine.skills.agents.{aid} ({len(s)})"))

    def host_exec(self) -> None:
        a = self.s.approvals if isinstance(self.s.approvals, dict) else {}
        per = a.get("agents") if isinstance(a.get("agents"), dict) else {}
        dflt = a.get("defaults") if isinstance(a.get("defaults"), dict) else {}
        exec_cfg = get(self.cfg, "tools", "exec") or {}
        loose = str(dflt.get("security") or "full") == "full" or str(exec_cfg.get("mode") or exec_cfg.get("security") or "") in ("full", "auto")
        for oc_id, aid in self.ids.items():
            entry = per.get(oc_id) or (per.get("default") if oc_id == "main" else None) or {}
            allow: list[str] = []
            narrow = 0
            for x in entry.get("allowlist") or [] if isinstance(entry, dict) else []:
                pat = x if isinstance(x, str) else x.get("pattern") if isinstance(x, dict) else None
                if not isinstance(pat, str) or not pat.strip():
                    continue
                if isinstance(x, dict) and str(x.get("argPattern") or "").strip():
                    narrow += 1
                    continue
                pat = str(Path(pat).expanduser()) if pat.startswith("~") else pat
                allow.append(f"{pat} ...")
            what = f"exec-approvals.agents.{oc_id}"
            sec = str((entry.get("security") if isinstance(entry, dict) else None) or dflt.get("security") or "full")
            if allow:
                put(self.p.patch, "engine", "host_exec", "agents", aid, {"allow": list(dict.fromkeys(allow)), "ask": "on-miss"})
                self.p.ok(what, L(f"→ engine.host_exec.agents.{aid}.allow（{len(allow)} 条；名单外的要你点头）",
                                  f"→ engine.host_exec.agents.{aid}.allow ({len(allow)}; anything else needs your OK)"))
            if narrow:
                self.p.ask(what, L(f"{narrow} 条放行只限定了参数（argPattern，OpenClaw「以后都放行」记下的）：没转，用到时 OpenMousse 会再问你",
                                   f"{narrow} allow entr{'y' if narrow == 1 else 'ies'} restricted by argPattern (OpenClaw's allow-always): not converted; "
                                   "OpenMousse will ask you when they come up"))
            if not allow and (sec == "full" or loose):
                self.p.ask(what, L(f"OpenClaw 上 {oc_id} 跑主机命令不审批（security full）；迁过来是关着的（OpenMousse 默认全关）。"
                                   f"要开，在 server.json 的 engine.host_exec.agents.{aid}.allow 写放行名单（engine/host_exec.py 开头有写法）",
                                   f"On OpenClaw {oc_id} ran host commands without approval (security full); here it's off (OpenMousse's default). "
                                   f"To turn it on, list allowed commands in engine.host_exec.agents.{aid}.allow in server.json (see engine/host_exec.py)"))

    # —— 定时任务和心跳 ——————————————————————————————————————————————————————————

    def when(self, sched: dict, what: str) -> str | None:
        from engine import when
        kind = str(sched.get("kind") or "")
        if kind == "cron" and sched.get("expr"):
            text = str(sched["expr"]).strip()
            tz = str(sched.get("tz") or "").strip()
            if tz and tz != self.tz:
                self.p.ask(what, L(f"OpenClaw 按 {tz} 算这个时间，OpenMousse 按 server.json 的 {self.tz}：看一下时间对不对",
                                   f"OpenClaw ran this in {tz}, OpenMousse uses server.json's {self.tz}: check the time"))
        elif kind == "every" and isinstance(sched.get("everyMs"), (int, float)):
            text = f"every {dur(sched['everyMs'] / 60000)}"
        elif kind == "at" and (sched.get("at") or sched.get("atMs")):
            at = sched.get("at") or datetime.fromtimestamp(float(sched["atMs"]) / 1000, timezone.utc).isoformat()
            text = str(at)
        else:
            self.p.no(what, L(f"时间写法 {kind or '?'} 转不了（只认 cron、every、at）", f"Schedule kind {kind or '?'} can't be converted (only cron, every, at)"))
            return None
        try:
            parsed = when.parse(text, when.now())
        except when.WhenError as e:
            self.p.no(what, str(e))
            return None
        if parsed["kind"] == "once" and when.next_after(parsed, when.now()) is None:
            self.p.no(what, L("那个时刻已经过了", "That time has already passed"))
            return None
        return text

    def command(self, payload: dict, what: str, name: str) -> dict | None:
        """OpenClaw 的 command（argv）→ 只跑命令的写法：BRIDGES 里的工具（argv 里是它的脚本，或者 mousse-tree），或 engine.jobs.scripts 里的脚本。"""
        import bridges
        argv = [str(x) for x in payload.get("argv") or []] if isinstance(payload.get("argv"), list) else []
        if not argv:
            return None
        i = 1 if Path(argv[0]).name.startswith("python") and len(argv) > 1 else 0
        prog = argv[i]
        for tool, spec in bridges.BRIDGES.items():
            script = spec[0]
            if (script == "mousse-tree" and Path(prog).name == "mousse-tree") or (script != "mousse-tree" and prog.endswith("/" + script.split("/", 1)[-1])
                                                                                    and Path(prog).name == Path(script).name):
                return {"tool": tool, "args": argv[i + 1:]}
        allowed = {str(Path(x).expanduser()) for x in get(config.raw(), "engine", "jobs", "scripts") or [] if isinstance(x, str)}
        path = str(Path(prog).expanduser())
        if path in allowed:
            return {"script": path, "args": argv[i + 1:]}
        self.p.ask(what, L(f"「{name}」跑的是 {' '.join(argv)[:120]}：不在 BRIDGES 里，也不在 engine.jobs.scripts 白名单里。要的话把脚本的绝对路径加进 "
                           "engine.jobs.scripts，再跑一次 mousse migrate openclaw --apply",
                           f'"{name}" runs {" ".join(argv)[:120]}: it isn\'t one of the command-line tools or in the engine.jobs.scripts allowlist. To '
                           "keep it, add the script's absolute path to engine.jobs.scripts and run mousse migrate openclaw --apply again"))
        return None

    def cron(self) -> None:
        seen: set[str] = set()
        for j in self.s.jobs:
            decl = str(j.get("declarationKey") or "")
            name = str(j.get("displayName") or j.get("name") or j.get("id") or "").strip()
            what = f"cron:{name or j.get('id')}"
            if decl.startswith(SKIP_DECL):
                self.p.no(what, L("OpenClaw 自己的整理任务（技能回看 / 记忆做梦）：OpenMousse 有自己的日结和修剪，不转",
                                  "OpenClaw's own housekeeping (skill review / memory dreaming): OpenMousse has its own digests and pruning"))
                continue
            if decl.startswith("heartbeat:") or j.get("payloadKind") == "heartbeat":
                oc_agent = decl.split(":", 1)[1] if decl.startswith("heartbeat:") else str(j.get("agentId") or "main")
                seen.add(oc_agent)
                self.heartbeat(j, oc_agent)
                continue
            oc_agent = str(j.get("agentId") or "main")
            aid = self.ids.get(oc_agent)
            if not aid:
                self.p.no(what, L(f"它的 Agent {oc_agent} 没迁过来", f"Its Agent {oc_agent} wasn't migrated"))
                continue
            sched = j.get("schedule") if isinstance(j.get("schedule"), dict) else {}
            text = self.when(sched, what)
            if text is None:
                continue
            payload = j.get("payload") if isinstance(j.get("payload"), dict) else {}
            kind = str(payload.get("kind") or j.get("payloadKind") or "")
            given: dict = {"name": name[:60] or None, "when": text}
            if kind in ("agentTurn", "systemEvent"):
                task = str(payload.get("message") or payload.get("text") or "").strip()
                if not task:
                    self.p.no(what, L("没有要说的话", "Nothing to say"))
                    continue
                given["task"] = task
                given["session"] = "isolated" if str(j.get("sessionTarget") or "") == "isolated" else "chat"
                if payload.get("model"):
                    ref, how = self.convert(str(payload["model"]))
                    if ref and how != "codex":
                        given["model"] = ref
            elif kind == "command":
                c = self.command(payload, what, name)
                if c is None:
                    continue
                given["command"] = c
                if payload.get("cwd") or payload.get("env"):
                    self.p.ask(what, L("cwd、env 没转：OpenMousse 在这个 Agent 的工作区里跑、环境变量去掉像密钥的",
                                       "cwd and env weren't converted: OpenMousse runs it in the Agent's workspace, without key-like variables"))
            else:
                self.p.no(what, L(f"任务种类 {kind or '?'} 转不了", f"Job kind {kind or '?'} can't be converted"))
                continue
            delivery = j.get("delivery") if isinstance(j.get("delivery"), dict) else {}
            given["level"] = "none" if str(delivery.get("mode") or "") == "none" else "quiet"
            if str(delivery.get("mode") or "") == "webhook":
                self.p.ask(what, L("OpenClaw 投到 webhook：OpenMousse 投进这个 Agent 的对话", "OpenClaw posted it to a webhook: OpenMousse posts to the Agent's chat"))
            was = L("OpenClaw 上开着", "on in OpenClaw") if j.get("enabled", True) else L("OpenClaw 上关着", "off in OpenClaw")
            self.p.jobs.append({"agent": aid, "given": {k: v for k, v in given.items() if v is not None}, "from": what, "was": was})
        if not seen:  # 老版本的心跳不是定时任务：照配置和 HEARTBEAT.md（主对话默认开着，每 30m）
            for oc_agent in ["main", *[k for k, e in self.entries.items() if k != "main" and isinstance(e.get("heartbeat"), dict)]]:
                conf = {**(self.defaults.get("heartbeat") or {}), **((self.entries.get(oc_agent) or {}).get("heartbeat") or {})}
                ws = self.workspaces.get(self.ids.get(oc_agent, ""))
                if str(conf.get("every") or "30m") not in ("0", "0m") and ws and (ws / "HEARTBEAT.md").is_file():
                    self.heartbeat({}, oc_agent)

    def heartbeat(self, j: dict, oc_agent: str) -> None:
        from engine import heartbeat as hb
        aid = self.ids.get(oc_agent)
        what = f"heartbeat:{oc_agent}"
        if not aid:
            self.p.no(what, L(f"它的 Agent {oc_agent} 没迁过来", f"Its Agent {oc_agent} wasn't migrated"))
            return
        ws = self.workspaces.get(aid)
        text = read_text(ws / "HEARTBEAT.md") if ws else None
        prompt = None
        if not text or hb.effectively_empty(text):
            scratch = self.s.scratch.get(str(j.get("id") or ""))
            if not scratch or hb.effectively_empty(scratch):
                self.p.no(what, L("HEARTBEAT.md 是空的、也没有清单：OpenMousse 没清单不跑心跳，不转", "HEARTBEAT.md is empty and there's no checklist: "
                                  "OpenMousse doesn't run a heartbeat without one, so it wasn't converted"))
                return
            prompt = scratch.strip()
        conf = {**(self.defaults.get("heartbeat") or {}), **((self.entries.get(oc_agent) or {}).get("heartbeat") or {})}
        every = conf.get("every")
        sched = j.get("schedule") if isinstance(j.get("schedule"), dict) else {}
        if isinstance(sched.get("everyMs"), (int, float)) and sched["everyMs"] > 0:
            every = dur(sched["everyMs"] / 60000)
        every = str(every or "30m")
        out: dict = {"every": "0", "level": "quiet"}
        ah = conf.get("activeHours") if isinstance(conf.get("activeHours"), dict) else None
        if ah and ah.get("start") and ah.get("end"):
            out["active"] = [str(ah["start"]), str(ah["end"])]
            if ah.get("timezone") and str(ah["timezone"]) != self.tz:
                self.p.ask(what, L(f"活跃时段按 {ah['timezone']} 算，OpenMousse 按 {self.tz}", f"Active hours were in {ah['timezone']}; OpenMousse uses {self.tz}"))
        if conf.get("lightContext") is False:
            out["light"] = False
        if oc_agent != "main" and isinstance(get(self.entries.get(oc_agent) or {}, "heartbeat", "model"), str):
            ref, how = self.convert(get(self.entries[oc_agent], "heartbeat", "model"))
            if ref and how != "codex":
                out["model"] = ref
        if prompt:
            out["prompt"] = prompt
        if conf.get("prompt"):
            self.p.ask(what, L("OpenClaw 的心跳提示词改过：OpenMousse 用自己的（照清单看，没事回 NO_REPLY），没转",
                               "OpenClaw's heartbeat prompt was customised: OpenMousse uses its own (follow the checklist, NO_REPLY when nothing's up)"))
        put(self.p.patch, "engine", "heartbeat", aid, out)
        src = L("清单在 HEARTBEAT.md", "checklist in HEARTBEAT.md") if not prompt else L("清单从 OpenClaw 库里搬进 prompt", "checklist copied into prompt")
        self.p.ok(what, L(f"→ engine.heartbeat.{aid}（{src}），先关着", f"→ engine.heartbeat.{aid} ({src}), off for now"))
        self.p.ask(what, L(f"心跳先关着：要开，把 server.json 里 engine.heartbeat.{aid}.every 从 \"0\" 改成 \"{every}\"（OpenClaw 上是每 {every}）",
                           f'The heartbeat is off for now: to turn it on, change engine.heartbeat.{aid}.every in server.json from "0" to "{every}" '
                           f"(it was every {every} on OpenClaw)"))

    def mcp(self) -> None:
        servers = get(self.cfg, "mcp", "servers") or {}
        for name, s in servers.items() if isinstance(servers, dict) else []:
            what = f"mcp.servers.{name}"
            if not isinstance(s, dict):
                continue
            url = str(s.get("url") or "").strip()
            if url and self._ours(url, s):
                self.p.no(what, L("这是 OpenMousse 自己的 /mcp（OpenClaw 用它调我们的工具）：自带内核用不着", "This is OpenMousse's own /mcp (OpenClaw used it "
                                  "to call our tools): the built-in engine doesn't need it"))
                continue
            if str(s.get("auth") or "") == "oauth":
                self.p.no(what, L("要 OAuth 登录的：在 app 的连接器里接", "Needs an OAuth sign-in: connect it under Connectors in the app"))
                continue
            out: dict = {}
            if url:
                out = {"type": "sse" if str(s.get("transport") or "") == "sse" else "http", "url": url}
                headers = self._clean(what, s.get("headers"))
                if headers:
                    out["headers"] = headers
            elif s.get("command"):
                out = {"command": str(s["command"]), "args": [str(a) for a in s.get("args") or []]}
                env = self._clean(what, s.get("env"))
                if env:
                    out["env"] = env
                if s.get("cwd"):
                    out["cwd"] = str(s["cwd"])
            else:
                continue
            if s.get("enabled") is False:
                out["disabled"] = True
            tf = s.get("toolFilter") if isinstance(s.get("toolFilter"), dict) else {}
            if tf.get("exclude"):
                out["tools"] = {str(t): "off" for t in tf["exclude"]}
            if tf.get("include"):
                self.p.ask(what, L("toolFilter.include 没转：OpenMousse 按工具写 auto / ask / off，用到时再关掉不要的",
                                   "toolFilter.include wasn't converted: OpenMousse sets auto / ask / off per tool; turn off the ones you don't want"))
            put(self.p.patch, "mcp_servers", str(name), out)
            self.p.ok(what, L(f"→ mcp_servers.{name}（只给主对话，同 OpenMousse 的默认）", f"→ mcp_servers.{name} (main chat only, OpenMousse's default)"))

    def _ours(self, url: str, s: dict) -> bool:
        from urllib.parse import urlparse
        u = urlparse(url)
        if not u.path.startswith("/mcp"):
            return False
        own = set(config.settings.tokens().values())
        auth = str((s.get("headers") or {}).get("Authorization") or "") if isinstance(s.get("headers"), dict) else ""
        local = (u.hostname or "") in ("127.0.0.1", "localhost", "::1", config.settings.host)
        return (local and (u.port or 80) == config.settings.port) or any(t and t in auth for t in own)

    def _clean(self, what: str, d) -> dict:
        """MCP 的 env、headers：${变量} 照写（MCP 那边只认这种）；直接写的、名字像密钥的去掉，写一句。"""
        out: dict = {}
        dropped: list[str] = []
        for k, v in d.items() if isinstance(d, dict) else []:
            if env_ref(v):
                out[str(k)] = f"${{{env_ref(v)}}}"
                if env_ref(v) in self.s.env:
                    self.p.secrets[env_ref(v)] = self.s.env_path  # type: ignore[index]
            elif SENSITIVE.search(str(k)):
                dropped.append(str(k))
            else:
                out[str(k)] = str(v)
        if dropped:
            self.p.ask(what, L(f"{', '.join(dropped)} 是直接写的密钥：没转（MCP 的密钥只能写成 ${{变量}}）",
                               f"{', '.join(dropped)} {'holds a key' if len(dropped) == 1 else 'hold keys'} written in plain: not converted (MCP keys must "
                               "be written as ${VARIABLE})"))
        return out

    def misc(self) -> None:
        at = get(self.cfg, "session", "reset", "atHour")
        if isinstance(at, int) and at != 4:
            self.p.ask("session.reset.atHour", L(f"OpenClaw 的对话每天 {at} 点重开；OpenMousse 的一天从 4 点起（日结 3:45），这个不能改",
                                                 f"OpenClaw restarted chats at {at}:00; OpenMousse's day starts at 4:00 (digest at 3:45) and that can't change"))
        for cid, c in (self.cfg.get("channels") or {}).items() if isinstance(self.cfg.get("channels"), dict) else []:
            if isinstance(c, dict) and c.get("enabled") is not False:
                self.p.no(f"channels.{cid}", L("OpenMousse 不接 OpenClaw 的聊天渠道：没转", "OpenMousse doesn't take over OpenClaw's chat channels: not converted"))
        if get(self.cfg, "tools", "media"):
            self.p.no("tools.media", L("看图、听音频、看视频的设置：不转（内核自己按模型能不能看图来）", "Image / audio / video settings: not converted (the "
                                       "kernel goes by what each model can take)"))
        for hid in (get(self.cfg, "hooks", "internal", "entries") or {}):
            self.p.no(f"hooks.internal.entries.{hid}", L("OpenClaw 的钩子：不转", "OpenClaw hook: not converted"))

    def env_file(self) -> None:
        """密钥的值留在 OpenClaw 的 .env：server.json 的 env_file 指过去（现在读的就是它就不写）。"""
        if not any(p == self.s.env_path for p in self.p.secrets.values()):
            return
        import secretstore
        if same_dir(secretstore.env_file(), self.s.env_path):
            return
        cur = config.raw().get("env_file")
        if cur:
            self.p.ask("env_file", L(f"server.json 的 env_file 已经指着 {tilde(str(cur))}：OpenClaw 的密钥在 {tilde(self.s.env_path)}；加 --copy-secrets 抄进 "
                                     "secrets.env，或者自己改", f"server.json's env_file already points at {tilde(str(cur))}: OpenClaw's keys are in "
                                     f"{tilde(self.s.env_path)}; add --copy-secrets to copy them into secrets.env, or change it yourself"))
            return
        put(self.p.patch, "env_file", str(self.s.env_path))
        self.p.ok(".env", L(f"→ env_file = {tilde(self.s.env_path)}（密钥的值留在那里，OpenMousse 去那里读）",
                            f"→ env_file = {tilde(self.s.env_path)} (the values stay there; OpenMousse reads them from it)"))


def plan(home: Path, detect=None) -> Plan:
    """读一个 OpenClaw 的家 → Plan（什么都不写）。detect：查 Claude Code 登录的函数（默认 engine/runners.detect；测试换掉）。"""
    from engine import runners
    src = Source(home)
    p = Plan("openclaw", home)
    Planner(src, p, detect or runners.detect).run()
    return p
