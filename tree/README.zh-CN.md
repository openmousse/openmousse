# OpenMousse · 世界树（tree）

**中文** · [English](README.md)

**一份自托管的个人记忆，你所有的 AI 都接到同一棵树上。**

你在 ChatGPT 里说"我早餐改吃燕麦了"，晚上问 Claude 明天吃什么，它已经知道。你的 OpenClaw、Notion AI、Claude Code 也一样。记忆存在你自己的服务器上，一个 SQLite 文件；各平台通过 MCP 读写，来源和日期都留痕。

没有 LLM 调用，不产生模型费用；模型费用是你各平台自己的订阅。

## 它解决什么

每个 AI 平台都有自己的"记忆"，互不相通，而且你拿不走。世界树把记忆反过来：**记忆是主体，平台是枝**。任何平台学到关于你的新东西，写回树；任何平台开口前，先读树。

## 安装（需要 Python 3.11+）

```bash
pipx install "git+https://github.com/openmousse/openmousse#subdirectory=tree"      # 或 pip install --user
mousse-tree init --name 你的称呼 --tz Asia/Shanghai         # 建库、生成各平台令牌
mousse-tree install-service                                 # systemd 常驻，只监听 127.0.0.1:8787
```

有 OpenClaw 的话再加一步，让你的 agent 能检索树的导出：

```bash
mousse-tree install-openclaw        # 备份 openclaw.json，把导出目录加进 memory.search.extraPaths
systemctl --user restart openclaw-gateway
```

档案：`mousse-tree init --profile ~/.openclaw/workspace/USER.md` 指到你已有的 USER.md，或打开管理页写一份。档案里 `## 小节` 下的 `- 要点` 行会进入检索。

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

> 对话开始先调 profile 和 recall 了解我；我说出关于自己的新事实、偏好、决定、近况时调 remember。

## 工具

| 工具 | 作用 |
|---|---|
| `profile()` | 整份档案，开场读一次 |
| `recall(query, limit)` | 关键词检索（FTS5 trigram，中英文都行） |
| `remember(text, kind, tags, observed_at, supersedes)` | 写一条；kind = fact / preference / decision / event |
| `recent(days)` | 最近几天各平台写了什么 |
| `forget(memory_id)` | 遗忘：清正文，留 id 和日期 |

## 管理页

用 `mousse-tree urls` 打印的管理页链接打开（`http://127.0.0.1:8787/ui#key=…`）：按来源看记忆、改、忘记、确认待审条目、编辑档案。管理页的接口只认带管理令牌（`config.json` 的 `ui_token`）的请求，浏览器第一次打开后会记住。`config.json` 里 `require_confirm: true` 可让平台写入先进"待确认"。

## 命令行

```bash
mousse-tree recall --q 早餐
mousse-tree recent --days 7
mousse-tree add --source myclaw --kind decision --text "……"    # 自己的 agent 也能往树上写
mousse-tree stats
```

## 设计

- 一条记忆 = 一句话 + kind + tags + source + observed_at + status。改了就 `supersedes` 旧条目，不追加矛盾。
- 忘记 = 清正文、留骨架，可审计。
- 档案（USER.md）只读进树，在管理页或文件里改。
- 树的导出 `TREE.md` 给不走 MCP 的 agent（如 OpenClaw 的 memory_search）用。
- 还没有的：语义召回（embedding）、冲突检测、多用户。欢迎 PR。

## 许可

MIT
