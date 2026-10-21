"""Zen 想法「拿去讨论」（主线第三步第二段，ideatalk.py）：和一位赛博一对一（原文只当前情）、新建群聊（小结写回、结束标为已想完）、放进播客、
交给功能 Agent、发给好友前先理成要点；想法上记下在哪讨论（一样东西只有一个家）。不连真模型：模型是假的（按内容回放）；事都是编的。"""
from __future__ import annotations

import asyncio
import json
import time

import pytest

import chat
import groupchat
import ideatalk
import podmaterials
import social
import think
from test_cybers import H, agent, env, groups_clean, make  # noqa: F401 — 赛博那边的 fixture 和建人的小函数
from test_groupchat import ByContent, said, settle, summ


def run(coro):
    return asyncio.run(coro)


async def done(thread: str, timeout: float = 5.0) -> None:
    t0 = time.monotonic()
    await asyncio.sleep(0)
    while (r := chat.RUNS.get(thread)) is not None and not r.done:
        if time.monotonic() - t0 > timeout:
            raise AssertionError("timed out waiting for the reply")
        await asyncio.sleep(0.01)


@pytest.fixture
def ienv(env, groups_clean):  # noqa: F811 — env、groups_clean 是导入的 fixture
    env.cfg(groupchat=True, user_name="Alex")
    with chat._lock, groupchat.gdb() as conn:
        conn.execute("DELETE FROM groupchats")
    with chat._lock, social.sdb() as conn:  # 别的测试留下的好友：「发给好友」那一项从没有好友算起
        conn.execute("DELETE FROM friends")
    return env


@pytest.fixture
def scripted(monkeypatch):
    from engine import providers

    def make_(rules):
        f = ByContent(rules)
        monkeypatch.setattr(providers, "stream", f.stream)
        return f
    return make_


def new_idea(text: str = "Maybe I should run a half marathon in spring, but only if training fits around work.") -> str:
    return think.create_fragment(kind="text", text=text)["id"]


def test_the_sheet_lists_where_an_idea_can_go(ienv):
    v = make(ienv, "Victor")["id"]
    s = make(ienv, "Socrates")["id"]
    with chat._lock, chat.db() as conn:  # 和 Socrates 聊过：排在前面
        conn.execute("INSERT INTO messages(thread, role, text, ts, status, origin) VALUES(?,?,?,?,?,?)", (s, "user", "hi", chat.now_iso(), "ok", "user"))
    r = agent(ienv, "Running", icon="target")
    fid = new_idea()
    out = ienv.client.get(f"/api/think/ideas/{fid}/discuss", headers=H).json()
    assert [c["id"] for c in out["cybers"]] == [s, v] and [a["id"] for a in out["agents"]] == [r]
    assert out["discussion"] is None and out["can"] == {"cyber": True, "group": True, "podcast": True, "agent": True, "friend": False}
    assert ienv.client.get("/api/think/ideas/th-00000000/discuss", headers=H).status_code == 404
    assert "think.discuss" in ienv.client.get("/api/health", headers=H).json()["server"]["features"]


def test_off_unless_cybers_or_group_chats_are_on(ienv):
    """跟着赛博、群聊的开关：两样都关着 → 一律 404（app 不显示「拿去讨论」）；只开群聊也行，只是找不了赛博。"""
    fid = new_idea()
    ienv.cfg(user_name="Alex", cybers=False)
    assert ienv.client.get(f"/api/think/ideas/{fid}/discuss", headers=H).status_code == 404
    assert ienv.client.post(f"/api/think/ideas/{fid}/points", headers=H).status_code == 404
    assert ienv.client.post(f"/api/think/ideas/{fid}/podcast", json={}, headers=H).status_code == 404
    ienv.cfg(groupchat=True, user_name="Alex", cybers=False)
    can = ienv.client.get(f"/api/think/ideas/{fid}/discuss", headers=H).json()["can"]
    assert can["cyber"] is False and can["group"] is True


def test_one_to_one_with_a_cyber_gets_only_this_idea_as_context(ienv, scripted):
    v = make(ienv, "Victor", identity="An early-stage investor.")["id"]
    fid = new_idea()
    f = scripted([("You are Victor", {"text": "What's the race date?"})])

    async def go():
        out = await ideatalk.to_cyber(fid, ideatalk.CyberIn(cyber=v))
        await done(v)
        return out
    out = run(go())
    assert out["thread"] == v
    seen = said(f.requests[0][1])
    assert "The thought, in full; just this one:" in seen and "only if training fits around work" in seen
    with chat._lock, chat.db() as conn:
        rows = conn.execute("SELECT role, text FROM messages WHERE thread=? ORDER BY id", (v,)).fetchall()
    assert rows[0]["text"].startswith('Let\'s talk about this thought: "Maybe I should run') and rows[1]["text"] == "What's the race date?"
    assert think.frag_json(think.frag_by_id(fid))["discussion"] == {"kind": "cyber", "id": v}  # 想法上记下在哪讨论
    shown = ienv.client.get(f"/api/think/ideas/{fid}/discuss", headers=H).json()["discussion"]
    assert shown == {"kind": "cyber", "id": v, "title": "Victor", "open": True}


def test_a_group_from_an_idea_writes_back_and_finishes_it(ienv, scripted):
    v = make(ienv, "Victor")["id"]
    s = make(ienv, "Socrates")["id"]
    fid = new_idea()
    scripted([("You are Victor", {"text": "Pick a spring race."}), ("You are Socrates", {"text": "Why spring?"}),
              ("Write the summary of round 1", summ(line="Spring race, if work allows.", decided=[{"text": "Aim for a spring half"}]))])

    async def go():
        out = await ideatalk.to_group(fid, ideatalk.GroupIn(members=[v, s]))
        await settle(out["group"]["id"])
        return out["group"]["id"]
    gid = run(go())
    g = groupchat.get_group(gid)["group"]
    assert g["source"]["kind"] == "idea" and g["source"]["id"] == fid and g["topic"].startswith("Maybe I should run")
    frag = think.frag_by_id(fid)
    assert think.frag_json(frag)["discussion"] == {"kind": "group", "id": gid}
    assert "Aim for a spring half" in frag["text"] and frag["status"] == "open"  # 小结写回这条想法
    run(groupchat.end(gid))
    assert think.frag_by_id(fid)["status"] == "done"  # 讨论完：标为已想完


def test_into_a_podcast_and_to_an_agent(ienv, scripted):
    r = agent(ienv, "Running", icon="target", purpose="Plans runs and races")
    fid = new_idea("Record something about why I started running.")
    out = run(ideatalk.to_podcast(fid, ideatalk.PodcastIn()))
    ep = out["episode"]
    assert ep["title"].startswith("Record something about why I started") and len(ep["title"]) <= 40 and [m["kind"] for m in podmaterials.listing(ep["id"])] == ["idea"]
    assert think.frag_json(think.frag_by_id(fid))["discussion"] == {"kind": "podcast", "id": ep["id"]}
    fid2 = new_idea("Book a 10K for April.")
    f = scripted([("Book a 10K for April.", {"text": "I'll look for April 10Ks."})])

    async def go():
        out = await ideatalk.to_agent(fid2, ideatalk.AgentIn(agent=r, note="Please find one."))
        await done(r)
        return out
    out = run(go())
    assert out["thread"] == r and "Please find one." in said(f.requests[0][1])
    assert think.frag_json(think.frag_by_id(fid2))["discussion"] == {"kind": "agent", "id": r}


def test_points_for_a_friend_are_key_points_not_the_original(ienv, scripted):
    fid = new_idea()
    scripted([("key points to send a friend", {"text": json.dumps({"points": ["Thinking about a spring half marathon", "Only if work allows"]})})])
    out = run(ideatalk.points(fid))
    assert out["points"] == ["Thinking about a spring half marathon", "Only if work allows"] and out["text"].startswith("· Thinking")


def test_points_are_a_tool_free_ask_and_only_your_own_click_uses_the_plan(ienv, monkeypatch):
    """想法可能是收藏的别人的文章：只走内核的零工具一问，不退回带工具的对话回合。app 里点的算你自己的（engine.use.json 是套餐就走套餐），
    经 /mcp 来的照它报的来源算：不花套餐。"""
    import engine
    import llmjson
    got: list = []

    async def ask_json(prompt, input_, *, model, purpose, required, timeout, kind, origin):
        got.append((model, kind, origin))
        return {"points": ["One point"]}, f"builtin:{model}"

    async def no_turn(*a, **k):
        raise AssertionError("fell back to a chat turn with tools")
    monkeypatch.setattr(engine, "ask_json", ask_json)
    monkeypatch.setattr(llmjson, "via_complete", no_turn)
    fid = new_idea()
    ienv.cfg(groupchat=True, user_name="Alex", engine={"model": "deepseek/deepseek-flash", "use": {"json": "claude-code/sonnet"}})
    assert ienv.client.post(f"/api/think/ideas/{fid}/points", headers=H).json()["points"] == ["One point"]
    assert ienv.client.post(f"/api/think/ideas/{fid}/points", headers={**H, "X-Mousse-Via": "mcp"}).status_code == 200
    assert got == [("claude-code/sonnet", "owner", "user"), ("deepseek/deepseek-flash", "owner", "mcp")]
