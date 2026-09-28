// 思考空间、收藏、冥想时间（server/think.py、server/saves.py）。
// 想法是服务器上库里收件箱的笔记（Obsidian 里也能看、能改），AI 不碰；勾几条「聊聊」「想完了」才开一个主题叫它来。
// 收藏存原件和抽出来的正文，存的时候不调模型。
import type { PendingFile } from '../data/types';
import { fileUrl, getBase, request } from './base';
import { formFile, xhrUpload } from './client';

export type FragmentKind = 'text' | 'keywords' | 'voice' | 'photo' | 'file' | 'link' | 'long' | 'save';
export interface ThinkFile { name: string; kind: string; size: number; url: string }
export interface Fragment {
  id: string; kind: FragmentKind; text: string; title: string; keywords: string[]; createdAt: string; day: string; time: string;
  status: 'open' | 'done'; topics: string[]; note: boolean; files: ThinkFile[]; url: string; linkTitle: string; duration: number | null;
  source: string; save: string | null; chars: number; bad: boolean; path: string;
}
export interface TopicBrief {
  id: string; title: string; count: number; /** 聊的时候「只记下」的几句（不算在 count 里） */ notes: number; status: 'open' | 'done' | 'deleted'; createdAt: string; updatedAt: string; talked: number; lastLine: string;
  lastAt: string | null; draftStatus: string | null; notePath: string | null;
}
export interface DraftPoint { text: string; from: string[] }
export interface Draft {
  title: string; oneLine: string; points: DraftPoint[]; open: string[]; next: { text: string; date: string }[]; keywords: string[];
  suggest: string[]; tree: string; branch: string;
}
export interface Topic extends TopicBrief {
  fragments: Fragment[]; missing: number; draft: Draft | null; draftStatus: 'running' | 'ready' | 'error' | null; draftError: string | null;
}
/** obsidianVault：手机上 Obsidian 里这个库叫什么（server.json think.obsidian_vault），有就能「在 Obsidian 里打开」 */
export interface Stream { fragments: Fragment[]; more: boolean; topics: TopicBrief[]; savesNew: number; vault: boolean; folder: string; obsidianVault?: string | null }

export type SaveKind = 'link' | 'text' | 'file' | 'image' | 'chat';
export interface SaveItem {
  id: string; kind: SaveKind; title: string; url: string; source: string; note: string; name: string | null; mime: string | null; size: number | null;
  textStatus: 'none' | 'fetching' | 'ok' | 'empty' | 'blocked' | 'failed'; textLen: number; textNote: string | null; keywords: string[];
  createdAt: string; day: string; time: string; seen: boolean; givenTo: string | null; thread: string | null; fileUrl: string | null; thumbUrl: string | null;
}
/** 搜索结果里的一段：[文字, 是不是命中]，命中的高亮。 */
export type Parts = [string, boolean][];
export interface KeywordStat { k: string; n: number; ideas: number; saves: number; last: string }
export interface SearchResult {
  q: string; keywords: KeywordStat[]; ideas: (Fragment & { parts: Parts })[]; saves: (SaveItem & { parts: Parts; inBody: boolean })[];
  topics: (TopicBrief & { parts: Parts })[]; notes: { path: string; title: string; folder: string; createdAt: string | null; parts: Parts }[];
  counts: { ideas: number; saves: number; topics: number; notes: number }; total: number;
}
export type DayItem = { type: 'idea'; at: string; idea: Fragment } | { type: 'save'; at: string; save: SaveItem };
export interface KeywordPage { k: string; ideas: number; saves: number; since: string | null; items: DayItem[]; co: { k: string; n: number }[]; topics: TopicBrief[] }
export interface Focus { id: number; startedAt: string; endsAt: string; minutes: number; endedAt: string | null; held: number; until: string }
export interface HeadsUp { title: string; time: string; kind: string; location: string }
export interface HeldPush { title: string; body: string; subtitle: string; at: string; kind: string | null; level: string | null; target: any; thread: string | null }
export interface FocusSummary {
  id: number; from: string; to: string; minutes: number; planned: number; words: number | null; notes: number | null; held: HeldPush[]; inbox: number; next: HeadsUp[];
}

// 附件、缩略图的地址：<Image> 带不了请求头，令牌放进 query
const withUrls = (f: Fragment): Fragment => ({ ...f, files: f.files.map((x) => ({ ...x, url: fileUrl(x.url) })) });
const saveUrls = (s: SaveItem): SaveItem => ({ ...s, fileUrl: s.fileUrl ? fileUrl(s.fileUrl) : null, thumbUrl: s.thumbUrl ? fileUrl(s.thumbUrl) : null });

export const stream = (status: 'open' | 'all' = 'open') =>
  request<Stream>(`/api/think/stream?status=${status}`).then((s) => ({ ...s, fragments: s.fragments.map(withUrls) }));
export const getFragment = (id: string) => request<{ fragment: Fragment }>(`/api/think/fragments/${id}`).then((j) => withUrls(j.fragment));
export const addFragment = (b: { kind?: FragmentKind; text?: string; title?: string; keywords?: string[]; url?: string; topic?: string }) =>
  request<{ fragment: Fragment }>('/api/think/fragments', { method: 'POST', body: b }).then((j) => withUrls(j.fragment));
/** 带附件的一条：照片、文件；kind voice = 录音（服务器转成文字当正文，原声留着）。 */
export async function uploadFragment(files: PendingFile[], opts: { text?: string; kind?: FragmentKind; title?: string; keywords?: string[]; duration?: number }) {
  const fd = new FormData();
  for (const f of files) formFile(fd, 'files', f);
  if (opts.text) fd.append('text', opts.text);
  if (opts.kind) fd.append('kind', opts.kind);
  if (opts.title) fd.append('title', opts.title);
  if (opts.keywords?.length) fd.append('keywords', JSON.stringify(opts.keywords));
  if (opts.duration) fd.append('duration', String(opts.duration));
  const j = await xhrUpload(`${getBase()}/api/think/fragments/upload`, fd, 5 * 60 * 1000);
  return withUrls(j.fragment as Fragment);
}
export const patchFragment = (id: string, b: { text?: string; title?: string; keywords?: string[] }) =>
  request<{ fragment: Fragment }>(`/api/think/fragments/${id}`, { method: 'PATCH', body: b }).then((j) => withUrls(j.fragment));
export const deleteFragment = (id: string) => request(`/api/think/fragments/${id}`, { method: 'DELETE' });
/** 全屏写字板「存进写作」：直接写进库的 写作/。 */
export const writeNote = (b: { title: string; text: string; folder?: 'writing' | 'notes' }) =>
  request<{ path: string }>('/api/think/notes', { method: 'POST', body: b }).then((j) => j.path);

export const createTopic = (fragments: string[], title?: string) =>
  request<{ id: string; topic: Topic }>('/api/think/topics', { method: 'POST', body: { fragments, ...(title ? { title } : {}) } })
    .then((j) => ({ ...j.topic, fragments: j.topic.fragments.map(withUrls) }));
export const getTopic = (id: string) => request<{ topic: Topic }>(`/api/think/topics/${id}`).then((j) => ({ ...j.topic, fragments: j.topic.fragments.map(withUrls) }));
export const listTopics = (status: 'open' | 'done' | 'all' = 'open') => request<{ topics: TopicBrief[] }>(`/api/think/topics?status=${status}`).then((j) => j.topics);
export const patchTopic = (id: string, b: { title?: string; add?: string[]; remove?: string[]; status?: 'open' }) =>
  request<{ topic: Topic }>(`/api/think/topics/${id}`, { method: 'PATCH', body: b }).then((j) => ({ ...j.topic, fragments: j.topic.fragments.map(withUrls) }));
export const deleteTopic = (id: string) => request(`/api/think/topics/${id}`, { method: 'DELETE' });
export const talk = (id: string) => request(`/api/think/topics/${id}/talk`, { method: 'POST' });
export const done = (id: string, fresh = false) => request<{ status: string }>(`/api/think/topics/${id}/done${fresh ? '?fresh=1' : ''}`, { method: 'POST' });
export const saveTopic = (id: string, b: { title: string; oneLine: string; points: string[]; open: string[]; next: string[]; keywords: string[]; folder: 'notes' | 'writing'; tree?: string | null; branch?: string | null }) =>
  request<{ path: string; tree: { ok?: boolean; id?: string } | null; moved: number }>(`/api/think/topics/${id}/save`, { method: 'POST', body: b, timeoutMs: 90000 });

export const search = (q: string, scope = 'all') =>
  request<SearchResult>(`/api/think/search?q=${encodeURIComponent(q)}&scope=${scope}`)
    .then((r) => ({ ...r, ideas: r.ideas.map((x) => ({ ...withUrls(x), parts: x.parts })), saves: r.saves.map((x) => ({ ...saveUrls(x), parts: x.parts, inBody: x.inBody })) }));
export const keywords = () => request<{ keywords: KeywordStat[] }>('/api/think/keywords').then((j) => j.keywords);
export const keyword = (k: string) => request<KeywordPage>(`/api/think/keyword?k=${encodeURIComponent(k)}`).then((p) => ({ ...p, items: p.items.map(fixItem) }));
export const days = (month: string) => request<{ month: string; days: { day: string; ideas: number; saves: number }[]; total: number }>(`/api/think/days?month=${month}`);
export const day = (d: string) => request<{ items: DayItem[] }>(`/api/think/day?day=${d}`).then((j) => j.items.map(fixItem));
const fixItem = (x: DayItem): DayItem => (x.type === 'idea' ? { ...x, idea: withUrls(x.idea) } : { ...x, save: saveUrls(x.save) });

export const saves = (filter = 'all') => request<{ saves: SaveItem[]; new: number }>(`/api/think/saves?filter=${filter}`).then((j) => ({ ...j, saves: j.saves.map(saveUrls) }));
export const getSave = (id: string, full = false) =>
  request<{ save: SaveItem; text: string; more: boolean }>(`/api/think/saves/${id}${full ? '?full=1' : ''}`).then((j) => ({ ...j, save: saveUrls(j.save) }));
export const addSave = (b: { url?: string; text?: string; title?: string; note?: string; source?: string; keywords?: string[] }) =>
  request<{ save: SaveItem }>('/api/think/saves', { method: 'POST', body: b }).then((j) => saveUrls(j.save));
export async function uploadSaves(files: PendingFile[], opts: { note?: string; source?: string; keywords?: string[] }) {
  const fd = new FormData();
  for (const f of files) formFile(fd, 'files', f);
  if (opts.note) fd.append('note', opts.note);
  if (opts.source) fd.append('source', opts.source);
  if (opts.keywords?.length) fd.append('keywords', JSON.stringify(opts.keywords));
  const j = await xhrUpload(`${getBase()}/api/think/saves/upload`, fd, 5 * 60 * 1000);
  return (j.saves as SaveItem[]).map(saveUrls);
}
export const saveMessage = (thread: string, id: string) =>
  request<{ save: SaveItem }>('/api/think/saves/from-message', { method: 'POST', body: { thread, id } }).then((j) => saveUrls(j.save));
export const patchSave = (id: string, b: { title?: string; note?: string; keywords?: string[]; seen?: boolean }) =>
  request<{ save: SaveItem }>(`/api/think/saves/${id}`, { method: 'PATCH', body: b }).then((j) => saveUrls(j.save));
export const deleteSave = (id: string) => request(`/api/think/saves/${id}`, { method: 'DELETE' });
export const restoreSave = (id: string) => request<{ save: SaveItem }>(`/api/think/saves/${id}/restore`, { method: 'POST' }).then((j) => saveUrls(j.save));
export const giveSave = (id: string, agent: string) => request<{ save: SaveItem }>(`/api/think/saves/${id}/give`, { method: 'POST', body: { agent } }).then((j) => saveUrls(j.save));
export const saveToIdea = (id: string) => request<{ fragment: Fragment }>(`/api/think/saves/${id}/to-idea`, { method: 'POST' }).then((j) => withUrls(j.fragment));

export const focus = () => request<{ active: Focus | null; unseen: Focus | null }>('/api/think/focus');
export const focusPreview = (minutes: number) => request<{ now: string; until: string; minutes: number; items: HeadsUp[] }>(`/api/think/focus/preview?minutes=${minutes}`);
export const focusStart = (minutes: number) => request<{ active: Focus }>('/api/think/focus/start', { method: 'POST', body: { minutes } }).then((j) => j.active);
export const focusEnd = (b: { words?: number; notes?: number }) => request<{ summary: FocusSummary }>('/api/think/focus/end', { method: 'POST', body: b }).then((j) => j.summary);
export const focusSummary = (id: number) => request<{ summary: FocusSummary }>(`/api/think/focus/summary/${id}`).then((j) => j.summary);
export const focusSeen = (id: number) => request(`/api/think/focus/seen/${id}`, { method: 'POST' });
