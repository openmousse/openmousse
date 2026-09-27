// 目标（server/goals.py）：读、加、改、完成 / 不做了、撤销，体重和体脂的读数趋势。
import type { Goal, GoalCategory, GoalChange, GoalMetric, GoalStatus, GoalTrend, GoalTrendPoint } from '../data/types';
import { L } from '../lang';
import { httpStatus, request } from './base';

const missing = (e: unknown) => { const s = httpStatus(e); return s === 404 || s === 405; };
const str = (v: unknown) => (typeof v === 'string' ? v : '');
const num = (v: unknown) => (typeof v === 'number' && Number.isFinite(v) ? v : null);
const CATEGORIES: GoalCategory[] = ['健康', '学业', '职业', '财务'];
const STATUSES: GoalStatus[] = ['active', 'done', 'dropped'];

/** 服务器在另一边同时开发（也可能是老版本）：字段缺了补默认值，别让界面崩。 */
function normGoal(raw: any): Goal {
  return {
    id: String(raw.id), category: CATEGORIES.includes(raw.category) ? raw.category : '健康', title: str(raw.title), detail: str(raw.detail),
    due: str(raw.due), daysLeft: num(raw.daysLeft), groupId: str(raw.groupId) || null, source: str(raw.source),
    status: STATUSES.includes(raw.status) ? raw.status : 'active', metric: str(raw.metric) || null,
    unit: str(raw.unit) || null, targetLow: num(raw.targetLow), targetHigh: num(raw.targetHigh),
    current: num(raw.current), currentDate: str(raw.currentDate) || null, currentSource: str(raw.currentSource) || null,
    start: num(raw.start), startDate: str(raw.startDate) || null, progress: num(raw.progress),
    direction: ['down', 'up', 'keep'].includes(raw.direction) ? raw.direction : null,
    state: ['in', 'above', 'below'].includes(raw.state) ? raw.state : null,
    stale: !!raw.stale, addedBy: str(raw.addedBy) || null, closedAt: str(raw.closedAt) || null,
  };
}

function normChange(raw: any): GoalChange | null {
  if (!raw || typeof raw.logId !== 'number') return null;
  return { logId: raw.logId, at: str(raw.at), actor: str(raw.actor), actorName: str(raw.actorName), goal: str(raw.goal), title: str(raw.title),
    action: str(raw.action), summary: str(raw.summary), status: raw.status === 'undone' ? 'undone' : 'done' };
}

/** 老服务器没给 metrics 时用的（写成函数：L 要在用的时候取语言）。 */
const fallbackMetrics = (): GoalMetric[] => [{ key: 'bodyfat', label: L('体脂', 'Body fat'), unit: '%' }, { key: 'weight', label: L('体重', 'Weight'), unit: 'kg' }];

export type GoalsData = { goals: Goal[]; closed: Goal[]; recent: GoalChange[]; metrics: GoalMetric[]; editable: boolean };

/** 全部目标。老服务器只回进行中的（没有 closed）：那就是只读的，不给改。 */
export async function load(fresh = false): Promise<GoalsData> {
  const j = await request<any>(`/api/goals${fresh ? '?fresh=1' : ''}`);
  const editable = Array.isArray(j.closed);
  return {
    goals: (Array.isArray(j.goals) ? j.goals : []).map(normGoal),
    closed: (editable ? j.closed : []).map(normGoal),
    recent: (Array.isArray(j.recent) ? j.recent : []).map(normChange).filter((c: GoalChange | null): c is GoalChange => !!c),
    metrics: Array.isArray(j.metrics) && j.metrics.length ? j.metrics.map((m: any) => ({ key: str(m.key), label: str(m.label), unit: str(m.unit) })) : fallbackMetrics(),
    editable,
  };
}

/** 加 / 改时发的字段（和服务器一样的名字）。改的时候只带改了的；null = 清掉。 */
export interface GoalFields {
  title?: string;
  category?: GoalCategory;
  detail?: string | null;
  due?: string | null;
  unit?: string | null;
  targetLow?: number | null;
  targetHigh?: number | null;
  metric?: string | null;
  groupId?: string | null;
  status?: GoalStatus;
  position?: number;
}

type Saved = { goal: Goal; logId: number | null; summary: string };
const saved = (j: any): Saved => ({ goal: normGoal(j.goal), logId: typeof j.logId === 'number' ? j.logId : null, summary: str(j.summary) });

export const add = (f: GoalFields & { title: string; category: GoalCategory }) => request<any>('/api/goals', { method: 'POST', body: f }).then(saved);
export const patch = (id: string, f: GoalFields) => request<any>(`/api/goals/${encodeURIComponent(id)}`, { method: 'PATCH', body: f }).then(saved);
/** 撤销一次改动（redo = 再做回来）。 */
export const undo = (logId: number, redo = false) => request<{ change: GoalChange }>(`/api/goals/undo/${logId}`, { method: 'POST', body: { redo } });
/** 目标页顶上那条点了「知道了」。 */
export const seen = (ids: number[]) => request('/api/goals/seen', { method: 'POST', body: { ids } });

/** 一个指标的读数趋势。老服务器没有这个接口 = null（不显示）。 */
export async function trend(metric: string, days = 180, fresh = false): Promise<GoalTrend | null> {
  try {
    const j = await request<any>(`/api/goals/trend?metric=${encodeURIComponent(metric)}&days=${days}${fresh ? '&fresh=1' : ''}`);
    const series: GoalTrendPoint[] = (Array.isArray(j.series) ? j.series : [])
      .filter((p: any) => typeof p?.date === 'string' && num(p.value) != null)
      .map((p: any) => ({ date: p.date, value: p.value, source: p.source === 'health' ? 'health' : 'body' }));
    return {
      metric: str(j.metric) || metric, label: str(j.label), unit: str(j.unit), days: num(j.days) ?? days, from: str(j.from), to: str(j.to), series,
      summary: j.summary && j.summary.latest ? j.summary : null, sources: Array.isArray(j.sources) ? j.sources : [],
    };
  } catch (e) {
    if (missing(e)) return null;
    throw e;
  }
}
