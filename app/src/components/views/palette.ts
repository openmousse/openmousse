// 集合块视图用的颜色。可视化素材库（src/components/viz/，viz-kit 分支）合进来以后换成 theme 里的 series，规则一样：
// 分类色 6 个、深浅两套（dataviz 校验过，同一色相顺序），按顺序用、不循环，第 7 类起用中性色或合并为「其他」；状态色只表示状态。
import { Platform, Settings } from 'react-native';
import type { Tone } from '../../api/boards';
import type { Theme } from '../../theme';

const SERIES_DARK = ['#2A97B0', '#B5873A', '#8E6FD8', '#3E9E6E', '#5E86D6', '#C0679A'];
const SERIES_LIGHT = ['#1488A3', '#B07D22', '#7B5CC7', '#2F8A5B', '#4A72C2', '#B24F86'];

/** 第 i 个分类色（柱、点、列头的圆点）；null 或超出 6 个 = 中性色。 */
export function seriesColor(t: Theme, i: number | null | undefined): string {
  if (i == null || i < 0 || i >= 6) return t.ink3;
  return (t.mode === 'dark' ? SERIES_DARK : SERIES_LIGHT)[i];
}

// 选项标签（表格里的「状态」这类）：浅底 + 前景，前景在浅底上 ≥ 4.5:1。前四个和 Agent 颜色共用（青、金、紫、绿），第 5 个蓝、第 6 个粉。
const BLUE = { dark: { soft: '#1A2540', fg: '#8FB0F0' }, light: { soft: '#E3EAF8', fg: '#3A5FA8' } };

/** 第 i 个分类色的标签配色 [底, 字]；null = 中性。 */
export function seriesTag(t: Theme, i: number | null | undefined): [string, string] {
  const order = ['cyan', 'gold', 'purple', 'green', 'blue', 'pink'] as const;
  if (i == null || i < 0 || i >= 6) return [t.mode === 'dark' ? t.line : t.surface2, t.ink2];
  const k = order[i];
  if (k === 'blue') { const b = BLUE[t.mode]; return [b.soft, b.fg]; }
  const tint = t.tints[k];
  return [tint.soft, tint.fg];
}

/** 卡片上的标签按 tone 配色。卡片底色在深色里是 surface2，中性标签要比它亮一点才看得出。 */
export function toneTag(t: Theme, tone?: Tone): [string, string] {
  switch (tone) {
    case 'good': return [t.goodSoft, t.good];
    case 'warn': return [t.warnSoft, t.warn];
    case 'bad': return [t.badSoft, t.bad];
    case 'cyan': return [t.cyanSoft, t.cyan];
    case 'gold': return [t.goldSoft, t.gold];
    default: return [t.mode === 'dark' ? t.line : t.surface2, t.ink2];
  }
}

/** 看板列和卡片的底色：深色里列是 surface、卡片 surface2；浅色里列是 surface2、卡片白。 */
export const columnBg = (t: Theme) => (t.mode === 'dark' ? t.surface : t.surface2);
export const cardBg = (t: Theme) => (t.mode === 'dark' ? t.surface2 : t.surface);

// —— 每个集合块上次看的是哪个视图（本机记住，和草稿一样：iOS 用 Settings，网页用 localStorage）——
const KEY = 'mousse.views';
let picked: Record<string, string> = {};
try {
  const raw = Platform.OS === 'ios' ? Settings.get(KEY) : Platform.OS === 'web' ? globalThis.localStorage?.getItem(KEY) : null;
  const j: unknown = typeof raw === 'string' ? JSON.parse(raw) : null;
  if (j && typeof j === 'object') picked = j as Record<string, string>;
} catch {
  picked = {};
}

export const pickedView = (agent: string, block: string) => picked[`${agent}:${block}`];

export function pickView(agent: string, block: string, view: string) {
  picked = { ...picked, [`${agent}:${block}`]: view };
  const value = JSON.stringify(picked);
  try {
    if (Platform.OS === 'ios') Settings.set({ [KEY]: value });
    else if (Platform.OS === 'web') globalThis.localStorage?.setItem(KEY, value);
  } catch {
    // 存不上就只在这次打开里记得
  }
}
