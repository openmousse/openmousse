# Agent 会议

[English](meetings.md)

几个 Agent 围绕一个议题一起商量，主对话的 agent（下面叫「主持」，界面上显示你给助手起的名字）主持、写纪要；你确认纪要后，待办写进日程和各 Agent 的看板。会后可以保留成常驻的 Agent 群。代码在 [`server/meetings.py`](../server/meetings.py)，app 在 `app/src/screens/MeetingScreens.tsx`。

## 怎么发起

- **手动**：对话页「+」→ 发起会议：写议题，勾选参会的 Agent（2–8 个）。议题停下来约 1 秒，主持按议题推荐人选和会议名（不带工具的模型，见下）；你勾过以后不再覆盖。可以打开「会后保留为群聊」。
- **主对话自动发起**：你在主对话里说的事涉及好几个 Agent、需要它们互相配合（比如面试前一周的训练、饮食和睡眠），主对话按 handoff skill 发起会议（`ask_agent.py --meet "议题" <Agent id>…`），一句「此事涉及多个 Agent，已发起会议。」下面出一张会议卡（进行中）；一轮说完，主对话里再记一条「会议「…」已结束，以下 N 项待办需要你确认：」，下面是纪要卡（确认 / 查看会议）。

## 规则

- **每轮每个 Agent 最多发言一次。** 第 1 轮全体成员按顺序发言。之后的轮次：你在补充里 @ 了谁就请谁；没 @，主持决定请谁——你补充了一句就按这句挑（不带工具的模型，1–3 位），没补充（点「继续讨论」）就请上一轮纪要里 `next` 写的，都没有就全体。
- **@ 只转一层。** 发言里可以 @ 另一位成员一次（只认第一个）。被 @ 的这一轮还没说：提到下一个发言，发言标「回复 X」，它在这次回答里再 @ 别人不算。被 @ 的这一轮已经说过了：不再多给一次，问题留给纪要的「未定」和下一轮。
- **用量**：一轮最多 = 成员数次回复 + 1 次纪要（主持挑人的那一次是一个很短的不带工具的调用，不算一次回复）。发起页和会议资料页都写着。
- **会议里只讨论。** 发给成员的规矩写明：不改日程、看板和记录，不交收件箱卡，不派后台任务；要做的事写成建议，确认纪要后由服务器写。成员可以用自己的记忆和工具查数据。
- **纪要**：一轮说完，主持写纪要——决定、待办（每条有且只有一个负责的 Agent，有具体日期；有开始时间是日程，没有是截止）、未定的问题、下一轮该请谁。新一轮的纪要取代上一轮的。
- **确认**：每条待办按日期写进日程（`schedule_items`，`source` = 负责的 Agent，`key = meeting:<会议>:<轮>:<序号>:<日期>`，再点一次不会重复），再记进那个 Agent 看板上的「会议待办」清单（表 `meeting_todos`；看板上没有用它的积木就加一块 checklist，Agent 改的一版，看板顶上能撤回）。会议结束；开了「会后保留为群聊」的变成群聊。
- **群聊**：你说一句开一轮：@ 谁谁答，否则主持请人（同上）。不写纪要。资料页能解散（结束），结束的也能重新开成群聊。
- **结束 / 删除**：结束 = 归入「已结束的会议」，记录留着，没确认的纪要不写进日程。删除 = 删掉会议的消息、纪要和各成员在会里的会话（OpenClaw 会压缩存档一份），主对话里那几条纪要通知一起删；已确认写进日程和看板的待办留着。
- **不推送。** 纪要出来只记未读（会议线程和主对话各一条），不新加推送。

## 每个成员怎么参会

- 会议是一个线程 `grp-<8 位十六进制>`。每个成员在会里有自己的 OpenClaw 会话 `agent:<id>:grava:grp-<id>`（还借住在 main 的老 Agent：`agent:main:grava:grp-<id>-<id>`；`grava` 是会话键里的内部值，老会话靠它找回来），走 Gateway 的 OpenAI 兼容接口、指明它自己的 agent（`x-openclaw-agent-id`），所以用的是它自己的记忆、工具和它对话里选的模型。
- 轮到它时，发给它的是它还没看过的会议发言，每条标明谁说的（`【饮食记录】……`、`【你的称呼】……`、`【助手名 · 第 1 轮纪要】……`），不含它自己说过的（会话里有）。第一次发言带上议题、谁在场、它是谁、规矩；OpenClaw 每天 04:00 重置会话，隔了天的第一次重新带一遍（最多最近 30 条）。别的 claw（Hermes、nanobot…）不记会话：每次都带全。
- 被 @ 的那位另外看到「「X」在发言里 @ 了你：「…」先回答它；这次发言不要再 @ 别人。」
- 一位最多说 5 分钟，到点算没说完，接着请下一位。

## 不带工具的模型

推荐人选、挑人、写纪要都经 [`server/llmjson.py`](../server/llmjson.py)：OpenClaw 开了 `llm-task` 插件就走它（零工具、每次新会话、按 schema 校验最外层）；没开就退回一次性的临时会话。三处的输入都是你自己的话和你自己的 Agent 的发言，不含朋友的原话。

## 主对话知道会上定了什么

主对话发起的会议有了纪要、确认或结束，主对话下一次说话时，这些结果（纪要正文 + 已确认 / 待确认 / 没确认就结束）带在前情里，只带一次（`meetings.news` / `told`）。

## 赛博、「定了告诉我」、会议放在哪（2026-10-21）

- **赛博可以参会**（`cybers` 开着时）：赛博走赛博回合（[`engine/cyber.py`](../server/engine/cyber.py) 的 `speak`：从不花你的 Claude Code、ChatGPT 套餐；带它的装载工具 `ask_agent`；会话 `meeting:<会议>:<赛博>`）。它只出看法、不领待办：纪要里的待办只给功能 Agent，一场会至少要有一个功能 Agent。健康、饮食、睡眠的功能 Agent 不和赛博同会（赛博用别家的模型，读得到会上的每一句）。
- **定了告诉我**（发起时 `auto: true`；只给你在 app 里发起的会，还要开着下面的 `by_origin`，不然不理 `auto`）：全自动跑完。一轮的纪要写好以后，还有没定的、纪要里说了该请谁，就自己再开一轮（一共最多 2 轮）；然后自动确认（待办写进日程和看板），会议结束。主对话里一行「会议「…」已开完，N 项待办已写进日程和看板：」，下面挂纪要卡（已确认的回执）。
- **会议放在哪看谁发起**（`server.json` 的 `"meetings": {"by_origin": true}`；默认关，已经在用的安装照旧，上线时再问）：你自己发起、没选「定了告诉我」的留在对话列表（「会后保留为群聊」默认开）；助手发起的和全自动的开完就收走（`listed: false`）：进行中时主对话那一行写「会议中 · …」，主对话留纪要卡。单不单列是建会议那一刻定的，老的会议照旧显示。
- **会议记录**（主对话资料页 →「会议记录」）：全部会议（你发起的也存一份），新的在前，按谁发起筛，议题、纪要、发言都能搜。

## 数据

- `meetings`：`id, title, topic, host（main）, members（JSON，发言顺序）, status（running 进行中 / pending 待确认 / ended 已结束 / group 群聊）, keep, round, origin（manual / main）, from_thread, message_id（会议卡挂在哪条回复下面）, minutes（最新一轮的纪要 JSON）, confirmed（确认的结果 JSON）, news, told, created_at, updated_at, ended_at`
- `meeting_seen`：`(meeting, agent) → last_id, day`：每个成员看到第几条、哪天（隔天要重新带规矩）。
- `messages` 多两列：`speaker`（Agent id；主持的系统行和纪要是 `main`；你说的是空）、`meta`（JSON：`round`、`replyTo`、`at`、`deferred`、`kind: minutes | system | meeting`、`event`、`agents`、`minutes`）。role：你 = `user`；成员发言和纪要 = `grava`（助手这一边在库里的内部值），`origin = meeting`（算未读，不算角标的「给你的回复」）；系统行 = `auto`。
- 服务重启时正在进行的一轮会中断：会议读到的时候改成待确认，记一行「服务重启，本轮未完成」，可以继续讨论。进行中的一轮算进 `/api/chat/busy`，`safe_restart` 会等它说完。

## 接口

| 接口 | 做什么 |
|---|---|
| `GET /api/meetings?status=active\|ended\|all` | `{meetings, endedCount}`：active = 进行中、待确认、群聊（对话列表的会议行），新的在前；每行带 `last`（最后一条：`name`、`text`、`time`、`kind`）和 `needsYou`（待确认） |
| `POST /api/meetings` | `{topic, members, title?, keep?, auto?, origin?}` → 发起并开第 1 轮；成员要 2–8 个已有的 Agent（可以有赛博，至少一个功能 Agent）。`keep` 不给 = 默认（false；`meetings.by_origin` 开着时按谁发起）；`auto` = 定了告诉我（只在 `origin: manual`、`meetings.by_origin` 开着时算）。`origin: main` 时会议卡当场经 SSE 发给正在回复的主对话 |
| `GET /api/meetings/options` | 发起会议那一页：能请谁（`members`，功能 Agent 在前、赛博在后；`health: true` = 不和赛博同会）、「会后保留为群聊」的默认值（`keep`，跟着 `meetings.by_origin`）、`byOrigin`（`meetings.by_origin` 开没开：开着 app 才画「定了告诉我」）、`records`（主对话资料页画不画「会议记录」：开关开着，或者有开着时收走的会）、`min` / `max` |
| `GET /api/meetings/records?filter=all\|main\|auto\|mine&q=` | `{records, intro}`：全部会议，新的在前；每条有 `initiator`（「… 发起」/「你发起 · 定了告诉我」）、`outcome`（「纪要已确认」/「已进日程」/……）、`line`、`day`，搜的时候多一段 `match` |
| `POST /api/meetings/suggest` | `{topic}` → `{agents, title}`：按议题推荐（模型用不了时 `agents` 为空） |
| `GET /api/meetings/{id}` | `{meeting, messages}`：全部消息（旧的在前，最多 500 条），`meeting.live` = 正在进行的一轮（`round, phase, speaker, replyTo, text, queue`） |
| `GET /api/meetings/{id}/stream` | 接上正在进行的一轮（SSE；没有就 204），按发言人分段：`state`（现状）→ `round` / `speaker` / `delta` / `said` / `system` / `message` / `phase`（minutes = 在写纪要）/ `minutes` / `status` → `done` |
| `POST /api/meetings/{id}/send` | `{text}`：补充一句。待确认、群聊：开下一轮；进行中：只记下，还没发言的成员会看到；已结束：409 |
| `POST /api/meetings/{id}/continue` | 继续讨论（不补充） |
| `POST /api/meetings/{id}/confirm` | 确认这一轮的纪要 → `{meeting, confirmed: {todos, schedule, board, failed}}` |
| `POST /api/meetings/{id}/stop` | 停掉正在进行的一轮（在说的那位标「已停止」，后面的不再请，不写纪要） |
| `POST /api/meetings/{id}/end` | 结束（群聊是解散） |
| `PATCH /api/meetings/{id}` | `{keep?, title?}`：已结束的打开 keep = 重新开成群聊；群聊关掉 = 结束 |
| `DELETE /api/meetings/{id}` | 删记录（已确认的待办留着） |
| `GET /api/chat/cards?thread=` | 多两种卡：`{kind: "meeting", meeting, messageId, title, members, status, statusLabel, round}`（挂在发起它的那条回复下面）、`{kind: "minutes", meeting, messageId, round, status: pending / confirmed / superseded / ended, decisions, todos, open, confirmed}`（挂在纪要通知下面） |

`/api/unread` 的 `threads` 里有没结束的会议（`grp-…`）。`/api/chat/send` 不收会议线程（400）。服务器的 `/api/health` → `server.features` 有 `meetings`，app 用 `supports('meetings')` 决定显不显示入口。

```bash
python3 packs/core/scripts/ask_agent.py --meet "下周三模拟面试前的训练、饮食与睡眠安排" apply fitness diet health --title 面试前一周
```
