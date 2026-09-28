// 服务器地址、接入令牌和请求工具。
// 网页版默认和接口同源（地址留空）；原生 app 第一次打开要在连接页填自己服务器的地址和令牌，存在本机（iOS 钥匙串 / 浏览器 localStorage）。
import { Platform } from 'react-native';
import { acceptLanguage, L } from '../lang';  // 从 lang 而不是 i18n 引：i18n.tsx 自己引了本文件，反过来引会成环

const KEY_SERVER = 'mousse.server';
const KEY_TOKEN = 'mousse.token';
const KEY_NAME = 'mousse.name';
const KEY_LANG = 'mousse.lang';
const KEY_AGENTS_VIEW = 'mousse.agentsView';
let base = '';
let token = '';
let loaded = false;

// —— 本机存储：web 用 localStorage；原生用 expo-secure-store（惰性 require，缺这个原生模块的旧包不会在加载时崩） ——
function secureStore(): { getItemAsync(k: string): Promise<string | null>; setItemAsync(k: string, v: string): Promise<void>; deleteItemAsync(k: string): Promise<void> } | null {
  if (Platform.OS === 'web') return null;
  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    return require('expo-secure-store');
  } catch {
    return null;
  }
}

async function readItem(key: string): Promise<string> {
  try {
    if (Platform.OS === 'web') return (typeof localStorage !== 'undefined' && localStorage.getItem(key)) || '';
    return (await secureStore()?.getItemAsync(key)) ?? '';
  } catch {
    return '';
  }
}

async function writeItem(key: string, value: string): Promise<void> {
  try {
    if (Platform.OS === 'web') {
      if (typeof localStorage === 'undefined') return;
      if (value) localStorage.setItem(key, value); else localStorage.removeItem(key);
      return;
    }
    const s = secureStore();
    if (!s) return;
    if (value) await s.setItemAsync(key, value); else await s.deleteItemAsync(key);
  } catch { /* 存不了就只在内存里 */ }
}

/** 启动时读一次。之后 getBase / getToken 都是同步的。 */
export async function loadServerConfig(): Promise<{ base: string; token: string }> {
  if (!loaded) {
    base = normalizeBase(await readItem(KEY_SERVER));
    token = (await readItem(KEY_TOKEN)).trim();
    loaded = true;
  }
  return { base, token };
}

export async function saveServerConfig(newBase: string, newToken: string): Promise<void> {
  base = normalizeBase(newBase);
  token = newToken.trim();
  loaded = true;
  await writeItem(KEY_SERVER, base);
  await writeItem(KEY_TOKEN, token);
}

export const loadAgentName = () => readItem(KEY_NAME);
export const persistAgentName = (n: string) => writeItem(KEY_NAME, n);
/** 界面语言：'zh' / 'en'，空 = 跟随系统。 */
export const loadLangPref = () => readItem(KEY_LANG);
export const saveLangPref = (v: string) => writeItem(KEY_LANG, v);
/** Agents 页竖排（list）还是横排（grid，两列卡片）。 */
export const loadAgentsView = () => readItem(KEY_AGENTS_VIEW);
export const saveAgentsView = (v: string) => writeItem(KEY_AGENTS_VIEW, v);

export function normalizeBase(v: string): string {
  let s = (v || '').trim().replace(/\/+$/, '');
  if (s && !/^https?:\/\//i.test(s)) {
    // 没写协议：IP、localhost、.local 是私网直连，按 http；域名一律 https（令牌不能明文走公网）
    const host = s.split('/')[0].replace(/:\d+$/, '');
    const plain = /^\d{1,3}(\.\d{1,3}){3}$/.test(host) || host.startsWith('[') || host === 'localhost' || host.endsWith('.local');
    s = `${plain ? 'http' : 'https'}://${s}`;
  }
  return s;
}

export const getBase = () => base;
export const getToken = () => token;
/** 网页版同源就算配置好了；原生 app 必须填过地址。 */
export const serverConfigured = () => Platform.OS === 'web' || !!base;
/** 网页版默认的服务器地址（同源）。 */
export const defaultBase = () => (Platform.OS === 'web' && typeof window !== 'undefined' ? window.location.origin : '');

/** 每个请求都带：令牌（有的话）+ 界面语言（服务器按它回中文或英文）。 */
export function authHeaders(): Record<string, string> {
  return { 'Accept-Language': acceptLanguage(), ...(token ? { Authorization: `Bearer ${token}` } : {}) };
}

/** 给 <Image> 这类带不了请求头的地方用：令牌放在 query 里。 */
export function fileUrl(path: string): string {
  const url = path.startsWith('http') ? path : `${base}${path}`;
  if (!token) return url;
  return `${url}${url.includes('?') ? '&' : '?'}token=${encodeURIComponent(token)}`;
}

export class AuthError extends Error {}
/** 服务器回了错误状态码。status 用来区分「这个接口老服务器上没有」（404 / 405）；data 是回来的 JSON（比如 ok=false 时的 missing_source）。 */
export class HttpError extends Error {
  status: number;
  data?: unknown;
  constructor(message: string, status: number, data?: unknown) { super(message); this.status = status; this.data = data; }
}
/** 错误里带的 HTTP 状态码（不靠 instanceof：跨打包边界时不一定认得出子类）。 */
export const httpStatus = (e: unknown): number | undefined => {
  const s = (e as { status?: unknown } | null)?.status;
  return typeof s === 'number' ? s : undefined;
};

/** 试连某个服务器（不改当前配置）。 */
export async function testServer(b: string, tok: string): Promise<{ ok: true; appName: string } | { ok: false; reason: 'auth' | 'down'; message: string }> {
  const url = `${normalizeBase(b)}/api/health`;
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), 12000);
  try {
    const r = await fetch(url, { signal: ctl.signal, headers: { Accept: 'application/json', 'Accept-Language': acceptLanguage(), ...(tok.trim() ? { Authorization: `Bearer ${tok.trim()}` } : {}) } });
    const j = await r.json().catch(() => ({}));
    if (r.status === 401) return { ok: false, reason: 'auth', message: tok.trim() ? L('令牌不对，服务器不认。', 'Wrong token. The server rejected it.') : L('这个服务器要令牌。', 'This server needs an access token.') };
    if (!r.ok || !j.ok) return { ok: false, reason: 'down', message: j.error || j.detail || `HTTP ${r.status}` };
    return { ok: true, appName: j.app_name || 'OpenMousse' };
  } catch (e) {
    return { ok: false, reason: 'down', message: e instanceof Error && e.name === 'AbortError' ? L('连接超时', 'Connection timed out.') : L('地址不通，连不上。', "Can't reach this address.") };
  } finally {
    clearTimeout(timer);
  }
}

/** 从贴进来的文字里找配对信息：openmousse://pair?s=<服务器>&c=<码> 的链接，或者只有 8 位配对码（服务器上 tokens.py pair 出的，见 server/pairing.py）。 */
export function parsePairing(text: string): { server?: string; code?: string } {
  const s = (text || '').trim();
  const m = /openmousse:\/\/pair\?([^\s]+)/.exec(s);
  if (m) {
    const out: { server?: string; code?: string } = {};
    for (const kv of m[1].split('&')) {
      const [k, v = ''] = kv.split('=');
      let val = v;
      try { val = decodeURIComponent(v.replace(/\+/g, ' ')); } catch { /* 原样 */ }
      if (k === 's') out.server = val;
      if (k === 'c') out.code = val;
    }
    return out;
  }
  const code = s.replace(/[\s-]/g, '').toUpperCase();
  return /^[23456789ABCDEFGHJKMNPQRSTUVWXYZ]{8}$/.test(code) ? { code } : {};
}

/** 配对码换一个自己的接入令牌，换到了就存好地址和令牌（长期令牌不用抄，也不经过聊天）。 */
export async function pairWithCode(b: string, code: string, device: string): Promise<{ ok: true; appName: string } | { ok: false; message: string }> {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), 12000);
  try {
    const r = await fetch(`${normalizeBase(b)}/api/pair`, {
      method: 'POST', signal: ctl.signal,
      headers: { 'Content-Type': 'application/json', Accept: 'application/json', 'Accept-Language': acceptLanguage() },
      body: JSON.stringify({ code, device }),
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok || !j.ok || !j.token) return { ok: false, message: j.error || j.detail || `HTTP ${r.status}` };
    await saveServerConfig(b, j.token);
    return { ok: true, appName: j.appName || 'OpenMousse' };
  } catch (e) {
    return {
      ok: false,
      message: e instanceof Error && e.name === 'AbortError'
        ? L('连接超时：手机连上 Tailscale 了吗？', 'Timed out. Is the phone on Tailscale?')
        : L('连不上服务器：手机要先连上 Tailscale（和服务器同一个账号）。', "Can't reach the server. The phone needs Tailscale first (the same account as the server)."),
    };
  } finally {
    clearTimeout(timer);
  }
}

/** JSON 请求：超时、错误信息统一处理。服务端的错误说明（detail / error）原样抛出来给界面显示。 */
export async function request<T>(path: string, init?: { method?: string; body?: unknown; timeoutMs?: number }): Promise<T> {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), init?.timeoutMs ?? 45000);
  try {
    const r = await fetch(base + path, {
      method: init?.method ?? 'GET',
      signal: ctl.signal,
      headers: { Accept: 'application/json', ...authHeaders(), ...(init?.body !== undefined ? { 'Content-Type': 'application/json' } : {}) },
      body: init?.body !== undefined ? JSON.stringify(init.body) : undefined,
    });
    const j = await r.json().catch(() => ({}));
    if (r.status === 401) throw new AuthError(j.error || L('接入令牌不对', 'Wrong access token'));
    if (!r.ok || j.ok === false) throw new HttpError((typeof j.detail === 'string' && j.detail) || j.error || `HTTP ${r.status}`, r.status, j);
    return j as T;
  } finally {
    clearTimeout(timer);
  }
}
