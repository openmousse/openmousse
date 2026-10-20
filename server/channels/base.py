"""渠道层的几样东西（第 6 批）：进来的一条（Inbound）、一个账号（Account）、渠道的接口（Channel）、出错（ChannelError）。

每个渠道（telegram.py、email.py、discord.py……）只写它自己的那个文件：继承 Channel，把收到的东西变成 Inbound 交给 hub，
发的时候照 Outbound 一条条发。渠道层不为某一家改形状：哪家多一样（按钮、改消息、正在输入），用类上的开关说。
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

ACCOUNT_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
VAR = re.compile(r"^\$\{([A-Z_][A-Z0-9_]*)\}$")
DM_POLICIES = ("pairing", "allowlist", "ignore")
CARD_MODES = ("turn", "all", "none")


class ChannelError(Exception):
    """发不出去、收不了。retry_after：渠道说要等几秒（Telegram 429）；permanent：再试也一样（令牌不对、对方把机器人拉黑了、消息不合格）；
    kind：给状态看的一类（conflict / auth / format / network / rate …）。str(e) 是一句给人看的话，不带令牌、密码。"""

    def __init__(self, message: str, *, retry_after: float | None = None, permanent: bool = False, kind: str = "") -> None:
        super().__init__(message)
        self.retry_after, self.permanent, self.kind = retry_after, permanent, kind


@dataclass
class Inbound:
    """进来的一条（落库的就是它的 JSON）。正文、媒体占位以外的东西都在 meta 里，一律当不可信的资料（照 OpenClaw 的入站信封）：
    对方自己设的名字、群名、转发自谁、名片、位置说明、语音转写……给模型时单独放一块，不拼进正文。"""
    channel: str                     # telegram / email / …
    account: str                     # 账号 id（server.json 里写的）
    ext_id: str                      # 渠道自己的 id：Telegram 的 update_id、邮件的 UIDVALIDITY:UID；去重靠它
    sender: str = ""                 # 谁发的（渠道上的 id：Telegram 的用户 id、邮件地址小写）
    chat: str = ""                   # 哪个聊天（Telegram 的 chat id；邮件 = 发件人地址）
    chat_type: str = "dm"            # dm / group
    topic: str = ""                  # 论坛话题（Telegram 的 message_thread_id）/ 邮件的那一串（第一封的 Message-ID 的指纹）
    message_id: str = ""             # 这一条在渠道上的 id（回的时候引用它）
    reply_to: str = ""               # 回的是哪一条（渠道上的 id）
    reply_to_bot: bool = False       # 回的是机器人自己发的那条
    ts: str = ""                     # 发出的时间（ISO）
    text: str = ""                   # 正文
    media: list[dict] = field(default_factory=list)   # 媒体占位：{kind: image | voice | audio | video | file, name, mime, size, ref}
    meta: dict = field(default_factory=dict)          # 不可信的元数据（见上）
    mentioned: bool = False          # 群里 @ 了机器人（或者叫了 Agent 的名字）
    is_bot: bool = False             # 发的人是个机器人（Telegram 的 from.is_bot）
    edited: bool = False             # 改过的消息（Telegram 的 edited_message）
    command: str = ""                # /start、/stop……（只认开头的那一个，去掉 @机器人名）
    args: str = ""                   # 命令后面的字
    callback: dict | None = None     # 按钮回调：{id, data, message_id}
    auth: dict = field(default_factory=dict)          # 邮件：{"verified": 发件人过了 DMARC / DKIM 对齐, "reason": …}
    skip: str = ""                   # 渠道自己已经看出来不用理的（自动回复、退信、自己发的……）：原因

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, s: str) -> Inbound:
        d = json.loads(s)
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    @property
    def lane(self) -> str:
        """串行处理按它：同一个聊天（同一个话题）一条一条来。"""
        return f"{self.chat}:{self.topic}" if self.topic else self.chat


@dataclass
class Outbound:
    """出去的一条（出去的队列里一行）。text 是这一段（已经分好段）；format：markdown（渠道自己转）/ plain / html（已经转好的）。"""
    id: int
    channel: str
    account: str
    chat: str
    topic: str = ""
    text: str = ""
    format: str = "markdown"
    reply_to: str = ""               # 回的是哪一条（渠道上的 id）
    buttons: list[list[dict]] = field(default_factory=list)  # [[{text, data}]]：一行几个按钮
    edit: str = ""                   # 改这一条（渠道上的 id），不发新的
    extra: dict = field(default_factory=dict)  # 渠道自己用的：邮件的主题、In-Reply-To、References……
    why: str = ""                    # reply / card / pair / deliver / notify / message / typing …
    tries: int = 0


@dataclass
class Account:
    """一个账号（一个机器人、一个邮箱）。cfg 是 server.json 里这一个账号的原样（单账号的写法也拼成这样）。"""
    kind: str
    id: str
    cfg: dict
    channel_cfg: dict

    def get(self, key: str, default: Any = None) -> Any:
        """账号上写的优先，再看渠道那一层（照 OpenClaw：账号的设置盖过渠道根上的）。"""
        if key in self.cfg:
            return self.cfg[key]
        return self.channel_cfg.get(key, default)

    @property
    def enabled(self) -> bool:
        return self.channel_cfg.get("enabled") is True and self.cfg.get("enabled", True) is not False

    @property
    def agent(self) -> str:
        return str(self.get("agent") or "main").strip() or "main"

    @property
    def dm(self) -> str:
        d = str(self.get("dm") or ("ignore" if self.kind == "email" else "pairing")).strip().lower()
        return d if d in DM_POLICIES else "pairing" if self.kind != "email" else "ignore"

    @property
    def cards(self) -> str:
        c = str(self.get("cards") or "turn").strip().lower()
        return c if c in CARD_MODES else "turn"

    @property
    def tier(self) -> str:
        t = str(self.get("tier") or "friend").strip().lower()
        return t if t in ("close", "friend", "mate") else "friend"

    def groups(self) -> dict:
        g = self.get("groups")
        return g if isinstance(g, dict) else {}

    def owners(self) -> set[str]:
        """配置里写的主人（Telegram：用户 id；邮件：地址，小写）。Telegram 还认认主人的码记下的（people.py）。"""
        o = self.get("owners")
        return {norm_id(self.kind, x) for x in o if str(x).strip()} if isinstance(o, list) else set()

    def allow_from(self) -> set[str]:
        a = self.get("allow_from")
        return {norm_id(self.kind, x) for x in a if str(x).strip()} if isinstance(a, list) else set()

    def secret(self, value: Any) -> str:
        """配置里的一个值：写成 ${变量名} 的从 secrets.env / 环境变量里取（secretstore.lookup），别的原样。取不到 = 空。"""
        s = str(value or "").strip()
        m = VAR.match(s)
        if not m:
            return s
        import secretstore
        return secretstore.lookup(m.group(1))[0]

    def plain_secret(self, value: Any) -> bool:
        """这个值是不是直接写在 server.json 里的密码 / 令牌（不是 ${变量名}）：状态里提醒一句。"""
        s = str(value or "").strip()
        return bool(s) and not VAR.match(s)


def norm_id(kind: str, value: Any) -> str:
    """渠道上的 id 统一写法：邮件地址小写、去空白；Telegram 去掉 telegram: / tg: 前缀（照 OpenClaw 的 allow-from）；Discord 只留数字 id。"""
    s = str(value or "").strip()
    if kind == "email":
        return s.lower()
    if kind == "telegram":
        return re.sub(r"^(?:telegram|tg):", "", s, flags=re.I)
    if kind == "discord":  # discord:123、user:123、<@123>、<@!123> → 123
        return re.sub(r"^(?:discord:|user:)?<?@?!?(\d+)>?$", r"\1", s, flags=re.I)
    return s


class Channel:
    """一个渠道的一个账号跑起来的样子。子类填：收（run：一直收到 stop，收到的交给 self.hub.ingest）、发（send）、测（test），
    能做的用类上的开关说。"""
    kind = ""
    label = ""
    max_len = 4000         # 一段最多多少字（分段用；渠道自己的上限再留一点余量）
    buttons = False        # 消息上能放按钮（审批、选项在聊天里点）
    edits = False          # 能改发出去的消息（边写边改）
    typing_every = 0.0     # 「正在输入」多久发一次；0 = 没有
    formats = ("plain",)   # 认哪几种格式：plain / html
    groups = False         # 有群
    min_gap = 0.0          # 同一个聊天两条之间至少隔几秒（渠道的限频）

    def __init__(self, acc: Account, hub: Any) -> None:
        self.acc, self.hub = acc, hub
        self.me: dict = {}  # 自己是谁（Telegram 的 getMe：id、username；邮件：地址）

    async def run(self) -> None:
        raise NotImplementedError

    async def send(self, out: Outbound) -> list[str]:
        """发一条 → 渠道上的消息 id（几段就几个）。出错抛 ChannelError。"""
        raise NotImplementedError

    async def test(self) -> dict:
        """测一下：连得上、令牌 / 密码对不对（不发消息）。→ {"ok", "detail"}；出错抛 ChannelError。"""
        raise NotImplementedError

    async def typing(self, chat: str, topic: str = "") -> None:
        """正在输入（尽力而为，出错不抛）。"""

    async def fetch(self, item: dict) -> bytes:
        """下载主人发来的一个媒体（媒体占位里的 ref）。出错抛 ChannelError。"""
        raise ChannelError("this channel can't fetch media", permanent=True)

    async def answer_callback(self, cb: dict, text: str = "") -> None:
        """按钮点了以后回一下（Telegram 的 answerCallbackQuery），不回客户端会一直转圈。"""

    async def edit(self, chat: str, message_id: str, text: str, *, topic: str = "", buttons: list | None = None, fmt_: str = "plain") -> None:
        """改一条发出去的（边写边改、卡点过以后写结果用；fmt_ = plain / markdown）。出错抛 ChannelError。"""
        raise ChannelError("this channel can't edit messages", permanent=True)

    def reply_ref(self, ib: Inbound) -> str:
        """回一条进来的时，回的是哪一条（渠道上的 id）：默认群里回那一条、私聊不用。"""
        return ib.message_id if ib.chat_type == "group" else ""

    def reply_extra(self, ib: Inbound) -> dict:
        """回一条进来的时渠道自己要的（邮件：主题、In-Reply-To、References）。"""
        return {}

    def language(self, ib: Inbound) -> str:
        """对方大概用什么语言（固定句子按它说）：zh / en；看不出来按 server.json 的 language。"""
        from config import settings
        code = str(ib.meta.get("language") or "").lower()
        return "zh" if code.startswith("zh") else "en" if code else settings.language

    async def close(self) -> None:
        """停下时收尾（关连接）。"""
