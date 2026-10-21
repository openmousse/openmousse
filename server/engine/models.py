"""模型和接口：引用一律写成「provider/model」（和 OpenClaw 一样：anthropic/claude-opus-5-5、moonshot/kimi-k3、deepseek/deepseek-flash）。

provider = 谁家的接口、地址、密钥放哪。三处按顺序叠（后面的盖前面的）：
  1. 内置的常见几家（PROVIDERS）
  2. openclaw.json 的 models.providers（这台机器装着 OpenClaw 时：地址、接口类型、apiKey 写成 ${VAR} 就认这个变量名；
     Google 官方的地址 OpenClaw 写的是 Gemini 自己的接口 …/v1beta，换成目录里 OpenAI 兼容的那个，见 _openclaw_url）
  3. server.json 的 engine.providers：{"名字": {"api": "openai" | "anthropic" | "openai-responses", "url": ".../v1", "key_env": "变量名",
     "headers": {…}, "json_mode": true, "reasoning_effort": false, "fallbacks": true, "block_binding": "drop_block", …各家脾气见 PROVIDERS}}，
     可以加新的（本机的 Ollama、公司的代理），也可以只改一项（比如 moonshot 换成国内地址 https://api.moonshot.cn/v1）
api：anthropic = 官方 SDK（只认 API 密钥：Anthropic 条款不许第三方产品借 Claude 订阅登录）；openai = OpenAI 兼容的 /chat/completions
（Kimi、DeepSeek、Qwen、Gemini、OpenRouter、Ollama、LM Studio、vLLM…）；openai-responses = OpenAI 的 Responses API（engine/provider_responses.py：
思考项加密回放；普通 OpenAI 密钥写 {"openai": {"api": "openai-responses"}} 就换过去，不写的照旧走 /chat/completions；还可以写
"reasoning": false 给不会思考的型号、"effort_map"）。provider 名 chatgpt 留给用户自己的 ChatGPT 套餐（见下），不能另作他用。
密钥：key_env 指环境变量名（默认 <PROVIDER>_API_KEY），先看进程环境、再看 app 里存的 secrets.env（第 9 步，secretstore.py）、
再看 server.json 的 env_file（默认 <openclaw_home>/.env）。每次现读，改了不用重启；不打印、不记日志。
第 9 步（通用的选择层）：内置的这几家来自 engine/catalog.py 的服务商目录（云端、本机的二十几家，地址都核对过官方文档），设置页在
/api/setup/…（engine/setup_api.py）。一家有好几把密钥时 key_env 写成列表（["DEEPSEEK_API_KEY", "DEEPSEEK_API_KEY_2"]）：
按顺序用第一把没在冷却的，这一把限流、计费错、不认了就先换下一把，都不行才换模型（engine/fallback.py）。

server.json 的 engine 段（不写这一段 = 内核不接管任何事，全部照旧）：
  "engine": {
    "model": "deepseek/deepseek-flash",       默认模型（DeepSeek 10/16 列出来的只有 deepseek-flash、deepseek-v4-pro）
    "models": ["deepseek/deepseek-flash", …], claw 选 builtin 时 app 里能切换的模型（不写就只有 model）；某一项写成列表
                                               ["anthropic/claude-opus-5-5", "moonshot/kimi-k3"] = 这个模型自己的备用链（app 里只显示第一个；
                                               Agent、对话、定时任务选了它就带上这条链）。model 也能这样写成列表
    "fallbacks": ["moonshot/kimi-k3", …],      默认的备用链（第 13 步，engine/fallback.py）：模型出了能换的错（限流、过载、5xx、超时 /
                                               连不上、这一家的密钥 / 额度 / 计费错、型号不存在），重试用完以后按顺序换下一个；已经给用户
                                               看到字的那一步不换，用户点停、我们自己写错的请求（400）、拒答也不换。上下文太长：链里有窗口
                                               更大的就换它，没有就压缩（compact.py）。换过的模型这一轮剩下的步接着用，下一轮从头来。
                                               不写 = 不换（和以前一样）。对走内核的每件事都有效（主人回合、赛博、外人回合、压缩的摘要）；
                                               某件事不想换到别家，就把它写成只有一个模型的列表。主人回合用 Claude Code（claude-code/…）
                                               时它跑不了（登录掉了、命令坏了、套餐额度用完）也换（engine/claude_brain.py），只换到不是
                                               套餐的；它自己的链写成 "model": ["claude-code/opus", "deepseek/deepseek-flash"]
    "use": {"card": true, "doorman": "moonshot/kimi-k3", "json": false, "background": "deepseek/deepseek-flash", "writing": "google/gemini-4-argon",
            "compact": "claude-code/sonnet"},
                                               哪些事改走内核：true = 用默认模型，写模型 = 用它；不写 / false = 照旧（llm-task 等）
                                               card = 名片 Agent（含 A2A）；doorman = Doorman 复查（名片回复 + 代办出网）；json = llmjson.ask；
                                               background（第 9 步）= 定时任务、心跳、后台任务没自己写模型时用的（不写 = 跟对话的模型）；
                                               writing（10/19）= 写作：长文、文书（写文书的技能以后接，现在只有路由和设置）；不写 = 跟这一轮
                                               自己的模型；compact（10/19）= 压缩的摘要（engine/compact.py；不写 = 这一轮自己的模型）。
                                               writing、json、compact（PLAN_PURPOSES）能配成用户自己的套餐，照 may_spend：route(…, kind=…,
                                               origin=…) 认这一轮能花才回套餐，不认的（不报 = 当外人）换成 outside() 挑的。claude-code/… 交给
                                               他自己登录的 Claude Code 一次性地跑（engine/claude_brain.ask：零工具、不存会话）；
                                               写成列表 ["anthropic/claude-sonnet-5-5", "moonshot/kimi-k3"] = 第一个是主模型、后面是它自己的链
                                               （盖过默认链；只写一个 = 这件事不换模型；["claude-code/sonnet", "deepseek/deepseek-flash"] = 套餐
                                               跑不了、这一轮不能花套餐时用后面的）。赛博自己的 model 也能写成列表（engine/cyber.py）
                                               第 3 批（MEDIA_PURPOSES）：vision 看图 / pdf 看文档 / video 看视频 / stt 转写 / tts 朗读 /
                                               image 生图 / video_gen 生成视频 / music 生成音乐：写「服务商/型号」或 {"model": …, 选项}，engine/media.py 解析（不走 route、不进备用链、
                                               不能是套餐：都是按量花钱的）；不写 = 没有这一样
    "fallback": true,                          内核这条路出错时退回原来的路（默认 true；false = 不退，直接报错）。外人回合的一次性 JSON
                                               先走完备用链，链走完了才退回原来的路（llmjson、名片 Agent、Doorman）
    "effort": "low",                           外人回合的思考档位（low / medium / high；只给支持的模型）
    "record_openclaw": false,                  claw 是 OpenClaw 时把它的每一轮也记进内核的事件表（engine_turns 的 kind = claw，
                                               chat.ClawRecord；只多记一份，默认关）
    "providers": {…}                           见上
    "recall": {"extra": ["~/…/profile/USER.md", {"path": "~/vault/笔记", "name": "笔记", "agents": ["main"]}], "main": "own",
               "tree": true, "max_chars": 6000, "half_life_days": 30, "db": "<data_dir>/recall.db",
               "embeddings": {"backend": "gemini", "model": "gemini-embedding-001", "dims": 768, "key_env": "GEMINI_API_KEY", "rpm": 30},
               "tree_md": "~/…/TREE.md"}
                                               记忆检索（memory_search / memory_get，说明在 engine/recall.py 开头）：extra = 共享给 Agent 的
                                               文件或文件夹（不写 = 只有档案）；main = "all" 时主对话搜得到所有 Agent 的；tree = 顺手查世界树；
                                               embeddings = 按意思搜（不写 = 不开，什么都不发出去；写法在 engine/embeddings.py 开头）；
                                               tree_md = 世界树的模块读不了时退回读的 TREE.md 导出（engine/recall_tree.py）
    "jobs": {"catch_up_missed": true, "idle_timeout": 600, "max_parallel": 3, "daily_limit": 1000}
                                               定时任务（cron 工具，说明在 engine/jobs.py 开头）：服务器停着时错过的要不要补跑一次、
                                               多久没动静就停、同时跑几条、一天最多跑几次（所有任务合计）
    "heartbeat": {"main": {"every": "30m", "active": ["08:00", "22:00"], "level": "quiet", "model": "…", "light": true, "prompt": "…"}}
                                               心跳（说明在 engine/heartbeat.py 开头）：开了的 Agent 定期「看一眼」，不写 = 不开
    "sandbox": {"network": "mousse-errand", "proxy": "http://172.30.99.1:3128", "idle_minutes": 120, "max_containers": 4,
                "agents": {"errand": {"access": "none", "browser": true}}}
                                               沙箱（说明在 engine/sandbox.py 开头）：在沙箱里干活的 Agent（代办一定在）、容器的网络、Doorman 的地址、
                                               镜像、上限、闲多久收；不写 = 照 errand.py setup 装好的那一套
    "skills": {"nudge": true, "nudge_tools": 8, "review": false, "agents": {"g-diet": ["board", "inbox"]}}
                                               写自己的 skill（第 13 步，说明在 engine/skill_write.py 开头）：上一轮调了很多次工具、被纠正过，
                                               这一轮提醒一句可以存（不多调模型）；review = true（或者写一个模型）在后台多一次便宜的调用，
                                               读那一轮决定要不要存，默认关。agents = 每个 Agent 能用哪些技能（技能名的列表；没写的 Agent
                                               看全部，说明在 engine/agent_skills.py 开头）
    "manuals": {"max": 2, "min_score": 0.45, "catalog": "names", "rewrite": false, "max_chars": 8000, "stale_days": 14, "unused_days": 30}
                                               手册和技能按这句话搜（13c，说明在 engine/manuals.py 开头）：每轮开头只带命中的正文（最多 max 份、
                                               分够 min_score）；catalog = 会话开头的技能目录 names（只留名字，默认）/ none / full（以前带说明的）；
                                               rewrite = true（或者写一个模型）先让模型改写成几种说法再搜，默认关，从不花套餐；
                                               stale_days 天没用的技能标旧，unused_days 天没用上的进每周修剪卡；false = 关掉，一切照以前
    "claude_code": {"models": ["claude-code/opus", "claude-code/sonnet", "claude-code/haiku"], "effort": "medium",
                    "fallback_model": "sonnet", "idle": 600, "builtin_tools": [], "max_concurrent": 2, "max_oneshot": 1}
                                               主人回合交给这台电脑上用户自己登录的官方 Claude Code 跑（第 11 项，说明在 engine/claude_brain.py
                                               开头）：models = app 里多出来的那几个（查到登录了才出现）；max_concurrent = 同时最多跑几个
                                               （一个 claude 进程约 0.3 GB，多出来的排队，默认 2）；max_oneshot = 其中一次性调用最多占几个
                                               （默认比 max_concurrent 少一个、至少 1：总留一个给整轮对话）；false = 关掉
    "chatgpt": {"models": ["chatgpt/gpt-6.1-sol"], "headers": {…}}
                                               用户自己的 ChatGPT 套餐（第 11b 项，说明在 engine/chatgpt_plan.py 开头）：models = app 里列哪几个
                                               （不写用这个账号的模型目录；连上了、开了用套餐才出现）；false = 关掉
    "grok": {"models": ["grok/grok-4.7"], "route": "api", "headers": {…}}
                                               10/20 用户自己的 Grok 订阅（说明在 engine/grok_plan.py 开头）：models = app 里列哪几个（不写用这个
                                               账号的目录；登录了才出现）；route = api（默认，api.x.ai）/ proxy（Grok CLI 的订阅代理）；false = 关掉
  }
另外 server.json 的 mcp_servers（不在 engine 段里）：你自己配的 MCP 服务，说明在 engine/mcp_client.py 开头

用户自己的套餐（第 11 项）：claude-code/<型号>（opus / sonnet / haiku 或完整 id，交给 --model）不经我们的接口，一轮交给本机用户自己登录的
Claude Code 跑（engine/claude_brain.py），花的是他自己的套餐。subscription(ref) 认它。may_spend 看来路、不看 kind（10/21：能走套餐的应走尽走）：
替用户自己干活的来路能花（OWNER_ORIGINS：user 用户本人、auto 他自己的定时和后台、relay 经中继的本人），不管这一轮是主对话、赛博、群里一位的
发言、会议、一问一答还是写回。替外人答的来路不花（card 名片 Agent、doorman、butler 管家回外人、api / acp / openai / mcp 对外接口，以及没报来路）。
挑不到、这一轮不能花的换成 outside() 的非套餐模型，再没有就照调用方自己的退路。
10/19 起一次性调用也能用（claude_brain.ask）：一问一答、llmjson.ask（会议纪要、播客、学习页……）、压缩摘要。调用方报上来路（origin），
may_spend 认了才花；不报的当外人。kind 还留在参数里（事件表、OpenClaw 那条路），自己的内核里不再拿它挡套餐。
valid_ref 对它回 False（我们的接口跑不了），所以没专门处理的地方默认就碰不到它；loop.run_turn 不跑 claude-code（只交给 claude_brain）。
第 11b 项：chatgpt/<型号>（型号是这个账号模型目录里的 slug）= 用户自己的 ChatGPT 套餐（engine/chatgpt_plan.py 拿令牌，
provider_responses.py 调公开的 Responses API）。也是套餐：subscription() 回 "chatgpt"，valid_ref 同样回 False（不进备用链、outside()、
route() 这些地方），只在专门放行的地方跑，每一处都问 may_spend，loop.run_turn 再挡一道。may_spend 对它比 claude-code 严：
用户自己引出来的（user、relay，含他的赛博和别的 kind）能花；后台的（auto：定时任务、心跳、收件箱、写回和他的赛博后台；task：后台任务自己）
要用户在设置里同意过（chatgpt_plan.background_allowed）；替外人答的、对外接口、/mcp 来的一律不行。via = 这一轮是经谁的请求引出来的
（外面的 MCP 客户端经 /mcp 跑的脚本 = mcp；内核自己的工具跑的脚本 = 那一轮的来路，比如后台的一轮里调了转交 = auto）：和 origin 取更严的那个。
10/20：grok/<型号> = 用户自己的 Grok 订阅（SuperGrok / X Premium+，engine/grok_plan.py：xAI 的 OAuth 设备码登录，推理经 providers.stream
调 https://api.x.ai/v1 的 Responses API，每次现取访问令牌）。命名照 chatgpt/ 和 openai/ 的分法：grok/ 是订阅、xai/ 是按量的 API 密钥，
看前缀就分得清谁花套餐。和命令行的套餐（CLI_PLANS）一样是 valid_ref（主对话、定时任务、后台任务、压缩、一次性调用、用户自己的赛博都照常经
providers.stream 跑，不用每一处另开一条路），可还是套餐：subscription() 回 "grok"（API_PLANS），may_spend 照 ChatGPT 套餐的规矩（替用户自己的
能花；后台的要用户在设置里另外同意过，grok_plan.background_allowed，和 ChatGPT 的同意分开记；替外人答的一律不行），outside() / route()（card、
doorman）/ chain_of / fallback.chain 都不把它当按量的；loop.run_turn 那一道照样挡。
OpenClaw 那条路（claw 不是 builtin）照旧只给 kind 是 owner 的回合花套餐：may_spend 里那一道还在，那边的行为不变。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import secretstore
from config import raw, settings

from . import catalog
from .errors import EngineError

ANTHROPIC, OPENAI, RESPONSES = "anthropic", "openai", "openai-responses"

# 各家的脾气（provider_openai.py 开头有每一项的意思；10/16 对照 pi、OpenClaw、Hermes 的源码 + 真调核对）。第 9 步起这一份从服务商目录来
# （engine/catalog.py：云端、本机的二十几家，10/17 逐家核对过官方文档）；原来就有的十三家一项没变
PROVIDERS: dict[str, dict] = catalog.engine_providers()
OPENCLAW_API = {"openai-completions": OPENAI, "openai-responses": OPENAI, "anthropic-messages": ANTHROPIC}
ENV_REF = re.compile(r"^\$\{([A-Z_][A-Z0-9_]*)\}$")
REF = re.compile(r"^([a-z0-9][a-z0-9_.-]*)/(\S+)$")

# 每百万 token 的美元价：(输入, 输出, 缓存读, 缓存写)。只列核对过官方价目页的；没列的不算钱，界面上写「?」。
#   Anthropic  2026-10-06 的价目
#   DeepSeek   api-docs.deepseek.com/quick_start/pricing，2026-10-17 核对（这一版价目 2026-09-10 起）：输入 = 缓存没命中，缓存读 = 命中，
#              没有单独的缓存写入价（按没命中算）。列的是峰时价，闲时半价（OFF_PEAK）
#   Kimi       platform.moonshot.ai/docs/pricing/chat（现在跳到 platform.kimi.ai），2026-10-17 核对：输入 = 缓存没命中，缓存读 = 命中，
#              缓存写入按默认的 5 分钟档（$3.00，和输入一样；1 小时档 $6.00，我们不设）。国内站按人民币计价，这里一律按国际站的美元价。
#              kimi-k2.6 在同一页的 K2 那张表，2026-10-19 核对（页面没写日期）：缓存命中 $0.16、没命中 $0.95、输出 $4.00；那张表没有缓存
#              写入价，按没命中算（照 DeepSeek 那一项）
#   Gemini     ai.google.dev/gemini-api/docs/pricing（页面 2026-10-16 更新），2026-10-19 核对 Standard 档：输出含思考的 token；缓存读 =
#              Context caching（只有隐式缓存，没有存储费、没有单独的写入价：缓存写按输入算）；不按提示长短分档。12 月 1 日起价钱翻倍（PRICES_FROM）
#   向量       只有输入价（输出、缓存记 0；engine/usage.py 的 embeddings 那一栏用）。OpenAI：developers.openai.com/api/docs/pricing（页面没写
#              日期），2026-10-19 核对。Google：同一天的价目页只列了 gemini-embedding-2（文字输入），gemini-embedding-001 已经不在上面，不写
PRICES: dict[str, tuple[float, float, float, float]] = {
    "anthropic/claude-fable-5-1": (10, 50, 0.25, 12.5),
    "anthropic/claude-opus-5-5": (4, 20, 0.20, 5),
    "anthropic/claude-opus-5": (5, 25, 0.50, 6.25),
    "anthropic/claude-sonnet-5-5": (2, 10, 0.20, 2.5),
    "anthropic/claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125),
    "deepseek/deepseek-flash": (0.30, 1.20, 0.006, 0.30),
    "deepseek/deepseek-v4-pro": (1.32, 3.96, 0.044, 1.32),
    "moonshot/kimi-k3": (3.00, 15.00, 0.30, 3.00),
    "moonshot/kimi-k2.6": (0.95, 4.00, 0.16, 0.95),
    "google/gemini-4-argon": (2.00, 10.00, 0.10, 2.00),
    "openai/text-embedding-3-small": (0.02, 0, 0, 0),
    "openai/text-embedding-3-large": (0.13, 0, 0, 0),
    "openai/text-embedding-ada-002": (0.10, 0, 0, 0),
    "google/gemini-embedding-2": (0.20, 0, 0, 0),
}
# 从哪天起换价（价目页写了「几号起」的）：ref → ((哪天起, 那天起的价), …)，按日子排；日子按 UTC 的日历日算（价目页没写时区），
# 那天以前照 PRICES（PRICES 里没有 = 那天以前不算钱）
PRICES_FROM: dict[str, tuple[tuple[str, tuple[float, float, float, float]], ...]] = {
    "google/gemini-4-argon": (("2026-12-01", (4.00, 20.00, 0.20, 4.00)),),
}
# 分峰谷的：provider → 峰时（UTC 的 [起, 止) 小时，周一到周五），别的时候和周末按 PRICES 的一半算。DeepSeek 价目页的脚注：峰时
# 01:00–04:00、06:00–10:00 UTC，闲时半价；中国法定节假日整天也算闲时，这里认不出节假日，那几天照峰时算（只会多算，不会少算）
OFF_PEAK: dict[str, tuple[tuple[int, int], ...]] = {"deepseek": ((1, 4), (6, 10))}

# Anthropic 各型号能用的参数（只认确切的型号名；不认识的按名字里的代数猜：5 代及以后当新的，更老的什么都不带）
ADAPTIVE = {"claude-fable-5-1", "claude-mythos-5-1", "claude-fable-5", "claude-mythos-5", "claude-opus-5-5", "claude-opus-5",
            "claude-sonnet-5-5", "claude-sonnet-5", "claude-haiku-5-5", "claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6",
            "claude-sonnet-4-6"}
MID_SYSTEM = {"claude-fable-5-1", "claude-mythos-5-1", "claude-fable-5", "claude-mythos-5", "claude-opus-5-5", "claude-opus-5",
              "claude-opus-4-8", "claude-sonnet-5-5", "claude-haiku-5-5"}  # 对话中间的 role: system（Sonnet 5 不行）
DISPLAY_UPDATES = {"claude-fable-5-1", "claude-mythos-5-1", "claude-fable-5", "claude-opus-5-5", "claude-sonnet-5-5"}
SERVER_FALLBACKS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}  # fallbacks: "default"
GEN = re.compile(r"^claude-(?:opus|sonnet|haiku|fable|mythos)-(\d+)")


def cfg() -> dict:
    e = raw().get("engine")
    return e if isinstance(e, dict) else {}


ALIAS = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")  # 别名：不带斜杠，撞不上 provider/model


def aliases() -> dict[str, str]:
    """engine.aliases（第 7 批，2.11）：{"fast": "deepseek/deepseek-flash", "smart": "moonshot/kimi-k3"}，别名 → 真的引用。写错的不要：
    别名得是不带斜杠的名字，值得是 provider/model（别名指别名不认，只认一层）。"""
    v = cfg().get("aliases")
    if not isinstance(v, dict):
        return {}
    return {str(k): str(t).strip() for k, t in v.items() if ALIAS.match(str(k)) and isinstance(t, str) and REF.match(t.strip())}


def resolve(ref: str | None) -> str:
    """别名 → 真的引用；不是别名的原样（去掉两头的空白）。解析引用的地方都先过它（parse_ref、subscription……），所以哪儿写别名都认，
    判能不能花套餐（may_spend）看的也是解析完的那个：别名绕不过它。"""
    s = str(ref or "").strip()
    if not s or "/" in s:
        return s
    return aliases().get(s, s)


def parse_ref(ref: str) -> tuple[str, str]:
    """「provider/model」→ (provider, model)。model 里可以再有斜杠（openrouter/anthropic/claude-…）。不像样的抛 EngineError(config)。
    model 是规范的写法：目录里那家列出来带前缀的（Google 的 models/<型号>，catalog.bare）去掉前缀，老配置里带着的照样能调。
    别名（engine.aliases）先换成真的引用（resolve）。"""
    m = REF.match(resolve(ref))
    if not m:
        raise EngineError(f"model should look like provider/model, got {str(ref)[:60]!r}", kind="config")
    return m.group(1), catalog.bare(m.group(1), m.group(2))


def canonical(ref: str | None) -> str:
    """模型引用的规范写法（google/models/gemini-x → google/gemini-x，见 parse_ref）；不像样的原样还回去。比价、汇总、按用途选都按它比。"""
    try:
        name, model = parse_ref(ref or "")
    except EngineError:
        return str(ref or "")
    return f"{name}/{model}"


def valid_ref(ref: str | None) -> bool:
    """我们的接口拿密钥跑得了的模型引用。用户自己的套餐（claude-code/…、chatgpt/…）一律 False：只在专门放行的地方跑（见文件开头）。"""
    try:
        name, _ = parse_ref(ref or "")
    except EngineError:
        return False
    if name in API_PLANS:  # 10/20：Grok 订阅经 providers.stream 跑（见文件开头）；花不花照 may_spend
        return True
    return name not in SUBSCRIPTIONS and (name in PROVIDERS or name in CLI_PLANS or name in _openclaw_providers()
                                          or name in (cfg().get("providers") or {}))  # CLI_PLANS：第 7 批，engine/provider_cli.py 跑


def _first(v) -> str:
    """一处写的模型（规范的写法，见 canonical）：字符串照旧；写成列表（主模型 + 它的链）取第一个。"""
    if isinstance(v, list):
        v = next((x for x in v if isinstance(x, str) and x.strip()), "")
    return canonical(str(v or "").strip()) if isinstance(v, str) else ""


def chain_of(v) -> list[str] | None:
    """写成列表的一处 → 整条链（合法的引用，规范的写法，去重、保持顺序，第一个是主模型）；字符串 / 不写 → None（用默认链）。"""
    if not isinstance(v, list):
        return None
    out: list[str] = []
    for x in v:
        r = canonical(str(x).strip()) if isinstance(x, str) else ""
        if r and valid_ref(r) and not subscription(r) and r not in out:  # 命令行的套餐（CLI_PLANS）同 claude-code：不进链
            out.append(r)
    return out


def default_model() -> str:
    return _first(cfg().get("model"))


def models() -> list[str]:
    """claw 选 builtin 时 app 里能切换的：engine.models 里合法的（写成列表的那一项取第一个；没写就只有 engine.model），加上这台电脑上有用户自己登录的 Claude Code
    时的 claude-code/…（没有就一个都不列，写在 engine.models 里的也不列），再加上 engine.aliases 里指到按量模型的别名（第 7 批）。"""
    ms = [m for m in (_first(x) for x in cfg().get("models") or []) if (valid_ref(m) and not subscription(m)) or subscription_ready(m)]
    d = default_model()
    if not ms and d and (not subscription(d) or subscription_ready(d)):
        ms = [d]
    ms += [m for m in subscription_models() if m not in ms]
    # 别名（engine.aliases）：指到我们的接口能跑的模型的，app 里也能选（选了记的是别名，指的模型改了跟着变）；指到套餐的不列
    return ms + [a for a, t in aliases().items() if a not in ms and valid_ref(t) and not subscription(t)]


# 能配成用户自己套餐的那几件事（route 照 may_spend 放不放）：writing 写作；json 一次性 JSON（llmjson.ask、/api/engine/oneshot）；
# compact 压缩的摘要（engine/compact.py）。10/19 起后两样也能：主人自己的一问一答、主人回合的压缩交给他自己登录的 Claude Code
PLAN_PURPOSES = frozenset({"writing", "json", "compact"})
# 第 3 批：媒体那几样（看图、看文档、看视频、转写、朗读、生图、生成视频、生成音乐），engine/media.py 解析 engine.use.<这一样>
MEDIA_PURPOSES = ("vision", "pdf", "video", "stt", "tts", "image", "video_gen", "music")


def route(purpose: str, override: str | None = None, *, kind: str = "outsider", origin: str | None = None,
          via: str | None = None) -> str | None:
    """这件事（card / doorman / json / writing / compact）走不走内核：走就回模型引用，不走回 None。override = 调用方自己指定的模型
    （合法才用；PLAN_PURPOSES 的这一轮能花时，套餐也认）。card / doorman 是外人回合，不花用户的套餐：配成 claude-code/…（或者默认模型
    就是它）的换成 outside() 挑的，挑不到就不走内核。PLAN_PURPOSES 那几件能配成套餐：调用方报上这一轮的 kind / origin / via，may_spend
    认的才回套餐，不认的（不报 = 当外人回合）一样换成 outside() 挑的。写成列表的：回第一个（主模型），后面的链由 purpose_chain 给；
    套餐打头的列表这一轮不能花，就回它后面第一个按量的。
    10/20：claw 不是 builtin（接的是 OpenClaw 或别的 claw）时一律不回套餐（claude-code/…、chatgpt/… 和别的套餐），换成 outside() 挑的，
    挑不到回 None（调用方照旧走自己的退路）：用户自己的 Claude Code 只在自己的内核里跑，和 10/19 以前一样。"""
    use = cfg().get("use")
    v = use.get(purpose) if isinstance(use, dict) else None
    if v is None or v is False:
        return None
    plan = purpose in PLAN_PURPOSES
    import claw  # claw 不导入内核：用到时再导
    mine = claw.is_builtin()

    def spend(ref: str) -> bool:  # 命令行的套餐（CLI_PLANS）不接这几件一次性的事：那几条路只会把它当外人跑（第 7 批，one_shot_plan）
        return plan and mine and one_shot_plan(ref) and may_spend(ref, kind=kind, origin=origin, via=via)

    if override and ((valid_ref(override) and not subscription(override)) or subscription(override) and spend(override)):
        return canonical(override)
    if isinstance(v, list):
        head, c = _first(v), chain_of(v)
        if subscription(head) and spend(head):
            return head
        return c[0] if c else outside() if plan and subscription(head) else None
    if isinstance(v, str) and valid_ref(v) and not subscription(v):
        return canonical(v)
    d = default_model()
    want = canonical(v) if isinstance(v, str) else d if v is True else ""
    if subscription(want):
        return want if spend(want) else outside()
    return d if v is True and valid_ref(d) else None


def background_model() -> str | None:
    """engine.use.background（第 9 步「按用途选模型」的「后台和定时」）：定时任务、心跳、后台任务没自己写模型时用哪个。true = 默认模型；
    写成列表取第一个（主模型）；不写、写错了 → None（照旧：跟那个对话的模型）。可以是用户自己的套餐（后台用 ChatGPT 套餐要用户同意过，
    调用方照旧问 may_spend）。"""
    use = cfg().get("use")
    v = use.get("background") if isinstance(use, dict) else None
    m = default_model() if v is True else _first(v)
    return m if m and known(m) else None


# —— 备用链（第 13 步，engine/fallback.py 用）——————————————————————————————————————

def fallbacks() -> list[str]:
    """默认的备用链：engine.fallbacks（合法的、去重）。不写 = []。"""
    return chain_of(cfg().get("fallbacks")) or []


def purpose_chain(purpose: str, primary: str) -> list[str] | None:
    """engine.use.<这件事> 写成列表、第一个就是 primary → 它后面那几个（它自己的链，可以是空的 = 不换）；否则 None（用默认的）。
    套餐打头的（["claude-code/sonnet", "deepseek/deepseek-flash"]）：chain_of 去掉了套餐，剩下的按量的就是它的链。"""
    use = cfg().get("use")
    v = use.get(purpose) if isinstance(use, dict) else None
    c = chain_of(v)
    if c and c[0] == primary:
        return c[1:]
    if isinstance(v, list) and subscription(primary) and _first(v) == primary:
        return c
    return None


def own_chain(ref: str) -> list[str] | None:
    """这个模型自己写了链吗：engine.model 或 engine.models 里写成列表、第一个就是它的那一项 → 后面那几个；没写 → None。"""
    ref = canonical(ref)  # 别名：照它指的那个找
    for v in [cfg().get("model"), *(cfg().get("models") or [])]:
        c = chain_of(v)
        if c and c[0] == ref:
            return c[1:]
    return None


def chain_after(ref: str) -> list[str]:
    """ref 跑不了时往下换的那几个（不含它自己）：它自己写的链，没写用 engine.fallbacks（同 fallback.chain）。套餐模型（claude-code/…）
    自己写的链也认：["claude-code/opus", "deepseek/deepseek-flash"]（own_chain 认不出：chain_of 先把套餐去掉了，第一个对不上）。
    只有我们的接口跑得了的（chain_of 去掉了套餐和写错的）。"""
    ref = canonical(ref)
    own = own_chain(ref)
    if own is None and subscription(ref):
        own = next((chain_of(v) or [] for v in [cfg().get("model"), *(cfg().get("models") or [])] if isinstance(v, list) and _first(v) == ref),
                   None)
    return [r for r in (fallbacks() if own is None else own) if r != ref]


# —— 用户自己的套餐（第 11 项）——————————————————————————————————————————————————————

SUBSCRIPTIONS = {"claude-code": "claude-code", "chatgpt": "chatgpt"}  # provider → 哪家的套餐（chatgpt：第 11b 项，engine/chatgpt_plan.py）
# 第 7 批：用户自己登录的官方命令行当模型（engine/provider_cli.py：codex/…、copilot/…）。和上面两家不同，它们是 valid_ref（经
# providers.stream 跑），可还是用户自己的套餐：subscription 照样认出来，may_spend 照套餐的规矩，outside / route / fallback.chain 都不把它们当按量的
CLI_PLANS = {"codex": "codex", "copilot": "copilot"}
# 10/20：用户自己的套餐、我们自己拿着他的 OAuth 令牌调公开接口的（engine/grok_plan.py：grok/…）。也是 valid_ref、也是套餐（见文件开头）
API_PLANS = {"grok": "grok"}
# 能花套餐的来路（看这一轮替谁干活，不看 kind）：用户自己说的（user）、他自己的定时、心跳、收件箱、写回和后台接着做的（auto）、
# 主对话转给 Agent 的（relay）。名片 Agent、Doorman、管家回外人（card / doorman / butler）、对外接口（api / acp / openai / mcp）不在这里。
# 只认名单里的，新加的来路默认不能花。后台任务自己的来路 task 不在这份名单里（claude-code 照旧不花）；ChatGPT 那几家把 task 算后台，见下。
OWNER_ORIGINS = frozenset({"user", "auto", "relay"})
# ChatGPT 套餐更严（条款：后台用之前要用户明确同意）：用户自己引出来的直接能花；后台的（auto、后台任务自己 task）要用户在设置里同意过
PLAN_FOREGROUND = frozenset({"user", "relay"})
PLAN_BACKGROUND = frozenset({"auto", "task"})
CLAUDE_CODE_MODELS = ("claude-code/opus", "claude-code/sonnet", "claude-code/haiku")
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:\[\]-]{0,79}$")  # 交给 --model 的：别名（opus）或完整 id（claude-<型号>-<版本>，可带 [1m]）


def subscription(ref: str | None) -> str | None:
    """这个模型花不花用户自己的套餐：claude-code/<型号> → "claude-code"；chatgpt/<型号> → "chatgpt"；别的 → None。所有「花用户套餐」的
    判断都用它。别名先解析（resolve）：指到套餐的别名一样算套餐。"""
    m = REF.match(resolve(ref))
    if m and m.group(1) in CLI_PLANS:  # codex/…、copilot/… 是 valid_ref：型号写得怪（codex/a/b、codex/-c…）也算套餐，免得绕过 may_spend
        return CLI_PLANS[m.group(1)]
    if m and m.group(1) in API_PLANS:  # 10/20：grok/… 同上（valid_ref，型号写得怪也算套餐）
        return API_PLANS[m.group(1)]
    return SUBSCRIPTIONS.get(m.group(1)) if m and NAME.match(m.group(2)) else None


def one_shot_plan(ref: str | None) -> bool:
    """一次性的事（PLAN_PURPOSES：写作、一问一答、压缩摘要）能不能配成这个套餐：是套餐，又不是命令行的（CLI_PLANS 那几条路只会把它
    当外人跑，route 换成按量的）。route 和设置页（engine/setup_api.py：能选哪些、PUT 收不收）共用。"""
    sub = subscription(ref)
    return sub is not None and sub not in CLI_PLANS.values()


def known(ref: str | None) -> bool:
    """设置里写的模型认不认（定时任务、心跳）：我们的接口能跑的，或者用户自己的套餐（到点时这台电脑上没有就报错）。套餐要写真的引用：
    指到套餐的别名在这儿不认（定时任务、心跳把模型名原样交给 Claude Code，别名它不认得）。"""
    return valid_ref(ref) or (subscription(ref) is not None and "/" in str(ref or ""))


def _rank(o: str | None) -> int:
    return 0 if o in PLAN_FOREGROUND else 1 if o in PLAN_BACKGROUND else 2


def stricter(origin: str | None, via: str | None) -> str | None:
    """origin 和 via 里对 ChatGPT 套餐更严的那个（via 没给 = origin）。工具跑的脚本再引出一轮时，带下去的就是它。"""
    return via if via is not None and _rank(via) > _rank(origin) else origin


def plan_class(origin: str | None, via: str | None = None) -> str:
    """ChatGPT 套餐眼里这一轮算什么：foreground（用户自己引出来的）/ background（后台，要同意过）/ never。via 给了就和 origin 取更严的。"""
    return ("foreground", "background", "never")[_rank(stricter(origin, via))]


def may_spend(ref: str | None, *, kind: str, origin: str | None, via: str | None = None) -> bool:
    """这一轮能不能用这个模型：不花套餐的都能。花套餐的看替谁干活（来路），不看 kind（10/21）。claude-code：来路在 OWNER_ORIGINS 里。
    chatgpt / codex / copilot / grok：用户自己引出来的能花，后台的要用户同意过，替外人答的不行（plan_class，见文件开头）。
    kind 还收着：OpenClaw 那条路照旧只放主人回合；自己的内核里它不参与判断。"""
    sub = subscription(ref)
    if sub is None:
        return True
    import claw  # 不在文件头导入：claw 不依赖内核，这里只用它认是不是自己的内核
    if not claw.is_builtin() and kind != "owner":  # OpenClaw 那条路的行为不变
        return False
    if sub in ("chatgpt", "codex"):  # codex/…（第 7 批）：Codex 用 ChatGPT 登录花的就是 ChatGPT 套餐，同一条规矩、同一个后台同意
        how = plan_class(origin, via)
        if how == "background":
            from . import chatgpt_plan
            return chatgpt_plan.background_allowed()
        return how == "foreground"
    if sub == "copilot":  # copilot/…（第 7 批）：Copilot 的 AI 额度；后台的要 engine.copilot.background = true（engine/provider_cli.py）
        how = plan_class(origin, via)
        if how == "background":
            from . import provider_cli
            return provider_cli.background_allowed("copilot")
        return how == "foreground"
    if sub == "grok":  # 10/20：Grok 订阅照 ChatGPT 套餐的规矩；后台的同意单独记（engine/grok_plan.py）
        how = plan_class(origin, via)
        if how == "background":
            from . import grok_plan
            return grok_plan.background_allowed()
        return how == "foreground"
    return origin in OWNER_ORIGINS


def outside(ref: str | None = None) -> str | None:
    """这一轮不能花套餐时用哪个模型（替外人答的、对外接口、来路不在 OWNER_ORIGINS 里、后台没同意的）：ref 是我们的接口能跑的就用它；
    否则（是套餐的、没给的）用默认模型，默认的也是套餐就挑 engine.models 里第一个能跑的；都没有 → None（调用方照自己的退路：llm-task、不做……）。"""
    if ref and valid_ref(ref) and not subscription(ref):  # 命令行的套餐（CLI_PLANS）也是 valid_ref：不给外面
        return resolve(ref)
    for m in (default_model(), *(_first(x) for x in cfg().get("models") or [])):
        if m and valid_ref(m) and not subscription(m):
            return m
    return None


def claude_code_cfg() -> dict:
    """server.json 的 engine.claude_code（false = 关掉）。"""
    v = cfg().get("claude_code")
    return {"enabled": False} if v is False else v if isinstance(v, dict) else {}


def subscription_ready(ref: str | None) -> bool:
    """这台电脑上现在跑得了这个套餐模型吗：claude-code/… → 有用户自己登录的官方 Claude Code（runners 的检测，登录是它自己存的那种），
    engine.claude_code 没关；chatgpt/… → ChatGPT 套餐连着、开了用套餐、没标要重连（chatgpt_plan.usable，不连网）。"""
    sub = subscription(ref)
    if sub == "chatgpt":
        from . import chatgpt_plan
        return chatgpt_plan.usable()
    if sub in CLI_PLANS.values():  # 第 7 批：装了、登录着（engine/provider_cli.py：只看 login status 那一行字）
        from . import provider_cli
        return provider_cli.ready(ref)
    if sub == "grok":  # 10/20：Grok 订阅登录着、没标要重登、没被 xAI 拒（grok_plan.usable，不连网）
        from . import grok_plan
        return grok_plan.usable()
    if sub != "claude-code" or claude_code_cfg().get("enabled") is False:
        return False
    from . import runners
    if runners.checking("claude-code"):  # 服务刚起来、登录还在后台查（有这个命令）：先当它在，app 别闪一下「选模型」的引导
        return True
    return "claude-code" in runners.offered() and runners.own_login(runners.available().get("claude-code"))


def claude_code_models() -> list[str]:
    """app 里多出来的 claude-code/…：这台电脑上有用户自己登录的 Claude Code 才有（engine.claude_code.models，默认 opus / sonnet / haiku）。"""
    if not subscription_ready(CLAUDE_CODE_MODELS[0]):
        return []
    ms = claude_code_cfg().get("models")
    return [m for m in ((str(x).strip() for x in ms) if isinstance(ms, list) else CLAUDE_CODE_MODELS) if subscription(m) == "claude-code"]


def subscription_models() -> list[str]:
    """app 里多出来的套餐模型：claude-code/…（同上）+ chatgpt/…（连上了、开了用套餐才有，chatgpt_plan.model_ids）。都没有 = []。"""
    from . import chatgpt_plan, provider_cli
    out = claude_code_models()
    out += [m for m in chatgpt_plan.model_ids() if m not in out]
    from . import grok_plan  # 10/20：grok/…（登录着才有：账号目录里对话的型号，默认的排最前）
    out += [m for m in grok_plan.model_ids() if m not in out]
    return out + [m for m in provider_cli.offered() if m not in out]  # 第 7 批：codex/…、copilot/…（开了、登录着才有）


def fallback_allowed() -> bool:
    return cfg().get("fallback", True) is not False


def effort(purpose: str = "") -> str | None:
    e = cfg().get("effort")
    return str(e) if e in ("low", "medium", "high", "xhigh", "max") else None


# —— provider ——————————————————————————————————————————————————————————

_oc_cache: tuple[float, dict] | None = None


def _openclaw_providers() -> dict:
    """openclaw.json 的 models.providers（按 mtime 缓存）。没装 OpenClaw、读不到 = 空。"""
    global _oc_cache
    path = Path(settings.openclaw_json)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    if _oc_cache and _oc_cache[0] == mtime:
        return _oc_cache[1]
    try:
        p = (json.loads(path.read_text(encoding="utf8")).get("models") or {}).get("providers") or {}
    except (OSError, ValueError, AttributeError):
        p = {}
    _oc_cache = (mtime, p if isinstance(p, dict) else {})
    return _oc_cache[1]


def _openclaw_url(url: str) -> str:
    """openclaw.json 的 baseUrl → 内核用的地址。Google 官方的那个，OpenClaw 写的是 Gemini 自己的接口（…/v1beta；光写域名它也当成
    /v1beta），内核跟 Google 只说 OpenAI 兼容的那套：照原样带过来，请求就发到 Gemini 自己的接口上，回 401。官方域名、路径是空的
    或者 /v1beta、/v1、/v1alpha 的，换成目录里的（…/v1beta/openai；?key= 之类的不带过来）；代理、本来就是兼容的，照原样。"""
    try:
        u = urlsplit(url.strip())
    except ValueError:
        return url
    if u.hostname == "generativelanguage.googleapis.com" and u.path.rstrip("/") in ("", "/v1beta", "/v1", "/v1alpha"):
        return str((PROVIDERS.get("google") or {}).get("url") or url)
    return url


def provider(name: str) -> dict:
    """{"name", "api", "url", "key", "headers", …其余设置}。不认识的 provider 抛 EngineError(config)。key 可能是空的（本机模型不要密钥）。
    另有 keys = [(哪一把, 值)…]（key_env 写成列表时有好几把）和 key_name（key 是哪一把）：用哪一把由 engine/fallback.py 挑。
    chatgpt：用户自己的 ChatGPT 套餐（engine/chatgpt_plan.py）不连网的那一份，key 是空的（推理前 providers.stream 现取令牌）。"""
    if name == "chatgpt":
        from . import chatgpt_plan
        return chatgpt_plan.provider_stub()
    if name in CLI_PLANS:  # 第 7 批：命令行自己存的登录，没有地址、没有密钥（engine/provider_cli.py）
        from . import provider_cli
        return provider_cli.stub(name)
    if name in API_PLANS:  # 10/20：Grok 订阅不连网的那一份（engine/grok_plan.py），key 是空的：推理前 providers.stream 现取访问令牌
        from . import grok_plan
        return grok_plan.provider_stub()
    p: dict = dict(PROVIDERS.get(name) or {})
    oc = _openclaw_providers().get(name)
    if isinstance(oc, dict):
        if oc.get("baseUrl"):
            p["url"] = _openclaw_url(str(oc["baseUrl"]))
        if not p.get("api") and OPENCLAW_API.get(str(oc.get("api") or "")):
            p["api"] = OPENCLAW_API[str(oc["api"])]
        if not p.get("api") and oc.get("baseUrl"):
            p["api"] = OPENAI
        m = ENV_REF.match(str(oc.get("apiKey") or ""))
        if m:
            p["key_env"] = m.group(1)
    over = (cfg().get("providers") or {}).get(name)
    if isinstance(over, dict):
        p.update(over)
    if p.get("api") not in (ANTHROPIC, OPENAI, RESPONSES):
        raise EngineError(f"unknown model provider {name!r} (add it under engine.providers in server.json)", kind="config")
    p["name"] = name
    p["url"] = str(p.get("url") or "").rstrip("/")
    if p["api"] in (OPENAI, RESPONSES) and not p["url"]:
        raise EngineError(f"provider {name!r} has no url", kind="config")
    key_env = p.get("key_env", f"{name.upper()}_API_KEY")
    literal = str(p.get("key") or "").strip()
    if literal:
        keys = [("key", literal)]
    else:
        names = [str(x) for x in key_env] if isinstance(key_env, list) else [str(key_env or "")]
        keys = [(n, v) for n in names if n and (v := env_value(n))]
    p["keys"] = keys
    p["key_name"], p["key"] = keys[0] if keys else ("", "")
    return p


def env_value(name: str) -> str:
    """环境变量：先看进程环境，再看 app 里存的 secrets.env（第 9 步，secretstore.py），再看 server.json 的 env_file（没写就是
    <openclaw_home>/.env）。读不到 = 空。"""
    return secretstore.lookup(name)[0]


def has_key(ref: str) -> bool:
    """这个模型能不能用：provider 认得、要密钥的有密钥（本机的 Ollama 这类不要密钥）。chatgpt/…：套餐连着、开了用套餐。"""
    if subscription(ref) in ("chatgpt", *CLI_PLANS.values()):
        return subscription_ready(ref)
    if subscription(ref) in API_PLANS.values():  # 10/20：Grok 订阅登录着就算有
        return subscription_ready(ref)
    try:
        name, _ = parse_ref(ref)
        p = provider(name)
    except EngineError:
        return False
    return bool(p["key"]) or p.get("key_env", "x") == ""


# —— 型号能力和价钱 ———————————————————————————————————————————————————————

def anthropic_caps(model_id: str) -> dict:
    """{"adaptive": 带 thinking adaptive + effort, "mid_system": 对话中间能放 role system, "updates": thinking.display 能用 updates,
    "fallbacks": 能用服务端的 fallbacks: "default"}。"""
    if model_id in ADAPTIVE:
        return {"adaptive": True, "mid_system": model_id in MID_SYSTEM, "updates": model_id in DISPLAY_UPDATES,
                "fallbacks": model_id in SERVER_FALLBACKS}
    m = GEN.match(model_id)
    new = bool(m and int(m.group(1)) >= 5)
    return {"adaptive": new, "mid_system": new, "updates": False, "fallbacks": False}


def price(ref: str, at: datetime | None = None) -> tuple[float, float, float, float] | None:
    """at 那一刻这个模型的价（每百万 token：输入, 输出, 缓存读, 缓存写）：PRICES，过了 PRICES_FROM 里写的日子换成那天起的；没有 → None。
    ref 按规范的写法查（google/models/… 和 google/… 是同一个价）。"""
    ref = canonical(ref)
    p = PRICES.get(ref)
    day = (at or datetime.now(timezone.utc)).astimezone(timezone.utc).date().isoformat()
    for since, later in PRICES_FROM.get(ref, ()):
        if day >= since:
            p = later
    return p


def cost(ref: str, usage: dict, at: datetime | None = None) -> float | None:
    """这次用量值多少美元；价目里没有的回 None。usage：in / out / cache_read / cache_write（token 数）。at：什么时候调的（按日子换价的
    见 PRICES_FROM，分峰谷的见 OFF_PEAK；不给 = 现在）。"""
    p = price(ref, at)
    if not p:
        return None
    total = (usage.get("in", 0) * p[0] + usage.get("out", 0) * p[1] + usage.get("cache_read", 0) * p[2]
             + usage.get("cache_write", 0) * p[3]) / 1e6
    if (peaks := OFF_PEAK.get(ref.split("/", 1)[0])) is not None:
        t = (at or datetime.now(timezone.utc)).astimezone(timezone.utc)
        if t.weekday() >= 5 or not any(a <= t.hour < b for a, b in peaks):
            total /= 2
    return round(total, 6)
