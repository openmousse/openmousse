"""Telegram（第 6 批，10.2）：用 httpx 直接调 Bot API（core.telegram.org/bots/api），不加依赖。

收：长轮询 getUpdates（limit 100，allowed_updates 只要 message / edited_message / callback_query / my_chat_member）。第一次 timeout 0
  （先确认这个令牌现在归我们），之后每次等 poll_timeout 秒（默认 25）。这一批先落库（hub.ingest，去重、和 offset 写在同一个事务里），
  落进去了才把 offset 挪到最后一个 update_id + 1；落不进去就不挪，下次还会收到这一批（见过的去重掉）。offset 跟着机器人的 id 记：
  换了令牌、换了机器人就从头来。
  409（同一个令牌别处在 getUpdates，或者设了 webhook）：停下、状态里写清楚、隔 30 秒、60 秒……最多 10 分钟再试，不跟它抢，也不替别人
  删 webhook。401 / 404（令牌不对）：状态写清楚，10 分钟后再看（在设置里换了令牌会重起）。429 照 retry_after 等；连不上按 1、2、4…30 秒退避。
认：私聊、群（group / supergroup；要 @ 机器人、回复机器人、或叫 Agent 的名字，见 router）、论坛话题（按话题分会话：话题 id 没写的是 General = 1）。
  @ 机器人按消息的 entities 认（offset、length 是 UTF-16 的），mention 对上 @用户名、text_mention 对上机器人的 id、/命令@机器人；
  没有 entities 才在字里找。回复的是机器人自己发的那条也算叫它（话题的建立消息不算）。/命令@别的机器人 的不理。
  别的机器人发的默认不理（router）；改过的消息不理（原来那条已经处理过了）。频道、机器人被拉进群（my_chat_member）只记下来。
  名字、用户名、群名、转发自谁、引用的那条、位置、名片……都放进 meta（不可信，见 owner.envelope）。
  媒体：照片取最大的那一张、语音、音频、视频、文件、贴纸：只记占位（file_id、大小），主人的才在处理时下载（getFile，最多 20 MB）。
发：Markdown 转成 Telegram 的 HTML（format.telegram_html），一段最多 3800 字（UTF-16，4096 留余量）；Telegram 说 can't parse entities
  （或太长）就退回纯文字再发一次。群里回叫它的那一条（reply_parameters，原消息没了也照发）；话题里带 message_thread_id（General 不带）；
  不展开链接预览；按钮（inline keyboard）只在最后一段。429 交给出去的队列照 retry_after 等；被拉黑、聊天不在了是永久的错（进死信）。
  正在输入：sendChatAction 每 4 秒一次。/start、/stop 登记进命令菜单（setMyCommands）。
webhook 模式这一版没做（要公网）：只有长轮询。
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from datetime import datetime, timezone

import httpx

from i18n import L

from . import format as fmt
from . import store
from .base import Channel, ChannelError, Inbound, Outbound

log = logging.getLogger("mousse.channels")
API = "https://api.telegram.org"
POLL = 25                   # 长轮询每次等几秒
ALLOWED = ["message", "edited_message", "callback_query", "my_chat_member"]
CONFLICT_WAIT = (30, 60, 120, 300, 600)
AUTH_WAIT = 600
FILE_MAX = 20 * 1024 * 1024  # Bot API 能下载的上限
GENERAL = "1"                # 论坛的 General 话题
INGEST_WAIT = 1.0            # 落库失败（库被锁之类）：隔几秒再收同一批


def _u16_slice(text: str, offset: int, length: int) -> str:
    b = text.encode("utf-16-le")
    return b[2 * offset: 2 * (offset + length)].decode("utf-16-le", "replace")


def _name(user: dict | None) -> str:
    if not isinstance(user, dict):
        return ""
    return " ".join(x for x in (str(user.get("first_name") or ""), str(user.get("last_name") or "")) if x).strip() or str(user.get("username") or "")


class Telegram(Channel):
    kind = "telegram"
    label = "Telegram"
    max_len = 3800
    buttons = True
    edits = True
    typing_every = 4.0
    formats = ("html", "plain")
    groups = True
    min_gap = 1.0

    def __init__(self, acc, hub) -> None:
        super().__init__(acc, hub)
        self.base = str(acc.get("api_base") or API).rstrip("/")
        self.client: httpx.AsyncClient | None = None
        self.poll = int(acc.get("poll_timeout") or POLL)

    # —— 调 Bot API ——
    def token(self) -> str:
        return self.acc.secret(self.acc.get("token"))

    def _client(self) -> httpx.AsyncClient:
        if self.client is None:
            self.client = httpx.AsyncClient(timeout=httpx.Timeout(30, connect=10))
        return self.client

    async def call(self, method: str, payload: dict | None = None, *, timeout: float = 30) -> object:
        """调一个方法 → result。出错抛 ChannelError（话里不带令牌）。"""
        tok = self.token()
        if not tok:
            raise ChannelError(L("没有机器人的令牌（设置里存一个）", "No bot token (save one in settings)"), permanent=True, kind="auth")
        try:
            r = await self._client().post(f"{self.base}/bot{tok}/{method}", json=payload or {}, timeout=timeout)
        except httpx.HTTPError as e:
            raise ChannelError(f"{method}: {type(e).__name__}: {str(e).replace(tok, '<token>')[:160]}", kind="network") from None
        try:
            j = r.json()
        except ValueError:
            raise ChannelError(f"{method}: HTTP {r.status_code}", kind="network" if r.status_code >= 500 else "bad",
                               permanent=r.status_code < 500 and r.status_code != 429) from None
        if isinstance(j, dict) and j.get("ok"):
            return j.get("result")
        j = j if isinstance(j, dict) else {}
        code = int(j.get("error_code") or r.status_code or 0)
        desc = str(j.get("description") or f"HTTP {code}").replace(tok, "<token>")[:300]
        params = j.get("parameters") if isinstance(j.get("parameters"), dict) else {}
        if code == 429:
            raise ChannelError(desc, retry_after=float(params.get("retry_after") or 1), kind="rate")
        if code == 409:
            raise ChannelError(desc, kind="conflict")
        if code in (401, 404) and method in ("getMe", "getUpdates"):
            raise ChannelError(desc, permanent=True, kind="auth")
        if code == 401:
            raise ChannelError(desc, permanent=True, kind="auth")
        if code == 403:
            raise ChannelError(desc, permanent=True, kind="forbidden")
        if code == 400 and fmt.PARSE_ERROR.search(desc):
            raise ChannelError(desc, kind="format")
        if code == 400 and re.search(r"message is too long|text is too long", desc, re.I):
            raise ChannelError(desc, kind="format")
        if code == 400 and re.search(r"thread not found", desc, re.I):
            raise ChannelError(desc, kind="thread")
        if code == 400 and re.search(r"message is not modified", desc, re.I):
            raise ChannelError(desc, kind="same")
        if code >= 500:
            raise ChannelError(desc, kind="network")
        raise ChannelError(desc, permanent=True, kind="bad")

    # —— 收 ——
    async def hello(self) -> dict:
        """getMe：认出自己（id、用户名），换了机器人就把 offset 清掉。登记 /start、/stop。"""
        me = await self.call("getMe")
        if not isinstance(me, dict) or not me.get("id"):
            raise ChannelError("getMe: unexpected reply", kind="network")
        self.me = {"id": str(me["id"]), "username": str(me.get("username") or ""), "name": _name(me)}
        kind, account = self.acc.kind, self.acc.id
        if store.state(kind, account, "bot_id") != self.me["id"]:
            store.set_state(kind, account, bot_id=self.me["id"], offset="0")
        store.set_state(kind, account, bot=json.dumps(self.me, ensure_ascii=False), bot_username=self.me["username"])
        try:
            await self.call("setMyCommands", {"commands": [
                {"command": "start", "description": L("开始；认主人：/start <码>", "Start; to claim the bot as its owner: /start <code>")},
                {"command": "stop", "description": L("停下正在回的这一轮", "Stop the reply in progress")}]})
        except ChannelError as e:
            log.info("telegram setMyCommands: %s", e)
        return self.me

    async def run(self) -> None:
        kind, account = self.acc.kind, self.acc.id
        backoff, conflicts = 1.0, 0
        while True:
            try:
                await self.hello()
                break
            except ChannelError as e:
                if e.kind == "auth":
                    self.hub.note("auth", L("令牌不对（Telegram 说 401 / 404）：在设置里重新存一个", "The token was rejected (401 / 404); save a new one in settings"),
                                  error=str(e))
                    await asyncio.sleep(AUTH_WAIT)
                else:
                    self.hub.note("error", L("连不上 Telegram", "Can't reach Telegram"), error=str(e))
                    await asyncio.sleep(min(backoff, 30))
                    backoff *= 2
        first, backoff = True, 1.0
        while True:
            offset = int(store.state(kind, account, "offset") or 0)
            body = {"timeout": 0 if first else self.poll, "limit": 100, "allowed_updates": ALLOWED}
            if offset:
                body["offset"] = offset
            try:
                updates = await self.call("getUpdates", body, timeout=self.poll + 15)
            except ChannelError as e:
                if e.kind == "conflict":
                    wait = CONFLICT_WAIT[min(conflicts, len(CONFLICT_WAIT) - 1)] * (1 + random.random() * 0.2)
                    conflicts += 1
                    self.hub.note("conflict", L(f"别处在用同一个令牌收消息（或者设了 webhook）：停下了，{int(wait)} 秒后再试，不跟它抢。"
                                                "停掉另一个，或者给这里换一个机器人。",
                                                f"Something else is reading updates with this token (another poller or a webhook). Paused; "
                                                f"trying again in {int(wait)}s without fighting it. Stop the other one, or give this account "
                                                "its own bot."), error=str(e))
                    first = True
                    await asyncio.sleep(wait)
                elif e.kind == "auth":
                    self.hub.note("auth", L("令牌不对（Telegram 说 401 / 404）：在设置里重新存一个", "The token was rejected (401 / 404); save a new one in settings"),
                                  error=str(e))
                    await asyncio.sleep(AUTH_WAIT)
                elif e.kind == "rate":
                    await asyncio.sleep(e.retry_after or 1)
                else:
                    self.hub.note("error", L("连 Telegram 出错了，过一会儿再试", "Trouble reaching Telegram; retrying"), error=str(e))
                    await asyncio.sleep(min(backoff, 30))
                    backoff *= 2
                continue
            first, backoff, conflicts = False, 1.0, 0
            if self.hub.status.get("state") != "ok":
                self.hub.note("ok", L(f"在收 @{self.me.get('username')} 的消息", f"Receiving for @{self.me.get('username')}"))
            if not isinstance(updates, list) or not updates:
                continue
            ids = [u.get("update_id") for u in updates if isinstance(u, dict) and isinstance(u.get("update_id"), int)]
            if not ids:
                continue
            items = [ib for u in updates if isinstance(u, dict) and (ib := self.normalize(u)) is not None]
            try:
                self.hub.ingest(items, {"offset": str(max(ids) + 1)})  # 落进去了才往前挪
            except Exception as e:  # noqa: BLE001 — 库写不进去：offset 不挪，下一圈再收到这一批（见过的去重掉）
                self.hub.note("error", L("收到的消息存不进库，过一会儿再收", "Couldn't store incoming messages; retrying"), error=f"{type(e).__name__}: {e}")
                await asyncio.sleep(INGEST_WAIT)

    def normalize(self, u: dict) -> Inbound | None:
        """一个 update → Inbound（不认的类型 → None，只挪 offset）。"""
        uid = str(u.get("update_id"))
        acc = self.acc
        if isinstance(u.get("callback_query"), dict):
            cq = u["callback_query"]
            frm = cq.get("from") or {}
            msg = cq.get("message") if isinstance(cq.get("message"), dict) else {}
            chat = msg.get("chat") or {}
            return Inbound(channel=acc.kind, account=acc.id, ext_id=uid, sender=str(frm.get("id") or ""), chat=str(chat.get("id") or frm.get("id") or ""),
                           chat_type="dm" if chat.get("type", "private") == "private" else "group", topic=self._topic(msg, chat),
                           is_bot=bool(frm.get("is_bot")), callback={"id": str(cq.get("id") or ""), "data": str(cq.get("data") or ""),
                                                                     "message_id": str(msg.get("message_id") or "")},
                           meta={"name": _name(frm), "username": frm.get("username") or ""}, ts=self._ts(msg))
        if isinstance(u.get("my_chat_member"), dict):
            m = u["my_chat_member"]
            chat = m.get("chat") or {}
            return Inbound(channel=acc.kind, account=acc.id, ext_id=uid, sender=str((m.get("from") or {}).get("id") or ""), chat=str(chat.get("id") or ""),
                           chat_type="group", skip=f"member:{(m.get('new_chat_member') or {}).get('status') or ''}",
                           meta={"chat_title": chat.get("title") or ""})
        edited = "edited_message" in u
        msg = u.get("edited_message") if edited else u.get("message")
        if not isinstance(msg, dict):
            return None
        chat = msg.get("chat") or {}
        ctype = chat.get("type") or "private"
        if ctype == "channel":
            return None
        frm = msg.get("from") or {}
        text = str(msg.get("text") or msg.get("caption") or "")
        entities = msg.get("entities") or msg.get("caption_entities") or []
        me_id, me_name = str(self.me.get("id") or ""), str(self.me.get("username") or "").lower()
        mentioned = False
        for e in entities if isinstance(entities, list) else []:
            if not isinstance(e, dict):
                continue
            span = _u16_slice(text, int(e.get("offset") or 0), int(e.get("length") or 0)).lower()
            if (e.get("type") == "mention" and me_name and span == f"@{me_name}") or \
                    (e.get("type") == "text_mention" and str((e.get("user") or {}).get("id")) == me_id) or \
                    (e.get("type") == "bot_command" and me_name and span.endswith(f"@{me_name}")):
                mentioned = True
        if not entities and me_name and re.search(rf"(?<![\w@])@{re.escape(me_name)}(?!\w)", text, re.I):
            mentioned = True
        fo = msg.get("forward_origin") if isinstance(msg.get("forward_origin"), dict) else None
        relayed = bool(fo or isinstance(msg.get("via_bot"), dict))  # 转发来的、经别的机器人发的：字是别人写的
        mentioned = mentioned and not relayed  # 转发的那条里 @ 了机器人不算叫它
        command, args, skip = "", "", ""
        if text.startswith("/") and not relayed:  # 别人写的「/…」不当命令跑
            head, _, rest = text.partition(" ")
            cmd, _, target = head[1:].partition("@")
            if target and me_name and target.lower() != me_name:
                skip = "other_bot_command"
            elif re.fullmatch(r"[A-Za-z0-9_]{1,32}", cmd or ""):
                command, args = cmd.lower(), rest.strip()
                text = f"/{command} {args}".strip()  # /cmd@机器人 参数 → /cmd 参数（只去掉命令上的 @）
        if me_name and mentioned and not command:
            text = re.sub(rf"(?i)@{re.escape(me_name)}\b[,:\-]*\s*", "", text).strip() or text
        rt = msg.get("reply_to_message") if isinstance(msg.get("reply_to_message"), dict) else None
        reply_to_bot = bool(rt and str((rt.get("from") or {}).get("id")) == me_id and me_id and not rt.get("forum_topic_created"))
        meta: dict = {"name": _name(frm), "username": frm.get("username") or "", "language": frm.get("language_code") or ""}
        if ctype != "private":
            meta["chat_title"] = chat.get("title") or ""
        if relayed:  # 原文只当资料：主人回合里正文换成一句说明、原文进不可信的那一块（owner.prepare / envelope）
            meta["forwarded"] = text[:4000] or "(forwarded message)"
        if fo:
            meta["forward_from"] = _name(fo.get("sender_user")) or fo.get("sender_user_name") or (fo.get("chat") or {}).get("title") or \
                (fo.get("sender_chat") or {}).get("title") or fo.get("type") or ""
        if rt and not rt.get("forum_topic_created"):
            meta["reply_to_from"] = _name(rt.get("from"))
            if not reply_to_bot:
                meta["reply_to_text"] = str(rt.get("text") or rt.get("caption") or "")[:1000]
        if isinstance(msg.get("quote"), dict):
            meta["quote"] = str(msg["quote"].get("text") or "")[:1000]
        if isinstance(msg.get("location"), dict):
            meta["location"] = {k: msg["location"].get(k) for k in ("latitude", "longitude") if k in msg["location"]}
        if isinstance(msg.get("venue"), dict):
            meta["venue"] = {k: msg["venue"].get(k) for k in ("title", "address") if k in msg["venue"]}
        if isinstance(msg.get("contact"), dict):
            meta["contact"] = {"name": _name(msg["contact"]), "phone": str(msg["contact"].get("phone_number") or "")}
        if isinstance(msg.get("via_bot"), dict):
            meta["via_bot"] = msg["via_bot"].get("username") or ""
        return Inbound(channel=acc.kind, account=acc.id, ext_id=uid, sender=str(frm.get("id") or ""), chat=str(chat.get("id") or ""),
                       chat_type="dm" if ctype == "private" else "group", topic=self._topic(msg, chat), message_id=str(msg.get("message_id") or ""),
                       reply_to=str(rt.get("message_id")) if rt else "", reply_to_bot=reply_to_bot, ts=self._ts(msg), text=text,
                       media=self._media(msg), meta={k: v for k, v in meta.items() if v not in ("", None, {}, [])}, mentioned=mentioned,
                       is_bot=bool(frm.get("is_bot")), edited=edited, command=command, args=args, skip=skip)

    @staticmethod
    def _topic(msg: dict, chat: dict) -> str:
        """论坛话题（按话题分会话）：论坛里没写话题的是 General（1）；普通群里回复带的 thread id 不算话题。"""
        if chat.get("is_forum") or msg.get("is_topic_message"):
            return str(msg.get("message_thread_id") or GENERAL)
        return ""

    @staticmethod
    def _ts(msg: dict) -> str:
        try:
            return datetime.fromtimestamp(int(msg.get("date") or 0), timezone.utc).isoformat(timespec="seconds")
        except (TypeError, ValueError, OSError):
            return ""

    @staticmethod
    def _media(msg: dict) -> list[dict]:
        out = []
        if isinstance(msg.get("photo"), list) and msg["photo"]:
            p = max((x for x in msg["photo"] if isinstance(x, dict)), key=lambda x: (x.get("file_size") or 0, x.get("width") or 0), default=None)
            if p:
                out.append({"kind": "image", "name": "photo.jpg", "mime": "image/jpeg", "size": p.get("file_size"), "ref": p.get("file_id")})
        for key, kind, name, mime in (("voice", "voice", "voice.ogg", "audio/ogg"), ("audio", "audio", "audio", "audio/mpeg"),
                                      ("video", "video", "video.mp4", "video/mp4"), ("video_note", "video", "video_note.mp4", "video/mp4"),
                                      ("document", "file", "file", "application/octet-stream"), ("animation", "video", "animation.mp4", "video/mp4"),
                                      ("sticker", "sticker", "sticker.webp", "image/webp")):
            m = msg.get(key)
            if isinstance(m, dict):
                out.append({"kind": kind, "name": str(m.get("file_name") or name), "mime": str(m.get("mime_type") or mime), "size": m.get("file_size"),
                            "ref": m.get("file_id"), **({"emoji": m.get("emoji")} if key == "sticker" and m.get("emoji") else {})})
        return out

    # —— 发 ——
    def reply_ref(self, ib: Inbound) -> str:
        return ib.message_id if ib.chat_type == "group" else ""

    def _target(self, payload: dict, chat: str, topic: str) -> dict:
        payload["chat_id"] = int(chat) if re.fullmatch(r"-?\d+", str(chat)) else chat
        if topic and topic != GENERAL:
            payload["message_thread_id"] = int(topic) if str(topic).isdigit() else topic
        return payload

    async def send(self, out: Outbound) -> list[str]:
        if out.edit:
            try:
                await self.edit(out.chat, out.edit, out.text, topic=out.topic, buttons=out.buttons, fmt_=out.format)
                return [out.edit]
            except ChannelError as e:
                if e.kind == "rate":
                    raise
                # 草稿改不动了（太老、被删）：照常发一条新的
        payload = self._target({"link_preview_options": {"is_disabled": True}}, out.chat, out.topic)
        if out.format == "markdown":
            payload.update(text=fmt.telegram_html(out.text), parse_mode="HTML")
        elif out.format == "html":
            payload.update(text=out.text, parse_mode="HTML")
        else:
            payload["text"] = out.text
        if out.reply_to:
            payload["reply_parameters"] = {"message_id": int(out.reply_to) if str(out.reply_to).isdigit() else out.reply_to,
                                           "allow_sending_without_reply": True}
        if out.buttons:
            payload["reply_markup"] = {"inline_keyboard": [[{"text": b["text"], "callback_data": b["data"]} for b in row] for row in out.buttons]}
        try:
            res = await self._send(payload)
        except ChannelError as e:
            if e.kind != "format" or "parse_mode" not in payload:
                raise
            payload.pop("parse_mode", None)  # Telegram 不认这段 HTML（或太长）：退回纯文字再发一次
            payload["text"] = fmt.plain(out.text) if out.format == "markdown" else re.sub(r"<[^>]+>", "", out.text)
            res = await self._send(payload)
        return [str((res or {}).get("message_id") or "")]

    async def _send(self, payload: dict) -> dict:
        try:
            return await self.call("sendMessage", payload)  # type: ignore[return-value]
        except ChannelError as e:
            if e.kind != "thread" or "message_thread_id" not in payload:
                raise
            payload.pop("message_thread_id", None)  # 话题没了：发到群里
            return await self.call("sendMessage", payload)  # type: ignore[return-value]

    async def edit(self, chat: str, message_id: str, text: str, *, topic: str = "", buttons: list | None = None, fmt_: str = "plain") -> None:
        payload: dict = {"chat_id": int(chat) if re.fullmatch(r"-?\d+", str(chat)) else chat, "message_id": int(message_id)}
        if fmt_ == "markdown":
            payload.update(text=fmt.telegram_html(text), parse_mode="HTML")
        else:
            payload["text"] = text
        payload["reply_markup"] = {"inline_keyboard": [[{"text": b["text"], "callback_data": b["data"]} for b in row] for row in buttons or []]}
        try:
            await self.call("editMessageText", payload)
        except ChannelError as e:
            if e.kind == "same":
                return
            if e.kind == "format" and "parse_mode" in payload:
                payload.pop("parse_mode", None)
                payload["text"] = fmt.plain(text)
                await self.call("editMessageText", payload)
                return
            raise

    async def typing(self, chat: str, topic: str = "") -> None:
        payload = {"action": "typing"}
        self._target(payload, chat, "")
        if topic:
            payload["message_thread_id"] = int(topic) if str(topic).isdigit() else topic
        try:
            await self.call("sendChatAction", payload, timeout=10)
        except ChannelError:
            pass

    async def answer_callback(self, cb: dict, text: str = "") -> None:
        try:
            await self.call("answerCallbackQuery", {"callback_query_id": cb.get("id"), **({"text": text[:190]} if text else {})}, timeout=10)
        except ChannelError:
            pass

    async def fetch(self, item: dict) -> bytes:
        """主人发来的一个媒体：getFile → 下载（最多 20 MB）。"""
        fid = str(item.get("ref") or "")
        if not fid:
            raise ChannelError("no file id", permanent=True)
        if item.get("size") and int(item["size"]) > FILE_MAX:
            raise ChannelError(L("超过 20 MB（Telegram 机器人只能下载 20 MB 以内的）", "Over 20 MB (bots can only download up to 20 MB)"), permanent=True)
        info = await self.call("getFile", {"file_id": fid})
        path = str((info or {}).get("file_path") or "") if isinstance(info, dict) else ""
        if not path:
            raise ChannelError("getFile: no file_path", permanent=True)
        tok = self.token()
        try:
            async with self._client().stream("GET", f"{self.base}/file/bot{tok}/{path}", timeout=120) as r:
                if r.status_code != 200:
                    raise ChannelError(f"download: HTTP {r.status_code}")
                buf = bytearray()
                async for chunk in r.aiter_bytes():
                    buf += chunk
                    if len(buf) > FILE_MAX:
                        raise ChannelError(L("超过 20 MB", "Over 20 MB"), permanent=True)
                return bytes(buf)
        except httpx.HTTPError as e:
            raise ChannelError(f"download: {type(e).__name__}", kind="network") from None

    async def test(self) -> dict:
        me = await self.call("getMe")
        if not isinstance(me, dict):
            raise ChannelError("getMe: unexpected reply")
        out = {"detail": f"@{me.get('username')}", "bot": {"id": me.get("id"), "username": me.get("username"), "name": _name(me)}}
        try:
            wh = await self.call("getWebhookInfo")
            if isinstance(wh, dict) and wh.get("url"):
                out["warning"] = L("这个令牌设了 webhook（别处在用）：这里的长轮询会一直 409。先在那边关掉，或者换一个机器人。",
                                   "This token has a webhook set (used elsewhere), so polling here would keep getting 409. Turn it off there, or use another bot.")
        except ChannelError:
            pass
        return out

    async def close(self) -> None:
        if self.client is not None:
            await self.client.aclose()
            self.client = None
