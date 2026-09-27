"""可选数据源：看板要的训练 / 餐食 / 身体 / 日历 / 健康派生指标，各来自 workspace `scripts/` 里的一个脚本。

脚本在就加载，不在就是 None，对应接口回「还没接数据源」（HTTP 200，ok=false，missing_source=<类型>），
其它功能照常。/api/health 的 `sources` 告诉 app 哪些类型接上了，没接的看板显示空状态而不是报错。

  workouts / meals / body   ← scripts/xunji.py（作者用的训记；换别的软件就写一个同名接口的适配器）
  calendar                  ← scripts/calendar_ics.py（ICS 链接）
  health                    ← scripts/apple_health.py（恢复分、热量缺口、体能趋势；原始数据由手机推到 /api/health/daily，不需要脚本）
  tree                      ← scripts/memory_tree.py（世界树：各 AI 平台共用的记忆，真身是 Obsidian 库里的笔记；见 memtree.py）

脚本目录在 server.json 的 `scripts`（默认 <workspace>/scripts）。
"""
from __future__ import annotations

import importlib
import sys
import traceback
from types import ModuleType

from fastapi import Request
from fastapi.responses import JSONResponse

from config import settings
from i18n import L

# 放在 sys.path 最后：scripts/ 里的文件（agent 能写）不能顶替服务自己的模块（chat.py、data.py 这些同名文件）。
if settings.scripts.is_dir() and str(settings.scripts) not in sys.path:
    sys.path.append(str(settings.scripts))


def _load(name: str) -> ModuleType | None:
    if not (settings.scripts / f"{name}.py").is_file():
        return None
    try:
        return importlib.import_module(name)
    except Exception:  # noqa: BLE001 — 脚本在但加载失败（少依赖、少密钥）也当没接，把原因打出来
        print(f"[sources] {name}.py 加载失败，当作没接：", file=sys.stderr)
        traceback.print_exc()
        return None


xunji = _load("xunji")
calendar_ics = _load("calendar_ics")
apple_health = _load("apple_health")
memory_tree = _load("memory_tree")

AVAILABLE: dict[str, bool] = {
    "workouts": xunji is not None,
    "meals": xunji is not None,
    "body": xunji is not None,
    "calendar": calendar_ics is not None,
    "health": apple_health is not None,
    "tree": memory_tree is not None,
}


def xunji_name() -> str:
    """训练 / 饮食 / 身体数据的来源名（看板的小字、目标的读数来源）。适配器脚本可以自报 SOURCE_NAME（字符串，或 {"zh": ..., "en": ...}），没写就是训记。"""
    name = getattr(xunji, "SOURCE_NAME", None) if xunji else None
    if isinstance(name, dict):
        return L(name.get("zh") or name.get("en") or "", name.get("en") or name.get("zh") or "")
    if isinstance(name, str) and name:
        return name
    return L("训记", "Xunji")


def label(kind: str) -> str:
    """数据类型的显示名，按请求的语言（英文要能套进 "No {x} data connected yet"）。"""
    names = {"workouts": L("训练", "workout"), "meals": L("餐食", "meal"), "body": L("身体数据", "body"),
             "calendar": L("日历", "calendar"), "health": L("健康指标", "health"), "tree": L("世界树", "memory tree")}
    return names.get(kind, kind)


class NoSource(Exception):
    """接口要的数据类型还没接数据源。main.py 把它变成 200 + ok=false，app 据此显示空状态。"""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        super().__init__(L(f"还没接{label(kind)}数据源", f"No {label(kind)} data connected yet"))


def require(kind: str) -> None:
    if not AVAILABLE.get(kind):
        raise NoSource(kind)


def hint(kind: str) -> str:
    if kind == "tree":
        return L("服务器的 scripts 目录里没有 memory_tree.py（世界树的脚本），接上以后这里就有了。",
                 "memory_tree.py (the memory tree script) isn't in the server's scripts directory. Once it's there, this page fills in.")
    return L(f"在 server.json 的 scripts 目录放一个提供{label(kind)}的脚本，或者直接在对话里记。",
             f"Put a script that provides {label(kind)} data in the scripts directory set in server.json, or just log it in chat.")


async def no_source_handler(_: Request, exc: NoSource) -> JSONResponse:
    return JSONResponse({"ok": False, "error": str(exc), "missing_source": exc.kind, "hint": hint(exc.kind)})
