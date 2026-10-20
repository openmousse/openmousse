"""渠道跑起来的样子（第 6 批）：每个开着的账号一个 Runner，里面三样一直转：
  收      渠道自己的 run()（Telegram 长轮询、IMAP 轮询）：收到的交给 ingest，落库以后渠道才往前挪 offset / 水位
  处理    按聊天（lane）串行：同一个聊天一条处理完再下一条，不同聊天同时（最多 LANES_MAX 个）；router.process 管一条怎么办
  发      出去的队列（outbox.pump）：按聊天的先后、限频、重试、死信
只在 claw 选 builtin 时起（main.py 的 lifespan 调 start）；server.json 没写 channels、渠道没开、账号关了的都不起。
设置里改了账号（/api/setup/channels…）调 restart 重起那一个。
"""
from __future__ import annotations

import asyncio
import importlib
import logging
import time
from collections.abc import Callable

from chat import now_iso

from . import accounts, store
from .base import Account, Channel

log = logging.getLogger("mousse.channels")
LANES_MAX = 8          # 一个账号同时处理几个聊天
TICK = 2.0             # 没人叫醒时多久看一次库（命令行批了配对、重试到点了……）
CRASH_WAIT = 30.0      # 收的那一段意外出错退出了：隔多久重起
KINDS: dict[str, Callable[[], type[Channel]]] = {
    "telegram": lambda: importlib.import_module("channels.telegram").Telegram,
    "email": lambda: importlib.import_module("channels.email").Email,
    "discord": lambda: importlib.import_module("channels.discord").Discord,
}
RUNNERS: dict[tuple[str, str], Runner] = {}
_bg: set[asyncio.Task] = set()


def channel_class(kind: str) -> type[Channel] | None:
    f = KINDS.get(kind)
    return f() if f else None


def spawn(coro) -> asyncio.Task:
    t = asyncio.get_running_loop().create_task(coro)
    _bg.add(t)
    t.add_done_callback(_bg.discard)
    return t


class Runner:
    def __init__(self, acc: Account, cls: type[Channel]) -> None:
        self.acc = acc
        self.ch = cls(acc, self)
        self.tasks: list[asyncio.Task] = []
        self.lanes: dict[str, asyncio.Task] = {}
        self.wake_in = asyncio.Event()
        self.wake_out = asyncio.Event()
        self.sem = asyncio.Semaphore(LANES_MAX)
        self.status: dict = {"state": "starting", "detail": "", "since": now_iso()}
        self.stopped = False
        self.last_sent: dict[str, float] = {}   # 聊天 → 上一条发出去的时间（限频）

    @property
    def key(self) -> tuple[str, str]:
        return self.acc.kind, self.acc.id

    # —— 状态 ——
    def note(self, state: str, detail: str = "", *, error: str | None = None) -> None:
        """渠道报一下现在怎么样：ok / conflict（别处在用同一个令牌）/ auth（令牌、密码不对）/ error / waiting …"""
        if state != self.status.get("state") or detail != self.status.get("detail"):
            self.status.update(state=state, detail=detail, since=now_iso())
        kv = {"state": state, "detail": detail}
        if error:
            self.status.update(error=error, error_at=now_iso())
            kv.update(error=error, error_at=now_iso())
        try:
            store.set_state(*self.key, **kv)
        except Exception:  # noqa: BLE001 — 记不下状态不影响收发
            pass

    # —— 收 ——
    def ingest(self, items: list, state_kv: dict | None = None) -> list[int]:
        """渠道收到一批（Inbound 的列表）：落库（state_kv 同一个事务里写）→ 叫醒处理。写不进去抛（渠道别往前挪）。"""
        kv = dict(state_kv or {})
        if items:
            kv["last_in"] = now_iso()
        ids = store.ingest(self.acc.kind, self.acc.id, items, kv or None)
        if ids:
            self.status["last_in"] = kv.get("last_in")
            self.wake_in.set()
        return ids

    async def _recv(self) -> None:
        while not self.stopped:
            try:
                await self.ch.run()
                return
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — 渠道自己的 run 出了没接住的错：记下、隔一会儿重起
                log.exception("channel %s/%s crashed", *self.key)
                self.note("error", f"{type(e).__name__}", error=str(e)[:300])
                await asyncio.sleep(CRASH_WAIT)

    # —— 处理 ——
    async def _work(self) -> None:
        from . import router
        while not self.stopped:
            try:
                for lane in store.lanes(*self.key):
                    t = self.lanes.get(lane)
                    if t is None or t.done():
                        self.lanes[lane] = asyncio.get_running_loop().create_task(self._drain(lane, router))
            except Exception:  # noqa: BLE001 — 读库出错：下一轮再看
                log.exception("channel %s/%s dispatch", *self.key)
            try:
                await asyncio.wait_for(self.wake_in.wait(), TICK)
            except asyncio.TimeoutError:
                pass
            self.wake_in.clear()

    async def _drain(self, lane: str, router) -> None:
        async with self.sem:
            while not self.stopped:
                row = store.head(*self.key, lane)
                if row is None or row["next_at"] > time.time():
                    return
                if not store.take(row["id"]):
                    return
                await router.process(self, row)

    # —— 发 ——
    async def _send(self) -> None:
        from . import outbox
        while not self.stopped:
            wait = None
            try:
                wait = await outbox.pump(self)
            except Exception:  # noqa: BLE001
                log.exception("channel %s/%s outbox", *self.key)
            try:
                await asyncio.wait_for(self.wake_out.wait(), min(TICK, wait) if wait is not None else TICK)
            except asyncio.TimeoutError:
                pass
            self.wake_out.clear()

    async def start(self) -> None:
        from . import owner
        store.recover(*self.key)
        store.recover_out(*self.key)
        loop = asyncio.get_running_loop()
        self.tasks = [loop.create_task(self._recv()), loop.create_task(self._work()), loop.create_task(self._send())]
        try:
            await owner.recover(self)
        except Exception:  # noqa: BLE001 — 重启前等着的回复补不上：只少一条，不拦着起
            log.exception("channel %s/%s recover", *self.key)

    async def stop(self) -> None:
        self.stopped = True
        for t in [*self.tasks, *self.lanes.values()]:
            t.cancel()
        for t in [*self.tasks, *self.lanes.values()]:
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        try:
            await self.ch.close()
        except Exception:  # noqa: BLE001
            pass
        self.note("stopped")


def runner(kind: str, account: str) -> Runner | None:
    return RUNNERS.get((kind, account))


def wake_out(kind: str, account: str) -> None:
    r = runner(kind, account)
    if r is not None:
        r.wake_out.set()


def start_one(acc: Account) -> Runner | None:
    cls = channel_class(acc.kind)
    if cls is None:
        return None
    r = Runner(acc, cls)
    RUNNERS[r.key] = r
    spawn(r.start())
    return r


def start() -> int:
    """服务启动时（main.py 的 lifespan，claw 是 builtin 才调）：开着的账号都起来。→ 起了几个。"""
    if not accounts.builtin():
        return 0
    try:
        store.prune()
    except Exception:  # noqa: BLE001
        pass
    n = 0
    for acc in accounts.enabled():
        if (acc.kind, acc.id) in RUNNERS:
            continue
        if start_one(acc) is not None:
            n += 1
    return n


async def stop() -> None:
    for r in list(RUNNERS.values()):
        await r.stop()
    RUNNERS.clear()


async def restart(kind: str, account: str) -> Runner | None:
    """设置里改了这个账号：停下旧的，开着就按新的配置重起。"""
    old = RUNNERS.pop((kind, account), None)
    if old is not None:
        await old.stop()
    acc = accounts.find(kind, account)
    if acc is None or not acc.enabled or not accounts.builtin():
        return None
    return start_one(acc)
