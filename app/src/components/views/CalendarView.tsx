// 日历视图：按日期字段把行放进当月的格子里（周一开头）。点的颜色按视图的 group（比如状态），配图例；点一天列出那天的行；
// ‹ › 翻月由服务器重算（POST /api/boards/{agent}/view {month}）。
import React, { useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from 'react-native';
import type { CalendarData, CalendarDay, ViewData } from '../../api/boards';
import { L } from '../../i18n';
import { radius, space, type, useTheme } from '../../theme';
import { ChevronLeft, ChevronRight } from '../icons';
import { showError, T } from '../ui';
import { CardItem } from './Card';
import type { ViewProps } from './env';
import { seriesColor } from './palette';

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
  const day = data.weeks.flat().find((x) => x.date === sel) ?? null;
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
        <View style={styles.week}>
          {data.weekdays.map((w) => <Text key={w} style={[type.caption, styles.dow, { color: t.ink3 }]}>{w}</Text>)}
        </View>
        {data.weeks.map((w) => (
          <View key={w[0].date} style={styles.week}>
            {w.map((d) => <Day key={d.date} d={d} on={sel === d.date} big={env.full} onPress={() => setSel(sel === d.date ? null : d.date)} />)}
          </View>
        ))}
        {data.legend?.length ? (
          <View style={styles.legend}>
            {data.legend.map((x) => (
              <View key={x.label} style={{ flexDirection: 'row', alignItems: 'center', gap: 5 }}>
                <View style={[styles.dotSm, { backgroundColor: seriesColor(t, x.color) }]} />
                <T v="caption" color={t.ink2} style={{ fontWeight: '400' }}>{x.label}</T>
              </View>
            ))}
          </View>
        ) : null}
      </View>
      {day ? (
        <View style={{ gap: 8 }}>
          <T v="caption" color={t.ink3} style={{ paddingHorizontal: 4 }}>{L(`${Number(day.date.slice(5, 7))} 月 ${day.day} 日 · ${day.items.length} 条`, `${day.date} · ${day.items.length}`)}</T>
          {day.items.length ? day.items.map((it) => (
            <CardItem key={it.id} card={it.time ? { ...it, meta: [it.time, it.meta].filter(Boolean).join(' · ') } : it} dot={it.color}
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

function Day({ d, on, big, onPress }: { d: CalendarDay; on: boolean; big: boolean; onPress: () => void }) {
  const t = useTheme();
  const size = big ? 30 : 26;
  const dots = d.items.slice(0, 3);
  return (
    <Pressable onPress={onPress} accessibilityRole="button" accessibilityState={{ selected: on }}
      accessibilityLabel={L(`${d.day} 日${d.items.length ? `，${d.items.length} 条` : ''}`, `${d.day}${d.items.length ? `, ${d.items.length}` : ''}`)}
      style={[styles.cell, { height: big ? 50 : 42 }]}>
      <View style={[styles.num, { width: size, height: size, borderRadius: size / 2,
        backgroundColor: d.today ? t.goldFill : 'transparent', borderColor: on && !d.today ? t.ink : 'transparent' }]}>
        <Text style={[type.caption, { fontSize: 13, fontWeight: d.today || on ? '700' : '400', fontVariant: ['tabular-nums'],
          color: d.today ? t.onGold : d.inMonth ? t.ink : t.ink3, opacity: d.inMonth ? 1 : 0.6 }]}>{d.day}</Text>
      </View>
      <View style={styles.dots}>
        {dots.map((x) => <View key={x.id} style={[styles.dotSm, { backgroundColor: seriesColor(t, x.color) }]} />)}
        {d.items.length > 3 ? <Text style={[type.caption, { fontSize: 9, color: t.ink3, lineHeight: 10 }]}>+</Text> : null}
      </View>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  card: { borderRadius: radius.lg, padding: space.md, gap: 2 },
  bar: { flexDirection: 'row', alignItems: 'center', marginBottom: space.sm },
  nav: { width: 36, height: 32, alignItems: 'center', justifyContent: 'center' },
  week: { flexDirection: 'row' },
  dow: { flex: 1, textAlign: 'center', fontSize: 11, paddingBottom: 4 },
  cell: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 3 },
  num: { alignItems: 'center', justifyContent: 'center', borderWidth: 1.5 },
  dots: { flexDirection: 'row', gap: 2, height: 5, alignItems: 'center' },
  dotSm: { width: 5, height: 5, borderRadius: 2.5 },
  legend: { flexDirection: 'row', flexWrap: 'wrap', gap: space.md, paddingTop: space.sm, paddingHorizontal: 4 },
});
