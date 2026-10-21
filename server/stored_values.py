"""存储和协议里的值（2026-10-20 收到这一处）：库里的老数据、老的 app、别人的服务器、OpenClaw 的会话列表和你自己的笔记都认这些值。
别改值：改了，老数据、老 app 就对不上了。代码里别处只引用这里的名字；名字是很早定下的，所以和项目名对不上。不导入别的模块（谁都能导入它）。
app 那边同样的值在 app/src/data/stored.ts。
"""
from __future__ import annotations

ASSISTANT_ROLE = "grava"                # messages.role：助手说的话（/api/chat 各接口、/api/chats 的 lineRole 也把它给 app）。老数据和老 app 认这个值，别改
SESSION_TAG = "grava"                   # OpenClaw 的会话键 agent:<Agent id>:grava:<线程> 中间那一段（OpenClaw 的会话列表里看得到，老会话按它找回来），别改
TREE_SOURCE = "grava"                   # 世界树叶子的来源：主对话写的（工作区那份世界树脚本收 --source；老叶子都是这个值），别改
TREE_AGENT_PREFIX = "grava-"            # 同上，Agent 写的：grava-<Agent id>，别改
NOTE_SOURCE_THINK = "grava-think"       # Zen 想完写进 Obsidian 库的笔记，frontmatter 的 source（你库里的笔记可能按它筛），别改
NOTE_SOURCE_PODCAST = "grava-podcast"   # 播客整理好写进库的笔记，同上，别改
