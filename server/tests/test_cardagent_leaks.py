"""名片 Agent 的防泄漏检查（cardagent.leaks）：放出来的住址原样说出去要放行，句末带句号也一样（10/16 评测 c06：五个模型都答
「…27 Wren Lane.」，全被当成漏住址换成「答不了」）；没放出来的住址照样拦。"""
from __future__ import annotations

import cardagent

ADDR = [{"id": "address", "label": "address", "text": "Flat 4, 27 Wren Lane, Wrenford"}]


def test_released_address_with_trailing_period_passes():
    assert cardagent.leaks("It's Flat 4, 27 Wren Lane.", ADDR, ["address"]) == []
    assert cardagent.leaks("Mira lives at 27 Wren Lane, Wrenford.", ADDR, ["address"]) == []


def test_unreleased_or_different_address_is_still_caught():
    assert "address" in cardagent.leaks("It's 27 Wren Lane.", [], [])
    assert "address" in cardagent.leaks("It's 99 Cedar Road.", ADDR, ["address"])
