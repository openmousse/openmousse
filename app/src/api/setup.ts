// 第一次打开的三步和设置中心（2026-10-19，第 9 批）：server/engine/setup_api.py 的 /api/setup/…、ChatGPT 套餐的 /api/engine/chatgpt/…、
// 「手」页读的 /api/security。/api/setup 只认主人的设备（app 的令牌、Tailscale 里你自己的设备），别的令牌一律 403：出错照 showError 说。
// 密钥只往服务器送一次（测一下、保存），服务器永远不回原文：只回有没有、从哪来、末四位、哪天存的。
import { httpStatus, request } from './base';

const enc = encodeURIComponent;

/** 测一下没通的原因：kind 一类（auth、billing、network……，见 server/engine/probe.py），message 是服务器写好的一句 + 对方原话（密钥已抹掉）。 */
export interface ProbeError { kind: string; message: string }

/** 一把密钥给 app 看的那一面：source = 在 app 里存的（app）/ 服务器的环境变量（env）/ env_file / server.json 里直接写的（config）。 */
export interface KeyInfo { name?: string | null; present: boolean; source: 'app' | 'env' | 'env_file' | 'config' | null; hint: string | null; savedAt: string | null }

type Caps = { tools: boolean | 'some' | null; images: boolean | 'some' | null; reasoning: boolean | 'some' | null; embeddings: boolean | 'some' | null };
type Listed = { count: number; at: string | null };

export interface CloudRow {
  id: string; kind: 'cloud'; name: string; api: string; url: string; defaultUrl: string;
  /** 有地区的那几家：现在选的是哪个（地址对不上 = custom）；没有地区 = null */
  region: string | null; regions: { id: string; label: string; url: string; /** 这个地区去哪拿密钥（老服务器没有：用 keyUrl） */ keyUrl?: string | null }[];
  keyEnv: string; key: KeyInfo; keyUrl: string | null; docs: string | null; caps: Caps; market: string | null; note: string | null;
  connected: boolean; models: Listed;
  /** 列得出模型（false 的几家测的时候要写一个型号名） */
  canList: boolean; suggest: { chat?: string; cheap?: string; writing?: string }; cost: string | null;
}
export interface LocalRow {
  id: string; kind: 'local'; name: string; api: string; url: string; defaultUrl: string; detected: boolean; version: string | null;
  models: string[]; connected: boolean; configured: boolean; start: string | null; install: string | null; docs: string | null;
  /** 只有 Mac 上有（MLX 这类） */
  mac: boolean; caps: Caps; cost: string | null;
}
export interface LoginRow {
  id: 'claude-code' | 'chatgpt' | 'codex'; kind: 'login'; role: 'chat' | 'helper'; name: string; ready: boolean;
  auth?: string | null; billing?: string | null; plan?: string | null; models?: string[]; enabled?: boolean; checking?: boolean;
  connected?: boolean; planEnabled?: boolean; needsReconnect?: boolean; lastError?: { code?: string; message?: string; at?: string } | null;
  how: string | null; docs: string | null; cost?: string | null;
}
export interface CustomRow {
  id: string; kind: 'custom'; name: string; api: string; url: string; keyEnv: string | null; key: KeyInfo | null; keyless: boolean;
  valid: boolean; connected: boolean; models: Listed; cost: string | null;
}
export interface OpenclawRow { id: string; kind: 'openclaw'; name: string; api: string; url: string | null; readOnly: true; key: KeyInfo | null; connected: boolean; models: Listed }
export type ProviderRow = CloudRow | LocalRow | LoginRow | CustomRow | OpenclawRow;

export interface ProvidersList {
  providers: ProviderRow[];
  custom: { hint: string | null; apis: string[] };
  /** 服务器是不是 Mac（「仅 Mac」的那几家本机跑的；老服务器没有这一项） */
  host?: { mac: boolean };
}

/** 测一下的结果。via：list（列出了模型）/ request（发了一句最小的请求）/ detect（本机探到）/ login（命令行登录现查了一遍）。 */
export interface TestResult { ok: boolean; via: string | null; models: string[]; count: number; listed: boolean; ms: number; error: ProbeError | null; version?: string | null }

/** 只连了这一家、主对话还没选模型时，服务器存的时候顺手用它：outsider = null 是套餐（外人回合花不了，要另选）。 */
export interface AutoChoice { provider: string; chat: string; outsider: true | null; backup?: string | null }
export interface Saved { provider: ProviderRow; saved: { key: boolean; config: boolean }; backup: string | null; auto: AutoChoice | null }
export interface Removed { removed: { key: boolean; config: boolean }; affected: string[]; backup: string | null }

export interface Recommendation { value: string | { id: string; model: string | null } | null; why: string | null; cost: string | null }
/** 最近一次用套餐没做成、之后也没做成过（kind：auth 没登录 / quota 额度用完 / broken 命令坏了 / timeout 超时 / error 别的）。 */
export interface PlanFailure { at: string; kind: string; error: string }
export interface Purpose {
  id: 'chat' | 'background' | 'writing' | 'json' | 'compact' | 'outsider' | 'embeddings'; label: string; applies: boolean; current: unknown;
  effective?: string | null; usable?: boolean; follows?: string | null; recommended: Recommendation | null; why?: string;
  // 服务器有 setup.purposes.oneshot 才有：effective 是谁家的套餐（按量的 = null）、这台机器上现在跑不跑得了、这件事能不能用套餐、
  // 出问题时按顺序换谁（空 = 不自动换）、最近一次套餐没做成；thenOwn（只有 compact）：套餐没做成，最后用这个对话自己的模型
  plan?: string | null; planReady?: boolean | null; mayPlan?: boolean; backups?: string[]; lastFailure?: PlanFailure | null; thenOwn?: boolean;
}
export interface ModelChoice { ref: string; provider: string; providerName: string; model: string; kind: string; plan: string | null; listed: boolean; oneShot?: boolean }
export interface EmbeddingChoice { id: string; name: string; provider: string; backend: string; model: string | null; dims: number | null; local: boolean; note: string | null; models: string[] | null }
export interface Purposes {
  claw: string; purposes: Purpose[]; choices: { models: ModelChoice[]; embeddings: EmbeddingChoice[] };
  providers: Record<string, { kind: string; name: string; listed: boolean }>; auto: AutoChoice | null;
}

export interface SearchRow {
  id: string; name: string; keyless: boolean; selfHosted: boolean; unofficial: boolean; keyEnv: string | null; key: KeyInfo | null;
  url: string | null; connected: boolean; keyUrl: string | null; docs: string | null; free: string | null; note: string | null;
}
/** provider = 现在真用的那一家；chosen = 写死的那一家（null = 按顺序挑有密钥的）。 */
export interface SearchView { enabled: boolean; provider: string | null; chosen: string | null; off: boolean; auto: boolean; providers: SearchRow[]; warning?: string }
export interface SearchTest { ok: boolean; results: number; ms: number; error: ProbeError | null }

export interface TreeInfo {
  package: boolean; initialized: boolean; selected: 'package' | 'script' | null; storage: 'sqlite' | 'markdown' | null; path: string | null;
  memories: number | null; canChoose: boolean; why: string | null;
}
export interface CurrentEmbeddings { id: string; backend: string; model: string; dims: number | null; label: string }
export interface MemoryView { tree: TreeInfo; embeddings: Record<string, unknown>; current: CurrentEmbeddings | null; backup?: string | null }
export interface EmbedTest { ok: boolean; dims: number | null; ms: number; error: ProbeError | null }

export type LayerStatus = 'ok' | 'missing' | 'optional' | 'off' | 'external' | 'warn';
export interface Layer { status: LayerStatus; line: string; missing: string[] }
export interface Overview {
  firstRun: boolean; noModel: boolean; needsSetup: boolean; claw: { kind: string; name: string };
  layers: {
    models: Layer & { chat?: string | null; background?: string | null };
    search: Layer & { provider?: string | null };
    memory: Layer & { tree?: TreeInfo };
    embeddings: Layer & { current?: CurrentEmbeddings };
    connections: Layer & { apps?: number; mcp?: number; mcpErrors?: number; skills?: number; claudeCode?: boolean; chatgpt?: boolean };
    hands: Layer & { files?: boolean; webFetch?: boolean; webSearch?: boolean; sandbox?: boolean; hostCommands?: string[]; agents?: HandsAgent[] };
  };
  config: { claw: string; engineSection: boolean; openclawJson: boolean };
  steps: { id: 'models' | 'search' | 'memory'; done: boolean; optional?: boolean }[];
}

export interface UsageAcc { turns: number; in: number; out: number; cacheRead: number; cacheWrite: number; cost: number; unpricedTurns: number; planTurns: number; notional: number | null }
export interface EmbedAcc { requests: number; texts: number; tokens: number; estimatedTokens: number; cost: number; unpricedTokens: number }
export interface Usage {
  since: string; until: string; days: number; currency: string; total: UsageAcc;
  byModel: (UsageAcc & { model: string; provider: string | null; plan: string | null; billing: 'metered' | 'unpriced' | 'plan' | 'mixed' })[];
  byPurpose: (UsageAcc & { purpose: string })[];
  embeddings: { total: EmbedAcc; byModel: (EmbedAcc & { model: string; billing: string })[]; recordedSince: string | null };
}

/** ChatGPT 套餐（server/engine/chatgpt_plan.py）：授权在浏览器里做，手机上做完把地址栏整串粘回来。 */
export interface PlanStatus { enabled: boolean; connected: boolean; planEnabled: boolean; ready: boolean; plan: string | null; email: string | null; models: string[]; needsReconnect: boolean }
export interface PlanLogin { url: string; redirect: string; expiresAt: string; tunnel: string; registering: boolean }
export interface PlanLoginState { status: 'none' | 'pending' | 'done' | 'error' | 'expired' | 'cancelled'; error?: string | null }

/** 「手」页的一个 Agent（GET /api/setup 的 layers.hands.agents，主对话在前；老服务器没有）。 */
export interface HandsAgent { id: string; name: string; files: boolean; webFetch: boolean; webSearch: boolean; sandbox: boolean; browser: boolean; hostExec: boolean }

/** 「手」页（builtin）：GET /api/security 的 engine 一段。 */
export interface HostExecPolicy { agent: string; allow: string[]; ask: 'on-miss' | 'always' | 'off'; security: string; timeout: number }
export interface SandboxHands { agent: string; only: boolean; access: 'none' | 'ro' | 'rw' | string; browser: boolean }
export interface EngineSecurity { hostExec: HostExecPolicy[]; levels: Record<string, string>; sandbox: SandboxHands[]; pending: number }

/** 测一下没通时服务器回 200 + ok false + error：这是结果不是出错，照样交回去（request 见 ok false 会抛）。 */
async function result<T>(path: string, body: unknown): Promise<T> {
  try {
    return await request<T>(path, { method: 'POST', body, timeoutMs: 75000 });
  } catch (e) {
    const data = (e as { data?: unknown } | null)?.data;
    if (httpStatus(e) === 200 && data && typeof data === 'object') return data as T;
    throw e;
  }
}

export const setupApi = {
  overview: () => request<Overview>('/api/setup', { timeoutMs: 30000 }),
  /** probe = 0：不探本机（快）；check = 1：现查一遍 Claude Code / Codex 登没登录。 */
  providers: (opts?: { probe?: boolean; check?: boolean }) =>
    request<ProvidersList>(`/api/setup/models?probe=${opts?.probe === false ? 0 : 1}&check=${opts?.check ? 1 : 0}`, { timeoutMs: 30000 }),
  /** 测一下：带 key 是还没存的那把（不存），不带用存下的；model：列不了模型的那几家、或者「用一次真实回复验证模型」。 */
  test: (id: string, body: { key?: string; region?: string; url?: string; model?: string; api?: string } = {}) =>
    result<TestResult>(`/api/setup/models/${enc(id)}/test`, body),
  save: (id: string, body: { key?: string; region?: string; url?: string; name?: string; api?: string }) =>
    request<Saved>(`/api/setup/models/${enc(id)}`, { method: 'PUT', body, timeoutMs: 45000 }),
  remove: (id: string) => request<Removed>(`/api/setup/models/${enc(id)}`, { method: 'DELETE' }),
  purposes: (probe = true) => request<Purposes>(`/api/setup/purposes?probe=${probe ? 1 : 0}`, { timeoutMs: 30000 }),
  /** json / compact（setup.purposes.oneshot）：引用 | 'default'（json = 跟后台和定时一样，compact = 跟对话自己的模型）| null */
  setPurposes: (body: { chat?: string; background?: string | null; writing?: string | null; json?: string | null; compact?: string | null; outsider?: string | null; embeddings?: { id: string; model?: string | null } | 'off' }) =>
    request<Purposes & { backup: string | null }>('/api/setup/purposes', { method: 'PUT', body }),
  search: () => request<SearchView>('/api/setup/search'),
  setSearch: (body: { enabled: boolean; provider?: string | null }) => request<SearchView>('/api/setup/search', { method: 'PUT', body }),
  saveSearch: (id: string, body: { key?: string; url?: string }) => request<SearchView>(`/api/setup/search/${enc(id)}`, { method: 'PUT', body }),
  testSearch: (id: string, body: { key?: string; url?: string } = {}) => result<SearchTest>(`/api/setup/search/${enc(id)}/test`, body),
  removeSearch: (id: string) => request<SearchView & { removed: boolean }>(`/api/setup/search/${enc(id)}`, { method: 'DELETE' }),
  memory: () => request<MemoryView>('/api/setup/memory', { timeoutMs: 30000 }),
  setMemory: (body: { storage?: 'sqlite' | 'markdown'; path?: string; embeddings?: { id: string; model?: string | null } | 'off' }) =>
    request<MemoryView>('/api/setup/memory', { method: 'PUT', body, timeoutMs: 60000 }),
  testEmbeddings: (body: { id: string; model?: string | null }) => result<EmbedTest>('/api/setup/embeddings/test', body),
  usage: (days = 30) => request<Usage>(`/api/setup/usage?days=${days}`, { timeoutMs: 30000 }),
  /** 「手」页：builtin 下 /api/security 才带 engine 这一段（OpenClaw 那条路没有）。 */
  hands: () => request<{ engine?: EngineSecurity }>('/api/security', { timeoutMs: 60000 }).then((r) => r.engine ?? null),
};

export const planApi = {
  status: () => request<PlanStatus>('/api/engine/chatgpt'),
  start: () => request<PlanLogin>('/api/engine/chatgpt/login', { method: 'POST', body: { account: 'auto' } }),
  state: () => request<PlanLoginState>('/api/engine/chatgpt/login'),
  cancel: () => request<{ ok: boolean }>('/api/engine/chatgpt/login', { method: 'DELETE' }),
  /** 手机上授权完：浏览器地址栏里那一整串（http://127.0.0.1:<端口>/auth/callback?code=…）。 */
  callback: (url: string) => request<PlanStatus>('/api/engine/chatgpt/callback', { method: 'POST', body: { url }, timeoutMs: 60000 }),
};

/** 给助手起名字（server/naming.py，features 里有 naming 才有）：名字 = server.json 的 app_name，vibe = IDENTITY.md 里一行性格。 */
export interface Naming { ok: boolean; name: string; vibe: string; applies: boolean; backup?: string | null }

export const namingApi = {
  get: () => request<Naming>('/api/naming'),
  save: (b: { name?: string; vibe?: string }) => request<Naming>('/api/naming', { method: 'PUT', body: b }),
};

/** 套餐来的模型（claude-code/…、chatgpt/…）：外人回合（名片 Agent、Doorman）花不了。 */
export const isPlanRef = (ref: string | null | undefined) => !!ref && /^(claude-code|chatgpt)\//.test(ref);

/** 一家的名字缩成字母块里的一两个字（OpenAI → O、DeepSeek → D、通义千问 → 通）。 */
export function monoOf(name: string): string {
  const s = (name || '').trim();
  if (!s) return '?';
  const first = s[0];
  return /[A-Za-z0-9]/.test(first) ? first.toUpperCase() : first;
}
