---
name: board
description: 你自己的数据表和看板（app 里你那一页的「看板」tab）。用户想长期记、想一眼看到的东西（库存、花费、个人最好成绩、读了几页……）记进你自己的表，用积木摆到看板上：数字、进度、趋势、列表、清单、文字、按钮七种。用户在对话里让你加的，直接加（看板顶上会出「撤回」）；你自己想到的，交提案等他点头（收件箱卡里带预览）。收到用户从看板按钮发来的消息（比如「这是今天的小票，记进库存」带一张照片）也按这里做。现成的一整套（表 + 积木 + 用法，比如「家里的库存」）是功能包，pack install 装上；到点查表推一条的提醒（比如周六提醒补货）也在这里提，用户点了同意才开。命令：python3 ~/.openmousse/repo/server/board_ctl.py。
---

# 看板：你自己的表 + 积木

> **有 OpenMousse 的 MCP 工具时（工具名里带 openmousse，比如 openmousse__board）就不用 shell**：下面每条命令对应一个工具——board_ctl.py → board、inbox_ctl.py → inbox、goals_ctl.py → goals、project_ctl.py → project、schedule_ctl.py → schedule、agent_ctl.py → agents、ask_agent.py → handoff、tasks_ctl.py → tasks、proposals_ctl.py → proposals、journal.py → journal、settings_ctl.py → settings、mousse-tree → tree。args 放命令里脚本名后面的词（一个词一项，JSON 整段一项，不用加引号），本来要从标准输入给的放 input；你是某个 Agent 时 agent 填你的 id（主对话不填）。没有这些工具就照原样在 shell 里跑命令。

app 按你写的配置画看板，不需要改代码。你管三样：**表**（记什么，字段你定）、**行**（数据）、**看板配置**（用哪几块积木、放哪、数据从哪张表怎么算）。

```bash
B="python3 $HOME/.openmousse/repo/server/board_ctl.py"   # 在自己的工作区里跑，不用写 --agent
$B show                      # 先看：现在的看板每块显示什么、有哪些表、能插的位置
$B get > /tmp/board.json     # 现在的配置；改完整份交回去（apply / propose 都是整份替换）
$B check --file /tmp/board.json
```

## 直接加，还是交提案

- **用户在对话里让你加的**（「以后拍小票给你，帮我记库存」「看板上加个体重趋势」）：建表、写好配置、`check`、`apply --note "一句话说加了什么"`，回复里说加了哪几块。看板顶上会出「撤回」，不用再问。
- **你自己想到的**：`propose`，why 写证据（「这个月你问了 3 次最好成绩：9/12、9/19、9/26」），一天最多一两条。要新表就先 `table add … --draft` 建草稿表、补几行真数据，卡片里的预览才有内容；用户点「加上」草稿表自动转正，点「不要」自动归档，30 天内别再提同一件事。
- 永远不替用户点同意。积木只显示和让用户自己点；**不会推送、不写外面的系统**。想加提醒（比如补货）是另一件事：写一条提醒规则 `alert propose`（见下面「提醒」），用户同意才开。

## 表和数据

```bash
$B table add pantry --title 家里的食物 --field name:text:名字 --field qty:number:数量 --field unit:text:单位 \
   --field expires:date:到期 --field price:money:价格:GBP --field category:choice:类别:肉蛋,奶,蔬果,主食,其他
$B rows add pantry --json '[{"name": "鸡胸肉", "qty": 2, "unit": "盒", "expires": "2026-09-28", "price": 7, "category": "肉蛋"}]'
$B rows query pantry --where 'qty>0' --where 'expires<=+3d' --sort expires
$B rows update r-1a2b3c --inc qty=-1          # 或 --json '{"expires": "2026-10-01"}'
$B rows delete r-1a2b3c                        # 30 天内 rows restore 能找回
```

- 字段类型：text / number（可带单位）/ money（货币，默认 GBP）/ date（YYYY-MM-DD）/ datetime / bool / choice（给选项）/ photo / link。表名、字段名用小写英文加下划线，标签写用户的语言。
- 用户在 app 里也会直接改、删、打勾、点「用掉 1 份」：读数据以表为准，别拿你记得的覆盖。
- 一个 Agent 最多 20 张表、每张 5000 行。不存密码、证件号这类东西。

## 积木（配置是 `{"blocks": [ … ]}`）

每块都有 `id`（稳定，改配置时别换）、`type`、`title`（短，8 个字以内）、`after`（放在哪：内置看板的小节或另一块的 id，不写放最后）。查询 Q 见下一节。

```json
{"id": "spend", "type": "stat", "title": "买菜花费", "items": [
  {"label": "这周", "value": {"from": "pantry", "agg": "sum", "field": "price", "where": [["bought", ">=", "week"]]},
   "compare": {"from": "pantry", "agg": "sum", "field": "price", "where": [["bought", ">=", "lastweek"], ["bought", "<", "week"]]},
   "compareLabel": "比上周", "good": "down"},
  {"label": "每镑蛋白质", "value": {"ratio": [{"from": "pantry", "agg": "sum", "field": "protein_g"}, {"from": "pantry", "agg": "sum", "field": "price"}]}, "unit": "g"}]}
{"id": "budget", "type": "progress", "title": "本月预算", "value": {"from": "spend", "agg": "sum", "field": "amount", "where": [["date", ">=", "month"]]}, "target": 900}
{"id": "trend", "type": "chart", "title": "每周买菜", "series": {"from": "pantry", "agg": "sum", "field": "price", "by": "week", "date": "bought", "range": "8w"}}
{"id": "expiring", "type": "list", "title": "快过期", "after": "diet.next",
 "source": {"from": "pantry", "where": [["qty", ">", 0], ["expires", "<=", "+3d"]], "sort": ["expires"]},
 "row": {"title": "{name} · {qty} {unit}", "sub": ["store"], "right": "expires",
         "badge": [{"field": "expires", "op": "<=", "value": "today", "text": "今天到期", "tone": "bad"}]},
 "rowActions": [{"label": "用掉 1 份", "set": {"qty": "-1"}}], "edit": true, "empty": "这几天没有快过期的"}
{"id": "tobuy", "type": "checklist", "title": "要买", "source": {"from": "shopping", "sort": ["bought", "-_created"]}, "check": "bought", "row": {"title": "item"}}
{"id": "note", "type": "text", "title": "这周小结", "text": "蛋白质最划算的是鸡胸，每镑 31 g。"}
{"id": "receipt", "type": "action", "actions": [
  {"kind": "upload", "label": "拍小票", "message": "这是今天的小票，记进库存", "primary": true},
  {"kind": "form", "label": "记一样", "collection": "pantry"},
  {"kind": "ask", "label": "出采购单", "message": "按库存出这周的采购单"}]}
```

- `list`：`row.title` / `sub` / `right` 写字段名，或「{字段} · {字段}」模板；`badge` 是规则，第一条对上的显示（tone：good / warn / bad / neutral）；`limit` 默认显示几行、其余折起来；`group` 按某个字段分组；`style: "chips"` 显示成一排小标签；`rowActions` 的 `set` 里 `"-1"` / `"+1"` 是加减，`"today"` / `"now"` 是今天 / 现在。
- `checklist`：`check` 是 bool 字段，用户点一下就写；勾掉的淡下去、折成「已完成 N 项」，不用自己筛。
- `text` 也可以取某张表最新一行的某个字段：`"source": {"from": "notes", "sort": ["-_created"]}, "field": "body"`（每周小结这种，写一行新的就换）。
- `action`：`upload` 让用户拍照 / 选相册 / 选文件，连同 message 发给你；`form` 按表的字段出小表单，直接写一行；`ask` 发一句话给你。
- 看板排序按用户想知道的顺序：现在该做什么 → 今天 → 这周 → 长期。最多 30 块。不认识的 type 在旧版 app 上显示「要新版 app」，别发明新的 type。

## 查询 Q

- 行：`{"from": 表, "where": [[字段, 运算, 值], …], "sort": ["字段", "-字段"], "limit": N}`。运算：= != > >= < <= in not_in contains empty not_empty。
- 一个数：加 `"agg": "sum|avg|count|min|max|last"` 和 `"field"`（count 不用 field；last 按 `date` 字段取最新一行）。两个数相除：`{"ratio": [Q1, Q2]}`。
- 趋势：加 `"by": "day|week|month"`、`"date": 日期字段`、`"range": "14d|8w|6m"`。
- 日期的值：today / tomorrow / yesterday / week（本周一）/ lastweek / month（本月 1 号）/ lastmonth / +3d / -30d / +2w / 2026-09-30。系统字段 `_created` / `_updated` 也能用。
- 和同一行的另一个字段比：值写 `{"field": "min_qty", "default": 0}`（`default` 是那个字段空着时用的数），比如「数量 ≤ 该补的线」：`["qty", "<=", {"field": "min_qty", "default": 0}]`。badge 的 value 也能这么写。
- 只读的系统来源（不用建表，不能改）：`health:daily`（每晚：sleep_min / deep_min / rem_min / core_min / awake_min / hrv_ms / rhr_bpm / resp_rate / wrist_temp_c，按 date）和 `health:<Apple 健康指标>`（按天汇总：date / sum / avg / min / max / count，比如 `health:StepCount` 的 sum 是当天步数）。例：`{"from": "health:daily", "agg": "avg", "field": "hrv_ms", "by": "day", "date": "date", "range": "14d"}`。

## 能插的位置（after）

`top`（最前面）；内置看板的小节：健身 `fitness.now` `fitness.body` `fitness.week` `fitness.long`，饮食 `diet.today` `diet.next` `diet.eaten` `diet.week` `diet.shopping`，健康 `health.sleep` `health.recovery`，求职 `apply.list`，申请学校 `masters.list`。没有内置看板的 Agent 整页都是积木，按数组顺序排。`show` 会列出你能用的。

**内置小节本身也能挪、能藏**（用户在 app 里长按一节）。挂在某一节后面的积木跟着那一节走。`get` 出来的配置里有 `sections`（小节的顺序和藏没藏，`{"id": "diet.week", "hidden": true}`）：原样交回去就不变；配置里不写 `sections` 也不变。只有用户让你挪、藏小节时才改它，小节不能删、内容也改不了（是 app 画的）。

## 看板按钮发来的照片

「这是今天的小票，记进库存」带一张图：看图读出每样东西、数量、价格、保质期（小票上没有就按常识估，标清楚是估的），`rows add` 一次写完，回复一句「记好了：11 样，£23.40」加几条要紧的（快过期的）。认不出的列出来问用户，别瞎猜。那张表是功能包装的（`show` 最后一行写着装了哪些包），先 `pack guide <包名>` 照包里的用法做。

## 功能包

一整套现成的表 + 积木 + 用法（比如 `pantry`「家里的库存」：拍小票记库存、快过期、该补货了、买菜花费）。

```bash
$B pack list                         # 有哪些、装在谁身上
$B pack show pantry                  # 装了会有哪些表、哪几块、带不带提醒
$B pack install pantry --check       # 装在你身上会改什么（已有同名的表只补字段，同 id 的积木不重复加），不动东西
$B pack install pantry               # 用户明确让装的：直接装，看板顶上有撤回
$B pack install pantry --propose --why "你这周拍了 3 次小票，都是我手记的"   # 你自己想到的：交卡，预览是装好以后的看板
$B pack guide pantry                 # 用法：装好以后、每次动这张表之前看
$B pack remove pantry                # 看板上拿掉这个包的积木（表和数据留着）
```

- 包里的提醒不会跟着装上：装好以后每条另外出一张卡，用户点了同意才开。
- 装包不改你的 skills 和配置，用法就是 `pack guide` 的内容。

## 提醒（到点查表，有东西就推一条）

新推送要用户点头：**只能 propose**，他在卡片上看得到几点推、推出来长什么样，同意了才开。一个 Agent 最多 10 条。

```json
{"id": "restock", "title": "补货提醒", "source": {"from": "pantry", "where": [["staple", "=", true], ["qty", "<=", {"field": "min_qty", "default": 0}]]},
 "row": "{name}", "message": "家里快没了：{items}", "at": "10:00", "days": ["sat"], "level": "quiet"}
```

- `source` 和列表的查询一样；到点查出来是空的就不推。`row` 是每一行写成什么；`message` 里 `{items}` 是前几行（`limit`，默认 6）、`{count}` 是一共几行。
- `at` 几点（HH:MM）；`days` 写 mon…sun，不写 = 每天；`level` 默认 quiet（静默，进通知中心），真要响铃才写 ring，why 里说为什么要响。
- `$B alert check --file 提醒.json` 看按现在的数据会推什么 → `$B alert propose --file 提醒.json --why "证据"`。同一个 id 再提会换掉原来那条。
- 用户说别推了：`$B alert list` 找到它，`alert pause <id>`（能恢复）或 `alert delete <id>`。被拒过的同一个提醒 30 天内别再提。

## 出错了

`check` / `apply` 报错会说哪块哪个字段不对，照着改。看板改坏了：`history` 看版本，`revert <版本>` 回去（数据不动）。
