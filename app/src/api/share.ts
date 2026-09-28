// 分享（server/share.py，社交第一层）：一条回复、一篇想完了的笔记 → 链接（发微信、WhatsApp）或干净版卡片（发小红书）。
// 发之前服务器先把私事挡住（住址、家人的名字、邮箱电话、身体数字），这里一处处「放出来」；原文不出服务器。
import { request } from './base';

export type ShareKind = 'message' | 'note' | 'text';
export type ShareStyle = 'link' | 'clean';
export interface ShareMask {
  id: string; kind: 'address' | 'name' | 'contact' | 'body' | 'custom'; label: string;
  /** 挡住的原文和前后几个字：只给你自己看 */ text: string; before: string; after: string; released: boolean;
}
/** 正文切成段：m 有值的是一处挡着（或放出来了）的地方 */
export interface ShareSegment { t: string; m?: string; label?: string; released?: boolean }
export interface Share {
  id: string; kind: ShareKind; status: 'draft' | 'live' | 'revoked'; title: string; titleCustom: boolean; quote: string; quoteCustom: boolean;
  views: number; createdAt: string; publishedAt: string | null; revokedAt: string | null; day: string; time: string;
  /** 还挡着几处 / 一共认出几处 */ blocked: number; maskCount: number;
  /** 别人打得开的链接（发出去了、服务器配了对外地址才有） */ url: string | null;
  /** 链接页在服务器上的路径（发出去了才有）：自己的设备上用服务器地址 + 它预览 */ path: string | null;
  /** 服务器配了对外地址没有：没配的话链接只有你自己的设备打得开 */ canLink: boolean;
  source: { thread?: string; message?: number; withQuestion?: boolean; /** 回复前面有你问的那句（能选带不带） */ hasQuestion?: boolean; path?: string; topic?: string };
  masks?: ShareMask[]; segments?: ShareSegment[];
}
export type ShareFrom = { kind: 'message'; thread: string; id: string } | { kind: 'note'; topic?: string; path?: string } | { kind: 'text'; title?: string; text: string };
export interface ShareCard { dataUri: string; width: number; height: number }

export const createShare = (from: ShareFrom) => request<{ share: Share }>('/api/shares', { method: 'POST', body: from }).then((j) => j.share);
export const getShare = (id: string) => request<{ share: Share }>(`/api/shares/${id}`).then((j) => j.share);
export const patchShare = (id: string, b: { release?: string[]; hide?: string[]; quote?: string; title?: string; withQuestion?: boolean }) =>
  request<{ share: Share }>(`/api/shares/${id}`, { method: 'PATCH', body: b }).then((j) => j.share);
export const publishShare = (id: string) => request<{ share: Share }>(`/api/shares/${id}/publish`, { method: 'POST' }).then((j) => j.share);
export const revokeShare = (id: string) => request(`/api/shares/${id}`, { method: 'DELETE' });
export const listShares = () => request<{ shares: Share[]; canLink: boolean }>('/api/shares');
/** 卡片图（服务器上画好，data URI）：直接显示，也直接交给系统分享面板 */
export const shareCard = (id: string, style: ShareStyle) =>
  request<ShareCard>(`/api/shares/${id}/card?style=${style}`, { timeoutMs: 60000 });
