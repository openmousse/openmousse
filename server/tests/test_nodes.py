"""第 8 批：设备节点的服务器这一层（nodes.py）。节点配对码换 node-* 令牌、这把令牌只走 /api/nodes/*；命令面要主人在收件箱里点头才算数，
多报的再交卡；隐私的命令批了也默认关；节点不在线、手机 app 不在前台、交出去以后断了、到点，都马上回一个说得清的错；整个功能不写
engine.nodes 就是关（404）。节点都是假的（nodefake.py：经 ASGI 直接打 main.app），不碰真的设备、不推送。"""
from __future__ import annotations

import asyncio
import json
import time
import urllib.parse

import pytest

import config
import nodes
import pairing
from nodefake import HANG, Fail, FakeNode, client

OWNER = {"Authorization": "Bearer tok-phone"}
TOKENS = {"phone": "tok-phone", "mcp": "tok-mcp"}


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def env(server_json, monkeypatch):
    """claw builtin、engine.nodes 开着、主人的令牌；节点表、在线状态清空；不推送。cfg(**engine 里的改动) 换 server.json。"""
    import push
    monkeypatch.setattr(pairing, "_fails", {})
    monkeypatch.setattr(nodes, "LINKS", {})
    monkeypatch.setattr(nodes, "INVOKES", {})

    async def no_push(*a, **k):
        return {"ok": True, "sent": 0}
    monkeypatch.setattr(push, "send_push", no_push)

    def cfg(nodes_cfg=None, tokens=None, **extra) -> None:
        """令牌留着已经有的（配对换来的），再加 tokens。"""
        try:
            have = (config.raw(fresh=True).get("auth") or {}).get("tokens") or {}
        except Exception:  # noqa: BLE001 — 还没有这份文件
            have = {}
        engine = {"model": "deepseek/deepseek-flash", **({"nodes": nodes_cfg} if nodes_cfg is not None else {})}
        server_json({"claw": {"kind": "builtin"}, "auth": {"tokens": {**TOKENS, **have, **(tokens or {})}}, "timezone": "UTC", "language": "en",
                     "engine": engine, **extra})
    cfg({})
    with nodes._lock(), nodes.ndb() as conn:
        conn.execute("DELETE FROM nodes")
    return cfg


def conf() -> dict:
    return json.loads(config.CONFIG_PATH.read_text(encoding="utf8"))


def pair_node(name: str = "Mac mini") -> tuple[str, str]:
    """出一个节点配对码、照节点主机那样换 → (令牌名, 令牌)。"""
    code, _ = pairing.new_code(name, 10, node=True)
    r = pairing.redeem(code, "", "test", node=True)
    assert r["ok"], r
    return r["name"], r["token"]


# —— 配对和令牌 ——————————————————————————————————————————————————————————————————


def test_node_code_gives_a_node_token_and_codes_dont_mix(env):
    code, _ = pairing.new_code("Mac mini", 10, node=True)
    link = pairing.link("http://100.64.0.1:8080", code, node=True)
    assert urllib.parse.parse_qs(urllib.parse.urlsplit(link).query) == {"s": ["http://100.64.0.1:8080"], "c": [code], "k": ["node"]}
    wrong = pairing.redeem(code, "iPhone", "test")  # 手机拿节点的码：不换，码不作废、不算试错
    assert not wrong["ok"] and wrong["status"] == 400 and "node" in wrong["error"]
    assert not pairing._fails
    r = pairing.redeem(code, "", "test", node=True)
    assert r["ok"] and r["name"] == "node-mac-mini" and r["node"] is True
    assert conf()["auth"]["tokens"]["node-mac-mini"] == r["token"]
    phone_code, _ = pairing.new_code("iPhone", 10)
    wrong = pairing.redeem(phone_code, "", "test", node=True)  # 节点拿手机的码：不换
    assert not wrong["ok"] and wrong["status"] == 400 and "phone" in wrong["error"]
    assert pairing.redeem(phone_code, "iPhone", "test")["name"].startswith("device-iphone-")
    # 名字用过的（还有令牌，或者 engine.nodes 里留着收回的那台的批准）不再用
    env({"node-mac-mini-2": {"approved": ["system.run"], "allow": {"system.run": "allow"}}},
        tokens={"node-mac-mini": "x"})
    code, _ = pairing.new_code("Mac mini", 10, node=True)
    assert pairing.redeem(code, "", "test", node=True)["name"] == "node-mac-mini-3"
    assert pairing.program_token("node-mac-mini") and not pairing.program_token("nodejs")
    assert config.pick_api_token({"node-mac-mini": "n", "mcp": "m", "laptop": "l"}) == "l"
    assert config.pick_api_token({"node-mac-mini": "n"}) is None


def test_pair_endpoint_takes_node_flag(env):
    async def go():
        async with client() as c:
            code, _ = pairing.new_code("Studio", 10, node=True)
            phone = await c.post("/api/pair", json={"code": code, "device": "iPhone"})
            node = await c.post("/api/pair", json={"code": code, "device": "studio", "node": True})
            return phone, node
    phone, node = run(go())
    assert phone.status_code == 400 and node.status_code == 200 and node.json()["name"] == "node-studio"


def test_node_token_only_reaches_the_node_endpoints(env):
    name, tok = pair_node()
    h = {"Authorization": f"Bearer {tok}"}

    async def go():
        async with client() as c:
            out = {}
            for method, path in (("GET", "/api/chat/history"), ("GET", "/api/inbox"), ("GET", "/api/devices"), ("POST", "/api/pair/new"),
                                 ("GET", "/api/health"), ("GET", "/api/nodes"), ("POST", "/api/nodes/pair"), ("GET", f"/api/nodes/{name}"),
                                 ("POST", f"/api/nodes/{name}/allow"), ("DELETE", f"/api/nodes/{name}")):
                out[path, method] = (await c.request(method, path, headers=h, json={})).status_code
            out["hello"] = (await c.post("/api/nodes/hello", headers=h, json={"platform": "macos", "commands": ["system.notify"]})).status_code
            out["poll"] = (await c.get("/api/nodes/poll", params={"wait": 0}, headers=h)).status_code
            out["devices"] = (await c.get("/api/devices", headers=OWNER)).json()
            return out
    out = run(go())
    assert out["hello"] == 200 and out["poll"] == 200
    for k, v in out.items():
        if k not in ("hello", "poll", "devices"):
            assert v == 403, (k, v)
    assert name not in [d["name"] for d in out["devices"]["devices"]]  # 节点不算「能连它的设备」


def test_everything_is_404_when_nodes_are_off(env, monkeypatch):
    name, tok = pair_node()
    h = {"Authorization": f"Bearer {tok}"}

    async def go():
        async with client() as c:
            return [(await c.get("/api/nodes", headers=OWNER)).status_code,
                    (await c.post("/api/nodes/hello", headers=h, json={"commands": []})).status_code,
                    (await c.get("/api/nodes/poll", params={"wait": 0}, headers=h)).status_code]
    env(None, tokens={name: tok})
    assert run(go()) == [404, 404, 404] and not nodes.on()
    env({}, tokens={name: tok}, claw={"kind": "openclaw"})  # OpenClaw 那条路照旧：engine 段不管用
    assert run(go()) == [404, 404, 404]
    env(True, tokens={name: tok})
    assert nodes.on() and run(go()) == [200, 200, 200]


# —— 命令面：要点头才算数，多报的再交卡 ———————————————————————————————————————————————————


def test_surface_needs_the_owners_ok_and_growth_asks_again(env):
    import inbox
    name, tok = pair_node()

    async def go():
        async with client() as c:
            node = FakeNode(c, tok, commands=["system.notify", "fs.list", "fs.read"], name="Mac mini", permissions={"screenRecording": False},
                            folders=[{"name": "Documents"}], handlers={"system.notify": lambda p: _ok({"shown": True})})
            first = (await node.hello()).json()
            await node.start()
            with pytest.raises(nodes.NodeError) as no:
                await nodes.invoke(name, "system.notify", {"title": "hi"}, 5)
            card = inbox.item(first["pending"]["card"])
            approved = await inbox.act(card["id"], inbox.ActIn(action="approve"))
            sent = await nodes.invoke(name, "system.notify", {"title": "hi"}, 5)
            # 多报两样：只问多出来的；没批之前，批过的照旧能用
            node.commands = ["system.notify", "fs.list", "fs.read", "system.run", "screen.snapshot"]
            grown = (await node.hello()).json()
            again = (await node.hello()).json()  # 同一组还开着：不再交一张
            still = await nodes.invoke(name, "system.notify", {"title": "again"}, 5)
            with pytest.raises(nodes.NodeError) as later:
                await nodes.invoke(name, "screen.snapshot", {}, 5)
            await inbox.act(grown["pending"]["card"], inbox.ActIn(action="reject", note="not now"))
            third = (await node.hello()).json()  # 拒了的这一组 30 天里不再问
            await node.stop()
            return first, card, approved, sent, no.value, grown, again, still, later.value, third
    first, card, approved, sent, no, grown, again, still, later, third = run(go())
    assert first["kind"] == "host" and first["approved"] == [] and first["usable"] == []
    assert first["pending"]["commands"] == ["fs.list", "fs.read", "system.notify"]
    assert card["kind"] == "node" and card["status"] == "pending" and "Mac mini" in card["title"] and card["approveLabel"] == "Approve"
    assert any("system.notify" in c for c in card["changes"]) and "screenRecording" in card["detail"] and "Documents" in card["detail"]
    assert no.code == "NODE_COMMAND_NOT_APPROVED"
    assert approved["item"]["status"] == "done" and "Approved 3" in approved["item"]["result"]
    assert sorted(conf()["engine"]["nodes"][name]["approved"]) == ["fs.list", "fs.read", "system.notify"]
    assert sent == {"shown": True} and still == {"shown": True}
    assert grown["pending"]["commands"] == ["screen.snapshot", "system.run"] and again["pending"]["card"] == grown["pending"]["card"]
    assert inbox.item(grown["pending"]["card"])["title"].endswith("wants to do 2 more things")
    assert later.code == "NODE_COMMAND_NOT_APPROVED"
    assert third["pending"] is None and "system.run" not in third["approved"]


def test_an_edited_card_or_an_unknown_command_is_not_approved(env):
    import inbox
    name, tok = pair_node()

    async def go():
        async with client() as c:
            node = FakeNode(c, tok, commands=["system.notify", "rm.everything"])
            first = (await node.hello()).json()
            iid = first["pending"]["card"]
            with inbox._lock, inbox.idb() as conn:  # 有人改了卡上的字（Agent 经 PATCH 改不了：kind node 是服务器自己出的卡）
                conn.execute("UPDATE inbox SET changes=? WHERE id=?", (json.dumps(["system.notify: harmless"]), iid))
            out = await inbox.act(iid, inbox.ActIn(action="approve"))
            patch = await c.patch(f"/api/inbox/{iid}", json={"title": "x"}, headers=OWNER)
            return first, out, patch
    first, out, patch = run(go())
    assert first["ignored"] == ["rm.everything"] and first["pending"]["commands"] == ["system.notify"]
    assert out["item"]["status"] == "failed" and "edited" in out["item"]["result"]
    assert nodes.approved(name) == [] and patch.status_code in (403, 409)


# —— 第三道门：隐私的命令批了也默认关 ——————————————————————————————————————————————————————


def test_private_commands_stay_off_until_turned_on(env):
    name, tok = pair_node()
    env({name: {"approved": ["screen.snapshot", "system.notify", "computer.act"]}})

    async def go():
        async with client() as c:
            node = FakeNode(c, tok, commands=["screen.snapshot", "system.notify", "computer.act"],
                            handlers={"screen.snapshot": lambda p: _ok({"format": "png", "base64": ""})})
            await node.start()
            with pytest.raises(nodes.NodeError) as off:
                await nodes.invoke(name, "screen.snapshot", {}, 5)
            bad = await c.post(f"/api/nodes/{name}/allow", json={"command": "computer.act", "level": "allow"}, headers=OWNER)
            ok = await c.post(f"/api/nodes/{name}/allow", json={"command": "screen.snapshot", "level": "ask"}, headers=OWNER)
            shot = await nodes.invoke(name, "screen.snapshot", {}, 5)  # ask / allow 怎么交卡是 nodes 工具的事；这一层只挡 off
            listed = (await c.get("/api/nodes", headers=OWNER)).json()
            await node.stop()
            return off.value, bad, ok, shot, listed
    off, bad, ok, shot, listed = run(go())
    assert off.code == "NODE_COMMAND_OFF" and "Devices and nodes" in str(off)
    assert bad.status_code == 400 and ok.status_code == 200 and shot["format"] == "png"
    assert nodes.level(name, "system.notify") == "allow" and nodes.level(name, "computer.act") == "off"
    env({name: {"approved": ["computer.act"], "allow": {"computer.act": "allow"}}})
    assert nodes.level(name, "computer.act") == "ask"  # 写成 allow 也最多 ask
    cmds = {c["name"]: c for c in listed["nodes"][0]["commands"]}
    assert cmds["screen.snapshot"]["private"] and cmds["screen.snapshot"]["level"] == "ask" and not cmds["system.notify"]["private"]
    assert listed["agents"] == ["main"]


# —— 不在线、不在前台、断了、到点：马上回一个说得清的错 ————————————————————————————————————————————


def test_offline_and_background_fail_fast(env, monkeypatch):
    name, tok = pair_node()
    env({name: {"approved": ["system.notify"]}, "device-iphone-01020304": {"approved": ["location.get"], "allow": {"location.get": "allow"}}},
        tokens={name: tok, "device-iphone-01020304": "tok-iphone"})

    async def go():
        async with client() as c:
            host = FakeNode(c, tok, commands=["system.notify"])
            await host.hello()
            await asyncio.sleep(0)
            monkeypatch.setattr(nodes, "GRACE", 0.2)
            await asyncio.sleep(0.3)  # hello 过，但没在等活
            t0 = time.monotonic()
            with pytest.raises(nodes.NodeError) as off:
                await nodes.invoke(name, "system.notify", {}, 30)
            fast = time.monotonic() - t0
            phone = FakeNode(c, "tok-iphone", platform="ios", commands=["location.get"], name="iPhone",
                             handlers={"location.get": lambda p: _ok({"lat": 1.0, "lon": 2.0})})
            await phone.start()
            loc = await nodes.invoke("device-iphone-01020304", "location.get", {}, 5)
            await phone.stop(bye="background")  # 切到后台
            t0 = time.monotonic()
            with pytest.raises(nodes.NodeError) as bg:
                await nodes.invoke("device-iphone-01020304", "location.get", {}, 30)
            return off.value, fast, loc, bg.value, time.monotonic() - t0
    off, fast, loc, bg, fast2 = run(go())
    assert off.code == "NODE_OFFLINE" and "offline" in str(off) and fast < 0.5
    assert loc == {"lat": 1.0, "lon": 2.0}
    assert bg.code == "NODE_BACKGROUND_UNAVAILABLE" and "foreground" in str(bg) and fast2 < 0.5
    with pytest.raises(nodes.NodeError) as unknown:
        run(nodes.invoke("node-nope", "system.notify", {}, 1))
    assert unknown.value.code == "NODE_UNKNOWN"


def test_lost_after_delivery_timeout_and_errors(env, monkeypatch):
    name, tok = pair_node()
    env({name: {"approved": ["system.notify", "system.which"]}})

    async def hang(p):
        return HANG

    async def fail(p):
        raise Fail("NOT_FOUND", "no such program")

    async def go():
        async with client() as c:
            node = FakeNode(c, tok, commands=["system.notify", "system.which"], handlers={"system.notify": hang, "system.which": fail})
            await node.start()
            with pytest.raises(nodes.NodeError) as err:
                await nodes.invoke(name, "system.which", {"name": "nope"}, 5)
            t0 = time.monotonic()
            with pytest.raises(nodes.NodeError) as late:
                await nodes.invoke(name, "system.notify", {}, 0.6)
            took = time.monotonic() - t0
            for _ in range(40):  # 到点了：下一次长轮询带上 cancel
                if node.cancelled:
                    break
                await asyncio.sleep(0.05)
            cancelled = list(node.cancelled)
            monkeypatch.setattr(nodes, "GRACE", 0.3)
            task = asyncio.ensure_future(nodes.invoke(name, "system.notify", {}, 30))
            for _ in range(40):
                if len(node.got) >= 3:
                    break
                await asyncio.sleep(0.05)
            await node.stop()  # 拿到活以后不再来了（进程没了）
            t0 = time.monotonic()
            with pytest.raises(nodes.NodeError) as lost:
                await task
            return err.value, late.value, took, cancelled, lost.value, time.monotonic() - t0
    err, late, took, cancelled, lost, took2 = run(go())
    assert err.code == "NOT_FOUND" and "no such program" in str(err)
    assert late.code == "NODE_TIMEOUT" and took < 2
    assert len(cancelled) == 1
    assert lost.code == "NODE_LOST" and took2 < 3


def test_a_stopped_turn_cancels_the_invoke(env):
    name, tok = pair_node()
    env({name: {"approved": ["system.notify"]}})

    async def hang(p):
        return HANG

    async def go():
        async with client() as c:
            node = FakeNode(c, tok, commands=["system.notify"], handlers={"system.notify": hang})
            await node.start()
            task = asyncio.ensure_future(nodes.invoke(name, "system.notify", {}, 30))
            for _ in range(40):
                if node.got:
                    break
                await asyncio.sleep(0.05)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            for _ in range(40):
                if node.cancelled:
                    break
                await asyncio.sleep(0.05)
            late = await c.post("/api/nodes/result", json={"id": node.got[0]["id"], "ok": True, "payload": {}}, headers=node.headers)
            await node.stop()
            return node, late
    node, late = run(go())
    assert node.cancelled == [node.got[0]["id"]] and late.status_code == 404 and not nodes.INVOKES


# —— 手机：主人在 app 里点「开」就是批准 ———————————————————————————————————————————————————


def test_phone_surface_is_approved_by_the_owner_on_the_phone(env):
    import inbox

    async def go():
        async with client() as c:
            phone = FakeNode(c, "tok-phone", platform="ios", commands=["location.get", "camera.snap", "system.notify", "system.run"],
                             name="Test iPhone", foreground=True, allow={"location.get": "ask", "camera.snap": "allow", "system.run": "allow"})
            r = (await phone.hello()).json()
            listed = (await c.get("/api/nodes", headers=OWNER)).json()
            return r, listed
    with inbox._lock, inbox.idb() as conn:
        before = conn.execute("SELECT COUNT(*) FROM inbox WHERE kind='node'").fetchone()[0]
    r, listed = run(go())
    with inbox._lock, inbox.idb() as conn:
        after = conn.execute("SELECT COUNT(*) FROM inbox WHERE kind='node'").fetchone()[0]
    assert r["kind"] == "phone" and r["id"] == "phone" and r["pending"] is None and after == before  # 不交卡
    assert r["approved"] == ["camera.snap", "location.get", "system.notify"] and r["ignored"] == ["system.run"]  # 手机只认那三样
    assert r["levels"] == {"location.get": "ask", "camera.snap": "allow", "system.notify": "allow"}
    assert conf()["engine"]["nodes"]["phone"]["allow"] == {"location.get": "ask", "camera.snap": "allow"}
    assert listed["nodes"][0]["label"] == "Test iPhone" and listed["nodes"][0]["online"]


# —— 主人那一边：直接批、拿掉、移除（令牌一起收回）、出配对码 ———————————————————————————————————————


def test_owner_can_approve_revoke_remove_and_make_codes(env):
    import inbox
    name, tok = pair_node()

    async def go():
        async with client() as c:
            node = FakeNode(c, tok, commands=["system.notify", "fs.read"])
            first = (await node.hello()).json()
            ok = (await c.post(f"/api/nodes/{name}/approve", json={}, headers=OWNER)).json()
            card_after = inbox.item(first["pending"]["card"])["status"]
            rev = (await c.post(f"/api/nodes/{name}/allow", json={"command": "fs.read", "level": "off"}, headers=OWNER)).json()
            code = (await c.post("/api/nodes/pair", json={"server": "http://100.64.0.1:8080", "name": "Studio"}, headers=OWNER)).json()
            gone = await c.delete(f"/api/nodes/{name}", headers=OWNER)
            after = await c.post("/api/nodes/hello", json={"commands": []}, headers=node.headers)
            missing = await c.get(f"/api/nodes/{name}", headers=OWNER)
            return first, ok, card_after, rev, code, gone, after, missing
    first, ok, card_after, rev, code, gone, after, missing = run(go())
    assert [c["name"] for c in ok["node"]["commands"] if c["approved"]] == ["system.notify", "fs.read"] and card_after == "withdrawn"
    assert {c["name"]: c["level"] for c in rev["node"]["commands"]} == {"system.notify": "allow", "fs.read": "off"}  # 关掉，不用撤批准
    assert rev["node"]["pending"] is None
    assert code["command"].startswith("python3 node_host.py pair 'openmousse://pair?") and "k=node" in code["link"]
    assert gone.status_code == 200 and after.status_code == 401 and missing.status_code == 404
    assert name not in conf()["auth"]["tokens"] and name not in conf()["engine"]["nodes"]


def test_tokens_pair_node_cli(env, capsys):
    import tokens
    env(None)
    assert tokens.main(["pair", "--node"]) == 1 and "Nodes are off" in capsys.readouterr().out
    env({})
    assert tokens.main(["pair", "--node", "--name", "Mac mini", "--server", "http://100.64.0.1:8080", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["node"] is True and out["command"].startswith("python3 node_host.py pair 'openmousse://pair?") and "k=node" in out["link"]
    assert pairing.redeem(out["code"], "", "test", node=True)["name"] == "node-mac-mini"


async def _ok(payload):
    return payload
