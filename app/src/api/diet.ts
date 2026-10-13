// 饮食记录（server/diet.py，2026-10-13）：按饮食建议卡一键「记录这一餐」、撤销、改 / 删一样。
// 看板和今天页照旧读 /api/diet/day（live.ts 的 LiveDiet）；这里只放写入。服务器有没有：supports('diet.log_meal') / supports('diet.log')。
import { request } from './base';
import { supports } from './version';

export type DietMacros = { kcal: number; protein: number; carb: number; fat: number };

export type DietEntry = DietMacros & {
  id: string; date: string; meal: string; mealLabel: string; time: string | null; name: string; amount: number; unit: string; portion: string | null;
  /** plan 按建议 / chat 对话 / photo 照片 / manual 手动 / xunji 从训记导入 */
  source: string; estimated: boolean; batch: string | null;
};

export type DietDaySummary = { date: string; totals: DietMacros; targets: (DietMacros & { source?: string }) | null; remaining: DietMacros | null; meals: string[]; items: number };

export type LogMealResult = {
  card: string; batch: string; entries: DietEntry[]; adds: DietMacros; day: DietDaySummary;
  /** 这一次记了哪几餐（index = 建议卡 data.meals 里的位置） */
  meals: (DietMacros & { index: number; label: string; meal: string; items: number })[];
  /** 这次要记、但已经记过所以跳过的（餐名） */
  already: string[]; warnings: string[];
};

/** 改份量：建议卡第几餐（meal）的第几样（item，data.meals[meal].items 里的位置）记成多少克 / 毫升；0 = 这样没吃。 */
export type PortionChange = { meal: number; item: number; amount: number };

export const canLogMeal = () => supports('diet.log_meal');

/** 一键「记录这一餐」：按建议卡（feed 的 meal_plan）记。meals = data.meals 里的位置（不给 = 第一餐还没记的）；
 *  scale = 整餐按比例；items = 单样改份量。同一张卡的同一餐记过了会 409（HttpError.status），again = true 才再记一份。
 *  记到卡片那一天；今天的卡时间记成现在，补记以前的用卡上的计划时间。返回的 batch 给 undoMeal。 */
export function logMeal(card: string, opts: { meals?: number[]; scale?: number; items?: PortionChange[]; time?: string; again?: boolean } = {}): Promise<LogMealResult> {
  return request<LogMealResult>('/api/diet/log-plan', { method: 'POST', body: { card, ...opts } });
}

/** 只算不写：按同样的参数看会记什么（每样的克数和营养）。 */
export function previewMeal(card: string, opts: { meals?: number[]; scale?: number; items?: PortionChange[] } = {}) {
  return request<Omit<LogMealResult, 'batch' | 'day'> & { preview: true }>('/api/diet/log-plan', { method: 'POST', body: { card, ...opts, commit: false } });
}

/** 撤销同一次写入的几样（logMeal 返回的 batch）。 */
export function undoMeal(batch: string): Promise<{ undone: number; day: DietDaySummary }> {
  return request('/api/diet/undo', { method: 'POST', body: { batch } });
}

/** 改一样的份量（按记下时的每 100 g 营养重算）、餐次或时间。 */
export function editEntry(id: string, patch: { amount?: number; meal?: string; time?: string }): Promise<{ entry: DietEntry; day: DietDaySummary }> {
  return request(`/api/diet/entries/${encodeURIComponent(id)}`, { method: 'PATCH', body: patch });
}

export function deleteEntry(id: string): Promise<{ deleted: string; day: DietDaySummary }> {
  return request(`/api/diet/entries/${encodeURIComponent(id)}`, { method: 'DELETE' });
}
