// OpenMousse 账号（2026-09-29；Leo 定：只用邮箱验证码登录，不接 Apple / Google）。
// 服务在 Supabase：app.local.*.json 写 "account": {"url": "https://<项目>.supabase.co", "anonKey": "…"}（app.config.js 放进 extra.account）。
// 没写 = 没有账号这回事（自己搭的、Grava 这种自用的壳），打开就用。
// 账号里只放：邮箱、名字、你连过的 claw（名字、地址、种类、最近一次连上的时间；**不放令牌**）。对话、记忆、连接器的令牌都不上账号。
// 登录：POST /auth/v1/otp 往邮箱发 6 位验证码 → POST /auth/v1/verify 换会话。refresh token 和用户信息存本机（钥匙串），
// access token 只在内存里（一小时过期，快到了拿 refresh token 换）。claw 列表是 public.claws 表（行级权限：只能看写自己的），
// 删账号走 rpc delete_user（见 docs 里的 supabase.sql）。
import Constants from 'expo-constants';
import { L } from '../lang';
import { readItem, writeItem } from './base';

type Cfg = { url: string; anonKey: string };
const KEY_REFRESH = 'mousse.account.refresh';
const KEY_USER = 'mousse.account.user';

export function accountConfig(): Cfg | null {
  const a = (Constants.expoConfig?.extra as { account?: Partial<Cfg> } | undefined)?.account;
  const url = (a?.url || '').trim().replace(/\/+$/, '');
  const anonKey = (a?.anonKey || '').trim();
  return url && anonKey ? { url, anonKey } : null;
}

/** 这个壳有没有账号（OpenMousse 有；自己搭的、Grava 没有）。 */
export const accountsEnabled = () => accountConfig() !== null;

export interface AccountUser { id: string; email: string; name?: string }
interface Session { access: string; refresh: string; expiresAt: number; user: AccountUser }

let session: Session | null = null;
let loaded = false;
const listeners = new Set<(u: AccountUser | null) => void>();

/** 登录、退出、改名字时通知（设置页、账号页跟着变）。→ 取消订阅的函数 */
export function onAccountChange(fn: (u: AccountUser | null) => void): () => void {
  listeners.add(fn);
  return () => { listeners.delete(fn); };
}
const emit = () => { const u = session?.user ?? null; listeners.forEach((f) => f(u)); };
export const currentAccount = (): AccountUser | null => session?.user ?? null;

/** 头像上的字：名字有两个词取两个词的头一个字母（Leo Zhou → LZ），否则取名字或邮箱的第一个字（leo@… → L，周炫宇 → 周）。 */
export function initialsOf(u: AccountUser): string {
  const words = (u.name || '').trim().split(/\s+/).filter(Boolean);
  if (words.length > 1) return (words[0][0] + words[1][0]).toUpperCase();
  return ((u.name || u.email || '?').trim()[0] || '?').toUpperCase();
}

export class AccountError extends Error {
  status?: number;
  /** 连不上账号服务（没网、服务挂了），不是账号本身的问题 */
  network?: boolean;
}

/** Supabase 回的错误换成人话。 */
function explain(status: number, j: Record<string, unknown>): string {
  const code = String(j.error_code || j.error || j.code || '');
  const msg = String(j.msg || j.message || j.error_description || '');
  if (code === 'otp_expired' || /expired or is invalid/i.test(msg)) return L('验证码不对，或者已经过期了', 'That code is wrong or has expired');
  if (status === 429 || /rate limit/i.test(code + msg)) return L('发得太频繁了，过一会儿再试', 'Too many tries. Wait a bit and try again');
  if (code === 'email_address_invalid' || code === 'validation_failed' || /invalid.*email|email.*invalid/i.test(msg)) return L('邮箱格式不对', "That email address doesn't look right");
  if (code === 'user_banned') return L('这个账号被停用了', 'This account has been disabled');
  return msg || L(`账号服务出错（${status}）`, `The account service returned an error (${status})`);
}

async function call<T>(path: string, init: { method?: string; body?: unknown; user?: boolean; timeoutMs?: number; headers?: Record<string, string> } = {}): Promise<T> {
  const cfg = accountConfig();
  if (!cfg) throw new AccountError(L('这个 app 没有账号服务', "This app doesn't have an account service"));
  // 用户的请求带他的 access token；登录前的（发码、验码、换 token）只带 apikey：新式的 publishable key 不是 JWT，放进 Authorization 会被拒
  let bearer: string | null = null;
  if (init.user) {
    const s = await fresh();
    if (!s?.access) throw new AccountError(L('先登录', 'Sign in first'));
    bearer = s.access;
  }
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), init.timeoutMs ?? 15000);
  try {
    const r = await fetch(cfg.url + path, {
      method: init.method ?? 'GET',
      signal: ctl.signal,
      headers: {
        apikey: cfg.anonKey, Accept: 'application/json', ...(bearer ? { Authorization: `Bearer ${bearer}` } : {}),
        ...(init.body !== undefined ? { 'Content-Type': 'application/json' } : {}), ...(init.headers ?? {}),
      },
      body: init.body !== undefined ? JSON.stringify(init.body) : undefined,
    });
    const text = await r.text();
    let j: unknown = {};
    try { j = text ? JSON.parse(text) : {}; } catch { j = {}; }
    if (!r.ok) {
      const e = new AccountError(explain(r.status, (j && typeof j === 'object' ? j : {}) as Record<string, unknown>));
      e.status = r.status;
      throw e;
    }
    return j as T;
  } catch (e) {
    if (e instanceof AccountError) throw e;
    const err = new AccountError(e instanceof Error && e.name === 'AbortError'
      ? L('账号服务没有回应，等一下再试', "The account service didn't answer. Try again in a moment")
      : L('连不上账号服务，看看网络', "Can't reach the account service. Check your connection"));
    err.network = true;
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

type TokenResp = { access_token: string; refresh_token: string; expires_in?: number; expires_at?: number; user?: { id?: string; email?: string; user_metadata?: { name?: string } } };

function take(j: TokenResp): Session {
  const u = j.user ?? {};
  return {
    access: j.access_token,
    refresh: j.refresh_token,
    expiresAt: j.expires_at ? j.expires_at * 1000 : Date.now() + (j.expires_in ?? 3600) * 1000,
    user: { id: String(u.id || ''), email: String(u.email || ''), name: u.user_metadata?.name || undefined },
  };
}

async function persist(s: Session | null): Promise<void> {
  await writeItem(KEY_REFRESH, s?.refresh || '');
  await writeItem(KEY_USER, s ? JSON.stringify(s.user) : '');
}

/** 启动时读一次本机存的会话（不联网）。→ 登录着的用户或 null */
export async function loadAccount(): Promise<AccountUser | null> {
  if (!accountsEnabled()) return null;
  if (!loaded) {
    loaded = true;
    const refresh = await readItem(KEY_REFRESH);
    let user: AccountUser | null = null;
    try {
      const raw = await readItem(KEY_USER);
      user = raw ? (JSON.parse(raw) as AccountUser) : null;
    } catch {
      user = null;
    }
    if (refresh && user?.id) session = { access: '', refresh, expiresAt: 0, user };
  }
  return session?.user ?? null;
}

let refreshing: Promise<Session | null> | null = null;

/** 拿一个还有效的 access token：快过期了就用 refresh token 换。refresh token 作废（在别处退出、账号删了）= 算退出。 */
async function fresh(): Promise<Session | null> {
  if (!session) return null;
  if (session.access && session.expiresAt - Date.now() > 60_000) return session;
  if (!refreshing) {
    refreshing = (async () => {
      const s = session;
      if (!s) return null;
      try {
        session = take(await call<TokenResp>('/auth/v1/token?grant_type=refresh_token', { method: 'POST', body: { refresh_token: s.refresh } }));
        await persist(session);
        emit();
        return session;
      } catch (e) {
        if (e instanceof AccountError && !e.network && e.status && e.status < 500) {
          session = null;
          await persist(null);
          emit();
        }
        return null;
      } finally {
        refreshing = null;
      }
    })();
  }
  return refreshing;
}

const clean = (email: string) => email.trim().toLowerCase();

/** 往邮箱发 6 位验证码（没有这个账号就顺便建一个）。 */
export async function sendCode(email: string): Promise<void> {
  await call('/auth/v1/otp', { method: 'POST', body: { email: clean(email), create_user: true } });
}

/** 验证码换会话：登录成功。 */
export async function verifyCode(email: string, code: string): Promise<AccountUser> {
  session = take(await call<TokenResp>('/auth/v1/verify', { method: 'POST', body: { type: 'email', email: clean(email), token: code.replace(/\s/g, '') } }));
  await persist(session);
  emit();
  return session.user;
}

/** 退出这台设备上的账号（claw 照常连着）。 */
export async function signOut(): Promise<void> {
  try {
    if (session) await call('/auth/v1/logout?scope=local', { method: 'POST', user: true, timeoutMs: 6000 });
  } catch { /* 连不上也照样在本机退出 */ }
  session = null;
  await persist(null);
  emit();
}

/** 改名字（存在账号的 user_metadata.name）。 */
export async function setAccountName(name: string): Promise<AccountUser | null> {
  const j = await call<{ user_metadata?: { name?: string } }>('/auth/v1/user', { method: 'PUT', user: true, body: { data: { name: name.trim() } } });
  if (session) {
    session = { ...session, user: { ...session.user, name: j.user_metadata?.name ?? name.trim() } };
    await persist(session);
    emit();
  }
  return session?.user ?? null;
}

/** 删账号（App Store 要求 app 里能删）：账号和它记的 claw 列表都删掉；claw 上的东西一样不动。 */
export async function deleteAccount(): Promise<void> {
  await call('/rest/v1/rpc/delete_user', { method: 'POST', user: true, body: {} });
  session = null;
  await persist(null);
  emit();
}

export interface AccountClaw { base: string; name: string; claw_kind?: string | null; claw_name?: string | null; last_seen?: string | null }

/** 账号里记着的 claw（在别的设备上连过的也在；这台没有令牌的要重新配对）。 */
export async function listAccountClaws(): Promise<AccountClaw[]> {
  if (!session) return [];
  return call<AccountClaw[]>('/rest/v1/claws?select=base,name,claw_kind,claw_name,last_seen&order=last_seen.desc.nullslast', { user: true });
}

/** 连上一台 claw 时记进账号（不记令牌）。记不上不影响用。 */
export async function syncClaw(c: { base: string; name: string; clawKind?: string; clawName?: string }): Promise<void> {
  if (!session || !c.base) return;
  try {
    await call('/rest/v1/claws?on_conflict=user_id,base', {
      method: 'POST', user: true, headers: { Prefer: 'resolution=merge-duplicates,return=minimal' },
      body: [{ user_id: session.user.id, base: c.base, name: c.name, claw_kind: c.clawKind ?? null, claw_name: c.clawName ?? null, last_seen: new Date().toISOString() }],
    });
  } catch { /* 下次连上再记 */ }
}

/** 从账号里去掉一台（这台设备上「断开」时）。 */
export async function forgetAccountClaw(base: string): Promise<void> {
  if (!session || !base) return;
  try {
    await call(`/rest/v1/claws?base=eq.${encodeURIComponent(base)}`, { method: 'DELETE', user: true });
  } catch { /* 忘不掉就留着 */ }
}
