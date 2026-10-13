// 「今天」页的周 / 月视图（/api/today/range）：同一批事项换个范围看。
// 周 = 按天列表；月 = 日历（日程 / 截止 / 待办三色点 + 图例），点一天在下面列出那天的。点一条回到日视图的那一天。
import React, { useEffect, useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { loadRange, type RangeItem } from '../../api/today';
import { L } from '../../i18n';
import { radius, space, useTheme, type Theme } from '../../theme';
import { ChevronLeft, ChevronRight } from '../icons';
import { T, useScaledWidth } from '../ui';
import { MonthCalendar, seriesColor, type CalKind } from '../viz';
import { BlockHeader, addDays, isoOf, monthDay, monthStart, mondayOf, parseIso, shortWeekday, weekdayOf } from './parts';

const cache = new Map<string, RangeItem[]>();
/** 类别色和月历一样：日程 1 号色、截止 2 号色、待办 3 号色。 */
const typeColor = (t: Theme, type: RangeItem['type']) => seriesColor(t, type === 'event' ? 0 : type === 'deadline' ? 1 : 2);

/** 读一段日子（按 from + days 缓存，先画上次的）。 */
function useRange(from: string, days: number, bump: number) {
  const key = `${from}:${days}`;
  const [got, setGot] = useState<{ key: string; items: RangeItem[]; error: string } | null>(null);
  useEffect(() => {
    let alive = true;
    loadRange(from, days).then((r) => { cache.set(key, r.items); if (alive) setGot({ key, items: r.items, error: '' }); })
      .catch((e) => { if (alive) setGot({ key, items: cache.get(key) ?? [], error: e instanceof Error ? e.message : String(e) }); });
    return () => { alive = false; };
  }, [from, days, key, bump]);
  const items = got?.key === key ? got.items : cache.get(key) ?? null;
  return { items, error: got?.key === key ? got.error : '' };
}

function NavRow({ title, onPrev, onNext, prevLabel, nextLabel }: { title: string; onPrev: () => void; onNext: () => void; prevLabel: string; nextLabel: string }) {
  const t = useTheme();
  return (
    <View style={styles.navRow}>
      <T v="headline" style={{ fontSize: 17, fontWeight: '700' }}>{title}</T>
      <View style={{ flexDirection: 'row', gap: 8 }}>
        <Pressable onPress={onPrev} hitSlop={6} accessibilityRole="button" accessibilityLabel={prevLabel} style={({ pressed }) => [styles.navBtn, { backgroundColor: t.surface2, opacity: pressed ? 0.6 : 1 }]}><ChevronLeft size={18} color={t.ink} /></Pressable>
        <Pressable onPress={onNext} hitSlop={6} accessibilityRole="button" accessibilityLabel={nextLabel} style={({ pressed }) => [styles.navBtn, { backgroundColor: t.surface2, opacity: pressed ? 0.6 : 1 }]}><ChevronRight size={18} color={t.ink} /></Pressable>
      </View>
    </View>
  );
}

/** 一行：左边一道类别色，时间，标题和来源。截止写「截止：」。 */
function Row({ r, last, onPress }: { r: RangeItem; last: boolean; onPress: () => void }) {
  const t = useTheme();
  const timeW = useScaledWidth(46);
  const when = r.type === 'event' ? (r.allDay || !r.start ? L('全天', 'All day') : r.start) : r.start || '';
  const title = r.type === 'deadline' ? L(`截止：${r.title}`, `Due: ${r.title}`) : r.title;
  const dim = r.done || r.skip;
  return (
    <Pressable onPress={onPress} accessibilityRole="button" accessibilityLabel={`${when} ${title}`}
      style={({ pressed }) => [styles.row, !last && { borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: t.line }, { opacity: pressed ? 0.6 : 1 }]}>
      <View style={[styles.bar, { backgroundColor: typeColor(t, r.type), opacity: dim ? 0.4 : 1 }]} />
      <Text style={[styles.time, { width: timeW, color: t.ink2 }]} numberOfLines={1} adjustsFontSizeToFit minimumFontScale={0.6}>{when}</Text>
      <View style={{ flex: 1, minWidth: 0, gap: 2 }}>
        <Text style={[styles.title, { color: dim ? t.ink3 : t.ink }, dim && { textDecorationLine: 'line-through' }]} numberOfLines={1}>{title}</Text>
        {r.sub ? <Text style={[styles.sub, { color: t.ink3 }]} numberOfLines={1}>{r.sub}</Text> : null}
      </View>
      <ChevronRight size={16} color={t.ink3} />
    </Pressable>
  );
}

export function WeekView({ anchor, today, bump, onAnchor, onPickDay }: {
  anchor: string; today: string; bump: number; onAnchor: (iso: string) => void; onPickDay: (iso: string) => void;
}) {
  const t = useTheme();
  const from = mondayOf(anchor);
  const { items, error } = useRange(from, 7, bump);
  const days = Array.from({ length: 7 }, (_, i) => addDays(from, i));
  const end = parseIso(days[6]);
  const title = L(`${monthDay(from)} – ${parseIso(from).getMonth() === end.getMonth() ? `${end.getDate()}日` : monthDay(days[6])}`, `${monthDay(from)} – ${monthDay(days[6])}`);
  return (
    <View>
      <NavRow title={title} onPrev={() => onAnchor(addDays(anchor, -7))} onNext={() => onAnchor(addDays(anchor, 7))} prevLabel={L('上一周', 'Previous week')} nextLabel={L('下一周', 'Next week')} />
      {error ? <T v="callout" color={t.bad} style={{ paddingHorizontal: space.xs }}>{L(`无法加载：${error}`, `Couldn't load: ${error}`)}</T> : null}
      {!items ? <T v="callout" color={t.ink2} style={{ padding: space.xs }}>{L('正在加载…', 'Loading…')}</T> : days.map((d) => {
        const rows = items.filter((r) => r.date === d);
        const head = `${shortWeekday(d)} ${monthDay(d)}${d === today ? L(' · 今天', ' · Today') : ''}`;
        return (
          <View key={d}>
            <BlockHeader title={rows.length ? `${head} · ${rows.length}` : head} accent={d === today} />
            {rows.length ? (
              <View style={[styles.card, { backgroundColor: t.surface }]}>
                {rows.map((r, i) => <Row key={`${r.type}:${r.id}`} r={r} last={i === rows.length - 1} onPress={() => onPickDay(d)} />)}
              </View>
            ) : <T v="callout" color={t.ink3} style={{ paddingHorizontal: space.xs }}>{L('无安排', 'Nothing scheduled')}</T>}
          </View>
        );
      })}
    </View>
  );
}

export function MonthView({ anchor, today, bump, onAnchor, onPickDay }: {
  anchor: string; today: string; bump: number; onAnchor: (iso: string) => void; onPickDay: (iso: string) => void;
}) {
  const t = useTheme();
  const a = parseIso(anchor);
  const y = a.getFullYear(), m = a.getMonth();
  const { items, error } = useRange(monthStart(y, m), 42, bump);
  // 选中的一天：换月时落到那个月的 anchor 那天（今天所在的月就是今天）
  const [picked, setPicked] = useState<string | null>(null);
  const selected = picked && picked.slice(0, 7) === anchor.slice(0, 7) ? picked : anchor;
  // 每天每类一个记号（日程 / 截止 / 待办：颜色加形状），做完的、跳过的不算
  const marks: Record<string, string[]> = {};
  for (const r of items ?? []) if (!r.done && !r.skip) (marks[r.date] ??= []).push(r.type);
  const rows = (items ?? []).filter((r) => r.date === selected);
  const move = (n: number) => onAnchor(isoOf(new Date(y, m + n, 1)));
  const kinds: CalKind[] = [{ key: 'event', label: L('日程', 'Events') }, { key: 'deadline', label: L('截止', 'Deadlines') }, { key: 'todo', label: L('待办', 'To-dos') }];
  return (
    <View>
      <NavRow title={L(`${y}年${m + 1}月`, a.toLocaleDateString('en', { month: 'long', year: 'numeric' }))} onPrev={() => move(-1)} onNext={() => move(1)}
        prevLabel={L('上个月', 'Previous month')} nextLabel={L('下个月', 'Next month')} />
      {error ? <T v="callout" color={t.bad} style={{ paddingHorizontal: space.xs }}>{L(`无法加载：${error}`, `Couldn't load: ${error}`)}</T> : null}
      <MonthCalendar month={anchor.slice(0, 7)} marks={marks} kinds={kinds} today={today} selected={selected} onPressDay={setPicked} style={{ paddingHorizontal: space.xs }} />
      <BlockHeader title={L(`${monthDay(selected)} ${weekdayOf(selected)}`, `${weekdayOf(selected)}, ${monthDay(selected)}`)} accent={selected === today} />
      {!items ? <T v="callout" color={t.ink2} style={{ padding: space.xs }}>{L('正在加载…', 'Loading…')}</T>
        : rows.length ? (
          <View style={[styles.card, { backgroundColor: t.surface }]}>
            {rows.map((r, i) => <Row key={`${r.type}:${r.id}`} r={r} last={i === rows.length - 1} onPress={() => onPickDay(selected)} />)}
          </View>
        ) : <T v="callout" color={t.ink3} style={{ paddingHorizontal: space.xs }}>{L('无安排', 'Nothing scheduled')}</T>}
    </View>
  );
}

const styles = StyleSheet.create({
  navRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingLeft: space.xs, paddingTop: space.lg, paddingBottom: space.sm },
  navBtn: { width: 32, height: 32, borderRadius: 16, alignItems: 'center', justifyContent: 'center' },
  card: { borderRadius: radius.lg, overflow: 'hidden' },
  row: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingRight: 14, paddingVertical: 11 },
  bar: { width: 3, alignSelf: 'stretch', borderRadius: 2, marginLeft: 12, marginRight: 2 },
  time: { fontSize: 13, fontWeight: '600', fontVariant: ['tabular-nums'] },
  title: { fontSize: 15, lineHeight: 20 },
  sub: { fontSize: 12, lineHeight: 16 },
});
