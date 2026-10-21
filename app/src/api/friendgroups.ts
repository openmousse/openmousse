// 好友群（主线第三步第三段，2026-10-21；服务器 ../../server/friendgroup.py，协议 ../../docs/social-protocol.md 7.1）。
// 你和几位好友在一个群里：消息端到端加密，逐个投到每位成员的服务器；你这边只出一位管家（群主的管家主持）。
// 好友的话只给管家当资料，不驱动你的工具；管家出给你的卡（约时间、Doorman 扣下的一句）只在你这边。
// 跟着管家开（server.json 的 butler）：老服务器（不认 friendgroups）或没开（接口 404）就不显示入口。useFriendGroupsOn() 判断。
import { useEffect, useState } from 'react';
import type { InboxItem } from '../data/types';
import type { PrepEpisode, PrepOffer } from './groups';
import { httpStatus, request } from './base';
import { supports } from './version';

export interface FGMember { kid: string; name: string; me: boolean; owner: boolean; butler: boolean; butlerName: string | null }
export interface FriendGroup {
  id: string; kind: 'friendgroup'; title: string; owner: string; mine: boolean; version: number; status: string;
  members: FGMember[]; myButler: boolean; createdAt: string; updatedAt: string; note: string;
  last?: { name: string; text: string; at: string }; unread?: number;
  /** 一期播客的录前讨论（多人对谈拉了好友）：顶上钉着这一期 */
  source?: { kind: 'podcast'; id: string; title: string }; episode?: PrepEpisode | null;
}
export interface FGMessage {
  id: number; mid: string; sender: string; who: 'person' | 'butler' | 'system'; mine: boolean; name: string; text: string;
  replyTo: string | null; at: string; dir: 'in' | 'out' | 'local'; system: boolean;
  /** 管家答的时候请教了一位赛博（只在你这边：问了谁、带的要点在 consults 里） */
  view?: { cyber: string; name: string; consult: string };
  /** 你的管家这一句出了一张给你的卡（收件箱的 id） */
  card?: string;
  sentinel?: { verdict: string; reasons?: unknown[]; via?: string };
}
export interface FGConsult { id: string; cyber: string; name: string; gist: string; answer: string; status: string; ref: string; createdAt: string; line: string }
export type FGCard = InboxItem & { groupMessage: number };
export interface FriendGroupFull { group: FriendGroup; messages: FGMessage[]; cards: FGCard[]; consults: FGConsult[]; offers?: PrepOffer[] }
export interface FGCandidate { id: string; name: string; can: boolean; why: string | null }

const p = (gid: string) => `/api/friendgroups/${encodeURIComponent(gid)}`;

export const friendGroupsApi = {
  list: () => request<{ groups: FriendGroup[] }>('/api/friendgroups').then((j) => j.groups),
  candidates: () => request<{ friends: FGCandidate[]; note: string }>('/api/friendgroups/candidates'),
  create: (title: string, friends: string[]) =>
    request<{ group: FriendGroup }>('/api/friendgroups', { method: 'POST', body: { title, friends } }).then((j) => j.group),
  /** after = 只要这条之后的（轮询用） */
  get: (gid: string, after = 0) => request<FriendGroupFull>(`${p(gid)}${after ? `?after=${after}` : ''}`),
  send: (gid: string, text: string) => request<{ message: FGMessage }>(`${p(gid)}/send`, { method: 'POST', body: { text } }).then((j) => j.message),
  read: (gid: string, upto: number) => request(`${p(gid)}/read`, { method: 'POST', body: { upto } }),
  /** 你的管家进不进别人的群：发给群主，群主重签成员表之前 pending */
  setButler: (gid: string, butler: boolean) =>
    request<{ group: FriendGroup; pending: boolean }>(p(gid), { method: 'PATCH', body: { butler } }),
};

// —— 开着吗 ——————————————————————————————————————————————————————————————————————————————
const TTL = 60_000;
let known: { on: boolean; at: number } | null = null;
let asking: Promise<boolean> | null = null;

/** 好友群开着吗：老服务器（不认 friendgroups）= 关；接口 404 = 没开（跟着管家）。问一次记一分钟。 */
export function friendGroupsOn(): Promise<boolean> {
  if (!supports('friendgroups')) return Promise.resolve(false);
  if (known && Date.now() - known.at < TTL) return Promise.resolve(known.on);
  if (!asking) {
    asking = request('/api/friendgroups')
      .then(() => true)
      .catch((e) => (httpStatus(e) === 404 ? false : (known?.on ?? false)))
      .then((on) => { known = { on, at: Date.now() }; asking = null; return on; });
  }
  return asking;
}

export function useFriendGroupsOn(): boolean | null {
  const [on, setOn] = useState<boolean | null>(() => (!supports('friendgroups') ? false : known ? known.on : null));
  useEffect(() => {
    let live = true;
    friendGroupsOn().then((v) => { if (live) setOn(v); }).catch(() => {});
    return () => { live = false; };
  }, []);
  return on;
}
