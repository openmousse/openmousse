"""服务端配置：一个 JSON 文件描述这台机器上的一切位置和认证方式，代码里不再写死任何路径。

文件位置：环境变量 MOUSSE_SERVER_CONFIG，否则 ~/.openmousse/server.json。没有文件时用默认值（单机、loopback、无令牌 = 拒绝一切 /api）。

字段（都可省略）：
  app_name          界面上助手的名字（默认 OpenMousse）
  user_name         你的称呼：给模型的中文说明里用它指你（默认「用户」；settings_ctl.py user-name 改，每次读文件，不用重启）
  timezone          IANA 时区，逻辑日和时间显示都按它算
  language          "zh" 或 "en"：没带 Accept-Language 的请求、推送、定时器用的语言（默认 en；app 的请求按它自己的语言）
  bind              {"host", "port"}：服务监听地址。loopback 给反向代理 / Tailscale Serve；Tailscale 私网地址只给自己的设备
  claw              你的 claw：不写 = OpenClaw（下面 openclaw_home / gateway / openclaw_bin 那几项）；别的 claw 或 agent 写
                    {"kind": "openai", "name", "url": ".../v1", "token" 或 "token_env", "model", "models", "session", "headers"}，见 claw.py（每次读文件）
  openclaw_home     OpenClaw 的家（默认 ~/.openclaw）
  workspace         主 agent 的 workspace（默认 <openclaw_home>/workspace）
  agent_workspaces  {agent_id: workspace 路径}：有独立 workspace 的 Agent（对话按 id 路由到它，记忆页读它的 MEMORY.md）
  scripts           数据脚本目录（默认 <workspace>/scripts）
  data_dir          服务自己的数据（数据库、上传文件、档案历史；默认 ~/.openmousse/data）
  db / uploads / profile_history   分别覆盖 data_dir 下的三个位置
  profile           基础档案 USER.md（默认 <openclaw_home>/shared/profile/USER.md）
  dist              网页版构建产物目录（默认 仓库根/dist）
  gateway           OpenClaw Gateway 的 HTTP 地址（默认 http://127.0.0.1:<openclaw.json 的 gateway.port，没写就 18789>）
  openclaw_bin      openclaw 命令的路径（PATH 里找不到时用）
  env_file          读 API 密钥的 .env（默认 <openclaw_home>/.env）
  default_model     新线程默认模型
  auth.tokens       {名字: 令牌}：app 带 Authorization: Bearer <令牌>（或 X-API-Key、?token=）即通过
  auth.tailscale_nodes  Tailscale 设备名白名单：来源设备在里面就免令牌（需要本机装了 tailscale）
  auth.trust_loopback   true 时 127.0.0.1 来的请求免令牌。反向代理在本机时千万别开
  nutrition_targets {kcal, protein, carb, fat} 手动营养目标
  agent_default_skills  新建 Agent 默认的 skills 允许列表（空 = 不限制，继承 defaults）
  transcribe_prompt 语音转写的词表提示（你常说的专有名词）
  transcribe_url    OpenAI 兼容的转写接口（默认 OpenAI 官方；自建 Whisper 服务或代理填它的 /audio/transcriptions）
  backup_dir        改 openclaw.json 前的备份目录（默认 <data_dir>/backups）
  push              推送：{"quiet_hours": ["23:00", "07:30"]}：静默时段（HH:MM，按 timezone，可以跨午夜），这段时间里
                    「响铃」的推送自动降成「静默」（照样进通知中心，只是不出声不亮屏）；null 或 [] = 不设。每次读文件，不用重启（见 push.py）
  apns              实时活动直推苹果（app 1.0.5 起，见 live.py）：{"key_file": "APNs 的 .p8", "key_id": "…", "team_id": "…", "topic": "app 的 bundle id",
                    "sandbox": false}。不配也行：app 打开时自己开实时活动，只是 app 没开时开不了、改不了。每次读文件，不用重启
  tasks             后台任务：{"daily_limit": 10, "max_minutes": 30, "notify_done": true}：每天几个、单个最长几分钟（给 Agent 看的额度，
                    tasks_ctl.py quota）、做完了要不要静默推一条（见 cards.py；每次读文件，不用重启）
  study             学习台：{materials, pages, courses, deadlines_cmd, video_cmd, readings, recordings}（见 study.py；每次读文件，不用重启）
  chat              对话：{"transport": "http" | "ws"}。http（默认）走 Gateway 的 OpenAI 兼容接口；ws 走 Gateway 的 WebSocket 对话通道
                    （gateway_ws.py：回复进行中能插话、停止用 chat.abort、服务重启不掐断回复），设备身份存 <data_dir>/gateway-device.json（每次读文件）
  share             分享（见 share.py）：{"public_url": "https://<机器>.<tailnet>.ts.net", "public_port": 8089, "private_words": ["…"],
                    "font": "…", "font_bold": "…"}。public_port 配了，run.py 就在 127.0.0.1 上另起一个只有 /s/ 的小服务（public.py），
                    给 Funnel / 反向代理指过去；public_url 是外面看到的地址，没配就只能发干净版卡片。private_words：还要挡的词。
                    每次读文件，不用重启（public_port 改了要重启）
  social            朋友（社交第二层，见 social.py、friends.py、docs/social-protocol.zh-CN.md）：{"push": {"message": "ring", "answered": "quiet",
                    "friend": "quiet"}（不写 = 朋友的事不推）, "allow_http": false（只给同一台机器上的测试服）}。要先有 share.public_url 和
                    share.public_port（朋友经小服务的 /f 找到你）。每次读文件，不用重启
  podcast           播客（见 podcast.py，全部可选）：{"dir": 原声放哪（默认 <data_dir>/podcast）, "text_model": "gpt-transcribe",
                    "time_model": "whisper-1"（"" = 不要逐句时间）, "thinking": "low", "model": llm-task 的模型覆盖}。每次读文件，不用重启
  mcp               MCP 入口（/mcp，见 mcp_bridge.py）：{"scripts": {"工具名": ["命令", "参数"…] 或 null}}：换掉 / 加 / 关掉一个工具背后的命令
                    （令牌是 auth.tokens 里的 mcp、mcp-<agent id>，这几把只能用在 /mcp 上，/api 不认；改了工具要重启）
  apps              连接器（见 apps.py）：{"catalog": 加 / 改 / 藏目录里的应用, "client_id_url": CIMD 用的 client_id 网址,
                    "redirect_uris": [网页版用的 http(s) 回调], "allow_local": 只给测试}。每次读文件，不用重启
  wake              起床信号：{"notify_cmd": [...], "notify_hours": ["05:30", "13:00"]}：早上收到信号（快捷指令、app 回到前台、「我起来了」）
                    时跑一下这个命令，比如立刻跑一次出起床报告的定时脚本（见 health.py；每次读文件，不用重启）

agent_workspaces 由「新建 Agent」自动维护（agents.py），每次读文件，不用重启。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

CONFIG_PATH = Path(os.environ.get("MOUSSE_SERVER_CONFIG") or "~/.openmousse/server.json").expanduser()
REPO = Path(__file__).resolve().parent.parent
_cache: tuple[float, dict] | None = None
NOT_API = ("mcp", "sentinel")  # 这些令牌（还有 mcp-<agent id>）在 /api 上不通：只给 /mcp、/api/egress（见 main.py 的 guard）


def raw(fresh: bool = False) -> dict:
    """配置文件的内容（按 mtime 缓存，改了令牌不用重启）。"""
    global _cache
    try:
        mtime = CONFIG_PATH.stat().st_mtime
    except OSError:
        return {}
    if not fresh and _cache and _cache[0] == mtime:
        return _cache[1]
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf8"))
    except (OSError, ValueError):
        data = {}
    _cache = (mtime, data)
    return data


def save(data: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
    os.chmod(tmp, 0o600)
    tmp.replace(CONFIG_PATH)


def pick_api_token(tokens: dict) -> str | None:
    """本机脚本调 /api 用哪把令牌：第一把不是 mcp、mcp-<agent id>、sentinel 的（不管 server.json 里的顺序）。没有就 None。"""
    for name, tok in tokens.items():
        name = str(name)
        if tok and name not in NOT_API and not name.startswith("mcp-"):
            return str(tok)
    return None


def _p(value: str | None, default: Path) -> Path:
    return Path(value).expanduser() if value else default


def gateway_default(openclaw_json: Path) -> str:
    """server.json 没写 gateway：本机 + openclaw.json 里 Gateway 的端口（onboard 时换过端口也连得上）。读不到按 OpenClaw 默认的 18789。"""
    try:
        port = int((json.loads(openclaw_json.read_text(encoding="utf8")).get("gateway") or {}).get("port") or 18789)
    except (OSError, ValueError, TypeError, AttributeError):
        port = 18789
    return f"http://127.0.0.1:{port}"


class Settings:
    """启动时算好的路径与常量。令牌之类会变的东西每次从 raw() 读。"""

    def __init__(self) -> None:
        c = raw(fresh=True)
        self.app_name: str = c.get("app_name") or "OpenMousse"
        self.timezone: str = c.get("timezone") or "UTC"
        self.tz = ZoneInfo(self.timezone)
        self.language: str = "zh" if str(c.get("language") or "en").lower().startswith("zh") else "en"
        bind = c.get("bind") or {}
        self.host: str = bind.get("host") or "127.0.0.1"
        self.port: int = int(bind.get("port") or 8080)
        self.openclaw_home = _p(c.get("openclaw_home"), Path("~/.openclaw").expanduser())
        self.workspace = _p(c.get("workspace"), self.openclaw_home / "workspace")
        self.scripts = _p(c.get("scripts"), self.workspace / "scripts")
        self.data_dir = _p(c.get("data_dir"), Path("~/.openmousse/data").expanduser())
        self.db = _p(c.get("db"), self.data_dir / "mousse.db")
        self.uploads = _p(c.get("uploads"), self.data_dir / "uploads")
        self.profile_history = _p(c.get("profile_history"), self.data_dir / "profile-history.md")
        self.profile = _p(c.get("profile"), self.openclaw_home / "shared/profile/USER.md")
        self.dist = _p(c.get("dist"), REPO / "app" / "dist")
        self.openclaw_json = self.openclaw_home / "openclaw.json"
        self.gateway: str = c.get("gateway") or gateway_default(self.openclaw_json)
        self.openclaw_bin: str = c.get("openclaw_bin") or "openclaw"
        self.env_file = _p(c.get("env_file"), self.openclaw_home / ".env")
        self.default_model: str = c.get("default_model") or "anthropic/claude-opus-5-5"
        self.backup_dir = _p(c.get("backup_dir"), self.data_dir / "backups")
        self.agent_default_skills: list[str] = [str(x) for x in c.get("agent_default_skills") or []]
        self.transcribe_prompt: str = c.get("transcribe_prompt") or ""
        self.transcribe_url: str = c.get("transcribe_url") or "https://api.openai.com/v1/audio/transcriptions"

    # —— 会变的部分，每次读文件 ——
    @property
    def user_name(self) -> str:
        """你的称呼（settings_ctl.py user-name 写；新手带路时主对话问了就写进来）。空 = 没设。"""
        return str(raw().get("user_name") or "").strip()

    @property
    def agent_workspaces(self) -> dict[str, Path]:
        """有独立 workspace 的 Agent（新建 / 删除 Agent 时由 agents.py 改写 server.json）。"""
        return {k: Path(v).expanduser() for k, v in (raw().get("agent_workspaces") or {}).items()}

    @property
    def memory_files(self) -> dict[str, Path]:
        return {"main": self.workspace / "MEMORY.md", **{k: v / "MEMORY.md" for k, v in self.agent_workspaces.items()}}

    @property
    def group_agents(self) -> set[str]:
        return set(self.agent_workspaces)

    def set_agent_workspace(self, agent_id: str, path: Path | None) -> None:
        data = dict(raw(fresh=True))
        aw = dict(data.get("agent_workspaces") or {})
        if path is None:
            aw.pop(agent_id, None)
        else:
            aw[agent_id] = str(path)
        data["agent_workspaces"] = aw
        save(data)

    def auth(self) -> dict:
        return raw().get("auth") or {}

    def tokens(self) -> dict[str, str]:
        return {str(k): str(v) for k, v in (self.auth().get("tokens") or {}).items()}

    def api_token(self) -> str | None:
        """本机脚本（*_ctl.py）调 /api 带的令牌，见 pick_api_token。"""
        return pick_api_token(self.tokens())

    def tailscale_nodes(self) -> set[str]:
        return {str(x) for x in self.auth().get("tailscale_nodes") or []}

    def trust_loopback(self) -> bool:
        return bool(self.auth().get("trust_loopback"))

    def nutrition_targets(self) -> dict | None:
        return raw().get("nutrition_targets") or None


settings = Settings()
TZ = settings.tz


def user_word() -> str:
    """给模型的中文说明里怎么称呼用户：server.json 的 user_name（英文名两边带空格，排进中文句子里好看），没写就是「用户」。
    每次从 raw() 读（按 mtime 缓存）：settings_ctl.py user-name 改了，下一句就用新的，不用重启。"""
    n = settings.user_name
    if not n:
        return "用户"
    return f" {n} " if n.isascii() else n
