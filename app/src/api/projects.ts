// 项目（server/projects.py）：一张项目卡——目标、截止、下一步、已定的、进度、在跑的任务、结论。
// 截止走日程层：自己的截止增删改、打勾出的是日程卡；下一步和已定的改动出项目小卡，都能撤销。
import type { ProjectCard, ProjectChangeCard, ScheduleChangeCard } from '../data/types';
import { request } from './base';

type Card = ProjectChangeCard | ScheduleChangeCard;
type Changed = { ok: boolean; changed?: boolean; card?: Card | null; cards?: (Card | null)[]; id?: string };
const path = (pid: string, tail = '') => `/api/projects/${encodeURIComponent(pid)}${tail}`;

export const get = (pid: string) => request<{ project: ProjectCard }>(path(pid)).then((j) => j.project);
export const create = (b: { title: string; goal?: string; model?: string; deadlines?: { title: string; due: string }[] }) =>
  request<{ id: string }>('/api/projects', { method: 'POST', body: b }).then((j) => j.id);
export const patch = (pid: string, b: { title?: string; goal?: string; progress?: string }) => request<Changed>(path(pid), { method: 'PATCH', body: b });
/** kind step / decision：text；deadline：text（交什么）+ due（YYYY-MM-DD 或 YYYY-MM-DD HH:MM） */
export const addItem = (pid: string, b: { kind: 'step' | 'decision' | 'deadline'; text: string; due?: string }) =>
  request<Changed>(path(pid, '/items'), { method: 'POST', body: b });
/** id：下一步 / 已定的是 pi-…，截止是它的 id。done 打勾；text、due 改（挂上来的截止只能打勾）。 */
export const updateItem = (pid: string, b: { id: string; text?: string; due?: string; done?: boolean }) =>
  request<Changed>(path(pid, '/items/update'), { method: 'POST', body: b });
export const deleteItem = (pid: string, id: string) => request<Changed>(path(pid, '/items/delete'), { method: 'POST', body: { id } });
export const undo = (logId: number, redo = false) => request<{ card: ProjectChangeCard }>(`/api/projects/undo/${logId}`, { method: 'POST', body: { redo } });
/** 归档：summarize = 先让它写结论（进记忆），项目卡上也留一份。 */
export const archive = (pid: string, summarize: boolean) => request<Changed>(path(pid, '/archive'), { method: 'POST', body: { summarize } });
export const restore = (pid: string) => request<Changed>(path(pid, '/restore'), { method: 'POST' });
