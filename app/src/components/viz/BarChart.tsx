// 柱状（单系列）和堆叠柱（多系列）：柱子最粗 24、柱顶 4px 圆角、贴基线一端直角，从同一条基线长出来；
// 只标关键数值（当前、最高），其余点按显示；堆叠的段与段之间留 2px 底色，必须有图例。
import React, { useState } from 'react';
import { Pressable, Text, View, type StyleProp, type ViewStyle } from 'react-native';
import { L } from '../../i18n';
import { useTheme, vizMark } from '../../theme';
import { useScaledWidth } from '../ui';
import { fmtTick, Legend, niceScale, seriesColor, Tip, useFontScale, useWidth, VizFrame, vizText, type TipRow, type VizStateProps } from './core';

export interface BarDatum {
  key: string;
  /** 横轴上的字 */
  label: string;
  value: number | null;
  /** 已格式化的数值 */
  text?: string;
  /** 当前这一段（本周、今天）：横轴的字加粗，数值标出来 */
  current?: boolean;
  /** 读屏用的全称（横轴的字是简写时） */
  name?: string;
}

const plain = (v: number) => v.toLocaleString('en-GB', { maximumFractionDigits: 1 });

/** 柱子太多时隔几个写一个字（当前那一个一定写）。 */
const labelEvery = (n: number) => (n > 10 ? Math.ceil(n / 7) : 1);

/** 纵轴刻度 + 网格线（0 那条是基线，深一点）。 */
function YAxis({ ticks, top, plotH, head, gw, fsLine }: { ticks: number[]; top: number; plotH: number; head: number; gw: number; fsLine: number }) {
  const t = useTheme();
  return (
    <>
      {ticks.map((v) => {
        const y = head + plotH - (v / top) * plotH;
        return (
          <React.Fragment key={v}>
            <View pointerEvents="none" style={{ position: 'absolute', left: gw, right: 0, top: y, height: 1, backgroundColor: v === 0 ? t.viz.axis : t.viz.grid }} />
            {gw > 0 ? (
              <View pointerEvents="none" style={{ position: 'absolute', left: 0, width: gw - 6, top: y - fsLine / 2, height: fsLine, justifyContent: 'center' }}>
                <Text numberOfLines={1} style={[vizText.axis, { color: t.ink3, textAlign: 'right' }]}>{fmtTick(v)}</Text>
              </View>
            ) : null}
          </React.Fragment>
        );
      })}
    </>
  );
}

function XLabels({ items, gw, every }: { items: { key: string; label: string; current?: boolean }[]; gw: number; every: number }) {
  const t = useTheme();
  return (
    <View style={{ flexDirection: 'row', marginLeft: gw, marginTop: 6 }}>
      {items.map((d, i) => (
        <Text key={d.key} numberOfLines={1} style={[vizText.axis, { flex: 1, textAlign: 'center', color: d.current ? t.ink : t.ink3, fontWeight: d.current ? '700' : '400' }]}>
          {i % every === 0 || d.current ? d.label : ''}
        </Text>
      ))}
    </View>
  );
}

export interface BarChartProps extends VizStateProps {
  data: BarDatum[];
  /** 画图区的高度（不含横轴的字） */
  height?: number;
  /** 用第几个分类色（单系列默认 1 号） */
  slot?: number;
  color?: string;
  /** 哪些柱子标数值：key = 当前和最高（默认）、all、none */
  values?: 'key' | 'all' | 'none';
  /** 当前那根用强调色（同色相高一级） */
  emphasis?: boolean;
  /** 纵轴刻度和网格线；关掉就是只看形状的小柱图（数字在别处） */
  axis?: boolean;
  ticks?: number;
  /** 柱子宽度（默认按位置的 58%，最粗 24） */
  barWidth?: number;
  /** 点了某根柱子（同时会显示它的数值） */
  onPress?: (d: BarDatum) => void;
  fmt?: (v: number) => string;
  label?: string;
  style?: StyleProp<ViewStyle>;
}

export function BarChart({ data, height = 140, slot = 0, color, values = 'key', emphasis, axis = true, ticks = 2, barWidth, onPress, fmt = plain, label, style, ...state }: BarChartProps) {
  const t = useTheme();
  const fs = useFontScale();
  const gutter = useScaledWidth(26);
  const gw = axis ? gutter : 0;
  const [sel, setSel] = useState<string | null>(null);
  const max = Math.max(0, ...data.map((d) => d.value ?? 0));
  const sc = niceScale(0, max || 1, ticks);
  const top = axis ? sc.max || 1 : max || 1;
  const fsLine = Math.round(14 * fs);
  const head = values === 'none' && !onPress ? 4 : fsLine + 4;  // 最高那根上面写数值的地方
  const peak = max > 0 ? data.findIndex((d) => d.value === max) : -1;
  const c = color ?? seriesColor(t, slot);
  const text = (d: BarDatum) => d.text ?? (d.value != null ? fmt(d.value) : '—');
  const auto = data.map((d) => `${d.name ?? d.label} ${text(d)}`).join(L('，', ', '));
  const many = data.length > 31;
  return (
    <VizFrame empty={!data.length} height={height} state={state} label={label ?? auto} group={many} style={style}>
      <View style={{ height: head + height }}>
        {axis ? <YAxis ticks={sc.ticks} top={top} plotH={height} head={head} gw={gw} fsLine={fsLine} /> : null}
        <View style={{ position: 'absolute', left: gw, right: 0, top: 0, bottom: 0, flexDirection: 'row', alignItems: 'flex-end' }}>
          {data.map((d, i) => {
            const v = d.value ?? 0;
            const h = v > 0 ? Math.max(3, Math.round((v / top) * height)) : 2;
            const show = v > 0 && (sel === d.key || values === 'all' || (values === 'key' && (d.current || i === peak)));
            return (
              <Pressable key={d.key} disabled={many && !onPress} accessible={!many} accessibilityRole={onPress ? 'button' : 'text'}
                accessibilityLabel={`${d.name ?? d.label} ${text(d)}`}
                onPress={() => { setSel((p) => (p === d.key ? null : d.key)); onPress?.(d); }}
                style={{ flex: 1, height: '100%', alignItems: 'center', justifyContent: 'flex-end' }}>
                {show ? <Text numberOfLines={1} style={[vizText.value, { color: t.ink, marginBottom: 3 }]}>{text(d)}</Text> : null}
                <View style={{
                  width: barWidth ?? '58%', maxWidth: vizMark.barMax, height: h,
                  borderTopLeftRadius: v > 0 ? vizMark.radius : 1, borderTopRightRadius: v > 0 ? vizMark.radius : 1,
                  backgroundColor: v > 0 ? (emphasis && d.current ? t.viz.emph : c) : t.track,
                  opacity: sel && sel !== d.key ? 0.55 : 1,
                }} />
              </Pressable>
            );
          })}
        </View>
      </View>
      <XLabels items={data} gw={gw} every={labelEvery(data.length)} />
    </VizFrame>
  );
}

/**
 * 堆叠段的高度（从下往上）：按累计值算位置，段间 2px 的间隙从上面那段里扣，整根柱子的高度和刻度对得上。
 */
function stackHeights(values: number[], top: number, plotH: number): number[] {
  const out: number[] = [];
  let prev = 0;
  let cum = 0;
  values.forEach((v, k) => {
    cum += v;
    const y = Math.round((cum / top) * plotH);
    out.push(Math.max(1, y - prev - (k > 0 ? vizMark.gap : 0)));
    prev = y;
  });
  return out;
}

export interface StackSeries {
  key: string;
  label: string;
  slot?: number;
  /** 每个类别一个数（和 categories 同序） */
  values: (number | null)[];
  /** 图例上的合计（已格式化）；不给就按 fmt 算 */
  total?: string;
}

export interface StackedBarProps extends VizStateProps {
  categories: { key: string; label: string; current?: boolean; name?: string }[];
  /** 第一个系列在最下面 */
  series: StackSeries[];
  height?: number;
  axis?: boolean;
  ticks?: number;
  /** 柱顶写合计：key = 当前和最高、all、none（默认） */
  totals?: 'key' | 'all' | 'none';
  onPress?: (categoryKey: string) => void;
  fmt?: (v: number) => string;
  label?: string;
  style?: StyleProp<ViewStyle>;
}

export function StackedBar({ categories, series, height = 120, axis = true, ticks = 2, totals = 'none', onPress, fmt = plain, label, style, ...state }: StackedBarProps) {
  const t = useTheme();
  const fs = useFontScale();
  const gutter = useScaledWidth(26);
  const gw = axis ? gutter : 0;
  const [w, onLayout] = useWidth();
  const [sel, setSel] = useState<number | null>(null);
  const sums = categories.map((_, i) => series.reduce((a, s) => a + (s.values[i] ?? 0), 0));
  const max = Math.max(0, ...sums);
  const sc = niceScale(0, max || 1, ticks);
  const top = axis ? sc.max || 1 : max || 1;
  const fsLine = Math.round(14 * fs);
  const head = fsLine + 4;
  const peak = max > 0 ? sums.indexOf(max) : -1;
  const colorOf = (s: StackSeries, i: number) => seriesColor(t, s.slot ?? i);
  const legend = series.map((s, i) => ({ label: s.label, color: colorOf(s, i), value: s.total ?? fmt(s.values.reduce<number>((a, v) => a + (v ?? 0), 0)) }));
  const auto = categories.map((c, i) => `${c.name ?? c.label}${L('：', ': ')}${series.map((s) => `${s.label} ${fmt(s.values[i] ?? 0)}`).join(L('，', ', '))}`).join(L('；', '; '));
  const colW = categories.length ? Math.max(0, w - gw) / categories.length : 0;
  const tipRows: TipRow[] = sel == null ? [] : [
    ...series.map((s, si) => ({ label: s.label, value: fmt(s.values[sel] ?? 0), color: colorOf(s, si), shape: 'rect' as const })),
    { label: L('合计', 'Total'), value: fmt(sums[sel]) },
  ];
  return (
    <VizFrame empty={!categories.length || max <= 0} height={height} state={state} label={label ?? auto} style={style}>
      <Legend items={legend} style={{ marginBottom: 10 }} />
      <View onLayout={onLayout} style={{ height: head + height }}>
        {axis ? <YAxis ticks={sc.ticks} top={top} plotH={height} head={head} gw={gw} fsLine={fsLine} /> : null}
        <View style={{ position: 'absolute', left: gw, right: 0, top: 0, bottom: 0, flexDirection: 'row', alignItems: 'flex-end' }}>
          {categories.map((c, i) => {
            const parts = series.map((s, si) => ({ v: s.values[i] ?? 0, color: colorOf(s, si) })).filter((p) => p.v > 0);
            const show = sums[i] > 0 && (totals === 'all' || (totals === 'key' && (c.current || i === peak)));
            return (
              <Pressable key={c.key} onPress={() => { setSel((p) => (p === i ? null : i)); onPress?.(c.key); }}
                accessibilityRole="button" accessibilityLabel={`${c.name ?? c.label}${L('：', ': ')}${series.map((s) => `${s.label} ${fmt(s.values[i] ?? 0)}`).join(L('，', ', '))}`}
                style={{ flex: 1, height: '100%', alignItems: 'center', justifyContent: 'flex-end' }}>
                {show && sel !== i ? <Text numberOfLines={1} style={[vizText.value, { color: t.ink, marginBottom: 3 }]}>{fmt(sums[i])}</Text> : null}
                <View style={{ width: '58%', maxWidth: vizMark.barMax, gap: vizMark.gap, opacity: sel != null && sel !== i ? 0.55 : 1 }}>
                  {stackHeights(parts.map((p) => p.v), top, height).map((h, k) => ({ h, color: parts[k].color })).reverse().map((p, k) => (
                    <View key={k} style={{
                      height: p.h, backgroundColor: p.color,
                      borderTopLeftRadius: k === 0 ? vizMark.radius : 0, borderTopRightRadius: k === 0 ? vizMark.radius : 0,
                    }} />
                  ))}
                </View>
              </Pressable>
            );
          })}
        </View>
        {sel != null && colW > 0 ? <Tip x={gw + colW * (sel + 0.5)} width={w} title={categories[sel]?.name ?? categories[sel]?.label} rows={tipRows} /> : null}
      </View>
      <XLabels items={categories} gw={gw} every={labelEvery(categories.length)} />
    </VizFrame>
  );
}
