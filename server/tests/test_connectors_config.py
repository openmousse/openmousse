"""连接页（connectors.py）认的 systemd 单元和跟踪课程的文件：没配用默认的名字，server.json 的 connectors 段能换（2026-10-20）。"""
from __future__ import annotations

import json
from types import SimpleNamespace

import connectors
import memtree
from config import settings


def test_unit_names_default_and_override(server_json):
    server_json({})
    assert connectors.unit_name("tree") == "mousse-tree.service"
    assert connectors.unit_name("mail") == "mousse-mail.timer"
    server_json({"connectors": {"units": {"tree": "my-tree", "mail": "my-mail.timer", "browser": " "}}})
    assert connectors.unit_name("tree") == "my-tree.service"  # 不带后缀的当 .service
    assert connectors.unit_name("mail") == "my-mail.timer"
    assert connectors.unit_name("browser") == "mousse-browser.service"  # 空的 = 没写
    assert connectors.short_unit("my-tree.service") == "my-tree" and connectors.short_unit("my-mail.timer") == "my-mail.timer"


def test_canvas_courses_file(server_json, tmp_path):
    server_json({})
    assert connectors.canvas_courses_file() == settings.openclaw_home / "canvas/tracked.json"
    server_json({"connectors": {"canvas_courses": str(tmp_path / "courses.json")}})
    assert connectors.canvas_courses_file() == tmp_path / "courses.json"


def test_mail_check_names_the_configured_timer(server_json, tmp_path):
    items = tmp_path / "mail" / "items.json"
    items.parent.mkdir()
    items.write_text(json.dumps({"items": {}}), encoding="utf8")
    server_json({"remember": {"mail": {"items": str(items)}}, "connectors": {"units": {"mail": "my-mail.timer"}}})
    state = {"my-mail.timer": {"Id": "my-mail.timer", "LoadState": "loaded", "ActiveState": "inactive"}}
    got = connectors.check_mail(state)
    assert got["status"] == "warn" and "my-mail.timer" in got["fix"]


def test_tree_unit_is_the_configured_one(server_json, monkeypatch):
    asked = []

    def fake_run(cmd, **kw):
        asked.append(cmd)
        return SimpleNamespace(stdout="loaded\n", returncode=0)
    monkeypatch.setattr(memtree.subprocess, "run", fake_run)
    server_json({})
    assert memtree.tree_unit() == "mousse-tree.service"
    server_json({"connectors": {"units": {"tree": "my-tree.service"}}})
    assert memtree.tree_unit() == "my-tree.service" and asked[-1][-1] == "my-tree.service"
