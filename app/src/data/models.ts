// 模型目录：名字、简称、计费方式、适合做什么。这是我们维护的元数据，不是示例数据。
// 哪些模型真的能用、默认链和回退顺序以服务器的 /api/models 为准（来自 openclaw.json），界面只显示允许列表里的。
// note 是界面文字，写成 getter，读的时候按当前语言取；billing / cost 是给程序比较的值，不翻译，显示用 billingLabel / costLabel。
import { L } from '../i18n';
import type { Billing, CostTier, ModelOption } from './types';

export const MODELS: ModelOption[] = [
  // 两条产品线：日常对话（Opus 5.5 / GPT-5.6 Sol），任务执行（Fable 5.1 / GPT-6 Astra / K3）。
  { id: 'anthropic/claude-opus-5-5', name: 'Claude Opus 5.5', short: 'Opus 5.5', billing: '订阅', cost: '贵', get note() { return L('日常对话默认', 'Default for everyday chat'); }, featured: true },
  { id: 'anthropic/claude-fable-5-1', name: 'Claude Fable 5.1', short: 'Fable', billing: '订阅', cost: '贵', get note() { return L('任务执行：委派任务的默认模型', 'Tasks: default for delegated work'); }, featured: true },
  { id: 'openai/gpt-5.6-sol', name: 'GPT-5.6 Sol', short: 'GPT-5.6', billing: '订阅', cost: '中', get note() { return L('日常对话备选', 'Everyday chat alternative'); }, featured: true },
  { id: 'openai/gpt-6-astra', name: 'GPT-6 Astra', short: 'Astra', billing: '订阅', cost: '中', get note() { return L('任务执行：工具调用与交替推理', 'Tasks: tool use with interleaved reasoning'); }, featured: true },
  { id: 'moonshot/kimi-k3', name: 'Kimi K3', short: 'K3', billing: 'API', cost: '贵', get note() { return L('任务执行：长文档与深度推理', 'Tasks: long documents and deep reasoning'); }, featured: true },
  { id: 'moonshot/kimi-k2.6', name: 'Kimi K2.6', short: 'K2.6', billing: 'API', cost: '省', get note() { return L('仅用于定时检查等轻量后台任务，不用于对话', 'Only for light background jobs such as scheduled checks, not chat'); }, featured: false },
  { id: 'anthropic/claude-opus-5', name: 'Claude Opus 5', short: 'Opus 5', billing: '订阅', cost: '贵', get note() { return L('上一代日常对话模型', 'Previous-generation everyday model'); }, featured: false },
  { id: 'anthropic/claude-sonnet-5', name: 'Claude Sonnet 5', short: 'Sonnet 5', billing: '订阅', cost: '中', get note() { return L('用于节省额度', 'For saving quota'); }, featured: false },
  { id: 'openai/gpt-6-sol', name: 'GPT-6 Sol', short: 'GPT-6', billing: 'API', cost: '贵', get note() { return L('尚未进入订阅目录，按 API 计费', 'Not yet in the subscription catalog; billed via API'); }, featured: false },
  { id: 'openai/gpt-6-luna', name: 'GPT-6 Luna', short: 'Luna', billing: 'API', cost: '省', get note() { return L('尚未进入订阅目录，按 API 计费', 'Not yet in the subscription catalog; billed via API'); }, featured: false },
  { id: 'google/gemini-3.8-flash', name: 'Gemini 3.8 Flash', short: 'Flash', billing: '免费', cost: '省', get note() { return L('最终备用', 'Last-resort fallback'); }, featured: false },
  { id: 'dashscope/qwen3.8-max', name: 'Qwen3.8 Max', short: 'Qwen', billing: 'API', cost: '中', get note() { return L('中文分析', 'Chinese-language analysis'); }, featured: false },
  { id: 'zai/glm-5.3', name: 'GLM-5.3', short: 'GLM', billing: 'API', cost: '省', get note() { return L('事实核查', 'Fact-checking'); }, featured: false },
  { id: 'deepseek/deepseek-chat', name: 'DeepSeek', short: 'DeepSeek', billing: 'API', cost: '省', get note() { return L('通用', 'General purpose'); }, featured: false },
];

export const FALLBACK_CHAIN = ['anthropic/claude-opus-5-5', 'openai/gpt-5.6-sol', 'moonshot/kimi-k3', 'google/gemini-3.8-flash'];

/** 计费标签的显示文字（值本身 '订阅' / 'API' / '免费' 不变，程序拿它比较）。 */
export const billingLabel = (b: Billing): string => (b === '订阅' ? L('订阅', 'Subscription') : b === '免费' ? L('免费', 'Free') : b);

/** 价位标签的显示文字（值 '省' / '中' / '贵' 不变）。 */
export const costLabel = (c: CostTier): string => (c === '贵' ? L('高成本', 'High cost') : c === '省' ? L('低成本', 'Low cost') : L('中等成本', 'Medium cost'));
