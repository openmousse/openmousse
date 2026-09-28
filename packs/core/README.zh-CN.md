# packs/core

**中文** · [English](README.md)

装好就有的那一层：主对话和 Agent 之间怎么协作、日志、世界树、日结。安装器（仓库根目录 `install.sh` → `packs/core/setup.py`）把它接进你的 OpenClaw。

| 目录 | 是什么 |
|---|---|
| `skills/handoff` | 主对话把属于某个 Agent 的事转给它（`scripts/ask_agent.py` → 服务的 `/api/chat/relay`） |
| `skills/agent-builder` | 在对话里新建 / 删除 Agent（`server/agent_ctl.py`） |
| `skills/journal` | 用户随口说的感受、想法、决定记进日志（`scripts/journal.py` → 服务数据库 `journal` 表；app「我 → 日志」能翻） |
| `skills/memory-tree` | 学到关于用户的新东西写进世界树（`mousse-tree add`），回答前先查 |
| `skills/inbox` | 先问再做：自己的主意、会发给别人或撤不回的事、新定时任务 / 推送、改代码配置，先交到 app 的「等你点头」；用户明确让做、能撤回的直接做（`server/inbox_ctl.py` → 服务的 `/api/inbox`） |
| `skills/dispatch` | 派后台任务：先看今天的额度（`server/tasks_ctl.py quota`），按「目标 / 要交 / 约束」写任务；app 对话里出一张任务卡，显示在做哪一步和结果，「改一下」直接发给同一个子会话 |
| `skills/project` | 项目，有始有终的事（几天到几周、有目标和截止）：用户让开就开，一件事要聊好几天就提议开；在项目里随手更新项目卡，项目的事转进项目，归档时写结论（`server/project_ctl.py` → 服务的 `/api/projects`） |
| `skills/board` | 每个 Agent 自己的表和看板：用户想长期记的东西记进它自己定义的表，用积木（数字、进度、趋势、列表、清单、文字、按钮）摆到看板上，app 按配置画，不用改代码。用户让加的直接加（看板顶上能撤回）；它自己想到的交提案，「等你点头」里带预览（`server/board_ctl.py` → 服务的 `/api/boards`） |
| `skills/goals` | 用户的长期目标（app「目标」页：健康 / 学业 / 职业 / 财务）。用户让加、让改的直接改（目标页顶上能撤销）；它自己觉得该调的交提案，进「等你点头」；用户没说过的目标不编。体重、体脂的当前值自动读（训练软件为主，Apple 健康对照），体脂从不自动算（`server/goals_ctl.py` → 服务的 `/api/goals`） |
| `skills/onboarding` | 新手带路（主对话），一次一步、每一步都能跳过：这是什么、怎么称呼他（`server/settings_ctl.py user-name`：写 server.json 的 `user_name`，档案 USER.md 也记一行，档案就是世界树的主干）、让它认识他（把别的 AI 记得的贴回来，或者问几个问题）、Apple 健康、第一个 Agent（先出方案卡，他点头才建）、他别的 AI 接同一份记忆。新实例上 app 主对话顶上的「从这里开始」开的头 |
| `scripts/daily_close.py` | 03:45 给当天有过对话的线程发「【自动触发】日结」，04:00 会话重置前把结论写进记忆（回完不推送） |
| `scripts/mousse_common.py` | 上面几个脚本共用：从 `~/.openmousse/server.json` 读服务地址、`local` 令牌、数据库、时区 |
| `systemd/` | `openmousse-server`、`openmousse-daily-close.timer` 的模板 |
| `setup.py` | 安装器第二段（第一段是 `install.sh`）：写 server.json、软链 skills、改 openclaw.json（备份 + validate）、装服务 |

skills 里的命令都走固定路径 `~/.openmousse/repo/…`（安装器建的软链，指向仓库）和 `~/.openmousse/venv/bin/…`，所以 skills 目录可以直接软链进工作区，`git pull` 后立即生效。

安装器对 `openclaw.json` 做的改动（都先备份到 `~/.openmousse/backups/`，改完 `openclaw config validate`，不过就恢复）：

- `agents.defaults.skills` 是列表时追加主对话用的 skill（上面除了 board 都是）；没有这个键（= 不限制）就不动
- `gateway.http.endpoints.chatCompletions.enabled = true`：app 的对话走 Gateway 的 OpenAI 兼容接口（仍只在本机）
- `session.reset = {daily, 04:00}`：对话按天，之前的在历史页
- `tools.deny` 加 `ask_user`：app 的通道没人能回答工具里的提问，会把会话卡死
- `memory.search.extraPaths` 加 `<openclaw>/shared/digest`：主对话能查各 Agent 的日结

主 agent 的 `AGENTS.md` 末尾追加一节 `## OpenMousse`（日结、自动触发、Agent 协作、不用 ask_user、日志与世界树、先问再做）。删掉这一节 app 仍能对话，只是 Agent 之间不协作了。

语言：安装器会问用 `zh` 还是 `en`（默认看 `LANG`；也可以设 `MOUSSE_LANG`，或 `setup.py --lang`），写进 `server.json` 的 `language`。安装器的输出、`## OpenMousse` 规则、世界树、脚本的提示（包括日结的触发文字）都按它；`【自动触发】` 这个标记本身永远不变。已有的 `## OpenMousse` 一节再跑安装器也不会重写。

两个可以跳过的问题：服务器上已经在同步的 Obsidian 库（`MOUSSE_VAULT`；`setup.py --vault`）放思考空间（`server.json` 的 `think.vault`，没设过才写）和世界树的笔记（库里的「世界树」文件夹，世界树还是 SQLite 存储时才换）；让 AI 平台连世界树（`MOUSSE_TREE_PUBLIC=y`；`setup.py --tree-public`）用 Tailscale Funnel 开放世界树的 `/t` 和 `/m`，并把这台机器的名字加进它的 Host 白名单。
