---
name: journal
description: 用户的日志：他随口分享的身体感受（腿沉、肩疼、状态好）、对一件事的想法或观点、做的决定、想记下来的事。当用户说"记一下"、"我觉得…"、"今天练完感觉…"、"我决定…"时，把它记进日志；当他问"我上次怎么说的"、"最近肩怎么样"、"我对 X 的看法"时查日志。
---

# 日志

用户说的感受、想法、决定不能只留在对话里，要记进结构化日志，app「我 → 日志」和各 Agent 的「记忆」页能翻。

```bash
J=~/.openmousse/repo/packs/core/scripts/journal.py
python3 $J add --kind feeling --group <你的 agent id> --text "..." --tags 深蹲,腿 --context "Leg A 练后"
python3 $J add --kind thought --text "..." --tags 申请          # 不属于某个 Agent 就不给 --group
python3 $J add --kind decision --text "..."
python3 $J list --group <agent id> --days 14                     # 查最近的
python3 $J search --q 肩 --days 90
```

## 什么时候记

- **身体 / 训练感受**（`feeling`）：练前状态、练中某个动作的感觉、练后整体感受、睡得不好、精神差、心情。`--context` 写当时在做什么。
- **想法、观点**（`thought`）：他对某件事的看法、评价、判断。`--tags` 写话题。
- **决定**（`decision`）：他说"就这么定了"、"我决定…"、"以后…"。
- **其它**（`note`）：他明确说"记一下"的东西。
- 一条消息里有几件事就记几条，每条一句话，用他自己的说法，不要润色成报告腔。

## 怎么回

- 记完只回一句确认，比如"记下了：深蹲第三组腿发抖"，不复述整段。
- 不要因为记日志打断他正在说的话题；先答他的问题，日志顺手记。
- 他问"最近怎么样"之类的时候，先 `list` 再总结，引用日期。
- 删除：他说"把那条删了"，找到 id 后 `delete`，回一句"删了"。

## 边界

- 日志是他的私事，只在一对一会话里用，群聊里不引用。
- 你是某个 Agent 就带 `--group <自己的 id>`；主对话不确定归谁就不带。
