"""服务端配置：一个 JSON 文件描述这台机器上的一切位置和认证方式，代码里不再写死任何路径。

文件位置：环境变量 MOUSSE_SERVER_CONFIG，否则 ~/.openmousse/server.json。没有文件时用默认值（单机、loopback、无令牌 = 拒绝一切 /api）。

字段（都可省略）：
  app_name          界面上助手的名字（默认 OpenMousse）
  timezone          IANA 时区，逻辑日和时间显示都按它算
  language          "zh" 或 "en"：没带 Accept-Language 的请求、推送、定时器用的语言（默认 en；app 的请求按它自己的语言）
  bind              {"host", "port"}：服务监听地址。loopback 给反向代理 / Tailscale Serve；Tailscale 私网地址只给自己的设备
  openclaw_home     OpenClaw 的家（默认 ~/.openclaw）
  workspace         主 agent 的 workspace（默认 <openclaw_home>/workspace）
  agent_workspaces  {agent_id: workspace 路径}：有独立 workspace 的 Agent（对话按 id 路由到它，记忆页读它的 MEMORY.md）
  scripts           数据脚本目录（默认 <workspace>/scripts）
  data_dir          服务自己的数据（数据库、上传文件、档案历史；默认 ~/.openmousse/data）
  db / uploads / profile_history   分别覆盖 data_dir 下的三个位置
  profile           基础档案 USER.md（默认 <openclaw_home>/shared/profile/USER.md）
  dist              网页版构建产物目录（默认 仓库根/dist）
  gateway           OpenClaw Gateway 的 HTTP 地址
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


def _p(value: str | None, default: Path) -> Path:
    return Path(value).expanduser() if value else default


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
        self.gateway: str = c.get("gateway") or "http://127.0.0.1:18789"
        self.openclaw_bin: str = c.get("openclaw_bin") or "openclaw"
        self.env_file = _p(c.get("env_file"), self.openclaw_home / ".env")
        self.default_model: str = c.get("default_model") or "anthropic/claude-opus-5-5"
        self.openclaw_json = self.openclaw_home / "openclaw.json"
        self.backup_dir = _p(c.get("backup_dir"), self.data_dir / "backups")
        self.agent_default_skills: list[str] = [str(x) for x in c.get("agent_default_skills") or []]
        self.transcribe_prompt: str = c.get("transcribe_prompt") or ""
        self.transcribe_url: str = c.get("transcribe_url") or "https://api.openai.com/v1/audio/transcriptions"

    # —— 会变的部分，每次读文件 ——
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

    def tailscale_nodes(self) -> set[str]:
        return {str(x) for x in self.auth().get("tailscale_nodes") or []}

    def trust_loopback(self) -> bool:
        return bool(self.auth().get("trust_loopback"))

    def nutrition_targets(self) -> dict | None:
        return raw().get("nutrition_targets") or None


settings = Settings()
TZ = settings.tz
