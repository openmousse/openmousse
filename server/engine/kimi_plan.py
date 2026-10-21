"""Kimi 会员的 Kimi Code（kimi/<型号>，2026-10-21）。

和开放平台按量的 moonshot/<型号> 分开：这里是会员在 Kimi Code 控制台建的密钥，OpenAI 兼容。
国内 https://api.kimi.com/coding/v1，海外 https://api.kimi.ai/coding/v1。不做 OAuth（官方 OAuth 只给它自己的客户端）。
不冒充 Kimi CLI 或 Claude Code：User-Agent 用 OpenMousse 自己的名字和版本号，配置里写了别的也盖掉。

密钥存在 secretstore（KIMI_CODE_API_KEY），和别的 API 密钥一样，不进 state、不进日志、不回给 app。
站点、选中的型号、后台同意记在 <data_dir>/kimi/state.json（不含密钥）。

谁能花（engine/models.py 的 may_spend，照 ChatGPT / Grok 的分法，同意单独记）：
  用户自己引出来的（user、relay）能花；
  后台的（auto、task）要用户在设置里单独同意（background_allowed，默认不同意）；
  替外人答的（card、doorman、butler、api、acp、mcp、trigger）一律不花。
条款写「仅限个人交互式使用」。后台自动跑可能被限流，甚至封号。同意那一处把这句写给用户看。

哪一档会员能用哪个型号以会员页为准。调用回 401 / 403 时换成一句看得懂的话，原文里的密钥不进这句。

接口（只认主人的设备，同 /api/setup）：
  GET    /api/engine/kimi              状态（没有密钥原文）
  PUT    /api/engine/kimi              {"key"?, "site"?: "cn"|"intl", "model"?}
  DELETE /api/engine/kimi              删掉 app 里存的密钥（进程环境里的删不了）
  PUT    /api/engine/kimi/background   {"allow": true|false}
  POST   /api/engine/kimi/test         测一下（还没存的密钥放在 body 里，不存）
server.json 的 engine.kimi（可选）：false = 关掉；{"site", "model", "headers", "enabled"}。
设置里存过的站点、型号优先于 server.json。
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Request

import secretstore
from config import settings
from i18n import L

from .chatgpt_plan import read_json, write_private
from .errors import EngineError, scrub

KEY_ENV = "KIMI_CODE_API_KEY"
MODELS = ("k3", "k3-256k", "kimi-for-coding", "kimi-for-coding-highspeed")
DEFAULT_MODEL = "kimi-for-coding"
SITES = {
    "cn": {"url": "https://api.kimi.com/coding/v1", "label": ("国内", "China")},
    "intl": {"url": "https://api.kimi.ai/coding/v1", "label": ("海外", "Overseas")},
}
DOCS = "https://www.kimi.com/code/docs/"


def user_agent() -> str:
    try:
        from version import VERSION
    except Exception:  # noqa: BLE001
        VERSION = "unknown"  # noqa: N806
    return f"OpenMousse/{VERSION}"


def cfg() -> dict:
    """server.json 的 engine.kimi（false = 关掉）。"""
    from . import models
    v = models.cfg().get("kimi")
    return {"enabled": False} if v is False else v if isinstance(v, dict) else {}


def enabled() -> bool:
    return cfg().get("enabled", True) is not False


def data_path() -> Path:
    return settings.data_dir / "kimi"


def state() -> dict:
    return read_json(data_path() / "state.json") or {}


def save_state(**changes) -> dict:
    s = {**state(), **changes}
    write_private(data_path() / "state.json", s)
    return s


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


def api_key() -> str:
    """现在能用的密钥（进程环境优先，然后是 app 里存的）。没有 = 空。不打日志。"""
    return secretstore.lookup(KEY_ENV)[0]


def usable() -> bool:
    """没关、贴了密钥。不连网。"""
    return enabled() and bool(api_key())


def site_id() -> str:
    """设置里选的站点；没选过看 server.json；都没有就跟 app 语言走（中文 → 国内）。"""
    for raw in (state().get("site"), cfg().get("site")):
        if raw in SITES:
            return str(raw)
    return "cn" if settings.language == "zh" else "intl"


def base_url() -> str:
    return SITES[site_id()]["url"]


def model_id() -> str:
    """设置里选的型号（可以不带 kimi/）；不在四档里就用 kimi-for-coding。"""
    for raw in (state().get("model"), cfg().get("model")):
        bare = str(raw or "").strip().split("/")[-1]
        if bare in MODELS:
            return bare
    return DEFAULT_MODEL


def model_ids() -> list[str]:
    """app 里能选的 kimi/…：贴了密钥才有，选中的排最前。"""
    if not usable():
        return []
    chosen = f"kimi/{model_id()}"
    return [chosen, *[f"kimi/{m}" for m in MODELS if f"kimi/{m}" != chosen]]


def background_allowed() -> bool:
    """用户在设置里明确同意过后台花 Kimi 会员吗（默认没有；和 ChatGPT、Grok 的同意分开记）。"""
    b = state().get("background")
    return isinstance(b, dict) and b.get("allowed") is True


def set_background(allow: bool) -> dict:
    save_state(background={"allowed": bool(allow), "at": _iso(time.time())})
    return status()


def risk_text() -> str:
    return L("Kimi Code 的条款写「仅限个人交互式使用」。后台自动跑可能被限流，甚至封号。默认不同意。",
             "Kimi Code's terms allow personal interactive use only. Unattended background runs may be rate-limited or the account banned. "
             "Off unless you agree.")


def bad_key_text() -> str:
    return L("Kimi Code 的密钥不对，或者已经作废。到 Kimi Code 控制台换一把，再贴到设置里。",
             "The Kimi Code key is wrong or has been revoked. Make a new one in the Kimi Code console and paste it in Settings.")


def permission_text() -> str:
    return L("这个 Kimi 会员用不了这个型号。哪一档能用哪个，以会员页为准。换一个型号，或者升级会员。",
             "This Kimi membership can't use this model. Which tier includes which model is on the membership page. Pick another model, or upgrade.")


def _clean(text: str, key: str = "") -> str:
    """给人看的字：抹掉这一把密钥和像密钥的串。"""
    return scrub(text, key or api_key())


def headers() -> dict:
    """推理和测试带的头。User-Agent 永远是 OpenMousse 自己的，盖住配置里写的。"""
    extra = cfg().get("headers") if isinstance(cfg().get("headers"), dict) else {}
    out = {str(k): str(v) for k, v in extra.items() if str(k).lower() != "user-agent"}
    out["User-Agent"] = user_agent()
    return out


def provider_stub() -> dict:
    """models.provider("kimi")：OpenAI 兼容，地址是现在选的站点。key 是密钥本身（备用链按它冷却）；报错时 providers.stream 抹掉。"""
    from . import models
    key = api_key() if enabled() else ""
    return {"name": "kimi", "api": models.OPENAI, "url": base_url(), "key": key, "key_env": KEY_ENV, "plan": True, "images": True,
            "keys": [(KEY_ENV, key)] if key else [], "key_name": KEY_ENV, "headers": headers()}


def unavailable_text() -> str:
    """现在为什么还不能用：关了，或者还没贴密钥。"""
    if not enabled():
        return L("server.json 里关掉了 Kimi 会员", "Kimi membership is turned off in server.json")
    return L("还没贴 Kimi Code 的密钥。到设置里的「Kimi 会员（Kimi Code）」贴一把。",
             "No Kimi Code key yet. Paste one under Kimi membership (Kimi Code) in Settings.")


def prepare(p: dict) -> dict:
    """这一次请求用的那一份：密钥、站点、User-Agent。没关、没密钥 → EngineError(config)，不含密钥。"""
    if not enabled():
        raise EngineError(unavailable_text(), kind="config")
    key = str(p.get("key") or "") or api_key()
    if not key:
        raise EngineError(unavailable_text(), kind="config")
    return {**p, "key": key, "url": base_url(), "headers": headers()}


def note_ok() -> None:
    if state().get("error"):
        save_state(error=None)


def note_failure(e: EngineError) -> None:
    """一次调用没成（providers.stream 调，密钥已经抹过）：401 / 403 换成一句看得懂的；记进状态的也不含密钥。"""
    if e.status == 403:
        e.args = (permission_text(),)
    elif e.status == 401:
        e.args = (bad_key_text(),)
    msg = _clean(str(e))[:300]
    save_state(error={"code": f"http_{e.status}" if e.status else (e.kind or "error"), "message": msg, "at": _iso(time.time())})


def _human(err: dict | None, key: str) -> dict | None:
    if not isinstance(err, dict):
        return None
    msg = _clean(str(err.get("message") or ""), key)
    if err.get("kind") == "auth":
        msg = permission_text() if "403" in msg else bad_key_text() if "401" in msg else msg
    if key and key in msg:
        msg = msg.replace(key, "***")
    return {**err, "message": msg}


def _pick_site(raw: str | None) -> str:
    if raw in SITES:
        return str(raw)
    if raw:
        from .setup_api import SetupError
        raise SetupError(400, L("站点只能是国内（cn）或海外（intl）", "The site must be China (cn) or overseas (intl)"), field="site")
    return site_id()


def _pick_model(raw: str | None) -> str:
    bare = str(raw or "").strip().split("/")[-1]
    if not raw:
        return model_id()
    if bare not in MODELS:
        from .setup_api import SetupError
        raise SetupError(400, L(f"型号只能是这四档：{'、'.join(MODELS)}", f"The model must be one of: {', '.join(MODELS)}"), field="model")
    return bare


async def run_test(key: str | None = None, site: str | None = None, model: str | None = None) -> dict:
    """发一句最小的请求。key 给了就用这一把（不存）。回的 error 不含密钥。"""
    from . import probe
    use = (key or "").strip() or api_key()
    sid, mid = _pick_site(site), _pick_model(model)
    p = {"api": "openai", "url": SITES[sid]["url"], "list": "none", "needs_key": True, "headers": headers(), "test_model": mid}
    res = await probe.test_model(p, use, mid)
    err = _human(res.get("error"), use)
    return {**res, "error": err, "site": sid, "model": f"kimi/{mid}"}


def status() -> dict:
    """给 app 的状态。没有密钥原文。"""
    info = secretstore.info(KEY_ENV)
    bg = state().get("background") if isinstance(state().get("background"), dict) else {}
    err = state().get("error") if isinstance(state().get("error"), dict) else None
    last = {k: err.get(k) for k in ("code", "message", "at")} if err else None
    if last and last.get("message"):
        last["message"] = _clean(str(last["message"]))
    return {"enabled": enabled(), "connected": bool(info.get("present")) and enabled(), "ready": usable(),
            "key": {k: info.get(k) for k in ("present", "source", "hint", "savedAt")}, "site": site_id(), "url": base_url(),
            "sites": [{"id": k, "label": L(*v["label"]), "url": v["url"]} for k, v in SITES.items()],
            "model": f"kimi/{model_id()}", "models": [f"kimi/{m}" for m in MODELS], "defaultModel": f"kimi/{model_id()}",
            "background": {"allowed": bg.get("allowed") is True, "since": bg.get("at"), "risk": risk_text()},
            "lastError": last, "docs": DOCS}


def save(key: str | None = None, site: str | None = None, model: str | None = None) -> dict:
    """存站点、型号；key 给了就写进 secretstore。不像样的密钥抛 SetupError（不带值）。"""
    from .setup_api import SetupError
    changes: dict = {}
    if site is not None:
        changes["site"] = _pick_site(site)
    if model is not None:
        changes["model"] = _pick_model(model)
    if changes:
        save_state(**changes)
    if key:
        try:
            secretstore.put(KEY_ENV, secretstore.check_value(KEY_ENV, key))
        except secretstore.SecretError as e:
            raise SetupError(400, str(e), field="key") from None
        from . import fallback
        fallback.forget("kimi")
    return status()


def remove_key() -> dict:
    """删掉 app 里存的这一把。进程环境或 env_file 里的删不了。"""
    from .setup_api import SetupError, _home
    info = secretstore.info(KEY_ENV)
    if info["source"] in ("env", "env_file") and not secretstore.get(KEY_ENV):
        where = L("服务的进程环境", "the server's environment") if info["source"] == "env" else _home(secretstore.env_file())
        raise SetupError(409, L(f"这把密钥不是在 app 里存的（在 {where}），这里删不了",
                                f"This key wasn't saved in the app (it's in {where}), so it can't be removed here"), source=info["source"])
    removed = secretstore.delete(KEY_ENV)
    return {"ok": True, "removed": {"key": removed, "config": False}, "affected": [], "backup": None, **status()}


def _owner(request: Request) -> str:
    from .setup_api import owner
    return owner(request)


router = APIRouter(dependencies=[Depends(_owner)])


@router.get("/api/engine/kimi")
async def get_status():
    return {"ok": True, **status()}


@router.put("/api/engine/kimi")
async def put_settings(request: Request):
    from .setup_api import SetupError, _key, _str, body_of
    b = await body_of(request)
    key, site, model = _key(b), _str(b, "site", 40), _str(b, "model", 80)
    if key is None and site is None and model is None:
        raise SetupError(400, L("没有要存的（key、site 或 model）", "Nothing to save (key, site or model)"))
    return {"ok": True, **save(key, site, model)}


@router.delete("/api/engine/kimi")
async def delete_key():
    return remove_key()


@router.put("/api/engine/kimi/background")
async def put_background(request: Request):
    from .setup_api import SetupError, body_of
    b = await body_of(request)
    if not isinstance(b.get("allow"), bool):
        raise SetupError(400, L("allow 要是 true / false", "allow must be true or false"), field="allow")
    return {"ok": True, **set_background(b["allow"])}


@router.post("/api/engine/kimi/test")
async def post_test(request: Request):
    from .setup_api import _key, _str, body_of
    b = await body_of(request)
    return await run_test(_key(b), _str(b, "site", 40), _str(b, "model", 80))
