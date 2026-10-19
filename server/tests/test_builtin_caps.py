"""10/19 彩排补的第 4 件：claw 选 builtin 时的 caps（claw.caps：tasks、agentWorkspaces 回 true，app 的任务页、删 Agent 的说明靠它），
项目卡上「在跑的任务」读内核的台账（projects.tasks_of → engine/ledger.py），删 Agent 照 agentWorkspaces 说的归档工作区（agents.archive）。
OpenClaw 那条路不变。"""
from __future__ import annotations

import asyncio
import json

import httpx

import claw
import config

BUILTIN = {"claw": {"kind": "builtin"}, "engine": {"model": "deepseek/deepseek-flash"}}
OPENCLAW_CAPS = {"steer": True, "agentWorkspaces": True, "tasks": True, "approvals": True, "cron": True, "billing": True,
                 "channels": True, "rewind": True, "modelSwitch": True}


def test_builtin_turns_on_tasks_and_agent_workspaces(server_json):
    server_json(BUILTIN)
    c = claw.caps()
    assert c["tasks"] and c["agentWorkspaces"] and c["steer"]
    assert c["approvals"] and c["cron"] and c["billing"]  # 10/19 第 1 批起这几页读内核（engine/appdata.py，test_builtin_pages.py）
    assert not c["channels"]  # 内核没有 Telegram、Discord 这些渠道
    server_json({"claw": {"kind": "openai", "url": "http://127.0.0.1:9/v1"}})
    c = claw.caps()
    assert not (c["tasks"] or c["agentWorkspaces"] or c["steer"])  # 别的 claw 照旧
    server_json({})
    assert claw.caps() == OPENCLAW_CAPS  # OpenClaw 那条路不变


def test_health_tells_the_app(server_json):
    import main
    server_json({**BUILTIN, "auth": {"tokens": {"phone": "tok-phone"}}})

    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as c:
            return (await c.get("/api/health", headers={"Authorization": "Bearer tok-phone"})).json()
    h = asyncio.run(go())
    assert h["claw"]["kind"] == "builtin" and h["claw"]["caps"]["tasks"] is True and h["claw"]["caps"]["agentWorkspaces"] is True


def task_row(tid: str, thread: str, label: str, status: str, parent: str | None = None) -> dict:
    from engine import ledger
    now = ledger.now_ms()
    return ledger.insert(id=tid, thread=thread, agent="main", parent=parent, depth=2 if parent else 1, turn=None, call=None, label=label,
                         task=f"{label}.", runner="engine", model=None, level="read", timeout_s=600, status=status, created_at=now,
                         ended_at=None if status == "running" else now, delivery="none", data={})


def test_project_card_counts_tasks_from_the_engine_ledger(server_json):
    import projects
    server_json(BUILTIN)
    task_row("tk_pc_run", "sc-pcard01", "Draft the brief", "running")
    task_row("tk_pc_done", "sc-pcard01", "Collect sources", "done")
    task_row("tk_pc_child", "sc-pcard01", "A sub-step", "running", parent="tk_pc_run")  # 嵌套的子任务不单独算
    task_row("tk_pc_other", "sc-pcard02", "Someone else's", "running")
    got = projects.tasks_of("sc-pcard01")
    assert got["available"] and got["running"] == 1 and got["done"] == 1
    assert [(x["id"], x["title"], x["status"]) for x in got["items"]] == [("tk_pc_run", "Draft the brief", "进行中"),
                                                                        ("tk_pc_done", "Collect sources", "完成")]
    card = {"id": "sc-pcard01", "title": "Brief", "goal": "", "deadlines": [], "steps": [], "decisions": [], "progress": "", "progressAt": None,
            "tasks": got, "archived": False}
    assert "Tasks running: 1 (Draft the brief)" in projects.context_text(card)  # 带给模型的项目卡上也看得到
    server_json({})  # OpenClaw：照旧读它的台账（这台机器上没有 → available false）
    assert projects.tasks_of("sc-pcard01") == {"available": False, "running": 0, "done": 0, "items": []}


def test_deleting_an_agent_under_builtin_archives_its_workspace(server_json, tmp_path, monkeypatch):
    """caps.agentWorkspaces 回 true 以后 app 说「工作区和记忆移入归档」：builtin 下删 Agent 就照这样做，openclaw.json 一个字不碰。"""
    import chat
    import data
    home = tmp_path / "openclaw"
    ws = home / "workspace-g-arch"
    (ws / "memory").mkdir(parents=True)
    (ws / "MEMORY.md").write_text("Prefers tea.", encoding="utf8")
    monkeypatch.setattr(config.settings, "openclaw_home", home)
    path = server_json({**BUILTIN, "agent_workspaces": {"g-arch": str(ws)}})
    with chat._lock, data.ddb() as conn:
        for gid in ("g-arch", "g-plain"):
            conn.execute("INSERT OR REPLACE INTO groups(id, name, icon, purpose, created_at) VALUES(?,?,?,?,?)", (gid, gid, "x", "", chat.now_iso()))
    out = data.delete_group("g-arch")
    archived = home / "archive"
    moved = next(archived.iterdir())
    assert out["archived"] == str(moved) and moved.name.startswith("workspace-g-arch-") and (moved / "MEMORY.md").read_text() == "Prefers tea."
    assert not ws.exists() and "g-arch" not in json.loads(path.read_text())["agent_workspaces"]  # 路由去掉了
    assert not (home / "openclaw.json").exists()
    assert data.delete_group("g-plain")["archived"] is None  # builtin 里新建的（只在 groups 表里）：没有工作区可归档
