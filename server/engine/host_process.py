"""主机上的后台命令（第 3 批第 2 项，2026-10-19）：host_exec 的 background / yield_ms，和管它们的 host_process 工具。
照 OpenClaw 的 exec（yieldMs、background）+ process 工具（src/agents/bash-tools.process.ts：list / poll / log / write / kill）和
Hermes 的 tools/process_registry.py（输出写文件、做完通知、watch_patterns 只当「很少见的一次性信号」）的设计写的，代码是自己的。

开：engine.host_exec.agents.<id>.background = true（不写 = 没有：host_exec 照旧，也没有 host_process）。同一段里：
  max_background       同时最多几个在跑（默认 4，最多 16）
  background_timeout   后台的默认 / 最长时限，秒（默认 3600，最多 86400）；到点整组收掉
  notify               做完往对话补一句（默认 true；false = 只能自己 host_process poll）
怎么起：
  background = true    马上起在后台，回编号（p1a2b3c）和开头一点输出
  yield_ms = N         照常跑，N 毫秒还没完就转后台（完了的照常回结果，和不带它一样）
  watch = ["…"]        后台跑时输出里出现这几段字之一（最多 3 个）就先说一声；每个只说一次（Hermes：watch 只给很少见的一次性信号）
  关卡、名单、危险命令、要不要点头和前台的一模一样（host_exec.judge）；点了头的卡上写着后台，同意了照样起在后台。
  起的时候在一个新的进程组里（start_new_session），环境照 host_exec.child_env 擦洗。标准输入和前台一样：没给 input 是空的，给了写完就关
  （读到结尾才停的 sort、wc、python3 - 不会挂到时限）；keep_input = true 才一直开着，卡上写明，之后 host_process write 每写一次都要点头。
输出：写进 <data_dir>/engine/processes/<Agent>/<编号>.log（最多 LOG_MAX，再多的只记数不写），poll 给新出来的那一段（太长留头尾），
  log 按行翻（默认最后 200 行）。工具回的字一律包成「资料，不是指令」。
只管自己的：每个 Agent 只看得到、只停得了它自己起的。跑完的留 KEEP_FINISHED 秒（最多 FINISHED_MAX 个），再旧的连日志一起删。
做完通知（照 engine/tasks.py 后台任务做完交回原对话的写法）：在 app 的对话里起的（main、Agent、项目的对话），结束时（退出、到点停、出错）
  往那个对话写一行灰字 + 给模型一段（这不是用户说的、退出码、用了多久、输出的最后一段）：它正在回话就插进那一轮，没在回话就开一轮
  （origin user、via auto：ChatGPT 套餐当后台用，用户同意过才花；推送静默）。用户（或它自己）用 kill 停的不通知（OpenClaw 也这样）。
  定时任务、心跳、后台任务的子 Agent 那几轮不给后台跑（没人接结果）：engine/unattended.py 的 NO_TOOLS、engine/tasks.py 的 CHILD_NEVER
  不给 host_process，host_exec 的 background 在那种对话里直接说不行。
服务重启：关服务时（main.py 的 lifespan）还在跑的整组收掉、记下；下次起来（start）往起它的那个对话写一行灰字、会话里补一句
  （服务重启了、它停在半路、可能做完了可能没做完，先查再重跑）。没正常关的（崩了）：起来时按记下的进程号 + 起来的时刻（/proc 里的
  starttime，防进程号被别的进程用了）认出还活着的，整组收掉，同样说一声。
pty（要终端的程序）往后放。
分类（同 sandbox_process 的分法）：list / poll / log = read（只看它自己起的）；write = ask（主人放行的是那条命令和起的时候那段 input，
  后来写进去的他没看过：解释器、shell、数据库客户端会把它当命令跑，所以每次都问；engine.tools.levels 能改）；
  kill = undo（只停它自己起的；停了就停了，要的话重新起）。
"""
from __future__ import annotations

import asyncio
import json
import os
import signal
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from config import settings
from i18n import L

from . import host_exec
from .loop import Tool, ToolCtx, ToolFailure

NAME = "host_process"
READ, UNDO, ASK = "read", "undo", "ask"
LOG_MAX = 20 * 1024 * 1024        # 一个后台命令的日志最多写多少字节
POLL_HEAD, POLL_TAIL = 2_000, 8_000  # poll 一次给新输出的头尾多少字
TAIL_LINES, LOG_LINES_MAX = 200, 2000
KEEP_FINISHED, FINISHED_MAX = 3600.0, 50
WAIT_MAX = 30.0                   # poll 最多等几秒
NOTE_TAIL = 3_000                 # 做完通知里带输出的最后多少字
WATCH_MAX = 3
UNWATCHED = ("cron:", "heartbeat:", "task:")   # 这些对话里起的没人接结果：不给后台跑


@dataclass
class Proc:
    id: str
    agent: str
    conversation: str
    turn: str
    command: str                   # 给人看的那一条
    cwd: str
    pid: int
    ticks: int | None              # /proc/<pid>/stat 的 starttime（认进程用）
    log: Path
    started: float = field(default_factory=time.time)
    status: str = "running"        # running / exited / killed / timeout / restart / lost
    exit: int | None = None
    ended: float | None = None
    size: int = 0                  # 一共输出了多少字节（写进日志的不超过 LOG_MAX）
    read: int = 0                  # poll 已经给过的位置
    timeout: float = 3600.0
    notify: bool = True
    keep_input: bool = False       # 起的时候要了一直开着标准输入（之后 host_process write 往里写）
    watch: list[str] = field(default_factory=list)
    seen: set[str] = field(default_factory=set)
    collected: bool = False        # yield_ms 等到它跑完了：当前台的结果回过了，不再通知
    waiting: bool = False          # yield_ms 正在等它：这时候跑完的由等的那边当前台的结果回，不通知
    proc: Any = None
    task: Any = None
    done: Any = None               # asyncio.Event：跑完了
    lock: Any = None


_procs: dict[str, Proc] = {}


def root() -> Path:
    return Path(settings.data_dir) / "engine" / "processes"


def state_file() -> Path:
    return root() / "state.json"


def mine(agent: str) -> dict[str, Proc]:
    return {k: p for k, p in _procs.items() if p.agent == agent}


def _ticks(pid: int) -> int | None:
    """这个进程起来的时刻（/proc/<pid>/stat 的第 22 项）：进程号被重用了也认得出不是它。没有 /proc → None。"""
    try:
        raw = Path(f"/proc/{pid}/stat").read_text()
        return int(raw[raw.rindex(")") + 2:].split()[19])
    except (OSError, ValueError, IndexError):
        return None


def _save() -> None:
    """记下还在跑的、因为重启停下的（重启后认、通知用）。写不了不要紧。"""
    rows = [{"id": p.id, "agent": p.agent, "conversation": p.conversation, "command": p.command, "pid": p.pid, "ticks": p.ticks,
             "status": p.status, "started": p.started, "log": str(p.log)} for p in _procs.values() if p.status in ("running", "restart")]
    try:
        root().mkdir(parents=True, exist_ok=True)
        tmp = state_file().with_suffix(".tmp")
        tmp.write_text(json.dumps(rows, ensure_ascii=False))
        os.replace(tmp, state_file())
    except OSError:
        pass


def _prune(agent: str) -> None:
    """跑完的留 KEEP_FINISHED 秒、最多 FINISHED_MAX 个；再旧的连日志一起删。"""
    now = time.time()
    done = sorted((p for p in mine(agent).values() if p.status != "running"), key=lambda p: p.ended or 0)
    for i, p in enumerate(done):
        if now - (p.ended or now) > KEEP_FINISHED or len(done) - i > FINISHED_MAX:
            _procs.pop(p.id, None)
            try:
                p.log.unlink(missing_ok=True)
            except OSError:
                pass


def _secs(s: float) -> str:
    s = int(max(0, s))
    return f"{s}s" if s < 60 else f"{s // 60}m{s % 60:02d}s" if s < 3600 else f"{s // 3600}h{(s // 60) % 60:02d}m"


def _state(p: Proc) -> str:
    if p.status == "running":
        return f"running for {_secs(time.time() - p.started)}"
    took = _secs((p.ended or time.time()) - p.started)
    return {"exited": f"finished with exit code {p.exit} after {took}", "killed": f"stopped (killed) after {took}",
            "timeout": f"stopped at its time limit after {took}", "restart": "stopped by a server restart",
            "lost": "lost (the server restarted while it ran)"}.get(p.status, p.status)


def _read(p: Proc, start: int, end: int) -> str:
    try:
        with open(p.log, "rb") as f:
            f.seek(max(0, start))
            return f.read(max(0, end - start)).decode("utf-8", "replace")
    except OSError:
        return ""


def _clip(text: str, head: int = POLL_HEAD, tail: int = POLL_TAIL) -> str:
    if len(text) <= head + tail:
        return text
    return f"{text[:head]}\n[… {len(text) - head - tail} characters cut from the middle; host_process log shows them …]\n{text[-tail:]}"


def _wrap(text: str) -> str:
    from .tools import wrap_untrusted
    return wrap_untrusted("host process", text)


# —— 起 ————————————————————————————————————————————————————————————————————————

async def spawn(agent: str, c: host_exec.Cmd, stdin: str | None, *, timeout: float, keep_env: list[str], conversation: str, turn: str,
                notify: bool, watch: list[str], limit: int, keep_input: bool = False) -> Proc:
    """起一个后台命令（新的进程组、输出写文件；标准输入照前台：写完 input 就关，keep_input 才开着）。满了、起不来抛 ToolFailure。"""
    _prune(agent)
    running = [p for p in mine(agent).values() if p.status == "running"]
    if len(running) >= limit:
        raise ToolFailure(L(f"已经有 {len(running)} 个后台命令在跑（最多 {limit} 个）：先用 host_process 停掉不要的",
                            f"{len(running)} background commands are already running (at most {limit}); stop one with host_process first"))
    pid_ = "p" + uuid.uuid4().hex[:6]
    d = root() / host_exec.sandbox.slug(agent)
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    log = d / f"{pid_}.log"
    try:
        proc = await asyncio.create_subprocess_exec(c.exe, *c.argv[1:], cwd=str(c.cwd), env=host_exec.child_env(agent, keep_env),
                                                    start_new_session=True,
                                                    stdin=asyncio.subprocess.PIPE if stdin or keep_input else asyncio.subprocess.DEVNULL,
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    except OSError as e:
        raise ToolFailure(L(f"起不来：{e.strerror or e}", f"Couldn't start it: {e.strerror or e}")) from None
    p = Proc(pid_, agent, conversation, turn, c.raw, c.cwd_rel, proc.pid, _ticks(proc.pid), log, timeout=timeout, notify=notify,
             keep_input=keep_input, watch=[w for w in watch if w][:WATCH_MAX], proc=proc, done=asyncio.Event(), lock=asyncio.Lock())
    _procs[p.id] = p
    _save()
    if stdin:
        try:
            proc.stdin.write(stdin.encode("utf8"))
            await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
    if proc.stdin is not None and not keep_input:
        proc.stdin.close()
    p.task = asyncio.ensure_future(_pump(p, c.shell is not None))
    return p


async def _pump(p: Proc, shell: bool = False) -> None:
    """读输出写日志、看 watch、到点收掉；结束时记下状态、收掉这一组留下的（不管经不经 shell：后台命令的那一组都归它）、通知。"""
    proc = p.proc
    deadline = time.monotonic() + p.timeout
    buf = b""
    try:
        with open(p.log, "ab") as f:
            os.chmod(p.log, 0o600)

            async def reader() -> None:
                nonlocal buf
                while True:
                    chunk = await proc.stdout.read(65536)
                    if not chunk:
                        return
                    if p.size < LOG_MAX:
                        f.write(chunk[: LOG_MAX - p.size])
                        f.flush()
                    p.size += len(chunk)
                    if p.watch and len(p.seen) < len(p.watch):
                        buf = (buf + chunk)[-8192:]
                        _watch(p, buf.decode("utf-8", "replace"))
            read = asyncio.ensure_future(reader())
            wait = asyncio.ensure_future(host_exec.exited(proc))  # 退出就算完（不等 cmd & 起的还拿着的管道；见 host_exec.exited）
            done, _ = await asyncio.wait({wait}, timeout=max(0.0, deadline - time.monotonic()))
            if not done:
                p.status = "timeout"
                await host_exec._stop(proc)
            await asyncio.wait({wait}, timeout=10)
            await host_exec._reap_group(proc.pid)  # 这一组留下的（cmd & 起的）一起收掉，管道才关
            try:
                await asyncio.wait_for(read, 5)
            except asyncio.TimeoutError:
                read.cancel()
    except asyncio.CancelledError:  # 关服务（shutdown 已经记好状态、收掉了）
        raise
    except Exception as e:  # noqa: BLE001 — 读日志出错：照样收尾
        print(f"[engine] 后台命令 {p.id} 读输出出错：{type(e).__name__}: {str(e)[:120]}")
    p.exit = proc.returncode
    p.ended = time.time()
    if p.status == "running":
        p.status = "exited"
    p.done.set()
    _save()
    if p.notify and not p.collected and not p.waiting and p.status in ("exited", "timeout"):
        await _notify_done(p)


def _watch(p: Proc, text: str) -> None:
    """输出里出现了 watch 的哪一段（每段只说一次）。"""
    for w in p.watch:
        if w in text and w not in p.seen:
            p.seen.add(w)
            line = next((ln for ln in text.splitlines() if w in ln), w)
            asyncio.ensure_future(_tell(p, L(f"后台命令 {p.id} 的输出里出现了「{w}」", f"Background command {p.id} printed \"{w}\""),
                                        f"[Background command {p.id}: its output now contains \"{w}\". It's still running.]\n" +
                                        _wrap(line[:500]) + "\nThis isn't from the user; it's a signal from a command you started. Act on "
                                        "it if it matters; host_process poll shows more."))


# —— 做完、重启：往对话里说一声 ——————————————————————————————————————————————————————————

def deliverable(conversation: str) -> bool:
    """在 app 的对话里起的（main、Agent、项目的对话）：结果才有地方交。"""
    if conversation == "main":
        return True
    try:
        import inbox
        return conversation in inbox.names()
    except Exception:  # noqa: BLE001
        return False


async def _notify_done(p: Proc) -> None:
    tail = _read(p, max(0, min(p.size, LOG_MAX) - NOTE_TAIL * 4), min(p.size, LOG_MAX))[-NOTE_TAIL:].strip()
    head = f"[Background command finished] {p.id} · {' '.join(p.command.split())[:200]} · {_state(p)}"
    body = (head + "\n" + (_wrap(f"(last part of the output)\n{tail}") if tail else "(no output)")
            + "\nThis isn't from the user: it's the result of a command you started earlier. Check it against what you were doing, tell "
            "the user what matters, and don't re-run it unless it's needed (host_process log shows the whole output).")
    line = L(f"后台命令结束了：{' '.join(p.command.split())[:60]}", f"Background command finished: {' '.join(p.command.split())[:60]}")
    await _tell(p, line, body)


async def _tell(p: Proc, line: str, text: str) -> None:
    try:
        await deliver(p.conversation, line, text)
    except Exception as e:  # noqa: BLE001 — 交不出去：poll 照样看得到
        print(f"[engine] 后台命令 {p.id} 的通知没交到 {p.conversation}：{type(e).__name__}: {str(e)[:120]}")


async def deliver(thread: str, line: str, text: str) -> None:
    """往这个对话补一句（照 engine/tasks.py 的 hand_back）：一行灰字；它正在回话就插进那一轮（没赶上就排着），没在回话就开一轮。
    不是 app 的对话（对外接口、定时任务……）就不交。"""
    import chat
    import settle as settle_mod

    from . import owner
    if not deliverable(thread) or chat.SHUTTING_DOWN:
        return
    ts = chat.now_iso()
    with chat._lock, chat.db() as conn:
        uid = int(conn.execute("INSERT INTO messages(thread, role, text, model, ts) VALUES(?,?,?,?,?)",
                               (thread, "auto", settle_mod.MARK + line, None, ts)).lastrowid or 0)
        conn.execute("INSERT INTO threads(id, updated_at) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at", (thread, ts))
    cur = chat.RUNS.get(thread)
    loop = asyncio.get_running_loop()
    if cur is not None and not cur.done:
        item = chat.Queued(user_id=uid, content=text, gw_text=text, ts=ts, future=loop.create_future())
        msg = owner.user_message(text, uid)
        if cur.control is not None and cur.control.can_steer() and msg is not None:
            cur.steered[uid] = item
            cur.control.steer(msg)
        else:
            with chat._lock, chat.db() as conn:
                conn.execute("UPDATE messages SET status='queued' WHERE id=?", (uid,))
            chat.QUEUED.setdefault(thread, []).append(item)
        return
    with chat._lock, chat.db() as conn:
        model = chat.thread_model(conn, thread)
    chat.begin(thread, model, uid, ts, text, "", origin="user", level="quiet", watched=False, via="auto")


def note(thread: str, line: str, said: str) -> None:
    """不开一轮，只说一声：一行灰字 + 那个对话今天的会话里补一句（下一轮它知道）。重启后用。"""
    import chat
    import settle as settle_mod

    from .session import DbSession, day_key, system_note
    if not deliverable(thread):
        return
    ts = chat.now_iso()
    with chat._lock, chat.db() as conn:
        conn.execute("INSERT INTO messages(thread, role, text, model, ts) VALUES(?,?,?,?,?)", (thread, "auto", settle_mod.MARK + line, None, ts))
        conn.execute("INSERT INTO threads(id, updated_at) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at", (thread, ts))
    s = DbSession.latest(day_key(thread, chat.day_of(ts)))
    if s is not None:
        s.append(system_note(said))


# —— host_exec 的 background / yield_ms ——————————————————————————————————————————————————

def _check_conversation(ctx: ToolCtx) -> None:
    if any(str(ctx.conversation or "").startswith(x) for x in UNWATCHED):
        raise ToolFailure(L("这一轮没人接后台命令的结果（定时任务、心跳、后台任务）：去掉 background / yield_ms，照常跑",
                            "Nobody can pick up a background result in this kind of run (scheduled job, heartbeat, background task): "
                            "call again without background / yield_ms"))


def _started(p: Proc, notify: bool) -> str:
    head = _read(p, 0, min(p.size, 2000)).strip()
    tell = (" When it ends, a note with the result comes into this chat; you don't need to keep polling." if notify and deliverable(p.conversation)
            else " Check it with host_process poll.")
    return (f"Started in the background as {p.id} ({_state(p)}). host_process poll / log reads its output, "
            + ("write sends input (each write needs the user's OK), " if p.keep_input else "") + "kill stops it."
            + tell + ("\n" + _wrap(head) if head else ""))


async def run(agent: str, c: host_exec.Cmd, stdin: str | None, args: dict, q: host_exec.Policy, ctx: ToolCtx) -> str:
    """host_exec 带 background / yield_ms（关卡已经放过了）。"""
    _check_conversation(ctx)
    timeout = min(int(args.get("timeout") or q.background_timeout), q.background_timeout)
    watch = [str(w) for w in args.get("watch") or []] if isinstance(args.get("watch"), list) else []
    p = await spawn(agent, c, stdin, timeout=timeout, keep_env=q.env, conversation=ctx.conversation, turn=ctx.turn, notify=q.notify,
                    watch=watch, limit=q.max_background, keep_input=args.get("keep_input") is True)
    if args.get("background"):
        await asyncio.sleep(0.2)  # 给它一点时间吐出开头（起不来的报错也在这）
        return _started(p, q.notify)
    wait = max(0.0, min(float(args.get("yield_ms") or 0) / 1000, q.max_timeout))
    p.waiting = True
    try:
        await asyncio.wait_for(asyncio.shield(p.done.wait()), wait)
    except asyncio.TimeoutError:
        pass
    except asyncio.CancelledError:  # 等的时候用户点了停：这一个停掉（照前台的规矩）
        p.collected = True
        await kill(p)
        raise
    finally:
        p.waiting = False
    if not p.done.is_set():  # 到点还没完：转后台，做完再通知
        return _started(p, q.notify)
    p.collected = True  # 等到了：当前台的结果回，不再通知
    text = _read(p, 0, min(p.size, LOG_MAX))
    cut = max(0, p.size - len(text.encode("utf8")))
    res = host_exec.sandbox.Ran(p.exit, _clip(text, 8_000, 24_000), p.size, cut, int(((p.ended or time.time()) - p.started) * 1000),
                                timed_out=p.status == "timeout")
    _procs.pop(p.id, None)
    try:
        p.log.unlink(missing_ok=True)
    except OSError:
        pass
    _save()
    return host_exec.render(res)


async def start_approved(agent: str, c: host_exec.Cmd, a: dict, q: host_exec.Policy, row) -> str:
    """卡上要后台跑、用户点了同意（host_exec.approved）：照样起在后台，做完往起它的那个对话补一句。"""
    p = await spawn(agent, c, a.get("input"), timeout=float(a.get("timeout") or q.background_timeout), keep_env=q.env,
                    conversation=str(row["thread"] or agent), turn=str(row["turn"] or ""), notify=q.notify, watch=list(a.get("watch") or []),
                    limit=q.max_background, keep_input=a.get("keep_input") is True)
    await asyncio.sleep(0.2)
    return _started(p, q.notify)


# —— 停 ————————————————————————————————————————————————————————————————————————

async def kill(p: Proc) -> None:
    if p.status != "running":
        return
    p.status = "killed"
    await host_exec._stop(p.proc)
    await host_exec._reap_group(p.pid)


async def shutdown() -> int:
    """关服务（main.py 的 lifespan）：还在跑的整组收掉，记成「重启停下的」，下次起来往对话里说一声。→ 停了几个。"""
    n = 0
    for p in list(_procs.values()):
        if p.status != "running":
            continue
        p.status, p.notify = "restart", False
        try:
            await host_exec._stop(p.proc)
            await host_exec._reap_group(p.pid)
        except Exception:  # noqa: BLE001
            pass
        n += 1
    _save()
    return n


def _kill_group(pid: int) -> None:
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pid, sig)
        except (ProcessLookupError, PermissionError):
            return
        time.sleep(0.3)


def start() -> int:
    """服务起来时（main.py，claw 是 builtin）：上次记下的——正常关时停下的、没正常关还活着的（进程号和起来的时刻对得上才认，整组收掉）——
    往起它们的那个对话说一声，然后清掉记录。→ 说了几个。"""
    try:
        rows = json.loads(state_file().read_text())
    except (OSError, ValueError):
        return 0
    n = 0
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict):
            continue
        pid, ticks = r.get("pid"), r.get("ticks")
        if r.get("status") == "running" and isinstance(pid, int) and ticks is not None and _ticks(pid) == ticks:
            _kill_group(pid)  # 没正常关（崩了）：它还在跑，收掉
        cmd = " ".join(str(r.get("command") or "").split())
        try:
            note(str(r.get("conversation") or ""), L(f"服务重启了，后台命令停在半路：{cmd[:60]}", f"The server restarted and stopped a background command: {cmd[:60]}"),
                 f"(The server restarted while the background command {r.get('id')} (`{cmd[:200]}`) was running, so it was stopped there. It may "
                 "or may not have finished its work: check its side effects or saved state before running it again.)")
            n += 1
        except Exception as e:  # noqa: BLE001
            print(f"[engine] 重启后说一声没成（后台命令 {r.get('id')}）：{type(e).__name__}: {str(e)[:120]}")
    try:
        state_file().unlink(missing_ok=True)
    except OSError:
        pass
    return n


# —— host_process 工具 ——————————————————————————————————————————————————————————————————

def _get(agent: str, pid: str) -> Proc:
    p = mine(agent).get(str(pid or "").strip())
    if p is None:
        raise ToolFailure(L(f"没有后台命令 {pid}（host_process list 看有哪些；跑完一小时以后、服务重启以后就看不到了）",
                            f"No background command {pid} (host_process list shows them; finished ones go after an hour or a restart)"))
    return p


async def act(agent: str, args: dict) -> str:
    action = str(args.get("action") or "list").strip().lower()
    _prune(agent)
    if action == "list":
        ps = sorted(mine(agent).values(), key=lambda p: p.started)
        if not ps:
            return "No background commands."
        return "\n".join(f"{p.id}  {_state(p)}  {' '.join(p.command.split())[:100]}" for p in ps)
    p = _get(agent, args.get("id"))
    if action == "poll":
        async with p.lock:
            wait = max(0.0, min(float(args.get("wait") or 0), WAIT_MAX))
            end = time.monotonic() + wait
            while wait and p.status == "running" and p.size <= p.read and time.monotonic() < end:
                await asyncio.sleep(min(0.25, max(0.01, end - time.monotonic())))
            upto = min(p.size, LOG_MAX)
            new = _read(p, p.read, upto)
            p.read = upto
            more = " (the log stopped at 20 MB; later output was only counted)" if p.size > LOG_MAX else ""
            return f"{p.id}: {_state(p)}; {p.size} bytes of output so far{more}\n" + (_wrap(_clip(new)) if new.strip() else "(no new output)")
    if action == "log":
        text = _read(p, 0, min(p.size, LOG_MAX))
        lines = text.splitlines()
        limit = max(1, min(int(args.get("limit") or TAIL_LINES), LOG_LINES_MAX))
        if args.get("offset"):
            start = max(1, int(args["offset"]))
            part = lines[start - 1:start - 1 + limit]
        else:
            start = max(1, len(lines) - limit + 1)
            part = lines[start - 1:]
        end = start + len(part) - 1
        head = f"{p.id}: {_state(p)}; lines {start}–{end} of {len(lines)}" + (f" (more: offset {end + 1})" if end < len(lines) else "")
        return head + "\n" + (_wrap("\n".join(part)) if part else "(no output)")
    if action == "write":
        if p.status != "running" or p.proc is None:
            raise ToolFailure(L(f"{p.id} 已经不在跑了", f"{p.id} isn't running any more"))
        if not p.keep_input or p.proc.stdin is None or p.proc.stdin.is_closing():
            raise ToolFailure(L(f"{p.id} 的标准输入关了（起的时候没要 keep_input，或者已经 eof）：要往里写得重新起，带上 keep_input",
                                f"{p.id}'s input is closed (it was started without keep_input, or got eof); start it again with keep_input to write to it"))
        data = str(args.get("input") or "")
        if len(data) > host_exec.INPUT_MAX:
            raise ToolFailure(L(f"一次最多写 {host_exec.INPUT_MAX} 字", f"At most {host_exec.INPUT_MAX} characters at a time"))
        try:
            if data:
                p.proc.stdin.write(data.encode("utf8"))
                await p.proc.stdin.drain()
            if args.get("eof"):
                p.proc.stdin.close()
        except (BrokenPipeError, ConnectionResetError):
            raise ToolFailure(L(f"{p.id} 不收输入了（标准输入关了）", f"{p.id} no longer takes input (its standard input is closed)")) from None
        return f"Wrote {len(data)} characters to {p.id}'s input" + (" and closed it." if args.get("eof") else ".")
    if action == "kill":
        if p.status != "running":
            return f"{p.id} isn't running ({_state(p)})."
        await kill(p)
        return f"Stopped {p.id} and everything it started."
    raise ToolFailure(L("action 只能是 list / poll / log / write / kill", "action must be list, poll, log, write or kill"))


def tool(agent: str) -> Tool | None:
    """这个 Agent 的 host_process（开了 engine.host_exec.agents.<id>.background 才有）。"""
    p = host_exec.policy(agent)
    if p is None or not p.background or host_exec.workspace(agent) is None:
        return None
    from .handkit import levelled

    async def go(args: dict, ctx: ToolCtx) -> str:
        return await act(ctx.agent or "main", args)

    def level(a: dict) -> tuple[str, str]:
        action = str(a.get("action") or "list").strip().lower()
        if action == "kill":
            return levelled(NAME, (UNDO, L("只停它自己起的后台命令；停了就停了，要的话重新起", "Only stops a background command it started itself; "
                                           "start it again if needed")))
        if action == "write":
            return levelled(NAME, (ASK, L("往后台命令的标准输入写：主人放行的只是那条命令和起的时候那段输入，后来写的每一段都要他点头",
                                          "Writes to a background command's input: the user only allowed the command and its input at start, "
                                          "so each later write needs their OK")))
        return levelled(NAME, (READ, L("只看它自己起的后台命令", "Only looks at background commands it started itself")))

    return Tool(name=NAME, kinds=("owner",), run=go, timeout=WAIT_MAX + 30, max_output=20_000, classify=level,
                title=lambda a: f"{a.get('action') or 'list'} {a.get('id') or ''}".strip(),
                description=L(
                    "host_exec 用 background / yield_ms 转到后台的命令：list 看有哪些；poll 看状态和新出来的输出（wait 最多等几秒，等它结束或有新输出）；"
                    "log 按行翻全部输出（默认最后 200 行，offset / limit）；write 往它的标准输入写（起的时候要了 keep_input 才行，每次都要用户点头；"
                    "eof = 写完关上）；kill 停掉（连它起的进程）。"
                    "只看得到你自己起的；跑完会自己往对话里补一句，不用反复 poll。",
                    "Background commands from host_exec background / yield_ms: list them; poll for the state and new output (wait up to a few "
                    "seconds for it to end or print something); log pages through all the output by line (the last 200 lines by default; "
                    "offset / limit); write sends text to its input (only if it was started with keep_input; each write needs the user's OK; "
                    "eof closes it); kill stops it and everything it started. You only see "
                    "your own; a finished one posts a note into the chat by itself, so don't keep polling."),
                schema={"type": "object", "required": ["action"], "properties": {
                    "action": {"type": "string", "enum": ["list", "poll", "log", "write", "kill"]},
                    "id": {"type": "string", "description": L("后台命令的编号（p…）", "The background command's id (p…)")},
                    "wait": {"type": "number", "description": L("poll：最多等几秒（最多 30）", "poll: seconds to wait at most (30 max)")},
                    "offset": {"type": "integer", "description": L("log：从第几行起（从 1 数）", "log: line to start from (1-based)")},
                    "limit": {"type": "integer", "description": L("log：几行（默认 200）", "log: how many lines (default 200)")},
                    "input": {"type": "string", "description": L("write：要写进标准输入的字（要回车自己带 \\n）", "write: text for its input (add \\n for Enter)")},
                    "eof": {"type": "boolean", "description": L("write：写完关上标准输入", "write: close its input afterwards")}}})
