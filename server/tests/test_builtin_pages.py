"""10/19 第 1 批：claw 选 builtin 时 app 的活动、模型、安全、定时任务这几页读内核（engine/appdata.py），caps 的 cron / approvals / billing
跟着打开；新建 Agent 有自己的工作区和技能名单（agents.provision_builtin、engine/agent_skills.py），装日结提案里的 skill 写 engine.skills.agents，
app 里改名单的接口（/api/setup/skills/{agent}）只认主人的设备、写 server.json 之前先备份。
OpenClaw 那条路照旧：同样的接口在 openclaw 下还是去问 Gateway / openclaw 命令（这里换成假的，记下问了什么）。
不连真的模型、不花钱：内核的一轮轮直接写进事件表，密钥是假的，Claude Code 的检测是假的（runners._found），不碰这台机器上真的命令。"""
from __future__ import annotations

import asyncio
import json
import re
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import chat
import claw
import config
import data
from engine import agent_skills, appdata, jobs, runners, store, when

H = {"Authorization": "Bearer tok-phone"}
TOKENS = {"phone": "tok-phone", "mcp": "tok-mcp"}
FAKE_KEY = "sk-test-builtin-pages-0123QZXW"  # 假的密钥：哪个接口的返回里都不许出现（连末四位 QZXW 都不许）
CLOCK = r"(Today|Yesterday) \d\d:\d\d"  # data.when 写的时间（刚过零点跑的测试会是 Yesterday）
BUILTIN = {"claw": {"kind": "builtin"}, "auth": {"tokens": TOKENS}, "timezone": "UTC", "language": "en",
           "engine": {"model": "deepseek/deepseek-flash", "models": ["deepseek/deepseek-flash", ["moonshot/kimi-k3", "deepseek/deepseek-flash"]],
                      "fallbacks": ["moonshot/kimi-k3"], "use": {"background": "deepseek/deepseek-flash", "card": True}}}


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def env(server_json, tmp_path, monkeypatch):
    """claw builtin、令牌；主工作区和 OpenClaw 的家放 tmp；DeepSeek 的密钥是假的（Kimi 没有）；Claude Code 当没查到；不推送。
    cfg(**改动) 换 server.json（在 BUILTIN 上叠）；conf() 读回 server.json；backups() 列备份。"""
    import main
    import push
    for name in ("DEEPSEEK_API_KEY", "MOONSHOT_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", FAKE_KEY)
    monkeypatch.setattr(runners, "_warm", False)
    monkeypatch.setattr(runners, "_found", {})
    ws, home = tmp_path / "ws", tmp_path / "oc"
    ws.mkdir()
    (ws / "SOUL.md").write_text("Warm and brief.", encoding="utf8")
    (ws / "USER.md").write_text("# USER.md\n", encoding="utf8")
    monkeypatch.setattr(config.settings, "workspace", ws)
    monkeypatch.setattr(config.settings, "openclaw_home", home)
    monkeypatch.setattr(config.settings, "openclaw_json", home / "openclaw.json")

    async def no_push(*a, **k):
        return {"ok": True, "sent": 0}
    monkeypatch.setattr(push, "send_push", no_push)

    def cfg(**extra) -> Path:
        return server_json({**BUILTIN, **extra})
    cfg()

    class E:
        pass
    e = E()
    e.cfg, e.dir, e.ws, e.home, e.client = cfg, tmp_path, ws, home, TestClient(main.app)
    e.conf = lambda: json.loads(config.CONFIG_PATH.read_text(encoding="utf8"))
    e.backups = lambda: sorted(p.name for p in tmp_path.glob("server.json.bak-*"))
    return e


@pytest.fixture
def no_gateway(monkeypatch):
    """builtin 下谁都不许去问 Gateway / openclaw 命令：问了就记下来、报错。"""
    asked: list = []

    async def gw(method, params=None, timeout=30):
        asked.append(method)
        raise AssertionError(f"builtin asked the Gateway: {method}")

    async def cli(*args, timeout=30):
        asked.append(args)
        raise AssertionError(f"builtin ran openclaw {args}")
    monkeypatch.setattr(data, "gateway_call", gw)
    monkeypatch.setattr(data, "openclaw_cli", cli)
    return asked


def iso(minutes_ago: float = 0) -> str:
    return (datetime.now(config.settings.tz) - timedelta(minutes=minutes_ago)).isoformat(timespec="milliseconds")


def turn(conv: str, *, kind: str = "owner", agent: str | None = "main", model: str = "deepseek/deepseek-flash", status: str = "done",
         origin: str | None = "user", usage: dict | None = None, models: list | None = None, error: str | None = None, ago: float = 1,
         tools: list[str] = ()) -> str:
    tid = "t_" + uuid.uuid4().hex[:16]
    with store.tx() as conn:
        conn.execute("INSERT INTO engine_turns(id, conversation, kind, agent, model, origin, status, error, steps, usage, started_at, ended_at, ms, models) "
                     "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (tid, conv, kind, agent, model, origin, status, error, len(tools) + 1, json.dumps(usage or {}), iso(ago + 0.5), iso(ago), 1200,
                      json.dumps(models) if models else None))
        for i, name in enumerate(tools, 1):
            conn.execute("INSERT INTO engine_steps(turn, seq, type, name, status, started_at) VALUES(?,?,?,?,?,?)", (tid, i, "tool", name, "done", iso(ago)))
    return tid


def group(gid: str, name: str) -> None:
    with chat._lock, data.ddb() as conn:
        conn.execute("INSERT OR REPLACE INTO groups(id, name, icon, purpose, created_at) VALUES(?,?,?,?,?)", (gid, name, "leaf", "", chat.now_iso()))


def drop_group(gid: str) -> None:
    with chat._lock, data.ddb() as conn:
        conn.execute("DELETE FROM groups WHERE id=?", (gid,))
        conn.execute("DELETE FROM threads WHERE id=?", (gid,))


def by_id(rows: list[dict]) -> dict[str, dict]:
    return {r["id"]: r for r in rows}


# —— caps ——————————————————————————————————————————————————————————————————————————————

def test_caps_open_cron_approvals_and_billing_only_under_builtin(env):
    c = env.client.get("/api/health", headers=H).json()["claw"]["caps"]
    assert c["cron"] is c["approvals"] is c["billing"] is True and c["tasks"] and c["agentWorkspaces"] and c["channels"] is False
    env.cfg(claw={"kind": "openai", "url": "http://127.0.0.1:9/v1"})
    c = claw.caps()
    assert not (c["cron"] or c["approvals"] or c["billing"])  # 别的 claw 照旧没有
    env.cfg(claw={"kind": "openclaw"})
    assert all(claw.caps()[k] for k in ("cron", "approvals", "billing", "channels"))  # OpenClaw 照旧全有


# —— 活动记录 ——————————————————————————————————————————————————————————————————————————

def test_activity_reads_the_engine_turns_and_job_runs_without_the_gateway(env, no_gateway, monkeypatch):
    db = env.dir / "mousse.db"  # 只看这个测试自己那几行：它的行记在 1 到 9 分钟以前，跟别的测试共用一个库的话，前面刚写的几百行都比它新，把它挤出前 200 条
    monkeypatch.setattr(config.settings, "db", db)  # 内核的表
    monkeypatch.setattr(chat, "DB", db)  # app 自己的表（activity_log、groups）：跟线上一样放在同一个库里
    group("g-actv", "Garden")
    water = make_job("Water", "on")
    jid = water["id"]
    ok = turn("main", usage={"in": 1200, "out": 300, "cost": 0.0123}, tools=["board", "board", "inbox"], ago=9)
    bad = turn("g-actv", agent="g-actv", status="error", error="DeepSeek HTTP 500: upstream exploded", ago=8)
    stopped = turn("main", status="aborted", error="cancelled", usage={"in": 10, "out": 2, "cost": 0.0001}, ago=7)
    plan = turn("main", model="claude-code/sonnet", usage={"in": 900, "out": 80, "notional_cost_usd": 0.4}, ago=6)
    switched = turn("main", model="moonshot/kimi-k3", models=["moonshot/kimi-k3", "deepseek/deepseek-flash"],
                    usage={"in": 100, "out": 10, "cost": 0.0004}, ago=5)
    door = turn("doorman", kind="outsider", agent=None, model="ollama/llama-test", origin="doorman", usage={"in": 50, "out": 5}, ago=4)
    reboot = turn("main", status="aborted", error="server restarted", ago=3.5)
    iso_turn = turn(f"cron:{jid}", origin="auto", usage={"in": 400, "out": 40, "cost": 0.002}, ago=3)
    chat_turn = turn("main", origin="auto", usage={"in": 400, "out": 40, "cost": 0.002}, ago=2.5)  # session chat：在对话里跑的那一轮
    hb_turn = turn("heartbeat:main", origin="auto", usage={"in": 20, "out": 2, "cost": 0.00002}, ago=2)
    runs = {"ok": ("jr-" + uuid.uuid4().hex[:10], jid, "cron", "ok", iso_turn, None, 3),
            "chat": ("jr-" + uuid.uuid4().hex[:10], jid, "cron", "ok", chat_turn, None, 2.5),
            "beat": ("jr-" + uuid.uuid4().hex[:10], "heartbeat:main", "heartbeat", "silent", hb_turn, None, 2),
            "miss": ("jr-" + uuid.uuid4().hex[:10], jid, "missed", "missed", None, None, 1.5),
            "err": ("jr-" + uuid.uuid4().hex[:10], jid, "on_time", "error", None, "No activity for 600s; stopped", 1)}
    with store.tx() as conn:
        for rid, job, kind, status, t, err, ago in runs.values():
            conn.execute("INSERT INTO engine_job_runs(id, job, agent, due, started_at, ended_at, kind, status, turn, error) VALUES(?,?,?,?,?,?,?,?,?,?)",
                         (rid, job, "main", iso(ago), iso(ago), iso(ago), kind, status, t, err))
    chat.log_activity("Created agent \"Garden\"", "edit")
    r = env.client.get("/api/activity?limit=200", headers=H)
    assert r.status_code == 200 and no_gateway == []  # 一次都没问 Gateway
    rows = r.json()["activity"]
    assert all({"id", "time", "actor", "text", "kind"} <= set(x) for x in rows)
    assert all(x["kind"] in ("reply", "cron", "failed") for x in rows if x.get("source") in ("turn", "job", "heartbeat"))  # app 认得的那几种
    got = by_id(rows)
    a = got[ok]
    assert a["kind"] == "reply" and a["actor"] == "TestMousse · Main chat" and re.fullmatch(CLOCK, a["time"])
    assert a["text"] == "Replied (deepseek/deepseek-flash · $0.0123), used tools: board ×2, inbox"
    assert a["cost"] == 0.0123 and a["billing"] == "metered" and a["tokens"] == 1500 and a["model"] == "deepseek/deepseek-flash"
    assert a["tools"] == {"board": 2, "inbox": 1} and a["turn"] == ok and a["conversation"] == "main"
    b = got[bad]
    assert b["kind"] == "failed" and b["actor"] == "TestMousse · Garden" and "A reply failed" in b["text"] and "upstream exploded" in b["text"]
    assert got[stopped]["kind"] == "reply" and "stopped partway" in got[stopped]["text"]
    assert got[reboot]["kind"] == "failed" and got[reboot]["text"].endswith(": the server restarted")
    p = got[plan]
    assert p["billing"] == "plan" and p["cost"] is None and "(claude-code/sonnet · your plan)" in p["text"]
    assert "deepseek/deepseek-flash · $0.0004" in got[switched]["text"] and "switched from moonshot/kimi-k3" in got[switched]["text"]
    d = got[door]
    assert d["actor"] == "TestMousse" and d["text"].startswith("Doorman reviewed a line") and "55 tokens" in d["text"]  # 价目里没有：只有 token
    assert iso_turn not in got and chat_turn not in got and hb_turn not in got  # 定时任务那一轮算在它跑的那一次里
    assert got[runs["ok"][0]]["text"] == '"Water" finished (deepseek/deepseek-flash · $0.002)' and got[runs["ok"][0]]["kind"] == "cron"
    assert got[runs["ok"][0]]["actor"] == "Scheduled job · TestMousse" and got[runs["chat"][0]]["turn"] == chat_turn
    assert got[runs["beat"][0]]["text"].startswith('"Heartbeat (Main chat)" finished; nothing to say this time')
    assert got[runs["miss"][0]]["kind"] == "failed" and "was missed" in got[runs["miss"][0]]["text"]
    assert got[runs["err"][0]]["kind"] == "failed" and got[runs["err"][0]]["text"] == '"Water" failed: No activity for 600s; stopped'
    assert any(x["text"] == 'Created agent "Garden"' and x["id"].startswith("a") for x in rows)  # app 自己的动作照旧
    order = [x["id"] for x in rows]
    assert order.index(runs["err"][0]) < order.index(ok)  # 新的在前
    zh = env.client.get("/api/activity?limit=200", headers={**H, "Accept-Language": "zh-CN"}).json()["activity"]
    assert by_id(zh)[ok]["text"] == "回复了一次（deepseek/deepseek-flash · $0.0123），用了工具 board ×2、inbox"
    assert len(env.client.get("/api/activity?limit=3", headers=H).json()["activity"]) == 3


def test_activity_under_openclaw_still_reads_the_gateway_audit(env, monkeypatch):
    env.cfg(claw={"kind": "openclaw"})
    asked = []
    now = int(datetime.now().timestamp() * 1000)

    async def gw(method, params=None, timeout=30):
        asked.append(method)
        if method == "audit.activity.list":
            return {"events": [{"eventId": "ev-oc-1", "action": "agent.run.finished", "runId": "run-1", "status": "succeeded", "agentId": "main",
                                "sessionKey": "agent:main:main", "occurredAt": now}]}
        return {"tasks": []}
    monkeypatch.setattr(data, "gateway_call", gw)
    data.forget_cache("audit", "tasks")
    rows = by_id(env.client.get("/api/activity", headers=H).json()["activity"])
    assert sorted(asked) == ["audit.activity.list", "tasks.list"]
    assert rows["ev-oc-1"]["text"] == "Replied" and rows["ev-oc-1"]["actor"] == "TestMousse · Main chat"
    data.forget_cache("audit", "tasks")


# —— 模型与计费 ——————————————————————————————————————————————————————————————————————

def test_models_show_the_engine_chain_logins_purposes_and_usage(env, no_gateway, monkeypatch):
    monkeypatch.setattr(runners, "_found", {"claude-code": {"bin": "/x/claude", "auth": "claude.ai", "billing": "subscription", "plan": "max"}})
    turn("main", usage={"in": 100, "out": 10, "cost": 0.5}, ago=1)
    r = env.client.get("/api/models", headers=H)
    assert r.status_code == 200 and no_gateway == []
    m = r.json()
    assert FAKE_KEY not in r.text and FAKE_KEY[-4:] not in r.text  # 密钥一个字都不出现
    assert m["primary"] == "deepseek/deepseek-flash" and m["fallbacks"] == ["moonshot/kimi-k3"] and m["subagent"] == "deepseek/deepseek-flash"
    assert m["allowed"] == ["deepseek/deepseek-flash", "moonshot/kimi-k3", "claude-code/opus", "claude-code/sonnet", "claude-code/haiku"]
    assert m["claw"]["kind"] == "builtin" and m["claw"]["caps"]["billing"] is True
    provs = {p["provider"]: p for p in m["providers"]}
    assert [p["provider"] for p in m["providers"]][0] == "claude-code"  # 套餐的在前（同 OpenClaw 那条路的「登录与计费」）
    assert provs["claude-code"] == {"provider": "claude-code", "name": "Claude Code", "status": "ok", "subscription": True, "expires": None,
                                    "plan": "max", "auth": "claude.ai"}
    assert provs["deepseek"]["status"] == "ok" and provs["deepseek"]["subscription"] is False and provs["deepseek"]["name"] == "DeepSeek"
    assert provs["moonshot"]["status"] == "missing"  # Kimi 没有密钥
    chains = {x["ref"]: x for x in m["engine"]["models"]}
    assert chains["moonshot/kimi-k3"]["chain"] == ["deepseek/deepseek-flash"] and chains["deepseek/deepseek-flash"]["chain"] == ["moonshot/kimi-k3"]
    assert chains["deepseek/deepseek-flash"]["usable"] and not chains["moonshot/kimi-k3"]["usable"] and chains["claude-code/opus"]["plan"] == "claude-code"
    assert m["engine"]["fallbacks"] == ["moonshot/kimi-k3"] and m["engine"]["fallback"] is True
    assert m["claudeCode"]["available"] and m["claudeCode"]["billing"] == "subscription" and m["claudeCode"]["plan"] == "max"
    assert set(m["chatgpt"]) >= {"connected", "ready", "plan"} and "email" not in m["chatgpt"]
    rows = {x["id"]: x for x in m["purposes"]}
    assert set(rows) == {"chat", "background", "writing", "json", "compact", "outsider", "embeddings"}
    assert rows["chat"]["current"] == "deepseek/deepseek-flash" and rows["background"]["effective"] == "deepseek/deepseek-flash"
    assert rows["outsider"]["parts"]["card"]["effective"] == "deepseek/deepseek-flash"
    assert m["usage"]["days"] == 30 and m["usage"]["total"]["cost"] >= 0.5  # 主人的设备看得到花了多少
    other = env.client.get("/api/models", headers={**H, "X-Mousse-Via": "mcp"}).json()  # 脚本、/mcp 替 Agent 发的：不给用量
    assert other["usage"] is None and other["primary"] == "deepseek/deepseek-flash"


def test_models_under_openclaw_still_read_openclaw_json(env, monkeypatch):
    env.cfg(claw={"kind": "openclaw"})
    monkeypatch.setattr(data, "config", lambda: {"agents": {"defaults": {"model": {"primary": "anthropic/claude-opus-5-5", "fallbacks": ["moonshot/kimi-k3"]},
                                                                          "subagents": {"model": "anthropic/claude-fable-5-1"},
                                                                          "modelPolicy": {"allow": ["anthropic/claude-opus-5-5"]}}}})
    asked = []

    async def gw(method, params=None, timeout=30):
        asked.append(method)
        return {"providers": [{"provider": "anthropic", "displayName": "Anthropic", "status": "ok", "profiles": [{"type": "oauth"}]}]}
    monkeypatch.setattr(data, "gateway_call", gw)
    data.forget_cache("auth")
    m = env.client.get("/api/models", headers=H).json()
    data.forget_cache("auth")
    assert asked == ["models.authStatus"]
    assert m == {"ok": True, "primary": "anthropic/claude-opus-5-5", "fallbacks": ["moonshot/kimi-k3"], "subagent": "anthropic/claude-fable-5-1",
                 "allowed": ["anthropic/claude-opus-5-5"], "providers": [{"provider": "anthropic", "name": "Anthropic", "status": "ok", "subscription": True,
                                                                         "expires": None}]}


# —— 安全 ——————————————————————————————————————————————————————————————————————————

async def tool_card(title: str, tool: str = "journal", source: str = "main") -> str:
    """一张等你点头的内核的卡（收件箱 kind tool）和它背后记下的调用（engine_calls），同 tools.submit_card。"""
    import inbox

    from engine import tools as tools_mod
    card = {"title": title, "why": "It can't be undone.", "changes": [f"Tool: {tool}"], "detail": "delete j-1"}
    res = await inbox.add(inbox.ItemIn(kind="tool", source=source, thread=source, dedupe=f"test:{uuid.uuid4().hex}", **card))
    with store.tx() as conn:
        conn.execute("""INSERT INTO engine_calls(id, inbox, dedupe, tool, args, input, agent, thread, turn, call, card, status, created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", ("c_" + uuid.uuid4().hex[:16], res["id"], "d", tool, '["delete", "j-1"]', None, source, source, None,
                                                  None, tools_mod.card_hash(card["title"], card["why"], card["changes"], card["detail"]), "pending", iso()))
    return res["id"]


def test_security_shows_host_commands_tool_levels_sandbox_and_cards(env, no_gateway):
    group("g-secu", "Lab")
    iid = run(tool_card("Delete a journal entry"))
    try:
        env.cfg(engine={**BUILTIN["engine"],
                        "host_exec": {"agents": {"main": {"allow": ["python3 <workspace>/scripts/*.py ...", "git status"]},
                                                 "g-secu": {"security": "full"}}},
                        "tools": {"levels": {"journal": "off", "board rows delete": "auto"}},
                        "sandbox": {"agents": {"main": {"access": "ro"}, "g-secu": {"only": False, "access": "rw", "browser": False}}}})
        r = env.client.get("/api/security", headers=H)
        assert r.status_code == 200 and no_gateway == []  # 不读 openclaw approvals
        s = r.json()
        facts = {f["title"]: f for f in s["facts"]}
        assert all(set(f) == {"title", "sub", "state", "tone"} and f["tone"] in ("good", "warn", "neutral") for f in s["facts"])
        assert "Gateway is local-only" not in facts and facts["App API auth"]["state"] == "OK" and "phone, mcp" in facts["App API auth"]["sub"]
        main_cmds = facts["Commands on the server · Main chat"]
        assert main_cmds["tone"] == "good" and "Allowlist: 2 (python3 <workspace>/scripts/*.py ...; git status)" in main_cmds["sub"]
        assert "anything else asks you first" in main_cmds["sub"]
        lab = facts["Commands on the server · Lab"]
        assert lab["state"] == "Wide open" and lab["tone"] == "warn"
        lv = facts["Tool levels"]
        assert lv["state"] == "Changed" and lv["tone"] == "warn" and "board rows delete → auto; journal → off" in lv["sub"]
        box = facts["Sandbox"]
        assert box["state"] == "On" and "Main chat: its own sandbox (workspace mounted read-only)" in box["sub"]
        assert "Lab: its own sandbox (workspace writable (commands ask you first))" in box["sub"] and "Doorman" in box["sub"]
        pending = facts["Awaiting approval"]
        assert int(pending["state"]) >= 1 and s["engine"]["pending"] == int(pending["state"])
        assert s["engine"]["levels"] == {"journal": "off", "board rows delete": "auto"}
        assert {x["agent"]: x["security"] for x in s["engine"]["hostExec"]} == {"main": "allowlist", "g-secu": "full"}
        assert [p["title"] for p in s["plan"]] == ["Payments"] and len(s["rules"]) == 7  # 隔离、出网审批、凭证代位都做了：plan 只留付款
        env.cfg()  # 都不写：主机命令全关、工具档位默认、沙箱没装
        facts = {f["title"]: f for f in env.client.get("/api/security", headers=H).json()["facts"]}
        assert facts["Commands on the server"]["state"] == "Off" and facts["Tool levels"]["state"] == "Default"
        assert facts["Sandbox"]["state"] == "Off" and "errand.py setup" in facts["Sandbox"]["sub"]
    finally:
        run(_withdraw(iid))
        drop_group("g-secu")


async def _withdraw(iid: str) -> None:
    import inbox
    try:
        await inbox.withdraw(iid)
    except Exception:  # noqa: BLE001 — 已经定了的
        pass


def test_security_under_openclaw_still_reads_openclaw(env, monkeypatch):
    env.cfg(claw={"kind": "openclaw"})
    monkeypatch.setattr(data, "config", lambda: {"gateway": {"bind": "loopback", "port": 18789}, "agents": {"defaults": {}}})
    ran = []

    async def cli(*args, timeout=30):
        ran.append(args)
        if args[:2] == ("approvals", "get"):
            return {"effectivePolicy": {"scopes": [{"agentId": "main", "security": {"effective": "allowlist"}, "ask": {"effective": "on-miss"}}]}}
        return {"approvals": []}
    monkeypatch.setattr(data, "openclaw_cli", cli)
    data.forget_cache("policy", "approvals")
    s = env.client.get("/api/security", headers=H).json()
    data.forget_cache("policy", "approvals")
    titles = [f["title"] for f in s["facts"]]
    assert titles[:2] == ["Gateway is local-only", "App API auth"] and "Command execution" in titles and "Tool levels" not in titles
    assert ("approvals", "get") in ran and ("approvals", "pending") in ran and "engine" not in s
    assert [p["title"] for p in s["plan"]] == ["Isolated execution", "Doorman egress approval (errands)", "Credential stand-ins"]


# —— 等你点头（老 app 的审批队列）——————————————————————————————————————————————————————

def test_approvals_list_and_decide_the_engines_cards(env, no_gateway):
    iid = run(tool_card("Delete a journal entry"))
    try:
        r = env.client.get("/api/approvals", headers=H)
        assert r.status_code == 200 and no_gateway == []
        a = by_id(r.json()["approvals"])[iid]
        assert a["kind"] == "tool" and a["action"] == "Delete a journal entry" and a["detail"] == "It can't be undone." and a["groupId"] is None
        assert {"k": "Tool", "v": "journal"} in a["fields"] and {"k": "Agent", "v": "TestMousse"} in a["fields"] and re.fullmatch(CLOCK, a["requestedAt"])
        assert env.client.post("/api/approvals/ib-nope", headers=H, json={"allow": True}).status_code == 404
        r = env.client.post(f"/api/approvals/{iid}", headers=H, json={"allow": False})  # 拒绝：走收件箱，卡背后的调用记成没同意
        assert r.status_code == 200 and r.json() == {"ok": True}
        import inbox

        from engine import tools as tools_mod
        assert inbox.item(iid)["status"] == "rejected" and tools_mod.call_of(iid)["status"] == "rejected"
        assert iid not in by_id(env.client.get("/api/approvals", headers=H).json()["approvals"])
        assert env.client.post(f"/api/approvals/{iid}", headers=H, json={"allow": True}).status_code == 404  # 定了的不能再点
        acts = env.client.get("/api/activity", headers=H).json()["activity"]
        assert any(x["text"] == 'Declined "Delete a journal entry" from TestMousse' and x["kind"] == "denied" for x in acts)
    finally:
        run(_withdraw(iid))


def test_approvals_under_openclaw_still_read_openclaw(env, monkeypatch):
    env.cfg(claw={"kind": "openclaw"})
    ran = []

    async def cli(*args, timeout=30):
        ran.append(args)
        if args[0] == "approvals" and args[1] == "pending":
            return {"approvals": [{"id": "ap-1", "request": {"command": "rm -rf /tmp/x", "agentId": "main"}, "createdAtMs": 1}]}
        return {}
    monkeypatch.setattr(data, "openclaw_cli", cli)
    data.forget_cache("approvals")
    got = env.client.get("/api/approvals", headers=H).json()["approvals"]
    data.forget_cache("approvals")
    assert [a["id"] for a in got] == ["ap-1"] and got[0]["kind"] == "exec" and ("approvals", "pending") in ran
    assert env.client.post("/api/approvals/ap-1", headers=H, json={"allow": True}).json() == {"ok": True}
    assert ("approvals", "resolve", "ap-1", "allow-once") in ran
    data.forget_cache("approvals")


# —— 接下来会自动做的事 ——————————————————————————————————————————————————————————————————

def make_job(name: str, state: str, *, approved: bool = True, when_text: str = "every day 9am") -> dict:
    spec = jobs.clean("main", {"when": when_text, "task": f"{name}.", "name": name}, by_user=True)
    nxt = jobs.first_next(spec) if state == "on" else None
    return jobs._insert("main", spec, state=state, approved=approved, by="user" if approved else "agent:main", next_run=nxt)


def test_upcoming_lists_engine_jobs_and_heartbeats_and_toggles_them(env, no_gateway, monkeypatch):
    async def no_timers():
        return []
    monkeypatch.setattr(data, "timers", no_timers)
    env.cfg(engine={**BUILTIN["engine"], "heartbeat": {"main": {"every": "2h", "active": ["08:00", "22:00"]}}})
    on, paused, draft = make_job("Stretch", "on"), make_job("Read", "paused"), make_job("Walk", "draft", approved=False)
    done = make_job("Old one", "done")
    try:
        r = env.client.get("/api/upcoming", headers=H)
        assert r.status_code == 200 and no_gateway == []
        items = r.json()["upcoming"]
        got = by_id(items)
        a = got[on["id"]]
        assert a["source"] == "cron" and a["title"] == "Stretch" and a["enabled"] and a["toggleable"] and a["state"] == "on"
        assert a["repeat"] == "Every day 09:00" and a["nextAt"] == int(when.parse_iso(on["next_run"]).timestamp() * 1000) and a["agent"] == "TestMousse"
        assert a["when"] and a["last"] is None and a["deliverName"]
        assert got[paused["id"]]["enabled"] is False and got[paused["id"]]["toggleable"] is True and got[paused["id"]]["nextAt"] == 0
        d = got[draft["id"]]
        assert d["enabled"] is False and d["toggleable"] is False and d["stateText"] == "waiting for your OK"
        assert done["id"] not in got  # 跑完了的不是「接下来」
        hb = got["heartbeat:main"]
        assert hb["source"] == "heartbeat" and hb["toggleable"] is False and hb["title"] == "Heartbeat (Main chat)" and hb["repeat"] == "Every 2 h · 08:00–22:00"
        assert [x["enabled"] for x in items] == sorted((x["enabled"] for x in items), reverse=True)  # 开着的在前
        r = env.client.post(f"/api/upcoming/{on['id']}", headers=H, json={"enabled": False})
        assert r.status_code == 200 and r.json()["job"]["state"] == "paused" and jobs.get(on["id"])["state"] == "paused"
        r = env.client.post(f"/api/upcoming/{paused['id']}", headers=H, json={"enabled": True})
        assert r.status_code == 200 and jobs.get(paused["id"])["state"] == "on" and jobs.get(paused["id"])["next_run"]
        assert env.client.post(f"/api/upcoming/{paused['id']}", headers=H, json={"enabled": True}).status_code == 200  # 已经开着：不变
        assert env.client.post(f"/api/upcoming/{draft['id']}", headers=H, json={"enabled": True}).status_code == 409  # 要在卡上点「开」
        assert env.client.post("/api/upcoming/heartbeat:main", headers=H, json={"enabled": False}).status_code == 409
        assert env.client.post("/api/upcoming/job-nope", headers=H, json={"enabled": False}).status_code == 404
        texts = [x["text"] for x in env.client.get("/api/activity", headers=H).json()["activity"] if x["kind"] == "toggled"]
        assert 'Disabled scheduled job "Stretch"' in texts and 'Enabled scheduled job "Read"' in texts
    finally:
        for j in (on, paused, draft, done):
            jobs._write(j["id"], state="deleted", next_run=None)


def test_upcoming_under_openclaw_still_reads_openclaw_cron(env, monkeypatch):
    env.cfg(claw={"kind": "openclaw"}, upcoming={"titles": {"heartbeat-main": {"zh": "心跳检查（main）", "en": "Heartbeat check (main)"}}})
    asked = []

    async def gw(method, params=None, timeout=30):
        asked.append((method, params))
        return {"jobs": [{"id": "oc-job-1", "name": "heartbeat-main", "enabled": True, "schedule": {"kind": "every", "everyMs": 3_600_000},
                          "state": {"nextRunAtMs": int(datetime.now().timestamp() * 1000) + 60_000}}]}

    async def no_timers():
        return []
    monkeypatch.setattr(data, "gateway_call", gw)
    monkeypatch.setattr(data, "timers", no_timers)
    data.forget_cache("cron")
    items = env.client.get("/api/upcoming", headers=H).json()["upcoming"]
    assert [x["id"] for x in items] == ["oc-job-1"] and items[0]["title"] == "Heartbeat check (main)" and items[0]["repeat"] == "Every 1 h"
    assert env.client.post("/api/upcoming/oc-job-1", headers=H, json={"enabled": False}).json() == {"ok": True}
    assert ("cron.update", {"id": "oc-job-1", "patch": {"enabled": False}}) in asked
    data.forget_cache("cron")
    env.cfg(claw={"kind": "openclaw"})  # 没配 upcoming.titles：照定时任务自己的名字
    items = env.client.get("/api/upcoming", headers=H).json()["upcoming"]
    assert items[0]["title"] == "heartbeat-main"
    data.forget_cache("cron")


def test_upcoming_timer_titles_and_reminders_come_from_config(env, monkeypatch):
    """systemd 定时器只列 upcoming.titles 里有的（repeat 写 weekly 才是每周）；一次性提醒只在配了 reminder_prefix 时列。"""
    nxt = int((datetime.now().timestamp() + 3600) * 1e6)
    listed = [{"unit": "daily-backup.timer", "next": nxt}, {"unit": "weekly-tidy.timer", "next": nxt}, {"unit": "other.timer", "next": nxt},
              {"unit": "mousse-reminder-1.timer", "next": nxt}]

    class Proc:
        def __init__(self, out: bytes):
            self.out = out

        async def communicate(self):
            return self.out, b""

    async def fake_exec(*args, **kw):
        if "list-timers" in args:
            return Proc(json.dumps(listed).encode())
        return Proc(b"Water the plants")  # systemctl show <unit> -p Description
    monkeypatch.setattr(data.asyncio, "create_subprocess_exec", fake_exec)
    env.cfg()
    assert asyncio.run(data.timers()) == []  # 什么都没配：一个都不列
    env.cfg(upcoming={"titles": {"daily-backup.timer": {"zh": "每日备份", "en": "Daily backup"},
                                 "weekly-tidy.timer": {"zh": "每周整理", "en": "Weekly tidy-up", "repeat": "weekly"}, "heartbeat-main": "Heartbeat"},
                      "reminder_prefix": "mousse-reminder-"})
    rows = {r["id"]: r for r in asyncio.run(data.timers())}
    assert set(rows) == {"daily-backup.timer", "weekly-tidy.timer", "mousse-reminder-1.timer"}
    assert (rows["daily-backup.timer"]["title"], rows["daily-backup.timer"]["repeat"]) == ("Daily backup", "Daily")
    assert (rows["weekly-tidy.timer"]["title"], rows["weekly-tidy.timer"]["repeat"]) == ("Weekly tidy-up", "Weekly")
    assert rows["mousse-reminder-1.timer"]["title"] == "Water the plants" and rows["mousse-reminder-1.timer"]["source"] == "reminder"
    assert data.cron_titles() == {"heartbeat-main": "Heartbeat"}  # 不是 .timer 的给 OpenClaw 的定时任务


# —— 新建 Agent 的工作区和技能名单 ———————————————————————————————————————————————————————

def test_a_new_agent_under_builtin_gets_a_workspace_and_a_skill_list(env, monkeypatch):
    from engine import context, host_files
    monkeypatch.setattr(config.settings, "agent_default_skills", ["journal", "inbox"])
    r = env.client.post("/api/groups", headers=H, json={"name": "Garden", "purpose": "Plants and seasons", "model": "deepseek/deepseek-flash"})
    assert r.status_code == 200
    gid = r.json()["id"]
    try:
        ws = env.dir / f"workspace-{gid}"  # 主工作区旁边
        assert sorted(p.name for p in ws.iterdir()) == ["AGENTS.md", "IDENTITY.md", "MEMORY.md", "SOUL.md", "memory", "skills"]
        agents_md = (ws / "AGENTS.md").read_text(encoding="utf8")
        assert "Garden" in agents_md and "Plants and seasons" in agents_md and "the server keeps it" in agents_md and "ask_user" not in agents_md
        assert "<!-- mousse:role -->" in (ws / "IDENTITY.md").read_text(encoding="utf8") and "Garden" in (ws / "MEMORY.md").read_text(encoding="utf8")
        assert (ws / "memory").is_dir() and (ws / "skills").is_symlink() and (ws / "skills").resolve() == (env.ws / "skills").resolve()
        assert (ws / "SOUL.md").read_text(encoding="utf8") == "Warm and brief."  # USER.md 不复制：内核把档案本身放进会话开头
        conf = env.conf()
        assert conf["agent_workspaces"][gid] == str(ws) and conf["engine"]["skills"]["agents"][gid] == ["journal", "inbox"]
        assert conf["engine"]["model"] == "deepseek/deepseek-flash" and conf["auth"]["tokens"] == TOKENS  # 别的设置原样留着
        assert len(env.backups()) == 1 and not env.home.exists()  # 写之前备份了；openclaw.json、~/.openclaw 一样不碰
        assert context.workspace_of(gid) == ws and host_files.root_of(gid) == ws.resolve()  # 内核按它找规矩文件、文件工具的范围
        assert set(agent_skills.skills_for(gid)) == {"journal", "inbox"}  # 开头的技能目录、skill 工具照名单
        r = env.client.patch(f"/api/groups/{gid}", headers=H, json={"purpose": "Plants, seasons and the allotment"})
        assert r.status_code == 200 and "the allotment" in (ws / "IDENTITY.md").read_text(encoding="utf8")  # 改职责照样写进 IDENTITY.md
        r = env.client.delete(f"/api/groups/{gid}", headers=H)
        assert r.status_code == 200 and r.json()["archived"] and not ws.exists()
        conf = env.conf()
        assert gid not in conf.get("agent_workspaces", {}) and gid not in conf["engine"].get("skills", {}).get("agents", {})  # 名单跟着去掉
    finally:
        drop_group(gid)


def test_new_agent_skill_lists_follow_the_request_and_the_template(env, monkeypatch):
    monkeypatch.setattr(config.settings, "agent_default_skills", [])
    made = []
    try:
        r = env.client.post("/api/groups", headers=H, json={"name": "Coach", "model": "deepseek/deepseek-flash", "skills": ["board", "goals"]})
        made.append(r.json()["id"])
        r = env.client.post("/api/groups", headers=H, json={"name": "Tutor", "model": "deepseek/deepseek-flash", "template": "study"})
        made.append(r.json()["id"])
        r = env.client.post("/api/groups", headers=H, json={"name": "Free", "model": "deepseek/deepseek-flash"})
        made.append(r.json()["id"])
        lists = env.conf()["engine"]["skills"]["agents"]
        assert lists[made[0]] == ["board", "goals"] and lists[made[1]] == ["study"]
        assert made[2] not in lists and agent_skills.names_for(made[2]) is None  # 都没给：不写名单，看全部（同 OpenClaw 那条路）
        assert all(gid in env.conf()["agent_workspaces"] for gid in made)
    finally:
        for gid in made:
            drop_group(gid)


def test_a_new_agent_leaves_nothing_behind_when_server_json_cannot_be_written(env):
    env.cfg()
    config.CONFIG_PATH.write_text('{"claw": {"kind": "builtin"}, "auth": {"tokens": {"phone": "tok-phone"}}, oops', encoding="utf8")
    config._cache = None
    import agents
    with pytest.raises(agents.ProvisionError):
        agents.provision_builtin("g-broken1", "Broken", "", "leaf")
    assert not (env.dir / "workspace-g-broken1").exists() and not env.backups()
    assert config.CONFIG_PATH.read_text(encoding="utf8").endswith("oops")  # 坏了的 server.json 不拿空的盖上去


def test_a_new_agent_under_openclaw_still_goes_through_openclaw(env, monkeypatch):
    import agents
    env.cfg(claw={"kind": "openclaw"})
    called = []
    monkeypatch.setattr(agents, "provision", lambda gid, name, purpose, icon, skills=None: called.append(("openclaw", gid, skills)))
    monkeypatch.setattr(agents, "provision_builtin", lambda *a, **k: called.append(("builtin",)))
    gid = env.client.post("/api/groups", headers=H, json={"name": "Oc agent", "model": "anthropic/claude-opus-5-5"}).json()["id"]
    try:
        assert called == [("openclaw", gid, None)] and "agent_workspaces" not in env.conf()
    finally:
        drop_group(gid)


# —— 装日结提案里的 skill ————————————————————————————————————————————————————————————————

SKILL_MD = "---\nname: garden-log\ndescription: Log what was planted and when.\n---\n\n# Garden log\n\nWrite one line per planting.\n"


def proposal(name: str, targets: list[str], content: str = SKILL_MD) -> str:
    import proposals
    iid = "ib-test-" + uuid.uuid4().hex[:8]
    with chat._lock, proposals.pdb() as conn:
        conn.execute("""INSERT INTO proposals(id, kind, slug, title, why, evidence, name, agents, content, status, inbox_id, day, created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", ("pp-" + uuid.uuid4().hex[:8], "skill", f"slug-{uuid.uuid4().hex[:6]}", "Add a garden log", "You asked 3 times.",
                                                  "[]", name, json.dumps(targets), content, "pending", iid, "2000-01-01", chat.now_iso()))  # 老日子：不占今天的提案名额
    return iid


def test_installing_a_proposed_skill_writes_the_engine_skill_lists(env, monkeypatch):
    import agents
    import proposals
    monkeypatch.setattr(agents, "edit_openclaw_json", lambda *a, **k: pytest.fail("builtin touched openclaw.json"))
    group("g-plist", "Garden")
    group("g-pfree", "Anything")
    env.cfg(engine={**BUILTIN["engine"], "skills": {"nudge": False, "agents": {"g-plist": ["journal"]}}})
    try:
        iid = proposal("garden-log", ["g-plist", "g-pfree", "main"])
        out = run(proposals.on_decided({"id": iid, "note": ""}, "approve"))
        assert out and "result" in out and "garden-log" in out["result"]
        assert (env.ws / "skills" / "garden-log" / "SKILL.md").read_text(encoding="utf8") == SKILL_MD
        lists = env.conf()["engine"]["skills"]["agents"]
        assert lists == {"g-plist": ["journal", "garden-log"]}  # 写了名单的加上；没写名单的（g-pfree、main）本来就看得到全部，不动
        assert env.conf()["engine"]["skills"]["nudge"] is False and len(env.backups()) == 1
        assert "garden-log" in agent_skills.skills_for("g-plist") and "garden-log" in agent_skills.skills_for("g-pfree")
        ctx = env.client.get("/api/proposals/context", headers=H).json()
        skills = {s["name"]: s for s in ctx["skills"]}
        assert {"main", "g-plist", "g-pfree"} <= set(skills["garden-log"]["agents"]) and "g-plist" not in skills["board"]["agents"]  # 内核直接读的 core 技能也列，照名单
        assert "g-pfree" in skills["board"]["agents"]
        clash = proposal("journal", ["main"], SKILL_MD.replace("garden-log", "journal"))  # 和内核直接读的 core 技能重名：不装、不盖掉它
        out = run(proposals.on_decided({"id": clash, "note": ""}, "approve"))
        assert out and "failed" in out and "already exists" in out["failed"] and not (env.ws / "skills" / "journal").exists()
    finally:
        drop_group("g-plist")
        drop_group("g-pfree")


def test_installing_a_proposed_skill_under_openclaw_still_edits_openclaw_json(env, monkeypatch):
    import agents
    import proposals
    env.cfg(claw={"kind": "openclaw"})
    edits = []

    def edit(change, tag):
        oc = {"agents": {"defaults": {"skills": ["journal"]}, "entries": {"main": {}}}}
        change(oc)
        edits.append((tag, oc))
        return True
    monkeypatch.setattr(agents, "edit_openclaw_json", edit)
    iid = proposal("garden-log", ["main"])
    out = run(proposals.on_decided({"id": iid, "note": ""}, "approve"))
    assert out and "result" in out
    assert edits == [("skill-garden-log", {"agents": {"defaults": {"skills": ["journal", "garden-log"]}, "entries": {"main": {}}}})]
    assert "engine" not in env.conf() or "skills" not in env.conf()["engine"]  # 内核的名单不碰


# —— app 里改一个 Agent 的技能名单 ———————————————————————————————————————————————————————

def test_the_skill_list_api_reads_and_replaces_one_agents_list(env):
    group("g-sapi", "Garden")
    try:
        c = env.client
        r = c.get("/api/setup/skills/g-sapi", headers=H)
        assert r.status_code == 200
        v = r.json()
        assert v["agent"] == "g-sapi" and v["name"] == "Garden" and v["applies"] and v["limited"] is False and v["skills"] is None
        names = {x["name"]: x for x in v["available"]}
        assert {"journal", "inbox", "board"} <= set(names) and names["journal"]["source"] == "core" and all(x["on"] for x in v["available"])
        r = c.put("/api/setup/skills/g-sapi", headers=H, json={"skills": ["journal", "board", "journal"]})
        assert r.status_code == 200 and r.json()["changed"] and r.json()["backup"]
        v = r.json()
        assert v["skills"] == ["journal", "board"] and v["limited"] and {x["name"] for x in v["available"] if x["on"]} == {"journal", "board"}
        assert env.conf()["engine"]["skills"]["agents"]["g-sapi"] == ["journal", "board"] and set(agent_skills.skills_for("g-sapi")) == {"journal", "board"}
        assert env.conf()["engine"]["model"] == "deepseek/deepseek-flash"  # 别的设置原样留着
        n = len(env.backups())
        assert c.put("/api/setup/skills/g-sapi", headers=H, json={"skills": ["journal", "board"]}).json()["changed"] is False
        assert len(env.backups()) == n  # 没变：不写、不备份
        r = c.put("/api/setup/skills/g-sapi", headers=H, json={"skills": ["journal", "no-such-skill"]})
        assert r.status_code == 400 and r.json()["unknown"] == ["no-such-skill"] and r.json()["ok"] is False
        assert c.put("/api/setup/skills/g-sapi", headers=H, json={"skills": "journal"}).status_code == 400
        assert c.put("/api/setup/skills/g-sapi", headers=H, json={}).status_code == 400
        assert c.put("/api/setup/skills/g-nope", headers=H, json={"skills": []}).status_code == 404
        assert c.put("/api/setup/skills/g-sapi", headers=H, json={"skills": []}).json()["skills"] == []  # 空列表 = 一个都不给
        assert agent_skills.skills_for("g-sapi") == {}
        r = c.put("/api/setup/skills/g-sapi", headers=H, json={"skills": None})  # null = 去掉名单，看全部
        assert r.json()["skills"] is None and "g-sapi" not in env.conf()["engine"].get("skills", {}).get("agents", {})
        assert c.get("/api/setup/skills/main", headers=H).json()["name"] == "TestMousse"
        acts = [x["text"] for x in c.get("/api/activity", headers=H).json()["activity"]]
        assert 'Changed which skills "Garden" can use' in acts
    finally:
        drop_group("g-sapi")


def test_the_skill_list_api_keeps_names_that_are_not_installed_yet(env):
    env.cfg(engine={**BUILTIN["engine"], "skills": {"agents": {"main": ["journal", "not-installed-yet"]}}})
    v = env.client.get("/api/setup/skills/main", headers=H).json()
    assert v["skills"] == ["journal", "not-installed-yet"] and v["missing"] == ["not-installed-yet"]
    r = env.client.put("/api/setup/skills/main", headers=H, json={"skills": ["not-installed-yet", "inbox"]})  # 名单上本来就有的照样能留着
    assert r.status_code == 200 and env.conf()["engine"]["skills"]["agents"]["main"] == ["not-installed-yet", "inbox"]


def test_the_skill_list_api_only_answers_the_owners_devices(env, monkeypatch):
    import main
    c = env.client
    before = config.CONFIG_PATH.read_bytes()
    assert c.get("/api/setup/skills/main").status_code == 401
    assert c.get("/api/setup/skills/main", headers={"Authorization": "Bearer tok-mcp"}).status_code == 403  # 给程序的令牌
    r = c.put("/api/setup/skills/main", headers={**H, "X-Mousse-Via": "mcp"}, json={"skills": []})  # 脚本、/mcp 替 Agent 发的
    assert r.status_code == 403 and r.json()["ok"] is False
    monkeypatch.setattr(main, "principal_of", lambda req: "loopback")  # 只靠本机免令牌进来的
    assert c.put("/api/setup/skills/main", json={"skills": []}).status_code == 403
    assert config.CONFIG_PATH.read_bytes() == before and not env.backups()  # 一个字没写
    monkeypatch.setattr(main, "principal_of", lambda req: "tailscale:my-phone")
    assert c.put("/api/setup/skills/main", json={"skills": ["journal"]}).status_code == 200 and len(env.backups()) == 1


def test_the_skill_list_api_does_not_write_for_other_claws(env):
    env.cfg(claw={"kind": "openclaw"})
    before = config.CONFIG_PATH.read_bytes()
    v = env.client.get("/api/setup/skills/main", headers=H).json()
    assert v["applies"] is False and "openclaw.json" in v["why"]
    assert env.client.put("/api/setup/skills/main", headers=H, json={"skills": []}).status_code == 409
    assert config.CONFIG_PATH.read_bytes() == before and not env.backups()


def test_appdata_helpers_format_money_and_never_touch_engine_tables_when_unused(env, monkeypatch, tmp_path):
    import sqlite3
    assert [appdata.money(x) for x in (0, 0.00004, 0.0021, 0.002, 0.0123, 0.5, 1.5)] == ["$0", "<$0.0001", "$0.0021", "$0.002", "$0.0123", "$0.50", "$1.50"]
    db = tmp_path / "fresh.db"
    sqlite3.connect(db).close()
    monkeypatch.setattr(config.settings, "db", db)
    assert appdata.engine_items(50, {}) == [] and not store.exists()  # 没用过内核：不建表
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0] == 0
