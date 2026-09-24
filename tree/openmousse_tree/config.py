"""配置：~/.mousse-tree/config.json（600 权限，含各平台令牌，不要进 git）。

默认路径按 OpenClaw 单 agent 安装猜：档案 ~/.openclaw/workspace/USER.md，导出 ~/.openclaw/shared/tree/TREE.md。
多 agent 或自定义布局在 config.json 里改 profile_path / export_path。
"""
from __future__ import annotations

import json
import os
import secrets
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

HOME = Path(os.environ.get("MOUSSE_TREE_HOME", Path.home() / ".mousse-tree"))
CONFIG = HOME / "config.json"
DB = HOME / "tree.db"
PLATFORMS = ("claude", "chatgpt", "gemini", "notion", "claude-code")

DEFAULTS = {
    "port": 8787,
    "timezone": "",                      # 空 = 跟系统；写 "Europe/London" 之类
    "language": "en",                    # "zh" | "en"：给模型的说明、工具描述和回话、命令行输出用哪种语言（改了重启服务生效）
    "profile_path": str(Path.home() / ".openclaw/workspace/USER.md"),
    "export_path": str(Path.home() / ".openclaw/shared/tree/TREE.md"),
    "public_hosts": [],                  # 公网域名，如 "xxx.tail1234.ts.net"；MCP SDK 的 DNS rebinding 保护只放行这些 Host
    "require_confirm": False,            # True = 平台写入先 pending，在管理页确认后才 active
    "owner_name": "",                    # 你的称呼，写进给模型看的说明里
    "tokens": {},                        # token -> 平台名
    "ui_token": "",                      # 管理页 /ui 的接口令牌；空的话 init / serve 时自动生成，链接见 mousse-tree urls
}


def load() -> dict:
    cfg = dict(DEFAULTS)
    try:
        saved = json.loads(CONFIG.read_text(encoding="utf8"))
    except (OSError, ValueError):
        saved = None
    if isinstance(saved, dict):
        cfg.update(saved)
        # language 是后来才加的键。配置文件已经存在却没有它 = 加这个选项之前装的，那时说明、工具描述、命令行全是中文：
        # 按 zh 算，老用户升级后不会突然变成英文。只有还没有配置文件（全新安装）才落到 DEFAULTS 的 en；
        # init 会按 --lang 或环境变量写进去。save() 存的是整份 cfg，所以第一次保存就把这里的推断固定进文件。
        if "language" not in saved:
            cfg["language"] = "zh"
    return cfg


def save(cfg: dict) -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
    CONFIG.chmod(0o600)


def ensure_tokens(cfg: dict) -> dict:
    tokens: dict[str, str] = cfg.setdefault("tokens", {})
    have = set(tokens.values())
    for p in PLATFORMS:
        if p not in have:
            tokens[secrets.token_urlsafe(24)] = p
    ensure_ui_token(cfg)
    return cfg


def ensure_ui_token(cfg: dict) -> bool:
    """管理页令牌不存在就生成一个；返回是否新生成（调用方负责 save）。"""
    if cfg.get("ui_token"):
        return False
    cfg["ui_token"] = secrets.token_urlsafe(24)
    return True


def lang(cfg: dict | None = None) -> str:
    """配置的语言："zh" 或 "en"。"""
    return "zh" if str((cfg or load()).get("language") or "").lower().startswith("zh") else "en"


def L(zh: str, en: str) -> str:
    """双语文字：按 config.json 的 language 挑一种。每次调用时读配置，别在导入时（模块常量里）调用。"""
    return zh if lang() == "zh" else en


def env_lang() -> str:
    """环境变量的语言（LC_ALL 优先，其次 LANG）：zh 开头 → zh，其它 → en。init 没给 --lang 时用。"""
    loc = os.environ.get("LC_ALL") or os.environ.get("LANG") or ""
    return "zh" if loc.lower().startswith("zh") else "en"


def tz(cfg: dict | None = None) -> ZoneInfo | None:
    name = (cfg or load()).get("timezone") or ""
    if name:
        try:
            return ZoneInfo(name)
        except Exception:  # noqa: BLE001
            return None
    return None


def now(cfg: dict | None = None) -> datetime:
    z = tz(cfg)
    return datetime.now(z) if z else datetime.now().astimezone()
