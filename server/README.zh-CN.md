# OpenMousse server

**中文** · [English](README.md)

薄 API 层（FastAPI）：认证、对话转发到你的 OpenClaw Gateway、Agent 的创建与删除、看板数据、附件与语音、推送，同时托管网页版。

## 装

一条命令（仓库根目录的 `install.sh`）会做完下面全部，包括 systemd 服务。手动的话：

```bash
cd ~/openmousse/server
pip install -r requirements.txt
mkdir -p ~/.openmousse && cp server.example.json ~/.openmousse/server.json   # 改成你的路径和时区
python3 tokens.py add 手机       # 生成 app 的接入令牌，填进 app 的连接页
python3 run.py                   # 或按 openmousse-server.service.example 装成 systemd 服务
```

`server.json` 每个字段的含义在 [`config.py`](config.py) 顶部。改令牌、加 Agent 不用重启；改监听地址要重启。

## 认证

`/api/*` 要 `Authorization: Bearer <令牌>`（也认 `X-API-Key`；`?token=` 只用于 `GET /api/files/…`，给带不了请求头的图片用）。没凭证返回 401。两个免令牌的口子都默认关：`auth.tailscale_nodes`（Tailscale 设备名白名单，本机要装 tailscale）和 `auth.trust_loopback`（反向代理在本机时不能开）。网页版的静态文件公开。

## 让手机连上

- **Tailscale**（最省事）：服务绑 Tailscale 地址，手机装 Tailscale，app 里填 `http://100.x.x.x:8080`。
- **公网 HTTPS**：`tailscale serve` / `tailscale funnel`，或 Caddy / nginx 反向代理到 127.0.0.1:8080，app 里填 `https://你的域名`。

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

## 数据源是可选的

看板要的训练 / 餐食 / 身体 / 日历 / 健康派生指标，各来自 `server.json` 的 `scripts` 目录（默认 `<workspace>/scripts`）里的一个脚本：`xunji.py`（训练 / 餐食 / 身体）、`calendar_ics.py`（日历）、`apple_health.py`（恢复分、热量缺口、体能趋势，还有起没起床：`/api/health/wake` 看手机推到 `/api/health/sleep` 的睡眠分段和 `/api/health/signal` 收到的起床信号）。`memory_tree.py` 提供世界树（「我 → 世界树」）。脚本在就加载，不在就是「还没接」：`/api/health` 的 `sources` 告诉 app 哪些接了，没接的接口回 `ok=false` + `missing_source`，app 的看板显示空状态，其它功能照常。见 [`sources.py`](sources.py)。

这三个脚本目前还是作者自己的数据源（训记、IC 日历、Apple 健康）的形状，正在拆成可选的功能包（packs/）：每个包只声明需要的数据类型，来源由你映射。
