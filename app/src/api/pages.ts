// 页面（2026-10-13，服务器 ../server/pages.py）：积木画不了的互动和可视化，Agent 写一份网页，app 放进盒子里显示。
// 盒子 = 网页版的 sandbox="allow-scripts" iframe（srcdoc，不透明源）/ 手机 app 的 WebView（about:blank、无痕）：页面读不到 app 的令牌和数据，也调不了 /api。
// 页面经桥（postMessage / window.ReactNativeWebView）要数据：app 用自己的令牌调 POST /api/pages/{id}/data，只给你点过「允许」的、当前这一版的。
// 手机上还要服务器的桥认 WebView（pages.native，2026-10-14）；更早的服务器页面只在网页版里显示（PageScreen 给说明）。
import { Platform } from 'react-native';
import { HttpError, request } from './base';
import { supports } from './version';

/** 页面要读的一份数据：哪张表（标题）、有哪些字段，只读。system = Apple 健康这类系统来源。 */
export interface PageNeed { name: string; from: string; title: string; fields: string[]; system: boolean; readonly: boolean; missing?: boolean }

export interface PageInfo {
  id: string; agent: string; name: string; title: string; summary: string;
  /** 这次给的是第几版；latest = 最新一版（对话卡片里的旧版本会小于它） */
  version: number; latest: number; digest: string; note: string; createdAt: string; updatedAt: string; deleted: boolean;
  needs: PageNeed[];
  /** 你对这一版的决定：allow / deny；none = 不读数据，不用问；null = 还没问过 */
  grant: 'allow' | 'deny' | 'none' | null;
  thread: string | null; messageId: number | null;
}

export interface PageField { key: string; label?: string; type?: string; options?: string[]; unit?: string; currency?: string }
export interface PageData { name: string; collection: string; title: string; fields: PageField[]; rows: Record<string, unknown>[]; total: number; truncated: boolean }

export const canPages = () => supports('pages');
/** 这里能不能显示页面本身：网页版一直能；手机 app 要服务器的桥认 WebView。 */
export const canShowPages = () => Platform.OS === 'web' || supports('pages.native');

const enc = encodeURIComponent;

export const pagesApi = {
  get: (id: string) => request<{ ok: true } & PageInfo>(`/api/pages/${enc(id)}`),
  doc: (id: string, theme: 'dark' | 'light') =>
    request<{ ok: true; id: string; version: number; digest: string; html: string }>(`/api/pages/${enc(id)}/doc?theme=${theme}`),
  grant: (id: string, digest: string, decision: 'allow' | 'deny') =>
    request<{ ok: true; grant: 'allow' | 'deny' }>(`/api/pages/${enc(id)}/grant`, { method: 'POST', body: { digest, decision } }),
  data: (id: string, digest: string, name: string) =>
    request<{ ok: true } & PageData>(`/api/pages/${enc(id)}/data`, { method: 'POST', body: { name, digest } }),
  pin: (id: string) =>
    request<{ ok: true; version?: number; block?: string; already?: boolean; undoTo?: number | null }>(`/api/pages/${enc(id)}/pin`, { method: 'POST', body: {} }),
  remove: (id: string) => request<{ ok: true }>(`/api/pages/${enc(id)}`, { method: 'DELETE' }),
};

/** 服务器回的错误码：not_granted（没允许）/ stale（页面刚更新过）/ unknown（没声明这份数据）；别的错误回 null。 */
export function pageErrorCode(e: unknown): string | null {
  const d = (e instanceof HttpError ? e.data : null) as { detail?: { error?: unknown } } | null;
  return typeof d?.detail?.error === 'string' ? d.detail.error : null;
}

export function pageErrorMessage(e: unknown): string {
  const d = (e instanceof HttpError ? e.data : null) as { detail?: { message?: unknown } } | null;
  if (typeof d?.detail?.message === 'string') return d.detail.message;
  return e instanceof Error ? e.message : String(e);
}
