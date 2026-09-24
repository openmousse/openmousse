"""可选数据源：看板要的训练 / 餐食 / 身体 / 日历 / 健康派生指标，各来自 workspace `scripts/` 里的一个脚本。

脚本在就加载，不在就是 None，对应接口回「还没接数据源」（HTTP 200，ok=false，missing_source=<类型>），
其它功能照常。/api/health 的 `sources` 告诉 app 哪些类型接上了，没接的看板显示空状态而不是报错。

  workouts / meals / body   ← scripts/xunji.py（作者用的训记；换别的软件就写一个同名接口的适配器）
  calendar                  ← scripts/calendar_ics.py（ICS 链接）
  health                    ← scripts/apple_health.py（恢复分、热量缺口、体能趋势；原始数据由手机推到 /api/health/daily，不需要脚本）

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

if settings.scripts.is_dir() and str(settings.scripts) not in sys.path:
    sys.path.insert(0, str(settings.scripts))


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

AVAILABLE: dict[str, bool] = {
    "workouts": xunji is not None,
    "meals": xunji is not None,
    "body": xunji is not None,
    "calendar": calendar_ics is not None,
    "health": apple_health is not None,
}

LABEL = {"workouts": "训练", "meals": "餐食", "body": "身体数据", "calendar": "日历", "health": "健康指标"}


class NoSource(Exception):
    """接口要的数据类型还没接数据源。main.py 把它变成 200 + ok=false，app 据此显示空状态。"""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        super().__init__(f"还没接{LABEL.get(kind, kind)}数据源")


def require(kind: str) -> None:
    if not AVAILABLE.get(kind):
        raise NoSource(kind)


async def no_source_handler(_: Request, exc: NoSource) -> JSONResponse:
    return JSONResponse({"ok": False, "error": str(exc), "missing_source": exc.kind,
                         "hint": f"在 server.json 的 scripts 目录放一个提供{LABEL.get(exc.kind, exc.kind)}的脚本，或者直接在对话里记。"})
