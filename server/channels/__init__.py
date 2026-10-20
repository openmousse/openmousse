"""渠道层（第 6 批，2026-10-19；对照表 10.12–10.17、8.5–8.7、3.21、9.6）：主人在 Telegram、Discord、邮件里跟自己的 Agent 说话；
别人经这些渠道把话送进来，由名片 Agent 按档位答，要主人表态的出卡。claw 选 builtin 时才起（OpenClaw 有它自己的渠道，那条路不变）；
server.json 不写 channels = 什么都不起，每个渠道还要 "enabled": true；对已经在用的安装默认关。

谁走什么回合（最要紧，见 router.py、people.py）：
  主人     Telegram：认主人的码记下的账号 id（或配置的 owners）；邮件：发件人在 owners 里、而且 DMARC 过了（或 DKIM 对齐过了）
           → 主人回合，在绑定的那个 Agent 的对话里，和在 app 里说的一样（chat.send：关卡、记录照旧；origin user，套餐照 may_spend）
  联系人   配对批了的、名单上的（有档位、绑哪个 Agent）→ 名片 Agent（cardagent.answer：零工具、按档位只看放出来的、Doorman 复查、
           要主人表态的出 kind social 的卡、每人每天有上限；外人回合，花不了套餐）
  陌生人   私聊默认配对（回一个码、不调模型；主人批了才成联系人），也能配成只认名单、或者不理；邮件的陌生人默认不理、不发配对码
  群       默认关；开了的群里要 @ 机器人（或回复它、或叫 Agent 的名字）才答；群里别人的话只当资料，进不了主人回合的指令
  外来的元数据（显示名、群名、转发自谁、名片、位置说明、语音转写）都标不可信，放进单独的一块，不拼进正文
  聊天里任何一句话都不能把人升成主人：认主人只能从主人自己的设备上发起（认主人的码）

分工：
  base.py      进来的一条（Inbound）、一个账号（Account）、渠道的接口（Channel）、出错（ChannelError）
  accounts.py  server.json 的 channels 段：账号、开关、绑定（越具体越优先）、配置的毛病
  store.py     表（mousse.db）：进来的队列（先落库再确认、去重）、出去的队列（重试、死信）、人、配对、认主人的码、群历史、发过的消息
  hub.py       跑起来的样子：每个账号收、按聊天串行处理、发；起停、重起
  people.py    认人：主人（认主人的码）、联系人、配对
  router.py    一条进来以后怎么办（防回环、认人、群里要 @、每人上限、/start /stop、路由）
  owner.py     主人回合：走 app 那条路，回复回到渠道、正在输入、ask_user、边写边改（可选）、重启后补发
  cards.py     卡发到主人的渠道、在聊天里点（按钮）、回复卡 = 改一下、配对的卡
  outbox.py    出去：分段、排队、限频、429 照 retry_after、退避、死信；cardagent.DELIVER（主人点过的卡的回话送回去）
  format.py    分段（不切断代码块）、Markdown → Telegram 的 HTML / 纯文字
  tool.py      Agent 的 message 工具（发给主人直接发，发给别人交卡）、服务器内部的 send_to_owner
  api.py       /api/setup/channels…（只认主人的设备）；命令行 mousse channels 在 ../channels_ctl.py
  telegram.py  Telegram（Bot API，httpx，长轮询）
  email.py     邮件（imaplib + smtplib，标准库）
  discord.py   Discord（aiohttp：Gateway 收、REST 发；本来就有的依赖）
"""
from __future__ import annotations

import logging

from .accounts import any_enabled


def start() -> int:
    """服务启动时（main.py 的 lifespan）：claw 是 builtin 才起开着的账号。→ 起了几个。"""
    from . import hub
    return hub.start()


async def stop() -> None:
    from . import hub
    await hub.stop()


def install() -> None:
    """main.py 导入时挂上：收件箱新卡的钩子、名片 Agent 的 DELIVER、配对卡的钩子、message 工具同意后的跑法。"""
    from . import cards, tool
    cards.install()
    tool.install()


def tools_for(agent: str) -> list:
    """engine/tools.py 的 for_agent：这一轮多一个 message 工具（没开渠道就没有；渠道的配置读不了也没有，不连累别的工具）。"""
    from . import tool
    try:
        t = tool.tool(agent or "main")
    except Exception:  # noqa: BLE001
        logging.getLogger("mousse.channels").exception("channels: message tool unavailable")
        return []
    return [t] if t is not None else []


def send_to_owner(text: str, channel: str | None = None) -> dict:
    """服务器内部往主人的渠道投一条（定时任务、通知以后用；见 tool.py）。"""
    from . import tool
    return tool.send_to_owner(text, channel)


__all__ = ["any_enabled", "install", "send_to_owner", "start", "stop", "tools_for"]
