// 「今天」页几块共用的小零件：块的小标题、右边的标签、日子的写法。
import React from 'react';
import { StyleSheet, Text, View } from 'react-native';
import type { Tone } from '../../api/today';
import { L } from '../../i18n';
import { space, type Theme, useTheme } from '../../theme';
import { T } from '../ui';

const pad = (n: number) => String(n).padStart(2, '0');
export const parseIso = (iso: string) => { const [y, m, d] = iso.split('-').map(Number); return new Date(y, m - 1, d); };
export const isoOf = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
export const addDays = (iso: string, n: number) => { const d = parseIso(iso); d.setDate(d.getDate() + n); return isoOf(d); };
export const diffDays = (a: string, b: string) => Math.round((parseIso(a).getTime() - parseIso(b).getTime()) / 86400000);
export const mondayOf = (iso: string) => { const d = parseIso(iso); d.setDate(d.getDate() - ((d.getDay() + 6) % 7)); return isoOf(d); };
const WD_ZH = '日一二三四五六';
const WD_EN = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
export const weekdayOf = (iso: string) => { const d = parseIso(iso); return L(`星期${WD_ZH[d.getDay()]}`, WD_EN[d.getDay()]); };
export const shortWeekday = (iso: string) => { const d = parseIso(iso); return L(`周${WD_ZH[d.getDay()]}`, WD_EN[d.getDay()]); };
/** 10月12日 / Oct 12 */
export const monthDay = (iso: string) => { const d = parseIso(iso); return L(`${d.getMonth() + 1}月${d.getDate()}日`, d.toLocaleDateString('en', { month: 'short', day: 'numeric' })); };
/** 大标题：今天 / 明天 / 昨天 / 10月14日 */
export function dayTitle(iso: string, today: string): string {
  const n = diffDays(iso, today);
  if (n === 0) return L('今天', 'Today');
  if (n === 1) return L('明天', 'Tomorrow');
  if (n === -1) return L('昨天', 'Yesterday');
  return monthDay(iso);
}
/** 大标题下面：10月12日 星期一 */
export const daySub = (iso: string) => L(`${monthDay(iso)} ${weekdayOf(iso)}`, `${weekdayOf(iso)}, ${monthDay(iso)}`);
export const toMin = (s: string) => { const m = /^(\d{1,2}):(\d{2})$/.exec(s || ''); return m ? Number(m[1]) * 60 + Number(m[2]) : null; };

/** 标签的底色和字色（只表示状态：安全 / 逾期 = bad，今天 = warn，待确认 = 金）。 */
export function toneColors(t: Theme, tone: Tone): readonly [string, string] {
  return ({ bad: [t.badSoft, t.bad], warn: [t.warnSoft, t.warn], gold: [t.goldSoft, t.gold], good: [t.goodSoft, t.good], neutral: [t.surface2, t.ink2] } as const)[tone] ?? [t.surface2, t.ink2];
}

export function Chip({ label, tone }: { label: string; tone: Tone }) {
  const t = useTheme();
  if (!label) return null;
  const [bg, fg] = toneColors(t, tone);
  return (
    <View style={[styles.chip, { backgroundColor: bg }]}>
      <Text style={[styles.chipText, { color: fg }]} numberOfLines={1}>{label}</Text>
    </View>
  );
}

/** 一块的小标题：「待处理 · 6」（要动手的用金色），右边放提示或「添加」。 */
export function BlockHeader({ title, accent, right }: { title: string; accent?: boolean; right?: React.ReactNode }) {
  const t = useTheme();
  return (
    <View style={styles.head} accessibilityRole="header">
      <T v="callout" color={accent ? t.gold : t.ink3} style={{ fontSize: 13, fontWeight: '700' }}>{title}</T>
      {right}
    </View>
  );
}

const styles = StyleSheet.create({
  chip: { borderRadius: 7, paddingHorizontal: 8, paddingVertical: 3, flexShrink: 0, maxWidth: 120 },
  chipText: { fontSize: 12, fontWeight: '600' },
  head: { flexDirection: 'row', alignItems: 'baseline', justifyContent: 'space-between', paddingHorizontal: space.xs, marginTop: space.xl, marginBottom: space.sm, gap: space.sm },
});
