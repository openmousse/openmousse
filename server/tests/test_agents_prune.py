"""删 Agent（write_entry(id, None)）时把 openclaw.json 里指着它的引用一起清掉，照 OpenClaw 的 pruneAgentConfig。数据都是虚构的。"""
import copy
import json

import agents
import pytest
from config import settings


@pytest.fixture
def cfg_file(tmp_path, monkeypatch):
    path = tmp_path / "openclaw.json"
    monkeypatch.setattr(settings, "openclaw_json", path)
    monkeypatch.setattr(agents, "validate_config", lambda: None)
    monkeypatch.setattr(agents, "backup_openclaw_json", lambda tag: None)

    def write(data):
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf8")

    def read():
        return json.loads(path.read_text(encoding="utf8"))
    return write, read


def three() -> dict:
    return {
        "agents": {
            "ownership": "explicit",
            "defaults": {
                "workspace": "/srv/demo/workspace",
                "heartbeat": {"agentId": " Beta ", "every": "30m"},
                "systemAgent": {"agentId": "beta"},
                "authInheritance": {"agentId": "BETA", "mode": "copy"},
                "sessionStore": {"agentId": "beta"},
                "subagents": {"allowAgents": ["alpha", "beta", "main"]},
            },
            "entries": {
                "main": {"workspace": "/srv/demo/workspace", "subagents": {"allowAgents": ["beta", "alpha"]}},
                "alpha": {"model": "demo/model-a", "subagents": {"allowAgents": ["Beta"]}},
                "beta": {"model": "demo/model-b"},
            },
        },
        "bindings": [
            {"agentId": "main", "match": {"channel": "telegram", "accountId": "*"}},
            {"agentId": "beta", "match": {"channel": "discord", "accountId": "*"}},
            {"agentId": "alpha", "match": {"channel": "slack", "accountId": "*"}},
        ],
        "talk": {"agentId": "beta", "voice": "demo-voice"},
        "broadcast": {"peer-1": ["alpha", "beta"], "peer-2": {"agents": ["beta", "main"], "mode": "all"}},
        "tools": {"agentToAgent": {"enabled": True, "allow": ["alpha", "beta"]}, "profile": "full"},
        "hooks": {
            "allowedAgentIds": ["beta", "alpha"],
            "mappings": [{"match": "x", "agentId": "beta"}, {"match": "y", "agentId": "alpha"}, {"match": "z"}],
            "token": "fake-token-for-tests",
        },
        "session": {"store": "/srv/demo/sessions.json"},
    }


def test_remove_beta_prunes_every_ref(cfg_file):
    write, read = cfg_file
    before = three()
    write(before)
    agents.write_entry("beta", None, "test")
    got = read()
    assert "beta" not in json.dumps(got).lower()
    want = copy.deepcopy(before)
    del want["agents"]["entries"]["beta"]
    d = want["agents"]["defaults"]
    del d["heartbeat"]  # 归 beta、还剩两个 agent → 整个去掉
    del d["systemAgent"], d["sessionStore"]
    d["authInheritance"] = {"mode": "copy"}
    d["subagents"]["allowAgents"] = ["alpha", "main"]
    want["agents"]["entries"]["main"]["subagents"]["allowAgents"] = ["alpha"]
    want["agents"]["entries"]["alpha"]["subagents"]["allowAgents"] = []
    want["bindings"] = [before["bindings"][0], before["bindings"][2]]
    want["talk"] = {"voice": "demo-voice"}
    want["broadcast"] = {"peer-1": ["alpha"], "peer-2": {"agents": ["main"], "mode": "all"}}
    want["tools"]["agentToAgent"]["allow"] = ["alpha"]
    want["hooks"]["allowedAgentIds"] = ["alpha"]
    want["hooks"]["mappings"] = [{"match": "y", "agentId": "alpha"}, {"match": "z"}]
    assert got == want


def test_heartbeat_owner_only_drops_agent_id_when_one_left(cfg_file):
    write, read = cfg_file
    write({"agents": {"ownership": "explicit", "defaults": {"heartbeat": {"agentId": "beta", "every": "1h"}},
                      "entries": {"main": {}, "beta": {}}},
           "bindings": [{"agentId": "beta", "match": {"channel": "discord", "accountId": "*"}}],
           "talk": {"agentId": "beta"}})
    agents.write_entry("beta", None, "test")
    assert read() == {"agents": {"defaults": {"heartbeat": {"every": "1h"}}, "entries": {"main": {}}}}


def test_collapse_to_one_drops_ownership_as_before(cfg_file):
    write, read = cfg_file
    write({"agents": {"ownership": "explicit", "entries": {"main": {"workspace": "/srv/demo/ws"}, "alpha": {}}},
           "gateway": {"port": 12345}})
    agents.write_entry("alpha", None, "test")
    assert read() == {"agents": {"entries": {"main": {"workspace": "/srv/demo/ws"}}}, "gateway": {"port": 12345}}
