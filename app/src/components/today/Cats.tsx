// 「今天」页的三类（server/todocat.py，2026-10-15）：学习 / 生活 / 工作，颜色取自图表色板（金色留给助手自己，青色给工程看板）。
// 圆圈、色条、小点都按类别上色，灰字里也写着类别（不只靠颜色）。顶上一排筛选，点开一条能改类别。
import React from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import type { TodoCat } from '../../data/types';
import { L } from '../../i18n';
import { type Theme, useTheme } from '../../theme';
import { seriesColor, withAlpha } from '../viz';

export const CATS: TodoCat[] = ['study', 'life', 'work'];
/** 图表色板里的序号：2 紫 = 学习，3 绿 = 生活，4 蓝 = 工作；0 青 = 工程看板。 */
export const CAT_SLOT: Record<TodoCat, number> = { study: 2, life: 3, work: 4 };
export const ENG_SLOT = 0;

export const catColor = (t: Theme, c?: TodoCat | null) => (c ? seriesColor(t, CAT_SLOT[c]) : t.ink3);
export const catLabel = (c?: TodoCat | null) => (c === 'study' ? L('学习', 'Study') : c === 'life' ? L('生活', 'Life') : c === 'work' ? L('工作', 'Work') : '');
export const isCat = (v: unknown): v is TodoCat => v === 'study' || v === 'life' || v === 'work';

export type CatFilterValue = 'all' | TodoCat;

/** 顶上一排：全部 / 学习 / 生活 / 工作，后面是条数。 */
export function CatFilter({ value, onChange, counts }: { value: CatFilterValue; onChange: (v: CatFilterValue) => void; counts: Record<CatFilterValue, number> }) {
  const t = useTheme();
  const opts: CatFilterValue[] = ['all', ...CATS];
  return (
    <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.chips} accessibilityLabel={L('按类别筛选', 'Filter by category')}>
      {opts.map((k) => {
        const on = value === k;
        return (
          <Pressable key={k} onPress={() => onChange(k)} accessibilityRole="button" accessibilityState={{ selected: on }}
            accessibilityLabel={`${k === 'all' ? L('全部', 'All') : catLabel(k)} ${counts[k]}`}
            style={({ pressed }) => [styles.chip, {
              backgroundColor: on ? (t.mode === 'dark' ? t.line : t.surface2) : t.surface,
              borderColor: on ? t.ink3 : t.line, opacity: pressed ? 0.7 : 1,
            }]}>
            {k !== 'all' ? <View style={[styles.dot, { backgroundColor: catColor(t, k) }]} /> : null}
            <Text style={[styles.chipText, { color: on ? t.ink : t.ink2 }]}>{k === 'all' ? L('全部', 'All') : catLabel(k)}</Text>
            <Text style={[styles.chipText, { color: on ? t.ink : t.ink3, fontVariant: ['tabular-nums'] }]}>{counts[k]}</Text>
          </Pressable>
        );
      })}
    </ScrollView>
  );
}

/** 收起的一行后面那几颗小点：里面有哪几类。 */
export function CatDots({ cats }: { cats: (TodoCat | null | undefined)[] }) {
  const t = useTheme();
  const seen = CATS.filter((c) => cats.includes(c));
  if (!seen.length) return null;
  return (
    <View style={styles.dots} accessible accessibilityLabel={seen.map(catLabel).join(L('、', ', '))}>
      {seen.map((c) => <View key={c} style={[styles.smallDot, { backgroundColor: catColor(t, c) }]} />)}
    </View>
  );
}

/** 详情页顶上的三类单选：点一下就改（以你改的为准）。 */
export function CatPills({ value, onChange, disabled }: { value: TodoCat | null | undefined; onChange: (c: TodoCat) => void; disabled?: boolean }) {
  const t = useTheme();
  return (
    <View style={styles.pills} accessibilityRole="radiogroup" accessibilityLabel={L('类别', 'Category')}>
      {CATS.map((c) => {
        const on = value === c;
        const color = catColor(t, c);
        return (
          <Pressable key={c} onPress={() => { if (!on) onChange(c); }} disabled={disabled} accessibilityRole="radio" accessibilityState={{ checked: on, disabled }}
            style={({ pressed }) => [styles.pill, {
              borderColor: on ? color : t.line, backgroundColor: on ? withAlpha(color, t.mode === 'dark' ? 0.2 : 0.12) : 'transparent',
              opacity: pressed ? 0.7 : 1,
            }]}>
            <View style={[styles.dot, { backgroundColor: color }]} />
            <Text style={[styles.pillText, { color: on ? t.ink : t.ink2 }]}>{catLabel(c)}</Text>
          </Pressable>
        );
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  chips: { flexDirection: 'row', gap: 6, paddingTop: 14 },
  chip: { height: 32, paddingHorizontal: 11, borderRadius: 16, borderWidth: 1, flexDirection: 'row', alignItems: 'center', gap: 5 },
  chipText: { fontSize: 13, fontWeight: '600' },
  dot: { width: 8, height: 8, borderRadius: 4 },
  dots: { flexDirection: 'row', gap: 4 },
  smallDot: { width: 7, height: 7, borderRadius: 4 },
  pills: { flexDirection: 'row', gap: 8, flexWrap: 'wrap' },
  pill: { height: 34, paddingHorizontal: 14, borderRadius: 17, borderWidth: 1.5, flexDirection: 'row', alignItems: 'center', gap: 6 },
  pillText: { fontSize: 14, fontWeight: '600' },
});
