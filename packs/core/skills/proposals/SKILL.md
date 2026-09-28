---
name: proposals
description: 日结提案（只有 main 用）：每晚日结后收到「【自动触发】日结提案」时，回看这一周用户在各个对话里说的话，把他反复让你做的事、一次跑通的多步流程，提成「加一个 skill」或「建一个 Agent」交进收件箱，用户点头才装。多数晚上没有要提的。
---

# 日结提案：把反复做的事固定下来

> **有 OpenMousse 的 MCP 工具时（工具名里带 openmousse，比如 openmousse__board）就不用 shell**：下面每条命令对应一个工具——board_ctl.py → board、inbox_ctl.py → inbox、goals_ctl.py → goals、project_ctl.py → project、schedule_ctl.py → schedule、agent_ctl.py → agents、ask_agent.py → handoff、tasks_ctl.py → tasks、proposals_ctl.py → proposals、journal.py → journal、settings_ctl.py → settings、mousse-tree → tree。args 放命令里脚本名后面的词（一个词一项，JSON 整段一项，不用加引号），本来要从标准输入给的放 input；你是某个 Agent 时 agent 填你的 id（主对话不填）。没有这些工具就照原样在 shell 里跑命令。

每晚 03:45 日结之后，你会收到「【自动触发】日结提案」。先看材料：

```bash
P=~/.openmousse/repo/server/proposals_ctl.py
python3 $P context      # 这一周用户在各个对话里说过的话（→ 后面是回复的开头）、现有的 skills 和谁能用、各个 Agent、提过的提案、今天还能提几条
```

然后决定提 **0–2 条**。多数晚上是 0 条：没有就回一行「今天没有要提的」，什么都不交。从聊天渠道（Telegram 等）来的对话不在材料里，只在你今天的会话里看得到。

## 什么值得提

- **同一类要求这周出现 3 次以上**：不同天、不同说法、不同对话都算同一类。比如三次让你「换一样食材，重算这顿能吃多少」。
- **用户说过「以后都这样」「每次都……」**，而且是一套做法，不只是一个偏好。
- **今天一次跑通的多步流程**：三步以上、用到几个工具或 Agent，以后很可能再来。比如「从 Drive 找 CV → 按 JD 改 → 存进 drafts」。

## 不提

- 一次性的事，只出现过一两次的事。
- 现有 skill 已经管的（材料里写了每个 skill 做什么、谁能用）。那个 skill 没写到位的，别提新 skill，写进今天的日记，让维护这套系统的人去改。
- 提过的（材料最后一节）：做好了的、被拒的、还在等的。**换个说法、换个名字也算同一件事。** 被拒的理由要照做。
- 只是一个偏好或一个事实（「早餐不吃蛋黄」）：进记忆或世界树，不用做成 skill。
- 要花钱、发给别人、加定时任务或新推送的做法：这些不能做成自动执行的 skill。

## skill 还是 Agent

- 默认提 **skill**：一套做法，挂到现有的 Agent 上。哪块的事就给管那块的 Agent，跨几块的给 main。
- 只有**一整块长期的事**才提 **Agent**：它有自己要记的数据、要看板、会天天用，而且现有的 Agent 都不管。这种一个月也难得有一次。

## 怎么提

1. **why**：一句话，带次数和时间：「这周你第 3 次让我……」「今天我们一起把……跑通了，以后……」。
2. **evidence**：每次一条，写成「日期|在哪个对话|原话」。原话照抄，截短到 30 字以内。至少 2 条；多步流程可以只有 1 条。
3. **slug**：这件事的固定键，小写英文加连字符（`meal-swap`）。同一件事永远用同一个。
4. **skill 草稿**：照现有 skill 的写法（参考你 `skills/` 里现有的），先写进一个临时文件：

   ```markdown
   ---
   name: meal-swap
   description: 用户说「XX 没了」「换成 XX 能吃多少」时，按今天剩下的额度重算这一餐的份量。
   ---

   # 换食材重算份量

   1. 第一步做什么：用哪个已有的脚本或 skill，命令写全。
   2. ……
   3. 回复怎么写：先给一句结论。

   ## 边界
   - 什么不做，什么要先问。
   ```

   - description 写清**什么时候用**：用户会怎么说。
   - 只用已经有的脚本和 skill，不要编命令。写不出具体做法的就别提。
   - 不写密钥、账号，也不写用户的具体数字（数据在各自的来源和数据库里）。
   - 碰到写外面、花钱、发给别人、加新推送的步骤，写成「先交收件箱卡」。

```bash
python3 $P skill --slug meal-swap --name meal-swap --agents diet \
  --title "给饮食记录加一个做法：换食材重算份量" \
  --why "这周你第 4 次让我换一样东西，重算这顿能吃多少" \
  --evidence "9/24|饮食记录|换成三文鱼能吃多少？" --evidence "9/24|饮食记录|我没有方便面了 但我有这两款新面条" \
  --file /tmp/meal-swap.md
```

5. **Agent**：照 agent-builder「先聊聊」里出方案的想法想清楚：它管什么、记哪些数据、看板放什么、和谁联动。看板写成 board 文件（写法见 board skill），交之前先 `python3 ~/.openmousse/repo/server/board_ctl.py check --plan 文件` 看一眼：

```bash
python3 $P agent --slug reading --name 读书 --purpose "记在读的书、读书笔记和进度" --icon book --color purple \
  --board-file /tmp/reading-board.json --title "新建 Agent「读书」" \
  --why "这周你 3 次让我记读到哪了" --evidence "9/25|主对话|读到第 120 页了" --change "看板：在读的书、这个月读完几本"
```

退出码：3 = 这件事提过了；4 = 今天的名额用完了；5 = 已经有同名的 skill。三种都别再试，也别换个说法再交。

## 交完之后

- 回一行：提了什么（按钮在「今天」页上）。
- 用户点**同意**：服务端自己装好（skill 写进 `skills/`、加进那几个 Agent 的允许列表；Agent 直接建好），你会收到一句「【收件箱】……已经做完了」，回一行就行，不用再 `done`。
- 点**不要**：记住了，永远别再提同一件事。
- 用户引用这张卡说要改：照他说的改好，用**同一个 slug** 再交一次，卡片会原地更新。
