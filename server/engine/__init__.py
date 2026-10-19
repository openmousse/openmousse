"""OpenMousse 自己的内核（builtin）。10/15 定的：自己的内核，Python 写进服务器；借 pi、OpenClaw 的设计，三家都是 MIT。
交接和十步计划：~/openmousse-wt/engine/README.md；规矩（三种回合、关卡、事件流）：~/openmousse-wt/harness/README.md。

分工：
  models.py              模型引用 provider/model、各家接口和密钥、server.json 的 engine 段、价钱
  providers.py           按模型找到哪一家：provider_anthropic.py（官方 SDK）/ provider_openai.py（OpenAI 兼容接口）/
                         provider_responses.py（OpenAI 的 Responses API：api = openai-responses 的密钥、ChatGPT 套餐）
  fallback.py            备用链（第 13 步）：什么错换、换谁、冷却、一家多把密钥先换密钥；loop.py 每次调模型问它
  session.py             中立格式的消息、会话（内存 / 库里的一棵树，撤回 = 换叶子）
  loop.py                一轮：模型 → 工具 → 模型……；三种回合（owner / cyber / outsider）按回合卡工具；插话、后续、停
  events.py, store.py    事件流：对话 → 一轮 → 每一步（mousse.db 的 engine_* 四张表）+ 进程里的实时广播
  oneshot.py             一次性 JSON（名片 Agent、Doorman、llmjson.ask 改走它，能换模型）：默认外人回合；调用方报了主人自己的来路、
                         模型是他的 Claude Code 时交给 claude_brain.ask（走他的套餐，跑不了换按量的备用）
  cyber.py               赛博回合（后端 + 测试入口）
  tools.py               主人回合的工具：我们自己的命令行工具（bridges.py）+ 连接器 + skill；分类表和关卡（撤得回直接做，撤不回进收件箱）
  mcp_client.py          你自己配的 MCP 服务（第 13 步，server.json 的 mcp_servers）：本机 stdio 的常驻进程、写好令牌的远端，工具接进主人回合
  skill_write.py         写自己的 skill（第 13 步）：skill_manage 工具、改之前存历史、内容检查、提醒存、后台回看（默认关）
  agent_skills.py        每个 Agent 能用哪些技能（engine.skills.agents.<id>，10/19）：skills_for 管住开头的技能目录、skill 工具、经 /mcp 给外援的；
                         谁写名单：新建 Agent、装日结提案的 skill、app 里改（GET / PUT /api/setup/skills/{agent}，只认主人的设备、先备份）
  appdata.py             claw 选 builtin 时 app 的几页读内核（10/19 第 1 批）：/api/activity（engine_turns、engine_job_runs）、/api/models、
                         /api/security、/api/upcoming（定时任务和心跳，开关 = 暂停 / 恢复）、老 app 的 /api/approvals；样子和 OpenClaw 那条路对齐
  calls.py               不是命令行脚本、也要点头的调用（MCP 工具、写 skill）：交 kind tool 的卡，同意后照原样跑一次
  manuals.py             13c 手册和技能按这句话搜：每轮开头只带命中的正文（engine.manuals）、常驻目录只留名字、skill 工具的 query、
                         赛博回合只读的 manual 工具
  used.py                13c 用了哪条留记号：每轮真读到的手册、技能、叶子（engine_used 表 + 事件流），done 和回复那一行带上，
                         账本（多久没用、标旧）；/api/engine/used、/api/engine/skills
  improve.py             13c ⑬ 只对这一轮用过的提「改它」；改说明（模型想新说法、交 kind tool 的卡，点了才改）、停用（只停不删）
  manual_write.py        13c 改手册的 manual_manage：手册是做法（memgate），先交 kind tool 的卡，点了头照卡改一次，改之前留一版
  owner.py               claw 选 builtin 时的主对话：每个对话每个逻辑日一条会话，带这个 Agent 的工具和关卡
  context.py             主人回合的备料：会话开头放什么、各放多少（规矩文件、档案、记忆、技能、世界树、日结、上次聊到哪、项目卡）；变了往后补
  compact.py             压缩：太长了把前面的压成一条摘要条目，只在一轮的开头切，压完开头按当时的背景重写
  recall.py              记忆检索：MEMORY.md、日记、日结、共享资料和以前的对话的 FTS5 索引（中文切成两字词）；memory_search / memory_get
  embeddings.py          按意思搜的向量（engine.recall.embeddings，不配不开）：Gemini 的 embedContent / batchEmbedContents、OpenAI 兼容的
                         /embeddings（OpenAI、本机 Ollama……）；密钥照 models.env_value、限速、出错分类、报错里抹掉密钥
  semantic.py            向量存进 recall.db（float32 + 符号位）、后台慢慢补（不卡主人回合）、换型号重建；搜的时候汉明粗筛 + 余弦精算，
                         关键词和意思合分 × 新近 × 信几分，MMR 去掉差不多的；/api/engine/recall 的状态
  recall_tree.py         世界树的叶子进记忆检索（配了向量才有）：作者的 memory_tree.py 或开源包（读不了退回 TREE.md 导出），一片一段，按枝的范围
  memgate.py             记性的闸和读写表（13b / 13d）：四种记忆（经历 / 叶子 / 档案 / 做法）× 三种回合 × 谁的话 → 直接写 / 先提议 / 进待定 / 不做；
                         工具（skill_manage、主机文件、tree）和后台（写回、skill 回看、Zen、待定）都问它，每一次记进 engine_memory；读叶子的三档信；
                         loop 挑工具时照读写表再筛一遍（screen）；/api/engine/memory、/api/engine/memory/table
  writeback.py           对话结束时的写回（13b）：一段对话停下来（闲了 30 分钟 / 跨过 04:00 / 攒了 20 句）后台一次零工具的便宜调用列候选、打分，
                         服务器挑最多 3 条长成叶子、没选的留一笔，改做法进收件箱；名片 Agent 收到的别人的话挑出来进待定（../memhold.py）；只在 builtin
  when.py                定时任务的时间写法（每隔多久 / 每天几点 / 哪几天 / 某个时刻 / cron 五段）和「下一次什么时候」、补跑窗口
  jobs.py                定时任务（第 6 项）：表、cron 工具、收件箱卡（Agent 建的先停着，用户点了才开）、到点跑、接口 /api/engine/jobs；
                         10/19 起多一种只跑命令的（不调模型：BRIDGES 的工具或 engine.jobs.scripts 白名单里的脚本，有时限，输出记进运行记录）
  heartbeat.py           心跳：每个 Agent 可选的定期「看一眼」（server.json 开），没清单不调模型，没要说的回 NO_REPLY 什么都不发
  unattended.py          没人在看的一轮：新会话里跑、认「没要说的」、结果发进对话 + 推送 + 补进那个对话今天的会话
  tasks.py               后台任务和子 Agent：tasks 工具（派、看、接着说、停、重派、额度）、子 Agent 的工具子集和关卡、做完交回原对话、重启收尾
  sessions.py            第 1 批 b 看别的会话：sessions_list / sessions_history / session_status（只读；范围 engine.sessions：默认 Agent 看自己的、
                         main 看全部；读到的标成 [Inter-session message]、当资料）；handoff 放开成 Agent → Agent 以后的跳数（最多 2 跳）和圈、
                         转过去那句话标成别的会话转来（../chat.py 的 /api/chat/relay 用）
  agent_cli.py           第 1 批 b 命令行 mousse agent（../mousse.py）的服务器那一半：POST /api/engine/agent，一个新会话里只读地跑一轮，
                         回答、用了什么、多少 token 打成 JSON；模型不写用那个 Agent 的，chatgpt/… 照 models.may_spend 走用户自己的套餐
  ledger.py              后台任务的台账（engine_tasks 表；给 cards.py / data.py 的是 OpenClaw 台账的样子）
  runners.py             外援子 Agent：本机用户自己登录的官方 Claude Code / Codex，一次性，经 /mcp 用我们的工具（临时令牌、只读）
  sandbox.py             沙箱（第 8 项）：内核自己起、管、收 Docker 容器（加固、只经 Doorman 出网、闲了收掉、重启后认回）；容器里跑命令、读写文件
  browser.py             沙箱里的浏览器：经 CDP 开网页、页面大纲（带 ref）、点、填、按键、多页
  hands.py               沙箱里的手：在沙箱里干活的 Agent（代办）的全部工具（exec / process / read / write / edit / ls / browser）和开头的规矩；
                         ⑫ 起同一套换个名字（sandbox_exec……）给普通 Agent 自己的沙箱
  handkit.py             ⑫ 普通 Agent 的手：给谁什么、开关、分类怎么改、要点头的怎么交卡、会话开头讲手的那一节
  host_files.py          ⑫ 主机上的文件工具：自己工作区里读、列、找、搜、写、改、换回旧版本；路径守卫（..、符号链接、像密钥的、别人的）
  safefs.py, versions.py ⑫ 不跟符号链接的读写（openat 一层层走）；写之前留旧版本 / 恢复（通用的小模块）
  host_exec.py           ⑫ 主机上跑命令（照 OpenClaw 的执行审批）：默认全关；放行名单、危险命令识别、不经 shell、去掉像密钥的环境变量
  web.py                 ⑫ web_search（Brave、Tavily、Exa、Perplexity、SearXNG、DuckDuckGo）和 web_fetch（只去公网、钉住查过的地址、转成 Markdown）
  images.py              ⑫b 工具结果里的图：卡大小和缩放（照 pi）、存进图库（按内容取名，会话里只记引用）、发给各家前读回来或换成一句话、
                         老图成批换成字
  ask.py                 ⑫b ask_user：一轮里问用户一句、等他回答（插话或 /api/engine/answer），只给有人在看的那几轮
  todo.py                ⑫b 待办清单：todo 工具、engine_todos 表（一个对话一行），会话开头带上还没做完的
  plan.py                ⑫b 计划模式：只看不改、propose_plan 交卡、同意了退出计划模式接着做；/api/engine/plan_mode
  claude_brain.py        模型 claude-code/…：主人回合交给本机用户自己登录的官方 Claude Code 跑（用他自己的套餐），会话、开头、工具、关卡照旧；
                         主人自己的一次性调用（ask：一问一答、llmjson、Zen「想完了」、压缩摘要）交给它的 claude -p，零工具、不存会话，
                         和整轮对话共用同时跑的上限、排在它们后面
  chatgpt_plan.py        模型 chatgpt/…（第 11b 项）：用户自己的 ChatGPT 套餐（官方 Sign in with ChatGPT 给开源 / 本地应用的那条路）：授权、
                         令牌存在本机（0600）、刷新排队、撤销；推理走公开的 Responses API；谁能花照 models.may_spend（后台的要用户同意过）；
                         /api/engine/chatgpt…
  mcp_engine.py          内核的工具经 MCP 给外援：一轮一把临时令牌，看到的是这一轮的整张工具表，每次调用走内核同一套关卡
  catalog.py             第 9 步「通用的选择层」：服务商目录（云端二十几家、本机的 Ollama / LM Studio / vLLM / llama.cpp / MLX、命令行登录、
                         自定义地址、搜索、向量），每家的地址、密钥变量名、拿密钥的网址、怎么列模型、能力、核对过的官方页面；models.PROVIDERS 从它来
  probe.py               第 9 步：测一下（列模型，列不了发一句最小的请求；搜一句；算一个向量）、本机探测；报错不带密钥
  usage.py               第 9 步：用量（engine_turns 按天 / 模型 / 用途汇总 token 和花费；套餐的记「套餐、不计钱」）；向量的调用另记在
                         engine_embed_usage（embeddings.py 每成一个请求记一笔，攒一批再写），汇总成 embeddings 那一栏
  setup_api.py           第 9 步：/api/setup/…（第一次打开的引导、设置中心）：总览、服务商（存 / 测 / 删）、按用途选模型、搜索、记忆、用量；
                         只认主人的设备、密钥只进 secrets.env（../secretstore.py）、写 server.json 之前先备份；命令行的同一套在 ../setup_ctl.py
  api.py                 /api/engine/…
  openai_api.py          对外接口：OpenAI 兼容的 /v1（models、chat/completions，Agent 当成 model：openmousse/<agent id>），会话照 owner.py，
                         撤不回的照样进收件箱、调用方那边能当场点头；给 ACP 适配器的 /v1/openmousse/…。server.json 的 engine.api 开了才挂，
                         令牌只认 api、api-<agent id>
  acp.py                 ACP 适配器：编辑器以 stdio 起的独立小进程（只用标准库，经 /v1 跟服务器说话，不导入这里的任何模块）

这一层只在用到时才导入 anthropic SDK；不配 server.json 的 engine 段，服务里一切照旧。
"""
from __future__ import annotations

from .errors import EngineError
from .loop import Control, Result, Spec, Tool, ToolCtx, ToolFailure, ToolOutput, run_turn
from .models import default_model, fallback_allowed, route, valid_ref
from .oneshot import ask_json
from .session import DbSession, MemorySession, system_note, text_of, tool_result, user

__all__ = ["EngineError", "Control", "Result", "Spec", "Tool", "ToolCtx", "ToolFailure", "ToolOutput", "run_turn", "default_model",
           "fallback_allowed", "route", "valid_ref", "ask_json", "DbSession", "MemorySession", "system_note", "text_of", "tool_result", "user"]
