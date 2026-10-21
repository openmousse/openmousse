// 连接器授权的两个网址（2026-10-21，从 apps.ts 挪出来，不碰 React Native，node scripts/test-oauth-url.mjs 能直接测）：
// 授权网址里这次登录的 state；授权页跳回来的 <scheme>://oauth/callback?code=…&state=…（或 error=…）。

export type OAuthCallback = { state: string; code?: string; error?: string; error_description?: string; iss?: string };

/** 网址的查询参数（RN 的 URL 没有 searchParams）。 */
function query(qs: string): Record<string, string> {
  const q: Record<string, string> = {};
  for (const kv of qs.split('&')) {
    const [k, v = ''] = kv.split('=');
    if (!k) continue;
    try { q[decodeURIComponent(k)] = decodeURIComponent(v.replace(/\+/g, ' ')); } catch { q[k] = v; }
  }
  return q;
}

/** 从 <scheme>://oauth/callback?… 里取出 state、code 或 error（带了 iss 一起交，服务器核对）。不是这种地址就 null。
 *  state：应用内登录交回来的地址没带 state 时用这个（有的服务报错时不带；那个地址只会交给发起的那次登录）。 */
export function parseOAuthCallback(url: string, state?: string): OAuthCallback | null {
  const m = /^[a-z][a-z0-9+.-]*:\/\/oauth\/callback\/?\?([^#]*)/i.exec(url || '');
  if (!m) return null;
  const q = query(m[1]);
  const st = q.state || state;
  if (!st) return null;
  return { state: st, code: q.code || undefined, error: q.error || undefined, error_description: q.error_description || undefined, iss: q.iss || undefined };
}

/** 授权网址里的 state（这次登录是哪一次）。 */
export function stateOf(authorizeUrl: string): string | null {
  const m = /\?([^#]*)/.exec(authorizeUrl || '');
  return (m && query(m[1]).state) || null;
}
