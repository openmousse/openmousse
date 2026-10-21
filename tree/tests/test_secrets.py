"""密钥：每种形状一个正例，容易误伤的反例，写入（SQLite / Markdown / 命令行 / MCP）和导出都过一遍。

样本都是编的，不是哪一家发过的真密钥。
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import random
import sqlite3
import string
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from openmousse_tree import config as C
from openmousse_tree import secrets as K
from openmousse_tree import store as S

# 每条都带数字，长度够得上那条规则。编的。
NOTION = "ntn_aB3dEf7hIj9kLm1nOp2qRs4tUv6wXy8z"
NOTION_SECRET = "secret_aB3dEf7hIj9kLm1nOp2qRs4tUv6wXy8z"
PPLX = "pplx-aB3dEf7hIj9kLm1nOp2qRs4tUv6wXy8z"
OPENAI = "sk-aB3dEf7hIj9kLm1nOp2qRs4tUv6wXy8z"
OPENAI_PROJ = "sk-proj-aB3dEf7hIj9kLm1nOp2qRs4tUv6wXy8zA0"
ANTHROPIC = "sk-ant-aB3dEf7hIj9kLm1nOp2qRs4tUv6wXy8zA0"
GH_PAT = "github_pat_11ABCDEFG0abcdef12345678_1234567890abcdef"
GH_GHP = "ghp_aB3dEf7hIj9kLm1nOp2qRs4tUv6wXy8z"
GH_GHO = "gho_aB3dEf7hIj9kLm1nOp2qRs4tUv6wXy8z"
XAI = "xai-aB3dEf7hIj9kLm1nOp2qRs4tUv6wXy8z"
GOOGLE = "AIzaSyA0b1C2d3E4f5G6h7I8j9K0l1M2n3O4"
SLACK = "xoxb-123456789012-123456789012-AbCdEfGhIjKlMnOp"
AWS = "AKIA3EX4MPL3KEY7X9Z2"
STRIPE = "sk_live_aB3dEf7hIj9kLm1n"
STRIPE_RK = "rk_live_aB3dEf7hIj9kLm1nOp2q"
ENTROPY = "mK7qP9xL2vN8rT4wZ1cB6yH5jF0sDaE3"
UUID = "550e8400-e29b-41d4-a716-446655440000"
GIT_SHA = "4a7c1e2b9d0f3a6c8e1b2d4f5a7c9e0b1d2f3a4b"
PEM = "-----BEGIN PRIVATE KEY-----\nMIIBfakeKeyMaterial12ABCxyz==\n-----END PRIVATE KEY-----"
PEM_LONG = "-----BEGIN RSA PRIVATE KEY-----\nABCDEFGHIJKLMNOPQRSTUVWXYZ012345\n-----END RSA PRIVATE KEY-----"
URL = "https://hooks.example.com/in?token=n0tArealToken99ab"
# 提交号、uuid4().hex、对话网址：用真的哈希算出来，熵和真的一样（"ab" * 32 这种连改之前的代码都认不出来，测不到东西）
SHA256 = hashlib.sha256(b"fixture").hexdigest()
SHA1 = hashlib.sha1(b"fixture").hexdigest()
HEX32 = hashlib.md5(b"fixture").hexdigest()
CHAT = str(uuid.UUID(hashlib.md5(b"chat").hexdigest()))
A2A = "a2a:" + hashlib.md5(b"thread").hexdigest()


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _jwt() -> str:
    head = _b64(b'{"alg":"HS256","typ":"JWT"}')
    body = _b64(b'{"sub":"demo","room":"north"}')
    sig = _b64(b"not-a-real-signature-0123456789")
    return f"{head}.{body}.{sig}"


PREFIXES = {
    "notion": NOTION,
    "notion-secret": NOTION_SECRET,
    "perplexity": PPLX,
    "openai": OPENAI,
    "openai-proj": OPENAI_PROJ,
    "anthropic": ANTHROPIC,
    "github-pat": GH_PAT,
    "github-ghp": GH_GHP,
    "github-gho": GH_GHO,
    "xai": XAI,
    "google": GOOGLE,
    "slack": SLACK,
    "aws": AWS,
    "stripe": STRIPE,
    "stripe-rk": STRIPE_RK,
}


def _flat(got) -> str:
    if isinstance(got, str):
        return got
    if isinstance(got, dict):
        return json.dumps(got, ensure_ascii=False)
    parts = []
    for block in got:
        parts.append(getattr(block, "text", None) or str(block))
    return "\n".join(parts)


@pytest.mark.parametrize("name", list(PREFIXES))
def test_vendor_prefix_is_redacted(name):
    secret = PREFIXES[name]
    out, n = K.redact(f"Mira left {secret} on a card")
    assert n == 1 and secret not in out and ("‹redacted›" in out or "‹已隐去›" in out)
    again, n2 = K.redact(out)
    assert n2 == 0 and again == out


def test_each_shape_once():
    jwt = _jwt()
    assert jwt.startswith("eyJ") and jwt.count(".") == 2
    cases = {
        "jwt": f"session {jwt} ended",
        "pem": f"block:\n{PEM}\nend",
        "url": f"See {URL} please",
        "entropy": f"the code is {ENTROPY} today",
        "uuid-beside-key": f"The api key is {UUID}",
        "uuid-beside-chinese": f"令牌是{UUID}",
    }
    for secret, text in ((jwt, cases["jwt"]), (PEM, cases["pem"]), ("n0tArealToken99ab", cases["url"]),
                         (ENTROPY, cases["entropy"]), (UUID, cases["uuid-beside-key"]), (UUID, cases["uuid-beside-chinese"])):
        out, n = K.redact(text)
        assert n == 1 and secret not in out, text
    url_out, _ = K.redact(cases["url"])
    assert "token=" in url_out and "hooks.example.com" in url_out  # 只换参数值，网址还在


def test_overlapping_detectors_count_as_one_place():
    out, n = K.redact(f"Mira pasted\n{PEM_LONG}\n")
    assert n == 1 and "ABCDEFGHIJKLMNOPQRSTUVWXYZ012345" not in out and "PRIVATE KEY" not in out
    assert len(OPENAI_PROJ) >= 32
    out, n = K.redact(f"proj {OPENAI_PROJ}")
    assert n == 1 and OPENAI_PROJ not in out


def test_two_secrets_count_as_two():
    out, n = K.redact(f"{NOTION} and {PPLX}")
    assert n == 2 and NOTION not in out and PPLX not in out


@pytest.mark.parametrize("text", [
    f"id {UUID} is a shelf label",
    f"The secretary's id is {UUID}",
    f"keyboard layout {UUID}",
    f"landed {GIT_SHA} today",
    "pneumonoultramicroscopicsilicovolcanoconiosis is a long word",
    "https://notes.example.com/week/three",
    "https://example.com/docs/getting-started?page=3",
    "Alex rows twice a week",
    "Sam is vegetarian now",
    "sort(key=lambda row: row.name)",
])
def test_ordinary_text_is_left_alone(text):
    assert len(GIT_SHA) == 40
    out, n = K.redact(text)
    assert n == 0 and out == text


def test_a_keyword_far_from_a_uuid_does_not_count():
    out, n = K.redact(f"key {'x' * 50}{UUID}")
    assert n == 0 and out.endswith(UUID)


@pytest.mark.parametrize("text", [
    f"the fix landed in {SHA256}",
    f"landed {SHA256.upper()} today",
    f"token {'word ' * 10}{SHA256}",  # key 字样在 40 字以外
    f"see code.example.com/acme/repo/commit/{SHA1}",
    f"https://www.notion.so/acme/Plan-{HEX32}",
    f"https://claude.ai/chat/{CHAT}",
    f"thread {A2A}",
])
def test_hex_ids_without_a_key_word_nearby_stay(text):
    out, n = K.redact(text)
    assert n == 0 and out == text


@pytest.mark.parametrize("text,secret", [
    (f"token {SHA256}", SHA256),
    (f"SECRET: {SHA256.upper()}", SHA256.upper()),
    (f"密钥是{SHA256}", SHA256),
    (f"the token is {SHA1}", SHA1),
    (f"password {HEX32}", HEX32),
    (f"Authorization: Bearer {HEX32}", HEX32),
])
def test_hex_ids_next_to_a_key_word_go(text, secret):
    out, n = K.redact(text)
    assert n == 1 and secret not in out


@pytest.mark.parametrize("text", [
    "https://code.example.com/acme-labs/thesis-experiments/pull/123",
    "https://www.example.ac.uk/study/postgraduate/msc-business-analytics-2026",
    "/home/alex/projects/openmousse-notes/2026-10-21/summary.md",
    "snake_case_variable_name_v2_final_backup",
    "London-Heathrow-to-JFK-terminal-5",
    "fal/fal-ai/kling-video/v2/master/text-to-video",
    "backup-2026-10-21T0315-openmousse.tar.gz",
    "files/data/uploads/2026-10/photo.jpg",
])
def test_paths_slugs_and_identifiers_stay(text):
    out, n = K.redact(text)
    assert n == 0 and out == text


def test_a_secret_segment_in_a_path_still_goes():
    rnd = random.Random(7)
    seg = "".join(rnd.choice(string.ascii_letters + string.digits) for _ in range(24))
    out, n = K.redact(f"hook https://hooks.example.com/services/T0AB1CD2E/B0FG3HI4J/{seg} posted")
    assert n == 1 and seg not in out and out.startswith("hook https://hooks.example.")


def test_random_tokens_are_still_caught():
    """路径和 slug 放过了，随机串照样认：三种字母表、32–40 位各 200 个，至少 98% 换掉。"""
    rnd = random.Random(0)
    alphabets = (string.ascii_letters + string.digits, string.ascii_letters + string.digits + "+/",
                 string.ascii_letters + string.digits + "-_")
    for chars in alphabets:
        hit = 0
        for _ in range(200):
            tok = "".join(rnd.choice(chars) for _ in range(rnd.randint(32, 40)))
            if not any(c.isdigit() for c in tok):
                tok = tok[:-1] + "7"
            hit += K.redact(f"code {tok} today")[1]
        assert hit >= 196, chars


def test_long_runs_stay_fast():
    """一长串 base64url、a- 交替、没有 END 的 BEGIN、一长串小写字母加数字：每样都在 2 秒内（改之前有的要几秒到几分钟）。"""
    rnd = random.Random(1)
    letters = "".join(rnd.choice(string.ascii_lowercase) for _ in range(3000))
    for text in (_b64(rnd.randbytes(150_000)), "a-" * 20_000, "-----BEGIN PRIVATE KEY-----\n" * 500 + "x" * 50_000,
                 letters + "12345x"):
        t = time.monotonic()
        K.redact(text)
        assert time.monotonic() - t < 2, text[:20]


def test_note_and_count_follow_the_language(tree_home):
    tree_home.make(lang="zh")
    assert K.note(2) == "隐去过 2 处"
    assert K.count_of("隐去过 2 处") == 2 and K.count_of(True) == 0 and K.count_of("") == 0
    tree_home.make(lang="en")
    assert K.note(2) == "redacted 2" and K.count_of("redacted 2") == 2 and K.count_of(4) == 4


@pytest.mark.parametrize("storage", ["sqlite", "markdown"])
def test_add_stores_the_placeholder_and_the_count(tree_home, storage):
    zh = storage == "markdown"
    tree_home.make(storage, lang="zh" if zh else "en")
    conn = S.connect()
    r = S.add(conn, text=f"Mira wrote {NOTION} on a card", source="claude", kind="fact", quote=f"it was {NOTION}",
              tags=f"note,{NOTION}", cues=[f"the {NOTION} card"])
    assert r["redacted"] >= 4
    row = dict(conn.execute("SELECT text, quote, tags, cues, redacted FROM tree WHERE id=?", (r["id"],)).fetchone())
    blob = " ".join(row[k] for k in ("text", "quote", "tags", "cues"))
    assert NOTION not in blob and row["redacted"] >= 4
    assert ("‹已隐去›" if zh else "‹redacted›") in row["text"]
    plain = S.add(conn, text="Alex rows twice a week", source="claude")
    assert "redacted" not in plain
    assert conn.execute("SELECT redacted FROM tree WHERE id=?", (plain["id"],)).fetchone()["redacted"] == 0
    if storage == "markdown":
        path = tree_home.vault / conn.execute("SELECT path FROM tree WHERE id=?", (r["id"],)).fetchone()["path"]
        raw = path.read_text(encoding="utf8")
        assert NOTION not in raw and "隐去过" in raw


def test_inherited_cues_are_redacted_and_the_old_leaf_stays(tree_home):
    tree_home.make()
    conn = S.connect()
    conn.execute(
        "INSERT INTO tree(id, text, source, observed_at, created_at, updated_at, cues) VALUES (?,?,?,?,?,?,?)",
        ("oldcue000001", "Mira kept a spare key in the drawer", "claude", "2026-04-01",
         "2026-04-01T00:00:00+00:00", "2026-04-01T00:00:00+00:00", ENTROPY),
    )
    conn.commit()
    r = S.add(conn, text="Mira moved the spare key to a new drawer", source="claude", supersedes="oldcue000001")
    got = conn.execute("SELECT cues, redacted FROM tree WHERE id=?", (r["id"],)).fetchone()
    old = conn.execute("SELECT cues, status FROM tree WHERE id='oldcue000001'").fetchone()
    assert ENTROPY not in got["cues"] and got["redacted"] >= 1
    assert ENTROPY in old["cues"] and old["status"] == "superseded"


def test_export_redacts_an_old_leaf_without_rewriting_it(tree_home):
    tree_home.make()
    conn = S.connect()
    conn.execute(
        "INSERT INTO tree(id, text, kind, tags, source, observed_at, status, created_at, updated_at, cues) "
        "VALUES ('oldsecret01', ?, 'fact', '', 'claude', '2026-03-01', 'active', '2026-03-01T00:00:00+00:00', "
        "'2026-03-01T00:00:00+00:00', ?)",
        (f"Mira left {OPENAI} in a drawer", f"ask about {OPENAI}"),
    )
    conn.commit()
    path = S.export(conn)
    body = path.read_text(encoding="utf8")
    assert OPENAI not in body and "‹redacted›" in body
    assert OPENAI in conn.execute("SELECT text FROM tree WHERE id='oldsecret01'").fetchone()["text"]
    assert S.export(conn).read_text(encoding="utf8") == body  # 摘要没变：不重写


def test_export_does_not_rewrite_when_nothing_is_secret(tree_home):
    tree_home.make()
    conn = S.connect()
    S.add(conn, text="Alex rows twice a week", source="claude")
    path = S.export(conn)  # 没有密钥：摘要按原文算，和改算法之前一样
    path.write_text(path.read_text(encoding="utf8") + "\n<!-- marker -->\n", encoding="utf8")
    S.export(conn)
    assert "marker" in path.read_text(encoding="utf8")


def test_profile_bundle_redacts_the_copy_not_the_file(tree_home):
    tree_home.make()
    S.save_profile(f"## About\n- Mira keeps {STRIPE} offline\n")
    conn = S.connect()
    bundle = S.profile_bundle(conn)
    assert STRIPE not in bundle and "‹redacted›" in bundle
    assert STRIPE in S.profile_text()


def test_cli_add_redacts(tree_home):
    tree_home.make()
    proc = subprocess.run(
        [sys.executable, "-m", "openmousse_tree.cli", "add", "--text", f"Mira saved {GH_GHP} for the demo", "--source", "claude"],
        capture_output=True, text=True, check=False,
        env={**os.environ, "MOUSSE_TREE_HOME": str(tree_home.home), "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
    )
    assert proc.returncode == 0, proc.stderr
    got = json.loads(proc.stdout.strip().splitlines()[-1])
    assert got["redacted"] >= 1
    row = S.connect().execute("SELECT text FROM tree WHERE id=?", (got["id"],)).fetchone()
    assert GH_GHP not in row["text"] and "‹redacted›" in row["text"]


def test_mcp_remember_redacts_and_exports(tree_home):
    tree_home.make()
    from openmousse_tree.server import platform_server
    mcp = platform_server("claude", C.load())
    said = _flat(asyncio.run(mcp.call_tool("remember", {"text": f"Mira stored {GH_PAT} for the demo", "kind": "fact"})))
    assert GH_PAT not in said and "redacted" in said
    conn = S.connect()
    row = conn.execute("SELECT text, redacted FROM tree WHERE text LIKE '%demo%'").fetchone()
    assert row and GH_PAT not in row["text"] and row["redacted"] >= 1
    body = Path(C.load()["export_path"]).read_text(encoding="utf8")
    assert GH_PAT not in body and "‹redacted›" in body


def test_old_sqlite_gains_a_redacted_column(tree_home):
    tree_home.make()
    with sqlite3.connect(C.DB) as old:
        old.executescript("""
            CREATE TABLE tree (
              id TEXT PRIMARY KEY, text TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'fact',
              tags TEXT NOT NULL DEFAULT '', source TEXT NOT NULL, observed_at TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'active', supersedes TEXT,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL, branch TEXT);
            CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT);
        """)
        old.execute(
            "INSERT INTO tree(id, text, source, observed_at, created_at, updated_at) VALUES (?,?,?,?,?,?)",
            ("oldplain01", "Alex rows twice a week", "claude", "2026-04-01", "2026-04-01T00:00:00+00:00", "2026-04-01T00:00:00+00:00"),
        )
    conn = S.connect()
    row = conn.execute("SELECT text, redacted FROM tree WHERE id='oldplain01'").fetchone()
    assert row["text"] == "Alex rows twice a week" and row["redacted"] == 0
    assert S.migrate(conn) is False


def test_conversation_urls_and_threads_stay_as_given(tree_home):
    tree_home.make()
    conn = S.connect()
    for text, thread in (("Mira drinks green tea", f"https://claude.ai/chat/{CHAT}"), ("Mira bikes to the studio", A2A)):
        r = S.add(conn, text=text, source="claude", thread=thread)
        row = conn.execute("SELECT thread, redacted FROM tree WHERE id=?", (r["id"],)).fetchone()
        assert row["thread"] == thread and row["redacted"] == 0 and "redacted" not in r


@pytest.mark.parametrize("storage", ["sqlite", "markdown"])
def test_prune_write_redacts_what_it_carries_over(tree_home, storage):
    """修剪合并、改写走 store.write：从旧叶子带过来的原话、标签、「用得上的时候」也先隐去、记处数；对话网址原样。"""
    tree_home.make(storage)
    conn = S.connect()
    old = S.add(conn, text="Mira keeps the studio code on a card", source="claude")["id"]
    chat = f"https://claude.ai/chat/{CHAT}"
    mid = S.write(conn, text=f"Mira keeps the studio code {OPENAI} on a card", source="prune", tags=f"studio,{NOTION}", replaces=[old],
                  prov={"quote": f"it was {NOTION}", "platform": "claude", "thread": chat, "message": ""},
                  cues=[f"the {PPLX} card", "studio door"])
    row = dict(conn.execute("SELECT text, quote, tags, cues, thread, redacted FROM tree WHERE id=?", (mid,)).fetchone())
    blob = " ".join(row[k] for k in ("text", "quote", "tags", "cues"))
    assert OPENAI not in blob and NOTION not in blob and PPLX not in blob and "studio door" in row["cues"]
    assert row["redacted"] == 4 and row["thread"] == chat
    if storage == "markdown":
        raw = (tree_home.vault / conn.execute("SELECT path FROM tree WHERE id=?", (mid,)).fetchone()["path"]).read_text(encoding="utf8")
        assert NOTION not in raw and PPLX not in raw and "redacted 4" in raw


@pytest.mark.parametrize("storage", ["sqlite", "markdown"])
def test_admin_edit_redacts_and_adds_to_the_count(tree_home, storage):
    tree_home.make(storage)
    conn = S.connect()
    mid = S.add(conn, text=f"Mira wrote {NOTION} on a card", source="claude")["id"]
    assert S.edit(conn, mid, f"Mira wrote {GH_GHP} on the back of a card", "owner")
    row = conn.execute("SELECT text, redacted FROM tree WHERE id=?", (mid,)).fetchone()
    assert GH_GHP not in row["text"] and "‹redacted›" in row["text"] and row["redacted"] == 2
    if storage == "markdown":
        raw = (tree_home.vault / conn.execute("SELECT path FROM tree WHERE id=?", (mid,)).fetchone()["path"]).read_text(encoding="utf8")
        assert GH_GHP not in raw and "redacted 2" in raw
    assert S.edit(conn, mid, "Mira wrote it on a card", "owner")
    assert conn.execute("SELECT redacted FROM tree WHERE id=?", (mid,)).fetchone()["redacted"] == 2  # 这次没有：照旧


def test_promote_puts_the_placeholder_into_the_profile(tree_home):
    """模型没写 text 时并进档案的是旧叶子的原文（可能是 0.7.0 以前写的）：USER.md 里是占位符；再做一遍不重复。"""
    from openmousse_tree import prune as P
    tree_home.make()
    S.save_profile("## About\n- Mira rows twice a week\n")
    conn = S.connect()
    conn.execute(
        "INSERT INTO tree(id, text, kind, tags, source, observed_at, status, created_at, updated_at) "
        "VALUES ('oldpromo01', ?, 'fact', '', 'claude', '2026-03-01', 'active', '2026-03-01T00:00:00+00:00', '2026-03-01T00:00:00+00:00')",
        (f"Mira keeps the studio code {STRIPE} offline",),
    )
    conn.commit()
    p = P.plan(conn, {"steps": [{"op": "promote", "id": "oldpromo01", "section": "About", "why": "a standing fact"}]})
    assert P.apply(conn, p["week"], digest=p["digest"])["done"] == 1
    prof = S.profile_text()
    assert STRIPE not in prof and "‹redacted›" in prof and "studio code" in prof
    P.apply(conn, p["week"], digest=p["digest"])
    assert S.profile_text() == prof
