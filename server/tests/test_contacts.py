"""通讯录（主线第三步第 7 件，contacts.py）：管家（第一步是名片 Agent）在顶上，下面赛博、功能 Agent、好友三段；好友下面挂「X 的管家」；
搜索、筛选。名字、人和事都是编的。"""
from __future__ import annotations

import pytest

import chat
from test_cybers import H, agent, env, fake, groups_clean, make, talk  # noqa: F401 — 同一套 server.json、假模型、功能 Agent


@pytest.fixture
def people():
    """好友：先清空（库是几个测试共用的），再加两位编的（Mia 的名片上有 Agent，Pat 没有）。"""
    import social
    ts = chat.now_iso()
    with chat._lock, social.sdb() as conn:
        conn.execute("DELETE FROM friends")
        for fid, name, caps in (("f-mia", "Mia", '["agent"]'), ("f-pat", "Pat", "[]")):
            conn.execute("INSERT INTO friends(id, kid, pub, url, name, tier, status, caps, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                         (fid, f"kid-{fid}", "pub", "https://example.invalid/f", name, "friend", "active", caps, ts, ts))
    yield
    with chat._lock, social.sdb() as conn:
        conn.execute("DELETE FROM friends WHERE id IN ('f-mia', 'f-pat')")


def test_contacts_are_behind_the_cybers_switch(env):  # noqa: F811 — 导入的 fixture
    env.cfg(cybers=False)
    assert env.client.get("/api/contacts", headers=H).status_code == 404


def test_contacts_put_the_butler_first_then_cybers_agents_and_friends(env, fake, groups_clean, people):  # noqa: F811 — 导入的 fixture
    make(env, "Victor", tag="Early investor")
    kai = make(env, "Kai", tag="Code advisor", model="moonshot/kimi-k3")
    fake([{"text": "Three repos touch your sync module."}])
    talk(kai["id"], "anything new?")
    radar = agent(env, "GitHub radar", icon="code", purpose="Writes a daily report on new repos")
    r = env.client.get("/api/contacts", headers=H).json()
    assert r["butler"]["kind"] == "butler" and r["butler"]["open"] == "CardAgent" and r["butler"]["name"] == "Card agent"
    assert [s["kind"] for s in r["sections"]] == ["cyber", "agent", "friend"]
    cy, ag, fr = (s["items"] for s in r["sections"])
    assert [c["name"] for c in cy] == ["Kai", "Victor"]  # 最近聊过的在前
    assert cy[0]["modelName"] == "Kimi K3" and cy[0]["tag"] == "Code advisor" and cy[0]["line"] == "Three repos touch your sync module."
    assert [(a["id"], a["purpose"]) for a in ag] == [(radar, "Writes a daily report on new repos")]
    assert {f["name"]: f["butler"] and f["butler"]["name"] for f in fr} == {"Mia": "Mia's butler", "Pat": None}  # 名片上有 Agent 才挂管家
    assert {f["id"]: f["count"] for f in r["filters"]} == {"all": 5, "cyber": 2, "agent": 1, "friend": 2}


def test_contacts_search_and_filter(env, groups_clean, people):  # noqa: F811 — 导入的 fixture
    make(env, "Victor", tag="Early investor")
    agent(env, "Job search", icon="briefcase", purpose="Tracks applications")
    r = env.client.get("/api/contacts", params={"q": "INVESTOR"}, headers=H).json()
    assert [[i["name"] for i in s["items"]] for s in r["sections"]] == [["Victor"], [], []] and r["butler"] is None
    r = env.client.get("/api/contacts", params={"q": "mia's butler"}, headers=H).json()
    assert [i["name"] for i in r["sections"][2]["items"]] == ["Mia"]  # 搜管家也找得到那位好友
    r = env.client.get("/api/contacts", params={"filter": "agent"}, headers=H).json()
    assert [s["kind"] for s in r["sections"]] == ["agent"] and r["butler"] is None
    assert "applications" in r["sections"][0]["items"][0]["keywords"]  # app 就地筛用的
    assert env.client.get("/api/contacts", params={"filter": "robots"}, headers=H).status_code == 400
