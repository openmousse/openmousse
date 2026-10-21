"""发版前的隐私扫描（tools/release/privacy_scan.py）：一个临时的 git 仓库里放几个文件，看每一类认不认得出、假值放不放过、
名单和密钥只按指纹比、输出里不带原文。名字、地址都是编的。"""
from __future__ import annotations

import importlib.util
import json
import struct
import subprocess
import sys
import zlib
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[2] / "tools/release/privacy_scan.py"


@pytest.fixture(scope="module")
def ps():
    spec = importlib.util.spec_from_file_location("privacy_scan", TOOL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["privacy_scan"] = mod  # dataclass 要在 sys.modules 里找到它
    spec.loader.exec_module(mod)
    return mod


def png_with_text(text: str) -> bytes:
    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", b"\0" * 13) + chunk(b"tEXt", b"Comment\0" + text.encode()) + chunk(b"IEND", b"")


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    files = {
        "keys.py": 'A = "ghp_' + "Q7w" * 12 + '"\nB = "sk-wrong-test-9876543210zyxw"  # 假的\n',
        "paths.md": "see /home/quinnmaple/notes and /home/user/ok and ~/openmousse-wt/x\n",
        "net.md": "bind 100.88.13.7 and 100.64.0.1; host quinn-laptop.tail7e2f9.ts.net and https://<machine>.<tailnet>.ts.net\n",
        "mail.md": "write quinn@maplemail.com or alex@example.com; pkg d3@7.9.0\n",
        "phone.md": "call +44 7911 123 987 or 13958264710; fake +44 7700 900123\n",
        "people.md": "Quinn Maple went to Larkfield Academy.\n住在枫叶镇的人\n",
        "env.md": "the leaked value tok_live_A1b2C3d4E5f6G7h8 is here\n",
        "pic.png": png_with_text("Author: Quinn Maple"),
    }
    for name, body in files.items():
        p = root / name
        p.write_bytes(body if isinstance(body, bytes) else body.encode("utf8"))
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    return root


def kinds(hits) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for h in hits:
        out.setdefault(h.kind, []).append(h.file)
    return out


def test_each_kind_is_found_and_fakes_pass(ps, repo):
    hits, skipped, n = ps.scan(repo, None, ps.Secrets([]))
    got = kinds(hits)
    assert n == 8 and not skipped
    assert got["key"] == ["keys.py"]                       # sk-wrong-…-9876543210 一眼是假的
    assert sorted(got["path"]) == ["paths.md", "paths.md"]  # /home/quinnmaple 和 ~/openmousse-wt；/home/user 放过
    assert sorted(got["tailnet"]) == ["net.md", "net.md"]   # 100.88.13.7 和 quinn-laptop…ts.net；100.64.0.1、<machine> 放过
    assert got["email"] == ["mail.md"] and got["phone"] == ["phone.md", "phone.md"]
    assert got["image"] == ["pic.png"]


def test_words_and_secrets_match_by_fingerprint_and_never_print_the_value(ps, repo, tmp_path, capsys):
    words = tmp_path / "words.txt"
    words.write_text("# 编的名单\nquinn maple\nLarkfield Academy\n枫叶镇\n", encoding="utf8")
    env = tmp_path / ".env"
    env.write_text("SOME_TOKEN=tok_live_A1b2C3d4E5f6G7h8\nSHORT=abc\n", encoding="utf8")
    conf = tmp_path / "server.json"
    conf.write_text(json.dumps({"auth": {"tokens": {"phone": "tok_live_A1b2C3d4E5f6G7h8"}}, "app_name": "Quinn Maple",
                                "engine": {"providers": {"x": {"key_env": "PEBBLE_API_KEY"}}}}), encoding="utf8")
    tree = tmp_path / "config.json"
    tree.write_text(json.dumps({"tokens": {"Zq9Xv2Lm7Np4Rt6Wy8Bc": "claude"}}), encoding="utf8")  # 世界树的：令牌是键
    w = ps.Words(words.read_text(encoding="utf8").splitlines())
    assert w.count == 3 and all(len(k) == 64 for k in w.fps)  # 只留指纹
    s = ps.Secrets([env, conf, tree])
    assert "tokens.<键>（config.json）" in s.fps.values() and ps.fp("PEBBLE_API_KEY") not in s.fps  # 当键用的令牌算；key_env 是名字，不算
    hits, _, _ = ps.scan(repo, w, ps.Secrets([env, conf]))
    found = [h.show() for h in hits if h.kind in ("word", "secret")]
    assert "people.md:1: [word] 名单第 1 个词" in found and "people.md:1: [word] 名单第 2 个词" in found
    assert "people.md:2: [word] 名单第 3 个词" in found and "pic.png:0: [word] 名单第 1 个词" in found
    assert any(s.startswith("env.md:1: [secret] 真的密钥：SOME_TOKEN（.env）") for s in found)
    code = ps.main(["--root", str(repo), "--words", str(words), "--secrets", str(env), "--secrets", str(conf)])
    out = capsys.readouterr().out
    assert code == 1 and "tok_live_A1b2C3d4E5f6G7h8" not in out and "Quinn" not in out and "Larkfield" not in out and "枫叶镇" not in out
    assert ps.main(["--root", str(repo), "--words", str(tmp_path / "missing.txt")]) == 2
