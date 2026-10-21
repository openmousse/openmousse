"""进度上的一步说人话（engine/steps.py，app 设计第 23 张）：工具名 + Tool.title → label / doing / 细节（不放参数原文）。"""
from __future__ import annotations

import pytest

import bridges
import i18n
from engine import steps


@pytest.fixture
def zh():
    tok = i18n.use("zh-CN")
    yield
    i18n.reset(tok)


def test_bridge_tools_by_subcommand(zh):
    assert steps.describe("schedule", "schedule day") == {"label": "查看日程", "doing": "正在查看日程", "detail": ""}
    assert steps.describe("board", "board rows add")["label"] == "记录数据"
    assert steps.describe("board", "board table")["label"] == "查看数据表"  # 没写动作：那个子命令自己的一句
    assert steps.describe("schedule", "schedule frobnicate")["label"] == "处理日程"  # 认不出的子命令：这个工具的默认
    assert steps.describe("handoff", "handoff --meet")["label"] == "发起会议"
    assert steps.describe("tree", "tree recall")["label"] == "检索世界树"


def test_english_and_doing():
    assert steps.describe("schedule", "schedule day") == {"label": "Check the schedule", "doing": "Checking the schedule", "detail": ""}
    assert steps.describe("memory_search", "memory_search")["label"] == "Search memory"


def test_detail_keeps_only_what_the_label_does_not_say(zh):
    assert steps.describe("read", "notes/plan.md")["detail"] == "plan.md"
    assert steps.describe("read_file", "/srv/x/report final.pdf")["detail"] == "report final.pdf"
    assert steps.describe("web_fetch", "https://www.example.com/a/b?q=1")["detail"] == "example.com"
    assert steps.describe("browser", "open https://news.example.org/item?id=1")["detail"] == "news.example.org"
    assert steps.describe("skill", "skill writing-guide")["detail"] == "writing-guide"
    assert steps.describe("tasks", "派后台任务：整理面试材料") == {"label": "派出后台任务", "doing": "正在派出后台任务", "detail": "整理面试材料"}
    assert steps.describe("apply_patch", "a/x.py, b/y.py (+2)")["detail"] == "x.py, y.py (+2)"
    assert steps.describe("nodes", "camera_snap phone")["detail"] == "phone"  # 哪台设备
    # 参数原文不放：命令、搜索词、提示词、一句话搜的 query、附件 id
    for name, title in (("exec", "rm -rf build && make"), ("web_search", "flights to Lisbon"), ("image_generate", "a cat on a sofa"),
                        ("speak", "Good morning"), ("skill", "skill how do I write a report"), ("view_image", "0123456789ab"),
                        ("ls", "/workspace"), ("todo", "待办：写周报"),
                        # nodes 带命令、路径的几样：没写设备（只有一台能做时可以不写）时第二个词就是命令、路径，分不出来，设备也不写
                        ("nodes", "run cat notes.txt"), ("nodes", "file_read /srv/x/plan.md"), ("nodes", "run mini uptime")):
        assert steps.describe(name, title)["detail"] == "", name


def test_apps_and_unknown_tools(zh):
    assert steps.describe("notion__search", "Notion · search_pages") == {"label": "使用 Notion", "doing": "正在使用 Notion", "detail": "search_pages"}
    assert steps.describe("weird_tool", "whatever it was") == {"label": "使用工具", "doing": "正在使用工具", "detail": "weird_tool"}
    assert steps.describe("tool_call", "schedule add")["label"] == "添加日程"  # 按名字调的：照被调的那个配
    assert steps.describe("tool_call", "some raw arguments")["detail"] == ""
    assert steps.describe("chrome", "click e12")["doing"] == "正在 Chrome 中点击"  # 不写成「正在在」


def test_every_kernel_tool_has_its_own_label():
    """新加的工具忘了配：这里会挂（照 engine 里的工具名常量列，和 bridges.BRIDGES）。"""
    from engine import (ask, chrome, codeexec, difftool, hands, heartbeat, host_exec, host_files, host_process, imagegen, intents, llm_task,
                        manual_write, mediagen, nodes, patch, pdf, plan, sessions, skill_market, skill_write, spill, stt, tasks, todo, tts,
                        unattended, video, vision)
    names = {ask.TOOL, chrome.NAME, codeexec.NAME, difftool.NAME, heartbeat.RESPOND, host_exec.NAME, host_process.NAME, imagegen.NAME,
             intents.TOOL, llm_task.NAME, manual_write.TOOL_NAME, mediagen.VIDEO, mediagen.MUSIC, nodes.NAME, patch.NAME, pdf.NAME,
             plan.ENTER, plan.PROPOSE, skill_market.TOOL, skill_write.TOOL_NAME, spill.NAME, stt.NAME, tasks.TOOL, todo.TOOL, tts.NAME,
             unattended.CRON_TOOL, video.NAME, vision.NAME, *sessions.TOOLS, *host_files.NAMES, *hands.RENAMED, *hands.RENAMED.values(),
             "web_search", "web_fetch", "memory_search", "memory_get", "skill", "manual", "tool_search", "tool_describe",
             *(set(bridges.BRIDGES) - {"tasks"})}
    unknown = steps.describe("no_such_tool", "")["label"]
    missing = sorted(n for n in names if steps.describe(n, "")["label"] == unknown)
    assert not missing


def test_every_policy_subcommand_has_a_label():
    """命令行工具的每个子命令（engine/tools.POLICY）都有自己的一句，不落到「处理…」那句默认。"""
    from engine import tools
    loose = []
    for tool, table in tools.POLICY.items():
        if tool not in steps.BRIDGE:
            continue
        for sub, node in table.items():
            for act in (node if isinstance(node, dict) else [""]):
                if " ".join(x for x in (sub, act) if x) not in steps.BRIDGE[tool] and sub not in steps.BRIDGE[tool]:
                    loose.append(" ".join(x for x in (tool, sub, act) if x))
    assert not loose
    assert set(tools.POLICY) - set(steps.BRIDGE) == {"tasks", "cron"}  # 这两个是内核自己的工具，按 action 配（steps.ACTIONS）
    assert {"tasks", "cron"} <= set(steps.ACTIONS)
