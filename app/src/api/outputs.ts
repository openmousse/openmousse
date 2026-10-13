// Agent 的产出（2026-10-13，服务器 ../server/outputs.py）：标签栏上方的浮动标签、半屏小板子、对话里的产出卡片用同一份数据。
// 新产出 = 这个逻辑日里还没看过的建议卡（GET /api/outputs，最新在前）；点开小板子或下滑关掉浮动标签就标成看过（store.markFeedSeen → /api/feed/seen）。
// 小板子要画的数服务器都算好了（board，按 kind 套模板），app 只画。浮动标签的列表放在模块里（像 progress.ts），不进 store：
// 它只跟着 /api/unread 的 feedNew 变，不该让整个 store 的使用者跟着重画。
import { useSyncExternalStore } from 'react';
import { request } from './base';
import { supports } from './version';

export type Trend = 'up' | 'flat' | 'down' | 'new';
export type Tone = 'good' | 'warn' | 'bad' | 'neutral';

/** 浮动标签上的小图：pair 两个点（上次 → 本次），ring 三大营养素的占比，trend 近几天的折线。 */
export interface OutputSpark { type: 'pair' | 'ring' | 'trend'; values: number[]; labels?: string[]; a11y?: string }

export interface ReviewMove { name: string; now: string; last: string; verdict: string; delta: string | null; trend: Trend }
export interface ReviewBoard {
  template: 'training_review';
  session: string;
  stats: { value: string; label: string }[];
  recovery: number | null;
  main: { name: string; value: string; delta: string | null; trend: Trend; note: string; points: { label: string; value: number }[]; a11y: string } | null;
  /** 「与 9/29 相比」 */
  compare: string;
  moves: ReviewMove[];
  skipped: string | null;
  next24h: string[];
  next: string | null;
  verdict: string | null;
  verdictTrend: Trend | null;
  /** 对话卡片最底下那一行：「明天休息 · 下次 10/14 Legs A」 */
  footer: string | null;
  /** 老卡没有 moves：正文「这次怎么样」那几条 */
  notes: string[];
}
export interface MealBoard {
  template: 'meal_plan';
  /** 营养环：总热量和三大营养素的克数（比例由 NutritionRing 按 4 / 4 / 9 算），caption =「kcal · 两顿」 */
  ring: { total: string; kcal: number | null; protein: number; carb: number; fat: number; caption: string; parts: { key: 'protein' | 'carb' | 'fat'; label: string; grams: string; pct: number }[]; a11y: string } | null;
  /** 蛋白质对目标：value / target 是写好的字，grams / targetGrams 是数（目标条用） */
  protein: { value: string; target: string | null; grams: number; targetGrams: number | null; a11y: string } | null;
  note: string | null;
  /** 「大虾和酸奶今天到期」 */
  tag: string | null;
  meals: { time: string; label: string; items: string; kcal: string; note: string | null }[];
  why: string | null;
  shopping: string[];
  loggable: boolean;
}
export interface SleepBoard {
  template: 'sleep_report';
  synced: boolean;
  score: { value: string | null; label: string | null; tone: Tone; provisional: boolean };
  /** 睡眠时长对目标：low / high 是目标范围（分钟），range =「7–9 小时」 */
  sleep: { text: string | null; minutes: number | null; low: number; high: number; target: string; range: string };
  stages: { label: string; minutes: number | null; text: string }[];
  vitals: { label: string; value: string }[];
  trend: { points: { label: string; value: number }[]; caption: string } | null;
  notice: string | null;
  /** 报告写的时候健康数据没同步、现在同步了：新的数 */
  updated: { score: string; label: string; tone: Tone; sleep: string | null; minutes?: number | null } | null;
  notes: string[];
}
export interface PlanBoard {
  template: 'training_plan';
  decision: string | null; tone: Tone; session: string | null; time: string | null; duration: string | null;
  intensity: string | null; focus: string[]; cautions: string[]; why: string | null;
}
export interface TextBoard { template: 'text' }
export type OutputBoard = ReviewBoard | MealBoard | SleepBoard | PlanBoard | TextBoard;

export interface Output {
  id: string;
  kind: string | null;
  /** 产出的名字：练后报告 / 晚餐建议 / 睡眠报告 / 训练计划 */
  label: string;
  /** 哪个 Agent（groups 的 id）；null = 助手自己（主对话） */
  agent: string | null;
  title: string;
  /** Agent 写的全文（Markdown） */
  body: string;
  /** 一句结论（浮动标签的第二行） */
  line: string;
  spark: OutputSpark | null;
  createdAt: string;
  time: string;
  day: string;
  seen: boolean;
  /** 同一天同 kind 的新一份把它替换了 */
  replaced: boolean;
  /** 挂在哪个对话的哪条回复下（db<id> 的数字）；不是在回复里写的 = null */
  thread: string | null;
  messageId: number | null;
  board: OutputBoard;
}

export const outputsApi = {
  /** 这个逻辑日还没看过的产出，最新在前（浮动标签） */
  fresh: () => request<{ outputs: Output[] }>('/api/outputs').then((j) => j.outputs ?? []),
  /** 某一天的全部产出（今天页的报告） */
  day: (day?: string) => request<{ outputs: Output[] }>(`/api/outputs?all=1${day ? `&day=${day}` : ''}`).then((j) => j.outputs ?? []),
  get: (id: string) => request<{ output: Output }>(`/api/outputs/${encodeURIComponent(id)}`).then((j) => j.output),
};

/**
 * 「记录这一餐」：按饮食建议卡的 data 记下第一顿（饮食迁移那边的接口；服务器报了 diet.log 才显示按钮）。
 * 约定：POST /api/diet/log {feed: 建议卡 id, meal: 第几顿（0 起）}，记好返回 {ok}。
 */
export const dietLog = (feedId: string, meal = 0) => request<{ ok: boolean }>('/api/diet/log', { method: 'POST', body: { feed: feedId, meal } });

// —— 浮动标签的列表 ——

type FloatState = { items: Output[]; json: string };
let state: FloatState = { items: [], json: '[]' };
const subs = new Set<() => void>();
const emit = (items: Output[]) => {
  const json = JSON.stringify(items);
  if (json === state.json) return;
  state = { items, json };
  subs.forEach((f) => f());
};
const subscribe = (f: () => void) => { subs.add(f); return () => { subs.delete(f); }; };
/** 本机已经看过 / 关掉的：服务器那边标看过要一点时间，期间读回来的旧列表里别再冒出来。 */
const hidden = new Set<string>();

/** 浮动标签现在显示的几份（最新在前）。 */
export const useFreshOutputs = () => useSyncExternalStore(subscribe, () => state.items, () => state.items);
/** 同一份列表，给手势回调这类不在渲染里的地方读。 */
export const currentOutputs = () => state.items;

let loading: Promise<void> | null = null;
let again = false;

/** 重读新产出（服务器没有这项能力 = 空）。正在读时又要读：读完再读一次，别丢了刚到的那份。 */
export function refreshOutputs(): Promise<void> {
  if (!supports('outputs')) { emit([]); return Promise.resolve(); }
  if (loading) { again = true; return loading; }
  loading = outputsApi.fresh()
    .then((items) => emit(items.filter((o) => !hidden.has(o.id))))
    .catch(() => { /* 读不到就保持原样，下次未读变了再读 */ })
    .finally(() => {
      loading = null;
      if (again) { again = false; refreshOutputs().catch(() => {}); }
    });
  return loading;
}

/** 这几份看过了：从浮动标签里拿掉。标看过本身走 store.markFeedSeen（同时去掉「今天」页的「新」）。 */
export function dropOutputs(ids: string[]) {
  ids.forEach((id) => hidden.add(id));
  emit(state.items.filter((o) => !hidden.has(o.id)));
}

/** 换了服务器（切 claw、断开）：清空。 */
export function resetOutputs() {
  hidden.clear();
  emit([]);
}
