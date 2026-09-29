# OpenMousse server

**中文** · [English](README.md)

薄 API 层（FastAPI）：认证、对话转发到你的 claw（OpenClaw 的 Gateway，或任何 OpenAI 兼容接口，见[别的 claw](#别的-claw)）、Agent 的创建与删除、看板数据、附件与语音、推送，同时托管网页版。

## 装

一条命令（仓库根目录的 `install.sh`）会做完下面全部，包括 systemd 服务。手动的话：

```bash
cd ~/openmousse/server
pip install -r requirements.txt
mkdir -p ~/.openmousse && cp server.example.json ~/.openmousse/server.json   # 改成你的路径和时区
python3 tokens.py add 手机       # 生成 app 的接入令牌，填进 app 的连接页
python3 run.py                   # 或按 openmousse-server.service.example 装成 systemd 服务
```

`server.json` 每个字段的含义在 [`config.py`](config.py) 顶部。改令牌、加 Agent 不用重启；改监听地址要重启。`python3 settings_ctl.py user-name <称呼>` 改给模型的说明里怎么称呼你（`user_name`，不用重启），档案 USER.md 也记一行；新手带路时主对话会调它。

## 认证

`/api/*` 要 `Authorization: Bearer <令牌>`（也认 `X-API-Key`；`?token=` 只用于 GET 文件：`/api/files/…`、思考里的附件和收藏的原件、播客的原声，给带不了请求头的图片和网页版的 `<audio>` 用）。没凭证返回 401。两个免令牌的口子都默认关：`auth.tailscale_nodes`（Tailscale 设备名白名单，本机要装 tailscale）和 `auth.trust_loopback`（反向代理在本机时不能开）。网页版的静态文件公开。

## 让手机连上

- **Tailscale**（最省事）：服务绑 Tailscale 地址，手机装 Tailscale，app 里填 `http://100.x.x.x:8080`。
- **配对码**（不用抄令牌）：`python3 tokens.py pair` 出一个一次性配对码（[`pairing.py`](pairing.py)：10 分钟、只能用一次、只存哈希、猜错多了锁 10 分钟），终端里画二维码，旁边是 `openmousse://pair?s=<地址>&c=<码>` 链接。手机相机扫码或点链接 → app 的连接页填好地址和码 → 点「用配对码连接」→ `POST /api/pair` 换一把新令牌（`device-<设备>-<时间>`，能 `tokens.py remove`）。`--json` 给 claw 读（见 [`docs/connect.zh-CN.md`](../docs/connect.zh-CN.md)）。`/api/pair` 在 `/api` 下，只在 Tailscale 私网里。安装器装完会直接打一个（30 分钟）。
- **设备**（app：设置 → 你的 claw）：`GET /api/devices` 列出人用的令牌（只回名字；`mcp`、`mcp-<id>`、`sentinel` 这类给程序用的不列），`DELETE /api/devices/{name}` 收回一台（不能是发请求的这台），`POST /api/pair/new` `{server, name}` 让已经连着的 app 给另一台设备出配对码（链接 + 二维码的 SVG path，规矩和 `tokens.py pair` 一样）。
- **公网 HTTPS**：`tailscale serve` / `tailscale funnel`，或 Caddy / nginx 反向代理到 127.0.0.1:8080，app 里填 `https://你的域名`。

## 别的 claw

服务器默认接 OpenClaw。别的 claw 或 agent 只要有 OpenAI 兼容的对话接口，就在 `server.json` 里加一段 `claw`（安装器问「你的 claw」时填它的地址，就会写这一段）：

```json
"claw": {"kind": "openai", "name": "我的 claw", "url": "http://127.0.0.1:8642/v1", "token": "…", "model": "default"}
```

- `url` 写到 `/v1`：服务器往 `<url>/chat/completions` 发 `stream: true` 的请求（直接回整段 JSON 的也认），连接页用 `<url>/models` 看连不连得上。`token`（或者 `token_env`：环境变量名，先看进程环境再看 `env_file`）作为 Bearer 令牌带上。`models` 是 app 里能切换的模型；`headers` 是它要的额外请求头。
- `session` 定一个对话怎么接上一句：`{"mode": "history", "turns": 40}`（默认）每次把这个对话今天的记录一起发过去，给自己没有会话的接口用（比如直接接一个模型的 API），撤回就是这边删掉、下一轮不带。自己记会话的 claw 每次只收新的一句和一个会话键：`{"mode": "header", "header": "X-Session-Id"}`、`{"mode": "body", "field": "session_id"}` 或 `{"mode": "user"}`。会话键是 `mousse:<对话>:<逻辑日>`，每天 04:00 换新的（多数 claw 自己不按天重置，前一天靠日结接上）；`"daily": false` 就一直用 `mousse:<对话>`。`"model": ""` = 请求里不带 model。
- 现成配置（[`claw_presets.py`](claw_presets.py)），安装时答名字就填好：**Hermes Agent**（`hermes`：端口 8642、`X-Hermes-Session-Id`、令牌从 `~/.hermes/.env` 的 `API_SERVER_KEY` 读、skills 在 `~/.hermes/skills`）、**nanobot**（`nanobot`：端口 8900、会话在请求体 `session_id`、不带 model）、**Letta Code**（`letta`：端口 4500、`x-letta-chat-key`、model 写 agent 的名字）。
- 还不支持：没有 OpenAI 兼容对话接口的（ZeroClaw 的 webhook、Moltis 的 RPC、NullClaw 的 A2A、PicoClaw 的 WebSocket、Agent Zero、NanoClaw、TinyAGI），还有 IronClaw 的 Responses 接口，都要各写一个驱动。
- Agents：每个 Agent 是同一个 claw 的一段单独的对话，每天第一句话前面带上它的名字、职责和上一次的日结。
- 日结：03:45 `daily_close.py` 让今天说过话的每个对话用 5–10 行总结今天，服务器把回复存成 `<data_dir>/digest/<对话>/<日期>.md`，第二天第一句话前面带上。
- skills 和规矩：安装时给出它的 skills 文件夹和每轮都读的规则文件（AGENTS.md 之类），OpenMousse 的 skill 软链进去、规矩追加一小节。skill 要跑 `python3 ~/.openmousse/repo/server/…_ctl.py`，所以它得能执行命令；能连 MCP 的 claw 走 `/mcp` 就不用（见下面「MCP」）。
- `/api/health` 报 `claw: {kind, name, caps}`，app 按它藏起做不到的：回复中插话、后台任务、执行审批、OpenClaw 的定时任务、模型计费、每个 Agent 各自的工作区。这些接口回空列表，不去调 `openclaw`。

## MCP：claw 不用 shell 也能用这些功能

服务器在 `/mcp` 开了一个 MCP 入口（Streamable HTTP，无状态，[`mcp_bridge.py`](mcp_bridge.py)），claw 经它用看板、收件箱、目标、项目、日程、Agent、后台任务额度、日结提案、日志、转给 Agent、称呼、世界树。沙箱里的 Agent、换了 docker / ssh 终端后端的 Hermes、云上托管的 claw 跑不了 skill 里的命令，走这里就行。

- 地址：`http://<服务地址>/mcp/<令牌>`（令牌放在路径里），或者 `/mcp` + `Authorization: Bearer <令牌>`。令牌是 `server.json` 的 `auth.tokens` 里名字为 `mcp` 的那个（安装器生成；`python3 tokens.py add mcp` 也行），别的令牌 `/mcp` 不认。名字是 `mcp-<agent id>` 的令牌绑一个 Agent，只能替它做事。
- 工具：每个是一条现成命令的桥，写法和 skills 里的命令一模一样：`args` = 脚本名后面的词（一个词一项，不经 shell，不用加引号），`input` = 本来要从标准输入给的，`agent` = 你是哪个 Agent（命令就在它的工作区里跑，看板、收件箱按它认；别的 claw 的 Agent 没有工作区也认得）。每次起一个子进程（约 0.1 秒），同时最多 4 个，单次最长 120 秒（handoff 300 秒）。
- OpenClaw：安装器在 `openclaw.json` 的 `mcp.servers` 里加 `openmousse`（Gateway 热加载，不用重启），工具名是 `openmousse__board` 这种。skills 里的命令照旧能用，两条路并存，skill 开头写了有工具就用工具。
- 别的 claw：安装完打印地址和它那家怎么加（Hermes 的 `mcp_servers`、nanobot 的 `tools.mcpServers`、Letta Code 的 `/mcp add`）。Agent 那段说明里带着它的 id，调工具时填进 `agent`。
- `server.json` 的 `mcp.scripts` 可以换掉或加一个工具背后的命令（`{"journal": ["python3", "~/…/my_journal.py"]}`，写 `null` = 不提供），改了要重启服务。
- 每轮的上下文：11 个工具的定义约 7,500 字，跟 11 个 skill 的描述差不多。

## Agent

app 里「新建 Agent」= 在你的 OpenClaw 里建一个独立 agent：自己的 workspace（AGENTS.md / IDENTITY.md / MEMORY.md）、skills 允许列表、路由。见 [`agents.py`](agents.py)。命令行：

```bash
python3 agent_ctl.py list
python3 agent_ctl.py create --name 睡眠 --purpose "每天早上解读昨晚睡眠。" --icon moon --color purple
python3 agent_ctl.py update g-xxxxxxxx --name 睡眠与恢复 --color default   # 只改给了的字段
python3 agent_ctl.py delete g-xxxxxxxx      # workspace 归档到 ~/.openclaw/archive/，不删
```

主 agent 装上 `skills/agent-builder`（见根目录 packs，移植中）就能在对话里建。

| 接口 | 做什么 |
|---|---|
| `GET /api/groups` | 所有 Agent：`{id, name, icon, color, purpose, modelId, dashboard, lastLine}`。`color` 没设是 null（app 用默认色） |
| `POST /api/groups` | `{name, purpose?, icon?, color?, model, skills?}` → `{ok, id}`：先建 OpenClaw agent，再写一行；哪一步失败都什么也不留 |
| `PATCH /api/groups/{id}` | `{name?, icon?, color?, purpose?, model?}` → `{ok, group}`（`group` 和 GET 里的一项同样形状）。只改给了、而且真变了的字段；`color: null` = 换回默认色 |
| `DELETE /api/groups/{id}` | 去掉 OpenClaw 条目和路由，workspace 移到 `archive/` |

POST 和 PATCH 校验一样：名字不能空（400），不能和别的 Agent 重名、不分大小写（409）；`icon` 是图标名，小写字母和连字符，最多 24 个字符（app 自带 moon、dumbbell、utensils、book、wallet、briefcase、heart、plane、coffee、music、camera、code、cart、home、car、paw、leaf、gamepad、palette、globe、graduation、lightbulb、trophy、pill）；`color` 只能是 cyan、gold、green、purple、pink、orange（否则 400）。

PATCH 除了数据库还动什么：

- **图标、颜色**：别的都不动。
- **名字或职责**：Agent 的 `IDENTITY.md`，让它知道。只替换 `<!-- mousse:role -->` 和 `<!-- /mousse:role -->` 之间那一段：「职责（在 app 里改的，以这里为准）」，写着名字和职责。标记外面的内容，手写的也好、agent 自己写的也好，逐字节不变。还没有这一段 → 追加在文件末尾（没有文件就新建）；新建的 Agent 一开始就有。改之前把旧文件复制到 `backup_dir`。
- **模型**：`openclaw.json` 里这个 Agent 的默认模型（`agents.entries.<id>.model`，文件别的地方不动），步骤和新建 Agent 一样：备份、写、`openclaw config validate`、不过就恢复。原来就是 `{primary, fallbacks}` 的保留回退链；原来跟着默认模型走的，抄一份默认的回退链，出错照样回退。没有自己条目的 id（比如 main）→ 400。app 里这个 Agent 的线程也换成新模型。
- 先全部校验再写文件。模型写不进去（502）时 `IDENTITY.md` 放回原样，数据库不改。

## 等你点头（收件箱）

Agent 要你同意才能做的事（你自己的主意以外，主要是会发给别人或撤不回的、新的定时任务和推送、改代码配置）和它们自己的提议，都交到收件箱，app「今天」页的「等你点头」里列出来；OpenClaw 的执行审批（`openclaw approvals pending`）也合在里面，id 写成 `exec:<审批 id>`。见 [`inbox.py`](inbox.py)，Agent 按 `packs/core/skills/inbox` 的规则用它。

```bash
python3 inbox_ctl.py add --kind send --source apply --title "给 HR 回邮件确认周三面试" --why "HR 问周三还是周四" --change "发给 hr@…" --dedupe apply:hr:0926
python3 inbox_ctl.py done ib-xxxxxxxx --result "发了"
python3 inbox_ctl.py list [--status recent]
```

| 接口 | 做什么 |
|---|---|
| `GET /api/inbox?status=pending` | 等你点头的（过了 `expiresAt` 的先标成 expired）+ OpenClaw 执行审批，新的在前 |
| `GET /api/inbox?status=recent` | 最近 7 天定下来 / 做完的，最多 50 条 |
| `GET /api/inbox?thread=<线程>` | 这个线程的条目（等你点头的 + 最近 7 天定下来 / 做完的），旧的在前：对话里按时间线显示成卡片，挂在 `messageId` 那条回复下面 |
| `GET /api/inbox/{id}` | 一条 |
| `POST /api/inbox` | Agent 提交 `{kind, title, source?, thread?, why?, changes?, detail?, approveLabel?, level?, dedupe?, expiresAt?}` → `{ok, id}`。同一个 `dedupe` 还在等 → 原地更新（`updated: true`，再推一次）；30 天内被拒过 → 409 `{ok: false, error: "rejected_before", rejectedAt, note}` |
| `POST /api/inbox/{id}` | 你点的：`{action: approve / reject / revise, note?}`。同意 → 在条目的线程里发一句「【收件箱】已同意…」让 Agent 去做（那个线程正在回复就等它回完再发）；`exec:` 条目同意 = allow-once，拒绝 = deny |
| `PATCH /api/inbox/{id}` | Agent 改好重新提交：回到 pending，再推一次 |
| `POST /api/inbox/{id}/result` | Agent 报结果 `{status: done / failed, result}`，静默推一条 |
| `POST /api/inbox/{id}/withdraw` | Agent 撤回还没定下来的 |

「改一下」：在 Agent 的对话里引用这张卡回复，`/api/chat/send` 带 `inboxId`：条目变成 revising，你的话记成 note，模型另外看到一句「这是在回复哪一条、改好怎么交」（对话记录里只有你的话）。

「跟进」：已经定下来的条目（「已处理」→ 点一条 → 跟进）带同样的 `inboxId`：记下 `followedAt` / `followNote`；做完 / 没做成的改回 approved（在做），模型另外看到这一条、现在的状态和结果、怎么再用 `done` / `fail` 报。没要 / 撤回 / 过期的状态不变，模型被告知要做就重新提一条。条目还带 `day`（提它的逻辑日），app 的「看原对话」按它打开那天的记录、滚到那条消息。

条目：`{id, kind, source, sourceName, thread, title, why, changes: [], detail, approveLabel, fields?, status, note, result, level, createdAt, updatedAt, decidedAt, expiresAt, messageId}`。`messageId`：Agent 在一次回复里交的条目，回复结束时挂到那条回复（`messages.id`）下面；不是在回复里交的是 null。kind：task / write / send / spend / schedule / push / skill / agent / block / code / calendar / other（exec 只来自 OpenClaw，带 `fields`）；status：pending / approved / rejected / revising / done / failed / withdrawn / expired。旧的 `/api/approvals` 接口还在，给老版本 app。

## 对话：排队、停止、引用

- 一个对话同一时间只有一条回复在跑。回复进行中你又发的（`/api/chat/send`）不再 409：先记进库（status `queued`，app 上标「排队」），SSE 先回一个 `queued` 事件，连接等着；这条回复一结束（包括被停掉），排着的几条合成一轮发给模型（编号列出，前面一句说明：分开回答时每段开头单独一行写 `> 「原话」`，app 把它画成引用、点了跳回那条），等着的连接都接到这一轮上。服务重启时库里还排着的：30 分钟以内的接着发，更早的标成没发出去。定时器、转交这类系统触发（`/api/chat/trigger`、`relay`）照旧遇忙 409。
- `POST /api/chat/stop` `{thread}`：停掉正在进行的回复——断开到 Gateway 的连接，Gateway 就中止这一轮；已经说了的留着，末尾加「（停了）」，status `stopped`，不推送。排着的接着发。
- 长按「引用」：`/api/chat/send` 带 `replyTo`（`db<id>`）。模型另外看到原话，这条记 `reply_to`，`/api/chat/history` 里带 `replyTo {id, role, text}`，app 在气泡上面显示。
- 走 Gateway 的 WebSocket 对话通道（server.json `chat.transport: "ws"`，见 `gateway_ws.py`）时：回复进行中你又发的不排队，改成**插话**——`chat.send` 带 `queueMode: steer`，Gateway 在这一轮的下一步把这句交给模型，还是同一条回复（这条记 status `steered`，app 上标「插话」）；Gateway 没能插进去、排成了单独一轮的，接管它记成这条的回复。停止用 `chat.abort`。一轮由 Gateway 跑到底：服务重启时还没回完的，启动后接管或从 `chat.history` 补回回复。带图片的消息仍走 HTTP（照样排队）。第一次连会在本机回环地址自动配对，设备身份存 `<data_dir>/gateway-device.json`。
- `GET /api/chat/busy` → `{running, queued, idle}`。要重启服务就用 `python3 safe_restart.py --unit <服务名>`：等没有进行中的回复、没有排着的消息再重启（最多等 10 分钟），重启会掐断进行中的回复。

## 转交卡和任务卡

主对话把问题转给某个 Agent（`scripts/ask_agent.py` → `/api/chat/relay`），或者派一个后台任务（OpenClaw 的 `sessions_spawn`），对话里都会出一张卡。见 [`cards.py`](cards.py)。

- **转交卡**：问题一转出去就出现（「正在问 饮食记录…」，带着转过去的原话），答完变成「转给了 饮食记录 · 34 秒」，挂在发起转交的那条回复下面。点一下打开那个 Agent 的对话，定位到那个问题。转交记在 grava.db 的 `handoffs`；「发起转交的那条回复」= relay 进来时正在回复的那个线程（脚本不知道自己在哪个会话里；几个同时在回复时优先主 agent 的线程，再取最晚开始的）。那个 Agent 正忙（409）也记一张，状态 `busy`。
- **任务卡**：读 OpenClaw 的任务台账（`<openclaw_home>/state/openclaw.sqlite` 的 `task_runs` / `subagent_runs`，只读，一次几毫秒；`tasks.list` 要 2 秒）。回复进行中每 2 秒看一眼这个会话有没有新派的，有就从同一条 SSE 流发过去（`event: card`），卡片当场出现；回复结束时挂到这条回复下面（`task_links`）。进行中显示在做哪一步（「在读 L2.pdf」，读子会话记录，最多 20 秒一次）；做完显示结果开头。「改一下」把意见发给同一个子会话（`POST /api/tasks/{id}/revise`），第几轮、意见、每一轮的结果从 `task:<id>` 线程算。
- **额度**：`server.json` 的 `tasks: {daily_limit: 10, max_minutes: 30, notify_done: true}`。只是给 Agent 看的数（`tasks_ctl.py quota`，用完了退出码 3）；真正到点停掉任务的是 OpenClaw 的 `agents.defaults.subagents.runTimeoutSeconds`。

```bash
python3 tasks_ctl.py quota   # 今天派了几个、上限、还能派几个
python3 tasks_ctl.py list
```

| 接口 | 做什么 |
|---|---|
| `GET /api/chat/cards?thread=&day=` | `{cards, incoming, tasksAvailable}`：这个线程这一天（04:00 起）的转交卡和任务卡，旧的在前，每张带 `messageId`（挂在哪条回复下面；那条回复还没结束是 null）；`incoming` = 别的线程转给它的 |
| `GET /api/tasks/quota` | `{today, running, limit, left, maxMinutes}`；读不到台账时 `today` / `left` 是 null |
| `GET /api/tasks` | 多了 `quota`（外加今天用了多少 `tokens`），每个任务多了 `minutes`、`timedOut`、`step` |

转交卡：`{kind: "handoff", id, thread, messageId, createdAt, status: running / done / error / busy / lost, to, toName, from, fromName, question, seconds, relayId, replyId, error}`。任务卡：`{kind: "task", id, thread, messageId, createdAt, status（进行中 / 完成 / 失败 / 已取消）, timedOut, title, deliverable: [], modelId, minutes, startedAt, finishedAt, tools, step, result, error, round, roundStatus, note, roundResult, tokens, limitMinutes, seq, dailyLimit}`（`seq` = 今天第几个派的）。`deliverable` 取任务正文里「要交：」/「Deliverable:」那一行和紧跟着的列表。

## 日程和「要记得的」

每天一条时间线、一张「要记得的」，都能改。见 [`schedule.py`](schedule.py)。

- **日程** = 课表（可选数据源 `calendar`，只读）+ 服务器自己存的一层（grava.db 的 `schedule_items`：用户自己的安排、各 Agent 排的）+ 当天到期的截止（带勾）。课表本身改不了，但这一层能给一节课标「不去」（`series` = 每周这节都不去）、改地点、加备注。过去的日子记实际发生的：去没去、做没做、实际几点（key 是 `<agent>:training:<日期>` 的训练，实际时间从训练数据源来）。
- **要记得的** = 课程作业（`study.deadlines_cmd`）+ 邮件里抽出来的事（下面的 `remember.mail`）+ 求职和申请的截止（`applications`）+ 用户自己加的截止。一件事只出现一次：到期那天挪进那天的时间线。分组：`security`（置顶）、`overdue`、`tomorrow`、`week`、`later`、`nodate`、`news`（没日子的钱和状态类邮件）。
- **打勾** = 做完了或不用管（`schedule_marks.done_at`）：从列表里去掉，提醒的脚本也不再提（Grava 的 watcher 读 `schedule_marks`）。邮件条目打勾同时跑 `remember.mail.cmd --done <id>`（取消：`--undo`）；改条目（`/api/remember/edit`）跑 `--edit <id> --json …`。
- **Agent 改的**（`schedule_ctl.py`，带 `source` 调这些接口）：它正在回复时，从它的 SSE 流发一张 `schedule` 卡；回复结束挂到那条回复下面（`schedule_log.message_id`）。每次改动都能撤销（`/api/schedule/undo/{id}`）。Agent 用同一个 `key` 再加一次就是改那一条，但不会盖掉用户自己挪过的时间。
- **iPhone 日历**：`GET /cal/<令牌>.ics` 在 `/api` 之外、不要认证头，链接里的令牌就是密码（app 里能换）。四类开关：课表（默认关，手机上已经有课表就不重复）、你的和 Agent 排的、截止、邮件里的活动。

```bash
python3 schedule_ctl.py day [--date 明天] [--days 3]
python3 schedule_ctl.py remember
python3 schedule_ctl.py add --title "训练 · Push A" --date 今天 --start 17:30 --end 18:30 --key fitness:training:2026-09-28
python3 schedule_ctl.py skip "ics:2026-09-28T13:00|Office Hours" --every-week
python3 schedule_ctl.py done "mail:1a0d…:todo"
python3 schedule_ctl.py undo 42
```

| 接口 | 做什么 |
|---|---|
| `GET /api/schedule?from=&days=` | 合并后的时间线（从 `from` 起 1–14 天，默认今天）。每条：`id`（改它用的 ref：`item:` / `ics:` / `canvas:` / `mail:` / `app:`）、`kind`、`origin`、`title`、`date`、`start`、`end`、`allDay`、`badge`、`by`、`link`、`done`、`skip`、`series`、`attended`、`actualStart`、`actualEnd`、`clash`、`past` |
| `POST /api/schedule` | 加一条 `{title, date, start?, end?, kind: event/deadline, location?, note?, key?, source?}` |
| `PATCH /api/schedule/{id}` / `DELETE` | 改 / 删自己的（软删，能撤销） |
| `POST /api/schedule/mark` | 课：`{ref, skip?, series?, location?, note?, attended?, actualStart?, actualEnd?}` |
| `GET /api/remember?all=` | 要记得的，分好组（`all=1` 连打过勾的也给） |
| `POST /api/remember/done` | `{ref, done}`：打勾 / 取消 |
| `POST /api/remember/edit` | `{ref, title?, due?, detail?, type?}`：改邮件条目（或自己加的截止） |
| `POST /api/schedule/undo/{log}` | 撤销一次改动（`{redo: true}` 再做回来） |
| `GET/POST /api/schedule/feed` | iPhone 订阅：`{path, include}`；`{include}` 开关各类，`{rotate: true}` 换链接 |

`server.json` 的 `remember.mail`（可选）：`{"items": "<邮件抽取脚本写的 JSON>", "cmd": ["python3", ".../mail_digest.py"], "sources": {"<键>": "<显示名>"}, "link": "https://mail.google.com/mail/?authuser=…#all/{thread_id}"}`。

## 项目

有始有终的事：持续几天到几周、有目标和截止（小组作业、求职冲刺）。一个项目 = 一个对话线程（以前的「独立空间」，id `sc-…`）+ 顶上一张**项目卡**：目标、截止、下一步、已定的、进度、在项目里派的任务，归档后加一份结论。见 [`projects.py`](projects.py)。

- **截止放在日程层**：自己的截止是 `schedule_items` 的一行（kind deadline，key `project:<id>:…`）；已有的（课程作业、邮件条目、申请截止、别的截止）按 ref 挂上。打勾、提醒、「要记得的」都照旧，`/api/remember`、`/api/schedule` 的条目多一个 `project: {id, title}`。
- **跨天接得上**：项目里每天第一句话、项目卡改过以后，`chat.start_run` 把项目卡拼在消息前面（模型看得到，对话里不显示；`side_chats.fed_rev` / `fed_at`）。日结脚本给每个项目发「【自动触发】日结（项目）」，让它更新进度和下一步。
- **Agent 改的**（`project_ctl.py`，带 `source` 调这些接口）：回复进行中在 SSE 流里出一张 `project` 卡，回复结束挂到那条回复下（`project_log.message_id`），能撤销（`/api/projects/undo/{id}`）。截止的改动是日程层的改动，出 `schedule` 卡。
- **开项目**：app 里开，或用户让 Agent 开（`POST /api/projects`，可带 `brief`，像转交一样转进新项目）。Agent 自己想到的走收件箱（`POST /api/projects/propose` → kind `project`），同意了服务端开好、把 brief 转进去。
- **归档**：`POST /api/projects/{id}/archive {summarize}` 马上收进「已归档」；带 `summarize` 就在项目里发「【自动触发】项目归档」，让它写结论（`/conclude`）和记忆。`POST /api/projects/review`（日结调）对最后一个截止过了 3 天以上的项目，经收件箱问一次「归档？」。

```bash
python3 project_ctl.py list
python3 project_ctl.py create --title "小组作业" --goal "…" --deadline "组内彩排|2026-10-01 18:00" --link "canvas:…" --brief "…"
python3 project_ctl.py add sc-1a2b3c4d decision "视频 8 分钟以内"
python3 project_ctl.py done sc-1a2b3c4d pi-5e6f7a8b
python3 project_ctl.py ask sc-1a2b3c4d "周五前还差什么"
python3 project_ctl.py conclude sc-1a2b3c4d --done "…" --learned "…"
```

| 接口 | 做什么 |
|---|---|
| `GET /api/projects?all=` | 没归档的项目（`all=1` 全部），每个带 `goal`、`next`（最近一个没勾的截止）、`stepsLeft`、`hasSummary` |
| `POST /api/projects` | 开一个：`{title, goal?, model?, deadlines: [{title, due} 或 {ref}], steps?, decisions?, brief?, source?}` |
| `GET /api/projects/{id}` | 项目卡：`goal, progress, deadlines, steps, decisions, next, stepsLeft, tasks, summary, archived, closing, rev` |
| `PATCH /api/projects/{id}` | `{title?, goal?, progress?}` |
| `POST /api/projects/{id}/items` | `{kind: step/decision/deadline, text, due?, ref?}` |
| `POST /api/projects/{id}/items/update` / `…/delete` | `{id, text?, due?, done?}` / `{id}`（挂上的截止只能打勾或拿掉） |
| `POST /api/projects/undo/{log}` | 撤销项目卡的一次改动（`{redo: true}` 做回来） |
| `POST /api/projects/propose` | Agent 的提议 → 收件箱 kind `project` |
| `POST /api/projects/{id}/archive` / `restore` / `conclude` | 归档（`{summarize}`）、恢复、写结论（`{done, decided: [], learned, saved}`） |
| `POST /api/projects/review` | 截止都过了的项目问「归档？」（日结用） |
| `GET /api/sidechats` | 侧栏列表，每个项目也带上面那几个摘要字段 |

## 目标

按领域分的长期目标（健康 / 学业 / 职业 / 财务，存的就是这四个中文词，app 按语言显示），用户在 app 里改，Agent 在命令行里改。见 [`goals.py`](goals.py)。

- **一个目标** = `goals` 表一行：标题、说明、截止（`YYYY-MM-DD`、`YYYY-MM` 或「2027 秋」这样的说法）、可选的数字目标（`targetLow` / `targetHigh` / `unit`，可以只给一头）、可选的 `metric`（`bodyfat` 体脂、`weight` 体重：服务端自己读当前值）、归哪个 Agent 盯（`groupId`）、`status` active / done / dropped。从不删除：「不做了」= `dropped`，在页面最下面折起来。
- **读数**：身体数据源（`xunji.py`，体重、体脂近 400 天一次查询、有缓存，`/api/goals` 和趋势共用）为主，Apple 健康的日均值（`health_metrics` 的 BodyMass / BodyFatPercentage）对照。当前值取两边最新的那次（同一天以身体数据源为准）。体脂从不自动算，只读记下的。进度从起点（设目标那天或之前最近的一次读数）到目标区间，往下往上都行，进了区间 = 100%；读数超过 30 天算 `stale`。
- **每次改动**记一行 `goal_log`（谁、之前 / 之后）和一行活动记录。撤销只改回这次动过、之后没人再改过的字段（`kept` 是没动的那些；全都后来改过 = 409）；撤销「加了」= 把目标藏起来。Agent 24 小时内的改动、用户还没点「知道了」的，在 `recent` 里：app 目标页顶上一条，能撤销。
- **Agent** 用 `goals_ctl.py`（`list`、`trend`、`log`、`add`、`update`、`done`、`drop`、`reopen`、`undo`；`--source` = 谁在改）。skill（`packs/core/skills/goals`）：用户让改的直接改；它自己的主意先交收件箱；用户没说过的目标不编。

```bash
python3 goals_ctl.py list [--all]
python3 goals_ctl.py add --title "体重回到 75 kg 以下" --category 健康 --metric weight --low 72 --high 75 --due 2026-12-31 --agent fitness
python3 goals_ctl.py update bodyfat --low 14 --high 16
python3 goals_ctl.py drop goal-1a2b3c
python3 goals_ctl.py trend --metric weight
python3 goals_ctl.py undo 12
```

| 接口 | 做什么 |
|---|---|
| `GET /api/goals?fresh=` | `{goals（进行中的，带 current、currentDate、currentSource、start、progress、direction、state、stale、daysLeft）, closed（完成 / 不做了的）, recent（Agent 的改动，能撤销）, metrics}`；每个目标前面那些字段没变，老 app 照常能用。`fresh=1`：身体数据源的缓存超过 90 秒就重读 |
| `POST /api/goals` | 加一个 `{title, category, detail?, due?, unit?, targetLow?, targetHigh?, metric?, groupId?, position?, source?}` |
| `PATCH /api/goals/{id}` | 只改给了的字段（`null` 清掉说明 / 截止 / 单位 / 目标数字 / metric / groupId），外加 `status`、`position` |
| `POST /api/goals/undo/{log}` | 撤销一次改动（`{redo: true}` 做回来） |
| `GET /api/goals/log?limit=&goal=` | 最近的改动，新的在前 |
| `POST /api/goals/seen` | `{ids}`：目标页顶上那条点了「知道了」 |
| `GET /api/goals/trend?metric=weight&days=180&fresh=` | `{series: [{date, value, source: body / health}], summary: {latest, avg7, change30: {value, since}, check}, sources}`；哪边都没接 = series 为空，不算错 |

## 看板、功能包和提醒

每个 Agent 有自己的表，看板上的积木由它自己摆；app 只按服务端算好的数据画七种积木，不自己算。见 [`boards.py`](boards.py)、[`packs.py`](packs.py)、[`alerts.py`](alerts.py)，Agent 用 [`board_ctl.py`](board_ctl.py)。

| 接口 | 做什么 |
|---|---|
| `GET /api/boards/{agent}` | 现在的看板：`blocks`（Agent 的积木和数据）、`sections`（内置看板各节的显示顺序，`{id, title, hidden}`）、`collections`、`strip`（Agent 改过、用户还没点「知道了」时的撤回条） |
| `PUT /api/boards/{agent}` | 整份换一版 `{blocks, sections?, note, mode: apply / propose, by}`。`sections`（内置小节的顺序、藏没藏）不给就沿用现在的，Agent 加一块不会把用户挪过、藏过的冲掉 |
| `GET /api/boards/proposal/{inboxId}` | 看板提案（kind `block`）的预览：改了的块，用现在的数据画 |
| `POST /api/boards/plan`，`PUT` / `GET /api/boards/plan/{inboxId}` | 新 Agent 的方案 `{tables（可以带示例行）, blocks}`：在内存里校验、画出来，建 Agent 的卡在 Agent 还没建时就能看看板（`inbox_ctl.py add --board-file`） |
| `GET /api/packs`，`GET /api/packs/{name}` | 功能包（`packs/<名字>/pack.json`：表 + 积木 + 提醒 + `GUIDE.md`），以及装在哪些 Agent 上 |
| `POST /api/packs/{name}/install` | `{agent, mode: apply / propose / check}`：没有的表建上，已有的只补缺的字段和选项，看板上已经有的块不重复加；propose 走收件箱，预览是装好以后的看板。包里的提醒另外各出一张卡 |
| `POST /api/packs/{name}/remove` | 看板上拿掉这个包的积木（表和数据留着） |
| `GET /api/alerts/{agent}`，`POST /api/alerts/item/{id}` | 开着和暂停的提醒；暂停 / 恢复 / 删掉 |
| `POST /api/alerts/{agent}/check`，`POST /api/alerts/{agent}/propose` | 一条提醒规则 `{id, title, source（和列表一样的查询）, row, message, at, days, level}`：check 看今天会推什么；propose 出一张 kind `push` 的收件箱卡（新推送一律要用户点头）。同意后服务端每 30 秒看一次，到点查出有东西就推一条（服务器没开着，3 小时内补推），点开进这个 Agent 的看板 |

## 日结提案

每晚日结之后，主对话回看这一周，把反复出现的事提成一条提案：**加一个 skill**（一套做法，给用得上的 Agent），偶尔**建一个 Agent**。提案进收件箱，用户点头之前什么都不变。见 [`proposals.py`](proposals.py)；主对话用 [`proposals_ctl.py`](proposals_ctl.py) 和 `proposals` skill，夜里的触发由 `packs/core/scripts/daily_close.py` 发。

| 接口 | 做什么 |
|---|---|
| `GET /api/proposals/context?days=7` | 回看的材料：这几天用户在各个对话里说的话（配上回复的开头）、现有的 skills 和谁能用、各个 Agent、提过的提案和被拒的理由、今天还能提几条 |
| `POST /api/proposals` | `{kind: skill / agent, slug, title, why, evidence: [{date, thread, quote}], changes?, skill: {name, agents, markdown} / agent: {name, purpose, icon, color, board}}` → 一张 kind `skill` / `agent` 的收件箱卡。每天最多 2 条（429）；同一个 `slug` 只提一次（还在等、做过、被拒都 409；用户引用卡片说了要改之后，同一个 slug 再交是原地改这张卡）；skill 重名 409 |
| `GET /api/proposals`，`GET /api/proposals/{id 或 inboxId}` | 提过的提案和状态（pending / installed / rejected / withdrawn / failed / expired） |

点了同意由服务端自己做（收件箱钩子）：skill 写进 `<workspace>/skills/<名字>/SKILL.md`，加进那几个 Agent 在 `openclaw.json` 里的 skills 允许列表（先备份、改完校验、不通过就恢复；没设允许列表的 Agent 本来就什么 skill 都能用）；Agent 和「新建 Agent」一样建好，连表和起步看板。卡片变成回执，主对话只收到一句知会；没做成就在卡片上写明原因。

```bash
python3 proposals_ctl.py context
python3 proposals_ctl.py skill --slug meal-swap --name meal-swap --agents diet --title "…" --why "这周第 3 次……" \
    --evidence "9/24|饮食记录|换成三文鱼能吃多少？" --file SKILL.md
python3 proposals_ctl.py agent --slug reading --name 读书 --purpose "…" --icon book --board-file board.json --title "…" --why "…" --evidence "…"
```

## 推送

三档：**ring** 响铃（有声音，interruptionLevel active）、**quiet** 静默（不出声，进通知中心，passive）、**none** 不推。`server.json` 的 `push.quiet_hours`（默认 `["23:00", "07:30"]`，按 `timezone`；`[]` = 不设）里 ring 自动降成 quiet。

| 什么时候 | 档位 |
|---|---|
| 你发的消息回完了 | ring |
| 主对话转给 Agent（relay）、学习台 | none |
| 系统触发 `/api/chat/trigger` | 请求里的 `level`；只给 `notify: false` = none；都不给 = quiet |
| 收件箱新条目 / 改好重新提交 | 条目的 level（task / write / send / spend / calendar 默认 ring，其余 quiet） |
| 收件箱做完 / 没做成 | quiet |
| 从 app 派的后台任务做完、没做成、到点停了；「改一下」的一轮做完（`tasks.notify_done`） | quiet（Telegram 派的由 OpenClaw 在 Telegram 里回；取消的不推） |
| `/api/push/send`（起床报告、ddl 提醒……） | 请求里的 `level`，默认 ring |
| 冥想时间里的以上任何一条 | 压住（返回 `held: true`），结束时进小结 |

回复期间写了建议卡（`feed_items` 多了一行、group_id 是这个线程；main 认没挂 Agent 的卡）就推卡片（副标题是卡的类型，正文是「卡标题 · 第一条要点」），否则推回复的开头（去掉 Markdown，按句子截断）。`data` 带 `thread`（老版本 app 只认它）、`target`（`{type: thread | card | inbox | today, …}`）、`level`（算过静默时段后实际用的档位）、`kind`（reply / card / inbox / done / report）。角标 = 收件箱待你点头 + 给你的未读回复。`/api/push/send` 收 `{title, body, thread?, thread_id?, subtitle?, level?, category?, collapse?, target?}`。

卡片和收件箱的推送另带 `data.card`，长按通知时 app 的通知内容扩展（1.0.5 起）把它画成一张卡：`k` 类型、`t` 标题、`s` 最多 3 个 `[标签, 值]` 数字、`r` 进度环 `[值, 满值, 标签]`、`l` 最多 5 条要点、`f` 脚注、`c` 颜色（Agent 的颜色名或 `#RRGGBB`）。键名短是因为整条推送只有 4 KB；`push.py` 的 `rich_card` 认三餐建议和训练建议，别的用卡片正文的前几行。

## 小组件和实时活动

`GET /api/widget`（`widget.py`）是 iOS 小组件（app 1.0.5）要的一小份：今天的恢复分（有了昨晚的睡眠才给）、今天最新一张 `meal_plan` 卡里的下一餐、今明两天还没过去的日程（`start` / `end` 是 Unix 秒，小组件自己按它把「下一件」往后挪；`time` 标签、`title`、`place`、`kind` class / training / deadline / event，最多 8 条），一周内「要记得的」有几件。文字按请求的语言排好；每块单独出错；按语言缓存 60 秒。

实时活动（`live.py`）：`GET /api/live` 列出现在锁屏 / 灵动岛上该有的，`{key, kind, state, staleAt}`（`state` 就是 Swift 的 `ContentState`：`title`、`subtitle`、`icon`（SF Symbol）、`accent`、`startAt` / `endAt`（Unix 秒）、`progress`、`lines`、`done`）。自带两种：冥想时间（`focus:<id>`，从 `think_focus` 推出来）和练后餐倒计时（`meal:post`：一次回复写了 `meal_plan` 卡、里面有还没到点的「练后」一餐就开，倒到那一餐的时刻；新卡里没有练后餐了就关）。别的用 `POST /api/live {key, kind, state, staleAt?, endsAt?, minutes?}` 开，`POST /api/live/{key}/end {state?}` 关。app 在前台时照着开、改、关；push-to-start 令牌和每个活动的令牌交到 `POST /api/live/token {type: start | activity, token, key?, id?}`，在锁屏上划掉的交 `POST /api/live/dismissed {key}`（同一个 key 下次新开之前不再开）。`server.json` 配了 `apns`（`{key_file, key_id, team_id, topic: <bundle id>, sandbox?}`，APNs 的 `.p8` 密钥；Expo 的推送服务不转发实时活动）以后，服务器还直接用 HTTP/2（`curl --http2`）推给苹果开、改、关，不用打开 app。

## 未读

`GET /api/unread` → `{threads: {<线程>: {n, mine, last: {id, text, ts, origin}}}, feedNew: [卡片 id], inbox, badge}`。只列有未读的线程（main、各 Agent、没归档的独立空间）；n = 读到的位置之后助手回了几条，mine = 其中回的是你发的话（`messages.origin = user`，定时器和收件箱触发的不算）；inbox = 等你点头的条数（含执行审批）；badge = inbox + 各线程 mine 之和。`POST /api/unread/read {thread, upto?}` 标成已读（只往后挪），返回同样的摘要。「今天」页的新卡片：`GET /api/feed` 每张带 `seen`，`POST /api/feed/seen {ids}` 标成看过。

## 学习台

`/study` 是给电脑用的宽屏页面：左边是课程和模块，中间看学习页、课件（PDF）、闪卡、小测，右边就着这一节的材料提问。在 `server.json` 的 `study` 里配置（见 [`study.py`](study.py)）：

- `materials`：课件目录，一门课一个文件夹，下面一层是模块（周 / session），模块里放文件。按模块下载的课程平台镜像（比如 Canvas）正好是这个结构。
- `pages`：学习页目录，一门课一个文件夹，Markdown，开头 YAML front matter（`session`、`title`、`sources` = 相对课程文件夹的课件路径）。学习页挂在第一个来源所在的模块下；视频放 `<课程>/media/`，文件名 `S03 ….mp4`。
- `courses`（可选）：显示哪几门、什么顺序。`deadlines_cmd`（可选）：打印 `{due, course, title, url}` JSON 数组的命令，显示在顶栏。
- `readings`（可选）：阅读清单目录，一门课一个 `<课程>.json`：`{"items": [{title, kind, required, instructions, sessions, file, status, url}]}`，`file` 相对这门课的课件目录。每节多一个「阅读」标签；必读材料没到手时，生成学习路线 / 闪卡 / 小测前会先提醒你补齐。
- `recordings`（可选）：录播字幕，`<课程>/index.json` 列出录播（`id, name, start, duration, sessions, file`），`file` 里是 `{viewer_url, segments: [{t, text}]}`。每节多一个「录播」标签（带时间点的字幕，点时间跳到录播那一刻），字幕也会放进问答前情。学校的录播规定要求保密的话，字幕只留在自己的服务器上。
- `video_cmd`（可选）：渲染命令（argv 列表，`{script}` = 助手写的 Manim 脚本，`{media_dir}` = 工作目录）。配了以后每节多一个「做视频」：助手写脚本、服务器渲染，出错把报错交回去改一次。这会在本机运行助手写的代码，只在已经信任助手能在这台机器上执行代码时打开。

每个学习页打开先看到**学习路线**：按这一节的全部材料排 5–8 步（做什么、看课件哪几页 / 学习页哪一节 / 哪篇阅读 / 录播哪个时间点、大概多久），每步可以打勾，进度存在服务器上，目录里也显示。

**复习**：播客的费曼讲错和漏了的，点一下加进这门课（`POST /api/study/review {course, page?, items, …}`，存在 `pages/<课程>/.gen/review.json`，挂在那一节上）；学习台打开那一节时顶上一个「复习」框，点「复习过了」划掉（`POST /api/study/review/done`）。`GET /api/study/review?course=&page=&all=`。

提问走和 app 同一条对话通道，每个学习页一个线程；每天第一个问题会带上学习页、课件全文、录播字幕和阅读材料（放得下的放全文，放不下的给路径），以及你正在做学习路线的第几步。闪卡、小测、学习路线也这样生成，存在学习页旁边。

## 世界树

app 里的「我 → 世界树」。你的各个 AI（Claude、ChatGPT、Gemini、Claude Code 和你自己的 Agent）共用的记忆放在 Obsidian 库里，一条记忆一篇笔记，挂在枝上。索引、写入、遗忘都归 workspace 的 `memory_tree.py` 管；这个服务只读它，再把三个动作转过去（可选数据源 `tree`：没有这个脚本时两个接口都回 `ok=false` + `missing_source: tree`）。见 [`memtree.py`](memtree.py)。

| 接口 | 做什么 |
|---|---|
| `GET /api/tree` | `branches` 按先序排（大枝后面跟着它的小枝；`leaves` = 直接挂在上面的，`total` = 连小枝一起的，`agents` = 默认挂到这里的 Agent）、`leaves`（当前的：active 和 pending，不含档案要点；`source` 是笔记里写的，`origin` 是最早记下它的平台，每周修剪改写过的顺着 `supersedes` 找回去）、`trunk: {name, count}`（档案）、`counts: {total, pending, bySource}`（按 origin 数）、`issues`（格式不对、先跳过的笔记数） |
| `POST /api/tree/{id}` | `{action: confirm}` 待确认 → 当前；`{action: forget}` 笔记掏空成只剩属性的空壳挪进归档，索引里删掉；`{action: move, branch}` 挪到别的枝（写主干的名字 = 直接挂主干）。活动记录由 `memory_tree.py` 写，不含记忆内容 |

## 连接

「我 → 连接」：助手接着的每一样东西，现在怎么样。`GET /api/connectors[?fresh=1]` → `{groups: [{id, title, items}], counts: {ok, warn, off}, checkedAt}`；每一项 `{id, name, icon, status: ok | warn | off, line, facts: [{label, value}], uses, fix, open}`（`open` = app 里能跳去的页）。整份结果按语言缓存 60 秒（`fresh=1` 跳过），聊天渠道的在线状态（`openclaw channels status`）缓存 2 分钟。每一项都是尽力而为，不返回任何密钥：只看密钥的名字在不在、文件的时间、条数和 systemd 单元的状态。你的机器上没有的（脚本、单元、目录）那一项就不出现；自带的功能还没用上的（Apple 健康、日历订阅、推送）显示「没接」。日历订阅（`/cal/<令牌>.ics`）现在会记下日历上次来取的时间和大致是哪种（iPhone / Mac / Google / Outlook）。见 [`connectors.py`](connectors.py)。

## 思考空间和收藏

app 的「思考」tab（2026-09-28 起界面上叫 **Zen**，代码和接口仍叫 think）：想到什么先扔进来（一句话、几个 `#关键词`、语音、照片、文件、链接、长文），没有人回。勾几条点「聊聊」或「想完了」，模型才参与。「收藏」存别的 App 里的好东西（链接、文件、截图），同样不调模型，你决定怎么处理。「冥想时间」期间推送全压住，结束时一次给你。见 [`think.py`](think.py)、[`saves.py`](saves.py)。

- **一条想法 = 一篇 Markdown 笔记。** `server.json` 配了 `think.vault`（例如 `"think": {"vault": "~/vault", "obsidian_vault": "库的名字"}`）就放进库的收件箱，Obsidian 里看得到，在那边改了也读得回来；没配放 `<data_dir>/think/`。文件夹名按语言默认（中文：收件箱 / 收件箱/已想完 / 收件箱/附件 / 笔记 / 写作；`think.inbox_dir`、`done_dir`、`attach_dir`、`notes_dir`、`writing_dir` 可改）。属性：`id, kind, created_at, source, keywords, tags, topics, note, files, url`；附件在正文末尾嵌成 `![[…]]`。在 Obsidian 里新建的笔记也收。文件按 mtime 和大小判断有没有变，1 秒内刚改的等下一轮（同步客户端不是原子写），写一律临时文件 + rename。删除 = 挪进库的 `.trash/`；想完的挪进「已想完」，不删。
- **关键词** = 属性 `keywords` + 正文里的 `#词`（跟在中文后面也算）。关键词页列出带它的全部想法和收藏、用过它的主题、常一起出现的词。
- **主题**（`think_topics`，id `tp-…`，也是对话线程和 OpenClaw 会话 `agent:main:grava:tp-…`）：「聊聊」先发一句「【自动触发】聊聊」，让模型先问、不急着下结论；每天第一句话、主题里的碎片变了以后，`chat.start_run` 把碎片和「陪你想」的规矩拼在消息前面（对话里不显示）。聊的时候「只记下」的一句不给模型看（`note: true`，对话里一行虚线，不发给 Gateway），想完了时一起用。「想完了」在另一个会话里后台整理草稿（标题、一句话、要点和出处、还没想清的、下一步、关键词、记进世界树的一句），你改完「存进库」：写进笔记或写作文件夹，碎片挪进已想完，要记的经 workspace 的 `memory_tree.py` 写一片世界树叶子。
- **收藏**（`think_saves`，原件在 `<data_dir>/saves/`，`think.saves_dir` 可改）：原件不进库。存的时候抽一份正文（网页在后台抓，公众号文章也行，原文删了也还在；PDF / Word / 表格抽文字），抓不到就把原因记在链接旁边。之后你决定：带进主对话问（`/api/chat/send` 的 `save` 把正文给模型）、交给某个 Agent（在它的线程里安静地跑一轮）、放进思考变成一条想法、提炼成笔记、删掉（软删，能恢复）。
- **搜索**在内存里按字面找想法、收藏、聊过的主题和存进库的笔记（中文两个字就能搜），结果带高亮分段，不调模型。**历史**按天数想法和收藏。
- **冥想时间**（`think_focus`，25 / 45 / 90 分钟或不限 = 3 小时）：开始前 app 先列出这段时间里的日程；期间 `push.send_push` 一律不推，记进 `think_focus_held`；结束（点结束或到点）给小结：压住的按去处合并（同一个对话只留最新一条），加上等你点头的和接下来的日程。没人看过的小结，下次 `GET /api/think/focus` 还会给。

| 接口 | 做什么 |
|---|---|
| `GET /api/think/stream?before=&limit=` | 想法（新的在前，只记下的不列）、在想的 `topics`（`count` = 碎片数，`notes` = 只记下的句数）、`savesNew`、`vault`、`folder`、`obsidianVault` |
| `POST /api/think/fragments` | `{kind?, text?, title?, keywords?, url?, topic?}`；带 `topic` = 在这个主题里只记下 |
| `POST /api/think/fragments/upload` | multipart：最多 10 个 `files`、`text`、`kind`（voice 转文字、原声留着）、`keywords`（JSON 数组）、`duration`、`title` |
| `GET` / `PATCH` / `DELETE /api/think/fragments/{id}` | 一条想法；`{text?, title?, keywords?}` 改笔记本身；删 = 挪进库的回收站 |
| `GET /api/think/file/{id}/{index}?thumb=1` | 附件（缩略图缓存在库外面） |
| `POST /api/think/notes` | `{title, text, folder}`：长文直接存进写作（或笔记）文件夹 |
| `POST /api/think/topics` / `GET /api/think/topics?status=` | 开一个主题 `{fragments, title?}` / 列出来 |
| `GET` / `PATCH /api/think/topics/{id}` | 主题、它的碎片和草稿 / `{title?, add?, remove?, status: open?}` |
| `POST /api/think/topics/{id}/talk` | 开始聊（模型先问） |
| `POST /api/think/topics/{id}/done?fresh=` | 后台整理草稿（`draftStatus` running → ready / failed）；`fresh=1` 重新整理 |
| `POST /api/think/topics/{id}/save` | `{title, oneLine, points, open, next, keywords, folder: notes / writing, tree?, branch?}` |
| `GET /api/think/search?q=&scope=` | `scope`：all / idea / save / topic / note |
| `GET /api/think/keywords` / `GET /api/think/keyword?k=` | 关键词按用得多排 / 一个关键词的页 |
| `GET /api/think/days?month=` / `GET /api/think/day?day=` | 一个月每天的数 / 某一天的想法和收藏 |
| `GET /api/think/focus`、`GET /api/think/focus/preview?minutes=` | 现在的冥想和没看过的小结 / 这段时间里的日程 |
| `POST /api/think/focus/start` / `end` | `{minutes}`（0 = 不限）/ `{words?, notes?}` → 小结 |
| `GET /api/think/focus/summary/{id}`、`POST /api/think/focus/seen/{id}` | 之前的小结 / 标成看过 |
| `GET /api/think/saves?filter=` | `filter`：all / new / link / file / image / text，另给 `new` 数 |
| `POST /api/think/saves` / `…/upload` / `…/from-message` | 存链接或一段字 `{url?, text?, title?, note?, source?, keywords?}` / 文件 / 对话里的一条消息 `{thread, id}` |
| `GET` / `PATCH` / `DELETE /api/think/saves/{id}` | `?full=1` 给全文 / `{title?, note?, keywords?, seen?}` / 软删（`…/restore` 恢复） |
| `GET /api/think/saves/{id}/file?thumb=1` | 原件 |
| `POST /api/think/saves/{id}/give` / `…/to-idea` | 交给 Agent `{agent}` / 放进思考 |

## 播客

「思考」（Zen）的第三块：说出来，录完帮你理成笔记。它是「聊聊」的语音版，录完走同一条路：理成笔记（尽量用你的原话）→ 存进笔记 / 写作 / 学习 → 长期有用的问你记不记世界树，不点不记。原声和逐字稿留在服务器上（`<data_dir>/podcast/<期>/`，`podcast.dir` 可改），不进库。见 [`podcast.py`](podcast.py)。

- **今天聊点什么**：从 Zen 里还没想完的主题、库里笔记的「还没想清的」、学习台最近几节、6 天内的截止、世界树里挑，模型挑 4 个写成具体的问题，每条写明从哪来，按天缓存；「换一批」再挑。
- **四种录法**：自己讲（它只听）/ 有主持人（你停下它才问，一次一个，能跳过、能换个问法）/ 费曼（它扮聪明的外行追问，讲完对照学习台这一节的课件、学习页和录播字幕，列出讲对、讲错、漏了，带出处；没有课件就按公认的讲法对照）/ 约朋友（坐一起用一台手机，录完按声音分人，第一次让你认一下谁是谁；只有你说的进笔记，每人一份纪要）。
- **录前先聊聊**：它先问一句第一反应，你答（打字或说一段），它按你的话排一张 3–5 条的提纲卡，录的时候一直在屏幕上。只出提纲，不写稿子。
- **一段一段录**：app 每停一次就传一段，服务器马上转写：`gpt-transcribe` 出文字（带词表、每个词的置信度，低的标「听不准」），`whisper-1` 出逐句时间，两边按字对齐，点一句播一句。词表 = 你改过的词 + `transcribe_prompt` 里的常见词 + 课名、Agent 名、项目名、世界树的枝（档案里的人名、住址不进词表）。逐字稿里改一句，改掉的词自动进词表。
- **录完整理**（后台，app 轮询）：校对同音字和专有名词 → 标题、一句话、你的原话（带时间点）、还没想清的、关键词（建议的点了才加）、要不要记世界树 → 跟库里以前的笔记比想法变没变 → 费曼对照。
- **模型**一律走 [`llmjson.py`](llmjson.py)：OpenClaw 的 `llm-task`（零工具、不进任何对话；主持人一个追问两三秒），没开就用一个用完即删的会话一问一答。转写要 `OPENAI_API_KEY`（和语音输入同一个）：`gpt-transcribe` 约 $0.0045 / 分钟、`whisper-1` 约 $0.006 / 分钟。
- **素材**：一期可以放进已有的东西：任何对话里的一条（长按 →「放进播客」，或者在这一期里挑）、和朋友的聊天（两边说的都行）、文件（PDF、Word、Excel、PPT、文本；录音转成文字）、Zen 的想法和主题、收藏。放进来时抽成文字存下（每条最多 12000 字、每期最多 40 条），录前聊天、主持人追问、录完整理（新的在前，8000 / 6000 / 24000 字）、跟以前的笔记比（只拿你自己说的）、费曼对照（文件当课件）都参考。朋友说的只在这一期里用：不原话引用、不进标题和世界树，存进库的笔记「参考了」那一节只写「参考了和小林的聊天」。`llm-task` 用不了、退回普通对话回合（带工具）时，朋友说的不带进去（`llmjson.ask` 的 `fallback_input`）。见 [`podmaterials.py`](podmaterials.py)。
- **朋友画像**（约朋友录的）：开录前选谁在；认人时给每个声音对上一个人（以前一起录过的、朋友、或者新名字；也可以只写名字、不记画像）。整理完给每个对上的人记 3–8 条（看法 / 在做的事 / 在意的 / 下次问问），每条落在这个人说的一句上（点一下听原话）。说的还是那件事就不再写；变了的写新的一条、旧的留作以前的说法；「下次问问」有了答案的标成问过了。重新整理、删掉一期会撤回这一期记的（你改过的留着）。只有你看得到：主持人和录前聊天能接上以前说的（「上次小林说……」），「今天聊点什么」能出「约小林聊……」；别的都不读——名片 agent 哪一档都不用，主对话和 Agent 不用，也不进库和世界树。会告诉朋友：约朋友那页说会记画像，每人纪要最后一句也写着。约朋友录的，标题、一句话、关键词只按你说的写（它们会存进库）。画像只经 `llm-task`（零工具；整段都是别人的话）记：没有它这次就不记，录完页会说。退回普通对话回合时不带画像，约朋友录的只带你自己说的句子。见 [`people.py`](people.py)。
- **远程一起录还没做，接口先留着**：以后（可能是 app 里直接打电话）每个人一条音轨，按 `track`（谁）和开录时的时间（对齐）传进同一期，整理时按音轨分人，不用再猜声音。现在的分段是一条时间线接着一条。

`server.json` 的 `podcast`（全部可选）：`dir`、`text_model`（默认 `gpt-transcribe`）、`time_model`（默认 `whisper-1`，`""` = 不要逐句时间）、`thinking`（默认 `low`）。

| 接口 | 做什么 |
|---|---|
| `GET /api/podcast` | 今天挑好的话题（还没挑是 `null`；`mode: friends` + `source.person` = 约某个朋友聊）、最近 40 期（`chip` = 列表上那个小标签）、`study`（配了学习台没有）、`materials` / `people`（这台服务器有素材、朋友画像） |
| `POST /api/podcast/suggest` | `{exclude?}` 挑 4 个（换一批时把现在的传进来） |
| `POST /api/podcast/episodes` | `{title, mode: solo / host / feynman / friends, source?, people?}` 建一期（约朋友：谁在，`[{id} \| {friend} \| {name}]`） |
| `GET` / `PATCH` / `DELETE /api/podcast/episodes/{id}` | 一期的全部（提纲、每段的句子和时间、问过的、整理结果（带 `people` 画像多了几条）、费曼、存到哪、`materials` 素材条数、`people` / `speakerPeople`）/ `{title?, mode?, outline?, done?, cur?, speakers?, people?}`（坐一起录的认人：`{"A": "@me", "B": "小林"}`；`people: {"B": {id} \| {friend} \| {name} \| {name, skip}}` 给声音对上人，名字跟着人走）/ 删原声、逐字稿、素材和这一期记的画像（存进库的笔记、你改过的画像还在） |
| `POST /api/podcast/episodes/{id}/prep` | 录前聊：`{}` 它先问 / `{text}` 你答 / `{outline: true}` 现在排提纲；`…/prep/voice`（multipart `file`、`duration`）说一段 |
| `POST /api/podcast/episodes/{id}/segments` | multipart `file`、`idx`（从 0 数；同一个 idx 再传 = 重传）、`duration`：传一段，马上开始转写 |
| `POST /api/podcast/episodes/{id}/ask` | `{how: next / again / skip}`：等这段转完问一个 / 换个问法 / 跳过 |
| `POST /api/podcast/episodes/{id}/finish` | 录完了，后台整理（`status` processing → ready / naming（坐一起录的要先认人）/ failed）；再点 = 重新整理 |
| `PATCH /api/podcast/episodes/{id}/sentence` | `{idx, i, text}` 改一句（改掉的词进词表） |
| `POST /api/podcast/episodes/{id}/save` | `{folder: notes / writing / study, title, oneLine, quotes, open, keywords, relates, explain?, tree?, branch?}`：写进库（再存一次覆盖同一篇），`tree` 给了才记世界树 |
| `POST /api/podcast/episodes/{id}/review` | 费曼讲错和漏了的加进学习台复习 |
| `GET /api/podcast/episodes/{id}/audio/{idx}` | 一段原声（支持 Range；网页版可以用 `?token=`） |
| `GET` / `POST /api/podcast/episodes/{id}/materials` | 这一期的素材 / `{items: [{kind: chat / friend / idea / topic / save, ref}]}` 放进几条（同一条不重复放） |
| `POST /api/podcast/episodes/{id}/materials/upload` | multipart `files`（最多 10 个、每个 25 MB）：抽文字，录音转写；原件留在这一期的目录里 |
| `DELETE /api/podcast/episodes/{id}/materials/{mid}` | 拿掉一条 |
| `GET /api/podcast/pick?kind=chat\|friend\|idea\|topic\|save[&friend=][&days=]` | 挑素材的候选：对话里你说的、朋友列表 → 某个朋友的聊天、Zen、收藏 |
| `POST /api/podcast/materials/quick` | `{kind, ref, episode?}`：长按一条「放进播客」，放进某一期或者新开一期（朋友说的不拿来当标题） |
| `GET` / `POST /api/people` | 人（每人几条）和还没对上人的朋友 / `{name, friend?}` 加一个 |
| `GET` / `PATCH` / `DELETE /api/people/{id}` | 一个人：画像（每条的出处是哪一期哪一句、取代了哪条）、一起录过的几期 / `{name?, friend?}` / 连画像一起删 |
| `POST /api/people/{id}/notes` | `{kind: view / doing / care / ask, text}` 自己加一条 |
| `PATCH` / `DELETE /api/people/notes/{nid}` | `{text?, kind?, status?: active / done}` / 删一条（删的是新说法，旧的那条回来） |

## 分享

把一条回复、一篇想完了的笔记变成一个链接或一张干净版卡片：对话里长按一条 →「分享」，或者 Zen 想完了存好以后点「分享」；「我 → 分享出去的」列着发出去的。见 [`share.py`](share.py)。

- **分享是一份快照**：分享的时候把正文抄一份存进 `shares`，原文以后再改，已经发出去的不变。
- **先挡私事**：档案（`USER.md`）里的住址、伴侣和家人的名字，邮箱、电话，身体数字（体重、体脂、心率、睡眠……），还有 `share.private_words` 里你自己加的词，默认都挡住，你一处处放出来。挡住的原文不出服务器：链接页和卡片上都是一块灰条，标题也一样。
- **两种样子**：带链接的 `/s/<令牌>` 是一页干净的网页，不用装 app 就能看，`/s/<令牌>/card.png` 是聊天里显示的预览图；干净版是一张 3:4 的图，没有网址、二维码和 app 名字，发不让带链接的平台。图在服务器上用 Pillow 画，中文要有中文字体（自动找 Noto Sans CJK，`share.font` / `share.font_bold` 可以换）。
- **让外网打得开链接**：配上 `share.public_port`，`run.py` 会在 `127.0.0.1:<端口>` 上另起一个只有 `/s/` 的小服务（[`public.py`](public.py)：没有 `/api`，不认令牌也不认设备），用 Tailscale Funnel 或反向代理指过去（`tailscale funnel --bg --set-path /s http://127.0.0.1:<端口>/s`），再把 `share.public_url` 设成外面看到的地址。没配 `public_url` 时 app 只给干净版卡片。主服务上也有 `/s/`，只在你自己的设备上开得到；浏览次数只数从小服务进来的，链接预览机器人不算。
- **收回**：快照清空（挡好的标题留在你的列表里），链接页变成「已经收回了」。没发出去的草稿 7 天后删掉。不调模型。

| 接口 | 做什么 |
|---|---|
| `POST /api/shares` | `{kind: message, thread, id}` / `{kind: note, topic 或 path}` / `{kind: text, title?, text}` → 草稿，带 `masks`（标签、挡住的原文和前后几个字、放没放出来）和 `segments`；同一条再分享是同一份 |
| `GET /api/shares` | 发出去的和收回的；`canLink` = 配了对外地址 |
| `GET` / `PATCH /api/shares/{id}` | `{release?: [挡住处的 id], hide?: [...], quote?, title?, withQuestion?}`（`""` = 回到默认；`withQuestion` 会重新取快照） |
| `POST /api/shares/{id}/publish` | 链接开始能打开（配了 `public_url` 才有 `url`） |
| `DELETE /api/shares/{id}` | 收回（草稿直接删） |
| `GET /api/shares/{id}/card?style=clean\|link` | 卡片图（data URI）；`/card.png` 是图本身 |
| `GET /s/{token}`、`/s/{token}/card.png` | 公开的：链接页和预览图 |

## 朋友

app 里「对话 → 朋友」。你的服务器和朋友的服务器直接说话：身份是每台服务器一把 Ed25519 密钥（不是地址），加朋友靠一次性邀请码，两台服务器之间的每个请求都带签名。协议见 [`../docs/social-protocol.zh-CN.md`](../docs/social-protocol.zh-CN.md)；代码是 [`social.py`](social.py)（身份、名片、签名、表、档位）和 [`friends.py`](friends.py)（邀请码、聊天、投递、分享发给朋友、追问）。

- **两边都要有公网地址**：朋友经小服务（[`public.py`](public.py)）的 `/f` 找到你：用 Tailscale Funnel 或反向代理指过去（`tailscale funnel --bg --set-path /f http://127.0.0.1:<share.public_port>/f`），再设 `share.public_url`。没有它、或者没设 `user_name`，app 会说为什么还不能加朋友。反向代理不许改写 `/f` 这一段（路径签在签名里）。
- **邀请码**：一个链接 `<你的地址>/f/i/<令牌>/<你的公钥>`，只能用一次，默认 7 天，服务器只存令牌的哈希。浏览器打开是一页落地页（带给手机扫的二维码和「在 app 里打开」）；app 粘进来，用链接里的公钥核对对方服务器的名片，再签名 `POST /f/hello` 兑换。10 位指纹给两个人打电话时对一下。
- **聊天**：一条消息一个签名的 `POST /f/msg`。发出去的先排进 `friend_messages`，投递循环按朋友依次发，失败重试最多 3 天；改、收回、名字或地址变了、删朋友也都是消息。拉黑 = 对方发的一律收下不存。
- **分享发给朋友**：分享页选朋友，对方收到的是同一份挡过私事的快照（`▇▇▇`，原文不出服务器）。不开链接时这条分享 status = `friends`，`/s/` 打不开。收回分享 = 发出去的每一份都收回。
- **追问和名片 agent**：朋友能对着你分享的东西追问；分享开着追问、对方那一档 `shares` 是 `ask` 时，你的名片 agent（`cardagent.py`，不经 claw，没有工具）按那一档代答，并说明用了什么。每条代答你都看得到：没问题 / 我来改 / 收回。没有名片 agent 时追问等你自己回。档位（亲近 / 朋友 / 同学 / 陌生）和近况在「对话 → 朋友 → 我的名片 agent」（`/api/card`）；健康和世界树哪一档都不给。名片 agent 说出去的每一句先过 **Sentinel**（`sentinel.py`，第 9 步安全底座）：规则 + 另起一次的独立复查，不妥的先扣下、出一张卡等你照发 / 改一下 / 不发，复查不了就换成固定的话（细节见 `docs/a2a.zh-CN.md` 2.4.1）。
- **推送**：朋友的推送是新的推送类型，`server.json` 的 `social.push` 开了才推（`{"message": "ring", "answered": "quiet", "friend": "quiet"}`；`agents` = 你的名片 agent 问过的事、对方本人定了，没写就跟着 `answered`）。朋友发来的照样算未读、算进角标。
- `social.allow_http: true` 只给同一台机器上的测试服用（`http://127.0.0.1:<端口>` 这种地址），真服务器别开。

| 接口 | |
|---|---|
| `GET /api/friends` | 能不能加朋友（`ready`、`why`）、你的名字和指纹、朋友和各自最后一句、未读、没用的邀请码 |
| `POST /api/friends/invites`、`DELETE /api/friends/invites/{id}` | `{note?, tier, days?}` → 邀请链接和二维码（SVG path，只给这一次）；收回没用过的 |
| `POST /api/friends/preview`、`POST /api/friends/accept` | `{code}` → 对方是谁（用链接里的公钥核对过）；`{code, tier, alias?}` → 兑换 |
| `PATCH` / `DELETE /api/friends/{id}`、`POST /api/friends/{id}/block` | 档位、备注；删朋友（会告诉对方）；拉黑 / 解开 |
| `GET /api/friends/{id}/messages?after=`、`POST …/messages`、`POST …/ask`、`POST …/read` | 聊天记录（`recent` 带状态变化）；发；对着对方的一条分享追问；标已读 |
| `POST /api/friends/messages/{id}/review`、`…/revoke`、`…/retry` | 名片 agent 的代答：`{action: ok \| edit \| revoke, text?}`；收回自己发的；重发没送到的 |
| `POST /api/shares/{id}/send` | `{friends, ask, link, text?}` |
| `GET` / `PATCH /api/card` | 档位和近况 |
| `GET /f/card`、`/f/jwks.json`、`/f/i/{token}/{key}`；`POST /f/hello`、`/f/msg` | 公开的（只在小服务上）：签名名片、公钥、邀请落地页；兑换邀请码、投消息（要签名） |

## 代办和 Sentinel 出口（预览）

替你在外面办事（查资料、填表、发信、预订）的 Agent，在 OpenClaw 的 Docker 沙箱里跑，上网只有 Sentinel 一条路：读随便读，要提交、发送、用你的凭证、带你私事的，都在收件箱里等你点「放行这一次」（kind `egress`）。凭证是占位符，出门时才换成真值，只对绑定的网站。不跑 `python3 errand.py setup` 就不生效；分几层锁、怎么判、凭证、回滚都在 [`../sandbox/errand/README.zh-CN.md`](../sandbox/errand/README.zh-CN.md)。模块：`egress.py`（判断、`/api/egress/*`、收件箱钩子）、`egress_proxy.py` + `sentinel_run.py`（代理）、`errand.py`（装、查、凭证）。

## 数据源是可选的

看板要的训练 / 餐食 / 身体 / 日历 / 健康派生指标，各来自 `server.json` 的 `scripts` 目录（默认 `<workspace>/scripts`）里的一个脚本：`xunji.py`（训练 / 餐食 / 身体）、`calendar_ics.py`（日历）、`apple_health.py`（恢复分、热量缺口、体能趋势，还有起没起床：`/api/health/wake` 看手机推到 `/api/health/sleep` 的睡眠分段和 `/api/health/signal` 收到的起床信号）。`memory_tree.py` 提供世界树（「我 → 世界树」）。脚本在就加载，不在就是「还没接」：`/api/health` 的 `sources` 告诉 app 哪些接了，没接的接口回 `ok=false` + `missing_source`，app 的看板显示空状态，其它功能照常。见 [`sources.py`](sources.py)。

这三个脚本目前还是作者自己的数据源（训记、IC 日历、Apple 健康）的形状，正在拆成可选的功能包（packs/）：每个包只声明需要的数据类型，来源由你映射。
