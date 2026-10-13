// 日历两种：热力日历（每天一格，颜色深浅 = 数值，顺序色阶）和月历（日程 / 截止 / 待办用不同形状的点 + 图例）。
// 一周从周一开始。没数据的格子只画一圈细线，今天描金边；点按一天在下面写出这一天的数。
import React, { useState } from 'react';
import { Pressable, StyleSheet, Text, View, type StyleProp, type ViewStyle } from 'react-native';
import { L } from '../../i18n';
import { space, useTheme } from '../../theme';
import { useScaledWidth } from '../ui';
import { seriesColor, VizFrame, vizText, type VizStateProps } from './core';
import { dayNum } from './LineChart';

const DAY = 86400000;
/** 第几天 → YYYY-MM-DD */
export const isoOfDay = (n: number) => new Date(n * DAY).toISOString().slice(0, 10);
/** 周一 = 0 … 周日 = 6 */
const weekdayOf = (n: number) => (new Date(n * DAY).getUTCDay() + 6) % 7;
const md = (iso: string) => `${Number(iso.slice(5, 7))}/${Number(iso.slice(8, 10))}`;
export const weekdayLetters = () => [L('一', 'M'), L('二', 'T'), L('三', 'W'), L('四', 'T'), L('五', 'F'), L('六', 'S'), L('日', 'S')];
const weekdayNames = () => [L('周一', 'Mon'), L('周二', 'Tue'), L('周三', 'Wed'), L('周四', 'Thu'), L('周五', 'Fri'), L('周六', 'Sat'), L('周日', 'Sun')];

const plain = (v: number) => v.toLocaleString('en-GB', { maximumFractionDigits: 1 });

export interface HeatDay { date: string; value: number | null; text?: string }

export interface HeatCalendarProps extends VizStateProps {
  days: HeatDay[];
  /** 今天（描金边；今天之后的格子是空的） */
  today?: string;
  /** 分档的界线（从小到大，最多 3 个 → 4 档，用色阶 1–4 级）；不给就按数据的高低均分 4 档 */
  bins?: number[];
  /** 这是什么数（点按后那一行里写：「恢复分」） */
  name?: string;
  fmt?: (v: number) => string;
  onPressDay?: (d: HeatDay) => void;
  label?: string;
  style?: StyleProp<ViewStyle>;
}

/** 热力日历：一行一周，左边写这一周的周一。示例：近 4 周的恢复分。 */
export function HeatCalendar({ days, today, bins, name, fmt = plain, onPressDay, label, style, ...state }: HeatCalendarProps) {
  const t = useTheme();
  const lw = useScaledWidth(36);
  const [sel, setSel] = useState<string | null>(null);
  const byDate = new Map(days.map((d) => [d.date, d]));
  const vals = days.map((d) => d.value).filter((v): v is number => v != null);
  const lo = vals.length ? Math.min(...vals) : 0;
  const hi = vals.length ? Math.max(...vals) : 1;
  const edges = (bins?.length ? bins : [1, 2, 3].map((k) => lo + ((hi - lo) * k) / 4)).slice(0, 3);
  const colorOf = (v: number) => {
    let i = 0;
    while (i < edges.length && v >= edges[i]) i++;
    return t.viz.seq[Math.min(4, 1 + i + (4 - 1 - edges.length))];
  };
  const dates = days.map((d) => dayNum(d.date));
  const first = dates.length ? Math.min(...dates) : dayNum(today ?? '2026-01-01');
  const last = Math.max(dates.length ? Math.max(...dates) : first, today ? dayNum(today) : first);
  const start = first - weekdayOf(first);
  const weeks: number[] = [];
  for (let w = start; w <= last; w += 7) weeks.push(w);
  const picked = sel ? byDate.get(sel) : undefined;
  const show = (d: HeatDay) => (d.value != null ? d.text ?? fmt(d.value) : L('无数据', 'no data'));
  const auto = days.filter((d) => d.value != null).map((d) => `${md(d.date)} ${show(d)}`).join(L('，', ', '));
  const swatches = Array.from({ length: edges.length + 1 }, (_, i) => t.viz.seq[Math.min(4, 1 + i + (4 - 1 - edges.length))]);
  return (
    <VizFrame empty={!vals.length} state={state} label={label ?? auto} group={false} style={[{ gap: 6 }, style]}>
      <View style={styles.row}>
        <View style={{ width: lw }} />
        {weekdayLetters().map((w, i) => <Text key={i} style={[vizText.axis, styles.cellText, { color: t.ink3 }]}>{w}</Text>)}
      </View>
      {weeks.map((w) => (
        <View key={w} style={styles.row}>
          <Text numberOfLines={1} style={[vizText.axis, { width: lw, color: t.ink3 }]}>{md(isoOfDay(w))}</Text>
          {Array.from({ length: 7 }, (_, k) => {
            const iso = isoOfDay(w + k);
            const d = byDate.get(iso);
            const has = d?.value != null;
            const isToday = iso === today;
            return (
              <Pressable key={iso} disabled={!has} onPress={() => { setSel((s) => (s === iso ? null : iso)); if (d) onPressDay?.(d); }}
                accessibilityRole={has ? 'button' : undefined} accessibilityLabel={`${md(iso)} ${d ? show(d) : L('无数据', 'no data')}`}
                style={[styles.heat, has ? { backgroundColor: colorOf(d.value as number) } : { borderWidth: 1, borderColor: t.line },
                  isToday && { borderWidth: 1.5, borderColor: t.gold }, sel === iso && { borderWidth: 2, borderColor: t.ink }]} />
            );
          })}
        </View>
      ))}
      <View style={[styles.row, { justifyContent: 'space-between', marginTop: 2 }]}>
        <Text numberOfLines={1} style={[vizText.axis, { fontSize: 12, color: picked ? t.ink : t.ink3, flexShrink: 1 }]}>
          {picked ? `${md(picked.date)} ${weekdayNames()[weekdayOf(dayNum(picked.date))]} · ${name ? `${name} ` : ''}${show(picked)}` : ''}
        </Text>
        <View style={[styles.row, { gap: 4 }]}>
          <Text style={[vizText.axis, { color: t.ink3 }]}>{fmt(lo)}</Text>
          {swatches.map((c, i) => <View key={i} style={{ width: 14, height: 10, borderRadius: 3, backgroundColor: c }} />)}
          <Text style={[vizText.axis, { color: t.ink3 }]}>{fmt(hi)}</Text>
        </View>
      </View>
    </VizFrame>
  );
}

// —— 月历 ——

export type MarkShape = 'dot' | 'diamond' | 'square';
export interface CalKind { key: string; label: string; slot?: number; shape?: MarkShape }

/** 日历上的小记号：颜色 + 形状一起区分种类（深色下前三个分类色任意两两色觉差 7.5，要靠形状再分一道）。 */
export function CalMark({ shape = 'dot', color, size = 6 }: { shape?: MarkShape; color: string; size?: number }) {
  if (shape === 'diamond') return <View style={{ width: size - 1, height: size - 1, borderRadius: 1, backgroundColor: color, transform: [{ rotate: '45deg' }] }} />;
  if (shape === 'square') return <View style={{ width: size - 1, height: size - 1, borderRadius: 1.5, backgroundColor: color }} />;
  return <View style={{ width: size, height: size, borderRadius: size, backgroundColor: color }} />;
}

export interface MonthCalendarProps extends VizStateProps {
  /** YYYY-MM */
  month: string;
  /** 每天有哪几种（kind 的 key，重复的只画一个） */
  marks: Record<string, string[]>;
  /** 种类：日程 / 截止 / 待办……按顺序取分类色和形状（点、菱形、方块） */
  kinds: CalKind[];
  today?: string;
  /** 选中的那天（不给就自己记） */
  selected?: string | null;
  onPressDay?: (date: string) => void;
  style?: StyleProp<ViewStyle>;
}

const SHAPES: MarkShape[] = ['dot', 'diamond', 'square'];

/** 月历：今天金底，选中的一圈描边；每天下面一排小记号，下方图例。 */
export function MonthCalendar({ month, marks, kinds, today, selected, onPressDay, style, ...state }: MonthCalendarProps) {
  const t = useTheme();
  const [own, setOwn] = useState<string | null>(null);
  const sel = selected === undefined ? own : selected;
  const y = Number(month.slice(0, 4));
  const m = Number(month.slice(5, 7));
  const first = Math.round(Date.UTC(y, m - 1, 1) / DAY);
  const lastDay = Math.round(Date.UTC(y, m, 0) / DAY);
  const start = first - weekdayOf(first);
  const end = lastDay + (6 - weekdayOf(lastDay));
  const kindOf = new Map(kinds.map((k, i) => [k.key, { ...k, color: seriesColor(t, k.slot ?? i), shape: k.shape ?? SHAPES[i % SHAPES.length], order: i }]));
  const weeks: number[] = [];
  for (let w = start; w <= end; w += 7) weeks.push(w);
  return (
    <VizFrame empty={false} state={state} group={false} style={[{ gap: 2 }, style]}>
      <View style={styles.row}>
        {weekdayLetters().map((w, i) => <Text key={i} style={[vizText.axis, styles.cellText, { color: t.ink3, paddingBottom: 4 }]}>{w}</Text>)}
      </View>
      {weeks.map((w) => (
        <View key={w} style={styles.row}>
          {Array.from({ length: 7 }, (_, k) => {
            const n = w + k;
            const iso = isoOfDay(n);
            const inMonth = n >= first && n <= lastDay;
            const ks = inMonth ? [...new Set(marks[iso] ?? [])].map((key) => kindOf.get(key)).filter((x): x is NonNullable<typeof x> => !!x).sort((a, b) => a.order - b.order) : [];
            const isToday = iso === today;
            const on = sel === iso;
            return (
              <Pressable key={iso} disabled={!inMonth} onPress={() => { setOwn((s) => (s === iso ? null : iso)); onPressDay?.(iso); }}
                accessibilityRole="button" accessibilityState={{ selected: on }}
                accessibilityLabel={`${Number(iso.slice(5, 7))}/${Number(iso.slice(8, 10))}${isToday ? L('，今天', ', today') : ''}${ks.length ? `${L('：', ': ')}${ks.map((x) => x.label).join(L('、', ', '))}` : ''}`}
                style={styles.day}>
                <View style={[styles.num, isToday && { backgroundColor: t.goldFill }, on && !isToday && { borderWidth: 1.5, borderColor: t.ink }]}>
                  <Text style={{ fontSize: 12, fontVariant: ['tabular-nums'], color: isToday ? t.onGold : inMonth ? t.ink : t.ink3, opacity: inMonth ? 1 : 0.5, fontWeight: isToday ? '700' : '400' }}>{Number(iso.slice(8, 10))}</Text>
                </View>
                <View style={styles.marks}>{ks.map((x) => <CalMark key={x.key} shape={x.shape} color={x.color} />)}</View>
              </Pressable>
            );
          })}
        </View>
      ))}
      <View style={[styles.row, { gap: space.md, marginTop: 6, flexWrap: 'wrap' }]}>
        {[...kindOf.values()].map((x) => (
          <View key={x.key} style={[styles.row, { gap: 5 }]}>
            <CalMark shape={x.shape} color={x.color} size={8} />
            <Text style={[vizText.axis, { fontSize: 12, color: t.ink2 }]}>{x.label}</Text>
          </View>
        ))}
      </View>
    </VizFrame>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: 'row', alignItems: 'center', gap: 4 },
  cellText: { flex: 1, textAlign: 'center' },
  heat: { flex: 1, height: 28, borderRadius: 6 },
  day: { flex: 1, height: 40, alignItems: 'center', justifyContent: 'center', gap: 3 },
  num: { width: 26, height: 26, borderRadius: 13, alignItems: 'center', justifyContent: 'center' },
  marks: { flexDirection: 'row', gap: 3, height: 6, alignItems: 'center' },
});
