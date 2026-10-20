"""server.json 的 channels 段：有哪些账号、开没开、绑哪个 Agent（第 6 批）。每次现读（改了配置不用重启读法；起停见 hub.py）。

  "channels": {
    "telegram": {"enabled": true, "accounts": [{"id": "mousse", "token": "${TELEGRAM_BOT_TOKEN}", …}], "bindings": [...]},
    "email":    {"enabled": true, "accounts": [{"id": "mail", "address": …, "imap": {"host", "user", "folder", …}, "smtp": {…}, …}]}
  }
不写 accounts 的写法（只有一个账号）：渠道那一层本身就是一个账号，id = default。账号上写的盖过渠道根上的（照 OpenClaw）。
绑定（bindings，越具体越优先，照 OpenClaw 的 resolve-route）：每条 {account?, chat?, peer?, agent}，写了的都要对上；
  peer（这个人）> chat（这个聊天）> account（这个账号）；一样具体的按写的顺序。联系人自己身上的 agent（配对时定的、设置里改的）
  算 peer 那一级、排在配置之后。都没有 → 账号的 agent → 渠道的 agent → main。绑的 Agent 不在了按 main，状态里提醒。
"""
from __future__ import annotations

from config import raw

from .base import ACCOUNT_ID, Account, norm_id

KINDS = ("telegram", "email", "discord")   # 认得的渠道；新的渠道加在这里和 hub.KINDS
NAMES = {"telegram": "Telegram", "email": "Email", "discord": "Discord"}


def cfg() -> dict:
    c = raw().get("channels")
    return c if isinstance(c, dict) else {}


def channel_cfg(kind: str) -> dict:
    c = cfg().get(kind)
    return c if isinstance(c, dict) else {}


def accounts(kind: str | None = None) -> list[Account]:
    """配了的账号（开没开都算；id 不像样的跳过，见 problems）。"""
    out: list[Account] = []
    for k in ([kind] if kind else list(cfg())):
        if k not in KINDS and k not in _extra_kinds():
            continue
        c = channel_cfg(k)
        if not c:
            continue
        accs = c.get("accounts")
        if isinstance(accs, list):
            seen: set[str] = set()
            for a in accs:
                if isinstance(a, dict) and ACCOUNT_ID.match(str(a.get("id") or "")) and a["id"] not in seen:
                    seen.add(a["id"])
                    out.append(Account(kind=k, id=str(a["id"]), cfg=a, channel_cfg=c))
        else:
            out.append(Account(kind=k, id="default", cfg={}, channel_cfg=c))
    return out


def _extra_kinds() -> set[str]:
    """测试里登记的假渠道（hub.KINDS 里有、这里的 KINDS 没有的）。"""
    from . import hub
    return set(hub.KINDS) - set(KINDS)


def find(kind: str, account: str) -> Account | None:
    return next((a for a in accounts(kind) if a.id == account), None)


def builtin() -> bool:
    import claw
    return claw.is_builtin()


def enabled(kind: str | None = None) -> list[Account]:
    """该起的：claw 是 builtin、渠道 enabled、账号没关。"""
    if not builtin():
        return []
    return [a for a in accounts(kind) if a.enabled]


def any_enabled() -> bool:
    """claw.caps 的 channels：builtin 下有开着的渠道才 true。"""
    try:
        return bool(enabled())
    except Exception:  # noqa: BLE001 — 配置读不了：当没有
        return False


def bindings(kind: str) -> list[dict]:
    b = channel_cfg(kind).get("bindings")
    return [x for x in b if isinstance(x, dict) and str(x.get("agent") or "").strip()] if isinstance(b, list) else []


def known_agent(agent: str) -> bool:
    try:
        import bridges
        return bridges.known_agent(agent)
    except Exception:  # noqa: BLE001
        return agent == "main"


def bound_agent(acc: Account, chat: str = "", sender: str = "", person: dict | None = None) -> str:
    """这个账号、这个聊天、这个人 → 哪个 Agent（见文件开头）。"""
    s = norm_id(acc.kind, sender)
    best, best_score = None, -1
    for b in bindings(acc.kind):
        bacc, bchat, bpeer = str(b.get("account") or ""), str(b.get("chat") or ""), norm_id(acc.kind, b.get("peer") or "")
        if (bacc and bacc not in (acc.id, "*")) or (bchat and bchat != str(chat)) or (bpeer and bpeer != s):
            continue
        score = (4 if bpeer else 0) + (2 if bchat else 0) + (1 if bacc and bacc != "*" else 0)
        if score > best_score:
            best, best_score = str(b["agent"]).strip(), score
    if person and person.get("agent") and best_score < 4:
        best = str(person["agent"])
    agent = best or acc.agent
    return agent if known_agent(agent) else "main"


def problems(acc: Account) -> list[str]:
    """配置里能看出来的毛病（状态里提醒）：绑的 Agent 不在了、密钥直接写在 server.json 里、邮箱和第 4 批的邮件触发重了……"""
    from i18n import L
    out: list[str] = []
    for key in ("agent",):
        a = str(acc.get(key) or "")
        if a and not known_agent(a):
            out.append(L(f"绑的 Agent「{a}」不在了，先按 main", f'The bound Agent "{a}" no longer exists; main answers for now'))
    for b in bindings(acc.kind):
        if not known_agent(str(b.get("agent") or "")):
            out.append(L(f"bindings 里的 Agent「{b.get('agent')}」不在了", f'The Agent "{b.get("agent")}" in bindings no longer exists'))
    secrets_ = [acc.get("token")] if acc.kind in ("telegram", "discord") else []
    if acc.kind == "email":
        for part in ("imap", "smtp"):
            p = acc.get(part)
            if isinstance(p, dict):
                secrets_.append(p.get("password"))
    if any(acc.plain_secret(s) for s in secrets_ if s):
        out.append(L("令牌 / 密码直接写在 server.json 里了：建议在设置里重新存一次（会放进 secrets.env，server.json 只留 ${变量名}）",
                     "A token or password is written straight into server.json; save it again in settings (it goes into secrets.env, "
                     "server.json keeps only ${NAME})"))
    if acc.kind == "email":
        out += email_overlap(acc)
    return out


def email_overlap(acc: Account) -> list[str]:
    """同一个邮箱（主机 + 账号 + 文件夹）不能同时配成渠道和第 4 批的邮件触发（engine.triggers.mail.<名字>）：重了提醒一句。"""
    from i18n import L
    imap = acc.get("imap") if isinstance(acc.get("imap"), dict) else {}
    mine = (str(imap.get("host") or "").lower(), str(imap.get("user") or acc.get("address") or "").lower(), str(imap.get("folder") or "INBOX"))
    eng = raw().get("engine")
    trig = (eng.get("triggers") or {}).get("mail") if isinstance(eng, dict) and isinstance(eng.get("triggers"), dict) else None
    out = []
    for name, t in (trig.items() if isinstance(trig, dict) else []):
        if not isinstance(t, dict):
            continue
        t = t["imap"] if isinstance(t.get("imap"), dict) else t  # imap.host 这样写的，和直接写 host 的都认
        theirs = (str(t.get("host") or "").lower(), str(t.get("user") or "").lower(), str(t.get("folder") or "INBOX"))
        if mine[0] and theirs == mine:
            out.append(L(f"这个邮箱也配成了邮件触发（engine.triggers.mail.{name}）：同一封信两边都会处理，留一边就好",
                         f"This mailbox is also set up as a mail trigger (engine.triggers.mail.{name}); both would handle the same "
                         "mail, so keep just one"))
    return out
