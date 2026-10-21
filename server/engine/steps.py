"""进度上的一步说人话（app 设计第 23 张，2026-10-20）：内核的工具名 + 那一次调用的标题（Tool.title，loop 的 tool_run 带来的 detail）→
  label   这一步做什么：动宾短语、陈述（「查看日程」「检索记忆」「运行命令」「派出后台任务」）
  doing   正在做时标题那一行（「正在查看日程」）
  detail  label 里没有、值得看一眼的：文件名、网址的域名、技能名、哪台设备、应用里的哪个工具、后台任务卡和计划的标题；没有就空着。
          不放命令、搜索词、提示词、要朗读的字——标题里有的也不放（任务卡和计划的标题本来就是写给人看的，放）。
chat.note_engine 给每一步加上 label / doing / note（= 这里的 detail）：只新增，原来的 detail（Tool.title）不动，老 app 和安卓 1.0.6
那条线不认新的几样，照旧画「调用工具 + 标题」。
认不出的工具：「使用工具」，detail 是工具名；连接器、接进来的 MCP 服务器、插件的工具（名字 <应用>__<工具>，标题「应用名 · 工具」）：
「使用 {应用名}」，detail 是那个工具。命令行工具（bridges.BRIDGES，标题是「工具 子命令 [动作]」，engine/tools.classify）按子命令、
动作配，越具体的越优先；认不出的子命令用那个工具自己的一句。
OpenClaw 那条路（对话通道，chat.note_progress）不经这里：工具名是 OpenClaw 的，app 照旧按自己的表配（Thinking.tsx）。

照 OpenClaw 的 tool display（src/agents/tool-display-config.ts、tool-display.ts，commit 14eb1b44：一个工具一个标题，参数里只挑一两样
当细节，多个动作的工具按 action 配，认不出的按名字）和 Hermes 的工具预览（agent/display.py、tools/tool_labels.py，commit 4eaeee1b：
一行写清在做什么，网址只留域名，MCP 的写「应用 · 工具」）的思路自己写的，没有拿代码。和它们不一样的：它们把搜索词、命令也放进细节，
这里不放（参数原文不上进度）。
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from i18n import L

# 一条 = (中文, English, English 进行时)；中文进行时 = 「正在」+ 中文
Say = tuple[str, str, str]

# —— 命令行工具（bridges.BRIDGES）：键是「工具 子命令 [动作]」，"" = 这个工具的默认 ——————————————————————————
BRIDGE: dict[str, dict[str, Say]] = {
    "schedule": {
        "": ("处理日程", "Work on the schedule", "Working on the schedule"),
        # day 默认是今天，也可能带 --date 看别的日子；标题里看不到参数，不写「今天」
        "day": ("查看日程", "Check the schedule", "Checking the schedule"),
        "remember": ("查看要记得的事", "Check things to remember", "Checking things to remember"),
        "log": ("查看日程的改动", "Check schedule changes", "Checking schedule changes"),
        "add": ("添加日程", "Add to the schedule", "Adding to the schedule"),
        "update": ("修改日程", "Change the schedule", "Changing the schedule"),
        "delete": ("删除日程", "Remove from the schedule", "Removing from the schedule"),
        "skip": ("标记跳过", "Mark as skipped", "Marking as skipped"),
        "attend": ("记录是否出席", "Record attendance", "Recording attendance"),
        "place": ("修改地点", "Change the place", "Changing the place"),
        "done": ("标记完成", "Mark as done", "Marking as done"),
        "dismiss": ("标记不处理", "Dismiss an item", "Dismissing an item"),
        "category": ("修改分类", "Change the category", "Changing the category"),
        "edit": ("修改待办", "Edit a to-do", "Editing a to-do"),
        "undo": ("撤销日程改动", "Undo a schedule change", "Undoing a schedule change"),
    },
    "goals": {
        "": ("处理目标", "Work on goals", "Working on goals"),
        "list": ("查看目标", "Check goals", "Checking goals"),
        "trend": ("查看身体读数", "Check body readings", "Checking body readings"),
        "log": ("查看目标的改动", "Check goal changes", "Checking goal changes"),
        "add": ("添加目标", "Add a goal", "Adding a goal"),
        "update": ("修改目标", "Update a goal", "Updating a goal"),
        "done": ("标记目标完成", "Mark a goal done", "Marking a goal done"),
        "drop": ("放下一个目标", "Drop a goal", "Dropping a goal"),
        "reopen": ("重新开始一个目标", "Reopen a goal", "Reopening a goal"),
        "undo": ("撤销目标改动", "Undo a goal change", "Undoing a goal change"),
    },
    "inbox": {
        "": ("处理收件箱", "Work on the inbox", "Working on the inbox"),
        "add": ("提交到收件箱", "Add to your inbox", "Adding to your inbox"),
        "update": ("修改收件箱事项", "Update an inbox item", "Updating an inbox item"),
        "done": ("报告已完成", "Report it done", "Reporting it done"),
        "fail": ("报告未完成", "Report it didn't work", "Reporting it didn't work"),
        "withdraw": ("撤回收件箱事项", "Withdraw an inbox item", "Withdrawing an inbox item"),
        "list": ("查看收件箱", "Check the inbox", "Checking the inbox"),
        "get": ("查看收件箱事项", "Read an inbox item", "Reading an inbox item"),
    },
    "project": {
        "": ("处理项目", "Work on a project", "Working on a project"),
        "list": ("查看项目", "Check projects", "Checking projects"),
        "show": ("查看项目卡", "Read a project card", "Reading a project card"),
        "create": ("新建项目", "Create a project", "Creating a project"),
        "propose": ("提议新项目", "Propose a project", "Proposing a project"),
        "goal": ("修改项目目标", "Update the project goal", "Updating the project goal"),
        "progress": ("更新项目进度", "Update project progress", "Updating project progress"),
        "rename": ("重命名项目", "Rename a project", "Renaming a project"),
        "add": ("添加到项目卡", "Add to a project card", "Adding to a project card"),
        "link": ("关联到项目", "Link to a project", "Linking to a project"),
        "done": ("标记项目事项完成", "Mark a project item done", "Marking a project item done"),
        "edit": ("修改项目卡", "Edit a project card", "Editing a project card"),
        "remove": ("从项目卡移除", "Remove from a project card", "Removing from a project card"),
        "undo": ("撤销项目改动", "Undo a project change", "Undoing a project change"),
        "ask": ("转给项目对话", "Pass it to the project chat", "Passing it to the project chat"),
        "conclude": ("写项目结论", "Write the project's conclusion", "Writing the project's conclusion"),
        "archive": ("归档项目", "Archive a project", "Archiving a project"),
        "review": ("询问是否归档项目", "Ask about archiving a project", "Asking about archiving a project"),
    },
    "board": {
        "": ("处理看板", "Work on the board", "Working on the board"),
        "show": ("查看看板", "Check the board", "Checking the board"),
        "get": ("读取看板配置", "Read the board's setup", "Reading the board's setup"),
        "table": ("查看数据表", "Check tables", "Checking tables"),
        "table list": ("查看数据表", "Check tables", "Checking tables"),
        "table add": ("新建数据表", "Create a table", "Creating a table"),
        "table alter": ("修改数据表", "Change a table", "Changing a table"),
        "table archive": ("归档数据表", "Archive a table", "Archiving a table"),
        "rows": ("查询数据", "Look up data", "Looking up data"),
        "rows add": ("记录数据", "Log data", "Logging data"),
        "rows update": ("修改数据", "Update data", "Updating data"),
        "rows delete": ("删除数据", "Delete data", "Deleting data"),
        "rows restore": ("恢复数据", "Restore data", "Restoring data"),
        "rows query": ("查询数据", "Look up data", "Looking up data"),
        "rows list": ("查询数据", "Look up data", "Looking up data"),
        "check": ("校验看板", "Check the board's setup", "Checking the board's setup"),
        "apply": ("更新看板", "Update the board", "Updating the board"),
        "propose": ("提议看板改动", "Propose a board change", "Proposing a board change"),
        "pack": ("查看功能包", "Check feature packs", "Checking feature packs"),
        "pack install": ("安装功能包", "Install a feature pack", "Installing a feature pack"),
        "pack remove": ("移除功能包", "Remove a feature pack", "Removing a feature pack"),
        "alert": ("查看提醒", "Check reminders", "Checking reminders"),
        "alert check": ("校验提醒", "Check a reminder", "Checking a reminder"),
        "alert propose": ("提议新提醒", "Propose a reminder", "Proposing a reminder"),
        "alert pause": ("暂停提醒", "Pause a reminder", "Pausing a reminder"),
        "alert resume": ("恢复提醒", "Resume a reminder", "Resuming a reminder"),
        "alert delete": ("删除提醒", "Delete a reminder", "Deleting a reminder"),
        "view": ("查看视图", "Check views", "Checking views"),
        "view add": ("修改视图", "Change a view", "Changing a view"),
        "view update": ("修改视图", "Change a view", "Changing a view"),
        "view remove": ("修改视图", "Change a view", "Changing a view"),
        "view default": ("修改视图", "Change a view", "Changing a view"),
        "history": ("查看看板的版本", "Check board versions", "Checking board versions"),
        "revert": ("恢复看板的旧版本", "Restore an earlier board", "Restoring an earlier board"),
    },
    "study": {
        "": ("处理课程", "Work on a course", "Working on a course"),
        "courses": ("查看课程", "Check courses", "Checking courses"),
        "show": ("查看课程安排", "Read a course", "Reading a course"),
        "check": ("检查课程缺什么", "Check what a course is missing", "Checking what a course is missing"),
        "log": ("查看课程的改动", "Check course changes", "Checking course changes"),
        "session": ("修改课节", "Change a session", "Changing a session"),
        "reading": ("修改阅读", "Change the readings", "Changing the readings"),
        "ddl": ("修改截止", "Change a deadline", "Changing a deadline"),
        "file": ("更新课程资料", "Update course files", "Updating course files"),
        "assign": ("更新课程资料", "Update course files", "Updating course files"),
        "answer": ("记录答案", "Save an answer", "Saving an answer"),
        "style": ("修改学习页的写法", "Change the study page style", "Changing the study page style"),
        "syllabus": ("读取课程大纲", "Read the syllabus", "Reading the syllabus"),
        "generate": ("生成学习页", "Write study pages", "Writing study pages"),
        "undo": ("撤销课程改动", "Undo a course change", "Undoing a course change"),
        "create": ("新建课程", "Create a course", "Creating a course"),
        "delete": ("删除课程", "Delete a course", "Deleting a course"),
    },
    "agents": {
        "": ("管理 Agent", "Manage Agents", "Managing Agents"),
        "list": ("查看 Agent", "Check Agents", "Checking Agents"),
        "create": ("新建 Agent", "Create an Agent", "Creating an Agent"),
        "update": ("修改 Agent", "Update an Agent", "Updating an Agent"),
        "delete": ("删除 Agent", "Delete an Agent", "Deleting an Agent"),
    },
    "handoff": {
        "": ("转交给 Agent", "Hand off to an Agent", "Handing off to an Agent"),
        "--list": ("查看有哪些 Agent", "Check which Agents there are", "Checking which Agents there are"),
        "--meet": ("发起会议", "Start a meeting", "Starting a meeting"),
        "*": ("转交给 Agent", "Hand off to an Agent", "Handing off to an Agent"),
    },
    "proposals": {
        "": ("处理提案", "Work on proposals", "Working on proposals"),
        "context": ("回看材料", "Review the material", "Reviewing the material"),
        "list": ("查看提案", "Check proposals", "Checking proposals"),
        "show": ("查看提案", "Read a proposal", "Reading a proposal"),
        "skill": ("提议新技能", "Propose a skill", "Proposing a skill"),
        "agent": ("提议新 Agent", "Propose an Agent", "Proposing an Agent"),
        "withdraw": ("撤回提案", "Withdraw a proposal", "Withdrawing a proposal"),
    },
    "journal": {
        "": ("处理日志", "Work on the journal", "Working on the journal"),
        "add": ("记一条日志", "Add a journal entry", "Adding a journal entry"),
        "list": ("翻看日志", "Read the journal", "Reading the journal"),
        "search": ("搜索日志", "Search the journal", "Searching the journal"),
        "delete": ("删除一条日志", "Delete a journal entry", "Deleting a journal entry"),
    },
    "diet": {
        "": ("处理饮食记录", "Work on the meal log", "Working on the meal log"),
        "day": ("查看饮食记录", "Check the meal log", "Checking the meal log"),
        "days": ("查看每日饮食合计", "Check daily meal totals", "Checking daily meal totals"),
        "library": ("查看常吃的", "Check usual meals", "Checking usual meals"),
        "foods": ("查看食物库", "Check the food library", "Checking the food library"),
        "lookup": ("查询营养", "Look up nutrition", "Looking up nutrition"),
        "targets": ("查看营养目标", "Check nutrition targets", "Checking nutrition targets"),
        "targets show": ("查看营养目标", "Check nutrition targets", "Checking nutrition targets"),
        "targets set": ("修改营养目标", "Change nutrition targets", "Changing nutrition targets"),
        "food-save": ("存入食物库", "Save a food", "Saving a food"),
        "food-add": ("存入食物库", "Save a food", "Saving a food"),
        "food-update": ("修改食物库", "Update a food", "Updating a food"),
        "food-rm": ("从食物库删除", "Remove a food", "Removing a food"),
        "add": ("记录饮食", "Log a meal", "Logging a meal"),
        "log-plan": ("按建议记录饮食", "Log the planned meal", "Logging the planned meal"),
        "edit": ("修改饮食记录", "Edit a meal entry", "Editing a meal entry"),
        "rm": ("删除饮食记录", "Delete a meal entry", "Deleting a meal entry"),
        "undo": ("撤销饮食记录", "Undo a meal log", "Undoing a meal log"),
        "day-type": ("标记训练日或休息日", "Set the day type", "Setting the day type"),
    },
    "settings": {
        "": ("查看设置", "Check settings", "Checking settings"),
        "user-name": ("设置称呼", "Set what to call you", "Setting what to call you"),
    },
    "tree": {
        "": ("使用世界树", "Use the memory tree", "Using the memory tree"),
        "add": ("记下一条记忆", "Save a memory", "Saving a memory"),
        "recall": ("检索世界树", "Search the memory tree", "Searching the memory tree"),
        "recent": ("查看最近的记忆", "Check recent memories", "Checking recent memories"),
        "forget": ("忘掉一条记忆", "Forget a memory", "Forgetting a memory"),
        "stats": ("查看世界树概况", "Check the memory tree", "Checking the memory tree"),
        "profile": ("读取档案", "Read the profile", "Reading the profile"),
        "branches": ("查看世界树的枝", "Check the tree's branches", "Checking the tree's branches"),
        "prune-context": ("准备每周修剪", "Prepare the weekly pruning", "Preparing the weekly pruning"),
        "prune-plan": ("提交修剪计划", "Submit a pruning plan", "Submitting a pruning plan"),
        "manual": ("读取手册", "Read a manual", "Reading a manual"),
    },
    "page": {
        "": ("处理页面", "Work on a page", "Working on a page"),
        "check": ("检查页面", "Check a page", "Checking a page"),
        "list": ("查看页面", "Check pages", "Checking pages"),
        "show": ("查看页面", "Read a page", "Reading a page"),
        "get": ("取回页面", "Fetch a page", "Fetching a page"),
        "data": ("读取页面数据", "Read page data", "Reading page data"),
        "publish": ("发布页面", "Publish a page", "Publishing a page"),
        "rm": ("删除页面", "Delete a page", "Deleting a page"),
    },
}  # tasks 不在这里：内核换成了自己的 tasks 工具（engine/tasks.py，engine/tools.NOT_YET），见 ACTIONS

# —— 一个工具一句（没有子命令；文件、网址的取细节见 DETAIL）——————————————————————————————————————
ONE: dict[str, Say] = {
    # 命令和文件（代办在沙箱里；普通 Agent 的沙箱、主机上的文件）
    "exec": ("运行命令", "Run a command", "Running a command"),
    "sandbox_exec": ("在沙箱里运行命令", "Run a command in the sandbox", "Running a command in the sandbox"),
    "host_exec": ("在主机上运行命令", "Run a command on the server", "Running a command on the server"),
    "execute_code": ("运行代码", "Run code", "Running code"),
    "read": ("读取文件", "Read a file", "Reading a file"),
    "sandbox_read": ("读取文件", "Read a file", "Reading a file"),
    "read_file": ("读取文件", "Read a file", "Reading a file"),
    "write": ("写入文件", "Write a file", "Writing a file"),
    "sandbox_write": ("写入文件", "Write a file", "Writing a file"),
    "write_file": ("写入文件", "Write a file", "Writing a file"),
    "edit": ("修改文件", "Edit a file", "Editing a file"),
    "sandbox_edit": ("修改文件", "Edit a file", "Editing a file"),
    "edit_file": ("修改文件", "Edit a file", "Editing a file"),
    "apply_patch": ("修改文件", "Edit files", "Editing files"),
    "restore_file": ("恢复文件的旧版本", "Restore a file", "Restoring a file"),
    "ls": ("查看文件夹", "List a folder", "Listing a folder"),
    "sandbox_ls": ("查看文件夹", "List a folder", "Listing a folder"),
    "list_dir": ("查看文件夹", "List a folder", "Listing a folder"),
    "find_files": ("查找文件", "Find files", "Finding files"),
    "grep_files": ("搜索文件内容", "Search file contents", "Searching file contents"),
    "diff": ("比较两个版本", "Compare versions", "Comparing versions"),
    "read_output": ("翻看工具输出", "Page through tool output", "Paging through tool output"),
    # 网页
    "web_search": ("搜索网页", "Search the web", "Searching the web"),
    "WebSearch": ("搜索网页", "Search the web", "Searching the web"),  # Claude Code 自带的搜索（engine.claude_code.builtin_tools）
    "web_fetch": ("打开网页", "Open a web page", "Opening a web page"),
    # 记忆、会话
    "memory_search": ("检索记忆", "Search memory", "Searching memory"),
    "memory_get": ("读取记忆", "Read memory", "Reading memory"),
    "sessions_list": ("查看其他会话", "Check other sessions", "Checking other sessions"),
    "sessions_history": ("读取其他会话", "Read another session", "Reading another session"),
    "session_status": ("查看会话状态", "Check a session's status", "Checking a session's status"),
    # 工具目录、技能、手册
    "tool_search": ("查找可用工具", "Find a tool", "Finding a tool"),
    "tool_describe": ("查看工具说明", "Read a tool's description", "Reading a tool's description"),
    "skill": ("读取技能", "Read a skill", "Reading a skill"),
    "manual": ("读取手册", "Read a manual", "Reading a manual"),
    # 问你、计划
    "ask_user": ("向你提问", "Ask you a question", "Asking you a question"),
    "enter_plan_mode": ("进入计划模式", "Enter plan mode", "Entering plan mode"),
    "propose_plan": ("提交计划", "Submit a plan", "Submitting a plan"),
    "heartbeat_respond": ("汇报检查结果", "Report the check-in", "Reporting the check-in"),
    # 看、听、生成
    "view_image": ("查看图片", "View an image", "Viewing an image"),
    "read_pdf": ("读取 PDF", "Read a PDF", "Reading a PDF"),
    "transcribe": ("转写录音", "Transcribe a recording", "Transcribing a recording"),
    "video_analyze": ("查看视频", "Watch a video", "Watching a video"),
    "image_generate": ("生成图片", "Generate an image", "Generating an image"),
    "speak": ("朗读", "Read aloud", "Reading aloud"),
    "video_generate": ("生成视频", "Generate a video", "Generating a video"),
    "music_generate": ("生成音乐", "Generate music", "Generating music"),
    "llm_task": ("调用模型", "Call a model", "Calling a model"),
}

# —— 按 action 分的（标题的第一个词是 action）——————————————————————————————————————————————————————
ACTIONS: dict[str, dict[str, Say]] = {
    "process": {
        "": ("管理后台命令", "Manage background commands", "Managing background commands"),
        "list": ("查看后台命令", "Check background commands", "Checking background commands"),
        "poll": ("查看后台命令的输出", "Check a command's output", "Checking a command's output"),
        "log": ("查看后台命令的输出", "Check a command's output", "Checking a command's output"),
        "write": ("向后台命令输入", "Send input to a command", "Sending input to a command"),
        "kill": ("停止后台命令", "Stop a background command", "Stopping a background command"),
    },
    "browser": {
        "": ("使用浏览器", "Use the browser", "Using the browser"),
        "open": ("打开网页", "Open a web page", "Opening a web page"),
        "snapshot": ("读取网页", "Read the page", "Reading the page"),
        "text": ("读取网页", "Read the page", "Reading the page"),
        "screenshot": ("截取网页", "Take a screenshot", "Taking a screenshot"),
        "pdf": ("把网页存成 PDF", "Save the page as a PDF", "Saving the page as a PDF"),
        "tabs": ("查看标签页", "Check tabs", "Checking tabs"),
        "tab": ("切换标签页", "Switch tabs", "Switching tabs"),
        "wait": ("等待网页加载", "Wait for the page", "Waiting for the page"),
        "scroll": ("滚动网页", "Scroll the page", "Scrolling the page"),
        "click": ("点击网页", "Click on the page", "Clicking on the page"),
        "type": ("在网页里输入", "Type on the page", "Typing on the page"),
        "select": ("在网页里选择", "Choose on the page", "Choosing on the page"),
        "press": ("在网页里按键", "Press a key", "Pressing a key"),
        "back": ("浏览网页", "Go back a page", "Going back a page"),
        "forward": ("浏览网页", "Go forward a page", "Going forward a page"),
        "reload": ("刷新网页", "Reload the page", "Reloading the page"),
        "evaluate": ("在网页里运行脚本", "Run a script on the page", "Running a script on the page"),
        "close": ("关闭标签页", "Close a tab", "Closing a tab"),
    },
    "chrome": {
        "": ("使用你的 Chrome", "Use your Chrome", "Using your Chrome"),
        "open": ("在 Chrome 中打开网页", "Open a page in Chrome", "Opening a page in Chrome"),
        "snapshot": ("读取 Chrome 中的网页", "Read the page in Chrome", "Reading the page in Chrome"),
        "text": ("读取 Chrome 中的网页", "Read the page in Chrome", "Reading the page in Chrome"),
        "screenshot": ("截取 Chrome 中的网页", "Screenshot the page in Chrome", "Screenshotting the page in Chrome"),
        "pdf": ("把 Chrome 中的网页存成 PDF", "Save the Chrome page as a PDF", "Saving the Chrome page as a PDF"),
        "tabs": ("查看 Chrome 的标签页", "Check Chrome tabs", "Checking Chrome tabs"),
        "tab": ("切换 Chrome 的标签页", "Switch Chrome tabs", "Switching Chrome tabs"),
        "wait": ("等待网页加载", "Wait for the page", "Waiting for the page"),
        "scroll": ("在 Chrome 中滚动网页", "Scroll in Chrome", "Scrolling in Chrome"),
        "click": ("在 Chrome 中点击", "Click in Chrome", "Clicking in Chrome"),
        "type": ("在 Chrome 中输入", "Type in Chrome", "Typing in Chrome"),
        "select": ("在 Chrome 中选择", "Choose in Chrome", "Choosing in Chrome"),
        "press": ("在 Chrome 中按键", "Press a key in Chrome", "Pressing a key in Chrome"),
        "back": ("在 Chrome 中后退", "Go back in Chrome", "Going back in Chrome"),
        "forward": ("在 Chrome 中前进", "Go forward in Chrome", "Going forward in Chrome"),
        "reload": ("刷新 Chrome 中的网页", "Reload the page in Chrome", "Reloading the page in Chrome"),
        "evaluate": ("在 Chrome 中运行脚本", "Run a script in Chrome", "Running a script in Chrome"),
        "close": ("关闭 Chrome 的标签页", "Close a Chrome tab", "Closing a Chrome tab"),
    },
    "nodes": {
        "": ("使用设备", "Use a device", "Using a device"),
        "list": ("查看设备", "Check devices", "Checking devices"),
        "describe": ("查看设备能做什么", "Check what a device can do", "Checking what a device can do"),
        "notify": ("向设备发送通知", "Send a notification", "Sending a notification"),
        "camera_snap": ("用设备拍照", "Take a photo", "Taking a photo"),
        "location_get": ("获取位置", "Get the location", "Getting the location"),
        "screen_snapshot": ("截取屏幕", "Capture the screen", "Capturing the screen"),
        "run": ("在设备上运行命令", "Run a command on a device", "Running a command on a device"),
        "which": ("查找设备上的命令", "Find a command on a device", "Finding a command on a device"),
        "file_list": ("查看设备上的文件", "List files on a device", "Listing files on a device"),
        "file_read": ("读取设备上的文件", "Read a file on a device", "Reading a file on a device"),
        "file_write": ("写入设备上的文件", "Write a file on a device", "Writing a file on a device"),
        "computer": ("操作电脑", "Control the computer", "Controlling the computer"),
    },
    # 标题是「工具名 动作 …」的几样：第一个词是工具名，跳过
    "cron": {
        "": ("管理定时任务", "Manage scheduled jobs", "Managing scheduled jobs"),
        "list": ("查看定时任务", "Check scheduled jobs", "Checking scheduled jobs"),
        "create": ("新建定时任务", "Create a scheduled job", "Creating a scheduled job"),
        "update": ("修改定时任务", "Update a scheduled job", "Updating a scheduled job"),
        "pause": ("暂停定时任务", "Pause a scheduled job", "Pausing a scheduled job"),
        "resume": ("恢复定时任务", "Resume a scheduled job", "Resuming a scheduled job"),
        "delete": ("删除定时任务", "Delete a scheduled job", "Deleting a scheduled job"),
        "run": ("立即运行定时任务", "Run a scheduled job now", "Running a scheduled job now"),
    },
    "remember_when": {
        "": ("记下要提醒的事", "Note something to bring up", "Noting something to bring up"),
        "create": ("记下要提醒的事", "Note something to bring up", "Noting something to bring up"),
        "list": ("查看要提醒的事", "Check things to bring up", "Checking things to bring up"),
        "cancel": ("撤掉一条提醒", "Cancel a reminder", "Cancelling a reminder"),
    },
    "skill_manage": {  # 标题「skill 动作 名字」
        "": ("修改技能", "Edit a skill", "Editing a skill"),
        "create": ("新建技能", "Create a skill", "Creating a skill"),
        "patch": ("修改技能", "Edit a skill", "Editing a skill"),
        "edit": ("修改技能", "Edit a skill", "Editing a skill"),
        "write_file": ("修改技能", "Edit a skill", "Editing a skill"),
        "remove_file": ("修改技能", "Edit a skill", "Editing a skill"),
        "delete": ("删除技能", "Delete a skill", "Deleting a skill"),
        "history": ("查看技能的历史", "Check a skill's history", "Checking a skill's history"),
        "restore": ("恢复技能的旧版本", "Restore a skill", "Restoring a skill"),
    },
    "skill_market": {
        "": ("浏览技能市场", "Browse the skill market", "Browsing the skill market"),
        "search": ("搜索技能市场", "Search the skill market", "Searching the skill market"),
        "list": ("查看已装的技能", "Check installed skills", "Checking installed skills"),
        "install": ("安装技能", "Install a skill", "Installing a skill"),
        "update": ("更新技能", "Update a skill", "Updating a skill"),
        "remove": ("移除技能", "Remove a skill", "Removing a skill"),
    },
    "manual_manage": {  # 标题「manual 动作 名字」
        "": ("修改手册", "Edit a manual", "Editing a manual"),
        "add": ("新增手册", "Add a manual", "Adding a manual"),
        "edit": ("修改手册", "Edit a manual", "Editing a manual"),
        "disable": ("停用手册", "Turn off a manual", "Turning off a manual"),
        "enable": ("启用手册", "Turn on a manual", "Turning on a manual"),
        "rm": ("删除手册", "Delete a manual", "Deleting a manual"),
        "restore": ("恢复手册的旧版本", "Restore a manual", "Restoring a manual"),
        "history": ("查看手册的历史", "Check a manual's history", "Checking a manual's history"),
    },
    "tasks": {  # 内核自己的 tasks 工具（engine/tasks.py）：派活的标题是「派后台任务：卡片标题」，别的是「tasks 动作 [id]」
        "": ("查看后台任务", "Check background tasks", "Checking background tasks"),
        "spawn": ("派出后台任务", "Start a background task", "Starting a background task"),
        "list": ("查看后台任务", "Check background tasks", "Checking background tasks"),
        "get": ("查看后台任务", "Check a background task", "Checking a background task"),
        "send": ("给后台任务补充说明", "Message a background task", "Messaging a background task"),
        "stop": ("停止后台任务", "Stop a background task", "Stopping a background task"),
        "retry": ("重派后台任务", "Retry a background task", "Retrying a background task"),
        "quota": ("查看任务额度", "Check the task allowance", "Checking the task allowance"),
    },
}
ACTIONS["sandbox_process"] = ACTIONS["host_process"] = ACTIONS["process"]  # 普通 Agent 的沙箱、主机上的后台命令：标题一样是「动作 id」
NAMED = {"cron", "remember_when", "skill_manage", "manual_manage", "tasks", "skill_market"}  # 标题第一个词是工具名（或它的简称）
NODE_PLAIN = {"describe", "notify", "camera_snap", "location_get", "screen_snapshot"}  # nodes 里不带命令、路径的几样（engine/nodes.py 的标题）
TODO_READ = ("看待办清单", "Read the to-do list")
TODO: tuple[Say, Say] = (("查看待办清单", "Check the to-do list", "Checking the to-do list"),
                         ("更新待办清单", "Update the to-do list", "Updating the to-do list"))
MEDIA_STATUS: Say = ("查看生成进度", "Check generation progress", "Checking generation progress")
SPAWNED = ("派后台任务：", "Background task: ")  # engine/tasks.py 的派活标题（两种语言都可能：标题是派的那一轮的语言）
PROPOSED = ("交计划：", "Plan: ")                # engine/plan.py 的交计划标题
UNKNOWN: Say = ("使用工具", "Use a tool", "Using a tool")
APP = ("使用 {app}", "Use {app}", "Using {app}")

FILES = {"read", "sandbox_read", "read_file", "write", "sandbox_write", "write_file", "edit", "sandbox_edit", "edit_file", "restore_file",
         "view_image", "read_pdf", "transcribe", "video_analyze", "diff"}
FOLDERS = {"ls", "sandbox_ls", "list_dir"}
DETAIL_MAX = 60
NAME_RE = re.compile(r"^[\w.-]{1,64}$")  # 技能、手册的名字（engine/agent_skills.NAME）：一句话搜的 query 不算名字
ATTACHMENT = re.compile(r"^[0-9a-f]{12}$")  # 这个对话的附件 id（files.py）：看图、读 PDF 时给的是它，不是文件名


def _say(s: Say, **fmt: str) -> tuple[str, str]:
    zh, en, en_doing = (x.format(**fmt) for x in s)
    return L(zh, en), L(f"正{zh}" if zh.startswith("在") else f"正在{zh}", en_doing)  # 「在 Chrome 中点击」→「正在 Chrome 中点击」


def _clip(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= DETAIL_MAX else text[: DETAIL_MAX - 1] + "…"


def _base(path: str) -> str:
    """路径只留最后一段（文件名、文件夹名）；网址留域名；当前目录这种没有意思的不留。"""
    p = path.strip().strip("'\"")
    if not p:
        return ""
    if re.match(r"^[a-z][a-z0-9+.-]*://", p, re.I):
        return host(p)
    if p in ("/workspace", "/workspace/"):  # 沙箱的工作目录（engine/sandbox.WORKDIR）：ls 没写路径时的标题
        return ""
    name = p.rstrip("/").rsplit("/", 1)[-1]
    return "" if name in ("", ".", "..", "~") else name


def host(url: str) -> str:
    """网址 → 域名（去掉 www.、端口、账号）；不像网址的 → ""。"""
    try:
        h = urlsplit(url.strip() if "://" in url else f"https://{url.strip()}").hostname or ""
    except ValueError:
        return ""
    h = h.removeprefix("www.")
    return h if "." in h or h == "localhost" else ""


def _bridge(name: str, title: str) -> Say:
    """「工具 子命令 [动作]」→ 最具体的那一句；认不出的子命令用这个工具的默认。"""
    table = BRIDGE[name]
    words = title.split()
    if words and words[0] == name:
        words = words[1:]
    for n in range(min(len(words), 2), 0, -1):
        if (say := table.get(" ".join(words[:n]))) is not None:
            return say
    return table[""]


def describe(name: str, title: str) -> dict[str, str]:
    """一步 → {"label", "doing", "detail"}（按这个请求的语言）。title 是 loop 的 tool_run 带来的那一句（Tool.title）。"""
    name, title = str(name or "").strip(), str(title or "").strip()
    detail = ""
    if name in BRIDGE:
        say = _bridge(name, title)
    elif name in ACTIONS:
        words = title.split()
        if name in NAMED and words and words[0] in (name, name.split("_")[0]):
            words = words[1:]
        say = ACTIONS[name].get(words[0].lower() if words else "", ACTIONS[name][""])
        if name == "tasks" and title.startswith(SPAWNED):  # 派活：卡片标题当细节（它本来就是给人看的标题）
            say, detail = ACTIONS["tasks"]["spawn"], title.split("：" if title.startswith(SPAWNED[0]) else ": ", 1)[1]
        elif name in ("browser", "chrome") and len(words) > 1 and words[0].lower() == "open":
            detail = host(words[1])
        elif name == "nodes" and len(words) == 2 and words[0].lower() in NODE_PLAIN:
            detail = words[1]  # 哪台设备（标题「动作 设备」）；带命令、路径的几样没写设备时第二个词就是命令、路径，分不出来，不放
        elif name in ("skill_manage", "manual_manage") and len(words) > 1 and NAME_RE.match(words[1]):
            detail = words[1]
    elif name == "todo":
        say = TODO[0] if title in TODO_READ else TODO[1]
    elif name in ("video_generate", "music_generate", "image_generate") and title.lower() == "status":
        say = MEDIA_STATUS
    elif name == "tool_call":
        return _inner(title)
    elif name in ONE:
        say = ONE[name]
        if name in FILES or name in FOLDERS:
            detail = "" if ATTACHMENT.match(title) else _base(title)
        elif name == "apply_patch":  # 标题「a.py, b/c.py (+2)」：只留文件名
            more = re.search(r"\s*\(\+\d+\)$", title)
            paths = (title[: more.start()] if more else title).split(", ")
            detail = ", ".join(x for x in map(_base, paths) if x and x != name)
            detail += f" {more.group(0).strip()}" if more and detail else ""
        elif name == "web_fetch":
            detail = host(title)
        elif name in ("skill", "manual"):
            word = title.split(None, 1)[1] if len(title.split(None, 1)) > 1 else ""
            detail = word if NAME_RE.match(word) else ""
        elif name == "propose_plan" and title.startswith(PROPOSED):
            detail = title.split("：" if title.startswith(PROPOSED[0]) else ": ", 1)[1]
    elif "__" in name:  # 连接器、MCP 服务器、插件的工具：<应用>__<工具>，标题「应用名 · 工具」
        app, _, tool = title.partition(" · ")
        if not tool:
            app, tool = name.split("__", 1)
        label, doing = _say(APP, app=_clip(app.strip()) or name)
        return {"label": label, "doing": doing, "detail": _clip(tool.strip())}
    else:
        label, doing = _say(UNKNOWN)
        return {"label": label, "doing": doing, "detail": name}
    label, doing = _say(say)
    return {"label": label, "doing": doing, "detail": _clip(detail)}


def _inner(title: str) -> dict[str, str]:
    """tool_call（按名字调目录里的工具，engine/toolsearch.py）：标题是被调的那个工具的标题。认得出的（命令行工具、应用的工具）照它配，
    别的写「使用工具」，不放标题（可能是参数原文）。"""
    first = title.split(None, 1)[0] if title else ""
    if first in BRIDGE:
        return describe(first, title)
    if " · " in title:
        app, _, tool = title.partition(" · ")
        label, doing = _say(APP, app=_clip(app.strip()))
        return {"label": label, "doing": doing, "detail": _clip(tool.strip())}
    label, doing = _say(UNKNOWN)
    return {"label": label, "doing": doing, "detail": ""}
