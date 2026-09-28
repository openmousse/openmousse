// 播客（「思考」里的第三块，服务端 ../server/podcast.py）：今天聊点什么、录前先聊聊、一段段录（停一次传一段，服务器马上转写）、
// 主持人 / 外行追问、录完整理、存进库、费曼对照学习台。原声在服务器上，app 播的时候带令牌去取。
import { Platform } from 'react-native';
import type { AudioSource } from 'expo-audio';
import type { PendingFile } from '../data/types';
import { authHeaders, fileUrl, getBase, request } from './base';
import { formFile, xhrUpload } from './client';

export type PodMode = 'solo' | 'host' | 'feynman' | 'friends';
export type PodStatus = 'prep' | 'recording' | 'processing' | 'naming' | 'ready' | 'saved' | 'failed';
export interface PodSource { kind?: 'open' | 'study' | 'deadline' | 'tree' | 'own'; label?: string; course?: string; page?: string }
export interface PodChip { text: string; tone: 'gold' | 'cyan' | 'red' | 'gray' }
export interface PodBrief { id: string; title: string; mode: PodMode; status: PodStatus; duration: number; createdAt: string; updatedAt: string; source: PodSource; chip: PodChip | null }
export interface PodSentence { i: number; t0: number; t1: number; text: string; flag?: string; fixed?: [string, string][]; edited?: boolean; speaker?: string }
export interface PodSegment { idx: number; offset: number; duration: number; status: 'transcribing' | 'done' | 'failed'; error: string | null; url: string; sentences: PodSentence[] }
export interface PodTurn { id: number; phase: 'prep' | 'rec'; role: 'host' | 'me'; text: string; at: number | null; voice: number | null; status: string | null; createdAt: string }
export interface PodQuote { id: string; at: number; text: string }
export interface PodRelate { path: string; title: string; then: string; now: string; at: number | null; changed: boolean }
export interface PodResult { title: string; oneLine: string; quotes: PodQuote[]; open: string[]; keywords: string[]; suggest: string[]; tree: string; branch: string; relates: PodRelate[]; minutes?: Record<string, string[]> }
export interface PodFeynman {
  right: string[];
  wrong: { id: string; at: number | null; said: string; correct: string; source: string }[];
  missed: { text: string; source: string }[];
  explain: string;
  against: string | null;
  questions: { at: number | null; text: string }[];
}
export interface Episode extends PodBrief {
  outline: { text: string; done: boolean }[];
  cur: number;
  error: string | null;
  segments: PodSegment[];
  speakers: Record<string, string>;
  turns: PodTurn[];
  result: PodResult | null;
  feynman: PodFeynman | null;
  saved: { path: string; folder: 'notes' | 'writing' | 'study'; at: string; tree: string | null; obsidian: string | null } | null;
  reviewAt: string | null;
}
export interface PodSuggestion { title: string; mode: Exclude<PodMode, 'friends'>; source: PodSource }
export interface PodHome { suggestions: PodSuggestion[] | null; suggestedAt: string | null; episodes: PodBrief[]; study: boolean }

export const home = () => request<PodHome>('/api/podcast');
export const suggest = (exclude: string[] = []) =>
  request<{ suggestions: PodSuggestion[] }>('/api/podcast/suggest', { method: 'POST', body: { exclude }, timeoutMs: 120000 }).then((j) => j.suggestions);
export const create = (b: { title: string; mode: PodMode; source?: PodSource }) =>
  request<{ episode: Episode }>('/api/podcast/episodes', { method: 'POST', body: b }).then((j) => j.episode);
export const get = (id: string) => request<{ episode: Episode }>(`/api/podcast/episodes/${id}`).then((j) => j.episode);
export const patch = (id: string, b: { title?: string; mode?: PodMode; outline?: string[]; done?: number[]; cur?: number; speakers?: Record<string, string> }) =>
  request<{ episode: Episode }>(`/api/podcast/episodes/${id}`, { method: 'PATCH', body: b }).then((j) => j.episode);
export const remove = (id: string) => request(`/api/podcast/episodes/${id}`, { method: 'DELETE' });
/** 录前先聊聊：不带 text = 让它先问；outline: true = 现在就排提纲。 */
export const prep = (id: string, b: { text?: string; outline?: boolean }) =>
  request<{ episode: Episode }>(`/api/podcast/episodes/${id}/prep`, { method: 'POST', body: b, timeoutMs: 120000 }).then((j) => j.episode);
export async function prepVoice(id: string, file: PendingFile, duration: number) {
  const fd = new FormData();
  formFile(fd, 'file', file);
  fd.append('duration', String(duration));
  const j = await xhrUpload(`${getBase()}/api/podcast/episodes/${id}/prep/voice`, fd, 3 * 60 * 1000);
  return j.episode as Episode;
}
/** 停一次传一段（idx 从 0 数；同一个 idx 再传 = 重传）。 */
export async function uploadSegment(id: string, idx: number, file: PendingFile, duration: number) {
  const fd = new FormData();
  formFile(fd, 'file', file);
  fd.append('idx', String(idx));
  fd.append('duration', String(duration));
  await xhrUpload(`${getBase()}/api/podcast/episodes/${id}/segments`, fd, 5 * 60 * 1000);
}
export const ask = (id: string, how: 'next' | 'again' | 'skip' = 'next') =>
  request<{ episode: Episode }>(`/api/podcast/episodes/${id}/ask`, { method: 'POST', body: { how }, timeoutMs: 120000 }).then((j) => j.episode);
export const finish = (id: string) => request<{ episode: Episode }>(`/api/podcast/episodes/${id}/finish`, { method: 'POST' }).then((j) => j.episode);
export const editSentence = (id: string, idx: number, i: number, text: string) =>
  request<{ episode: Episode; vocab: string[] }>(`/api/podcast/episodes/${id}/sentence`, { method: 'PATCH', body: { idx, i, text } });
export interface SaveBody {
  folder: 'notes' | 'writing' | 'study'; title: string; oneLine: string; quotes: { text: string; at: number | null }[]; open: string[]; keywords: string[];
  relates: PodRelate[]; explain?: string | null; tree?: string | null; branch?: string | null;
}
export const save = (id: string, b: SaveBody) =>
  request<{ path: string; obsidian: string | null; tree: { ok?: boolean } | null; episode: Episode }>(`/api/podcast/episodes/${id}/save`, { method: 'POST', body: b, timeoutMs: 90000 });
export const review = (id: string) => request<{ added: number; episode: Episode }>(`/api/podcast/episodes/${id}/review`, { method: 'POST' });

/** 一段原声：原生上带请求头取（expo-audio 支持），网页版的 <audio> 带不了头，令牌放在 query 里（服务器只对 GET 原声认它）。 */
export function audioSource(url: string): AudioSource {
  if (Platform.OS === 'web') return fileUrl(url);
  return { uri: url.startsWith('http') ? url : `${getBase()}${url}`, headers: authHeaders() };
}

/** 秒 → 3:07 / 1:02:09。 */
export function clock(s: number | null | undefined) {
  const t = Math.max(0, Math.round(s ?? 0));
  const h = Math.floor(t / 3600);
  const m = Math.floor((t % 3600) / 60);
  const sec = String(t % 60).padStart(2, '0');
  return h ? `${h}:${String(m).padStart(2, '0')}:${sec}` : `${m}:${sec}`;
}
