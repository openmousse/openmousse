"""好友群（friendgroup.py，主线第三步第三段）：成员表由群主签、对不上一律拒；逐个成员投递（签名，能封就封进信封，直连不通换中继）；
不是我好友的成员凭成员表认得出；每个服务器只派一位管家、群主的管家主持；老版本只能一对一；好友的话不驱动工具。
别人的服务器是假的：测试里生一把钥匙当它（签名、端到端都用它），发出去的请求截下来看。人、地址和事都是编的。"""
from __future__ import annotations

import asyncio
import base64
import json
import secrets
import time

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from fastapi.testclient import TestClient

import cardagent
import chat
import friendgroup
import friends
import inbox
import public
import social
from engine import providers
from test_butler import ENGINE
from test_cybers import H, env, groups_clean, make  # noqa: F401 — 赛博那边的 fixture（builtin、假密钥）和建赛博的小函数
from test_groupchat import ByContent, said

ME_URL = "https://alex.example.com"


def run(coro):
    return asyncio.run(coro)


class Peer:
    """一台假的「别人的服务器」：自己的签名钥匙和端到端钥匙，地址是编的。"""

    def __init__(self, name: str, url: str, caps=("chat", "ask", "group")) -> None:
        self.key = Ed25519PrivateKey.generate()
        self.x = social.public_x(self.key)
        self.kid = social.thumbprint(self.x)
        self.enc_key = X25519PrivateKey.generate()
        self.enc = social.b64u(social.raw_x25519(self.enc_key.public_key()))
        self.name, self.url, self.caps = name, url, list(caps)

    def entry(self, butler: bool = False, relay: str = "") -> dict:
        return {"kid": self.kid, "x": self.x, "enc": self.enc, "url": self.url, "relay": relay, "name": self.name, "butler": butler}

    def befriend(self) -> dict:
        info = {"kid": self.kid, "x": self.x, "url": self.url, "name": self.name, "caps": self.caps, "a2a": None,
                "card": {"enc": {"kty": "OKP", "crv": "X25519", "x": self.enc}}, "enc": self.enc, "relay": None}
        return friends.upsert_friend(info, tier="friend", via="code")[0]

    def sign_roster(self, gid: str, title: str, members: list[dict], version: int = 1) -> dict:
        body = {"openmousse": "1", "group": gid, "title": title, "owner": self.kid, "version": version, "at": chat.now_iso(), "members": members}
        protected = social.b64u(json.dumps({"alg": "EdDSA", "kid": self.kid, "typ": "JOSE"}, separators=(",", ":")).encode())
        sig = self.key.sign(protected.encode() + b"." + social.b64u(social.jcs(body)).encode())
        return {**body, "signatures": [{"protected": protected, "signature": social.b64u(sig)}]}

    def headers(self, path: str, body: bytes) -> dict:
        """照第 5 节签一个发给我的请求。"""
        digest = social.content_digest(body)
        to = social.my_kid()
        params = social.sig_params(list(social.COMPONENTS), [("created", int(time.time())), ("nonce", social.b64u(secrets.token_bytes(16))),
                                                             ("keyid", self.kid), ("alg", "ed25519"), ("tag", social.SIG_TAG)])
        base = "\n".join([f'"@method": POST', f'"@path": {path}', f'"content-digest": {digest}', f'"mousse-to": {to}',  # noqa: F541
                          f'"@signature-params": {params}']).encode()
        sig = self.key.sign(base)
        return {"Content-Type": "application/json", "Content-Digest": digest, "Mousse-To": to, "Signature-Input": f"om={params}",
                "Signature": f"om=:{base64.b64encode(sig).decode()}:"}


@pytest.fixture
def fenv(env, monkeypatch):  # noqa: F811 — env 是导入的 fixture
    env.cfg(butler=True, user_name="Alex", engine=ENGINE, share={"public_url": ME_URL})
    with chat._lock, friendgroup.gdb() as conn:
        for t in ("friend_groups", "friend_group_msgs", "friend_group_out"):
            conn.execute(f"DELETE FROM {t}")  # noqa: S608
    with chat._lock, social.sdb() as conn:
        conn.execute("DELETE FROM friends")
    with chat._lock, cardagent.cdb() as conn:
        conn.execute("DELETE FROM card_log")
        conn.execute("DELETE FROM card_asks")
    with chat._lock, inbox.idb() as conn:
        conn.execute("DELETE FROM inbox WHERE kind='social'")  # 别的测试留下的社交卡：测试里按「等你点的那一张」找卡
    sent: list = []

    async def fake_post(url, payload, *, to_kid, headers=None, timeout=15.0, seal_to=None):
        sent.append({"url": url, "payload": payload, "to": to_kid, "seal": seal_to})
        code = env.codes.get(url.rsplit("/f/", 1)[0], 200)
        return httpx.Response(code, json={"ok": code == 200}, request=httpx.Request("POST", url))
    monkeypatch.setattr(social, "signed_post", fake_post)
    later: list = []
    monkeypatch.setattr(friendgroup, "run_on_main", later.append)  # 管家答话：截下来，测试里自己跑
    env.sent, env.codes, env.later = sent, {}, later
    env.pub = TestClient(public.app)
    return env


def post_group(e, peer: Peer, payload: dict):
    body = json.dumps(payload, ensure_ascii=False).encode()
    return e.pub.post("/f/group", content=body, headers=peer.headers("/f/group", body))


def deliver() -> None:
    run(friendgroup.deliver_due())


def test_off_by_default_and_the_card_says_group_only_when_on(fenv):
    assert "group" in social.card_body()["caps"]
    fenv.cfg(butler=None, user_name="Alex", engine=ENGINE, share={"public_url": ME_URL})
    assert fenv.client.get("/api/friendgroups", headers=H).status_code == 404
    assert "group" not in social.card_body()["caps"]
    assert fenv.pub.post("/f/group", content=b"{}").status_code == 404


def test_creating_signs_the_roster_and_queues_one_copy_per_member(fenv):
    mia, sam, old = Peer("Mia", "https://mia.example.com"), Peer("Sam", "https://sam.example.com"), Peer("Pat", "https://pat.example.com", caps=("chat", "ask"))
    fm, fs, fo = mia.befriend(), sam.befriend(), old.befriend()
    c = fenv.client
    cands = {x["name"]: x for x in c.get("/api/friendgroups/candidates", headers=H).json()["friends"]}
    assert cands["Mia"]["can"] and not cands["Pat"]["can"] and "one-to-one only" in cands["Pat"]["why"]  # 老版本：写明为什么
    r = c.post("/api/friendgroups", json={"title": "Weekend reading", "friends": [fm["id"], fo["id"]]}, headers=H)
    assert r.status_code == 409 and "too old" in r.json()["detail"]
    g = c.post("/api/friendgroups", json={"title": "Weekend reading", "friends": [fm["id"], fs["id"]]}, headers=H).json()["group"]
    assert g["mine"] and g["myButler"] and [m["name"] for m in g["members"]] == ["You", "Mia", "Sam"]
    assert [m["butler"] for m in g["members"]] == [True, False, False]  # 群主的管家主持；好友的管家由他们自己决定
    deliver()
    assert sorted(s["to"] for s in fenv.sent) == sorted([mia.kid, sam.kid]) and all(s["url"].endswith("/f/group") for s in fenv.sent)
    assert {s["to"]: s["seal"] for s in fenv.sent} == {mia.kid: mia.enc, sam.kid: sam.enc}  # 逐个封进各自的信封
    roster = fenv.sent[0]["payload"]["roster"]
    assert fenv.sent[0]["payload"]["type"] == "roster" and social.verify_jws(roster, social.identity()["x"])
    assert friendgroup.verify_roster(roster)["version"] == 1


def test_members_are_checked_against_the_owners_signed_roster(fenv):
    mia, sam, zed = Peer("Mia", "https://mia.example.com"), Peer("Sam", "https://sam.example.com"), Peer("Zed", "https://zed.example.com")
    mia.befriend()  # Sam 不是我的好友（是 Mia 的）；Zed 谁都不是
    me = {"kid": social.my_kid(), "x": social.identity()["x"], "enc": social.enc_x(), "url": ME_URL, "relay": "", "name": "Alex", "butler": False}
    gid = "fg-" + "1" * 16
    roster = mia.sign_roster(gid, "Board games", [mia.entry(butler=True), me, sam.entry()])
    assert post_group(fenv, mia, {"v": 1, "type": "roster", "roster": roster}).json() == {"ok": True, "version": 1}
    assert fenv.client.get(f"/api/friendgroups/{gid}", headers=H).json()["group"]["title"] == "Board games"
    msg = {"id": "a" * 32, "who": "person", "text": "Saturday at three?", "at": chat.now_iso()}
    ok = post_group(fenv, sam, {"v": 1, "type": "msg", "roster": roster, "msg": msg}).json()  # 不是我好友，凭成员表认得出
    assert ok == {"ok": True, "id": "a" * 32, "dup": False}
    assert post_group(fenv, sam, {"v": 1, "type": "msg", "roster": roster, "msg": msg}).json()["dup"] is True
    assert post_group(fenv, zed, {"v": 1, "type": "msg", "roster": roster, "msg": {**msg, "id": "b" * 32}}).status_code == 401  # 不在表上
    bad = {**roster, "title": "Changed after signing"}
    assert post_group(fenv, mia, {"v": 1, "type": "roster", "roster": bad}).json()["error"] == "bad_roster"  # 签名对不上：拒
    zg = zed.sign_roster("fg-" + "2" * 16, "Zed's", [zed.entry(), me])
    assert post_group(fenv, zed, {"v": 1, "type": "roster", "roster": zg}).json()["error"] == "bad_roster"  # 群主不是我的好友：拒
    assert post_group(fenv, sam, {"v": 1, "type": "msg", "roster": roster, "msg": {**msg, "id": "c" * 32, "who": "butler"}}).status_code == 403
    assert post_group(fenv, sam, {"v": 1, "type": "roster", "roster": roster}).json()["error"] == "not_owner"
    assert post_group(fenv, mia, {"v": 1, "type": "poll", "roster": roster}).json()["error"] == "bad_request"  # 不认识的 type
    msgs = fenv.client.get(f"/api/friendgroups/{gid}", headers=H).json()["messages"]
    assert [(m["name"], m["text"]) for m in msgs] == [("Sam", "Saturday at three?")]


def test_another_member_cant_take_the_group_over_with_their_own_roster(fenv):
    """群主是第一次存下时的那位：另一位好友拿同一个群 id、以自己为群主另签一份（版本给得很大、加了别人），单发、夹在消息里都不认。"""
    mia, pat, zed = Peer("Mia", "https://mia.example.com"), Peer("Pat", "https://pat.example.com"), Peer("Zed", "https://zed.example.com")
    mia.befriend()
    pat.befriend()
    me = {"kid": social.my_kid(), "x": social.identity()["x"], "enc": social.enc_x(), "url": ME_URL, "relay": "", "name": "Alex", "butler": False}
    gid = "fg-" + "3" * 16
    roster = mia.sign_roster(gid, "Board games", [mia.entry(butler=True), me, pat.entry()])
    assert post_group(fenv, mia, {"v": 1, "type": "roster", "roster": roster}).json() == {"ok": True, "version": 1}
    taken = pat.sign_roster(gid, "Board games", [pat.entry(), me, mia.entry(), zed.entry()], version=999)
    assert post_group(fenv, pat, {"v": 1, "type": "roster", "roster": taken}).json()["error"] == "not_owner"
    msg = {"id": "d" * 32, "who": "person", "text": "New list", "at": chat.now_iso()}
    assert post_group(fenv, pat, {"v": 1, "type": "msg", "roster": taken, "msg": msg}).json()["error"] == "not_owner"
    assert friendgroup.store_roster(friendgroup.verify_roster(taken)) is False
    g = fenv.client.get(f"/api/friendgroups/{gid}", headers=H).json()
    assert sorted(m["name"] for m in g["group"]["members"]) == sorted(["Mia", "You", "Pat"]) and g["messages"] == []  # 还是 Mia 那份


def test_sealed_envelopes_open_and_the_relay_is_the_fallback(fenv):
    mia, sam = Peer("Mia", "https://mia.example.com"), Peer("Sam", "https://sam.example.com")
    mia.befriend()
    me = {"kid": social.my_kid(), "x": social.identity()["x"], "enc": social.enc_x(), "url": ME_URL, "relay": "", "name": "Alex", "butler": False}
    gid = "fg-" + "3" * 16
    roster = mia.sign_roster(gid, "Hiking", [mia.entry(), me, sam.entry(relay="https://relay.example.com/u/sam")])
    body = json.dumps({"v": 1, "type": "roster", "roster": roster}).encode()
    inner = json.dumps({"m": "POST", "p": "/f/group", "h": mia.headers("/f/group", body), "b": social.b64u(body)}).encode()
    env_, k_res = social.seal_request(social.enc_x(), inner)
    r = fenv.pub.post("/f/sealed", content=env_, headers={"Content-Type": social.SEAL_TYPE})
    out = json.loads(social.open_response(k_res, r.content))
    assert out["s"] == 200 and json.loads(social.unb64u(out["b"]))["version"] == 1  # /f/group 能装进信封（SEALED_PATHS 多了这一条）
    fenv.codes["https://sam.example.com"] = 503  # Sam 的直连不通：换他的中继
    friendgroup.say(gid, "Bring snacks")
    deliver()
    to_sam = [s["url"] for s in fenv.sent if s["to"] == sam.kid]
    assert to_sam == ["https://sam.example.com/f/group", "https://relay.example.com/u/sam/f/group"]
    with chat._lock, friendgroup.gdb() as conn:
        assert {r["to_kid"]: r["status"] for r in conn.execute("SELECT * FROM friend_group_out")} == {mia.kid: "sent", sam.kid: "sent"}


def test_my_butler_answers_by_the_card_agent_rules_and_friends_drive_no_tools(fenv, monkeypatch):
    mia, sam = Peer("Mia", "https://mia.example.com"), Peer("Sam", "https://sam.example.com")
    fm, fs = mia.befriend(), sam.befriend()
    g = fenv.client.post("/api/friendgroups", json={"title": "Weekend reading", "friends": [fm["id"], fs["id"]]}, headers=H).json()["group"]
    deliver()
    fenv.sent.clear()
    roster = json.loads(friendgroup.group_row(chat.db(), g["id"])["roster"])

    def no_run(*a, **k):
        raise AssertionError("a friend's words started an owner turn")
    monkeypatch.setattr(chat, "start_run", no_run)
    f = ByContent([("Is Alex free on Friday evening?", {"text": json.dumps({"reply": "Alex is busy on Friday evening.", "used": [], "ask_owner": None,
                                                                            "declined": []})})])
    monkeypatch.setattr(providers, "stream", f.stream)
    msg = {"id": "d" * 32, "who": "person", "text": "@管家 Is Alex free on Friday evening?", "at": chat.now_iso()}
    assert post_group(fenv, mia, {"v": 1, "type": "msg", "roster": roster, "msg": msg}).json()["dup"] is False
    quiet = {"id": "e" * 32, "who": "person", "text": "Sounds good", "at": chat.now_iso()}
    post_group(fenv, sam, {"v": 1, "type": "msg", "roster": roster, "msg": quiet})
    assert len(fenv.later) == 1  # 只有叫它的那句
    run(fenv.later.pop())
    assert "You are the card agent of Alex" in f.requests[0][1].system and not f.requests[0][1].tools  # 名片 Agent 那一套，零工具
    assert '"message": "@管家 Is Alex free on Friday evening?"' in said(f.requests[0][1]) and '"name": "Mia"' in said(f.requests[0][1])  # 问的人是好友 Mia
    msgs = fenv.client.get(f"/api/friendgroups/{g['id']}", headers=H).json()["messages"]
    assert (msgs[-1]["who"], msgs[-1]["name"], msgs[-1]["text"]) == ("butler", "Alex's butler", "Alex is busy on Friday evening.")
    deliver()
    assert sorted(s["to"] for s in fenv.sent) == sorted([mia.kid, sam.kid]) and fenv.sent[0]["payload"]["msg"]["who"] == "butler"
    with chat._lock, cardagent.cdb() as conn:
        assert [r["channel"] for r in conn.execute("SELECT channel FROM card_log WHERE dir='out'")] == ["group"]  # 进「说过的话」


def test_the_whole_group_reads_my_butlers_answer_so_it_goes_at_the_tightest_tier(fenv, monkeypatch):
    """管家答的话全群都看得到：资料每一类按问的人和别的成员里最紧的那一级放，Doorman 也按这一份查；不是我好友的成员算陌生人。"""
    import sentinel
    mia, pat, sam = Peer("Mia", "https://mia.example.com"), Peer("Pat", "https://pat.example.com"), Peer("Sam", "https://sam.example.com")
    fm, fp = mia.befriend(), pat.befriend()
    with chat._lock, social.sdb() as conn:
        conn.execute("UPDATE friends SET tier='close' WHERE id=?", (fm["id"],))
        conn.execute("UPDATE friends SET tier='mate' WHERE id=?", (fp["id"],))
    seen: list = []
    real_build, real_review = cardagent.build_material, sentinel.review
    monkeypatch.setattr(cardagent, "build_material", lambda scope, lang, extra=None: seen.append(dict(scope)) or real_build(scope, lang, extra))

    async def review(peer, scope, *a, **k):
        seen.append(dict(scope))
        return await real_review(peer, scope, *a, **k)
    monkeypatch.setattr(sentinel, "review", review)
    reply = {"text": json.dumps({"reply": "Alex is busy that evening.", "used": [], "ask_owner": None, "declined": []})}
    monkeypatch.setattr(providers, "stream", ByContent([("You are the card agent of Alex", reply)] * 2).stream)
    # 我的群：close 的 Mia 问，mate 的 Pat 也在：按 mate 那一档（只有忙闲，没有日程的内容）
    g = fenv.client.post("/api/friendgroups", json={"title": "Weekend", "friends": [fm["id"], fp["id"]]}, headers=H).json()["group"]
    mine = json.loads(friendgroup.group_row(chat.db(), g["id"])["roster"])
    msg = {"id": "f" * 32, "who": "person", "text": "@管家 Is Alex free on Friday?", "at": chat.now_iso()}
    post_group(fenv, mia, {"v": 1, "type": "msg", "roster": mine, "msg": msg})
    run(fenv.later.pop())
    assert seen == [cardagent.scopes("mate")] * 2 and cardagent.scopes("close")["calendar"] == "detail" and seen[0]["calendar"] == "busy"
    # Mia 的群：我不认识的 Sam 也在：陌生人那一档
    seen.clear()
    me = {"kid": social.my_kid(), "x": social.identity()["x"], "enc": social.enc_x(), "url": ME_URL, "relay": "", "name": "Alex", "butler": True}
    hers = mia.sign_roster("fg-" + "4" * 16, "Board games", [mia.entry(butler=True), me, sam.entry()])
    assert post_group(fenv, mia, {"v": 1, "type": "roster", "roster": hers}).json()["ok"]
    msg = {"id": "9" * 32, "who": "person", "text": "@Alex's butler Is Alex free on Friday?", "at": chat.now_iso()}
    post_group(fenv, mia, {"v": 1, "type": "msg", "roster": hers, "msg": msg})
    run(fenv.later.pop())
    assert seen == [cardagent.scopes("stranger")] * 2


def test_asked_for_a_view_my_butler_consults_a_cyber_with_only_the_gist(fenv, monkeypatch):
    fenv.cfg(butler=True, user_name="Alex", engine=ENGINE, share={"public_url": ME_URL}, cybers=True)
    v = make(fenv, "Victor", tag="Early investor", identity="An early-stage investor.")["id"]
    fenv.client.patch("/api/butler", json={"cybers": [v]}, headers=H)
    mia, sam = Peer("Mia", "https://mia.example.com"), Peer("Sam", "https://sam.example.com")
    fm, fs = mia.befriend(), sam.befriend()
    g = fenv.client.post("/api/friendgroups", json={"title": "Weekend reading", "friends": [fm["id"], fs["id"]]}, headers=H).json()["group"]
    roster = json.loads(friendgroup.group_row(chat.db(), g["id"])["roster"])
    f = ByContent([("Turn it into the gist", {"text": json.dumps({"gist": "Whether to open-source a sync library"})}),
                   ("You are Victor", {"text": "Open core is the safer bet."}),
                   ("You are the card agent of Alex", {"text": json.dumps({"reply": "Open core is the safer bet.", "used": [f"view:{v}"],
                                                                           "ask_owner": None, "declined": []})})])
    monkeypatch.setattr(providers, "stream", f.stream)
    q = "@管家 What do you think, should Mia open-source the sync library before the reading group?"
    post_group(fenv, mia, {"v": 1, "type": "msg", "roster": roster, "msg": {"id": "9" * 32, "who": "person", "text": q, "at": chat.now_iso()}})
    run(fenv.later.pop())
    cyber_saw = said(f.requests[1][1])
    assert "sync library" in cyber_saw and "Mia" not in cyber_saw and "reading group" not in cyber_saw  # 赛博只拿到要点
    out = fenv.client.get(f"/api/friendgroups/{g['id']}", headers=H).json()
    last = out["messages"][-1]
    assert last["text"] == "Open core is the safer bet.\n(Victor's view)" and last["view"]["name"] == "Victor"  # 写明是谁的看法
    assert out["consults"][0]["name"] == "Victor"  # 只给你看：问了哪位赛博
    deliver()
    assert all(s["payload"]["msg"]["text"].endswith("(Victor's view)") for s in fenv.sent if s["payload"]["type"] == "msg")
    assert all("consult" not in json.dumps(s["payload"]) for s in fenv.sent)  # 问了谁、要点不出这台服务器


def test_the_app_gets_my_cards_unread_counts_and_only_newer_lines(fenv, monkeypatch):
    mia, sam = Peer("Mia", "https://mia.example.com"), Peer("Sam", "https://sam.example.com")
    fm, fs = mia.befriend(), sam.befriend()
    assert "friendgroups" in fenv.client.get("/api/health", headers=H).json()["server"]["features"]
    g = fenv.client.post("/api/friendgroups", json={"title": "Weekend reading", "friends": [fm["id"], fs["id"]]}, headers=H).json()["group"]
    roster = json.loads(friendgroup.group_row(chat.db(), g["id"])["roster"])
    ask = {"kind": "decision", "summary": "Dinner on Friday at 7pm",
           "proposal": {"what": "dinner", "date": "2026-11-06", "start": "19:00", "end": "", "place": ""}}
    f = ByContent([("Dinner on Friday at 7pm?", {"text": json.dumps({"reply": "I'll check with Alex.", "used": [], "ask_owner": ask, "declined": []})})])
    monkeypatch.setattr(providers, "stream", f.stream)
    post_group(fenv, mia, {"v": 1, "type": "msg", "roster": roster, "msg": {"id": "7" * 32, "who": "person", "text": "@管家 Dinner on Friday at 7pm?",
                                                                             "at": chat.now_iso()}})
    assert fenv.client.get("/api/friendgroups", headers=H).json()["groups"][0]["unread"] == 1  # 别人发来、我还没看
    run(fenv.later.pop())
    out = fenv.client.get(f"/api/friendgroups/{g['id']}", headers=H).json()
    reply = out["messages"][-1]
    assert reply["who"] == "butler" and reply["card"] and out["cards"][0]["id"] == reply["card"] and out["cards"][0]["groupMessage"] == reply["id"]
    assert out["cards"][0]["kind"] == "social" and out["cards"][0]["status"] == "pending"  # 只给我的卡：收件箱那一张
    deliver()
    assert all("card" not in s["payload"]["msg"] for s in fenv.sent if s["payload"]["type"] == "msg")  # 卡不出这台服务器
    newer = fenv.client.get(f"/api/friendgroups/{g['id']}?after={out['messages'][0]['id']}", headers=H).json()["messages"]
    assert [m["id"] for m in newer] == [m["id"] for m in out["messages"][1:]]  # 只要这条之后的
    assert fenv.client.post(f"/api/friendgroups/{g['id']}/read", json={"upto": reply["id"]}, headers=H).json() == {"ok": True}
    assert fenv.client.get("/api/friendgroups", headers=H).json()["groups"][0]["unread"] == 0


def test_a_members_butler_joins_only_through_the_owner(fenv):
    mia, sam = Peer("Mia", "https://mia.example.com"), Peer("Sam", "https://sam.example.com")
    fm, fs = mia.befriend(), sam.befriend()
    g = fenv.client.post("/api/friendgroups", json={"title": "Weekend reading", "friends": [fm["id"], fs["id"]]}, headers=H).json()["group"]
    deliver()
    fenv.sent.clear()
    roster = json.loads(friendgroup.group_row(chat.db(), g["id"])["roster"])
    assert post_group(fenv, mia, {"v": 1, "type": "butler", "roster": roster, "butler": True}).json() == {"ok": True, "version": 2}
    deliver()
    new = fenv.sent[0]["payload"]["roster"]
    assert fenv.sent[0]["payload"]["type"] == "roster" and new["version"] == 2 and {m["kid"]: m["butler"] for m in new["members"]}[mia.kid] is True
    fenv.sent.clear()
    assert post_group(fenv, mia, {"v": 1, "type": "butler", "roster": new, "butler": True}).json() == {"ok": True, "version": 2}  # 没变：不重签
    deliver()
    assert fenv.sent == []  # 也不群发
    msg = {"id": "f" * 32, "who": "butler", "text": "Mia's butler here: Mia can do Sunday.", "at": chat.now_iso()}
    assert post_group(fenv, mia, {"v": 1, "type": "msg", "roster": new, "msg": msg}).json()["dup"] is False
    names = [m["name"] for m in fenv.client.get(f"/api/friendgroups/{g['id']}", headers=H).json()["messages"]]
    assert names == ["Mia's butler"]  # 名字照成员表拼：发的人改不了
    assert fenv.client.patch(f"/api/friendgroups/{g['id']}", json={"butler": False}, headers=H).status_code == 409  # 群主的管家一直在


def test_a_lines_time_is_a_short_iso_time_and_nothing_goes_out_while_off(fenv):
    """消息上对方报的发送时间只认短的、带时区的 ISO 时间，别的用收到的时间；好友群关着时排着的不送，开回来再送。"""
    from datetime import datetime
    mia, sam = Peer("Mia", "https://mia.example.com"), Peer("Sam", "https://sam.example.com")
    fm, fs = mia.befriend(), sam.befriend()
    g = fenv.client.post("/api/friendgroups", json={"title": "Weekend reading", "friends": [fm["id"], fs["id"]]}, headers=H).json()["group"]
    roster = json.loads(friendgroup.group_row(chat.db(), g["id"])["roster"])
    for i, at in enumerate(["2026-10-21T09:30:00+00:00", "x" * 5000, "2026-10-21T09:30:00", "yesterday"]):
        msg = {"id": str(i + 1) * 32, "who": "person", "text": f"Line {i}", "at": at}
        assert post_group(fenv, mia, {"v": 1, "type": "msg", "roster": roster, "msg": msg}).json()["dup"] is False
    got = [m["at"] for m in fenv.client.get(f"/api/friendgroups/{g['id']}", headers=H).json()["messages"]]
    assert got[0] == "2026-10-21T09:30:00+00:00" and len(got) == 4
    assert all(a != "2026-10-21T09:30:00" and len(a) <= 40 and datetime.fromisoformat(a).tzinfo is not None for a in got[1:])
    fenv.cfg(user_name="Alex", engine=ENGINE, share={"public_url": ME_URL})  # 管家关了：好友群跟着关
    deliver()
    assert fenv.sent == []
    fenv.cfg(butler=True, user_name="Alex", engine=ENGINE, share={"public_url": ME_URL})
    deliver()
    assert {x["to"] for x in fenv.sent if x["payload"]["type"] == "roster"} == {mia.kid, sam.kid}


def test_before_asking_a_cyber_the_butler_checks_the_daily_limit_and_hides_every_members_name(fenv, monkeypatch):
    """请教赛博之前先看名片 Agent 今天的上限（到了不理要点、不问赛博）；要点里成员表上的名字都藏掉，不是我好友的也算。"""
    import butler
    fenv.cfg(butler=True, user_name="Alex", engine=ENGINE, share={"public_url": ME_URL}, cybers=True)
    v = make(fenv, "Victor", tag="Early investor", identity="An early-stage investor.")["id"]
    fenv.client.patch("/api/butler", json={"cybers": [v]}, headers=H)
    mia, sam = Peer("Mia", "https://mia.example.com"), Peer("Sam", "https://sam.example.com")
    mia.befriend()  # Sam 不是我的好友
    me = {"kid": social.my_kid(), "x": social.identity()["x"], "enc": social.enc_x(), "url": ME_URL, "relay": "", "name": "Alex", "butler": True}
    hers = mia.sign_roster("fg-" + "5" * 16, "Board games", [mia.entry(butler=True), me, sam.entry()])
    assert post_group(fenv, mia, {"v": 1, "type": "roster", "roster": hers}).json()["ok"]
    card = {"text": json.dumps({"reply": "Open core is the safer bet.", "used": [f"view:{v}"], "ask_owner": None, "declined": []})}
    f = ByContent([("Turn it into the gist", {"text": json.dumps({"gist": "Whether Sam should open-source a sync library"})}),
                   ("You are Victor", {"text": "Open core is the safer bet."}), ("You are the card agent of Alex", card)])
    monkeypatch.setattr(providers, "stream", f.stream)
    q = "@Alex's butler What do you think, should Sam open-source the sync library?"
    post_group(fenv, mia, {"v": 1, "type": "msg", "roster": hers, "msg": {"id": "8" * 32, "who": "person", "text": q, "at": chat.now_iso()}})
    run(fenv.later.pop())
    cyber_saw = said(f.requests[1][1])
    assert "sync library" in cyber_saw and "Sam" not in cyber_saw
    fenv.cfg(butler=True, user_name="Alex", engine=ENGINE, share={"public_url": ME_URL}, cybers=True, card={"limits": {"in_per_day": {"friend": 1}}})
    f.requests.clear()
    post_group(fenv, mia, {"v": 1, "type": "msg", "roster": hers, "msg": {"id": "7" * 32, "who": "person", "text": q, "at": chat.now_iso()}})
    run(fenv.later.pop())
    assert f.requests == []  # Mia 今天问满了：不理要点、不问赛博，也不调名片 Agent
    assert butler.consults(ref=f"fg:{hers['group']}")["items"][0]["gist"] == "Whether they should open-source a sync library"


def test_friends_lines_only_go_to_held_memory(fenv, monkeypatch):
    import groupchat
    from engine import writeback
    mia = Peer("Mia", "https://mia.example.com")
    fm = mia.befriend()
    sam = Peer("Sam", "https://sam.example.com")
    fs = sam.befriend()
    g = fenv.client.post("/api/friendgroups", json={"title": "Weekend reading", "friends": [fm["id"], fs["id"]]}, headers=H).json()["group"]
    roster = json.loads(friendgroup.group_row(chat.db(), g["id"])["roster"])
    post_group(fenv, mia, {"v": 1, "type": "msg", "roster": roster, "msg": {"id": "1" * 32, "who": "person", "text": "I'm moving to Berlin next month",
                                                                             "at": chat.now_iso()}})
    friendgroup.say(g["id"], "Congrats!")
    friendgroup.say(g["id"], "Mia's moving.", "butler")
    got: list = []

    async def fake_hold(msgs, **kw):
        got.append((msgs, kw))
        return [], 0
    monkeypatch.setattr(groupchat, "hold_lines", fake_hold)
    monkeypatch.setattr(writeback, "enabled", lambda: True)
    monkeypatch.setattr(writeback, "words", lambda text: 99)
    run(friendgroup.hold_pass(g["id"]))
    msgs, kw = got[0]
    assert [(m["who"], m["speaker"] == "") for m in msgs] == [("Mia", False), ("You", True)]  # 谁的管家说的不算；我的话只当上下文
    assert kw["via"] == 'friend group "Weekend reading"' and kw["prefix"] == "fg"
    run(friendgroup.hold_pass(g["id"]))
    assert len(got) == 1  # 游标往前挪了：同一段不再挑
