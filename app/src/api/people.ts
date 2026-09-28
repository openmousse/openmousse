// 朋友画像（服务端 ../server/people.py，2026-09-28）：和朋友一起录播客，整理完给每个认出来的人记几条（看法 / 在做的事 / 在意的 / 下次问问），
// 带出处（哪一期哪一句）。只有你看得到：名片 agent、世界树、库、主对话都不用它。能改能删、自己加。
import { request } from './base';

export type NoteKind = 'view' | 'doing' | 'care' | 'ask';
export interface Person { id: string; name: string; friend: string | null; friendName: string | null; notes: number; asks: number; lastAt: string | null; createdAt: string; updatedAt: string }
/** status：active 现在的 / replaced 被新的一条取代（旧说法）/ done 下次问问的问过了。episode.gone = 那一期删了（原声不在了）。 */
export interface PersonNote {
  id: string; kind: NoteKind; text: string; quote: string | null; sid: string | null; at: number | null; status: 'active' | 'replaced' | 'done';
  replaces: string | null; by: 'podcast' | 'me'; edited: boolean; createdAt: string; updatedAt: string;
  episode: { id: string; title: string | null; gone: boolean } | null;
}
export interface PersonPage { person: Person; notes: PersonNote[]; episodes: { id: string; title: string; status: string; createdAt: string }[]; kinds: { kind: NoteKind; label: string }[] }

export const list = () => request<{ people: Person[]; friends: { id: string; name: string }[] }>('/api/people');
export const get = (id: string) => request<PersonPage>(`/api/people/${id}`);
export const create = (name: string, friend?: string) => request<PersonPage>('/api/people', { method: 'POST', body: { name, friend } });
export const rename = (id: string, name: string) => request<PersonPage>(`/api/people/${id}`, { method: 'PATCH', body: { name } });
export const remove = (id: string) => request(`/api/people/${id}`, { method: 'DELETE' });
export const addNote = (id: string, kind: NoteKind, text: string) => request<PersonPage>(`/api/people/${id}/notes`, { method: 'POST', body: { kind, text } });
export const patchNote = (nid: string, b: { text?: string; kind?: NoteKind; status?: 'active' | 'done' }) =>
  request<PersonPage>(`/api/people/notes/${nid}`, { method: 'PATCH', body: b });
export const removeNote = (nid: string) => request<PersonPage>(`/api/people/notes/${nid}`, { method: 'DELETE' });
