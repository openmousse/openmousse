---
name: memory-tree
description: 世界树：用户跨平台共享的个人记忆。Claude.ai / ChatGPT / Gemini 和这里的各 Agent 都读写同一棵树。学到关于用户的新事实、偏好、决定、近况时写进去；回答涉及他的偏好或近况时先查。
---

# 世界树（memory-tree）

用户在任何 AI 平台说过的关于他自己的事，都汇进同一棵树。你读到的 `shared/tree/TREE.md`（memory_search 能搜到）就是树的导出；写入用下面的命令。

## 什么时候写

- 用户说出关于自己的**新事实、偏好、决定、近况**，且这件事在别的平台或别的 Agent 也用得上。
- 一条一句话、第三人称、能独立理解："早餐改成燕麦加鸡蛋"要写成"<名字>早餐改成燕麦加鸡蛋"，不是"用户说早餐改了"。
- 不写：临时闲聊、你的推测、本 Agent 自己的业务数据（那些进你自己的记忆和服务数据库）。

```bash
T=~/.openmousse/venv/bin/mousse-tree
$T add --source openclaw-<你的 agent id> --kind preference --tags 饮食 --text "…早餐改成燕麦加鸡蛋"
```

`--kind`：fact 事实 / preference 偏好 / decision 决定 / event 近况。`--observed YYYY-MM-DD` 是事情发生的日期，默认今天。取代旧记忆时加 `--supersedes <旧 id>`。

## 什么时候读

- 先 `memory_search` 搜 TREE.md 就够；要精确找 id 或看最近几天：

```bash
$T recall --q "早餐"
$T recent --days 7
```

## 忘记

用户明确要求忘记时：`$T forget <id>`。档案（USER.md）里的条目不在这里删，告诉用户去改档案。
