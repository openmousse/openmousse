# packs/core

**中文** · [English](README.md)

装好就有的那一层：主对话和 Agent 之间怎么协作、日志、世界树、日结。安装器（仓库根目录 `install.sh` → `packs/core/setup.py`）把它接进你的 OpenClaw。

| 目录 | 是什么 |
|---|---|
| `skills/handoff` | 主对话把属于某个 Agent 的事转给它（`scripts/ask_agent.py` → 服务的 `/api/chat/relay`） |
| `skills/agent-builder` | 在对话里新建 / 删除 Agent（`server/agent_ctl.py`） |
| `skills/journal` | 用户随口说的感受、想法、决定记进日志（`scripts/journal.py` → 服务数据库 `journal` 表；app「我 → 日志」能翻） |
| `skills/memory-tree` | 学到关于用户的新东西写进世界树（`mousse-tree add`），回答前先查 |
| `scripts/daily_close.py` | 03:45 给当天有过对话的线程发「【自动触发】日结」，04:00 会话重置前把结论写进记忆 |
| `scripts/mousse_common.py` | 上面几个脚本共用：从 `~/.openmousse/server.json` 读服务地址、`local` 令牌、数据库、时区 |
| `systemd/` | `openmousse-server`、`openmousse-daily-close.timer` 的模板 |
| `setup.py` | 安装器第二段（第一段是 `install.sh`）：写 server.json、软链 skills、改 openclaw.json（备份 + validate）、装服务 |

skills 里的命令都走固定路径 `~/.openmousse/repo/…`（安装器建的软链，指向仓库）和 `~/.openmousse/venv/bin/…`，所以 skills 目录可以直接软链进工作区，`git pull` 后立即生效。

安装器对 `openclaw.json` 做的改动（都先备份到 `~/.openmousse/backups/`，改完 `openclaw config validate`，不过就恢复）：

- `agents.defaults.skills` 是列表时追加这四个 skill；没有这个键（= 不限制）就不动
- `gateway.http.endpoints.chatCompletions.enabled = true`：app 的对话走 Gateway 的 OpenAI 兼容接口（仍只在本机）
- `session.reset = {daily, 04:00}`：对话按天，之前的在历史页
- `tools.deny` 加 `ask_user`：app 的通道没人能回答工具里的提问，会把会话卡死
- `memory.search.extraPaths` 加 `<openclaw>/shared/digest`：主对话能查各 Agent 的日结

主 agent 的 `AGENTS.md` 末尾追加一节 `## OpenMousse`（日结、自动触发、Agent 协作、不用 ask_user、日志与世界树）。删掉这一节 app 仍能对话，只是 Agent 之间不协作了。

语言：安装器会问用 `zh` 还是 `en`（默认看 `LANG`；也可以设 `MOUSSE_LANG`，或 `setup.py --lang`），写进 `server.json` 的 `language`。安装器的输出、`## OpenMousse` 规则、世界树、脚本的提示（包括日结的触发文字）都按它；`【自动触发】` 这个标记本身永远不变。已有的 `## OpenMousse` 一节再跑安装器也不会重写。
