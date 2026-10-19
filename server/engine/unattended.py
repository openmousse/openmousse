"""没人在看的一轮（内核第 6 项）：定时任务和心跳到点自己跑的主人回合怎么跑、怎么认「没要说的」、结果怎么交回对话。

fresh()    在一条新会话里跑一轮主人回合，不带那个对话的聊天记录（Hermes 的 cron 每次都是新会话，OpenClaw 的 isolated 也是）。
           开头：light=False 和这个 Agent 平时的主人回合一样（engine/context.py 备料）；light=True 只放它是谁和规矩（OpenClaw 的
           lightContext：心跳默认这样，一次少发上万 token）。工具：这个 Agent 平时的那些，去掉 cron（Hermes：定时任务里不给 cronjob 工具，
           免得越建越多）；关卡照旧（撤不回的照样出卡，挂在这个 Agent 的对话里）。会话不落库（MemorySession，一轮一条），事件表照记，
           对话记成 cron:<任务> / heartbeat:<Agent>（不往 app 那个对话的实时广播里推）。
           看门狗：一直没有动静（不出字、不想、不开始也不做完工具）超过 idle 秒就停（Hermes 的 600 秒 inactivity watchdog）。
           模型是 claude-code/…（第 11 项）：交给本机用户自己登录的 Claude Code 跑一次（engine/claude_brain.once：一次性的新会话、
           工具和关卡同上），看门狗照旧。它跑不了（登录掉了、命令坏了、额度用完，还没出字、没调工具）：换备用链里的（同主对话）。
           模型是 chatgpt/…（第 11b 项，用户自己的 ChatGPT 套餐）：定时任务、心跳是后台用，用户在设置里同意过才花（models.may_spend，
           来路 auto）；没同意换成 models.outside() 挑的非套餐模型，没有就这一次记成出错，说清楚为什么。
in_chat()  照 chat.trigger 那条路在那个对话里跑（chat.start_run，origin auto，app 里一行灰字 + 回复）：接着这个对话当天的会话。
           那个对话正在回话就等它说完（最多 10 分钟）。推送按任务的档位，回完才推（回的要是「没要说的」就把这两条删掉、会话退回去、不推）。
silent()   「没要说的」：整段，或者第一行、最后一行，是 [SILENT] / NO_REPLY / 静默 这类记号；或者 HEARTBEAT_OK 去掉以后剩下不到 300 字
           （Hermes 的 is_autonomous_silence_response，OpenClaw 的 stripHeartbeatToken）。
post()     结果发进那个对话：一行灰字（【自动触发】…，app 画成 ⚙）+ 回复（origin auto），这一轮出的卡挂在回复下面，按档位推送；
           再往那个对话今天的会话里补一条 system（mirror：接下来那句话，模型知道刚才发过什么。Hermes 的 mirror_to_session 也是在一轮的
           边界上补：那个会话正在跑一轮就先等它跑完）。同一个来源连着补、中间没人说话的，只留最新一条。今天还没有会话就不补：
           建会话时会把 app 里今天的记录抄进去（engine/owner.py 的 seed）。

「没要说的」那几个记号改写自 Hermes 的 gateway/response_filters.py（LIVE_GATEWAY_SILENT_MARKERS、_strip_edge_silence_punctuation、
is_intentional_silence_response、is_autonomous_silence_response），NousResearch/hermes-agent commit 848fc0a，MIT，
Copyright (c) 2025 Nous Research；许可全文见仓库根目录的 THIRD_PARTY_NOTICES。HEARTBEAT_OK 的规矩照 OpenClaw 的 src/auto-reply/heartbeat.ts
（stripHeartbeatToken、DEFAULT_HEARTBEAT_ACK_MAX_CHARS = 300）自己写的。
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import re
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field

from config import settings
from i18n import L

from . import models
from .loop import Control, Result, Spec, run_turn, wait_idle
from .session import DbSession, MemorySession, day_key, system_note, time_stamp, user

CRON_TOOL = "cron"          # 定时任务的工具（engine/jobs.py）：没人在看的一轮里不给
NO_TOOLS = {CRON_TOOL, "tasks", "skill_manage", "ask_user", "todo", "enter_plan_mode", "propose_plan"}
# 后台任务（第 5 项）也不给：结果要交回派它的对话，这种一轮的对话是 cron:<任务> / heartbeat:<Agent>，没人接
# skill_manage（第 13 步，engine/skill_write.py）：没人在看的一轮不改自己的做法
# ⑫b 的问用户、待办、计划模式（tools.chat_tools 只给 app 的对话，for_agent 里本来就没有；写在这里防以后挪进去）：没人回答、没人点头
IDLE = 600                  # 看门狗：多久没动静就停（Hermes 的 HERMES_CRON_TIMEOUT 默认）
WAIT_FREE = 600             # in_chat：那个对话正在回话，最多等多久
MIRROR_WAIT = 120           # mirror：那个会话正在跑一轮，最多等多久（等不到也照补，repair 会把它挪到这一轮的工具结果后面）
MIRROR_MAX = 1500           # mirror 里放回复的多少字
AUTO_TRIGGER = "【自动触发】"  # app 画成一行灰字 ⚙（协议标记，不翻译；和 engine/context.py 的 AUTO_TRIGGER 一样）


# —— 「没要说的」 ——————————————————————————————————————————————————————————

SILENT_MARKERS = frozenset({"[SILENT]", "SILENT", "NO_REPLY", "NO REPLY", "[静默]", "静默", "[沉默]", "沉默"})
_BRACKETED = tuple(sorted(m for m in SILENT_MARKERS if m.startswith("[")))
_MARKER_CAP = 64
HEARTBEAT_OK = "HEARTBEAT_OK"
ACK_MAX = 300
_TRAILING_OK = re.compile(re.escape(HEARTBEAT_OK) + r"[^\w]{0,4}$")


def _edge_punct(ch: str) -> bool:
    return ch not in "[]" and unicodedata.category(ch).startswith("P")  # 方括号留着：「[SILENT」不能变成 SILENT


def _strip_edges(text: str) -> str:
    a, b = 0, len(text)
    while a < b and _edge_punct(text[a]):
        a += 1
    while b > a and _edge_punct(text[b - 1]):
        b -= 1
    return text[a:b].strip()


def _canon(text: str) -> str:
    return " ".join(text.strip().upper().split())


def exact_silence(text: str) -> bool:
    """整段就是一个记号（容许两边多几个标点：「.NO_REPLY」「*NO_REPLY*」）。提到 NO_REPLY 的正文不算。"""
    s = text.strip() if isinstance(text, str) else ""
    if not 0 < len(s) <= _MARKER_CAP:
        return False
    return any(_canon(f) in SILENT_MARKERS for f in {s, _strip_edges(s)})


def _heartbeat_ack(text: str) -> bool:
    """OpenClaw：HEARTBEAT_OK 在开头或结尾（裹着 ** 或标签也算），去掉以后剩下的不到 300 字 = 只是确认一下，不发。"""
    s = text.strip()
    if HEARTBEAT_OK not in s:
        return False
    s = re.sub(r"<[^>]*>", " ", s).replace("&nbsp;", " ").strip().strip("*`~_").strip()
    stripped = False
    while True:
        if s.startswith(HEARTBEAT_OK):
            s, stripped = s[len(HEARTBEAT_OK):].strip(), True
        elif _TRAILING_OK.search(s):
            i = s.rfind(HEARTBEAT_OK)
            s, stripped = (s[:i].rstrip() + s[i + len(HEARTBEAT_OK):].lstrip()).strip(), True
        else:
            break
    return stripped and len(" ".join(s.split())) <= ACK_MAX


def silent(text: str | None) -> bool:
    """没人在看的一轮回的是不是「没要说的」：整段 / 第一行 / 最后一行是记号，或者以 [SILENT] 开头（「[SILENT] 没有新的」），
    或者只是 HEARTBEAT_OK 式的确认。半句话里提到的不算（照常发）。"""
    s = (text or "").strip()
    if not s:
        return False
    lines = [ln for ln in s.splitlines() if ln.strip()]
    return s.upper().startswith(_BRACKETED) or any(exact_silence(c) for c in (s, lines[0], lines[-1])) or _heartbeat_ack(s)


# —— 一轮 ————————————————————————————————————————————————————————————————

@dataclass
class Outcome:
    status: str                    # done / silent / error / timeout / refused / busy
    text: str = ""
    turn: str | None = None
    model: str = ""
    error: str | None = None
    usage: dict = field(default_factory=dict)
    message: int | None = None     # 发进对话的那条回复（app 里的 id）
    served: str = ""               # 备用链换过模型时真正回答的那个（engine/fallback.py）；没换 = 空


def tools_for(agent: str) -> list:
    from . import tools as tools_mod
    return [t for t in tools_mod.for_agent(agent) if t.name not in NO_TOOLS]


LIGHT_NOTE = ("This is a light context for an unattended check: your workspace files, profile and background aren't loaded. "
              "Look things up with your tools when you need them (memory_search, the skill tool with no name lists your skills).")


async def system_for(agent: str, conversation: str, tools: list, light: bool) -> str:
    """开头：light 只有它是谁和规矩；否则和这个 Agent 平时的主人回合一样（备料按 conversation 那个对话取：上次聊到哪、项目卡……）。"""
    from . import context, owner
    if light:
        return "\n\n".join(x for x in [context.role_part(agent).text, owner.rules(agent, tools), LIGHT_NOTE] if x)
    import chat
    ctx = await context.gather(agent, conversation, chat.day_of(chat.now_iso()), {t.name for t in tools})
    return owner.system_for(agent, tools, ctx)


async def fresh(agent: str, conversation: str, key: str, prompt: str, *, model: str, light: bool = False, idle: float = IDLE) -> Outcome:
    """在新会话里跑一轮（见文件开头）。key = 事件表里记成哪个对话（cron:<任务>、heartbeat:<Agent>）。不抛：出的错记在 Outcome 里。"""
    from . import tools as tools_mod
    if models.subscription(model) == "chatgpt" and not models.may_spend(model, kind="owner", origin="auto"):  # 后台用 ChatGPT 套餐要先同意
        alt = models.outside(model)
        if not alt:
            return Outcome(status="error", model=model, error=L(
                "定时任务和心跳默认不花你的 ChatGPT 套餐：要用的话在设置里同意一次（后台任务可以用我的套餐），或者给它配一个用 API 密钥的模型",
                "Scheduled runs and heartbeats don't use your ChatGPT plan unless you allow it once in Settings (background tasks can use my "
                "plan), or give them a model with an API key"))
        model = alt
    try:
        models.parse_ref(model)
        tools = tools_for(agent)
        system = await system_for(agent, conversation, tools, light)
    except Exception as e:  # noqa: BLE001 — 配置不对、备料出错：这一次记成出错
        return Outcome(status="error", model=model, error=f"{type(e).__name__}: {str(e)[:200]}")
    spec = Spec(kind="owner", model=model, system=system, tools=tools, conversation=key, agent=agent, origin="auto", gate=tools_mod.gate,
                display="summarized", timeout=min(float(idle), 600.0), cache_ttl="1h")
    control = Control()
    asked = {**user(prompt), "at": time_stamp()}  # 和 app 里说的一句一样带上当地时间（session.with_time 放在这句话前面）
    last = [time.monotonic()]
    timed_out = [False]

    def on_event(_ev: dict) -> None:
        last[0] = time.monotonic()

    async def watchdog() -> None:
        while True:
            await asyncio.sleep(min(15.0, max(0.05, idle / 4)))
            if time.monotonic() - last[0] > idle:
                timed_out[0] = True
                control.abort()
                return

    dog = asyncio.create_task(watchdog())
    try:
        if models.subscription(model) == "claude-code":  # 用户自己的 Claude Code（第 11 项，engine/claude_brain.py）：他自己的定时任务和心跳能花他的套餐
            from . import claude_brain

            async def switch(chain: list[str], failed: Result) -> Result:
                """它跑不了（登录掉了、命令坏了、额度用完）：换上备用链里第一个跑这一次（Outcome.served 记下真正回答的那个）。"""
                res = await run_turn(dataclasses.replace(spec, model=chain[0], fallbacks=chain[1:]), MemorySession(), asked,
                                     on_event=on_event, control=control)
                res.models = [model, *res.models]
                return res
            res = await claude_brain.once(agent, key, system, tools, prompt, model=model, origin="auto", on_event=on_event, control=control,
                                          switch=switch)
        else:
            res = await run_turn(spec, MemorySession(), asked, on_event=on_event, control=control)
    finally:
        dog.cancel()
    if timed_out[0]:
        return Outcome(status="timeout", text=res.text, turn=res.turn, model=model, usage=res.usage,
                       error=L(f"{int(idle)} 秒没有动静，停了", f"No activity for {int(idle)}s; stopped"))
    if res.status == "error":
        return Outcome(status="error", text=res.text, turn=res.turn, model=model, usage=res.usage, error=res.error)
    if res.status == "refused" and not res.text.strip():
        return Outcome(status="refused", turn=res.turn, model=model, usage=res.usage, error="the model declined")
    if not res.text.strip():
        return Outcome(status="error", turn=res.turn, model=model, usage=res.usage, error=L("模型什么都没说", "The model said nothing"))
    served = res.model if res.models and res.model and res.model != res.models[0] else ""
    return Outcome(status="silent" if silent(res.text) else "done", text=res.text.strip(), turn=res.turn, model=model, usage=res.usage, served=served)


# —— 结果发进对话 ————————————————————————————————————————————————————————————

def thread_name(thread: str) -> str:
    if thread == "main":
        return settings.app_name
    try:
        import data
        return data.thread_names().get(thread) or thread
    except Exception:  # noqa: BLE001
        return thread


def thread_exists(thread: str) -> bool:
    import bridges
    if thread == "main" or bridges.known_agent(thread):
        return True
    try:
        import data
        return thread in data.thread_names()
    except Exception:  # noqa: BLE001
        return False


def marks() -> tuple[int, int]:
    """开跑前的记号：feed_items、inbox 的最大 rowid（回完看这之后这个对话新写的卡、新交的收件箱条目）。"""
    import chat
    return chat.feed_mark(), chat.max_rowid("inbox")


async def post(thread: str, label: str, gw: str, text: str, *, model: str, level: str, tag: str, note: str,
               feed_mark: int, inbox_mark: int, served: str = "") -> int:
    """发进对话、挂卡、补 mirror、推送（见文件开头）。→ 回复那条的 id。served：备用链换上的模型（记成这条的 model，要的那个记成
    requested，app 照 fallbackFrom 标出来）。"""
    import chat
    import inbox
    import push
    ts = chat.now_iso()
    with chat._lock, chat.db() as conn:
        aid = conn.execute("INSERT INTO messages(thread, role, text, model, ts, gw_text, origin) VALUES(?,?,?,?,?,?,?)",
                           (thread, "auto", AUTO_TRIGGER + label, None, ts, gw, "auto")).lastrowid
        rid = conn.execute("INSERT INTO messages(thread, role, text, model, requested, ts, status, origin) VALUES(?,?,?,?,?,?,?,?)",
                           (thread, "grava", text, served or model, model, chat.now_iso(), "ok", "auto")).lastrowid
        conn.execute("INSERT INTO threads(id, updated_at) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at", (thread, ts))
    try:
        inbox.link_message(thread, inbox_mark, rid)
    except Exception:  # noqa: BLE001 — 挂不上只是对话里少一张卡
        pass
    try:
        await mirror(thread, note, tag)
    except Exception as e:  # noqa: BLE001 — 补不上：模型下一句不知道刚才发过什么，回复照样在
        print(f"[engine] {thread}：mirror 没补上：{type(e).__name__}: {e}")
    run = chat.Run(thread=thread, model=served or model, user_id=aid, started=ts, text=text, done=True, status="ok", reply_id=rid, origin="auto",
                   level=level, feed_mark=feed_mark, finished=chat.now_iso(), requested=model)
    await push.notify_run(run)
    return rid


async def mirror(thread: str, note: str, tag: str) -> bool:
    """往 thread 今天的会话里补一条 system（带 mirror = 来源）。上一条就是同一个来源补的（中间没人说话）：换掉它。今天还没会话 → False。"""
    import chat
    s = DbSession.latest(day_key(thread, chat.day_of(chat.now_iso())))
    if s is None:
        return False
    await wait_idle(s.id, MIRROR_WAIT)
    path = s.path()
    if path and path[-1]["kind"] == "message" and path[-1]["role"] == "system" and json.loads(path[-1]["data"]).get("mirror") == tag:
        s.set_leaf(path[-1]["parent"])
    s.append({**system_note(note), "mirror": tag})
    return True


def mirror_note(head: str, text: str) -> str:
    body = text if len(text) <= MIRROR_MAX else text[:MIRROR_MAX] + " […]"
    return f"{head}\n\n{body}\n\n(The user can see this message in the chat; what they say next may be a reply to it.)"


# —— 在对话里跑（chat 模式）——————————————————————————————————————————————————————

async def in_chat(thread: str, label: str, instructions: str, *, level: str, idle: float = IDLE, model: str | None = None,
                  on_start: Callable[[int], None] | None = None) -> Outcome:
    """照 chat.trigger 那条路在 thread 里跑一轮（见文件开头）。on_start(灰字那条的 id)：一开跑就告诉调用方（这一轮里认得出是谁）。"""
    import chat
    import push
    from fastapi import HTTPException
    deadline = time.time() + WAIT_FREE
    run = None
    while run is None:
        if not chat.busy(thread) and not chat.QUEUED.get(thread):
            try:
                run = chat.start_run(thread, AUTO_TRIGGER + label, model, origin="auto", context=instructions, level="none")
                break
            except HTTPException as e:
                if e.status_code != 409:
                    return Outcome(status="error", error=str(e.detail)[:300])
        if time.time() > deadline:
            return Outcome(status="busy", error=L("那个对话一直在回话，这次没跑", "That chat stayed busy, so this run was skipped"))
        await asyncio.sleep(1)
    if on_start is not None:
        on_start(run.user_id)
    seen, last, timed_out = None, time.monotonic(), False
    while not run.done:
        await asyncio.sleep(0.2)
        now_seen = (len(run.text), json.dumps(run.progress, sort_keys=True, default=str) if run.progress else "")
        queued = (run.progress or {}).get("phase") == "queued"  # 排着等 Claude Code 的位子（claude_brain._acquire）：不算没动静
        if now_seen != seen or queued:
            seen, last = now_seen, time.monotonic()
        elif time.monotonic() - last > idle and not timed_out:
            timed_out = True
            run.watchdog = True  # 不是用户点的停（engine/loop.note_stopped 不补「用户停了」）
            await chat.stop(chat.StopBody(thread=thread))
    model = run.model or ""
    turn = {"turn": run.turn, "usage": run.usage or {}}  # 内核记的这一轮和用量（chat.engine_stream 记在 Run 上）：运行记录带上
    if timed_out:
        return Outcome(status="timeout", text=run.text, model=model, message=run.reply_id,
                       error=L(f"{int(idle)} 秒没有动静，停了", f"No activity for {int(idle)}s; stopped"), **turn)
    if run.status == "ok" and silent(run.text):
        drop(thread, run)
        return Outcome(status="silent", text=run.text, model=model, **turn)
    run.level = level
    await push.notify_run(run)  # 起的时候档位是 none（回的要是「没要说的」就不推）：现在按任务的档位推
    if run.status != "ok":
        return Outcome(status="error", text=run.text, model=model, message=run.reply_id, error=run.error or run.status, **turn)
    return Outcome(status="done", text=run.text, model=model, message=run.reply_id, **turn)


def drop(thread: str, run) -> None:
    """chat 模式回了「没要说的」：app 里那两条删掉；会话里这一轮要是最后一轮，退回它之前（后面已经有人接着说了就不动）。"""
    import chat
    with chat._lock, chat.db() as conn:
        conn.execute("DELETE FROM messages WHERE thread=? AND id IN (?, ?)", (thread, run.user_id, run.reply_id or -1))
    s = DbSession.latest(day_key(thread, chat.day_of(run.started)))
    if s is None:
        return
    path = s.path()
    at = next((i for i, r in enumerate(path) if r["kind"] == "message" and r["role"] == "user" and json.loads(r["data"]).get("ref") == run.user_id),
              None)
    if at is None or any(r["kind"] == "message" and r["role"] == "user" for r in path[at + 1:]):
        return
    s.set_leaf(path[at]["parent"])
