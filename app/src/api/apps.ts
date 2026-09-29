// 连接器（2026-09-29）：第三方应用经 MCP 接进来（Notion、Linear……或者任何 MCP 地址）。在 app 里点一下跳去对方的授权页（OAuth），
// 回来时带着 <scheme>://oauth/callback?code=…&state=…（navigation.tsx 接住交给服务器换令牌）。令牌存在服务器上（server/apps.py），
// 工具经 /mcp 给 claw 用；每个工具 自动 / 先问我 / 关，哪些 Agent 能用。「先问我」的调用出一张收件箱卡，你点了服务器才去做。
import Constants from 'expo-constants';
import { Platform } from 'react-native';
import { httpStatus, request } from './base';

export type AppLevel = 'auto' | 'ask' | 'off';
export type AppStatus = 'connected' | 'needs_auth' | 'error';
export type AppAuth = 'oauth' | 'token' | 'none';

/** 图标块：1–3 个字 + 底色 + 字色（目录里给的；自定义的没有，用名字的头一个字）。 */
export interface AppLook { mono?: string | null; bg?: string | null; fg?: string | null; border?: string | boolean | null }

export interface AppSummary extends AppLook {
  id: string;
  name: string;
  url: string;
  catalog?: string | null;
  auth: AppAuth;
  status: AppStatus;
  error?: string | null;
  /** 连的是哪个账号 / 工作区（服务器顺手能知道的时候才有） */
  account?: string | null;
  connectedAt?: string | null;
  updatedAt?: string | null;
  toolCount: number;
  readCount: number;
  writeCount: number;
  offCount?: number;
  agents: string[];
  policy: { read: AppLevel; write: AppLevel };
  overrides?: Record<string, AppLevel>;
  desc?: string | null;
  custom?: boolean;
  category?: string | null;
}

export interface CatalogEntry extends AppLook {
  id: string;
  name: string;
  url: string;
  category: string;
  desc?: string | { zh?: string; en?: string } | null;
  auth: AppAuth;
  hint?: string | null;
  installed: boolean;
}

export interface AppTool { name: string; title?: string | null; description?: string | null; kind: 'read' | 'write'; level: AppLevel; overridden: boolean }
export interface AppDetail extends AppSummary { tools: AppTool[] }
export interface AppsList { apps: AppSummary[]; catalog: CatalogEntry[]; agents: { id: string; name: string }[] }
type Added = { app: AppSummary; authorizeUrl?: string | null; state?: string | null };

/** 授权完跳回 app 的地址：<这个壳的 scheme>://oauth/callback（OpenMousse 是 openmousse，自己起名的壳是它自己的 scheme）。 */
export function redirectUri(): string {
  const s = Constants.expoConfig?.scheme;
  const scheme = (Array.isArray(s) ? s[0] : s) || 'openmousse';
  return `${scheme}://oauth/callback`;
}

/** 网页版接不住 <scheme>:// 的跳回：OAuth 类的只能在手机 app 里连。 */
export const oauthHere = () => Platform.OS !== 'web';

/** 目录里的说明：服务器按 Accept-Language 回好了一句，老一点的回 {zh, en}。 */
export function descOf(d: CatalogEntry['desc'], zh: boolean): string {
  if (!d) return '';
  if (typeof d === 'string') return d;
  return (zh ? d.zh || d.en : d.en || d.zh) || '';
}

const enc = encodeURIComponent;

type One = { app: AppDetail };

export const appsApi = {
  list: () => request<AppsList>('/api/apps'),
  get: (id: string) => request<One>(`/api/apps/${enc(id)}`).then((r) => r.app),
  /** 连目录里的一个：OAuth 的回 authorizeUrl（去浏览器授权），不用授权的当场连上。 */
  addCatalog: (catalog: string) => request<Added>('/api/apps', { method: 'POST', body: { catalog, redirect_uri: redirectUri() }, timeoutMs: 60000 }),
  addCustom: (b: { name: string; url: string; auth: AppAuth; token?: string }) =>
    request<Added>('/api/apps', { method: 'POST', body: { ...b, redirect_uri: redirectUri() }, timeoutMs: 60000 }),
  /** 重新授权 / 换个账号（OAuth：回 authorizeUrl）；要令牌的换一把令牌（{token}，不对就还用旧的）；不用登录的重新读一遍工具。 */
  connect: (id: string, token?: string) => request<Added>(`/api/apps/${enc(id)}/connect`, {
    method: 'POST', body: token !== undefined ? { token } : { redirect_uri: redirectUri() }, timeoutMs: 60000,
  }),
  /** 授权页跳回来的 code / state 交给服务器换令牌。 */
  callback: (b: { state: string; code?: string; error?: string; error_description?: string }) =>
    request<{ app: AppSummary }>('/api/apps/oauth/callback', { method: 'POST', body: b, timeoutMs: 90000 }),
  patch: (id: string, b: { policy?: Partial<Record<'read' | 'write', AppLevel>>; overrides?: Record<string, AppLevel | null>; agents?: string[] }) =>
    request<One>(`/api/apps/${enc(id)}`, { method: 'PATCH', body: b }).then((r) => r.app),
  refresh: (id: string) => request<One>(`/api/apps/${enc(id)}/refresh`, { method: 'POST', timeoutMs: 90000 }).then((r) => r.app),
  /** 断开：服务器顺手去对方那里吊销令牌（revoked = 吊销成功），还没处理的这个应用的卡一起撤回。 */
  remove: (id: string) => request<{ revoked: boolean }>(`/api/apps/${enc(id)}`, { method: 'DELETE', timeoutMs: 30000 }),
};

/** 服务器老，还没有连接器（404 / 405）。 */
export const noApps = (e: unknown) => [404, 405].includes(httpStatus(e) ?? 0);

/** 从 <scheme>://oauth/callback?… 里取出 state、code 或 error。不是这种地址就 null。 */
export function parseOAuthCallback(url: string): { state: string; code?: string; error?: string; error_description?: string } | null {
  const m = /^[a-z][a-z0-9+.-]*:\/\/oauth\/callback\/?\?([^#]*)/i.exec(url || '');
  if (!m) return null;
  const q: Record<string, string> = {};
  for (const kv of m[1].split('&')) {
    const [k, v = ''] = kv.split('=');
    if (!k) continue;
    try { q[decodeURIComponent(k)] = decodeURIComponent(v.replace(/\+/g, ' ')); } catch { q[k] = v; }
  }
  if (!q.state) return null;
  return { state: q.state, code: q.code || undefined, error: q.error || undefined, error_description: q.error_description || undefined };
}
