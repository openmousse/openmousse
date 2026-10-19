"""bridges.guard：经 MCP / 内核调命令行工具时不许读服务器上的文件，但别把正好是文件参数前缀的真参数也挡了
（10/19 彩排：diet add --item 被当成 --items-file 的缩写挡掉，每一餐都记不进去）。"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

import bridges


@pytest.mark.parametrize("name,args", [
    ("diet", ["add", "--meal", "早餐", "--item", "f-4ec6bd3e=130", "--item", "燕麦拿铁=300@150,3,18,8"]),
    ("diet", ["add", "--meal", "早餐", "--item=鸡蛋=130"]),
    ("page", ["publish", "--data", "rows=board:pantry"]),
    ("inbox", ["add", "--items-file", "-"]),
    ("board", ["apply", "--file", "-"]),
])
def test_real_flags_pass(name, args):
    bridges.guard(name, args)


@pytest.mark.parametrize("name,args", [
    ("inbox", ["add", "--items-file", "/etc/passwd"]),
    ("inbox", ["add", "--items-f", "/etc/passwd"]),
    ("board", ["apply", "--file", "/tmp/x.json"]),
    ("inbox", ["add", "--detail-file=/tmp/x"]),
    ("page", ["publish", "--data-file", "/tmp/x.json"]),
    ("inbox", ["update", "ib-1", "--item", "/etc/passwd"]),  # 别的工具里 --item 还是 --items-file 的缩写
    ("inbox", ["add", "--data", "/etc/passwd"]),
])
def test_file_flags_blocked(name, args):
    with pytest.raises(bridges.BridgeError):
        bridges.guard(name, args)


def test_no_real_flag_is_a_file_flag_prefix():
    """命令行工具里每个真参数：要么本身是文件参数，要么不是任何文件参数的前缀，要么列进 NOT_FILE。"""
    root = Path(bridges.__file__).resolve().parent.parent
    files = list((root / "server").glob("*_ctl.py")) + [root / "packs/core/scripts/ask_agent.py", root / "packs/core/scripts/journal.py"]
    bad = {}
    tool_of = {str(root / v[0]): k for k, v in bridges.BRIDGES.items()}
    for f in files:
        own = bridges.NOT_FILE_IN.get(tool_of.get(str(f), ""), set())
        for flag in set(re.findall(r'add_argument\(\s*"(--[a-z][a-z0-9-]*)"', f.read_text(encoding="utf8"))):
            if flag in bridges.FILE_FLAGS or flag in bridges.NOT_FILE or flag in own or len(flag) < 4:
                continue
            if any(ff.startswith(flag) for ff in bridges.FILE_FLAGS):
                bad.setdefault(flag, []).append(f.name)
    assert not bad
