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

想在 Mac（或者别的电脑）上试一下、不动太多东西：`MOUSSE_TRIAL=1 bash install.sh`（或 `bash install.sh --trial`）。用自带的内核，只听 `127.0.0.1`，不问开公网，不碰 OpenClaw；开机自启的服务可装可不装，不装就在后台先起一次。装完说网页版在哪（`http://127.0.0.1:8080`）、手机怎么连（Tailscale，或者同一个 Wi-Fi 配 `MOUSSE_BIND`）。macOS 上安装器找 Homebrew 的 Python 3.11 以上（系统自带的是 3.9），git 用 `xcode-select --install` 装，服务交给 launchd。每次安装把放了什么（服务、软链、目录、改过的文件、后台起的进程和怎么停它）记进 `~/.openmousse/install-manifest.json`，卸载照它删。已经正式装过的机器上试装什么都不改，直接退出。

`server.json` 每个字段的含义在 [`config.py`](config.py) 顶部。改令牌、加 Agent 不用重启；改监听地址要重启。用自带的内核（不装 OpenClaw：`bash install.sh` 时答 `builtin`）的，模型、搜索、记忆在服务器的终端里跑 `python3 setup_ctl.py` 选（见[选模型、搜索和记忆](#选模型搜索和记忆自己的内核)）。`python3 settings_ctl.py user-name <称呼>` 改给模型的说明里怎么称呼你（`user_name`，不用重启），档案 USER.md 也记一行；新手带路时主对话会调它。

## Docker

在仓库根目录 `docker compose -f docker/compose.yaml up -d --build`：一个容器跑服务器和自带的内核，会变的都在卷 `/data` 里（[`docs/docker.zh-CN.md`](../docs/docker.zh-CN.md)）。第一次起来写 `/data/server.json`（claw `builtin`、令牌随机，名字、语言、时区读 `MOUSSE_NAME`、`MOUSSE_LANG`、`MOUSSE_TZ`），建好世界树，照 `MOUSSE_PUBLIC_URL` 打印配对链接；以后再起来不动它。端口只开在宿主的 `127.0.0.1`，手机经宿主的 Tailscale 进来。容器里没有 Docker 沙箱，也没有 Claude Code / Codex 命令行。`MOUSSE_INSTALL=docker` 让 `mousse update` 改成叫你拉新镜像。

## 运维：`mousse doctor`、备份、更新、配置、安全检查和清理

在服务器的终端里日常照看（[`ops/`](ops/)，一个命令一个模块；`bin/mousse` 跑它们，软链进 PATH：`ln -s ~/.openmousse/repo/bin/mousse ~/.local/bin/mousse`）。服务器没开着也能跑，不打印令牌和密钥，不读、不存、不传任何订阅令牌：Claude Code、Codex 登没登录照 [`engine/runners.py`](engine/runners.py) 起它们自己的命令查。

- **`mousse doctor [--json] [--fix] [--yes] [--strict]`**（[`ops/doctor.py`](ops/doctor.py)）：体检，一节一节，每一项 ✓ / ! / ✗ / ·、一句话、怎么修。系统（Python 3.11 以上、`requirements.txt`、磁盘剩余、沙箱的 Docker）、安装（`~/.openmousse/repo`、venv、`mousse` 在不在 PATH 上）、`server.json` 和 `claw` 段、设置总览的每一层（模型、搜索、记忆、向量、连接、手）、命令行登录（Claude Code、Codex、ChatGPT 套餐）、数据（数据目录、`secrets.env`、`server.json`、ChatGPT 套餐的凭据目录、世界树配置的权限；SQLite 的 `quick_check`；记忆检索的 FTS 索引；过期的配对码）、世界树、服务（systemd / launchd、每晚日结、`/api/health`、跑的代码和仓库对不对得上）、上一次检查更新的结果。`--fix` 只修撤得回、安全的：收紧权限（0600 / 0700）、补建缺的目录、重建 `~/.openmousse/repo` 软链、重建 FTS 索引（正文不动）、清过期的配对码（先备份 `server.json`，备份名 `server.json.bak-<时间>`）；修完那一项再查一遍。要停、要重启服务的还要加 `--yes`（照 `safe_restart.py` 先等进行中的回复说完），没有就只说要跑什么。`--json` 照 OpenClaw 的 health contract：每一项 `id`、`section`、`status`（`ok` / `warn` / `error` / `info`）、`title`、`detail`、`fix`、`fixable`、`needsYes`、`fixed`、`note`。没有 ✗ 退出码 0（`--strict` 时有 ! 也算 1），否则 1。`check.sh` 留着给 venv 本身坏了的时候：它只用系统的 python3。
- **`mousse status [--json]`**（[`ops/status.py`](ops/status.py)）：一屏摘要：服务器在不在、版本和 commit（跑的是不是比仓库旧的代码）、claw、主对话的模型、今天几轮花了多少、定时任务几条、收件箱几张在等、有没有新版本（只读上一次 `mousse update --check` 存下的结果，不联网）。服务器没在回话时退出码 1。
- **`mousse logs [-f] [-n 200] [--since 1h] [--grep 词] [--unit server|daily-close|tree]`**（[`ops/logs.py`](ops/logs.py)）：Linux 的 systemd 读 `journalctl --user -u openmousse-server`；macOS 和自己起的服务读 `~/.openmousse/logs/<名字>.log`（这些行没有时间，`--since` 只有 journalctl 认）。每一行先过内核的打码，再抹掉这台机器上真的令牌和密钥，才打出来。

- **`mousse backup create [--out 文件或目录] [--no-secrets] [--no-workspace] [--no-tree] [--keep N]`**、**`mousse backup verify 文件`**、**`mousse backup list`**、**`mousse restore 文件 [--dry-run] [--yes]`**（[`ops/backup.py`](ops/backup.py)）：打一个 `openmousse-backup-<日期-时间>.tar.gz`（0600），默认放 `server.json` 旁边的 `backups/`；`--keep` 留最近几份（默认 5）。里面有 `server.json`、`secrets.env`、整个数据目录（SQLite 用 sqlite3 的 `backup()` 在线拷，不直接复制正在写的文件；`-wal`、`-shm`、锁和日志不要）、`server.json` 把库或上传的文件放到外面时的 `db` / `uploads`、世界树的家（Markdown 笔记文件夹在外面的也带上）、各 Agent 的工作区（软链只记下指向哪，不跟进去）。`--no-secrets` 不带 `secrets.env` 和数据目录里也算密钥的（`chatgpt/`、`apps/`、`sentinel/`、`gateway-device.json`、`social/identity.json`）；`server.json` 里照样有接入令牌，所以每个包都要像密钥一样收好。`manifest.json` 记格式版本、时间、版本、commit、带没带密钥、每一块原来在哪（家目录下的记成 `~/…`，换机器、换用户名也能恢复）、每个文件的大小、sha256、权限。`verify` 查：只有一个根目录、只有普通文件、没有绝对路径和 `..`、manifest 列的文件都在且大小和 sha256 对得上、没有多出来的、每个库 `PRAGMA quick_check` 过。`restore` 先 verify，不过一个字节都不写；动手前给现在的样子做一份快照 `openmousse-snapshot-pre-restore-<时间>.tar.gz`（`mousse restore <快照>` 就撤回了）；服务在跑就不动，加 `--yes` 才替你停（恢复世界树时连世界树的服务一起）、恢复完再起，没有 `--yes` 退出码 2；先解到临时目录、每个文件再对一遍 sha256，再一个一个换上去，换库之前删掉它旧的 `-wal` / `-shm`。包里没有的文件留着；不经指到外面去的软链往外写。`mousse update` 动手前的快照也是这一套。

- **`mousse update [--check] [--plan] [--channel stable|main] [--yes] [--stash]`**（[`ops/update.py`](ops/update.py)）：两个通道：`stable` = 远端最新的 `v<VERSION>` tag（发版时打的，见 [`version.py`](version.py)），`main` = 远端的 `main`。`--channel` 选了就记进 `server.json` 的 `update.channel`（默认 `stable`）；`update.remote`（默认 `origin`）、`update.wait`（等服务回话最多几秒，默认 90）可写可不写。不能更就停下说清楚（退出码 2）：Docker 装的（`MOUSSE_INSTALL=docker`：拉新镜像、重建容器）、不是 git 装的（重跑安装命令）、有没提交的改动（`--stash` 替你藏进 `git stash`）、现在的 commit 不在通道的历史里（在跑别的开发分支：不硬切）。比通道还新的不算有更新，从不往回退。安装器是浅克隆：先补全历史再比。`--check` fetch 后比一比，结果存下来给 `mousse status`、`doctor` 看（它们不联网）；`--plan` 列出中间的提交、依赖变了什么、会重启哪些服务，不动手。真更新在终端里问一句（或者 `--yes`）：先快照（`mousse backup` 那一套）→ 切到目标 → 用 venv 的 python 装 `server/requirements.txt` 和 `./tree` → `python -m compileall -q server`（不过就回到原来的 commit，依赖也装回去）→ 等进行中的回复说完再重启服务（没有服务管理器就说要跑什么）→ 等 `/api/health` 通，不通就回滚（原来的 commit、依赖、再重启）。全程写日志：`server.json` 旁边的 `update/logs/update-<时间>.log`（打过码），最后一次的回执在 `update/last.json`。不自动更新、不在后台定时检查，不改 `VERSION`、不打 tag。`safe_restart.py` 在 Mac 上也会经 launchd 重启了。

- **`mousse config validate [--json] [--file 路径]`**、**`config get <点路径>`**、**`config set <点路径> <JSON 值>`**、**`config unset <点路径>`**（[`ops/confcheck.py`](ops/confcheck.py)）：整份 `server.json` 对一张「已知的键 → 类型和取值」的表（照 [`config.py`](config.py)、[`engine/models.py`](engine/models.py) 开头和各 engine 模块开头的说明写，先覆盖顶层键和 `engine` 段的主要键）。不认识的键只警告、不算错：别的工作一直在加新键，新版本的配置拿给老版本也要能起。类型不对、取值不在范围里才算错（退出码 1）。`get` 把像密钥的一律打码（`auth.tokens` 整个），`secrets.env` 里的永远不给。`set` 现读文件（读不了的绝不拿空的写回去）、改一份拷贝、整份校验，不过就不落盘，过了先备份（`server.json.bak-<时间>`）再原子写；值不是 JSON 就当字符串（`mousse config set engine.model moonshot/kimi-k3`）。密钥、令牌不给 set：API 密钥用 `setup_ctl.py add`（进 `secrets.env`），接入令牌用 `tokens.py`。`mousse doctor` 的 `config.valid` 就是它；`setup_ctl.py` 写完以后有 ✗ 也会提一句（照旧写，不拦）。

- **`mousse security audit [--json] [--fix]`**（[`ops/audit.py`](ops/audit.py)）：照 OpenClaw 的安全体检，冷检查配置和文件：每一条有编号、轻重（✗ 危险、! 要留意、· 提一句）、危险在哪、怎么改。查：`secrets.env`、`server.json`、数据目录、ChatGPT 套餐的凭据、连接器的令牌（`data/apps`）、Doorman 的密钥（`data/sentinel`）、设备私钥和朋友认你的钥匙、世界树的配置、备份包的权限；`server.json` 里直接写着的密钥（服务商的 `key`、claw 的 `token`、MCP 服务 `env` 里不是 `${变量}` 的）；太短的、两个名字同一把的接入令牌（程序的令牌和人的一样 = 危险：拿着它的程序也能走 `/api`）；`auth.trust_loopback`；监听地址不是本机也不是 Tailscale（`0.0.0.0` = 危险）；Funnel 把 `/api` 或整个服务开到了公网（有 `tailscale` 命令才查；安装器开的 `/t`、`/m`、`/s`、`/f` 不算）；`engine.api` 让调用方当场点头；`engine.host_exec` 里的 `security: full`；`engine.tools.levels` 把本来要点头的改成了 `auto`；MCP 服务里写工具设成 `auto`；ChatGPT 套餐的「后台也能花」；沙箱不经 Doorman 直接上网（`host` = 危险）；过期的配对码。`--fix` 只收紧权限、清过期的配对码（先备份 `server.json`）。还有危险的那一条就退出码 1。

- **`mousse sessions cleanup [--older-than 90d] [--agent ID] [--dry-run] [--yes] [--no-vacuum] [--json]`**（[`ops/sessions.py`](ops/sessions.py)）：清掉自己内核的旧会话记录（模型那边看的逐字记录：`engine_sessions` 和它的 `engine_entries`），最后一次动它在 `--older-than` 以前的（默认 90 天；至少 `1d`，`12w`、`6m`、`1y` 也认），清完 `VACUUM`。app 里的聊天记录、活动页、用量和花费、记忆、世界树、工作区都不动。今天的、有一轮还在跑的、有卡在等你点头的、后台任务没跑完或者结果还没交回去的、定时任务正在跑的，还有（写回开着时）最近 3 天里还没写回记忆的主人会话一律不清，按原因数出来。对外接口的会话表里会话记录都清光了的那几行一起去掉，有攒着的点头结果的留着。先列出来（`--dry-run` 到此为止），没写 `--yes` 就问一句（没有终端又没 `--yes` = 拒绝，退出码 2）；动手时在一个写事务里再算一遍。`VACUUM` 遇到服务在跑一轮最多等一分钟，等不到就跳过、说一声。不建表、不改表。

- **`mousse owner migrate [--to owner] [--apply] [--db 路径] [--json]`**（[`ops/owner.py`](ops/owner.py)，2026-10-20）：日程、目标、项目、饮食这几张表里，你自己改的东西记成一个 id，就是 `server.json` 的 `owner_id`。新装的写 `"owner"`；没有这个键就是 `"leo"`（老的安装一直用的值），所以已有的安装一个字节不变。读的时候两个值（和 `owner_id` 写的那个）在服务器和 app 里都算你自己，不迁也照样能用。这个命令只是为了让库里只剩一个值：默认只列出哪张表、哪一列、多少行会改；`--apply` 先查写上 `owner_id` 以后 `server.json` 校验得过，备份数据库（放 `backup_dir`，默认 `<data_dir>/backups/`）和 `server.json`，一个事务里换掉，写上 `owner_id`，再扫一遍。`--to leo` 是往回换。改动记录里存的改动前后的快照、世界树的叶子不动。不在任何地方自动跑。

- **`mousse diagnostics [--out FILE.zip|目录] [--print] [--lines 2000]`**（[`ops/diagnostics.py`](ops/diagnostics.py)）：打一个给帮你看问题的人用的 zip（0600），默认放在 `server.json` 旁边的 `diagnostics/`（`--print` 不写文件，打到终端）。里面有 `doctor`、`status`、`security audit`、`config validate` 查出来的（只查，不修）；`server.json` 的样子（键都在，值只留安全的那几种：模型引用、claw 的种类、语言、时区、开关和数字；别的字换成 `<str:12>`、`<url>`、`<set>` 这样的记号，`auth.tokens` 只留名字）；内核的模型、备用、别名、各自有没有密钥、正在冷却的；每个 SQLite 库多大、`quick_check`、每张表几行；最近 7 天用量的加总；上一次检查更新的结果；还有日志最后几行里的信号（按 `[模块]` 和级别数、HTTP 状态按类数、报错的类型和出在哪几行代码）。日志原文一行都不放：里面可能有对话的片段。对话、消息、记忆、密钥、令牌、邮箱一律不放：每一份都过一遍打码（像密钥的字、这台机器上真的令牌和密钥），邮箱换成 `<email>`，路径里的家目录和用户名换成 `~`；最后再查一遍整包，还带着真的令牌、密钥、邮箱的那一份不放。

- **`mousse uninstall [--full] [--keep-tree] [--no-backup] [--backup-to DIR] [--dry-run] [--yes] [--json]`**（[`ops/uninstall.py`](ops/uninstall.py)）：默认只拿掉跑起来的那几样，数据一样不动：服务（systemd 的单元停下、关掉开机自启、删掉，再 `daemon-reload`；launchd 的先 bootout 再删 plist；自己起的停掉）、crontab 里跑日结的那几行（别的行不碰）、软链（`~/.openmousse/repo`、`~/.local/bin/mousse`、各工作区 `skills/` 里指到仓库的）。`--full` 再删掉 `~/.openmousse`、世界树的家（`--keep-tree` 留着）、安装器放的那份仓库（`~/openmousse`，要没有没提交、藏着、没推上去的改动才删）。先警告（列出每一样、多大），先备份（同 `mousse backup create`，放在家目录或 `--backup-to`，不能在要删的里面；`--no-backup` 不打；备份没打成就什么都不删），要你打出 `delete` 才动。家目录外面的、路径里没有 `mousse` 的一律不删；Obsidian 库、OpenClaw 自己的设置和规矩（说一声还剩什么）、Tailscale（只打印关掉 `/t`、`/m`、`/s`、`/f` 的命令）都不碰。安装器写了 `~/.openmousse/install-manifest.json` 的话也照它来（`services`、`links`、`paths`、`repo`、`cron`、`funnel`），安全栏一样。Docker 装的用 Docker 删。

认不认 systemd 照安装器（`/run/systemd/system` 在才算）；环境变量 `MOUSSE_SERVICE_MANAGER=systemd|launchd|none` 能直接指定。

## 认证

`/api/*` 要 `Authorization: Bearer <令牌>`（也认 `X-API-Key`；`?token=` 只用于 GET 文件：`/api/files/…`、思考里的附件和收藏的原件、播客的原声，给带不了请求头的图片和网页版的 `<audio>` 用）。没凭证返回 401。两个免令牌的口子都默认关：`auth.tailscale_nodes`（Tailscale 设备名白名单，本机要装 tailscale）和 `auth.trust_loopback`（反向代理在本机时不能开）。网页版的静态文件公开。`mcp`、`mcp-<agent id>` 令牌只能用在 `/mcp` 上（`/api` 回 403：只拿着它的 claw 不能替你点收件箱的卡、配对新设备），`sentinel` 只能用在 `/api/egress`，`api`、`api-<agent id>` 只能用在 `/v1`（自己内核的 OpenAI 兼容接口和 ACP 适配器，`engine.api` 开了才有；见 [`engine/openai_api.py`](engine/openai_api.py)、[`engine/acp.py`](engine/acp.py)）；本机脚本（`*_ctl.py`、`safe_restart.py`、`check.sh`）用第一把不是这几种的令牌，和 `server.json` 里的顺序无关。

## 让手机连上

- **Tailscale**（最省事）：服务绑 Tailscale 地址，手机装 Tailscale，app 里填 `http://100.x.x.x:8080`。
- **配对码**（不用抄令牌）：`python3 tokens.py pair` 出一个一次性配对码（[`pairing.py`](pairing.py)：10 分钟、只能用一次、只存哈希、猜错多了锁 10 分钟），终端里画二维码，旁边是 `openmousse://pair?s=<地址>&c=<码>` 链接。手机相机扫码或点链接 → app 的连接页填好地址和码 → 点「使用配对码连接」→ `POST /api/pair` 换一把新令牌（`device-<设备>-<时间>`，能 `tokens.py remove`）。`--json` 给 claw 读（见 [`docs/connect.zh-CN.md`](../docs/connect.zh-CN.md)）。`/api/pair` 在 `/api` 下，只在 Tailscale 私网里。安装器装完会直接打一个（30 分钟）。
- **设备**（app：设置 → 你的 claw）：`GET /api/devices` 列出人用的令牌（只回名字；`mcp`、`mcp-<id>`、`sentinel` 这类给程序用的不列），`DELETE /api/devices/{name}` 收回一台（不能是发请求的这台），`POST /api/pair/new` `{server, name}` 让已经连着的 app 给另一台设备出配对码（链接 + 二维码的 SVG path，规矩和 `tokens.py pair` 一样）。
- **公网 HTTPS**：`tailscale serve` / `tailscale funnel`，或 Caddy / nginx 反向代理到 127.0.0.1:8080，app 里填 `https://你的域名`。

## 设备节点（自己的内核）

节点 = 你自己的一台设备，替 Agent 做服务器做不了的事：在你的 Mac 上跑命令、截屏，读写你从它那里给出来的文件夹，弹通知，拿手机的位置、用手机拍照。照 OpenClaw 的节点做（先配对设备，再单独批准它报上来的命令面），传话换成长轮询：节点总是自己连服务器，Mac 和手机上不开端口。见 [`nodes.py`](nodes.py)。

- **默认关。** 要自己的内核，并且在 `server.json` 的 `engine` 里写 `"nodes": {}`；不写的话 `/api/nodes/*` 一律 404，哪个 Agent 都没有 `nodes` 工具。`engine.nodes.agents` 是给哪些 Agent 这个工具（默认 `["main"]`）。
- **配一台电脑**：`python3 tokens.py pair --node`（或者 app 的「设置 → 硬件 → 设备和节点 → 添加一台电脑」，`POST /api/nodes/pair`）出一个一次性的码（10 分钟、一次），打印在那台电脑上要跑的命令。节点主机拿它换一把 `node-<设备>` 令牌（`POST /api/pair` 带 `"node": true`；手机的码和节点的码不混用）。这把令牌只能用在 `/api/nodes/*`（别处 403：拿着它的电脑读不了你的对话、替你点不了头），不算「设备」，本机脚本也不会拿它。
- **批准它能做什么**：节点连上来（`POST /api/nodes/hello`）报它的平台、命令、系统权限、给出来的文件夹，服务器交一张收件箱卡（kind `node`），你点了同意，Agent 才能用这些命令（记在 `engine.nodes.<节点>.approved`）。之后它报得更多，只有多出来的再交卡；这期间批过的照样能用。拒了的同一组 30 天里不再问；app 的节点页上能直接批。手机不一样：app 用它自己的设备令牌，在 app 里打开某一项就是批准，不交卡。
- **隐私的命令先关着**：跑命令、写文件、截屏、拍照、定位、操作电脑，批了命令面也是关着的，每个节点逐项开：`engine.nodes.<节点>.allow` = `{"<命令>": "off" | "ask" | "allow"}`（`ask` = 每次一张卡，`allow` = 直接给）。`system.run` 的 `allow` 是这个节点的放行名单（`engine.nodes.<节点>.exec`）里的直接跑、别的问，危险的每次都问；`computer.act` 最多 `ask`。别的批过的命令（通知、看和读给出来的文件夹、查程序）默认 `allow`，也能设成 `off`。app 用 `POST /api/nodes/{id}/allow {command, level}` 改。
- **传话**：节点长轮询 `GET /api/nodes/poll?wait=20`，拿到 `{invokes: [{id, command, params, timeoutMs}], cancel: [...]}`；做完 `POST /api/nodes/result {id, ok, payload | error: {code, message}}`，要走了（手机切到后台、节点主机关了）`POST /api/nodes/bye {reason}`。服务器不等不在的节点：不在线、手机 app 不在前台、命令没报 / 没批 / 关着，马上报错，带稳定的 code（`NODE_OFFLINE`、`NODE_BACKGROUND_UNAVAILABLE`、`NODE_COMMAND_NOT_APPROVED`、`NODE_COMMAND_OFF`……）。拿到活以后断了的报 `NODE_LOST`；到点了、这一轮被停了，下一次长轮询带上 `cancel`。
- **管节点**（只认你自己的设备）：`GET /api/nodes`、`GET /api/nodes/{id}`、`POST /api/nodes/{id}/allow`、`POST /api/nodes/{id}/approve`、`DELETE /api/nodes/{id}`（忘掉这个节点、它的批准和各项的档，电脑的 `node-…` 令牌一起收回）。
- **`nodes` 工具**（[`engine/nodes.py`](engine/nodes.py)）：只在你自己的回合、只给 `engine.nodes.agents` 里的 Agent（外人回合照旧零工具，赛博回合没有；单独开会话跑的定时任务和心跳、`mousse agent`、后台的子 Agent 也没有）：`list`、`describe`、`notify`、`camera_snap`、`location_get`、`screen_snapshot`、`run`、`which`、`file_list`、`file_read`、`file_write`、`computer`。`run` 照 `host_exec` 按这个节点的放行名单：名单里的直接跑；名单外的交两张卡（只这一次 / 以后都放行，后者把这一条原样写进 `engine.nodes.<节点>.exec`）；危险的（同一个 `danger()`）每次都问、没有「以后都放行」。写文件、拍照、截屏、定位照你开的档：`allow` 直接做，`ask` 交卡。就算开了 `allow`，节点上的事也只在你自己在对话里说的那一轮直接做：定时、心跳、触发、工作流、对外接口引出的一轮，还有卡同意后接着做的那一轮，`allow` 的那几样（名单里的命令、读写文件、通知、拍照、截屏、定位）都不做；`ask` 照旧交卡。拍照、截屏点了同意是「放行一次」：10 分钟里照原样再调一次才拍，图回到问它的那一轮里。`computer` 每次都问。给手机的通知走推送，app 在后台也收得到。节点回来的东西（命令输出、文件内容、它说没做成的那一句）都包成外来的资料，它自己报的名字只留字、数字、空格和 `.'-`（工具说明里只列 id）；图存进图库，会话里只记引用。`engine.tools.levels.nodes = "off"` 去掉这个工具。

```json
"engine": {"nodes": {"agents": ["main"],
                     "node-mac-mini": {"approved": ["system.notify", "system.run", "fs.list", "fs.read"],
                                       "allow": {"system.run": "allow"}, "exec": ["/usr/bin/uname", "git status ..."]}}}
```

- **Mac（或 Linux）上的节点主机**（[`node_host.py`](node_host.py)）：一个文件、只用标准库，macOS 自带的 `python3`（3.9）就能跑；拷到那台电脑上，或者装了 OpenMousse 的地方用 `mousse node …`。`pair '<链接>'` 把令牌存进 `~/.openmousse-node/node.json`（0600），`run` 前台跑，`install` / `uninstall` 让它跟着登录起来（macOS 的 launchd `ai.openmousse.node`，Linux 的 systemd 用户服务 `openmousse-node.service`；没登录也要跑就 `loginctl enable-linger`），`status` 看它报了什么、服务器回不回话。断了按退避重连，令牌被收回就干净地停下（退出码 0），停的时候先说 `bye`。它报什么由它自己的 `~/.openmousse-node/config.json` 定，这是第二道门，服务器批过的也得过它（照 OpenClaw 的节点本机审批）：

  ```json
  {"allow": ["/usr/bin/uname ...", "git status"],
   "folders": {"Documents": "~/Documents", "Code": {"path": "~/code", "write": true}},
   "notify": true, "screen": false, "computer": false}
  ```

  `system.run` 只跑 `allow` 里的命令（写法同 `host_exec`；路径按那台电脑上找到的真实路径比），不经 shell，去掉像密钥的环境变量，有时限，输出留头尾，到点或被取消整个进程组收掉。`fs.list` / `fs.read` / `fs.write` 只在 `folders` 里（路径从文件夹名起；不许 `..`、不跟符号链接、`.env`、`.ssh` 这类像密钥的不碰；只在 `write` 为 true 的文件夹里写，`allow` 会跑到的脚本和它所在的子文件夹不写，免得 Agent 先改写名单里的脚本再跑它。可写的文件夹别给 `make`、`npm`、`pytest`、`python3 …` 这类会读当前目录文件的程序开名单：它们读的文件这里认不出来）。`screen.snapshot` 在 macOS 上用 `screencapture -x`（要给那个 Python「屏幕录制」权限），Linux 上用 `grim` / `gnome-screenshot` / `scrot` / `import`；`system.notify` 用 `osascript`（标题和正文当参数传，不拼进脚本）或 `notify-send`。`computer.act`（只有 macOS；`computer` 和 `screen` 都要开着，还要给跑节点主机的程序「辅助功能」权限：自己跑就是终端，装成服务就是那个 `python3`）：单击、双击、右键、移过去、滚动、按键（`cmd+s`、`enter`、`pagedown`……）、打字。鼠标和按键是 CoreGraphics 事件，经 `osascript` 的 JavaScript 发，坐标当参数传；打字走剪贴板（`pbcopy` 再按 ⌘V，打完把剪贴板里原来的字放回去）。`x` / `y` 是 10 分钟内最近一张第 0 块屏幕截屏里的像素，按截那张时量的屏幕大小换算成点；没有这样一张就回 `COMPUTER_NEEDS_SCREENSHOT`，没给辅助功能权限回 `PERMISSION_MISSING`。服务器那边每个动作都交卡。改了这个文件不用重启。

- **手机当节点**（app 的「设置 → 硬件 → 这台手机」；定位要 1.0.7 起的安装包，带 expo-location）：三样，默认都关，打开前先说清楚能做什么。定位（只要「使用期间」的权限，不在后台定位；「直接给」或「每次先问」，每次读取都记在那一页）、拍照（每次在手机上弹出全屏取景，你点了才拍）、通知（走推送，app 在后台也收得到）。app 用自己的设备令牌，只在前台长轮询，切到后台先说 `bye`，Agent 那边马上收到 `NODE_BACKGROUND_UNAVAILABLE`。「设置 → 硬件 → 设备和节点」列出所有节点：批准等着的、改每一项的档、移除节点、配一台电脑。

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

服务器在 `/mcp` 开了一个 MCP 入口（Streamable HTTP，无状态，[`mcp_bridge.py`](mcp_bridge.py)），claw 经它用看板、收件箱、目标、项目、日程、Agent、后台任务额度、日结提案、日志、转给 Agent、称呼、世界树、页面。沙箱里的 Agent、换了 docker / ssh 终端后端的 Hermes、云上托管的 claw 跑不了 skill 里的命令，走这里就行。

- 地址：`http://<服务地址>/mcp/<令牌>`（令牌放在路径里），或者 `/mcp` + `Authorization: Bearer <令牌>`。令牌是 `server.json` 的 `auth.tokens` 里名字为 `mcp` 的那个（安装器生成；`python3 tokens.py add mcp` 也行），别的令牌 `/mcp` 不认。名字是 `mcp-<agent id>` 的令牌绑一个 Agent，只能替它做事。
- Host 检查（防 DNS 重绑定）：`/mcp` 只回 `Host` 是本机（`127.0.0.1`、`localhost`、`[::1]`，任何端口）、`bind` 里的地址、`share.public_url` 的主机，或者写在 `mcp.allowed_hosts` 里的请求（`["desk.example:8080", "https://mcp.example.com"]`，每次请求现读，不用重启）。别的回 421；浏览器带的 `Origin` 对不上回 403。另一台机器上的 claw 用 Tailscale 地址或名字连监听在通配地址（`0.0.0.0`）上的服务，或者反向代理把公网的 `Host` 原样传过来，都要把那个主机写进这个名单。
- 工具：每个是一条现成命令的桥，写法和 skills 里的命令一模一样：`args` = 脚本名后面的词（一个词一项，不经 shell，不用加引号），`input` = 本来要从标准输入给的，`agent` = 你是哪个 Agent（命令就在它的工作区里跑，看板、收件箱按它认；别的 claw 的 Agent 没有工作区也认得）。每次起一个子进程（约 0.1 秒），同时最多 4 个，单次最长 120 秒（handoff 300 秒）。
- OpenClaw：安装器在 `openclaw.json` 的 `mcp.servers` 里加 `openmousse`（Gateway 热加载，不用重启），工具名是 `openmousse__board` 这种。skills 里的命令照旧能用，两条路并存，skill 开头写了有工具就用工具。
- 别的 claw：安装完打印地址和它那家怎么加（Hermes 的 `mcp_servers`、nanobot 的 `tools.mcpServers`、Letta Code 的 `/mcp add`）。Agent 那段说明里带着它的 id，调工具时填进 `agent`。
- `server.json` 的 `mcp.scripts` 可以换掉或加一个工具背后的命令（`{"journal": ["python3", "~/…/my_journal.py"]}`，写 `null` = 不提供），改了要重启服务。
- 你连上的应用（见[连接器](#连接器经-mcp-接进来的应用)）的工具接在这些后面，名字是 `<应用 id>__<工具名>`；连上、删掉、改权限都不用重启。
- 用自己的内核时，`/mcp` 还认内核发给本机用户自己登录的 Claude Code / Codex 的临时令牌：一轮或一个后台任务一把，只在内存里，完了就收回，不进 `server.json`。拿着它看到的是这个 Agent 在内核里的工具（模型选 `claude-code/…` 时、后台任务的外援），走内核自己的关卡，不是上面这些。见 [`engine/mcp_engine.py`](engine/mcp_engine.py)、[`engine/claude_brain.py`](engine/claude_brain.py)。
- 每轮的上下文：11 个工具的定义约 7,500 字，跟 11 个 skill 的描述差不多。
- 经 `/mcp` 跑的脚本请求服务器时带 `X-Mousse-Via: mcp`（环境变量 `MOUSSE_VIA`），所以外面的 MCP 客户端引出来的转交、触发不会花你的 ChatGPT 套餐（见下）。

## 用你自己的 ChatGPT 套餐（自己的内核）

用自己的内核时，模型写 `chatgpt/<型号>` 就用你自己的 ChatGPT Plus / Pro 套餐跑：走 OpenAI 官方给开源 / 本地应用的 [Sign in with ChatGPT](https://developers.openai.com/siwc/token-sharing-open-source) 那条路，调公开的 Responses API（`https://api.openai.com/v1/responses`）。见 [`engine/chatgpt_plan.py`](engine/chatgpt_plan.py)。

- 连接：`POST /api/engine/chatgpt/login` 开始授权，回授权网址 `url`；服务器先在 `http://127.0.0.1:<端口>/auth/callback` 起好监听。在任何浏览器里打开、同意：在服务器本机（或者开了 `ssh -N -L <端口>:127.0.0.1:<端口> …`，见 `tunnel`）浏览器直接落到监听上，就连好了；手机上那一页打不开，把地址栏里的整串复制下来，`POST /api/engine/chatgpt/callback {"url": "…"}` 交回来。换令牌、PKCE 的 verifier、令牌都只在服务器上。
- 状态：`GET /api/engine/chatgpt`（`/api/engine/status` 的 `chatgpt` 也是）说连没连、哪个套餐、邮箱、令牌快过期没有、最近一次的错、后台开关，从不回令牌。`POST /api/engine/chatgpt/disconnect` 撤销并清掉令牌。同一时间只连一个账号：连另一个会先撤销旧的。
- 令牌只存在这台服务器的 `<data_dir>/chatgpt/`（目录 0700、文件 0600、原子写）。快过期先刷新，同时只刷一次；被拒了强制刷新一次，还被拒就在状态里提示重新连。
- 模型：连上、同意用套餐以后，你账号模型目录里的 `chatgpt/<slug>` 出现在 app 里；`server.json` 的 `engine.chatgpt.models` 可以指定；`"chatgpt": false` 全部关掉。
- 谁能花：只有你自己的回合，和你自己发起的一次性调用（见下面「用你自己的 Claude 套餐跑一次性调用」）。赛博、朋友和别家的 agent（名片 agent、A2A、Doorman）、OpenAI 兼容的 `/v1` 和 ACP、经 `/mcp` 引出来的、没报是你的一次性调用，都换成用 API 密钥的模型。后台任务、定时任务、心跳要你明确同意一次才花：`PUT /api/engine/chatgpt/background {"allow": true}`（记下同意的时间）。
- 普通的 OpenAI 密钥也能走 Responses API：`engine` 里写 `"providers": {"openai": {"api": "openai-responses"}}`；没写 `api` 的照旧走 `/chat/completions`。

## 用你自己的 Grok 订阅（自己的内核）

用自己的内核时，模型写 `grok/<型号>` 就用你自己的 SuperGrok / X Premium+ 订阅跑，不要 API 密钥、不按量付费。这不是 xAI 点名支持的接入：xAI 公告里点名能用订阅登录的第三方 agent 是 Hermes、OpenClaw、OpenCode、Kilo Code、Warp 这几家，OpenMousse 不在里面；这里借的是它们共用的 OAuth 设备码登录入口（同意页上可能写着「Grok Build」）。xAI 随时可能不让这样用（回 403）：被拒了就改用 API 密钥（见下面）。服务器拿你的令牌调 xAI 的公开接口（`https://api.x.ai/v1`，Responses API）。见 [`engine/grok_plan.py`](engine/grok_plan.py)。

- 登录：在服务器上跑 `python3 setup_ctl.py grok login`，它打出一个网址和一个码；在手机或电脑上打开网址、认一下码、点同意，终端里自己就好了。app 或者脚本也能这样做：`POST /api/engine/grok/login`（回 `url`、`code`、`expiresAt`）、`GET /api/engine/grok/login`（进行到哪）、`DELETE /api/engine/grok/login`（取消）。不要回调页、不要 SSH 转发：服务器自己去问 xAI，等你点同意。
- 令牌只存在这台服务器的 `<data_dir>/grok/`（目录 0700、文件 0600、原子写），不进日志、不进网址、不回给 app。快过期先刷新（寿命的六分之一，夹在 2 分钟到 1 小时之间），同时只刷一次。`python3 setup_ctl.py grok logout` 或者 `POST /api/engine/grok/logout` 到 xAI 那边撤销、再删掉本机这一份；xAI 没确认撤销的话，回的话里会让你去 [accounts.x.ai](https://accounts.x.ai) 撤销。
- 状态：`python3 setup_ctl.py grok`、`GET /api/engine/grok`（`/api/engine/status` 的 `grok` 也是）：登没登录、邮箱、令牌快过期没有、要不要重新登录、xAI 拒没拒这个账号、最近一次的错、后台开关、型号、这一段的额度，从不回令牌。这几个接口只认你自己的设备（带 `X-Mousse-Via` 的脚本一律 403）。
- xAI 不给的时候：这条登录不是 xAI 点名支持 OpenMousse 的接入，哪些账号、哪些应用能用由 xAI 定，有的 SuperGrok 账号会被回 403。这时登录以「被拒」结束、说清楚原因，重新登录没用。改用 API 密钥：在 [console.x.ai](https://console.x.ai) 拿一把，存成 `XAI_API_KEY`（设置 → 模型 → xAI），模型选 `xai/<型号>`（按量计费，和订阅额度是两本账）。只有某一样（搜索、生图、朗读……）回 403：说的是订阅里可能没有这一样，别的照常用。
- 别的错：登录过期、在 xAI 那边撤销了：先强制刷新一次，还被拒就在状态里提示重新登录。429：这一段的额度可能用完了。连不上 xAI：旧令牌没过期就先用着。
- 型号：登录以后，你账号模型目录里对话的那几个出现成 `grok/<型号>`（生图、生视频、朗读、多 agent 的型号不列），`grok/grok-4.7` 排最前、默认挑它。主对话、后台、写作、一次性调用都能选；外人回合不能选。`engine.grok.models` 可以自己列。
- 谁能花：和 ChatGPT 套餐一样。你自己的回合、你自己发起的一次性调用：能。后台任务、定时任务、心跳：要你明确同意一次（和 ChatGPT 的分开记）：`python3 setup_ctl.py grok background on` 或者 `PUT /api/engine/grok/background {"allow": true}`（记下同意的时间）。别人的回合（名片 agent、A2A、Doorman、赛博）、OpenAI 兼容的 `/v1`、ACP、经 `/mcp` 引出来的：一律不花，换成用 API 密钥的模型，换不了就不跑、说清楚为什么。
- 搜网页：登录着、搜索用的是 `grok` 这一家（`engine.web.search.provider = "grok"`）时，先走订阅。xAI 没写明订阅里含不含搜索，留意一下额度（见下面）；用量里记一行、钱记 0。`XAI_API_KEY` 是退路，只在这一轮用不了订阅时才用（没登录、要重新登录、被 xAI 拒、这一轮不能花）；那时 xAI 按次收搜索工具的钱，再加型号的 token。订阅这一次没成（429、连不上）就照实报错，不悄悄换成按量。只登录不会自己把搜索换成 `grok`。`engine.grok.search = false`：搜网页只用 API 密钥。
- 搜 X：`x_search` 工具（搜 X 上公开的帖子，只读；[`engine/xsearch.py`](engine/xsearch.py)）是新的，默认没有，写 `engine.web.x_search = true` 才给。默认不走订阅：xAI 没写过 `x_search` 算不算在订阅里，API 那条路按抓到的量收钱（每 1000 条帖子 $5、每 1000 个账号资料 $10，另加 token；xAI 价目页 2026-10-10）。所以默认只用 `XAI_API_KEY`，用量里每次记一行、钱记 null（抓了多少这边算不出），花费上限算不到它；没有密钥就不给这个工具。写 `engine.grok.x_search = true`（默认 `false`）才先走订阅，谁能花照上面的规矩，`XAI_API_KEY` 是退路。账号名、日期的筛选先在这边查过再去问 xAI。用订阅的令牌时，xAI 有时回一段没有任何 X 帖子做出处的回答（模型自己知道的），结果里会写明。
- 生图、生视频、朗读：默认都关。登录以后，按用途选模型的设置（`/api/setup/purposes` 的 `media` 那几行）在生图、生成视频、朗读里多一家 `grok`；或者写 `engine.use.image = "grok/grok-imagine-image"`、`engine.use.video_gen = "grok/grok-imagine-video"`、`engine.use.tts = {"model": "grok/grok-tts", "voice": "eve"}`（[`engine/grok_media.py`](engine/grok_media.py)）。算订阅的额度，用量里记一行、钱记 0，花费上限不拦。视频一段 1–15 秒、没有声音，每段照样先问你。「用语音回」算后台。xAI 对有的订阅令牌拒过朗读（403）：那就给朗读换一家。
- 额度：`GET /api/setup/usage` 里的 `plans.grok`（套餐名、按周还是按月、用了多少、什么时候重置）；`GET /api/engine/grok/usage` 当场问 xAI；`python3 setup_ctl.py grok`、`python3 setup_ctl.py usage` 也打出来。只算订阅里含的那一份：xAI 的 API 预付余额是按量的另一本账。
- 设置：`server.json` 的 `engine` 下面（都可以不写，每次现读）：`"grok": false` 全部关掉。`"grok": {…}` 能写 `models`（默认用你账号的目录）、`route`（默认 `"api"`；`"proxy"` = 对话改走 Grok CLI 的订阅代理 `https://cli-chat-proxy.grok.com/v1`）、`headers`（对话请求多带的请求头）、`search`（默认 `true`；`false` = 搜网页只用 `XAI_API_KEY`）、`x_search`（默认 `false`；`true` = 搜 X 先走订阅）。`engine.web.x_search`：`true` 或者 `{"model": "…"}`（默认没有）。`engine.use.image` / `video_gen` / `tts`：`grok/<型号>`（默认不写）。

## 看别的会话、Agent 之间转话、mousse agent（自己的内核）

- **看别的会话**（[`engine/sessions.py`](engine/sessions.py)）：你自己的回合里多三个只读工具。`sessions_list` 列会话：哪个 Agent、哪个对话、哪天、最后一句、多少轮、在不在跑（新的在前，默认最近 7 天、20 条）。`sessions_history` 读一段会话最近说的话（默认 10 条，最多 40）：只有你和 Agent 说的字，一条长的截到 2000 字；`include_tools` 再带上调了哪些工具。`session_status` 看一个会话在不在跑、在做哪一步、在等什么（要你点头的卡、`ask_user` 的回答、排着的话、转出去还没回的话、后台任务），还有上一轮的模型和 token。Agent 看自己的会话（它的对话、学习台、对外接口、它派的后台任务），主对话看所有 Agent 的。`server.json`：`"engine": {"sessions": {"visibility": "agent" | "self" | "all", "agents": {"fitness": ["main", "diet"]}}}`，`self` = 只看它所在的这个对话，`agents` 给某个 Agent 单独定（`"*"` = 全部）。赛博、外人回合一律没有这三个。读到的东西照 OpenClaw 那样开头一行 `[Inter-session message] … isUser=false`，内容包在 `<prompt-data>` 里，模型当资料看，不当指令。
- **Agent 之间转话**：`handoff`（`ask_agent.py`）不再只有主对话能用，Agent 也能转：fitness 转给 `main`，health 转给 diet。点头的规矩、转交卡都照旧；会议（`--meet`）还是只有主对话发起。内核把「从哪个对话转出去」告诉脚本（`MOUSSE_FROM`），转交卡照实记从哪来；对方模型收到的那句标成 `[Inter-session message] sourceSession=… sourceAgent=… sourceTool=handoff hop=1/2 isUser=false`：别的会话转来的资料，不带你的同意。一条话最多转两跳（`engine.handoff.max_hops`，1–4），不能转回这一串里正在等回答的 Agent，也不能转给自己。`engine` 里写 `"handoff": {"agents": false}` 退回只有主对话能转。OpenClaw 那条路不变。
- **`mousse agent`**（[`mousse.py`](mousse.py) → `POST /api/engine/agent`，[`engine/agent_cli.py`](engine/agent_cli.py)）：在终端里、或者在另一个编码助手的会话里跟某个 Agent 说一句、拿回结果，原来用 `openclaw agent` 的地方换成它。

  ```bash
  echo "我笔记里那份上线计划靠谱吗？" | mousse agent --agent main --message-file -
  mousse agent --agent fitness --model chatgpt/gpt-6.1-sol --message-file question.md
  ```

  `bin/mousse` 跑的就是 `server/mousse.py`（软链进 PATH：`ln -s ~/.openmousse/repo/bin/mousse ~/.local/bin/mousse`）。每次一个新会话，带那个 Agent 平时的背景，工具只读（查得到；要改的回「没跑」，也不交卡）。打出一个 JSON：`ok`、`status`、`answer`、`model`（还有 `requested`，换了备用模型时有 `fallbackFrom`）、`tools`（名字、标题、结果）、`used`（用到的手册、技能、叶子）、`usage`（输入、输出、缓存的 token，`total`、`cost`，套餐的是 `null`）、`conversation`、`turn`、`ms`；没答成是 `ok: false` 加 `code`、`error`。退出码：0 答了、1 没答成、2 参数不对。不写 `--model` 用那个 Agent 在 app 里选的模型。`chatgpt/<型号>` 走你自己的 ChatGPT 套餐：命令行是你自己发起的，算你自己的回合；套餐没连上、没开用套餐、请求是经 `/mcp` 来的，都直接说清楚；型号要在 `engine.chatgpt.models` 里，没写就用你账号自己的模型目录。`claude-code/<型号>` 用你自己登录的 Claude Code。令牌：在服务器本机用本机脚本那一把，或者 `--token` / `MOUSSE_TOKEN` 给 app 的令牌；`--server` / `MOUSSE_SERVER` 指到别的机器。claw 要是 `builtin`。
- **`mousse chat`**（2026-10-19，[`termchat.py`](termchat.py)）：在终端里跟主对话（`--agent <id>` 换成某个 Agent）聊。每一行经 `/api/chat/send` 当你自己的一句话发进那个对话，和在 app 里打字一样（app 里看得到，排队、插话都照旧），回复一边来一边打出来，工具一步一行（开始打 `·`，没做成打 `✗`，交成要你点头的卡打 `✓ … 等你确认`）。`/stop`，或者回复进行中按 Ctrl-C：停下这条回复（已经说了的留着）；`/quit`、`/exit` 或 Ctrl-D 退出，回复进行中退出不会停它。这一轮交了要你点头的事，最后列出来，请你去 app 里确认；终端里不批。自己的内核问你选择题（`ask_user`）时可以在终端里直接答（`POST /api/engine/answer`），回车跳过就去 app 里答。流断了经 `/api/chat/stream` 接回来。网络只用标准库；令牌、地址同 `mousse agent`。Agent 自己跑的命令里（`MOUSSE_AGENT`）不跑。

## 斜杠命令和对话的基本功（自己的内核）

下面每一样在 `server.json` 的 `engine` 段里有自己的键；不写 = 已经在用的实例一个字不变。只在自己的内核：OpenClaw 那条路照旧。

- **斜杠命令**（`engine.commands`，[`engine/commands.py`](engine/commands.py)）：你自己在 app 里打的、以 `/名字` 开头的一句，在交给模型之前就办掉。命令和回答记在对话里，不进发给模型的会话；别人的话（朋友、名片 Agent、渠道进来的）里的 `/xxx` 只是文字。不认识的名字回一句没有这个命令。`true` 开；`{"off": ["btw", "goal"]}` = 开、但这几条不要。`GET /api/engine/commands?thread=` 列出来（没开回空的）；app 的输入框打 `/` 时列出来。

  | 命令 | 做什么 |
  | --- | --- |
  | `/help`（`/commands`） | 看有哪些命令 |
  | `/status` | 这个对话在不在跑、在等什么、模型和思考档位、上一轮用了多少 |
  | `/usage` | 这个对话今天、今天全部、最近 7 天用了多少 token 和钱 |
  | `/context` | 开头、工具、对话各占多少 token，离压缩还有多远 |
  | `/tools` | 这个 Agent 这一轮有哪些工具 |
  | `/model [名字]` | 看或换这个对话的模型 |
  | `/stop` | 停掉正在跑的回复 |
  | `/new [要点]`（`/reset`） | 马上换一条新会话，开头带上刚才聊到哪（写了要点就带你写的） |
  | `/compact [#消息号] [关注点]`（`/compress`） | 现在把前面的对话压成摘要；写了关注点就照它留细节；`#消息号` = 从那条往前压 |
  | `/think [档位]`（`/thinking`） | 看或设这个对话的思考档位：`low`、`medium`、`high`、`xhigh`、`max`，`default` 跟回默认 |
  | `/queue [模式]` | 回复在跑的时候你又说一句：`steer` 插话（默认）、`followup` 一句一轮、`collect` 合成一轮、`interrupt` 打断 |
  | `/btw <问题>`（`/side`） | 顺手问一句：拿这个对话当背景，不用工具、不进会话、不打断 |
  | `/bg <要做的事>` | 这句放到后台做：派成一个后台任务，主对话照常聊，做完结果回到这个对话（急停着、花费到了上限不派） |
  | `/resume [编号]` | 看这个对话之前的会话；`/resume 2` 开一条新会话接着聊 |
  | `/export [md\|json\|html] [tools]` | 把现在这条会话导成文件挂在回答上（像密钥的字换成 `***`） |
  | `/goal [目标\|status\|pause\|resume\|stop]` | 持续目标（要开 `engine.goal`） |

  `/new`、`/compact`、`/resume` 要等正在跑的回复说完，别的随时能用。整轮交给 Claude Code 跑的对话（`claude-code/…`）一样能用：`/new`、`/compact`、`/resume`、分叉以后，下一句开新的 Claude Code 会话，不 `--resume`。别的模块加命令用 `commands.register(Command(...))`，每个 Agent 不一样的一组（技能）用 `commands.add_source(名字, fn)`；渠道调 `commands.handle(对话, 原话, who=...)`，不是主人的命令回 `None`。
- **换新会话和换天的钟点**（`engine.reset`）：`{"at": "04:00", "idle_minutes": 240}`。`at` 改自己的内核的逻辑日分界（默认 04:00）；`idle_minutes` = 一个对话闲了这么久，下一句开一条新会话、带上一条聊到哪（默认不按闲置换）。新会话的键还是 `mousse:<对话>:<逻辑日>`，按键认会话的地方照旧。
- **压缩**（`engine.compaction`）：`"notify": true` = 自动压了在对话里留一行灰字；`"writeback": true` = 写回开着时顺手排一次写回这条会话；`"prune": true`（或 `{"keep_turns": 3, "min_chars": 4000, "head": 1500, "tail": 1500}`）= 发给模型的样子里，旧的文字工具结果一次裁一批，库里原文不动。`POST /api/engine/compact {"thread", "before": "db123", "focus"}` = 从这条往前压。
- **工具循环**（`engine.loop`）：`"parallel": true`（或 `{"max": 8}`）= 挨着的只读调用一起跑：要点头的、会改东西的、交互的当分隔，结果按原顺序，点停全部取消，标了 `sequential` 的不一起跑。`"guard": true`（或阈值和 `"hard_stop": "unattended" | "always" | "never"`）= 同一个工具反复失败、只读的反复拿到一样的结果，结果后面补一句提醒；没人在看的那几轮（定时、心跳、后台）到阈值硬停。`"spill": true`（或 `{"keep_days": 7, "store_max_mb": 512}`）= 太长的工具结果全文存进数据目录，你的回合多一个翻页工具 `read_output`。`"resume": true`（或 `{"within_minutes": 30}`）= 重启打断的那一轮接着跑一次，告诉模型哪个调用没结果。Claude Code 的回合：死循环检测在 `/mcp` 那一层也查；它自己循环里的并行、翻页归它自己，接着跑时 `--resume` 它的会话。
- **思考档位**（`engine.think`）：`{"main": "high", "*": "medium"}` = 按 Agent 的默认；`/think` 设一个对话的（跟着对话存）。你的回合按请求的 effort 发，Claude Code 照 `--effort`（先于 `engine.claude_code.effort`）；模型没有的档换成它有的、比要的低一档的。不写 = 不带，和以前一样。
- **旧会话、分叉、切枝**：`GET /api/engine/chats/{thread}/sessions`、`POST …/resume {"session"}`、`POST …/fork {"session"?, "entry"? | "message"?}`、`GET …/branches`、`POST …/branch {"leaf"}`、`GET …/export?format=md|json|html&tools=1`（回的样子写在 [`engine/history.py`](engine/history.py) 开头）。会话的标题是第一句；`engine.titles`（`true` 或 `{"model": "provider/model"}`）= 列出来的用小模型起个名。
- **持续目标**（`engine.goal`）：`true` 或 `{"max_turns": 20, "max_tokens": 1000000, "judge": "provider/model"}`。每一轮完了用一次便宜的零工具调用判做到没有，没做到就自动接一轮「接着做」，直到做到、做不了、到上限或者你叫停；你说的话永远先回。它会自己接着跑、接着花钱，所以默认不开。急停着的时候，一轮完了目标就停下，不判、不接着做。

## 用你自己的 Claude 套餐跑一次性调用（自己的内核）

用自己的内核时，你自己的一次性调用也能交给这台电脑上你登录过的 Claude Code 跑，和对话选 `claude-code/…` 一样花你自己的 Claude Pro / Max 套餐。以前 OpenClaw 存着套餐的令牌，这些都经它的 `llm-task` 走套餐；自己的内核不读、不存、不传任何 Claude 令牌，所以改成起官方的 `claude` 一次性模式（`claude -p`，[`engine/claude_brain.py`](engine/claude_brain.py) 的 `ask`）。

- 算哪些：你自己发起的一次性调用：会议纪要和挑谁发言、播客的准备、主持人追问和整理、学习页、闪卡和课程大纲、朋友画像、转发、Zen 的「想完了」草稿和会议的发言，还有压缩你自己长对话的摘要。每个调用方报上来路（`llmjson.ask`、`owner.stream` 的 `kind` / `origin`），没报的当外人。
- 不算哪些：名片 agent（朋友、A2A）、Doorman、别人的回合、赛博、OpenAI 兼容的 `/v1` 和 ACP、后台任务、经 `/mcp` 引出来的，照旧用 API 密钥的模型，和对话一个规矩（`models.may_spend`）。claw 不是自己的内核时也一个都不算：接 OpenClaw 或别的 claw，`engine.route` 不会把一次性 JSON、写作、摘要交给套餐（10/20 起），换成按量的模型（`models.outside()`）或者干脆不走内核，调用方照旧走自己的路（比如 OpenClaw 的 `llm-task`），和 10/19 以前一样。
- 怎么跑：一问一个 `claude -p`，一个工具都不给（没有内置工具，也没有 MCP），不存会话；你的设置、hooks、插件、CLAUDE.md、skills 都不读，环境里像密钥的都去掉，它只能用它自己的登录。调用方的规矩整个换掉 Claude Code 自己的开头。
- 怎么配：`server.json` 的 `engine` 段，`use.json` 管一次性 JSON 调用，`use.compact` 管压缩的摘要。写成列表就带上按量的备用，套餐那一下没做成（登录掉了、额度用完、到时限还没答完，排队也算）时换上。有备用时套餐最多拿一半的时间，剩下的给换上的那个（10/20 起，一次性 JSON、Zen 草稿这类一问一答、压缩摘要都一样），套餐卡住不答也来得及换。压缩摘要给套餐 240 秒（有备用时 120 秒；以前先等满 300 秒，换上的再给 300 秒）。一个从 OpenClaw 换过来的实例：对话、一次性调用、摘要走套餐，名片 agent 和 Doorman 照旧用密钥（它们本来就不能花套餐）：

```json
"engine": {
  "model": "claude-code/opus",
  "models": ["deepseek/deepseek-flash"],
  "use": {
    "json": ["claude-code/sonnet", "deepseek/deepseek-flash"],
    "compact": ["claude-code/haiku", "deepseek/deepseek-flash"],
    "card": "anthropic/claude-opus-5-5",
    "doorman": "moonshot/kimi-k3"
  }
}
```

- 没配备用：套餐那一下没做成就当场报错，说清是哪一类（没登录、额度用完、命令坏了、到时间没答完、过载）、去哪换，别的什么都不跑：10/20 起 `llmjson` 在套餐没做成以后不再退回原来的路。那条路在默认模型也是套餐时会用同一个套餐再跑一遍（再等一个完整的超时），默认模型是按量的就是悄悄换了模型。现在只换你写了的备用（`use.json` 列表后面的，或者 `engine.fallbacks`），备用也没做成就两样都写进报错。用的不是套餐时，原来的退路照旧。没写 `use.compact`：摘要照旧用这个对话自己的模型；写了而套餐那一下没做成，按列表往下换（没写成列表就按 `engine.fallbacks`），最后是这个对话自己的模型。Zen 的草稿和会议的发言跟着对话的模型：是 `claude-code/…` 就走套餐。它拒答的不换别的模型去答。
- 一台机器怎么分：一次性调用和对话一起算 `engine.claude_code.max_concurrent`（默认 2），但永远排在对话后面，同时最多 `engine.claude_code.max_oneshot` 个（默认比 `max_concurrent` 少一个、至少 1），后台一串打分也占不了留给对话的那个位子。10/20 起短的一次性调用（给它的时间不超过 120 秒，排队也算：挑谁发言、播客的建议、准备和提问、会议纪要、有备用时的压缩摘要）排在长的（学习页、课程大纲）前面；`engine.claude_code.burst`（默认 `0` = 不开，和以前一样）= 平常的位子都被占着时，短的最多再多开几个 `claude` 进程，学习页把一次性的位子占上十几分钟，45 秒的调用也不会排着排着就超时。对话和长的调用不用多开的位子；同时跑的最多 `max_concurrent + burst` 个（一个约 0.3 GB）。排队不另设上限：它本来就算在这次调用自己的时限里（有备用时只给一半）。
- 本机的脚本（岗位打分、检查脚本）也能这样问：带 `local` 令牌 `POST /api/engine/oneshot {"prompt": "…", "input": {…}, "required": ["score"]}`，回 `{"json": …, "via": …}`。经 `/mcp` 来的（`X-Mousse-Via: mcp`）不花套餐。
- 看得见、能手动换（feature `setup.purposes.oneshot`）：`GET /api/setup/purposes` 多了一问一答（`json`）和压缩摘要（`compact`）两行。每一行（老的几行也是）都说清：你自己引出来的这件事现在真用哪个（`effective`；没写 `use.json` 的一问一答走以前的退路，自己的内核下就是主对话的模型，`follows: "chat"`）、是谁家的套餐（`plan`：`claude-code`、`chatgpt`、`codex`、`kimi`……，按量的是 `null`）、这台电脑上现在跑不跑得了（`planReady`）、这件事能不能用套餐（`mayPlan`）、套餐没做成时按顺序换谁（`backups`：`use.<这件事>` 列表后面写的，没写成列表按这个模型自己的链或 `engine.fallbacks`，只列有密钥的），还有最近一次用套餐没做成、之后也没做成过的那次（`lastFailure`：`at`、`kind` = `auth` 没登录 / `quota` 额度用完 / `broken` 命令坏了 / `timeout` 超时 / `error` 别的，从内核的回合记录里找）。`backups` 是空的 = 不自动换：套餐出问题时自己去 app 里换（设置 → 按用途选模型 → 一问一答），或者 `PUT /api/setup/purposes {"json": "服务商/型号" | "default" | null, "compact": "服务商/型号" | "default" | null}`。`json` 的 `"default"` = 跟「后台和定时」一样，`null` = 去掉 `use.json`；`compact` 的两个都是跟这个对话自己的模型。能选套餐（真用的时候能不能花照旧是 `models.may_spend` 说了算）；原来写成列表的，换主模型时后面的备用照留。这两行只在自己的内核下能改（别的回 409；接 OpenClaw 时照样列出来，写明由 claw 管）。
- 10/20 起的变化：`PUT /api/setup/purposes {"background": …}` 以前每次都把 `use.json` 改成同一个；现在只在一问一答还跟着「后台和定时」时才一起改：`use.json` 没写、是 `true`、或者和 `use.background` 一样，而且不是在 app 里单独选的（单独选过记在 `<data_dir>/setup/pins.json`）。

## 只跑脚本的定时任务（自己的内核）

定时任务可以到点跑一个命令、不调模型：写 `command` 不写 `task`（[`engine/jobs.py`](engine/jobs.py)）。不调模型，每几分钟查一次也不花钱。

- 能跑什么：[`bridges.py`](bridges.py) 的 `BRIDGES` 里的命令行工具（`board`、`schedule`……，规矩和这个 Agent 平时调它一样），或者 `server.json` 的 `engine.jobs.scripts` 里写了绝对路径的脚本。参数一项一个词、从不经 shell，照 `bridges.guard` 查（不能借 `--file` 这类参数读服务器上的文件）。每次跑之前再查一遍。
- 每次跑：在这个 Agent 的工作区里起一个子进程（`MOUSSE_AGENT`；`MOUSSE_VIA=auto`，它再引出来的回合算后台），`timeout` 秒到了连它起的子进程一起停掉（默认工具自己的时限，脚本 120 秒；最多 600）。退出码 0 算跑成。输出（标准输出，有错误输出也带上）记进运行记录（`GET /api/engine/jobs/{id}` 的 `runs[].output`），不发进对话：要不要说话由脚本自己定。
- 没跑成记错，默认不重试：下一次照常排，第一次在对话里说一声，连着 5 次就停用。`command` 里写了 `"retry": true`（或者重试几次，1 到 5）才按[失败重试](#失败重试)那张表重试：只给跑两遍也没事的脚本写。它们不算进 `engine.jobs.daily_limit`（那是给调模型的上限）。
- 谁建：你在 app 里建（`POST /api/engine/jobs {"agent", "when", "command"}`，马上开）；Agent 用它的 `cron` 工具提，和别的新任务一样先停着，等你点卡上的「开」，卡上写着整条命令和时限。
- 时间写法和别的定时任务一样：`every 5m`、`weekdays 07:30`、`mon,wed 18:00`、cron。

例子：开练前一小时问一句。脚本每 5 分钟看一眼日程，一小时内有像是训练的、还没问过，就经 `/api/chat/trigger` 让主对话问一句；没有就什么都不做。先列进白名单，再建任务：

```json
"engine": {"jobs": {"scripts": ["/home/me/scripts/practice_check.py"]}}
```

```
POST /api/engine/jobs
{"agent": "main", "name": "Before practice", "when": "every 5m",
 "command": {"script": "/home/me/scripts/practice_check.py", "timeout": 30}}
```

<!-- example: practice_check.py -->
```python
#!/usr/bin/env python3
import json
import os
import re
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

conf = json.loads(Path(os.environ.get("MOUSSE_SERVER_CONFIG") or "~/.openmousse/server.json").expanduser().read_text())
bind = conf.get("bind") or {}
host = bind.get("host") if bind.get("host") not in (None, "", "0.0.0.0", "::") else "127.0.0.1"
base = f"http://{host}:{bind.get('port') or 8080}"
token = ((conf.get("auth") or {}).get("tokens") or {}).get("local") or ""
local = urllib.request.build_opener(urllib.request.ProxyHandler({}))
state = Path(__file__).with_name("practice_check.state.json")
practice = re.compile(r"practice|training|训练|练习", re.I)


def api(path, body=None):
    req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with local.open(req, timeout=30) as r:
        return json.loads(r.read())


now = datetime.now(ZoneInfo(conf.get("timezone") or "UTC"))
asked = json.loads(state.read_text()) if state.exists() else {}
asked = {k: v for k, v in asked.items() if k.rsplit("@", 1)[-1] >= now.date().isoformat()}
for e in api("/api/schedule?days=2")["events"]:
    if e.get("kind") != "event" or not e.get("start") or e.get("done") or not practice.search(e.get("title") or ""):
        continue
    starts = datetime.fromisoformat(f"{e['date']}T{e['start']}").replace(tzinfo=now.tzinfo)
    key = f"{e['id']}@{e['date']}"
    if timedelta(0) < starts - now <= timedelta(hours=1) and key not in asked:
        if str(conf.get("language") or "en").startswith("zh"):
            text = f"【开练前一小时】{e['start']} 开始「{e['title']}」：问问用户今天状态怎么样，要不要调整强度。"
        else:
            text = f"[An hour before practice] \"{e['title']}\" starts at {e['start']}: ask the user how they feel today and whether to adjust it."
        api("/api/chat/trigger", {"thread": "main", "text": text, "level": "quiet"})
        asked[key] = now.isoformat(timespec="seconds")
        print(f"asked about {e['title']} at {e['start']}")
state.write_text(json.dumps(asked))
```

## 每周整理技能（自己的内核）

每周一次，把每个 Agent 自己的技能当一整套看一遍，出一张收件箱卡，一条改法一行（[`engine/skill_review.py`](engine/skill_review.py)）。和 `engine.skills.review` 不是一回事：那个是一轮说完看要不要新存一个技能。

- 不写就不开：`server.json` 写 `"engine": {"skills": {"weekly": true}}` = 每周六 09:00（服务器的时区），或者 `{"weekly": {"day": "sun", "hour": 20, "model": "claude-code/opus"}}`。服务器每小时看一眼；每个 Agent 一个 ISO 周最多整理一次（没做成的等下一周），出这一周的卡时，上一周还没定的那张撤回。
- 整理谁：主对话和有自己 `skills/` 的 Agent（不是链接；在沙箱里干活的不算），只看它自己工作区里开着的技能。一个都没有的不问。
- 怎么问：每个 Agent 一次零工具的调用（[`engine/oneshot.py`](engine/oneshot.py)），算你自己的后台（`kind` owner、`origin` auto）：`may_spend` 认就走你的套餐（ChatGPT 套餐后台用要你同意过）。模型：写了 `weekly.model` 就用它（这一轮不能花就这周不整理，不换别的），没写按 `engine.use.json` 的路由，再是 `engine.use.background`，再是 `engine.model`。给它看每个技能的名字、说明、什么时候用、SKILL.md 全文（合计超过 12 万字时最长的几份只给开头）、附属文件、上次什么时候用、30 天里用了几次、出现多久了，还有哪些定时任务、规矩文件（`AGENTS.md`……）点了它的名。
- 能提的改法：改说明（新的 description 和什么时候用）、合并（一份重写，别的停用）、拆（建新的几份，原来那份重写）、停用。每条带理由；留着不动的和为什么写在卡的说明里。规矩照 OpenClaw 和 Hermes 每周整理的：按用途合并，不做大杂烩，没用上不是删的理由，拿不准就留着。服务器再查一遍，不合规矩的挡下：定时任务、规矩文件里点了名的不停用、不合并掉；别的 Agent 的 skills/ 链到这里、它的技能名单（`engine.skills.agents`）上有的，也不停用、不合并掉、不拆；自己写了技能名单的 Agent 一份都不拆（拆出来的新技能不在名单上）；没用过的不到 30 天（或者用了哪条的记录还不到 30 天）不停用；有附属文件的不合并掉；新名字不能撞；新的 SKILL.md 过格式和内容检查；一份技能只在一条里；一周最多 12 条、其中合并和拆最多 2 条。
- 卡（kind `skill`、静默、`itemsKind` `skill-review`，一周后作废）：停用默认不做，别的默认做。提交只做同意的；老 app 一下子点「同意」= 每条照默认。每一条改之前都留一版（`skill_manage restore` 找回来），停用只写 `disable-model-invocation: true`、什么都不删，记性的闸记一笔「点了头」。卡上的字被改过的那条、整理以后又被改过的技能的合并和拆，不做。不往对话里发话，结果写在卡上。卡上「修改」一条还不支持：在对话里回这张卡，Agent 用 `skill_manage` 照你说的改（会先问你）。

## 渠道：Telegram、Discord 和邮件（自己的内核）

在 Telegram、Discord、邮件里跟自己的 Agent 说话，也让你认识的人经这些渠道把话送进来：[`channels/`](channels/)（分工表在 [`channels/__init__.py`](channels/__init__.py)）。只在 claw 选 builtin 时起（OpenClaw 有它自己的渠道，那条路不变），而且只起你开了的：`server.json` 不写 `channels` = 什么都不起，每个渠道还要 `"enabled": true`。已经在用的安装默认关，你点头了才开。

谁走什么回合：

- **你（主人）**。Telegram、Discord：用认主人的码认过的账号（或者写在 `owners` 里的）；邮件：发件人在账号的 `owners` 里、而且这封信 DMARC 过了（或者 DKIM 和发件人的域名对齐）。你说的话走 app 那一条路（`/api/chat/send`）：私聊进绑定的那个 Agent 的对话，和 app 里是同一个对话，关卡、记录照旧；能用你自己的套餐（`origin` 是 `user`），和 app 里一样。回复回到渠道；app 里照样有记录，但这一轮不再推送（回复已经到你问的地方了）。回话期间显示「正在输入」；`ask_user` 的问题当场发过去（Telegram、Discord 上选项是按钮）；几句并成一轮的只回一次。
- **你认识的人（联系人）**：配对批了的、名单上的，有档位（亲近 / 朋友 / 同学）和绑哪个 Agent。他们的话交给名片 Agent（[`cardagent.py`](cardagent.py)）：零工具、只看这一档放出来的、Doorman 复查往外说的话、要你表态的出 `social` 的卡、每人每天有上限。外人回合，花不了你的套餐。
- **陌生人**：私聊回一个配对码，别的什么都不做（不调模型）；你在收件箱卡上、app 里或者 `mousse channels approve` 批，批的时候定档位和 Agent。码一小时过期，每个账号最多 3 个待批，同一个码只回一次。`dm` 也能写成 `allowlist`（只认 `allow_from` 里的，按账号的 `tier` 当联系人）或 `ignore`。邮件的陌生人默认不理、不发配对码。
- **群**默认关。开了的群（`groups.allow` 里写了，或者 `"*"`）里要 @ 机器人、回复它、或者叫 Agent 的名字（`groups.names` 加上绑的那个 Agent 的名字）才答；别的话留最近 `groups.history` 条（默认 30），被叫的时候当资料给模型看。每个群（论坛的每个话题）是自己的会话（`ch-…` 线程，现在都由 main 答）。群里别的人交给名片 Agent，`card.strangers` 没开就什么都不回。
- 显示名、群名、转发自谁、引用的那条、位置、名片都标成不可信，放在单独的一块里（`<channel_context>`、`<group_history>`），模型当资料读，不拼进正文。聊天里任何一句话都不能把人升成主人：只有你在自己设备上出的认主人的码可以。

可靠：每一条进来的先落库（按渠道自己的 id 去重）再确认（Telegram 的 offset、邮件的水位）；同一个聊天一条一条处理，重启以后接着处理剩下的。出去的也排队：按渠道的长度分段（不切断代码块），同一个聊天按顺序发，失败按退避重试（Telegram 的 `retry_after` 照等），试 7 次还不行进死信，能看、能重发。每个人一小时有上限（`limits`：联系人 30、陌生人 5）。第 4 批的急停（`engine/estop.py`）停着的时候，不回别人、不发配对码，你自己的话照常。

卡：`cards: "turn"`（默认）时，你在渠道里说的那一轮出的卡发回那个聊天，名片 Agent 的卡和配对的卡发给这个账号上的你，能放按钮的渠道（Telegram、Discord）带「同意 / 拒绝」：点了走 app 里同一条路，只认你的点击；回复一张卡说一句 = 「改一下」。`"all"` 每张新卡都发给你，`"none"` 一张都不发。邮件点不了，回信里说一句去 app 看。

绑定，越具体越优先：渠道下的 `bindings: [{"account"?, "chat"?, "peer"?, "agent"}]`（人 > 聊天 > 账号，一样具体的按写的顺序），然后是联系人身上定的 Agent，然后账号的 `agent`，最后 `main`。

```json
"channels": {
  "telegram": {
    "enabled": true,
    "accounts": [{"id": "mousse", "token": "${TELEGRAM_MOUSSE_TOKEN}", "agent": "main",
                  "dm": "pairing", "tier": "friend", "cards": "turn",
                  "groups": {"enabled": false, "allow": [], "require_mention": true, "history": 30, "names": []}}],
    "bindings": [{"chat": "-1001234567890", "agent": "fitness"}]
  }
}
```

设置（只认你自己的设备：app 的令牌，或者 Tailscale 里你的设备；脚本、`/mcp` 一律 403）。令牌、密码只进 `secrets.env`，`server.json` 里只写 `${变量名}`，接口永远不回它们；每次写 `server.json` 之前先备份：

| 接口 | |
|---|---|
| `GET /api/setup/channels` | 渠道和账号（不带密钥）、主人、联系人、待批的配对 |
| `GET /api/setup/channels/status` | 每个账号：连上没有、最后一次收到、最后一个错、死信、配置的毛病、待批的配对 |
| `PUT /api/setup/channels/{渠道}` | `{"enabled"?, "bindings"?, "limits"?}` |
| `PUT` / `DELETE /api/setup/channels/{渠道}/{账号}` | 加 / 改一个账号（密钥进 `secrets.env`）；删掉 |
| `POST /api/setup/channels/{渠道}/{账号}/test`、`…/enable` | 测一下连接（不发消息）；`{"enabled": true \| false}` |
| `POST /api/setup/channels/owner-code` | `{"channel"?, "minutes"?}` → 一次有效的认主人的码（默认 10 分钟），Telegram 还给一个 `t.me` 链接 |
| `DELETE /api/setup/channels/owners/{id}` | 这个账号不再是你 |
| `GET /api/setup/channels/pairs`、`POST …/pairs/{码或 id}` | 配对；`{"action": "approve" \| "reject", "tier"?, "agent"?}` |
| `GET` / `POST /api/setup/channels/contacts`、`PUT` / `DELETE …/contacts/{id}` | 联系人：档位、绑的 Agent、`active` / `blocked` |
| `GET /api/setup/channels/outbox?status=dead`、`POST …/outbox/{id}/retry` | 死信、重发一条 |

命令行（[`channels_ctl.py`](channels_ctl.py)）：`mousse channels list | status | owner-code | pairs | approve <码> [--tier] [--agent] | reject <码> | contacts [set <id> …]`。认主人的码、批配对、改联系人在 Agent 自己跑的命令里不跑。

### Telegram

[`channels/telegram.py`](channels/telegram.py) 用 `httpx` 直接调 Bot API（不加依赖），长轮询；webhook 要公网，这一版没做。在 @BotFather 那里建一个机器人，令牌在设置里存（进 `secrets.env`：`TELEGRAM_BOT_TOKEN`，起了名字的账号是 `TELEGRAM_<账号>_TOKEN`），再出一个认主人的码，用你自己的 Telegram 账号给机器人发 `/start <码>`（点 `t.me/<机器人>?start=<码>` 这个链接一下就发了）。

- 一个机器人令牌只能有一个程序在收。别处也在收（另一个安装、别的网关、设了 webhook），Telegram 回 409：这个账号停下、状态里写清楚，隔 30 秒、60 秒……最多 10 分钟再试，不跟它抢，也不替那边删 webhook。每个安装用自己的机器人。
- 群里要先把机器人拉进去；要它看见没叫它的话（叫名字、群历史），在 @BotFather 里用 `/setprivacy` 关掉隐私模式，或者让它当管理员。论坛的每个话题是单独的会话。
- 回复是 Markdown 转成的 Telegram HTML（标题、列表、代码块，表格按等宽文字），一条最多 3800 字；Telegram 不认这段格式就把同样的话按纯文字再发一次。「正在输入」每 4 秒续一次。`/start`、`/stop` 登记在机器人的菜单里；`/stop` 停下正在回的这一轮，和 app 里的停止键一样；别的 `/命令` 当普通的话发过去。
- 你发的照片、语音、文件（最多 `max_media_mb`；机器人本来就只能下 20 MB 以内的）存进这个对话，和在 app 里上传的一样。语音用你设的转写转成字，标上「机器转写」；转不了就是「[语音，没转写]」。别人发的媒体只写一句说明（「[发了一张图片，52 KB，没打开]」），不下载。
- 卡带「同意 / 拒绝」按钮（有好几项的卡改成说一句去 app 看），只认你的点击。`ask_user` 的选项是按钮。

| 键（账号上的，或整个渠道的） | 默认 | |
|---|---|---|
| `token` | — | `${变量名}`；令牌本身在 `secrets.env` |
| `owners` | `[]` | 是你的 Telegram 用户 id（认主人的码认过的不用写） |
| `allow_from` | `[]` | `"dm": "allowlist"` 时：这些用户 id 当联系人 |
| `poll_timeout` | `25` | 长轮询每次等几秒 |
| `stream` | `false` | 边写边显示（一条消息大约每秒改一次），不等写完再发 |
| `allow_bots` | `false` | 别的机器人发的也处理 |
| `max_media_mb` | `20` | 你的文件最大下载多大 |
| `limits` | `{"contact": 30, "stranger": 5}` | 每个人每小时最多几条 |
| `api_base` | `https://api.telegram.org` | 自己搭的 Bot API 服务 |

### Discord

[`channels/discord.py`](channels/discord.py) 用服务器本来就依赖的 `aiohttp`：Gateway（WebSocket）收、REST 接口发。在 Discord 的开发者后台建一个应用、加一个机器人，令牌在设置里存（进 `secrets.env`：`DISCORD_BOT_TOKEN`，起了名字的账号是 `DISCORD_<账号>_TOKEN`），把机器人邀请进一个你在的服务器（scope 选 `bot`，权限要能看频道、发消息、读消息历史），再私聊它 `/start <码>`。

- Discord 不像 Telegram 那样用 409 拦着两个程序用同一个机器人，两边会一起回：每个安装用自己的机器人，别和别的网关共用。
- 连上时要的 intent 是 Guilds、Guild Messages、Direct Messages：私聊、@ 它的话都收得到。要看频道里别的话（叫 Agent 的名字、群历史），先在开发者后台机器人那页打开 Message Content Intent，再写 `message_content: true`；这边开了那边没开，Discord 会拒绝连接（关闭码 4014），状态里说去哪打开。令牌不对（4004）也是停下、状态写清楚，不反复连。
- 断了先续上原来的会话，Discord 把断开期间的补发过来。最后存下的事件跟着消息一起记着，重启以后也从那里续；会话过期了（几分钟）就续不上，中间的消息就收不到了（Discord 不给机器人留）。
- 服务器算群：默认关，服务器里的话在频道写进 `groups.allow` 之前连存都不存。开了的频道里要 @ 机器人（@ 它的角色也算）、回复它、或者叫 Agent 的名字才答；子区、论坛的帖子是频道下面各自的会话。别的机器人、webhook 发的不理（`allow_bots` 开了才理）。
- 回复保留 Markdown（Discord 自己会显示），一条最多 1900 字，从不叫到谁：回复里的 `@everyone`、@某人都只是字。卡上的按钮和 Telegram 一样；Discord 要 3 秒内回应，所以点了先回一下，结果随后发来、只有你看得见。

| 键（账号上的，或整个渠道的） | 默认 | |
|---|---|---|
| `token` | — | `${变量名}`；令牌本身在 `secrets.env` |
| `owners` | `[]` | 是你的 Discord 用户 id（认主人的码认过的不用写） |
| `message_content` | `false` | 要 Message Content（先在开发者后台打开） |
| `allow_from`、`stream`、`allow_bots`、`max_media_mb`、`limits`、`groups` | 和 Telegram 一样 | `groups.allow` 写频道 id |
| `api_base` | `https://discord.com/api/v10` | |

### 邮件

[`channels/email.py`](channels/email.py) 用 Python 标准库的 IMAP 收、SMTP 回。给 Agent 一个自己的邮箱（单独的账号或文件夹）。

- 每 `poll` 秒（默认 60，最少 15）看一次文件夹，只读打开：不标已读、不挪信。第一次只记下现在到哪了（基线），之前就在的信一封都不处理；文件夹的 UIDVALIDITY 变了就重新建基线。每封先只看头，是要回的人才取正文。
- 发件人的地址要你的邮件服务器担保才算数：你的服务器（`authserv_id`）写的第一行 `Authentication-Results` 里，发件人的域名 `dmarc=pass`，或者 `dkim=pass` 而且签名的域名和它对齐。没有这一行、是别的服务器写的、没过，一律当陌生人。不配 `authserv_id` 谁都验不过，所以要写上你的服务器写在这个头开头的名字（打开信箱里一封信的原文看，比如 `mx.example.org`）。服务器还要把外面带进来、冒充它名字的这种行去掉（RFC 8601），主流的邮箱服务都会。
- 你：验过、而且在 `owners` 里的发件人。你的信进绑定的那个 Agent 的对话，和在 app 里说的一样，附件也带上（最多 `max_media_mb`）。回信是纯文字、在同一串里（`In-Reply-To`、`References`、`Re:`），带 `Auto-Submitted: auto-replied`。邮件里点不了卡：回信里说一句有几张在 app 里等你。
- 联系人：你加过的（app 里，或 `mousse channels contacts`）、验过的发件人交给名片 Agent，每一串邮件是自己的一段对话。陌生人默认不理（`"dm": "ignore"`，连正文都不取）；`"dm": "pairing"` 时验过的陌生人收到一个配对码；`"allowlist"` 时 `allow_from` 里的地址当联系人。
- 从来不回：自动回复（`Auto-Submitted`）、邮件列表和群发（`List-Id`、`List-Unsubscribe`、`Precedence`）、退信和 no-reply 地址、自己发出去又回来的。每个地址每小时最多回 `replies_per_hour` 封（默认 5；你是 60），多的进死信。
- 引用的上一封（「On … wrote:」「在 … 写道：」、`>` 开头的几行）去掉；只有 HTML 的信转成字；认不得的字符集解不了的字用替换符。别人的附件只写一句说明，不存。
- 是轮询；IMAP IDLE 这一版没做。同一个邮箱别再配成邮件触发（`engine.triggers.mail`）：两边读同一个文件夹时状态里会提醒。

| 键（账号上的） | 默认 | |
|---|---|---|
| `address` | — | Agent 的地址，回信从它发 |
| `imap.host`、`imap.port`、`imap.user`、`imap.folder`、`imap.security` | —、`993`、`address`、`INBOX`、`tls` | `security`：`tls`、`starttls` 或 `plain` |
| `imap.password` | — | `${变量名}`；密码本身在 `secrets.env`（`EMAIL_<账号>_PASSWORD`；应用专用密码去掉空格再填） |
| `smtp.host`、`smtp.port`、`smtp.user`、`smtp.security` | —、`587`、IMAP 的用户名、`starttls`（465 是 `tls`） | |
| `smtp.password` | 和 IMAP 的一样 | 不一样时是 `EMAIL_<账号>_SMTP_PASSWORD` |
| `authserv_id` | — | 你的邮件服务器在 `Authentication-Results` 里写的名字；不写谁都验不过 |
| `owners` | `[]` | 你的地址 |
| `poll` | `60` | 几秒看一次（最少 15） |
| `replies_per_hour` | `5` | 每个地址每小时最多回几封（你：60） |
| `max_media_mb` | `20` | 你的附件最大收多大 |
| `name` | app 的名字 | 回信上显示的名字 |
| `dm` | `"ignore"` | 或者 `"pairing"`、`"allowlist"` |

### message 工具

开了渠道，你的 Agent 自己的回合里多一个 `message` 工具（[`channels/tool.py`](channels/tool.py)）：

- `to: "owner"`（默认）：发给你，走第一个认得你的渠道（Telegram 在邮件前面，或者 `channel` 指定的那个），直接发、不用点头，和推送一样。`main` 以外的 Agent 发的，开头带上它的名字。
- `to: "<联系人 id>"`：发给你的一个联系人。对外、撤不回，所以一律先交一张卡，你点了同意服务器才照原话发一次。`action: "contacts"` 列出能发给谁：`main` 看全部，别的 Agent 只看绑给自己的。
- 不想要就写 `engine.tools.levels.message: "off"`。服务器内部用 `channels.send_to_owner(text, channel=None)` 同样往你的渠道发一句（定时任务、通知以后用；把定时任务接上是下一步）。

## 选模型、搜索和记忆（自己的内核）

选模型、搜索、记忆的接口是 `/api/setup/…`（[`engine/setup_api.py`](engine/setup_api.py)）；[`setup_ctl.py`](setup_ctl.py) 在终端里走同样的三步（`python3 setup_ctl.py`，或者 `status`、`models`、`add <id>`、`test <id>`、`use chat <provider/model>`、`search <id>`、`memory sqlite`）。不绑哪一家：每一层都能选云端（填你的密钥）、这台电脑上跑的、已经登录的工具、自己填地址，或者不用。

- 模型：目录（[`engine/catalog.py`](engine/catalog.py)）里有 24 家云端（每家的地址都对过它自己的官方文档，来源写在文件里）、这台电脑上的 Ollama、LM Studio、vLLM、llama.cpp、MLX、oMLX（在默认端口上探）、这里登录着的 Claude Code / ChatGPT / Grok / Codex，再加任何 OpenAI 兼容的地址。能同时连好几家，按用途挑：主对话（`engine.model`）、后台和定时（`engine.use.background`）、写作（`engine.use.writing`：长文、文书；有 Gemini 密钥时推荐 `google/gemini-4-argon`；写文书的技能以后接）、一问一答（`engine.use.json`）和压缩摘要（`engine.use.compact`，见上面「用你自己的 Claude 套餐跑一次性调用」）、外人回合（名片 Agent 和 Doorman，不花你的套餐；按 2026-10-19 的评测分开推荐：名片 Agent 推荐按量的 `anthropic/claude-opus-5-5`，没连 Anthropic 时推荐 `moonshot/kimi-k3`；Doorman 推荐 `moonshot/kimi-k3`）、向量（`engine.recall.embeddings`）。只连了一家就自动用它。
- 在 app 里填的密钥存进 `server.json` 旁边的 `secrets.env`（0600、原子写），读的顺序是进程环境 → 它 → `env_file`。永远不回给 app：只说有没有、从哪来、末四位、哪天存的。
- 点了保存才写，写之前在同一目录备份一份 `server.json`（`server.json.bak-<时间>`，留最近 20 份）。已经在用 OpenClaw 的照实显示，不会自己去改。
- `GET /api/setup/usage`（也挂在 `/api/engine/usage`）按天、模型、用途汇总内核的 token 和花费；套餐的记「套餐」、不计钱；登录了 Grok 订阅时 `plans.grok` 还写着这一段的额度用了多少（见「用你自己的 Grok 订阅」）。每一轮按它那天的价算（[`engine/models.py`](engine/models.py) 的 `PRICES` / `PRICES_FROM`：Gemini 4 Argon 2026-12-01 起每百万 token 从 $2 / $10 涨到 $4 / $20）。向量的调用（建索引、搜的那一句、设置页测一下）也算，单独一栏 `embeddings`（按天、型号、种类）：token 用服务商回的，没回按字数估、标成估的；价目里有的才算钱。

## app 里的第一次打开（自己的内核）

app 连上的服务器 features 里有 `setup`（[`version.py`](version.py)）、claw 是 `builtin`、`/api/health` 说 `no_model` 时，进对话之前先走三步：选一个模型（填密钥的云端服务商、服务器上跑的、已经登录的 Claude Code / ChatGPT、自己填的地址；密钥先测通再存，存完拿主对话的型号真发一句）、上网搜索、记忆放在哪，最后一张「设好了」。和 `setup_ctl.py` 是同一套 `/api/setup/…`，只认主人的设备。已经在用的安装只在没有能用的模型时才看得到；别的 claw（OpenClaw、Hermes、OpenAI 兼容的）跳过；没有 `setup` 的老服务器照旧（好友怎么称呼你 → 连接常用服务）。每一步都能「以后再说」，记在那台设备上。`GET /api/setup/models` 另外报服务器是不是 Mac（`host.mac`，给只在 Mac 上跑的本机运行时标灰），每个地区带自己拿密钥的网址（`regions[].keyUrl`）。

新服务器在「设好了」之后多一屏**给助手起个名字**（features 里有 `naming`、claw 是 `builtin`），在「好友如何称呼你」之前，能跳过：名字（默认现在的 `app_name`）、一句性格（可不填）、形象（透镜 / 日食 / 轨道，同「形象」页）。服务器是 [`naming.py`](naming.py)：`GET` / `PUT /api/naming` `{name?, vibe?}`，只认主人的设备，只在自带内核（别的 409）。名字写进 server.json 的 `app_name`（先备份），马上用上、不用重启（`/api/health`、每一轮开头的「You are …」）；性格写成主工作区 `IDENTITY.md` 里一行 `- **Vibe:** …`，写之前留一版（`engine/versions.py`）；`- **Name:**` 那一行还是旧名字的话一起换。之后在「设置 → 名字和性格」里能再进这一页。

app 的设置首页照同一个分法排：连接（授权登录、MCP 服务、技能、记忆、手）、密钥（模型、按用途选模型、向量、搜索、语音、用量）、通用（安全、外观）、其他（别的都在这里）。数字从 `GET /api/setup` 和 `/api/setup/usage` 读；「手」页（只读）读 `layers.hands.agents`（一个 Agent 一项：文件、打开网页、搜索、自己的沙箱和浏览器、主机命令）和 `/api/security` 里主机命令的放行名单。

## 从 OpenClaw、Hermes 搬过来（自己的内核）

`mousse migrate openclaw [--openclaw-home ~/.openclaw]`、`mousse migrate hermes [--hermes-home ~/.hermes]`（[`migrate/`](migrate/__init__.py)）读另一家的家目录，打一份报告：自动转的、要人做的、转不了的。什么都不写。`--out <目录>` 再写成 `server.patch.json`（要合进 server.json 的）、`jobs.json`（定时任务）、`report.md`。`--apply` 才写，只给 `builtin` 的 claw：先备份 server.json（旁边的 `server.json.bak-<时刻>`）和数据库（`<backup_dir>/mousse.db.<时刻>.pre-migrate`），再加上没有的键。已经有、又不一样的不动，列进「要人做的」。再跑一次 `--apply` 不会重复建。报告另存一份在 `<data_dir>/migrate/`。`install.sh` 装自带内核（不是试装）时看到 `~/.openclaw/openclaw.json` 或 `~/.hermes/config.yaml`，选模型之前先问一句：先给报告，你再点一次头才写（回车 = 不写）；`MOUSSE_NONINTERACTIVE` 时只打印命令。

- **源目录只读**：文件只读打开；SQLite（`state/openclaw.sqlite`）先拷到临时目录再只读打开，源目录里连 `-shm` 都不会多一个。工作区不搬：`workspace`（还是安装器的 `~/.openmousse/workspace` 时才换）和 `agent_workspaces` 指到 OpenClaw 的，OpenClaw 的别的 Agent 在 app 的 Agent 列表里补一行。两边别同时对着同一个工作区跑。
- **密钥**只记变量名：值留在原来的 `.env`，`env_file` 指过去。`--copy-secrets` 才抄进 `secrets.env`（0600）。登录令牌（Claude 的 setup-token、ChatGPT / Codex 的 OAuth）从来不抄：在服务器上登录 Claude Code，或者在 app 里 Continue with ChatGPT。
- **模型**：原来走你 Claude 订阅的（OpenClaw 的 `auth.order.anthropic` 第一个是订阅；Hermes 只有订阅令牌），这台机器的 Claude Code 登录着就换成 `claude-code/<型号>`，没有就照旧 `anthropic/<型号>`，按量计费，报告里写一句。Codex / ChatGPT 登录来的换成 `chatgpt/<型号>`，放进 `engine.chatgpt.models`。备用链里只留按量的。名片 Agent、Doorman、一次性 JSON 不替你选。同样的情况下，`--apply` 还把对话里选的 `anthropic/…` 换成 `claude-code/…`。
- **定时任务**转成内核的（[`engine/jobs.py`](engine/jobs.py)），一律先停着、没同意过：`mousse migrate on` 列出来，`mousse migrate on <id>` 打开一个。跑命令的只在命令是命令行工具、或者在 `engine.jobs.scripts` 里时才转。心跳写成 `every: "0"`，报告里写着改回什么就开了。
- **主机命令**：OpenClaw 默认不审批，这里照旧关着。只有写明了的放行名单才转成 `engine.host_exec.agents.<id>.allow`，从来不写 `security: full`。Hermes 的 `approvals.mode` 是 `off` 时也一样。
- **Hermes 的文件**：`SOUL.md` 没有才拷；`memories/` 里新的条目（条目之间一行 `§`）加在 `MEMORY.md` / `USER.md` 末尾，先留一版，已经有的不动；技能先过 [`skill_write.guard`](engine/skill_write.py)，拷过来先停着（`disable-model-invocation: true`、`migrated-from: hermes`），看过以后 `mousse migrate on skill:<名字>` 打开。每个 profile 一个 Agent，带自己的工作区。

## 更多的手：补丁、后台进程、看图听写、浏览器（自己的内核）

这一节的都默认关，在 `server.json` 的 `engine` 段里写了才开；不写这些键的实例一切照旧。工具只在你自己的回合里有（赛博、外人回合一个都没有），从外面拿回来的东西（网页、文件里的字、看图看视频的描述）都标成资料、不是指令。

- **补丁**（[`engine/patch.py`](engine/patch.py)）：写 `"files": {"patch": true}`，有文件工具的 Agent 多一个 `apply_patch`：一个 `*** Begin Patch … *** End Patch` 在它的工作区里一次增、改、改名、删好几个文件，全有或全无（OpenClaw 的格式；行尾空白、弯引号对不上也认）。路径守卫和 `write_file` 一样，每个文件改之前留旧版本，`restore_file` 能一个个换回。动到规矩文件（AGENTS.md、skills/…）、删大过 10 MB 的文件（留不了旧版本）先问你。
- **主机命令经 shell**（[`engine/host_exec.py`](engine/host_exec.py)）：写 `"host_exec": {"agents": {"main": {"shell": true, …}}}`，整条命令交给 `bash -c`，管道、重定向、`&&`、`$VAR` 都能用。照样每一段都查：整条过危险命令识别，再一段段看管道和 `&&` 的每一节、每个 `$( )` / 反引号 / `<( )`、每个重定向写到哪（工作区外面、像密钥的、放行名单里的脚本都算危险）、`cd` 到哪、`eval` / `source`、改 `LD_*` / `PATH`、`… | sh`。`if` / `then` / `while` / `do` / `!` / `{` 后面的命令照样查；`case` / `for` / `select` / `function` / `[[` / `((` 一律算危险（后面跟的不是一条条命令）。放行名单按整条比（只有一段的普通命令照旧一个词一个词地比）；别的照样出卡。不写 `shell` 一切照旧。
- **主机上的后台命令**（[`engine/host_process.py`](engine/host_process.py)）：同一段里写 `"background": true`，`host_exec` 多了 `background`（马上转后台、回编号）和 `yield_ms`（到时候还没完就转后台），还有 `watch`（输出里出现这一行就说一声）；新工具 `host_process` 列出、看输出、按行翻日志、写标准输入、停掉这个 Agent 自己起的后台命令。输出存在 `<data_dir>/engine/processes/`；`max_background`（默认 4）、`background_timeout`（默认 3600 秒）管着上限。一结束就往起它的那个对话写一行、带上输出的最后一段（模型接着回一句，和后台任务一样；`"notify": false` 关掉）。服务重启会把它们停掉，并在那个对话里说一声。定时任务、心跳、后台任务里不给。

- **看图**（[`engine/vision.py`](engine/vision.py)）：写 `"use": {"vision": "google/gemini-3.5-flash-lite"}`（任何会看图的模型；也能写成对象 `{"model": …, "prompt": …, "max_tokens": …}`）有两样。（1）Agent 多一个 `view_image`：一张图（工作区里的、图库里存着的截图路径、这个对话的附件的路径或 id、公网网址：和 `web_fetch` 一样只去公网）加一个问题。主模型看得见图：图直接进工具结果（不花钱）；看不见：看图模型回答。（2）主模型看不见图（DeepSeek，或者最近因为图被拒过）：你发的照片、工具截的图先让看图模型描述一遍，描述代替那句「这里有张图」。描述按图的 SHA-256 存着，同一张图只花一次钱。`"media": {"view_image": true}` 不配看图模型也给 `view_image`（给看得见图的主模型用）；`"media": {"describe": false}` 留着 `view_image`、不再描述。
- **看 PDF**（[`engine/pdf.py`](engine/pdf.py)）：写 `"use": {"pdf": "google/gemini-3.5-flash-lite"}` 或 `"media": {"read_pdf": true}` 多一个 `read_pdf`（来源和 `view_image` 一样，另有 `pages`，比如 `1-5,8`，和 `mode: "text"` 只抽字）。主模型能原样读 PDF（Claude 的接口、OpenAI 自己的对话接口）：PDF 直接进工具结果，最多 `"media": {"pdf_pages": 20}` 页；不能：交给看文档的模型回答（Google 走它自己的接口、PDF 原样放进去；Claude / OpenAI 用 document 块），或者抽出文字。扫描的页（抽不出字）装了 `pdftoppm` 就转成图：主模型看得见就给它看，看不见就让看图模型读出来。PDF 按内容存在 `<data_dir>/engine/docs/`，会话里只记引用。
- **转写**（[`engine/stt.py`](engine/stt.py)）：写 `"use": {"stt": "openai/gpt-4o-transcribe"}`（或者 `groq/whisper-large-v3-turbo`、`google/gemini-2.5-flash-lite`、`deepgram/nova-3`、`elevenlabs/scribe_v1`，服务器上装了 whisper.cpp 的 `whisper-cli` 也能写 `{"model": "local/whisper-cli", "model_path": "~/models/ggml-base.bin"}`；选项有 `language`、`prompt`），Agent 多一个 `transcribe`（工作区里的、附件、公网上的录音 → 文字），附件的转写和语音输入也改走这一家。不写：上传和语音输入照旧用 OpenAI 的 `gpt-4o-transcribe`，不行再 `whisper-1`，一点不变。长的音频交 Gemini 时先传它的 Files API；本机的有 `ffmpeg` 就先转成 16 kHz 的 WAV。给渠道用：`engine.stt.transcribe_file(path, *, mime=None, language=None, prompt=None, conversation=None, agent=None)` → `Transcript(text, seconds, provider, model)`。
- **朗读**（[`engine/tts.py`](engine/tts.py)）：写 `"use": {"tts": "openai/tts-1"}`（或者 `openai/gpt-4o-mini-tts`、`{"model": "elevenlabs/eleven_multilingual_v2", "voice": "<声音 id>"}`、`google/gemini-2.5-flash-preview-tts`、`deepgram/aura-2-thalia-en`，也能是本机的命令，比如 `{"model": "local/piper", "args": ["--model", "/…/voice.onnx", "--output_file", "{out}"], "stdin": true, "format": "wav"}`；选项有 `voice`、`format`、`instructions`、`speed`），Agent 多一个 `speak`：把字念成一个音频文件，存成这个对话的附件，这一轮回完挂到回复下面，app 的文件预览能放（现在的 app 要等对话重新读一次记录才显示）。「用语音回」是按对话的开关，目前只有服务器这边的接口（`engine.tts.set_voice_reply(thread, True, voice=None)`）：开了以后这个对话每条回复都在后台另外念一条。Hermes 默认的微软 Edge 免费语音不接：那是非公开的接口，要多装一个包，微软一改就坏；要免费的用本机的命令。给渠道用：`engine.tts.synthesize(text, *, voice=None, format=None, instructions=None, conversation=None, agent=None)` → `Speech(data, mime, ext, seconds, provider, model)`；`speech_file(…)` 另外存一个文件在 `<data_dir>/engine/speech/`（`Speech.path`）。
- **生图**（[`engine/imagegen.py`](engine/imagegen.py)）：写 `"use": {"image": "openai/gpt-image-1"}`（或者 `openai/gpt-image-2`、`google/gemini-3.1-flash-image`、`fal/fal-ai/flux/dev`，也能是本机的 ComfyUI：`{"model": "comfyui/workflow", "workflow": "~/comfy/workflow_api.json", "prompt_node": "6"}`，默认地址 `http://127.0.0.1:8188`），Agent 多一个 `image_generate`：提示词、可选的参考图（工作区里的路径、附件 id、公网网址，最多 4 张）、`count`（1–4）、`size` 或 `aspect_ratio`、`quality`、`background`、`format`。每张图进图库（模型在工具结果里看得见），也存成这个对话的附件，回复下面显示缩略图。分类是 `undo`（只在你的服务器上多几个文件，除了发给你配的那一家什么都不往外发；每次记花费）；`"tools": {"levels": {"image_generate": "ask"}}` 改成每次都问。在这一轮里跑（没做后台）：各家几秒到一分钟，工具的上限是 6 分钟。
- **看视频**（[`engine/video.py`](engine/video.py)）：写 `"use": {"video": "google/gemini-2.5-flash-lite"}`，Agent 多一个 `video_analyze`（工作区里的、附件、公网上的视频，最多 200 MB，加一个问题；`start` / `end` 写秒或 mm:ss 只看一段）。Gemini 直接看视频（20 MB 以内放在请求里，更大的先传它的 Files API，用完删掉）；别的会看图的模型（OpenAI、Claude……）拿到的是 `ffmpeg` 均匀抽的几张图（`frames`，默认 16 张），听不到声音。服务器上有 `ffmpeg` 时，超过 40 分钟的一次看 40 分钟，超过 100 MB 的先压成 360p。`"media": {"describe_video": true}` 让你在 app 里发的视频上传时先看一遍，描述像文档抽出来的字一样放进消息（默认关：上传照旧）。
- **生成视频、音乐**（[`engine/mediagen.py`](engine/mediagen.py)）：写 `"use": {"video_gen": "google/gemini-omni-1.1-flash"}`（Gemini Omni Flash，经 Google 的 Interactions API：一段 3–10 秒、带声音，`aspect_ratio` 16:9 或 9:16，`resolution` 360p–4k；写成 `{"delivery": "uri"}` 大的经 Files API 拿）或者 `"fal/<模型路径>"`（fal 的队列，比如 `fal/fal-ai/kling-video/v2/master/text-to-video`），Agent 多一个 `video_generate`（提示词、可选的起始图 `image`、服务商收的话 `seconds`）。写 `"use": {"music": "google/lyria-3-clip-preview"}`（30 秒的片段；整首用 `google/lyria-3.5`）、`"elevenlabs/music_v1"`（`music_v2`、`music_v2_5`）或者 `"fal/<模型路径>"`，多一个 `music_generate`（`prompt`、`lyrics`、`instrumental`、`seconds`）。做好的文件存成这个对话的附件。每次在这一轮里先等一会儿（视频 25 秒、音乐 60 秒），没好就转后台接着做，做好了往对话补一句、开一轮，文件挂在那一轮的回复下面（同主机的后台命令）。还在做的时候用同一个提示词再调，回的是它的状态；`action: "status"` 列出来；一个 Agent 同时最多 2 件；在等的这一轮点了停就跟着停；服务重启时没做完的收掉，在那个对话里说一声。`video_generate` 花真钱（Omni Flash 720p 约 $0.10 一秒），所以算 `ask`：每次交一张卡，写清模型和估计的钱，同意了在后台做；`"tools": {"levels": {"video_generate": "auto"}}` 不交卡直接做。`music_generate` 和 `image_generate` 一样算 `undo`（Lyria 一首 $0.04–0.08，ElevenLabs 每分钟 $0.15）。OpenAI 的 Sora 2 接口 2026-09-24 关了，Veo 3.1 预览版 2026-10-22 关，所以都不接。定时任务、心跳、后台任务里不给。
- **你自己的 Chrome**（[`engine/chrome.py`](engine/chrome.py)）：写 `"chrome": {"agents": {"main": true}}`（默认关，按 Agent 开；`"*"` 是别的 Agent 的默认），那个 Agent 多一个 `chrome`：经 CDP 用你真正的浏览器，带着你的全部登录，直接上网，**不经 Doorman**。地址和令牌存在密钥里，不写进 `server.json`：`CHROME_CDP_URL`（`http://<主机>:9222` = 用 `--remote-debugging-port=9222` 开的、服务器连得到的 Chrome，比如经 Tailscale 或 SSH 隧道；也能是 Browserless / Browserbase 给的 `ws(s)://…`）和可选的 `CHROME_CDP_TOKEN`（当 Bearer 令牌发）；存进 app 里的密钥或者服务器的环境变量（代码里用 `engine.chrome.save_address(url, token)`）。看（`snapshot`、`text`、`tabs`、`tab`、`screenshot`、`pdf` = 这一页存成附件）直接做，打开网页开在它自己的那一页，不动你正在看的；点、填、选、按键、跑脚本、关你的页面、打开带着你私事的网址，都交一张卡写清在哪个网站做什么，同意了那一页换了网站就不做。页面上的字标成资料。定时任务、心跳、后台任务里不给。设备节点以后能经 `chrome.ADDRESS_HOOKS` 或者 `chrome.connect_to(agent, address, token)` 交一个地址过来。
- **沙箱浏览器：留着的登录配置档、存 PDF**（[`engine/sandbox.py`](engine/sandbox.py)、[`engine/browser.py`](engine/browser.py)）：写 `"sandbox": {"agents": {"main": {"profile": true}}}`，这个 Agent 的沙箱浏览器配置档（cookie、经 Doorman 登录过的网站）放在 `<data_dir>/engine/browser-profiles/<Agent>`（0700），容器重建也在；只挂进它自己的浏览器容器，那个容器改用服务器这个用户跑。默认关（照旧：配置档用完就扔）。普通 Agent 的沙箱 `browser` 多一个 `pdf`：这一页存成对话的 PDF 附件。
- **搜索多几家、渲染只有脚本的网页**（[`engine/web.py`](engine/web.py)）：`web_search` 还能用 Gemini 的 Google 搜索接地（`GEMINI_API_KEY`）、Grok（`XAI_API_KEY`）、Kimi（`MOONSHOT_API_KEY` 或 `KIMI_API_KEY`）、Firecrawl（`FIRECRAWL_API_KEY`），请求和顺序照 OpenClaw。这几把密钥多半本来就为模型存着，所以这四家只在写明的时候用（`"web": {"search": {"provider": "gemini"}}`），或者写 `"search": {"more": true}` 让它们参与自动挑选（brave → gemini → grok → kimi → perplexity → firecrawl → exa → tavily → searxng）；不写一切照旧。Gemini 和 Grok 回一段带出处的回答（先回答，后面是链接）；这四家每搜一次在 `engine_media_usage` 记一行（只记次数，价钱没核对）。`"web": {"fetch": {"render": true}}` 让 `web_fetch` 遇到几乎没字的 HTML（只有脚本的壳）时交给这个 Agent 的沙箱浏览器渲染一次（只经 Doorman 出网）再读；没有沙箱浏览器的 Agent 照旧。
- **工具多了按需加载**（[`engine/toolsearch.py`](engine/toolsearch.py)）：写 `"tools": {"search": true}`（或者 `{"threshold": 20}`；默认门槛 30），一个 Agent 这一轮的 MCP 和连接器的工具加起来多过门槛时，不再一个个交给模型，换成三个：`tool_search`（按名字和说明找；全部的目录按名字排、最多 18000 字，写在它的说明里）、`tool_describe`（一个工具的说明和参数）、`tool_call`（调一个：`name` + `arguments`）。`tool_call` 的分类照被调的那个工具：只读的直接做，要你点头的交的卡和直接调它时一模一样（同意了照它自己的路做一次）。内核自己的工具、手、记忆照旧直接给。这一份在这一轮开始时定好，一轮里不变，提示缓存不破；关掉的（`engine.tools.levels` 写 off 的、设置 → 连接器里关掉的）不在里面，`tool_call` 先按那个工具自己的 schema 查参数。默认关：照旧全给。
- **结构化的子任务**（[`engine/llm_task.py`](engine/llm_task.py)）：写 `"tools": {"llm_task": true}`（或者 `{"model": "deepseek/deepseek-flash", "models": [...], "timeout": 60, "max_tokens": 4000}`），Agent 多一个 `llm_task`：问一次**没有工具**的模型，照 `prompt` 处理 `input`（任意 JSON，外面来的字也行），回一个 JSON，照 `schema`（JSON Schema：类型、required、properties / additionalProperties、items、enum / const、长短和大小、pattern、anyOf / oneOf / allOf / not / if、本文件里的 `$ref`；不认的关键字跳过）校验，不合的带上哪里不合再问一次。走内核的一次性调用（`engine/oneshot.py`：不存会话，用量照别的一次性调用记）。模型：调用写的 `model` 要在 `models` 里（不写 = app 里能切换的那几个），否则用 `model`，再否则 `engine.use.json`、这个对话的模型、`engine.model`。你自己的套餐（`claude-code/…`、`chatgpt/…`）只在能花的地方用（`models.may_spend`：你自己的回合），不然换按量的。`timeout` 是默认等多久（调用最多能要 300 秒），`max_tokens` 是默认也是上限。定时任务、心跳、后台任务里不给。默认关。
- **比较（diff）**（[`engine/difftool.py`](engine/difftool.py)）：写 `"tools": {"diff": true}`，Agent 多一个 `diff`：`before` + `after`（两段文字，各最多 512 KB；这时 `path` 只是显示的文件名），或者只给 `path`（工作区里的文件，路径守卫同 `read_file`）和它改之前留的旧版本比（`version`，不写 = 最近留的那一版；写 `to` = 比两个旧版本）。回统一格式的 diff（每处改动前后带 `context` 行，默认 3），太长的留头尾；二进制的只说大小和指纹。`page: true` 再发成一个页面（加的绿、删的红）挂在这次回复下面，名字 `name`（不写 = `diff-<文件>`），同名再发是新的一版。只比较算 `read`，带 `page` 算 `undo`。默认关。
- **代码里调工具**（[`engine/codeexec.py`](engine/codeexec.py)，照 Hermes 的 `execute_code`）：写 `"tools": {"execute_code": true}`（或者 `{"tools": ["write_file"], "timeout": 300, "max_calls": 50}`），有沙箱的 Agent 多一个 `execute_code`：一段 Python 在这个 Agent 自己的沙箱里跑（上网只经 Doorman，同 `sandbox_exec`），代码里能把它的工具当函数调（`read_file(path=…)`、`web_search(query=…)`、`call_tool(name, **args)`；`tools()` 列出有哪些），好几步的活一次做完，只有 print 的东西回来（太长留头尾，全部的留在沙箱的 `/tmp/.mousse-code/` 下）。调用走 `docker exec` 那个进程自己的标准输入、输出（不用联网、不用多挂目录），带一把只给这一次的令牌，跑完、停了、到点就收回；每个调用和平时一样过关卡。代码里只做只读的调用，除非你在 `tools` 里列了那个工具；要你点头的照样交卡（脚本停在那里，抛 `NeedsApproval`），关掉的照样不做。一次最多 `max_calls` 个调用（默认 50）、`timeout` 秒（默认 300，最多 900）。这个工具自己的分类同 `sandbox_exec`（`undo`；沙箱能写你的工作区时是 `ask`，卡上是整段代码）。定时任务、心跳、后台任务里不给，只在沙箱里的 Agent 也不给。默认关。
- **媒体用途、密钥和花费**（[`engine/media.py`](engine/media.py)）：`engine.use.vision`、`pdf`、`video`、`stt`、`tts`、`image`、`video_gen`、`music` 写「服务商/型号」或者带选项的对象；你自己的套餐（`claude-code/…`、`chatgpt/…`）这里不收（这几样都是按量花钱的），只有 Grok 订阅（`grok/…`）能用在 `image`、`video_gen`、`tts`（见「用你自己的 Grok 订阅」）。密钥照模型的读法（进程环境、app 里存的密钥、`env_file`），报错里抹掉。每次调用记一行 `engine_media_usage`（`mousse.db`），官方价目页上有价钱的算出花了多少（出处和核对日期在 `media.PRICES`；对话模型照 `engine/models.py` 的价目），`GET /api/setup/usage` 多一栏 `media`。`GET /api/setup/purposes` 在 `media` 那一栏列出来，`PUT /api/setup/purposes` 收 `{"vision" / "pdf" / "video" / "stt" / "tts" / "image" / "video_gen" / "music": "服务商/型号" | {…} | null}`。定时任务、心跳、后台任务里这几个工具都不给。服务商目录的 `caps` 多了 `pdf`、`video`、`audio`（官方文档写了的才填，别的是 `null`）。

## 技能的门槛和设置（自己的内核）

给 OpenClaw、Hermes 写的技能不用改写就能用（[`engine/skill_gate.py`](engine/skill_gate.py)）。门槛不满足的技能，Agent 看得到技能的地方都不列（会话开头的技能目录、`skill` 工具、每句话的搜索、经 `/mcp` 给外援的）；设置接口里照样列出来，写明缺什么。

- OpenClaw 的写法：`metadata: {"openclaw": {"requires": {"bins": [...], "anyBins": [...], "env": [...], "config": [...]}, "os": [...], "always": true, "primaryEnv": "FOO_API_KEY"}}`（JSON 写一行，或者写成 YAML）。`bins` 每个都要在 `PATH` 上，`anyBins` 有一个就行，`env` 要在进程环境、app 里存的密钥（`secrets.env`）、`env_file` 或者这个技能的 `entries.<名字>.env` 里，`config` 是 `server.json` 里的路径、值要算「真」，`os` 是 `darwin` / `linux` / `win32`（`macos`、`windows` 也认）。`always: true` 不看 `requires`，`os` 照样看。ClawHub 上老技能的 `metadata.clawdbot` 也认；`metadata.openmousse` 用一样的键。
- Hermes 的写法：`platforms: [macos, linux, windows]`；`metadata.hermes.requires_tools` / `requires_toolsets`（这个 Agent 没有那些工具就不列）、`fallback_for_tools` / `fallback_for_toolsets`（有就不列）。`required_environment_variables`（也认 `setup.collect_secrets`、`prerequisites.env_vars`）缺了不藏：目录里标 `(setup needed)`，全文后面附一句 `[Skill setup note: …]`，叫模型让你去设置里填，别在对话里要密钥。
- 每个技能的设置：`server.json` 的 `engine.skills.entries.<名字>`（都可以不写；已经在用的安装什么都不变）：
  - `enabled: false` 关掉这个技能。
  - `env: {"FOO_API_KEY": "${FOO_API_KEY}"}`：这个技能用的环境变量。名字像密钥的只能写成 `${变量名}`，值从进程环境、`secrets.env`、`env_file` 取；`apiKey: "${变量名}"` 是 `primaryEnv` 那一个的简写。
  - `config: {"units": "metric"}`：给技能看的非密钥设置。读这个技能全文时附在后面：`[Skill config (from server.json engine.skills.entries.<名字>.config): …]`；技能自己在 `metadata.hermes.config` 里声明了、你没填的，写它的默认值。
- 谁拿得到真值：主机上的命令（`host_exec`）只在这个 Agent 有这个技能（名单上有、过了门槛、没关）时，拿到这个技能声明的变量（`requires.env`、`primaryEnv`、`required_environment_variables`）和 `entries.<名字>.env` 里不像密钥的那几个。`PATH`、`LD_*`、`PYTHONPATH`、代理和证书这类变量一律不给。沙箱照旧只有占位符，真值不进容器。
- 设置接口（只认你自己的设备；写 `server.json` 之前先备份）：`GET /api/setup/skills/{agent}` 每个技能多一项 `gate`（`eligible`、`enabled`、`missing`：「要 ffmpeg」「要 FOO_API_KEY」、`setupNeeded`、`env`）。`GET /api/setup/skills/config/{名字}` 看一个技能的开关、环境变量（只说有没有、从哪来、末四位，不给值）、设置、门槛。`PUT /api/setup/skills/config/{名字}` 收 `{"enabled"?, "env"?: {"FOO_API_KEY": "值" | null}, "config"?: {…} | null}`：值只进 `secrets.env`，`server.json` 里只写 `${FOO_API_KEY}`；`null` 去掉这个变量（别的技能没在用的，`secrets.env` 里那一行也删）。
- 技能当斜杠命令用：`/<技能名> <要它做的事>`（或者 `/skill <技能名> <要它做的事>`）把这个技能的全文、它的设置和缺的环境变量、文件夹里还有哪些文件，再加上你跟着说的话一起交给模型（照 Hermes 的做法）。名字不分大小写、`-` 和 `_`。内置命令赢；frontmatter 写了 `user-invocable: false`（OpenClaw 的写法）的不算命令，这个 Agent 用不了的（名单上没有、没过门槛、关掉了）也不算。认命令的是 [`engine/agent_skills.py`](engine/agent_skills.py) 的 `slash_skill`；等内置命令的框架合进来，对话里先认内置命令、再调它。

## 所有 Agent 共用的技能（自己的内核）

除了每个 Agent 自己的 `skills/` 和自带的 `packs/core/skills`，还有一处所有 Agent 都看得到：数据目录下的 `skills/`（`<data_dir>/skills`，默认 `~/.openmousse/data/skills`；对应 OpenClaw 的 `<state-dir>/skills`、Hermes 的 `~/.hermes/skills`）。同名的时候 Agent 工作区的盖过它，它盖过 core 的。技能市场装的放在这里（你说装进某个 Agent 的工作区就放那边），你也能自己放进来。Agent 写不了这里：`skill_manage` 只写它自己的工作区（同名的写在那边会盖过共用的；指到共用目录的链接，改之前先换成它自己的拷贝）。门槛和设置（上一节）照样管。

## 技能市场：搜和装（自己的内核）

几家一起搜，扫描过、你点了头才装（[`engine/skill_market.py`](engine/skill_market.py)）。不做「发布」。

- 来源：**ClawHub**（`clawhub:@作者/slug`；搜、每次装之前查它的信任结论、下载）、**GitHub**（`github:owner/repo[/路径][@ref]`；ref 先换成 40 位提交号，lock 里记它；搜的是 `repos` 列的几个仓库，配了 `GITHUB_TOKEN` 再加 GitHub 的代码搜索）、**skills.sh**（只用来搜；装的时候从它指的 GitHub 仓库下，同样钉到提交号）、**照 agentskills.io 格式发布技能的网站**（`/.well-known/agent-skills/index.json`，带摘要；或者 `/.well-known/skills/index.json`；`sites` 里列的会被搜，别的网站给地址也能装），还有**直接给的 `SKILL.md` 链接**。agentskills.io 本身是格式规范：没有公开的目录或搜索接口（2026-10-19 查过），所以不单独当成一家，而是照它的发布格式的网站都认。
- 搜：几家一起问，每家有时限；出错、超时的跳过，在 `failed` 里写明。合并、去重（同一个 GitHub 文件夹从两家来的算一份），按名字和说明对上这句话的程度排。
- 装：先下到内存，有硬上限（zip 最多 25 MB、最多 100 个文件、一个最多 1 MB、加起来最多 10 MB）；往外跑的路径（zip-slip）、符号链接和硬链接、可执行的二进制，整份拒。放进隔离区（`<data_dir>/skills-market/quarantine/`，不留执行位），用 Hermes 的 skills guard 扫（外传数据、提示注入、破坏性命令、改 Agent 配置、隐形 Unicode……），不管从哪来一律按社区、不受信扫。`dangerous` 永远不装（命令行也不装）；ClawHub 拦下的、标成恶意的也不装（查不到 ClawHub 的结论也不装）。别的给 Agent 交一张卡：来源、版本或提交号、扫描结论和发现、`SKILL.md` 开头一段、装到哪。你点了同意才照卡上的指纹核对隔离区里那一份、再扫一遍，放进共用的技能目录（或者 `agent` 指定的那个 Agent 的工作区）。同名的已经有了不盖。
- `<data_dir>/skills-market/lock.json` 记每一次装的：来源、标识、版本或提交号、内容指纹、扫描结论和发现、装的时间。
- 更新：照同一个标识再下一次（GitHub 照原来的分支换成新的提交号），比指纹。一样 = 已经最新；你本地改过的跳过；上游变了的照样隔离、扫描、出卡，换之前先留一版（`engine/versions.py`）。卸载先留一版再拿掉。只动市场装的（lock 里有的）。
- Agent 的 `skill_market` 工具（`engine.skills.market.enabled` 写 `true` 才有，默认关：会联网）：`search`、`list` 只读；`install`、`update`、`remove` 交卡。照 OpenClaw 自带的 clawhub 技能：说「做不到」之前先搜一下；搜到了不等于能装。
- 设置接口（只认你自己的设备）：`GET /api/setup/skills/market/search?q=`、`POST /api/setup/skills/market/preview {"id", "agent"?}`（下载、隔离、扫描，回结论和一个 `staged`；dangerous 的 `installable: false`）、`POST /api/setup/skills/market/install {"staged"}`、`GET /api/setup/skills/market/installed`、`POST /api/setup/skills/market/update {"name", "agent"?}`、`DELETE /api/setup/skills/market/installed/{名字}?agent=`。
- 命令行（在你自己的机器上）：`mousse skills search <要搜的话>`、`mousse skills install <id> [--agent <id>] [--yes]`（扫过就直接装；`caution` 要 `--yes` 或者终端里答 `y`；`dangerous` 不装）、`mousse skills update [<名字> | --all] [--force]`、`mousse skills list`、`mousse skills remove <名字>`。
- 设置：`server.json` 的 `engine.skills.market`（都可以不写）：`{"enabled": false, "sources": ["clawhub", "skills.sh", "github", "well-known"], "clawhub": "https://clawhub.ai", "github_api": "https://api.github.com", "github_raw": "https://raw.githubusercontent.com", "skills_sh": "https://skills.sh", "repos": ["anthropics/skills"], "sites": [], "timeout": 8}`。Agent 给的链接（直接的 `SKILL.md`、不在 `sites` 里的网站）只去公网。

## 你自己的钩子（自己的内核）

内核里发生某件事时跑你自己的脚本（[`engine/user_hooks.py`](engine/user_hooks.py)）。只跑脚本：你的 Python 不在服务器进程里跑（那是下面的带代码的插件）。你写了、同意了才会跑。

- 事件：`startup`、`session:start`、`session:end`（一个对话这一天的会话建了，前一天那条算结束）、`message:received`（你在 app 里说了一句；能拦下这句话）、`reply:sent`、`tool:before`（工具调用之前；能拦、能改参数）、`tool:after`、`compact:before`、`compact:after`。能拦的那两个当场等脚本跑完，别的在后台跑。
- `server.json` 的 `engine.hooks.<事件>` 是一个列表：`{"command": ["python3", "/home/me/hooks/guard.py"], "matcher": "host_exec|demo__*", "timeout": 30, "fail_closed": false, "cwd"?}`。`command` 是 argv 列表，不经 shell；`matcher`（只看工具的事件）是工具名，可以带 `*` / `?`，几个用 `|` 隔开；`timeout` 默认 30 秒，最多 300。
- Agent 工作区里照 OpenClaw 写的钩子要写 `"workspace": true` 才认：`hooks/<名字>/HOOK.md`，`events`（也认 `metadata.openclaw.events`；OpenClaw 的 `gateway:startup`、`command:new` 这些名字照着换）、`requires` / `os` 和技能一样；处理程序只认脚本：frontmatter 的 `command`，或者旁边的 `handler.py` / `handler.sh` / 有执行位的 `handler`。只管那个 Agent 自己的事件。
- 协议和 Claude Code、Hermes 的一样：脚本从 stdin 收这个事件的 JSON（`event`、`hook_event_name`、`agent`、`conversation`、`at`，还有 `tool_name` / `tool_input`、`text` / `from` / `trusted`……）。stdout 回 `{"decision": "block", "reason": "…"}` = 拦下（Hermes 的 `{"action": "block", "message"}`、Claude Code 的 `permissionDecision: "deny"` 也认），`{"decision": "modify", "tool_input": {…}}` = 改这次工具调用的参数（只 `tool:before`；也认 `{"action": "modify", "args"}`、`updatedInput`）。退出码 2 = 拦下，原因取 stderr。别的退出码、超时、回的不是 JSON：照常做，除非写了 `fail_closed: true`。
- 拦下的工具调用把原因回给模型；拦下的那句话回一句「被钩子拦下了」。改过的参数照工具的 schema 查，只能改成同一类调用（关卡的分类不能变严，命令行工具的子命令不能换），再过一遍关卡。脚本打出来的字一概不进模型。
- 同意（照 Hermes）：一条钩子第一次跑之前要你同意。它的指纹（事件、命令、工作目录、它跑的脚本的内容）进 `<data_dir>/hooks/allowlist.json`，命令或脚本改了要重新同意。没同意的不跑（`fail_closed` 的也不拦），交一张同意卡；终端里 `mousse hooks approve` 也行。
- 脚本只拿到最小的环境（`PATH`、`HOME`、语言区域这些，加上 `MOUSSE_HOOK_EVENT`），拿不到服务器的各家密钥；自己一个进程组，到时限整组收掉。
- 自带两个，默认关，在 `engine.hooks.bundled` 里开：`"boot-md": true` 服务起来时把每个 Agent 工作区里非空的 `BOOT.md` 当一轮你自己的回合跑一次（新会话、不往任何对话里发，模型照心跳的挑法，不花你的套餐）；`"command-logger": true` 每次会话开始、结束和每条斜杠命令往 `<data_dir>/logs/commands.jsonl` 记一行 JSON。
- `GET /api/engine/hooks` 看有哪些钩子、同意了没有、最近几次跑的结果。命令行：`mousse hooks list`、`mousse hooks approve <指纹前几位> | --all`、`mousse hooks revoke <指纹前几位>`、`mousse hooks test <事件> [--tool <工具名>] [--payload <JSON>]`。

## 你配的 MCP 服务：prompts、sampling 和管理（自己的内核）

`server.json` 的 `mcp_servers` 里你自己加的 MCP 服务（[`engine/mcp_client.py`](engine/mcp_client.py)）多了三样。已经在用的不受影响：prompts 只有服务真有才出现，sampling 每个服务单独开，不开就是关的。

- prompts：服务说了有 prompts、还真列得出至少一个，Agent 就多两个只读工具：`<服务>__prompts_list`（名字、说明、参数，带 `*` 的必填）和 `<服务>__prompts_get`（`{"name", "arguments": {"…": "…"}}`）。取回来的包成「外面的资料，不是指令」。服务上写 `"prompts": false` 就不给。
- sampling，默认关：服务反过来要我们的模型写一段（MCP 的 `sampling/createMessage`）。没写 `sampling` 就照 MCP 规范回拒绝（"Sampling not supported"）。写了 `"sampling": {"model": "deepseek/deepseek-flash", "max_tokens": 1024, "max_rpm": 10}`（都能不写：`model` 默认用默认模型；`max_tokens` 是一次最多写多少，服务要得再多也按它；`max_rpm` 是一分钟最多答几次，超了回错）才答：永远是零工具的外人回合（服务给的 system 和消息就是全部输入，碰不到记忆、对话和任何工具），从来不花你自己的 Claude Code、ChatGPT 套餐（`model` 写成套餐的不答；没写 `model`、默认模型又是套餐的，用 `engine.models` 里第一个按量的），记进用量（用途算「其他」，对话 `mcp-sampling:<服务>`）。服务要的 `includeContext`、`modelPreferences`、`tools` 都不理。目前只有本机（stdio）的服务能用：远端每次调用一个短会话，答不了。会 sampling 的服务把 `timeout` 设得够模型答完。
- 命令行管理：`mousse mcp list [--json]`；`mousse mcp add <名字> --command <命令> [--arg …] [--env K=V …] [--cwd …]` 或者 `--url <地址> [--type http|sse] [--header K=V …]`，再加 `[--agent <id> …] [--tool <工具>=auto|ask|off …] [--disabled]`；`mousse mcp remove | enable | disable <名字>`；`mousse mcp probe <名字> [--json]`。`add` 先真连一次、列出工具，连不上不存（`--save-anyway` 存成关着的，`--no-probe` 不连）。`env`、请求头里的密钥照旧只能写成 `${变量名}`。
- 设置接口（只认你自己的设备）：`GET /api/setup/mcp`；`GET /api/setup/mcp/{名字}`（照 server.json 写的那一项，像密钥的明文值打码，加上它现在的状态）；`PUT /api/setup/mcp/{名字}`（请求体就是那一项，照 server.json 的规矩查，sampling 用套餐的不收）；`DELETE /api/setup/mcp/{名字}`（顺手收掉它的进程）；`POST /api/setup/mcp/{名字}/probe`（真连一次：起进程或者连上、初始化、列工具和 resources、prompts，再收掉；正在跑的那一个不动）。接口和命令行改 `server.json` 之前都先备份；内核每轮现读，改了下一轮就生效。
- elicitation（服务在调用中途反过来问你一句）还没做：照规范回拒绝。

## 要记得的事（自己的内核）

「下次我提到护照，提醒我续签」：照 OpenClaw 的 standing intents 做的（[`engine/intents.py`](engine/intents.py)）。默认关：`engine.intents.enabled` 写 `true` 才有。

- Agent 在你自己的回合里多一个 `remember_when` 工具：`create` 写 `triggers`（几个词或短语）和 `reminder`，可选 `max_fires`、`cooldown_hours`、`expires`；`list`；`cancel`（建错了也这样撤，所以不用交卡）。
- 你说的每一句都不调模型地对一遍：小写、拆成词，一个触发词的每个词都在这句话里才算对上（中文、日文、韩文、泰文词和词之间不空格，按字串找）。服务器自己写进对话的（收件箱知会、定时脚本、后台任务交回来的结果）不对，别人的话从来不对。
- 对上了：这一轮在你这句话后面多一行，和按这句话搜到的手册在同一条里（没有手册就单独一条）：`Reminder the user asked you to keep (set 2026-10-19; their message mentions "passport"): …`（给模型看的，英文）。一句话最多带 3 条、1200 字。每条最多提 `max_fires` 次（默认 3），两次之间至少隔 `cooldown_hours` 小时（默认 24），到 `expires` 为止（默认 90 天），然后自己停。每次对上都记进这一轮用了什么（`engine/used.py`，kind `intent`）。
- 在记性的闸里算「经历」，和 `HEARTBEAT.md` 一样：带日期、会自己用完过期的一条「你那时让我记着的」，不是事实（叶子），也不是长期的做法（做法）。所以在你的回合里建、撤都是直接写，记进闸的事件流；赛博、外人回合都不行。
- `GET /api/engine/intents?agent=main[&all=1]` 列出来；`DELETE /api/engine/intents/{id}` 撤掉一条。
- 设置，`server.json` 的 `engine.intents`：`{"enabled": false, "max_fires": 3, "cooldown_hours": 24, "expiry_days": 90}`（后三个是 `create` 不写时的默认）。

## 带代码的插件（自己的内核，默认关）

用 Python 给内核加工具、钩子、技能、MCP 服务（[`engine/plugins.py`](engine/plugins.py)），照 Hermes 的插件（manifest + `register(ctx)`）。

**插件的 Python 在服务器进程里跑，没有沙箱。** 它能做服务器能做的任何事：读 `server.json`、`secrets.env` 和各家的密钥、连网、起进程、改文件。OpenClaw 说它的原生插件「和核心代码是同一道进程级的信任边界」，这里一样。下面这些规矩管的是插件说自己要做什么、你同意了什么，挡不住一个存心作恶的插件：只装你信得过的。

- `server.json` 的 `engine.plugins.enabled` 里写了名字（`{"enabled": ["weather"], "load_timeout": 10, "entries": {"weather": {"agents": ["main"]}}}`）、装好了、你同意过它的能力，才载。不写就一个都不载。
- 一个插件 = `<data_dir>/plugins/<名字>/` 一个文件夹：`openmousse.plugin.json`（`name`、`version`、`description`、`capabilities`：`tools`（`[{"name", "undoable"}]`）、`hooks`（事件名）、`skills`（文件夹）、`mcp`（服务名））和 `plugin.py`（普通函数 `register(ctx)`）。
- `ctx` 只给 manifest 声明了的：`ctx.tool(name, fn, description=, schema=, undoable=False)`（名字 `<插件>__<工具>`，只在你自己的回合、只给 `entries.<插件>.agents` 里的 Agent，默认 `main`；撤得回的直接调，别的每次交卡）；`ctx.hook(event, fn, matcher=, timeout=)`（和脚本钩子一样的事件、一样的 `block` / `modify`）；`ctx.skills_dir("skills")`（所有 Agent 都看得到，在共用的技能目录后面、core 前面，照旧过门槛和名单）；`ctx.mcp_server(name, entry)`（照 `mcp_servers` 的规矩查；`server.json` 里同名的盖过它）。没声明的要一个，整个插件不载。
- 你同意的是它的能力串（`tool:x`、`undoable:x`、`hook:<事件>`、`skills`、`mcp:x`），记在 `<data_dir>/plugins/plugins.json`。命令行装（打了 `y`）、设置接口装都算同意。开着、你还没同意，或者更新以后能力多了的不载，交一张同意卡；能力少了不用重新同意。
- 装：本机文件夹，或者 git 地址加完整的 40 位提交（分支、标签、短哈希都不收；checkout 以后核对 HEAD）。文件过技能市场那一套上限（不许往外跑的路径、链接、可执行的二进制）、Hermes 的 skills guard，再加 Python 的静态检查（动态代码、shell 命令、本地库、环境变量、连网、密钥文件……）。这些发现只提醒：给你看，不拦。
- 载：服务器起来时、startup 的钩子之前，每个插件在一个线程里 import + register，有时限（`load_timeout`，默认 10 秒，最多 120）。超时、出错的这一个不载，它登记过的全部作废，别的照载。改了要重启服务器（Python 卸不掉已经载进来的模块）。
- 命令行：`mousse plugins list`、`mousse plugins install <文件夹 | git 地址> [--commit <哈希>] [--yes] [--enable] [--replace]`、`mousse plugins enable | disable | approve | remove <名字>`（remove 先在 `<data_dir>/plugins/.removed/` 留一份）。设置接口（只认你自己的设备）：`GET /api/setup/plugins`、`POST /api/setup/plugins/preview {"source", "commit"?}`、`POST /api/setup/plugins/install {"staged"}`、`POST /api/setup/plugins/{名字}/approve`、`PUT /api/setup/plugins/{名字} {"enabled"}`、`DELETE /api/setup/plugins/{名字}`。改 `server.json` 之前都先备份。

## 闸和从外面进来的触发（自己的内核）

这一节的东西对已经在用的安装默认都关着，你开了才有；急停一直在，不用就什么都不做。

### 急停

一个开关停掉所有自动的事（[`engine/estop.py`](engine/estop.py)，照 Hermes 的 `hermes pause`）。就是一个小文件 `<data_dir>/ESTOP`：重启照样停着，不写 `server.json`。

```bash
mousse pause --reason "周末出门"      # 新的自动的事都不做了
mousse pause --stop-running          # 正在跑的也停掉
mousse resume
```

- 停着的时候：定时任务、心跳到点不跑（运行记录记一条 `estop`，不算失败、不算进「连着 5 次」；这时到点的一次性任务记成错过）。webhook 回 `503` 带 `Retry-After`，盯邮箱、盯东西的不去拉，新的后台任务不派（`tasks` 工具回一句为什么），工作流不往下走，写回、技能回看、补向量都等着。Agent 不能用 `cron` 手动跑任务；你在 app 里照样能。
- 你自己在 app 里说话照常，点收件箱的卡也照常。
- 正在跑的跑完，除非停的时候带了 `--stop-running`（`"stop_running": true`）：那就像点了停一样，停掉正在跑的定时任务、心跳、后台任务、工作流那一步、转了后台的生成视频 / 音乐，还有对话里自动开的那几轮（`origin auto`）；记成被打断，不算失败。你自己在 app 里说的那一轮从来不停。
- 文件是空的、写坏了也算停着（`touch` 一下就行）。命令行直接写文件，服务器停着也能停；服务器每次现看，`--stop-running` 30 秒内照做。
- 接口，只认你自己的设备（app 的令牌或者 Tailscale 里你的设备，和设置页一样）：`GET /api/engine/pause`、`POST /api/engine/pause {"reason"?, "stop_running"?}`、`POST /api/engine/resume`。`/api/engine/status` 里有 `paused`。

### 花费上限

内核一天、一轮最多花多少美元（[`engine/spend.py`](engine/spend.py)）。不写 `engine.spend` 就没有上限，和以前一样。

```json
"engine": {"spend": {"day": 5, "turn": 1, "warn": 0.8,
                     "agents": {"g-study": {"day": 1}},
                     "purposes": {"background": {"day": 2}, "outsider": {"day": 1}}}}
```

- `day`：按 `server.json` 时区的日历日（和用量页一样），一天按量计费的一共最多多少美元。`turn`：一轮（一句话进来到模型说完，中间调几次模型都算）最多多少。`agents.<id>`、`purposes.<用途>` 各有自己的 `day` / `turn`；用途和用量页一样：`chat`（你自己的对话）、`background`（定时任务、心跳、后台任务、写回）、`outsider`（名片 Agent、Doorman、外面进来的触发那一次零工具的读）、`api`、`cyber`。写 `0` = 这一项按量的一个都不跑。`warn`（默认 0.8）到了这个比例每个上限一天推一条静默通知，真到了上限再推一条。
- 算什么：价目里有的模型（[`engine/models.py`](engine/models.py) 的 `PRICES`），和用量页算的是同一份钱，再加上向量（建索引算后台）和价目里有的媒体（生图、生成视频 / 音乐、看图、语音……）：媒体的钱算进当天和那个 Agent 的，不分用途。生图、生成视频 / 音乐开跑之前看一眼上限；本机的（ComfyUI、本机命令）从不拦。你自己的套餐（`claude-code/…`、`chatgpt/…`，还有用 Grok 订阅的媒体）这里不算钱，也从不拦；Claude Code 用 API 密钥登录的照算（它自己报的估算）。价目里没有的型号算不出钱：照记 token，`GET /api/engine/spend` 的 `unpriced` 里列出来，不拦——不然本机的 Ollama、刚出的型号会莫名其妙用不了。要管它们，就给它们写上价，或者别给按量的密钥。
- 到了以后：每次调模型之前都看一眼，跑到一半到了的那一轮在下一次调模型时停下，不重试、不换备用模型。你在 app 里的那一轮最后有一句话说清花了多少、上限多少、改哪个键。定时任务、心跳、后台任务开跑之前看一眼：到了就跳过、记 `limit`，不算失败。Claude Code 用 API 密钥登录的那几轮开跑前看一天的总数；补向量看一天的总数和 `background`。
- 管不到的：名片 Agent 的 `card.llm` 直连、OpenClaw 的 `llm-task`，都不经内核。
- `GET /api/engine/spend`：今天每个上限花了多少、还剩多少、哪些拦着、算不出钱的和套餐的有几轮。`PUT /api/engine/spend {"day"?, "turn"?, "warn"?, "agents"?, "purposes"?}` 改上限（`null` 去掉一项，`{"spend": null}` 全去掉），只认你自己的设备，写之前先备份 `server.json`。

### 失败重试

调模型的定时任务没跑成怎么办，要在 `engine.jobs` 里写 `"retry": true` 才开（[`engine/jobs.py`](engine/jobs.py)，照 OpenClaw 定时任务的退避和 Hermes 的 quota hold）。默认关：重试就是再调一次模型，花的是套餐的额度或者按量的钱。关着时没跑成的就等下一次正常到点，和以前一样。

- 一会儿就好的毛病（连不上服务商、429 或者过载、5xx、超时），而且模型还没出字、没调工具：同一次隔 30 秒、1 分、5 分、15 分、60 分再来，不越过这个任务的下一次正常到点（到了就照那一次跑）。一次的最多重试三次。重试那几次不算「跑了几次」，也不算进「连着 5 次」；最后一次重试也不行，对话里才说一声。服务商说了要等多久（「try again in 20s」）就至少等那么久。
- 服务商说配额窗口关了、还说了什么时候开（「resets 3pm」「retry after 7200s」）：不再每到点就失败一次，先等着。窗口里的那几次合成一次，挂到窗口之后的第一个到点；一天一次这种稀的，窗口一过补跑这一次。对话里说一声，写明到几点、下一次什么时候。没说什么时候开、或者要等一个星期以上的，当永久的。
- 别的都是永久的，不重试：规矩不许、没配好、没登录、命令跑不起来、到了花费上限、急停、模型拒了，或者这一次已经出了字、调过工具（再来一遍可能把做过的事再做一遍）。
- 发进对话的任务（`"session": "chat"`）一会儿就好的毛病不重试：没跑成的那一次已经在对话里留了「自动触发」和没拿到回复，再来一遍就是两份。配额窗口的等待照旧。
- 只跑命令的：`command` 里写了 `"retry"` 才重试（不管 `engine.jobs.retry` 写没写）；心跳不重试（下一拍反正会来）；你手动跑的也不重试。
- `engine.jobs.retry`：写 `true` 用 `[30, "1m", "5m", "15m", "1h"]` 这张表，写成列表换成你的，不写或者写 `false` 就不重试。

### Webhook

别的服务往 `/webhooks/<名字>` POST 一下，内核告诉你一声、你点头后交给 Agent，或者（你自己签了名的脚本）直接叫醒 Agent（[`engine/webhooks.py`](engine/webhooks.py)、[`engine/triggers.py`](engine/triggers.py)；验签、过滤、合并照 Hermes 的 webhook）。不加就没有，只在自己的内核下收。

```json
"engine": {"triggers": {"webhooks": {
  "github": {"verify": "github", "secret": "${GITHUB_HOOK_SECRET}", "label": "GitHub",
             "events": ["pull_request"], "filters": [{"field": "action", "in": ["opened", "reopened"]}],
             "coalesce": {"key": "pull_request.number"},
             "action": "card", "say": "只说要我 review 的 PR。",
             "task": "读一下这个 PR，给我写一段简短的 review 摘要。"},
  "backup": {"verify": "hmac", "secret": "${BACKUP_HOOK_SECRET}", "action": "agent", "trust": "owner",
             "task": "夜里的备份要是失败了，查清楚为什么再告诉我。"}}}}
```

- 进来的是别人的话。`notify`（默认）和 `card`：请求体只进一次零工具的读（和名片 Agent 一样的调用：没有工具，从不花你的 Claude、ChatGPT 套餐；模型用 `engine.use.trigger`，不写就是第一个按量计费的），照你写的 `say` 写一两句通知。`notify` 把它作为一行灰字 ⚙ 发进对话并推送（`level`，默认静默）。`card` 改成出一张收件箱卡；你点了才给 Agent 开一轮，照你写的 `task` 做，收到的内容只当资料交给它（点的时候写的那句算你的话）。以后模型看到的那份一律包成资料。
- `agent`（照你写的 `task` 叫醒 Agent）和 `job`（马上跑一个定时任务，`"job": "job-…"`）不经那一读：只给签了名的（`github` 或 `hmac`）、你写了 `"trust": "owner"` 的——你自己的脚本和设备。
- 验签，必写：`github`（`X-Hub-Signature-256`）、`hmac`（`X-Webhook-Timestamp` 写 Unix 秒，`X-Webhook-Signature: sha256=<"<时间>.<请求体>" 的 HMAC-SHA256 十六进制>`；时间差 5 分钟以上不收）、`bearer`（`Authorization: Bearer <密钥>`，给签不了名的小设备；永远不能 `trust: owner`）。密钥一律写成 `${变量名}`，值放 `secrets.env`（或者环境变量）。
- 回什么：没有这个名字、签名不对 → `404` / `401`，不说为什么；请求体超过 `max_bytes`（1 MB）→ `413`；一小时超过 `limit`（60）件 → `429`；急停 → `503` 带 `Retry-After`；收下了 → `202`，后台做。同一个投递（`X-GitHub-Delivery`、同一个签过名的请求体、同一个带时间的签名）只做一次；没做成的那件，对方再投一次会再做。`events` 和 `filters`（`exists`、`equals`、`not_equals`、`contains`、`in`、`regex`，用 `all` / `any` / `not` 组合；`field` 用点连，或者写 `event`）挡掉不要的；`coalesce` 同一个 `key` 接连来的只留最新那一下（安静 `window` 30 秒，最多等 `max_wait` 300 秒）。
- 怎么够得着：`/webhooks/<名字>` 在主服务上、不在 `/api` 下，你自己的设备和 Tailscale 能调。写了 `"public": true` 的也挂在对外的小服务上（`share.public_port`，Funnel 或反向代理指过去的那个）。服务器从不自己开 Funnel、开端口。
- `GET /api/engine/triggers` 列出有哪些（密钥只给变量名和有没有值）和最近的事件、每件怎么处理的。`PUT /api/engine/triggers/webhooks/<名字>`（上面那些字段，可以带 `secretValue`，存进 `secrets.env`）和 `DELETE` 改 `server.json`，只认你自己的设备，先备份。

### 盯着看、先跑一条命令

调模型的定时任务可以在调模型之前先看一眼（[`engine/watch.py`](engine/watch.py)，照 Hermes 定时任务的 monitor 和 wake gate）。不写这两样，任务照旧跑。

```json
{"agent": "g-garden", "when": "every 30m", "task": "告诉用户植物库存变了什么。",
 "watch": {"board": {"from": "plants"}}}
{"agent": "main", "when": "every 15m", "task": "预约名额放出来了就告诉我。",
 "watch": {"url": "https://clinic.example/booking", "match": "名额", "action": "notify"}}
{"agent": "main", "when": "every 10m", "task": "把新订单汇总一下。",
 "pre": {"script": "/home/me/scripts/new_orders.py"}}
```

- `watch` 写一样：`command`（命令行工具或白名单里的脚本，和只跑命令的任务一样）、`board`（这个 Agent 自己的看板表：`from`，可以加 `where` / `sort` / `limit`）或者 `url`。每次先看一眼，和上次看到的比（比的是输出原样的指纹：来源要输出稳定的东西；网页可以写 `match`，只留匹配这个正则的行）。第一次只记下来；没变就记一条 `unchanged`，不调模型，也不算进 `engine.jobs.daily_limit`。
- `command`、`board`（你自己的东西）变了：叫醒 Agent 跑这一次，带上变了哪些和现在的样子，当资料给它。网页（别人的字）变了：只交给零工具的那一读，照这个任务的 `task` 发一条通知，或者写了 `"action": "card"` 出一张收件箱卡；它自己从不开 Agent 的那一轮。网页只从公网取，和 `web_fetch` 工具一样。
- 看到的那份要等叫醒的那一轮跑成了才换，跑失败了下一次还认得出变了。来源读不到算这一次没跑成（照常重试、连着 5 次停用）。
- `pre` 先跑一条命令：它最后一行输出 `{"wake": false}`（Hermes 的 `{"wakeAgent": false}` 也认）就记 `gated`，不调模型；不然它的输出当资料交给模型。
- 在 app 里加（`POST` / `PATCH /api/engine/jobs`，写 `false` 去掉）。Agent 也能用 `cron` 工具提；和别的新任务一样先停着等你点头，卡上写清盯着什么、变了会怎样。

### 邮件

内核可以盯一个 IMAP 邮箱，来了新信告诉你（[`engine/mailwatch.py`](engine/mailwatch.py)，用 Python 自带的 `imaplib`，不用装别的）。不加邮箱就什么都不盯。

```json
"engine": {"triggers": {"mail": {
  "home": {"host": "imap.example.com", "user": "me@example.com", "password": "${HOME_MAIL_PASSWORD}",
           "known": ["boss@work.example", "@school.example"], "authserv_id": "mx.example.com",
           "action": "notify", "say": "告诉我是谁写的、今天要不要回。"}}}}
```

- 只读：文件夹用 `EXAMINE` 打开，信用 `BODY.PEEK` 取，所以不会标成已读，也不会移动、删除、发信。按 `UIDVALIDITY` 和 UID 记到哪了；第一次看（还有邮箱被重建以后）只记下现在到哪了，以前的信一封都不读。每 `interval` 秒看一次（默认 300，最少 60），走 `ssl`（993 端口）或 `starttls`；不加密的连法不认。
- 信是别人的话：每封新信（信头、正文、附件的名字）只进零工具的那一读，变成一条通知；写了 `"action": "card"` 就出一张收件箱卡，你点了才交给 Agent。邮件触发从不自己叫醒 Agent。
- `known` 只认你认识的寄件人：完整地址或者 `@域名`，而且要你自己的收信服务器（`authserv_id`）写的最上面那条 `Authentication-Results` 说这个寄件人的域名 `dmarc=pass`，伪造的 `From` 不算。不认识的照 `others`（默认 `ignore`，或者 `notify`）。不写 `known`，每封新信都照 `action`。
- 一次最多读 20 封（停机久了只读最新的，前面的记一条跳过了几封），一小时最多读 `limit`（30）封。到了花费上限没读成的那封，下一遍再读。急停着不连。同一个邮箱也接成了邮件对话（`channels.email` 的 IMAP host、user、folder 一样）时这个触发不跑，`GET /api/engine/triggers` 里写着为什么。
- `PUT /api/engine/triggers/mail/<名字>`（可以带 `passwordValue`，存进 `secrets.env`）和 `DELETE` 改 `server.json`，只认你自己的设备，先备份；列出来时密码只给变量名和有没有值。

### 心跳：在对话里跑、用工具回、先跑一条命令

`engine.heartbeat.<agent>` 多了三样（[`engine/heartbeat.py`](engine/heartbeat.py)）；不写的心跳照旧。

```json
"engine": {"heartbeat": {"main": {"every": "30m", "prompt": "看看今天有没有要我处理的。",
                                  "session": "chat", "pre": {"script": "/home/me/scripts/anything_new.py"}}}}
```

- `"session": "chat"`：在这个 Agent 今天的对话里看一眼，看得到今天聊了什么（默认 `isolated`：新会话，不带聊天记录）。没要说的，那两行照样删掉。
- `heartbeat_respond`（照 OpenClaw 的）：心跳开着的 Agent 每一轮都有这个工具，工具表不变；只有心跳那一轮收，而且只收一次。`notify: false` 加一句 `summary`：什么都不发，这一句下次心跳当它自己的笔记带上；`notify: true` 加 `text`：发这段。不用这个工具，`NO_REPLY` 照旧管用。
- `pre` 先跑一条命令（命令行工具，或者 `engine.jobs.scripts` 里列的脚本）：最后一行是 `{"wake": false}` 就记 `gated`、不调模型；不然它的输出当资料接在清单后面。

### 后台跑这句

`POST /api/engine/bg {"thread", "text", "label"?, "minutes"?, "model"?}`（[`engine/bg.py`](engine/bg.py)）把一句话交给后台任务，和 `tasks` 工具派的一样：任务页上看得到、能在那里改一下，做完结果作为新的一轮回到那个对话，你照常聊。是你自己要的，所以不出那张「多派一个后台任务」的额度卡；`tasks.max_minutes` 照样限它最长跑多久，急停、花费上限照样不派（回 `409` 说为什么）。只认你自己的设备。斜杠命令 `/bg <要做的事>` 调的就是这个，派的是你在的那个对话。

### 工作流

在 `server.json` 里写一次的几步，想跑就开，也能让定时任务开（[`engine/flows.py`](engine/flows.py)）。刻意做得很小：一步一步按顺序走。

```json
"engine": {"flows": {"weekly-review": {"agent": "main", "steps": [
  {"run": {"tool": "board", "args": ["rows", "query", "tasks"]}},
  {"do": "根据上面那些行，总结这周做完了什么。"},
  {"ask": "把总结发到家里的群里？"},
  {"do": "把总结发到家里的群里。"}]}}}
```

- `do` 是你写的一句要做的事：Agent 在它的对话里为这一步开一轮（前面几步的结果都在对话里；`run` 那几步的输出当资料接在后面）。`run` 跑一个命令行工具或者白名单里的脚本（和只跑命令的定时任务一样）；退出码不是 0 就停下，在对话里说一声。`ask` 出一张收件箱卡：点「接着做」往下走（点的时候写的那句交给下一步），拒绝就停。
- 每走一步都记进库。重启以后，停在两步之间、在等你点头的接着走；做到一半的那一步不自己重做（可能已经做了事），记成 `interrupted`，等你点接着走。急停让工作流停在下一步前面；`--stop-running` 停掉正在做的那一步。
- 定时任务能开它：写 `{"when": "every monday 9am", "flow": "weekly-review"}` 代替 `task`（上一次还没走完的，这一次记 `busy`）。
- `GET /api/engine/flows`（有哪些、哪里配错了、最近几次）、`POST /api/engine/flows/<名字>/start`、`GET /api/engine/flows/runs/<id>`、`POST …/runs/<id>/stop` 和 `…/resume`，只认你自己的设备。

### 长期指令

你把一块事长期交给某个 Agent（「以后周五的周报归你」「有新订单就整理一下告诉我」）时，`standing-orders` 这个 skill（[`packs/core/skills/standing-orders`](../packs/core/skills/standing-orders/SKILL.md)，照 OpenClaw 的 standing orders）告诉它怎么做：

- 先写下来，照固定的样子：能自己做什么、什么时候做、哪些还要你点头、什么时候停下来问、什么不做。写好给你看，你点了头才算数。记在以后每一轮都找得到的地方：用 `manual_manage` 提议一本世界树手册（一张卡），或者它自己工作区 `AGENTS.md` 里的一节 `## 长期指令`（模板不动）。
- 再让它真的发生：task 里写着这条指令名字的定时任务、`watch` 或 `pre`、心跳清单里的一行，或者（你来配的）webhook、邮件触发、工作流。没接触发的指令只是一句话。
- 每次都是做、核对、报告。长期指令不给额外的权限：撤不回的、对外的照样交卡给你，急停、花费上限照样管用，外面来的字从来不算指令。
- 问它「你现在长期管着哪些事」就列给你；改、撤也一样经你点头，撤掉一条连同它的定时任务、心跳那一行一起撤。

## 模型别名、记得住的冷却（自己的内核）

- **别名**（`engine.aliases`，不写就没有；[`engine/models.py`](engine/models.py)）：`{"fast": "deepseek/deepseek-flash", "smart": "moonshot/kimi-k3"}`。别名是不带斜杠的名字，指一个真的 `provider/model`（只认一层，别名不能指别名）。读模型引用的地方都先换成它指的那个（`engine.model`、`engine.models`、`engine.fallbacks`、`engine.use.*`、`/v1`、app 里换模型、`mousse agent --model`），改了别名，用它的地方一起变。指到按量模型的别名也出现在 app 的模型列表里。能不能花你自己的套餐（`may_spend`）永远照解析完的那个判：指到 `claude-code/…`、`chatgpt/…` 的别名和真的引用一样算套餐，外人回合、对外接口花不了；定时任务、心跳不认指到套餐的别名（那里写真的引用），app 里也不列它们。
- **冷却重启后还在**（[`engine/fallback.py`](engine/fallback.py)）：限流、计费错、密钥不认、型号没了的冷却以前只记在进程里；现在也存进 `<data_dir>/engine/cooldowns.json`（0600、原子写），第一次用到时读回来，过了期的扔掉。里面只有密钥的记号（变量名 + 短指纹）、给人看的 label、原因、次数、时间，没有密钥。

## 用你自己登录的 Codex 当对话模型（自己的内核）

默认关。服务器上装了 OpenAI 的 Codex 命令行、你自己登录过，再写上 `engine.codex`，app 的模型列表里就有 `codex/default`（Codex 自己默认的型号）或 `codex/<型号>`，能当对话的模型（[`engine/provider_cli.py`](engine/provider_cli.py)）。和 Claude Code 一样，只起官方的命令、用它自己存的登录：OpenMousse 不读、不存、不传它的令牌，也不把一家命令行的登录借给另一家用；环境变量里像密钥的（`OPENAI_API_KEY`……）和让它改连别处的（`OPENAI_BASE_URL`……）都去掉，它没法悄悄改用密钥。每一次回复是起一次命令，在空的 `<data_dir>/codex/<agent>/` 里跑：对话从标准输入给，它自己的 shell、文件、上网、插件、hooks 都关掉，这个 Agent 的工具只经 OpenMousse 的 MCP 给（令牌只管这一次），关卡照旧。没登录、套餐额度用完：这一次换到备用链上。别人的回合、对外接口、备用链一律不用它。它的长会话要压缩，得把 `engine.use.compact` 设成一个按量的模型。

- **Codex**（`codex exec --json --ephemeral`、只读沙箱、不读你的 `config.toml` 和 execpolicy 规则；拿 codex-cli 0.163.0 核对过）：用 ChatGPT 登录的花的是你的 ChatGPT 套餐，所以照 ChatGPT 套餐的规矩：你自己的回合能用，后台的要你在设置里同意过套餐在后台用。`codex login status` 说登录着才列出来。
- **`engine.codex`**（可选，默认关）：`true`，或者 `{"models": ["codex/default", "codex/<型号>"], "max_concurrent": 2}` 列出给哪几个、同时最多跑几个（多的排队）。
- **Copilot** 已不再支持。`copilot/…`，或者 server.json 里还留着 `engine.copilot`，都不当模型用：对话改走别的模型，配置校验和启动时提示到设置里换一个。
- **没做的**：Gemini 命令行用 Google 账号登录的那种（Google 给它的条款，2026-04-10 版，说经别的软件用它的 OAuth 算违规，举的例子就是 OpenClaw；要用 Gemini 就把 Gemini 的 API 密钥当普通的服务商加）。Cursor、Qwen Code、Kimi 命令行也没做：没找到允许这样用的条款，或者那种登录已经停了。

## Kimi 会员，Kimi Code（自己的内核）

Kimi Code 会员（`kimi/<型号>`）和开放平台按量的 `moonshot/<型号>` 分开。设置里、已登录的工具那一栏，贴一把你在 Kimi Code 控制台建的密钥，再选站点：国内（`https://api.kimi.com/coding/v1`）或海外（`https://api.kimi.ai/coding/v1`）。没选过时跟 app 的语言走（中文 → 国内）。能选的型号：`k3`、`k3-256k`、`kimi-for-coding`、`kimi-for-coding-highspeed`。哪一档会员能用哪个以会员页为准；调用报权限错时给一句看得懂的话。不做 OAuth（官方那条只给它自己的客户端）。请求头里的 User-Agent 是 OpenMousse 自己的名字和版本号，不冒充 Kimi CLI 或 Claude Code。密钥的存法和别的 API 密钥一样，只在这台服务器上，不进日志、不进报错。你自己的回合能花。后台（定时任务、心跳、后台任务）要在设置里另外同意一次才花；那一处写明条款是「仅限个人交互式使用」，后台自动跑可能被限流，甚至封号。默认不同意。替别人答的回合不花。`engine.kimi: false` 关掉；`{"site": "cn", "model": "kimi-for-coding"}` 在 app 里存之前先定站点和型号。

## 简单的一句交给便宜的模型（自己的内核）

默认关。写上 `engine.auto`，你自己说的简短、普通的一句（「谢谢！」「里斯本现在几点？」）交给便宜的模型答，别的照旧用主模型（[`engine/auto.py`](engine/auto.py)）。「简单」是：不超过 `max_chars` 个字、`max_words` 个词，最多一个换行，没有代码（反引号）、网址、图，也没有一看就是正经活的词（debug、implement、analyse、compare、写一篇、分析、为什么、代码……，再加上你自己写的 `keywords`）。只管你自己的回合（你在 app 里说的、转给 Agent 的；定时任务、收件箱、对外接口、ACP 都不动），而且这个对话用的是默认的 `engine.model`、它不是你的套餐（不按量的换成按量的反倒花钱）。这一轮第一次调模型时定下来，整轮都照它，调工具前后不变。便宜的那个还没出字就出错：照常记一次冷却，这一句和这一轮剩下的都由主模型答。回复照实记成真正回答的那个模型、照它的价算钱，app 里和备用链换过的一样标出来。
- **`engine.auto`**（可选，默认关）：`{"cheap": "deepseek/deepseek-flash", "max_chars": 160, "max_words": 28, "keywords": ["invoice"]}`。`cheap` 得是一个按量的、有密钥的模型（套餐不算）；不写它，或者写了 `"enabled": false`，什么都不变。

## 给 Claude 的提示缓存保温（自己的内核）

默认关。你自己和 Claude（Anthropic 的接口）的对话，开头会缓存 1 小时；过了这一小时再回来，整段前缀要重新写一遍缓存，按输入价的 2 倍算。写上 `engine.cache_warming`，服务器在这一小时快到的时候把这个对话最后那次请求再发一次：`max_tokens` 改成 1，别的一概不变（开头、记录、工具、缓存和思考的设置），缓存读一遍（输入价的十分之一）、再续一小时（[`engine/warming.py`](engine/warming.py)）。只保你自己在 app 里的回合（定时任务、收件箱、别人的回合、对外接口都不管），只保 Claude、而且是 1 小时缓存的，你最后一次说话以后最多保 `hours` 小时。有一次没成，这个对话就不再保温，等你下次说话；急停开着时跳过；服务重启就都忘了。每次保温各记成一轮（对话 `warm:<原来的对话>`，origin `warming`），token 和钱照实记，用量里算 `warming` 这个用途。
- **`engine.cache_warming`**（可选，默认关）：`true`，或者 `{"hours": 2, "every_minutes": 55}`（`hours` 0.1–24；`every_minutes` 5–59，赶在一小时到之前）。

## 企业云：Azure OpenAI 和 Amazon Bedrock（自己的内核）

这两家在模型目录里（[`engine/catalog.py`](engine/catalog.py)，官方页面 2026-10-19 核对过），和别的云端一样设置，地址填你自己的。都列不了型号，型号自己写。
- **Azure OpenAI**（`azure`，密钥在 `AZURE_OPENAI_API_KEY`）：v1 接口，`https://<你的资源名>.openai.azure.com/openai/v1`（或 `.services.ai.azure.com/openai/v1`），不用 `api-version`。它没有默认地址，存密钥时一起填你的资源地址（`python3 setup_ctl.py add azure --url https://<你的资源名>.openai.azure.com/openai/v1`；只有密钥、没有地址的不存，也不算连上），型号写你部署时起的名字：`azure/<部署名>`。
- **Amazon Bedrock**（`bedrock`，密钥在 `AWS_BEARER_TOKEN_BEDROCK`）：OpenAI 兼容的 Chat Completions，`https://bedrock-runtime.<地区>.amazonaws.com/openai/v1`（可以选美国东部、美国西部；别的地区改地址就行）。用 Bedrock 的长期 API 密钥，而且得是在那个地区生成的（短期的最多 12 小时就过期，这里不会自己换），型号写 Bedrock 的模型 ID：`bedrock/openai.gpt-oss-120b-1:0`。
- 价目表里没有它们的价钱，用量里只记 token、不算钱。Google 的 Vertex AI 没加：它的 OpenAI 兼容接口要的是一小时就过期的 OAuth 访问令牌，不是一把能存下来的密钥。

## 对外接口的补充（自己的内核）

都加在 OpenAI 兼容的 `/v1` 上（[`engine/openai_api.py`](engine/openai_api.py)）。每样默认关，而且要先开 `engine.api`；令牌（`api`、`api-<agent id>`）、能用的 Agent、收件箱的卡、会话都和 `/v1/chat/completions` 一样。

- **`/v1/responses`**（`engine.api.responses: true`；默认关，没开时下面这几条一律 404）：OpenAI 的 Responses API，给只会这种说法的程序。`POST /v1/responses` 跑一轮：`input` 是字或者消息的列表（`input_text` / `input_image` 块；别的 item 400），`instructions` 只管这一轮，`previous_response_id` 接上同一个会话，`store: false` 什么都不存。`stream: true` 发 Responses 的事件（`response.created` … `response.output_text.delta` … `response.completed`，或者 `response.incomplete` / `response.failed`）。用量照 Responses 的 `input_tokens` / `output_tokens` / `total_tokens`。`GET`、`DELETE /v1/responses/{id}` 拿回、删掉存下的一次，只认发它的那把令牌。存在 `mousse.db` 的 `engine_api_responses`（第一次用到时才建），每把令牌留最近 100 条，重启不丢。调用方带来的 `tools`、`temperature` 这些不管：Agent 用自己的工具和设置。
- **会话记录和 ACP 的 `session/load`**：`GET /v1/openmousse/history?id=<会话键>[&agent=<id>][&limit=200]` 回这把令牌自己的一个会话里说过的话，旧的在前，几天的接起来（用户说的、回的、思考摘要、工具调用和结果的开头）；别的令牌的会话、没有的键 404，多过 `limit`（最多 1000）就给最近的、`truncated: true`。ACP 适配器（[`engine/acp.py`](engine/acp.py)）现在报 `loadSession`：`session/load` 先把最近 500 条重放成 `session/update`（`user_message_chunk`、`agent_message_chunk`、`agent_thought_chunk`、`tool_call`），发完再回话，之后和 `session/resume` 一样接着用。
- **给你自己的 agent 用的 A2A**（`engine.api.a2a: true`；默认关，没开之前下面每一条都是 404）：每个 Agent 都是一个 A2A 1.0 的 agent（JSON-RPC 绑定），给你自己别处的 agent、A2A 客户端用，令牌同样是 `api` / `api-<agent id>`（[`engine/a2a_api.py`](engine/a2a_api.py)）。和给朋友的 `/f/a2a` 是两回事。`GET /v1/a2a` 列出这把令牌能用的 Agent。`GET /v1/a2a/<agent>/agent-card.json` 是这个 Agent 的名片（要令牌；在反向代理后面，接口地址照 `X-Forwarded-Proto` / `-Host`）。`POST /v1/a2a/<agent>` 收 `SendMessage`、`GetTask`、`ListTasks`、`CancelTask`；流式、推送、扩展名片回 UnsupportedOperation，0.3 的方法名回 VersionNotSupported。一条消息 = 一轮，同 `/v1/chat/completions`：文字（还有 raw 的 PNG / JPEG / WebP / GIF 图）是这一句，`contextId` 是会话（一直带着就接得上），同一个 `messageId` 再发回第一次的那个任务。回的总是一个任务：`TASK_STATE_COMPLETED` 的 `status.message` 和 artifact 里是回的话；`TASK_STATE_AUTH_REQUIRED` 是有卡在你的收件箱里等你点头，你点了以后再 `GetTask` 就变成 `COMPLETED`；`TASK_STATE_FAILED` 带着原因。`configuration.returnImmediately: true` 马上回 `TASK_STATE_WORKING`，之后用 `GetTask` 来问。任务存在 `mousse.db` 里（`engine_a2a_tasks`，用到时才建），每把令牌留最近 200 个。

## Agent

app 里「新建 Agent」= 在你的 OpenClaw 里建一个独立 agent：自己的 workspace（AGENTS.md / IDENTITY.md / MEMORY.md）、skills 允许列表、路由。见 [`agents.py`](agents.py)。用自带的内核（或者别的 claw）时不碰 `openclaw.json`：Agent 是 OpenMousse 数据库里的一行、同一个内核的一段单独对话，它知道这个 Agent 叫什么、管什么。命令行：

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

第一个额外的 Agent 会把只有一个 agent 的 OpenClaw 变成多 agent。这时 OpenClaw 要求 `openclaw.json` 写明 `agents.ownership: "explicit"`，也不再由「唯一的 agent」兜底，所以服务端在同一次写入里做 OpenClaw 自己的 `openclaw agents add` 会做的事：把原来那个 agent（一般是 `main`）默认管着的写明归它：`openclaw.json` 里配了的每个渠道各一条整条渠道的绑定、heartbeat、系统任务（`agents.defaults.systemAgent`）和 Talk。只靠环境变量或登录状态开着的渠道不在文件里，要自己绑（`openclaw agents bind --agent main --bind <渠道>:*`），`openclaw doctor` 会列出来。

POST 和 PATCH 校验一样：名字不能空（400），不能和别的 Agent 重名、不分大小写（409）；`icon` 是图标名，小写字母和连字符，最多 24 个字符（app 自带 moon、dumbbell、utensils、book、wallet、briefcase、heart、plane、coffee、music、camera、code、cart、home、car、paw、leaf、gamepad、palette、globe、graduation、lightbulb、trophy、pill）；`color` 只能是 cyan、gold、green、purple、pink、orange（否则 400）。

PATCH 除了数据库还动什么：

- **图标、颜色**：别的都不动。
- **名字或职责**：Agent 的 `IDENTITY.md`，让它知道。只替换 `<!-- mousse:role -->` 和 `<!-- /mousse:role -->` 之间那一段：「职责（在 app 里改的，以这里为准）」，写着名字和职责。标记外面的内容，手写的也好、agent 自己写的也好，逐字节不变。还没有这一段 → 追加在文件末尾（没有文件就新建）；新建的 Agent 一开始就有。改之前把旧文件复制到 `backup_dir`。
- **模型**：`openclaw.json` 里这个 Agent 的默认模型（`agents.entries.<id>.model`，文件别的地方不动），步骤和新建 Agent 一样：备份、写、`openclaw config validate`、不过就恢复。原来就是 `{primary, fallbacks}` 的保留回退链；原来跟着默认模型走的，抄一份默认的回退链，出错照样回退。没有自己条目的 id（比如 main）→ 400。app 里这个 Agent 的线程也换成新模型。
- 先全部校验再写文件。模型写不进去（502）时 `IDENTITY.md` 放回原样，数据库不改。

## 赛博（自己的内核，默认关）

Agent 分两类：功能 Agent 是做事的（上面那些：各管一个领域、能动手）；赛博是陪你想的：有自己的名字、形象、性格、模型、记忆，只说话。见 [`cybers.py`](cybers.py)。赛博不是 `groups` 表的一行，也不会变成 OpenClaw 的 agent：它有自己的表，内核直接跑它的赛博回合（[`engine/cyber.py`](engine/cyber.py)）。

- **默认关。** `server.json` 写 `"cybers": true`（要用自己的内核）才开；不写的话 `/api/cybers…` 一律 404，app 里不显示入口。已经在用的安装照旧关着，上线时再问开不开。
- **存了什么**（mousse.db 的 `cybers` 表）：id `cy-<8 位十六进制>`（也是它一对一对话的线程）、编号（`No. 07`，不回收）、名字、标签（通讯录和群里写在名字后面）、哪一类（`pro` 专业 / `friend` 朋友 / `machine` 机器 / `blank` 空白开始）、形象、性格三行（`identity` 身份、`tone` 语气、`rules` 规矩）、声音、模型（`provider/model`；不写 = `engine.use.cyber`，再不行 `engine.model`；你自己的 Claude Code、ChatGPT 套餐暂时不能给赛博用）、建立的时间。删除赛博时这一行和对话记录都留着，记忆移进归档，各个列表里不再出现。
- **形象**：半身像，部件 `{hair, hairColor, beard, collar, clothes, skin, bg, accColor, acc: {glasses, phones, earring, hat}}`；或者先经 `/api/chat/upload` 上传的图（`{"upload": <附件 id>}`，只存在这台服务器上）；机器类用线条图标 `{"machine": "timer" | "sides" | "rewind"}`。

| 接口 | 做什么 |
|---|---|
| `GET /api/cybers` | `{cybers}`：没删的，最近聊过的在前 |
| `POST /api/cybers` | `{name, tag?, kind?, template?, avatar?, identity?, tone?, rules?, voice?, model?, loadout?}` → `{ok, cyber}`。和别的赛博重名（不分大小写）→ 409，群里 @ 它才不会认错；`loadout` 照下面装载的规矩查 |
| `GET /api/cybers/{id}` | 一位：编号、建立时间、一起几天（`days`）、实际用的模型（`modelInUse`、`modelName`）、最后一句 |
| `PATCH /api/cybers/{id}` | 只改给了的、真变了的字段；`model: null` = 回到默认 |
| `DELETE /api/cybers/{id}` | 删除：记忆移进归档，什么都不真删 |

- **默认的两位**：赛博开着时，服务器建好 Iris（主持：会追问，一次只问一个问题，不替你下结论，收尾时理两三点）和阿哲（外行：听不懂就直说并追问，要你举例），用服务器的语言写，在服务启动时、或者 app 第一次来列赛博、通讯录时建。只建一次：删了不再建；你已经有一位同名的就不建那一位。播客的录前讨论以后用它们当主持和外行。没开赛博的安装什么都看不到。
- **一对一**：赛博的对话就是以它的 id 为名的线程。在那里 `/api/chat/send` 跑的是赛博回合，不是主人回合（`chat.engine_stream` 交给 `cybers.respond` → `engine/cyber.chat`：它自己当天的会话 `cyber:<id>:<逻辑日>`，带性格三行和你的名片）。流的事件、`messages` 里的记录、未读、置顶都和别的对话一样，app 的对话页不用改就能显示；回复的 `modelId` 是这位赛博的模型。赛博回合从不花你的 Claude Code、ChatGPT 套餐：赛博的默认（`engine.use.cyber`）是套餐的话，换成用 API 密钥的模型。
- **记忆三样**：名片（每位赛博都知道：名片 Agent 给「朋友」这一档看的那部分，比如你的名字、在做的事）、记下的（它自己长的，只记和你聊过的，每一条你点了头才记；能看、能删、能清空）、知识库（你给的资料、它读的日报，每一份写明来源）。最近的几条记下的和最新的资料放在开头的最后；更早的、和你这句话相关的笔记和片段跟在这句话后面，当资料。健康、饮食、睡眠不给赛博，它也不记这几类。接口：`GET /api/cybers/{id}/memory`（资料页的「记忆」卡）、`GET /api/cybers/{id}/notes`、`DELETE /api/cybers/{id}/notes/{条}`、`DELETE /api/cybers/{id}/notes`（清空）、`GET` / `POST /api/cybers/{id}/knowledge`（`{title, text}`，或者上传过的文档 `{file: <附件 id>}`）、`DELETE /api/cybers/{id}/knowledge/{份}`。
- **改法**：改任何一项都先给改好的样子（旧 → 新），点头才写进去，每次改记一个版本，能撤销。三条路，一个接口。**对话里说**（「以后每次推荐五个」）：赛博回合有一个只提议的工具 `propose_change`（[`engine/cyber_tools.py`](engine/cyber_tools.py)，只给赛博回合，一次回复最多 3 个），能提身份、语气、规矩、标签，或者记下一条。**它自己提**（回看、每天读）。这两条都变成挂在这位赛博对话上的收件箱卡（kind `tool`，经 [`engine/calls.py`](engine/calls.py) 的 `cyber:change`），同意就照它改一次。**资料页点一行**：`POST /api/cybers/{id}/changes`，给 `{field, value}`（写好的新的一行）、`{field, tweak}`（`stricter` 更严格 / `gentler` 更温和 / `shorter` 更简短 / `english` 用英文 / `examples` 多举例）或 `{field, ask}`（一句话说怎么改），资料页底下的输入框只给 `{ask}`（由模型挑改哪几项，最多两项，也可以是记下一条）；tweak、ask 用你自己的零工具的一问改写（`llmjson.ask`）。回的是改好的样子（`changes`，旧 → 新），不出卡；在那里用 `POST /api/cybers/{id}/changes/{改法}` `{action: apply | drop}` 定（同一个改法的卡跟着撤回）。`GET /api/cybers/{id}/changes` 列还在等你点头的（卡拒了、撤回了的不算）和最近的版本（从卡上来的带那张卡的 `inbox`）；`POST /api/cybers/{id}/undo` `{version?}` 回到上一版（只能撤一项最近的那一次；撤记下的那一条 = 删掉它）。直接用 `PATCH` 改的同样记版本。
- **自己长大（默认关）**：`"cybers": {"review": true}`（每 6 轮；`{"review": {"every": n}}`）让它隔几轮用自己的模型零工具地回头看一次最近的对话：哪句值得记、你说了什么以后语气或规矩要不要调。每个想法都是一张「旧 → 新」的卡，点了才改。这是新的后台花费，开了才有；急停时不跑。
- **装载：性格归赛博，本事归功能 Agent。** 给赛博装上一个功能 Agent 就会用，卸下就断开；数据一直留在功能 Agent 那里，一个功能 Agent 可以给几位赛博用。对话里赛博多一个工具 `ask_agent`（[`engine/cyber_tools.py`](engine/cyber_tools.py)，只给赛博回合），目标只能是它装载的 Agent。那个 Agent 自己只读地答一轮（它平时的工具，去掉转交和看你别的会话；告诉它问的是一位用别家模型的赛博，只回该知道的）：只读的结果回到对话，要改你数据的（看板、日程、目标、日志……）变成挂在赛博对话上的收件箱卡，只有你能点，同意后照原样跑一次。那一轮从不花你的 Claude Code、ChatGPT 套餐；赛博一次回复最多问 3 次，那个 Agent 也不能再转出去，只有一层。两边都留记录：功能 Agent 的对话里一行灰字「赛博 … 来问（只读）」和它的回答（不算未读、不改排序）。健康类的功能 Agent 不能装载：内建看板是 `diet` 或 `fitness`、图标是 `heart`、`pill`、`utensils`、`dumbbell`、名字或职责里有健康、饮食、睡眠的词，或者写在 `cybers.health_agents` 里（只能加，不能减）。接口：`GET /api/cybers/{id}/loadout`（`loaded`、带 `health` / `why` 的 `available`、`daily`）、`POST /api/cybers/{id}/loadout` `{agent}`（健康类 → 400，最多 6 个）、`DELETE /api/cybers/{id}/loadout/{agent}`。
- **每天读（每位赛博一个开关，默认关）**：`POST /api/cybers/{id}/daily` `{on}`。开着的赛博每天过了 `cybers.daily_at`（默认 `09:00`）读一次装载的 Agent 当天的产出（产出卡、它们的定时任务发进对话的回复），存进知识库并写明来源（「GitHub 雷达 · 10/20 日报」），再在自己的对话里起一轮和你聊读到的。每位每天一轮赛博回合，是新的定时花费；急停时不读。
- **一句话新建**（赛博和功能 Agent 走同一条路；[`agentnew.py`](agentnew.py)，同一个 `cybers` 开关）：一句话或选模板 → 两三道点选题（可以跳过）→ 全部预填的草稿 → 赛博还能试聊一句 → 创建。补问、草稿各是一次零工具的 JSON 调用（`llmjson.ask`，算你自己的：是你打的描述）；试聊是用这位赛博自己的模型零工具地问一次（从不花你的套餐）。模型没回，补问、草稿照模板和你点的答案先填一份（`fallback: true`）。模板：专业（Case 面试官 Rachel、早期投资人 Victor、质疑者「苏格拉底」、论文导师林老师）、朋友（日语语伴小林、外行朋友阿哲、树洞 Sam）、机器（出题机、反方、复盘机），外加「空白开始」。赛博的草稿：名字（重名就加个数字）、标签、哪一类、性格三行、3 个形象预设、推荐的模型和一句理由（[`cybermodels.py`](cybermodels.py)：开源的在前；专业的挑强一些的，别的挑便宜的）、建议装载的功能 Agent（最多两个，不会是健康类的）；创建走 `POST /api/cybers`。功能 Agent 的草稿：名字和图标（现有的 24 个里挑）、职责、一张表、看板起步的几块（从最近的记录 / 这周的数字 / 待办清单 / 记一条 / 一句说明里挑；查询在这里拼好，先用 `boards.plan_board` 画一遍）、提醒、主对话的模型；`POST /api/new/agent` 照每晚提议建 Agent 的那条路建（`data.create_group`、建表、换上看板），每个提醒出一张定时任务的卡，你点「开」才开。接口：`GET /api/new/templates`、`POST /api/new/questions` `{kind: cyber | agent, text, template?}`、`POST /api/new/draft` `{kind, text, template?, answers?: [{q, a}]}`、`POST /api/new/trial` `{name, identity, tone, rules, model?, tweak?: stricter | gentler | shorter | english | examples, previous?}` → `{line, tone, rules}`、`POST /api/new/agent` `{name, icon, color?, purpose, model?, table?, board?, reminders?}`。
- **赛博的模型**（[`cybermodels.py`](cybermodels.py)，同一个开关）：`GET /api/cyber-models?cyber=<id>`（新建时还没有这位就给 `?class=pro | friend | machine | blank`；`probe=1` 现探一次本机）读设置里已经知道的（`engine/setup_api.connected`：连上的每一家、探到的本机、登录着的套餐；再加 app 里能切的、别的赛博在用的），排好：开源的在前（本机的算开源、最便宜；闭源的写明它读到的会发到那一家），再按这一类赛博偏好的成本档（专业的挑强一些的，别的挑便宜的；成本档按价目表的输出价：每百万 token 不到 2 美元 = 低，不到 8 = 中，再贵 = 高）。每一行有厂商、一句用途、`billing`（`metered` 按量 / `local` 本机 / `plan` 套餐）和 `cost`；`recommended` 是前三个，各带一句理由（和新建草稿里推荐的是同一个），`local` 是你电脑上的，`channels` 是「还能接」四条（OpenRouter、本机模型、各家的 API 密钥、ChatGPT 套餐）。你自己的套餐照样列出来，但标灰（「赛博暂时不能用套餐」）；放不放开是上线时要问的，`may_spend` 不改。选好了走 `PATCH /api/cybers/{id}` `{model}`。
- **通讯录**（[`contacts.py`](contacts.py)，同一个开关）：`GET /api/contacts?q=&filter=all | cyber | agent | friend` 一次给全：顶上是管家（管家还没做好之前这一行就是你的名片 Agent，点进去到名片 Agent 页），下面赛博（最近聊过的在前，带标签和模型）、功能 Agent（带一句职责，照 Agent 栏的先后）、好友（最近聊过的在前；名片上有 Agent 的好友下面挂一行「Mia 的管家」）。`q` 在名字、标签、模型、职责里找（不分大小写）；每一项另带 `keywords`，app 打字时就地筛；`filters` 是四个筛选和各自几项。只读。
- **对话列表**：`GET /api/chats?with=cyber` 给聊过的每位赛博一行（`kind: "cyber"`，带 `tag`、`model`、`avatar`），和别的行一样排。老 app 不认这种行，不带 `with=cyber` 就不给。删赛博走 `DELETE /api/cybers/{id}`，不走 `/api/chats`。

## 群聊（自己的内核，默认关）

会议是议事（要纪要和待办，见 [`../docs/meetings.zh-CN.md`](../docs/meetings.zh-CN.md)）；群聊是拿想法和你自己的 Agent 碰一碰，要的是不同的看法。「+」里是两项。群里是你和你自己的赛博、功能 Agent（好友以后经管家来）。见 [`groupchat.py`](groupchat.py)。

- **默认关。** `server.json` 写 `"groupchat": true`（要用自己的内核）才开；不写的话 `/api/groupchats…` 一律 404，app 里不显示「新建群聊」。已经在用的安装照旧关着，上线时再问开不开。要有赛博成员，`cybers` 也得开着。
- **照会议的存法**：一个群是一个线程 `gc-<8 位十六进制>`，消息是 `messages` 表带 `speaker`、`meta` 的行（你说的 `user`；成员说的 `grava`（助手这一边在库里的内部值），`origin = group`：算未读、从不推送；系统行 `auto`）。`groupchats` 表存名字、议题（可空）、成员（赛博 `cy-…` 和功能 Agent 的 id，按发言顺序，1–8 位）和各自从哪条消息以后进的群、主持人（`main` = 助手、一位赛博的 id、空 = 不设）、发言方式（`mention` / `host`）、来源（一条想法 / 一期播客）、第几轮。`/api/chat/send` 不收群的线程（400）。
- **谁说话**：你随时说；Agent 只在被 @ 时说。@ 一位（或几位）就按先后各答一次。**@所有 Agent = 讨论一轮**，就是会议的那一轮（`meetings.run_round`，经 `MeetingRoom` / `GroupRoom` 两边共用）：按顺序各说一次，回复里的 @ 只转一层。建群时填了议题就先讨论一轮。**「主持人安排」**（`mode: host`，要有主持人）：你说了一句、没 @ 谁，主持人看要不要请一位回答（一次一位；你的一句话最多请 3 位，刚说过的不请）。不设主持人就只有 @。
- **主持人**：助手（在这个群自己的上下文里：零工具的 `llmjson.ask`，只看这个群，从不带主对话的记录）、一位赛博（用它自己的模型零工具地问一次，从不花你的套餐），或者不设。主持人只做两件事：「主持人安排」时挑下一位；每轮最后写小结。
- **成员各在自己的回合里说**：赛博走赛博回合（[`engine/cyber.py`](engine/cyber.py) 的 `speak`：从不花你的套餐；带它的装载工具 `ask_agent`；会话 `group:<群>:<赛博>`）；功能 Agent 用它自己的开头（它是谁、工作区的规矩文件、你的档案、世界树里它的枝、日记和上次的日结）、零工具，模型是它对话里选的。每次只读：这个群的设定（名字、议题、谁在、谁主持、规矩）、钉住的小结、最近 20 条（它进群以前的不给）、@ 它的那条。
- **群里只出主意。** 要动手的（加日程、改看板、设目标、给谁发消息）成员单独一行写「【要做】…」/「[To do] …」，服务器做成只有你看得到的收件箱卡（kind `other`，「来自 X · 交给 Y」）：功能 Agent 说的交给它自己，写了「交给 某个 Agent：…」的交给那一位，别的交给主对话；同意就照收件箱的老路交过去（那边在自己的对话里做，做完报 done）。赛博经 `ask_agent` 要改数据的，卡照旧挂在它一对一的对话上，群里也列出来。健康、饮食、睡眠的功能 Agent 不和赛博（包括赛博主持）同群：赛博的模型读得到群里的每一句。
- **群里的内容先进待定，不进世界树**：一段聊完（30 分钟没有新的话，或者群结束了），照名片 Agent 那条写回（[`engine/writeback.py`](engine/writeback.py)：零工具、不花套餐的一问）挑值得记的，服务器只留三项检验都过、原话真在群里的，放进 `memhold.hold(channel="group")`，写明是谁说的、原话，你逐条定。成员对你的猜测不算你说的。`engine.writeback` 关着就不挑。
- **小结写进群记忆**：讨论一轮、有主持人时，说完由主持人写这一轮的小结（助手：零工具的 `llmjson.ask`，照会议纪要的写法；赛博主持：用它自己的模型）。不覆盖上一轮，而是写进这个群的记忆（`group_memory`）：定了的（带第几轮、哪天，你说了同意的标「你同意了」）、还在争的（每一方一句；后来定下来的收起来）、各自记下的。最新的小结钉在群顶，之后每次发言（下一轮、第二天接着聊）都读它。赛博记下的照它自己「记下的」的规矩先提给你（挂在它一对一对话上的「旧 → 新」卡，`via: group`），点了才记。小结写回原处：从一条想法开的群，那条想法的笔记里有一段（`%% mousse:group <id> %%` 和 `%% /mousse:group %%` 之间，Obsidian 的注释，阅读视图里不显示），每轮换成最新的群记忆。不设主持人：没有小结，也不写回。
- **小结下的三个动作**：写回想法（没有来源的想法 → 小结记成一条新的 Zen 想法，以后写回到它）、发起会议（议题 = 还在争的，成员 = 群里能开会的，算你发起的）、做成播客（新的一期，这一轮的小结放进素材）。做过的记在小结上（`actions`），app 画成「已写回」、那场会议、那一期。
- **群结束时**主对话只收一行（「群聊「…」已结束，小结已写进群记忆：」）和下面一张小结卡（`/api/chat/cards` 里 `group: true` 的 `minutes` 卡：定了的、还在争的；点开进群）。一次小结都没写过的群不告诉主对话。删群时这一行也删。
- **用量按群按天记**（`group_usage`）：Agent 发言几次、调了几次模型、多少 token（没报用量的那几问按字数估，标「约」）。
- **对话列表**：`GET /api/chats?with=group` 给没结束的群各一行（`kind: "group"`、`members`、`faces` = 前四位成员、最新一句；有人在说时 `state` 是 working，有卡等你时是 waiting）；结束了的在 `?archived=1&with=group` 里，`endedGroups` 是条数。置顶、免打扰照常；`DELETE /api/chats/{thread}` 删群。未读算在 `/api/unread` 里。

| 接口 | 做什么 |
|---|---|
| `GET /api/groupchats?status=active\|ended\|all` | `{groups}`，每个带成员、主持人、发言方式、来源、`live`、今天的 `usage` 和 `cardsPending` |
| `GET /api/groupchats/candidates` | 新建群聊、加成员时能选谁：`cybers`（最近聊过的在前）、`agents`（`health: true` = 管健康、饮食、睡眠的，不和赛博同群）、`host`、`min` / `max` |
| `POST /api/groupchats` | `{members, title?, topic?, host?: main \| <赛博 id> \| "", mode?: mention \| host, source?: {kind: idea \| podcast, id, title?}}`；填了议题就开第 1 轮 |
| `GET /api/groupchats/{id}` | `{group, messages, cards}`：消息旧的在前（最多 500 条）；`cards` 是这个群出的收件箱卡（`groupMessage` = 挂在哪条下面） |
| `GET /api/groupchats/{id}/stream` | 接上正在进行的（SSE，同会议：`state`、`round`、`speaker`、`delta`、`said`、`system`、`message`、`done`；没有就 204） |
| `POST /api/groupchats/{id}/send` | `{text, attachments?}` → `{message, how: round \| mention \| arrange \| none \| queued}`；有人在说时只记下，还没说的成员会读到 |
| `POST /api/groupchats/{id}/stop` · `/end` · `/reopen` | 停（在说的到此为止，后面的不再请）· 结束（之后挑一遍待定）· 重新开启 |
| `PATCH /api/groupchats/{id}` | `{title?, topic?, host?, mode?, members?}`：members 是整张名单；新进来的读不到进群以前的 |
| `GET /api/groupchats/{id}/memory` | 群记忆页：`decided`（`tag` = 「第 2 轮 · 10/21 · 你同意了」）、`disputed`（带 `sides`）、`notes`、`counts`、最新一轮的 `line` |
| `POST /api/groupchats/{id}/memory/{条}` | `{action: agree \| unagree \| remove}` |
| `POST /api/groupchats/{id}/summaries/{轮}/writeback` · `/meeting` · `/podcast` | 那一轮小结下的三个动作 → `{writeback}` / `{meeting}` / `{episode}`，外加改好的那条小结 `message` |
| `DELETE /api/groupchats/{id}` | 删记录（消息、卡的关联、用量、群记忆、主对话里结束的那一行）；收件箱里的卡留着 |

## 管家（自己的内核，默认关）

管家是名片 Agent 升级来的：你还是只有一个对外身份。说话照旧是那一套（外人回合、零工具、只用这一档放出来的资料、说出去之前每句过 Doorman、要你定的出卡）；[`butler.py`](butler.py) 包着 [`cardagent.py`](cardagent.py)，多了资料、用例句代替档位表、可以请教的赛博、说过的话。

- **默认关。** `server.json` 写 `"butler": true`（要用自己的内核）才开；不写的话 `/api/butler…` 一律 404，通讯录顶上还是「名片 Agent」那一行，app 里还是「我的名片 Agent」那一页。已经在用的安装照旧关着，上线时再问开不开。
- **资料**：名字（你起的；没起就是「<你的名字>的管家」，好友看到的就是它）、形象（默认环与点，能换成捏的半身像，同赛博）、模型（设置里「按用途选」的 card：这里只读，改去设置页）、这周说了几句、Doorman 扣下几句。
- **例句**：每一档试答几句常见的问题（「这周忙吗？」「最近在忙什么？」「住哪儿？」），和真的回答同一套（这一档的资料、模型、服务端的检查），但不过 Doorman、不出卡、不发出去、不记下来；结果缓存，档位或资料变了才重答。「改这一句」= 在这一类的几种说法里挑（问日程：说具体安排 / 只说忙闲 / 不说），先给改好的样子，点头才写进 `social.tier_scopes`；每次改记一笔，能撤销。
- **固定的规矩**（不做开关）：约时间、答应别人的事、涉及钱、别人要你的资料，一律出只给你的卡；健康、饮食、睡眠不说，它也看不到。
- **请教赛博**：只问你列在名单上的。服务器先用零工具的一问把问题理成要点（去掉名字和原话，再查一遍：大段照抄的不要），再走那位赛博的回合（`engine/cyber.speak`：kind cyber、从不花套餐、零工具、它自己的一条会话），回答写明「Victor 的看法：…」。健康、饮食、睡眠的问题一律不拿去问。每次请教都记下来，只给你看（`butler_consults`）。
- **说过的话**：最近几天对外说过的每句（`card_log`），Doorman 扣下的也在，带原话和原因；「放行 / 不放」走原来那张 review 卡（`POST /api/inbox/{id}`，钩子 `cardagent.release`）。

| 接口 | 做什么 |
|---|---|
| `GET /api/butler` | 资料：`name`、`avatar`、`model`、`week {said, held}`、`rules`、`cybers`（带 `on`）、`tiers` |
| `PATCH /api/butler` | `{name?, avatar?, reset_avatar?, cybers?}`：`name: ""` = 回到默认 |
| `GET /api/butler/samples?tier=&fresh=` | 这一档的例句：`items [{key, question, category, level, reply, asks, cached, options [{level, label, example, current}]}]` |
| `POST /api/butler/samples/{tier}/{key}/preview` | `{level}` → `{change}`，带改之前（`before`）和改之后（`after`）的回答；还没写进去 |
| `POST /api/butler/changes/{id}` · `POST /api/butler/undo` | `{action: apply \| drop}` · `{change?}`（不给 = 最近改的那一笔） |
| `GET /api/butler/said?days=7` | 说过的话，新的在前：`where`（「回 Mia」）、`held`、`reasons`、`original`，扣下的在等你时带 `inboxId` |
| `POST /api/butler/said/{log}` | 扣下的那句 `{action: release \| keep}`：放行 / 不放 |
| `GET /api/butler/consults` | 请教过的：问了谁、带的要点、它怎么说 |

### 好友群（跟着管家）

你和几位好友在一个群里：消息端到端加密，你这边只出你的管家（[`friendgroup.py`](friendgroup.py)；协议见 [docs/social-protocol.zh-CN.md](../docs/social-protocol.zh-CN.md) 7.1）。

- **跟着管家开**：没有自己的键。`"butler": true` 时你的名片带 `group`，新版本的好友能拉你进群；老版本的好友只能一对一，app 在名字旁边写明为什么。
- **没有中心服务器**：你建群，你的服务器给成员表签名，每一句逐个投到每位成员的服务器：封进信封，没送到照朋友聊天那样重试最多 3 天，直连不通走中继。
- **你建的群你的管家主持**。别人的群，你说要它才进（`PATCH … {butler: true}`，群主重签成员表）。有人叫到它才答，照名片 Agent 的规矩；成员里不是你好友的，对它来说是陌生人。
- **好友的话只是资料**：不进你的主对话、不驱动任何工具。一个群安静 30 分钟，照写回挑值得记的进待定（`memhold`）；你点头之前一条都不记。

| 接口 | 做什么 |
|---|---|
| `GET /api/friendgroups` | 你的好友群，带最后一句 |
| `GET /api/friendgroups/candidates` | 能拉进来的好友（`can`；版本旧的带 `why`），和有好友在时显示的那句话 |
| `POST /api/friendgroups` | `{title, friends}`：建群（你是群主，你的管家主持） |
| `GET /api/friendgroups/{id}?after=` | `{group, messages, cards, consults}`：`after` = 只要这条之后的；`cards` = 管家在这个群里出给你的收件箱卡（只给你看），带挂在哪一条下面；`consults`（只给你看）= 管家问了哪位赛博、带的要点 |
| `POST /api/friendgroups/{id}/read` | `{upto}`：看到了这一条（`GET /api/friendgroups` 的未读清掉；不发已读回执） |
| `POST /api/friendgroups/{id}/send` | `{text}`：你说一句 |
| `PATCH /api/friendgroups/{id}` | `{butler}`：你的管家进不进别人的群（群主重签之前 `pending`） |

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

只读的卡（kind `info`，10/20 起；feature `inbox.info`）：告诉你一件事、不要你做决定的，比如每周的报告（`inbox_ctl.py add --kind info …`）。默认不推送（level `none`；写了档位的，推送上写「通知」，不写「要你点头」），也不发到 Telegram 这类聊天渠道。点了什么（app 里只有一个「知道了」，老 app 或者推送上的同意 / 拒绝 / 修改）都只是收起：卡记成 done、结果「已读」，不往任何对话发话，也就不起一轮。在「已处理」里点「跟进」照旧是你自己要接着聊，卡还是收起的，模型另外知道它只是一条通知、不用报结果。

条目：`{id, kind, source, sourceName, thread, title, why, changes: [], detail, approveLabel, fields?, status, note, result, level, createdAt, updatedAt, decidedAt, expiresAt, messageId}`。`messageId`：Agent 在一次回复里交的条目，回复结束时挂到那条回复（`messages.id`）下面；不是在回复里交的是 null。kind：task / write / send / spend / schedule / push / skill / agent / block / code / calendar / app（连接器的一次调用等你点头，见连接器） / info（只读的卡，见上） / other（exec 只来自 OpenClaw，带 `fields`）；status：pending / approved / rejected / revising / done / failed / withdrawn / expired。旧的 `/api/approvals` 接口还在，给老版本 app。

## 对话：排队、停止、引用

- 一个对话同一时间只有一条回复在跑。回复进行中你又发的（`/api/chat/send`）不再 409：先记进库（status `queued`，app 上标「排队」），SSE 先回一个 `queued` 事件，连接等着；这条回复一结束（包括被停掉），排着的几条合成一轮发给模型（编号列出，前面一句说明：分开回答时每段开头单独一行写 `> 「原话」`，app 把它画成引用、点了跳回那条），等着的连接都接到这一轮上。服务重启时库里还排着的：30 分钟以内的接着发，更早的标成没发出去。定时器这类系统触发（`/api/chat/trigger`）照旧遇忙 409；转交（`relay`）改成排队（见下）。
- `POST /api/chat/stop` `{thread}`：停掉正在进行的回复——断开到 Gateway 的连接，Gateway 就中止这一轮；已经说了的留着，末尾加「（停了）」，status `stopped`，不推送。排着的接着发。
- 长按「引用」：`/api/chat/send` 带 `replyTo`（`db<id>`）。模型另外看到原话，这条记 `reply_to`，`/api/chat/history` 里带 `replyTo {id, role, text}`，app 在气泡上面显示。
- 走 Gateway 的 WebSocket 对话通道（server.json `chat.transport: "ws"`，见 `gateway_ws.py`）时：回复进行中你又发的不排队，改成**插话**——`chat.send` 带 `queueMode: steer`，Gateway 在这一轮的下一步把这句交给模型，还是同一条回复（这条记 status `steered`，app 上标「插话」）；Gateway 没能插进去、排成了单独一轮的，接管它记成这条的回复。停止用 `chat.abort`。一轮由 Gateway 跑到底：服务重启时还没回完的，启动后接管或从 `chat.history` 补回回复。带图片的消息仍走 HTTP（照样排队）。第一次连会在本机回环地址自动配对，设备身份存 `<data_dir>/gateway-device.json`。
- **自己的内核**也一样插话（`steered`，这一批工具做完带着它再问模型）。这一轮没来得及读就结束了的不丢：排到队首，变成下一轮；2026-10-20 起这一轮自己的流（正在回的那一条的连接、并进去了的插话的连接）先推 `event: steer_missed` `{userIds: ["db<id>", …]}`，app 当场把那几条的「已插入」改成「未赶上本轮」。那几条自己的连接照旧等着、接到下一轮上。老 app 不认这个事件，跳过。
- **回复过程中的进度**（只在 WebSocket 对话通道上，2026-10-05）：同一条连接上 Gateway 的 `agent` 事件带着这一轮的准备阶段（`run_status`）、每一步工具（`item`：模型写的标题和状态）和模型的思考摘要（`thinking`）。服务器合成一份快照，经这条回复的 SSE 发 `event: progress` `{since, phase, steps: [{id, tool, detail, status: running | done | failed}], thought}`（最近 6 步、思考最后 600 字；重新接上的客户端先收到当前这份）。命令参数和输出一律不转。走 HTTP 时没有进度，app 等待时照样有动画。
- **自己的内核**也推同样的 `progress`，来自内核自己的事件（`chat.note_engine`）；2026-10-20 起每一步多几样：`label`（这一步说人话：「查看日程」）、`doing`（进行中时标题那一行：「正在查看日程」）、`note`（label 里没有的：文件名、网址的域名、技能名、应用里的哪个工具；没有是 `""`，不放命令、搜索词、提示词这些参数原文）、`wait: true`（这一步交成了要你确认的卡）（[`engine/steps.py`](engine/steps.py)）。只新增：`detail` 照旧是工具自己的标题（交成卡的后面接「· 等你确认」），老 app 照旧那样画。
- **回复过程中换了模型**（自己的内核，2026-10-20）：备用链在这一轮里换到别的模型时（[`engine/fallback.py`](engine/fallback.py)；同一个型号换一把密钥不算），这条回复的流当场推 `event: model_switch` `{from, to, reason}`（reason 照 `fallback.py`：`rate_limit`、`overloaded`、`server`、`timeout`、`network`、`auth`、`billing`、`model_not_found`、`context_overflow`、`cooldown`；换下你自己的 Claude Code 时是 `quota` / `broken` / `auth`，[`engine/owner.py`](engine/owner.py)），重新接上的客户端再补发最后一次。回完 `done` 在 `fallbackFrom` 旁边多一个 `fallbackReason`（为什么没用原来要的那个：第一次换的原因）；只在这次推送里，不进库，重读记录只有 `fallbackFrom`。老 app 两样都不认，跳过。
- `GET /api/chat/busy` → `{running, queued, idle}`。要重启服务就用 `python3 safe_restart.py --unit <服务名>`：等没有进行中的回复、没有排着的消息再重启（最多等 10 分钟），重启会掐断进行中的回复。
- **对讲**（手机 app，2026-10-19；feature `talk`）：主对话页头的对讲按钮打开一个全屏页：听你说，停顿 1.5 秒算说完，用 `POST /api/chat/transcribe` 转写（和语音输入同一个按次付费的调用），当你自己的一句话发进这个对话，回复一边流一边按句念出来（手机自带的语音；代码块、表格不念，只说一句见对话），念完接着听。点一下或者开口就打断朗读，回复照样在对话里写完。`GET /api/chat/transcribe` → `{ready, why}` 告诉 app 转写配好了没有（只看 `OPENAI_API_KEY` 在不在，不往外发）。不在后台跑：离开 app 就暂停。

## 附件

对话里发的文件（`POST /api/chat/upload`，一条最多 10 个、每个 30 MB，见 [`files.py`](files.py)）在 app 里点开就能看，不跳浏览器（[`preview.py`](preview.py)）：

| 接口 | 做什么 |
|---|---|
| `GET /api/files/{id}` | 原件。`thumb=1`：512 px 缩略图；`preview=1`（图片）：网页认得、不到 8 MB 的给原图，别的（HEIC、TIFF、BMP）转成最长边 3000 的 JPEG；`inline=1`：浏览器里直接显示而不是下载，只限图片、PDF、纯文字、音视频（HTML、SVG 永远下载） |
| `GET /api/files/{id}/preview` | 怎么显示：`view` = `image` / `pages`（PDF、EPUB、XPS、CBZ、SVG：每页宽高）/ `doc`（Markdown 和表格的块：Word、带备注的 PowerPoint、Excel 和 CSV 每张表最多 200 行 × 26 列、Markdown、Jupyter）/ `text`（代码、排好缩进的 JSON、日志、压缩包的文件列表、网页的正文）/ `audio`（带转写）/ `video` / `none`，还有「PDF · 3 页」这样的 `label` |
| `GET /api/files/{id}/page/{n}?w=1200` | 第 n 页的 JPEG，宽度往上取 800 / 1200 / 1600 / 2000，渲染一次存在原件旁边 |

渲染页面要 PyMuPDF（在 `requirements.txt` 里）；没装的话 PDF 只显示上传时抽的文字。缩略图、大图、页图带 `Cache-Control: private, max-age=604800`（文件不会变；别的 `/api` 回应照旧 `no-store`）。带透明的图转 JPEG 时铺白底（缩略图、大图、发给模型的那份都是）。

## 转交卡和任务卡

主对话把问题转给某个 Agent（`scripts/ask_agent.py` → `/api/chat/relay`），或者派一个后台任务（OpenClaw 的 `sessions_spawn`），对话里都会出一张卡。见 [`cards.py`](cards.py)。

- **转交卡**：问题一转出去就出现（「正在问 饮食记录…」，带着转过去的原话），答完变成「转给了 饮食记录 · 34 秒」，挂在发起转交的那条回复下面。点一下打开那个 Agent 的对话，定位到那个问题。转交记在 mousse.db 的 `handoffs`；「发起转交的那条回复」= relay 进来时正在回复的那个线程（脚本不知道自己在哪个会话里；几个同时在回复时优先主 agent 的线程，再取最晚开始的）。自己的内核里脚本知道（relay 带 `source`，见[看别的会话、Agent 之间转话](#看别的会话agent-之间转话mousse-agent自己的内核)）：卡照实记那个对话，Agent 也能转，Agent 那边的那一行写明是哪个 Agent 转来的。2026-10-05 起转给正在忙的 Agent 不再失败：卡上写「正忙，已排队」（`queued`），那个 Agent 一空下来（你自己在那边排着的消息先发）就转进去，排队的时间算在 relay 的 `timeout` 里；到时还没轮到，relay 回 200、`status: queued`，后台接着等，最多 15 分钟，回答进那个 Agent 的对话，卡跟着变。排着的转交算进 `/api/chat/busy`，`safe_restart` 会等它们。以前的记录里可能还有 `busy`（那时 409，没转过去）。
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
| `GET /api/tasks` | 多了 `quota`（外加今天用了多少 `tokens`），每个任务多了 `minutes`、`timedOut`、`step`，还有 `interrupted`（2026-10-20：自己的内核被服务重启打断的任务为 true；`status` 照旧是 失败） |
| `POST /api/tasks/{id}/retry` | 只有自己的内核：照原来的说明再派一个新的（被重启打断的、没做成的、到点停的、停掉的），算今天的额度，结果回到原对话。→ 新任务的 `{ok, id}`；不用重派的回 409 和原因。app 任务卡和任务页上的「重派」调它 |

转交卡：`{kind: "handoff", id, thread, messageId, createdAt, status: queued / running / done / error / busy / lost, to, toName, from, fromName, question, seconds, relayId, replyId, error}`。任务卡：`{kind: "task", id, thread, messageId, createdAt, status（进行中 / 完成 / 失败 / 已取消）, timedOut, title, deliverable: [], modelId, minutes, startedAt, finishedAt, tools, step, result, error, round, roundStatus, note, roundResult, tokens, limitMinutes, seq, dailyLimit, interrupted}`（`seq` = 今天第几个派的；`interrupted` 是 2026-10-20 加的，同 `/api/tasks`）。`deliverable` 取任务正文里「要交：」/「Deliverable:」那一行和紧跟着的列表。

## 会议

几个 Agent 围绕一个议题轮流发言，主对话的 agent 主持、写纪要，确认后待办写进日程和各 Agent 的看板；可以在 app 里手动发起（「+」→ 发起会议），也可以由主对话发起（`ask_agent.py --meet`）。每个成员在会里有自己的 OpenClaw 会话 `agent:<id>:grava:grp-<id>`（`grava` 是会话键里的内部值，老会话靠它找回来）；每轮每个 Agent 最多发言一次，@ 只转一层，一轮最多 = 成员数次回复 + 1 次纪要。见 [`meetings.py`](meetings.py)，规则、数据和接口（`/api/meetings…`、对话卡 `meeting` / `minutes`）在 [`docs/meetings.zh-CN.md`](../docs/meetings.zh-CN.md)。10/21 起（自己的内核）会议可以加赛博，「定了告诉我」（`auto`，只给你在 app 里发起的会，要开着后面的 `by_origin`）自己跑完，`GET /api/meetings/records` 列出、搜索全部会议。`"meetings": {"by_origin": true}`（默认关，已经在用的安装照旧，上线时再问）按谁发起决定会议放在哪：你发起的留在对话列表，助手发起的和全自动的开完收走（进行中时主对话那一行写「会议中」，主对话留纪要卡）。

## 日程和「要记得的」

每天一条时间线、一张「要记得的」，都能改。见 [`schedule.py`](schedule.py)。

- **日程** = 课表（可选数据源 `calendar`，只读）+ 服务器自己存的一层（mousse.db 的 `schedule_items`：用户自己的安排、各 Agent 排的）+ 当天到期的截止（带勾）。课表本身改不了，但这一层能给一节课标「不去」（`series` = 每周这节都不去）、改地点、加备注。过去的日子记实际发生的：去没去、做没做、实际几点（key 是 `<agent>:training:<日期>` 的训练，实际时间从训练数据源来）。
- **要记得的** = 课程作业（`study.deadlines_cmd`）+ 邮件里抽出来的事（下面的 `remember.mail`）+ 求职和申请的截止（`applications`）+ 用户自己加的截止。一件事只出现一次：到期那天挪进那天的时间线。分组：`security`（置顶）、`overdue`、`tomorrow`、`week`、`later`、`nodate`、`news`（没日子的钱和状态类邮件）。
- **打勾** = 做完了或不用管（`schedule_marks.done_at`）：从列表里去掉，提醒的脚本也不再提（工作区里提醒的脚本也可以读 `schedule_marks`）。邮件条目打勾同时跑 `remember.mail.cmd --done <id>`（取消：`--undo`）；改条目（`/api/remember/edit`）跑 `--edit <id> --json …`。
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
| `POST /api/schedule/mark` | 课：`{ref, skip?, series?, location?, note?, attended?, actualStart?, actualEnd?}`；邮件里写了时刻的活动（`mail:` ref）也能标，没有 `series` |
| `GET /api/remember?all=` | 要记得的，分好组（`all=1` 连打过勾的也给） |
| `POST /api/remember/done` | `{ref, done}`：打勾 / 取消 |
| `POST /api/remember/edit` | `{ref, title?, due?, detail?, type?}`：改邮件条目（或自己加的截止） |
| `POST /api/schedule/undo/{log}` | 撤销一次改动（`{redo: true}` 再做回来） |
| `GET/POST /api/schedule/feed` | iPhone 订阅：`{path, include}`；`{include}` 开关各类，`{rotate: true}` 换链接 |

`server.json` 的 `remember.mail`（可选）：`{"items": "<邮件抽取脚本写的 JSON>", "cmd": ["python3", ".../mail_digest.py"], "sources": {"<键>": "<显示名>"}, "link": "https://mail.google.com/mail/?authuser=…#all/{thread_id}"}`。

## 「今天」页

`GET /api/today?day=` 把一天按三块分好组，app 只画（2026-10-13 起；`/api/health` 的 features 带 `today`，没有的老服务器 app 用以前的页面）。三块各按一条标准，一件事只在一块里，见 [`today.py`](today.py)：

- **待处理**（要动手）：等你点头的（收件箱）、可疑安全提醒、7 天内到期的待办和截止（过了期没勾的留 3 天），按紧急程度排：待确认 → 安全 → 已逾期 → 按日子。每行一枚标签 `chip`（今天 / 明天 / 周三 / 周五截止 / 安全 / 已逾期）和色调 `tone`（bad / warn / gold / neutral）。没日子的待办单独给（`nodate`）。左滑推迟写 `today_snooze`，到那天再出现，只影响这一页（推送和起床报告照旧）。
- **日程**（要到场）：课、自己和 Agent 排的、邮件里写了时刻的活动（买了票、报了名）。同一天同一时刻、标题相近（字面重合过半，或者一边标题里的名字都在另一边的标题和正文里）或者同一个链接的并成一条，`refs` 是并进来的几条，状态成了标签 `tags`（「已购票」）。邮件只写了开始时刻、正文里写着「17:00–19:00」的用它的结束。过去的有实际时间就放在实际时间（`slotStart` / `slotEnd`，`moved` = 和原定不同）。训练计划、练后复盘的卡（`training_plan`、`training_review`）归到当天 key 为 `<agent>:training:<日期>` 的那条（`cards`）。
- **报告**（要阅读）：没归到日程的 Agent 卡，加一张邮件摘要。睡眠卡带 `sleep`（那天的恢复分、睡了多久、近 6 天的恢复分和最低的一天），饮食卡带 `meal`（哪一餐的建议、吃什么、营养合计、那天记了几条）。邮件摘要 = 钱和状态类的邮件条目（那天的；今天还加上没日子的），按类别计数：预约与提交、已购票、账户、扣费、退款、其他。
- 别的日子：待处理只列那天到期的（做没做完都列），没有收件箱、安全提醒和推迟。定时任务和后台任务不在这一页。

| 接口 | 做什么 |
|---|---|
| `GET /api/today?day=` | `{day, today, weekday, now（只在今天）, pending: {items, nodate}, schedule: {items, allDay, calendar}, reports, errors}`；不给 `day` = 今天（按 server.json 的时区） |
| `GET /api/today/range?from=&days=` | 周 / 月视图（1–42 天）：每条 `{date, type: event / deadline / todo, title, sub, start, end, allDay, done, skip, …}`，日程同样去重；不补实际时间 |
| `POST /api/today/snooze` | `{ref, until}` 推迟到 `until`（YYYY-MM-DD）；`until: null` = 取消 |

### 类别和待办（2026-10-15，可选）

`server.json` 写 `"today": {"categories": true}` 打开（不写 = 上面的老样子，`/api/health` 的 features 带 `today.categories` 只说服务器认得它）：

- **三类**：每条待办、截止、日程都有一类 `category`（`study` 学习 / `life` 生活 / `work` 工作，见 [`todocat.py`](todocat.py)），app 给圆圈、色条、周和月的小点上色，顶上能按一类筛。谁说了算：你在 app 里改的（`schedule_marks.category`）> 写入时给的（`schedule_items.category`、邮件条目的 `category`）> 来源（课表、课程作业 → 学习；申请学校 → 学习，别的求职 / 申请 → 工作；Agent 排的按它的看板：健身、饮食、睡眠 → 生活）> 字面（账单、退货、预约、旅行签证 → 生活；求职、面试、求职签证 → 工作；作业、考试、推荐信 → 学习）> 生活。
- **没日子的待办**：`schedule_items.kind = todo` 时 `date` 可以空，收在待处理末尾的「无日期」（`schedule_ctl.py add --todo --category life --title …`）。
- **过期的留着**：不再 3 天后消失。过期 1–7 天的在待处理里（`chip`「已逾期 N 天」、`overdueDays`），更早的在 `pending.stale`（app 收成一行「过期没处理 · N」），直到点了做完或「不用管」。邮件条目到期后来源会删掉，这里记一份快照（`todo_keep`）接着列，`kept: true`。
- **不用管**：`POST /api/remember/done {ref, how: "dismissed"}`，和做完分开记（`schedule_marks.resolved`），别处读 `done_at` 的照样当它处理过了。改类别：`POST /api/remember/category {ref, category}`（`null` = 回到自动分），都能撤销。
- `/api/today` 多给 `categories: {on, list}` 和 `pending.stale`，每行多 `category` / `categoryAuto` / `categorySet` / `overdueDays` / `kept` / `canDismiss`；`/api/today/range` 每条多 `category`；`/api/remember` 的 `group` 多一种 `stale`，`schedule_ctl.py remember --by-category` 按三类分开打印。

### 工程看板（2026-10-15，可选）

`"today": {"engineering": {"todos": "交接本的路径", "archive": "归档文件的 glob（可选）"}}`：把 Claude Code 的交接本（一份 markdown，`## ` 一节 = 一条线，`- [ ]` / `- [x]` = 一件事）只读同步进 `eng_lines` / `eng_items`（[`engboard.py`](engboard.py)），文件改了才重读，不加定时器。今天的报告里多一张工程看板卡（`type: eng`：这周做完 / 没做 / 等你三个数、主线几步），点进看板页。没打勾、写着要你定 / 要你动手的（等你、等 <你的称呼>、<你的称呼> 定、拍板 ·……）算「等你」，写着「→ 已搬到…」「不做了」的不算；整节挪走以后做完的照样算。

也可以改读 GitHub 的看板（Projects，2026-10-19）：`"today": {"engineering": {"source": "github", "owner": "看板的主人（用户或组织）", "number": 1, "mainline": "主线 JSON 的路径（可选）"}}`。服务器起子进程跑 `gh api graphql`（用这台机器上 gh 登录的账号，令牌不经过服务器），读一次管 5 分钟（看板页 1 分钟），gh 读不了就给上一次读到的（库里也存一份 `eng_gh`），说明是几点的。一条线 = 看板「线」字段的一个选项，一件事 = 看板上的一件。等你 → 等你；在做、等上线 → 算在做（等上线的标题前加「等上线 · 」）；排队 → 排队；这三格算三个数里的「没做」。以后不算进「没做」，单独一列，一条线只剩以后的收进「更早的线」。字段名、选项名默认是中文的那套（`fields` / `statuses` 可以改；GitHub 默认的 Todo / In Progress / Done 也认）。

| 接口 | 做什么 |
|---|---|
| `GET /api/today/eng` | `{weekDone, todo, wait, main, mainCells, mainNote, lines, older, columns}`：主线分格、各条线（做完 / 总数、状态 在做 / 等你 / 排队 / 做完）、三列（没做 / 等你 / 这周做完） |
| `GET /api/today/eng/line/{id}` | 一条线里没做的、等你的、做完的 |

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

- **一个目标** = `goals` 表一行：标题、说明、截止（`YYYY-MM-DD`、`YYYY-MM` 或「2027 春」这样的说法）、可选的数字目标（`targetLow` / `targetHigh` / `unit`，可以只给一头）、可选的 `metric`（`bodyfat` 体脂、`weight` 体重：服务端自己读当前值）、归哪个 Agent 盯（`groupId`）、`status` active / done / dropped。从不删除：「不做了」= `dropped`，在页面最下面折起来。
- **读数**：身体数据源（`xunji.py`，体重、体脂近 400 天一次查询、有缓存，`/api/goals` 和趋势共用）为主，Apple 健康的日均值（`health_metrics` 的 BodyMass / BodyFatPercentage）对照。当前值取两边最新的那次（同一天以身体数据源为准）。体脂从不自动算，只读记下的。进度从起点（设目标那天或之前最近的一次读数）到目标区间，往下往上都行，进了区间 = 100%；读数超过 30 天算 `stale`。
- **每次改动**记一行 `goal_log`（谁、之前 / 之后）和一行活动记录。撤销只改回这次动过、之后没人再改过的字段（`kept` 是没动的那些；全都后来改过 = 409）；撤销「加了」= 把目标藏起来。Agent 24 小时内的改动、用户还没点「知道了」的，在 `recent` 里：app 目标页顶上一条，能撤销。
- **Agent** 用 `goals_ctl.py`（`list`、`trend`、`log`、`add`、`update`、`done`、`drop`、`reopen`、`undo`；`--source` = 谁在改）。skill（`packs/core/skills/goals`）：用户让改的直接改；它自己的主意先交收件箱；用户没说过的目标不编。

```bash
python3 goals_ctl.py list [--all]
python3 goals_ctl.py add --title "体重降到 65 kg 以下" --category 健康 --metric weight --low 60 --high 65 --due 2026-12-31 --agent fitness
python3 goals_ctl.py update goal-1a2b3c --low 18 --high 22
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

## 饮食记录

吃了什么记在服务自己的库里（2026-10-13 起；以前从训记读）。见 [`diet.py`](diet.py)、[`nutrition.py`](nutrition.py)，Agent 用 [`diet_ctl.py`](diet_ctl.py)（没有 shell 的走 MCP 的 `diet` 工具），用法在 [`../packs/diet/GUIDE.md`](../packs/diet/GUIDE.md)。

- **三张表**：`diet_foods` 个人食物库（每 100 g，饮品每 100 ml；常用份量「1 个（去壳）= 65 g」；来源 xunji / off / cofid / usda / label / manual / estimate 和出处）、`diet_entries` 吃下去的一样一行（日期、餐次、时间、份量、这一样的营养和记下时的每 100 g 快照、`source` = plan 按建议 / chat 对话 / photo 照片 / manual 手动 / xunji 导入、`estimated`、同一次写入共用的 `batch`）、`diet_targets` 营养目标（按生效日期一行；某天的目标 = 那天及以前最近的一行）。删一律是软删。
- **查营养的顺序**（`/api/diet/lookup`）：个人食物库 → 包装食品查 Open Food Facts（条码走 API v3，名字走 Search-a-licious，优先英国在售的；或用户拍营养标签）→ 英国 CoFID 2021（2,887 种通用食物，第一次查时从 GOV.UK 下载 Excel 导入本地表）→ USDA FoodData Central（`.env` 里有 `USDA_FDC_API_KEY` 才查）；都没有由 Agent 估算并标「估算」。外部来源的结果缓存在 `<data_dir>/nutrition.db`（删了能重建）。Open Food Facts 是 ODbL：查过的只缓存在本机、食物库里记着出处，不对外分发合并后的库（分享、名片 Agent、A2A 都不带它）；User-Agent 只写项目名和网址。
- **一键「记录这一餐」**（`POST /api/diet/log-plan`）：按饮食建议卡（`feed_items` 的 `meal_plan`）的 `data.meals` 记，`meals` 选第几餐、`scale` 整餐按比例、`items` 单样改份量；同一张卡的同一餐不重复记（409）。老卡每一样只有热量和蛋白质时，整餐的碳水 / 脂肪按食物库里同类的比例分到每一样（不超过它自己的重量），整餐合计和卡片一样。
- **数据源**：`server.json` 的 `diet.source` = `local`（默认）或 `xunji`（还从训记读，迁移前用）。`/api/diet/day` 两种都给同一个结构（看板、今天页不用改）；写入只写这里的表。训记里的历史用 [`diet_import.py`](diet_import.py) 一次性导进来：只读导出 → 导入（训记的 `record_id` 是食物编号、不唯一，键用 日期:餐次:record_id#第几次）→ 逐天核对合计 → 迁移报告。

```bash
python3 diet_ctl.py day
python3 diet_ctl.py lookup 香蕉 --en "bananas flesh only"
python3 diet_ctl.py food-save --from cofid:14-318 --name 香蕉 --portion "1 根=120"
python3 diet_ctl.py add --meal 早餐 --item "香蕉=120" --item "鸡蛋:1 个（去壳）×2" --commit
python3 diet_ctl.py log-plan --meal 晚餐 --set 0.1=100 --commit
python3 diet_import.py export && python3 diet_import.py import --file <导出的文件> --commit
python3 nutrition.py cofid-import [--file 本地的 xlsx]
```

| 接口 | 做什么 |
|---|---|
| `GET /api/diet/day?date=` | `{source, date, totals, targets, item_count, meals: [{label, key, time, kcal, protein, carb, fat, items: [{id, name, amount, unit, kcal, protein, carb, fat, time, source, estimated}]}]}`（训记模式没有 key 之后的那些） |
| `GET /api/diet/days?from=&to=` | 逐天合计（有记录的天）和那天的目标 |
| `GET /api/diet/entries?date=`（或 `from` / `to`） | 每一行，带 id、每 100 g 快照、来源、批号 |
| `POST /api/diet/entries` | `{date?, meal, time?, source, items: [{food \| name, amount, unit, portion, count, per100, kcal/protein/carb/fat, estimated}], commit}`；`commit: false` = 预览 |
| `PATCH` / `DELETE /api/diet/entries/{id}` | 改份量（按快照重算）/ 餐次 / 时间 / 日期；删 |
| `POST /api/diet/undo` | `{batch}`：撤销同一次写入的几样 |
| `POST /api/diet/log-plan` | `{card?, meals?, scale?, items?: [{meal, item, amount}], date?, time?, again?, commit?}`；返回 `batch`、`entries`、`day`（合计和还差多少） |
| `POST /api/diet/log` | `{feed, meal?}`：同上的简写（产出小板子用），记这张卡的第几顿（从 0 数，不给 = 第一顿还没记的） |
| `GET /api/diet/foods?q=&recent=` · `POST` · `PATCH` / `DELETE /api/diet/foods/{id}` | 个人食物库；`POST {from: {source: off \| cofid \| usda, ref}}` 直接存 lookup 的结果 |
| `GET /api/diet/lookup?q=&en=&barcode=&packaged=&sources=&all=` | `{best, results, tried, estimate}`：按顺序查，`tried` 说每个来源查了没有、为什么跳过 |
| `GET /api/diet/library?days=` | 常吃的：次数、常用份量、常在哪餐 |
| `GET` / `PUT /api/diet/targets` | 现在的目标和历史；`PUT {protein, carb, fat, kcal?, water_ml?, from?, note?, day_type?}` 或 `{clear: true}`。`day_type` = train / rest：碳循环里训练日 / 休息日那一套（同一天没写另一套就沿用之前的另一套）；那天的目标带 `day_type`、`day_type_by`（set / schedule / default）、`cycle` |
| `PUT /api/diet/day-type` | `{date?, day_type: train \| rest \| null}`：标某天是训练日还是休息日；不标就看日程里有没有排训练（键带 `:training:`），没有算休息日 |
| `GET /api/diet/nutrition-sources` | 各来源的状态（CoFID 导没导、缓存了多少、USDA 有没有 key） |

## 看板、功能包和提醒

每个 Agent 有自己的表，看板上的积木由它自己摆；app 只按服务端算好的数据画七种积木，不自己算。见 [`boards.py`](boards.py)、[`packs.py`](packs.py)、[`alerts.py`](alerts.py)，Agent 用 [`board_ctl.py`](board_ctl.py)。

**集合块**（积木 type `collection`，2026-10-13，[`views.py`](views.py)）：参照 Notion「同一份数据，多种视图」，一张表配几个视图，app 顶上一条切换条：表格（列、排序、筛选）、看板（按 choice / bool / text 字段分列，卡片拖到别的列 = 改这个字段）、日历（按日期字段放进月格，点的颜色按 `group`）、时间线（从开始到日期的条，窗口和刻度服务端算好）、图表（`charts` 里 1–6 个：数字、柱状、横条、折线、环形）。视图的写法沿用查询 Q 的词：`where` / `sort` / `group` / `date` / `field` / `agg`（也认 `group_by` / `filter` / `date_field` / `value_field`）；卡片 `card {title, meta, tags}` 用模板。视图存在看板配置里，所以按版本、提案 → 预览 → 确认、撤回、改动记录都照旧；Agent 用 `board_ctl.py view list / show / add / update / remove / default`（`--check`、`--propose`）。不认识的视图种类 app 显示「需要更新 app」。

| 接口 | 做什么 |
|---|---|
| `GET /api/boards/{agent}` | 现在的看板：`blocks`（Agent 的积木和数据）、`sections`（内置看板各节的显示顺序，`{id, title, hidden}`）、`collections`、`strip`（Agent 改过、用户还没点「知道了」时的撤回条） |
| `PUT /api/boards/{agent}` | 整份换一版 `{blocks, sections?, note, mode: apply / propose, by}`。`sections`（内置小节的顺序、藏没藏）不给就沿用现在的，Agent 加一块不会把用户挪过、藏过的冲掉 |
| `GET /api/boards/proposal/{inboxId}` | 看板提案（kind `block`）的预览：改了的块，用现在的数据画 |
| `POST /api/boards/{agent}/view` | 集合块的一个视图按你点的重算（只读，feature `boards.views`）：`{block, view, sort?, where?, month?, column?, offset?, limit?, full?}`；带 `chart` + `bucket` 返回图表上点开的那一段有哪些行 |
| `POST /api/boards/plan`，`PUT` / `GET /api/boards/plan/{inboxId}` | 新 Agent 的方案 `{tables（可以带示例行）, blocks}`：在内存里校验、画出来，建 Agent 的卡在 Agent 还没建时就能看看板（`inbox_ctl.py add --board-file`） |
| `GET /api/packs`，`GET /api/packs/{name}` | 功能包（`packs/<名字>/pack.json`：表 + 积木 + 提醒 + `GUIDE.md`），以及装在哪些 Agent 上 |
| `POST /api/packs/{name}/install` | `{agent, mode: apply / propose / check}`：没有的表建上，已有的只补缺的字段和选项，看板上已经有的块不重复加；propose 走收件箱，预览是装好以后的看板。包里的提醒另外各出一张卡 |
| `POST /api/packs/{name}/remove` | 看板上拿掉这个包的积木（表和数据留着） |
| `GET /api/alerts/{agent}`，`POST /api/alerts/item/{id}` | 开着和暂停的提醒；暂停 / 恢复 / 删掉 |
| `POST /api/alerts/{agent}/check`，`POST /api/alerts/{agent}/propose` | 一条提醒规则 `{id, title, source（和列表一样的查询）, row, message, at, days, level}`：check 看今天会推什么；propose 出一张 kind `push` 的收件箱卡（新推送一律要用户点头）。同意后服务端每 30 秒看一次，到点查出有东西就推一条（服务器没开着，3 小时内补推），点开进这个 Agent 的看板 |

## 页面（Agent 写的网页）

积木画不了的（能拖、能实时计算的互动，自定义的图，一次性的分析页），Agent 写一份网页，app 放进盒子里显示（2026-10-13，[`pages.py`](pages.py)，Agent 用 [`page_ctl.py`](page_ctl.py) 或 MCP 工具 `page`，写法见 [`packs/core/skills/page`](../packs/core/skills/page/SKILL.md)）。思路和 Claude Artifacts、OpenClaw 的 `show_widget` 一样，接的是 OpenMousse 自己的数据和界面：

- **版本**：同一个 Agent 同一个 `name` 再发 = 新的一版，id 不变；内容和要读的数据都没变就不加版本。
- **盒子**：app 拿到的文档服务端包好：最前面一条 CSP（`connect-src 'none'`、不能嵌页面、不能提交表单，脚本和样式只认 jsDelivr / cdnjs / unpkg，字体可用 Google Fonts），然后是主题色（CSS 变量，跟 app 的深浅色）和桥。网页版放进 `sandbox="allow-scripts"` 的 iframe（srcdoc，不透明源）：页面读不到 app 的令牌、登录和别的数据，也调不了 `/api`；网页版外层的 `frame-src 'none'` 让它没法带着数据跳到别的网址，跳了 app 就停掉它。手机原生包没有 WebView，页面暂时只在网页版里显示。
- **桥**：页面里 `mousse.data(名字)` 读数据、`mousse.prompt(文字)` 把一句话放进这个 Agent 对话的输入框（你按发送才发）、`mousse.page` 是页面信息（id、标题、第几版、今天的日期）。经 postMessage 交给 app，app 只认这个 iframe 发来的、限频，再用自己的令牌调服务器。
- **数据和授权**：能读什么发布时写在 `caps.data`：名字 → 看板的查询 Q（`from` / `where` / `sort` / `limit`，最多 1000 行），只能读这个 Agent 自己的表和只读的系统来源 `health:*`，只读。声明了数据的页面第一次打开时 app 问你（哪些表、哪些字段），决定绑这一版的 digest（HTML + caps 的哈希）：内容改了要重新确认。不同意也显示，读数据回 `not_granted`。
- **对话卡片和看板**：Agent 在哪次回复里发的版本，挂在那条回复下面（`/api/chat/cards` 里 kind `page`）；页面页的「钉到看板」在这个 Agent 的看板最后加一块 type `page`（看板改动照常有记录、能撤销）。

| 接口 | 做什么 |
|---|---|
| `POST /api/pages` | Agent 发布 `{agent, name, title, summary?, html, caps?, note?, check?}`：先查大小（256 KB）、外部地址、内联脚本语法（`node --check`）、数据查询；`check: true` 只查不存 |
| `GET /api/pages?agent=`，`GET /api/pages/{id}` | 页面列表、一个页面（第几版、`needs` 要读的表和字段、`grant` 你的决定） |
| `GET /api/pages/{id}/doc?theme=dark\|light` | 盒子里显示的文档（`raw=1` 回 Agent 发的原文，`page_ctl get` 用） |
| `POST /api/pages/{id}/grant` | `{digest, decision: allow / deny}`：只认当前这一版，页面刚更新过回 409 `stale` |
| `POST /api/pages/{id}/data` | `{name, digest}`：页面经桥要的一份数据；没允许 403 `not_granted`，版本对不上 409 `stale` |
| `GET /api/pages/{id}/preview-data?name=` | Agent 看自己声明的数据现在读出来什么（`page_ctl data`，不经你的授权，app 不调） |
| `POST /api/pages/{id}/pin`，`DELETE /api/pages/{id}` | 钉到看板（回 `undoTo`，撤销 = `POST /api/boards/{agent}/revert`）；删除（对话卡片和看板积木显示「已删除」） |

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

对话列表（2026-10-13，`chats.py`，能力名 `chats.list`）：`GET /api/chats` → `{agents, rows, ended, archived}`，给「对话」tab 用。`agents` 是每个 Agent `{id, name, icon, color, purpose, line, lineRole, at, unread, pinned, muted, state}`；`rows` 是主对话（永远第一行、算置顶、不能删）、会议和 Agent 群（kind `meeting` / `room`，来自可选的 `meetings` 模块：`chat_rows()`、`delete_meeting(id)`）和没归档的项目；`ended` / `archived` 是已结束的会议、已归档的项目各几条（`GET /api/chats?archived=1` 列出来）。排序和未读只算你的话和给你的回复（`role=user`，或 `role=grava`（助手的话在库里的内部值）且 `origin=user`）：定时器、收件箱、主对话转来的回复不改排序、不算未读。`state` 是 `working`（正在回复，或它派的后台任务还在跑）或 `waiting`（收件箱里有 thread 或 source 是它的条目）。`PATCH /api/chats/{thread} {pinned?, muted?}` 把置顶、免打扰存进 `chat_prefs`；免打扰的线程不推送，回复不算进 app 图标的角标（`/api/unread` 里那一项带 `muted`）。`DELETE /api/chats/{thread}` 删项目或会议（Agent 走 `DELETE /api/groups/{id}`）。好友：`POST /api/friends/{id}/clear` 删除和这位好友的聊天记录，只在你的服务器上（能力名 `friends.clear`）。

## 学习台

`/study` 是给电脑用的宽屏页面：左边是课程和模块，中间看学习页、课件（PDF）、闪卡、小测，右边就着这一节的材料提问。在 `server.json` 的 `study` 里配置（见 [`study.py`](study.py)）：

- `materials`：课件目录，一门课一个文件夹，下面一层是模块（周 / session），模块里放文件。按模块下载的课程平台镜像（比如 Canvas）正好是这个结构。
- `pages`：学习页目录，一门课一个文件夹，Markdown，开头 YAML front matter（`session`、`title`、`sources` = 相对课程文件夹的课件路径）。学习页挂在第一个来源所在的模块下；视频放 `<课程>/media/`，文件名 `S03 ….mp4`。
- `courses`（可选）：显示哪几门、什么顺序。`deadlines_cmd`（可选）：打印 `{due, course, title, url}` JSON 数组的命令，显示在顶栏。
- `readings`（可选）：阅读清单目录，一门课一个 `<课程>.json`：`{"items": [{title, kind, required, instructions, sessions, file, status, url}]}`，`file` 相对这门课的课件目录。每节多一个「阅读」标签；必读材料没到手时，生成学习路线 / 闪卡 / 小测前会先提醒你补齐。
- `recordings`（可选）：录播字幕，`<课程>/index.json` 列出录播（`id, name, start, duration, sessions, file`），`file` 里是 `{viewer_url, segments: [{t, text}]}`。每节多一个「录播」标签（带时间点的字幕，点时间跳到录播那一刻），字幕也会放进问答前情。学校的录播规定要求保密的话，字幕只留在自己的服务器上。
- `video_cmd`（可选）：渲染命令（argv 列表，`{script}` = 助手写的 Manim 脚本，`{media_dir}` = 工作目录）。配了以后每节多一个「做视频」：助手写脚本、服务器渲染，出错把报错交回去改一次。这会在本机运行助手写的代码，只在已经信任助手能在这台机器上执行代码时打开。

课件和学习页的原件（`GET /api/study/file?course=&path=&where=materials|pages`）原样内联给浏览器，一律带 `X-Content-Type-Options: nosniff`。这些文件是课程平台、网页来的或 Agent 写的：网页、SVG、XML 再加 `Content-Security-Policy: sandbox`（不带 `allow-scripts`），直接打开地址也是不透明源、不跑脚本，碰不到网页版存的令牌和 `/api`；学习页里用 `<img>` 嵌的 SVG 照常显示。对话附件的原件守同一条规矩（`files.untrusted_headers`）。

每个学习页打开先看到**学习路线**：按这一节的全部材料排 5–8 步（做什么、看课件哪几页 / 学习页哪一节 / 哪篇阅读 / 录播哪个时间点、大概多久），每步可以打勾，进度存在服务器上，目录里也显示。

**复习**：播客的费曼讲错和漏了的，点一下加进这门课（`POST /api/study/review {course, page?, items, …}`，存在 `pages/<课程>/.gen/review.json`，挂在那一节上）；学习台打开那一节时顶上一个「复习」框，点「复习过了」划掉（`POST /api/study/review/done`）。`GET /api/study/review?course=&page=&all=`。

提问走和 app 同一条对话通道，每个学习页一个线程；每天第一个问题会带上学习页、课件全文、录播字幕和阅读材料（放得下的放全文，放不下的给路径），以及你正在做学习路线的第几步。闪卡、小测、学习路线存在学习页旁边；OpenClaw 开了 llm-task 时它们走零工具的一问一答（课件原文不进带工具的回合），没开就在这一节的生成线程里问。

`materials` 和 `pages` 没配时默认放在数据目录下（`<data_dir>/study/materials`、`pages`），安装器也按这个建好目录。

### 加一门课（课程档案）

学习台左栏「+ 加一门课」是五步向导，右边一直开着**学习 Agent** 的对话（`study.agent`，和 app 里这个 Agent 的「对话」是同一个线程）：说哪里不对它就用 `study_ctl.py` 改，改过的标黄、能撤销。每门课一份 `pages/<课程>/course.json`（见 [`coursefile.py`](coursefile.py)）：

1. **说说这门课**：课名、学期、考试怎么考、喜欢怎么学、还想说的（每次写学习页都带上）。
2. **你手上有什么**：大纲（文件、网页链接或对话里发的附件）、课件文件（文件或 zip）、Canvas 个人令牌、还没有；按选的课程网站写去哪拿、拿什么。大纲交给模型（零工具的 llm-task）读出每一节的日期、主题、要读的，作业和考试，看不清的列成要问的。
3. **核对每一节**：点格子改或者跟右边说；核对完才建每一节的文件夹，作业和考试进「今天」、要记得的、顶栏和学习 Agent 的截止表。
4. **放材料、查齐**：按文件名归节（Session / Lecture / 第 N 讲 → 那一节；Week N → 那一周，一周几节或者和主题对不上就问；阅读清单对得上的记成那篇阅读），zip 拆开；每节看课件、阅读几篇到手、有没有录播字幕（`.vtt` / `.srt` 放进那一节就行）；缺的可以补、可以明着跳过。
5. **生成**：只生成材料齐了的节，一次一节：学习页（固定的几节：这一节在回答什么 / 课上重点（有字幕才写）/ 核心概念 / 框架 / 阅读要点 / 题型 / 易混点 / 自测题（`**Q1.**` + `<details>`，「自测」标签靠它拆题）/ 可做视频的概念）→ 学习路线 → 闪卡、小测（→ 视频）。写好一节在学习 Agent 的对话里记一行灰字。

已有的课件文件夹（比如课程网站镜像下来的）可以直接拿来建课：按模块名认出每一节，已有的学习页按 `session` 挂上。没有档案的课照旧能用。

| 接口 | 做什么 |
|---|---|
| `GET /api/study/courses` | 所有的课 + 课件目录里还没当成课的文件夹 + 学习 Agent |
| `POST /api/study/courses` | 建课 `{name, title?, term, exam, learn, notes, platform, adopt?}` |
| `GET / PATCH / DELETE /api/study/courses/{课}` | 一门课的全貌（每一节查齐的结果、标黄、生成进度、待归节的文件）/ 改档案 / 删课（撤不回：出收件箱卡，点了同意整门课挪进 `<data_dir>/study-trash/`） |
| `POST …/syllabus`、`…/syllabus/from`、`GET …/syllabus` | 传大纲文件 / 链接、附件、文字 / 读到哪了 |
| `POST / PATCH / DELETE …/sessions[/{id}]`、`…/readings`、`…/deadlines`、`POST …/questions/{id}` | 加一节（后面顺延）、改、去掉；阅读（`skip` = 先跳过）；作业和考试；回答要问的 |
| `POST …/confirm` | 核对完 |
| `POST …/files`、`…/files/from-uploads`、`…/files/assign`、`DELETE …/files` | 传文件 / 把对话附件放进来 / 归到某一节 / 删待归节里的 |
| `GET …/check`、`POST / GET …/generate` | 每节缺什么；生成（`force` = 材料没齐也写） |
| `POST …/undo/{改动号}?redo=`、`GET …/changes` | 撤销 / 重做（只撤后来没再改过的地方），最近的改动 |
| `POST /api/study/canvas/connect`、`POST …/{课}/canvas` | Canvas 个人令牌（存 `~/.openmousse/canvas-tokens.json`，0600，只发给那个 Canvas）；把这门课的课件和作业同步进来（点一次同步一次） |

学习 Agent 在对话里改课用 [`study_ctl.py`](study_ctl.py)（skill `study`，MCP 工具 `study`）；它在回复里改的，回复下面出一张「改了课程结构」的卡（`/api/chat/cards` 的 kind `course`），能撤销。

### 接到 app 上

- 学习 Agent 的看板是学习看板（`groups.dashboard = study`，`POST /api/study/agent {agent}` 设）：顶上「学习台」卡（`GET /api/study/home`：复习几条、接着学哪一节第几步、下一个截止、每门课学完几节），下面是这个 Agent 自己的积木。新建 Agent 选「从例子开始 → 学习」（`POST /api/groups {…, template: "study"}`）：带学习看板、学习功能包（`packs/study`：截止表和学习记录）和 study skill。
- 手机原生学习屏：`GET /api/study/outline?course=`（每一节的状态）、`GET /api/study/unit?course=&page=`（一节的课件、阅读、录播、学习路线和打勾、复习），课件用和对话附件同一套预览（`/api/study/file/preview`、`/api/study/file/page`，页图缓存在课程的 `.gen/preview`）。
- 电脑上打开：`POST /api/study/login-link {server}` 出 `<服务器>/study#pair=<配对码>`（10 分钟、一次），浏览器打开就换一个自己的令牌。
- 自测写的答案、判的对错存服务器（`GET / PUT /api/study/self`，`pages/<课程>/.gen/self.json`），手机电脑共用；「没答上」一键进复习（`POST /api/study/review/add`）。
- 推送（都默认关）：`study.notify_generated` = 写好一节静音推一条；`study.notify_ready` = 课程开了「新的一节材料齐了提醒我」时，材料齐了静音推一条。

## 世界树

app 里的「我 → 世界树」。你的各个 AI（Claude、ChatGPT、Gemini、Claude Code 和你自己的 Agent）共用的记忆：主干是档案，枝按生活领域分、每个 Agent 一根，叶子是一条条记忆。真身是 workspace 的 `memory_tree.py`，或者安装器装的开源版世界树（[`../tree/`](../tree/) 的 `openmousse_tree`，经 [`treelib.py`](treelib.py)）；[`sources.py`](sources.py) 选一份，`tree` 工具和 Zen 的「想完了」用的也是这一份。索引、写入、遗忘都归它管；这个服务只读它，再把动作转过去（可选数据源 `tree`：两份都没有时两个接口都回 `ok=false` + `missing_source: tree`）。见 [`memtree.py`](memtree.py)。

开源版世界树：新建的 Agent 会长一根自己的小枝（`agents` 填它的 id，内核据此给它看自己那几根枝的叶子），老的 Agent 在服务器起来时补上。**每周修剪**（[`treeprune.py`](treeprune.py)，只在自己的内核 builtin 下）：主对话的一条定时任务，默认每周日 19:00，照 `tree-prune` skill 先 `tree prune-context`，再用 `tree prune-plan` 交计划，变成一张静默的收件箱卡（kind `tree`，一周一张，一周后过期）。点了同意服务器照计划修剪完、卡片标完成；拒绝 = 计划作废。`server.json` 里 `"tree": {"prune": {"when": "every sunday 19:00"}}` 改时间，`"tree": {"prune": false}` 关掉。

**手册**（世界树 0.6，自己的内核 builtin 下）：想让各处照同一套做的做法（怎么写文书、怎么记训练、怎么准备会议）在世界树旁边只存一份，叫手册（`mousse-tree manual …`，见 [`../tree/README.zh-CN.md`](../tree/README.zh-CN.md)）；别处只留四行指针（`mousse-tree manual pointers`）。你说的每一句，内核先按这句话（加上上一句）搜你的手册和这个 Agent 的技能，只把命中的正文接在这句话后面（[`engine/manuals.py`](engine/manuals.py)）；会话开头的技能目录只留名字。每一轮记下真读到了什么（手册、技能、世界树的叶子；[`engine/used.py`](engine/used.py)）：回复带 `used` 和一行「按『写文书』手册 · 用了 2 片叶子 ›」。Agent 想改手册用 `manual_manage`（[`engine/manual_write.py`](engine/manual_write.py)）：在记性的闸里手册是「做法」，先出一张卡给你点头，改之前留一版；经 `tree` 工具只能读。每周修剪的卡多一节「这个月没用上的」：没人用上的手册和你自己的技能，默认不动；同意 = 改说明（另出一张卡，名字和「什么时候用」改成平时说的话），在那一条上「修改」写「停用」= 停用。`"engine": {"manuals": false}` 换回以前的做法；别的设置见 `engine/models.py`。

| 接口 | 做什么 |
|---|---|
| `GET /api/tree` | `branches` 按先序排（大枝后面跟着它的小枝；`leaves` = 直接挂在上面的，`total` = 连小枝一起的，`agents` = 默认挂到这里的 Agent）、`leaves`（当前的：active 和 pending，不含档案要点；`source` 是笔记里写的，`origin` 是最早记下它的平台，每周修剪改写过的顺着 `supersedes` 找回去）、`trunk: {name, count}`（档案）、`counts: {total, pending, bySource}`（按 origin 数）、`issues`（格式不对、先跳过的笔记数） |
| `POST /api/tree/{id}` | `{action: confirm}` 待确认 → 当前；`{action: forget}` 笔记掏空成只剩属性的空壳挪进归档，索引里删掉，开源版世界树还回 `profileLines`（档案里修剪时从它并进去的句子；`alsoProfile: true` 一起删）；`{action: move, branch}` 挪到别的枝（写主干的名字 = 直接挂主干）。活动记录由世界树自己写，不含记忆内容 |
| `GET /api/tree/prune` | 最近一份修剪计划（每一步、做没做）和它的卡 |
| `GET /api/tree/prune/context` | 这周可剪的（和 `tree prune-context` 一样，JSON） |
| `GET /api/engine/used/{一轮}`、`GET /api/engine/used?message=db123` | 这一轮用到的：`used`（手册、技能、用法的名字，几片叶子，那一行 `line`）和 `items`（每份手册、技能的「什么时候用」和说明，每片叶子的那句话、在哪一枝） |
| `GET /api/engine/skills?agent=main` | 这个 Agent 的技能、手册、功能包的用法，带 `lastUsed`、`idleDays`、`stale`（`engine.manuals.stale_days` 天没用，默认 14；只标不删） |

## 连接

「我 → 连接」：助手接着的每一样东西，现在怎么样。`GET /api/connectors[?fresh=1]` → `{groups: [{id, title, items}], counts: {ok, warn, off}, checkedAt}`；每一项 `{id, name, icon, status: ok | warn | off, line, facts: [{label, value}], uses, fix, open}`（`open` = app 里能跳去的页）。整份结果按语言缓存 60 秒（`fresh=1` 跳过），聊天渠道的在线状态（`openclaw channels status`）缓存 2 分钟。每一项都是尽力而为，不返回任何密钥：只看密钥的名字在不在、文件的时间、条数和 systemd 单元的状态。你的机器上没有的（脚本、单元、目录）那一项就不出现；自带的功能还没用上的（Apple 健康、日历订阅、推送）显示「没接」。日历订阅（`/cal/<令牌>.ics`）现在会记下日历上次来取的时间和大致是哪种（iPhone / Mac / Google / Outlook）。见 [`connectors.py`](connectors.py)。

单元名和一个路径能在 `server.json` 里换（2026-10-20，每次请求现读）：`"connectors": {"units": {"browser", "mail", "obsidian", "tree"}, "canvas_courses"}`。默认是 `mousse-browser.service`（服务器上登 Canvas 用的浏览器）、`mousse-mail.timer`（邮件摘要）、`obsidian-sync.service` 和 `mousse-tree.service`（安装器装的）；不带后缀的当 `.service`。`canvas_courses` 是跟踪的课的 JSON 文件（`{课号: 课名}`，默认 `<openclaw_home>/canvas/tracked.json`）。

app 里「接下来会自动做的事」（`GET /api/upcoming`）列 OpenClaw 的定时任务，和只读的 systemd 用户定时器。`"upcoming": {"titles": {"<定时任务的名字或定时器单元>": {"zh": "…", "en": "…", "repeat": "weekly"}}, "reminder_prefix": "<单元名前缀>"}` 给它们起好认的名字（只写一个字符串 = 两种语言一样；定时任务的名字按 slug 比）。systemd 定时器只列 `titles` 里有的（`repeat` 不写 = 每天），一次性提醒（`systemd-run --unit=<前缀>…`，标题取它的 Description）只在配了 `reminder_prefix` 时列。默认一个定时器都不列，定时任务用它自己的名字。

## 连接器（经 MCP 接进来的应用）

「设置 → 连接器」：经 MCP 连到这台服务器上的第三方应用（Notion、Linear……或者任何一个远端 MCP 地址）。你的 claw 经 `/mcp` 用它们的工具；令牌只在这台服务器上。见 [`apps.py`](apps.py)。

- **连**按 MCP 授权规范：服务器找到应用的授权服务器（401 的 `WWW-Authenticate` → 资源元数据 → 授权服务器元数据；没有资源元数据的按旧规范，在 MCP 地址的同一个域上找），拿一个客户端（配了 `apps.client_id_url`、对方又支持 CIMD 就用它，否则动态注册，按授权服务器和回调地址记下来），给 app 一个授权网址（PKCE S256、`state`、`resource` = MCP 地址、对方宣称的 `scope`）。手机上登录，对方跳回 `<app 的 scheme>://oauth/callback?code&state`，app 交给 `/api/apps/oauth/callback`，服务器换令牌、列工具。一次授权 30 分钟内有效；回调可以来不止一次（网络断了 app 会重发）：正在换令牌时来的等同一个结果，成功以后来的拿到这个应用，失败以后来的拿到同样的错，过期的说授权超时（用过、过期的 state 只记指纹，`states.json` 留一天）。重新开始不会作废还没做完的那次。注册过的客户端换到过令牌才沿用，之前每次都重新注册，对方回 `invalid_client` 就忘掉它。令牌快过期前刷新，收到 401 刷新一次，同一个应用一次只刷一个。只有对方明确拒绝（`invalid_grant`、`invalid_client`、`unauthorized_client`、`invalid_scope` / `invalid_target`、`access_denied`、`unsupported_grant_type`，或不带错误码的 400 / 401 / 403）才显示要重新连；对方出错、连不上只算这一次没调成。删掉时能撤销就到对方那里撤销令牌。
- **卡在哪**：每次 OAuth 连接记五步（找授权服务器、注册这个应用、在对方登录同意、换令牌、列出工具）。连上以前，app 的 `attempt` 按做完 / 卡住 / 等着 / 没开始列出来，卡住那步带对方的原话；登录那步等了 30 分钟还没结果算授权超时；应用内登录页没带结果就关了，app 报给 `/api/apps/oauth/closed`。每一步、每次刷新、没调成的调用、状态变化也追加到 `apps/log.jsonl`（超过 256 KB 留后一半），不记令牌、授权码和 state。
- **用**：连上的应用的工具出现在 `/mcp` 上，叫 `<应用 id>__<工具名>`（多一个可选的 `agent` 参数；工具自己有 `agent` 参数时叫 `mousse_agent`）。每个工具一个档：`auto` 直接调；`ask` 每次调用是一张收件箱卡（kind `app`，响铃），点了同意就照原样调一次、结果发进那个 Agent 的对话，「改一下」让它换参数重新调；`off` 不给。默认读的 auto、写的 ask（看工具的 `readOnlyHint`，没写就看名字：get / list / search / read / fetch / find / query / view / describe / show / lookup 开头的算读）。哪些 Agent 能用按应用设（默认 main）；是谁：`mcp-<id>` 令牌绑的 Agent，否则 `agent` 参数，否则 main。按会话记工具列表的 claw 要重连才看到变化。
- 调用：每次一个短的 MCP 会话（streamable HTTP，对方只有 SSE 就用 SSE），最长 60 秒，同时最多 4 个；文字最多 30,000 字，图片之类只说一句。
- 只在 `server.json` 里（设置页还不能改）：`apps.<应用 id>.disabled_tools` / `enabled_tools`（工具名的列表；`enabled_tools` 写成空的 = 全关）让工具不出现在 `/mcp` 和内核里，调到关掉的会说是哪个键关的。用自己的内核时，工具说明掐到 `apps.desc_max` 字（600），`ask` 的卡 `apps.ask_ttl_hours` 小时（24）后过期，再批回 409；接 OpenClaw 或别的 claw 时说明照旧 8000 字，卡一直等到你回。
- 安全：从对方学来的地址（元数据、各个端点）要 https、解析出来是公网地址，和 MCP 地址同源的除外；自定义的 MCP 地址要 https，http 只给本机和 Tailscale 地址。令牌不回给 app、不写日志。`app` 卡交上来以后被改过就不照做。

| 接口 | 做什么 |
|---|---|
| `GET /api/apps` | `{apps: [app], catalog: [{id, name, url, category, desc, mono, bg, fg, border, auth, hint, blocked, installed}], agents: [{id, name}]}`（main 在前） |
| `POST /api/apps` | `{catalog, redirect_uri}` 或 `{name, url, auth: oauth / token / none, token?, redirect_uri?}` → oauth：`{app, authorizeUrl, state}`；token / none：现在就连、列工具 → `{app}`。已经连上的 409；目录里标了 `blocked` 的 → 400，回那句为什么 |
| `POST /api/apps/{id}/connect` | 重新连、换账号：`{redirect_uri}` → `{app, authorizeUrl, state}`；令牌方式的给 `{token}` → `{app}`；目录里标了 `blocked` 的 → 400 |
| `POST /api/apps/oauth/callback` | `{state, code?, error?, error_description?, iss?}` → `{app}`；同一个 `state` 再来回同样的结果；过期的 → 400（授权超时），认不出的 → 400，应用删掉了 → 404 |
| `POST /api/apps/oauth/closed` | `{state, how?}`：应用内登录页没带结果就关了。这一次记成卡在登录那步，用的那个注册下次不沿用；之后晚到的回调照样能做完 → `{app}` |
| `GET /api/apps/{id}` | `{app}`，带 `tools: [{name, title, description, kind, level, overridden}]` |
| `PATCH /api/apps/{id}` | `{policy?: {read?, write?}, overrides?: {工具名: auto / ask / off / null}, agents?: [id]}` → `{app}` |
| `POST /api/apps/{id}/refresh` | 重新列工具 → `{app}` |
| `DELETE /api/apps/{id}` | 撤销令牌（尽力而为），忘掉令牌和这一项，还在等你点头的卡撤回 → `{revoked}` |

app：`{id, name, url, catalog, custom, category, desc, auth, mono, bg, fg, border, status: connected / needs_auth / error, error, account, connectedAt, createdAt, updatedAt, toolsAt, toolCount, readCount, writeCount, offCount, policy, overrides, agents, blocked, attempt}`：`blocked` 是目录里那句为什么，没有是 null；`attempt` 是 `{at, failed, title, steps: [{key, label, state, note}]}`，这次以后连上过就是 null。`app` 类的收件箱条目多带 `app: {app, appName, tool, args, agent, mono, bg, fg, border}`。

目录里是 2026-09-29 核对过的服务：Notion、Todoist、Atlassian、Canva、Figma、Linear、Sentry、Stripe、Hugging Face（OAuth），GitHub（个人访问令牌：它的授权服务器不支持动态注册），Cloudflare 文档、DeepWiki、Context7（不用登录）。2026-10-21 体检以后，Canva（只收本机回调）、Figma（不让动态注册）、Stripe（只注册它白名单里的回调）标了 `blocked`：还列着、排在最后，加或连都回 400 和那句为什么；`apps.catalog {"<id>": {"blocked": null}}` 能放开。`server.json` → `apps`：`catalog`（加、改、藏目录里的应用）、`client_id_url`、`redirect_uris`（网页版用的 http(s) 回调）、`allow_local`（只给测试）。状态在 `<data_dir>/apps/`（目录 700、文件 600）。

## 思考空间和收藏

app 的「思考」tab（2026-09-28 起界面上叫 **Zen**，代码和接口仍叫 think）：想到什么先扔进来（一句话、几个 `#关键词`、语音、照片、文件、链接、长文），没有人回。勾几条点「聊聊」或「想完了」，模型才参与。「收藏」存别的 App 里的好东西（链接、文件、截图），同样不调模型，你决定怎么处理。「冥想时间」期间推送全压住，结束时一次给你。见 [`think.py`](think.py)、[`saves.py`](saves.py)。

- **一条想法 = 一篇 Markdown 笔记。** `server.json` 配了 `think.vault`（例如 `"think": {"vault": "~/vault", "obsidian_vault": "库的名字"}`）就放进库的收件箱，Obsidian 里看得到，在那边改了也读得回来；没配放 `<data_dir>/think/`。文件夹名按语言默认（中文：收件箱 / 收件箱/已想完 / 收件箱/附件 / 笔记 / 写作；`think.inbox_dir`、`done_dir`、`attach_dir`、`notes_dir`、`writing_dir` 可改）。属性：`id, kind, created_at, source, keywords, tags, topics, note, files, url`；附件在正文末尾嵌成 `![[…]]`。在 Obsidian 里新建的笔记也收。文件按 mtime 和大小判断有没有变，1 秒内刚改的等下一轮（同步客户端不是原子写），写一律临时文件 + rename。删除 = 挪进库的 `.trash/`；想完的挪进「已想完」，不删。
- **关键词** = 属性 `keywords` + 正文里的 `#词`（跟在中文后面也算）。关键词页列出带它的全部想法和收藏、用过它的主题、常一起出现的词。
- **主题**（`think_topics`，id `tp-…`，也是对话线程和 OpenClaw 会话 `agent:main:grava:tp-…`；`grava` 是会话键里的内部值）：「聊聊」先发一句「【自动触发】聊聊」，让模型先问、不急着下结论；每天第一句话、主题里的碎片变了以后，`chat.start_run` 把碎片和「陪你想」的规矩拼在消息前面（对话里不显示）。聊的时候「只记下」的一句不给模型看（`note: true`，对话里一行虚线，不发给 Gateway），想完了时一起用。「想完了」在另一个会话里后台整理草稿（标题、一句话、要点和出处、还没想清的、下一步、关键词、记进世界树的一句），你改完「存进库」：写进笔记或写作文件夹，碎片挪进已想完，要记的经 workspace 的 `memory_tree.py` 写一片世界树叶子。
- **收藏**（`think_saves`，原件在 `<data_dir>/saves/`，`think.saves_dir` 可改）：原件不进库。存的时候抽一份正文（网页在后台抓，公众号文章也行，原文删了也还在；PDF / Word / 表格抽文字），抓不到就把原因记在链接旁边。之后你决定：带进主对话问（`/api/chat/send` 的 `save` 把正文给模型）、交给某个 Agent（在它的线程里安静地跑一轮）、放进思考变成一条想法、提炼成笔记、删掉（软删，能恢复）。
- **Zen 首页**（2026-10-13）：app 顶上分「想法 | 播客」两边。想法一边不再分想法 / 收藏两栏：`GET /api/think/home` 给在想的主题（带进度四格 `stage`、一句 `state`、等你处理的 `waiting`）和「记下的」（没完成的想法和收藏放在一起，新的在前；归进主题的碎片、放进过思考的收藏不再单列）。每条都有不经模型的出口：**完成**（`POST /api/think/done`：想法挪进已想完、属性记 `status: done` 和 `done_at`；收藏记 `done_at`；`undo` 恢复），已想完的列表按完成时间排（写成笔记的主题一件一行）。
- **拿去讨论**（2026-10-21，[`ideatalk.py`](ideatalk.py)）：长按一条想法 → 一张单子（`GET /api/think/ideas/{id}/discuss`：现在在哪讨论、最近聊过的赛博在前、功能 Agent、好友、最近的几期，和这台服务器能去哪几处）。和一位赛博一对一（`POST …/cyber {cyber}`：它的对话里一句「聊聊这条想法」，原文只当前情给它读，不当一条消息）、新建群聊（`POST …/group {members, host?, mode?}`：想法是来源也是议题，先讨论一轮，小结写回这条想法的笔记，群结束时想法挪进「已想完」）、放进播客（`POST …/podcast {episode?}`：一条素材；不给哪一期就新建一期）、交给功能 Agent 做事（`POST …/agent {agent, note?}`：在它的对话里说一句、引着这条想法，它照平时的规矩做）、发给好友（只发要点：`POST …/points` → `{points}`，用零工具的一问理出来，算你自己的一问：app 里点的，`engine.use.json` 是套餐就走套餐，经 /mcp 来的不花套餐；app 给你看，你点了才经 `/api/friends/{id}/messages` 发，从不发原话）。一样东西只有一个家：笔记的属性 `talk`（小结写回以后还有 `group`）记下它去了哪，每条想法带 `discussion: {kind, id}`，Zen 里画「讨论中」，点开就是那里。赛博、群聊都关着（两样都要自己的内核）时一律 404，app 照旧「和助手讨论」；群聊、赛博那两处还要各自开着 `groupchat` / `cybers`。
- **搜索**在内存里按字面找想法、收藏、聊过的主题和存进库的笔记（中文两个字就能搜），结果带高亮分段，不调模型。**历史**按天数想法和收藏；搜索页的热力日历用 `GET /api/think/heat`（近几周每天记下几条）。
- **冥想时间**（`think_focus`，25 / 45 / 90 分钟或不限 = 3 小时）：开始前 app 先列出这段时间里的日程；期间 `push.send_push` 一律不推，记进 `think_focus_held`；结束（点结束或到点）给小结：压住的按去处合并（同一个对话只留最新一条），加上等你点头的和接下来的日程。没人看过的小结，下次 `GET /api/think/focus` 还会给。

| 接口 | 做什么 |
|---|---|
| `GET /api/think/home?before=&limit=` | Zen 首页（想法一边）：`thinking`（在想的主题 + `stage` / `state` / `waiting`）、`items`（记下的：`{type: idea \| save, at, idea \| save}`，新的在前）、`count`、`more`、`finished`（已想完几条）、`savesNew`、`vault`、`folder`、`obsidianVault` |
| `POST /api/think/done` | `{ids: [th-… / sv-…], undo?}`：完成（undo = 恢复）几条想法或收藏，不调模型 |
| `GET /api/think/finished` | 已想完：写成笔记的主题（`type: topic`）、单独完成的想法、完成的收藏，按完成时间新的在前 |
| `GET /api/think/heat?weeks=6` | 从 weeks 周前的周一到今天，每天记下几条（想法 + 收藏） |
| `GET /api/think/stream?before=&limit=` | 想法（新的在前，只记下的不列）、在想的 `topics`（`count` = 碎片数，`notes` = 只记下的句数）、`savesNew`、`vault`、`folder`、`obsidianVault`（老的三栏用） |
| `POST /api/think/fragments` | `{kind?, text?, title?, keywords?, url?, topic?}`；带 `topic` = 在这个主题里只记下 |
| `POST /api/think/fragments/upload` | multipart：最多 10 个 `files`、`text`、`kind`（voice 转文字、原声留着）、`keywords`（JSON 数组）、`duration`、`title` |
| `GET` / `PATCH` / `DELETE /api/think/fragments/{id}` | 一条想法；`{text?, title?, keywords?}` 改笔记本身；删 = 挪进库的回收站 |
| `GET /api/think/file/{id}/{index}?thumb=1` | 附件（缩略图缓存在库外面；`preview=1` 看大图、`inline=1` 在浏览器里直接显示，同对话附件） |
| `GET /api/think/file/{id}/{index}/preview` · `…/page/{n}?w=` | 在 app 里预览附件，和对话附件同一套（[`preview.py`](preview.py)）；大图和页图缓存在库外面 |
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
| `GET /api/think/saves/{id}/file?thumb=1` | 原件（`preview` / `inline` 同对话附件） |
| `GET /api/think/saves/{id}/preview` · `…/page/{n}?w=` | 在 app 里预览原件：Word / Excel / PPT 转好排版，PDF 出页图（同对话附件，页图存在原件旁边） |
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
- **每一期一个录前讨论群**（自己的内核，开着 `groupchat` 和 `cybers`；[`podprep.py`](podprep.py)）：「开始讨论」（`POST /api/podcast/episodes/{id}/discuss` `{mode?, guests?, friends?}`）给这一期开一个群聊（这一期的 `prep_group`；群的来源就是这一期）。谁来问你按录法：独白是 Iris（只帮你理提纲，录的时候不设主持）、主持人也是 Iris、费曼是阿哲（外行：听不懂就说、要你举例）；多人对谈拉了好友（`friends: [好友 id]`，要开着管家）= 录前讨论是一个好友群：你是群主，你的管家兼主持：先开口（固定的一句），有人提了时间它照名片 Agent 的规矩接，时间先出卡给你；提纲照旧由 Iris 帮忙排，她只看得到你自己在群里说的（好友的原话、名字都不给），提纲那一行只在你这边。没拉好友照旧由 Iris 帮着理。嘉宾是再拉进来的几位赛博。主持人先开口，之后你说一句它接一句。`POST /api/podcast/episodes/{id}/outline` `{note?}` 让主持人按聊到的排或者改提纲（零工具：助手走 `llmjson.ask`，赛博用它自己的模型）：存成这一期的提纲（录的时候屏幕上那一张），钉在群顶，群里一行「Iris 更新了提纲第 2、4 条」。讨论一轮的小结放进这一期的素材。**「放进」的卡**：`POST /api/podcast/episodes/{id}/offers` `{agent}` 拿一个功能 Agent 最近 7 天里最新的一份报告，出一张只有你看得到的卡；`POST /api/podcast/offers/{卡}` `{action: put | skip}`。普通的进素材；健康、饮食、睡眠那几个 Agent 的（比如近 7 晚的睡眠报告）不进素材：只给这个群的主持赛博、只用一次（它下一次发言读到，读过就标 used）。原来的录前聊天（`/prep`、`pod_turns`）照旧能用、照样能读，不搬不删。删一期，它的录前讨论群一起删（好友群只断开：那是大家的）。
- **「制作中」的一行**（`GET /api/podcast` 的每一期，和单独取一期）：`participants`（头像，主持人在前）、`booked`（你约的录制时间：`PATCH /api/podcast/episodes/{id}` `{bookedAt: "YYYY-MM-DDTHH:MM" | ""}`，进日程，key `podcast:<期>:rec`；好友群里由你的管家说一声；好友在群里提的时间，你在卡上点了同意才进同一条）、`latest`（录前讨论群里最新的一句；没人说话时写下一步「提纲已就绪 · 4 条」）、`steps`（讨论 → 提纲 → 录制 → 整理）、`waiting`、`prepGroup`。
- **朋友画像**（约朋友录的）：开录前选谁在；认人时给每个声音对上一个人（以前一起录过的、朋友、或者新名字；也可以只写名字、不记画像）。整理完给每个对上的人记 3–8 条（看法 / 在做的事 / 在意的 / 下次问问），每条落在这个人说的一句上（点一下听原话）。说的还是那件事就不再写；变了的写新的一条、旧的留作以前的说法；「下次问问」有了答案的标成问过了。重新整理、删掉一期会撤回这一期记的（你改过的留着）。只有你看得到：主持人和录前聊天能接上以前说的（「上次小林说……」），「今天聊点什么」能出「约小林聊……」；别的都不读——名片 agent 哪一档都不用，主对话和 Agent 不用，也不进库和世界树。会告诉朋友：约朋友那页说会记画像，每人纪要最后一句也写着。约朋友录的，标题、一句话、关键词只按你说的写（它们会存进库）。画像只经 `llm-task`（零工具；整段都是别人的话）记：没有它这次就不记，录完页会说。退回普通对话回合时不带画像，约朋友录的只带你自己说的句子。见 [`people.py`](people.py)。
- **远程一起录还没做，接口先留着**：以后（可能是 app 里直接打电话）每个人一条音轨，按 `track`（谁）和开录时的时间（对齐）传进同一期，整理时按音轨分人，不用再猜声音。现在的分段是一条时间线接着一条。

`server.json` 的 `podcast`（全部可选）：`dir`、`text_model`（默认 `gpt-transcribe`）、`time_model`（默认 `whisper-1`，`""` = 不要逐句时间）、`thinking`（默认 `low`）。

| 接口 | 做什么 |
|---|---|
| `GET /api/podcast` | 今天挑好的话题（还没挑是 `null`；`mode: friends` + `source.person` = 约某个朋友聊）、最近 40 期（`chip` = 列表上那个小标签；`stage` / `state` / `waiting` = Zen 播客一边「制作中」卡上的进度四格（讨论 → 录制 → 整理 → 存入）、一句状态、是不是在等你）、`study`（配了学习台没有）、`materials` / `people`（这台服务器有素材、朋友画像） |
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

## 合并转发

在 Agent 的对话或朋友的聊天里长按任意一条：**转发**、**选这一段**（模型把前后说同一件事的几条勾上，顺带写一句要点）、**多选**、**加入转发篮**（在几个对话里各挑几条，攒到一起再发）。见 [`forward.py`](forward.py)。

- **发给朋友**：变成一份 `kind: thread` 的分享（见上），先挡私事，朋友能追问（名片 Agent 只拿这一份回答），收回以后对方那边清空。里面有别人说的话（朋友本人、朋友的名片 Agent）的不能追问，每条都写明是谁说的。
- **转给自己的 Agent**：在它的对话里记成你发的一条，带一个意图（记录 / 处理 / 仅阅读）和一句补充说明，app 在上面画聊天记录卡；它正在回复就排队。**朋友说的话不进能用工具的回合**：先交给不带工具的模型（`llm-task` 或 `card.llm`）整理成要点，Agent 只看到要点；两条路都没有，只告诉它有几条没给。
- 含朋友的话的能放进播客，不能收藏（收藏可以再交给 Agent）。

| 接口 | 做什么 |
|---|---|
| `POST /api/forward/preview` | `{refs, gist?}` → 一条条、一句要点、来自哪几段对话、里面有谁说的话 |
| `POST /api/forward/segment` | `{ref}` → 前后说同一件事的几条和一句要点 |
| `POST /api/forward/share` | `{refs, gist}` → 一份 `thread` 草稿分享（之后走分享的接口：挡私事、`/send`） |
| `POST /api/forward/agent` | `{refs, gist, agent, intent, note?}` → 记进那个 Agent 的对话 |
| `POST /api/forward/save` | `{refs, gist}` → 一条收藏 |
| `GET` / `POST` / `DELETE /api/forward/basket` | 转发篮（`DELETE` 不给 refs = 清空） |
| `GET /api/forward/{id}` | 转给 Agent 的那一份（全文） |

ref 是 `chat:<线程>:<消息 id>` 或 `friend:<好友 id>:<行 id>`。

## 朋友

app 里「对话 → 朋友」。你的服务器和朋友的服务器直接说话：身份是每台服务器一把 Ed25519 密钥（不是地址），加朋友靠一次性邀请码，两台服务器之间的每个请求都带签名。协议见 [`../docs/social-protocol.zh-CN.md`](../docs/social-protocol.zh-CN.md)；代码是 [`social.py`](social.py)（身份、名片、签名、表、档位）和 [`friends.py`](friends.py)（邀请码、聊天、投递、分享发给朋友、追问）。

- **好友怎么找到你**：经小服务（[`public.py`](public.py)）的 `/f`：直连（用 Tailscale Funnel 或反向代理指过去，`tailscale funnel --bg --set-path /f http://127.0.0.1:<share.public_port>/f`，再设 `share.public_url`），或者没有能用的公网地址时默认经 OpenMousse 中继（见下）。没设 `user_name` 时 app 先请你设名字再加好友。反向代理不许改写 `/f` 这一段（路径签在签名里）。
- **邀请码**：一个链接 `<你的地址>/f/i/<令牌>/<你的公钥>`，只能用一次，默认 7 天，服务器只存令牌的哈希。浏览器打开是一页落地页（带给手机扫的二维码和「在 app 里打开」）；app 粘进来，用链接里的公钥核对对方服务器的名片，再签名 `POST /f/hello` 兑换。10 位指纹给两个人打电话时对一下。
- **聊天**：一条消息一个签名的 `POST /f/msg`。发出去的先排进 `friend_messages`，投递循环按朋友依次发，失败重试最多 3 天；改、收回、名字或地址变了、删朋友也都是消息。拉黑 = 对方发的一律收下不存。
- **分享发给朋友**：分享页选朋友，对方收到的是同一份挡过私事的快照（`▇▇▇`，原文不出服务器）。不开链接时这条分享 status = `friends`，`/s/` 打不开。收回分享 = 发出去的每一份都收回。
- **追问和名片 agent**：朋友能对着你分享的东西追问；分享开着追问、对方那一档 `shares` 是 `ask` 时，你的名片 agent（`cardagent.py`，不经 claw，没有工具）按那一档代答，并说明用了什么。每条代答你都看得到：没问题 / 我来改 / 收回。没有名片 agent 时追问等你自己回。档位（亲近 / 朋友 / 同学 / 陌生）和近况在「对话 → 朋友 → 我的名片 agent」（`/api/card`）；健康和世界树哪一档都不给。名片 agent 说出去的每一句先过 **Doorman**（`sentinel.py`，第 9 步安全底座）：规则 + 另起一次的独立复查，不妥的先扣下、出一张卡等你照发 / 改一下 / 不发，复查不了就换成固定的话（细节见 `docs/a2a.zh-CN.md` 2.4.1）。
- **推送**：朋友的推送是新的推送类型，`server.json` 的 `social.push` 开了才推（`{"message": "ring", "answered": "quiet", "friend": "quiet"}`；`agents` = 你的名片 agent 问过的事、对方本人定了，没写就跟着 `answered`）。朋友发来的照样算未读、算进角标。
- **中继和端到端信封**（2026-10-05，[`relay.py`](relay.py)、[`../relay/`](../relay/)）：没有能用的公网地址的服务器往外连 OpenMousse 中继（默认 `https://relay.openmousse.ai`），好友经 `https://relay.openmousse.ai/u/<kid>` 找到它，所以新装、没开 Funnel 也能加好友。名片多了一把 X25519 公钥（`enc`）：新版服务器之间的消息、邀请握手、A2A 都经 `POST /f/sealed` 封起来，中继只看得到密文。server.json 写 `"relay": false` 关掉。协议见 [`../docs/social-protocol.zh-CN.md`](../docs/social-protocol.zh-CN.md) 6.1。
- **你的名字、外面连不连得到你**（2026-10-05）：名片上的名字就是 `user_name`；app 用 `PATCH /api/card {name}` 设（同时写进档案，和 `settings_ctl.py user-name` 一样），第一次连上服务器后马上请用户设。`share.public_url` 是 `*.ts.net` 地址、但 `tailscale funnel status` 里 `/f` 没开到公网，或者最近一个试着连你的朋友服务器没连上时，`GET /api/friends` 报 `unreachable`（`publicFix` = 要跑的命令）。别人兑换你的邀请码时，你的服务器先试着取一下对方的名片再回话，回 `reach: true | false`，对方服务器记下来显示；之后收到任何朋友发来的消息就清掉。还没送到、在重试的消息带 `error` 和 `nextTry`，对它调 `…/retry` 马上再试一次。
- `social.allow_http: true` 只给同一台机器上的测试服用（`http://127.0.0.1:<端口>` 这种地址），真服务器别开。

| 接口 | |
|---|---|
| `GET /api/friends` | 能不能加朋友（`ready`、`why`）、你的名字和指纹、朋友和各自最后一句、未读、没用的邀请码 |
| `POST /api/friends/invites`、`DELETE /api/friends/invites/{id}` | `{note?, tier, days?}` → 邀请链接和二维码（SVG path，只给这一次）；收回没用过的 |
| `POST /api/friends/preview`、`POST /api/friends/accept` | `{code}` → 对方是谁（用链接里的公钥核对过）；`{code, tier, alias?}` → 兑换 |
| `PATCH` / `DELETE /api/friends/{id}`、`POST /api/friends/{id}/block` | 档位、备注、`pinned` 置顶、`muted` 免打扰（不推送、不算角标）；删朋友（会告诉对方）；拉黑 / 解开 |
| `GET /api/friends/{id}/messages?after=&before=`、`POST …/messages`、`POST …/ask`、`POST …/read` | 聊天记录：最新一页带 `more`（前面还有），`before` 往上翻，`after` 轮询（从早到晚给，`recent` 带状态、编辑、表情回应、`hidden`）。每条可能带 `read`（对方看过；已读回执开着才有）、`reactions` `{me, them}`、`quote`（回复的原话）。发（`replyTo` = 那条的 mid）；对着对方的一条分享追问；标已读（两边都开着已读回执时，顺带发一条 `read` 告诉对方） |
| `POST /api/friends/messages/{id}/review`、`…/revoke`、`…/retry` | 名片 agent 的代答：`{action: ok \| edit \| revoke, text?}`；收回自己发的；重发没送到的 |
| `POST /api/friends/messages/{id}/edit`、`…/react`、`…/hide` | 改自己发的文字（`{text}`，对方标「已编辑」）；表情回应 `{emoji}`（6 个之一，`null` 取消；对方服务器太旧回 409）；只在我这删除 |
| `GET` / `PATCH /api/friends/prefs` | `{notify, preview, receipts, friends: [{id, name, muted}]}`：好友通知、通知里显示内容、已读回执（双向）。存在 `social_settings`，优先于 `server.json` 的 `social.push` |
| `GET /api/friends/search?q=` | `{friends: [名字对得上的 id], hits: [{friend, name, id, mid, dir, kind, ts, snippet}]}`；`/api/search` 不带 `thread` 时也带这些（`kind: friend`） |
| `POST /api/shares/{id}/send` | `{friends, ask, link, text?}` |
| `GET` / `PATCH /api/card` | 档位、近况和名字（`name`；还没设时有 `suggest`） |
| `GET /f/card`、`/f/jwks.json`、`/f/i/{token}/{key}`；`POST /f/hello`、`/f/msg` | 公开的（只在小服务上）：签名名片、公钥、邀请落地页；兑换邀请码、投消息（要签名） |

## 代办和 Doorman 出口（预览）

Doorman 在 2026-10 之前叫 Sentinel；内部名字沿用旧词（服务 `openmousse-sentinel`、目录 `<data_dir>/sentinel/`、令牌 `sentinel`、配置 `sentinel.*`、模块 `sentinel.py`）。

替你在外面办事（查资料、填表、发信、预订）的 Agent，在 OpenClaw 的 Docker 沙箱里跑，上网只有 Doorman 一条路：读随便读，要提交、发送、用你的凭证、带你私事的，都在收件箱里等你点「放行这一次」（kind `egress`）。凭证是占位符，出门时才换成真值，只对绑定的网站。不跑 `python3 errand.py setup` 就不生效；分几层锁、怎么判、凭证、回滚都在 [`../sandbox/errand/README.zh-CN.md`](../sandbox/errand/README.zh-CN.md)。模块：`egress.py`（判断、`/api/egress/*`、收件箱钩子）、`egress_proxy.py` + `sentinel_run.py`（代理）、`errand.py`（装、查、凭证）。

## 数据源是可选的

看板要的训练 / 餐食 / 身体 / 日历 / 健康派生指标，各来自 `server.json` 的 `scripts` 目录（默认 `<workspace>/scripts`）里的一个脚本：`xunji.py`（训练 / 身体；餐食只在 `diet.source` 设成 `xunji` 时从它读，平时记在服务自己的库里，见「饮食记录」）、`calendar_ics.py`（日历）、`apple_health.py`（恢复分、热量缺口、体能趋势，还有起没起床：`/api/health/wake` 看手机推到 `/api/health/sleep` 的睡眠分段和 `/api/health/signal` 收到的起床信号）。`memory_tree.py` 提供世界树（「我 → 世界树」）。脚本在就加载，不在就是「还没接」：`/api/health` 的 `sources` 告诉 app 哪些接了，没接的接口回 `ok=false` + `missing_source`，app 的看板显示空状态，其它功能照常。见 [`sources.py`](sources.py)。

这三个脚本目前还是照几个具体的数据源（训记、ICS 日历、Apple 健康）写的，正在拆成可选的功能包（packs/）：每个包只声明需要的数据类型，来源由你映射。
