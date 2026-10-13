// 「今天」页（../server/today.py，2026-10-13 重做）：服务器按三块分好组（待处理 / 日程 / 报告），app 只画。
// 周 / 月视图读 /api/today/range；左滑推迟写 /api/today/snooze。服务器有没有这套看 supports('today')。
import type { ScheduleEntry } from '../data/types';
import { request } from './base';

export type Tone = 'bad' | 'warn' | 'gold' | 'neutral' | 'good';

/** 待处理的一行。kind：inbox（等你点头，完整的卡从 store 的 inbox 里找）/ security / todo / deadline。 */
export interface PendingRow {
  id: string; ref: string | null; kind: string; origin: string; inboxId?: string;
  title: string; sub: string; chip: string; tone: Tone; date: string | null; time: string; detail: string; link: string | null;
  done: boolean; urgent: boolean; agent: string | null;
  project?: { id: string; title: string } | null; study?: { course: string | null; session: string | null } | null;
  canTick: boolean; canSnooze: boolean;
}

/** 一张 Agent 卡（feed）：归到日程里的有 slot（plan / review）和 label。 */
export interface FeedRef {
  id: string; kind: string; agent: string | null; title: string; body: string; cta: string; data: unknown;
  createdAt: string; time: string; unseen: boolean; slot?: 'plan' | 'review'; label?: string;
}

/** 日程的一条：和 /api/schedule 同样的字段，加上去重并进来的 refs、状态标签、时间格里的位置和归进来的卡。 */
export type DayEvent = ScheduleEntry & {
  refs: string[]; tags: string[]; slotStart: string; slotEnd: string; moved: boolean;
  state: 'skipped' | 'attended' | 'past' | 'now' | 'upcoming'; cards?: FeedRef[];
};

export interface SleepSummary {
  score: number | null; label: string; band: string | null; sleepMin: number | null;
  series: { date: string; score: number | null }[]; low: { date: string; score: number } | null;
}
export interface MealSummary {
  heading: string; dishes: string; totals: { kcal?: number; protein?: number; carb?: number; fat?: number } | null;
  logged: { count: number; kcal: number } | null;
}
export interface MailItem { ref: string; title: string; kind: string; category: string; date: string | null; time: string; detail: string; link: string | null; source: string }
export type CardReport = FeedRef & { type: 'card'; sleep?: SleepSummary | null; meal?: MealSummary };
export interface MailReport { type: 'mail'; id: string; kind: 'mail_digest'; title: string; count: number; categories: { key: string; label: string; count: number }[]; items: MailItem[] }
export type Report = CardReport | MailReport;

export interface TodayDay {
  day: string; today: string; weekday: string; now: string | null;
  pending: { items: PendingRow[]; nodate: PendingRow[] };
  schedule: { items: DayEvent[]; allDay: DayEvent[]; calendar: boolean };
  reports: Report[]; errors: Record<string, string>;
}

/** 周 / 月视图的一条。type：event 日程 / deadline 截止 / todo 待办（月历上三色点）。 */
export interface RangeItem {
  id: string; refs: string[]; date: string; type: 'event' | 'deadline' | 'todo'; kind: string; origin: string;
  title: string; sub: string; start: string; end: string; allDay: boolean; done: boolean; skip: boolean; past: boolean;
  tags: string[]; agent: string | null; link: string | null; detail: string;
}

const arr = <T,>(v: unknown): T[] => (Array.isArray(v) ? (v as T[]) : []);

/** 服务器少给了哪个数组也别让页面崩。 */
function normDay(j: Partial<TodayDay>): TodayDay {
  const p = (j.pending ?? {}) as Partial<TodayDay['pending']>;
  const s = (j.schedule ?? {}) as Partial<TodayDay['schedule']>;
  const ev = (e: DayEvent): DayEvent => ({ ...e, refs: arr<string>(e.refs).length ? e.refs : [e.id], tags: arr<string>(e.tags), clash: arr<string>(e.clash), cards: arr<FeedRef>(e.cards) });
  return {
    day: String(j.day ?? ''), today: String(j.today ?? ''), weekday: String(j.weekday ?? ''), now: j.now ?? null,
    pending: { items: arr<PendingRow>(p.items), nodate: arr<PendingRow>(p.nodate) },
    schedule: { items: arr<DayEvent>(s.items).map(ev), allDay: arr<DayEvent>(s.allDay).map(ev), calendar: !!s.calendar },
    reports: arr<Report>(j.reports), errors: (j.errors ?? {}) as Record<string, string>,
  };
}

// 按日子缓存：翻回来、切回这个 tab 先画上次的，后台再读一遍。null = 今天（按服务器的时区算，和手机的时区无关）
const cache = new Map<string, TodayDay>();
export const cachedDay = (iso: string | null) => cache.get(iso ?? 'today') ?? null;

/** 一天的三块。iso 为 null = 服务器的今天。 */
export async function loadDay(iso: string | null): Promise<TodayDay> {
  const d = normDay(await request<Partial<TodayDay>>(iso ? `/api/today?day=${iso}` : '/api/today'));
  cache.set(iso ?? 'today', d);
  return d;
}

export async function loadRange(from: string, days: number): Promise<{ items: RangeItem[]; errors: Record<string, string> }> {
  const j = await request<{ items?: RangeItem[]; errors?: Record<string, string> }>(`/api/today/range?from=${from}&days=${days}`);
  return { items: arr<RangeItem>(j.items).map((r) => ({ ...r, refs: arr<string>(r.refs), tags: arr<string>(r.tags) })), errors: j.errors ?? {} };
}

/** 推迟一条待处理到 until（YYYY-MM-DD）；null = 取消推迟。 */
export const snooze = (ref: string, until: string | null) => request<{ ok: boolean }>('/api/today/snooze', { method: 'POST', body: { ref, until } });

/** 打勾 / 取消一条待处理（和「要记得的」同一个接口），带上标题和时间当快照。 */
export const tick = (r: Pick<PendingRow, 'ref' | 'title' | 'date' | 'time'>, done: boolean) =>
  request<{ ok: boolean }>('/api/remember/done', { method: 'POST', body: { ref: r.ref, done, title: r.title, date: r.date, time: r.time || null } });
