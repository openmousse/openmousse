// 日历视图：素材库的月历（周一开头，今天金底，记号 = 颜色 + 形状，配图例）。记号按视图的 group（比如状态）分种类；
// 点一天列出那天的行；‹ › 翻月由服务器重算（POST /api/boards/{agent}/view {month}）。
import React, { useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, View } from 'react-native';
import type { CalendarData, ViewData } from '../../api/boards';
import { L } from '../../i18n';
import { radius, space, useTheme } from '../../theme';
import { ChevronLeft, ChevronRight } from '../icons';
import { showError, T } from '../ui';
import { CardItem } from './Card';
import { MonthCalendar } from '../viz';
import type { ViewProps } from './env';

const firstWithItems = (d: CalendarData): string | null => {
  const days = d.weeks.flat().filter((x) => x.inMonth);
  return (days.find((x) => x.today && x.items.length) ?? days.find((x) => x.items.length))?.date ?? null;
};

export function CalendarView({ env, data: initial }: ViewProps<CalendarData>) {
  const t = useTheme();
  const [data, setData] = useState(initial);
  const [seen, setSeen] = useState(initial);
  const [month, setMonth] = useState<string | null>(null);  // 翻到别的月了（看板重读时照这个月重算）
  const [sel, setSel] = useState<string | null>(() => firstWithItems(initial));
  const [busy, setBusy] = useState(false);
  const go = (m: string) => {
    if (!env.query) return;
    setBusy(true);
    env.query({ month: m }).then((v: ViewData | null) => {
      if (!v || v.type !== 'calendar') return;
      const d = v as CalendarData;
      setData(d);
      setMonth(m);
      setSel(firstWithItems(d));
    }).catch((e) => showError(L('无法加载', "Couldn't load"), e)).finally(() => setBusy(false));
  };
  if (initial !== seen) {  // 看板重读了：还在默认那个月就换上新数据；翻到别的月的，照旧显示那个月（再翻一下就是新的）
    setSeen(initial);
    if (!month || month === initial.month) setData(initial);
  }
  const days = data.weeks.flat();
  const day = days.find((x) => x.date === sel) ?? null;
  const today = days.find((x) => x.today)?.date;
  // 素材库的月历按「种类」画记号：有 group 时一个选项一种（颜色 + 形状，配图例），没有就一种（日期字段的名字）
  const grouped = !!data.legend?.length;
  const kindOf = (color: number | null | undefined) => (grouped ? `k${color ?? 'x'}` : 'k');
  const kinds = grouped ? (data.legend ?? []).map((x) => ({ key: kindOf(x.color), label: x.label, slot: x.color ?? -1 })) : [{ key: 'k', label: data.dateLabel, slot: 0 }];
  const marks = Object.fromEntries(days.filter((d) => d.inMonth && d.items.length).map((d) => [d.date, d.items.map((it) => kindOf(it.color))]));
  return (
    <View style={{ gap: space.sm }}>
      <View style={[styles.card, { backgroundColor: t.surface }]}>
        <View style={styles.bar}>
          <Pressable onPress={() => go(data.prev)} disabled={!env.query || busy} hitSlop={10} accessibilityRole="button" accessibilityLabel={L('上个月', 'Previous month')}
            style={({ pressed }) => [styles.nav, { opacity: !env.query ? 0.3 : pressed ? 0.6 : 1 }]}>
            <ChevronLeft size={20} color={t.ink2} />
          </Pressable>
          <View style={{ flex: 1, alignItems: 'center', flexDirection: 'row', justifyContent: 'center', gap: 8 }}>
            <T v="headline" style={{ fontSize: 15 }}>{data.title}</T>
            {busy ? <ActivityIndicator size="small" color={t.ink3} /> : null}
          </View>
          <Pressable onPress={() => go(data.next)} disabled={!env.query || busy} hitSlop={10} accessibilityRole="button" accessibilityLabel={L('下个月', 'Next month')}
            style={({ pressed }) => [styles.nav, { opacity: !env.query ? 0.3 : pressed ? 0.6 : 1 }]}>
            <ChevronRight size={20} color={t.ink2} />
          </Pressable>
        </View>
        <MonthCalendar month={data.month} marks={marks} kinds={kinds} today={today} selected={sel} onPressDay={(d) => setSel(sel === d ? null : d)} />
      </View>
      {day ? (
        <View style={{ gap: 8 }}>
          <T v="caption" color={t.ink3} style={{ paddingHorizontal: 4 }}>{L(`${Number(day.date.slice(5, 7))} 月 ${day.day} 日 · ${day.items.length} 条`, `${day.date} · ${day.items.length}`)}</T>
          {day.items.length ? day.items.map((it) => (
            <CardItem key={it.id} card={it.time ? { ...it, meta: [it.time, it.meta].filter(Boolean).join(' · ') } : it} dot={grouped ? it.color : undefined}
              onPress={env.readOnly ? undefined : () => env.openCard(it)} />
          )) : <T v="callout" color={t.ink2} style={{ paddingHorizontal: 4 }}>{L('这一天没有。', 'Nothing on this day.')}</T>}
        </View>
      ) : (
        <T v="caption" color={t.ink3} style={{ fontWeight: '400', paddingHorizontal: 4 }}>
          {[data.count ? L(`本月 ${data.count} 条，点按日期查看`, `${data.count} this month; tap a day to see them`) : L('本月没有。', 'Nothing this month.'),
            data.undated ? L(`另有 ${data.undated} 条没有${data.dateLabel}`, `${data.undated} without ${data.dateLabel}`) : ''].filter(Boolean).join(L('；', '; '))}
        </T>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  card: { borderRadius: radius.lg, padding: space.md, gap: 2 },
  bar: { flexDirection: 'row', alignItems: 'center', marginBottom: space.sm },
  nav: { width: 36, height: 32, alignItems: 'center', justifyContent: 'center' },
});
