---
name: project
description: 项目（有始有终的事：持续几天到几周、有目标和截止，比如小组作业、求职冲刺、搬家）。消息前面带「【项目空间】」项目卡时你在项目里：先看卡，聊的过程中随手更新（下一步、已定的、截止、进度）。主对话里用户说「开个项目」直接开；你发现一件事要做好几天就提议开；项目的事转进项目。收到「【自动触发】日结（项目）」「【自动触发】项目归档」或同意归档的收件箱消息时按这里做。命令 python3 ~/.openmousse/repo/server/project_ctl.py。
---

# 项目

> **有 OpenMousse 的 MCP 工具时（工具名里带 openmousse，比如 openmousse__board）就不用 shell**：下面每条命令对应一个工具——board_ctl.py → board、inbox_ctl.py → inbox、goals_ctl.py → goals、project_ctl.py → project、schedule_ctl.py → schedule、agent_ctl.py → agents、ask_agent.py → handoff、tasks_ctl.py → tasks、proposals_ctl.py → proposals、journal.py → journal、settings_ctl.py → settings、mousse-tree → tree。args 放命令里脚本名后面的词（一个词一项，JSON 整段一项，不用加引号），本来要从标准输入给的放 input；你是某个 Agent 时 agent 填你的 id（主对话不填）。没有这些工具就照原样在 shell 里跑命令。

项目 = app 侧栏「项目」里的一个对话 + 对话顶上一张**项目卡**：目标、截止、下一步、已定的、进度、在跑的任务，归档时加一份结论。
Agent 管一整块领域、一直在；项目有始有终，做完归档；任务是一次性的活，交了结果就完。

```bash
P="python3 $HOME/.openmousse/repo/server/project_ctl.py"
$P list                     # 有哪些项目、最近的截止
$P show <项目 id>            # 一张卡，每条带 id
```

## 在项目里（消息前面带「【项目空间】……项目卡」）

- 那张卡是你和用户共用的进度板。每天第一句话和卡改过以后服务器自动带给你，对话里不显示。**每天会话重置以后靠它接上**：先看卡，别问用户「我们上次说到哪」。
- 聊的过程中**随手更新，直接做、不用问**（回复下面会出一张小卡，用户能撤销）：
  - 定下来的事：`$P add <id> decision "视频 8 分钟以内，每人讲一段"`
  - 新的下一步 / 做完了：`$P add <id> step "周日前定分工"` / `$P done <id> <pi-…>`
  - 新截止：`$P add <id> deadline "交分工表" --due "2026-09-29 12:00"`（进日程和「要记得的」）
  - **已经在「要记得的」里的**（课程作业、邮件里的事、申请截止）用 `$P link <id> <那条的 id>` 挂上，别重复建；id 从 `python3 ~/.openmousse/repo/server/schedule_ctl.py remember` 整段复制。
  - 目标变了：`$P goal <id> "…"`
- 改完在回复里一句话说清改了什么。条目 id 从卡上的方括号或 `show` 里拿，不要自己拼。
- 项目里的重活照 `skills/dispatch/` 派，在项目里派，任务卡就挂在项目下。
- 只删用户让删的；用户加的东西你觉得该删，先问。

## 主对话里

- **用户说「开个项目」**：直接开，把能带的都带上，`--brief` 写这件事到现在聊过的要点（三五行，给项目里的你看）：

  ```bash
  $P create --title "小组作业" --goal "交出案例视频和书面报告" --deadline "组内彩排|2026-10-01 18:00" \
     --link "canvas:…" --decision "视频 8 分钟以内" --step "周日前定分工" --brief "今天分了工：……"
  ```

  开好说一句「开好了，以后这件事在项目里聊」。对话里会出一张「开了项目」的小卡。
- **你自己发现一件事要做好几天**（跨了两天还在聊、有两个以上截止、要做一周以上），提议开，别直接开：`$P propose …（参数同 create）--why "要做三周、有两个截止"`。进「等你点头」，用户点了服务器会开好、把 brief 转进去，你会收到一句「【收件箱】已同意…」，简短回一句就行。退出码 3 = 30 天内拒过，放下别再提。
- **已经有项目的事**：`$P ask <项目 id> "问题或要点"` 转进项目，等它答完把答案带回来，开头标「X 那边说：」。顺手的小改动（加个截止、打个勾）也可以在主对话里直接改，改完说一声。项目的活别在主对话里派。

## 日结（「【自动触发】日结（项目）」）

1. 进度一句话（做到哪、卡在哪）：`$P progress <id> "…"`
2. 下一步：做完的勾掉，新冒出来的加上；今天新定的事记进已定的。
3. 今天的要点追加到 `memory/projects/<项目 id>.md`（日期打头，三五行）。不要写当天的 `memory/YYYY-MM-DD.md`：主对话的日结同时在写，会互相覆盖。
4. 回一行「日结好了」。

## 归档（「【自动触发】项目归档」，或收件箱「已同意「归档「…」？」」）

1. 写结论（写完自动归档）：`$P conclude <id> --done "做成了什么" --decided "定过的事"（可以多条） --learned "下次记得的" --saved "存到了哪"`
2. `MEMORY.md` 加一条：`- 项目「X」（开始–结束）：做了什么、结论、状态`。
3. 值得长期记住的（用户的做事方式、偏好、结果）用 `skills/memory-tree/` 写进世界树。
4. 收件箱来的：`python3 ~/.openmousse/repo/server/inbox_ctl.py done <ib-…> --result "结论写好了"`。
5. 回一行「结论写好了」。

## 撤销

- 项目卡的改动：`$P undo <改动号>`。
- 截止的增删改和打勾是日程层的改动，输出里写的是「日程改动号」：`python3 ~/.openmousse/repo/server/schedule_ctl.py undo <日程改动号>`。
