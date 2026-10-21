#!/usr/bin/env python3
"""第一次打开的三步，在终端里走（内核第 9 步「通用的选择层」，2026-10-17）：没有 app、或者在服务器上更顺手的人也能配。和 /api/setup
是同一套：直接调 engine/setup_api.py 的那几个接口函数，写 server.json 之前一样先备份，密钥一样存进 secrets.env（0600），一样不回显。
服务器不用开着，改完也不用重启（用到的地方每次现读）。

  python3 setup_ctl.py                       三步：选模型 → 搜网页 → 记忆（回车 = 方括号里的默认；Ctrl-C 随时停，做完的那几步已经存好了）
  python3 setup_ctl.py status                每一层现在怎么样（同 app 的总览）；还没有能用的模型时退出码 3
  python3 setup_ctl.py models                服务商：目录、连没连、本机探到的、命令行登录
  python3 setup_ctl.py add <id> [--region cn] [--url URL] [--name NAME] [--key-stdin]
                                             连一家：密钥在终端里问（不回显），或者 --key-stdin 从标准输入读；目录里没有的 id = 自定义地址（要 --url）
  python3 setup_ctl.py test <id> [型号]      测一下（列模型，列不了发一句最小的请求；带型号 = 测这个型号，列不了模型的那几家测通了就能选它）
  python3 setup_ctl.py remove <id>           删掉 app 里存的密钥（自定义的连同那一项）
  python3 setup_ctl.py use <chat | background | writing | outsider> <provider/model | default | off>   按用途选模型
  python3 setup_ctl.py search <id | off | auto> [--key-stdin] [--url URL]   搜网页用哪家（要密钥的顺手存）
  python3 setup_ctl.py memory <sqlite | markdown> [文件夹]   世界树放哪（先只支持第一次选）
  python3 setup_ctl.py embeddings <id | off> [--model M]   按意思搜用哪家
  python3 setup_ctl.py usage [天数]           用量（登录了 Grok 订阅还有这一段的额度）
  python3 setup_ctl.py grok [login | logout]  你自己的 Grok 订阅（SuperGrok / X Premium+，engine/grok_plan.py）：不写 = 看状态和额度；login = 打出网址
                                             和码，在手机或电脑上点同意，这里等着；logout = 退出（撤销、删掉这台服务器上的令牌）
  python3 setup_ctl.py grok background <on | off>   后台任务、定时任务、心跳能不能花你的 Grok 订阅（默认不能）
环境变量 MOUSSE_NONINTERACTIVE=1（claw 替人装时）：三步不问，只打印现在的样子和接下来怎么配。
"""
from __future__ import annotations

import asyncio
import getpass
import os
import sys
from collections.abc import Callable

from i18n import L

CMD = "~/.openmousse/venv/bin/python ~/.openmousse/repo/server/setup_ctl.py"  # 给人看的：在哪个目录都能跑的写法（安装器装的位置）


def usage() -> str:
    return L(__doc__, """The first-run steps, in a terminal (step 9 of the built-in engine, "a general chooser"): for people without the app, or who'd
rather do it on the server. It's the same as /api/setup: it calls the same functions (engine/setup_api.py), backs up server.json
before writing, keeps keys in secrets.env (0600) and never prints them. The server needn't be running, and nothing needs a restart.

  python3 setup_ctl.py                       three steps: model → web search → memory (Enter = the default in brackets; Ctrl-C stops any time,
                                             the steps you finished are already saved)
  python3 setup_ctl.py status                how each layer is doing (the app's overview); exit code 3 when there's no usable model yet
  python3 setup_ctl.py models                providers: the catalog, what's connected, local servers found, sign-ins
  python3 setup_ctl.py add <id> [--region cn] [--url URL] [--name NAME] [--key-stdin]
                                             connect one: the key is asked for in the terminal (hidden), or read from stdin with --key-stdin;
                                             an id that isn't in the catalog is a custom address (needs --url)
  python3 setup_ctl.py test <id> [MODEL]     test it (list the models, or send one tiny request if it can't list them; with a model, test
                                             that one: for providers that can't list, a model that works becomes choosable)
  python3 setup_ctl.py remove <id>           remove the key saved here (a custom one goes completely)
  python3 setup_ctl.py use <chat | background | writing | outsider> <provider/model | default | off>   choose a model per purpose
  python3 setup_ctl.py search <id | off | auto> [--key-stdin] [--url URL]   which web search to use (saves its key too)
  python3 setup_ctl.py memory <sqlite | markdown> [FOLDER]   where the memory tree lives (only the first choice for now)
  python3 setup_ctl.py embeddings <id | off> [--model M]   search by meaning with which provider
  python3 setup_ctl.py usage [DAYS]          usage (plus this period's Grok subscription allowance when signed in)
  python3 setup_ctl.py grok [login | logout]  your own Grok subscription (SuperGrok / X Premium+, engine/grok_plan.py): nothing = status
                                             and allowance; login = prints a link and a code, approve on your phone or computer while this
                                             waits; logout = signs out (revokes and removes the tokens on this server)
  python3 setup_ctl.py grok background <on | off>   whether background tasks, scheduled jobs and heartbeats may use your Grok
                                             subscription (they may not by default)
With MOUSSE_NONINTERACTIVE=1 (a claw installing for you) the steps don't ask anything: they print where things stand and what to do next.
""")


# —— 终端里问（照安装器的样子；管道里跑时从 /dev/tty 读）————————————————————————————————————

def say(msg: str = "") -> None:
    print(f"  {msg}" if msg else "")


def _tty():
    if sys.stdin.isatty():
        return sys.stdin
    try:
        return open("/dev/tty", encoding="utf8")  # noqa: SIM115 — 读一行就关
    except OSError:
        return None


def interactive() -> bool:
    if os.environ.get("MOUSSE_NONINTERACTIVE"):
        return False
    t = _tty()
    if t is None:
        return False
    if t is not sys.stdin:
        t.close()
    return True


def ask(prompt: str, default: str = "") -> str:
    """问一句，回车 = default。读不到（没有终端）= default。"""
    t = _tty()
    if t is None:
        return default
    print(f"  {prompt}" + (f" [{default}]" if default else "") + ": ", end="", flush=True)
    try:
        line = t.readline()
    finally:
        if t is not sys.stdin:
            t.close()
    if not line:
        raise EOFError
    return line.strip() or default


def secret(prompt: str) -> str:
    """问密钥：不回显（getpass 自己开 /dev/tty）。"""
    return getpass.getpass(f"  {prompt}: ").strip()


def yes(prompt: str, default: bool = False) -> bool:
    a = ask(prompt + L("（y/n）", " (y/n)"), "y" if default else "n").lower()
    return a.startswith(("y", "是", "1"))


def pick(prompt: str, options: list[tuple[str, str]], default: str = "") -> str:
    """列出来让挑：[(值, 给人看的)] → 值。可以填编号，也可以直接填值；回车 = default（可以是空 = 跳过）。"""
    for i, (_v, label) in enumerate(options, 1):
        say(f"  {i}) {label}")
    while True:
        a = ask(prompt, default)
        if not a:
            return ""
        if a.isdigit() and 1 <= int(a) <= len(options):
            return options[int(a) - 1][0]
        if a in {v for v, _ in options}:
            return a
        say(L("没有这一项，再选一次", "Not one of these; try again"))


# —— 调接口函数 ——————————————————————————————————————————————————————————————————————

def call(fn: Callable, *args, body: dict | None = None):
    """调 setup_api 的一个接口函数（不经 HTTP）。出错抛 setup_api.SetupError。"""
    from engine import setup_api as sa
    if body is not None:
        args = (*args, sa.Body(body))
    res = fn(*args)
    return asyncio.run(res) if asyncio.iscoroutine(res) else res


def show_error(e: Exception) -> None:
    say(L(f"没成：{e}", f"Didn't work: {e}"))


def show_test(r: dict) -> bool:
    if r.get("ok"):
        n = r.get("count") or 0
        say(L(f"✓ 通了（{r.get('ms', 0)} 毫秒）" + (f"，{n} 个型号" if n else ""),
              f"✓ Works ({r.get('ms', 0)} ms)" + (f", {n} model{'' if n == 1 else 's'}" if n else "")))
        return True
    say("✗ " + str((r.get("error") or {}).get("message") or L("不通", "It didn't work")))
    return False


# —— 第一步：模型 ——————————————————————————————————————————————————————————————————————

def name_of(r: dict) -> str:
    return str(r.get("name") or r.get("id"))


def connect_cloud(rows: list[dict]) -> str | None:
    """挑一家云端 → 地区 → 密钥（不回显）→ 测 → 存。→ 连上的 id。"""
    from engine import setup_api as sa
    cloud = [r for r in rows if r["kind"] == "cloud"]
    pid = pick(L("哪一家（编号或名字，回车跳过）", "Which one (number or id, Enter skips)"),
               [(r["id"], name_of(r) + (" ✓" if r["connected"] else "")) for r in cloud])
    if not pid:
        return None
    row = next(r for r in cloud if r["id"] == pid)
    region = None
    if row["regions"]:
        region = pick(L("哪个地区", "Which region"), [(g["id"], g["label"]) for g in row["regions"]], row["region"] if row["region"] != "custom" else row["regions"][0]["id"])
    if row.get("note"):
        say(row["note"])
    say(L(f"密钥在这里拿：{row['keyUrl']}", f"Get a key here: {row['keyUrl']}") if row.get("keyUrl") else "")
    url = None
    if not row.get("defaultUrl"):  # 没有默认地址的（Azure）：地址是你自己的资源
        url = ask(L("你自己的资源地址（url）", "Your own resource's address (url)"), row.get("url") or "").strip() or None
        if not url:
            say(L("没填地址，跳过", "No address; skipped"))
            return None
    key = secret(L(f"{name_of(row)} 的密钥（不回显；回车 = 用已经有的）", f"{name_of(row)} key (hidden; Enter = keep the one there is)"))
    if not key and not row["connected"]:
        say(L("没填密钥，跳过", "No key; skipped"))
        return None
    body = {**({"key": key} if key else {}), **({"region": region} if region else {}), **({"url": url} if url else {})}
    try:
        ok = show_test(call(sa.test_provider, pid, body=body))
        if not ok and not yes(L("还是存下来吗", "Save it anyway"), False):
            return None
        if body:
            res = call(sa.save_provider, pid, body=body)
            say(L("存好了", "Saved") + (L(f"（server.json 备份在 {res['backup']}）", f" (server.json backed up as {res['backup']})") if res.get("backup") else ""))
            if res.get("auto"):
                say(L(f"只有这一家：主对话先用 {res['auto']['chat']}", f"It's the only one: the main chat uses {res['auto']['chat']} for now"))
    except sa.SetupError as e:
        show_error(e)
        return None
    return pid


def connect_local(rows: list[dict]) -> str | None:
    from engine import setup_api as sa
    local = [r for r in rows if r["kind"] == "local"]
    opts = [(r["id"], name_of(r) + (L(f"（在跑，{len(r['models'])} 个模型）", f" (running, {len(r['models'])} models)") if r["detected"]
                                    else L(f"（没探到；起它：{r['start']}）", f" (not found; start it with: {r['start']})"))) for r in local]
    found = next((r["id"] for r in local if r["detected"]), "")
    pid = pick(L("哪一个（回车跳过）", "Which one (Enter skips)"), opts, found)
    if not pid:
        return None
    row = next(r for r in local if r["id"] == pid)
    url = ask(L("地址", "Address"), row["url"])
    try:
        ok = show_test(call(sa.test_provider, pid, body={"url": url}))
        if not ok:
            return None
        res = call(sa.save_provider, pid, body={"url": url} if url.rstrip("/") != row["defaultUrl"].rstrip("/") else {})
        if res.get("auto"):
            say(L(f"只有这一家：主对话先用 {res['auto']['chat']}", f"It's the only one: the main chat uses {res['auto']['chat']} for now"))
    except sa.SetupError as e:
        show_error(e)
        return None
    return pid


def show_logins(rows: list[dict]) -> None:
    for r in rows:
        if r["kind"] != "login":
            continue
        state = L("能用 ✓", "ready ✓") if r["ready"] else L("没连上", "not connected")
        say(f"{name_of(r)}: {state}")
        if not r["ready"] and r["id"] == "chatgpt":  # 目录里的说法是给 app 的按钮写的；终端里走接口
            say("  " + L("ChatGPT 套餐不在这里连：照 server/README.zh-CN.md 的「用你自己的 ChatGPT 套餐」，POST /api/engine/chatgpt/login 拿授权链接",
                         "The ChatGPT plan isn't connected here: follow \"Your ChatGPT plan\" in server/README.md (POST /api/engine/chatgpt/login gives the sign-in link)"))
        elif not r["ready"] and r["id"] == "grok":  # Grok 订阅（10/20）：设备码，终端里就能登
            if r.get("denied"):
                say("  " + str(r["denied"].get("message") or ""))
            if interactive() and yes(L("现在登录 Grok 订阅吗（打出网址和码，在手机或电脑上点同意）", "Sign in to your Grok subscription now (a link and a code to approve on your phone or computer)"), False):
                grok_login()
            else:
                say("  " + L("登录：python3 setup_ctl.py grok login", "To sign in: python3 setup_ctl.py grok login"))
        elif not r["ready"]:
            say("  " + str(r.get("how") or ""))


def connect_custom() -> str | None:
    from engine import setup_api as sa
    pid = ask(L("给它起个 id（小写字母、数字）", "An id for it (lowercase letters, digits)"), "")
    if not pid:
        return None
    url = ask(L("地址（OpenAI 兼容，写到 /v1）", "Address (OpenAI-compatible, up to /v1)"), "")
    if not url:
        return None
    key = secret(L("密钥（没有就回车）", "Key (Enter if none)"))
    body = {"url": url, **({"key": key} if key else {})}
    try:
        show_test(call(sa.test_provider, pid, body=body))
        res = call(sa.save_provider, pid, body={**body, "name": pid})
        say(L("存好了", "Saved"))
        if res.get("auto"):
            say(L(f"只有这一家：主对话先用 {res['auto']['chat']}", f"It's the only one: the main chat uses {res['auto']['chat']} for now"))
    except sa.SetupError as e:
        show_error(e)
        return None
    return pid


def choose_chat() -> None:
    """主对话用哪个（只有 claw 是 builtin 才问）。"""
    from engine import setup_api as sa
    view = call(sa.get_purposes)
    if view["claw"] != "builtin":
        return
    chat = next(p for p in view["purposes"] if p["id"] == "chat")
    refs = [c["ref"] for c in view["choices"]["models"]]
    if not refs:
        return
    rec = (chat.get("recommended") or {}).get("value")
    default = chat["current"] or rec or refs[0]
    shown = refs[:30]
    say(L(f"主对话用哪个模型（现在：{chat['current'] or '还没选'}）", f"Which model for the main chat (now: {chat['current'] or 'none yet'})"))
    for i, r in enumerate(shown, 1):
        say(f"  {i}) {r}" + (L("（推荐）", " (recommended)") if r == rec else ""))
    if len(refs) > len(shown):
        say(L(f"  ……还有 {len(refs) - len(shown)} 个，直接写 provider/model 也行", f"  …{len(refs) - len(shown)} more; you can also type provider/model"))
    a = ask(L("编号或 provider/model", "Number or provider/model"), default)
    ref = shown[int(a) - 1] if a.isdigit() and 1 <= int(a) <= len(shown) else a
    if ref and ref != chat["current"]:
        try:
            call(sa.put_purposes, body={"chat": ref})
            say(L(f"主对话用 {ref}", f"The main chat uses {ref}"))
        except sa.SetupError as e:
            show_error(e)


def step_models() -> None:
    from engine import setup_api as sa
    print(L("1/3 选模型", "1/3 Choose a model"))
    first = True
    while True:
        rows = call(sa.providers, True)
        on = [name_of(r) for r in rows if r.get("connected") or (r["kind"] == "login" and r.get("ready") and r["role"] == "chat")]
        say(L("已经连上的：", "Connected: ") + (L("、", ", ").join(on) if on else L("还没有", "none yet")))
        found = [name_of(r) for r in rows if r["kind"] == "local" and r["detected"]]
        # 头一回、什么都没连：回车 = 云端；之后回车 = 不再加了（手里没有密钥、跳过了的人也走得出去，不用 Ctrl-C）
        start = first and not on
        first = False
        how = pick(L("怎么跑模型" if start else "怎么跑模型（回车 = 不再加了）", "How to run models" if start else "How to run models (Enter = done adding)"), [
            ("cloud", L("云端：填一家的 API 密钥", "Cloud: an API key from a provider")),
            ("local", L("这台电脑上跑的（Ollama、LM Studio……）", "On this computer (Ollama, LM Studio…)") + (L(f"：探到 {'、'.join(found)}", f": found {', '.join(found)}") if found else "")),
            ("login", L("复用已经登录的 Claude Code / ChatGPT，或者登录 Grok 订阅", "Reuse Claude Code / ChatGPT you're signed in to, or sign in to Grok")),
            ("custom", L("自己填一个 OpenAI 兼容的地址", "Your own OpenAI-compatible address"))], "cloud" if start else "")
        if not how:
            break
        if how == "cloud":
            connect_cloud(rows)
        elif how == "local":
            connect_local(rows)
        elif how == "login":
            show_logins(rows)
        else:
            connect_custom()
    choose_chat()


# —— 第二步：搜网页 ——————————————————————————————————————————————————————————————————

def step_search() -> None:
    from engine import setup_api as sa
    print(L("2/3 搜网页", "2/3 Web search"))
    view = call(sa.get_search)
    say(L(f"现在：{view['provider'] or ('关着' if view['off'] else '没配')}", f"Now: {view['provider'] or ('off' if view['off'] else 'not set up')}"))
    opts = [(r["id"], r["name"] + (" ✓" if r["connected"] else "") + (f" · {r['free']}" if r.get("free") else "")) for r in view["providers"]]
    opts.append(("off", L("不用", "Don't use web search")))
    sid = pick(L("用哪家（回车 = 不改）", "Which one (Enter = keep as is)"), opts)
    if not sid:
        return
    try:
        if sid == "off":
            call(sa.put_search, body={"enabled": False})
            say(L("搜索关了", "Web search is off"))
            return
        row = next(r for r in view["providers"] if r["id"] == sid)
        if row.get("note"):
            say(row["note"])
        body: dict = {}
        if row["selfHosted"]:
            body["url"] = ask(L("你的 SearXNG 地址", "Your SearXNG address"), row.get("url") or "")
        elif not row["keyless"]:
            if row.get("keyUrl"):
                say(L(f"密钥在这里拿：{row['keyUrl']}", f"Get a key here: {row['keyUrl']}"))
            k = secret(L("密钥（不回显；回车 = 用已经有的）", "Key (hidden; Enter = keep the one there is)"))
            if k:
                body["key"] = k
            elif not row["connected"]:
                say(L("没填密钥，跳过", "No key; skipped"))
                return
        if body or row["keyless"]:
            show_test(call(sa.test_search, sid, body=body))
        if body:
            call(sa.save_search, sid, body=body)
        call(sa.put_search, body={"enabled": True, "provider": sid})
        say(L(f"上网搜用 {row['name']}", f"Web search uses {row['name']}"))
    except sa.SetupError as e:
        show_error(e)


# —— 第三步：记忆 ——————————————————————————————————————————————————————————————————————

def step_memory() -> None:
    from engine import setup_api as sa
    print(L("3/3 记忆", "3/3 Memory"))
    view = call(sa.get_memory)
    t = view["tree"]
    if t["initialized"]:
        where = L("本机 SQLite", "SQLite on this machine") if t["storage"] == "sqlite" else t["path"]
        say(L(f"世界树在 {where}，{t['memories'] or 0} 条", f"The memory tree is in {where}, {t['memories'] or 0} memories"))
    if t["canChoose"]:
        how = pick(L("世界树放哪（回车 = 不改）" if t["initialized"] else "世界树放哪", "Where the memory tree lives (Enter = keep as is)" if t["initialized"] else "Where the memory tree lives"),
                   [("sqlite", L("这台电脑上（SQLite）", "On this computer (SQLite)")),
                    ("markdown", L("一个 Markdown 文件夹（可以是 Obsidian 库里的一个文件夹）", "A Markdown folder (a folder in an Obsidian vault works)"))],
                   "" if t["initialized"] else "sqlite")
        if how:
            body = {"storage": how}
            if how == "markdown":
                body["path"] = ask(L("文件夹（完整路径）", "Folder (full path)"), "")
            try:
                out = call(sa.put_memory, body=body)
                t = out["tree"]
                say(L(f"好了：{t['storage']} {t['path'] or ''}", f"Done: {t['storage']} {t['path'] or ''}"))
            except sa.SetupError as e:
                show_error(e)
    elif t.get("why"):
        say(t["why"])
    import claw
    if not claw.is_builtin():
        return
    pv = call(sa.get_purposes)
    ch = pv["choices"]["embeddings"]
    cur = view["current"]
    say(L(f"按意思搜：{cur['label'] if cur else '没开（只按关键词，什么都不发出去）'}", f"Search by meaning: {cur['label'] if cur else 'off (keywords only; nothing is sent out)'}"))
    if not ch:
        return
    eid = pick(L("用哪家算向量（回车 = 不改；写 off 关掉）", "Which provider computes embeddings (Enter = keep; off turns it off)"),
               [(c["id"], c["name"] + (f" · {c['model']}" if c.get("model") else "")) for c in ch] + [("off", L("不用", "Off"))])
    if not eid:
        return
    try:
        if eid == "off":
            call(sa.put_purposes, body={"embeddings": "off"})
            return
        c = next(x for x in ch if x["id"] == eid)
        model = c.get("model") or ask(L("向量模型", "Embedding model"), (c.get("models") or [""])[0])
        show_test(call(sa.test_embeddings, body={"id": eid, "model": model}))
        call(sa.put_purposes, body={"embeddings": {"id": eid, "model": model}})
        say(L("按意思搜开了：后台慢慢补向量", "Search by meaning is on: vectors fill in in the background"))
    except sa.SetupError as e:
        show_error(e)


# —— 总览、子命令 ————————————————————————————————————————————————————————————————————————

def show_status() -> bool:
    """每一层一行。→ 是不是还没有能用的模型（status 子命令据此退出码 3，安装器认它）。"""
    from engine import setup_api as sa
    o = call(sa.overview)
    names = {"models": L("模型", "Models"), "search": L("搜索", "Search"), "memory": L("记忆", "Memory"), "embeddings": L("向量", "Embeddings"),
             "connections": L("连接", "Connections"), "hands": L("手", "Hands")}
    mark = {"ok": "✓", "external": "✓", "off": "·", "optional": "·", "missing": "✗", "warn": "!"}
    for k, layer in o["layers"].items():
        print(f"  {mark.get(layer.get('status'), '?')} {names.get(k, k)}: {layer.get('line')}")
    if o["noModel"]:
        print(L(f"  还没有能用的模型：{CMD}（三步：模型 → 搜网页 → 记忆）",
                f"  No usable model yet: {CMD} (three steps: model → web search → memory)"))
    return bool(o["noModel"])


def wizard() -> int:
    if not interactive():
        show_status()
        print(L(f"（没有终端，不问了：在服务器的终端里跑 {CMD}，或者用 add / use 这些子命令，见 --help）",
                f"(No terminal, so no questions: run {CMD} in a terminal on the server, or use subcommands such as add and use, see --help)"))
        return 0
    print(L("三步：选模型、搜网页、记忆。回车 = 方括号里的默认；Ctrl-C 随时停，做完的那几步已经存好了。",
            "Three steps: model, web search, memory. Enter = the default in brackets; Ctrl-C stops any time, finished steps are already saved."))
    try:
        step_models()
        step_search()
        step_memory()
    except (KeyboardInterrupt, EOFError):
        print()
        say(L("停了。做完的那几步已经存好了，随时再跑一次。", "Stopped. The steps you finished are saved; run this again any time."))
        return 1
    print()
    show_status()
    return 0


# —— Grok 订阅（10/20，engine/grok_plan.py）：设备码在这个终端里走，令牌存进服务器的 data_dir（服务器开着的话下一次用到就认） ——————————

async def _grok_login() -> int:
    from engine import grok_plan
    try:
        s = await grok_plan.start_login()
    except grok_plan.PlanError as e:
        print(L(f"没开始：{e}", f"Didn't start: {e}"), file=sys.stderr)
        return 1
    say(L("在手机或电脑上打开这个网址，认一下码、点同意：", "Open this link on your phone or computer, check the code and approve:"))
    say(f"  {s['url']}")
    say(L(f"  码：{s['code']}（{s['expiresAt']} 以前有效）", f"  Code: {s['code']} (valid until {s['expiresAt']})"))
    say(grok_plan.login_note())
    say(L("等你点同意……（Ctrl-C 取消）", "Waiting for you to approve… (Ctrl-C cancels)"))
    st = await grok_plan.wait_login()
    if st["status"] == "done":
        g = grok_plan.status()
        say(L(f"登录好了{'：' + g['email'] if g.get('email') else ''}", f"Signed in{': ' + g['email'] if g.get('email') else ''}"))
        return 0
    say(L(f"没登录上：{st.get('error') or st['status']}", f"Not signed in: {st.get('error') or st['status']}"))
    return 1


def grok_login() -> int:
    from engine import grok_plan
    try:
        return asyncio.run(_grok_login())
    except KeyboardInterrupt:
        grok_plan.cancel_login()
        print()
        say(L("取消了", "Cancelled"))
        return 1


def show_grok() -> None:
    from engine import grok_plan
    g = grok_plan.status()
    if not g["enabled"]:
        print(L("  Grok 订阅：关着（server.json 的 engine.grok = false）", "  Grok subscription: off (engine.grok = false in server.json)"))
        return
    if g["denied"]:
        print(L(f"  Grok 订阅：被 xAI 拒了 · {g['denied'].get('message')}", f"  Grok subscription: refused by xAI · {g['denied'].get('message')}"))
    elif g["needsReconnect"]:
        print(L("  Grok 订阅：要重新登录（python3 setup_ctl.py grok login）", "  Grok subscription: needs signing in again (python3 setup_ctl.py grok login)"))
    elif g["connected"]:
        print(L(f"  Grok 订阅：登录着{' · ' + g['email'] if g.get('email') else ''}", f"  Grok subscription: signed in{' · ' + g['email'] if g.get('email') else ''}"))
    else:
        print(L("  Grok 订阅：没登录（python3 setup_ctl.py grok login）", "  Grok subscription: not signed in (python3 setup_ctl.py grok login)"))
    if g["lastError"] and not g["denied"]:
        print(L(f"  最近一次的错：{g['lastError'].get('message')}", f"  Last error: {g['lastError'].get('message')}"))
    if g["connected"]:
        print(L("  后台任务、定时任务、心跳：" + ("能花订阅" if g["background"]["allowed"] else "不花订阅（python3 setup_ctl.py grok background on 同意）"),
                "  Background tasks, scheduled jobs, heartbeats: " + ("may use it" if g["background"]["allowed"] else
                                                                       "don't use it (python3 setup_ctl.py grok background on to allow)")))
    if g["ready"]:  # 这一段的额度：当场问一次（问不到说一声）
        q = asyncio.run(grok_plan.quota_now())
        line = grok_plan.quota_line(q)
        if line:
            print(L(f"  额度：{line}", f"  Allowance: {line}"))
        if q and q.get("error"):
            print(L(f"  （这一次没问到：{q['error']}）", f"  (couldn't check just now: {q['error']})"))


def grok(rest: list[str]) -> int:
    """grok [login | logout]：见文件开头。"""
    from engine import grok_plan
    sub = rest[0] if rest else "status"
    if sub == "login":
        return grok_login()
    if sub == "logout":
        r = asyncio.run(grok_plan.logout())
        print(L("退出了：这台服务器上的令牌删掉了", "Signed out: the tokens on this server are removed") + (f"\n  {r['note']}" if r.get("note") else ""))
        return 0
    if sub == "status":
        show_grok()
        return 0
    if sub == "background" and len(rest) == 2 and rest[1] in ("on", "off"):
        grok_plan.set_background(rest[1] == "on")
        print(L("好了：后台任务、定时任务、心跳" + ("能花你的 Grok 订阅了" if rest[1] == "on" else "不花你的 Grok 订阅"),
                "Done: background tasks, scheduled jobs and heartbeats " + ("may use your Grok subscription" if rest[1] == "on"
                                                                            else "won't use your Grok subscription")))
        return 0
    print(usage())
    return 2


def warn_invalid() -> None:
    """写完以后整份查一遍 server.json（ops/confcheck.py，第 7 批）：只提醒，不拦、不改（有 ✗ 就说一声，怎么查）。"""
    try:
        import json

        import config
        from ops import confcheck
        bad = confcheck.errors(confcheck.validate(json.loads(config.CONFIG_PATH.read_text(encoding="utf8"))))
    except Exception:  # noqa: BLE001 — 提醒而已：读不了、查不了都不耽误这条命令
        return
    if bad:
        say(L(f"server.json 里有 {len(bad)} 处不对（不是这次改的也算）：", f"server.json has {len(bad)} problems (not necessarily from this change):"))
        for f in bad[:5]:
            say(f"  ✗ {f.path}: {f.message}")
        say(L("  全部看：mousse config validate", "  all of them: mousse config validate"))


WRITES = ("add", "remove", "use", "search", "memory", "embeddings")  # 会写 server.json 的子命令：写完 warn_invalid


def _opt(argv: list[str], name: str) -> str | None:
    if name in argv:
        i = argv.index(name)
        if i + 1 < len(argv):
            v = argv[i + 1]
            del argv[i:i + 2]
            return v
    return None


def _flag(argv: list[str], name: str) -> bool:
    if name in argv:
        argv.remove(name)
        return True
    return False


def main(argv: list[str]) -> int:
    from engine import setup_api as sa
    if not argv:
        return wizard()
    cmd, rest = argv[0], list(argv[1:])
    if cmd in ("-h", "--help", "help"):
        print(usage())
        return 0
    try:
        if cmd == "status":
            return 3 if show_status() else 0
        elif cmd == "models":
            for r in call(sa.providers, True):
                on = r.get("connected") or (r["kind"] == "login" and r.get("ready"))
                print(f"  {'✓' if on else ' '} {r['id']:<14} {r['kind']:<8} {name_of(r)}")
        elif cmd == "add" and rest:
            pid = rest.pop(0)
            region, url, name, from_stdin = _opt(rest, "--region"), _opt(rest, "--url"), _opt(rest, "--name"), _flag(rest, "--key-stdin")
            key = sys.stdin.readline().strip() if from_stdin else (secret(L("密钥（不回显；没有就回车）", "Key (hidden; Enter if none)")) if interactive() else "")
            body = {k: v for k, v in (("key", key), ("region", region), ("url", url), ("name", name)) if v}
            res = call(sa.save_provider, pid, body=body)
            print(L("存好了", "Saved") + (L(f"；主对话用 {res['auto']['chat']}", f"; the main chat uses {res['auto']['chat']}") if res.get("auto") else ""))
        elif cmd == "test" and rest:
            return 0 if show_test(call(sa.test_provider, rest[0], body={"model": rest[1]} if len(rest) > 1 else {})) else 1
        elif cmd == "remove" and rest:
            res = call(sa.delete_provider, rest[0])
            print(L("删了", "Removed") if any(res["removed"].values()) else L("没有要删的", "Nothing to remove"))
            if res["affected"]:
                print(L(f"这些用途还指着它：{'、'.join(res['affected'])}", f"These purposes still point at it: {', '.join(res['affected'])}"))
        elif cmd == "use" and len(rest) == 2 and rest[0] in ("chat", "background", "writing", "outsider"):
            v = rest[1]
            val = None if v == "off" else v
            call(sa.put_purposes, body={rest[0]: val})
            print(L("好了", "Done"))
        elif cmd == "search" and rest:
            sid = rest.pop(0)
            url, from_stdin = _opt(rest, "--url"), _flag(rest, "--key-stdin")
            if sid == "off":
                call(sa.put_search, body={"enabled": False})
            elif sid == "auto":
                call(sa.put_search, body={"enabled": True, "provider": None})
            else:
                body = {k: v for k, v in (("key", sys.stdin.readline().strip() if from_stdin else ""), ("url", url)) if v}
                if body:
                    call(sa.save_search, sid, body=body)
                call(sa.put_search, body={"enabled": True, "provider": sid})
            print(L("好了", "Done"))
        elif cmd == "memory" and rest and rest[0] in ("sqlite", "markdown"):
            body = {"storage": rest[0], **({"path": rest[1]} if len(rest) > 1 else {})}
            t = call(sa.put_memory, body=body)["tree"]
            print(L(f"世界树：{t['storage']} {t['path'] or ''}", f"Memory tree: {t['storage']} {t['path'] or ''}"))
        elif cmd == "embeddings" and rest:
            model = _opt(rest, "--model")
            call(sa.put_purposes, body={"embeddings": "off" if rest[0] == "off" else {"id": rest[0], **({"model": model} if model else {})}})
            print(L("好了", "Done"))
        elif cmd == "grok":  # 10/20：Grok 订阅
            return grok(rest)
        elif cmd == "usage":
            u = call(sa.get_usage, int(rest[0]) if rest and rest[0].isdigit() else 30)
            tot = u["total"]
            print(L(f"{u['since']} 到 {u['until']}：{tot['turns']} 轮，输入 {tot['in']}、输出 {tot['out']} token，${tot['cost']:.4f}"
                    f"（套餐 {tot['planTurns']} 轮不计钱，价目里没有的 {tot['unpricedTurns']} 轮）",
                    f"{u['since']} to {u['until']}: {tot['turns']} turns, {tot['in']} in / {tot['out']} out tokens, ${tot['cost']:.4f} "
                    f"({tot['planTurns']} plan turns not billed, {tot['unpricedTurns']} unpriced)"))
            for m in u["byModel"]:
                cost = L("套餐", "plan") if m["billing"] == "plan" else f"${m['cost']:.4f}"
                print(f"  {m['model']:<40} {m['turns']:>5}  {cost}")
            from engine import grok_plan  # 10/20：Grok 订阅这一段的额度
            if line := grok_plan.quota_line((u.get("plans") or {}).get("grok")):
                print(L(f"Grok 订阅：{line}", f"Grok subscription: {line}"))
            emb = u["embeddings"]["total"]
            if emb["requests"]:
                est = L(f"（估的 {emb['estimatedTokens']}）", f" ({emb['estimatedTokens']} estimated)") if emb["estimatedTokens"] else ""
                print(L(f"向量：{emb['requests']} 个请求，{emb['tokens']} token{est}，${emb['cost']:.4f}（价目里没有的 {emb['unpricedTokens']} token 不算钱）",
                        f"Embeddings: {emb['requests']} requests, {emb['tokens']} tokens{est}, ${emb['cost']:.4f} "
                        f"({emb['unpricedTokens']} unpriced tokens not billed)"))
        else:
            print(usage())
            return 2
    except sa.SetupError as e:
        print(L(f"没成：{e}", f"Didn't work: {e}"), file=sys.stderr)
        return 1
    if cmd in WRITES:
        warn_invalid()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
