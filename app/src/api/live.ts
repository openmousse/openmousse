// 看板数据：训练 / 饮食 / 身体数据、日历、Apple 健康，全部只读；来源由服务器的可选数据源决定。
import type { HealthDay } from './health';
import { AuthError, request } from './base';

export interface LiveTrain { title: string; minutes: number; kcal: number | null; start: string; sets_done: number;
  movements: { name: string; type: string; sets_done: number; sets_total: number; top_set: string }[] }
export interface LiveDay { date: string; d: string; minutes: number; label: string; future: boolean; trains: LiveTrain[] }
export interface LiveWeek { source: string; week_start: string; today_index: number; sessions: number; active_days: number; total_minutes: number; total_sets: number; days: LiveDay[] }
export interface LiveMeal { label: string; kcal: number; protein: number; items: { name: string; amount: number; unit: string; kcal: number | null }[] }
export interface LiveDiet { source: string; date: string; totals: { kcal: number; protein: number; carb: number; fat: number };
  targets: { kcal: number; protein: number; carb: number; fat: number; source?: string; kcal_derived?: boolean } | null; item_count: number; meals: LiveMeal[] }
export interface LiveMetric { type: string; label: string; value: number; unit: string; date: string }
export interface LiveEvent { date: string; weekday: string; all_day: boolean; start: string; end: string; title: string; location: string; past: boolean; tentative: boolean }

export interface LiveHealth { days: HealthDay[]; synced_at: string | null }

// 派生指标，算法在服务器的健康数据源脚本里（对话里的 skill 用的是同一份）。
export interface LiveRecoveryDay {
  date: string; score: number | null; band: 'good' | 'ok' | 'low' | null; label: string; hrv_kind: string | null;
  components: {
    hrv?: { kind: string; value: number; baseline: number; score: number }; rhr?: { value: number; baseline: number; score: number };
    sleep?: { minutes: number; score: number }; temp?: { value: number; baseline: number; deviation: number; score: number };
  };
  resp: { value: number; baseline: number } | null;
  training_yesterday: { date: string; minutes: number; kcal: number; types: string[]; effort: number | null; hard: boolean } | null;
  modifier: number; notes: string[];
}
export interface LiveRecovery { days: LiveRecoveryDay[]; latest: LiveRecoveryDay | null; baseline_days: number; synced_at: string | null; method: string }
export interface LiveEnergyDay { date: string; weekday: string; active: number | null; basal: number | null; burned: number | null; basal_estimated: boolean; partial: boolean; intake: number | null; intake_items: number | null; deficit: number | null }
export interface LiveEnergy {
  days: LiveEnergyDay[];
  summary: { days_counted: number; avg_burned: number | null; avg_intake: number | null; avg_deficit: number | null; total_deficit: number | null; est_fat_kg: number | null };
  basal_ref: number | null; intake_error: string | null; synced_at: string | null; note: string;
}
export interface LiveTrendStat { last7: number | null; window: number | null; days: number }
export interface LiveTrend {
  days: number; walking_hr: LiveTrendStat; resting_hr: LiveTrendStat; steps: LiveTrendStat; exercise_min: LiveTrendStat;
  vo2max: { points: { date: string; value: number }[]; latest: { date: string; value: number } | null; change: number | null }; synced_at: string | null;
}

/** 服务器接了哪些类型的数据源（/api/health 的 sources）。false 的那块看板显示「还没接」，不算错误。 */
export type SourceKind = 'workouts' | 'meals' | 'body' | 'calendar' | 'health';
export type Sources = Partial<Record<SourceKind, boolean>>;

export interface LiveData {
  health: LiveHealth | null; week: LiveWeek | null; diet: LiveDiet | null; body: LiveMetric[]; events: LiveEvent[];
  recovery: LiveRecovery | null; energy: LiveEnergy | null; trend: LiveTrend | null; sources: Sources; loadedAt: string;
}

const get = <T,>(path: string) => request<T>(path);

export type ProbeResult = { status: 'ok'; appName: string; sharedChannels: string[]; sources: Sources } | { status: 'auth' | 'down' };

type HealthResp = { ok: boolean; app_name?: string; shared_channels?: string[]; sources?: Sources };

export async function probe(): Promise<ProbeResult> {
  try {
    const h = await get<HealthResp>('/api/health');
    return h.ok ? { status: 'ok', appName: h.app_name || 'OpenMousse', sharedChannels: h.shared_channels ?? [], sources: h.sources ?? {} } : { status: 'down' };
  } catch (e) {
    return { status: e instanceof AuthError ? 'auth' : 'down' };
  }
}

/** 各块互不拖累：某一块失败就留空并记下原因，其它照常显示。没接数据源的类型不去请求，也不算错误。 */
export async function loadLive(): Promise<{ data: LiveData; errors: Record<string, string> }> {
  const errors: Record<string, string> = {};
  const safe = async <T,>(key: string, p: Promise<T>, fallback: T): Promise<T> => {
    try { return await p; } catch (e) { errors[key] = e instanceof Error ? e.message : String(e); return fallback; }
  };
  const sources: Sources = await get<HealthResp>('/api/health').then((h) => h.sources ?? {}).catch(() => ({}));
  const has = (k: SourceKind) => sources[k] !== false;
  const skip = <T,>(fallback: T) => Promise.resolve(fallback);
  const [health, week, diet, body, cal, recovery, energy, trend] = await Promise.all([
    safe('health', get<LiveHealth>('/api/health/daily?days=14'), null as LiveHealth | null),
    safe('week', has('workouts') ? get<LiveWeek>('/api/fitness/week') : skip(null), null as LiveWeek | null),
    safe('diet', has('meals') ? get<LiveDiet>('/api/diet/day') : skip(null), null as LiveDiet | null),
    safe('body', has('body') ? get<{ metrics: LiveMetric[] }>('/api/body/latest') : skip({ metrics: [] }), { metrics: [] }),
    safe('events', has('calendar') ? get<{ events: LiveEvent[] }>('/api/calendar?days=2') : skip({ events: [] }), { events: [] }),
    safe('recovery', has('health') ? get<LiveRecovery>('/api/health/recovery?days=14') : skip(null), null as LiveRecovery | null),
    safe('energy', has('health') ? get<LiveEnergy>('/api/health/energy?days=7') : skip(null), null as LiveEnergy | null),
    safe('trend', has('health') ? get<LiveTrend>('/api/fitness/trend?days=90') : skip(null), null as LiveTrend | null),
  ]);
  const now = new Date();
  const loadedAt = `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}`;
  return { data: { health, week, diet, body: body.metrics, events: cal.events, recovery, energy, trend, sources, loadedAt }, errors };
}

export const HEALTH_KEYS = ['health', 'recovery', 'energy', 'trend'] as const;

/** Apple 健康同步完只重读依赖它的四块。 */
export async function loadHealthParts(): Promise<{ patch: Pick<LiveData, 'health' | 'recovery' | 'energy' | 'trend'>; errors: Record<string, string> }> {
  const errors: Record<string, string> = {};
  const safe = async <T,>(key: string, p: Promise<T>): Promise<T | null> => {
    try { return await p; } catch (e) { errors[key] = e instanceof Error ? e.message : String(e); return null; }
  };
  const sources: Sources = await get<HealthResp>('/api/health').then((h) => h.sources ?? {}).catch(() => ({}));
  const derived = sources.health !== false;
  const [health, recovery, energy, trend] = await Promise.all([
    safe('health', get<LiveHealth>('/api/health/daily?days=14')),
    derived ? safe('recovery', get<LiveRecovery>('/api/health/recovery?days=14')) : null,
    derived ? safe('energy', get<LiveEnergy>('/api/health/energy?days=7')) : null,
    derived ? safe('trend', get<LiveTrend>('/api/fitness/trend?days=90')) : null,
  ]);
  return { patch: { health, recovery, energy, trend }, errors };
}

/** 「今天」页翻到别的日子：那天的 IC 日程。 */
export function loadEventsOn(date: string): Promise<LiveEvent[]> {
  return get<{ events: LiveEvent[] }>(`/api/calendar?from=${date}&days=1`).then((j) => j.events);
}
