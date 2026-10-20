"""第 7 批 16：企业云（engine/catalog.py 的 azure、bedrock）。目录写得对（problems）、进 models.PROVIDERS 的样子、核对的页面和日子；
用户填了自己的资源地址和部署名以后真的走一次（假模型服务器，真的走 HTTP），不连网、不花钱。"""
from __future__ import annotations

import asyncio
import hashlib

from engine import catalog, loop, models, provider_openai, providers
from engine.session import user
from evals.fakemodel import FakeModelServer, Reply


def test_the_entries_are_well_formed():
    assert catalog.problems() == []
    for pid in ("azure", "bedrock"):
        e = catalog.CLOUD[pid]
        assert e["checked"] == "2026-10-19" and e["sources"] and all(s.startswith("https://") for s in e["sources"])
        assert e["list"] == "none" and e["test_model"] and catalog.text(e["note"])
        assert pid in models.PROVIDERS and catalog.kind_of(pid) == "cloud"
    az = models.PROVIDERS["azure"]
    assert az["api"] == "openai" and az["url"] == "" and az["key_env"] == "AZURE_OPENAI_API_KEY"  # 没有默认地址：填你自己的资源
    assert az["max_tokens_field"] == "max_completion_tokens"
    br = catalog.CLOUD["bedrock"]
    assert models.PROVIDERS["bedrock"]["key_env"] == "AWS_BEARER_TOKEN_BEDROCK" and list(br["regions"]) == ["us-east-1", "us-west-2"]
    assert catalog.region_of(br, "https://bedrock-runtime.us-west-2.amazonaws.com/openai/v1") == "us-west-2"
    assert catalog.region_of(br, "https://bedrock-runtime.eu-central-1.amazonaws.com/openai/v1") == "custom"
    assert models.valid_ref("bedrock/openai.gpt-oss-120b-1:0") and models.valid_ref("azure/my-deployment")


def test_azure_with_your_own_resource_and_deployment(server_json, monkeypatch, request):
    fm = FakeModelServer([Reply(text="Hello from your deployment.")]).start()
    request.addfinalizer(fm.stop)
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "azure-test-key")
    monkeypatch.setattr(loop, "MODEL_RETRIES", (0, 0))
    monkeypatch.setattr(provider_openai, "RETRY_DELAYS", (0, 0))
    server_json({"engine": {"providers": {"azure": {"url": fm.openai_url}}}})  # 用户填的资源地址（这里是假服务器）
    p = models.provider("azure")
    assert p["key"] == "azure-test-key" and p["api"] == "openai" and p["url"] == fm.openai_url

    async def go():
        return [ev async for ev in providers.stream("azure/my-deployment", providers.Request(system="Be brief.", messages=[user("hi")],
                                                                                              max_tokens=64))]
    evs = asyncio.run(go())
    assert evs[-1]["type"] == "done" and evs[-1]["message"]["content"][0]["text"] == "Hello from your deployment."
    [req] = fm.of("openai")
    body = req["body"]
    assert body["model"] == "my-deployment" and body.get("max_completion_tokens") == 64 and "max_tokens" not in body
    assert req["bearer"] == hashlib.sha256(b"azure-test-key").hexdigest()[:12]  # 密钥照 OpenAI 的写法放在 Authorization: Bearer
    assert "api-version" not in req["path"]
