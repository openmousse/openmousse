---
name: memory-tree
description: 世界树：用户跨平台共享的个人记忆。Claude.ai / ChatGPT / Gemini 和这里的各 Agent 都读写同一棵树：主干是档案，枝按生活领域和功能分，叶子挂在枝上。学到关于用户本人、用户身边的人（家人、伴侣、朋友、同学、同事）或长期约定的新东西时写进去；回答涉及用户的偏好、近况、身边的人时先查。写之前先看规则、先查重。
---

# 世界树（memory-tree）

> **有 OpenMousse 的 MCP 工具时（工具名里带 openmousse，比如 openmousse__board）就不用 shell**：下面每条命令对应一个工具——board_ctl.py → board、inbox_ctl.py → inbox、goals_ctl.py → goals、project_ctl.py → project、schedule_ctl.py → schedule、agent_ctl.py → agents、ask_agent.py → handoff、tasks_ctl.py → tasks、proposals_ctl.py → proposals、journal.py → journal、settings_ctl.py → settings、mousse-tree → tree。args 放命令里脚本名后面的词（一个词一项，JSON 整段一项，不用加引号），本来要从标准输入给的放 input；你是某个 Agent 时 agent 填你的 id（主对话不填）。没有这些工具就照原样在 shell 里跑命令。

用户在任何 AI 平台说过的关于自己和身边的人的事，都汇进同一棵树。你读到的 `shared/tree/TREE.md`（memory_search 能搜到）就是树的导出；写入用下面的命令。

```bash
T=~/.openmousse/venv/bin/mousse-tree
$T profile      # 档案 + 世界树的规则 + 枝的列表：写之前先看一遍，照规则来（用户改过规则就照改过的）
```

## 什么时候写

- 规则里写的那几类，别的平台或别的 Agent 也用得上：
  - 用户本人的**新事实、偏好、决定、近况**；
  - **身边的人**：家人、伴侣、朋友、同学、同事是谁、和用户什么关系、在意什么（喜好、忌口、重要的日子）。一片叶子写一个人的一件事，挂「人」这根枝；
  - **长期的约定**：用户答应别人的、别人答应用户的、说好定期做的（一次性、有日子的事进日程，不进树）。
- 一条一句话、第三人称、能独立理解："早餐改成燕麦加鸡蛋"要写成"<名字>早餐改成燕麦加鸡蛋"，不是"用户说早餐改了"。写别人用用户平时的叫法。
- 不写：临时闲聊、你的推测、本 Agent 自己的业务数据（那些进你自己的记忆和服务数据库）、密钥和账户、身体和训练的数字。

## 怎么写

1. **先查重**：`$T recall --q "关键词"`。同一件事已经有了，就写新版并把旧的 id 填进 `--supersedes`，不另起一条。
2. 写：

```bash
$T add --kind preference --tags 饮食 --text "…早餐改成燕麦加鸡蛋" --quote "以后早上就燕麦加两个蛋吧" --cue "早餐吃什么" --cue "记一顿早饭"
$T add --kind fact --branch 人 --text "…的妈妈 3 月 8 日生日"
$T add --kind decision --supersedes <旧 id> --text "…"
```

- `--kind`：fact 事实 / preference 偏好 / decision 决定 / event 近况。`--observed YYYY-MM-DD` 是事情发生的日期，默认今天。
- **出处**（`--quote`）：用户当时那句原话，照抄、不改写（太长就截他说的那一段）。每周修剪会拿它回这次对话核对，对不上的不并进档案；
  你自己推出来的、用户没这么说过的，就是推测，别写进树。经 OpenMousse 的 tree 工具写时是哪次对话、哪条消息服务器会补上，不用写 `--thread`。
- **用得上的时候**（`--cue`，可以写两到五次）：用户以后可能会怎么问起这件事，换个说法也找得回来，比如「这碗面多少卡」「睡少了还练不练」。
- `--branch`：挂到哪根枝（`$T branches` 看有哪些，别名也行）。不写就自动挂：被取代的那片原来的枝 > 你自己那根枝（功能 Agent 建好时就有一根）> 标签对上枝名或别名的 > 主干。拿不准就不写，每周修剪时再分。
- 经 OpenMousse 的 tree 工具写时不用写 `--source`：是你写的，树就记成你（环境变量 MOUSSE_AGENT）。在 shell 里跑（OpenClaw）写 `--source openclaw-<你的 agent id>`。
- 回出来的 JSON：`duplicate: true` = 已经有几乎一样的（`id` 是那一片）：要更新就带 `--supersedes <那个 id>` 再写；`similar` 里列着相近的几条，说的是同一件事的话也该用 supersedes。

## 什么时候读

- 先 `memory_search` 搜 TREE.md 就够；要精确找 id 或看最近几天（待确认的也在，标着待确认）：

```bash
$T recall --q "早餐"
$T recent --days 7
```

## 忘记

用户明确要求忘记时：`$T forget <id>`。档案（USER.md）里的条目不在这里删，告诉用户去改档案。
忘掉的那片叶子要是每周修剪时并进过档案，会列出档案里从它来的句子：问用户要不要一起删，用户说要就 `$T forget <id> --with-profile`。

## 每周修剪

一周一次由主对话做（tree-prune skill）：并进档案、归档过时的、合并重复的、挪到对的枝、分出小枝，做成一张收件箱卡，用户点了才动。平时写叶子的时候不用管。
