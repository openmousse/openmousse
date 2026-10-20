"""第 8 批：对讲（app 的对讲页）在服务器这边只多了一问：GET /api/chat/transcribe 说语音转写配好了没有（只看钥匙在不在，
不转写、不连外面）；没配时 POST /api/chat/transcribe 回 502，说明里有 OPENAI_API_KEY（app 认这个，说清楚是服务器没配）。
钥匙都是编的，不连 OpenAI。"""
from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import files
import secretstore
import version


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(files.router)
    return TestClient(app)


def test_talk_is_a_feature():
    assert "talk" in version.FEATURES


def test_ready_without_a_key_says_why(client, monkeypatch):
    monkeypatch.setattr(secretstore, "lookup", lambda name: ("", None))
    r = client.get("/api/chat/transcribe")
    assert r.status_code == 200
    j = r.json()
    assert j["ok"] is True and j["ready"] is False
    assert "OPENAI_API_KEY" in j["why"]


def test_ready_with_a_key_and_nothing_sent(client, monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(secretstore, "lookup", lambda name: (seen.append(name) or "sk-fake-123", "env"))

    def no_network(*a, **k):
        raise AssertionError("asking whether transcription is set up must not call out")
    monkeypatch.setattr(httpx, "post", no_network)
    j = client.get("/api/chat/transcribe").json()
    assert j == {"ok": True, "ready": True, "why": None}
    assert seen == ["OPENAI_API_KEY"]


def test_transcribing_without_a_key_names_the_key(client, monkeypatch):
    monkeypatch.setattr(secretstore, "lookup", lambda name: ("", None))
    r = client.post("/api/chat/transcribe", files={"file": ("talk.m4a", b"\x00" * 64, "audio/m4a")})
    assert r.status_code == 502
    assert "OPENAI_API_KEY" in r.json()["detail"]
    assert not list((files.UPLOAD_DIR / "_voice").glob("*talk.m4a"))  # 不存盘


def test_ready_follows_the_engine_stt_choice(client, monkeypatch):
    """配了 engine.use.stt（Groq、Deepgram、本机 whisper……）、没有 OpenAI 的钥匙：照内核那一家说（审查：原来一律要 OPENAI_API_KEY）。"""
    from engine import media, stt
    monkeypatch.setattr(secretstore, "lookup", lambda name: ("", None))
    monkeypatch.setattr(files, "_engine_stt", lambda: True)
    monkeypatch.setattr(media, "problem", lambda purpose: None)
    monkeypatch.setattr(stt, "on", lambda: True)
    assert client.get("/api/chat/transcribe").json() == {"ok": True, "ready": True, "why": None}
    monkeypatch.setattr(stt, "on", lambda: False)
    j = client.get("/api/chat/transcribe").json()
    assert j["ready"] is False and "engine.use.stt" in j["why"] and "OPENAI_API_KEY" not in j["why"]
    monkeypatch.setattr(media, "problem", lambda purpose: "engine.use.stt: nope isn't a known provider")
    monkeypatch.setattr(stt, "on", lambda: True)
    j = client.get("/api/chat/transcribe").json()
    assert j == {"ok": True, "ready": False, "why": "engine.use.stt: nope isn't a known provider"}
