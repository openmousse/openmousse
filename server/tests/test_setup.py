"""通用的选择层（内核第 9 步）：服务商目录、密钥文件、/api/setup/…、按用途选模型、搜索、记忆、向量、用量、安装器的 builtin 这条路、
命令行的三步。不连真的服务商、不花钱：模型是 evals/fakemodel.py 的假服务，本机的 Ollama、搜索、向量都是这里起的假服务；
请求经 main.app 打（/api 的令牌、guard、主人设备那一关都是真的）。"""
from __future__ import annotations

import contextlib
import io
import json
import os
import socket
import sqlite3
import stat
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

import config
import secretstore
from evals.fakeembed import FakeEmbedServer
from evals.fakemodel import FakeModelServer

TOKENS = {"phone": "tok-phone", "mcp": "tok-mcp", "api": "tok-api", "sentinel": "tok-sentinel", "mcp-books": "tok-mcpb"}
H = {"Authorization": "Bearer tok-phone"}
KEY = "sk-test-0123456789abcdWXYZ"           # 假的密钥：测试里到处找它，一处都不许出现
OTHER = "sk-test-9876543210zyxwQRST"
REPO = Path(__file__).resolve().parents[2]

# 原来 engine/models.py 里写死的那十三家（第 9 步以前的样子）：目录生成的必须一项不差
OLD_PROVIDERS = {
    "anthropic": {"api": "anthropic", "key_env": "ANTHROPIC_API_KEY"},
    "openai": {"api": "openai", "url": "https://api.openai.com/v1", "key_env": "OPENAI_API_KEY", "max_tokens_field": "max_completion_tokens",
               "reasoning_effort": True, "effort_map": {}, "tool_id_max": 40},
    "moonshot": {"api": "openai", "url": "https://api.moonshot.ai/v1", "key_env": "MOONSHOT_API_KEY", "reasoning_replay": "all", "mid_system": True,
                 "reasoning_effort": True, "effort_map": {"xhigh": "high"}, "schema_fix": True, "reasoning_pad": True},
    "deepseek": {"api": "openai", "url": "https://api.deepseek.com", "key_env": "DEEPSEEK_API_KEY", "reasoning_replay": "all", "images": False,
                 "reasoning_pad": True},
    "dashscope": {"api": "openai", "url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1", "key_env": "DASHSCOPE_API_KEY"},
    "google": {"api": "openai", "url": "https://generativelanguage.googleapis.com/v1beta/openai", "key_env": "GEMINI_API_KEY"},
    "openrouter": {"api": "openai", "url": "https://openrouter.ai/api/v1", "key_env": "OPENROUTER_API_KEY"},
    "xai": {"api": "openai", "url": "https://api.x.ai/v1", "key_env": "XAI_API_KEY"},
    "mistral": {"api": "openai", "url": "https://api.mistral.ai/v1", "key_env": "MISTRAL_API_KEY", "tool_id": "mistral9", "reasoning_replay": "none"},
    "groq": {"api": "openai", "url": "https://api.groq.com/openai/v1", "key_env": "GROQ_API_KEY", "reasoning_replay": "none"},
    "ollama": {"api": "openai", "url": "http://127.0.0.1:11434/v1", "key_env": "", "require_finish": False},
    "lmstudio": {"api": "openai", "url": "http://127.0.0.1:1234/v1", "key_env": "", "require_finish": False},
    "vllm": {"api": "openai", "url": "http://127.0.0.1:8000/v1", "key_env": "", "require_finish": False},
}


def closed_port() -> int:
    """一个现在没人在听的本机端口（连上去马上被拒）。"""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Fake(ThreadingHTTPServer):
    """一个小的假服务：routes = {(方法, 路径): 函数(handler, query, body) → (状态码, JSON)}。"""

    def __init__(self, routes: dict) -> None:
        self.routes, self.hits = routes, []
        fake = self

        class H_(BaseHTTPRequestHandler):
            def log_message(self, *_a):
                pass

            def _go(self, method: str) -> None:
                u = urlsplit(self.path)
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0)) if method == "POST" else b""
                fake.hits.append((method, u.path, dict(self.headers)))
                fn = fake.routes.get((method, u.path))
                status, payload = fn(self, parse_qs(u.query), body) if fn else (404, {"error": {"message": "no route"}})
                data = json.dumps(payload).encode() if payload is not None else b""
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self._go("GET")

            def do_POST(self):
                self._go("POST")

        super().__init__(("127.0.0.1", 0), H_)
        self.daemon_threads = True
        threading.Thread(target=self.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"

    def stop(self) -> None:
        self.shutdown()
        self.server_close()


def fake_ollama() -> Fake:
    return Fake({("GET", "/api/version"): lambda h, q, b: (200, {"version": "0.0.0-test"}),
                 ("GET", "/api/tags"): lambda h, q, b: (200, {"models": [{"name": "llama-test:latest", "model": "llama-test:latest"},
                                                                         {"name": "nomic-embed-text:latest", "model": "nomic-embed-text:latest"}]})})


def fake_brave(key: str) -> Fake:
    def search(h, q, b):
        if h.headers.get("X-Subscription-Token") != key:
            return 401, {"error": {"message": "bad token"}}
        return 200, {"web": {"results": [{"title": "OpenMousse", "url": "https://example.org/mousse", "description": "a test result"}]}}
    return Fake({("GET", "/res/v1/web/search"): search})


@pytest.fixture
def env(server_json, tmp_path, monkeypatch):
    """claw builtin、几把令牌；列过的模型、密钥文件放 tmp；本机的探测一律指到关着的端口；进程环境里没有各家的密钥；
    本机的 Claude Code / Codex 当没查到（不碰这台机器上真的命令）。cfg(**engine 段之外的键) 改 server.json。"""
    import main
    from engine import catalog, models, runners, setup_api
    monkeypatch.setattr(setup_api, "_mcache_path", lambda: tmp_path / "setup-models.json")
    setup_api.reset()
    for name in {e["key_env"] for e in catalog.CLOUD.values()} | {s["key_env"] for s in catalog.SEARCH.values() if s.get("key_env")} | {"SEARXNG_URL", "MYLAB_API_KEY"}:
        monkeypatch.delenv(name, raising=False)
    dead = f"http://127.0.0.1:{closed_port()}/v1"
    for pid in catalog.LOCAL:
        monkeypatch.setitem(catalog.LOCAL[pid], "url", dead)
        monkeypatch.setitem(catalog.LOCAL[pid], "alt", [])
    monkeypatch.setattr(runners, "_warm", True)
    monkeypatch.setattr(runners, "_found", {})
    monkeypatch.setattr(runners, "_checked_at", time.monotonic())
    monkeypatch.setattr(runners, "_codex_bin", None)
    monkeypatch.setattr(models, "_oc_cache", None)

    def cfg(**data) -> Path:
        base = {"claw": {"kind": "builtin"}, "auth": {"tokens": TOKENS}, "timezone": "UTC", "language": "en"}
        return server_json({**base, **data})
    cfg()

    class E:
        pass
    e = E()
    e.cfg, e.dir, e.client = cfg, tmp_path, TestClient(main.app)
    e.conf = lambda: json.loads(config.CONFIG_PATH.read_text(encoding="utf8"))
    e.backups = lambda: sorted(p.name for p in tmp_path.glob("server.json.bak-*"))
    return e


def everything(e) -> str:
    """服务器写下的每一个文件（server.json、备份、列过的模型）拼起来：查密钥有没有漏进去。secrets.env 本身除外。"""
    out = []
    for p in e.dir.rglob("*"):
        if p.is_file() and p.name not in ("secrets.env",) and not p.name.endswith(".lock"):
            out.append(p.read_text(encoding="utf8", errors="replace"))
    return "\n".join(out)


# —— 目录 ——————————————————————————————————————————————————————————————————————————

def test_catalog_entries_have_every_required_field():
    from engine import catalog
    assert catalog.problems() == []
    assert 20 <= len(catalog.CLOUD) <= 30
    for pid, e in catalog.CLOUD.items():
        for k in catalog.REQUIRED:
            assert e.get(k) or (k == "url" and e[k] == ""), (pid, k)  # 没有默认地址的（Azure）：url 空着
        assert e["api"] in ("openai", "anthropic", "openai-responses")
        assert e["list"] in ("openai", "anthropic", "ollama", "none")
        assert all(s.startswith("https://") for s in e["sources"]), pid
        assert e.get("test_model") or e["list"] != "none", pid
    for pid, e in catalog.LOCAL.items():
        assert e["key_env"] == "" and e["detect"]["path"].startswith("/") and e["url"].startswith("http://127.0.0.1:"), pid
    assert set(catalog.LOGINS) == {"claude-code", "chatgpt", "codex"}
    from engine import web
    assert set(catalog.SEARCH) == set(web.PROVIDERS)  # 搜索的两半（请求怎么发 / 给人看的）对得上
    for eid, x in catalog.EMBEDDINGS.items():
        assert x["provider"] in {**catalog.CLOUD, **catalog.LOCAL}, eid


def test_argon_is_in_the_catalog_and_recommendations_are_data():
    """Gemini 4 Argon 进了 Google 那一家（写作推荐的型号、型号页和价目页在 sources 里）；推荐是数据：每个用途、单独写了理由的每个型号
    都有中英两种「理由 + 代价」；写进代价里的价和价目表对得上。"""
    from engine import catalog, models
    g = catalog.CLOUD["google"]
    assert g["suggest"] == {"cheap": "gemini-3.5-flash-lite", "writing": "gemini-4-argon"}
    assert {"https://ai.google.dev/gemini-api/docs/models/gemini-4-argon", "https://ai.google.dev/gemini-api/docs/pricing"} <= set(g["sources"])
    assert set(catalog.RECOMMEND) == {"chat", "background", "writing", "card", "doorman", "embeddings"}
    for purpose, r in catalog.RECOMMEND.items():
        for note in (r, *(r.get("models") or {}).values()):
            assert all(isinstance(note[k], tuple) and len(note[k]) == 2 and all(note[k]) for k in ("why", "cost")), purpose
    for ref in catalog.RECOMMEND["writing"]["models"]:  # 单独写了理由的，都是哪一家 suggest 里写作推荐的那个
        pid, model = ref.split("/", 1)
        assert catalog.CLOUD[pid]["suggest"]["writing"] == model, ref
    zh, en = catalog.RECOMMEND["writing"]["models"]["google/gemini-4-argon"]["cost"]
    old, (since, new) = models.PRICES["google/gemini-4-argon"], models.PRICES_FROM["google/gemini-4-argon"][0]
    assert since == "2026-12-01" and "1 December" in en and "12 月 1 日起" in zh
    assert f"${old[0]:g} in / ${old[1]:g} out" in en and f"${new[0]:g} / ${new[1]:g}" in en and f"${new[0]:g} / ${new[1]:g}" in zh


def test_eval_based_picks_are_data_and_their_prices_match_the_price_list():
    """名片 Agent、Doorman 的推荐（评测结果）写成数据：prefer 里的每一个都有自己的中英「理由 + 代价」、在目录里认得出那一家、
    不是套餐；写进代价里的每百万 token 的价和价目表对得上。"""
    from engine import catalog, models
    assert catalog.RECOMMEND["card"]["prefer"] == ("anthropic/claude-opus-5-5", "moonshot/kimi-k3")
    assert catalog.RECOMMEND["doorman"]["prefer"] == ("moonshot/kimi-k3",)
    for purpose in ("card", "doorman"):
        r = catalog.RECOMMEND[purpose]
        for ref in r["prefer"]:
            pid, model = ref.split("/", 1)
            assert pid in catalog.CLOUD and not models.subscription(ref) and models.valid_ref(ref), ref
            zh, en = r["models"][ref]["cost"]
            p = models.PRICES[ref]
            assert f"${p[0]:g} in / ${p[1]:g} out per million tokens" in en and f"输入 ${p[0]:g}、输出 ${p[1]:g}" in zh, (purpose, ref)
    door = catalog.RECOMMEND["doorman"]["models"]["moonshot/kimi-k3"]
    assert "23/23" in door["why"][1] and "3/3" in door["why"][1] and "6.9 s" in door["why"][1] and "0.25" in door["cost"][1]
    assert "1–2" in catalog.RECOMMEND["card"]["models"]["anthropic/claude-opus-5-5"]["cost"][1]


def test_the_old_thirteen_providers_are_unchanged_and_new_ones_join_the_engine():
    from engine import catalog, models
    for name, old in OLD_PROVIDERS.items():
        assert models.PROVIDERS[name] == old, name
    assert set(models.PROVIDERS) == set(catalog.CLOUD) | set(catalog.LOCAL)
    for name in ("together", "fireworks", "zai", "siliconflow", "llamacpp", "omlx"):
        p = models.provider(name)
        assert p["api"] == "openai" and p["url"].startswith("http") and models.valid_ref(f"{name}/some-model")
    assert "url" not in models.PROVIDERS["anthropic"]  # 写了 url，官方 SDK 就不发 beta 的 block_binding、fallbacks


def test_regions_pick_their_own_address_key_page_and_test_model():
    from engine import catalog
    z = catalog.CLOUD["zai"]
    assert catalog.region(z, "cn")["url"] == "https://open.bigmodel.cn/api/paas/v4"
    assert catalog.region(z, "cn")["test_model"] == "glm-4.7-flash" and catalog.region(z, None)["test_model"] == "glm-5.3-flash"
    assert catalog.region_of(z, "https://open.bigmodel.cn/api/paas/v4/") == "cn"
    assert catalog.region_of(z, "https://proxy.example.org/v1") == "custom"
    assert catalog.region_of(catalog.CLOUD["openai"], "https://api.openai.com/v1") is None
    assert catalog.region(catalog.CLOUD["dashscope"], "cn")["list_url"] == "https://dashscope.aliyuncs.com/api/v1/models"


def test_model_lists_in_every_shape_the_providers_answer():
    from engine import probe
    assert probe.ids_of({"object": "list", "data": [{"id": "a"}, {"id": "b"}, {"id": "a"}]}) == ["a", "b"]           # OpenAI 兼容
    assert probe.ids_of([{"id": "Qwen/Qwen3.5-9B", "type": "chat"}]) == ["Qwen/Qwen3.5-9B"]                          # Together：直接一个列表
    assert probe.ids_of({"models": [{"name": "accounts/fireworks/models/x", "displayName": "X"}]}) == ["accounts/fireworks/models/x"]
    assert probe.ids_of({"models": [{"name": "llama3.2:latest", "model": "llama3.2:latest"}]}) == ["llama3.2:latest"]  # Ollama
    assert probe.ids_of({"output": {"models": [{"model": "qwen3.8-flash", "name": "通义千问 Flash"}]}}) == ["qwen3.8-flash"]  # 阿里云百炼原生
    assert probe.ids_of({"models": [{"slug": "gpt-test", "display_name": "GPT"}]}) == ["gpt-test"]
    with pytest.raises(ValueError):
        probe.ids_of({"nothing": True})


def test_probe_errors_never_carry_the_key():
    from engine import probe
    e = probe.error("auth", f"HTTP 401: Your api key: ****WXYZ is invalid; sent {KEY}", KEY)
    assert e["kind"] == "auth" and KEY not in e["message"] and "WXYZ" not in e["message"] and "HTTP 401" in e["message"]
    assert probe.kind_of(402) == "billing" and probe.kind_of(404) == "not_found" and probe.kind_of(429) == "rate" and probe.kind_of(503) == "server"


# —— 密钥文件 ————————————————————————————————————————————————————————————————————————

def test_secrets_file_is_private_atomic_and_never_echoes(env):
    info = secretstore.put("DEEPSEEK_API_KEY", KEY)
    p = secretstore.path()
    assert p == env.dir / "secrets.env"
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert KEY not in json.dumps(info) and info == {"name": "DEEPSEEK_API_KEY", "present": True, "source": "app", "hint": "…WXYZ",
                                                     "savedAt": info["savedAt"]}
    assert datetime.fromisoformat(info["savedAt"])
    secretstore.put("DEEPSEEK_API_KEY", OTHER)  # 换一把：同名的那一行换掉，不留两行
    text = p.read_text(encoding="utf8")
    assert text.count("DEEPSEEK_API_KEY=") == 1 and OTHER in text and KEY not in text
    assert not list(env.dir.glob(".secrets.env.*.tmp"))  # 临时文件换上去了，没剩
    secretstore.put("SHORT_ONE", "abc12345")
    assert secretstore.info("SHORT_ONE")["hint"] is None  # 太短的不给末四位
    assert secretstore.delete("DEEPSEEK_API_KEY") and not secretstore.delete("DEEPSEEK_API_KEY")
    assert "DEEPSEEK_API_KEY" not in p.read_text(encoding="utf8") and "SHORT_ONE=abc12345" in p.read_text(encoding="utf8")
    for bad in ("has space", "quote'd", "line\nbreak", "中文密钥"):
        with pytest.raises(secretstore.SecretError) as ei:
            secretstore.put("SOME_KEY", bad)
        assert bad not in str(ei.value)
    with pytest.raises(secretstore.SecretError):
        secretstore.put("lower-case", "value-123456789")


def test_a_loose_old_secrets_file_is_tightened_on_write(env):
    p = env.dir / "secrets.env"
    p.write_text("# mine\nKEEP_ME=hand-written-value\n", encoding="utf8")
    os.chmod(p, 0o644)
    secretstore.put("GROQ_API_KEY", KEY)
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    text = p.read_text(encoding="utf8")
    assert "# mine" in text and "KEEP_ME=hand-written-value" in text  # 用户自己的行原样留着


def test_lookup_order_is_environment_then_app_then_env_file(env, monkeypatch, tmp_path):
    from engine import models
    import claw
    ef = tmp_path / "old.env"
    ef.write_text("export MOONSHOT_API_KEY='from-env-file-1234567'\nXAI_API_KEY=xai-from-file-12345678\n", encoding="utf8")
    env.cfg(env_file=str(ef))
    assert models.env_value("MOONSHOT_API_KEY") == "from-env-file-1234567" and secretstore.lookup("MOONSHOT_API_KEY")[1] == "env_file"
    secretstore.put("MOONSHOT_API_KEY", KEY)
    assert models.env_value("MOONSHOT_API_KEY") == KEY and secretstore.lookup("MOONSHOT_API_KEY")[1] == "app"
    monkeypatch.setenv("MOONSHOT_API_KEY", OTHER)
    assert models.env_value("MOONSHOT_API_KEY") == OTHER and secretstore.lookup("MOONSHOT_API_KEY")[1] == "env"
    assert claw.env_value("MOONSHOT_API_KEY") == OTHER
    monkeypatch.delenv("MOONSHOT_API_KEY")
    assert claw.env_value("MOONSHOT_API_KEY") == KEY  # claw 的令牌也认 app 里存的
    assert models.has_key("moonshot/kimi-k3") and models.has_key("xai/grok-4.3")


def test_agents_file_tools_never_reach_the_secrets_file(env, tmp_path, monkeypatch):
    from engine import host_files
    ws = tmp_path  # 工作区把 server.json、secrets.env 套在里面：也照样挡
    monkeypatch.setattr(config.settings, "workspace", ws)
    secretstore.put("OPENAI_API_KEY", KEY)
    assert (tmp_path / "secrets.env").resolve() in host_files.fenced("main", ws.resolve())
    assert host_files.secret_name("secrets.env")


# —— 主人的设备 ————————————————————————————————————————————————————————————————————————

def test_only_the_owners_devices_reach_setup(env, monkeypatch):
    import main
    c = env.client
    assert c.get("/api/setup", headers=H).status_code == 200
    for tok in ("tok-mcp", "tok-api", "tok-sentinel", "tok-mcpb"):  # 给程序的令牌：guard 早就挡了
        assert c.get("/api/setup", headers={"Authorization": f"Bearer {tok}"}).status_code == 403
    assert c.get("/api/setup").status_code == 401
    r = c.get("/api/setup/models?probe=0", headers={**H, "X-Mousse-Via": "mcp"})  # 脚本、/mcp 替 Agent 发的
    assert r.status_code == 403 and r.json()["ok"] is False
    monkeypatch.setattr(main, "principal_of", lambda req: "loopback")  # 只靠本机免令牌进来的
    assert c.get("/api/setup").status_code == 403 and c.get("/api/health").status_code == 200
    monkeypatch.setattr(main, "principal_of", lambda req: "tailscale:my-phone")
    assert c.get("/api/setup").status_code == 200
    assert c.get("/api/engine/usage").status_code == 200


# —— 服务商：列、存、测、删 ————————————————————————————————————————————————————————————————

def test_providers_list_covers_every_way_to_run_a_model(env):
    r = env.client.get("/api/setup/models?probe=0", headers=H).json()
    kinds = {}
    for row in r["providers"]:
        kinds.setdefault(row["kind"], []).append(row["id"])
    from engine import catalog
    assert kinds["cloud"] == list(catalog.CLOUD) and kinds["local"] == list(catalog.LOCAL)
    assert kinds["login"] == ["claude-code", "chatgpt", "codex"]
    ds = next(x for x in r["providers"] if x["id"] == "deepseek")
    assert ds["connected"] is False and ds["key"]["present"] is False and ds["keyUrl"].startswith("https://") and ds["caps"]["reasoning"] is True
    mo = next(x for x in r["providers"] if x["id"] == "moonshot")
    assert mo["region"] == "global" and [g["id"] for g in mo["regions"]] == ["global", "cn"]
    assert r["custom"]["apis"] == ["openai", "anthropic"]


def test_azure_needs_your_own_address(env):
    """Azure 没有默认地址（第 7 批）：只给密钥不存、测不了；以前存了密钥的也不算连上、不会被挑成默认；填了地址才连上。"""
    from engine import models
    from engine.errors import EngineError
    c = env.client
    r = c.put("/api/setup/models/azure", headers=H, json={"key": KEY})
    assert r.status_code == 400 and r.json()["field"] == "url", r.text
    assert not secretstore.get("AZURE_OPENAI_API_KEY")
    r = c.post("/api/setup/models/azure/test", headers=H, json={"key": KEY})
    assert r.status_code == 400 and r.json()["field"] == "url", r.text
    secretstore.put("AZURE_OPENAI_API_KEY", KEY)
    row = next(p for p in c.get("/api/setup/models?probe=0", headers=H).json()["providers"] if p["id"] == "azure")
    assert row["connected"] is False and row["url"] == ""
    with pytest.raises(EngineError):
        models.provider("azure")
    url = "https://my-resource.openai.azure.com/openai/v1"
    j = c.put("/api/setup/models/azure", headers=H, json={"url": url}).json()
    assert j["provider"]["connected"] is True and env.conf()["engine"]["providers"]["azure"]["url"] == url
    assert models.provider("azure")["url"] == url and KEY not in everything(env)


def test_saving_a_cloud_key_writes_only_the_secrets_file_and_auto_uses_the_only_provider(env):
    c = env.client
    r = c.put("/api/setup/models/deepseek", headers=H, json={"key": KEY})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["provider"]["connected"] is True and j["provider"]["key"]["hint"] == "…WXYZ" and j["saved"] == {"key": True, "config": False}
    assert secretstore.get("DEEPSEEK_API_KEY") == KEY
    # 只有这一家、主对话还没选：顺手用它（推荐的那个型号），外人回合走内核
    assert j["auto"]["chat"] == "deepseek/deepseek-flash"
    eng = env.conf()["engine"]
    assert eng["model"] == "deepseek/deepseek-flash" and eng["use"] == {"card": True, "doorman": True, "json": True}
    assert KEY not in r.text and KEY not in everything(env)
    # 第二家：不再自动改
    c.put("/api/setup/models/moonshot", headers=H, json={"key": OTHER, "region": "cn"})
    eng = env.conf()["engine"]
    assert eng["model"] == "deepseek/deepseek-flash" and eng["providers"]["moonshot"] == {"url": "https://api.moonshot.cn/v1"}
    from engine import models
    assert models.provider("moonshot")["url"] == "https://api.moonshot.cn/v1"
    # 换回默认的地区：改过的那一项拿掉，不留空的 {}
    c.put("/api/setup/models/moonshot", headers=H, json={"region": "global"})
    assert "providers" not in env.conf()["engine"]
    assert c.get("/api/health", headers=H).json()["no_model"] is False


def test_suggested_models_follow_the_chosen_region(env):
    c = env.client
    j = c.put("/api/setup/models/zai", headers=H, json={"key": KEY, "region": "cn"}).json()
    assert j["auto"]["chat"] == "zai/glm-4.7-flash" and j["provider"]["region"] == "cn"  # 国内站的便宜型号，不是国际站的
    assert j["provider"]["suggest"] == {"cheap": "glm-4.7-flash"} and j["provider"]["keyUrl"] == "https://bigmodel.cn/usercenter/proj-mgmt/apikeys"
    refs = [x["ref"] for x in c.get("/api/setup/purposes?probe=0", headers=H).json()["choices"]["models"]]
    assert refs == ["zai/glm-4.7-flash"]


def test_every_write_backs_up_server_json_first_and_keeps_twenty(env):
    before = config.CONFIG_PATH.read_bytes()
    c = env.client
    r = c.put("/api/setup/models/moonshot", headers=H, json={"region": "cn"})
    name = r.json()["backup"]
    assert name and (env.dir / name).read_bytes() == before and stat.S_IMODE((env.dir / name).stat().st_mode) == 0o600
    for i in range(24):
        c.put("/api/setup/models/moonshot", headers=H, json={"region": "global" if i % 2 == 0 else "cn"})
    assert len(env.backups()) == 20
    n = len(env.backups())
    c.put("/api/setup/models/moonshot", headers=H, json={"region": "cn"})  # 和现在一样：不写、不备份
    assert len(env.backups()) == n and r.json()["saved"]["config"] is True


def test_a_broken_server_json_is_never_overwritten(env):
    config.CONFIG_PATH.write_text('{"auth": {"tokens": {"phone": "tok-phone"}}, oops', encoding="utf8")
    config._cache = None
    import main
    from engine import setup_api
    with pytest.raises(setup_api.SetupError):
        setup_api.edit(lambda d: d.update(x=1))
    assert config.CONFIG_PATH.read_text(encoding="utf8").endswith("oops") and not env.backups()
    assert main  # 读不了的 server.json 连令牌都没有：接口那边本来就进不来


def test_test_lists_models_with_a_fake_server_and_never_echoes_the_key(env):
    with FakeModelServer([], key=KEY, catalog=[{"slug": "fake-big"}, {"slug": "fake-small"}]) as fm:
        c = env.client
        r = c.post("/api/setup/models/mylab/test", headers=H, json={"url": fm.openai_url, "key": KEY})  # 还没存：只测，不写
        assert r.json()["ok"] is True and r.json()["models"] == ["fake-big", "fake-small"] and r.json()["via"] == "list"
        assert not (env.dir / "secrets.env").exists() and "engine" not in env.conf() and not env.backups()
        bad = c.post("/api/setup/models/mylab/test", headers=H, json={"url": fm.openai_url, "key": OTHER}).json()
        assert bad["ok"] is False and bad["error"]["kind"] == "auth" and OTHER not in json.dumps(bad)
        # 存下来（自定义的 OpenAI 兼容地址）：名字、地址进 server.json，密钥进 secrets.env
        j = c.put("/api/setup/models/mylab", headers=H, json={"url": fm.openai_url + "/", "name": "My lab", "key": KEY}).json()
        prov = env.conf()["engine"]["providers"]["mylab"]
        assert prov == {"api": "openai", "url": fm.openai_url, "name": "My lab", "key_env": "MYLAB_API_KEY"}
        assert j["provider"]["kind"] == "custom" and j["provider"]["connected"] is True
        assert j["auto"]["chat"] in ("mylab/fake-big", "mylab/fake-small")  # 只有这一家：用它列出来的
        again = c.post("/api/setup/models/mylab/test", headers=H, json={}).json()  # 用存下的
        assert again["ok"] is True and again["models"] == ["fake-big", "fake-small"]
        everything_text = everything(env) + json.dumps(j) + json.dumps(again)
        assert KEY not in everything_text and OTHER not in everything_text


def test_a_provider_that_cant_list_models_gets_one_tiny_request(env):
    with FakeModelServer([], key=KEY) as fm:
        env.cfg(engine={"providers": {"nvidia": {"url": fm.openai_url}}})  # NVIDIA 没写列模型的接口：list = none
        r = env.client.post("/api/setup/models/nvidia/test", headers=H, json={"key": KEY}).json()
        assert r["ok"] is True and r["via"] == "request" and r["models"] == ["openai/gpt-oss-20b"]
        sent = fm.of("openai")
        assert len(sent) == 1 and sent[0]["model"] == "openai/gpt-oss-20b" and sent[0]["body"]["max_tokens"] == 16
        r = env.client.post("/api/setup/models/nvidia/test", headers=H, json={"key": KEY, "model": "meta/llama-test"}).json()
        assert r["ok"] and r["listed"] is False and fm.of("openai")[-1]["model"] == "meta/llama-test"
        assert env.client.post("/api/setup/models/nvidia/test", headers=H, json={}).json()["error"]["kind"] == "no_key"
        # 列不了模型：测通的型号记下来，和推荐的一起能选
        row = next(x for x in env.client.get("/api/setup/models?probe=0", headers=H).json()["providers"] if x["id"] == "nvidia")
        assert row["canList"] is False
        env.client.put("/api/setup/models/nvidia", headers=H, json={"key": KEY})
        p = env.client.get("/api/setup/purposes?probe=0", headers=H).json()
        assert {"nvidia/openai/gpt-oss-20b", "nvidia/meta/llama-test"} <= {x["ref"] for x in p["choices"]["models"]}
        assert p["providers"]["nvidia"]["listed"] is False
        assert env.client.put("/api/setup/purposes", headers=H, json={"chat": "nvidia/meta/llama-test"}).status_code == 200


def test_models_listed_on_one_address_dont_count_on_another(env):
    c = env.client
    row = lambda pid: next(x for x in c.get("/api/setup/models?probe=0", headers=H).json()["providers"] if x["id"] == pid)  # noqa: E731
    with FakeModelServer([], key=KEY) as fm:
        r = c.post("/api/setup/models/moonshot/test", headers=H, json={"key": KEY, "url": fm.openai_url}).json()  # 先测（还没存）……
        assert r["ok"] and r["via"] == "list" and r["listed"] is True and r["count"] > 0
        assert row("moonshot")["models"]["count"] == 0  # 存的还是默认的地址：那边没列过
        c.put("/api/setup/models/moonshot", headers=H, json={"key": KEY, "url": fm.openai_url})  # ……再存：测的时候列的照样算
        assert row("moonshot")["models"]["count"] == r["count"] and row("moonshot")["canList"] is True
        c.put("/api/setup/models/moonshot", headers=H, json={"region": "cn"})  # 换了地区：别处列的不算
        assert row("moonshot")["region"] == "cn" and row("moonshot")["models"]["count"] == 0
        refs = {x["ref"] for x in c.get("/api/setup/purposes?probe=0", headers=H).json()["choices"]["models"]}
        assert env.conf()["engine"]["model"] == "moonshot/fake-model"  # 只有这一家：存的时候自动选了列出来的那个
        assert refs == {"moonshot/kimi-k3", "moonshot/kimi-k2.6", "moonshot/fake-model"}  # 退回推荐的，加上配置里用着的
        c.put("/api/setup/models/moonshot", headers=H, json={"url": fm.openai_url})
        assert row("moonshot")["models"]["count"] == r["count"]


def test_anthropic_lists_models_with_its_own_headers(env):
    with FakeModelServer([], key=KEY, catalog=[{"slug": "claude-test-1"}]) as fm:
        env.cfg(engine={"providers": {"anthropic": {"url": fm.anthropic_url}}})
        r = env.client.post("/api/setup/models/anthropic/test", headers=H, json={"key": KEY}).json()
        assert r["ok"] is True and r["models"] == ["claude-test-1"]


def test_deleting_removes_only_what_the_app_saved(env, tmp_path):
    c = env.client
    j = c.put("/api/setup/models/groq", headers=H, json={"key": KEY}).json()
    assert j["auto"]["chat"] == "groq/openai/gpt-oss-20b"
    r = c.delete("/api/setup/models/groq", headers=H).json()
    assert r["removed"] == {"key": True, "config": False} and r["affected"] == ["chat"]  # 主对话还指着它：app 据此提醒换一个
    assert not secretstore.get("GROQ_API_KEY") and env.conf()["engine"]["model"] == "groq/openai/gpt-oss-20b"  # 别的不替用户改
    ef = tmp_path / "keys.env"
    ef.write_text("XAI_API_KEY=xai-in-the-env-file-123456\n", encoding="utf8")
    env.cfg(env_file=str(ef))
    r = c.delete("/api/setup/models/xai", headers=H)
    assert r.status_code == 409 and r.json()["source"] == "env_file" and "xai-in-the-env-file" not in r.text
    assert c.delete("/api/setup/models/nope-not-here", headers=H).status_code == 404


def test_deleting_a_custom_provider_takes_its_config_too(env):
    c = env.client
    c.put("/api/setup/models/lab2", headers=H, json={"url": "http://127.0.0.1:9/v1", "key": KEY})
    assert "lab2" in env.conf()["engine"]["providers"]
    r = c.delete("/api/setup/models/lab2", headers=H).json()
    assert r["removed"] == {"key": True, "config": True} and "providers" not in env.conf().get("engine", {})


def test_bad_bodies_are_refused_without_echoing_them(env):
    c = env.client
    r = c.put("/api/setup/models/deepseek", headers=H, json={"key": "SECRETVALUE with a space"})
    assert r.status_code == 400 and "SECRETVALUE" not in r.text and r.json()["field"] == "key"
    r = c.put("/api/setup/models/deepseek", headers={**H, "Content-Type": "application/json"}, content=b"sk-not-json-SECRETVALUE")
    assert r.status_code == 400 and "SECRETVALUE" not in r.text
    r = c.put("/api/setup/models/deepseek", headers=H, json={"key": 123456789012345})
    assert r.status_code == 400 and "123456789012345" not in r.text
    assert c.put("/api/setup/models/Bad_ID", headers=H, json={"url": "http://x.example/v1"}).status_code == 400
    assert c.put("/api/setup/models/chatgpt", headers=H, json={"key": KEY}).status_code == 400
    assert c.put("/api/setup/models/mylab", headers=H, json={"url": "http://user:pw@example.org/v1"}).status_code == 400
    assert c.put("/api/setup/models/moonshot", headers=H, json={"region": "mars"}).status_code == 400


# —— 本机、命令行登录 ————————————————————————————————————————————————————————————————————

def test_local_servers_are_detected_and_their_models_listed(env):
    fo = fake_ollama()
    try:
        env.cfg(engine={"providers": {"ollama": {"url": fo.url + "/v1"}}})
        r = env.client.get("/api/setup/models", headers=H).json()
        o = next(x for x in r["providers"] if x["id"] == "ollama")
        assert o["detected"] is True and o["version"] == "0.0.0-test" and o["models"] == ["llama-test:latest", "nomic-embed-text:latest"]
        assert all(not x["detected"] for x in r["providers"] if x["kind"] == "local" and x["id"] != "ollama")
        p = env.client.get("/api/setup/purposes", headers=H).json()
        refs = [x["ref"] for x in p["choices"]["models"]]
        assert refs == ["ollama/llama-test:latest", "ollama/nomic-embed-text:latest"]
        assert p["auto"] == {"provider": "ollama", "chat": "ollama/llama-test:latest", "outsider": True}  # 向量模型不当主对话
        assert [x["id"] for x in p["choices"]["embeddings"]] == ["ollama"]
        t = env.client.post("/api/setup/models/ollama/test", headers=H, json={}).json()
        assert t["ok"] and t["via"] == "detect"
    finally:
        fo.stop()


def test_detection_skips_this_servers_own_port_and_tells_clashing_runtimes_apart(env, monkeypatch):
    from engine import catalog, probe, setup_api
    monkeypatch.setattr(config.settings, "host", "127.0.0.1")
    monkeypatch.setattr(config.settings, "port", 18080)
    assert probe.own_port("http://127.0.0.1:18080/v1") and not probe.own_port("http://127.0.0.1:18081/v1")
    import asyncio
    assert asyncio.run(probe.detect_local(catalog.LOCAL["mlx"], "http://127.0.0.1:18080/v1"))["detected"] is False
    llama = Fake({("GET", "/health"): lambda h, q, b: (200, {"status": "ok"}), ("GET", "/props"): lambda h, q, b: (200, {"total_slots": 1}),
                  ("GET", "/v1/models"): lambda h, q, b: (200, {"object": "list", "data": [{"id": "model.gguf", "owned_by": "llamacpp"}]})})
    try:  # llama.cpp（Docker 版在 8080）和 mlx-lm 都回 /health ok：/props 也通的是 llama.cpp
        env.cfg(engine={"providers": {"mlx": {"url": llama.url + "/v1"}, "llamacpp": {"url": llama.url + "/v1"}}})
        found = asyncio.run(setup_api.detect_locals(fresh=True))
        assert found["llamacpp"]["detected"] and found["llamacpp"]["models"] == ["model.gguf"]
        assert found["mlx"]["detected"] is False
    finally:
        llama.stop()


def test_command_line_logins_are_reported_without_any_token(env, monkeypatch):
    from engine import chatgpt_plan, models, runners
    monkeypatch.setattr(runners, "_found", {"claude-code": {"bin": "/usr/bin/claude", "auth": "claude.ai", "billing": "subscription", "plan": "max"}})
    monkeypatch.setattr(runners, "offered", lambda: ["claude-code"])
    monkeypatch.setattr(chatgpt_plan, "usable", lambda: True)
    monkeypatch.setattr(chatgpt_plan, "model_ids", lambda: ["chatgpt/gpt-test"])
    r = env.client.get("/api/setup/models?probe=0", headers=H).json()
    cc = next(x for x in r["providers"] if x["id"] == "claude-code")
    assert cc["ready"] is True and cc["billing"] == "subscription" and cc["models"] == list(models.CLAUDE_CODE_MODELS)
    gp = next(x for x in r["providers"] if x["id"] == "chatgpt")
    assert gp["ready"] is True and gp["models"] == ["chatgpt/gpt-test"] and gp["login"]["start"] == "POST /api/engine/chatgpt/login"
    rows = json.dumps([cc, gp]).lower()
    assert not any(k in rows for k in ("access_token", "refresh_token", "id_token", "email", "/usr/bin/claude"))  # 不回任何令牌、身份、路径
    p = env.client.get("/api/setup/purposes?probe=0", headers=H).json()
    plans = {x["ref"]: x["plan"] for x in p["choices"]["models"]}
    assert plans["claude-code/sonnet"] == "claude-code" and plans["chatgpt/gpt-test"] == "chatgpt"
    accepts = {x["id"]: x.get("accepts") for x in p["purposes"]}
    assert accepts["outsider"] == {"plans": False} and accepts["writing"] == accepts["chat"] == {"plans": True}
    # 测一下 = 现查一遍（这里换成假的检测）
    monkeypatch.setattr(runners, "detect", lambda name: {"bin": "/usr/bin/claude", "auth": "claude.ai", "billing": "subscription"} if name == "claude-code" else None)
    monkeypatch.setattr("importlib.util.find_spec", lambda name: object())
    t = env.client.post("/api/setup/models/claude-code/test", headers=H, json={}).json()
    assert t["ok"] is True and t["via"] == "login"


# —— 按用途选模型 ————————————————————————————————————————————————————————————————————————

def test_only_connected_models_can_be_chosen_and_choices_land_in_the_engine_section(env):
    c = env.client
    assert c.get("/api/setup/purposes?probe=0", headers=H).json()["choices"]["models"] == []
    c.put("/api/setup/models/deepseek", headers=H, json={"key": KEY})
    c.put("/api/setup/models/moonshot", headers=H, json={"key": OTHER})
    p = c.get("/api/setup/purposes?probe=0", headers=H).json()
    refs = {x["ref"] for x in p["choices"]["models"]}
    assert {"deepseek/deepseek-flash", "moonshot/kimi-k3", "moonshot/kimi-k2.6"} <= refs and p["auto"] is None
    rec = {x["id"]: x["recommended"] for x in p["purposes"]}
    assert rec["chat"]["why"] and rec["chat"]["cost"] and rec["background"]["value"] in refs
    r = c.put("/api/setup/purposes", headers=H, json={"chat": "moonshot/kimi-k3", "background": "deepseek/deepseek-flash", "outsider": "deepseek/deepseek-flash"})
    assert r.status_code == 200, r.text
    eng = env.conf()["engine"]
    assert eng["model"] == "moonshot/kimi-k3"
    assert eng["use"] == {"card": "deepseek/deepseek-flash", "doorman": "deepseek/deepseek-flash", "json": "deepseek/deepseek-flash",
                          "background": "deepseek/deepseek-flash"}
    assert r.json()["backup"]
    bad = c.put("/api/setup/purposes", headers=H, json={"chat": "together/some-model"})
    assert bad.status_code == 400 and bad.json()["field"] == "chat"
    assert c.put("/api/setup/purposes", headers=H, json={}).status_code == 400
    r = c.put("/api/setup/purposes", headers=H, json={"background": None, "outsider": "default"})
    use = env.conf()["engine"]["use"]
    assert "background" not in use and use["card"] is True and use["doorman"] is True
    assert use["json"] is True  # 一次性 JSON 跟着后台回到主对话的模型，不留在原来后台的那个
    r = c.put("/api/setup/purposes", headers=H, json={"outsider": None})
    assert "card" not in env.conf()["engine"]["use"]


def test_other_peoples_turns_never_get_a_plan(env, monkeypatch):
    from engine import models
    monkeypatch.setattr(models, "subscription_models", lambda: ["claude-code/sonnet"])
    monkeypatch.setattr(models, "subscription_ready", lambda ref: models.subscription(ref) == "claude-code")
    c = env.client
    p = c.get("/api/setup/purposes?probe=0", headers=H).json()
    assert p["auto"] == {"provider": "claude-code", "chat": "claude-code/sonnet", "outsider": None}
    r = c.put("/api/setup/purposes", headers=H, json={"outsider": "claude-code/sonnet"})
    assert r.status_code == 400 and r.json()["field"] == "outsider"
    assert c.put("/api/setup/purposes", headers=H, json={"doorman": "claude-code/sonnet"}).json()["field"] == "doorman"
    assert c.put("/api/setup/purposes", headers=H, json={"chat": "claude-code/sonnet"}).status_code == 200
    assert env.conf()["engine"]["model"] == "claude-code/sonnet"
    o = next(x for x in p["purposes"] if x["id"] == "outsider")  # 只连了套餐：外人回合的两件都没有可推荐的
    assert o["recommended"] is None and o["parts"]["card"]["recommended"] is None and o["parts"]["doorman"]["recommended"] is None


def test_card_agent_and_doorman_get_their_own_eval_based_picks(env):
    """外人回合的两件分开推荐（评测结果，只推荐连上的）：名片 Agent 先 anthropic/claude-opus-5-5，没连 Anthropic 用 kimi-k3；
    Doorman 用 kimi-k3。评测推荐的都没连上就照外人回合的挑法。推荐的总选得上；测过、列出来的型号里没有它就不推荐它。"""
    from engine import setup_api
    c = env.client

    def picks():
        p = c.get("/api/setup/purposes?probe=0", headers=H).json()
        o = next(x for x in p["purposes"] if x["id"] == "outsider")
        return o["parts"]["card"]["recommended"], o["parts"]["doorman"]["recommended"], o["recommended"], {x["ref"] for x in p["choices"]["models"]}
    c.put("/api/setup/models/deepseek", headers=H, json={"key": KEY})  # 评测推荐的一家都没连：照外人回合的挑法（便宜、快）
    card, door, row, _ = picks()
    assert card["value"] == door["value"] == "deepseek/deepseek-flash" and row == card
    assert "answers friends for you" in card["why"] and "One check per card-agent reply" in door["cost"]
    c.put("/api/setup/models/moonshot", headers=H, json={"key": OTHER})  # 连上 Kimi：两件都推荐 kimi-k3
    card, door, row, _ = picks()
    assert card["value"] == door["value"] == "moonshot/kimi-k3" and "Claude Opus 5.5" in card["why"] and "$3 in / $15 out" in card["cost"]
    assert "23/23" in door["why"] and "3/3" in door["why"] and "6.9 s" in door["why"] and "0.25" in door["cost"]
    setup_api.remember_models("moonshot", ["kimi-k2.6"], setup_api.url_now("moonshot"))  # 测过、列出来的里没有 k3：不推荐它
    card, door, _, _ = picks()
    assert card["value"] == door["value"] == "deepseek/deepseek-flash" and "answers friends for you" in card["why"]
    setup_api.remember_models("moonshot", ["kimi-k3", "kimi-k2.6"], setup_api.url_now("moonshot"))
    c.put("/api/setup/models/anthropic", headers=H, json={"key": KEY})  # 再连上 Anthropic：名片 Agent 换成按量的 Opus 5.5，Doorman 还是 Kimi
    card, door, row, refs = picks()
    assert card["value"] == row["value"] == "anthropic/claude-opus-5-5" and "1–2" in card["cost"] and door["value"] == "moonshot/kimi-k3"
    assert "anthropic/claude-opus-5-5" in refs  # 还没列过 Anthropic 的模型：推荐的照样在能选的里面
    url = setup_api.url_now("anthropic")
    setup_api.remember_models("anthropic", ["claude-haiku-5-5", "claude-sonnet-5-5"], url)  # 列出来的里没有 Opus 5.5：换下一个
    card, door, _, refs = picks()
    assert card["value"] == "moonshot/kimi-k3" and "anthropic/claude-opus-5-5" not in refs
    setup_api.remember_models("anthropic", ["claude-opus-5-5", "claude-haiku-5-5"], url)
    card, door, _, _ = picks()
    r = c.put("/api/setup/purposes", headers=H, json={"card": card["value"], "doorman": door["value"]})
    assert r.status_code == 200, r.text
    assert {k: env.conf()["engine"]["use"][k] for k in ("card", "doorman")} == {"card": "anthropic/claude-opus-5-5", "doorman": "moonshot/kimi-k3"}


def test_the_background_choice_reaches_jobs_heartbeats_and_tasks(env):
    from engine import heartbeat, jobs, models, tasks
    env.cfg(engine={"model": "deepseek/deepseek-flash", "use": {"background": "moonshot/kimi-k2.6"}})
    assert models.background_model() == "moonshot/kimi-k2.6"
    assert jobs.model_for("main", None) == "moonshot/kimi-k2.6" and jobs.model_for("main", "deepseek/deepseek-v4-pro") == "deepseek/deepseek-v4-pro"
    assert tasks.model_for("main") == "moonshot/kimi-k2.6"
    assert heartbeat.model_of("main", {"model": None}) == "moonshot/kimi-k2.6"
    env.cfg(engine={"model": "deepseek/deepseek-flash", "use": {"background": True}})
    assert models.background_model() == "deepseek/deepseek-flash"
    env.cfg(engine={"model": "deepseek/deepseek-flash", "use": {"background": "nope"}})
    assert models.background_model() is None  # 写错了 = 照旧跟对话


def test_writing_is_a_purpose_and_a_gemini_key_brings_argon_as_the_recommendation(env):
    from engine import models
    c = env.client
    c.put("/api/setup/models/deepseek", headers=H, json={"key": KEY})  # 只有它一家：主对话自动用它
    p = c.get("/api/setup/purposes?probe=0", headers=H).json()
    assert [x["id"] for x in p["purposes"]] == ["chat", "background", "writing", "outsider", "embeddings"]
    w = next(x for x in p["purposes"] if x["id"] == "writing")
    assert w["applies"] is True and w["accepts"] == {"plans": True} and w["current"] is None and w["follows"] == "chat"
    assert w["effective"] == w["recommended"]["value"] == "deepseek/deepseek-flash"  # 没有哪家专门推荐写作的：照主对话挑
    assert "best writer" in w["recommended"]["why"] and "output price" in w["recommended"]["cost"]
    assert models.route("writing") is None  # 没写 = 跟这一轮自己的模型
    # 连上 Gemini：推荐 Gemini 4 Argon，理由和代价是它自己的；别的用途的推荐不变
    c.put("/api/setup/models/google", headers=H, json={"key": OTHER})
    p = c.get("/api/setup/purposes?probe=0", headers=H).json()
    rec = {x["id"]: x["recommended"] for x in p["purposes"]}
    assert rec["writing"]["value"] == "google/gemini-4-argon" and "1M-token context" in rec["writing"]["why"]
    assert "$2 in / $10 out" in rec["writing"]["cost"] and "google/gemini-4-argon" in {x["ref"] for x in p["choices"]["models"]}
    assert rec["chat"]["value"] != "google/gemini-4-argon" and rec["background"]["value"] != "google/gemini-4-argon"
    r = c.put("/api/setup/purposes", headers=H, json={"writing": "google/gemini-4-argon"})
    assert r.status_code == 200, r.text
    assert env.conf()["engine"]["use"]["writing"] == "google/gemini-4-argon" and r.json()["backup"]
    w = next(x for x in r.json()["purposes"] if x["id"] == "writing")
    assert w["current"] == w["effective"] == "google/gemini-4-argon" and w["follows"] is None
    assert models.route("writing") == "google/gemini-4-argon" and models.purpose_chain("writing", "google/gemini-4-argon") is None
    bad = c.put("/api/setup/purposes", headers=H, json={"writing": "together/some-model"})  # 只列连上的
    assert bad.status_code == 400 and bad.json()["field"] == "writing"
    assert c.delete("/api/setup/models/google", headers=H).json()["affected"] == ["writing"]  # 还指着它：app 据此提醒换一个
    c.put("/api/setup/purposes", headers=H, json={"writing": "default"})
    assert "writing" not in env.conf()["engine"]["use"] and models.route("writing") is None
    assert KEY not in everything(env) and OTHER not in everything(env)


def test_listed_gemini_models_still_bring_argon_and_its_reasons(env):
    """测一下列出来的型号（Google 的写法带 models/ 前缀的规范成不带的）：列表里有 Argon 才推荐它，没有就照主对话挑。"""
    from engine import setup_api
    secretstore.put("GEMINI_API_KEY", KEY)
    for listed, want in ((["gemini-3.5-flash-lite", "gemini-4-argon"], "google/gemini-4-argon"),
                         (["models/gemini-3.5-flash-lite", "models/gemini-4-argon"], "google/gemini-4-argon"),
                         (["gemini-3.5-flash-lite"], "google/gemini-3.5-flash-lite")):
        setup_api.remember_models("google", listed, setup_api.url_now("google"))
        rec = next(x for x in env.client.get("/api/setup/purposes?probe=0", headers=H).json()["purposes"] if x["id"] == "writing")["recommended"]
        assert rec["value"] == want, listed
        assert ("Google's flagship" in rec["why"]) == ("argon" in want)


def test_gemini_model_names_lose_the_models_prefix(env, tmp_path, monkeypatch):
    """Gemini 列模型回的是 models/<型号>：列的时候去掉前缀，选项里不再有 google/models/…；以前列下来、配置里写着、用量里记着的
    带前缀的也认，规范成不带的；调模型时发的也是不带前缀的。"""
    import asyncio

    import engine
    from engine import models, store, usage
    from engine.session import MemorySession, user
    listing = [{"slug": "models/gemini-2.5-flash"}, {"slug": "models/gemini-4-argon"}, {"slug": "models/gemini-embedding-001"}]
    with FakeModelServer([], key=KEY, catalog=listing) as fm:
        env.cfg(engine={"providers": {"google": {"url": fm.openai_url}}})
        c = env.client
        r = c.post("/api/setup/models/google/test", headers=H, json={"key": KEY}).json()
        assert r["ok"] and r["models"] == ["gemini-2.5-flash", "gemini-4-argon", "gemini-embedding-001"]
        c.put("/api/setup/models/google", headers=H, json={"key": KEY})
        refs = [x["ref"] for x in c.get("/api/setup/purposes?probe=0", headers=H).json()["choices"]["models"]]
        assert refs[:3] == ["google/gemini-2.5-flash", "google/gemini-4-argon", "google/gemini-embedding-001"]
        assert not any("/models/" in x for x in refs)
        # 以前列下来的（升级前存的缓存）带着前缀：读的时候规范成不带的
        cache = tmp_path / "setup-models.json"
        old = json.loads(cache.read_text(encoding="utf8"))
        old["google"]["models"] = ["models/gemini-2.5-flash", "gemini-2.5-flash", "models/gemini-4-argon"]
        cache.write_text(json.dumps(old), encoding="utf8")
        refs = [x["ref"] for x in c.get("/api/setup/purposes?probe=0", headers=H).json()["choices"]["models"]]
        assert refs == ["google/gemini-2.5-flash", "google/gemini-4-argon"]
        assert next(x for x in c.get("/api/setup/models?probe=0", headers=H).json()["providers"] if x["id"] == "google")["models"]["count"] == 2
        # 配置里写着带前缀的（以前从选项里选的）：照样认，看到的、存进去的都是不带的
        env.cfg(engine={"providers": {"google": {"url": fm.openai_url}}, "model": "google/models/gemini-2.5-flash",
                        "use": {"writing": "google/models/gemini-4-argon", "card": "google/models/gemini-2.5-flash"}})
        p = c.get("/api/setup/purposes?probe=0", headers=H).json()
        now = {x["id"]: x for x in p["purposes"]}
        assert now["chat"]["current"] == "google/gemini-2.5-flash" and now["writing"]["current"] == now["writing"]["effective"] == "google/gemini-4-argon"
        card = now["outsider"]["parts"]["card"]
        assert card["value"] == card["effective"] == "google/gemini-2.5-flash"
        assert models.default_model() == "google/gemini-2.5-flash" and models.route("writing") == "google/gemini-4-argon"
        assert c.put("/api/setup/purposes", headers=H, json={"chat": "google/models/gemini-4-argon"}).status_code == 200
        assert env.conf()["engine"]["model"] == "google/gemini-4-argon"
        assert c.delete("/api/setup/models/google", headers=H).json()["affected"] == ["chat", "writing", "card"]
        # 调模型：发出去的是不带前缀的型号名；钱照不带前缀的那个价算
        secretstore.put("GEMINI_API_KEY", KEY)
        assert models.parse_ref("google/models/gemini-2.5-flash") == ("google", "gemini-2.5-flash")
        assert models.parse_ref("openrouter/models/x") == ("openrouter", "models/x")  # 只有目录里写了 list_prefix 的那家才去掉
        res = asyncio.run(engine.run_turn(engine.Spec(kind="owner", model="google/models/gemini-4-argon", conversation="t-gemini-name"),
                                          MemorySession(), user("hi")))
        assert res.status == "done" and fm.of("openai")[-1]["model"] == "gemini-4-argon"
        m = 1_000_000
        assert models.cost("google/models/gemini-4-argon", {"in": m}) == models.cost("google/gemini-4-argon", {"in": m}) is not None
    # 用量：老记录里带前缀的和不带的记在同一个型号名下
    monkeypatch.setattr(config.settings, "db", tmp_path / "usage-names.db")
    with store.tx() as conn:
        for i, ref in enumerate(("google/models/gemini-4-argon", "google/gemini-4-argon")):
            conn.execute("INSERT INTO engine_turns(id, conversation, kind, model, origin, status, usage, started_at) VALUES(?,?,?,?,?,?,?,?)",
                         (f"g{i}", "main", "owner", ref, "user", "done", json.dumps({"in": 1000, "out": 100, "cost": None}), store.now_iso()))
    rows = usage.summary(1)["byModel"]
    assert [(x["model"], x["turns"], x["billing"]) for x in rows] == [("google/gemini-4-argon", 2, "metered")]


def test_writing_may_use_a_plan_only_where_may_spend_allows(env, monkeypatch):
    from engine import chatgpt_plan, models
    monkeypatch.setattr(models, "subscription_models", lambda: ["claude-code/sonnet", "chatgpt/gpt-test"])
    monkeypatch.setattr(models, "subscription_ready", lambda ref: models.subscription(ref) is not None)
    c = env.client
    c.put("/api/setup/models/deepseek", headers=H, json={"key": KEY})
    c.put("/api/setup/purposes", headers=H, json={"chat": "deepseek/deepseek-flash"})  # 连着三家（两个套餐）：不自动选，自己选主对话
    assert c.put("/api/setup/purposes", headers=H, json={"writing": "claude-code/sonnet"}).status_code == 200  # 和主对话一样能选套餐
    assert env.conf()["engine"]["use"]["writing"] == "claude-code/sonnet"
    assert models.route("writing", kind="owner", origin="user") == models.route("writing", kind="owner", origin="auto") == "claude-code/sonnet"
    for kind, origin in (("owner", "api"), ("owner", "mcp"), ("cyber", "user"), ("outsider", "card")):  # 不能花套餐的：换成 outside() 挑的
        assert models.route("writing", kind=kind, origin=origin) == "deepseek/deepseek-flash", (kind, origin)
    assert models.route("writing") == "deepseek/deepseek-flash"  # 不报这一轮是谁 = 当外人回合
    c.put("/api/setup/purposes", headers=H, json={"writing": "chatgpt/gpt-test"})
    assert models.route("writing", kind="owner", origin="user") == "chatgpt/gpt-test"
    assert models.route("writing", kind="owner", origin="user", via="mcp") == "deepseek/deepseek-flash"  # 经 /mcp 引出来的：照 may_spend 取更严的
    monkeypatch.setattr(chatgpt_plan, "background_allowed", lambda: False)
    assert models.route("writing", kind="owner", origin="auto") == "deepseek/deepseek-flash"  # ChatGPT 套餐：后台用要用户同意过
    monkeypatch.setattr(chatgpt_plan, "background_allowed", lambda: True)
    assert models.route("writing", kind="owner", origin="auto") == "chatgpt/gpt-test"
    w = next(x for x in c.get("/api/setup/purposes?probe=0", headers=H).json()["purposes"] if x["id"] == "writing")
    assert w["current"] == w["effective"] == "chatgpt/gpt-test"
    # true = 默认模型：默认是套餐的照样问 may_spend；card 这些外人回合照旧不花套餐
    env.cfg(engine={"model": "claude-code/sonnet", "models": ["deepseek/deepseek-flash"], "use": {"card": True, "writing": True}})
    assert models.route("card") == "deepseek/deepseek-flash" and models.route("writing", kind="owner", origin="user") == "claude-code/sonnet"
    assert models.route("writing", kind="cyber", origin="user") == "deepseek/deepseek-flash"


def test_an_old_openclaw_instance_is_reported_as_is_and_never_changed(env, tmp_path, monkeypatch):
    oc = tmp_path / "openclaw.json"
    oc.write_text(json.dumps({"agents": {"defaults": {"model": "anthropic/claude-sonnet-5-5"}}}), encoding="utf8")
    monkeypatch.setattr(config.settings, "openclaw_json", oc)
    ef = tmp_path / "oc.env"
    ef.write_text("MOONSHOT_API_KEY=moonshot-from-env-file-12345\n", encoding="utf8")
    old = {"auth": {"tokens": TOKENS}, "env_file": str(ef), "engine": {"use": {"card": "moonshot/kimi-k3"}}, "timezone": "UTC"}
    config.CONFIG_PATH.write_text(json.dumps(old, indent=2), encoding="utf8")
    config._cache = None
    before = config.CONFIG_PATH.read_bytes()
    c = env.client
    o = c.get("/api/setup", headers=H).json()
    assert o["claw"]["kind"] == "openclaw" and o["noModel"] is False and o["layers"]["models"]["status"] == "external"
    assert o["layers"]["models"]["outsider"]["card"] == "moonshot/kimi-k3"
    assert o["config"]["engineSection"] is True and o["config"]["envFile"]["exists"] is True
    m = c.get("/api/setup/models", headers=H).json()
    mo = next(x for x in m["providers"] if x["id"] == "moonshot")
    assert mo["connected"] is True and mo["key"]["source"] == "env_file" and "moonshot-from-env-file" not in json.dumps(m)
    for path in ("/api/setup/purposes", "/api/setup/search", "/api/setup/memory", "/api/setup/usage", "/api/health"):
        assert c.get(path, headers=H).status_code == 200
    assert config.CONFIG_PATH.read_bytes() == before and not env.backups() and not (tmp_path / "secrets.env").exists()
    # 主对话、后台、向量归 OpenClaw 管：这里不改；外人回合可以（用户点了保存才写，先备份）
    r = c.put("/api/setup/purposes", headers=H, json={"chat": "moonshot/kimi-k3"})
    assert r.status_code == 409 and config.CONFIG_PATH.read_bytes() == before
    r = c.put("/api/setup/purposes", headers=H, json={"writing": "moonshot/kimi-k3"})
    assert r.status_code == 409 and r.json()["field"] == "writing" and config.CONFIG_PATH.read_bytes() == before
    w = next(x for x in c.get("/api/setup/purposes", headers=H).json()["purposes"] if x["id"] == "writing")
    assert w["applies"] is False and "writing" in w["why"]
    assert c.put("/api/setup/memory", headers=H, json={"embeddings": "off"}).status_code == 409
    # 存一把新密钥：不自动改主对话（OpenClaw 的老实例）
    j = c.put("/api/setup/models/deepseek", headers=H, json={"key": KEY}).json()
    assert j["auto"] is None and config.CONFIG_PATH.read_bytes() == before
    r = c.put("/api/setup/purposes", headers=H, json={"outsider": "deepseek/deepseek-flash"})
    assert r.status_code == 200 and env.conf()["engine"]["use"] == {"card": "deepseek/deepseek-flash", "doorman": "deepseek/deepseek-flash"}
    assert len(env.backups()) == 1 and (tmp_path / env.backups()[0]).read_bytes() == before


def test_a_google_address_from_openclaw_json_becomes_the_openai_compatible_one(env, tmp_path, monkeypatch):
    """OpenClaw 的 Google 走 Gemini 自己的接口（openclaw.json 里写 …/v1beta），内核走 OpenAI 兼容的那条：照原样带过来，请求一发就 401。
    官方地址的几种写法都换成目录里的；代理、本来就是兼容的、server.json 里自己写的，照用。地址、密钥都是编的。"""
    from engine import catalog, models, setup_api
    official = catalog.CLOUD["google"]["url"]
    oc = tmp_path / "openclaw.json"
    oc.write_text(json.dumps({"models": {"providers": {
        "google": {"apiKey": "${GEMINI_API_KEY}", "baseUrl": "https://generativelanguage.googleapis.com/v1beta", "models": []},
        "g-bare": {"baseUrl": "https://generativelanguage.googleapis.com/"},
        "g-v1": {"baseUrl": "https://generativelanguage.googleapis.com/v1", "api": "google-generative-ai"},
        "g-alpha": {"baseUrl": "https://GenerativeLanguage.googleapis.com/v1alpha/?key=fake-gemini-key-0001"},
        "g-compat": {"baseUrl": "https://generativelanguage.googleapis.com/v1beta/openai/", "api": "openai-completions"},
        "g-proxy": {"baseUrl": "https://gemini-proxy.example/v1beta", "api": "openai-completions"},
    }}}), encoding="utf8")
    monkeypatch.setattr(config.settings, "openclaw_json", oc)
    g = models.provider("google")
    assert g["url"] == official and g["api"] == "openai" and g["key_env"] == "GEMINI_API_KEY"
    for pid in ("g-bare", "g-v1", "g-alpha", "g-compat"):  # 光写域名、别的版本、大写的域名带 ?key=、本来就是兼容的
        assert models.provider(pid)["url"] == official, pid
    assert models.provider("g-v1")["api"] == "openai"  # OpenClaw 自己的接口类型（google-generative-ai）这里没有：照地址走 OpenAI 兼容的
    assert models.provider("g-proxy")["url"] == "https://gemini-proxy.example/v1beta"  # 代理不替它改
    m = env.client.get("/api/setup/models", headers=H).json()
    row = next(x for x in m["providers"] if x["id"] == "google")
    assert row["url"] == row["defaultUrl"] == official and "fake-gemini-key-0001" not in json.dumps(m)  # 设置页不再像改过地址
    assert setup_api.target("google", {})[0]["url"] == official  # 「测一下」也发到兼容的那个（这里不真发）
    env.cfg(engine={"providers": {"google": {"url": "https://my-gateway.example/google/v1beta/openai"}}})
    assert models.provider("google")["url"] == "https://my-gateway.example/google/v1beta/openai"  # server.json 里自己写的照用


def test_overview_reports_each_layer_and_what_is_missing(env):
    o = env.client.get("/api/setup", headers=H).json()
    assert o["noModel"] is True and o["needsSetup"] is True and isinstance(o["firstRun"], bool)
    assert set(o["layers"]) == {"models", "search", "memory", "embeddings", "connections", "hands"}
    assert o["layers"]["models"]["missing"] == ["chat"] and o["layers"]["search"]["status"] == "optional"
    assert o["layers"]["embeddings"]["status"] == "off"
    assert [s["id"] for s in o["steps"]] == ["models", "search", "memory"]
    env.cfg(engine={"model": "deepseek/deepseek-flash"})
    o = env.client.get("/api/setup", headers=H).json()
    assert o["layers"]["models"]["missing"] == ["key:deepseek"] and o["noModel"] is True
    secretstore.put("DEEPSEEK_API_KEY", KEY)
    o = env.client.get("/api/setup", headers=H).json()
    assert o["noModel"] is False and o["layers"]["models"]["status"] == "ok" and KEY not in json.dumps(o)
    assert o["config"]["secretsFile"]["mode"] == "600" and o["config"]["secretsFile"]["tooOpen"] is False


# —— 搜索 ————————————————————————————————————————————————————————————————————————————

def test_search_keys_toggle_default_and_test(env):
    fb = fake_brave(KEY)
    try:
        env.cfg(engine={"web": {"search": {"providers": {"brave": {"url": fb.url + "/res/v1/web/search"}}}}})
        c = env.client
        s = c.get("/api/setup/search", headers=H).json()
        assert s["enabled"] is False and s["auto"] is True and {x["id"] for x in s["providers"]} >= {"brave", "tavily", "exa", "perplexity", "searxng"}
        t = c.post("/api/setup/search/brave/test", headers=H, json={"key": OTHER}).json()
        assert t["ok"] is False and t["error"]["kind"] == "auth" and OTHER not in json.dumps(t)
        good = c.post("/api/setup/search/brave/test", headers=H, json={"key": KEY}).json()
        assert good["ok"] is True and good["results"] == 1 and good["error"] is None
        assert not (env.dir / "secrets.env").exists()  # 测一下不存
        s = c.put("/api/setup/search/brave", headers=H, json={"key": KEY}).json()
        assert s["enabled"] is True and s["provider"] == "brave" and secretstore.get("BRAVE_API_KEY") == KEY  # 只有这一家：自动用它
        assert c.post("/api/setup/search/brave/test", headers=H, json={}).json()["ok"] is True
        s = c.put("/api/setup/search", headers=H, json={"enabled": False}).json()
        assert s["enabled"] is False and s["off"] is True and env.conf()["engine"]["web"]["search"]["provider"] == "off"
        s = c.put("/api/setup/search", headers=H, json={"enabled": True, "provider": "brave"}).json()
        assert s["enabled"] is True and s["chosen"] == "brave"
        assert c.put("/api/setup/search", headers=H, json={"enabled": True, "provider": "tavily"}).status_code == 409  # 没连上的不能选
        c.put("/api/setup/search/searxng", headers=H, json={"url": "http://127.0.0.1:9/"})
        assert env.conf()["engine"]["web"]["search"]["providers"]["searxng"] == {"url": "http://127.0.0.1:9"}
        s = c.delete("/api/setup/search/brave", headers=H).json()
        assert s["removed"] is True and not secretstore.get("BRAVE_API_KEY")
        assert KEY not in everything(env)
    finally:
        fb.stop()


# —— 记忆、向量 ——————————————————————————————————————————————————————————————————————

def test_memory_first_choice_sqlite_then_switching_with_data_needs_migration(env, mousse_tree, monkeypatch, tmp_path):
    import sources
    import treelib
    monkeypatch.setattr(sources, "memory_tree", None)
    monkeypatch.setitem(sources.AVAILABLE, "tree", False)
    c = env.client
    m = c.get("/api/setup/memory", headers=H).json()
    assert m["tree"]["initialized"] is False and m["tree"]["canChoose"] is True
    r = c.put("/api/setup/memory", headers=H, json={"storage": "sqlite"})
    assert r.status_code == 200, r.text
    t = r.json()["tree"]
    assert t["initialized"] is True and t["storage"] == "sqlite" and t["memories"] == 0 and t["selected"] == "package"
    assert sources.memory_tree is treelib and sources.AVAILABLE["tree"] is True  # 不用重启就接上了
    vault = tmp_path / "vault"
    vault.mkdir()
    r = c.put("/api/setup/memory", headers=H, json={"storage": "markdown", "path": str(vault / "Memory")})  # 还没有记忆：能换
    assert r.status_code == 200 and r.json()["tree"]["storage"] == "markdown" and (vault / "Memory").is_dir()
    treelib.remember("Likes long walks in the rain", source="owner")
    r = c.put("/api/setup/memory", headers=H, json={"storage": "sqlite"})
    assert r.status_code == 409 and r.json()["migrate"] is True and r.json()["storage"] == "markdown"
    assert c.put("/api/setup/memory", headers=H, json={"storage": "markdown", "path": str(vault / "Memory")}).status_code == 200  # 一样的：不动
    assert c.put("/api/setup/memory", headers=H, json={"storage": "markdown", "path": str(Path.home())}).status_code == 400
    assert c.put("/api/setup/memory", headers=H, json={"storage": "markdown", "path": "relative/dir"}).status_code == 400
    assert c.put("/api/setup/memory", headers=H, json={"storage": "cloud"}).status_code == 400


def test_the_authors_own_tree_script_is_left_alone(env, monkeypatch):
    import sources
    import types
    monkeypatch.setattr(sources, "memory_tree", types.SimpleNamespace(__file__="/somewhere/memory_tree.py"))
    m = env.client.get("/api/setup/memory", headers=H).json()
    assert m["tree"]["selected"] == "script" and m["tree"]["canChoose"] is False
    assert env.client.put("/api/setup/memory", headers=H, json={"storage": "sqlite"}).status_code == 409


def test_embeddings_choice_test_and_status(env, monkeypatch):
    from engine import catalog
    with FakeEmbedServer(key=KEY) as fe:
        monkeypatch.setitem(catalog.EMBEDDINGS["openai"], "url", fe.openai_url)
        c = env.client
        assert c.get("/api/setup/purposes?probe=0", headers=H).json()["choices"]["embeddings"] == []
        c.put("/api/setup/models/openai", headers=H, json={"key": KEY})
        p = c.get("/api/setup/purposes?probe=0", headers=H).json()
        assert [x["id"] for x in p["choices"]["embeddings"]] == ["openai"]
        assert next(x for x in p["purposes"] if x["id"] == "embeddings")["recommended"]["value"] == {"id": "openai", "model": "text-embedding-3-small"}
        t = c.post("/api/setup/embeddings/test", headers=H, json={"id": "openai"}).json()
        assert t["ok"] is True and t["dims"] == 64
        assert c.put("/api/setup/purposes", headers=H, json={"embeddings": {"id": "gemini"}}).status_code == 400  # 没连上
        r = c.put("/api/setup/memory", headers=H, json={"embeddings": {"id": "openai"}})
        assert r.status_code == 200, r.text
        assert env.conf()["engine"]["recall"]["embeddings"] == {"backend": "openai", "model": "text-embedding-3-small", "key_env": "OPENAI_API_KEY",
                                                                 "url": fe.openai_url}
        m = c.get("/api/setup/memory", headers=H).json()
        assert m["current"]["id"] == "openai" and m["embeddings"]["enabled"] is True and m["embeddings"]["key"] is True
        c.put("/api/setup/purposes", headers=H, json={"embeddings": "off"})
        assert "recall" not in env.conf()["engine"] and c.get("/api/setup/memory", headers=H).json()["current"] is None
        assert KEY not in everything(env)


# —— 用量 ————————————————————————————————————————————————————————————————————————————

def test_usage_sums_tokens_and_money_by_day_model_and_purpose_and_never_bills_plans(env, tmp_path, monkeypatch):
    from engine import store, usage
    monkeypatch.setattr(config.settings, "db", tmp_path / "usage.db")
    now = datetime.now(config.settings.tz)
    rows = [  # (几天前, kind, origin, model, models, usage)
        (0, "owner", "user", "deepseek/deepseek-flash", None, {"in": 1000, "out": 200, "cache_read": 50, "cache_write": 0, "cost": 0.0005}),
        (0, "owner", "auto", "moonshot/kimi-k3", None, {"in": 500, "out": 100, "cost": 0.003}),
        (2, "outsider", "card", "deepseek/deepseek-flash", None, {"in": 100, "out": 10, "cost": 0.00004}),
        (2, "outsider", "json", "deepseek/deepseek-flash", None, {"in": 10, "out": 1, "cost": 0.00001}),
        (1, "owner", "user", "claude-code/sonnet", None, {"in": 50, "out": 20, "cost": None, "notional_cost_usd": 0.01, "via": "claude-code"}),
        (1, "owner", "user", "fake/m1", None, {"in": 10, "out": 1, "cost": None}),
        (1, "owner", "user", "deepseek/deepseek-flash", ["deepseek/deepseek-flash", "moonshot/kimi-k3"], {"in": 7, "out": 3, "cost": 0.0001}),
        (40, "owner", "user", "deepseek/deepseek-flash", None, {"in": 99999, "out": 99999, "cost": 9.9}),  # 30 天以前：不算
    ]
    with store.tx() as conn:
        for i, (ago, kind, origin, model, called, u) in enumerate(rows):
            at = (now - timedelta(days=ago)).isoformat(timespec="milliseconds")
            conn.execute("INSERT INTO engine_turns(id, conversation, kind, model, origin, status, usage, started_at, models) VALUES(?,?,?,?,?,?,?,?,?)",
                         (f"t{i}", "main", kind, model, origin, "done", json.dumps(u), at, json.dumps(called) if called else None))
    s = env.client.get("/api/setup/usage?days=30", headers=H).json()
    tot = s["total"]
    assert tot["turns"] == 7 and tot["in"] == 1677 and tot["out"] == 335 and tot["cacheRead"] == 50
    assert tot["cost"] == pytest.approx(0.00365) and tot["planTurns"] == 1 and tot["unpricedTurns"] == 1 and tot["notional"] == pytest.approx(0.01)
    by_model = {m["model"]: m for m in s["byModel"]}
    assert by_model["claude-code/sonnet"]["billing"] == "plan" and by_model["claude-code/sonnet"]["cost"] == 0 and by_model["claude-code/sonnet"]["plan"] == "claude-code"
    assert by_model["fake/m1"]["billing"] == "unpriced" and by_model["moonshot/kimi-k3"]["turns"] == 2  # 备用链换过的记在真回答的那个名下
    by_purpose = {p["purpose"]: p["turns"] for p in s["byPurpose"]}
    assert by_purpose == {"chat": 4, "background": 2, "outsider": 1}  # 一次性 JSON（llmjson）算后台的活
    assert [d["day"] for d in s["byDay"]] == sorted(d["day"] for d in s["byDay"]) and len(s["byDay"]) == 3
    assert s["notCounted"] == [] and s["embeddings"]["total"]["requests"] == 0 and s["embeddings"]["recordedSince"] is None
    assert usage.purpose_of("cyber", None) == "cyber" and usage.purpose_of("owner", "api") == "api" and usage.purpose_of("claw", "user") == "claw"
    assert env.client.get("/api/engine/usage?days=1", headers=H).json()["total"]["turns"] == 2  # 只算今天


def test_usage_prices_each_turn_as_of_its_own_day(env, tmp_path, monkeypatch):
    """当时没算钱、记了 token 的（价目那时还没有这个型号）按这一轮开始那天的价补上：Argon 11-30 的按 $2 / $10，12-01 起的按 $4 / $20
    （换价按 UTC 的日子：东八区 12-01 早上 7 点还是旧价）；算过钱的照记下的；没记 token 的（OpenClaw 的回合）照旧「价目里没有」。"""
    from engine import store, usage
    monkeypatch.setattr(config.settings, "db", tmp_path / "usage-days.db")
    m = 1_000_000
    rows = [("2026-11-30T12:00:00+00:00", "owner", "user", {"in": m, "out": m, "cost": None}),
            ("2026-12-01T07:00:00+08:00", "owner", "user", {"in": m, "out": m, "cost": None}),   # = UTC 11-30 23:00
            ("2026-12-01T00:30:00+00:00", "owner", "auto", {"in": m, "out": m, "cost": None}),
            ("2026-12-01T09:00:00+00:00", "owner", "user", {"in": m, "out": m, "cost": 1.25}),   # 当时算过的：照记下的
            ("2026-12-01T10:00:00+00:00", "claw", "user", {})]                                  # OpenClaw 记下的：没有 token
    with store.tx() as conn:
        for i, (at, kind, origin, u) in enumerate(rows):
            conn.execute("INSERT INTO engine_turns(id, conversation, kind, model, origin, status, usage, started_at) VALUES(?,?,?,?,?,?,?,?)",
                         (f"d{i}", "main", kind, "google/gemini-4-argon", origin, "done", json.dumps(u), at))
    s = usage.summary(30, now=datetime(2026, 12, 2, 12, tzinfo=timezone.utc))
    days = {d["day"]: d for d in s["byDay"]}
    assert days["2026-11-30"]["turns"] == 2 and days["2026-11-30"]["cost"] == pytest.approx(2 * (2.00 + 10.00))
    assert days["2026-12-01"]["turns"] == 3 and days["2026-12-01"]["cost"] == pytest.approx(4.00 + 20.00 + 1.25)
    assert days["2026-12-01"]["unpricedTurns"] == 1 and s["total"]["cost"] == pytest.approx(49.25)
    argon = s["byModel"][0]
    assert argon["model"] == "google/gemini-4-argon" and argon["billing"] == "mixed" and argon["turns"] == 5
    assert usage.priced("google/gemini-4-argon", {"in": m, "cost": None}, "not a time") == {"in": m, "cost": None}


def test_embedding_usage_has_its_own_column_and_old_databases_get_the_table(env, tmp_path, monkeypatch, capsys):
    """向量的用量单独一栏：按天、型号、种类（建索引 / 搜的那一句 / 设置页测一下）。记在谁名下按地址认目录里的那一家（地区的、server.json
    改过的地址都认），价目里有的才算钱。以前的库（内核的表都在，没有这一张）第一次写的时候建；看用量只读，不建表。"""
    import setup_ctl
    from engine import catalog, embeddings, store, usage
    usage.flush()
    db = tmp_path / "old.db"
    monkeypatch.setattr(config.settings, "db", db)
    with store.tx() as conn:
        conn.execute("INSERT INTO engine_turns(id, conversation, kind, model, origin, status, usage, started_at) VALUES(?,?,?,?,?,?,?,?)",
                     ("t1", "main", "owner", "deepseek/deepseek-flash", "user", "done", json.dumps({"in": 10, "out": 2, "cost": 0.0001}), store.now_iso()))
    s = env.client.get("/api/setup/usage", headers=H).json()
    assert s["total"]["turns"] == 1 and s["notCounted"] == [] and s["embeddings"]["total"]["requests"] == 0
    has_table = "SELECT count(*) FROM sqlite_master WHERE name='engine_embed_usage'"
    with contextlib.closing(sqlite3.connect(db)) as c:
        assert c.execute(has_table).fetchone()[0] == 0  # 看用量不建表
    env.cfg(engine={"providers": {"ollama": {"url": "http://127.0.0.1:11500/v1"}}})
    spec = embeddings.spec_from
    m = 1_000_000
    usage.embedded(spec({"backend": "openai", "key_env": ""}), ["hello world"] * 3, m, kind="index")  # OpenAI 自己的地址：价目里有
    usage.embedded(spec({"backend": "gemini", "key_env": ""}), ["你好"], None, kind="query")  # Google 自己的接口、没回用量：按字数估
    usage.embedded(spec({"backend": "openai", "url": "https://open.bigmodel.cn/api/paas/v4", "model": "embedding-3"}), ["x"], 5, kind="index")
    usage.embedded(spec({"backend": "openai", "url": "http://127.0.0.1:11500/v1", "model": "nomic-embed-text", "key_env": ""}), ["y"], 7,
                   kind="index")
    usage.embedded(spec({"backend": "openai", "key_env": ""}), ["hello again"], m, kind="index")  # 同一天同一种：加进同一份
    assert usage.flush() == 4
    with contextlib.closing(sqlite3.connect(db)) as c:
        assert c.execute(has_table).fetchone()[0] == 1 and c.execute("SELECT count(*) FROM engine_embed_usage").fetchone()[0] == 4
    secretstore.put("OPENAI_API_KEY", KEY)
    with FakeEmbedServer(key=KEY) as fe:  # 设置页的「测一下」记成 test
        monkeypatch.setitem(catalog.EMBEDDINGS["openai"], "url", fe.openai_url)
        assert env.client.post("/api/setup/embeddings/test", headers=H, json={"id": "openai"}).json()["ok"] is True
    e = env.client.get("/api/setup/usage", headers=H).json()["embeddings"]
    by = {x["model"]: x for x in e["byModel"]}
    assert set(by) == {"openai/text-embedding-3-small", "google/gemini-embedding-001", "zai/embedding-3", "ollama/nomic-embed-text",
                       "custom/text-embedding-3-small"}
    oa = by["openai/text-embedding-3-small"]
    assert oa["provider"] == "openai" and oa["requests"] == 2 and oa["tokens"] == 2 * m and oa["billing"] == "metered" and oa["cost"] == pytest.approx(0.04)
    g = by["google/gemini-embedding-001"]
    assert g["provider"] == "google" and g["tokens"] == g["estimatedTokens"] == embeddings.tokens("你好") and g["billing"] == "unpriced"
    assert by["zai/embedding-3"]["provider"] == "zai" and by["ollama/nomic-embed-text"]["provider"] == "ollama"  # 国内站的地址、改过的端口
    assert by["custom/text-embedding-3-small"]["provider"] is None  # 测的时候指到了别的地址：认不出是哪一家
    assert {k["kind"]: k["requests"] for k in e["byKind"]} == {"index": 4, "query": 1, "test": 1}
    assert e["total"]["cost"] == pytest.approx(0.04) and e["total"]["estimatedTokens"] == embeddings.tokens("你好")
    assert e["recordedSince"] == e["byDay"][0]["day"] == datetime.now(config.settings.tz).date().isoformat()
    assert setup_ctl.main(["usage", "7"]) == 0
    out = capsys.readouterr().out
    assert "Embeddings: 6 requests" in out and "$0.0400" in out


# —— 不回显密钥：一整套走下来 ———————————————————————————————————————————————————————————————

def test_a_whole_flow_never_shows_the_key_anywhere(env):
    seen = []
    c = env.client
    with FakeModelServer([], key=KEY) as fm:
        for method, path, body in [("put", "/api/setup/models/lab", {"url": fm.openai_url, "key": KEY}),
                                   ("post", "/api/setup/models/lab/test", {}), ("get", "/api/setup", None), ("get", "/api/setup/models", None),
                                   ("get", "/api/setup/purposes", None), ("put", "/api/setup/purposes", {"chat": "lab/fake-model"}),
                                   ("get", "/api/engine/status", None), ("get", "/api/health", None), ("delete", "/api/setup/models/lab", None)]:
            r = getattr(c, method)(path, headers=H, **({"json": body} if body is not None else {}))
            assert r.status_code == 200, (path, r.text)
            seen.append(r.text)
    text = "\n".join(seen) + everything(env)
    assert KEY not in text and KEY[-12:] not in text


# —— 命令行的三步、安装器的 builtin 这条路 ————————————————————————————————————————————————————

def test_cli_subcommands_share_the_same_rules(env, monkeypatch, capsys):
    import setup_ctl
    monkeypatch.setattr(sys, "stdin", io.StringIO(KEY + "\n"))
    assert setup_ctl.main(["add", "deepseek", "--key-stdin"]) == 0
    assert secretstore.get("DEEPSEEK_API_KEY") == KEY and env.conf()["engine"]["model"] == "deepseek/deepseek-flash"
    assert setup_ctl.main(["status"]) == 0
    assert setup_ctl.main(["use", "chat", "together/nope"]) == 1  # 没连上
    assert setup_ctl.main(["use", "background", "deepseek/deepseek-flash"]) == 0 and env.conf()["engine"]["use"]["background"] == "deepseek/deepseek-flash"
    assert setup_ctl.main(["use", "background", "default"]) == 0 and "background" not in env.conf()["engine"]["use"]  # 跟主对话
    assert setup_ctl.main(["use", "writing", "deepseek/deepseek-flash"]) == 0 and env.conf()["engine"]["use"]["writing"] == "deepseek/deepseek-flash"
    assert setup_ctl.main(["use", "writing", "default"]) == 0 and "writing" not in env.conf()["engine"]["use"]
    assert setup_ctl.main(["remove", "deepseek"]) == 0 and setup_ctl.main(["status"]) == 3
    assert setup_ctl.main(["usage", "7"]) == 0
    out = capsys.readouterr()
    assert KEY not in out.out + out.err


def test_cli_three_steps_walk_through_with_scripted_answers(env, monkeypatch, mousse_tree, capsys):
    import setup_ctl
    import sources
    monkeypatch.setattr(sources, "memory_tree", None)
    monkeypatch.setitem(sources.AVAILABLE, "tree", False)
    with FakeModelServer([], key=KEY) as fm:
        answers = iter(["custom", "lab", fm.openai_url, "",   # 1/3：自己填地址，存好；回车 = 不再加了
                        "",                                   #      主对话：回车用默认（只有这一家时已经自动选上）
                        "",                                   # 2/3：搜索回车 = 不改
                        ""])                                  # 3/3：世界树回车 = 默认的 SQLite
        monkeypatch.setattr(setup_ctl, "interactive", lambda: True)
        monkeypatch.setattr(setup_ctl, "ask", lambda prompt, default="": next(answers) or default)
        monkeypatch.setattr(setup_ctl, "secret", lambda prompt: KEY)
        assert setup_ctl.wizard() == 0
    eng = env.conf()["engine"]
    assert eng["model"] == "lab/fake-model" and eng["providers"]["lab"]["key_env"] == "LAB_API_KEY" and secretstore.get("LAB_API_KEY") == KEY
    import treelib
    assert treelib.available() and treelib.storage()["kind"] == "sqlite"
    out = capsys.readouterr().out
    assert "1/3" in out and "2/3" in out and "3/3" in out and KEY not in out


def test_cli_enter_twice_gets_out_without_a_model(env, monkeypatch, mousse_tree, capsys):
    """什么都没连、手里也没有密钥：头一下回车 = 云端，在那里跳过以后再回车 = 不再加了（10/19 以前回车一直是云端，只能 Ctrl-C）。"""
    import setup_ctl
    import sources
    monkeypatch.setattr(sources, "memory_tree", None)
    monkeypatch.setitem(sources.AVAILABLE, "tree", False)
    answers = iter([""] * 8)  # 用完了会抛 StopIteration：要是又绕回云端，测试就失败，不会卡住
    monkeypatch.setattr(setup_ctl, "interactive", lambda: True)
    monkeypatch.setattr(setup_ctl, "ask", lambda prompt, default="": next(answers) or default)
    monkeypatch.setattr(setup_ctl, "secret", lambda prompt: "")
    assert setup_ctl.wizard() == 0
    assert not (env.conf().get("engine") or {}).get("providers")
    assert "2/3" in capsys.readouterr().out


def test_cli_does_not_ask_when_a_claw_installs_for_you(env, monkeypatch, capsys):
    import setup_ctl
    monkeypatch.setenv("MOUSSE_NONINTERACTIVE", "1")
    assert setup_ctl.wizard() == 0
    assert "setup_ctl.py" in capsys.readouterr().out


def test_installer_builtin_path_needs_no_openclaw(tmp_path):
    home = tmp_path / "home"
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    # 用一个小脚本转给 sys.executable，别用软链：跑测试的要是 venv 的 python，从别的软链进去它认不出自己的 venv（pyvenv.cfg 按进来的路径找），
    # setup_ctl.py 就导不到 httpx 了；CI 装在全局的 python 里两种都行
    (venv / "bin" / "python").write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n', encoding="utf8")
    (venv / "bin" / "python").chmod(0o755)
    env_ = {k: v for k, v in os.environ.items() if not k.startswith("MOUSSE_")}
    env_.update(HOME=str(home), MOUSSE_NONINTERACTIVE="1", MOUSSE_TREE_HOME=str(home / ".mousse-tree"))
    r = subprocess.run([sys.executable, str(REPO / "packs/core/setup.py"), "--repo", str(REPO), "--venv", str(venv), "--builtin", "--no-systemd",
                        "--no-tree", "--lang", "en", "--tz", "UTC", "--name", "Mousse"], capture_output=True, text=True, env=env_, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    cfg = json.loads((home / ".openmousse/server.json").read_text(encoding="utf8"))
    assert cfg["claw"] == {"kind": "builtin"} and cfg["workspace"] == str(home / ".openmousse/workspace")
    assert (home / ".openmousse/workspace/USER.md").is_file() and not (home / ".openclaw").exists()
    assert stat.S_IMODE((home / ".openmousse/server.json").stat().st_mode) == 0o600
    assert "setup_ctl.py" in r.stdout and "No model chosen yet" in r.stdout and "No usable model yet" in r.stdout
    assert "follow the guide" not in r.stdout and "app's settings" not in r.stdout  # app 里还没有这三步的界面：只说终端里怎么选
    assert "Start the services yourself" in r.stdout and "systemctl" not in r.stdout  # --no-systemd：给自己起服务的命令
    assert "openclaw.json" not in r.stdout
    # 再跑一遍：一样的，不重复写、不问
    r2 = subprocess.run(r.args, capture_output=True, text=True, env=env_, timeout=120)
    assert r2.returncode == 0 and json.loads((home / ".openmousse/server.json").read_text(encoding="utf8"))["claw"] == {"kind": "builtin"}


def load_installer():
    """packs/core/setup.py 当模块读进来（CI 里也这么调 adopt_fresh_workspace）。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location("om_installer", REPO / "packs/core/setup.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_installer_uses_systemd_only_when_it_runs(monkeypatch, tmp_path):
    """容器、没开 systemd 的 WSL 有 systemctl 这个命令却连不上 systemd（10/19 干净机器上装：两个服务「启动失败」，也没说怎么自己起）：
    当没有 systemd；服务没交给它管的时候，重启和日志的提示都是自己起的那一套。"""
    m = load_installer()
    monkeypatch.setattr(m.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(m, "SYSTEMD_RUN", tmp_path / "missing")
    assert not m.has_systemd()
    monkeypatch.setattr(m, "SYSTEMD_RUN", tmp_path)
    assert m.has_systemd()
    monkeypatch.setattr(m, "MANAGED", False)
    assert m.restart_cmd("openmousse-server") == ('pkill -u "$(id -u)" -f "^$HOME/.openmousse/venv/bin/python[0-9.]* run.py"; '
                                                 "cd ~/.openmousse/repo/server && nohup ~/.openmousse/venv/bin/python run.py >> ~/.openmousse/logs/server.log 2>&1 &")
    # 停的那一半认得出服务，却认不出跑这一行的 shell 自己（脚本里、bash -c 里它的命令行也带着这串字）。只比对，不真的 pkill：
    # 跑测试的机器上可能正开着自己的 OpenMousse
    import re
    for name, proc in (("openmousse-server", "/home/x/.openmousse/venv/bin/python run.py"),
                       ("mousse-tree", "/home/x/.openmousse/venv/bin/python /home/x/.openmousse/venv/bin/mousse-tree serve")):
        pat = re.search(r'-f "([^"]+)"', m.stop_cmd(name)).group(1).replace("$HOME", "/home/x")
        assert re.search(pat, proc)
        assert not re.search(pat, f"bash -c {m.restart_cmd(name)}".replace("$HOME", "/home/x").replace("~", "/home/x"))
    assert m.logs_cmd("openmousse-server") == "tail -n 50 ~/.openmousse/logs/server.log"


def fake_venv(tmp_path: Path) -> Path:
    """装了 server 依赖的 python（跑测试的这个）套一层 venv 的样子，见上面 builtin 那个测试。"""
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python").write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n', encoding="utf8")
    (venv / "bin" / "python").chmod(0o755)
    return venv


def test_installer_switching_back_to_openclaw_follows_its_workspace(tmp_path):
    """先装成 builtin，后来装了 OpenClaw、重跑安装器选 openclaw：server.json 的工作区、档案、OpenClaw 的位置跟着换到 OpenClaw 那边
    （skills 和规则都装在那边，10/19 以前还留在 ~/.openmousse/workspace）；那里原来的文件不搬，说一句在哪。openclaw 命令是个替身。"""
    home, bin_ = tmp_path / "home", tmp_path / "bin"
    venv = fake_venv(tmp_path)
    bin_.mkdir()
    (bin_ / "openclaw").write_text("#!/bin/sh\nexit 0\n", encoding="utf8")  # config validate 一律过
    (bin_ / "openclaw").chmod(0o755)
    env_ = {k: v for k, v in os.environ.items() if not k.startswith("MOUSSE_")}
    env_.update(HOME=str(home), MOUSSE_NONINTERACTIVE="1", MOUSSE_TREE_HOME=str(home / ".mousse-tree"), PATH=f"{bin_}:{os.environ.get('PATH', '')}")
    base = [sys.executable, str(REPO / "packs/core/setup.py"), "--repo", str(REPO), "--venv", str(venv), "--no-systemd", "--no-tree",
            "--lang", "en", "--tz", "UTC", "--name", "Mousse"]
    r = subprocess.run([*base, "--builtin"], capture_output=True, text=True, env=env_, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    own = home / ".openmousse/workspace"
    (own / "MEMORY.md").write_text("- kept by the built-in engine\n", encoding="utf8")
    oc = home / "oc"  # 不在默认的 ~/.openclaw：位置也要跟着换
    (oc / "workspace").mkdir(parents=True)
    (oc / "workspace/USER.md").write_text("# USER.md\n\n## Basics\n", encoding="utf8")
    (oc / "openclaw.json").write_text(json.dumps({"agents": {"defaults": {"workspace": str(oc / "workspace")}},
                                                  "gateway": {"port": closed_port(), "reload": {"mode": "off"}}}), encoding="utf8")
    r = subprocess.run([*base, "--openclaw-home", str(oc)], capture_output=True, text=True, env=env_, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    cfg = json.loads((home / ".openmousse/server.json").read_text(encoding="utf8"))
    assert "claw" not in cfg and cfg["workspace"] == str(oc / "workspace") and cfg["profile"] == str(oc / "workspace/USER.md")
    assert cfg["openclaw_home"] == str(oc)
    assert (oc / "workspace/skills/journal").is_symlink() and "## OpenMousse" in (oc / "workspace/AGENTS.md").read_text(encoding="utf8")
    assert (own / "MEMORY.md").exists() and not (oc / "workspace/MEMORY.md").exists()  # 不搬
    assert f"still in {own}" in r.stdout


def test_check_sh_runs_through_with_the_builtin_engine(tmp_path):
    """check.sh 碰到 claw = builtin：10/19 以前当成别的 claw，拿空地址去请求，ValueError 一抛后面全没了。现在是「自带的内核」一段，
    服务没起来也照样跑完（服务连不上 = ✗，退出码 1）。"""
    home = tmp_path / "home"
    (home / ".openmousse").mkdir(parents=True)
    (home / ".openmousse/server.json").write_text(json.dumps({"claw": {"kind": "builtin"}, "language": "en", "timezone": "UTC",
                                                              "bind": {"host": "127.0.0.1", "port": closed_port()},
                                                              "auth": {"tokens": {"local": "tok-local"}}}), encoding="utf8")
    env_ = {**{k: v for k, v in os.environ.items() if not k.startswith("MOUSSE_")}, "HOME": str(home)}
    r = subprocess.run(["bash", str(REPO / "check.sh"), "--no-chat"], capture_output=True, text=True, env=env_, timeout=180)
    assert "Traceback" not in r.stderr, r.stderr
    assert "Built-in engine" in r.stdout and "Your claw" not in r.stdout and "setup_ctl.py status" in r.stdout
    assert r.returncode == 1 and "Server not reachable" in r.stdout


def test_install_sh_offers_builtin():
    text = (REPO / "install.sh").read_text(encoding="utf8")
    assert "builtin|mousse|openmousse|own|none) CLAW_KIND=builtin" in text and "CLAW_ARGS=(--builtin)" in text
    assert subprocess.run(["bash", "-n", str(REPO / "install.sh")]).returncode == 0
