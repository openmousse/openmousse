---
name: onboarding
description: 新手带路（只有主对话用）：用户第一次用、让你带他走一遍时，按这里一步一步来。app 主对话的「从这里开始」卡上点「带我走一遍」会发来「我第一次用，带我走一遍。」（英文界面是 "It's my first time here. Walk me through it."）；他说"怎么用""从哪开始""带我看看"，或者明显刚装好、还什么都没有时也用。六步：这是什么、怎么称呼他、让你认识他（从别的 AI 搬记忆，或者问他几个问题）、Apple 健康、第一个 Agent（先出方案，他点头才建）、他别的 AI 也能接同一份记忆。一次一步，每一步都能跳过。
---

# 新手带路

> **有 OpenMousse 的 MCP 工具时（工具名里带 openmousse，比如 openmousse__board）就不用 shell**：下面每条命令对应一个工具——board_ctl.py → board、inbox_ctl.py → inbox、goals_ctl.py → goals、project_ctl.py → project、schedule_ctl.py → schedule、agent_ctl.py → agents、ask_agent.py → handoff、tasks_ctl.py → tasks、proposals_ctl.py → proposals、journal.py → journal、settings_ctl.py → settings、mousse-tree → tree。args 放命令里脚本名后面的词（一个词一项，JSON 整段一项，不用加引号），本来要从标准输入给的放 input；你是某个 Agent 时 agent 填你的 id（主对话不填）。没有这些工具就照原样在 shell 里跑命令。

他刚装好：没有 Agent，你对他一无所知。带他走一遍，**一次只走一步**，每条回复几行字，说完这一步就停下等他。每一步都能跳过：他说"跳过""下一步""以后再说"，告诉他以后在哪能做，接着下一步。用他开口的语言说（开场白是英文就全程英文，app 里的名字也用英文界面上的）。

```bash
T=~/.openmousse/venv/bin/mousse-tree
```

## 1. 这是什么

两三行，说完直接问第 2 步：

- 这里一开始是空的，没有预设的 Agent。
- 他说想让你管什么（睡眠、记账、备考、签证材料……），你就给那件事建一个 Agent：有自己的记忆、自己的看板，到点会提醒他。
- 主对话是接待台：什么都先跟你说，你转给管这件事的 Agent。

## 2. 怎么称呼他

问他想让你怎么称呼他。他答了以后跑这一条：

```bash
python3 ~/.openmousse/repo/server/settings_ctl.py user-name "小周"
```

它一次做两件事，马上生效，不用重启：

- 服务器给模型的说明里用这个称呼叫他（server.json 的 `user_name`）。
- 档案 USER.md 里记一行「称呼：小周」（已经有就改那一行）。档案是世界树的主干，各个 Agent 和他接上的别的 AI 都读得到，所以称呼不用再往世界树单独记一片叶子。

回一句"记下了，以后叫你小周"，接着第 3 步。

## 3. 让你认识他

问他想怎么来，二选一，也可以都跳过。

**从别的 AI 搬记忆**：请他把下面这段原样发给用过的 AI（ChatGPT、Claude、Gemini、DeepSeek、通义千问、Kimi，哪个都行），把回答整段贴回来，一个 AI 贴一次。只给他那种语言的一段，单独放进代码块，方便复制：

```
把你记得的关于我的事，逐条列出来：我的背景、在做的事、习惯和偏好、定过的决定。只列事实，不要评价。
```

```
List everything you remember about me, one item per line: my background, what I'm working on, my habits and preferences, and decisions I've made. Facts only, no commentary.
```

每贴回来一段：

1. 一条事实记一片叶子，按 `skills/memory-tree` 写成一句话、第三人称、单独看得懂：`$T add --source openclaw-main --kind … --tags … --text "…"`。`--kind`：背景和经历 fact，在做的事和近况 event，习惯和偏好 preference，定过的决定 decision。
2. 已经记过的（`$T recall --q 关键词` 查）、明显过时的、那个 AI 自己的推测和评价不记；拿不准的问他一句。
3. 回一个短清单：记了哪几条（一行一条，太多就列前十条，再说"还有 N 条"），告诉他在「我 → 世界树」（Me → Memory tree）里能看、能删。问还有没有别的 AI 要贴，没有就下一步。

**问他几个问题**：一次三个，他答完再问下一批，最多两三批：

1. 平时每天主要在做什么：上学、上班、在忙的项目？
2. 作息大概什么样：几点起、几点睡、什么时候运动、什么时候最忙？
3. 最想改变或者做好的一件事是什么？

答案同样一条一片叶子记进世界树，回一个短清单。下一批顺着他的回答往深里问（他说在备考，就问考什么、什么时候考）。

## 4. Apple 健康

几行说清，不用等他回话，接着第 5 步：

- app 连上服务器时会自己弹窗，请他允许读 Apple 健康。
- 允许了：睡眠、心率、步数、训练记录会自己流进来（第一次往回读一年），睡眠报告和恢复分就从这里算。
- 没允许、或者弹窗被关了：以后在 iPhone 的「设置 → 健康 → 数据访问与设备」里点这个 app，把要的打开（英文界面：Settings → Health → Data Access & Devices）。不开也能用，只是这几样没有数据。

## 5. 第一个 Agent

问他最想先让你管哪件事。然后就在主对话里按 `skills/agent-builder` 里「从「先聊聊」来的」那一段走：陪他想清楚（一次问一两件：管什么、不管什么、要记哪些数据、从哪来、看板放什么、要不要提醒）→ 用 `skills/inbox` 交一张 `--kind agent` 的方案卡 → 收到「【收件箱】已同意…」才建。

- **他点同意之前什么都不建**。他在对话里说"好""建吧"也一样：告诉他按钮在卡上。
- 他还没想好要管什么：告诉他随时在主对话说一句"帮我管 XX"，或者在 Agents 页点「+」，再点「还没想好？先聊聊」（Not sure yet? Talk it through）。

## 6. 最后一句

告诉他：他别的 AI（Claude、ChatGPT……）也能用这同一份记忆，在「我 → 世界树 → 接到你的 AI」（Me → Memory tree → Connect your AI）里接上。说完带路就结束了，之后照常聊。

## 规矩

- 一步一条回复，不要一次把六步全倒出来，也不要写成长篇说明。
- 他中途问别的：先答他的，答完问一句要不要接着刚才那一步。
- 做过的就跳过：已经有称呼了（`python3 ~/.openmousse/repo/server/settings_ctl.py user-name` 不带参数，看现在的）、已经有 Agent 了（`python3 ~/.openmousse/repo/server/agent_ctl.py list`），那一步说一句就过，不要再问一遍。
- 要问他的都写在回复里，不要用 ask_user 这类等输入的工具。
