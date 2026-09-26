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

条目：`{id, kind, source, sourceName, thread, title, why, changes: [], detail, approveLabel, fields?, status, note, result, level, createdAt, updatedAt, decidedAt, expiresAt, messageId}`。`messageId`：Agent 在一次回复里交的条目，回复结束时挂到那条回复（`messages.id`）下面；不是在回复里交的是 null。kind：task / write / send / spend / schedule / push / skill / agent / block / code / calendar / other（exec 只来自 OpenClaw，带 `fields`）；status：pending / approved / rejected / revising / done / failed / withdrawn / expired。旧的 `/api/approvals` 接口还在，给老版本 app。

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

转交卡：`{kind: "handoff", id, thread, messageId, createdAt, status: running / done / error / busy / lost, to, toName, from, fromName, question, seconds, relayId, replyId, error}`。任务卡：`{kind: "task", id, thread, messageId, createdAt, status（进行中 / 完成 / 失败 / 已取消）, timedOut, title, deliverable: [], modelId, minutes, startedAt, finishedAt, tools, step, result, error, round, roundStatus, note, roundResult, tokens, limitMinutes}`。`deliverable` 取任务正文里「要交：」/「Deliverable:」那一行和紧跟着的列表。

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

回复期间写了建议卡（`feed_items` 多了一行、group_id 是这个线程；main 认没挂 Agent 的卡）就推卡片（副标题是卡的类型，正文是「卡标题 · 第一条要点」），否则推回复的开头（去掉 Markdown，按句子截断）。`data` 带 `thread`（老版本 app 只认它）、`target`（`{type: thread | card | inbox | today, …}`）、`level`（算过静默时段后实际用的档位）、`kind`（reply / card / inbox / done / report）。角标 = 收件箱待你点头 + 给你的未读回复。`/api/push/send` 收 `{title, body, thread?, thread_id?, subtitle?, level?, category?, collapse?, target?}`。

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

## 数据源是可选的

看板要的训练 / 餐食 / 身体 / 日历 / 健康派生指标，各来自 `server.json` 的 `scripts` 目录（默认 `<workspace>/scripts`）里的一个脚本：`xunji.py`（训练 / 餐食 / 身体）、`calendar_ics.py`（日历）、`apple_health.py`（恢复分、热量缺口、体能趋势）。脚本在就加载，不在就是「还没接」：`/api/health` 的 `sources` 告诉 app 哪些接了，没接的接口回 `ok=false` + `missing_source`，app 的看板显示空状态，其它功能照常。见 [`sources.py`](sources.py)。

这三个脚本目前还是作者自己的数据源（训记、IC 日历、Apple 健康）的形状，正在拆成可选的功能包（packs/）：每个包只声明需要的数据类型，来源由你映射。
