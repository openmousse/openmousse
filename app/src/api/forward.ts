// 合并转发（server/forward.py）：几条消息合成一份，发给朋友（变成一份「聊天记录」分享：挡私事、能追问、能收回），
// 或者转给自己的 Agent（带一个意图：记下来 / 去处理 / 先看看）。转发篮：在几个对话里各挑几条攒到一起再发。
// ref：chat:<线程>:<消息 id>（和 Agent 的对话）/ friend:<好友 id>:<行 id>（和朋友的聊天）。
import { request } from './base';
import type { Share } from './share';

export type ForwardWho = 'me' | 'agent' | 'friend' | 'friend_agent' | 'my_card' | 'other';
export type ForwardIntent = 'remember' | 'handle' | 'look';
export interface ForwardItem {
  ref: string; src: 'chat' | 'friend'; convName: string; who: ForwardWho; name: string; text: string; at: string; when: string;
  /** 不是你、也不是你的 Agent 说的（朋友本人、朋友的名片 Agent、你的名片 Agent 替你答朋友的） */
  external: boolean;
  back: { kind: 'chat'; thread: string; id: string } | { kind: 'friend'; friend: string; id: number };
}
export interface ForwardPreview { items: ForwardItem[]; gist: string; convs: string[]; /** 里面有哪些朋友说的话 */ external: string[]; hasExternal: boolean }
/** 转给 Agent 的那条消息上的转发卡（服务器存在消息上，只有前三条的开头） */
export interface ForwardCard {
  id: string; n: number; gist: string; intent: ForwardIntent; note: string; convs: string[];
  lines: { name: string; text: string }[]; external: boolean;
}

/** 和服务器回来的一样写法（消息 id 不带 db 前缀）：多选时拿它比对哪些勾上了 */
export const chatRef = (thread: string, id: string) => `chat:${thread}:${id.replace(/^db/, '')}`;
export const friendRef = (friend: string, id: number) => `friend:${friend}:${id}`;

export const preview = (refs: string[], gist?: string) =>
  request<ForwardPreview>('/api/forward/preview', { method: 'POST', body: { refs, ...(gist ? { gist } : {}) }, timeoutMs: 60000 });
/** 长按「选这一段」：Agent 圈出说同一件事的那几条，顺带写一句要点 */
export const segment = (ref: string) => request<{ refs: string[]; gist: string }>('/api/forward/segment', { method: 'POST', body: { ref }, timeoutMs: 60000 });
/** 发给朋友：先变成一份草稿分享（挡私事、发给谁用分享的接口） */
export const toShare = (refs: string[], gist: string) =>
  request<{ share: Share }>('/api/forward/share', { method: 'POST', body: { refs, gist } }).then((j) => j.share);
export const toAgent = (b: { refs: string[]; gist: string; agent: string; intent: ForwardIntent; note?: string }) =>
  request<{ thread: string; messageId: string; queued: boolean; forward: ForwardCard }>('/api/forward/agent', { method: 'POST', body: b, timeoutMs: 90000 });
export const toSave = (refs: string[], gist: string) => request<{ save: { id: string; title: string } }>('/api/forward/save', { method: 'POST', body: { refs, gist } });
export const getForward = (id: string) =>
  request<{ forward: { id: string; to: string; intent: ForwardIntent; note: string; gist: string; items: ForwardItem[]; createdAt: string } }>(`/api/forward/${id}`)
    .then((j) => j.forward);

export const basket = () => request<{ items: ForwardItem[]; count: number }>('/api/forward/basket');
export const addToBasket = (refs: string[]) => request<{ count: number }>('/api/forward/basket', { method: 'POST', body: { refs } }).then((j) => j.count);
/** 不给 refs = 清空 */
export const dropFromBasket = (refs?: string[]) =>
  request<{ count: number }>('/api/forward/basket', { method: 'DELETE', body: refs ? { refs } : {} }).then((j) => j.count);
