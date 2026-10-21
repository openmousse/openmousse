# 更新记录

**中文** · [English](CHANGELOG.md)

OpenMousse 每一版改了什么，新的在上面。一版按发版日期命名，打 `v<版本>` 的 tag（[docs/releasing.zh-CN.md](docs/releasing.zh-CN.md)）。世界树的包另有自己的记录：[tree/CHANGELOG.md](tree/CHANGELOG.md)。

## 未发布（草稿）

从 v2026.10.13-3 到现在。
- 下面写「关着」的是默认关，这一行写了怎么开；标了关着的，不会自己开。
- 要用你自己的密钥、套餐或者订阅的，都写明了。
- `mousse` 就是 `~/.openmousse/repo/bin/mousse`；`ln -s ~/.openmousse/repo/bin/mousse ~/.local/bin/mousse` 放进 PATH。

### 自带的内核：不装 OpenClaw 也能用

- **自己就能跑。** 对话、Agent、后台任务、定时任务、记忆都在 OpenMousse 服务器里跑，模型你自己挑。不再需要 OpenClaw。
  - 机器上没有 OpenClaw 时，第一次装直接回车就是自带的内核（或者答 `builtin`、设 `MOUSSE_CLAW=builtin`）。
  - 已经装好的照旧用原来的 claw。Docker 装的一律是自带的内核。
- **模型自己挑。**
  - 模型从哪来：用你自己密钥的云端服务商（OpenAI、Anthropic、Google、xAI、DeepSeek、Kimi、通义千问、OpenRouter、Azure OpenAI、Amazon Bedrock 等等）、这台机器上跑的模型（Ollama、LM Studio、vLLM、llama.cpp、MLX，在它们常用的端口上自动找），或者任何 OpenAI 兼容的地址。
  - app 第一次连上时选，或者在终端里用 `setup_ctl.py` 选。
  - 不同的用处用不同的模型（对话、后台、写作、名片 Agent 和 Doorman、媒体）：设置 → 密钥 → 按用途选模型，或者 `setup_ctl.py use …`。
  - 云端的花你的密钥；本机的不花钱。
- **用你自己的订阅。**
  - **Claude Code：** 主对话可以交给服务器上你自己登录的 Claude Code（`claude-code/opus`、`sonnet`、`haiku`）。找到登录好的 Claude Code 才列出来。用的是你的 Claude Pro / Max 套餐，只给你自己的回合用，同时最多 2 个，其余的排队。
  - **ChatGPT：** ChatGPT Plus / Pro 套餐在 app 里点 **Continue with ChatGPT** 连（`chatgpt/<型号>`），令牌只存在你的服务器上。后台的活要你先同意（`PUT /api/engine/chatgpt/background`）才用套餐。
  - **Grok：** SuperGrok / X Premium+ 订阅（`grok/<型号>`）。在服务器上跑 `setup_ctl.py grok login` 登录，app 里还登不了。令牌只存在你的服务器上，`setup_ctl.py grok` 能看额度用了多少。这不是 xAI 支持 OpenMousse 的登录方式，xAI 可能拒（403）：那就改用 `XAI_API_KEY` 配 `xai/<型号>`，花你的密钥。后台的活要先跑 `setup_ctl.py grok background on` 才用订阅；`engine.grok: false` 全部关掉。
  - **Codex、Copilot：** 你命令行里的登录也能拿来跑对话。关着：`engine.codex: true`、`engine.copilot: true`。
  - **一问一答也走 Claude 套餐：** 总结、纪要这种一问一答的也能走 Claude 套餐，后面接按量付费的备用（`engine.use.json` / `engine.use.compact` = `["claude-code/sonnet", …]`）。
  - **在 app 里选：** 设置 → 密钥 → 按用途选模型 里多了「一问一答」「压缩摘要」两行，写着用哪个模型、花套餐还是按量、出问题时换成哪个。Codex、Copilot 的套餐不在可选的里面。
  - **套餐没做成时**，说清是什么问题，只换你写了的备用：不再把套餐重跑一遍，也不自己换成按量付费的模型。有备用时，套餐最多只给一半的时间。
  - **短的调用多开几个位子。** 关着：`engine.claude_code.burst: <个数>`。平常的位子都占着时，短的调用最多再多开这么多个 Claude Code 进程，花你的套餐，每个约占 0.3 GB 内存。
  - 名片 Agent、Doorman 和对外的接口从来不用套餐。
- **工具分档。** 每个工具三档：自己跑、先问你、关掉（`engine.tools.levels`）。撤不回来的、往外发的，都在收件箱里等你点头，点了就照你看到的那样跑一次。
- **用量。** 按天、按模型、按用处算 token 和钱：设置 → 密钥 → 用量、`/usage`、`mousse status`。套餐的回合记作套餐，不算钱。
- **花钱的几个开关。**
  - 按天、按回合、按 Agent、按用处的花费上限（美元）。关着：`engine.spend`。
  - 模型出错时换下一个。关着：`engine.fallbacks`；备用的按量付费。
  - 简单的短句交给便宜的模型。关着：`engine.auto`。
  - 让 Claude 一小时的提示缓存一直热着。关着：`engine.cache_warming`；每次保温花一点点 Anthropic 的钱。

### 对话

- **会话。**
  - 每天 04:00 开新会话（`engine.reset.at`；`idle_minutes` 是安静多久也换新）。聊长了自动压缩，压一次调一次 `engine.use.compact` 或者对话的模型。
  - 以前的会话能接着聊、分叉、开分支，能导出成 Markdown、JSON、HTML（像密钥的会打码），接口在 `/api/engine/chats/<线程>/…`。分叉和分支 app 里还没有界面。
  - 每一句都带着发出时的本地时间。
- **斜杠命令。** 关着：`engine.commands: true`（或者 `{"off": [...]}` 去掉不要的几条）。输入框打 `/` 时 app 会列出来：
  - `/help`、`/status`、`/usage`、`/context`、`/tools`、`/model`、`/stop`
  - `/new`、`/compact`、`/resume`、`/export`
  - `/think`、`/queue`、`/btw`、`/bg`、`/goal`
- **长期目标。** Agent 一直干到判定目标达成为止。关着：`engine.goal: true`。每一轮多花几个回合，再加一次判定；能用 `max_turns`、`max_tokens` 封顶。
- **会话的几个小开关。**
  - 会话标题。关着：`engine.titles: true`；每个会话一次小调用。
  - 服务器重启掐断的那一轮接着跑。关着：`engine.loop.resume: true`。
  - 每个对话、每个 Agent 的思考档位：`/think` 或者 `engine.think`。
- **看别的会话、Agent 之间转话。** Agent 能看别的会话（`engine.sessions.visibility`），也能把活交给别的 Agent，最多转 2 次（`engine.handoff`）。
- **终端里。** `mousse agent` 给某个 Agent 发一句、回 JSON。`mousse chat` 是在终端里聊天，算你自己说的。
- **回复的时候**（自带的内核）：
  - 显示的每一步都是大白话（「正在查看日程」）。
  - 插话没赶上这一轮，当场写「未赶上本轮」，这一轮结束后再回它。
  - 换成备用模型时，回复下面一行写着从哪个换的、为什么、这一轮换成了哪个。
- **任务重派**（自带的内核）。被服务器重启打断的任务写「被重启打断」。失败、超时、停掉、被打断的任务，卡上和任务页上都有「重派」：照原来的说明从头再做，结果回到原来的对话，算在今天的任务额度里。

### 赛博、群聊和管家

这一节的都默认关，都要自己的内核。开了以后重启服务器，已经开着的 app 要关掉重开才看得到。

- **赛博。** 有自己人格、记忆和模型的思考伙伴，和功能 Agent 并列。关着：`"cybers": true`。
  - 新的赛博（新的功能 Agent 也一样）从一句话或模板开始：补问两三道、出草稿、试聊。形象可以自己捏，也可以上传。
  - 赛博从来不花套餐：用你给它配的模型（或 `engine.use.cyber`），记在你的密钥上；本机模型不花钱。选模型页把开源的排在前面。
  - 它只记和它聊过的。改它的资料（对话里说、资料页改、它自己提）都先给「改好的样子」，你点头才写，记版本、能撤销。隔几轮自己回头看一次默认关：`cybers.review`，每次多几次调用。
  - 可以给它装载功能 Agent 去问：只读、一轮、每次回复最多问 3 次。健康、饮食、睡眠类的不能装载（`cybers.health_agents` 能再加）。每天读一次它们的产出，每位赛博各有开关，默认关（在它的资料页；`cybers.daily_at` 以后读）。
  - 开的时候建好两位，只建一次：主持的 Iris、外行的阿哲。
  - Agent 栏最前面是通讯录：管家（没开管家时是名片 Agent）、赛博、功能 Agent、好友。
- **群聊。** 赛博和功能 Agent 在一个群里。关着：`"groupchat": true`（有赛博成员还要开着 `cybers`）。
  - @ 一位，或者 @所有 Agent 跑一轮。助手、一位赛博主持，或者不设主持。
  - 每轮的小结写进这个群自己的记忆，钉在顶上给下一轮。
  - 助手和功能 Agent 用各自平常的模型发言，可以是你的套餐；一句 @所有 Agent 大约是成员数加一次调用。
  - 健康、饮食、睡眠类的功能 Agent 不和赛博同群。
- **会议。**
  - 按谁发起收列表。关着：`meetings.by_origin: true`。你发起、自己开的留在对话列表里；助手发起的和「定了告诉我」自己跑完的，开完收走，留一张纪要卡。主对话资料页的「会议记录」都找得到。
  - 「定了告诉我」，要开着 `meetings.by_origin`：你在 app 里发起的会自己跑完（最多 2 轮），待办不问你就进日程和看板。
  - 会议能请赛博，赛博不领待办。
- **录前讨论群**（要开着群聊和赛博）。每一期一个群，主持人（Iris、阿哲或不设）把提纲钉在顶上；「放进」把素材交给主持人。拉了好友的（还要开着管家），录前讨论是你的管家主持的好友群；好友提的录制时间，你在卡上点了同意才进日程。
- **Zen「拿去讨论」**（赛博、群聊开着一样就行）。长按一条想法：和一位赛博聊、开群、放进播客、交给功能 Agent，或者把要点发给好友。要点用零工具的一问理出来，算你自己的一问（「一问一答」配成套餐就走套餐），你看过才发。
- **管家。** 升级了的名片 Agent。关着：`"butler": true`。
  - 自己的资料页；每个档位的例句，一句一句改（先给样子、能撤销）；说过的话。
  - 可以请教的赛博：先去掉名字和原话，回答标成「那位赛博的看法」。每次请教是一次零工具的调用加那位赛博的一轮，按量付费。
  - 用名片 Agent 的模型（按用途选模型），从来不花套餐。
- **好友群**（跟着管家）。和好友的群聊：每台服务器只派一位管家，群主的管家主持。
  - 成员表由群主签名，发给每位成员的都单独封好（端到端加密；没有公网地址时走中继）。两边都要是这一版。
  - 好友的话只当管家的资料，不进你的主对话、不驱动工具；他们想放进你日程的，都等你点头。
  - 群里管家按成员里最紧的那一档回答，不是你好友的人算陌生人。
  - 还不能退群、移人。

### 工具

- **都是关着的，开了也只在你自己的回合里用：**
  - 一次改好几个文件，要么全改要么不改：`engine.files.patch: true`；
  - 在服务器上照放行名单跑命令，能在后台跑：`engine.host_exec.agents.<id>`。名单外的先问你，危险的每次都问；
  - 每个 Agent 一个 Docker 沙箱，能跑命令、开浏览器，上网只走 Doorman：`python3 server/errand.py setup`（要 Docker；Docker 装的那种里没有）；
  - 在沙箱里跑一段 Python，里面调这个 Agent 的工具：`engine.tools.execute_code: true`；
  - 你自己的 Chrome，带着你的登录：`engine.chrome.agents.main: true`，再配 `CHROME_CDP_URL`。每个动作都先问；
  - 工具很多时按需给：`engine.tools.search: true`；
  - 结构化的小任务：`engine.tools.llm_task: true`；
  - 对比两段字：`engine.tools.diff: true`。
- **媒体。** 每样各配一家，每次调用按量付费；本机的不花钱，有三样也能走 Grok 订阅（见下）。
  - 看图、读 PDF：`engine.use.vision`、`engine.use.pdf`。
  - 语音转文字：`engine.use.stt`。朗读：`engine.use.tts`。
  - 生图：`engine.use.image`。看视频：`engine.use.video`。
  - 生成视频、音乐：`engine.use.video_gen`、`engine.use.music`。每段视频都先问，带着估的价钱。
  - 登录了 Grok 订阅，生图、生成视频、朗读也能走订阅：`engine.use.image`、`engine.use.video_gen`、`engine.use.tts` 写成 `grok/<型号>`。算订阅的额度、不算钱，所以花费上限拦不住它们。
- **联网搜索。** Brave、Tavily、Exa、Perplexity、SearXNG、DuckDuckGo；Gemini、Grok、Kimi、Firecrawl 点名才用。
  - 选好一家之前没有 `web_search`：app 的 密钥 → 搜索，或者 `setup_ctl.py search`。
  - 花你搜索服务的密钥。打开链接（`web_fetch`）不用配。
  - 登录了 Grok 订阅、搜索选的又是 `grok` 时，先走订阅；xAI 没说订阅里含不含搜索。`engine.grok.search: false` 就只用 `XAI_API_KEY`。
  - 搜 X 上公开的帖子（`x_search`）。关着：`engine.web.x_search: true`。花的是 `XAI_API_KEY`，xAI 按抓到的帖子收钱；用量里算不出钱，花费上限也算不到它。`engine.grok.x_search: true` 先走订阅。

### 自动跑的

- **定时任务。** 用这个任务的模型，没有就用 `engine.use.background`。Agent 自己建的任务，你点头之前不跑。
  - 任务可以只跑一个脚本（`command`，脚本要列在 `engine.jobs.scripts`），不调模型。
  - 没变化可以不叫模型（`watch`、`pre`）。
  - 失败了可以隔一阵再试。关着：`engine.jobs.retry: true`。
- **心跳。** 每次一个回合。关着：`engine.heartbeat.<agent>`。
- **触发。** 不配就没有。
  - webhook，地址 `/webhooks/<名字>`，验签名：`engine.triggers.webhooks`。
  - 只读地盯一个 IMAP 邮箱：`engine.triggers.mail`。
  - webhook 和邮件进来，各由一个按量付费的模型读一次，从来不用套餐。
  - 带审批步骤的小流程：`engine.flows`。
  - 你说到某些话时触发的提醒，不调模型。关着：`engine.intents.enabled: true`。
  - 把一句话当后台任务跑：`POST /api/engine/bg`、`/bg`。
  - 「长期职责」这个 skill：一件事交给一个 Agent 长期照看，每一条你点头才算数。
- **通知卡。** 只告诉你一件事、不用你定的卡，比如每周的报告（`inbox_ctl.py add --kind info`）。不推送，也不发到聊天渠道，点「知道了」就收起来。
- **急停。** `mousse pause [--stop-running]`、`mousse resume`（或者 `POST /api/engine/pause`）。停掉定时任务、心跳、触发、后台的活、写回和长期目标；聊天、点头照常。

### 技能、钩子、MCP

- **技能。**
  - 给 OpenClaw、Hermes 写的 skill 照样能用。缺程序、缺密钥、系统不对的会藏起来，设置的接口说清缺什么。
  - 每个 skill 自己的设置和密钥写在 `engine.skills.entries`。
  - 所有 Agent 都能看到一个共享的技能文件夹（`<data_dir>/skills`）。每个 Agent 用哪些：`engine.skills.agents`。
- **技能市场。** 关着：`engine.skills.market.enabled: true`。app 里还没有界面。
  - `mousse skills search | install | update | list | remove`，来源有 ClawHub、GitHub、skills.sh、agentskills.io 格式的站和直接的 SKILL.md 链接。
  - 下载的都先扫一遍。危险的不装，别的都等收件箱的卡。
- **每周看一遍技能。** 每个 Agent 每周一次调用。关着：`engine.skills.weekly: true`。
- **钩子和插件。**
  - 钩子：内核出事件时跑你自己的脚本（`engine.hooks.<事件>`），每个钩子你点头之后才跑。
  - 代码插件。关着：`engine.plugins.enabled` 和 `mousse plugins install`。插件跑在服务器进程里，没有沙箱。
- **MCP 服务器**，给你自己的回合用：`mcp_servers`、`mousse mcp list | add | remove | probe`。
  - 它们的 prompt 变成工具。
  - sampling 默认关，给这台 MCP 服务器配一个按量付费的模型才开。
- **连接器**，就是你在 app 里连上的外部应用：
  - 在 `server.json` 里关掉单个工具：`apps.<id>.disabled_tools`，或者用 `enabled_tools` 只放行列出来的（空名单 = 全关）。还没有界面。
  - 自带的内核里，每个工具的说明最多 600 字（`apps.desc_max`），「先问我」的卡 24 小时后过期（`apps.ask_ttl_hours`）。

### 渠道

- **Telegram、Discord、邮件。** 关着：`channels.<种类>.enabled: true` 再配一个账号（令牌放 `secrets.env`），或者 `mousse channels …`。app 里还没有界面。
- **谁会被回话。**
  - 在你自己的设备上生成一个主人码（`mousse channels owner-code`，再发 `/start <码>`），你就是主人。
  - 别人要你批准以后由名片 Agent 回；陌生人一句都不回。
  - 邮件过了 DMARC 或者对齐的 DKIM 才算你自己发的。
- **转发、引用来的字** 只当资料，不当成你说的话。
- **Agent 多一个 `message` 工具：** 发给你直接发，发给联系人先问你。

### 运维

- **体检：** `mousse doctor [--fix]`、`mousse status`、`mousse logs`。
- **备份：** `mousse backup create | verify | list`、`mousse restore`。
- **更新：** `mousse update`。
  - stable 通道跟最新的发版 tag，`--channel main` 跟 main。
  - 先打快照，服务起不来就回滚。Docker 里不跑。
- **配置和清理：** `mousse config validate | get | set | unset`、`mousse security audit [--fix]`、`mousse sessions cleanup`。
- **排查包：** `mousse diagnostics`，一个 zip，不带对话、密钥和邮箱。
- **卸载：** `mousse uninstall [--full]`，`--full` 之前先备份。
- 这些都不调模型，也不打印令牌。
- **接的是 OpenClaw 时**，删掉一个 Agent 会一起清掉 `openclaw.json` 里别处指着它的地方，OpenClaw 照样读得了配置。

### 对外的接口

- 关着，`engine.api: true`：OpenAI 兼容的接口（`/v1/models`、`/v1/chat/completions`，模型写 `openmousse/<agent>`）。
  - 用它自己的令牌（`python3 server/tokens.py add api`）。
  - 跑在那个 Agent 的模型上；是套餐的话换成按量付费的。
- 也关着，要先开 `engine.api`：
  - `/v1/responses`：`engine.api.responses: true`；
  - 给你自己别的 agent 用的 A2A 1.0：`engine.api.a2a: true`。
- 给编辑器用的 ACP 适配：`server/engine/acp.py`。

### 设备

- **设备节点。** 关着：`"engine": {"nodes": {}}`。
  - 配对一台电脑（`tokens.py pair --node`，或者 设置 → 硬件 → 设备和节点），每条命令定成关、问、放行。
  - 放行的也只在你自己发起的回合里跑。
  - Mac 当手：`mousse node pair | run | install`。在 macOS 上操作电脑，每个动作都先问。
  - 手机当节点（位置、照片、通知）：iPhone 的 app 1.0.7，app 开着的时候。
- **对讲**，iPhone 的 app（1.0.7，自带的内核）：你说话，它把你说的发出去，回复一边出来一边念。
  - 服务器要配好转写（`OPENAI_API_KEY` 或者 `engine.use.stt`），花你的密钥。
  - 念的声音是手机自己的。

### 装和搬过来

- **第一次连上。** 服务器还没有模型时，app 先带你走三步：模型、搜网页、记忆。然后给助手起名字、写一句性格、选样子。设置中心里有连接、密钥、用量和「手」。
- **试装。** `MOUSSE_TRIAL=1`（或者 `--trial`），Linux 和 macOS 都行：自带的内核、只在这台电脑上、不开公网、不碰 OpenClaw。装了什么都记在 `~/.openmousse/install-manifest.json`。
- **Docker。** `docker compose -f docker/compose.yaml up -d --build`（[docs/docker.zh-CN.md](docs/docker.zh-CN.md)）。
- **从 OpenClaw、Hermes 搬过来。** `mousse migrate openclaw|hermes` 先出报告；`--apply` 先备份再写。定时任务搬过来是暂停的，登录的令牌不复制。安装器发现它们时会问要不要搬。
- **没有 systemd 的机器。** `check.sh` 有自带内核的一段，在这种机器上也能用；安装器会打印怎么自己起服务。

### 记忆

- **世界树 0.7**（[tree/CHANGELOG.md](tree/CHANGELOG.md)）：枝（主干是你的档案，另有「人」和每个 Agent 一根）、规则、查重、叶子带着在哪说的、信几分、手册。
- **密钥不进记忆**（0.7 新加的）。像密钥、令牌的字，叶子存下之前就换成占位符：AI 平台记的、写回的、修剪合并的、管理页上改的、挪进档案的都算。交给平台的 `TREE.md` 和档案包，出去时也清一遍。树上已有的叶子不改写。
- **每周修剪。** 每周日一张收件箱卡，每一步各自选。
  - 自带的内核加开源版世界树时开着；`"tree": {"prune": false}` 关。
  - 每周一个无人值守的回合。
- **记忆检索**，搜记忆文件、日结、以前的对话：开着，按关键词，什么都不出服务器。
- **按意思搜。** 关着：`engine.recall.embeddings`。
  - 服务商：Gemini、OpenAI，或者任何 OpenAI 兼容的服务，本机的 Ollama 也行。
  - 开了以后，记忆文件、日结、对话、世界树叶子的字会发给那家，花你的密钥（本机的不花钱）。
- **写回。** 自带的内核开着；`"engine": {"writeback": false}` 关。
  - 一段对话安静下来以后，最多写 3 片叶子进世界树，原话得是你自己说的。
  - 用按量付费的模型，不用套餐，一天最多 40 次。
  - 改档案、规则、手册、技能的，都先出卡。别人说的话先压着，等每周那张卡。

### 页面、今天和 app

- **页面。** Agent 能发一个沙箱里的网页，画看板画不了的东西，可以钉到看板上。要读你的数据，每一版都先问你。手机上要 app 1.0.6 以上。
- **今天。**
  - 类别（学习、生活、工作）、没有日子的待办、筛选、「不用管」。关着：`"today": {"categories": true}`。
  - 一张工程看板卡，来源是一个待办文件或者 GitHub Projects 的看板。关着：`today.engineering`。
- **记忆页**（底栏第四格）：
  - 「记忆」：最近 7 天的新记忆画成几圈，下面是等你确认的，「记下」或者「不记」。
  - 「方向」（你的目标）和「全部记忆」：关于你、朋友、日志、记忆条目、来源。
  - 能搜记忆，不调模型。点开一片叶子能看到它从哪来：原话、什么时候用得上、在哪段对话里说的。
  - 周日那张每周修剪卡放在最上面，一条一条定：同意、修改、拒绝。「修改」照你写的那句重出那一条，是一次零工具的调用：「一问一答」（`engine.use.json`）配成套餐就走套餐，不然按量付费。
- **回复。** iPhone 上能选回复里的一段，只引用、收藏或者转发这一段。每条回复都有 ⋯，里面有「复制整条」。
- **样子。** 默认深色；登录、装好、关于这几页换上新标志：金色的环加一个青点。
- **app 1.0.6。**
  - 扫配对码和好友邀请的二维码，把卡片当图片分享。
  - 蓝牙连 Flipper Zero。
  - 手机上看页面和视频，原生的日期选择。
  - 播客退到后台也接着录，小组件点开是「今天」。
  - Android 上能连 http 的服务器、草稿留着、返回键关掉弹出的面板、表单不被键盘挡住。
- **app 1.0.7（iPhone）。** 给手机节点和对讲用的定位和朗读，给以后功能预先装好的原生模块，权限说明写得更清楚。Android 暂时还是 1.0.6。

### 更新之前

- **不用迁移。** 已经装好的，claw、模型、设置都照旧。
- **从 v2026.10.13-3 更新。** 那一版还没有 `mousse update`，所以重跑一遍安装器（`curl -fsSL https://openmousse.ai/install | bash`）：它会拉新版本、重装依赖和世界树，`server.json` 不动。从这一版起用 `mousse update`。
- **可选的主人迁移。** `mousse owner migrate` 能把库里代表「你自己」的老值（`leo`）换成 `owner`。不加 `--apply` 只报告；加了会先备份。不迁也照样能用。
- **档案的标签。** 你确认过的行标 `[U]`。以前的 `[L]`、`[Gr]` 照样认，老档案不改写。
- **app 默认深色。** 以前每次都跟着系统走；现在没选过就是深色，已经装好的也一样。设置 → 通用 → 外观 能换回来（跟随系统、浅色、深色），选了会记住。
- **底栏第四格换成「记忆」。** 「目标」挪进来成了「方向」，体重图挪到健身看板的「长期」那一节。基础档案、记忆条目、日志、朋友画像从 设置 → 其他 挪到了记忆页。
- **iPhone 上长按改成选字。** 回完的回复长按是选字，菜单里多了「引用这段」「收藏这段」「转发这段」。复制整条和别的操作在回复的 ⋯ 里。
- **`/mcp` 要查请求从哪来。** Host 不是 localhost、服务器监听的具体地址、你的 `share.public_url` 的，一律回 421；Origin 对不上回 403。AI 平台或者 claw 用别的名字、地址连 `/mcp` 的，把它写进 `server.json` 的 `mcp.allowed_hosts`（每次现读，不用重启）。
- **隐去密钥要世界树 0.7.0。** 服务器的 Python 环境装上 0.7.0 才开始挡密钥。`mousse update` 会装上并重启服务；Docker 重建镜像就有。只 `git pull` 的，再跑 `~/.openmousse/venv/bin/python -m pip install ~/.openmousse/repo/tree`，然后重启。装上之前什么都不隐去，也没有任何提示；`mousse doctor` 能看到装的是哪一版。
