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
python3 agent_ctl.py create --name 睡眠 --purpose "每天早上解读昨晚睡眠。" --icon moon
python3 agent_ctl.py delete g-xxxxxxxx      # workspace 归档到 ~/.openclaw/archive/，不删
```

主 agent 装上 `skills/agent-builder`（见根目录 packs，移植中）就能在对话里建。

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
