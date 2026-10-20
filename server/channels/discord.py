"""Discord（第 6 批第 5 项）：用 requirements.txt 里本来就有的 aiohttp 连 Gateway（WebSocket）收、调 REST（v10）发，不加依赖。

收：GET /gateway/bot 拿地址 → Gateway（JSON，不压缩）：HELLO → IDENTIFY（intents：GUILDS、GUILD_MESSAGES、DIRECT_MESSAGES；
  message_content 开了再加 MESSAGE_CONTENT——要先在开发者后台打开的特权 intent；不开也收得到私聊的话和 @ 它的话）→ READY（自己的 id、
  session、resume 用的地址、application id）。按 heartbeat_interval 心跳，上一次心跳没回 ACK 就断开重连（僵死的连接）。断了先 RESUME
  （session + 最后一个 seq），Discord 补发断开期间的；补不了（INVALID_SESSION、关闭码 4007 / 4009）再 IDENTIFY。
  Gateway 没有「确认」这一说：seq 和 session 跟落库的那一批写在同一个事务里（hub.ingest），落不进库就退回上一个落了库的 seq 重连 RESUME，
  让 Discord 再发一遍；重启以后也照记下的 RESUME（会话还在就补发过来）。按消息 id 去重。会话已经过期的只能从头 IDENTIFY，
  断开期间的消息就收不到了（Discord 不留）。
  关闭码 4004（令牌不对）、4013 / 4014（intent 不对、没在开发者后台打开）、4010–4012：停下、状态写清楚，10 分钟后再看，不反复连。
  Discord 不拦两个程序连同一个机器人（不像 Telegram 会 409）：每个安装用自己的机器人，别和别的网关共用，不然两边都会回。
认：私聊（chat = 对方的用户 id，和 Telegram 一样；私聊频道的 id 另外记着）、服务器里的频道（群：默认关；开了的频道里要 @ 机器人或它的角色、
  回复它、或者叫 Agent 的名字才答）、子区 / 论坛的帖子（thread）按话题分会话：chat = 上级频道，topic = thread 的 id。
  没开、不在 allow 里的频道的消息不落库（大服务器里每一条都存就太多了）。别的机器人、webhook 发的默认不理（router）；系统消息（有人加入、
  置顶……）不收；改过的消息不收（只要 MESSAGE_CREATE）。名字、服务器名、频道名、引用的那条、转发的内容都放进 meta（不可信，见 owner.envelope）。
  媒体：附件（图、语音消息、文件）、贴纸只记占位，主人的才下载（Discord 的 CDN 地址，最多 max_media_mb）。
发：POST /channels/{id}/messages，一段最多 1900 字（上限 2000）；Markdown Discord 自己会显示，照原样发；allowed_mentions 一律空
  （回复里写了 @everyone、@某人也不会真的叫到谁）。群里回叫它的那一条（message_reference，原消息没了也照发）；按钮（components）只在最后一段。
  点按钮是一次 interaction：一收到就先回 DEFERRED_UPDATE_MESSAGE（Discord 要 3 秒内回），处理完要说的话发 followup（只有点的人看得见）。
  429 照 retry_after 等；没权限、发不了私信（50007）是永久的错（进死信）。正在输入：POST /channels/{id}/typing，每 8 秒一次（一次管 10 秒）。
  只知道用户 id 的（配置里的 owners、send_to_owner、发给主人的卡）先 POST /users/@me/channels 打开私聊，记下来。
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import sys
from urllib.parse import urlsplit

import aiohttp

from i18n import L

from . import store
from .base import Channel, ChannelError, Inbound, Outbound

log = logging.getLogger("mousse.channels")
API = "https://discord.com/api/v10"
GATEWAY_QS = "?v=10&encoding=json"
GUILDS, GUILD_MESSAGES, DIRECT_MESSAGES, MESSAGE_CONTENT = 1 << 0, 1 << 9, 1 << 12, 1 << 15
APP_CONTENT = (1 << 18) | (1 << 19)   # application flags：GATEWAY_MESSAGE_CONTENT（_LIMITED）= 后台打开了 Message Content
FATAL = {4004: "auth", 4010: "config", 4011: "config", 4012: "config", 4013: "intents", 4014: "intents"}
FRESH = {4007, 4009}                  # seq 不对、会话超时：不能 RESUME，从头 IDENTIFY
FATAL_WAIT = 600
RECONNECT = 4900                      # 自己断开（重连、僵死）用的关闭码：不用 1000 / 1001，那两个会让会话作废、不能 RESUME
TYPES = {0, 19}                       # 普通消息、回复；别的是系统消息
THREAD_TYPES = {10, 11, 12}           # 频道类型：子区、帖子
VOICE = 1 << 13                       # 附件的 flags：语音消息
CDN_HOSTS = ("cdn.discordapp.com", "media.discordapp.net")
INGEST_TRIES = 3


def _name(user: dict | None) -> str:
    if not isinstance(user, dict):
        return ""
    return str(user.get("global_name") or user.get("username") or "")


class Discord(Channel):
    kind = "discord"
    label = "Discord"
    max_len = 1900
    buttons = True
    edits = True
    typing_every = 8.0
    formats = ("markdown", "plain")
    groups = True
    min_gap = 1.0

    def __init__(self, acc, hub) -> None:
        super().__init__(acc, hub)
        self.base = str(acc.get("api_base") or API).rstrip("/")
        self.http: aiohttp.ClientSession | None = None
        self.seq: int | None = None
        self.saved_seq: int | None = None     # 最后一个落了库的 seq：落库失败时退回它重连
        self.session_id = ""
        self.resume_url = ""
        self.app_id = ""
        self.acked = True
        self.threads: dict[str, str] = {}     # thread 的 id → 上级频道
        self.channels: dict[str, dict] = {}   # 服务器里的频道 id → {name, guild}
        self.guilds: dict[str, str] = {}      # 服务器 id → 名字
        self.roles: set[str] = set()          # 机器人自己的角色（@ 它的角色也算 @ 它）
        self.dm: dict[str, str] = {}          # 用户 id → 私聊频道 id（也记在库里）
        self.dm_new: dict[str, str] = {}      # 这一批新认出来的私聊频道：和这一批一起落库

    # —— 调 REST ——
    def token(self) -> str:
        return self.acc.secret(self.acc.get("token"))

    def _client(self) -> aiohttp.ClientSession:
        if self.http is None or self.http.closed:
            self.http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30, connect=10), trust_env=True)
        return self.http

    async def call(self, method: str, path: str, payload: dict | None = None, *, timeout: float = 30) -> object:
        """调一个 REST 接口 → JSON。出错抛 ChannelError（话里不带令牌）。"""
        tok = self.token()
        if not tok:
            raise ChannelError(L("没有机器人的令牌（设置里存一个）", "No bot token (save one in settings)"), permanent=True, kind="auth")
        headers = {"Authorization": f"Bot {tok}", "User-Agent": "DiscordBot (OpenMousse, 1.0)"}
        try:
            async with self._client().request(method, self.base + path, json=payload, headers=headers,
                                              timeout=aiohttp.ClientTimeout(total=timeout)) as r:
                status, text = r.status, await r.text()
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            raise ChannelError(f"{method} {path.split('?')[0]}: {type(e).__name__}", kind="network") from None
        try:
            j = json.loads(text) if text else {}
        except ValueError:
            j = {}
        if 200 <= status < 300:
            return j
        j = j if isinstance(j, dict) else {}
        desc = str(j.get("message") or f"HTTP {status}").replace(tok, "<token>")[:300]
        if j.get("code"):
            desc = f"{desc} ({j['code']})"
        if status == 429:
            raise ChannelError(desc, retry_after=float(j.get("retry_after") or 1), kind="rate")
        if status == 401:
            raise ChannelError(desc, permanent=True, kind="auth")
        if status == 403:
            raise ChannelError(desc, permanent=True, kind="forbidden")
        if status == 404:
            raise ChannelError(desc, permanent=True, kind="gone")
        if status >= 500:
            raise ChannelError(desc, kind="network")
        raise ChannelError(desc, permanent=True, kind="bad")

    # —— 收：Gateway ——
    async def run(self) -> None:
        kind, account = self.acc.kind, self.acc.id
        st = store.states(kind, account)
        self.session_id, self.resume_url = st.get("session_id", ""), st.get("resume_url", "")
        self.seq = self.saved_seq = int(st["seq"]) if str(st.get("seq") or "").isdigit() else None
        self.app_id = st.get("app_id", "")
        if st.get("bot_id"):
            self.me = {"id": st["bot_id"], "username": st.get("bot_username", ""), "name": st.get("bot_username", "")}
        backoff = 1.0
        while True:
            try:
                await self._connect()
                backoff = 1.0
                await asyncio.sleep(0.5 + random.random())  # 要我们重连的（op 7、僵死、可以 RESUME 的关闭码）：稍等一下再连
            except ChannelError as e:
                if e.kind in FATAL.values():
                    self.hub.note("auth" if e.kind == "auth" else "error", self._fatal_text(4004) if e.kind == "auth" else str(e), error=str(e))
                    await asyncio.sleep(FATAL_WAIT)
                    continue
                self.hub.note("error", L("连不上 Discord，过一会儿再试", "Can't reach Discord; retrying"), error=str(e))
                await asyncio.sleep(min(backoff, 60) * (1 + random.random() * 0.2))
                backoff *= 2

    async def _gateway_url(self) -> str:
        info = await self.call("GET", "/gateway/bot")
        info = info if isinstance(info, dict) else {}
        lim = info.get("session_start_limit") if isinstance(info.get("session_start_limit"), dict) else {}
        if lim.get("remaining") == 0:  # 一天能 IDENTIFY 的次数用完了：等它重置
            wait = min(float(lim.get("reset_after") or 60_000) / 1000, 3600)
            self.hub.note("waiting", L(f"今天连 Discord 的次数用完了，{int(wait)} 秒后再连", f"Out of Discord connections for today; trying again in {int(wait)}s"))
            await asyncio.sleep(wait)
        return str(info.get("url") or "wss://gateway.discord.gg")

    async def _connect(self) -> None:
        """连一次 Gateway，一直收到断开。要重连就返回；不该再连（令牌、intent 不对）抛 ChannelError。"""
        resume = bool(self.session_id and self.seq is not None)
        url = (self.resume_url if resume and self.resume_url else await self._gateway_url()).rstrip("/") + "/" + GATEWAY_QS
        try:
            ws = await self._client().ws_connect(url, heartbeat=None, autoping=True, max_msg_size=16 * 1024 * 1024)
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            if resume:
                self.resume_url = ""  # 那台连不上：下次照 /gateway/bot 给的地址连（还是先 RESUME；不行 Discord 会说，再 IDENTIFY）
            raise ChannelError(f"gateway: {type(e).__name__}", kind="network") from None
        beat: asyncio.Task | None = None
        try:
            hello = await ws.receive(timeout=30)
            p = json.loads(hello.data) if hello.type == aiohttp.WSMsgType.TEXT else {}
            if p.get("op") != 10:
                raise ChannelError("gateway: no HELLO", kind="network")
            self.acked = True
            beat = asyncio.get_running_loop().create_task(self._beat(ws, float(p["d"]["heartbeat_interval"]) / 1000))
            if resume:
                await ws.send_json({"op": 6, "d": {"token": self.token(), "session_id": self.session_id, "seq": self.seq}})
            else:
                await self._identify(ws)
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    await self._on(ws, json.loads(msg.data))
                elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                    break
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, KeyError, TypeError) as e:
            raise ChannelError(f"gateway: {type(e).__name__}", kind="network") from None
        finally:
            if beat is not None:
                beat.cancel()
            if not ws.closed:
                await ws.close(code=RECONNECT)
        code = ws.close_code
        if code in FATAL:
            raise ChannelError(self._fatal_text(code), permanent=True, kind=FATAL[code])
        if code in FRESH:
            self._forget()

    def _fatal_text(self, code: int) -> str:
        if FATAL[code] == "auth":
            return L("令牌不对（Discord 回 401 / 关闭码 4004）：在设置里重新存一个", "The token was rejected (Discord said 401 / close code 4004); "
                     "save a new one in settings")
        if FATAL[code] == "intents":
            return L(f"Discord 不给这些 intent（关闭码 {code}）：在开发者后台的 Bot 页打开 Message Content Intent，或者把 message_content 关掉",
                     f"Discord refused the intents (close code {code}): turn on Message Content Intent on the Bot page of the Developer Portal, "
                     "or set message_content to false")
        return L(f"Discord 不接受这个连接（关闭码 {code}）", f"Discord refused the connection (close code {code})")

    async def _identify(self, ws) -> None:
        intents = GUILDS | GUILD_MESSAGES | DIRECT_MESSAGES | (MESSAGE_CONTENT if self.acc.get("message_content") is True else 0)
        await ws.send_json({"op": 2, "d": {"token": self.token(), "intents": intents,
                                           "properties": {"os": sys.platform, "browser": "openmousse", "device": "openmousse"}}})

    async def _beat(self, ws, interval: float) -> None:
        await asyncio.sleep(interval * random.random())  # 第一次心跳随机错开（Discord 的要求）
        while not ws.closed:
            if not self.acked:  # 上一次心跳没回：僵死的连接，断开重连（RESUME）
                await ws.close(code=RECONNECT)
                return
            self.acked = False
            await ws.send_json({"op": 1, "d": self.seq})
            await asyncio.sleep(interval)

    def _forget(self) -> None:
        """会话作废了：下次从头 IDENTIFY。"""
        self.session_id, self.resume_url, self.seq, self.saved_seq = "", "", None, None
        store.set_state(self.acc.kind, self.acc.id, session_id="", resume_url="", seq="")

    async def _on(self, ws, p: dict) -> None:
        op = p.get("op")
        if op == 11:
            self.acked = True
        elif op == 1:  # Discord 要一次心跳
            await ws.send_json({"op": 1, "d": self.seq})
        elif op == 7:  # 要我们重连（RESUME）
            await ws.close(code=RECONNECT)
        elif op == 9:  # 会话不能用了：d 为真还能 RESUME，否则从头 IDENTIFY
            if not p.get("d"):
                self._forget()
            await asyncio.sleep(1 + random.random() * 4)
            await ws.close(code=RECONNECT)
        elif op == 0:
            s = p["s"] if isinstance(p.get("s"), int) else None
            # seq 处理完这一条才往前挪（心跳、RESUME、关的时候记下的都是它）：处理到一半被打断的，RESUME 时 Discord 会再发
            if await self._dispatch(ws, str(p.get("t") or ""), p.get("d") if isinstance(p.get("d"), dict) else {}, s) and s is not None:
                self.seq = s

    async def _dispatch(self, ws, t: str, d: dict, s: int | None) -> bool:
        """一个事件。→ False：这一条没落进库、已经退回去重连了（seq 别往前挪）。"""
        kind, account = self.acc.kind, self.acc.id
        if t == "READY":
            user = d.get("user") if isinstance(d.get("user"), dict) else {}
            if self.me.get("id") and str(user.get("id") or "") != self.me.get("id"):
                self.dm.clear()  # 换了机器人：私聊频道是另一套
            self.me = {"id": str(user.get("id") or ""), "username": str(user.get("username") or ""), "name": _name(user)}
            self.session_id, self.resume_url = str(d.get("session_id") or ""), str(d.get("resume_gateway_url") or "")
            self.app_id = str((d.get("application") or {}).get("id") or "")
            self.seq = self.saved_seq = s
            store.set_state(kind, account, bot=json.dumps(self.me, ensure_ascii=False), bot_username=self.me["username"], bot_id=self.me["id"],
                            app_id=self.app_id, session_id=self.session_id, resume_url=self.resume_url, seq=str(s if s is not None else ""))
            self.hub.note("ok", L(f"在收 {self.me['username']} 的消息", f"Receiving for {self.me['username']}"))
        elif t == "RESUMED":
            self.hub.note("ok", L(f"在收 {self.me.get('username') or 'Discord'} 的消息", f"Receiving for {self.me.get('username') or 'Discord'}"))
        elif t == "GUILD_CREATE":
            self._guild(d)
        elif t in ("THREAD_CREATE", "THREAD_UPDATE") and d.get("parent_id"):
            self.threads[str(d["id"])] = str(d["parent_id"])
        elif t == "THREAD_LIST_SYNC":
            for th in d.get("threads") or []:
                if isinstance(th, dict) and th.get("parent_id"):
                    self.threads[str(th["id"])] = str(th["parent_id"])
        elif t in ("CHANNEL_CREATE", "CHANNEL_UPDATE") and d.get("guild_id"):
            self.channels[str(d["id"])] = {"name": str(d.get("name") or ""), "guild": str(d["guild_id"])}
        elif t == "MESSAGE_CREATE":
            ib = await self.normalize(d)
            if ib is not None:
                return await self._ingest(ws, [ib], s)
        elif t == "INTERACTION_CREATE":
            ib = self.interaction(d)
            if ib is not None:
                await self._ack(d)
                return await self._ingest(ws, [ib], s)
        return True

    def _guild(self, d: dict) -> None:
        if d.get("unavailable"):
            return
        gid = str(d.get("id") or "")
        self.guilds[gid] = str(d.get("name") or "")
        for c in d.get("channels") or []:
            if isinstance(c, dict):
                self.channels[str(c.get("id"))] = {"name": str(c.get("name") or ""), "guild": gid}
        for th in d.get("threads") or []:
            if isinstance(th, dict) and th.get("parent_id"):
                self.threads[str(th["id"])] = str(th["parent_id"])
        me = self.me.get("id")
        for r in d.get("roles") or []:
            if isinstance(r, dict) and me and str((r.get("tags") or {}).get("bot_id") or "") == me:
                self.roles.add(str(r["id"]))

    async def _ingest(self, ws, items: list[Inbound], s: int | None) -> bool:
        """落库（这一条的 seq、session、新认出来的私聊频道写在同一个事务里）。落不进去：退回上一个落了库的 seq 重连，让 Discord 再发一遍。"""
        seq = s if s is not None else self.seq
        kv = {"seq": str(seq if seq is not None else ""), "session_id": self.session_id, "resume_url": self.resume_url, **self.dm_new}
        err = ""
        for n in range(INGEST_TRIES):
            try:
                self.hub.ingest(items, kv)
                self.saved_seq = seq
                self.dm_new.clear()
                return True
            except Exception as e:  # noqa: BLE001 — 库被锁之类：等一下再试，还不行就重连补发
                err = f"{type(e).__name__}: {e}"
                await asyncio.sleep(0.2 * (n + 1))
        self.hub.note("error", L("收到的消息存不进库：重连让 Discord 再发一遍", "Couldn't store incoming messages; reconnecting so Discord sends them again"),
                      error=err)
        self.seq = self.saved_seq
        self.dm_new.clear()
        await ws.close(code=RECONNECT)
        return False

    # —— 一条消息 → Inbound ——
    async def _parent(self, cid: str) -> str:
        """频道是子区 / 帖子的话 → 上级频道的 id；不是 → ""。先看 Gateway 给过的，不认得的问一次 REST（记下来）。"""
        if cid in self.threads:
            return self.threads[cid]
        if cid in self.channels:
            return ""
        try:
            info = await self.call("GET", f"/channels/{cid}", timeout=10)
        except ChannelError:
            return ""
        info = info if isinstance(info, dict) else {}
        if info.get("type") in THREAD_TYPES and info.get("parent_id"):
            self.threads[cid] = str(info["parent_id"])
            return self.threads[cid]
        self.channels[cid] = {"name": str(info.get("name") or ""), "guild": str(info.get("guild_id") or "")}
        return ""

    async def normalize(self, d: dict) -> Inbound | None:
        """一条 MESSAGE_CREATE → Inbound；不收的（系统消息、自己发的、没开的频道）→ None。"""
        acc = self.acc
        author = d.get("author") if isinstance(d.get("author"), dict) else {}
        mid, cid, gid = str(d.get("id") or ""), str(d.get("channel_id") or ""), str(d.get("guild_id") or "")
        me = str(self.me.get("id") or "")
        sender = str(author.get("id") or "")
        if not mid or not cid or not sender or d.get("type", 0) not in TYPES or (me and sender == me):
            return None
        if gid and acc.groups().get("enabled") is not True:
            return None  # 群没开：服务器里的话一条都不存
        text = str(d.get("content") or "")
        mentions = [str(u.get("id")) for u in d.get("mentions") or [] if isinstance(u, dict)]
        mentioned = bool(me) and (me in mentions or any(str(r) in self.roles for r in d.get("mention_roles") or []))
        if me:
            text = re.sub(rf"<@!?{me}>[,:\-]*\s*", "", text)
        for r in self.roles:
            text = re.sub(rf"<@&{r}>[,:\-]*\s*", "", text)
        text = text.strip()
        if gid:
            parent = await self._parent(cid)
            chat, topic = (parent, cid) if parent else (cid, "")
            ctype = "group"
        else:
            chat, topic, ctype = sender, "", "dm"
            if self.dm.get(sender) != cid:
                self.dm[sender] = cid
                self.dm_new[f"dm:{me}:{sender}"] = cid
        ref = d.get("referenced_message") if isinstance(d.get("referenced_message"), dict) else None
        reply_to_bot = bool(ref and me and str((ref.get("author") or {}).get("id")) == me)
        meta: dict = {"name": _name(author), "username": str(author.get("username") or "")}
        if gid:
            meta["chat_title"] = (self.channels.get(chat) or {}).get("name") or ""
            meta["server"] = self.guilds.get(gid, "")
        if ref:
            meta["reply_to_from"] = _name(ref.get("author"))
            if not reply_to_bot:
                meta["reply_to_text"] = str(ref.get("content") or "")[:1000]
        snaps = d.get("message_snapshots") if isinstance(d.get("message_snapshots"), list) else []
        if snaps and isinstance(snaps[0], dict):
            meta["forwarded"] = str((snaps[0].get("message") or {}).get("content") or "")[:4000] or "(forwarded message)"
        command, args = "", ""
        if text.startswith("/"):
            head, _, rest = text.partition(" ")
            if re.fullmatch(r"[A-Za-z0-9_]{1,32}", head[1:]):
                command, args = head[1:].lower(), rest.strip()
        media = self._media(d)
        ib = Inbound(channel=acc.kind, account=acc.id, ext_id=f"m:{mid}", sender=sender, chat=chat, chat_type=ctype, topic=topic,
                     message_id=mid, reply_to=str((d.get("message_reference") or {}).get("message_id") or ""), reply_to_bot=reply_to_bot,
                     ts=str(d.get("timestamp") or ""), text=text, media=media, meta={k: v for k, v in meta.items() if v not in ("", None)},
                     mentioned=mentioned, is_bot=bool(author.get("bot")) or bool(d.get("webhook_id")), command=command, args=args)
        if gid:
            from . import router
            gate = router.group_gate(acc, ib)
            if gate in ("off", "not_allowed"):
                return None  # 不在开了的频道里：不存
            if not text and not media:
                ib.skip = "no_content"  # 没开 Message Content 时别人没 @ 它的话是空的：不进群历史
        return ib

    @staticmethod
    def _media(d: dict) -> list[dict]:
        out = []
        for a in d.get("attachments") or []:
            if not isinstance(a, dict):
                continue
            ctype = str(a.get("content_type") or "application/octet-stream").split(";")[0]
            kind = ("voice" if int(a.get("flags") or 0) & VOICE or a.get("duration_secs") else "image" if ctype.startswith("image/")
                    else "video" if ctype.startswith("video/") else "audio" if ctype.startswith("audio/") else "file")
            out.append({"kind": kind, "name": str(a.get("filename") or "file"), "mime": ctype, "size": a.get("size"), "ref": str(a.get("url") or "")})
        for s in d.get("sticker_items") or []:
            if isinstance(s, dict):
                out.append({"kind": "sticker", "name": str(s.get("name") or "sticker"), "mime": "image/png", "size": None, "ref": ""})
        return out

    def interaction(self, d: dict) -> Inbound | None:
        """点了按钮（MESSAGE_COMPONENT）→ 带 callback 的 Inbound；别的 interaction（斜杠命令……）不认。"""
        if d.get("type") != 3 or not d.get("id") or not d.get("token"):
            return None
        user = d.get("user") if isinstance(d.get("user"), dict) else (d.get("member") or {}).get("user") or {}
        uid = str(user.get("id") or "")
        msg = d.get("message") if isinstance(d.get("message"), dict) else {}
        gid, cid = str(d.get("guild_id") or ""), str(d.get("channel_id") or msg.get("channel_id") or "")
        if gid:
            parent = self.threads.get(cid, "")
            chat, topic = (parent, cid) if parent else (cid, "")
        else:
            chat, topic = uid, ""
            if cid and self.dm.get(uid) != cid:
                self.dm[uid] = cid
                self.dm_new[f"dm:{self.me.get('id') or ''}:{uid}"] = cid
        lang = str(d.get("locale") or "")
        return Inbound(channel=self.acc.kind, account=self.acc.id, ext_id=f"i:{d['id']}", sender=uid, chat=chat, chat_type="group" if gid else "dm",
                       topic=topic, is_bot=bool(user.get("bot")),
                       callback={"id": str(d["id"]), "token": str(d["token"]), "data": str((d.get("data") or {}).get("custom_id") or ""),
                                 "message_id": str(msg.get("id") or "")},
                       meta={k: v for k, v in {"name": _name(user), "username": str(user.get("username") or ""), "language": lang}.items() if v})

    async def _ack(self, d: dict) -> None:
        """点按钮要 3 秒内回：先回「收到了，消息不改」（DEFERRED_UPDATE_MESSAGE），处理完再说话（followup）。"""
        try:
            await self.call("POST", f"/interactions/{d['id']}/{d['token']}/callback", {"type": 6}, timeout=5)
        except ChannelError as e:
            log.info("discord interaction ack: %s", e)

    # —— 发 ——
    async def _target(self, chat: str, topic: str = "") -> str:
        """要发到哪个频道：话题（thread）→ 它自己；服务器里的频道 → 它自己；用户 id → 私聊频道（不知道就打开一个，记下来）。"""
        if topic:
            return topic
        if chat in self.channels or chat in self.threads:
            return chat
        kind, account = self.acc.kind, self.acc.id
        me = self.me.get("id") or store.state(kind, account, "bot_id")
        dm = self.dm.get(chat) or store.state(kind, account, f"dm:{me}:{chat}")
        if dm:
            self.dm[chat] = dm
            return dm
        try:
            res = await self.call("POST", "/users/@me/channels", {"recipient_id": chat})
        except ChannelError as e:
            if e.kind in ("rate", "network", "auth"):
                raise
            self.channels.setdefault(chat, {})  # 不是用户：当成频道
            return chat
        dm = str((res or {}).get("id") or "") if isinstance(res, dict) else ""
        if not dm:
            return chat
        self.dm[chat] = dm
        store.set_state(kind, account, **{f"dm:{me}:{chat}": dm})
        return dm

    @staticmethod
    def _components(buttons: list) -> list:
        rows = []
        for row in (buttons or [])[:5]:
            comps = []
            for b in row[:5]:
                data = str(b.get("data") or "")
                style = 3 if data.startswith("ib:a:") else 4 if data.startswith("ib:r:") else 2
                comps.append({"type": 2, "style": style, "label": str(b.get("text") or "")[:80], "custom_id": data[:100]})
            if comps:
                rows.append({"type": 1, "components": comps})
        return rows

    async def send(self, out: Outbound) -> list[str]:
        if out.edit:
            try:
                await self.edit(out.chat, out.edit, out.text, topic=out.topic, buttons=out.buttons, fmt_=out.format)
                return [out.edit]
            except ChannelError as e:
                if e.kind == "rate":
                    raise
                # 草稿改不动了（太老、被删）：照常发一条新的
        cid = await self._target(out.chat, out.topic)
        text = re.sub(r"<[^>]+>", "", out.text) if out.format == "html" else out.text
        payload: dict = {"content": text[:2000], "allowed_mentions": {"parse": []}}
        if out.reply_to:
            payload["message_reference"] = {"message_id": out.reply_to, "fail_if_not_exists": False}
        if out.buttons:
            payload["components"] = self._components(out.buttons)
        res = await self.call("POST", f"/channels/{cid}/messages", payload)
        return [str((res or {}).get("id") or "") if isinstance(res, dict) else ""]

    async def edit(self, chat: str, message_id: str, text: str, *, topic: str = "", buttons: list | None = None, fmt_: str = "plain") -> None:
        cid = await self._target(chat, topic)
        await self.call("PATCH", f"/channels/{cid}/messages/{message_id}",
                        {"content": text[:2000], "components": self._components(buttons or []), "allowed_mentions": {"parse": []}})

    async def typing(self, chat: str, topic: str = "") -> None:
        try:
            cid = await self._target(chat, topic)
            await self.call("POST", f"/channels/{cid}/typing", timeout=10)
        except ChannelError:
            pass

    async def answer_callback(self, cb: dict, text: str = "") -> None:
        """按钮的回话：只有点的人看得见（ephemeral followup）。ACK 在收到时就回过了。"""
        if not text or not self.app_id or not cb.get("token"):
            return
        try:
            await self.call("POST", f"/webhooks/{self.app_id}/{cb['token']}", {"content": text[:2000], "flags": 64}, timeout=10)
        except ChannelError:
            pass

    async def fetch(self, item: dict) -> bytes:
        """主人发来的一个附件：从 Discord 的 CDN 下载（最多 max_media_mb）。"""
        url = str(item.get("ref") or "")
        parts = urlsplit(url)
        if parts.scheme not in ("https", "http") or parts.hostname not in (*CDN_HOSTS, urlsplit(self.base).hostname):
            raise ChannelError(L("不是 Discord 的附件地址", "Not a Discord attachment URL"), permanent=True)
        cap = int(self.acc.get("max_media_mb") or 20) * 1024 * 1024
        try:
            async with self._client().get(url, timeout=aiohttp.ClientTimeout(total=120)) as r:
                if r.status != 200:
                    raise ChannelError(f"download: HTTP {r.status}")
                buf = bytearray()
                async for chunk in r.content.iter_chunked(64 * 1024):
                    buf += chunk
                    if len(buf) > cap:
                        raise ChannelError(L("附件太大", "The attachment is too large"), permanent=True)
                return bytes(buf)
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            raise ChannelError(f"download: {type(e).__name__}", kind="network") from None

    async def test(self) -> dict:
        me = await self.call("GET", "/users/@me")
        me = me if isinstance(me, dict) else {}
        out: dict = {"detail": str(me.get("username") or ""), "bot": {"id": me.get("id"), "username": me.get("username"), "name": _name(me)}}
        warnings = [L("Discord 不拦两个程序用同一个机器人：别和别的网关（比如 OpenClaw）共用这个机器人，不然两边都会回。",
                      "Discord doesn't stop two programs from using one bot: don't share this bot with another gateway (OpenClaw, say), "
                      "or both will answer.")]
        if self.acc.get("message_content") is True:
            try:
                app = await self.call("GET", "/applications/@me")
                if isinstance(app, dict) and not int(app.get("flags") or 0) & APP_CONTENT:
                    warnings.append(L("message_content 开着，但开发者后台的 Message Content Intent 没打开：连上时会被拒（4014）。",
                                      "message_content is on, but Message Content Intent isn't enabled in the Developer Portal: "
                                      "connecting would be refused (4014)."))
            except ChannelError:
                pass
        out["warning"] = " ".join(warnings)
        return out

    async def close(self) -> None:
        if self.seq is not None:  # seq 只在一条处理完以后才往前挪：记下它，下次 RESUME 从这里接
            try:
                store.set_state(self.acc.kind, self.acc.id, seq=str(self.seq), session_id=self.session_id, resume_url=self.resume_url)
            except Exception:  # noqa: BLE001
                pass
        if self.http is not None:
            await self.http.close()
            self.http = None
