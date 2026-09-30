// 日程和「要记得的」（server/schedule.py）：合并后的时间线、改自己的日程、课「不去」、打勾、撤销、iPhone 日历订阅。
import type { ScheduleChangeCard, ScheduleEntry, ScheduleFeed } from '../data/types';
import { L } from '../lang';
import { httpStatus, request } from './base';
import type { LiveEvent } from './live';

/** 老服务器没有 /api/schedule：日程退回只读的 /api/calendar（不能改），「要记得的」是空的。null = 还没试过。 */
export const scheduleSupport: { ok: boolean | null } = { ok: null };
const missing = (e: unknown) => { const s = httpStatus(e); return s === 404 || s === 405; };

/** 服务器那边字段缺了也别让界面崩。 */
function norm(raw: Partial<ScheduleEntry> & { id: string }): ScheduleEntry {
  const str = (v: unknown) => (typeof v === 'string' ? v : '');
  return {
    id: String(raw.id), kind: raw.kind ?? 'event', origin: raw.origin ?? 'own', title: str(raw.title), detail: str(raw.detail),
    location: str(raw.location), note: str(raw.note), date: typeof raw.date === 'string' ? raw.date : null, weekday: raw.weekday,
    start: str(raw.start), end: str(raw.end), allDay: !!raw.allDay, badge: str(raw.badge), by: raw.by ?? null, link: raw.link ?? null,
    done: !!raw.done, skip: !!raw.skip, series: !!raw.series, attended: typeof raw.attended === 'boolean' ? raw.attended : null,
    actualStart: str(raw.actualStart), actualEnd: str(raw.actualEnd), actualFrom: raw.actualFrom, past: !!raw.past,
    tentative: !!raw.tentative, free: !!raw.free, clash: Array.isArray(raw.clash) ? raw.clash.map(String) : [], editable: !!raw.editable,
    group: raw.group ?? null, urgent: !!raw.urgent, key: raw.key ?? null, locationChanged: !!raw.locationChanged,
    sourceLocation: raw.sourceLocation,
    project: raw.project && typeof raw.project.id === 'string' ? { id: raw.project.id, title: str(raw.project.title) } : undefined,
    study: raw.study && typeof raw.study.course === 'string' ? { course: raw.study.course, session: typeof raw.study.session === 'string' ? raw.study.session : null } : undefined,
  };
}

/** 老接口的一行（只读课表 + ddl）。 */
function fromLive(e: LiveEvent & { deadline?: boolean }, i: number): ScheduleEntry {
  const allDay = e.all_day || !/^\d{1,2}:\d{2}$/.test(e.start);
  return norm({ id: `legacy:${e.date}:${i}`, kind: e.deadline ? 'deadline' : 'class', origin: e.deadline ? 'canvas' : 'calendar',
    title: e.title, location: e.location, date: e.date, weekday: e.weekday, start: allDay ? '' : e.start, end: e.end, allDay,
    past: e.past, tentative: e.tentative });
}

export type Timeline = { events: ScheduleEntry[]; errors: Record<string, string>; editable: boolean };

/** from（YYYY-MM-DD）起 days 天的时间线：课表 + 自己的 + 到期那天的截止。 */
export async function timeline(from: string, days = 1): Promise<Timeline> {
  if (scheduleSupport.ok !== false) {
    try {
      const j = await request<{ events: (Partial<ScheduleEntry> & { id: string })[]; errors?: Record<string, string> }>(`/api/schedule?from=${from}&days=${days}`);
      scheduleSupport.ok = true;
      return { events: j.events.map(norm), errors: j.errors ?? {}, editable: true };
    } catch (e) {
      if (!missing(e)) throw e;
      scheduleSupport.ok = false;
    }
  }
  const j = await request<{ events: (LiveEvent & { deadline?: boolean })[] }>(`/api/calendar?from=${from}&days=${days}`);
  return { events: j.events.map(fromLive), errors: {}, editable: false };
}

/** 要记得的（按 group 分好）。老服务器没有就是空的。 */
export async function remember(): Promise<{ items: ScheduleEntry[]; errors: Record<string, string> }> {
  if (scheduleSupport.ok === false) return { items: [], errors: {} };
  try {
    const j = await request<{ items: (Partial<ScheduleEntry> & { id: string })[]; errors?: Record<string, string> }>('/api/remember');
    return { items: j.items.map(norm), errors: j.errors ?? {} };
  } catch (e) {
    if (missing(e)) return { items: [], errors: {} };
    throw e;
  }
}

export type Changed = { ok: boolean; changed?: boolean; id?: string; card?: ScheduleChangeCard | null; cards?: (ScheduleChangeCard | null)[] };
export interface ItemFields { title?: string; date?: string; start?: string | null; end?: string | null; kind?: 'event' | 'deadline'; location?: string; note?: string }

const path = (id: string) => `/api/schedule/${encodeURIComponent(id)}`;

export const addItem = (b: ItemFields & { title: string; date: string }) => request<Changed>('/api/schedule', { method: 'POST', body: b });
export const patchItem = (id: string, b: ItemFields & { attended?: boolean | null; actualStart?: string | null; actualEnd?: string | null }) =>
  request<Changed>(path(id), { method: 'PATCH', body: b });
export const deleteItem = (id: string) => request<Changed>(path(id), { method: 'DELETE' });
/** 课表里那一节：不去（series = 每周）、改地点、备注、去没去。 */
export const mark = (b: { ref: string; skip?: boolean; series?: boolean; location?: string; note?: string; attended?: boolean | null; actualStart?: string | null; actualEnd?: string | null }) =>
  request<Changed>('/api/schedule/mark', { method: 'POST', body: b });
/** 打勾 / 取消。带上标题和时间当快照（源头没了以后过去的日子还显示得出来）。 */
export const tick = (e: ScheduleEntry, done: boolean) =>
  request<Changed>('/api/remember/done', { method: 'POST', body: { ref: e.id, done, title: e.title, date: e.date, time: e.start || null } });
/** 撤销一次改动（redo = 再做回来）。 */
export const undo = (logId: number, redo = false) => request<{ card: ScheduleChangeCard }>(`/api/schedule/undo/${logId}`, { method: 'POST', body: { redo } });
export const feed = () => request<ScheduleFeed>('/api/schedule/feed');
export const setFeed = (b: { include?: Partial<ScheduleFeed['include']>; rotate?: boolean }) => request<ScheduleFeed>('/api/schedule/feed', { method: 'POST', body: b });

/** 这次改动的卡（有就返回第一张），给「撤销」用。 */
export const cardOf = (r: Changed): ScheduleChangeCard | null => r.card ?? r.cards?.find((c): c is ScheduleChangeCard => !!c) ?? null;

export const scheduleError = (e: unknown) => (e instanceof Error ? e.message : L('没改成', "Couldn't change it"));
