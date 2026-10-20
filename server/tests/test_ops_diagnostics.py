"""第 7 批 2.12：mousse diagnostics（ops/diagnostics.py）。临时的家目录里放一份带着假的人名、令牌、密钥、邮箱、对话片段的配置、日志和库，
打出来的包里一样都不能有。不连服务、不连网。"""
from __future__ import annotations

import json
import os
import sqlite3
import stat
import zipfile

import pytest

from ops import diagnostics as D
from tests.ops_helpers import make_home, ns, run_mousse

TOKENS = {"phone": "tok-phone-0123456789abcdef", "api": "tok-api-abcdefghijklmnopqrst"}
INLINE = "sk-inline-secret-0000111122223333"
ENVKEY = "sk-deepseek-secret-abcdef0123456789"
FORBIDDEN = (*TOKENS.values(), INLINE, ENVKEY, "alex@example.com", "my secret diary", "Pixel", "Alex Example", "secret project", "alex-family",
             "alex-phone", "/home/alex")
FILES = {"README.txt", "summary.json", "doctor.json", "status.json", "audit.json", "config-shape.json", "config-check.json", "engine.json",
         "databases.json", "usage.json", "logs.json", "update.json"}

LOG = """INFO:     Started server process [4242]
INFO:     127.0.0.1:5555 - "GET /api/chat/messages?token=tok-phone-0123456789abcdef HTTP/1.1" 200 OK
INFO:     127.0.0.1:5556 - "POST /api/chat HTTP/1.1" 500 Internal Server Error
[chat] 日结没存上：KeyError
[chat] user said: my secret diary about Pixel
ERROR:    Exception in ASGI application
Traceback (most recent call last):
  File "/home/alex/.openmousse/repo/server/chat.py", line 120, in respond
    do_it(text)
  File "/home/alex/.openmousse/venv/lib/python3.12/site-packages/httpx/_client.py", line 900, in send
    raise ValueError("my secret diary about Pixel")
ValueError: my secret diary about Pixel alex@example.com
[friends] alex@example.com sent something
"""


@pytest.fixture
def h(tmp_path, monkeypatch):
    h = make_home(tmp_path, monkeypatch)
    data = h.mousse / "data"
    data.mkdir()
    h.write({"app_name": "Mousse", "user_name": "Alex Example", "timezone": "Europe/Lisbon", "language": "en", "data_dir": str(data),
             "bind": {"port": 9}, "claw": {"kind": "builtin"},
             "auth": {"tokens": TOKENS, "tailscale_nodes": ["alex-phone"], "pairing": [{"hash": "abc", "expires": "2026-01-01T00:00:00Z"}]},
             "engine": {"model": "deepseek/deepseek-flash", "fallbacks": ["moonshot/kimi-k3"], "aliases": {"fast": "deepseek/deepseek-flash"},
                        "providers": {"custom": {"api": "openai", "url": "https://llm.alex-family.example/v1", "key": INLINE},
                                      "local": {"api": "openai", "url": "http://127.0.0.1:11434/v1", "key_env": "LOCAL_KEY"}}},
             "nutrition_targets": {"kcal": 2100, "protein": 120}, "share": {"private_words": ["secret project"]},
             "transcribe_prompt": "Alex Example talks about Pixel the cat", "social": {"email": "alex@example.com"}})
    (h.mousse / "secrets.env").write_text(f"DEEPSEEK_API_KEY={ENVKEY}\n", encoding="utf8")
    os.chmod(h.mousse / "secrets.env", 0o600)
    (h.mousse / "logs").mkdir()
    (h.mousse / "logs" / "server.log").write_text(LOG, encoding="utf8")
    conn = sqlite3.connect(data / "mousse.db")
    conn.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, thread TEXT, text TEXT)")
    conn.execute("INSERT INTO messages(thread, text) VALUES('main', 'my secret diary about Pixel')")
    conn.commit()
    conn.close()
    return h


def bundle(path) -> dict[str, str]:
    with zipfile.ZipFile(path) as z:
        return {n: z.read(n).decode("utf8") for n in z.namelist()}


def test_the_bundle_holds_no_secrets_content_or_emails(h, tmp_path, capsys):
    out = tmp_path / "out" / "diag.zip"
    assert D.main(ns(out=str(out), print=False, lines=None)) == 0
    assert stat.S_IMODE(out.stat().st_mode) == 0o600 and "Written: " in capsys.readouterr().out
    files = bundle(out)
    assert set(files) == FILES
    everything = "\n".join(files.values())
    for bad in (*FORBIDDEN, str(h.home)):
        assert bad not in everything, bad
    docs = {n: json.loads(t) for n, t in files.items() if n.endswith(".json")}
    assert all("error" not in d for n, d in docs.items() if n != "usage.json"), {n: d.get("error") for n, d in docs.items()}
    assert "Left out on purpose" in files["README.txt"] and "Not included" not in files["README.txt"]
    shape = docs["config-shape.json"]["shape"]
    assert shape["engine"]["model"] == "deepseek/deepseek-flash" and shape["engine"]["fallbacks"] == ["moonshot/kimi-k3"]
    assert shape["engine"]["aliases"] == {"fast": "deepseek/deepseek-flash"} and shape["timezone"] == "Europe/Lisbon"
    assert shape["auth"]["tokens"] == {"phone": "<set>", "api": "<set>"} and shape["auth"]["tailscale_nodes"] == "<1 items>"
    assert shape["auth"]["pairing"] == "<1 items>" and shape["user_name"] == "<str:12>" and shape["share"]["private_words"] == "<1 items>"
    custom, local = shape["engine"]["providers"]["custom"], shape["engine"]["providers"]["local"]
    assert custom == {"api": "openai", "url": "<https url>", "key": "<set>"}
    assert local == {"api": "openai", "url": "http://127.0.0.1:11434", "key_env": "LOCAL_KEY"}
    assert shape["nutrition_targets"] == {"kcal": "<number>", "protein": "<number>"} and shape["data_dir"] == "~/.openmousse/data"
    assert shape["bind"] == {"port": 9} and shape["claw"] == {"kind": "builtin"}
    logs = docs["logs.json"]
    assert logs["source"] == "~/.openmousse/logs/server.log" and logs["lines"] == len(LOG.splitlines())
    assert logs["levels"] == {"INFO": 3, "ERROR": 1} and logs["http"] == {"2xx": 1, "5xx": 1}
    assert logs["routes"] == {"GET /api/chat/…": 1, "POST /api/chat": 1}
    assert logs["tags"] == {"chat": {"lines": 2, "KeyError": 1}, "friends": {"lines": 1}}
    assert logs["tracebacks"] == [{"exception": "ValueError", "frames": ["server/chat.py:120:respond", "<lib>/httpx/_client.py:900:send"],
                                   "count": 1}]
    db = docs["databases.json"]["~/.openmousse/data/mousse.db"]
    assert db["tables"] == {"messages": 1} and db["quickCheck"] == "ok"  # 只数行，不读内容
    eng = docs["engine.json"]
    assert eng["chatModel"] == "deepseek/deepseek-flash" and eng["models"]["deepseek/deepseek-flash"]["key"] is True
    assert eng["models"]["moonshot/kimi-k3"]["key"] is False
    summary = docs["summary.json"]
    assert summary["kind"] == "openmousse-diagnostics" and "node" not in json.dumps(summary["system"]) and summary["serviceManager"] == "none"
    assert {c["id"] for c in docs["doctor.json"]["checks"]} and docs["status.json"]["server"]["up"] is False
    assert docs["config-check.json"]["problem"] is None


def test_default_place_and_print(h, capsys):
    assert D.main(ns(out=None, print=False, lines=50)) == 0
    found = list((h.mousse / "diagnostics").glob("openmousse-diagnostics-*.zip"))
    assert len(found) == 1 and stat.S_IMODE((h.mousse / "diagnostics").stat().st_mode) == 0o700
    capsys.readouterr()
    assert D.main(ns(out=None, print=True, lines=50)) == 0
    printed = capsys.readouterr().out
    assert "===== summary.json" in printed and "===== logs.json" in printed
    for bad in FORBIDDEN:
        assert bad not in printed, bad
    assert len(list((h.mousse / "diagnostics").glob("*.zip"))) == 1  # --print 不写文件
    assert D.main(ns(out=None, print=True, lines=-1)) == 2


def test_the_last_check_leaves_out_a_leaky_document(h, tmp_path, monkeypatch):
    monkeypatch.setattr(D, "update_part", lambda: {"oops": "write to alex@example.com"})
    monkeypatch.setattr(D, "clean", lambda v, words=(): v)  # 打码漏了
    out = tmp_path / "leak.zip"
    assert D.main(ns(out=str(out), print=False, lines=None)) == 0
    files = bundle(out)
    assert "update.json" not in files and "Not included: " in files["README.txt"] and "update.json" in files["README.txt"]
    assert not any(TOKENS["phone"] in t for t in files.values())  # config-shape 本来就只有记号；漏了的那份不在


def test_clean_text():
    home = str(D.C.home())
    assert D.clean_text("/home/bob/.openmousse/x and /Users/carol/Library") == "~/.openmousse/x and ~/Library"
    assert D.clean_text(f"{home}/.openmousse/data/mousse.db") == "~/.openmousse/data/mousse.db"
    assert D.clean_text("mail me: someone.else+tag@sub.example.org now") == "mail me: <email> now"
    assert D.clean_text("/usr/lib/python3/server/chat.py") == "/usr/lib/python3/server/chat.py"  # 别的路径不动
    assert D.shape("hello", "user_name") == "<str:5>" and D.shape("${MY_KEY}", "engine.providers.x.key") == "${MY_KEY}"
    assert D.shape("https://user:pw@example.test/v1", "engine.providers.x.url") == "<https url>"


def test_hosts_paths_and_names_stay_out(h, tmp_path, monkeypatch):
    """claw 的名字和 Tailscale 地址（doctor、status）、Funnel 的主机名（audit）、配对来的设备令牌名、~ 底下自己的目录：包里一样都不能有。"""
    cfg = h.read()
    cfg["claw"] = {"kind": "openai", "name": "Alex Hermes", "url": "https://alex-box.tail9f3e.ts.net/v1"}
    cfg["auth"]["tokens"]["device-alex-pixel-10191230"] = "tok-device-0123456789abcdef"
    cfg["engine"]["recall"] = {"extra": [{"path": str(D.C.home() / "Documents" / "Alex diary"), "name": "diary"}]}
    h.write(cfg)
    monkey = {"funnel": "Funnel exposes alex-box.tail9f3e.ts.net:443/api", "ip": "bind 100.101.102.103 · lan 192.168.1.20 · ok 127.0.0.1:8080",
              "v6": "fd7a:115c:a1e0::12 at 12:30:45", "remote": "git@github.com:alex/openmousse.git", "where": str(D.C.home() / "Documents" / "notes.md")}
    monkeypatch.setattr(D, "update_part", lambda: monkey)  # 当一份「会漏的」输出
    out = tmp_path / "hosts.zip"
    assert D.main(ns(out=str(out), print=False, lines=None)) == 0
    files = bundle(out)
    assert set(files) == FILES, set(files) ^ FILES  # 换干净了：一份都没被最后那道查扔掉
    text = "\n".join(files.values())
    for bad in ("Alex Hermes", "alex-box", "tail9f3e", "ts.net", "alex-pixel", "Documents", "Alex diary", "100.101.102.103", "192.168.1.20",
                "fd7a:", "github.com:alex", "tok-device-"):
        assert bad not in text, bad
    upd = json.loads(files["update.json"])
    assert upd["funnel"] == "Funnel exposes <tailnet host>:443/api" and upd["where"] == "~/<path>" and upd["remote"] == "<git remote>"
    assert upd["ip"] == "bind <tailnet ip> · lan <lan ip> · ok 127.0.0.1:8080" and upd["v6"] == "<tailnet ip> at 12:30:45"
    assert "<claw name> · https://<tailnet host>" in files["doctor.json"]
    assert json.loads(files["config-shape.json"])["shape"]["auth"]["tokens"] == {"phone": "<set>", "api": "<set>", "device-1": "<set>"}
    assert D.clean({"~/a/x.db": 1, "~/b/x.db": 2}) == {"~/<path>": 1, "~/<path> (2)": 2}  # 换完撞上的键：编号，不丢


def test_the_command_line(h):
    r = run_mousse(h, "diagnostics", "--print", "--lines", "20")
    assert r.returncode == 0, r.stderr
    assert "===== summary.json" in r.stdout and TOKENS["phone"] not in r.stdout and "alex@example.com" not in r.stdout
