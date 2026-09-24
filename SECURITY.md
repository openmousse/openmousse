# Security · 安全

**Please don't report a vulnerability in a public issue.** · **请不要在公开 issue 里报漏洞。**

## Report · 怎么报

- Preferred: this repository's **Security → Report a vulnerability** (private vulnerability reporting is on). Or email **security@openmousse.ai**.
- 首选：本仓库的 **Security → Report a vulnerability**（已开启私密漏洞报告）。也可以发邮件到 **security@openmousse.ai**。

Include what you found, how to reproduce it and what an attacker could do with it. You'll get a reply within a few days; fixes land on `main` (there are no versioned releases yet).
请写清楚发现了什么、怎么复现、攻击者能拿它做什么。几天内会回复；修复直接进 `main`（目前还没有版本号发布）。

## Scope · 范围

`server/`, `tree/`, `packs/`, `install.sh` and the app (`app/`). OpenClaw itself is a separate project: report its issues to [openclaw/openclaw](https://github.com/openclaw/openclaw).
`server/`、`tree/`、`packs/`、`install.sh` 和 app（`app/`）。OpenClaw 本身是另一个项目，它的问题请报给 [openclaw/openclaw](https://github.com/openclaw/openclaw)。

## Known limits by design · 设计上的已知边界

- One app token gives full control of your agent, including approving commands. Keep tokens on your own devices; if one leaks, `python3 server/tokens.py remove <name>` and add a new one.
- 一个 app 令牌等于对你的 agent 的全部控制，包括批准命令。令牌只放在自己的设备上；泄露了就 `python3 server/tokens.py remove <名字>` 再加一个新的。
- Your agent can run commands on the host unless you turn on OpenClaw's exec approvals. There is no sandbox yet.
- 除非打开 OpenClaw 的执行审批，你的 agent 能在主机上执行命令。目前还没有沙箱。
- Memories that AI platforms write to the memory tree are information, not instructions; agents are told so, but treat the tree as untrusted input.
- AI 平台写进世界树的内容是资料不是指令，agent 的规则里写了这一点，但请把树当成不可信的输入。
