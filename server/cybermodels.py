"""赛博的模型（主线第三步「Agents 和群聊」，2026-10-21）：给新建的草稿挑一个推荐的（带一句理由），也给选模型的列表排顺序。

只读现有的模型设置（engine/models.py、engine/setup_api.py 的函数，/api/models、/api/setup/models 两个接口是 B 的，不改），在这里排：
  开源的排前面（开源模型是主线；闭源的接进来只给有限的权限：它读到的就发到那一家）；
  本机的（Ollama、LM Studio……）算开源、成本最低，对话不出你的机器；
  用户自己的套餐（Claude Code、ChatGPT……）照样列出来，但标「赛博暂时不能用套餐」（engine/models.py 的 may_spend 不改：
  赛博回合 kind 不是 owner，一律不花套餐）。
成本档按价目表（models.price）的输出价：每百万 token 不到 2 美元 = 低，不到 8 = 中，再贵 = 高；价目表里没有的不猜（None）。
推荐（recommend）照这位赛博是哪一类挑：专业的（出题、点评、给判断）要能力强一些，朋友、机器、空白的要便宜、快。

选模型的页（第 8 件）：GET /api/cyber-models?cyber=<id> | class=<pro | friend | machine | blank>&probe=0 | 1
  → {current（它自己选的，空 = 跟默认）, inUse, class, recommended（适合这一类的前三个，每个一句理由）, all（全部，排好的）, local（本机）,
     channels（「还能接」四条：OpenRouter、本机模型、各家的 API 密钥、ChatGPT 套餐）}
  能选的来自 engine/setup_api.connected（设置里「模型服务」连上的每一家：云端有密钥的、本机探到的、自定义的、登录着的套餐），再加上
  candidates()（app 里能切的、默认的、engine.use.cyber、别的赛博在用的）。probe=1 现探一次本机（Ollama、LM Studio……），默认用上次探的。
  和赛博一样挂在 cybers 开关后面（关着 404）。只读：选好以后照旧 PATCH /api/cybers/{id} {model}。
"""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Query

from i18n import L


def _enabled() -> None:
    import cybers
    cybers.enabled()


router = APIRouter(dependencies=[Depends(_enabled)])
ALL_MAX = 80  # 「全部」最多列几个（OpenRouter 这类一列几百个）：能切的、在用的都在里面，剩下的按排好的顺序截

# 开源（开放权重）的模型家族：名字里带这些的算开源。闭源的（Claude、GPT、Gemini、Grok……）不在这里。拿不准的按闭源算。
OPEN = re.compile(r"deepseek|kimi|qwen|qwq|glm|chatglm|llama|mixtral|ministral|mistral-(?:small|nemo|7b)|gemma|gpt-oss|minimax|phi-?\d|"
                  r"\byi-|baichuan|internlm|olmo|nemotron|hermes|granite|devstral|magistral|falcon|smollm|seed-oss|step-\d|ernie-4\.5", re.I)
COST_RANK = {"low": 0, "mid": 1, "high": 2, None: 3}
# 每一类偏好的成本档（先看开不开源，再看这个）：专业的要能力强一些，别的要便宜、快
PREFER = {"pro": ("mid", "high", "low", None), "friend": ("low", "mid", None, "high"), "machine": ("low", "mid", None, "high"),
          "blank": ("low", "mid", None, "high")}


def provider_of(ref: str) -> str:
    return ref.split("/", 1)[0] if "/" in ref else ""


def is_local(ref: str) -> bool:
    from engine import catalog
    return provider_of(ref) in catalog.LOCAL


def is_open(ref: str) -> bool:
    """开放权重：本机跑的都算；别的看型号名（经 OpenRouter 这类转一道的看后面的型号）。"""
    return is_local(ref) or bool(OPEN.search(ref.split("/", 1)[-1]))


def billing(ref: str) -> str:
    """local 本机 / plan 用户自己的套餐 / metered 按量（API 密钥）。"""
    from engine import models
    if models.subscription(ref):
        return "plan"
    return "local" if is_local(ref) else "metered"


def cost_tier(ref: str) -> str | None:
    """low / mid / high / None（价目表里没有；套餐不算）。本机的不花钱，算 low。"""
    from engine import models
    if billing(ref) == "local":
        return "low"
    if billing(ref) == "plan":
        return None
    p = models.price(ref)
    if not p:
        return None
    return "low" if p[1] < 2 else "mid" if p[1] < 8 else "high"


def vendor(ref: str) -> str:
    """厂商（目录里的名字；不在目录里的写 provider 本身）。"""
    from engine import catalog
    pid = provider_of(ref)
    if pid in catalog.LOGINS:
        return catalog.text(catalog.LOGINS[pid]["name"]) or pid
    e = catalog.entry(pid)
    return (catalog.text(e["name"]) if e else None) or pid


def use_line(ref: str) -> str:
    """一句用途（列表每一行的第二行）。"""
    b, tier = billing(ref), cost_tier(ref)
    if b == "plan":
        return L("你自己的套餐：赛博暂时不能用套餐", "Your own plan: Cybers can't use subscription plans yet")
    if b == "local":
        return L("在你的电脑上跑，对话不出你的机器", "Runs on your computer; the chat stays on your machine")
    if is_open(ref):
        return (L("开源、便宜，适合天天聊", "Open-weight and cheap: fine for chatting every day") if tier == "low"
                else L("开源、能力强，适合出题、点评和给判断", "Open-weight and capable: good at setting questions, reviewing and judging"))
    return L("闭源，能力强；它读到的会发到这一家", "Closed model, capable; what it reads goes to this vendor")


def candidates() -> list[str]:
    """这台服务器上跑得到的模型（规范的写法、去重）：app 里能切的（engine.models、别名、登录着的套餐）、默认的、engine.use.cyber、
    已经有赛博在用的。"""
    from engine import models
    out: list[str] = []

    def add(ref) -> None:
        if isinstance(ref, str) and ref.strip():
            r = models.canonical(models.resolve(ref.strip()))
            if r and r not in out and (models.valid_ref(r) or models.subscription(r)):
                out.append(r)

    for r in models.models():
        add(r)
    add(models.default_model())
    add(models.route("cyber"))
    try:
        import chat
        import cybers
        with chat._lock, cybers.cydb() as conn:
            for row in cybers.active_rows(conn):
                add(row["model"])
    except Exception:  # noqa: BLE001 — 赛博的表还没建：只是少几个候选
        pass
    for r in models.subscription_models():
        add(r)
    return out


def usable(ref: str) -> bool:
    """现在跑得了（不连网）：认得这一家、要密钥的有密钥；套餐要登录着（列出来但赛博不能用）。"""
    from engine import setup_api
    return setup_api.usable(ref)


def row(ref: str) -> dict:
    """列表的一行：{ref, name, vendor, open, local, billing, cost, usable, allowed, use, note?}。allowed = 赛博能选（不是套餐）。"""
    import cybers
    b = billing(ref)
    out = {"ref": ref, "name": cybers.pretty_model(ref), "vendor": vendor(ref), "open": is_open(ref), "local": b == "local", "billing": b,
           "cost": cost_tier(ref), "usable": usable(ref), "allowed": b != "plan", "use": use_line(ref)}
    if b == "plan":
        out["note"] = L("赛博暂时不能用套餐", "Cybers can't use subscription plans yet")
    return out


def ranked(refs: list[str], kind: str = "blank") -> list[str]:
    """开源的在前，再按这一类偏好的成本档；同一档里保持原来的先后（app 里能切的、默认的在前）。套餐的排最后。"""
    prefer = PREFER.get(kind, PREFER["blank"])
    order = {tier: i for i, tier in enumerate(prefer)}
    return sorted(refs, key=lambda r: (billing(r) == "plan", not usable(r), not is_open(r), order.get(cost_tier(r), len(order)), refs.index(r)))


def reason(ref: str, kind: str) -> str:
    """推荐理由（一句）。"""
    tier, b = cost_tier(ref), billing(ref)
    if b == "local":
        return L("在你的电脑上跑：不花钱，对话不出你的机器", "Runs on your computer: free, and the chat stays on your machine")
    if not is_open(ref):
        return L("能用的模型里最合适的；它读到的会发到这一家", "The best fit among the models you can use; what it reads goes to this vendor")
    if kind == "pro":
        return (L("开源里能力强的，适合出题、点评和给判断", "A capable open-weight model: good at setting questions, reviewing and judging")
                if tier in ("mid", "high") else L("开源、便宜；要更细的点评可以换一个强一些的", "Open-weight and cheap; pick a stronger one for finer reviews"))
    if kind == "machine":
        return L("开源、便宜、快，适合一问一答", "Open-weight, cheap and fast: fits quick question and answer")
    return L("开源、便宜，适合天天聊", "Open-weight and cheap: fine for chatting every day")


async def pool(probe: bool = False) -> tuple[list[str], dict[str, dict]]:
    """能列的全部（candidates() 在前，再加设置里连上的每一家能选的，去重）和 setup_api.connected 的原样（给「还能接」）。"""
    from engine import setup_api
    conn = await setup_api.connected(probe_local=probe)
    refs: list[str] = []
    for ref in [*candidates(), *(r for d in conn.values() for r in d["models"])]:
        if ref not in refs:
            refs.append(ref)
    return refs, conn


async def recommend(kind: str = "blank") -> dict | None:
    """给这一类赛博推荐一个（跑得了、不是套餐的里排第一的）：{ref, name, reason, cost, open, local}；一个都没有 → None（跟默认的走）。
    和选模型的页「推荐」那一栏的第一个是同一个。"""
    import cybers
    refs, _ = await pool()
    usable_refs = [r for r in refs if billing(r) != "plan" and usable(r)]
    if not usable_refs:
        return None
    ref = ranked(usable_refs, kind)[0]
    return {"ref": ref, "name": cybers.pretty_model(ref), "reason": reason(ref, kind), "cost": cost_tier(ref), "open": is_open(ref),
            "local": billing(ref) == "local"}


def channels(conn: dict[str, dict]) -> list[dict]:
    """「还能接」四条渠道（connected = 这台服务器上已经连着）：都在设置里「模型服务」接，provider 是那一页上的那一家。"""
    from engine import catalog
    cloud = [pid for pid, d in conn.items() if d["kind"] == "cloud" and pid != "openrouter"]
    return [{"id": "openrouter", "provider": "openrouter", "name": "OpenRouter", "connected": "openrouter" in conn,
             "line": L("一把钥匙接很多家，开源的也有", "One key for many vendors, open-weight ones too")},
            {"id": "local", "provider": next(iter(catalog.LOCAL)), "name": L("本机模型", "Local models"),
             "connected": any(d["kind"] == "local" for d in conn.values()),
             "line": L("Ollama、LM Studio：对话不出你的机器", "Ollama, LM Studio: the chat stays on your machine")},
            {"id": "keys", "provider": None, "name": L("各家的 API 密钥", "API keys from each vendor"), "connected": bool(cloud),
             "line": L("DeepSeek、Kimi、通义千问……填一把密钥就能用，按用量付费", "DeepSeek, Kimi, Qwen and more: add a key, pay as you go")},
            {"id": "chatgpt", "provider": "chatgpt", "name": L("ChatGPT 套餐", "ChatGPT plan"), "connected": "chatgpt" in conn, "allowed": False,
             "line": L("赛博暂时不能用套餐", "Cybers can't use subscription plans yet")}]


@router.get("/api/cyber-models")
async def cyber_models(cyber: str | None = None, kind: str = Query("blank", alias="class"), probe: int = 0):
    """选模型的页（见文件开头）。cyber = 给哪一位选（看它是哪一类、现在用的哪个）；没有 cyber 时按 class（新建时还没有这位）。"""
    import chat
    import cybers
    current, in_use = None, None
    if cyber:
        with chat._lock, cybers.cydb() as conn:
            r = cybers.get_row(conn, cyber)
        current, in_use, kind = r["model"], cybers.model_in_use(r), r["kind"]
    if kind not in cybers.KINDS:
        raise HTTPException(400, L(f"class 只能是 {' / '.join(cybers.KINDS)}", f"class must be one of {', '.join(cybers.KINDS)}"))
    refs, conn = await pool(bool(probe))
    names = {ref: d["name"] for d in conn.values() for ref in d["models"]}
    order = ranked(refs, kind)
    keep = set(candidates()) | {x for x in (current, in_use) if x}
    shown = [r for r in order if r in keep] + [r for r in order if r not in keep]
    shown = sorted(shown[:max(ALL_MAX, len(keep))], key=order.index)

    def one(ref: str) -> dict:
        return {**row(ref), "providerName": names.get(ref) or vendor(ref), "current": ref == (current or None), "inUse": ref == in_use}

    rec = [{**one(r), "reason": reason(r, kind)} for r in order if billing(r) != "plan" and usable(r)][:3]
    rows = [one(r) for r in shown]
    return {"ok": True, "cyber": cyber, "class": kind, "current": current, "inUse": in_use, "inUseName": cybers.pretty_model(in_use),
            "recommended": rec, "all": rows, "local": [x for x in rows if x["local"]], "channels": channels(conn)}
