---
name: agent-builder
description: 在对话里新建 / 删除 Agent。当用户说"帮我做个 XX agent"、"建一个管 XX 的 Agent"、"把 XX Agent 删了"、"现在有哪些 Agent"时使用。新建的 Agent 是 OpenClaw 里的独立 agent：自己的工作区、记忆和 skills，app 的「Agents」页立刻能看到。
---

# 新建 / 删除 Agent

```bash
A=~/.openmousse/repo/server/agent_ctl.py
python3 $A list
python3 $A create --name 睡眠 --purpose "每天早上解读昨晚睡眠，解释每项指标，给作息建议。" --icon moon
python3 $A delete g-xxxxxxxx
```

图标：dumbbell 训练 / utensils 饮食 / book 学习 / wallet 财务 / moon 睡眠 / briefcase 求职 / heart 健康 / plane 出行 / coffee 咖啡 / music 音乐 / camera 拍照 / code 代码 / cart 购物 / home 家 / car 车 / paw 宠物 / leaf 植物 / gamepad 游戏 / palette 画画 / globe 语言 / graduation 申请 / lightbulb 灵感 / trophy 比赛 / pill 吃药。
颜色：`--color cyan / gold / green / purple / pink / orange`，不给就是默认的青色。改已有的 Agent：`python3 $A update <id> --name … --purpose … --icon … --color … --model …`，只给要改的；改名字或职责会同步改它 IDENTITY.md 里标记的那一段。
模型默认跟主对话一样，用户说了再用 `--model`。skills 默认是 server.json 里 `agent_default_skills` 那几个，要别的用 `--skills a,b,c`（名字见工作区的 `skills/` 目录）。

## 怎么做

1. **先把职责写清楚再建**：一两句话，说清这一块管什么、不管什么。用户只说了个名字就先问一句它该管什么；他说"你看着办"就按名字合理地写。
2. 建好后告诉用户：Agent 的名字、id、它管什么，以及"app 的 Agents 页能看到，之后属于这一块的事我会转给它"。**不要**自己替它答第一个问题——让用户去它的页面说，或者用 handoff 转过去。
3. 删除要用户明确说。删了之后工作区和记忆归档在 OpenClaw 目录的 `archive/`，对话记录留着。
4. 建好的 Agent 从此归 handoff 管：`ask_agent.py --list` 会带上它。

## 从「先聊聊」来的：先出方案，用户点头再建

用户在 app 的新建页点了「还没想好？先聊聊」，会开一个独立空间，第一条消息说他想建一个 Agent、还没想好它管什么。这时：

1. 陪他想清楚，一次问一两件：它管什么、不管什么；要记哪些数据、从哪里来；看板上放什么；和哪些 Agent 联动；要不要定时提醒（新推送要他另外点头）。
2. 想清楚后**不要直接建**，用 inbox skill 出一张卡：`--kind agent --title "新建 Agent「名字」" --why "为什么值得单独一个 Agent" --change "负责：…" --change "记这些数据：…" --change "看板：…" --change "图标 moon · 颜色 purple" --approve-label 建好它 --dedupe agent-<名字>`。
3. 收到【收件箱】已同意的消息后，用 `create --name … --purpose … --icon … --color …` 建好，再 `inbox_ctl.py done <id> --result "建好了：名字（id）"`。
4. 用户在新建页直接填好点「创建」的，不走这里。
