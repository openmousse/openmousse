"""运维和命令行（第 7 批，2026-10-19）：`mousse` 的运维子命令，每个命令一个模块，身子都在这里，../mousse.py 只挂上参数。
对照 OpenClaw（docs/cli/doctor、backup、update、config、security、sessions、logs、status、uninstall）和 Hermes（hermes_cli 的 doctor、
backup、update、config、logs、dump、uninstall）补的基础功能；只借设计，照搬的代码在文件头写出处。

分工：
  cli.py        把下面这些命令挂到 mousse 的参数上（mousse.py 里只多一行 ops_cli.add(sub) 和一处分派）
  common.py     共用的小件：路径（~/.openmousse、仓库软链、venv、日志）、服务管理（systemd / launchd / 自己起的）、/api/health、
                打码（engine/errors.py 的 scrub，不经 engine 包导入：venv 坏了也能用）、权限、SQLite 小工具、server.json 的备份和原子写
  doctor.py     mousse doctor：体检（每一项 ✓ / ! / ✗、一句话、怎么修；--json 照 OpenClaw 的 health contract），--fix 只修撤得回的
  status.py     mousse status：一屏摘要（服务、版本、claw、主对话的模型、今天几轮花多少、定时任务、收件箱、有没有新版本）
  logs.py       mousse logs：Linux 读 journalctl --user -u openmousse-server，macOS / 自己起的读 ~/.openmousse/logs/，都过一遍打码
  backup.py     mousse backup create / verify / list、mousse restore：一个 tar.gz + manifest（每个文件的大小和 sha256），恢复前先校验、先快照
  update.py     mousse update：stable（最新的 v<VERSION> tag）/ main 两个通道，先快照，起不来就退回去
  confcheck.py  mousse config validate / get / set / unset：已知键的类型表，整份校验；改之前备份、原子写；密钥打码、不让 set
  audit.py      mousse security audit：权限、写在配置里的密钥、令牌、监听地址、Funnel、关卡和沙箱的设置，--fix 只做安全的
  sessions.py   mousse sessions cleanup：清旧的内核会话记录（在用的不清），再 VACUUM
  diagnostics.py  mousse diagnostics：给人看问题的 zip（体检、摘要、配置的样子、库的行数、日志里的信号），不带对话、密钥、令牌、邮箱
  uninstall.py  mousse uninstall：默认只拿掉服务、crontab 那一行、软链；--full 先备份、再删 ~/.openmousse、世界树、安装器放的干净的仓库

服务器没开着也能跑；改 server.json 之前先备份（同 engine/setup_api.py 的 server.json.bak-<时间>）；不读、不存、不传任何订阅令牌
（Claude Code / Codex 登没登录照 engine/runners.py：起它们自己的命令查）。
"""
