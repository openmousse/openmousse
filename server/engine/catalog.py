"""服务商目录（内核第 9 步「通用的选择层」，2026-10-17）：模型（云端、本机、命令行登录、自定义）、搜索、向量，各家都在这里，一份数据。
第一次打开的引导和设置中心（engine/setup_api.py、setup_ctl.py）照它列，engine/models.py 的 PROVIDERS 也从这里来（engine_providers()）。

一家云端的写法（必填的见 REQUIRED；problems() 查格式，测试会跑）：
  name          显示的名字：一个字符串，或者 (中文, English)
  api           openai（OpenAI 兼容的 /chat/completions）| anthropic（Claude 的 Messages API，官方 SDK）| openai-responses
  url           接口的根（openai 兼容写到 /v1 那一层；anthropic 写 https://api.anthropic.com，进内核时不带：写了 url 官方 SDK 就不发
                beta 的 block_binding、fallbacks，见 provider_anthropic）
  regions       有几个地区的：{"global": {"label", "url", "key_url"?, "test_model"?, "list_url"?}, "cn": {…}}，第一个是默认（= url）
  key_env       默认的密钥变量名（各家文档里用的那个）；app 里存的密钥就存在这个名字下（secretstore.py）
  key_url       去哪拿密钥
  docs          文档首页
  list          怎么列模型：openai（GET <url>/models）| anthropic（GET <根>/v1/models）| ollama（GET <根>/api/tags）| none（列不了：
                测一下发一句最小的请求）；list_url = 列模型的地址不在 <url>/models 的（文档写的那个）；list_prefix = 列出来的型号名前面
                带的前缀（Google 回 models/<型号>）：列的时候去掉，配置里、老的记录里带着的也认，规范成不带的（bare、models.canonical）
  test_model    测一下时列不了模型就拿它发一句（文档里写着的便宜型号）；也是只有这一家时自动用的型号（suggest 里没写 chat 的话）
  suggest       {"chat": 主对话推荐的, "cheap": 后台、外人回合推荐的, "writing": 写作推荐的}（只写文档里有的型号）
  caps          {"tools", "images", "reasoning", "embeddings", "pdf", "video", "audio"}：True / False / "some"（看型号）/ None（文档里没写，不猜）；
                pdf / video / audio（第 3 批，2026-10-19）= 这一家的接口收不收 PDF、视频、音频（官方文档写了的才填，别的都是 None）
  engine        进内核时的脾气（provider_openai.py 开头有每一项的意思）；拿不准的不写，按默认（最保守：思考只在一轮的工具来回里带回去、
                图照发、被拒了自动换成一句话再发）
  market        global / china / both：界面分组用
  sources       核对过的官方页面；checked = 哪天核对的
本机的另有 detect（不加载模型也会回的那个地址：{"path", "root": 在根上, "expect": 回答里要有的键, "unless": 这个地址也通 = 不是它}）、
  alt（别的常见端口：[(地址, detect)]）、start（怎么起）、install；key_env 是 ""（不要密钥）。
命令行登录（LOGINS）：用户自己在这台电脑上登录的工具，我们不读、不存、不传它们的令牌（claude_brain.py、chatgpt_plan.py、runners.py）。
核对：2026-10-17 逐家读了官方文档（下面每家的 sources），核对不到的字段不写（比如 Z.AI、NVIDIA 没写列模型的接口：list = none）。
  2026-10-19 补：Google 的 gemini-4-argon（型号页、价目页；价钱在 engine/models.py 的 PRICES）。
  2026-10-19 第 3 批补：caps 的 pdf / video / audio（OpenAI 的文件输入、Claude 的 PDF、Gemini 的文档 / 视频 / 音频理解，见各家 sources 的新链接）；
  只做媒体的几家（MEDIA：转写、朗读、生图的服务商，engine/media.py 用）。
  2026-10-19 补（第 7 批）：企业云 Azure OpenAI（v1 接口：learn.microsoft.com 那一页 ms.date 2026-05-13、06-05 更新过，v1 的 OpenAPI 定义）、
  Amazon Bedrock（OpenAI 兼容的 Chat Completions、API 密钥那两页）；这两家写了 checked。Google 的 Vertex AI 没加：它的 OpenAI 兼容接口
  「uses OAuth to authenticate with a short-lived access token」（默认 1 小时就过期，要自己刷新；docs.cloud.google.com 的
  gemini-enterprise-agent-platform/models/migrate/openai/auth-and-credentials，2026-10-15 更新），不是一把能存的密钥。
"""
from __future__ import annotations

OPENAI, ANTHROPIC, RESPONSES = "openai", "anthropic", "openai-responses"
CHECKED = "2026-10-17"
REQUIRED = ("name", "api", "url", "key_env", "key_url", "docs", "list", "caps", "sources")
LOCAL_REQUIRED = ("name", "api", "url", "detect", "list", "sources")
CAP_KEYS = ("tools", "images", "reasoning", "embeddings", "pdf", "video", "audio")


def _caps(tools=True, images="some", reasoning="some", embeddings=False, pdf=None, video=None, audio=None) -> dict:
    return {"tools": tools, "images": images, "reasoning": reasoning, "embeddings": embeddings, "pdf": pdf, "video": video, "audio": audio}


# —— 云端（填密钥）——————————————————————————————————————————————————————————————
CLOUD: dict[str, dict] = {
    "openai": {
        "name": "OpenAI", "api": OPENAI, "url": "https://api.openai.com/v1", "key_env": "OPENAI_API_KEY",
        "key_url": "https://platform.openai.com/settings/organization/api-keys", "docs": "https://developers.openai.com/api/docs/quickstart",
        "list": "openai", "test_model": "gpt-6-luna", "suggest": {"cheap": "gpt-6-luna"},
        "caps": _caps(images=True, reasoning=True, embeddings=True, pdf=True, audio="some"), "market": "global",
        "engine": {"max_tokens_field": "max_completion_tokens", "reasoning_effort": True, "effort_map": {}, "tool_id_max": 40},
        "sources": ["https://developers.openai.com/api/reference/overview", "https://developers.openai.com/api/reference/resources/models/methods/list",
                    "https://developers.openai.com/api/docs/models/gpt-6-luna", "https://developers.openai.com/api/docs/guides/pdf-files",
                    "https://developers.openai.com/api/docs/guides/audio"]},
    "anthropic": {
        "name": "Anthropic (Claude)", "api": ANTHROPIC, "url": "https://api.anthropic.com", "key_env": "ANTHROPIC_API_KEY",
        "key_url": "https://platform.claude.com/settings/keys", "docs": "https://platform.claude.com/docs/en/api/overview",
        "list": "anthropic", "test_model": "claude-haiku-5-5", "suggest": {"chat": "claude-sonnet-5-5", "cheap": "claude-haiku-5-5"},
        "caps": _caps(images=True, reasoning=True, pdf=True), "market": "global",
        "sources": ["https://platform.claude.com/docs/en/api/overview", "https://platform.claude.com/docs/en/api/models/list",
                    "https://platform.claude.com/docs/en/models/overview", "https://platform.claude.com/docs/en/build-with-claude/pdf-support"]},
    "google": {
        "name": "Google Gemini", "api": OPENAI, "url": "https://generativelanguage.googleapis.com/v1beta/openai", "key_env": "GEMINI_API_KEY",
        "key_url": "https://aistudio.google.com/apikey", "docs": "https://ai.google.dev/gemini-api/docs/openai",
        "list": "openai", "list_prefix": "models/", "test_model": "gemini-3.5-flash-lite",
        "suggest": {"cheap": "gemini-3.5-flash-lite", "writing": "gemini-4-argon"},
        "caps": _caps(images=True, reasoning=True, embeddings=True, pdf=True, video=True, audio=True), "market": "global",
        "sources": ["https://ai.google.dev/gemini-api/docs/openai", "https://ai.google.dev/gemini-api/docs/api-key",
                    "https://ai.google.dev/gemini-api/docs/models", "https://ai.google.dev/gemini-api/docs/models/gemini-4-argon",
                    "https://ai.google.dev/gemini-api/docs/pricing", "https://ai.google.dev/gemini-api/docs/document-processing",
                    "https://ai.google.dev/gemini-api/docs/video-understanding", "https://ai.google.dev/gemini-api/docs/audio"]},
    "xai": {
        "name": "xAI (Grok)", "api": OPENAI, "url": "https://api.x.ai/v1", "key_env": "XAI_API_KEY",
        "key_url": "https://console.x.ai/team/default/api-keys", "docs": "https://docs.x.ai/developers/quickstart",
        "list": "openai", "test_model": "grok-4.3", "suggest": {"cheap": "grok-4.3"},
        "caps": _caps(images=True, reasoning=True), "market": "global",
        "sources": ["https://docs.x.ai/developers/quickstart", "https://docs.x.ai/developers/rest-api-reference/inference/models",
                    "https://docs.x.ai/developers/models"]},
    "mistral": {
        "name": "Mistral AI", "api": OPENAI, "url": "https://api.mistral.ai/v1", "key_env": "MISTRAL_API_KEY",
        "key_url": "https://console.mistral.ai/api-keys", "docs": "https://docs.mistral.ai/getting-started/quickstarts/developer/first-api-request",
        "list": "openai", "test_model": "ministral-3b-latest", "suggest": {"cheap": "ministral-3b-latest"},
        "caps": _caps(images=True, reasoning="some", embeddings=True), "market": "global",
        "engine": {"tool_id": "mistral9", "reasoning_replay": "none"},
        "sources": ["https://docs.mistral.ai/openapi.yaml", "https://docs.mistral.ai/getting-started/quickstarts/developer/first-api-request",
                    "https://docs.mistral.ai/models/ministral-3-3b-25-12"]},
    "openrouter": {
        "name": "OpenRouter", "api": OPENAI, "url": "https://openrouter.ai/api/v1", "key_env": "OPENROUTER_API_KEY",
        "key_url": "https://openrouter.ai/keys", "docs": "https://openrouter.ai/docs/quickstart",
        "list": "openai", "test_model": "openrouter/free", "suggest": {"cheap": "openrouter/free"},
        "caps": _caps(embeddings=True), "market": "global",
        "note": ("一把密钥用几百个型号（各家的都有）；openrouter/free 随便挑一个免费的", "One key for hundreds of models from many labs; openrouter/free picks a free one"),
        "sources": ["https://openrouter.ai/docs/quickstart", "https://openrouter.ai/docs/api_reference/authentication",
                    "https://openrouter.ai/docs/guides/overview/models", "https://openrouter.ai/docs/guides/routing/routers/free-router"]},
    "groq": {
        "name": "Groq", "api": OPENAI, "url": "https://api.groq.com/openai/v1", "key_env": "GROQ_API_KEY",
        "key_url": "https://console.groq.com/keys", "docs": "https://console.groq.com/docs/openai",
        "list": "openai", "test_model": "openai/gpt-oss-20b", "suggest": {"cheap": "openai/gpt-oss-20b"},
        "caps": _caps(), "market": "global", "engine": {"reasoning_replay": "none"},
        "sources": ["https://console.groq.com/docs/openai", "https://console.groq.com/docs/models", "https://console.groq.com/docs/api-reference"]},
    "together": {
        "name": "Together AI", "api": OPENAI, "url": "https://api.together.ai/v1", "key_env": "TOGETHER_API_KEY",
        "key_url": "https://api.together.ai/settings/projects/~current/api-keys", "docs": "https://docs.together.ai/docs/inference/openai-compatibility",
        "list": "openai", "test_model": "Qwen/Qwen3.5-9B", "suggest": {"cheap": "Qwen/Qwen3.5-9B"},
        "caps": _caps(), "market": "global",
        "sources": ["https://docs.together.ai/docs/quickstart", "https://docs.together.ai/docs/inference/openai-compatibility",
                    "https://docs.together.ai/reference/models", "https://docs.together.ai/docs/serverless/models"]},
    "fireworks": {
        "name": "Fireworks AI", "api": OPENAI, "url": "https://api.fireworks.ai/inference/v1", "key_env": "FIREWORKS_API_KEY",
        "key_url": "https://app.fireworks.ai/settings/users/api-keys", "docs": "https://docs.fireworks.ai/tools-sdks/openai-compatibility",
        "list": "openai", "list_url": "https://api.fireworks.ai/v1/accounts/fireworks/models?filter=supports_serverless%3Dtrue",
        "test_model": "accounts/fireworks/models/glm-5p3-flash", "suggest": {"cheap": "accounts/fireworks/models/glm-5p3-flash"},
        "caps": _caps(embeddings=True), "market": "global",
        "sources": ["https://docs.fireworks.ai/getting-started/quickstart", "https://docs.fireworks.ai/tools-sdks/openai-compatibility",
                    "https://docs.fireworks.ai/api-reference/list-models", "https://docs.fireworks.ai/guides/querying-embeddings-models"]},
    "cerebras": {
        "name": "Cerebras", "api": OPENAI, "url": "https://api.cerebras.ai/v1", "key_env": "CEREBRAS_API_KEY",
        "key_url": "https://cloud.cerebras.ai", "docs": "https://inference-docs.cerebras.ai/resources/openai",
        "list": "openai", "test_model": "gpt-oss-120b", "suggest": {"cheap": "gpt-oss-120b"},
        "caps": _caps(), "market": "global",
        "sources": ["https://inference-docs.cerebras.ai/resources/openai", "https://inference-docs.cerebras.ai/api-reference/models/list-models",
                    "https://inference-docs.cerebras.ai/models/overview"]},
    "huggingface": {
        "name": "Hugging Face", "api": OPENAI, "url": "https://router.huggingface.co/v1", "key_env": "HF_TOKEN",
        "key_url": "https://huggingface.co/settings/tokens/new?ownUserPermissions=inference.serverless.write&tokenType=fineGrained",
        "docs": "https://huggingface.co/docs/inference-providers/index",
        "list": "openai", "test_model": "openai/gpt-oss-120b", "suggest": {"cheap": "openai/gpt-oss-120b"},
        "caps": _caps(), "market": "global",
        "note": ("令牌要勾上「Make calls to Inference Providers」", "The token needs the \"Make calls to Inference Providers\" permission"),
        "sources": ["https://huggingface.co/docs/inference-providers/index", "https://huggingface.co/docs/inference-providers/tasks/chat-completion",
                    "https://huggingface.co/docs/inference-providers/hub-api"]},
    "nvidia": {
        "name": "NVIDIA", "api": OPENAI, "url": "https://integrate.api.nvidia.com/v1", "key_env": "NVIDIA_API_KEY",
        "key_url": "https://build.nvidia.com/settings/api-keys", "docs": "https://docs.api.nvidia.com/nim/reference/llm-apis",
        "list": "none", "test_model": "openai/gpt-oss-20b", "suggest": {"cheap": "openai/gpt-oss-20b"},
        "caps": _caps(), "market": "global",
        "sources": ["https://docs.api.nvidia.com/nim/reference/llm-apis", "https://docs.api.nvidia.com/nim/docs/api-quickstart",
                    "https://docs.api.nvidia.com/nim/reference/openai-gpt-oss-20b-infer"]},
    "deepinfra": {
        "name": "DeepInfra", "api": OPENAI, "url": "https://api.deepinfra.com/v1/openai", "key_env": "DEEPINFRA_API_KEY",
        "key_url": "https://deepinfra.com/dash/api_keys", "docs": "https://docs.deepinfra.com/quickstart",
        "list": "openai", "list_url": "https://api.deepinfra.com/v1/models",
        "test_model": "meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo", "suggest": {"cheap": "meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo"},
        "caps": _caps(embeddings=True), "market": "global",
        "sources": ["https://docs.deepinfra.com/quickstart", "https://docs.deepinfra.com/api-reference/models/openai-models",
                    "https://docs.deepinfra.com/api-reference/embeddings/openai-embeddings", "https://deepinfra.com/pricing"]},
    "novita": {
        "name": "Novita AI", "api": OPENAI, "url": "https://api.novita.ai/openai/v1", "key_env": "NOVITA_API_KEY",
        "key_url": "https://novita.ai/settings/key-management", "docs": "https://docs.novita.ai/guides/llm-api",
        "list": "openai", "test_model": "meta-llama/llama-3.1-8b-instruct", "suggest": {"cheap": "meta-llama/llama-3.1-8b-instruct"},
        "caps": _caps(embeddings=True), "market": "global",
        "sources": ["https://docs.novita.ai/guides/quickstart", "https://docs.novita.ai/api-reference/model-apis-llm-list-models",
                    "https://docs.novita.ai/api-reference/model-apis-llm-create-embeddings"]},
    "vercel": {
        "name": "Vercel AI Gateway", "api": OPENAI, "url": "https://ai-gateway.vercel.sh/v1", "key_env": "AI_GATEWAY_API_KEY",
        "key_url": "https://vercel.com/d?to=%2F%5Bteam%5D%2F%7E%2Fai-gateway%2Fapi-keys&title=AI+Gateway+API+Keys",
        "docs": "https://vercel.com/docs/ai-gateway/sdks-and-apis/openai-chat-completions",
        "list": "openai", "test_model": "meta/llama-3.1-8b", "suggest": {"cheap": "meta/llama-3.1-8b"},
        "caps": _caps(embeddings=True), "market": "global",
        "sources": ["https://vercel.com/docs/ai-gateway/sdks-and-apis/openai-chat-completions",
                    "https://vercel.com/docs/ai-gateway/authentication-and-byok/api-keys", "https://vercel.com/docs/ai-gateway/models-and-providers"]},
    # 第 7 批：企业云（10/19 读的官方文档）。地址是用户自己的资源 / 地区的，型号是他自己部署的名字：列不了，测一下用文档里的例子。
    # Azure 没有默认地址（url 空着）：只存了密钥、没填地址的不算连上（models.provider 报没有地址），不会把对话和密钥发到别人的主机
    "azure": {
        "name": "Azure OpenAI (Microsoft Foundry)", "api": OPENAI, "url": "",
        "key_env": "AZURE_OPENAI_API_KEY", "key_url": "https://ai.azure.com/", "docs": "https://learn.microsoft.com/en-us/azure/foundry/openai/api-version-lifecycle",
        "list": "none", "test_model": "gpt-4.1-nano",
        "caps": _caps(images="some", reasoning="some", embeddings="some"), "market": "global",
        "engine": {"max_tokens_field": "max_completion_tokens", "tool_id_max": 40},
        "note": ("地址换成你自己的资源：https://<资源名>.openai.azure.com/openai/v1（或 .services.ai.azure.com/openai/v1，不用 api-version）；"
                 "型号写你部署时起的名字（部署名）。测一下默认找叫 gpt-4.1-nano 的部署，你的叫别的就在测试里写上",
                 "Put in your own resource: https://<resource-name>.openai.azure.com/openai/v1 (or .services.ai.azure.com/openai/v1, no api-version); "
                 "the model is your deployment's name. The test looks for a deployment called gpt-4.1-nano unless you name another"),
        "checked": "2026-10-19",
        "sources": ["https://learn.microsoft.com/en-us/azure/foundry/openai/api-version-lifecycle",
                    "https://github.com/Azure/azure-rest-api-specs/blob/main/specification/ai/data-plane/OpenAI.v1/azure-v1-v1-generated.json"]},
    "bedrock": {
        "name": "Amazon Bedrock", "api": OPENAI, "url": "https://bedrock-runtime.us-east-1.amazonaws.com/openai/v1", "key_env": "AWS_BEARER_TOKEN_BEDROCK",
        "key_url": "https://console.aws.amazon.com/bedrock", "docs": "https://docs.aws.amazon.com/bedrock/latest/userguide/inference-chat-completions-mantle.html",
        "regions": {"us-east-1": {"label": ("美国东部（弗吉尼亚北部）", "US East (N. Virginia)"), "url": "https://bedrock-runtime.us-east-1.amazonaws.com/openai/v1"},
                    "us-west-2": {"label": ("美国西部（俄勒冈）", "US West (Oregon)"), "url": "https://bedrock-runtime.us-west-2.amazonaws.com/openai/v1"}},
        "list": "none", "test_model": "openai.gpt-oss-120b-1:0",
        "caps": _caps(tools=None, images=None, reasoning="some", embeddings=False), "market": "global",
        "note": ("用 Bedrock 的长期 API 密钥（Bedrock 控制台左边的 API keys；只在生成它的那个地区能用。短期的最多 12 小时就过期，这里不会自己换）；"
                 "型号写 Bedrock 的模型 ID（openai.gpt-oss-120b-1:0 这样）；别的地区把地址里的 us-east-1 换掉",
                 "Use a long-term Bedrock API key (API keys in the Bedrock console's left pane; it only works in the Region it was made in. Short-term "
                 "keys expire within 12 hours and aren't renewed here); the model is a Bedrock model ID (like openai.gpt-oss-120b-1:0); for another "
                 "Region, replace us-east-1 in the address"),
        "checked": "2026-10-19",
        "sources": ["https://docs.aws.amazon.com/bedrock/latest/userguide/inference-chat-completions-mantle.html",
                    "https://docs.aws.amazon.com/bedrock/latest/userguide/api-keys.html"]},
    "deepseek": {
        "name": "DeepSeek", "api": OPENAI, "url": "https://api.deepseek.com", "key_env": "DEEPSEEK_API_KEY",
        "key_url": "https://platform.deepseek.com/api_keys", "docs": "https://api-docs.deepseek.com/",
        "list": "openai", "test_model": "deepseek-flash", "suggest": {"chat": "deepseek-flash", "cheap": "deepseek-flash"},
        "caps": _caps(images="some", reasoning=True), "market": "both",
        "engine": {"reasoning_replay": "all", "images": False, "reasoning_pad": True},
        "sources": ["https://api-docs.deepseek.com/", "https://api-docs.deepseek.com/api/list-models", "https://api-docs.deepseek.com/guides/thinking_mode/",
                    "https://api-docs.deepseek.com/quick_start/pricing"]},
    "moonshot": {
        "name": ("月之暗面 Kimi", "Kimi (Moonshot AI)"), "api": OPENAI, "url": "https://api.moonshot.ai/v1", "key_env": "MOONSHOT_API_KEY",
        "key_url": "https://platform.kimi.ai/console/api-keys", "docs": "https://platform.kimi.ai/docs/overview",
        "regions": {"global": {"label": ("国际站", "International"), "url": "https://api.moonshot.ai/v1", "key_url": "https://platform.kimi.ai/console/api-keys"},
                    "cn": {"label": ("中国大陆", "Mainland China"), "url": "https://api.moonshot.cn/v1", "key_url": "https://platform.kimi.com/console/api-keys"}},
        "list": "openai", "test_model": "kimi-k2.6", "suggest": {"chat": "kimi-k3", "cheap": "kimi-k2.6"},
        "caps": _caps(images="some", reasoning=True), "market": "both",
        "engine": {"reasoning_replay": "all", "mid_system": True, "reasoning_effort": True, "effort_map": {"xhigh": "high"}, "schema_fix": True,
                   "reasoning_pad": True},
        "sources": ["https://platform.kimi.ai/docs/api/overview", "https://platform.kimi.ai/docs/api/list-models", "https://platform.kimi.com/docs/api/overview",
                    "https://platform.kimi.ai/docs/pricing/chat"]},
    "zai": {
        "name": ("智谱 Z.AI", "Z.AI (Zhipu)"), "api": OPENAI, "url": "https://api.z.ai/api/paas/v4", "key_env": "ZAI_API_KEY",
        "key_url": "https://z.ai/manage-apikey/apikey-list", "docs": "https://docs.z.ai/api-reference/introduction",
        "regions": {"global": {"label": ("国际站 Z.AI", "International (Z.AI)"), "url": "https://api.z.ai/api/paas/v4",
                               "key_url": "https://z.ai/manage-apikey/apikey-list", "test_model": "glm-5.3-flash"},
                    "cn": {"label": ("中国大陆（智谱开放平台）", "Mainland China (BigModel)"), "url": "https://open.bigmodel.cn/api/paas/v4",
                           "key_url": "https://bigmodel.cn/usercenter/proj-mgmt/apikeys", "test_model": "glm-4.7-flash"}},
        "list": "none", "test_model": "glm-5.3-flash", "suggest": {"cheap": "glm-5.3-flash"},
        "caps": _caps(images="some", reasoning=True, embeddings="some"), "market": "both",
        "note": ("用的是通用 API 的密钥；Coding Plan 的密钥只给它认的那几个编程工具用，这里不能用", "Use a general API key; Coding Plan keys only work in the coding tools it supports"),
        "sources": ["https://docs.z.ai/api-reference/introduction", "https://docs.z.ai/guides/overview/pricing", "https://docs.bigmodel.cn/cn/api/introduction",
                    "https://docs.bigmodel.cn/cn/guide/start/pricing"]},
    "minimax": {
        "name": "MiniMax", "api": OPENAI, "url": "https://api.minimax.io/v1", "key_env": "MINIMAX_API_KEY",
        "key_url": "https://platform.minimax.io/user-center/basic-information/interface-key", "docs": "https://platform.minimax.io/docs/api-reference/text-openai-api",
        "regions": {"global": {"label": ("国际站", "International"), "url": "https://api.minimax.io/v1",
                               "key_url": "https://platform.minimax.io/user-center/basic-information/interface-key"},
                    "cn": {"label": ("中国大陆", "Mainland China"), "url": "https://api.minimax.cn/v1",
                           "key_url": "https://platform.minimax.cn/user-center/basic-information/interface-key"}},
        "list": "openai", "test_model": "MiniMax-M2.7", "suggest": {"cheap": "MiniMax-M2.7"},
        "caps": _caps(images="some", reasoning=True), "market": "both",
        "sources": ["https://platform.minimax.io/docs/guides/quickstart-preparation", "https://platform.minimax.io/docs/api-reference/text-openai-api",
                    "https://platform.minimax.io/docs/api-reference/models/openai/list-models", "https://platform.minimax.cn/docs/guides/quickstart-preparation"]},
    "stepfun": {
        "name": ("阶跃星辰 StepFun", "StepFun"), "api": OPENAI, "url": "https://api.stepfun.ai/v1", "key_env": "STEP_API_KEY",
        "key_url": "https://platform.stepfun.ai/interface-key", "docs": "https://platform.stepfun.ai/docs/en/quickstart/overview",
        "regions": {"global": {"label": ("国际站", "International"), "url": "https://api.stepfun.ai/v1", "key_url": "https://platform.stepfun.ai/interface-key"},
                    "cn": {"label": ("中国大陆", "Mainland China"), "url": "https://api.stepfun.com/v1", "key_url": "https://platform.stepfun.com/interface-key"}},
        "list": "openai", "test_model": "step-3.5-flash", "suggest": {"cheap": "step-3.5-flash"},
        "caps": _caps(images="some", reasoning=True), "market": "both",
        "sources": ["https://platform.stepfun.ai/docs/en/quickstart/overview", "https://platform.stepfun.ai/docs/en/api-reference/models/list",
                    "https://platform.stepfun.com/docs/zh/quickstart/overview", "https://platform.stepfun.ai/docs/en/guides/pricing/details"]},
    "dashscope": {
        "name": ("阿里云百炼（通义千问）", "Alibaba Cloud Model Studio (Qwen)"), "api": OPENAI, "url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        "key_env": "DASHSCOPE_API_KEY", "key_url": "https://modelstudio.console.alibabacloud.com/ap-southeast-1/model/settings/api-key",
        "docs": "https://www.alibabacloud.com/help/en/model-studio/compatibility-of-openai-with-dashscope",
        "regions": {"global": {"label": ("国际（新加坡）", "International (Singapore)"), "url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
                               "key_url": "https://modelstudio.console.alibabacloud.com/ap-southeast-1/model/settings/api-key",
                               "list_url": "https://dashscope-intl.aliyuncs.com/api/v1/models"},
                    "cn": {"label": ("中国大陆（北京）", "Mainland China (Beijing)"), "url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                           "key_url": "https://bailian.console.aliyun.com/cn-beijing/model/settings/api-key",
                           "list_url": "https://dashscope.aliyuncs.com/api/v1/models"},
                    "hk": {"label": ("中国香港", "Hong Kong"), "url": "https://cn-hongkong.dashscope.aliyuncs.com/compatible-mode/v1",
                           "key_url": "https://modelstudio.console.alibabacloud.com/cn-hongkong/model/settings/api-key",
                           "list_url": "https://cn-hongkong.dashscope.aliyuncs.com/api/v1/models"}},
        "list": "openai", "list_url": "https://dashscope-intl.aliyuncs.com/api/v1/models",
        "test_model": "qwen3.8-flash", "suggest": {"cheap": "qwen3.8-flash"},
        "caps": _caps(images="some", reasoning="some", embeddings=True), "market": "both",
        "note": ("密钥按地区分：哪个地区的密钥就选哪个地区", "Keys belong to a region: pick the region your key is from"),
        "sources": ["https://www.alibabacloud.com/help/en/model-studio/base-url", "https://www.alibabacloud.com/help/en/model-studio/get-api-key",
                    "https://www.alibabacloud.com/help/en/model-studio/list-models", "https://help.aliyun.com/zh/model-studio/base-url",
                    "https://www.alibabacloud.com/help/en/model-studio/text-generation-model"]},
    "modelscope": {
        "name": ("魔搭 ModelScope", "ModelScope"), "api": OPENAI, "url": "https://api-inference.modelscope.cn/v1", "key_env": "MODELSCOPE_API_KEY",
        "key_url": "https://modelscope.cn/my/myaccesstoken", "docs": "https://modelscope.cn/docs/model-service/API-Inference/intro",
        "regions": {"cn": {"label": ("中国站", "China site"), "url": "https://api-inference.modelscope.cn/v1", "key_url": "https://modelscope.cn/my/myaccesstoken"},
                    "global": {"label": ("国际站", "International site"), "url": "https://api-inference.modelscope.ai/v1",
                               "key_url": "https://modelscope.ai/my/myaccesstoken"}},
        "list": "none", "test_model": "Qwen/Qwen3.5-35B-A3B", "suggest": {"cheap": "Qwen/Qwen3.5-35B-A3B"},
        "caps": {"tools": None, "images": "some", "reasoning": "some", "embeddings": False, "pdf": None, "video": None, "audio": None}, "market": "china",
        "note": ("免费、按次扣魔粒；账号要先绑定实名的阿里云账号。用的是 ModelScope 的访问令牌",
                 "Free, metered in credits per call; the account must be linked to a verified Alibaba Cloud account. Uses your ModelScope access token"),
        "sources": ["https://modelscope.cn/docs/model-service/API-Inference/intro", "https://modelscope.cn/docs/model-service/API-Inference/limits",
                    "https://modelscope.ai/docs/model-service/API-Inference/intro", "https://modelscope.cn/docs/accounts/token"]},
    "siliconflow": {
        "name": ("硅基流动 SiliconFlow", "SiliconFlow"), "api": OPENAI, "url": "https://api.siliconflow.com/v1", "key_env": "SILICONFLOW_API_KEY",
        "key_url": "https://cloud.siliconflow.com/account/ak", "docs": "https://docs.siliconflow.com/en/userguide/quickstart",
        "regions": {"global": {"label": ("国际站", "International"), "url": "https://api.siliconflow.com/v1", "key_url": "https://cloud.siliconflow.com/account/ak",
                               "test_model": "openai/gpt-oss-20b"},
                    "cn": {"label": ("中国站", "China site"), "url": "https://api.siliconflow.cn/v1", "key_url": "https://cloud.siliconflow.cn/account/ak",
                           "test_model": "Qwen/Qwen3.5-35B-A3B"}},
        "list": "openai", "test_model": "openai/gpt-oss-20b", "suggest": {"cheap": "openai/gpt-oss-20b"},
        "caps": _caps(embeddings=True), "market": "both",
        "sources": ["https://docs.siliconflow.com/en/userguide/quickstart", "https://docs.siliconflow.com/en/api-reference/models/get-model-list",
                    "https://docs.siliconflow.cn/docs/userguide/quickstart", "https://docs.siliconflow.com/en/api-reference/embeddings/create-embeddings",
                    "https://www.siliconflow.com/pricing"]},
    "volcengine": {
        "name": ("火山方舟（豆包）", "Volcengine Ark (Doubao)"), "api": OPENAI, "url": "https://ark.cn-beijing.volces.com/api/v3", "key_env": "ARK_API_KEY",
        "key_url": "https://ark.volcengine.com/region:cn-beijing/apiKey", "docs": "https://www.volcengine.com/docs/82379/1330626",
        "list": "none", "test_model": "doubao-seed-2-0-mini-260428", "suggest": {"cheap": "doubao-seed-2-0-mini-260428"},
        "caps": _caps(images="some", reasoning="some"), "market": "china",
        "note": ("每个模型要先在方舟控制台「开通管理」里开通；model 写模型 ID 或推理接入点 ID",
                 "Each model must be enabled in the Ark console first; model is a model ID or an endpoint ID"),
        "sources": ["https://www.volcengine.com/docs/82379/1330626", "https://www.volcengine.com/docs/82379/1298459",
                    "https://www.volcengine.com/docs/82379/1541594", "https://www.volcengine.com/docs/82379/1544106"]},
}

# —— 本机跑的 ——————————————————————————————————————————————————————————————————
LOCAL: dict[str, dict] = {
    "ollama": {
        "name": "Ollama", "api": OPENAI, "url": "http://127.0.0.1:11434/v1", "key_env": "", "list": "ollama",
        "detect": {"path": "/api/version", "root": True, "expect": "version"}, "embeddings": True,
        "start": "ollama serve", "install": "https://ollama.com/download", "docs": "https://docs.ollama.com/api/openai-compatibility",
        "caps": _caps(images="some", reasoning="some", embeddings=True), "engine": {"require_finish": False},
        "sources": ["https://docs.ollama.com/faq", "https://docs.ollama.com/api/openai-compatibility", "https://docs.ollama.com/api/tags",
                    "https://docs.ollama.com/api-reference/get-version"]},
    "lmstudio": {
        "name": "LM Studio", "api": OPENAI, "url": "http://127.0.0.1:1234/v1", "key_env": "", "list": "openai",
        "detect": {"path": "/models", "expect": "data"}, "embeddings": True,
        "start": "lms server start", "install": "https://lmstudio.ai/download", "docs": "https://lmstudio.ai/docs/developer/openai-compat",
        "caps": _caps(images="some", reasoning="some", embeddings=True), "engine": {"require_finish": False},
        "sources": ["https://lmstudio.ai/docs/developer/openai-compat", "https://lmstudio.ai/docs/developer/rest/endpoints", "https://lmstudio.ai/docs/cli/server-start"]},
    "vllm": {
        "name": "vLLM", "api": OPENAI, "url": "http://127.0.0.1:8000/v1", "key_env": "", "list": "openai",
        "detect": {"path": "/version", "root": True, "expect": "version"}, "embeddings": True,
        "start": "vllm serve <model>", "install": "https://docs.vllm.ai/en/latest/getting_started/installation/", "docs": "https://docs.vllm.ai/en/latest/serving/online_serving/openai_compatible_server/",
        "caps": _caps(images="some", reasoning="some", embeddings="some"), "engine": {"require_finish": False},
        "sources": ["https://docs.vllm.ai/en/latest/cli/serve.html", "https://docs.vllm.ai/en/latest/serving/online_serving/openai_compatible_server/"]},
    "llamacpp": {
        "name": "llama.cpp", "api": OPENAI, "url": "http://127.0.0.1:9931/v1", "key_env": "", "list": "openai",
        "detect": {"path": "/health", "root": True, "expect": "status"},
        "alt": [("http://127.0.0.1:8080/v1", {"path": "/props", "root": True, "expect": "total_slots"})],  # Docker 镜像和 10/09 以前的版本
        "start": "llama-server -m <model.gguf>", "install": "https://github.com/ggml-org/llama.cpp", "docs": "https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md",
        "caps": _caps(images="some", reasoning="some", embeddings="some"), "engine": {"require_finish": False},
        "sources": ["https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md", "https://github.com/ggml-org/llama.cpp/pull/30159"]},
    "mlx": {
        "name": "MLX (mlx-lm)", "api": OPENAI, "url": "http://127.0.0.1:8080/v1", "key_env": "", "list": "openai",
        "detect": {"path": "/health", "root": True, "expect": "status", "unless": "/props"}, "mac": True,
        "start": "mlx_lm.server --model <repo>", "install": "https://github.com/ml-explore/mlx-lm", "docs": "https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/SERVER.md",
        "caps": _caps(images=False, reasoning="some"), "engine": {"require_finish": False},
        "sources": ["https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/SERVER.md", "https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/server.py"]},
    "omlx": {
        "name": "oMLX", "api": OPENAI, "url": "http://127.0.0.1:8000/v1", "key_env": "", "list": "openai",
        "detect": {"path": "/health", "root": True, "expect": "status"}, "mac": True, "embeddings": True,
        "start": "omlx serve", "install": "https://github.com/jundot/omlx", "docs": "https://github.com/jundot/omlx",
        "caps": _caps(images="some", reasoning="some", embeddings=True), "engine": {"require_finish": False},
        "sources": ["https://github.com/jundot/omlx"]},
}

# —— 命令行登录：复用这台电脑上用户自己登录的工具 ——————————————————————————————————————
LOGINS: dict[str, dict] = {
    "claude-code": {
        "name": "Claude Code", "role": "chat", "prefix": "claude-code",
        "how": ("在这台服务器上装好 Claude Code，在终端里跑 claude，自己登录一次（用你的 Pro / Max 套餐）。我们只起官方的命令，不碰它的令牌。",
                "Install Claude Code on this server, run claude in a terminal and sign in yourself (with your Pro / Max plan). We only start the "
                "official command; we never touch its tokens."),
        "docs": "https://code.claude.com/docs/en/setup", "terms": "https://code.claude.com/docs/en/legal-and-compliance"},
    "chatgpt": {
        "name": ("ChatGPT 套餐", "ChatGPT plan"), "role": "chat", "prefix": "chatgpt",
        "how": ("点「Continue with ChatGPT」，在浏览器里授权一次（手机上授权完把地址栏整串粘回来）。令牌只存在这台服务器上。",
                "Tap Continue with ChatGPT and approve once in a browser (on a phone, paste the whole address bar back). The tokens stay on this server."),
        "docs": "https://openai.com/policies/sign-in-with-chatgpt-terms"},
    # Grok 订阅（engine/grok_plan.py，10/20）：xAI 的 OAuth 设备码登录，令牌存在这台服务器上；不是命令行的登录，我们自己拿令牌
    "grok": {
        "name": ("Grok 订阅", "Grok subscription"), "role": "chat", "prefix": "grok",
        "how": ("用你的 SuperGrok / X Premium+ 订阅登录：在服务器上跑 python3 setup_ctl.py grok login（或者 POST /api/engine/grok/login），"
                "在手机或电脑上打开它给的网址、认一下码、点同意。令牌只存在这台服务器上。这不是 xAI 点名支持的接入：借的是 xAI 给几家点名的"
                "第三方 agent 共用的登录入口（同意页上可能写着 Grok Build），xAI 随时可能拒（403），被拒了就改用 API 密钥（XAI_API_KEY）。",
                "Sign in with your SuperGrok / X Premium+ subscription: run python3 setup_ctl.py grok login on the server (or POST "
                "/api/engine/grok/login), open the link it gives on your phone or computer, check the code and approve. The tokens stay on "
                "this server. This isn't an integration xAI has named or supports: it borrows the sign-in xAI shares with a few named "
                "third-party agents (the consent page may say Grok Build), and xAI may refuse it (403) at any time. If it does, use an API "
                "key (XAI_API_KEY) instead."),
        "docs": "https://docs.x.ai/developers/quickstart"},
    # Kimi 会员的 Kimi Code（engine/kimi_plan.py，10/21）：控制台建的密钥，不是 OAuth，也不是开放平台按量的 moonshot/
    "kimi": {
        "name": ("Kimi 会员（Kimi Code）", "Kimi membership (Kimi Code)"), "role": "chat", "prefix": "kimi",
        "how": ("在 Kimi Code 控制台建一把密钥，贴到这里，再选国内或海外站点。密钥只存在这台服务器上。这是会员的 Kimi Code，不是开放平台按量的 moonshot/。",
                "Create a key in the Kimi Code console, paste it here, and pick the China or overseas site. The key stays on this server. "
                "This is the Kimi Code membership, not the pay-as-you-go moonshot/ platform."),
        "docs": "https://www.kimi.com/code/docs/"},
    "codex": {
        "name": "Codex", "role": "helper",
        "how": ("在这台服务器上装好 Codex 命令行，自己登录一次（codex login）：后台任务能交给它做。",
                "Install the Codex CLI on this server and sign in yourself (codex login): background tasks can be handed to it."),
        "docs": "https://developers.openai.com/codex/cli"},
}

# —— 自定义：OpenAI 兼容的地址 + 可选的密钥 ——————————————————————————————————————————
CUSTOM = {"api": OPENAI, "apis": (OPENAI, ANTHROPIC),
          "hint": ("任何 OpenAI 兼容的地址（写到 /v1 那一层）：公司的代理、自己搭的服务、目录里没有的服务商。密钥可以不填。",
                   "Any OpenAI-compatible address (up to /v1): a company proxy, your own server, a provider that isn't listed. The key is optional.")}

# —— 搜索 ——————————————————————————————————————————————————————————————————————
# 请求怎么发在 engine/web.py（键和它的 PROVIDERS 一样）；这里是给人看的那一半
SEARCH: dict[str, dict] = {
    "brave": {"name": "Brave Search", "key_env": "BRAVE_API_KEY", "key_url": "https://api-dashboard.search.brave.com/register",
              "docs": "https://api-dashboard.search.brave.com/app/documentation/web-search/get-started",
              "free": ("每月送 5 美元额度（要绑卡）", "$5 of free credit every month (a card is required)"),
              "sources": ["https://api-dashboard.search.brave.com/app/documentation/web-search/get-started", "https://brave.com/search/api/"]},
    "tavily": {"name": "Tavily", "key_env": "TAVILY_API_KEY", "key_url": "https://app.tavily.com",
               "docs": "https://docs.tavily.com/documentation/api-reference/endpoint/search",
               "free": ("每月 1000 次免费，不用绑卡", "1,000 free credits a month, no card"),
               "sources": ["https://docs.tavily.com/documentation/api-reference/endpoint/search", "https://docs.tavily.com/documentation/api-credits"]},
    "exa": {"name": "Exa", "key_env": "EXA_API_KEY", "key_url": "https://dashboard.exa.ai/api-keys", "docs": "https://exa.ai/docs/reference/search",
            "free": ("每月 10 美元免费额度，不用绑卡", "$10 of free credit every month, no card"),
            "sources": ["https://exa.ai/docs/reference/search", "https://exa.ai/pricing"]},
    "perplexity": {"name": "Perplexity Search", "key_env": "PERPLEXITY_API_KEY", "key_url": "https://console.perplexity.ai/project/keys",
                   "docs": "https://docs.perplexity.ai/api-reference/search-post", "free": None,
                   "sources": ["https://docs.perplexity.ai/api-reference/search-post", "https://docs.perplexity.ai/getting-started/pricing"]},
    "searxng": {"name": "SearXNG", "keyless": True, "self_hosted": True, "url_env": "SEARXNG_URL", "docs": "https://docs.searxng.org/dev/search_api.html",
                "free": ("自己搭的，不花钱", "Self-hosted, free"),
                "note": ("填你自己的 SearXNG 地址；它的 settings.yml 里 search.formats 要加上 json，不然回 403",
                         "Enter your own SearXNG address; its settings.yml needs json under search.formats, or it answers 403"),
                "sources": ["https://docs.searxng.org/dev/search_api.html", "https://docs.searxng.org/admin/settings/settings_search.html"]},
    # 第 3 批（2026-10-19，照 OpenClaw 的 web-search 扩展）：密钥多半本来就为模型存着，所以默认不自己选上（engine/web.py 的 opt_in）
    "gemini": {"name": ("Gemini（Google 搜索接地）", "Gemini (Google Search grounding)"), "key_env": "GEMINI_API_KEY",
               "key_url": "https://aistudio.google.com/apikey", "docs": "https://ai.google.dev/gemini-api/docs/google-search",
               "free": ("免费层每天有一定额度，超了按次计费（看型号）", "A daily free allowance, then billed per grounded request (depends on the model)"),
               "note": ("回一段带出处的回答；要写明 provider: gemini 才用", "Returns an answer with sources; only used when provider is set to gemini"),
               "sources": ["https://ai.google.dev/gemini-api/docs/google-search", "https://ai.google.dev/gemini-api/docs/pricing"]},
    "grok": {"name": "Grok (xAI)", "key_env": "XAI_API_KEY", "key_url": "https://console.x.ai/team/default/api-keys",
             "docs": "https://docs.x.ai/docs/guides/tools/search-tools", "free": None,
             "note": ("回一段带出处的回答；要写明 provider: grok 才用。登录了 Grok 订阅时先走订阅（xAI 没写明搜索算不算在订阅里），"
                      "订阅用不了才用 XAI_API_KEY（按量）",
                      "Returns an answer with sources; only used when provider is set to grok. With your Grok subscription signed in it "
                      "goes through the subscription first (xAI hasn't said whether search is included); XAI_API_KEY (pay as you go) is "
                      "the fallback"),
             "sources": ["https://docs.x.ai/docs/guides/tools/search-tools", "https://docs.x.ai/developers/models"]},
    "kimi": {"name": ("Kimi 搜索", "Kimi search (Moonshot)"), "key_env": "MOONSHOT_API_KEY", "key_url": "https://platform.kimi.ai/console/api-keys",
             "docs": "https://platform.kimi.ai/docs/overview", "free": None,
             "note": ("要写明 provider: kimi 才用；中国大陆的密钥另写地址", "Only used when provider is set to kimi; mainland China keys need the .cn address"),
             "sources": ["https://platform.kimi.ai/docs/overview"]},
    "firecrawl": {"name": "Firecrawl", "key_env": "FIRECRAWL_API_KEY", "key_url": "https://www.firecrawl.dev/app/api-keys",
                  "docs": "https://docs.firecrawl.dev/api-reference/endpoint/search", "free": ("注册送一次性的免费额度", "One-off free credits on sign-up"),
                  "sources": ["https://docs.firecrawl.dev/api-reference/endpoint/search", "https://www.firecrawl.dev/pricing"]},
    "duckduckgo": {"name": "DuckDuckGo", "keyless": True, "unofficial": True, "docs": "https://duckduckgo.com",
                   "free": ("不要密钥", "No key"),
                   "note": ("没有官方接口：读它的网页结果，常被限流、要人机验证，只在你明确选它时用",
                            "No official API: it reads the results page, which is often rate-limited or asks for a bot check; only used if you pick it"),
                   "sources": ["https://duckduckgo.com"]},
}

# —— 向量（按意思搜记忆）——————————————————————————————————————————————————————————
# backend 和 engine/embeddings.py 的一样：gemini（Google 自己的接口）/ openai（OpenAI 兼容的 /embeddings）；provider = 用哪一家模型服务的密钥；
# region = 只有那一家选了这个地区才有；follow_url = 地址跟着那一家现在选的地区走（url 是默认地区的）
EMBEDDINGS: dict[str, dict] = {
    "gemini": {"name": "Google Gemini", "provider": "google", "backend": "gemini", "model": "gemini-embedding-001", "dims": 768,
               "key_env": "GEMINI_API_KEY", "sources": ["https://ai.google.dev/api/embeddings", "https://ai.google.dev/gemini-api/docs/embeddings"]},
    "openai": {"name": "OpenAI", "provider": "openai", "backend": "openai", "url": "https://api.openai.com/v1", "model": "text-embedding-3-small",
               "key_env": "OPENAI_API_KEY", "sources": ["https://developers.openai.com/api/reference/resources/embeddings/methods/create",
                                                        "https://developers.openai.com/api/docs/models/text-embedding-3-small"]},
    "mistral": {"name": "Mistral AI", "provider": "mistral", "backend": "openai", "url": "https://api.mistral.ai/v1", "model": "mistral-embed",
                "key_env": "MISTRAL_API_KEY", "sources": ["https://docs.mistral.ai/models/mistral-embed-23-12", "https://docs.mistral.ai/openapi.yaml"]},
    "openrouter": {"name": "OpenRouter", "provider": "openrouter", "backend": "openai", "url": "https://openrouter.ai/api/v1",
                   "model": "openai/text-embedding-3-small", "key_env": "OPENROUTER_API_KEY",
                   "sources": ["https://openrouter.ai/docs/api_reference/embeddings"]},
    "fireworks": {"name": "Fireworks AI", "provider": "fireworks", "backend": "openai", "url": "https://api.fireworks.ai/inference/v1",
                  "model": "fireworks/qwen3-embedding-8b", "key_env": "FIREWORKS_API_KEY",
                  "sources": ["https://docs.fireworks.ai/guides/querying-embeddings-models"]},
    "deepinfra": {"name": "DeepInfra", "provider": "deepinfra", "backend": "openai", "url": "https://api.deepinfra.com/v1/openai",
                  "model": "Qwen/Qwen3-Embedding-8B", "key_env": "DEEPINFRA_API_KEY",
                  "sources": ["https://docs.deepinfra.com/api-reference/embeddings/openai-embeddings", "https://docs.deepinfra.com/apis/embeddings"]},
    "novita": {"name": "Novita AI", "provider": "novita", "backend": "openai", "url": "https://api.novita.ai/openai/v1", "model": "baai/bge-m3",
               "key_env": "NOVITA_API_KEY", "sources": ["https://docs.novita.ai/api-reference/model-apis-llm-create-embeddings"]},
    "vercel": {"name": "Vercel AI Gateway", "provider": "vercel", "backend": "openai", "url": "https://ai-gateway.vercel.sh/v1",
               "model": "openai/text-embedding-3-small", "key_env": "AI_GATEWAY_API_KEY",
               "sources": ["https://vercel.com/docs/ai-gateway/sdks-and-apis/openai-chat-completions/embeddings"]},
    "zai-cn": {"name": ("智谱开放平台（中国大陆）", "BigModel (Mainland China)"), "provider": "zai", "region": "cn", "backend": "openai",
               "url": "https://open.bigmodel.cn/api/paas/v4", "model": "embedding-3", "key_env": "ZAI_API_KEY",
               "sources": ["https://docs.bigmodel.cn/api-reference/模型-api/文本嵌入"]},
    "dashscope": {"name": ("阿里云百炼", "Alibaba Cloud Model Studio"), "provider": "dashscope", "backend": "openai", "follow_url": True,
                  "url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1", "model": "text-embedding-v4", "key_env": "DASHSCOPE_API_KEY",
                  "sources": ["https://www.alibabacloud.com/help/en/model-studio/embedding-interfaces-compatible-with-openai"]},
    "siliconflow": {"name": ("硅基流动（国际站）", "SiliconFlow (international)"), "provider": "siliconflow", "region": "global", "backend": "openai",
                    "url": "https://api.siliconflow.com/v1", "model": "Qwen/Qwen3-Embedding-8B", "key_env": "SILICONFLOW_API_KEY",
                    "sources": ["https://docs.siliconflow.com/en/api-reference/embeddings/create-embeddings"]},
    "siliconflow-cn": {"name": ("硅基流动（中国站）", "SiliconFlow (China site)"), "provider": "siliconflow", "region": "cn", "backend": "openai",
                       "url": "https://api.siliconflow.cn/v1", "model": "BAAI/bge-large-zh-v1.5", "key_env": "SILICONFLOW_API_KEY",
                       "sources": ["https://docs.siliconflow.cn/docs/api/embeddings-post"]},
    "ollama": {"name": "Ollama", "provider": "ollama", "local": True, "backend": "openai", "url": "http://127.0.0.1:11434/v1", "model": "nomic-embed-text",
               "key_env": "", "note": ("先在服务器上 ollama pull nomic-embed-text（或者选你装好的别的向量模型）",
                                       "Run ollama pull nomic-embed-text on the server first (or pick another embedding model you have)"),
               "sources": ["https://docs.ollama.com/api/openai-compatibility"]},
    "lmstudio": {"name": "LM Studio", "provider": "lmstudio", "local": True, "backend": "openai", "url": "http://127.0.0.1:1234/v1", "model": None,
                 "key_env": "", "note": ("选一个 LM Studio 里装好的向量模型", "Pick an embedding model you've loaded in LM Studio"),
                 "sources": ["https://lmstudio.ai/docs/developer/openai-compat"]},
}

# —— 只做媒体的几家（第 3 批，2026-10-19）：转写、朗读、生图的服务商，不是对话模型（不进 PROVIDERS、不进模型列表）—————————————————————
# engine/media.py 按 engine.use.<用途> 的「服务商/型号」找到这里：url = 接口的根，key_env = 密钥变量名（app 里存的密钥也存在这个名字下），
# does = 能做哪几样（媒体的用途名，engine/media.PURPOSES）。engine.providers.<id> 照样能改 url / key_env（自己的代理、测试的假服务）。
# 本机的命令（whisper-cli、ffmpeg、pdftoppm）不在这里：写 local/<命令>，探测到才有（engine/media.local_command）。
MEDIA: dict[str, dict] = {
    "deepgram": {"name": "Deepgram", "url": "https://api.deepgram.com/v1", "key_env": "DEEPGRAM_API_KEY", "does": ("stt", "tts"),
                 "key_url": "https://console.deepgram.com", "docs": "https://developers.deepgram.com/reference/speech-to-text/listen-pre-recorded",
                 "sources": ["https://developers.deepgram.com/reference/speech-to-text/listen-pre-recorded",
                             "https://developers.deepgram.com/reference/text-to-speech/speak-request", "https://deepgram.com/pricing"]},
    "elevenlabs": {"name": "ElevenLabs", "url": "https://api.elevenlabs.io/v1", "key_env": "ELEVENLABS_API_KEY", "does": ("tts", "stt", "music"),
                   "key_url": "https://elevenlabs.io/app/settings/api-keys", "docs": "https://elevenlabs.io/docs/api-reference/text-to-speech/convert",
                   "sources": ["https://elevenlabs.io/docs/api-reference/text-to-speech/convert",
                               "https://elevenlabs.io/docs/api-reference/speech-to-text/convert", "https://elevenlabs.io/pricing/api",
                               "https://elevenlabs.io/docs/api-reference/music/compose"]},
    "fal": {"name": "fal", "url": "https://fal.run", "key_env": "FAL_KEY", "does": ("image", "video_gen", "music"),
            "key_url": "https://fal.ai/dashboard/keys", "docs": "https://docs.fal.ai/model-apis/model-endpoints/synchronous-requests",
            "sources": ["https://docs.fal.ai/model-apis/model-endpoints/synchronous-requests", "https://docs.fal.ai/model-apis/authentication",
                        "https://fal.ai/docs/model-apis/model-endpoints/queue"]},
    "comfyui": {"name": "ComfyUI", "url": "http://127.0.0.1:8188", "key_env": "", "does": ("image",), "local": True,
                "docs": "https://docs.comfy.org/development/comfyui-server/comms_routes",
                "sources": ["https://docs.comfy.org/development/comfyui-server/comms_routes"]},
}

# —— 按用途选模型的推荐：理由 + 代价（只是建议，不强制）—————————————————————————————————————
# 一个用途一项 {"why", "cost"}，可以再有 prefer（按顺序挑第一个连上、选得到的：评测里好的那几个）和 models（单独写了理由 + 代价的型号）。
# card / doorman 的 prefer 和数字来自 10/19 的评测（server/evals 的虚构题库：名片 Agent 那套、Doorman 那套），不是这里现算的
RECOMMEND: dict[str, dict] = {
    "chat": {"why": ("你和它说话最多：挑你最信得过的那一个", "It's the one you talk to most: pick the one you trust most"),
             "cost": ("大部分 token 花在这里", "Most of your tokens go here")},
    "background": {"why": ("定时任务、心跳、后台任务没人在看：便宜、快的就够", "Scheduled jobs, heartbeats and background tasks run unattended: a cheaper, faster model is enough"),
                   "cost": ("每次定时、每个后台任务都算，积少成多", "Every scheduled run and background task counts, and they add up")},
    # 写作：连着的哪一家 suggest 里写了 writing 就推荐它；models 里有这个型号自己的理由 + 代价就用它的，没有用这一段的
    "writing": {"why": ("长文、文书（报告、申请信、讲稿）看文笔，也要一口气写得长：挑写得最好的那一个",
                        "Long pieces and documents (reports, letters, talks) need good prose and long output: pick the best writer you have"),
                "cost": ("一篇长文大部分是输出 token，按输出价算（比输入贵好几倍）；不常写就花得不多",
                         "A long piece is mostly output tokens, billed at the output price (several times the input price); little if you rarely write"),
                "models": {"google/gemini-4-argon": {
                    "why": ("Google 的旗舰推理模型：上下文 1M token，一次默认能写 6.5 万 token（开长输出能到 1M），整份资料放得下",
                            "Google's flagship reasoning model: a 1M-token context and 65k tokens of output by default (up to 1M with long decode), "
                            "so whole sources fit"),
                    "cost": ("按量计费，每百万 token：2026 年 11 月 30 日以前输入 $2、输出 $10，12 月 1 日起 $4 / $20（思考按输出算）",
                             "Pay as you go, per million tokens: $2 in / $10 out through 30 November 2026, $4 / $20 from 1 December "
                             "(thinking counts as output)")}}},
    # 外人回合（名片 Agent、Doorman）分开推荐，outsider 那一行的推荐照名片 Agent 的。评测里推荐的都没连上：照外人回合的挑法
    # （便宜、快、不花套餐），理由 + 代价用这里通用的那一段
    "card": {"why": ("名片 Agent 替你回朋友：不能花你的套餐，要答得对、不该说的不说", "The card agent answers friends for you: it can't use your plan, and must get things right without saying what it shouldn't"),
             "cost": ("有人来找才花，一般很少", "Only spent when someone reaches out; usually little"),
             "prefer": ("anthropic/claude-opus-5-5", "moonshot/kimi-k3"),
             "models": {
                 "anthropic/claude-opus-5-5": {
                     "why": ("名片 Agent 的评测里答得最好；外人回合不花你的套餐，这里用按量计费的 API", "Best in the card-agent evals; other people's turns never use your plan, so this is the pay-as-you-go API"),
                     "cost": ("一问大约 1–2 美分（按量：每百万 token 输入 $4、输出 $20）", "About 1–2 US cents a question (pay as you go: $4 in / $20 out per million tokens)")},
                 "moonshot/kimi-k3": {
                     "why": ("没连 Anthropic 时推荐它（名片 Agent 评测里最好的是 Claude Opus 5.5，要连 Anthropic 才有）",
                             "The pick without Anthropic (Claude Opus 5.5 did best in the card-agent evals, but needs an Anthropic key)"),
                     "cost": ("按量：每百万 token 输入 $3、输出 $15", "Pay as you go: $3 in / $15 out per million tokens")}}},
    "doorman": {"why": ("Doorman 在名片 Agent 的话发出去之前把关：要拦得住、回得快，不能花你的套餐",
                        "Doorman checks the card agent's replies before they go out: it must catch problems, answer fast and can't use your plan"),
                "cost": ("名片 Agent 每发一句查一次，便宜的就够", "One check per card-agent reply; a cheap model is enough"),
                "prefer": ("moonshot/kimi-k3",),
                "models": {
                    "moonshot/kimi-k3": {
                        "why": ("评测（虚构题库）23/23 都判对了，注入的 3/3 都拦下了，中位 6.9 秒；和名片 Agent 用不同的一家更稳：一家被绕过，另一家还拦得住",
                                "23/23 right in the evals (made-up cases), 3/3 injections caught, 6.9 s median; safer on a different provider from the card agent, "
                                "so one being fooled doesn't let a message through"),
                        "cost": ("每题大约 0.25 美分（按量：每百万 token 输入 $3、输出 $15）", "About 0.25 US cents a check (pay as you go: $3 in / $15 out per million tokens)")}}},
    "embeddings": {"why": ("记忆能按意思找到，不只是关键词；你的笔记和对话会发给这一家去算", "Memory search finds things by meaning, not just keywords; your notes and chats are sent to this provider"),
                   "cost": ("建索引时花一点，之后很少；本机的 Ollama 不花钱", "A little while indexing, then very little; local Ollama is free")},
}
KIND_NOTES: dict[str, tuple[str, str]] = {
    "cloud": ("按用量付费，记在你自己的账上", "Pay as you go, billed to your own account"),
    "local": ("在这台电脑上跑：不花钱、不出门，慢一些", "Runs on this computer: free and private, but slower"),
    "plan": ("花你自己的套餐，只给你自己的回合用", "Uses your own plan, only for your own turns"),
    "custom": ("你自己填的地址", "An address you entered"),
}


def text(v) -> str | None:
    """(中文, English) → 按这个请求的语言挑一个；字符串原样；None → None。"""
    if v is None:
        return None
    if isinstance(v, tuple):
        from i18n import L
        return L(v[0], v[1])
    return str(v)


def entry(pid: str) -> dict | None:
    """云端或本机的一家（不在目录里 = None）。"""
    return CLOUD.get(pid) or LOCAL.get(pid)


def bare(pid: str, model: str) -> str:
    """型号名的规范写法：这一家列出来带前缀的（list_prefix，Google 的 models/<型号>）去掉前缀；别的原样。"""
    pre = (entry(pid) or {}).get("list_prefix")
    return model[len(pre):] if pre and model.startswith(pre) and len(model) > len(pre) else model


def kind_of(pid: str) -> str | None:
    return "cloud" if pid in CLOUD else "local" if pid in LOCAL else "login" if pid in LOGINS else None


REGION_KEYS = ("url", "key_url", "test_model", "list_url")


def region(e: dict, rid: str | None) -> dict:
    """一家的某个地区（没写 / 没有这个 = 默认的那个：url、key_url、test_model、list_url 都是这一家自己的）。"""
    regs = e.get("regions") or {}
    r = regs.get(rid) if rid else None
    base = {k: e.get(k) for k in REGION_KEYS}
    return {**base, **{k: v for k, v in (r or {}).items() if k in REGION_KEYS}} if r else base


def region_of(e: dict, url: str | None) -> str | None:
    """现在的地址是哪个地区的（没有地区的 → None；对不上 = custom）。"""
    regs = e.get("regions") or {}
    if not regs:
        return None
    u = str(url or e["url"]).rstrip("/")
    return next((rid for rid, r in regs.items() if str(r["url"]).rstrip("/") == u), "custom")


def engine_providers() -> dict[str, dict]:
    """进 engine/models.PROVIDERS 的那一份：{名字: {"api", "url", "key_env", …脾气}}。Claude 不带 url（见文件开头）。"""
    out: dict[str, dict] = {}
    for pid, e in {**CLOUD, **LOCAL}.items():
        p: dict = {"api": e["api"]}
        if e["api"] != ANTHROPIC:
            p["url"] = e["url"]
        p["key_env"] = e.get("key_env", "")
        p.update(e.get("engine") or {})
        out[pid] = p
    return out


def problems() -> list[str]:
    """目录写得对不对：每家必填的都有、地址像样、键不重复。→ 问题（空 = 没问题）。测试跑它。"""
    import re
    out: list[str] = []
    url = re.compile(r"^https?://[^\s/]+(?:/[^\s]*)?$")
    for pid, e in CLOUD.items():
        out += [f"{pid}: missing {k}" for k in REQUIRED if not e.get(k) and e.get(k) != ""]
        if e.get("api") not in (OPENAI, ANTHROPIC, RESPONSES):
            out.append(f"{pid}: bad api {e.get('api')!r}")
        if e.get("list") not in ("openai", "anthropic", "ollama", "none"):
            out.append(f"{pid}: bad list {e.get('list')!r}")
        if e.get("list") == "none" and not e.get("test_model"):
            out.append(f"{pid}: can't list models and has no test_model")
        if not re.fullmatch(r"[A-Z_][A-Z0-9_]*", str(e.get("key_env") or "")):
            out.append(f"{pid}: bad key_env")
        for k in ("url", "key_url", "docs", "list_url"):
            if e.get(k) and not url.match(str(e[k])):
                out.append(f"{pid}: bad {k}")
        if set(e.get("caps") or {}) != set(CAP_KEYS) or any(v not in (True, False, "some", None) for v in (e.get("caps") or {}).values()):
            out.append(f"{pid}: caps must have {CAP_KEYS}, each True / False / \"some\" / None")
        for rid, r in (e.get("regions") or {}).items():
            if not url.match(str(r.get("url") or "")) or not r.get("label"):
                out.append(f"{pid}: region {rid} needs label and url")
        if e.get("regions") and str(next(iter(e["regions"].values()))["url"]) != e["url"]:
            out.append(f"{pid}: the first region must be the default url")
        if not all(str(s).startswith("https://") for s in e.get("sources") or []):
            out.append(f"{pid}: sources must be https links")
    for pid, e in LOCAL.items():
        out += [f"{pid}: missing {k}" for k in LOCAL_REQUIRED if not e.get(k)]
        if e.get("key_env") != "":
            out.append(f"{pid}: local servers take no key by default (key_env \"\")")
        if not str(e.get("url") or "").startswith("http://127.0.0.1:"):
            out.append(f"{pid}: local url should be on 127.0.0.1")
    dup = set(CLOUD) & set(LOCAL) | (set(CLOUD) | set(LOCAL)) & set(LOGINS)
    out += [f"{x}: listed twice" for x in sorted(dup)]
    for sid, s in SEARCH.items():
        if not s.get("name") or not s.get("docs") or not (s.get("keyless") or (s.get("key_env") and s.get("key_url"))):
            out.append(f"search {sid}: needs name, docs and key_env + key_url (or keyless)")
    for eid, e in EMBEDDINGS.items():
        if e.get("backend") not in ("gemini", "openai") or e.get("provider") not in {**CLOUD, **LOCAL}:
            out.append(f"embeddings {eid}: bad backend or provider")
        if e.get("backend") == "openai" and not url.match(str(e.get("url") or "")):
            out.append(f"embeddings {eid}: openai backend needs a url")
    for mid, e in MEDIA.items():
        if not e.get("name") or not url.match(str(e.get("url") or "")) or not e.get("docs") or not e.get("does") or not e.get("sources"):
            out.append(f"media {mid}: needs name, url, docs, does and sources")
        if not e.get("local") and not (re.fullmatch(r"[A-Z_][A-Z0-9_]*", str(e.get("key_env") or "")) and e.get("key_url")):
            out.append(f"media {mid}: needs key_env and key_url (or local)")
        if not set(e.get("does") or ()) <= {"vision", "pdf", "video", "stt", "tts", "image", "video_gen", "music"}:  # = models.MEDIA_PURPOSES
            out.append(f"media {mid}: does names an unknown purpose")
    out += [f"{x}: listed twice (media)" for x in sorted(set(MEDIA) & (set(CLOUD) | set(LOCAL) | set(LOGINS)))]
    return out
