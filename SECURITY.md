# Security · 安全

**Please don't report a vulnerability in a public issue.** · **请不要在公开 issue 里报漏洞。**

## Report · 怎么报

- Preferred: this repository's **Security → Report a vulnerability** (private vulnerability reporting is on). Or email **security@openmousse.ai**.
- 首选：本仓库的 **Security → Report a vulnerability**（已开启私密漏洞报告）。也可以发邮件到 **security@openmousse.ai**。

Include what you found, how to reproduce it and what an attacker could do with it. You'll get a reply within a few days; fixes land on `main` and ship in the next release (releases are tagged `v<date>` and listed in [CHANGELOG.md](CHANGELOG.md); `mousse update` follows the latest one).
请写清楚发现了什么、怎么复现、攻击者能拿它做什么。几天内会回复；修复进 `main`，随下一个版本发出（版本打 `v<日期>` 的 tag，写在 [CHANGELOG.zh-CN.md](CHANGELOG.zh-CN.md)；`mousse update` 跟着最新的那个）。

## Scope · 范围

`server/`, `tree/`, `packs/`, `install.sh` and the app (`app/`). OpenClaw itself is a separate project: report its issues to [openclaw/openclaw](https://github.com/openclaw/openclaw).
`server/`、`tree/`、`packs/`、`install.sh` 和 app（`app/`）。OpenClaw 本身是另一个项目，它的问题请报给 [openclaw/openclaw](https://github.com/openclaw/openclaw)。

## Known limits by design · 设计上的已知边界

- One app token gives full control of your agent, including approving commands. Keep tokens on your own devices; if one leaks, `python3 server/tokens.py remove <name>` and add a new one.
- 一个 app 令牌等于对你的 agent 的全部控制，包括批准命令。令牌只放在自己的设备上；泄露了就 `python3 server/tokens.py remove <名字>` 再加一个新的。
- On OpenClaw, your agent can run commands on the host unless you turn on OpenClaw's exec approvals. On the built-in engine, host commands are off until you list the allowed ones for an Agent (`engine.host_exec` in `server.json`); commands outside the list, and dangerous ones even on it, wait in the inbox for your OK. A Docker sandbox for commands and the browser (`python3 server/errand.py setup`) is optional; without it there is no sandbox.
- 用 OpenClaw 时，除非打开 OpenClaw 的执行审批，你的 agent 能在主机上执行命令。用自带的内核时，主机上的命令默认关着，要在 `server.json` 里给某个 Agent 列出放行的命令（`engine.host_exec`）；名单外的、以及在名单里但危险的，都在收件箱里等你点头。跑命令和浏览器的 Docker 沙箱（`python3 server/errand.py setup`）是可选的；没装就没有沙箱。
- Memories that AI platforms write to the memory tree are information, not instructions; agents are told so, but treat the tree as untrusted input.
- AI 平台写进世界树的内容是资料不是指令，agent 的规则里写了这一点，但请把树当成不可信的输入。
