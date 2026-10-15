// 「今天」页（../server/today.py，2026-10-13 重做）：服务器按三块分好组（待处理 / 日程 / 报告），app 只画。
// 周 / 月视图读 /api/today/range；左滑推迟写 /api/today/snooze。服务器有没有这套看 supports('today')。
// 2026-10-15 类别（server/todocat.py，supports('today.categories')）：开着时每行带 category（学习 / 生活 / 工作），过期超过 7 天的在 pending.stale，
// 左滑能「不用管」（/api/remember/done how=dismissed），点开能改类别（/api/remember/category）；报告里多一张工程看板卡（/api/today/eng）。
import type { ScheduleEntry, TodoCat } from '../data/types';
import { request } from './base';

export type Tone = 'bad' | 'warn' | 'gold' | 'neutral' | 'good';

/** 待处理的一行。kind：inbox（等你点头，完整的卡从 store 的 inbox 里找）/ security / todo / deadline。 */
export interface PendingRow {
  id: string; ref: string | null; kind: string; origin: string; inboxId?: string;
  title: string; sub: string; chip: string; tone: Tone; date: string | null; time: string; detail: string; link: string | null;
  done: boolean; urgent: boolean; agent: string | null;
  project?: { id: string; title: string } | null; study?: { course: string | null; session: string | null } | null;
  canTick: boolean; canSnooze: boolean;
  /** 类别（开着 today.categories 才有）；categoryAuto = 没改过时自动分的那一类，categorySet = 你改过 */
  category?: TodoCat | null; categoryAuto?: TodoCat | null; categorySet?: boolean;
  /** 过期几天（0 = 没过期）；kept = 来源里已经没了，照快照列着；canDismiss = 能「不用管」；dismissed = 别的日子里看到的「不用管了」 */
  overdueDays?: number; kept?: boolean; canDismiss?: boolean; dismissed?: boolean;
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
/** 工程看板卡（server/engboard.py）：这周做完 / 没做 / 等你三个数、主线几步，点进看板页。 */
export interface EngReport { type: 'eng'; id: string; kind: 'eng_board'; title: string; time: string; weekDone: number; todo: number; wait: number; main: EngMain[] }
export type Report = CardReport | MailReport | EngReport;

/** 主线的一段（内核 10 步、群聊 3 步）。cells 只在看板页给：每一步做没做完。 */
export interface EngMain { name: string; done: number; total: number; cells?: boolean[] }
export type EngStatus = 'doing' | 'wait' | 'queue' | 'done';
export interface EngLine { id: string; name: string; heading: string; done: number; total: number; status: EngStatus; statusLabel: string; day: string | null; archived: boolean }
export interface EngCard { id: string; title: string; tags?: { text: string; tone: string }[]; data: { text?: string; line?: string; doneAt?: string | null }; display: Record<string, string> }
export interface EngColumn { key: string; id: string; label: string; count: number; color: number | null; drop: boolean; cards: EngCard[]; more: number }
/** 工程看板页（GET /api/today/eng）。 */
export interface EngBoard {
  weekDone: number; todo: number; wait: number; main: EngMain[]; mainCells: EngMain[]; mainNote: string; syncedAt: string | null;
  /** 交接本最后一次改动后同步的时间（今天的写几点，更早的写日子） */
  syncedLabel: string;
  lines: EngLine[]; older: EngLine[]; columns: EngColumn[];
}

export interface TodayDay {
  day: string; today: string; weekday: string; now: string | null;
  /** stale：过期超过 7 天还没处理的（只在开着 today.categories 时有） */
  pending: { items: PendingRow[]; nodate: PendingRow[]; stale: PendingRow[] };
  /** 服务器开没开类别（server.json today.categories）；list 是三类的名字 */
  categories: { on: boolean; list: { key: TodoCat; label: string }[] };
  schedule: { items: DayEvent[]; allDay: DayEvent[]; calendar: boolean };
  reports: Report[]; errors: Record<string, string>;
}

/** 周 / 月视图的一条。type：event 日程 / deadline 截止 / todo 待办（月历上三色点）。 */
export interface RangeItem {
  id: string; refs: string[]; date: string; type: 'event' | 'deadline' | 'todo'; kind: string; origin: string;
  title: string; sub: string; start: string; end: string; allDay: boolean; done: boolean; skip: boolean; past: boolean;
  tags: string[]; agent: string | null; link: string | null; detail: string; category?: TodoCat | null;
}

const arr = <T,>(v: unknown): T[] => (Array.isArray(v) ? (v as T[]) : []);

/** 服务器少给了哪个数组也别让页面崩。 */
function normDay(j: Partial<TodayDay>): TodayDay {
  const p = (j.pending ?? {}) as Partial<TodayDay['pending']>;
  const s = (j.schedule ?? {}) as Partial<TodayDay['schedule']>;
  const c = (j.categories ?? {}) as Partial<TodayDay['categories']>;
  const ev = (e: DayEvent): DayEvent => ({ ...e, refs: arr<string>(e.refs).length ? e.refs : [e.id], tags: arr<string>(e.tags), clash: arr<string>(e.clash), cards: arr<FeedRef>(e.cards) });
  return {
    day: String(j.day ?? ''), today: String(j.today ?? ''), weekday: String(j.weekday ?? ''), now: j.now ?? null,
    pending: { items: arr<PendingRow>(p.items), nodate: arr<PendingRow>(p.nodate), stale: arr<PendingRow>(p.stale) },
    categories: { on: !!c.on, list: arr<{ key: TodoCat; label: string }>(c.list) },
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

/** 打勾 / 取消一条待处理（和「要记得的」同一个接口），带上标题和时间当快照。how = dismissed：「不用管」（和做完分开记）。 */
export const tick = (r: Pick<PendingRow, 'ref' | 'title' | 'date' | 'time'>, done: boolean, how: 'done' | 'dismissed' = 'done') =>
  request<{ ok: boolean; card?: { logId: number } }>('/api/remember/done', {
    method: 'POST', body: { ref: r.ref, done, how, title: r.title, date: r.date, time: r.time || null },
  });

/** 改一条的类别（以你改的为准）；null = 回到自动分。 */
export const setCategory = (ref: string, category: TodoCat | null) =>
  request<{ ok: boolean; changed: boolean; category: TodoCat | null; card?: { logId: number } }>('/api/remember/category', { method: 'POST', body: { ref, category } });

export interface EngItem { id: string; title: string; text: string; state: 'todo' | 'wait' | 'done'; doneAt: string | null; group: string | null }
/** 看板页点开的一条线：没做的、等你的、做完的（新的在前）。 */
export interface EngLineDetail { id: string; name: string; heading: string; status: EngStatus; statusLabel: string; todo: EngItem[]; wait: EngItem[]; done: EngItem[] }

export async function loadEngLine(id: string): Promise<EngLineDetail> {
  const j = await request<Partial<EngLineDetail>>(`/api/today/eng/line/${encodeURIComponent(id)}`);
  return {
    id: String(j.id ?? id), name: String(j.name ?? ''), heading: String(j.heading ?? ''), status: (j.status ?? 'queue') as EngStatus,
    statusLabel: String(j.statusLabel ?? ''), todo: arr<EngItem>(j.todo), wait: arr<EngItem>(j.wait), done: arr<EngItem>(j.done),
  };
}

/** 工程看板页（只读）。 */
export async function loadEng(): Promise<EngBoard> {
  const j = await request<Partial<EngBoard>>('/api/today/eng');
  return {
    weekDone: Number(j.weekDone ?? 0), todo: Number(j.todo ?? 0), wait: Number(j.wait ?? 0), main: arr<EngMain>(j.main),
    mainCells: arr<EngMain>(j.mainCells), mainNote: String(j.mainNote ?? ''), syncedAt: j.syncedAt ?? null, syncedLabel: String(j.syncedLabel ?? ''),
    lines: arr<EngLine>(j.lines), older: arr<EngLine>(j.older), columns: arr<EngColumn>(j.columns),
  };
}
