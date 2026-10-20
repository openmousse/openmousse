"""mousse migrate openclaw（第 9 批 15.8，2026-10-19）：现建一个假的 OpenClaw 家目录（openclaw.json、state/openclaw.sqlite 的 cron_jobs、
工作区的 AGENTS.md、HEARTBEAT.md、memory/；数据全是虚构的），看报告、--out、--apply。不连网、不花钱、不碰这台机器上真的 claude。

要看的：只看（不加 --apply）什么都不写；--apply 先备份 server.json 和数据库；源目录一个字节不改（库开着 WAL、像 OpenClaw 正在跑）；
密钥默认不搬（只记变量名），--copy-secrets 才抄、值不进报告；定时任务建出来停着；要人做的列得出来；OpenClaw 不审批的主机命令迁过来
不会全放开。"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import stat
from pathlib import Path

import pytest

import config

TOKENS = {"phone": "tok-phone", "mcp": "tok-mcp"}
SECRETS = {"DEEPSEEK_API_KEY": "sk-fake-deepseek-0123456789abcdef", "MOONSHOT_API_KEY": "sk-fake-moonshot-0123456789abcdef",
           "BRAVE_API_KEY": "fake-brave-0123456789abcdef"}
LITERAL = "sk-fake-literal-proxy-0123456789"
CC = {"bin": "/usr/bin/claude", "auth": "claude.ai", "billing": "subscription"}


def tree(root: Path) -> dict[str, str]:
    """一个目录里每个文件（含隐藏的、-wal、-shm）的指纹和权限：前后一比就知道有没有一个字节变了、多没多文件。"""
    out = {}
    for p in sorted(root.rglob("*")):
        rel = str(p.relative_to(root))
        if p.is_symlink():
            out[rel] = "link:" + str(p.readlink())
        elif p.is_file():
            out[rel] = hashlib.sha256(p.read_bytes()).hexdigest() + f":{stat.S_IMODE(p.stat().st_mode):o}"
        else:
            out[rel] = "dir"
    return out


CRON = [  # (job_id, declaration_key, name, enabled, agent_id, payload_kind, job)
    ("j-brief", None, "Morning brief", 1, "main", "agentTurn",
     {"schedule": {"kind": "cron", "expr": "0 8 * * *", "tz": "UTC"}, "sessionTarget": "isolated",
      "payload": {"kind": "agentTurn", "message": "Summarize today's calendar in three lines."}, "delivery": {"mode": "announce"}}),
    ("j-water", None, "Water", 0, "fitness", "systemEvent",
     {"schedule": {"kind": "every", "everyMs": 7_200_000}, "sessionTarget": "main", "payload": {"kind": "systemEvent", "text": "Remind me to drink water."}}),
    ("j-habits", None, "Habit check", 1, "main", "command",
     {"schedule": {"kind": "cron", "expr": "*/30 * * * *", "tz": "Europe/Lisbon"},
      "payload": {"kind": "command", "argv": ["python3", "/opt/openmousse/server/board_ctl.py", "rows", "query", "habits"]}}),
    ("j-backup", None, "Nightly backup", 1, "main", "command",
     {"schedule": {"kind": "cron", "expr": "0 3 * * *"}, "payload": {"kind": "command", "argv": ["/srv/fictional/bin/backup.sh", "--all"]}}),
    ("j-old", None, "Old one-off", 1, "main", "agentTurn",
     {"schedule": {"kind": "at", "at": "2020-01-01T09:00:00Z"}, "payload": {"kind": "agentTurn", "message": "Too late."}}),
    ("hb-main", "heartbeat:main", "heartbeat-main", 1, "main", "heartbeat", {"schedule": {"kind": "every", "everyMs": 2_700_000}, "payload": {"kind": "heartbeat"}}),
    ("hb-fit", "heartbeat:fitness", "heartbeat-fitness", 1, "fitness", "heartbeat", {"schedule": {"kind": "every", "everyMs": 7_200_000}, "payload": {"kind": "heartbeat"}}),
    ("j-review", "skill-collection-review:main", "Skill review", 1, "main", "agentTurn", {"schedule": {"kind": "cron", "expr": "0 4 * * 0"}, "payload": {"kind": "agentTurn", "message": "x"}}),
    ("j-dream", "memory-core:memory-dreaming-promotion", "Memory Dreaming Promotion", 1, "main", "agentTurn",
     {"schedule": {"kind": "cron", "expr": "0 3 * * *"}, "payload": {"kind": "agentTurn", "message": "__openclaw_memory_core_short_term_promotion_dream__"}}),
]


def make_openclaw(root: Path, *, subscription: bool = True) -> tuple[Path, sqlite3.Connection]:
    """一个虚构的 OpenClaw 家。返回 (家, 还开着的库连接)：库是 WAL 的，连接开着 = OpenClaw 正在跑（-wal、-shm 都在）。"""
    home = root / "openclaw"
    ws = home / "workspace"
    (ws / "memory").mkdir(parents=True)
    (ws / "AGENTS.md").write_text("# AGENTS.md\n\nBe brief.\n", encoding="utf8")
    (ws / "SOUL.md").write_text("Calm, dry humour.\n", encoding="utf8")
    (ws / "MEMORY.md").write_text("# MEMORY.md\n\n- Prefers tea over coffee.\n", encoding="utf8")
    (ws / "memory" / "2026-10-01.md").write_text("Walked by the river.\n", encoding="utf8")
    (ws / "HEARTBEAT.md").write_text("# Heartbeat\n\n- Check whether a parcel was delivered today.\n", encoding="utf8")
    fit = home / "workspace-fitness"
    fit.mkdir()
    (fit / "AGENTS.md").write_text("# Fitness\n", encoding="utf8")
    (fit / "HEARTBEAT.md").write_text("# Heartbeat\n\n- [ ]\n", encoding="utf8")  # 实际是空的：清单在库里
    (root / "notes").mkdir()
    (home / ".env").write_text("".join(f"{k}={v}\n" for k, v in SECRETS.items()), encoding="utf8")
    profiles = {"anthropic:subscription": {"provider": "anthropic", "mode": "token"}, "anthropic:api": {"provider": "anthropic", "mode": "api_key"},
                "openai:me": {"provider": "openai", "mode": "oauth"}}
    order = ["anthropic:subscription", "anthropic:api"] if subscription else ["anthropic:api"]
    cfg = {
        "auth": {"profiles": profiles, "order": {"anthropic": order, "openai": ["openai:me"]}},
        "agents": {
            "defaults": {
                "model": {"primary": "anthropic/claude-opus-5-5", "fallbacks": ["deepseek/deepseek-flash", "anthropic/claude-sonnet-5-5", "openai/gpt-5.5"]},
                "modelPolicy": {"allow": ["opus", "deepseek/deepseek-flash", "moonshot/kimi-k3", "openrouter/*"]},
                "heartbeat": {"every": "45m", "model": "deepseek/deepseek-flash", "activeHours": {"start": "08:00", "end": "22:00"}},
                "skills": ["journal", "inbox"],
                "sandbox": {"mode": "non-main", "workspaceAccess": "ro", "docker": {"image": "openclaw-sandbox:bookworm-slim", "network": "bridge", "memory": "1g"},
                            "prune": {"idleHours": 2}},
            },
            "entries": {"fitness": {"name": "Fitness Coach", "skills": ["board"], "heartbeat": {"every": "2h"}, "model": "moonshot/kimi-k3"}},
        },
        "models": {"providers": {
            "moonshot": {"baseUrl": "https://api.moonshot.cn/v1", "apiKey": "${MOONSHOT_API_KEY}", "api": "openai-completions"},
            "my-proxy": {"baseUrl": "http://127.0.0.1:9/v1", "apiKey": LITERAL, "api": "openai-completions", "models": [{"id": "m1", "name": "m1"}]}}},
        "memory": {"search": {"provider": "openai", "model": "text-embedding-3-small", "remote": {"apiKey": "${OPENAI_API_KEY}"},
                              "extraPaths": [str(root / "notes")]}},
        "tools": {"web": {"search": {"enabled": True, "provider": "brave", "maxResults": 5}}, "exec": {"mode": "full"}, "media": {"image": {"enabled": True}}},
        "session": {"reset": {"mode": "daily", "atHour": 5}},
        "channels": {"telegram": {"enabled": True, "botToken": "${TELEGRAM_BOT_TOKEN}"}},
        "mcp": {"servers": {"notes": {"command": "npx", "args": ["-y", "notes-mcp"], "env": {"NOTES_TOKEN": "fake-literal-token", "NOTES_DIR": "/tmp/notes"}},
                            "openmousse": {"url": "http://127.0.0.1:8080/mcp", "headers": {"Authorization": "Bearer tok-mcp"}}}},
        "hooks": {"internal": {"entries": {"session-memory": {"enabled": True}}}},
    }
    text = json.dumps(cfg, indent=2)
    (home / "openclaw.json").write_text("// fictional test config (JSON5)\n" + text[:-1].rstrip() + ",\n}\n", encoding="utf8")
    (home / "state").mkdir()
    conn = sqlite3.connect(home / "state" / "openclaw.sqlite")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
      CREATE TABLE cron_jobs (store_key TEXT NOT NULL, job_id TEXT NOT NULL, declaration_key TEXT, owner_agent_id TEXT, name TEXT NOT NULL,
        description TEXT, enabled INTEGER NOT NULL, agent_id TEXT, payload_kind TEXT NOT NULL, job_json TEXT NOT NULL, state_json TEXT NOT NULL DEFAULT '{}',
        sort_order INTEGER NOT NULL DEFAULT 0, updated_at INTEGER NOT NULL, PRIMARY KEY (store_key, job_id));
      CREATE TABLE cron_job_scratch (store_key TEXT NOT NULL, job_id TEXT NOT NULL, content TEXT, revision INTEGER NOT NULL, source_sha256 TEXT,
        updated_at_ms INTEGER NOT NULL, PRIMARY KEY (store_key, job_id));
      CREATE TABLE exec_approvals_config (config_key TEXT NOT NULL PRIMARY KEY, raw_json TEXT NOT NULL);
    """)
    store = str(home / "cron" / "jobs.json")
    for i, (jid, decl, name, enabled, agent, kind, job) in enumerate(CRON):
        conn.execute("INSERT INTO cron_jobs(store_key, job_id, declaration_key, name, enabled, agent_id, payload_kind, job_json, sort_order, updated_at) "
                     "VALUES(?,?,?,?,?,?,?,?,?,?)", (store, jid, decl, name, enabled, agent, kind, json.dumps({"id": jid, "name": name, **job}), i, 1_760_000_000))
    conn.execute("INSERT INTO cron_job_scratch VALUES(?,?,?,?,?,?)", (store, "hb-fit", "- Check whether a workout was logged today.", 1, None, 1))
    approvals = {"version": 1, "defaults": {"security": "full", "ask": "off"},
                 "agents": {"main": {"security": "allowlist", "allowlist": [{"pattern": "/usr/bin/uptime"},
                                                                            {"pattern": "/srv/fictional/bin/backup.sh", "argPattern": "^--hash [a-f0-9]+$"}]}}}
    conn.execute("INSERT INTO exec_approvals_config VALUES('default', ?)", (json.dumps(approvals),))
    conn.commit()
    return home, conn


@pytest.fixture
def oc(tmp_path):
    home, conn = make_openclaw(tmp_path / "src")
    yield home
    conn.close()


@pytest.fixture(autouse=True)
def no_real_claude(monkeypatch):
    """runners.detect 会起这台机器上真的 claude：测试里一律换成假的（默认没登录）。"""
    from engine import runners
    monkeypatch.setattr(runners, "detect", lambda name: None)


@pytest.fixture
def mousse(tmp_path, server_json, monkeypatch):
    """一台用自带内核的 OpenMousse：server.json、数据库、备份都在 tmp_path，库里先有两个对话（一个选了 anthropic/…）。"""
    import chat
    data = tmp_path / "data"
    db = data / "mousse.db"
    ws = tmp_path / "own-workspace"
    ws.mkdir()
    for attr, val in (("db", db), ("data_dir", data), ("backup_dir", data / "backups"), ("workspace", ws), ("env_file", tmp_path / "none.env")):
        monkeypatch.setattr(config.settings, attr, val)
    monkeypatch.setattr(chat, "DB", db)
    base = {"claw": {"kind": "builtin"}, "auth": {"tokens": TOKENS}, "timezone": "UTC", "language": "en", "data_dir": str(data),
            "engine": {"model": "deepseek/deepseek-flash"}}
    path = server_json(base)
    import data as data_mod
    with chat._lock, data_mod.ddb() as conn:
        conn.execute("INSERT INTO threads(id, model, updated_at) VALUES('main', 'anthropic/claude-opus-5-5', '2026-10-01T00:00:00Z')")
        conn.execute("INSERT INTO threads(id, model, updated_at) VALUES('t-side', 'deepseek/deepseek-flash', '2026-10-01T00:00:00Z')")

    class M:
        pass
    m = M()
    m.path, m.base, m.db, m.data, m.ws, m.write = path, base, db, data, ws, server_json
    return m


def cli(*argv: str) -> int:
    import mousse
    return mousse.main(["migrate", *argv])


def test_json5_with_comments_trailing_commas_and_bare_keys():
    from migrate.common import _strip_json5
    text = "{\n  // a comment\n  agents: {defaults: {model: 'anthropic/x', list: [1, 2,],},},\n  /* block */ \"url\": \"http://a/b, }\",\n}\n"
    assert json.loads(_strip_json5(text)) == {"agents": {"defaults": {"model": "anthropic/x", "list": [1, 2]}}, "url": "http://a/b, }"}


def test_dry_run_reports_and_writes_nothing(oc, mousse, capsys):
    before_src, before_cfg = tree(oc), mousse.path.read_bytes()
    assert cli("openclaw", "--openclaw-home", str(oc)) == 0
    out = capsys.readouterr().out
    assert tree(oc) == before_src                       # 源目录一个字节没变，-wal、-shm 也没动
    assert mousse.path.read_bytes() == before_cfg       # server.json 没写
    assert not list(mousse.path.parent.glob("server.json.bak-*")) and not (mousse.data / "backups").exists()
    assert not (mousse.path.parent / "secrets.env").exists()
    assert "dry run: nothing was written" in out and "Converted" in out and "Needs you" in out and "Not converted" in out
    for v in [*SECRETS.values(), LITERAL]:
        assert v not in out                             # 值一个都不打出来
    assert "mousse migrate openclaw --openclaw-home" in out and "--apply" in out


def test_plan_converts_models_providers_and_keeps_subscriptions_out_of_the_fallbacks(oc, mousse, monkeypatch):
    import migrate
    from engine import runners
    monkeypatch.setattr(runners, "detect", lambda name: CC)
    p = migrate.make_plan("openclaw", oc)
    e = p.patch["engine"]
    assert e["model"] == "claude-code/claude-opus-5-5"                 # 订阅优先 + Claude Code 登录着
    assert e["fallbacks"] == ["deepseek/deepseek-flash"]               # anthropic 去掉；Codex 登录的 openai 不进
    assert e["chatgpt"]["models"] == ["chatgpt/gpt-5.5"]
    assert e["models"] == ["claude-code/claude-opus-5-5", "deepseek/deepseek-flash", "moonshot/kimi-k3"]  # 别名 opus 换成引用，通配的不转
    assert e["use"] == {"background": "deepseek/deepseek-flash"}
    assert e["providers"]["moonshot"] == {"url": "https://api.moonshot.cn/v1"}
    assert e["providers"]["my-proxy"] == {"api": "openai", "url": "http://127.0.0.1:9/v1", "name": "my-proxy", "key_env": "MY_PROXY_API_KEY"}
    assert p.held == {"MY_PROXY_API_KEY": LITERAL}                     # 直接写的值只在内存里
    assert set(p.secrets) == {"DEEPSEEK_API_KEY", "MOONSHOT_API_KEY", "BRAVE_API_KEY"} and p.patch["env_file"] == str(oc / ".env")
    assert e["recall"]["embeddings"] == {"backend": "openai", "model": "text-embedding-3-small"}
    assert e["recall"]["extra"] == [str(oc.parent / "notes")]
    assert e["web"]["search"] == {"provider": "brave", "count": 5}
    assert e["skills"]["agents"] == {"main": ["journal", "inbox"], "fitness": ["board"]}
    assert p.replace["workspace"] == str(oc / "workspace") and p.patch["agent_workspaces"] == {"fitness": str(oc / "workspace-fitness")}
    assert [a["id"] for a in p.agents] == ["fitness"] and p.agents[0]["name"] == "Fitness Coach"
    assert p.patch["mcp_servers"] == {"notes": {"command": "npx", "args": ["-y", "notes-mcp"], "env": {"NOTES_DIR": "/tmp/notes"}}}
    assert p.threads == {"from": "anthropic/", "to": "claude-code/"}
    cant = " ".join(f"{x.what} {x.text}" for x in p.cant)
    for what in ("openrouter/*", "channels.telegram", "tools.media", "hooks.internal.entries.session-memory", "cron:Skill review", "cron:Memory Dreaming Promotion", "OpenClaw's own",
                 "mcp.servers.openmousse", "cron:Old one-off"):
        assert what in cant, what


def test_without_claude_code_the_subscription_model_stays_pay_as_you_go_and_says_so(oc, mousse):
    import migrate
    p = migrate.make_plan("openclaw", oc)
    assert p.patch["engine"]["model"] == "anthropic/claude-opus-5-5" and p.threads is None
    assert "anthropic/claude-sonnet-5-5" not in p.patch["engine"]["fallbacks"]
    assert any(x.what == "engine.model" and "billed per use" in x.text for x in p.todo)


def test_needs_you_lists_what_a_person_has_to_decide(oc, mousse):
    import migrate
    p = migrate.make_plan("openclaw", oc)
    todo = {x.what: x.text for x in p.todo}
    assert "session.reset.atHour" in todo
    assert "engine.use.card / doorman / json" in " ".join(x.text for x in p.todo)
    assert "isn't one of the command-line tools" in todo["cron:Nightly backup"]
    assert "Europe/Lisbon" in todo["cron:Habit check"]
    assert '"0" to "45m"' in todo["heartbeat:main"] and '"0" to "2h"' in todo["heartbeat:fitness"]
    assert "argPattern" in todo["exec-approvals.agents.main"]
    assert "without approval" in todo["exec-approvals.agents.fitness"]    # OpenClaw 不审批：迁过来关着
    assert "NOTES_TOKEN" in todo["mcp.servers.notes"]
    assert "Image openclaw-sandbox:bookworm-slim wasn't moved" in todo["agents.defaults.sandbox.docker.image"]
    assert "Network bridge wasn't moved" in todo["agents.defaults.sandbox.docker.network"]


def test_sandbox_is_only_written_where_docker_is_there(oc, mousse, monkeypatch):
    import migrate
    import migrate.openclaw as mo
    monkeypatch.setattr(mo.shutil, "which", lambda name: None)
    p = migrate.make_plan("openclaw", oc)
    assert "sandbox" not in p.patch["engine"] and "no docker" in {x.what: x.text for x in p.todo}["agents.*.sandbox"]
    monkeypatch.setattr(mo.shutil, "which", lambda name: "/usr/bin/docker" if name == "docker" else None)
    p = migrate.make_plan("openclaw", oc)
    assert p.patch["engine"]["sandbox"] == {"memory": "1g", "idle_minutes": 120, "agents": {"fitness": {"only": False, "access": "ro"}}}  # non-main：主对话不进


def test_apply_backs_up_first_then_writes_and_never_touches_the_source(oc, mousse, capsys, monkeypatch):
    from engine import jobs, runners
    monkeypatch.setattr(runners, "detect", lambda name: CC)
    before_src, before_cfg = tree(oc), mousse.path.read_bytes()
    assert cli("openclaw", "--openclaw-home", str(oc), "--apply") == 0
    out = capsys.readouterr().out
    assert tree(oc) == before_src
    baks = list(mousse.path.parent.glob("server.json.bak-*"))
    assert len(baks) == 1 and baks[0].read_bytes() == before_cfg and stat.S_IMODE(baks[0].stat().st_mode) == 0o600
    dbs = list((mousse.data / "backups").glob("mousse.db.*.pre-migrate"))
    assert len(dbs) == 1
    old = sqlite3.connect(dbs[0]).execute("SELECT model FROM threads WHERE id='main'").fetchone()[0]
    assert old == "anthropic/claude-opus-5-5"                             # 备份是改之前的
    cfg = json.loads(mousse.path.read_text())
    assert cfg["auth"]["tokens"] == TOKENS and cfg["claw"] == {"kind": "builtin"}  # 原来的都在
    assert cfg["engine"]["model"] == "deepseek/deepseek-flash"            # 已经有的不盖，列进「要人做的」
    assert "engine.model: Already set in server.json (deepseek/deepseek-flash) and left alone; OpenClaw has claude-code/claude-opus-5-5" in out
    assert cfg["engine"]["fallbacks"] == ["deepseek/deepseek-flash"] and cfg["workspace"] == str(oc / "workspace")
    db = sqlite3.connect(mousse.db)
    assert dict(db.execute("SELECT id, model FROM threads").fetchall()) == {"main": "claude-code/claude-opus-5-5", "t-side": "deepseek/deepseek-flash",
                                                                            "fitness": "moonshot/kimi-k3"}   # Agent 自己的模型跟过去
    assert db.execute("SELECT name FROM groups WHERE id='fitness'").fetchone()[0] == "Fitness Coach"
    mine = [j for j in jobs.listing() if j["created_by"] == "migrate:openclaw"]
    assert sorted(j["name"] for j in mine) == ["Habit check", "Morning brief", "Water"]
    assert all(j["state"] == "paused" and not j["approved"] and j["next_run"] is None for j in mine)  # 一律先停着
    cmd = next(j for j in mine if j["name"] == "Habit check")["command"]
    assert cmd["tool"] == "board" and cmd["args"] == ["rows", "query", "habits"]
    water = next(j for j in mine if j["name"] == "Water")
    assert water["agent"] == "fitness" and water["schedule"]["kind"] == "every" and water["session"] == "chat"
    hb = cfg["engine"]["heartbeat"]
    assert hb["main"] == {"every": "0", "level": "quiet", "active": ["08:00", "22:00"]}
    assert hb["fitness"]["every"] == "0" and hb["fitness"]["prompt"] == "- Check whether a workout was logged today."
    assert not (mousse.path.parent / "secrets.env").exists()             # 密钥默认不搬
    for v in [*SECRETS.values(), LITERAL]:
        assert v not in out and v not in mousse.path.read_text()
    saved = list((mousse.data / "migrate").glob("openclaw-*/report.md"))
    assert len(saved) == 1 and stat.S_IMODE(saved[0].stat().st_mode) == 0o600


def test_host_commands_never_become_fully_open(oc, mousse):
    assert cli("openclaw", "--openclaw-home", str(oc), "--apply") == 0
    he = json.loads(mousse.path.read_text())["engine"]["host_exec"]
    assert he == {"agents": {"main": {"allow": ["/usr/bin/uptime ..."], "ask": "on-miss"}}}   # 只有写明了的那一条；fitness 关着
    assert "full" not in json.dumps(he)


def test_apply_twice_creates_nothing_twice(oc, mousse):
    from engine import jobs
    assert cli("openclaw", "--openclaw-home", str(oc), "--apply") == 0
    first = sorted(j["id"] for j in jobs.listing() if j["created_by"] == "migrate:openclaw")
    cfg = mousse.path.read_text()
    assert cli("openclaw", "--openclaw-home", str(oc), "--apply") == 0
    assert sorted(j["id"] for j in jobs.listing() if j["created_by"] == "migrate:openclaw") == first
    assert json.loads(mousse.path.read_text()) == json.loads(cfg)
    assert sqlite3.connect(mousse.db).execute("SELECT COUNT(*) FROM groups").fetchone()[0] == 1


def test_copy_secrets_copies_values_into_secrets_env_but_never_prints_them(oc, mousse, capsys):
    before = tree(oc)
    assert cli("openclaw", "--openclaw-home", str(oc), "--apply", "--copy-secrets") == 0
    out = capsys.readouterr().out
    import secretstore
    got = secretstore.entries()
    assert {k: v for k, (v, _at) in got.items()} == {**SECRETS, "MY_PROXY_API_KEY": LITERAL}
    assert stat.S_IMODE(secretstore.path().stat().st_mode) == 0o600
    for v in [*SECRETS.values(), LITERAL]:
        assert v not in out
    assert tree(oc) == before


def test_out_writes_the_three_files_without_secret_values(oc, mousse, tmp_path):
    out = tmp_path / "report"
    assert cli("openclaw", "--openclaw-home", str(oc), "--out", str(out)) == 0
    assert sorted(p.name for p in out.iterdir()) == ["jobs.json", "report.md", "server.patch.json"]
    patch = json.loads((out / "server.patch.json").read_text())
    assert patch["workspace"] == str(oc / "workspace") and patch["engine"]["fallbacks"] == ["deepseek/deepseek-flash"]
    jobs_ = json.loads((out / "jobs.json").read_text())
    assert {j["name"] for j in jobs_} == {"Morning brief", "Water", "Habit check"} and {j["state"] for j in jobs_} == {"paused"}
    for f in out.iterdir():
        assert stat.S_IMODE(f.stat().st_mode) == 0o600
        for v in [*SECRETS.values(), LITERAL]:
            assert v not in f.read_text()
    assert not list(mousse.path.parent.glob("server.json.bak-*"))     # --out 不等于 --apply


def test_apply_refuses_on_another_claw(oc, mousse, capsys):
    mousse.write({**mousse.base, "claw": {"kind": "openai", "url": "http://127.0.0.1:9/v1"}})
    before = mousse.path.read_bytes()
    assert cli("openclaw", "--openclaw-home", str(oc), "--apply") == 2
    assert mousse.path.read_bytes() == before and not list(mousse.path.parent.glob("server.json.bak-*"))
    assert "isn't the built-in engine" in capsys.readouterr().err


def test_turning_a_migrated_job_on(oc, mousse, capsys):
    from engine import jobs
    assert cli("openclaw", "--openclaw-home", str(oc), "--apply") == 0
    j = next(j for j in jobs.listing() if j["name"] == "Morning brief")
    capsys.readouterr()
    assert cli("on") == 0 and j["id"] in capsys.readouterr().out          # 不写 id = 列出停着的
    assert cli("on", j["id"]) == 0
    now = jobs.get(j["id"])
    assert now["state"] == "on" and now["approved"] and now["next_run"]
    other = jobs._insert("main", jobs.clean("main", {"when": "every day 9am", "task": "Hi"}, by_user=True), state="paused", approved=False,  # noqa: SLF001
                         by="user", next_run=None)
    assert cli("on", other["id"]) == 1 and jobs.get(other["id"])["state"] == "paused"   # 不是迁过来的不管


def test_missing_home_and_wrong_home(tmp_path, mousse, capsys):
    assert cli("openclaw", "--openclaw-home", str(tmp_path / "nope")) == 2
    (tmp_path / "empty").mkdir()
    assert cli("openclaw", "--openclaw-home", str(tmp_path / "empty")) == 2
    assert "openclaw.json" in capsys.readouterr().err
