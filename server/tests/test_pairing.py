"""pairing.py：配对码的规范化、出码、换令牌（过期、用过、试错限流）、设备名。server.json 写在 tmp_path 里。"""
from __future__ import annotations

import time
import urllib.parse
from datetime import datetime, timedelta, timezone

import pytest

import config
import pairing
from config import settings


@pytest.fixture(autouse=True)
def clean(server_json, monkeypatch):
    server_json({"app_name": "TestMousse", "auth": {"tokens": {"laptop": "existing"}}})
    monkeypatch.setattr(pairing, "_fails", {})
    monkeypatch.setattr(settings, "language", "en")


def stored() -> dict:
    return config.raw(fresh=True).get("auth") or {}


# —— normalize / link / _slug / device_label ——————————————————————————


@pytest.mark.parametrize("raw_code,expected", [
    ("abcd2345", "ABCD2345"), ("ABCD-2345", "ABCD2345"), (" ab cd\t23-45 ", "ABCD2345"), ("", ""), (None, ""), (12345678, "12345678"),
])
def test_normalize(raw_code, expected):
    assert pairing.normalize(raw_code) == expected


def test_hash_ignores_formatting():
    assert pairing._hash("abcd-2345") == pairing._hash("ABCD2345")
    assert pairing._hash("ABCD2345") != pairing._hash("ABCD2346")


def test_link_encodes_query():
    url = pairing.link("http://100.64.0.1:8080", "ABCD2345")
    assert url.startswith("openmousse://pair?")
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    assert q == {"s": ["http://100.64.0.1:8080"], "c": ["ABCD2345"]}


@pytest.mark.parametrize("device,expected", [
    ("iPhone 15 Pro", "iphone-15-pro"), ("--Pixel__8--", "pixel-8"), ("", "phone"), (None, "phone"), ("我的手机", "phone"),
    ("a" * 50, "a" * 20),
])
def test_slug(device, expected):
    assert pairing._slug(device) == expected


@pytest.mark.parametrize("name,expected", [
    ("device-iphone-15-09291530", "iphone 15"), ("device-phone-01020304x", "phone"), ("laptop", "laptop"), ("device-short", "device-short"),
])
def test_device_label(name, expected):
    assert pairing.device_label(name) == expected


@pytest.mark.parametrize("name,expected", [("mcp", True), ("mcp-helper", True), ("sentinel", True), ("phone", False), ("mcpx", False)])
def test_program_token(name, expected):
    assert pairing.program_token(name) is expected


# —— new_code ——————————————————————————————————————————————————————————


def test_new_code_shape_and_storage():
    code, expires = pairing.new_code("Test Phone", 10)
    assert len(code) == pairing.CODE_LEN
    assert set(code) <= set(pairing.ALPHABET)
    entries = stored()["pairing"]
    assert len(entries) == 1
    assert entries[0]["hash"] == pairing._hash(code)
    assert code not in str(config.raw(fresh=True))  # 只存哈希
    assert entries[0]["name"] == "Test Phone"
    assert entries[0]["expires"] == expires
    left = datetime.fromisoformat(expires) - datetime.now(timezone.utc)
    assert timedelta(minutes=9) < left <= timedelta(minutes=10)
    assert stored()["tokens"] == {"laptop": "existing"}  # 别的令牌不动


@pytest.mark.parametrize("minutes,lo,hi", [(0, 0, 1), (-5, 0, 1), (999, 59, 60)])
def test_new_code_minutes_clamped(minutes, lo, hi):
    _, expires = pairing.new_code("", minutes)
    left = datetime.fromisoformat(expires) - datetime.now(timezone.utc)
    assert timedelta(minutes=lo) < left <= timedelta(minutes=hi)


def test_new_code_name_truncated():
    pairing.new_code("x" * 100)
    assert stored()["pairing"][0]["name"] == "x" * 40


def test_new_code_drops_expired_and_keeps_last_20(server_json):
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(timespec="seconds")
    server_json({"auth": {"pairing": [{"hash": "old", "expires": past, "name": ""}, "junk"]}})
    pairing.new_code()
    assert all(p["hash"] != "old" for p in stored()["pairing"])
    for _ in range(25):
        pairing.new_code()
    assert len(stored()["pairing"]) == 20


# —— redeem ———————————————————————————————————————————————————————————


def test_redeem_success_creates_token_and_consumes_code():
    code, _ = pairing.new_code("Test Phone")
    out = pairing.redeem(code.lower()[:4] + "-" + code[4:], "ignored", "10.0.0.2")
    assert out["ok"] is True
    assert out["appName"] == settings.app_name
    assert out["name"].startswith("device-test-phone-")
    tokens = stored()["tokens"]
    assert tokens[out["name"]] == out["token"]
    assert tokens["laptop"] == "existing"
    assert stored()["pairing"] == []
    again = pairing.redeem(code, "x", "10.0.0.2")  # 只能用一次
    assert again["ok"] is False and again["status"] == 403


def test_redeem_uses_device_when_code_has_no_name():
    code, _ = pairing.new_code("")
    out = pairing.redeem(code, "Pixel 8", "src")
    assert out["name"].startswith("device-pixel-8-")


def test_redeem_name_collision_gets_suffix(monkeypatch):
    c1, _ = pairing.new_code("same")
    c2, _ = pairing.new_code("same")
    fixed = datetime(2026, 1, 2, 3, 4)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed if tz is None else datetime.now(tz)

    monkeypatch.setattr(pairing, "datetime", FrozenDatetime)
    a = pairing.redeem(c1, "", "s")
    b = pairing.redeem(c2, "", "s")
    assert a["name"] == "device-same-01020304"
    assert b["name"] == "device-same-01020304x"


def test_redeem_wrong_code_counts_failure():
    pairing.new_code()
    out = pairing.redeem("ZZZZZZZZ", "phone", "10.0.0.9")
    assert out == {"ok": False, "status": 403, "error": out["error"]}
    assert "Wrong pairing code" in out["error"]
    assert len(pairing._fails["10.0.0.9"]) == 1
    assert set(stored()["tokens"]) == {"laptop"}


def test_redeem_expired_code(server_json):
    past = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat(timespec="seconds")
    server_json({"auth": {"pairing": [{"hash": pairing._hash("ABCD2345"), "expires": past, "name": ""}]}})
    out = pairing.redeem("ABCD2345", "phone", "s")
    assert out["ok"] is False and out["status"] == 403


@pytest.mark.parametrize("code", ["", "   ", "-"])
def test_redeem_empty_code(code):
    assert pairing.redeem(code, "phone", "s")["status"] == 403


def test_rate_limit_per_source():
    for _ in range(pairing.PER_SOURCE):
        assert pairing.redeem("WRONG000", "p", "attacker")["status"] == 403
    code, _ = pairing.new_code()
    assert pairing.redeem(code, "p", "attacker")["status"] == 429  # 码对也不行
    assert pairing.redeem(code, "p", "someone-else")["ok"] is True


def test_rate_limit_total():
    for i in range(pairing.TOTAL):
        pairing._fails.setdefault(f"src-{i}", []).append(time.monotonic())
    assert pairing.limited("fresh-source") is True


def test_rate_limit_window_expires():
    old = time.monotonic() - pairing.WINDOW - 1
    pairing._fails["slow"] = [old] * (pairing.PER_SOURCE + 3)
    assert pairing.limited("slow") is False
    assert "slow" not in pairing._fails  # 过期的记录清掉


def test_limited_empty():
    assert pairing.limited("anyone") is False
