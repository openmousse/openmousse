// app 其余页面的数据接口（server/data.py）。每一项的真源写在 data.py 顶部的表格里。
import type {
  ActivityEntry, AgentColor, ChatCard, DayInfo, HandoffCard, SearchHit, Application, Approval, AvatarConfig, FeedItem, Goal, Group, GroupIcon, InboxAction, InboxItem, InboxStatus, JournalEntry, MemoryItem, ModelsInfo, ProfileItem, SecurityInfo, SideChat, Task, TaskQuota, ThreadCards, UnreadSummary, UpcomingTask,
} from '../data/types';
import { L } from '../lang';
import { httpStatus, request } from './base';

/**
 * 这个服务器有没有收件箱 / 未读 / 对话卡片接口。老服务器上它们 404（POST 是 405）：收件箱退回读 /api/approvals，未读当作没有、也不轮询，
 * 对话里不显示转交卡和任务卡。null = 还没试过。换服务器、重新连接时 resetServerSupport()。
 */
export const serverSupport: { inbox: boolean | null; unread: boolean | null; cards: boolean | null } = { inbox: null, unread: null, cards: null };
export const resetServerSupport = () => { serverSupport.inbox = null; serverSupport.unread = null; serverSupport.cards = null; };
const missing = (e: unknown) => { const s = httpStatus(e); return s === 404 || s === 405; };

/** 消息 id：数字，也认 "db123" 和 "123" 这种写法。 */
const msgNum = (v: unknown): number | null => {
  if (typeof v === 'number' && Number.isFinite(v)) return v;
  const m = typeof v === 'string' ? /^(?:db)?(\d+)$/.exec(v) : null;
  return m ? Number(m[1]) : null;
};
const STATUSES: InboxStatus[] = ['pending', 'approved', 'rejected', 'revising', 'done', 'failed', 'withdrawn', 'expired'];
/** 服务器在另一边同时开发：字段缺了也别让界面崩。 */
function normalizeInbox(raw: Partial<InboxItem> & { id: string }): InboxItem {
  const str = (v: unknown) => (typeof v === 'string' ? v : '');
  return {
    id: String(raw.id),
    kind: (raw.kind ?? 'other') as InboxItem['kind'],
    source: str(raw.source) || 'main',
    sourceName: str(raw.sourceName),
    thread: str(raw.thread) || str(raw.source) || 'main',
    messageId: msgNum(raw.messageId),
    title: str(raw.title),
    why: str(raw.why),
    changes: Array.isArray(raw.changes) ? raw.changes.map(String).filter(Boolean) : [],
    detail: str(raw.detail),
    approveLabel: str(raw.approveLabel),
    fields: Array.isArray(raw.fields) ? raw.fields.filter((f) => f && f.k != null).map((f) => ({ k: String(f.k), v: String(f.v ?? '') })) : undefined,
    status: STATUSES.includes(raw.status as InboxStatus) ? (raw.status as InboxStatus) : 'pending',
    note: str(raw.note),
    result: str(raw.result),
    level: raw.level === 'ring' || raw.level === 'quiet' || raw.level === 'none' ? raw.level : 'quiet',
    createdAt: str(raw.createdAt),
    decidedAt: typeof raw.decidedAt === 'string' ? raw.decidedAt : null,
    whenText: raw.whenText,
  };
}

/** 老服务器的一条审批，换成收件箱的样子（执行命令）。 */
const fromApproval = (a: Approval): InboxItem => normalizeInbox({
  id: `exec:${a.id}`, kind: 'exec', source: a.groupId ?? 'main', thread: a.groupId ?? 'main', title: a.action, why: a.detail,
  fields: a.fields, status: 'pending', level: 'ring', whenText: a.requestedAt,
});

const AFTER: Record<InboxAction, InboxStatus> = { approve: 'approved', reject: 'rejected', revise: 'revising' };

/** 对话里的卡片：服务器在另一边同时开发，认不出的种类丢掉，缺的字段补默认值。 */
function normalizeCard(raw: any): ChatCard | null {
  if (!raw || typeof raw.id !== 'string') return null;
  const num = (v: unknown) => (typeof v === 'number' && Number.isFinite(v) ? v : null);
  const str = (v: unknown) => (typeof v === 'string' ? v : '');
  if (raw.kind === 'handoff') {
    return { ...raw, messageId: msgNum(raw.messageId), seconds: num(raw.seconds), question: str(raw.question), toName: str(raw.toName) || str(raw.to),
      from: str(raw.from) || 'main', fromName: str(raw.fromName), status: ['running', 'done', 'error', 'busy', 'lost'].includes(raw.status) ? raw.status : 'done' } as HandoffCard;
  }
  if (raw.kind === 'task') {
    return { ...raw, messageId: msgNum(raw.messageId), title: str(raw.title), step: str(raw.step), result: str(raw.result), minutes: num(raw.minutes) ?? 0,
      tools: num(raw.tools) ?? 0, round: num(raw.round) ?? 1, deliverable: Array.isArray(raw.deliverable) ? raw.deliverable.map(String) : [],
      status: ['进行中', '完成', '失败', '已取消'].includes(raw.status) ? raw.status : '完成', timedOut: !!raw.timedOut } as ChatCard;
  }
  return null;
}
export const cardOf = normalizeCard;

/** 未读摘要：只留 n > 0 的线程。回来的不像摘要（比如只有 {ok}）就返回 null，别拿它把本地的清空。 */
function normalizeUnread(j: Partial<UnreadSummary> | null | undefined): UnreadSummary | null {
  if (!j || typeof j.threads !== 'object' || j.threads === null) return null;
  const threads: UnreadSummary['threads'] = {};
  for (const [tid, u] of Object.entries(j.threads)) {
    const n = Number(u?.n) || 0;
    if (n > 0) threads[tid] = { n, mine: Number(u?.mine) || 0, last: u?.last && typeof u.last.id === 'number' ? u.last : undefined };
  }
  return {
    threads,
    feedNew: Array.isArray(j.feedNew) ? j.feedNew.map(String) : [],
    inbox: Number(j.inbox) || 0,
    badge: Number(j.badge) || 0,
  };
}

export const dataApi = {
  groups: () => request<{ groups: Group[] }>('/api/groups').then((j) => j.groups),
  createGroup: (g: { name: string; purpose: string; icon: GroupIcon; color: AgentColor; model: string }) => request<{ id: string }>('/api/groups', { method: 'POST', body: g }).then((j) => j.id),
  /** 改 Agent：只发改了的字段（model 是模型 id）。回来的是改完的那一条；老服务器没回就返回 null。 */
  patchGroup: (id: string, p: { name?: string; icon?: GroupIcon; color?: AgentColor; purpose?: string; model?: string }) =>
    request<{ ok: boolean; group?: Group }>(`/api/groups/${encodeURIComponent(id)}`, { method: 'PATCH', body: p }).then((j) => (j.group && j.group.id ? j.group : null)),

  sideChats: () => request<{ sideChats: SideChat[] }>('/api/sidechats').then((j) => j.sideChats),
  createSideChat: (c: { title: string; purpose: string; model: string }) => request<{ id: string }>('/api/sidechats', { method: 'POST', body: c }).then((j) => j.id),
  patchSideChat: (id: string, patch: { title?: string; archived?: boolean }) => request(`/api/sidechats/${id}`, { method: 'PATCH', body: patch }),
  deleteSideChat: (id: string) => request(`/api/sidechats/${id}`, { method: 'DELETE' }),
  deleteGroup: (id: string) => request(`/api/groups/${id}`, { method: 'DELETE' }),

  goals: () => request<{ goals: Goal[] }>('/api/goals').then((j) => j.goals),
  journal: () => request<{ entries: JournalEntry[] }>('/api/journal?days=365&limit=300').then((j) => j.entries),
  deleteJournal: (id: string) => request(`/api/journal/${id}`, { method: 'DELETE' }),
  applications: () => request<{ applications: Application[] }>('/api/applications').then((j) => j.applications),
  feed: () => request<{ feed: FeedItem[] }>('/api/feed').then((j) => j.feed),
  days: (thread: string) => request<{ days: DayInfo[] }>(`/api/chat/days?thread=${encodeURIComponent(thread)}`).then((j) => j.days),
  search: (q: string, thread?: string) => request<{ hits: SearchHit[] }>(`/api/search?q=${encodeURIComponent(q)}${thread ? `&thread=${encodeURIComponent(thread)}` : ''}`).then((j) => j.hits),
  feedOn: (date: string) => request<{ feed: FeedItem[] }>(`/api/feed?date=${date}`).then((j) => j.feed),
  dismissFeed: (id: string) => request(`/api/feed/${id}/dismiss`, { method: 'POST' }),

  upcoming: () => request<{ upcoming: UpcomingTask[] }>('/api/upcoming', { timeoutMs: 60000 }).then((j) => j.upcoming),
  toggleUpcoming: (id: string, enabled: boolean) => request(`/api/upcoming/${encodeURIComponent(id)}`, { method: 'POST', body: { enabled }, timeoutMs: 60000 }),

  // 老服务器的审批队列：只在 /api/inbox 不存在时用
  approvals: () => request<{ approvals: Approval[] }>('/api/approvals').then((j) => j.approvals),
  decide: (id: string, allow: boolean) => request(`/api/approvals/${encodeURIComponent(id)}`, { method: 'POST', body: { allow } }),

  /** 收件箱。pending = 等你点头的；recent = 最近 7 天点过头的。老服务器：pending 读审批队列，recent 为空。 */
  inbox: async (status: 'pending' | 'recent'): Promise<InboxItem[]> => {
    if (serverSupport.inbox !== false) {
      try {
        const j = await request<{ items?: (Partial<InboxItem> & { id: string })[] }>(`/api/inbox?status=${status}`);
        serverSupport.inbox = true;
        return (j.items ?? []).filter((i) => i && i.id != null).map(normalizeInbox);
      } catch (e) {
        if (!missing(e)) throw e;
        serverSupport.inbox = false;
      }
    }
    if (status === 'recent') return [];
    const old = await request<{ approvals: Approval[] }>('/api/approvals').then((j) => j.approvals);
    return old.map(fromApproval);
  },
  /** 一个对话里的收件箱（等你点头的 + 最近 7 天处理过的，早的在前）：对话里显示在提它的那条消息下面。老服务器没有：空。 */
  inboxForThread: async (thread: string): Promise<InboxItem[]> => {
    if (serverSupport.inbox === false) return [];
    try {
      const j = await request<{ items?: (Partial<InboxItem> & { id: string })[] }>(`/api/inbox?thread=${encodeURIComponent(thread)}`);
      serverSupport.inbox = true;
      // 服务器要是不认 thread 参数、把别的对话的也给了：只留这个对话的
      return (j.items ?? []).filter((i) => i && i.id != null).map(normalizeInbox).filter((i) => i.thread === thread);
    } catch (e) {
      if (!missing(e)) throw e;
      serverSupport.inbox = false;
      return [];
    }
  },
  /** 同意 / 不要 / 改一下。返回处理后的那一条（服务器没回就按动作推出状态）。exec 只能同意或拒绝。 */
  decideInbox: async (id: string, action: InboxAction, note?: string, current?: InboxItem): Promise<InboxItem> => {
    const now = new Date().toISOString();
    const base = current ?? normalizeInbox({ id, kind: id.startsWith('exec:') ? 'exec' : 'other' });
    if (serverSupport.inbox === false && id.startsWith('exec:')) {
      if (action === 'revise') throw new Error(L('执行命令只能同意或拒绝。', 'A command can only be allowed or denied.'));
      await request(`/api/approvals/${encodeURIComponent(id.slice(5))}`, { method: 'POST', body: { allow: action === 'approve' } });
      return { ...base, status: AFTER[action], decidedAt: now };
    }
    const j = await request<{ item?: Partial<InboxItem> & { id: string } }>(`/api/inbox/${encodeURIComponent(id)}`, { method: 'POST', body: note ? { action, note } : { action } });
    if (j.item && j.item.id != null) {
      const item = normalizeInbox({ ...base, ...j.item });
      // 服务器回的还是 pending（比如还没来得及改状态）：按动作显示回执
      return item.status === 'pending' ? { ...item, status: AFTER[action], decidedAt: item.decidedAt ?? now, note: note ?? item.note } : item;
    }
    return { ...base, status: AFTER[action], decidedAt: now, note: note ?? base.note };
  },

  /** 未读摘要。老服务器上没有这个接口：返回 null（serverSupport.unread 变成 false，之后不再请求）。 */
  unread: async (): Promise<UnreadSummary | null> => {
    if (serverSupport.unread === false) return null;
    try {
      const j = await request<Partial<UnreadSummary>>('/api/unread');
      serverSupport.unread = true;
      return normalizeUnread(j);
    } catch (e) {
      if (missing(e)) { serverSupport.unread = false; return null; }
      throw e;
    }
  },
  /** 这个线程看到这里了。upto = 屏幕上最后一条消息的数字 id（消息 id 是 "db<id>"）。 */
  markRead: async (thread: string, upto?: number): Promise<UnreadSummary | null> => {
    if (serverSupport.unread === false) return null;
    const j = await request<Partial<UnreadSummary>>('/api/unread/read', { method: 'POST', body: upto != null ? { thread, upto } : { thread } });
    return normalizeUnread(j);
  },
  /** 这几张建议卡看过了（「今天」页上出现在屏幕里 1.5 秒）。 */
  feedSeen: (ids: string[]) => request('/api/feed/seen', { method: 'POST', body: { ids } }),

  tasks: () => request<{ tasks: Task[]; quota?: TaskQuota }>('/api/tasks', { timeoutMs: 90000 }).then((j) => ({ tasks: j.tasks, quota: j.quota ?? null })),
  /** 一个对话里的转交卡和任务卡。老服务器没有这个接口：返回 null（serverSupport.cards 变成 false，之后不再请求）。 */
  cards: async (thread: string): Promise<ThreadCards | null> => {
    if (serverSupport.cards === false) return null;
    try {
      const j = await request<{ cards: unknown[]; incoming?: unknown[] }>(`/api/chat/cards?thread=${encodeURIComponent(thread)}`, { timeoutMs: 20000 });
      serverSupport.cards = true;
      return {
        cards: (j.cards ?? []).map(normalizeCard).filter((c): c is ChatCard => !!c),
        incoming: (j.incoming ?? []).map(normalizeCard).filter((c): c is HandoffCard => !!c && c.kind === 'handoff'),
      };
    } catch (e) {
      if (missing(e)) { serverSupport.cards = false; return null; }
      throw e;
    }
  },
  task: (id: string) => request<{ task: Task }>(`/api/tasks/${id}`, { timeoutMs: 60000 }).then((j) => j.task),
  cancelTask: (id: string) => request(`/api/tasks/${id}/cancel`, { method: 'POST', timeoutMs: 60000 }),
  reviseTask: (id: string, note: string) => request(`/api/tasks/${id}/revise`, { method: 'POST', body: { note }, timeoutMs: 60000 }),

  activity: () => request<{ activity: ActivityEntry[] }>('/api/activity', { timeoutMs: 60000 }).then((j) => j.activity),

  profile: () => request<{ items: ProfileItem[]; file: string }>('/api/profile'),
  editProfile: (id: string, text: string | null) => request(`/api/profile/${id}`, { method: 'PUT', body: { text } }),

  memories: () => request<{ items: MemoryItem[]; file: string; updated: string }>('/api/memories'),
  forget: (id: string) => request(`/api/memories/${id}`, { method: 'DELETE' }),

  models: () => request<ModelsInfo>('/api/models', { timeoutMs: 60000 }),
  security: () => request<SecurityInfo>('/api/security', { timeoutMs: 60000 }),

  avatar: () => request<{ avatar: AvatarConfig | null }>('/api/settings').then((j) => j.avatar),
  setAvatar: (a: AvatarConfig) => request('/api/settings/avatar', { method: 'PUT', body: a }),
};
