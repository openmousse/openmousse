"""常见 claw 的现成配置（server.json 的 claw 段，见 claw.py）。只用标准库：安装脚本在装依赖之前也能用它填默认值。

按各家 2026-09 的文档和代码核对过（地址都是默认端口；改过端口的在安装时改地址就行）：
  hermes    Hermes Agent（Nous Research）：`hermes gateway` 打开 API server（~/.hermes/.env 里 API_SERVER_ENABLED=true、API_SERVER_KEY），
            带 X-Hermes-Session-Id 时它自己从库里读这个会话的历史，只发新的一句
  nanobot   nanobot（HKUDS）：`nanobot plugins enable api && nanobot serve`；会话放请求体 session_id，每次只能有一条用户消息，
            model 不发（或者和它配置的一样）；不在本机听时要 api.apiKey
  letta     Letta Code：`letta server --listen ws://127.0.0.1:4500 --openai-api`；x-letta-chat-key 定会话，model 写 agent 的名字或 id
别的有 OpenAI 兼容对话接口的（比如直接接一个模型的 API）用通用配置：每次带上今天的记录（session.mode = history）。

  python3 claw_presets.py <名字> <字段>   打印一个预设的某个字段（安装脚本用）；没有这个预设 / 字段就什么都不打印
"""
from __future__ import annotations

import json
import sys

PRESETS: dict[str, dict] = {
    "hermes": {"name": "Hermes", "url": "http://127.0.0.1:8642/v1", "model": "hermes-agent",
               "session": {"mode": "header", "header": "X-Hermes-Session-Id"},
               "token_env": "API_SERVER_KEY", "env_file": "~/.hermes/.env", "skills": "~/.hermes/skills"},
    "nanobot": {"name": "nanobot", "url": "http://127.0.0.1:8900/v1", "model": "",
                "session": {"mode": "body", "field": "session_id"}, "token_env": "NANOBOT_API_KEY"},
    "letta": {"name": "Letta", "url": "http://127.0.0.1:4500/v1",
              "session": {"mode": "header", "header": "x-letta-chat-key"}},
}


def preset(name: str) -> dict:
    return json.loads(json.dumps(PRESETS.get(str(name or "").strip().lower(), {})))  # 拷一份，调用方随便改


if __name__ == "__main__":
    if len(sys.argv) == 3:
        v = preset(sys.argv[1]).get(sys.argv[2])
        if v is not None:
            print(v if isinstance(v, str) else json.dumps(v, ensure_ascii=False))
