---
name: goals
description: 用户的长期目标（app 的「目标」页，分健康 / 学业 / 职业 / 财务，比如「体重回到 75 kg 以下」「申请到 2027 秋的硕士」）。用户明确让你加、改、标完成、不做了的，直接改，回一句改了什么；你自己觉得哪个目标该调（读数早进了区间、截止过了、他最近说的对不上），交收件箱等他点头，不要悄悄改；用户没说过的目标不要自己编。聊到目标、体重趋势、「还差多少」时先读这里。命令 python3 ~/.openmousse/repo/server/goals_ctl.py。
---

# 目标

> **有 OpenMousse 的 MCP 工具时（工具名里带 openmousse，比如 openmousse__board）就不用 shell**：下面每条命令对应一个工具——board_ctl.py → board、inbox_ctl.py → inbox、goals_ctl.py → goals、project_ctl.py → project、schedule_ctl.py → schedule、agent_ctl.py → agents、ask_agent.py → handoff、tasks_ctl.py → tasks、proposals_ctl.py → proposals、journal.py → journal、settings_ctl.py → settings、mousse-tree → tree。args 放命令里脚本名后面的词（一个词一项，JSON 整段一项，不用加引号），本来要从标准输入给的放 input；你是某个 Agent 时 agent 填你的 id（主对话不填）。没有这些工具就照原样在 shell 里跑命令。

目标存在助手自己的数据库里（app「目标」页），你和用户都能改。每次改动都记下来；你改的，app 目标页顶上会出一条「撤销」（24 小时内）。

```bash
G="python3 $HOME/.openmousse/repo/server/goals_ctl.py"
$G list                       # 进行中的目标：id、目标数字、现在的读数和进度、截止、归哪个 Agent
$G list --all                 # 连完成的、不做了的
$G trend --metric weight      # 体重：最新一次、近 7 天平均、比几周前变了多少，两个来源各自的读数
$G log                        # 最近的改动（谁改了什么），带改动号
```

## 直接改，还是先问

- **用户明确让你加 / 改的**（「把体脂目标改成 14–16%」「加个目标：年底前体重回到 75 kg 以下」「那个实习不做了」）：直接改。这是助手自己的数据库，改了能撤销，不用再问一遍。改完回一句：「改了：体脂目标 15–18% → 14–16%（目标页顶上能撤销）」。
- **你自己觉得该调的**（读数早就进了区间、截止过了还没做到、他最近说的和目标对不上）：不要直接改，交一张收件箱卡等他点头：

  ```bash
  python3 ~/.openmousse/repo/server/inbox_ctl.py add --kind other --source <你的 id> --level quiet \
    --dedupe "goal:<目标 id>:target" --title "把体脂目标调到 14–16%" \
    --why "9/20 起连着三次在 17% 以下，已经进了现在的区间（15–18%）" \
    --change "体脂目标：15–18% → 14–16%" --approve-label "改目标"
  ```

  他点了同意，你的线程里会来一句「【收件箱】已同意…」：照卡上写的改（`$G update …`），再 `inbox_ctl.py done <ib-…> --result "改好了"`。一天最多提一两条；拿不出证据（日期、读数）就先别提。退出码 3 = 30 天内拒过，放下别再提。
- **用户没说过的目标，不要自己编。** 觉得有个目标值得定，在对话里问他一句，他说要再加。

## 加和改

```bash
$G add --title "体重回到 75 kg 以下" --category 健康 --metric weight --low 72 --high 75 --due 2026-12-31 --agent <Agent id>
$G add --title "申请到 2027 秋的硕士" --category 学业 --due "2027 秋" --detail "目标学校……"
$G update <id> --low 14 --high 16              # 只改给了的；--no-due / --no-detail / --no-agent / --no-low / --no-high 清掉
$G done <id>                                   # 做到了
$G drop <id>                                   # 不做了（不删，app 里折在「不做了的」）
$G reopen <id>                                 # 放回进行中
$G undo <改动号>                                # 撤销（用户说「刚才那个改回去」）
```

- 分类只有四个：健康 / 学业 / 职业 / 财务（也认 health / study / career / finance）。
- `--metric`：`weight` 体重、`bodyfat` 体脂。服务器自己读当前值（训练软件记的为主，Apple 健康对照），不用你填。**体脂从不自己算**：没有公式能从体重推出来。用户报了一个数，就按那个数记进他的训练软件（写外部系统，照 inbox skill 先出卡），这里会自动读到。
- `--low` / `--high` 是目标区间，可以只给一头：只给 `--high` = 降到它以下，只给 `--low` = 到它以上。进了区间就算做到（进度 100%）。
- 标题里写了数字的（「体脂降到 15–18%」），改目标数字时标题一起改（`--title`），别让标题和数字对不上。
- `--due`：`2026-12-31`、`2026-12`，或者「2027 秋」这样的说法。
- `--agent`：这个目标归哪个 Agent 盯（app 里「去看板」跳过去），不给就不挂。
- `--source` 不用写：在自己的工作区里跑就是你，主对话是 main。

## 说到目标的时候

- 先 `list` 看现在的数，别凭记忆说「还差 5 kg」。
- 读数超过 30 天的（list 里写着「超过 30 天没量了」），提醒他量一次，别拿旧数算进度。
- 体重有起伏，看 `trend`：说近 7 天平均和几周来的变化，不拿一天的数下结论。训练软件和 Apple 健康同一天对不上时，trend 会写出来，以训练软件为准，提一句就行。
