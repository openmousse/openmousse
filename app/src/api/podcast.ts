// 播客（「思考」里的第三块，服务端 ../server/podcast.py）：今天聊点什么、录前先聊聊、一段段录（停一次传一段，服务器马上转写）、
// 主持人 / 外行追问、录完整理、存进库、费曼对照学习台。原声在服务器上，app 播的时候带令牌去取。
import { Platform } from 'react-native';
import type { AudioSource } from 'expo-audio';
import type { PendingFile } from '../data/types';
import { authHeaders, fileUrl, getBase, httpStatus, request } from './base';
import { formFile, xhrUpload } from './client';

export type PodMode = 'solo' | 'host' | 'feynman' | 'friends';
export type PodStatus = 'prep' | 'recording' | 'processing' | 'naming' | 'ready' | 'saved' | 'failed';
export interface PodSource { kind?: 'open' | 'study' | 'deadline' | 'tree' | 'own' | 'person'; label?: string; course?: string; page?: string; person?: string; name?: string }
export interface PodChip { text: string; tone: 'gold' | 'cyan' | 'red' | 'gray' }
export interface PodBrief { id: string; title: string; mode: PodMode; status: PodStatus; duration: number; createdAt: string; updatedAt: string; source: PodSource; chip: PodChip | null }
export interface PodSentence { i: number; t0: number; t1: number; text: string; flag?: string; fixed?: [string, string][]; edited?: boolean; speaker?: string }
export interface PodSegment { idx: number; offset: number; duration: number; status: 'transcribing' | 'done' | 'failed'; error: string | null; url: string; sentences: PodSentence[] }
export interface PodTurn { id: number; phase: 'prep' | 'rec'; role: 'host' | 'me'; text: string; at: number | null; voice: number | null; status: string | null; createdAt: string }
export interface PodQuote { id: string; at: number; text: string }
export interface PodRelate { path: string; title: string; then: string; now: string; at: number | null; changed: boolean }
/** 这一期给某个人记的画像：多了几条、取代了几条、「下次问问」问过了几条。 */
export interface PodPersonCount { person: string; name: string; added?: number; replaced?: number; answered?: number; /** 这次没记上（比如没有 llm-task）：上次记的原样留着 */ error?: string }
export interface PodResult {
  title: string; oneLine: string; quotes: PodQuote[]; open: string[]; keywords: string[]; suggest: string[]; tree: string; branch: string; relates: PodRelate[];
  minutes?: Record<string, string[]>; people?: PodPersonCount[];
}
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
  /** 放进这一期的素材条数（老服务器没有） */
  materials?: number;
  /** 约朋友：这一期有谁（开录前选的 + 认人对上的）、认人时 {声音: 人} */
  people?: { id: string; name: string }[];
  speakerPeople?: Record<string, string>;
}
/** mode friends = 朋友画像来的「约小林聊…」（source.person 是那个人）：点了去约朋友那页。 */
export interface PodSuggestion { title: string; mode: PodMode; source: PodSource }
export interface PodHome { suggestions: PodSuggestion[] | null; suggestedAt: string | null; episodes: PodBrief[]; study: boolean; materials?: boolean; people?: boolean }

export const home = () => request<PodHome>('/api/podcast');

export interface PodFeatures { podcast: boolean; materials: boolean; people: boolean }
let feats: PodFeatures | null = null;
/** 服务器有哪些：播客（老服务器 /api/podcast 是 404 / 405，思考页就不显示这一栏）、素材（长按「放进播客」）、朋友画像（我 → 朋友画像）。
 * 连不上时先当都有（别把栏目闪没了）。一次打开 app 只问一次。 */
export async function podFeatures(): Promise<PodFeatures> {
  if (feats) return feats;
  try {
    const h = await request<PodHome>('/api/podcast');
    feats = { podcast: true, materials: !!h.materials, people: !!h.people };
  } catch (e) {
    const s = httpStatus(e);
    if (s === 404 || s === 405) feats = { podcast: false, materials: false, people: false };
    else return { podcast: true, materials: true, people: true };
  }
  return feats;
}
export const podcastSupported = async () => (await podFeatures()).podcast;
export const suggest = (exclude: string[] = []) =>
  request<{ suggestions: PodSuggestion[] }>('/api/podcast/suggest', { method: 'POST', body: { exclude }, timeoutMs: 120000 }).then((j) => j.suggestions);
/** 认人 / 选谁在时挑的：已有的人、一个朋友（还没对上人就新建）、或者新名字；skip = 只写名字、不记画像。 */
export interface PersonPick { id?: string; name?: string; friend?: string; skip?: boolean }
export const create = (b: { title: string; mode: PodMode; source?: PodSource; people?: PersonPick[] }) =>
  request<{ episode: Episode }>('/api/podcast/episodes', { method: 'POST', body: b }).then((j) => j.episode);
export const get = (id: string) => request<{ episode: Episode }>(`/api/podcast/episodes/${id}`).then((j) => j.episode);
export const patch = (id: string, b: { title?: string; mode?: PodMode; outline?: string[]; done?: number[]; cur?: number; speakers?: Record<string, string>; people?: Record<string, PersonPick> }) =>
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

// —— 素材（../server/podmaterials.py）：一期播客放进来的主对话、朋友聊天、文件、Zen 想法 / 主题、收藏 ——
export type MatKind = 'chat' | 'friend' | 'file' | 'idea' | 'topic' | 'save';
/** who：me 你说的 / assistant 它回的（或你的名片 agent 替你答的）/ friend 朋友说的 / '' 别人写的（文件、收藏） */
export interface PodMaterial { id: string; kind: MatKind; ref: string; title: string; who: '' | 'me' | 'assistant' | 'friend'; at: string | null; preview: string; chars: number; friend?: string | null; note?: string | null; createdAt: string }
/** 挑素材的候选。kind friends = 朋友列表的一行（ref 是朋友 id，text 是名字，n 条消息）。 */
export interface PodPick { kind: MatKind | 'friends'; ref: string; text: string; at: string | null; who?: 'me' | 'agent' | 'friend'; where?: string; title?: string; n?: number; source?: string; agent?: boolean }
export const materials = (id: string) => request<{ items: PodMaterial[] }>(`/api/podcast/episodes/${id}/materials`).then((j) => j.items);
export const addMaterials = (id: string, items: { kind: MatKind; ref: string }[]) =>
  request<{ items: PodMaterial[]; added: string[]; failed: { kind: string; ref: string; error: string }[] }>(`/api/podcast/episodes/${id}/materials`, { method: 'POST', body: { items }, timeoutMs: 60000 });
export async function uploadMaterials(id: string, files: PendingFile[]) {
  const fd = new FormData();
  for (const f of files) formFile(fd, 'files', f);
  const j = await xhrUpload(`${getBase()}/api/podcast/episodes/${id}/materials/upload`, fd, 5 * 60 * 1000);
  return j as { items: PodMaterial[]; added: string[]; failed: { name: string; error: string }[] };
}
export const removeMaterial = (id: string, mid: string) => request<{ items: PodMaterial[] }>(`/api/podcast/episodes/${id}/materials/${mid}`, { method: 'DELETE' }).then((j) => j.items);
export const pick = (kind: MatKind, friend?: string) =>
  request<{ items: PodPick[] }>(`/api/podcast/pick?kind=${kind}${friend ? `&friend=${encodeURIComponent(friend)}` : ''}&days=14`).then((j) => j.items);
/** 长按一条「放进播客」：episode 不给 = 新开一期。 */
export const quick = (kind: MatKind, ref: string, episode?: string) =>
  request<{ episode: PodBrief; material: PodMaterial; count: number }>('/api/podcast/materials/quick', { method: 'POST', body: { kind, ref, episode }, timeoutMs: 60000 });

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
