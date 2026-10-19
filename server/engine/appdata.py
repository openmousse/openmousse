"""claw 选 builtin（自己的内核）时，app 的活动、模型、安全、定时任务这几页读内核（10/19 第 1 批：第一位用户的实例这天换到 builtin，
OpenClaw 的 Gateway 只留一周当退路，Gateway 一停这几页就空了）。data.py 的这几个接口在 builtin 下交给这里，返回的样子和 OpenClaw 那条路
对齐（app 不用改就能显示），多出来的字段留给以后的 app；OpenClaw 那条路一个字不变。

  GET  /api/activity        {id, time, actor, text, kind}：app 自己的动作（activity_log）+ 内核的一轮轮（engine_turns：谁、在哪、什么时候、
                            哪个模型、成没成、花了多少、用了哪些工具；kind reply / failed）+ 定时任务和心跳跑的每一次（engine_job_runs，
                            kind cron / failed；它们那一轮不再单列）。另给 turn、model、models、cost、billing、tokens、status、ms……不调 Gateway
  GET  /api/models          primary（默认模型）、fallbacks（它的备用链）、subagent（后台和定时用的）、allowed（app 里能切换的）、providers（用到的
                            每一家：套餐还是按量、连没连上，OpenClaw 那条路的样子；Claude Code 照 runners 的检测，不碰令牌）；另给 engine（每个模型的链）、
                            claudeCode、chatgpt、purposes（按用途选的结果，engine/setup_api.py）、usage（这 30 天花了多少，只给主人的设备）
  GET  /api/security        facts：app 接口认证、密钥放哪、主机上的命令（engine.host_exec 的放行名单，按 Agent）、工具档位（engine.tools.levels）、
                            沙箱给了谁（engine/sandbox.py）、等你点头的内核的卡、Doorman；plan 只留还没做的（隔离、出网审批、凭证代位都做了）；
                            另给 engine（同样的东西，结构化的）
  GET  /api/upcoming        内核的定时任务（engine/jobs.py：等你点「开」的、开着的、停着的；source cron）和心跳（engine/heartbeat.py；source
                            heartbeat），下一次什么时候跑；系统定时器照常（data.timers）。另给 state、stateText、agentId、deliver……
  POST /api/upcoming/{id}   {"enabled"}：开 = 恢复（jobs.resume），关 = 暂停（jobs.pause）；还在等你点头的、心跳 → 409（各有各的开法）
  GET  /api/approvals       老 app 的审批队列（没有收件箱的 app 才读）：等你点头的内核的卡（收件箱 kind tool：关卡交的、主机命令的
                            「只这一次 / 以后都放行」、MCP 工具……），样子同 OpenClaw 的执行审批
  POST /api/approvals/{id}  {"allow"}：同意 / 拒绝，走收件箱（inbox.act），和在收件箱里点一样
"""
from __future__ import annotations

import json
from datetime import datetime

from fastapi import HTTPException, Request

import claw
from config import settings
from i18n import L

from . import jobs, models, store, usage, when

ACTIVITY_MAX = 500
FAILED_STATUSES = ("error", "timeout", "refused", "interrupted", "missed", "limit")


# —— 小工具 ——————————————————————————————————————————————————————————————————

def _at(s: str | None) -> datetime | None:
    """库里存的 ISO（带偏移）→ server.json 时区的时间；读不了 → None。"""
    try:
        return datetime.fromisoformat(s).astimezone(settings.tz) if s else None
    except (TypeError, ValueError):
        return None


def _n(v) -> int:
    try:
        return max(0, int(v or 0))
    except (TypeError, ValueError):
        return 0


def _json(raw, default):
    try:
        v = json.loads(raw) if raw else default
    except (TypeError, ValueError):
        return default
    return v if isinstance(v, type(default)) else default


def served(r) -> str:
    """这一轮记在哪个模型名下（同 usage.summary）：真的调过的最后一个（备用链换过就是换上的那个），没有就是一开始要的。"""
    called = _json(r["models"], [])
    m = next((x for x in reversed(called) if isinstance(x, str) and x), None)
    return models.canonical(m or str(r["model"] or ""))


def spend(model: str, raw: str | None, started: str | None) -> dict:
    """这一轮花了多少 → {"billing": metered | plan | unpriced, "cost": 美元（metered 才有）, "tokens"}（同 usage.summary 的算法：
    当时没算钱、记了 token 的按那天的价补上；用户自己的套餐记「套餐」不计钱）。"""
    u = usage.priced(model, _json(raw, {}), started or "")
    billing = usage.billing_of(model, u)
    return {"billing": billing, "cost": round(float(u.get("cost") or 0), 6) if billing == "metered" else None,
            "tokens": sum(_n(u.get(k)) for k in ("in", "out", "cache_read", "cache_write"))}


def money(c: float) -> str:
    """$1.50、$0.0123、$0.002：不到 1 美元的留到 4 位小数（一轮常常只有几厘钱），末尾的 0 去掉、至少留 2 位。"""
    if c == 0:
        return "$0"
    if c < 0.0001:
        return "<$0.0001"
    if c >= 1:
        return f"${c:.2f}"
    whole, frac = f"{c:.4f}".rstrip("0").split(".")
    return f"${whole}.{frac.ljust(2, '0')}"


def spend_text(s: dict) -> str:
    if s["billing"] == "plan":
        return L("套餐", "your plan")
    if s["billing"] == "metered":
        return money(s["cost"] or 0.0)
    n = s["tokens"]
    if not n:
        return ""
    k = f"{n / 1000:.1f}k" if n >= 1000 else str(n)
    return L(f"{k} token", f"{k} tokens")


def who_of(agent: str | None, names: dict[str, str]) -> str:
    """活动记录里显示的名字，同 data.agent_label（一次查好的 names，不一行查一次库）。"""
    if not agent or agent == "main":
        return settings.app_name
    return f"{settings.app_name} · {names[agent]}" if agent in names else agent


def agent_of(agent: str | None, names: dict[str, str]) -> str:
    """一个 Agent 自己的名字（主对话 = 「主对话」）。"""
    if not agent or agent == "main":
        return L("主对话", "Main chat")
    return names.get(agent) or agent


def short(text: str | None, n: int = 60) -> str:
    import data
    return data.short(text or "", n)


def when_text(dt: datetime | None) -> str:
    import data
    return data.when(dt) if dt else ""


# —— 活动记录 ————————————————————————————————————————————————————————————————

# 一轮是在做什么 → ((做成了的说法), (没做成的说法))
ACTS = {
    "reply": (("回复了一次", "Replied"), ("一次回复没成", "A reply failed")),
    "task": (("后台任务做完了一轮", "A background task finished a turn"), ("后台任务的一轮没成", "A background task turn failed")),
    "api": (("经对外接口回复了一次", "Replied over the API"), ("对外接口的一次回复没成", "An API reply failed")),
    "card": (("名片 Agent 回了一句", "Your card agent replied"), ("名片 Agent 的一句没回成", "A card agent reply failed")),
    "doorman": (("Doorman 复查了一句", "Doorman reviewed a line"), ("Doorman 的一次复查没成", "A Doorman review failed")),
    "writeback": (("整理了一段对话里要记的", "Sorted out what to remember from a chat"), ("整理要记的没成", "Sorting out what to remember failed")),
    "compact": (("把太长的对话压缩了一次", "Compacted a long chat"), ("压缩对话没成", "Compacting a chat failed")),
    "skills": (("回看了一轮，看要不要存成技能", "Looked back at a turn for a skill to keep"), ("回看技能没成", "Looking back for a skill failed")),
    "json": (("在后台整理了一次", "Ran a background job"), ("后台的一次整理没成", "A background job failed")),
    "cyber": (("赛博回了一句", "A cyber replied"), ("赛博的一句没回成", "A cyber reply failed")),
    "claw": (("OpenClaw 回复了一次", "OpenClaw replied"), ("OpenClaw 的一次回复没成", "An OpenClaw reply failed")),
}


def act_of(r) -> str:
    """一轮在做什么（ACTS 的键）：看回合类型、来路、对话。"""
    kind, origin, conv = str(r["kind"] or ""), str(r["origin"] or ""), str(r["conversation"] or "")
    if conv.startswith("compact:"):
        return "compact"
    if conv.startswith("writeback:") or origin == "writeback":
        return "writeback"
    if kind in ("cyber", "claw"):
        return kind
    if conv.startswith("task:") or origin == "task":
        return "task"
    if origin == "api" or conv.startswith("api:"):
        return "api"
    if kind == "outsider":
        if origin in ("card", "a2a"):
            return "card"
        if origin == "doorman":
            return "doorman"
        return "skills" if origin in ("skill-review", "skills") else "json"
    return "reply"


MACHINE = ("-", "", "card", "a2a", "doorman", "json", "manuals", "improve", "writeback")


def where_of(conv: str, names: dict[str, str], labels: dict[str, str]) -> str:
    """这一轮在哪个对话：主对话 / 某个 Agent / 项目 / 后台任务「…」/ 对外接口……；内核自己的（Doorman、一次性 JSON……）→ 空。"""
    if conv.startswith(("compact:", "writeback:")):
        rest = conv.split(":", 1)[1]
        return L("名片 Agent", "Card agent") if rest.startswith("card:") else where_of(rest, names, labels)
    if conv.startswith("task:"):
        label = labels.get(conv[5:])
        return L(f"后台任务「{label}」", f'Background task "{label}"') if label else L("后台任务", "Background task")
    if conv.startswith("api:"):
        return L("对外接口", "API")
    if conv.startswith("cli:"):  # 第 1 批 b：命令行 mousse agent 的一轮（engine/agent_cli.py，cli:<Agent>:<随机>）
        return L("命令行", "Command line")
    if conv.startswith("cyber:"):
        return L("赛博", "Cyber")
    if conv in MACHINE or conv.startswith(("skills:", "cron:", "heartbeat:")):
        return ""
    if conv == "main":
        return L("主对话", "Main chat")
    if conv in names:
        return names[conv]
    if conv.startswith("tp-"):
        return L("Zen 的话题", "Zen topic")
    if conv.startswith("grp-"):
        return L("会议", "Meeting")
    if conv.startswith("study-"):
        return L("学习台", "Study desk")
    return conv


def _in(conn, sql: str, ids: list[str]) -> list:
    """WHERE … IN (…) 一次查完（ids 空 = 不查）。"""
    if not ids:
        return []
    return conn.execute(sql.format(",".join("?" * len(ids))), ids).fetchall()


def detail_of(model: str, s: dict) -> str:
    return " · ".join(x for x in (model, spend_text(s)) if x)


def turn_item(r, tools: dict[str, int], names: dict[str, str], labels: dict[str, str]) -> tuple[float, dict]:
    """engine_turns 的一行 → (时间戳, 活动记录的一行)。"""
    end = _at(r["ended_at"]) or _at(r["started_at"])
    model = served(r)
    s = spend(model, r["usage"], r["started_at"])
    (ok_zh, ok_en), (bad_zh, bad_en) = ACTS[act_of(r)]
    detail = detail_of(model, s)
    asked = models.canonical(str(r["model"] or ""))
    if asked and model and asked != model:  # 备用链换过模型
        detail += L(f"，从 {asked} 换过来的", f", switched from {asked}")
    counts = [f"{k} ×{v}" if v > 1 else k for k, v in tools.items()]
    tool_text = L(f"，用了工具 {'、'.join(counts)}", f", used tools: {', '.join(counts)}") if counts else ""
    status, err = str(r["status"] or ""), str(r["error"] or "")
    if status == "done":
        kind, text = "reply", L(f"{ok_zh}（{detail}）", f"{ok_en} ({detail})")
    elif status == "aborted" and err != "server restarted":  # 用户点了停、关服务时停下的
        kind, text = "reply", L(f"{ok_zh}，中途停了（{detail}）", f"{ok_en}, stopped partway ({detail})")
    else:
        why = {"max_steps": L("步数到上限了", "hit the step limit"), "length": L("回复被截断了", "the reply was cut off"),
               "refused": L("模型拒答了", "the model declined"), "aborted": L("服务器重启了", "the server restarted")}.get(status) or short(err) or status
        kind, text = "failed", L(f"{bad_zh}（{detail}）：{why}", f"{bad_en} ({detail}): {why}")
    conv = str(r["conversation"] or "")
    where = "" if r["agent"] and r["agent"] != "main" and conv == r["agent"] else where_of(conv, names, labels)  # Agent 自己的对话：名字不写两遍
    who = who_of(r["agent"], names)
    return ((end.timestamp() if end else 0.0),
            {"id": r["id"], "time": when_text(end), "actor": f"{who} · {where}" if where else who, "text": text + tool_text, "kind": kind,
             "source": "turn", "turn": r["id"], "conversation": r["conversation"], "agent": r["agent"] or "main", "status": status,
             "model": model, "models": _json(r["models"], []), "tools": dict(tools), "ms": r["ms"], **s})


def run_item(r, t, names: dict[str, str]) -> tuple[float, dict]:
    """engine_job_runs 的一行（定时任务、心跳跑的一次）→ (时间戳, 活动记录的一行)。t = 它那一轮（没调模型的没有）。"""
    end = _at(r["ended_at"]) or _at(r["started_at"])
    if r["kind"] == "heartbeat":
        title = L(f"心跳（{agent_of(r['agent'], names)}）", f"Heartbeat ({agent_of(r['agent'], names)})")
    else:
        title = r["job_name"] or r["job"]
    st, err = str(r["status"] or ""), short(r["error"] or "", 60)
    texts = {"ok": L(f"「{title}」跑完了", f'"{title}" finished'),
             "silent": L(f"「{title}」跑完了，这次没有要说的", f'"{title}" finished; nothing to say this time'),
             "busy": L(f"「{title}」这次没跑：那个对话一直在回话", f'"{title}" was skipped this time: that chat stayed busy'),
             "skipped": L(f"「{title}」这次跳过了（服务器停着时错过的不补跑）", f'"{title}" was skipped (runs missed while the server was down aren\'t caught up)'),
             "missed": L(f"「{title}」错过了，没跑", f'"{title}" was missed and didn\'t run'),
             "limit": L(f"「{title}」没跑：今天的定时任务到上限了", f'"{title}" didn\'t run: today\'s limit for scheduled jobs was reached'),
             "interrupted": L(f"「{title}」跑到一半服务器重启了", f'"{title}" was cut off by a server restart')}
    text = texts.get(st) or L(f"「{title}」没跑成：{err or st}", f'"{title}" failed: {err or st}')
    extra: dict = {"turn": r["turn"], "model": None, "cost": None, "billing": None, "tokens": 0}
    if t is not None:
        model = served(t)
        s = spend(model, t["usage"], t["started_at"])
        text += L(f"（{detail_of(model, s)}）", f" ({detail_of(model, s)})")
        extra.update(model=model, **s)
    return ((end.timestamp() if end else 0.0),
            {"id": r["id"], "time": when_text(end), "actor": f"{L('定时任务', 'Scheduled job')} · {who_of(r['agent'], names)}", "text": text,
             "kind": "failed" if st in FAILED_STATUSES else "cron", "source": "heartbeat" if r["kind"] == "heartbeat" else "job",
             "job": r["job"], "run": r["kind"], "status": st, "agent": r["agent"], **extra})


def engine_items(limit: int, names: dict[str, str]) -> list[tuple[float, dict]]:
    """内核记下的：定时任务和心跳跑的每一次 + 别的一轮轮（定时任务那一轮已经算在它跑的那一次里，不再单列）。没用过内核 → []（不建表）。"""
    if not store.exists():
        return []
    out: list[tuple[float, dict]] = []
    have_jobs = jobs.exists()
    with store.read() as conn:
        runs = conn.execute("SELECT r.*, j.name AS job_name FROM engine_job_runs r LEFT JOIN engine_jobs j ON j.id = r.job "
                            "WHERE r.status != 'running' ORDER BY r.rowid DESC LIMIT ?", (limit,)).fetchall() if have_jobs else []
        rows = conn.execute("SELECT * FROM engine_turns WHERE status != 'running' AND conversation NOT LIKE 'cron:%' "
                            "AND conversation NOT LIKE 'heartbeat:%' ORDER BY rowid DESC LIMIT ?", (limit,)).fetchall()
        ids = [r["id"] for r in rows]
        of_jobs = {x["turn"] for x in _in(conn, "SELECT turn FROM engine_job_runs WHERE turn IN ({})", ids)} if have_jobs else set()
        rows = [r for r in rows if r["id"] not in of_jobs]  # 定时任务在对话里跑的那一轮（session chat）：算在它跑的那一次里
        used: dict[str, dict[str, int]] = {}
        for s in _in(conn, "SELECT turn, name FROM engine_steps WHERE type='tool' AND turn IN ({}) ORDER BY turn, seq", [r["id"] for r in rows]):
            n = str(s["name"] or L("工具", "tool"))
            used.setdefault(s["turn"], {})[n] = used.get(s["turn"], {}).get(n, 0) + 1
        labels = {x["id"]: x["label"] for x in _in(conn, "SELECT id, label FROM engine_tasks WHERE id IN ({})",
                                                   [str(r["conversation"])[5:] for r in rows if str(r["conversation"] or "").startswith("task:")])
                  if x["label"]}
        turns = {t["id"]: t for t in _in(conn, "SELECT id, model, models, usage, started_at FROM engine_turns WHERE id IN ({})",
                                         [r["turn"] for r in runs if r["turn"]])}
    out += [run_item(r, turns.get(r["turn"]), names) for r in runs]
    out += [turn_item(r, used.get(r["id"], {}), names, labels) for r in rows]
    return out


def activity(limit: int = 80) -> list[dict]:
    """/api/activity 在 builtin 下的那一串（见文件开头），新的在前，最多 limit 条。"""
    import chat
    import data
    limit = max(1, min(ACTIVITY_MAX, int(limit or 80)))
    items: list[tuple[float, dict]] = []
    with chat._lock, data.ddb() as conn:  # 1) app 自己的动作（同 OpenClaw 那条路）
        for r in conn.execute("SELECT * FROM activity_log ORDER BY id DESC LIMIT ?", (limit,)):
            dt = datetime.fromisoformat(r["ts"])
            items.append((dt.timestamp(), {"id": f"a{r['id']}", "time": data.when(dt), "actor": r["actor"], "text": r["text"], "kind": r["kind"]}))
    items += engine_items(limit, data.thread_names())  # 2) 内核的一轮轮、定时任务和心跳跑的每一次
    items.sort(key=lambda x: x[0], reverse=True)
    return [x for _, x in items[:limit]]


# —— 模型与计费 ——————————————————————————————————————————————————————————————

def is_owner(request: Request | None) -> bool:
    """这个请求是不是主人的设备（setup_api.owner 那一关，不抛）。"""
    from . import setup_api
    if request is None:
        return False
    try:
        setup_api.owner(request)
    except setup_api.SetupError:
        return False
    return True


def provider_rows(refs: list[str]) -> list[dict]:
    """用到的每一家（OpenClaw 那条路 /api/models 的 providers 的样子）：{provider, name, status: ok | missing | expired,
    subscription: 套餐（Claude Code、ChatGPT）还是按量（API 密钥）, expires}，套餐的在前。不碰任何令牌：Claude Code 照 runners 的检测，
    ChatGPT 照 chatgpt_plan 记下的状态。"""
    from . import catalog, chatgpt_plan, claude_brain, setup_api
    out: list[dict] = []
    seen: set[str] = set()
    for ref in refs:
        pid = ref.split("/", 1)[0] if "/" in ref else ""
        if not pid or pid in seen:
            continue
        seen.add(pid)
        sub = models.subscription(ref)
        if sub == "claude-code":
            cc = claude_brain.status()
            out.append({"provider": pid, "name": "Claude Code", "status": "ok" if models.subscription_ready(ref) else "missing",
                        "subscription": cc["billing"] != "api_key", "expires": None, "plan": cc["plan"], "auth": cc["auth"]})
        elif sub == "chatgpt":
            st = chatgpt_plan.status()
            out.append({"provider": pid, "name": catalog.text(catalog.LOGINS["chatgpt"]["name"]),
                        "status": "ok" if st["ready"] else "expired" if st["needsReconnect"] else "missing", "subscription": True,
                        "expires": None, "plan": st["plan"]})
        else:
            e = catalog.entry(pid)
            name = catalog.text(e["name"]) if e else str(setup_api.over(pid).get("name") or pid)
            out.append({"provider": pid, "name": name, "status": "ok" if models.has_key(ref) else "missing", "subscription": False, "expires": None})
    out.sort(key=lambda p: not p["subscription"])
    return out


async def purposes() -> list[dict]:
    """按用途选的结果（setup_api.purposes_view，不探本机）：每个用途选了什么、实际用哪个、跟着谁；出错 → []。"""
    from . import setup_api
    try:
        view = await setup_api.purposes_view(probe_local=False)
    except Exception as e:  # noqa: BLE001 — 设置那一层读不了，模型页照样出
        print(f"[engine] 模型页读不到按用途选的：{type(e).__name__}: {e}")
        return []
    out = []
    for r in view["purposes"]:
        row = {k: r[k] for k in ("id", "label", "applies", "current", "effective", "usable", "follows") if k in r}
        if isinstance(r.get("parts"), dict):
            row["parts"] = {k: {"value": v.get("value"), "effective": v.get("effective")} for k, v in r["parts"].items()}
        out.append(row)
    return out


async def models_view(request: Request | None = None) -> dict:
    """/api/models 在 builtin 下（见文件开头）。"""
    from . import chatgpt_plan, claude_brain, runners, setup_api
    ref = models.default_model() or None
    allowed = claw.models()
    chain = models.chain_after(ref) if ref else []
    bg = models.background_model()
    routes = [x for x in (models.route(k) for k in ("card", "doorman", "json")) if x]
    refs = list(dict.fromkeys([*([ref] if ref else []), *allowed, *chain, *([bg] if bg else []), *routes]))
    cc = {**claude_brain.status(), "checking": runners.checking("claude-code")}
    st = chatgpt_plan.status()
    out = {"ok": True, "primary": claw.model(), "fallbacks": chain, "subagent": bg or ref, "allowed": allowed,
           "providers": provider_rows(refs), "claw": claw.info(),
           "engine": {"model": ref, "fallbacks": models.fallbacks(), "fallback": models.fallback_allowed(),
                      "models": [{"ref": m, "chain": models.chain_after(m), "usable": setup_api.usable(m), "plan": models.subscription(m)}
                                 for m in allowed]},
           "claudeCode": cc,
           "chatgpt": {**{k: st[k] for k in ("enabled", "connected", "ready", "planEnabled", "plan", "needsReconnect", "models")},
                       "background": bool((st.get("background") or {}).get("allowed"))},
           "purposes": await purposes(), "usage": None}
    if is_owner(request):  # 花了多少只给主人的设备（同 /api/setup/usage）
        try:
            u = usage.summary(30)
            out["usage"] = {k: u[k] for k in ("since", "until", "days", "currency", "total", "byModel", "byPurpose")}
        except Exception as e:  # noqa: BLE001
            print(f"[engine] 模型页读不到用量：{type(e).__name__}: {e}")
    return out


# —— 安全 ————————————————————————————————————————————————————————————————————

def group_ids() -> list[str]:
    import chat
    import data
    with chat._lock, data.ddb() as conn:
        return [r["id"] for r in conn.execute("SELECT id FROM groups ORDER BY position, created_at")]


def pending_cards() -> list:
    """等你点头的内核的卡（收件箱 kind tool），新的在前。"""
    import chat
    import inbox
    with chat._lock, inbox.idb() as conn:
        inbox.expire(conn)
        return conn.execute("SELECT * FROM inbox WHERE status='pending' AND kind='tool' ORDER BY created_at DESC, rowid DESC").fetchall()


def host_facts(names: dict[str, str]) -> tuple[list[dict], list[dict]]:
    """主机上的命令（engine.host_exec）：开了的 Agent 一行一个（放行名单、名单外的怎么办）；都没开 → 一行「全关」。→ (facts, 结构化的)"""
    from . import host_exec
    a = host_exec.cfg().get("agents")
    rows, raw = [], []
    for aid in (str(k) for k, v in (a.items() if isinstance(a, dict) else []) if v is True or isinstance(v, dict)):
        p = host_exec.policy(aid)
        if p is None:  # engine.tools.levels 把 host_exec 关了：工具档位那一行说
            continue
        who = agent_of(aid, names)
        raw.append({"agent": aid, "allow": list(p.allow), "ask": p.ask, "security": p.security, "timeout": p.timeout})
        if p.security == "full":
            rows.append({"title": L(f"主机上的命令 · {who}", f"Commands on the server · {who}"),
                         "sub": L("不看放行名单（security full）：每条都直接跑，危险的命令才先问你。",
                                  "No allowlist (security full): every command runs right away; only risky ones ask you first."),
                         "state": L("全放开", "Wide open"), "tone": "warn"})
            continue
        shown = "; ".join(x[:80] for x in p.allow[:5]) + ("…" if len(p.allow) > 5 else "")
        miss = {"on-miss": L("名单外的先问你", "anything else asks you first"), "always": L("每一条都先问你", "every command asks you first"),
                "off": L("名单外的直接不跑", "anything else is refused")}[p.ask]
        rows.append({"title": L(f"主机上的命令 · {who}", f"Commands on the server · {who}"),
                     "sub": L(f"放行名单 {len(p.allow)} 条" + (f"：{shown}" if shown else "") + f"；{miss}；危险的命令照样先问，不经 shell。",
                              f"Allowlist: {len(p.allow)}" + (f" ({shown})" if shown else "") + f"; {miss}; risky commands always ask; no shell."),
                     "state": L("有名单", "Allowlist"), "tone": "good"})
    if not rows:
        rows.append({"title": L("主机上的命令", "Commands on the server"),
                     "sub": L("全关：没有 Agent 能在服务器上直接跑命令（要跑的在自己的沙箱里跑）。",
                              "Off: no Agent can run commands on the server itself (they use their own sandbox)."),
                     "state": L("关着", "Off"), "tone": "good"})
    return rows, raw


def levels_fact() -> tuple[dict, dict]:
    """工具档位（engine.tools.levels）：没改过 = 默认的分类；改过的列出来，有 auto（本来要问的直接做）的提醒一下。"""
    from . import tools as tools_mod
    lv = tools_mod.levels()
    base = L("只读和撤得回的直接做，撤不回的、对外的先问你（收件箱的卡）", "reads and changes that can be undone run right away; anything that can't be "
             "undone or reaches outside asks you first (an inbox card)")
    if not lv:
        return ({"title": L("工具档位", "Tool levels"), "sub": L(f"都按默认的分类：{base}。", f"All default: {base}."),
                 "state": L("默认", "Default"), "tone": "good"}, lv)
    words = {"auto": L("直接做", "auto"), "ask": L("先问", "ask"), "off": L("关掉", "off")}
    changed = [f"{k} → {words.get(v, v)}" for k, v in sorted(lv.items())]
    loose = any(v == "auto" for v in lv.values())
    return ({"title": L("工具档位", "Tool levels"),
             "sub": L(f"改过的：{'；'.join(changed)}。别的照默认：{base}。" + ("写了「直接做」的那几样不再问你。" if loose else ""),
                      f"Changed: {'; '.join(changed)}. Everything else is default: {base}." + (" The ones set to auto no longer ask you." if loose else "")),
             "state": L("改过", "Changed"), "tone": "warn" if loose else "good"}, lv)


def sandbox_fact(names: dict[str, str]) -> tuple[dict, list[dict]]:
    """沙箱给了谁：只在沙箱里的 Agent（代办……）、普通 Agent 自己的沙箱和它们看不看得到工作区；出网只经 Doorman。"""
    import errand

    from . import sandbox
    access = {"none": L("看不到工作区", "can't see the workspace"), "ro": L("工作区只读挂进去", "workspace mounted read-only"),
              "rw": L("能写工作区（命令先问你）", "workspace writable (commands ask you first)")}
    raw, lines = [], []
    for aid, v in sandbox.agents().items():
        if aid == sandbox.errand_id() and not errand.enabled():  # 代办没装（errand.py setup）：不算
            continue
        if aid != sandbox.errand_id() and aid not in names:
            continue
        raw.append({"agent": aid, "only": True, "access": v["access"], "browser": v["browser"]})
        lines.append(L(f"{agent_of(aid, names)}：只在沙箱里（{access.get(v['access'], v['access'])}）",
                       f"{agent_of(aid, names)}: works only in the sandbox ({access.get(v['access'], v['access'])})"))
    for aid in ["main", *group_ids()]:
        v = sandbox.hands_of(aid)
        if v is None:
            continue
        raw.append({"agent": aid, "only": False, "access": v["access"], "browser": v["browser"]})
        lines.append(L(f"{agent_of(aid, names)}：自己的沙箱（{access.get(v['access'], v['access'])}）",
                       f"{agent_of(aid, names)}: its own sandbox ({access.get(v['access'], v['access'])})"))
    if not raw:
        return ({"title": L("沙箱", "Sandbox"),
                 "sub": L("这台服务器还没装沙箱（python3 server/errand.py setup）：Agent 的文件工具只在各自的工作区里，主机上的命令默认全关。",
                          "No sandbox on this server yet (python3 server/errand.py setup): Agents' file tools stay in their own workspaces and "
                          "commands on the server are off by default."),
                 "state": L("未启用", "Off"), "tone": "neutral"}, raw)
    net = (L("断网", "no network at all") if sandbox.setting("network") == "none"
           else L("出网只经 Doorman（提交、发送、登录、上传先问你；真的密钥只在出口换上）",
                  "online only through Doorman (submitting, sending, logging in and uploading ask you first; real keys are only added on the way out)"))
    return ({"title": L("沙箱", "Sandbox"), "sub": L("；".join(lines) + f"。{net}。", "; ".join(lines) + f". {net[0].upper() + net[1:]}."),
             "state": L("已启用", "On"), "tone": "good"}, raw)


def keys_fact() -> dict:
    """密钥放哪：app 里存的在 secrets.env（0600），不进 server.json、日志、报错（secretstore.py）。"""
    from . import setup_api
    f = setup_api.config_facts()["secretsFile"]
    if not f["exists"]:
        return {"title": L("密钥存放", "Key storage"), "sub": L("app 里还没存过密钥（密钥也可以放在进程环境或 env_file 里）。",
                                                              "No keys saved in the app yet (keys can also live in the environment or env_file)."),
                "state": L("没有", "None"), "tone": "neutral"}
    if f["tooOpen"]:
        return {"title": L("密钥存放", "Key storage"), "sub": L(f"{f['path']} 的权限是 {f['mode']}，别人也读得到：改成 600。",
                                                              f"{f['path']} has mode {f['mode']}, so others can read it: change it to 600."),
                "state": L("注意", "Review"), "tone": "warn"}
    return {"title": L("密钥存放", "Key storage"),
            "sub": L(f"app 里存的密钥在 {f['path']}（只有你能读）；不进 server.json、日志和报错。",
                     f"Keys saved in the app are in {f['path']} (only you can read it); never in server.json, logs or errors."),
            "state": L("已满足", "OK"), "tone": "good"}


def security_view() -> dict:
    """/api/security 在 builtin 下（见文件开头）。"""
    import data
    names = data.thread_names()
    tokens, nodes = settings.tokens(), settings.tailscale_nodes()
    token_names, node_names = ", ".join(tokens), ", ".join(sorted(nodes))
    auth = {"title": L("app 接口认证", "App API auth"),
            "sub": L(f"接入令牌 {len(tokens)} 个（{token_names or '无'}）；Tailscale 免令牌设备：{node_names or '无'}；监听 {settings.host}:{settings.port}。",
                     f"Access tokens: {len(tokens)} ({token_names or 'none'}); Tailscale devices without a token: {node_names or 'none'}; "
                     f"listening on {settings.host}:{settings.port}."),
            "state": L("已满足", "OK") if (tokens or nodes) else L("无认证", "No auth"), "tone": "good" if (tokens or nodes) else "warn"}
    hosts, host_raw = host_facts(names)
    lv_fact, lv = levels_fact()
    box, box_raw = sandbox_fact(names)
    n = len(pending_cards())
    pending = {"title": L("待审批", "Awaiting approval"),
               "sub": L(f"收件箱里现在有 {n} 张内核的卡等你决定（撤不回的、对外的、主机上名单外的命令）。",
                        f"{n} engine {'card' if n == 1 else 'cards'} in your inbox waiting for your OK (things that can't be undone, reach "
                        "outside, or commands off the allowlist)."),
               "state": str(n), "tone": "neutral"}
    facts = [auth, keys_fact(), *hosts, lv_fact, box, pending]
    if sf := data.sentinel_fact():
        facts.append(sf)
    plan = [{"title": L("付款", "Payments"), "sub": L("每笔都先问你，用限额的虚拟卡；现在内核不碰付款。",
                                                    "Each payment asks you first and uses a capped virtual card; the engine doesn't handle payments yet.")}]
    each_time, no_need = L("每次审批", "Approve every time"), L("免审", "No approval")
    rules = [[L("后台执行", "Background work"), no_need], [L("浏览器只读", "Read-only browsing"), no_need], [L("发邮件", "Sending email"), each_time],
             [L("登录后操作", "Actions while logged in"), each_time], [L("填表", "Filling in forms"), L("提交前审批", "Approve before submitting")],
             [L("订行程", "Booking travel"), L("下单前审批", "Approve before booking")], [L("付款", "Payments"), L("每笔审批 + 限额虚拟卡", "Approve each one + capped virtual card")]]
    return {"ok": True, "facts": facts, "plan": plan, "rules": rules,
            "engine": {"hostExec": host_raw, "levels": lv, "sandbox": box_raw, "pending": n}}


# —— 接下来会自动做的事 ——————————————————————————————————————————————————————————

def _ms(dt: datetime | None) -> int:
    return int(dt.timestamp() * 1000) if dt else 0


def job_row(j: dict, names: dict[str, str]) -> dict:
    """一条内核的定时任务 → /api/upcoming 的一行（OpenClaw 那条路 cron_jobs 的样子）。"""
    nxt = when.parse_iso(j["next_run"]) if j["state"] == "on" else None
    last = _at(j["last_run"])
    v = jobs.view(j)
    repeat = v["when"][:1].upper() + v["when"][1:]  # 「every day 09:00」→「Every day 09:00」（中文不变），和 OpenClaw 那条路的写法一样起头大写
    return {"id": j["id"], "source": "cron", "title": j["name"], "rawName": j["id"], "agent": who_of(j["agent"], names), "modelId": j["model"],
            "when": when_text(nxt), "repeat": repeat, "enabled": j["state"] == "on",
            "toggleable": j["state"] in ("on", "paused"), "nextAt": _ms(nxt),
            "last": {"status": j["last_status"], "when": when_text(last)} if last else None,
            "state": j["state"], "stateText": v["stateText"], "agentId": j["agent"], "deliver": j["deliver"], "deliverName": v["deliverName"],
            "session": j["session"], "level": j["level"], "runs": j["runs"], "times": j["times"], "pending": bool(j["pending"]), "inbox": j["inbox"]}


def heartbeat_rows(names: dict[str, str]) -> list[dict]:
    """开了心跳的 Agent（engine/heartbeat.py）一行一个：只读（开关在 server.json 的 engine.heartbeat）。"""
    from . import heartbeat
    out = []
    for agent, h in heartbeat.status().items():
        nxt, last = _at(h.get("nextRun")), _at(h.get("lastRun"))
        every = int(h["every"])
        repeat = L(f"每 {every // 60} 小时", f"Every {every // 60} h") if every % 60 == 0 else L(f"每 {every} 分钟", f"Every {every} min")
        if h.get("active"):
            repeat += f" · {h['active'][0]}–{h['active'][1]}"
        name = agent_of(agent, names)
        out.append({"id": f"heartbeat:{agent}", "source": "heartbeat", "title": L(f"心跳（{name}）", f"Heartbeat ({name})"),
                    "rawName": f"heartbeat:{agent}", "agent": who_of(agent, names), "modelId": h.get("model"), "when": when_text(nxt),
                    "repeat": repeat, "enabled": True, "toggleable": False, "nextAt": _ms(nxt),
                    "last": {"status": h.get("lastStatus"), "when": when_text(last)} if last else None,
                    "agentId": agent, "checklist": h.get("checklist"), "lastReason": h.get("lastReason")})
    return out


def upcoming() -> list[dict]:
    """内核的定时任务（等你点「开」的、开着的、停着的；跑完的、错过的、不要了的不列）和心跳。系统定时器在 data.timers。"""
    import data
    names = data.thread_names()
    rows = [job_row(j, names) for j in jobs.listing() if j["state"] in jobs.LIVE] if jobs.exists() else []
    return rows + heartbeat_rows(names)


async def toggle(jid: str, enabled: bool) -> dict:
    """POST /api/upcoming/{id}：开 = 恢复、关 = 暂停（同 /api/engine/jobs/{id}/resume | pause，用户做的直接生效）。"""
    import chat
    if jid.startswith("heartbeat:"):
        raise HTTPException(409, L("心跳在 server.json 的 engine.heartbeat 里开关", "Heartbeats are switched on and off in engine.heartbeat in server.json"))
    j = jobs.get(jid) if jobs.exists() else None
    if j is None or j["state"] in jobs.GONE:
        raise HTTPException(404, L("没有这个定时任务", "No such scheduled job"))
    if enabled and j["state"] == "draft":
        raise HTTPException(409, L("这个定时任务还在等你点头：点收件箱里那张卡上的「开」", "This job is still waiting for your OK: approve its card in the inbox"))
    try:
        if enabled and j["state"] != "on":
            jobs.resume(jid, by_user=True)
        elif not enabled and j["state"] == "on":
            jobs.pause(jid)
    except jobs.JobError as e:
        raise HTTPException(409, str(e)) from None
    now = jobs.get(jid) or j
    if now["state"] != j["state"]:
        chat.log_activity(L(f"{'启用' if enabled else '停用'}了定时任务「{j['name']}」", f'{"Enabled" if enabled else "Disabled"} scheduled job "{j["name"]}"'),
                          "toggled")
    return {"ok": True, "job": jobs.view(now)}


# —— 等你点头（老 app 的审批队列）——————————————————————————————————————————————————

def approvals() -> list[dict]:
    """等你点头的内核的卡 → OpenClaw 执行审批的样子 {id, kind, action, detail, fields, groupId, requestedAt}（另给 thread、agent、tool）。"""
    import data
    import inbox

    from . import tools as tools_mod
    nm = inbox.names()
    out = []
    for r in pending_cards():
        call = tools_mod.call_of(r["id"]) if store.exists() else None
        tool = call["tool"] if call is not None else ""
        thread = r["thread"] or "main"
        fields = [(L("工具", "Tool"), tool), (L("agent", "Agent"), inbox.source_name(r["source"], nm)),
                  (L("对话", "Chat"), L("主对话", "Main chat") if thread == "main" else nm.get(thread) or thread)]
        created = _at(r["created_at"])
        out.append({"id": r["id"], "kind": "tool", "action": data.short(r["title"], 80), "detail": r["why"] or "",
                    "fields": [{"k": k, "v": v} for k, v in fields if v], "groupId": None if thread == "main" else thread,
                    "requestedAt": when_text(created), "thread": thread, "agent": r["source"], "tool": tool or None})
    return out


async def decide(aid: str, allow: bool) -> dict:
    """同意 / 拒绝一张等你点头的内核的卡：走收件箱（inbox.act：卡上的钩子照原样跑一次、结果发回对话、记活动），和在收件箱里点一样。"""
    import inbox
    if not any(r["id"] == aid for r in pending_cards()):
        raise HTTPException(404, L("没有这个等你点头的动作（可能已经处理过了）", "No such action awaiting approval (it may have been handled already)"))
    res = await inbox.act(aid, inbox.ActIn(action="approve" if allow else "reject"))
    return {"ok": True, "item": res.get("item")}
