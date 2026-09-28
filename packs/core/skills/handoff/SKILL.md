---
name: handoff
description: 主对话把属于某个 Agent 的问题转给它。当用户问的事明显归某个 Agent 管（`--list` 看现在有哪些、各管什么）——要建议、要计划、要记录，或者你需要某个 Agent 的专业判断来回答一个跨块问题时使用。
---

# 转给 Agent

> **有 OpenMousse 的 MCP 工具时（工具名里带 openmousse，比如 openmousse__board）就不用 shell**：下面每条命令对应一个工具——board_ctl.py → board、inbox_ctl.py → inbox、goals_ctl.py → goals、project_ctl.py → project、schedule_ctl.py → schedule、agent_ctl.py → agents、ask_agent.py → handoff、tasks_ctl.py → tasks、proposals_ctl.py → proposals、journal.py → journal、settings_ctl.py → settings、mousse-tree → tree。args 放命令里脚本名后面的词（一个词一项，JSON 整段一项，不用加引号），本来要从标准输入给的放 input；你是某个 Agent 时 agent 填你的 id（主对话不填）。没有这些工具就照原样在 shell 里跑命令。

每个 Agent 各管一块，有自己的记忆、skills 和看板（`--list` 看现在有哪些，包括用户在 app 里或对话里新建的）。属于它们的事**转给它们做**，不要自己直接答：那样记录会落在你这边，Agent 的记忆里没有，明天它就不知道。

```bash
A=~/.openmousse/repo/packs/core/scripts/ask_agent.py
python3 $A --list                       # 谁管什么
python3 $A <agent id> "用户的原话 + 必要的上下文"
```

## 怎么转

1. **原话转**，把用户的问题和必要的上下文一起给（比如他刚说的"我 7 点有课"），一句话说清，不要自己先答一半。
2. 等答案（一般 20–70 秒；超时会告诉你它还在答）。
3. **把答案带回给用户**：直接给它的回答，开头一句「XX Agent 说：」标明来源；它写的卡在对应看板和「今天」页。不要重新润色成你的话，也不要再加一段自己的意见，除非用户问的是跨块的事需要你综合。
   app 的对话里会自动出一张转交卡（问的时候是「正在问 XX」，答完是「转给了 XX · 多少秒」，点一下到那个 Agent 的对话），你不用另外说转给了谁；「XX 说：」照写，Telegram 这类渠道里没有卡。
4. 它要用户确认的事，原样转达，用户答了再转回去。
5. 转不过去（409 忙 / 超时）：告诉用户那个 Agent 正忙，可以直接去它的页面看，或者稍后再问。

## 不转的

- 闲聊、系统问题、跨块的综合判断、任务派发、记忆和档案：你自己答。
- 只是查一个数（"我昨晚睡了多久"）且你自己有工具能查：直接查；**要建议、要计划、要记录**才转。
