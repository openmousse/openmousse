// 朋友（server/friends.py，社交第二层）：邀请码加朋友、朋友聊天、分享发给朋友、对着分享追问（对方的名片 agent 代答）、我的名片 agent 的档位。
// 协议见 ../../docs/social-protocol.zh-CN.md。服务器之间的事（签名、投递、重试）都在服务器上，app 只读写自己的服务器。
import { request } from './base';
import type { Share } from './share';

export type Tier = 'close' | 'friend' | 'mate';
export type AnyTier = Tier | 'stranger';
export type FriendStatus = 'active' | 'removed' | 'gone' | 'blocked';

export interface FriendLast { text: string; ts: string; kind: FriendMsgKind; dir: 'in' | 'out'; by: 'person' | 'agent' }
export interface Friend {
  id: string; name: string; cardName: string; alias: string | null; tier: Tier; tierName: string; status: FriendStatus;
  caps: string[]; /** 对方有名片 agent（能替他答追问） */ agent: boolean;
  fingerprint: string; url: string; note: string | null; createdAt: string; unread: number; last: FriendLast | null;
}
export interface Invite {
  id: string; note: string; tier: Tier; createdAt: string; expiresAt: string; status: 'open' | 'used' | 'expired' | 'revoked';
  usedAt: string | null; usedBy: { id: string; name: string } | null;
  /** 只在刚生成时有：邀请码（一个网址）和它的二维码（SVG path，边长 size 格，含白边） */
  code?: string; qr?: { size: number; path: string };
}
export interface FriendsHome {
  /** 能不能加朋友：没有外面打得进来的地址（no_url）或没设称呼（no_name）就不行 */
  ready: boolean; why: 'no_url' | 'no_name' | null;
  me: { name: string; fingerprint: string; url: string | null };
  /** 我的名片 agent 开着没有（开着才会替我答朋友的追问） */
  agent: boolean;
  friends: Friend[]; invites: Invite[];
}

export type FriendMsgKind = 'text' | 'share' | 'ask' | 'answer' | 'system';
export interface SharedSnapshot { sid: string; kind: string; title: string; text: string; quote: string; when: string; link: string | null; can_ask: boolean }
export interface FriendMsg {
  id: number; mid: string; dir: 'in' | 'out'; kind: FriendMsgKind; by: 'person' | 'agent'; text: string; replyTo: string | null;
  /** 发出去的：queued 发送中 / sent 送到了 / failed 没送到；收到的：new / read；都可能 revoked（收回了） */
  status: 'queued' | 'sent' | 'failed' | 'new' | 'read' | 'revoked' | 'local';
  /** 我的名片 agent 替我答的：pending 等我看 / ok 没问题 / edited 我改过 / revoked 我收回了 */
  review: 'pending' | 'ok' | 'edited' | 'revoked' | null;
  ts: string; edited: boolean;
  share?: SharedSnapshot; about?: string | null; used?: string[]; usedLabel?: string; defer?: boolean; outcome?: string; error?: string | null;
}
export interface FriendThread { friend: Friend; messages: FriendMsg[]; recent: FriendMsg[]; agent: boolean; canAsk: boolean }

export interface CardSettings {
  tiers: Record<AnyTier, Record<ScopeKey, string>>;
  scopes: Record<ScopeKey, string[]>;
  status: string;
  people: Record<Tier, { id: string; name: string }[]>;
  agent: boolean;
  tierNames: Record<AnyTier, string>;
}
export type ScopeKey = 'calendar' | 'status' | 'shares' | 'notes' | 'address';

export const home = () => request<FriendsHome>('/api/friends');
export const newInvite = (b: { note?: string; tier: Tier; days?: number }) =>
  request<{ invite: Invite }>('/api/friends/invites', { method: 'POST', body: b }).then((j) => j.invite);
export const withdrawInvite = (id: string) => request(`/api/friends/invites/${id}`, { method: 'DELETE' });
export const preview = (code: string) =>
  request<{ name: string; fingerprint: string; url: string; agent: boolean; already: string | null }>('/api/friends/preview', { method: 'POST', body: { code }, timeoutMs: 20000 });
export const accept = (b: { code: string; tier: Tier; alias?: string }) =>
  request<{ friend: Friend }>('/api/friends/accept', { method: 'POST', body: b, timeoutMs: 30000 }).then((j) => j.friend);
export const patchFriend = (id: string, b: { alias?: string; tier?: Tier }) =>
  request<{ friend: Friend }>(`/api/friends/${id}`, { method: 'PATCH', body: b }).then((j) => j.friend);
export const removeFriend = (id: string) => request(`/api/friends/${id}`, { method: 'DELETE' });
export const blockFriend = (id: string, blocked: boolean) =>
  request<{ friend: Friend }>(`/api/friends/${id}/block`, { method: 'POST', body: { blocked } }).then((j) => j.friend);

export const thread = (id: string, after?: number) =>
  request<FriendThread>(`/api/friends/${id}/messages${after ? `?after=${after}` : ''}`);
export const sendText = (id: string, text: string, replyTo?: string) =>
  request<{ message: FriendMsg }>(`/api/friends/${id}/messages`, { method: 'POST', body: { text, replyTo } }).then((j) => j.message);
export const ask = (id: string, about: string, text: string) =>
  request<{ message: FriendMsg }>(`/api/friends/${id}/ask`, { method: 'POST', body: { about, text } }).then((j) => j.message);
export const markRead = (id: string, upto?: number) => request(`/api/friends/${id}/read`, { method: 'POST', body: { upto } });
export const review = (msgId: number, action: 'ok' | 'edit' | 'revoke', text?: string) =>
  request<{ message: FriendMsg }>(`/api/friends/messages/${msgId}/review`, { method: 'POST', body: { action, text } }).then((j) => j.message);
export const revokeMsg = (msgId: number) =>
  request<{ message: FriendMsg }>(`/api/friends/messages/${msgId}/revoke`, { method: 'POST' }).then((j) => j.message);
export const retry = (msgId: number) =>
  request<{ message: FriendMsg }>(`/api/friends/messages/${msgId}/retry`, { method: 'POST' }).then((j) => j.message);

/** 分享发给朋友：ask = 朋友能追问（还看对方那一档），link = 有链接的人都能看（否则只发给这几个朋友，链接不开） */
export const sendShare = (sid: string, b: { friends: string[]; ask: boolean; link: boolean; text?: string }) =>
  request<{ share: Share; sent: number }>(`/api/shares/${sid}/send`, { method: 'POST', body: b });

export const card = () => request<CardSettings>('/api/card');
export const patchCard = (b: { tiers?: Partial<Record<AnyTier, Partial<Record<ScopeKey, string>>>>; status?: string }) =>
  request<CardSettings>('/api/card', { method: 'PATCH', body: b });

/** 贴进来的文字里有没有邀请码（…/f/i/<令牌>/<公钥>，或 openmousse://friends/add?code=…）。 */
export function findCode(text: string): string | null {
  let s = text || '';
  const q = /[?&]code=([^&\s]+)/.exec(s);
  if (q && !s.includes('/f/i/')) { try { s = decodeURIComponent(q[1]); } catch { /* 原样 */ } }
  const m = /(https?:\/\/[^\s/?#<>"']+)\/f\/i\/([A-Za-z0-9_-]{22})\/([A-Za-z0-9_-]{43})/.exec(s);
  return m ? m[0] : null;
}
