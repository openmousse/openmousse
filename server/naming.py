"""给助手起名字（第 9 批 15.5，2026-10-19）：第一次打开 app 时「给助手起个名字」那一屏（app/src/screens/SetupScreens.tsx 的
SetupNameScreen，在「设好了」之后、「好友如何称呼你」之前，能跳过）。形象（透镜 / 日食 / 轨道）照旧走 PUT /api/settings/avatar。

  GET /api/naming   → {"ok", "name", "vibe", "applies"}：现在的名字（server.json 的 app_name）、主工作区 IDENTITY.md 里那一行性格
                      （「- **Vibe:** …」，没有 = ""）、能不能改（claw 是 builtin）
  PUT /api/naming   {"name"?, "vibe"?} → GET 的样子 + "backup"（改了名字时 server.json 的备份名）
    name   写进 server.json 的 app_name（先备份：engine/setup_api.edit），这个进程里的 settings.app_name 跟着换：/api/health、
           内核每一轮开头的「You are …」马上用新的，不用重启。最多 NAME_MAX 个字，换行、多余的空白并成一个空格
    vibe   一句性格，写成 IDENTITY.md 的一行「- **Vibe:** …」：有这一行就换掉，没有就加在末尾，"" = 去掉这一行；没有 IDENTITY.md
           就建一个（名字一行、性格一行）。改之前留一版（engine/versions.keep，能在文件工具里看历史、换回去）。IDENTITY.md 里
           「- **Name:** …」还是旧名字的话一起换成新的（不然开头说的名字和它自己记的对不上）；写的是别的就不动
只在 claw 是 builtin 时能改（别的 claw 的名字、性格在它自己那边：409）；只认主人的设备（engine/setup_api.owner：app 的令牌或
Tailscale；本机脚本、程序用的令牌 403）。
"""
from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, Depends, Request

import claw
import config
from i18n import L

from engine import setup_api, versions
from engine.setup_api import SetupError

router = APIRouter()
NAME_MAX = 40
VIBE_MAX = 120
VIBE = re.compile(r"^- \*\*Vibe:\*\*[ \t]*(.*)$", re.M)
NAME = re.compile(r"^(- \*\*Name:\*\*)[ \t]*(.*)$", re.M)
CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def identity() -> Path:
    return config.settings.workspace / "IDENTITY.md"


def vibe_now() -> str:
    try:
        m = VIBE.search(identity().read_text(encoding="utf8"))
    except (OSError, UnicodeDecodeError):
        return ""
    return m.group(1).strip() if m else ""


def view() -> dict:
    return {"ok": True, "name": config.settings.app_name, "vibe": vibe_now(), "applies": claw.is_builtin()}


def one_line(v, limit: int, field: str) -> str:
    if not isinstance(v, str):
        raise SetupError(400, L(f"{field} 要是一段字", f"{field} must be text"), field=field)
    s = " ".join(CONTROL.sub(" ", v).split())
    if len(s) > limit:
        raise SetupError(400, L(f"{field} 最多 {limit} 个字", f"{field} is at most {limit} characters"), field=field)
    return s


def identity_text(old: str, new: str | None, vibe: str | None) -> str | None:
    """IDENTITY.md 要改成什么：性格那一行（vibe 不是 None 才动）、还是旧名字的那一行 Name。没变 = None。
    只算不写：是链接、读不了在这里就报错，server.json 还没动。"""
    p = identity()
    if p.is_symlink():
        raise SetupError(409, L("IDENTITY.md 是一个链接，没改：去它指的那个文件里改", "IDENTITY.md is a link and wasn't changed: edit the file it points to"))
    try:
        text = p.read_text(encoding="utf8") if p.is_file() else ""
    except (OSError, UnicodeDecodeError):
        raise SetupError(409, L("IDENTITY.md 读不了", "IDENTITY.md can't be read")) from None
    out = text
    if vibe is not None:
        line = f"- **Vibe:** {vibe}"
        if VIBE.search(out):
            # 换成的那行原样放进去（给函数，不当替换模板）：性格里的 \o、\n 之类不报错、也不变成真的换行
            out = VIBE.sub(lambda _m: line, out, count=1) if vibe else re.sub(r"^- \*\*Vibe:\*\*.*(?:\n|$)", "", out, count=1, flags=re.M)
        elif vibe and out.strip():
            out = out.rstrip("\n") + "\n" + line + "\n"
        elif vibe:
            out = f"# IDENTITY.md\n\n- **Name:** {new or old}\n{line}\n"
    if new and new != old and (m := NAME.search(out)) and m.group(2).strip() == old:
        out = out[:m.start()] + f"{m.group(1)} {new}" + out[m.end():]
    return None if out == text else out


def keep_identity() -> None:
    """改 IDENTITY.md 之前留一版（太大就报错、不改）。"""
    ws = identity().parent
    ws.mkdir(parents=True, exist_ok=True)
    try:
        versions.keep(ws, "IDENTITY.md", by="app", note="naming")
    except versions.TooBig:
        raise SetupError(409, L("IDENTITY.md 太大，没改", "IDENTITY.md is too large and wasn't changed")) from None


@router.get("/api/naming")
def get_naming(_who: str = Depends(setup_api.owner)):
    return view()


@router.put("/api/naming")
async def put_naming(request: Request, _who: str = Depends(setup_api.owner)):
    if not claw.is_builtin():
        raise SetupError(409, L("名字和性格在你的 claw 自己那边改：这里只管自带的内核", "Change the name and character in your claw itself: this only "
                                "applies to the built-in engine"))
    b = await setup_api.body_of(request)
    old = config.settings.app_name
    new = None
    if b.get("name") is not None:
        new = one_line(b["name"], NAME_MAX, "name")
        if not new:
            raise SetupError(400, L("名字不能是空的", "The name can't be empty"), field="name")
    vibe = one_line(b["vibe"], VIBE_MAX, "vibe") if b.get("vibe") is not None else None
    # IDENTITY.md 先算好、留好版本再动 server.json：哪一步出错都不会只改了一半
    text = identity_text(old, new, vibe)
    if text is not None:
        keep_identity()
    backup = None
    if new is not None and new != old:
        backup = setup_api.edit(lambda d: d.__setitem__("app_name", new))["backup"]
        config.settings.app_name = new  # 名字是启动时读的：这个进程里也换上，不用重启
    if text is not None:
        identity().write_text(text, encoding="utf8")
    return {**view(), "backup": backup}
