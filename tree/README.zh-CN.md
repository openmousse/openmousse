# OpenMousse · 世界树（tree）

**中文** · [English](README.md)

**一份自托管的个人记忆，你所有的 AI 都接到同一棵树上。**

你在 ChatGPT 里说"我早餐改吃燕麦了"，晚上问 Claude 明天吃什么，它已经知道。你的 OpenClaw、Notion AI、Claude Code 也一样。记忆存在你自己的服务器上：一个 SQLite 文件，或者一个 Markdown 笔记文件夹（比如放进 Obsidian 库，手机上也能看、能改）；各平台通过 MCP 读写，来源和日期都留痕。

没有 LLM 调用，不产生模型费用；模型费用是你各平台自己的订阅。

## 它解决什么

每个 AI 平台都有自己的"记忆"，互不相通，而且你拿不走。世界树把记忆反过来：一棵树，所有平台都来读、都来长。**主干是你的档案，枝是你生活的各个领域（每个 Agent 一根），叶子是一条条记忆。** 任何平台学到关于你、关于你身边的人的新东西，写回树；任何平台开口前，先读树和它的规则。树一周修剪一次，你点了头才动。

## 安装（需要 Python 3.11+）

```bash
pipx install "git+https://github.com/openmousse/openmousse#subdirectory=tree"      # 或 pip install --user
mousse-tree init --name 你的称呼 --tz Asia/Shanghai         # 建库、生成各平台令牌
mousse-tree install-service                                 # 开机常驻（Linux：systemd；macOS：launchd），只监听 127.0.0.1:8787
```

语言：`mousse-tree init --lang zh`（或 `en`）决定各平台看到的说明、工具描述、工具回话，以及命令行输出用中文还是英文。不给的话，第一次 `init` 看环境变量 `LANG`（zh 开头 → 中文，其它 → 英文）；加这个选项之前建的配置保持中文。改了之后重启服务生效。

有 OpenClaw 的话再加一步，让你的 agent 能检索树的导出：

```bash
mousse-tree install-openclaw        # 备份 openclaw.json，把导出目录加进 memory.search.extraPaths（OpenClaw 不在默认位置：--openclaw-home DIR）
systemctl --user restart openclaw-gateway
```

档案：`mousse-tree init --profile ~/.openclaw/workspace/USER.md` 指到你已有的 USER.md，或打开管理页写一份。档案里 `## 小节` 下的 `- 要点` 行会进入检索。

## 存储：SQLite 或 Markdown 文件夹

默认记忆存在 `~/.mousse-tree/tree.db`。也可以一条记忆一篇 Markdown 笔记，比如放进 Obsidian 库里的一个文件夹：

```bash
mousse-tree migrate markdown --notes ~/vault/世界树 --profile-note 档案.md   # 已有的记忆导成笔记，tree.db 原样留着
systemctl --user restart mousse-tree
```

新装直接用：`mousse-tree init --storage markdown --notes ~/vault/世界树`。

- 笔记属性（front matter）放 `id`、`kind`、`source`、`observed_at`、`status`、`branch`（`"[[人]]"`；`"[[档案]]"` = 直接挂主干）、`tags`、`supersedes`、`created_at`、`updated_at`，0.4 起可选的 `quote`、`platform`、`thread`、`message`（在哪儿说的）和 `cues`（用得上的时候），正文就是那句话。文件名是日期加那句话的开头。树改写一篇笔记（确认、取代、挪枝）时，你在 Obsidian 里自己加的属性原样留着。
- 枝也是笔记，一枝一篇放在 `branches/`（见下面），规则是根下的 `rules.md`；这两样不算记忆。
- 文件夹根下只放当前有效的记忆。被取代的挪进 `归档/`（英文是 `Archive/`），你自己挪进去的也不再算数。遗忘 = 笔记改成空壳（只剩属性，文件名换成 id）放进 `归档/`、删索引、记一条不含内容的操作记录。如果文件夹由同步服务（Obsidian Sync、iCloud、git……）带着，它的版本历史里可能还留着旧版本一段时间。
- 笔记是真身，SQLite（`index.db`）只当检索索引和操作记录，文件夹一变就从笔记重建；服务每 5 秒看一眼，手机上改了笔记，下一次 recall 就能看到。手动重建：`mousse-tree rebuild`。
- 自己新建的、没有属性的笔记也收（id 按路径算，source 是 `owner`）。属性写坏的笔记会跳过，别的照常检索，管理页顶部会提示（`mousse-tree check` 也能看）。软链和隐藏文件不收，MCP 工具碰不到文件夹以外的东西。
- `--profile-note 档案.md` 在文件夹里放一份档案，和 `profile_path` 双向同步：改哪边都行；两边同时改以笔记为准，另一版存进 `~/.mousse-tree/profile-conflicts.md`。故意不用软链：OpenClaw 的记忆检索会跳过软链文件。
- 换回：`mousse-tree migrate sqlite`，笔记写回 `tree.db`。

## 暴露到公网

Claude.ai、ChatGPT、Gemini、Notion 都是从它们的云来连你，所以需要公网 HTTPS。最省事是 Tailscale Funnel（免费）：

```bash
tailscale funnel --bg --set-path=/t http://127.0.0.1:8787/t
tailscale funnel --bg --set-path=/m http://127.0.0.1:8787/m
mousse-tree init --host <你的机器名>.<tailnet>.ts.net    # 加进 Host 白名单，否则 MCP SDK 会 421
systemctl --user restart mousse-tree
```

只暴露 `/t` 和 `/m` 两个前缀。管理页 `/ui` 不要用 Funnel 暴露，用 `tailscale serve`（仅 tailnet）或 SSH 隧道打开。

## 接平台

```bash
mousse-tree urls     # 打印各平台的接入地址（含秘密令牌，只在自己终端看）
```

| 平台 | 在哪加 | 用哪种地址 |
|---|---|---|
| Claude.ai | Settings → Connectors → Add custom connector，认证留空 | `/t/<token>/mcp` |
| ChatGPT（Plus） | Settings → Apps & Connectors → Advanced → Developer mode → Create，认证 None | `/t/<token>/mcp` |
| Gemini | 网页 Settings → Connected Apps → Add a custom app（官方要求人在美国） | `/t/<token>/mcp` |
| Notion（Business+） | Custom Agent → Tools & Access → Custom MCP server，认证选 Bearer token | `/m/mcp` + 令牌 |
| Claude Code | `claude mcp add --transport http tree <url>` | `/t/<token>/mcp` |

每个平台一个令牌，服务端凭令牌知道是谁写的，不用问模型。

然后在每个平台的自定义指令里加一句，并关掉它自带的记忆：

> 对话开始先调 profile，读我的档案和世界树的规则，照规则记。我说出关于自己、身边的人或长期约定的新信息时，先 recall 查有没有：有就用 supersedes 取代旧的，没有再 remember，挂到合适的枝上。

## 工具

| 工具 | 作用 |
|---|---|
| `profile()` | 档案、世界树的规则和枝的列表，开场读一次 |
| `recall(query, limit)` | 关键词检索（FTS5 trigram，中英文都行）；待确认的也在，标着待确认 |
| `remember(text, kind, tags, observed_at, supersedes, branch, quote, conversation, cues)` | 写一条；kind = fact / preference / decision / event；branch 可不填（见下面）；quote、conversation、cues 也可不填（见下面） |
| `recent(days)` | 最近几天各平台写了什么 |
| `forget(memory_id, also_profile)` | 遗忘：清正文，留 id 和日期；会列出档案里从它来的句子（见下面） |

## 枝、规则和每周修剪

- **树的样子**：主干是档案（`档案`，英文是 `Profile`；第一次跑时定下来，以后换语言也不变）。枝按生活领域分大枝、按功能分小枝（OpenMousse 的每个 Agent 一根），每片叶子挂在一根枝上。新树只有主干和一根「**人**」（别名：家人、伴侣、朋友、同学、同事、约定）。在 OpenMousse 里每建一个 Agent 就给它长一根小枝；别的枝靠每周修剪长出来。从 0.2 升级：已有的记忆一片不动（没有枝的算主干），第一次跑补上「人」。
- **枝放在哪**：SQLite 存储在 `tree.db` 的 `branch` 表。Markdown 存储在 `branches/` 里一枝一篇：文件名就是枝名，属性 `parent`（大枝是 `"[[档案]]"`）、`aliases`、`agents`（默认往这根枝上写的 Agent id）、`order`，正文第一行写它管什么。在 Obsidian 里像别的笔记一样改。
- **新叶子挂哪**：写的人指定的（`branch`，枝名或别名）> 被取代的那片原来的枝 > 写的 Agent 自己那根（某根枝的 `agents` 里有它；命令行从环境变量 `MOUSSE_AGENT` 认出是哪个 Agent，来源也记成它）> 标签对上枝名或别名的 > 主干。
- **出处和「用得上的时候」**（0.4）：一片叶子可以带上用户当时的原话（`quote`）和来自哪次对话（MCP 是 `conversation`；命令行是 `--thread` / `--message` / `--platform`，OpenMousse 经 `MOUSSE_THREAD` / `MOUSSE_MESSAGE` 自动补上）。`recall` 返回时带着原话。`cues` 是两到五句用户以后可能会怎么问起这件事，和正文、标签一起检索，也进导出，说法换了也找得回来。每周修剪把合并、改写、并进档案的那几步拿去回原话核对（对话在同一台服务器上才查得到，也就是 OpenMousse），对不上的默认不做。老库第一次打开时补上这几列，叶子一片不动。
- **查重**：写之前把这句话（去掉标记和日期）和当前的叶子、档案要点比一遍。相似度 0.9 以上的不写，回话指出已有的那条，让写的人用 supersedes 取代；0.6 以上的照写，回话里列出相近的。
- **规则**：树目录里的 `rules.md`（Markdown 存储是笔记文件夹，SQLite 存储是 `~/.mousse-tree`），第一次跑按你的语言写一份、带上你的称呼，之后不再覆盖。`profile` 把它和档案、枝一起给每个平台，改它就改了所有 AI 记东西的规矩。清空 = 不要规则；删掉 = 下次换回默认的。
- **每周修剪**：`mousse-tree prune-context` 列出可剪的：稳定两周以上的叶子（可以并进档案对应的那一节）、满 30 天的近况、相近的几片、挂错枝的、超过 30 片的枝。模型写一份计划（`merge` 合并、`rewrite` 改写、`archive` 归档、`promote` 并进档案、`branch` 挪枝、`sprout` 长一根新枝），`mousse-tree prune-plan --file 计划.json` 校验后存下，`mousse-tree prune-apply` 照着做，做过的步骤跳过。OpenMousse 每周日晚上自动跑：主对话写计划，计划做成一张收件箱卡，你点「照这样修剪」才动。
- **来源**：合并、改写出来的叶子记着从哪几片来，修剪并进档案的每一句记着从哪片叶子来（`~/.mousse-tree/origins.json`）。忘掉一片叶子时会列出档案里从它来的句子，让你决定要不要一起删（`forget --with-profile`、`also_profile`，管理页会问）。

## 管理页

用 `mousse-tree urls` 打印的管理页链接打开（`http://127.0.0.1:8787/ui#key=…`）：按来源看记忆、改、忘记、确认待审条目、编辑档案。管理页的接口只认带管理令牌（`config.json` 的 `ui_token`）的请求，浏览器第一次打开后会记住。页面语言跟浏览器走。`config.json` 里 `require_confirm: true` 可让平台写入先进"待确认"。

## 命令行

```bash
mousse-tree recall --q 早餐
mousse-tree recent --days 7
mousse-tree add --source myclaw --kind decision --text "……"    # 自己的 agent 也能往树上写（--branch 枝名 指定挂哪根）
mousse-tree profile                                            # 档案 + 规则 + 枝，和平台看到的一样
mousse-tree branches                                           # 有哪些枝
mousse-tree sprout 健身 --about "训练和恢复" --agent fitness     # 手动长一根枝
mousse-tree move <id> 人                                       # 把一片叶子挪到别的枝
mousse-tree forget <id> [--with-profile]                       # 遗忘；--with-profile 连档案里从它来的句子一起删
mousse-tree prune-context | prune-plan --file 计划.json | prune-apply   # 手动走一遍每周修剪
mousse-tree stats
mousse-tree check      # Markdown 存储：格式有问题的笔记
mousse-tree rebuild    # Markdown 存储：从笔记重建索引
```

## 设计

- 一条记忆 = 一句话 + kind + tags + source + observed_at + status + branch。改了就 `supersedes` 旧条目，不追加矛盾；几乎一样的写之前就挡下来。
- 忘记 = 清正文、留骨架，可审计。
- 存储是一个 SQLite 文件或一个 Markdown 笔记文件夹（见「存储」），工具、导出和管理页两种都一样。
- 档案（USER.md）只读进树，在管理页或文件里改。唯一的例外是每周修剪：你同意计划以后，往对应那一节末尾加一行。
- 树的导出 `TREE.md` 给不走 MCP 的 agent（如 OpenClaw 的 memory_search）用。
- 还没有的：语义召回（embedding）、多用户、管理页里的修剪界面（OpenMousse 里是一张收件箱卡）。欢迎 PR。

## 许可

[AGPL-3.0](LICENSE)，和 OpenMousse 其余部分一样；商业授权开一个 [GitHub issue](https://github.com/openmousse/openmousse/issues)。2026 年 9 月 28 日之前发布的版本是 MIT，仍然可以按 MIT 使用。
