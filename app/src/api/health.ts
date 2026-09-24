// Apple 健康（第 4 步）：原生 app 读 HealthKit，按天汇总后推给 server/health.py。两层：
// - health_daily：睡眠分期、夜间 HRV、静息心率等，给「恢复」卡用。一晚的睡眠归到醒来那天（前一天 18:00 到当天 14:00）。
// - health_metrics：全部类型按天汇总，一个指标一行：数值类累加型求和、其余取均值 / 最小 / 最大；
//   类别类记次数和时长；外加体能训练和心情记录。不存原始样本。训练和饮食以你接的数据源为准。
// "哪一天"都按手机当地时区。
import { Platform } from 'react-native';
import {
  CategoryValueSleepAnalysis, isHealthDataAvailable, queryCategorySamples, queryQuantitySamples, queryStateOfMindSamples,
  queryStatisticsCollectionForQuantity, queryWorkoutSamples, requestAuthorization, WorkoutActivityType,
} from '@kingstinct/react-native-healthkit';
import { CATEGORY_TYPES, QUANTITY_TYPES } from '../data/healthTypes';
import { authHeaders, getBase } from './base';

const READ = [
  'HKCategoryTypeIdentifierSleepAnalysis',
  'HKQuantityTypeIdentifierHeartRateVariabilitySDNN',
  'HKQuantityTypeIdentifierRestingHeartRate',
  'HKQuantityTypeIdentifierRespiratoryRate',
  'HKQuantityTypeIdentifierAppleSleepingWristTemperature',
] as const;

export interface HealthDay {
  date: string;
  sleep_min: number | null; deep_min: number | null; rem_min: number | null; core_min: number | null; awake_min: number | null;
  bed_start: string | null; bed_end: string | null;
  hrv_ms: number | null; rhr_bpm: number | null; resp_rate: number | null; wrist_temp_c: number | null;
}

export const healthSupported = () => Platform.OS === 'ios' && isHealthDataAvailable();

const ymd = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
const hm = (d: Date) => `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
const round = (x: number | null, k = 0) => (x == null ? null : Math.round(x * 10 ** k) / 10 ** k);
const mean = (xs: number[]) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null);

/** 手表和手机可能同时记睡眠，区间有重叠：先合并再算分钟，避免重复计。 */
function mergedMinutes(spans: [number, number][]): number {
  const s = [...spans].sort((a, b) => a[0] - b[0]);
  let total = 0; let cur: [number, number] | null = null;
  for (const [a, b] of s) {
    if (!cur || a > cur[1]) { if (cur) total += cur[1] - cur[0]; cur = [a, b]; } else cur[1] = Math.max(cur[1], b);
  }
  if (cur) total += cur[1] - cur[0];
  return total / 60000;
}

const iosAtLeast = (v: string) => {
  const [a, b = 0] = String(Platform.Version).split('.').map(Number);
  const [x, y = 0] = v.split('.').map(Number);
  return a > x || (a === x && b >= y);
};
// 生殖健康这一类不同步：不申请权限、不读、不上传。症状和医疗类照常同步，但只给 app 看，Grava 的工具读不到。
const EXCLUDED = new Set([
  'HKQuantityTypeIdentifierBasalBodyTemperature',
  'HKCategoryTypeIdentifierMenstrualFlow', 'HKCategoryTypeIdentifierIntermenstrualBleeding', 'HKCategoryTypeIdentifierPersistentIntermenstrualBleeding',
  'HKCategoryTypeIdentifierIrregularMenstrualCycles', 'HKCategoryTypeIdentifierInfrequentMenstrualCycles', 'HKCategoryTypeIdentifierProlongedMenstrualPeriods',
  'HKCategoryTypeIdentifierCervicalMucusQuality', 'HKCategoryTypeIdentifierOvulationTestResult', 'HKCategoryTypeIdentifierPregnancyTestResult',
  'HKCategoryTypeIdentifierProgesteroneTestResult', 'HKCategoryTypeIdentifierSexualActivity', 'HKCategoryTypeIdentifierContraceptive',
  'HKCategoryTypeIdentifierPregnancy', 'HKCategoryTypeIdentifierLactation', 'HKCategoryTypeIdentifierMenopausalState',
  'HKCategoryTypeIdentifierBleedingAfterMenopause', 'HKCategoryTypeIdentifierBleedingAfterPregnancy', 'HKCategoryTypeIdentifierBleedingDuringPregnancy',
]);
// 本机系统版本支持的类型才申请，申请不存在的类型会让 HealthKit 直接崩。
const QUANTITIES = () => QUANTITY_TYPES.filter(([id, v]) => !EXCLUDED.has(id) && iosAtLeast(v));
const CATEGORIES = () => CATEGORY_TYPES.filter(([id, v]) => !EXCLUDED.has(id) && iosAtLeast(v));
const EXTRA = () => ['HKWorkoutTypeIdentifier', ...(iosAtLeast('18.0') ? ['HKStateOfMindTypeIdentifier'] : [])];

/** 请求读取权限：先申请恢复卡要的几项，再申请全部。全部那次失败也不影响前者。
 *  HealthKit 不告诉 app 用户到底给没给读权限，没给的话查出来就是空的。 */
export async function authorizeHealth(): Promise<boolean> {
  if (!healthSupported()) return false;
  const ok = await requestAuthorization({ toRead: READ });
  const all = [...QUANTITIES().map((q) => q[0]), ...CATEGORIES().map((c) => c[0]), ...EXTRA()];
  await requestAuthorization({ toRead: all as any }).catch(() => false);
  return ok;
}

/** 读最近 days 天，按天汇总。 */
export async function readHealthDays(days = 14): Promise<HealthDay[]> {
  const end = new Date();
  const start = new Date(end); start.setDate(start.getDate() - days); start.setHours(18, 0, 0, 0);
  const filter = { date: { startDate: start, endDate: end } };
  const [sleep, hrv, rhr, resp, temp] = await Promise.all([
    queryCategorySamples('HKCategoryTypeIdentifierSleepAnalysis', { limit: 0, filter }),
    queryQuantitySamples('HKQuantityTypeIdentifierHeartRateVariabilitySDNN', { limit: 0, filter, unit: 'ms' }),
    queryQuantitySamples('HKQuantityTypeIdentifierRestingHeartRate', { limit: 0, filter, unit: 'count/min' }),
    queryQuantitySamples('HKQuantityTypeIdentifierRespiratoryRate', { limit: 0, filter, unit: 'count/min' }),
    queryQuantitySamples('HKQuantityTypeIdentifierAppleSleepingWristTemperature', { limit: 0, filter, unit: 'degC' }),
  ]);

  const out: HealthDay[] = [];
  for (let i = days - 1; i >= 0; i--) {
    const day = new Date(end); day.setDate(day.getDate() - i); day.setHours(0, 0, 0, 0);
    const from = new Date(day); from.setDate(from.getDate() - 1); from.setHours(18);
    const to = new Date(day); to.setHours(14);
    const night = sleep.filter((s) => s.endDate > from && s.endDate <= to);
    const span = (v: number[]) => night.filter((s) => v.includes(s.value)).map((s) => [+s.startDate, +s.endDate] as [number, number]);
    const staged = night.some((s) => [CategoryValueSleepAnalysis.asleepCore, CategoryValueSleepAnalysis.asleepDeep, CategoryValueSleepAnalysis.asleepREM].includes(s.value));
    // 有分期（手表）就只用分期；没有才用"未分期的睡着"（手机或第三方）。
    const asleepVals = staged
      ? [CategoryValueSleepAnalysis.asleepCore, CategoryValueSleepAnalysis.asleepDeep, CategoryValueSleepAnalysis.asleepREM]
      : [CategoryValueSleepAnalysis.asleepUnspecified];
    const asleep = span(asleepVals);
    const sleepMin = asleep.length ? mergedMinutes(asleep) : null;
    const bedStart = asleep.length ? new Date(Math.min(...asleep.map((x) => x[0]))) : null;
    const bedEnd = asleep.length ? new Date(Math.max(...asleep.map((x) => x[1]))) : null;
    // HRV 和呼吸频率取睡眠期间的均值（手表夜里测得最稳）；没睡眠记录就取当天。
    const inWin = (d: Date) => (bedStart && bedEnd ? d >= bedStart && d <= bedEnd : ymd(d) === ymd(day));
    const onDay = (d: Date) => ymd(d) === ymd(day);
    out.push({
      date: ymd(day),
      sleep_min: round(sleepMin),
      deep_min: staged ? round(mergedMinutes(span([CategoryValueSleepAnalysis.asleepDeep]))) : null,
      rem_min: staged ? round(mergedMinutes(span([CategoryValueSleepAnalysis.asleepREM]))) : null,
      core_min: staged ? round(mergedMinutes(span([CategoryValueSleepAnalysis.asleepCore]))) : null,
      awake_min: staged ? round(mergedMinutes(span([CategoryValueSleepAnalysis.awake]))) : null,
      bed_start: bedStart ? hm(bedStart) : null,
      bed_end: bedEnd ? hm(bedEnd) : null,
      hrv_ms: round(mean(hrv.filter((s) => inWin(s.startDate)).map((s) => s.quantity)), 1),
      rhr_bpm: round(mean(rhr.filter((s) => onDay(s.startDate)).map((s) => s.quantity))),
      resp_rate: round(mean(resp.filter((s) => inWin(s.startDate)).map((s) => s.quantity)), 1),
      wrist_temp_c: round(mean(temp.filter((s) => onDay(s.endDate)).map((s) => s.quantity)), 2),
    });
  }
  return out;
}

export interface MetricRow {
  date: string; metric: string; unit?: string | null;
  sum?: number | null; avg?: number | null; min?: number | null; max?: number | null;
  count?: number | null; minutes?: number | null; extra?: Record<string, unknown> | null;
}

/** 同时最多跑 n 个查询，单个失败（比如没授权、这台设备没有这种数据）只跳过它。 */
async function pool<T>(items: T[], n: number, f: (x: T) => Promise<void>) {
  let i = 0;
  await Promise.all(Array.from({ length: n }, async () => { while (i < items.length) { const x = items[i++]; await f(x).catch(() => {}); } }));
}

// HealthKit 的规范单位不都顺手：心率、呼吸频率是"次/秒"，UV 指数没有单位。
const UNIT_OVERRIDE: Record<string, string> = {
  HKQuantityTypeIdentifierHeartRate: 'count/min',
  HKQuantityTypeIdentifierRespiratoryRate: 'count/min',
  HKQuantityTypeIdentifierUVExposure: 'count',
};

const short = (id: string) => id.replace(/^HK(Quantity|Category)TypeIdentifier/, '');
const workoutName = (t: number) => (WorkoutActivityType as unknown as Record<number, string>)[t] ?? `type${t}`;

/** 全部类型按天汇总。 */
export async function readAllMetrics(days: number): Promise<MetricRow[]> {
  const end = new Date();
  const start = new Date(end); start.setDate(start.getDate() - days + 1); start.setHours(0, 0, 0, 0);
  const filter = { date: { startDate: start, endDate: end } };
  const rows: MetricRow[] = [];
  // 保留 4 位有效数字：维生素、矿物质按克存是 0.00005 这种量级，按小数位取整会变成 0。
  const r = (x: number | undefined) => (x == null || !isFinite(x) ? null : x === 0 ? 0 : Number(x.toPrecision(4)));

  await pool([...QUANTITIES()], 8, async ([id, , canonical, cumulative]) => {
    const unit = UNIT_OVERRIDE[id] ?? canonical;
    const stats = await queryStatisticsCollectionForQuantity(id as any, cumulative ? ['cumulativeSum'] : ['discreteAverage', 'discreteMin', 'discreteMax'],
      start, { day: 1 }, { filter, unit: unit as any });
    for (const st of stats) {
      if (!st.startDate) continue;
      const row: MetricRow = cumulative
        ? { date: ymd(st.startDate), metric: short(id), unit, sum: r(st.sumQuantity?.quantity) }
        : { date: ymd(st.startDate), metric: short(id), unit, avg: r(st.averageQuantity?.quantity), min: r(st.minimumQuantity?.quantity), max: r(st.maximumQuantity?.quantity) };
      if (row.sum != null || row.avg != null) rows.push(row);
    }
  });

  await pool([...CATEGORIES()], 8, async ([id]) => {
    const samples = await queryCategorySamples(id as any, { limit: 0, filter });
    const byDay = new Map<string, { count: number; minutes: number; values: Record<string, number> }>();
    for (const x of samples) {
      const k = ymd(x.startDate);
      const d = byDay.get(k) ?? { count: 0, minutes: 0, values: {} };
      const m = (+x.endDate - +x.startDate) / 60000;
      d.count += 1; d.minutes += m; d.values[String(x.value)] = (d.values[String(x.value)] ?? 0) + Math.round(m);
      byDay.set(k, d);
    }
    for (const [date, d] of byDay) rows.push({ date, metric: short(id), count: d.count, minutes: r(d.minutes), extra: { minutesByValue: d.values } });
  });

  await (async () => {
    const workouts = await queryWorkoutSamples({ limit: 0, filter });
    const byKey = new Map<string, MetricRow>();
    for (const w of workouts) {
      const x = w.toJSON();
      w.dispose();
      const date = ymd(x.startDate);
      const name = workoutName(x.workoutActivityType as number);
      const k = `${date}|${name}`;
      const cur = byKey.get(k) ?? { date, metric: `Workout.${name}`, unit: 'kcal', sum: 0, count: 0, minutes: 0, extra: { km: 0, sessions: [] as string[] } };
      cur.count! += 1;
      cur.minutes = r((cur.minutes ?? 0) + (+x.endDate - +x.startDate) / 60000);
      cur.sum = r((cur.sum ?? 0) + (x.totalEnergyBurned?.quantity ?? 0));
      const ex = cur.extra as { km: number; sessions: string[] };
      ex.km = r(ex.km + (x.totalDistance ? x.totalDistance.quantity / (x.totalDistance.unit === 'm' ? 1000 : 1) : 0)) ?? 0;
      ex.sessions.push(hm(x.startDate));
      byKey.set(k, cur);
    }
    rows.push(...byKey.values());
  })().catch(() => {});

  if (iosAtLeast('18.0')) {
    await (async () => {
      const moods = await queryStateOfMindSamples({ limit: 0, filter });
      const byDay = new Map<string, { v: number[]; labels: string[] }>();
      for (const m of moods) {
        const k = ymd(m.startDate);
        const d = byDay.get(k) ?? { v: [], labels: [] };
        d.v.push(m.valence); d.labels.push(...(m.labels ?? []).map(String));
        byDay.set(k, d);
      }
      for (const [date, d] of byDay) rows.push({ date, metric: 'StateOfMind', avg: r(mean(d.v) ?? undefined), min: r(Math.min(...d.v)), max: r(Math.max(...d.v)), count: d.v.length, extra: { labels: d.labels } });
    })().catch(() => {});
  }
  return rows;
}

async function post(path: string, body: object) {
  const r = await fetch(`${getBase()}${path}`, { method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() }, body: JSON.stringify(body) });
  if (!r.ok) throw new Error(`上传失败 HTTP ${r.status}`);
  return r.json();
}

/** 读取并上传。恢复卡的 14 天每次都推；全部指标平时推最近 30 天，服务器上还没有的时候补一年。 */
export async function syncHealth(days = 14): Promise<number> {
  await authorizeHealth();
  const rows = await readHealthDays(days);
  await post('/api/health/daily', { days: rows, source: 'healthkit' });
  const status = await fetch(`${getBase()}/api/health/metrics/status`, { headers: authHeaders() }).then((x) => x.json()).catch(() => ({ rows: 1 }));
  const metrics = await readAllMetrics(status.rows ? 30 : 365);
  for (let i = 0; i < metrics.length; i += 2000) await post('/api/health/metrics', { rows: metrics.slice(i, i + 2000), source: 'healthkit' });
  return rows.length;
}

export async function loadHealthDays(days = 14): Promise<{ days: HealthDay[]; synced_at: string | null }> {
  const r = await fetch(`${getBase()}/api/health/daily?days=${days}`, { headers: { Accept: 'application/json', ...authHeaders() } });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}
