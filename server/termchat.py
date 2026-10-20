"""mousse chat：在终端里跟主对话（或某个 Agent）说话（第 8 批，13.7，2026-10-19）。网络只用标准库（urllib），文字走服务器的 i18n。

  mousse chat [--agent main|<id>] [--server 地址] [--token 令牌]

读一行，当你自己的一句话发进那个对话（POST /api/chat/send，和 app 发的一样：app 里看得到，排队、插话都照旧），回复一边来一边打出来
（SSE 的 delta），用到的工具一步一行（progress 事件里的步骤：开始打一行，没做成、交给你点头的再打一行）。
  /stop，或者回复进行中按 Ctrl-C：停下这一轮（POST /api/chat/stop，已经说了的留着）。
  /quit、/exit 或 Ctrl-D：退出。回复进行中退出不会停它，服务器照样回完，app 里看得到。
这一轮交了要你点头的事（收件箱卡）：打一行请你去 app 里确认，终端里不批。它问你一个选择题（ask_user，自己的内核）：在终端里直接答
（POST /api/engine/answer），回车跳过就去 app 里答。流断了（网络、Ctrl-C）用 GET /api/chat/stream 接回这一轮。
令牌和地址照 mousse agent（mousse.py）：本机用 server.json 里第一把能走 /api 的，--token / MOUSSE_TOKEN、--server / MOUSSE_SERVER 换。
Agent 自己跑的命令里（环境里有 MOUSSE_AGENT）不跑：这是给你自己用的，花的也是你自己回合的额度。
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator
from typing import TextIO

from i18n import L, lang

READ_TIMEOUT = 1900  # 一轮最长 1800 秒；工具跑着的时候流里可以很久没有字，别断
SHORT = 30


class Failed(Exception):
    """请求没成：连不上、服务器拒了。消息给人看。"""


class Api:
    def __init__(self, base: str, token: str | None) -> None:
        self.base = base.rstrip("/")
        self.token = token

    def _req(self, method: str, path: str, body: dict | None, accept: str) -> urllib.request.Request:
        headers = {"Accept": accept, "Accept-Language": "zh-CN" if lang() == "zh" else "en"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if os.environ.get("MOUSSE_VIA"):  # 同 mousse agent：经 /mcp、内核的工具跑的，服务器据此认能不能花 ChatGPT 套餐
            headers["X-Mousse-Via"] = os.environ["MOUSSE_VIA"]
        data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
        return urllib.request.Request(self.base + path, data=data, headers=headers, method=method)

    def _open(self, req: urllib.request.Request, timeout: float):
        try:
            return urllib.request.urlopen(req, timeout=timeout)  # noqa: S310 — 用户自己的服务器
        except urllib.error.HTTPError as e:
            raise Failed(_why(e)) from None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            why = getattr(e, "reason", None) or e
            raise Failed(L(f"连不上服务器 {self.base}：{why}", f"Can't reach the server at {self.base}: {why}")) from None

    def call(self, method: str, path: str, body: dict | None = None, timeout: float = SHORT) -> dict:
        with self._open(self._req(method, path, body, "application/json"), timeout) as r:
            raw = r.read().decode("utf8", "replace")
        try:
            out = json.loads(raw) if raw.strip() else {}
        except ValueError:
            raise Failed(L("服务器回的不是 JSON", "The server's reply wasn't JSON")) from None
        return out if isinstance(out, dict) else {}

    def stream(self, method: str, path: str, body: dict | None = None):
        """SSE 的响应；204（没有在进行的回复）→ None。"""
        r = self._open(self._req(method, path, body, "text/event-stream"), READ_TIMEOUT)
        if r.status == 204:
            r.close()
            return None
        return r


def _why(e: urllib.error.HTTPError) -> str:
    if e.code == 401:  # 服务器的那句是给 app 的（连接页），终端里换成这边的说法
        return L("令牌不对：用 --token 或 MOUSSE_TOKEN 给一把能用的（python3 tokens.py add <名字>）",
                 "Wrong token: pass one that works with --token or MOUSSE_TOKEN (python3 tokens.py add <name>)")
    try:
        j = json.loads(e.read().decode("utf8", "replace"))
    except (ValueError, OSError):
        j = {}
    detail = j.get("detail") if isinstance(j, dict) else None
    if detail is None and isinstance(j, dict):
        detail = j.get("error")
    return detail if isinstance(detail, str) and detail else f"HTTP {e.code}"


def events(resp) -> Iterator[tuple[str, dict]]:
    """SSE：`event: x` + `data: {...}` + 空行 = 一个事件。坏行跳过。"""
    event, data = "message", []
    for raw in resp:
        line = raw.decode("utf8", "replace").rstrip("\r\n")
        if not line:
            if data:
                try:
                    payload = json.loads("\n".join(data))
                except ValueError:
                    payload = None
                if isinstance(payload, dict):
                    yield event, payload
            event, data = "message", []
        elif line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].strip())


class Out:
    """往终端打：回复的字接着打；步骤、提示另起一行（回复打到一半时先换行）。"""

    def __init__(self, f: TextIO) -> None:
        self.f = f
        self.mid = False

    def text(self, s: str) -> None:
        if s:
            self.f.write(s)
            self.f.flush()
            self.mid = not s.endswith("\n")

    def line(self, s: str = "") -> None:
        if self.mid:
            self.f.write("\n")
        self.f.write(s + "\n")
        self.f.flush()
        self.mid = False

    def end(self) -> None:
        if self.mid:
            self.f.write("\n")
            self.f.flush()
        self.mid = False


class Turn:
    """一轮回复在终端上的样子：字、步骤（每步开始一行，没做成 / 等你点头再一行）、转交和后台任务的卡、选择题。
    text 是服务器给的全文，printed 是终端上已经打出来的：接回来时服务器先补发整段，只打还没打过的那截。"""

    def __init__(self, out: Out) -> None:
        self.out = out
        self.text = ""
        self.printed = ""
        self.steps: dict[str, tuple[str, str]] = {}  # id → (状态, 打过的说明)
        self.cards: set[str] = set()
        self.done: dict | None = None

    def show(self) -> None:
        if self.text.startswith(self.printed):
            self.out.text(self.text[len(self.printed):])
        else:  # 整段换掉了（Claude Code 的流断了重来之类）：终端收不回已经打的字，另起一段打新的
            self.out.line(L("（回复整段重写：）", "(The reply was rewritten:)"))
            self.out.text(self.text)
        self.printed = self.text

    def delta(self, s: str) -> None:
        self.text += s
        self.show()

    def replace(self, s: str) -> None:
        self.text = s
        self.show()

    def progress(self, data: dict) -> None:
        for st in data.get("steps") or []:
            if not isinstance(st, dict) or not st.get("id"):
                continue
            sid, status = str(st["id"]), str(st.get("status") or "running")
            what = str(st.get("detail") or st.get("tool") or "").strip()  # 说明是工具自己的一句标题（带着是哪样工具），没有就写工具名
            old = self.steps.get(sid)
            if old is None:
                self.out.line(f"  · {what}" if status != "failed" else f"  ✗ {what}")
            elif old[0] != status:
                if status == "failed":
                    self.out.line(f"  ✗ {what}")
                elif status == "done" and what != old[1]:  # 交给你点头的：说明后面多了「等你点头」
                    self.out.line(f"  ✓ {what}")
            self.steps[sid] = (status, what if old is None or status == "failed" or what != old[1] else old[1])

    def card(self, data: dict) -> None:
        cid = f"{data.get('kind')}:{data.get('id')}"
        if cid in self.cards:
            return
        self.cards.add(cid)
        if data.get("kind") == "handoff":
            q = " ".join(str(data.get("question") or "").split())[:80]
            self.out.line(L(f"  ↳ 转给 {data.get('toName') or data.get('to')}：{q}", f"  ↳ Asked {data.get('toName') or data.get('to')}: {q}"))
        elif data.get("kind") == "task":
            self.out.line(L(f"  ↳ 后台任务：{data.get('title') or ''}", f"  ↳ Background task: {data.get('title') or ''}"))


def pending_cards(api: Api, thread: str) -> list[dict]:
    """这个对话里等你点头的收件箱卡。读不到就当没有（不挡着聊天）。"""
    try:
        items = api.call("GET", f"/api/inbox?thread={urllib.parse.quote(thread)}").get("items") or []
    except Failed:
        return []
    return [it for it in items if isinstance(it, dict) and it.get("status") == "pending"]


def answer(api: Api, thread: str, q: dict, said: str, out: Out) -> None:
    """选择题：编号或选项的字 → choice，别的 → 自己的话。服务器拒了（只认选项的题、已经答过、过了时间）打一行说明，去 app 里答。"""
    options = [str(o) for o in q.get("options") or []]
    body: dict = {"conversation": thread, "id": q.get("id")}
    if said.isdigit() or said.lower() in (o.lower() for o in options):
        body["choice"] = int(said) if said.isdigit() else said
    else:
        body["text"] = said
    try:
        api.call("POST", "/api/engine/answer", body)
    except Failed as e:
        out.line(L(f"  没答上：{e}", f"  Not answered: {e}"))


def follow(api: Api, resp, thread: str, turn: Turn, read: Callable[[str], str]) -> None:
    """读一条回复流到 done（或者流断了）。选择题当场问。"""
    out = turn.out
    for event, data in events(resp):
        if event == "delta":
            turn.delta(str(data.get("text") or ""))
        elif event == "text":
            turn.replace(str(data.get("text") or ""))
        elif event == "progress":
            turn.progress(data)
        elif event == "card":
            turn.card(data)
        elif event == "queued":
            out.line(L("（插进了正在进行的这一轮）", "(Added to the reply in progress)") if data.get("steer")
                     else L("（排在正在进行的回复后面）", "(Queued after the reply in progress)"))
        elif event == "ask_user":
            out.end()
            try:
                said = read(L("回答（编号、选项或你自己的话；回车 = 去 app 里答）：", "Answer (number, option or your own words; Enter = answer in the app): ")).strip()
            except EOFError:  # Ctrl-D：不在这里答
                said = ""
            if said:
                answer(api, thread, data, said, out)
        elif event == "done":  # 全文：停了的「（停了）」、没回成的说明只在这里
            if data.get("text"):
                turn.replace(str(data["text"]))
            turn.done = data
            return


def one_turn(api: Api, thread: str, text: str, out: Out, read: Callable[[str], str]) -> None:
    """发一句、把回复打出来；Ctrl-C = 停下这一轮（已经说了的留着），再接回来把结尾读完。"""
    turn = Turn(out)
    before = {it.get("id") for it in pending_cards(api, thread)}
    try:
        try:
            resp = api.stream("POST", "/api/chat/send", {"thread": thread, "text": text})
        except Failed as e:
            out.line(L(f"没发出去：{e}", f"Not sent: {e}"))
            return
        try:
            if resp is not None:
                with resp:
                    follow(api, resp, thread, turn, read)
        except (OSError, urllib.error.URLError):
            pass  # 流断了：下面接回来
        if turn.done is None:
            reattach(api, thread, turn, read)
    except KeyboardInterrupt:
        out.end()
        stop(api, thread, out, quiet=True)
        try:
            reattach(api, thread, turn, read)
        except KeyboardInterrupt:
            out.end()
    finish(api, thread, turn, before)


def reattach(api: Api, thread: str, turn: Turn, read: Callable[[str], str]) -> None:
    """GET /api/chat/stream 接回这一轮：服务器先补发已经说了的整段（终端上已经打过的不再打），再接着收到 done。"""
    try:
        resp = api.stream("GET", f"/api/chat/stream?thread={urllib.parse.quote(thread)}")
    except Failed as e:
        turn.out.line(L(f"接不回这一轮：{e}（回复在 app 里看得到）", f"Couldn't reattach: {e} (the reply is in the app)"))
        return
    if resp is None:
        return
    turn.text = ""
    try:
        with resp:
            follow(api, resp, thread, turn, read)
    except (OSError, urllib.error.URLError):
        pass


def stop(api: Api, thread: str, out: Out, quiet: bool = False) -> None:
    """POST /api/chat/stop。quiet：正跟着这一轮的流（「（停了）」会跟着回复的结尾打出来），停成了就不另说。"""
    try:
        r = api.call("POST", "/api/chat/stop", {"thread": thread})
    except Failed as e:
        out.line(L(f"停不下来：{e}", f"Couldn't stop it: {e}"))
        return
    if not quiet or not r.get("stopped"):
        out.line(L("（停了）", "(Stopped)") if r.get("stopped") else L("（没有在进行的回复）", "(No reply in progress)"))


def finish(api: Api, thread: str, turn: Turn, before: set) -> None:
    """一轮结束：出错说一声；这一轮新交的要你点头的事，请你去 app 里确认。"""
    out = turn.out
    out.end()
    d = turn.done or {}
    if d.get("status") == "error" and d.get("error") and str(d["error"]) not in turn.text:
        out.line(L(f"  ✗ 没回成：{d['error']}", f"  ✗ No reply: {d['error']}"))
    new = [it for it in pending_cards(api, thread) if it.get("id") not in before]
    if new:
        out.line(L(f"有 {len(new)} 件事等你确认，请在 app 里看（收件箱）：", f"{len(new)} item(s) need your OK. Confirm in the app (Inbox):"))
        for it in new:
            out.line(f"  • {it.get('title') or ''}")


def who(api: Api, agent: str) -> tuple[str, str]:
    """--agent → (对话线程, 名字)。main = 主对话；别的要是有这个 Agent。"""
    if agent == "main":
        return "main", str(api.call("GET", "/api/health").get("app_name") or "") or "main"
    groups = api.call("GET", "/api/groups").get("groups") or []
    for g in groups:
        if isinstance(g, dict) and g.get("id") == agent:
            return agent, str(g.get("name") or agent)
    ids = ", ".join(str(g.get("id")) for g in groups if isinstance(g, dict)) or "-"
    raise Failed(L(f"没有这个 Agent：{agent}（有：main, {ids}）", f"No such Agent: {agent} (there are: main, {ids})"))


def chat(api: Api, agent: str, out: TextIO | None = None, read: Callable[[str], str] = input) -> int:
    """读一行、发一句，直到 /quit 或 Ctrl-D。退出码：0 正常退出；1 连不上、没有这个 Agent。"""
    o = Out(out or sys.stdout)
    try:
        thread, name = who(api, agent)
    except Failed as e:
        o.line(str(e))
        return 1
    o.line(L(f"跟 {name} 说话（{thread}）。/stop 或 Ctrl-C 停下正在进行的回复，/quit 或 Ctrl-D 退出。",
             f"Talking to {name} ({thread}). /stop or Ctrl-C stops the reply in progress; /quit or Ctrl-D quits."))
    waiting = pending_cards(api, thread)
    if waiting:
        o.line(L(f"这个对话里有 {len(waiting)} 件事在等你确认，请在 app 里看（收件箱）。",
                 f"{len(waiting)} item(s) in this chat are waiting for your OK. Confirm them in the app (Inbox)."))
    while True:
        try:
            line = read("› ")
        except EOFError:
            o.end()
            return 0
        except KeyboardInterrupt:  # 提示符下按 Ctrl-C：换一行接着等（同 Python 的交互模式）
            o.line()
            continue
        text = line.strip()
        if not text:
            continue
        if text in ("/quit", "/exit"):
            return 0
        if text == "/stop":
            stop(api, thread, o)
            continue
        one_turn(api, thread, text, o, read)


def main(base: str, token: str | None, agent: str) -> int:
    if os.environ.get("MOUSSE_AGENT"):
        print(L("mousse chat 是给你自己在终端里用的；Agent 自己跑的命令里不能用。Agent 之间转话用 handoff。",
                "mousse chat is for you, in a terminal; it can't run inside an Agent's own command. Agents hand over with handoff."),
              file=sys.stderr)
        return 2
    try:
        import readline  # noqa: F401 — 有就有了行编辑和上下键翻历史（标准库，有的系统没有）
    except ImportError:
        pass
    return chat(Api(base, token), agent)
