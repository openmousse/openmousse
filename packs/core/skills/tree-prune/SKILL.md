---
name: tree-prune
description: 世界树的每周修剪（只有主对话用）：收到「【世界树每周修剪】」的定时任务、用户让你整理世界树、或者在对话里回复修剪那张收件箱卡时用。先看这周可剪的（tree prune-context），写一份修剪计划（JSON）交上去（tree prune-plan），服务器出一张收件箱卡，用户点「照这样修剪」才动。多数星期没几件可剪的，没有就不交。
---

# 世界树每周修剪

> **有 OpenMousse 的 MCP 工具时（工具名里带 openmousse，比如 openmousse__board）就不用 shell**：下面每条命令对应一个工具——board_ctl.py → board、inbox_ctl.py → inbox、goals_ctl.py → goals、project_ctl.py → project、schedule_ctl.py → schedule、agent_ctl.py → agents、ask_agent.py → handoff、tasks_ctl.py → tasks、proposals_ctl.py → proposals、journal.py → journal、settings_ctl.py → settings、mousse-tree → tree。args 放命令里脚本名后面的词（一个词一项，JSON 整段一项，不用加引号），本来要从标准输入给的放 input；你是某个 Agent 时 agent 填你的 id（主对话不填）。没有这些工具就照原样在 shell 里跑命令。

世界树：主干是档案（用户是谁），枝按生活领域分大枝、按功能分小枝，叶子是一条条记忆。各平台和 Agent 平时只长叶子；一周修一次，把树理干净。规则在 `tree profile` 里（「怎么进化（每周修剪）」那一节），用户改过就照改过的来。

## 1. 看材料

```bash
T=~/.openmousse/venv/bin/mousse-tree
$T prune-context      # 这周可剪的：稳定两周以上的、满 30 天的近况、相近的几片、挂错枝的、超过 30 片的枝；还有现在的枝和档案的小节
```

写着「这周没有可剪的」就不交计划：定时任务里只回 `[SILENT]`。

## 2. 想清楚每一片

- **稳定两周以上、以后一直用得上的** → `promote`：并进档案对应的那一节（section 用档案里已有的小节名；确实没有合适的才新开一节），text 写成档案里的一句要点（不带日期、不写「最近」）。叶子会归档。
  - 「人」那根枝（和它下面的小枝）上的叶子是关于别人的，**不进档案**。
  - 近况（event）、还在变的事、只在某个 Agent 那块有用的，不进档案。
- **满 30 天的近况** → 过时了 `archive`；还算数但说法旧了 `rewrite`；照旧有效就不动。
- **相近的几片** → 说的是同一件事才 `merge` 成一片（text 写合成以后的一句，取最新的说法）；只是话题像，不动。
- **挂错枝的** → `branch` 挪到该挂的那根（材料里有建议；不认同就不动）。
- **超过 30 片的枝** → `sprout` 长一根小枝（parent 写那根大枝），把属于它的叶子用 move 挪过去。别的时候不长新枝；功能 Agent 的枝服务器会自己长。
- 拿不准的不动，留到下周。宁少勿多：一周 0 到 15 步。

## 3. 写计划、交上去

计划是一个 JSON；steps 按顺序做，每步可以写 why（一句话，出现在卡上）。一片叶子只能出现在一步里。id 就是材料里方括号里的那个。

```json
{"summary": "这周合并了两条早餐的说法，一条偏好进了档案",
 "steps": [
  {"op": "merge", "ids": ["a1b2c3d4e5f6", "0f9e8d7c6b5a"], "text": "小林早餐吃燕麦加鸡蛋", "why": "说的是同一件事"},
  {"op": "promote", "id": "1234abcd5678", "section": "偏好", "text": "不吃香菜", "why": "两周都没变"},
  {"op": "archive", "id": "abcdef123456", "why": "考试已经考完了"},
  {"op": "rewrite", "id": "aa11bb22cc33", "text": "小林每周二、四去游泳"},
  {"op": "branch", "id": "dd44ee55ff66", "to": "人"},
  {"op": "sprout", "name": "运动", "parent": "身体", "about": "跑步、游泳、球类", "move": ["9988aabbccdd"]}
 ]}
```

- `merge`：ids 至少两片；branch、kind 可不写（沿用第一片的）。
- `promote`：section、text；叶子归档，档案那一节末尾多一行，记下这一句从哪片叶子来（用户以后忘掉那片叶子，会问用户要不要一起删这一句）。
- `branch`：to 写枝名（别名也行），写主干的名字 = 直接挂主干。
- `sprout`：name（新枝名，不能和已有的枝、别名重名）、parent（上一级，不写 = 长在主干上）、about（这根枝管什么，一句话）、aliases（可选）、move（可选，顺手挪过去的叶子）。

```bash
$T prune-plan --file - < 计划.json      # 经 tree 工具：args 写 prune-plan --file -，计划放 input
```

- 校验不过会一条一条说哪步不对（叶子不在了、一片用了两次、枝对不上……）：改好再交。
- 交上去了：服务器出一张收件箱卡（「世界树每周修剪：N 处」），一步一条，用户可以逐条同意、修改、拒绝，最后提交；不点就不动；一周后过期。
- 合并、改写、并进档案的那几步，服务器先拿叶子的原话回那次对话核对（卡上每条写着「与原对话相符 / 不符 / 没有出处」）：
  对不上、找不到原话的默认不做，用户可以改选。所以没有原话的推测别急着并进档案。
- 同一周再交一份 = 原地换掉那张卡。交完别再交；定时任务里只回 `[SILENT]`，用户让你修的就说一句卡在收件箱里。

## 之后

- 用户提交了（或者在老版 app 里点了「照这样修剪」= 每条照默认的来）：服务器只做同意的那几步、卡片标完成，你不用做什么，也不会收到消息。
- 用户在卡上「修改」了某一条：服务器按用户写的那句重出那一条，你也不用管。
- 用户在对话里回复这张卡、说要改：照用户说的改好，用 `prune-plan` 重新交（同一周会原地换掉这张卡），别用 `inbox_ctl update`。
- 用户拒了：这一周就不修了，别再交，下周照常。
- 你不能自己跑 `prune-apply`：只有用户点了卡，服务器才照计划做。
