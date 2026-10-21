"""存储和协议里的值（stored_values.py）钉在这里：库里的老数据、老的 app（app/src/data/stored.ts）、OpenClaw 的会话、世界树的老叶子和
你库里的笔记都认这些值。这个测试挂了 = 有人改了值：别改，老数据会对不上。"""
from __future__ import annotations

from pathlib import Path

import stored_values as V


def test_values_never_change():
    assert (V.ASSISTANT_ROLE, V.SESSION_TAG, V.TREE_SOURCE, V.TREE_AGENT_PREFIX) == ("grava", "grava", "grava", "grava-")
    assert (V.NOTE_SOURCE_THINK, V.NOTE_SOURCE_PODCAST) == ("grava-think", "grava-podcast")


def test_the_app_uses_the_same_role():
    ts = (Path(__file__).resolve().parents[2] / "app/src/data/stored.ts").read_text(encoding="utf8")
    assert f"export const ASSISTANT_ROLE = '{V.ASSISTANT_ROLE}'" in ts
