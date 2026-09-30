"""Agent 的创建、修改与删除。一个 Agent = OpenClaw 的一个独立 agent（自己的 workspace、MEMORY.md、skills 允许列表）+ app 里 groups 表的一行。

新建（provision）做四件事，任何一步失败就回滚前面的：
  1. 建 workspace：<openclaw_home>/workspace-<id>/，AGENTS.md（职责 + 通用规则）、IDENTITY.md（带 app 管的职责段，见 role_block）、MEMORY.md、memory/；
     SOUL.md / USER.md 从主 workspace 复制（同一个人格、同一个用户）；skills 软链到主 workspace 的 skills。
  2. 备份 openclaw.json，直接写 agents.entries.<id>，再 `openclaw config validate`（不过就恢复备份）。Gateway 监视这个文件，agents.* 热加载，不用重启。
     名单从一个 agent 变成多个时，同一次写入里把原来那个 agent 默认管着的写明归它（claim_ambient），不然 validate 不过。
  3. server.json 的 agent_workspaces 加一项（对话路由、记忆页靠它）。
修改（app 里编辑 Agent，PATCH /api/groups/{id}）：
  - 名字 / 职责 → IDENTITY.md 里 <!-- mousse:role --> … <!-- /mousse:role --> 这一段整段换掉（write_role），段外的字一个字节都不动：
    手写的 IDENTITY.md、agent 自己写进去的内容都留着。先备份到 backup_dir。
  - 模型 → openclaw.json 的 agents.entries.<id>.model（set_model），和第 2 步同一套备份 + 校验 + 失败恢复。
删除（remove）反过来：条目去掉、agent_workspaces 去掉、workspace 整个移到 <openclaw_home>/archive/（记忆永远不删）。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from config import settings
from i18n import L

# 图标键 → 新 Agent 的 IDENTITY.md 里的 emoji。app 认得的图标就是这些；服务端只要求是小写字母和连字符（data.py），不认得的 emoji 用 ✨
ICON_EMOJI = {"moon": "🌙", "dumbbell": "🏋️", "utensils": "🥗", "book": "📚", "wallet": "💷", "briefcase": "💼", "heart": "❤️‍🩹", "plane": "✈️",
              "coffee": "☕", "music": "🎵", "camera": "📷", "code": "💻", "cart": "🛒", "home": "🏠", "car": "🚗", "paw": "🐾",
              "leaf": "🌿", "gamepad": "🎮", "palette": "🎨", "globe": "🌍", "graduation": "🎓", "lightbulb": "💡", "trophy": "🏆", "pill": "💊"}
# Agent 的颜色（groups.color）。NULL = app 的默认色
COLORS = ("cyan", "gold", "green", "purple", "pink", "orange")
# IDENTITY.md 里归 app 管的那一段的首尾标记
ROLE_START, ROLE_END = "<!-- mousse:role -->", "<!-- /mousse:role -->"
# openclaw.json 的 channels 下不是渠道的键（OpenClaw 的 CHANNEL_CONFIG_METADATA_KEYS）
CHANNEL_META_KEYS = ("defaults", "modelByChannel")
# 读改写 openclaw.json / IDENTITY.md 的都排队：两个请求同时读改写，后写的会冲掉先写的。
# RLock：编辑 Agent 时 data.py 拿着它走完「写 IDENTITY.md → 写模型 → 失败就撤销」，里面的函数还能再拿
edit_lock = threading.RLock()


class ProvisionError(Exception):
    pass


class NoEntry(ProvisionError):
    """openclaw.json 里没有 agents.entries.<id>：这个 id 没有自己的 OpenClaw 配置（比如 main，或借用 main 的旧 Group）。"""


def openclaw_bin() -> str:
    return shutil.which(settings.openclaw_bin) or settings.openclaw_bin


def clean_output(p: subprocess.CompletedProcess) -> str:
    lines = [ln for ln in ((p.stdout or "") + "\n" + (p.stderr or "")).splitlines() if ln.strip() and not ln.startswith("[agents/harness]")]
    return "\n".join(lines)[-400:]


def validate_config() -> None:
    try:
        p = subprocess.run([openclaw_bin(), "config", "validate"], capture_output=True, text=True, timeout=60, check=False)  # noqa: S603
    except (OSError, subprocess.SubprocessError) as e:
        raise ProvisionError(L(f"跑不了 openclaw config validate：{e}", f"Couldn't run openclaw config validate: {e}")) from e
    if p.returncode != 0:
        raise ProvisionError(L(f"openclaw config validate 不通过：{clean_output(p)}", f"openclaw config validate failed: {clean_output(p)}"))


def edit_openclaw_json(change: Callable[[dict], bool], tag: str) -> bool:
    """改 openclaw.json 的唯一入口：读 → change(data) 就地改（返回 False = 没东西要改，什么都不写）→ 备份 → 原子写入 →
    `openclaw config validate`，不通过就恢复备份。返回是否写了。
    整份按 JSON 重写：缩进 2、不转义中文、结尾换行跟原文件走，和 OpenClaw 自己写出来的格式一样，所以 change 没碰的地方不变。
    不用 `openclaw config patch`：它的 dry-run 会对整份配置做模型引用解析，环境稍有不顺就整个拒绝。Gateway 会自己热加载这个文件。"""
    path = settings.openclaw_json
    with edit_lock:
        try:
            text = path.read_text(encoding="utf8")
            data = json.loads(text)
        except (OSError, ValueError) as e:
            raise ProvisionError(L(f"读不了 {path}：{e}", f"Couldn't read {path}: {e}")) from e
        if not change(data):
            return False
        backup = backup_openclaw_json(tag)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + ("\n" if text.endswith("\n") else ""), encoding="utf8")
        shutil.copymode(path, tmp)
        tmp.replace(path)
        try:
            validate_config()
        except ProvisionError:
            if backup:
                shutil.copy2(backup, path)
            raise
        return True


def write_entry(agent_id: str, entry: dict | None, tag: str) -> None:
    """整条写 openclaw.json 的 agents.entries.<id>（None = 删），走 edit_openclaw_json。
    加进去的是第二个 agent 时，同一次写入里把名单改成多 agent 的写法（claim_ambient）。"""
    def change(data: dict) -> bool:
        ag = data.setdefault("agents", {})
        if entry is not None and not ag.get("entries") and "list" not in ag and ag.get("ownership") != "explicit":
            ag["entries"] = {"main": {}}  # 没写名单 = 只有一个隐含的 main（OpenClaw 读配置时也这么补）；不补上，新 Agent 就成了唯一的 agent
        entries = ag.setdefault("entries", {})
        if entry is None:
            if agent_id not in entries:
                return False
            del entries[agent_id]
            if len(entries) <= 1:
                ag.pop("ownership", None)  # 又只剩一个：和 OpenClaw 自己删 agent 一样去掉（再加 agent 时 claim_ambient 重新写上）
        else:
            sole = next(iter(entries)) if len(entries) == 1 and agent_id not in entries else None
            entries[agent_id] = entry
            if sole:
                claim_ambient(data, sole)
        return True
    edit_openclaw_json(change, tag)


def claim_ambient(data: dict, owner: str) -> None:
    """名单刚从一个 agent（owner）变成多个时（write_entry 刚加了第二个），把 owner 默认管着的写明归它，就地改 data。
    OpenClaw（查过 2026.8.2 到 2026.9.7）多 agent 的名单要写明 agents.ownership = "explicit"（或者一个已退役的 default: true 标记），
    不然 `openclaw config validate` 不过：新装的 OpenClaw 在 app 里第一次新建 Agent 就是这样失败回滚的。写明以后没有「唯一的 agent」兜底，
    没写归属的就停：渠道消息报 AGENT_SELECTION_REQUIRED、heartbeat 不跑、不带 --agent 的命令和没指定 agent 的 cron 没人接。
    OpenClaw 自己改配置（`openclaw config set`、`openclaw agents add`）在这一步会把这些写成原来那个 agent 的；我们不走那条路
    （见 edit_openclaw_json），照着做一样的（和 2026.9.6 的 `config set` 写出来的逐项对过，2026.9.7 这段没变）：
      - 没写 ownership 的写上 "explicit"，去掉 default 标记；
      - owner 没写 workspace 的，写上它一直在用的那个（agents.defaults.workspace，没有就 <openclaw_home>/workspace）；
      - channels 里没关掉的渠道，还没有整条渠道（accountId "*"）的绑定的，各加一条绑到 owner；
      - agents.defaults 的 heartbeat（哪儿都没配过 heartbeat 时）、systemAgent、authInheritance（owner 不叫 main 时）、
        sessionStore（session.store 是一个固定文件时），还有 talk：没写 agentId 的写成 owner。
    只看得见写在 openclaw.json 里的渠道：只靠环境变量或登录状态开着的渠道，要自己加绑定（`openclaw doctor` 会报出来）。"""
    ag, aid = data["agents"], owner.strip().lower()
    entries = ag["entries"]
    if "ownership" not in ag:
        ag["ownership"] = "explicit"
        if any(isinstance(e, dict) and e.get("default") is True for e in entries.values()):
            for e in entries.values():
                if isinstance(e, dict):
                    e.pop("default", None)
    d = ag.get("defaults", {})
    mine = entries[owner]
    if isinstance(mine, dict) and ("workspace" not in mine or isinstance(mine["workspace"], str) and not mine["workspace"].strip()):
        ws = d.get("workspace") if isinstance(d, dict) else None
        mine["workspace"] = ws.strip() if isinstance(ws, str) and ws.strip() else str(settings.openclaw_home / "workspace")
    bindings, channels = data.get("bindings"), data.get("channels")
    if (bindings is None or isinstance(bindings, list)) and isinstance(channels, dict):
        routes = [b for b in bindings or [] if isinstance(b, dict) and b.get("type") != "acp"]
        ids = sorted({k.strip().lower() for k, v in channels.items()
                      if k.strip() and k.strip() not in CHANNEL_META_KEYS and not (isinstance(v, dict) and v.get("enabled") is False)})
        new = [{"agentId": aid, "match": {"channel": c, "accountId": "*"}} for c in ids if not any(channel_wide(b, c) for b in routes)]
        if new:
            data["bindings"] = [*(bindings or []), *new]
    if isinstance(d, dict):
        def unset(k: str) -> bool:
            return k not in d or isinstance(d[k], dict) and "agentId" not in d[k]
        session = data.get("session")
        store = session.get("store") if isinstance(session, dict) else None
        claims = [k for k, yes in (
            ("heartbeat", "heartbeat" not in d and not any(isinstance(e, dict) and e.get("heartbeat") not in (None, False, 0, "") for e in entries.values())),
            ("systemAgent", unset("systemAgent")),
            ("authInheritance", aid != "main" and unset("authInheritance")),
            ("sessionStore", isinstance(store, str) and bool(store.strip()) and "{agentId}" not in store and unset("sessionStore")),
        ) if yes]
        for k in claims:
            d[k] = {**(d[k] if isinstance(d.get(k), dict) else {}), "agentId": aid}
        if claims:
            ag["defaults"] = d
    if "talk" not in data:
        data["talk"] = {"agentId": aid}
    elif isinstance(data["talk"], dict) and "agentId" not in data["talk"]:
        data["talk"]["agentId"] = aid


def channel_wide(binding: dict, channel: str) -> bool:
    """这条绑定是不是把一整条渠道（所有账号）交给了一个 agent：accountId "*"，没有 peer / guildId / teamId / roles。"""
    m = binding.get("match")
    if not isinstance(m, dict) or not isinstance(m.get("channel"), str) or m["channel"].strip().lower() != channel:
        return False
    filled = [v for v in (m.get("guildId"), m.get("teamId")) if isinstance(v, str) and v.strip()]
    return (isinstance(m.get("accountId"), str) and m["accountId"].strip() == "*" and "peer" not in m and not filled
            and not (isinstance(m.get("roles"), list) and m["roles"]))


def entry_of(agent_id: str) -> dict | None:
    """openclaw.json 里这个 agent 的条目（agents.entries.<id>）；没有 = None，文件读不了 → ProvisionError。"""
    path = settings.openclaw_json
    try:
        data = json.loads(path.read_text(encoding="utf8"))
    except (OSError, ValueError) as e:
        raise ProvisionError(L(f"读不了 {path}：{e}", f"Couldn't read {path}: {e}")) from e
    entry = ((data.get("agents") or {}).get("entries") or {}).get(agent_id)
    return entry if isinstance(entry, dict) else None


def set_model(agent_id: str, model: str) -> bool:
    """Agent 的默认模型：只改 agents.entries.<id>.model，openclaw.json 别的地方一概不动；备份、校验、不过就恢复（edit_openclaw_json）。
    - 已经是对象 {primary, fallbacks…}：只换 primary，回退链原样留着（OpenClaw 自己改模型也是这样）。
    - 是字符串（严格模式，不回退）：换成新的字符串，还是严格模式。
    - 没写（跟着 agents.defaults.model 走）：写 {primary, fallbacks}，fallbacks 抄一份 defaults 的回退链。
      只写 primary 的话，OpenClaw 会把这个 agent 当成严格模式、主模型出错就不再回退。
    新模型就是它现在实际在用的 → 不写，返回 False。没有这个条目 → NoEntry。"""
    def change(data: dict) -> bool:
        ag = data.get("agents") or {}
        entry = (ag.get("entries") or {}).get(agent_id)
        if not isinstance(entry, dict):
            raise NoEntry(L(f"openclaw.json 里没有 agents.entries.{agent_id}：它没有自己的 OpenClaw 配置，默认模型改不了",
                            f"openclaw.json has no agents.entries.{agent_id}: it has no OpenClaw entry of its own, so its default model can't be set"))
        cur = entry.get("model")
        if isinstance(cur, dict):
            if cur.get("primary") == model:
                return False
            cur["primary"] = model
        elif isinstance(cur, str) and cur:
            if cur == model:
                return False
            entry["model"] = model
        else:
            dflt = (ag.get("defaults") or {}).get("model")
            primary = dflt.get("primary") if isinstance(dflt, dict) else dflt if isinstance(dflt, str) else None
            if primary == model:
                return False
            new: dict = {"primary": model}
            if isinstance(dflt, dict) and isinstance(dflt.get("fallbacks"), list):
                new["fallbacks"] = [m for m in dflt["fallbacks"] if m != model]
            entry["model"] = new
        return True
    return edit_openclaw_json(change, f"model-{agent_id}")


def backup_copy(src: Path, name: str) -> Path:
    """src 复制到 backup_dir/<name>-<时间>；同一秒里再备份就加 -2、-3，不覆盖更早的那份。"""
    settings.backup_dir.mkdir(parents=True, exist_ok=True)
    first = settings.backup_dir / f"{name}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    dst, n = first, 1
    while dst.exists():
        n += 1
        dst = first.with_name(f"{first.name}-{n}")
    shutil.copy2(src, dst)
    return dst


def backup_openclaw_json(tag: str) -> Path | None:
    src = settings.openclaw_json
    if not src.is_file():
        return None
    return backup_copy(src, f"openclaw.json.pre-{tag}")


def agents_md(agent_id: str, name: str, purpose: str) -> str:
    """按请求的语言写（英文用户的 Agent 拿到英文的规则）。【自动触发】是系统发的标记，两种语言都原样保留。"""
    app = settings.app_name
    digest = settings.openclaw_home / "shared/digest" / agent_id
    return L(f"""# AGENTS.md — {app} · {name}（Agent）

> {datetime.now(settings.tz).strftime('%Y-%m-%d')} 在 app 里建。你是 {app} 在「{name}」这一块的分身：独立工作区、独立记忆。主对话（main）是接待台，会把属于这一块的问题转给你。

## 职责

{purpose or '（还没写。第一次对话时问清楚这一块要管什么，然后把结论写进 MEMORY.md。）'}

用户在 app 里改过名字或职责的话，新的写在 IDENTITY.md 的「职责」一节；和这里不一致时，以那里为准。

不归你管的事：一句话告诉用户去主对话或对应的 Agent 说。

## 每次会话

- `SOUL.md`、`USER.md`、`MEMORY.md` 已注入。用户的完整档案在 `{settings.profile}`，`memory_search` 可查。
- 今天的流水在 `memory/YYYY-MM-DD.md`；上一天的日结在前一天文件末尾，开会话时看一眼。
- **要问用户的问题写在回复里，不要用 ask_user 之类等待输入的工具**：app 的通道没人能回答工具里的提问，会一直卡住。
- app 里这些分身叫 Agent。跟用户说话用「Agent」。

## 自动触发

消息以「【自动触发】」开头的，不是用户在说话，是系统按时间点发的。不要提问，直接做该做的事，回复两行以内。

## 日结（收到「【自动触发】日结」时）

1. 把今天的结论写到 `memory/YYYY-MM-DD.md` 末尾一节 `## 日结`：要点、用户说过的事、明天要盯的。5–10 行。
2. 同一段再写一份到 `{digest}/YYYY-MM-DD.md`。
3. 值得长期记住的写进 `MEMORY.md`，就地改，不追加矛盾条目。
4. 回复一行"日结好了"。

## 记忆 / 安全 / 时间

- 流水进 `memory/YYYY-MM-DD.md`，结论进 `MEMORY.md`；结构化数据走工具脚本。
- 不外泄私人数据；用户的数据只出现在给用户的回复里。
- 服务器时间可能不是用户所在地，报时按 `USER.md` 的时区换算。
""", f"""# AGENTS.md — {app} · {name} (Agent)

> Created in the app on {datetime.now(settings.tz).strftime('%Y-%m-%d')}. You are {app}'s dedicated agent for "{name}", with your own workspace and your own memory. The main chat (main) is the front desk: it passes questions that belong to this area on to you.

## Your job

{purpose or '(Not written yet. In your first conversation, find out what this area should cover, then write the answer into MEMORY.md.)'}

If the user changes your name or role in the app, the new version goes into the Role section of IDENTITY.md; where the two differ, that section wins.

For anything outside your area, tell the user in one sentence to take it to the main chat or the right Agent.

## Every session

- `SOUL.md`, `USER.md` and `MEMORY.md` are already loaded. The user's full profile is at `{settings.profile}`; `memory_search` can search it.
- Today's running log is `memory/YYYY-MM-DD.md`. The previous day's daily digest is at the end of that day's file: glance at it when a session starts.
- **Ask the user questions in your reply, never with ask_user or any other tool that waits for input**: nobody can answer a tool's question through the app, so it would hang forever.
- In the app these dedicated agents are called Agents. Say "Agent" when talking to the user.

## Automatic triggers

A message that starts with "【自动触发】" (automatic trigger) isn't the user talking: the system sent it at a scheduled time. Don't ask questions. Just do what needs doing and reply in two lines or fewer.

## Daily digest (when a "【自动触发】" message asks for the daily digest, 日结)

1. Write today's conclusions into a final `## Daily digest` section at the end of `memory/YYYY-MM-DD.md`: key points, what the user told you, what to keep an eye on tomorrow. 5–10 lines.
2. Write the same section to `{digest}/YYYY-MM-DD.md` as well.
3. Put anything worth remembering long term into `MEMORY.md`. Edit it in place; don't append entries that contradict it.
4. Reply with one line: "Daily digest done".

## Memory / security / time

- Running notes go in `memory/YYYY-MM-DD.md`, conclusions in `MEMORY.md`; structured data goes through the tool scripts.
- Never leak private data. The user's data only appears in your replies to the user.
- Server time may not be the user's local time; convert times to the timezone in `USER.md`.
""")


def identity_md(name: str, icon: str, purpose: str = "") -> str:
    """新 Agent 的 IDENTITY.md：身份几行 + 末尾 app 管的职责段（以后在 app 里改名字 / 职责只换那一段）。"""
    app = settings.app_name
    return L(f"""# IDENTITY.md - Who Am I?

- **Name:** {app} · {name}
- **Creature:** {app} 的一个分身，专管「{name}」这一块。同一个 {app}，同一个脾气，只是范围小、记得深。
- **Vibe:** 说人话，不废话，直接。
- **Emoji:** {ICON_EMOJI.get(icon, '✨')}
- **Avatar:** 与主 {app} 相同。
""", f"""# IDENTITY.md - Who Am I?

- **Name:** {app} · {name}
- **Creature:** A dedicated version of {app} that looks after "{name}". Same {app}, same temperament, just a narrower scope and a deeper memory.
- **Vibe:** Plain-spoken, no filler, direct.
- **Emoji:** {ICON_EMOJI.get(icon, '✨')}
- **Avatar:** Same as the main {app}.
""") + "\n" + role_block(name, purpose) + "\n"


def role_block(name: str, purpose: str) -> str:
    """IDENTITY.md 里归 app 管的一段：Agent 在 app 里的名字和职责，首尾是 ROLE_START / ROLE_END，每次整段替换。
    OpenClaw 逐行按「标签: 值」解析 IDENTITY.md（Name / Emoji / Vibe / Theme…，后出现的算数），所以这一段不写「Name:」这类行，
    职责每行前面加「> 」：用户写的「Theme: …」这种行不会被当成身份字段。名字和职责里出现的标记本身去掉，免得下次找错段。"""
    def clean(s: str) -> str:
        return s.replace(ROLE_START, "").replace(ROLE_END, "")
    name = clean(name)
    quoted = "\n".join(f"> {ln}" if ln else ">" for ln in (x.rstrip() for x in clean(purpose).strip().splitlines()))
    return L(f"""{ROLE_START}
## 职责（在 app 里改的，以这里为准）

在 app 里叫「{name}」。名字和职责是用户在 app 里定的：和上面或 AGENTS.md 冲突时按这里的来，不冲突的细节照旧。

{quoted or '（还没写。第一次对话时问清楚这一块要管什么。）'}
{ROLE_END}""", f"""{ROLE_START}
## Role (set in the app; this wins)

In the app this Agent is called "{name}". The user set this name and role in the app. Where they conflict with the lines above or with AGENTS.md, this section wins; details that don't conflict still apply.

{quoted or '(Not written yet. In your first conversation, find out what this area should cover.)'}
{ROLE_END}""")


def write_role(agent_id: str, ws: Path, name: str, purpose: str) -> Callable[[], None]:
    """把 ws/IDENTITY.md 的职责段换成新的名字和职责，返回一个撤销函数（后面的步骤失败时把文件放回原样）。
    有 ROLE_START … ROLE_END 就只换这一段（多于一段时换最后一段）；没有就追加在文件末尾，前面空一行；文件不存在就新建。
    按字节读写、不动换行符：段外的内容（手写的、agent 自己写的）逐字节不变。改之前备份到 backup_dir/IDENTITY.md.pre-edit-<id>-<时间>。"""
    path = ws / "IDENTITY.md"
    if path.is_symlink():
        path = path.resolve()
    block = role_block(name, purpose)
    with edit_lock:
        try:
            old = path.read_bytes().decode("utf8") if path.exists() else None
        except (OSError, UnicodeDecodeError) as e:
            raise ProvisionError(L(f"读不了 {path}：{e}", f"Couldn't read {path}: {e}")) from e
        if old is None:
            new = block + "\n"
        else:
            start = old.rfind(ROLE_START)
            end = old.find(ROLE_END, start) if start >= 0 else -1
            if end >= 0:
                new = old[:start] + block + old[end + len(ROLE_END):]
            else:
                new = old + ("\n" if old.endswith("\n") else "\n\n" if old else "") + block + "\n"
        if new == old:
            return lambda: None
        tmp = path.with_name(path.name + ".tmp")
        try:
            backup = backup_copy(path, f"IDENTITY.md.pre-edit-{agent_id}") if old is not None else None
            tmp.write_bytes(new.encode("utf8"))
            if old is not None:
                shutil.copymode(path, tmp)
            tmp.replace(path)
        except OSError as e:
            tmp.unlink(missing_ok=True)  # 别在 agent 的工作区里留半个临时文件
            raise ProvisionError(L(f"写不了 {path}：{e}", f"Couldn't write {path}: {e}")) from e

    def undo() -> None:
        with edit_lock:
            if backup:
                shutil.copy2(backup, path)
            else:
                path.unlink(missing_ok=True)
    return undo


def memory_md(name: str) -> str:
    return L(f"""# MEMORY.md — {settings.app_name} · {name}

> {datetime.now(settings.tz).strftime('%Y-%m-%d')} 建。只放提炼后的结论，日结时维护。

## 观察到的规律

- （待积累）

## 规则与偏好（本块）

- （待积累）

## 当前状态

- {datetime.now(settings.tz).strftime('%Y-%m-%d')}：Agent 新建。
""", f"""# MEMORY.md — {settings.app_name} · {name}

> Created {datetime.now(settings.tz).strftime('%Y-%m-%d')}. Distilled conclusions only; kept up to date at each daily digest.

## Patterns noticed

- (none yet)

## Rules and preferences (this area)

- (none yet)

## Current state

- {datetime.now(settings.tz).strftime('%Y-%m-%d')}: Agent created.
""")


def workspace_path(agent_id: str) -> Path:
    return settings.openclaw_home / f"workspace-{agent_id}"


def build_workspace(agent_id: str, name: str, purpose: str, icon: str) -> Path:
    ws = workspace_path(agent_id)
    if ws.exists() and any(ws.iterdir()):
        raise ProvisionError(L(f"{ws} 已经存在且不为空", f"{ws} already exists and isn't empty"))
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "AGENTS.md").write_text(agents_md(agent_id, name, purpose), encoding="utf8")
    (ws / "IDENTITY.md").write_text(identity_md(name, icon, purpose), encoding="utf8")
    (ws / "MEMORY.md").write_text(memory_md(name), encoding="utf8")
    (ws / "memory").mkdir(exist_ok=True)
    for fn in ("SOUL.md", "USER.md"):
        src = settings.workspace / fn
        if src.is_file():
            shutil.copy2(src, ws / fn)
    skills = settings.workspace / "skills"
    if skills.is_dir():
        os.symlink(skills, ws / "skills")
    (settings.openclaw_home / "shared/digest" / agent_id).mkdir(parents=True, exist_ok=True)
    return ws


def provision(agent_id: str, name: str, purpose: str, icon: str = "moon", skills: list[str] | None = None) -> Path:
    if not agent_id.replace("-", "").isalnum() or not agent_id.islower():
        raise ProvisionError(L("agent id 只能是小写字母、数字和连字符", "agent id may only contain lowercase letters, digits and hyphens"))
    entry: dict = {"workspace": str(workspace_path(agent_id))}
    allow = skills if skills is not None else settings.agent_default_skills
    if allow:
        entry["skills"] = list(allow)
    ws = build_workspace(agent_id, name, purpose, icon)
    try:
        write_entry(agent_id, entry, f"agent-{agent_id}")
        settings.set_agent_workspace(agent_id, ws)
    except Exception:
        shutil.rmtree(ws, ignore_errors=True)
        raise
    time.sleep(2)  # 给 Gateway 热加载一点时间
    return ws


def remove(agent_id: str) -> Path | None:
    """去掉 OpenClaw 条目和路由；workspace 移到 archive/，返回归档路径。没 workspace 的（只在 groups 表里的旧 Group）只删路由。"""
    if agent_id == "main":
        raise ProvisionError(L("main 不能删", "main can't be deleted"))
    if agent_id in settings.agent_workspaces:
        write_entry(agent_id, None, f"remove-{agent_id}")
        settings.set_agent_workspace(agent_id, None)
    ws = workspace_path(agent_id)
    if not ws.exists():
        return None
    archive = settings.openclaw_home / "archive"
    archive.mkdir(parents=True, exist_ok=True)
    dst = archive / f"workspace-{agent_id}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    shutil.move(str(ws), str(dst))
    return dst
