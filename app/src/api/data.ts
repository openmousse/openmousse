// app 其余页面的数据接口（server/data.py）。每一项的真源写在 data.py 顶部的表格里。
import type {
  ActivityEntry, DayInfo, SearchHit, Application, Approval, AvatarConfig, FeedItem, Goal, Group, GroupIcon, JournalEntry, MemoryItem, ModelsInfo, ProfileItem, SecurityInfo, SideChat, Task, UpcomingTask,
} from '../data/types';
import { request } from './base';

export const dataApi = {
  groups: () => request<{ groups: Group[] }>('/api/groups').then((j) => j.groups),
  createGroup: (g: { name: string; purpose: string; icon: GroupIcon; model: string }) => request<{ id: string }>('/api/groups', { method: 'POST', body: g }).then((j) => j.id),

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

  approvals: () => request<{ approvals: Approval[] }>('/api/approvals').then((j) => j.approvals),
  decide: (id: string, allow: boolean) => request(`/api/approvals/${encodeURIComponent(id)}`, { method: 'POST', body: { allow } }),

  tasks: () => request<{ tasks: Task[] }>('/api/tasks', { timeoutMs: 90000 }).then((j) => j.tasks),
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
