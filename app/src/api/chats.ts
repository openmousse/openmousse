// 对话列表（2026-10-13，../../server/chats.py）：「对话」tab 的 Grava 一边 = 顶上一排 Agent + 主对话、会议 / Agent 群、项目。
// 排序和未读服务器算好（只算你的话和给你的回复）；老服务器没有 /api/chats，就用 /api/groups、/api/sidechats、/api/unread 拼一份（不能置顶 / 免打扰）。
import type { Group, InboxItem, Message, SideChat, UnreadSummary } from '../data/types';
import { request } from './base';

/** 外圈状态：在做（正在回复、后台任务在跑）= 青色弧；等你点头（收件箱里有它的）= 琥珀点。 */
export type ChatState = 'working' | 'waiting' | null;

export interface ChatAgent {
  id: string;
  name: string;
  icon: string;
  color: string | null;
  purpose: string;
  /** 最后一句（你的话或给你的回复；一条都没有时是最新的一条） */
  line: string;
  lineRole: 'user' | 'grava' | 'auto' | null;
  /** 排序用的时间：最后一条你的话或给你的回复（ISO）；没有 = null */
  at: string | null;
  unread: number;
  pinned: boolean;
  muted: boolean;
  state: ChatState;
}

export type RowKind = 'main' | 'meeting' | 'room' | 'project';

export interface ChatRow {
  kind: RowKind;
  id: string;
  /** 主对话是空的（用 agentName()） */
  title: string;
  purpose?: string;
  /** 会议 / Agent 群的成员：Agent id 或 "main" */
  members?: string[];
  line: string;
  lineRole: 'user' | 'grava' | 'auto' | null;
  at: string | null;
  unread: number;
  pinned: boolean;
  muted: boolean;
  state: ChatState;
}

export interface ChatList {
  agents: ChatAgent[];
  rows: ChatRow[];
  /** 已结束的会议、已归档的项目各有几条（列表底下的入口） */
  ended: number;
  archived: number;
  /** 服务器给的（能置顶 / 免打扰）；false = 老服务器，app 自己拼的 */
  server: boolean;
}

export const chatsApi = {
  load: () => request<Omit<ChatList, 'server'>>('/api/chats').then((j): ChatList => ({ agents: j.agents, rows: j.rows, ended: j.ended, archived: j.archived, server: true })),
  archived: () => request<{ rows: ChatRow[] }>('/api/chats?archived=1').then((j) => j.rows),
  patch: (thread: string, body: { pinned?: boolean; muted?: boolean }) =>
    request<{ pinned: boolean; muted: boolean }>(`/api/chats/${encodeURIComponent(thread)}`, { method: 'PATCH', body }),
  remove: (thread: string) => request(`/api/chats/${encodeURIComponent(thread)}`, { method: 'DELETE' }),
};

const iso = (ms: number) => (ms ? new Date(ms).toISOString() : null);

/** 老服务器：用已有的几样拼出同样的形状（最后一句：未读里带的，或今天读回来的最后一条，再不行用列表给的）。Agent 按原来的顺序，项目按最近活动。 */
export function fallbackList(groups: Group[], sideChats: SideChat[], unread: UnreadSummary, threads: Record<string, Message[]>): ChatList {
  const n = (id: string) => unread.threads[id]?.n ?? 0;
  const today = (id: string) => { const m = threads[id]?.[threads[id].length - 1]; return m ? m.body.text.replace(/\s+/g, ' ').trim() : ''; };
  const last = (id: string, fb: string) => unread.threads[id]?.last?.text || today(id) || fb;
  const agents: ChatAgent[] = groups.map((g) => ({
    id: g.id, name: g.name, icon: g.icon, color: g.color ?? null, purpose: g.purpose, line: last(g.id, g.lastLine), lineRole: 'grava',
    at: unread.threads[g.id]?.last?.ts ?? null, unread: n(g.id), pinned: false, muted: false, state: null,
  }));
  const live = sideChats.filter((c) => !c.archived).sort((a, b) => b.updatedAt - a.updatedAt);
  const rows: ChatRow[] = [
    { kind: 'main', id: 'main', title: '', line: last('main', ''), lineRole: 'grava', at: unread.threads.main?.last?.ts ?? null, unread: n('main'), pinned: true, muted: false, state: null },
    ...live.map((c): ChatRow => ({ kind: 'project', id: c.id, title: c.title, purpose: c.purpose, line: last(c.id, c.lastLine), lineRole: 'grava', at: iso(c.updatedAt),
      unread: n(c.id), pinned: false, muted: false, state: null })),
  ];
  return { agents, rows, ended: 0, archived: sideChats.length - live.length, server: false };
}

/**
 * 叠上本机知道的、比服务器那份新的：
 * 正在回复（typing）= 在做；收件箱里有它的 = 等你点头（会议的「纪要待确认」由服务器给，照留）；
 * 未读摘要里已经没有这个线程 = 看过了（刚点开过，服务器那份还没重读）。
 */
export function overlay(list: ChatList, typing: Record<string, boolean>, inbox: InboxItem[], unread: UnreadSummary): ChatList {
  const asking = new Set<string>();
  for (const i of inbox) { asking.add(i.thread); asking.add(i.source); }
  const state = (id: string, server: ChatState, keepWaiting: boolean): ChatState =>
    asking.has(id) || (keepWaiting && server === 'waiting') ? 'waiting' : typing[id] || server === 'working' ? 'working' : null;
  const seen = (id: string, n: number) => (unread.threads[id] ? n : 0);
  return {
    ...list,
    agents: list.agents.map((a) => ({ ...a, unread: seen(a.id, a.unread), state: state(a.id, a.state, false) })),
    // 会议线程不在未读摘要里（unread.py 只算主对话、Agent、项目）：会议的未读照服务器的
    rows: list.rows.map((r) => {
      const room = r.kind === 'meeting' || r.kind === 'room';
      return { ...r, unread: room ? r.unread : seen(r.id, r.unread), state: state(r.id, r.state, room) };
    }),
  };
}
